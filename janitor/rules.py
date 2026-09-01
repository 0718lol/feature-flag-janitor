from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Callable


ARCHIVED_STATUSES = {"archived", "removed", "retired"}


def group_experiments(experiments: list[dict[str, Any]], normalize_key: Callable[[Any], str]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in experiments:
        key = normalize_key(item.get("flag") or item.get("name") or item.get("key"))
        if key:
            grouped[key].append(item)
    return grouped


def flag_lifecycle(flag: dict[str, Any], experiments: list[dict[str, Any]], today: datetime, parse_date: Callable[[Any], datetime | None]) -> dict[str, Any]:
    expires_at = parse_date(flag.get("expires_at"))
    created_at = parse_date(flag.get("created_at"))
    status = str(flag.get("status") or "").lower()
    completed_experiment = any(
        (ended := parse_date(exp.get("ended_at"))) is not None and ended < today
        for exp in experiments
    )
    stale_age = (today - created_at).days if created_at else None
    return {
        "expires_at": expires_at,
        "created_at": created_at,
        "status": status or "unknown",
        "expired": bool(expires_at and expires_at < today),
        "archived": status in ARCHIVED_STATUSES,
        "completed_experiment": completed_experiment,
        "stale_age": stale_age,
    }


def dead_branch_state(hits: list[dict[str, Any]], rollout: int) -> str | None:
    branch_hits = [hit for hit in hits if hit["reference_type"] == "conditional_branch"]
    if not branch_hits:
        return None
    has_positive = any(hit["polarity"] == "positive" for hit in branch_hits)
    if rollout >= 100:
        return "dead-else" if has_positive else "dead-then"
    if rollout <= 0:
        return "dead-then" if has_positive else "dead-else"
    return None


def reasons_for(flag: dict[str, Any], lifecycle: dict[str, Any], refs: int) -> list[str]:
    reasons: list[str] = []
    if lifecycle["expired"]:
        expires_at = lifecycle["expires_at"]
        reasons.append(f"已过期 {expires_at.date().isoformat() if expires_at else ''}")
    if lifecycle["archived"]:
        reasons.append("状态已归档")
    rollout = int(flag.get("rollout") or 0)
    if rollout >= 100:
        reasons.append("已 100% 发布")
    if rollout <= 0:
        reasons.append("已完全关闭")
    if not refs:
        reasons.append("代码中未再检出引用")
    if lifecycle["completed_experiment"]:
        reasons.append("实验已结束")
    return reasons or ["仍处在观察窗口"]
