"""Frozen logical contract for the Reviewer-2 specification feasibility pilot.

This module is deliberately pure: it does not call a model, Android, Docker,
or the network.  It separates the source-defined input partition from the
execution-based specification oracle and renders the three experimental arms.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .structured_runner import exact_json_candidate


PROTOCOL_ID = "reviewer2-spec-feasibility-pilot-v1"
ARMS = ("P0", "P1", "P2")
SPEC_VERDICTS = {"PASS", "FAIL", "UNKNOWN"}
PARTITIONS = {"TARGET", "CONTROL", "OUTSIDE", "UNKNOWN"}
SUPPORTED_CASE_SETS = (
    frozenset({"B014", "B023"}),
    frozenset({"B002", "B018"}),
)
SUPPORTED_PREDICATES = {
    "wallabag_malformed_fts_v1",
    "nextcloud_empty_multidigit_list_item_at_eof_v1",
    "debitum_grouped_amount_v1",
    "masked_edittext_nondigit_v1",
    "debitum_edit_grouped_amount_v2",
    "masked_phone_repeated_symbols_v2",
}


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _nonempty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value


def required_resource_ids(case: dict[str, Any]) -> list[str]:
    """Return the exact extracted-prompt field order for one case.

    The first pilot happened to use one input widget per case.  The original
    B002 and B018 prompts require three and four fields, respectively, so the
    generalized contract accepts ``resource_ids`` while retaining the legacy
    singular key for the already-frozen B014/B023 artifacts.
    """
    input_contract = case.get("input", {})
    values = input_contract.get("resource_ids")
    if values is None:
        values = [input_contract.get("resource_id")]
    if (
        not isinstance(values, list)
        or not values
        or any(not isinstance(value, str) or not value.strip() for value in values)
        or len(set(values)) != len(values)
    ):
        raise ValueError(f"{case.get('case_id', 'case')} resource_ids must be unique non-empty strings")
    return values


def validate_config(config: dict[str, Any], *, require_live: bool = False) -> None:
    """Validate the design, and optionally every gate required for a live run."""
    if config.get("protocol_id") != PROTOCOL_ID:
        raise ValueError("unexpected specification-pilot protocol_id")
    if tuple(config.get("arms", {})) != ARMS:
        raise ValueError("arm order must be exactly P0, P1, P2")
    if config.get("scope", {}).get("automatic_hidden_constraint_discovery_claimed") is not False:
        raise ValueError("the pilot must not claim automatic hidden-constraint discovery")
    if config.get("scope", {}).get("supplied_specification_integration_only") is not True:
        raise ValueError("the pilot scope must be supplied-specification integration")

    budget = config.get("budget", {})
    replicates = budget.get("replicates_per_case_arm", 1)
    if type(replicates) is not int or replicates < 1:
        raise ValueError("replicates_per_case_arm must be a positive integer")
    turns = budget.get("turns_per_trajectory")
    if type(turns) is not int or turns < 1:
        raise ValueError("turns_per_trajectory must be a positive integer")
    cases = config.get("cases")
    if not isinstance(cases, list) or len(cases) != 2:
        raise ValueError("the frozen pilot design requires exactly two cases")
    expected_experimental = len(cases) * len(ARMS) * replicates * turns
    if budget.get("max_experimental_paid_requests") != expected_experimental:
        raise ValueError("experimental request cap differs from cases x arms x turns")
    qualification = budget.get("max_qualification_paid_requests")
    if type(qualification) is not int or qualification < 0:
        raise ValueError("qualification request cap must be a non-negative integer")
    if budget.get("max_total_paid_requests") != expected_experimental + qualification:
        raise ValueError("total request cap must include experimental and qualification calls")
    if not isinstance(budget.get("max_total_cost_usd"), (int, float)) or budget["max_total_cost_usd"] <= 0:
        raise ValueError("max_total_cost_usd must be positive")

    runtime = config.get("runtime", {})
    if runtime.get("provider_cursor_reuse") is not False:
        raise ValueError("provider cursor reuse must be disabled")
    if runtime.get("fresh_provider_context_value") != "":
        raise ValueError("fresh provider context must be the empty string")
    if runtime.get("max_transport_attempts") != 1:
        raise ValueError("paid requests must not have automatic transport retries")

    baseline = config.get("baseline_prompt", {})
    if baseline.get("representation") != "extracted/full":
        raise ValueError("the pilot baseline must be the frozen extracted/full prompt")
    _nonempty_text(baseline.get("case_registry"), "baseline_prompt.case_registry")
    registry_sha256 = _nonempty_text(
        baseline.get("case_registry_sha256"),
        "baseline_prompt.case_registry_sha256",
    )
    if re.fullmatch(r"[0-9a-f]{64}", registry_sha256) is None:
        raise ValueError("baseline_prompt.case_registry_sha256 must be a lowercase SHA-256")
    if baseline.get("source_fields") != [
        "global", "component", "adjacent", "restrictive", "guiding"
    ]:
        raise ValueError("the extracted prompt source fields must remain frozen")
    if baseline.get("require_exact_sha256_match") is not True:
        raise ValueError("the extracted prompt SHA-256 gate must be enabled")

    seen: set[str] = set()
    for case in cases:
        case_id = _nonempty_text(case.get("case_id"), "case_id")
        if case_id in seen:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        requirement = case.get("requirement")
        if not isinstance(requirement, dict):
            raise ValueError(f"{case_id} requirement must be an object")
        for field in ("id", "version", "precondition", "input_condition", "action"):
            _nonempty_text(requirement.get(field), f"{case_id} requirement.{field}")
        for field in ("expected_postconditions", "forbidden_postconditions", "unknown_conditions", "out_of_scope"):
            values = requirement.get(field)
            if not isinstance(values, list) or not values or any(
                not isinstance(item, str) or not item.strip() for item in values
            ):
                raise ValueError(f"{case_id} requirement.{field} must be a non-empty string list")
        generation_contract = case.get("generation_contract")
        if generation_contract is not None:
            if not isinstance(generation_contract, dict):
                raise ValueError(f"{case_id} generation_contract must be an object")
            for field in ("contract_id", "scope", "precondition", "input_domain", "action", "provenance_boundary"):
                _nonempty_text(
                    generation_contract.get(field),
                    f"{case_id} generation_contract.{field}",
                )
            for field in ("required_behavior", "acceptable_outcomes", "forbidden_outcomes"):
                values = generation_contract.get(field)
                if not isinstance(values, list) or not values or any(
                    not isinstance(item, str) or not item.strip() for item in values
                ):
                    raise ValueError(
                        f"{case_id} generation_contract.{field} must be a non-empty string list"
                    )
            serialized_generation_contract = canonical_json(generation_contract)
            if (
                "target_input_condition" in generation_contract
                or requirement["input_condition"] in serialized_generation_contract
            ):
                raise ValueError(
                    f"{case_id} generalized generation contract exposes the issue-derived target condition"
                )
        sources = requirement.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError(f"{case_id} requires source provenance")
        for source in sources:
            _nonempty_text(source.get("url"), f"{case_id} source URL")
            _nonempty_text(source.get("supports"), f"{case_id} source support statement")

        input_contract = case.get("input")
        if not isinstance(input_contract, dict):
            raise ValueError(f"{case_id} input contract must be an object")
        resource_ids = required_resource_ids(case)
        if input_contract.get("predicate") not in SUPPORTED_PREDICATES:
            raise ValueError(f"{case_id} has an unsupported target partition")
        ui_context = case.get("ui_context")
        ui_resource_ids = (
            ui_context.get("resource_ids")
            if isinstance(ui_context, dict) and ui_context.get("resource_ids") is not None
            else [ui_context.get("resource_id")] if isinstance(ui_context, dict) else None
        )
        if not isinstance(ui_context, dict) or ui_resource_ids != resource_ids:
            raise ValueError(f"{case_id} UI context differs from the input contract")
        _nonempty_text(ui_context.get("description"), f"{case_id} UI description")
        _nonempty_text(ui_context.get("available_action"), f"{case_id} UI action")
        execution = case.get("execution")
        if not isinstance(execution, dict):
            raise ValueError(f"{case_id} execution contract must be an object")
        for field in ("package", "version_code", "apk_path", "apk_sha256", "manifest_path", "manifest_sha256", "snapshot_path", "device_serial"):
            _nonempty_text(execution.get(field), f"{case_id} execution.{field}")
        for field in ("apk_sha256", "manifest_sha256"):
            if re.fullmatch(r"[0-9a-f]{64}", execution[field]) is None:
                raise ValueError(f"{case_id} execution.{field} must be a lowercase SHA-256")
        action = execution.get("action")
        if not isinstance(action, dict) or action.get("kind") not in {
            "settle_only", "click_resource_id", "structured_actions", "source_backed_key_sequence",
        }:
            raise ValueError(f"{case_id} execution action is unsupported")
        if action["kind"] == "click_resource_id":
            _nonempty_text(action.get("resource_id"), f"{case_id} action.resource_id")
        if action["kind"] == "structured_actions":
            bindings = execution.get("bindings")
            if not isinstance(bindings, dict) or set(bindings) != set(resource_ids):
                raise ValueError(f"{case_id} structured bindings must match every prompt field")
            if any(not isinstance(selector, dict) or not selector for selector in bindings.values()):
                raise ValueError(f"{case_id} structured bindings must be non-empty objects")
            actions = execution.get("verify_actions")
            if not isinstance(actions, list) or not actions or any(
                not isinstance(item, dict) or not item for item in actions
            ):
                raise ValueError(f"{case_id} structured verify_actions must be non-empty")
        observation_seconds = execution.get("observation_seconds")
        if not isinstance(observation_seconds, (int, float)) or not 0 < observation_seconds <= 30:
            raise ValueError(f"{case_id} observation window must be in (0,30] seconds")

    if frozenset(seen) not in SUPPORTED_CASE_SETS:
        raise ValueError("the pilot cases must be exactly B014/B023 or B002/B018")

    if require_live:
        if config.get("protocol_status") != "frozen" or config.get("live_run_enabled") is not True:
            raise ValueError("live run requires a frozen, explicitly enabled protocol")
        model = config.get("model", {})
        for field in ("id", "revision", "qualification_receipt", "qualification_receipt_sha256"):
            _nonempty_text(model.get(field), f"model.{field}")
        if model.get("capacity_verified") is not True:
            raise ValueError("live run requires a frozen model prompt-capacity qualification")
        if type(model.get("max_prompt_bytes")) is not int or model["max_prompt_bytes"] < 1:
            raise ValueError("live run requires a positive frozen prompt-byte limit")
        pricing = model.get("pricing_usd_per_million_tokens")
        if not isinstance(pricing, dict) or any(
            not isinstance(pricing.get(key), (int, float)) or pricing[key] < 0
            for key in ("input", "output")
        ):
            raise ValueError("live run requires frozen input/output pricing")
        if not isinstance(model.get("max_request_cost_usd"), (int, float)) or model["max_request_cost_usd"] <= 0:
            raise ValueError("live run requires a positive per-request cost ceiling")
        for case in cases:
            execution = case.get("execution", {})
            for field in (
                "apk_path",
                "apk_sha256",
                "apk_signer_sha256",
                "manifest_path",
                "manifest_sha256",
                "snapshot_path",
                "device_serial",
                "package",
                "version_code",
            ):
                _nonempty_text(execution.get(field), f"{case['case_id']} execution.{field}")
            command = execution.get("reset_command")
            if not isinstance(command, list) or not command or any(
                not isinstance(item, str) or not item for item in command
            ):
                raise ValueError(f"{case['case_id']} reset_command is not frozen")
            for source in case["requirement"]["sources"]:
                _nonempty_text(source.get("archive_path"), f"{case['case_id']} source archive_path")
                _nonempty_text(source.get("archive_sha256"), f"{case['case_id']} source archive_sha256")


def case_index(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    validate_config(config)
    return {case["case_id"]: case for case in config["cases"]}


def _specification_json_candidate(
    raw_response: str,
    required_ids: list[str],
) -> tuple[str, dict[str, str] | None, str | None]:
    """Parse one fenced JSON object even when a string contains triple backticks.

    The production prompt asks for a fenced JSON object and permits an
    explanation.  A Markdown robustness candidate may itself contain a code
    fence, so locating the first textual closing fence is ambiguous.  Decode
    the complete JSON value first, using JSON string semantics, and only then
    require the outer fence to close.  Multiple ``json``-labelled candidate
    blocks remain ambiguous and are rejected.
    """
    text = raw_response.strip()
    fence = "```json"
    if fence not in text:
        return exact_json_candidate(raw_response, required_ids)
    if text.count(fence) != 1:
        return "parse_error", None, "response must contain exactly one JSON code fence"
    payload = text[text.index(fence) + len(fence):].lstrip()
    try:
        value, end = json.JSONDecoder().raw_decode(payload)
    except json.JSONDecodeError as exc:
        return "parse_error", None, f"{exc.msg} at character {exc.pos}"
    remainder = payload[end:].lstrip()
    if not remainder.startswith("```"):
        return "parse_error", None, "JSON code fence is not closed after the JSON object"
    if fence in remainder[3:]:
        return "parse_error", None, "response contains multiple JSON code fences"
    return exact_json_candidate(
        json.dumps(value, ensure_ascii=True, separators=(",", ":")),
        required_ids,
    )


def parse_candidate(raw_response: str, case: dict[str, Any]) -> dict[str, str]:
    """Reuse the A--F parser for the frozen extracted-prompt output contract."""
    if not isinstance(raw_response, str) or not raw_response.strip():
        raise ValueError("model response is empty")
    keys = required_resource_ids(case)
    status, value, error = _specification_json_candidate(raw_response, keys)
    if status != "valid" or value is None:
        raise ValueError(error or "candidate does not satisfy the extracted-prompt output contract")
    for key in keys:
        try:
            value[key].encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("candidate contains an unpaired Unicode surrogate") from exc
        if len(value[key]) > int(case["input"].get("max_chars", 2000)):
            raise ValueError("candidate exceeds the frozen character limit")
    return value


def candidate_partition(case: dict[str, Any], candidate: dict[str, str]) -> str:
    """Return the source-defined target/control partition, not an App verdict."""
    keys = required_resource_ids(case)
    if set(candidate) != set(keys) or any(not isinstance(candidate[key], str) for key in keys):
        return "UNKNOWN"
    value = candidate[keys[0]]
    predicate = case["input"]["predicate"]
    if predicate == "wallabag_malformed_fts_v1":
        stripped = value.strip()
        if stripped and ('"' in stripped or re.match(r"^-\S", stripped)):
            return "TARGET"
        return "CONTROL" if stripped else "OUTSIDE"
    if predicate == "nextcloud_empty_multidigit_list_item_at_eof_v1":
        normalized = value.replace("\r\n", "\n").replace("\r", "\n")
        target = re.search(r"(?:^|\n)\d{2,}\.[ \t]*(?:\n)?\Z", normalized) is not None
        if target:
            return "TARGET"
        if value.strip():
            return "CONTROL"
        return "OUTSIDE"
    if predicate == "debitum_grouped_amount_v1":
        amount_id = "org.ebur.debitum:id/edit_amount"
        amount = candidate.get(amount_id, "").strip()
        target = False
        if amount and " " in amount and re.fullmatch(r"\d[\d ]*(\.\d+)?", amount):
            try:
                target = float(amount.replace(" ", "")) > 999
            except ValueError:
                target = False
        if target:
            return "TARGET"
        return "CONTROL" if amount else "OUTSIDE"
    if predicate == "masked_edittext_nondigit_v1":
        values = [candidate[key] for key in keys]
        if any(item != "" and not item.isdigit() for item in values):
            return "TARGET"
        return "CONTROL" if any(item != "" for item in values) else "OUTSIDE"
    if predicate == "debitum_edit_grouped_amount_v2":
        amount = candidate.get("org.ebur.debitum:id/edit_amount", "").strip()
        normalized = re.sub(r"[ \u00a0\u202f]", "", amount)
        if re.fullmatch(r"\d+(?:[.,]\d+)?", normalized):
            return "TARGET" if float(normalized.replace(",", ".")) > 999 else "CONTROL"
        return "OUTSIDE"
    if predicate == "masked_phone_repeated_symbols_v2":
        phone = candidate.get("mo.must.maskededittextbug:id/phone", "")
        if sum(character in ",.-" for character in phone) >= 2:
            return "TARGET"
        return "CONTROL" if phone and phone.isdecimal() else "OUTSIDE"
    raise ValueError(f"unsupported target partition: {predicate}")


def _model_requirement_card(case: dict[str, Any]) -> dict[str, Any]:
    generation_contract = case.get("generation_contract")
    if isinstance(generation_contract, dict):
        return json.loads(json.dumps(generation_contract))
    requirement = case["requirement"]
    return {
        "requirement_id": requirement["id"],
        "version": requirement["version"],
        "precondition": requirement["precondition"],
        "target_input_condition": requirement["input_condition"],
        "action": requirement["action"],
        "expected_observable_postconditions": requirement["expected_postconditions"],
        "forbidden_observable_postconditions": requirement["forbidden_postconditions"],
        "out_of_scope": requirement["out_of_scope"],
    }


def render_prompt(
    case: dict[str, Any],
    arm: str,
    prior_attempts: list[dict[str, Any]],
    base_prompt: str,
) -> tuple[str, dict[str, Any]]:
    """Append specification sidecars to the byte-frozen extracted prompt."""
    if arm not in ARMS:
        raise ValueError(f"unsupported pilot arm: {arm}")
    if not isinstance(base_prompt, str) or not base_prompt.strip():
        raise ValueError("base_prompt must be the non-empty frozen extracted prompt")
    prompt = base_prompt
    sidecar: dict[str, Any] = {}
    if arm in {"P1", "P2"}:
        sidecar["supplied_requirement_contract"] = _model_requirement_card(case)
    feedback_visible = False
    target_partition_visible = False
    if arm == "P2" and prior_attempts:
        latest = prior_attempts[-1]
        partition = latest.get("candidate_target_partition", "UNKNOWN")
        verdict = latest.get("o_spec", "UNKNOWN")
        if partition not in PARTITIONS or verdict not in SPEC_VERDICTS:
            raise ValueError("P2 feedback contains an unsupported partition or verdict")
        public_observations = latest.get("public_observations", [])
        if not isinstance(public_observations, list) or any(
            not isinstance(item, str) for item in public_observations
        ):
            raise ValueError("P2 public observations must be a string list")
        feedback = {
            "turn": latest["turn"],
            "candidate": latest.get("candidate"),
            "candidate_parse_status": (
                "USABLE" if latest.get("usable_response") is True else "UNUSABLE"
            ),
            "candidate_parse_error": latest.get("parse_error"),
            "O_spec": verdict,
            "public_observations": public_observations,
            "unknown_reason": latest.get("unknown_reason") if verdict == "UNKNOWN" else None,
            "interpretation": (
                "This is evidence about the previous attempt, not generation advice. "
                "The requirement and execution budget are unchanged."
            ),
        }
        if not isinstance(case.get("generation_contract"), dict):
            feedback["candidate_target_partition"] = partition
            target_partition_visible = True
        else:
            feedback["hidden_oracle_note"] = (
                "Membership in the evaluator's hidden bug-trigger partition is not disclosed."
            )
        sidecar["previous_attempt_specification_feedback"] = feedback
        feedback_visible = True
    if sidecar:
        prompt = (
            base_prompt
            + "\n\n[SPECIFICATION SIDECAR]\n"
            + json.dumps(sidecar, ensure_ascii=False, sort_keys=True, indent=2)
        )
    base_prompt_sha256 = hashlib.sha256(base_prompt.encode("utf-8")).hexdigest()
    audit = {
        "schema_version": "spec-feasibility-prompt-audit-2",
        "arm": arm,
        "case_id": case["case_id"],
        "baseline_representation": "extracted/full",
        "baseline_prompt_sha256": base_prompt_sha256,
        "baseline_prompt_bytes": len(base_prompt.encode("utf-8")),
        "baseline_is_exact_prompt": prompt == base_prompt,
        "baseline_preserved_as_exact_prefix": prompt.startswith(base_prompt),
        "requirement_visible": arm in {"P1", "P2"},
        "model_visible_contract_kind": (
            "generalized_behavioral_contract"
            if isinstance(case.get("generation_contract"), dict)
            else "issue_derived_regression_contract"
        ),
        "model_visible_contract_sha256": (
            sha256_json(_model_requirement_card(case))
            if arm in {"P1", "P2"}
            else None
        ),
        "exact_target_condition_visible": (
            arm in {"P1", "P2"} and not isinstance(case.get("generation_contract"), dict)
        ),
        "target_partition_feedback_visible": target_partition_visible,
        "specification_feedback_visible": feedback_visible,
        "history_count": 1 if feedback_visible else 0,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    }
    return prompt, audit


def usage_tokens(usage: Any) -> tuple[int, int]:
    """Normalize the common gateway token fields and fail closed if absent."""
    if not isinstance(usage, dict):
        raise ValueError("provider usage telemetry is missing")
    # Some gateways include zero-valued Anthropic aliases alongside actual
    # OpenAI-normalized counts. Never discard positive completion telemetry.
    input_counts = [usage[key] for key in ("input_tokens", "prompt_tokens") if key in usage]
    output_counts = [usage[key] for key in ("output_tokens", "completion_tokens") if key in usage]
    if any(type(value) is not int or value < 0 for value in input_counts + output_counts):
        raise ValueError("provider token telemetry is invalid")
    if not any(key in usage for key in ("input_tokens", "prompt_tokens")):
        raise ValueError("provider input-token telemetry is missing")
    if not any(key in usage for key in ("output_tokens", "completion_tokens")):
        raise ValueError("provider output-token telemetry is missing")
    input_tokens, output_tokens = max(input_counts), max(output_counts)
    if type(input_tokens) is not int or input_tokens < 0:
        raise ValueError("provider input-token telemetry is missing")
    if type(output_tokens) is not int or output_tokens < 0:
        raise ValueError("provider output-token telemetry is missing")
    return input_tokens, output_tokens


def request_cost_usd(usage: Any, pricing: dict[str, float]) -> float:
    input_tokens, output_tokens = usage_tokens(usage)
    return (
        input_tokens * float(pricing["input"])
        + output_tokens * float(pricing["output"])
    ) / 1_000_000
