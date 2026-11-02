"""E1 independent generations; E2 shared first draw and four isolated arms."""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

from text_input_eval.services.one_shot import LLMClient
from .context import Case, digest, prepare
from .protocol import ARMS, Evaluator, empty_evaluation, feedback_prompt
from .storage import Ledger
from .usage import normalize_usage


class CapacityGuard:
    def __init__(self, models: list[dict]):
        self.models = {m["id"]: m for m in models}
        self.tokenizers = {}

    def check(self, model: str, message: str) -> dict:
        config = self.models[model]
        size = len(message.encode("utf-8"))
        out = {"prompt_bytes": size, "token_count": None, "token_method": "unavailable",
               "allowed": size <= config["max_prompt_bytes"], "reason": None}
        if not out["allowed"]:
            out["reason"] = "prompt_byte_budget_exceeded"
        if config.get("tokenizer_json"):
            from tokenizers import Tokenizer
            if model not in self.tokenizers:
                self.tokenizers[model] = Tokenizer.from_file(config["tokenizer_json"])
            count = len(self.tokenizers[model].encode(message).ids)
            # Counts message text, not opaque gateway chat-template/system tokens.
            out.update(token_count=count, token_method="local_tokenizer_message_only")
            if count + config["reserved_tokens"] > config["context_window_tokens"]:
                out.update(allowed=False, reason="context_budget_exceeded")
        return out


class Gateway:
    """Use the response-ID API contract without conversation reuse or retries."""
    def __init__(self, models, api_key, base_url, timeout):
        self.clients = {m["id"]: LLMClient(api_key, m["id"], base_url, timeout, verify_ssl=True) for m in models}

    def __call__(self, model, message):
        return self.clients[model].chat_completion(message=message, cursor_id="")


