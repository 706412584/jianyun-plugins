#!/usr/bin/env python3
"""
插件包校验。PR（源目录）与 Issue（上传的 zip）两条路径共用。

用法：
    python tools/validate.py plugins/hello-service          # 校验源目录
    python tools/validate.py --zip foo.zip                  # 校验 zip 包
    python tools/validate.py plugins/hello-service --json   # 机器可读输出

退出码：0 = 通过（可能有警告），1 = 拒绝。

设计取舍：宁可误拒也不要放过。误拒用户会来问，放过则可能删库。
但**警告**不拒绝——比如未声明的外连域名，插件作者可能有正当理由。
"""
import argparse
import json
import os
import re
import sys
import zipfile

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
REQUIRED = ("id", "name", "version", "description", "publisher")

# `requires[].component` 允许的值：App 侧 ComponentFamily 枚举的 id 全集。
# 两边必须同步——App 只认这些，写别的会在安装时被拦下。
COMPONENT_IDS = frozenset({
    "php", "python", "node", "go", "jdk", "gradle", "rust",
    "android_sdk", "android_ndk",
    "mariadb", "postgresql", "nginx", "redis", "memcached",
    "container", "phpmyadmin", "adminer", "composer", "maven",
})
MAX_ZIP_BYTES = 20 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_ENTRIES = 2000

# 二进制魔数：插件包里的 payload 只允许脚本，不允许编译产物。
# 理由：二进制无法审阅，且容器架构与宿主不同，放进去多半也跑不起来。
BINARY_MAGIC = (
    b"\x7fELF",                      # ELF
    b"MZ",                           # PE
    b"\xca\xfe\xba\xbe",             # Mach-O fat
    b"\xcf\xfa\xed\xfe",             # Mach-O 64 LE
    b"\xce\xfa\xed\xfe",             # Mach-O 32 LE
)

DANGEROUS_PATTERNS = (
    (re.compile(r"rm\s+-rf?\s+/(\s|$|\*)"), "rm -rf /"),
    (re.compile(r"rm\s+-rf?\s+/\*"), "rm -rf /*"),
    (re.compile(r"(curl|wget)[^|;&]*\|\s*(ba)?sh"), "curl/wget 直接管道到 shell"),
    (re.compile(r"chmod\s+777\s+/(\s|$)"), "chmod 777 /"),
    (re.compile(r"/data/data/"), "写入宿主应用私有目录"),
    (re.compile(r"/system/(bin|lib|etc)"), "写入 Android 系统目录"),
    (re.compile(r"mkfs\."), "格式化文件系统"),
    (re.compile(r"dd\s+if=.*of=/dev/"), "直接写块设备"),
)

DOMAIN_RE = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    @property
    def ok(self):
        return not self.errors


def load_manifest(text):
    """解析清单。JSON 坏了直接抛，由调用方转成错误。"""
    return json.loads(text)


def check_manifest(manifest, report):
    """字段级检查。返回 False 表示不必继续。"""
    for key in REQUIRED:
        if not str(manifest.get(key, "")).strip():
            report.error(f"manifest 缺少必填字段：{key}")

    pid = str(manifest.get("id", "")).strip()
    if pid and not ID_RE.match(pid):
        report.error(
            f"id 不合法：{pid!r}。要求：字母或数字开头，仅含 A-Za-z0-9._-，最长 64 字符"
        )

    version = str(manifest.get("version", "")).strip()
    if version and not re.match(r"^\d+(\.\d+)*", version):
        report.error(f"version 不合法：{version!r}。要求数字开头，如 1.2.0")

    port = manifest.get("servicePort", 0)
    if port and (not isinstance(port, int) or not (1 <= port <= 65535)):
        report.error(f"servicePort 不合法：{port!r}")

    steps = manifest.get("installSteps", [])
    if not isinstance(steps, list):
        report.error("installSteps 必须是数组")
    else:
        for i, step in enumerate(steps):
            if not isinstance(step, str):
                report.error(f"installSteps[{i}] 必须是字符串")

    for key in ("permissions", "architectures"):
        v = manifest.get(key, [])
        if not isinstance(v, list):
            report.error(f"{key} 必须是数组")

    # 组件依赖：只认宿主内置的组件 id。写错一个字母会在用户装机时才发现
    # （App 侧把不认识的 id 当未满足并阻断安装），不如在投稿校验就拦下。
    requires = manifest.get("requires", [])
    if requires and not isinstance(requires, list):
        report.error("requires 必须是数组")
    elif isinstance(requires, list):
        for i, item in enumerate(requires):
            if not isinstance(item, dict):
                report.error(f"requires[{i}] 必须是对象")
                continue
            comp = str(item.get("component", "")).strip()
            if not comp:
                report.error(f"requires[{i}] 缺少 component")
            elif comp not in COMPONENT_IDS:
                report.error(
                    f"requires[{i}] 的 component 不支持：{comp!r}。"
                    f"可选：{', '.join(sorted(COMPONENT_IDS))}"
                )
            minv = item.get("minVersion")
            if minv is not None and not re.match(r"^\d+(\.\d+)*", str(minv).strip()):
                report.error(f"requires[{i}] 的 minVersion 不合法：{minv!r}。要求数字开头，如 3.11")

    # 清单里的命令字段同样会在容器里以 root 执行，必须与 payload/ 里的脚本同等待遇。
    # 只扫 payload/ 是不够的：installSteps 写 `rm -rf /` 的包能一路通过校验并自动发布。
    for where, command in manifest_commands(manifest):
        for pattern, label in DANGEROUS_PATTERNS:
            if pattern.search(command):
                report.error(f"{where} 含危险命令：{label}（{command[:80]}）")

    return report.ok


