#!/usr/bin/env python3
"""Create an immutable, no-model runtime qualification receipt for one case."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .spec_feasibility_contract import canonical_json, validate_config


REPO_ROOT = Path(__file__).resolve().parents[3]


def run(
    argv: list[str],
    *,
    timeout: float = 60,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        argv,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        cwd=REPO_ROOT,
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-2000:]
        raise RuntimeError(f"command failed ({argv[0]}): {detail}")
    return completed


def resolve(config_path: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (config_path.parent / path).resolve()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def last_json_object(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError("reset hook did not emit a JSON receipt")


def apksigner() -> Path:
    candidates = list((Path.home() / "Library/Android/sdk/build-tools").glob("*/apksigner"))
    if not candidates:
        raise RuntimeError("apksigner is unavailable")

    def version(path: Path) -> tuple[int, ...]:
        return tuple(int(part) for part in re.findall(r"\d+", path.parent.name))

    return max(candidates, key=version)


def signer_digest(path: Path) -> str:
    output = run([str(apksigner()), "verify", "--print-certs", str(path)]).stdout
    match = re.search(r"Signer #1 certificate SHA-256 digest:\s*([0-9a-fA-F]{64})", output)
    if match is None:
        raise RuntimeError("apksigner did not return a signer SHA-256")
    return match.group(1).lower()


def installed_receipt(execution: dict[str, Any]) -> dict[str, str]:
    serial = execution["device_serial"]
    package = execution["package"]
    rows = run(["adb", "-s", serial, "shell", "pm", "path", package]).stdout.strip().splitlines()
    if len(rows) != 1 or not rows[0].startswith("package:"):
        raise RuntimeError("qualification requires exactly one installed base APK")
    remote_path = rows[0].removeprefix("package:")
    remote_sha = run(
        ["adb", "-s", serial, "shell", "sha256sum", remote_path]
    ).stdout.split()[0]
    if remote_sha != execution["apk_sha256"]:
        raise RuntimeError("installed APK differs from the frozen SHA-256")
    dump = run(["adb", "-s", serial, "shell", "dumpsys", "package", package]).stdout
    version = re.search(r"\bversionCode=(\d+)\b", dump)
    if version is None or version.group(1) != str(execution["version_code"]):
        raise RuntimeError("installed versionCode differs")
    return {
        "package": package,
        "version_code": version.group(1),
        "remote_apk_path": remote_path,
        "remote_apk_sha256": remote_sha,
    }


def docker_receipt(case_id: str) -> dict[str, Any] | None:
    if case_id != "B023":
        return None
    value = json.loads(run(["docker", "inspect", "cd_nextcloud_bugs_eval"]).stdout)[0]
    ports = value["NetworkSettings"]["Ports"].get("80/tcp") or []
    mounts = [
        {"name": mount.get("Name"), "destination": mount.get("Destination")}
        for mount in value.get("Mounts", [])
    ]
    if not value["State"]["Running"]:
        raise RuntimeError("dedicated Nextcloud container is not running")
    if not any(row.get("HostIp") == "127.0.0.1" and row.get("HostPort") == "38080" for row in ports):
        raise RuntimeError("dedicated Nextcloud container is not loopback-bound")
    if {"name": "cd_nextcloud_bugs_eval_data", "destination": "/var/www/html"} not in mounts:
        raise RuntimeError("dedicated Nextcloud data volume differs")
    return {
        "container": "cd_nextcloud_bugs_eval",
        "running": True,
        "image_id": value["Image"],
        "loopback_port": "127.0.0.1:38080",
        "volume": "cd_nextcloud_bugs_eval_data:/var/www/html",
    }


def qualify(config_path: Path, case_id: str, output_path: Path) -> dict[str, Any]:
    if output_path.exists():
        raise FileExistsError("runtime qualification receipt already exists")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    case = next((row for row in config["cases"] if row["case_id"] == case_id), None)
    if case is None:
        raise ValueError(f"unknown case: {case_id}")
    execution = case["execution"]
    command = execution.get("reset_command")
    if not isinstance(command, list) or not command:
        raise RuntimeError("reset command is not frozen")
    reset_request = {
        "protocol_id": config["protocol_id"],
        "case": {"case_id": case_id},
        "attempt": {"evidence_dir": None, "oracle": {"case_id": case_id}},
    }
    reset = run(
        command,
        timeout=float(execution.get("reset_timeout_seconds", 600)),
        input_text=canonical_json(reset_request),
    )
    reset_receipt = last_json_object(reset.stdout)
    if reset_receipt.get("reset_verified") is not True:
        raise RuntimeError("reset hook did not verify the baseline")
    if reset_receipt.get("reset_id") != execution.get("reset_id"):
        raise RuntimeError("reset receipt ID differs")

    local_apk = resolve(config_path, execution["apk_path"])
    if not local_apk.is_file() or sha256_file(local_apk) != execution["apk_sha256"]:
        raise RuntimeError("local extracted APK differs")
    local_signer = signer_digest(local_apk)
    if local_signer != execution["apk_signer_sha256"]:
        raise RuntimeError("local extracted APK signer differs")
    installed = installed_receipt(execution)

    import uiautomator2 as u2

    device = u2.connect(execution["device_serial"])
    foreground = device.app_current()
    if foreground.get("package") != execution["package"]:
        raise RuntimeError(f"wrong foreground package after reset: {foreground}")
    field = device(resourceId=case["input"]["resource_id"])
    if not field.wait(timeout=10):
        raise RuntimeError("target field is absent after reset")

    assets = []
    for asset in execution.get("required_local_assets", []):
        path = resolve(config_path, asset["path"])
        actual = sha256_file(path) if path.is_file() else None
        if actual != asset["sha256"]:
            raise RuntimeError(f"required asset differs: {path}")
        assets.append({"path": str(path), "sha256": actual})
    snapshot = resolve(config_path, execution["snapshot_path"])
    snapshot_pb = snapshot / "snapshot.pb"
    if not snapshot_pb.is_file():
        raise RuntimeError("snapshot.pb is missing")

    receipt = {
        "schema_version": "spec-feasibility-runtime-qualification-1",
        "protocol_id": config["protocol_id"],
        "qualified_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_id": case_id,
        "no_model_call": True,
        "device_serial": execution["device_serial"],
        "snapshot_path": str(snapshot),
        "snapshot_pb_sha256": sha256_file(snapshot_pb),
        "reset_command": command,
        "reset_receipt": reset_receipt,
        "installed": installed,
        "local_apk_path": str(local_apk),
        "local_apk_sha256": sha256_file(local_apk),
        "local_apk_signer_sha256": local_signer,
        "foreground": foreground,
        "target_resource_id": case["input"]["resource_id"],
        "target_field_present": True,
        "required_assets": assets,
        "docker": docker_receipt(case_id),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description="Qualify one pilot runtime without a model call")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--case-id", required=True, choices=("B014", "B023"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    receipt = qualify(args.config.resolve(), args.case_id, output)
    print(json.dumps({
        "case_id": receipt["case_id"],
        "no_model_call": receipt["no_model_call"],
        "output": str(output),
        "sha256": sha256_file(output),
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
