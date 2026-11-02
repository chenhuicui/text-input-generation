from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from tosem_major.text_input_eval.services.one_shot import get_conn


TOSEM_MAJOR_ROOT = Path(__file__).resolve().parents[2]
if str(TOSEM_MAJOR_ROOT) not in sys.path:
    sys.path.insert(0, str(TOSEM_MAJOR_ROOT))

import text_input_eval.validators  # noqa: E402,F401
from text_input_eval.validators.registry import get_validator  # noqa: E402


DEFAULT_TABLE = "t_llm_tester_revision_generation"
DEFAULT_PROMPT_VERSION = "llm_tester_revision_all_sources_v2"


def parse_csv_arg(value: str) -> List[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def normalize_generated_input_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise TypeError(f"Unsupported generated_input_json type: {type(value)}")


def placeholders(items: Sequence[Any]) -> str:
    return ",".join(["%s"] * len(items))


def build_filters(args: argparse.Namespace) -> Tuple[str, List[Any]]:
    clauses = ["prompt_version = %s"]
    params: List[Any] = [args.prompt_version]

    if args.only_null:
        clauses.append("eval_success IS NULL")

    if args.statuses:
        clauses.append(f"status IN ({placeholders(args.statuses)})")
        params.extend(args.statuses)

    if args.tasks:
        clauses.append(f"rq3_task IN ({placeholders(args.tasks)})")
        params.extend(args.tasks)

    if args.modes:
        clauses.append(f"mode IN ({placeholders(args.modes)})")
        params.extend(args.modes)

    if args.tester_ids:
        clauses.append(f"tester_id IN ({placeholders(args.tester_ids)})")
        params.extend(args.tester_ids)

    if args.case_ids:
        clauses.append(f"case_id IN ({placeholders(args.case_ids)})")
        params.extend(args.case_ids)

    return " AND ".join(clauses), params


def count_rows(*, table: str, where_sql: str, where_params: Sequence[Any]) -> int:
    sql = f"SELECT COUNT(*) AS row_count FROM {table} WHERE {where_sql}"
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, list(where_params))
            row = cursor.fetchone()
            return int(row["row_count"] or 0)
    finally:
        conn.close()


def fetch_batch(
        *,
        table: str,
        where_sql: str,
        where_params: Sequence[Any],
        last_id: int,
        batch_size: int,
) -> List[Dict[str, Any]]:
    sql = f"""
        SELECT
            id,
            rq3_task,
            case_id,
            mode,
            tester_id,
            status,
            parse_success,
            generated_input_json
        FROM {table}
        WHERE {where_sql}
          AND id > %s
        ORDER BY id ASC
        LIMIT %s
    """
    params = list(where_params) + [last_id, batch_size]
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()
    finally:
        conn.close()


def evaluate_row(row: Dict[str, Any]) -> Tuple[int, str]:
    if str(row.get("status") or "") != "success":
        return 0, "not_success"
    if int(row.get("parse_success") or 0) != 1 or row.get("generated_input_json") is None:
        return 0, "not_parsed"

    generated_inputs = normalize_generated_input_json(row["generated_input_json"])
    validator = get_validator(str(row["case_id"]))
    try:
        return (1 if validator.validate(generated_inputs) else 0), "validator"
    except Exception as exc:
        return 0, f"validator_exception:{type(exc).__name__}"


def update_batch(table: str, updates: Sequence[Tuple[int, int]]) -> None:
    if not updates:
        return
    eval_case_parts = []
    eval_params: List[Any] = []
    ids: List[int] = []
    for eval_success, row_id in updates:
        eval_case_parts.append("WHEN %s THEN %s")
        eval_params.extend([row_id, eval_success])
        ids.append(row_id)

    sql = f"""
        UPDATE {table}
        SET eval_success = CASE id
                {' '.join(eval_case_parts)}
                ELSE eval_success
            END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id IN ({placeholders(ids)})
    """
    params: List[Any] = eval_params + ids
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def log(message: str) -> None:
    print(message, flush=True)


