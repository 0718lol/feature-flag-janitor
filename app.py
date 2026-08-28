from __future__ import annotations

import json
import os
import re
import tomllib
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".toml": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}

SAMPLE_PAYLOAD = {
    "manifest_text": json.dumps(
        {
            "flags": [
                {
                    "key": "checkout_banner",
                    "owner": "growth",
                    "kind": "release",
                    "status": "on",
                    "rollout": 100,
                    "expires_at": "2026-03-01",
                    "created_at": "2026-01-08",
                    "replacement": "new_checkout_banner",
                    "description": "活动页横幅新版本",
                },
                {
                    "key": "legacy_search",
                    "owner": "search",
                    "kind": "experiment",
                    "status": "off",
                    "rollout": 0,
                    "expires_at": "2026-04-15",
                    "created_at": "2026-01-18",
                    "description": "旧搜索栈回退开关",
                },
                {
                    "key": "payment_risk_guard",
                    "owner": "risk",
                    "kind": "release",
                    "status": "on",
                    "rollout": 35,
                    "expires_at": "2026-12-20",
                    "created_at": "2026-06-01",
                    "description": "高风险资金风控保护",
                },
                {
                    "key": "pricing_experiment_v2",
                    "owner": "monetization",
                    "kind": "experiment",
                    "status": "archived",
                    "rollout": 100,
                    "expires_at": "2026-02-28",
                    "created_at": "2025-12-16",
                    "description": "定价实验残留",
                },
            ]
        },
        ensure_ascii=False,
        indent=2,
    ),
    "experiments_text": json.dumps(
        [
            {
                "flag": "checkout_banner",
                "started_at": "2026-01-10",
                "ended_at": "2026-04-10",
                "status": "completed",
                "owner": "growth",
            },
            {
                "flag": "legacy_search",
                "started_at": "2026-01-20",
                "ended_at": "2026-03-20",
                "status": "completed",
                "owner": "search",
            },
            {
                "flag": "pricing_experiment_v2",
                "started_at": "2025-12-18",
                "ended_at": "2026-02-28",
                "status": "completed",
                "owner": "monetization",
            },
        ],
        ensure_ascii=False,
        indent=2,
    ),
    "releases_text": json.dumps(
        [
            {
                "version": "2.8.0",
                "date": "2026-04-12",
                "notes": "checkout_banner 已 100% 发布，legacy_search 可以开始清理",
                "flags": ["checkout_banner"],
            },
            {
                "version": "2.9.1",
                "date": "2026-05-18",
                "notes": "pricing_experiment_v2 实验结束，保留回滚说明",
                "flags": ["pricing_experiment_v2"],
            },
            {
                "version": "3.0.0",
                "date": "2026-08-20",
                "notes": "payment_risk_guard 继续保留，等待风控验收",
                "flags": ["payment_risk_guard"],
            },
        ],
        ensure_ascii=False,
        indent=2,
    ),
    "code_files": [
        {
            "path": "src/ui/banner.tsx",
            "content": """import { flags } from '../feature-flags';

export function Banner() {
  if (!flags.checkout_banner) {
    return <LegacyBanner />;
  }
  return <NewBanner />;
}
""",
        },
        {
            "path": "src/search/engine.py",
            "content": """from feature_flags import feature_flags

def pick_search_path(query: str) -> str:
    if feature_flags['legacy_search']:
        return run_legacy_path(query)
    return run_new_path(query)
""",
        },
        {
            "path": "src/payments/risk.ts",
            "content": """export function shouldApplyGuard(flags) {
  if (flags.payment_risk_guard) {
    return true;
  }
  return false;
}
""",
        },
        {
            "path": "tests/banner.test.ts",
            "content": """import { Banner } from '../src/ui/banner';

describe('Banner', () => {
  it('keeps fallback snapshot for the rollout window', () => {
    expect(Banner).toBeDefined();
  });
});
""",
        },
        {
            "path": "tests/search.test.py",
            "content": """def test_search_paths():
    assert True
""",
        },
    ],
}


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_date(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 10 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        year, month, day = map(int, text.split("-"))
        return datetime(year, month, day, tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_scalar(value: Any) -> Any:
    if isinstance(value, (dict, list, bool, int, float)) or value is None:
        return value
    text = str(value).strip()
    if text.startswith(("'", '"')) and text.endswith(("'", '"')) and len(text) >= 2:
        return text[1:-1]
    lowered = text.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none"}:
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def normalize_key(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")


def slug_aliases(flag_key: str) -> list[str]:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", flag_key) if part]
    camel = parts[0] + "".join(part.capitalize() for part in parts[1:]) if parts else flag_key
    env = re.sub(r"[^A-Za-z0-9]+", "_", flag_key).upper()
    aliases = {
        flag_key,
        normalize_key(flag_key),
        env,
        camel,
        flag_key.replace("_", "-"),
        flag_key.replace("-", "_"),
    }
    return [alias for alias in aliases if alias]


def load_text_or_json(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (int, float, bool)):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        return tomllib.loads(text)
    except Exception:
        pass
    return text


def coerce_flag_item(key: Any, value: Any) -> dict[str, Any]:
    item = dict(value) if isinstance(value, dict) else {"value": value}
    item.setdefault("key", key)
    item["key"] = str(item["key"])
    item["owner"] = str(item.get("owner") or item.get("team") or "unassigned")
    item["kind"] = str(item.get("kind") or item.get("type") or "release")
    item["status"] = str(item.get("status") or item.get("state") or ("on" if item.get("enabled") else "unknown"))
    item["rollout"] = int(item.get("rollout") or item.get("percentage") or item.get("percent") or 0)
    item["expires_at"] = item.get("expires_at") or item.get("expiresAt") or item.get("sunset_at") or item.get("sunsetAt")
    item["created_at"] = item.get("created_at") or item.get("createdAt")
    item["replacement"] = item.get("replacement") or item.get("replacement_flag")
    item["description"] = item.get("description") or item.get("note") or ""
    item["ticket"] = item.get("ticket") or item.get("issue") or item.get("pr")
    return item


def parse_manifest(text: Any) -> list[dict[str, Any]]:
    loaded = load_text_or_json(text)
    if loaded is None:
        return []
    if isinstance(loaded, list):
        return [coerce_flag_item(item.get("key") or item.get("name") or item.get("id"), item) for item in loaded if isinstance(item, dict)]
    if isinstance(loaded, dict):
        if isinstance(loaded.get("flags"), list):
            return [coerce_flag_item(item.get("key") or item.get("name") or item.get("id"), item) for item in loaded["flags"] if isinstance(item, dict)]
        if isinstance(loaded.get("flags"), dict):
            return [coerce_flag_item(key, value) for key, value in loaded["flags"].items()]
        if any(isinstance(value, dict) for value in loaded.values()):
            return [coerce_flag_item(key, value) for key, value in loaded.items() if isinstance(value, dict)]
    if isinstance(loaded, str):
        return parse_block_manifest(loaded)
    return []


def parse_block_manifest(text: str) -> list[dict[str, Any]]:
    blocks = re.split(r"\n\s*\n", text.strip())
    flags: list[dict[str, Any]] = []
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines() if line.strip() and not line.strip().startswith("#")]
        if not lines:
            continue
        first = lines[0]
        match = re.match(r"^(?:-?\s*)?(?:flag|feature|name)?\s*([A-Za-z0-9_.:-]+)\s*[:=]?\s*(.*)$", first)
        if not match:
            continue
        key = match.group(1)
        record: dict[str, Any] = {"key": key}
        if match.group(2):
            record["value"] = parse_scalar(match.group(2))
        for line in lines[1:]:
            pair = re.match(r"^([A-Za-z0-9_.:-]+)\s*[:=]\s*(.+)$", line.strip())
            if not pair:
                continue
            record[pair.group(1)] = parse_scalar(pair.group(2))
        flags.append(coerce_flag_item(key, record))
    return flags


def parse_records(value: Any) -> list[dict[str, Any]]:
    loaded = load_text_or_json(value)
    if loaded is None:
        return []
    if isinstance(loaded, list):
        return [item for item in loaded if isinstance(item, dict)]
    if isinstance(loaded, dict):
        if isinstance(loaded.get("items"), list):
            return [item for item in loaded["items"] if isinstance(item, dict)]
        return [value for value in loaded.values() if isinstance(value, dict)]
    if isinstance(loaded, str):
        records = []
        for chunk in re.split(r"\n\s*\n", loaded.strip()):
            block = {}
            for line in chunk.splitlines():
                pair = re.match(r"^([A-Za-z0-9_.:-]+)\s*[:=]\s*(.+)$", line.strip())
                if pair:
                    block[pair.group(1)] = parse_scalar(pair.group(2))
            if block:
                records.append(block)
        return records
    return []


def parse_code_files(payload: Any) -> list[dict[str, str]]:
    if not payload:
        return []
    files: list[dict[str, str]] = []
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or item.get("name") or "untitled.txt")
            content = str(item.get("content") or item.get("text") or "")
            files.append({"path": path, "content": content})
        return files
    if isinstance(payload, dict) and isinstance(payload.get("files"), list):
        return parse_code_files(payload["files"])
    return [{"path": "input.txt", "content": str(payload)}]


def line_matches_flag(line: str, flag: str) -> tuple[bool, str]:
    aliases = slug_aliases(flag)
    pattern = r"(?<![A-Za-z0-9_])(" + "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True)) + r")(?![A-Za-z0-9_])"
    if not re.search(pattern, line):
        return False, ""
    lowered = line.lower()
    alias_hit = any(alias.lower() in lowered for alias in aliases)
    negative = bool(
        alias_hit
        and (
            "!" in line
            or "== false" in lowered
            or "=== false" in lowered
            or re.search(r"\bnot\b", lowered)
        )
    )
    if negative:
        return True, "negative"
    if re.search(r"\belse\b", line):
        return True, "else"
    if re.search(r"\bif\b|\bwhen\b|\bcase\b", line):
        return True, "positive"
    return True, "reference"


