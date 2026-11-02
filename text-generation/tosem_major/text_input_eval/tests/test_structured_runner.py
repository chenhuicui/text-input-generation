from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from text_input_eval.supplementary.structured_runner import StructuredRunner, exact_json_candidate
from text_input_eval.supplementary.structured_storage import (
    StructuredLedger,
    canonical_json as storage_canonical_json,
)
from text_input_eval.supplementary.structured_templates import canonical_json as template_canonical_json


SELECTOR = {"package": "example.app", "class": "android.widget.TextView", "resource-id": "message"}


class FakeGateway:
    def __init__(self, first: str = '{"field":"bad"}') -> None:
        self.first = first
        self.calls: list[tuple[str, str]] = []

    def generate(self, model: dict, prompt: str) -> dict:
        self.calls.append((model["id"], prompt))
        raw = self.first if len(self.calls) == 1 else '{"field":"good"}'
        return {"run_status": "ok", "raw_response": raw, "capacity": {"prompt_bytes": len(prompt.encode())}}


class FakeEnvironment:
    def __init__(self) -> None:
        self.states: dict[str, str] = {}
        self.begins = 0
        self.executes = 0
        self.recovers = 0

    def begin_attempt(self, case: dict, attempt: dict) -> dict:
        self.begins += 1
        attempt_id = attempt["attempt_id"]
        state = f"initial:{attempt_id}"
        self.states[attempt_id] = state
        return {
            "reset_verified": True,
            "target_page_verified": True,
            "remote_baseline": "not_applicable",
            "reset_id": "reset-v1",
            "state_hash": state,
        }

    def execute_turn(self, candidate: dict[str, str], request: dict) -> dict:
        self.executes += 1
        attempt_id = Path(request["evidence_dir"]).parent.name
        before = self.states[attempt_id]
        passed = candidate["field"] == "good"
        after = f"{'pass' if passed else 'fail'}:{attempt_id}:{request['turn']}"
        self.states[attempt_id] = after
        evidence_dir = Path(request["evidence_dir"])
        evidence_dir.mkdir(parents=True, exist_ok=True)
        xml = '<hierarchy><node package="example.app" class="android.widget.TextView" resource-id="message" text="bad value" /></hierarchy>'
        path = evidence_dir / "after.xml"
        path.write_text(xml, encoding="utf-8")
        evidence = [{"path": "after.xml", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "kind": "xml"}]
        return {
            "verdict": "pass" if passed else "fail",
            "observation_complete": True,
            "state_before_hash": before,
            "state_after_hash": after,
            "evidence_hash": hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest(),
            "evidence": evidence,
            "diagnostics": [] if passed else [{
                "type": "observed_ui_message",
                "detail": "bad value",
                "public": True,
                "source": "ui_observation",
                "checker_id": "message",
                "evidence_ref": f"request:{request['request_id']}:after.xml",
            }],
        }

    def recover_turn(self, execution: dict) -> dict:
        self.recovers += 1
        attempt_id = Path(execution["evidence_dir"]).parent.name
        state = f"recovered:{attempt_id}:{execution['turn']}"
        self.states[attempt_id] = state
        return {
            "snapshot_loaded": False,
            "remote_reset": False,
            "renavigated_from_baseline": False,
            "natural_state_preserved": True,
            "state_before_hash": execution["state_after_hash"],
            "target_page_verified": True,
            "input_cleared": True,
            "state_hash": state,
            "locator_refresh_hash": f"locators:{attempt_id}",
        }


