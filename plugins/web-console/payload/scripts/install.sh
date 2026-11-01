#!/bin/sh
# 网页控制台 · 安装
#
# 本插件是纯 Python 标准库 + 一份静态网页，安装只做两件事：
# 部署启动入口到 bin/（打包带不了执行位，是宿主按 <插件目录>/bin/<launchCommand> 执行的）、
# 检查静态页与 Python 是否就绪。
set -eu

TOOL_DIR="${JY_PLUGIN_DIR:-$(pwd)}"
PKG_DIR="${JY_PLUGIN_PAYLOAD:-$TOOL_DIR/payload}"
DATA_DIR="${JY_PLUGIN_DATA:-$TOOL_DIR/data}"

echo "=== 网页控制台 安装开始 ==="

if [ ! -f "$PKG_DIR/scripts/server.py" ]; then
    echo "安装失败：当前目录不是插件目录（找不到 payload/scripts/server.py）"
    exit 1
fi
echo "[1/4] 插件目录 $TOOL_DIR"

if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "安装失败：容器里没有 python3。请先在「组件」里安装 Python，再重新导入本插件。"
    exit 2
fi
echo "[2/4] Python：$("$PY" -V 2>&1)"

mkdir -p "$TOOL_DIR/bin"
cp -f "$PKG_DIR/bin/console-serve" "$TOOL_DIR/bin/console-serve"
chmod 755 "$TOOL_DIR/bin/console-serve"
echo "[3/4] 已部署 bin/console-serve（启动入口）"

if [ ! -f "$PKG_DIR/www/index.html" ]; then
    echo "安装失败：缺少 payload/www/index.html（网页本体），请向插件作者索取完整包"
    exit 3
fi

mkdir -p "$DATA_DIR/logs"
echo "[4/4] 数据目录 $DATA_DIR"
echo "=== 安装完成 ==="
echo "用法：设置里填「网页登录 key」和「App 控制令牌」，启动服务后浏览器打开："
echo "      本机/局域网  http://<设备 IP>:21872/"
echo "      公网访问     搭配「公网穿透」插件，把目标端口填 21872"
echo "      本机探活接口 http://127.0.0.1:21872/health"
