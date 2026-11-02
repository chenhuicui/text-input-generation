from __future__ import annotations

import argparse
import importlib
import json
import os
import pkgutil
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

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

if str(TOSEM_MAJOR_ROOT) not in sys.path:
    sys.path.insert(0, str(TOSEM_MAJOR_ROOT))

# ============================================================
# API / DB
# ============================================================

API_URL = "http://must.lab.help1.com.cn/api/chat/completions"
DEFAULT_MODEL = "gpt-5.4-2026-03-05"
DB_TABLE = "t_llm_text_generation"


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
        connect_timeout=20,
        read_timeout=600,
        write_timeout=600,
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

VALIDATOR_TASK_VALID = "valid_input"
VALIDATOR_TASK_INVALID = "invalid_bug"

PROMPT_SETTING_EXTRACTED = "extracted"
PROMPT_SETTING_XML = "xml"
PROMPT_SETTING_VISION = "vision"

PROMPT_VARIANT_FULL = "full"
PROMPT_VARIANT_XML = "xml"
PROMPT_VARIANT_VISION = "vision"

FEEDBACK_MODE_FEEDBACK = "feedback"

EXPERIMENT_RQ2_VALID = "rq2_valid_feedback"
EXPERIMENT_RQ2_INVALID = "rq2_invalid_feedback"

VALID_MAX_TURNS = 3
INVALID_MAX_TURNS = 30

# ============================================================
# Utils
# ============================================================

JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
BARE_JSON_RE = re.compile(r"(\{.*\})", re.DOTALL)
GENERATED_VALUE_RE = re.compile(r"\bgenerated_value\b", re.IGNORECASE)

REFUSAL_RE = re.compile(
    r"(i'?m not going to help|i can'?t help|i won'?t help|cannot assist|can'?t assist|"
    r"not able to help|unable to assist|i must refuse)",
    re.IGNORECASE,
)


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
        return {str(item["app_id"]): item for item in records}

    if task == INVALID_TASK:
        records = read_json_file(BUG_PROMPTS_JSON)
        return {safe_int_str(item["page_id"]): item for item in records}

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


def placeholder_tag_of_obj(obj: Optional[Dict[str, Any]]) -> int:
    if not obj:
        return 0

    for value in obj.values():
        if isinstance(value, str) and GENERATED_VALUE_RE.search(value):
            return 1
    return 0


def sort_dict_by_keys(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: d[k] for k in sorted(d.keys())}


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


def json_dumps_safe(value: Any) -> str:
    return json.dumps(clean_surrogates(value), ensure_ascii=False)


def is_refusal_response(text: str) -> bool:
    return bool(REFUSAL_RE.search(text or ""))


# ============================================================
# Validator manager
# ============================================================

class ValidatorManager:
    def __init__(self) -> None:
        self.validator_map: Dict[Tuple[str, str], Any] = {}
        self._import_all_validator_modules()
        self._collect_registered_validators()

    def _import_all_validator_modules(self) -> None:
        for pkg_name in (
                "text_input_eval.validators.pptr",
                "text_input_eval.validators.bug",
        ):
            pkg = importlib.import_module(pkg_name)
            for module_info in pkgutil.iter_modules(pkg.__path__):
                if module_info.name.startswith("_"):
                    continue
                importlib.import_module(f"{pkg_name}.{module_info.name}")

    def _collect_registered_validators(self) -> None:
        try:
            from text_input_eval.validators.registry import VALIDATOR_REGISTRY

            for key, validator_cls in VALIDATOR_REGISTRY.items():
                validator = validator_cls()
                case_id = getattr(validator, "case_id", None)
                task = getattr(validator, "task", None)
                if case_id and task:
                    self.validator_map[(str(case_id), str(task))] = validator
            return
        except Exception:
            pass

        from text_input_eval.validators.base import BaseValidator

        subclasses = list(BaseValidator.__subclasses__())
        seen = set()

        while subclasses:
            cls = subclasses.pop()
            if cls in seen:
                continue

            seen.add(cls)
            subclasses.extend(cls.__subclasses__())

            case_id = getattr(cls, "case_id", None)
            task = getattr(cls, "task", None)
            if case_id and task:
                self.validator_map[(str(case_id), str(task))] = cls()

    def validate(self, case_id: str, validator_task: str, parsed_inputs: Dict[str, Any]) -> bool:
        key = (case_id, validator_task)
        if key not in self.validator_map:
            raise KeyError(f"Validator not found for case_id={case_id}, task={validator_task}")

        return bool(self.validator_map[key].validate(parsed_inputs))


# ============================================================
# Prompt repository
# ============================================================

