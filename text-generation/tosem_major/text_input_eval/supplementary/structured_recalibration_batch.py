"""Resume action/outcome recalibration, controls, and promotion for one AVD."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from .structured_live_calibration import (
    atomic_json,
    current_avd,
    ensure_backup,
    infer_avd,
    read_json,
)
from .structured_live_controls import run_controls
from .structured_live_recalibration import recalibrate
from .structured_oracle_promotion import (
    build_oracle,
    commit,
    smoke,
    validate_oracle,
)
from .structured_snapshot_backup import sha256_file


PLAN_PREFERENCE = (
    "control_plan_reviewed.json",
    "control_plan_recovered.json",
    "control_plan_strong_negative.json",
    "control_plan_validator_fixed.json",
    "control_plan_manual.json",
    "control_plan_augmented.json",
    "control_plan_expanded.json",
    "control_plan.json",
    "control_plan_offline.json",
)


def implementation_sha256(*paths: Path) -> str:
    """Hash every implementation file that can change cached live evidence."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def select_plan(case_root: Path) -> Path | None:
    return next((case_root / name for name in PLAN_PREFERENCE if (case_root / name).is_file()), None)


def error_result(case_id: str, phase: str, exc: Exception) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "status": f"{phase}_error",
        "error_type": type(exc).__name__,
        "error": str(exc) or repr(exc),
    }


