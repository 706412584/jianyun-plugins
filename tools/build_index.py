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


def load_trusted():
    path = os.path.join(ROOT, ".github", "trusted-publishers.txt")
    if not os.path.isfile(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        return {
            line.strip().lower()
            for line in f
            if line.strip() and not line.strip().startswith("#")
        }


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

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo("manifest.json")
        info.external_attr = 0o644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        with open(manifest_path, "rb") as src:
            zf.writestr(info, src.read())

        payload = os.path.join(plugin_dir, "payload")
        for root, _dirs, files in os.walk(payload):
            for fname in files:
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, plugin_dir).replace(os.sep, "/")
                mode = _mode_for(rel, os.stat(full).st_mode)
                info = zipfile.ZipInfo(rel)
                info.external_attr = mode << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(full, "rb") as src:
                    zf.writestr(info, src.read())

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
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    index = load_index()
    by_id = {p["id"]: p for p in index["plugins"]}
    trusted = load_trusted()

    uploads = []
    changed = False

    ids = sorted(
        d for d in os.listdir(PLUGINS_DIR)
        if os.path.isdir(os.path.join(PLUGINS_DIR, d))
        and os.path.isfile(os.path.join(PLUGINS_DIR, d, "manifest.json"))
    )
    if args.only:
        wanted = set(args.only)
        ids = [i for i in ids if i in wanted]

    for pid in ids:
        plugin_dir = os.path.join(PLUGINS_DIR, pid)

        # 先跑校验：不合格的包绝不能进索引（索引是用户的安装来源）
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "validate.py"), plugin_dir],
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
        if prev and prev.get("version") == version and args.skip_existing:
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

        tag = f"{args.tag_prefix}/{pid}-{version}"
        publisher = str(manifest.get("publisher", "")).strip()
        trust = "verified" if publisher.lower() in trusted else "community"

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
        if manifest.get("minAppVersionCode"):
            entry["minAppVersionCode"] = int(manifest["minAppVersionCode"])

        by_id[pid] = entry
        changed = True
        uploads.append(f"{pkg_path}\t{tag}")
        print(f"打包 {pid} v{version} → {os.path.basename(pkg_path)} ({entry['sizeBytes']} B, {trust})")

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
