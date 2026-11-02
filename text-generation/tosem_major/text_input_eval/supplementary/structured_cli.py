"""CLI for planning and running the frozen E2-structure-v1 protocol."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .structured_environment import AndroidCommandEnvironment, HybridEnvironment, StringPredicateEnvironment
from .structured_gateway import StructuredGateway
from .structured_preflight import REPO_ROOT, read_json, sha256_file, verify_freeze
from .structured_runner import StructuredRunner
from .structured_storage import StructuredLedger, digest

DEFAULT_CONFIG = REPO_ROOT / "configs" / "supplementary" / "e2_structure_v1.json"
DEFAULT_PREFLIGHT = REPO_ROOT / "output" / "e2_structure_v1_20260920"


def _resolve(base: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def load_oracles(path: Path) -> dict[str, dict[str, Any]]:
    value = read_json(path)
    rows = value.get("cases", value) if isinstance(value, dict) else value
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list):
        raise ValueError("Oracle manifest must contain a cases list or map")
    output = {str(row["case_id"]): row for row in rows}
    if len(output) != len(rows):
        raise ValueError("duplicate case_id in Oracle manifest")
    return output


def validate_oracles(
    registry: dict[str, Any],
    oracles: dict[str, dict[str, Any]],
    manual_binding_cases: set[str] | None = None,
) -> None:
    expected = {
        row["case_id"] for row in registry["cases"]
        if row.get("formal_applicability", {}).get("in_scope", True)
    }
    excluded = {
        row["case_id"] for row in registry["cases"]
        if row.get("formal_applicability", {}).get("in_scope") is False
    }
    if expected & excluded or expected | excluded != {
        row["case_id"] for row in registry["cases"]
    }:
        raise ValueError("registry applicability partition is incomplete")
    if set(oracles) != expected:
        raise ValueError(
            f"Oracle universe mismatch: missing={sorted(expected-set(oracles))}, extra={sorted(set(oracles)-expected)}"
        )
    for case in registry["cases"]:
        if case["case_id"] in excluded:
            applicability = case.get("formal_applicability", {})
            if (
                applicability.get("disposition") != "not_applicable"
                or applicability.get("replacement_oracle") is not False
                or len(applicability.get("evidence", [])) < 2
            ):
                raise ValueError(f"formal exclusion is incomplete for {case['case_id']}")
            continue
        oracle = oracles[case["case_id"]]
        if oracle.get("task") != case["task"] or not oracle.get("version"):
            raise ValueError(f"Oracle task/version mismatch for {case['case_id']}")
        if case["task"] == "invalid_bug":
            predicate = oracle.get("predicate", {})
            if oracle.get("oracle_kind") != "string_predicate":
                raise ValueError(f"Bug Oracle kind is not string_predicate for {case['case_id']}")
            if predicate.get("validator_path") != case["source_files"]["validator"]:
                raise ValueError(f"Bug validator path differs from the frozen case for {case['case_id']}")
            if predicate.get("validator_sha256") != case["source_files"]["validator_sha256"]:
                raise ValueError(f"Bug validator hash differs from the frozen case for {case['case_id']}")
            if predicate.get("required_resource_ids") != case["required_resource_ids"]:
                raise ValueError(f"Bug predicate keys differ from required inputs for {case['case_id']}")
            if predicate.get("true_means") != "bug_revealed" or predicate.get("control_pair_verified") is not True:
                raise ValueError(f"Bug predicate semantics/controls are incomplete for {case['case_id']}")
            continue
        calibration = oracle.get("calibration", {})
        if set(oracle.get("binding", {})) != set(case["required_resource_ids"]):
            raise ValueError(f"Oracle binding keys differ from required inputs for {case['case_id']}")
        if not all(calibration.get(key) is True for key in (
            "version_alignment_verified", "reset_verified", "recovery_verified",
        )):
            raise ValueError(f"Oracle calibration is incomplete for {case['case_id']}")
        if not all(isinstance(calibration.get(key), str) and calibration[key] for key in (
            "positive_control", "negative_control",
        )):
            raise ValueError(f"Oracle controls are missing for {case['case_id']}")
        begin = oracle.get("begin_attempt", {})
        recovery = oracle.get("recover_turn", {})
        if not begin.get("reset_command") or begin.get("remote_baseline") not in {"verified", "not_applicable"}:
            raise ValueError(f"Oracle baseline reset is incomplete for {case['case_id']}")
        if not recovery.get("command") or any(recovery.get(key) is not False for key in (
            "snapshot_loaded", "remote_reset", "renavigated_from_baseline",
        )):
            raise ValueError(f"Oracle recovery reset boundary is invalid for {case['case_id']}")
        if recovery.get("natural_state_preserved") is not True or recovery.get("dynamic_relocation_required") is not True:
            raise ValueError(f"Oracle recovery continuity is invalid for {case['case_id']}")
        clear_targets = recovery.get("input_clear_assertion", {}).get("targets", [])
        clear_selectors = {
            json.dumps(target.get("selector"), sort_keys=True)
            for target in clear_targets
            if target.get("exists") is True
            and (
                (
                    target.get("attribute") == "text"
                    and isinstance(target.get("equals"), str)
                )
                or (
                    target.get("attribute") == "text_equals_hint"
                    and target.get("equals") is True
                )
            )
        }
        binding_selectors = {
            json.dumps(selector, sort_keys=True) for selector in oracle.get("binding", {}).values()
        }
        if not binding_selectors or not binding_selectors.issubset(clear_selectors):
            raise ValueError(f"Oracle recovery does not prove cleared input for {case['case_id']}")
        if case["case_id"] in (manual_binding_cases or set()):
            if (
                oracle.get("binding_review_resolved") is not True
                and case.get("protocol_readiness", {}).get(
                    "binding_review_resolution_verified"
                ) is not True
            ):
                raise ValueError(f"manual binding review is unresolved for {case['case_id']}")


def load_inputs(config_path: Path, preflight_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path | None]:
    config = read_json(config_path)
    registry = read_json(preflight_dir / "case_registry.json")
    preflight = read_json(preflight_dir / "preflight_report.json")
    oracle_path = _resolve(config_path.parent, config.get("runtime", {}).get("oracle_manifest"))
    return config, registry, preflight, oracle_path


def assert_formal_gate(config_path: Path, preflight_dir: Path, preflight: dict[str, Any]) -> None:
    verification = verify_freeze(preflight_dir)
    if not verification["verified"]:
        raise RuntimeError(f"freeze verification failed: {verification['mismatches']}")
    if preflight.get("formal_run_allowed") is not True:
        raise RuntimeError(f"formal run is blocked: {preflight.get('blocking_gates', [])}")
    config = read_json(config_path)
    if config.get("formal_run_enabled") is not True:
        raise RuntimeError("formal_run_enabled is not true")


def _selection(args: argparse.Namespace) -> tuple[set[str] | None, set[str] | None, set[int] | None]:
    return (
        set(args.case) if args.case else None,
        set(args.model) if args.model else None,
        set(args.replicate) if args.replicate else None,
    )


def command_plan(args: argparse.Namespace) -> None:
    config, registry, preflight, oracle_path = load_inputs(args.config, args.preflight_dir)
    runner = StructuredRunner.__new__(StructuredRunner)
    runner.config = config
    runner.registry = registry
    cases, models, replicates = _selection(args)
    blocks = runner.plan(case_ids=cases, model_ids=models, replicates=replicates)
    arm_count = len(config["arms"])
    print(json.dumps({
        "protocol_id": config["protocol_id"],
        "formal_run_allowed": preflight.get("formal_run_allowed"),
        "blocking_gates": preflight.get("blocking_gates", []),
        "oracle_manifest": str(oracle_path) if oracle_path else None,
        "paired_blocks": len(blocks),
        "arm_trajectories": len(blocks) * arm_count,
        "execution_cap": args.max_blocks,
        "smoke_turn_cap": args.smoke_turn_cap,
        "schedule_sha256": digest(blocks),
    }, ensure_ascii=False, indent=2))


def command_run(args: argparse.Namespace) -> None:
    config, registry, preflight, oracle_path = load_inputs(args.config, args.preflight_dir)
    assert_formal_gate(args.config, args.preflight_dir, preflight)
    if oracle_path is None or not oracle_path.is_file():
        raise RuntimeError("runtime.oracle_manifest is not a readable frozen file")
    oracles = load_oracles(oracle_path)
    validate_oracles(registry, oracles, set(config.get("manual_binding_review_cases", [])))
    token_env = config["runtime"]["api_token_env"]
    token = os.environ.get(token_env, "").strip()
    if not token:
        raise RuntimeError(f"environment variable {token_env} is empty")
    cases, models, replicates = _selection(args)
    if args.smoke_turn_cap is not None and args.smoke_turn_cap < 1:
        raise ValueError("--smoke-turn-cap must be a positive integer")
    args.run_dir.mkdir(parents=True, exist_ok=True)
    freeze_path = args.preflight_dir / "freeze_manifest.json"
    selection = {
        "cases": sorted(cases) if cases else "all",
        "models": sorted(models) if models else "all",
        "replicates": sorted(replicates) if replicates else "all",
    }
    manifest = {
        "schema_version": "e2-structure-v1-run-manifest-1",
        "protocol_id": config["protocol_id"],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_sha256": sha256_file(args.config),
        "freeze_manifest_sha256": sha256_file(freeze_path),
        "oracle_manifest_sha256": sha256_file(oracle_path),
        "selection": selection,
        "execution_overrides": {
            "smoke_turn_cap": args.smoke_turn_cap,
        },
    }
    manifest_path = args.run_dir / "run_manifest.json"
    if manifest_path.exists():
        old = read_json(manifest_path)
        old_without_time = {key: value for key, value in old.items() if key != "created_at_utc"}
        new_without_time = {key: value for key, value in manifest.items() if key != "created_at_utc"}
        if old_without_time != new_without_time:
            raise ValueError("run manifest drift; use a new run directory")
        manifest = old
    else:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ledger = StructuredLedger(args.run_dir / "ledger.sqlite3", manifest)
    gateway = StructuredGateway(
        config["runtime"]["api_url"],
        token,
        timeout_seconds=float(config["runtime"]["api_timeout_seconds"]),
        verify_ssl=bool(config["runtime"].get("verify_ssl", False)),
    )
    environment = HybridEnvironment(
        AndroidCommandEnvironment(
            config["runtime"]["environment_command"],
            timeout_seconds=float(config["runtime"]["environment_timeout_seconds"]),
        ),
        StringPredicateEnvironment(REPO_ROOT),
    )
    runner = StructuredRunner(
        repo_root=REPO_ROOT,
        output_dir=args.run_dir,
        config=config,
        registry=registry,
        oracles=oracles,
        ledger=ledger,
        gateway=gateway,
        environment=environment,
    )
    try:
        summary = runner.run(
            case_ids=cases,
            model_ids=models,
            replicates=replicates,
            max_blocks=args.max_blocks,
            turn_cap=args.smoke_turn_cap,
        )
    finally:
        ledger.close()
    summary_path = args.run_dir / "last_run_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


def command_report(args: argparse.Namespace) -> None:
    path = args.run_dir / "ledger.sqlite3"
    if not path.is_file():
        raise FileNotFoundError(path)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    states = dict(db.execute("SELECT state,COUNT(*) FROM trajectories GROUP BY state").fetchall())
    statuses: Counter[str] = Counter()
    for row in db.execute("SELECT result FROM trajectories WHERE state='done'"):
        result = json.loads(row[0])
        statuses[result.get("status", "missing")] += 1
    uncertain = [dict(row) for row in db.execute(
        "SELECT id,kind,created_at FROM external_calls WHERE state='reserved' ORDER BY created_at"
    )]
    db.close()
    print(json.dumps({
        "trajectory_states": states,
        "completed_statuses": dict(statuses),
        "uncertain_external_calls": uncertain,
    }, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="E2-structure-v1 formal execution CLI")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--preflight-dir", type=Path, default=DEFAULT_PREFLIGHT)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        child = sub.add_parser(name)
        child.add_argument("--case", action="append")
        child.add_argument("--model", action="append")
        child.add_argument("--replicate", action="append", type=int)
        child.add_argument("--max-blocks", type=int)
        child.add_argument(
            "--smoke-turn-cap",
            type=int,
            help="cap turns per trajectory for smoke only; the frozen formal budget is unchanged",
        )
    run = sub.choices["run"]
    run.add_argument("--run-dir", type=Path, required=True)
    report = sub.add_parser("report")
    report.add_argument("--run-dir", type=Path, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.config = args.config.resolve()
    args.preflight_dir = args.preflight_dir.resolve()
    if hasattr(args, "run_dir"):
        args.run_dir = args.run_dir.resolve()
    try:
        if args.command == "plan":
            command_plan(args)
        elif args.command == "run":
            command_run(args)
        else:
            command_report(args)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        sys.stderr.write(json.dumps({
            "status": "blocked",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }, ensure_ascii=False) + "\n")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
