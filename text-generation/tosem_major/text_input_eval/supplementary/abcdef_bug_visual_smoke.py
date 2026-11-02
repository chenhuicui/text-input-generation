"""Run resumable hybrid predicate plus App-observation smokes for 37 Bug cases."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from .abcdef_environment import ABCDEFHybridEnvironment
from .abcdef_preflight import (
    DEFAULT_CONFIG,
    REPO_ROOT,
    load_bug_feedback_oracles,
    read_json,
    resolve_path,
    sha256_file,
)
from .structured_bug_predicates import control_pair
from .structured_cli import load_oracles
from .structured_environment import AndroidCommandEnvironment, StringPredicateEnvironment


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


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


def _recording_ok(evidence_dir: Path, evidence: Any) -> bool:
    rows = [
        row for row in evidence if isinstance(row, dict) and row.get("kind") == "screen_recording"
    ] if isinstance(evidence, list) else []
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
    execution = result.get("execution")
    if not isinstance(case_id, str) or not isinstance(execution, dict):
        return False
    case_root = output_root / "cases" / case_id
    return (
        _evidence_ok(case_root.resolve(), execution.get("visual_feedback"))
        and _recording_ok(
            case_root / "turn" / "app_observation",
            execution.get("app_observation_evidence"),
        )
    )


def _run_case(
    *,
    case: dict[str, Any],
    predicate_oracle: dict[str, Any],
    feedback_oracle: dict[str, Any],
    config: dict[str, Any],
    output_root: Path,
    observation_seconds: float,
) -> dict[str, Any]:
    case_id = case["case_id"]
    case_root = output_root / "cases" / case_id
    begin_dir = case_root / "begin"
    turn_dir = case_root / "turn"
    attempt_id = f"{case_id}-abcdef-bug-visual-smoke"
    _, candidate = control_pair(case)
    smoke_feedback = deepcopy(feedback_oracle)
    smoke_feedback["execute_turn"]["observation_seconds"] = observation_seconds
    environment = ABCDEFHybridEnvironment(
        AndroidCommandEnvironment(
            config["runtime"]["environment_command"],
            timeout_seconds=float(config["runtime"]["environment_timeout_seconds"]),
        ),
        StringPredicateEnvironment(REPO_ROOT),
        {case_id: smoke_feedback},
    )
    started = datetime.now(timezone.utc).isoformat()
    begin = environment.begin_attempt(case, {
        "attempt_id": attempt_id,
        "case_id": case_id,
        "oracle": predicate_oracle,
        "evidence_dir": str(begin_dir),
        "command_timeout_seconds": config["runtime"]["environment_timeout_seconds"],
    })
    execution_request = {
        "attempt_id": attempt_id,
        "request_id": f"{case_id}-abcdef-bug-visual-smoke-negative",
        "physical_request_id": "not_applicable_smoke",
        "arm": "E",
        "turn": 1,
        "case": case,
        "oracle": predicate_oracle,
        "evidence_dir": str(turn_dir),
        "trajectory_evidence_root": str(case_root.resolve()),
        "capture_protocol": config["capture_protocol"],
        "command_timeout_seconds": config["runtime"]["environment_timeout_seconds"],
    }
    execution = environment.execute_turn(candidate, execution_request)
    recovery = environment.recover_turn({
        **execution,
        "attempt_id": attempt_id,
        "arm": "E",
        "turn": 1,
        "case": case,
        "oracle": predicate_oracle,
        "evidence_dir": str(turn_dir),
        "command_timeout_seconds": config["runtime"]["environment_timeout_seconds"],
    })
    passed = (
        begin.get("reset_verified") is True
        and begin.get("target_page_verified") is True
        and execution.get("verdict") == "fail"
        and execution.get("observation_complete") is True
        and execution.get("app_observation_verdict") == "unknown"
        and _evidence_ok(case_root.resolve(), execution.get("visual_feedback"))
        and _recording_ok(
            turn_dir / "app_observation", execution.get("app_observation_evidence")
        )
        and recovery.get("snapshot_loaded") is True
        and recovery.get("target_page_verified") is True
        and recovery.get("input_cleared") is True
        and recovery.get("app_observation_recovery_mode") == "independent_baseline_reload"
    )
    return {
        "schema_version": "e2-abcdef-v1-bug-visual-smoke-case-1",
        "protocol_id": config["protocol_id"],
        "case_id": case_id,
        "status": "passed" if passed else "failed",
        "started_at_utc": started,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "predicate_oracle_version": predicate_oracle["version"],
        "feedback_oracle_version": feedback_oracle["version"],
        "formal_observation_seconds": feedback_oracle["execute_turn"]["observation_seconds"],
        "smoke_observation_seconds": observation_seconds,
        "begin_attempt": begin,
        "execution": execution,
        "recover_turn": recovery,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run 37 Bug hybrid App-observation smokes")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--contracts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--observation-seconds", type=float, default=3.0)
    parser.add_argument("--retry-passed", action="store_true")
    args = parser.parse_args()
    if args.observation_seconds < 3:
        raise ValueError("Bug visual smoke must include the frozen t=3 tail frame")

    config_path = args.config.resolve()
    contracts_path = args.contracts.resolve()
    output_root = args.output.resolve()
    config = read_json(config_path)
    registry_path = resolve_path(config_path, config["runtime"]["case_registry"])
    oracle_path = resolve_path(config_path, config["runtime"]["oracle_manifest"])
    if registry_path is None or oracle_path is None:
        raise ValueError("runtime case registry and Oracle manifest are required")
    registry = read_json(registry_path)
    oracles = load_oracles(oracle_path)
    contracts = load_bug_feedback_oracles(contracts_path)
    requested = set(args.case or [])
    cases = [
        row for row in registry["cases"]
        if row.get("task") == "invalid_bug" and (not requested or row["case_id"] in requested)
    ]
    if requested - {row["case_id"] for row in cases}:
        raise ValueError(f"requested cases are not Bug cases: {sorted(requested - {row['case_id'] for row in cases})}")
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
                and prior.get("predicate_oracle_version") == oracles[case_id]["version"]
                and prior.get("feedback_oracle_version") == contracts[case_id]["version"]
                and _cached_result_ok(output_root, prior)
            ):
                results.append(prior)
                print(f"[{ordinal}/{len(cases)}] {case_id} passed (cached)", flush=True)
                continue
        try:
            result = _run_case(
                case=case,
                predicate_oracle=oracles[case_id],
                feedback_oracle=contracts[case_id],
                config=config,
                output_root=output_root,
                observation_seconds=args.observation_seconds,
            )
        except Exception as exc:
            result = {
                "schema_version": "e2-abcdef-v1-bug-visual-smoke-case-1",
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
        "schema_version": "e2-abcdef-v1-bug-visual-smoke-report-1",
        "protocol_id": config["protocol_id"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if len(passed) == 37 and len(results) == 37 else "incomplete",
        "config_sha256": sha256_file(config_path),
        "contracts_sha256": sha256_file(contracts_path),
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
