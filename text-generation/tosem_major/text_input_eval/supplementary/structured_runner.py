"""Formal paired A/B/C/D execution engine for E2-structure-v1."""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, Callable

from .structured_state import fresh_model_payload, validate_event_chain
from .structured_storage import StructuredLedger, digest
from .structured_templates import render_feedback, sha256_json


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def exact_json_candidate(raw: str, required_ids: list[str]) -> tuple[str, dict[str, str] | None, str | None]:
    """Accept one JSON object, optionally in one fenced block plus explanation.

    The frozen source prompts require a JSON code fence and then ask the model
    to explain its selection strategy.  Preserve that explanation in the raw
    response audit, while extracting only a single, unambiguous ``json`` fence
    for candidate execution.  Bare responses remain strict JSON-only.
    """
    text = raw.strip()
    fence = "```json"
    if fence in text:
        if text.count(fence) != 1:
            return "parse_error", None, "response must contain exactly one JSON code fence"
        start = text.index(fence) + len(fence)
        end = text.find("```", start)
        if end < 0:
            return "parse_error", None, "JSON code fence is not closed"
        if fence in text[end + 3:] or "```" in text[end + 3:]:
            return "parse_error", None, "response contains multiple code fences"
        text = text[start:end].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return "parse_error", None, f"{exc.msg} at character {exc.pos}"
    if not isinstance(value, dict):
        return "schema_error", None, "top-level JSON value is not an object"
    actual = set(value)
    expected = set(required_ids)
    if actual != expected:
        return "schema_error", None, f"field mismatch: missing={sorted(expected-actual)}, extra={sorted(actual-expected)}"
    if any(not isinstance(item, str) for item in value.values()):
        return "schema_error", None, "all field values must be strings"
    return "valid", {key: value[key] for key in required_ids}, None


def load_base_prompt(repo_root: Path, case: dict[str, Any]) -> str:
    source = repo_root / case["source_files"]["prompt_collection"]
    rows = json.loads(source.read_text(encoding="utf-8"))
    row = rows[int(case["ordinal_in_source"]) - 1]
    keys = ("global", "component", "adjacent", "restrictive", "guiding")
    prompt = "\n".join(str(row[key]) for key in keys if str(row.get(key, "")).strip()).strip()
    if sha256_text(prompt) != case["extracted_prompt_sha256"]:
        raise ValueError(f"base prompt drift for {case['case_id']}")
    return prompt


