"""Idempotently mirror one local E2-abcdef-v2 ledger into MySQL.

SQLite and evidence files remain the crash-safe source of truth.  MySQL is an
analytics mirror: complete prompts, complete returned answer text, parsed JSON
candidates, provider envelopes, usage/timing, trajectory events, and artifact
metadata are copied without modifying the legacy generation tables.
"""
from __future__ import annotations

import json
import os
import sqlite3
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .structured_storage import canonical_json, digest


SCHEMA_PATH = Path(__file__).with_name("sql") / "e2_abcdef_v2_mysql.sql"
TABLES = {
    "runs": "e2_abcdef_v2_runs",
    "trajectories": "e2_abcdef_v2_trajectories",
    "external_calls": "e2_abcdef_v2_external_calls",
    "turns": "e2_abcdef_v2_turns",
}


def _encoded(value: Any) -> str | None:
    return None if value is None else canonical_json(value)


def _load_json(path: Path, *, required: bool = True) -> dict[str, Any] | None:
    if not path.is_file():
        if required:
            raise FileNotFoundError(path)
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _read_ledger(run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    path = run_dir / "ledger.sqlite3"
    if not path.is_file():
        raise FileNotFoundError(path)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        trajectories = []
        for row in db.execute(
            "SELECT id,dimensions,state,events,result,updated_at FROM trajectories ORDER BY id"
        ):
            trajectories.append({
                "id": row["id"],
                "dimensions": json.loads(row["dimensions"]),
                "state": row["state"],
                "events": json.loads(row["events"]),
                "result": json.loads(row["result"]) if row["result"] else None,
                "updated_at": row["updated_at"],
            })
        calls = []
        for row in db.execute(
            "SELECT id,kind,dimensions,payload,state,result,created_at,completed_at "
            "FROM external_calls ORDER BY created_at,id"
        ):
            calls.append({
                "id": row["id"],
                "kind": row["kind"],
                "dimensions": json.loads(row["dimensions"]),
                "payload": json.loads(row["payload"]),
                "state": row["state"],
                "result": json.loads(row["result"]) if row["result"] else None,
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
            })
    finally:
        db.close()
    return trajectories, calls


def _run_identity(manifest: dict[str, Any]) -> str:
    return digest(manifest)


def build_mysql_rows(run_dir: Path) -> dict[str, Any]:
    """Build lossless normalized rows without connecting to MySQL."""
    run_dir = run_dir.resolve()
    manifest = _load_json(run_dir / "run_manifest.json")
    assert manifest is not None
    protocol_id = manifest.get("protocol_id")
    smoke = _load_json(run_dir / "smoke_acceptance_report.json", required=False)
    summary = _load_json(run_dir / "last_run_summary.json", required=False)
    trajectories, calls = _read_ledger(run_dir)
    run_id = _run_identity(manifest)
    call_map = {row["id"]: row for row in calls}

    run_row = (
        run_id,
        manifest.get("protocol_id"),
        manifest.get("run_scope"),
        str(run_dir),
        manifest.get("created_at_utc"),
        manifest.get("config_sha256"),
        int(manifest.get("formal_run_enabled_at_start") is True),
        _encoded(manifest),
        _encoded(smoke),
        _encoded(summary),
        summary.get("elapsed_ms") if summary else None,
    )

    trajectory_rows: list[tuple[Any, ...]] = []
    turn_rows: list[tuple[Any, ...]] = []
    candidate_by_call: dict[str, Any] = {}
    for row in trajectories:
        dimensions = row["dimensions"]
        if protocol_id == "E2-abcdef-v2" and "replicate" in dimensions:
            raise ValueError("E2-abcdef-v2 trajectory dimensions must not contain replicate")
        result = row["result"] or {}
        trajectory_rows.append((
            run_id, row["id"], dimensions.get("case_id"), dimensions.get("task"),
            dimensions.get("model"), dimensions.get("representation"),
            dimensions.get("arm"), row["state"],
            result.get("status"), result.get("reason"), result.get("budget"),
            result.get("first_success_turn"),
            result.get("model_calls"), result.get("executions"), result.get("recoveries"),
            result.get("analyzer_calls"), _encoded(dimensions), _encoded(row["events"]),
            result.get("started_at_utc"), result.get("completed_at_utc"),
            result.get("elapsed_ms"), _encoded(row["result"]), row["updated_at"],
        ))
        events = row["events"]
        model_events = {
            int(event["turn"]): event for event in events
            if event.get("event") == "model_request" and event.get("turn") is not None
        }
        output_events = {
            int(event["turn"]): event for event in events
            if event.get("event") == "output_check" and event.get("turn") is not None
        }
        execution_events = {
            int(event["turn"]): event for event in events
            if event.get("event") == "execution" and event.get("turn") is not None
        }
        for record in result.get("records", []):
            turn = int(record["turn"])
            model_event = model_events.get(turn, {})
            output_event = output_events.get(turn, {})
            execution_event = execution_events.get(turn, {})
            call_id = model_event.get("physical_request_id")
            call = call_map.get(call_id, {})
            call_result = call.get("result") or {}
            payload = call.get("payload") or {}
            audit = model_event.get("prompt_audit") or {}
            candidate_by_call[str(call_id)] = record.get("candidate")
            turn_rows.append((
                run_id, row["id"], turn, call_id, record.get("request_id"),
                dimensions.get("case_id"), dimensions.get("task"), dimensions.get("model"),
                dimensions.get("arm"),
                record.get("output_status", output_event.get("output_status")),
                record.get("parse_error", output_event.get("parse_error")),
                record.get("outcome"), record.get("run_status"), execution_event.get("verdict"),
                record.get("error_type", execution_event.get("error_type")),
                record.get("oracle_version"), record.get("verdict_oracle_version"),
                record.get("visual_observation_oracle_version"),
                audit.get("status_visible"), audit.get("visual_visible"),
                audit.get("ui_observations_visible"), audit.get("analysis_visible"),
                model_event.get("attachment_sha256"), call_result.get("response_id"),
                call_result.get("session_id"), call_result.get("elapsed_ms"),
                call_result.get("provider_elapsed_seconds"), _encoded(payload.get("message")),
                _encoded(record.get("raw_response", "")), _encoded(record.get("candidate")),
                _encoded(audit), _encoded(record.get("visual_feedback")),
                _encoded(call_result.get("usage")),
                _encoded(call_result.get("provider_response")),
                _encoded(execution_event),
            ))

    call_rows: list[tuple[Any, ...]] = []
    for row in calls:
        dimensions = row["dimensions"]
        payload = row["payload"]
        result = row["result"] or {}
        query = payload.get("message", payload.get("context"))
        call_rows.append((
            run_id, row["id"], row["kind"], row["state"], dimensions.get("case_id"),
            dimensions.get("task"), dimensions.get("model"), dimensions.get("arm"),
            dimensions.get("turn"), dimensions.get("role"),
            result.get("response_id"), result.get("session_id"), result.get("response_model"),
            result.get("run_status"), result.get("error_type"), result.get("elapsed_ms"),
            result.get("provider_elapsed_seconds"), _encoded(dimensions), _encoded(payload),
            _encoded(row["result"]), _encoded(query), _encoded(result.get("raw_response")),
            _encoded(candidate_by_call.get(row["id"])), _encoded(result.get("provider_response")),
            _encoded(result.get("usage")), row["created_at"], row["completed_at"],
        ))
    return {
        "run_id": run_id,
        "run": run_row,
        "trajectories": trajectory_rows,
        "external_calls": call_rows,
        "turns": turn_rows,
    }


def mysql_inventory(run_dir: Path) -> dict[str, Any]:
    rows = build_mysql_rows(run_dir)
    return {
        "run_id": rows["run_id"],
        "runs": 1,
        "trajectories": len(rows["trajectories"]),
        "external_calls": len(rows["external_calls"]),
        "turns": len(rows["turns"]),
        "schema_path": str(SCHEMA_PATH),
    }


def _settings() -> dict[str, Any]:
    names = ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE")
    missing = [name for name in names if not os.environ.get(name, "").strip()]
    if missing:
        raise RuntimeError(f"MySQL environment variables are missing: {missing}")
    return {
        "host": os.environ["MYSQL_HOST"],
        "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "user": os.environ["MYSQL_USER"],
        "password": os.environ["MYSQL_PASSWORD"],
        "database": os.environ["MYSQL_DATABASE"],
        "charset": "utf8mb4",
        "autocommit": False,
        "connect_timeout": 20,
        "read_timeout": 600,
        "write_timeout": 600,
    }


def _row_payload_bytes(row: tuple[Any, ...]) -> int:
    """Conservatively size the variable-width part of one MySQL row."""
    return sum(
        len(value.encode("utf-8")) if isinstance(value, str)
        else len(value) if isinstance(value, bytes)
        else 0
        for value in row
    )


def _chunks(
    rows: Sequence[tuple[Any, ...]],
    size: int = 100,
    max_payload_bytes: int = 256 * 1024,
) -> Iterable[Sequence[tuple[Any, ...]]]:
    """Yield count- and byte-bounded batches for the remote MySQL mirror.

    Provider envelopes make row sizes highly nonuniform.  A row-count-only
    batch can therefore hold many megabytes and leave the remote connection
    blocked in ``sendall``.  The conservative pre-escaping bound keeps each
    explicit transaction small; a single oversized row is still yielded by
    itself so that no source data is truncated.
    """
    batch: list[tuple[Any, ...]] = []
    batch_bytes = 0
    for row in rows:
        row_bytes = _row_payload_bytes(row)
        if batch and (
            len(batch) >= size or batch_bytes + row_bytes > max_payload_bytes
        ):
            yield batch
            batch = []
            batch_bytes = 0
        batch.append(row)
        batch_bytes += row_bytes
    if batch:
        yield batch


def _ensure_schema(cursor: Any, database: str) -> bool:
    """Create the mirror schema only when a required table is absent.

    Reissuing ``CREATE TABLE IF NOT EXISTS`` for every ledger sync can pile up
    in MySQL's ``Opening tables`` state during a server-side table incident.
    The schema is immutable after qualification, so a cheap information-schema
    probe avoids metadata DDL for the normal, already-qualified path.  A
    missing table still fails closed through the original schema statements.
    """
    expected = set(TABLES.values())
    placeholders = ",".join(["%s"] * len(expected))
    cursor.execute(
        "SELECT table_name FROM information_schema.tables "
        f"WHERE table_schema=%s AND table_name IN ({placeholders})",
        (database, *sorted(expected)),
    )
    existing = {str(row[0]) for row in cursor.fetchall()}
    if existing == expected:
        return False
    for statement in SCHEMA_PATH.read_text(encoding="utf-8").split(";"):
        if statement.strip():
            cursor.execute(statement)
    return True


def sync_run_to_mysql(run_dir: Path) -> dict[str, Any]:
    import pymysql

    rows = build_mysql_rows(run_dir)
    connection = pymysql.connect(**_settings())
    try:
        with connection.cursor() as cursor:
            _ensure_schema(cursor, os.environ["MYSQL_DATABASE"])
            cursor.execute(
                f"""INSERT INTO {TABLES['runs']}
                (run_id,protocol_id,run_scope,source_run_dir,created_at_utc,config_sha256,
                 formal_run_enabled_at_start,manifest_json,smoke_acceptance_json,
                 last_run_summary_json,last_run_elapsed_ms)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE source_run_dir=VALUES(source_run_dir),
                manifest_json=VALUES(manifest_json),smoke_acceptance_json=VALUES(smoke_acceptance_json),
                last_run_summary_json=VALUES(last_run_summary_json),
                last_run_elapsed_ms=VALUES(last_run_elapsed_ms),
                last_synced_at=CURRENT_TIMESTAMP""",
                rows["run"],
            )
            trajectory_sql = f"""INSERT INTO {TABLES['trajectories']}
                (run_id,trajectory_id,case_id,task,model,representation,arm,state,
                 status,reason,budget,first_success_turn,model_calls,executions,recoveries,analyzer_calls,
                 dimensions_json,events_json,started_at_utc,completed_at_utc,elapsed_ms,
                 result_json,sqlite_updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE state=VALUES(state),status=VALUES(status),reason=VALUES(reason),
                budget=VALUES(budget),first_success_turn=VALUES(first_success_turn),
                model_calls=VALUES(model_calls),executions=VALUES(executions),
                recoveries=VALUES(recoveries),analyzer_calls=VALUES(analyzer_calls),
                started_at_utc=VALUES(started_at_utc),completed_at_utc=VALUES(completed_at_utc),
                elapsed_ms=VALUES(elapsed_ms),
                events_json=VALUES(events_json),result_json=VALUES(result_json),
                sqlite_updated_at=VALUES(sqlite_updated_at)"""
            call_sql = f"""INSERT INTO {TABLES['external_calls']}
                (run_id,call_id,kind,state,case_id,task,model,arm,turn_id,role_name,
                 response_id,session_id,response_model,run_status,error_type,elapsed_ms,provider_elapsed_seconds,
                 dimensions_json,payload_json,result_json,raw_query_json,raw_response_json,
                 parsed_candidate_json,provider_response_json,usage_json,created_at_utc,completed_at_utc)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE state=VALUES(state),result_json=VALUES(result_json),
                raw_response_json=VALUES(raw_response_json),parsed_candidate_json=VALUES(parsed_candidate_json),
                provider_response_json=VALUES(provider_response_json),usage_json=VALUES(usage_json),
                response_id=VALUES(response_id),session_id=VALUES(session_id),
                response_model=VALUES(response_model),run_status=VALUES(run_status),
                elapsed_ms=VALUES(elapsed_ms),provider_elapsed_seconds=VALUES(provider_elapsed_seconds),
                completed_at_utc=VALUES(completed_at_utc)"""
            turn_sql = f"""INSERT INTO {TABLES['turns']}
                (run_id,trajectory_id,turn_id,physical_call_id,request_id,case_id,task,model,
                 arm,output_status,parse_error,outcome,run_status,verdict,error_type,oracle_version,
                 verdict_oracle_version,visual_observation_oracle_version,status_visible,visual_visible,
                 ui_observations_visible,analysis_visible,attachment_sha256,response_id,session_id,
                 elapsed_ms,provider_elapsed_seconds,raw_prompt_json,raw_response_json,
                 parsed_candidate_json,prompt_audit_json,visual_feedback_json,usage_json,
                 provider_response_json,execution_event_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE output_status=VALUES(output_status),parse_error=VALUES(parse_error),
                outcome=VALUES(outcome),run_status=VALUES(run_status),verdict=VALUES(verdict),
                raw_prompt_json=VALUES(raw_prompt_json),raw_response_json=VALUES(raw_response_json),
                parsed_candidate_json=VALUES(parsed_candidate_json),prompt_audit_json=VALUES(prompt_audit_json),
                visual_feedback_json=VALUES(visual_feedback_json),usage_json=VALUES(usage_json),
                provider_response_json=VALUES(provider_response_json),
                execution_event_json=VALUES(execution_event_json),error_type=VALUES(error_type),
                response_id=VALUES(response_id),session_id=VALUES(session_id),elapsed_ms=VALUES(elapsed_ms),
                provider_elapsed_seconds=VALUES(provider_elapsed_seconds)"""
            connection.commit()
            for sql, values in (
                (trajectory_sql, rows["trajectories"]),
                (call_sql, rows["external_calls"]),
                (turn_sql, rows["turns"]),
            ):
                for chunk in _chunks(values):
                    cursor.executemany(sql, chunk)
                    # The mirror is idempotent and is verified by exact final
                    # counts.  Committing each bounded batch permits a retry to
                    # resume after a transport failure without one huge remote
                    # transaction or any mutation of the SQLite source.
                    connection.commit()
    except Exception:
        try:
            connection.rollback()
        except Exception:
            # Preserve the original sync failure when the socket itself has
            # already gone away; rollback cannot repair a dead connection.
            pass
        raise
    finally:
        connection.close()
    return mysql_inventory(run_dir)


def require_mysql_database(expected_database: str) -> str:
    """Fail closed unless the configured connection reaches the frozen target DB."""
    import pymysql

    connection = pymysql.connect(**_settings())
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE()")
            actual = str(cursor.fetchone()[0])
    finally:
        connection.close()
    if actual != expected_database:
        raise RuntimeError(
            f"MySQL target mismatch: expected {expected_database!r}, got {actual!r}"
        )
    return actual


def qualify_run_to_mysql(
    *, config_path: Path, run_dir: Path, output_path: Path,
) -> dict[str, Any]:
    """Sync a completed smoke and verify lossless analytical coverage in MySQL."""
    import pymysql

    config = _load_json(config_path.resolve())
    assert config is not None
    storage = config.get("storage", {})
    local = build_mysql_rows(run_dir)
    inventory = sync_run_to_mysql(run_dir)
    connection = pymysql.connect(**_settings())
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE()")
            database_name = str(cursor.fetchone()[0])
            counts: dict[str, int] = {}
            for key, table in (
                ("runs", TABLES["runs"]),
                ("trajectories", TABLES["trajectories"]),
                ("external_calls", TABLES["external_calls"]),
                ("turns", TABLES["turns"]),
            ):
                cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id=%s", (local["run_id"],))
                counts[key] = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*),"
                "SUM(raw_prompt_json IS NOT NULL),SUM(raw_response_json IS NOT NULL),"
                "SUM(parsed_candidate_json IS NOT NULL) "
                f"FROM {TABLES['turns']} WHERE run_id=%s",
                (local["run_id"],),
            )
            total_turns, prompt_rows, answer_rows, candidate_rows = [
                int(value or 0) for value in cursor.fetchone()
            ]
            cursor.execute(
                "SELECT COUNT(*),SUM(usage_json IS NOT NULL),"
                "SUM(provider_response_json IS NOT NULL) "
                f"FROM {TABLES['external_calls']} "
                "WHERE run_id=%s AND kind='subject_model_generation'",
                (local["run_id"],),
            )
            subject_calls, usage_rows, provider_rows = [
                int(value or 0) for value in cursor.fetchone()
            ]
    finally:
        connection.close()
    expected = {
        "runs": 1,
        "trajectories": len(local["trajectories"]),
        "external_calls": len(local["external_calls"]),
        "turns": len(local["turns"]),
    }
    prompt_coverage = prompt_rows / total_turns if total_turns else 0.0
    answer_coverage = answer_rows / total_turns if total_turns else 0.0
    provider_coverage = provider_rows / subject_calls if subject_calls else 0.0
    row_count_match = counts == expected
    target_database = storage.get("target_database")
    database_match = database_name == target_database
    smoke = _load_json(run_dir.resolve() / "smoke_acceptance_report.json", required=False)
    passed = (
        smoke is not None
        and smoke.get("status") == "passed"
        and row_count_match
        and prompt_coverage == 1.0
        and answer_coverage == 1.0
        and provider_coverage == 1.0
        and database_match
        and storage.get("legacy_tables_modified") is False
    )
    report = {
        "schema_version": "e2-abcdef-v2-mysql-qualification-1",
        "protocol_id": config.get("protocol_id"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if passed else "failed",
        "run_id": local["run_id"],
        "source_run_dir": str(run_dir.resolve()),
        "source_smoke_status": smoke.get("status") if smoke else None,
        "database_env": storage.get("database_env"),
        "target_database": target_database,
        "database_name": database_name,
        "database_match": database_match,
        "table_prefix": storage.get("table_prefix"),
        "schema_path": str(SCHEMA_PATH),
        "schema_sha256": hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
        "expected_counts": expected,
        "mysql_counts": counts,
        "row_count_match": row_count_match,
        "complete_prompt_coverage": prompt_coverage,
        "complete_answer_coverage": answer_coverage,
        "parsed_candidate_rows": candidate_rows,
        "subject_calls": subject_calls,
        "usage_rows": usage_rows,
        "usage_coverage": usage_rows / subject_calls if subject_calls else 0.0,
        "provider_envelope_rows": provider_rows,
        "provider_envelope_coverage": provider_coverage,
        "missing_usage_is_not_zero": True,
        "media_bodies_stored_in_mysql": False,
        "media_paths_and_hashes_stored_in_mysql": True,
        "legacy_tables_modified": False,
        "sync_inventory": inventory,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report
