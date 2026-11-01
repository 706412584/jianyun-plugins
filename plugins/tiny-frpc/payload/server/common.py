#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""frpc 插件共用模块：设置读取、frpc.toml 生成。

适配标准 frp 服务端（frps）：走 bindPort + token 认证，不再走 SSH 隧道网关。
"""
import json
import os
import subprocess
import sys

PORT = 18899

DEFAULTS = {
    "SERVER_ADDR": "",
    "SERVER_PORT": "7000",
    "AUTH_TOKEN": "",
    "TLS_ENABLE": "true",
    "USER": "",
    "PROXY_NAME": "tcp-tunnel",
    "PROXY_TYPE": "tcp",
    "LOCAL_IP": "127.0.0.1",
    "LOCAL_PORT": "22",
    "REMOTE_PORT": "0",
    "CUSTOM_DOMAINS": "",
    "SUBDOMAIN": "",
    "SECRET_KEY": "",
    "EXTRA_TOML": "",
    "AUTO_RECONNECT": "true",
    "RECONNECT_DELAY": "10",
}

TOML_QUOTE = json.dumps  # JSON 字符串字面量与 TOML 基本字符串兼容


class ConfigError(Exception):
    pass


def tool_dir():
    env = os.environ.get("JY_PLUGIN_DIR")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))      # .../payload/server
    return os.path.dirname(os.path.dirname(here))          # .../<tool>


def payload_dir():
    env = os.environ.get("JY_PLUGIN_PAYLOAD")
    if env and os.path.isdir(env):
        return env
    return os.path.join(tool_dir(), "payload")


def data_dir():
    env = os.environ.get("JY_PLUGIN_DATA")
    if env:
        return env
    return os.path.join(tool_dir(), "data")


def log_dir():
    return os.path.join(data_dir(), "logs")


def conf_path():
    return os.path.join(data_dir(), "frpc.toml")


def settings_path():
    return os.path.join(tool_dir(), "settings.json")


def load_settings():
    """settings.json 打底，宿主注入的环境变量覆盖（环境变量优先）。"""
    s = dict(DEFAULTS)
    try:
        with open(settings_path(), encoding="utf-8") as f:
            disk = json.load(f)
        if isinstance(disk, dict):
            for k, v in disk.items():
                if k in s and v is not None:
                    s[k] = str(v)
    except Exception:
        pass
    for k in list(s.keys()):
        v = os.environ.get(k)
        if v is not None and v != "":
            s[k] = v
    return s


def _int(value, name, default=0, allow_zero=True):
    v = (value or "").strip()
    if v == "":
        v = str(default)
    try:
        n = int(v)
    except ValueError:
        raise ConfigError("%s 必须是数字，当前填的是 %r" % (name, value))
    if n < 0 or n > 65535:
        raise ConfigError("%s 超出范围（0-65535）：%d" % (name, n))
    if not allow_zero and n == 0:
        raise ConfigError("%s 不能为 0" % name)
    return n


def _bool(value, default=True):
    return (str(value).strip().lower() if value is not None else str(default).lower()) == "true"


def _norm_domain(raw):
    """域名规范化：剥掉 http:// 前缀与结尾斜杠，避免直接写进 vhost 匹配失败。

    用户常在「自定义域名」里顺手填成完整 URL（http://g.920zm.cn），
    而 frps 是按 Host 精确匹配域名的，带协议前缀永远匹配不上。
    """
    d = (raw or "").strip()
    low = d.lower()
    for prefix in ("http://", "https://"):
        if low.startswith(prefix):
            d = d[len(prefix):]
            break
    return d.rstrip("/").strip()


def build_toml(s):
    """按设置生成标准 frpc 能解析的 TOML 配置。"""
    addr = (s.get("SERVER_ADDR") or "").strip()
    if not addr:
        raise ConfigError("还没填 frps 服务器地址（设置 → 连接 → frps 服务器地址）")

    ptype = (s.get("PROXY_TYPE") or "tcp").strip().lower()
    if ptype not in ("tcp", "http", "https", "stcp"):
        raise ConfigError("代理类型只支持 tcp / http / https / stcp，当前是 %r" % ptype)

    lines = [
        "# 由 frpc 插件自动生成，手改会在下次启动时被覆盖",
        "serverAddr = %s" % TOML_QUOTE(addr, ensure_ascii=False),
        "serverPort = %d" % _int(s.get("SERVER_PORT"), "服务端端口", 7000, allow_zero=False),
        "# 首次登录失败不要直接退出，交给重连逻辑继续试",
        "loginFailExit = false",
    ]

    user = (s.get("USER") or "").strip()
    if user:
        lines.append("user = %s" % TOML_QUOTE(user, ensure_ascii=False))

    token = (s.get("AUTH_TOKEN") or "").strip()
    if token:
        lines.append('auth.method = "token"')
        lines.append("auth.token = %s" % TOML_QUOTE(token, ensure_ascii=False))

    if not _bool(s.get("TLS_ENABLE"), True):
        lines.append("transport.tls.enable = false")

    name = (s.get("PROXY_NAME") or "").strip() or "tcp-tunnel"
    local_ip = (s.get("LOCAL_IP") or "").strip() or "127.0.0.1"
    local_port = _int(s.get("LOCAL_PORT"), "本地端口", 22, allow_zero=False)

    lines += [
        "",
        "[[proxies]]",
        "name = %s" % TOML_QUOTE(name, ensure_ascii=False),
        "type = %s" % TOML_QUOTE(ptype),
        "localIP = %s" % TOML_QUOTE(local_ip, ensure_ascii=False),
        "localPort = %d" % local_port,
    ]

    if ptype == "tcp":
        remote = _int(s.get("REMOTE_PORT"), "远程端口", 0)
        if remote == 0:
            raise ConfigError("TCP 代理必须填远程端口（frps 上对外开放的端口）")
        lines.append("remotePort = %d" % remote)
    elif ptype == "stcp":
        secret = (s.get("SECRET_KEY") or "").strip()
        if not secret:
            raise ConfigError("STCP 代理必须填 secretKey（访客侧要用同一个值）")
        lines.append("secretKey = %s" % TOML_QUOTE(secret, ensure_ascii=False))
    else:  # http / https
        domains = [_norm_domain(d) for d in (s.get("CUSTOM_DOMAINS") or "").split(",")]
        domains = [d for d in domains if d]
        subdomain = _norm_domain(s.get("SUBDOMAIN"))
        if not domains and not subdomain:
            raise ConfigError("%s 代理至少要填自定义域名或子域名之一" % ptype.upper())
        if domains:
            lines.append("customDomains = [%s]" % ", ".join(
                TOML_QUOTE(d, ensure_ascii=False) for d in domains))
        if subdomain:
            lines.append("subdomain = %s" % TOML_QUOTE(subdomain, ensure_ascii=False))

    extra = (s.get("EXTRA_TOML") or "").strip()
    if extra:
        lines += ["", "# ---- 附加 TOML 片段（来自设置）----", extra]

    return "\n".join(lines) + "\n"


def write_conf(s):
    text = build_toml(s)
    os.makedirs(data_dir(), exist_ok=True)
    path = conf_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path, text


def verify_conf():
    """调用 frpc verify 做语法校验，返回 (ok, 输出)。"""
    try:
        binary = binary_path()
    except ConfigError as e:
        return False, str(e)
    if not os.path.exists(conf_path()):
        return False, "配置文件还不存在"
    r = subprocess.run([binary, "verify", "-c", conf_path()],
                       capture_output=True, text=True, timeout=30)
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0, out.strip()


def binary_path():
    """优先用安装时部署到 bin/ 的二进制。"""
    candidates = [
        os.path.join(tool_dir(), "bin", "frpc"),
        os.path.join(payload_dir(), "vendor", "frpc"),
    ]
    for c in candidates:
        if os.path.exists(c) and os.access(c, os.X_OK):
            return c
    for c in candidates:
        if os.path.exists(c):
            os.chmod(c, 0o755)
            return c
    raise ConfigError("找不到 frpc 可执行文件，请重新导入插件")


def binary_version():
    try:
        binary = binary_path()
    except ConfigError:
        return "未安装"
    try:
        r = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15)
        return (r.stdout or r.stderr or "").strip().splitlines()[0] or "未知"
    except Exception:                                       # noqa: BLE001
        return "读取失败"


def _main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    s = load_settings()
    if cmd == "build":
        sys.stdout.write(build_toml(s))
        return 0
    if cmd == "write-conf":
        path, _ = write_conf(s)
        print(path)
        return 0
    if cmd == "verify":
        ok, out = verify_conf()
        print(out)
        return 0 if ok else 1
    if cmd == "binary":
        print(binary_path())
        return 0
    if cmd == "version":
        print(binary_version())
        return 0
    print("用法: common.py build|write-conf|verify|binary|version", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(_main(sys.argv))
    except ConfigError as e:
        print("配置错误：%s" % e, file=sys.stderr)
        sys.exit(3)
