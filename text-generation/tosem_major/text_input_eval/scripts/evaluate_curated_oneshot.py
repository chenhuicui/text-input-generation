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
from text_input_eval.core.placeholder import contains_generated_value  # noqa: E402
from text_input_eval.validators.registry import get_validator  # noqa: E402


DEFAULT_TABLE = "t_llm_text_generation_curated"
DEFAULT_EXPERIMENTS = (
    "rq1_valid",
    "rq1_invalid",
    "ablation_prompt",
    "ablation_multi_component",
)


def parse_csv_arg(value: str) -> List[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def normalize_parsed_input_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return json.loads(value)
    raise TypeError(f"Unsupported parsed_input_json type: {type(value)}")


def evaluate_curated_row(row: Dict[str, Any]) -> Tuple[int, int, str]:
    parse_success = int(row.get("parse_success") or 0)
    parsed_value = row.get("parsed_input_json")
    if parse_success != 1 or parsed_value is None:
        return 0, 0, "not_parsed"

    parsed_inputs = normalize_parsed_input_json(parsed_value)
    placeholder_tag = 1 if contains_generated_value(parsed_inputs) else 0
    validator = get_validator(str(row["case_id"]))
    try:
        eval_success = 1 if validator.validate(parsed_inputs) else 0
    except Exception:
        return 0, placeholder_tag, "validator_exception"
    return eval_success, placeholder_tag, "validator"


def placeholders(items: Sequence[str]) -> str:
    return ",".join(["%s"] * len(items))


def build_filters(args: argparse.Namespace) -> Tuple[str, List[Any]]:
    clauses = [f"experiment IN ({placeholders(args.experiments)})"]
    params: List[Any] = list(args.experiments)

    if args.only_null:
        clauses.append("eval_success IS NULL")

    if args.models:
        clauses.append(f"canonical_model IN ({placeholders(args.models)})")
        params.extend(args.models)

    if args.case_ids:
        clauses.append(f"case_id IN ({placeholders(args.case_ids)})")
        params.extend(args.case_ids)

    if args.settings:
        clauses.append(f"prompt_setting IN ({placeholders(args.settings)})")
        params.extend(args.settings)

    if args.prompt_variants:
        clauses.append(f"prompt_variant IN ({placeholders(args.prompt_variants)})")
        params.extend(args.prompt_variants)

    return " AND ".join(clauses), params


def fetch_batch(
        *,
        table: str,
        where_sql: str,
        where_params: Sequence[Any],
        last_curated_id: int,
        batch_size: int,
) -> List[Dict[str, Any]]:
    sql = f"""
        SELECT
            curated_id,
            experiment,
            case_id,
            task,
            canonical_model,
            prompt_setting,
            prompt_variant,
            attempt_id,
            invalid_index,
            tau,
            combo_index,
            parse_success,
            parsed_input_json
        FROM {table}
        WHERE {where_sql}
          AND curated_id > %s
        ORDER BY curated_id ASC
        LIMIT %s
    """
    params = list(where_params) + [last_curated_id, batch_size]
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()
    finally:
        conn.close()


def count_rows(*, table: str, where_sql: str, where_params: Sequence[Any]) -> int:
    sql = f"""
        SELECT COUNT(*) AS row_count
        FROM {table}
        WHERE {where_sql}
    """
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, list(where_params))
            row = cursor.fetchone()
            return int(row["row_count"] or 0)
    finally:
        conn.close()


