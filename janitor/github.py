from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.parse import quote
from urllib.request import Request, urlopen

from janitor.validation import MAX_CODE_FILES, MAX_FILE_BYTES, MAX_TOTAL_CODE_BYTES, InputError
from janitor.archive import read_zip_files


GITHUB_TIMEOUT_SECONDS = 15


def parse_github_url(value: str) -> tuple[str, str, str | None]:
    text = str(value or "").strip()
    parsed = urlsplit(text)
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        raise InputError("只支持 https://github.com 的公共仓库地址")
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


def _get_bytes(url: str) -> bytes:
    request = Request(url, headers={"Accept": "application/zip", "User-Agent": "feature-flag-janitor"})
    try:
        with urlopen(request, timeout=GITHUB_TIMEOUT_SECONDS) as response:
            return response.read()
    except HTTPError as error:
        if error.code == 404:
            raise InputError("GitHub 仓库或分支不存在") from None
        if error.code == 403:
            raise InputError("GitHub 下载暂时限流，请稍后再试") from None
        raise InputError(f"GitHub 下载返回 HTTP {error.code}") from None
    except (URLError, TimeoutError):
        raise InputError("GitHub 下载超时或暂时不可用") from None


def fetch_github_repo(repo_url: str) -> dict[str, Any]:
    owner, repo, requested_branch = parse_github_url(repo_url)
    api_root = f"https://api.github.com/repos/{owner}/{repo}"
    metadata = _get_json(api_root)
    branch = requested_branch or str(metadata.get("default_branch") or "main")
    archive_url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{quote(branch, safe='')}"
    archive_files = read_zip_files(_get_bytes(archive_url))
    files: list[dict[str, str]] = []
    warnings: list[str] = []
    for item in archive_files:
        parts = PurePosixPath(item["path"]).parts
        path_text = "/".join(parts[1:]) if len(parts) > 1 else parts[0]
        if not path_text:
            continue
        files.append({"path": path_text, "content": item["content"]})
    if len(files) >= MAX_CODE_FILES:
        warnings.append(f"文件数量达到 {MAX_CODE_FILES} 上限，其余文件未读取。")
    entries_count = len(archive_files)
    skipped = max(0, entries_count - len(files))
    return {
        "code_files": files,
        "repo": {"owner": owner, "name": repo, "branch": branch, "url": f"https://github.com/{owner}/{repo}"},
        "source_meta": {"kind": "github", "repo": f"{owner}/{repo}", "branch": branch, "tree_entries": entries_count, "loaded_files": len(files), "skipped_files": skipped, "warnings": warnings, "transport": "codeload_zip"},
    }
