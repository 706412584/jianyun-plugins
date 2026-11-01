#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""frpc 隧道监督器：单线程循环托管 frpc 子进程。

设计要点（踩坑后定稿）：
- 全局只有一个监督线程，绝不并发起多个 frpc 实例
  —— 同 remotePort 重复注册会被 frps 踢掉，造成无限重连风暴。
- 重启/重载通过 threading.Event 通知监督线程，而不是新起线程。
- 监督线程阻塞等待子进程退出，同时每 0.5 秒查一次重启请求。
"""
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque

import common

LOG_LINES = 300


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.trigger = threading.Event()     # 重启/重载请求
        self.started_at = time.time()
        self.proc = None
        self.pid = None
        self.state = "starting"              # starting / running / reconnecting / error / stopped
        self.message = "正在启动…"
        self.config_error = ""
        self.restarts = 0
        self.last_exit = None
        self.last_start = None
        self.proxy_desc = {}
        self.bin_version = ""
        self.verify_note = ""
        self.conf_text = ""
        self.worker = None
        self.logs = deque(maxlen=LOG_LINES)

    def snapshot(self):
        with self.lock:
            return {
                "state": self.state,
                "message": self.message,
                "config_error": self.config_error,
                "pid": self.pid,
                "uptime": int(time.time() - self.last_start) if self.last_start else 0,
                "supervisor_uptime": int(time.time() - self.started_at),
                "restarts": self.restarts,
                "last_exit": self.last_exit,
                "proxy": dict(self.proxy_desc),
                "bin_version": self.bin_version,
                "verify_note": self.verify_note,
                "conf_text": self.conf_text,
                "conf_path": common.conf_path(),
                "port": common.PORT,
                "worker_alive": bool(self.worker and self.worker.is_alive()),
                "logs": list(self.logs),
            }


ST = State()


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text):
    """frpc 日志带 ANSI 颜色码，落到状态页前先剥掉，否则显示成乱码。"""
    return _ANSI_RE.sub("", text)


def log_line(text):
    stamp = time.strftime("%H:%M:%S")
    text = strip_ansi(text).rstrip()
    if not text:
        return
    with ST.lock:
        ST.logs.append("[%s] %s" % (stamp, text))


def summarize(s):
    addr = (s.get("SERVER_ADDR") or "").strip() or "（未填）"
    return {
        "server": "%s:%s" % (addr, s.get("SERVER_PORT") or "7000"),
        "user": (s.get("USER") or "").strip() or "(空)",
        "name": (s.get("PROXY_NAME") or "").strip(),
        "type": (s.get("PROXY_TYPE") or "tcp").lower(),
        "local": "%s:%s" % (s.get("LOCAL_IP") or "127.0.0.1", s.get("LOCAL_PORT") or ""),
        "remote": (s.get("REMOTE_PORT") or "0"),
        "domains": (s.get("CUSTOM_DOMAINS") or "").strip(),
        "subdomain": (s.get("SUBDOMAIN") or "").strip(),
        "auto_reconnect": (s.get("AUTO_RECONNECT") or "true").lower() == "true",
        "delay": s.get("RECONNECT_DELAY") or "10",
    }


def prepare(settings):
    """生成配置并做语法校验；返回 (ok, 错误信息, 配置文本)。"""
    try:
        path, text = common.write_conf(settings)
    except common.ConfigError as e:
        return False, str(e), ""
    except Exception as e:                                  # noqa: BLE001
        return False, "准备配置失败：%s" % e, ""
    with ST.lock:
        ST.conf_text = text
        ST.bin_version = common.binary_version()
    log_line("配置已生成：%s" % path)

    ok, out = common.verify_conf()
    if not ok:
        with ST.lock:
            ST.verify_note = out
        log_line("配置校验未通过：%s" % out)
        return False, "生成的配置未通过 frpc 校验：%s" % out, text
    with ST.lock:
        ST.verify_note = ""
    log_line("配置校验通过")
    return True, "", text


def _delay(settings):
    try:
        return max(3, min(600, int(settings.get("RECONNECT_DELAY") or 10)))
    except ValueError:
        return 10


def _auto_reconnect(settings):
    return (settings.get("AUTO_RECONNECT") or "true").lower() == "true"


def _kill(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:                                       # noqa: BLE001
        try:
            proc.kill()
        except Exception:                                   # noqa: BLE001
            pass


def _wait_proc_or_trigger(proc):
    """等子进程退出；期间收到重启请求就把它收掉。返回 (退出码, 是否因重启中断)。"""
    while True:
        try:
            return proc.wait(timeout=0.5), False
        except subprocess.TimeoutExpired:
            if ST.trigger.is_set():
                log_line("收到重启请求，结束当前隧道进程")
                _kill(proc)
                try:
                    return proc.wait(timeout=5), True
                except subprocess.TimeoutExpired:
                    return -9, True


_TUNNEL_OK = ("start proxy success", "login to server success")
_TUNNEL_DOWN = ("connect to server error", "login to the server failed",
                "connection refused", "i/o timeout", "proxy closing",
                "loginFailExit", "token in login doesn't match")


def _note_tunnel_state(line):
    """按 frpc 自己的日志行更新隧道通断，避免进程活着就报「运行中」。"""
    low = line.lower()
    if any(k in low for k in _TUNNEL_DOWN):
        with ST.lock:
            if ST.state in ("running", "starting"):
                ST.state = "reconnecting"
                ST.message = "与 frps 的连接已断开，正在自动重连…"
    elif any(k in line for k in _TUNNEL_OK):
        with ST.lock:
            if ST.state in ("reconnecting", "starting"):
                ST.state = "running"
                ST.message = "隧道已恢复，转发中"


def _tail_follow(log_path, proc):
    """把隧道进程写进文件里的日志读进内存，供状态页展示。"""

    def run():
        try:
            f = open(log_path, encoding="utf-8", errors="replace")
        except OSError:
            return
        f.seek(0, os.SEEK_END)
        buf = ""
        idle = 0
        while True:
            chunk = f.readline()
            if chunk:
                idle = 0
                buf += chunk
                if chunk.endswith("\n"):
                    line = buf.strip()
                    buf = ""
                    if line and not line.startswith("====="):
                        log_line(line)
                        _note_tunnel_state(strip_ansi(line))
                continue
            if proc.poll() is not None:
                idle += 1
                if idle >= 3:                # 进程结束后再多读几轮，收干净残余输出
                    break
            time.sleep(0.3)
        f.close()

    threading.Thread(target=run, daemon=True).start()


def _worker():
    """唯一的隧道监督循环（异常一律兜住，绝不让线程静默死掉）。"""
    try:
        _worker_loop()
    except Exception as e:                                  # noqa: BLE001
        import traceback
        log_line("监督线程异常：%s" % e)
        for line in traceback.format_exc().strip().splitlines()[-6:]:
            log_line("  " + line)
        with ST.lock:
            ST.state = "error"
            ST.message = "监督线程异常：%s" % e
            ST.worker = None


def _worker_loop():
    while True:
        ST.trigger.clear()
        settings = common.load_settings()
        with ST.lock:
            ST.proxy_desc = summarize(settings)

        ok, err, _ = prepare(settings)
        if not ok:
            with ST.lock:
                ST.state = "error"
                ST.config_error = err
                ST.message = err
            log_line("配置不完整，等待修改后重载：%s" % err)
            ST.trigger.wait()
            continue
        with ST.lock:
            ST.config_error = ""

        try:
            binary = common.binary_path()
        except common.ConfigError as e:
            with ST.lock:
                ST.state = "error"
                ST.message = str(e)
            log_line(str(e))
            ST.trigger.wait()
            continue

        log_path = os.path.join(common.log_dir(), "frpc.log")
        os.makedirs(common.log_dir(), exist_ok=True)
        logf = open(log_path, "a", encoding="utf-8", errors="replace")
        logf.write("\n===== %s 启动隧道 =====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        logf.flush()

        with ST.lock:
            ST.proxy_desc["binary"] = binary
            ST.state = "starting"
            ST.message = "正在连接 frps…"
            ST.last_start = time.time()
        log_line("启动 %s -c %s" % (binary, common.conf_path()))

        env = dict(os.environ)
        env.setdefault("HOME", "/root")
        proc = None
        try:
            proc = subprocess.Popen(
                [binary, "-c", common.conf_path()],
                stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                cwd=common.data_dir(), env=env, close_fds=True)
        except Exception as e:                              # noqa: BLE001
            logf.close()
            with ST.lock:
                ST.state = "error"
                ST.message = "无法启动隧道进程：%s" % e
            log_line("无法启动隧道进程：%s" % e)
            if ST.trigger.wait(timeout=_delay(settings) if _auto_reconnect(settings) else None):
                continue
            if not _auto_reconnect(settings):
                with ST.lock:
                    ST.state = "stopped"
                ST.trigger.wait()
            continue

        with ST.lock:
            ST.proc = proc
            ST.pid = proc.pid
        sys.stdout.flush()          # 让宿主/日志立刻看到 pid 与启动行
        _tail_follow(log_path, proc)

        rc, interrupted = _wait_proc_or_trigger(proc)
        logf.close()
        with ST.lock:
            ST.proc = None
            ST.pid = None
            ST.last_exit = rc
        log_line("隧道进程退出，退出码 %s" % rc)

        if interrupted or ST.trigger.is_set():
            continue                                   # 立即用新配置重启

        if not _auto_reconnect(settings):
            with ST.lock:
                ST.state = "stopped"
                ST.message = "隧道已停止（自动重连已关闭），退出码 %s" % rc
            log_line("自动重连已关闭，等待手动重启")
            ST.trigger.wait()
            continue

        delay = _delay(settings)
        with ST.lock:
            ST.state = "reconnecting"
            ST.message = "连接断开，%d 秒后重试" % delay
            ST.restarts += 1
        log_line("%d 秒后重连…" % delay)
        ST.trigger.wait(timeout=delay)


def restart():
    """请求重启/重载：只置事件，由唯一的监督线程处理。"""
    ST.trigger.set()


def start_worker():
    """启动监督线程（幂等）。"""
    with ST.lock:
        if ST.worker is not None and ST.worker.is_alive():
            return
        ST.state = "starting"
        ST.message = "正在启动…"
        ST.worker = threading.Thread(target=_worker, daemon=True)
        ST.worker.start()