@dataclass
class PromptContext:
    task: str
    target_id: str
    case_id: str
    validator_task: str
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
            validator_task = VALIDATOR_TASK_VALID

        elif task == INVALID_TASK:
            row = self.invalid_records[target_id]
            case_id = normalize_case_id_for_invalid(target_id)
            validator_task = VALIDATOR_TASK_INVALID

        else:
            raise ValueError(f"Unsupported task: {task}")

        restrictive_text = str(row["restrictive"])
        resource_ids = parse_component_resource_ids_from_restrictive(restrictive_text)

        return PromptContext(
            task=task,
            target_id=target_id,
            case_id=case_id,
            validator_task=validator_task,
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
            return PromptGenerator._build_extracted_prompt(ctx), None

        if prompt_setting == PROMPT_SETTING_XML:
            return PromptGenerator._build_xml_prompt(ctx), None

        if prompt_setting == PROMPT_SETTING_VISION:
            message = PromptGenerator._build_vision_prompt(ctx)
            image_url = f"data:image/png;base64,{ctx.image_64}"
            return message, image_url

        raise ValueError(f"Unsupported prompt_setting: {prompt_setting}")

    @staticmethod
    def _build_extracted_prompt(ctx: PromptContext) -> str:
        parts = [
            ctx.global_text,
            ctx.component_text,
            ctx.adjacent_text,
            ctx.restrictive_text,
            ctx.guiding_text,
        ]
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

    def build_payload(
            self,
            message: str,
            image_url: Optional[str] = None,
            cursor_id: str = "",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "modelType": self.model_type,
            # Send the previous response data.id as the next request id.
            "id": cursor_id,
            "message": message,
        }
        if image_url:
            payload["imageUrl"] = image_url
        return payload

    def post_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = self.session.post(
            self.base_url,
            json=payload,
            timeout=self.timeout,
            verify=self.verify_ssl,
        )
        response.raise_for_status()
        return response.json()

    def chat_completion(
            self,
            message: str,
            image_url: Optional[str] = None,
            cursor_id: str = "",
    ) -> Dict[str, Any]:
        return self.post_payload(
            self.build_payload(
                message=message,
                image_url=image_url,
                cursor_id=cursor_id,
            )
        )


# ============================================================
# Feedback builder
# ============================================================

class FeedbackBuilder:
    @staticmethod
    def build_schema_text(resource_ids: Sequence[str]) -> str:
        return json.dumps(
            {rid: "generated_value" for rid in resource_ids},
            ensure_ascii=False,
            indent=2,
        )

    @staticmethod
    def build_failed_history_text(failed_turns: Sequence[Dict[str, Any]]) -> str:
        if not failed_turns:
            return "(No previous failed turns.)"

        # Keep only recent failed attempts.
        # Do NOT include raw model outputs here, because raw outputs may contain
        # explanations, security wording, markdown, or refusal text that can
        # pollute the next feedback prompt.
        recent_turns = list(failed_turns)[-5:]

        parts: List[str] = []
        for item in recent_turns:
            turn = item.get("turn")
            parsed_json = item.get("parsed_input_json")
            error_type = item.get("error_type")
            error_detail = str(item.get("error_detail") or "")
            parse_success = item.get("parse_success")
            eval_success = item.get("eval_success")

            if len(error_detail) > 500:
                error_detail = error_detail[:500] + "\n...[truncated]"

            parsed_json_text = (
                json.dumps(parsed_json, ensure_ascii=False, indent=2)
                if parsed_json is not None
                else "(not available)"
            )

            parts.append(
                f"[TURN {turn}]\n"
                f"- parse_success: {parse_success}\n"
                f"- eval_success: {eval_success}\n"
                f"- error_type: {error_type}\n"
                f"- error_detail: {error_detail}\n"
                f"- previous_json:\n{parsed_json_text}"
            )

        return "\n\n".join(parts)

    @staticmethod
    def build_feedback(
            *,
            ctx: PromptContext,
            failed_turns: Sequence[Dict[str, Any]],
            error_type: str,
            error_detail: str,
    ) -> str:
        required_ids = "\n".join([f"- {rid}" for rid in ctx.resource_ids])
        schema_text = FeedbackBuilder.build_schema_text(ctx.resource_ids)
        history_text = FeedbackBuilder.build_failed_history_text(failed_turns)

        if ctx.task == VALID_TASK:
            task_goal = (
                "Generate valid text inputs that satisfy the page pass-through condition. "
                "The app should be able to proceed to the next page after these values are entered."
            )
            semantic_requirement = (
                "Avoid repeating previous values that failed the validator. "
                "Revise the generated values to better satisfy the UI field constraints and page pass-through requirement."
            )
        else:
            task_goal = (
                "Generate one boundary-case text input for a negative GUI test. "
                "The value should exercise the app's existing text-input validation behavior for this specific UI field."
            )
            semantic_requirement = (
                "Avoid repeating previous values that did not satisfy the case-specific validator. "
                "Revise the value according to the UI field semantics and the validator objective. "
                "Do not include explanations, attack descriptions, security terms, markdown, or code-like payloads."
            )

        if error_type == "syntax":
            problem_text = (
                "At least one previous response could not be parsed as a valid JSON object."
            )
        elif error_type == "schema":
            problem_text = (
                "At least one previous response used incorrect resource IDs. "
                "The regenerated JSON must use exactly the required resource IDs."
            )
        elif error_type == "refusal":
            problem_text = (
                "A previous response did not return the required JSON object. "
                "Regenerate a concise JSON-only boundary-case input without explanations."
            )
        else:
            problem_text = (
                "Previous responses were syntactically valid and used the required resource IDs, "
                "but they did not satisfy the validator objective."
            )

        return (
            "[FEEDBACK]\n"
            f"{problem_text}\n\n"
            "[TASK GOAL]\n"
            f"{task_goal}\n\n"
            "[FAILED HISTORY]\n"
            f"{history_text}\n\n"
            "[LATEST ERROR DETAIL]\n"
            f"{error_detail}\n\n"
            "[REGENERATION REQUIREMENTS]\n"
            f"{semantic_requirement}\n"
            "Return ONLY one valid JSON object. Do not include explanations, markdown fences, comments, analysis, security discussion, or extra text.\n"
            "The JSON keys must exactly match the required resource IDs: no missing keys and no extra keys.\n\n"
            "[REQUIRED RESOURCE IDS]\n"
            f"{required_ids}\n\n"
            "[REQUIRED JSON SCHEMA]\n"
            f"{schema_text}"
        )


# ============================================================
# DB writer
# ============================================================

class ResultWriter:
    def __init__(self, conn) -> None:
        self.conn = conn

    def ensure_conn_alive(self) -> None:
        self.conn.ping(reconnect=True)

    def delete_existing_feedback_records_for_setting(
            self,
            *,
            experiment: str,
            case_id: str,
            task: str,
            model: str,
            prompt_setting: str,
            prompt_variant: str,
            feedback_mode: str,
            tau: int = -1,
            combo_index: int = -1,
    ) -> int:
        self.ensure_conn_alive()
        sql = f"""
            DELETE FROM {DB_TABLE}
            WHERE experiment = %(experiment)s
              AND case_id = %(case_id)s
              AND task = %(task)s
              AND model = %(model)s
              AND prompt_setting = %(prompt_setting)s
              AND prompt_variant = %(prompt_variant)s
              AND feedback_mode = %(feedback_mode)s
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
            "tau": tau,
            "combo_index": combo_index,
        }
        with self.conn.cursor() as cursor:
            affected = cursor.execute(sql, params)
        self.conn.commit()
        return int(affected)

    def has_existing_turn(self, *, experiment, case_id, task, model,
                          prompt_setting, prompt_variant, feedback_mode,
                          attempt_id, invalid_index, tau, combo_index) -> bool:
        self.ensure_conn_alive()

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

        try:
            with self.conn.cursor() as cursor:
                cursor.execute("SET SESSION innodb_lock_wait_timeout = 5")
                cursor.execute(sql, params)
                row = cursor.fetchone()
            return row is not None
        except pymysql.err.OperationalError as e:
            print(f"      [TURN_EXIST_CHECK_ERROR] {type(e).__name__}: {e}; continue as not existing", flush=True)
            return False

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
            feedback_prompt_text: Optional[str] = None,
    ) -> None:
        self.ensure_conn_alive()
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
                mock,
                feedback_prompt_text
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
                %(mock)s,
                %(feedback_prompt_text)s
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
                mock = VALUES(mock),
                feedback_prompt_text = VALUES(feedback_prompt_text)
        """
        params = {
            "experiment": clean_surrogates(experiment),
            "case_id": clean_surrogates(case_id),
            "task": clean_surrogates(task),
            "model": clean_surrogates(model),
            "prompt_setting": clean_surrogates(prompt_setting),
            "prompt_variant": clean_surrogates(prompt_variant),
            "feedback_mode": clean_surrogates(feedback_mode),
            "attempt_id": attempt_id,
            "invalid_index": invalid_index,
            "tau": tau,
            "combo_index": combo_index,
            "session_ids_json": json_dumps_safe(session_ids_json) if session_ids_json is not None else None,
            "raw_response": clean_surrogates(raw_response),
            "parsed_input_json": json_dumps_safe(parsed_input_json) if parsed_input_json is not None else None,
            "parse_success": parse_success,
            "parse_error": clean_surrogates(parse_error),
            "placeholder_tag": placeholder_tag,
            "eval_success": eval_success,
            "elapsed_ms": elapsed_ms,
            "mock": mock,
            "feedback_prompt_text": clean_surrogates(feedback_prompt_text),
        }
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)

    def commit(self) -> None:
        try:
            self.ensure_conn_alive()
            self.conn.commit()
        except pymysql.err.OperationalError:
            self.conn.ping(reconnect=True)
            self.conn.commit()