def manifest_commands(manifest):
    """清单里所有会被宿主执行的命令，返回 (字段名, 命令文本)。"""
    out = []
    for i, step in enumerate(manifest.get("installSteps") or []):
        if isinstance(step, str):
            out.append((f"installSteps[{i}]", step))
    for i, step in enumerate(manifest.get("uninstallSteps") or []):
        if isinstance(step, str):
            out.append((f"uninstallSteps[{i}]", step))
    for key in ("launchCommand", "verifyCommand", "infoCommand"):
        v = manifest.get(key)
        if isinstance(v, str) and v.strip():
            out.append((key, v))
    for i, action in enumerate(manifest.get("actions") or []):
        if isinstance(action, dict) and isinstance(action.get("command"), str):
            out.append((f"actions[{i}].command", action["command"]))
    return out


def check_script_content(name, data, report):
    """扫描脚本里的危险模式。二进制不扫（前面已拒）。"""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        report.warn(f"{name} 不是 UTF-8 文本，跳过内容扫描")
        return
    for pattern, label in DANGEROUS_PATTERNS:
        if pattern.search(text):
            report.error(f"{name} 含危险命令：{label}")


def check_declared_domains(manifest, all_text, report):
    """installSteps 里出现的域名若未在 manifest 声明，给警告。"""
    declared = set()
    for key in ("homepage",):
        v = str(manifest.get(key, ""))
        for m in DOMAIN_RE.finditer(v):
            declared.add(m.group(1).lower())
    for ep in manifest.get("endpoints", []) or []:
        for m in DOMAIN_RE.finditer(str(ep.get("url", ""))):
            declared.add(m.group(1).lower())
    for value in (manifest.get("environment") or {}).values():
        for m in DOMAIN_RE.finditer(str(value)):
            declared.add(m.group(1).lower())

    used = set()
    for m in DOMAIN_RE.finditer(all_text):
        used.add(m.group(1).lower())
    undeclared = sorted(used - declared)
    if undeclared:
        report.warn(
            "installSteps 里出现了未在 manifest 声明的域名："
            + ", ".join(undeclared)
            + "（建议在 homepage/endpoints 里说明用途）"
        )


def is_binary(data):
    head = data[:8]
    return any(head.startswith(magic) for magic in BINARY_MAGIC)


