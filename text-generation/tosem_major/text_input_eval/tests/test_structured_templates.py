from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from text_input_eval.supplementary.structured_templates import render_feedback, sha256_json


def failed_record(turn: int = 1) -> dict:
    candidate = {"app:id/input": f"bad-{turn}"}
    request_id = f"request-{turn}"
    return {
        "turn": turn,
        "request_id": request_id,
        "candidate": candidate,
        "output_status": "valid",
        "behavior_verdict": False,
        "diagnostics": [
            {
                "type": "goal_not_observed",
                "detail": "The target behavior was not observed.",
                "source": "task_oracle",
                "public": True,
                "checker_id": "frozen_behavior_oracle",
                "evidence_ref": f"request:{request_id}:verdict",
            },
            {
                "type": "private",
                "detail": "PRIVATE_TRIGGER_MUST_NOT_LEAK",
                "source": "private_validator",
                "public": True,
                "checker_id": "hidden",
                "evidence_ref": f"request:{request_id}:private",
            },
        ],
    }


class StructuredTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = "BASE EXTRACTED PROMPT"
        self.required = ["app:id/input"]
        self.task = {"case_id": "P001", "goal": "Reach the frozen target page."}

    def test_a_isolation_and_b_c_information_equivalence(self) -> None:
        records = [failed_record(i) for i in range(1, 3)]
        a, a_audit = render_feedback(self.base, "A", records, self.required, self.task)
        b, b_audit = render_feedback(self.base, "B", records, self.required, self.task)
        c, c_audit = render_feedback(self.base, "C", records, self.required, self.task)
        self.assertEqual(a, self.base)
        self.assertEqual(a_audit["explicit_history_count"], 0)
        self.assertEqual(b_audit["shared_information"], c_audit["shared_information"])
        self.assertEqual(b_audit["shared_information_hash"], c_audit["shared_information_hash"])
        self.assertNotEqual(b, c)
        self.assertNotIn("PRIVATE_TRIGGER_MUST_NOT_LEAK", b)
        self.assertNotIn("PRIVATE_TRIGGER_MUST_NOT_LEAK", c)

    def test_history_window_and_truncation_are_shared(self) -> None:
        records = [failed_record(i) for i in range(1, 8)]
        records[-1]["candidate"] = {"app:id/input": "x" * 100}
        records[-1]["diagnostics"][0]["detail"] = "e" * 100
        _, b_audit = render_feedback(
            self.base, "B", records, self.required, self.task,
            history_limit=5, max_candidate_chars=20, max_public_error_chars=10,
        )
        _, c_audit = render_feedback(
            self.base, "C", records, self.required, self.task,
            history_limit=5, max_candidate_chars=20, max_public_error_chars=10,
        )
        history = b_audit["shared_information"]["failed_history"]
        self.assertEqual([row["turn"] for row in history], [3, 4, 5, 6, 7])
        self.assertTrue(history[-1]["candidate_truncated"])
        self.assertTrue(history[-1]["public_errors"][0]["detail_truncated"])
        self.assertEqual(b_audit["shared_information_hash"], c_audit["shared_information_hash"])

    def _evidence_fixture(self, root: Path) -> tuple[dict, dict]:
        record = failed_record()
        request_dir = root / record["request_id"]
        request_dir.mkdir(parents=True)
        xml = (
            '<hierarchy><node package="app" class="android.widget.TextView" '
            'resource-id="app:id/error" text="At least 8 characters" /></hierarchy>'
        ).encode()
        (request_dir / "after.xml").write_bytes(xml)
        record["observation"] = {
            "run_status": "ok",
            "behavior_verdict": False,
            "request_id": record["request_id"],
            "candidate_hash": sha256_json(record["candidate"]),
            "oracle_version": "oracle-v1",
            "evidence": [{"path": "after.xml", "sha256": hashlib.sha256(xml).hexdigest()}],
        }
        record["diagnostics"].append({
            "type": "observed_ui_message",
            "detail": "At least 8 characters",
            "source": "ui_observation",
            "public": True,
            "checker_id": "length-message",
            "evidence_ref": f"request:{record['request_id']}:after.xml",
        })
        targets = {
            "oracle_version": "oracle-v1",
            "targets": [{
                "id": "length-message",
                "selector": {
                    "package": "app",
                    "class": "android.widget.TextView",
                    "resource-id": "app:id/error",
                },
                "attribute": "text",
            }],
        }
        return record, targets

    def test_d_accepts_bound_ui_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record, targets = self._evidence_fixture(root)
            c, _ = render_feedback(self.base, "C", [record], self.required, self.task)
            d, audit = render_feedback(
                self.base, "D", [record], self.required, self.task,
                evidence_root=root, diagnostic_targets=targets,
            )
            self.assertTrue(d.startswith(c))
            self.assertFalse(audit["fallback_to_c"])
            self.assertEqual(len(audit["verified_evidence"]), 1)
            self.assertIn("At least 8 characters", d)

    def test_d_falls_back_exactly_to_c_for_stale_tampered_or_escaped_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record, targets = self._evidence_fixture(root)
            c, _ = render_feedback(self.base, "C", [record], self.required, self.task)
            mutations = []
            stale = copy.deepcopy(record)
            stale["observation"]["candidate_hash"] = "stale"
            mutations.append(stale)
            tampered = copy.deepcopy(record)
            (root / record["request_id"] / "after.xml").write_text("<hierarchy />")
            mutations.append(tampered)
            for bad in mutations:
                d, audit = render_feedback(
                    self.base, "D", [bad], self.required, self.task,
                    evidence_root=root, diagnostic_targets=targets,
                )
                self.assertEqual(d, c)
                self.assertTrue(audit["fallback_to_c"])
            record, targets = self._evidence_fixture(root / "fresh")
            record["diagnostics"][-1]["evidence_ref"] = f"request:{record['request_id']}:../outside.xml"
            d, audit = render_feedback(
                self.base, "D", [record], self.required, self.task,
                evidence_root=root / "fresh", diagnostic_targets=targets,
            )
            c, _ = render_feedback(self.base, "C", [record], self.required, self.task)
            self.assertEqual(d, c)
            self.assertTrue(audit["fallback_to_c"])

    def test_unknown_execution_cannot_enter_feedback(self) -> None:
        record = failed_record()
        record["behavior_verdict"] = None
        with self.assertRaises(ValueError):
            render_feedback(self.base, "C", [record], self.required, self.task)


if __name__ == "__main__":
    unittest.main()
