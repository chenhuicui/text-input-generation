"""Extract auditable historical candidate pairs for live App control calibration.

The database is read only.  Offline validator outcomes are used solely to pick
candidate inputs; this artifact never promotes them to App behavior Oracles.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from text_input_eval.db.connection import get_conn

from .structured_android import canonical_json
from .structured_live_calibration import atomic_json, read_json
from .structured_snapshot_backup import sha256_file


def parsed_candidate(value: Any) -> dict[str, str] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value.items()
    ):
        return None
    return value


def source_record(row: dict[str, Any], candidate: dict[str, str]) -> dict[str, Any]:
    metadata = {
        "db_id": row.get("id"),
        "experiment": row.get("experiment"),
        "model": row.get("model"),
        "prompt_setting": row.get("prompt_setting"),
        "prompt_variant": row.get("prompt_variant"),
        "feedback_mode": row.get("feedback_mode"),
        "attempt_id": row.get("attempt_id"),
        "invalid_index": row.get("invalid_index"),
        "candidate": candidate,
    }
    return {**metadata, "sha256": hashlib.sha256(
        canonical_json(metadata).encode("utf-8")
    ).hexdigest()}


def select_pair(
    rows: list[dict[str, Any]], required: set[str], validate: Callable[[dict[str, str]], bool],
) -> dict[str, Any]:
    positive = None
    negative = None
    examined = 0
    for row in rows:
        candidate = parsed_candidate(row.get("parsed_input_json"))
        if candidate is None or set(candidate) != required:
            continue
        examined += 1
        try:
            accepted = bool(validate(candidate))
        except Exception:
            continue
        record = {"candidate": candidate, "source": source_record(row, candidate)}
        if accepted and positive is None:
            positive = record
        elif not accepted and negative is None and any(candidate.values()):
            negative = record
        if positive is not None and negative is not None:
            break
    return {
        "status": "pair_available" if positive and negative else "pair_incomplete",
        "examined_exact_key_candidates": examined,
        "positive_control": positive,
        "negative_control": negative,
    }


def load_validator(case_id: str) -> Any:
    import text_input_eval.validators.bug  # noqa: F401
    import text_input_eval.validators.pptr  # noqa: F401
    from text_input_eval.validators.registry import get_validator

    return get_validator(case_id)


def build(registry_path: Path, cases_root: Path) -> dict[str, Any]:
    registry = read_json(registry_path)
    ready = []
    case_by_id = {case["case_id"]: case for case in registry["cases"]}
    for case_id, case in case_by_id.items():
        inspection_path = cases_root / case_id / "inspection.json"
        if not inspection_path.is_file():
            continue
        inspection = read_json(inspection_path)
        if (
            inspection.get("binding_complete") is True
            and inspection.get("version_matches") is True
            and inspection.get("foreground_matches") is True
        ):
            ready.append(case)
    ids = [case["case_id"] for case in ready]
    placeholders = ",".join(["%s"] * len(ids))
    sql = f"""
        SELECT id, experiment, case_id, model, prompt_setting, prompt_variant,
               feedback_mode, attempt_id, invalid_index, parsed_input_json
        FROM t_llm_text_generation
        WHERE case_id IN ({placeholders})
          AND parse_success = 1
          AND parsed_input_json IS NOT NULL
        ORDER BY id DESC
    """
    connection = get_conn()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, ids)
            db_rows = cursor.fetchall()
        connection.rollback()
    finally:
        connection.close()
    grouped: dict[str, list[dict[str, Any]]] = {case_id: [] for case_id in ids}
    for row in db_rows:
        if row.get("case_id") in grouped:
            grouped[row["case_id"]].append(row)
    results = []
    for case in ready:
        case_id = case["case_id"]
        validator = load_validator(case_id)
        pair = select_pair(
            grouped[case_id], set(case["required_resource_ids"]), validator.validate,
        )
        results.append({"case_id": case_id, "task": case["task"], **pair})
    counts = {
        "binding_ready": len(ready),
        "pair_available": sum(row["status"] == "pair_available" for row in results),
        "pair_incomplete": sum(row["status"] != "pair_available" for row in results),
    }
    return {
        "schema_version": "e2-structure-v1-control-candidates-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "registry_sha256": sha256_file(registry_path),
        "source": {
            "table": "t_llm_text_generation",
            "access": "read_only",
            "meaning": "offline candidate selection only; not App behavior evidence",
        },
        "counts": counts,
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract historical candidate control pairs")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--cases-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.registry.resolve(), args.cases_root.resolve())
    atomic_json(args.output.resolve(), report)
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
