"""Attach stable per-case contract hashes to existing live inspections."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml

from .structured_live_calibration import (
    atomic_json,
    assertion,
    case_contract_sha256,
    goal_candidate,
    read_json,
)
from .structured_snapshot_backup import sha256_file


def artifact_set_sha256(rows: list[dict[str, object]]) -> str | None:
    if not rows or any(
        not isinstance(row.get("sha256"), str) or len(str(row["sha256"])) != 64
        for row in rows
    ):
        return None
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def backfill(registry_path: Path, cases_root: Path) -> dict[str, int]:
    registry = read_json(registry_path)
    registry_hash = sha256_file(registry_path)
    changed = 0
    verified = 0
    for case in registry["cases"]:
        if case["task"] != "valid_pptr":
            continue
        path = cases_root / case["case_id"] / "inspection.json"
        row = read_json(path)
        if row.get("case_id") != case["case_id"]:
            raise RuntimeError(f"inspection case ID mismatch: {path}")
        expected = case_contract_sha256(case)
        if row.get("case_contract_sha256") != expected:
            raise RuntimeError(f"inspection immutable contract differs from registry: {path}")
        artifact = row.get("artifact")
        if not isinstance(artifact, dict):
            verified += 1
            continue
        digest = artifact_set_sha256(artifact.get("installed_files", []))
        source_match = str(artifact.get("version_code")) == str(case["mirror"].get("version_code"))
        old = json.dumps(row, ensure_ascii=False, sort_keys=True)
        artifact["artifact_set_sha256"] = digest
        artifact["identity_complete"] = digest is not None and bool(artifact.get("version_code"))
        row["version_matches"] = artifact["identity_complete"]
        row["version_alignment_policy"] = "freeze_live_installed_artifact_set"
        row["source_version_matches"] = source_match
        row["source_version_difference"] = None if source_match else {
            "installed_version": artifact.get("version_code"),
            "historical_expected_version": case["mirror"].get("version_code"),
        }
        row["issues"] = [
            issue for issue in row.get("issues", [])
            if not (
                isinstance(issue, dict) and (
                    ("installed_version" in issue and "expected_version" in issue)
                    or "goal" in issue
                )
            )
        ]
        config_path = Path(case["source_files"]["page_config"])
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        goal, goal_issues = goal_candidate(case["mirror"]["package"], config)
        initial_xml = cases_root / case["case_id"] / row["evidence"]["initial_xml"]
        root = ET.fromstring(initial_xml.read_text(encoding="utf-8"))
        goal_initial = assertion(root, goal) if goal is not None and not goal_issues else None
        row["goal_assertion"] = goal
        row["goal_holds_initially"] = goal_initial
        row["issues"].extend({"goal": issue} for issue in goal_issues)
        if goal_initial is True:
            row["issues"].append({"goal": "goal already holds in the frozen initial state"})
        if artifact["identity_complete"] is not True:
            row["issues"].append({"installed_artifact_identity": "incomplete"})
        if (
            row.get("foreground_matches") is True
            and row.get("binding_complete") is True
            and row.get("goal_holds_initially") is False
            and not row["issues"]
        ):
            row["status"] = "inspect_ready"
        else:
            row["status"] = "review_required"
        new = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if new != old:
            atomic_json(path, row)
            changed += 1
        else:
            verified += 1
    return {"changed": changed, "already_verified": verified, "total": changed + verified}


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill immutable live-calibration contract hashes")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--cases-root", type=Path, required=True)
    args = parser.parse_args()
    result = backfill(args.registry.resolve(), args.cases_root.resolve())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
