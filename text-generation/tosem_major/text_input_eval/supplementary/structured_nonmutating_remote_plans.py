"""Freeze reviewed online controls that do not mutate provider state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .structured_live_calibration import atomic_json, read_json


AUTH_PROBES = {
    "P001": "reserved example.com email challenge; no account session is established",
    "P035": "unowned group-id sign-in probe; no account session is established",
    "P066": "reserved test-number authentication challenge; no account session is established",
    "P098": "reserved test-number authentication challenge; no account session is established",
}

# These pages expose a local input-validity transition before the final remote
# Save/Create action.  Calibrate that transition and deliberately omit mutation.
LOCAL_ONLY_ACTIONS = {
    "P010": [{"action": "wait", "seconds": 0.2}],
    "P087": [{"action": "wait", "seconds": 0.2}],
}

SAFE_POSITIVE_OVERRIDES = {
    "P066": {"com.opentable:id/phone_input_field": "+12025550123"},
    "P098": {"com.vkontakte.android:id/phone_edit_text": "+12025550123"},
}


def selected_plan(case_root: Path) -> Path | None:
    for name in (
        "control_plan_strong_negative.json", "control_plan_manual.json",
        "control_plan_augmented.json", "control_plan_expanded.json",
        "control_plan.json", "control_plan_offline.json",
    ):
        path = case_root / name
        if path.is_file():
            return path
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Review nonmutating online control plans")
    parser.add_argument("--cases-root", type=Path, required=True)
    args = parser.parse_args()
    updated = []
    for case_id in sorted(set(AUTH_PROBES) | set(LOCAL_ONLY_ACTIONS)):
        path = selected_plan(args.cases_root.resolve() / case_id)
        if path is None:
            raise FileNotFoundError(case_id)
        plan = read_json(path)
        plan.update({
            "network_mode": "online",
            "remote_baseline_status": "not_applicable",
            "remote_baseline_note": (
                AUTH_PROBES.get(case_id)
                or "The frozen action observes only local input acceptance and omits remote Save/Create."
            ),
            "remote_state_review": {
                "classification": (
                    "nonmutating_rejected_auth_challenge"
                    if case_id in AUTH_PROBES else "local_input_transition_before_remote_mutation"
                ),
                "provider_mutation_observed": False,
            },
        })
        if case_id in LOCAL_ONLY_ACTIONS:
            plan["verify_action"] = LOCAL_ONLY_ACTIONS[case_id]
            plan["recalibration_action_source"] = "plan"
        if case_id in SAFE_POSITIVE_OVERRIDES:
            plan["positive_control"] = SAFE_POSITIVE_OVERRIDES[case_id]
        atomic_json(path, plan)
        updated.append(case_id)
    print(json.dumps({"updated": updated}, ensure_ascii=False))


if __name__ == "__main__":
    main()
