"""Android adapter with time-ordered screenshots for E2-abcdef-v1.

Begin-attempt and within-attempt recovery retain the calibrated structured
contracts.  Execute-turn adds a frozen screenshot burst, optional screen
recording, deterministic UI deltas, and candidate-bound feedback artifacts.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from . import structured_android as base
from .abcdef_contract import canonical_json
from .abcdef_evidence import build_feedback_artifacts


_OBSERVATIONAL_INPUT_REJECTION_MARKERS = (
    "dynamic selector matched 0 nodes",
    "binding post-write check matched 0 nodes",
    "binding post-write value mismatch",
    "surrogates not allowed",
    # ``adb shell input text`` cannot represent a literal percent sign without
    # changing the candidate.  Bug App execution is observation-only, while
    # the independent string predicate still evaluates the original value.
    "adb_shell_input does not support percent characters",
    "adb_shell_input supports printable ASCII input only",
    # The selector-bound tap and clear commands have already succeeded before
    # this exact error.  A failing text command therefore means the candidate
    # cannot be represented by Android's shell input grammar; the broader
    # pre-write ``adb_shell_input command failed`` remains fail-closed.
    "adb_shell_input text command failed",
    # uiautomator2 may surface an App-owned field disappearing during
    # ``set_text`` directly as the Android UiObjectNotFound exception tuple,
    # rather than through its selector-wait JSON envelope.  This remains an
    # observation-only App rejection for Bug contracts; verdict Oracles and
    # every other RuntimeError still fail closed.
    "androidx.test.uiautomator.UiObjectNotFoundException",
)


def _is_observational_input_rejection(
    oracle: dict[str, Any], exc: Exception,
) -> bool:
    """Classify App-side write refusal only for Bug observation contracts."""
    message = str(exc)
    return (
        type(exc) is RuntimeError
        and oracle.get("oracle_role") == "feedback_observation_only"
        and (
            any(marker in message for marker in _OBSERVATIONAL_INPUT_REJECTION_MARKERS)
            or (
                # uiautomator2 reports a disappeared App-owned binding as a
                # JSON-RPC selector wait timeout.  For Bug cases the Android
                # path is observation-only, so this is an observed missing
                # field to capture, not a failure of the independent string
                # predicate verdict Oracle.
                "Selector [" in message
                and "'method': 'wait'" in message
            )
        )
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_png(path: Path) -> bool:
    """Reject partial captures before they can enter formal evidence."""
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        from PIL import Image

        with Image.open(path) as image:
            if image.format != "PNG":
                return False
            image.load()
    except (OSError, ValueError):
        return False
    return True


def _take_screenshot(driver: base.AndroidDriver, path: Path) -> None:
    if driver.serial == "fake":
        driver.device.screenshot(str(path))
        return
    adb = shutil.which("adb") or "adb"
    for attempt in range(3):
        with path.open("wb") as stream:
            completed = subprocess.run(
                [adb, "-s", driver.serial, "exec-out", "screencap", "-p"],
                stdout=stream,
                stderr=subprocess.PIPE,
                timeout=15,
                check=False,
            )
        if completed.returncode == 0 and _valid_png(path):
            return
        if attempt < 2:
            time.sleep(0.15)
    # uiautomator2 uses a different transport and can recover from a transient
    # empty adb framebuffer.  The smoke timing gate still rejects a late frame.
    driver.device.screenshot(str(path))
    if not _valid_png(path):
        raise RuntimeError("Android screenshot transport returned a truncated PNG")


class ScreenRecorder:
    def __init__(self, serial: str, directory: Path, duration_seconds: float) -> None:
        self.adb = shutil.which("adb") or "adb"
        self.serial = serial
        self.directory = directory
        self.duration_seconds = duration_seconds
        self.remote = "/sdcard/e2_abcdef_feedback.mp4"
        self.local = directory / "feedback_window.mp4"
        self.process: subprocess.Popen[str] | None = None
        self.started_monotonic: float | None = None
        self.video_zero_monotonic: float | None = None
        self.video_duration_seconds: float | None = None

    def start(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [self.adb, "-s", self.serial, "shell", "rm", "-f", self.remote],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
        limit = max(1, min(180, int(math.ceil(self.duration_seconds + 15))))
        spawned = time.monotonic()
        self.process = subprocess.Popen(
            [self.adb, "-s", self.serial, "shell", "screenrecord", "--time-limit", str(limit), self.remote],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.started_monotonic = spawned
        # Emulator screenrecord can accept the process launch before its first
        # encoded frame is ready.  Warm up the encoder before the protocol's
        # own pre-roll begins; otherwise a short run can finalize as N/A.
        time.sleep(0.75)
        if self.process.poll() is not None:
            raise RuntimeError("Android screen recording stopped during encoder warm-up")

    def stop(self) -> dict[str, Any] | None:
        if self.process is None:
            return None
        stop_signal_monotonic = time.monotonic()
        if self.process.poll() is None:
            pid = subprocess.run(
                [self.adb, "-s", self.serial, "shell", "pidof", "screenrecord"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=10,
                check=False,
            ).stdout.strip().split()
            if pid:
                subprocess.run(
                    [self.adb, "-s", self.serial, "shell", "kill", "-2", pid[0]],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        pull = subprocess.run(
            [self.adb, "-s", self.serial, "pull", self.remote, str(self.local)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            check=False,
        )
        subprocess.run(
            [self.adb, "-s", self.serial, "shell", "rm", "-f", self.remote],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
        if pull.returncode != 0 or not self.local.is_file() or self.local.stat().st_size == 0:
            raise RuntimeError("Android screen recording was not retrieved")
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            raise RuntimeError("ffprobe is required to validate Android screen recordings")
        probe = subprocess.run(
            [
                ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
                "-show_entries", "stream=codec_name,nb_read_frames,duration",
                "-of", "json", str(self.local),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        try:
            stream = json.loads(probe.stdout)["streams"][0]
            duration = float(stream.get("duration", 0.0))
            frame_count = int(stream["nb_read_frames"])
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Android screen recording is not a valid finalized MP4") from exc
        if probe.returncode != 0 or stream.get("codec_name") != "h264" or frame_count < 1 or duration < 0:
            raise RuntimeError("Android screen recording is not a valid finalized MP4")
        self.video_duration_seconds = duration
        self.video_zero_monotonic = stop_signal_monotonic - duration if duration > 0 else None
        return {
            "path": self.local.name,
            "sha256": _sha256(self.local),
            "kind": "screen_recording",
            "duration_seconds": duration,
            "frame_count": frame_count,
            "timeline_usable": duration > 0,
        }


def _capture_frame(
    driver: base.AndroidDriver,
    directory: Path,
    *,
    label: str,
    scheduled_seconds: float,
    action_started: float | None,
    request_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    xml_path = directory / f"{label}.xml"
    png_path = directory / f"{label}.png"
    # Capture pixels first.  Hierarchy collection is slower and otherwise
    # shifts the nominal t=0 screenshot far enough to miss a brief toast.
    _take_screenshot(driver, png_path)
    actual = None if action_started is None else time.monotonic() - action_started
    if not png_path.is_file() or png_path.stat().st_size == 0:
        raise RuntimeError("Android screenshot capture returned an empty file")
    state_hash, hierarchy, foreground = driver.state()
    xml_path.write_text(hierarchy, encoding="utf-8")
    frame = {
        "label": label,
        "scheduled_seconds": scheduled_seconds,
        "actual_seconds": -0.1 if actual is None else actual,
        "state_hash": state_hash,
        "xml_path": str(xml_path),
        "png_path": str(png_path),
        "foreground": foreground,
        "evidence_ref": f"request:{request_id}:{png_path.name}",
        "hierarchy_source": driver.hierarchy_source,
    }
    evidence = [
        {"path": xml_path.name, "sha256": _sha256(xml_path), "kind": "xml", "label": label},
        {"path": png_path.name, "sha256": _sha256(png_path), "kind": "screenshot", "label": label},
    ]
    return frame, evidence


def _capture_conditional_stop_frame(
    driver: base.AndroidDriver,
    directory: Path,
    *,
    request_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Capture a stopped prefix without another accessibility traversal.

    P094's App-owned login/onboarding loop can add dozens of window roots
    after the root-pressure guard has already stopped the action sequence.  A
    normal ``driver.state()`` call would re-enter the failing hierarchy dumper
    and turn a contained App rejection back into exit 137.  Preserve the
    pixels, foreground component, root count, and exact stop receipt instead.
    The XML is deliberately marked as a non-accessibility diagnostic receipt;
    it is never represented as a complete UI hierarchy.
    """
    directory.mkdir(parents=True, exist_ok=True)
    xml_path = directory / "pre_action.xml"
    png_path = directory / "pre_action.png"
    _take_screenshot(driver, png_path)
    if not png_path.is_file() or png_path.stat().st_size == 0:
        raise RuntimeError("Android screenshot capture returned an empty file")
    foreground = driver.current_app()
    stop = dict(driver.last_action_stop or {"reason": "conditional_prefix_stop"})
    package = stop.get("package") or foreground.get("package")
    if "view_root_count" not in stop and isinstance(package, str) and package:
        try:
            stop["view_root_count"] = driver.app_view_root_count(package)
        except RuntimeError as exc:
            stop["view_root_count_error"] = str(exc)
    root = ET.Element("hierarchy", {
        "source": "conditional_prefix_stop_receipt",
        "accessibility-complete": "false",
    })
    ET.SubElement(root, "diagnostic", {
        "type": "conditional_prefix_stop",
        "receipt-json": canonical_json(stop),
    })
    xml_path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    state_hash = hashlib.sha256(canonical_json({
        "foreground": {
            "package": foreground.get("package"),
            "activity": foreground.get("activity"),
        },
        "conditional_stop": stop,
    }).encode("utf-8")).hexdigest()
    frame = {
        "label": "pre_action",
        "scheduled_seconds": -0.1,
        "actual_seconds": -0.1,
        "state_hash": state_hash,
        "xml_path": str(xml_path),
        "png_path": str(png_path),
        "foreground": foreground,
        "evidence_ref": f"request:{request_id}:{png_path.name}",
        "hierarchy_source": "conditional_prefix_stop_receipt",
        "conditional_stop": stop,
    }
    evidence = [
        {"path": xml_path.name, "sha256": _sha256(xml_path), "kind": "xml", "label": "pre_action"},
        {"path": png_path.name, "sha256": _sha256(png_path), "kind": "screenshot", "label": "pre_action"},
    ]
    return frame, evidence


