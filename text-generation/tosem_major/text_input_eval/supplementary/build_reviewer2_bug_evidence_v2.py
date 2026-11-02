#!/usr/bin/env python3
"""Build the Reviewer 2 evidence-driven classification for B001--B037.

This is an offline synthesis step.  It reads the frozen E2 registry and emits
review artifacts only; it does not contact providers, devices, or databases.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "reviewer2-bug-evidence-v2.1-best-effort"

ISO_29148_URL = "https://www.iso.org/standard/72089.html"
ISO_25010_URL = "https://www.iso.org/standard/78176.html"


CLASSIFICATION_DECISION_RULE = {
    "benchmark_provenance_defect": "source_does_not_support_a_distinct_app_bug_case",
    "external_interoperability_and_resilience_spec": "external_service_app_or_ime_state_is_necessary_to_the_failure",
    "performance_and_timing_spec": "rate_scheduling_or_responsiveness_is_necessary_to_the_failure",
    "feedback_and_error_presentation_spec": "core_action_occurs_but_feedback_visibility_localization_or_actionability_is_violated",
    "ui_component_and_affordance_spec": "widget_configuration_or_control_enablement_is_the_primary_violated_constraint",
    "text_transformation_and_parser_spec": "text_transformation_parsing_or_boundary_semantics_are_primary",
    "stateful_interaction_spec": "prior_configuration_mode_loading_or_multi_action_state_is_necessary",
    "input_domain_and_validation_spec": "accepted_rejected_normalized_or_persisted_input_domain_is_primary",
}


APP_DISPLAY_OVERRIDES = {
    # The embedded v1 evidence row contains the typo "FlorisBpard".
    "B012": "FlorisBoard",
    "B013": "FlorisBoard",
}


BEHAVIOR_FAMILY = {
    "crash": "reliability_crash",
    "crash_or_stuck": "reliability_hang_or_crash",
    "anr_or_crash": "reliability_hang_or_crash",
    "crash_and_persistent_state": "persistent_state_failure",
    "wrong_output": "incorrect_or_missing_output",
    "missing_output": "incorrect_or_missing_output",
    "error_ui": "defective_feedback_or_error_presentation",
    "wrong_error_message": "defective_feedback_or_error_presentation",
    "hidden_error_message": "defective_feedback_or_error_presentation",
    "invalid_ui_acceptance": "invalid_input_accepted",
    "invalid_acceptance": "invalid_input_accepted",
}


INTERACTION_SCOPE = {
    "B001": "single_form_action",
    "B002": "input_sequence_same_field",
    "B003": "input_sequence_same_field",
    "B004": "prior_state_or_mode_required",
    "B005": "timing_or_external_service_dependent",
    "B006": "single_form_action",
    "B007": "prior_state_or_mode_required",
    "B008": "prior_state_or_mode_required",
    "B009": "cross_app_state_required",
    "B010": "prior_state_or_mode_required",
    "B011": "prior_state_or_mode_required",
    "B012": "input_sequence_same_field",
    "B013": "input_sequence_same_field",
    "B014": "single_form_action",
    "B015": "input_sequence_same_field",
    "B016": "multi_step_same_app",
    "B017": "multi_step_same_app",
    "B018": "input_sequence_same_field",
    "B019": "multi_step_same_app",
    "B020": "single_form_action",
    "B021": "single_form_action",
    "B022": "single_form_action",
    "B023": "input_sequence_same_field",
    "B024": "timing_or_external_service_dependent",
    "B025": "single_form_action",
    "B026": "multi_step_same_app",
    "B027": "single_form_action",
    "B028": "single_form_action",
    "B029": "prior_state_or_mode_required",
    "B030": "single_form_action",
    "B031": "single_form_action",
    "B032": "single_form_action",
    "B033": "multi_step_same_app",
    "B034": "multi_step_same_app",
    "B035": "single_form_action",
    "B036": "single_form_action",
    "B037": "multi_step_same_app",
}


# Every case receives a best-effort mechanism.  The accompanying evidence basis
# and confidence distinguish direct source support from analyst inference.
IMPLEMENTATION_FAULT = {
    "B001": ("missing_or_late_input_guard", "direct_issue_plus_fix_diff", "high", "trim was applied before the empty-email guard"),
    "B002": ("locale_sensitive_number_round_trip_mismatch", "issue_reproduction_supported_inference", "medium", "grouping-space formatting was not accepted by the later amount parsing/edit path"),
    "B003": ("candidate_generation_normalization_error", "issue_reproduction_supported_inference", "medium", "numeric suggestion generation produced an unwanted leading-zero-stripped candidate"),
    "B004": ("suggestion_only_dictionary_filtering_error", "issue_reproduction_supported_inference", "medium", "the suggestion-only user-dictionary entry was omitted from candidate retrieval"),
    "B005": ("external_response_parsing", "direct_stack_trace", "high", "a non-JSON YouTube suggestion response reached the JSON parser"),
    "B006": ("input_widget_constraint_configuration", "direct_fix_diff", "high", "the doses field admitted non-numeric text before the fix restricted its input type"),
    "B007": ("scientific_notation_state_reparse_error", "issue_reproduction_supported_inference", "medium", "a scientific-notation result was fed back into a path that could not parse or preserve it"),
    "B008": ("unsafe_numeric_conversion", "direct_stack_and_code_location", "high", "a symbolic app-op name reached Integer.parseInt"),
    "B009": ("stale_ime_composition_after_external_completion", "issue_reproduction_supported_inference", "medium", "the keyboard did not reconcile its composing state after Discord completed the colon token externally"),
    "B010": ("null_optional_icon_dereference", "direct_fix_diff", "high", "a nullable hidden-app icon was passed into shaping and drawing without a null guard"),
    "B011": ("null_optional_icon_dereference", "direct_fix_diff", "high", "a nullable hidden-app icon was passed into shaping and drawing without a null guard"),
    "B012": ("placeholder_suggestion_autoinsert_left_enabled", "direct_maintainer_comment", "high", "the alpha placeholder suggestion framework auto-inserted its demo candidate for auto-prefixed words"),
    "B013": ("placeholder_suggestion_autoinsert_left_enabled", "direct_maintainer_comment", "high", "the alpha placeholder suggestion framework repeatedly auto-inserted the demo zero candidate"),
    "B014": ("unhandled_query_syntax", "direct_stack_plus_fix_diff", "high", "malformed SQLite FTS MATCH syntax propagated instead of returning an empty result"),
    "B015": ("bounds_or_indexing", "maintainer_code_location", "high", "visible-glyph computation incremented beyond the string boundary for a lone markup opener"),
    "B016": ("context_insensitive_text_replacement", "issue_reproduction_supported_inference", "medium", "text-smilie replacement treated the URL colon sequence as an emoticon without cursor or URL context"),
    "B017": ("null_or_type_handling", "exception_sequence_only", "medium", "blank departure state first produced an NPE and then a ClassCastException in the follow-up action"),
    "B018": ("mask_state_corruption_on_rejected_character", "issue_reproduction_supported_inference", "medium", "non-digit input consumed mask symbols until the mask state became invalid and crashed"),
    "B019": ("nullable_public_room_count_state", "direct_fix_diff", "high", "public-room counts used nullable boxed integers during an in-progress search; the fix introduced explicit initialized sentinel values"),
    "B020": ("missing_blank_dom_selector_guard", "issue_reproduction_supported_inference", "medium", "a whitespace-only DOM selector reached website creation without a blank-value guard"),
    "B021": ("benchmark_case_split_not_supported_by_source", "direct_source_contradiction", "high", "the retrieved source attributes the reproduced crash to B020's blank DOM selector rather than a distinct malformed-URL defect"),
    "B022": ("leading_decimal_partial_number_parsing", "issue_reproduction_supported_inference", "medium", "the numeric path did not safely accept or reject a leading-decimal value such as .67"),
    "B023": ("markdown_numbered_list_boundary_failure", "stack_and_component_localization", "medium", "RxMarkdown failed when previewing an empty two-digit numbered-list item at the document boundary"),
    "B024": ("competing_autosave_timer_scheduling", "maintainer_hypothesis_plus_fix_diff", "medium", "rapid edits created or rescheduled timer work in a way that overloaded UI-thread autosave processing"),
    "B025": ("unhandled_empty_numeric_action", "direct_reporter_exception_comment", "medium", "Set Frequency became enabled for an empty field and an exception propagated unhandled"),
    "B026": ("editable_token_cut_index_out_of_bounds", "direct_stack_and_dependency_localization", "high", "cutting an invalid recipient token triggered an index-out-of-bounds path in TokenAutoComplete"),
    "B027": ("required_field_action_enabled_too_early", "direct_fix_diff", "high", "the login action was enabled before required input existed"),
    "B028": ("whitespace_normalization_guard_gap", "issue_supported_hypothesis_no_fix_diff", "medium", "a single-space query normalized to empty without an effective post-normalization guard"),
    "B029": ("wordwrap_delete_layout_recalculation_failure", "reporter_code_location_plus_reproduction", "medium", "held backspace under word wrap drove problematic column-count/layout recalculation"),
    "B030": ("missing_required_identifier_guard", "issue_reproduction_supported_inference", "medium", "Login processed an empty Hospital ID instead of presenting validation feedback"),
    "B031": ("error_message_design", "direct_fix_diff_closed_unmerged", "high", "technical NumberFormatException text was exposed instead of localized range guidance"),
    "B032": ("incomplete_hex_color_parse_guard", "issue_plus_fix_commit_reference", "medium", "the color parser processed the incomplete token # without an incremental-edit guard"),
    "B033": ("validation_message_layout_occlusion", "issue_screenshot_and_code_location", "high", "the next editor field covered the red validation message"),
    "B034": ("unsafe_numeric_conversion", "direct_stack_and_code_location", "high", "an empty persisted update-period string reached Integer.parseInt"),
    "B035": ("input_widget_constraint_configuration", "direct_fix_diff", "high", "the coupon EditText admitted multiple lines until maxLines was restricted"),
    "B036": ("missing_or_late_input_guard", "direct_fix_diff", "high", "attendee creation lacked an email-validity check"),
    "B037": ("text_input_hint_component_misconfiguration", "direct_surviving_pr_but_trigger_conflicted", "medium", "hint placement on TextInputEditText rather than TextInputLayout caused the surviving documented crash"),
}


CAUSE_EVIDENCE_RANK = {
    "direct_issue_plus_fix_diff": "direct",
    "direct_stack_trace": "direct",
    "direct_fix_diff": "direct",
    "direct_stack_and_code_location": "direct",
    "direct_stack_plus_fix_diff": "direct",
    "maintainer_code_location": "direct",
    "direct_fix_diff_closed_unmerged": "direct_but_not_merged",
    "direct_surviving_pr_but_trigger_conflicted": "direct_but_trigger_conflicted",
    "direct_maintainer_comment": "direct_maintainer_statement",
    "direct_source_contradiction": "direct_source_contradiction",
    "direct_reporter_exception_comment": "direct_failure_statement",
    "direct_stack_and_dependency_localization": "direct_stack_and_dependency",
    "issue_plus_fix_commit_reference": "source_plus_fix_reference",
    "issue_screenshot_and_code_location": "direct_ui_evidence",
    "exception_sequence_only": "component_or_failure_localized",
    "stack_and_component_localization": "component_or_failure_localized",
    "reporter_code_location_plus_reproduction": "component_or_failure_localized",
    "issue_supported_hypothesis_no_fix_diff": "provisional",
    "maintainer_hypothesis_plus_fix_diff": "provisional_with_fix_structure",
    "issue_reproduction_supported_inference": "best_effort_inference_from_reproduction",
}


SPECIFICATION_OBLIGATION = {
    "B001": ("input_domain_and_validation_spec", "validate presence before normalization", "issue_and_fix", "high"),
    "B002": ("input_domain_and_validation_spec", "round-trip locale-formatted monetary values", "issue_reproduction", "medium"),
    "B003": ("text_transformation_and_parser_spec", "preserve intended leading-zero semantics in candidates", "explicit_expected_vs_actual", "high"),
    "B004": ("stateful_interaction_spec", "honor suggestion-only dictionary entries after configuration", "explicit_expected_vs_actual", "high"),
    "B005": ("external_interoperability_and_resilience_spec", "handle malformed provider responses without user-visible failure", "stack_and_issue", "high"),
    "B006": ("input_domain_and_validation_spec", "enforce the numeric dose domain at entry", "issue_and_fix", "high"),
    "B007": ("stateful_interaction_spec", "accept calculator-produced scientific notation as the next input state", "explicit_expected_vs_actual", "high"),
    "B008": ("input_domain_and_validation_spec", "distinguish symbolic app-op identifiers from numeric IDs", "stack_and_issue", "high"),
    "B009": ("external_interoperability_and_resilience_spec", "synchronize IME composition after external text completion", "cross_app_reproduction", "medium"),
    "B010": ("stateful_interaction_spec", "support search when hidden items have nullable icons", "issue_and_fix", "high"),
    "B011": ("stateful_interaction_spec", "support all documented search characters with hidden nullable icons", "issue_and_fix", "high"),
    "B012": ("text_transformation_and_parser_spec", "prevent placeholder suggestions from altering user text", "maintainer_comment", "high"),
    "B013": ("text_transformation_and_parser_spec", "prevent repeated placeholder insertion in alpha suggestion mode", "maintainer_comment", "high"),
    "B014": ("text_transformation_and_parser_spec", "reject or escape malformed FTS query syntax", "stack_and_fix", "high"),
    "B015": ("text_transformation_and_parser_spec", "bound markup scanning at end of input", "stack_and_maintainer_comment", "high"),
    "B016": ("text_transformation_and_parser_spec", "preserve URLs during optional emoticon replacement", "explicit_expected_vs_actual", "high"),
    "B017": ("stateful_interaction_spec", "handle a blank departures state across refresh and selection actions", "exception_sequence", "medium"),
    "B018": ("input_domain_and_validation_spec", "preserve mask invariants when rejecting non-digit characters", "issue_reproduction", "medium"),
    "B019": ("stateful_interaction_spec", "represent public-room loading state without nullable-count crashes", "issue_and_fix", "high"),
    "B020": ("input_domain_and_validation_spec", "reject a whitespace-only DOM selector before website creation", "issue_followup_reproduction", "high"),
    "B021": ("benchmark_provenance_defect", "avoid splitting one reproduced DOM-selector defect into a second malformed-URL case", "source_contradiction", "high"),
    "B022": ("input_domain_and_validation_spec", "define and safely handle leading-decimal numeric grammar", "issue_reproduction", "medium"),
    "B023": ("text_transformation_and_parser_spec", "handle empty multi-digit Markdown list items at document end", "issue_and_stack", "high"),
    "B024": ("performance_and_timing_spec", "debounce autosave under rapid input without UI-thread overload", "issue_comments_and_fix", "medium"),
    "B025": ("ui_component_and_affordance_spec", "keep Set Frequency disabled until numeric input exists", "explicit_expected_behavior", "high"),
    "B026": ("stateful_interaction_spec", "support cutting an invalid recipient token without data loss", "issue_stack_and_dependency_link", "high"),
    "B027": ("ui_component_and_affordance_spec", "disable Login until required fields are present", "issue_and_fix", "high"),
    "B028": ("input_domain_and_validation_spec", "treat whitespace-only search as empty after normalization", "issue_reproduction", "high"),
    "B029": ("performance_and_timing_spec", "maintain responsive word-wrap layout during repeated deletion", "issue_reproduction_and_code_location", "medium"),
    "B030": ("input_domain_and_validation_spec", "reject an empty Hospital ID with visible feedback", "explicit_expected_behavior", "high"),
    "B031": ("feedback_and_error_presentation_spec", "show localized range guidance rather than technical exceptions", "issue_and_fix", "high"),
    "B032": ("input_domain_and_validation_spec", "tolerate the intermediate # state while editing a color", "issue_and_fix_reference", "medium"),
    "B033": ("feedback_and_error_presentation_spec", "keep validation messages visible in the editor layout", "issue_screenshot", "high"),
    "B034": ("input_domain_and_validation_spec", "validate an empty update-period value before saving and reopening", "stack_and_code_location", "high"),
    "B035": ("ui_component_and_affordance_spec", "constrain coupon entry to a single line", "issue_and_fix", "high"),
    "B036": ("input_domain_and_validation_spec", "validate attendee email before creation", "issue_and_fix", "high"),
    "B037": ("ui_component_and_affordance_spec", "place hints on the supported TextInputLayout component", "surviving_fix_pr", "medium"),
}


def parse_json_cell(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError(f"Expected a JSON list, got {type(parsed).__name__}")
    return parsed


def evidence_text(value: Any) -> Any:
    """Normalize legacy absence labels without pretending they are facts."""
    if not isinstance(value, str):
        return value
    return value.replace("unknown", "not established").replace(
        "Unknown", "Not established"
    )


def reported_version(value: Any) -> str:
    if value in (None, "", "unknown"):
        return "not_reported_in_retrieved_source"
    return str(value)


def version_alignment(value: Any) -> str:
    if value == "aligned":
        return "aligned"
    return "not_proven"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dump_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def source_trigger_status(case_id: str, old_alignment: str, validator_changed: bool) -> str:
    if case_id == "B028" and validator_changed:
        return "aligned_after_current_validator_repair"
    return {
        "aligned": "aligned",
        "overbroad": "overbroad",
        "partial_missing_state": "partial_missing_state",
        "needs_fix_binding": "trigger_supported_binding_unverified",
        "contradicted": "contradicted",
        "needs_fix_constant_false": "known_validator_defect",
    }.get(old_alignment, f"unmapped:{old_alignment}")


def reviewer_disposition(trigger_status: str) -> str:
    if trigger_status in {"aligned", "aligned_after_current_validator_repair"}:
        return "include_for_source_trigger_analysis_version_unaligned"
    if trigger_status in {"overbroad", "partial_missing_state"}:
        return "hold_until_trigger_or_state_scope_is_corrected"
    if trigger_status == "trigger_supported_binding_unverified":
        return "hold_until_binding_metadata_is_adjudicated"
    return "exclude_from_source_faithful_effect_claims_pending_resolution"


def issue_cluster(urls: list[str]) -> str:
    issue_urls = sorted(url for url in urls if "/issues/" in url)
    basis = issue_urls or sorted(urls)
    return "IC-" + hashlib.sha256("|".join(basis).encode("utf-8")).hexdigest()[:12]


def build_rows(registry: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cases = sorted(
        (item for item in registry["cases"] if item["case_id"].startswith("B")),
        key=lambda item: item["case_id"],
    )
    if [item["case_id"] for item in cases] != [f"B{i:03d}" for i in range(1, 38)]:
        raise ValueError("The current registry does not contain exactly B001--B037")

    rows: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    for item in cases:
        case_id = item["case_id"]
        source = item["app_or_page_source"]
        mirror = item["mirror"]
        formal = item["formal_oracle"]
        candidate = item["candidate_validator_audit"]
        readiness = item["protocol_readiness"]
        urls = parse_json_cell(source.get("source_urls"))
        page_shas = parse_json_cell(source.get("page_sha256s"))
        tests = parse_json_cell(source.get("test_paths"))
        cluster = issue_cluster(urls)
        old_validator_sha = source.get("validator_sha256")
        current_validator_sha = item["source_files"].get("validator_sha256")
        validator_changed = old_validator_sha != current_validator_sha
        trigger_status = source_trigger_status(
            case_id, source["validator_alignment"], validator_changed
        )
        fault_family, fault_evidence, fault_confidence, fault_cause = IMPLEMENTATION_FAULT[
            case_id
        ]
        obligation_family, obligation, obligation_basis, obligation_confidence = (
            SPECIFICATION_OBLIGATION[case_id]
        )
        metadata_conflicts: list[str] = []
        if candidate.get("binding_exact") is False and formal.get("binding_keys_exact") is True:
            metadata_conflicts.append("candidate_binding_false_vs_formal_binding_true")
        if candidate.get("predicate_control_pair") is False and formal.get("evidence_chain_verified") is True:
            metadata_conflicts.append("candidate_control_false_vs_formal_evidence_true")
        if validator_changed:
            metadata_conflicts.append("embedded_source_validator_sha_differs_from_current_validator")

        row = {
            "case_id": case_id,
            "issue_cluster_id": cluster,
            "app": APP_DISPLAY_OVERRIDES.get(case_id, source["app"]),
            "app_source_label": source["app"],
            "behavioral_failure_family": BEHAVIOR_FAMILY[source["symptom_class"]],
            "observable_symptom": source["symptom_class"],
            "interaction_scope": INTERACTION_SCOPE[case_id],
            "state_or_action_precondition": source["state_dependency"],
            "trigger_form_secondary": source["trigger_class"],
            "constraint_visibility": source["constraint_visibility"],
            "implementation_fault_family": fault_family,
            "implementation_cause_best_effort": fault_cause,
            "implementation_cause_evidence": evidence_text(
                source["implementation_cause_evidence"]
            ),
            "implementation_cause_evidence_level": CAUSE_EVIDENCE_RANK[fault_evidence],
            "implementation_cause_confidence": fault_confidence,
            "specification_obligation_family": obligation_family,
            "specification_obligation": obligation,
            "classification_decision_rule": CLASSIFICATION_DECISION_RULE[
                obligation_family
            ],
            "specification_attribution_basis": obligation_basis,
            "specification_attribution_confidence": obligation_confidence,
            "requirements_process_root_cause_claimed": False,
            "source_reported_version": reported_version(source["source_reported_version"]),
            "installed_package": mirror.get("package"),
            "installed_version_code": mirror.get("version_code"),
            "installed_source_sha256": mirror.get("source_sha256"),
            "public_report_to_installed_artifact_alignment": version_alignment(
                source.get("apk_version_alignment")
            ),
            "expected_behavior_evidence": source["expected_behavior_evidence"],
            "source_trigger_alignment_historical": source["validator_alignment"],
            "source_trigger_alignment_current_assessment": trigger_status,
            "classification_confidence_historical": source["classification_confidence"],
            "reviewer_analysis_disposition": reviewer_disposition(trigger_status),
            "source_urls": json.dumps(urls, ensure_ascii=False),
            "source_page_sha256s": json.dumps(page_shas, ensure_ascii=False),
            "retrieved_source_count": source.get("retrieved_sources", 0),
            "retrieval_error_count": source.get("retrieval_errors", 0),
            "merged_fix_pr_count": source.get("merged_fix_prs", 0),
            "closed_unmerged_pr_count": source.get("closed_unmerged_prs", 0),
            "fix_commit_shas": source.get("fix_commit_shas", "[]"),
            "retrieved_diff_test_file_status": source.get("test_evidence"),
            "retrieved_diff_test_paths": json.dumps(tests, ensure_ascii=False),
            "current_validator_path": item["source_files"].get("validator"),
            "historical_validator_sha256": old_validator_sha,
            "current_validator_sha256": current_validator_sha,
            "validator_changed_since_source_review": validator_changed,
            "current_formal_oracle_kind": formal.get("oracle_kind"),
            "current_formal_oracle_evidence_chain_verified": formal.get(
                "evidence_chain_verified"
            ),
            "current_formal_oracle_binding_keys_exact": formal.get("binding_keys_exact"),
            "current_candidate_binding_exact": candidate.get("binding_exact"),
            "current_candidate_control_pair": candidate.get("predicate_control_pair"),
            "current_binding_review_resolution_verified": readiness.get(
                "binding_review_resolution_verified"
            ),
            "current_formal_run_ready": readiness.get("formal_run_ready"),
            "strict_historical_reproduction_ready": bool(
                readiness.get("formal_run_ready")
                and source.get("apk_version_alignment") == "aligned"
                and trigger_status in {"aligned", "aligned_after_current_validator_repair"}
            ),
            "metadata_conflicts": ";".join(metadata_conflicts),
            "provenance_or_scope_gaps": evidence_text(source["unresolved"]),
        }
        rows.append(row)

        for index, url in enumerate(urls):
            mappings.append(
                {
                    "case_id": case_id,
                    "issue_cluster_id": cluster,
                    "source_ordinal": index + 1,
                    "source_type": "issue" if "/issues/" in url else "pull_request" if "/pull/" in url else "other",
                    "url": url,
                    "page_sha256": page_shas[index] if index < len(page_shas) else "",
                    "retrieval_status": "retrieved" if index < len(page_shas) else "missing",
                    "primary_title_if_applicable": source.get("primary_title", "") if index == 0 else "",
                    "primary_state_if_applicable": source.get("primary_state", "") if index == 0 else "",
                }
            )
    return rows, mappings


def build_adjudication_queue(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for row in rows:
        status = row["source_trigger_alignment_current_assessment"]
        if status == "contradicted":
            queue.append(
                {
                    "priority": "P0",
                    "item_id": f"{row['case_id']}-SOURCE-CONTRADICTION",
                    "case_id": row["case_id"],
                    "decision_needed": "Decide whether to exclude the case or replace its benchmark trigger with the retrieved issue trigger.",
                    "evidence": row["provenance_or_scope_gaps"],
                    "proposed_action": "Exclude from source-faithful effect claims until corrected and re-audited.",
                    "status": "open",
                }
            )
        elif status == "trigger_supported_binding_unverified":
            queue.append(
                {
                    "priority": "P0",
                    "item_id": f"{row['case_id']}-BINDING-METADATA",
                    "case_id": row["case_id"],
                    "decision_needed": "Resolve the conflict between the candidate binding audit and the formal-oracle binding assertion.",
                    "evidence": row["metadata_conflicts"]
                    or row["provenance_or_scope_gaps"],
                    "proposed_action": "Attach a verified binding-resolution receipt before source-faithful inclusion.",
                    "status": "open",
                }
            )
        elif status in {"overbroad", "partial_missing_state"}:
            queue.append(
                {
                    "priority": "P1",
                    "item_id": f"{row['case_id']}-TRIGGER-SCOPE",
                    "case_id": row["case_id"],
                    "decision_needed": "Correct the validator/action preconditions to match the retrieved issue evidence.",
                    "evidence": row["provenance_or_scope_gaps"],
                    "proposed_action": "Keep the case classified, but hold it from source-faithful effect estimates until scope is corrected.",
                    "status": "open",
                }
            )
        if row["case_id"] == "B028" and row["validator_changed_since_source_review"]:
            queue.append(
                {
                    "priority": "P0",
                    "item_id": "B028-STALE-AUDIT-METADATA",
                    "case_id": "B028",
                    "decision_needed": "Refresh candidate/source audit metadata after the literal-space validator repair.",
                    "evidence": row["metadata_conflicts"],
                    "proposed_action": "Record the current validator hash and rerun only offline control-pair/binding checks.",
                    "status": "open",
                }
            )

    queue.extend(
        [
            {
                "priority": "P0",
                "item_id": "GLOBAL-VERSION-ALIGNMENT",
                "case_id": "ALL",
                "decision_needed": "Choose whether the paper will make historical reproduction claims without report-to-installed-artifact version matches.",
                "evidence": "0/37 cases have proven public-report to installed-artifact version alignment.",
                "proposed_action": "Use frozen-artifact benchmark wording; do not call the suite a version-matched historical reproduction.",
                "status": "open_author_wording_decision",
            },
            {
                "priority": "P0",
                "item_id": "GLOBAL-REQUIREMENTS-ATTRIBUTION",
                "case_id": "ALL",
                "decision_needed": "Approve the best-effort requirements-obligation attribution and its confidence labels.",
                "evidence": "37/37 cases have obligation categories; these categories describe missing behavioral obligations rather than organizational process causes.",
                "proposed_action": "Report the assigned obligation family and evidence level; do not relabel it as a proven requirements-process cause.",
                "status": "open_author_wording_decision",
            },
        ]
    )
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    return sorted(queue, key=lambda item: (priority_order[item["priority"]], item["case_id"], item["item_id"]))


def counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row[key]) for row in rows).items()))


def markdown_count_table(title: str, values: dict[str, int]) -> str:
    lines = [f"### {title}", "", "| Value | Cases |", "|---|---:|"]
    lines.extend(f"| `{name}` | {count} |" for name, count in values.items())
    return "\n".join(lines)


def build_codebook() -> str:
    return """# Reviewer 2 Bug Classification Codebook (v2)

