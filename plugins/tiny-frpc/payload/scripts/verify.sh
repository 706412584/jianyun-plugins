#!/bin/sh
# frpc 插件静态自检：只看文件齐备、语法与配置能否生成，不起服务
set -eu
TOOL_DIR="${JY_PLUGIN_DIR:-$(pwd)}"
PAYLOAD_DIR="${JY_PLUGIN_PAYLOAD:-$TOOL_DIR/payload}"
DATA_DIR="${JY_PLUGIN_DATA:-$TOOL_DIR/data}"

FAIL=0
note() { printf '  %s\n' "$1"; }
bad()  { printf '  [失败] %s\n' "$1"; FAIL=1; }

printf '== frpc 插件自检 ==\n'

printf '1) 文件齐备性\n'
for f in payload/server/common.py payload/server/cli.py payload/server/dashboard.py \
         payload/server/supervisor.py \
         payload/bin/frpc-serve payload/bin/frpc-cli payload/vendor/frpc \
         payload/scripts/install.sh payload/scripts/uninstall.sh payload/scripts/info.sh; do
    if [ -f "$TOOL_DIR/$f" ] || [ -f "$PAYLOAD_DIR/${f#payload/}" ]; then
        note "ok   $f"
    else
        bad "缺少 $f"
    fi
done
# manifest.json 由宿主读取，安装后可不在工具目录里，所以只做提示不判失败
if [ -f "$TOOL_DIR/manifest.json" ]; then
    note "ok   manifest.json"
else
    note "提示 manifest.json 不在工具目录（由宿主保存，正常）"
fi

printf '2) 运行时探测\n'
PY=""
for c in python3 python; do
    command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
done
if [ -n "$PY" ]; then
    note "ok   python：$("$PY" -c 'import sys; print(sys.version.split()[0])')"
else
    bad "没有 python3"
fi

printf '3) 配置能否生成（用默认值做一次试算，不联网）\n'
if [ -n "$PY" ]; then
    if (cd "$TOOL_DIR" && SERVER_ADDR=127.0.0.1 REMOTE_PORT=6000 LOCAL_PORT=8080 \
        "$PY" "$PAYLOAD_DIR/server/common.py" build >/tmp/frpc-verify.toml 2>/tmp/frpc-verify.err); then
        note "ok   配置模板可生成（$(wc -l </tmp/frpc-verify.toml | tr -d ' ') 行）"
    else
        bad "配置生成失败：$(head -2 /tmp/frpc-verify.err | tr '\n' ' ')"
    fi
    if "$PY" -m py_compile "$PAYLOAD_DIR/server/common.py" "$PAYLOAD_DIR/server/cli.py" \
        "$PAYLOAD_DIR/server/dashboard.py" "$PAYLOAD_DIR/server/supervisor.py" 2>/tmp/frpc-compile.err; then
        note "ok   python 源码语法正确"
    else
        bad "python 语法错误：$(head -3 /tmp/frpc-compile.err | tr '\n' ' ')"
    fi
    rm -rf "$PAYLOAD_DIR/server/__pycache__" 2>/dev/null || true
fi

printf '4) 二进制可执行性\n'
BIN=""
if [ -x "$TOOL_DIR/bin/frpc" ]; then
    BIN="$TOOL_DIR/bin/frpc"
elif [ -x "$PAYLOAD_DIR/vendor/frpc" ]; then
    BIN="$PAYLOAD_DIR/vendor/frpc"
fi
if [ -n "$BIN" ]; then
    note "ok   $BIN → $("$BIN" --version 2>/dev/null | head -1)"
else
    bad "frpc 二进制缺失或不可执行"
fi

printf '5) 监听端口占用\n'
if (echo >/dev/tcp/127.0.0.1/18899) >/dev/null 2>&1; then
    note "注意 127.0.0.1:18899 已有服务响应（可能是本插件已在跑）"
else
    note "ok   127.0.0.1:18899 空闲"
fi

printf '6) 目录\n'
mkdir -p "$DATA_DIR/logs" 2>/dev/null || true
[ -d "$DATA_DIR" ] && note "ok   数据目录 $DATA_DIR" || bad "数据目录不可写"

if [ "$FAIL" -eq 0 ]; then
    printf '== 自检通过 ==\n'
    exit 0
fi
printf '== 自检未通过，见上面 [失败] 项 ==\n' >&2
exit 1
