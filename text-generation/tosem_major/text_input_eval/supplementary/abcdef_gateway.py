"""Fresh-session subject gateway with one optional feedback image."""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable


class SubjectCapacityError(ValueError):
    pass


_QUOTA_MARKERS = (
    "insufficient balance",
    "overdue balance",
    "insufficient credit",
    "insufficient quota",
    "quota exhausted",
    "quota has been exhausted",
    "credit balance",
    "balance not enough",
    "balance insufficient",
    "out of credit",
    "no remaining quota",
    "余额不足",
    "额度不足",
    "配额不足",
    "欠费",
)


def provider_failure_type(body: Any, *, http_status: int | None = None) -> str:
    """Classify a terminal provider response without conflating rate limits and funds."""
    rendered = json.dumps(body, ensure_ascii=False, sort_keys=True).lower()
    normalized = re.sub(r"[_-]+", " ", rendered)
    provider_code = body.get("code") if isinstance(body, dict) else None
    if (
        http_status == 402
        or provider_code in {402, "402"}
        or any(marker in rendered or marker in normalized for marker in _QUOTA_MARKERS)
    ):
        return "quota_exhausted"
    if http_status is not None:
        return f"http_{http_status}"
    return "provider_error_response"


def _http_error_body(exc: urllib.error.HTTPError) -> Any:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_body": raw[:8000]}


def request_json_with_retries(
    send: Callable[[], Any],
    *,
    max_attempts: int,
    backoff_seconds: float,
) -> tuple[Any | None, list[dict[str, Any]], dict[str, Any] | None]:
    """Run one logical read-only inference request with bounded transport retries.

    A physical attempt is always recorded.  Provider quota responses are terminal;
    an ordinary HTTP 429 is not treated as exhausted funds unless its body says so.
    """
    attempts: list[dict[str, Any]] = []
    count = max(1, int(max_attempts))
    delay = max(0.0, float(backoff_seconds))
    for number in range(1, count + 1):
        started = time.monotonic()
        try:
            body = send()
            attempts.append({
                "attempt": number,
                "status": "response_received",
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            })
            return body, attempts, None
        except urllib.error.HTTPError as exc:
            provider_body = _http_error_body(exc)
            error_type = provider_failure_type(provider_body, http_status=exc.code)
            attempts.append({
                "attempt": number,
                "status": "transport_error",
                "error_type": error_type,
                "error_message": str(exc)[:1000],
                "http_status": exc.code,
                "provider_response": provider_body,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            })
            retryable = exc.code in {408, 425, 429} or exc.code >= 500
            if error_type == "quota_exhausted" or not retryable or number == count:
                return None, attempts, {
                    "run_status": "infrastructure_error",
                    "error_type": error_type,
                    "error_message": str(exc)[:1000],
                    "http_status": exc.code,
                    "provider_response": provider_body,
                }
        except (
            TimeoutError,
            urllib.error.URLError,
            http.client.RemoteDisconnected,
            ConnectionError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            attempts.append({
                "attempt": number,
                "status": "transport_error",
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:1000],
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            })
            if number == count:
                return None, attempts, {
                    "run_status": "infrastructure_error",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:1000],
                }
        if delay:
            time.sleep(delay * number)
    raise AssertionError("bounded transport loop ended unexpectedly")


def provider_usage(body: dict[str, Any], data: dict[str, Any]) -> Any:
    """Prefer explicit gateway usage, then the JSON provider envelope when present."""
    usage = body.get("usage") or data.get("usage")
    if usage is not None:
        return usage
    origin = data.get("originContent")
    if isinstance(origin, str):
        try:
            origin = json.loads(origin)
        except json.JSONDecodeError:
            return None
    return origin.get("usage") if isinstance(origin, dict) else None


def image_data_url(path: Path) -> str:
    raw = path.read_bytes()
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("subject feedback attachment must be a PNG")
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


class ABCDEFSubjectGateway:
    def __init__(
        self,
        api_url: str,
        api_token: str,
        *,
        timeout_seconds: float = 500,
        verify_ssl: bool = False,
        max_transport_attempts: int = 3,
        transport_retry_backoff_seconds: float = 0.5,
        transport: Any | None = None,
    ) -> None:
        if not api_url or not api_token:
            raise ValueError("subject api_url and api_token are required")
        self.api_url = api_url
        self.timeout_seconds = timeout_seconds
        self.max_transport_attempts = max(1, int(max_transport_attempts))
        self.transport_retry_backoff_seconds = max(0.0, float(transport_retry_backoff_seconds))
        self.transport = transport
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
        handlers: list[Any] = [urllib.request.ProxyHandler({})]
        if api_url.startswith("https://") and not verify_ssl:
            handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
        self.opener = urllib.request.build_opener(*handlers)

    @staticmethod
    def build_payload(model: dict[str, Any], prompt: str, attachments: list[Path]) -> dict[str, Any]:
        if len(attachments) > 1:
            raise ValueError("the frozen subject gateway supports at most one composite image")
        maximum = model.get("max_prompt_bytes")
        if model.get("capacity_verified") is not True or not isinstance(maximum, int) or maximum < 1:
            raise SubjectCapacityError("subject prompt capacity is not frozen")
        prompt_bytes = len(prompt.encode("utf-8"))
        if prompt_bytes > maximum:
            raise SubjectCapacityError(f"prompt is {prompt_bytes} bytes; frozen limit is {maximum}")
        payload: dict[str, Any] = {"modelType": model["id"], "id": "", "message": prompt}
        if attachments:
            image_path = attachments[0]
            image_bytes = image_path.stat().st_size
            image_maximum = model.get("max_feedback_image_bytes")
            if (
                model.get("feedback_image_verified") is not True
                or not isinstance(image_maximum, int)
                or image_maximum < 1
            ):
                raise SubjectCapacityError("subject feedback-image transport is not frozen")
            if image_bytes > image_maximum:
                raise SubjectCapacityError(
                    f"feedback image is {image_bytes} bytes; frozen limit is {image_maximum}"
                )
            payload["imageUrl"] = image_data_url(image_path)
        return payload

    def generate(
        self,
        model: dict[str, Any],
        prompt: str,
        attachments: list[Path] | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        try:
            payload = self.build_payload(model, prompt, attachments or [])
        except (OSError, ValueError) as exc:
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
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=self.headers,
                method="POST",
            )
            def send() -> Any:
                with self.opener.open(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
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
        data = body.get("data") if isinstance(body, dict) else None
        data = data if isinstance(data, dict) else {}
        chat = data.get("chat")
        if not isinstance(body, dict) or body.get("success") is False or body.get("error"):
            error_type = provider_failure_type(body)
            return {
                "run_status": "infrastructure_error",
                "error_type": error_type,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "provider_response": body,
                "transport_attempts": attempts,
            }
        if not isinstance(chat, str):
            return {
                "run_status": "infrastructure_error",
                "error_type": "missing_chat_field",
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "provider_response": body,
                "transport_attempts": attempts,
            }
        return {
            "run_status": "ok",
            "raw_response": chat,
            "raw_response_sha256": hashlib.sha256(chat.encode("utf-8")).hexdigest(),
            "response_id": data.get("id"),
            "session_id": data.get("sessionId"),
            "response_model": data.get("model"),
            "usage": provider_usage(body, data),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "provider_elapsed_seconds": data.get("time"),
            "provider_response": body,
            "payload_fields": sorted(payload),
            "transport_attempts": attempts,
        }