## Classification principle

The old five classes (`missing_or_blank`, `numeric_or_format`,
`lexical_sequence`, `external_state_or_multistep`, and `length_or_volume`)
describe **trigger form**. They are retained only as a secondary input axis.
They are not used as the primary Bug taxonomy.

The primary taxonomy is the **specification obligation implicated by the bug**:

| Primary family | Definition |
|---|---|
| `input_domain_and_validation_spec` | Valid/invalid input domains, normalization order, incremental editing grammar, and persisted-value validation. |
| `stateful_interaction_spec` | Preconditions and transitions spanning prior state, modes, loading state, or multi-action workflows. |
| `text_transformation_and_parser_spec` | Semantic preservation and boundary safety in suggestions, replacement, query, markup, or parsing logic. |
| `external_interoperability_and_resilience_spec` | Robust behavior across external services, apps, IMEs, or externally completed text. |
| `performance_and_timing_spec` | Responsiveness, debouncing, scheduling, and bounded work under repeated input. |
| `feedback_and_error_presentation_spec` | Correct, visible, localized, and actionable validation feedback. |
| `ui_component_and_affordance_spec` | Widget configuration and control enablement that constrain legal user actions. |
| `benchmark_provenance_defect` | Provenance exclusion, not an app-bug specification family: the retrieved source does not support a distinct app-bug case as encoded by the benchmark. |

These seven application-bug families are an operational coding scheme, not
official ISO category names. The scheme uses ISO/IEC/IEEE 29148:2018 as the
requirements-engineering frame and ISO/IEC 25010:2023 as the product-quality,
test-objective, and acceptance-criteria frame. The exact standards references
are recorded in `classification_basis_v2.md`.

## Independent secondary axes

1. `specification_obligation`: the concrete case-level obligation inferred from
   expected behavior, reproduction steps, stack traces, maintainer comments,
   and fix diffs.
2. `specification_attribution_basis` and `specification_attribution_confidence`:
   make best-effort inference explicit instead of leaving cases uncategorized.
3. `behavioral_failure_family`: crash, hang/crash, persistent state failure,
   incorrect/missing output, defective feedback, or invalid input acceptance.
4. `interaction_scope`: direct form action, same-field sequence, prior state or
   mode, multi-step same-app workflow, cross-app state, or timing/external
   service dependency.
5. `trigger_form_secondary`: the former five-way input grouping.
6. `constraint_visibility`: whether the constraint is visible, semantic-only,
   implicit/hidden, or hidden.
7. `implementation_fault_family`, cause text, evidence level, and confidence:
   every case receives a best-effort mechanism, clearly separating direct
   evidence from analyst inference.
8. `source_trigger_alignment_current_assessment`: whether the benchmark trigger
   matches the retrieved issue evidence.