def validate_dir(path):
    """校验源目录（PR 路径）。"""
    report = Report()
    manifest_path = os.path.join(path, "manifest.json")
    if not os.path.isfile(manifest_path):
        report.error("缺少 manifest.json")
        return report, None

    with open(manifest_path, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        manifest = load_manifest(raw)
    except json.JSONDecodeError as e:
        report.error(f"manifest.json 解析失败：{e}")
        return report, None

    if not isinstance(manifest, dict):
        report.error("manifest.json 顶层必须是对象")
        return report, None
    if not check_manifest(manifest, report):
        return report, manifest

    payload_dir = os.path.join(path, "payload")
    if not os.path.isdir(payload_dir):
        report.error("缺少 payload/ 目录")
        return report, manifest

    total = 0
    count = 0
    texts = []
    for root, dirs, files in os.walk(payload_dir):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, path).replace(os.sep, "/")
            if ".." in rel.split("/"):
                report.error(f"路径含 ..：{rel}")
                continue
            size = os.path.getsize(full)
            total += size
            count += 1
            if count > MAX_ENTRIES:
                report.error(f"文件数超过 {MAX_ENTRIES}")
                break
            with open(full, "rb") as f:
                data = f.read()
            if is_binary(data):
                report.error(f"{rel} 是二进制文件（插件包只允许脚本）")
                continue
            texts.append(data.decode("utf-8", errors="ignore"))
            check_script_content(rel, data, report)

    if total > MAX_UNCOMPRESSED_BYTES:
        report.error(
            f"解压后体积 {total / 1048576:.1f}MB 超过上限 {MAX_UNCOMPRESSED_BYTES // 1048576}MB"
        )
    if count == 0:
        report.error("payload/ 为空")

    check_declared_domains(manifest, raw + "\n".join(texts), report)
    return report, manifest


def validate_zip(zip_path):
    """校验 zip 包（Issue 路径与 CI 打包产物）。"""
    report = Report()
    if not os.path.isfile(zip_path):
        report.error(f"文件不存在：{zip_path}")
        return report, None

    size = os.path.getsize(zip_path)
    if size > MAX_ZIP_BYTES:
        report.error(f"包体积 {size / 1048576:.1f}MB 超过上限 {MAX_ZIP_BYTES // 1048576}MB")
        return report, None

    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        report.error(f"不是合法的 zip：{e}")
        return report, None

    with zf:
        names = zf.namelist()
        if len(names) > MAX_ENTRIES:
            report.error(f"zip 条目数 {len(names)} 超过上限 {MAX_ENTRIES}")
            return report, None

        total = 0
        for info in zf.infolist():
            name = info.filename
            # 结构性拒绝：绝对路径、.. 段、符号链接
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                report.error(f"zip 条目是绝对路径：{name}")
            if ".." in name.split("/"):
                report.error(f"zip 条目含 ..：{name}")
            # 外部属性高 16 位是 Unix mode；0o120000 = 符号链接
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                report.error(f"zip 条目是符号链接：{name}")
            total += info.file_size

        if total > MAX_UNCOMPRESSED_BYTES:
            report.error(
                f"解压后体积 {total / 1048576:.1f}MB 超过上限 {MAX_UNCOMPRESSED_BYTES // 1048576}MB"
            )

        if "manifest.json" not in names:
            report.error("包内缺少 manifest.json（应在根目录）")
            return report, None

        raw = zf.read("manifest.json").decode("utf-8", errors="replace")
        try:
            manifest = load_manifest(raw)
        except json.JSONDecodeError as e:
            report.error(f"manifest.json 解析失败：{e}")
            return report, None

        if not isinstance(manifest, dict):
            report.error("manifest.json 顶层必须是对象")
            return report, None
        if not check_manifest(manifest, report):
            return report, manifest

        texts = []
        has_payload = False
        for info in zf.infolist():
            if info.is_dir():
                continue
            if not info.filename.startswith("payload/"):
                continue
            has_payload = True
            data = zf.read(info)
            if is_binary(data):
                report.error(f"{info.filename} 是二进制文件（插件包只允许脚本）")
                continue
            texts.append(data.decode("utf-8", errors="ignore"))
            check_script_content(info.filename, data, report)

        if not has_payload:
            report.error("包内 payload/ 为空")

        check_declared_domains(manifest, raw + "\n".join(texts), report)

    return report, manifest


def main():
    ap = argparse.ArgumentParser(description="校验简云插件包")
    ap.add_argument("path", nargs="?", help="插件源目录")
    ap.add_argument("--zip", help="校验 zip 包")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    if not args.path and not args.zip:
        ap.error("需要 path 或 --zip")

    if args.zip:
        report, manifest = validate_zip(args.zip)
    else:
        report, manifest = validate_dir(args.path)

    if args.json:
        print(json.dumps({
            "ok": report.ok,
            "errors": report.errors,
            "warnings": report.warnings,
            "manifest": manifest,
        }, ensure_ascii=False, indent=2))
    else:
        for w in report.warnings:
            print(f"警告: {w}")
        for e in report.errors:
            print(f"错误: {e}")
        if report.ok:
            pid = (manifest or {}).get("id", "?")
            ver = (manifest or {}).get("version", "?")
            print(f"通过：{pid} v{ver}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
