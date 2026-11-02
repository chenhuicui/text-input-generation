"""Environment routing for A-F with Oracle/feedback separation.

Page cases use their frozen App behavior Oracle.  Bug cases retain the frozen
string predicate as the verdict Oracle, while a separate, versioned Android
contract is executed for every A-F arm solely to collect comparable public UI
evidence.  The observational App verdict is never substituted for the bug
predicate verdict.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .abcdef_host_preflight import b019_docker_preflight
from .structured_environment import AndroidCommandEnvironment, StringPredicateEnvironment


class ABCDEFHybridEnvironment:
    def __init__(
        self,
        android: AndroidCommandEnvironment,
        predicate: StringPredicateEnvironment,
        bug_feedback_oracles: dict[str, dict[str, Any]],
    ) -> None:
        self.android = android
        self.predicate = predicate
        self.bug_feedback_oracles = bug_feedback_oracles

    @staticmethod
    def _is_bug(case: dict[str, Any]) -> bool:
        return case.get("task") == "invalid_bug"

    def _feedback_oracle(self, case_id: str) -> dict[str, Any]:
        try:
            oracle = self.bug_feedback_oracles[case_id]
        except KeyError as exc:
            raise ValueError(f"missing Bug App-observation contract for {case_id}") from exc
        if oracle.get("oracle_role") != "feedback_observation_only":
            raise ValueError(f"Bug App contract can only be observational for {case_id}")
        return oracle

    def requires_block_preflight(self, case: dict[str, Any]) -> bool:
        return self._is_bug(case) and case.get("case_id") == "B019"

    def preflight_block(
        self, case: dict[str, Any], oracle: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.requires_block_preflight(case):
            raise ValueError("block preflight was requested for an unsupported case")
        feedback = self._feedback_oracle(case["case_id"])
        command = feedback.get("begin_attempt", {}).get("reset_command")
        if not isinstance(command, list) or any(
            not isinstance(item, str) or not item for item in command
        ):
            return {
                "run_status": "infrastructure_error",
                "error_type": "B019PreflightContractError",
                "error_message": "B019 reset command is unavailable",
            }
        return b019_docker_preflight(command)

    @staticmethod
    def _observation_evidence_dir(path: str) -> str:
        return str(Path(path) / "app_observation")

    def begin_attempt(self, case: dict[str, Any], attempt: dict[str, Any]) -> dict[str, Any]:
        if not self._is_bug(case):
            return self.android.begin_attempt(case, attempt)
        predicate_receipt = self.predicate.begin_attempt(case, attempt)
        app_attempt = {
            **attempt,
            "oracle": self._feedback_oracle(case["case_id"]),
            "evidence_dir": self._observation_evidence_dir(attempt["evidence_dir"]),
        }
        app_receipt = self.android.begin_attempt(case, app_attempt)
        if app_receipt.get("run_status") == "infrastructure_error":
            return {
                **app_receipt,
                "reset_verified": False,
                "target_page_verified": False,
                "predicate_receipt": predicate_receipt,
                "app_observation_receipt": app_receipt,
            }
        return {
            "reset_verified": predicate_receipt.get("reset_verified") is True
            and app_receipt.get("reset_verified") is True,
            "target_page_verified": app_receipt.get("target_page_verified"),
            "remote_baseline": app_receipt.get("remote_baseline"),
            "reset_id": app_receipt.get("reset_id"),
            "state_hash": predicate_receipt.get("state_hash"),
            "device_time": app_receipt.get("device_time"),
            "predicate_receipt": predicate_receipt,
            "app_observation_receipt": app_receipt,
        }

    def execute_turn(self, candidate: dict[str, str], request: dict[str, Any]) -> dict[str, Any]:
        case = request["case"]
        if not self._is_bug(case):
            return self.android.execute_turn(candidate, request)
        feedback_oracle = self._feedback_oracle(case["case_id"])
        predicate_result = self.predicate.execute_turn(candidate, request)
        app_request = {
            **request,
            "oracle": feedback_oracle,
            "evidence_dir": self._observation_evidence_dir(request["evidence_dir"]),
        }
        app_result = self.android.execute_turn(candidate, app_request)
        if app_result.get("verdict") == "infrastructure_error":
            return {
                **app_result,
                "verdict": "infrastructure_error",
                "observation_complete": False,
                "predicate_verdict": predicate_result.get("verdict"),
                "app_observation_verdict": "infrastructure_error",
                "verdict_oracle_version": request["oracle"]["version"],
                "visual_observation_oracle_version": feedback_oracle["version"],
            }
        return {
            **predicate_result,
            "verdict": predicate_result["verdict"],
            "observation_complete": predicate_result.get("observation_complete") is True,
            "predicate_state_after_hash": predicate_result.get("state_after_hash"),
            "app_state_after_hash": app_result.get("state_after_hash"),
            "visual_feedback": app_result.get("visual_feedback"),
            "app_observation_verdict": app_result.get("verdict"),
            "app_observation_evidence_hash": app_result.get("evidence_hash"),
            "app_observation_evidence": app_result.get("evidence", []),
            "verdict_oracle_version": request["oracle"]["version"],
            "visual_observation_oracle_version": feedback_oracle["version"],
        }

    def recover_turn(self, execution: dict[str, Any]) -> dict[str, Any]:
        case = execution["case"]
        if not self._is_bug(case):
            return self.android.recover_turn(execution)
        predicate_execution = {
            **execution,
            "state_after_hash": execution.get("predicate_state_after_hash", execution.get("state_after_hash")),
        }
        predicate_receipt = self.predicate.recover_turn(predicate_execution)
        # Bug verdicts are stateless string predicates and their App runs are
        # feedback observations only.  A candidate may legitimately crash or
        # navigate away from the frozen page, so a no-reset UI recovery would
        # make the next candidate depend on the previous candidate.  Restore
        # the same frozen baseline after every predicate-false turn instead.
        # This reset never changes the predicate verdict and is recorded
        # explicitly as an App-observation baseline reload.
        app_attempt = {
            "attempt_id": execution["attempt_id"],
            "case_id": case["case_id"],
            "oracle": self._feedback_oracle(case["case_id"]),
            "evidence_dir": self._observation_evidence_dir(
                str(Path(execution["evidence_dir"]) / "baseline_reload")
            ),
            "command_timeout_seconds": execution.get("command_timeout_seconds", 600),
        }
        app_receipt = self.android.begin_attempt(case, app_attempt)
        if app_receipt.get("run_status") == "infrastructure_error":
            return {
                **app_receipt,
                "snapshot_loaded": False,
                "natural_state_preserved": False,
                "target_page_verified": False,
                "input_cleared": False,
                "predicate_receipt": predicate_receipt,
                "app_observation_receipt": app_receipt,
            }
        return {
            "snapshot_loaded": True,
            "remote_reset": False,
            "renavigated_from_baseline": False,
            "natural_state_preserved": False,
            "state_before_hash": predicate_receipt.get("state_before_hash"),
            "target_page_verified": app_receipt.get("target_page_verified"),
            "input_cleared": app_receipt.get("target_page_verified"),
            "state_hash": predicate_receipt.get("state_hash"),
            "locator_refresh_hash": app_receipt.get("state_hash"),
            "app_observation_recovery_mode": "independent_baseline_reload",
            "predicate_receipt": predicate_receipt,
            "app_observation_receipt": app_receipt,
        }
