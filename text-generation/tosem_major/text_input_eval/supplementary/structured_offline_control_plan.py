"""Derive an airplane-mode calibration plan from an existing reviewed plan."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .structured_live_calibration import atomic_json, read_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze an offline copy of a reviewed control plan")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = read_json(args.input.resolve())
    if not all(key in plan for key in (
        "case_id", "positive_control", "negative_control", "negative_assertion",
    )):
        raise ValueError("input control plan is incomplete")
    plan.pop("remote_rollback_control", None)
    plan["network_mode"] = "offline"
    plan["remote_baseline_status"] = "not_applicable"
    plan["remote_baseline_note"] = (
        "Both control arms verified Android airplane mode; the observed goal does not require provider state."
    )
    atomic_json(args.output.resolve(), plan)
    print(json.dumps({"case_id": plan["case_id"], "network_mode": "offline"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
