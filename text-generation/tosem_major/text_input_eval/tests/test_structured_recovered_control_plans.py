from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from text_input_eval.supplementary.structured_recovered_control_plans import (
    RECOVERED_CONTROLS,
    recover_plan,
)
from text_input_eval.supplementary.structured_reviewed_control_plans import source_plan


class StructuredRecoveredControlPlanTests(unittest.TestCase):
    def test_recovery_replaces_generic_candidates_and_records_limits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "P010"
            root.mkdir()
            base = {
                "case_id": "P010",
                "positive_control": {"title": "a", "description": ""},
                "negative_control": {"title": "", "description": ""},
                "verify_action": [{"action": "wait", "seconds": 1}],
            }
            (root / "control_plan_strong_negative.json").write_text(
                json.dumps(base), encoding="utf-8",
            )
            recovered = recover_plan(root, RECOVERED_CONTROLS["P010"])
            self.assertEqual(
                recovered["positive_control"]["co.fable.fable:id/list_title_edit_text"],
                "Sci-Fi Masterpieces",
            )
            provenance = recovered["recovery_provenance"]
            self.assertFalse(provenance["exact_original_file_recovered"])
            self.assertTrue(provenance["live_controls_required"])
            self.assertEqual(provenance["evidence_kind"], "control_result")

    def test_recovered_plan_is_first_review_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("control_plan_strong_negative.json", "control_plan_recovered.json"):
                (root / name).write_text("{}", encoding="utf-8")
            selected, used_fallback = source_plan(
                root, "lost_historical_plan.json", allow_recovery_fallback=True,
            )
            self.assertEqual(selected.name, "control_plan_recovered.json")
            self.assertTrue(used_fallback)

    def test_recovered_plan_supersedes_a_stale_expected_intermediate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "historical_name.json").write_text("{}", encoding="utf-8")
            (root / "control_plan_recovered.json").write_text("{}", encoding="utf-8")
            selected, used_fallback = source_plan(
                root, "historical_name.json", allow_recovery_fallback=True,
            )
            self.assertEqual(selected.name, "control_plan_recovered.json")
            self.assertTrue(used_fallback)

    def test_all_pairs_have_identical_keys_when_explicit(self) -> None:
        for case_id, recovery in RECOVERED_CONTROLS.items():
            if "positive_control" not in recovery:
                continue
            self.assertEqual(
                set(recovery["positive_control"]),
                set(recovery["negative_control"]),
                case_id,
            )

    def test_p043_uses_independent_fresh_client_remote_reset(self) -> None:
        overrides = RECOVERED_CONTROLS["P043"]["plan_overrides"]
        self.assertEqual(overrides["network_mode"], "online")
        self.assertEqual(overrides["remote_baseline_status"], "verified")
        self.assertEqual(
            overrides["case_specific_reset"]["reset_id"],
            "isharing:P043:fresh-client-phone:v1",
        )

    def test_p063_freezes_transient_local_clear_affordance(self) -> None:
        recovery = RECOVERED_CONTROLS["P063"]
        self.assertEqual(next(iter(recovery["negative_control"].values())), "")
        self.assertTrue(
            recovery["plan_overrides"]["time_sensitive_local_observation"]
        )
        self.assertEqual(recovery["plan_overrides"]["network_mode"], "offline")
        self.assertEqual(
            recovery["plan_overrides"]["goal_assertion"]["targets"][0]["selector"]["resource-id"],
            "com.olo.ihop:id/pickUpSearchBarClearIcon",
        )

    def test_p112_uses_distinct_search_result_controls(self) -> None:
        recovery = RECOVERED_CONTROLS["P112"]
        self.assertEqual(
            next(iter(recovery["positive_control"].values())), "news",
        )
        self.assertEqual(
            next(iter(recovery["negative_control"].values())),
            "zzqxjv84726391unfindable",
        )
        self.assertEqual(
            recovery["plan_overrides"]["goal_assertion"]["targets"][0]["selector"]["resource-id"],
            "sanity.podcast.freak:id/cover",
        )


if __name__ == "__main__":
    unittest.main()