def update_batch(table: str, updates: Sequence[Tuple[int, int, int]]) -> None:
    if not updates:
        return
    eval_case_parts = []
    placeholder_case_parts = []
    eval_params: List[Any] = []
    placeholder_params: List[Any] = []
    curated_ids: List[int] = []
    for eval_success, placeholder_tag, curated_id in updates:
        eval_case_parts.append("WHEN %s THEN %s")
        eval_params.extend([curated_id, eval_success])
        placeholder_case_parts.append("WHEN %s THEN %s")
        placeholder_params.extend([curated_id, placeholder_tag])
        curated_ids.append(curated_id)

    sql = f"""
        UPDATE {table}
        SET eval_success = CASE curated_id
                {' '.join(eval_case_parts)}
                ELSE eval_success
            END,
            placeholder_tag = CASE curated_id
                {' '.join(placeholder_case_parts)}
                ELSE placeholder_tag
            END
        WHERE curated_id IN ({placeholders(curated_ids)})
    """
    params: List[Any] = eval_params + placeholder_params
    params.extend(curated_ids)
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def format_eta(*, seen: int, total: int, elapsed: float) -> str:
    if total <= 0 or seen <= 0:
        return "unknown"
    remaining = max(total - seen, 0)
    if remaining == 0:
        return "0.0s"
    rate = seen / elapsed if elapsed > 0 else 0
    if rate <= 0:
        return "unknown"
    return f"{remaining / rate:.1f}s"


def short_counter(counter: Counter[str], keys: Sequence[str]) -> str:
    return " ".join(f"{key}={counter[key]}" for key in keys)


def log(message: str) -> None:
    print(message, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate one-shot rows in t_llm_text_generation_curated with case validators."
    )
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument(
        "--experiments",
        default=",".join(DEFAULT_EXPERIMENTS),
        help="Comma-separated experiments. Default excludes RQ2 feedback experiments.",
    )
    parser.add_argument("--models", default="", help="Optional comma-separated canonical_model filter.")
    parser.add_argument("--case-ids", default="", help="Optional comma-separated case_id filter.")
    parser.add_argument("--settings", default="", help="Optional comma-separated prompt_setting filter.")
    parser.add_argument("--prompt-variants", default="", help="Optional comma-separated prompt_variant filter.")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--limit", type=int, default=0, help="0 means no limit.")
    parser.add_argument("--force", action="store_true", help="Re-evaluate rows even when eval_success is not NULL.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--progress-every", type=int, default=1000, help="Print progress after this many rows. 0 disables periodic progress logs.")
    parser.add_argument("--heartbeat-every", type=int, default=100, help="Print lightweight in-batch progress after this many rows. 0 disables heartbeat logs.")
    parser.add_argument("--fail-fast", action="store_true")
    args = parser.parse_args()

    args.experiments = parse_csv_arg(args.experiments)
    args.models = parse_csv_arg(args.models)
    args.case_ids = parse_csv_arg(args.case_ids)
    args.settings = parse_csv_arg(args.settings)
    args.prompt_variants = parse_csv_arg(args.prompt_variants)
    args.only_null = not args.force

    if not args.experiments:
        raise ValueError("--experiments cannot be empty")

    where_sql, where_params = build_filters(args)
    total_to_process = count_rows(table=args.table, where_sql=where_sql, where_params=where_params)
    if args.limit:
        total_to_process = min(total_to_process, args.limit)

    last_curated_id = 0
    total_seen = 0
    batch_index = 0
    next_progress_at = args.progress_every if args.progress_every > 0 else 0
    stats: Counter[str] = Counter()
    by_experiment: Counter[Tuple[str, str]] = Counter()
    t0 = time.perf_counter()

    log(
        "[EVAL_START] "
        f"table={args.table} experiments={','.join(args.experiments)} "
        f"only_null={args.only_null} dry_run={args.dry_run} "
        f"total={total_to_process} batch_size={args.batch_size} limit={args.limit}"
    )
    if args.models or args.case_ids or args.settings or args.prompt_variants:
        log(
            "[EVAL_FILTERS] "
            f"models={','.join(args.models) or '*'} "
            f"case_ids={','.join(args.case_ids) or '*'} "
            f"settings={','.join(args.settings) or '*'} "
            f"variants={','.join(args.prompt_variants) or '*'}"
        )

    while True:
        if args.limit and total_seen >= args.limit:
            break

        batch_size = args.batch_size
        if args.limit:
            batch_size = min(batch_size, args.limit - total_seen)
        rows = fetch_batch(
            table=args.table,
            where_sql=where_sql,
            where_params=where_params,
            last_curated_id=last_curated_id,
            batch_size=batch_size,
        )
        if not rows:
            break

        batch_index += 1
        batch_start_id = int(rows[0]["curated_id"])
        batch_end_id = int(rows[-1]["curated_id"])
        batch_stats: Counter[str] = Counter()
        updates: List[Tuple[int, int, int]] = []
        log(
            f"[BATCH_START] batch={batch_index} ids={batch_start_id}-{batch_end_id} "
            f"rows={len(rows)} seen={total_seen}/{total_to_process}"
        )
        for row in rows:
            last_curated_id = int(row["curated_id"])
            total_seen += 1

            try:
                eval_success, placeholder_tag, source = evaluate_curated_row(row)
            except Exception as exc:
                stats["errors"] += 1
                batch_stats["errors"] += 1
                log(
                    "[ERR] "
                    f"curated_id={row.get('curated_id')} case_id={row.get('case_id')} "
                    f"experiment={row.get('experiment')} error={type(exc).__name__}: {exc}"
                )
                if args.fail_fast:
                    raise
                continue

            stats[f"{source}_rows"] += 1
            batch_stats[f"{source}_rows"] += 1
            stats[f"eval_{eval_success}"] += 1
            batch_stats[f"eval_{eval_success}"] += 1
            if placeholder_tag:
                stats["placeholder_tag_1"] += 1
                batch_stats["placeholder_tag_1"] += 1
            by_experiment[(str(row["experiment"]), f"eval_{eval_success}")] += 1
            updates.append((eval_success, placeholder_tag, last_curated_id))

            if args.heartbeat_every and total_seen % args.heartbeat_every == 0:
                elapsed = time.perf_counter() - t0
                log(
                    f"[HEARTBEAT] seen={total_seen}/{total_to_process} "
                    f"curated_id={last_curated_id} "
                    f"eval_1={stats['eval_1']} eval_0={stats['eval_0']} "
                    f"validator_exception={stats['validator_exception_rows']} "
                    f"not_parsed={stats['not_parsed_rows']} errors={stats['errors']} "
                    f"elapsed={elapsed:.1f}s"
                )

        if args.dry_run:
            stats["dry_run_updates"] += len(updates)
            batch_stats["dry_run_updates"] += len(updates)
        else:
            update_batch(args.table, updates)
            stats["updated"] += len(updates)
            batch_stats["updated"] += len(updates)

        if args.progress_every and total_seen >= next_progress_at:
            elapsed = time.perf_counter() - t0
            eta = format_eta(seen=total_seen, total=total_to_process, elapsed=elapsed)
            log(
                f"[BATCH] batch={batch_index} ids={batch_start_id}-{batch_end_id} "
                f"seen={total_seen}/{total_to_process} "
                f"{short_counter(batch_stats, ('updated', 'dry_run_updates', 'eval_1', 'eval_0', 'validator_rows', 'validator_exception_rows', 'not_parsed_rows', 'placeholder_tag_1', 'errors'))} "
                f"total_updated={stats['updated']} total_dry_run={stats['dry_run_updates']} "
                f"elapsed={elapsed:.1f}s eta={eta}"
            )
            while next_progress_at and total_seen >= next_progress_at:
                next_progress_at += args.progress_every

    elapsed = time.perf_counter() - t0
    log(
        f"[EVAL_DONE] seen={total_seen} updated={stats['updated']} "
        f"dry_run_updates={stats['dry_run_updates']} errors={stats['errors']} "
        f"elapsed={elapsed:.1f}s"
    )
    log("[SUMMARY]")
    for key in sorted(stats):
        log(f"  {key}: {stats[key]}")
    log("[SUMMARY_BY_EXPERIMENT]")
    for (experiment, key), value in sorted(by_experiment.items()):
        log(f"  {experiment} {key}: {value}")


if __name__ == "__main__":
    main()
