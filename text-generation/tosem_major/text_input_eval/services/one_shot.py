from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pymysql
import requests
import urllib3
from pymysql.cursors import DictCursor

# ============================================================
# Paths
# ============================================================

PROJECT_ROOT = Path(
    "/Users/cuichenhui/Documents/local-repositories/"
    "llm-empirical-study-workspace/llm-empirical-study"
)

TOSEM_MAJOR_ROOT = PROJECT_ROOT / "tosem_major"
CACHE_ROOT = TOSEM_MAJOR_ROOT / "apk_info" / "cache"

PPTR_PROMPTS_JSON = CACHE_ROOT / "pptr_prompts.json"
BUG_PROMPTS_JSON = CACHE_ROOT / "bug_prompts.json"

PPTR_CACHE_DIR = CACHE_ROOT / "pptr"
BUG_CACHE_DIR = CACHE_ROOT / "bug"

# ============================================================
# API / DB
# ============================================================

API_URL = "http://must.lab.help1.com.cn/api/chat/completions"
DEFAULT_MODEL = "gpt-5.4-2026-03-05"

CURATED_DB_TABLE = "t_llm_text_generation_curated"
DB_TABLE = os.getenv("TEXT_GENERATION_DB_TABLE", "t_llm_text_generation")


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


def get_api_key() -> str:
    value = os.getenv("MUST_LAB_API_TOKEN", "").strip()
    if not value:
        raise RuntimeError("Environment variable MUST_LAB_API_TOKEN is empty.")
    return value


# ============================================================
# Constants
# ============================================================

VALID_TASK = "valid_pptr"
INVALID_TASK = "invalid_bug"

PROMPT_SETTING_EXTRACTED = "extracted"
PROMPT_SETTING_XML = "xml"
PROMPT_SETTING_VISION = "vision"

PROMPT_VARIANT_FULL = "full"
PROMPT_VARIANT_NO_GLOBAL = "no_global"
PROMPT_VARIANT_NO_COMPONENT = "no_component"
PROMPT_VARIANT_NO_ADJACENT = "no_adjacent"
PROMPT_VARIANT_NO_GUIDING = "no_guiding"
PROMPT_VARIANT_XML = "xml"
PROMPT_VARIANT_VISION = "vision"

FEEDBACK_MODE_NONE = "none"

EXPERIMENT_RQ1_VALID = "rq1_valid"
EXPERIMENT_RQ1_INVALID = "rq1_invalid"
EXPERIMENT_ABLATION_PROMPT = "ablation_prompt"
EXPERIMENT_ABLATION_MULTI_COMPONENT = "ablation_multi_component"

VALID_ATTEMPT_IDS = (1, 2, 3)
INVALID_INDICES = tuple(range(1, 31))

# ============================================================
# Utils
# ============================================================

JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
BARE_JSON_RE = re.compile(r"(\{.*\})", re.DOTALL)
GENERATED_VALUE_RE = re.compile(r"\bgenerated_value\b", re.IGNORECASE)


