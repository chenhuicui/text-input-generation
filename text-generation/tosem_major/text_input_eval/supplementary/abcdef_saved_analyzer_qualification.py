"""Replay saved invalid F-analysis evidence through the repaired analyzer contract."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .abcdef_analyzer import MUSTGatewayAnalyzer
from .abcdef_preflight import read_json, sha256_file


def _saved_invalid_calls(run_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ledger_path in sorted(run_root.glob("worker_*/ledger.sqlite3")):
        db = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            calls = db.execute(
                "SELECT id,dimensions,payload,result FROM external_calls "
                "WHERE kind='external_feedback_analysis' AND state='done'"
            ).fetchall()
            for call in calls:
                result = json.loads(call["result"])
                if result.get("run_status") != "analysis_invalid":
                    continue
                dimensions = json.loads(call["dimensions"])
                payload = json.loads(call["payload"])
                trajectory = db.execute(
                    "SELECT id,result FROM trajectories "
                    "WHERE json_extract(dimensions,'$.case_id')=? "
                    "AND json_extract(dimensions,'$.replicate')=? "
                    "AND json_extract(dimensions,'$.arm')='F' "
                    "AND state='done' ORDER BY updated_at DESC LIMIT 1",
                    (dimensions["case_id"], dimensions["replicate"]),
                ).fetchone()
                image_path: Path | None = None
                if trajectory is not None:
                    trajectory_result = json.loads(trajectory["result"])
                    record = next(
                        (
                            item for item in trajectory_result.get("records", [])
                            if item.get("turn") == dimensions.get("turn")
                        ),
                        None,
                    )
                    visual = record.get("visual_feedback") if isinstance(record, dict) else None
                    relative = visual.get("composite_path") if isinstance(visual, dict) else None
                    if isinstance(relative, str):
                        candidate = ledger_path.parent / "evidence" / trajectory["id"] / relative
                        if candidate.is_file():
                            image_path = candidate
                rows.append({
                    "worker": ledger_path.parent.name,
                    "source_call_id": call["id"],
                    "dimensions": dimensions,
                    "context": payload["context"],
                    "image_path": image_path,
                    "original_result": result,
                })
        finally:
            db.close()
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config = read_json(args.config.resolve())
    token_env = config["analyzer"]["api_key_env"]
    token = os.environ.get(token_env, "").strip()
    if not token:
        raise RuntimeError(f"environment variable {token_env} is empty")
    saved = _saved_invalid_calls(args.run_root.resolve())
    if len(saved) != 7:
        raise RuntimeError(f"expected exactly seven saved analysis_invalid calls, found {len(saved)}")
    analyzer = MUSTGatewayAnalyzer(
        token,
        model=config["analyzer"]["model"],
        reasoning_effort=config["analyzer"]["reasoning_effort"],
        api_url=config["analyzer"]["api_url"],
        timeout_seconds=float(config["runtime"]["api_timeout_seconds"]),
        verify_ssl=bool(config["analyzer"].get("verify_ssl", False)),
        max_transport_attempts=int(config["runtime"].get("transport_max_attempts", 3)),
        transport_retry_backoff_seconds=float(
            config["runtime"].get("transport_retry_backoff_seconds", 0.5)
        ),
    )
    started = datetime.now(timezone.utc)
    results = []
    for item in saved:
        response = analyzer.analyze(item["context"], item["image_path"])
        diagnosis = response.get("diagnosis")
        passed = (
            response.get("run_status") == "ok"
            and isinstance(diagnosis, dict)
            and diagnosis.get("execution_status") == item["context"]["execution_status"]
            and diagnosis.get("recommendation") is None
        )
        results.append({
            "worker": item["worker"],
            "source_call_id": item["source_call_id"],
            "dimensions": item["dimensions"],
            "source_context_execution_status": item["context"]["execution_status"],
            "source_image": (
                {
                    "path": str(item["image_path"]),
                    "sha256": sha256_file(item["image_path"]),
                }
                if item["image_path"] is not None else None
            ),
            "original_validation_error": item["original_result"].get("validation_error"),
            "qualification_passed": passed,
            "new_result": response,
        })
    completed = datetime.now(timezone.utc)
    report = {
        "schema_version": "e2-abcdef-v1-saved-f-analyzer-requalification-1",
        "started_at_utc": started.isoformat(),
        "completed_at_utc": completed.isoformat(),
        "source_run_root": str(args.run_root.resolve()),
        "config_path": str(args.config.resolve()),
        "config_sha256": sha256_file(args.config.resolve()),
        "saved_invalid_observations": len(saved),
        "passed_observations": sum(row["qualification_passed"] for row in results),
        "status": "passed" if all(row["qualification_passed"] for row in results) else "failed",
        "token_recorded": False,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output.resolve()),
        "status": report["status"],
        "passed": report["passed_observations"],
        "total": report["saved_invalid_observations"],
    }, ensure_ascii=False))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