def promote_with_lock(
    case: dict[str, Any],
    inspection: dict[str, Any],
    plan: dict[str, Any],
    control: dict[str, Any],
    avd: str,
    schema: Path,
    config: dict[str, Any],
    oracle_manifest: Path,
    case_root: Path,
) -> dict[str, Any]:
    mirror_manifest = Path(case["mirror"]["manifest_path"])
    oracle = build_oracle(case, inspection, plan, control, mirror_manifest, avd)
    validate_oracle(oracle, schema)
    candidate_path = case_root / "oracle_candidate_recalibrated.json"
    atomic_json(candidate_path, oracle)
    smoke_output = case_root / "formal_adapter_smoke_recalibrated"
    smoke_result = smoke(
        case,
        oracle,
        plan["negative_control"],
        config["runtime"]["environment_command"],
        smoke_output,
    )
    smoke_path = smoke_output / "result.json"
    oracle["calibration"]["formal_adapter_smoke_sha256"] = sha256_file(smoke_path)
    validate_oracle(oracle, schema)
    lock_path = oracle_manifest.with_suffix(oracle_manifest.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    with lock_path.open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        commit(oracle_manifest, oracle, Path(control["_path"]), smoke_path)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return {
        "case_id": case["case_id"],
        "status": "promoted",
        "oracle_version": oracle["version"],
        "smoke": smoke_result,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Recalibrate eligible PPTR cases on one AVD")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--cases-root", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--oracle-manifest", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--avd", required=True)
    parser.add_argument("--case", action="append")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    registry_path = args.registry.resolve()
    source_root = args.source_root.resolve()
    cases_root = args.cases_root.resolve()
    ensure_backup(args.backup_manifest.resolve(), registry_path)
    if current_avd(args.device) != args.avd:
        raise RuntimeError("running AVD does not match the batch")
    registry = read_json(registry_path)
    formal = read_json(args.oracle_manifest.resolve())
    promoted = {row["case_id"] for row in formal.get("cases", [])}
    requested = set(args.case or [])
    selected: list[tuple[dict[str, Any], Path, Path]] = []
    skipped: list[dict[str, str]] = []
    for case in registry["cases"]:
        case_id = case["case_id"]
        if case.get("task") != "valid_pptr" or (requested and case_id not in requested):
            continue
        avd, snapshot = infer_avd(source_root, case)
        if avd != args.avd:
            continue
        if case_id in promoted:
            skipped.append({"case_id": case_id, "reason": "already_promoted"})
            continue
        case_root = cases_root / case_id
        plan_path = select_plan(case_root)
        if plan_path is None:
            skipped.append({"case_id": case_id, "reason": "no_control_plan"})
            continue
        inspection_source = (
            case_root / "inspection_binding_recalibrated.json"
            if (case_root / "inspection_binding_recalibrated.json").is_file()
            else case_root / "inspection.json"
        )
        inspection = read_json(inspection_source)
        if inspection.get("binding_complete") is not True:
            skipped.append({"case_id": case_id, "reason": "binding_incomplete"})
            continue
        selected.append((case, snapshot, plan_path))
    if requested - {case["case_id"] for case, _, _ in selected} - promoted:
        unresolved = sorted(
            requested - {case["case_id"] for case, _, _ in selected} - promoted
        )
        raise RuntimeError(f"requested cases are not eligible on this AVD: {unresolved}")
    if args.limit is not None:
        selected = selected[: args.limit]

    config = read_json(args.config.resolve())
    module_root = Path(__file__).parent
    recalibrator_hash = implementation_sha256(
        module_root / "structured_live_recalibration.py",
        module_root / "structured_live_controls.py",
        module_root / "structured_android.py",
        module_root / "structured_case_reset.py",
        module_root / "structured_snapshot_reset.py",
        module_root / "structured_visual_observer.py",
    )
    control_runner_hash = implementation_sha256(
        module_root / "structured_live_controls.py",
        module_root / "structured_android.py",
        module_root / "structured_case_reset.py",
        module_root / "structured_snapshot_reset.py",
        module_root / "structured_recovery.py",
        module_root / "structured_visual_observer.py",
    )
    results: list[dict[str, Any]] = []
    for ordinal, (case, snapshot, source_plan_path) in enumerate(selected, 1):
        case_id = case["case_id"]
        case_root = cases_root / case_id
        recalibration_root = case_root / "recalibration"
        recalibration_path = recalibration_root / "recalibration_result.json"
        revised_inspection_path = case_root / "inspection_recalibrated.json"
        revised_plan_path = case_root / "control_plan_recalibrated.json"
        control_root = case_root / "controls_recalibrated"
        control_path = control_root / "control_result.json"
        try:
            prior = read_json(recalibration_path) if recalibration_path.is_file() else {}
            source_plan_hash = sha256_file(source_plan_path)
            inspection_path = (
                case_root / "inspection_binding_recalibrated.json"
                if (case_root / "inspection_binding_recalibrated.json").is_file()
                else case_root / "inspection.json"
            )
            source_inspection_hash = sha256_file(inspection_path)
            if (
                prior.get("status") != "recalibrated_candidate"
                or prior.get("source_plan_sha256") != source_plan_hash
                or prior.get("source_inspection_sha256") != source_inspection_hash
                or prior.get("recalibrator_sha256") != recalibrator_hash
            ):
                inspection = {**read_json(inspection_path), "_path": str(inspection_path)}
                plan = {**read_json(source_plan_path), "_path": str(source_plan_path)}
                legacy_path = Path(case["app_or_page_source"]["config"])
                legacy = yaml.safe_load(legacy_path.read_text(encoding="utf-8")) or {}
                recalibration_root.mkdir(parents=True, exist_ok=True)
                recalibrated = recalibrate(
                    case,
                    inspection,
                    plan,
                    legacy,
                    args.device,
                    args.avd,
                    snapshot,
                    recalibration_root,
                )
                recalibrated["source_inspection_sha256"] = source_inspection_hash
                recalibrated["recalibrator_sha256"] = recalibrator_hash
                atomic_json(recalibration_path, recalibrated)
            inspection = {
                **read_json(revised_inspection_path),
                "_path": str(revised_inspection_path),
            }
            plan = {**read_json(revised_plan_path), "_path": str(revised_plan_path)}
        except Exception as exc:
            result = error_result(case_id, "recalibration", exc)
            atomic_json(recalibration_path, result)
            results.append(result)
            print(f"[{ordinal}/{len(selected)}] {case_id} {result['status']}: {result['error']}", flush=True)
            continue

        try:
            old_control = read_json(control_path) if control_path.is_file() else {}
            current_inspection_hash = sha256_file(revised_inspection_path)
            current_plan_hash = sha256_file(revised_plan_path)
            if (
                old_control.get("status") != "controls_passed_local_only"
                or old_control.get("inspection_sha256") != current_inspection_hash
                or old_control.get("plan_sha256") != current_plan_hash
                or old_control.get("control_runner_sha256") != control_runner_hash
            ):
                control = run_controls(
                    case,
                    inspection,
                    plan,
                    args.device,
                    args.avd,
                    snapshot,
                    control_root,
                )
                control["control_runner_sha256"] = control_runner_hash
                atomic_json(control_path, control)
            control = {**read_json(control_path), "_path": str(control_path)}
        except Exception as exc:
            result = error_result(case_id, "control", exc)
            atomic_json(control_path, result)
            results.append(result)
            print(f"[{ordinal}/{len(selected)}] {case_id} {result['status']}: {result['error']}", flush=True)
            continue

        try:
            result = promote_with_lock(
                case,
                inspection,
                plan,
                control,
                args.avd,
                args.schema.resolve(),
                config,
                args.oracle_manifest.resolve(),
                case_root,
            )
        except Exception as exc:
            result = error_result(case_id, "promotion", exc)
        atomic_json(case_root / "promotion_recalibrated.json", result)
        results.append(result)
        print(f"[{ordinal}/{len(selected)}] {case_id} {result['status']}", flush=True)

    counts = Counter(row["status"] for row in results)
    summary = {
        "schema_version": "e2-structure-v1-recalibration-batch-1",
        "avd": args.avd,
        "device": args.device,
        "selected": [case["case_id"] for case, _, _ in selected],
        "counts": dict(sorted(counts.items())),
        "results": results,
        "skipped": skipped,
    }
    summary_path = cases_root.parent / f"recalibration_batch_{args.avd}.json"
    atomic_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
