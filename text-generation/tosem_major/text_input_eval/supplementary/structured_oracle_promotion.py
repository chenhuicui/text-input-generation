"""Promote one fully controlled local-only case to the formal Oracle manifest.

Promotion is deliberately fail-closed: the live inspection, control plan, and
control result hashes must agree; reset/recovery/remote-baseline receipts must
be complete; the generated Oracle must satisfy the frozen JSON Schema; and a
real formal-adapter begin/fail/recover smoke must pass before either the formal
manifest or the control result is updated.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .structured_environment import AndroidCommandEnvironment
from .structured_android import canonical_json
from .structured_live_calibration import atomic_json, read_json
from .structured_snapshot_backup import sha256_file


RECEIPT_FIELDS = [
    "reset_verified", "target_page_verified", "remote_baseline", "reset_id", "state_hash",
]
UI_SELECTOR_KEYS = {
    "package", "class", "resource-id", "text", "content-desc", "enabled",
}


def ui_selector(selector: dict[str, Any]) -> dict[str, Any]:
    value = {key: item for key, item in selector.items() if key in UI_SELECTOR_KEYS}
    if value.get("package") and any(
        value.get(key) for key in ("resource-id", "text", "content-desc")
    ):
        return value
    # Some WebView/Compose input nodes expose only a unique class contract.
    # The formal assertion schema and Android adapter support the same frozen
    # binding selector used for field relocation, so target-page verification
    # does not need an invented resource id.
    return selector


def target_page_assertion(binding: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "mode": "all",
        "targets": [
            {"selector": ui_selector(selector), "exists": True}
            for selector in binding.values()
        ],
    }


def evidence_reference(path: Path, fragment: str) -> str:
    parts = path.resolve().parts
    try:
        index = parts.index("live_calibration")
    except ValueError as exc:
        raise ValueError(f"calibration evidence is outside live_calibration: {path}") from exc
    return f"{Path(*parts[index:])}#{fragment}"


def build_oracle(
    case: dict[str, Any], inspection: dict[str, Any], plan: dict[str, Any],
    control: dict[str, Any], manifest_path: Path, avd: str,
) -> dict[str, Any]:
    case_id = case["case_id"]
    if any(row.get("case_id") != case_id for row in (inspection, plan, control)):
        raise ValueError("case IDs differ across promotion inputs")
    if inspection.get("status") != "inspect_ready":
        raise ValueError("live inspection is not ready")
    if inspection.get("version_matches") is not True or inspection.get("binding_complete") is not True:
        raise ValueError("version alignment and exact binding must be verified")
    if set(inspection.get("binding", {})) != set(case["required_resource_ids"]):
        raise ValueError("inspected binding keys differ from the frozen case")
    if control.get("status") != "controls_passed_local_only":
        raise ValueError("local behavior controls have not passed")
    device_serial = control.get("device")
    if not isinstance(device_serial, str) or not device_serial:
        raise ValueError("controlled device serial is unavailable")
    if control.get("avd") != avd:
        raise ValueError("controlled AVD differs from the requested formal AVD")
    if control.get("remote_baseline", {}).get("status") not in {"verified", "not_applicable"}:
        raise ValueError("remote baseline is unresolved")
    if control.get("independent_attempt_reset", {}).get("reset_verified") is not True:
        raise ValueError("independent snapshot reset is not verified")
    recovery = control.get("within_attempt_recovery", {})
    if recovery.get("verified") is not True or any(
        recovery.get(key) is not False
        for key in ("snapshot_loaded", "remote_reset", "renavigated_from_baseline")
    ) or recovery.get("natural_state_preserved") is not True:
        raise ValueError("within-attempt recovery contract is incomplete")
    network_mode = control.get("network_isolation", {}).get("mode", "online")
    if network_mode not in {"online", "offline"}:
        raise ValueError("control result has an invalid network-isolation mode")
    if network_mode == "offline" and not all(
        control.get("network_isolation", {}).get(arm, {}).get("airplane_mode_verified") is True
        for arm in ("positive", "negative")
    ):
        raise ValueError("offline control did not verify airplane mode for both arms")
    if control.get("inspection_sha256") != sha256_file(Path(inspection["_path"])):
        raise ValueError("control result references a different inspection")
    if control.get("plan_sha256") != sha256_file(Path(plan["_path"])):
        raise ValueError("control result references a different plan")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"mirror manifest is unavailable: {manifest_path}")
    mirror_manifest = read_json(manifest_path)
    if mirror_manifest.get("case_id") != case_id or mirror_manifest.get("status") != "ready":
        raise ValueError("mirror manifest case/status differs from the controlled case")

    binding = inspection["binding"]
    prepare_action = plan.get("prepare_action", [])
    if not isinstance(prepare_action, list):
        raise ValueError("prepare_action must be an action list")
    prepare_files = plan.get("prepare_files", [])
    if not isinstance(prepare_files, list):
        raise ValueError("prepare_files must be a list")
    prepare_adb_actions = plan.get("prepare_adb_actions", [])
    if not isinstance(prepare_adb_actions, list):
        raise ValueError("prepare_adb_actions must be a list")
    prepare_adb_assertion = plan.get("prepare_adb_assertion")
    if prepare_adb_actions and not isinstance(prepare_adb_assertion, dict):
        raise ValueError("prepare_adb_actions require a prepare_adb_assertion")
    if not prepare_adb_actions and prepare_adb_assertion is not None:
        raise ValueError("prepare_adb_assertion requires prepare_adb_actions")
    input_strategies = plan.get("input_strategies", {})
    if (
        not isinstance(input_strategies, dict)
        or not set(input_strategies).issubset(binding)
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
    if input_rejection_is_negative and (
        control.get("negative_control", {}).get("input_rejected") is not True
    ):
        raise ValueError("App input rejection was not demonstrated by the negative control")
    candidate_binding_goal = plan.get("candidate_binding_goal", False)
    if not isinstance(candidate_binding_goal, bool):
        raise ValueError("candidate_binding_goal must be boolean")
    if candidate_binding_goal and (
        control.get("candidate_binding_goal") is not True
    ):
        raise ValueError("candidate binding goal was not demonstrated by live controls")
    candidate_text_goal = plan.get("candidate_text_goal", False)
    if not isinstance(candidate_text_goal, bool):
        raise ValueError("candidate_text_goal must be boolean")
    if candidate_text_goal and control.get("candidate_text_goal") is not True:
        raise ValueError("candidate text goal was not demonstrated by live controls")
    input_clear_assertion = recovery["input_clear_assertion"]
    initial_assertion = plan.get("initial_assertion", input_clear_assertion)
    recovery_target_assertion = plan.get(
        "target_page_assertion", target_page_assertion(binding),
    )
    case_reset = plan.get("case_specific_reset")
    if case_reset is not None:
        expected_manifest = str(manifest_path)
        expected_reset_id = case.get("mirror", {}).get("attempt_reset", {}).get("reset_id")
        if (
            not isinstance(case_reset, dict)
            or case_reset.get("manifest_path") != expected_manifest
            or case_reset.get("reset_id") != expected_reset_id
            or control["remote_baseline"]["status"] != "verified"
            or network_mode != "online"
        ):
            raise ValueError("case-specific reset differs from the frozen registry/remote contract")
        reset_id = case_reset["reset_id"]
        reset_command = [
            "/opt/anaconda3/bin/python3", "-m",
            "text_input_eval.supplementary.structured_case_reset",
            "--manifest", str(manifest_path),
            "--device", device_serial,
            "--avd", avd,
            "--reset-id", reset_id,
        ]
    else:
        reset_id = f"{case_id}-local-snapshot-v1"
        reset_command = [
            "/opt/anaconda3/bin/python3", "-m",
            "text_input_eval.supplementary.structured_snapshot_reset",
            "--manifest", str(manifest_path),
            "--device", device_serial,
            "--avd", avd,
            "--reset-id", reset_id,
            *(["--offline"] if network_mode == "offline" else []),
            *(["--force-cold"] if plan.get("force_cold_reset") is True else []),
            *(
                ["--prepare-action-json", canonical_json(prepare_action)]
                if prepare_action else []
            ),
            *(
                ["--prepare-files-json", canonical_json(prepare_files)]
                if prepare_files else []
            ),
            *(
                ["--prepare-adb-actions-json", canonical_json(prepare_adb_actions)]
                if prepare_adb_actions else []
            ),
            *(
                ["--prepare-adb-assertion-json", canonical_json(prepare_adb_assertion)]
                if prepare_adb_assertion is not None else []
            ),
            *(
                ["--remote-baseline", "verified"]
                if control["remote_baseline"]["status"] == "verified" else []
            ),
        ]
    recovery_id = f"{case_id}-clear-current-page-v1"
    recovery_action = plan.get("recovery_action", [])
    if not isinstance(recovery_action, list):
        raise ValueError("recovery_action must be an action list")
    recovery_values = plan.get("recovery_baseline_values")
    if recovery_values is not None and (
        not isinstance(recovery_values, dict)
        or set(recovery_values) != set(binding)
        or any(not isinstance(value, str) for value in recovery_values.values())
        or recovery.get("input_restore_values") != recovery_values
    ):
        raise ValueError(
            "recovery_baseline_values were not verified for every binding"
        )
    artifact = inspection["artifact"]
    artifact_set_sha256 = artifact.get("artifact_set_sha256")
    if not isinstance(artifact_set_sha256, str) or len(artifact_set_sha256) != 64:
        raise ValueError("frozen installed App artifact-set SHA-256 is unavailable")
    version_day = datetime.now(timezone.utc).strftime("%Y%m%d")
    visual_observer = plan.get("visual_text_observer")
    observer_command = (
        [
            "/opt/anaconda3/bin/python3", "-m",
            "text_input_eval.supplementary.structured_visual_observer",
            "--config-json", canonical_json(visual_observer),
        ]
        if visual_observer is not None else None
    )
    return {
        "case_id": case_id,
        "version": f"{case_id}-live-{version_day}-v1",
        "task": case["task"],
        "aut": case["mirror"]["package"],
        "device_serial": device_serial,
        "app_artifact": {
            "version_code": str(artifact["version_code"]),
            "sha256": artifact_set_sha256,
            "install_kind": artifact["install_kind"],
        },
        "binding": binding,
        "begin_attempt": {
            "reset_id": reset_id,
            "reset_command": reset_command,
            "remote_baseline": control["remote_baseline"]["status"],
            "initial_assertion": initial_assertion,
            "receipt_fields": RECEIPT_FIELDS,
        },
        "execute_turn": {
            "verify_action": control["verify_action"],
            **({"input_strategies": input_strategies} if input_strategies else {}),
            **({"candidate_binding_goal": True} if candidate_binding_goal else {}),
            **({"candidate_text_goal": True} if candidate_text_goal else {}),
            **(
                {"input_rejection_verdict": "fail"}
                if input_rejection_is_negative else {}
            ),
            "observation_seconds": float(plan.get("observation_seconds", 10)),
            "poll_seconds": 0.5,
            "evidence_kinds": ["xml"],
            **({"observer_command": observer_command} if observer_command else {}),
        },
        "recover_turn": {
            "recovery_id": recovery_id,
            "command": [
                "/opt/anaconda3/bin/python3", "-m",
                "text_input_eval.supplementary.structured_recovery",
                "--max-back-presses", str(int(plan.get("max_back_presses", 3))),
                *(
                    ["--recovery-action-json", canonical_json(recovery_action)]
                    if recovery_action else []
                ),
                *(
                    ["--recovery-values-json", canonical_json(recovery_values)]
                    if recovery_values is not None else []
                ),
            ],
            "snapshot_loaded": False,
            "remote_reset": False,
            "renavigated_from_baseline": False,
            "natural_state_preserved": True,
            "target_page_assertion": recovery_target_assertion,
            "input_clear_assertion": input_clear_assertion,
            "dynamic_relocation_required": True,
        },
        "goal_assertion": plan.get("goal_assertion", inspection["goal_assertion"]),
        "diagnostic_targets": [],
        "calibration": {
            "positive_control": evidence_reference(Path(control["_path"]), "positive_control"),
            "negative_control": evidence_reference(Path(control["_path"]), "negative_control"),
            "version_alignment_verified": True,
            "reset_verified": True,
            "recovery_verified": True,
            "artifact_identity_policy": "installed_apk_set_sha256",
            "inspection_sha256": sha256_file(Path(inspection["_path"])),
            "plan_sha256": sha256_file(Path(plan["_path"])),
        },
    }


def validate_oracle(oracle: dict[str, Any], schema_path: Path) -> None:
    errors = list(Draft202012Validator(read_json(schema_path)).iter_errors(oracle))
    if errors:
        first = errors[0]
        location = "/".join(str(item) for item in first.absolute_path)
        raise ValueError(f"formal Oracle schema validation failed at {location}: {first.message}")


def smoke(
    case: dict[str, Any], oracle: dict[str, Any], negative: dict[str, str],
    environment_command: list[str], output: Path,
) -> dict[str, Any]:
    environment = AndroidCommandEnvironment(environment_command, timeout_seconds=600)
    begin_dir = output / "begin"
    negative_dir = output / "negative"
    recovery_dir = output / "recovery"
    begin = environment.begin_attempt(case, {
        "oracle": oracle,
        "evidence_dir": str(begin_dir),
        "command_timeout_seconds": 600,
    })
    execution = environment.execute_turn(negative, {
        "request_id": f"{case['case_id']}-formal-smoke-negative",
        "oracle": oracle,
        "case": case,
        "evidence_dir": str(negative_dir),
        "command_timeout_seconds": 600,
    })
    if execution.get("verdict") != "fail" or execution.get("observation_complete") is not True:
        raise RuntimeError(
            "formal adapter negative execution did not produce a complete fail verdict: "
            f"verdict={execution.get('verdict')!r}, "
            f"observation_complete={execution.get('observation_complete')!r}, "
            f"error_type={execution.get('error_type')!r}"
        )
    recovered = environment.recover_turn({
        "oracle": oracle,
        "case": case,
        "evidence_dir": str(recovery_dir),
        "state_after_hash": execution["state_after_hash"],
        "command_timeout_seconds": 600,
    })
    if not all(recovered.get(key) is True for key in (
        "target_page_verified", "input_cleared", "natural_state_preserved",
    )):
        raise RuntimeError("formal adapter recovery did not verify the current input page")
    result = {
        "schema_version": "e2-structure-v1-formal-adapter-smoke-1",
        "case_id": case["case_id"],
        "oracle_version": oracle["version"],
        "inspection_sha256": oracle["calibration"]["inspection_sha256"],
        "plan_sha256": oracle["calibration"]["plan_sha256"],
        "begin_attempt": {
            key: begin[key] for key in ("reset_verified", "target_page_verified", "remote_baseline")
        },
        "negative_execution": {
            key: execution[key] for key in ("verdict", "observation_complete")
        },
        "recover_turn": {
            key: recovered[key] for key in (
                "target_page_verified", "input_cleared", "natural_state_preserved",
                "snapshot_loaded", "remote_reset", "renavigated_from_baseline",
            )
        },
        "evidence": {
            "attempt_begin": "begin/attempt_begin.xml",
            "negative_before": "negative/before.xml",
            "negative_after": "negative/after.xml",
            "recovery": "recovery/recovery.xml",
        },
    }
    atomic_json(output / "result.json", result)
    return result


def commit(
    oracle_manifest_path: Path, oracle: dict[str, Any],
    control_path: Path, smoke_result_path: Path,
) -> None:
    manifest = read_json(oracle_manifest_path)
    rows = manifest.get("cases")
    if not isinstance(rows, list):
        raise ValueError("formal Oracle manifest must contain a cases list")
    rows = [row for row in rows if row.get("case_id") != oracle["case_id"]] + [oracle]
    rows.sort(key=lambda row: row["case_id"])
    manifest["cases"] = rows
    atomic_json(oracle_manifest_path, manifest)
    control = {**read_json(control_path), "_path": str(control_path)}
    control["formal_oracle_promoted"] = True
    control["formal_oracle_version"] = oracle["version"]
    control["formal_adapter_smoke"] = {
        "path": str(smoke_result_path),
        "sha256": sha256_file(smoke_result_path),
    }
    atomic_json(control_path, control)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke and promote one controlled formal Oracle")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--inspection", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--mirror-manifest", type=Path, required=True)
    parser.add_argument("--avd", required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--oracle-manifest", type=Path, required=True)
    parser.add_argument("--smoke-output", type=Path, required=True)
    parser.add_argument("--candidate-output", type=Path, required=True)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    registry = read_json(args.registry.resolve())
    inspection_path = args.inspection.resolve()
    plan_path = args.plan.resolve()
    control_path = args.control.resolve()
    inspection = {**read_json(inspection_path), "_path": str(inspection_path)}
    plan = {**read_json(plan_path), "_path": str(plan_path)}
    control = {**read_json(control_path), "_path": str(control_path)}
    case = next(
        (row for row in registry["cases"] if row["case_id"] == inspection["case_id"]), None,
    )
    if case is None:
        raise ValueError("case is absent from the frozen registry")
    oracle = build_oracle(
        case, inspection, plan, control, args.mirror_manifest.resolve(), args.avd,
    )
    validate_oracle(oracle, args.schema.resolve())
    atomic_json(args.candidate_output.resolve(), oracle)
    if not args.commit:
        print(json.dumps({
            "case_id": oracle["case_id"],
            "oracle_version": oracle["version"],
            "candidate": str(args.candidate_output.resolve()),
            "committed": False,
        }, ensure_ascii=False))
        return
    config = read_json(args.config.resolve())
    smoke_result = smoke(
        case, oracle, plan["negative_control"],
        config["runtime"]["environment_command"], args.smoke_output.resolve(),
    )
    oracle["calibration"]["formal_adapter_smoke_sha256"] = sha256_file(
        args.smoke_output.resolve() / "result.json"
    )
    validate_oracle(oracle, args.schema.resolve())
    commit(
        args.oracle_manifest.resolve(), oracle, control_path,
        args.smoke_output.resolve() / "result.json",
    )
    print(json.dumps({
        "case_id": oracle["case_id"],
        "oracle_version": oracle["version"],
        "smoke": smoke_result,
        "committed": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
