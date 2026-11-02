"""Run a manifest-pinned case-specific reset with its required emulator renderer."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .structured_live_calibration import current_avd, run, stop_conflicting_avd, wait_boot
from .structured_snapshot_reset import synchronize_device_time, verify_device_time


ISHARING_RESET_ID = "isharing:P043:fresh-client-phone:v1"


def _isharing_phone_row_center(hierarchy: str) -> tuple[int, int]:
    """Locate the unique E.164 phone row without exposing its stored value."""
    root = ET.fromstring(hierarchy)
    rows = [
        node for node in root.iter("node")
        if node.get("clickable") == "true"
        and re.fullmatch(r"\+1 \d{10}", node.get("content-desc", ""))
    ]
    if len(rows) != 1:
        raise RuntimeError("fresh-client phone row is missing or ambiguous")
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", rows[0].get("bounds", ""))
    if match is None:
        raise RuntimeError("fresh-client phone row has invalid bounds")
    left, top, right, bottom = map(int, match.groups())
    return (left + right) // 2, (top + bottom) // 2


def _repair_isharing_remote_baseline(fresh_serial: str) -> None:
    """Repair the P043 remote phone through its separate fresh client."""
    import uiautomator2 as u2

    baseline_result = run([
        "security", "find-generic-password",
        "-s", "cd-eval-isharing-baseline-phone",
        "-a", "p043-isolated-google", "-w",
    ], timeout=20)
    baseline = baseline_result.stdout.strip()
    if not re.fullmatch(r"\d{10}", baseline):
        raise RuntimeError("stored P043 baseline phone has unexpected shape")
    device = u2.connect(fresh_serial)
    if device.app_current().get("package") != "com.isharing.isharing":
        raise RuntimeError("fresh-client iSharing session is unavailable for repair")
    x, y = _isharing_phone_row_center(device.dump_hierarchy())
    device.click(x, y)
    field = device(resourceId="textInputPhonenumber")
    if not field.wait(timeout=10):
        raise RuntimeError("fresh-client phone editor unavailable after row selection")
    field.set_text(baseline)
    save = device(text="Save")
    if not save.wait(timeout=10):
        raise RuntimeError("fresh-client phone Save control unavailable")
    save.click()
    time.sleep(3)


def _run_case_command(
    command: list[str], *, environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    executable = sys.executable if command[0] == "python3" else command[0]
    return subprocess.run(
        [executable, *command[1:]],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=600,
        env=environment,
    )


def _last_json_object(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError("case-specific reset did not emit a JSON receipt")


def _running_argv(avd: str, serial: str) -> list[str] | None:
    port = serial.removeprefix("emulator-")
    result = run(["ps", "-Ao", "command"], timeout=20)
    matches = [
        shlex.split(line) for line in result.stdout.splitlines()
        if "qemu-system" in line and f"-avd {avd}" in line and f"-port {port}" in line
    ]
    if len(matches) > 1:
        raise RuntimeError("multiple emulator processes match the case reset")
    return matches[0] if matches else None


def _contains_required(argv: list[str], required: list[str]) -> bool:
    index = 0
    while index < len(required):
        token = required[index]
        if token.startswith("-") and index + 1 < len(required) and not required[index + 1].startswith("-"):
            return token in argv and argv.index(token) + 1 < len(argv) and argv[argv.index(token) + 1] == required[index + 1] and _contains_required(argv, required[index + 2 :])
        if token not in argv:
            return False
        index += 1
    return True


def _cold_start_required(
    serial: str, avd: str, snapshot: str, required: list[str], log_path: Path,
) -> None:
    stop_conflicting_avd(serial, avd)
    run(["adb", "-s", serial, "emu", "kill"], timeout=20, check=False)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if run(["adb", "-s", serial, "get-state"], timeout=5, check=False).returncode != 0:
            break
        time.sleep(0.5)
    port = serial.removeprefix("emulator-")
    if not port.isdigit():
        raise ValueError("case-specific reset requires an emulator serial")
    emulator = Path.home() / "Library" / "Android" / "sdk" / "emulator" / "emulator"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as log:
        subprocess.Popen(
            [
                str(emulator), "-avd", avd, "-port", port, "-no-boot-anim",
                *required, "-snapshot", snapshot, "-no-snapshot-save",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    time.sleep(3)
    wait_boot(serial)
    time.sleep(2)
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    if "Failed to load snapshot" in log_text or "starting from scratch" in log_text:
        raise RuntimeError("case-specific cold restart did not load the target snapshot")
    if current_avd(serial) != avd:
        raise RuntimeError("case-specific cold restart selected the wrong AVD")


def reset(
    manifest_path: Path,
    serial: str,
    avd: str,
    reset_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    case_id = manifest.get("case_id")
    if manifest.get("status") != "ready" or not isinstance(case_id, str):
        raise RuntimeError("case-specific reset manifest is not ready")
    payload_case = payload.get("case", {}).get("case_id")
    payload_oracle = payload.get("attempt", {}).get("oracle", {}).get("case_id")
    if payload_case is not None and payload_case != case_id:
        raise ValueError("case-specific reset payload differs from its manifest")
    if payload_oracle is not None and payload_oracle != case_id:
        raise ValueError("case-specific reset Oracle differs from its manifest")
    declared_reset_id = manifest.get("attempt_reset_id") or manifest.get("reset_id")
    if declared_reset_id != reset_id:
        raise ValueError("case-specific reset_id differs from its manifest")
    command = manifest.get("attempt_prepare_command") or manifest.get("required_prepare_command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
        raise RuntimeError("case-specific reset command is unavailable")
    snapshot = manifest.get("target_hold_snapshot") or manifest.get("snapshot")
    required = manifest.get("required_emulator_args") or []
    if not isinstance(snapshot, str) or not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise RuntimeError("case-specific snapshot/renderer contract is invalid")
    argv = _running_argv(avd, serial)
    if argv is None or not _contains_required(argv, required):
        evidence_dir = Path(payload.get("attempt", {}).get("evidence_dir", manifest_path.parent))
        _cold_start_required(
            serial, avd, snapshot, required,
            evidence_dir / "case_reset_emulator.log",
        )
    if current_avd(serial) != avd:
        raise RuntimeError("running emulator AVD differs from the case reset")
    device_time = synchronize_device_time(serial)
    command_environment = None
    fresh_serial = None
    fresh_client_device_time = None
    if reset_id == ISHARING_RESET_ID:
        fresh_serial = os.environ.get("E2_P043_FRESH_CLIENT_SERIAL", "emulator-5564")
        if fresh_serial == serial:
            raise RuntimeError("P043 target and fresh-client serials must differ")
        # Preserve the signed-in fresh-client session. Its clock check is
        # read-only: no snapshot load and no guest-time mutation.
        fresh_client_device_time = verify_device_time(fresh_serial)
        command_environment = os.environ.copy()
        command_environment.update({
            "P043_TARGET_SERIAL": serial,
            "P043_FRESH_SERIAL": fresh_serial,
        })
    completed = _run_case_command(command, environment=command_environment)
    if completed.returncode != 0 and reset_id == ISHARING_RESET_ID:
        # The preserved helper used a fixed y-coordinate for the editable
        # phone row.  Current API35 layout metrics can move that row while its
        # unique App-owned accessibility contract remains intact.  Repair the
        # baseline through that contract, then require the original helper to
        # clear/re-authenticate/re-read it independently before acceptance.
        assert fresh_serial is not None
        _repair_isharing_remote_baseline(fresh_serial)
        completed = _run_case_command(command, environment=command_environment)
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "unknown error"
        raise RuntimeError(f"case-specific reset command failed: {detail}")
    receipt = _last_json_object(completed.stdout)
    if (
        receipt.get("case_id") != case_id
        or receipt.get("reset_id") != reset_id
        or receipt.get("reset_verified") is not True
        or receipt.get("remote_baseline_verified") is not True
    ):
        raise RuntimeError("case-specific reset receipt is incomplete")
    result = {
        **receipt,
        "remote_baseline": "verified",
        "case_specific_reset": True,
        "device_time": device_time,
    }
    if fresh_client_device_time is not None:
        result["fresh_client_device_time"] = fresh_client_device_time
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a manifest-pinned case-specific reset")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--avd", required=True)
    parser.add_argument("--reset-id", required=True)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        result = reset(
            args.manifest.resolve(), args.device, args.avd, args.reset_id, payload,
        )
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        sys.stderr.write(json.dumps({
            "error_type": type(exc).__name__, "message": str(exc),
        }, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
