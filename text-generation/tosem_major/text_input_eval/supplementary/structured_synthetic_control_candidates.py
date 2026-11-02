"""Complete missing PPTR candidate pairs by deterministic validator-guided search.

The generated values are candidates only.  They are never behavior Oracles:
every selected pair must still pass the live App positive/negative controls,
snapshot reset, within-attempt recovery, and formal-adapter smoke.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from text_input_eval.db.connection import get_conn

from .structured_android import canonical_json
from .structured_control_candidates import load_validator, parsed_candidate
from .structured_live_calibration import atomic_json, read_json
from .structured_snapshot_backup import sha256_file


GENERIC_VALUES = (
    "", " ", "a", "A", "0", "1", "8", "12", "123", "1234", "123456",
    "12345678", "abcdefgh", "Abc12345", "Test1234!", "test@example.com",
    "user@example.com", "https://example.com", "John", "Smith", "New York",
    "10001", "2025551234", "+12025551234", "12.34", "50", "true",
    "2026-09-21",
    # Deterministic domain probes for search/catalog validators.  These are
    # calibration candidates only; live App controls remain authoritative.
    "dog", "fantasy", "productivity", "United States", "apple", "English",
)


def field_values(field: str) -> list[str]:
    values = list(GENERIC_VALUES)
    key = field.lower()
    if "email" in key:
        values.extend(("a@b.co", "invalid", "user+tag@example.org"))
    if "password" in key or "secret" in key:
        values.extend(("Password123!", "12345678", "short"))
    if "phone" in key or "mobile" in key:
        values.extend(("5551234567", "(202) 555-1234"))
    if "zip" in key or "postal" in key:
        values.extend(("90210", "SW1A 1AA"))
    if "url" in key or "website" in key:
        values.extend(("https://openai.com", "example.com"))
    if "carrier" in key:
        values.extend(("UPS", "FedEx", "USPS"))
    if "tracking" in key:
        values.extend(("1Z999AA10123456784", "9400111899223856928499"))
    if "delivery" in key:
        values.extend(("Home delivery", "Package"))
    return values


def exact_candidates(rows: Iterable[dict[str, Any]], required: set[str]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        candidate = parsed_candidate(row.get("parsed_input_json"))
        if candidate is None or set(candidate) != required:
            continue
        signature = canonical_json(candidate)
        if signature not in seen:
            seen.add(signature)
            result.append(candidate)
    return result


def synthesized_source(
    case_id: str, candidate: dict[str, str], examined: int,
) -> dict[str, Any]:
    value = {
        "kind": "deterministic_validator_guided_recombination",
        "case_id": case_id,
        "algorithm": "observed_field_values_plus_frozen_generic_boundaries_v1",
        "examined_historical_exact_key_candidates": examined,
        "candidate": candidate,
        "meaning": "Candidate selection only; current App controls decide behavior.",
    }
    return {**value, "sha256": hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()}


def synthesize_pair(
    case_id: str,
    rows: list[dict[str, Any]],
    required: set[str],
    validate: Callable[[dict[str, str]], bool],
    positive: dict[str, Any] | None,
    negative: dict[str, Any] | None,
    random_budget: int = 30_000,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    historical = exact_candidates(rows, required)
    pools = {field: [] for field in sorted(required)}
    for candidate in historical:
        for field, value in candidate.items():
            if value not in pools[field]:
                pools[field].append(value)
    for field in pools:
        for value in field_values(field):
            if value not in pools[field]:
                pools[field].append(value)

    def consider(candidate: dict[str, str]) -> bool:
        nonlocal positive, negative
        try:
            accepted = bool(validate(candidate))
        except Exception:
            return False
        record = {
            "candidate": candidate,
            "source": synthesized_source(case_id, candidate, len(historical)),
        }
        if accepted and positive is None:
            positive = record
        if not accepted and negative is None and any(candidate.values()):
            negative = record
        return positive is not None and negative is not None

    seen: set[str] = set()

    def unique_consider(candidate: dict[str, str]) -> bool:
        signature = canonical_json(candidate)
        if signature in seen:
            return False
        seen.add(signature)
        return consider(candidate)

    for candidate in historical:
        if unique_consider(candidate):
            return positive, negative
    bases = [row["candidate"] for row in (positive, negative) if row is not None]
    if not bases:
        bases = [{field: "" for field in required}]
    for base in bases:
        if unique_consider(dict(base)):
            return positive, negative
        for field in sorted(required):
            for value in pools[field][:100]:
                candidate = dict(base)
                candidate[field] = value
                if unique_consider(candidate):
                    return positive, negative
    seed = int(hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    for _ in range(random_budget):
        candidate = {field: rng.choice(pools[field]) for field in sorted(required)}
        if unique_consider(candidate):
            break
    return positive, negative


def build(
    registry_path: Path, base_path: Path, cases_root: Path,
) -> dict[str, Any]:
    registry = read_json(registry_path)
    base = read_json(base_path)
    required_by_id = {
        case["case_id"]: set(case["required_resource_ids"])
        for case in registry["cases"]
        if case["task"] == "valid_pptr"
    }
    incomplete = [
        row for row in base["cases"]
        if row.get("task") == "valid_pptr" and row["status"] == "pair_incomplete"
    ]
    ids = [row["case_id"] for row in incomplete]
    placeholders = ",".join(["%s"] * len(ids))
    connection = get_conn()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, case_id, parsed_input_json FROM t_llm_text_generation "
                f"WHERE case_id IN ({placeholders}) AND parse_success = 1 "
                "AND parsed_input_json IS NOT NULL ORDER BY id DESC",
                ids,
            )
            database_rows = cursor.fetchall()
        connection.rollback()
    finally:
        connection.close()
    grouped: dict[str, list[dict[str, Any]]] = {case_id: [] for case_id in ids}
    for row in database_rows:
        if row.get("case_id") in grouped:
            grouped[row["case_id"]].append(row)

    results = []
    for row in base["cases"]:
        value = dict(row)
        if row.get("task") == "valid_pptr" and row["status"] == "pair_incomplete":
            case_id = row["case_id"]
            inspection = read_json(cases_root / case_id / "inspection.json")
            if not all(
                inspection.get(key) is True
                for key in ("binding_complete", "version_matches", "foreground_matches")
            ):
                raise ValueError(f"{case_id} binding readiness changed")
            positive, negative = synthesize_pair(
                case_id,
                grouped[case_id],
                required_by_id[case_id],
                load_validator(case_id).validate,
                row.get("positive_control"),
                row.get("negative_control"),
            )
            value["positive_control"] = positive
            value["negative_control"] = negative
            value["status"] = (
                "pair_available" if positive is not None and negative is not None
                else "pair_incomplete"
            )
            value["candidate_completion"] = "deterministic_validator_guided_search"
        results.append(value)
    counts = {
        "binding_ready": len(results),
        "pair_available": sum(row["status"] == "pair_available" for row in results),
        "pair_incomplete": sum(row["status"] == "pair_incomplete" for row in results),
        "newly_completed": sum(
            before["status"] == "pair_incomplete" and after["status"] == "pair_available"
            for before, after in zip(base["cases"], results)
        ),
    }
    return {
        "schema_version": "e2-structure-v1-control-candidates-2",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "registry_sha256": sha256_file(registry_path),
        "base_candidates_sha256": sha256_file(base_path),
        "source": {
            "table": "t_llm_text_generation",
            "access": "read_only",
            "meaning": "Validator-guided candidate selection only; not App behavior evidence.",
        },
        "counts": counts,
        "cases": results,
    }


def build_offline(registry_path: Path) -> dict[str, Any]:
    """Build candidate pairs without consulting historical generation rows."""
    from .structured_manual_control_candidates import PAIRS, source as reviewed_source

    registry = read_json(registry_path)
    results = []
    for case in registry["cases"]:
        if case.get("task") != "valid_pptr":
            continue
        case_id = case["case_id"]
        reviewed = PAIRS.get(case_id)
        if reviewed is not None:
            required = set(case["required_resource_ids"])
            if set(reviewed["positive"]) != required or set(reviewed["negative"]) != required:
                raise ValueError(f"{case_id} reviewed candidate keys differ from the frozen case")
            positive = {
                "candidate": reviewed["positive"],
                "source": reviewed_source(case_id, "positive", reviewed["positive"]),
            }
            negative = {
                "candidate": reviewed["negative"],
                "source": reviewed_source(case_id, "negative", reviewed["negative"]),
            }
            completion = "human_reviewed_local_calibration_probe"
        else:
            positive, negative = synthesize_pair(
                case_id,
                [],
                set(case["required_resource_ids"]),
                load_validator(case_id).validate,
                None,
                None,
            )
            completion = "offline_deterministic_validator_guided_search"
        results.append({
            "case_id": case_id,
            "task": case["task"],
            "status": (
                "pair_available"
                if positive is not None and negative is not None
                else "pair_incomplete"
            ),
            "examined_exact_key_candidates": 0,
            "positive_control": positive,
            "negative_control": negative,
            "candidate_completion": completion,
        })
    counts = {
        "binding_ready": len(results),
        "pair_available": sum(row["status"] == "pair_available" for row in results),
        "pair_incomplete": sum(row["status"] == "pair_incomplete" for row in results),
        "newly_completed": sum(row["status"] == "pair_available" for row in results),
    }
    return {
        "schema_version": "e2-structure-v1-control-candidates-offline-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "registry_sha256": sha256_file(registry_path),
        "source": {
            "kind": "offline_deterministic_validator_guided_search",
            "database_access": False,
            "meaning": "Candidate selection only; current App controls decide behavior.",
        },
        "counts": counts,
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministically complete PPTR candidate pairs")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--base-candidates", type=Path)
    parser.add_argument("--cases-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--offline-only",
        action="store_true",
        help="derive all Page candidate pairs locally without querying MySQL",
    )
    args = parser.parse_args()
    if args.offline_only:
        report = build_offline(args.registry.resolve())
    else:
        if args.base_candidates is None or args.cases_root is None:
            parser.error("--base-candidates and --cases-root are required without --offline-only")
        report = build(
            args.registry.resolve(), args.base_candidates.resolve(), args.cases_root.resolve(),
        )
    atomic_json(args.output.resolve(), report)
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
