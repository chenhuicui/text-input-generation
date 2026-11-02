from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from text_input_eval.supplementary.spec_feasibility_android import (
    _crash_signature,
    adjudicate_observation,
)
from text_input_eval.supplementary.spec_feasibility_contract import (
    candidate_partition,
    parse_candidate,
    render_prompt,
    request_cost_usd,
    validate_config,
)
from text_input_eval.supplementary.spec_feasibility_pilot import (
    _frozen_baselines,
    _schedule,
    build_summary,
    local_preflight,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "configs" / "supplementary" / "spec_feasibility_pilot_v1.json"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


class SpecificationFeasibilityContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.cases = {case["case_id"]: case for case in self.config["cases"]}

    def test_draft_config_is_structurally_valid_but_not_live(self) -> None:
        draft_config = load_config()
        draft_config["protocol_status"] = "draft_unfrozen"
        draft_config["live_run_enabled"] = False
        validate_config(draft_config)
        with self.assertRaisesRegex(ValueError, "frozen"):
            validate_config(draft_config, require_live=True)

    def test_b014_partition_is_source_condition_not_app_verdict(self) -> None:
        case = self.cases["B014"]
        key = case["input"]["resource_id"]
        self.assertEqual(candidate_partition(case, {key: 'a"b'}), "TARGET")
        self.assertEqual(candidate_partition(case, {key: "-test"}), "TARGET")
        self.assertEqual(candidate_partition(case, {key: "- test"}), "CONTROL")
        self.assertEqual(candidate_partition(case, {key: "ordinary query"}), "CONTROL")
        self.assertEqual(candidate_partition(case, {key: "  "}), "OUTSIDE")

    def test_b023_partition_accepts_final_empty_multidigit_item(self) -> None:
        case = self.cases["B023"]
        key = case["input"]["resource_id"]
        self.assertEqual(candidate_partition(case, {key: "10."}), "TARGET")
        self.assertEqual(candidate_partition(case, {key: "intro\n123.  \n"}), "TARGET")
        self.assertEqual(candidate_partition(case, {key: "intro\r\n123.\r\n"}), "TARGET")
        self.assertEqual(candidate_partition(case, {key: "9."}), "CONTROL")
        self.assertEqual(candidate_partition(case, {key: "10. text"}), "CONTROL")
        self.assertEqual(candidate_partition(case, {key: "10.\nmore"}), "CONTROL")

    def test_candidate_parser_reuses_extracted_prompt_fence_contract(self) -> None:
        case = self.cases["B014"]
        key = case["input"]["resource_id"]
        self.assertEqual(parse_candidate(json.dumps({key: '"'}), case), {key: '"'})
        self.assertEqual(
            parse_candidate(
                f"```json\n{json.dumps({key: 'x'})}\n```\nStrategy: boundary input.",
                case,
            ),
            {key: "x"},
        )
        with self.assertRaises(ValueError):
            parse_candidate(json.dumps({key: "x", "extra": "y"}), case)

    def test_candidate_parser_rejects_unpaired_unicode_surrogate(self) -> None:
        case = self.cases["B023"]
        key = case["input"]["resource_id"]
        raw = '{"' + key + '": "\\uD800"}'
        with self.assertRaisesRegex(ValueError, "unpaired Unicode surrogate"):
            parse_candidate(raw, case)

    def test_candidate_parser_allows_markdown_fence_inside_json_string(self) -> None:
        case = self.cases["B023"]
        key = case["input"]["resource_id"]
        candidate = "# malformed\n```python\ndef broken(:\n"
        raw = (
            "Strategy: stress an incomplete Markdown code block.\n"
            "```json\n"
            + json.dumps({key: candidate})
            + "\n```\nExplanation: the value contains an unclosed code fence."
        )
        self.assertEqual(parse_candidate(raw, case), {key: candidate})

    def test_candidate_parser_still_rejects_multiple_json_candidate_blocks(self) -> None:
        case = self.cases["B023"]
        key = case["input"]["resource_id"]
        raw = (
            "```json\n" + json.dumps({key: "first"}) + "\n```\n"
            "```json\n" + json.dumps({key: "second"}) + "\n```"
        )
        with self.assertRaisesRegex(ValueError, "exactly one JSON code fence"):
            parse_candidate(raw, case)

    def test_prompt_arms_expose_only_the_predeclared_information(self) -> None:
        case = self.cases["B014"]
        base_prompt = _frozen_baselines(CONFIG_PATH, self.config)["B014"]
        key = case["input"]["resource_id"]
        previous = [{
            "turn": 1,
            "candidate": {key: '"'},
            "candidate_target_partition": "TARGET",
            "o_spec": "FAIL",
            "public_observations": ["A candidate-bound process crash was observed."],
            "unknown_reason": None,
        }]
        p0, audit0 = render_prompt(case, "P0", previous, base_prompt)
        p1, audit1 = render_prompt(case, "P1", previous, base_prompt)
        p2, audit2 = render_prompt(case, "P2", previous, base_prompt)
        self.assertEqual(p0, base_prompt)
        self.assertTrue(p1.startswith(base_prompt))
        self.assertTrue(p2.startswith(base_prompt))
        self.assertNotIn("supplied_requirement_contract", p0)
        self.assertNotIn("previous_attempt_specification_feedback", p0)
        self.assertIn("supplied_requirement_contract", p1)
        self.assertNotIn("previous_attempt_specification_feedback", p1)
        self.assertIn("supplied_requirement_contract", p2)
        self.assertIn("previous_attempt_specification_feedback", p2)
        self.assertFalse(audit0["requirement_visible"])
        self.assertTrue(audit0["baseline_is_exact_prompt"])
        self.assertEqual(audit0["history_count"], 0)
        self.assertTrue(audit1["requirement_visible"])
        self.assertEqual(audit1["history_count"], 0)
        self.assertTrue(audit2["specification_feedback_visible"])
        self.assertEqual(audit2["history_count"], 1)

    def test_generalized_contract_hides_issue_trigger_and_partition_feedback(self) -> None:
        case = json.loads(json.dumps(self.cases["B014"]))
        case["generation_contract"] = {
            "contract_id": "B014-search-robustness-smoke-v1",
            "input_domain": "Any text accepted by the search field.",
            "required_behavior": ["The application remains running and responsive."],
        }
        base_prompt = _frozen_baselines(CONFIG_PATH, self.config)["B014"]
        key = case["input"]["resource_id"]
        previous = [{
            "turn": 1,
            "candidate": {key: "ordinary query"},
            "usable_response": True,
            "candidate_target_partition": "CONTROL",
            "o_spec": "PASS",
            "public_observations": ["The application remained foreground."],
            "unknown_reason": None,
        }]
        p1, audit1 = render_prompt(case, "P1", [], base_prompt)
        p2, audit2 = render_prompt(case, "P2", previous, base_prompt)
        self.assertIn("Any text accepted by the search field.", p1)
        self.assertNotIn(case["requirement"]["input_condition"], p1)
        self.assertNotIn("candidate_target_partition", p2)
        self.assertIn("hidden bug-trigger partition is not disclosed", p2)
        self.assertFalse(audit1["exact_target_condition_visible"])
        self.assertFalse(audit2["target_partition_feedback_visible"])

    def test_frozen_baselines_are_the_original_b014_and_b023_extracted_prompts(self) -> None:
        baselines = _frozen_baselines(CONFIG_PATH, self.config)
        self.assertIn("This is an app named wallabag", baselines["B014"])
        self.assertIn("Please generate invalid text input", baselines["B014"])
        self.assertIn("This is an app named Nextcloud Note", baselines["B023"])
        self.assertIn("Explain your value selection strategy step-by-step.", baselines["B023"])

    def test_cost_requires_explicit_usage(self) -> None:
        self.assertAlmostEqual(
            request_cost_usd(
                {"prompt_tokens": 1000, "completion_tokens": 500},
                {"input": 2.0, "output": 6.0},
            ),
            0.005,
        )
        with self.assertRaises(ValueError):
            request_cost_usd({}, {"input": 2.0, "output": 6.0})


class SpecificationOracleTests(unittest.TestCase):
    def base_observation(self) -> dict:
        return {
            "package": "example.app",
            "initial_pid": 10,
            "final_pid": 10,
            "foreground_package": "example.app",
            "crash_evidence": False,
            "action_completed": True,
            "capture_complete": True,
        }

    def test_crash_is_fail(self) -> None:
        observation = self.base_observation()
        observation.update(crash_evidence=True, final_pid=None)
        self.assertEqual(adjudicate_observation(observation)["o_spec"], "FAIL")

    def test_same_live_process_is_pass_for_local_no_crash_obligation(self) -> None:
        self.assertEqual(adjudicate_observation(self.base_observation())["o_spec"], "PASS")

    def test_process_change_without_crash_evidence_is_unknown(self) -> None:
        observation = self.base_observation()
        observation["final_pid"] = 11
        result = adjudicate_observation(observation)
        self.assertEqual(result["o_spec"], "UNKNOWN")
        self.assertIn("without a corroborating crash", result["unknown_reason"])

    def test_crash_signature_is_bound_to_the_aut_event_neighborhood(self) -> None:
        package = "example.app"
        matching = "FATAL EXCEPTION: main\nProcess: example.app, PID: 42\n"
        unrelated = (
            "example.app normal activity message\n"
            + ("x" * 5000)
            + "FATAL EXCEPTION: main\nProcess: other.app, PID: 99\n"
        )
        self.assertTrue(_crash_signature(matching, package))
        self.assertFalse(_crash_signature(unrelated, package))


class SpecificationPilotPreflightAndReportTests(unittest.TestCase):
    def test_repeat50_schedule_contains_independent_trajectory_ids(self) -> None:
        config = load_config()
        schedule = _schedule(config)
        self.assertEqual(len(schedule), 300)
        identities = {
            (row["case_id"], row["arm"], row["replicate_id"])
            for row in schedule
        }
        self.assertEqual(len(identities), 300)
        self.assertEqual(
            {row["replicate_id"] for row in schedule},
            set(range(1, 51)),
        )

    def test_four_b014_shards_are_disjoint_and_complete(self) -> None:
        schedule = [
            row for row in _schedule(load_config())
            if row["case_id"] == "B014"
        ]
        shards = [
            [row for index, row in enumerate(schedule) if index % 4 == shard]
            for shard in range(4)
        ]
        identities = [
            (row["case_id"], row["arm"], row["replicate_id"])
            for shard in shards for row in shard
        ]
        self.assertEqual(len(identities), 150)
        self.assertEqual(len(set(identities)), 150)
        self.assertEqual(sorted(map(len, shards)), [37, 37, 38, 38])

    def test_draft_preflight_fails_closed_without_external_calls(self) -> None:
        draft_config = load_config()
        draft_config["protocol_status"] = "draft_unfrozen"
        draft_config["live_run_enabled"] = False
        draft_config["model"]["qualification_receipt"] = None
        draft_config["model"]["qualification_receipt_sha256"] = None
        report = local_preflight(CONFIG_PATH, draft_config)
        self.assertFalse(report["ready_for_live_run"])
        self.assertGreater(report["blocker_count"], 0)
        gates = {row["gate"]: row for row in report["gates"]}
        self.assertEqual(gates["protocol_frozen_and_enabled"]["status"], "block")
        self.assertEqual(gates["model_qualification_receipt"]["status"], "block")
        self.assertEqual(gates["B014:apk"]["status"], "pass")
        self.assertEqual(gates["B014:reset_command_frozen"]["status"], "pass")
        self.assertEqual(gates["B014:runtime_qualification_receipt"]["status"], "pass")
        self.assertEqual(gates["B014:runtime_qualification_contract_matches"]["status"], "pass")
        self.assertEqual(gates["B023:apk"]["status"], "pass")
        self.assertEqual(gates["B023:reset_command_frozen"]["status"], "pass")
        self.assertEqual(gates["B023:runtime_qualification_receipt"]["status"], "pass")
        self.assertEqual(gates["B023:runtime_qualification_contract_matches"]["status"], "pass")
        self.assertIn("no provider, ADB, App, or Docker", report["read_only_scope"])

    def test_summary_keeps_partition_and_o_spec_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = [
                {
                    "case_id": "B014",
                    "arm": "P0",
                    "replicate_id": 1,
                    "turn": 1,
                    "paid_model_request": True,
                    "usable_response": True,
                    "candidate_target_partition": "TARGET",
                    "o_spec": "PASS",
                    "request_cost_usd": 0.01,
                },
                {
                    "case_id": "B014",
                    "arm": "P0",
                    "replicate_id": 1,
                    "turn": 2,
                    "paid_model_request": True,
                    "usable_response": True,
                    "candidate_target_partition": "TARGET",
                    "o_spec": "FAIL",
                    "request_cost_usd": 0.01,
                },
            ]
            for record in records:
                path = (
                    root / "trajectories" / record["case_id"] / record["arm"]
                    / f"replicate_{record['replicate_id']:03d}"
                    / f"turn_{record['turn']:02d}" / "attempt_record.json"
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(record), encoding="utf-8")
            summary = build_summary(root)
        self.assertEqual(summary["targeted_candidate_at_1"], 1)
        self.assertEqual(summary["violation_detected_at_1"], 0)
        self.assertEqual(summary["violation_detected_at_3"], 1)
        self.assertEqual(summary["o_spec_counts"], {"PASS": 1, "FAIL": 1, "UNKNOWN": 0})


if __name__ == "__main__":
    unittest.main()
