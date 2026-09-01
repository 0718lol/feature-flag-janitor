from __future__ import annotations

from typing import Any


def infer_risk(flag: dict[str, Any]) -> str:
    text = " ".join(str(flag.get(field, "")) for field in ("key", "description", "owner", "kind", "replacement")).lower()
    if any(word in text for word in ("payment", "billing", "risk", "auth", "permission", "export", "security")):
        return "high"
    if flag.get("kind") == "experiment":
        return "medium"
    return "low"


def score_finding(lifecycle: dict[str, Any], refs: int, rollout: int) -> int:
    score = 0
    if lifecycle["expired"]:
        score += 40
    if lifecycle["archived"]:
        score += 20
    if refs and rollout in {0, 100}:
        score += 20
    if not refs:
        score += 15
    if lifecycle["completed_experiment"]:
        score += 10
    if lifecycle["stale_age"] and lifecycle["stale_age"] > 90:
        score += 10
    return score


def priority_for(lifecycle: dict[str, Any], rollout: int, branch_state: str | None, refs: int) -> str:
    if lifecycle["expired"] and rollout == 100:
        return "P0"
    if lifecycle["expired"] or branch_state:
        return "P1"
    if not refs:
        return "P2"
    return "P3"


def action_for(lifecycle: dict[str, Any], refs: int) -> str:
    if lifecycle["expired"] and refs:
        return "delete-config-and-code"
    if not refs:
        return "remove-config"
    return "delete-dead-branch"


def confidence_for(lifecycle: dict[str, Any], branch_state: str | None, rollout: int, refs: int) -> str:
    if (lifecycle["expired"] and (rollout in {0, 100} or refs == 0)) or lifecycle["archived"]:
        return "high"
    if lifecycle["expired"] or branch_state or lifecycle["completed_experiment"]:
        return "medium"
    return "low"
