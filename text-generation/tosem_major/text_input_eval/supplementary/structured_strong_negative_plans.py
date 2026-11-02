"""Build blank-field negative controls for App differential recalibration."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import text_input_eval.validators  # noqa: F401 - populate registry
from text_input_eval.validators.registry import get_validator

from .structured_live_calibration import atomic_json, read_json


SOURCE_PREFERENCE = (
    "control_plan_manual.json",
    "control_plan_augmented.json",
    "control_plan.json",
    "control_plan_offline.json",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create stronger blank negative control plans")
    parser.add_argument("--cases-root", type=Path, required=True)
    parser.add_argument("--case", action="append")
    args = parser.parse_args()
    requested = set(args.case or [])
    created = []
    skipped = []
    for case_root in sorted(args.cases_root.resolve().glob("P*")):
        case_id = case_root.name
        if requested and case_id not in requested:
            continue
        source = next(
            (case_root / name for name in SOURCE_PREFERENCE if (case_root / name).is_file()),
            None,
        )
        if source is None:
            skipped.append({"case_id": case_id, "reason": "no_source_plan"})
            continue
        plan = read_json(source)
        positive = plan.get("positive_control")
        if not isinstance(positive, dict) or not positive:
            skipped.append({"case_id": case_id, "reason": "positive_missing"})
            continue
        validator = get_validator(case_id)
        negative = None
        negative_policy = None
        for value, policy in (
            ("", "all_bound_fields_empty"),
            ("__invalid__", "all_bound_fields_invalid_token"),
            ("0", "all_bound_fields_zero"),
        ):
            candidate = {key: value for key in positive}
            try:
                verdict = validator.validate(candidate)
            except Exception:
                continue
            if verdict is False:
                negative = candidate
                negative_policy = policy
                break
        if negative is None:
            skipped.append({"case_id": case_id, "reason": "blank_not_validator_negative"})
            continue
        if negative == plan.get("negative_control"):
            skipped.append({"case_id": case_id, "reason": "already_blank_negative"})
            continue
        revised = {
            **plan,
            "schema_version": "e2-structure-v1-live-control-plan-strong-negative-1",
            "negative_control": negative,
            "strong_negative_provenance": {
                "source_plan": source.name,
                "policy": negative_policy,
                "validator_result": False,
                "meaning": "Candidate selection only; live App controls still decide behavior.",
            },
        }
        output = case_root / "control_plan_strong_negative.json"
        atomic_json(output, revised)
        created.append(case_id)
    print(json.dumps({"created": created, "skipped": skipped}, ensure_ascii=False))


if __name__ == "__main__":
    main()
