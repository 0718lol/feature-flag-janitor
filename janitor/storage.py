from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "janitor.sqlite3"
VALID_ACTIONS = {"defer", "ignore", "resolve"}


class StorageError(ValueError):
    """A user-correctable persistence request error."""


class Store:
    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path or os.environ.get("JANITOR_DB_PATH", DEFAULT_DB_PATH))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS scans (
                    scan_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    result_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS finding_actions (
                    scan_id TEXT NOT NULL,
                    finding_key TEXT NOT NULL,
                    action TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (scan_id, finding_key),
                    FOREIGN KEY (scan_id) REFERENCES scans(scan_id) ON DELETE CASCADE
                );
            """)

    def save_scan(self, result: dict[str, Any]) -> str:
        scan_id = f"scan_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        stored = {**result, "scan_id": scan_id}
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO scans(scan_id, created_at, summary_json, result_json) VALUES (?, ?, ?, ?)",
                (scan_id, result["generated_at"], json.dumps(result["summary"], ensure_ascii=False), json.dumps(stored, ensure_ascii=False)),
            )
        return scan_id

    def list_scans(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT scan_id, created_at, summary_json FROM scans ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 100),)).fetchall()
        return [{"scan_id": row["scan_id"], "created_at": row["created_at"], "summary": json.loads(row["summary_json"])} for row in rows]

    def get_scan(self, scan_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT result_json FROM scans WHERE scan_id = ?", (scan_id,)).fetchone()
            actions = connection.execute("SELECT finding_key, action, note, updated_at FROM finding_actions WHERE scan_id = ?", (scan_id,)).fetchall()
        if row is None:
            return None
        result = json.loads(row["result_json"])
        result["actions"] = {item["finding_key"]: dict(item) for item in actions}
        return result

    def save_action(self, scan_id: str, finding_key: str, action: str, note: str = "") -> dict[str, Any]:
        if action not in VALID_ACTIONS:
            raise StorageError("action 必须是 defer、ignore 或 resolve")
        if not finding_key.strip():
            raise StorageError("finding_key 不能为空")
        updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as connection:
            row = connection.execute("SELECT result_json FROM scans WHERE scan_id = ?", (scan_id,)).fetchone()
            if row is None:
                raise StorageError("找不到对应的扫描记录")
            result = json.loads(row["result_json"])
            valid_keys = {item.get("finding_key") for item in result.get("cleanup_list", [])}
            if finding_key.strip() not in valid_keys:
                raise StorageError("找不到对应的清理项")
            connection.execute(
                "INSERT INTO finding_actions(scan_id, finding_key, action, note, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(scan_id, finding_key) DO UPDATE SET action=excluded.action, note=excluded.note, updated_at=excluded.updated_at",
                (scan_id, finding_key.strip(), action, note[:500], updated_at),
            )
        return {"finding_key": finding_key.strip(), "action": action, "note": note[:500], "updated_at": updated_at}
