"""Qualified Android adapter repairs for E2-abcdef-v1.

This module intentionally patches the shared Android implementation only in
new adapter subprocesses launched through the repaired formal config.  Workers
that started with the previous adapter keep their imported implementation and
therefore remain reproducible while the Doubao run is still in progress.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from xml.etree import ElementTree as ET

from . import structured_android as base


_P094_UNSAFE_SELECTOR = {
    "package": "com.touchtunes.android",
    "class": "androidx.compose.ui.platform.k1",
    "enabled": True,
    "unique": True,
}
_P094_REPAIRED_SELECTOR = {
    "package": "com.touchtunes.android",
    "class": "android.view.View",
    "resource-id-prefix": "search_list_item_",
    "enabled": True,
    "instance": 0,
}
_P094_TERMINAL_SELECTOR_GUARDS = [
    {
        "action": "stop_if_present",
        "selector": {
            "package": "com.touchtunes.android",
            "class": "android.widget.TextView",
            "resource-id": "user_signup_login_title",
        },
    },
    {
        "action": "stop_if_present",
        "selector": {
            "package": "com.touchtunes.android",
            "class": "android.widget.TextView",
            "text": "First Name (optional)",
        },
    },
]
_P094_PRE_ENTER_GUARD_ACTIONS = copy.deepcopy(_P094_TERMINAL_SELECTOR_GUARDS)
_P094_MAX_TRANSIENT_VIEW_ROOTS = 2
_P094_LOGIN_GUARD_ACTIONS = [
    {
        "action": "stop_if_view_roots_exceed",
        "package": "com.touchtunes.android",
        "max_view_roots": _P094_MAX_TRANSIENT_VIEW_ROOTS,
        "timeout_seconds": 15.0,
        "poll_seconds": 0.1,
    },
    *copy.deepcopy(_P094_TERMINAL_SELECTOR_GUARDS),
]
_P033_DATE_BINDING = "compassTextInput::#::4"
_P093_PHONE_BINDING = "phoneInput"
_P001_ORACLE_VERSION = "P001-live-20260929-v1"
_P001_RESET_ID = "P001-local-snapshot-v1"
_P001_SOURCE_MANIFEST = (
    "/Volumes/Extreme Pro/emulator/CD_115page_37bug/cases/P001/manifest.json"
)
_P001_SOURCE_MANIFEST_SHA256 = (
    "a24e3b4c7ff2b4fcf703ec37d26b4d32eb7fb24d0828ba99c6add6483ee623e0"
)
_P001_CLONE_MANIFEST = (
    "/Users/cuichenhui/Documents/local-repositories/llm-empirical-study-workspace/"
    "llm-empirical-study/output/e2_abcdef_v1_20261002_formal_doubao_parallel_v1/"
    "infrastructure_5566_pool_clone/P001_manifest_clone.json"
)
_P001_CLONE_MANIFEST_SHA256 = (
    "b6751e49b7aee39ed0a57a491386c09887467c7fce14003b2ead1a734354ff77"
)
_P001_CLONE_AVD = "cd_115page_37bug_api5x"
_P006_ORACLE_VERSION = "P006-live-20260929-v1"
_P006_RESET_ID = "P006-local-snapshot-v1"
_P006_SOURCE_MANIFEST = (
    "/Volumes/Extreme Pro/emulator/CD_115page_37bug/cases/P006/manifest.json"
)
_P006_SOURCE_MANIFEST_SHA256 = (
    "42fb0e03bfaefc4da67b6d0181296660e22ca8b4e69cddfd9c94b0a63bafba78"
)
_P006_RESET_WRAPPER = (
    "/Users/cuichenhui/Documents/local-repositories/llm-empirical-study-workspace/"
    "llm-empirical-study/output/e2_abcdef_v1_20261002_formal_doubao_parallel_v1/"
    "reset_p006_snapshot_activity.py"
)
_P006_RESET_WRAPPER_SHA256 = (
    "770918f613e3760247d8761f35f47ae39faf87a0171cd009a06d2db7b1a2ecdf"
)
_P006_SOURCE_SERIAL = "emulator-5554"
_P006_SOURCE_AVD = "cd_115page_37bug_api33"
_P006_COMPONENT = "app.kids360.kid/.ui.onboarding.OnboardingActivity"
_P006_FOREGROUND_COMPONENT = (
    "app.kids360.kid/app.kids360.kid.ui.onboarding.OnboardingActivity"
)
_P006_BINDING = {
    "package": "app.kids360.kid",
    "resource-id": "app.kids360.kid:id/codeInput",
    "class": "android.widget.EditText",
}
_B017_FROZEN_INITIAL_XML_SHA256 = (
    "2975b4b61177e8e05214d7df4145eae580b45d41b6afcdf7b0e15b704b8da634"
)
_B017_BINDING = {
    "it.sasabz.android.sasabus:id/from_text": {
        "package": "it.sasabz.android.sasabus",
        "class": "android.widget.AutoCompleteTextView",
        "resource-id": "it.sasabz.android.sasabus:id/from_text",
    },
    "it.sasabz.android.sasabus:id/to_text": {
        "package": "it.sasabz.android.sasabus",
        "class": "android.widget.AutoCompleteTextView",
        "resource-id": "it.sasabz.android.sasabus:id/to_text",
    },
}


def _operation_case_and_oracle(payload: dict) -> tuple[dict | None, dict | None]:
    """Return the case and App-observation Oracle for one adapter operation."""
    operation = payload.get("operation")
    if operation == "begin_attempt":
        return payload.get("case"), (payload.get("attempt") or {}).get("oracle")
    if operation == "execute_turn":
        request = payload.get("request") or {}
        return request.get("case"), request.get("oracle")
    if operation == "recover_turn":
        execution = payload.get("execution") or {}
        return execution.get("case"), execution.get("oracle")
    return None, None


def _repair_b017_payload(payload: dict) -> tuple[dict, bool]:
    """Use the qualified native hierarchy when B017's frozen XML is absent.

    The historical contract pins a frozen hierarchy that is no longer present
    on disk. Reconstructing it from a new dump would falsely claim the old
    SHA-256. Instead, retain the exact binding, snapshot reset, action, and
    independent string-predicate verdict while routing hierarchy reads through
    the live uiautomator2 channel verified against the restored snapshot. If the
    pinned file is restored byte for
    byte, the original transport remains authoritative.
    """
    case, oracle = _operation_case_and_oracle(payload)
    if not isinstance(case, dict) or case.get("case_id") != "B017":
        return payload, False
    if not isinstance(oracle, dict):
        raise ValueError("B017 repaired adapter requires an App-observation Oracle")
    if (
        oracle.get("case_id") != "B017"
        or oracle.get("version") != "B017-app-observation-20260925-v2"
        or oracle.get("oracle_role") != "feedback_observation_only"
        or oracle.get("verdict_oracle") != "frozen_string_predicate"
        or oracle.get("binding") != _B017_BINDING
    ):
        raise ValueError("B017 repaired adapter requires the frozen reviewed contract")
    source = oracle.get("source_evidence") or {}
    frozen_path = Path(str(source.get("initial_xml", "")))
    declared_sha256 = source.get("initial_xml_sha256")
    if declared_sha256 != _B017_FROZEN_INITIAL_XML_SHA256:
        raise ValueError("B017 frozen hierarchy SHA-256 drift")
    if frozen_path.is_file():
        actual_sha256 = hashlib.sha256(frozen_path.read_bytes()).hexdigest()
        if actual_sha256 != declared_sha256:
            raise ValueError("B017 restored frozen hierarchy SHA-256 mismatch")
        return payload, False
    execution = oracle.get("execute_turn") or {}
    transport = execution.get("hierarchy_transport")
    if transport not in {"frozen_initial_xml", "uiautomator2"}:
        raise ValueError("B017 repaired adapter found an unknown hierarchy transport")
    repaired = copy.deepcopy(payload)
    _, repaired_oracle = _operation_case_and_oracle(repaired)
    assert repaired_oracle is not None
    repaired_execution = repaired_oracle["execute_turn"]
    repaired_execution["hierarchy_transport"] = "uiautomator2"
    repaired_execution["hierarchy_transport_repair"] = (
        "B017-missing-frozen-xml-live-uiautomator2-v1"
    )
    return repaired, True


def _repair_p094_payload(payload: dict) -> tuple[dict, bool]:
    """Replace only P094's calibrated full-screen center tap.

    The old selector uniquely matched a non-clickable full-screen Compose root.
    Its coordinate fallback happened to hit a result during calibration, but on
    the current search-error screen it opens a retry/login loop and leaks App
    windows until hierarchy capture is killed.  Select the first actual search
    result by its stable resource-id prefix instead.  Provider-driven login,
    onboarding, and retry states are captured as terminal App rejections when
    they first increase retained roots, without traversing hierarchy or using
    Back: a Back from the login sheet starts onboarding and compounds the root
    leak.  Exact terminal selectors provide a second fail-closed check after
    the bounded pressure window.  Result absence remains the existing
    ``if_missing=stop`` path.
    """
    if payload.get("operation") != "execute_turn":
        return payload, False
    request = payload.get("request")
    case = request.get("case") if isinstance(request, dict) else None
    if not isinstance(case, dict) or case.get("case_id") != "P094":
        return payload, False
    repaired = copy.deepcopy(payload)
    actions = repaired["request"]["oracle"]["execute_turn"]["verify_action"]
    enter_indexes = [
        index for index, action in enumerate(actions)
        if action == {"action": "press", "key": "enter"}
    ]
    if len(enter_indexes) != 1:
        raise ValueError("P094 repaired adapter requires exactly one known Enter action")

    def ensure_terminal_guards() -> None:
        enter_index = next(
            index for index, action in enumerate(actions)
            if action == {"action": "press", "key": "enter"}
        )
        pre_start = enter_index - len(_P094_PRE_ENTER_GUARD_ACTIONS)
        if (
            pre_start < 0
            or actions[pre_start:enter_index] != _P094_PRE_ENTER_GUARD_ACTIONS
        ):
            actions[enter_index:enter_index] = copy.deepcopy(
                _P094_PRE_ENTER_GUARD_ACTIONS
            )
            enter_index += len(_P094_PRE_ENTER_GUARD_ACTIONS)
        post_start = enter_index + 1
        if (
            actions[post_start:post_start + len(_P094_LOGIN_GUARD_ACTIONS)]
            != _P094_LOGIN_GUARD_ACTIONS
        ):
            actions[post_start:post_start] = copy.deepcopy(_P094_LOGIN_GUARD_ACTIONS)
    matches = [
        action for action in actions
        if action.get("action") == "click"
        and action.get("selector") == _P094_UNSAFE_SELECTOR
        and action.get("if_missing") == "stop"
    ]
    if len(matches) != 1:
        already_repaired = [
            action for action in actions
            if action.get("action") in {
                "click",
                "click_unless_view_roots_exceed",
                "click_from_observed_hierarchy_unless_view_roots_exceed",
            }
            and action.get("selector") == _P094_REPAIRED_SELECTOR
            and action.get("if_missing") == "stop"
        ]
        if len(already_repaired) == 1:
            already_repaired[0]["action"] = (
                "click_from_observed_hierarchy_unless_view_roots_exceed"
            )
            already_repaired[0]["package"] = "com.touchtunes.android"
            already_repaired[0]["max_view_roots"] = _P094_MAX_TRANSIENT_VIEW_ROOTS
            ensure_terminal_guards()
            return repaired, True
        raise ValueError("P094 repaired adapter requires exactly one known action selector")
    matches[0]["selector"] = dict(_P094_REPAIRED_SELECTOR)
    matches[0]["action"] = "click_from_observed_hierarchy_unless_view_roots_exceed"
    matches[0]["package"] = "com.touchtunes.android"
    matches[0]["max_view_roots"] = _P094_MAX_TRANSIENT_VIEW_ROOTS
    ensure_terminal_guards()
    return repaired, True


def _repair_p033_payload(payload: dict) -> tuple[dict, bool]:
    """Route P033's Date of birth field through its Material date dialog."""
    if payload.get("operation") != "execute_turn":
        return payload, False
    request = payload.get("request")
    case = request.get("case") if isinstance(request, dict) else None
    if not isinstance(case, dict) or case.get("case_id") != "P033":
        return payload, False
    repaired = copy.deepcopy(payload)
    oracle = repaired["request"]["oracle"]
    selector = oracle["binding"].get(_P033_DATE_BINDING)
    if not isinstance(selector, dict) or selector.get("descendant-text") != "Date of birth":
        raise ValueError("P033 repaired adapter requires the frozen Date of birth binding")
    strategies = oracle["execute_turn"].setdefault("input_strategies", {})
    existing = strategies.get(_P033_DATE_BINDING)
    if existing not in {None, "material_date_picker"}:
        raise ValueError("P033 Date of birth input strategy conflicts with the repair")
    strategies[_P033_DATE_BINDING] = "material_date_picker"
    return repaired, True


