#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""frpc 插件命令行工具（给宿主 actions 调用）。"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def _pid_state():
    """先看控制台接口，再退回扫描进程。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % common.PORT, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:                                     # noqa: BLE001
        return None


def cmd_status(argv):
    print("== frpc 隧道状态 ==")
    try:
        s = common.load_settings()
    except Exception as e:                                # noqa: BLE001
        s = dict(common.DEFAULTS)
        print("读取设置失败：%s" % e)
    addr = (s.get("SERVER_ADDR") or "").strip() or "（未填写）"
    print("frps 服务器   : %s:%s" % (addr, s.get("SERVER_PORT") or "7000"))
    print("用户标识      : %s" % ((s.get("USER") or "").strip() or "(空)"))
    print("代理名 / 类型 : %s / %s" % (s.get("PROXY_NAME"), (s.get("PROXY_TYPE") or "tcp").upper()))
    print("本地监听      : %s:%s" % (s.get("LOCAL_IP"), s.get("LOCAL_PORT")))
    if (s.get("PROXY_TYPE") or "tcp").lower() == "tcp":
        print("远程端口      : %s" % s.get("REMOTE_PORT"))
    else:
        print("子域名 / 域名 : %s / %s" % (s.get("SUBDOMAIN") or "-", s.get("CUSTOM_DOMAINS") or "-"))
    print("自动重连      : %s（间隔 %s 秒）" % (s.get("AUTO_RECONNECT"), s.get("RECONNECT_DELAY")))

    snap = _pid_state()
    print()
    if snap is None:
        print("控制台服务  : 未运行（127.0.0.1:%d 无响应）" % common.PORT)
        print("提示        : 在插件页点「启动」，或运行 bin/frpc-serve")
    else:
        print("控制台服务  : 运行中")
        print("隧道状态    : %s —— %s" % (snap.get("state"), snap.get("message")))
        print("隧道进程号  : %s" % (snap.get("pid") or "无"))
        print("已运行      : %s 秒" % snap.get("uptime"))
        print("重连次数    : %s" % snap.get("restarts"))
        if snap.get("config_error"):
            print("配置问题    : %s" % snap["config_error"])
    print()
    print("frpc 版本   : %s" % common.binary_version())
    print("配置文件    : %s" % common.conf_path())
    return 0


def cmd_show_conf(argv):
    s = common.load_settings()
    try:
        text = common.build_toml(s)
    except common.ConfigError as e:
        print("配置错误：%s" % e, file=sys.stderr)
        print(json.dumps({"SERVER_ADDR": s.get("SERVER_ADDR"), "PROXY_TYPE": s.get("PROXY_TYPE")},
                         ensure_ascii=False, indent=2))
        return 3
    print(text, end="")
    return 0


def cmd_verify(argv):
    """生成配置并做语法校验，供「校验配置」按钮调用。"""
    s = common.load_settings()
    try:
        path, text = common.write_conf(s)
    except common.ConfigError as e:
        print("配置错误：%s" % e, file=sys.stderr)
        return 3
    print("配置已写入：%s" % path)
    print("-" * 46)
    print(text, end="")
    print("-" * 46)
    ok, out = common.verify_conf()
    print(out)
    if ok:
        print("结论：配置语法正确，可以启动隧道。")
        return 0
    print("结论：配置校验未通过，请按上面的提示修正设置。", file=sys.stderr)
    return 4


def cmd_test(argv):
    s = common.load_settings()
    try:
        path, _ = common.write_conf(s)
        binary = common.binary_path()
    except common.ConfigError as e:
        print("配置错误：%s" % e, file=sys.stderr)
        return 3

    print("前台试连 15 秒，配置：%s" % path)
    print("-" * 46)
    env = dict(os.environ)
    env.setdefault("HOME", "/root")
    try:
        p = subprocess.Popen([binary, "-c", path], cwd=common.data_dir(), env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except Exception as e:                                # noqa: BLE001
        print("启动失败：%s" % e, file=sys.stderr)
        return 4
    deadline = time.time() + 15
    keep = []
    # 读输出必须放后台线程：隧道一旦连通就不再输出，主线程会被 readline 永久阻塞，
    # 15 秒上限就形同虚设（实测会挂死）。
    def _pump():
        for raw in p.stdout:
            keep.append(raw.rstrip())

    reader = threading.Thread(target=_pump, daemon=True)
    reader.start()
    shown = 0
    try:
        while time.time() < deadline:
            if p.poll() is not None and not reader.is_alive():
                break
            if shown < len(keep):
                for line in keep[shown:]:
                    print(line)
                shown = len(keep)
            time.sleep(0.2)
        for line in keep[shown:]:
            print(line)
    finally:
        # 判定只用「终止之前」的输出快照：terminate 会让隧道进程吐出 ERROR，
        # 那是我们自己杀的，不能让结论变成「连接出错」。
        snap_lines = list(keep)
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        if p.stdout:
            p.stdout.close()
    print("-" * 46)
    joined = "\n".join(snap_lines)
    logged_in = "login to server success" in joined
    proxy_ok = "start proxy success" in joined
    has_error = "ERROR" in joined or "[W]" in joined
    if proxy_ok:
        print("结论：已连上 frps 并注册成功，隧道正在转发。")
    elif logged_in:
        print("结论：已登录 frps，但代理尚未注册成功，请看上面日志末尾的提示。")
    elif "authentication failed" in joined or "authorization failed" in joined or "token" in joined.lower():
        print("结论：认证失败，请核对「认证 Token」是否与 frps.toml 里的 auth.token 一致。")
    elif "connection refused" in joined or "i/o timeout" in joined or "dial tcp" in joined:
        print("结论：连不上 frps 的 bindPort 端口，请确认服务器地址、端口正确，"
              "且宝塔防火墙/云安全组已放行该端口。")
    elif has_error:
        print("结论：连接失败，请对照上面的 ERROR 行检查服务端地址 / 端口 / token。")
    else:
        print("结论：未拿到明确结果，可能是网络不通或服务器地址有误。")
    return 0


def cmd_restart(argv):
    try:
        req = urllib.request.Request("http://127.0.0.1:%d/api/restart" % common.PORT,
                                     data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=6) as r:
            r.read()
    except Exception as e:                                # noqa: BLE001
        print("控制台没在跑（%s），改从命令行直接拉起…" % e)
        binpath = os.path.join(common.tool_dir(), "bin", "frpc-serve")
        if not os.path.exists(binpath):
            print("找不到启动入口：%s" % binpath, file=sys.stderr)
            return 4
        subprocess.Popen(["/bin/sh", binpath],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        time.sleep(2)
        snap = _pid_state()
        print("已拉起控制台：%s" % ("成功" if snap else "失败，请稍后再查状态"))
        return 0 if snap else 4
    time.sleep(2)
    snap = _pid_state()
    if snap:
        print("已重启隧道，当前状态：%s —— %s" % (snap.get("state"), snap.get("message")))
        print("隧道进程号：%s" % (snap.get("pid") or "无"))
    else:
        print("已发出重启请求，但状态接口暂无响应，请稍后查看。")
    return 0


COMMANDS = {
    "status": cmd_status,
    "show-conf": cmd_show_conf,
    "verify": cmd_verify,
    "test": cmd_test,
    "restart": cmd_restart,
}


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print("用法: frpc-cli status|show-conf|verify|test|restart")
        return 0 if len(argv) >= 2 else 2
    fn = COMMANDS.get(argv[1])
    if fn is None:
        print("未知命令：%s" % argv[1], file=sys.stderr)
        return 2
    try:
        return fn(argv[2:])
    except common.ConfigError as e:
        print("配置错误：%s" % e, file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv))
