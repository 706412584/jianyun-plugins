#!/bin/sh
# 网页控制台 · 卸载
#
# 只清部署出来的入口：设置（settings.json）与 data/ 由宿主保留，重装回来还是同一套配置。
set -eu

TOOL_DIR="${JY_PLUGIN_DIR:-$(pwd)}"
DATA_DIR="${JY_PLUGIN_DATA:-$TOOL_DIR/data}"

echo "=== 网页控制台 卸载 ==="
rm -f "$TOOL_DIR/bin/console-serve"
echo "已清掉启动入口；设置与日志保留在 $DATA_DIR"
