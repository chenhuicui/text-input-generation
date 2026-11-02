from __future__ import annotations

import argparse
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import pymysql
from pymysql.cursors import DictCursor
from scipy.stats import mannwhitneyu


PROJECT_ROOT = Path(
    "/Users/cuichenhui/Documents/local-repositories/"
    "llm-empirical-study-workspace/llm-empirical-study"
)

DEFAULT_TABLE = "t_llm_text_generation_curated"
DEFAULT_TOKEN_TABLE = "t_llm_text_generation_curated_token"
DEFAULT_OFFICIAL_TOKEN_TABLE = "t_llm_text_generation_curated_token_recomputed"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output" / "tosem_rq_metrics"
DEFAULT_MD_PATH = PROJECT_ROOT / "TOSEM_RQ指标设计与适用性判断.md"

FINAL_MODELS = (
    "baidu/ernie-5.0-thinking-preview",
    "claude-opus-4-6",
    "Doubao-Seed-2.0-pro",
    "gemini-3.1-pro-preview",
    "gpt-5.4-2026-03-05",
    "grok-4.20-reasoning",
    "kimi/kimi-k2.5",
    "mistralai/mistral-large-2512",
    "qwen3.5-plus",
)

PROMPT_SETTINGS = ("extracted", "xml", "vision")
PROMPT_VARIANT_BY_SETTING = {
    "extracted": "full",
    "xml": "xml",
    "vision": "vision",
}
SETTING_LABEL = {
    "extracted": "Extracted context",
    "xml": "UI hierarchy XML",
    "vision": "Screenshot vision",
}
SETTING_SHORT_LABEL = {
    "extracted": "Extracted",
    "xml": "XML",
    "vision": "Vision",
}
SETTING_PAIR_LABEL = {
    ("extracted", "xml"): "Extracted/XML",
    ("extracted", "vision"): "Extracted/Vision",
    ("xml", "vision"): "XML/Vision",
}
MODEL_MACRO = {
    "gemini-3.1-pro-preview": r"\GeminiM",
    "claude-opus-4-6": r"\ClaudeM",
    "grok-4.20-reasoning": r"\GrokM",
    "gpt-5.4-2026-03-05": r"\GPTM",
    "Doubao-Seed-2.0-pro": r"\DoubaoM",
    "qwen3.5-plus": r"\QwenM",
    "baidu/ernie-5.0-thinking-preview": r"\ERNIEM",
    "kimi/kimi-k2.5": r"\KimiM",
    "mistralai/mistral-large-2512": r"\MistralM",
}
LLM_TABLE_ORDER = (
    "gemini-3.1-pro-preview",
    "claude-opus-4-6",
    "grok-4.20-reasoning",
    "gpt-5.4-2026-03-05",
    "Doubao-Seed-2.0-pro",
    "qwen3.5-plus",
    "baidu/ernie-5.0-thinking-preview",
    "kimi/kimi-k2.5",
    "mistralai/mistral-large-2512",
)

VALID_CASES = tuple(f"P{i:03d}" for i in range(1, 116))
INVALID_CASES = tuple(f"B{i:03d}" for i in range(1, 38))

BUG_CATEGORY_BY_CASE = {
    "B001": "Empty Char",
    "B002": "Constraint",
    "B003": "Specific Input",
    "B004": "Specific Input",
    "B005": "Constraint",
    "B006": "Constraint",
    "B007": "Special Char",
    "B008": "Specific Input",
    "B009": "Special Char",
    "B010": "Constraint",
    "B011": "Special Char",
    "B012": "Specific Input",
    "B013": "Specific Input",
    "B014": "Special Char",
    "B015": "Specific Input",
    "B016": "Specific Input",
    "B017": "Empty Char",
    "B018": "Special Char",
    "B019": "Constraint",
    "B020": "Empty Char",
    "B021": "Constraint",
    "B022": "Specific Input",
    "B023": "Specific Input",
    "B024": "Constraint",
    "B025": "Empty Char",
    "B026": "Constraint",
    "B027": "Empty Char",
    "B028": "Special Char",
    "B029": "Constraint",
    "B030": "Empty Char",
    "B031": "Constraint",
    "B032": "Special Char",
    "B033": "Special Char",
    "B034": "Empty Char",
    "B035": "Constraint",
    "B036": "Constraint",
    "B037": "Constraint",
}

EXPERIMENTS = (
    "rq1_valid",
    "rq1_invalid",
    "rq2_valid_feedback",
    "rq2_invalid_feedback",
)

ANALYSIS_START = "<!-- AUTO_RQ_METRICS_RESULTS_START -->"
ANALYSIS_END = "<!-- AUTO_RQ_METRICS_RESULTS_END -->"


@dataclass(frozen=True)
class TaskSpec:
    rq: str
    experiment: str
    task: str
    cases: Sequence[str]
    budget: int
    index_column: str
    cumulative_cutoffs: Sequence[int]
    final_metric_name: str


TASK_SPECS = {
    "rq1_valid": TaskSpec(
        rq="RQ1.1",
        experiment="rq1_valid",
        task="valid_pptr",
        cases=VALID_CASES,
        budget=3,
        index_column="attempt_id",
        cumulative_cutoffs=(1, 3),
        final_metric_name="PPTR@3",
    ),
    "rq1_invalid": TaskSpec(
        rq="RQ1.2",
        experiment="rq1_invalid",
        task="invalid_bug",
        cases=INVALID_CASES,
        budget=30,
        index_column="invalid_index",
        cumulative_cutoffs=(1, 5, 10, 30),
        final_metric_name="BugReveal@30",
    ),
    "rq2_valid_feedback": TaskSpec(
        rq="RQ2.1",
        experiment="rq2_valid_feedback",
        task="valid_pptr",
        cases=VALID_CASES,
        budget=3,
        index_column="attempt_id",
        cumulative_cutoffs=(1, 3),
        final_metric_name="PPTR@3",
    ),
    "rq2_invalid_feedback": TaskSpec(
        rq="RQ2.2",
        experiment="rq2_invalid_feedback",
        task="invalid_bug",
        cases=INVALID_CASES,
        budget=30,
        index_column="invalid_index",
        cumulative_cutoffs=(1, 5, 10, 30),
        final_metric_name="BugReveal@30",
    ),
}

TOKEN_LIMIT_FAILURE_RE = re.compile(
    r"("
    r"api_context_limit"
    r"|"
    r"prompt\s+contains.*too\s+large"
    r"|too\s+large\s+for\s+model"
    r"|maximum\s+context\s+length"
    r"|context\s+length"
    r"|max(?:imum)?\s+tokens?"
    r"|token\s+limit"
    r"|too\s+many\s+tokens?"
    r"|exceed(?:ed|s|ing)?.*(?:context|token)"
    r"|tokens?\s+exceed"
    r"|token\s*超"
    r"|超过.*token"
    r"|上下文.*超"
    r"|上限"
    r")",
    re.IGNORECASE | re.DOTALL,
)


def get_conn():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "101.200.47.200"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "must_user"),
        password=os.getenv("MYSQL_PASSWORD", "REDACTED_USE_ENV_MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE", "tosem_text_input_db"),
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
    )


def log(message: str) -> None:
    print(message, flush=True)


def placeholders(items: Sequence[Any]) -> str:
    return ",".join(["%s"] * len(items))


def safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan", "--"}:
        return None
    text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_session_ids(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw = value
    else:
        text = str(value).strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except Exception:
            raw = [text]
    out: List[str] = []
    seen = set()
    for item in raw:
        sid = str(item or "").strip()
        if sid and sid not in seen:
            out.append(sid)
            seen.add(sid)
    return out


def is_token_limit_failure(row: pd.Series) -> bool:
    if int(row.get("raw_token_limit_failure") or 0) == 1:
        return True
    status_text = " ".join(
        str(row.get(name) or "")
        for name in (
            "generation_status",
            "completion_status",
            "curation_status",
            "parse_error",
            "raw_response_excerpt",
        )
    )
    failure_like = (
        int(row.get("parse_success") or 0) != 1
        or str(row.get("generation_status") or "") != "parse_success"
        or str(row.get("completion_status") or "") != "ready_for_eval"
        or "failure" in str(row.get("curation_status") or "").lower()
        or "incomplete" in str(row.get("curation_status") or "").lower()
    )
    return bool(failure_like and TOKEN_LIMIT_FAILURE_RE.search(status_text))


def fetch_token_limit_failure_ids_from_raw(table: str, curated_ids: Sequence[Any]) -> set[int]:
    unique_ids = [int(x) for x in dict.fromkeys(curated_ids) if not pd.isna(x)]
    if not unique_ids:
        return set()

    out: set[int] = set()
    conn = get_conn()
    batch_size = 200
    try:
        with conn.cursor() as cursor:
            for start in range(0, len(unique_ids), batch_size):
                batch = unique_ids[start:start + batch_size]
                sql = f"""
                    SELECT curated_id
                    FROM {table}
                    WHERE curated_id IN ({placeholders(batch)})
                      AND (
                            LOCATE('Prompt contains', COALESCE(raw_response, '')) > 0
                         OR LOCATE('too large for model', COALESCE(raw_response, '')) > 0
                         OR LOCATE('maximum context length', COALESCE(raw_response, '')) > 0
                         OR LOCATE('context length', COALESCE(raw_response, '')) > 0
                         OR LOCATE('too many tokens', COALESCE(raw_response, '')) > 0
                         OR LOCATE('token limit', COALESCE(raw_response, '')) > 0
                         OR LOCATE('上限', COALESCE(raw_response, '')) > 0
                      )
                """
                cursor.execute(sql, batch)
                for row in cursor.fetchall():
                    out.add(int(row["curated_id"]))
    finally:
        conn.close()
    return out


def fetch_generation_rows(table: str) -> pd.DataFrame:
    sql = f"""
        SELECT
            curated_id,
            experiment,
            case_id,
            task,
            canonical_model,
            prompt_setting,
            prompt_variant,
            feedback_mode,
            attempt_id,
            invalid_index,
            eval_success,
            elapsed_ms,
            parse_success,
            generation_status,
            completion_status,
            curation_status,
            session_ids_json,
            parse_error,
            '' AS raw_response_excerpt
        FROM {table}
        WHERE experiment IN ({placeholders(EXPERIMENTS)})
          AND canonical_model IN ({placeholders(FINAL_MODELS)})
          AND prompt_setting IN ({placeholders(PROMPT_SETTINGS)})
          AND (
                (prompt_setting = 'extracted' AND prompt_variant = 'full')
             OR (prompt_setting = 'xml' AND prompt_variant = 'xml')
             OR (prompt_setting = 'vision' AND prompt_variant = 'vision')
          )
          AND tau = -1
          AND combo_index = -1
    """
    params = [*EXPERIMENTS, *FINAL_MODELS, *PROMPT_SETTINGS]
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
    finally:
        conn.close()
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("No rows fetched from curated generation table.")
    df["eval_success"] = df["eval_success"].fillna(0).astype(int)
    df["elapsed_ms"] = pd.to_numeric(df["elapsed_ms"], errors="coerce").fillna(0)
    df["session_id_list"] = df["session_ids_json"].map(parse_session_ids)
    df["raw_token_limit_failure"] = 0
    failure_like = (
        (df["parse_success"].fillna(0).astype(int) != 1)
        | (df["generation_status"].fillna("") != "parse_success")
        | (df["completion_status"].fillna("") != "ready_for_eval")
        | df["curation_status"].fillna("").str.lower().str.contains("failure|incomplete", regex=True)
    )
    raw_check_ids = df.loc[
        failure_like
        & (df["canonical_model"] == "mistralai/mistral-large-2512")
        & (df["prompt_setting"] == "vision"),
        "curated_id",
    ].tolist()
    log(f"[analyze] checking token-limit raw patterns for Mistral vision rows={len(raw_check_ids)}")
    raw_token_limit_ids = fetch_token_limit_failure_ids_from_raw(table, raw_check_ids)
    log(f"[analyze] matched token-limit raw patterns rows={len(raw_token_limit_ids)}")
    if raw_token_limit_ids:
        df.loc[df["curated_id"].isin(raw_token_limit_ids), "raw_token_limit_failure"] = 1
    df["token_limit_failure"] = df.apply(is_token_limit_failure, axis=1)
    df.loc[df["token_limit_failure"], "session_id_list"] = df.loc[
        df["token_limit_failure"], "session_id_list"
    ].map(lambda _: [])
    return df


def fetch_token_rows_for_sessions(token_table: str, session_ids: Sequence[str]) -> Dict[str, Dict[str, Optional[float]]]:
    unique_ids = [sid for sid in dict.fromkeys(session_ids) if sid]
    if not unique_ids:
        return {}

    conn = get_conn()
    token_by_session: Dict[str, Dict[str, Optional[float]]] = {}
    batch_size = 5000
    try:
        with conn.cursor() as cursor:
            for start in range(0, len(unique_ids), batch_size):
                batch = unique_ids[start:start + batch_size]
                sql = f"""
                    SELECT session_id, prompt_tokens, completion_tokens, total_tokens, time
                    FROM {token_table}
                    WHERE session_id IN ({placeholders(batch)})
                """
                cursor.execute(sql, batch)
                for row in cursor.fetchall():
                    sid = str(row.get("session_id") or "").strip()
                    if not sid:
                        continue
                    parsed = {
                        "prompt_tokens": safe_float(row.get("prompt_tokens")),
                        "completion_tokens": safe_float(row.get("completion_tokens")),
                        "total_tokens": safe_float(row.get("total_tokens")),
                        "time": safe_float(row.get("time")),
                    }
                    if (
                            parsed["total_tokens"] is None
                            and parsed["prompt_tokens"] is not None
                            and parsed["completion_tokens"] is not None
                    ):
                        parsed["total_tokens"] = parsed["prompt_tokens"] + parsed["completion_tokens"]

                    old = token_by_session.get(sid)
                    if old is None:
                        token_by_session[sid] = parsed
                        continue
                    old_score = sum(v is not None for v in old.values())
                    new_score = sum(v is not None for v in parsed.values())
                    if new_score > old_score:
                        token_by_session[sid] = parsed
    finally:
        conn.close()
    return token_by_session


OFFICIAL_TOKEN_OVERRIDE_MODELS = {
    "claude-opus-4-6",
    "gpt-5.4-2026-03-05",
}


def fetch_official_token_rows(
        official_token_table: str,
        curated_ids: Sequence[Any],
) -> Dict[int, Dict[str, Optional[float]]]:
    unique_ids = [int(x) for x in dict.fromkeys(curated_ids) if not pd.isna(x)]
    if not unique_ids:
        return {}

    conn = get_conn()
    out: Dict[int, Dict[str, Optional[float]]] = {}
    batch_size = 5000
    try:
        with conn.cursor() as cursor:
            for start in range(0, len(unique_ids), batch_size):
                batch = unique_ids[start:start + batch_size]
                sql = f"""
                    SELECT
                        curated_id,
                        recomputed_input_tokens,
                        completion_tokens,
                        estimated_total_tokens,
                        status
                    FROM {official_token_table}
                    WHERE curated_id IN ({placeholders(batch)})
                      AND status = 'computed'
                """
                cursor.execute(sql, batch)
                for row in cursor.fetchall():
                    curated_id = int(row["curated_id"])
                    prompt = safe_float(row.get("recomputed_input_tokens"))
                    completion = safe_float(row.get("completion_tokens"))
                    total = safe_float(row.get("estimated_total_tokens"))
                    if total is None and prompt is not None and completion is not None:
                        total = prompt + completion
                    out[curated_id] = {
                        "prompt_tokens": prompt,
                        "completion_tokens": completion,
                        "total_tokens": total,
                    }
    finally:
        conn.close()
    return out


def attach_session_token_metrics(
        df: pd.DataFrame,
        token_table: str,
        official_token_table: str,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    all_session_ids: List[str] = []
    for sessions in df["session_id_list"]:
        all_session_ids.extend(sessions)

    log(f"[analyze] fetching token rows by session_id sessions={len(set(all_session_ids))}")
    token_by_session = fetch_token_rows_for_sessions(token_table, all_session_ids)
    log(f"[analyze] matched token sessions={len(token_by_session)}")
    official_ids = df[
        (df["canonical_model"].isin(OFFICIAL_TOKEN_OVERRIDE_MODELS))
        & (~df["token_limit_failure"])
    ]["curated_id"].tolist()
    log(f"[analyze] fetching official token override rows rows={len(official_ids)}")
    official_by_curated = fetch_official_token_rows(
        official_token_table,
        official_ids,
    )
    log(f"[analyze] matched official token override rows={len(official_by_curated)}")

    metric_rows: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        sessions = row["session_id_list"]
        token_limit_failure = bool(row.get("token_limit_failure"))
        prompt_sum = completion_sum = total_sum = time_sum = 0.0
        prompt_n = completion_n = total_n = time_n = matched_n = 0
        chargeable_sessions = sessions
        token_source = "token_limit_failure_excluded" if token_limit_failure else "session_token_table"

        for sid in sessions:
            token = token_by_session.get(sid)
            if token is None:
                continue
            matched_n += 1
            prompt = token.get("prompt_tokens")
            completion = token.get("completion_tokens")
            total = token.get("total_tokens")
            time_value = token.get("time")
            if prompt is not None:
                prompt_sum += prompt
            if completion is not None:
                completion_sum += completion
            if total is not None:
                total_sum += total
            if time_value is not None:
                time_sum += time_value

        if (
                not token_limit_failure
                and row["canonical_model"] in OFFICIAL_TOKEN_OVERRIDE_MODELS
                and int(row["curated_id"]) in official_by_curated
        ):
            official = official_by_curated[int(row["curated_id"])]
            prompt = official.get("prompt_tokens")
            completion = official.get("completion_tokens")
            total = official.get("total_tokens")
            prompt_sum = prompt if prompt is not None else 0.0
            completion_sum = completion if completion is not None else 0.0
            total_sum = total if total is not None else 0.0
            prompt_n = 1 if prompt is not None and prompt_sum > 0 else 0
            completion_n = 1 if completion is not None and completion_sum > 0 else 0
            total_n = 1 if total is not None and total_sum > 0 else 0
            token_source = "official_recomputed"
        else:
            # A generation row is one token observation.  For feedback
            # rows, session_ids_json may contain the whole context chain; the row's
            # token value is therefore the sum over all matched sessions in that JSON.
            prompt_n = 1 if matched_n > 0 and prompt_sum > 0 else 0
            completion_n = 1 if matched_n > 0 and completion_sum > 0 else 0
            total_n = 1 if matched_n > 0 and total_sum > 0 else 0

        time_n = 1 if matched_n > 0 and time_sum > 0 else 0

        # Prefer token-table time when present, using the same session-chain sum
        # as the token fields.  Fall back to curated elapsed_ms when no token time
        # can be matched.
        if token_limit_failure:
            effective_elapsed_ms = 0.0
            elapsed_source = "token_limit_failure_excluded"
        elif time_n > 0:
            effective_elapsed_ms = time_sum * 1000.0
            elapsed_source = "token_table"
        else:
            effective_elapsed_ms = float(row.get("elapsed_ms") or 0.0)
            elapsed_source = "curated_elapsed_ms"

        metric_rows.append(
            {
                "curated_id": row["curated_id"],
                "session_count": len(sessions),
                "chargeable_session_count": len([sid for sid in chargeable_sessions if sid]),
                "chargeable_session_key": "|".join(chargeable_sessions) if chargeable_sessions else "",
                "matched_token_session_count": matched_n,
                "prompt_token_sum": prompt_sum if prompt_n else np.nan,
                "completion_token_sum": completion_sum if completion_n else np.nan,
                "total_token_sum": total_sum if total_n else np.nan,
                "prompt_token_call_count": prompt_n,
                "completion_token_call_count": completion_n,
                "total_token_call_count": total_n,
                "token_time_seconds_sum": time_sum if time_n else np.nan,
                "token_time_call_count": time_n,
                "elapsed_ms_effective": effective_elapsed_ms,
                "elapsed_source": elapsed_source,
                "token_source": token_source,
            }
        )

    token_metrics = pd.DataFrame(metric_rows)
    out = df.merge(token_metrics, on="curated_id", how="left")
    out["elapsed_ms_original"] = out["elapsed_ms"]
    out["elapsed_ms"] = out["elapsed_ms_effective"].fillna(out["elapsed_ms"]).fillna(0)

    coverage = []
    for (experiment, model, setting), group in out.groupby(
            ["experiment", "canonical_model", "prompt_setting"], sort=False):
        coverage.append(
            {
                "experiment": experiment,
                "model": model,
                "prompt_setting": setting,
                "rows": int(len(group)),
                "session_rows": int((group["session_count"] > 0).sum()),
                "chargeable_session_refs": int(group["chargeable_session_count"].sum()),
                "matched_token_rows": int((group["matched_token_session_count"] > 0).sum()),
                "token_call_count": int(group["total_token_call_count"].sum()),
                "prompt_call_count": int(group["prompt_token_call_count"].sum()),
                "completion_call_count": int(group["completion_token_call_count"].sum()),
                "token_time_call_count": int(group["token_time_call_count"].sum()),
                "token_time_row_count": int((group["elapsed_source"] == "token_table").sum()),
                "token_limit_failure_rows": int(group["token_limit_failure"].sum()),
                "official_token_rows": int((group["token_source"] == "official_recomputed").sum()),
            }
        )
    return out, pd.DataFrame(coverage)


def validate_eval_completeness(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for exp in EXPERIMENTS:
        part = df[df["experiment"] == exp]
        rows.append(
            {
                "experiment": exp,
                "row_count": int(len(part)),
                "null_eval_count": int(part["eval_success"].isna().sum()),
                "eval_1_count": int((part["eval_success"] == 1).sum()),
                "eval_0_count": int((part["eval_success"] == 0).sum()),
                "distinct_models": int(part["canonical_model"].nunique()),
                "distinct_settings": int(part["prompt_setting"].nunique()),
            }
        )
    return pd.DataFrame(rows)


def first_success_index(
        group: pd.DataFrame,
        index_column: str,
        budget: int,
) -> Optional[int]:
    success_rows = group[
        (group["eval_success"] == 1)
        & (group[index_column] >= 1)
        & (group[index_column] <= budget)
    ]
    if success_rows.empty:
        return None
    return int(success_rows[index_column].min())


def dedupe_token_rows(group: pd.DataFrame) -> pd.DataFrame:
    return group


def token_sum(group: pd.DataFrame, column: str) -> float:
    token_group = dedupe_token_rows(group)
    return float(token_group[column].sum(skipna=True)) if len(token_group) else 0.0


def token_call_count(group: pd.DataFrame, column: str) -> int:
    token_group = dedupe_token_rows(group)
    return int(token_group[column].sum()) if len(token_group) else 0


def fixed_budget_experiment(spec: TaskSpec) -> bool:
    return spec.experiment in {"rq1_valid", "rq1_invalid"}


def valid_index_rows(group: pd.DataFrame, spec: TaskSpec) -> pd.DataFrame:
    if group.empty:
        return group
    return group[
        (group[spec.index_column] >= 1)
        & (group[spec.index_column] <= spec.budget)
    ]


def attempt_count_for_case(group: pd.DataFrame, spec: TaskSpec) -> int:
    if fixed_budget_experiment(spec):
        return spec.budget
    valid_rows = valid_index_rows(group, spec)
    if valid_rows.empty:
        return 0
    return int(valid_rows[spec.index_column].dropna().astype(int).nunique())


def attempt_count_at_round(group: pd.DataFrame, spec: TaskSpec, idx: int) -> int:
    if fixed_budget_experiment(spec):
        return 1
    valid_rows = valid_index_rows(group, spec)
    return int((valid_rows[spec.index_column] == idx).any())


def attempt_count_until_round(group: pd.DataFrame, spec: TaskSpec, idx: int) -> int:
    if fixed_budget_experiment(spec):
        return idx
    valid_rows = valid_index_rows(group, spec)
    until_rows = valid_rows[valid_rows[spec.index_column] <= idx]
    if until_rows.empty:
        return 0
    return int(until_rows[spec.index_column].dropna().astype(int).nunique())


def build_case_level_for_spec(df: pd.DataFrame, spec: TaskSpec) -> pd.DataFrame:
    part = df[
        (df["experiment"] == spec.experiment)
        & (df["task"] == spec.task)
    ].copy()
    rows = []
    grouped = {
        (str(model), str(setting), str(case_id)): g
        for (model, setting, case_id), g in part.groupby(
            ["canonical_model", "prompt_setting", "case_id"], dropna=False
        )
    }

    for model in FINAL_MODELS:
        for setting in PROMPT_SETTINGS:
            for case_id in spec.cases:
                group = grouped.get((model, setting, case_id))
                if group is None:
                    group = part.iloc[0:0].copy()
                first_idx = first_success_index(group, spec.index_column, spec.budget)
                actual_attempt_count = attempt_count_for_case(group, spec)
                row = {
                    "rq": spec.rq,
                    "experiment": spec.experiment,
                    "task": spec.task,
                    "model": model,
                    "prompt_setting": setting,
                    "prompt_variant": PROMPT_VARIANT_BY_SETTING[setting],
                    "case_id": case_id,
                    "case_category": BUG_CATEGORY_BY_CASE.get(case_id, ""),
                    "first_success_index": first_idx if first_idx is not None else np.nan,
                    "success_final": 1 if first_idx is not None and first_idx <= spec.budget else 0,
                    "actual_call_count": actual_attempt_count,
                    "elapsed_ms_sum": float(group["elapsed_ms"].sum()) if len(group) else 0.0,
                    "elapsed_ms_mean": float(group["elapsed_ms"].mean()) if len(group) else 0.0,
                    "prompt_token_sum": token_sum(group, "prompt_token_sum"),
                    "completion_token_sum": token_sum(group, "completion_token_sum"),
                    "total_token_sum": token_sum(group, "total_token_sum"),
                    "prompt_token_call_count": token_call_count(group, "prompt_token_call_count"),
                    "completion_token_call_count": token_call_count(group, "completion_token_call_count"),
                    "total_token_call_count": token_call_count(group, "total_token_call_count"),
                }
                for cutoff in spec.cumulative_cutoffs:
                    row[f"success_at_{cutoff}"] = (
                        1 if first_idx is not None and first_idx <= cutoff else 0
                    )
                for idx in range(1, spec.budget + 1):
                    row[f"new_at_{idx}"] = 1 if first_idx == idx else 0
                    round_rows = group[group[spec.index_column] == idx]
                    until_rows = group[
                        (group[spec.index_column] >= 1)
                        & (group[spec.index_column] <= idx)
                    ]
                    row[f"calls_at_{idx}"] = attempt_count_at_round(group, spec, idx)
                    row[f"calls_until_{idx}"] = attempt_count_until_round(group, spec, idx)
                    row[f"elapsed_ms_at_{idx}"] = (
                        float(round_rows["elapsed_ms"].sum()) if len(round_rows) else 0.0
                    )
                    row[f"elapsed_ms_until_{idx}"] = (
                        float(until_rows["elapsed_ms"].sum()) if len(until_rows) else 0.0
                    )
                    row[f"prompt_token_sum_at_{idx}"] = (
                        token_sum(round_rows, "prompt_token_sum")
                    )
                    row[f"completion_token_sum_at_{idx}"] = (
                        token_sum(round_rows, "completion_token_sum")
                    )
                    row[f"total_token_sum_at_{idx}"] = (
                        token_sum(round_rows, "total_token_sum")
                    )
                    row[f"prompt_token_call_count_at_{idx}"] = (
                        token_call_count(round_rows, "prompt_token_call_count")
                    )
                    row[f"completion_token_call_count_at_{idx}"] = (
                        token_call_count(round_rows, "completion_token_call_count")
                    )
                    row[f"total_token_call_count_at_{idx}"] = (
                        token_call_count(round_rows, "total_token_call_count")
                    )
                    row[f"prompt_token_sum_until_{idx}"] = (
                        token_sum(until_rows, "prompt_token_sum")
                    )
                    row[f"completion_token_sum_until_{idx}"] = (
                        token_sum(until_rows, "completion_token_sum")
                    )
                    row[f"total_token_sum_until_{idx}"] = (
                        token_sum(until_rows, "total_token_sum")
                    )
                    row[f"prompt_token_call_count_until_{idx}"] = (
                        token_call_count(until_rows, "prompt_token_call_count")
                    )
                    row[f"completion_token_call_count_until_{idx}"] = (
                        token_call_count(until_rows, "completion_token_call_count")
                    )
                    row[f"total_token_call_count_until_{idx}"] = (
                        token_call_count(until_rows, "total_token_call_count")
                    )
                rows.append(row)
    return pd.DataFrame(rows)


def summarize_case_level(case_df: pd.DataFrame, spec: TaskSpec) -> pd.DataFrame:
    rows = []
    denom = len(spec.cases)
    for (model, setting), group in case_df.groupby(["model", "prompt_setting"], sort=False):
        actual_attempts_total = int(group["actual_call_count"].sum())
        row: Dict[str, Any] = {
            "rq": spec.rq,
            "experiment": spec.experiment,
            "task": spec.task,
            "model": model,
            "prompt_setting": setting,
            "n_cases": denom,
            "actual_calls_total": actual_attempts_total,
            "actual_calls_per_case": float(group["actual_call_count"].mean()),
            "elapsed_ms_per_case_mean": float(group["elapsed_ms_sum"].mean()),
            "elapsed_ms_per_generation_mean": (
                float(group["elapsed_ms_sum"].sum() / group["actual_call_count"].sum())
                if group["actual_call_count"].sum() > 0
                else 0.0
            ),
            "prompt_tokens_total": float(group["prompt_token_sum"].sum()),
            "completion_tokens_total": float(group["completion_token_sum"].sum()),
            "total_tokens_total": float(group["total_token_sum"].sum()),
            "prompt_token_calls": int(group["prompt_token_call_count"].sum()),
            "completion_token_calls": int(group["completion_token_call_count"].sum()),
            "total_token_calls": int(group["total_token_call_count"].sum()),
            "prompt_tokens_per_case": float(group["prompt_token_sum"].mean()),
            "completion_tokens_per_case": float(group["completion_token_sum"].mean()),
            "total_tokens_per_case": float(group["total_token_sum"].mean()),
        }
        row["prompt_tokens_per_call"] = (
            row["prompt_tokens_total"] / actual_attempts_total
            if actual_attempts_total > 0 and row["prompt_token_calls"] > 0
            else float("nan")
        )
        row["completion_tokens_per_call"] = (
            row["completion_tokens_total"] / actual_attempts_total
            if actual_attempts_total > 0 and row["completion_token_calls"] > 0
            else float("nan")
        )
        row["total_tokens_per_call"] = (
            row["total_tokens_total"] / actual_attempts_total
            if actual_attempts_total > 0
            else float("nan")
        )
        for cutoff in spec.cumulative_cutoffs:
            metric_prefix = "PPTR" if spec.task == "valid_pptr" else "BugReveal"
            row[f"{metric_prefix}@{cutoff}"] = float(group[f"success_at_{cutoff}"].sum() / denom)
            row[f"{metric_prefix}@{cutoff}_count"] = int(group[f"success_at_{cutoff}"].sum())
        metric_prefix = "NewPPTR" if spec.task == "valid_pptr" else "NewBugReveal"
        for idx in range(1, spec.budget + 1):
            row[f"{metric_prefix}_{idx}"] = float(group[f"new_at_{idx}"].sum() / denom)
            row[f"{metric_prefix}_{idx}_count"] = int(group[f"new_at_{idx}"].sum())
        rows.append(row)

    # Add setting-aggregated rows so statistical analysis can use all cases/settings together.
    for model, group in case_df.groupby("model", sort=False):
        denom_all = len(spec.cases) * len(PROMPT_SETTINGS)
        actual_attempts_total = int(group["actual_call_count"].sum())
        row = {
            "rq": spec.rq,
            "experiment": spec.experiment,
            "task": spec.task,
            "model": model,
            "prompt_setting": "ALL",
            "n_cases": denom_all,
            "actual_calls_total": actual_attempts_total,
            "actual_calls_per_case": float(group["actual_call_count"].mean()),
            "elapsed_ms_per_case_mean": float(group["elapsed_ms_sum"].mean()),
            "elapsed_ms_per_generation_mean": (
                float(group["elapsed_ms_sum"].sum() / group["actual_call_count"].sum())
                if group["actual_call_count"].sum() > 0
                else 0.0
            ),
            "prompt_tokens_total": float(group["prompt_token_sum"].sum()),
            "completion_tokens_total": float(group["completion_token_sum"].sum()),
            "total_tokens_total": float(group["total_token_sum"].sum()),
            "prompt_token_calls": int(group["prompt_token_call_count"].sum()),
            "completion_token_calls": int(group["completion_token_call_count"].sum()),
            "total_token_calls": int(group["total_token_call_count"].sum()),
            "prompt_tokens_per_case": float(group["prompt_token_sum"].mean()),
            "completion_tokens_per_case": float(group["completion_token_sum"].mean()),
            "total_tokens_per_case": float(group["total_token_sum"].mean()),
        }
        row["prompt_tokens_per_call"] = (
            row["prompt_tokens_total"] / actual_attempts_total
            if actual_attempts_total > 0 and row["prompt_token_calls"] > 0
            else float("nan")
        )
        row["completion_tokens_per_call"] = (
            row["completion_tokens_total"] / actual_attempts_total
            if actual_attempts_total > 0 and row["completion_token_calls"] > 0
            else float("nan")
        )
        row["total_tokens_per_call"] = (
            row["total_tokens_total"] / actual_attempts_total
            if actual_attempts_total > 0
            else float("nan")
        )
        for cutoff in spec.cumulative_cutoffs:
            metric_prefix = "PPTR" if spec.task == "valid_pptr" else "BugReveal"
            row[f"{metric_prefix}@{cutoff}"] = float(group[f"success_at_{cutoff}"].sum() / denom_all)
            row[f"{metric_prefix}@{cutoff}_count"] = int(group[f"success_at_{cutoff}"].sum())
        metric_prefix = "NewPPTR" if spec.task == "valid_pptr" else "NewBugReveal"
        for idx in range(1, spec.budget + 1):
            row[f"{metric_prefix}_{idx}"] = float(group[f"new_at_{idx}"].sum() / denom_all)
            row[f"{metric_prefix}_{idx}_count"] = int(group[f"new_at_{idx}"].sum())
        rows.append(row)
    return pd.DataFrame(rows)


def build_category_summary(case_df: pd.DataFrame, spec: TaskSpec) -> pd.DataFrame:
    if spec.task != "invalid_bug":
        return pd.DataFrame()
    rows = []
    for (model, setting, category), group in case_df.groupby(
            ["model", "prompt_setting", "case_category"], sort=False):
        if not category:
            continue
        row = {
            "rq": spec.rq,
            "experiment": spec.experiment,
            "model": model,
            "prompt_setting": setting,
            "bug_category": category,
            "n_bugs": int(len(group)),
        }
        for cutoff in spec.cumulative_cutoffs:
            row[f"BugReveal@{cutoff}"] = float(group[f"success_at_{cutoff}"].mean())
            row[f"BugReveal@{cutoff}_count"] = int(group[f"success_at_{cutoff}"].sum())
        rows.append(row)
    return pd.DataFrame(rows)


def build_trend_summary_for_spec(case_df: pd.DataFrame, spec: TaskSpec) -> pd.DataFrame:
    metric_prefix = "PPTR" if spec.task == "valid_pptr" else "BugReveal"
    new_prefix = "NewPPTR" if spec.task == "valid_pptr" else "NewBugReveal"
    rows: List[Dict[str, Any]] = []

    def append_rows(group: pd.DataFrame, *, model: str, setting: str, denom: int) -> None:
        cumulative_count = 0
        for idx in range(1, spec.budget + 1):
            new_count = int(group[f"new_at_{idx}"].sum())
            cumulative_count += new_count
            calls_at_total = int(group[f"calls_at_{idx}"].sum())
            calls_until_total = int(group[f"calls_until_{idx}"].sum())
            elapsed_at_total = float(group[f"elapsed_ms_at_{idx}"].sum())
            elapsed_until_total = float(group[f"elapsed_ms_until_{idx}"].sum())
            prompt_token_until_total = float(group[f"prompt_token_sum_until_{idx}"].sum())
            completion_token_until_total = float(group[f"completion_token_sum_until_{idx}"].sum())
            total_token_until_total = float(group[f"total_token_sum_until_{idx}"].sum())
            prompt_token_calls_until = int(group[f"prompt_token_call_count_until_{idx}"].sum())
            completion_token_calls_until = int(group[f"completion_token_call_count_until_{idx}"].sum())
            total_token_calls_until = int(group[f"total_token_call_count_until_{idx}"].sum())
            rows.append(
                {
                    "rq": spec.rq,
                    "experiment": spec.experiment,
                    "task": spec.task,
                    "model": model,
                    "prompt_setting": setting,
                    "round": idx,
                    "metric_name": f"{metric_prefix}@{idx}",
                    "new_metric_name": f"{new_prefix}_{idx}",
                    "n_cases": denom,
                    "new_count": new_count,
                    "new_rate": new_count / denom,
                    "cumulative_count": cumulative_count,
                    "cumulative_rate": cumulative_count / denom,
                    "calls_at_round_total": calls_at_total,
                    "calls_at_round_per_case": calls_at_total / denom,
                    "calls_until_round_total": calls_until_total,
                    "calls_until_round_per_case": calls_until_total / denom,
                    "elapsed_ms_at_round_total": elapsed_at_total,
                    "elapsed_ms_at_round_per_case": elapsed_at_total / denom,
                    "elapsed_ms_at_round_per_executed_call": (
                        elapsed_at_total / calls_at_total if calls_at_total else 0.0
                    ),
                    "elapsed_ms_until_round_total": elapsed_until_total,
                    "elapsed_ms_until_round_per_case": elapsed_until_total / denom,
                    "elapsed_ms_until_round_per_executed_call": (
                        elapsed_until_total / calls_until_total if calls_until_total else 0.0
                    ),
                    "prompt_tokens_until_round_total": prompt_token_until_total,
                    "completion_tokens_until_round_total": completion_token_until_total,
                    "total_tokens_until_round_total": total_token_until_total,
                    "prompt_token_calls_until_round": prompt_token_calls_until,
                    "completion_token_calls_until_round": completion_token_calls_until,
                    "total_token_calls_until_round": total_token_calls_until,
                    "prompt_tokens_per_token_call": (
                        prompt_token_until_total / calls_until_total
                        if calls_until_total and prompt_token_calls_until else float("nan")
                    ),
                    "completion_tokens_per_token_call": (
                        completion_token_until_total / calls_until_total
                        if calls_until_total and completion_token_calls_until else float("nan")
                    ),
                    "total_tokens_per_token_call": (
                        total_token_until_total / calls_until_total
                        if calls_until_total else float("nan")
                    ),
                }
            )

    denom = len(spec.cases)
    for (model, setting), group in case_df.groupby(["model", "prompt_setting"], sort=False):
        append_rows(group, model=model, setting=setting, denom=denom)

    denom_all = len(spec.cases) * len(PROMPT_SETTINGS)
    for model, group in case_df.groupby("model", sort=False):
        append_rows(group, model=model, setting="ALL", denom=denom_all)

    return pd.DataFrame(rows)


def build_time_summary_from_trends(trend_df: pd.DataFrame) -> pd.DataFrame:
    final_round_rows = []
    for _, group in trend_df.groupby(["experiment", "model", "prompt_setting"], sort=False):
        last = group.sort_values("round").tail(1).iloc[0].to_dict()
        final_round_rows.append(
            {
                "rq": last["rq"],
                "experiment": last["experiment"],
                "task": last["task"],
                "model": last["model"],
                "prompt_setting": last["prompt_setting"],
                "budget": int(last["round"]),
                "calls_total": int(last["calls_until_round_total"]),
                "calls_per_case": float(last["calls_until_round_per_case"]),
                "elapsed_ms_total": float(last["elapsed_ms_until_round_total"]),
                "elapsed_ms_per_case": float(last["elapsed_ms_until_round_per_case"]),
                "elapsed_ms_per_executed_call": float(
                    last["elapsed_ms_until_round_per_executed_call"]
                ),
                "prompt_tokens_per_call": float(last["prompt_tokens_per_token_call"]),
                "completion_tokens_per_call": float(last["completion_tokens_per_token_call"]),
                "total_tokens_per_call": float(last["total_tokens_per_token_call"]),
                "total_tokens_per_case": (
                    float(last["total_tokens_until_round_total"]) / float(last["n_cases"])
                    if float(last["n_cases"]) else float("nan")
                ),
                "total_token_calls": int(last["total_token_calls_until_round"]),
            }
        )
    return pd.DataFrame(final_round_rows)


def vargha_delaney_a12(x: Sequence[float], y: Sequence[float]) -> float:
    x_arr = np.asarray(x, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    if len(x_arr) == 0 or len(y_arr) == 0:
        return float("nan")
    wins = 0.0
    ties = 0.0
    for value in x_arr:
        wins += float(np.sum(value > y_arr))
        ties += float(np.sum(value == y_arr))
    return (wins + 0.5 * ties) / (len(x_arr) * len(y_arr))


def mann_whitney_pvalue(x: Sequence[float], y: Sequence[float]) -> float:
    try:
        return float(mannwhitneyu(x, y, alternative="two-sided").pvalue)
    except ValueError:
        return 1.0


def comparison_symbol(p_value: float, a12: float) -> str:
    if math.isnan(p_value) or math.isnan(a12) or p_value >= 0.05:
        return "no_sig"
    if a12 > 0.50:
        return "m1_better"
    if a12 < 0.50:
        return "m2_better"
    return "no_sig"


def symbol_to_latex(symbol: str) -> str:
    if symbol == "m1_better":
        return r"\ding{52}"
    if symbol == "m2_better":
        return r"\textcolor{red}{\ding{54}}"
    return r"\ding{109}"


def format_p_value(p_value: float, compact: bool = False) -> str:
    if math.isnan(p_value):
        return "--"
    if p_value < 0.001:
        return r"$<0.001$"
    text = f"{p_value:.3f}"
    return text


def format_a12(value: float, compact: bool = False) -> str:
    if math.isnan(value):
        return "--"
    text = f"{value:.2f}"
    return text


def metric_values_for_case_level(
        case_df: pd.DataFrame,
        *,
        model: str,
        setting: str,
        metric_column: str,
        smaller_is_better: bool = False,
) -> np.ndarray:
    part = case_df[(case_df["model"] == model) & (case_df["prompt_setting"] == setting)].copy()
    if metric_column == "token_per_call":
        denom = part["actual_call_count"].replace(0, np.nan)
        values = (part["total_token_sum"].fillna(0) / denom).dropna().astype(float).to_numpy()
    elif metric_column == "token_per_case":
        values = part["total_token_sum"].fillna(0).astype(float).to_numpy()
    elif metric_column == "time_per_call":
        denom = part["actual_call_count"].replace(0, np.nan)
        values = ((part["elapsed_ms_sum"] / denom) / 1000.0).dropna().astype(float).to_numpy()
    elif metric_column == "time_per_case":
        values = (part["elapsed_ms_sum"] / 1000.0).dropna().astype(float).to_numpy()
    else:
        values = part[metric_column].dropna().astype(float).to_numpy()
    if smaller_is_better:
        values = -values
    return values


def pairwise_summary_against_peers(
        case_df: pd.DataFrame,
        *,
        setting: str,
        model: str,
        metric_column: str,
        smaller_is_better: bool,
) -> Dict[str, Any]:
    counts = {"m1_better": 0, "no_sig": 0, "m2_better": 0}
    p_values: List[float] = []
    a12_values: List[float] = []
    x = metric_values_for_case_level(
        case_df,
        model=model,
        setting=setting,
        metric_column=metric_column,
        smaller_is_better=smaller_is_better,
    )
    for other in LLM_TABLE_ORDER:
        if other == model:
            continue
        y = metric_values_for_case_level(
            case_df,
            model=other,
            setting=setting,
            metric_column=metric_column,
            smaller_is_better=smaller_is_better,
        )
        if len(x) == 0 or len(y) == 0:
            continue
        p_value = mann_whitney_pvalue(x, y)
        a12 = vargha_delaney_a12(x, y)
        symbol = comparison_symbol(p_value, a12)
        counts[symbol] += 1
        p_values.append(p_value)
        a12_values.append(a12)
    return {
        "better": counts["m1_better"],
        "same": counts["no_sig"],
        "worse": counts["m2_better"],
        "p_min": min(p_values) if p_values else float("nan"),
        "p_max": max(p_values) if p_values else float("nan"),
        "a12_min": min(a12_values) if a12_values else float("nan"),
        "a12_max": max(a12_values) if a12_values else float("nan"),
    }


def pairwise_between_two_groups(
        case_df: pd.DataFrame,
        *,
        model: str,
        setting_1: str,
        setting_2: str,
        metric_column: str,
        smaller_is_better: bool,
) -> Dict[str, Any]:
    x = metric_values_for_case_level(
        case_df,
        model=model,
        setting=setting_1,
        metric_column=metric_column,
        smaller_is_better=smaller_is_better,
    )
    y = metric_values_for_case_level(
        case_df,
        model=model,
        setting=setting_2,
        metric_column=metric_column,
        smaller_is_better=smaller_is_better,
    )
    if len(x) == 0 or len(y) == 0:
        return {"symbol": "no_sig", "p": float("nan"), "a12": float("nan")}
    p_value = mann_whitney_pvalue(x, y)
    a12 = vargha_delaney_a12(x, y)
    return {"symbol": comparison_symbol(p_value, a12), "p": p_value, "a12": a12}


def pairwise_stats_for_metric(
        case_df: pd.DataFrame,
        spec: TaskSpec,
        metric_column: str,
        metric_name: str,
        setting: str,
) -> List[Dict[str, Any]]:
    if setting == "ALL":
        part = case_df.copy()
    else:
        part = case_df[case_df["prompt_setting"] == setting].copy()
    rows = []
    for i, model1 in enumerate(FINAL_MODELS):
        x = part[part["model"] == model1][metric_column].astype(float).to_numpy()
        for model2 in FINAL_MODELS[i + 1:]:
            y = part[part["model"] == model2][metric_column].astype(float).to_numpy()
            p_value = mann_whitney_pvalue(x, y)
            a12 = vargha_delaney_a12(x, y)
            rows.append(
                {
                    "rq": spec.rq,
                    "experiment": spec.experiment,
                    "task": spec.task,
                    "metric": metric_name,
                    "prompt_setting": setting,
                    "model_1": model1,
                    "model_2": model2,
                    "n_1": len(x),
                    "n_2": len(y),
                    "mean_1": float(np.mean(x)) if len(x) else float("nan"),
                    "mean_2": float(np.mean(y)) if len(y) else float("nan"),
                    "p_value": p_value,
                    "a12_model_1_vs_model_2": a12,
                    "symbol": comparison_symbol(p_value, a12),
                }
            )
    return rows


def build_pairwise_stats(case_levels: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for exp, case_df in case_levels.items():
        spec = TASK_SPECS[exp]
        metric_prefix = "PPTR" if spec.task == "valid_pptr" else "BugReveal"
        for cutoff in spec.cumulative_cutoffs:
            metric_col = f"success_at_{cutoff}"
            metric_name = f"{metric_prefix}@{cutoff}"
            for setting in (*PROMPT_SETTINGS, "ALL"):
                rows.extend(
                    pairwise_stats_for_metric(case_df, spec, metric_col, metric_name, setting)
                )
    return pd.DataFrame(rows)


def build_feedback_gain_case_level(
        no_feedback_df: pd.DataFrame,
        feedback_df: pd.DataFrame,
        spec: TaskSpec,
        cutoff: int,
) -> pd.DataFrame:
    left = no_feedback_df[
        ["model", "prompt_setting", "case_id", f"success_at_{cutoff}"]
    ].rename(columns={f"success_at_{cutoff}": "no_feedback_success"})
    right = feedback_df[
        ["model", "prompt_setting", "case_id", f"success_at_{cutoff}"]
    ].rename(columns={f"success_at_{cutoff}": "feedback_success"})
    merged = left.merge(right, on=["model", "prompt_setting", "case_id"], how="inner")
    merged["gain"] = merged["feedback_success"] - merged["no_feedback_success"]
    merged["rq"] = spec.rq
    merged["task"] = spec.task
    merged["metric"] = "DeltaPPTR@3" if spec.task == "valid_pptr" else "DeltaBugReveal@30"
    return merged


def build_feedback_summary(
        summary_df: pd.DataFrame,
        no_feedback_experiment: str,
        feedback_experiment: str,
        task: str,
        final_metric: str,
        delta_name: str,
) -> pd.DataFrame:
    left = summary_df[
        (summary_df["experiment"] == no_feedback_experiment)
        & (summary_df["task"] == task)
        & (summary_df["prompt_setting"].isin([*PROMPT_SETTINGS, "ALL"]))
    ].copy()
    right = summary_df[
        (summary_df["experiment"] == feedback_experiment)
        & (summary_df["task"] == task)
        & (summary_df["prompt_setting"].isin([*PROMPT_SETTINGS, "ALL"]))
    ].copy()
    cols = ["model", "prompt_setting", final_metric, f"{final_metric}_count",
            "actual_calls_per_case", "elapsed_ms_per_case_mean"]
    merged = left[cols].merge(
        right[cols],
        on=["model", "prompt_setting"],
        suffixes=("_no_feedback", "_with_feedback"),
    )
    merged[delta_name] = merged[f"{final_metric}_with_feedback"] - merged[f"{final_metric}_no_feedback"]
    merged["delta_elapsed_ms_per_case"] = (
        merged["elapsed_ms_per_case_mean_with_feedback"]
        - merged["elapsed_ms_per_case_mean_no_feedback"]
    )
    merged["delta_calls_per_case"] = (
        merged["actual_calls_per_case_with_feedback"]
        - merged["actual_calls_per_case_no_feedback"]
    )
    return merged


def build_feedback_pairwise_stats(
        valid_gain: pd.DataFrame,
        invalid_gain: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    for gain_df, rq, task, metric in (
            (valid_gain, "RQ2.1", "valid_pptr", "DeltaPPTR@3"),
            (invalid_gain, "RQ2.2", "invalid_bug", "DeltaBugReveal@30"),
    ):
        for setting in (*PROMPT_SETTINGS, "ALL"):
            part = gain_df if setting == "ALL" else gain_df[gain_df["prompt_setting"] == setting]
            for i, model1 in enumerate(FINAL_MODELS):
                x = part[part["model"] == model1]["gain"].astype(float).to_numpy()
                for model2 in FINAL_MODELS[i + 1:]:
                    y = part[part["model"] == model2]["gain"].astype(float).to_numpy()
                    p_value = mann_whitney_pvalue(x, y)
                    a12 = vargha_delaney_a12(x, y)
                    rows.append(
                        {
                            "rq": rq,
                            "experiment": "feedback_gain",
                            "task": task,
                            "metric": metric,
                            "prompt_setting": setting,
                            "model_1": model1,
                            "model_2": model2,
                            "n_1": len(x),
                            "n_2": len(y),
                            "mean_1": float(np.mean(x)) if len(x) else float("nan"),
                            "mean_2": float(np.mean(y)) if len(y) else float("nan"),
                            "p_value": p_value,
                            "a12_model_1_vs_model_2": a12,
                            "symbol": comparison_symbol(p_value, a12),
                        }
                    )
    return pd.DataFrame(rows)


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def markdown_table(df: pd.DataFrame, columns: Sequence[str], max_rows: Optional[int] = None) -> str:
    if max_rows is not None:
        df = df.head(max_rows)
    if df.empty:
        return "_无数据_"
    lines = []
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    lines.extend([header, sep])
    for _, row in df.iterrows():
        values = [str(row.get(col, "")) for col in columns]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def metric_summary_for_md(summary_df: pd.DataFrame, task: str, setting: str, metric: str) -> pd.DataFrame:
    rows = summary_df[
        (summary_df["task"] == task)
        & (summary_df["prompt_setting"] == setting)
    ][[
        "model", metric, f"{metric}_count", "actual_calls_per_case",
        "elapsed_ms_per_generation_mean", "total_tokens_per_call",
    ]].copy()
    rows = rows.sort_values(by=metric, ascending=False)
    rows["rate"] = rows[metric].map(pct)
    rows["count"] = rows[f"{metric}_count"].astype(int)
    rows["attempts/case"] = rows["actual_calls_per_case"].map(lambda x: f"{x:.2f}")
    rows["time/attempt(s)"] = rows["elapsed_ms_per_generation_mean"].map(lambda x: f"{x / 1000:.2f}")
    rows["token/attempt"] = rows["total_tokens_per_call"].map(
        lambda x: "--" if pd.isna(x) else f"{x:.1f}"
    )
    return rows[["model", "count", "rate", "attempts/case", "token/attempt", "time/attempt(s)"]]


def best_rows_by_metric(summary_df: pd.DataFrame, task: str, setting: str, metric: str, n: int = 3) -> pd.DataFrame:
    rows = metric_summary_for_md(summary_df, task, setting, metric).copy()
    rows["_rate"] = rows["rate"].str.rstrip("%").astype(float)
    return rows.sort_values("_rate", ascending=False).drop(columns=["_rate"]).head(n)


def significant_counts(pairwise_df: pd.DataFrame) -> pd.DataFrame:
    grouped = pairwise_df.groupby(["rq", "metric", "prompt_setting", "symbol"], dropna=False).size().reset_index(name="n")
    pivot = grouped.pivot_table(
        index=["rq", "metric", "prompt_setting"],
        columns="symbol",
        values="n",
        fill_value=0,
        aggfunc="sum",
    ).reset_index()
    for col in ("m1_better", "m2_better", "no_sig"):
        if col not in pivot:
            pivot[col] = 0
    pivot["significant_pairs"] = pivot["m1_better"] + pivot["m2_better"]
    return pivot


def trend_table_for_md(
        trend_df: pd.DataFrame,
        *,
        experiment: str,
        setting: str,
        rounds: Sequence[int],
) -> pd.DataFrame:
    part = trend_df[
        (trend_df["experiment"] == experiment)
        & (trend_df["prompt_setting"] == setting)
        & (trend_df["round"].isin(rounds))
    ].copy()
    rows = []
    for model, group in part.groupby("model", sort=False):
        group = group.sort_values("round")
        row: Dict[str, Any] = {"model": model}
        for _, item in group.iterrows():
            round_id = int(item["round"])
            row[f"@{round_id}"] = (
                f"{item['cumulative_count']}/{item['n_cases']} "
                f"({item['cumulative_rate'] * 100:.2f}%)"
            )
            row[f"New@{round_id}"] = (
                f"{item['new_count']} ({item['new_rate'] * 100:.2f}%)"
            )
        final_item = group.sort_values("round").tail(1).iloc[0]
        row["attempts/case"] = f"{final_item['calls_until_round_per_case']:.2f}"
        row["time/attempt(s)"] = f"{final_item['elapsed_ms_until_round_per_executed_call'] / 1000:.2f}"
        rows.append(row)
    return pd.DataFrame(rows)


def time_table_for_md(time_df: pd.DataFrame, *, setting: str) -> pd.DataFrame:
    part = time_df[time_df["prompt_setting"] == setting].copy()
    rows = []
    for _, item in part.iterrows():
        rows.append(
            {
                "rq": item["rq"],
                "model": item["model"],
                "budget": int(item["budget"]),
                "attempts/case": f"{item['calls_per_case']:.2f}",
                "token/attempt": "--" if pd.isna(item["total_tokens_per_call"]) else f"{item['total_tokens_per_call']:.1f}",
                "time/attempt(s)": f"{item['elapsed_ms_per_executed_call'] / 1000:.2f}",
            }
        )
    return pd.DataFrame(rows)


def trend_tables_by_setting_for_md(
        trend_df: pd.DataFrame,
        *,
        experiment: str,
        rounds: Sequence[int],
        columns: Sequence[str],
) -> str:
    sections = []
    for setting in PROMPT_SETTINGS:
        table = trend_table_for_md(
            trend_df,
            experiment=experiment,
            setting=setting,
            rounds=rounds,
        )
        sections.append(
            f"**{setting}**\n\n"
            f"{markdown_table(table, columns)}"
        )
    return "\n\n".join(sections)


def feedback_tables_by_setting_for_md(
        feedback_df: pd.DataFrame,
        *,
        metric: str,
        delta_metric: str,
) -> str:
    sections = []
    for setting in PROMPT_SETTINGS:
        part = feedback_df[feedback_df["prompt_setting"] == setting].copy()
        part = part.sort_values(delta_metric, ascending=False)
        part["no-feedback"] = part[f"{metric}_no_feedback"].map(pct)
        part["with-feedback"] = part[f"{metric}_with_feedback"].map(pct)
        part["gain"] = part[delta_metric].map(lambda x: f"{x * 100:+.2f} pp")
        part["attempts/case(no)"] = part["actual_calls_per_case_no_feedback"].map(lambda x: f"{x:.2f}")
        part["attempts/case(fb)"] = part["actual_calls_per_case_with_feedback"].map(lambda x: f"{x:.2f}")
        part["time/attempt(no,s)"] = (
            part["elapsed_ms_per_case_mean_no_feedback"]
            / part["actual_calls_per_case_no_feedback"].replace(0, np.nan)
        ).map(
            lambda x: f"{x / 1000:.2f}"
        )
        part["time/attempt(fb,s)"] = (
            part["elapsed_ms_per_case_mean_with_feedback"]
            / part["actual_calls_per_case_with_feedback"].replace(0, np.nan)
        ).map(
            lambda x: f"{x / 1000:.2f}"
        )
        sections.append(
            f"**{setting}**\n\n"
            f"{markdown_table(part, ['model', 'no-feedback', 'with-feedback', 'gain', 'attempts/case(no)', 'attempts/case(fb)', 'time/attempt(no,s)', 'time/attempt(fb,s)'])}"
        )
    return "\n\n".join(sections)


def render_analysis_section(
        output_dir: Path,
        summary_df: pd.DataFrame,
        feedback_valid: pd.DataFrame,
        feedback_invalid: pd.DataFrame,
        valid_trend: pd.DataFrame,
        invalid_trend: pd.DataFrame,
        time_summary: pd.DataFrame,
        pairwise_df: pd.DataFrame,
        completeness_df: pd.DataFrame,
) -> str:
    rq1_valid_summary = summary_df[summary_df["experiment"] == "rq1_valid"].copy()
    rq1_invalid_summary = summary_df[summary_df["experiment"] == "rq1_invalid"].copy()
    all_valid_rq1 = metric_summary_for_md(rq1_valid_summary, "valid_pptr", "ALL", "PPTR@3")
    all_invalid_rq1 = metric_summary_for_md(rq1_invalid_summary, "invalid_bug", "ALL", "BugReveal@30")
    valid_feedback_all = feedback_valid[feedback_valid["prompt_setting"] == "ALL"].copy()
    invalid_feedback_all = feedback_invalid[feedback_invalid["prompt_setting"] == "ALL"].copy()
    valid_feedback_all["no-feedback"] = valid_feedback_all["PPTR@3_no_feedback"].map(pct)
    valid_feedback_all["with-feedback"] = valid_feedback_all["PPTR@3_with_feedback"].map(pct)
    valid_feedback_all["gain"] = valid_feedback_all["DeltaPPTR@3"].map(lambda x: f"{x * 100:+.2f} pp")
    invalid_feedback_all["no-feedback"] = invalid_feedback_all["BugReveal@30_no_feedback"].map(pct)
    invalid_feedback_all["with-feedback"] = invalid_feedback_all["BugReveal@30_with_feedback"].map(pct)
    invalid_feedback_all["gain"] = invalid_feedback_all["DeltaBugReveal@30"].map(lambda x: f"{x * 100:+.2f} pp")

    sig_counts = significant_counts(pairwise_df)
    sig_all = sig_counts[sig_counts["prompt_setting"] == "ALL"].copy()
    sig_all = sig_all.sort_values(["rq", "metric"])
    sig_all_display = sig_all[["rq", "metric", "significant_pairs", "no_sig"]].copy()
    sig_all_display["significant_pairs"] = sig_all_display["significant_pairs"].astype(int)
    sig_all_display["no_sig"] = sig_all_display["no_sig"].astype(int)

    completeness_display = completeness_df.copy()
    for col in ("row_count", "null_eval_count", "eval_1_count", "eval_0_count"):
        completeness_display[col] = completeness_display[col].astype(int)

    rq1_valid_trend_table = trend_table_for_md(
        valid_trend, experiment="rq1_valid", setting="ALL", rounds=(1, 2, 3)
    )
    rq2_valid_trend_table = trend_table_for_md(
        valid_trend, experiment="rq2_valid_feedback", setting="ALL", rounds=(1, 2, 3)
    )
    rq1_invalid_trend_table = trend_table_for_md(
        invalid_trend, experiment="rq1_invalid", setting="ALL", rounds=(1, 5, 10, 30)
    )
    rq2_invalid_trend_table = trend_table_for_md(
        invalid_trend,
        experiment="rq2_invalid_feedback",
        setting="ALL",
        rounds=(1, 5, 10, 30),
    )
    time_all_table = time_table_for_md(time_summary, setting="ALL")
    valid_trend_columns = [
        "model", "@1", "New@1", "@2", "New@2", "@3", "New@3", "time/attempt(s)"
    ]
    invalid_trend_columns = [
        "model", "@1", "New@1", "@5", "New@5", "@10", "New@10", "@30", "New@30",
        "time/attempt(s)"
    ]
    valid_feedback_trend_columns = [
        "model", "@1", "New@1", "@2", "New@2", "@3", "New@3", "attempts/case", "time/attempt(s)"
    ]
    invalid_feedback_trend_columns = [
        "model", "@1", "New@1", "@5", "New@5", "@10", "New@10", "@30", "New@30",
        "attempts/case", "time/attempt(s)"
    ]
    rq1_valid_setting_tables = trend_tables_by_setting_for_md(
        valid_trend,
        experiment="rq1_valid",
        rounds=(1, 2, 3),
        columns=valid_trend_columns,
    )
    rq2_valid_setting_tables = trend_tables_by_setting_for_md(
        valid_trend,
        experiment="rq2_valid_feedback",
        rounds=(1, 2, 3),
        columns=valid_feedback_trend_columns,
    )
    rq1_invalid_setting_tables = trend_tables_by_setting_for_md(
        invalid_trend,
        experiment="rq1_invalid",
        rounds=(1, 5, 10, 30),
        columns=invalid_trend_columns,
    )
    rq2_invalid_setting_tables = trend_tables_by_setting_for_md(
        invalid_trend,
        experiment="rq2_invalid_feedback",
        rounds=(1, 5, 10, 30),
        columns=invalid_feedback_trend_columns,
    )
    rq2_valid_setting_gain_tables = feedback_tables_by_setting_for_md(
        feedback_valid,
        metric="PPTR@3",
        delta_metric="DeltaPPTR@3",
    )
    rq2_invalid_setting_gain_tables = feedback_tables_by_setting_for_md(
        feedback_invalid,
        metric="BugReveal@30",
        delta_metric="DeltaBugReveal@30",
    )

    files = [
        "rq_metric_summary.csv",
        "rq_token_summary.csv",
        "rq_token_session_coverage.csv",
        "rq_latex_tables.tex",
        "rq_valid_trend.csv",
        "rq_invalid_trend.csv",
        "rq_time_summary.csv",
        "rq1_valid_case_level.csv",
        "rq1_invalid_case_level.csv",
        "rq2_valid_feedback_case_level.csv",
        "rq2_invalid_feedback_case_level.csv",
        "rq_invalid_category_summary.csv",
        "rq2_valid_feedback_gain.csv",
        "rq2_invalid_feedback_gain.csv",
        "pairwise_llm_stats.csv",
    ]
    file_lines = "\n".join(
        f"- [{name}]({(output_dir / name).as_posix()})" for name in files
    )

    return f"""
{ANALYSIS_START}
## RQ1/RQ2 数据整理与统计结果（自动生成）

生成时间：脚本运行时从 `t_llm_text_generation_curated` 读取数据，并按本文档定义的 case-level 累计口径整理。RQ3 仍在运行，本节暂不包含 RQ3。

### 数据完整性

{markdown_table(completeness_display, ["experiment", "row_count", "null_eval_count", "eval_1_count", "eval_0_count", "distinct_models", "distinct_settings"])}

说明：`null_eval_count=0` 表示对应实验的 `eval_success` 已全部落为 0/1。失败生成、解析失败、空 JSON、resource-id 不匹配和 validator exception 均按失败计入分母。

### 输出文件

{file_lines}

### RQ1.1：Page pass-through 结果

下面先给出三种 prompting setting 合并后的 PPTR@3 排名。完整的 extracted/xml/vision 分 setting 数据见 `rq_metric_summary.csv`。

{markdown_table(all_valid_rq1, ["model", "count", "rate", "token/attempt", "time/attempt(s)"])}

Top-3 模型：

{markdown_table(best_rows_by_metric(rq1_valid_summary, "valid_pptr", "ALL", "PPTR@3"), ["model", "count", "rate", "token/attempt", "time/attempt(s)"])}

PPTR 逐轮递增趋势（ALL setting；`@i` 为累计通过数/比例，`New@i` 为首次在第 i 次通过的新增数/比例）：

{markdown_table(rq1_valid_trend_table, valid_trend_columns)}

按 prompt setting 分解的 PPTR 逐轮递增趋势如下：

{rq1_valid_setting_tables}

### RQ1.2：Bug detection 结果

下面给出三种 prompting setting 合并后的 BugReveal@30 排名。完整的 BugReveal@1/@5/@10/@30 和 bug category breakdown 见 CSV。

{markdown_table(all_invalid_rq1, ["model", "count", "rate", "token/attempt", "time/attempt(s)"])}

Top-3 模型：

{markdown_table(best_rows_by_metric(rq1_invalid_summary, "invalid_bug", "ALL", "BugReveal@30"), ["model", "count", "rate", "token/attempt", "time/attempt(s)"])}

BugReveal 递增趋势（ALL setting；完整 1--30 轮见 `rq_invalid_trend.csv`，这里展示 @1/@5/@10/@30）：

{markdown_table(rq1_invalid_trend_table, invalid_trend_columns)}

按 prompt setting 分解的 BugReveal 递增趋势如下：

{rq1_invalid_setting_tables}

### RQ2.1：Feedback 对 page pass-through 的提升

三种 prompting setting 合并后，with-feedback 与 no-feedback 的 PPTR@3 对比如下。

{markdown_table(valid_feedback_all.sort_values("DeltaPPTR@3", ascending=False), ["model", "no-feedback", "with-feedback", "gain"])}

With-feedback PPTR 逐轮递增趋势（ALL setting）：

{markdown_table(rq2_valid_trend_table, valid_feedback_trend_columns)}

按 prompt setting 分解的 feedback 提升如下：

{rq2_valid_setting_gain_tables}

按 prompt setting 分解的 with-feedback PPTR 逐轮递增趋势如下：

{rq2_valid_setting_tables}

### RQ2.2：Feedback 对 bug detection 的提升

三种 prompting setting 合并后，with-feedback 与 no-feedback 的 BugReveal@30 对比如下。

{markdown_table(invalid_feedback_all.sort_values("DeltaBugReveal@30", ascending=False), ["model", "no-feedback", "with-feedback", "gain"])}

With-feedback BugReveal 递增趋势（ALL setting；完整 1--30 轮见 `rq_invalid_trend.csv`，这里展示 @1/@5/@10/@30）：

{markdown_table(rq2_invalid_trend_table, invalid_feedback_trend_columns)}

按 prompt setting 分解的 feedback 提升如下：

{rq2_invalid_setting_gain_tables}

按 prompt setting 分解的 with-feedback BugReveal 递增趋势如下：

{rq2_invalid_setting_tables}

### 时间开销摘要

下表为 ALL setting 下每个模型在各 RQ 最终预算处的实际尝试次数和每次生成开销。RQ2 使用 feedback workflow，因此 `attempts/case` 需要单独报告；RQ1 的预算固定，因此论文主表不展示该列。

{markdown_table(time_all_table, ["rq", "model", "budget", "attempts/case", "token/attempt", "time/attempt(s)"])}

### 两两 LLM 统计检验摘要

统计检验采用论文中定义的 two-tailed Mann–Whitney U test 和 Vargha–Delaney \\(\\hat{{A}}_{{12}}\\)。`pairwise_llm_stats.csv` 包含所有模型对、所有 primary cutoffs、分 setting 以及 ALL setting 的 p-value 和 effect size。下表只汇总 ALL setting 下每个指标的显著模型对数量。

{markdown_table(sig_all_display, ["rq", "metric", "significant_pairs", "no_sig"])}

判定口径：`p_value < 0.05` 且 `A12 > 0.50` 表示 `model_1` 显著优于 `model_2`；`p_value < 0.05` 且 `A12 < 0.50` 表示 `model_2` 显著优于 `model_1`；否则记为无显著差异。

### 当前结果解读

- RQ1.1 的 PPTR@3 整体较高，说明多数 LLM 在 valid page pass-through 任务中具备较强的基础生成能力。需要在正文中进一步结合 PPTR@1 到 PPTR@3 的提升说明少量重试的收益。
- RQ1.2 的 BugReveal@30 明显低于 PPTR@3，符合 bug detection 更接近 search process 的任务特征。因此 RQ1.2 应重点展示累计 reveal curve 和 bug category breakdown，而不只报告最终平均值。
- RQ2.1 中 feedback 对 PPTR@3 的收益需要结合 gain 表逐模型分析；如果某些模型 gain 较小，可能说明其 no-feedback 已经接近饱和，feedback 的边际空间有限。
- RQ2.2 中 feedback 的价值应优先看 BugReveal@5/@10/@30 的曲线是否更早 reveal bug；即使最终 BugReveal@30 提升有限，早期 reveal 的提升也可以作为效率收益讨论。
- Token/Attempt 和 Time/Attempt 通过 `session_ids_json` 中的每个 session ID 关联 `t_llm_text_generation_curated_token.session_id` 计算，不使用 `curated_id` 关联 token。若一条 generation 记录包含多个 session ID（feedback 场景），该记录的 token/time 等于这些 session 的 token/time 总和；最终除以 generation attempt 槽位数，得到生成一个 text input 的平均开销。RQ2 是独立运行的 with-feedback end-to-end 流程，attempt/index 从 1 开始，因此 RQ2 的 token/time 直接统计 RQ2 自身所有尝试，不再解释为 RQ1 基线之外的 feedback 增量。无 session/token 记录的失败调用仍计入 attempt 分母；若没有 token/time 记录，则 token/time 分子按 0 处理。
{ANALYSIS_END}
"""


def update_markdown(md_path: Path, analysis: str) -> None:
    text = md_path.read_text(encoding="utf-8")
    if ANALYSIS_START in text and ANALYSIS_END in text:
        before = text.split(ANALYSIS_START, 1)[0].rstrip()
        after = text.split(ANALYSIS_END, 1)[1].lstrip()
        new_text = before + "\n\n" + analysis.strip() + "\n"
        if after:
            new_text += "\n" + after
    else:
        new_text = text.rstrip() + "\n\n" + analysis.strip() + "\n"
    md_path.write_text(new_text, encoding="utf-8")


def latex_pct_count(count: float, denom: int, *, bold: bool = False) -> str:
    text = (
        f"{count:.1f} ({count / denom * 100:.1f}\\%)"
        if bold or abs(count - round(count)) > 1e-9
        else f"{int(round(count))} ({count / denom * 100:.1f}\\%)"
    )
    return rf"\textbf{{{text}}}" if bold else text


def latex_number(value: float, decimals: int = 1) -> str:
    return f"{value:,.{decimals}f}"


def latex_token(value: float, *, bold: bool = False) -> str:
    if value is None or math.isnan(value):
        return "--"
    text = latex_number(value)
    return rf"\textbf{{{text}}}" if bold else text


def latex_time(value_ms: float, *, bold: bool = False) -> str:
    text = latex_number(value_ms / 1000.0)
    return rf"\textbf{{{text}}}" if bold else text


def summary_item(summary_df: pd.DataFrame, *, experiment: str, model: str, setting: str) -> pd.Series:
    part = summary_df[
        (summary_df["experiment"] == experiment)
        & (summary_df["model"] == model)
        & (summary_df["prompt_setting"] == setting)
    ]
    if part.empty:
        raise KeyError((experiment, model, setting))
    return part.iloc[0]


def average_summary_values(rows: List[pd.Series], spec: TaskSpec) -> Dict[str, float]:
    out: Dict[str, float] = {}
    metric_prefix = "PPTR" if spec.task == "valid_pptr" else "BugReveal"
    new_prefix = "NewPPTR" if spec.task == "valid_pptr" else "NewBugReveal"
    for cutoff in spec.cumulative_cutoffs:
        out[f"{metric_prefix}@{cutoff}_count"] = float(np.mean([r[f"{metric_prefix}@{cutoff}_count"] for r in rows]))
    for idx in range(1, spec.budget + 1):
        key = f"{new_prefix}_{idx}_count"
        out[key] = float(np.mean([r[key] for r in rows]))
    for key in (
            "actual_calls_per_case",
            "prompt_tokens_per_call",
            "completion_tokens_per_call",
            "total_tokens_per_call",
            "prompt_tokens_per_case",
            "completion_tokens_per_case",
            "total_tokens_per_case",
            "elapsed_ms_per_generation_mean",
    ):
        values = [float(r[key]) for r in rows if not pd.isna(r[key])]
        out[key] = float(np.mean(values)) if values else float("nan")
    out["elapsed_ms_per_case_mean"] = float(np.mean([r["elapsed_ms_per_case_mean"] for r in rows]))
    return out


def summary_cumulative_count(item: pd.Series, spec: TaskSpec, cutoff: int) -> float:
    metric_prefix = "PPTR" if spec.task == "valid_pptr" else "BugReveal"
    existing_key = f"{metric_prefix}@{cutoff}_count"
    if existing_key in item.index and not pd.isna(item[existing_key]):
        return float(item[existing_key])
    new_prefix = "NewPPTR" if spec.task == "valid_pptr" else "NewBugReveal"
    total = 0.0
    for idx in range(1, cutoff + 1):
        key = f"{new_prefix}_{idx}_count"
        if key in item.index and not pd.isna(item[key]):
            total += float(item[key])
    return total


def average_cumulative_count(rows: List[pd.Series], spec: TaskSpec, cutoff: int) -> float:
    return float(np.mean([summary_cumulative_count(r, spec, cutoff) for r in rows]))


def latex_trend_table(
        summary_df: pd.DataFrame,
        *,
        experiment: str,
        label: str,
        caption: str,
) -> str:
    spec = TASK_SPECS[experiment]
    denom = len(spec.cases)
    metric_prefix = "PPTR" if spec.task == "valid_pptr" else "BugReveal"
    new_prefix = "NewPPTR" if spec.task == "valid_pptr" else "NewBugReveal"
    is_valid = spec.task == "valid_pptr"
    show_attempts = experiment.startswith("rq2_")
    if is_valid and not show_attempts:
        col_spec = "llrrrrrrrrrr"
        metric_header = r"\multicolumn{6}{c}{\textbf{PPTR}}"
        cmidrule = r"\cline{3-8}\cline{9-11}"
        metric_cols = r"& & \textbf{First@1} & \textbf{@1} & \textbf{First@2} & \textbf{@2} & \textbf{First@3} & \textbf{@3} & \textbf{Prompt} & \textbf{Completion} & \textbf{Total} & \\"
        overhead_header = r"\multicolumn{3}{c}{\textbf{Token/Attempt}} & \multirow{2}{*}{\makecell[r]{\textbf{Time}/\\\textbf{Attempt (s)}}}"
    elif is_valid:
        col_spec = "llrrrrrrrrrrr"
        metric_header = r"\multicolumn{6}{c}{\textbf{PPTR}}"
        cmidrule = r"\cline{3-8}\cline{10-12}"
        metric_cols = r"& & \textbf{First@1} & \textbf{@1} & \textbf{First@2} & \textbf{@2} & \textbf{First@3} & \textbf{@3} & & \textbf{Prompt} & \textbf{Completion} & \textbf{Total} & \\"
        overhead_header = r"\multirow{2}{*}{\makecell[c]{\textbf{Attempts}/\\\textbf{Case}}} & \multicolumn{3}{c}{\textbf{Token/Attempt}} & \multirow{2}{*}{\makecell[r]{\textbf{Time}/\\\textbf{Attempt (s)}}}"
    elif not show_attempts:
        col_spec = "llrrrrrrrrrrrr"
        metric_header = r"\multicolumn{8}{c}{\textbf{Bug-Reveal}}"
        cmidrule = r"\cline{3-10}\cline{11-13}"
        metric_cols = r"& & \textbf{First@1} & \textbf{@1} & \textbf{First@5} & \textbf{@5} & \textbf{First@10} & \textbf{@10} & \textbf{First@30} & \textbf{@30} & \textbf{Prompt} & \textbf{Completion} & \textbf{Total} & \\"
        overhead_header = r"\multicolumn{3}{c}{\textbf{Token/Attempt}} & \multirow{2}{*}{\makecell[r]{\textbf{Time}/\\\textbf{Attempt (s)}}}"
    else:
        col_spec = "llrrrrrrrrrrrrr"
        metric_header = r"\multicolumn{8}{c}{\textbf{Bug-Reveal}}"
        cmidrule = r"\cline{3-10}\cline{12-14}"
        metric_cols = r"& & \textbf{First@1} & \textbf{@1} & \textbf{First@5} & \textbf{@5} & \textbf{First@10} & \textbf{@10} & \textbf{First@30} & \textbf{@30} & & \textbf{Prompt} & \textbf{Completion} & \textbf{Total} & \\"
        overhead_header = r"\multirow{2}{*}{\makecell[c]{\textbf{Attempts}/\\\textbf{Case}}} & \multicolumn{3}{c}{\textbf{Token/Attempt}} & \multirow{2}{*}{\makecell[r]{\textbf{Time}/\\\textbf{Attempt (s)}}}"

    lines = [
        r"\begin{table*}[!t]",
        r"    \centering",
        r"    \scriptsize",
        rf"    \caption{{\revo{{{caption}}}}}",
        rf"    \label{{{label}}}",
        r"    \setlength\tabcolsep{1.0pt}",
        rf"    \begin{{tabular}}{{{col_spec}}}",
        r"        \hline",
        rf"        \multirow{{2}}{{*}}{{\textbf{{Setting}}}} & \multirow{{2}}{{*}}{{\textbf{{LLM}}}} & {metric_header} & {overhead_header} \\",
        f"        {cmidrule}",
        f"        {metric_cols}",
        r"        \hline",
    ]

    for setting_index, setting in enumerate(PROMPT_SETTINGS):
        setting_rows = [summary_item(summary_df, experiment=experiment, model=m, setting=setting) for m in LLM_TABLE_ORDER]
        for row_index, model in enumerate(LLM_TABLE_ORDER):
            item = setting_rows[row_index]
            prefix = SETTING_LABEL[setting] if row_index == 0 else ""
            cells = [prefix, MODEL_MACRO[model]]
            if is_valid:
                for cutoff in (1, 2, 3):
                    metric_count = summary_cumulative_count(item, spec, cutoff)
                    new_count = item[f"{new_prefix}_{cutoff}_count"]
                    cells.append(latex_pct_count(new_count, denom))
                    cells.append(latex_pct_count(metric_count, denom))
            else:
                for cutoff in (1, 5, 10, 30):
                    metric_count = item[f"{metric_prefix}@{cutoff}_count"]
                    new_count = item[f"{new_prefix}_{cutoff}_count"]
                    cells.append(latex_pct_count(new_count, denom))
                    cells.append(latex_pct_count(metric_count, denom))
            if show_attempts:
                cells.append(latex_number(float(item["actual_calls_per_case"])))
            cells.extend(
                [
                    latex_token(float(item["prompt_tokens_per_call"])),
                    latex_token(float(item["completion_tokens_per_call"])),
                    latex_token(float(item["total_tokens_per_call"])),
                    latex_time(float(item["elapsed_ms_per_generation_mean"])),
                ]
            )
            lines.append("        " + " & ".join(cells) + r" \\")

        avg = average_summary_values(setting_rows, spec)
        cells = ["", r"\textbf{Avg.}"]
        if is_valid:
            for cutoff in (1, 2, 3):
                cells.append(latex_pct_count(avg[f"{new_prefix}_{cutoff}_count"], denom, bold=True))
                cells.append(latex_pct_count(average_cumulative_count(setting_rows, spec, cutoff), denom, bold=True))
        else:
            for cutoff in (1, 5, 10, 30):
                cells.append(latex_pct_count(avg[f"{new_prefix}_{cutoff}_count"], denom, bold=True))
                cells.append(latex_pct_count(avg[f"{metric_prefix}@{cutoff}_count"], denom, bold=True))
        if show_attempts:
            cells.append(rf"\textbf{{{latex_number(avg['actual_calls_per_case'])}}}")
        cells.extend(
            [
                latex_token(avg["prompt_tokens_per_call"], bold=True),
                latex_token(avg["completion_tokens_per_call"], bold=True),
                latex_token(avg["total_tokens_per_call"], bold=True),
                latex_time(avg["elapsed_ms_per_generation_mean"], bold=True),
            ]
        )
        lines.append("        " + " & ".join(cells) + r" \\")
        if setting_index != len(PROMPT_SETTINGS) - 1:
            lines.append(r"        \hline")

    lines.extend(
        [
            r"        \hline",
            r"    \end{tabular}",
            r"\end{table*}",
        ]
    )
    return "\n".join(lines)


def latex_stat_range(summary: Dict[str, Any]) -> Tuple[str, str, str]:
    counts = f"{summary['better']}/{summary['same']}/{summary['worse']}"
    p_range = f"[{format_p_value(summary['p_min'])}, {format_p_value(summary['p_max'])}]"
    a_range = f"[{format_a12(summary['a12_min'])}, {format_a12(summary['a12_max'])}]"
    return counts, p_range, a_range


def latex_setting_pair_cell(result: Dict[str, Any]) -> str:
    if math.isnan(result["p"]) or math.isnan(result["a12"]):
        return "--"
    return (
        f"{symbol_to_latex(result['symbol'])}"
        f" ({format_p_value(result['p'], compact=True)}, {format_a12(result['a12'], compact=True)})"
    )


def latex_stat_table(
        case_df: pd.DataFrame,
        *,
        spec: TaskSpec,
        label: str,
        caption: str,
) -> str:
    final_metric_column = max((f"success_at_{c}" for c in spec.cumulative_cutoffs), key=lambda x: int(x.rsplit("_", 1)[1]))
    final_metric_name = spec.final_metric_name
    lines = [
        r"\begin{table*}[!t]",
        r"    \centering",
        r"    \scriptsize",
        rf"    \caption{{\revo{{{caption}}}}}",
        rf"    \label{{{label}}}",
        r"    \setlength\tabcolsep{1.0pt}",
        r"    \begin{tabular}{llccccccccc}",
        r"        \hline",
        r"        \multicolumn{11}{l}{\textbf{Panel A: LLM comparisons within the same prompting setting}} \\",
        rf"        \textbf{{Setting}} & \textbf{{LLM}} & \multicolumn{{3}}{{c}}{{\textbf{{{final_metric_name}}}}} & \multicolumn{{3}}{{c}}{{\textbf{{Token/Attempt}}}} & \multicolumn{{3}}{{c}}{{\textbf{{Time/Attempt}}}} \\",
        r"        \cline{3-5}\cline{6-8}\cline{9-11}",
        r"        & & \textbf{\#\ding{52}/\#\ding{109}/\#\ding{54}} & \textbf{$p$} & \textbf{$\hat{A}_{12}$} & \textbf{\#\ding{52}/\#\ding{109}/\#\ding{54}} & \textbf{$p$} & \textbf{$\hat{A}_{12}$} & \textbf{\#\ding{52}/\#\ding{109}/\#\ding{54}} & \textbf{$p$} & \textbf{$\hat{A}_{12}$} \\",
        r"        \hline",
    ]
    for setting_index, setting in enumerate(PROMPT_SETTINGS):
        for row_index, model in enumerate(LLM_TABLE_ORDER):
            prefix = SETTING_LABEL[setting] if row_index == 0 else ""
            metric_s = latex_stat_range(
                pairwise_summary_against_peers(
                    case_df,
                    setting=setting,
                    model=model,
                    metric_column=final_metric_column,
                    smaller_is_better=False,
                )
            )
            token_s = latex_stat_range(
                pairwise_summary_against_peers(
                    case_df,
                    setting=setting,
                    model=model,
                    metric_column="token_per_call",
                    smaller_is_better=True,
                )
            )
            time_s = latex_stat_range(
                pairwise_summary_against_peers(
                    case_df,
                    setting=setting,
                    model=model,
                    metric_column="time_per_call",
                    smaller_is_better=True,
                )
            )
            cells = [
                prefix,
                MODEL_MACRO[model],
                *metric_s,
                *token_s,
                *time_s,
            ]
            lines.append("        " + " & ".join(cells) + r" \\")
        if setting_index != len(PROMPT_SETTINGS) - 1:
            lines.append(r"        \hline")
    lines.extend(
        [
            r"        \hline",
            r"    \end{tabular}",
            r"    \vspace{1mm}",
            "",
            r"    \setlength\tabcolsep{0.7pt}",
            r"    \begin{tabular}{lccc|ccc|ccc}",
            r"        \hline",
            r"        \multicolumn{10}{l}{\textbf{Panel B: Prompting-setting comparisons within the same LLM}} \\",
            rf"        \textbf{{LLM}} & \multicolumn{{3}}{{c}}{{\textbf{{{final_metric_name}}}}} & \multicolumn{{3}}{{c}}{{\textbf{{Token/Attempt}}}} & \multicolumn{{3}}{{c}}{{\textbf{{Time/Attempt}}}} \\",
            r"        \cline{2-4}\cline{5-7}\cline{8-10}",
            r"        & \textbf{Extracted/XML} & \textbf{Extracted/Vision} & \textbf{XML/Vision} & \textbf{Extracted/XML} & \textbf{Extracted/Vision} & \textbf{XML/Vision} & \textbf{Extracted/XML} & \textbf{Extracted/Vision} & \textbf{XML/Vision} \\",
            r"        \hline",
        ]
    )
    pairs = (("extracted", "xml"), ("extracted", "vision"), ("xml", "vision"))
    for model in LLM_TABLE_ORDER:
        cells = [MODEL_MACRO[model]]
        for metric_column, smaller in (
                (final_metric_column, False),
                ("token_per_call", True),
                ("time_per_call", True),
        ):
            for s1, s2 in pairs:
                result = pairwise_between_two_groups(
                    case_df,
                    model=model,
                    setting_1=s1,
                    setting_2=s2,
                    metric_column=metric_column,
                    smaller_is_better=smaller,
                )
                cells.append(latex_setting_pair_cell(result))
        lines.append("        " + " & ".join(cells) + r" \\")

    lines.extend(
        [
            r"        \hline",
            r"    \end{tabular}",
            r"\end{table*}",
        ]
    )
    return "\n".join(lines)


def latex_pct_rate(rate: float, *, bold: bool = False) -> str:
    text = f"{rate * 100:.2f}\\%"
    return rf"\textbf{{{text}}}" if bold else text


def latex_pp_gain(gain: float, *, bold: bool = False) -> str:
    text = f"{gain * 100:+.2f} pp"
    return rf"\textbf{{{text}}}" if bold else text


def feedback_gain_stat_cell(
        gain_df: pd.DataFrame,
        *,
        setting: Optional[str] = None,
        model: Optional[str] = None,
) -> str:
    part = gain_df.copy()
    if setting is not None:
        part = part[part["prompt_setting"] == setting].copy()
    if model is not None:
        part = part[part["model"] == model].copy()
    x = part["feedback_success"].astype(float).to_numpy()
    y = part["no_feedback_success"].astype(float).to_numpy()
    if len(x) == 0 or len(y) == 0:
        return "--"
    p_value = mann_whitney_pvalue(x, y)
    a12 = vargha_delaney_a12(x, y)
    return (
        f"{symbol_to_latex(comparison_symbol(p_value, a12))} "
        f"({format_p_value(p_value)}, {format_a12(a12)})"
    )


def feedback_gain_values(gain_df: pd.DataFrame, *, model: Optional[str] = None) -> Tuple[float, float, float, str]:
    part = gain_df.copy()
    if model is not None:
        part = part[part["model"] == model].copy()
    if part.empty:
        return math.nan, math.nan, math.nan, "--"
    no_feedback = float(part["no_feedback_success"].astype(float).mean())
    with_feedback = float(part["feedback_success"].astype(float).mean())
    gain = with_feedback - no_feedback
    return no_feedback, with_feedback, gain, feedback_gain_stat_cell(gain_df, model=model)


def latex_feedback_model_gain_table(
        valid_gain: pd.DataFrame,
        invalid_gain: pd.DataFrame,
) -> str:
    lines = [
        r"\begin{table*}[!t]",
        r"    \centering",
        r"    \scriptsize",
        r"    \caption{\revo{Feedback gains across LLMs for RQ2.}}",
        r"    \label{TAB:result-RQ2-feedback-gain}",
        r"    \setlength\tabcolsep{1.0mm}",
        r"    \begin{tabular}{lcccccccc}",
        r"        \hline",
        r"        \multirow{2}{*}{\textbf{LLM}} & \multicolumn{4}{c}{\textbf{RQ2.1 Page Pass-Through}} & \multicolumn{4}{c}{\textbf{RQ2.2 Bug Reveal}} \\",
        r"        \cline{2-5}\cline{6-9}",
        r"        & \textbf{No-feedback} & \textbf{With-feedback} & \textbf{Gain} & \textbf{\textit{vs.} no-feedback} & \textbf{No-feedback} & \textbf{With-feedback} & \textbf{Gain} & \textbf{\textit{vs.} no-feedback} \\",
        r"        \hline",
    ]
    for model in LLM_TABLE_ORDER:
        v_no, v_fb, v_gain, v_stat = feedback_gain_values(valid_gain, model=model)
        i_no, i_fb, i_gain, i_stat = feedback_gain_values(invalid_gain, model=model)
        cells = [
            MODEL_MACRO[model],
            latex_pct_rate(v_no),
            latex_pct_rate(v_fb),
            latex_pp_gain(v_gain),
            v_stat,
            latex_pct_rate(i_no),
            latex_pct_rate(i_fb),
            latex_pp_gain(i_gain),
            i_stat,
        ]
        lines.append("        " + " & ".join(cells) + r" \\")

    v_no, v_fb, v_gain, v_stat = feedback_gain_values(valid_gain)
    i_no, i_fb, i_gain, i_stat = feedback_gain_values(invalid_gain)
    cells = [
        r"\textbf{Avg.}",
        latex_pct_rate(v_no, bold=True),
        latex_pct_rate(v_fb, bold=True),
        latex_pp_gain(v_gain, bold=True),
        v_stat,
        latex_pct_rate(i_no, bold=True),
        latex_pct_rate(i_fb, bold=True),
        latex_pp_gain(i_gain, bold=True),
        i_stat,
    ]
    lines.append("        " + " & ".join(cells) + r" \\")
    lines.extend(
        [
            r"        \hline",
            r"    \end{tabular}",
            r"\end{table*}",
        ]
    )
    return "\n".join(lines)


def latex_feedback_gain_table(
        feedback_df: pd.DataFrame,
        gain_df: pd.DataFrame,
        *,
        metric: str,
        delta_metric: str,
        label: str,
        caption: str,
) -> str:
    lines = [
        r"\begin{table*}[!t]",
        r"    \centering",
        r"    \scriptsize",
        rf"    \caption{{\revo{{{caption}}}}}",
        rf"    \label{{{label}}}",
        r"    \setlength\tabcolsep{2.0mm}",
        r"    \begin{tabular}{llcccc}",
        r"        \hline",
        r"        \textbf{Setting} & \textbf{LLM} & \textbf{No-feedback} & \textbf{With-feedback} & \textbf{Gain} & \textbf{\textit{vs.} no-feedback} \\",
        r"        \hline",
    ]
    for setting_index, setting in enumerate(PROMPT_SETTINGS):
        part = feedback_df[feedback_df["prompt_setting"] == setting].copy()
        for row_index, model in enumerate(LLM_TABLE_ORDER):
            item = part[part["model"] == model]
            if item.empty:
                continue
            row = item.iloc[0]
            prefix = SETTING_LABEL[setting] if row_index == 0 else ""
            cells = [
                prefix,
                MODEL_MACRO[model],
                latex_pct_rate(float(row[f"{metric}_no_feedback"])),
                latex_pct_rate(float(row[f"{metric}_with_feedback"])),
                latex_pp_gain(float(row[delta_metric])),
                feedback_gain_stat_cell(gain_df, setting=setting, model=model),
            ]
            lines.append("        " + " & ".join(cells) + r" \\")

        avg_no = float(part[f"{metric}_no_feedback"].mean())
        avg_feedback = float(part[f"{metric}_with_feedback"].mean())
        avg_gain = float(part[delta_metric].mean())
        cells = [
            "",
            r"\textbf{Avg.}",
            latex_pct_rate(avg_no, bold=True),
            latex_pct_rate(avg_feedback, bold=True),
            latex_pp_gain(avg_gain, bold=True),
            feedback_gain_stat_cell(gain_df, setting=setting),
        ]
        lines.append("        " + " & ".join(cells) + r" \\")
        if setting_index != len(PROMPT_SETTINGS) - 1:
            lines.append(r"        \hline")

    lines.extend(
        [
            r"        \hline",
            r"    \end{tabular}",
            r"\end{table*}",
        ]
    )
    return "\n".join(lines)


def render_latex_tables(
        summary_df: pd.DataFrame,
        case_levels: Dict[str, pd.DataFrame],
        feedback_valid: Optional[pd.DataFrame] = None,
        feedback_invalid: Optional[pd.DataFrame] = None,
        valid_gain: Optional[pd.DataFrame] = None,
        invalid_gain: Optional[pd.DataFrame] = None,
) -> str:
    table_specs = [
        (
            "rq1_valid",
            "TAB:result-RQ1.1",
            "Page-pass-through trends for RQ1.1 under the three prompting settings (First@$i$ denotes pages first passed at attempt $i$; @$i$ denotes the cumulative number and percentage of UI pages passed within the first $i$ attempts).",
            "TAB:result-RQ1.1-stat",
            "Symbol-based statistical comparisons and overhead for RQ1.1.",
        ),
        (
            "rq1_invalid",
            "TAB:result-RQ1.2",
            "Bug-reveal trends for RQ1.2 under the three prompting settings (First@$i$ denotes bugs first revealed at attempt $i$; @$i$ denotes the cumulative number and percentage of bugs revealed within the first $i$ attempts).",
            "TAB:result-RQ1.2-stat",
            "Symbol-based statistical comparisons and overhead for RQ1.2.",
        ),
        (
            "rq2_valid_feedback",
            "TAB:result-RQ2.1",
            "Feedback-enhanced page-pass-through trends for RQ2.1 under the three prompting settings.",
            "TAB:result-RQ2.1-stat",
            "Symbol-based statistical comparisons and overhead for RQ2.1.",
        ),
        (
            "rq2_invalid_feedback",
            "TAB:result-RQ2.2",
            "Feedback-enhanced bug-reveal trends for RQ2.2 under the three prompting settings.",
            "TAB:result-RQ2.2-stat",
            "Symbol-based statistical comparisons and overhead for RQ2.2.",
        ),
    ]
    parts: List[str] = []
    for exp, trend_label, trend_caption, stat_label, stat_caption in table_specs:
        parts.append(latex_trend_table(summary_df, experiment=exp, label=trend_label, caption=trend_caption))
        parts.append("")
        parts.append(latex_stat_table(case_levels[exp], spec=TASK_SPECS[exp], label=stat_label, caption=stat_caption))
        parts.append("")
        if exp == "rq2_invalid_feedback" and valid_gain is not None and invalid_gain is not None:
            parts.append(latex_feedback_model_gain_table(valid_gain, invalid_gain))
            parts.append("")
    return "\n".join(parts).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--token-table", default=DEFAULT_TOKEN_TABLE)
    parser.add_argument("--official-token-table", default=DEFAULT_OFFICIAL_TOKEN_TABLE)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--md-path", default=str(DEFAULT_MD_PATH))
    parser.add_argument("--no-md-update", action="store_true")
    parser.add_argument("--no-latex", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = Path(args.md_path)

    log("[analyze] fetching generation rows")
    df = fetch_generation_rows(args.table)
    df, token_coverage = attach_session_token_metrics(
        df,
        args.token_table,
        args.official_token_table,
    )
    token_coverage.to_csv(output_dir / "rq_token_session_coverage.csv", index=False)
    completeness_df = validate_eval_completeness(df)
    completeness_df.to_csv(output_dir / "rq_eval_completeness.csv", index=False)

    case_levels: Dict[str, pd.DataFrame] = {}
    summaries = []
    category_summaries = []
    trend_summaries = []
    for exp, spec in TASK_SPECS.items():
        log(f"[analyze] building case-level data for {exp}")
        case_df = build_case_level_for_spec(df, spec)
        case_levels[exp] = case_df
        case_df.to_csv(output_dir / f"{exp}_case_level.csv", index=False)
        summaries.append(summarize_case_level(case_df, spec))
        trend_summaries.append(build_trend_summary_for_spec(case_df, spec))
        category = build_category_summary(case_df, spec)
        if not category.empty:
            category_summaries.append(category)

    summary_df = pd.concat(summaries, ignore_index=True)
    summary_df.to_csv(output_dir / "rq_metric_summary.csv", index=False)
    trend_df = pd.concat(trend_summaries, ignore_index=True)
    valid_trend = trend_df[trend_df["task"] == "valid_pptr"].copy()
    invalid_trend = trend_df[trend_df["task"] == "invalid_bug"].copy()
    valid_trend.to_csv(output_dir / "rq_valid_trend.csv", index=False)
    invalid_trend.to_csv(output_dir / "rq_invalid_trend.csv", index=False)
    time_summary = build_time_summary_from_trends(trend_df)
    time_summary.to_csv(output_dir / "rq_time_summary.csv", index=False)
    summary_df[[
        "rq", "experiment", "task", "model", "prompt_setting",
        "prompt_tokens_per_call", "completion_tokens_per_call", "total_tokens_per_call",
        "prompt_tokens_per_case", "completion_tokens_per_case", "total_tokens_per_case",
        "prompt_token_calls", "completion_token_calls", "total_token_calls",
    ]].to_csv(output_dir / "rq_token_summary.csv", index=False)
    if category_summaries:
        pd.concat(category_summaries, ignore_index=True).to_csv(
            output_dir / "rq_invalid_category_summary.csv",
            index=False,
        )

    log("[analyze] building feedback gain data")
    valid_gain = build_feedback_gain_case_level(
        case_levels["rq1_valid"],
        case_levels["rq2_valid_feedback"],
        TASK_SPECS["rq2_valid_feedback"],
        3,
    )
    invalid_gain = build_feedback_gain_case_level(
        case_levels["rq1_invalid"],
        case_levels["rq2_invalid_feedback"],
        TASK_SPECS["rq2_invalid_feedback"],
        30,
    )
    valid_gain.to_csv(output_dir / "rq2_valid_feedback_gain.csv", index=False)
    invalid_gain.to_csv(output_dir / "rq2_invalid_feedback_gain.csv", index=False)

    feedback_valid = build_feedback_summary(
        summary_df,
        "rq1_valid",
        "rq2_valid_feedback",
        "valid_pptr",
        "PPTR@3",
        "DeltaPPTR@3",
    )
    feedback_invalid = build_feedback_summary(
        summary_df,
        "rq1_invalid",
        "rq2_invalid_feedback",
        "invalid_bug",
        "BugReveal@30",
        "DeltaBugReveal@30",
    )
    feedback_valid.to_csv(output_dir / "rq2_valid_feedback_summary.csv", index=False)
    feedback_invalid.to_csv(output_dir / "rq2_invalid_feedback_summary.csv", index=False)

    log("[analyze] computing pairwise LLM statistics")
    pairwise_df = build_pairwise_stats(case_levels)
    gain_pairwise_df = build_feedback_pairwise_stats(valid_gain, invalid_gain)
    all_pairwise = pd.concat([pairwise_df, gain_pairwise_df], ignore_index=True)
    all_pairwise.to_csv(output_dir / "pairwise_llm_stats.csv", index=False)

    if not args.no_latex:
        latex_tables = render_latex_tables(
            summary_df,
            case_levels,
            feedback_valid=feedback_valid,
            feedback_invalid=feedback_invalid,
            valid_gain=valid_gain,
            invalid_gain=invalid_gain,
        )
        (output_dir / "rq_latex_tables.tex").write_text(latex_tables, encoding="utf-8")

    if not args.no_md_update:
        log(f"[analyze] updating markdown {md_path}")
        analysis = render_analysis_section(
            output_dir=output_dir,
            summary_df=summary_df,
            feedback_valid=feedback_valid,
            feedback_invalid=feedback_invalid,
            valid_trend=valid_trend,
            invalid_trend=invalid_trend,
            time_summary=time_summary,
            pairwise_df=all_pairwise,
            completeness_df=completeness_df,
        )
        update_markdown(md_path, analysis)

    log(f"[analyze] done output_dir={output_dir}")


if __name__ == "__main__":
    main()