# ============================================================
# Generation core
# ============================================================

@dataclass
class TurnCheckResult:
    parsed_input_json: Optional[Dict[str, Any]]
    parse_success: int
    parse_error: Optional[str]
    placeholder_tag: int
    eval_success: int
    error_type: Optional[str]
    error_detail: Optional[str]


@dataclass
class FeedbackTrajectoryOutput:
    raw_response_text: str
    parsed_input_json: Optional[Dict[str, Any]]
    parse_success: int
    parse_error: Optional[str]
    placeholder_tag: int
    eval_success: int
    session_ids: List[str]
    success_turn: Optional[int]
    total_turns: int


class GenerationEngine:
    def __init__(
            self,
            prompt_repo: PromptRepository,
            llm_client: LLMClient,
            result_writer: ResultWriter,
            validator_manager: ValidatorManager,
    ) -> None:
        self.prompt_repo = prompt_repo
        self.llm_client = llm_client
        self.result_writer = result_writer
        self.validator_manager = validator_manager

    def check_turn_result(
            self,
            *,
            ctx: PromptContext,
            raw_response_text: str,
    ) -> TurnCheckResult:
        if is_refusal_response(raw_response_text):
            return TurnCheckResult(
                parsed_input_json=None,
                parse_success=0,
                parse_error="Model refusal response detected.",
                placeholder_tag=0,
                eval_success=0,
                error_type="refusal",
                error_detail="Model returned a refusal instead of a JSON object.",
            )

        try:
            parsed_obj = extract_json_object_from_text(raw_response_text)
        except Exception as e:
            return TurnCheckResult(
                parsed_input_json=None,
                parse_success=0,
                parse_error=str(e),
                placeholder_tag=0,
                eval_success=0,
                error_type="syntax",
                error_detail=str(e),
            )

        try:
            validate_resource_ids_exact_match(parsed_obj, ctx.resource_ids)
        except Exception as e:
            return TurnCheckResult(
                parsed_input_json=None,
                parse_success=0,
                parse_error=str(e),
                placeholder_tag=placeholder_tag_of_obj(parsed_obj),
                eval_success=0,
                error_type="schema",
                error_detail=str(e),
            )

        sorted_obj = sort_dict_by_keys(parsed_obj)
        placeholder_tag = placeholder_tag_of_obj(sorted_obj)

        try:
            eval_success = 1 if self.validator_manager.validate(
                ctx.case_id,
                ctx.validator_task,
                sorted_obj,
            ) else 0
        except Exception as e:
            return TurnCheckResult(
                parsed_input_json=sorted_obj,
                parse_success=1,
                parse_error=None,
                placeholder_tag=placeholder_tag,
                eval_success=0,
                error_type="semantic",
                error_detail=f"Validator execution failed or returned false: {type(e).__name__}: {e}",
            )

        if eval_success == 1:
            return TurnCheckResult(
                parsed_input_json=sorted_obj,
                parse_success=1,
                parse_error=None,
                placeholder_tag=placeholder_tag,
                eval_success=1,
                error_type=None,
                error_detail=None,
            )

        return TurnCheckResult(
            parsed_input_json=sorted_obj,
            parse_success=1,
            parse_error=None,
            placeholder_tag=placeholder_tag,
            eval_success=0,
            error_type="semantic",
            error_detail=(
                "Validator returned false. "
                f"case_id={ctx.case_id}, validator_task={ctx.validator_task}"
            ),
        )

    def generate_feedback_trajectory(
            self,
            *,
            experiment: str,
            ctx: PromptContext,
            prompt_setting: str,
            prompt_variant: str,
            max_turns: int,
            tau: int = -1,
            combo_index: int = -1,
    ) -> FeedbackTrajectoryOutput:
        initial_message, image_url = PromptGenerator.build_prompt(ctx, prompt_setting, prompt_variant)

        current_message = initial_message
        current_cursor_id = ""

        # Compatibility field: the database column is still named
        # session_ids_json, but these values are response data.id cursors.
        session_ids: List[str] = []
        turns: List[Dict[str, Any]] = []

        final_parsed_input_json: Optional[Dict[str, Any]] = None
        final_parse_success = 0
        final_parse_error: Optional[str] = None
        final_placeholder_tag = 0
        final_eval_success = 0
        success_turn: Optional[int] = None

        trajectory_start_time = time.perf_counter()

        for turn_id in range(1, max_turns + 1):
            if ctx.task == VALID_TASK:
                turn_attempt_id = turn_id
                turn_invalid_index = -1
            else:
                turn_attempt_id = -1
                turn_invalid_index = turn_id

            print(
                f"      [TURN_EXIST_CHECK_START] case_id={ctx.case_id} "
                f"setting={prompt_setting}/{prompt_variant} "
                f"turn={turn_id}/{max_turns}",
                flush=True,
            )

            if self.result_writer.has_existing_turn(
                    experiment=experiment,
                    case_id=ctx.case_id,
                    task=ctx.task,
                    model=self.llm_client.model_type,
                    prompt_setting=prompt_setting,
                    prompt_variant=prompt_variant,
                    feedback_mode=FEEDBACK_MODE_FEEDBACK,
                    attempt_id=turn_attempt_id,
                    invalid_index=turn_invalid_index,
                    tau=tau,
                    combo_index=combo_index,
            ):
                print(
                    f"      [TURN_SKIP] case_id={ctx.case_id} "
                    f"setting={prompt_setting}/{prompt_variant} "
                    f"turn={turn_id}/{max_turns} "
                    f"attempt_id={turn_attempt_id} invalid_index={turn_invalid_index} "
                    f"reason=turn already exists"
                )

                break

            request_cursor_id = current_cursor_id
            request_message_type = "initial" if turn_id == 1 else "feedback"

            print(
                f"      [TURN_START] case_id={ctx.case_id} "
                f"setting={prompt_setting}/{prompt_variant} "
                f"turn={turn_id}/{max_turns} "
                f"request_cursor_id={request_cursor_id}"
            )

            request_payload = self.llm_client.build_payload(
                message=current_message,
                image_url=image_url if turn_id == 1 else None,
                cursor_id=request_cursor_id,
            )
            feedback_prompt_text = None if turn_id == 1 else json_dumps_safe(request_payload)

            try:
                response = self.llm_client.post_payload(request_payload)
                api_success = bool(response.get("success", True))
                data = response.get("data") or {}

                response_id = str(data.get("id") or "").strip()
                response_session_id = str(data.get("sessionId") or "").strip()
                next_cursor_id = response_id
                if next_cursor_id:
                    session_ids.append(next_cursor_id)
                    current_cursor_id = next_cursor_id

                chat_text = str(data.get("chat") or "")
                if not chat_text:
                    chat_text = json_dumps_safe(response)

                raw_response_text = chat_text

                api_error = None
                if not api_success:
                    api_error = str(response.get("msg") or response.get("code") or "api success=false")

            except Exception as e:
                api_success = False
                response_id = ""
                response_session_id = ""
                api_error = f"{type(e).__name__}: {e}"
                raw_response_text = ""

                check = TurnCheckResult(
                    parsed_input_json=None,
                    parse_success=0,
                    parse_error=api_error,
                    placeholder_tag=0,
                    eval_success=0,
                    error_type="syntax",
                    error_detail=api_error,
                )

                turn_record = {
                    "turn": turn_id,
                    "request_cursor_id": request_cursor_id,
                    "request_message_type": request_message_type,
                    "request_payload": request_payload,
                    "feedback_prompt_text": feedback_prompt_text,
                    "response_id": response_id,
                    "response_session_id": response_session_id,
                    "api_success": api_success,
                    "api_error": api_error,
                    "raw_response": raw_response_text,
                    "parsed_input_json": None,
                    "parse_success": check.parse_success,
                    "parse_error": check.parse_error,
                    "placeholder_tag": check.placeholder_tag,
                    "eval_success": check.eval_success,
                    "error_type": check.error_type,
                    "error_detail": check.error_detail,
                }
                turns.append(turn_record)

                final_parsed_input_json = None
                final_parse_success = 0
                final_parse_error = api_error
                final_placeholder_tag = 0
                final_eval_success = 0

                elapsed_ms = int((time.perf_counter() - trajectory_start_time) * 1000)
                self._store_turn_record(
                    experiment=experiment,
                    ctx=ctx,
                    prompt_setting=prompt_setting,
                    prompt_variant=prompt_variant,
                    turn_id=turn_id,
                    attempt_id=turn_attempt_id,
                    invalid_index=turn_invalid_index,
                    tau=tau,
                    combo_index=combo_index,
                    max_turns=max_turns,
                    success_turn=success_turn,
                    session_ids=session_ids,
                    turns=turns,
                    parsed_input_json=final_parsed_input_json,
                    parse_success=final_parse_success,
                    parse_error=final_parse_error,
                    placeholder_tag=final_placeholder_tag,
                    eval_success=final_eval_success,
                    elapsed_ms=elapsed_ms,
                    feedback_prompt_text=feedback_prompt_text,
                )

                if turn_id >= max_turns:
                    break

                current_message = FeedbackBuilder.build_feedback(
                    ctx=ctx,
                    failed_turns=turns,
                    error_type=check.error_type or "syntax",
                    error_detail=check.error_detail or "Unknown API/runtime error.",
                )
                continue

            check = self.check_turn_result(
                ctx=ctx,
                raw_response_text=raw_response_text,
            )

            # print(
            #     f"      [TURN_DONE] case_id={ctx.case_id} "
            #     f"setting={prompt_setting}/{prompt_variant} "
            #     f"turn={turn_id}/{max_turns} "
            #     f"response_id={response_id} "
            #     f"parse_success={check.parse_success} "
            #     f"eval_success={check.eval_success} "
            #     f"error_type={check.error_type}"
            # )

            turn_record = {
                "turn": turn_id,
                "request_cursor_id": request_cursor_id,
                "request_message_type": request_message_type,
                "request_payload": request_payload,
                "feedback_prompt_text": feedback_prompt_text,
                "response_id": response_id,
                "response_session_id": response_session_id,
                "api_success": api_success,
                "api_error": api_error,
                "raw_response": raw_response_text,
                "parsed_input_json": check.parsed_input_json,
                "parse_success": check.parse_success,
                "parse_error": check.parse_error,
                "placeholder_tag": check.placeholder_tag,
                "eval_success": check.eval_success,
                "error_type": check.error_type,
                "error_detail": check.error_detail,
            }
            turns.append(turn_record)

            final_parsed_input_json = check.parsed_input_json
            final_parse_success = check.parse_success
            final_parse_error = check.parse_error
            final_placeholder_tag = check.placeholder_tag
            final_eval_success = check.eval_success

            if check.eval_success == 1:
                success_turn = turn_id

            elapsed_ms = int((time.perf_counter() - trajectory_start_time) * 1000)
            self._store_turn_record(
                experiment=experiment,
                ctx=ctx,
                prompt_setting=prompt_setting,
                prompt_variant=prompt_variant,
                turn_id=turn_id,
                attempt_id=turn_attempt_id,
                invalid_index=turn_invalid_index,
                tau=tau,
                combo_index=combo_index,
                max_turns=max_turns,
                success_turn=success_turn,
                session_ids=session_ids,
                turns=turns,
                parsed_input_json=final_parsed_input_json,
                parse_success=final_parse_success,
                parse_error=final_parse_error,
                placeholder_tag=final_placeholder_tag,
                eval_success=final_eval_success,
                elapsed_ms=elapsed_ms,
                feedback_prompt_text=feedback_prompt_text,
            )

            if check.eval_success == 1:
                break

            if check.error_type == "refusal":
                print(
                    f"      [TURN_STOP_REFUSAL] case_id={ctx.case_id} "
                    f"setting={prompt_setting}/{prompt_variant} turn={turn_id}/{max_turns}",
                    flush=True,
                )
                break

            if turn_id >= max_turns:
                break

            current_message = FeedbackBuilder.build_feedback(
                ctx=ctx,
                failed_turns=turns,
                error_type=check.error_type or "semantic",
                error_detail=check.error_detail or "Unknown error.",
            )

        trajectory = {
            "mode": "feedback_based",
            "case_id": ctx.case_id,
            "target_id": ctx.target_id,
            "task": ctx.task,
            "validator_task": ctx.validator_task,
            "prompt_setting": prompt_setting,
            "prompt_variant": prompt_variant,
            "max_turns": max_turns,
            "total_turns": len(turns),
            "success_turn": success_turn,
            "stopped_early": success_turn is not None,
            "session_ids": session_ids,
            "latest_turn": turns[-1] if turns else None,
        }

        return FeedbackTrajectoryOutput(
            raw_response_text=json_dumps_safe(trajectory),
            parsed_input_json=final_parsed_input_json,
            parse_success=final_parse_success,
            parse_error=final_parse_error,
            placeholder_tag=final_placeholder_tag,
            eval_success=final_eval_success,
            session_ids=session_ids,
            success_turn=success_turn,
            total_turns=len(turns),
        )

    def _store_turn_record(
            self,
            *,
            experiment: str,
            ctx: PromptContext,
            prompt_setting: str,
            prompt_variant: str,
            turn_id: int,
            attempt_id: int,
            invalid_index: int,
            tau: int,
            combo_index: int,
            max_turns: int,
            success_turn: Optional[int],
            session_ids: List[str],
            turns: List[Dict[str, Any]],
            parsed_input_json: Optional[Dict[str, Any]],
            parse_success: int,
            parse_error: Optional[str],
            placeholder_tag: int,
            eval_success: int,
            elapsed_ms: int,
            feedback_prompt_text: Optional[str] = None,
    ) -> None:
        trajectory = {
            "mode": "feedback_based",
            "case_id": ctx.case_id,
            "target_id": ctx.target_id,
            "task": ctx.task,
            "validator_task": ctx.validator_task,
            "prompt_setting": prompt_setting,
            "prompt_variant": prompt_variant,
            "max_turns": max_turns,
            "current_turn": turn_id,
            "total_turns_so_far": len(turns),
            "success_turn": success_turn,
            "stopped_early": success_turn is not None,
            "session_ids": session_ids,
            "latest_turn": turns[-1] if turns else None,
        }

        self.result_writer.upsert_generation_result(
            experiment=experiment,
            case_id=ctx.case_id,
            task=ctx.task,
            model=self.llm_client.model_type,
            prompt_setting=prompt_setting,
            prompt_variant=prompt_variant,
            feedback_mode=FEEDBACK_MODE_FEEDBACK,
            attempt_id=attempt_id,
            invalid_index=invalid_index,
            tau=tau,
            combo_index=combo_index,
            session_ids_json=session_ids,
            raw_response=json_dumps_safe(trajectory),
            parsed_input_json=parsed_input_json,
            parse_success=parse_success,
            parse_error=parse_error,
            placeholder_tag=placeholder_tag,
            eval_success=eval_success,
            elapsed_ms=elapsed_ms,
            mock=0,
            feedback_prompt_text=feedback_prompt_text,
        )
        self.result_writer.commit()

        print(
            f"      [TURN_SAVED] case_id={ctx.case_id} "
            f"setting={prompt_setting}/{prompt_variant} "
            f"turn={turn_id}/{max_turns} "
            f"attempt_id={attempt_id} invalid_index={invalid_index} "
            f" | result: parse_success={parse_success} / eval_success={eval_success} | elapsed_ms={elapsed_ms}"
        )

    def run_feedback_trajectory_and_store(
            self,
            *,
            experiment: str,
            ctx: PromptContext,
            prompt_setting: str,
            prompt_variant: str,
            attempt_id: int,
            invalid_index: int,
            max_turns: int,
            tau: int = -1,
            combo_index: int = -1,
    ) -> None:
        t0 = time.perf_counter()

        result = self.generate_feedback_trajectory(
            experiment=experiment,
            ctx=ctx,
            prompt_setting=prompt_setting,
            prompt_variant=prompt_variant,
            max_turns=max_turns,
            tau=tau,
            combo_index=combo_index,
        )

        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        print(
            f"    [SAVED] case_id={ctx.case_id} "
            f"setting={prompt_setting}/{prompt_variant} "
            f"attempt_id={attempt_id} invalid_index={invalid_index} "
            f"turns={result.total_turns}/{max_turns} "
            f"success_turn={result.success_turn} "
            f"eval_success={result.eval_success} "
            f"parse_success={result.parse_success} "
            f"elapsed_ms={elapsed_ms}"
        )


