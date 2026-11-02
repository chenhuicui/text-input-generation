from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from text_input_eval.supplementary.abcdef_evidence import build_feedback_artifacts
from text_input_eval.supplementary.abcdef_runner import ABCDEFRunner
from text_input_eval.supplementary.structured_storage import StructuredLedger


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, model: dict, prompt: str, attachments: list[Path]) -> dict:
        self.calls.append({"model": model["id"], "prompt": prompt, "attachments": list(attachments)})
        raw = '{"field":"bad"}' if len(self.calls) == 1 else '{"field":"good"}'
        return {"run_status": "ok", "raw_response": raw}


class FakeAnalyzer:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, Path | None]] = []

    def analyze(self, context: dict, image_path: Path | None) -> dict:
        self.calls.append((context, image_path))
        return {"run_status": "ok", "diagnosis": {
            "execution_status": context["execution_status"],
            "diagnosis_category": "visible_rejection",
            "diagnosis": "The candidate produced a visible error on the unchanged screen.",
            "support_level": "direct",
            "temporal_summary": "The error appeared after submission and persisted.",
            "evidence_refs": ["request:r1:frame_000.png"],
            "alternative_explanations": [],
            "uncertainty": "No server response body was visible.",
            "recommendation": None,
        }}


class InfrastructureAnalyzer(FakeAnalyzer):
    def analyze(self, context: dict, image_path: Path | None) -> dict:
        self.calls.append((context, image_path))
        return {
            "run_status": "infrastructure_error",
            "error_type": "UnicodeEncodeError",
            "elapsed_ms": 0,
        }


class QuotaAnalyzer(FakeAnalyzer):
    def analyze(self, context: dict, image_path: Path | None) -> dict:
        self.calls.append((context, image_path))
        return {
            "run_status": "infrastructure_error",
            "error_type": "quota_exhausted",
            "provider_response": {"message": "credit balance exhausted"},
            "elapsed_ms": 0,
        }


class FakeEnvironment:
    def __init__(self) -> None:
        self.begins = self.executes = self.recovers = 0

    def begin_attempt(self, case: dict, attempt: dict) -> dict:
        self.begins += 1
        return {"reset_verified": True, "state_hash": f"begin:{attempt['attempt_id']}"}

    @staticmethod
    def _frame(directory: Path, name: str, text: str, seconds: float, request_id: str) -> dict:
        xml = directory / f"{name}.xml"
        png = directory / f"{name}.png"
        xml.write_text(
            '<hierarchy><node package="example.app" class="android.widget.TextView" '
            f'resource-id="message" text="{text}" /></hierarchy>',
            encoding="utf-8",
        )
        Image.new("RGB", (8, 12), "white").save(png)
        return {
            "label": name, "scheduled_seconds": seconds, "actual_seconds": seconds,
            "state_hash": name, "xml_path": str(xml), "png_path": str(png),
            "foreground": {"package": "example.app", "activity": ".Main"},
            "evidence_ref": f"request:{request_id}:{png.name}",
        }

    def execute_turn(self, candidate: dict[str, str], request: dict) -> dict:
        self.executes += 1
        request_dir = Path(request["evidence_dir"])
        request_dir.mkdir(parents=True, exist_ok=True)
        passed = candidate["field"] == "good"
        frames = [
            self._frame(request_dir, "pre_action", "Ready", -0.1, request["request_id"]),
            self._frame(request_dir, "frame_000", "Success" if passed else "Invalid value", 0.0, request["request_id"]),
            self._frame(request_dir, "frame_001", "Success" if passed else "Invalid value", 1.0, request["request_id"]),
        ]
        visual = build_feedback_artifacts(
            evidence_root=Path(request["trajectory_evidence_root"]),
            request_dir=request_dir,
            request_id=request["request_id"],
            candidate=candidate,
            oracle_version=request["oracle"]["version"],
            binding={},
            frames=frames,
        )
        return {
            "verdict": "pass" if passed else "fail",
            "observation_complete": True,
            "state_before_hash": "before",
            "state_after_hash": "after",
            "evidence_hash": hashlib.sha256(str(visual).encode()).hexdigest(),
            "visual_feedback": visual,
        }

    def recover_turn(self, execution: dict) -> dict:
        self.recovers += 1
        return {"input_cleared": True, "state_hash": "recovered"}


