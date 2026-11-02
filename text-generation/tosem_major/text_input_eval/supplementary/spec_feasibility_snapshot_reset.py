#!/usr/bin/env python3
"""Restore and verify the local-only B014 specification-pilot baseline.

The command is an attempt reset hook.  It never calls a model and never saves
or modifies the named snapshot.  It fails closed unless the expected AVD,
renderer, installed APK, foreground page, and target field all match.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path("/Volumes/Extreme Pro/emulator/CD_115page_37bug")
CASES: dict[str, dict[str, Any]] = {
    "B014": {
        "avd": "cd_115page_37bug_api33",
        "avd_path": ROOT / "avd",
        "serial": "emulator-5554",
        "port": "5554",
        "snapshot": "B014_baseline_v1",
        "reset_id": "B014-baseline-v1",
        "package": "fr.gaulupeau.apps.InThePoche",
        "version_code": "228",
        "apk_sha256": "92840ede49698e1ded0d876abc7dd57ac71eadb75c736aef4d708c00925c82f2",
        "resource_id": "fr.gaulupeau.apps.InThePoche:id/search_src_text",
        "activity_contains": "MainActivity",
        "gpu": "swiftshader_indirect",
    },
}


def command(*args: str, timeout: float = 45, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-2000:]
        raise RuntimeError(f"command failed ({args[0]}): {detail}")
    return completed


def validate_request(case_id: str) -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        return
    try:
        request = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("reset request is not valid JSON") from exc
    if request.get("protocol_id") != "reviewer2-spec-feasibility-pilot-v1":
        raise RuntimeError("reset request protocol differs")
    if request.get("case", {}).get("case_id") != case_id:
        raise RuntimeError("reset request case differs")


def verify_process(case: dict[str, Any]) -> None:
    lines = command("ps", "-Ao", "command").stdout.splitlines()
    matches = []
    for line in lines:
        if "qemu-system-aarch64-headless" not in line:
            continue
        argv = shlex.split(line)
        if (
            "-avd" in argv
            and argv[argv.index("-avd") + 1] == case["avd"]
            and "-port" in argv
            and argv[argv.index("-port") + 1] == case["port"]
        ):
            matches.append(argv)
    if len(matches) != 1:
        raise RuntimeError("expected exactly one matching headless emulator")
    argv = matches[0]
    if "-no-window" not in argv or "-no-snapshot-save" not in argv:
        raise RuntimeError("emulator is not in the frozen non-saving headless mode")
    if "-gpu" not in argv or argv[argv.index("-gpu") + 1] != case["gpu"]:
        raise RuntimeError("emulator renderer differs from the frozen contract")


def installed_receipt(case: dict[str, Any]) -> dict[str, str]:
    serial = case["serial"]
    package = case["package"]
    rows = command("adb", "-s", serial, "shell", "pm", "path", package).stdout.strip().splitlines()
    if len(rows) != 1 or not rows[0].startswith("package:"):
        raise RuntimeError("expected one installed base APK")
    remote_path = rows[0].removeprefix("package:")
    digest = command("adb", "-s", serial, "shell", "sha256sum", remote_path).stdout.split()[0]
    if digest != case["apk_sha256"]:
        raise RuntimeError("installed APK SHA-256 differs")
    dump = command("adb", "-s", serial, "shell", "dumpsys", "package", package).stdout
    version = re.search(r"\bversionCode=(\d+)\b", dump)
    if version is None or version.group(1) != case["version_code"]:
        raise RuntimeError("installed versionCode differs")
    return {
        "package": package,
        "version_code": version.group(1),
        "remote_apk_path": remote_path,
        "remote_apk_sha256": digest,
    }


def prepare(
    case_id: str,
    *,
    serial: str | None = None,
    avd: str | None = None,
    avd_path: Path | None = None,
) -> dict[str, Any]:
    case = dict(CASES[case_id])
    if serial is not None:
        if re.fullmatch(r"emulator-\d+", serial) is None:
            raise RuntimeError("invalid emulator serial override")
        case["serial"] = serial
        case["port"] = serial.removeprefix("emulator-")
    if avd is not None:
        case["avd"] = avd
    if avd_path is not None:
        case["avd_path"] = avd_path
    snapshot_path = case["avd_path"] / "snapshots" / case["snapshot"] / "snapshot.pb"
    if not snapshot_path.is_file():
        raise RuntimeError(f"missing case snapshot: {snapshot_path}")
    verify_process(case)
    serial = case["serial"]
    avd_name = command("adb", "-s", serial, "emu", "avd", "name").stdout
    if case["avd"] not in avd_name:
        raise RuntimeError("wrong emulator is connected")
    loaded = command(
        "adb", "-s", serial, "emu", "avd", "snapshot", "load", case["snapshot"],
        timeout=180,
        check=False,
    )
    output = (loaded.stdout or "") + (loaded.stderr or "")
    if loaded.returncode != 0 or re.search(r"(?m)^\s*KO\b", output):
        raise RuntimeError(f"snapshot load failed: {output.strip()}")
    command("adb", "-s", serial, "wait-for-device", timeout=60)
    command(
        "adb", "-s", serial, "shell", "cmd", "alarm", "set-time",
        str(int(time.time() * 1000)),
    )
    installed = installed_receipt(case)

    import uiautomator2 as u2

    device = u2.connect(serial)
    foreground = device.app_current()
    if foreground.get("package") != case["package"]:
        raise RuntimeError(f"snapshot restored to wrong package: {foreground}")
    if case["activity_contains"] not in foreground.get("activity", ""):
        raise RuntimeError(f"snapshot restored to wrong activity: {foreground}")
    field = device(resourceId=case["resource_id"])
    if not field.wait(timeout=10):
        raise RuntimeError(f"target field missing: {case['resource_id']}")
    return {
        "case_id": case_id,
        "reset_id": case["reset_id"],
        "reset_verified": True,
        "snapshot": case["snapshot"],
        "snapshot_pb_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
        "foreground": foreground,
        "target_field_present": True,
        "installed": installed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore a specification-pilot case snapshot")
    parser.add_argument("case_id", choices=sorted(CASES))
    parser.add_argument("--serial")
    parser.add_argument("--avd")
    parser.add_argument("--avd-path", type=Path)
    args = parser.parse_args()
    validate_request(args.case_id)
    print(json.dumps(prepare(
        args.case_id,
        serial=args.serial,
        avd=args.avd,
        avd_path=args.avd_path,
    ), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