9. `public_report_to_installed_artifact_alignment`: a separate version/provenance
   dimension.

## Evidence boundaries

- `current_formal_run_ready=true` means the current frozen benchmark has an
  operational oracle and execution contract. It does **not** prove that the
  installed artifact matches the version in the public report.
- A specification-obligation category identifies the missing behavioral
  contract implicated by the evidence; it is not an organizational-process
  root-cause claim.
- A PR containing test files does not automatically establish a case-specific
  regression test.
- Contradicted triggers are excluded from source-faithful effect claims until
  corrected. Partial or overbroad triggers are held until their scope is
  corrected. Binding conflicts require a verified resolution receipt.
- Outcome data are intentionally not used to assign any classification in this
  artifact, preventing result-driven recoding.
"""


def build_classification_basis_markdown(rows: list[dict[str, Any]]) -> str:
    app_bug_rows = [
        row
        for row in rows
        if row["specification_obligation_family"] != "benchmark_provenance_defect"
    ]
    provenance_rows = [
        row
        for row in rows
        if row["specification_obligation_family"] == "benchmark_provenance_defect"
    ]
    sections = [
        "# Reviewer 2 Bug Classification Basis (v2)",
        "",
        "## 1. Conceptual basis",
        "",
        "The coding target is the **observable specification obligation violated by each defect**. It is not the input string shape, the eventual crash type, or an unsupported claim about the project's requirements-development process.",
        "",
        f"- ISO/IEC/IEEE 29148:2018 defines the requirements-engineering process and the required requirements information items: {ISO_29148_URL}",
        f"- ISO/IEC 25010:2023 defines a product-quality reference model intended for specifying, measuring, and evaluating quality, including requirements, test objectives, quality-control criteria, and acceptance criteria: {ISO_25010_URL}",
        "- The seven application-bug families below are this study's operational coding scheme adapted to those frames. They are **not** claimed to be official ISO category labels.",
        "",
        "## 2. Evidence precedence",
        "",
        "For every case, evidence is read in this order:",
        "",
        "1. Issue report's expected behavior, actual behavior, and reproduction steps.",
        "2. Stack trace and named code/component location.",
        "3. Fix PR or commit diff, especially guards, state representations, parsing changes, and widget constraints.",
        "4. Maintainer diagnosis or reporter follow-up.",
        "5. Current benchmark validator only for operational trigger/oracle status, never as proof of the original application's specification.",
        "",
        "The first four sources determine the obligation and implementation-mechanism evidence. The fifth is tracked separately so current benchmark readiness cannot be confused with historical source fidelity.",
        "",
        "## 3. Mutually exclusive decision rules",
        "",
        "Apply the first matching rule. This makes the primary code reproducible and avoids counting a case in several headline classes:",
        "",
        "1. **Provenance exclusion:** the retrieved source does not support a distinct benchmark case. This is not counted as an application Bug family.",
        "2. **External interoperability and resilience:** an external service, app, IME, or externally completed text state is necessary to the failure.",
        "3. **Performance and timing:** rate, scheduling, debouncing, or responsiveness is necessary to the failure.",
        "4. **Feedback and error presentation:** the core action occurs, but feedback visibility, localization, correctness, or actionability is the primary violated obligation.",
        "5. **UI component and affordance:** widget configuration or control enablement is the primary violated constraint.",
        "6. **Text transformation and parser:** transformation, replacement, suggestion, query, markup, parsing, or boundary semantics are primary.",
        "7. **Stateful interaction:** prior configuration, mode, loading state, or a multi-action state transition is necessary.",
        "8. **Input domain and validation:** legal/illegal input, normalization order, incremental input grammar, or persisted-value validation is primary.",
        "",
        "Behavioral outcome, interaction scope, trigger form, implementation mechanism, source fidelity, and confidence remain independent secondary axes.",
        "",
        "## 4. Evidence-bound coding policy",
        "",
        "- `high`: the obligation is directly supported by explicit expected-versus-actual behavior, a stack/code location, a maintainer statement, or a fix diff.",
        "- `medium`: the obligation is the best explanation of the reproduced behavior, but the retrieved source lacks a fully binding fix or diagnosis.",
        "- No case is left uncategorized. Lower evidentiary strength is represented by confidence and basis fields rather than a placeholder category.",
        "- No row claims an organizational requirements-process root cause.",
        "- Outcome data are excluded from coding, preventing result-driven recategorization.",
        "",
        "## 5. Resulting scope",
        "",
        f"- Application Bug cases classified: **{len(app_bug_rows)}**.",
        f"- Provenance exclusions: **{len(provenance_rows)}** ({', '.join(row['case_id'] for row in provenance_rows)}).",
        "- Therefore the application-Bug distribution is computed over 36 cases, not 37; B021 is reported separately.",
        "",
        "## 6. Per-case decision trace",
        "",
        "| Case | Primary family | Concrete violated obligation | Decisive rule | Evidence basis | Confidence | Key evidence | Source URLs |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        key_evidence = str(row["expected_behavior_evidence"]).replace("|", "\\|")
        urls = ", ".join(json.loads(row["source_urls"])).replace("|", "\\|")
        sections.append(
            "| {case_id} | `{specification_obligation_family}` | {specification_obligation} | "
            "`{classification_decision_rule}` | `{specification_attribution_basis}` | "
            "`{specification_attribution_confidence}` | {key_evidence} | {urls} |".format(
                key_evidence=key_evidence,
                urls=urls,
                **row
            )
        )
    sections.extend(
        [
            "",
            "The machine-readable companion `classification_basis_v2.csv` also records source page hashes, implementation evidence, current trigger alignment, and analysis disposition for independent checking.",
        ]
    )
    return "\n".join(sections) + "\n"


def build_summary_markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    sections = [
        "# Reviewer 2 - 37 Bug Evidence Classification Summary (v2)",
        "",
        "This is an offline, outcome-independent reclassification of B001--B037.",
        "The primary taxonomy is the implicated specification obligation; observable failure and input-trigger form are secondary axes.",
        "",
        "## Key conclusions",
        "",
        f"- Coverage: **{summary['case_count']}/37** cases and **{summary['issue_cluster_count']}** issue clusters.",
        f"- Application-Bug specification coverage: **{summary['app_bug_case_count']}/36 classified**, with an evidence basis and confidence on every case.",
        f"- Provenance exclusions: **{summary['provenance_exclusion_count']}** ({', '.join(summary['provenance_exclusion_cases'])}); these are not counted as an application-Bug family.",
        f"- Organizational requirements-process cause: **{summary['requirements_process_root_cause_claimed_cases']}/37 claimed**; the table classifies behavioral obligations rather than inventing process history.",
        f"- Public-report to installed-artifact version alignment: **{summary['version_aligned_cases']}/37 proven aligned**.",
        f"- Current operational formal-run readiness: **{summary['current_formal_run_ready_cases']}/37**. This is not historical-version alignment.",
        f"- Strict historical reproduction readiness: **{summary['strict_historical_reproduction_ready_cases']}/37**.",
        "- The four source contradictions are B019, B021, B024, and B037.",
        "- B028's literal-space validator changed after the source review; its current predicate is repaired, but embedded candidate-audit metadata remains stale and needs a receipt refresh.",
        "- Five binding cases (B012, B013, B015, B033, B036) have a candidate/formal metadata conflict and remain in the adjudication queue.",
        "",
        markdown_count_table("Primary specification-obligation family (36 application Bugs)", summary["app_bug_specification_family_counts"]),
        "",
        markdown_count_table("Specification-attribution confidence", summary["specification_attribution_confidence_counts"]),
        "",
        markdown_count_table("Behavioral failure family (secondary)", summary["behavioral_failure_family_counts"]),
        "",
        markdown_count_table("Interaction scope", summary["interaction_scope_counts"]),
        "",
        markdown_count_table("Trigger form (secondary only)", summary["trigger_form_secondary_counts"]),
        "",
        markdown_count_table("Implementation-cause evidence level", summary["implementation_cause_evidence_level_counts"]),
        "",
        markdown_count_table("Reviewer analysis disposition", summary["reviewer_analysis_disposition_counts"]),
        "",
        "## Per-case classification",
        "",
        "| Case | App | Primary specification family | Concrete obligation | Failure | Cause family | Spec confidence |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        sections.append(
            "| {case_id} | {app} | `{specification_obligation_family}` | {specification_obligation} | "
            "`{behavioral_failure_family}` | `{implementation_fault_family}` | "
            "`{specification_attribution_confidence}` |".format(**row)
        )
    sections.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            "The table supports a reviewer-facing specification-obligation taxonomy plus observed failures, trigger complexity, implementation mechanisms, and evidence strength. It does not claim organizational requirements-process history or exact historical-version reproduction.",
        ]
    )
    return "\n".join(sections) + "\n"


def build_readme(input_path: Path, summary: dict[str, Any]) -> str:
    return f"""# Reviewer 2 - 37 Bug Evidence Reclassification (v2)