# ============================================================
# Batch runners
# ============================================================

def run_rq2_valid(
        engine: GenerationEngine,
        prompt_repo: PromptRepository,
        target_ids: Sequence[str],
        model: str = DEFAULT_MODEL,
        rerun: bool = False,
        settings: Sequence[Tuple[str, str]] = (),
) -> None:
    for i, app_id in enumerate(target_ids, start=1):
        print(f"[{model}][RQ2_VALID_FEEDBACK] {i}/{len(target_ids)} -> {app_id}")
        ctx = prompt_repo.get_context(VALID_TASK, app_id)

        for prompt_setting, prompt_variant in settings:
            if rerun:
                deleted = engine.result_writer.delete_existing_feedback_records_for_setting(
                    experiment=EXPERIMENT_RQ2_VALID,
                    case_id=ctx.case_id,
                    task=ctx.task,
                    model=engine.llm_client.model_type,
                    prompt_setting=prompt_setting,
                    prompt_variant=prompt_variant,
                    feedback_mode=FEEDBACK_MODE_FEEDBACK,
                    tau=-1,
                    combo_index=-1,
                )
                print(
                    f"    [RERUN_DELETE_SETTING] case_id={ctx.case_id} "
                    f"setting={prompt_setting}/{prompt_variant} deleted_rows={deleted}"
                )

            engine.run_feedback_trajectory_and_store(
                experiment=EXPERIMENT_RQ2_VALID,
                ctx=ctx,
                prompt_setting=prompt_setting,
                prompt_variant=prompt_variant,
                attempt_id=1,
                invalid_index=-1,
                max_turns=VALID_MAX_TURNS,
                tau=-1,
                combo_index=-1,
            )


