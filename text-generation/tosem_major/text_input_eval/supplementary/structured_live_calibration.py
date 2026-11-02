"""Live, resumable inspection for E2 per-case Android Oracle calibration.

This first calibration phase restores frozen snapshots and inspects current UI
state only.  It does not fill fields or execute submit actions.  Coordinate
click, double-click, and long-click actions are converted to a stable, unique
selector only when the current UI tree proves such a selector exists.  The
gesture kind is retained and legacy coordinates are discarded.  Results remain candidates until separate
positive/negative controls and reset/recovery checks have passed.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import subprocess
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import yaml

from .structured_snapshot_backup import resolve_snapshot, sha256_file


AVD_DIRECTORY_NAMES = {
    "avd": "cd_115page_37bug_api33",
    "google_login_api33_avd": "cd_blocked_google_api33",
    "google_api35_migration_avd": "cd_google_migrated_api35",
    "p018_api35_avd": "cd_p018_api35",
    "root_api30_avd": "cd_blocked_root_api30",
}
STABLE_SELECTOR_KEYS = ("resource-id", "text", "content-desc", "hint")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def case_contract_sha256(case: dict[str, Any]) -> str:
    """Hash immutable calibration inputs, excluding evolving readiness overlays."""
    mirror = case.get("mirror", {})
    source_files = case.get("source_files", {})
    contract = {
        "case_id": case.get("case_id"),
        "task": case.get("task"),
        "required_resource_ids": case.get("required_resource_ids"),
        "source_hashes": {
            key: value for key, value in source_files.items() if key.endswith("_sha256")
        },
        "mirror": {
            key: mirror.get(key) for key in (
                "package", "version_code", "source_sha256", "snapshot",
                "snapshot_path", "manifest_sha256",
            )
        },
    }
    return hashlib.sha256(
        json.dumps(
            contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def run(argv: list[str], timeout: float = 180, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout, check=False,
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(argv[:4])}{suffix}"
        )
    return completed


def infer_avd(source_root: Path, case: dict[str, Any]) -> tuple[str, Path]:
    snapshot = resolve_snapshot(source_root, case["mirror"])
    relative = snapshot.relative_to(source_root)
    avd_dir = source_root / relative.parts[0]
    configured = case["mirror"].get("device", {}).get("avd")
    avd_name = configured or AVD_DIRECTORY_NAMES.get(avd_dir.name)
    if not isinstance(avd_name, str) or not avd_name:
        raise RuntimeError(f"cannot infer AVD for {case['case_id']}: {avd_dir}")
    return avd_name, snapshot


def ensure_backup(backup_manifest: Path, registry_path: Path) -> str:
    backup = read_json(backup_manifest)
    if backup.get("status") != "complete" or backup.get("case_count") != 152:
        raise RuntimeError("the pre-calibration backup is not complete for 152 cases")
    registry = read_json(registry_path)
    current_cases = {case["case_id"]: case for case in registry.get("cases", [])}
    backup_cases = {case["case_id"]: case for case in backup.get("cases", [])}
    if set(current_cases) != set(backup_cases) or len(current_cases) != 152:
        raise RuntimeError("backup and current registry case universes differ")
    drift = []
    for case_id, current in current_cases.items():
        saved = backup_cases[case_id]
        if current.get("mirror", {}).get("snapshot") != saved.get("snapshot"):
            drift.append(f"{case_id}:snapshot")
        if current.get("mirror", {}).get("manifest_sha256") != saved.get("source_manifest_sha256"):
            drift.append(f"{case_id}:mirror_manifest")
    if drift:
        raise RuntimeError(f"backup and current snapshot contracts differ: {drift[:8]}")
    verification = backup.get("verification", {})
    if not all(verification.get(key) is True for key in (
        "source_avds_stopped", "avd_file_inventory_equal",
        "snapshot_signatures_equal", "case_manifest_hashes_equal",
    )):
        raise RuntimeError("backup verification flags are incomplete")
    return sha256_file(backup_manifest)


def current_avd(serial: str) -> str:
    output = run(["adb", "-s", serial, "emu", "avd", "name"], timeout=20).stdout
    return next((line.strip() for line in output.splitlines() if line.strip() != "OK"), "")


def stop_conflicting_avd(target_serial: str, avd: str) -> list[str]:
    """Stop a completed experiment emulator that holds the requested AVD.

    Android locks an AVD directory even when separate cases assign it to
    different emulator ports.  The structured runner is sequential, so an
    emulator on another declared port is stale once its case has completed.
    Only an exact AVD-name match is stopped; unrelated emulators are preserved.
    """
    devices = run(["adb", "devices"], timeout=20).stdout.splitlines()
    serials = [
        fields[0]
        for line in devices[1:]
        if len(fields := line.split()) >= 2
        and fields[0].startswith("emulator-")
        and fields[1] == "device"
        and fields[0] != target_serial
    ]
    stopped: list[str] = []
    for serial in serials:
        probe = run(
            ["adb", "-s", serial, "emu", "avd", "name"],
            timeout=20, check=False,
        )
        if probe.returncode != 0:
            continue
        running_avd = next(
            (line.strip() for line in probe.stdout.splitlines() if line.strip() != "OK"),
            "",
        )
        if running_avd != avd:
            continue
        run(["adb", "-s", serial, "emu", "kill"], timeout=20, check=False)
        stopped.append(serial)
    for serial in stopped:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = run(["adb", "-s", serial, "get-state"], timeout=5, check=False)
            if state.returncode != 0:
                break
            time.sleep(0.5)
        else:
            raise TimeoutError(f"conflicting emulator did not stop: {serial}")
    return stopped


def wait_boot(serial: str) -> None:
    run(["adb", "-s", serial, "wait-for-device"], timeout=60)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        completed = run(
            ["adb", "-s", serial, "shell", "getprop", "sys.boot_completed"],
            timeout=10, check=False,
        )
        if completed.stdout.strip() == "1":
            time.sleep(1)
            return
        time.sleep(1)
    raise TimeoutError("emulator did not return to a boot-complete state")


def snapshot_launch_compatibility(snapshot_path: Path) -> list[str]:
    metadata = snapshot_path / "snapshot.pb"
    extracted = run(["strings", str(metadata)], timeout=20).stdout.splitlines()
    allowed = {"-gpu", "-feature"}
    no_argument = {"-writable-system"}
    result: list[str] = []
    index = 0
    while index < len(extracted):
        if extracted[index] in no_argument:
            result.append(extracted[index])
            index += 1
            continue
        if index + 1 >= len(extracted):
            break
        if extracted[index] in allowed:
            result.extend([extracted[index], extracted[index + 1]])
            index += 2
        else:
            index += 1
    return result or ["-gpu", "swiftshader_indirect"]


def cold_start_snapshot(
    serial: str, avd: str, snapshot_path: Path, log_path: Path
) -> None:
    snapshot = snapshot_path.name
    stop_conflicting_avd(serial, avd)
    run(["adb", "-s", serial, "emu", "kill"], timeout=20, check=False)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = run(["adb", "-s", serial, "get-state"], timeout=5, check=False)
        if state.returncode != 0:
            break
        time.sleep(0.5)
    port = serial.removeprefix("emulator-")
    if not port.isdigit():
        raise ValueError(f"cold restart requires an emulator serial: {serial}")
    emulator = Path.home() / "Library" / "Android" / "sdk" / "emulator" / "emulator"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    compatibility = snapshot_launch_compatibility(snapshot_path)
    with log_path.open("wb") as log:
        subprocess.Popen(
            [
                str(emulator), "-avd", avd, "-port", port, "-no-window", "-no-audio",
                "-no-boot-anim", *compatibility, "-snapshot", snapshot, "-no-snapshot-save",
            ],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    # A restored guest can expose a stale boot-completed property before its
    # adb transport and Android services have settled.  Give the new emulator
    # process time to replace the old transport before probing it.
    time.sleep(3)
    wait_boot(serial)
    time.sleep(2)
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    if "Failed to load snapshot" in log_text or "starting from scratch" in log_text:
        raise RuntimeError(f"cold restart did not load snapshot: {snapshot}")
    if current_avd(serial) != avd:
        raise RuntimeError("cold-started emulator AVD name does not match")


def load_snapshot(
    serial: str, avd: str, snapshot_path: Path, log_path: Path,
    *, force_cold: bool = False,
) -> str:
    if force_cold:
        cold_start_snapshot(serial, avd, snapshot_path, log_path)
        return "cold_restart"
    snapshot = snapshot_path.name
    loaded = run(
        ["adb", "-s", serial, "emu", "avd", "snapshot", "load", snapshot],
        timeout=180, check=False,
    )
    combined = f"{loaded.stdout}\n{loaded.stderr}"
    if loaded.returncode == 0 and not re.search(r"(?m)^\s*KO\b", combined):
        try:
            wait_boot(serial)
            return "hot_load"
        except (RuntimeError, TimeoutError, subprocess.TimeoutExpired):
            pass
    cold_start_snapshot(serial, avd, snapshot_path, log_path)
    return "cold_restart"


def foreground_app(serial: str) -> dict[str, Any]:
    dumps = run(
        ["adb", "-s", serial, "shell", "dumpsys", "window"],
        timeout=30,
    ).stdout
    match = re.search(r"mCurrentFocus=.*?\bu\d+\s+([^\s/]+)/([^\s}]+)", dumps)
    if match is None:
        activities = run(
            ["adb", "-s", serial, "shell", "dumpsys", "activity", "activities"],
            timeout=30,
        ).stdout
        match = re.search(
            r"(?:mResumedActivity:|topResumedActivity=).*?\bu\d+\s+([^\s/]+)/([^\s}]+)",
            activities,
        )
    if match is None:
        raise RuntimeError("foreground App is unavailable from Android system state")
    package, activity = match.groups()
    pid_output = run(
        ["adb", "-s", serial, "shell", "pidof", package], timeout=20, check=False,
    ).stdout.strip()
    pid = int(pid_output.split()[0]) if pid_output.split() and pid_output.split()[0].isdigit() else None
    return {"package": package, "activity": activity, "pid": pid}


def capture_ui(serial: str, screenshot_path: Path) -> tuple[str, dict[str, Any], tuple[int, int]]:
    """Capture a stable UI state, tolerating post-restore adb/uiautomator races."""
    import uiautomator2 as u2

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            device = u2.connect(serial)
            device.reset_uiautomator()
            xml = device.dump_hierarchy(compressed=False, pretty=True)
            current = foreground_app(serial)
            window = device.window_size()
            device.screenshot(str(screenshot_path))
            return xml, current, window
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(3)
    assert last_error is not None
    raise last_error


def node_matches(node: ET.Element, selector: dict[str, Any]) -> bool:
    for key, value in selector.items():
        if key == "unique":
            continue
        if isinstance(value, bool):
            if node.get(key, "") == str(value).lower():
                continue
            return False
        if node.get(key, "") != value:
            return False
    return True


def unique_selector(root: ET.Element, node: ET.Element, package: str) -> dict[str, str] | None:
    if node.get("package") != package:
        return None
    candidates: list[dict[str, str]] = []
    for key in STABLE_SELECTOR_KEYS:
        value = node.get(key, "").strip()
        if value:
            candidates.append({"package": package, key: value})
            class_name = node.get("class", "").strip()
            if class_name:
                candidates.append({"package": package, "class": class_name, key: value})
    for selector in candidates:
        if sum(node_matches(item, selector) for item in root.iter("node")) == 1:
            return selector
    class_name = node.get("class", "").strip()
    enabled = node.get("enabled")
    if class_name and enabled in {"true", "false"}:
        selector = {
            "package": package,
            "class": class_name,
            "enabled": enabled == "true",
            "unique": True,
        }
        if sum(node_matches(item, selector) for item in root.iter("node")) == 1:
            return selector
    return None


def bounds(node: ET.Element) -> tuple[int, int, int, int] | None:
    values = [int(value) for value in re.findall(r"-?\d+", node.get("bounds", ""))]
    return tuple(values) if len(values) == 4 else None  # type: ignore[return-value]


def coordinate_selector(
    root: ET.Element, action: dict[str, Any], package: str, width: int, height: int
) -> tuple[dict[str, str] | None, dict[str, Any]]:
    x = max(0, min(width - 1, int((int(str(action["raw_x_hex"]), 16) / 0x7FFF) * width)))
    y = max(0, min(height - 1, int((int(str(action["raw_y_hex"]), 16) / 0x7FFF) * height)))
    parent = {child: item for item in root.iter() for child in item}
    containing = []
    for node in root.iter("node"):
        box = bounds(node)
        if box and box[0] <= x < box[2] and box[1] <= y < box[3] and node.get("package") == package:
            containing.append((max(1, (box[2] - box[0]) * (box[3] - box[1])), node))
    containing.sort(key=lambda item: (item[1].get("clickable") != "true", item[0]))
    checked: set[ET.Element] = set()
    for _, node in containing:
        current: ET.Element | None = node
        while current is not None and current not in checked:
            checked.add(current)
            selector = unique_selector(root, current, package)
            if selector:
                return selector, {
                    "legacy_point": [x, y],
                    "matched_bounds": bounds(current),
                    "matched_clickable": current.get("clickable") == "true",
                }
            current = parent.get(current)
    return None, {"legacy_point": [x, y], "reason": "no unique stable selector at point"}


def selector_for_target(
    root: ET.Element, package: str, target: str, by: str,
) -> dict[str, str] | None:
    attribute = {"resource-id": "resource-id", "description": "content-desc"}.get(by, "text")
    nodes = [
        node for node in root.iter("node")
        if node.get("package") == package and node.get(attribute) == target
    ]
    selectors = [unique_selector(root, node, package) for node in nodes]
    selectors = [selector for selector in selectors if selector is not None]
    unique = {json.dumps(selector, sort_keys=True): selector for selector in selectors}
    if len(unique) == 1:
        return next(iter(unique.values()))
    if not nodes:
        return {"package": package, attribute: target}
    return None


def translate_actions(
    root: ET.Element, package: str, actions: list[dict[str, Any]], window: tuple[int, int]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    translated: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        kind = action.get("action")
        action_type = action.get("type")
        translated_action: dict[str, Any] | None = None
        detail: dict[str, Any] = {}
        if kind == "click" and action_type in {"enter", "back"}:
            translated_action = {"action": "press", "key": action_type}
        elif kind in {"click", "double_click", "long_click"} and action_type == "coordinate":
            selector, detail = coordinate_selector(root, action, package, *window)
            if selector:
                translated_action = {"action": kind, "selector": selector}
        elif kind == "click" and action.get("target"):
            selector = selector_for_target(
                root, package, str(action["target"]), str(action.get("by") or action_type or "text")
            )
            if selector:
                translated_action = {"action": "click", "selector": selector}
        if translated_action is None:
            issues.append({"index": index, "legacy_action": action, **detail})
            continue
        translated.append(translated_action)
        delay = action.get("delay")
        if isinstance(delay, (int, float)) and 0 < float(delay) <= 30:
            translated.append({"action": "wait", "seconds": float(delay)})
    return translated, issues


def goal_candidate(package: str, config: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    targets: list[dict[str, Any]] = []
    issues: list[str] = []
    modes = set()
    for key, exists in (("verify_appear", True), ("verify_disappear", False)):
        group = config.get(key)
        if not isinstance(group, dict):
            continue
        mode = str(group.get("match_mode", "all"))
        modes.add(mode)
        by = str(group.get("by", "text"))
        attribute = {"resource-id": "resource-id", "description": "content-desc"}.get(by, "text")
        for value in group.get("targets") or []:
            normalized = str(value)
            if not normalized:
                issues.append(f"empty {attribute} goal target")
                continue
            targets.append({"selector": {"package": package, attribute: normalized}, "exists": exists})
    if not targets:
        return None, ["no goal targets"]
    if modes - {"all", "any"}:
        issues.append(f"unsupported match modes: {sorted(modes)}")
    if len(modes) > 1 or ("any" in modes and len(targets) > 1):
        issues.append("mixed or multi-target any semantics require manual review")
    by_selector: dict[str, set[bool]] = {}
    for target in targets:
        signature = json.dumps(
            target["selector"], ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        by_selector.setdefault(signature, set()).add(target["exists"])
    if any(values == {True, False} for values in by_selector.values()):
        issues.append("contradictory existence requirements for the same goal selector")
    mode = "any" if modes == {"any"} else "all"
    return {"mode": mode, "targets": targets}, issues


def assertion(root: ET.Element, contract: dict[str, Any]) -> bool:
    checks = []
    for target in contract["targets"]:
        found = any(node_matches(node, target["selector"]) for node in root.iter("node"))
        checks.append(found is target["exists"])
    return all(checks) if contract["mode"] == "all" else any(checks)


def normalized_resource_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.rsplit("/", 1)[-1].lower())


def legacy_position(value: str) -> tuple[str, str, int] | None:
    """Return the live resource-id/class family and old top coordinate."""
    with_bounds = re.fullmatch(r"(.+)__bounds_-?\d+_(-?\d+)_-?\d+_-?\d+", value)
    if with_bounds:
        return with_bounds.group(1), "", int(with_bounds.group(2))
    without_id = re.fullmatch(r"no_id_([^_]+)_-?\d+_(-?\d+)_-?\d+_-?\d+", value)
    if without_id:
        return "", f"android.widget.{without_id.group(1)}", int(without_id.group(2))
    return None


def positional_binding_nodes(
    root: ET.Element, package: str, required_ids: list[str],
) -> dict[str, tuple[ET.Element, int]]:
    """Map reviewed legacy bounds identities to current nodes by stable vertical order.

    Bounds are used only to recover the old logical field ordering during calibration;
    the returned runtime selectors never contain coordinates.
    """
    groups: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for required in required_ids:
        parsed = legacy_position(required)
        if parsed is None:
            continue
        resource_id, class_name, top = parsed
        groups.setdefault((resource_id, class_name), []).append((required, top))
    mapped: dict[str, tuple[ET.Element, int]] = {}
    for (resource_id, class_name), members in groups.items():
        nodes = [
            node for node in root.iter("node")
            if node.get("package") == package
            and (not resource_id or node.get("resource-id") == resource_id)
            and (not class_name or node.get("class") == class_name)
            and node.get("enabled") == "true"
        ]
        if len(nodes) < len(members):
            continue
        nodes.sort(key=lambda node: (bounds(node) or (0, 0, 0, 0))[1])
        members.sort(key=lambda item: item[1])
        if len(nodes) > len(members):
            if len(members) < 3:
                continue
            old_tops = [top for _, top in members]
            ranked: list[tuple[float, tuple[ET.Element, ...]]] = []
            for candidate in itertools.combinations(nodes, len(members)):
                live_tops = [(bounds(node) or (0, 0, 0, 0))[1] for node in candidate]
                ratios = [
                    (live_tops[index + 1] - live_tops[index])
                    / (old_tops[index + 1] - old_tops[index])
                    for index in range(len(old_tops) - 1)
                    if old_tops[index + 1] != old_tops[index]
                ]
                mean = sum(ratios) / len(ratios)
                score = sum(abs(value - mean) for value in ratios)
                ranked.append((score, candidate))
            ranked.sort(key=lambda item: item[0])
            if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 1e-6:
                continue
            nodes = list(ranked[0][1])
        for (required, _), node in zip(members, nodes):
            same_class = [
                item for item in root.iter("node")
                if item.get("package") == package
                and item.get("class") == node.get("class")
                and item.get("enabled") == node.get("enabled")
            ]
            mapped[required] = (node, same_class.index(node))
    return mapped


def binding_candidates(
    root: ET.Element, package: str, required_ids: list[str], mirror: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    bindings: dict[str, dict[str, Any]] = {}
    issues = []
    positional = positional_binding_nodes(root, package, required_ids)

    migration = mirror.get("locator_migration")
    migrated: dict[str, str] = {}
    if isinstance(migration, dict):
        original = migration.get("original")
        runtime = migration.get("runtime")
        originals = original if isinstance(original, list) else [original]
        runtimes = runtime if isinstance(runtime, list) else [runtime]
        if len(originals) == len(runtimes):
            migrated = {
                str(source): str(target) for source, target in zip(originals, runtimes)
                if isinstance(source, str) and isinstance(target, str)
            }

    semantic = mirror.get("semantic_fields") or []
    semantic_by_required = dict(zip(required_ids, semantic)) if len(semantic) == len(required_ids) else {}
    expected = mirror.get("expected_fields") or []
    expected_by_required = (
        dict(zip(required_ids, expected))
        if len(expected) == len(required_ids) and all(isinstance(item, str) and item for item in expected)
        else {}
    )
    for required in required_ids:
        lookup = migrated.get(required, expected_by_required.get(required, required))
        real_id, occurrence = (lookup.split("::#::", 1) + [None])[:2]
        nodes = [
            node for node in root.iter("node")
            if node.get("package") == package and node.get("resource-id") == real_id
        ]
        if not nodes and real_id:
            normalized = normalized_resource_id(real_id)
            normalized_nodes = [
                node for node in root.iter("node")
                if node.get("package") == package
                and node.get("resource-id")
                and normalized_resource_id(node.get("resource-id", "")) == normalized
            ]
            if len(normalized_nodes) == 1:
                nodes = normalized_nodes
                real_id = nodes[0].get("resource-id", real_id)
        semantic_selector: dict[str, Any] | None = None
        if not nodes and required in semantic_by_required:
            contract = semantic_by_required[required]
            nodes = []
            for node in root.iter("node"):
                if node.get("package") != package:
                    continue
                if contract.get("class") and node.get("class") != contract["class"]:
                    continue
                if contract.get("resource_id") and node.get("resource-id") != contract["resource_id"]:
                    continue
                if contract.get("text") and node.get("text") != contract["text"]:
                    continue
                descendant_text = contract.get("descendant_text")
                if descendant_text and not any(
                    child.get("text") == descendant_text for child in node.iter("node")
                ):
                    continue
                nodes.append(node)
            if len(nodes) == 1 and contract.get("descendant_text"):
                semantic_selector = {
                    "package": package,
                    "class": nodes[0].get("class", ""),
                    "descendant-text": str(contract["descendant_text"]),
                }
        positional_instance: int | None = None
        if not nodes and required in positional:
            node, positional_instance = positional[required]
            nodes = [node]
            real_id = node.get("resource-id", "")
        if not nodes and len(required_ids) == 1:
            editable = [
                node for node in root.iter("node")
                if node.get("package") == package
                and node.get("class") == "android.widget.EditText"
                and node.get("enabled") == "true"
            ]
            if len(editable) == 1:
                nodes = editable
                real_id = nodes[0].get("resource-id", "")
        if occurrence is not None:
            index = int(occurrence)
            nodes = [nodes[index]] if 0 <= index < len(nodes) else []
        if len(nodes) != 1:
            issues.append({"required_id": required, "live_matches": len(nodes)})
            continue
        node = nodes[0]
        class_name = node.get("class", "")
        if not class_name:
            issues.append({"required_id": required, "reason": "class missing"})
            continue
        selector = semantic_selector or unique_selector(root, node, package)
        if selector is not None:
            selector["class"] = class_name
        if selector is None and positional_instance is not None:
            selector = {
                "package": package,
                "class": class_name,
                "enabled": node.get("enabled") == "true",
                "instance": positional_instance,
                "unique": True,
            }
        if selector is None and real_id and node.get("resource-id") == real_id:
            selector = {"package": package, "class": class_name, "resource-id": real_id}
            if occurrence is not None:
                selector["instance"] = int(occurrence)
        if selector is None:
            issues.append({"required_id": required, "reason": "live node lacks a stable formal selector"})
            continue
        bindings[required] = selector
    return bindings, issues


def installed_artifact(serial: str, package: str) -> dict[str, Any]:
    dump = run(["adb", "-s", serial, "shell", "dumpsys", "package", package], timeout=30).stdout
    match = re.search(r"\bversionCode=(\d+)", dump)
    paths_output = run(["adb", "-s", serial, "shell", "pm", "path", package], timeout=30).stdout
    paths = [line.removeprefix("package:").strip() for line in paths_output.splitlines() if line.startswith("package:")]
    hashes = []
    for path in paths:
        checked = run(["adb", "-s", serial, "shell", "sha256sum", path], timeout=60, check=False)
        digest = checked.stdout.split()[0] if checked.returncode == 0 and checked.stdout.split() else None
        hashes.append({"path": path, "sha256": digest})
    complete = bool(match and hashes) and all(
        isinstance(row.get("sha256"), str) and len(row["sha256"]) == 64
        for row in hashes
    )
    artifact_set_sha256 = hashlib.sha256(
        json.dumps(
            hashes, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest() if complete else None
    return {
        "version_code": match.group(1) if match else None,
        "install_kind": "apk" if len(paths) == 1 else "split_apks" if paths else None,
        "installed_files": hashes,
        "artifact_set_sha256": artifact_set_sha256,
        "identity_complete": complete,
    }


def inspect_case(
    case: dict[str, Any], config: dict[str, Any], serial: str, avd: str, snapshot_path: Path,
    output: Path, registry_hash: str, backup_hash: str,
) -> dict[str, Any]:
    case_id = case["case_id"]
    package = case["mirror"]["package"]
    started = datetime.now(timezone.utc).isoformat()
    case_output = output / "cases" / case_id
    case_output.mkdir(parents=True, exist_ok=True)
    try:
        load_strategy = load_snapshot(
            serial, avd, snapshot_path, case_output / "emulator_cold_restart.log"
        )
        xml, current, window = capture_ui(serial, case_output / "initial.png")
        root = ET.fromstring(xml)
        (case_output / "initial.xml").write_text(xml, encoding="utf-8")
        app_node_count = sum(
            node.get("package") == package for node in root.iter("node")
        )
        if app_node_count == 0:
            raise RuntimeError(
                "captured UI contains no nodes owned by the expected App; "
                "a system overlay may be blocking the snapshot"
            )
        artifact = installed_artifact(serial, package)
        bindings, binding_issues = binding_candidates(
            root, package, case["required_resource_ids"], case["mirror"]
        )
        actions, action_issues = translate_actions(root, package, config.get("verify_action") or [], window)
        goal, goal_issues = goal_candidate(package, config)
        goal_initial = assertion(root, goal) if goal is not None and not goal_issues else None
        source_version_matches = str(artifact.get("version_code")) == str(case["mirror"].get("version_code"))
        version_matches = artifact.get("identity_complete") is True
        foreground_matches = current.get("package") == package
        issues: list[Any] = []
        if not foreground_matches:
            issues.append({"foreground_package": current.get("package"), "expected": package})
        if not version_matches:
            issues.append({
                "installed_artifact_identity": "incomplete",
            })
        issues.extend({"binding": item} for item in binding_issues)
        issues.extend({"action": item} for item in action_issues)
        issues.extend({"goal": item} for item in goal_issues)
        if goal_initial is True:
            issues.append({"goal": "goal already holds in the frozen initial state"})
        status = "inspect_ready" if not issues and goal_initial is False else "review_required"
        result = {
            "schema_version": "e2-structure-v1-live-inspection-1",
            "case_id": case_id,
            "task": case["task"],
            "status": status,
            "started_at_utc": started,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "registry_sha256": registry_hash,
            "case_contract_sha256": case_contract_sha256(case),
            "backup_manifest_sha256": backup_hash,
            "serial": serial,
            "avd": current_avd(serial),
            "load_strategy": load_strategy,
            "snapshot": snapshot_path.name,
            "snapshot_path": str(snapshot_path),
            "foreground": current,
            "foreground_matches": foreground_matches,
            "app_node_count": app_node_count,
            "artifact": artifact,
            "version_matches": version_matches,
            "version_alignment_policy": "freeze_live_installed_artifact_set",
            "source_version_matches": source_version_matches,
            "source_version_difference": None if source_version_matches else {
                "installed_version": artifact.get("version_code"),
                "historical_expected_version": case["mirror"].get("version_code"),
            },
            "binding": bindings,
            "binding_complete": set(bindings) == set(case["required_resource_ids"]),
            "verify_action": actions,
            "goal_assertion": goal,
            "goal_holds_initially": goal_initial,
            "issues": issues,
            "evidence": {
                "initial_xml": "initial.xml",
                "initial_xml_sha256": sha256_file(case_output / "initial.xml"),
                "initial_png": "initial.png",
                "initial_png_sha256": sha256_file(case_output / "initial.png"),
            },
        }
    except Exception as exc:
        result = {
            "schema_version": "e2-structure-v1-live-inspection-1",
            "case_id": case_id,
            "task": case["task"],
            "status": "infrastructure_error",
            "started_at_utc": started,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "registry_sha256": registry_hash,
            "case_contract_sha256": case_contract_sha256(case),
            "backup_manifest_sha256": backup_hash,
            "serial": serial,
            "snapshot": snapshot_path.name,
            "snapshot_path": str(snapshot_path),
            "error_type": type(exc).__name__,
            "error": str(exc) or repr(exc),
        }
    atomic_json(case_output / "inspection.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect frozen E2 case snapshots on one running AVD")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--avd", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--task", choices=("valid_pptr", "invalid_bug"), default="valid_pptr")
    parser.add_argument("--case", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    registry_path = args.registry.resolve()
    source_root = args.source_root.resolve()
    registry = read_json(registry_path)
    registry_hash = sha256_file(registry_path)
    backup_hash = ensure_backup(args.backup_manifest.resolve(), registry_path)
    if current_avd(args.device) != args.avd:
        raise RuntimeError(f"running AVD on {args.device} does not equal {args.avd}")
    requested = set(args.case or [])
    selected = []
    for case in registry["cases"]:
        if case["task"] != args.task or (requested and case["case_id"] not in requested):
            continue
        avd, snapshot = infer_avd(source_root, case)
        if avd == args.avd:
            selected.append((case, snapshot))
    if requested - {case["case_id"] for case, _ in selected}:
        raise RuntimeError(f"requested cases are not assigned to {args.avd}: {sorted(requested - {case['case_id'] for case, _ in selected})}")
    if args.limit is not None:
        selected = selected[:args.limit]
    results = []
    for ordinal, (case, snapshot) in enumerate(selected, 1):
        result_path = args.output / "cases" / case["case_id"] / "inspection.json"
        if args.resume and result_path.is_file():
            old = read_json(result_path)
            if old.get("registry_sha256") == registry_hash and old.get("backup_manifest_sha256") == backup_hash:
                results.append(old)
                print(f"[{ordinal}/{len(selected)}] {case['case_id']} resume {old.get('status')}", flush=True)
                continue
        config_label = case.get("app_or_page_source", {}).get("config")
        config_path = Path(config_label) if config_label else None
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {} if config_path else {}
        result = inspect_case(
            case, config, args.device, args.avd, snapshot, args.output.resolve(), registry_hash, backup_hash,
        )
        results.append(result)
        print(f"[{ordinal}/{len(selected)}] {case['case_id']} {result['status']}", flush=True)
    counts = Counter(result["status"] for result in results)
    summary = {
        "schema_version": "e2-structure-v1-live-inspection-summary-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "registry_sha256": registry_hash,
        "backup_manifest_sha256": backup_hash,
        "avd": args.avd,
        "device": args.device,
        "task": args.task,
        "selected_cases": [case["case_id"] for case, _ in selected],
        "counts": dict(sorted(counts.items())),
    }
    atomic_json(args.output.resolve() / f"summary_{args.task}_{args.avd}.json", summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
