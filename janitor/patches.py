from __future__ import annotations

import difflib
from typing import Any


def build_cleanup_patch(result: dict[str, Any], finding_keys: list[str] | None = None) -> str:
    selected = set(finding_keys or [item.get("finding_key") for item in result.get("cleanup_list", [])])
    rows = {row["key"]: row for row in result.get("flags", []) if row.get("key") in selected}
    output = ["# Feature Flag Janitor cleanup draft", "# Review-only proposal. No files were changed.", ""]
    for key in selected:
        row = rows.get(key)
        if not row:
            continue
        output.extend([f"# {key} | priority={row['cleanup_priority']} | risk={row['risk']} | confidence={row['confidence']}", f"# Reasons: {'; '.join(row['reasons'])}"])
        if row["risk"] == "high":
            output.append("# REVIEW REQUIRED: high-risk flag; confirm rollback and external consumers first.")
        by_file: dict[str, list[dict[str, Any]]] = {}
        for hit in row.get("references", []):
            if hit.get("reference_type") == "conditional_branch":
                by_file.setdefault(hit["file"], []).append(hit)
        if not by_file:
            output.append(f"# No conditional branch hunk generated for {key}; remove config only after confirmation.")
        for path, hits in by_file.items():
            output.append(f"# Proposed branch review: {path} lines {', '.join(str(hit['line']) for hit in hits)}")
            for hit in hits:
                old = hit["snippet"]
                output.extend(difflib.unified_diff([old + "\n"], [f"# REVIEW: replace fixed branch for {key}\n"], fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))
        output.append("")
    return "\n".join(output).rstrip() + "\n"
