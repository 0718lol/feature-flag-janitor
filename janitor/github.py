from __future__ import annotations

import base64
import json
from pathlib import PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from janitor.validation import MAX_CODE_FILES, MAX_FILE_BYTES, MAX_TOTAL_CODE_BYTES, InputError


TEXT_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs", ".java", ".json", ".yaml", ".yml", ".toml", ".md", ".mdx", ".txt", ".rst", ".css", ".html"}
SKIP_PARTS = {".git", "node_modules", "vendor", "dist", "build", "coverage"}
MAX_GITHUB_FILE_REQUESTS = 300
GITHUB_TIMEOUT_SECONDS = 15


def parse_github_url(value: str) -> tuple[str, str, str | None]:
    text = str(value or "").strip()
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != "github.com":
        raise InputError("只支持 github.com 的公共仓库地址")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise InputError("GitHub 地址应为 https://github.com/owner/repository")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        raise InputError("GitHub 地址缺少 owner 或 repository")
    branch = None
    if len(parts) >= 4 and parts[2] == "tree":
        branch = "/".join(parts[3:]).strip() or None
    elif len(parts) > 2:
        raise InputError("GitHub 地址只支持仓库地址或 tree/<branch> 地址")
    return owner, repo, branch


def _get_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "feature-flag-janitor"})
    try:
        with urlopen(request, timeout=GITHUB_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 404:
            raise InputError("GitHub 仓库不存在、不是公开仓库，或分支不存在") from None
        if error.code == 403:
            raise InputError("GitHub API 暂时限流，请稍后再试") from None
        raise InputError(f"GitHub API 返回 HTTP {error.code}") from None
    except (URLError, TimeoutError):
        raise InputError("GitHub 连接超时或暂时不可用") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise InputError("GitHub 返回了无法解析的数据") from None


def _read_blob(url: str) -> str:
    payload = _get_json(url)
    if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
        raise InputError("GitHub 文件内容格式不受支持")
    try:
        return base64.b64decode(payload["content"].replace("\n", "")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise InputError("GitHub 文件不是 UTF-8 文本") from None


def fetch_github_repo(repo_url: str) -> dict[str, Any]:
    owner, repo, requested_branch = parse_github_url(repo_url)
    api_root = f"https://api.github.com/repos/{owner}/{repo}"
    metadata = _get_json(api_root)
    branch = requested_branch or str(metadata.get("default_branch") or "main")
    tree = _get_json(f"{api_root}/git/trees/{branch}?recursive=1")
    files: list[dict[str, str]] = []
    warnings: list[str] = []
    total_bytes = 0
    skipped = 0
    if tree.get("truncated"):
        warnings.append("GitHub 文件树被 API 截断，扫描结果可能不完整。")
    entries = tree.get("tree") if isinstance(tree.get("tree"), list) else []
    for entry in entries:
        path_text = str(entry.get("path") or "")
        path = PurePosixPath(path_text)
        if entry.get("type") != "blob" or path.is_absolute() or any(part in SKIP_PARTS for part in path.parts) or path.suffix.lower() not in TEXT_EXTENSIONS:
            skipped += 1
            continue
        size = int(entry.get("size") or 0)
        if size > MAX_FILE_BYTES:
            skipped += 1
            warnings.append(f"已跳过超大文件：{path_text}")
            continue
        if len(files) >= MAX_CODE_FILES:
            warnings.append(f"文件数量达到 {MAX_CODE_FILES} 上限，其余文件未读取。")
            break
        if total_bytes + size > MAX_TOTAL_CODE_BYTES:
            warnings.append("文件总大小达到 6 MB 上限，其余文件未读取。")
            break
        if len(files) >= MAX_GITHUB_FILE_REQUESTS:
            warnings.append("GitHub API 文件读取次数达到上限，其余文件未读取。")
            break
        try:
            content = _read_blob(str(entry.get("url") or ""))
        except InputError as error:
            skipped += 1
            warnings.append(f"已跳过文件 {path_text}：{error}")
            continue
        files.append({"path": path_text, "content": content})
        total_bytes += len(content.encode("utf-8"))
    return {
        "code_files": files,
        "repo": {"owner": owner, "name": repo, "branch": branch, "url": f"https://github.com/{owner}/{repo}"},
        "source_meta": {"kind": "github", "repo": f"{owner}/{repo}", "branch": branch, "tree_entries": len(entries), "loaded_files": len(files), "skipped_files": skipped, "warnings": warnings},
    }
