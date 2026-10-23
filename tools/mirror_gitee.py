#!/usr/bin/env python3
"""把 GitHub Release 附件同步到 Gitee 镜像仓库。

由 .github/workflows/mirror-gitee.yml 调用（代码同步那步在 workflow 里用 git push 做，
那里没有可提取的逻辑）。

为什么单独成文件而不是写在 workflow 的 run 里：workflow 里嵌 heredoc 时缩进会
破坏 `<<'EOF'` 的终止符，而 YAML 块标量又强制缩进——两者天然冲突。逻辑放这里，
workflow 只留一行调用。

用法：
    python3 tools/mirror_gitee.py --owner <gitee-owner> --repo <repo>

需要环境变量：
    GH_TOKEN     GitHub 令牌（读附件）
    GITEE_TOKEN  Gitee 私人令牌（写 release）
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

GITEE_API = "https://gitee.com/api/v5"


def _req(url: str, data: bytes | None = None, method: str | None = None,
         headers: dict[str, str] | None = None) -> bytes:
    """发一个请求，返回响应正文；HTTP 错误抛异常并带上响应正文（便于定位）。"""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method or 'GET'} {url} 失败：HTTP {e.code} {e.read()[:400]!r}") from e


def _gitee_releases(owner: str, repo: str, token: str) -> list[dict]:
    url = f"{GITEE_API}/repos/{owner}/{repo}/releases?" + urllib.parse.urlencode(
        {"access_token": token, "per_page": 100}
    )
    try:
        return json.loads(_req(url))
    except RuntimeError as e:
        # 仓库还没建、或令牌不对：给出可执行的提示而不是裸异常
        raise RuntimeError(f"读取 Gitee release 列表失败（仓库是否存在？令牌权限是否含 projects？）：{e}") from e


def _github_tags() -> list[str]:
    out = subprocess.run(
        ["gh", "release", "list", "--limit", "200", "--json", "tagName", "--jq", ".[].tagName"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [t for t in out.splitlines() if t.strip()]


def _github_assets(tag: str) -> list[tuple[str, str]]:
    """该 tag 的附件 (名称, API URL)。"""
    out = subprocess.run(
        ["gh", "release", "view", tag, "--json", "assets", "--jq", ".assets[] | [.name, .url] | @tsv"],
        capture_output=True, text=True, check=True,
    ).stdout
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        name, _, url = line.partition("\t")
        if name and url:
            rows.append((name, url))
    return rows


def _ensure_release(owner: str, repo: str, token: str, tag: str, existing: list[dict]) -> str:
    """返回该 tag 在 Gitee 上的 release id，不存在就建一个。"""
    for r in existing:
        if r.get("tag_name") == tag:
            return str(r["id"])
    body = urllib.parse.urlencode(
        {"access_token": token, "tag_name": tag, "name": tag, "body": f"自动同步自 GitHub 仓库 {tag}"}
    ).encode()
    created = json.loads(_req(f"{GITEE_API}/repos/{owner}/{repo}/releases", data=body, method="POST"))
    return str(created["id"])


def _existing_asset_names(owner: str, repo: str, token: str, rid: str) -> set[str]:
    url = f"{GITEE_API}/repos/{owner}/{repo}/releases/{rid}?" + urllib.parse.urlencode(
        {"access_token": token}
    )
    detail = json.loads(_req(url))
    return {a.get("name") for a in detail.get("assets", [])}


def _download_github_asset(url: str, dest: str, token: str) -> None:
    # asset 的 API URL 要带 Accept: application/octet-stream 才返回二进制正文，
    # 否则会拿到 JSON 元数据——存下来是个坏包，且要到用户安装时才暴露
    data = _req(url, headers={"Authorization": f"token {token}", "Accept": "application/octet-stream"})
    with open(dest, "wb") as f:
        f.write(data)


def _upload_to_gitee(owner: str, repo: str, token: str, rid: str, path: str, name: str) -> None:
    """multipart 上传单个附件。

    手写 multipart 而不是依赖 requests：CI 里少一个依赖就少一个版本漂移点。
    """
    boundary = "----jianyunmirror7f3a9c"
    with open(path, "rb") as f:
        content = f.read()
    parts = []
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"access_token\"\r\n\r\n{token}\r\n".encode()
    )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n".encode()
    )
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    _req(
        f"{GITEE_API}/repos/{owner}/{repo}/releases/{rid}/attach_files",
        data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", required=True, help="Gitee 用户名/组织名")
    ap.add_argument("--repo", required=True, help="Gitee 仓库名（与 GitHub 同名）")
    args = ap.parse_args()

    gitee_token = os.environ.get("GITEE_TOKEN", "")
    gh_token = os.environ.get("GH_TOKEN", "")
    if not gitee_token:
        print("缺少 GITEE_TOKEN", file=sys.stderr)
        return 1

    tags = _github_tags()
    if not tags:
        print("GitHub 侧还没有 release，跳过附件同步")
        return 0

    existing = _gitee_releases(args.owner, args.repo, gitee_token)
    uploaded = skipped = 0

    for tag in tags:
        assets = _github_assets(tag)
        if not assets:
            continue
        rid = _ensure_release(args.owner, args.repo, gitee_token, tag, existing)
        print(f"── {tag} (release id={rid})")
        present = _existing_asset_names(args.owner, args.repo, gitee_token, rid)

        for name, url in assets:
            if name in present:
                # Gitee 的 attach_files 不做同名覆盖，重传会报错；跳过即可保持幂等
                print(f"   {name} 已存在，跳过")
                skipped += 1
                continue
            tmp = os.path.join("/tmp", name)
            _download_github_asset(url, tmp, gh_token)
            _upload_to_gitee(args.owner, args.repo, gitee_token, rid, tmp, name)
            os.remove(tmp)
            print(f"   已上传 {name}")
            uploaded += 1

    print(f"\n完成：上传 {uploaded} 个，跳过 {skipped} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
