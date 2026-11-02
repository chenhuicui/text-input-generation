"""Build auditable App-observation-only contracts for the 37 Bug cases."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from PIL import Image

from .abcdef_preflight import DEFAULT_CONFIG, read_json, resolve_path, sha256_file


ACTION_OVERRIDES: dict[str, dict[str, Any]] = {
    "B001": {"resource-id": "com.example.unserhoersaal:id/loginFragmentLoginButton"},
    "B002": {"resource-id": "org.ebur.debitum:id/miSaveTransaction"},
    "B003": {"resource-id": "android:id/button1"},
    "B004": {"resource-id": "android:id/button1"},
    "B005": {"press": "enter"},
    "B006": {"resource-id": "de.symeda.sormas.app:id/action_save"},
    "B007": {"text": "="},
    "B008": {"resource-id": "android:id/button1"},
    "B009": {"resource-id": "android:id/button1"},
    "B010": {"wait": 0.25},
    "B011": {"wait": 0.25},
    "B012": {"wait": 0.25},
    "B013": {"wait": 0.25},
    "B014": {"wait": 0.25},
    "B015": {"text": "RENDER WRAPPED TEXT"},
    "B016": {"resource-id": "org.kontalk.debug:id/audio_send_button"},
    "B017": {"resource-id": "it.sasabz.android.sasabus:id/search"},
    "B018": {"wait": 0.25},
    "B019": {"resource-id": "im.vector.alpha:id/room_send_layout"},
    "B020": {"resource-id": "io.gresse.hugo.anecdote:id/saveButton"},
    "B021": {"resource-id": "io.gresse.hugo.anecdote:id/saveButton"},
    "B022": {"resource-id": "com.landfilleforms.android.landfille_forms:id/barometric_button"},
    "B023": {"wait": 0.25},
    "B024": {"wait": 0.25},
    "B025": {"resource-id": "com.example.khelair.mintone:id/btnManualFreqChange"},
    "B026": {"resource-id": "com.fsck.k9:id/send"},
    "B027": {"resource-id": "communi.dog.aplicatiion:id/login_button"},
    "B028": {"resource-id": "com.faanggang.wisetrack:id/activity_search_search_button"},
    "B029": {"text": "->"},
    "B030": {"resource-id": "com.example.healthlog:id/loginActivity_login_btn"},
    "B031": {"resource-id": "org.secuso.privacyfriendlyintervaltimer:id/start_workout"},
    "B032": {"resource-id": "io.mrarm.irc:id/check"},
    "B033": {"wait": 0.25},
    "B034": {"resource-id": "android:id/button1"},
    "B035": {"resource-id": "com.eventyay.attendee:id/applyButton"},
    "B036": {"wait": 0.25},
    "B037": {"resource-id": "com.eventyay.organizer:id/btnResetPassword"},
}

# These cases changed their observable App contract after live smoke exposed a
# value-dependent selector or a case-specific remote reset requirement.
V2_CONTRACT_CASES = {"B012", "B013", "B015", "B017", "B019", "B033", "B036"}


def _matches(node: ET.Element, selector: dict[str, str]) -> bool:
    return all(node.get(key) == value for key, value in selector.items())


def _action(
    case_id: str, package: str, root: ET.Element,
    screen_size: tuple[int, int],
) -> list[dict[str, Any]]:
    override = ACTION_OVERRIDES[case_id]
    if "wait" in override:
        return [{"action": "wait", "seconds": float(override["wait"])}]
    if "press" in override:
        return [{"action": "press", "key": override["press"]}]
    key = "resource-id" if "resource-id" in override else "text"
    selector = {"package": package, key: str(override[key])}
    matches = [node for node in root.iter("node") if _matches(node, selector)]
    if len(matches) != 1:
        raise ValueError(f"{case_id} action selector resolves to {len(matches)} nodes: {selector}")
    node = matches[0]
    if node.get("clickable") != "true":
        raise ValueError(f"{case_id} action node is not clickable: {selector}")
    if case_id == "B017":
        bounds = re.fullmatch(
            r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""),
        )
        if bounds is None:
            raise ValueError("B017 guarded tap lacks frozen bounds")
        left, top, right, bottom = map(int, bounds.groups())
        screen_width, screen_height = screen_size
        return [{
            "action": "tap",
            "package": package,
            "screen_width": screen_width,
            "screen_height": screen_height,
            "x": (left + right) // 2,
            "y": (top + bottom) // 2,
        }]
    return [{"action": "click", "selector": selector, "if_missing": "stop"}]


def _assertion(binding: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "mode": "all",
        "targets": [{"selector": selector, "exists": True} for selector in binding.values()],
    }


def _stabilize_editable_bindings(
    binding: dict[str, dict[str, Any]], root: ET.Element,
) -> dict[str, dict[str, Any]]:
    """Replace value-dependent no-id EditText selectors with frozen instances."""
    stable: dict[str, dict[str, Any]] = {}
    nodes = list(root.iter("node"))
    for field, selector in binding.items():
        current = dict(selector)
        value_anchored = any(current.get(key) for key in ("text", "hint", "content-desc"))
        if (
            current.get("class") == "android.widget.EditText"
            and not current.get("resource-id")
            and value_anchored
        ):
            matches = [node for node in nodes if _matches(node, current)]
            if len(matches) != 1:
                raise ValueError(f"{field} cannot be resolved before selector stabilization")
            if matches[0].get("enabled") != "true":
                # Retain value anchoring for read-only fields.  They are not
                # rewritten during execution, and the value distinguishes
                # otherwise duplicate disabled/active controls.
                stable[field] = current
                continue
            candidates = [
                node for node in nodes
                if node.get("package") == current.get("package")
                and node.get("class") == current.get("class")
                and node.get("enabled") == "true"
            ]
            if matches[0] not in candidates:
                raise ValueError(f"{field} cannot be converted to a stable EditText instance")
            current = {
                "package": current["package"],
                "class": current["class"],
                "enabled": True,
                "instance": candidates.index(matches[0]),
                "unique": True,
            }
        stable[field] = current
    return stable


def _contract(
    *,
    case: dict[str, Any],
    inspection_path: Path,
    python: str,
) -> dict[str, Any]:
    case_id = case["case_id"]
    inspection = read_json(inspection_path)
    if inspection.get("binding_complete") is not True:
        raise ValueError(f"{case_id} live binding is incomplete")
    if set(inspection.get("binding", {})) != set(case["required_resource_ids"]):
        raise ValueError(f"{case_id} live binding differs from the string-predicate contract")
    xml_path = inspection_path.parent / inspection["evidence"]["initial_xml"]
    png_path = inspection_path.parent / inspection["evidence"]["initial_png"]
    root = ET.fromstring(xml_path.read_text(encoding="utf-8"))
    with Image.open(png_path) as initial_image:
        screen_size = initial_image.size
    package = inspection["foreground"]["package"]
    if package != case["mirror"]["package"]:
        raise ValueError(f"{case_id} foreground package differs from the frozen mirror")
    manifest = Path(case["mirror"]["manifest_path"]).resolve()
    if not manifest.is_file() or sha256_file(manifest) != case["mirror"]["manifest_sha256"]:
        raise ValueError(f"{case_id} mirror manifest is missing or drifted")
    binding = _stabilize_editable_bindings(inspection["binding"], root)
    baseline = _assertion(binding)
    reset_id = f"{case_id}-app-observation-snapshot-v1"
    manifest_row = read_json(manifest)
    if manifest_row.get("readiness_scope") == "full_independent_attempt_with_remote_reset":
        reset_command = list(manifest_row.get("attempt_prepare_command") or [])
        if not reset_command:
            raise ValueError(f"{case_id} remote-state manifest lacks attempt_prepare_command")
        if reset_command[0] in {"python", "python3"}:
            reset_command[0] = python
        reset_id = manifest_row.get("attempt_reset_id")
        if not isinstance(reset_id, str) or not reset_id:
            raise ValueError(f"{case_id} remote-state manifest lacks attempt_reset_id")
        remote_baseline = "verified"
    else:
        reset_command = [
            python,
            "-m",
            "text_input_eval.supplementary.structured_snapshot_reset",
            "--manifest",
            str(manifest),
            "--device",
            inspection["serial"],
            "--avd",
            inspection["avd"],
            "--reset-id",
            reset_id,
        ]
        remote_baseline = "not_applicable"
    return {
        "case_id": case_id,
        "version": (
            f"{case_id}-app-observation-20260925-v2"
            if case_id in V2_CONTRACT_CASES
            else f"{case_id}-app-observation-20260924-v1"
        ),
        "task": "invalid_bug",
        "oracle_role": "feedback_observation_only",
        "verdict_oracle": "frozen_string_predicate",
        "aut": package,
        "device_serial": inspection["serial"],
        "app_artifact": {
            "version_code": inspection["artifact"]["version_code"],
            "source_sha256": case["mirror"]["source_sha256"],
            "install_kind": inspection["artifact"]["install_kind"],
        },
        "binding": binding,
        "begin_attempt": {
            "reset_id": reset_id,
            "reset_command": reset_command,
            "remote_baseline": remote_baseline,
            "initial_assertion": baseline,
        },
        "execute_turn": {
            "verify_action": _action(case_id, package, root, screen_size),
            "observation_seconds": 5,
            "poll_seconds": 0.5,
            "observer_command": [
                python,
                "-m",
                "text_input_eval.supplementary.abcdef_bug_observer",
            ],
            "evidence_kinds": ["xml", "screenshot", "screen_recording", "feedback_composite", "ui_observations"],
            **({
                "input_strategies": {
                    field: "adb_shell_input" for field in binding
                },
                "hierarchy_transport": "frozen_initial_xml",
            } if case_id == "B017" else {}),
        },
        "recover_turn": {
            "recovery_mode": "independent_baseline_reload",
            "target_page_assertion": baseline,
            "input_clear_assertion": baseline,
        },
        "goal_assertion": baseline,
        "diagnostic_targets": [],
        "source_evidence": {
            "inspection": str(inspection_path.resolve()),
            "inspection_sha256": sha256_file(inspection_path),
            "initial_xml": str(xml_path.resolve()),
            "initial_xml_sha256": sha256_file(xml_path),
            "mirror_manifest": str(manifest),
            "mirror_manifest_sha256": sha256_file(manifest),
        },
        "interpretation": (
            "App evidence is exposed equally to A-F feedback construction; it never replaces "
            "or explains the private Bug string-predicate verdict."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the 37 Bug App-observation contracts")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--cases-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = read_json(config_path)
    registry_path = resolve_path(config_path, config["runtime"]["case_registry"])
    if registry_path is None:
        raise ValueError("case registry is required")
    registry = read_json(registry_path)
    python = config["runtime"]["environment_command"][0]
    rows = []
    for case in registry["cases"]:
        if case.get("task") != "invalid_bug":
            continue
        inspection_path = args.cases_root.resolve() / case["case_id"] / "inspection.json"
        rows.append({
            "case_id": case["case_id"],
            "feedback_oracle": _contract(
                case=case,
                inspection_path=inspection_path,
                python=python,
            ),
        })
    if len(rows) != 37 or set(ACTION_OVERRIDES) != {row["case_id"] for row in rows}:
        raise RuntimeError("Bug App-observation contract coverage is not exactly B001-B037")
    output = {
        "schema_version": "e2-abcdef-v1-bug-app-observation-contracts-1",
        "protocol_id": config["protocol_id"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "oracle_separation": {
            "bug_verdict": "frozen_string_predicate",
            "app_contract_role": "feedback_observation_only",
            "app_observation_can_override_verdict": False,
        },
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output.resolve()),
        "contracts": len(rows),
        "manifest_sha256": sha256_file(args.output.resolve()),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
