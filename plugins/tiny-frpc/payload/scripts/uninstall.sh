#!/bin/sh
# frpc 插件卸载：清理自建文件与进程；按约定保留 settings.json 与 data/
set -eu
TOOL_DIR="${JY_PLUGIN_DIR:-$(pwd)}"
DATA_DIR="${JY_PLUGIN_DATA:-$TOOL_DIR/data}"

# 停掉隧道进程与控制台（先温和后强制）
if command -v pkill >/dev/null 2>&1; then
    pkill -f "$TOOL_DIR/bin/frpc " >/dev/null 2>&1 || true
    pkill -f "$TOOL_DIR/bin/frpc-serve" >/dev/null 2>&1 || true
    pkill -f "$TOOL_DIR/bin/tiny-frpc" >/dev/null 2>&1 || true
fi
if command -v pgrep >/dev/null 2>&1; then
    for i in 1 2 3 4 5; do
        pgrep -f "$TOOL_DIR/bin/frpc" >/dev/null 2>&1 || break
        sleep 1
    done
    if pgrep -f "$TOOL_DIR/bin/frpc" >/dev/null 2>&1; then
        pkill -9 -f "$TOOL_DIR/bin/frpc" >/dev/null 2>&1 || true
    fi
fi

rm -f "$TOOL_DIR/bin/frpc-serve" "$TOOL_DIR/bin/frpc-cli" "$TOOL_DIR/bin/frpc"
rm -f "$TOOL_DIR/bin/tiny-frpc-serve" "$TOOL_DIR/bin/tiny-frpc-cli" "$TOOL_DIR/bin/tiny-frpc"
rmdir "$TOOL_DIR/bin" 2>/dev/null || true

printf '卸载完成：已清理可执行文件与运行进程。\n'
printf '保留：%s（设置）与 %s（配置/日志）\n' \
    "${TOOL_DIR}/settings.json" "$DATA_DIR"
