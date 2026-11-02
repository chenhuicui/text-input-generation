"""Reconcile all E2 live-inspection and local-control calibration artifacts."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .structured_live_calibration import atomic_json, case_contract_sha256, read_json
from .structured_snapshot_backup import sha256_file


def issue_ids(rows: list[dict[str, Any]], key: str) -> list[str]:
    return [
        row["case_id"] for row in rows
        if any(key in issue for issue in row.get("issues", []))
    ]


def build(registry_path: Path, cases_root: Path) -> dict[str, Any]:
    registry = read_json(registry_path)
    expected = {case["case_id"]: case for case in registry["cases"]}
    rows = []
    evidence = []
    missing = []
    for case_id, case in expected.items():
        path = cases_root / case_id / "inspection.json"
        if not path.is_file():
            missing.append(case_id)
            continue
        row = read_json(path)
        if row.get("case_id") != case_id:
            raise RuntimeError(f"inspection case ID mismatch: {path}")
        row["_task"] = case["task"]
        rows.append(row)
        evidence.append({
            "case_id": case_id,
            "task": case["task"],
            "status": row.get("status"),
            "avd": row.get("avd"),
            "path": str(path),
            "sha256": sha256_file(path),
        })
    extras = sorted(path.parent.name for path in cases_root.glob("*/inspection.json") if path.parent.name not in expected)
    if missing or extras or len(rows) != len(expected):
        raise RuntimeError(f"inspection universe mismatch: missing={missing} extras={extras}")
    registry_hash = sha256_file(registry_path)
    drift = [
        row["case_id"] for row in rows
        if row["_task"] == "valid_pptr"
        if row.get("registry_sha256") != registry_hash
        and row.get("case_contract_sha256") != case_contract_sha256(expected[row["case_id"]])
    ]
    if drift:
        raise RuntimeError(
            "one or more inspections reference a different immutable case contract: "
            f"{drift[:8]}"
        )
    backup_hashes = {row.get("backup_manifest_sha256") for row in rows}
    if len(backup_hashes) != 1 or None in backup_hashes:
        raise RuntimeError("inspection backup-manifest hashes are inconsistent")

    by_task: dict[str, Counter[str]] = defaultdict(Counter)
    by_avd: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        by_task[row["_task"]][row["status"]] += 1
        by_avd[str(row.get("avd"))][row["status"]] += 1

    control_rows = []
    for path in sorted(cases_root.glob("*/*/control_result.json")):
        result = read_json(path)
        case_id = result.get("case_id")
        if case_id not in expected or expected[case_id]["task"] != "valid_pptr":
            continue
        control_rows.append({
            "case_id": result.get("case_id"),
            "status": result.get("status"),
            "remote_baseline": result.get("remote_baseline", {}).get("status"),
            "network_mode": result.get("network_isolation", {}).get("mode"),
            "error_type": result.get("error_type"),
            "error": result.get("error"),
            "formal_oracle_promoted": result.get("formal_oracle_promoted"),
            "attempt": path.parent.name,
            "path": str(path),
            "sha256": sha256_file(path),
        })

    issue_map = {
        "foreground_mismatch": [row["case_id"] for row in rows if row.get("foreground_matches") is False],
        "version_mismatch": [row["case_id"] for row in rows if row.get("version_matches") is False],
        "binding_incomplete": [row["case_id"] for row in rows if row.get("binding_complete") is False],
        "binding_issue": issue_ids(rows, "binding"),
        "action_issue": issue_ids(rows, "action"),
        "goal_issue": issue_ids(rows, "goal"),
    }
    legacy_status_counts = Counter(row["status"] for row in rows)
    pptr_rows = [row for row in rows if row["_task"] == "valid_pptr"]
    status_counts = Counter(row["status"] for row in pptr_rows)
    review_count = sum(row["status"] == "review_required" for row in pptr_rows)
    control_count = len({row["case_id"] for row in control_rows})
    passed_case_ids = {
        row["case_id"] for row in control_rows
        if row["status"] == "controls_passed_local_only"
    }
    control_error_case_ids = sorted(
        {row["case_id"] for row in control_rows if row["status"] == "control_error"}
        - passed_case_ids
    )
    passed_control_count = len(passed_case_ids)
    control_error_count = len(control_error_case_ids)
    remote_unresolved = sum(
        row["status"] == "controls_passed_local_only"
        and row["remote_baseline"] not in {"verified", "not_applicable"}
        for row in control_rows
    )
    promoted_count = sum(row["formal_oracle_promoted"] is True for row in control_rows)
    controlled_ids = {row["case_id"] for row in control_rows}
    inspect_ready_ids = {
        row["case_id"] for row in pptr_rows if row["status"] == "inspect_ready"
    }
    review_required_ids = sorted(
        row["case_id"] for row in pptr_rows if row["status"] == "review_required"
    )
    control_error_rows = [
        row for row in control_rows
        if row["status"] == "control_error" and row["case_id"] in control_error_case_ids
    ]
    remote_unresolved_ids = sorted(
        row["case_id"] for row in control_rows
        if row["status"] == "controls_passed_local_only"
        and row["remote_baseline"] not in {"verified", "not_applicable"}
    )
    promoted_ids = sorted(
        row["case_id"] for row in control_rows
        if row["formal_oracle_promoted"] is True
    )
    unpromoted_local_pass_ids = sorted(
        row["case_id"] for row in control_rows
        if row["status"] == "controls_passed_local_only"
        and row["formal_oracle_promoted"] is not True
    )
    formal_ready_ids = sorted(
        case_id for case_id, case in expected.items()
        if case["protocol_readiness"]["formal_run_ready"] is True
    )
    bug_predicate_ready_ids = sorted(
        case_id for case_id, case in expected.items()
        if case["task"] == "invalid_bug"
        and case["formal_oracle"].get("oracle_kind") == "string_predicate"
        and case["protocol_readiness"]["formal_run_ready"] is True
    )
    pptr_unready_ids = sorted(
        case_id for case_id, case in expected.items()
        if case["task"] == "valid_pptr"
        and case["protocol_readiness"]["formal_run_ready"] is not True
    )
    binding_review_ids = sorted(
        case_id for case_id, case in expected.items()
        if case["protocol_readiness"]["binding_review_required"] is True
    )
    formal_blockers = []
    if review_count:
        formal_blockers.append(f"{review_count} PPTR cases require live Oracle review")
    if pptr_unready_ids:
        formal_blockers.append(
            f"{len(pptr_unready_ids)} of 115 PPTR cases lack a complete formal App Oracle/reset/recovery contract"
        )
    if control_error_count:
        formal_blockers.append(
            f"{control_error_count} live control case"
            f"{'s have' if control_error_count != 1 else ' has'} unresolved control errors"
        )
    if remote_unresolved:
        formal_blockers.append(
            "remote-baseline applicability remains unresolved for "
            f"{remote_unresolved} controlled case{'s' if remote_unresolved != 1 else ''}"
        )
    if binding_review_ids:
        formal_blockers.append(
            f"{len(binding_review_ids)} PPTR cases still require binding review"
        )
    formal_ready = len(formal_ready_ids) == len(expected)
    by_task["invalid_bug"] = Counter({"string_predicate_ready": len(bug_predicate_ready_ids)})
    return {
        "schema_version": "e2-structure-v1-live-calibration-summary-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "registry_sha256": registry_hash,
        "backup_manifest_sha256": next(iter(backup_hashes)),
        "coverage": {
            "expected": len(expected),
            "inspected": len(rows),
            "unique_case_ids": len({row["case_id"] for row in rows}),
            "complete": len(rows) == len(expected),
        },
        "status_counts": dict(sorted(status_counts.items())),
        "legacy_all_case_app_inspection_status_counts": dict(sorted(legacy_status_counts.items())),
        "by_task": {key: dict(sorted(value.items())) for key, value in sorted(by_task.items())},
        "by_avd": {key: dict(sorted(value.items())) for key, value in sorted(by_avd.items())},
        "infrastructure_error_case_ids": [
            row["case_id"] for row in rows if row["status"] == "infrastructure_error"
        ],
        "review_ledger": {
            key: {"count": len(ids), "case_ids": ids} for key, ids in issue_map.items()
        },
        "local_controls": {
            "executed": len(control_rows),
            "executed_cases": control_count,
            "passed_local_only": passed_control_count,
            "control_errors": control_error_count,
            "formal_oracle_promoted": sum(row["formal_oracle_promoted"] is True for row in control_rows),
            "cases": control_rows,
        },
        "formal_feasibility": {
            "formal_ready_case_ids": formal_ready_ids,
            "app_oracle_promoted_case_ids": promoted_ids,
            "bug_string_predicate_ready_case_ids": bug_predicate_ready_ids,
            "pptr_formal_unready_case_ids": pptr_unready_ids,
            "local_pass_not_promoted_case_ids": unpromoted_local_pass_ids,
            "remote_baseline_unresolved_case_ids": remote_unresolved_ids,
            "control_error_cases": control_error_rows,
            "inspect_ready_without_control_case_ids": sorted(inspect_ready_ids - controlled_ids),
            "review_required_case_ids": review_required_ids,
            "binding_review_required_case_ids": binding_review_ids,
        },
        "formal_experiment_ready": formal_ready,
        "formal_blockers": formal_blockers,
        "inspection_evidence": evidence,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize all E2 live calibration artifacts")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--cases-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.registry.resolve(), args.cases_root.resolve())
    atomic_json(args.output.resolve(), report)
    print(json.dumps({
        "coverage": report["coverage"],
        "status_counts": report["status_counts"],
        "local_controls": {
            key: report["local_controls"][key]
            for key in (
                "executed", "passed_local_only", "control_errors",
                "formal_oracle_promoted",
            )
        },
        "formal_experiment_ready": report["formal_experiment_ready"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
