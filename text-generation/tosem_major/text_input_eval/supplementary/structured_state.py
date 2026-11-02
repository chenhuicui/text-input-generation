"""Fail-closed event-chain contract for E2-structure-v1 App attempts.

This module does not drive Android.  It defines and validates the receipts a
real adapter must emit for begin_attempt, execute_turn, and recover_turn.
"""
from __future__ import annotations

from typing import Any, Protocol

VERDICTS = {"pass", "fail", "unknown", "infrastructure_error"}
ARMS = {"A", "B", "C", "D"}
OUTPUT_STATUSES = {"valid", "parse_error", "schema_error", "refusal", "infrastructure_error"}


class StructuredEnvironment(Protocol):
    def begin_attempt(self, case: dict[str, Any], attempt: dict[str, Any]) -> dict[str, Any]: ...

    def execute_turn(self, candidate: dict[str, str], request: dict[str, Any]) -> dict[str, Any]: ...

    def recover_turn(self, execution: dict[str, Any]) -> dict[str, Any]: ...


def fresh_model_payload(model: str, prompt: str) -> dict[str, str]:
    """Create a gateway request with no provider-side conversation cursor."""
    if not model or not prompt:
        raise ValueError("model and prompt must be non-empty")
    return {"modelType": model, "id": "", "message": prompt}


def _nonempty(event: dict[str, Any], *keys: str) -> None:
    missing = [key for key in keys if not isinstance(event.get(key), str) or not event[key]]
    if missing:
        raise ValueError(f"missing non-empty fields: {missing}")


def _validate_begin(event: dict[str, Any]) -> None:
    _nonempty(event, "attempt_id", "case_id", "arm", "model", "initial_state_hash", "oracle_version")
    if event["arm"] not in ARMS:
        raise ValueError("unknown arm")
    if not isinstance(event.get("replicate"), int) or event["replicate"] < 1:
        raise ValueError("replicate must be a positive integer")
    receipt = event.get("reset_receipt")
    if not isinstance(receipt, dict):
        raise ValueError("attempt_begin requires reset_receipt")
    if receipt.get("reset_verified") is not True or receipt.get("target_page_verified") is not True:
        raise ValueError("independent attempt reset and target page must be verified")
    if receipt.get("remote_baseline") not in {"verified", "not_applicable"}:
        raise ValueError("remote baseline must be verified or explicitly not applicable")
    _nonempty(receipt, "reset_id", "state_hash")
    if receipt["state_hash"] != event["initial_state_hash"]:
        raise ValueError("begin receipt state hash mismatch")


def _validate_request(event: dict[str, Any], model: str, expected_turn: int) -> None:
    if event.get("turn") != expected_turn:
        raise ValueError("model request turn is not sequential")
    _nonempty(event, "request_id", "prompt_hash", "visible_history_hash")
    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("model_request requires payload")
    if set(payload) != {"modelType", "id", "message"}:
        raise ValueError("model payload fields differ from the frozen gateway contract")
    if payload.get("modelType") != model or payload.get("id") != "" or not payload.get("message"):
        raise ValueError("each request must be a fresh explicit-history model session")


def _validate_execution(event: dict[str, Any], request_id: str, oracle_version: str, expected_turn: int) -> None:
    if event.get("turn") != expected_turn or event.get("request_id") != request_id:
        raise ValueError("execution is not bound to the current request")
    if event.get("oracle_version") != oracle_version:
        raise ValueError("execution oracle version drift")
    _nonempty(event, "candidate_hash", "state_before_hash", "state_after_hash", "evidence_hash")
    if event.get("verdict") not in VERDICTS:
        raise ValueError("invalid execution verdict")
    if event["verdict"] in {"pass", "fail"} and event.get("observation_complete") is not True:
        raise ValueError("semantic verdict requires complete observation")
    if event["verdict"] in {"unknown", "infrastructure_error"} and event.get("observation_complete") is True:
        raise ValueError("unknown/infrastructure verdict must remain non-semantic")


def _validate_output_check(event: dict[str, Any], request_id: str, expected_turn: int) -> None:
    if event.get("turn") != expected_turn or event.get("request_id") != request_id:
        raise ValueError("output_check is not bound to the current request")
    if event.get("output_status") not in OUTPUT_STATUSES:
        raise ValueError("invalid output status")
    if event["output_status"] == "valid":
        _nonempty(event, "candidate_hash")
    elif event.get("candidate_hash") is not None:
        raise ValueError("rejected output must not claim a candidate hash")


