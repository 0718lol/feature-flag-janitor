from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import io
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP_DIR = tempfile.TemporaryDirectory()
os.environ["PORT"] = "0"

import app
from janitor.scanner import reference_type, scan_references
from janitor.rules import dead_branch_state
from janitor.scoring import infer_risk, priority_for, score_finding


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

    def test_invalid_rollout_returns_actionable_error(self):
        status, result = self.request_json(
            "/api/analyze",
            {"manifest_text": json.dumps({"flags": [{"key": "bad_rollout", "rollout": 120}]})},
        )
        self.assertEqual(status, 400)
        self.assertIn("rollout", result["error"])

    def test_invalid_date_returns_actionable_error(self):
        status, result = self.request_json(
            "/api/analyze",
            {"manifest_text": json.dumps({"flags": [{"key": "bad_date", "expires_at": "tomorrow"}]})},
        )
        self.assertEqual(status, 400)
        self.assertIn("YYYY-MM-DD", result["error"])

    def test_invalid_json_returns_actionable_error(self):
        req = Request(
            self.base_url + "/api/analyze",
            data=b"{not-json}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(req, timeout=3)
        except HTTPError as error:
            self.assertEqual(error.code, 400)
            result = json.load(error)
            self.assertIn("有效的 JSON", result["error"])
        else:
            self.fail("invalid JSON should return HTTP 400")

    def test_comments_are_not_counted_as_code_references(self):
        payload = {
            "manifest_text": json.dumps({"flags": [{"key": "comment_only", "rollout": 100}]}),
            "code_files": [{"path": "src/app.ts", "content": "// comment_only\nconst note = 'comment_only';\n"}],
            "today": "2026-09-01",
        }
        status, result = self.request_json("/api/analyze", payload)
        self.assertEqual(status, 200)
        self.assertEqual(result["flags"][0]["reference_count"], 1)
        self.assertEqual(result["flags"][0]["reference_types"], {"reference": 1})

    def test_reference_evidence_includes_conditional_type(self):
        payload = {
            "manifest_text": json.dumps({"flags": [{"key": "checkout_banner", "rollout": 100}]}),
            "code_files": [{"path": "src/app.ts", "content": "if (flags.checkout_banner) {\n  render();\n}\n"}],
            "today": "2026-09-01",
        }
        status, result = self.request_json("/api/analyze", payload)
        self.assertEqual(status, 200)
        self.assertEqual(result["flags"][0]["reference_types"], {"conditional_branch": 1})
        self.assertEqual(result["dead_branches"][0]["branch"], "else")

    def test_scanner_classifies_tests_and_docs_without_branch_noise(self):
        flags = [{"key": "checkout_banner"}]
        files = [
            {"path": "README.md", "content": "checkout_banner is retired\n"},
            {"path": "tests/banner.spec.ts", "content": "expect(flags.checkout_banner).toBe(true);\n"},
        ]
        hits = scan_references(flags, files)["checkout_banner"]
        self.assertEqual({hit["reference_type"] for hit in hits}, {"documentation", "test_reference"})
        self.assertEqual(reference_type("src/app.ts", "flags.checkout_banner", "reference"), "reference")

    def test_rules_do_not_mark_runtime_read_as_dead_branch(self):
        hits = [{"reference_type": "runtime_read", "polarity": "reference"}]
        self.assertIsNone(dead_branch_state(hits, 100))

    def test_scoring_prioritizes_expired_sensitive_flag(self):
        lifecycle = {
            "expired": True,
            "archived": False,
            "completed_experiment": False,
            "stale_age": 120,
        }
        self.assertEqual(score_finding(lifecycle, 2, 100), 70)
        self.assertEqual(priority_for(lifecycle, 100, "dead-else", 2), "P0")
        self.assertEqual(infer_risk({"key": "payment_risk_guard"}), "high")

    def test_scan_is_persisted_and_action_can_be_updated(self):
        status, sample = self.request_json("/api/sample")
        self.assertEqual(status, 200)
        status, result = self.request_json("/api/analyze", sample)
        self.assertEqual(status, 200)
        self.assertTrue(result["scan_id"].startswith("scan_"))
        finding_key = result["cleanup_list"][0]["finding_key"]
        status, action = self.request_json(
            "/api/actions",
            {"scan_id": result["scan_id"], "finding_key": finding_key, "action": "ignore"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(action["action"], "ignore")
        status, loaded = self.request_json(f"/api/scans/{result['scan_id']}")
        self.assertEqual(status, 200)
        self.assertEqual(loaded["actions"][finding_key]["action"], "ignore")

    def test_invalid_action_returns_bad_request(self):
        status, result = self.request_json(
            "/api/actions",
            {"scan_id": "missing", "finding_key": "flag", "action": "delete"},
        )
        self.assertEqual(status, 400)
        self.assertIn("action", result["error"])

    def test_action_must_target_existing_finding(self):
        status, sample = self.request_json("/api/sample")
        status, result = self.request_json("/api/analyze", sample)
        self.assertEqual(status, 200)
        status, response = self.request_json(
            "/api/actions",
            {"scan_id": result["scan_id"], "finding_key": "not_a_finding", "action": "ignore"},
        )
        self.assertEqual(status, 400)
        self.assertIn("清理项", response["error"])

    def test_output_contract_contains_input_check_and_consistent_counts(self):
        status, sample = self.request_json("/api/sample")
        status, result = self.request_json("/api/analyze", sample)
        self.assertEqual(status, 200)
        self.assertTrue(result["input_check"]["valid"])
        self.assertEqual(result["input_check"]["flags"], result["summary"]["total_flags"])
        self.assertEqual(result["input_check"]["code_files"], result["summary"]["code_files"])
        self.assertEqual(result["summary"]["expired_flags"], sum(1 for row in result["flags"] if row["expired"]))

    def test_input_check_warns_when_optional_sources_are_missing(self):
        status, result = self.request_json(
            "/api/analyze",
            {"manifest_text": json.dumps({"flags": [{"key": "simple_flag"}]})},
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["input_check"]["valid"])
        self.assertEqual(len(result["input_check"]["warnings"]), 3)

    def test_zip_import_reads_text_files_and_skips_unsafe_paths(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("src/app.py", "if flags.old_gate:\n    pass\n")
            zipped.writestr("../escape.py", "old_gate")
            zipped.writestr("image.bin", "not code")
        req = Request(self.base_url + "/api/import-zip", data=archive.getvalue(), headers={"Content-Type": "application/zip"}, method="POST")
        with urlopen(req, timeout=3) as response:
            self.assertEqual(response.status, 200)
            result = json.load(response)
        self.assertEqual([item["path"] for item in result["code_files"]], ["src/app.py"])

    def test_patch_endpoint_is_review_only(self):
        status, sample = self.request_json("/api/sample")
        status, result = self.request_json("/api/analyze", sample)
        self.assertEqual(status, 200)
        status, patch = self.request_json("/api/patch", {"scan_id": result["scan_id"]})
        self.assertEqual(status, 200)
        self.assertTrue(patch["review_only"])
        self.assertIn("No files were changed", patch["patch"])
        self.assertIn("checkout_banner", patch["patch"])


if __name__ == "__main__":
    unittest.main()