def run_rq2_invalid(
        engine: GenerationEngine,
        prompt_repo: PromptRepository,
        target_ids: Sequence[str],
        model: str = DEFAULT_MODEL,
        rerun: bool = False,
        settings: Sequence[Tuple[str, str]] = (),
) -> None:
    for i, page_id in enumerate(target_ids, start=1):
        print(f"[{model}][RQ2_INVALID_FEEDBACK] {i}/{len(target_ids)} -> {page_id}")
        ctx = prompt_repo.get_context(INVALID_TASK, page_id)

        for prompt_setting, prompt_variant in settings:
            if rerun:
                deleted = engine.result_writer.delete_existing_feedback_records_for_setting(
                    experiment=EXPERIMENT_RQ2_INVALID,
                    case_id=ctx.case_id,
                    task=ctx.task,
                    model=engine.llm_client.model_type,
                    prompt_setting=prompt_setting,
                    prompt_variant=prompt_variant,
                    feedback_mode=FEEDBACK_MODE_FEEDBACK,
                    tau=-1,
                    combo_index=-1,
                )
                print(
                    f"    [RERUN_DELETE_SETTING] case_id={ctx.case_id} "
                    f"setting={prompt_setting}/{prompt_variant} deleted_rows={deleted}"
                )

            engine.run_feedback_trajectory_and_store(
                experiment=EXPERIMENT_RQ2_INVALID,
                ctx=ctx,
                prompt_setting=prompt_setting,
                prompt_variant=prompt_variant,
                attempt_id=-1,
                invalid_index=1,
                max_turns=INVALID_MAX_TURNS,
                tau=-1,
                combo_index=-1,
            )


