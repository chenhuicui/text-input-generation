"""Fresh-session model gateway for E2-structure-v1."""
from __future__ import annotations

import hashlib
import json
import ssl
import urllib.error
import urllib.request
from typing import Any

from .structured_state import fresh_model_payload


class PromptCapacityError(ValueError):
    pass


class StructuredGateway:
    """Submit exactly the frozen three-field request contract.

    A caller-supplied transport is supported for tests.  Proxy inheritance is
    disabled because the internal gateway has historically been sensitive to
    ambient proxy settings.  Only the Python standard library is required.
    """

    def __init__(
        self,
        api_url: str,
        api_token: str,
        *,
        timeout_seconds: float = 500,
        verify_ssl: bool = False,
        transport: Any | None = None,
    ) -> None:
        if not api_url or not api_token:
            raise ValueError("api_url and api_token are required")
        self.api_url = api_url
        self.timeout_seconds = timeout_seconds
        self.verify_ssl = verify_ssl
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
    def check_capacity(prompt: str, model_contract: dict[str, Any]) -> dict[str, Any]:
        encoded = prompt.encode("utf-8")
        maximum = model_contract.get("max_prompt_bytes")
        if model_contract.get("capacity_verified") is not True or not isinstance(maximum, int) or maximum < 1:
            raise PromptCapacityError("model capacity is not frozen to a positive max_prompt_bytes value")
        if len(encoded) > maximum:
            raise PromptCapacityError(f"prompt is {len(encoded)} bytes; frozen limit is {maximum}")
        return {"prompt_bytes": len(encoded), "max_prompt_bytes": maximum}

    def generate(self, model_contract: dict[str, Any], prompt: str) -> dict[str, Any]:
        capacity = self.check_capacity(prompt, model_contract)
        payload = fresh_model_payload(str(model_contract["id"]), prompt)
        if self.transport is not None:
            body = self.transport(payload)
        else:
            request = urllib.request.Request(
                self.api_url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=self.headers,
                method="POST",
            )
            try:
                with self.opener.open(request, timeout=self.timeout_seconds) as response:
                    body = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                # An HTTP response is a definitive provider-side rejection,
                # not an uncertain transport timeout.
                return {
                    "run_status": "infrastructure_error",
                    "error_type": f"http_{exc.code}",
                    "capacity": capacity,
                }
        data = body.get("data") if isinstance(body, dict) else None
        data = data if isinstance(data, dict) else {}
        chat = data.get("chat")
        if not isinstance(body, dict) or body.get("success") is False or body.get("error"):
            return {"run_status": "infrastructure_error", "error_type": "provider_error_response", "capacity": capacity}
        if not isinstance(chat, str):
            return {"run_status": "infrastructure_error", "error_type": "missing_chat_field", "capacity": capacity}
        usage = body.get("usage") or data.get("usage")
        return {
            "run_status": "ok",
            "raw_response": chat,
            "raw_response_sha256": hashlib.sha256(chat.encode("utf-8")).hexdigest(),
            "response_id": data.get("id"),
            "usage": usage if isinstance(usage, dict) else None,
            "capacity": capacity,
            "response_sha256": hashlib.sha256(
                json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }
