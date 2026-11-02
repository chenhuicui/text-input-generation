"""Restore a frozen target-page snapshot for an independently started attempt.

The default receipt is local-only. A reviewed prepare action may also restore a
remote baseline through the App UI; in that case the promoted command freezes
``--remote-baseline verified`` and the live controls must have demonstrated the
same action and resulting input-page baseline first.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .structured_android import AndroidDriver
from .structured_live_calibration import AVD_DIRECTORY_NAMES, assertion, load_snapshot


def resolve_snapshot_path(manifest_path: Path, manifest: dict[str, Any], snapshot: str) -> Path:
    declared = Path(
        manifest.get("target_hold_snapshot_path") or manifest.get("snapshot_path") or ""
    )
    if declared.name == snapshot and (declared / "snapshot.pb").is_file():
        return declared
    # A few migrated mirror manifests freeze the snapshot name but predate the
    # portable absolute-path field.  Resolve only an exact, unique asset below
    # an ancestor of that manifest; ambiguity remains a hard error.
    for ancestor in manifest_path.parents:
        matches = [
            path for path in ancestor.glob(f"*/snapshots/{snapshot}")
            if (path / "snapshot.pb").is_file()
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError("declared snapshot name resolves to multiple assets")
    raise RuntimeError("declared snapshot asset is missing")


def run(argv: list[str], timeout: float = 180) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed: {argv[0]}")
    return completed


def isolate_external_network(serial: str) -> dict[str, Any]:
    """Enable Android airplane mode and return a verified local receipt."""
    run(["adb", "-s", serial, "shell", "cmd", "connectivity", "airplane-mode", "enable"], timeout=30)
    state = run(
        ["adb", "-s", serial, "shell", "settings", "get", "global", "airplane_mode_on"],
        timeout=20,
    ).stdout.strip()
    if state != "1":
        raise RuntimeError("Android airplane mode was not verified")
    return {"mode": "offline", "airplane_mode_verified": True}


def verify_device_time(serial: str) -> dict[str, Any]:
    """Read the guest clock without mutating the device and verify host skew."""
    host_before_epoch_millis = int(time.time() * 1000)
    observed_text = run(
        ["adb", "-s", serial, "shell", "date", "+%s%3N"], timeout=20,
    ).stdout.strip()
    host_after_epoch_millis = int(time.time() * 1000)
    if not observed_text.isdigit():
        raise RuntimeError("Android device time verification returned a non-numeric value")
    observed_epoch_millis = int(observed_text)
    skew_millis = min(
        abs(observed_epoch_millis - host_before_epoch_millis),
        abs(observed_epoch_millis - host_after_epoch_millis),
    )
    if skew_millis > 5_000:
        raise RuntimeError("Android device time is not current")
    return {
        "host_before_epoch_millis": host_before_epoch_millis,
        "observed_epoch_millis": observed_epoch_millis,
        "host_after_epoch_millis": host_after_epoch_millis,
        "absolute_skew_millis": skew_millis,
        "verified": True,
        "mutation_performed": False,
    }


def synchronize_device_time(serial: str) -> dict[str, Any]:
    """Replace a snapshot's frozen guest clock with the current host time.

    Android emulator snapshots preserve wall-clock state. Network-backed Apps
    can consequently reject TLS sessions or cached credentials immediately
    after an otherwise valid snapshot restore. ``cmd alarm set-time`` is
    available to the adb shell user on the supported emulator images, so make
    the correction explicit and fail closed when it cannot be verified.
    """
    last_receipt: dict[str, Any] | None = None
    for attempt in range(1, 4):
        requested_epoch_millis = int(time.time() * 1000)
        run([
            "adb", "-s", serial, "shell", "cmd", "alarm", "set-time",
            str(requested_epoch_millis),
        ], timeout=20)
        observed_text = run(
            ["adb", "-s", serial, "shell", "date", "+%s%3N"], timeout=20,
        ).stdout.strip()
        if not observed_text.isdigit():
            raise RuntimeError("Android device time verification returned a non-numeric value")
        observed_epoch_millis = int(observed_text)
        verified_against_host_millis = int(time.time() * 1000)
        skew_millis = abs(observed_epoch_millis - verified_against_host_millis)
        last_receipt = {
            "requested_epoch_millis": requested_epoch_millis,
            "observed_epoch_millis": observed_epoch_millis,
            "verified_against_host_millis": verified_against_host_millis,
            "absolute_skew_millis": skew_millis,
            "verification_attempt": attempt,
            "verified": skew_millis <= 5_000,
        }
        if last_receipt["verified"]:
            return last_receipt
        if attempt < 3:
            # A resumed emulator can briefly expose the snapshot clock after
            # accepting set-time. Repeat the idempotent clock correction and
            # retain the successful attempt in the formal receipt.
            time.sleep(0.5 * attempt)
    assert last_receipt is not None
    raise RuntimeError("Android device time remained stale after snapshot restore")


def push_prepare_files(
    serial: str, files: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Install hash-pinned local fixtures after every VM-snapshot restore."""
    receipts: list[dict[str, Any]] = []
    for item in files or []:
        if not isinstance(item, dict):
            raise ValueError("prepare files must be JSON objects")
        host = Path(str(item.get("host_path", ""))).resolve()
        device_path = item.get("device_path")
        expected = item.get("sha256")
        if not host.is_file():
            raise FileNotFoundError(f"prepare file is missing: {host}")
        if not isinstance(device_path, str) or not device_path.startswith("/sdcard/"):
            raise ValueError("prepare file device_path must be below /sdcard")
        actual = hashlib.sha256(host.read_bytes()).hexdigest()
        if not isinstance(expected, str) or actual != expected:
            raise ValueError("prepare file SHA-256 mismatch")
        run(["adb", "-s", serial, "push", str(host), device_path], timeout=60)
        # adb push preserves the host mtime. Android's Recent picker may hide
        # an otherwise indexed fixture when that timestamp predates its
        # current filter window, so make the per-attempt copy current before
        # asking MediaStore to scan it.
        run(["adb", "-s", serial, "shell", "touch", device_path], timeout=20)
        if item.get("media_scan") is True:
            run([
                "adb", "-s", serial, "shell", "am", "broadcast", "-a",
                "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d",
                f"file://{device_path}",
            ], timeout=30)
        receipts.append({
            "device_path": device_path,
            "sha256": actual,
            "media_scan": item.get("media_scan") is True,
        })
    return receipts


