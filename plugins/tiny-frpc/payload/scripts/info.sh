#!/bin/sh
# frpc 插件信息区：stdout 必须是合法 JSON
set -eu
TOOL_DIR="${JY_PLUGIN_DIR:-$(pwd)}"
PAYLOAD_DIR="${JY_PLUGIN_PAYLOAD:-$TOOL_DIR/payload}"
DATA_DIR="${JY_PLUGIN_DATA:-$TOOL_DIR/data}"

PY=""
for c in python3 python; do
    command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
done

jesc() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/ /g' | tr -d '\r\n'; }

ADDR="${SERVER_ADDR:-}"
PORT="${SERVER_PORT:-7000}"
TOKEN_SET="未设置"
[ -n "${AUTH_TOKEN:-}" ] && TOKEN_SET="已设置"
PNAME="${PROXY_NAME:-tcp-tunnel}"
PTYPE="$(printf '%s' "${PROXY_TYPE:-tcp}" | tr 'a-z' 'A-Z')"
LPORT="${LOCAL_PORT:-22}"
RPORT="${REMOTE_PORT:-0}"

STATE="未运行"
if [ -n "$PY" ]; then
    SNAP="$("$PY" - <<'PYEOF' 2>/dev/null || true
import json, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:18899/api/status", timeout=3) as r:
        d = json.loads(r.read().decode())
    print("%s|%s|%s" % (d.get("state", "-"), d.get("pid") or "-", d.get("message", "")))
except Exception:
    print("offline|-|-")
PYEOF
)"
    case "$SNAP" in
        offline|"") STATE="未运行（点插件页「启动」）" ;;
        *) STATE="$(printf '%s' "$SNAP" | cut -d'|' -f1) / PID $(printf '%s' "$SNAP" | cut -d'|' -f2)" ;;
    esac
fi

BIN_VER="未知"
if [ -x "$TOOL_DIR/bin/frpc" ]; then
    BIN_VER="$("$TOOL_DIR/bin/frpc" --version 2>/dev/null | head -1)"
elif [ -x "$PAYLOAD_DIR/vendor/frpc" ]; then
    BIN_VER="$("$PAYLOAD_DIR/vendor/frpc" --version 2>/dev/null | head -1)"
fi

if [ -n "$ADDR" ]; then
    SERVER_LINE="$ADDR:$PORT"
else
    SERVER_LINE="未填写（设置 → 连接）"
fi

printf '{"sections":['
printf '{"title":"隧道状态","items":['
printf '{"label":"运行状态","value":"%s"},' "$(jesc "$STATE")"
printf '{"label":"frps 服务器","value":"%s","mono":true},' "$(jesc "$SERVER_LINE")"
printf '{"label":"认证 Token","value":"%s"},' "$(jesc "$TOKEN_SET")"
printf '{"label":"代理名 / 类型","value":"%s / %s"}' "$(jesc "$PNAME")" "$PTYPE"
printf ']},'
printf '{"title":"转发规则","items":['
printf '{"label":"本地监听","value":"%s:%s","mono":true},' "$(jesc "${LOCAL_IP:-127.0.0.1}")" "$(jesc "$LPORT")"
if [ "$PTYPE" = "TCP" ]; then
    printf '{"label":"远程端口","value":"%s","mono":true},' "$(jesc "$RPORT")"
else
    printf '{"label":"域名","value":"%s"},' "$(jesc "${CUSTOM_DOMAINS:-（未填）}")"
fi
printf '{"label":"自动重连","value":"%s（间隔 %s 秒）"}' "$(jesc "${AUTO_RECONNECT:-true}")" "$(jesc "${RECONNECT_DELAY:-10}")"
printf ']},'
printf '{"title":"打开方式","items":['
printf '{"label":"状态页","value":"http://127.0.0.1:18899/","hint":"本机浏览器打开","mono":true},'
printf '{"label":"健康检查","value":"http://127.0.0.1:18899/health","hint":"返回 JSON，供探活","mono":true}'
printf ']},'
printf '{"title":"运行环境","text":"frpc %s\\n配置文件：%s\\n插件目录：%s"}' \
    "$(jesc "$BIN_VER")" "$(jesc "$DATA_DIR/frpc.toml")" "$(jesc "$TOOL_DIR")"
printf ']}\n'
