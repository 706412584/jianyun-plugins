#!/usr/bin/env python3
"""网页控制台 · 服务端。

一条链路：浏览器 → 本插件（登录 + 反代）→ App 本地控制接口（127.0.0.1）。

本插件自己只做三件事：发/校验登录 cookie、把 /api/* 转发给 App、把 www/ 下的静态页发出去。
真正的能力（站点、组件、插件、AI）全在 App 侧，插件不重复实现——两套真相必然会对不上。

全部用标准库：容器里不保证能 pip install，也不能引入第三方依赖。
"""
import hmac
import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

SERVICE_PORT = 21872
COOKIE_NAME = "jykey"
START = time.time()

HERE = os.path.dirname(os.path.abspath(__file__))
WWW_DIR = os.path.normpath(os.path.join(HERE, "..", "www"))
TOOL_DIR = os.environ.get("JY_PLUGIN_DIR") or os.path.normpath(os.path.join(HERE, "..", ".."))

DEFAULTS = {"CONSOLE_KEY": "", "APP_TOKEN": "", "APP_PORT": "21871"}

# 未登录也得能拿到的文件：登录页本身与它要用的样式脚本。
# 少了这个白名单，未登录访问 /login.html 会被当成「未登录」又跳向 /login.html，
# 浏览器卡在重定向循环里报「无法访问」。
PUBLIC_FILES = {"/login.html", "/style.css"}


def is_public(path):
    """登录前可访问的静态资源：登录页自身，以及设计系统的样式脚本。"""
    return path in PUBLIC_FILES or path.startswith("/assets/")


MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
}


