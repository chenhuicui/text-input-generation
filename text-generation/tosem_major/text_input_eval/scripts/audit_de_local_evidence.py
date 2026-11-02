#!/usr/bin/env python3
"""Build a complete local-evidence inventory for D and E candidates.

The audit covers every Page and Bug case.  D evidence is mined only from
candidate-failing App executions or frozen negative controls.  E candidates
are separated by provenance so UI text, runtime diagnostics, researcher
oracles, and independent specifications cannot be conflated.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET


TEXT_ATTRIBUTES = ("text", "hint", "content-desc")
INPUT_CLASSES = {
    "android.widget.EditText",
    "android.widget.AutoCompleteTextView",
    "android.inputmethodservice.ExtractEditText",
}

DIAGNOSTIC_RE = re.compile(
    r"(?:\binvalid\b|\bnot (?:a )?valid\b|\brequired\b|\bcompulsory\b|\bmust\b|"
    r"\bminimum\b|\bmaximum\b|\bat least\b|\bat most\b|\btoo short\b|"
    r"\btoo long\b|\bdo(?:es)? not match\b|\bdon't match\b|\bcan't be\b|"
    r"\bcannot be\b|\bnot allowed\b|\bforbidden\b|\bplease enter\b|"
    r"\benter (?:a|an|your )?(?:valid )?(?:email|phone|number|zip|city|name|code)\b|"
    r"\bno (?:results?|languages?) found\b|\bnot found\b|\bsingle[- ]line\b|"
    r"\bbetween\s+\d|\bcharacters? entered\b|\bwrong (?:email|password|number|code)\b)",
    re.IGNORECASE,
)
COUNTER_RE = re.compile(r"(?:\b\d+\s*(?:of|/)\s*\d+\b|characters? entered)", re.IGNORECASE)
INFRA_RE = re.compile(
    r"(?:network|offline|connect(?:ion|ing)?|server|service unavailable|timeout|timed out|"
    r"something went wrong|trouble connecting|try again|temporarily unavailable|rate limit)",
    re.IGNORECASE,
)
GENERIC_RE = re.compile(
    r"^(?:error|warning|failed|failure|unable to continue|something went wrong[.! ]*)$",
    re.IGNORECASE,
)
EXPLICIT_SURFACE_RE = re.compile(
    r"(?:\bat least\b|\bat most\b|\bminimum\b|\bmaximum\b|\bmust\b|\brequired\b|"
    r"\bcompulsory\b|\bonly\b|\bbetween\b|\bno more than\b|\bup to\b|"
    r"\b\d+\s*(?:characters?|words?|digits?)\b|\b\d+\s*/\s*\d+\b|"
    r"\bsingle[- ]line\b|\bvalid (?:email|phone|number|zip|url|code)\b)",
    re.IGNORECASE,
)
TYPE_HINT_RE = re.compile(
    r"\b(?:email|e-mail|password|phone|zip(?: code)?|postal code|url|website|date|time|"
    r"promo code|coupon|group code|number|amount|price|name|address|city|state)\b",
    re.IGNORECASE,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def visible_nodes(path: Path, package: str) -> list[dict[str, str]]:
    root = ET.fromstring(path.read_bytes())
    nodes: list[dict[str, str]] = []
    for node in root.iter("node"):
        attributes = {str(key): str(value) for key, value in node.attrib.items()}
        if attributes.get("package") != package:
            continue
        if attributes.get("visible-to-user") == "false":
            continue
        nodes.append(attributes)
    return nodes


def attribute_index(nodes: Iterable[dict[str, str]]) -> dict[tuple[str, str, str, str], str]:
    result: dict[tuple[str, str, str, str], str] = {}
    for node in nodes:
        package = node.get("package", "")
        class_name = node.get("class", "")
        resource_id = node.get("resource-id", "")
        if class_name in INPUT_CLASSES or class_name.endswith("EditText"):
            continue
        for attribute in TEXT_ATTRIBUTES:
            detail = node.get(attribute, "").strip()
            if detail:
                result[(package, class_name, resource_id, attribute)] = detail
    return result


def classify_detail(detail: str, resource_id: str, attribute: str) -> str:
    normalized = normalize_text(detail)
    resource_lower = resource_id.casefold()
    if normalized in {"error", "warning"} or (
        attribute == "content-desc" and normalized in {"error", "warning"}
    ):
        return "icon_only"
    if INFRA_RE.search(detail):
        return "infrastructure_or_generic_remote_failure"
    if GENERIC_RE.fullmatch(detail.strip()):
        return "generic_failure"
    if DIAGNOSTIC_RE.search(detail) or any(
        token in resource_lower
        for token in ("error", "invalid", "required", "wrong_", "validation")
    ):
        if re.search(r"\bno (?:results?|languages?) found\b", detail, re.IGNORECASE):
            return "result_diagnostic"
        return "input_diagnostic"
    if COUNTER_RE.search(detail) or "counter" in resource_lower:
        return "counter_or_limit_state"
    return "contextual_ui_change"


def selector_for(node: dict[str, str], after_nodes: list[dict[str, str]], attribute: str) -> tuple[dict[str, str], str]:
    package = node.get("package", "")
    class_name = node.get("class", "")
    resource_id = node.get("resource-id", "")
    detail = node.get(attribute, "").strip()
    if resource_id:
        selector = {"package": package, "class": class_name, "resource-id": resource_id}
        count = sum(
            all(candidate.get(key) == value for key, value in selector.items())
            for candidate in after_nodes
        )
        return selector, "stable_resource_id_unique" if count == 1 else "resource_id_not_unique"
    selector = {"package": package, "class": class_name, attribute: detail}
    count = sum(
        all(candidate.get(key) == value for key, value in selector.items())
        for candidate in after_nodes
    )
    return selector, "unique_text_fallback" if count == 1 else "text_fallback_not_unique"


def changed_messages(
    before_path: Path,
    after_path: Path,
    package: str,
    base_context: str,
    source: dict[str, Any],
) -> list[dict[str, Any]]:
    if not before_path.is_file() or not after_path.is_file():
        return []
    try:
        before_nodes = visible_nodes(before_path, package)
        after_nodes = visible_nodes(after_path, package)
    except (ET.ParseError, OSError):
        return []
    before = attribute_index(before_nodes)
    base_normalized = normalize_text(base_context)
    observations: list[dict[str, Any]] = []
    for node in after_nodes:
        class_name = node.get("class", "")
        if class_name in INPUT_CLASSES or class_name.endswith("EditText"):
            continue
        for attribute in TEXT_ATTRIBUTES:
            detail = node.get(attribute, "").strip()
            if not detail or len(detail) > 1000:
                continue
            key = (package, class_name, node.get("resource-id", ""), attribute)
            before_value = before.get(key)
            if before_value == detail:
                continue
            selector, selector_quality = selector_for(node, after_nodes, attribute)
            category = classify_detail(detail, node.get("resource-id", ""), attribute)
            detail_normalized = normalize_text(detail)
            already_in_base = bool(detail_normalized and detail_normalized in base_normalized)
            observations.append(
                {
                    "detail": detail,
                    "attribute": attribute,
                    "before_value": before_value,
                    "selector": selector,
                    "selector_quality": selector_quality,
                    "category": category,
                    "already_in_base_context": already_in_base,
                    "source": source,
                }
            )
    return observations


def control_observations(
    repo: Path,
    control_path: Path,
    package: str,
    base_context: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    control = load_json(control_path)
    negative = control.get("negative_control") or {}
    baseline_name = (negative.get("baseline") or {}).get("xml")
    after_name = (negative.get("after") or {}).get("xml")
    metadata = {
        "control_result": str(control_path.relative_to(repo)),
        "candidate": negative.get("candidate"),
        "goal_observed": negative.get("goal_observed"),
        "negative_observation": negative.get("negative_observation"),
        "negative_assertion": negative.get("negative_assertion"),
    }
    if not baseline_name or not after_name:
        metadata["evidence_status"] = "missing_negative_xml_reference"
        return [], metadata
    baseline = control_path.parent / baseline_name
    after = control_path.parent / after_name
    metadata.update(
        {
            "evidence_status": "available" if baseline.is_file() and after.is_file() else "missing_file",
            "before_xml": str(baseline.relative_to(repo)) if baseline.is_relative_to(repo) else str(baseline),
            "after_xml": str(after.relative_to(repo)) if after.is_relative_to(repo) else str(after),
            "before_sha256": sha256_file(baseline) if baseline.is_file() else None,
            "after_sha256": sha256_file(after) if after.is_file() else None,
        }
    )
    source = {
        "kind": "frozen_negative_control",
        "control_result": metadata["control_result"],
        "before_xml": metadata["before_xml"],
        "after_xml": metadata["after_xml"],
    }
    return changed_messages(baseline, after, package, base_context, source), metadata


def smoke_observations(
    repo: Path,
    coverage_path: Path,
    package_by_case: dict[str, str],
    base_by_case: dict[str, str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    audit = {"coverage": str(coverage_path.relative_to(repo)), "failed_executions": 0, "missing_xml": []}
    if not coverage_path.is_file():
        audit["status"] = "coverage_missing"
        return result, audit
    coverage = load_json(coverage_path)
    for source in coverage.get("sources", []):
        database_path = repo / source["db"]
        selected_cases = set(source.get("cases", []))
        if not database_path.is_file():
            continue
        connection = sqlite3.connect(database_path)
        try:
            rows = connection.execute("SELECT dimensions, events, result FROM trajectories").fetchall()
        finally:
            connection.close()
        for dimensions_raw, events_raw, result_raw in rows:
            dimensions = json.loads(dimensions_raw)
            case_id = dimensions.get("case_id")
            if case_id not in selected_cases or case_id not in package_by_case:
                continue
            trajectory_result = json.loads(result_raw) if result_raw else {}
            attempt_id = trajectory_result.get("attempt_id")
            if not attempt_id:
                continue
            for event in json.loads(events_raw):
                if event.get("event") != "execution" or event.get("verdict") != "fail":
                    continue
                observation = event.get("observation") or {}
                if observation.get("run_status") != "ok" or observation.get("behavior_verdict") is not False:
                    continue
                request_id = observation.get("request_id")
                declared = {
                    item.get("path"): item
                    for item in observation.get("evidence", [])
                    if isinstance(item, dict)
                }
                if not request_id or "before.xml" not in declared or "after.xml" not in declared:
                    audit["missing_xml"].append(
                        {"case_id": case_id, "attempt_id": attempt_id, "request_id": request_id}
                    )
                    continue
                evidence_dir = database_path.parent / "evidence" / attempt_id / request_id
                before = evidence_dir / "before.xml"
                after = evidence_dir / "after.xml"
                if not before.is_file() or not after.is_file():
                    audit["missing_xml"].append(
                        {"case_id": case_id, "attempt_id": attempt_id, "request_id": request_id}
                    )
                    continue
                if sha256_file(before) != declared["before.xml"].get("sha256") or sha256_file(after) != declared["after.xml"].get("sha256"):
                    audit["missing_xml"].append(
                        {"case_id": case_id, "attempt_id": attempt_id, "request_id": request_id, "reason": "hash_mismatch"}
                    )
                    continue
                audit["failed_executions"] += 1
                source_record = {
                    "kind": "candidate_bound_smoke_failure",
                    "database": str(database_path.relative_to(repo)),
                    "attempt_id": attempt_id,
                    "request_id": request_id,
                    "turn": event.get("turn"),
                    "before_xml": str(before.relative_to(repo)),
                    "after_xml": str(after.relative_to(repo)),
                    "candidate_hash": observation.get("candidate_hash"),
                    "oracle_version": observation.get("oracle_version"),
                }
                result[case_id].extend(
                    changed_messages(
                        before,
                        after,
                        package_by_case[case_id],
                        base_by_case[case_id],
                        source_record,
                    )
                )
    audit["status"] = "complete"
    audit["missing_xml_count"] = len(audit["missing_xml"])
    return result, audit


def observation_key(item: dict[str, Any]) -> str:
    key = {
        "detail": item["detail"],
        "attribute": item["attribute"],
        "selector": item["selector"],
        "category": item["category"],
    }
    return json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def merge_observations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in items:
        key = observation_key(item)
        if key not in grouped:
            grouped[key] = {**item, "sources": [item["source"]], "occurrences": 1}
            grouped[key].pop("source", None)
        else:
            grouped[key]["sources"].append(item["source"])
            grouped[key]["occurrences"] += 1
            grouped[key]["already_in_base_context"] = (
                grouped[key]["already_in_base_context"] and item["already_in_base_context"]
            )
            qualities = {grouped[key]["selector_quality"], item["selector_quality"]}
            if "stable_resource_id_unique" in qualities:
                grouped[key]["selector_quality"] = "stable_resource_id_unique"
            elif "unique_text_fallback" in qualities:
                grouped[key]["selector_quality"] = "unique_text_fallback"
    return sorted(
        grouped.values(),
        key=lambda item: (
            item["category"],
            item["already_in_base_context"],
            item["detail"].casefold(),
        ),
    )


def d_grade(observations: list[dict[str, Any]]) -> str:
    diagnostic = [
        item
        for item in observations
        if item["category"] in {"input_diagnostic", "result_diagnostic"}
    ]
    if any(
        item["selector_quality"] == "stable_resource_id_unique"
        and not item["already_in_base_context"]
        for item in diagnostic
    ):
        return "strong_resource_id_candidate"
    if any(
        item["selector_quality"] == "unique_text_fallback"
        and not item["already_in_base_context"]
        for item in diagnostic
    ):
        return "candidate_requires_text_selector_policy"
    if diagnostic:
        return "diagnostic_redundant_or_unstable"
    if observations:
        return "only_generic_contextual_or_infrastructure_changes"
    return "no_public_diagnostic_observed"


def page_surface_constraints(prompt: dict[str, Any]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for section in ("component", "adjacent"):
        text = str(prompt.get(section) or "")
        for sentence in re.split(r"(?<=[.!?])\s+|\\n|'\s*,\s*'", text):
            cleaned = sentence.strip(" []'\"")
            if not cleaned:
                continue
            if EXPLICIT_SURFACE_RE.search(cleaned):
                candidates.append({"kind": "explicit_surface_constraint", "text": cleaned, "section": section})
            elif TYPE_HINT_RE.search(cleaned):
                candidates.append({"kind": "surface_type_hint", "text": cleaned, "section": section})
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for item in candidates:
        unique[(item["kind"], normalize_text(item["text"]))] = item
    return list(unique.values())


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = (repo / args.output).resolve() if not args.output.is_absolute() else args.output
    output.mkdir(parents=True, exist_ok=True)

    registry = load_json(repo / "output/e2_structure_v1_20260920/case_registry.json")
    oracle_document = load_json(repo / "output/e2_structure_v1_20260920/formal_oracles.json")
    page_sources = load_json(repo / "output/tosem_spec_source_audit_20260908/page_source_inventory.json")
    bug_sources = load_json(repo / "output/tosem_spec_source_audit_20260908/bug_source_inventory.json")
    page_prompts = load_json(repo / "tosem_major/apk_info/cache/pptr_prompts.json")
    bug_prompts = load_json(repo / "tosem_major/apk_info/cache/bug_prompts.json")

    registry_by_case = {item["case_id"]: item for item in registry["cases"]}
    oracle_by_case = {item["case_id"]: item for item in oracle_document["cases"]}
    source_by_page = {item["case_id"]: item for item in page_sources}
    prompt_by_package = {item["app_id"]: item for item in page_prompts}
    bug_prompt_by_id = {f"B{int(item['page_id']):03d}": item for item in bug_prompts}

    page_ids = [f"P{index:03d}" for index in range(1, 116)]
    package_by_case = {case_id: registry_by_case[case_id]["source_target"] for case_id in page_ids}
    base_by_case = {
        case_id: "\n".join(
            str(prompt_by_package[package_by_case[case_id]].get(key) or "")
            for key in ("global", "component", "adjacent", "restrictive", "guiding")
        )
        for case_id in page_ids
    }
    smoke_by_case, smoke_audit = smoke_observations(
        repo,
        repo / "output/e2_structure_v1_app_smoke_doubao_r1_authoritative_coverage_20260923.json",
        package_by_case,
        base_by_case,
    )

    page_rows: list[dict[str, Any]] = []
    page_csv: list[dict[str, Any]] = []
    for case_id in page_ids:
        registry_case = registry_by_case[case_id]
        package = package_by_case[case_id]
        oracle = oracle_by_case.get(case_id)
        observations: list[dict[str, Any]] = list(smoke_by_case.get(case_id, []))
        control_meta: dict[str, Any] | None = None
        if oracle and (oracle.get("calibration") or {}).get("negative_control"):
            relative = oracle["calibration"]["negative_control"].split("#", 1)[0]
            control_path = repo / "output/e2_structure_v1_20260920" / relative
            control_items, control_meta = control_observations(
                repo, control_path, package, base_by_case[case_id]
            )
            observations.extend(control_items)
        else:
            candidates = sorted(
                (repo / "output/e2_structure_v1_20260920/live_calibration/cases" / case_id).glob(
                    "**/control_result.json"
                ),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for control_path in candidates:
                control_items, candidate_meta = control_observations(
                    repo, control_path, package, base_by_case[case_id]
                )
                if candidate_meta.get("evidence_status") == "available":
                    observations.extend(control_items)
                    control_meta = candidate_meta
                    break
        merged = merge_observations(observations)
        grade = d_grade(merged)
        applicable = bool((registry_case.get("formal_applicability") or {}).get("in_scope"))
        surface = page_surface_constraints(prompt_by_package[package])
        row = {
            "case_id": case_id,
            "task": "valid_pptr",
            "package": package,
            "formal_applicable": applicable,
            "formal_exclusion": None if applicable else registry_case.get("formal_applicability"),
            "oracle_version": oracle.get("version") if oracle else None,
            "d_status": grade if applicable else f"formal_not_applicable__{grade}",
            "d_observations": merged,
            "d_observation_count": len(merged),
            "d_diagnostic_count": sum(
                item["category"] in {"input_diagnostic", "result_diagnostic"} for item in merged
            ),
            "control_evidence": control_meta,
            "surface_constraints": surface,
            "surface_constraint_count": len(surface),
            "e_status": (
                "surface_constraint_already_in_base_context"
                if any(item["kind"] == "explicit_surface_constraint" for item in surface)
                else "no_independent_specification_in_local_materials"
            ),
            "external_documentation_status": source_by_page[case_id].get("external_documentation_status"),
            "manifest_path": registry_case.get("mirror", {}).get("manifest_path"),
            "apk_version_code": registry_case.get("mirror", {}).get("version_code"),
            "source_sha256": registry_case.get("mirror", {}).get("source_sha256"),
        }
        page_rows.append(row)
        best_details = [
            item["detail"]
            for item in merged
            if item["category"] in {"input_diagnostic", "result_diagnostic"}
        ]
        page_csv.append(
            {
                "case_id": case_id,
                "package": package,
                "formal_applicable": applicable,
                "d_status": row["d_status"],
                "d_diagnostic_count": row["d_diagnostic_count"],
                "d_candidate_messages": " | ".join(best_details),
                "surface_constraint_count": row["surface_constraint_count"],
                "e_status": row["e_status"],
            }
        )

    bug_rows: list[dict[str, Any]] = []
    bug_csv: list[dict[str, Any]] = []
    bug_source_by_id = {item["id"]: item for item in bug_sources}
    for index in range(1, 38):
        case_id = f"B{index:03d}"
        registry_case = registry_by_case[case_id]
        source = bug_source_by_id[case_id]
        prompt = bug_prompt_by_id[case_id]
        row = {
            "case_id": case_id,
            "task": "invalid_bug",
            "app": source.get("app"),
            "package": registry_case.get("mirror", {}).get("package"),
            "apk_version_code": registry_case.get("mirror", {}).get("version_code"),
            "source_sha256": registry_case.get("mirror", {}).get("source_sha256"),
            "category": source.get("category"),
            "benchmark_trigger_description": source.get("description"),
            "d_status": "not_available_under_frozen_string_predicate_protocol",
            "d_reason": (
                "The frozen Bug backend evaluates a string predicate and produces no candidate-bound App UI XML; "
                "task-oracle predicate_false feedback is already C, not D."
            ),
            "e_status": "public_issue_or_pr_requires_independence_and_leakage_review",
            "e_source_urls": source.get("urls", []),
            "oracle_leakage_risk": "high_exact_trigger_is_benchmark_ground_truth",
            "base_prompt": {
                "global": prompt.get("global"),
                "component": prompt.get("component"),
                "adjacent": prompt.get("adjacent"),
            },
            "manifest_path": registry_case.get("mirror", {}).get("manifest_path"),
        }
        bug_rows.append(row)
        bug_csv.append(
            {
                "case_id": case_id,
                "app": row["app"],
                "package": row["package"],
                "category": row["category"],
                "d_status": row["d_status"],
                "e_status": row["e_status"],
                "source_urls": " | ".join(row["e_source_urls"]),
                "benchmark_trigger_description": row["benchmark_trigger_description"],
            }
        )

    summary = {
        "schema_version": "de-spec-audit-local-evidence-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "page_cases": len(page_rows),
        "bug_cases": len(bug_rows),
        "formal_applicable_pages": sum(row["formal_applicable"] for row in page_rows),
        "page_d_status_counts": dict(
            sorted(
                {
                    status: sum(row["d_status"] == status for row in page_rows)
                    for status in {row["d_status"] for row in page_rows}
                }.items()
            )
        ),
        "pages_with_any_diagnostic": sum(row["d_diagnostic_count"] > 0 for row in page_rows),
        "pages_with_explicit_surface_constraints": sum(
            any(item["kind"] == "explicit_surface_constraint" for item in row["surface_constraints"])
            for row in page_rows
        ),
        "bug_d_status_counts": {
            "not_available_under_frozen_string_predicate_protocol": len(bug_rows)
        },
        "smoke_audit": smoke_audit,
        "evidence_boundary": {
            "D": "candidate-failing public App UI evidence only; generic task-oracle failure is C",
            "E": "sourced specification or implementation/dynamically inferred constraint with provenance; exact Bug trigger is leakage",
        },
    }

    (output / "d_page_inventory.json").write_text(
        json.dumps({"summary": summary, "cases": page_rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "de_bug_inventory.json").write_text(
        json.dumps({"summary": summary, "cases": bug_rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "local_evidence_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_csv(
        output / "d_page_inventory.csv",
        page_csv,
        [
            "case_id",
            "package",
            "formal_applicable",
            "d_status",
            "d_diagnostic_count",
            "d_candidate_messages",
            "surface_constraint_count",
            "e_status",
        ],
    )
    write_csv(
        output / "de_bug_inventory.csv",
        bug_csv,
        [
            "case_id",
            "app",
            "package",
            "category",
            "d_status",
            "e_status",
            "source_urls",
            "benchmark_trigger_description",
        ],
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
