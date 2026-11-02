#!/usr/bin/env python3
"""Apply explicit, reviewable D adjudication to all 115 Page cases."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONFIRMED_MESSAGES = {
    "P001": ["Invalid email."],
    "P015": ["A password is required for re-login. If you skip this step, you won’t be able to access your account after signing out."],
    "P021": ["Please enter name"],
    "P022": ["User not found!"],
    "P027": ["Enter a US City, State or a ZIP or postal code"],
    "P035": ["This is not a valid GGID"],
    "P038": ["You must enter a valid zip code."],
    "P054": ["Please enter name"],
    "P056": ["Your passwords do not match"],
    "P057": ["Email address is invalid"],
    "P058": ["Your first name is required"],
    "P059": ["Please enter First Name."],
    "P069": ["Enter phone number", "Invalid input number"],
    "P078": ["ZIP code is required"],
    "P089": [
        "Code & approval required",
        "Group Code must contain letters and numbers",
        "Group code required",
        "To join this group (required)",
    ],
    "P090": ["Please enter a first name"],
    "P091": ["Title is required"],
    "P092": ["Enter a problem or project."],
    "P099": ["This email is not valid."],
    "P110": ["Please enter a valid phone number", "This field is required"],
    "P111": ["No languages found"],
    "P112": ["Sorry. No results found."],
    "P115": ["Email Address is a compulsory field."],
}

EXCLUSIONS = {
    "P010": (
        "weak_ambiguous_counter",
        "The candidate-specific counter is public evidence, but the same resource id occurs for two fields, so it is not safely field-bound.",
    ),
    "P013": (
        "excluded_redundant_with_base_context",
        "The changed text is already present in A/B/C context and adds no D information.",
    ),
    "P032": (
        "excluded_infrastructure_version_gate",
        "Update Required describes the installed App version gate, not the candidate input.",
    ),
    "P050": (
        "excluded_redundant_with_base_context",
        "The changed location prompt is already present in A/B/C context and adds no D information.",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    args = parser.parse_args()

    source = json.loads(args.input.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = []
    csv_rows: list[dict[str, Any]] = []
    for row in source["cases"]:
        case_id = row["case_id"]
        by_detail = {item["detail"]: item for item in row["d_observations"]}
        selected = [by_detail[text] for text in CONFIRMED_MESSAGES.get(case_id, []) if text in by_detail]
        missing = [text for text in CONFIRMED_MESSAGES.get(case_id, []) if text not in by_detail]
        if missing:
            raise RuntimeError(f"{case_id}: adjudicated messages disappeared: {missing}")
        if selected:
            needs_text_policy = any(item["selector_quality"] == "unique_text_fallback" for item in selected)
            status = (
                "confirmed_after_strict_text_selector_support"
                if needs_text_policy
                else "confirmed_current_resource_id_policy"
            )
            rationale = "Candidate-bound App-visible diagnostic, unique in the preserved after XML and not already supplied by A/B/C."
        elif case_id in EXCLUSIONS:
            status, rationale = EXCLUSIONS[case_id]
        elif not row["formal_applicable"]:
            status = "formal_case_not_applicable"
            rationale = "The case is outside the frozen formal Page experiment; retained in the 115-case audit."
        else:
            status = "no_candidate_bound_public_diagnostic_observed"
            rationale = "No candidate-specific public UI diagnostic survived before/after XML review."
        record = {
            "case_id": case_id,
            "package": row["package"],
            "formal_applicable": row["formal_applicable"],
            "d_adjudication": status,
            "d_rationale": rationale,
            "selected_d_evidence": selected,
            "selected_d_message_count": len(selected),
            "raw_observation_count": row["d_observation_count"],
            "raw_diagnostic_count": row["d_diagnostic_count"],
            "oracle_version": row["oracle_version"],
            "apk_version_code": row["apk_version_code"],
            "source_sha256": row["source_sha256"],
        }
        cases.append(record)
        csv_rows.append(
            {
                "case_id": case_id,
                "package": row["package"],
                "formal_applicable": row["formal_applicable"],
                "d_adjudication": status,
                "selected_d_messages": " | ".join(item["detail"] for item in selected),
                "selector_policies": " | ".join(sorted({item["selector_quality"] for item in selected})),
                "rationale": rationale,
            }
        )
    counts = Counter(row["d_adjudication"] for row in cases)
    summary = {
        "schema_version": "page-d-manual-adjudication-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_count": len(cases),
        "status_counts": dict(sorted(counts.items())),
        "confirmed_d_cases": sum(row["d_adjudication"].startswith("confirmed_") for row in cases),
        "confirmed_under_current_resource_id_policy": counts["confirmed_current_resource_id_policy"],
        "confirmed_after_strict_text_selector_support": counts["confirmed_after_strict_text_selector_support"],
        "weak_counter_cases": counts["weak_ambiguous_counter"],
        "adjudication_rule": "D must be candidate-bound, App-visible, new relative to A/B/C, and uniquely selectable in the preserved current XML.",
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps({"summary": summary, "cases": cases}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
