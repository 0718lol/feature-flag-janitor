from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["PORT"] = "0"

import app


class FeatureFlagJanitorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        TEMP_DIR.cleanup()

    def request_json(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST" if payload is not None else "GET",
        )
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def test_sample_returns_actionable_candidates(self):
        status, sample = self.request_json("/api/sample")
        self.assertEqual(status, 200)
        status, result = self.request_json("/api/analyze", sample)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(result["summary"]["expired_flags"], 1)
        self.assertGreaterEqual(result["summary"]["dead_branches"], 1)
        self.assertGreaterEqual(result["summary"]["clean_candidates"], 1)
        self.assertTrue(result["cleanup_list"])

    def test_expired_flag_and_dead_branch_are_reported(self):
        payload = {
            "manifest_text": json.dumps(
                {
                    "flags": [
                        {
                            "key": "old_gate",
                            "owner": "platform",
                            "status": "on",
                            "rollout": 100,
                            "expires_at": "2026-01-01",
                        }
                    ]
                }
            ),
            "experiments_text": "[]",
            "releases_text": json.dumps([{"version": "1.0.0", "date": "2026-01-02", "flags": ["old_gate"]}]),
            "code_files": [
                {"path": "src/app.ts", "content": "if (!flags.old_gate) {\n  return fallback();\n}\n"},
            ],
        }
        status, result = self.request_json("/api/analyze", payload)
        self.assertEqual(status, 200)
        self.assertEqual(result["flags"][0]["key"], "old_gate")
        self.assertTrue(result["flags"][0]["expired"])
        self.assertEqual(result["dead_branches"][0]["branch"], "then")

    def test_health_endpoint(self):
        status, result = self.request_json("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()

