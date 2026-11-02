"""Evidence-only GPT-5.6 Sol analyzer used by condition F via MUST."""
from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .abcdef_contract import canonical_json
from .abcdef_gateway import (
    provider_failure_type,
    provider_usage,
    request_json_with_retries,
)
from .abcdef_templates import ANALYZER_FIELDS, validate_analyzer_diagnosis


ANALYZER_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": sorted(ANALYZER_FIELDS),
    "properties": {
        "execution_status": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
        "diagnosis_category": {"type": "string"},
        "diagnosis": {"type": "string"},
        "support_level": {"type": "string", "enum": ["direct", "partial", "insufficient"]},
        "temporal_summary": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "alternative_explanations": {"type": "array", "items": {"type": "string"}},
        "uncertainty": {"type": "string"},
        "recommendation": {"type": "null"},
    },
}


def analyzer_prompt(context: dict[str, Any]) -> str:
    allowed = {
        "case_id": context.get("case_id"),
        "task_goal": context.get("task_goal"),
        "candidate": context.get("candidate"),
        "execution_status": context.get("execution_status"),
        "ui_observations": context.get("ui_observations"),
        "frame_manifest": context.get("frame_manifest"),
    }
    evidence_refs = sorted(_allowed_evidence_refs(context))
    evidence_rule = (
        "No screenshot or UI evidence reference was supplied. Set evidence_refs to [], "
        "set support_level to insufficient, and do not invent visual evidence. "
        if not evidence_refs else
        "Every evidence_refs entry must be copied exactly from this allowed list: "
        + json.dumps(evidence_refs, ensure_ascii=False)
        + ". "
    )
    return (
        "You are a fixed external evidence analyzer for a controlled experiment. "
        "Describe why the recorded attempt appears to have passed, failed, or remained unknown, "
        "using only the supplied candidate, status, screenshots, and public UI observations. "
        "The execution_status output field must exactly copy the supplied execution_status. "
        "Never reinterpret or change it, even when visible evidence appears inconsistent; "
        "describe any conflict only in diagnosis and uncertainty. "
        "Do not propose a new input, do not give advice, do not infer hidden validation rules, "
        "and do not mention validators, regular expressions, expected answers, or future attempts. "
        "Do not use the literal terms 'validator', 'regex', 'expected value', 'try ', "
        "'should ', '建议', '应该', or '请改' in any output value. "
        "Set recommendation to null. "
        + evidence_rule
        + "\n\n"
        "[EVIDENCE]\n" + json.dumps(allowed, ensure_ascii=False, sort_keys=True, indent=2)
    )


def _allowed_evidence_refs(context: dict[str, Any]) -> set[str]:
    references: set[str] = set()
    for row in context.get("frame_manifest", []):
        if isinstance(row, dict) and isinstance(row.get("evidence_ref"), str):
            references.add(row["evidence_ref"])
    observations = context.get("ui_observations")
    if isinstance(observations, dict):
        for row in observations.get("timeline", []):
            if isinstance(row, dict) and isinstance(row.get("evidence_ref"), str):
                references.add(row["evidence_ref"])
    return references


def _request_json_bytes(payload: dict[str, Any]) -> bytes:
    """Serialize analyzer requests without emitting invalid UTF-8 surrogates."""
    return canonical_json(payload).encode("utf-8")


