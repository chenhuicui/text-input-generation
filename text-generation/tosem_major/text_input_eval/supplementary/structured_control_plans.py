"""Create conservative offline live-control plans from historical candidate pairs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .structured_live_calibration import atomic_json, read_json


def negative_assertion(inspection: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "all",
        "targets": [
            {"selector": selector, "exists": True}
            for selector in inspection["binding"].values()
        ],
    }


def build_plan(case_id: str, inspection: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    if not all(
        inspection.get(key) is True
        for key in ("binding_complete", "version_matches", "foreground_matches")
    ):
        raise ValueError(f"{case_id} binding is not ready")
    if candidate.get("status") != "pair_available":
        raise ValueError(f"{case_id} lacks a historical candidate pair")
    positive = candidate["positive_control"]["candidate"]
    negative = candidate["negative_control"]["candidate"]
    if set(positive) != set(inspection["binding"]) or set(negative) != set(inspection["binding"]):
        raise ValueError(f"{case_id} candidate/binding keys differ")
    return {
        "schema_version": "e2-structure-v1-live-control-plan-1",
        "case_id": case_id,
        "positive_control": positive,
        "negative_control": negative,
        "candidate_provenance": {
            "catalog": "../../augmented_control_candidates.json",
            "positive_source_sha256": candidate["positive_control"]["source"]["sha256"],
            "negative_source_sha256": candidate["negative_control"]["source"]["sha256"],
            "meaning": "Frozen-validator outcomes select candidates only; current App controls decide behavior.",
        },
        "verify_action": inspection["verify_action"],
        "goal_assertion": inspection["goal_assertion"],
        "negative_assertion": negative_assertion(inspection),
        "observation_seconds": 15,
        "max_back_presses": 3,
        "network_mode": "offline",
        "remote_baseline_status": "not_applicable",
        "remote_baseline_note": (
            "Both control arms must verify Android airplane mode; no provider-side mutation is admitted."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create missing conservative offline App-control plans")
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--cases-root", type=Path, required=True)
    parser.add_argument("--case", action="append")
    parser.add_argument("--output-name", default="control_plan.json")
    args = parser.parse_args()
    candidates = read_json(args.candidates.resolve())
    index = {
        row["case_id"]: row
        for row in candidates["cases"]
        if row.get("task") == "valid_pptr" and row.get("status") == "pair_available"
    }
    selected = sorted(set(args.case or index))
    created = []
    skipped = []
    for case_id in selected:
        recalibrated = args.cases_root / case_id / "inspection_binding_recalibrated.json"
        inspection_path = (
            recalibrated if recalibrated.is_file()
            else args.cases_root / case_id / "inspection.json"
        )
        plan_path = args.cases_root / case_id / args.output_name
        if plan_path.is_file():
            skipped.append(case_id)
            continue
        plan = build_plan(case_id, read_json(inspection_path), index[case_id])
        atomic_json(plan_path, plan)
        created.append(case_id)
    print(json.dumps({"created": created, "skipped_existing": skipped}, ensure_ascii=False))


if __name__ == "__main__":
    main()
