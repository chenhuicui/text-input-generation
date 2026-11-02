from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pymysql

from tosem_major.text_input_eval.scripts.llm_tester_assessment import (
    API_URL_DEFAULT,
    EXPERIMENT_RQ1_INVALID,
    EXPERIMENT_RQ1_VALID,
    FINAL_MODELS,
    INVALID_INDICES,
    MustLabJudgeClient,
    PROMPT_SETTINGS,
    VALID_ATTEMPTS,
    build_context,
    compact_log_text,
    ensure_conn_alive,
    ensure_identifier,
    expand_int_ranges,
    extract_json_object,
    get_conn,
    normalize_json_value,
    parse_csv,
    prompt_variant_for_setting,
    run_db_write_with_retry,
)
from tosem_major.text_input_eval.services.one_shot import (
    INVALID_TASK,
    VALID_TASK,
    validate_resource_ids_exact_match,
)


PROJECT_ROOT = Path(
    "/Users/cuichenhui/Documents/local-repositories/"
    "llm-empirical-study-workspace/llm-empirical-study"
)

DEFAULT_SELECTED_VALID_JSON = (
    PROJECT_ROOT / "output" / "rq3_tester_table" / "rq3_new_llm_source_data.json"
)

SOURCE_TABLE_DEFAULT = "t_llm_text_generation_curated"
CASE_TABLE_DEFAULT = "t_llm_tester_revision_case"
TASK_TABLE_DEFAULT = "t_llm_tester_revision_generation"
SOURCE_PAYLOAD_TABLE_DEFAULT = "t_llm_tester_revision_source"
PROMPT_VERSION_DEFAULT = "llm_tester_revision_all_sources_v2"
DEFAULT_JUDGE_MODEL = "claude-opus-4-6"
DEFAULT_LOGICAL_JUDGE_MODEL = "claude-tester"
COMPRESSED_JSON_PREFIX = "zlib+base64:"
COMPRESS_JSON_THRESHOLD = 20000

TASK_VALID = "valid_pptr"
TASK_INVALID = "invalid_bug"
MODE_MODIFICATION = "modification"
MODE_REGENERATION = "regeneration"

TESTER_BACKGROUNDS = {
    1: (
        "Tester 1",
        "A senior professional Android software tester with over 15 years of "
        "software-testing experience. The tester is strong at diagnosing why "
        "generated inputs fail, using execution feedback, and crafting robust "
        "inputs that satisfy the test objective.",
    ),
    2: (
        "Tester 2",
        "A professional Android software tester with over five years of "
        "software-testing experience. The tester focuses on practical UI "
        "semantics, validation behavior, and reproducible test inputs.",
    ),
    3: (
        "Tester 3",
        "A professional Android software tester with over three years of "
        "software-testing experience. The tester focuses on field hints, visible "
        "labels, and common mobile-app input-validation behavior.",
    ),
}


def log(message: str) -> None:
    print(message, flush=True)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_dumps_for_db(value: Any) -> str:
    text = json_dumps(value)
    if len(text) < COMPRESS_JSON_THRESHOLD:
        return text
    compressed = zlib.compress(text.encode("utf-8"), level=6)
    return COMPRESSED_JSON_PREFIX + base64.b64encode(compressed).decode("ascii")


def json_loads_from_db(value: str) -> Any:
    text = str(value or "")
    if text.startswith(COMPRESSED_JSON_PREFIX):
        raw = base64.b64decode(text[len(COMPRESSED_JSON_PREFIX):])
        text = zlib.decompress(raw).decode("utf-8")
    return json.loads(text)


def normalize_case_id(value: str, task: str) -> str:
    raw = str(value).strip().upper()
    if task == TASK_VALID:
        prefix = "P"
    elif task == TASK_INVALID:
        prefix = "B"
    else:
        raise ValueError(f"Unsupported task: {task}")
    if raw.startswith(prefix):
        return f"{prefix}{int(raw[1:]):03d}"
    return f"{prefix}{int(raw):03d}"


def tester_id_for_num(tester_num: int) -> str:
    return f"tester_{tester_num}"


def tester_background_for_num(tester_num: int) -> Tuple[str, str]:
    if tester_num not in TESTER_BACKGROUNDS:
        raise ValueError(f"Unsupported tester id: {tester_num}. Expected 1, 2, or 3.")
    return TESTER_BACKGROUNDS[tester_num]


def load_selected_valid_cases(path: Path) -> List[Dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for item in data.get("selected_apps") or []:
        app_id = str(item["app_id"])
        case_id = normalize_case_id(str(item["case_id"]), TASK_VALID)
        cases.append({"case_id": case_id, "app_id": app_id})
    if len(cases) != 20:
        raise ValueError(f"Expected 20 selected valid cases from {path}, got {len(cases)}")
    return cases


def fetch_invalid_case_pool(conn) -> List[Dict[str, Any]]:
    ensure_conn_alive(conn)
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                page_id,
                app_name,
                category,
                bug_description_en,
                url
            FROM t_invalid_app_info
            ORDER BY page_id
            """
        )
        rows = cursor.fetchall()
    out = []
    for row in rows:
        page_id = int(row["page_id"])
        out.append(
            {
                "case_id": f"B{page_id:03d}",
                "page_id": page_id,
                "app_name": str(row.get("app_name") or ""),
                "category": str(row.get("category") or ""),
                "bug_description": str(row.get("bug_description_en") or ""),
                "url": str(row.get("url") or ""),
            }
        )
    return out


def stratified_invalid_selection(rows: Sequence[Dict[str, Any]], limit: int = 20) -> List[Dict[str, Any]]:
    by_category: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_category.setdefault(str(row["category"]), []).append(row)
    for items in by_category.values():
        items.sort(key=lambda item: int(item["page_id"]))

    total = len(rows)
    if total == 0:
        raise ValueError("No invalid cases available for selection")

    quotas: Dict[str, int] = {}
    remainders: List[Tuple[float, str]] = []
    for category, items in sorted(by_category.items()):
        exact = len(items) * limit / total
        base = int(exact)
        quotas[category] = base
        remainders.append((exact - base, category))

    remaining = limit - sum(quotas.values())
    for _, category in sorted(remainders, key=lambda item: (-item[0], item[1]))[:remaining]:
        quotas[category] += 1

    selected: List[Dict[str, Any]] = []
    for category in sorted(by_category):
        selected.extend(by_category[category][:quotas[category]])
    selected.sort(key=lambda item: int(item["page_id"]))
    if len(selected) != limit:
        raise ValueError(f"Expected {limit} invalid cases, got {len(selected)}")
    return selected


def context_metadata_for_case(conn, task: str, case_id: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ctx = build_context(case_id, "valid" if task == TASK_VALID else "invalid")
    payload: Dict[str, Any] = {
        "task": task,
        "case_id": case_id,
        "target_id": ctx.target_id,
        "global": ctx.global_text,
        "component": ctx.component_text,
        "adjacent": ctx.adjacent_text,
        "restrictive": ctx.restrictive_text,
        "guiding": ctx.guiding_text,
        "resource_ids": ctx.resource_ids,
    }
    if extra:
        payload.update(extra)
    if task == TASK_INVALID and "bug_description" not in payload:
        page_id = int(case_id[1:])
        ensure_conn_alive(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT app_name, category, bug_description_en, url
                FROM t_invalid_app_info
                WHERE page_id = %s
                """,
                [page_id],
            )
            row = cursor.fetchone()
        if row:
            payload.update(
                {
                    "app_name": row.get("app_name"),
                    "category": row.get("category"),
                    "bug_description": row.get("bug_description_en"),
                    "url": row.get("url"),
                }
            )
    return payload


