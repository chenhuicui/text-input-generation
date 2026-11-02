"""Freeze conservative, human-reviewed PPTR candidate pairs for local App controls.

These values are calibration probes, not expected answers and not Oracles.
They are restricted to cases whose controls can be executed with Android
airplane mode enabled and whose effects are discarded by snapshot reset.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .structured_android import canonical_json
from .structured_live_calibration import atomic_json, read_json
from .structured_snapshot_backup import sha256_file


PAIRS: dict[str, dict[str, dict[str, str]]] = {
    "P007": {
        "positive": {"ion-input-0": "2025551234"},
        "negative": {"ion-input-0": "abc"},
    },
    "P016": {
        "positive": {"homeSearchField": "10001"},
        "negative": {"homeSearchField": "invalid"},
    },
    "P017": {
        "positive": {
            "com.cars.android:id/carPrice_or_monthlyPayment_input": "30000",
            "com.cars.android:id/down_payment_input": "5000",
            "com.cars.android:id/trade_in_value_input": "3000",
            "com.cars.android:id/sales_tax_input": "6.5",
            "com.cars.android:id/interest_rate_input": "5.9",
        },
        "negative": {
            "com.cars.android:id/carPrice_or_monthlyPayment_input": "",
            "com.cars.android:id/down_payment_input": "",
            "com.cars.android:id/trade_in_value_input": "",
            "com.cars.android:id/sales_tax_input": "",
            "com.cars.android:id/interest_rate_input": "",
        },
    },
    "P033": {
        "positive": {
            "compassTextInput::#::0": "Chenhui",
            "compassTextInput::#::1": "Cui",
            "compassTextInput::#::2": "chenhui.calibration@example.com",
            "compassTextInput::#::3": "2025551234",
            "compassTextInput::#::4": "1990-01-01",
        },
        "negative": {
            "compassTextInput::#::0": "Chenhui",
            "compassTextInput::#::1": "Cui",
            "compassTextInput::#::2": "not-an-email",
            "compassTextInput::#::3": "2025551234",
            "compassTextInput::#::4": "1990-01-01",
        },
    },
    "P041": {
        "positive": {"com.ibragunduz.applockpro:id/search_src_text": "Safety"},
        "negative": {"com.ibragunduz.applockpro:id/search_src_text": "__no_such_app__"},
    },
    "P044": {
        "positive": {"com.jeyluta.timestampcamerafree:id/custom_editText": "Capture the Moment!"},
        "negative": {"com.jeyluta.timestampcamerafree:id/custom_editText": ""},
    },
    "P046": {
        "positive": {"com.kroger.mobile:id/generic_input": "10001"},
        "negative": {"com.kroger.mobile:id/generic_input": "abc"},
    },
    "P048": {
        "positive": {"personal-info-input": "newuser@example.com"},
        "negative": {"personal-info-input": "not-an-email"},
    },
    "P049": {
        "positive": {"com.limebike:id/phone_number_input": "2025551234"},
        "negative": {"com.limebike:id/phone_number_input": "1"},
    },
    "P054": {
        "positive": {"com.musicplayer.playermusic:id/etName": "Jamie"},
        "negative": {"com.musicplayer.playermusic:id/etName": ""},
    },
    "P059": {
        "positive": {
            "com.nihaojewelry.nihao:id/et_first_name": "Chenhui",
            "com.nihaojewelry.nihao:id/et_last_name": "Cui",
            "com.nihaojewelry.nihao:id/et_phone_number": "2025551234",
            "com.nihaojewelry.nihao:id/et_search_address": "",
            "com.nihaojewelry.nihao:id/et_zip_code": "94043",
            "com.nihaojewelry.nihao:id/et_city": "Mountain View",
            "com.nihaojewelry.nihao:id/et_address_line_2": "",
            "com.nihaojewelry.nihao:id/et_address_line_1": "1600 Amphitheatre Parkway",
        },
        "negative": {
            "com.nihaojewelry.nihao:id/et_first_name": "",
            "com.nihaojewelry.nihao:id/et_last_name": "Cui",
            "com.nihaojewelry.nihao:id/et_phone_number": "2025551234",
            "com.nihaojewelry.nihao:id/et_search_address": "",
            "com.nihaojewelry.nihao:id/et_zip_code": "94043",
            "com.nihaojewelry.nihao:id/et_city": "Mountain View",
            "com.nihaojewelry.nihao:id/et_address_line_2": "",
            "com.nihaojewelry.nihao:id/et_address_line_1": "1600 Amphitheatre Parkway",
        },
    },
    "P071": {
        "positive": {"com.qvon.novellair:id/etSearch": "Bride of the Cursed Alpha"},
        "negative": {"com.qvon.novellair:id/etSearch": "__no_such_book__"},
    },
    "P073": {
        "positive": {"com.redfin.android:id/nameField": "Downtown Condos 2"},
        "negative": {"com.redfin.android:id/nameField": ""},
    },
    "P075": {
        "positive": {"com.rnovel.android.app:id/et_input": "mariasmith456"},
        "negative": {"com.rnovel.android.app:id/et_input": ""},
    },
    "P103": {
        "positive": {
            "com.xogrp.planner:id/et_user_first_name": "Sophia",
            "com.xogrp.planner:id/et_user_last_name": "Smith",
            "com.xogrp.planner:id/et_partner_first_name": "Liam",
            "com.xogrp.planner:id/et_partner_last_name": "Garcia",
        },
        "negative": {
            "com.xogrp.planner:id/et_user_first_name": "",
            "com.xogrp.planner:id/et_user_last_name": "Smith",
            "com.xogrp.planner:id/et_partner_first_name": "Liam",
            "com.xogrp.planner:id/et_partner_last_name": "Garcia",
        },
    },
    "P101": {
        "positive": {"android:id/edit": "Oliver"},
        "negative": {"android:id/edit": ""},
    },
    "P104": {
        "positive": {"com.yelp.android.biz:id/full_bleed_edit_text": "Alice"},
        "negative": {"com.yelp.android.biz:id/full_bleed_edit_text": ""},
    },
}


def source(case_id: str, arm: str, candidate: dict[str, str]) -> dict[str, Any]:
    value = {
        "kind": "human_reviewed_local_calibration_probe",
        "case_id": case_id,
        "arm": arm,
        "candidate": candidate,
        "network_policy": "airplane_mode_required",
        "meaning": "Calibration probe only; current App controls decide behavior.",
    }
    return {**value, "sha256": hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()}


def build(registry_path: Path, cases_root: Path) -> dict[str, Any]:
    registry = read_json(registry_path)
    case_by_id = {row["case_id"]: row for row in registry["cases"]}
    cases = []
    for case_id, pair in sorted(PAIRS.items()):
        case = case_by_id[case_id]
        recalibrated = cases_root / case_id / "inspection_binding_recalibrated.json"
        inspection = read_json(
            recalibrated if recalibrated.is_file()
            else cases_root / case_id / "inspection.json"
        )
        required = set(case["required_resource_ids"])
        if inspection.get("status") != "inspect_ready":
            raise ValueError(f"{case_id} inspection is not ready")
        if set(pair["positive"]) != required or set(pair["negative"]) != required:
            raise ValueError(f"{case_id} candidate keys differ from the frozen case")
        cases.append({
            "case_id": case_id,
            "task": case["task"],
            "status": "pair_available",
            "examined_exact_key_candidates": 0,
            "positive_control": {
                "candidate": pair["positive"],
                "source": source(case_id, "positive", pair["positive"]),
            },
            "negative_control": {
                "candidate": pair["negative"],
                "source": source(case_id, "negative", pair["negative"]),
            },
        })
    return {
        "schema_version": "e2-structure-v1-manual-control-candidates-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "registry_sha256": sha256_file(registry_path),
        "policy": "airplane mode plus snapshot rollback; App controls remain mandatory",
        "counts": {"pair_available": len(cases)},
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze reviewed local PPTR calibration probes")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--cases-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.registry.resolve(), args.cases_root.resolve())
    atomic_json(args.output.resolve(), report)
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
