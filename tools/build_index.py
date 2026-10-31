#!/usr/bin/env python3
"""
把 plugins/<id>/ 打成 .plugin 包，生成 index.json。

用法：
    python tools/build_index.py --base-url https://github.com/706412584/jianyun-plugins/releases/download
    python tools/build_index.py --out dist --base-url ... --tag-prefix plugin

产物：
    dist/<id>-<version>.plugin     待上传的包
    dist/index.json                市场清单
    dist/upload-list.txt           给 CI 用的「包路径 tag」清单

设计取舍：
- **包名带版本**（`foo-1.2.0.plugin`），tag 也带版本（`plugin/foo-1.2.0`）。
  这样同一插件的新版本是不同资产，旧版本链接不会失效，用户降级也有据可查。
- **sha256 必须由脚本算**，不接受手填——手填的哈希迟早会与文件不一致，
  而校验失败的表现是「所有用户都装不上」。
- **版本比较放在这里**：与 index.json 里已有的版本比，不允许回退或重复。
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.join(ROOT, "plugins")
INDEX_PATH = os.path.join(ROOT, "index.json")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def plugin_updated_at(plugin_dir):
    """插件目录最后一次提交的时间（ISO 8601 UTC），查不到返回空串。

    为什么用 git 提交时间而不是「打包时的当前时间」：后者每次重跑 CI 都会变，
    index.json 会被无谓地改一遍（并且每次提交都触发下游的镜像同步）。
    提交时间是稳定的事实，重跑得到同一个值。

    CI 里需要 fetch-depth: 0；浅克隆时 git log 只回溯一个提交，
    会让所有插件的时间都显示成最新那次，所以查不到更早历史时宁可不报时间。
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", os.path.relpath(plugin_dir, ROOT)],
            capture_output=True, text=True, cwd=ROOT, timeout=20,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def parse_version(v):
    """把 1.2.3 拆成 (1,2,3)。非数字段按 0 处理并保留原文用于比较。"""
    parts = []
    for seg in re.split(r"[.\-+]", str(v)):
        m = re.match(r"^(\d+)", seg)
        parts.append(int(m.group(1)) if m else 0)
    return tuple(parts)


def version_gt(a, b):
    pa, pb = parse_version(a), parse_version(b)
    n = max(len(pa), len(pb))
    pa += (0,) * (n - len(pa))
    pb += (0,) * (n - len(pb))
    return pa > pb


def load_index():
    if not os.path.isfile(INDEX_PATH):
        return {"schemaVersion": 1, "plugins": []}
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("schemaVersion", 1)
    data.setdefault("plugins", [])
    return data


