"""Freeze the 37 invalid-bug cases as executable string predicates.

Bug success is defined solely by the existing validator returning True for an
exact-key JSON object with string values.  Android, APK versions, snapshots,
and UI bindings are deliberately outside this oracle kind.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import text_input_eval.validators  # noqa: F401 - populate the validator registry
from jsonschema import Draft202012Validator
from text_input_eval.validators.registry import get_validator

from .structured_live_calibration import atomic_json
from .structured_preflight import REPO_ROOT, read_json, sha256_file, sha256_json


CONTROL_VALUES = (
    "", " ", "0", "1", "01", "a", "b", "a1", "auto", "auto0x", "め",
    ":", '"', "-x", "[", "x:/", ".1", "12.", "#", "1 000",
    "WRITE_SETTINGS", "not-an-email", "https://example.com", "61:00",
    "00:00", "a\nb", "x" * 40, "x" * 80, "x" * 100,
)


def _control_candidates(required_ids: list[str]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for common in CONTROL_VALUES:
        candidates.append({resource_id: common for resource_id in required_ids})
    for resource_id in required_ids:
        for value in CONTROL_VALUES:
            candidate = {item: "1" for item in required_ids}
            candidate[resource_id] = value
            candidates.append(candidate)
    unique: dict[str, dict[str, str]] = {}
    for candidate in candidates:
        unique[sha256_json(candidate)] = candidate
    return list(unique.values())


def control_pair(case: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    validator = get_validator(case["case_id"])
    if validator.task != "invalid_bug":
        raise ValueError(f"{case['case_id']} is not an invalid_bug validator")
    positives: list[dict[str, str]] = []
    negatives: list[dict[str, str]] = []
    for candidate in _control_candidates(case["required_resource_ids"]):
        result = validator.validate(candidate)
        if not isinstance(result, bool):
            raise TypeError(f"{case['case_id']} validator did not return bool")
        (positives if result else negatives).append(candidate)
    if not positives or not negatives:
        raise ValueError(
            f"{case['case_id']} predicate lacks a deterministic positive/negative control pair"
        )
    return positives[0], negatives[0]


def build_oracle(case: dict[str, Any]) -> dict[str, Any]:
    if case["task"] != "invalid_bug":
        raise ValueError("string-predicate Oracle accepts invalid_bug cases only")
    validator_path = REPO_ROOT / case["source_files"]["validator"]
    validator_sha256 = sha256_file(validator_path)
    if validator_sha256 != case["source_files"]["validator_sha256"]:
        raise ValueError(f"validator source drift for {case['case_id']}; rebuild the registry first")
    positive, negative = control_pair(case)
    return {
        "case_id": case["case_id"],
        "version": f"{case['case_id']}-string-predicate-v1",
        "task": "invalid_bug",
        "oracle_kind": "string_predicate",
        "predicate": {
            "validator_path": case["source_files"]["validator"],
            "validator_sha256": validator_sha256,
            "required_resource_ids": case["required_resource_ids"],
            "true_means": "bug_revealed",
            "control_pair_verified": True,
            "positive_control_sha256": sha256_json(positive),
            "negative_control_sha256": sha256_json(negative),
        },
    }


def promote_all(registry: dict[str, Any], manifest: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in manifest.get("cases", []) if row.get("task") != "invalid_bug"]
    rows.extend(build_oracle(case) for case in registry["cases"] if case["task"] == "invalid_bug")
    rows.sort(key=lambda row: row["case_id"])
    validator = Draft202012Validator(schema)
    errors = []
    for row in rows:
        errors.extend(
            f"{row['case_id']}:{'/'.join(map(str, error.absolute_path))}:{error.message}"
            for error in validator.iter_errors(row)
        )
    if errors:
        raise ValueError(f"formal Oracle schema validation failed: {errors[:8]}")
    return {**manifest, "cases": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze all invalid-bug string-predicate Oracles")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--oracle-manifest", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    args = parser.parse_args()
    registry = read_json(args.registry.resolve())
    manifest_path = args.oracle_manifest.resolve()
    result = promote_all(registry, read_json(manifest_path), read_json(args.schema.resolve()))
    atomic_json(manifest_path, result)
    bug_rows = [row for row in result["cases"] if row["task"] == "invalid_bug"]
    print(json.dumps({
        "bug_predicates_frozen": len(bug_rows),
        "total_oracles": len(result["cases"]),
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