class MUSTGatewayAnalyzer:
    def __init__(
        self,
        api_token: str,
        *,
        model: str = "gpt-5.6-sol",
        reasoning_effort: str = "medium",
        api_url: str = "http://must.lab.help1.com.cn/api/chat/completions",
        timeout_seconds: float = 500,
        verify_ssl: bool = False,
        max_transport_attempts: int = 3,
        transport_retry_backoff_seconds: float = 0.5,
        transport: Any | None = None,
    ) -> None:
        if not api_token:
            raise ValueError("MUST analyzer API token is required")
        if model != "gpt-5.6-sol" or reasoning_effort != "medium":
            raise ValueError("F analyzer model and reasoning effort are frozen")
        self.api_token = api_token
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.api_url = api_url
        self.timeout_seconds = timeout_seconds
        self.max_transport_attempts = max(1, int(max_transport_attempts))
        self.transport_retry_backoff_seconds = max(0.0, float(transport_retry_backoff_seconds))
        self.transport = transport
        handlers: list[Any] = [urllib.request.ProxyHandler({})]
        if api_url.startswith("https://") and not verify_ssl:
            import ssl
            handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
        self.opener = urllib.request.build_opener(*handlers)

    def build_payload(self, context: dict[str, Any], image_path: Path | None) -> dict[str, Any]:
        prompt = (
            analyzer_prompt(context)
            + "\n\n[OUTPUT CONTRACT]\n"
            + "Return exactly one JSON object and no Markdown or surrounding text. "
            + "The object must satisfy this schema: "
            + json.dumps(ANALYZER_JSON_SCHEMA, ensure_ascii=False, sort_keys=True)
        )
        payload: dict[str, Any] = {
            "modelType": self.model,
            "sessionId": "",
            "message": prompt,
            "reasoningEffort": self.reasoning_effort,
        }
        if image_path is not None:
            raw = image_path.read_bytes()
            if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("F analyzer image must be a PNG")
            payload["imageUrl"] = (
                "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
            )
        return payload

    def analyze(self, context: dict[str, Any], image_path: Path | None = None) -> dict[str, Any]:
        started = time.monotonic()
        try:
            payload = self.build_payload(context, image_path)
            request_data = None if self.transport is not None else _request_json_bytes(payload)
        except (OSError, UnicodeError, ValueError) as exc:
            return {
                "run_status": "infrastructure_error",
                "error_type": type(exc).__name__,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            }
        if self.transport is not None:
            send = lambda: self.transport(payload)
        else:
            request = urllib.request.Request(
                self.api_url,
                data=request_data,
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            def send() -> Any:
                with self.opener.open(request, timeout=self.timeout_seconds) as reply:
                    return json.loads(reply.read().decode("utf-8"))
        body, attempts, transport_error = request_json_with_retries(
            send,
            max_attempts=self.max_transport_attempts,
            backoff_seconds=self.transport_retry_backoff_seconds,
        )
        if transport_error is not None:
            return {
                **transport_error,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "transport_attempts": attempts,
            }
        if not isinstance(body, dict) or body.get("success") is not True or body.get("error"):
            return {
                "run_status": "infrastructure_error",
                "error_type": provider_failure_type(body),
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "provider_response": body,
                "transport_attempts": attempts,
            }
        data = body.get("data")
        if not isinstance(data, dict):
            return {"run_status": "infrastructure_error", "error_type": "missing_data_field"}
        if data.get("model") != self.model:
            return {"run_status": "infrastructure_error", "error_type": "response_model_mismatch"}
        raw = data.get("chat")
        if not isinstance(raw, str):
            return {"run_status": "infrastructure_error", "error_type": "missing_chat_field"}
        try:
            diagnosis = json.loads(raw)
            diagnosis = validate_analyzer_diagnosis(
                diagnosis,
                outcome=str(context["execution_status"]),
            )
            cited = set(diagnosis["evidence_refs"])
            if not cited.issubset(_allowed_evidence_refs(context)):
                raise ValueError("F analyzer cited evidence that was not supplied")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return {
                "run_status": "analysis_invalid",
                "error_type": type(exc).__name__,
                "validation_error": str(exc)[:500],
                "raw_output_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "raw_response": raw,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "provider_elapsed_seconds": data.get("time"),
                "provider_response": body,
                "transport_attempts": attempts,
            }
        return {
            "run_status": "ok",
            "diagnosis": diagnosis,
            "raw_response": raw,
            "response_id": data.get("id"),
            "session_id": data.get("sessionId"),
            "response_model": data.get("model"),
            "requested_reasoning_effort": self.reasoning_effort,
            "usage": provider_usage(body, data),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "provider_elapsed_seconds": data.get("time"),
            "provider_response": body,
            "transport_attempts": attempts,
            "request_sha256": hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest(),
            "response_sha256": hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest(),
        }