def _read_list(path):
    if not os.path.isfile(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        return {
            line.strip().lower()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        }


def load_trusted():
    """受信任的发布者（GitHub 用户名）。

    **这只是一半条件**：publisher 是投稿者在 manifest 里自己填的字符串，
    任何人写 `"publisher": "维护者用户名"` 都能命中这个列表。
    真正决定「已验证」的还有 [load_vouched_plugins] ——维护者逐个插件签过字的名单。
    """
    return _read_list(os.path.join(ROOT, ".github", "trusted-publishers.txt"))


def load_vouched_plugins():
    """维护者人工确认过的插件 id（一行一个）。

    为什么需要：自动校验只能挡住已知的危险模式，挡不住「把容器里的凭据发到
    某个看起来正常的域名」。而 publisher 字段无法证明身份，所以「已验证」必须
    由人来签发，签发的结果就是这份名单。
    """
    return _read_list(os.path.join(ROOT, ".github", "verified-plugins.txt"))


# 需要可执行位的扩展名。**不能依赖 os.stat**：Windows 上的文件系统没有 Unix
# 执行位，检出后 stat 给的是 0666，打包进 zip 后脚本在容器里不可执行，
# 插件启动直接失败（而错误只显示 "Permission denied"，很难联想到打包机）。
EXECUTABLE_EXTS = (".sh", ".bash", ".py", ".pl", ".rb")

# 需要保留原样权限的扩展名（其余一律 0644，避免把打包机的奇怪权限带出去）
def _mode_for(rel_path, src_mode):
    if rel_path.lower().endswith(EXECUTABLE_EXTS):
        return 0o755
    # Unix 上有真实执行位时尊重它（CI 跑在 Linux，检出时会按 .gitattributes 保留）
    if src_mode & 0o111:
        return src_mode & 0o777
    return 0o644


def pack(plugin_dir, out_dir):
    """把一个插件源目录打成 zip，返回 (路径, 清单)。"""
    manifest_path = os.path.join(plugin_dir, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    pid = manifest["id"]
    version = manifest["version"]
    name = f"{pid}-{version}.plugin"
    out_path = os.path.join(out_dir, name)

    # **可复现打包**：固定时间戳与条目顺序，同一台机器上每次打出的字节完全一致。
    # 否则 CI 重跑会生成不同 sha256，而 index.json 里记的是上一次的——
    # 用户下载后校验必然失败（真机踩到：Release 资产与索引哈希对不上）。
    #
    # 注意：跨平台仍可能不同——zlib 版本差异会让 deflate 输出不一样
    # （实测同一份源码：Windows/Python 3.13 打出 1616 B，Linux/Python 3.12 是 1611 B）。
    # 所以**以 CI 打出的包为准**：本地跑出来的哈希不要手填进 index，
    # build-index.yml 会拿 Release 资产的 digest 与 index 比对，不一致就重建。
    FIXED_TIME = (2020, 1, 1, 0, 0, 0)

    def add(zf, rel, data, mode):
        info = zipfile.ZipInfo(rel, date_time=FIXED_TIME)
        info.external_attr = mode << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3  # Unix，让 external_attr 的 mode 生效
        zf.writestr(info, data)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        with open(manifest_path, "rb") as src:
            add(zf, "manifest.json", src.read(), 0o644)

        payload = os.path.join(plugin_dir, "payload")
        collected = []
        for root, _dirs, files in os.walk(payload):
            for fname in files:
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, plugin_dir).replace(os.sep, "/")
                collected.append((rel, full))
        for rel, full in sorted(collected):
            mode = _mode_for(rel, os.stat(full).st_mode)
            with open(full, "rb") as src:
                add(zf, rel, src.read(), mode)

    return out_path, manifest


def main():
    ap = argparse.ArgumentParser(description="打包插件并生成 index.json")
    ap.add_argument(
        "--base-url",
        default="https://github.com/706412584/jianyun-plugins/releases/download",
        help="Release 资产的基础 URL",
    )
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"), help="产物目录")
    ap.add_argument("--tag-prefix", default="plugin", help="Release tag 前缀")
    ap.add_argument(
        "--only", action="append", help="只处理指定 id（可多次）。默认处理全部"
    )
    ap.add_argument(
        "--skip-existing",
        action="store_true",
        help="index 里已有同 id 同版本的条目时跳过（幂等重跑用）",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help=(
            "忽略 index 里已有的版本，强制重新打包并写索引。"
            "用于「索引有条目但 Release 资产缺失」的修复（首次发布时 index.json "
            "若已随仓库提交，正常流程会误判为已发布）"
        ),
    )
    ap.add_argument(
        "--trust",
        choices=["auto", "community", "verified"],
        default="auto",
        help=(
            "信任级别。auto = 按 manifest 的 publisher 与 trusted-publishers.txt 比对；"
            "community = 一律标为社区投稿。"
            "**Issue 投稿必须用 community**：publisher 是投稿者自己填的字符串，"
            "不是身份证明——任何人写 publisher=维护者用户名 都能拿到「已验证」徽章。"
            "verified 只应来自人工合并的 PR（维护者看过内容）。"
        ),
    )
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    index = load_index()
    by_id = {p["id"]: p for p in index["plugins"]}
    trusted = load_trusted()
    vouched = load_vouched_plugins()

    uploads = []
    changed = False

    # plugins/ 目录可能整个不存在：最后一个插件被下架后，GitHub 不跟踪空目录，
    # 该目录会从仓库消失。此时应视为「没有任何插件」，而不是让 CI 崩掉。
    ids = sorted(
        d for d in (os.listdir(PLUGINS_DIR) if os.path.isdir(PLUGINS_DIR) else [])
        if os.path.isdir(os.path.join(PLUGINS_DIR, d))
        and os.path.isfile(os.path.join(PLUGINS_DIR, d, "manifest.json"))
    )
    if args.only:
        wanted = set(args.only)
        ids = [i for i in ids if i in wanted]

    for pid in ids:
        plugin_dir = os.path.join(PLUGINS_DIR, pid)

        # 先跑校验：不合格的包绝不能进索引（索引是用户的安装来源）。
        # --allow-published-version：重新生成索引时会遍历**已发布**的插件，它们的
        # 版本当然不会大于 index.json 里的自己；版本单调性由本脚本下面的
        # version_gt 判断负责，不该在这里把已发布的包判成违规。
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "validate.py"), plugin_dir,
             "--allow-published-version"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            print(f"跳过 {pid}：校验未通过\n{proc.stdout}{proc.stderr}", file=sys.stderr)
            continue

        try:
            pkg_path, manifest = pack(plugin_dir, args.out)
        except Exception as e:
            print(f"跳过 {pid}：打包失败 {e}", file=sys.stderr)
            continue

        if manifest["id"] != pid:
            print(
                f"跳过 {pid}：目录名与 manifest.id（{manifest['id']}）不一致",
                file=sys.stderr,
            )
            continue

        version = manifest["version"]
        prev = by_id.get(pid)
        if not args.force:
            if prev and prev.get("version") == version and args.skip_existing:
                print(f"跳过 {pid} v{version}：已存在")
                os.remove(pkg_path)
                continue
            if prev and prev.get("version") == version:
                # 同版本不重新打包（省时间，也避免无谓改写 sha256），但要把
                # **索引新增的字段补进已有条目**——否则加了新字段后，
                # 已发布的插件永远拿不到它（首次遇到 updatedAt 就是这么漏的）。
                before = dict(prev)
                updated = plugin_updated_at(plugin_dir)
                if updated:
                    prev["updatedAt"] = updated
                if prev != before:
                    changed = True
                    print(f"补全 {pid} v{version} 的索引字段")
                else:
                    print(f"跳过 {pid} v{version}：已存在")
                os.remove(pkg_path)
                continue
            if prev and not version_gt(version, prev.get("version", "0")):
                print(
                    f"跳过 {pid}：版本 {version} 不大于已发布的 {prev.get('version')}",
                    file=sys.stderr,
                )
                os.remove(pkg_path)
                continue
        elif prev and prev.get("version") == version:
            print(f"强制重建 {pid} v{version}")

        tag = f"{args.tag_prefix}/{pid}-{version}"
        publisher = str(manifest.get("publisher", "")).strip()
        if args.trust == "auto":
            # publisher 只是清单里的一个字符串，**不是身份证明**：任何人写
            # `"publisher": "维护者用户名"` 都能命中 trusted-publishers.txt。
            # 所以「已验证」要求两个条件同时成立：
            #   1. publisher 在受信发布者列表里；
            #   2. 这个插件 id 在维护者人工签字的 verified-plugins.txt 里。
            # 条件 2 只能靠人改文件来满足，投稿者改不到。
            trust = (
                "verified"
                if publisher.lower() in trusted and pid.lower() in vouched
                else "community"
            )
        else:
            trust = args.trust

        entry = {
            "id": pid,
            "name": manifest.get("name", pid),
            "description": manifest.get("description", ""),
            "version": version,
            "publisher": publisher,
            "category": manifest.get("category", ""),
            "homepage": manifest.get("homepage", ""),
            "architectures": manifest.get("architectures", []),
            "permissions": manifest.get("permissions", []),
            "downloadUrl": f"{args.base_url}/{tag}/{os.path.basename(pkg_path)}",
            "sha256": sha256_of(pkg_path),
            "sizeBytes": os.path.getsize(pkg_path),
            "trust": trust,
        }
        # 更新时间：详情页用它回答「这插件还在维护吗」。查不到就不写字段，
        # 避免用假时间误导。
        updated = plugin_updated_at(plugin_dir)
        if updated:
            entry["updatedAt"] = updated
        if manifest.get("minAppVersionCode"):
            entry["minAppVersionCode"] = int(manifest["minAppVersionCode"])

        by_id[pid] = entry
        changed = True
        uploads.append(f"{pkg_path}\t{tag}")
        print(f"打包 {pid} v{version} → {os.path.basename(pkg_path)} ({entry['sizeBytes']} B, {trust})")

    # 对账删除：仓库里已经没有 plugins/<id>/ 目录的条目要从索引里剔除。
    # 少了这一步，下架（删目录）只会让 CI 报「没有需要更新的插件」，
    # index.json 里的旧条目永远留着，市场继续显示已下架的插件。
    # 只在全量运行时做：--only 是局部修复，不该顺带删别的条目。
    if not args.only:
        present = set(ids)
        gone = [pid for pid in by_id if pid not in present]
        for pid in gone:
            del by_id[pid]
            changed = True
            print(f"移除 {pid}：仓库里已没有该插件目录（下架）")

    if not changed:
        print("没有需要更新的插件")
        return 0

    index["plugins"] = sorted(by_id.values(), key=lambda p: p["id"])
    index["schemaVersion"] = 1

    out_index = os.path.join(args.out, "index.json")
    with open(out_index, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
        f.write("\n")

    upload_list = os.path.join(args.out, "upload-list.txt")
    with open(upload_list, "w", encoding="utf-8") as f:
        f.write("\n".join(uploads))
        if uploads:
            f.write("\n")

    # 同时更新仓库根目录的 index.json（App 从 raw/CDN 读这个）
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"已生成 {out_index}，共 {len(index['plugins'])} 个插件，{len(uploads)} 个待上传包")
    return 0


if __name__ == "__main__":
    sys.exit(main())