This directory is the current reviewer-facing evidence package for B001--B037.
It was generated offline from `{input_path.as_posix()}`. No device, model,
provider, or database was contacted.

## What changed from v1

- Specification obligation is now the primary Bug taxonomy.
- Observable failure behavior and trigger form are retained as secondary axes.
- Every case has a best-effort implementation mechanism and a concrete
  specification obligation, each with evidence basis and confidence.
- State/action scope, constraint visibility, implementation mechanism, evidence
  strength, specification obligation, issue cluster, version alignment,
  and current operational readiness are separate fields.
- Current benchmark readiness is explicitly separated from public-report to
  installed-artifact version alignment.
- Classification is outcome-independent; no Doubao or Claude result is used.

## Package contents

- `classification_codebook.md`: definitions and evidence rules.
- `classification_basis_v2.md` / `.csv`: conceptual basis, evidence precedence,
  deterministic decision rules, and a 37-case evidence trace.
- `bug_evidence_registry_v2.csv` / `.json`: 37-row case ledger.
- `case_issue_mapping_v2.csv`: source URL and page-hash mapping.
- `classification_summary_v2.json` / `.md`: aggregate and per-case summary.
- `author_adjudication_queue.csv`: unresolved source, binding, and wording decisions.
- `artifact_manifest.json`: input/output SHA-256 values.

