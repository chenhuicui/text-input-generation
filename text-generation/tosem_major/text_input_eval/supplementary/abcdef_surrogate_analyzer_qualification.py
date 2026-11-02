"""Replay one saved surrogate-bearing F-analysis context through the repaired gateway."""
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


def _json_safe(value: Any) -> Any:
    """Replace lone surrogate code units with visible escapes in the report artifact."""
    if isinstance(value, str):
        return value.encode("utf-8", errors="backslashreplace").decode("utf-8")
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--call-id", required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeat < 3:
        raise ValueError("surrogate qualification requires at least three repetitions")

    config = read_json(args.config.resolve())
    token_env = config["analyzer"]["api_key_env"]
    token = os.environ.get(token_env, "").strip()
    if not token:
        raise RuntimeError(f"environment variable {token_env} is empty")

    ledger_path = args.ledger.resolve()
    db = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
    try:
        row = db.execute(
            "SELECT kind,state,payload FROM external_calls WHERE id=?",
            (args.call_id,),
        ).fetchone()
    finally:
        db.close()
    if row is None:
        raise RuntimeError("saved analyzer call was not found")
    if row[0] != "external_feedback_analysis":
        raise RuntimeError("saved call is not an external feedback analysis")
    payload = json.loads(row[2])
    context = payload["context"]
    image_path = args.image.resolve()
    image_sha256 = sha256_file(image_path)
    if image_sha256 != payload.get("image_sha256"):
        raise RuntimeError("saved analyzer image hash does not match the call payload")

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
    results: list[dict[str, Any]] = []
    for repetition in range(1, args.repeat + 1):
        response = analyzer.analyze(context, image_path)
        diagnosis = response.get("diagnosis")
        passed = (
            response.get("run_status") == "ok"
            and isinstance(diagnosis, dict)
            and diagnosis.get("execution_status") == context.get("execution_status")
            and diagnosis.get("recommendation") is None
            and isinstance(response.get("provider_response"), dict)
            and isinstance(response.get("usage"), dict)
            and response["usage"].get("total_tokens") is not None
            and response.get("request_sha256") is not None
            and response.get("response_sha256") is not None
            and response.get("elapsed_ms") is not None
            and response.get("provider_elapsed_seconds") is not None
        )
        results.append({
            "repetition": repetition,
            "qualification_passed": passed,
            "result": response,
        })
    completed = datetime.now(timezone.utc)
    report = {
        "schema_version": "e2-abcdef-surrogate-analyzer-qualification-1",
        "started_at_utc": started.isoformat(),
        "completed_at_utc": completed.isoformat(),
        "source_ledger": str(ledger_path),
        "source_ledger_sha256": sha256_file(ledger_path),
        "source_call_id": args.call_id,
        "source_call_state": row[1],
        "source_context_sha256": payload.get("context_sha256"),
        "source_image": {"path": str(image_path), "sha256": image_sha256},
        "execution_status": context.get("execution_status"),
        "repetitions": args.repeat,
        "passed_repetitions": sum(item["qualification_passed"] for item in results),
        "status": "passed" if all(item["qualification_passed"] for item in results) else "failed",
        "report_string_sanitization": "lone surrogate code units rendered as visible backslash-u escapes",
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(_json_safe(report), ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output.resolve()),
        "status": report["status"],
        "passed": report["passed_repetitions"],
        "total": report["repetitions"],
    }))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