def load_settings():
    """取值优先级：环境变量（宿主注入）→ settings.json → 默认值。"""
    values = dict(DEFAULTS)
    path = os.path.join(TOOL_DIR, "settings.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as handle:
                stored = json.load(handle)
            if isinstance(stored, dict):
                for key in values:
                    if stored.get(key) not in (None, ""):
                        values[key] = str(stored[key])
        except Exception as exc:  # 坏配置不能拖垮服务
            print("[web-console] 读取 settings.json 失败：%s" % exc, file=sys.stderr, flush=True)
    for key in values:
        if os.environ.get(key) not in (None, ""):
            values[key] = os.environ[key]
    return values


class Handler(BaseHTTPRequestHandler):
    server_version = "JianYunWebConsole/0.3"
    # HTTP/1.0 下每个资源都要新建一次 TCP 连接，一个页面十几个请求会被串行化，
    # 刷新起来明显卡。改为 1.1 复用连接（所有响应都带 Content-Length，不会挂住浏览器）。
    protocol_version = "HTTP/1.1"
    console_key = ""
    app_token = ""
    app_port = 21871

    # ---------- 基础响应 ----------

    def _send(self, code, body, content_type="text/plain; charset=utf-8", extra_headers=None, cache="no-store"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for name, value in (extra_headers or []):
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, payload):
        self._send(code, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")

    def _redirect(self, location, extra_headers=None):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra_headers or []):
            self.send_header(name, value)
        self.end_headers()

    # ---------- 登录 ----------

    def _cookie_value(self, name):
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return ""

    def _logged_in(self):
        if not self.console_key:
            return False
        return hmac.compare_digest(self._cookie_value(COOKIE_NAME), self.console_key)

    def _do_login(self, body):
        given = (parse_qs(body.decode("utf-8", "replace")).get("key") or [""])[0]
        if self.console_key and given and hmac.compare_digest(given, self.console_key):
            cookie = "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000" % (COOKIE_NAME, self.console_key)
            self._redirect("/", [("Set-Cookie", cookie)])
        else:
            self._redirect("/login.html?error=1")

    # ---------- 静态文件 ----------

    def _serve_static(self, path):
        rel = unquote(path).lstrip("/") or "index.html"
        target = os.path.normpath(os.path.join(WWW_DIR, rel))
        if not target.startswith(WWW_DIR + os.sep) and target != WWW_DIR:
            self._send(404, "找不到页面")
            return
        if os.path.isdir(target):
            target = os.path.join(target, "index.html")
        if not os.path.isfile(target):
            self._send(404, "找不到页面")
            return
        ext = os.path.splitext(target)[1].lower()
        # HTML 不缓存（免得改了页面用户还看到旧的）；样式脚本用 ETag 校验缓存，
        # 换插件版本时浏览器能立即拿到新文件，而没变时只回一个 304
        is_html = ext in (".html", "")
        try:
            info = os.stat(target)
            etag = '"%x-%x"' % (int(info.st_mtime), info.st_size)
        except OSError:
            etag = None
        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-store" if is_html else "no-cache")
            self.end_headers()
            return
        with open(target, "rb") as handle:
            data = handle.read()
        headers = [] if etag is None else [("ETag", etag)]
        self._send(
            200,
            data,
            MIME.get(ext, "application/octet-stream"),
            extra_headers=headers,
            # `no-cache` 而不是 `max-age`：插件更新很频繁，一旦让浏览器直接拿缓存，
            # 用户重装了插件却还在跑旧 JS，会看到已经修好的问题「依然存在」。
            # no-cache 每次都会带 ETag 来校验，命中 304 时不传 body，性能一样好。
            cache="no-store" if is_html else "no-cache",
        )

    # ---------- 反代到 App ----------

    def _proxy(self, method, path, body=None):
        url = "http://127.0.0.1:%d%s" % (self.app_port, path)
        request = urllib.request.Request(url, method=method, data=body)
        request.add_header("Authorization", "Bearer %s" % self.app_token)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, response.read(), response.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), "application/json; charset=utf-8"
        except Exception as exc:
            message = json.dumps(
                {
                    "ok": False,
                    "error": "连不上 App 控制接口（%s）。请确认 App 在运行、接口已启用，且端口与设置里的 %d 一致。"
                    % (exc, self.app_port),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            return 502, message, "application/json; charset=utf-8"

    # ---------- 路由 ----------

    def do_GET(self):
        self._route("GET", None)

    def do_POST(self):
        length = min(int(self.headers.get("Content-Length") or 0), 1 << 20)
        body = self.rfile.read(length) if length else b""
        self._route("POST", body)

    def _route(self, method, body):
        parsed = urlparse(self.path)
        path = parsed.path

        # 宿主探活走这里：不需要登录，响应体带自己的名字以便和占用同端口的别的服务区分
        if path == "/health":
            self._json(200, {"status": "ok", "name": "web-console", "uptimeSec": round(time.time() - START)})
            return

        if path == "/login":
            self._do_login(body or b"")
            return
        if path == "/logout":
            cookie = "%s=; Path=/; HttpOnly; Max-Age=0" % COOKIE_NAME
            self._redirect("/login.html", [("Set-Cookie", cookie)])
            return

        if not self._logged_in():
            if path.startswith("/api/"):
                self._json(401, {"ok": False, "error": "未登录"})
            elif is_public(path):
                self._serve_static(path)
            else:
                self._redirect("/login.html")
            return

        if path.startswith("/api/"):
            status, data, content_type = self._proxy(method, self.path, body if method == "POST" else None)
            self._send(status, data, content_type)
            return

        if path == "/" or path.endswith("/"):
            path = path + "index.html"
        self._serve_static(path)

    def log_message(self, fmt, *args):
        print("[web-console] " + fmt % args, flush=True)


def main():
    settings = load_settings()
    Handler.console_key = settings["CONSOLE_KEY"].strip()
    Handler.app_token = settings["APP_TOKEN"].strip()
    try:
        Handler.app_port = int(settings["APP_PORT"])
    except ValueError:
        Handler.app_port = int(DEFAULTS["APP_PORT"])

    httpd = ThreadingHTTPServer(("0.0.0.0", SERVICE_PORT), Handler)

    def stop(signum, _frame):
        print("[web-console] 收到信号 %s，退出中" % signum, flush=True)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    if not Handler.console_key:
        print("[web-console] 警告：还没设置网页登录 key，网页会一直要求登录。请在插件设置里填「网页登录 key」。", file=sys.stderr, flush=True)
    print("[web-console] 已监听 0.0.0.0:%d（网页 http://127.0.0.1:%d/ ，App 接口 127.0.0.1:%d）" % (SERVICE_PORT, SERVICE_PORT, Handler.app_port), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
