from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class AIUnavailable(RuntimeError):
    """The optional AI provider cannot be reached or is not configured."""


def _provider_config() -> tuple[str, str, str]:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip() or "deepseek-chat"
    if not api_key:
        raise AIUnavailable("未配置 DEEPSEEK_API_KEY")
    return api_key, base_url, model


def _safe_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Keep source code and raw uploaded files out of the external AI request."""
    return {
        "scan_id": result.get("scan_id"),
        "input_check": result.get("input_check", {}),
        "summary": result.get("summary", {}),
        "flags": [
            {
                "key": row.get("key"),
                "owner": row.get("owner"),
                "kind": row.get("kind"),
                "status": row.get("status"),
                "rollout": row.get("rollout"),
                "expires_at": row.get("expires_at"),
                "risk": row.get("risk"),
                "score": row.get("score"),
                "cleanup_priority": row.get("cleanup_priority"),
                "confidence": row.get("confidence"),
                "reasons": row.get("reasons", []),
                "reference_count": row.get("reference_count", 0),
                "test_candidates": row.get("test_candidates", []),
            }
            for row in result.get("flags", [])
        ],
        "cleanup_list": result.get("cleanup_list", []),
        "reminders": result.get("reminders", []),
    }


def _extract_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AIUnavailable("DeepSeek 返回中没有 choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise AIUnavailable("DeepSeek 返回中没有可读内容")
    return content.strip()


def summarize_scan(result: dict[str, Any]) -> dict[str, Any]:
    api_key, base_url, model = _provider_config()
    context = json.dumps(_safe_payload(result), ensure_ascii=False)
    system = (
        "你是 Feature Flag Janitor 的资深发布工程师。只基于提供的扫描证据做判断，不臆测不存在的事实。"
        "请用简体中文返回严格 JSON，不要 Markdown，不要思考过程。字段必须是："
        "headline(string), summary(string), priorities(array of objects with key, action, reason), "
        "next_steps(array of strings), caveats(array of strings)。"
    )
    body = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 1200,
        "thinking": {"type": "disabled"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"请解释这次 flag 扫描结果：\n{context}"},
        ],
    }
    request = Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "feature-flag-janitor"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = json.load(response)
    except HTTPError as error:
        if error.code in {401, 403}:
            raise AIUnavailable("DeepSeek 密钥无效或无权访问该模型") from None
        if error.code == 429:
            raise AIUnavailable("DeepSeek 请求过于频繁，请稍后再试") from None
        raise AIUnavailable(f"DeepSeek 返回 HTTP {error.code}") from None
    except (URLError, TimeoutError):
        raise AIUnavailable("DeepSeek 连接超时或暂时不可用") from None
    content = _extract_content(raw)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {"headline": "AI 返回了非结构化解读", "summary": content, "priorities": [], "next_steps": [], "caveats": ["本次返回未符合结构化 JSON，未用于自动决策。"]}
    if not isinstance(parsed, dict):
        raise AIUnavailable("DeepSeek 返回的解读格式不正确")
    return parsed
