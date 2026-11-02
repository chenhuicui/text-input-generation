"""Fail-closed offline preflight for the no-replicate E2-abcdef-v2 experiment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .abcdef_contract import ARMS, LEGACY_PROTOCOL_IDS, PROTOCOL_ID, canonical_json
from .structured_cli import load_oracles, validate_oracles


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO_ROOT / "configs" / "supplementary" / "e2_abcdef_v1.json"
DEFAULT_OUTPUT = REPO_ROOT / "output" / "e2_abcdef_v1_20260924"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def qualification_config_sha256(
    config: dict[str, Any], *, protocol_id: str | None = None,
) -> str:
    """Bind the analyzer probe only to fields that can alter its live route."""
    runtime = config.get("runtime", {})
    stable = {
        "protocol_id": protocol_id or config.get("protocol_id"),
        "analyzer": config.get("analyzer"),
        "runtime_route": {
            key: runtime.get(key)
            for key in ("api_url", "api_token_env", "api_timeout_seconds", "verify_ssl")
        },
    }
    return hashlib.sha256(canonical_json(stable).encode("utf-8")).hexdigest()


def resolve_path(config_path: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else (config_path.parent / path).resolve()


def load_bug_feedback_oracles(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    value = read_json(path)
    rows = value.get("cases", value) if isinstance(value, dict) else value
    if not isinstance(rows, list):
        raise ValueError("Bug feedback contract manifest must contain a cases list")
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
            raise ValueError("Bug feedback contract row lacks case_id")
        oracle = row.get("feedback_oracle", row.get("oracle"))
        if not isinstance(oracle, dict):
            raise ValueError(f"Bug feedback contract lacks feedback_oracle: {row['case_id']}")
        if row["case_id"] in output:
            raise ValueError(f"duplicate Bug feedback contract: {row['case_id']}")
        output[row["case_id"]] = oracle
    return output


def _gate(gates: list[dict[str, Any]], gate_id: str, passed: bool, detail: Any) -> None:
    gates.append({"id": gate_id, "passed": bool(passed), "detail": detail})


def _run_offline_tests() -> dict[str, Any]:
    tests_dir = REPO_ROOT / "tosem_major" / "text_input_eval" / "tests"
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    pythonpath = str(REPO_ROOT / "tosem_major")
    environment["PYTHONPATH"] = (
        pythonpath + os.pathsep + environment["PYTHONPATH"]
        if environment.get("PYTHONPATH") else pythonpath
    )
    completed = subprocess.run(
        [
            sys.executable, "-m", "unittest", "discover",
            "-s", str(tests_dir), "-p", "test_abcdef_*.py",
        ],
        cwd=REPO_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
        check=False,
    )
    combined = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    return {
        "passed": completed.returncode == 0,
        "returncode": completed.returncode,
        "output": combined[-8000:],
    }


def build_preflight(config_path: Path, *, run_tests: bool = True) -> dict[str, Any]:
    config_path = config_path.resolve()
    config = read_json(config_path)
    if config.get("protocol_id") != PROTOCOL_ID:
        raise ValueError("preflight requires E2-abcdef-v2")
    runtime = config.get("runtime", {})
    registry_path = resolve_path(config_path, runtime.get("case_registry"))
    oracle_path = resolve_path(config_path, runtime.get("oracle_manifest"))
    exclusion_path = resolve_path(config_path, runtime.get("formal_exclusions"))
    if registry_path is None or oracle_path is None:
        raise ValueError("case registry and Oracle manifest paths are required")
    registry = read_json(registry_path)
    oracles = load_oracles(oracle_path)
    validate_oracles(registry, oracles)
    cases = registry.get("cases", [])
    page_ids = {
        row["case_id"] for row in cases
        if row.get("task") == "valid_pptr" and row.get("formal_applicability", {}).get("in_scope") is True
    }
    bug_ids = {
        row["case_id"] for row in cases
        if row.get("task") == "invalid_bug" and row.get("formal_applicability", {}).get("in_scope") is True
    }
    excluded_ids = {
        row["case_id"] for row in cases
        if row.get("formal_applicability", {}).get("in_scope") is False
    }
    gates: list[dict[str, Any]] = []
    expected_excluded = set(config["applicability"]["not_applicable_case_ids"])
    _gate(
        gates,
        "registry_oracle_partition",
        len(cases) == 152
        and len(page_ids) == 112
        and len(bug_ids) == 37
        and excluded_ids == expected_excluded
        and set(oracles) == page_ids | bug_ids,
        {
            "registry": len(cases), "page_behavior_oracles": len(page_ids),
            "bug_predicate_oracles": len(bug_ids), "excluded": sorted(excluded_ids),
        },
    )
    _gate(
        gates,
        "abcdef_arm_contract",
        tuple(config.get("arms", {})) == ARMS
        and "replicates" not in config
        and config.get("experimental_unit", {}).get("trajectory_axes")
        == ["model", "case_id", "arm"]
        and config.get("experimental_unit", {}).get("one_trajectory_per_axis_cell") is True
        and config.get("experimental_unit", {}).get("replicate_axis") is False
        and config.get("experimental_unit", {}).get("semantic_success_early_stop") is True
        and config.get("budgets_including_first_turn")
        == {"valid_pptr": 3, "invalid_bug": 30}
        and config.get("feedback", {}).get("candidate_history_excludes_outcome_in_b") is True
        and config.get("oracle_policy", {}).get("same_oracle_across_all_arms") is True
        and config.get("oracle_policy", {}).get("feedback_cannot_change_verdict") is True,
        config.get("arms"),
    )
    text_fields = (
        "gateway_verified", "revision_verified", "generation_defaults_verified",
        "capacity_verified", "fresh_session_isolation_verified",
    )
    unqualified_text = [
        model["id"] for model in config.get("models", [])
        if any(model.get(field) is not True for field in text_fields)
        or not isinstance(model.get("max_prompt_bytes"), int)
    ]
    _gate(gates, "subject_text_gateway_qualification", not unqualified_text, unqualified_text)
    unqualified_images = [
        model["id"] for model in config.get("models", [])
        if model.get("feedback_image_verified") is not True
        or not isinstance(model.get("max_feedback_image_bytes"), int)
    ]
    _gate(gates, "subject_feedback_image_qualification", not unqualified_images, unqualified_images)
    capture = config.get("capture_protocol", {})
    capture_frozen = (
        capture.get("pre_roll_seconds") == 0.5
        and capture.get("burst_seconds") == [0.0, 0.25, 0.5, 1.0, 2.0]
        and capture.get("tail_interval_seconds") == 1.0
        and capture.get("record_video") is True
        and capture.get("contact_sheet_roles") == [
            "before", "first_changed", "transient_message_peak", "settled"
        ]
    )
    _gate(gates, "capture_timing_contract", capture_frozen, capture)
    smoke_path = resolve_path(config_path, runtime.get("page_visual_capture_smoke_report"))
    smoke_ids: set[str] = set()
    if smoke_path is not None and smoke_path.is_file():
        smoke = read_json(smoke_path)
        if smoke.get("protocol_id") in {PROTOCOL_ID, *LEGACY_PROTOCOL_IDS} and smoke.get("status") == "passed":
            smoke_ids = set(smoke.get("passed_case_ids", []))
    _gate(
        gates, "page_visual_capture_smoke",
        page_ids.issubset(smoke_ids),
        {"required": len(page_ids), "passed": len(page_ids & smoke_ids), "report": str(smoke_path) if smoke_path else None},
    )
    bug_contract_path = resolve_path(config_path, runtime.get("bug_visual_feedback_contracts"))
    bug_contracts = load_bug_feedback_oracles(bug_contract_path)
    valid_bug_contracts = {
        case_id for case_id, oracle in bug_contracts.items()
        if oracle.get("case_id") == case_id
        and oracle.get("oracle_role") == "feedback_observation_only"
        and isinstance(oracle.get("version"), str)
        and all(key in oracle for key in (
            "binding", "begin_attempt", "execute_turn", "recover_turn", "goal_assertion"
        ))
    }
    _gate(
        gates, "bug_app_observation_contracts",
        valid_bug_contracts == bug_ids,
        {
            "required": len(bug_ids), "valid": len(valid_bug_contracts & bug_ids),
            "missing": sorted(bug_ids - valid_bug_contracts),
            "extra": sorted(valid_bug_contracts - bug_ids),
            "manifest": str(bug_contract_path) if bug_contract_path else None,
            "note": "These contracts supply equal App observations to A-F; string predicates remain the verdict Oracles.",
        },
    )
    bug_smoke_path = resolve_path(config_path, runtime.get("bug_visual_capture_smoke_report"))
    bug_smoke_ids: set[str] = set()
    if bug_smoke_path is not None and bug_smoke_path.is_file():
        bug_smoke = read_json(bug_smoke_path)
        if bug_smoke.get("protocol_id") in {PROTOCOL_ID, *LEGACY_PROTOCOL_IDS} and bug_smoke.get("status") == "passed":
            bug_smoke_ids = set(bug_smoke.get("passed_case_ids", []))
    _gate(
        gates, "bug_visual_capture_smoke",
        bug_ids.issubset(bug_smoke_ids),
        {
            "required": len(bug_ids), "passed": len(bug_ids & bug_smoke_ids),
            "report": str(bug_smoke_path) if bug_smoke_path else None,
            "verdict_oracle": "frozen_string_predicate",
        },
    )
    analyzer = config.get("analyzer", {})
    analyzer_access_path = resolve_path(config_path, analyzer.get("access_preflight_report"))
    analyzer_access = (
        read_json(analyzer_access_path)
        if analyzer_access_path is not None and analyzer_access_path.is_file()
        else None
    )
    analyzer_static = (
        analyzer.get("provider") == "must_chat_completions_gateway"
        and analyzer.get("model") == "gpt-5.6-sol"
        and analyzer.get("reasoning_effort") == "medium"
        and analyzer.get("api_url") == runtime.get("api_url")
        and analyzer.get("api_key_env") == runtime.get("api_token_env")
        and analyzer.get("session_field") == "sessionId"
        and analyzer.get("fresh_session_value") == ""
        and analyzer.get("image_field") == "imageUrl"
        and analyzer.get("reasoning_effort_field") == "reasoningEffort"
        and analyzer.get("schema_enforcement") == "prompt_plus_fail_closed_local_validation"
        and analyzer.get("resolved_reasoning_effort_returned") is False
        and analyzer.get("strict_json_schema") == "execution_diagnosis_v1"
        and analyzer.get("advice_output") is False
        and analyzer.get("outcome_override") is False
    )
    _gate(gates, "f_analyzer_static_contract", analyzer_static, analyzer)
    qualification = (
        analyzer_access.get("qualification", {})
        if isinstance(analyzer_access, dict) else {}
    )
    request_contract = (
        analyzer_access.get("request_contract", {})
        if isinstance(analyzer_access, dict) else {}
    )
    formal_gate = (
        analyzer_access.get("formal_gate", {})
        if isinstance(analyzer_access, dict) else {}
    )
    qualification_hash_configs = [config]
    # Relocating the same signed qualification report beside a generated
    # device-shard config does not change the analyzer route. Preserve the
    # original configured path spelling when checking legacy report hashes.
    if config_path != DEFAULT_CONFIG.resolve() and DEFAULT_CONFIG.is_file():
        default_config = read_json(DEFAULT_CONFIG)
        normalized = {
            **config,
            "analyzer": {
                **config.get("analyzer", {}),
                "access_preflight_report": default_config.get("analyzer", {}).get(
                    "access_preflight_report"
                ),
            },
        }
        qualification_hash_configs.append(normalized)
    accepted_qualification_hashes = {
        qualification_config_sha256(value, protocol_id=protocol_id)
        for value in qualification_hash_configs
        for protocol_id in {PROTOCOL_ID, *LEGACY_PROTOCOL_IDS}
    }
    analyzer_report_verified = (
        isinstance(analyzer_access, dict)
        and analyzer_access.get("schema_version")
        == "e2-abcdef-v1-f-analyzer-gateway-qualification-1"
        and analyzer_access.get("protocol_id") in {PROTOCOL_ID, *LEGACY_PROTOCOL_IDS}
        and analyzer_access.get("qualification_config_sha256") in accepted_qualification_hashes
        and analyzer_access.get("endpoint") == analyzer.get("api_url")
        and analyzer_access.get("api_token_env") == analyzer.get("api_key_env")
        and analyzer_access.get("token_recorded") is False
        and request_contract.get("model") == analyzer.get("model")
        and request_contract.get("session_field") == analyzer.get("session_field")
        and request_contract.get("fresh_session_value") == analyzer.get("fresh_session_value")
        and request_contract.get("image_field") == analyzer.get("image_field")
        and request_contract.get("reasoning_effort_field")
        == analyzer.get("reasoning_effort_field")
        and request_contract.get("reasoning_effort") == analyzer.get("reasoning_effort")
        and request_contract.get("schema_enforcement") == analyzer.get("schema_enforcement")
        and request_contract.get("resolved_reasoning_effort_returned") is False
        and qualification.get("status") == "passed"
        and qualification.get("run_status") == "ok"
        and qualification.get("response_model") == analyzer.get("model")
        and qualification.get("response_id_present") is True
        and qualification.get("session_id_present") is True
        and qualification.get("execution_diagnosis_v1_valid") is True
        and qualification.get("execution_status_preserved") is True
        and qualification.get("recommendation_is_null") is True
        and qualification.get("evidence_refs_subset_of_supplied_refs") is True
        and formal_gate.get("api_verified") is True
        and analyzer_access.get("formal_experiment_calls") == 0
        and analyzer_access.get("probe_calls") == 1
    )
    _gate(
        gates, "f_analyzer_api_qualification",
        analyzer.get("api_verified") is True and analyzer_report_verified,
        {
            "model": analyzer.get("model"),
            "reasoning_effort": analyzer.get("reasoning_effort"),
            "api_verified": analyzer.get("api_verified"),
            "qualification_report_verified": analyzer_report_verified,
            "access_preflight_report": str(analyzer_access_path) if analyzer_access_path else None,
            "gateway_endpoint": analyzer.get("api_url"),
            "response_model": qualification.get("response_model"),
            "image_route_status": qualification.get("status"),
            "schema_valid": qualification.get("execution_diagnosis_v1_valid"),
            "reasoning_effort_audit": formal_gate.get("reasoning_effort_audit"),
        },
    )
    storage = config.get("storage", {})
    storage_report_path = resolve_path(config_path, storage.get("qualification_report"))
    storage_report = (
        read_json(storage_report_path)
        if storage_report_path is not None and storage_report_path.is_file()
        else {}
    )
    mysql_storage_static = (
        storage.get("local_primary") == "append_only_sqlite_and_evidence_files"
        and storage.get("mysql_mirror_required_for_formal_run") is True
        and storage.get("database_env") == "MYSQL_DATABASE"
        and storage.get("host_env") == "MYSQL_HOST"
        and storage.get("port_env") == "MYSQL_PORT"
        and storage.get("user_env") == "MYSQL_USER"
        and storage.get("password_env") == "MYSQL_PASSWORD"
        and storage.get("target_database") == "must_lab_db"
        and storage.get("table_prefix") == "e2_abcdef_v2_"
        and storage.get("legacy_tables_modified") is False
        and storage.get("media_storage") == "filesystem_paths_and_sha256_in_mysql"
        and storage.get("sync_after_formal_run") is True
    )
    mysql_storage_qualified = (
        mysql_storage_static
        and storage_report.get("schema_version") == "e2-abcdef-v2-mysql-qualification-1"
        and storage_report.get("protocol_id") == PROTOCOL_ID
        and storage_report.get("status") == "passed"
        and storage_report.get("legacy_tables_modified") is False
        and storage_report.get("complete_prompt_coverage") == 1.0
        and storage_report.get("complete_answer_coverage") == 1.0
        and storage_report.get("provider_envelope_coverage") == 1.0
        and storage_report.get("row_count_match") is True
        and storage_report.get("database_name") == storage.get("target_database")
        and storage_report.get("database_match") is True
    )
    sqlite_deferred_static = (
        storage.get("local_primary") == "append_only_sqlite_and_evidence_files"
        and storage.get("mysql_mirror_required_for_formal_run") is False
        and storage.get("sync_after_formal_run") is False
        and storage.get("mysql_mirror_mode") == "deferred_until_model_completion"
        and storage.get("local_acceptance_report") == "sqlite_audit_report.json"
        and storage.get("sqlite_integrity_check") == "quick_check"
        and storage.get("media_storage")
        == "filesystem_evidence_with_paths_and_sha256_in_sqlite_events"
        and storage.get("legacy_tables_modified") is False
        and int(storage.get("minimum_free_bytes") or 0) >= 20 * 1024 ** 3
    )
    storage_qualified = mysql_storage_qualified or sqlite_deferred_static
    storage_mode = (
        "mysql_synchronous_mirror" if mysql_storage_qualified
        else "sqlite_primary_mysql_deferred" if sqlite_deferred_static
        else "invalid"
    )
    _gate(
        gates, "analysis_storage_qualification", storage_qualified,
        {
            "mode": storage_mode,
            "static_contract_valid": mysql_storage_static or sqlite_deferred_static,
            "mysql_required_for_formal_run": storage.get(
                "mysql_mirror_required_for_formal_run"
            ),
            "sync_after_formal_run": storage.get("sync_after_formal_run"),
            "qualification_report": str(storage_report_path) if storage_report_path else None,
            "qualification_status": storage_report.get("status"),
            "target_database": storage.get("target_database"),
            "database_name": storage_report.get("database_name"),
            "database_match": storage_report.get("database_match"),
            "complete_prompt_coverage": storage_report.get("complete_prompt_coverage"),
            "complete_answer_coverage": storage_report.get("complete_answer_coverage"),
            "provider_envelope_coverage": storage_report.get("provider_envelope_coverage"),
            "row_count_match": storage_report.get("row_count_match"),
        },
    )
    tests = _run_offline_tests() if run_tests else {
        "passed": False, "returncode": None, "output": "offline tests were not run"
    }
    _gate(gates, "offline_abcdef_tests", tests["passed"], tests)
    _gate(
        gates, "formal_run_switch",
        config.get("formal_run_enabled") is True,
        {"formal_run_enabled": config.get("formal_run_enabled")},
    )
    blocking = [gate["id"] for gate in gates if not gate["passed"]]
    inputs = [config_path, registry_path, oracle_path]
    if exclusion_path is not None and exclusion_path.is_file():
        inputs.append(exclusion_path)
    if smoke_path is not None and smoke_path.is_file():
        inputs.append(smoke_path)
    if bug_contract_path is not None and bug_contract_path.is_file():
        inputs.append(bug_contract_path)
    if bug_smoke_path is not None and bug_smoke_path.is_file():
        inputs.append(bug_smoke_path)
    if analyzer_access_path is not None and analyzer_access_path.is_file():
        inputs.append(analyzer_access_path)
    if storage_report_path is not None and storage_report_path.is_file():
        inputs.append(storage_report_path)
    mysql_schema_path = (
        REPO_ROOT / "tosem_major" / "text_input_eval" / "supplementary"
        / "sql" / "e2_abcdef_v2_mysql.sql"
    )
    if mysql_schema_path.is_file() and storage_mode == "mysql_synchronous_mirror":
        inputs.append(mysql_schema_path)
    inputs.extend(sorted(
        (REPO_ROOT / "tosem_major" / "text_input_eval" / "supplementary").glob("abcdef_*.py")
    ))
    inputs.extend(sorted(
        (REPO_ROOT / "tosem_major" / "text_input_eval" / "tests").glob("test_abcdef_*.py")
    ))
    return {
        "schema_version": "e2-abcdef-v2-preflight-1",
        "protocol_id": PROTOCOL_ID,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "formal_run_allowed": not blocking,
        "blocking_gates": blocking,
        "gates": gates,
        "counts": {
            "registry": len(cases), "formal_pages": len(page_ids),
            "formal_bugs": len(bug_ids), "not_applicable": len(excluded_ids),
            "case_blocks_per_model": len(page_ids) + len(bug_ids),
            "arm_trajectories_per_model": (len(page_ids) + len(bug_ids)) * len(ARMS),
            "case_blocks_all_models": (len(page_ids) + len(bug_ids)) * len(config["models"]),
            "arm_trajectories_all_models": (len(page_ids) + len(bug_ids)) * len(config["models"]) * len(ARMS),
        },
        "input_sha256": {str(path): sha256_file(path) for path in inputs},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="E2-abcdef-v2 no-replicate offline preflight")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()
    report = build_preflight(args.config, run_tests=not args.skip_tests)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "preflight_report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