class Runner:
    def __init__(self, config: dict, cases: list[Case], ledger: Ledger, client,
                 evaluator: Evaluator, output: Path):
        self.config, self.cases, self.ledger = config, cases, ledger
        self.client, self.evaluator, self.output = client, evaluator, output
        self.guard = CapacityGuard(config["models"])
        self.new_calls = 0
        self.max_calls = None

    def generate(self, case, prepared, dimensions, prompt):
        request_id = digest(dimensions)
        payload = {"modelType": dimensions["model"], "id": "", "message": prompt}
        saved = self.ledger.get(request_id)
        if saved:
            if json.loads(saved["payload"]) != payload:
                raise ValueError("Resume request differs from recorded payload")
            if saved["state"] == "done":
                return json.loads(saved["result"])
            if saved["state"] != "responded":
                raise RuntimeError(f"Uncertain request {request_id}; no automatic replay")
            response_record = json.loads(saved["response"])
        else:
            capacity = self.guard.check(dimensions["model"], prompt)
            if self.max_calls is not None and self.new_calls >= self.max_calls and capacity["allowed"]:
                raise CallLimitReached()
            self.ledger.reserve(request_id, dimensions, payload)
            if not capacity["allowed"]:
                result = {"request_id": request_id, "evaluation": empty_evaluation("capacity_blocked", capacity["reason"]),
                          "chat": "", "submitted": False, "capacity": capacity, "elapsed_ms": 0, "usage": None}
                self.ledger.finish(request_id, result)
                return result
            started = time.perf_counter()
            self.new_calls += 1
            try:
                response = self.client(dimensions["model"], prompt)
            except Exception as exc:
                # No raw exception repr: some HTTP clients include request headers.
                result = {"request_id": request_id, "evaluation": empty_evaluation("api_error", type(exc).__name__),
                          "chat": "", "submitted": True, "capacity": capacity, "usage": None,
                          "elapsed_ms": round((time.perf_counter() - started) * 1000)}
                self.ledger.finish(request_id, result)
                return result
            response_record = {"response": response, "capacity": capacity,
                               "elapsed_ms": round((time.perf_counter() - started) * 1000)}
            # Storage failures leave a reserved uncertain record; never relabel
            # them as an API error or retry an already submitted generation.
            self.ledger.responded(request_id, response_record)
        response = response_record["response"]
        data = response.get("data") if isinstance(response, dict) else None
        data = data if isinstance(data, dict) else {}
        chat = data.get("chat")
        result = {"request_id": request_id, "chat": chat if isinstance(chat, str) else "",
                  "submitted": True, "capacity": response_record["capacity"],
                  "elapsed_ms": response_record["elapsed_ms"], "response_id": data.get("id"),
                  "usage": normalize_usage(response)}
        self.ledger.evaluating(request_id)
        evaluation_started = time.perf_counter()
        if not isinstance(response, dict) or response.get("success") is False or response.get("error"):
            ev = empty_evaluation("api_error", "provider_error_response")
        elif not isinstance(chat, str):
            ev = empty_evaluation("api_error", "missing_chat_field")
        elif chat.strip() in self.config["refusal_exact_matches"]:
            ev = empty_evaluation("ok")
            ev.update(output_status="refusal", goal_verdict=False)
        else:
            try:
                ev = self.evaluator.evaluate(case, prepared, chat, request_id, self.output / "evidence" / request_id)
            except Exception as exc:
                ev = empty_evaluation("checker_error", type(exc).__name__)
        result["evaluation"] = ev
        result["evaluation_elapsed_ms"] = round((time.perf_counter() - evaluation_started) * 1000)
        self.ledger.finish(request_id, result)
        return result

    @staticmethod
    def stopped(history, budget):
        ev = history[-1]["evaluation"]
        if ev["run_status"] != "ok" or ev["goal_verdict"] is None:
            return "incomplete"
        if ev["goal_verdict"] is True:
            return "success"
        if ev["output_status"] == "refusal":
            return "refusal"
        if len(history) >= budget:
            return "budget_exhausted"
        return None

    def save_trajectory(self, dimensions, history, budget, status):
        self.ledger.trajectory(dimensions, {"status": status, "budget": budget,
            "request_ids": [r["request_id"] for r in history],
            "first_success_turn": next((i for i, r in enumerate(history, 1)
                                         if r["evaluation"]["goal_verdict"] is True), None),
            "shared_first_failed": history[0]["evaluation"]["goal_verdict"] is False,
            "endpoint": self.evaluator.endpoint})

    def run(self, max_calls=None):
        self.max_calls = max_calls
        self.ledger.ensure_resumable()
        try:
            for experiment in self.config["experiments"]:
                for case in self.cases:
                    if experiment == "E1" and case.task != "valid_pptr":
                        continue
                    for model in self.config["models"]:
                        for repeat in range(1, self.config["replicates"] + 1):
                            base = {"experiment": experiment, "case_id": case.case_id, "aut": case.aut,
                                    "task": case.task, "model": model["id"], "replicate_id": repeat}
                            if experiment == "E1":
                                self.run_e1(case, base)
                            else:
                                self.run_e2(case, base)
            return "finished"
        except CallLimitReached:
            return "paused_at_call_limit"

    def run_e1(self, case, base):
        for representation in self.config["e1"]["representations"]:
            for condition in self.config["e1"]["conditions"]:
                dimensions = {**base, "representation": representation, "condition": condition, "arm": "single"}
                prepared = prepare(case, representation, condition, self.config["e1"]["noise_nodes"])
                if prepared["exclusion_reason"]:
                    self.ledger.trajectory(dimensions, {"status": "excluded", "reason": prepared["exclusion_reason"],
                        "request_ids": [], "first_success_turn": None, "endpoint": self.evaluator.endpoint,
                        "budget": 1, "shared_first_failed": False})
                    continue
                result = self.generate(case, prepared, {**dimensions, "turn": 1}, prepared["prompt"])
                self.save_trajectory(dimensions, [result], 1, self.stopped([result], 1))

    def run_e2(self, case, base):
        prepared = prepare(case, "extracted", "original")
        public_specs = self.evaluator.oracle_cases.get(case.case_id, {}).get("public_specs", [])
        if public_specs:
            prepared["prompt"] += "\nPublic specifications:\n" + json.dumps(public_specs, ensure_ascii=False)
        dimensions = {**base, "representation": "extracted", "condition": "original"}
        budget = self.config["e2"]["budgets"][case.task]
        first = self.generate(case, prepared, {**dimensions, "arm": "shared", "turn": 1}, prepared["prompt"])
        arms = list(ARMS)
        random.Random(digest([self.config["schedule_seed"], dimensions])).shuffle(arms)
        for arm in arms:
            history = [first]
            while not (status := self.stopped(history, budget)):
                prompt = feedback_prompt(prepared["prompt"], arm, history, self.config["e2"]["history_limit"],
                    self.config["e2"]["max_candidate_chars"], self.config["e2"]["max_diagnostic_chars"])
                history.append(self.generate(case, prepared, {**dimensions, "arm": arm, "turn": len(history) + 1}, prompt))
            self.save_trajectory({**dimensions, "arm": arm}, history, budget, status)


class CallLimitReached(Exception):
    pass