def _adb_foreground_package(serial: str) -> str:
    output = run(["adb", "-s", serial, "shell", "dumpsys", "window"], timeout=30).stdout
    match = re.search(r"mCurrentFocus=.*?\bu\d+\s+([^\s/]+)/", output)
    if match is None:
        raise RuntimeError("ADB preparation could not verify the foreground package")
    return match.group(1)


def _adb_window_size(serial: str) -> tuple[int, int]:
    output = run(["adb", "-s", serial, "shell", "wm", "size"], timeout=20).stdout
    sizes = re.findall(r"(\d+)x(\d+)", output)
    if not sizes:
        raise RuntimeError("ADB preparation could not verify the screen size")
    width, height = sizes[-1]
    return int(width), int(height)


def _adb_hierarchy(serial: str) -> str:
    remote = "/sdcard/codex_e2_prepare.xml"
    last_error = "ADB preparation could not capture the final UI hierarchy"
    for attempt in range(3):
        try:
            run(["adb", "-s", serial, "shell", "uiautomator", "dump", remote], timeout=30)
            xml = run(["adb", "-s", serial, "shell", "cat", remote], timeout=20).stdout
            ET.fromstring(xml)
            return xml
        except (RuntimeError, ET.ParseError) as exc:
            last_error = str(exc)
            if attempt < 2:
                time.sleep(1)
    raise RuntimeError(last_error)


