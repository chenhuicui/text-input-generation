"""Qualify the frozen subject-model gateway with a real feedback image."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .abcdef_preflight import DEFAULT_CONFIG, read_json, sha256_file


def _data_url(path: Path) -> str:
    raw = path.read_bytes()
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("image qualification fixture must be a PNG")
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def _call(
    *,
    opener: Any,
    url: str,
    token: str,
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    started = time.monotonic()
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
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
        "chat_sha256": (
            hashlib.sha256(chat.encode("utf-8")).hexdigest()
            if isinstance(chat, str) else None
        ),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Qualify A-F subject-model image input")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument(
        "--prompt",
        default=(
            "Inspect the attached screenshot. Reply with exactly RED if the large toolbar "
            "behind the word Search is red; otherwise reply with exactly NOT_RED."
        ),
    )
    parser.add_argument("--expected", default="RED")
    parser.add_argument("--model", action="append")
    args = parser.parse_args()

    config_path = args.config.resolve()
    image_path = args.image.resolve()
    config = read_json(config_path)
    runtime = config["runtime"]
    token_env = runtime["api_token_env"]
    token = os.environ.get(token_env, "").strip()
    if not token:
        raise RuntimeError(f"environment variable {token_env} is empty")
    requested = set(args.model or [row["id"] for row in config["models"]])
    known = {row["id"] for row in config["models"]}
    if not requested.issubset(known):
        raise ValueError(f"unknown model IDs: {sorted(requested - known)}")

    handlers: list[Any] = [urllib.request.ProxyHandler({})]
    if runtime["api_url"].startswith("https://") and not runtime.get("verify_ssl", False):
        handlers.append(urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    opener = urllib.request.build_opener(*handlers)
    encoded = _data_url(image_path)
    rows: list[dict[str, Any]] = []
    started = datetime.now(timezone.utc)
    for model in config["models"]:
        if model["id"] not in requested:
            continue
        payload = {
            "modelType": model["id"],
            "id": "",
            "message": args.prompt,
            "imageUrl": encoded,
        }
        raw = _call(
            opener=opener,
            url=runtime["api_url"],
            token=token,
            payload=payload,
            timeout=args.timeout,
        )
        exact = raw.get("chat") == args.expected
        rows.append({
            "model_id": model["id"],
            "feedback_image_verified": raw.get("success") is True and exact,
            "max_feedback_image_bytes": image_path.stat().st_size if exact else None,
            "request_fields": sorted(payload),
            "id_empty": payload["id"] == "",
            "expected_chat": args.expected,
            "expected_chat_match": exact,
            **raw,
        })

    report = {
        "schema_version": "e2-abcdef-v1-subject-image-preflight-1",
        "protocol_id": config["protocol_id"],
        "started_at_utc": started.isoformat(),
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_sha256": sha256_file(config_path),
        "api_url": runtime["api_url"],
        "api_token_env": token_env,
        "token_recorded": False,
        "fixture": {
            "path": str(image_path),
            "sha256": sha256_file(image_path),
            "bytes": image_path.stat().st_size,
            "expected_visual_answer": args.expected,
        },
        "models": rows,
        "all_models_qualified": (
            len(rows) == len(config["models"])
            and all(row["feedback_image_verified"] is True for row in rows)
        ),
        "formal_experiment_calls": 0,
        "probe_calls": len(rows),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output.resolve()),
        "all_models_qualified": report["all_models_qualified"],
        "models": {
            row["model_id"]: {
                "verified": row["feedback_image_verified"],
                "transport_status": row["transport_status"],
                "provider_code": row.get("provider_code"),
                "chat": row.get("chat"),
                "elapsed_ms": row["elapsed_ms"],
            }
            for row in rows
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
