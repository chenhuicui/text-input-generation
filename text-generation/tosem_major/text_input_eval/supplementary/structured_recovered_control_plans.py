"""Recover semantically meaningful control candidates from historical evidence.

The ignored live-calibration directory can be lost when the repository is
recreated.  This module reconstructs *new* control-plan inputs from the closest
surviving plan plus candidate pairs recorded in the 2026-09-20 calibration
session.  It deliberately does not claim byte identity with the lost plan
files or the lost formal Oracle manifest.
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .structured_live_calibration import atomic_json, read_json
from .structured_snapshot_backup import sha256_file


HISTORICAL_SESSION_ID = "01a0bdc8-16ba-7543-8296-5440d334f75b"


# Evidence kinds are intentionally explicit. ``control_result`` means the pair
# appears in a successful controls_passed_local_only receipt. ``plan_output``
# means it appears in a historical plan/candidate dump. ``promotion_plus_base``
# is weaker: the historical session proves promotion, while the surviving base
# plan supplies the pair. ``recovery_candidate`` marks a new replacement chosen
# because the historical pair no longer separates current App behavior. All
# outputs still require a fresh live control run.
RECOVERED_CONTROLS: dict[str, dict[str, Any]] = {
    "P008": {
        "positive_control": {"audioeditor.musiceditor.soundeditor.songeditor:id/et_search": "recording"},
        "negative_control": {"audioeditor.musiceditor.soundeditor.songeditor:id/et_search": ""},
        "evidence": "plan_output",
    },
    "P009": {"evidence": "promotion_plus_base"},
    "P010": {
        "positive_control": {
            "co.fable.fable:id/list_title_edit_text": "Sci-Fi Masterpieces",
            "co.fable.fable:id/list_description_edit_text": "Best science fiction novels of all time",
        },
        "negative_control": {
            "co.fable.fable:id/list_title_edit_text": "Invalid title",
            "co.fable.fable:id/list_description_edit_text": "x" * 101,
        },
        "evidence": "control_result",
    },
    "P020": {
        "positive_control": {"SearchBox__input-field": "Mafia"},
        "negative_control": {"SearchBox__input-field": ""},
        "evidence": "plan_output",
        "plan_overrides": {
            "verify_action": [
                {"action": "press", "key": "enter"},
                {"action": "wait", "seconds": 3},
            ],
            "recalibration_action_source": "plan",
            "goal_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {"package": "com.colt", "text": "Books"},
                    "exists": True,
                }],
            },
            "negative_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {"package": "com.colt", "text": "Books"},
                    "exists": False,
                }],
            },
        },
    },
    "P024": {
        "positive_control": {"searchbar-field": "inflation"},
        "negative_control": {"searchbar-field": ""},
        "evidence": "plan_output",
        "plan_overrides": {
            "verify_action": [
                {"action": "press", "key": "enter"},
                {"action": "wait", "seconds": 3},
            ],
            "recalibration_action_source": "plan",
            "goal_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.economist.lamarr",
                        "resource-id": "article-card-for-article-",
                    },
                    "exists": True,
                }],
            },
            "negative_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.economist.lamarr",
                        "resource-id": "article-card-for-article-",
                    },
                    "exists": False,
                }],
            },
            "network_mode": "online",
            "remote_baseline_status": "not_applicable",
            "remote_baseline_note": (
                "The controls perform a read-only public article search and do not mutate provider state."
            ),
        },
    },
    "P028": {
        "positive_control": {"com.fly.hong.pdfreader:id/edit_name": "MyConvertedImage.pdf"},
        "negative_control": {"com.fly.hong.pdfreader:id/edit_name": ""},
        "evidence": "plan_output",
        "plan_overrides": {"recalibration_action_source": "plan"},
    },
    "P029": {
        "positive_control": {"com.forecasts.noaa.live.weather:id/et_input": "Berlin"},
        "negative_control": {"com.forecasts.noaa.live.weather:id/et_input": ""},
        "evidence": "plan_output",
        "plan_overrides": {
            "verify_action": [{"action": "wait", "seconds": 0.5}],
            "observation_seconds": 1,
            "recalibration_action_source": "plan",
            "goal_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.forecasts.noaa.live.weather",
                        "resource-id": "com.forecasts.noaa.live.weather:id/iv_clear",
                    },
                    "exists": True,
                }],
            },
            "negative_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.forecasts.noaa.live.weather",
                        "resource-id": "com.forecasts.noaa.live.weather:id/iv_clear",
                    },
                    "exists": False,
                }],
            },
        },
    },
    "P032": {
        "positive_control": {"searchBar": "Lakers"},
        "negative_control": {"searchBar": ""},
        "evidence": "plan_output",
    },
    "P035": {
        "positive_control": {"com.golftripgenius.android.leaguegenius:id/login_edit_ggid": "456789"},
        "negative_control": {"com.golftripgenius.android.leaguegenius:id/login_edit_ggid": "789012"},
        "evidence": "control_result",
        "plan_overrides": {"observation_seconds": 60},
    },
    "P036": {
        "positive_control": {
            "wish-form-url-input": "https://www.sony.com/electronics/headband-headphones/wh-1000xm5",
            "wish-form-price-input": "2899",
            "wish-form-title-input": "Sony WH-1000XM5 Wireless Headphones",
            "wish-form-description-input": (
                "Premium noise cancelling headphones with 30-hour battery life, crystal "
                "clear hands-free calling, and ultra-comfortable lightweight design perfect "
                "for travel and work."
            ),
        },
        "negative_control": {
            "wish-form-url-input": "",
            "wish-form-price-input": "",
            "wish-form-title-input": "",
            "wish-form-description-input": "",
        },
        "evidence": "plan_output",
    },
    "P040": {
        "positive_control": {"com.hoopladigital.android:id/search": "Beaumont"},
        "negative_control": {"com.hoopladigital.android:id/search": "zzzx no such library 9f6a"},
        "evidence": "control_result",
        "plan_overrides": {
            "verify_action": [
                {"action": "wait", "seconds": 2},
                {"action": "press", "key": "enter"},
                {"action": "wait", "seconds": 8},
            ],
            "recalibration_action_source": "plan",
            "goal_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.hoopladigital.android",
                        "text": "Beaumont, AB",
                    },
                    "exists": True,
                }],
            },
            "negative_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.hoopladigital.android",
                        "text": "Beaumont, AB",
                    },
                    "exists": False,
                }],
            },
        },
    },
    "P043": {
        "positive_control": {"textInputPhonenumber": "6505551234"},
        "negative_control": {"textInputPhonenumber": ""},
        "evidence": "control_result",
        "plan_overrides": {
            "case_specific_reset": {
                "manifest_path": (
                    "/Volumes/Extreme Pro/emulator/CD_115page_37bug/"
                    "google_eval/cases/P043/manifest.json"
                ),
                "reset_id": "isharing:P043:fresh-client-phone:v1",
            },
            "network_mode": "online",
            "remote_baseline_status": "verified",
            "remote_baseline_note": (
                "Before every arm, the manifest-pinned fresh API35 client reads and, if "
                "needed, repairs the stored baseline phone, then independently re-reads it."
            ),
        },
    },
    "P044": {
        "positive_control": {"com.jeyluta.timestampcamerafree:id/custom_editText": "Capture the Moment!"},
        "negative_control": {"com.jeyluta.timestampcamerafree:id/custom_editText": ""},
        "evidence": "control_result",
        "plan_overrides": {
            "goal_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.jeyluta.timestampcamerafree",
                        "text": "Capture the Moment!",
                    },
                    "exists": True,
                }],
            },
            "negative_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.jeyluta.timestampcamerafree",
                        "text": "Capture the Moment!",
                    },
                    "exists": False,
                }],
            },
        },
    },
    "P060": {
        "positive_control": {"com.novanews.localnews.en:id/et_content": "Community update"},
        "negative_control": {"com.novanews.localnews.en:id/et_content": ""},
        "evidence": "plan_output",
    },
    "P063": {
        "positive_control": {"com.olo.ihop:id/pickUpSearchBar": "90210"},
        "negative_control": {"com.olo.ihop:id/pickUpSearchBar": ""},
        "evidence": "plan_output",
        "plan_overrides": {
            "verify_action": [{"action": "wait", "seconds": 0.1}],
            "observation_seconds": 0.25,
            "endpoint_settle_seconds": 0.1,
            "time_sensitive_local_observation": True,
            "network_mode": "offline",
            "remote_baseline_status": "not_applicable",
            "remote_baseline_note": (
                "Both arms observe only the App-owned local clear affordance in "
                "airplane mode; no location search request is submitted."
            ),
            "recalibration_action_source": "plan",
            "goal_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.olo.ihop",
                        "resource-id": "com.olo.ihop:id/pickUpSearchBarClearIcon",
                    },
                    "exists": True,
                }],
            },
            "negative_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.olo.ihop",
                        "resource-id": "com.olo.ihop:id/pickUpSearchBarClearIcon",
                    },
                    "exists": False,
                }],
            },
        },
    },
    "P067": {
        "positive_control": {
            "com.peerspace.app:id/firstNameTxt": "Emma",
            "com.peerspace.app:id/lastNameTxt": "Johnson",
            "com.peerspace.app:id/professionTxt": "Event Marketing Manager",
            "com.peerspace.app:id/organizationTxt": "Creative Events Co.",
        },
        "negative_control": {
            "com.peerspace.app:id/firstNameTxt": "",
            "com.peerspace.app:id/lastNameTxt": "Johnson",
            "com.peerspace.app:id/professionTxt": "Data Analyst",
            "com.peerspace.app:id/organizationTxt": "Visionary Corp",
        },
        "evidence": "control_result",
    },
    "P068": {
        "positive_control": {
            "com.podbean.app.podcast:id/pop_et_epi_title": (
                "The Future of AI in Podcasting: Trends and Predictions for 2025"
            ),
        },
        "negative_control": {"com.podbean.app.podcast:id/pop_et_epi_title": ""},
        "evidence": "control_result",
    },
    "P074": {
        "evidence": "promotion_plus_base",
        "plan_overrides": {
            "verify_action": [{"action": "wait", "seconds": 0.5}],
            "recalibration_action_source": "plan",
            "goal_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {"package": "com.rent", "content-desc": "Clear"},
                    "exists": True,
                }],
            },
            "negative_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {"package": "com.rent", "content-desc": "Clear"},
                    "exists": False,
                }],
            },
        },
    },
    "P077": {
        "positive_control": {"com.scribd.app.reader0:id/searchView": "The Great Gatsby"},
        "negative_control": {"com.scribd.app.reader0:id/searchView": ""},
        "evidence": "plan_output",
        "plan_overrides": {
            "verify_action": [
                {"action": "press", "key": "enter"},
                {"action": "wait", "seconds": 2},
            ],
            "recalibration_action_source": "plan",
            "goal_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.scribd.app.reader0",
                        "resource-id": "com.scribd.app.reader0:id/webViewContainer",
                    },
                    "exists": True,
                }],
            },
            "negative_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.scribd.app.reader0",
                        "resource-id": "com.scribd.app.reader0:id/webViewContainer",
                    },
                    "exists": False,
                }],
            },
        },
    },
    "P083": {
        "positive_control": {"com.sillens.shapeupclub:id/search_edit_text": "Chicken breast"},
        "negative_control": {"com.sillens.shapeupclub:id/search_edit_text": "yoga"},
        "evidence": "plan_output",
        "plan_overrides": {
            "verify_action": [
                {"action": "wait", "seconds": 20},
            ],
            "recalibration_action_source": "plan",
            "goal_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.sillens.shapeupclub",
                        "resource-id": "com.sillens.shapeupclub:id/item_title",
                        "text": "Chicken breast",
                    },
                    "exists": True,
                }],
            },
            "negative_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.sillens.shapeupclub",
                        "resource-id": "com.sillens.shapeupclub:id/item_title",
                        "text": "Chicken breast",
                    },
                    "exists": False,
                }],
            },
        },
    },
    "P085": {
        "positive_control": {"com.smule.singandroid:id/search_edit_text": "Hello"},
        "negative_control": {"com.smule.singandroid:id/search_edit_text": ""},
        "evidence": "control_result",
        "plan_overrides": {
            "verify_action": [
                {"action": "press", "key": "enter"},
                {"action": "wait", "seconds": 2},
            ],
            "recalibration_action_source": "plan",
            "goal_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.smule.singandroid",
                        "text": "Search for songs, singers, invites…",
                    },
                    "exists": False,
                }],
            },
            "negative_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.smule.singandroid",
                        "text": "Search for songs, singers, invites…",
                    },
                    "exists": True,
                }],
            },
        },
    },
    "P087": {
        "positive_control": {
            "com.spotify.music:id/title_edit_text": "Summer Vibes Playlist",
            "com.spotify.music:id/description_edit_text": (
                "A collection of upbeat tracks to enjoy during the sunny days."
            ),
        },
        "negative_control": {
            "com.spotify.music:id/title_edit_text": "",
            "com.spotify.music:id/description_edit_text": "",
        },
        "plan_overrides": {
            "verify_action": [{"action": "wait", "seconds": 0.2}],
            "recalibration_action_source": "plan",
            "network_mode": "offline",
            "remote_baseline_status": "not_applicable",
            "remote_baseline_note": (
                "Both arms observe only local playlist-editor validation in airplane mode; Save is not clicked."
            ),
            "goal_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.spotify.music",
                        "text": "Give your playlist a catchy description",
                    },
                    "exists": False,
                }],
            },
            "negative_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.spotify.music",
                        "text": "Give your playlist a catchy description",
                    },
                    "exists": True,
                }],
            },
        },
        "evidence": "plan_output",
    },
    "P088": {
        "positive_control": {
            "text-field-input-Email address or mobile number": "john.doe@example.com",
        },
        "negative_control": {
            "text-field-input-Email address or mobile number": "",
        },
        "evidence": "plan_output",
    },
    "P094": {
        "positive_control": {"play_search_box": "Led Zeppelin"},
        "negative_control": {"play_search_box": ""},
        "evidence": "plan_output",
    },
    "P095": {
        "positive_control": {
            "com.trubeacon.scooters_mobile_android:id/locationSearchEditText": "Omaha, NE 68102",
        },
        "negative_control": {
            "com.trubeacon.scooters_mobile_android:id/locationSearchEditText": "",
        },
        "evidence": "plan_output",
    },
    "P096": {
        "positive_control": {"search_input": "Bohemian Rhapsody"},
        "negative_control": {"search_input": ""},
        "evidence": "control_result",
    },
    "P099": {
        "positive_control": {"signup-options-email-text-field": "test@example.com"},
        "negative_control": {"signup-options-email-text-field": ""},
        "evidence": "promotion_plus_base",
        "plan_overrides": {
            "verify_action": [{"action": "wait", "seconds": 0.5}],
            "recalibration_action_source": "plan",
            "goal_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.wendys.nutritiontool",
                        "resource-id": "signup-options-next-button",
                        "enabled": True,
                    },
                    "exists": True,
                }],
            },
            "negative_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.wendys.nutritiontool",
                        "resource-id": "signup-options-next-button",
                        "enabled": False,
                    },
                    "exists": True,
                }],
            },
        },
    },
    "P102": {
        "positive_control": {"com.wondershare.famisafe:id/et_name": "Emma Johnson"},
        "negative_control": {"com.wondershare.famisafe:id/et_name": ""},
        "evidence": "plan_output",
    },
    "P103": {
        "positive_control": {
            "com.xogrp.planner:id/et_user_last_name": "Johnson",
            "com.xogrp.planner:id/et_user_first_name": "Emma",
            "com.xogrp.planner:id/et_partner_last_name": "Williams",
            "com.xogrp.planner:id/et_partner_first_name": "Noah",
        },
        "negative_control": {
            "com.xogrp.planner:id/et_user_last_name": "",
            "com.xogrp.planner:id/et_user_first_name": "",
            "com.xogrp.planner:id/et_partner_last_name": "",
            "com.xogrp.planner:id/et_partner_first_name": "",
        },
        "evidence": "control_result",
    },
    "P105": {
        "positive_control": {
            "com.zennioptical.app:id/search_edit_text": "Women's blue light blocking glasses",
        },
        "negative_control": {"com.zennioptical.app:id/search_edit_text": ""},
        "plan_overrides": {
            "candidate_text_goal": True,
            "goal_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.zennioptical.app",
                        "text": "Women's blue light blocking glasses",
                    },
                    "exists": True,
                }],
            },
            "negative_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "com.zennioptical.app",
                        "text": "Women's blue light blocking glasses",
                    },
                    "exists": False,
                }],
            },
        },
        "evidence": "plan_output",
    },
    "P107": {
        "positive_control": {
            "heartratemonitor.heartrate.pulse.pulseapp:id/etWeight": "175.5",
            "heartratemonitor.heartrate.pulse.pulseapp:id/etHeightFt": "5",
            "heartratemonitor.heartrate.pulse.pulseapp:id/etHeightIn": "9",
        },
        "negative_control": {
            "heartratemonitor.heartrate.pulse.pulseapp:id/etWeight": "0",
            "heartratemonitor.heartrate.pulse.pulseapp:id/etHeightFt": "0",
            "heartratemonitor.heartrate.pulse.pulseapp:id/etHeightIn": "12",
        },
        "evidence": "control_result",
    },
    "P108": {
        "positive_control": {"net.wordbit.enes:id/view_auto_search": "hello"},
        "negative_control": {"net.wordbit.enes:id/view_auto_search": ""},
        "evidence": "promotion_plus_base",
        "plan_overrides": {
            "verify_action": [
                {
                    "action": "click",
                    "selector": {
                        "package": "net.wordbit.enes",
                        "resource-id": "net.wordbit.enes:id/button_search",
                    },
                },
                {"action": "wait", "seconds": 1},
            ],
            "recalibration_action_source": "plan",
            "goal_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "net.wordbit.enes",
                        "resource-id": "net.wordbit.enes:id/container_search",
                    },
                    "exists": False,
                }],
            },
            "negative_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "net.wordbit.enes",
                        "resource-id": "net.wordbit.enes:id/container_search",
                    },
                    "exists": True,
                }],
            },
        },
    },
    "P109": {
        "positive_control": {
            "notes.notepad.checklist.calendar.todolist.notebook:id/et_note": "Milk, Eggs, Bread, Apples",
            "notes.notepad.checklist.calendar.todolist.notebook:id/tv_title": "Grocery List",
        },
        "negative_control": {
            "notes.notepad.checklist.calendar.todolist.notebook:id/et_note": "",
            "notes.notepad.checklist.calendar.todolist.notebook:id/tv_title": "",
        },
        "evidence": "control_result",
    },
    "P111": {
        "evidence": "promotion_plus_base",
        "plan_overrides": {
            "verify_action": [
                {"action": "press", "key": "enter"},
                {"action": "wait", "seconds": 1},
            ],
            "recalibration_action_source": "plan",
            "goal_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "org.wikipedia",
                        "resource-id": "org.wikipedia:id/search_close_btn",
                    },
                    "exists": True,
                }],
            },
            "negative_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "org.wikipedia",
                        "resource-id": "org.wikipedia:id/search_close_btn",
                    },
                    "exists": False,
                }],
            },
        },
    },
    "P112": {
        "positive_control": {"sanity.podcast.freak:id/search_bar_text": "news"},
        "negative_control": {
            "sanity.podcast.freak:id/search_bar_text": "zzqxjv84726391unfindable"
        },
        "evidence": "recovery_candidate",
        "plan_overrides": {
            "verify_action": [
                {"action": "press", "key": "enter"},
                {"action": "wait", "seconds": 5},
            ],
            "recalibration_action_source": "plan",
            "observation_seconds": 60,
            "goal_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "sanity.podcast.freak",
                        "resource-id": "sanity.podcast.freak:id/cover",
                    },
                    "exists": True,
                }],
            },
            "negative_assertion": {
                "mode": "all",
                "targets": [{
                    "selector": {
                        "package": "sanity.podcast.freak",
                        "resource-id": "sanity.podcast.freak:id/cover",
                    },
                    "exists": False,
                }],
            },
        },
    },
}


BASE_PLAN_PREFERENCE = (
    "control_plan_manual_v2.json",
    "control_plan_manual.json",
    "control_plan_augmented.json",
    "control_plan_strong_negative.json",
    "control_plan_validator_fixed.json",
    "control_plan_expanded.json",
    "control_plan.json",
    "control_plan_offline.json",
)


def select_base_plan(case_root: Path) -> Path:
    path = next(
        (case_root / name for name in BASE_PLAN_PREFERENCE if (case_root / name).is_file()),
        None,
    )
    if path is None:
        raise FileNotFoundError(f"{case_root.name} has no surviving base control plan")
    return path


def recover_plan(case_root: Path, recovery: dict[str, Any]) -> dict[str, Any]:
    base_path = select_base_plan(case_root)
    plan = deepcopy(read_json(base_path))
    for key in ("positive_control", "negative_control"):
        if key in recovery:
            plan[key] = deepcopy(recovery[key])
    for key, value in recovery.get("plan_overrides", {}).items():
        plan[key] = deepcopy(value)
    if set(plan.get("positive_control", {})) != set(plan.get("negative_control", {})):
        raise ValueError(f"{case_root.name} recovered control keys do not match")
    plan["recovery_provenance"] = {
        "schema_version": "e2-structure-v1-recovered-control-plan-1",
        "historical_session_id": HISTORICAL_SESSION_ID,
        "evidence_kind": recovery["evidence"],
        "source_plan": base_path.name,
        "source_plan_sha256": sha256_file(base_path),
        "exact_original_file_recovered": False,
        "live_controls_required": True,
        "note": (
            "This is a newly frozen recovery artifact. It must not be represented as "
            "the lost historical plan or used without fresh positive/negative controls."
        ),
    }
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Write recovered historical control candidates")
    parser.add_argument("--cases-root", type=Path, required=True)
    parser.add_argument("--case", action="append")
    args = parser.parse_args()
    requested = set(args.case or RECOVERED_CONTROLS)
    unknown = requested - set(RECOVERED_CONTROLS)
    if unknown:
        raise ValueError(f"unknown recovery cases: {sorted(unknown)}")
    written: list[dict[str, str]] = []
    for case_id in sorted(requested):
        case_root = args.cases_root.resolve() / case_id
        plan = recover_plan(case_root, RECOVERED_CONTROLS[case_id])
        output = case_root / "control_plan_recovered.json"
        atomic_json(output, plan)
        written.append({
            "case_id": case_id,
            "output": str(output),
            "evidence_kind": RECOVERED_CONTROLS[case_id]["evidence"],
        })
    print(json.dumps({"written": written}, ensure_ascii=False))


if __name__ == "__main__":
    main()
