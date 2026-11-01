#!/bin/sh
# 网页控制台 · 自检
#
# 只做静态检查，不起服务（起服务是「启动」按钮的事）：
# 文件齐不齐、Python 在不在、脚本有没有语法错。
set -eu

TOOL_DIR="${JY_PLUGIN_DIR:-$(pwd)}"
PKG_DIR="${JY_PLUGIN_PAYLOAD:-$TOOL_DIR/payload}"

fail() {
    echo "自检失败：$1"
    exit 1
}

echo "=== 网页控制台 自检 ==="

[ -f "$PKG_DIR/scripts/server.py" ] || fail "缺少 payload/scripts/server.py"
[ -f "$PKG_DIR/bin/console-serve" ] || fail "缺少 payload/bin/console-serve"
[ -f "$PKG_DIR/www/index.html" ] || fail "缺少 payload/www/index.html"
[ -f "$PKG_DIR/www/login.html" ] || fail "缺少 payload/www/login.html"
[ -f "$PKG_DIR/www/app.js" ] || fail "缺少 payload/www/app.js"
[ -f "$PKG_DIR/www/style.css" ] || fail "缺少 payload/www/style.css"
echo "[1/3] 文件齐全"

if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    fail "容器里没有 python3/python，请先在「组件」里安装 Python"
fi
echo "[2/3] Python：$("$PY" -V 2>&1)"

"$PY" -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" "$PKG_DIR/scripts/server.py" \
    || fail "server.py 语法有错"
echo "[3/3] server.py 语法正常"

echo "=== 自检通过 ==="
