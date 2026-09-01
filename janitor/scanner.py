from __future__ import annotations

import re
from pathlib import Path


def slug_aliases(flag_key: str) -> list[str]:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", flag_key) if part]
    normalized = re.sub(r"[^a-z0-9_]+", "_", flag_key.lower()).strip("_")
    camel = parts[0] + "".join(part.capitalize() for part in parts[1:]) if parts else flag_key
    env = re.sub(r"[^A-Za-z0-9]+", "_", flag_key).upper()
    aliases = {flag_key, normalized, env, camel, flag_key.replace("_", "-"), flag_key.replace("-", "_")}
    return [alias for alias in aliases if alias]


def line_matches_flag(line: str, flag: str) -> tuple[bool, str]:
    if line.strip().startswith(("//", "#", "/*", "*", "<!--")):
        return False, ""
    aliases = slug_aliases(flag)
    pattern = r"(?<![A-Za-z0-9_])(" + "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True)) + r")(?![A-Za-z0-9_])"
    if not re.search(pattern, line):
        return False, ""
    lowered = line.lower()
    if "!" in line or "== false" in lowered or "=== false" in lowered or re.search(r"\bnot\b", lowered):
        return True, "negative"
    if re.search(r"\belse\b", line):
        return True, "else"
    if re.search(r"\bif\b|\bwhen\b|\bcase\b", line):
        return True, "positive"
    return True, "reference"


def reference_type(path: str, line: str, polarity: str) -> str:
    lowered_path = path.lower()
    stripped = line.strip()
    if lowered_path.endswith((".md", ".mdx", ".txt", ".rst")):
        return "documentation"
    if "/test" in lowered_path or lowered_path.startswith("test") or ".test." in lowered_path or ".spec." in lowered_path:
        return "test_reference"
    if polarity in {"positive", "negative", "else"} or re.search(r"\?|\bif\b|\bwhen\b|\bcase\b", stripped, re.IGNORECASE):
        return "conditional_branch"
    if re.search(r"(?:get|read|is|has|enabled|check)[A-Za-z0-9_]*\s*\(", stripped, re.IGNORECASE):
        return "runtime_read"
    return "reference"


def scan_references(flags: list[dict], code_files: list[dict[str, str]]) -> dict[str, list[dict]]:
    hits: dict[str, list[dict]] = {flag["key"]: [] for flag in flags}
    file_index = {item["path"]: item["content"].splitlines() for item in code_files}
    for flag in flags:
        aliases = slug_aliases(flag["key"])
        for path, lines in file_index.items():
            for line_no, line in enumerate(lines, 1):
                found, polarity = line_matches_flag(line, flag["key"])
                if found and any(alias.lower() in line.lower() for alias in aliases):
                    hits[flag["key"]].append({
                        "file": path, "line": line_no, "snippet": line.strip(),
                        "polarity": polarity, "reference_type": reference_type(path, line, polarity),
                    })
    return hits


def lookup_test_candidates(code_files: list[dict[str, str]], ref_path: str) -> list[str]:
    ref = Path(ref_path)
    base = ref.stem
    candidates: list[str] = []
    for item in code_files:
        path = Path(item["path"])
        name = path.name.lower()
        if path == ref or path.suffix.lower() not in {".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs", ".java", ".json"}:
            continue
        if any(token in name for token in {base.lower(), "test", "spec"}):
            candidates.append(item["path"])
    return candidates[:5]
