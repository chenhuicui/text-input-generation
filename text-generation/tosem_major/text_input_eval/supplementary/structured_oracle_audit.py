"""Build per-case Oracle candidates and an explicit live-calibration gap ledger.

This command never promotes cached validators, YAML checks, or target-page
snapshots to behavior Oracles.  It extracts only defensible selector and source
candidates, then records the remaining live work case by case.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .structured_preflight import REPO_ROOT, read_json, sha256_file


def selector_candidates(case: dict[str, Any]) -> tuple[dict[str, dict[str, str]], list[str]]:
    xml_path = REPO_ROOT / case["source_files"]["cached_xml"]
    nodes = list(ET.fromstring(xml_path.read_text(encoding="utf-8")).iter("node"))
    bindings: dict[str, dict[str, str]] = {}
    gaps: list[str] = []
    for declared in case["required_resource_ids"]:
        raw = declared.split("::#::", 1)[0]
        matches = [node for node in nodes if node.get("resource-id") == raw]
        triples = {
            (node.get("package"), node.get("class"), node.get("resource-id"))
            for node in matches
            if node.get("package") and node.get("class") and node.get("resource-id")
        }
        if len(matches) == 1 and len(triples) == 1 and "::#::" not in declared:
            package, klass, resource_id = next(iter(triples))
            bindings[declared] = {
                "package": package,
                "class": klass,
                "resource-id": resource_id,
            }
        else:
            gaps.append(f"binding_requires_live_unique_selector:{declared}:cached_matches={len(matches)}")
    return bindings, gaps


def action_scan(case: dict[str, Any]) -> dict[str, Any]:
    source = case.get("app_or_page_source") or {}
    config_label = source.get("config") if isinstance(source, dict) else None
    if not config_label:
        return {"config": None, "unsupported_coordinate_action_candidate": False, "live_action_freeze_required": True}
    path = REPO_ROOT / config_label
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    active = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    section_match = re.search(
        r"(?ms)^verify_action:\s*\n(?P<body>(?:^[ \t]+.*(?:\n|$)|^\s*$)*)",
        active,
    )
    verify_action = section_match.group("body") if section_match else ""
    coordinate = bool(re.search(r"(?m)^\s*(type:\s*coordinate|raw_[xy]|raw_[ft][xy]_hex:)", verify_action))
    gesture = bool(re.search(r"(?m)^\s*action:\s*(swipe|double_click|long_click)\s*$", verify_action))
    return {
        "config": config_label,
        "config_sha256": sha256_file(path) if path.is_file() else None,
        "unsupported_coordinate_action_candidate": coordinate or gesture,
        "legacy_verify_action_present": bool(source.get("verify_action_present")),
        "live_action_freeze_required": True,
    }


def build(registry: dict[str, Any]) -> dict[str, Any]:
    cases = []
    for case in registry["cases"]:
        bindings, binding_gaps = selector_candidates(case)
        mirror = case["mirror"]
        readiness = case["protocol_readiness"]
        source = case.get("app_or_page_source") or {}
        action = action_scan(case)
        formal_ready = readiness["formal_run_ready"] is True
        if case["task"] == "valid_pptr":
            goal_source = {
                "kind": "legacy_yaml_ui_check_candidate",
                "verify_appear": source.get("verify_appear"),
                "verify_disappear": source.get("verify_disappear"),
                "formal_spec_status": source.get("formal_spec_status"),
            }
        else:
            audit = case["candidate_validator_audit"]
            goal_source = {
                "kind": "retrieved_bug_report_candidate",
                "validator_audit_status": audit.get("source_status"),
                "binding_exact": audit.get("binding_exact"),
                "predicate_control_pair": audit.get("predicate_control_pair"),
                "note": audit.get("note"),
            }
        gaps = []
        if not formal_ready and case["task"] == "invalid_bug":
            gaps.append("string_predicate_oracle_not_frozen")
        elif not formal_ready:
            gaps.extend(binding_gaps)
            if action["unsupported_coordinate_action_candidate"]:
                gaps.append("verify_action_requires_dynamic_live_replacement")
            if not readiness["behavior_oracle_ready"]:
                gaps.extend([
                    "positive_behavior_control_not_executed",
                    "negative_behavior_control_not_executed",
                ])
            if not readiness["within_attempt_recovery_ready"]:
                gaps.append("within_attempt_recovery_not_calibrated")
            if not readiness["independent_attempt_reset_ready"]:
                gaps.append("independent_attempt_reset_not_verified")
            if readiness["binding_review_required"]:
                gaps.append("manual_binding_review_required")
        manifest_path = mirror.get("manifest_path")
        reset_id = mirror.get("attempt_reset", {}).get("reset_id")
        prepare_command = mirror.get("attempt_reset", {}).get("prepare_command")
        if not prepare_command and manifest_path and mirror.get("snapshot_path"):
            reset_id = f"{case['case_id'].lower()}_local_snapshot_v1"
            prepare_command = [
                "/opt/anaconda3/bin/python3", "-m",
                "text_input_eval.supplementary.structured_snapshot_reset",
                "--manifest", manifest_path,
                "--device", mirror.get("device", {}).get("serial") or "emulator-5556",
                "--reset-id", reset_id,
            ]
        cases.append({
            "case_id": case["case_id"],
            "task": case["task"],
            "artifact_candidate": None if case["task"] == "invalid_bug" else {
                "package": mirror.get("package"),
                "version_code": mirror.get("version_code"),
                "source_sha256": mirror.get("source_sha256"),
            },
            "binding_candidates": bindings,
            "required_resource_ids": case["required_resource_ids"],
            "binding_candidate_complete": set(bindings) == set(case["required_resource_ids"]),
            "action_candidate": action,
            "goal_source_candidate": goal_source,
            "reset_candidate": None if case["task"] == "invalid_bug" else {
                "readiness_scope": mirror.get("readiness_scope"),
                "reset_id": reset_id,
                "prepare_command": prepare_command,
                "remote_baseline_decision_required": mirror.get("readiness_scope") != "full_independent_attempt_with_remote_reset",
            },
            "recovery_candidate": None if case["task"] == "invalid_bug" else {
                "command": [
                    "/opt/anaconda3/bin/python3", "-m",
                    "text_input_eval.supplementary.structured_recovery",
                    "--max-back-presses", "3",
                ],
                "snapshot_loaded": False,
                "remote_reset": False,
                "renavigated_from_baseline": False,
                "natural_state_preserved": True,
                "calibration_required": True,
            },
            "formal_oracle_ready": formal_ready,
            "gaps": sorted(set(gaps)),
        })
    gap_counts = Counter(gap for case in cases for gap in case["gaps"])
    return {
        "schema_version": "e2-structure-v1-oracle-gap-ledger-1",
        "protocol_id": registry["protocol_id"],
        "source_case_registry_sha256": None,
        "summary": {
            "cases": len(cases),
            "binding_candidate_complete": sum(row["binding_candidate_complete"] for row in cases),
            "dynamic_action_replacement_required": sum(
                row["action_candidate"]["unsupported_coordinate_action_candidate"] for row in cases
            ),
            "formal_oracle_ready": sum(row["formal_oracle_ready"] for row in cases),
            "gap_counts": dict(sorted(gap_counts.items())),
        },
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the E2 per-case Oracle gap ledger")
    parser.add_argument(
        "--registry", type=Path,
        default=REPO_ROOT / "output" / "e2_structure_v1_20260920" / "case_registry.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    registry_path = args.registry.resolve()
    report = build(read_json(registry_path))
    report["source_case_registry_sha256"] = sha256_file(registry_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