def run_adb_prepare(
    serial: str,
    actions: list[dict[str, Any]] | None,
    final_assertion: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Run a frozen, fail-closed App UI preparation without a u2 session.

    This exists for App-owned Compose surfaces which are visible in the native
    hierarchy but whose list row is dismissed, rather than activated, when a
    uiautomator2 HTTP click is issued. Every gesture is guarded by the exact
    foreground package and screen dimensions, and a final App-owned UI
    assertion is mandatory.
    """
    if not actions:
        if final_assertion is not None:
            raise ValueError("ADB prepare assertion requires ADB prepare actions")
        return None
    if not isinstance(final_assertion, dict):
        raise ValueError("ADB prepare actions require a final UI assertion")
    frozen_actions: list[dict[str, Any]] = []
    for index, item in enumerate(actions):
        if not isinstance(item, dict):
            raise ValueError("ADB prepare actions must be JSON objects")
        kind = item.get("action")
        if kind == "wait":
            seconds = float(item.get("seconds", 0))
            if not 0 < seconds <= 15:
                raise ValueError(f"invalid ADB prepare wait at index {index}")
            time.sleep(seconds)
            frozen_actions.append({"action": "wait", "seconds": seconds})
            continue
        package = item.get("package")
        if not isinstance(package, str) or not package:
            raise ValueError(f"ADB prepare action lacks a package guard at index {index}")
        if _adb_foreground_package(serial) != package:
            raise RuntimeError(f"ADB prepare foreground package changed at index {index}")
        if kind == "tap":
            width, height = _adb_window_size(serial)
            expected_width = int(item.get("screen_width", 0))
            expected_height = int(item.get("screen_height", 0))
            x, y = int(item.get("x", -1)), int(item.get("y", -1))
            if (width, height) != (expected_width, expected_height):
                raise RuntimeError(f"ADB prepare screen size changed at index {index}")
            if not (0 <= x < width and 0 <= y < height):
                raise ValueError(f"ADB prepare tap is outside the screen at index {index}")
            run(["adb", "-s", serial, "shell", "input", "tap", str(x), str(y)], timeout=20)
            frozen_actions.append(dict(item))
        elif kind == "input_text":
            value = item.get("value")
            if not isinstance(value, str) or not value or len(value) > 120:
                raise ValueError(f"invalid ADB prepare text at index {index}")
            if re.fullmatch(r"[A-Za-z0-9 ._@+\-]+", value) is None:
                raise ValueError(f"unsafe ADB prepare text at index {index}")
            run([
                "adb", "-s", serial, "shell", "input", "text",
                value.replace(" ", "%s"),
            ], timeout=20)
            frozen_actions.append(dict(item))
        else:
            raise ValueError(f"unsupported ADB prepare action at index {index}")
    xml = _adb_hierarchy(serial)
    if not assertion(ET.fromstring(xml), final_assertion):
        raise RuntimeError("ADB preparation final UI assertion failed")
    return {
        "verified": True,
        "actions_sha256": hashlib.sha256(
            json.dumps(
                frozen_actions, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "final_assertion": final_assertion,
        "final_ui_sha256": hashlib.sha256(xml.encode("utf-8")).hexdigest(),
    }


def restore(
    manifest_path: Path,
    serial: str,
    reset_id: str,
    payload: dict[str, Any],
    avd_override: str | None = None,
    offline: bool = False,
    prepare_action: list[dict[str, Any]] | None = None,
    force_cold: bool = False,
    prepare_files: list[dict[str, Any]] | None = None,
    remote_baseline: str = "not_applicable",
    prepare_adb_actions: list[dict[str, Any]] | None = None,
    prepare_adb_assertion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    case = payload.get("case", {})
    oracle = payload.get("attempt", {}).get("oracle", {})
    if case.get("case_id") != manifest.get("case_id") or oracle.get("case_id") != manifest.get("case_id"):
        raise ValueError("reset request case_id does not match the frozen mirror manifest")
    if manifest.get("status") != "ready":
        raise RuntimeError("mirror manifest is not ready")
    if manifest.get("readiness_scope") == "full_independent_attempt_with_remote_reset":
        raise RuntimeError("remote-state cases must use their case-specific reset command")
    if remote_baseline not in {"not_applicable", "verified"}:
        raise ValueError("remote baseline receipt must be not_applicable or verified")
    if remote_baseline == "verified" and (offline or not prepare_action):
        raise ValueError("verified remote baseline requires an online prepare action")
    snapshot = manifest.get("target_hold_snapshot") or manifest.get("snapshot")
    if not isinstance(snapshot, str) or not snapshot:
        raise RuntimeError("declared snapshot asset is missing")
    snapshot_path = resolve_snapshot_path(manifest_path, manifest, snapshot)
    if manifest.get("serial") and manifest["serial"] != serial and not avd_override:
        raise RuntimeError("mirror manifest targets a different emulator serial")
    manifest_avd = manifest.get("avd") or manifest.get("avd_name")
    if manifest_avd and avd_override and manifest_avd != avd_override:
        raise RuntimeError("explicit AVD does not match the snapshot manifest")
    snapshot_avd_directory = (
        snapshot_path.parent.parent.name
        if snapshot_path.parent.name == "snapshots" else ""
    )
    expected_avd = (
        manifest_avd
        or avd_override
        or AVD_DIRECTORY_NAMES.get(snapshot_avd_directory)
    )
    if not isinstance(expected_avd, str) or not expected_avd:
        raise RuntimeError("snapshot reset requires an explicit or path-resolvable AVD")
    running_avd: str | None = None
    try:
        run(["adb", "-s", serial, "get-state"], timeout=20)
        output = run(
            ["adb", "-s", serial, "emu", "avd", "name"], timeout=20,
        ).stdout
        running_avd = next(
            (line.strip() for line in output.splitlines() if line.strip() != "OK"),
            None,
        )
    except RuntimeError:
        # A formal schedule spans multiple AVDs and may start with no emulator.
        # ``load_snapshot(..., force_cold=True)`` starts the exact frozen AVD,
        # port, renderer compatibility, and snapshot through the existing
        # bounded cold-start path.
        running_avd = None
    avd_switch_required = running_avd != expected_avd
    evidence_dir = Path(payload.get("attempt", {}).get("evidence_dir", snapshot_path.parent))
    strategy = load_snapshot(
        serial, expected_avd, snapshot_path,
        evidence_dir / "snapshot_reset_emulator.log",
        force_cold=force_cold or avd_switch_required,
    )
    device_time = synchronize_device_time(serial)
    prepared_files = push_prepare_files(serial, prepare_files)
    network_isolation = isolate_external_network(serial) if offline else None
    adb_preparation = run_adb_prepare(serial, prepare_adb_actions, prepare_adb_assertion)
    if prepare_action:
        import uiautomator2 as u2

        device = u2.connect(serial)
        AndroidDriver(serial, device=device).actions(prepare_action, stop_is_error=True)
    return {
        "case_id": manifest["case_id"],
        "snapshot": snapshot,
        "reset_id": reset_id,
        "reset_verified": True,
        "remote_baseline": remote_baseline,
        "vm_snapshot_restored": True,
        "snapshot_load_strategy": strategy,
        "device_time": device_time,
        "avd_before_reset": running_avd,
        "avd_switch_required": avd_switch_required,
        "prepared_files": prepared_files,
        "adb_preparation": adb_preparation,
        "network_isolation": network_isolation,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore a local-only E2 snapshot baseline")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--device", default="emulator-5556")
    parser.add_argument("--avd")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--prepare-action-json")
    parser.add_argument("--prepare-files-json")
    parser.add_argument("--prepare-adb-actions-json")
    parser.add_argument("--prepare-adb-assertion-json")
    parser.add_argument(
        "--remote-baseline", choices=("not_applicable", "verified"),
        default="not_applicable",
    )
    parser.add_argument("--force-cold", action="store_true")
    parser.add_argument("--reset-id", required=True)
    args = parser.parse_args()
    try:
        prepare_action = (
            json.loads(args.prepare_action_json)
            if args.prepare_action_json is not None else None
        )
        if prepare_action is not None and not isinstance(prepare_action, list):
            raise ValueError("prepare action must be a JSON action list")
        prepare_files = (
            json.loads(args.prepare_files_json)
            if args.prepare_files_json is not None else None
        )
        if prepare_files is not None and not isinstance(prepare_files, list):
            raise ValueError("prepare files must be a JSON list")
        prepare_adb_actions = (
            json.loads(args.prepare_adb_actions_json)
            if args.prepare_adb_actions_json is not None else None
        )
        if prepare_adb_actions is not None and not isinstance(prepare_adb_actions, list):
            raise ValueError("prepare ADB actions must be a JSON list")
        prepare_adb_assertion = (
            json.loads(args.prepare_adb_assertion_json)
            if args.prepare_adb_assertion_json is not None else None
        )
        if prepare_adb_assertion is not None and not isinstance(prepare_adb_assertion, dict):
            raise ValueError("prepare ADB assertion must be a JSON object")
        result = restore(
            args.manifest.resolve(), args.device, args.reset_id, json.load(sys.stdin), args.avd,
            args.offline, prepare_action,
            args.force_cold,
            prepare_files,
            args.remote_baseline,
            prepare_adb_actions,
            prepare_adb_assertion,
        )
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        sys.stderr.write(json.dumps({"error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