def lookup_test_candidates(code_files: list[dict[str, str]], ref_path: str) -> list[str]:
    ref = Path(ref_path)
    base = ref.stem
    candidates: list[str] = []
    for item in code_files:
        path = Path(item["path"])
        name = path.name.lower()
        if path == ref:
            continue
        if path.suffix.lower() not in {".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs", ".java", ".json"}:
            continue
        if any(token in name for token in {base.lower(), "test", "spec"}):
            candidates.append(item["path"])
    return candidates[:5]


def infer_risk(flag: dict[str, Any]) -> str:
    text = " ".join(str(flag.get(field, "")) for field in ("key", "description", "owner", "kind", "replacement")).lower()
    if any(word in text for word in ("payment", "billing", "risk", "auth", "permission", "export", "security")):
        return "high"
    if flag.get("kind") == "experiment":
        return "medium"
    return "low"


def build_findings(flags: list[dict[str, Any]], code_files: list[dict[str, str]], experiments: list[dict[str, Any]], releases: list[dict[str, Any]], today: datetime) -> dict[str, Any]:
    all_hits: dict[str, list[dict[str, Any]]] = defaultdict(list)
    file_index = {item["path"]: item["content"].splitlines() for item in code_files}

    for flag in flags:
        aliases = slug_aliases(flag["key"])
        for path, lines in file_index.items():
            for line_no, line in enumerate(lines, 1):
                found, polarity = line_matches_flag(line, flag["key"])
                if not found:
                    continue
                if not any(alias.lower() in line.lower() for alias in aliases):
                    continue
                all_hits[flag["key"]].append(
                    {
                        "file": path,
                        "line": line_no,
                        "snippet": line.strip(),
                        "polarity": polarity,
                    }
                )

    experiment_by_flag = defaultdict(list)
    for item in experiments:
        key = normalize_key(item.get("flag") or item.get("name") or item.get("key"))
        if key:
            experiment_by_flag[key].append(item)

    flag_rows: list[dict[str, Any]] = []
    dead_branches: list[dict[str, Any]] = []
    experimental_residue: list[dict[str, Any]] = []
    orphan_flags: list[dict[str, Any]] = []

    for flag in flags:
        key = normalize_key(flag["key"])
        hits = all_hits.get(flag["key"], [])
        refs = len(hits)
        expires_at = parse_date(flag.get("expires_at"))
        created_at = parse_date(flag.get("created_at"))
        rollout = int(flag.get("rollout") or 0)
        status = str(flag.get("status") or "").lower()
        expired = bool(expires_at and expires_at < today)
        archived = status in {"archived", "removed", "retired"}
        completed_experiment = any(
            parse_date(exp.get("ended_at")) and parse_date(exp.get("ended_at")) < today
            for exp in experiment_by_flag.get(key, [])
        )
        stale_age = None
        if created_at:
            stale_age = (today - created_at).days
        branch_state = None
        if refs:
            if rollout >= 100:
                branch_state = "dead-else" if any(hit["polarity"] == "positive" for hit in hits) else "dead-then"
            elif rollout <= 0:
                branch_state = "dead-then" if any(hit["polarity"] == "positive" for hit in hits) else "dead-else"
        score = 0
        if expired:
            score += 40
        if archived:
            score += 20
        if refs and rollout in {0, 100}:
            score += 20
        if not refs:
            score += 15
        if completed_experiment:
            score += 10
        if stale_age and stale_age > 90:
            score += 10
        priority = "P0" if expired and rollout == 100 else "P1" if expired or branch_state else "P2" if not refs else "P3"
        action = "delete-config-and-code" if expired and refs else "remove-config" if not refs else "delete-dead-branch"
        reason_bits = []
        if expired:
            reason_bits.append(f"已过期 {expires_at.date().isoformat() if expires_at else ''}")
        if archived:
            reason_bits.append("状态已归档")
        if rollout >= 100:
            reason_bits.append("已 100% 发布")
        if rollout <= 0:
            reason_bits.append("已完全关闭")
        if not refs:
            reason_bits.append("代码中未再检出引用")
        if completed_experiment:
            reason_bits.append("实验已结束")
        if not reason_bits:
            reason_bits.append("仍处在观察窗口")
        risk = infer_risk(flag)
        evidence = "；".join(reason_bits[:2])
        confidence = "high" if (expired and (rollout in {0, 100} or refs == 0)) or archived else "medium" if expired or branch_state or completed_experiment else "low"
        row = {
            "key": flag["key"],
            "owner": flag["owner"],
            "kind": flag["kind"],
            "status": status or "unknown",
            "rollout": rollout,
            "expires_at": expires_at.date().isoformat() if expires_at else None,
            "created_at": created_at.date().isoformat() if created_at else None,
            "risk": risk,
            "references": hits,
            "reference_count": refs,
            "dead_branch": branch_state,
            "expired": expired,
            "archived": archived,
            "completed_experiment": completed_experiment,
            "cleanup_priority": priority,
            "cleanup_action": action,
            "reasons": reason_bits,
            "score": score,
            "replacement": flag.get("replacement"),
            "ticket": flag.get("ticket"),
            "description": flag.get("description"),
            "confidence": confidence,
            "evidence": evidence,
            "test_candidates": lookup_test_candidates(code_files, hits[0]["file"]) if hits else [],
        }
        flag_rows.append(row)
        if branch_state:
            dead_branches.append(
                {
                    "flag": flag["key"],
                    "priority": priority,
                    "file": hits[0]["file"],
                    "line": hits[0]["line"],
                    "branch": "else" if branch_state == "dead-else" else "then",
                    "reason": "flag 已稳定到单侧分支，另一侧可删",
                    "snippet": hits[0]["snippet"],
                    "tests": lookup_test_candidates(code_files, hits[0]["file"]),
                    "removal_hint": f"删除 {hits[0]['file']}:{hits[0]['line']} 附近的 {('else' if branch_state == 'dead-else' else 'if')} 分支",
                }
            )
        if completed_experiment and hits:
            experimental_residue.append(
                {
                    "flag": flag["key"],
                    "file": hits[0]["file"],
                    "line": hits[0]["line"],
                    "reason": "实验已结束但代码仍保留实验分支",
                    "hint": "优先删除实验路径并同步更新文档/测试",
                }
            )
        if not refs:
            orphan_flags.append(
                {
                    "flag": flag["key"],
                    "reason": "配置中仍保留，但仓库内未发现使用点",
                    "cleanup": "先确认是否还有外部消费者，再删配置和发布说明",
                }
            )

    return {
        "flags": flag_rows,
        "dead_branches": dead_branches,
        "experimental_residue": experimental_residue,
        "orphan_flags": orphan_flags,
        "references": all_hits,
    }


