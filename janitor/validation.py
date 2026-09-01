from __future__ import annotations

from collections.abc import Callable
from typing import Any


MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_CODE_FILES = 2000
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_CODE_BYTES = 6 * 1024 * 1024


class InputError(ValueError):
    """An input problem that can be shown directly to the user."""


def parse_rollout(value: Any, field: str) -> int:
    if value in (None, ""):
        return 0
    try:
        if isinstance(value, bool):
            raise ValueError
        rollout = int(value)
    except (TypeError, ValueError):
        raise InputError(f"{field} 必须是 0 到 100 的整数") from None
    if not 0 <= rollout <= 100:
        raise InputError(f"{field} 必须是 0 到 100 的整数")
    return rollout


def validate_payload(
    payload: Any,
    *,
    parse_manifest: Callable[[Any], list[dict[str, Any]]],
    parse_code_files: Callable[[Any], list[dict[str, str]]],
    parse_records: Callable[[Any], list[dict[str, Any]]],
    normalize_key: Callable[[Any], str],
    parse_date: Callable[[Any], Any],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise InputError("请求内容必须是 JSON 对象")

    flags = parse_manifest(payload.get("manifest_text") or payload.get("manifest") or payload.get("flags"))
    if len(flags) > MAX_CODE_FILES:
        raise InputError(f"flag 数量不能超过 {MAX_CODE_FILES}")
    seen: set[str] = set()
    for index, flag in enumerate(flags):
        key = str(flag.get("key") or "").strip()
        if not key:
            raise InputError(f"manifest.flags[{index}].key 不能为空")
        normalized = normalize_key(key)
        if normalized in seen:
            raise InputError(f"发现重复 flag：{key}")
        seen.add(normalized)
        parse_rollout(flag.get("rollout"), f"manifest.flags[{index}].rollout")
        for field in ("expires_at", "created_at"):
            value = flag.get(field)
            if value not in (None, "") and parse_date(value) is None:
                raise InputError(f"manifest.flags[{index}].{field} 必须是 YYYY-MM-DD 或 ISO 8601 日期")

    code_files = parse_code_files(payload.get("code_files") or payload.get("files") or payload.get("code"))
    if len(code_files) > MAX_CODE_FILES:
        raise InputError(f"代码文件数量不能超过 {MAX_CODE_FILES}")
    total_bytes = 0
    for code_file in code_files:
        size = len(code_file["content"].encode("utf-8"))
        if size > MAX_FILE_BYTES:
            raise InputError(f"代码文件 {code_file['path']} 超过 1 MB 限制")
        total_bytes += size
    if total_bytes > MAX_TOTAL_CODE_BYTES:
        raise InputError("代码文件总大小不能超过 6 MB")

    for section, value in (
        ("experiments", payload.get("experiments_text") or payload.get("experiments")),
        ("releases", payload.get("releases_text") or payload.get("releases")),
    ):
        for index, record in enumerate(parse_records(value)):
            for field in ("started_at", "ended_at", "date"):
                date_value = record.get(field)
                if field in record and date_value not in (None, "") and parse_date(date_value) is None:
                    raise InputError(f"{section}[{index}].{field} 必须是 YYYY-MM-DD 或 ISO 8601 日期")
    today = payload.get("today")
    if today not in (None, "") and parse_date(today) is None:
        raise InputError("today 必须是 YYYY-MM-DD 或 ISO 8601 日期")
    return payload
