#!/usr/bin/env python3
"""Combine runtime, APK, and public-source evidence into a 152-case D/E matrix."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SURFACE_SPEC_CASES = {
    "P003": "feedback must contain at least 6 characters",
    "P010": "two visible 0/100 counters expose a 100-character limit",
    "P015": "password must contain at least 6 characters",
    "P056": "password must contain at least 10 characters",
    "P060": "comment should contain at least 30 words",
    "P089": "group code must contain at least 8 characters",
    "P115": "password must contain at least 8 characters",
}

NON_CONTRACT_ATTRS = {
    "hint",
    "imeOptions",
    "ems",
    "minEms",
    "maxEms",
    "inputType",
    "password",
    "minWidth",
    "maxWidth",
    "minHeight",
    "maxHeight",
}


def is_contract_attribute(name: str) -> bool:
    if name in NON_CONTRACT_ATTRS or name.startswith("layout_constraint"):
        return False
    if name in {"digits", "singleLine", "maxLength", "minLength", "maxLines", "minLines", "lines", "max", "min", "numeric"}:
        return True
    lowered = name.casefold()
    return any(token in lowered for token in ("validate", "regex", "pattern")) or lowered.endswith("length")


def normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def implementation_contracts(apk_case: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    contracts: list[dict[str, Any]] = []
    type_hints: list[dict[str, Any]] = []
    for binding in apk_case.get("target_layout_bindings") or []:
        for name, value in (binding.get("attributes") or {}).items():
            item = {
                "expected_fields": binding.get("expected_fields") or [],
                "layout": binding.get("layout"),
                "attribute": name,
                "value": value,
            }
            if name in {"inputType", "password"}:
                type_hints.append(item)
            elif is_contract_attribute(name):
                contracts.append(item)
    def aggregate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for item in items:
            key_data = {
                "expected_fields": item["expected_fields"],
                "attribute": item["attribute"],
                "value": item["value"],
            }
            key = json.dumps(key_data, ensure_ascii=False, sort_keys=True)
            if key not in grouped:
                grouped[key] = {**key_data, "layouts": []}
            if item["layout"] not in grouped[key]["layouts"]:
                grouped[key]["layouts"].append(item["layout"])
        return list(grouped.values())

    return aggregate(contracts), aggregate(type_hints)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "case_id",
        "task",
        "app_or_package",
        "formal_applicable",
        "d_status",
        "d_messages",
        "d_apk_resource_matches",
        "d_currently_executable",
        "e_surface_status",
        "e_surface_spec",
        "e_implementation_status",
        "e_implementation_contracts",
        "e_type_hints",
        "e_packaged_string_candidates",
        "e_official_independent_status",
        "e_bug_upper_bound_status",
        "e_private_oracle_upper_status",
        "recommended_use",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--page-d", required=True, type=Path)
    parser.add_argument("--page-local", required=True, type=Path)
    parser.add_argument("--bug-sources", required=True, type=Path)
    parser.add_argument("--apk", required=True, type=Path)
    parser.add_argument("--oracle-upper", required=True, type=Path)
    parser.add_argument("--play-sources", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()

    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    page_d = json.loads(args.page_d.read_text(encoding="utf-8"))
    page_local = json.loads(args.page_local.read_text(encoding="utf-8"))
    bug_sources = json.loads(args.bug_sources.read_text(encoding="utf-8"))
    apk = json.loads(args.apk.read_text(encoding="utf-8"))
    oracle_upper = json.loads(args.oracle_upper.read_text(encoding="utf-8"))
    play_sources = json.loads(args.play_sources.read_text(encoding="utf-8"))
    registry_by_id = {row["case_id"]: row for row in registry["cases"]}
    page_d_by_id = {row["case_id"]: row for row in page_d["cases"]}
    page_local_by_id = {row["case_id"]: row for row in page_local["cases"]}
    bug_by_id = {row["case_id"]: row for row in bug_sources["cases"]}
    apk_by_id = {row["case_id"]: row for row in apk["cases"]}
    oracle_upper_by_id = {row["case_id"]: row for row in oracle_upper["cases"]}
    play_by_id = {row["case_id"]: row for row in play_sources["cases"]}

    cases: list[dict[str, Any]] = []
    csv_rows: list[dict[str, Any]] = []
    for case_id in [f"P{index:03d}" for index in range(1, 116)] + [f"B{index:03d}" for index in range(1, 38)]:
        registry_case = registry_by_id[case_id]
        apk_case = apk_by_id[case_id]
        upper_case = oracle_upper_by_id[case_id]
        contracts, type_hints = implementation_contracts(apk_case)
        packaged_strings = apk_case.get("candidate_resource_strings") or []
        task = registry_case["task"]
        if task == "valid_pptr":
            d = page_d_by_id[case_id]
            selected_messages = [item["detail"] for item in d["selected_d_evidence"]]
            d_status = d["d_adjudication"]
            d_executable = d_status == "confirmed_current_resource_id_policy"
            surface_spec = SURFACE_SPEC_CASES.get(case_id)
            surface_status = "available_but_already_in_abc" if surface_spec else "not_found"
            upper_status = "not_applicable"
            play_case = play_by_id[case_id]
            official_status = (
                "current_play_listing_checked_no_field_constraint"
                if play_case["status"] == "retrieved"
                else "current_play_listing_unavailable"
            )
            app_or_package = registry_case["source_target"]
            formal_applicable = bool((registry_case.get("formal_applicability") or {}).get("in_scope"))
        else:
            bug = bug_by_id[case_id]
            selected_messages = []
            d_status = bug["d_status"]
            d_executable = False
            surface_spec = None
            surface_status = "not_a_page_surface_condition"
            upper_status = bug["e_upper_bound_status"]
            official_status = "not_established_from_independent_source"
            app_or_package = bug.get("app") or registry_case.get("mirror", {}).get("package")
            formal_applicable = True
            play_case = None

        d_apk_matches = [
            {
                "message": message,
                "resource_name": candidate["resource_name"],
                "resource_text": candidate["text"],
            }
            for message in selected_messages
            for candidate in packaged_strings
            if normalize(message) == normalize(candidate.get("text") or "")
        ]

        implementation_status = (
            "version_matched_apk_target_contract_candidate" if contracts else "not_found"
        )
        if d_executable and contracts:
            recommended = "D now; E_impl eligible as a separate sourced-implementation stratum"
        elif d_status == "confirmed_after_strict_text_selector_support" and contracts:
            recommended = "D after selector patch; E_impl eligible as a separate sourced-implementation stratum"
        elif d_executable:
            recommended = "D now; no incremental clean E found"
        elif d_status == "confirmed_after_strict_text_selector_support":
            recommended = "D after selector patch; no incremental clean E found"
        elif contracts:
            recommended = "No D; E_impl eligible as a separate sourced-implementation stratum"
        elif upper_status.startswith("available"):
            recommended = "C in main experiment; benchmark-linked E only as labelled leakage upper bound"
        elif surface_status == "available_but_already_in_abc":
            recommended = "C in main experiment; surface specification is already present in A/B/C"
        else:
            recommended = "C in main experiment; no defensible incremental D/E evidence found"

        record = {
            "case_id": case_id,
            "task": task,
            "app_or_package": app_or_package,
            "formal_applicable": formal_applicable,
            "d_status": d_status,
            "d_messages": selected_messages,
            "d_apk_resource_matches": d_apk_matches,
            "d_currently_executable": d_executable,
            "e": {
                "surface_status": surface_status,
                "surface_spec": surface_spec,
                "implementation_status": implementation_status,
                "implementation_contracts": contracts,
                "type_hints_not_treated_as_validation": type_hints,
                "packaged_string_candidate_count": len(packaged_strings),
                "packaged_strings_status": (
                    "requires_runtime_page_binding" if packaged_strings else "none"
                ),
                "official_independent_status": official_status,
                "current_play_source": (
                    {
                        "status": play_case["status"],
                        "url": play_case["url"],
                        "title": play_case.get("title"),
                        "developer_websites": play_case.get("developer_websites") or [],
                        "description_constraint_matches": play_case.get("description_constraint_matches") or [],
                    }
                    if play_case
                    else None
                ),
                "bug_upper_bound_status": upper_status,
                "private_oracle_upper": {
                    "status": upper_case["e_status"],
                    "validator_path": upper_case["validator_path"],
                    "validator_sha256": upper_case["validator_sha256"],
                    "leakage": upper_case["leakage"],
                },
            },
            "apk_evidence": {
                "status": apk_case["status"],
                "source": apk_case.get("source"),
                "source_sha256": apk_case.get("actual_source_sha256"),
                "version_code": apk_case.get("version_code"),
                "target_layout_binding_count": apk_case.get("target_layout_binding_count", 0),
            },
            "recommended_use": recommended,
        }
        cases.append(record)
        csv_rows.append(
            {
                "case_id": case_id,
                "task": task,
                "app_or_package": app_or_package,
                "formal_applicable": formal_applicable,
                "d_status": d_status,
                "d_messages": " | ".join(selected_messages),
                "d_apk_resource_matches": " | ".join(
                    f"{item['resource_name']}={item['resource_text']}" for item in d_apk_matches
                ),
                "d_currently_executable": d_executable,
                "e_surface_status": surface_status,
                "e_surface_spec": surface_spec or "",
                "e_implementation_status": implementation_status,
                "e_implementation_contracts": " | ".join(
                    f"{item['attribute']}={item['value']}@{'+'.join(item['expected_fields'])}"
                    for item in contracts
                ),
                "e_type_hints": " | ".join(
                    f"{item['attribute']}={item['value']}@{'+'.join(item['expected_fields'])}"
                    for item in type_hints
                ),
                "e_packaged_string_candidates": len(packaged_strings),
                "e_official_independent_status": official_status,
                "e_bug_upper_bound_status": upper_status,
                "e_private_oracle_upper_status": upper_case["e_status"],
                "recommended_use": recommended,
            }
        )

    page_cases = [row for row in cases if row["task"] == "valid_pptr"]
    bug_cases = [row for row in cases if row["task"] == "invalid_bug"]
    summary = {
        "schema_version": "de-case-matrix-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_count": len(cases),
        "page_count": len(page_cases),
        "bug_count": len(bug_cases),
        "page_d_status_counts": dict(sorted(Counter(row["d_status"] for row in page_cases).items())),
        "page_d_confirmed": sum(row["d_status"].startswith("confirmed_") for row in page_cases),
        "page_d_currently_executable": sum(row["d_currently_executable"] for row in page_cases),
        "page_d_after_selector_patch": sum(row["d_status"] == "confirmed_after_strict_text_selector_support" for row in page_cases),
        "page_d_with_exact_apk_resource_backing": sum(bool(row["d_apk_resource_matches"]) for row in page_cases),
        "page_surface_specs_found": sum(row["e"]["surface_spec"] is not None for row in page_cases),
        "page_surface_specs_incremental": 0,
        "page_apk_contract_candidates": sum(bool(row["e"]["implementation_contracts"]) for row in page_cases),
        "page_apk_contract_candidates_formal": sum(
            row["formal_applicable"] and bool(row["e"]["implementation_contracts"])
            for row in page_cases
        ),
        "bug_d_available": 0,
        "bug_apk_contract_candidates": sum(bool(row["e"]["implementation_contracts"]) for row in bug_cases),
        "bug_clean_official_e": 0,
        "bug_upper_bound_e": sum(row["e"]["bug_upper_bound_status"].startswith("available") for row in bug_cases),
        "private_oracle_upper_bound_e": sum(
            row["e"]["private_oracle_upper"]["status"] == "available_private_oracle_upper_bound_only"
            for row in cases
        ),
        "page_play_listings_retrieved": sum(
            row["e"]["current_play_source"] and row["e"]["current_play_source"]["status"] == "retrieved"
            for row in page_cases
        ),
        "page_play_listings_unavailable": sum(
            row["e"]["current_play_source"] and row["e"]["current_play_source"]["status"] != "retrieved"
            for row in page_cases
        ),
        "page_play_listing_field_constraints": sum(
            bool((row["e"]["current_play_source"] or {}).get("description_constraint_matches"))
            for row in page_cases
        ),
        "apk_cases_mined_and_hash_matched": sum(row["apk_evidence"]["status"] == "mined" for row in cases),
        "input_sha256": {
            "registry": sha256_file(args.registry),
            "page_d": sha256_file(args.page_d),
            "page_local": sha256_file(args.page_local),
            "bug_sources": sha256_file(args.bug_sources),
            "apk": sha256_file(args.apk),
            "oracle_upper": sha256_file(args.oracle_upper),
            "play_sources": sha256_file(args.play_sources),
        },
        "boundaries": {
            "D": "candidate-bound current App-visible diagnostic; no XML or screenshot is sent to the model",
            "E_surface": "public surface constraint; all seven found Page cases already expose it in A/B/C",
            "E_impl": "exact-field contract mined from the frozen APK; source-backed but not called an official business specification",
            "E_upper": "Bug issue/fix evidence; exact-trigger leakage, allowed only as an explicitly labelled upper bound",
            "E_oracle_upper": "private validator predicate; complete answer leakage, allowed only as an explicitly labelled ceiling",
        },
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps({"summary": summary, "cases": cases}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(args.output_csv, csv_rows)

    d_ids = [row["case_id"] for row in page_cases if row["d_status"].startswith("confirmed_")]
    page_e_ids = [row["case_id"] for row in page_cases if row["e"]["implementation_contracts"]]
    bug_e_ids = [row["case_id"] for row in bug_cases if row["e"]["implementation_contracts"]]
    report = f"""# D/E evidence audit for 115 Page + 37 Bug cases