def _capture_pixel_frame(
    driver: base.AndroidDriver,
    directory: Path,
    *,
    label: str,
    scheduled_seconds: float,
    action_started: float,
    request_id: str,
    baseline: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Capture a timed screenshot without letting XML collection delay it."""
    png_path = directory / f"{label}.png"
    _take_screenshot(driver, png_path)
    actual = time.monotonic() - action_started
    if not png_path.is_file() or png_path.stat().st_size == 0:
        raise RuntimeError("Android screenshot capture returned an empty file")
    return ({
        "label": label,
        "scheduled_seconds": scheduled_seconds,
        "actual_seconds": actual,
        "state_hash": baseline["state_hash"],
        "xml_path": baseline["xml_path"],
        "png_path": str(png_path),
        "foreground": baseline["foreground"],
        "evidence_ref": f"request:{request_id}:{png_path.name}",
        "hierarchy_source": baseline.get("hierarchy_source", "live_uiautomator2"),
    }, {
        "path": png_path.name,
        "sha256": _sha256(png_path),
        "kind": "screenshot",
        "label": label,
    })


def _attach_settled_state(
    driver: base.AndroidDriver,
    directory: Path,
    frame: dict[str, Any],
) -> dict[str, Any]:
    """Attach one exact post-window hierarchy to the final timed screenshot."""
    state_hash, hierarchy, foreground = driver.state()
    xml_path = directory / "settled_state.xml"
    xml_path.write_text(hierarchy, encoding="utf-8")
    frame.update({
        "state_hash": state_hash,
        "xml_path": str(xml_path),
        "foreground": foreground,
    })
    return {
        "path": xml_path.name,
        "sha256": _sha256(xml_path),
        "kind": "xml",
        "label": "settled_state",
    }


def _replace_delayed_frames_from_video(
    *,
    recorder: ScreenRecorder,
    directory: Path,
    frames: list[dict[str, Any]],
    offsets: list[float],
    action_started: float,
    request_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Use exact video timestamps when live screenshots missed their slots."""
    delayed = any(
        float(row["actual_seconds"]) - float(row["scheduled_seconds"]) > 0.75
        for row in frames[1:]
    )
    if not delayed:
        return frames, []
    if recorder.video_zero_monotonic is None or not recorder.local.is_file():
        return frames, []
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return frames, []
    video_duration = recorder.video_duration_seconds
    if video_duration is None or video_duration <= 0:
        return frames, []
    action_video_offset = max(0.0, action_started - recorder.video_zero_monotonic)
    # Android's screenrecord output can be variable-frame-rate: when the UI is
    # static, its final encoded timestamp may precede the wall-clock end of the
    # observation window.  Preserve the requested observation time, but clamp
    # extraction to the final decodable frame and disclose that source time.
    # This recovers the settled visual state without claiming it was a fresh
    # screenshot taken at the requested wall-clock instant.
    # Container duration can extend well past the final frame PTS on Android's
    # variable-frame-rate recordings.  Query the actual frame timestamps and
    # seek just before the final PTS; duration-based estimates can otherwise
    # produce a successful ffmpeg process with no output image.
    timestamp_probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "frame=best_effort_timestamp_time",
            "-of", "json", str(recorder.local),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )
    try:
        frame_rows = json.loads(timestamp_probe.stdout)["frames"]
        frame_seconds = [
            float(row["best_effort_timestamp_time"])
            for row in frame_rows
            if row.get("best_effort_timestamp_time") is not None
        ]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return frames, []
    if timestamp_probe.returncode != 0 or not frame_seconds:
        return frames, []
    final_decodable_second = max(0.0, max(frame_seconds) - 0.05)
    baseline = frames[0]
    settled = frames[-1]
    extracted: list[dict[str, Any]] = [baseline]
    evidence: list[dict[str, Any]] = []
    for index, offset in enumerate(offsets):
        label = f"video_frame_{index:03d}"
        png_path = directory / f"{label}.png"
        requested_source_second = action_video_offset + float(offset)
        source_second = min(requested_source_second, final_decodable_second)
        completed = subprocess.run(
            [
                ffmpeg, "-loglevel", "error", "-y", "-i", str(recorder.local),
                "-ss", f"{source_second:.6f}",
                "-frames:v", "1", str(png_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0 or not _valid_png(png_path):
            return frames, []
        row = {
            "label": label,
            "scheduled_seconds": float(offset),
            "actual_seconds": float(offset),
            "state_hash": baseline["state_hash"],
            "xml_path": baseline["xml_path"],
            "png_path": str(png_path),
            "foreground": baseline["foreground"],
            "evidence_ref": f"request:{request_id}:{png_path.name}",
            "timing_source": "screen_recording",
            "alignment_uncertainty_seconds": 0.25,
            "video_source_seconds": source_second,
            "static_tail_frame_reused": requested_source_second > final_decodable_second,
            "hierarchy_source": baseline.get("hierarchy_source", "live_uiautomator2"),
        }
        extracted.append(row)
        evidence.append({
            "path": png_path.name,
            "sha256": _sha256(png_path),
            "kind": "screenshot",
            "label": label,
            "source": "screen_recording",
            "video_source_seconds": source_second,
            "static_tail_frame_reused": requested_source_second > final_decodable_second,
        })
    extracted[-1].update({
        "state_hash": settled["state_hash"],
        "xml_path": settled["xml_path"],
        "foreground": settled["foreground"],
    })
    return extracted, evidence


def _capture_contract(request: dict[str, Any]) -> dict[str, Any]:
    contract = request.get("capture_protocol")
    if not isinstance(contract, dict):
        raise ValueError("E2-abcdef-v1 execute_turn requires capture_protocol")
    burst = contract.get("burst_seconds")
    if (
        not isinstance(burst, list)
        or burst != [0.0, 0.25, 0.5, 1.0, 2.0]
        or contract.get("tail_interval_seconds") != 1.0
        or contract.get("pre_roll_seconds") != 0.5
        or contract.get("record_video") is not True
    ):
        raise ValueError("capture protocol differs from the frozen A-F timing contract")
    return contract


def _split_observation_trigger(
    actions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    """Split setup actions from the final App trigger and delay-only tail.

    Calibration plans often contain ``press/wait/click/wait``.  Capturing only
    after the entire list misses a short toast emitted by the final click.  The
    prefix is therefore executed first, the final non-wait action is treated as
    the trigger, and trailing waits are represented by the frozen observation
    window itself.  Wait-only plans begin observing immediately after input.
    """
    non_wait = [index for index, action in enumerate(actions) if action.get("action") != "wait"]
    if not non_wait:
        return [], None, list(actions)
    trigger_index = non_wait[-1]
    trailing = list(actions[trigger_index + 1:])
    if any(action.get("action") != "wait" for action in trailing):
        raise ValueError("observation trigger must be the final non-wait action")
    return list(actions[:trigger_index]), dict(actions[trigger_index]), trailing


def _enforce_trigger_reached(
    verdict: str,
    complete: bool,
    trigger_reached: bool,
    oracle_role: str | None,
) -> tuple[str, bool]:
    """Prevent a coarse page goal from passing after a stopped trigger."""
    if trigger_reached is False and oracle_role != "feedback_observation_only":
        return "fail", True
    return verdict, complete


def execute_turn(payload: dict[str, Any], *, device: Any | None = None) -> dict[str, Any]:
    candidate = payload["candidate"]
    request = payload["request"]
    oracle = request["oracle"]
    contract = _capture_contract(request)
    evidence_root = Path(request["trajectory_evidence_root"])
    evidence_dir = Path(request["evidence_dir"])
    evidence_dir.mkdir(parents=True, exist_ok=True)
    serial = base._device_serial(request["case"], oracle)
    hierarchy_transport = oracle["execute_turn"].get("hierarchy_transport")
    frozen_hierarchy = None
    if hierarchy_transport == "frozen_initial_xml":
        frozen_path = Path(oracle["source_evidence"]["initial_xml"])
        frozen_hierarchy = frozen_path.read_text(encoding="utf-8")
    driver = base.AndroidDriver(
        serial,
        device=device,
        prefer_adb_hierarchy=(hierarchy_transport == "adb"),
        frozen_hierarchy=frozen_hierarchy,
    )
    try:
        driver.fill(
            oracle["binding"],
            candidate,
            oracle["execute_turn"].get("input_strategies"),
        )
    except (base.AppInputRejected, RuntimeError) as exc:
        if type(exc) is RuntimeError and not _is_observational_input_rejection(oracle, exc):
            raise
        # A widget-level rejection is a real App observation, not a reason to
        # omit D/E/F evidence.  Capture the same frozen timeline without a
        # submit action so later feedback can see the rejection state while
        # the verdict still follows the calibrated input-rejection contract.
        recorder = ScreenRecorder(
            serial,
            evidence_dir,
            float(oracle["execute_turn"]["observation_seconds"]),
        )
        frames: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        try:
            recorder.start()
            time.sleep(max(0.0, float(contract["pre_roll_seconds"]) - 0.1))
            frame, items = _capture_frame(
                driver,
                evidence_dir,
                label="pre_action",
                scheduled_seconds=-0.1,
                action_started=None,
                request_id=request["request_id"],
            )
            frames.append(frame)
            evidence.extend(items)
            action_started = time.monotonic()
            offsets = list(contract["burst_seconds"])
            tail = 2.0 + float(contract["tail_interval_seconds"])
            observation_seconds = float(oracle["execute_turn"]["observation_seconds"])
            while tail <= observation_seconds + 1e-9:
                offsets.append(tail)
                tail += float(contract["tail_interval_seconds"])
            for index, offset in enumerate(offsets):
                remaining = action_started + float(offset) - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                frame, item = _capture_pixel_frame(
                    driver,
                    evidence_dir,
                    label=f"frame_{index:03d}",
                    scheduled_seconds=float(offset),
                    action_started=action_started,
                    request_id=request["request_id"],
                    baseline=frames[0],
                )
                frames.append(frame)
                evidence.append(item)
            evidence.append(_attach_settled_state(driver, evidence_dir, frames[-1]))
            video = recorder.stop()
            if video is not None:
                evidence.append(video)
            frames, extracted = _replace_delayed_frames_from_video(
                recorder=recorder,
                directory=evidence_dir,
                frames=frames,
                offsets=[float(value) for value in offsets],
                action_started=action_started,
                request_id=request["request_id"],
            )
            evidence.extend(extracted)
        except Exception:
            try:
                recorder.stop()
            except Exception:
                pass
            raise
        visual_feedback = build_feedback_artifacts(
            evidence_root=evidence_root,
            request_dir=evidence_dir,
            request_id=request["request_id"],
            candidate=candidate,
            oracle_version=oracle["version"],
            binding=oracle["binding"],
            frames=frames,
        )
        composite = evidence_root / visual_feedback["composite_path"]
        observations = evidence_root / visual_feedback["observation_path"]
        evidence.extend([
            {"path": composite.name, "sha256": _sha256(composite), "kind": "feedback_composite"},
            {"path": observations.name, "sha256": _sha256(observations), "kind": "ui_observations"},
        ])
        if oracle.get("oracle_role") == "feedback_observation_only":
            # Bug App runs collect public UI evidence only.  A widget rejecting
            # the frozen control candidate is itself a valid observation; it
            # must neither become an infrastructure failure nor replace the
            # independent string-predicate Bug verdict.
            verdict = "unknown"
        else:
            verdict = (
                "fail"
                if oracle["execute_turn"].get("input_rejection_verdict") == "fail"
                else "infrastructure_error"
            )
        return {
            "verdict": verdict,
            "observation_complete": verdict in {"fail", "unknown"},
            "state_before_hash": frames[0]["state_hash"],
            "state_after_hash": frames[-1]["state_hash"],
            "evidence": evidence,
            "evidence_hash": hashlib.sha256(canonical_json(evidence).encode("utf-8")).hexdigest(),
            "foreground": frames[-1]["foreground"],
            "diagnostics": [{
                "type": "app_input_rejected",
                "detail": "The frozen App interaction path did not accept the candidate value.",
                "public": True,
                "source": "ui_observation",
                "checker_id": "app_input_rejected",
                "evidence_ref": f"request:{request['request_id']}:{Path(frames[-1]['xml_path']).name}",
            }],
            "error_type": type(exc).__name__,
            "visual_feedback": visual_feedback,
        }

    actions = oracle["execute_turn"]["verify_action"]
    prefix_actions, trigger_action, trailing_waits = _split_observation_trigger(actions)
    trigger_reached = True
    if prefix_actions:
        trigger_reached = driver.actions(prefix_actions)
    if trigger_reached is False:
        # A prefix guard has already observed a terminal condition.  Do not
        # start screenrecord warm-up or re-read accessibility state: on P094
        # the App can keep allocating window roots after the guard trips, and
        # that otherwise turns the contained stop into a native dump exit 137.
        frame, evidence = _capture_conditional_stop_frame(
            driver,
            evidence_dir,
            request_id=request["request_id"],
        )
        frames = [frame]
        visual_feedback = build_feedback_artifacts(
            evidence_root=evidence_root,
            request_dir=evidence_dir,
            request_id=request["request_id"],
            candidate=candidate,
            oracle_version=oracle["version"],
            binding=oracle["binding"],
            frames=frames,
        )
        composite = evidence_root / visual_feedback["composite_path"]
        observations = evidence_root / visual_feedback["observation_path"]
        evidence.extend([
            {"path": composite.name, "sha256": _sha256(composite), "kind": "feedback_composite"},
            {"path": observations.name, "sha256": _sha256(observations), "kind": "ui_observations"},
        ])
        stop = dict(driver.last_action_stop or {"reason": "conditional_prefix_stop"})
        return {
            "verdict": (
                "unknown"
                if oracle.get("oracle_role") == "feedback_observation_only"
                else "fail"
            ),
            "observation_complete": True,
            "state_before_hash": frame["state_hash"],
            "state_after_hash": frame["state_hash"],
            "evidence": evidence,
            "evidence_hash": hashlib.sha256(
                canonical_json(evidence).encode("utf-8")
            ).hexdigest(),
            "foreground": frame["foreground"],
            "observation_trigger": {
                "prefix_action_count": len(prefix_actions),
                "trigger_action": (
                    trigger_action.get("action")
                    if trigger_action else "automatic_after_input"
                ),
                "trigger_reached": False,
                "trailing_wait_seconds": sum(
                    float(action["seconds"]) for action in trailing_waits
                ),
            },
            "diagnostics": [{
                "type": "conditional_prefix_stop",
                "detail": "The App reached a fail-closed prefix guard before the calibrated trigger.",
                "public": True,
                "source": "root_pressure_or_terminal_selector_guard",
                "checker_id": "conditional_prefix_stop",
                "evidence_ref": f"request:{request['request_id']}:pre_action.xml",
                "receipt": stop,
            }],
            "visual_feedback": visual_feedback,
        }

    recorder = ScreenRecorder(
        serial,
        evidence_dir,
        float(oracle["execute_turn"]["observation_seconds"]),
    )
    frames: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    try:
        recorder.start()
        time.sleep(max(0.0, float(contract["pre_roll_seconds"]) - 0.1))
        frame, items = _capture_frame(
            driver,
            evidence_dir,
            label="pre_action",
            scheduled_seconds=-0.1,
            action_started=None,
            request_id=request["request_id"],
        )
        frames.append(frame)
        evidence.extend(items)
        if trigger_reached and trigger_action is not None:
            trigger_reached = driver.actions(
                [trigger_action],
                observed_hierarchy=(
                    Path(frame["xml_path"]).read_text(encoding="utf-8")
                    if trigger_action.get("action")
                    == "click_from_observed_hierarchy_unless_view_roots_exceed"
                    else None
                ),
            )
        if trigger_reached is False:
            # ``if_missing=stop`` is already a complete semantic observation.
            # The pre-action hierarchy is the exact evidence that the
            # conditionally available target did not exist.  Do not wait for
            # the full capture window or request a settled hierarchy: unstable
            # Compose surfaces such as P094 can retain a new App window on each
            # later accessibility traversal even when no further action runs.
            passed = False
            video = recorder.stop()
            if video is not None:
                evidence.append(video)
        else:
            # Start the observation clock immediately after the final effective
            # trigger.  A calibrated trailing wait is replaced by this same frozen
            # observation window so transient App feedback remains capturable.
            action_started = time.monotonic()
            observation_seconds = float(oracle["execute_turn"]["observation_seconds"])
            offsets = list(contract["burst_seconds"])
            tail = 2.0 + float(contract["tail_interval_seconds"])
            while tail <= observation_seconds + 1e-9:
                offsets.append(tail)
                tail += float(contract["tail_interval_seconds"])
            for index, offset in enumerate(offsets):
                remaining = action_started + float(offset) - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                frame, item = _capture_pixel_frame(
                    driver,
                    evidence_dir,
                    label=f"frame_{index:03d}",
                    scheduled_seconds=float(offset),
                    action_started=action_started,
                    request_id=request["request_id"],
                    baseline=frames[0],
                )
                frames.append(frame)
                evidence.append(item)
            evidence.append(_attach_settled_state(driver, evidence_dir, frames[-1]))
            # Assertion queries can take hundreds of milliseconds and must not
            # distort the 0/.25/.5/1/2-second screenshot burst.  Evaluate the
            # persistent goal once after the frozen capture window.
            passed = (
                driver.assertion(oracle["goal_assertion"])
                and (
                    not oracle["execute_turn"].get("candidate_binding_goal", False)
                    or driver.candidate_bindings_match(oracle["binding"], candidate)
                )
                and (
                    not oracle["execute_turn"].get("candidate_text_goal", False)
                    or driver.candidate_texts_visible(oracle["aut"], candidate)
                )
            )
            video = recorder.stop()
            if video is not None:
                evidence.append(video)
            frames, extracted = _replace_delayed_frames_from_video(
                recorder=recorder,
                directory=evidence_dir,
                frames=frames,
                offsets=[float(value) for value in offsets],
                action_started=action_started,
                request_id=request["request_id"],
            )
            evidence.extend(extracted)
    except Exception:
        try:
            recorder.stop()
        except Exception:
            pass
        raise

    visual_feedback = build_feedback_artifacts(
        evidence_root=evidence_root,
        request_dir=evidence_dir,
        request_id=request["request_id"],
        candidate=candidate,
        oracle_version=oracle["version"],
        binding=oracle["binding"],
        frames=frames,
    )
    composite = evidence_root / visual_feedback["composite_path"]
    observations = evidence_root / visual_feedback["observation_path"]
    evidence.extend([
        {"path": composite.name, "sha256": _sha256(composite), "kind": "feedback_composite"},
        {"path": observations.name, "sha256": _sha256(observations), "kind": "ui_observations"},
    ])
    diagnostics = base._ui_diagnostics(
        Path(frames[-1]["xml_path"]).read_text(encoding="utf-8"),
        oracle.get("diagnostic_targets", []),
        request["request_id"],
        Path(frames[-1]["xml_path"]).name,
    )
    observer_command = oracle["execute_turn"].get("observer_command")
    if observer_command:
        observed = base._run_contract_command(
            observer_command,
            {**payload, "captured_evidence": evidence, "ui_goal_assertion": passed},
            float(request.get("command_timeout_seconds", 600)),
        )
        verdict = observed.get("verdict")
        complete = observed.get("observation_complete")
        if verdict not in {"pass", "fail", "unknown", "infrastructure_error"} or not isinstance(complete, bool):
            raise RuntimeError("observer command returned an invalid verdict")
        diagnostics.extend(observed.get("diagnostics", []))
    else:
        verdict, complete = ("pass" if passed else "fail"), True
    verdict, complete = _enforce_trigger_reached(
        verdict,
        complete,
        trigger_reached,
        oracle.get("oracle_role"),
    )
    if trigger_reached is False and oracle.get("oracle_role") != "feedback_observation_only":
        diagnostics.append({
            "type": "conditional_action_target_missing",
            "detail": "The App did not expose the conditionally available action target.",
            "public": True,
            "source": "ui_observation",
            "checker_id": "conditional_action_target_missing",
            "evidence_ref": f"request:{request['request_id']}:{Path(frames[-1]['xml_path']).name}",
        })
    if verdict in {"unknown", "infrastructure_error"}:
        complete = False
    return {
        "verdict": verdict,
        "observation_complete": complete,
        "state_before_hash": frames[0]["state_hash"],
        "state_after_hash": frames[-1]["state_hash"],
        "evidence": evidence,
        "evidence_hash": hashlib.sha256(canonical_json(evidence).encode("utf-8")).hexdigest(),
        "foreground": frames[-1]["foreground"],
        "observation_trigger": {
            "prefix_action_count": len(prefix_actions),
            "trigger_action": trigger_action.get("action") if trigger_action else "automatic_after_input",
            "trigger_reached": trigger_reached,
            "trailing_wait_seconds": sum(float(action["seconds"]) for action in trailing_waits),
        },
        "diagnostics": diagnostics,
        "visual_feedback": visual_feedback,
    }


def recover_turn(payload: dict[str, Any]) -> dict[str, Any]:
    """Prefer natural recovery, then fail over to the frozen local snapshot.

    Some calibrated negative paths navigate to a parent or system screen from
    which Back cannot reconstruct the exact input page.  Reloading the already
    frozen baseline is safer than inventing case-specific navigation and keeps
    every subsequent candidate independent.  The receipt makes the fallback
    explicit; it never changes the verdict produced by ``execute_turn``.
    """
    try:
        receipt = base.recover_turn(payload)
        return {**receipt, "recovery_mode": "natural"}
    except Exception as exc:
        if isinstance(exc, (KeyError, TypeError, ValueError)):
            raise
        execution = payload["execution"]
        fallback_dir = Path(execution["evidence_dir"]) / "snapshot_fallback"
        begin_receipt = base.begin_attempt({
            "case": execution["case"],
            "attempt": {
                "oracle": execution["oracle"],
                "evidence_dir": str(fallback_dir),
                "command_timeout_seconds": execution.get("command_timeout_seconds", 600),
            },
        })
        return {
            "snapshot_loaded": True,
            "remote_reset": False,
            "renavigated_from_baseline": False,
            "natural_state_preserved": False,
            "state_before_hash": execution.get("state_after_hash"),
            "target_page_verified": begin_receipt.get("target_page_verified"),
            "input_cleared": begin_receipt.get("target_page_verified"),
            "state_hash": begin_receipt.get("state_hash"),
            "locator_refresh_hash": hashlib.sha256(
                canonical_json(execution["oracle"]["binding"]).encode("utf-8")
            ).hexdigest(),
            "evidence": begin_receipt.get("evidence", []),
            "foreground": begin_receipt.get("foreground"),
            "recovery_mode": "snapshot_fallback",
            "natural_recovery_error_type": type(exc).__name__,
            "natural_recovery_error": str(exc) or repr(exc),
        }


def begin_attempt(payload: dict[str, Any]) -> dict[str, Any]:
    """Retry transient UiAutomation startup after a verified snapshot load."""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            receipt = base.begin_attempt(payload)
            return {**receipt, "begin_attempt_retry_count": attempt}
        except Exception as exc:
            if isinstance(exc, (KeyError, TypeError, ValueError)):
                raise
            last_error = exc
            if attempt < 2:
                time.sleep(2.0 * (attempt + 1))
    assert last_error is not None
    raise last_error


def dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    operation = payload.get("operation")
    if operation == "begin_attempt":
        return begin_attempt(payload)
    if operation == "execute_turn":
        return execute_turn(payload)
    if operation == "recover_turn":
        return recover_turn(payload)
    raise ValueError(f"unknown operation: {operation}")


def main() -> None:
    try:
        result = dispatch(json.load(sys.stdin))
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        sys.stderr.write(json.dumps({"error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