class StructuredRunner:
    def __init__(
        self,
        *,
        repo_root: Path,
        output_dir: Path,
        config: dict[str, Any],
        registry: dict[str, Any],
        oracles: dict[str, dict[str, Any]],
        ledger: StructuredLedger,
        gateway: Any,
        environment: Any,
    ) -> None:
        self.repo_root = repo_root
        self.output_dir = output_dir
        self.config = config
        self.registry = registry
        self.oracles = oracles
        self.ledger = ledger
        self.gateway = gateway
        self.environment = environment
        self.models = {row["id"]: row for row in config["models"]}
        self.feedback = config["feedback"]
        self.refusal_exact_matches = set(config.get("runtime", {}).get("refusal_exact_matches", []))

    @staticmethod
    def _invoke(
        ledger: StructuredLedger,
        call_id: str,
        kind: str,
        dimensions: dict[str, Any],
        payload: dict[str, Any],
        function: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        cached = ledger.reserve_call(call_id, kind, dimensions, payload)
        if cached is not None:
            return cached
        # Exceptions deliberately leave a reserved record.  The external side
        # may already have completed, so this call must never be replayed.
        result = function()
        if not isinstance(result, dict):
            raise RuntimeError(f"{kind} returned a non-object result")
        ledger.finish_call(call_id, result)
        return result

    def _generate(
        self,
        call_id: str,
        dimensions: dict[str, Any],
        model: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:
        capacity_check = getattr(self.gateway, "check_capacity", None)
        if callable(capacity_check):
            try:
                capacity_check(prompt, model)
            except ValueError as exc:
                return {"run_status": "infrastructure_error", "error_type": type(exc).__name__}
        payload = fresh_model_payload(model["id"], prompt)
        return self._invoke(
            self.ledger,
            call_id,
            "model_generation",
            dimensions,
            payload,
            lambda: self.gateway.generate(model, prompt),
        )

    def plan(
        self,
        *,
        case_ids: set[str] | None = None,
        model_ids: set[str] | None = None,
        replicates: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        known_case_ids = {case["case_id"] for case in self.registry["cases"]}
        excluded_case_ids = {
            case["case_id"] for case in self.registry["cases"]
            if case.get("formal_applicability", {}).get("in_scope") is False
        }
        if case_ids is not None:
            unknown = case_ids - known_case_ids
            excluded = case_ids & excluded_case_ids
            if unknown:
                raise ValueError(f"unknown formal case selection: {sorted(unknown)}")
            if excluded:
                raise ValueError(
                    f"not-applicable cases cannot enter the formal schedule: {sorted(excluded)}"
                )
        selected = []
        for case in self.registry["cases"]:
            if case["case_id"] in excluded_case_ids:
                continue
            if case_ids is not None and case["case_id"] not in case_ids:
                continue
            for model in self.config["models"]:
                if model_ids is not None and model["id"] not in model_ids:
                    continue
                for replicate in range(1, int(self.config["replicates"]) + 1):
                    if replicates is not None and replicate not in replicates:
                        continue
                    dimensions = {
                        "protocol_id": self.config["protocol_id"],
                        "case_id": case["case_id"],
                        "task": case["task"],
                        "model": model["id"],
                        "replicate": replicate,
                        "representation": self.config["representation"]["primary"],
                    }
                    selected.append({"block_id": f"block-{digest(dimensions)}", "dimensions": dimensions})
        return selected

    def run(
        self,
        *,
        case_ids: set[str] | None = None,
        model_ids: set[str] | None = None,
        replicates: set[int] | None = None,
        max_blocks: int | None = None,
        turn_cap: int | None = None,
    ) -> dict[str, Any]:
        if turn_cap is not None and turn_cap < 1:
            raise ValueError("turn_cap must be a positive integer")
        self.ledger.ensure_resumable()
        case_map = {row["case_id"]: row for row in self.registry["cases"]}
        blocks = self.plan(case_ids=case_ids, model_ids=model_ids, replicates=replicates)
        finished = 0
        skipped = 0
        for block in blocks:
            dimensions = block["dimensions"]
            case = case_map[dimensions["case_id"]]
            trajectory_ids = {
                arm: f"trajectory-{digest({**dimensions, 'arm': arm})}"
                for arm in self.config["arms"]
            }
            if all(
                (row := self.ledger.trajectory(trajectory_id)) is not None and row["state"] == "done"
                for trajectory_id in trajectory_ids.values()
            ):
                skipped += 1
                continue
            if max_blocks is not None and finished >= max_blocks:
                break
            self._run_block(
                block["block_id"], dimensions, case, trajectory_ids,
                turn_cap=turn_cap,
            )
            finished += 1
        return {
            "planned_blocks": len(blocks),
            "executed_blocks": finished,
            "skipped_completed_blocks": skipped,
            "execution_cap": max_blocks,
            "turn_cap": turn_cap,
        }

    def _run_block(
        self,
        block_id: str,
        dimensions: dict[str, Any],
        case: dict[str, Any],
        trajectory_ids: dict[str, str],
        *,
        turn_cap: int | None = None,
    ) -> None:
        model = self.models[dimensions["model"]]
        oracle = self.oracles[case["case_id"]]
        base_prompt = load_base_prompt(self.repo_root, case)
        shared_call_id = f"model-{digest({**dimensions, 'role': 'shared_first'})}"
        shared = self._generate(
            shared_call_id,
            {**dimensions, "role": "shared_first"},
            model,
            base_prompt,
        )
        arms = list(self.config["arms"])
        random.Random(f"{self.config.get('schedule_seed', 0)}:{block_id}").shuffle(arms)
        for arm in arms:
            existing = self.ledger.trajectory(trajectory_ids[arm])
            if existing is not None and existing["state"] == "done":
                continue
            self._run_arm(
                trajectory_ids[arm], dimensions, case, oracle, model, arm, base_prompt,
                shared_call_id, shared, turn_cap=turn_cap,
            )

    def _run_arm(
        self,
        trajectory_id: str,
        dimensions: dict[str, Any],
        case: dict[str, Any],
        oracle: dict[str, Any],
        model: dict[str, Any],
        arm: str,
        base_prompt: str,
        shared_call_id: str,
        shared_result: dict[str, Any],
        *,
        turn_cap: int | None = None,
    ) -> None:
        arm_dimensions = {**dimensions, "arm": arm}
        configured_budget = int(self.config["budgets_including_first_turn"][case["task"]])
        budget = min(configured_budget, turn_cap) if turn_cap is not None else configured_budget
        self.ledger.begin_trajectory(trajectory_id, arm_dimensions)
        attempt_root = self.output_dir / "evidence" / trajectory_id
        attempt = {
            "attempt_id": trajectory_id,
            "case_id": case["case_id"],
            "arm": arm,
            "model": model["id"],
            "replicate": dimensions["replicate"],
            "oracle": oracle,
            "evidence_dir": str(attempt_root / "begin"),
            "command_timeout_seconds": self.config["runtime"]["environment_timeout_seconds"],
        }
        environment_kind = "predicate" if case["task"] == "invalid_bug" else "android"
        begin_call_id = f"{environment_kind}-{digest({**arm_dimensions, 'operation': 'begin_attempt'})}"
        reset_receipt = self._invoke(
            self.ledger,
            begin_call_id,
            f"{environment_kind}_begin_attempt",
            {**arm_dimensions, "operation": "begin_attempt"},
            {"case": case, "attempt": attempt},
            lambda: self.environment.begin_attempt(case, attempt),
        )
        begin_event = {
            "event": "attempt_begin",
            "attempt_id": trajectory_id,
            "case_id": case["case_id"],
            "arm": arm,
            "model": model["id"],
            "replicate": dimensions["replicate"],
            "initial_state_hash": reset_receipt.get("state_hash"),
            "oracle_version": oracle["version"],
            "reset_receipt": reset_receipt,
        }
        self.ledger.append_event(trajectory_id, begin_event)
        events = [begin_event]
        records: list[dict[str, Any]] = []
        prompt = base_prompt
        prompt_audit: dict[str, Any] = {
            "arm": arm,
            "shared_information_hash": None,
            "sent_prompt_hash": sha256_text(base_prompt),
        }
        model_result = shared_result
        physical_request_id = shared_call_id
        final_status = "incomplete"
        end_reason = "unexpected_end"
        for turn in range(1, budget + 1):
            # The paired first generation has one shared public request label;
            # physical App evidence remains isolated below each attempt root.
            logical_request_id = (
                f"{shared_call_id}-logical"
                if turn == 1
                else f"{trajectory_id}-turn-{turn:02d}"
            )
            if turn > 1:
                prompt, prompt_audit = render_feedback(
                    base_prompt,
                    arm,
                    records,
                    case["required_resource_ids"],
                    {"case_id": case["case_id"], "task": case["task"], "goal": "Complete the unchanged base-prompt task."},
                    evidence_root=attempt_root,
                    diagnostic_targets={
                        "oracle_version": oracle["version"],
                        "targets": oracle.get("diagnostic_targets", []),
                    },
                    history_limit=int(self.feedback["history_limit"]),
                    max_candidate_chars=int(self.feedback["max_candidate_chars"]),
                    max_public_error_chars=int(self.feedback["max_public_error_chars"]),
                )
                physical_request_id = f"model-{digest({**arm_dimensions, 'turn': turn})}"
                model_result = self._generate(
                    physical_request_id,
                    {**arm_dimensions, "turn": turn},
                    model,
                    prompt,
                )
            payload = fresh_model_payload(model["id"], prompt)
            request_event = {
                "event": "model_request",
                "turn": turn,
                "request_id": logical_request_id,
                "physical_request_id": physical_request_id,
                "prompt_hash": sha256_text(prompt),
                "visible_history_hash": prompt_audit.get("shared_information_hash") or sha256_text(""),
                "prompt_audit": prompt_audit,
                "payload": payload,
            }
            events.append(request_event)
            self.ledger.append_event(trajectory_id, request_event)
            if model_result.get("run_status") != "ok":
                output_event = {
                    "event": "output_check", "turn": turn, "request_id": logical_request_id,
                    "output_status": "infrastructure_error", "candidate_hash": None,
                    "error_type": model_result.get("error_type", "model_infrastructure_error"),
                }
                events.append(output_event)
                self.ledger.append_event(trajectory_id, output_event)
                final_status, end_reason = "incomplete", "model_infrastructure_error"
                break
            raw = str(model_result.get("raw_response", ""))
            if raw.strip() in self.refusal_exact_matches:
                output_status, candidate, parse_error = "refusal", None, "exact configured refusal"
            else:
                output_status, candidate, parse_error = exact_json_candidate(raw, case["required_resource_ids"])
            output_event = {
                "event": "output_check",
                "turn": turn,
                "request_id": logical_request_id,
                "output_status": output_status,
                "candidate_hash": sha256_json(candidate) if candidate is not None else None,
                "error": parse_error,
            }
            events.append(output_event)
            self.ledger.append_event(trajectory_id, output_event)
            if candidate is None:
                records.append({
                    "turn": turn,
                    "request_id": logical_request_id,
                    "raw_response": raw,
                    "candidate": None,
                    "output_status": output_status,
                    "behavior_verdict": None,
                    "diagnostics": [{
                        "type": output_status,
                        "detail": parse_error or output_status,
                        "public": True,
                        "source": "output_parser" if output_status == "parse_error" else "output_schema",
                        "checker_id": "exact_json_output_contract",
                        "evidence_ref": f"request:{logical_request_id}:output",
                    }],
                })
                if turn == budget:
                    final_status, end_reason = "completed_failure", f"budget_exhausted_{output_status}"
                    break
                continue
            request_root = attempt_root / logical_request_id
            execution_request = {
                "attempt_id": trajectory_id,
                "request_id": logical_request_id,
                "physical_request_id": physical_request_id,
                "turn": turn,
                "case": case,
                "oracle": oracle,
                "evidence_dir": str(request_root),
                "command_timeout_seconds": self.config["runtime"]["environment_timeout_seconds"],
            }
            execute_call_id = f"{environment_kind}-{digest({**arm_dimensions, 'turn': turn, 'operation': 'execute_turn'})}"
            execution = self._invoke(
                self.ledger,
                execute_call_id,
                f"{environment_kind}_execute_turn",
                {**arm_dimensions, "turn": turn, "operation": "execute_turn"},
                {"candidate": candidate, "request": execution_request},
                lambda: self.environment.execute_turn(candidate, execution_request),
            )
            execution_event = {
                "event": "execution",
                "turn": turn,
                "request_id": logical_request_id,
                "candidate_hash": sha256_json(candidate),
                "oracle_version": oracle["version"],
                "state_before_hash": execution.get("state_before_hash"),
                "state_after_hash": execution.get("state_after_hash"),
                "evidence_hash": execution.get("evidence_hash"),
                "verdict": execution.get("verdict"),
                "observation_complete": execution.get("observation_complete"),
                "observation": {
                    "run_status": "ok" if execution.get("observation_complete") else execution.get("verdict"),
                    "behavior_verdict": True if execution.get("verdict") == "pass" else False if execution.get("verdict") == "fail" else None,
                    "request_id": logical_request_id,
                    "candidate_hash": sha256_json(candidate),
                    "oracle_version": oracle["version"],
                    "evidence": execution.get("evidence", []),
                },
                "diagnostics": execution.get("diagnostics", []),
            }
            events.append(execution_event)
            self.ledger.append_event(trajectory_id, execution_event)
            verdict = execution_event["verdict"]
            if verdict == "pass":
                final_status, end_reason = "completed_success", "oracle_pass"
                break
            if verdict in {"unknown", "infrastructure_error"}:
                final_status, end_reason = "incomplete", f"{environment_kind}_{verdict}"
                break
            diagnostics = list(execution.get("diagnostics", []))
            diagnostics.append({
                "type": "predicate_false" if environment_kind == "predicate" else "goal_not_observed",
                "detail": (
                    "The frozen string predicate returned false for this candidate."
                    if environment_kind == "predicate"
                    else "The frozen task oracle did not observe the goal after this candidate."
                ),
                "public": True,
                "source": "task_oracle",
                "checker_id": (
                    "frozen_string_predicate"
                    if environment_kind == "predicate"
                    else "frozen_behavior_oracle"
                ),
                "evidence_ref": f"request:{logical_request_id}:verdict",
            })
            records.append({
                "turn": turn,
                "request_id": logical_request_id,
                "raw_response": raw,
                "candidate": candidate,
                "output_status": "valid",
                "behavior_verdict": False,
                "observation": execution_event["observation"],
                "diagnostics": diagnostics,
            })
            if turn == budget:
                final_status, end_reason = "completed_failure", "budget_exhausted_oracle_fail"
                break
            recovery_payload = {
                **execution,
                "turn": turn,
                "case": case,
                "oracle": oracle,
                "evidence_dir": str(request_root),
                "command_timeout_seconds": self.config["runtime"]["environment_timeout_seconds"],
            }
            recovery_call_id = f"{environment_kind}-{digest({**arm_dimensions, 'turn': turn, 'operation': 'recover_turn'})}"
            recovery = self._invoke(
                self.ledger,
                recovery_call_id,
                f"{environment_kind}_recover_turn",
                {**arm_dimensions, "turn": turn, "operation": "recover_turn"},
                {"execution": recovery_payload},
                lambda: self.environment.recover_turn(recovery_payload),
            )
            recovery_event = {"event": "recovery", "turn": turn, **recovery}
            events.append(recovery_event)
            self.ledger.append_event(trajectory_id, recovery_event)
        end_event = {
            "event": "trajectory_end",
            "turns_executed": sum(event.get("event") == "model_request" for event in events),
            "status": final_status,
            "reason": end_reason,
        }
        events.append(end_event)
        self.ledger.append_event(trajectory_id, end_event)
        audit = validate_event_chain(events, budget)
        self.ledger.finish_trajectory(trajectory_id, {
            **audit,
            "budget": budget,
            "physical_model_request_ids": [
                event["physical_request_id"] for event in events if event.get("event") == "model_request"
            ],
        })
