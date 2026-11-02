"""Recalibrate stale PPTR actions and outcome assertions on frozen snapshots.

The historical YAML files are useful provenance, but some of their coordinate
actions refer to a page that is reached only after an earlier action and some
success strings no longer exist in the installed App build.  This tool keeps
the frozen snapshot, App artifact, field binding, and positive/negative inputs
unchanged.  It resolves every legacy action against the UI that is current at
that exact step, runs both control arms, and freezes one App-owned endpoint
that is true only for the positive arm.

The result is still only a candidate.  ``structured_live_controls`` must rerun
the generated inspection/plan and ``structured_oracle_promotion`` must pass its
formal adapter smoke before the case becomes a formal Oracle.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import yaml

from .structured_android import AndroidDriver, AppInputRejected
from .structured_live_calibration import (
    assertion,
    atomic_json,
    coordinate_selector,
    current_avd,
    ensure_backup,
    infer_avd,
    node_matches,
    read_json,
    selector_for_target,
)
from .structured_live_controls import capture, connect_driver, restore, wait_assertion
from .structured_snapshot_backup import sha256_file
from .structured_visual_observer import observe_image


def _root(driver: AndroidDriver) -> ET.Element:
    return ET.fromstring(driver.hierarchy())


def _wait_selector(
    driver: AndroidDriver,
    package: str,
    action: dict[str, Any],
    seconds: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.monotonic() + seconds
    last_detail: dict[str, Any] = {}
    while True:
        root = _root(driver)
        action_type = action.get("type")
        if action_type == "coordinate":
            width, height = driver.device.window_size()
            selector, last_detail = coordinate_selector(
                root, action, package, int(width), int(height)
            )
        else:
            selector = selector_for_target(
                root,
                package,
                str(action.get("target", "")),
                str(action.get("by") or action_type or "text"),
            )
            last_detail = {"target": action.get("target"), "by": action.get("by")}
            if selector is not None:
                matches = sum(
                    node_matches(node, selector) for node in root.iter("node")
                )
                # ``selector_for_target`` deliberately returns an exact
                # deferred selector for zero matches.  Let AndroidDriver hide
                # the IME and scroll before deciding it is absent.  Multiple
                # matches remain ambiguous and must not be frozen.
                if matches > 1:
                    selector = None
        if selector is not None:
            return selector, last_detail
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "legacy action has no unique live selector at its execution step: "
                f"{json.dumps(last_detail, ensure_ascii=False, sort_keys=True)}"
            )
        time.sleep(0.5)


def translate_and_execute(
    driver: AndroidDriver,
    package: str,
    legacy_actions: list[dict[str, Any]],
    output: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not legacy_actions:
        raise ValueError("legacy verify_action is empty")
    frozen: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    for index, legacy in enumerate(legacy_actions):
        kind = legacy.get("action")
        action_type = legacy.get("type")
        detail: dict[str, Any] = {}
        if kind == "wait":
            seconds = float(legacy.get("seconds", 0))
            if not 0 < seconds <= 30:
                raise ValueError(f"invalid wait action at index {index}: {legacy}")
            current = {"action": "wait", "seconds": seconds}
        elif kind == "press" and legacy.get("key") in {"enter", "back"}:
            current = {"action": "press", "key": legacy["key"]}
        elif kind == "press_if_present" and isinstance(legacy.get("selector"), dict):
            key = legacy.get("key")
            if key not in {"enter", "back"}:
                raise ValueError(f"invalid conditional press at index {index}: {legacy}")
            current = {
                "action": "press_if_present",
                "selector": legacy["selector"],
                "key": key,
            }
        elif kind == "click" and action_type in {"enter", "back"}:
            current: dict[str, Any] = {"action": "press", "key": action_type}
        elif kind == "click_if_present" and isinstance(legacy.get("selector"), dict):
            current = {"action": "click_if_present", "selector": legacy["selector"]}
        elif kind in {"click", "double_click", "long_click"} and isinstance(
            legacy.get("selector"), dict
        ):
            current = {"action": kind, "selector": legacy["selector"]}
            current["if_missing"] = "stop"
        elif kind in {"click", "double_click", "long_click"} and (
            action_type == "coordinate" or legacy.get("target")
        ):
            timeout = float(legacy.get("timeout", 5))
            selector, detail = _wait_selector(driver, package, legacy, timeout)
            current = {"action": kind, "selector": selector}
            current["if_missing"] = "stop"
        elif kind in {"tap", "tap_if_present", "swipe"}:
            # Reviewed plans may contain a screen/package-guarded gesture for
            # an inaccessible WebView or drawing surface. AndroidDriver
            # validates every guard before executing it.
            current = dict(legacy)
        else:
            raise ValueError(f"unsupported legacy action at index {index}: {legacy}")
        completed = driver.actions([current], stop_is_error=True)
        if not completed:
            raise RuntimeError("positive action sequence stopped unexpectedly")
        frozen.append(current)
        delay = legacy.get("delay")
        if isinstance(delay, (int, float)) and 0 < float(delay) <= 30:
            wait = {"action": "wait", "seconds": float(delay)}
            driver.actions([wait], stop_is_error=True)
            frozen.append(wait)
        xml = driver.hierarchy()
        xml_path = output / f"positive_step_{index + 1}.xml"
        xml_path.write_text(xml, encoding="utf-8")
        steps.append({
            "index": index,
            "legacy": legacy,
            "frozen": current,
            "resolution": detail,
            "xml": xml_path.name,
            "xml_sha256": sha256_file(xml_path),
        })
    return frozen, steps


def _candidate_values(plan: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for arm in ("positive_control", "negative_control"):
        for value in plan.get(arm, {}).values():
            if isinstance(value, str) and value.strip():
                values.add(value.strip())
    return values


def revised_observation_seconds(plan: dict[str, Any]) -> float:
    """Keep the normal settle floor unless a reviewed local endpoint is transient."""
    seconds = float(plan.get("observation_seconds", 10))
    transient = plan.get("time_sensitive_local_observation", False)
    if not isinstance(transient, bool) or not 0 < seconds <= 60:
        raise ValueError("invalid observation timing contract")
    return seconds if transient else max(5, seconds)


def _selector_candidates(
    root: ET.Element,
    package: str,
    excluded_ids: set[str],
    excluded_values: set[str],
) -> list[tuple[int, dict[str, str], str]]:
    result: dict[str, tuple[int, dict[str, str], str]] = {}
    for node in root.iter("node"):
        if node.get("package") != package:
            continue
        resource_id = node.get("resource-id", "").strip()
        if resource_id and resource_id not in excluded_ids:
            selector = {"package": package, "resource-id": resource_id}
            signature = json.dumps(selector, sort_keys=True)
            result.setdefault(signature, (0, selector, "resource-id"))
        for score, key in ((1, "content-desc"), (2, "text")):
            value = node.get(key, "").strip()
            if not value or len(value) > 180 or value in excluded_values:
                continue
            if any(value == item or value in item or item in value for item in excluded_values):
                continue
            if re.fullmatch(r"[\W\d_]+", value, flags=re.UNICODE):
                continue
            selector = {"package": package, key: value}
            signature = json.dumps(selector, ensure_ascii=False, sort_keys=True)
            result.setdefault(signature, (score, selector, key))
    return list(result.values())


def _holds(root: ET.Element, selector: dict[str, str]) -> bool:
    return any(node_matches(node, selector) for node in root.iter("node"))


def _enabled_state_candidates(
    package: str,
    baseline: ET.Element,
    positive: ET.Element,
    negative: ET.Element,
    excluded_ids: set[str],
    excluded_values: set[str],
) -> list[dict[str, Any]]:
    """Find stable controls enabled only by the validator-positive arm."""
    roots = (baseline, positive, negative)
    maps: list[dict[str, list[ET.Element]]] = []
    for root in roots:
        current: dict[str, list[ET.Element]] = {}
        for node in root.iter("node"):
            if node.get("package") != package or not node.get("class"):
                continue
            anchor_key = next((
                key for key in ("resource-id", "content-desc", "text")
                if node.get(key)
                and node.get(key) not in excluded_values
                and (key != "resource-id" or node.get(key) not in excluded_ids)
            ), None)
            if anchor_key is None or node.get("enabled") not in {"true", "false"}:
                continue
            base = {
                "package": package,
                "class": node.get("class"),
                anchor_key: node.get(anchor_key),
            }
            signature = json.dumps(base, ensure_ascii=False, sort_keys=True)
            current.setdefault(signature, []).append(node)
        maps.append(current)
    result = []
    for signature, positive_nodes in maps[1].items():
        baseline_nodes = maps[0].get(signature, [])
        negative_nodes = maps[2].get(signature, [])
        if not all(len(nodes) == 1 for nodes in (baseline_nodes, positive_nodes, negative_nodes)):
            continue
        baseline_enabled = baseline_nodes[0].get("enabled")
        positive_enabled = positive_nodes[0].get("enabled")
        negative_enabled = negative_nodes[0].get("enabled")
        if positive_enabled == baseline_enabled or baseline_enabled != negative_enabled:
            continue
        selector = json.loads(signature)
        selector["enabled"] = positive_enabled == "true"
        result.append(selector)
    return result


def derive_goal(
    package: str,
    baseline_xml: str,
    positive_xml: str,
    negative_xml: str,
    inspection: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    baseline = ET.fromstring(baseline_xml)
    positive = ET.fromstring(positive_xml)
    negative = ET.fromstring(negative_xml)
    old = plan.get("goal_assertion") or inspection.get("goal_assertion")
    if isinstance(old, dict):
        try:
            if assertion(positive, old) and not assertion(baseline, old) and not assertion(negative, old):
                target = old["targets"][0] if len(old.get("targets", [])) == 1 else None
                negative_assertion = (
                    {
                        "mode": "all",
                        "targets": [{
                            "selector": target["selector"],
                            "exists": not target["exists"],
                        }],
                    }
                    if target is not None else plan["negative_assertion"]
                )
                return old, negative_assertion, {"source": "historical_goal_revalidated"}
        except (KeyError, TypeError, ValueError):
            pass

    excluded_ids = {
        selector.get("resource-id", "")
        for selector in inspection["binding"].values()
        if selector.get("resource-id")
    }
    excluded_values = _candidate_values(plan)
    ranked: list[tuple[int, int, dict[str, str], bool, str]] = []
    for selector in _enabled_state_candidates(
        package, baseline, positive, negative, excluded_ids, excluded_values,
    ):
        ranked.append((0, 0, selector, True, "positive_only_enabled_state"))
    for score, selector, kind in _selector_candidates(
        positive, package, excluded_ids, excluded_values
    ):
        if not _holds(baseline, selector) and not _holds(negative, selector):
            ranked.append((score, 0, selector, True, f"positive_only_{kind}"))
    for score, selector, kind in _selector_candidates(
        baseline, package, excluded_ids, excluded_values
    ):
        if not _holds(positive, selector) and _holds(negative, selector):
            ranked.append((score, 1, selector, False, f"positive_disappeared_{kind}"))
    if not ranked:
        raise RuntimeError("positive and negative App endpoints have no stable discriminator")
    ranked.sort(key=lambda row: (row[0], row[1], json.dumps(row[2], sort_keys=True)))
    _, _, selector, exists, reason = ranked[0]
    goal = {"mode": "all", "targets": [{"selector": selector, "exists": exists}]}
    negative_assertion = {
        "mode": "all",
        "targets": [{"selector": selector, "exists": not exists}],
    }
    return goal, negative_assertion, {
        "source": "live_positive_negative_endpoint_difference",
        "selected_reason": reason,
        "eligible_discriminator_count": len(ranked),
        "selected_selector": selector,
        "positive_exists": exists,
    }


def recalibrate(
    case: dict[str, Any],
    inspection: dict[str, Any],
    plan: dict[str, Any],
    legacy: dict[str, Any],
    serial: str,
    avd: str,
    snapshot: Path,
    output: Path,
) -> dict[str, Any]:
    started = datetime.now(timezone.utc).isoformat()
    package = case["mirror"]["package"]
    if inspection.get("binding_complete") is not True:
        raise ValueError("field binding is incomplete")
    if set(inspection.get("binding", {})) != set(case["required_resource_ids"]):
        raise ValueError("field binding differs from the frozen case")
    network_mode = plan.get("network_mode", "online")
    case_reset = plan.get("case_specific_reset")
    positive_driver, positive_strategy, positive_baseline, positive_network, _ = restore(
        serial, avd, snapshot, output, "positive", network_mode,
        plan.get("prepare_action"), bool(plan.get("force_cold_reset", False)),
        plan.get("prepare_files"), case_reset,
        plan.get("prepare_adb_actions"), plan.get("prepare_adb_assertion"),
    )
    try:
        positive_driver.fill(
            inspection["binding"], plan["positive_control"],
            plan.get("input_strategies"),
        )
    except Exception as exc:
        raise RuntimeError(f"positive binding fill failed: {exc}") from exc
    try:
        action_source = (
            plan.get("verify_action")
            if plan.get("recalibration_action_source") == "plan"
            else legacy.get("verify_action")
        )
        frozen, steps = translate_and_execute(
            positive_driver, package, action_source or [], output,
        )
    except Exception as exc:
        raise RuntimeError(f"positive legacy action recalibration failed: {exc}") from exc
    old_goal = plan.get("goal_assertion") or inspection.get("goal_assertion")
    if isinstance(old_goal, dict):
        try:
            wait_assertion(
                positive_driver,
                old_goal,
                min(30, max(1, float(plan.get("observation_seconds", 10)))),
            )
        except (KeyError, TypeError, ValueError, RuntimeError):
            # Historical assertions are hints only.  The differential endpoint
            # below remains the source of truth when an old contract is stale.
            pass
    time.sleep(float(plan.get("endpoint_settle_seconds", 1)))
    positive_after = capture(positive_driver, output, "positive_after")

    negative_driver, negative_strategy, negative_baseline, negative_network, _ = restore(
        serial, avd, snapshot, output, "negative", network_mode,
        plan.get("prepare_action"), bool(plan.get("force_cold_reset", False)),
        plan.get("prepare_files"), case_reset,
        plan.get("prepare_adb_actions"), plan.get("prepare_adb_assertion"),
    )
    negative_input_rejected = False
    try:
        negative_driver.fill(
            inspection["binding"], plan["negative_control"],
            plan.get("input_strategies"),
        )
    except AppInputRejected as exc:
        if plan.get("input_rejection_is_negative") is not True:
            raise RuntimeError(f"negative binding fill failed: {exc}") from exc
        negative_input_rejected = True
    except Exception as exc:
        raise RuntimeError(f"negative binding fill failed: {exc}") from exc
    if negative_input_rejected:
        negative_actions_completed = False
    else:
        try:
            negative_actions_completed = negative_driver.actions(frozen)
        except Exception as exc:
            raise RuntimeError(f"negative frozen action execution failed: {exc}") from exc
    time.sleep(float(plan.get("endpoint_settle_seconds", 1)))
    negative_after = capture(negative_driver, output, "negative_after")

    if positive_after["foreground"].get("package") != package:
        raise RuntimeError("positive arm left the expected App; endpoint is not safe to freeze")
    if negative_after["foreground"].get("package") != package:
        raise RuntimeError("negative arm left the expected App; endpoint is not safe to freeze")
    visual_observer = plan.get("visual_text_observer")
    if visual_observer is not None:
        positive_visual = observe_image(
            output / positive_after["png"], visual_observer,
        )
        negative_visual = observe_image(
            output / negative_after["png"], visual_observer,
        )
        if not positive_visual["passed"] or negative_visual["passed"]:
            raise RuntimeError("visual App endpoints did not separate the controls")
        goal = plan["goal_assertion"]
        negative_assertion = plan["negative_assertion"]
        derivation = {
            "source": "live_screenshot_ocr_integer_range",
            "positive": positive_visual,
            "negative": negative_visual,
        }
    else:
        goal, negative_assertion, derivation = derive_goal(
            package,
            (output / positive_baseline["xml"]).read_text(encoding="utf-8"),
            (output / positive_after["xml"]).read_text(encoding="utf-8"),
            (output / negative_after["xml"]).read_text(encoding="utf-8"),
            inspection,
            plan,
        )
    revised_inspection = {
        **inspection,
        "status": "inspect_ready",
        "verify_action": frozen,
        "goal_assertion": goal,
        "goal_holds_initially": False,
        "issues": [],
        "recalibration": {
            "source": "live_stepwise_action_and_differential_endpoint",
            "evidence": "recalibration/recalibration_result.json",
        },
    }
    revised_plan = {
        **plan,
        "schema_version": "e2-structure-v1-live-control-plan-2",
        "verify_action": frozen,
        "goal_assertion": goal,
        "negative_assertion": negative_assertion,
        "observation_seconds": revised_observation_seconds(plan),
        "max_back_presses": max(8, int(plan.get("max_back_presses", 3))),
        "recalibration_evidence": "recalibration/recalibration_result.json",
    }
    inspection_path = output.parent / "inspection_recalibrated.json"
    plan_path = output.parent / "control_plan_recalibrated.json"
    atomic_json(inspection_path, revised_inspection)
    atomic_json(plan_path, revised_plan)
    return {
        "schema_version": "e2-structure-v1-live-recalibration-1",
        "case_id": case["case_id"],
        "status": "recalibrated_candidate",
        "started_at_utc": started,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "device": serial,
        "avd": current_avd(serial),
        "snapshot": snapshot.name,
        "source_inspection_sha256": sha256_file(Path(inspection["_path"])),
        "source_plan_sha256": sha256_file(Path(plan["_path"])),
        "positive": {
            "load_strategy": positive_strategy,
            "network": positive_network,
            "baseline": positive_baseline,
            "after": positive_after,
        },
        "negative": {
            "load_strategy": negative_strategy,
            "network": negative_network,
            "actions_completed": negative_actions_completed,
            "input_rejected": negative_input_rejected,
            "baseline": negative_baseline,
            "after": negative_after,
        },
        "stepwise_action_resolution": steps,
        "verify_action": frozen,
        "goal_assertion": goal,
        "negative_assertion": negative_assertion,
        "goal_derivation": derivation,
        "generated_inspection": str(inspection_path),
        "generated_plan": str(plan_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Recalibrate one PPTR App action/outcome contract")
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
    inspection = {**read_json(inspection_path), "_path": str(inspection_path)}
    plan = {**read_json(plan_path), "_path": str(plan_path)}
    case = next(
        (row for row in registry["cases"] if row["case_id"] == inspection.get("case_id")),
        None,
    )
    if case is None or plan.get("case_id") != case["case_id"]:
        raise ValueError("case, inspection, and plan IDs differ")
    avd, snapshot = infer_avd(args.source_root.resolve(), case)
    if avd != args.avd or current_avd(args.device) != args.avd:
        raise RuntimeError("running AVD does not match the recalibration case")
    config_path = Path(case["app_or_page_source"]["config"])
    legacy = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    try:
        result = recalibrate(
            case, inspection, plan, legacy, args.device, avd, snapshot, output,
        )
    except Exception as exc:
        result = {
            "schema_version": "e2-structure-v1-live-recalibration-1",
            "case_id": case["case_id"],
            "status": "recalibration_error",
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "error_type": type(exc).__name__,
            "error": str(exc) or repr(exc),
        }
    atomic_json(output / "recalibration_result.json", result)
    print(json.dumps(result, ensure_ascii=False))
    if result["status"] != "recalibrated_candidate":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
