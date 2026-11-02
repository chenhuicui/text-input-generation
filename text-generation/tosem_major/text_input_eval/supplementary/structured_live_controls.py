"""Run auditable local behavior controls for one inspected E2 Android case.

Each control arm starts from the frozen VM snapshot.  The negative arm is
followed by an in-attempt recovery that may press Back and clear inputs, but it
must not reload a snapshot, relaunch the App, or reset remote state.  Passing
this command proves only the local behavior/reset/recovery observations in the
result; remote-baseline applicability remains an explicit separate decision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .structured_android import (
    AndroidDriver, AppInputRejected, binding_nodes, canonical_json,
    logical_binding_value,
)
from .structured_live_calibration import (
    atomic_json,
    current_avd,
    ensure_backup,
    infer_avd,
    load_snapshot,
    read_json,
)
from .structured_snapshot_backup import sha256_file
from .structured_snapshot_reset import (
    isolate_external_network, push_prepare_files, run_adb_prepare,
)
from .structured_case_reset import reset as case_specific_reset
from .structured_visual_observer import observe_image


def state_hash(xml: str, foreground: dict[str, Any]) -> str:
    package = foreground.get("package")
    stable_foreground = {
        "package": package,
        "activity": foreground.get("activity"),
    }
    stable_keys = (
        "class", "resource-id", "text", "content-desc", "hint", "bounds",
        "clickable", "enabled", "checked", "selected", "focused",
    )
    app_nodes = [
        {key: node.get(key, "") for key in stable_keys}
        for node in ET.fromstring(xml).iter("node")
        if node.get("package") == package
    ]
    value = {"foreground": stable_foreground, "app_nodes": app_nodes}
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def capture(driver: AndroidDriver, output: Path, stem: str) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    xml = driver.hierarchy()
    foreground = driver.current_app()
    xml_path = output / f"{stem}.xml"
    png_path = output / f"{stem}.png"
    xml_path.write_text(xml, encoding="utf-8")
    driver.device.screenshot(str(png_path))
    return {
        "state_hash": state_hash(xml, foreground),
        "foreground": foreground,
        "xml": xml_path.name,
        "xml_sha256": sha256_file(xml_path),
        "png": png_path.name,
        "png_sha256": sha256_file(png_path),
    }


def connect_driver(serial: str) -> AndroidDriver:
    import uiautomator2 as u2

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            device = u2.connect(serial)
            return AndroidDriver(serial, device=device)
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(3)
    assert last_error is not None
    raise last_error


def wait_assertion(
    driver: AndroidDriver, contract: dict[str, Any], seconds: float,
    *, bindings: dict[str, Any] | None = None,
    candidate: dict[str, str] | None = None,
    candidate_text_package: str | None = None,
) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if driver.assertion(contract) and (
            bindings is None
            or candidate is not None and driver.candidate_bindings_match(bindings, candidate)
        ) and (
            candidate_text_package is None
            or candidate is not None
            and driver.candidate_texts_visible(candidate_text_package, candidate)
        ):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def wait_negative_observation(
    driver: AndroidDriver,
    goal: dict[str, Any],
    negative: dict[str, Any],
    seconds: float,
    *, bindings: dict[str, Any] | None = None,
    candidate: dict[str, str] | None = None,
    candidate_text_package: str | None = None,
) -> bool:
    """Wait for an asynchronous negative endpoint and reject any later drift."""
    deadline = time.monotonic() + seconds
    negative_seen = False
    while True:
        if driver.assertion(goal) and (
            bindings is None
            or candidate is not None and driver.candidate_bindings_match(bindings, candidate)
        ) and (
            candidate_text_package is None
            or candidate is not None
            and driver.candidate_texts_visible(candidate_text_package, candidate)
        ):
            return False
        current_negative = driver.assertion(negative)
        if negative_seen and not current_negative:
            return False
        negative_seen = negative_seen or current_negative
        if time.monotonic() >= deadline:
            return negative_seen
        time.sleep(0.5)


def restore(
    serial: str, avd: str, snapshot: Path, output: Path, stem: str,
    network_mode: str, prepare_action: list[dict[str, Any]] | None = None,
    force_cold: bool = False,
    prepare_files: list[dict[str, Any]] | None = None,
    case_reset: dict[str, Any] | None = None,
    prepare_adb_actions: list[dict[str, Any]] | None = None,
    prepare_adb_assertion: dict[str, Any] | None = None,
) -> tuple[
    AndroidDriver, str, dict[str, Any], dict[str, Any] | None,
    dict[str, Any] | None,
]:
    reset_receipt: dict[str, Any] | None = None
    if case_reset is not None:
        reset_receipt = case_specific_reset(
            Path(case_reset["manifest_path"]), serial, avd,
            case_reset["reset_id"], {},
        )
        strategy = "case_specific_reset"
    else:
        strategy = load_snapshot(
            serial, avd, snapshot, output / f"{stem}_emulator.log",
            force_cold=force_cold,
        )
    push_prepare_files(serial, prepare_files)
    network_isolation = isolate_external_network(serial) if network_mode == "offline" else None
    run_adb_prepare(serial, prepare_adb_actions, prepare_adb_assertion)
    driver = connect_driver(serial)
    if prepare_action:
        driver.actions(prepare_action, stop_is_error=True)
    return (
        driver, strategy, capture(driver, output, f"{stem}_baseline"),
        network_isolation, reset_receipt,
    )


def bindings_visible(driver: AndroidDriver, bindings: dict[str, dict[str, str]]) -> bool:
    try:
        return all(driver.binding_object(selector) is not None for selector in bindings.values())
    except RuntimeError:
        return False


def recover_without_reset(
    driver: AndroidDriver,
    bindings: dict[str, dict[str, str]],
    baseline_values: dict[str, str],
    package: str,
    clear_assertion: dict[str, Any],
    max_back_presses: int,
    recovery_action: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    presses = 0
    if recovery_action:
        driver.actions(recovery_action, stop_is_error=True)
        # Modal/page transitions can populate Accessibility just after the
        # click returns. Do not immediately press Back and dismiss the very
        # surface the reviewed recovery action opened.
        for _ in range(4):
            if bindings_visible(driver, bindings):
                break
            time.sleep(0.5)
    while not bindings_visible(driver, bindings):
        if presses >= max_back_presses:
            raise RuntimeError("input page was not recovered within the frozen Back budget")
        driver.device.press("back")
        presses += 1
        time.sleep(0.5)
    for resource_id, selector in bindings.items():
        driver.set_binding_text(selector, baseline_values[resource_id])
    cleared = driver.binding_assertion(clear_assertion)
    foreground = driver.current_app()
    if not cleared or foreground.get("package") != package:
        raise RuntimeError("within-attempt recovery assertions failed")
    return {
        "snapshot_loaded": False,
        "remote_reset": False,
        "renavigated_from_baseline": False,
        "natural_state_preserved": True,
        "back_presses": presses,
        "input_clear_assertion": clear_assertion,
        "recovery_action": recovery_action or [],
        "input_restore_values": baseline_values,
    }


def baseline_restore_contract(
    bindings: dict[str, dict[str, str]], baseline_xml: Path,
    driver: AndroidDriver | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    root = ET.fromstring(baseline_xml.read_text(encoding="utf-8"))
    targets = []
    baseline_values: dict[str, str] = {}
    for resource_id, selector in bindings.items():
        nodes = binding_nodes(root, selector)
        if len(nodes) != 1 and driver is not None:
            # Long native forms expose only the current viewport in one XML
            # dump.  Resolve the stable binding through the App-owned
            # ScrollView, then read that field from the newly visible tree.
            driver.binding_object(selector)
            nodes = binding_nodes(ET.fromstring(driver.hierarchy()), selector)
        if len(nodes) != 1:
            raise RuntimeError("baseline binding is not unique")
        text = nodes[0].get("text", "")
        hint = nodes[0].get("hint", "")
        baseline_values[resource_id] = logical_binding_value(text, hint)
        logical_text = baseline_values[resource_id]
        target: dict[str, Any] = {"selector": selector, "exists": True}
        # Assert the logical field value, not the widget's transient hint
        # encoding.  Some editors expose ``text == hint`` while unfocused but
        # expose both as empty immediately after programmatic clearing; both
        # states represent the same frozen empty baseline.
        target.update({"attribute": "text", "equals": logical_text})
        targets.append(target)
    return {"mode": "all", "targets": targets}, baseline_values


def run_controls(
    case: dict[str, Any], inspection: dict[str, Any], plan: dict[str, Any],
    serial: str, avd: str, snapshot: Path, output: Path,
) -> dict[str, Any]:
    started = datetime.now(timezone.utc).isoformat()
    bindings = inspection["binding"]
    goal = plan.get("goal_assertion", inspection["goal_assertion"])
    verify_action = plan.get("verify_action", inspection["verify_action"])
    positive = plan["positive_control"]
    negative = plan["negative_control"]
    required = set(case["required_resource_ids"])
    if inspection.get("status") != "inspect_ready":
        raise ValueError("live inspection is not ready for behavior controls")
    if set(bindings) != required or set(positive) != required or set(negative) != required:
        raise ValueError("control fields must exactly match the inspected bindings")
    if any(not isinstance(value, str) for value in [*positive.values(), *negative.values()]):
        raise ValueError("control values must be strings")
    input_strategies = plan.get("input_strategies", {})
    if (
        not isinstance(input_strategies, dict)
        or not set(input_strategies).issubset(required)
        or any(
            strategy not in {"adb_key_events", "masked_phone_suffix"}
            for strategy in input_strategies.values()
        )
    ):
        raise ValueError(
            "input_strategies must reference bindings and use a supported strategy"
        )
    input_rejection_is_negative = plan.get("input_rejection_is_negative", False)
    if not isinstance(input_rejection_is_negative, bool):
        raise ValueError("input_rejection_is_negative must be boolean")
    if input_rejection_is_negative and not any(negative.values()):
        raise ValueError(
            "input rejection controls require a non-empty negative"
        )
    candidate_binding_goal = plan.get("candidate_binding_goal", False)
    if not isinstance(candidate_binding_goal, bool):
        raise ValueError("candidate_binding_goal must be boolean")
    candidate_text_goal = plan.get("candidate_text_goal", False)
    if not isinstance(candidate_text_goal, bool):
        raise ValueError("candidate_text_goal must be boolean")
    seconds = float(plan.get("observation_seconds", 10))
    if not 0 < seconds <= 60:
        raise ValueError("observation_seconds must be in (0,60]")
    network_mode = plan.get("network_mode", "online")
    if network_mode not in {"online", "offline"}:
        raise ValueError("network_mode must be online or offline")
    if network_mode == "offline" and plan.get("remote_baseline_status") != "not_applicable":
        raise ValueError("offline controls require an explicit not_applicable remote baseline")
    case_reset = plan.get("case_specific_reset")
    if case_reset is not None:
        expected_manifest = case.get("mirror", {}).get("manifest_path")
        expected_reset_id = case.get("mirror", {}).get("attempt_reset", {}).get("reset_id")
        if (
            not isinstance(case_reset, dict)
            or case_reset.get("manifest_path") != expected_manifest
            or case_reset.get("reset_id") != expected_reset_id
            or plan.get("remote_baseline_status") != "verified"
            or network_mode != "online"
        ):
            raise ValueError("case-specific reset differs from the frozen registry/remote contract")

    positive_driver, positive_strategy, positive_baseline, positive_network, positive_reset = restore(
        serial, avd, snapshot, output, "positive", network_mode,
        plan.get("prepare_action"), bool(plan.get("force_cold_reset", False)),
        plan.get("prepare_files"), case_reset,
        plan.get("prepare_adb_actions"), plan.get("prepare_adb_assertion"),
    )
    if positive_driver.assertion(goal):
        raise RuntimeError("goal already holds at the positive-control baseline")
    positive_driver.fill(bindings, positive, input_strategies)
    positive_driver.actions(verify_action, stop_is_error=True)
    visual_observer = plan.get("visual_text_observer")
    if input_rejection_is_negative and visual_observer is not None:
        raise ValueError("input rejection controls cannot use the visual observer")
    if visual_observer is not None:
        positive_after = capture(positive_driver, output, "positive_after")
        positive_visual = observe_image(
            output / positive_after["png"], visual_observer,
        )
        positive_passed = positive_visual["passed"]
    else:
        positive_passed = wait_assertion(
            positive_driver, goal, seconds,
            bindings=bindings if candidate_binding_goal else None,
            candidate_text_package=(case["mirror"]["package"] if candidate_text_goal else None),
            candidate=positive if candidate_binding_goal or candidate_text_goal else None,
        )
        positive_after = capture(positive_driver, output, "positive_after")
    if not positive_passed:
        raise RuntimeError("positive control did not satisfy the goal assertion")

    remote_rollback: dict[str, Any] | None = None
    rollback_candidate = plan.get("remote_rollback_control")
    if rollback_candidate is not None:
        if (
            not isinstance(rollback_candidate, dict)
            or set(rollback_candidate) != required
            or any(not isinstance(value, str) for value in rollback_candidate.values())
        ):
            raise ValueError("remote rollback fields must exactly match the inspected bindings")
        rollback_driver, rollback_strategy, rollback_baseline, _, _ = restore(
            serial, avd, snapshot, output, "remote_rollback", network_mode,
            plan.get("prepare_action"), bool(plan.get("force_cold_reset", False)),
            plan.get("prepare_files"), case_reset,
            plan.get("prepare_adb_actions"), plan.get("prepare_adb_assertion"),
        )
        rollback_driver.fill(bindings, rollback_candidate, input_strategies)
        rollback_driver.actions(verify_action, stop_is_error=True)
        rollback_passed = wait_assertion(
            rollback_driver, goal, seconds,
            bindings=bindings if candidate_binding_goal else None,
            candidate=rollback_candidate if candidate_binding_goal or candidate_text_goal else None,
            candidate_text_package=(case["mirror"]["package"] if candidate_text_goal else None),
        )
        rollback_after = capture(rollback_driver, output, "remote_rollback_after")
        if not rollback_passed:
            raise RuntimeError("remote rollback control did not satisfy the goal assertion")
        remote_rollback = {
            "candidate": rollback_candidate,
            "load_strategy": rollback_strategy,
            "goal_observed": rollback_passed,
            "baseline": rollback_baseline,
            "after": rollback_after,
            "scope": "App-observed rollback submission; provider-side persistence remains unproven",
        }

    negative_driver, negative_strategy, negative_baseline, negative_network, negative_reset = restore(
        serial, avd, snapshot, output, "negative", network_mode,
        plan.get("prepare_action"), bool(plan.get("force_cold_reset", False)),
        plan.get("prepare_files"), case_reset,
        plan.get("prepare_adb_actions"), plan.get("prepare_adb_assertion"),
    )
    # Check the goal before constructing the restore contract.  Long forms may
    # scroll while resolving offscreen bindings; that viewport movement must
    # not turn an offscreen error message into a false baseline success.
    if negative_driver.assertion(goal):
        raise RuntimeError("goal already holds at the negative-control baseline")
    clear_assertion, baseline_values = baseline_restore_contract(
        bindings, output / negative_baseline["xml"], negative_driver,
    )
    reset_verified = (
        positive_baseline["foreground"].get("package")
        == negative_baseline["foreground"].get("package")
        == case["mirror"]["package"]
        and positive_baseline["foreground"].get("activity")
        == negative_baseline["foreground"].get("activity")
        and negative_driver.binding_assertion(clear_assertion)
    )
    if not reset_verified:
        raise RuntimeError("independent snapshot baseline did not restore the target input contract")
    negative_input_rejected = False
    try:
        negative_driver.fill(bindings, negative, input_strategies)
    except AppInputRejected:
        if not input_rejection_is_negative:
            raise
        negative_input_rejected = True
    if not negative_input_rejected:
        negative_driver.actions(verify_action)
    if negative_input_rejected:
        negative_goal = negative_driver.assertion(goal)
        # A calibrated AppInputRejected result is itself the negative
        # observation.  Requiring an additional page assertion can be
        # unstable because the rejecting editor may leave the IME open and
        # move an unrelated diagnostic node outside the accessibility tree.
        negative_observed = True
        negative_after = capture(negative_driver, output, "negative_after")
    elif visual_observer is not None:
        negative_after = capture(negative_driver, output, "negative_after")
        negative_visual = observe_image(
            output / negative_after["png"], visual_observer,
        )
        negative_goal = negative_visual["passed"]
        negative_observed = not negative_goal
    else:
        negative_observed = wait_negative_observation(
            negative_driver, goal, plan["negative_assertion"], seconds,
            bindings=bindings if candidate_binding_goal else None,
            candidate=negative if candidate_binding_goal or candidate_text_goal else None,
            candidate_text_package=(case["mirror"]["package"] if candidate_text_goal else None),
        )
        negative_goal = negative_driver.assertion(goal) and (
            not candidate_binding_goal
            or negative_driver.candidate_bindings_match(bindings, negative)
        ) and (
            not candidate_text_goal
            or negative_driver.candidate_texts_visible(case["mirror"]["package"], negative)
        )
        negative_after = capture(negative_driver, output, "negative_after")
    if (negative_goal and not negative_input_rejected) or not negative_observed:
        raise RuntimeError("negative control did not produce the frozen negative observation")

    recovery_values = baseline_values
    recovery_clear_assertion = clear_assertion
    recovery_override = plan.get("recovery_baseline_values")
    if recovery_override is not None:
        if (
            not isinstance(recovery_override, dict)
            or set(recovery_override) != required
            or any(not isinstance(value, str) for value in recovery_override.values())
        ):
            raise ValueError(
                "recovery_baseline_values must exactly match the inspected bindings"
            )
        recovery_values = recovery_override
        recovery_clear_assertion = plan.get("recovery_clear_assertion", {
            "mode": "all",
            "targets": [
                {
                    "selector": selector,
                    "exists": True,
                    "attribute": "text",
                    "equals": recovery_values[resource_id],
                }
                for resource_id, selector in bindings.items()
            ],
        })
        if not isinstance(recovery_clear_assertion, dict):
            raise ValueError("recovery_clear_assertion must be an assertion object")
    recovery = recover_without_reset(
        negative_driver, bindings, recovery_values, case["mirror"]["package"],
        recovery_clear_assertion,
        int(plan.get("max_back_presses", 3)),
        plan.get("recovery_action"),
    )
    recovery_after = capture(negative_driver, output, "recovery_after")
    return {
        "schema_version": "e2-structure-v1-live-controls-1",
        "case_id": case["case_id"],
        "status": "controls_passed_local_only",
        "started_at_utc": started,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "device": serial,
        "avd": current_avd(serial),
        "snapshot": snapshot.name,
        "inspection_sha256": sha256_file(Path(inspection["_path"])),
        "plan_sha256": sha256_file(Path(plan["_path"])),
        "verify_action": verify_action,
        "prepare_action": plan.get("prepare_action", []),
        "prepare_files": plan.get("prepare_files", []),
        "prepare_adb_actions": plan.get("prepare_adb_actions", []),
        "prepare_adb_assertion": plan.get("prepare_adb_assertion"),
        "input_strategies": input_strategies,
        "input_rejection_is_negative": input_rejection_is_negative,
        "candidate_binding_goal": candidate_binding_goal,
        "candidate_text_goal": candidate_text_goal,
        "case_specific_reset": case_reset,
        "positive_control": {
            "candidate": positive,
            "goal_assertion": goal,
            "load_strategy": positive_strategy,
            "goal_observed": positive_passed,
            "baseline": positive_baseline,
            "after": positive_after,
        },
        "negative_control": {
            "candidate": negative,
            "load_strategy": negative_strategy,
            "goal_observed": negative_goal,
            "negative_observation": negative_observed,
            "input_rejected": negative_input_rejected,
            "negative_assertion": plan["negative_assertion"],
            "baseline": negative_baseline,
            "after": negative_after,
        },
        "visual_observer": (
            {
                "config": visual_observer,
                "positive": positive_visual,
                "negative": negative_visual,
            }
            if visual_observer is not None else None
        ),
        "independent_attempt_reset": {
            "reset_verified": reset_verified,
            "kind": "case_specific_remote" if case_reset is not None else "local_vm_snapshot",
            "positive_receipt": positive_reset,
            "negative_receipt": negative_reset,
            "baseline_state_hash": positive_baseline["state_hash"],
            "repeat_state_hash": negative_baseline["state_hash"],
            "exact_state_hash_equal": (
                positive_baseline["state_hash"] == negative_baseline["state_hash"]
            ),
            "verification_policy": "foreground_activity_and_frozen_input_contract",
        },
        "within_attempt_recovery": {**recovery, "verified": True, "after": recovery_after},
        "remote_rollback": remote_rollback,
        "remote_baseline": {
            "status": plan.get("remote_baseline_status", "decision_required"),
            "note": plan.get("remote_baseline_note"),
        },
        "network_isolation": {
            "mode": network_mode,
            "positive": positive_network,
            "negative": negative_network,
        },
        "formal_oracle_promoted": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run live positive/negative/reset/recovery controls")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--inspection", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--avd", required=True)
    args = parser.parse_args()

    registry_path = args.registry.resolve()
    ensure_backup(args.backup_manifest.resolve(), registry_path)
    registry = read_json(registry_path)
    inspection_path = args.inspection.resolve()
    plan_path = args.plan.resolve()
    inspection = read_json(inspection_path)
    plan = read_json(plan_path)
    inspection["_path"] = str(inspection_path)
    plan["_path"] = str(plan_path)
    if inspection.get("case_id") != plan.get("case_id"):
        raise ValueError("inspection and plan case IDs differ")
    case = next(
        (item for item in registry["cases"] if item["case_id"] == inspection["case_id"]), None
    )
    if case is None:
        raise ValueError("control case is absent from the registry")
    avd, snapshot = infer_avd(args.source_root.resolve(), case)
    if avd != args.avd or current_avd(args.device) != args.avd:
        raise RuntimeError("running AVD does not match the control case")
    output = args.output.resolve()
    try:
        result = run_controls(case, inspection, plan, args.device, args.avd, snapshot, output)
    except Exception as exc:
        result = {
            "schema_version": "e2-structure-v1-live-controls-1",
            "case_id": case["case_id"],
            "status": "control_error",
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "error_type": type(exc).__name__,
            "error": str(exc) or repr(exc),
            "formal_oracle_promoted": False,
        }
    atomic_json(output / "control_result.json", result)
    print(json.dumps(result, ensure_ascii=False))
    if result["status"] != "controls_passed_local_only":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
