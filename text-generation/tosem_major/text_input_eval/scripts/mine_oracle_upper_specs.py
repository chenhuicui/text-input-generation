#!/usr/bin/env python3
"""Extract the private benchmark predicates as an explicit leakage upper bound.

These predicates are complete specifications of benchmark acceptance, not
ordinary model context.  The output exists so they cannot be accidentally
mixed with clean E evidence.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def functions_by_name(path: Path) -> dict[str, str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    result: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            segment = ast.get_source_segment(source, node)
            if segment:
                result[node.name] = segment
    return result


def extract_validator(path: Path) -> tuple[str, list[str]]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for member in node.body:
            if isinstance(member, ast.FunctionDef) and member.name == "validate":
                segment = ast.get_source_segment(source, member)
                calls = sorted(
                    {
                        call.func.id
                        for call in ast.walk(member)
                        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    }
                )
                if segment:
                    return segment, calls
    raise RuntimeError(f"validate method not found: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    common_path = repo / "tosem_major/text_input_eval/validators/common.py"
    common_functions = functions_by_name(common_path)
    rows: list[dict[str, Any]] = []
    csv_rows: list[dict[str, str]] = []
    for case in registry["cases"]:
        relative = case["source_files"]["validator"]
        path = repo / relative
        source_sha = sha256_file(path)
        expected_sha = case["source_files"]["validator_sha256"]
        if source_sha != expected_sha:
            raise RuntimeError(f"validator changed after freeze: {case['case_id']}")
        validate_source, calls = extract_validator(path)
        helpers = {name: common_functions[name] for name in calls if name in common_functions}
        record = {
            "case_id": case["case_id"],
            "task": case["task"],
            "validator_path": relative,
            "validator_sha256": source_sha,
            "required_resource_ids": case["required_resource_ids"],
            "validate_method": validate_source,
            "common_helper_sources": helpers,
            "e_status": "available_private_oracle_upper_bound_only",
            "leakage": "complete benchmark-answer leakage",
            "allowed_use": "labelled upper-bound/sensitivity analysis only; never ordinary E",
        }
        rows.append(record)
        csv_rows.append(
            {
                "case_id": case["case_id"],
                "task": case["task"],
                "validator_path": relative,
                "validator_sha256": source_sha,
                "required_resource_ids": " | ".join(case["required_resource_ids"]),
                "validate_method": validate_source,
                "helper_names": " | ".join(sorted(helpers)),
                "e_status": record["e_status"],
                "leakage": record["leakage"],
            }
        )
    summary = {
        "schema_version": "private-oracle-upper-spec-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_count": len(rows),
        "page_count": sum(row["task"] == "valid_pptr" for row in rows),
        "bug_count": sum(row["task"] == "invalid_bug" for row in rows),
        "hash_verified": len(rows),
        "ordinary_e_eligible": 0,
        "upper_bound_eligible": len(rows),
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps({"summary": summary, "cases": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