def _repair_p093_payload(payload: dict) -> tuple[dict, bool]:
    """Verify TickPick's calibrated phone-mask suffix after App formatting."""
    if payload.get("operation") != "execute_turn":
        return payload, False
    request = payload.get("request")
    case = request.get("case") if isinstance(request, dict) else None
    if not isinstance(case, dict) or case.get("case_id") != "P093":
        return payload, False
    repaired = copy.deepcopy(payload)
    oracle = repaired["request"]["oracle"]
    selector = oracle["binding"].get(_P093_PHONE_BINDING)
    if (
        not isinstance(selector, dict)
        or selector.get("package") != "com.tickpickllc.ceobrien.tickpick"
        or selector.get("class") != "android.widget.EditText"
        or selector.get("unique") is not True
    ):
        raise ValueError("P093 repaired adapter requires the frozen phone binding")
    strategies = oracle["execute_turn"].setdefault("input_strategies", {})
    existing = strategies.get(_P093_PHONE_BINDING)
    if existing not in {None, "masked_phone_suffix"}:
        raise ValueError("P093 phone input strategy conflicts with the repair")
    strategies[_P093_PHONE_BINDING] = "masked_phone_suffix"
    return repaired, True


def _repair_p001_payload(payload: dict) -> tuple[dict, bool]:
    """Route P001 through the hash-pinned manifest for the 5566 pool clone.

    The frozen P001 Oracle retained the pre-migration ``api5x`` AVD override,
    while its source manifest still declares the original ``api33`` AVD and
    source snapshot path. The pool clone contains the byte-rewritten snapshot
    under ``av4``. Use a hash-pinned derivative manifest that changes only the
    clone identity/path, and fail closed on every other command shape. This
    avoids launching the source AVD concurrently on the clone's serial.
    """
    if payload.get("operation") != "begin_attempt":
        return payload, False
    case = payload.get("case")
    if not isinstance(case, dict) or case.get("case_id") != "P001":
        return payload, False
    oracle = (payload.get("attempt") or {}).get("oracle")
    if (
        not isinstance(oracle, dict)
        or oracle.get("case_id") != "P001"
        or oracle.get("version") != _P001_ORACLE_VERSION
    ):
        raise ValueError("P001 repaired adapter requires the frozen reviewed contract")
    begin = oracle.get("begin_attempt")
    if not isinstance(begin, dict) or begin.get("reset_id") != _P001_RESET_ID:
        raise ValueError("P001 repaired adapter requires the frozen reset contract")
    command = begin.get("reset_command")
    if not isinstance(command, list) or any(not isinstance(value, str) for value in command):
        raise ValueError("P001 repaired adapter requires a structured reset command")

    def flag_value(flag: str) -> str:
        indexes = [index for index, value in enumerate(command) if value == flag]
        if len(indexes) != 1 or indexes[0] + 1 >= len(command):
            raise ValueError(f"P001 reset command requires exactly one {flag}")
        return command[indexes[0] + 1]

    if (
        command[:3] != [
            "/opt/anaconda3/bin/python3",
            "-m",
            "text_input_eval.supplementary.structured_snapshot_reset",
        ]
        or flag_value("--manifest") not in {
            _P001_SOURCE_MANIFEST,
            _P001_CLONE_MANIFEST,
        }
        or flag_value("--reset-id") != _P001_RESET_ID
    ):
        raise ValueError("P001 reset command drifted from the reviewed contract")
    avd = flag_value("--avd")
    if avd != _P001_CLONE_AVD:
        raise ValueError("P001 reset AVD drifted from the reviewed contract")
    source_manifest = Path(_P001_SOURCE_MANIFEST)
    clone_manifest = Path(_P001_CLONE_MANIFEST)
    if (
        not source_manifest.is_file()
        or hashlib.sha256(source_manifest.read_bytes()).hexdigest()
        != _P001_SOURCE_MANIFEST_SHA256
    ):
        raise ValueError("P001 source manifest SHA-256 drift")
    if (
        not clone_manifest.is_file()
        or hashlib.sha256(clone_manifest.read_bytes()).hexdigest()
        != _P001_CLONE_MANIFEST_SHA256
    ):
        raise ValueError("P001 clone manifest SHA-256 drift")
    clone = json.loads(clone_manifest.read_text(encoding="utf-8"))
    if (
        clone.get("case_id") != "P001"
        or clone.get("avd_name") != _P001_CLONE_AVD
        or clone.get("snapshot") != "P001_baseline_v1"
        or clone.get("snapshot_path")
        != "/Volumes/Extreme Pro/emulator/CD_115page_37bug/av4/snapshots/P001_baseline_v1"
    ):
        raise ValueError("P001 clone manifest contract drift")
    if flag_value("--manifest") == _P001_CLONE_MANIFEST:
        return payload, False
    repaired = copy.deepcopy(payload)
    repaired_command = repaired["attempt"]["oracle"]["begin_attempt"]["reset_command"]
    repaired_command[repaired_command.index("--manifest") + 1] = _P001_CLONE_MANIFEST
    return repaired, True


