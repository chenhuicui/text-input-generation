from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from text_input_eval.supplementary.abcdef_contract import sha256_json
from text_input_eval.supplementary.abcdef_templates import (
    render_feedback,
    validate_analyzer_diagnosis,
)


def diagnosis(outcome: str = "FAIL") -> dict:
    return {
        "execution_status": outcome,
        "diagnosis_category": "visible_rejection",
        "diagnosis": "The submitted value remained on the same screen and an error was visible.",
        "support_level": "direct",
        "temporal_summary": "The error appeared after submission and remained in the settled frame.",
        "evidence_refs": ["request:r1:frame_001.png"],
        "alternative_explanations": ["A transient network response could produce the same display."],
        "uncertainty": "The server response body is not visible.",
        "recommendation": None,
    }


class ABCDEFTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        request = self.root / "r1"
        request.mkdir()
        composite = request / "feedback_frames.png"
        Image.new("RGB", (4, 4), "white").save(composite)
        observation = request / "ui_observations.json"
        candidate = {"field": "bad"}
        value = {
            "schema_version": "e2-abcdef-v1-ui-observations-1",
            "request_id": "r1",
            "candidate_sha256": sha256_json(candidate),
            "oracle_version": "oracle-v1",
            "observations": {
                "navigation_observed": {"changed": False},
                "screen_before": "pre_action",
                "screen_after": "frame_004",
                "added_visible_text": ["Invalid value"],
                "removed_visible_text": [],
                "public_error_messages": ["Invalid value"],
                "dialog_or_toast": [],
                "field_state_changes": [],
                "timeline": [],
            },
        }
        observation.write_text(json.dumps(value), encoding="utf-8")
        self.record = {
            "turn": 1,
            "request_id": "r1",
            "raw_response": '{"field":"bad"}',
            "candidate": candidate,
            "outcome": "FAIL",
            "run_status": "ok",
            "oracle_version": "oracle-v1",
            "visual_feedback": {
                "request_id": "r1",
                "candidate_sha256": sha256_json(candidate),
                "oracle_version": "oracle-v1",
                "composite_path": "r1/feedback_frames.png",
                "composite_sha256": hashlib.sha256(composite.read_bytes()).hexdigest(),
                "observation_path": "r1/ui_observations.json",
                "observation_sha256": hashlib.sha256(observation.read_bytes()).hexdigest(),
                "frames": [
                    {"role": "before", "evidence_ref": "request:r1:pre.png"},
                    {"role": "first_changed", "evidence_ref": "request:r1:first.png"},
                    {"role": "transient_message_peak", "evidence_ref": "request:r1:peak.png"},
                    {"role": "settled", "evidence_ref": "request:r1:last.png"},
                ],
            },
        }

    def test_information_is_monotone_and_arm_specific(self) -> None:
        outputs = {}
        for arm in "ABCDEF":
            outputs[arm] = render_feedback(
                "BASE",
                arm,
                [self.record],
                evidence_root=self.root,
                analyzer_diagnosis=diagnosis() if arm == "F" else None,
            )
            self.assertNotIn("sha256", outputs[arm][0].lower())
            self.assertNotIn("oracle_version", outputs[arm][0])
            self.assertNotIn("candidate_sha256", outputs[arm][0])
        self.assertEqual(outputs["A"][0], "BASE")
        self.assertIn("previous_candidates", outputs["B"][0])
        self.assertNotIn('"status"', outputs["B"][0])
        self.assertNotIn("sha256", outputs["B"][0].lower())
        self.assertIn('"status": "FAIL"', outputs["C"][0])
        self.assertNotIn("visual_execution_evidence", outputs["C"][0])
        self.assertEqual(outputs["D"][2][0].name, "feedback_frames.png")
        self.assertIn("visual_execution_evidence", outputs["D"][0])
        self.assertNotIn("sha256", outputs["D"][0].lower())
        self.assertNotIn("observed_ui_changes", outputs["D"][0])
        self.assertIn("Invalid value", outputs["E"][0])
        self.assertNotIn("evidence_based_diagnosis", outputs["E"][0])
        self.assertIn("evidence_based_diagnosis", outputs["F"][0])
        self.assertTrue(outputs["F"][1]["analysis_visible"])

    def test_tampered_visual_is_not_attached(self) -> None:
        (self.root / "r1" / "feedback_frames.png").write_bytes(b"not a png")
        prompt, audit, attachments = render_feedback(
            "BASE", "D", [self.record], evidence_root=self.root
        )
        self.assertEqual(attachments, [])
        self.assertFalse(audit["visual_visible"])
        self.assertIn('"attached": false', prompt)

    def test_visual_observation_oracle_is_separate_from_verdict_oracle(self) -> None:
        self.record["oracle_version"] = "predicate-v1"
        self.record["verdict_oracle_version"] = "predicate-v1"
        self.record["visual_observation_oracle_version"] = "oracle-v1"
        prompt, audit, attachments = render_feedback(
            "BASE", "E", [self.record], evidence_root=self.root
        )
        self.assertEqual(attachments[0].name, "feedback_frames.png")
        self.assertTrue(audit["visual_visible"])
        self.assertTrue(audit["ui_observations_visible"])
        self.assertIn("Invalid value", prompt)
        self.assertNotIn("predicate-v1", prompt)

    def test_f_rejects_advice_and_outcome_drift(self) -> None:
        advice = diagnosis()
        advice["diagnosis"] = "You should try another input."
        with self.assertRaisesRegex(ValueError, "advice"):
            render_feedback(
                "BASE", "F", [self.record], evidence_root=self.root,
                analyzer_diagnosis=advice,
            )
        drift = diagnosis("PASS")
        with self.assertRaisesRegex(ValueError, "changed"):
            render_feedback(
                "BASE", "F", [self.record], evidence_root=self.root,
                analyzer_diagnosis=drift,
            )

    def test_f_advice_filter_does_not_reject_entry_substring(self) -> None:
        value = diagnosis()
        value["diagnosis"] = "The text-entry screen remained visible after submission."
        self.assertIs(validate_analyzer_diagnosis(value, outcome="FAIL"), value)
        value["diagnosis"] = "Try another input."
        with self.assertRaisesRegex(ValueError, "advice"):
            validate_analyzer_diagnosis(value, outcome="FAIL")


if __name__ == "__main__":
    unittest.main()