Generated: {summary['created_at_utc']}

## Result

- Every one of the 152 cases has a row in `de_case_matrix.csv` and a provenance-rich record in `de_case_matrix.json`.
- Page D: {summary['page_d_confirmed']} confirmed cases. {summary['page_d_currently_executable']} use the existing unique resource-id policy; {summary['page_d_after_selector_patch']} require a strict unique text/content-description selector policy. {summary['page_d_with_exact_apk_resource_backing']} cases also have an exact message match in the frozen APK resources; the others are dynamic/server/WebView text and remain backed by hashed runtime XML.
- Page surface specifications: {summary['page_surface_specs_found']} found, but all are already in A/B/C, so they do not create an incremental E contrast.
- APK-bound implementation contracts: {summary['page_apk_contract_candidates']} Page cases ({summary['page_apk_contract_candidates_formal']} in the formal set) and {summary['bug_apk_contract_candidates']} Bug cases. These are suitable for a separately named E_impl stratum, not for a claim of official business specifications.
- Bug reports/fixes: all {summary['bug_upper_bound_e']} cases have benchmark-linked specification evidence for an explicitly labelled leakage upper bound. They must not be used as ordinary E.
- Private benchmark predicates: all {summary['private_oracle_upper_bound_e']} Page/Bug cases have a hash-verified executable specification, extracted separately as E_oracle_upper. It is complete answer leakage and must never be presented as ordinary E.
- Independent official specifications found in the audited sources: 0. Local prompts/configs, runtime XML, all frozen APK resources, and the complete public Bug issue/PR chain were audited. This does not claim that no external document exists; a broad external product-documentation search remains non-exhaustive and would still require screen/version matching.
- Current Google Play package audit: {summary['page_play_listings_retrieved']} listings retrieved, {summary['page_play_listings_unavailable']} returned 404/unavailable, and {summary['page_play_listing_field_constraints']} listing descriptions stated a relevant field-level constraint. Developer sites were recorded but not treated as version-matched screen specifications.

## Case sets

- Confirmed Page D ({len(d_ids)}): {', '.join(d_ids)}
- Page E_impl candidates ({len(page_e_ids)}): {', '.join(page_e_ids) if page_e_ids else 'none'}
- Bug E_impl candidates ({len(bug_e_ids)}): {', '.join(bug_e_ids) if bug_e_ids else 'none'}
- Surface specification already in A/B/C ({len(SURFACE_SPEC_CASES)}): {', '.join(SURFACE_SPEC_CASES)}

## Experimental interpretation

Use A/B/C on the full formal set. D should be evaluated only on the confirmed D subset, with a paired C comparison on the same cases. E should be split into E_impl (sourced APK field contracts) and E_upper (bug-linked leakage upper bound). Do not merge E_upper into the main effectiveness claim.
"""
    args.report.write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
