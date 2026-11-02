"""P089-scoped Android adapter repair for delayed offline maintenance takeover.

TeamReach's frozen Create Group snapshot is initially valid, but the App can
replace it with its offline maintenance screen while a comparatively slow
subject-model request is in flight.  The candidate and all A-F semantics stay
unchanged.  Immediately before executing a P089 candidate, reload and verify
the same frozen snapshot once more, and retain that extra reset as evidence.

This wrapper is intentionally separate from ``abcdef_android_repaired`` so
already-running formal claims keep using the exact implementation hashed in
their manifests.  Only an explicitly qualified P089 recovery config should
select this module.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from . import abcdef_android_repaired as repaired
from .abcdef_contract import canonical_json


_CASE_ID = "P089"
_ORACLE_VERSION = "P089-live-20260929-v1"
_SERIAL = "emulator-5564"
_RESET_ID = "P089-local-snapshot-v1"
_NAME_BINDING = {
    "package": "com.teamreach.app",
    "resource-id": "create-team-name-input",
    "class": "android.widget.EditText",
}
_CODE_BINDING = {
    "package": "com.teamreach.app",
    "resource-id": "create-team-code-input",
    "class": "android.widget.EditText",
}


def _p089_request(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("operation") != "execute_turn":
        return None
    request = payload.get("request")
    case = request.get("case") if isinstance(request, dict) else None
    if not isinstance(case, dict) or case.get("case_id") != _CASE_ID:
        return None
    oracle = request.get("oracle")
    if not isinstance(oracle, dict):
        raise ValueError("P089 repaired adapter requires an App-observation Oracle")
    if (
        oracle.get("case_id") != _CASE_ID
        or oracle.get("version") != _ORACLE_VERSION
        or oracle.get("device_serial") != _SERIAL
        or oracle.get("begin_attempt", {}).get("reset_id") != _RESET_ID
        or oracle.get("binding", {}).get("com.teamreach.app:id/et_group_name")
        != _NAME_BINDING
        or oracle.get("binding", {}).get("com.teamreach.app:id/et_group_id")
        != _CODE_BINDING
    ):
        raise ValueError("P089 pre-execution reset requires the frozen reviewed contract")
    return request


def _repair_p089_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Use the qualified low-latency input channel for P089's React fields."""
    request = _p089_request(payload)
    if request is None:
        return payload, False
    repaired_payload = copy.deepcopy(payload)
    repaired_oracle = repaired_payload["request"]["oracle"]
    strategies = repaired_oracle["execute_turn"].setdefault("input_strategies", {})
    for resource_id in (
        "com.teamreach.app:id/et_group_name",
        "com.teamreach.app:id/et_group_id",
    ):
        existing = strategies.get(resource_id)
        if existing not in {None, "adb_shell_input"}:
            raise ValueError("P089 input strategy conflicts with the scoped repair")
        strategies[resource_id] = "adb_shell_input"
    return repaired_payload, True


def _p089_pre_execution_reset(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Reload the already-frozen P089 baseline after the model response."""
    request = _p089_request(payload)
    if request is None:
        return None
    case = request["case"]
    oracle = request["oracle"]

    from .abcdef_android import begin_attempt as adapter_begin_attempt

    reset_evidence_dir = Path(request["evidence_dir"]) / "pre_execution_reset"
    receipt = adapter_begin_attempt({
        "case": case,
        "attempt": {
            "oracle": oracle,
            "evidence_dir": str(reset_evidence_dir),
            "command_timeout_seconds": request.get("command_timeout_seconds", 600),
        },
    })
    if (
        receipt.get("reset_verified") is not True
        or receipt.get("target_page_verified") is not True
        or receipt.get("reset_id") != _RESET_ID
    ):
        raise RuntimeError("P089 pre-execution baseline reset was not verified")
    return receipt


def _attach_reset_evidence(
    result: dict[str, Any], receipt: dict[str, Any],
) -> dict[str, Any]:
    prefixed_evidence = [
        {
            **item,
            "path": f"pre_execution_reset/{item['path']}",
            "kind": "pre_execution_reset_xml",
        }
        for item in receipt.get("evidence", [])
    ]
    combined = [*prefixed_evidence, *result.get("evidence", [])]
    result["evidence"] = combined
    result["evidence_hash"] = hashlib.sha256(
        canonical_json(combined).encode("utf-8")
    ).hexdigest()
    result["pre_execution_reset_repair"] = {
        "repair_id": "P089-frozen-baseline-after-model-response-v1",
        "reset_verified": receipt.get("reset_verified"),
        "target_page_verified": receipt.get("target_page_verified"),
        "reset_id": receipt.get("reset_id"),
        "state_hash": receipt.get("state_hash"),
        "device_time": receipt.get("device_time"),
        "evidence": prefixed_evidence,
    }
    return result


def main() -> None:
    repaired.install_repairs()
    from .abcdef_android import dispatch as adapter_dispatch

    try:
        payload, b017_repaired = repaired._repair_b017_payload(json.load(sys.stdin))
        payload, p033_repaired = repaired._repair_p033_payload(payload)
        payload, p093_repaired = repaired._repair_p093_payload(payload)
        payload, p094_repaired = repaired._repair_p094_payload(payload)
        payload, p089_repaired = _repair_p089_payload(payload)
        reset_receipt = _p089_pre_execution_reset(payload)
        result = adapter_dispatch(payload)
        if reset_receipt is not None:
            result = _attach_reset_evidence(result, reset_receipt)
        if b017_repaired:
            result["hierarchy_transport_repair"] = (
                "B017-missing-frozen-xml-live-uiautomator2-v1"
            )
        if p033_repaired:
            result["input_strategy_repair"] = "P033-material-date-picker-v1"
        if p093_repaired:
            result["input_strategy_repair"] = "P093-masked-phone-suffix-v1"
        if p094_repaired:
            result["action_selector_repair"] = (
                "P094-prefix-stop-no-hierarchy-reread-v8"
            )
        if p089_repaired:
            result["input_strategy_repair"] = (
                "P089-adb-shell-input-after-model-reset-v1"
            )
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        sys.stderr.write(json.dumps(
            {"error_type": type(exc).__name__, "message": str(exc)},
            ensure_ascii=False,
        ))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
