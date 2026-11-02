from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from text_input_eval.supplementary.abcdef_cli import (
    _audit_nonformal_smoke,
    _metric_report,
    _nonformal_smoke_blocking,
)
from text_input_eval.supplementary.structured_storage import StructuredLedger


class ABCDEFSmokeAuditTests(unittest.TestCase):
    def test_qualification_smoke_only_waives_circular_and_formal_gates(self) -> None:
        report = {
            "blocking_gates": [
                "mysql_analysis_mirror_qualification",
                "formal_run_switch",
                "f_analyzer_api_qualification",
            ]
        }
        self.assertEqual(
            _nonformal_smoke_blocking(report),
            {"f_analyzer_api_qualification"},
        )

    def _ledger(
        self, root: Path, *, f_analysis_ok: bool = True, early_stop: bool = False,
    ) -> StructuredLedger:
        ledger = StructuredLedger(root / "ledger.sqlite3", {"scope": "test-smoke"})
        common = {
            "protocol_id": "E2-abcdef-v2", "case_id": "B001",
            "task": "invalid_bug", "model": "model",
            "representation": "extracted",
        }
        for arm in "ABCDEF":
            trajectory_id = f"trajectory-{arm}"
            ledger.begin_trajectory(trajectory_id, {**common, "arm": arm})
            audit = {
                "visual_visible": arm in "DEF",
                "ui_observations_visible": arm in "EF",
                "analysis_visible": arm == "F",
            }
            if not early_stop:
                ledger.append_event(trajectory_id, {
                    "event": "model_request", "turn": 2, "prompt_audit": audit,
                    "attachment_sha256": "image-hash" if arm in "DEF" else None,
                })
            if arm == "F" and not early_stop:
                ledger.append_event(trajectory_id, {
                    "event": "feedback_analysis", "turn": 1,
                    "run_status": "ok" if f_analysis_ok else "analysis_invalid",
                    "image_sha256": "image-hash" if f_analysis_ok else None,
                })
            ledger.finish_trajectory(trajectory_id, {
                "status": "completed_failure" if arm != "F" or f_analysis_ok else "incomplete",
                "first_success_turn": 1 if early_stop else None,
            })
        return ledger

    def test_complete_a_to_f_smoke_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = self._ledger(root)
            ledger.close()
            report = _audit_nonformal_smoke(root)
            self.assertEqual(report["status"], "passed")
            self.assertEqual(report["failed_gates"], [])
            self.assertTrue((root / "smoke_acceptance_report.json").is_file())

    def test_incomplete_f_analysis_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = self._ledger(root, f_analysis_ok=False)
            ledger.close()
            report = _audit_nonformal_smoke(root)
            self.assertEqual(report["status"], "failed")
            self.assertIn("no_incomplete_results", report["failed_gates"])
            self.assertIn("f_analyzer_succeeded_with_image_or_early_stopped", report["failed_gates"])

    def test_success_at_turn_one_is_valid_smoke_early_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = self._ledger(root, early_stop=True)
            for trajectory_id in ledger.completed_trajectory_ids():
                row = ledger.trajectory(trajectory_id)
                result = {**row["result"], "status": "completed_success", "first_success_turn": 1}
                # StructuredLedger is append-only; use the fixture database only to model the
                # already-finished early-stop result emitted by the runner.
                ledger.db.execute(
                    "UPDATE trajectories SET result=? WHERE id=?",
                    (json.dumps(result), trajectory_id),
                )
            ledger.db.commit()
            ledger.close()
            report = _audit_nonformal_smoke(root)
            self.assertEqual(report["status"], "passed")

    def test_metric_report_emits_full_curves_and_excludes_infrastructure(self) -> None:
        trajectories = [
            {
                "id": "page-success", "state": "done",
                "dimensions": {"case_id": "P001", "task": "valid_pptr", "arm": "A"},
                "result": {
                    "status": "completed_success", "reason": "oracle_pass", "budget": 3,
                    "model_calls": 2, "elapsed_ms": 200,
                    "records": [{"turn": 1, "outcome": "FAIL"}, {"turn": 2, "outcome": "PASS"}],
                },
            },
            {
                "id": "page-failure", "state": "done",
                "dimensions": {"case_id": "P001", "task": "valid_pptr", "arm": "B"},
                "result": {
                    "status": "completed_failure", "reason": "budget_exhausted_oracle_fail",
                    "budget": 3, "model_calls": 3, "elapsed_ms": 300,
                    "records": [{"turn": turn, "outcome": "FAIL"} for turn in range(1, 4)],
                },
            },
            {
                "id": "bug-success", "state": "done",
                "dimensions": {"case_id": "B001", "task": "invalid_bug", "arm": "A"},
                "result": {
                    "status": "completed_success", "reason": "oracle_pass", "budget": 30,
                    "model_calls": 29, "elapsed_ms": 2900,
                    "records": [
                        {"turn": turn, "outcome": "PASS" if turn == 29 else "FAIL"}
                        for turn in range(1, 30)
                    ],
                },
            },
            {
                "id": "infra", "state": "done",
                "dimensions": {"case_id": "B002", "task": "invalid_bug", "arm": "A"},
                "result": {
                    "status": "incomplete", "reason": "subject_model_infrastructure_error",
                    "budget": 30, "model_calls": 1, "elapsed_ms": 10, "records": [],
                },
            },
        ]
        report = _metric_report(trajectories, [])
        self.assertEqual(list(report["valid_pptr"]["full_curve"]), ["1", "2", "3"])
        self.assertEqual(len(report["invalid_bug"]["full_curve"]), 30)
        self.assertEqual(report["valid_pptr"]["full_curve"]["2"]["successes"], 1)
        self.assertEqual(report["valid_pptr"]["full_curve"]["2"]["new_at_k"], 1)
        self.assertEqual(report["invalid_bug"]["full_curve"]["29"]["successes"], 1)
        self.assertEqual(report["invalid_bug"]["full_curve"]["30"]["new_at_k"], 0)
        self.assertEqual(report["invalid_bug"]["admissible_trajectories"], 1)
        self.assertEqual(len(report["invalid_bug"]["excluded_nonsemantic_or_audit_invalid"]), 1)


if __name__ == "__main__":
    unittest.main()