def _repair_p006_payload(payload: dict) -> tuple[dict, bool]:
    """Bring P006's frozen target Activity forward after snapshot restore.

    The snapshot itself is valid, but the restored task can intermittently
    settle on Launcher before the formal adapter asserts the target field.
    Reuse the zero-provider-qualified reset wrapper from the completed Doubao
    run.  The wrapper changes no App state beyond explicitly foregrounding the
    already frozen target Activity after the same snapshot restore.  Keep the
    repair on the original api33 emulator where its control cycles passed.
    """
    if payload.get("operation") != "begin_attempt":
        return payload, False
    case = payload.get("case")
    if not isinstance(case, dict) or case.get("case_id") != "P006":
        return payload, False
    oracle = (payload.get("attempt") or {}).get("oracle")
    if (
        not isinstance(oracle, dict)
        or oracle.get("case_id") != "P006"
        or oracle.get("version") != _P006_ORACLE_VERSION
    ):
        raise ValueError("P006 repaired adapter requires the frozen reviewed contract")
    begin = oracle.get("begin_attempt")
    if not isinstance(begin, dict) or begin.get("reset_id") != _P006_RESET_ID:
        raise ValueError("P006 repaired adapter requires the frozen reset contract")
    command = begin.get("reset_command")
    if not isinstance(command, list) or any(not isinstance(value, str) for value in command):
        raise ValueError("P006 repaired adapter requires a structured reset command")

    def flag_value(flag: str) -> str:
        indexes = [index for index, value in enumerate(command) if value == flag]
        if len(indexes) != 1 or indexes[0] + 1 >= len(command):
            raise ValueError(f"P006 reset command requires exactly one {flag}")
        return command[indexes[0] + 1]

    allowed_prefixes = {
        (
            "/opt/anaconda3/bin/python3",
            "-m",
            "text_input_eval.supplementary.structured_snapshot_reset",
        ),
        ("/opt/anaconda3/bin/python3", _P006_RESET_WRAPPER),
    }
    prefix = tuple(command[:3]) if len(command) >= 3 and command[1] == "-m" else tuple(command[:2])
    if (
        prefix not in allowed_prefixes
        or flag_value("--manifest") != _P006_SOURCE_MANIFEST
        or flag_value("--device") != _P006_SOURCE_SERIAL
        or flag_value("--avd") != _P006_SOURCE_AVD
        or flag_value("--reset-id") != _P006_RESET_ID
        or "--offline" not in command
    ):
        raise ValueError("P006 reset command drifted from the reviewed contract")
    source_manifest = Path(_P006_SOURCE_MANIFEST)
    reset_wrapper = Path(_P006_RESET_WRAPPER)
    if (
        not source_manifest.is_file()
        or hashlib.sha256(source_manifest.read_bytes()).hexdigest()
        != _P006_SOURCE_MANIFEST_SHA256
    ):
        raise ValueError("P006 source manifest SHA-256 drift")
    if (
        not reset_wrapper.is_file()
        or hashlib.sha256(reset_wrapper.read_bytes()).hexdigest()
        != _P006_RESET_WRAPPER_SHA256
    ):
        raise ValueError("P006 reset wrapper SHA-256 drift")
    if prefix == ("/opt/anaconda3/bin/python3", _P006_RESET_WRAPPER):
        return payload, False
    repaired = copy.deepcopy(payload)
    repaired_command = repaired["attempt"]["oracle"]["begin_attempt"]["reset_command"]
    repaired_command[1:3] = [_P006_RESET_WRAPPER]
    return repaired, True


