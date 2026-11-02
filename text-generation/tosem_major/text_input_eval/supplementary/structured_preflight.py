"""Build a traceable 152-case registry and fail-closed E2 preflight report.

The command is read-only with respect to Android, model gateways, and source
datasets.  It writes only generated manifests under the requested output path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
EXPECTED_COUNTS = {"valid_pptr": 115, "invalid_bug": 37}
PROMPT_KEYS = ("global", "component", "adjacent", "restrictive", "guiding")
JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
BARE_JSON_RE = re.compile(r"(\{.*\})", re.DOTALL)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def model_probe_contract(config: dict[str, Any]) -> dict[str, Any]:
    runtime = config.get("runtime", {})
    return {
        "protocol_id": config.get("protocol_id"),
        "model_ids": [row.get("id") for row in config.get("models", [])],
        "model_request": config.get("model_request"),
        "api_url": runtime.get("api_url"),
        "api_token_env": runtime.get("api_token_env"),
        "verify_ssl": runtime.get("verify_ssl"),
    }


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_config_path(config_path: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else (config_path.parent / path).resolve()


def prompt_resource_ids(restrictive: str) -> list[str]:
    match = JSON_BLOCK_RE.search(restrictive) or BARE_JSON_RE.search(restrictive)
    if not match:
        raise ValueError("cannot parse required JSON schema")
    value = json.loads(match.group(1))
    if not isinstance(value, dict) or not value:
        raise ValueError("required JSON schema is empty")
    return list(value)


def extracted_prompt(row: dict[str, Any]) -> str:
    return "\n".join(str(row[key]) for key in PROMPT_KEYS if str(row.get(key, "")).strip()).strip()


def _load_index(path: Path | None, key: str = "case_id") -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if path is None or not path.is_file():
        return {}, {"available": False, "path": str(path) if path else None, "sha256": None}
    data = read_json(path)
    rows = data.get("cases", data) if isinstance(data, dict) else data
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list):
        raise ValueError(f"unsupported registry structure: {path}")
    index = {str(row[key]): row for row in rows}
    if len(index) != len(rows):
        raise ValueError(f"duplicate {key} in {path}")
    return index, {"available": True, "path": str(path), "sha256": sha256_file(path), "rows": len(rows)}


def _load_mirror(mirror_root: Path | None) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if mirror_root is None or not mirror_root.is_dir():
        return {}, {"available": False, "path": str(mirror_root) if mirror_root else None}
    inventory_path = mirror_root / "inventory.json"
    if not inventory_path.is_file():
        return {}, {"available": False, "path": str(mirror_root), "reason": "inventory_missing"}
    inventory = read_json(inventory_path)
    rows = inventory.get("cases", inventory)
    if not isinstance(rows, list):
        raise ValueError("mirror inventory cases must be a list")
    merged: dict[str, dict[str, Any]] = {}
    for source_row in rows:
        case_id = source_row["case_id"]
        row = source_row
        main_manifest = mirror_root / "cases" / case_id / "manifest.json"
        if main_manifest.is_file():
            row = read_json(main_manifest)
            row = {**row, "_manifest_path": str(main_manifest), "_manifest_sha256": sha256_file(main_manifest)}
        remediation = mirror_root / "google_eval" / "cases" / case_id / "manifest.json"
        if remediation.is_file():
            candidate = read_json(remediation)
            if candidate.get("status") == "ready":
                row = {**candidate, "_manifest_path": str(remediation), "_manifest_sha256": sha256_file(remediation)}
        merged[case_id] = row
    return merged, {
        "available": True,
        "path": str(mirror_root),
        "inventory_path": str(inventory_path),
        "inventory_sha256": sha256_file(inventory_path),
        "rows": len(merged),
    }


def _validate_formal_exclusions(
    path: Path,
    config: dict[str, Any],
    source_cases: dict[str, dict[str, Any]],
    exclusions: dict[str, dict[str, Any]],
) -> None:
    manifest = read_json(path)
    policy = manifest.get("policy", {})
    if (
        manifest.get("schema_version") != "e2-structure-v1-formal-exclusions-1"
        or manifest.get("protocol_id") != config.get("protocol_id")
        or not isinstance(manifest.get("applicability_revision"), str)
        or not manifest["applicability_revision"]
        or policy.get("disposition") != "not_applicable"
        or policy.get("formal_denominator_action") != "exclude"
        or policy.get("oracle_substitution_prohibited") is not True
        or policy.get("silent_case_removal_prohibited") is not True
    ):
        raise ValueError("formal exclusion policy is incomplete")
    allowed_reasons = {
        "unavailable_behavior_endpoint",
        "incomplete_snapshot_activity",
        "unsafe_remote_mutation_without_rollback",
    }
    for case_id, exclusion in exclusions.items():
        source = source_cases.get(case_id)
        if source is None or source.get("task") != "valid_pptr":
            raise ValueError(f"formal exclusion is not a frozen PPTR case: {case_id}")
        if (
            exclusion.get("task") != source["task"]
            or exclusion.get("disposition") != "not_applicable"
            or exclusion.get("reason_code") not in allowed_reasons
            or exclusion.get("replacement_oracle") is not False
            or not isinstance(exclusion.get("rationale"), str)
            or len(exclusion["rationale"].strip()) < 40
        ):
            raise ValueError(f"formal exclusion record is incomplete: {case_id}")
        evidence = exclusion.get("evidence")
        if not isinstance(evidence, list) or len(evidence) < 2:
            raise ValueError(f"formal exclusion evidence is incomplete: {case_id}")
        for item in evidence:
            label = item.get("path") if isinstance(item, dict) else None
            expected = item.get("sha256") if isinstance(item, dict) else None
            if (
                not isinstance(label, str)
                or not label
                or Path(label).is_absolute()
                or not isinstance(item.get("kind"), str)
                or not isinstance(expected, str)
                or len(expected) != 64
            ):
                raise ValueError(f"formal exclusion evidence reference is invalid: {case_id}")
            evidence_path = (REPO_ROOT / label).resolve()
            if not evidence_path.is_relative_to(REPO_ROOT) or not evidence_path.is_file():
                raise ValueError(f"formal exclusion evidence is unavailable: {case_id}")
            if sha256_file(evidence_path) != expected:
                raise ValueError(f"formal exclusion evidence hash drift: {case_id}")


def _calibration_evidence_paths(
    oracle_manifest_path: Path, oracle: dict[str, Any],
) -> list[Path]:
    calibration = oracle.get("calibration", {})
    positive = str(calibration.get("positive_control", ""))
    negative = str(calibration.get("negative_control", ""))
    positive_label = positive.split("#", 1)[0]
    negative_label = negative.split("#", 1)[0]
    if not positive_label or positive_label != negative_label:
        raise ValueError(f"{oracle.get('case_id')} control evidence references differ")
    control_path = (oracle_manifest_path.parent / positive_label).resolve()
    if not control_path.is_file():
        raise FileNotFoundError(f"{oracle.get('case_id')} control evidence is unavailable")
    control = read_json(control_path)
    if any((
        control.get("case_id") != oracle.get("case_id"),
        control.get("status") != "controls_passed_local_only",
        control.get("formal_oracle_promoted") is not True,
        control.get("formal_oracle_version") != oracle.get("version"),
        not isinstance(control.get("positive_control"), dict),
        not isinstance(control.get("negative_control"), dict),
    )):
        raise ValueError(f"{oracle.get('case_id')} control evidence identity/status differs")
    case_root = control_path.parent.parent
    inspections = [
        path for path in case_root.glob("inspection*.json")
        if sha256_file(path) == calibration.get("inspection_sha256")
    ]
    if len(inspections) != 1:
        raise ValueError(f"{oracle.get('case_id')} inspection evidence does not resolve uniquely")
    inspection_path = inspections[0]
    plans = [
        path for path in case_root.glob("control_plan*.json")
        if sha256_file(path) == calibration.get("plan_sha256")
    ]
    if len(plans) != 1:
        raise ValueError(f"{oracle.get('case_id')} plan evidence does not resolve uniquely")
    smokes = [
        path for path in case_root.glob("formal_adapter_smoke*/result.json")
        if sha256_file(path) == calibration.get("formal_adapter_smoke_sha256")
    ]
    if len(smokes) != 1:
        raise ValueError(f"{oracle.get('case_id')} formal smoke evidence does not resolve uniquely")
    smoke_path = smokes[0]
    smoke = read_json(smoke_path)
    if smoke.get("case_id") != oracle.get("case_id") or smoke.get("oracle_version") != oracle.get("version"):
        raise ValueError(f"{oracle.get('case_id')} formal smoke identity differs")
    return [control_path, inspection_path, plans[0], smoke_path]


def _validator_path(task: str, ordinal: int) -> Path:
    family = "pptr" if task == "valid_pptr" else "bug"
    stem = "page" if task == "valid_pptr" else "bug"
    return REPO_ROOT / "tosem_major" / "text_input_eval" / "validators" / family / f"{stem}_{ordinal:03d}.py"


def _case_rows(cache_root: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for task, filename, directory in (
        ("valid_pptr", "pptr_prompts.json", "pptr"),
        ("invalid_bug", "bug_prompts.json", "bug"),
    ):
        prompt_path = cache_root / filename
        rows = read_json(prompt_path)
        if not isinstance(rows, list) or len(rows) != EXPECTED_COUNTS[task]:
            raise ValueError(f"unexpected {task} prompt count")
        for ordinal, prompt_row in enumerate(rows, 1):
            if task == "valid_pptr":
                case_id = f"P{ordinal:03d}"
                target = str(prompt_row["app_id"])
            else:
                page_id = int(float(prompt_row["page_id"]))
                case_id = f"B{page_id:03d}"
                target = str(page_id)
            xml_path = cache_root / directory / f"hierarchy_{target}.xml"
            validator_path = _validator_path(task, ordinal if task == "valid_pptr" else int(target))
            if not xml_path.is_file() or not validator_path.is_file():
                raise FileNotFoundError(f"missing source for {case_id}")
            prompt = extracted_prompt(prompt_row)
            output.append({
                "case_id": case_id,
                "task": task,
                "ordinal_in_source": ordinal,
                "source_target": target,
                "required_resource_ids": prompt_resource_ids(str(prompt_row["restrictive"])),
                "source_files": {
                    "prompt_collection": str(prompt_path.relative_to(REPO_ROOT)),
                    "prompt_collection_sha256": sha256_file(prompt_path),
                    "cached_xml": str(xml_path.relative_to(REPO_ROOT)),
                    "cached_xml_sha256": sha256_file(xml_path),
                    "validator": str(validator_path.relative_to(REPO_ROOT)),
                    "validator_sha256": sha256_file(validator_path),
                },
                "prompt_record_sha256": sha256_json(prompt_row),
                "extracted_prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
                "extracted_prompt_bytes": len(prompt.encode("utf-8")),
            })
    ids = [row["case_id"] for row in output]
    expected = [f"P{i:03d}" for i in range(1, 116)] + [f"B{i:03d}" for i in range(1, 38)]
    if ids != expected:
        raise ValueError("case IDs differ from the frozen source order/page_id mapping")
    return output


def build_registry(config_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    config = read_json(config_path)
    sources = config["sources"]
    cache_root = resolve_config_path(config_path, sources["cache_root"])
    page_inventory_path = resolve_config_path(config_path, sources.get("page_source_inventory"))
    oracle_path = resolve_config_path(config_path, sources.get("oracle_audit"))
    binding_review_path = resolve_config_path(config_path, sources.get("binding_review_resolutions"))
    bug_evidence_path = resolve_config_path(config_path, sources.get("bug_evidence"))
    mirror_root = resolve_config_path(config_path, sources.get("mirror_root"))
    formal_oracle_path = resolve_config_path(config_path, config.get("runtime", {}).get("oracle_manifest"))
    formal_exclusion_path = resolve_config_path(
        config_path, config.get("runtime", {}).get("formal_exclusions")
    )
    model_preflight_path = resolve_config_path(config_path, config.get("runtime", {}).get("model_preflight_report"))
    if cache_root is None or not cache_root.is_dir():
        raise FileNotFoundError("cache_root is unavailable")
    page_inventory, page_source_meta = _load_index(page_inventory_path)
    oracle_audit, oracle_source_meta = _load_index(oracle_path)
    binding_resolutions, binding_resolution_meta = _load_index(binding_review_path)
    bug_evidence, bug_source_meta = _load_index(bug_evidence_path)
    mirror, mirror_meta = _load_mirror(mirror_root)
    formal_oracles, formal_oracle_meta = _load_index(formal_oracle_path)
    formal_exclusions, formal_exclusion_meta = _load_index(formal_exclusion_path)
    source_rows = _case_rows(cache_root)
    source_cases = {row["case_id"]: row for row in source_rows}
    if formal_exclusion_path is None or not formal_exclusion_path.is_file():
        raise FileNotFoundError("runtime.formal_exclusions is unavailable")
    _validate_formal_exclusions(
        formal_exclusion_path, config, source_cases, formal_exclusions,
    )
    overlap = set(formal_oracles) & set(formal_exclusions)
    if overlap:
        raise ValueError(f"cases cannot have both an Oracle and an exclusion: {sorted(overlap)}")
    if formal_oracles:
        oracle_schema = read_json(config_path.with_name("e2_structure_v1_oracle.schema.json"))
        validator = Draft202012Validator(oracle_schema)
        validation_errors = []
        for case_id, oracle in formal_oracles.items():
            for error in validator.iter_errors(oracle):
                location = "/".join(str(item) for item in error.absolute_path)
                validation_errors.append(f"{case_id}:{location}:{error.message}")
        if validation_errors:
            raise ValueError(f"formal Oracle schema validation failed: {validation_errors[:8]}")
    if model_preflight_path is not None and model_preflight_path.is_file():
        model_preflight = read_json(model_preflight_path)
        model_preflight_meta = {
            "available": True,
            "path": str(model_preflight_path),
            "sha256": sha256_file(model_preflight_path),
        }
    else:
        model_preflight = {}
        model_preflight_meta = {
            "available": False,
            "path": str(model_preflight_path) if model_preflight_path else None,
            "sha256": None,
        }
    model_evidence = {
        row.get("model_id"): row
        for row in model_preflight.get("models", [])
        if isinstance(row, dict) and isinstance(row.get("model_id"), str)
    }
    manual_review = set(config.get("manual_binding_review_cases", []))
    rows = []
    for base in source_rows:
        case_id = base["case_id"]
        page_source = page_inventory.get(case_id) if case_id.startswith("P") else None
        if page_source and page_source.get("config"):
            page_config = (REPO_ROOT / page_source["config"]).resolve()
            if page_config.is_file():
                base["source_files"]["page_config"] = str(page_config.relative_to(REPO_ROOT))
                base["source_files"]["page_config_sha256"] = sha256_file(page_config)
        mirror_row = mirror.get(case_id, {})
        formal_oracle = formal_oracles.get(case_id, {})
        formal_exclusion = formal_exclusions.get(case_id)
        formal_run_in_scope = formal_exclusion is None
        predicate = formal_oracle.get("predicate", {}) if isinstance(formal_oracle, dict) else {}
        predicate_ready = base["task"] == "invalid_bug" and bool(formal_oracle) and all((
            formal_oracle.get("task") == "invalid_bug",
            formal_oracle.get("oracle_kind") == "string_predicate",
            predicate.get("validator_path") == base["source_files"]["validator"],
            predicate.get("validator_sha256") == base["source_files"]["validator_sha256"],
            predicate.get("required_resource_ids") == base["required_resource_ids"],
            predicate.get("true_means") == "bug_revealed",
            predicate.get("control_pair_verified") is True,
            isinstance(predicate.get("positive_control_sha256"), str),
            isinstance(predicate.get("negative_control_sha256"), str),
        ))
        calibration = formal_oracle.get("calibration", {}) if isinstance(formal_oracle, dict) else {}
        scope = mirror_row.get("readiness_scope") or (
            "legacy_ready_scope_unclassified" if mirror_row.get("status") == "ready" else "unavailable"
        )
        target_page_ready = base["task"] == "invalid_bug" or mirror_row.get("status") == "ready"
        attempt_reset_ready = predicate_ready or (bool(formal_oracle) and all((
            calibration.get("reset_verified") is True,
            calibration.get("version_alignment_verified") is True,
            bool(formal_oracle.get("begin_attempt", {}).get("reset_command")),
            bool(formal_oracle.get("begin_attempt", {}).get("reset_id")),
            formal_oracle.get("begin_attempt", {}).get("remote_baseline") in {"verified", "not_applicable"},
        )))
        if base["task"] == "invalid_bug":
            attempt_reset_ready = True
        elif not formal_oracle:
            attempt_reset_ready = (
                scope == "full_independent_attempt_with_remote_reset"
                and bool(mirror_row.get("attempt_prepare_command"))
                and bool(mirror_row.get("attempt_reset_id"))
            )
        recovery_ready = predicate_ready or (bool(formal_oracle) and all((
            calibration.get("recovery_verified") is True,
            bool(formal_oracle.get("recover_turn", {}).get("command")),
            formal_oracle.get("recover_turn", {}).get("snapshot_loaded") is False,
            formal_oracle.get("recover_turn", {}).get("remote_reset") is False,
            formal_oracle.get("recover_turn", {}).get("renavigated_from_baseline") is False,
            formal_oracle.get("recover_turn", {}).get("natural_state_preserved") is True,
            formal_oracle.get("recover_turn", {}).get("dynamic_relocation_required") is True,
        )))
        if base["task"] == "invalid_bug":
            recovery_ready = True
        audit = oracle_audit.get(case_id, {})
        evidence_chain_verified = False
        if base["task"] == "valid_pptr" and formal_oracle and formal_oracle_path is not None:
            try:
                _calibration_evidence_paths(formal_oracle_path, formal_oracle)
                evidence_chain_verified = True
            except (FileNotFoundError, ValueError):
                evidence_chain_verified = False
        behavior_oracle_ready = predicate_ready or (bool(formal_oracle) and all((
            calibration.get("version_alignment_verified") is True,
            calibration.get("artifact_identity_policy") == "installed_apk_set_sha256",
            isinstance(calibration.get("inspection_sha256"), str),
            isinstance(calibration.get("plan_sha256"), str),
            isinstance(calibration.get("formal_adapter_smoke_sha256"), str),
            evidence_chain_verified,
            isinstance(calibration.get("positive_control"), str) and bool(calibration.get("positive_control")),
            isinstance(calibration.get("negative_control"), str) and bool(calibration.get("negative_control")),
            bool(formal_oracle.get("goal_assertion")),
            formal_oracle.get("task") == base["task"],
            set(formal_oracle.get("binding", {})) == set(base["required_resource_ids"]),
        )))
        if base["task"] == "valid_pptr" and not formal_oracle:
            behavior_oracle_ready = audit.get("behavior_oracle_ready") is True
        binding_resolution = binding_resolutions.get(case_id, {})
        binding_inspection_path = (
            binding_review_path.parent / "live_calibration" / "cases" / case_id / "inspection.json"
            if binding_review_path is not None else None
        )
        binding_resolution_verified = bool(
            binding_resolution.get("status") == "resolved"
            and set(binding_resolution.get("required_resource_ids", []))
            == set(base["required_resource_ids"])
            and set(binding_resolution.get("binding", {}))
            == set(base["required_resource_ids"])
            and binding_inspection_path is not None
            and binding_inspection_path.is_file()
            and binding_resolution.get("inspection_sha256")
            == sha256_file(binding_inspection_path)
        )
        binding_review_required = (
            base["task"] == "valid_pptr"
            and formal_run_in_scope
            and case_id in manual_review
            and formal_oracle.get("binding_review_resolved") is not True
            and not binding_resolution_verified
        )
        blockers = []
        if not target_page_ready:
            blockers.append("target_page_mirror_not_ready")
        if not attempt_reset_ready:
            blockers.append("independent_attempt_reset_not_verified")
        if not recovery_ready:
            blockers.append("within_attempt_recovery_not_verified")
        if not behavior_oracle_ready:
            blockers.append("behavior_oracle_not_calibrated")
        if binding_review_required:
            blockers.append("binding_requires_manual_confirmation")
        row = {
            **base,
            "app_or_page_source": page_source if case_id.startswith("P") else bug_evidence.get(case_id),
            "candidate_validator_audit": {
                "source_status": audit.get("status", "missing"),
                "binding_exact": audit.get("binding_exact"),
                "predicate_control_pair": audit.get("predicate_control_pair"),
                "behavior_oracle_ready": behavior_oracle_ready,
                "note": audit.get("behavior_oracle_note"),
            },
            "formal_oracle": {
                "available": bool(formal_oracle),
                "version": formal_oracle.get("version"),
                "oracle_kind": formal_oracle.get("oracle_kind") or (
                    "app_behavior" if formal_oracle else None
                ),
                "artifact_sha256": formal_oracle.get("app_artifact", {}).get("sha256"),
                "evidence_chain_verified": evidence_chain_verified if base["task"] == "valid_pptr" else predicate_ready,
                "binding_keys_exact": (
                    predicate.get("required_resource_ids") == base["required_resource_ids"]
                    if base["task"] == "invalid_bug"
                    else set(formal_oracle.get("binding", {})) == set(base["required_resource_ids"])
                ),
                "calibration": calibration,
            },
            "formal_applicability": {
                "in_scope": formal_run_in_scope,
                "disposition": (
                    "included" if formal_run_in_scope else formal_exclusion["disposition"]
                ),
                "reason_code": (
                    None if formal_run_in_scope else formal_exclusion["reason_code"]
                ),
                "rationale": (
                    None if formal_run_in_scope else formal_exclusion["rationale"]
                ),
                "evidence": (
                    [] if formal_run_in_scope else formal_exclusion["evidence"]
                ),
                "replacement_oracle": (
                    None if formal_run_in_scope else formal_exclusion["replacement_oracle"]
                ),
            },
            "mirror": {
                "available": bool(mirror_row),
                "status": mirror_row.get("status", "missing"),
                "package": mirror_row.get("package"),
                "version_code": mirror_row.get("version_code"),
                "source_sha256": mirror_row.get("source_sha256"),
                "snapshot": mirror_row.get("target_hold_snapshot") or mirror_row.get("snapshot"),
                "snapshot_path": mirror_row.get("target_hold_snapshot_path") or mirror_row.get("snapshot_path"),
                "readiness_scope": scope,
                "expected_activity": mirror_row.get("expected_activity"),
                "expected_fields": mirror_row.get("expected_fields", []),
                "semantic_fields": mirror_row.get("semantic_fields", []),
                "indexed_field_contracts": mirror_row.get("indexed_field_contracts", {}),
                "page_guard_texts": mirror_row.get("page_guard_texts", []),
                "page_guard_descriptions": mirror_row.get("page_guard_descriptions", []),
                "locator_migration": mirror_row.get("locator_migration"),
                "device": {
                    "serial": mirror_row.get("serial"),
                    "avd": mirror_row.get("avd") or mirror_row.get("avd_name"),
                    "snapshot_load_strategy": mirror_row.get("snapshot_load_strategy"),
                },
                "attempt_reset": {
                    "reset_id": mirror_row.get("attempt_reset_id"),
                    "prepare_command": mirror_row.get("attempt_prepare_command"),
                },
                "manifest_path": mirror_row.get("_manifest_path"),
                "manifest_sha256": mirror_row.get("_manifest_sha256"),
            },
            "protocol_readiness": {
                "target_page_ready": target_page_ready,
                "independent_attempt_reset_ready": attempt_reset_ready,
                "within_attempt_recovery_ready": recovery_ready,
                "behavior_oracle_ready": behavior_oracle_ready,
                "binding_review_required": binding_review_required,
                "binding_review_resolution_verified": binding_resolution_verified,
                "formal_run_in_scope": formal_run_in_scope,
                "formal_run_ready": formal_run_in_scope and not blockers,
                "blockers": blockers,
            },
        }
        rows.append(row)
    counts = {
        "cases": len(rows),
        "valid_pptr": sum(row["task"] == "valid_pptr" for row in rows),
        "invalid_bug": sum(row["task"] == "invalid_bug" for row in rows),
        "formal_applicable": sum(row["formal_applicability"]["in_scope"] for row in rows),
        "formal_excluded": sum(not row["formal_applicability"]["in_scope"] for row in rows),
        "formal_applicable_valid_pptr": sum(
            row["task"] == "valid_pptr" and row["formal_applicability"]["in_scope"]
            for row in rows
        ),
        "formal_applicable_invalid_bug": sum(
            row["task"] == "invalid_bug" and row["formal_applicability"]["in_scope"]
            for row in rows
        ),
        "target_page_ready": sum(row["protocol_readiness"]["target_page_ready"] for row in rows),
        "independent_attempt_reset_ready": sum(row["protocol_readiness"]["independent_attempt_reset_ready"] for row in rows),
        "within_attempt_recovery_ready": sum(row["protocol_readiness"]["within_attempt_recovery_ready"] for row in rows),
        "behavior_oracle_ready": sum(row["protocol_readiness"]["behavior_oracle_ready"] for row in rows),
        "binding_review_required": sum(row["protocol_readiness"]["binding_review_required"] for row in rows),
        "formal_run_ready": sum(row["protocol_readiness"]["formal_run_ready"] for row in rows),
        "applicable_target_page_ready": sum(
            row["formal_applicability"]["in_scope"]
            and row["protocol_readiness"]["target_page_ready"]
            for row in rows
        ),
        "applicable_independent_attempt_reset_ready": sum(
            row["formal_applicability"]["in_scope"]
            and row["protocol_readiness"]["independent_attempt_reset_ready"]
            for row in rows
        ),
        "applicable_within_attempt_recovery_ready": sum(
            row["formal_applicability"]["in_scope"]
            and row["protocol_readiness"]["within_attempt_recovery_ready"]
            for row in rows
        ),
        "applicable_behavior_oracle_ready": sum(
            row["formal_applicability"]["in_scope"]
            and row["protocol_readiness"]["behavior_oracle_ready"]
            for row in rows
        ),
        "mirror_readiness_scope": dict(Counter(row["mirror"]["readiness_scope"] for row in rows)),
        "validator_audit_status": dict(Counter(row["candidate_validator_audit"]["source_status"] for row in rows)),
    }
    registry = {
        "schema_version": "e2-structure-v1-case-registry-1",
        "protocol_id": config["protocol_id"],
        "id_mapping": {
            "valid_pptr": "P001-P115 use pptr_prompts.json array order without re-sorting",
            "invalid_bug": "B001-B037 use each bug prompt page_id, not array position assumptions",
        },
        "source_registries": {
            "page_source_inventory": page_source_meta,
            "oracle_audit": oracle_source_meta,
            "binding_review_resolutions": binding_resolution_meta,
            "formal_behavior_oracles": formal_oracle_meta,
            "formal_exclusions": formal_exclusion_meta,
            "model_preflight": model_preflight_meta,
            "bug_evidence": bug_source_meta,
            "mirror": mirror_meta,
        },
        "counts": counts,
        "cases": rows,
    }
    qualification_fields = (
        "gateway_verified", "revision_verified", "generation_defaults_verified",
        "capacity_verified", "fresh_session_isolation_verified",
    )
    models_unqualified = []
    model_qualification = []
    for model in config["models"]:
        evidence = model_evidence.get(model["id"], {})
        ready = (
            model_preflight.get("probe_contract_sha256") == sha256_json(model_probe_contract(config))
            and all(model.get(field) is True for field in qualification_fields)
            and all(evidence.get(field) is True for field in qualification_fields)
            and isinstance(model.get("max_prompt_bytes"), int)
            and model["max_prompt_bytes"] > 0
            and evidence.get("max_prompt_bytes") == model["max_prompt_bytes"]
        )
        model_qualification.append({
            "model_id": model["id"],
            "ready": ready,
            "config": {field: model.get(field) for field in qualification_fields},
            "evidence": {field: evidence.get(field) for field in qualification_fields},
            "max_prompt_bytes": model.get("max_prompt_bytes"),
            "evidence_max_prompt_bytes": evidence.get("max_prompt_bytes"),
        })
        if not ready:
            models_unqualified.append(model["id"])
    all_case_ids = {row["case_id"] for row in rows}
    applicable_case_ids = {
        row["case_id"] for row in rows if row["formal_applicability"]["in_scope"]
    }
    applicability_covered = (
        set(formal_oracles) == applicable_case_ids
        and set(formal_oracles) | set(formal_exclusions) == all_case_ids
        and not (set(formal_oracles) & set(formal_exclusions))
    )
    applicable_count = counts["formal_applicable"]
    gates = [
        {"id": "case_universe", "status": "pass" if len(rows) == 152 else "block", "value": len(rows)},
        {
            "id": "explicit_applicability",
            "status": "pass" if applicability_covered else "block",
            "included": counts["formal_applicable"],
            "excluded": counts["formal_excluded"],
            "excluded_case_ids": sorted(formal_exclusions),
            "requirement": "Every frozen case is covered by exactly one formal Oracle or hash-pinned not-applicable record.",
        },
        {
            "id": "representation_decision",
            "status": "pass" if config["representation"]["decision_status"] == "frozen" else "block",
            "value": config["representation"]["decision_status"],
        },
        {"id": "model_qualification", "status": "block" if models_unqualified else "pass", "models": models_unqualified},
        {
            "id": "fresh_session_isolation",
            "status": "block" if any(not model.get("fresh_session_isolation_verified") for model in config["models"]) else "pass",
            "requirement": "Every request uses id=empty and gateway-side isolation is empirically verified.",
        },
        {"id": "target_page_mirror", "status": "pass" if counts["applicable_target_page_ready"] == applicable_count else "block", "ready": counts["applicable_target_page_ready"], "required": applicable_count},
        {"id": "independent_attempt_reset", "status": "pass" if counts["applicable_independent_attempt_reset_ready"] == applicable_count else "block", "ready": counts["applicable_independent_attempt_reset_ready"], "required": applicable_count},
        {"id": "within_attempt_recovery", "status": "pass" if counts["applicable_within_attempt_recovery_ready"] == applicable_count else "block", "ready": counts["applicable_within_attempt_recovery_ready"], "required": applicable_count},
        {"id": "behavior_oracle", "status": "pass" if counts["applicable_behavior_oracle_ready"] == applicable_count else "block", "ready": counts["applicable_behavior_oracle_ready"], "required": applicable_count},
        {"id": "binding_review", "status": "pass" if counts["binding_review_required"] == 0 else "block", "remaining": counts["binding_review_required"]},
    ]
    blocks = [gate["id"] for gate in gates if gate["status"] == "block"]
    pptr_blocks = counts["formal_applicable_valid_pptr"] * len(config["models"]) * config["replicates"]
    bug_blocks = counts["formal_applicable_invalid_bug"] * len(config["models"]) * config["replicates"]
    pptr_budget = config["budgets_including_first_turn"]["valid_pptr"]
    bug_budget = config["budgets_including_first_turn"]["invalid_bug"]
    preflight = {
        "schema_version": "e2-structure-v1-preflight-1",
        "protocol_id": config["protocol_id"],
        "formal_run_allowed": config.get("formal_run_enabled") is True and not blocks,
        "blocking_gates": blocks + ([] if config.get("formal_run_enabled") is True else ["formal_run_enabled_false"]),
        "gates": gates,
        "model_qualification": model_qualification,
        "counts": counts,
        "planned_scale_if_primary_extracted_is_frozen": {
            "paired_blocks": pptr_blocks + bug_blocks,
            "arm_trajectories": (pptr_blocks + bug_blocks) * len(config["arms"]),
            "valid_pptr_trajectories": pptr_blocks * len(config["arms"]),
            "invalid_bug_trajectories": bug_blocks * len(config["arms"]),
            "model_request_upper_bound_with_shared_first_generation": (
                pptr_blocks * (1 + len(config["arms"]) * (pptr_budget - 1))
                + bug_blocks * (1 + len(config["arms"]) * (bug_budget - 1))
            ),
            "logical_candidate_execution_slots_upper_bound": (
                pptr_blocks * len(config["arms"]) * pptr_budget
                + bug_blocks * len(config["arms"]) * bug_budget
            ),
            "calibration_and_recovery_costs_included": False,
        },
        "interpretation": {
            "formal_denominator": (
                f"{counts['formal_applicable']} applicable cases; "
                f"{counts['formal_excluded']} hash-pinned not-applicable cases remain in the 152-case registry."
            ),
            "formal_exclusions": (
                "Excluded cases are never executed, never replaced by validators or field-echo Oracles, "
                "and remain visible as not_applicable rather than semantic zero scores."
            ),
            "mirror_ready": "Target-page reachability/locator evidence only; not behavior Oracle, reset, or recovery readiness.",
            "candidate_validator": (
                "For invalid_bug, the frozen exact-key string validator is the success Oracle: "
                "True means bug revealed. It does not require App behavior, APK alignment, UI binding, or AVD reset."
            ),
            "unknown_and_infrastructure": "They remain incomplete and are never converted to zero-score semantic failures.",
        },
    }
    return registry, preflight


def write_outputs(config_path: Path, output_dir: Path) -> dict[str, Any]:
    registry, preflight = build_registry(config_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    registry_path = output_dir / "case_registry.json"
    preflight_path = output_dir / "preflight_report.json"
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    preflight_path.write_text(json.dumps(preflight, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    source_paths = [
        config_path,
        config_path.with_name("e2_structure_v1_oracle.schema.json"),
        Path(__file__).resolve(),
        Path(__file__).with_name("structured_templates.py"),
        Path(__file__).with_name("structured_state.py"),
        Path(__file__).with_name("structured_storage.py"),
        Path(__file__).with_name("structured_gateway.py"),
        Path(__file__).with_name("structured_environment.py"),
        Path(__file__).with_name("structured_android.py"),
        Path(__file__).with_name("structured_recovery.py"),
        Path(__file__).with_name("structured_snapshot_reset.py"),
        Path(__file__).with_name("structured_case_reset.py"),
        Path(__file__).with_name("structured_visual_observer.py"),
        Path(__file__).with_name("structured_snapshot_backup.py"),
        Path(__file__).with_name("structured_live_calibration.py"),
        Path(__file__).with_name("structured_live_controls.py"),
        Path(__file__).with_name("structured_live_summary.py"),
        Path(__file__).with_name("structured_oracle_promotion.py"),
        Path(__file__).with_name("structured_bug_predicates.py"),
        Path(__file__).with_name("structured_live_contract_backfill.py"),
        Path(__file__).with_name("structured_control_candidates.py"),
        Path(__file__).with_name("structured_control_plans.py"),
        Path(__file__).with_name("structured_synthetic_control_candidates.py"),
        Path(__file__).with_name("structured_manual_control_candidates.py"),
        Path(__file__).with_name("structured_oracle_artifact_backfill.py"),
        Path(__file__).with_name("structured_binding_review.py"),
        Path(__file__).with_name("structured_offline_control_plan.py"),
        Path(__file__).with_name("structured_recovery_seed.py"),
        Path(__file__).with_name("structured_recovered_control_plans.py"),
        Path(__file__).with_name("structured_live_recalibration.py"),
        Path(__file__).with_name("structured_recalibration_batch.py"),
        Path(__file__).with_name("structured_recovery_preflight.py"),
        Path(__file__).with_name("structured_runner.py"),
        Path(__file__).with_name("structured_cli.py"),
        Path(__file__).with_name("structured_model_preflight.py"),
        Path(__file__).with_name("structured_oracle_audit.py"),
        output_dir / "PROTOCOL.zh-CN.md",
        registry_path,
        preflight_path,
    ]
    model_preflight_path = resolve_config_path(
        config_path, read_json(config_path).get("runtime", {}).get("model_preflight_report")
    )
    if model_preflight_path is not None and model_preflight_path.is_file():
        source_paths.append(model_preflight_path)
    formal_oracle_path = resolve_config_path(
        config_path, read_json(config_path).get("runtime", {}).get("oracle_manifest")
    )
    if formal_oracle_path is not None and formal_oracle_path.is_file():
        source_paths.append(formal_oracle_path)
        for oracle in read_json(formal_oracle_path).get("cases", []):
            if oracle.get("task") == "valid_pptr":
                source_paths.extend(_calibration_evidence_paths(formal_oracle_path, oracle))
    formal_exclusion_path = resolve_config_path(
        config_path, read_json(config_path).get("runtime", {}).get("formal_exclusions")
    )
    if formal_exclusion_path is not None and formal_exclusion_path.is_file():
        source_paths.append(formal_exclusion_path)
        for exclusion in read_json(formal_exclusion_path).get("cases", []):
            for evidence in exclusion.get("evidence", []):
                source_paths.append((REPO_ROOT / evidence["path"]).resolve())
    binding_review_path = resolve_config_path(
        config_path, read_json(config_path).get("sources", {}).get("binding_review_resolutions")
    )
    if binding_review_path is not None and binding_review_path.is_file():
        source_paths.append(binding_review_path)
        for resolution in read_json(binding_review_path).get("cases", []):
            if resolution.get("status") == "resolved":
                source_paths.append(
                    binding_review_path.parent / "live_calibration" / "cases"
                    / resolution["case_id"] / "inspection.json"
                )
    oracle_gap_path = output_dir / "oracle_gap_report.json"
    if oracle_gap_path.is_file():
        source_paths.append(oracle_gap_path)
    source_paths = list(dict.fromkeys(source_paths))
    missing_freeze_inputs = [str(path) for path in source_paths if not path.is_file()]
    if missing_freeze_inputs:
        raise FileNotFoundError(f"freeze inputs missing: {missing_freeze_inputs}")
    freeze = {
        "schema_version": "e2-structure-v1-freeze-manifest-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_id": "E2-structure-v1",
        "formal_run_allowed": preflight["formal_run_allowed"],
        "files": {
            str(path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path): sha256_file(path)
            for path in source_paths
        },
        "case_registry_sha256": sha256_file(registry_path),
        "preflight_report_sha256": sha256_file(preflight_path),
        "case_source_digest": sha256_json([
            {
                "case_id": row["case_id"],
                "prompt": row["extracted_prompt_sha256"],
                "xml": row["source_files"]["cached_xml_sha256"],
                "validator": row["source_files"]["validator_sha256"],
                "mirror_manifest": row["mirror"]["manifest_sha256"],
            }
            for row in registry["cases"]
        ]),
    }
    freeze_path = output_dir / "freeze_manifest.json"
    freeze_path.write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return freeze


def verify_freeze(output_dir: Path) -> dict[str, Any]:
    freeze_path = output_dir / "freeze_manifest.json"
    freeze = read_json(freeze_path)
    mismatches = []
    for label, expected in freeze.get("files", {}).items():
        path = Path(label)
        path = path if path.is_absolute() else REPO_ROOT / path
        actual = sha256_file(path) if path.is_file() else None
        if actual != expected:
            mismatches.append({"path": label, "expected": expected, "actual": actual})
    registry_path = output_dir / "case_registry.json"
    registry = read_json(registry_path)
    checked_sources: set[tuple[str, str]] = set()
    for row in registry["cases"]:
        source_files = row["source_files"]
        for path_key in ("prompt_collection", "cached_xml", "validator", "page_config"):
            expected = source_files.get(f"{path_key}_sha256")
            label = source_files.get(path_key)
            if not expected or not label or (label, expected) in checked_sources:
                continue
            checked_sources.add((label, expected))
            path = Path(label)
            path = path if path.is_absolute() else REPO_ROOT / path
            actual = sha256_file(path) if path.is_file() else None
            if actual != expected:
                mismatches.append({"path": label, "expected": expected, "actual": actual})
        mirror_label = row["mirror"].get("manifest_path")
        mirror_expected = row["mirror"].get("manifest_sha256")
        if mirror_label and mirror_expected and (mirror_label, mirror_expected) not in checked_sources:
            checked_sources.add((mirror_label, mirror_expected))
            mirror_path = Path(mirror_label)
            actual = sha256_file(mirror_path) if mirror_path.is_file() else None
            if actual != mirror_expected:
                mismatches.append({"path": mirror_label, "expected": mirror_expected, "actual": actual})
    for source in registry.get("source_registries", {}).values():
        if source.get("inventory_path"):
            label = source.get("inventory_path")
            expected = source.get("inventory_sha256")
        else:
            label = source.get("path")
            expected = source.get("sha256")
        if label and expected and (label, expected) not in checked_sources:
            path = Path(label)
            actual = sha256_file(path) if path.is_file() else None
            if actual != expected:
                mismatches.append({"path": label, "expected": expected, "actual": actual})
    source_digest = sha256_json([
        {
            "case_id": row["case_id"],
            "prompt": row["extracted_prompt_sha256"],
            "xml": row["source_files"]["cached_xml_sha256"],
            "validator": row["source_files"]["validator_sha256"],
            "mirror_manifest": row["mirror"]["manifest_sha256"],
        }
        for row in registry["cases"]
    ])
    if source_digest != freeze.get("case_source_digest"):
        mismatches.append({
            "path": "case_source_digest",
            "expected": freeze.get("case_source_digest"),
            "actual": source_digest,
        })
    return {
        "schema_version": "e2-structure-v1-freeze-verification-1",
        "verified": not mismatches,
        "checked_source_files": len(checked_sources),
        "mismatches": mismatches,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build E2-structure-v1 case and preflight manifests")
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs" / "supplementary" / "e2_structure_v1.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "output" / "e2_structure_v1_20260920",
    )
    parser.add_argument(
        "--verify-freeze",
        action="store_true",
        help="verify the existing freeze manifest without rebuilding generated files",
    )
    args = parser.parse_args()
    if args.verify_freeze:
        result = verify_freeze(args.output.resolve())
        print(json.dumps(result, ensure_ascii=False))
        if not result["verified"]:
            raise SystemExit(1)
        return
    freeze = write_outputs(args.config.resolve(), args.output.resolve())
    print(json.dumps({
        "output": str(args.output.resolve()),
        "formal_run_allowed": freeze["formal_run_allowed"],
        "case_registry_sha256": freeze["case_registry_sha256"],
        "preflight_report_sha256": freeze["preflight_report_sha256"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
