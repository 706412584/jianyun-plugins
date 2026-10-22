#!/bin/sh
# 示例常驻服务：用 busybox/nc 之外的纯 shell 方式起一个极简 HTTP 服务。
# 优先用 python3（容器基础依赖里已装），没有就报错退出——比假装成功更容易排查。

PORT=18080
GREETING="${GREETING:-hello from jianyun}"

if command -v python3 >/dev/null 2>&1; then
  exec python3 - "$PORT" "$GREETING" <<'PY'
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

port = int(sys.argv[1])
greeting = sys.argv[2]

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (greeting + "\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("[hello-service] " + (fmt % args) + "\n")

HTTPServer(("127.0.0.1", port), Handler).serve_forever()
PY
fi

echo "hello-service: 需要 python3，请先在「组件 → Python」安装" >&2
exit 1
