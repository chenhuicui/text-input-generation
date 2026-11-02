"""Live qualification probe for the frozen condition-F MUST gateway route."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .abcdef_analyzer import MUSTGatewayAnalyzer
from .abcdef_contract import canonical_json
from .abcdef_preflight import (
    DEFAULT_CONFIG,
    qualification_config_sha256,
    read_json,
    sha256_file,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Qualify condition-F MUST gateway route")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()

    config_path = args.config.resolve()
    image_path = args.image.resolve()
    config = read_json(config_path)
    analyzer_config = config["analyzer"]
    token_env = analyzer_config["api_key_env"]
    token = os.environ.get(token_env, "").strip()
    if not token:
        raise RuntimeError(f"environment variable {token_env} is empty")

    evidence_ref = "qualification:frame-1"
    context = {
        "case_id": "QUALIFICATION",
        "task_goal": "Describe only what the supplied UI screenshot directly supports.",
        "candidate": {"qualification_probe": True},
        "execution_status": "UNKNOWN",
        "ui_observations": {
            "timeline": [{
                "evidence_ref": evidence_ref,
                "description": "A UI screenshot was supplied for transport qualification.",
            }],
        },
        "frame_manifest": [{"evidence_ref": evidence_ref, "role": "settled"}],
    }
    analyzer = MUSTGatewayAnalyzer(
        token,
        model=analyzer_config["model"],
        reasoning_effort=analyzer_config["reasoning_effort"],
        api_url=analyzer_config["api_url"],
        timeout_seconds=args.timeout,
        verify_ssl=bool(analyzer_config.get("verify_ssl", False)),
    )
    started = datetime.now(timezone.utc)
    result = analyzer.analyze(context, image_path)
    completed = datetime.now(timezone.utc)
    diagnosis = result.get("diagnosis")
    passed = (
        result.get("run_status") == "ok"
        and result.get("response_model") == analyzer_config["model"]
        and isinstance(result.get("response_id"), str)
        and bool(result["response_id"])
        and isinstance(diagnosis, dict)
        and diagnosis.get("execution_status") == "UNKNOWN"
        and diagnosis.get("recommendation") is None
        and set(diagnosis.get("evidence_refs", [])).issubset({evidence_ref})
    )
    report = {
        "schema_version": "e2-abcdef-v1-f-analyzer-gateway-qualification-1",
        "protocol_id": config["protocol_id"],
        "started_at_utc": started.isoformat(),
        "completed_at_utc": completed.isoformat(),
        "config_sha256": sha256_file(config_path),
        "qualification_config_sha256": qualification_config_sha256(config),
        "endpoint": analyzer_config["api_url"],
        "api_token_env": token_env,
        "token_recorded": False,
        "request_contract": {
            "model_field": "modelType",
            "model": analyzer_config["model"],
            "session_field": analyzer_config["session_field"],
            "fresh_session_value": analyzer_config["fresh_session_value"],
            "message_field": "message",
            "image_field": analyzer_config["image_field"],
            "reasoning_effort_field": analyzer_config["reasoning_effort_field"],
            "reasoning_effort": analyzer_config["reasoning_effort"],
            "schema_enforcement": analyzer_config["schema_enforcement"],
            "resolved_reasoning_effort_returned": False,
        },
        "fixture": {
            "path": str(image_path),
            "sha256": sha256_file(image_path),
            "bytes": image_path.stat().st_size,
        },
        "qualification": {
            "status": "passed" if passed else "failed",
            "run_status": result.get("run_status"),
            "error_type": result.get("error_type"),
            "response_model": result.get("response_model"),
            "response_id_present": isinstance(result.get("response_id"), str) and bool(result.get("response_id")),
            "session_id_present": isinstance(result.get("session_id"), str) and bool(result.get("session_id")),
            "execution_diagnosis_v1_valid": isinstance(diagnosis, dict),
            "execution_status_preserved": isinstance(diagnosis, dict) and diagnosis.get("execution_status") == "UNKNOWN",
            "recommendation_is_null": isinstance(diagnosis, dict) and diagnosis.get("recommendation") is None,
            "evidence_refs_subset_of_supplied_refs": (
                isinstance(diagnosis, dict)
                and set(diagnosis.get("evidence_refs", [])).issubset({evidence_ref})
            ),
            "diagnosis_sha256": (
                hashlib.sha256(canonical_json(diagnosis).encode("utf-8")).hexdigest()
                if isinstance(diagnosis, dict) else None
            ),
            "request_sha256": result.get("request_sha256"),
            "response_sha256": result.get("response_sha256"),
        },
        "formal_gate": {
            "api_verified": passed,
            "reasoning_effort_audit": "requested_parameter_recorded_response_does_not_echo_resolved_value",
        },
        "formal_experiment_calls": 0,
        "probe_calls": 1,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output.resolve()),
        "api_verified": passed,
        "qualification": report["qualification"],
    }, ensure_ascii=False))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