def fixtures(root: Path, budget: int = 2) -> tuple[dict, dict, dict]:
    prompts = [{"global": "goal", "component": "fields", "adjacent": "context", "restrictive": "schema", "guiding": "guide"}]
    source = root / "prompts.json"
    source.write_text(json.dumps(prompts), encoding="utf-8")
    prompt = "\n".join(prompts[0].values())
    case = {
        "case_id": "P001",
        "task": "valid_pptr",
        "ordinal_in_source": 1,
        "required_resource_ids": ["field"],
        "extracted_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "source_files": {"prompt_collection": "prompts.json"},
    }
    config = {
        "protocol_id": "E2-structure-v1",
        "representation": {"primary": "extracted"},
        "models": [{"id": "model", "capacity_verified": True, "max_prompt_bytes": 100000}],
        "arms": {"A": "a", "B": "b", "C": "c", "D": "d"},
        "replicates": 1,
        "schedule_seed": 7,
        "budgets_including_first_turn": {"valid_pptr": budget},
        "feedback": {"history_limit": 5, "max_candidate_chars": 8000, "max_public_error_chars": 500},
        "runtime": {"environment_timeout_seconds": 30, "refusal_exact_matches": []},
    }
    registry = {"cases": [case]}
    oracle = {
        "case_id": "P001",
        "version": "oracle-v1",
        "task": "valid_pptr",
        "binding": {"field": {"package": "example.app", "class": "android.widget.EditText", "resource-id": "field"}},
        "diagnostic_targets": [{"id": "message", "selector": SELECTOR, "attribute": "text"}],
    }
    return config, registry, oracle


