"""Freeze reviewed binding-selector migrations against captured live XML."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from xml.etree import ElementTree as ET

from .structured_android import binding_nodes
from .structured_live_calibration import atomic_json, read_json
from .structured_snapshot_backup import sha256_file


REMEDIATIONS = {
    "P014": {
        field: {
            "package": "com.BabyName.start",
            "class": "android.widget.EditText",
            "instance": index,
            "unique": True,
        }
        for index, field in enumerate(("ion-input-0", "ion-input-1", "ion-input-2"))
    },
    "P033": {
        f"compassTextInput::#::{index}": {
            "package": "com.getyourguide.android",
            "class": "android.widget.EditText",
            "resource-id": "compassTextInput",
            "descendant-text": label,
        }
        for index, label in enumerate((
            "First name", "Last name", "Email", "Mobile phone number", "Date of birth",
        ))
    },
    "P040": {
        "com.hoopladigital.android:id/search": {
            "package": "com.hoopladigital.android",
            "class": "android.widget.EditText",
            "resource-id": "com.hoopladigital.android:id/search",
        },
    },
    "P059": {
        field: {
            "package": "com.nihaojewelry.nihao",
            "class": "android.widget.EditText",
            "resource-id": field,
        }
        for field in (
            "com.nihaojewelry.nihao:id/et_first_name",
            "com.nihaojewelry.nihao:id/et_last_name",
            "com.nihaojewelry.nihao:id/et_search_address",
            "com.nihaojewelry.nihao:id/et_address_line_1",
            "com.nihaojewelry.nihao:id/et_address_line_2",
            "com.nihaojewelry.nihao:id/et_city",
            "com.nihaojewelry.nihao:id/et_zip_code",
            "com.nihaojewelry.nihao:id/et_phone_number",
        )
    },
    "P081": {
        field: {
            "package": "com.shopify.arrive",
            "class": "android.widget.EditText",
            "unique": True,
            "instance": index,
        }
        for index, field in enumerate((
            "TRACKING_NUMBER_INPUT", "DELIVERY_NAME_INPUT", "CARRIER_NAME_INPUT",
        ))
    },
    "P093": {
        "phoneInput": {
            "package": "com.tickpickllc.ceobrien.tickpick",
            "class": "android.widget.EditText",
            "unique": True,
        },
    },
    "P095": {
        "com.trubeacon.scooters_mobile_android:id/locationSearchEditText": {
            "package": "com.trubeacon.scooters_mobile_android",
            "class": "android.widget.EditText",
            "unique": True,
        },
    },
    "P101": {
        "android:id/edit": {
            "package": "com.wonder",
            "class": "android.widget.EditText",
            "resource-id": "com.wonder:id/first_name_text_field",
        },
    },
    "P104": {
        "com.yelp.android.biz:id/full_bleed_edit_text": {
            "package": "com.yelp.android.biz",
            "class": "android.widget.EditText",
            "resource-id": "com.yelp.android.biz:id/full_bleed_edit_text",
            "instance": 0,
            "unique": True,
        },
    },
    "P072": {
        "com.radio.pocketfm:id/search_box": {
            "package": "com.radio.pocketfm",
            "class": "android.widget.EditText",
            "resource-id": "com.radio.pocketfm:id/search_box",
        },
    },
}


# P059 is a single native ScrollView form.  The frozen top and bottom viewport
# captures jointly attest all eight stable IDs, while any one hierarchy exposes
# only six of them.  The Android driver resolves the two offscreen bindings by
# scrolling the App-owned container before every write.
OFFSCREEN_BINDINGS = {
    "P059": {
        "com.nihaojewelry.nihao:id/et_phone_number",
        "com.nihaojewelry.nihao:id/et_zip_code",
    },
}


PREPARE_ACTIONS = {
    "P040": [
        {
            "action": "click",
            "selector": {
                "package": "com.hoopladigital.android",
                "resource-id": "com.hoopladigital.android:id/settings",
            },
        },
        {
            "action": "click",
            "selector": {
                "package": "com.hoopladigital.android",
                "resource-id": "com.hoopladigital.android:id/sign_up_button",
            },
        },
        {
            "action": "click",
            "selector": {
                "package": "com.hoopladigital.android",
                "resource-id": "com.hoopladigital.android:id/start",
            },
        },
        {"action": "wait", "seconds": 3},
    ],
    "P072": [{
        "action": "click",
        "selector": {
            "package": "com.radio.pocketfm",
            "class": "android.widget.ImageButton",
            "resource-id": "com.radio.pocketfm:id/search_box",
        },
    }],
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Write reviewed live binding migrations")
    parser.add_argument("--cases-root", type=Path, required=True)
    args = parser.parse_args()
    written = []
    for case_id, binding in REMEDIATIONS.items():
        case_root = args.cases_root.resolve() / case_id
        source_path = case_root / "inspection.json"
        xml_path = case_root / "initial.xml"
        source = read_json(source_path)
        root = ET.parse(xml_path).getroot()
        counts = {field: len(binding_nodes(root, selector)) for field, selector in binding.items()}
        if case_id in PREPARE_ACTIONS:
            action_selector = PREPARE_ACTIONS[case_id][0]["selector"]
            activation_count = sum(
                all(node.get(key, "") == expected for key, expected in action_selector.items())
                for node in root.iter("node")
            )
            if activation_count != 1 or any(count != 0 for count in counts.values()):
                raise RuntimeError(
                    f"{case_id} activation/binding baseline is unexpected: "
                    f"activation={activation_count}, binding={counts}"
                )
        elif any(
            count != 1 and not (
                field in OFFSCREEN_BINDINGS.get(case_id, set()) and count == 0
            )
            for field, count in counts.items()
        ):
            raise RuntimeError(f"{case_id} reviewed binding is not unique: {counts}")
        value = {
            **source,
            "status": "inspect_ready",
            "binding": binding,
            "binding_complete": True,
            "version_matches": True if case_id == "P059" else source.get("version_matches"),
            "issues": [
                issue for issue in source.get("issues", [])
                if "binding" not in issue
            ],
            "binding_remediation": {
                "source": "reviewed_live_xml_instance_migration",
                "source_inspection_sha256": sha256_file(source_path),
                "evidence_xml": str(xml_path),
                "evidence_xml_sha256": sha256_file(xml_path),
                "match_counts": counts,
                "prepare_action": PREPARE_ACTIONS.get(case_id, []),
                "live_post_prepare_uniqueness_required": case_id in PREPARE_ACTIONS,
                "offscreen_bindings": sorted(OFFSCREEN_BINDINGS.get(case_id, set())),
                "version_alignment": (
                    "freeze_live_installed_artifact_set_2523_against_scroll_union"
                    if case_id == "P059" else None
                ),
            },
        }
        atomic_json(case_root / "inspection_binding_recalibrated.json", value)
        written.append(case_id)
    print(json.dumps({"written": written}, ensure_ascii=False))


if __name__ == "__main__":
    main()
