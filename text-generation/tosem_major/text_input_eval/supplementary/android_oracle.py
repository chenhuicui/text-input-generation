"""JSON stdin/stdout behavior adapter using the repository's Android actions.

This adapter supports UI-defined endpoints for a single installed APK. Crash/log
or split-APK bug oracles need their own evidence adapter, not a guessed UI rule.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET

from .context import ROOT, digest, encoded


def observe(xml: str, assertion: dict) -> tuple[bool, list[dict]]:
    mode, targets = assertion.get("mode"), assertion.get("targets")
    if mode not in ("all", "any") or not targets:
        raise ValueError("An assertion requires nonempty targets and all/any mode")
    tree = ET.fromstring(xml)
    details = []
    for target in targets:
        selector = target["selector"]
        if not selector or set(selector) - {"resource-id", "text", "content-desc", "class", "index", "package"}:
            raise ValueError("Unsupported or empty assertion selector")
        if type(target.get("exists")) is not bool:
            raise ValueError("Each target requires boolean exists")
        count = sum(all(n.get(k) == v for k, v in selector.items()) for n in tree.iter("node"))
        details.append({"selector": selector, "count": count, "expected_exists": target["exists"],
                        "passed": (count > 0) == target["exists"]})
    return (all(d["passed"] for d in details) if mode == "all" else any(d["passed"] for d in details)), details


def execute(request: dict) -> dict:
    sys.path.insert(0, str(ROOT))
    from src.apk_management.launcher import AppLauncher
    from src.test_execution.action_executor import ActionExecutor

    oracle, candidate = request["oracle"], request["candidate"]
    output = Path(request["artifact_dir"])
    output.mkdir(parents=True, exist_ok=True)
    result = {"request_id": request["request_id"], "candidate_hash": digest(candidate),
              "oracle_version": oracle["version"], "run_status": "execution_error",
              "reset_verified": False, "behavior_verdict": None, "evidence": [], "diagnostics": []}
    if request["candidate_hash"] != digest(candidate):
        raise ValueError("Candidate hash mismatch")
    apk = Path(oracle["apk_path"])
    if hashlib.sha256(apk.read_bytes()).hexdigest() != oracle["apk_sha256"]:
        raise ValueError("Local APK hash mismatch")
    # Validate definitions before executing any reset or device action.
    for key in ("initial_assertion", "goal_assertion"):
        observe("<hierarchy />", oracle[key])
    if not oracle.get("reset_command") or not oracle.get("verify_action"):
        raise ValueError("Explicit reset command and submission actions are required")
    if not (0 < oracle["observation_seconds"] <= 120 and 0 < oracle["poll_seconds"] <= 5):
        raise ValueError("Invalid observation window")
    try:
        reset = subprocess.run(oracle["reset_command"], capture_output=True, text=True,
                               timeout=oracle.get("reset_timeout", 90), check=True)
        (output / "reset.stdout.txt").write_text(reset.stdout)
        (output / "reset.stderr.txt").write_text(reset.stderr)
        receipt = json.loads(reset.stdout)
        if receipt.get("reset_verified") is not True or receipt.get("reset_id") != oracle["reset_id"]:
            raise ValueError("Missing matching state-reset receipt")
        adb = [oracle.get("adb", "adb"), "-s", oracle["device_id"]]
        installed = subprocess.run(adb + ["shell", "pm", "path", oracle["aut"]],
                                   capture_output=True, text=True, timeout=20, check=True).stdout.strip().splitlines()
        if len(installed) != 1 or not installed[0].startswith("package:"):
            raise ValueError("This adapter requires a single installed APK")
        remote_path = installed[0][len("package:"):]
        remote = subprocess.run(adb + ["shell", "sha256sum", remote_path],
                                capture_output=True, text=True, timeout=30, check=True).stdout.split()[0]
        if remote != oracle["apk_sha256"]:
            raise ValueError("Installed APK hash mismatch")
        launcher = AppLauncher(oracle["device_id"])
        if not launcher.launch_app(oracle["aut"]):
            raise RuntimeError("App launch failed")
        if not launcher.navigate_to_target_page(oracle["navigation_steps"]):
            raise RuntimeError("Navigation failed")
        device = launcher.device
        initial = device.dump_hierarchy()
        (output / "before.xml").write_text(initial)
        device.screenshot(str(output / "before.png"))
        if not observe(initial, oracle["initial_assertion"])[0]:
            raise RuntimeError("Initial state assertion failed")
        if observe(initial, oracle["goal_assertion"])[0]:
            raise RuntimeError("Goal already holds before input; this UI oracle cannot establish task completion")
        result["reset_verified"] = True
        # Map canonical schema keys to declared selectors supported by the old
        # executor (resource-id, or its explicit ::#::index disambiguation).
        bindings = oracle["bindings"]
        if set(bindings) != set(candidate) or len(set(bindings.values())) != len(bindings):
            raise ValueError("Binding schema mismatch or multiple values for the same control")
        nodes = list(ET.fromstring(initial).iter("node"))
        for binding in bindings.values():
            parts = binding.split("::#::", 1)
            matches = [n for n in nodes if n.get("resource-id") == parts[0]]
            if len(parts) == 2:
                index = int(parts[1])
                if index < 0 or index >= len(matches):
                    raise ValueError("Binding occurrence index out of range")
                matches = [matches[index]]
            if len(matches) != 1:
                raise ValueError("Binding does not identify exactly one node")
        executor = ActionExecutor(device)
        if not executor.fill_text_inputs({bindings[k]: v for k, v in candidate.items()}):
            raise RuntimeError("Text input failed")
        (output / "filled.xml").write_text(device.dump_hierarchy())
        if not executor.execute_actions(oracle["verify_action"]):
            raise RuntimeError("Submission action failed")
        observations = []
        start = time.monotonic()
        while True:
            xml = device.dump_hierarchy()  # Transport failures must not mean absence.
            passed, details = observe(xml, oracle["goal_assertion"])
            observations.append({"elapsed_seconds": time.monotonic() - start, "passed": passed, "details": details})
            if passed or time.monotonic() - start >= oracle["observation_seconds"]:
                break
            time.sleep(oracle["poll_seconds"])
        (output / "after.xml").write_text(xml)
        device.screenshot(str(output / "after.png"))
        (output / "assertions.json").write_text(encoded(observations))
        logcat = subprocess.run(adb + ["logcat", "-d", "-t", "1000"], capture_output=True,
                                text=True, timeout=20, check=True)
        (output / "logcat.txt").write_text(logcat.stdout or "No logcat lines returned.\n")
        result.update(run_status="ok", behavior_verdict=passed)
        for target in oracle.get("public_diagnostic_targets", []):
            selector = target["selector"]
            if not selector or set(selector) - {"resource-id", "class", "index"}:
                raise ValueError("Diagnostics require an explicit UI selector")
            for node in ET.fromstring(xml).iter("node"):
                if all(node.get(k) == v for k, v in selector.items()) and node.get("text", "").strip():
                    result["diagnostics"].append({"type": "observed_ui_message", "detail": node.get("text"),
                        "source": "ui_observation", "public": True, "checker_id": target["id"],
                        "evidence_ref": f"request:{request['request_id']}:after.xml"})
    except Exception as exc:
        result.update(run_status="execution_error", error_type=type(exc).__name__, behavior_verdict=None, diagnostics=[])
    finally:
        result["evidence"] = [{"path": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                              for p in sorted(output.iterdir()) if p.is_file() and p.stat().st_size]
    return result


def main():
    request = json.load(sys.stdin)
    # The old launcher/action utilities log to stdout; keep protocol stdout pure.
    with contextlib.redirect_stdout(sys.stderr):
        try:
            result = execute(request)
        except Exception as exc:
            result = {"run_status": "execution_error", "error_type": type(exc).__name__}
    print(encoded(result))


if __name__ == "__main__":
    main()
