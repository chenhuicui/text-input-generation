"""Subprocess boundary for the E2 Android adapter."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import text_input_eval.validators  # noqa: F401 - populate the validator registry
from text_input_eval.validators.registry import get_validator

from .structured_templates import sha256_json
from .structured_storage import canonical_json


class AndroidCommandEnvironment:
    def __init__(self, command: list[str], *, timeout_seconds: float = 600) -> None:
        if not command or any(not isinstance(item, str) or not item for item in command):
            raise ValueError("environment command must be a non-empty argv list")
        self.command = command
        self.timeout_seconds = timeout_seconds

    def _call(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = {"operation": operation, **payload}
        try:
            completed = subprocess.run(
                self.command,
                input=canonical_json(request),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "run_status": "infrastructure_error",
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:2000],
                "operation": operation,
            }
        if completed.returncode != 0:
            # The command may have already changed the App.  Do not turn this
            # into a retryable semantic result.
            detail = (completed.stderr or completed.stdout).strip()
            parsed_error: dict[str, Any] = {}
            try:
                candidate = json.loads(detail)
                if isinstance(candidate, dict):
                    parsed_error = candidate
            except json.JSONDecodeError:
                pass
            return {
                "run_status": "infrastructure_error",
                "error_type": str(parsed_error.get("error_type") or "android_adapter_nonzero_exit"),
                "error_message": str(parsed_error.get("message") or detail)[-2000:],
                "adapter_returncode": completed.returncode,
                "operation": operation,
            }
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            # uiautomator2 may print one-time helper-APK installation progress
            # to stdout before the adapter emits its final one-line JSON
            # response.  Accept only the last independently parseable object;
            # arbitrary prose or partial JSON remains a hard protocol error.
            result = None
            for line in reversed(completed.stdout.splitlines()):
                candidate = line.strip()
                if not (candidate.startswith("{") and candidate.endswith("}")):
                    continue
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    result = parsed
                    break
            if result is None:
                return {
                    "run_status": "infrastructure_error",
                    "error_type": "android_adapter_invalid_json",
                    "error_message": str(exc)[:2000],
                    "operation": operation,
                }
        if not isinstance(result, dict):
            return {
                "run_status": "infrastructure_error",
                "error_type": "android_adapter_non_object",
                "operation": operation,
            }
        return result

    def begin_attempt(self, case: dict[str, Any], attempt: dict[str, Any]) -> dict[str, Any]:
        return self._call("begin_attempt", {"case": case, "attempt": attempt})

    def execute_turn(self, candidate: dict[str, str], request: dict[str, Any]) -> dict[str, Any]:
        evidence_dir = Path(request["evidence_dir"])
        evidence_dir.mkdir(parents=True, exist_ok=True)
        result = self._call("execute_turn", {"candidate": candidate, "request": request})
        if result.get("run_status") == "infrastructure_error":
            result.setdefault("verdict", "infrastructure_error")
            result.setdefault("observation_complete", False)
        return result

    def recover_turn(self, execution: dict[str, Any]) -> dict[str, Any]:
        return self._call("recover_turn", {"execution": execution})


class StringPredicateEnvironment:
    """Execute invalid-bug Oracles without touching Android or an AVD."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.states: dict[str, str] = {}

    @staticmethod
    def _state_hash(payload: dict[str, Any]) -> str:
        return sha256_json(payload)

    def _verify_contract(
        self, case: dict[str, Any], oracle: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        if case.get("task") != "invalid_bug" or oracle.get("oracle_kind") != "string_predicate":
            raise ValueError("StringPredicateEnvironment received a non-predicate case")
        predicate = oracle.get("predicate", {})
        if predicate.get("true_means") != "bug_revealed":
            raise ValueError("string-predicate truth semantics are not frozen")
        if predicate.get("required_resource_ids") != case.get("required_resource_ids"):
            raise ValueError("string-predicate resource IDs differ from the case contract")
        validator_path = (self.repo_root / str(predicate.get("validator_path", ""))).resolve()
        if not validator_path.is_relative_to(self.repo_root) or not validator_path.is_file():
            raise ValueError("string-predicate validator path is unavailable")
        actual = hashlib.sha256(validator_path.read_bytes()).hexdigest()
        if actual != predicate.get("validator_sha256"):
            raise ValueError("string-predicate validator source hash drift")
        validator = get_validator(case["case_id"])
        if validator.case_id != case["case_id"] or validator.task != "invalid_bug":
            raise ValueError("registered validator identity differs from the case contract")
        return validator, predicate

    def begin_attempt(self, case: dict[str, Any], attempt: dict[str, Any]) -> dict[str, Any]:
        oracle = attempt["oracle"]
        self._verify_contract(case, oracle)
        attempt_id = attempt["attempt_id"]
        state = self._state_hash({
            "attempt_id": attempt_id,
            "case_id": case["case_id"],
            "oracle_version": oracle["version"],
            "turn": 0,
            "kind": "stateless_string_predicate",
        })
        self.states[attempt_id] = state
        return {
            "reset_verified": True,
            "target_page_verified": True,
            "remote_baseline": "not_applicable",
            "reset_id": "stateless-string-predicate-v1",
            "state_hash": state,
            "environment_kind": "string_predicate",
        }

    def execute_turn(self, candidate: dict[str, str], request: dict[str, Any]) -> dict[str, Any]:
        case = request["case"]
        oracle = request["oracle"]
        validator, predicate = self._verify_contract(case, oracle)
        if set(candidate) != set(case["required_resource_ids"]) or any(
            not isinstance(value, str) for value in candidate.values()
        ):
            raise ValueError("candidate differs from the exact string-predicate input contract")
        attempt_id = request["attempt_id"]
        before = self.states[attempt_id]
        passed = validator.validate(candidate)
        if not isinstance(passed, bool):
            raise TypeError("string-predicate validator did not return bool")
        candidate_hash = sha256_json(candidate)
        after = self._state_hash({
            "attempt_id": attempt_id,
            "previous_state_hash": before,
            "turn": request["turn"],
            "candidate_hash": candidate_hash,
            "verdict": passed,
        })
        self.states[attempt_id] = after
        evidence_dir = Path(request["evidence_dir"])
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = evidence_dir / "predicate_result.json"
        evidence_payload = {
            "schema_version": "e2-structure-v1-string-predicate-result-1",
            "case_id": case["case_id"],
            "oracle_version": oracle["version"],
            "validator_sha256": predicate["validator_sha256"],
            "candidate_sha256": candidate_hash,
            "predicate_result": passed,
            "true_means": "bug_revealed",
        }
        evidence_path.write_text(
            json.dumps(evidence_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        evidence = [{
            "path": evidence_path.name,
            "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            "kind": "predicate_result",
        }]
        return {
            "attempt_id": attempt_id,
            "verdict": "pass" if passed else "fail",
            "observation_complete": True,
            "state_before_hash": before,
            "state_after_hash": after,
            "evidence_hash": sha256_json(evidence),
            "evidence": evidence,
            # The runner emits the single public predicate-false diagnostic so
            # all environment implementations share one feedback contract.
            "diagnostics": [],
        }

    def recover_turn(self, execution: dict[str, Any]) -> dict[str, Any]:
        attempt_id = execution["attempt_id"]
        state = self._state_hash({
            "attempt_id": attempt_id,
            "previous_state_hash": execution["state_after_hash"],
            "turn": execution["turn"],
            "kind": "stateless_predicate_recovery",
        })
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
            "locator_refresh_hash": self._state_hash({"attempt_id": attempt_id, "kind": "not_applicable"}),
            "environment_kind": "string_predicate",
        }


class HybridEnvironment:
    """Route PPTR cases to Android and bug cases to frozen predicates."""

    def __init__(self, android: AndroidCommandEnvironment, predicate: StringPredicateEnvironment) -> None:
        self.android = android
        self.predicate = predicate

    @staticmethod
    def _is_predicate(case: dict[str, Any]) -> bool:
        return case.get("task") == "invalid_bug"

    def begin_attempt(self, case: dict[str, Any], attempt: dict[str, Any]) -> dict[str, Any]:
        environment = self.predicate if self._is_predicate(case) else self.android
        return environment.begin_attempt(case, attempt)

    def execute_turn(self, candidate: dict[str, str], request: dict[str, Any]) -> dict[str, Any]:
        environment = self.predicate if self._is_predicate(request["case"]) else self.android
        return environment.execute_turn(candidate, request)

    def recover_turn(self, execution: dict[str, Any]) -> dict[str, Any]:
        environment = self.predicate if self._is_predicate(execution["case"]) else self.android
        return environment.recover_turn(execution)