def create_tables(conn, case_table: str, task_table: str, source_payload_table: str) -> None:
    case_table = ensure_identifier(case_table)
    task_table = ensure_identifier(task_table)
    source_payload_table = ensure_identifier(source_payload_table)
    ensure_conn_alive(conn)
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {case_table} (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                rq3_task VARCHAR(32) NOT NULL,
                case_id VARCHAR(64) NOT NULL,
                target_id VARCHAR(255) NOT NULL,
                app_or_bug_name VARCHAR(255) NOT NULL DEFAULT '',
                category VARCHAR(64) NOT NULL DEFAULT '',
                description TEXT NULL,
                selection_source VARCHAR(1024) NOT NULL,
                selection_reason TEXT NULL,
                context_json LONGTEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_case (rq3_task, case_id),
                KEY idx_case_task (rq3_task),
                KEY idx_case_category (category)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {task_table} (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                rq3_task VARCHAR(32) NOT NULL,
                case_id VARCHAR(64) NOT NULL,
                mode VARCHAR(32) NOT NULL,
                tester_id VARCHAR(32) NOT NULL,
                tester_label VARCHAR(64) NOT NULL,
                tester_background TEXT NOT NULL,
                source_model VARCHAR(128) NOT NULL DEFAULT '',
                prompt_setting VARCHAR(32) NOT NULL DEFAULT '',
                prompt_variant VARCHAR(64) NOT NULL DEFAULT '',
                source_scope VARCHAR(64) NOT NULL,
                source_group_count INT NOT NULL DEFAULT 0,
                source_input_count INT NOT NULL DEFAULT 0,
                source_success_count INT NOT NULL DEFAULT 0,
                source_parse_success_count INT NOT NULL DEFAULT 0,
                source_curated_ids_json LONGTEXT NULL,
                source_hash CHAR(64) NULL,
                source_candidates_json LONGTEXT NOT NULL,
                context_json LONGTEXT NOT NULL,
                prompt_version VARCHAR(64) NOT NULL,
                prompt_hash CHAR(64) NULL,
                judge_model VARCHAR(128) NULL,
                logical_judge_model VARCHAR(128) NULL,
                request_text LONGTEXT NULL,
                raw_response LONGTEXT NULL,
                usage_json LONGTEXT NULL,
                generated_input_json LONGTEXT NULL,
                rationale TEXT NULL,
                change_analysis_json LONGTEXT NULL,
                future_recommendations_json LONGTEXT NULL,
                parse_success TINYINT NOT NULL DEFAULT 0,
                parse_error TEXT NULL,
                eval_success TINYINT NULL,
                elapsed_ms BIGINT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                error_message TEXT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_revision_slot (
                    rq3_task,
                    case_id,
                    mode,
                    tester_id,
                    source_model,
                    prompt_setting,
                    prompt_version
                ),
                KEY idx_revision_status (status),
                KEY idx_revision_task_case (rq3_task, case_id),
                KEY idx_revision_mode (mode),
                KEY idx_revision_tester (tester_id)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {source_payload_table} (
                source_hash CHAR(64) PRIMARY KEY,
                rq3_task VARCHAR(32) NOT NULL,
                case_id VARCHAR(64) NOT NULL,
                mode VARCHAR(32) NOT NULL,
                source_model VARCHAR(128) NOT NULL DEFAULT '',
                prompt_setting VARCHAR(32) NOT NULL DEFAULT '',
                prompt_variant VARCHAR(64) NOT NULL DEFAULT '',
                source_scope VARCHAR(64) NOT NULL,
                source_group_count INT NOT NULL DEFAULT 0,
                source_input_count INT NOT NULL DEFAULT 0,
                source_success_count INT NOT NULL DEFAULT 0,
                source_parse_success_count INT NOT NULL DEFAULT 0,
                source_curated_ids_json LONGTEXT NULL,
                source_candidates_json LONGTEXT NOT NULL,
                context_json LONGTEXT NOT NULL,
                prompt_version VARCHAR(64) NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                KEY idx_revision_source_case (rq3_task, case_id),
                KEY idx_revision_source_mode (mode),
                KEY idx_revision_source_model_setting (source_model, prompt_setting)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """
        )
        cursor.execute(f"SHOW COLUMNS FROM {case_table} LIKE %s", ["selection_source"])
        selection_source_column = cursor.fetchone()
        selection_source_type = ""
        if isinstance(selection_source_column, dict):
            selection_source_type = str(selection_source_column.get("Type") or "").lower()
        elif selection_source_column:
            selection_source_type = str(selection_source_column[1]).lower()
        if "varchar(1024)" not in selection_source_type:
            cursor.execute(
                f"""
                ALTER TABLE {case_table}
                MODIFY selection_source VARCHAR(1024) NOT NULL
                """
            )
        for column, ddl in [
            ("source_hash", "CHAR(64) NULL"),
            ("change_analysis_json", "LONGTEXT NULL"),
            ("future_recommendations_json", "LONGTEXT NULL"),
        ]:
            cursor.execute(f"SHOW COLUMNS FROM {task_table} LIKE %s", [column])
            if cursor.fetchone() is None:
                cursor.execute(f"ALTER TABLE {task_table} ADD COLUMN {column} {ddl}")
    conn.commit()


@dataclass(frozen=True)
class SourceRows:
    groups: List[Dict[str, Any]]
    total_inputs: int
    parse_success_count: int
    success_count: int
    curated_ids: List[int]


def row_payload(row: Optional[Dict[str, Any]], index_name: str, index_value: int) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        index_name: index_value,
        "curated_id": None,
        "parse_success": 0,
        "eval_success": None,
        "generation_status": "missing_generation",
        "text_inputs": None,
    }
    if row is None:
        return base
    base.update(
        {
            "curated_id": int(row["curated_id"]),
            "parse_success": int(row.get("parse_success") or 0),
            "eval_success": row.get("eval_success"),
            "generation_status": str(row.get("generation_status") or ""),
            "completion_status": str(row.get("completion_status") or ""),
            "curation_status": str(row.get("curation_status") or ""),
        }
    )
    if int(row.get("parse_success") or 0) == 1 and row.get("parsed_input_json") is not None:
        try:
            parsed = normalize_json_value(row["parsed_input_json"])
            base["text_inputs"] = parsed if isinstance(parsed, dict) else None
        except Exception as exc:
            base["parse_success"] = 0
            base["generation_status"] = "json_decode_error"
            base["parse_error"] = f"{type(exc).__name__}: {exc}"[:500]
    else:
        base["parse_error"] = str(row.get("parse_error") or "")[:500]
    return base


def fetch_generation_rows(
        conn,
        source_table: str,
        *,
        rq3_task: str,
        case_id: str,
        models: Sequence[str],
        settings: Sequence[str],
) -> Dict[Tuple[str, str, int], Dict[str, Any]]:
    source_table = ensure_identifier(source_table)
    placeholders_models = ",".join(["%s"] * len(models))
    placeholders_settings = ",".join(["%s"] * len(settings))
    if rq3_task == TASK_VALID:
        experiment = EXPERIMENT_RQ1_VALID
        task_name = VALID_TASK
        index_col = "attempt_id"
        index_filter = "attempt_id IN (1, 2, 3)"
        indices = VALID_ATTEMPTS
    elif rq3_task == TASK_INVALID:
        experiment = EXPERIMENT_RQ1_INVALID
        task_name = INVALID_TASK
        index_col = "invalid_index"
        index_filter = "invalid_index BETWEEN 1 AND 30"
        indices = INVALID_INDICES
    else:
        raise ValueError(f"Unsupported rq3_task: {rq3_task}")

    sql = f"""
        SELECT
            curated_id,
            canonical_model,
            prompt_setting,
            prompt_variant,
            {index_col} AS generation_index,
            parse_success,
            parsed_input_json,
            parse_error,
            eval_success,
            curation_status,
            completion_status,
            generation_status
        FROM {source_table}
        WHERE experiment = %s
          AND task = %s
          AND case_id = %s
          AND canonical_model IN ({placeholders_models})
          AND prompt_setting IN ({placeholders_settings})
          AND feedback_mode = 'none'
          AND tau = -1
          AND combo_index = -1
          AND {index_filter}
        ORDER BY curated_id DESC
    """
    params = [experiment, task_name, case_id, *models, *settings]
    ensure_conn_alive(conn)
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    out: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    allowed_indices = set(int(i) for i in indices)
    for row in rows:
        model = str(row["canonical_model"])
        setting = str(row["prompt_setting"])
        if str(row["prompt_variant"]) != prompt_variant_for_setting(setting):
            continue
        generation_index = int(row["generation_index"])
        if generation_index not in allowed_indices:
            continue
        out.setdefault((model, setting, generation_index), row)
    return out


def fetch_generation_rows_for_cases(
        conn,
        source_table: str,
        *,
        rq3_task: str,
        case_ids: Sequence[str],
        models: Sequence[str],
        settings: Sequence[str],
) -> Dict[str, Dict[Tuple[str, str, int], Dict[str, Any]]]:
    source_table = ensure_identifier(source_table)
    if not case_ids:
        return {}
    placeholders_cases = ",".join(["%s"] * len(case_ids))
    placeholders_models = ",".join(["%s"] * len(models))
    placeholders_settings = ",".join(["%s"] * len(settings))
    if rq3_task == TASK_VALID:
        experiment = EXPERIMENT_RQ1_VALID
        task_name = VALID_TASK
        index_col = "attempt_id"
        index_filter = "attempt_id IN (1, 2, 3)"
        indices = VALID_ATTEMPTS
    elif rq3_task == TASK_INVALID:
        experiment = EXPERIMENT_RQ1_INVALID
        task_name = INVALID_TASK
        index_col = "invalid_index"
        index_filter = "invalid_index BETWEEN 1 AND 30"
        indices = INVALID_INDICES
    else:
        raise ValueError(f"Unsupported rq3_task: {rq3_task}")

    sql = f"""
        SELECT
            curated_id,
            case_id,
            canonical_model,
            prompt_setting,
            prompt_variant,
            {index_col} AS generation_index,
            parse_success,
            parsed_input_json,
            parse_error,
            eval_success,
            curation_status,
            completion_status,
            generation_status
        FROM {source_table}
        WHERE experiment = %s
          AND task = %s
          AND case_id IN ({placeholders_cases})
          AND canonical_model IN ({placeholders_models})
          AND prompt_setting IN ({placeholders_settings})
          AND feedback_mode = 'none'
          AND tau = -1
          AND combo_index = -1
          AND {index_filter}
        ORDER BY curated_id DESC
    """
    params = [experiment, task_name, *case_ids, *models, *settings]
    ensure_conn_alive(conn)
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    out: Dict[str, Dict[Tuple[str, str, int], Dict[str, Any]]] = {
        case_id: {} for case_id in case_ids
    }
    allowed_indices = set(int(i) for i in indices)
    for row in rows:
        case_id = str(row["case_id"])
        model = str(row["canonical_model"])
        setting = str(row["prompt_setting"])
        if str(row["prompt_variant"]) != prompt_variant_for_setting(setting):
            continue
        generation_index = int(row["generation_index"])
        if generation_index not in allowed_indices:
            continue
        out.setdefault(case_id, {}).setdefault((model, setting, generation_index), row)
    return out


def build_source_rows(
        conn,
        source_table: str,
        *,
        rq3_task: str,
        case_id: str,
        models: Sequence[str],
        settings: Sequence[str],
        mode: str,
        source_model: str = "",
        prompt_setting: str = "",
        generation_rows: Optional[Dict[Tuple[str, str, int], Dict[str, Any]]] = None,
) -> SourceRows:
    all_rows = generation_rows
    if all_rows is None:
        all_rows = fetch_generation_rows(
            conn,
            source_table,
            rq3_task=rq3_task,
            case_id=case_id,
            models=models,
            settings=settings,
        )
    if mode == MODE_MODIFICATION:
        if source_model and prompt_setting:
            selected_models = [source_model]
            selected_settings = [prompt_setting]
        else:
            selected_models = list(models)
            selected_settings = list(settings)
    elif mode == MODE_REGENERATION:
        selected_models = list(models)
        selected_settings = list(settings)
    else:
        raise ValueError(f"Unsupported mode: {mode}")

    if rq3_task == TASK_VALID:
        indices = VALID_ATTEMPTS
        index_name = "attempt_id"
    elif rq3_task == TASK_INVALID:
        indices = INVALID_INDICES
        index_name = "invalid_index"
    else:
        raise ValueError(f"Unsupported rq3_task: {rq3_task}")

    groups: List[Dict[str, Any]] = []
    total_inputs = 0
    parse_success_count = 0
    success_count = 0
    curated_ids: List[int] = []
    for model in selected_models:
        for setting in selected_settings:
            items: List[Dict[str, Any]] = []
            for idx in indices:
                row = all_rows.get((model, setting, int(idx)))
                payload = row_payload(row, index_name, int(idx))
                total_inputs += 1
                parse_success_count += 1 if int(payload.get("parse_success") or 0) == 1 else 0
                success_count += 1 if int(payload.get("eval_success") or 0) == 1 else 0
                if payload.get("curated_id") is not None:
                    curated_ids.append(int(payload["curated_id"]))
                items.append(payload)
            groups.append(
                {
                    "model": model,
                    "prompt_setting": setting,
                    "prompt_variant": prompt_variant_for_setting(setting),
                    "items": items,
                    "parse_success_count": sum(
                        1 for item in items if int(item.get("parse_success") or 0) == 1
                    ),
                    "success_count": sum(
                        1 for item in items if int(item.get("eval_success") or 0) == 1
                    ),
                }
            )
    return SourceRows(
        groups=groups,
        total_inputs=total_inputs,
        parse_success_count=parse_success_count,
        success_count=success_count,
        curated_ids=curated_ids,
    )


def insert_case_selection(
        conn,
        case_table: str,
        *,
        rq3_task: str,
        case_id: str,
        context_json: Dict[str, Any],
        selection_source: str,
        selection_reason: str,
) -> None:
    case_table = ensure_identifier(case_table)
    target_id = str(context_json.get("target_id") or "")
    app_or_bug_name = str(
        context_json.get("app_id")
        or context_json.get("app_name")
        or context_json.get("global")
        or ""
    )[:255]
    category = str(context_json.get("category") or "")
    description = str(context_json.get("bug_description") or "")
    ensure_conn_alive(conn)
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {case_table} (
                rq3_task,
                case_id,
                target_id,
                app_or_bug_name,
                category,
                description,
                selection_source,
                selection_reason,
                context_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                target_id = VALUES(target_id),
                app_or_bug_name = VALUES(app_or_bug_name),
                category = VALUES(category),
                description = VALUES(description),
                selection_source = VALUES(selection_source),
                selection_reason = VALUES(selection_reason),
                context_json = VALUES(context_json),
                updated_at = CURRENT_TIMESTAMP
            """,
            [
                rq3_task,
                case_id,
                target_id,
                app_or_bug_name,
                category,
                description,
                selection_source,
                selection_reason,
                json_dumps(context_json),
            ],
        )


def revision_task_upsert_sql(task_table: str) -> str:
    task_table = ensure_identifier(task_table)
    return f"""
        INSERT INTO {task_table} (
            rq3_task,
            case_id,
            mode,
            tester_id,
            tester_label,
            tester_background,
            source_model,
            prompt_setting,
            prompt_variant,
            source_scope,
            source_group_count,
            source_input_count,
            source_success_count,
            source_parse_success_count,
            source_curated_ids_json,
            source_hash,
            source_candidates_json,
            context_json,
            prompt_version,
            status
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON DUPLICATE KEY UPDATE
            tester_label = VALUES(tester_label),
            tester_background = VALUES(tester_background),
            prompt_variant = VALUES(prompt_variant),
            source_scope = VALUES(source_scope),
            source_group_count = VALUES(source_group_count),
            source_input_count = VALUES(source_input_count),
            source_success_count = VALUES(source_success_count),
            source_parse_success_count = VALUES(source_parse_success_count),
            source_curated_ids_json = VALUES(source_curated_ids_json),
            source_hash = VALUES(source_hash),
            source_candidates_json = VALUES(source_candidates_json),
            context_json = VALUES(context_json),
            status = IF(status IN ('pending', 'source_ready', 'source_incomplete'), VALUES(status), status),
            updated_at = CURRENT_TIMESTAMP
    """


def revision_task_params(
        *,
        rq3_task: str,
        case_id: str,
        mode: str,
        tester_num: int,
        source_model: str,
        prompt_setting: str,
        source_rows: SourceRows,
        context_json: Dict[str, Any],
        prompt_version: str,
        source_hash: str = "",
        inline_source_candidates: bool = True,
) -> List[Any]:
    tester_label, tester_background = tester_background_for_num(tester_num)
    tester_id = tester_id_for_num(tester_num)
    prompt_variant = prompt_variant_for_setting(prompt_setting) if prompt_setting else ""
    if mode == MODE_MODIFICATION and source_model and prompt_setting:
        source_scope = "model_setting_all_attempts"
    elif mode == MODE_MODIFICATION:
        source_scope = "all_models_settings_history_for_modification"
    else:
        source_scope = "all_models_settings_history"
    status = "source_ready" if source_rows.parse_success_count > 0 else "source_incomplete"
    return [
        rq3_task,
        case_id,
        mode,
        tester_id,
        tester_label,
        tester_background,
        source_model,
        prompt_setting,
        prompt_variant,
        source_scope,
        len(source_rows.groups),
        source_rows.total_inputs,
        source_rows.success_count,
        source_rows.parse_success_count,
        json_dumps(source_rows.curated_ids),
        source_hash,
        json_dumps_for_db(source_rows.groups) if inline_source_candidates else "[]",
        json_dumps(context_json),
        prompt_version,
        status,
    ]


def upsert_revision_task(
        conn,
        task_table: str,
        *,
        rq3_task: str,
        case_id: str,
        mode: str,
        tester_num: int,
        source_model: str,
        prompt_setting: str,
        source_rows: SourceRows,
        context_json: Dict[str, Any],
        prompt_version: str,
) -> None:
    params = revision_task_params(
        rq3_task=rq3_task,
        case_id=case_id,
        mode=mode,
        tester_num=tester_num,
        source_model=source_model,
        prompt_setting=prompt_setting,
        source_rows=source_rows,
        context_json=context_json,
        prompt_version=prompt_version,
    )
    ensure_conn_alive(conn)
    with conn.cursor() as cursor:
        cursor.execute(revision_task_upsert_sql(task_table), params)


def upsert_revision_tasks_many(
        conn,
        task_table: str,
        params_list: Sequence[Sequence[Any]],
        *,
        batch_size: int = 24,
) -> None:
    if not params_list:
        return
    sql = revision_task_upsert_sql(task_table)
    for start in range(0, len(params_list), batch_size):
        batch = params_list[start:start + batch_size]
        for attempt in range(1, 4):
            try:
                ensure_conn_alive(conn)
                with conn.cursor() as cursor:
                    cursor.executemany(sql, batch)
                break
            except pymysql.err.OperationalError:
                try:
                    conn.rollback()
                except Exception:
                    pass
                try:
                    conn.ping(reconnect=True)
                except Exception:
                    pass
                if attempt >= 3:
                    raise
                time.sleep(2.0 * attempt)


def source_payload_upsert_sql(source_payload_table: str) -> str:
    source_payload_table = ensure_identifier(source_payload_table)
    return f"""
        INSERT INTO {source_payload_table} (
            source_hash,
            rq3_task,
            case_id,
            mode,
            source_model,
            prompt_setting,
            prompt_variant,
            source_scope,
            source_group_count,
            source_input_count,
            source_success_count,
            source_parse_success_count,
            source_curated_ids_json,
            source_candidates_json,
            context_json,
            prompt_version
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON DUPLICATE KEY UPDATE
            source_group_count = VALUES(source_group_count),
            source_input_count = VALUES(source_input_count),
            source_success_count = VALUES(source_success_count),
            source_parse_success_count = VALUES(source_parse_success_count),
            source_curated_ids_json = VALUES(source_curated_ids_json),
            source_candidates_json = VALUES(source_candidates_json),
            context_json = VALUES(context_json),
            updated_at = CURRENT_TIMESTAMP
    """


def source_payload_params(
        *,
        rq3_task: str,
        case_id: str,
        mode: str,
        source_model: str,
        prompt_setting: str,
        source_rows: SourceRows,
        context_json: Dict[str, Any],
        prompt_version: str,
) -> Tuple[str, List[Any]]:
    prompt_variant = prompt_variant_for_setting(prompt_setting) if prompt_setting else ""
    if mode == MODE_MODIFICATION and source_model and prompt_setting:
        source_scope = "model_setting_all_attempts"
    elif mode == MODE_MODIFICATION:
        source_scope = "all_models_settings_history_for_modification"
    else:
        source_scope = "all_models_settings_history"
    source_candidates_json = json_dumps_for_db(source_rows.groups)
    context_json_text = json_dumps(context_json)
    hash_payload = json_dumps(
        {
            "rq3_task": rq3_task,
            "case_id": case_id,
            "mode": mode,
            "source_model": source_model,
            "prompt_setting": prompt_setting,
            "prompt_version": prompt_version,
            "source_candidates_json": source_candidates_json,
        }
    )
    source_hash = hashlib.sha256(hash_payload.encode("utf-8")).hexdigest()
    return source_hash, [
        source_hash,
        rq3_task,
        case_id,
        mode,
        source_model,
        prompt_setting,
        prompt_variant,
        source_scope,
        len(source_rows.groups),
        source_rows.total_inputs,
        source_rows.success_count,
        source_rows.parse_success_count,
        json_dumps(source_rows.curated_ids),
        source_candidates_json,
        context_json_text,
        prompt_version,
    ]


def upsert_source_payloads_many(
        conn,
        source_payload_table: str,
        params_list: Sequence[Sequence[Any]],
        *,
        batch_size: int = 1,
) -> None:
    if not params_list:
        return
    sql = source_payload_upsert_sql(source_payload_table)
    for start in range(0, len(params_list), batch_size):
        batch = params_list[start:start + batch_size]
        for attempt in range(1, 4):
            try:
                ensure_conn_alive(conn)
                with conn.cursor() as cursor:
                    cursor.executemany(sql, batch)
                break
            except pymysql.err.OperationalError:
                try:
                    conn.rollback()
                except Exception:
                    pass
                try:
                    conn.ping(reconnect=True)
                except Exception:
                    pass
                if attempt >= 3:
                    raise
                time.sleep(2.0 * attempt)


def seed_tasks(
        conn,
        *,
        case_table: str,
        task_table: str,
        source_payload_table: str,
        source_table: str,
        selected_valid_json: Path,
        rq3_tasks: Sequence[str],
        valid_case_ids: Sequence[str],
        invalid_case_ids: Sequence[str],
        models: Sequence[str],
        settings: Sequence[str],
        modes: Sequence[str],
        tester_nums: Sequence[int],
        prompt_version: str,
        store_source_payload: bool,
) -> Dict[str, int]:
    if TASK_VALID in rq3_tasks:
        valid_cases_all = load_selected_valid_cases(selected_valid_json)
        valid_case_set = set(valid_case_ids) if valid_case_ids else {item["case_id"] for item in valid_cases_all}
        valid_cases = [item for item in valid_cases_all if item["case_id"] in valid_case_set]
    else:
        valid_cases = []

    if TASK_INVALID in rq3_tasks:
        if invalid_case_ids:
            invalid_selected = [
                item
                for item in fetch_invalid_case_pool(conn)
                if item["case_id"] in set(invalid_case_ids)
            ]
        else:
            invalid_selected = stratified_invalid_selection(fetch_invalid_case_pool(conn), limit=20)
    else:
        invalid_selected = []

    counts = {
        "valid_cases": len(valid_cases),
        "invalid_cases": len(invalid_selected),
        "case_rows": 0,
        "task_rows": 0,
    }
    valid_generation_rows_by_case = fetch_generation_rows_for_cases(
        conn,
        source_table,
        rq3_task=TASK_VALID,
        case_ids=[item["case_id"] for item in valid_cases],
        models=models,
        settings=settings,
    )
    invalid_generation_rows_by_case = fetch_generation_rows_for_cases(
        conn,
        source_table,
        rq3_task=TASK_INVALID,
        case_ids=[item["case_id"] for item in invalid_selected],
        models=models,
        settings=settings,
    )

    for item in valid_cases:
        case_id = item["case_id"]
        task_rows_before_case = counts["task_rows"]
        case_task_params: List[List[Any]] = []
        case_source_params_by_hash: Dict[str, List[Any]] = {}
        log(f"[SEED_CASE] task={TASK_VALID} case_id={case_id}")
        context_json = context_metadata_for_case(
            conn,
            TASK_VALID,
            case_id,
            {"app_id": item["app_id"]},
        )
        generation_rows = valid_generation_rows_by_case.get(case_id, {})
        insert_case_selection(
            conn,
            case_table,
            rq3_task=TASK_VALID,
            case_id=case_id,
            context_json=context_json,
            selection_source=str(selected_valid_json),
            selection_reason="The same 20 valid UI pages used in the prior RQ3 tester modification table.",
        )
        counts["case_rows"] += 1
        for tester_num in tester_nums:
            if MODE_REGENERATION in modes:
                source_rows = build_source_rows(
                    conn,
                    source_table,
                    rq3_task=TASK_VALID,
                    case_id=case_id,
                    models=models,
                    settings=settings,
                    mode=MODE_REGENERATION,
                    generation_rows=generation_rows,
                )
                source_hash = ""
                if store_source_payload:
                    source_hash, source_params = source_payload_params(
                        rq3_task=TASK_VALID,
                        case_id=case_id,
                        mode=MODE_REGENERATION,
                        source_model="",
                        prompt_setting="",
                        source_rows=source_rows,
                        context_json=context_json,
                        prompt_version=prompt_version,
                    )
                    case_source_params_by_hash[source_hash] = source_params
                case_task_params.append(revision_task_params(
                    rq3_task=TASK_VALID,
                    case_id=case_id,
                    mode=MODE_REGENERATION,
                    tester_num=tester_num,
                    source_model="",
                    prompt_setting="",
                    source_rows=source_rows,
                    context_json=context_json,
                    prompt_version=prompt_version,
                    source_hash=source_hash,
                    inline_source_candidates=False,
                ))
                counts["task_rows"] += 1
            if MODE_MODIFICATION in modes:
                source_rows = build_source_rows(
                    conn,
                    source_table,
                    rq3_task=TASK_VALID,
                    case_id=case_id,
                    models=models,
                    settings=settings,
                    mode=MODE_MODIFICATION,
                    generation_rows=generation_rows,
                )
                source_hash = ""
                if store_source_payload:
                    source_hash, source_params = source_payload_params(
                        rq3_task=TASK_VALID,
                        case_id=case_id,
                        mode=MODE_MODIFICATION,
                        source_model="",
                        prompt_setting="",
                        source_rows=source_rows,
                        context_json=context_json,
                        prompt_version=prompt_version,
                    )
                    case_source_params_by_hash[source_hash] = source_params
                case_task_params.append(revision_task_params(
                    rq3_task=TASK_VALID,
                    case_id=case_id,
                    mode=MODE_MODIFICATION,
                    tester_num=tester_num,
                    source_model="",
                    prompt_setting="",
                    source_rows=source_rows,
                    context_json=context_json,
                    prompt_version=prompt_version,
                    source_hash=source_hash,
                    inline_source_candidates=False,
                ))
                counts["task_rows"] += 1
        if store_source_payload:
            upsert_source_payloads_many(conn, source_payload_table, list(case_source_params_by_hash.values()))
        upsert_revision_tasks_many(conn, task_table, case_task_params)
        conn.commit()
        log(
            f"[SEED_CASE_DONE] task={TASK_VALID} case_id={case_id} "
            f"case_tasks={counts['task_rows'] - task_rows_before_case} "
            f"total_tasks={counts['task_rows']}"
        )

    for item in invalid_selected:
        case_id = item["case_id"]
        task_rows_before_case = counts["task_rows"]
        case_task_params = []
        case_source_params_by_hash = {}
        log(f"[SEED_CASE] task={TASK_INVALID} case_id={case_id} category={item.get('category')}")
        context_json = context_metadata_for_case(conn, TASK_INVALID, case_id, item)
        generation_rows = invalid_generation_rows_by_case.get(case_id, {})
        insert_case_selection(
            conn,
            case_table,
            rq3_task=TASK_INVALID,
            case_id=case_id,
            context_json=context_json,
            selection_source="stratified_invalid_bug_selection",
            selection_reason="Stratified sample of 20 bugs preserving bug-category coverage from the 37 RQ1.2 bugs.",
        )
        counts["case_rows"] += 1
        for tester_num in tester_nums:
            if MODE_REGENERATION in modes:
                source_rows = build_source_rows(
                    conn,
                    source_table,
                    rq3_task=TASK_INVALID,
                    case_id=case_id,
                    models=models,
                    settings=settings,
                    mode=MODE_REGENERATION,
                    generation_rows=generation_rows,
                )
                source_hash = ""
                if store_source_payload:
                    source_hash, source_params = source_payload_params(
                        rq3_task=TASK_INVALID,
                        case_id=case_id,
                        mode=MODE_REGENERATION,
                        source_model="",
                        prompt_setting="",
                        source_rows=source_rows,
                        context_json=context_json,
                        prompt_version=prompt_version,
                    )
                    case_source_params_by_hash[source_hash] = source_params
                case_task_params.append(revision_task_params(
                    rq3_task=TASK_INVALID,
                    case_id=case_id,
                    mode=MODE_REGENERATION,
                    tester_num=tester_num,
                    source_model="",
                    prompt_setting="",
                    source_rows=source_rows,
                    context_json=context_json,
                    prompt_version=prompt_version,
                    source_hash=source_hash,
                    inline_source_candidates=False,
                ))
                counts["task_rows"] += 1
            if MODE_MODIFICATION in modes:
                source_rows = build_source_rows(
                    conn,
                    source_table,
                    rq3_task=TASK_INVALID,
                    case_id=case_id,
                    models=models,
                    settings=settings,
                    mode=MODE_MODIFICATION,
                    generation_rows=generation_rows,
                )
                source_hash = ""
                if store_source_payload:
                    source_hash, source_params = source_payload_params(
                        rq3_task=TASK_INVALID,
                        case_id=case_id,
                        mode=MODE_MODIFICATION,
                        source_model="",
                        prompt_setting="",
                        source_rows=source_rows,
                        context_json=context_json,
                        prompt_version=prompt_version,
                    )
                    case_source_params_by_hash[source_hash] = source_params
                case_task_params.append(revision_task_params(
                    rq3_task=TASK_INVALID,
                    case_id=case_id,
                    mode=MODE_MODIFICATION,
                    tester_num=tester_num,
                    source_model="",
                    prompt_setting="",
                    source_rows=source_rows,
                    context_json=context_json,
                    prompt_version=prompt_version,
                    source_hash=source_hash,
                    inline_source_candidates=False,
                ))
                counts["task_rows"] += 1
        if store_source_payload:
            upsert_source_payloads_many(conn, source_payload_table, list(case_source_params_by_hash.values()))
        upsert_revision_tasks_many(conn, task_table, case_task_params)
        conn.commit()
        log(
            f"[SEED_CASE_DONE] task={TASK_INVALID} case_id={case_id} "
            f"case_tasks={counts['task_rows'] - task_rows_before_case} "
            f"total_tasks={counts['task_rows']}"
        )
    conn.commit()
    return counts


def build_system_prompt(row: Dict[str, Any]) -> str:
    return (
        f"You are {row['tester_label']}: {row['tester_background']}\n"
        "You are acting as a professional Android GUI tester. "
        "Your job is to produce final text inputs for the given UI components. "
        "You must return JSON only, without Markdown."
    )


def task_goal_text(row: Dict[str, Any]) -> str:
    if row["rq3_task"] == TASK_VALID:
        return (
            "Goal: generate VALID text inputs that are practical and likely to pass "
            "through the current text-input page."
        )
    if row["rq3_task"] == TASK_INVALID:
        context = json.loads(row["context_json"])
        bug = context.get("bug_description") or ""
        category = context.get("category") or ""
        return (
            "Goal: generate INVALID or bug-triggering text inputs that are likely to "
            "reveal the target bug. "
            f"Bug category: {category}. Bug description: {bug}"
        )
    raise ValueError(f"Unsupported task: {row['rq3_task']}")


def mode_instruction_text(row: Dict[str, Any]) -> str:
    if row["mode"] == MODE_MODIFICATION:
        return (
            "Mode: modification. You are given LLM-generated candidate inputs and "
            "their execution results. Modify them into ONE final test input. Use the "
            "execution results as evidence: preserve useful values, correct malformed "
            "or weak values, and make the final input more likely to satisfy the goal."
        )
    if row["mode"] == MODE_REGENERATION:
        return (
            "Mode: tester-guided regeneration. You are given prior LLM-generated "
            "candidate inputs and execution results as empirical hints. Use them to "
            "understand what worked or failed, but create ONE new final test input "
            "from your tester perspective."
        )
    raise ValueError(f"Unsupported mode: {row['mode']}")


def build_request_text(
        row: Dict[str, Any],
        *,
        conn=None,
        source_table: str = SOURCE_TABLE_DEFAULT,
        models: Sequence[str] = FINAL_MODELS,
        settings: Sequence[str] = PROMPT_SETTINGS,
) -> str:
    context = json.loads(row["context_json"])
    source = json_loads_from_db(row["source_candidates_json"])
    if not source and conn is not None:
        rebuilt_source = build_source_rows(
            conn,
            source_table,
            rq3_task=row["rq3_task"],
            case_id=row["case_id"],
            models=models,
            settings=settings,
            mode=row["mode"],
            source_model=str(row.get("source_model") or ""),
            prompt_setting=str(row.get("prompt_setting") or ""),
        )
        source = rebuilt_source.groups
    resource_ids = context["resource_ids"]
    payload = {
        "case_id": row["case_id"],
        "task": row["rq3_task"],
        "mode": row["mode"],
        "required_resource_ids": resource_ids,
        "ui_context": {
            "global": context.get("global"),
            "components": context.get("component"),
            "adjacent_labels": context.get("adjacent"),
            "bug_category": context.get("category"),
            "bug_description": context.get("bug_description"),
        },
        "source_summary": {
            "source_model": row.get("source_model") or None,
            "prompt_setting": row.get("prompt_setting") or None,
            "source_scope": row.get("source_scope"),
            "source_group_count": int(row.get("source_group_count") or 0),
            "source_input_count": int(row.get("source_input_count") or 0),
            "source_parse_success_count": int(row.get("source_parse_success_count") or 0),
            "source_success_count": int(row.get("source_success_count") or 0),
        },
        "source_candidates": source,
    }
    output_schema = {
        "text_inputs": {rid: "generated_value" for rid in resource_ids},
        "rationale": "overall tester rationale for the final input set",
        "change_analysis": [
            {
                "resource_id": "one required resource id",
                "source_problem": "what was weak, placeholder-like, malformed, missing, or already useful in the prior LLM candidates",
                "change_made": "how the tester changed or preserved the value; mention representative before/after values when available",
                "reason": "why this change improves page pass-through or bug revelation",
                "evidence_from_history": "which execution results or candidate patterns motivated this decision",
                "future_guideline": "how a tester should handle similar fields or bugs in future cases",
            }
        ],
        "future_recommendations": [
            "general reusable recommendation for similar UI fields, validation rules, or bug categories"
        ],
    }
    return (
        f"{build_system_prompt(row)}\n\n"
        f"{task_goal_text(row)}\n\n"
        f"{mode_instruction_text(row)}\n\n"
        "Rules:\n"
        "1. Output must include exactly the required resource IDs as keys under text_inputs.\n"
        "2. Do not omit any component and do not add extra component IDs.\n"
        "3. Use string values only.\n"
        "4. The change_analysis list must include one item per required resource ID.\n"
        "5. Explicitly explain concrete tester edits, such as replacing placeholder-like values "
        "(for example, \"example\") with realistic values (for example, a valid email address), "
        "or preserving a value when execution history shows it worked.\n"
        "6. For invalid/bug tasks, explain how the final value targets the bug category and why "
        "it is expected to stress the validation logic.\n"
        "7. Return JSON only. Do not include Markdown or code fences.\n\n"
        f"Required response shape:\n{json_dumps(output_schema)}\n\n"
        f"Input data:\n{json_dumps(payload)}"
    )


def parse_generated_input(
        raw_response: str,
        resource_ids: Sequence[str],
) -> Tuple[
    Optional[Dict[str, Any]],
    Optional[str],
    str,
    List[Dict[str, Any]],
    List[str],
]:
    try:
        obj = extract_json_object(raw_response)
        if isinstance(obj.get("text_inputs"), dict):
            text_inputs = obj["text_inputs"]
            rationale = str(obj.get("rationale") or "")
        else:
            text_inputs = obj
            rationale = str(obj.get("rationale") or "") if isinstance(obj, dict) else ""
        validate_resource_ids_exact_match(text_inputs, resource_ids)
        normalized = {key: str(text_inputs[key]) for key in resource_ids}

        raw_change_analysis = obj.get("change_analysis") if isinstance(obj, dict) else None
        change_analysis: List[Dict[str, Any]] = []
        if isinstance(raw_change_analysis, list):
            for item in raw_change_analysis:
                if isinstance(item, dict):
                    change_analysis.append(item)
        raw_recommendations = obj.get("future_recommendations") if isinstance(obj, dict) else None
        future_recommendations: List[str] = []
        if isinstance(raw_recommendations, list):
            future_recommendations = [str(item) for item in raw_recommendations]
        elif isinstance(raw_recommendations, str) and raw_recommendations.strip():
            future_recommendations = [raw_recommendations.strip()]

        return normalized, None, rationale, change_analysis, future_recommendations
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}", "", [], []


def pending_rows(
        conn,
        task_table: str,
        source_payload_table: str,
        *,
        tasks: Sequence[str],
        modes: Sequence[str],
        tester_ids: Sequence[str],
        case_ids: Sequence[str],
        prompt_version: str,
        limit: int,
        rerun: bool,
) -> List[Dict[str, Any]]:
    task_table = ensure_identifier(task_table)
    source_payload_table = ensure_identifier(source_payload_table)
    where = ["t.prompt_version = %s"]
    params: List[Any] = [prompt_version]
    if tasks:
        where.append("t.rq3_task IN (" + ",".join(["%s"] * len(tasks)) + ")")
        params.extend(tasks)
    if modes:
        where.append("t.mode IN (" + ",".join(["%s"] * len(modes)) + ")")
        params.extend(modes)
    if tester_ids:
        where.append("t.tester_id IN (" + ",".join(["%s"] * len(tester_ids)) + ")")
        params.extend(tester_ids)
    if case_ids:
        where.append("t.case_id IN (" + ",".join(["%s"] * len(case_ids)) + ")")
        params.extend(case_ids)
    if not rerun:
        where.append("t.status NOT IN ('success')")
    sql = f"""
        SELECT
            t.*,
            COALESCE(s.source_candidates_json, t.source_candidates_json) AS source_candidates_json
        FROM {task_table} t
        LEFT JOIN {source_payload_table} s
          ON t.source_hash = s.source_hash
        WHERE {" AND ".join(where)}
        ORDER BY
            FIELD(t.mode, 'regeneration', 'modification'),
            t.rq3_task,
            t.case_id,
            t.tester_id,
            t.source_model,
            t.prompt_setting,
            t.id
    """
    if limit > 0:
        sql += " LIMIT %s"
        params.append(limit)
    ensure_conn_alive(conn)
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def mark_row_running(conn, task_table: str, row_id: int, request_text: str, judge_model: str, logical_judge_model: str) -> None:
    task_table = ensure_identifier(task_table)
    digest = hashlib.sha256(request_text.encode("utf-8")).hexdigest()
    ensure_conn_alive(conn)
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE {task_table}
            SET status = 'running',
                request_text = %s,
                prompt_hash = %s,
                judge_model = %s,
                logical_judge_model = %s,
                error_message = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            [request_text, digest, judge_model, logical_judge_model, row_id],
        )
    conn.commit()


