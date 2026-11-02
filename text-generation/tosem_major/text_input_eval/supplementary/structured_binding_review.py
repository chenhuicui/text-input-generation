"""Freeze evidence-backed resolutions for configured manual PPTR binding reviews."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .structured_live_calibration import atomic_json, read_json
from .structured_snapshot_backup import sha256_file


def build(config_path: Path, registry_path: Path, cases_root: Path) -> dict[str, Any]:
    config = read_json(config_path)
    registry = read_json(registry_path)
    case_by_id = {case["case_id"]: case for case in registry["cases"]}
    rows = []
    for case_id in config.get("manual_binding_review_cases", []):
        case = case_by_id[case_id]
        if case["task"] != "valid_pptr":
            continue
        inspection_path = cases_root / case_id / "inspection.json"
        inspection = read_json(inspection_path)
        required = set(case["required_resource_ids"])
        bindings = inspection.get("binding", {})
        binding_issues = [
            issue for issue in inspection.get("issues", [])
            if isinstance(issue, dict) and "binding" in issue
        ]
        resolved = (
            inspection.get("binding_complete") is True
            and set(bindings) == required
            and not binding_issues
        )
        rows.append({
            "case_id": case_id,
            "status": "resolved" if resolved else "unresolved",
            "inspection_sha256": sha256_file(inspection_path),
            "required_resource_ids": sorted(required),
            "binding": bindings,
            "binding_issues": binding_issues,
            "resolution_basis": (
                "current live hierarchy uniquely resolves every frozen required ID"
                if resolved else "current live hierarchy does not uniquely resolve every required ID"
            ),
        })
    return {
        "schema_version": "e2-structure-v1-binding-review-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "registry_sha256": sha256_file(registry_path),
        "counts": {
            "cases": len(rows),
            "resolved": sum(row["status"] == "resolved" for row in rows),
            "unresolved": sum(row["status"] == "unresolved" for row in rows),
        },
        "cases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze live manual-binding review evidence")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--cases-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build(
        args.config.resolve(), args.registry.resolve(), args.cases_root.resolve(),
    )
    atomic_json(args.output.resolve(), report)
    print(json.dumps(report["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
