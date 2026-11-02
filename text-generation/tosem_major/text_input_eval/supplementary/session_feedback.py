"""Same-information restructuring of B; evidence-bound augmentation for D."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

from text_input_eval.services.feedback_based import FeedbackBuilder
from .context import digest, encoded


def failure_record(case, result: dict, turn: int, endpoint: str) -> dict:
    """Expose checks, never private validator predicates or adapter exceptions."""
    ev = result["evaluation"]
    if ev["run_status"] != "ok" or ev["goal_verdict"] is not False:
        raise ValueError("Only definite task failures can enter feedback")
    kind = {"parse_error": "syntax", "schema_error": "schema", "refusal": "refusal"}.get(
        ev["output_status"], "semantic")
    if kind in ("syntax", "schema"):
        # Parser/schema diagnostics are local output checks already available to B.
        details = [d["detail"] for d in ev.get("diagnostics", [])
                   if d.get("checker_id") == "supplementary_output_v1"
                   and d.get("source") in ("output_parser", "output_schema")
                   and d.get("evidence_ref") == f"request:{result['request_id']}"]
        detail = "\n".join(details) or "Output does not satisfy the required JSON format."
    elif kind == "refusal":
        detail = "Model returned a refusal instead of a JSON object."
    elif endpoint == "rule":
        task = "valid_input" if case.task == "valid_pptr" else "invalid_bug"
        detail = f"Validator returned false. case_id={case.case_id}, validator_task={task}"
    else:
        detail = "The task-specific execution oracle returned false. The cause is unknown."
    return {"turn": turn, "parsed_input_json": ev.get("canonical_candidate") if kind == "semantic" else None,
            "parse_success": int(kind == "semantic"), "eval_success": 0,
            "error_type": kind, "error_detail": detail}


def structured_feedback(ctx, failed_turns: list[dict]) -> tuple[str, dict]:
    """Use B's exact instructions, five-turn history and 500-char detail limit.

    Extract static sections from an inert template so candidate/error contents
    cannot masquerade as delimiters. C adds no repair rule or diagnosis to B.
    """
    if not failed_turns:
        raise ValueError("Feedback requires a failed turn")
    last = failed_turns[-1]
    sentinel = "__LATEST_ERROR_PLACEHOLDER__"
    template = FeedbackBuilder.build_feedback(ctx=ctx, failed_turns=[],
        error_type=last["error_type"], error_detail=sentinel)
    names = ("FEEDBACK", "TASK GOAL", "FAILED HISTORY", "LATEST ERROR DETAIL",
             "REGENERATION REQUIREMENTS", "REQUIRED RESOURCE IDS", "REQUIRED JSON SCHEMA")
    sections = {}
    for i, name in enumerate(names):
        text = template.split(f"[{name}]\n", 1)[1]
        sections[name] = text.split(f"\n\n[{names[i + 1]}]\n", 1)[0] if i + 1 < len(names) else text
    visible = []
    for row in failed_turns[-5:]:
        detail = str(row.get("error_detail") or "")
        if len(detail) > 500:
            detail = detail[:500] + "\n...[truncated]"
        visible.append({"turn": row.get("turn"), "candidate": row.get("parsed_input_json"),
            "checks": {"parse_success": row.get("parse_success"), "eval_success": row.get("eval_success")},
            "known_reason": {"error_type": row.get("error_type"), "error_detail": detail}})
    facts = {"task_goal": sections["TASK GOAL"], "candidate_check_history": visible,
             "latest_error_detail": last["error_detail"], "problem": sections["FEEDBACK"],
             "next_turn_requirements": sections["REGENERATION REQUIREMENTS"],
             "required_resource_ids": sections["REQUIRED RESOURCE IDS"],
             "required_json_schema": sections["REQUIRED JSON SCHEMA"]}
    original = FeedbackBuilder.build_feedback(ctx=ctx, failed_turns=failed_turns,
        error_type=last["error_type"], error_detail=last["error_detail"])
    prompt = "[STRUCTURED FEEDBACK]\n" + json.dumps(facts, ensure_ascii=False, indent=2)
    return prompt, {"b_reference_prompt": original, "shared_information": facts,
                    "shared_information_hash": digest(facts), "c_prompt_hash": digest(prompt)}


def verified_ui_evidence(result: dict, oracle: dict, evidence_root: Path) -> tuple[list[dict], list[str]]:
    """Accept only current, declared UI messages actually present in saved XML.

    This verifies provenance/text, not whether a message explains the root cause.
    Private bug triggers, arbitrary diagnostic prose and spec-check claims do not
    become public merely because an adapter labels them public.
    """
    ev, request_id = result["evaluation"], result["request_id"]
    obs = ev.get("observation", {})
    if not (ev.get("run_status") == "ok" and ev.get("behavior_verdict") is False
            and obs.get("run_status") == "ok" and obs.get("behavior_verdict") is False
            and obs.get("request_id") == request_id and obs.get("reset_verified") is True
            and obs.get("oracle_version") == oracle.get("version")
            and oracle.get("version")
            and obs.get("candidate_hash") == digest(ev.get("canonical_candidate"))):
        return [], ["no_current_failed_execution"]
    targets = {t["id"]: t for t in oracle.get("public_diagnostic_targets", [])}
    evidence = {e["path"]: e for e in obs.get("evidence", [])}
    directory = (evidence_root / request_id).resolve()
    accepted, rejected = [], []
    for item in ev.get("diagnostics", []):
        try:
            target = targets[item["checker_id"]]
            if item.get("source") != "ui_observation" or item.get("type") != "observed_ui_message" or item.get("public") is not True:
                raise ValueError("unsupported_diagnostic")
            prefix = f"request:{request_id}:"
            ref = item["evidence_ref"]
            if not ref.startswith(prefix):
                raise ValueError("stale_evidence")
            filename = ref[len(prefix):]
            path = (directory / filename).resolve()
            declared = evidence[filename]
            if not path.is_relative_to(directory) or path.suffix.lower() != ".xml":
                raise ValueError("invalid_evidence_path")
            raw = path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            if not raw or declared.get("sha256") != sha:
                raise ValueError("missing_or_changed_evidence")
            selector = target["selector"]
            if not selector or set(selector) - {"resource-id", "class", "index"}:
                raise ValueError("unsupported_selector")
            text = item["detail"]
            matches = [n for n in ET.fromstring(raw).iter("node")
                       if all(n.get(k) == v for k, v in selector.items())]
            if not isinstance(text, str) or not text.strip() or not any(n.get("text") == text for n in matches):
                raise ValueError("text_not_in_evidence")
            accepted.append({"type": "observed_ui_message", "detail": text, "source": "ui_observation",
                "checker_id": item["checker_id"], "selector": selector, "evidence_ref": ref,
                "evidence_sha256": sha, "interpretation": "Observed text; causal explanation not established."})
        except (KeyError, ValueError, TypeError, OSError, ET.ParseError) as exc:
            # Keep rejection labels in the audit; never send private content.
            rejected.append(type(exc).__name__)
    unique = {digest(item): item for item in accepted}
    return list(unique.values()), rejected


def next_feedback(case, arm: str, history: list[dict], endpoint: str, oracle: dict, evidence_root: Path):
    if arm not in ("C", "D"):
        raise ValueError("Only new C/D trajectories are generated")
    failures = [failure_record(case, result, i, endpoint) for i, result in enumerate(history, 1)]
    prompt, audit = structured_feedback(case.context, failures)
    extra, rejected = verified_ui_evidence(history[-1], oracle, evidence_root) if arm == "D" else ([], [])
    audit.update(arm=arm, diagnostic_available=bool(extra), diagnostics=extra,
                 evidence_eligible=history[-1]["evaluation"].get("behavior_verdict") is False,
                 diagnostic_rejections=rejected, fallback_to_c=arm == "D" and not extra)
    if extra:
        prompt += ("\n\n[CURRENT EXECUTION EVIDENCE]\n" + encoded(extra)
                   + "\n[NEXT TURN EVIDENCE USE]\nUse the observed UI messages to revise relevant fields "
                     "when they state applicable constraints. Do not infer hidden rules from failure alone. "
                     "The next candidate will be checked against the same task objective.")
    audit["sent_prompt_hash"] = digest(prompt)
    return prompt, audit