def short_counter(counter: Counter[str], keys: Sequence[str]) -> str:
    return " ".join(f"{key}={counter[key]}" for key in keys)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate RQ3 tester modification/regeneration outputs with case validators."
    )
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--prompt-version", default=DEFAULT_PROMPT_VERSION)
    parser.add_argument("--tasks", default="valid_pptr,invalid_bug")
    parser.add_argument("--modes", default="modification,regeneration")
    parser.add_argument("--tester-ids", default="")
    parser.add_argument("--case-ids", default="")
    parser.add_argument("--statuses", default="success")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="Re-evaluate rows even when eval_success is not NULL.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--progress-every", type=int, default=200)
    parser.add_argument("--fail-fast", action="store_true")
    args = parser.parse_args()

    args.tasks = parse_csv_arg(args.tasks)
    args.modes = parse_csv_arg(args.modes)
    args.tester_ids = parse_csv_arg(args.tester_ids)
    args.case_ids = parse_csv_arg(args.case_ids)
    args.statuses = parse_csv_arg(args.statuses)
    args.only_null = not args.force

    where_sql, where_params = build_filters(args)
    total = count_rows(table=args.table, where_sql=where_sql, where_params=where_params)
    if args.limit:
        total = min(total, args.limit)

    log(
        "[EVAL_START] "
        f"table={args.table} prompt_version={args.prompt_version} "
        f"tasks={','.join(args.tasks) or '*'} modes={','.join(args.modes) or '*'} "
        f"only_null={args.only_null} dry_run={args.dry_run} total={total}"
    )

    last_id = 0
    seen = 0
    stats: Counter[str] = Counter()
    by_task_mode: Counter[Tuple[str, str, str]] = Counter()
    t0 = time.perf_counter()

    while True:
        if args.limit and seen >= args.limit:
            break
        batch_size = args.batch_size
        if args.limit:
            batch_size = min(batch_size, args.limit - seen)
        rows = fetch_batch(
            table=args.table,
            where_sql=where_sql,
            where_params=where_params,
            last_id=last_id,
            batch_size=batch_size,
        )
        if not rows:
            break

        updates: List[Tuple[int, int]] = []
        batch_stats: Counter[str] = Counter()
        batch_start_id = int(rows[0]["id"])
        batch_end_id = int(rows[-1]["id"])
        log(f"[BATCH_START] ids={batch_start_id}-{batch_end_id} rows={len(rows)} seen={seen}/{total}")

        for row in rows:
            last_id = int(row["id"])
            seen += 1
            try:
                eval_success, source = evaluate_row(row)
            except Exception as exc:
                stats["errors"] += 1
                batch_stats["errors"] += 1
                log(
                    "[ERR] "
                    f"id={row.get('id')} case_id={row.get('case_id')} "
                    f"mode={row.get('mode')} tester={row.get('tester_id')} "
                    f"error={type(exc).__name__}: {exc}"
                )
                if args.fail_fast:
                    raise
                continue

            stats[f"{source}_rows"] += 1
            batch_stats[f"{source}_rows"] += 1
            stats[f"eval_{eval_success}"] += 1
            batch_stats[f"eval_{eval_success}"] += 1
            by_task_mode[(str(row["rq3_task"]), str(row["mode"]), f"eval_{eval_success}")] += 1
            updates.append((eval_success, last_id))

        if args.dry_run:
            stats["dry_run_updates"] += len(updates)
            batch_stats["dry_run_updates"] += len(updates)
        else:
            update_batch(args.table, updates)
            stats["updated"] += len(updates)
            batch_stats["updated"] += len(updates)

        if args.progress_every and (seen % args.progress_every == 0 or seen >= total):
            elapsed = time.perf_counter() - t0
            log(
                f"[BATCH] ids={batch_start_id}-{batch_end_id} seen={seen}/{total} "
                f"{short_counter(batch_stats, ('updated', 'dry_run_updates', 'eval_1', 'eval_0', 'validator_rows', 'not_parsed_rows', 'not_success_rows', 'errors'))} "
                f"total_updated={stats['updated']} elapsed={elapsed:.1f}s"
            )

    elapsed = time.perf_counter() - t0
    log(f"[EVAL_DONE] seen={seen} updated={stats['updated']} dry_run_updates={stats['dry_run_updates']} errors={stats['errors']} elapsed={elapsed:.1f}s")
    log("[SUMMARY]")
    for key in sorted(stats):
        log(f"  {key}: {stats[key]}")
    log("[SUMMARY_BY_TASK_MODE]")
    for (task, mode, key), value in sorted(by_task_mode.items()):
        log(f"  {task} {mode} {key}: {value}")


if __name__ == "__main__":
    main()