def update_generation_result(
        conn,
        task_table: str,
        *,
        row_id: int,
        status: str,
        raw_response: Optional[str],
        usage: Optional[Dict[str, Any]],
        generated_input: Optional[Dict[str, Any]],
        rationale: str,
        change_analysis: Sequence[Dict[str, Any]],
        future_recommendations: Sequence[str],
        parse_success: int,
        parse_error: Optional[str],
        elapsed_ms: int,
        error_message: Optional[str],
) -> None:
    task_table = ensure_identifier(task_table)
    ensure_conn_alive(conn)
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE {task_table}
            SET status = %s,
                raw_response = %s,
                usage_json = %s,
                generated_input_json = %s,
                rationale = %s,
                change_analysis_json = %s,
                future_recommendations_json = %s,
                parse_success = %s,
                parse_error = %s,
                elapsed_ms = %s,
                error_message = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            [
                status,
                raw_response,
                json_dumps(usage or {}),
                json_dumps(generated_input) if generated_input is not None else None,
                rationale,
                json_dumps(list(change_analysis or [])),
                json_dumps(list(future_recommendations or [])),
                parse_success,
                parse_error,
                elapsed_ms,
                error_message,
                row_id,
            ],
        )
    conn.commit()


def run_generation(
        conn,
        *,
        task_table: str,
        source_table: str,
        client: MustLabJudgeClient,
        rows: Sequence[Dict[str, Any]],
        models: Sequence[str],
        settings: Sequence[str],
        max_retries: int,
        retry_sleep_seconds: float,
        sleep_seconds: float,
        use_image: bool,
        judge_model: str,
        logical_judge_model: str,
        fail_fast: bool,
) -> Dict[str, int]:
    counts = {"attempted": 0, "success": 0, "failed": 0}
    for index, row in enumerate(rows, start=1):
        row_id = int(row["id"])
        request_text = build_request_text(
            row,
            conn=conn,
            source_table=source_table,
            models=models,
            settings=settings,
        )
        context = json.loads(row["context_json"])
        resource_ids = context["resource_ids"]
        log(
            f"[RUN_START] {index}/{len(rows)} id={row_id} task={row['rq3_task']} "
            f"mode={row['mode']} case_id={row['case_id']} tester={row['tester_id']} "
            f"model={row.get('source_model') or 'ALL'} setting={row.get('prompt_setting') or 'ALL'} "
            f"prompt_chars={len(request_text)}"
        )
        run_db_write_with_retry(
            conn,
            label=f"mark_running id={row_id}",
            max_attempts=3,
            sleep_seconds=retry_sleep_seconds,
            write_fn=lambda: mark_row_running(
                conn,
                task_table,
                row_id,
                request_text,
                judge_model,
                logical_judge_model,
            ),
        )
        raw_response: Optional[str] = None
        usage: Dict[str, Any] = {}
        started = time.perf_counter()
        last_error: Optional[Exception] = None
        counts["attempted"] += 1
        for attempt in range(1, max_retries + 1):
            try:
                image_64 = None
                if use_image:
                    task_for_context = "valid" if row["rq3_task"] == TASK_VALID else "invalid"
                    image_64 = build_context(row["case_id"], task_for_context).image_64
                response = client.chat_completion(
                    message=request_text,
                    image_64=image_64,
                    session_id="",
                )
                raw_response = client.response_chat_text(response)
                usage = {"api_response": response, "session_id": client.response_session_id(response)}
                (
                    generated_input,
                    parse_error,
                    rationale,
                    change_analysis,
                    future_recommendations,
                ) = parse_generated_input(raw_response, resource_ids)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                if generated_input is None:
                    raise ValueError(parse_error or "parse failure")
                run_db_write_with_retry(
                    conn,
                    label=f"success id={row_id}",
                    max_attempts=3,
                    sleep_seconds=retry_sleep_seconds,
                    write_fn=lambda: update_generation_result(
                        conn,
                        task_table,
                        row_id=row_id,
                        status="success",
                        raw_response=raw_response,
                        usage=usage,
                        generated_input=generated_input,
                        rationale=rationale,
                        change_analysis=change_analysis,
                        future_recommendations=future_recommendations,
                        parse_success=1,
                        parse_error=None,
                        elapsed_ms=elapsed_ms,
                        error_message=None,
                    ),
                )
                counts["success"] += 1
                log(
                    f"[RUN_DONE] id={row_id} status=success elapsed_ms={elapsed_ms} "
                    f"values={compact_log_text(generated_input, 180)}"
                )
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
                break
            except Exception as exc:
                last_error = exc
                response_hint = f" response_excerpt={compact_log_text(raw_response)}" if raw_response else ""
                log(
                    f"[RUN_RETRY] id={row_id} attempt={attempt}/{max_retries} "
                    f"error={type(exc).__name__}: {str(exc)[:300]}{response_hint}"
                )
                if attempt < max_retries:
                    time.sleep(retry_sleep_seconds)
        else:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            (
                generated_input,
                parse_error,
                rationale,
                change_analysis,
                future_recommendations,
            ) = parse_generated_input(raw_response or "", resource_ids)
            run_db_write_with_retry(
                conn,
                label=f"failed id={row_id}",
                max_attempts=3,
                sleep_seconds=retry_sleep_seconds,
                write_fn=lambda: update_generation_result(
                    conn,
                    task_table,
                    row_id=row_id,
                    status="failed",
                    raw_response=raw_response,
                    usage=usage,
                    generated_input=generated_input,
                    rationale=rationale,
                    change_analysis=change_analysis,
                    future_recommendations=future_recommendations,
                    parse_success=1 if generated_input is not None else 0,
                    parse_error=parse_error,
                    elapsed_ms=elapsed_ms,
                    error_message=f"{type(last_error).__name__}: {last_error}" if last_error else "unknown",
                ),
            )
            counts["failed"] += 1
            if fail_fast and last_error:
                raise last_error
    return counts


