"""C/D experiment using the revised response-id cursor contract.

Each trajectory owns an id chain, including its first draw. No cursor forking,
implicit retry, image-free fresh-context continuation or A/B regeneration.
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import re
import time
from dataclasses import replace
from pathlib import Path

from text_input_eval.services.feedback_based import LLMClient, PromptGenerator, is_refusal_response
from .context import digest, encoded
from .protocol import empty_evaluation
from .runner import CapacityGuard, CallLimitReached, Runner
from .session_feedback import next_feedback
from .usage import normalize_usage

VARIANTS = {"extracted": "full", "xml": "xml", "vision": "vision"}


def prepare_session(case, representation: str, cache_root: Path) -> dict:
    if representation not in VARIANTS:
        raise ValueError("Unknown representation")
    ctx = case.context
    image_sha = None
    if representation == "vision":
        directory = "pptr" if case.task == "valid_pptr" else "bug"
        raw = "".join((cache_root / directory / f"base64_{ctx.target_id}.txt").read_text().split())
        image = base64.b64decode(raw, validate=True)
        if not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Legacy vision prompt requires a cached PNG")
        image_sha = hashlib.sha256(image).hexdigest()
        ctx = replace(ctx, image_64=raw)
    prompt, image_url = PromptGenerator.build_prompt(ctx, representation, VARIANTS[representation])
    return {"prompt": prompt, "image_url": image_url, "image_sha256": image_sha,
            "prompt_hash": digest(prompt), "resource_ids": ctx.resource_ids,
            "inverse_map": {}, "output_check_version": "supplementary_json_text_v1"}


class SessionGateway:
    def __init__(self, models, api_key, base_url, timeout):
        self.clients = {m["id"]: LLMClient(api_key, m["id"], base_url, timeout, verify_ssl=True) for m in models}

    def __call__(self, payload):
        return self.clients[payload["modelType"]].post_payload(payload)


class SessionCapacityGuard:
    def __init__(self, models):
        self.models = {m["id"]: m for m in models}
        self.text_guard = CapacityGuard([{**m, "max_prompt_bytes": m["max_visible_text_bytes"]} for m in models])

    def check(self, payload, transcript: list[str], has_image: bool):
        # Count the whole visible id-chain context, including previous feedback
        # messages and replies. Five explicit records do not truncate the chain.
        capacity = self.text_guard.check(payload["modelType"], "\n".join(transcript + [payload["message"]]))
        capacity["visible_context_text_bytes"] = capacity.pop("prompt_bytes")
        capacity["request_bytes"] = len(encoded(payload).encode("utf-8"))
        capacity["conversation_contains_image"] = has_image
        capacity["scope"] = "visible_id_chain_text_only; gateway template and image tokens unknown"
        capacity["full_context_fit_verified"] = False
        if capacity["request_bytes"] > self.models[payload["modelType"]]["max_request_bytes"]:
            capacity.update(allowed=False, reason="request_byte_budget_exceeded")
        return capacity


def provider_error_kind(response):
    """Persist the raw response locally; return only a non-sensitive category."""
    if not isinstance(response, dict):
        return "malformed_response"
    if response.get("success") is not False and not response.get("error"):
        return None
    detail = encoded({k: response.get(k) for k in ("msg", "message", "error", "code")}).lower()
    if re.search(r"context_length_exceeded|maximum context length|too many input tokens|"
                 r"input.{0,50}tokens?.{0,50}exceed|exceed.{0,50}(context|token).{0,30}(limit|length)", detail):
        return "provider_context_limit"
    if re.search(r"(session|cursor|conversation).{0,30}(expired|not found|invalid)|"
                 r"invalid.{0,10}(session|cursor|conversation)", detail):
        return "provider_cursor_unavailable"
    return "provider_error_response"


class SessionRunner:
    def __init__(self, config, cases, ledger, client, evaluator, output: Path):
        if config["endpoint"] != evaluator.endpoint:
            raise ValueError("Configured and actual oracle endpoints differ")
        if "D" in config["arms"] and evaluator.endpoint != "behavior":
            raise ValueError("D requires an execution evidence endpoint")
        self.config, self.cases, self.ledger = config, cases, ledger
        self.client, self.evaluator, self.output = client, evaluator, output
        self.guard = SessionCapacityGuard(config["models"])
        self.new_calls, self.max_calls = 0, None
        self.prepared = {}
        self.ledger.db.execute("CREATE TABLE IF NOT EXISTS cursor_owners (cursor_id TEXT PRIMARY KEY, owner TEXT NOT NULL)")
        self.ledger.db.commit()

    def claim_cursor(self, cursor_id, owner):
        if not cursor_id:
            return True
        with self.ledger.db:
            self.ledger.db.execute("INSERT OR IGNORE INTO cursor_owners VALUES (?,?)", (cursor_id, owner))
        actual = self.ledger.db.execute("SELECT owner FROM cursor_owners WHERE cursor_id=?", (cursor_id,)).fetchone()[0]
        return actual == owner

    def generate(self, case, prepared, dims, payload, transcript, feedback_audit):
        request_id = digest(dims)
        owner = digest({k: v for k, v in dims.items() if k != "turn"})
        saved = self.ledger.get(request_id)
        if saved:
            if json.loads(saved["payload"]) != payload:
                raise ValueError("Resume payload changed; automatic replacement is disabled")
            if saved["state"] == "done":
                return json.loads(saved["result"])
            if saved["state"] != "responded":
                raise RuntimeError("Uncertain request; automatic model/app replay is disabled")
            received = json.loads(saved["response"])
        else:
            capacity = self.guard.check(payload, transcript, bool(prepared["image_url"]))
            if capacity["allowed"] and self.max_calls is not None and self.new_calls >= self.max_calls:
                raise CallLimitReached()
            self.ledger.reserve(request_id, dims, payload)
            if not capacity["allowed"]:
                result = {"request_id": request_id, "chat": "", "submitted": False, "usage": None,
                          "cursor_id": payload["id"], "elapsed_ms": 0, "capacity": capacity,
                          "feedback_audit": feedback_audit,
                          "evaluation": empty_evaluation("capacity_blocked", capacity["reason"])}
                self.ledger.finish(request_id, result)
                return result
            started = time.perf_counter()
            self.new_calls += 1
            try:
                response = self.client(payload)
            except Exception as exc:
                result = {"request_id": request_id, "chat": "", "submitted": True, "usage": None,
                          "cursor_id": payload["id"], "capacity": capacity, "feedback_audit": feedback_audit,
                          "elapsed_ms": round((time.perf_counter() - started) * 1000),
                          "evaluation": empty_evaluation("api_error", type(exc).__name__)}
                self.ledger.finish(request_id, result)
                return result
            received = {"response": response, "capacity": capacity, "feedback_audit": feedback_audit,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000)}
            self.ledger.responded(request_id, received)
        response = received["response"]
        data = response.get("data", {}) if isinstance(response, dict) else {}
        data = data if isinstance(data, dict) else {}
        chat = data.get("chat")
        # The revised gateway uses response data.id as the cursor carried by
        # the next request's id field. data.sessionId is audit metadata only.
        response_cursor_id = str(data.get("id") or "").strip()
        response_session_id = str(data.get("sessionId") or "").strip()
        cursor_id = response_cursor_id
        result = {"request_id": request_id, "chat": chat if isinstance(chat, str) else "", "submitted": True,
            "cursor_id": cursor_id, "response_cursor_id": response_cursor_id,
            "response_session_id": response_session_id, "request_cursor_id": payload["id"],
            "capacity": received["capacity"], "elapsed_ms": received["elapsed_ms"],
            "feedback_audit": received.get("feedback_audit"),
            "usage": normalize_usage(response)}
        self.ledger.evaluating(request_id)
        started = time.perf_counter()
        error = provider_error_kind(response)
        if error:
            status = "capacity_blocked" if error == "provider_context_limit" else "api_error"
            ev = empty_evaluation(status, error)
        elif not self.claim_cursor(cursor_id, owner):
            ev = empty_evaluation("session_error", "cross_trajectory_cursor_collision")
            with self.ledger.db:
                self.ledger.db.execute("INSERT OR REPLACE INTO metadata VALUES ('cursor_integrity_error', ?)",
                                       (encoded({"request_id": request_id, "owner": owner}),))
        elif not isinstance(chat, str) or not chat.strip():
            ev = empty_evaluation("api_error", "missing_chat_field")
        elif is_refusal_response(chat) or chat.strip() in self.config["refusal_exact_matches"]:
            ev = empty_evaluation("ok")
            ev.update(output_status="refusal", goal_verdict=False)
        else:
            try:
                ev = self.evaluator.evaluate(case, prepared, chat, request_id, self.output / "evidence" / request_id)
            except Exception as exc:
                ev = empty_evaluation("checker_error", type(exc).__name__)
        result["evaluation"] = ev
        result["evaluation_elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        self.ledger.finish(request_id, result)
        return result

    def save(self, dims, history, status, reason=None):
        self.ledger.trajectory(dims, {"origin": "new_session_cd", "endpoint": self.evaluator.endpoint,
            "budget": self.config["budgets"][dims["task"]], "status": status, "reason": reason,
            "request_ids": [r["request_id"] for r in history],
            "first_failed": bool(history) and history[0]["evaluation"]["goal_verdict"] is False,
            "first_success_turn": next((i for i, r in enumerate(history, 1)
                                        if r["evaluation"]["goal_verdict"] is True), None),
            "first_draw_shared": False})

    def run_trajectory(self, case, dims):
        previous = self.ledger.db.execute("SELECT result FROM trajectories WHERE id=?", (digest(dims),)).fetchone()
        if previous and json.loads(previous[0])["status"] != "paused":
            return
        key = (case.case_id, dims["representation"])
        if key not in self.prepared:
            self.prepared[key] = prepare_session(case, dims["representation"], Path(self.config["cache_root"]))
        prepared = self.prepared[key]
        history, transcript, cursor_id = [], [], ""
        budget = self.config["budgets"][case.task]
        for turn in range(1, budget + 1):
            audit = None
            if turn == 1:
                message = prepared["prompt"]
            else:
                message, audit = next_feedback(case, dims["arm"], history, self.evaluator.endpoint,
                    self.evaluator.oracle_cases.get(case.case_id, {}), self.output / "evidence")
            payload = {"modelType": dims["model"], "id": cursor_id, "message": message}
            if turn == 1 and prepared["image_url"]:
                payload["imageUrl"] = prepared["image_url"]
            try:
                result = self.generate(case, prepared, {**dims, "turn": turn}, payload, transcript, audit)
            except CallLimitReached:
                self.save(dims, history, "paused", "call_limit")
                raise
            history.append(result)
            transcript.extend((message, result["chat"]))
            cursor_id = result.get("cursor_id", "")
            status = Runner.stopped(history, budget)
            reason = result["evaluation"].get("error_type")
            if not status and not cursor_id:
                status, reason = "incomplete", "missing_cursor_id_for_continuation"
            if status:
                self.save(dims, history, status, reason)
                if reason == "cross_trajectory_cursor_collision":
                    raise RuntimeError("Gateway reused a response cursor across trajectories; the run is stopped")
                return

    def run(self, max_calls=None):
        self.max_calls = max_calls
        self.ledger.ensure_resumable()
        if self.ledger.db.execute("SELECT value FROM metadata WHERE key='cursor_integrity_error'").fetchone():
            raise RuntimeError("Prior cross-trajectory cursor collision requires reconciliation; resume is disabled")
        schedule = []
        for case in self.cases:
            for model in self.config["models"]:
                for representation in self.config["representations"]:
                    for repeat in range(1, self.config["replicates"] + 1):
                        for arm in self.config["arms"]:
                            schedule.append((case, {"experiment": "RQ2_SESSION_CD", "case_id": case.case_id,
                                "task": case.task, "aut": case.aut, "model": model["id"],
                                "representation": representation, "replicate_id": repeat, "arm": arm}))
        random.Random(self.config["schedule_seed"]).shuffle(schedule)
        try:
            for case, dims in schedule:
                self.run_trajectory(case, dims)
        except CallLimitReached:
            return "paused_at_call_limit"
        return "finished"
