"""Plan, preflight, run, and report the no-replicate E2-abcdef-v2 protocol."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .abcdef_analyzer import MUSTGatewayAnalyzer
from .abcdef_environment import ABCDEFHybridEnvironment
from .abcdef_gateway import ABCDEFSubjectGateway
from .abcdef_preflight import (
    DEFAULT_CONFIG,
    DEFAULT_OUTPUT,
    REPO_ROOT,
    build_preflight,
    load_bug_feedback_oracles,
    read_json,
    resolve_path,
    sha256_file,
)
from .abcdef_runner import ABCDEFRunner
from .structured_cli import load_oracles, validate_oracles
from .structured_environment import AndroidCommandEnvironment, StringPredicateEnvironment
from .structured_storage import StructuredLedger, digest


def _selection(args: argparse.Namespace) -> tuple[set[str] | None, set[str] | None]:
    return (
        set(args.case) if args.case else None,
        set(args.model) if args.model else None,
    )


def _arm_selection(args: argparse.Namespace) -> set[str] | None:
    values = getattr(args, "arm", None)
    return set(values) if values else None


def _nonformal_smoke_blocking(report: dict[str, Any]) -> set[str]:
    """Return gates that must pass before the qualification smoke can run.

    The smoke is the input used to qualify the MySQL mirror, so requiring an
    existing MySQL qualification here would make the qualification workflow
    circular.  Both gates remain mandatory for a formal run.
    """
    blocking = set(report["blocking_gates"])
    blocking.discard("formal_run_switch")
    blocking.discard("mysql_analysis_mirror_qualification")
    blocking.discard("analysis_storage_qualification")
    return blocking


def _load(config_path: Path) -> tuple[dict[str, Any], dict[str, Any], Path, Path, dict[str, dict[str, Any]]]:
    config = read_json(config_path)
    registry_path = resolve_path(config_path, config["runtime"]["case_registry"])
    oracle_path = resolve_path(config_path, config["runtime"]["oracle_manifest"])
    bug_contract_path = resolve_path(config_path, config["runtime"].get("bug_visual_feedback_contracts"))
    if registry_path is None or oracle_path is None:
        raise ValueError("registry and Oracle paths are required")
    registry = read_json(registry_path)
    oracles = load_oracles(oracle_path)
    validate_oracles(registry, oracles)
    return config, registry, registry_path, oracle_path, load_bug_feedback_oracles(bug_contract_path)


def _audit_nonformal_smoke(run_dir: Path) -> dict[str, Any]:
    """Fail closed unless one saved paired block exercised the complete A-F chain."""
    ledger_path = run_dir / "ledger.sqlite3"
    db = sqlite3.connect(ledger_path)
    db.row_factory = sqlite3.Row
    try:
        trajectories = []
        for row in db.execute(
            "SELECT id,dimensions,state,events,result FROM trajectories ORDER BY id"
        ):
            trajectories.append({
                "id": row["id"],
                "dimensions": json.loads(row["dimensions"]),
                "state": row["state"],
                "events": json.loads(row["events"]),
                "result": json.loads(row["result"]) if row["result"] else None,
            })
        reserved_calls = [
            row[0] for row in db.execute(
                "SELECT id FROM external_calls WHERE state='reserved' ORDER BY id"
            )
        ]
    finally:
        db.close()

    rows_by_arm = {
        row["dimensions"].get("arm"): row
        for row in trajectories
        if row["dimensions"].get("arm") in set("ABCDEF")
    }
    block_dimensions = {
        digest({key: value for key, value in row["dimensions"].items() if key != "arm"})
        for row in trajectories
    }

    def feedback_requests(arm: str) -> list[dict[str, Any]]:
        row = rows_by_arm.get(arm)
        if row is None:
            return []
        return [
            event for event in row["events"]
            if event.get("event") == "model_request" and int(event.get("turn", 0)) >= 2
        ]

    def has_prompt_evidence(arm: str, *fields: str) -> bool:
        return any(
            all(event.get("prompt_audit", {}).get(field) is True for field in fields)
            and bool(event.get("attachment_sha256"))
            for event in feedback_requests(arm)
        )

    f_row = rows_by_arm.get("F")
    f_analysis_events = [] if f_row is None else [
        event for event in f_row["events"] if event.get("event") == "feedback_analysis"
    ]
    statuses = {
        arm: (row.get("result") or {}).get("status")
        for arm, row in sorted(rows_by_arm.items())
    }
    early_stopped_at_one = {
        arm: (
            (row.get("result") or {}).get("status") == "completed_success"
            and (row.get("result") or {}).get("first_success_turn") == 1
        )
        for arm, row in rows_by_arm.items()
    }
    gates = {
        "exactly_one_complete_paired_block": (
            len(trajectories) == 6
            and set(rows_by_arm) == set("ABCDEF")
            and len(block_dimensions) == 1
        ),
        "all_trajectories_done": (
            len(trajectories) == 6 and all(row["state"] == "done" for row in trajectories)
        ),
        "no_incomplete_results": (
            len(statuses) == 6
            and all(status is not None and status != "incomplete" for status in statuses.values())
        ),
        "no_reserved_external_calls": not reserved_calls,
        "all_arms_exercised_second_turn_or_early_stopped": all(
            feedback_requests(arm) or early_stopped_at_one.get(arm) for arm in "ABCDEF"
        ),
        "d_visual_feedback_visible_or_early_stopped": (
            has_prompt_evidence("D", "visual_visible") or early_stopped_at_one.get("D")
        ),
        "e_visual_and_ui_feedback_visible_or_early_stopped": (
            has_prompt_evidence("E", "visual_visible", "ui_observations_visible")
            or early_stopped_at_one.get("E")
        ),
        "f_visual_ui_and_analysis_visible_or_early_stopped": (
            has_prompt_evidence("F", "visual_visible", "ui_observations_visible", "analysis_visible")
            or early_stopped_at_one.get("F")
        ),
        "f_analyzer_succeeded_with_image_or_early_stopped": (
            any(
                event.get("run_status") == "ok" and bool(event.get("image_sha256"))
                for event in f_analysis_events
            ) or early_stopped_at_one.get("F")
        ),
    }
    failed = [name for name, passed in gates.items() if not passed]
    report = {
        "schema_version": "e2-abcdef-v2-smoke-acceptance-1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_scope": "nonformal_smoke",
        "status": "passed" if not failed else "failed",
        "gates": gates,
        "failed_gates": failed,
        "trajectory_statuses": statuses,
        "trajectory_count": len(trajectories),
        "reserved_external_calls": reserved_calls,
    }
    (run_dir / "smoke_acceptance_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def command_preflight(args: argparse.Namespace) -> None:
    report = build_preflight(args.config, run_tests=True)
    args.preflight_dir.mkdir(parents=True, exist_ok=True)
    path = args.preflight_dir / "preflight_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def command_plan(args: argparse.Namespace) -> None:
    config, registry, _, oracle_path, _ = _load(args.config)
    report = build_preflight(args.config, run_tests=True)
    runner = ABCDEFRunner.__new__(ABCDEFRunner)
    runner.config = config
    runner.registry = registry
    cases, models = _selection(args)
    arms = _arm_selection(args)
    blocks = runner.plan(case_ids=cases, model_ids=models)
    print(json.dumps({
        "protocol_id": config["protocol_id"],
        "formal_run_allowed": report["formal_run_allowed"],
        "blocking_gates": report["blocking_gates"],
        "oracle_manifest": str(oracle_path),
        "paired_blocks": len(blocks),
        "selected_arms": sorted(arms) if arms else list(config["arms"]),
        "arm_trajectories": len(blocks) * (len(arms) if arms else len(config["arms"])),
        "execution_cap": args.max_blocks,
        "smoke_turn_cap": args.smoke_turn_cap,
        "schedule_sha256": digest(blocks),
    }, ensure_ascii=False, indent=2))


def command_run(args: argparse.Namespace) -> None:
    report = build_preflight(args.config, run_tests=True)
    nonformal_smoke = bool(getattr(args, "nonformal_smoke", False))
    blocking = set(report["blocking_gates"])
    if nonformal_smoke:
        blocking = _nonformal_smoke_blocking(report)
        if blocking:
            raise RuntimeError(f"non-formal smoke is blocked: {sorted(blocking)}")
        if not (
            args.case and len(args.case) == 1
            and args.model and len(args.model) == 1
            and args.max_blocks == 1
            and args.smoke_turn_cap is not None
            and args.smoke_turn_cap >= 2
        ):
            raise ValueError(
                "non-formal smoke requires exactly one case and model, "
                "one paired block, and at least two turns"
            )
    elif report["formal_run_allowed"] is not True:
        raise RuntimeError(f"formal run is blocked: {report['blocking_gates']}")
    config, registry, registry_path, oracle_path, bug_feedback_oracles = _load(args.config)
    token_name = config["runtime"]["api_token_env"]
    analyzer_key_name = config["analyzer"]["api_key_env"]
    token = os.environ.get(token_name, "").strip()
    analyzer_key = os.environ.get(analyzer_key_name, "").strip()
    if not token:
        raise RuntimeError(f"environment variable {token_name} is empty")
    if not analyzer_key:
        raise RuntimeError(f"environment variable {analyzer_key_name} is empty")
    if not nonformal_smoke and config.get("storage", {}).get("sync_after_formal_run") is True:
        from .abcdef_mysql import require_mysql_database

        require_mysql_database(config["storage"]["target_database"])
    if args.smoke_turn_cap is not None and args.smoke_turn_cap < 1:
        raise ValueError("--smoke-turn-cap must be positive")
    cases, models = _selection(args)
    arms = _arm_selection(args)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    selection = {
        "cases": sorted(cases) if cases else "all",
        "models": sorted(models) if models else "all",
        "arms": sorted(arms) if arms else "all",
    }
    manifest = {
        "schema_version": "e2-abcdef-v2-run-manifest-1",
        "protocol_id": config["protocol_id"],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config_sha256": sha256_file(args.config),
        "registry_sha256": sha256_file(registry_path),
        "oracle_manifest_sha256": sha256_file(oracle_path),
        "preflight_input_sha256": report["input_sha256"],
        "run_scope": "nonformal_smoke" if nonformal_smoke else "formal",
        "formal_run_enabled_at_start": config.get("formal_run_enabled") is True,
        "selection": selection,
        "trajectory_identity_axes": ["model", "case_id", "arm"],
        "replicate_axis": False,
        "execution_overrides": {"smoke_turn_cap": args.smoke_turn_cap},
        "implementation_sha256": {
            "abcdef_cli.py": sha256_file(Path(__file__).resolve()),
            "abcdef_runner.py": sha256_file(Path(__file__).with_name("abcdef_runner.py")),
            "abcdef_gateway.py": sha256_file(Path(__file__).with_name("abcdef_gateway.py")),
            "abcdef_mysql.py": sha256_file(Path(__file__).with_name("abcdef_mysql.py")),
        },
    }
    manifest_path = args.run_dir / "run_manifest.json"
    if manifest_path.exists():
        old = read_json(manifest_path)
        if {k: v for k, v in old.items() if k != "created_at_utc"} != {
            k: v for k, v in manifest.items() if k != "created_at_utc"
        }:
            raise ValueError("run manifest drift; use a new run directory")
        manifest = old
    else:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ledger = StructuredLedger(args.run_dir / "ledger.sqlite3", manifest)
    gateway = ABCDEFSubjectGateway(
        config["runtime"]["api_url"], token,
        timeout_seconds=float(config["runtime"]["api_timeout_seconds"]),
        verify_ssl=bool(config["runtime"].get("verify_ssl", False)),
        max_transport_attempts=int(config["runtime"].get("transport_max_attempts", 3)),
        transport_retry_backoff_seconds=float(
            config["runtime"].get("transport_retry_backoff_seconds", 0.5)
        ),
    )
    analyzer = MUSTGatewayAnalyzer(
        analyzer_key,
        model=config["analyzer"]["model"],
        reasoning_effort=config["analyzer"]["reasoning_effort"],
        api_url=config["analyzer"]["api_url"],
        timeout_seconds=float(config["runtime"]["api_timeout_seconds"]),
        verify_ssl=bool(config["analyzer"].get("verify_ssl", False)),
        max_transport_attempts=int(config["runtime"].get("transport_max_attempts", 3)),
        transport_retry_backoff_seconds=float(
            config["runtime"].get("transport_retry_backoff_seconds", 0.5)
        ),
    )
    environment = ABCDEFHybridEnvironment(
        AndroidCommandEnvironment(
            config["runtime"]["environment_command"],
            timeout_seconds=float(config["runtime"]["environment_timeout_seconds"]),
        ),
        StringPredicateEnvironment(REPO_ROOT),
        bug_feedback_oracles,
    )
    runner = ABCDEFRunner(
        repo_root=REPO_ROOT,
        output_dir=args.run_dir,
        config=config,
        registry=registry,
        oracles=load_oracles(oracle_path),
        ledger=ledger,
        gateway=gateway,
        environment=environment,
        analyzer=analyzer,
    )
    try:
        summary = runner.run(
            case_ids=cases,
            model_ids=models,
            arm_ids=arms,
            max_blocks=args.max_blocks,
            turn_cap=args.smoke_turn_cap,
        )
    finally:
        ledger.close()
    (args.run_dir / "last_run_summary.json").write_text(
        json.dumps({
            **summary,
            "run_scope": "nonformal_smoke" if nonformal_smoke else "formal",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output = {
        **summary,
        "run_scope": "nonformal_smoke" if nonformal_smoke else "formal",
    }
    if summary.get("stop_signal") is not None:
        stop_report = {
            "schema_version": "e2-abcdef-v2-run-stop-1",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "run_scope": output["run_scope"],
            "stop_signal": summary["stop_signal"],
            "mysql_sync_attempted": False,
        }
        (args.run_dir / "run_stop_report.json").write_text(
            json.dumps(stop_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({**output, "run_stop": stop_report}, ensure_ascii=False, indent=2))
        raise RuntimeError(f"run stopped: {summary['stop_signal']['reason']}")
    if nonformal_smoke:
        smoke_acceptance = _audit_nonformal_smoke(args.run_dir)
        output["smoke_acceptance"] = smoke_acceptance
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if smoke_acceptance["status"] != "passed":
            raise RuntimeError(
                "non-formal smoke acceptance failed: "
                f"{smoke_acceptance['failed_gates']}"
            )
        return
    if config.get("storage", {}).get("sync_after_formal_run") is True:
        from .abcdef_mysql import sync_run_to_mysql

        mysql_sync = sync_run_to_mysql(args.run_dir)
        mysql_sync_report = {
            **mysql_sync,
            "status": "synced",
            "database": config["storage"]["target_database"],
        }
        (args.run_dir / "mysql_sync_report.json").write_text(
            json.dumps(mysql_sync_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output["mysql_sync"] = mysql_sync_report
    print(json.dumps(output, ensure_ascii=False, indent=2))


def _metric_report(
    trajectories: list[dict[str, Any]], calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute the frozen @k metrics from one no-replicate ledger.

    Semantic failures are valid observations only when their full task budget
    was consumed. Infrastructure, analysis, clock, evidence, and interrupted
    rows remain excluded regardless of the apparent PASS/FAIL outcome.
    """
    full_k = {"valid_pptr": list(range(1, 4)), "invalid_bug": list(range(1, 31))}
    checkpoints = {"valid_pptr": [1, 2, 3], "invalid_bug": [1, 5, 10, 30]}
    labels = {"valid_pptr": "PPTR", "invalid_bug": "BugReveal"}
    output: dict[str, Any] = {}
    for task, task_k_values in full_k.items():
        rows = [row for row in trajectories if row["dimensions"].get("task") == task]
        admissible: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for row in rows:
            result = row.get("result") or {}
            status = result.get("status")
            reason = str(result.get("reason") or "")
            budget = int(result.get("budget") or 0)
            attempts = int(result.get("model_calls") or 0)
            valid = (
                row.get("state") == "done"
                and (
                    status == "completed_success"
                    or (
                        status in {"completed_failure", "completed_unknown"}
                        and attempts == budget
                        and reason.startswith("budget_exhausted_")
                    )
                )
            )
            if valid:
                success_turn = next(
                    (
                        int(record["turn"]) for record in result.get("records", [])
                        if record.get("outcome") == "PASS"
                    ),
                    None,
                )
                admissible.append({**row, "success_turn": success_turn})
            else:
                excluded.append({
                    "trajectory_id": row.get("id"),
                    "case_id": row["dimensions"].get("case_id"),
                    "arm": row["dimensions"].get("arm"),
                    "state": row.get("state"),
                    "status": status,
                    "reason": reason or None,
                })
        denominator = len(admissible)
        curve: dict[str, Any] = {}
        for cutoff in task_k_values:
            reached = sum(
                row["success_turn"] is not None and row["success_turn"] <= cutoff
                for row in admissible
            )
            new_exact = sum(row["success_turn"] == cutoff for row in admissible)
            curve[str(cutoff)] = {
                "successes": reached,
                "denominator": denominator,
                "rate": reached / denominator if denominator else None,
                "new_at_k": new_exact,
            }
        checkpoint_metrics = {
            f"{labels[task]}@{cutoff}": curve[str(cutoff)]
            for cutoff in checkpoints[task]
        }
        attempts = sum(int((row.get("result") or {}).get("model_calls") or 0) for row in admissible)
        cases = {row["dimensions"].get("case_id") for row in admissible}
        elapsed_ms = sum(int((row.get("result") or {}).get("elapsed_ms") or 0) for row in admissible)
        output[task] = {
            "metric_family": labels[task],
            "full_curve": curve,
            "checkpoint_metrics": checkpoint_metrics,
            "checkpoint_k": checkpoints[task],
            "admissible_trajectories": denominator,
            "excluded_nonsemantic_or_audit_invalid": excluded,
            "logical_attempts": attempts,
            "case_count": len(cases),
            "attempts_per_case": attempts / len(cases) if cases else None,
            "attempts_per_trajectory": attempts / denominator if denominator else None,
            "trajectory_wall_time_ms_per_attempt": elapsed_ms / attempts if attempts else None,
        }
    subject_calls = [row for row in calls if row.get("kind") == "subject_model_generation"]
    provider_seconds = sum(
        float((row.get("result") or {}).get("provider_elapsed_seconds") or 0.0)
        for row in subject_calls if row.get("state") == "done"
    )
    output["physical_subject_calls"] = {
        "count": len(subject_calls),
        "provider_time_seconds": provider_seconds,
        "provider_time_seconds_per_call": (
            provider_seconds / len(subject_calls) if subject_calls else None
        ),
        "note": "The shared first response is one physical call reused across all six arms; logical attempts are reported separately.",
    }
    return output


