"""Frozen contracts shared by the A-F feedback experiment.

The experiment deliberately separates task adjudication from information that
is exposed to the next subject-model turn.  Private validators, behavior
assertions, reset receipts, and infrastructure diagnostics may be persisted for
audit, but only the fields admitted here may enter an A-F prompt.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


PROTOCOL_ID = "E2-abcdef-v2"
LEGACY_PROTOCOL_IDS = {"E2-abcdef-v1"}
ARMS = ("A", "B", "C", "D", "E", "F")
OUTCOMES = {"PASS", "FAIL", "UNKNOWN"}
INTERNAL_RUN_STATUSES = {
    "ok",
    "invalid_output",
    "infrastructure_error",
    "analysis_invalid",
}


def canonical_json(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "".join(
        f"\\u{ord(char):04x}" if 0xD800 <= ord(char) <= 0xDFFF else char
        for char in serialized
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def attempt_outcome(record: dict[str, Any]) -> str:
    """Return the only three-valued result visible in condition C and above."""
    value = record.get("outcome")
    if value not in OUTCOMES:
        raise ValueError("feedback record requires PASS, FAIL, or UNKNOWN outcome")
    return str(value)


def validate_internal_adjudication(record: dict[str, Any]) -> None:
    """Fail closed on conflating infrastructure state with semantic failure."""
    outcome = attempt_outcome(record)
    run_status = record.get("run_status")
    if run_status not in INTERNAL_RUN_STATUSES:
        raise ValueError("feedback record has an unsupported internal run_status")
    if run_status == "infrastructure_error":
        raise ValueError("infrastructure errors must be replayed, not fed back")
    if run_status == "analysis_invalid":
        raise ValueError("invalid analyzer output must not enter subject feedback")
    if run_status == "invalid_output" and outcome != "FAIL":
        raise ValueError("an invalid model output is a definite attempt failure")
    if run_status == "ok" and outcome not in OUTCOMES:
        raise ValueError("completed adjudication requires a three-valued outcome")


def public_candidate(record: dict[str, Any], *, limit: int) -> dict[str, Any]:
    """Create a candidate-only history row without outcome or Oracle details."""
    if limit < 1:
        raise ValueError("candidate limit must be positive")
    candidate = record.get("candidate")
    if candidate is not None and not isinstance(candidate, dict):
        raise ValueError("candidate must be an object or null")
    if candidate is None:
        raw = str(record.get("raw_response") or "")
    else:
        raw = canonical_json(candidate)
    truncated = len(raw) > limit
    visible = raw if not truncated else raw[:limit] + "\n...[truncated]"
    # Hashes and encoding metadata remain in the audit ledger. B-F receive
    # only the candidate history promised by the experimental factor.
    return {"turn": int(record["turn"]), "candidate": visible}
