"""Result-blind migration selector for the no-replicate E2-abcdef-v2 analysis.

Legacy trajectories are never copied or mutated.  This module records which
immutable source trajectory is the earliest audit-valid observation for each
``(model, case_id, arm)`` cell and which cells require a fresh v2 trajectory.
Semantic PASS/FAIL never participates in replacement ordering.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .abcdef_contract import ARMS, PROTOCOL_ID, canonical_json


VALID_PROTOCOLS = {"E2-abcdef-v1", PROTOCOL_ID}
EXPECTED_BUDGETS = {"valid_pptr": 3, "invalid_bug": 30}
AUDIT_EXCLUSION_REASONS = {
    "infrastructure_error", "incomplete", "analysis_invalid",
    "unverified_clock", "missing_evidence", "missing_provider_envelope",
    "interrupted_worker", "unstarted", "wrong_budget", "malformed_result",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _ledger_rows(path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        calls = {
            row["id"]: {
                "id": row["id"], "kind": row["kind"],
                "dimensions": json.loads(row["dimensions"]),
                "payload": json.loads(row["payload"]), "state": row["state"],
                "result": json.loads(row["result"]) if row["result"] else None,
                "created_at": row["created_at"], "completed_at": row["completed_at"],
            }
            for row in db.execute(
                "SELECT id,kind,dimensions,payload,state,result,created_at,completed_at "
                "FROM external_calls"
            )
        }
        rows = [
            {
                "id": row["id"], "dimensions": json.loads(row["dimensions"]),
                "state": row["state"], "events": json.loads(row["events"]),
                "result": json.loads(row["result"]) if row["result"] else None,
                "updated_at": row["updated_at"],
            }
            for row in db.execute(
                "SELECT id,dimensions,state,events,result,updated_at FROM trajectories"
            )
        ]
    finally:
        db.close()
    return rows, calls


def _event_map(events: list[dict[str, Any]], name: str) -> dict[int, dict[str, Any]]:
    return {
        int(event["turn"]): event for event in events
        if event.get("event") == name and event.get("turn") is not None
    }


def _has_clock_receipt(events: list[dict[str, Any]]) -> bool:
    begin = next((event for event in events if event.get("event") == "attempt_begin"), {})
    receipt = begin.get("reset_receipt") or {}
    device_time = receipt.get("device_time")
    return isinstance(device_time, dict) and device_time.get("verified") is True


def _evidence_present(ledger_path: Path, trajectory_id: str, request_id: str) -> bool:
    request_root = ledger_path.parent / "evidence" / trajectory_id / request_id
    return request_root.is_dir() and any(request_root.iterdir())


def audit_trajectory(
    *, ledger_path: Path, row: dict[str, Any], calls: dict[str, dict[str, Any]],
    network_mode: str | None,
) -> dict[str, Any]:
    """Apply only frozen infrastructure/audit criteria; never inspect desirability."""
    dimensions = row["dimensions"]
    result = row.get("result") or {}
    events = row.get("events") or []
    reasons: list[str] = []
    task = dimensions.get("task")
    expected_budget = EXPECTED_BUDGETS.get(task)
    status = result.get("status")
    result_reason = str(result.get("reason") or "")
    records = result.get("records") if isinstance(result.get("records"), list) else []
    budget = result.get("budget")
    model_calls = result.get("model_calls")
    protocol_id = result.get("protocol_id") or dimensions.get("protocol_id")

    if row.get("state") == "running":
        reasons.append("interrupted_worker")
    if row.get("state") != "done" or not result:
        reasons.append("unstarted")
    if protocol_id not in VALID_PROTOCOLS:
        reasons.append("malformed_result")
    if status in {"incomplete", "analysis_invalid"}:
        reasons.append(status)
    if status not in {"completed_success", "completed_failure", "completed_unknown"}:
        reasons.append("malformed_result")
    if budget != expected_budget:
        reasons.append("wrong_budget")
    if not isinstance(model_calls, int) or model_calls < 1 or len(records) != model_calls:
        reasons.append("malformed_result")
    if status in {"completed_failure", "completed_unknown"} and not (
        model_calls == expected_budget and result_reason.startswith("budget_exhausted_")
    ):
        reasons.append("malformed_result")
    if status == "completed_success" and not any(record.get("outcome") == "PASS" for record in records):
        reasons.append("malformed_result")

    if network_mode != "offline_isolated" and not _has_clock_receipt(events):
        reasons.append("unverified_clock")

    model_events = _event_map(events, "model_request")
    execution_events = _event_map(events, "execution")
    for record in records:
        turn = record.get("turn")
        if not isinstance(turn, int) or not isinstance(record.get("raw_response"), str):
            reasons.append("malformed_result")
            continue
        model_event = model_events.get(turn)
        if not model_event:
            reasons.append("missing_provider_envelope")
            continue
        call = calls.get(str(model_event.get("physical_request_id")))
        call_result = (call or {}).get("result") or {}
        payload = (call or {}).get("payload") or {}
        if not (
            call and call.get("state") == "done"
            and call_result.get("run_status") == "ok"
            and isinstance(payload.get("message"), str) and payload.get("message")
            and isinstance(call_result.get("raw_response"), str)
            and call_result.get("provider_response") is not None
            and call_result.get("usage") is not None
        ):
            reasons.append("missing_provider_envelope")
        if record.get("candidate") is not None:
            execution = execution_events.get(turn) or {}
            if not (
                execution.get("verdict") in {"pass", "fail", "unknown"}
                and execution.get("observation_complete") is True
                and execution.get("evidence_hash")
                and _evidence_present(ledger_path, row["id"], str(record.get("request_id")))
            ):
                reasons.append("missing_evidence")

    unique_reasons = sorted(set(reasons))
    return {
        "valid": not unique_reasons,
        "exclusion_reasons": unique_reasons,
        "audit_criteria_are_result_blind": True,
        "semantic_status": status,
        "semantic_reason": result_reason or None,
        "budget": budget,
        "model_calls": model_calls,
        "clock_receipt_verified": _has_clock_receipt(events),
    }


def build_migration_plan(
    *, ledger_paths: Iterable[Path], registry_path: Path, clock_audit_path: Path,
    model: str,
) -> dict[str, Any]:
    registry = _read_json(registry_path)
    applicable = {
        row["case_id"]: row for row in registry["cases"]
        if row.get("formal_applicability", {}).get("in_scope") is True
    }
    clock_audit = _read_json(clock_audit_path)
    network_modes = {
        row["case_id"]: row.get("reset_network_mode") for row in clock_audit.get("cases", [])
    }
    candidates: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    fingerprints: dict[str, dict[str, Any]] = {}
    source_count = 0
    excluded_source_ledgers: list[dict[str, str]] = []
    for ledger_path in sorted({path.resolve() for path in ledger_paths}):
        claim = _read_json(ledger_path.parent / "claim.json") if (
            ledger_path.parent / "claim.json"
        ).is_file() else {}
        if isinstance(claim, dict) and (
            claim.get("resume_prohibited") is True
            or str(claim.get("status") or "").startswith("protocol_superseded")
        ):
            excluded_source_ledgers.append({
                "ledger": str(ledger_path),
                "reason": "protocol_superseded_source_prohibited",
            })
            continue
        rows, calls = _ledger_rows(ledger_path)
        source_count += 1
        for row in rows:
            dimensions = row["dimensions"]
            case_id = dimensions.get("case_id")
            arm = dimensions.get("arm")
            if dimensions.get("model") != model or case_id not in applicable or arm not in ARMS:
                continue
            fingerprint = _sha256({
                "id": row["id"], "dimensions": dimensions, "events": row["events"],
                "result": row["result"], "state": row["state"],
            })
            audit = audit_trajectory(
                ledger_path=ledger_path, row=row, calls=calls,
                network_mode=network_modes.get(case_id),
            )
            timestamp = (
                (row.get("result") or {}).get("started_at_utc")
                or row.get("updated_at") or "9999"
            )
            candidate = {
                "fingerprint_sha256": fingerprint,
                "trajectory_id": row["id"],
                "source_ledger": str(ledger_path),
                "model": dimensions["model"],
                "case_id": case_id,
                "arm": arm,
                "legacy_replicate": dimensions.get("replicate"),
                "chronology_utc": timestamp,
                **audit,
            }
            previous = fingerprints.get(fingerprint)
            if previous is None or (not previous["valid"] and candidate["valid"]):
                fingerprints[fingerprint] = candidate
    for candidate in fingerprints.values():
        candidates[(candidate["model"], candidate["case_id"], candidate["arm"])].append(candidate)

    selections: list[dict[str, Any]] = []
    fresh_cells: list[dict[str, str]] = []
    reason_counts: Counter[str] = Counter()
    for case_id, case in sorted(applicable.items()):
        for arm in ARMS:
            key = (model, case_id, arm)
            ordered = sorted(
                candidates.get(key, []),
                key=lambda item: (
                    item["chronology_utc"],
                    item["legacy_replicate"] if isinstance(item["legacy_replicate"], int) else 0,
                    item["trajectory_id"], item["source_ledger"],
                ),
            )
            chosen = next((item for item in ordered if item["valid"]), None)
            for item in ordered:
                reason_counts.update(item["exclusion_reasons"])
            if chosen is None:
                fresh_cells.append({"model": model, "case_id": case_id, "task": case["task"], "arm": arm})
            selections.append({
                "model": model, "case_id": case_id, "task": case["task"], "arm": arm,
                "selected": chosen,
                "candidate_count": len(ordered),
                "valid_candidate_count": sum(item["valid"] for item in ordered),
                "replacement_rule": "earliest_chronologically_valid_complete_audit_qualified",
            })

    fresh_case_ids = sorted({cell["case_id"] for cell in fresh_cells})
    return {
        "schema_version": "e2-abcdef-v2-result-blind-migration-plan-1",
        "protocol_id": PROTOCOL_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "experimental_unit_axes": ["model", "case_id", "arm"],
        "replicate_axis": False,
        "selection_is_result_blind": True,
        "semantic_failure_is_valid_when_full_budget_complete": True,
        "legacy_data_mutated": False,
        "source_ledger_count": source_count,
        "excluded_source_ledgers": excluded_source_ledgers,
        "target_trajectories": len(applicable) * len(ARMS),
        "selected_legacy_trajectories": sum(item["selected"] is not None for item in selections),
        "fresh_trajectory_cells": len(fresh_cells),
        "fresh_case_blocks": len(fresh_case_ids),
        "fresh_case_ids": fresh_case_ids,
        "fresh_cells": fresh_cells,
        "audit_exclusion_reason_counts": dict(sorted(reason_counts.items())),
        "selections": selections,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, action="append", required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--clock-audit", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_migration_plan(
        ledger_paths=args.ledger, registry_path=args.registry,
        clock_audit_path=args.clock_audit, model=args.model,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "protocol_id", "model", "target_trajectories", "selected_legacy_trajectories",
        "fresh_trajectory_cells", "fresh_case_blocks",
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
