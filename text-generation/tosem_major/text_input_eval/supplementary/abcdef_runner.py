"""Crash-safe paired execution engine for the A-F feedback experiment."""
from __future__ import annotations

import hashlib
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .abcdef_contract import ARMS, PROTOCOL_ID, sha256_json
from .abcdef_templates import build_analyzer_context, render_feedback
from .structured_runner import exact_json_candidate, load_base_prompt
from .structured_storage import StructuredLedger, digest


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ABCDEFRunner:
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
        analyzer: Any,
    ) -> None:
        if config.get("protocol_id") != PROTOCOL_ID or tuple(config.get("arms", {})) != ARMS:
            raise ValueError("runner requires the frozen E2-abcdef-v2 A-F arm order")
        unit = config.get("experimental_unit", {})
        if (
            unit.get("trajectory_axes") != ["model", "case_id", "arm"]
            or unit.get("one_trajectory_per_axis_cell") is not True
            or unit.get("replicate_axis") is not False
            or "replicates" in config
        ):
            raise ValueError("runner requires the no-replicate E2-abcdef-v2 unit")
        self.repo_root = repo_root
        self.output_dir = output_dir
        self.config = config
        self.registry = registry
        self.oracles = oracles
        self.ledger = ledger
        self.gateway = gateway
        self.environment = environment
        self.analyzer = analyzer
        self.models = {row["id"]: row for row in config["models"]}
        self.feedback = config["feedback"]
        self.refusal_exact_matches = set(config.get("runtime", {}).get("refusal_exact_matches", []))
        self.stop_signal: dict[str, Any] | None = None

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
        started = time.monotonic()
        result = function()
        if not isinstance(result, dict):
            raise RuntimeError(f"{kind} returned a non-object result")
        result = dict(result)
        result.setdefault("elapsed_ms", round((time.monotonic() - started) * 1000))
        ledger.finish_call(call_id, result)
        return result

    def _generate(
        self,
        call_id: str,
        dimensions: dict[str, Any],
        model: dict[str, Any],
        prompt: str,
        attachments: list[Path],
    ) -> dict[str, Any]:
        payload = {
            "modelType": model["id"],
            "id": "",
            "message": prompt,
            "image": (
                {"path": str(attachments[0]), "sha256": hashlib.sha256(attachments[0].read_bytes()).hexdigest()}
                if attachments else None
            ),
        }
        return self._invoke(
            self.ledger,
            call_id,
            "subject_model_generation",
            dimensions,
            payload,
            lambda: self.gateway.generate(model, prompt, attachments),
        )

    def _analyze(
        self,
        *,
        trajectory_id: str,
        turn: int,
        dimensions: dict[str, Any],
        record: dict[str, Any],
        evidence_root: Path,
        case: dict[str, Any],
        base_prompt: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        context, image_path = build_analyzer_context(
            record,
            evidence_root=evidence_root,
            case_id=case["case_id"],
            task_goal=base_prompt,
        )
        call_id = f"analyzer-{digest({**dimensions, 'arm': 'F', 'turn': turn, 'role': 'diagnosis'})}"
        audit_payload = {
            "model": self.config["analyzer"]["model"],
            "reasoning_effort": self.config["analyzer"]["reasoning_effort"],
            "context_sha256": sha256_json(context),
            "context": context,
            "image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest() if image_path else None,
        }
        result = self._invoke(
            self.ledger,
            call_id,
            "external_feedback_analysis",
            {**dimensions, "arm": "F", "turn": turn},
            audit_payload,
            lambda: self.analyzer.analyze(context, image_path),
        )
        event = {
            "event": "feedback_analysis",
            "turn": turn,
            "call_id": call_id,
            "run_status": result.get("run_status"),
            "context_sha256": audit_payload["context_sha256"],
            "image_sha256": audit_payload["image_sha256"],
            "diagnosis_sha256": sha256_json(result["diagnosis"]) if result.get("run_status") == "ok" else None,
        }
        return result, event

    def plan(
        self,
        *,
        case_ids: set[str] | None = None,
        model_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        known = {row["case_id"] for row in self.registry["cases"]}
        excluded = {
            row["case_id"] for row in self.registry["cases"]
            if row.get("formal_applicability", {}).get("in_scope") is False
        }
        if case_ids is not None:
            if case_ids - known:
                raise ValueError(f"unknown case selection: {sorted(case_ids-known)}")
            if case_ids & excluded:
                raise ValueError(f"not-applicable cases cannot be scheduled: {sorted(case_ids&excluded)}")
        blocks = []
        for case in self.registry["cases"]:
            if case["case_id"] in excluded or (case_ids is not None and case["case_id"] not in case_ids):
                continue
            for model in self.config["models"]:
                if model_ids is not None and model["id"] not in model_ids:
                    continue
                dimensions = {
                    "protocol_id": PROTOCOL_ID,
                    "case_id": case["case_id"],
                    "task": case["task"],
                    "model": model["id"],
                    "representation": self.config["representation"]["primary"],
                }
                blocks.append({"block_id": f"block-{digest(dimensions)}", "dimensions": dimensions})
        return blocks

    def run(
        self,
        *,
        case_ids: set[str] | None = None,
        model_ids: set[str] | None = None,
        arm_ids: set[str] | None = None,
        max_blocks: int | None = None,
        turn_cap: int | None = None,
    ) -> dict[str, Any]:
        run_started = time.monotonic()
        self.ledger.ensure_resumable()
        unknown_arms = set() if arm_ids is None else arm_ids - set(ARMS)
        if unknown_arms:
            raise ValueError(f"unknown arm selection: {sorted(unknown_arms)}")
        selected_arms = tuple(arm for arm in ARMS if arm_ids is None or arm in arm_ids)
        if not selected_arms:
            raise ValueError("arm selection must not be empty")
        case_map = {row["case_id"]: row for row in self.registry["cases"]}
        blocks = self.plan(case_ids=case_ids, model_ids=model_ids)
        executed = skipped = preflight_failed = 0
        preflight_failures: list[dict[str, Any]] = []

        def summary() -> dict[str, Any]:
            return {
                "protocol_id": PROTOCOL_ID,
                "planned_blocks": len(blocks),
                "executed_blocks": executed,
                "skipped_completed_blocks": skipped,
                "preflight_failed_blocks": preflight_failed,
                "preflight_failures": preflight_failures,
                "execution_cap": max_blocks,
                "turn_cap": turn_cap,
                "selected_arms": list(selected_arms),
                "stop_signal": self.stop_signal,
                "elapsed_ms": round((time.monotonic() - run_started) * 1000),
            }

        for block in blocks:
            dimensions = block["dimensions"]
            trajectory_ids = {
                arm: f"trajectory-{digest({**dimensions, 'arm': arm})}"
                for arm in selected_arms
            }
            if all(
                (row := self.ledger.trajectory(trajectory_id)) is not None and row["state"] == "done"
                for trajectory_id in trajectory_ids.values()
            ):
                skipped += 1
                continue
            if max_blocks is not None and executed >= max_blocks:
                break
            case = case_map[dimensions["case_id"]]
            model = self.models[dimensions["model"]]
            requires_preflight = getattr(self.environment, "requires_block_preflight", None)
            preflight_block = getattr(self.environment, "preflight_block", None)
            if callable(requires_preflight) and requires_preflight(case):
                if not callable(preflight_block):
                    raise RuntimeError("environment requires a missing block preflight")
                preflight_call_id = f"host-preflight-{digest(dimensions)}"
                preflight = self._invoke(
                    self.ledger,
                    preflight_call_id,
                    "host_service_block_preflight",
                    {**dimensions, "operation": "block_preflight"},
                    {"case": case, "oracle": self.oracles[case["case_id"]]},
                    lambda: preflight_block(case, self.oracles[case["case_id"]]),
                )
                if preflight.get("run_status") != "ok":
                    preflight_failed += 1
                    preflight_failures.append({
                        "block_id": block["block_id"],
                        "case_id": dimensions["case_id"],
                        "model": dimensions["model"],
                        "call_id": preflight_call_id,
                        "error_type": preflight.get("error_type"),
                    })
                    executed += 1
                    continue
            base_prompt = load_base_prompt(self.repo_root, case)
            shared_call_id = f"model-{digest({**dimensions, 'role': 'shared_first'})}"
            shared = self._generate(
                shared_call_id,
                {**dimensions, "role": "shared_first"},
                model,
                base_prompt,
                [],
            )
            if shared.get("error_type") == "quota_exhausted":
                self.stop_signal = {
                    "reason": "subject_model_quota_exhausted",
                    "provider_role": "subject_model",
                    "provider_model": model["id"],
                    "model": model["id"],
                    "case_id": dimensions["case_id"],
                    "call_id": shared_call_id,
                }
                return summary()
            arms = list(ARMS)
            random.Random(f"{self.config.get('schedule_seed', 0)}:{block['block_id']}").shuffle(arms)
            arms = [arm for arm in arms if arm in selected_arms]
            for arm in arms:
                row = self.ledger.trajectory(trajectory_ids[arm])
                if row is not None and row["state"] == "done":
                    continue
                self._run_arm(
                    trajectory_ids[arm],
                    dimensions,
                    case,
                    self.oracles[case["case_id"]],
                    model,
                    arm,
                    base_prompt,
                    shared_call_id,
                    shared,
                    turn_cap=turn_cap,
                )
                if self.stop_signal is not None:
                    return summary()
            executed += 1
        return summary()

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
        turn_cap: int | None,
    ) -> None:
        trajectory_started = time.monotonic()
        trajectory_started_at = datetime.now(timezone.utc).isoformat()
        configured_budget = int(self.config["budgets_including_first_turn"][case["task"]])
        budget = min(configured_budget, turn_cap) if turn_cap is not None else configured_budget
        if budget < 1:
            raise ValueError("trajectory turn budget must be positive")
        arm_dimensions = {**dimensions, "arm": arm}
        self.ledger.begin_trajectory(trajectory_id, arm_dimensions)
        attempt_root = self.output_dir / "evidence" / trajectory_id
        attempt = {
            "attempt_id": trajectory_id,
            "case_id": case["case_id"],
            "arm": arm,
            "model": model["id"],
            "oracle": oracle,
            "evidence_dir": str(attempt_root / "begin"),
            "command_timeout_seconds": self.config["runtime"]["environment_timeout_seconds"],
        }
        environment_kind = "predicate" if case["task"] == "invalid_bug" else "android"
        begin_call_id = f"{environment_kind}-{digest({**arm_dimensions, 'operation': 'begin_attempt'})}"
        reset = self._invoke(
            self.ledger,
            begin_call_id,
            f"{environment_kind}_begin_attempt",
            {**arm_dimensions, "operation": "begin_attempt"},
            {"case": case, "attempt": attempt},
            lambda: self.environment.begin_attempt(case, attempt),
        )
        self.ledger.append_event(trajectory_id, {
            "event": "attempt_begin",
            "reset_receipt": reset,
            "oracle_version": oracle["version"],
        })
        records: list[dict[str, Any]] = []
        model_result = shared_result
        physical_request_id = shared_call_id
        final_status, end_reason = "incomplete", "unexpected_end"
        model_calls = executions = recoveries = analyzer_calls = 0
        if reset.get("run_status") == "infrastructure_error":
            result = {
                "protocol_id": PROTOCOL_ID,
                "case_id": case["case_id"],
                "arm": arm,
                "model": model["id"],
                "budget": budget,
                "status": "incomplete",
                "reason": f"{environment_kind}_begin_infrastructure_error",
                "model_calls": 0,
                "executions": 0,
                "recoveries": 0,
                "analyzer_calls": 0,
                "records": [],
                "started_at_utc": trajectory_started_at,
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": round((time.monotonic() - trajectory_started) * 1000),
            }
            self.ledger.append_event(trajectory_id, {"event": "trajectory_end", **result})
            self.ledger.finish_trajectory(trajectory_id, result)
            return
        for turn in range(1, budget + 1):
            logical_request_id = f"{trajectory_id}-turn-{turn:02d}"
            prompt, audit, attachments = base_prompt, {
                "arm": arm,
                "history_count": 0,
                "sent_prompt_sha256": sha256_text(base_prompt),
            }, []
            if turn > 1:
                diagnosis = None
                if arm == "F":
                    analysis, analysis_event = self._analyze(
                        trajectory_id=trajectory_id,
                        turn=turn - 1,
                        dimensions=dimensions,
                        record=records[-1],
                        evidence_root=attempt_root,
                        case=case,
                        base_prompt=base_prompt,
                    )
                    analyzer_calls += 1
                    self.ledger.append_event(trajectory_id, analysis_event)
                    if analysis.get("run_status") != "ok":
                        if analysis.get("error_type") == "quota_exhausted":
                            # Keep the legacy reason token until every already
                            # running device-queue process has been replaced:
                            # those processes use it as the global quota-stop
                            # fence.  The provider_role fields preserve the
                            # accurate GPT/analyzer provenance.
                            final_status, end_reason = (
                                "incomplete", "subject_model_quota_exhausted"
                            )
                            self.stop_signal = {
                                "reason": end_reason,
                                "provider_role": "analyzer",
                                "provider_model": self.config["analyzer"]["model"],
                                "model": model["id"],
                                "case_id": case["case_id"],
                                "arm": arm,
                                "turn": turn - 1,
                                "call_id": analysis_event["call_id"],
                            }
                        else:
                            final_status, end_reason = (
                                "incomplete",
                                str(analysis.get("run_status") or "analysis_error"),
                            )
                        break
                    diagnosis = analysis["diagnosis"]
                prompt, audit, attachments = render_feedback(
                    base_prompt,
                    arm,
                    records,
                    evidence_root=attempt_root,
                    analyzer_diagnosis=diagnosis,
                    history_limit=int(self.feedback["history_limit"]),
                    max_candidate_chars=int(self.feedback["max_candidate_chars"]),
                )
                physical_request_id = f"model-{digest({**arm_dimensions, 'turn': turn})}"
                model_result = self._generate(
                    physical_request_id,
                    {**arm_dimensions, "turn": turn},
                    model,
                    prompt,
                    attachments,
                )
            model_calls += 1
            self.ledger.append_event(trajectory_id, {
                "event": "model_request",
                "turn": turn,
                "request_id": logical_request_id,
                "physical_request_id": physical_request_id,
                "prompt_audit": audit,
                "attachment_sha256": (
                    hashlib.sha256(attachments[0].read_bytes()).hexdigest() if attachments else None
                ),
            })
            if model_result.get("run_status") != "ok":
                records.append({
                    "turn": turn,
                    "request_id": logical_request_id,
                    "raw_response": str(model_result.get("raw_response", "")),
                    "candidate": None,
                    "outcome": None,
                    "run_status": model_result.get("run_status") or "infrastructure_error",
                    "output_status": "infrastructure_error",
                    "parse_error": None,
                    "error_type": model_result.get("error_type"),
                    "oracle_version": oracle["version"],
                    "visual_feedback": None,
                })
                if model_result.get("error_type") == "quota_exhausted":
                    final_status, end_reason = "incomplete", "subject_model_quota_exhausted"
                    self.stop_signal = {
                        "reason": end_reason,
                        "provider_role": "subject_model",
                        "provider_model": model["id"],
                        "model": model["id"],
                        "case_id": case["case_id"],
                        "arm": arm,
                        "turn": turn,
                        "call_id": physical_request_id,
                    }
                else:
                    final_status, end_reason = "incomplete", "subject_model_infrastructure_error"
                break
            raw = str(model_result.get("raw_response", ""))
            if raw.strip() in self.refusal_exact_matches:
                output_status, candidate, parse_error = "refusal", None, "exact configured refusal"
            else:
                output_status, candidate, parse_error = exact_json_candidate(raw, case["required_resource_ids"])
            self.ledger.append_event(trajectory_id, {
                "event": "output_check",
                "turn": turn,
                "request_id": logical_request_id,
                "output_status": output_status,
                "parse_error": parse_error,
                "candidate_sha256": sha256_json(candidate) if candidate is not None else None,
            })
            if candidate is None:
                records.append({
                    "turn": turn,
                    "request_id": logical_request_id,
                    "raw_response": raw,
                    "candidate": None,
                    "outcome": "FAIL",
                    "run_status": "invalid_output",
                    "output_status": output_status,
                    "parse_error": parse_error,
                    "oracle_version": oracle["version"],
                    "visual_feedback": None,
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
                "arm": arm,
                "turn": turn,
                "case": case,
                "oracle": oracle,
                "evidence_dir": str(request_root),
                "trajectory_evidence_root": str(attempt_root),
                "capture_protocol": self.config["capture_protocol"],
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
            executions += 1
            verdict = execution.get("verdict")
            if verdict not in {"pass", "fail", "unknown", "infrastructure_error"}:
                records.append({
                    "turn": turn,
                    "request_id": logical_request_id,
                    "raw_response": raw,
                    "candidate": candidate,
                    "outcome": None,
                    "run_status": "infrastructure_error",
                    "output_status": output_status,
                    "parse_error": None,
                    "error_type": "invalid_environment_verdict",
                    "oracle_version": oracle["version"],
                    "visual_feedback": execution.get("visual_feedback"),
                })
                final_status, end_reason = "incomplete", "invalid_environment_verdict"
                break
            self.ledger.append_event(trajectory_id, {
                "event": "execution",
                "turn": turn,
                "request_id": logical_request_id,
                "candidate_sha256": sha256_json(candidate),
                "oracle_version": oracle["version"],
                "verdict_oracle_version": execution.get("verdict_oracle_version", oracle["version"]),
                "visual_observation_oracle_version": execution.get(
                    "visual_observation_oracle_version", oracle["version"]
                ),
                "verdict": verdict,
                "error_type": execution.get("error_type"),
                "error_message": execution.get("error_message"),
                "observation_complete": execution.get("observation_complete"),
                "evidence_hash": execution.get("evidence_hash"),
                "visual_feedback": execution.get("visual_feedback"),
            })
            if verdict == "infrastructure_error":
                records.append({
                    "turn": turn,
                    "request_id": logical_request_id,
                    "raw_response": raw,
                    "candidate": candidate,
                    "outcome": None,
                    "run_status": "infrastructure_error",
                    "output_status": output_status,
                    "parse_error": None,
                    "error_type": execution.get("error_type"),
                    "oracle_version": oracle["version"],
                    "visual_feedback": execution.get("visual_feedback"),
                })
                final_status, end_reason = "incomplete", f"{environment_kind}_infrastructure_error"
                break
            outcome = {"pass": "PASS", "fail": "FAIL", "unknown": "UNKNOWN"}[verdict]
            record = {
                "turn": turn,
                "request_id": logical_request_id,
                "raw_response": raw,
                "candidate": candidate,
                "outcome": outcome,
                "run_status": "ok",
                "output_status": output_status,
                "parse_error": None,
                "oracle_version": oracle["version"],
                "verdict_oracle_version": execution.get("verdict_oracle_version", oracle["version"]),
                "visual_observation_oracle_version": execution.get(
                    "visual_observation_oracle_version", oracle["version"]
                ),
                "visual_feedback": execution.get("visual_feedback"),
            }
            records.append(record)
            if verdict == "pass":
                final_status, end_reason = "completed_success", "oracle_pass"
                break
            if arm in {"D", "E", "F"} and record["visual_feedback"] is None:
                final_status, end_reason = "incomplete", "required_visual_feedback_missing"
                break
            if turn == budget:
                final_status = "completed_unknown" if verdict == "unknown" else "completed_failure"
                end_reason = "budget_exhausted_unknown" if verdict == "unknown" else "budget_exhausted_oracle_fail"
                break
            recovery_payload = {
                **execution,
                "arm": arm,
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
            recoveries += 1
            self.ledger.append_event(trajectory_id, {"event": "recovery", "turn": turn, **recovery})
            if recovery.get("run_status") == "infrastructure_error":
                final_status, end_reason = "incomplete", f"{environment_kind}_recovery_infrastructure_error"
                break
        result = {
            "protocol_id": PROTOCOL_ID,
            "case_id": case["case_id"],
            "arm": arm,
            "model": model["id"],
            "budget": budget,
            "first_success_turn": next(
                (
                    int(record["turn"]) for record in records
                    if record.get("outcome") == "PASS"
                ),
                None,
            ),
            "status": final_status,
            "reason": end_reason,
            "model_calls": model_calls,
            "executions": executions,
            "recoveries": recoveries,
            "analyzer_calls": analyzer_calls,
            "records": records,
            "started_at_utc": trajectory_started_at,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": round((time.monotonic() - trajectory_started) * 1000),
        }
        self.ledger.append_event(trajectory_id, {"event": "trajectory_end", **result})
        self.ledger.finish_trajectory(trajectory_id, result)