class InfrastructureBeginEnvironment(FakeEnvironment):
    def begin_attempt(self, case: dict, attempt: dict) -> dict:
        self.begins += 1
        return {
            "run_status": "infrastructure_error",
            "error_type": "android_adapter_nonzero_exit",
            "error_message": "dynamic selector matched 0 nodes",
        }


class OneShotHierarchyFailureEnvironment(FakeEnvironment):
    def execute_turn(self, candidate: dict[str, str], request: dict) -> dict:
        if self.executes == 0:
            self.executes += 1
            return {
                "verdict": "infrastructure_error",
                "observation_complete": False,
                "error_type": "RuntimeError",
                "error_message": (
                    "native UI hierarchy fallback command failed "
                    "(exit=137, detail=none)"
                ),
            }
        return super().execute_turn(candidate, request)


class OneCasePreflightFailureEnvironment(FakeEnvironment):
    def requires_block_preflight(self, case: dict) -> bool:
        return case["case_id"] == "P001"

    def preflight_block(self, case: dict, oracle: dict) -> dict:
        return {
            "run_status": "infrastructure_error",
            "error_type": "DockerPreflightError",
            "error_message": "daemon unavailable",
        }


class QuotaGateway(FakeGateway):
    def generate(self, model: dict, prompt: str, attachments: list[Path]) -> dict:
        self.calls.append({"model": model["id"], "prompt": prompt, "attachments": attachments})
        return {
            "run_status": "infrastructure_error",
            "error_type": "quota_exhausted",
            "provider_response": {"message": "余额不足"},
        }


class AlwaysInvalidGateway(FakeGateway):
    def generate(self, model: dict, prompt: str, attachments: list[Path]) -> dict:
        self.calls.append({"model": model["id"], "prompt": prompt, "attachments": attachments})
        return {"run_status": "ok", "raw_response": "{}"}


def fixtures(root: Path) -> tuple[dict, dict, dict]:
    prompts = [{"global": "goal", "component": "fields", "adjacent": "context", "restrictive": "schema", "guiding": "guide"}]
    (root / "prompts.json").write_text(json.dumps(prompts), encoding="utf-8")
    prompt = "\n".join(prompts[0].values())
    case = {
        "case_id": "P001", "task": "valid_pptr", "ordinal_in_source": 1,
        "required_resource_ids": ["field"],
        "extracted_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "source_files": {"prompt_collection": "prompts.json"},
        "formal_applicability": {"in_scope": True},
    }
    config = {
        "protocol_id": "E2-abcdef-v2",
        "representation": {"primary": "extracted"},
        "models": [{
            "id": "model", "capacity_verified": True, "max_prompt_bytes": 100000,
            "feedback_image_verified": True, "max_feedback_image_bytes": 100000,
        }],
        "arms": {arm: arm for arm in "ABCDEF"},
        "experimental_unit": {
            "trajectory_axes": ["model", "case_id", "arm"],
            "one_trajectory_per_axis_cell": True,
            "replicate_axis": False,
            "semantic_success_early_stop": True,
        },
        "schedule_seed": 7,
        "budgets_including_first_turn": {"valid_pptr": 3, "invalid_bug": 30},
        "feedback": {"history_limit": 5, "max_candidate_chars": 8000},
        "capture_protocol": {},
        "analyzer": {"model": "gpt-5.6-sol", "reasoning_effort": "medium"},
        "runtime": {"environment_timeout_seconds": 30, "refusal_exact_matches": []},
    }
    return config, {"cases": [case]}, {"case_id": "P001", "task": "valid_pptr", "version": "oracle-v1"}


