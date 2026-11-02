"""Mark reviewed network-backed query controls as online and remote-read-only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .structured_live_calibration import atomic_json, read_json


# These actions search, select, or open public catalog data.  They do not
# create accounts, change profiles, post content, purchase, or save records.
READ_ONLY_QUERY_CASES = {
    "P018", "P020", "P022", "P024", "P029", "P030", "P031", "P034",
    "P037", "P039", "P040", "P047", "P052", "P063", "P071", "P079",
    "P083", "P094", "P095", "P096", "P105", "P106", "P112",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Enable online read-only query controls")
    parser.add_argument("--cases-root", type=Path, required=True)
    args = parser.parse_args()
    updated = []
    missing = []
    for case_id in sorted(READ_ONLY_QUERY_CASES):
        case_root = args.cases_root.resolve() / case_id
        path = next(
            (
                case_root / name
                for name in (
                    "control_plan_strong_negative.json",
                    "control_plan_manual.json",
                    "control_plan_augmented.json",
                    "control_plan_expanded.json",
                    "control_plan.json",
                    "control_plan_offline.json",
                )
                if (case_root / name).is_file()
            ),
            None,
        )
        if path is None:
            missing.append(case_id)
            continue
        plan = read_json(path)
        plan.update({
            "network_mode": "online",
            "remote_baseline_status": "not_applicable",
            "remote_baseline_note": (
                "Reviewed action contract performs a read-only public query/selection; "
                "it does not mutate account-scoped provider state."
            ),
            "remote_state_review": {
                "classification": "read_only_public_query",
                "mutation_actions_excluded": [
                    "account_creation", "profile_update", "content_post",
                    "purchase", "record_save",
                ],
            },
        })
        atomic_json(path, plan)
        updated.append(case_id)
    print(json.dumps({"updated": updated, "missing": missing}, ensure_ascii=False))


if __name__ == "__main__":
    main()
