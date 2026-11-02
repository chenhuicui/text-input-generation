from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from text_input_eval.supplementary.spec_feasibility_contract import (
    candidate_partition,
    parse_candidate,
    render_prompt,
    required_resource_ids,
    validate_config,
)
from text_input_eval.supplementary.spec_feasibility_pilot import _frozen_baselines
from text_input_eval.supplementary.spec_source_backed_smoke import _prompt_isolation


REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = (
    REPO_ROOT / "configs" / "supplementary" / "spec_source_backed_smoke_v1.json"
)


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


class SourceBackedSpecificationSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.cases = {case["case_id"]: case for case in self.config["cases"]}

    def test_config_is_valid_but_cannot_launch_a_paid_run(self) -> None:
        validate_config(self.config)
        self.assertFalse(self.config["live_run_enabled"])
        self.assertFalse(self.config["scope"]["paid_model_calls_authorized"])
        with self.assertRaisesRegex(ValueError, "frozen"):
            validate_config(self.config, require_live=True)

    def test_original_multi_field_prompts_are_preserved(self) -> None:
        baselines = _frozen_baselines(CONFIG_PATH, self.config)
        for case_id in ("B002", "B018"):
            for resource_id in required_resource_ids(self.cases[case_id]):
                self.assertIn(resource_id, baselines[case_id])
        self.assertIn("app named Debitum", baselines["B002"])
        self.assertIn("app named Masked-EditText", baselines["B018"])

    def test_multi_field_parser_requires_every_original_id(self) -> None:
        for case_id in ("B002", "B018"):
            case = self.cases[case_id]
            candidate = case["hidden_smoke_fixtures"]["control"]
            self.assertEqual(parse_candidate(json.dumps(candidate), case), candidate)
            incomplete = dict(candidate)
            incomplete.pop(next(iter(incomplete)))
            with self.assertRaises(ValueError):
                parse_candidate(json.dumps(incomplete), case)

    def test_hidden_partitions_reuse_frozen_candidate_semantics(self) -> None:
        for case_id in ("B002", "B018"):
            case = self.cases[case_id]
            fixtures = case["hidden_smoke_fixtures"]
            self.assertEqual(candidate_partition(case, fixtures["control"]), "CONTROL")
            self.assertEqual(candidate_partition(case, fixtures["target"]), "TARGET")

    def test_public_specification_never_exposes_hidden_trigger_or_issue(self) -> None:
        baselines = _frozen_baselines(CONFIG_PATH, self.config)
        for case in self.config["cases"]:
            prior = [{
                "turn": 1,
                "candidate": case["hidden_smoke_fixtures"]["control"],
                "usable_response": True,
                "candidate_target_partition": "CONTROL",
                "o_spec": "PASS",
                "public_observations": ["The App remained alive and foreground."],
                "unknown_reason": None,
            }]
            p1, audit1 = render_prompt(case, "P1", [], baselines[case["case_id"]])
            p2, audit2 = render_prompt(case, "P2", prior, baselines[case["case_id"]])
            for prompt in (p1, p2):
                self.assertNotIn(case["requirement"]["input_condition"], prompt)
                self.assertNotIn(case["hidden_oracle_provenance"]["url"], prompt)
            self.assertNotIn("candidate_target_partition", p2)
            self.assertFalse(audit1["exact_target_condition_visible"])
            self.assertFalse(audit2["target_partition_feedback_visible"])

    def test_materialized_prompt_isolation_report_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = _prompt_isolation(
                CONFIG_PATH,
                self.config,
                Path(directory),
            )
        self.assertTrue(report["passed"])
        self.assertEqual(
            {row["case_id"] for row in report["cases"]},
            {"B002", "B018"},
        )


if __name__ == "__main__":
    unittest.main()
