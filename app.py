from __future__ import annotations

import json
import os
import re
import tomllib
from collections import Counter
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from janitor.validation import (
    MAX_REQUEST_BYTES,
    InputError,
    parse_rollout,
    validate_payload,
)
from janitor.scanner import lookup_test_candidates, scan_references
from janitor.rules import dead_branch_state, flag_lifecycle, group_experiments, reasons_for
from janitor.scoring import action_for, confidence_for, infer_risk, priority_for, score_finding
from janitor.storage import Store, StorageError


ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
STORE = Store()

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
    rollout_value = item.get("rollout") or item.get("percentage") or item.get("percent") or 0
    item["rollout"] = parse_rollout(rollout_value, f"flag {item['key']}.rollout")
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


def build_findings(flags: list[dict[str, Any]], code_files: list[dict[str, str]], experiments: list[dict[str, Any]], releases: list[dict[str, Any]], today: datetime) -> dict[str, Any]:
    all_hits = scan_references(flags, code_files)
    experiment_by_flag = group_experiments(experiments, normalize_key)

    flag_rows: list[dict[str, Any]] = []
    dead_branches: list[dict[str, Any]] = []
    experimental_residue: list[dict[str, Any]] = []
    orphan_flags: list[dict[str, Any]] = []

    for flag in flags:
        key = normalize_key(flag["key"])
        hits = all_hits.get(flag["key"], [])
        refs = len(hits)
        rollout = parse_rollout(flag.get("rollout"), f"flag {flag['key']}.rollout")
        lifecycle = flag_lifecycle(flag, experiment_by_flag.get(key, []), today, parse_date)
        branch_state = dead_branch_state(hits, rollout)
        score = score_finding(lifecycle, refs, rollout)
        priority = priority_for(lifecycle, rollout, branch_state, refs)
        action = action_for(lifecycle, refs)
        reason_bits = reasons_for(flag, lifecycle, refs)
        risk = infer_risk(flag)
        evidence = "；".join(reason_bits[:2])
        confidence = confidence_for(lifecycle, branch_state, rollout, refs)
        row = {
            "key": flag["key"],
            "owner": flag["owner"],
            "kind": flag["kind"],
            "status": lifecycle["status"],
            "rollout": rollout,
            "expires_at": lifecycle["expires_at"].date().isoformat() if lifecycle["expires_at"] else None,
            "created_at": lifecycle["created_at"].date().isoformat() if lifecycle["created_at"] else None,
            "risk": risk,
            "references": hits,
            "reference_count": refs,
            "reference_types": dict(Counter(hit["reference_type"] for hit in hits)),
            "dead_branch": branch_state,
            "expired": lifecycle["expired"],
            "archived": lifecycle["archived"],
            "completed_experiment": lifecycle["completed_experiment"],
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
        if lifecycle["completed_experiment"] and hits:
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
                    "finding_key": row["key"],
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


def build_input_check(flags: list[dict[str, Any]], code_files: list[dict[str, str]], experiments: list[dict[str, Any]], releases: list[dict[str, Any]]) -> dict[str, Any]:
    warnings: list[str] = []
    if not flags:
        warnings.append("没有解析到 flag 配置，无法判断过期项。")
    if not code_files:
        warnings.append("没有上传代码文件，孤儿 flag 和死分支判断会不完整。")
    if not experiments:
        warnings.append("没有实验日期，实验残留判断会不完整。")
    if not releases:
        warnings.append("没有发布记录，无法完整验证最后发布和回滚窗口。")
    return {
        "valid": not warnings or bool(flags),
        "flags": len(flags),
        "code_files": len(code_files),
        "experiments": len(experiments),
        "releases": len(releases),
        "warnings": warnings,
    }


def analyze_payload(payload: dict[str, Any]) -> dict[str, Any]:
    flags = parse_manifest(payload.get("manifest_text") or payload.get("manifest") or payload.get("flags"))
    code_files = parse_code_files(payload.get("code_files") or payload.get("files") or payload.get("code"))
    experiments = parse_records(payload.get("experiments_text") or payload.get("experiments"))
    releases = parse_records(payload.get("releases_text") or payload.get("releases"))
    validate_payload(
        payload,
        parse_manifest=parse_manifest,
        parse_code_files=parse_code_files,
        parse_records=parse_records,
        normalize_key=normalize_key,
        parse_date=parse_date,
    )
    today = parse_date(payload.get("today")) or datetime.now(timezone.utc)
    findings = build_findings(flags, code_files, experiments, releases, today)
    graph = build_graph(flags, code_files, findings)
    cleanup_list = build_cleanup_list(findings)
    reminders = build_reminders(findings)
    input_check = build_input_check(flags, code_files, experiments, releases)
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
    result = {
        "generated_at": iso_now(),
        "summary": {
            **counts,
            "risk_score": risk_score,
            "code_files": len(code_files),
        },
        "input_check": input_check,
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
    result["scan_id"] = STORE.save_scan(result)
    result["actions"] = {}
    return result


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
        if path == "/api/scans":
            return json_response(self, 200, {"scans": STORE.list_scans()})
        if path.startswith("/api/scans/"):
            scan = STORE.get_scan(path.rsplit("/", 1)[-1])
            return json_response(self, 200, scan) if scan else json_response(self, 404, {"ok": False, "error": "找不到对应的扫描记录"})
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in {"/api/analyze", "/api/actions"}:
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_REQUEST_BYTES:
                raise InputError("请求体不能超过 8 MB")
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if path == "/api/actions":
                action = STORE.save_action(str(payload.get("scan_id") or ""), str(payload.get("finding_key") or ""), str(payload.get("action") or ""), str(payload.get("note") or ""))
                return json_response(self, 200, {"ok": True, **action})
            result = analyze_payload(payload)
            json_response(self, 200, result)
        except json.JSONDecodeError:
            json_response(self, 400, {"ok": False, "error": "请求内容不是有效的 JSON"})
        except UnicodeDecodeError:
            json_response(self, 400, {"ok": False, "error": "请求内容必须使用 UTF-8 编码"})
        except (InputError, StorageError) as exc:
            json_response(self, 400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            json_response(self, 500, {"ok": False, "error": "扫描服务暂时无法处理该请求"})


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Feature Flag Janitor listening on {port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
