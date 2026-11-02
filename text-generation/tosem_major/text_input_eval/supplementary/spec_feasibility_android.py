"""Case-bound Android execution adapter for the specification pilot.

The adapter accepts one JSON request on stdin and emits one JSON object on
stdout.  It is intentionally separate from the input partition: this module
observes the App and returns O_spec in PASS/FAIL/UNKNOWN, while the pure
contract module classifies whether the generated candidate targets the
source-defined condition.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .spec_feasibility_contract import canonical_json, sha256_json
from .structured_android import AndroidDriver, binding_nodes, logical_binding_value


def _run(
    argv: list[str],
    *,
    timeout: float,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        argv,
        input=input_text,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-2000:]
        raise RuntimeError(f"command failed ({argv[0]}): {detail}")
    return completed


def _last_json_object(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError("reset command did not emit a JSON receipt")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pid(serial: str, package: str) -> int | None:
    completed = _run(
        ["adb", "-s", serial, "shell", "pidof", package],
        timeout=20,
        check=False,
    )
    tokens = completed.stdout.strip().split() if completed.returncode == 0 else []
    return int(tokens[0]) if tokens and tokens[0].isdigit() else None


def _installed_receipt(serial: str, execution: dict[str, Any]) -> dict[str, Any]:
    package = execution["package"]
    paths = _run(
        ["adb", "-s", serial, "shell", "pm", "path", package],
        timeout=30,
    ).stdout.strip().splitlines()
    if len(paths) != 1 or not paths[0].startswith("package:"):
        raise RuntimeError("the pilot requires one hash-verifiable installed base APK")
    remote_path = paths[0].removeprefix("package:")
    actual_sha = _run(
        ["adb", "-s", serial, "shell", "sha256sum", remote_path],
        timeout=45,
    ).stdout.strip().split()[0]
    if actual_sha != execution["apk_sha256"]:
        raise RuntimeError("installed APK SHA-256 differs from the frozen artifact")
    dump = _run(
        ["adb", "-s", serial, "shell", "dumpsys", "package", package],
        timeout=45,
    ).stdout
    match = re.search(r"\bversionCode=(\d+)\b", dump)
    if match is None or match.group(1) != str(execution["version_code"]):
        raise RuntimeError("installed versionCode differs from the requirement contract")
    return {
        "package": package,
        "version_code": match.group(1),
        "remote_apk_path": remote_path,
        "remote_apk_sha256": actual_sha,
    }


def _capture(device: Any, directory: Path, stem: str) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    xml_path = directory / f"{stem}.xml"
    png_path = directory / f"{stem}.png"
    hierarchy = device.dump_hierarchy()
    xml_path.write_text(hierarchy, encoding="utf-8")
    device.screenshot(str(png_path))
    if not png_path.is_file() or png_path.stat().st_size == 0:
        raise RuntimeError("screenshot capture returned an empty file")
    foreground = device.app_current()
    return {
        "stem": stem,
        "xml_path": xml_path.name,
        "xml_sha256": _sha256_file(xml_path),
        "png_path": png_path.name,
        "png_sha256": _sha256_file(png_path),
        "foreground": {
            "package": foreground.get("package"),
            "activity": foreground.get("activity"),
            "pid": foreground.get("pid"),
        },
    }


def _crash_signature(logcat: str, package: str) -> bool:
    lower = logcat.lower()
    package_lower = package.lower()
    markers = (
        "fatal exception",
        "am_crash",
        "force finishing activity",
        "has just crashed",
        "process has died",
    )
    for marker in markers:
        offset = 0
        while True:
            index = lower.find(marker, offset)
            if index < 0:
                break
            # AndroidRuntime commonly prints the Process line shortly after
            # FATAL EXCEPTION.  Require the AUT package in one bounded event
            # neighborhood instead of elsewhere in the candidate-bound log.
            neighborhood = lower[max(0, index - 1000): index + 4000]
            if package_lower in neighborhood:
                return True
            offset = index + len(marker)
    return False


def adjudicate_observation(observation: dict[str, Any]) -> dict[str, Any]:
    """Apply the no-crash local requirement without guessing broader behavior."""
    package = observation["package"]
    initial_pid = observation.get("initial_pid")
    final_pid = observation.get("final_pid")
    foreground_package = observation.get("foreground_package")
    crash_evidence = observation.get("crash_evidence") is True
    action_completed = observation.get("action_completed") is True
    capture_complete = observation.get("capture_complete") is True

    if crash_evidence:
        return {
            "o_spec": "FAIL",
            "unknown_reason": None,
            "public_observations": [
                "A candidate-bound process crash was observed after the frozen action."
            ],
        }
    if not action_completed:
        return {
            "o_spec": "UNKNOWN",
            "unknown_reason": "frozen action did not complete",
            "public_observations": ["The frozen action could not be completed."],
        }
    if initial_pid is None:
        return {
            "o_spec": "UNKNOWN",
            "unknown_reason": "initial AUT process identity was unavailable",
            "public_observations": ["The initial App process identity was unavailable."],
        }
    if final_pid is None or final_pid != initial_pid:
        return {
            "o_spec": "UNKNOWN",
            "unknown_reason": "AUT process disappeared or changed without a corroborating crash signature",
            "public_observations": [
                "The App process disappeared or changed, but the collected evidence did not establish a crash."
            ],
        }
    if not capture_complete:
        return {
            "o_spec": "UNKNOWN",
            "unknown_reason": "post-action observation was incomplete",
            "public_observations": ["The post-action observation was incomplete."],
        }
    if foreground_package != package:
        return {
            "o_spec": "UNKNOWN",
            "unknown_reason": "foreground ownership was ambiguous after the action",
            "public_observations": ["The App was not verifiably foreground after the action."],
        }
    return {
        "o_spec": "PASS",
        "unknown_reason": None,
        "public_observations": [
            "The frozen action completed and the same App process remained alive and foreground during the observation window."
        ],
    }


def _reset(case: dict[str, Any], artifact_dir: Path) -> dict[str, Any]:
    execution = case["execution"]
    command = execution["reset_command"]
    reset_payload = {
        "protocol_id": "reviewer2-spec-feasibility-pilot-v1",
        "case": {"case_id": case["case_id"]},
        "attempt": {
            "evidence_dir": str(artifact_dir),
            "oracle": {"case_id": case["case_id"]},
        },
    }
    completed = _run(
        command,
        timeout=float(execution.get("reset_timeout_seconds", 600)),
        input_text=canonical_json(reset_payload),
    )
    (artifact_dir / "reset.stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (artifact_dir / "reset.stderr.txt").write_text(completed.stderr, encoding="utf-8")
    receipt = _last_json_object(completed.stdout)
    if receipt.get("case_id") not in {None, case["case_id"]}:
        raise RuntimeError("reset receipt case identity differs")
    if receipt.get("reset_verified") is not True:
        raise RuntimeError("reset receipt did not verify the baseline")
    expected = execution.get("reset_id")
    if expected and receipt.get("reset_id") != expected:
        raise RuntimeError("reset receipt ID differs from the frozen contract")
    return receipt


def execute_attempt(request: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    case = request["case"]
    candidate = request["candidate"]
    execution = case["execution"]
    artifact_dir = Path(request["artifact_dir"]).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=False)
    serial = execution["device_serial"]
    package = execution["package"]
    result: dict[str, Any] = {
        "schema_version": "spec-feasibility-android-attempt-1",
        "case_id": case["case_id"],
        "turn": request["turn"],
        "candidate_sha256": sha256_json(candidate),
        "run_status": "infrastructure_error",
        "o_spec": "UNKNOWN",
        "unknown_reason": "adapter did not complete",
        "public_observations": [],
    }
    try:
        reset_receipt = _reset(case, artifact_dir)
        installed_receipt = _installed_receipt(serial, execution)
        import uiautomator2 as u2

        device = u2.connect(serial)
        if device.app_current().get("package") != package:
            raise RuntimeError("reset did not leave the expected App foreground")
        action = execution["action"]
        structured = action["kind"] == "structured_actions"
        driver = AndroidDriver(serial, device=device) if structured else None
        if structured:
            for selector in execution["bindings"].values():
                assert driver is not None
                driver.binding_object(selector)
        else:
            resource_id = case["input"]["resource_id"]
            field = device(resourceId=resource_id)
            if not field.wait(timeout=10):
                raise RuntimeError("the frozen input field is absent after reset")
        if action["kind"] == "click_resource_id":
            action_target = device(resourceId=action["resource_id"])
            if not action_target.wait(timeout=10):
                raise RuntimeError("the frozen action target is absent after reset")

        before = _capture(device, artifact_dir, "before")
        initial_pid = _pid(serial, package)
        _run(["adb", "-s", serial, "logcat", "-c"], timeout=30)
        interaction_error: Exception | None = None
        after_input: dict[str, Any] | None = None
        observed_binding_values: dict[str, str] | None = None
        candidate_bindings_match: bool | None = None
        action_completed = False
        capture_complete = True
        try:
            if structured:
                assert driver is not None
                driver.fill(
                    execution["bindings"],
                    candidate,
                    execution.get("input_strategies"),
                )
                observed_binding_values = {}
                root = ET.fromstring(driver.hierarchy())
                for candidate_key, selector in execution["bindings"].items():
                    nodes = binding_nodes(root, selector)
                    if len(nodes) != 1:
                        raise RuntimeError(
                            f"post-fill binding matched {len(nodes)} nodes: {candidate_key}"
                        )
                    observed_binding_values[candidate_key] = logical_binding_value(
                        nodes[0].get("text", ""), nodes[0].get("hint", ""),
                    )
                candidate_bindings_match = driver.candidate_bindings_match(
                    execution["bindings"], candidate,
                )
                action_completed = driver.actions(execution["verify_actions"])
            else:
                field.click()
                field.clear_text()
                field.set_text(candidate[resource_id])
            if action["kind"] == "settle_only":
                # For search-as-you-type, committing the field value is the
                # frozen action.  A later hierarchy failure may itself be the
                # effect under test, so record completion before capturing.
                action_completed = True
            try:
                after_input = _capture(device, artifact_dir, "after_input")
            except Exception as exc:
                capture_complete = False
                (artifact_dir / "after_input_capture_error.txt").write_text(
                    f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
                )
            if action["kind"] == "click_resource_id":
                device(resourceId=action["resource_id"]).click()
                action_completed = True
            elif action["kind"] not in {"settle_only", "structured_actions"}:
                raise ValueError("unsupported specification-pilot action")
        except Exception as exc:
            interaction_error = exc
            (artifact_dir / "interaction_error.txt").write_text(
                f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
            )

        observation_seconds = float(execution["observation_seconds"])
        if not 0 < observation_seconds <= 30:
            raise ValueError("observation window must be in (0, 30] seconds")
        time.sleep(observation_seconds)
        final_capture: dict[str, Any] | None = None
        try:
            final_capture = _capture(device, artifact_dir, "after")
        except Exception as exc:
            capture_complete = False
            (artifact_dir / "after_capture_error.txt").write_text(
                f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
            )
        final_pid = _pid(serial, package)
        try:
            foreground = device.app_current()
        except Exception as exc:
            foreground = {}
            capture_complete = False
            (artifact_dir / "foreground_error.txt").write_text(
                f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
            )
        logcat = _run(
            ["adb", "-s", serial, "logcat", "-d", "-v", "epoch"],
            timeout=45,
            check=False,
        ).stdout
        (artifact_dir / "logcat.txt").write_text(logcat, encoding="utf-8")
        observation = {
            "package": package,
            "initial_pid": initial_pid,
            "final_pid": final_pid,
            "foreground_package": foreground.get("package"),
            "crash_evidence": _crash_signature(logcat, package),
            "action_completed": action_completed,
            "capture_complete": capture_complete,
            "candidate_bindings_match_after_fill": candidate_bindings_match,
            "observed_binding_values_after_fill": observed_binding_values,
        }
        verdict = adjudicate_observation(observation)
        run_status = "ok"
        if interaction_error is not None and verdict["o_spec"] != "FAIL":
            run_status = "infrastructure_error"
            verdict = {
                "o_spec": "UNKNOWN",
                "unknown_reason": "input or action execution failed without corroborating crash evidence",
                "public_observations": [
                    "The input or action could not be completed, and the evidence did not establish a crash."
                ],
            }
        result.update(
            run_status=run_status,
            **verdict,
            reset_receipt=reset_receipt,
            installed_receipt=installed_receipt,
            observation=observation,
            captures=[item for item in (before, after_input, final_capture) if item is not None],
        )
        if interaction_error is not None:
            result["interaction_error_type"] = type(interaction_error).__name__
    except Exception as exc:
        error_path = artifact_dir / "adapter_error.txt"
        error_path.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        result.update(
            run_status="infrastructure_error",
            error_type=type(exc).__name__,
            error_message=str(exc)[:2000],
            o_spec="UNKNOWN",
            unknown_reason="execution infrastructure did not establish the requirement outcome",
            public_observations=["Execution infrastructure did not establish the requirement outcome."],
        )
    result["evidence"] = [
        {
            "path": path.name,
            "sha256": _sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(artifact_dir.iterdir())
        if path.is_file()
    ]
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result


def main() -> None:
    try:
        request = json.load(sys.stdin)
        result = execute_attempt(request)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        sys.stderr.write(json.dumps({
            "error_type": type(exc).__name__,
            "message": str(exc),
        }, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