class ABCDEFRunnerTests(unittest.TestCase):
    def test_arm_selection_runs_only_requested_cells(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config, registry, oracle = fixtures(root)
        ledger = StructuredLedger(root / "ledger.sqlite3", {"test": "arm-selection"})
        self.addCleanup(ledger.close)
        gateway = FakeGateway()
        environment = FakeEnvironment()
        runner = ABCDEFRunner(
            repo_root=root,
            output_dir=root / "run",
            config=config,
            registry=registry,
            oracles={"P001": oracle},
            ledger=ledger,
            gateway=gateway,
            environment=environment,
            analyzer=FakeAnalyzer(),
        )

        result = runner.run(arm_ids={"B", "E"})

        self.assertEqual(result["selected_arms"], ["B", "E"])
        rows = [ledger.trajectory(item) for item in ledger.completed_trajectory_ids()]
        self.assertEqual({row["dimensions"]["arm"] for row in rows}, {"B", "E"})
        self.assertEqual(len(rows), 2)
        self.assertEqual(environment.begins, 2)
        self.assertEqual(len(gateway.calls), 3)

    def test_unknown_arm_selection_fails_before_provider_call(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config, registry, oracle = fixtures(root)
        ledger = StructuredLedger(root / "ledger.sqlite3", {"test": "bad-arm"})
        self.addCleanup(ledger.close)
        gateway = FakeGateway()
        runner = ABCDEFRunner(
            repo_root=root,
            output_dir=root / "run",
            config=config,
            registry=registry,
            oracles={"P001": oracle},
            ledger=ledger,
            gateway=gateway,
            environment=FakeEnvironment(),
            analyzer=FakeAnalyzer(),
        )

        with self.assertRaisesRegex(ValueError, "unknown arm selection"):
            runner.run(arm_ids={"G"})
        self.assertEqual(gateway.calls, [])

    def test_paired_first_turn_and_six_distinct_feedback_conditions(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config, registry, oracle = fixtures(root)
        ledger = StructuredLedger(root / "ledger.sqlite3", {"test": "abcdef"})
        self.addCleanup(ledger.close)
        gateway = FakeGateway()
        environment = FakeEnvironment()
        analyzer = FakeAnalyzer()
        runner = ABCDEFRunner(
            repo_root=root,
            output_dir=root / "run",
            config=config,
            registry=registry,
            oracles={"P001": oracle},
            ledger=ledger,
            gateway=gateway,
            environment=environment,
            analyzer=analyzer,
        )
        result = runner.run()
        self.assertEqual(result["executed_blocks"], 1)
        self.assertEqual(len(gateway.calls), 7)
        self.assertEqual(environment.begins, 6)
        self.assertEqual(environment.executes, 12)
        self.assertEqual(environment.recovers, 6)
        self.assertEqual(len(analyzer.calls), 1)
        trajectories = [ledger.trajectory(item) for item in ledger.completed_trajectory_ids()]
        self.assertEqual({row["result"]["status"] for row in trajectories}, {"completed_success"})
        self.assertTrue(all("replicate" not in row["dimensions"] for row in trajectories))
        self.assertEqual({row["result"]["first_success_turn"] for row in trajectories}, {2})
        self.assertEqual({row["result"]["model_calls"] for row in trajectories}, {2})
        second_requests = {
            row["dimensions"]["arm"]: next(
                event for event in row["events"]
                if event.get("event") == "model_request" and event.get("turn") == 2
            )
            for row in trajectories
        }
        self.assertFalse(second_requests["B"]["prompt_audit"]["status_visible"])
        self.assertTrue(second_requests["C"]["prompt_audit"]["status_visible"])
        self.assertTrue(second_requests["D"]["prompt_audit"]["visual_visible"])
        self.assertTrue(second_requests["E"]["prompt_audit"]["ui_observations_visible"])
        self.assertTrue(second_requests["F"]["prompt_audit"]["analysis_visible"])
        self.assertEqual(gateway.calls[0]["attachments"], [])

    def test_bug_semantic_failure_consumes_full_thirty_turn_budget(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config, registry, oracle = fixtures(root)
        registry["cases"][0]["case_id"] = "B001"
        registry["cases"][0]["task"] = "invalid_bug"
        oracle = {**oracle, "case_id": "B001", "task": "invalid_bug"}
        ledger = StructuredLedger(root / "ledger.sqlite3", {"test": "bug-budget"})
        self.addCleanup(ledger.close)
        gateway = AlwaysInvalidGateway()
        runner = ABCDEFRunner(
            repo_root=root,
            output_dir=root / "run",
            config=config,
            registry=registry,
            oracles={"B001": oracle},
            ledger=ledger,
            gateway=gateway,
            environment=FakeEnvironment(),
            analyzer=FakeAnalyzer(),
        )

        result = runner.run()

        self.assertEqual(result["executed_blocks"], 1)
        rows = [ledger.trajectory(item) for item in ledger.completed_trajectory_ids()]
        self.assertEqual(len(rows), 6)
        self.assertEqual({row["result"]["status"] for row in rows}, {"completed_failure"})
        self.assertEqual({row["result"]["reason"] for row in rows}, {"budget_exhausted_schema_error"})
        self.assertEqual({row["result"]["model_calls"] for row in rows}, {30})
        self.assertEqual({len(row["result"]["records"]) for row in rows}, {30})
        self.assertEqual({row["result"]["first_success_turn"] for row in rows}, {None})

    def test_android_infrastructure_error_finishes_each_arm_without_worker_crash(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config, registry, oracle = fixtures(root)
        ledger = StructuredLedger(root / "ledger.sqlite3", {"test": "abcdef-infra"})
        self.addCleanup(ledger.close)
        runner = ABCDEFRunner(
            repo_root=root,
            output_dir=root / "run",
            config=config,
            registry=registry,
            oracles={"P001": oracle},
            ledger=ledger,
            gateway=FakeGateway(),
            environment=InfrastructureBeginEnvironment(),
            analyzer=FakeAnalyzer(),
        )
        result = runner.run()
        self.assertEqual(result["executed_blocks"], 1)
        rows = [ledger.trajectory(item) for item in ledger.completed_trajectory_ids()]
        self.assertEqual(len(rows), 6)
        self.assertEqual({row["result"]["status"] for row in rows}, {"incomplete"})
        self.assertEqual(
            {row["result"]["reason"] for row in rows},
            {"android_begin_infrastructure_error"},
        )

    def test_injected_hierarchy_failure_is_incomplete_and_runner_advances(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config, registry, oracle = fixtures(root)
        ledger = StructuredLedger(root / "ledger.sqlite3", {"test": "hierarchy-injection"})
        self.addCleanup(ledger.close)
        environment = OneShotHierarchyFailureEnvironment()
        runner = ABCDEFRunner(
            repo_root=root,
            output_dir=root / "run",
            config=config,
            registry=registry,
            oracles={"P001": oracle},
            ledger=ledger,
            gateway=FakeGateway(),
            environment=environment,
            analyzer=FakeAnalyzer(),
        )

        result = runner.run()

        self.assertEqual(result["executed_blocks"], 1)
        rows = [ledger.trajectory(item) for item in ledger.completed_trajectory_ids()]
        statuses = [row["result"]["status"] for row in rows]
        self.assertEqual(statuses.count("incomplete"), 1)
        self.assertEqual(statuses.count("completed_success"), 5)
        incomplete = next(row for row in rows if row["result"]["status"] == "incomplete")
        self.assertEqual(incomplete["result"]["reason"], "android_infrastructure_error")
        self.assertEqual(len(rows), 6)
        self.assertGreater(environment.executes, 1)

    def test_failed_block_preflight_consumes_no_trajectory_and_next_case_runs(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config, registry, oracle = fixtures(root)
        second = {**registry["cases"][0], "case_id": "P002"}
        registry["cases"].append(second)
        ledger = StructuredLedger(root / "ledger.sqlite3", {"test": "block-preflight"})
        self.addCleanup(ledger.close)
        gateway = FakeGateway()
        environment = OneCasePreflightFailureEnvironment()
        runner = ABCDEFRunner(
            repo_root=root,
            output_dir=root / "run",
            config=config,
            registry=registry,
            oracles={"P001": oracle, "P002": {**oracle, "case_id": "P002"}},
            ledger=ledger,
            gateway=gateway,
            environment=environment,
            analyzer=FakeAnalyzer(),
        )

        result = runner.run()

        self.assertEqual(result["executed_blocks"], 2)
        self.assertEqual(result["preflight_failed_blocks"], 1)
        self.assertEqual(len(result["preflight_failures"]), 1)
        rows = [ledger.trajectory(item) for item in ledger.completed_trajectory_ids()]
        self.assertEqual(len(rows), 6)
        self.assertEqual({row["dimensions"]["case_id"] for row in rows}, {"P002"})
        self.assertEqual(len(gateway.calls), 7)
        ledger.ensure_resumable()

    def test_analyzer_infrastructure_error_is_persisted_without_worker_crash(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config, registry, oracle = fixtures(root)
        ledger = StructuredLedger(root / "ledger.sqlite3", {"test": "analyzer-infra"})
        self.addCleanup(ledger.close)
        analyzer = InfrastructureAnalyzer()
        runner = ABCDEFRunner(
            repo_root=root,
            output_dir=root / "run",
            config=config,
            registry=registry,
            oracles={"P001": oracle},
            ledger=ledger,
            gateway=FakeGateway(),
            environment=FakeEnvironment(),
            analyzer=analyzer,
        )
        result = runner.run()
        self.assertEqual(result["executed_blocks"], 1)
        rows = [ledger.trajectory(item) for item in ledger.completed_trajectory_ids()]
        f_row = next(row for row in rows if row["dimensions"]["arm"] == "F")
        self.assertEqual(f_row["result"]["status"], "incomplete")
        self.assertEqual(f_row["result"]["reason"], "infrastructure_error")
        analysis_event = next(
            event for event in f_row["events"] if event.get("event") == "feedback_analysis"
        )
        analysis_call = ledger.call(analysis_event["call_id"])
        self.assertEqual(analysis_call["state"], "done")
        self.assertEqual(analysis_call["result"]["error_type"], "UnicodeEncodeError")
        ledger.ensure_resumable()

    def test_analyzer_quota_emits_global_compatible_provider_stop(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config, registry, oracle = fixtures(root)
        ledger = StructuredLedger(root / "ledger.sqlite3", {"test": "analyzer-quota"})
        self.addCleanup(ledger.close)
        analyzer = QuotaAnalyzer()
        runner = ABCDEFRunner(
            repo_root=root,
            output_dir=root / "run",
            config=config,
            registry=registry,
            oracles={"P001": oracle},
            ledger=ledger,
            gateway=FakeGateway(),
            environment=FakeEnvironment(),
            analyzer=analyzer,
        )

        result = runner.run(arm_ids={"F"})

        self.assertEqual(
            result["stop_signal"]["reason"], "subject_model_quota_exhausted"
        )
        self.assertEqual(result["stop_signal"]["provider_role"], "analyzer")
        self.assertEqual(result["stop_signal"]["provider_model"], "gpt-5.6-sol")
        row = ledger.trajectory(next(iter(ledger.completed_trajectory_ids())))
        self.assertEqual(row["result"]["status"], "incomplete")
        self.assertEqual(row["result"]["reason"], "subject_model_quota_exhausted")
        analysis_event = next(
            event for event in row["events"] if event.get("event") == "feedback_analysis"
        )
        analysis_call = ledger.call(analysis_event["call_id"])
        self.assertEqual(analysis_call["kind"], "external_feedback_analysis")
        self.assertEqual(analysis_call["result"]["error_type"], "quota_exhausted")
        ledger.ensure_resumable()

    def test_confirmed_quota_stops_before_any_trajectory_is_reserved(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config, registry, oracle = fixtures(root)
        ledger = StructuredLedger(root / "ledger.sqlite3", {"test": "abcdef-quota"})
        self.addCleanup(ledger.close)
        runner = ABCDEFRunner(
            repo_root=root,
            output_dir=root / "run",
            config=config,
            registry=registry,
            oracles={"P001": oracle},
            ledger=ledger,
            gateway=QuotaGateway(),
            environment=FakeEnvironment(),
            analyzer=FakeAnalyzer(),
        )
        result = runner.run()
        self.assertEqual(result["stop_signal"]["reason"], "subject_model_quota_exhausted")
        self.assertEqual(ledger.completed_trajectory_ids(), set())
        ledger.ensure_resumable()


if __name__ == "__main__":
    unittest.main()