# ============================================================
# ID loaders
# ============================================================

def parse_settings(settings_text: str) -> list[tuple[str,str]]:
    mapping = {
        'extracted': ('extracted','full'),
        'xml': ('xml','xml'),
        'vision': ('vision','vision'),
    }
    out: list[tuple[str,str]] = []
    for item in settings_text.split(','):
        key = item.strip()
        if not key:
            continue
        if key not in mapping:
            raise ValueError(f'Unknown setting: {key}. Expected one of: extracted,xml,vision')
        out.append(mapping[key])
    if not out:
        raise ValueError('--settings cannot be empty')
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


def expand_numeric_range(range_text: str, max_value: int) -> List[str]:
    """
    Expand closed numeric range, e.g., "2,115" -> ["2", ..., "115"].
    """
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


def list_all_invalid_ids() -> List[str]:
    records = read_json_file(BUG_PROMPTS_JSON)
    return [safe_int_str(item["page_id"]) for item in records]


# ============================================================
# Main
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--task",
        required=True,
        choices=["rq2_valid_feedback", "rq2_invalid_feedback", "all"],
        help="Which RQ2 feedback batch to run.",
    )
    parser.add_argument(
        "--ids",
        default="",
        help="Comma-separated app_ids or page_ids. Empty means all.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Model type for API payload.",
    )
    parser.add_argument(
        "--range",
        default="",
        help="Closed numeric range START,END. Used only when --ids is empty, e.g., --range 2,115.",
    )
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="Delete existing RQ2 feedback records for the selected cases/settings/model before running.",
    )
    parser.add_argument(
        "--settings",
        default="extracted,xml,vision",
        help="Comma-separated prompt settings to run: extracted,xml,vision.",
    )
    args = parser.parse_args()

    api_key = get_api_key()
    conn = get_conn()
    start_time = time.time()

    try:
        prompt_repo = PromptRepository()
        validator_manager = ValidatorManager()
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
            validator_manager=validator_manager,
        )

        settings = parse_settings(args.settings)

        if args.task == "rq2_valid_feedback":
            raw_ids = [x.strip() for x in args.ids.split(",") if x.strip()] if args.ids.strip() else []
            if raw_ids:
                ids = normalize_valid_ids(raw_ids)
            elif args.range.strip():
                ids = normalize_valid_ids(expand_numeric_range(args.range, len(list_all_valid_ids())))
            else:
                ids = list_all_valid_ids()
            run_rq2_valid(engine, prompt_repo, ids, model=args.model, rerun=args.rerun, settings=settings)

        elif args.task == "rq2_invalid_feedback":
            if args.ids.strip():
                ids = [x.strip() for x in args.ids.split(",") if x.strip()]
            elif args.range.strip():
                ids = expand_numeric_range(args.range, len(list_all_invalid_ids()))
            else:
                ids = list_all_invalid_ids()
            run_rq2_invalid(engine, prompt_repo, ids, model=args.model, rerun=args.rerun, settings=settings)

        elif args.task == "all":
            if args.ids.strip():
                raw_ids = [x.strip() for x in args.ids.split(",") if x.strip()]
                for x in raw_ids:
                    if not re.fullmatch(r"\d+", x):
                        raise ValueError(
                            "--task all with --ids only supports numeric ids, e.g., --ids 3,4,5. "
                            "Use rq2_valid_feedback or rq2_invalid_feedback if you want to pass Pxxx/Bxxx-specific ids."
                        )
                valid_ids = normalize_valid_ids(raw_ids)
                invalid_ids = raw_ids
            elif args.range.strip():
                valid_ids = normalize_valid_ids(expand_numeric_range(args.range, len(list_all_valid_ids())))
                invalid_ids = expand_numeric_range(args.range, len(list_all_invalid_ids()))
            else:
                valid_ids = list_all_valid_ids()
                invalid_ids = list_all_invalid_ids()

            run_rq2_valid(engine, prompt_repo, valid_ids, model=args.model, rerun=args.rerun, settings=settings)
            run_rq2_invalid(engine, prompt_repo, invalid_ids, model=args.model, rerun=args.rerun, settings=settings)

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