def build_graph(flags: list[dict[str, Any]], code_files: list[dict[str, str]], findings: dict[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    x_flag = 240
    x_file = 760
    y = 110
    for idx, flag in enumerate(findings["flags"]):
        nodes.append(
            {
                "id": f"flag:{flag['key']}",
                "label": flag["key"],
                "kind": "flag",
                "x": x_flag,
                "y": y + idx * 84,
                "risk": flag["risk"],
                "score": flag["score"],
            }
        )
    file_y = 80
    for idx, file in enumerate(code_files):
        nodes.append(
            {
                "id": f"file:{file['path']}",
                "label": file["path"],
                "kind": "file",
                "x": x_file,
                "y": file_y + idx * 64,
            }
        )
    node_lookup = {node["id"]: node for node in nodes}
    for flag in findings["flags"]:
        fid = f"flag:{flag['key']}"
        for hit in flag["references"]:
            nid = f"file:{hit['file']}"
            if nid in node_lookup:
                edges.append(
                    {
                        "from": fid,
                        "to": nid,
                        "line": hit["line"],
                        "polarity": hit["polarity"],
                    }
                )
    return {"nodes": nodes, "edges": edges}


def build_cleanup_list(findings: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in findings["flags"]:
        if row["cleanup_priority"] in {"P0", "P1"}:
            items.append(
                {
                    "priority": row["cleanup_priority"],
                    "task": f"清理 {row['key']}",
                    "detail": ", ".join(row["reasons"]),
                    "files": [hit["file"] for hit in row["references"]] or ["manifest"],
                    "tests": row.get("test_candidates", []),
                }
            )
    if not items:
        items.append({"priority": "P3", "task": "暂无高风险清理项", "detail": "继续观察当前旗标状态", "files": [], "tests": []})
    return items


def build_reminders(findings: dict[str, Any]) -> list[str]:
    reminders = [
        "先确认每个 flag 的最后一次发布记录，再删配置，避免误伤回滚窗口。",
        "删除代码分支前，先补齐回归测试或快照测试，尤其是支付、认证、导出类开关。",
        "如果一个 flag 已经 100% 发布，优先删 fallback 分支，再删配置，再清文档和监控。",
    ]
    if any(row["risk"] == "high" for row in findings["flags"]):
        reminders.append("高风险 flag 需要第二位 reviewer 复核，并确认灰度/回滚路径仍可用。")
    if findings["experimental_residue"]:
        reminders.append("实验代码不要只删 if 条件，连埋点、文档和实验说明一起清掉。")
    return reminders


def analyze_payload(payload: dict[str, Any]) -> dict[str, Any]:
    flags = parse_manifest(payload.get("manifest_text") or payload.get("manifest") or payload.get("flags"))
    code_files = parse_code_files(payload.get("code_files") or payload.get("files") or payload.get("code"))
    experiments = parse_records(payload.get("experiments_text") or payload.get("experiments"))
    releases = parse_records(payload.get("releases_text") or payload.get("releases"))
    today = parse_date(payload.get("today")) or datetime.now(timezone.utc)
    findings = build_findings(flags, code_files, experiments, releases, today)
    graph = build_graph(flags, code_files, findings)
    cleanup_list = build_cleanup_list(findings)
    reminders = build_reminders(findings)
    counts = Counter(
        {
            "total_flags": len(findings["flags"]),
            "expired_flags": sum(1 for row in findings["flags"] if row["expired"]),
            "dead_branches": len(findings["dead_branches"]),
            "experimental_residue": len(findings["experimental_residue"]),
            "orphan_flags": len(findings["orphan_flags"]),
            "clean_candidates": sum(1 for row in findings["flags"] if row["cleanup_priority"] in {"P0", "P1"}),
        }
    )
    risk_score = max(0, 100 - counts["expired_flags"] * 18 - counts["dead_branches"] * 10 - counts["experimental_residue"] * 8 - counts["orphan_flags"] * 5)
    return {
        "generated_at": iso_now(),
        "summary": {
            **counts,
            "risk_score": risk_score,
            "code_files": len(code_files),
        },
        "flags": sorted(findings["flags"], key=lambda row: (row["cleanup_priority"], -row["score"], row["key"])),
        "dead_branches": findings["dead_branches"],
        "experimental_residue": findings["experimental_residue"],
        "orphan_flags": findings["orphan_flags"],
        "cleanup_list": cleanup_list,
        "reminders": reminders,
        "graph": graph,
        "source": {
            "manifest_text": payload.get("manifest_text") or "",
            "experiments_text": payload.get("experiments_text") or "",
            "releases_text": payload.get("releases_text") or "",
            "code_files": code_files,
        },
    }


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_file(self, path: Path) -> None:
        if not path.exists():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            return self.send_file(PUBLIC / "index.html")
        if path == "/styles.css":
            return self.send_file(PUBLIC / "styles.css")
        if path == "/app.js":
            return self.send_file(PUBLIC / "app.js")
        if path == "/api/health":
            return json_response(self, 200, {"ok": True, "name": "Feature Flag Janitor", "time": iso_now()})
        if path == "/api/sample":
            return json_response(self, 200, SAMPLE_PAYLOAD)
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/analyze":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            result = analyze_payload(payload)
            json_response(self, 200, result)
        except Exception as exc:
            json_response(self, 400, {"ok": False, "error": str(exc)})


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Feature Flag Janitor listening on {port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
