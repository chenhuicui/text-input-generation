"""Generic no-reset recovery command for E2 Android attempts.

The command may press Back only until the frozen target-page assertion holds,
then dynamically relocates and clears every bound input.  It never loads a VM
snapshot, relaunches the App, or resets remote state.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from .structured_android import (
    AndroidDriver,
    _device_serial,
    assertion_in_hierarchy,
    canonical_json,
)


def recover(
    payload: dict[str, Any], max_back_presses: int,
    recovery_action: list[dict[str, Any]] | None = None,
    recovery_values: dict[str, str] | None = None,
    *, device: Any | None = None,
) -> dict[str, Any]:
    execution = payload["execution"]
    oracle = execution["oracle"]
    contract = oracle["recover_turn"]
    driver = AndroidDriver(_device_serial(execution["case"], oracle), device=device)
    presses = 0
    if recovery_action:
        driver.actions(recovery_action, stop_is_error=True)
    hierarchy = driver.hierarchy()
    while not assertion_in_hierarchy(hierarchy, contract["target_page_assertion"]):
        if presses >= max_back_presses:
            raise RuntimeError("target page was not recovered within the frozen Back budget")
        driver.device.press("back")
        presses += 1
        time.sleep(0.5)
        hierarchy = driver.hierarchy()
    frozen_values: dict[str, str] = {}
    for target in contract["input_clear_assertion"].get("targets", []):
        selector = target.get("selector")
        if not isinstance(selector, dict):
            continue
        value = ""
        if target.get("attribute") == "text" and isinstance(target.get("equals"), str):
            value = target["equals"]
        frozen_values[canonical_json(selector)] = value
    if recovery_values is not None and (
        set(recovery_values) != set(oracle["binding"])
        or any(not isinstance(value, str) for value in recovery_values.values())
    ):
        raise ValueError("recovery values must exactly match Oracle bindings")
    # Avoid focusing or rewriting fields when the failed turn already left the
    # complete frozen baseline in place.  Some legacy Apps treat even a no-op
    # empty write as a navigation event and leave the otherwise recoverable
    # input page.  The live assertion is the authoritative check here; a
    # mismatching baseline still takes the normal explicit restore path below.
    if not assertion_in_hierarchy(hierarchy, contract["input_clear_assertion"]):
        for resource_id, selector in oracle["binding"].items():
            driver.set_binding_text(
                selector,
                (
                    recovery_values[resource_id]
                    if recovery_values is not None
                    else frozen_values.get(canonical_json(selector), "")
                ),
            )
    if not driver.binding_assertion(contract["input_clear_assertion"]):
        raise RuntimeError("input clear assertion failed after recovery")
    return {
        "snapshot_loaded": False,
        "remote_reset": False,
        "renavigated_from_baseline": False,
        "natural_state_preserved": True,
        "back_presses": presses,
        "recovery_action": recovery_action or [],
        "input_restore_values": recovery_values,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover one E2 turn without resetting its attempt")
    parser.add_argument("--max-back-presses", type=int, default=3)
    parser.add_argument("--recovery-action-json")
    parser.add_argument("--recovery-values-json")
    args = parser.parse_args()
    if not 0 <= args.max_back_presses <= 10:
        raise SystemExit("max-back-presses must be between 0 and 10")
    try:
        recovery_action = (
            json.loads(args.recovery_action_json)
            if args.recovery_action_json is not None else None
        )
        if recovery_action is not None and not isinstance(recovery_action, list):
            raise ValueError("recovery action must be a JSON action list")
        recovery_values = (
            json.loads(args.recovery_values_json)
            if args.recovery_values_json is not None else None
        )
        if recovery_values is not None and not isinstance(recovery_values, dict):
            raise ValueError("recovery values must be a JSON object")
        result = recover(
            json.load(sys.stdin), args.max_back_presses,
            recovery_action, recovery_values,
        )
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        sys.stderr.write(json.dumps({"error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
