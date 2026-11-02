from __future__ import annotations

import json
import unittest
from pathlib import Path

from text_input_eval.supplementary.structured_preflight import REPO_ROOT, build_registry
from text_input_eval.supplementary.structured_oracle_audit import build as build_oracle_audit
from text_input_eval.supplementary.structured_cli import load_oracles, validate_oracles


class StructuredPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config_path = REPO_ROOT / "configs" / "supplementary" / "e2_structure_v1.json"
        cls.registry, cls.preflight = build_registry(cls.config_path)

    def test_complete_ordered_case_universe_and_hashes(self) -> None:
        rows = self.registry["cases"]
        self.assertEqual(len(rows), 152)
        self.assertEqual([row["case_id"] for row in rows[:3]], ["P001", "P002", "P003"])
        self.assertEqual([row["case_id"] for row in rows[-3:]], ["B035", "B036", "B037"])
        self.assertTrue(all(len(row["extracted_prompt_sha256"]) == 64 for row in rows))
        self.assertTrue(all(len(row["source_files"]["cached_xml_sha256"]) == 64 for row in rows))

    def test_explicit_applicability_partition_is_formal_ready(self) -> None:
        counts = self.registry["counts"]
        config = json.loads(self.config_path.read_text())
        oracle_path = (
            self.config_path.parent / config["runtime"]["oracle_manifest"]
        ).resolve()
        promoted = len(json.loads(oracle_path.read_text())["cases"])
        self.assertEqual(counts["valid_pptr"], 115)
        self.assertEqual(counts["invalid_bug"], 37)
        self.assertEqual(promoted, 149)
        self.assertEqual(counts["formal_applicable"], 149)
        self.assertEqual(counts["formal_excluded"], 3)
        self.assertEqual(counts["behavior_oracle_ready"], promoted)
        self.assertEqual(counts["within_attempt_recovery_ready"], promoted)
        self.assertEqual(counts["formal_run_ready"], promoted)
        self.assertGreaterEqual(counts["independent_attempt_reset_ready"], promoted)
        self.assertFalse(self.preflight["formal_run_allowed"])
        self.assertEqual(
            self.preflight["blocking_gates"],
            ["model_qualification", "formal_run_enabled_false"],
        )
        excluded = {
            row["case_id"] for row in self.registry["cases"]
            if not row["formal_applicability"]["in_scope"]
        }
        self.assertEqual(excluded, {"P031", "P082", "P104"})

    def test_oracle_gap_ledger_respects_promoted_formal_oracles(self) -> None:
        report = build_oracle_audit(self.registry)
        promoted = self.registry["counts"]["formal_run_ready"]
        self.assertEqual(report["summary"]["formal_oracle_ready"], promoted)
        ready_rows = [row for row in report["cases"] if row["formal_oracle_ready"]]
        self.assertEqual(len(ready_rows), promoted)
        self.assertTrue(all(not row["gaps"] for row in ready_rows))

    def test_formal_oracle_manifest_matches_applicable_partition(self) -> None:
        config = json.loads(self.config_path.read_text())
        oracle_path = (
            self.config_path.parent / config["runtime"]["oracle_manifest"]
        ).resolve()
        oracles = load_oracles(oracle_path)

        validate_oracles(
            self.registry, oracles,
            set(config.get("manual_binding_review_cases", [])),
        )

        self.assertEqual(len(oracles), 149)

    def test_scale_for_extracted_primary(self) -> None:
        scale = self.preflight["planned_scale_if_primary_extracted_is_frozen"]
        self.assertEqual(scale["paired_blocks"], 1341)
        self.assertEqual(scale["arm_trajectories"], 5364)
        self.assertEqual(scale["model_request_upper_bound_with_shared_first_generation"], 48033)
        self.assertEqual(scale["logical_candidate_execution_slots_upper_bound"], 52056)

    def test_representation_and_formal_run_are_frozen(self) -> None:
        config = json.loads(self.config_path.read_text())
        self.assertEqual(config["representation"]["primary"], "extracted")
        self.assertEqual(config["representation"]["decision_status"], "frozen")
        self.assertFalse(config["formal_run_enabled"])


if __name__ == "__main__":
    unittest.main()