def _ensure_p006_execute_target(payload: dict) -> bool:
    """Re-foreground P006 when the frozen Activity drops between operations.

    The formal environment runs begin-attempt and execute-turn in independent
    adapter subprocesses. This App can return to Launcher during that boundary
    even though begin-attempt verified the exact frozen field. Reuse the same
    explicit-Activity action already qualified for reset, only when the exact
    reviewed binding is absent, and verify both component and binding before
    allowing candidate entry.
    """
    if payload.get("operation") != "execute_turn":
        return False
    request = payload.get("request")
    case = request.get("case") if isinstance(request, dict) else None
    oracle = request.get("oracle") if isinstance(request, dict) else None
    if not isinstance(case, dict) or case.get("case_id") != "P006":
        return False
    if (
        not isinstance(oracle, dict)
        or oracle.get("case_id") != "P006"
        or oracle.get("version") != _P006_ORACLE_VERSION
        or oracle.get("device_serial") != _P006_SOURCE_SERIAL
        or oracle.get("binding")
        != {"app.kids360.kid:id/codeInput": _P006_BINDING}
    ):
        raise ValueError("P006 execute repair requires the frozen reviewed contract")

    import uiautomator2 as u2

    device = u2.connect(_P006_SOURCE_SERIAL)
    driver = base.AndroidDriver(_P006_SOURCE_SERIAL, device=device)

    def target_ready() -> bool:
        current = driver.current_app()
        component = (
            f"{current.get('package')}/{current.get('activity')}"
            if isinstance(current, dict) else ""
        )
        nodes = base.binding_nodes(ET.fromstring(driver.hierarchy()), _P006_BINDING)
        return component == _P006_FOREGROUND_COMPONENT and len(nodes) == 1

    # Reapply the reviewed Activity even when the first hierarchy read is
    # healthy. P006 can drop to Launcher immediately after that read; an
    # idempotent ``am start`` at the execute boundary refreshes the exact task
    # just before candidate entry and removes that check/use race.
    completed = subprocess.run(
        [
            "adb", "-s", _P006_SOURCE_SERIAL, "shell", "am", "start", "-W",
            "-n", _P006_COMPONENT,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    combined = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0 or "Status: ok" not in combined:
        raise RuntimeError("P006 execute repair could not foreground target Activity")
    for _ in range(20):
        time.sleep(0.5)
        if target_ready():
            return True
    raise RuntimeError("P006 execute repair could not verify target binding")


def _repaired_adb_uiautomator_hierarchy(serial: str) -> str:
    """Capture the native hierarchy with bounded recovery from exit 137."""
    remote = "/sdcard/codex_e2_window_dump.xml"
    last_error = "native UI hierarchy fallback failed"
    for attempt in range(5):
        subprocess.run(
            ["adb", "-s", serial, "shell", "rm", "-f", remote],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        dumped = subprocess.run(
            ["adb", "-s", serial, "shell", "uiautomator", "dump", "--compressed", remote],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=25,
            check=False,
        )
        if dumped.returncode == 0:
            captured = subprocess.run(
                ["adb", "-s", serial, "shell", "cat", remote],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            if captured.returncode == 0 and captured.stdout.strip():
                try:
                    ET.fromstring(captured.stdout)
                    return captured.stdout
                except ET.ParseError:
                    last_error = "native UI hierarchy fallback returned invalid XML"
            else:
                last_error = "native UI hierarchy fallback returned no XML"
        else:
            detail = (dumped.stderr or dumped.stdout).strip().replace("\n", " ")
            last_error = (
                "native UI hierarchy fallback command failed"
                f" (exit={dumped.returncode}, detail={detail[:300] or 'none'})"
            )
        if attempt < 4:
            # An exit-137 uiautomator process may leave the accessibility
            # service briefly unavailable.  Give it progressively more time.
            time.sleep(2.0 * (attempt + 1) if dumped.returncode == 137 else 1.0)
    raise RuntimeError(last_error)


def _is_system_crash_dialog(root: ET.Element) -> bool:
    """Recognize Android's process-crash UI without changing the Bug verdict."""
    nodes = list(root.iter("node"))
    visible_text = "\n".join(
        value
        for node in nodes
        for value in (node.get("text", ""), node.get("content-desc", ""))
        if value
    ).lower()
    system_owned = any(
        node.get("package") in {"android", "com.android.systemui"}
        for node in nodes
    )
    return system_owned and (
        "keeps stopping" in visible_text
        or "has just crashed" in visible_text
        or "close app" in visible_text
    )


def _repaired_hierarchy(self: base.AndroidDriver) -> str:
    """Preserve a valid system crash dialog instead of invoking a killed dumper.

    B008's positive control deliberately crashes the AUT.  uiautomator2 returns
    the Android crash dialog correctly, but the generic driver rejects it
    because the restarted AUT has no semantic nodes and then invokes the
    native dumper, which exits 137 on this image.  Accept only an explicitly
    recognized system-owned crash dialog; every other sparse hierarchy keeps
    the original fail-closed fallback behavior.
    """
    if self.frozen_hierarchy is not None:
        return self.frozen_hierarchy
    if self.serial != "fake" and self.prefer_adb_hierarchy:
        return _repaired_adb_uiautomator_hierarchy(self.serial)
    value = ""
    last_dump_error: Exception | None = None
    for attempt in range(3):
        try:
            value = self.device.dump_hierarchy(compressed=False)
        except Exception as exc:
            last_dump_error = exc
            if attempt < 2:
                reset = getattr(self.device, "reset_uiautomator", None)
                if callable(reset):
                    try:
                        reset()
                    except Exception:
                        pass
                time.sleep(1)
                continue
            break
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError("empty UI hierarchy")
        root = ET.fromstring(value)
        # The system crash dialog is independently identifiable from its
        # system-owned nodes.  Accept it before querying foreground-App state,
        # which can itself be temporarily unavailable immediately after the
        # AUT process dies.
        if _is_system_crash_dialog(root):
            self.hierarchy_source = "live_uiautomator2_system_crash_dialog"
            return value
        current = self.current_app()
        package = current.get("package") if isinstance(current, dict) else None
        app_nodes = (
            [node for node in root.iter("node") if node.get("package") == package]
            if isinstance(package, str) and package else []
        )
        semantic_app_nodes = [
            node for node in app_nodes
            if any(node.get(key) for key in ("resource-id", "text", "content-desc"))
        ]
        if not isinstance(package, str) or not package or semantic_app_nodes:
            return value
        if attempt < 2:
            time.sleep(0.5)
    if not value and self.serial == "fake" and last_dump_error is not None:
        raise last_dump_error
    if self.serial != "fake":
        value = _repaired_adb_uiautomator_hierarchy(self.serial)
    return value


def _repaired_ime_visible(self: base.AndroidDriver, package: str) -> bool:
    """Use authoritative input-method state before hierarchy heuristics.

    Stale accessibility nodes previously caused a Back press when the keyboard
    was already hidden, navigating away from the form and invalidating the
    following dynamic selector lookup.
    """
    state = subprocess.run(
        ["adb", "-s", self.serial, "shell", "dumpsys", "input_method"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
        check=False,
    )
    if state.returncode == 0 and state.stdout.strip():
        output = state.stdout
        input_shown = re.findall(
            r"\bmInputShown=(true|false)\b", output, flags=re.IGNORECASE,
        )
        if input_shown:
            return any(value.lower() == "true" for value in input_shown)
        input_view_shown = re.findall(
            r"\bmIsInputViewShown=(true|false)\b", output, flags=re.IGNORECASE,
        )
        if input_view_shown:
            return any(value.lower() == "true" for value in input_view_shown)
        requested = re.findall(
            r"\bmShowRequested=(true|false)\b", output, flags=re.IGNORECASE,
        )
        if requested:
            return any(value.lower() == "true" for value in requested)
        # Android releases vary in the exact field name but keep this token in
        # the visible input-view line.  Absence is not treated as visibility.
        if re.search(r"\binputShown\s*=\s*true\b", output, re.IGNORECASE):
            return True
        return False

    # Fail over only when the system service itself is unavailable.  This is
    # the old behavior and remains bounded by the repaired hierarchy capture.
    root = ET.fromstring(self.hierarchy())
    return any(
        node.get("package") != package
        and (
            "inputmethod" in node.get("package", "").lower()
            or "inputmethod" in node.get("class", "").lower()
            or node.get("package") == "com.github.uiautomator"
        )
        for node in root.iter("node")
    )


def install_repairs() -> None:
    """Install the repairs in this adapter subprocess only."""
    base._adb_uiautomator_hierarchy = _repaired_adb_uiautomator_hierarchy
    base.AndroidDriver.hierarchy = _repaired_hierarchy
    base.AndroidDriver._ime_visible = _repaired_ime_visible


def main() -> None:
    install_repairs()
    from .abcdef_android import dispatch as adapter_dispatch
    import json
    import sys

    try:
        payload, b017_repaired = _repair_b017_payload(json.load(sys.stdin))
        payload, p001_repaired = _repair_p001_payload(payload)
        payload, p006_repaired = _repair_p006_payload(payload)
        p006_execute_repaired = _ensure_p006_execute_target(payload)
        payload, p033_repaired = _repair_p033_payload(payload)
        payload, p093_repaired = _repair_p093_payload(payload)
        payload, p094_repaired = _repair_p094_payload(payload)
        result = adapter_dispatch(payload)
        if b017_repaired:
            result["hierarchy_transport_repair"] = (
                "B017-missing-frozen-xml-live-uiautomator2-v1"
            )
        if p001_repaired:
            result["snapshot_manifest_repair"] = "P001-5566-clone-manifest-v1"
        if p006_repaired:
            result["snapshot_activity_repair"] = "P006-explicit-target-activity-v1"
        if p006_execute_repaired:
            result["execute_activity_repair"] = "P006-execute-target-activity-v1"
        if p033_repaired:
            result["input_strategy_repair"] = "P033-material-date-picker-v1"
        if p093_repaired:
            result["input_strategy_repair"] = "P093-masked-phone-suffix-v1"
        if p094_repaired:
            result["action_selector_repair"] = "P094-prefix-stop-no-hierarchy-reread-v8"
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        sys.stderr.write(json.dumps(
            {"error_type": type(exc).__name__, "message": str(exc)},
            ensure_ascii=False,
        ))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