def command_report(args: argparse.Namespace) -> None:
    path = args.run_dir / "ledger.sqlite3"
    if not path.is_file():
        raise FileNotFoundError(path)
    db = sqlite3.connect(path)
    states = dict(db.execute("SELECT state,COUNT(*) FROM trajectories GROUP BY state").fetchall())
    statuses: Counter[str] = Counter()
    trajectories = []
    for row in db.execute("SELECT id,dimensions,state,result FROM trajectories ORDER BY id"):
        item = {
            "id": row[0], "dimensions": json.loads(row[1]), "state": row[2],
            "result": json.loads(row[3]) if row[3] else None,
        }
        trajectories.append(item)
        if item["state"] == "done":
            statuses[(item["result"] or {}).get("status", "missing")] += 1
    external_calls = dict(db.execute("SELECT kind,COUNT(*) FROM external_calls GROUP BY kind").fetchall())
    calls = [
        {
            "kind": row[0], "state": row[1],
            "result": json.loads(row[2]) if row[2] else None,
        }
        for row in db.execute("SELECT kind,state,result FROM external_calls")
    ]
    uncertain = [row[0] for row in db.execute(
        "SELECT id FROM external_calls WHERE state='reserved' ORDER BY created_at"
    )]
    db.close()
    print(json.dumps({
        "trajectory_states": states,
        "completed_statuses": dict(statuses),
        "external_calls": external_calls,
        "uncertain_external_calls": uncertain,
        "primary_metrics": _metric_report(trajectories, calls),
    }, ensure_ascii=False, indent=2))


def command_sync_mysql(args: argparse.Namespace) -> None:
    from .abcdef_mysql import mysql_inventory, sync_run_to_mysql

    result = mysql_inventory(args.run_dir) if args.dry_run else sync_run_to_mysql(args.run_dir)
    print(json.dumps({
        **result,
        "mode": "dry_run" if args.dry_run else "synced",
    }, ensure_ascii=False, indent=2))


def command_qualify_mysql(args: argparse.Namespace) -> None:
    from .abcdef_mysql import qualify_run_to_mysql

    report = qualify_run_to_mysql(
        config_path=args.config,
        run_dir=args.run_dir,
        output_path=args.output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "passed":
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="E2-abcdef-v2 no-replicate experiment CLI")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--preflight-dir", type=Path, default=DEFAULT_OUTPUT)
    sub = parser.add_subparsers(dest="command", required=True)
    preflight = sub.add_parser("preflight")
    preflight.set_defaults(function=command_preflight)
    for name in ("plan", "run"):
        item = sub.add_parser(name)
        item.add_argument("--case", action="append")
        item.add_argument("--model", action="append")
        item.add_argument("--arm", action="append", choices=list("ABCDEF"))
        item.add_argument("--max-blocks", type=int)
        item.add_argument("--smoke-turn-cap", type=int)
        if name == "run":
            item.add_argument("--run-dir", type=Path, required=True)
            item.set_defaults(function=command_run)
        else:
            item.set_defaults(function=command_plan)
    smoke = sub.add_parser("smoke")
    smoke.add_argument("--case", action="append", required=True)
    smoke.add_argument("--model", action="append", required=True)
    smoke.add_argument("--smoke-turn-cap", type=int, default=2)
    smoke.add_argument("--run-dir", type=Path, required=True)
    smoke.set_defaults(
        function=command_run,
        max_blocks=1,
        nonformal_smoke=True,
    )
    report = sub.add_parser("report")
    report.add_argument("--run-dir", type=Path, required=True)
    report.set_defaults(function=command_report)
    mysql = sub.add_parser("sync-mysql")
    mysql.add_argument("--run-dir", type=Path, required=True)
    mysql.add_argument("--dry-run", action="store_true")
    mysql.set_defaults(function=command_sync_mysql)
    qualify_mysql = sub.add_parser("qualify-mysql")
    qualify_mysql.add_argument("--run-dir", type=Path, required=True)
    qualify_mysql.add_argument("--output", type=Path, required=True)
    qualify_mysql.set_defaults(function=command_qualify_mysql)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
