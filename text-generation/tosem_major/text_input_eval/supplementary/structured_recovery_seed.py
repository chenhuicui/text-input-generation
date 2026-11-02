"""Build a non-formal recovery seed from the verified 152-case backup.

This command exists for disaster recovery when generated calibration material is
lost but the source tree, snapshot backup, and external mirror remain intact.
It refreshes source and validator hashes while preserving the exact snapshot
mapping captured by the backup.  The result is deliberately named and marked as
a recovery seed; it is input to live calibration only and is never a formal
preflight registry.
"""
from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .structured_live_calibration import atomic_json, ensure_backup, read_json
from .structured_preflight import (
    REPO_ROOT,
    _case_rows,
    resolve_config_path,
    sha256_file,
)


def _exclusions(path: Path, expected: set[str]) -> dict[str, dict[str, Any]]:
    document = read_json(path)
    rows = document.get("cases")
    if not isinstance(rows, list):
        raise ValueError("formal exclusion manifest must contain a cases list")
    index = {
        row["case_id"]: row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("case_id"), str)
    }
    if set(index) != expected or len(index) != len(rows):
        raise ValueError(
            f"formal exclusion IDs differ from the frozen applicability set: "
            f"expected={sorted(expected)}, observed={sorted(index)}"
        )
    for case_id, row in index.items():
        if (
            row.get("task") != "valid_pptr"
            or row.get("disposition") != "not_applicable"
            or row.get("replacement_oracle") is not False
        ):
            raise ValueError(f"formal exclusion is incomplete: {case_id}")
    return index


def _refresh_case(
    saved: dict[str, Any],
    current: dict[str, Any],
    exclusion: dict[str, Any] | None,
) -> dict[str, Any]:
    if saved.get("case_id") != current.get("case_id"):
        raise ValueError("backup/current case identity differs")
    row = copy.deepcopy(saved)
    for key in (
        "case_id", "task", "ordinal_in_source", "source_target",
        "required_resource_ids", "source_files", "prompt_record_sha256",
        "extracted_prompt_sha256", "extracted_prompt_bytes",
    ):
        row[key] = copy.deepcopy(current[key])

    old_page_config = saved.get("source_files", {}).get("page_config")
    if isinstance(old_page_config, str):
        page_config = (REPO_ROOT / old_page_config).resolve()
        if page_config.is_file():
            row["source_files"]["page_config"] = old_page_config
            row["source_files"]["page_config_sha256"] = sha256_file(page_config)

    source = row.get("app_or_page_source")
    if isinstance(source, dict) and row["task"] == "invalid_bug":
        source["validator_path"] = row["source_files"]["validator"]
        source["validator_sha256"] = row["source_files"]["validator_sha256"]

    in_scope = exclusion is None
    row["formal_applicability"] = {
        "in_scope": in_scope,
        "disposition": "included" if in_scope else exclusion["disposition"],
        "reason_code": None if in_scope else exclusion.get("reason_code"),
        "rationale": None if in_scope else exclusion.get("rationale"),
        "evidence": [] if in_scope else exclusion.get("evidence", []),
        "replacement_oracle": None if in_scope else exclusion["replacement_oracle"],
    }
    row["formal_oracle"] = {
        "available": False,
        "version": None,
        "oracle_kind": None,
        "artifact_sha256": None,
        "evidence_chain_verified": False,
        "binding_keys_exact": False,
        "calibration": {},
    }
    row["protocol_readiness"] = {
        "target_page_ready": row.get("mirror", {}).get("status") == "ready",
        "independent_attempt_reset_ready": row["task"] == "invalid_bug",
        "within_attempt_recovery_ready": row["task"] == "invalid_bug",
        "behavior_oracle_ready": False,
        "binding_review_required": False,
        "binding_review_resolution_verified": False,
        "formal_run_in_scope": in_scope,
        "formal_run_ready": False,
        "blockers": [] if not in_scope else ["recovery_seed_requires_oracle_requalification"],
    }
    return row


def build_seed(
    config_path: Path,
    backup_registry_path: Path,
    backup_manifest_path: Path,
) -> dict[str, Any]:
    config = read_json(config_path)
    cache_root = resolve_config_path(config_path, config["sources"]["cache_root"])
    exclusion_path = resolve_config_path(
        config_path, config.get("runtime", {}).get("formal_exclusions")
    )
    if cache_root is None or not cache_root.is_dir():
        raise FileNotFoundError("cache_root is unavailable")
    if exclusion_path is None or not exclusion_path.is_file():
        raise FileNotFoundError("formal exclusion manifest is unavailable")

    saved_document = read_json(backup_registry_path)
    saved = {row["case_id"]: row for row in saved_document.get("cases", [])}
    current_rows = _case_rows(cache_root)
    current = {row["case_id"]: row for row in current_rows}
    expected = {f"P{index:03d}" for index in range(1, 116)} | {
        f"B{index:03d}" for index in range(1, 38)
    }
    if set(saved) != expected or set(current) != expected:
        raise ValueError("backup and current source must both contain the frozen 152 cases")
    ensure_backup(backup_manifest_path, backup_registry_path)
    exclusions = _exclusions(
        exclusion_path, set(config["applicability"]["not_applicable_case_ids"])
    )

    rows = [
        _refresh_case(saved[row["case_id"]], row, exclusions.get(row["case_id"]))
        for row in current_rows
    ]
    missing_mirrors = [
        row["case_id"] for row in rows
        if row.get("mirror", {}).get("status") != "ready"
        or not Path(row.get("mirror", {}).get("manifest_path", "")).is_file()
    ]
    manifest_drift = [
        row["case_id"] for row in rows
        if row["case_id"] not in missing_mirrors
        and sha256_file(Path(row["mirror"]["manifest_path"]))
        != row["mirror"].get("manifest_sha256")
    ]
    if missing_mirrors or manifest_drift:
        raise ValueError(
            f"external mirror is not the verified backup contract: "
            f"missing={missing_mirrors}, drift={manifest_drift}"
        )
    return {
        "schema_version": "e2-structure-v1-recovery-seed-1",
        "protocol_id": config["protocol_id"],
        "recovery_seed_only": True,
        "formal_runner_input": False,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "provenance": {
            "config": str(config_path),
            "config_sha256": sha256_file(config_path),
            "backup_registry": str(backup_registry_path),
            "backup_registry_sha256": sha256_file(backup_registry_path),
            "backup_manifest": str(backup_manifest_path),
            "backup_manifest_sha256": sha256_file(backup_manifest_path),
            "formal_exclusions": str(exclusion_path),
            "formal_exclusions_sha256": sha256_file(exclusion_path),
        },
        "counts": {
            "cases": len(rows),
            "valid_pptr": sum(row["task"] == "valid_pptr" for row in rows),
            "invalid_bug": sum(row["task"] == "invalid_bug" for row in rows),
            "formal_in_scope": sum(
                row["formal_applicability"]["in_scope"] for row in rows
            ),
            "not_applicable": sum(
                not row["formal_applicability"]["in_scope"] for row in rows
            ),
        },
        "cases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a non-formal 152-case recovery seed for live recalibration"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--backup-registry", type=Path, required=True)
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_seed(
        args.config.resolve(),
        args.backup_registry.resolve(),
        args.backup_manifest.resolve(),
    )
    output = args.output.resolve()
    atomic_json(output, result)
    print(json.dumps({
        "output": str(output),
        "sha256": sha256_file(output),
        "counts": result["counts"],
        "recovery_seed_only": result["recovery_seed_only"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
