"""Write narrow, human-reviewed control corrections for live App evidence.

These corrections preserve the existing plan evidence and change only details
proved stale by a live positive/negative run: an invalid negative value, an
extra action, an oversized generated value, or an immutable frozen field.
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from .structured_live_calibration import atomic_json, read_json
from .structured_snapshot_backup import sha256_file


SOURCE_PLAN = {
    "P001": "control_plan_strong_negative.json",
    "P003": "control_plan_strong_negative.json",
    "P010": "control_plan_recalibrated.json",
    "P014": "control_plan_strong_negative.json",
    "P017": "control_plan_recalibrated.json",
    "P023": "control_plan_validator_fixed.json",
    "P030": "control_plan_expanded.json",
    "P033": "control_plan_recalibrated.json",
    "P034": "control_plan_recalibrated.json",
    "P035": "control_plan_recalibrated.json",
    "P036": "control_plan_strong_negative.json",
    "P038": "control_plan_augmented.json",
    "P039": "control_plan_strong_negative.json",
    "P040": "control_plan_strong_negative.json",
    "P044": "control_plan_manual.json",
    "P046": "control_plan.json",
    "P051": "control_plan_strong_negative.json",
    "P052": "control_plan_strong_negative.json",
    "P053": "control_plan_strong_negative.json",
    "P054": "control_plan.json",
    "P056": "control_plan_expanded.json",
    "P059": "control_plan_manual_v2.json",
    "P065": "control_plan_strong_negative.json",
    "P063": "control_plan_expanded.json",
    "P067": "control_plan_offline.json",
    "P069": "control_plan_validator_fixed.json",
    "P070": "control_plan_validator_fixed.json",
    "P072": "control_plan_strong_negative.json",
    "P073": "control_plan_manual.json",
    "P076": "control_plan_expanded.json",
    "P080": "control_plan_validator_fixed.json",
    "P084": "control_plan_validator_fixed.json",
    "P086": "control_plan_strong_negative.json",
    "P088": "control_plan_strong_negative.json",
    "P089": "control_plan_strong_negative.json",
    "P090": "control_plan_validator_fixed.json",
    "P093": "control_plan_strong_negative.json",
    "P097": "control_plan_strong_negative.json",
    "P101": "control_plan_manual.json",
    "P103": "control_plan_validator_fixed.json",
    "P104": "control_plan_manual.json",
    "P105": "control_plan_strong_negative.json",
    "P107": "control_plan_strong_negative.json",
    "P109": "control_plan_expanded.json",
    "P095": "control_plan_strong_negative.json",
    "P096": "control_plan_strong_negative.json",
    "P115": "control_plan_expanded.json",
}


RECOVERY_FALLBACK_PLAN = (
    "control_plan_recovered.json",
    "control_plan_strong_negative.json",
    "control_plan_manual_v2.json",
    "control_plan_manual.json",
    "control_plan.json",
    "control_plan_offline.json",
)


def source_plan(
    case_root: Path, expected_name: str, *, allow_recovery_fallback: bool,
) -> tuple[Path, bool]:
    recovered = case_root / "control_plan_recovered.json"
    if allow_recovery_fallback and recovered.is_file():
        return recovered, True
    expected = case_root / expected_name
    if expected.is_file():
        return expected, False
    if not allow_recovery_fallback:
        raise FileNotFoundError(expected)
    fallback = next(
        (case_root / name for name in RECOVERY_FALLBACK_PLAN if (case_root / name).is_file()),
        None,
    )
    if fallback is None:
        raise FileNotFoundError(
            f"{case_root.name} lacks expected plan {expected_name} and every recovery fallback"
        )
    return fallback, True


def _review(case_id: str, plan: dict) -> tuple[dict, str]:
    value = deepcopy(plan)
    if case_id == "P001":
        value["negative_control"] = {"ai.chat.gpt.bot:id/inputEditText": "invalid"}
        reason = "blank produced no error; malformed non-empty email is validator-false"
    elif case_id == "P003":
        value["negative_control"] = {"ai.photo.enhancer.photoclear:id/et_input": ""}
        value["verify_action"] = [{
            "action": "click",
            "selector": {
                "package": "ai.photo.enhancer.photoclear",
                "resource-id": "ai.photo.enhancer.photoclear:id/tv_submit",
            },
        }]
        value["network_mode"] = "offline"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "Android airplane mode is verified before both arms; the feedback acknowledgement is local and no provider write can complete."
        )
        value["recalibration_action_source"] = "plan"
        reason = "observe the submission response before the stale Back presses dismiss it; use a truly empty invalid control"
    elif case_id == "P010":
        value["negative_control"] = {
            "co.fable.fable:id/list_title_edit_text": "Invalid title",
            "co.fable.fable:id/list_description_edit_text": "x" * 101,
        }
        value["input_rejection_is_negative"] = True
        reason = "freeze the App-owned 100-character description cap as a complete negative observation"
    elif case_id == "P014":
        value["negative_control"] = {
            "ion-input-0": "Jo",
            "ion-input-1": "an",
            "ion-input-2": "se",
        }
        value["verify_action"] = [
            {"action": "click", "selector": {"package": "com.BabyName.start", "text": "APPLY FILTERS"}},
            {"action": "wait", "seconds": 2},
        ]
        value["recalibration_action_source"] = "plan"
        value["observation_seconds"] = 2
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.BabyName.start",
                    "text": "__visual_observer_only__",
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.BabyName.start",
                    "text": "__visual_observer_only__",
                },
                "exists": False,
            }],
        }
        value["visual_text_observer"] = {
            "crop": [100, 120, 500, 320],
            "pattern": r"List\s+(\d+)",
            "minimum": 1,
            "maximum": 2421,
            "scale": 4,
            "psm": 6,
        }
        value["recovery_action"] = [
            {
                "action": "tap_if_present",
                "guard_selector": {
                    "package": "com.BabyName.start",
                    "text": "No more names? Let’s fix that!",
                },
                "package": "com.BabyName.start",
                "x": 877,
                "y": 763,
                "screen_width": 1080,
                "screen_height": 2400,
            },
            {"action": "wait", "seconds": 0.5},
            {
                "action": "tap",
                "package": "com.BabyName.start",
                "x": 884,
                "y": 200,
                "screen_width": 1080,
                "screen_height": 2400,
            },
            {"action": "wait", "seconds": 1},
        ]
        value["max_back_presses"] = 0
        reason = "observe the App-rendered zero-result count and recover through its blocking empty-result dialog"
    elif case_id == "P017":
        value["negative_control"] = {
            "com.cars.android:id/carPrice_or_monthlyPayment_input": "$27990",
            "com.cars.android:id/down_payment_input": "$5600",
            "com.cars.android:id/trade_in_value_input": "$8000",
            "com.cars.android:id/sales_tax_input": "7.2 %",
            "com.cars.android:id/interest_rate_input": "5.3 %",
        }
        value["input_rejection_is_negative"] = True
        reason = "freeze rejection of display-formatted currency and percentage strings by the numeric editors"
    elif case_id == "P023":
        value["verify_action"] = [{
            "action": "click",
            "selector": {
                "package": "com.duolingo",
                "resource-id": "com.duolingo:id/signinButton",
            },
        }]
        value["recalibration_action_source"] = "plan"
        reason = "replace the stale localized Sign In label with the installed App's stable button id"
    elif case_id == "P030":
        value["negative_control"] = {"com.foxnews.android:id/search_field": ""}
        value["verify_action"] = [
            {"action": "press", "key": "enter"},
            {"action": "wait", "seconds": 2},
        ]
        value["recalibration_action_source"] = "plan"
        reason = "submit the query through the keyboard and remove the stale RecyclerView click"
    elif case_id == "P034":
        value["negative_control"] = {"com.goeuro.rosie:id/suggesterEditor": ""}
        value["prepare_action"] = [{
            "action": "click_if_present",
            "selector": {
                "package": "com.goeuro.rosie",
                "text": "Enter Departure Location",
            },
        }, {"action": "wait", "seconds": 1}]
        value["verify_action"] = [{"action": "wait", "seconds": 2}]
        value["recalibration_action_source"] = "plan"
        reason = "stabilize the optional departure landing page before observing live place suggestions"
    elif case_id == "P035":
        value["negative_control"] = {
            "com.golftripgenius.android.leaguegenius:id/login_edit_ggid": "NOT-A-VALID-GGID"
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.golftripgenius.android.leaguegenius",
                    "text": "This is not a valid GGID",
                },
                "exists": True,
            }],
        }
        value["recovery_action"] = [{
            "action": "click_if_present",
            "selector": {
                "package": "com.golftripgenius.android.leaguegenius",
                "text": "Ok",
            },
        }, {"action": "wait", "seconds": 0.5}]
        value["max_back_presses"] = 0
        reason = "dismiss the invalid-GGID dialog explicitly before clearing the login field"
    elif case_id == "P036":
        value["verify_action"] = [{
            "action": "click",
            "selector": {
                "package": "com.gowish.app",
                "resource-id": "wish-form-primary-action",
            },
        }]
        value["recalibration_action_source"] = "plan"
        reason = "replace the stale uppercase label with the installed App's stable Add-wish action id"
    elif case_id == "P038":
        value["verify_action"] = [{
            "action": "click",
            "selector": {
                "package": "com.hellofresh.androidapp",
                "text": "Next",
            },
        }]
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.hellofresh.androidapp",
                    "text": "How many people will be eating?",
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [
                {
                    "selector": {
                        "package": "com.hellofresh.androidapp",
                        "resource-id": "zipcode-validation-input",
                        "class": "android.widget.EditText",
                    },
                    "exists": True,
                },
                {
                    "selector": {
                        "package": "com.hellofresh.androidapp",
                        "text": "You must enter a valid zip code.",
                    },
                    "exists": True,
                },
            ],
        }
        value["force_cold_reset"] = True
        value["network_mode"] = "online"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "Both arms exercise the unauthenticated meal-plan funnel only; no account or provider state is written."
        )
        value["observation_seconds"] = 10
        value["recalibration_action_source"] = "plan"
        reason = (
            "use the installed App's current Next action and separate the people-count page from its explicit invalid-zip error"
        )
    elif case_id == "P039":
        value["verify_action"] = [{"action": "wait", "seconds": 4}]
        value["recalibration_action_source"] = "plan"
        value["network_mode"] = "online"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "Both arms exercise only the public destination autocomplete and do not mutate provider state."
        )
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.hometogo",
                    "text": "San Francisco County, California, United States",
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [
                {
                    "selector": {
                        "package": "com.hometogo",
                        "text": "Popular destinations",
                    },
                    "exists": True,
                },
                {
                    "selector": {
                        "package": "com.hometogo",
                        "text": "San Francisco County, California, United States",
                    },
                    "exists": False,
                },
            ],
        }
        reason = "observe the App's live destination suggestions instead of clicking the entire Compose root into the date picker"
    elif case_id == "P040":
        value["positive_control"] = {
            "com.hoopladigital.android:id/search": "Beaumont"
        }
        value["negative_control"] = {
            "com.hoopladigital.android:id/search": "zzzx no such library 9f6a"
        }
        value["prepare_action"] = [
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
        ]
        value["verify_action"] = [
            {"action": "wait", "seconds": 2},
            {"action": "press", "key": "enter"},
            {"action": "wait", "seconds": 8},
        ]
        value["recalibration_action_source"] = "plan"
        value["force_cold_reset"] = True
        value["network_mode"] = "online"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "Both arms perform only a public library lookup and do not select a library or create an account."
        )
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.hoopladigital.android",
                    "text": "Beaumont, AB",
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.hoopladigital.android",
                    "text": "Beaumont, AB",
                },
                "exists": False,
            }],
        }
        value["target_page_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.hoopladigital.android",
                    "class": "android.widget.EditText",
                    "resource-id": "com.hoopladigital.android:id/search",
                },
                "exists": True,
            }],
        }
        reason = "hide the IME after submitting a non-empty public lookup so App results and recovery remain observable"
    elif case_id == "P044":
        value["verify_action"] = [
            {
                "action": "click",
                "selector": {
                    "package": "com.jeyluta.timestampcamerafree",
                    "resource-id": "com.jeyluta.timestampcamerafree:id/savecustomtext_link",
                },
            },
            {
                "action": "click",
                "selector": {
                    "package": "com.jeyluta.timestampcamerafree",
                    "resource-id": "com.jeyluta.timestampcamerafree:id/custom_editText_clearbtn",
                },
            },
            {
                "action": "click",
                "selector": {
                    "package": "com.jeyluta.timestampcamerafree",
                    "resource-id": "com.jeyluta.timestampcamerafree:id/loadcustomtext_link",
                },
            },
            {
                "action": "click",
                "selector": {
                    "package": "com.jeyluta.timestampcamerafree",
                    "resource-id": "com.jeyluta.timestampcamerafree:id/title",
                },
                "if_missing": "stop",
            },
            {"action": "wait", "seconds": 0.5},
        ]
        value["recalibration_action_source"] = "plan"
        value["network_mode"] = "offline"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = "The action exercises only the App's snapshot-local custom-text store."
        value["candidate_binding_goal"] = True
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.jeyluta.timestampcamerafree",
                    "text": value["positive_control"][
                        "com.jeyluta.timestampcamerafree:id/custom_editText"
                    ],
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.jeyluta.timestampcamerafree",
                    "text": value["positive_control"][
                        "com.jeyluta.timestampcamerafree:id/custom_editText"
                    ],
                },
                "exists": False,
            }],
        }
        value["recovery_action"] = [
            {
                "action": "click",
                "selector": {
                    "package": "com.jeyluta.timestampcamerafree",
                    "resource-id": "android:id/button1",
                },
            },
            {"action": "wait", "seconds": 0.5},
        ]
        reason = "validate the local save-clear-load round trip for non-empty custom text"
    elif case_id == "P046":
        field = {
            "package": "com.kroger.mobile",
            "class": "android.widget.EditText",
            "resource-id": "com.kroger.mobile:id/generic_input",
        }
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{"selector": field, "exists": False}],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [{"selector": field, "exists": True}],
        }
        value["verify_action"] = [{
            "action": "click",
            "selector": {
                "package": "com.kroger.mobile",
                "class": "android.widget.ImageButton",
                "resource-id": "com.kroger.mobile:id/registration_zip_entry_button",
                "enabled": True,
            },
            "if_missing": "stop",
        }]
        value["observation_seconds"] = 30
        value["network_mode"] = "online"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "The ZIP lookup is read-only; success is the original postal-code editor "
            "leaving the hierarchy, including while the next page still offers a separate "
            "Enter Postal Code action."
        )
        reason = "distinguish the original ZIP editor from the same-named action on the next page"
    elif case_id == "P054":
        value["candidate_text_goal"] = True
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [
                {
                    "selector": {
                        "package": "com.musicplayer.playermusic",
                        "text": "Profile",
                    },
                    "exists": True,
                },
                {
                    "selector": {
                        "package": "com.musicplayer.playermusic",
                        "text": "Done",
                    },
                    "exists": False,
                },
            ],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.musicplayer.playermusic",
                    "text": "Done",
                },
                "exists": True,
            }],
        }
        reason = "require the profile result page to display the current candidate instead of a fixed calibration name"
    elif case_id == "P063":
        value["negative_control"] = {
            "com.olo.ihop:id/pickUpSearchBar": ""
        }
        value["verify_action"] = [{"action": "wait", "seconds": 0.1}]
        value["observation_seconds"] = 0.25
        value["endpoint_settle_seconds"] = 0.1
        value["time_sensitive_local_observation"] = True
        value["network_mode"] = "offline"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "Both arms observe only the App-owned local clear affordance in "
            "airplane mode; no location search request is submitted."
        )
        value["recalibration_action_source"] = "plan"
        clear_icon = {
            "package": "com.olo.ihop",
            "resource-id": "com.olo.ihop:id/pickUpSearchBarClearIcon",
        }
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{"selector": clear_icon, "exists": True}],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [{"selector": clear_icon, "exists": False}],
        }
        reason = "freeze the App-owned clear affordance before the legacy network client can leave the recoverable input page"
    elif case_id == "P053":
        value["verify_action"] = [{"action": "press", "key": "enter"}]
        value["recalibration_action_source"] = "plan"
        reason = "the historical follow-up coordinate only dismissed the search surface and is not part of input acceptance"
    elif case_id == "P056":
        value["verify_action"] = [{"action": "wait", "seconds": 1}]
        value["recalibration_action_source"] = "plan"
        value["network_mode"] = "offline"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "Both arms observe only local CHANGE PASSWORD enablement in airplane mode; the remote password-change action is never invoked."
        )
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.myfitnesspal.android",
                    "class": "android.widget.Button",
                    "resource-id": "com.myfitnesspal.android:id/changePassword",
                    "enabled": True,
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.myfitnesspal.android",
                    "class": "android.widget.Button",
                    "resource-id": "com.myfitnesspal.android:id/changePassword",
                    "enabled": False,
                },
                "exists": True,
            }],
        }
        reason = "replace the irreversible password submission with the App's local enablement behavior for matching valid passwords"
    elif case_id == "P051":
        fixture = (
            Path(__file__).resolve().parents[3]
            / "tosem_major/apk_info/screenshots/screenshot_9.png"
        )
        package = "com.mobile.bizo.inpainting.clothes"
        value["prepare_files"] = [{
            "host_path": str(fixture),
            "device_path": "/sdcard/Download/e2_p051_fixture.png",
            "sha256": "d3f8e37a76969aedc6644ee0a5a8bb0fc50b02f161a52e1445ed83b4b1487729",
            "media_scan": True,
        }]
        value["verify_action"] = [
            {
                "action": "click",
                "selector": {
                    "package": package,
                    "class": "android.widget.Button",
                    "resource-id": f"{package}:id/promptInputDialog_confirm",
                },
            },
            {
                "action": "click",
                "selector": {
                    "package": package,
                    "class": "android.widget.Button",
                    "resource-id": f"{package}:id/menu_source_gallery_text",
                },
            },
            {"action": "wait", "seconds": 2},
            {
                "action": "click",
                "selector": {
                    "package": "com.google.android.documentsui",
                    "class": "android.widget.FrameLayout",
                    "resource-id": "com.google.android.documentsui:id/thumbnail",
                },
            },
            {"action": "wait", "seconds": 30},
            {"action": "wait", "seconds": 15},
            {"action": "press", "key": "back"},
            {"action": "wait", "seconds": 3},
            {
                "action": "click",
                "selector": {
                    "package": package,
                    "class": "android.widget.ImageView",
                    "resource-id": f"{package}:id/effect_tutorialClose",
                },
            },
            {
                "action": "swipe",
                "package": package,
                "start_x": 350,
                "start_y": 900,
                "end_x": 700,
                "end_y": 1400,
                "duration_seconds": 0.8,
                "screen_width": 1080,
                "screen_height": 2400,
            },
            {
                "action": "click",
                "selector": {
                    "package": package,
                    "class": "android.widget.Button",
                    "resource-id": f"{package}:id/effect_generate",
                },
            },
            {"action": "wait", "seconds": 20},
        ]
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": package,
                    "class": "android.widget.ProgressBar",
                    "resource-id": "android:id/progress",
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": package,
                    "class": "android.widget.EditText",
                    "resource-id": f"{package}:id/promptInputDialog_prompt",
                },
                "exists": True,
                "attribute": "text_equals_hint",
                "equals": True,
            }, {
                "selector": {
                    "package": package,
                    "class": "android.widget.ProgressBar",
                    "resource-id": "android:id/progress",
                },
                "exists": False,
            }],
        }
        value["recovery_action"] = [{
            "action": "click",
            "selector": {
                "package": package,
                "class": "android.widget.LinearLayout",
                "resource-id": f"{package}:id/effect_change_prompt_container",
            },
        }, {"action": "wait", "seconds": 2}, {
            "action": "click",
            "selector": {
                "package": package,
                "text": "Use your own description",
            },
        }, {"action": "wait", "seconds": 2}]
        value["observation_seconds"] = 5
        value["network_mode"] = "online"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "The frozen fixture is a hash-pinned local file. The negative arm is "
            "kept in the editor without a generation request; a positive arm begins "
            "a stateless image generation and is identified by the App progress dialog."
        )
        value["remote_state_review"] = {
            "classification": "stateless_generation_request",
            "provider_mutation_observed": False,
        }
        value["recalibration_action_source"] = "plan"
        reason = (
            "drive both controls through the same frozen image and mask gesture; "
            "freeze the App progress dialog for valid text and the unchanged editor "
            "without progress for invalid text"
        )
    elif case_id == "P052":
        value["verify_action"] = [{"action": "wait", "seconds": 2}]
        value["recalibration_action_source"] = "plan"
        value.pop("recovery_action", None)
        reason = "observe the filtered search results without closing the search surface or relying on the rotating Store hero"
    elif case_id == "P065":
        value["verify_action"] = [
            {"action": "press", "key": "enter"},
            {"action": "wait", "seconds": 2},
        ]
        value["recalibration_action_source"] = "plan"
        reason = "submit the typed place query instead of invoking the unrelated current-location shortcut"
    elif case_id == "P067":
        value["negative_control"] = {
            "com.peerspace.app:id/firstNameTxt": "",
            "com.peerspace.app:id/lastNameTxt": "",
            "com.peerspace.app:id/professionTxt": "",
            "com.peerspace.app:id/organizationTxt": "",
        }
        value["network_mode"] = "offline"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "Both arms verify airplane mode and observe only local Save-button enablement; no profile update is submitted."
        )
        value["verify_action"] = [{"action": "wait", "seconds": 1}]
        value["recalibration_action_source"] = "plan"
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [
                {
                    "selector": {
                        "package": "com.peerspace.app",
                        "resource-id": "com.peerspace.app:id/firstNameTxt",
                        "text": "Emma",
                    },
                    "exists": True,
                },
                {
                    "selector": {
                        "package": "com.peerspace.app",
                        "class": "android.view.ViewGroup",
                        "resource-id": "com.peerspace.app:id/saveBtn",
                        "enabled": True,
                    },
                    "exists": True,
                },
            ],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.peerspace.app",
                    "class": "android.view.ViewGroup",
                    "resource-id": "com.peerspace.app:id/saveBtn",
                    "enabled": False,
                },
                "exists": True,
            }],
        }
        reason = "observe local Save-button enablement without invoking the external profile-update path"
    elif case_id == "P059":
        value["verify_action"] = [
            {
                "action": "click",
                "selector": {
                    "package": "com.nihaojewelry.nihao",
                    "resource-id": "com.nihaojewelry.nihao:id/btn_save",
                },
            },
            {"action": "wait", "seconds": 2},
        ]
        value["recalibration_action_source"] = "plan"
        value["network_mode"] = "offline"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "Both arms run in airplane mode; Save can exercise local required-field validation but cannot persist an address."
        )
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.nihaojewelry.nihao",
                    "text": "Please enter First Name.",
                },
                "exists": False,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.nihaojewelry.nihao",
                    "text": "Please enter First Name.",
                },
                "exists": True,
            }],
        }
        value["initial_assertion"] = {
            "mode": "all",
            "targets": [
                {
                    "selector": {
                        "package": "com.nihaojewelry.nihao",
                        "text": "Add Address",
                    },
                    "exists": True,
                },
                {
                    "selector": {
                        "package": "com.nihaojewelry.nihao",
                        "text": "Please enter First Name.",
                    },
                    "exists": True,
                },
            ],
        }
        value["target_page_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.nihaojewelry.nihao",
                    "text": "Add Address",
                },
                "exists": True,
            }],
        }
        reason = "fill the native address form in visual order and invoke Save once so local required-field validation separates the arms"
    elif case_id == "P070":
        value["positive_control"] = {
            "van-field-1-input": "Protect oceans and reduce plastic waste."
        }
        value["negative_control"] = {"van-field-1-input": ""}
        value["verify_action"] = [{
            "action": "click",
            "selector": {
                "package": "com.qianfan.aihomework",
                "class": "android.widget.TextView",
                "text": "Essay",
                "instance": 1,
            },
        }]
        value["recalibration_action_source"] = "plan"
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.qianfan.aihomework",
                    "text": "Try again",
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [
                {
                    "selector": {
                        "package": "com.qianfan.aihomework",
                        "resource-id": "van-field-1-input",
                        "class": "android.widget.EditText",
                    },
                    "exists": True,
                },
                {
                    "selector": {
                        "package": "com.qianfan.aihomework",
                        "text": "Try again",
                    },
                    "exists": False,
                },
            ],
        }
        reason = "select the second Essay node, the bottom generation CTA, instead of the WebView root"
    elif case_id == "P072":
        value["positive_control"] = {
            "com.radio.pocketfm:id/search_box": "Insta Millionaire"
        }
        value["prepare_action"] = [{
            "action": "click",
            "selector": {
                "package": "com.radio.pocketfm",
                "class": "android.widget.ImageButton",
                "resource-id": "com.radio.pocketfm:id/search_box",
            },
        }, {"action": "wait", "seconds": 3}]
        value["verify_action"] = [{"action": "wait", "seconds": 4}]
        value["recalibration_action_source"] = "plan"
        value["force_cold_reset"] = True
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.radio.pocketfm",
                    "text": "Insta Millionaire (English)",
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [
                {
                    "selector": {
                        "package": "com.radio.pocketfm",
                        "text": "Top Searches",
                    },
                    "exists": True,
                },
                {
                    "selector": {
                        "package": "com.radio.pocketfm",
                        "text": "Insta Millionaire (English)",
                    },
                    "exists": False,
                },
            ],
        }
        value["network_mode"] = "online"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "The search is a read-only catalog query and does not mutate provider state."
        )
        value["remote_state_review"] = {
            "classification": "readonly_public_catalog_search",
            "provider_mutation_observed": False,
        }
        reason = "open the search editor and observe the stable public catalog result without the stale Enter and container clicks"
    elif case_id == "P073":
        package = "com.redfin.android"
        field = {
            "package": package,
            "class": "android.widget.EditText",
            "resource-id": f"{package}:id/nameField",
        }
        value["prepare_action"] = [{
            "action": "set_text",
            "selector": field,
            "value": "CD Snapshot Eval",
        }, {
            "action": "click",
            "selector": {
                "package": package,
                "class": "android.widget.Button",
                "resource-id": f"{package}:id/progress_button_base",
            },
        }, {"action": "wait", "seconds": 12}, {
            "action": "click",
            "selector": {
                "package": package,
                "class": "android.view.View",
                "content-desc": "Edit saved search",
                "instance": 0,
                "unique": True,
            },
        }, {"action": "wait", "seconds": 3}]
        value["verify_action"] = [{
            "action": "click",
            "selector": {
                "package": package,
                "class": "android.widget.Button",
                "resource-id": f"{package}:id/progress_button_base",
            },
        }, {"action": "wait", "seconds": 5}]
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {"package": package, "text": "Saved Searches"},
                "exists": True,
            }, {
                "selector": {"package": package, "text": "Edit Saved Search"},
                "exists": False,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {"package": package, "text": "Edit Saved Search"},
                "exists": True,
            }, {
                "selector": {"package": package, "text": "Saved Searches"},
                "exists": False,
            }],
        }
        value["recovery_baseline_values"] = {
            "com.redfin.android:id/nameField": "CD Snapshot Eval",
        }
        value["observation_seconds"] = 5
        value["force_cold_reset"] = True
        value["network_mode"] = "online"
        value["remote_baseline_status"] = "verified"
        value["remote_baseline_note"] = (
            "Every begin-attempt restores the synthetic saved-search name to "
            "CD Snapshot Eval through the App, waits for Saved Searches, and "
            "reopens that first synthetic entry before the candidate is filled."
        )
        value["remote_state_review"] = {
            "classification": "app_ui_remote_baseline_reset",
            "provider_mutation_observed": True,
        }
        value["recalibration_action_source"] = "plan"
        reason = (
            "reset the synthetic remote saved search through the App before each "
            "arm; valid names return to Saved Searches while blank names remain "
            "on Edit Saved Search"
        )
    elif case_id == "P076":
        value["negative_control"] = {
            "com.roomthermometer.interior:id/places_autocomplete_search_bar": ""
        }
        value["verify_action"] = [{"action": "wait", "seconds": 2}]
        value["recalibration_action_source"] = "plan"
        value["force_cold_reset"] = True
        value["network_mode"] = "online"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "Both arms exercise only the public Google Places autocomplete and do not mutate provider state."
        )
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.roomthermometer.interior",
                    "resource-id": "com.roomthermometer.interior:id/places_autocomplete_prediction_primary_text",
                    "text": "New York",
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [
                {
                    "selector": {
                        "package": "com.roomthermometer.interior",
                        "resource-id": "com.roomthermometer.interior:id/places_autocomplete_search_bar",
                        "class": "android.widget.EditText",
                    },
                    "exists": True,
                },
                {
                    "selector": {
                        "package": "com.roomthermometer.interior",
                        "resource-id": "com.roomthermometer.interior:id/places_autocomplete_prediction_primary_text",
                        "text": "New York",
                    },
                    "exists": False,
                },
            ],
        }
        reason = "observe the live Places prediction in online read-only mode; offline mode destroyed the input widget"
    elif case_id == "P080":
        value["negative_control"] = {"text-input": "abc"}
        value["input_strategies"] = {"text-input": "adb_key_events"}
        value["input_rejection_is_negative"] = True
        value["force_cold_reset"] = True
        value["verify_action"] = [{
            "action": "click",
            "selector": {
                "package": "com.shiftsmart.workerapp",
                "text": "Continue",
            },
        }]
        value["network_mode"] = "offline"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "Both arms run in airplane mode. The valid phone reaches the App request path and exposes the "
            "App-owned network error; alphabetic key events are rejected by the App-owned numeric phone widget."
        )
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.shiftsmart.workerapp",
                    "text": "Network issues detected",
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [
                {
                    "selector": {
                        "package": "com.shiftsmart.workerapp",
                        "resource-id": "text-input",
                        "class": "android.widget.EditText",
                    },
                    "exists": True,
                    "attribute": "text",
                    "equals": "+1",
                },
                {
                    "selector": {
                        "package": "com.shiftsmart.workerapp",
                        "text": "Network issues detected",
                    },
                    "exists": False,
                },
            ],
        }
        reason = (
            "cold-reset each arm so the transient App-owned network error cannot leak across attempts; "
            "the valid phone reaches the request path while real Android key events prove that the numeric App widget rejects alphabetic input"
        )
    elif case_id == "P084":
        value["verify_action"] = [{"action": "wait", "seconds": 0.2}]
        value["recalibration_action_source"] = "plan"
        reason = "observe local Sign Up enablement without requesting a remote registration code"
    elif case_id == "P086":
        value["verify_action"] = [{"action": "wait", "seconds": 2}]
        value["recalibration_action_source"] = "plan"
        reason = "observe in-App live search without the IME action that launches an external handler"
    elif case_id == "P088":
        value["verify_action"] = [{"action": "wait", "seconds": 0.2}]
        value["recalibration_action_source"] = "plan"
        value["network_mode"] = "offline"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "Both arms observe only local Next-button enablement in airplane mode; no OTP or provider request is submitted."
        )
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.sunrun.customerapp",
                    "class": "android.widget.Button",
                    "resource-id": "login-next",
                    "enabled": True,
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.sunrun.customerapp",
                    "class": "android.widget.Button",
                    "resource-id": "login-next",
                    "enabled": False,
                },
                "exists": True,
            }],
        }
        reason = "replace the OTP-producing Next action with the installed App's local identifier-validity enablement"
    elif case_id == "P089":
        value["verify_action"] = [{"action": "wait", "seconds": 0.01}]
        value["recalibration_action_source"] = "plan"
        value["endpoint_settle_seconds"] = 0.0
        value["observation_seconds"] = 2
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [
                {
                    "selector": {
                        "package": "com.teamreach.app",
                        "resource-id": "create-team-name-input",
                    },
                    "exists": True,
                },
                {
                    "selector": {
                        "package": "com.teamreach.app",
                        "resource-id": "create-team-code-input",
                    },
                    "exists": True,
                },
                {
                    "selector": {
                        "package": "com.teamreach.app",
                        "text": "Group Code must be at least 8 characters long",
                    },
                    "exists": False,
                },
                {
                    "selector": {
                        "package": "com.teamreach.app",
                        "text": "Group Code must contain letters and numbers",
                    },
                    "exists": False,
                },
            ],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.teamreach.app",
                    "text": "Group Code must be at least 8 characters long",
                },
                "exists": True,
            }],
        }
        reason = "observe local group-code validation without submitting or creating a remote group"
    elif case_id == "P093":
        value["verify_action"] = [{"action": "wait", "seconds": 1}]
        value["recalibration_action_source"] = "plan"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "The frozen action observes only local Save Preferences enablement and omits the remote save."
        )
        value["remote_state_review"] = {
            "classification": "local_input_transition_before_remote_mutation",
            "provider_mutation_observed": False,
        }
        reason = "observe local phone validity through Save Preferences enablement without mutating the profile"
    elif case_id == "P097":
        value["negative_control"] = {
            "com.virtual.video.i18n:id/etSubtitleInput": ""
        }
        accepted_text = value["positive_control"][
            "com.virtual.video.i18n:id/etSubtitleInput"
        ]
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.virtual.video.i18n",
                    "text": accepted_text,
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.virtual.video.i18n",
                    "text": accepted_text,
                },
                "exists": False,
            }],
        }
        value["observation_seconds"] = 5
        reason = "freeze the App-rendered subtitle content rather than the preview container that also accepts an empty script"
    elif case_id == "P101":
        value["verify_action"] = [{"action": "wait", "seconds": 1}]
        value["recalibration_action_source"] = "plan"
        reason = "bind the historical first-name prompt to the installed App's current first-name editor without submitting registration"
    elif case_id == "P103":
        value["verify_action"] = [
            {
                "action": "click",
                "selector": {
                    "package": "com.xogrp.planner",
                    "resource-id": "com.xogrp.planner:id/menu_item",
                },
            },
            {"action": "wait", "seconds": 5},
            {"action": "press", "key": "back"},
            {"action": "wait", "seconds": 3},
        ]
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.xogrp.planner",
                    "text": "Emma & Noah",
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [
                {
                    "selector": {
                        "package": "com.xogrp.planner",
                        "resource-id": "user_profile_header",
                    },
                    "exists": True,
                },
                {
                    "selector": {
                        "package": "com.xogrp.planner",
                        "text": "Emma & Noah",
                    },
                    "exists": False,
                },
            ],
        }
        value["recovery_action"] = [
            {
                "action": "click",
                "selector": {
                    "package": "com.xogrp.planner",
                    "resource-id": "user_profile_header",
                },
            },
            {"action": "wait", "seconds": 2},
        ]
        value["case_specific_reset"] = {
            "manifest_path": "/Volumes/Extreme Pro/emulator/CD_115page_37bug/google_eval/cases/P103/manifest.json",
            "reset_id": "theknot:P103:blank-personal-details:v1",
        }
        value["network_mode"] = "online"
        value["remote_baseline_status"] = "verified"
        value["remote_baseline_note"] = (
            "The manifest-pinned reset clears all four provider fields and re-reads the blank profile before every arm."
        )
        value["observation_seconds"] = 8
        value["recalibration_action_source"] = "plan"
        reason = (
            "verify a real save through the profile header's server-backed first-name readback, then return "
            "to the editor for within-attempt recovery; use the manifest-pinned remote reset before every arm"
        )
    elif case_id == "P104":
        value["verify_action"] = [
            {
                "action": "click",
                "selector": {
                    "package": "com.yelp.android.biz",
                    "resource-id": "com.yelp.android.biz:id/save",
                },
            },
            {"action": "wait", "seconds": 2},
        ]
        value["recalibration_action_source"] = "plan"
        value["network_mode"] = "offline"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = "Airplane mode prevents the account-name update from reaching Yelp during calibration."
        reason = "address the source prompt's duplicate ID exactly as the historical executor did: edit the first matching name field"
    elif case_id == "P105":
        value["verify_action"] = [{"action": "press", "key": "enter"}]
        value["recalibration_action_source"] = "plan"
        reason = "submit the search query without the stale follow-up double click that opens the shopping quiz"
    elif case_id == "P107":
        value["verify_action"] = [
            {
                "action": "click",
                "selector": {
                    "package": "heartratemonitor.heartrate.pulse.pulseapp",
                    "resource-id": "heartratemonitor.heartrate.pulse.pulseapp:id/btn_save",
                },
            },
            {"action": "wait", "seconds": 1},
            {"action": "press", "key": "back"},
            {"action": "wait", "seconds": 1},
        ]
        value["recalibration_action_source"] = "plan"
        value["network_mode"] = "offline"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "The BMI calculation is local; airplane mode prevents unrelated ad or feedback traffic."
        )
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "heartratemonitor.heartrate.pulse.pulseapp",
                    "resource-id": "heartratemonitor.heartrate.pulse.pulseapp:id/tvBmi",
                    "text": "25.9",
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [
                {
                    "selector": {
                        "package": "heartratemonitor.heartrate.pulse.pulseapp",
                        "text": "Result",
                    },
                    "exists": True,
                },
                {
                    "selector": {
                        "package": "heartratemonitor.heartrate.pulse.pulseapp",
                        "resource-id": "heartratemonitor.heartrate.pulse.pulseapp:id/tvBmi",
                        "text": "25.9",
                    },
                    "exists": False,
                },
            ],
        }
        value["recovery_action"] = [
            {
                "action": "click",
                "selector": {
                    "package": "heartratemonitor.heartrate.pulse.pulseapp",
                    "resource-id": "heartratemonitor.heartrate.pulse.pulseapp:id/tvDone",
                },
            },
            {"action": "wait", "seconds": 1},
            {
                "action": "click",
                "selector": {
                    "package": "heartratemonitor.heartrate.pulse.pulseapp",
                    "resource-id": "heartratemonitor.heartrate.pulse.pulseapp:id/btnNegative",
                },
            },
            {"action": "wait", "seconds": 1},
            {
                "action": "click",
                "selector": {
                    "package": "heartratemonitor.heartrate.pulse.pulseapp",
                    "resource-id": "heartratemonitor.heartrate.pulse.pulseapp:id/btn_add",
                },
            },
            {"action": "wait", "seconds": 1},
        ]
        value["recovery_baseline_values"] = {
            "heartratemonitor.heartrate.pulse.pulseapp:id/etWeight": "140.00",
            "heartratemonitor.heartrate.pulse.pulseapp:id/etHeightFt": "5",
            "heartratemonitor.heartrate.pulse.pulseapp:id/etHeightIn": "",
        }
        value["initial_assertion"] = {
            "mode": "all",
            "targets": [
                {
                    "selector": value["selector"],
                    "exists": True,
                    "attribute": "text",
                    "equals": "",
                }
                for value in [
                    {
                        "selector": {
                            "package": "heartratemonitor.heartrate.pulse.pulseapp",
                            "class": "android.widget.EditText",
                            "resource-id": resource_id,
                        },
                    }
                    for resource_id in (
                        "heartratemonitor.heartrate.pulse.pulseapp:id/etWeight",
                        "heartratemonitor.heartrate.pulse.pulseapp:id/etHeightFt",
                        "heartratemonitor.heartrate.pulse.pulseapp:id/etHeightIn",
                    )
                ]
            ],
        }
        reason = "dismiss the shared satisfaction overlay and freeze the App-computed BMI value that distinguishes the controls"
    elif case_id == "P109":
        value["negative_control"] = {
            "notes.notepad.checklist.calendar.todolist.notebook:id/et_note": "",
            "notes.notepad.checklist.calendar.todolist.notebook:id/tv_title": "",
        }
        value["verify_action"] = [
            {"action": "press", "key": "back"},
            {"action": "wait", "seconds": 1},
            {"action": "press", "key": "back"},
            {"action": "wait", "seconds": 1},
        ]
        value.pop("goal_assertion", None)
        value.pop("negative_assertion", None)
        value["recovery_action"] = [
            {
                "action": "click",
                "selector": {
                    "package": "notes.notepad.checklist.calendar.todolist.notebook",
                    "resource-id": "notes.notepad.checklist.calendar.todolist.notebook:id/iv_add_button",
                },
            },
            {"action": "wait", "seconds": 1},
            {
                "action": "click",
                "selector": {
                    "package": "notes.notepad.checklist.calendar.todolist.notebook",
                    "text": "TXT",
                },
            },
            {"action": "wait", "seconds": 1},
        ]
        value["recalibration_action_source"] = "plan"
        reason = "use the deterministic two-step Back flow; a fully empty note leaves the editor earlier than a non-empty note"
    elif case_id == "P069":
        value["positive_control"] = {"lr-hide": "16502530000"}
        value["negative_control"] = {"lr-hide": "123"}
        value["input_strategies"] = {"lr-hide": "adb_key_events"}
        value.pop("prepare_action", None)
        value["prepare_adb_actions"] = [
            {
                "action": "tap",
                "package": "com.punicapp.whoosh",
                "x": 310,
                "y": 540,
                "screen_width": 1080,
                "screen_height": 2400,
            },
            {"action": "wait", "seconds": 1},
            {
                "action": "tap",
                "package": "com.punicapp.whoosh",
                "x": 540,
                "y": 370,
                "screen_width": 1080,
                "screen_height": 2400,
            },
            {
                "action": "input_text",
                "package": "com.punicapp.whoosh",
                "value": "United States",
            },
            {"action": "wait", "seconds": 3},
            {
                "action": "tap",
                "package": "com.punicapp.whoosh",
                "x": 540,
                "y": 650,
                "screen_width": 1080,
                "screen_height": 2400,
            },
            {"action": "wait", "seconds": 1},
        ]
        value["prepare_adb_assertion"] = {
            "mode": "all",
            "targets": [
                {
                    "selector": {
                        "package": "com.punicapp.whoosh",
                        "text": "🇺🇸",
                    },
                    "exists": True,
                },
                {
                    "selector": {
                        "package": "com.punicapp.whoosh",
                        "resource-id": "lr-hide",
                        "class": "android.widget.EditText",
                    },
                    "exists": True,
                },
            ],
        }
        value["verify_action"] = [{
            "action": "tap",
            "package": "com.punicapp.whoosh",
            "x": 540,
            "y": 1510,
            "screen_width": 1080,
            "screen_height": 2400,
        }]
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.punicapp.whoosh",
                    "resource-id": "wrong_phone_number_text",
                    "text": "Connection issues",
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.punicapp.whoosh",
                    "resource-id": "wrong_phone_number_text",
                    "text": "Invalid input number",
                },
                "exists": True,
            }],
        }
        value["force_cold_reset"] = True
        value["network_mode"] = "offline"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "Both arms run in airplane mode after selecting the frozen United States dial plan; no OTP request can leave the App."
        )
        value["observation_seconds"] = 10
        value["recalibration_action_source"] = "plan"
        reason = (
            "freeze the United States country selector with a package/geometry-guarded ADB UI preparation, submit a complete +1 E.164 number, and distinguish the App's accepted offline request path from its explicit invalid-number error"
        )
    elif case_id == "P095":
        value["verify_action"] = [{"action": "press", "key": "enter"}]
        value["recalibration_action_source"] = "plan"
        reason = "Enter exposes the read-only location result; the second legacy click removed it"
    elif case_id == "P096":
        value["verify_action"] = [{"action": "press", "key": "enter"}]
        value["recalibration_action_source"] = "plan"
        value["observation_seconds"] = 8
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.ultimateguitar.tabs",
                    "text": "Bohemian Rhapsody",
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [
                {
                    "selector": {
                        "package": "com.ultimateguitar.tabs",
                        "text": "What do you want to play?",
                    },
                    "exists": True,
                },
                {
                    "selector": {
                        "package": "com.ultimateguitar.tabs",
                        "text": "Bohemian Rhapsody",
                    },
                    "exists": False,
                },
            ],
        }
        reason = "freeze the stable App search-result title and remove the unrelated root click and virtualized companion labels"
    elif case_id == "P115":
        value["verify_action"] = [{
            "action": "click",
            "selector": {
                "package": "us.mitene",
                "resource-id": "us.mitene:id/register",
            },
        }]
        value["recovery_action"] = [{
            "action": "click",
            "selector": {
                "package": "us.mitene",
                "resource-id": "android:id/button1",
            },
        }]
        value["recalibration_action_source"] = "plan"
        reason = "freeze the local DONE validation transition; the offline positive endpoint has no OK button"
    elif case_id == "P033":
        value["positive_control"] = {
            "compassTextInput::#::0": "Chenhui",
            "compassTextInput::#::1": "Cui",
            "compassTextInput::#::2": "chenhuicui97@gmail.com",
            "compassTextInput::#::3": "",
            "compassTextInput::#::4": "",
        }
        value["negative_control"] = {
            **value["positive_control"],
            "compassTextInput::#::0": "",
            "compassTextInput::#::2": "different.account@example.com",
        }
        value["input_rejection_is_negative"] = True
        first_name = {
            "package": "com.getyourguide.android",
            "class": "android.widget.EditText",
            "resource-id": "compassTextInput",
            "descendant-text": "First name",
        }
        value["prepare_action"] = [{
            "action": "set_text",
            "selector": first_name,
            "value": "",
        }]
        value["verify_action"] = [{"action": "wait", "seconds": 1}]
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.getyourguide.android",
                    "class": "android.view.View",
                    "resource-id": "1670069978",
                    "enabled": True,
                },
                "exists": True,
            }],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [{
                "selector": {
                    "package": "com.getyourguide.android",
                    "class": "android.view.View",
                    "resource-id": "1670069978",
                    "enabled": False,
                },
                "exists": True,
            }, {
                "selector": first_name,
                "exists": True,
                "attribute": "text",
                "equals": "",
            }],
        }
        value["recovery_baseline_values"] = {
            "compassTextInput::#::0": "",
            "compassTextInput::#::1": "Cui",
            "compassTextInput::#::2": "chenhuicui97@gmail.com",
            "compassTextInput::#::3": "",
            "compassTextInput::#::4": "",
        }
        value["recalibration_action_source"] = "plan"
        value["network_mode"] = "offline"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "The begin-attempt preparation clears First name locally and the "
            "control observes only the App-owned Save enabled state; Save is not clicked."
        )
        value["remote_state_review"] = {
            "classification": "local_validation_without_submission",
            "provider_mutation_observed": False,
        }
        reason = (
            "prepare the restored editor in a reversible invalid state, then freeze "
            "the App's own Save enabled/disabled behavior without a remote submission"
        )
    elif case_id == "P090":
        value["prepare_action"] = [{
            "action": "click",
            "selector": {
                "package": "com.thanx.nothingbundtcakes",
                "class": "android.widget.Button",
                "resource-id": "Button.Signup.UpdateUser",
            },
        }, {"action": "wait", "seconds": 1}]
        value["verify_action"] = [{"action": "wait", "seconds": 1}]
        error_selectors = [
            {"package": "com.thanx.nothingbundtcakes", "text": "Please enter a first name"},
            {"package": "com.thanx.nothingbundtcakes", "text": "Please enter a last name"},
        ]
        value["goal_assertion"] = {
            "mode": "all",
            "targets": [
                {"selector": selector, "exists": False}
                for selector in error_selectors
            ],
        }
        value["negative_assertion"] = {
            "mode": "all",
            "targets": [
                {"selector": selector, "exists": True}
                for selector in error_selectors
            ],
        }
        value["observation_seconds"] = 3
        value["network_mode"] = "online"
        value["remote_baseline_status"] = "not_applicable"
        value["remote_baseline_note"] = (
            "Begin-attempt submits only the restored invalid blank form to expose "
            "local validation. Candidate execution does not click Next, so no valid "
            "candidate is sent to the provider."
        )
        value["remote_state_review"] = {
            "classification": "local_validation_without_candidate_submission",
            "provider_mutation_observed": False,
        }
        value["recalibration_action_source"] = "plan"
        reason = (
            "freeze the App's own first/last-name validation messages: a valid "
            "candidate clears them while the invalid control leaves them visible, "
            "without submitting the candidate"
        )
    else:  # pragma: no cover - closed reviewed table
        raise KeyError(case_id)
    return value, reason


def main() -> None:
    parser = argparse.ArgumentParser(description="Write reviewed live control plan corrections")
    parser.add_argument("--cases-root", type=Path, required=True)
    parser.add_argument(
        "--allow-recovery-fallback",
        action="store_true",
        help=(
            "Use the best current base plan when a historical intermediate plan was not "
            "preserved. The written provenance records both expected and actual sources."
        ),
    )
    args = parser.parse_args()
    written = []
    recovered = []
    for case_id, filename in SOURCE_PLAN.items():
        case_root = args.cases_root.resolve() / case_id
        source, used_fallback = source_plan(
            case_root, filename, allow_recovery_fallback=args.allow_recovery_fallback,
        )
        plan, reason = _review(case_id, read_json(source))
        plan["reviewed_correction"] = {
            "expected_historical_source_plan": filename,
            "source_plan": source.name,
            "source_plan_sha256": sha256_file(source),
            "recovery_fallback": used_fallback,
            "reason": reason,
        }
        atomic_json(case_root / "control_plan_reviewed.json", plan)
        written.append(case_id)
        if used_fallback:
            recovered.append(case_id)
    print(json.dumps({"written": written, "recovery_fallback": recovered}, ensure_ascii=False))


if __name__ == "__main__":
    main()