## Current bottom line

- {summary['case_count']}/37 cases classified across {summary['issue_cluster_count']} issue clusters.
- {summary['app_bug_case_count']}/36 application Bugs have a specification-obligation category.
- {summary['provenance_exclusion_count']} provenance exclusion ({', '.join(summary['provenance_exclusion_cases'])}) is reported separately.
- {summary['requirements_process_root_cause_claimed_cases']}/37 make an organizational requirements-process claim.
- {summary['version_aligned_cases']}/37 public-report to installed-artifact version matches are proven.
- {summary['current_formal_run_ready_cases']}/37 are operationally formal-run ready in the current benchmark.
- {summary['strict_historical_reproduction_ready_cases']}/37 meet the stricter historical-reproduction condition.

The author queue must be adjudicated before freezing a paper table or joining
these classes to outcome data.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("output/e2_structure_v1_20260920/case_registry.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/reviewer2_bug_evidence_v2_20261006"),
    )
    args = parser.parse_args()

    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    rows, mappings = build_rows(registry)
    queue = build_adjudication_queue(rows)
    args.output.mkdir(parents=True, exist_ok=True)

    summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "classification_is_outcome_independent": True,
        "case_count": len(rows),
        "issue_cluster_count": len({row["issue_cluster_id"] for row in rows}),
        "specification_obligation_family_counts_including_provenance": counts(
            rows, "specification_obligation_family"
        ),
        "app_bug_specification_family_counts": counts(
            [
                row
                for row in rows
                if row["specification_obligation_family"]
                != "benchmark_provenance_defect"
            ],
            "specification_obligation_family",
        ),
        "specification_attribution_confidence_counts": counts(
            rows, "specification_attribution_confidence"
        ),
        "behavioral_failure_family_counts": counts(rows, "behavioral_failure_family"),
        "observable_symptom_counts": counts(rows, "observable_symptom"),
        "interaction_scope_counts": counts(rows, "interaction_scope"),
        "trigger_form_secondary_counts": counts(rows, "trigger_form_secondary"),
        "constraint_visibility_counts": counts(rows, "constraint_visibility"),
        "implementation_fault_family_counts": counts(rows, "implementation_fault_family"),
        "implementation_cause_evidence_level_counts": counts(
            rows, "implementation_cause_evidence_level"
        ),
        "implementation_cause_confidence_counts": counts(
            rows, "implementation_cause_confidence"
        ),
        "source_trigger_alignment_counts": counts(
            rows, "source_trigger_alignment_current_assessment"
        ),
        "reviewer_analysis_disposition_counts": counts(
            rows, "reviewer_analysis_disposition"
        ),
        "specification_classified_cases": sum(
            bool(row["specification_obligation_family"]) for row in rows
        ),
        "app_bug_case_count": sum(
            row["specification_obligation_family"] != "benchmark_provenance_defect"
            for row in rows
        ),
        "provenance_exclusion_count": sum(
            row["specification_obligation_family"] == "benchmark_provenance_defect"
            for row in rows
        ),
        "provenance_exclusion_cases": [
            row["case_id"]
            for row in rows
            if row["specification_obligation_family"] == "benchmark_provenance_defect"
        ],
        "requirements_process_root_cause_claimed_cases": sum(
            row["requirements_process_root_cause_claimed"] for row in rows
        ),
        "version_aligned_cases": sum(
            row["public_report_to_installed_artifact_alignment"] == "aligned"
            for row in rows
        ),
        "current_formal_run_ready_cases": sum(row["current_formal_run_ready"] for row in rows),
        "strict_historical_reproduction_ready_cases": sum(
            row["strict_historical_reproduction_ready"] for row in rows
        ),
        "adjudication_queue_items": len(queue),
        "interpretation": {
            "primary_taxonomy": "specification obligation implicated by the bug evidence",
            "behavioral_failure": "secondary outcome axis",
            "trigger_form": "secondary input axis",
            "process_root_cause": "not claimed; obligation classification is not process history",
            "current_formal_readiness": "operational benchmark readiness, not historical-version reproduction",
            "outcome_join": "prohibited until classification and adjudication are frozen",
        },
    }

    registry_json_path = args.output / "bug_evidence_registry_v2.json"
    registry_csv_path = args.output / "bug_evidence_registry_v2.csv"
    mapping_path = args.output / "case_issue_mapping_v2.csv"
    summary_json_path = args.output / "classification_summary_v2.json"
    summary_md_path = args.output / "classification_summary_v2.md"
    codebook_path = args.output / "classification_codebook.md"
    basis_md_path = args.output / "classification_basis_v2.md"
    basis_csv_path = args.output / "classification_basis_v2.csv"
    queue_path = args.output / "author_adjudication_queue.csv"
    readme_path = args.output / "README.md"
    validation_path = args.output / "validation_report.json"

    dump_json(registry_json_path, {"schema_version": SCHEMA_VERSION, "cases": rows})
    write_csv(registry_csv_path, rows, list(rows[0].keys()))
    write_csv(mapping_path, mappings, list(mappings[0].keys()))
    dump_json(summary_json_path, summary)
    summary_md_path.write_text(build_summary_markdown(summary, rows), encoding="utf-8")
    codebook_path.write_text(build_codebook(), encoding="utf-8")
    basis_md_path.write_text(build_classification_basis_markdown(rows), encoding="utf-8")
    basis_fields = [
        "case_id",
        "app",
        "specification_obligation_family",
        "specification_obligation",
        "classification_decision_rule",
        "specification_attribution_basis",
        "specification_attribution_confidence",
        "expected_behavior_evidence",
        "implementation_fault_family",
        "implementation_cause_best_effort",
        "implementation_cause_evidence_level",
        "implementation_cause_confidence",
        "source_trigger_alignment_current_assessment",
        "reviewer_analysis_disposition",
        "source_urls",
        "source_page_sha256s",
    ]
    write_csv(basis_csv_path, rows, basis_fields)
    write_csv(queue_path, queue, list(queue[0].keys()))
    readme_path.write_text(build_readme(args.registry, summary), encoding="utf-8")

    required_fields = [
        "case_id",
        "behavioral_failure_family",
        "observable_symptom",
        "interaction_scope",
        "trigger_form_secondary",
        "constraint_visibility",
        "implementation_fault_family",
        "implementation_cause_best_effort",
        "implementation_cause_evidence_level",
        "implementation_cause_confidence",
        "specification_obligation_family",
        "specification_obligation",
        "classification_decision_rule",
        "specification_attribution_basis",
        "specification_attribution_confidence",
        "reviewer_analysis_disposition",
    ]
    validation = {
        "schema_version": f"{SCHEMA_VERSION}-validation",
        "exact_case_id_sequence": [row["case_id"] for row in rows]
        == [f"B{i:03d}" for i in range(1, 38)],
        "unique_case_id_count": len({row["case_id"] for row in rows}),
        "case_count": len(rows),
        "required_fields": required_fields,
        "rows_with_blank_required_fields": [
            row["case_id"]
            for row in rows
            if any(row.get(field) in (None, "") for field in required_fields)
        ],
        "classification_is_outcome_independent": True,
        "source_mapping_row_count": len(mappings),
        "source_mapping_missing_pages": [
            {"case_id": item["case_id"], "url": item["url"]}
            for item in mappings
            if item["retrieval_status"] == "missing"
        ],
        "specification_classified_cases": summary["specification_classified_cases"],
        "app_bug_case_count": summary["app_bug_case_count"],
        "provenance_exclusion_count": summary["provenance_exclusion_count"],
        "classification_basis_row_count": len(rows),
        "requirements_process_root_cause_claimed_cases": summary[
            "requirements_process_root_cause_claimed_cases"
        ],
        "version_aligned_cases": summary["version_aligned_cases"],
        "strict_historical_reproduction_ready_cases": summary[
            "strict_historical_reproduction_ready_cases"
        ],
        "passed": bool(
            len(rows) == 37
            and len({row["case_id"] for row in rows}) == 37
            and summary["app_bug_case_count"] == 36
            and summary["provenance_exclusion_count"] == 1
            and not any(
                row.get(field) in (None, "")
                for row in rows
                for field in required_fields
            )
        ),
    }
    dump_json(validation_path, validation)

    output_files = sorted(
        path for path in args.output.iterdir() if path.name != "artifact_manifest.json"
    )
    manifest = {
        "schema_version": f"{SCHEMA_VERSION}-manifest",
        "generated_at_utc": summary["generated_at_utc"],
        "inputs": {args.registry.as_posix(): sha256_file(args.registry)},
        "outputs": {path.name: sha256_file(path) for path in output_files},
    }
    dump_json(args.output / "artifact_manifest.json", manifest)

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
