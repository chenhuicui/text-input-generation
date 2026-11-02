"""Run resumable per-case Android visual-capture smokes for E2-abcdef-v1."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from .abcdef_preflight import DEFAULT_CONFIG, REPO_ROOT, read_json, resolve_path, sha256_file
from .structured_cli import load_oracles
from .structured_environment import AndroidCommandEnvironment


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _fragment(path: Path, pointer: str) -> Any:
    value: Any = read_json(path)
    for item in pointer.strip("/").split("/") if pointer.strip("/") else []:
        value = value[int(item)] if isinstance(value, list) else value[item]
    return value


def _candidate(oracle_path: Path, oracle: dict[str, Any], arm: str) -> dict[str, str]:
    reference = oracle.get("calibration", {}).get(arm)
    if not isinstance(reference, str) or "#" not in reference:
        raise ValueError(f"{oracle['case_id']} lacks a frozen {arm} evidence reference")
    label, pointer = reference.split("#", 1)
    source = (oracle_path.parent / label).resolve()
    row = _fragment(source, pointer)
    candidate = row.get("candidate") if isinstance(row, dict) else None
    if not isinstance(candidate, dict) or set(candidate) != set(oracle["binding"]):
        raise ValueError(f"{oracle['case_id']} {arm} candidate differs from the binding")
    if any(not isinstance(value, str) for value in candidate.values()):
        raise ValueError(f"{oracle['case_id']} {arm} candidate contains non-string values")
    return candidate


def _evidence_ok(root: Path, visual: Any) -> bool:
    if not isinstance(visual, dict):
        return False
    resolved: dict[str, Path] = {}
    for key in ("composite_path", "observation_path"):
        label = visual.get(key)
        if not isinstance(label, str):
            return False
        path = (root / label).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size == 0:
            return False
        resolved[key] = path
    if sha256_file(resolved["composite_path"]) != visual.get("composite_sha256"):
        return False
    if sha256_file(resolved["observation_path"]) != visual.get("observation_sha256"):
        return False
    with Image.open(resolved["composite_path"]) as composite:
        if composite.width > 512 or visual.get("composite_max_width") != 512:
            return False
    frames = visual.get("frames")
    timeline = visual.get("capture_timeline")
    timeline_ok = (
        isinstance(timeline, list)
        and len(timeline) >= 7
        and timeline[0].get("label") == "pre_action"
        and len({row.get("label") for row in timeline}) == len(timeline)
        and len({row.get("evidence_ref") for row in timeline}) == len(timeline)
        and all(
            isinstance(row.get("actual_seconds"), (int, float))
            for row in timeline
        )
        and all(
            isinstance(row.get("capture_lag_seconds"), (int, float))
            and 0 <= float(row["capture_lag_seconds"]) <= 0.75
            for row in timeline[1:]
        )
        and all(
            float(left["actual_seconds"]) < float(right["actual_seconds"])
            for left, right in zip(timeline, timeline[1:])
        )
    )
    return timeline_ok and (
        isinstance(frames, list)
        and {row.get("role") for row in frames if isinstance(row, dict)}
        == {"before", "first_changed", "transient_message_peak", "settled"}
    )


def _recording_ok(evidence_dir: Path, execution: Any) -> bool:
    if not isinstance(execution, dict):
        return False
    rows = [
        row for row in execution.get("evidence", [])
        if isinstance(row, dict) and row.get("kind") == "screen_recording"
    ]
    if len(rows) != 1 or not isinstance(rows[0].get("path"), str):
        return False
    row = rows[0]
    path = (evidence_dir / row["path"]).resolve()
    if not path.is_relative_to(evidence_dir.resolve()) or not path.is_file():
        return False
    if sha256_file(path) != row.get("sha256"):
        return False
    recorded = row.get("duration_seconds")
    recorded_frames = row.get("frame_count")
    ffprobe = shutil.which("ffprobe")
    if (
        not isinstance(recorded, (int, float)) or recorded < 0
        or not isinstance(recorded_frames, int) or recorded_frames < 1
        or ffprobe is None
    ):
        return False
    probe = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
            "-show_entries", "stream=codec_name,nb_read_frames,duration",
            "-of", "json", str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )
    try:
        stream = json.loads(probe.stdout)["streams"][0]
        actual = float(stream.get("duration", 0.0))
        actual_frames = int(stream["nb_read_frames"])
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return (
        probe.returncode == 0 and stream.get("codec_name") == "h264"
        and actual >= 0 and actual_frames == recorded_frames
        and abs(actual - float(recorded)) <= 0.01
    )


def _cached_result_ok(output_root: Path, result: Any) -> bool:
    if not isinstance(result, dict) or result.get("status") != "passed":
        return False
    case_id = result.get("case_id")
    if not isinstance(case_id, str):
        return False
    case_root = output_root / "cases" / case_id
    execution = result.get("execution")
    return (
        _evidence_ok(case_root.resolve(), execution.get("visual_feedback") if isinstance(execution, dict) else None)
        and _recording_ok(case_root / "turn", execution)
    )


def _run_case(
    *,
    case: dict[str, Any],
    oracle: dict[str, Any],
    oracle_path: Path,
    config: dict[str, Any],
    output_root: Path,
    observation_seconds: float,
) -> dict[str, Any]:
    case_id = case["case_id"]
    case_root = output_root / "cases" / case_id
    begin_dir = case_root / "begin"
    turn_dir = case_root / "turn"
    recovery_dir = case_root / "recovery"
    evidence_root = case_root.resolve()
    candidate = _candidate(oracle_path, oracle, "negative_control")
    smoke_oracle = deepcopy(oracle)
    smoke_oracle["execute_turn"]["observation_seconds"] = observation_seconds
    environment = AndroidCommandEnvironment(
        config["runtime"]["environment_command"],
        timeout_seconds=float(config["runtime"]["environment_timeout_seconds"]),
    )
    started = datetime.now(timezone.utc).isoformat()
    begin = environment.begin_attempt(case, {
        "oracle": smoke_oracle,
        "evidence_dir": str(begin_dir),
        "command_timeout_seconds": config["runtime"]["environment_timeout_seconds"],
    })
    execution = environment.execute_turn(candidate, {
        "request_id": f"{case_id}-abcdef-visual-smoke-negative",
        "oracle": smoke_oracle,
        "case": case,
        "evidence_dir": str(turn_dir),
        "trajectory_evidence_root": str(evidence_root),
        "capture_protocol": config["capture_protocol"],
        "command_timeout_seconds": config["runtime"]["environment_timeout_seconds"],
    })
    recovered = environment.recover_turn({
        "oracle": smoke_oracle,
        "case": case,
        "evidence_dir": str(recovery_dir),
        "state_after_hash": execution.get("state_after_hash"),
        "command_timeout_seconds": config["runtime"]["environment_timeout_seconds"],
    })
    passed = (
        begin.get("reset_verified") is True
        and begin.get("target_page_verified") is True
        and execution.get("verdict") == "fail"
        and execution.get("observation_complete") is True
        and _evidence_ok(evidence_root, execution.get("visual_feedback"))
        and _recording_ok(turn_dir, execution)
        and recovered.get("target_page_verified") is True
        and recovered.get("input_cleared") is True
        and (
            (
                recovered.get("recovery_mode") == "natural"
                and recovered.get("natural_state_preserved") is True
                and recovered.get("snapshot_loaded") is False
            )
            or (
                recovered.get("recovery_mode") == "snapshot_fallback"
                and recovered.get("natural_state_preserved") is False
                and recovered.get("snapshot_loaded") is True
            )
        )
    )
    return {
        "schema_version": "e2-abcdef-v1-page-visual-smoke-case-1",
        "protocol_id": config["protocol_id"],
        "case_id": case_id,
        "status": "passed" if passed else "failed",
        "started_at_utc": started,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "oracle_version": oracle["version"],
        "candidate_sha256": hashlib.sha256(
            json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "formal_observation_seconds": oracle["execute_turn"]["observation_seconds"],
        "smoke_observation_seconds": observation_seconds,
        "begin_attempt": begin,
        "execution": execution,
        "recover_turn": recovered,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run per-page A-F visual-capture smokes")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--observation-seconds", type=float, default=3.0)
    parser.add_argument("--retry-passed", action="store_true")
    args = parser.parse_args()
    if args.observation_seconds < 3:
        raise ValueError("visual smoke must include the frozen t=3 tail frame")

    config_path = args.config.resolve()
    output_root = args.output.resolve()
    config = read_json(config_path)
    oracle_path = resolve_path(config_path, config["runtime"]["oracle_manifest"])
    registry_path = resolve_path(config_path, config["runtime"]["case_registry"])
    if oracle_path is None or registry_path is None:
        raise ValueError("runtime oracle manifest and case registry are required")
    oracles = load_oracles(oracle_path)
    registry = read_json(registry_path)
    requested = set(args.case or [])
    cases = [
        row for row in registry["cases"]
        if row.get("task") == "valid_pptr"
        and row.get("formal_applicability", {}).get("in_scope") is True
        and (not requested or row["case_id"] in requested)
    ]
    if requested - {row["case_id"] for row in cases}:
        raise ValueError(f"requested cases are not formal Page cases: {sorted(requested - {row['case_id'] for row in cases})}")
    if args.limit is not None:
        cases = cases[: args.limit]

    results: list[dict[str, Any]] = []
    for ordinal, case in enumerate(cases, 1):
        case_id = case["case_id"]
        result_path = output_root / "cases" / case_id / "result.json"
        if result_path.is_file() and not args.retry_passed:
            prior = read_json(result_path)
            if (
                prior.get("protocol_id") == config["protocol_id"]
                and prior.get("oracle_version") == oracles[case_id]["version"]
                and _cached_result_ok(output_root, prior)
            ):
                results.append(prior)
                print(f"[{ordinal}/{len(cases)}] {case_id} passed (cached)", flush=True)
                continue
        try:
            result = _run_case(
                case=case,
                oracle=oracles[case_id],
                oracle_path=oracle_path,
                config=config,
                output_root=output_root,
                observation_seconds=args.observation_seconds,
            )
        except Exception as exc:
            result = {
                "schema_version": "e2-abcdef-v1-page-visual-smoke-case-1",
                "protocol_id": config["protocol_id"],
                "case_id": case_id,
                "status": "error",
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                "error_type": type(exc).__name__,
                "error": str(exc) or repr(exc),
            }
        _atomic_json(result_path, result)
        results.append(result)
        print(f"[{ordinal}/{len(cases)}] {case_id} {result['status']}", flush=True)

    counts = Counter(row["status"] for row in results)
    passed = sorted(row["case_id"] for row in results if row["status"] == "passed")
    report = {
        "schema_version": "e2-abcdef-v1-page-visual-smoke-report-1",
        "protocol_id": config["protocol_id"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if len(passed) == 112 and len(results) == 112 else "incomplete",
        "config_sha256": sha256_file(config_path),
        "implementation_sha256": sha256_file(Path(__file__).resolve()),
        "selected_case_ids": [row["case_id"] for row in cases],
        "passed_case_ids": passed,
        "counts": dict(sorted(counts.items())),
        "results": [
            {
                "case_id": row["case_id"],
                "status": row["status"],
                "error_type": row.get("error_type"),
                "error": row.get("error"),
            }
            for row in results
        ],
    }
    _atomic_json(output_root / "report.json", report)
    print(json.dumps({
        "report": str((output_root / "report.json").resolve()),
        "status": report["status"],
        "counts": report["counts"],
        "passed": len(passed),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
