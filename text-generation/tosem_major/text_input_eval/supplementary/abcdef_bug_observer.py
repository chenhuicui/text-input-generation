"""Evidence-only observer for Bug App feedback.

The frozen string predicate remains the Bug verdict Oracle.  This command only
checks that the App capture exists and deliberately returns UNKNOWN, so App UI
evidence cannot override or leak the private trigger condition.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def observe(payload: dict[str, Any]) -> dict[str, Any]:
    request = payload.get("request", {})
    evidence_dir = Path(str(request.get("evidence_dir", ""))).resolve()
    evidence = payload.get("captured_evidence")
    if not evidence_dir.is_dir() or not isinstance(evidence, list) or not evidence:
        raise ValueError("Bug observer requires captured App evidence")
    kinds = set()
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("Bug observer received malformed evidence")
        path = (evidence_dir / item["path"]).resolve()
        if not path.is_relative_to(evidence_dir) or not path.is_file() or path.stat().st_size == 0:
            raise ValueError("Bug observer evidence is missing or escapes the request directory")
        kinds.add(item.get("kind"))
    required = {"xml", "screenshot", "feedback_composite", "ui_observations"}
    if not required.issubset(kinds):
        raise ValueError(f"Bug observer lacks required evidence kinds: {sorted(required - kinds)}")
    return {
        "verdict": "unknown",
        "observation_complete": False,
        "diagnostics": [],
        "observer_role": "feedback_evidence_only",
    }


def main() -> None:
    try:
        sys.stdout.write(json.dumps(observe(json.load(sys.stdin)), ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        sys.stderr.write(json.dumps({"error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