class StructuredRunnerTests(unittest.TestCase):
    def test_exact_json_candidate_accepts_one_fence_plus_required_explanation(self) -> None:
        status, candidate, error = exact_json_candidate(
            '```json\n{"field":"value"}\n```\n\nSelection strategy: valid text.',
            ["field"],
        )
        self.assertEqual(status, "valid")
        self.assertEqual(candidate, {"field": "value"})
        self.assertIsNone(error)

    def test_exact_json_candidate_rejects_multiple_fences(self) -> None:
        status, candidate, error = exact_json_candidate(
            '```json\n{"field":"first"}\n```\n```json\n{"field":"second"}\n```',
            ["field"],
        )
        self.assertEqual(status, "parse_error")
        self.assertIsNone(candidate)
        self.assertIn("exactly one", error or "")

    def test_exact_json_candidate_keeps_bare_response_strict(self) -> None:
        status, candidate, error = exact_json_candidate(
            '{"field":"value"}\nSelection strategy: trailing prose.',
            ["field"],
        )
        self.assertEqual(status, "parse_error")
        self.assertIsNone(candidate)
        self.assertIsNotNone(error)

    def test_canonical_json_losslessly_escapes_lone_surrogate(self) -> None:
        value = json.loads('{"field":"\\ud800"}')
        for serializer in (template_canonical_json, storage_canonical_json):
            serialized = serializer(value)
            serialized.encode("utf-8")
            self.assertEqual(json.loads(serialized), value)
            self.assertIn("\\ud800", serialized)

    def test_plan_skips_explicit_not_applicable_cases(self) -> None:
        runner = StructuredRunner.__new__(StructuredRunner)
        runner.config = {
            "protocol_id": "E2-structure-v1",
            "representation": {"primary": "extracted"},
            "models": [{"id": "model"}],
            "replicates": 1,
        }
        runner.registry = {"cases": [
            {
                "case_id": "P001", "task": "valid_pptr",
                "formal_applicability": {"in_scope": True},
            },
            {
                "case_id": "P031", "task": "valid_pptr",
                "formal_applicability": {"in_scope": False},
            },
        ]}

        self.assertEqual(
            [row["dimensions"]["case_id"] for row in runner.plan()], ["P001"],
        )
        with self.assertRaisesRegex(ValueError, "not-applicable"):
            runner.plan(case_ids={"P031"})

    def _run(self, first: str = '{"field":"bad"}') -> tuple[FakeGateway, FakeEnvironment, StructuredLedger]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config, registry, oracle = fixtures(root)
        ledger = StructuredLedger(root / "ledger.sqlite3", {"test": True})
        self.addCleanup(ledger.close)
        gateway = FakeGateway(first)
        environment = FakeEnvironment()
        runner = StructuredRunner(
            repo_root=root,
            output_dir=root / "run",
            config=config,
            registry=registry,
            oracles={"P001": oracle},
            ledger=ledger,
            gateway=gateway,
            environment=environment,
        )
        result = runner.run()
        self.assertEqual(result["executed_blocks"], 1)
        return gateway, environment, ledger

    def test_shared_first_generation_then_independent_arm_attempts(self) -> None:
        gateway, environment, ledger = self._run()
        self.assertEqual(len(gateway.calls), 5)
        self.assertEqual(environment.begins, 4)
        self.assertEqual(environment.executes, 8)
        self.assertEqual(environment.recovers, 4)
        trajectories = [ledger.trajectory(item) for item in ledger.completed_trajectory_ids()]
        self.assertEqual({row["result"]["status"] for row in trajectories}, {"completed_success"})
        first_physical = {row["result"]["physical_model_request_ids"][0] for row in trajectories}
        self.assertEqual(len(first_physical), 1)
        second_events = {
            row["dimensions"]["arm"]: next(
                event for event in row["events"] if event.get("event") == "model_request" and event.get("turn") == 2
            )
            for row in trajectories
        }
        self.assertEqual(
            second_events["B"]["prompt_audit"]["shared_information_hash"],
            second_events["C"]["prompt_audit"]["shared_information_hash"],
        )
        self.assertFalse(second_events["D"]["prompt_audit"]["fallback_to_c"])
        self.assertTrue(second_events["D"]["prompt_audit"]["verified_evidence"])
        for row in ledger.db.execute("SELECT payload FROM external_calls WHERE kind='model_generation'"):
            self.assertEqual(json.loads(row[0])["id"], "")

    def test_rejected_shared_output_consumes_turn_without_app_execution(self) -> None:
        gateway, environment, ledger = self._run(first="not JSON")
        self.assertEqual(len(gateway.calls), 5)
        self.assertEqual(environment.begins, 4)
        self.assertEqual(environment.executes, 4)
        self.assertEqual(environment.recovers, 0)
        for trajectory_id in ledger.completed_trajectory_ids():
            result = ledger.trajectory(trajectory_id)["result"]
            self.assertEqual(result["model_calls"], 2)
            self.assertEqual(result["executions"], 1)

    def test_execution_cap_advances_past_completed_blocks_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, registry, oracle = fixtures(root)
            config["replicates"] = 2
            ledger = StructuredLedger(root / "ledger.sqlite3", {"test": "resume"})
            gateway = FakeGateway()
            environment = FakeEnvironment()
            runner = StructuredRunner(
                repo_root=root,
                output_dir=root / "run",
                config=config,
                registry=registry,
                oracles={"P001": oracle},
                ledger=ledger,
                gateway=gateway,
                environment=environment,
            )
            first = runner.run(max_blocks=1)
            second = runner.run(max_blocks=1)
            self.assertEqual(first["executed_blocks"], 1)
            self.assertEqual(second["executed_blocks"], 1)
            self.assertEqual(second["skipped_completed_blocks"], 1)
            self.assertEqual(len(ledger.completed_trajectory_ids()), 8)
            ledger.close()

    def test_smoke_turn_cap_limits_trajectory_without_changing_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, registry, oracle = fixtures(root, budget=30)
            ledger = StructuredLedger(root / "ledger.sqlite3", {"test": "smoke-turn-cap"})
            gateway = FakeGateway(first='{"field":"bad"}')
            environment = FakeEnvironment()
            runner = StructuredRunner(
                repo_root=root,
                output_dir=root / "run",
                config=config,
                registry=registry,
                oracles={"P001": oracle},
                ledger=ledger,
                gateway=gateway,
                environment=environment,
            )

            summary = runner.run(turn_cap=1)

            self.assertEqual(summary["turn_cap"], 1)
            self.assertEqual(config["budgets_including_first_turn"]["valid_pptr"], 30)
            self.assertEqual(len(gateway.calls), 1)
            for trajectory_id in ledger.completed_trajectory_ids():
                result = ledger.trajectory(trajectory_id)["result"]
                self.assertEqual(result["budget"], 1)
                self.assertEqual(result["status"], "completed_failure")
            ledger.close()

    def test_smoke_turn_cap_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, registry, oracle = fixtures(root)
            ledger = StructuredLedger(root / "ledger.sqlite3", {"test": "invalid-turn-cap"})
            self.addCleanup(ledger.close)
            runner = StructuredRunner(
                repo_root=root,
                output_dir=root / "run",
                config=config,
                registry=registry,
                oracles={"P001": oracle},
                ledger=ledger,
                gateway=FakeGateway(),
                environment=FakeEnvironment(),
            )

            with self.assertRaisesRegex(ValueError, "positive integer"):
                runner.run(turn_cap=0)


if __name__ == "__main__":
    unittest.main()
