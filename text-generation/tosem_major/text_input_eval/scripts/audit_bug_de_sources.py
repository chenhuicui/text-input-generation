#!/usr/bin/env python3
"""Adjudicate D/E evidence for every frozen Bug case.

Bug reports and their fixes are intentionally labelled benchmark-linked.  They
are useful provenance and an upper-bound specification condition, but they are
not silently promoted to a leakage-free E condition.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


HEADING_RE = re.compile(
    r"(?im)^\s{0,3}(?:#{1,6}\s*|\*\*)?"
    r"(?P<label>expected(?: behavior| result)?|desired behavior|requirements?|acceptance criteria)"
    r"(?:\*\*)?\s*:?[ \t]*(?P<inline>.*)$"
)
CONSTRAINT_RE = re.compile(
    r"(?:invalid|required|must|at least|at most|minimum|maximum|maxLength|maxLines|"
    r"singleLine|inputType|digits|empty|blank|whitespace|valid email|between\s+\d|"
    r"setError|Patterns\.EMAIL_ADDRESS|NumberFormatException|parse|trim\()",
    re.IGNORECASE,
)


def extract_expected_sections(text: str | None) -> list[str]:
    if not text:
        return []
    matches = list(HEADING_RE.finditer(text))
    sections: list[str] = []
    for match in matches:
        start = match.start("inline")
        next_heading = re.search(r"(?m)^\s{0,3}#{1,6}\s+", text[match.end() :])
        end = match.end() + next_heading.start() if next_heading else len(text)
        value = " ".join(text[start:end].strip().split())
        if value:
            sections.append(value[:1000])
    return sections


def pull_records(source: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if source.get("pull"):
        records.append(
            {
                "url": source["pull"].get("html_url") or source.get("url"),
                "pull": source["pull"],
                "files": source.get("files") or [],
                "issue_comments": source.get("issue_comments") or [],
                "review_comments": source.get("review_comments") or [],
            }
        )
    records.extend(source.get("linked_pull_requests") or [])
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        unique[str(record.get("url"))] = record
    return list(unique.values())


def patch_constraint_lines(pulls: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for pull in pulls:
        for changed in pull.get("files") or []:
            patch = changed.get("patch") or ""
            for line in patch.splitlines():
                if not line.startswith(("+", "-")) or line.startswith(("+++", "---")):
                    continue
                if not CONSTRAINT_RE.search(line):
                    continue
                findings.append(
                    {
                        "pull_url": str(pull.get("url")),
                        "filename": str(changed.get("filename")),
                        "change": line[:1000],
                    }
                )
    unique = {
        (item["pull_url"], item["filename"], item["change"]): item for item in findings
    }
    return list(unique.values())


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "case_id",
        "app",
        "category",
        "d_status",
        "github_retrieval_status",
        "expected_behavior_section_count",
        "pull_count",
        "merged_pull_count",
        "patch_constraint_count",
        "test_file_count",
        "e_clean_status",
        "e_upper_bound_status",
        "apk_version_alignment",
        "source_urls",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--github", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    args = parser.parse_args()

    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    github = json.loads(args.github.read_text(encoding="utf-8"))
    github_by_case = {case["case_id"]: case for case in github["cases"]}
    cases: list[dict[str, Any]] = []
    csv_rows: list[dict[str, Any]] = []
    for registry_case in registry["cases"]:
        if registry_case["task"] != "invalid_bug":
            continue
        case_id = registry_case["case_id"]
        metadata = registry_case.get("app_or_page_source") or {}
        github_case = github_by_case[case_id]
        sources = github_case.get("sources") or []
        retrieved = [source for source in sources if source.get("status") == "retrieved"]
        errors = [source for source in sources if source.get("status") != "retrieved"]
        expected_sections: list[dict[str, str]] = []
        pulls: list[dict[str, Any]] = []
        for source in retrieved:
            issue = source.get("issue") or {}
            for section in extract_expected_sections(issue.get("body")):
                expected_sections.append({"source_url": source["url"], "text": section})
            for comment in source.get("comments") or []:
                for section in extract_expected_sections(comment.get("body")):
                    expected_sections.append(
                        {"source_url": comment.get("html_url") or source["url"], "text": section}
                    )
            pulls.extend(pull_records(source))
        unique_pulls = {str(pull.get("url")): pull for pull in pulls}
        pulls = list(unique_pulls.values())
        patch_constraints = patch_constraint_lines(pulls)
        test_files = sorted(
            {
                str(changed.get("filename"))
                for pull in pulls
                for changed in pull.get("files") or []
                if re.search(r"(?:^|/)(?:test|tests|androidTest)(?:/|$)|(?:Test|Tests)\.", str(changed.get("filename")), re.IGNORECASE)
            }
        )
        retrieval_status = (
            "complete"
            if retrieved and not errors
            else "partial_one_source_unavailable"
            if retrieved
            else "unavailable"
        )
        upper_evidence = bool(retrieved or metadata.get("expected_behavior_evidence"))
        row = {
            "case_id": case_id,
            "task": "invalid_bug",
            "app": github_case.get("app"),
            "package": registry_case.get("mirror", {}).get("package"),
            "category": github_case.get("category"),
            "benchmark_trigger_description": github_case.get("description"),
            "d_status": "not_available_under_frozen_string_predicate_protocol",
            "d_reason": "The Bug runner evaluates strings and emits no candidate-bound App UI state; predicate_false is C.",
            "github_retrieval_status": retrieval_status,
            "retrieved_urls": [source["url"] for source in retrieved],
            "unavailable_urls": [source["url"] for source in errors],
            "expected_behavior_sections": expected_sections,
            "pulls": [
                {
                    "url": pull.get("url"),
                    "merged_at": (pull.get("pull") or {}).get("merged_at"),
                    "base_sha": (pull.get("pull") or {}).get("base_sha"),
                    "head_sha": (pull.get("pull") or {}).get("head_sha"),
                    "changed_files": len(pull.get("files") or []),
                }
                for pull in pulls
            ],
            "patch_constraint_lines": patch_constraints,
            "test_files": test_files,
            "source_reported_version": metadata.get("source_reported_version"),
            "experimental_apk_version_code": registry_case.get("mirror", {}).get("version_code"),
            "apk_version_alignment": metadata.get("apk_version_alignment") or "unknown",
            "existing_expected_behavior_evidence": metadata.get("expected_behavior_evidence"),
            "existing_implementation_cause_evidence": metadata.get("implementation_cause_evidence"),
            "e_clean_status": "not_established_from_bug_linked_issue_or_fix_sources",
            "e_clean_reason": (
                "The report/fix is part of the benchmark provenance and usually reveals the held-out trigger; "
                "it requires an independent source or a pre-trigger packaged constraint for clean E."
            ),
            "e_upper_bound_status": "available_benchmark_linked_spec" if upper_evidence else "unavailable",
            "e_upper_bound_inputs": {
                "expected_behavior_sections": len(expected_sections),
                "implementation_constraint_lines": len(patch_constraints),
                "curated_expected_behavior_summary": metadata.get("expected_behavior_evidence"),
            },
            "leakage_policy": "never include the exact trigger in ordinary E; benchmark-linked evidence is upper-bound only",
        }
        cases.append(row)
        csv_rows.append(
            {
                "case_id": case_id,
                "app": row["app"],
                "category": row["category"],
                "d_status": row["d_status"],
                "github_retrieval_status": retrieval_status,
                "expected_behavior_section_count": len(expected_sections),
                "pull_count": len(pulls),
                "merged_pull_count": sum(bool((pull.get("pull") or {}).get("merged_at")) for pull in pulls),
                "patch_constraint_count": len(patch_constraints),
                "test_file_count": len(test_files),
                "e_clean_status": row["e_clean_status"],
                "e_upper_bound_status": row["e_upper_bound_status"],
                "apk_version_alignment": row["apk_version_alignment"],
                "source_urls": " | ".join(github_case.get("urls") or []),
            }
        )
    summary = {
        "schema_version": "bug-de-source-adjudication-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_count": len(cases),
        "github_retrieval_counts": dict(sorted(Counter(row["github_retrieval_status"] for row in cases).items())),
        "cases_with_expected_behavior_section": sum(bool(row["expected_behavior_sections"]) for row in cases),
        "cases_with_pull": sum(bool(row["pulls"]) for row in cases),
        "cases_with_merged_pull": sum(any(pull["merged_at"] for pull in row["pulls"]) for row in cases),
        "cases_with_patch_constraint": sum(bool(row["patch_constraint_lines"]) for row in cases),
        "cases_with_test_file": sum(bool(row["test_files"]) for row in cases),
        "clean_e_ready_from_bug_linked_sources": 0,
        "upper_bound_e_available": sum(row["e_upper_bound_status"].startswith("available") for row in cases),
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps({"summary": summary, "cases": cases}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(args.output_csv, csv_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