def _validate_recovery(event: dict[str, Any], expected_turn: int, expected_before_hash: str) -> None:
    if event.get("turn") != expected_turn:
        raise ValueError("recovery turn mismatch")
    forbidden_truthy = ["snapshot_loaded", "remote_reset", "renavigated_from_baseline"]
    if any(event.get(key) is not False for key in forbidden_truthy):
        raise ValueError("within-attempt recovery must not reset or reload the baseline")
    required_true = ["natural_state_preserved", "target_page_verified", "input_cleared"]
    if any(event.get(key) is not True for key in required_true):
        raise ValueError("recovery did not preserve state and restore the input page")
    _nonempty(event, "state_before_hash", "state_hash", "locator_refresh_hash")
    if event["state_before_hash"] != expected_before_hash:
        raise ValueError("recovery did not continue from the failed execution state")


def validate_event_chain(events: list[dict[str, Any]], budget: int) -> dict[str, Any]:
    """Validate one arm trajectory and return a compact audit summary."""
    if budget < 1 or not events:
        raise ValueError("budget and events must be non-empty")
    if events[0].get("event") != "attempt_begin" or events[-1].get("event") != "trajectory_end":
        raise ValueError("event chain must begin with attempt_begin and end with trajectory_end")
    if sum(event.get("event") == "attempt_begin" for event in events) != 1:
        raise ValueError("an independent attempt may reset exactly once")
    begin = events[0]
    _validate_begin(begin)
    expected = "model_request"
    turn = 1
    last_request_id = ""
    last_verdict: str | None = None
    last_output_status: str | None = None
    expected_state_hash = begin["initial_state_hash"]
    failed_state_hash = ""
    model_calls = 0
    executions = 0
    recoveries = 0
    for event in events[1:-1]:
        kind = event.get("event")
        if expected == "recovery_or_end":
            expected = "recovery"
        if expected == "output_check_or_execution" and kind not in {"output_check", "execution"}:
            raise ValueError(f"expected output_check or execution, received {kind}")
        if expected != "output_check_or_execution" and kind != expected:
            raise ValueError(f"expected {expected}, received {kind}")
        if kind == "model_request":
            _validate_request(event, begin["model"], turn)
            last_request_id = event["request_id"]
            model_calls += 1
            expected = "output_check_or_execution"
        elif kind == "output_check":
            _validate_output_check(event, last_request_id, turn)
            last_output_status = event["output_status"]
            if last_output_status == "valid":
                expected = "execution"
            elif last_output_status == "infrastructure_error":
                expected = "trajectory_end"
            elif turn < budget:
                turn += 1
                expected = "model_request"
            else:
                expected = "trajectory_end"
        elif kind == "execution":
            _validate_execution(event, last_request_id, begin["oracle_version"], turn)
            if event["state_before_hash"] != expected_state_hash:
                raise ValueError("execution did not continue from the verified current App state")
            last_output_status = "valid"
            executions += 1
            last_verdict = event["verdict"]
            failed_state_hash = event["state_after_hash"]
            if last_verdict == "fail" and turn < budget:
                expected = "recovery_or_end"
            else:
                expected = "trajectory_end"
        elif kind == "recovery":
            _validate_recovery(event, turn, failed_state_hash)
            expected_state_hash = event["state_hash"]
            recoveries += 1
            turn += 1
            expected = "model_request"
        else:
            raise ValueError(f"unsupported event: {kind}")
    end = events[-1]
    if expected in {"recovery", "recovery_or_end"}:
        expected = "trajectory_end"
    if expected != "trajectory_end":
        raise ValueError(f"trajectory ended while expecting {expected}")
    if end.get("turns_executed") != model_calls or not isinstance(end.get("reason"), str) or not end["reason"]:
        raise ValueError("trajectory_end summary mismatch")
    status = end.get("status")
    if last_verdict == "pass" and status != "completed_success":
        raise ValueError("pass must end as completed_success")
    if last_verdict == "fail":
        expected_status = "completed_failure" if model_calls == budget else "incomplete"
        if status != expected_status:
            raise ValueError("fail status does not match budget/recovery outcome")
    if last_verdict in {"unknown", "infrastructure_error"} and status != "incomplete":
        raise ValueError("unknown and infrastructure outcomes must remain incomplete")
    if last_output_status in {"parse_error", "schema_error", "refusal"}:
        expected_status = "completed_failure" if model_calls == budget else "incomplete"
        if status != expected_status:
            raise ValueError("rejected output status does not match budget")
    if last_output_status == "infrastructure_error" and status != "incomplete":
        raise ValueError("model infrastructure outcomes must remain incomplete")
    return {
        "attempt_id": begin["attempt_id"],
        "case_id": begin["case_id"],
        "arm": begin["arm"],
        "model": begin["model"],
        "replicate": begin["replicate"],
        "model_calls": model_calls,
        "executions": executions,
        "recoveries": recoveries,
        "final_verdict": last_verdict,
        "status": status,
    }
