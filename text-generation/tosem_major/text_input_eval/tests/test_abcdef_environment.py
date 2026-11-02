from __future__ import annotations

import unittest
import subprocess
from subprocess import CompletedProcess
from unittest.mock import patch

from text_input_eval.supplementary.abcdef_environment import ABCDEFHybridEnvironment
from text_input_eval.supplementary.abcdef_host_preflight import b019_docker_preflight
from text_input_eval.supplementary.structured_environment import AndroidCommandEnvironment


class FakeSide:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.calls: list[tuple[str, dict]] = []

    def begin_attempt(self, case: dict, attempt: dict) -> dict:
        self.calls.append(("begin", attempt))
        return {
            "reset_verified": True, "target_page_verified": True,
            "remote_baseline": "not_applicable", "reset_id": "reset",
            "state_hash": f"{self.kind}-begin",
        }

    def execute_turn(self, candidate: dict, request: dict) -> dict:
        self.calls.append(("execute", request))
        if self.kind == "predicate":
            return {
                "verdict": "pass", "observation_complete": True,
                "state_after_hash": "predicate-after", "evidence": [],
            }
        return {
            "verdict": "fail", "observation_complete": True,
            "state_after_hash": "app-after", "evidence_hash": "app-evidence",
            "evidence": [{"kind": "screenshot"}],
            "visual_feedback": {"composite_path": "feedback.png"},
        }

    def recover_turn(self, execution: dict) -> dict:
        self.calls.append(("recover", execution))
        return {
            "natural_state_preserved": True, "target_page_verified": True,
            "input_cleared": True, "state_before_hash": execution.get("state_after_hash"),
            "state_hash": f"{self.kind}-recovered", "locator_refresh_hash": "locators",
        }


class ABCDEFEnvironmentTests(unittest.TestCase):
    @patch("text_input_eval.supplementary.abcdef_host_preflight.subprocess.run")
    def test_b019_docker_preflight_contains_unavailable_daemon(self, run) -> None:
        run.return_value = CompletedProcess(
            args=["docker", "info"], returncode=1, stdout="",
            stderr="Cannot connect to the Docker daemon",
        )
        result = b019_docker_preflight([
            "/opt/anaconda3/bin/python3",
            "/Volumes/Extreme Pro/emulator/CD_115page_37bug/scripts/b019_matrix_reset.py",
        ])
        self.assertEqual(result["run_status"], "infrastructure_error")
        self.assertEqual(result["error_type"], "RuntimeError")
        self.assertIn("Docker daemon", result["error_message"])

    @patch("text_input_eval.supplementary.structured_environment.subprocess.run")
    def test_android_boundary_losslessly_escapes_lone_surrogate(self, run) -> None:
        run.return_value = CompletedProcess(
            args=["adapter"], returncode=0, stdout='{"ok":true}', stderr="",
        )
        value = chr(0xD800)
        result = AndroidCommandEnvironment(["adapter"])._call(
            "execute_turn", {"candidate": {"field": value}},
        )
        serialized = run.call_args.kwargs["input"]
        self.assertIn("\\ud800", serialized)
        self.assertEqual(serialized.encode("utf-8").decode("utf-8"), serialized)
        self.assertEqual(result, {"ok": True})

    @patch("text_input_eval.supplementary.structured_environment.subprocess.run")
    def test_android_nonzero_exit_becomes_structured_infrastructure_error(self, run) -> None:
        run.return_value = CompletedProcess(
            args=["adapter"], returncode=1,
            stdout="", stderr="dynamic selector matched 0 nodes",
        )
        result = AndroidCommandEnvironment(["adapter"]).execute_turn(
            {"field": "value"}, {"evidence_dir": "/tmp/e2-boundary"},
        )
        self.assertEqual(result["run_status"], "infrastructure_error")
        self.assertEqual(result["verdict"], "infrastructure_error")
        self.assertEqual(result["adapter_returncode"], 1)
        self.assertIn("dynamic selector", result["error_message"])

    @patch("text_input_eval.supplementary.structured_environment.subprocess.run")
    def test_android_timeout_becomes_structured_infrastructure_error(self, run) -> None:
        run.side_effect = subprocess.TimeoutExpired(["adapter"], 10)
        result = AndroidCommandEnvironment(["adapter"]).begin_attempt({}, {})
        self.assertEqual(result["run_status"], "infrastructure_error")
        self.assertEqual(result["error_type"], "TimeoutExpired")

    def test_bug_verdict_remains_predicate_while_app_supplies_visual_evidence(self) -> None:
        android = FakeSide("android")
        predicate = FakeSide("predicate")
        feedback_oracle = {
            "case_id": "B001", "oracle_role": "feedback_observation_only",
            "version": "app-observation-v1",
        }
        environment = ABCDEFHybridEnvironment(android, predicate, {"B001": feedback_oracle})
        case = {"case_id": "B001", "task": "invalid_bug"}
        verdict_oracle = {"oracle_kind": "string_predicate", "version": "predicate-v1"}
        attempt = {"attempt_id": "a", "oracle": verdict_oracle, "evidence_dir": "/tmp/begin"}
        environment.begin_attempt(case, attempt)
        result = environment.execute_turn({"field": "value"}, {
            "attempt_id": "a", "case": case,
            "oracle": verdict_oracle,
            "evidence_dir": "/tmp/turn", "turn": 1,
        })
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["app_observation_verdict"], "fail")
        self.assertEqual(result["verdict_oracle_version"], "predicate-v1")
        self.assertEqual(result["visual_observation_oracle_version"], "app-observation-v1")
        self.assertEqual(result["visual_feedback"], {"composite_path": "feedback.png"})
        self.assertIs(android.calls[1][1]["oracle"], feedback_oracle)
        self.assertEqual(predicate.calls[1][1]["oracle"]["oracle_kind"], "string_predicate")
        recovery = environment.recover_turn({
            **result, "case": case, "oracle": verdict_oracle,
            "attempt_id": "a", "evidence_dir": "/tmp/turn", "turn": 1,
        })
        self.assertEqual(predicate.calls[-1][1]["state_after_hash"], "predicate-after")
        self.assertEqual(android.calls[-1][0], "begin")
        self.assertEqual(
            android.calls[-1][1]["evidence_dir"],
            "/tmp/turn/baseline_reload/app_observation",
        )
        self.assertTrue(recovery["snapshot_loaded"])
        self.assertEqual(recovery["app_observation_recovery_mode"], "independent_baseline_reload")

    def test_missing_bug_app_contract_fails_closed(self) -> None:
        environment = ABCDEFHybridEnvironment(FakeSide("android"), FakeSide("predicate"), {})
        with self.assertRaisesRegex(ValueError, "missing Bug"):
            environment.begin_attempt(
                {"case_id": "B001", "task": "invalid_bug"},
                {"attempt_id": "a", "oracle": {}, "evidence_dir": "/tmp/begin"},
            )


if __name__ == "__main__":
    unittest.main()