def status_summary(conn, case_table: str, task_table: str, prompt_version: str) -> None:
    case_table = ensure_identifier(case_table)
    task_table = ensure_identifier(task_table)
    ensure_conn_alive(conn)
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT rq3_task, COUNT(*) AS n
            FROM {case_table}
            GROUP BY rq3_task
            ORDER BY rq3_task
            """
        )
        log("[CASE_STATUS]")
        for row in cursor.fetchall():
            log(json_dumps(row))
        cursor.execute(
            f"""
            SELECT rq3_task, mode, status, COUNT(*) AS n
            FROM {task_table}
            WHERE prompt_version = %s
            GROUP BY rq3_task, mode, status
            ORDER BY rq3_task, mode, status
            """,
            [prompt_version],
        )
        log(f"[TASK_STATUS] prompt_version={prompt_version}")
        for row in cursor.fetchall():
            log(json_dumps(row))


def parse_case_ids_arg(value: str, task: str) -> List[str]:
    out = []
    for item in parse_csv(value):
        out.append(normalize_case_id(item, task))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed and run RQ3 tester modification/regeneration tasks using Claude as tester personas."
    )
    parser.add_argument("--action", choices=["seed", "run", "seed-and-run", "status"], default="seed")
    parser.add_argument("--source-table", default=SOURCE_TABLE_DEFAULT)
    parser.add_argument("--case-table", default=CASE_TABLE_DEFAULT)
    parser.add_argument("--task-table", default=TASK_TABLE_DEFAULT)
    parser.add_argument("--source-payload-table", default=SOURCE_PAYLOAD_TABLE_DEFAULT)
    parser.add_argument("--selected-valid-json", default=str(DEFAULT_SELECTED_VALID_JSON))
    parser.add_argument("--tasks", default="valid,invalid", help="valid, invalid, or both.")
    parser.add_argument("--modes", default="modification,regeneration")
    parser.add_argument("--models", default=",".join(FINAL_MODELS))
    parser.add_argument("--settings", default=",".join(PROMPT_SETTINGS))
    parser.add_argument("--tester-ids", default="1-3")
    parser.add_argument("--valid-case-ids", default="", help="Optional Pxxx list; default uses the selected 20 valid pages.")
    parser.add_argument("--invalid-case-ids", default="", help="Optional Bxxx list; default stratifies 20 bugs.")
    parser.add_argument("--prompt-version", default=PROMPT_VERSION_DEFAULT)
    parser.add_argument(
        "--store-source-payload",
        action="store_true",
        help=(
            "Store full source candidate JSON in the revision source table. "
            "By default, seed stores only source ids/counts and run reconstructs candidates from the curated table."
        ),
    )
    parser.add_argument("--judge-model", default=os.getenv("MUST_LAB_JUDGE_MODEL", DEFAULT_JUDGE_MODEL))
    parser.add_argument("--logical-judge-model", default=DEFAULT_LOGICAL_JUDGE_MODEL)
    parser.add_argument("--api-url", default=os.getenv("MUST_LAB_API_URL", API_URL_DEFAULT))
    parser.add_argument("--api-token", default=os.getenv("MUST_LAB_API_TOKEN", ""))
    parser.add_argument("--timeout", type=float, default=500.0)
    parser.add_argument("--verify-ssl", action="store_true")
    parser.add_argument("--no-image", action="store_true", help="Do not attach the UI screenshot to Claude calls.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--rerun", action="store_true")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-sleep-seconds", type=float, default=5.0)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--fail-fast", action="store_true")
    args = parser.parse_args()

    tasks_raw = set(parse_csv(args.tasks))
    task_names = []
    if "valid" in tasks_raw:
        task_names.append(TASK_VALID)
    if "invalid" in tasks_raw:
        task_names.append(TASK_INVALID)
    if not task_names:
        raise ValueError("--tasks must include valid and/or invalid")

    modes = parse_csv(args.modes)
    for mode in modes:
        if mode not in {MODE_MODIFICATION, MODE_REGENERATION}:
            raise ValueError(f"Unsupported mode: {mode}")
    models = parse_csv(args.models)
    settings = parse_csv(args.settings)
    for setting in settings:
        prompt_variant_for_setting(setting)
    tester_nums = expand_int_ranges(args.tester_ids, min_value=1, max_value=3)
    tester_ids = [tester_id_for_num(num) for num in tester_nums]
    valid_case_ids = parse_case_ids_arg(args.valid_case_ids, TASK_VALID)
    invalid_case_ids = parse_case_ids_arg(args.invalid_case_ids, TASK_INVALID)

    conn = get_conn()
    started = time.time()
    try:
        create_tables(conn, args.case_table, args.task_table, args.source_payload_table)

        if args.action in {"seed", "seed-and-run"}:
            counts = seed_tasks(
                conn,
                case_table=args.case_table,
                task_table=args.task_table,
                source_payload_table=args.source_payload_table,
                source_table=args.source_table,
                selected_valid_json=Path(args.selected_valid_json),
                rq3_tasks=task_names,
                valid_case_ids=valid_case_ids,
                invalid_case_ids=invalid_case_ids,
                models=models,
                settings=settings,
                modes=modes,
                tester_nums=tester_nums,
                prompt_version=args.prompt_version,
                store_source_payload=args.store_source_payload,
            )
            log(f"[SEED_DONE] {json_dumps(counts)}")

        if args.action in {"run", "seed-and-run"}:
            rows = pending_rows(
                conn,
                args.task_table,
                args.source_payload_table,
                tasks=task_names,
                modes=modes,
                tester_ids=tester_ids,
                case_ids=[*valid_case_ids, *invalid_case_ids],
                prompt_version=args.prompt_version,
                limit=args.limit,
                rerun=args.rerun,
            )
            log(
                f"[RUN_PLAN] rows={len(rows)} model={args.judge_model} "
                f"use_image={int(not args.no_image)} limit={args.limit}"
            )
            client = MustLabJudgeClient(
                api_key=args.api_token,
                model_type=args.judge_model,
                base_url=args.api_url,
                timeout=args.timeout,
                verify_ssl=args.verify_ssl,
            )
            counts = run_generation(
                conn,
                task_table=args.task_table,
                source_table=args.source_table,
                client=client,
                rows=rows,
                models=models,
                settings=settings,
                max_retries=args.max_retries,
                retry_sleep_seconds=args.retry_sleep_seconds,
                sleep_seconds=args.sleep_seconds,
                use_image=not args.no_image,
                judge_model=args.judge_model,
                logical_judge_model=args.logical_judge_model,
                fail_fast=args.fail_fast,
            )
            log(f"[RUN_DONE_SUMMARY] {json_dumps(counts)}")

        status_summary(conn, args.case_table, args.task_table, args.prompt_version)
        log(f"[DONE] elapsed={time.time() - started:.1f}s")
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