def read_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def safe_int_str(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def normalize_case_id_for_invalid(page_id: Any) -> str:
    return f"B{int(float(page_id)):03d}"


def load_prompt_records(task: str) -> Dict[str, Dict[str, Any]]:
    if task == VALID_TASK:
        records = read_json_file(PPTR_PROMPTS_JSON)
        out: Dict[str, Dict[str, Any]] = {}
        for item in records:
            app_id = str(item["app_id"])
            out[app_id] = item
        return out

    if task == INVALID_TASK:
        records = read_json_file(BUG_PROMPTS_JSON)
        out: Dict[str, Dict[str, Any]] = {}
        for item in records:
            page_id = safe_int_str(item["page_id"])
            out[page_id] = item
        return out

    raise ValueError(f"Unsupported task: {task}")


def load_xml_text(task: str, target_id: str) -> str:
    base_dir = PPTR_CACHE_DIR if task == VALID_TASK else BUG_CACHE_DIR
    file_path = base_dir / f"hierarchy_{target_id}.xml"
    if not file_path.exists():
        raise FileNotFoundError(f"XML not found: {file_path}")
    return read_text_file(file_path)


def load_base64_text(task: str, target_id: str) -> str:
    base_dir = PPTR_CACHE_DIR if task == VALID_TASK else BUG_CACHE_DIR
    file_path = base_dir / f"base64_{target_id}.txt"
    if not file_path.exists():
        raise FileNotFoundError(f"Base64 txt not found: {file_path}")
    return read_text_file(file_path).strip()


def parse_component_resource_ids_from_restrictive(restrictive: str) -> List[str]:
    m = JSON_BLOCK_RE.search(restrictive)
    if not m:
        m = BARE_JSON_RE.search(restrictive)
    if not m:
        raise ValueError("Cannot parse JSON schema from restrictive prompt")
    obj = json.loads(m.group(1))
    if not isinstance(obj, dict) or not obj:
        raise ValueError("Restrictive JSON schema is empty or invalid")
    return list(obj.keys())


def validate_resource_ids_exact_match(
        parsed_obj: Dict[str, Any],
        expected_resource_ids: Sequence[str],
) -> None:
    if not isinstance(parsed_obj, dict):
        raise ValueError("Returned JSON is not an object")

    actual_keys = set(parsed_obj.keys())
    expected_keys = set(expected_resource_ids)

    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise ValueError(
            f"Returned resource ids do not exactly match expected ones. "
            f"missing={missing}, extra={extra}"
        )


def extract_json_object_from_text(text: str) -> Dict[str, Any]:
    candidates: List[str] = []

    for m in JSON_BLOCK_RE.finditer(text):
        candidates.append(m.group(1))

    if not candidates:
        for m in BARE_JSON_RE.finditer(text):
            candidates.append(m.group(1))

    last_error: Optional[Exception] = None
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except Exception as e:
            last_error = e

    if last_error:
        raise ValueError(f"Failed to parse JSON object: {last_error}")
    raise ValueError("No JSON object found in LLM output")


def placeholder_tag_of_obj(obj: Optional[Dict[str, Any]]) -> int:
    if not obj:
        return 0
    for value in obj.values():
        if isinstance(value, str) and GENERATED_VALUE_RE.search(value):
            return 1
    return 0


def sort_dict_by_keys(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: d[k] for k in sorted(d.keys())}


def combinations_for_tau(resource_ids: Sequence[str], tau: int) -> Iterable[Tuple[str, ...]]:
    if tau < 0 or tau > len(resource_ids):
        return []
    return itertools.combinations(resource_ids, tau)


def clean_surrogates(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return value.encode("utf-8", "replace").decode("utf-8")
    if isinstance(value, list):
        return [clean_surrogates(v) for v in value]
    if isinstance(value, dict):
        return {str(clean_surrogates(k)): clean_surrogates(v) for k, v in value.items()}
    return value


# ============================================================
# Prompt repository
# ============================================================

@dataclass
class PromptContext:
    task: str
    target_id: str
    case_id: str
    global_text: str
    component_text: str
    adjacent_text: str
    restrictive_text: str
    guiding_text: str
    xml_text: str
    image_64: str
    resource_ids: List[str]


class PromptRepository:
    def __init__(self) -> None:
        self.valid_records = load_prompt_records(VALID_TASK)
        self.invalid_records = load_prompt_records(INVALID_TASK)
        self.valid_case_id_map = self._build_valid_case_id_map()

    def _build_valid_case_id_map(self) -> Dict[str, str]:
        records = read_json_file(PPTR_PROMPTS_JSON)
        out: Dict[str, str] = {}
        for idx, item in enumerate(records, start=1):
            app_id = str(item["app_id"])
            out[app_id] = f"P{idx:03d}"
        return out

    def get_context(self, task: str, target_id: str) -> PromptContext:
        if task == VALID_TASK:
            row = self.valid_records[target_id]
            case_id = self.valid_case_id_map[target_id]
        elif task == INVALID_TASK:
            row = self.invalid_records[target_id]
            case_id = normalize_case_id_for_invalid(target_id)
        else:
            raise ValueError(f"Unsupported task: {task}")

        restrictive_text = str(row["restrictive"])
        resource_ids = parse_component_resource_ids_from_restrictive(restrictive_text)

        return PromptContext(
            task=task,
            target_id=target_id,
            case_id=case_id,
            global_text=str(row["global"]),
            component_text=str(row["component"]),
            adjacent_text=str(row["adjacent"]),
            restrictive_text=restrictive_text,
            guiding_text=str(row["guiding"]),
            xml_text=load_xml_text(task, target_id),
            image_64=load_base64_text(task, target_id),
            resource_ids=resource_ids,
        )


# ============================================================
# Prompt generation
# ============================================================

class PromptGenerator:
    @staticmethod
    def build_prompt(
            ctx: PromptContext,
            prompt_setting: str,
            prompt_variant: str,
    ) -> Tuple[str, Optional[str]]:
        if prompt_setting == PROMPT_SETTING_EXTRACTED:
            return PromptGenerator._build_extracted_prompt(ctx, prompt_variant), None

        if prompt_setting == PROMPT_SETTING_XML:
            return PromptGenerator._build_xml_prompt(ctx), None

        if prompt_setting == PROMPT_SETTING_VISION:
            message = PromptGenerator._build_vision_prompt(ctx)
            image_url = f"data:image/png;base64,{ctx.image_64}"
            return message, image_url

        raise ValueError(f"Unsupported prompt_setting: {prompt_setting}")

    @staticmethod
    def _build_extracted_prompt(ctx: PromptContext, prompt_variant: str) -> str:
        if prompt_variant == PROMPT_VARIANT_FULL:
            parts = [
                ctx.global_text,
                ctx.component_text,
                ctx.adjacent_text,
                ctx.restrictive_text,
                ctx.guiding_text,
            ]
        elif prompt_variant == PROMPT_VARIANT_NO_GLOBAL:
            parts = [
                ctx.component_text,
                ctx.adjacent_text,
                ctx.restrictive_text,
                ctx.guiding_text,
            ]
        elif prompt_variant == PROMPT_VARIANT_NO_COMPONENT:
            parts = [
                ctx.global_text,
                ctx.adjacent_text,
                ctx.restrictive_text,
                ctx.guiding_text,
            ]
        elif prompt_variant == PROMPT_VARIANT_NO_ADJACENT:
            parts = [
                ctx.global_text,
                ctx.component_text,
                ctx.restrictive_text,
                ctx.guiding_text,
            ]
        elif prompt_variant == PROMPT_VARIANT_NO_GUIDING:
            parts = [
                ctx.global_text,
                ctx.component_text,
                ctx.adjacent_text,
                ctx.restrictive_text,
            ]
        else:
            raise ValueError(f"Unsupported extracted prompt_variant: {prompt_variant}")

        return "\n".join([p for p in parts if p is not None and str(p).strip() != ""]).strip()

    @staticmethod
    def _build_xml_prompt(ctx: PromptContext) -> str:
        parts = [
            ctx.global_text,
            ctx.xml_text,
            ctx.restrictive_text,
            ctx.guiding_text,
        ]
        return "\n".join([p for p in parts if p is not None and str(p).strip() != ""]).strip()

    @staticmethod
    def _build_vision_prompt(ctx: PromptContext) -> str:
        parts = [
            ctx.global_text,
            ctx.restrictive_text,
            ctx.guiding_text,
        ]
        return "\n".join([p for p in parts if p is not None and str(p).strip() != ""]).strip()


# ============================================================
# API client
# ============================================================

class LLMClient:
    def __init__(
            self,
            api_key: str,
            model_type: str = DEFAULT_MODEL,
            base_url: str = API_URL,
            timeout: int = 500,
            verify_ssl: bool = False,
    ) -> None:
        self.api_key = api_key
        self.model_type = model_type
        self.base_url = base_url
        self.timeout = timeout
        self.verify_ssl = verify_ssl

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        )

    def chat_completion(
            self,
            message: str,
            image_url: Optional[str] = None,
            cursor_id: str = "",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "modelType": self.model_type,
            # The gateway binds a conversation through the previous response
            # data.id carried in the next request's id field.
            "id": cursor_id,
            "message": message,
        }
        if image_url:
            payload["imageUrl"] = image_url

        response = self.session.post(
            self.base_url,
            json=payload,
            timeout=self.timeout,
            verify=self.verify_ssl,
        )
        response.raise_for_status()
        return response.json()


# ============================================================
# DB writer
# ============================================================

class ResultWriter:
    def __init__(self, conn) -> None:
        self.conn = conn

    @staticmethod
    def is_curated_table() -> bool:
        return DB_TABLE == CURATED_DB_TABLE

    @staticmethod
    def status_for_result(
            *,
            parse_success: int,
            parsed_input_json: Optional[Dict[str, Any]],
            raw_response: str,
            parse_error: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        status_text = f"{raw_response or ''}\n{parse_error or ''}".lower()
        if (
                "prompt contains" in status_text
                and (
                    "too large for model" in status_text
                    or "maximum context length" in status_text
                    or "context length" in status_text
                )
        ):
            return "selected_terminal_failure", "terminal_failure", "api_context_limit"
        if parse_success == 1 and parsed_input_json:
            return "selected_usable", "ready_for_eval", "parse_success"
        if (
                "reject_no_credit" in status_text
                or "http 402" in status_text
                or "insufficient balance" in status_text
                or "account is in arrears" in status_text
                or "exceededcurrentquota" in status_text
        ):
            return "selected_incomplete_generation", "incomplete_generation", "api_no_credit"
        if '"code": "900001"' in status_text or '"code":"900001"' in status_text or "system error" in status_text:
            return "selected_incomplete_generation", "incomplete_generation", "api_system_error"
        return "selected_incomplete_generation", "incomplete_generation", "parse_failure"

    def ensure_conn_alive(self) -> None:
        self.conn.ping(reconnect=True)

    def delete_exact_generation_record(
            self,
            *,
            experiment: str,
            case_id: str,
            task: str,
            model: str,
            prompt_setting: str,
            prompt_variant: str,
            feedback_mode: str,
            attempt_id: int,
            invalid_index: int,
            tau: int,
            combo_index: int,
    ) -> int:
        sql = f"""
            DELETE FROM {DB_TABLE}
            WHERE experiment = %(experiment)s
              AND case_id = %(case_id)s
              AND task = %(task)s
              AND model = %(model)s
              AND prompt_setting = %(prompt_setting)s
              AND prompt_variant = %(prompt_variant)s
              AND feedback_mode = %(feedback_mode)s
              AND attempt_id = %(attempt_id)s
              AND invalid_index = %(invalid_index)s
              AND tau = %(tau)s
              AND combo_index = %(combo_index)s
        """
        params = {
            "experiment": experiment,
            "case_id": case_id,
            "task": task,
            "model": model,
            "prompt_setting": prompt_setting,
            "prompt_variant": prompt_variant,
            "feedback_mode": feedback_mode,
            "attempt_id": attempt_id,
            "invalid_index": invalid_index,
            "tau": tau,
            "combo_index": combo_index,
        }
        self.ensure_conn_alive()
        with self.conn.cursor() as cursor:
            affected = cursor.execute(sql, params)
        self.conn.commit()
        return int(affected)

    def delete_multi_component_records_for_attempt(
            self,
            *,
            case_id: str,
            task: str,
            model: str,
            attempt_id: int,
    ) -> int:
        sql = f"""
            DELETE FROM {DB_TABLE}
            WHERE experiment = %(experiment)s
              AND case_id = %(case_id)s
              AND task = %(task)s
              AND model = %(model)s
              AND prompt_setting = %(prompt_setting)s
              AND prompt_variant = %(prompt_variant)s
              AND feedback_mode = %(feedback_mode)s
              AND attempt_id = %(attempt_id)s
              AND invalid_index = -1
        """
        params = {
            "experiment": EXPERIMENT_ABLATION_MULTI_COMPONENT,
            "case_id": case_id,
            "task": task,
            "model": model,
            "prompt_setting": PROMPT_SETTING_EXTRACTED,
            "prompt_variant": PROMPT_VARIANT_FULL,
            "feedback_mode": FEEDBACK_MODE_NONE,
            "attempt_id": attempt_id,
        }
        self.ensure_conn_alive()
        with self.conn.cursor() as cursor:
            affected = cursor.execute(sql, params)
        self.conn.commit()
        return int(affected)

    def has_parsed_or_session_result(
            self,
            *,
            experiment: str,
            case_id: str,
            task: str,
            model: str,
            prompt_setting: str,
            prompt_variant: str,
            feedback_mode: str,
            attempt_id: int,
            invalid_index: int,
            tau: int,
            combo_index: int,
    ) -> bool:
        sql = f"""
            SELECT 1
            FROM {DB_TABLE}
            WHERE experiment = %(experiment)s
              AND case_id = %(case_id)s
              AND task = %(task)s
              AND model = %(model)s
              AND prompt_setting = %(prompt_setting)s
              AND prompt_variant = %(prompt_variant)s
              AND feedback_mode = %(feedback_mode)s
              AND attempt_id = %(attempt_id)s
              AND invalid_index = %(invalid_index)s
              AND tau = %(tau)s
              AND combo_index = %(combo_index)s
              AND (
                    parsed_input_json IS NOT NULL
                    OR (
                        session_ids_json IS NOT NULL
                        AND JSON_LENGTH(session_ids_json) > 0
                    )
                  )
            LIMIT 1
        """
        params = {
            "experiment": experiment,
            "case_id": case_id,
            "task": task,
            "model": model,
            "prompt_setting": prompt_setting,
            "prompt_variant": prompt_variant,
            "feedback_mode": feedback_mode,
            "attempt_id": attempt_id,
            "invalid_index": invalid_index,
            "tau": tau,
            "combo_index": combo_index,
        }
        self.ensure_conn_alive()
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        return row is not None

    def upsert_generation_result(
            self,
            *,
            experiment: str,
            case_id: str,
            task: str,
            model: str,
            prompt_setting: str,
            prompt_variant: str,
            feedback_mode: str,
            attempt_id: int,
            invalid_index: int,
            tau: int,
            combo_index: int,
            session_ids_json: Optional[List[str]],
            raw_response: str,
            parsed_input_json: Optional[Dict[str, Any]],
            parse_success: int,
            parse_error: Optional[str],
            placeholder_tag: int,
            eval_success: Optional[int],
            elapsed_ms: int,
            mock: int = 0,
    ) -> None:
        curation_status, completion_status, generation_status = self.status_for_result(
            parse_success=parse_success,
            parsed_input_json=parsed_input_json,
            raw_response=raw_response,
            parse_error=parse_error,
        )
        params = {
            "id": None,
            "source_id": None,
            "derived_from_source_id": None,
            "experiment": experiment,
            "case_id": clean_surrogates(case_id),
            "task": clean_surrogates(task),
            "model": clean_surrogates(model),
            "canonical_model": clean_surrogates(model),
            "exact_model": clean_surrogates(model),
            "prompt_setting": clean_surrogates(prompt_setting),
            "prompt_variant": clean_surrogates(prompt_variant),
            "feedback_mode": clean_surrogates(feedback_mode),
            "attempt_id": attempt_id,
            "invalid_index": invalid_index,
            "tau": tau,
            "combo_index": combo_index,
            "session_ids_json": json.dumps(clean_surrogates(session_ids_json), ensure_ascii=False)
            if session_ids_json is not None
            else None,
            "raw_response": clean_surrogates(raw_response),
            "parsed_input_json": json.dumps(clean_surrogates(parsed_input_json), ensure_ascii=False)
            if parsed_input_json is not None
            else None,
            "parse_success": parse_success,
            "parse_error": clean_surrogates(parse_error),
            "placeholder_tag": placeholder_tag,
            "eval_success": eval_success,
            "elapsed_ms": elapsed_ms,
            "mock": mock,
            "curation_status": curation_status,
            "completion_status": completion_status,
            "generation_status": generation_status,
            "selection_reason": "rerun_direct_to_curated",
        }
        if self.is_curated_table():
            sql = f"""
                INSERT INTO {DB_TABLE} (
                    id,
                    source_id,
                    derived_from_source_id,
                    experiment,
                    case_id,
                    task,
                    model,
                    canonical_model,
                    exact_model,
                    prompt_setting,
                    prompt_variant,
                    feedback_mode,
                    attempt_id,
                    invalid_index,
                    session_ids_json,
                    raw_response,
                    parsed_input_json,
                    parse_success,
                    parse_error,
                    placeholder_tag,
                    eval_success,
                    tau,
                    combo_index,
                    elapsed_ms,
                    curation_status,
                    completion_status,
                    generation_status,
                    selection_reason
                ) VALUES (
                    %(id)s,
                    %(source_id)s,
                    %(derived_from_source_id)s,
                    %(experiment)s,
                    %(case_id)s,
                    %(task)s,
                    %(model)s,
                    %(canonical_model)s,
                    %(exact_model)s,
                    %(prompt_setting)s,
                    %(prompt_variant)s,
                    %(feedback_mode)s,
                    %(attempt_id)s,
                    %(invalid_index)s,
                    %(session_ids_json)s,
                    %(raw_response)s,
                    %(parsed_input_json)s,
                    %(parse_success)s,
                    %(parse_error)s,
                    %(placeholder_tag)s,
                    %(eval_success)s,
                    %(tau)s,
                    %(combo_index)s,
                    %(elapsed_ms)s,
                    %(curation_status)s,
                    %(completion_status)s,
                    %(generation_status)s,
                    %(selection_reason)s
                )
            """
        else:
            sql = f"""
                INSERT INTO {DB_TABLE} (
                    experiment,
                    case_id,
                    task,
                    model,
                    prompt_setting,
                    prompt_variant,
                    feedback_mode,
                    attempt_id,
                    invalid_index,
                    tau,
                    combo_index,
                    session_ids_json,
                    raw_response,
                    parsed_input_json,
                    parse_success,
                    parse_error,
                    placeholder_tag,
                    eval_success,
                    elapsed_ms,
                    mock
                ) VALUES (
                    %(experiment)s,
                    %(case_id)s,
                    %(task)s,
                    %(model)s,
                    %(prompt_setting)s,
                    %(prompt_variant)s,
                    %(feedback_mode)s,
                    %(attempt_id)s,
                    %(invalid_index)s,
                    %(tau)s,
                    %(combo_index)s,
                    %(session_ids_json)s,
                    %(raw_response)s,
                    %(parsed_input_json)s,
                    %(parse_success)s,
                    %(parse_error)s,
                    %(placeholder_tag)s,
                    %(eval_success)s,
                    %(elapsed_ms)s,
                    %(mock)s
                )
                ON DUPLICATE KEY UPDATE
                    session_ids_json = VALUES(session_ids_json),
                    raw_response = VALUES(raw_response),
                    parsed_input_json = VALUES(parsed_input_json),
                    parse_success = VALUES(parse_success),
                    parse_error = VALUES(parse_error),
                    placeholder_tag = VALUES(placeholder_tag),
                    eval_success = VALUES(eval_success),
                    elapsed_ms = VALUES(elapsed_ms),
                    mock = VALUES(mock)
            """
        self.ensure_conn_alive()
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)

    def commit(self) -> None:
        self.ensure_conn_alive()
        self.conn.commit()


# ============================================================
# Generation core
# ============================================================

@dataclass
class GenerationOutput:
    raw_response_text: str
    parsed_input_json: Optional[Dict[str, Any]]
    parse_success: int
    parse_error: Optional[str]
    placeholder_tag: int
    session_ids: List[str]


class GenerationEngine:
    def __init__(
            self,
            prompt_repo: PromptRepository,
            llm_client: LLMClient,
            result_writer: ResultWriter,
    ) -> None:
        self.prompt_repo = prompt_repo
        self.llm_client = llm_client
        self.result_writer = result_writer

    @staticmethod
    def _compact_log_text(text: Optional[str], limit: int = 200) -> str:
        compact = " ".join(str(text or "").split())
        if len(compact) <= limit:
            return compact
        return compact[:limit] + "..."

    @staticmethod
    def _format_slot_for_log(
            *,
            case_id: str,
            prompt_setting: str,
            prompt_variant: str,
            attempt_id: int,
            invalid_index: int,
            tau: int,
            combo_index: int,
    ) -> str:
        parts = [
            f"case_id={case_id}",
            f"setting={prompt_setting}/{prompt_variant}",
        ]
        if attempt_id != -1:
            parts.append(f"attempt={attempt_id}")
        if invalid_index != -1:
            parts.append(f"invalid={invalid_index}")
        if tau != -1:
            parts.append(f"tau={tau}")
        if combo_index != -1:
            parts.append(f"combo={combo_index}")
        return " ".join(parts)

    @classmethod
    def _failure_reason_for_log(cls, result: GenerationOutput) -> str:
        raw = str(result.raw_response_text or "").strip()
        if raw:
            try:
                payload = json.loads(raw)
            except Exception:
                payload = None
            if isinstance(payload, dict) and payload.get("success") is False:
                msg = payload.get("msg")
                if msg:
                    return cls._compact_log_text(str(msg))
                error = payload.get("error")
                if isinstance(error, dict):
                    return cls._compact_log_text(
                        str(error.get("message") or error.get("msg") or error)
                    )

        return cls._compact_log_text(result.parse_error or raw)

    def generate_one_shot(
            self,
            *,
            ctx: PromptContext,
            prompt_setting: str,
            prompt_variant: str,
    ) -> GenerationOutput:
        message, image_url = PromptGenerator.build_prompt(ctx, prompt_setting, prompt_variant)

        try:
            response = self.llm_client.chat_completion(
                message=message,
                image_url=image_url,
                cursor_id="",
            )
        except Exception as e:
            return GenerationOutput(
                raw_response_text="",
                parsed_input_json=None,
                parse_success=0,
                parse_error=f"{type(e).__name__}: {e}",
                placeholder_tag=0,
                session_ids=[],
            )

        data = response.get("data") or {}

        resp_id = str(data.get("id") or "").strip()
        chat_text = str(data.get("chat") or "")
        if not chat_text:
            chat_text = json.dumps(response, ensure_ascii=False)

        session_ids: List[str] = [resp_id] if resp_id else []

        try:
            parsed_obj = extract_json_object_from_text(chat_text)
            validate_resource_ids_exact_match(parsed_obj, ctx.resource_ids)

            return GenerationOutput(
                raw_response_text=chat_text,
                parsed_input_json=sort_dict_by_keys(parsed_obj),
                parse_success=1,
                parse_error=None,
                placeholder_tag=placeholder_tag_of_obj(parsed_obj),
                session_ids=session_ids,
            )
        except Exception as e:
            return GenerationOutput(
                raw_response_text=chat_text,
                parsed_input_json=None,
                parse_success=0,
                parse_error=str(e),
                placeholder_tag=0,
                session_ids=session_ids,
            )

    def run_single_generation_and_store(
            self,
            *,
            experiment: str,
            ctx: PromptContext,
            prompt_setting: str,
            prompt_variant: str,
            feedback_mode: str = FEEDBACK_MODE_NONE,
            attempt_id: int = -1,
            invalid_index: int = -1,
            tau: int = -1,
            combo_index: int = -1,
            auto_expand_multi_component: bool = True,
    ) -> None:
        if self.result_writer.has_parsed_or_session_result(
                experiment=experiment,
                case_id=ctx.case_id,
                task=ctx.task,
                model=self.llm_client.model_type,
                prompt_setting=prompt_setting,
                prompt_variant=prompt_variant,
                feedback_mode=feedback_mode,
                attempt_id=attempt_id,
                invalid_index=invalid_index,
                tau=tau,
                combo_index=combo_index,
        ):
            reason = getattr(
                self.result_writer,
                "last_skip_reason",
                "already_has_parsed_input_or_session",
            ) or "already_has_parsed_input_or_session"
            slot_log = self._format_slot_for_log(
                case_id=ctx.case_id,
                prompt_setting=prompt_setting,
                prompt_variant=prompt_variant,
                attempt_id=attempt_id,
                invalid_index=invalid_index,
                tau=tau,
                combo_index=combo_index,
            )
            print(f"    [SKIP] {slot_log} reason={reason}")
            if hasattr(self.result_writer, "last_skip_reason"):
                self.result_writer.last_skip_reason = None
            return

        t0 = time.perf_counter()

        result = self.generate_one_shot(
            ctx=ctx,
            prompt_setting=prompt_setting,
            prompt_variant=prompt_variant,
        )

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        eval_success = None

        self.result_writer.upsert_generation_result(
            experiment=experiment,
            case_id=ctx.case_id,
            task=ctx.task,
            model=self.llm_client.model_type,
            prompt_setting=prompt_setting,
            prompt_variant=prompt_variant,
            feedback_mode=feedback_mode,
            attempt_id=attempt_id,
            invalid_index=invalid_index,
            tau=tau,
            combo_index=combo_index,
            session_ids_json=result.session_ids,
            raw_response=result.raw_response_text,
            parsed_input_json=result.parsed_input_json,
            parse_success=result.parse_success,
            parse_error=result.parse_error,
            placeholder_tag=result.placeholder_tag,
            eval_success=eval_success,
            elapsed_ms=elapsed_ms,
            mock=0,
        )
        self.result_writer.commit()

        status = f"parse={result.parse_success}"
        if result.parse_success != 1:
            reason = self._failure_reason_for_log(result)
            if reason:
                status += f" reason={reason!r}"
        slot_log = self._format_slot_for_log(
            case_id=ctx.case_id,
            prompt_setting=prompt_setting,
            prompt_variant=prompt_variant,
            attempt_id=attempt_id,
            invalid_index=invalid_index,
            tau=tau,
            combo_index=combo_index,
        )
        print(
            f"    [SAVED] {slot_log} {status} elapsed_ms={elapsed_ms}"
        )

        if (
                auto_expand_multi_component
                and ctx.task == VALID_TASK
                and experiment == EXPERIMENT_RQ1_VALID
                and prompt_setting == PROMPT_SETTING_EXTRACTED
                and prompt_variant == PROMPT_VARIANT_FULL
                and result.parse_success == 1
                and result.parsed_input_json is not None
                and len(ctx.resource_ids) >= 2
        ):
            self.expand_multi_component_from_base_result(
                ctx=ctx,
                base_parsed_json=result.parsed_input_json,
                inherited_attempt_id=attempt_id,
            )

    def expand_multi_component_from_base_result(
            self,
            *,
            ctx: PromptContext,
            base_parsed_json: Dict[str, Any],
            inherited_attempt_id: int,
    ) -> None:
        resource_ids = list(ctx.resource_ids)
        d = len(resource_ids)

        for tau in range(0, d + 1):
            for combo_index, active_subset in enumerate(combinations_for_tau(resource_ids, tau), start=1):
                if self.result_writer.has_parsed_or_session_result(
                        experiment=EXPERIMENT_ABLATION_MULTI_COMPONENT,
                        case_id=ctx.case_id,
                        task=ctx.task,
                        model=self.llm_client.model_type,
                        prompt_setting=PROMPT_SETTING_EXTRACTED,
                        prompt_variant=PROMPT_VARIANT_FULL,
                        feedback_mode=FEEDBACK_MODE_NONE,
                        attempt_id=inherited_attempt_id,
                        invalid_index=-1,
                        tau=tau,
                        combo_index=combo_index,
                ):
                    continue

                t0 = time.perf_counter()

                active_set = set(active_subset)
                scenario: Dict[str, Any] = {}
                for rid in resource_ids:
                    if rid in active_set:
                        scenario[rid] = base_parsed_json.get(rid, "")
                    else:
                        scenario[rid] = ""

                raw_response_text = json.dumps(
                    {
                        "source": "auto_expand_multi_component",
                        "tau": tau,
                        "combo_index": combo_index,
                        "active_ids": list(active_subset),
                        "scenario": scenario,
                    },
                    ensure_ascii=False,
                )

                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                eval_success = None
                placeholder_tag = placeholder_tag_of_obj(scenario)

                self.result_writer.upsert_generation_result(
                    experiment=EXPERIMENT_ABLATION_MULTI_COMPONENT,
                    case_id=ctx.case_id,
                    task=ctx.task,
                    model=self.llm_client.model_type,
                    prompt_setting=PROMPT_SETTING_EXTRACTED,
                    prompt_variant=PROMPT_VARIANT_FULL,
                    feedback_mode=FEEDBACK_MODE_NONE,
                    attempt_id=inherited_attempt_id,
                    invalid_index=-1,
                    tau=tau,
                    combo_index=combo_index,
                    session_ids_json=[],
                    raw_response=raw_response_text,
                    parsed_input_json=scenario,
                    parse_success=1,
                    parse_error=None,
                    placeholder_tag=placeholder_tag,
                    eval_success=eval_success,
                    elapsed_ms=elapsed_ms,
                    mock=0,
                )
                self.result_writer.commit()

    def run_ablation_prompt(
            self,
            *,
            ctx: PromptContext,
            attempt_id: int = -1,
    ) -> None:
        variants = [
            PROMPT_VARIANT_NO_GLOBAL,
            PROMPT_VARIANT_NO_COMPONENT,
            PROMPT_VARIANT_NO_ADJACENT,
            PROMPT_VARIANT_NO_GUIDING,
        ]
        for variant in variants:
            self.run_single_generation_and_store(
                experiment=EXPERIMENT_ABLATION_PROMPT,
                ctx=ctx,
                prompt_setting=PROMPT_SETTING_EXTRACTED,
                prompt_variant=variant,
                attempt_id=attempt_id,
                invalid_index=-1,
                tau=-1,
                combo_index=-1,
                auto_expand_multi_component=False,
            )


# ============================================================
# Batch runners
# ============================================================

def run_rq1_valid(
        engine: GenerationEngine,
        prompt_repo: PromptRepository,
        target_ids: Sequence[str],
        model: str = DEFAULT_MODEL,
        rerun: bool = False,
        settings: Sequence[Tuple[str, str]] = (),
        attempt_ids: Sequence[int] = VALID_ATTEMPT_IDS,
        run_ablation_prompt: bool = True,
) -> None:
    for i, app_id in enumerate(target_ids, start=1):
        print(f"[{model}][RQ1_VALID] {i}/{len(target_ids)} -> {app_id}")
        ctx = prompt_repo.get_context(VALID_TASK, app_id)

        for attempt_id in attempt_ids:
            print(f"  [{model}][ATTEMPT] valid attempt_id={attempt_id}")

            for prompt_setting, prompt_variant in settings:
                if rerun:
                    deleted = engine.result_writer.delete_exact_generation_record(
                        experiment=EXPERIMENT_RQ1_VALID,
                        case_id=ctx.case_id,
                        task=ctx.task,
                        model=engine.llm_client.model_type,
                        prompt_setting=prompt_setting,
                        prompt_variant=prompt_variant,
                        feedback_mode=FEEDBACK_MODE_NONE,
                        attempt_id=attempt_id,
                        invalid_index=-1,
                        tau=-1,
                        combo_index=-1,
                    )
                    print(
                        f"    [RERUN_DELETE] experiment={EXPERIMENT_RQ1_VALID} "
                        f"case_id={ctx.case_id} setting={prompt_setting}/{prompt_variant} "
                        f"attempt_id={attempt_id} deleted_rows={deleted}"
                    )

                    if prompt_setting == PROMPT_SETTING_EXTRACTED and prompt_variant == PROMPT_VARIANT_FULL:
                        deleted_tau = engine.result_writer.delete_multi_component_records_for_attempt(
                            case_id=ctx.case_id,
                            task=ctx.task,
                            model=engine.llm_client.model_type,
                            attempt_id=attempt_id,
                        )
                        if deleted_tau:
                            print(
                                f"    [RERUN_DELETE_MULTI_COMPONENT] case_id={ctx.case_id} "
                                f"attempt_id={attempt_id} deleted_rows={deleted_tau}"
                            )

                engine.run_single_generation_and_store(
                    experiment=EXPERIMENT_RQ1_VALID,
                    ctx=ctx,
                    prompt_setting=prompt_setting,
                    prompt_variant=prompt_variant,
                    attempt_id=attempt_id,
                    invalid_index=-1,
                    tau=-1,
                    combo_index=-1,
                    auto_expand_multi_component=(
                        prompt_setting == PROMPT_SETTING_EXTRACTED
                        and prompt_variant == PROMPT_VARIANT_FULL
                    ),
                )

            if run_ablation_prompt:
                for variant in (
                        PROMPT_VARIANT_NO_GLOBAL,
                        PROMPT_VARIANT_NO_COMPONENT,
                        PROMPT_VARIANT_NO_ADJACENT,
                        PROMPT_VARIANT_NO_GUIDING,
                ):
                    if rerun:
                        deleted = engine.result_writer.delete_exact_generation_record(
                            experiment=EXPERIMENT_ABLATION_PROMPT,
                            case_id=ctx.case_id,
                            task=ctx.task,
                            model=engine.llm_client.model_type,
                            prompt_setting=PROMPT_SETTING_EXTRACTED,
                            prompt_variant=variant,
                            feedback_mode=FEEDBACK_MODE_NONE,
                            attempt_id=attempt_id,
                            invalid_index=-1,
                            tau=-1,
                            combo_index=-1,
                        )
                        print(
                            f"    [RERUN_DELETE] experiment={EXPERIMENT_ABLATION_PROMPT} "
                            f"case_id={ctx.case_id} setting=extracted/{variant} "
                            f"attempt_id={attempt_id} deleted_rows={deleted}"
                        )

                engine.run_ablation_prompt(
                    ctx=ctx,
                    attempt_id=attempt_id,
                )


def run_rq1_invalid(
        engine: GenerationEngine,
        prompt_repo: PromptRepository,
        target_ids: Sequence[str],
        model: str,
        rerun: bool = False,
        settings: Sequence[Tuple[str, str]] = (),
        invalid_indices: Sequence[int] = INVALID_INDICES,
) -> None:
    for i, page_id in enumerate(target_ids, start=1):
        print(f"[{model}][RQ1_INVALID] {i}/{len(target_ids)} -> {page_id}")
        ctx = prompt_repo.get_context(INVALID_TASK, page_id)

        for invalid_index in invalid_indices:
            print(f"  [{model}][INVALID_INDEX] invalid_index={invalid_index}")

            for prompt_setting, prompt_variant in settings:
                if rerun:
                    deleted = engine.result_writer.delete_exact_generation_record(
                        experiment=EXPERIMENT_RQ1_INVALID,
                        case_id=ctx.case_id,
                        task=ctx.task,
                        model=engine.llm_client.model_type,
                        prompt_setting=prompt_setting,
                        prompt_variant=prompt_variant,
                        feedback_mode=FEEDBACK_MODE_NONE,
                        attempt_id=-1,
                        invalid_index=invalid_index,
                        tau=-1,
                        combo_index=-1,
                    )
                    print(
                        f"    [RERUN_DELETE] experiment={EXPERIMENT_RQ1_INVALID} "
                        f"case_id={ctx.case_id} setting={prompt_setting}/{prompt_variant} "
                        f"invalid_index={invalid_index} deleted_rows={deleted}"
                    )

                engine.run_single_generation_and_store(
                    experiment=EXPERIMENT_RQ1_INVALID,
                    ctx=ctx,
                    prompt_setting=prompt_setting,
                    prompt_variant=prompt_variant,
                    attempt_id=-1,
                    invalid_index=invalid_index,
                    tau=-1,
                    combo_index=-1,
                    auto_expand_multi_component=False,
                )


# ============================================================
# ID loaders
# ============================================================

def parse_settings(settings_text: str) -> List[Tuple[str, str]]:
    mapping = {
        PROMPT_SETTING_EXTRACTED: (PROMPT_SETTING_EXTRACTED, PROMPT_VARIANT_FULL),
        PROMPT_SETTING_XML: (PROMPT_SETTING_XML, PROMPT_VARIANT_XML),
        PROMPT_SETTING_VISION: (PROMPT_SETTING_VISION, PROMPT_VARIANT_VISION),
    }
    out: List[Tuple[str, str]] = []
    for item in settings_text.split(","):
        key = item.strip()
        if not key:
            continue
        if key not in mapping:
            raise ValueError(f"Unknown setting: {key}. Expected one of: extracted,xml,vision")
        out.append(mapping[key])
    if not out:
        raise ValueError("--settings cannot be empty")
    return out


def parse_numeric_selection(selection_text: str, *, min_value: int, max_value: int, label: str) -> List[int]:
    text = str(selection_text).strip()
    if not text:
        return list(range(min_value, max_value + 1))

    values: List[int] = []
    for raw_part in text.split(","):
        part = raw_part.strip()
        if not part:
            continue

        range_match = re.fullmatch(r"(\d+)\s*[-:]\s*(\d+)", part)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            if start > end:
                raise ValueError(f"Invalid {label} range: {part}")
            values.extend(range(start, end + 1))
        else:
            values.append(int(part))

    out: List[int] = []
    seen = set()
    for value in values:
        if value < min_value or value > max_value:
            raise ValueError(f"{label} out of range: {value}. Expected {min_value}-{max_value}")
        if value not in seen:
            out.append(value)
            seen.add(value)

    if not out:
        raise ValueError(f"{label} cannot be empty")
    return out


def list_all_valid_ids() -> List[str]:
    records = read_json_file(PPTR_PROMPTS_JSON)
    return [str(item["app_id"]) for item in records]


def normalize_valid_ids(raw_ids: Sequence[str]) -> List[str]:
    app_ids = list_all_valid_ids()
    out: List[str] = []

    for raw in raw_ids:
        x = str(raw).strip()
        if not x:
            continue

        if x in app_ids:
            out.append(x)
            continue

        if re.fullmatch(r"[Pp]\d+", x):
            idx = int(x[1:])
        elif re.fullmatch(r"\d+", x):
            idx = int(x)
        else:
            raise ValueError(f"Unknown valid id: {x}")

        if idx < 1 or idx > len(app_ids):
            raise ValueError(f"Valid numeric id out of range: {x}. Expected 1-{len(app_ids)}")

        out.append(app_ids[idx - 1])

    return out


def list_all_invalid_ids() -> List[str]:
    records = read_json_file(BUG_PROMPTS_JSON)
    return [safe_int_str(item["page_id"]) for item in records]


def normalize_invalid_ids(raw_ids: Sequence[str]) -> List[str]:
    all_ids = set(list_all_invalid_ids())
    out: List[str] = []
    for raw in raw_ids:
        x = str(raw).strip()
        if not x:
            continue

        if re.fullmatch(r"[Bb]\d+", x):
            idx = int(x[1:])
            x = str(idx)
        elif re.fullmatch(r"\d+", x):
            x = str(int(x))
        else:
            raise ValueError(f"Unknown invalid id: {x}")

        if x not in all_ids:
            raise ValueError(f"Invalid numeric id out of range: {raw}. Expected 1-{len(all_ids)}")

        out.append(x)

    return out


def expand_numeric_range(range_text: str, max_value: int) -> List[str]:
    text = str(range_text).strip()
    if not text:
        return []

    parts = [x.strip() for x in text.split(",") if x.strip()]
    if len(parts) != 2:
        raise ValueError(f"--range must be START,END, e.g., 2,115. Got: {range_text}")

    start = int(parts[0])
    end = int(parts[1])

    if start < 1 or end < 1 or start > end or end > max_value:
        raise ValueError(f"Invalid --range {range_text}. Expected closed range within 1-{max_value}")

    return [str(i) for i in range(start, end + 1)]


def selected_valid_ids(ids_text: str, range_text: str) -> List[str]:
    if ids_text.strip():
        raw_ids = [x.strip() for x in ids_text.split(",") if x.strip()]
        return normalize_valid_ids(raw_ids)
    if range_text.strip():
        return normalize_valid_ids(expand_numeric_range(range_text, len(list_all_valid_ids())))
    return list_all_valid_ids()


def selected_invalid_ids(ids_text: str, range_text: str) -> List[str]:
    if ids_text.strip():
        raw_ids = [x.strip() for x in ids_text.split(",") if x.strip()]
        return normalize_invalid_ids(raw_ids)
    if range_text.strip():
        return normalize_invalid_ids(expand_numeric_range(range_text, len(list_all_invalid_ids())))
    return list_all_invalid_ids()


# ============================================================
# Main
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--task",
        required=True,
        choices=["rq1_valid", "rq1_invalid", "all"],
        help="Which batch to run.",
    )
    parser.add_argument(
        "--ids",
        default="",
        help=(
            "Comma-separated ids. For rq1_valid, supports app_id, numeric index, or Pxxx. "
            "For rq1_invalid, supports numeric id or Bxxx. Empty means all unless --range is set."
        ),
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Model type for API payload.",
    )
    parser.add_argument(
        "--range",
        default="",
        help="Closed numeric range START,END. Used only when --ids is empty, e.g., --range 9,37.",
    )
    parser.add_argument(
        "--settings",
        default="extracted,xml,vision",
        help="Comma-separated prompt settings to run: extracted,xml,vision.",
    )
    parser.add_argument(
        "--attempts",
        default="",
        help="Valid-task attempt ids to run. Supports comma values and ranges like 1,3 or 1-3. Empty means 1-3.",
    )
    parser.add_argument(
        "--invalid-indices",
        default="",
        help="Invalid-task indices to run. Supports comma values and ranges like 1,30 or 20-30. Empty means 1-30.",
    )
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="Delete existing selected RQ1 rows before running, so selected slots are overwritten.",
    )
    parser.add_argument(
        "--skip-ablation-prompt",
        action="store_true",
        help="For rq1_valid, do not run ablation_prompt variants.",
    )
    args = parser.parse_args()

    api_key = get_api_key()
    conn = get_conn()
    start_time = time.time()

    try:
        prompt_repo = PromptRepository()
        llm_client = LLMClient(
            api_key=api_key,
            model_type=args.model,
            base_url=API_URL,
            timeout=500,
            verify_ssl=False,
        )
        writer = ResultWriter(conn)
        engine = GenerationEngine(
            prompt_repo=prompt_repo,
            llm_client=llm_client,
            result_writer=writer,
        )

        settings = parse_settings(args.settings)
        attempt_ids = parse_numeric_selection(args.attempts, min_value=1, max_value=3, label="attempt_id")
        invalid_indices = parse_numeric_selection(
            args.invalid_indices,
            min_value=1,
            max_value=30,
            label="invalid_index",
        )

        if args.task == "rq1_valid":
            ids = selected_valid_ids(args.ids, args.range)
            run_rq1_valid(
                engine,
                prompt_repo,
                ids,
                model=args.model,
                rerun=args.rerun,
                settings=settings,
                attempt_ids=attempt_ids,
                run_ablation_prompt=not args.skip_ablation_prompt,
            )

        elif args.task == "rq1_invalid":
            ids = selected_invalid_ids(args.ids, args.range)
            run_rq1_invalid(
                engine,
                prompt_repo,
                ids,
                model=args.model,
                rerun=args.rerun,
                settings=settings,
                invalid_indices=invalid_indices,
            )

        elif args.task == "all":
            if args.ids.strip():
                raw_ids = [x.strip() for x in args.ids.split(",") if x.strip()]
                for x in raw_ids:
                    if not re.fullmatch(r"\d+", x):
                        raise ValueError(
                            "--task all with --ids only supports numeric ids, e.g., --ids 3,4,5. "
                            "Use rq1_valid or rq1_invalid if you want to pass Pxxx/Bxxx/app_id ids."
                        )
                valid_ids = normalize_valid_ids(raw_ids)
                invalid_ids = normalize_invalid_ids(raw_ids)
            elif args.range.strip():
                valid_ids = normalize_valid_ids(expand_numeric_range(args.range, len(list_all_valid_ids())))
                invalid_ids = normalize_invalid_ids(expand_numeric_range(args.range, len(list_all_invalid_ids())))
            else:
                valid_ids = list_all_valid_ids()
                invalid_ids = list_all_invalid_ids()

            run_rq1_valid(
                engine,
                prompt_repo,
                valid_ids,
                model=args.model,
                rerun=args.rerun,
                settings=settings,
                attempt_ids=attempt_ids,
                run_ablation_prompt=not args.skip_ablation_prompt,
            )
            run_rq1_invalid(
                engine,
                prompt_repo,
                invalid_ids,
                model=args.model,
                rerun=args.rerun,
                settings=settings,
                invalid_indices=invalid_indices,
            )

        elapsed = time.time() - start_time
        print(f"[DONE] elapsed={elapsed:.2f}s")

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
