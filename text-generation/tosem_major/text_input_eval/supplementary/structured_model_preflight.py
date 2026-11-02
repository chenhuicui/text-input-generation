"""Empirically qualify E2 model gateway contracts without running App cases."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .structured_preflight import REPO_ROOT, model_probe_contract, read_json, sha256_file, sha256_json
from .structured_state import fresh_model_payload


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ProbeClient:
    def __init__(self, url: str, token: str, timeout: float, verify_ssl: bool) -> None:
        self.url = url
        self.token = token
        self.timeout = timeout
        handlers: list[Any] = [urllib.request.ProxyHandler({})]
        if url.startswith("https://") and not verify_ssl:
            handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
        self.opener = urllib.request.build_opener(*handlers)

    def call(self, payload: dict[str, str]) -> dict[str, Any]:
        started = time.monotonic()
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return {
                "transport_status": "http_error",
                "http_status": exc.code,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            }
        except (TimeoutError, urllib.error.URLError) as exc:
            return {
                "transport_status": "transport_error",
                "error_type": type(exc).__name__,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            }
        data = body.get("data") if isinstance(body, dict) else None
        data = data if isinstance(data, dict) else {}
        chat = data.get("chat")
        return {
            "transport_status": "response",
            "success": body.get("success") is True,
            "provider_code": body.get("code"),
            "provider_message": body.get("msg"),
            "response_model": data.get("model"),
            "response_id_present": isinstance(data.get("id"), str) and bool(data.get("id")),
            "chat": chat if isinstance(chat, str) else None,
            "chat_sha256": hashlib.sha256(chat.encode("utf-8")).hexdigest() if isinstance(chat, str) else None,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }


def public_attempt(result: dict[str, Any], expected_chat: str | None = None) -> dict[str, Any]:
    output = {key: value for key, value in result.items() if key != "chat"}
    if expected_chat is not None:
        output["expected_chat_match"] = result.get("chat") == expected_chat
    return output


def capacity_message(limit: int) -> str:
    prefix = "Ignore padding and return exactly CAPACITY_OK.\n"
    if len(prefix.encode("utf-8")) > limit:
        raise ValueError("capacity limit is smaller than probe prefix")
    return prefix + "x" * (limit - len(prefix.encode("utf-8")))


def probe_model(client: ProbeClient, model_id: str, capacity_bytes: int) -> dict[str, Any]:
    baseline_text = "Return exactly E2_PREFLIGHT_OK and nothing else."
    baseline_payload = fresh_model_payload(model_id, baseline_text)
    baseline = client.call(baseline_payload)
    baseline_ok = baseline.get("success") is True and baseline.get("chat") == "E2_PREFLIGHT_OK"
    result: dict[str, Any] = {
        "model_id": model_id,
        "gateway_verified": baseline_ok,
        "revision_verified": baseline_ok and baseline.get("response_model") == model_id,
        "generation_defaults_verified": baseline_ok,
        "capacity_verified": False,
        "fresh_session_isolation_verified": False,
        "max_prompt_bytes": None,
        "request_contract": {
            "fields": sorted(baseline_payload),
            "id_empty": baseline_payload["id"] == "",
            "payload_sha256": hashlib.sha256(canonical_json(baseline_payload).encode("utf-8")).hexdigest(),
        },
        "attempts": {"baseline": public_attempt(baseline, "E2_PREFLIGHT_OK")},
    }
    if not baseline_ok:
        return result
    nonce = f"E2_ISOLATION_{secrets.token_hex(8).upper()}"
    poison = client.call(fresh_model_payload(
        model_id, f"Memorize the nonce {nonce} for this conversation. Return exactly STORED."
    ))
    fresh = client.call(fresh_model_payload(
        model_id, "This is a fresh request. Return exactly FRESH_OK. Do not include any other text."
    ))
    capacity = client.call(fresh_model_payload(model_id, capacity_message(capacity_bytes)))
    isolation_ok = (
        poison.get("success") is True
        and poison.get("chat") == "STORED"
        and fresh.get("success") is True
        and fresh.get("chat") == "FRESH_OK"
        and nonce not in str(fresh.get("chat") or "")
    )
    capacity_ok = capacity.get("success") is True and capacity.get("response_model") == model_id
    result.update(
        capacity_verified=capacity_ok,
        fresh_session_isolation_verified=isolation_ok,
        max_prompt_bytes=capacity_bytes if capacity_ok else None,
    )
    result["attempts"].update({
        "isolation_poison": public_attempt(poison, "STORED"),
        "isolation_fresh": public_attempt(fresh, "FRESH_OK"),
        "capacity": {
            **public_attempt(capacity),
            "prompt_bytes": capacity_bytes,
        },
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Qualify E2 model gateway contracts")
    parser.add_argument(
        "--config", type=Path,
        default=REPO_ROOT / "configs" / "supplementary" / "e2_structure_v1.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capacity-bytes", type=int, default=65536)
    parser.add_argument("--timeout", type=float, default=40)
    parser.add_argument("--model", action="append")
    args = parser.parse_args()
    config = read_json(args.config.resolve())
    runtime = config["runtime"]
    token_env = runtime["api_token_env"]
    token = os.environ.get(token_env, "").strip()
    if not token:
        raise RuntimeError(f"environment variable {token_env} is empty")
    if args.capacity_bytes < 1:
        raise ValueError("capacity bytes must be positive")
    requested = set(args.model or [row["id"] for row in config["models"]])
    known = {row["id"] for row in config["models"]}
    if not requested.issubset(known):
        raise ValueError(f"unknown model IDs: {sorted(requested-known)}")
    client = ProbeClient(
        runtime["api_url"], token, args.timeout, bool(runtime.get("verify_ssl", False))
    )
    started = datetime.now(timezone.utc)
    models = [
        probe_model(client, row["id"], args.capacity_bytes)
        for row in config["models"] if row["id"] in requested
    ]
    report = {
        "schema_version": "e2-structure-v1-model-preflight-1",
        "protocol_id": config["protocol_id"],
        "started_at_utc": started.isoformat(),
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_sha256": sha256_file(args.config.resolve()),
        "probe_contract": model_probe_contract(config),
        "probe_contract_sha256": sha256_json(model_probe_contract(config)),
        "api_url": runtime["api_url"],
        "api_token_env": token_env,
        "token_recorded": False,
        "capacity_probe_bytes": args.capacity_bytes,
        "models": models,
        "all_models_qualified": all(
            all(row.get(field) is True for field in (
                "gateway_verified", "revision_verified", "generation_defaults_verified",
                "capacity_verified", "fresh_session_isolation_verified",
            )) for row in models
        ) and len(models) == len(config["models"]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output.resolve()),
        "all_models_qualified": report["all_models_qualified"],
        "models": {
            row["model_id"]: {
                key: row[key] for key in (
                    "gateway_verified", "revision_verified", "generation_defaults_verified",
                    "capacity_verified", "fresh_session_isolation_verified", "max_prompt_bytes",
                )
            } for row in models
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
