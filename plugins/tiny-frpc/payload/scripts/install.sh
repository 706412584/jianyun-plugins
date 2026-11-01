#!/bin/sh
# frpc 插件安装：探测运行时、部署二进制与入口、建目录、写默认设置
set -eu
TOOL_DIR="${JY_PLUGIN_DIR:-$(pwd)}"
PAYLOAD_DIR="${JY_PLUGIN_PAYLOAD:-$TOOL_DIR/payload}"
DATA_DIR="${JY_PLUGIN_DATA:-$TOOL_DIR/data}"

PY=""
for c in python3 python; do
    command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
done
if [ -z "$PY" ]; then
    printf '错误：容器里没有 python3，请在简云「组件」里安装 Python 后重新导入本插件。\n' >&2
    exit 1
fi

for f in server/common.py server/cli.py server/dashboard.py server/supervisor.py \
         bin/frpc-serve bin/frpc-cli vendor/frpc; do
    if [ ! -f "$PAYLOAD_DIR/$f" ]; then
        printf '错误：插件包缺少 payload/%s，请重新导入。\n' "$f" >&2
        exit 1
    fi
done

mkdir -p "$TOOL_DIR/bin" "$DATA_DIR/logs"

cp -f "$PAYLOAD_DIR/bin/frpc-serve" "$TOOL_DIR/bin/frpc-serve"
cp -f "$PAYLOAD_DIR/bin/frpc-cli" "$TOOL_DIR/bin/frpc-cli"
cp -f "$PAYLOAD_DIR/vendor/frpc" "$TOOL_DIR/bin/frpc"
# zip 不保留执行位，必须在这里补，否则启动报权限错误
chmod 755 "$TOOL_DIR/bin/frpc-serve" "$TOOL_DIR/bin/frpc-cli" "$TOOL_DIR/bin/frpc"

# 清掉旧版 tiny-frpc 遗留文件（从 SSH 版升级过来时）
rm -f "$TOOL_DIR/bin/tiny-frpc-serve" "$TOOL_DIR/bin/tiny-frpc-cli" "$TOOL_DIR/bin/tiny-frpc"

if [ ! -f "$TOOL_DIR/settings.json" ]; then
    printf '{\n  "SERVER_ADDR": "",\n  "SERVER_PORT": "7000",\n  "AUTH_TOKEN": "",\n  "TLS_ENABLE": "true",\n  "USER": "",\n  "PROXY_NAME": "tcp-tunnel",\n  "PROXY_TYPE": "tcp",\n  "LOCAL_IP": "127.0.0.1",\n  "LOCAL_PORT": "22",\n  "REMOTE_PORT": "0",\n  "CUSTOM_DOMAINS": "",\n  "SUBDOMAIN": "",\n  "SECRET_KEY": "",\n  "EXTRA_TOML": "",\n  "AUTO_RECONNECT": "true",\n  "RECONNECT_DELAY": "10"\n}\n' > "$TOOL_DIR/settings.json"
fi

# 二进制可执行性自检
if ! "$TOOL_DIR/bin/frpc" --version >/dev/null 2>&1; then
    printf '警告：%s 无法在本机运行（架构不匹配或权限不足）。\n' "$TOOL_DIR/bin/frpc" >&2
fi

BIN_VER="$("$TOOL_DIR/bin/frpc" --version 2>/dev/null | head -1 || echo '未知')"
printf '安装完成：%s\n' "$TOOL_DIR"
printf '  frpc 版本 : %s\n' "$BIN_VER"
printf '  python    : %s\n' "$("$PY" -c 'import sys; print(sys.version.split()[0])')"
printf '  下一步    : 在插件设置里填 frps 地址、服务端端口(bindPort)、认证 Token，然后启动。\n'
