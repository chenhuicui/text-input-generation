from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from text_input_eval.supplementary.abcdef_mysql import (
    TABLES,
    _chunks,
    _ensure_schema,
    build_mysql_rows,
    mysql_inventory,
)
from text_input_eval.supplementary.structured_storage import StructuredLedger


class ABCDEFMySQLExportTests(unittest.TestCase):
    def test_qualified_schema_skips_redundant_create_table_ddl(self) -> None:
        class Cursor:
            def __init__(self) -> None:
                self.executions: list[tuple[str, object]] = []

            def execute(self, sql: str, params: object = None) -> None:
                self.executions.append((sql, params))

            def fetchall(self) -> list[tuple[str]]:
                return [(name,) for name in TABLES.values()]

        cursor = Cursor()
        self.assertFalse(_ensure_schema(cursor, "must_lab_db"))
        self.assertEqual(len(cursor.executions), 1)
        self.assertIn("information_schema.tables", cursor.executions[0][0])

    def test_mysql_chunks_are_count_and_payload_bounded(self) -> None:
        rows = [
            ("a" * 60, 1),
            ("b" * 60, 2),
            ("c" * 20, 3),
            ("d" * 20, 4),
            ("e" * 20, 5),
        ]
        chunks = list(_chunks(rows, size=2, max_payload_bytes=100))
        self.assertEqual(chunks, [rows[0:1], rows[1:3], rows[3:5]])

    def test_mysql_chunks_preserve_single_oversized_row(self) -> None:
        oversized = ("x" * 200,)
        rows = [("small",), oversized, ("tail",)]
        self.assertEqual(
            list(_chunks(rows, size=10, max_payload_bytes=100)),
            [[rows[0]], [oversized], [rows[2]]],
        )

    def test_export_preserves_full_prompt_answer_and_parsed_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {
                "protocol_id": "E2-abcdef-v2", "run_scope": "nonformal_smoke",
                "created_at_utc": "2026-10-02T00:00:00+00:00",
                "config_sha256": "a" * 64, "formal_run_enabled_at_start": False,
                "trajectory_identity_axes": ["model", "case_id", "arm"],
                "replicate_axis": False,
            }
            (root / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            ledger = StructuredLedger(root / "ledger.sqlite3", manifest)
            dimensions = {
                "case_id": "B001", "task": "invalid_bug", "model": "model",
                "representation": "extracted", "arm": "F",
            }
            call_id = "model-call-1"
            ledger.reserve_call(call_id, "subject_model_generation", dimensions, {
                "message": "COMPLETE QUERY", "modelType": "model", "id": "",
            })
            ledger.finish_call(call_id, {
                "run_status": "ok", "raw_response": "COMPLETE ANSWER",
                "response_id": "response-1", "session_id": "session-1",
                "elapsed_ms": 123, "usage": {"total_tokens": 17},
                "provider_response": {"success": True, "data": {"chat": "COMPLETE ANSWER"}},
            })
            ledger.begin_trajectory("trajectory-1", dimensions)
            ledger.append_event("trajectory-1", {
                "event": "model_request", "turn": 1, "request_id": "request-1",
                "physical_request_id": call_id, "prompt_audit": {"analysis_visible": False},
                "attachment_sha256": None,
            })
            ledger.append_event("trajectory-1", {
                "event": "output_check", "turn": 1, "output_status": "valid",
                "parse_error": None,
            })
            ledger.append_event("trajectory-1", {
                "event": "execution", "turn": 1, "verdict": "fail",
            })
            ledger.finish_trajectory("trajectory-1", {
                "status": "completed_failure", "reason": "budget_exhausted_oracle_fail",
                "budget": 1, "model_calls": 1, "executions": 1, "recoveries": 0,
                "analyzer_calls": 0, "records": [{
                    "turn": 1, "request_id": "request-1", "raw_response": "COMPLETE ANSWER",
                    "candidate": {"field": "value"}, "outcome": "FAIL", "run_status": "ok",
                    "output_status": "valid", "parse_error": None, "oracle_version": "v1",
                    "visual_feedback": None,
                }],
            })
            ledger.close()

            rows = build_mysql_rows(root)
            inventory = mysql_inventory(root)
            self.assertEqual(inventory["turns"], 1)
            self.assertEqual(json.loads(rows["turns"][0][27]), "COMPLETE QUERY")
            self.assertEqual(json.loads(rows["turns"][0][28]), "COMPLETE ANSWER")
            self.assertEqual(json.loads(rows["turns"][0][29]), {"field": "value"})
            self.assertEqual(json.loads(rows["external_calls"][0][21]), "COMPLETE ANSWER")
            self.assertEqual(
                json.loads(rows["external_calls"][0][23])["data"]["chat"],
                "COMPLETE ANSWER",
            )
            self.assertNotIn("replicate", json.loads(rows["trajectories"][0][16]))
            self.assertEqual(json.loads(rows["turns"][0][33])["data"]["chat"], "COMPLETE ANSWER")
            self.assertEqual(json.loads(rows["turns"][0][34])["verdict"], "fail")

    def test_v2_export_rejects_replicate_dimension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {
                "protocol_id": "E2-abcdef-v2", "run_scope": "formal",
                "created_at_utc": "2026-10-02T00:00:00+00:00",
                "config_sha256": "a" * 64, "formal_run_enabled_at_start": True,
            }
            (root / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            ledger = StructuredLedger(root / "ledger.sqlite3", manifest)
            dimensions = {
                "case_id": "P001", "task": "valid_pptr", "model": "model",
                "replicate": 1, "representation": "extracted", "arm": "A",
            }
            ledger.begin_trajectory("trajectory-1", dimensions)
            ledger.finish_trajectory("trajectory-1", {"status": "completed_success", "records": []})
            ledger.close()
            with self.assertRaisesRegex(ValueError, "must not contain replicate"):
                build_mysql_rows(root)


if __name__ == "__main__":
    unittest.main()
