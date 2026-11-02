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
MAX_RETRIES = 5

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


def normalize_case_id_for_valid(app_id: str) -> str:
    return app_id


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
        out = {}
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


def format_retry_prompt(example_json: Dict[str, Any], component_count: int) -> str:
    example_json_text = json.dumps(example_json, ensure_ascii=False, indent=2)
    return (
        "Test case parsing failed. Please regenerate with strict format:\n"
        "MUST use the following EXACT JSON format:\n"
        "```json\n"
        f"{example_json_text}\n"
        "```\n"
        "Requirements:\n"
        "1. Keys must match resource ids exactly.\n"
        f"2. Include all {component_count} components.\n"
        "3. Return ONLY one JSON object.\n"
        "4. Do not wrap the answer with extra explanations if possible.\n"
    )


def build_example_json_from_resource_ids(resource_ids: Sequence[str]) -> Dict[str, str]:
    return {rid: "generated_value" for rid in resource_ids}


def sort_dict_by_keys(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: d[k] for k in sorted(d.keys())}


def combinations_for_tau(resource_ids: Sequence[str], tau: int) -> Iterable[Tuple[str, ...]]:
    if tau < 0 or tau > len(resource_ids):
        return []
    return itertools.combinations(resource_ids, tau)


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

    def get_context(self, task: str, target_id: str) -> PromptContext:
        if task == VALID_TASK:
            row = self.valid_records[target_id]
            case_id = normalize_case_id_for_valid(target_id)
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
        timeout: int = 60,
        verify_ssl: bool = False,
    ) -> None:
        self.api_key = api_key
        self.model_type = model_type
        self.base_url = base_url
        self.timeout = timeout
        self.verify_ssl = verify_ssl

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        self.session = requests.Session()
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
        mock: int = 0,
    ) -> None:
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
                mock = VALUES(mock)
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
            "session_ids_json": json.dumps(session_ids_json, ensure_ascii=False)
            if session_ids_json is not None
            else None,
            "raw_response": raw_response,
            "parsed_input_json": json.dumps(parsed_input_json, ensure_ascii=False)
            if parsed_input_json is not None
            else None,
            "parse_success": parse_success,
            "parse_error": parse_error,
            "placeholder_tag": placeholder_tag,
            "eval_success": eval_success,
            "mock": mock,
        }
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)


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
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self.prompt_repo = prompt_repo
        self.llm_client = llm_client
        self.result_writer = result_writer
        self.max_retries = max_retries

    def generate_once_with_retry(
        self,
        *,
        ctx: PromptContext,
        prompt_setting: str,
        prompt_variant: str,
    ) -> GenerationOutput:
        message, image_url = PromptGenerator.build_prompt(ctx, prompt_setting, prompt_variant)

        current_message = message
        current_cursor_id = ""
        session_ids: List[str] = []
        last_raw_text = ""
        last_parse_error: Optional[str] = None
        example_json = build_example_json_from_resource_ids(ctx.resource_ids)

        for _retry_round in range(1, self.max_retries + 1):
            response = self.llm_client.chat_completion(
                message=current_message,
                image_url=image_url,
                cursor_id=current_cursor_id,
            )

            data = response.get("data") or {}

            resp_id = str(data.get("id") or "").strip()

            if resp_id:
                session_ids.append(resp_id)
                current_cursor_id = resp_id

            chat_text = str(data.get("chat") or "")
            if not chat_text:
                chat_text = json.dumps(response, ensure_ascii=False)

            last_raw_text = chat_text

            try:
                parsed_obj = extract_json_object_from_text(chat_text)
                validate_resource_ids_exact_match(parsed_obj, ctx.resource_ids)

                parse_success = 1
                parse_error = None
                placeholder_tag = placeholder_tag_of_obj(parsed_obj)

                return GenerationOutput(
                    raw_response_text=chat_text,
                    parsed_input_json=sort_dict_by_keys(parsed_obj),
                    parse_success=parse_success,
                    parse_error=parse_error,
                    placeholder_tag=placeholder_tag,
                    session_ids=session_ids,
                )
            except Exception as e:
                last_parse_error = str(e)
                retry_prompt = format_retry_prompt(example_json, len(ctx.resource_ids))
                current_message = message + "\n\n" + retry_prompt

        return GenerationOutput(
            raw_response_text=last_raw_text,
            parsed_input_json=None,
            parse_success=0,
            parse_error=last_parse_error or "unknown parse error",
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
        result = self.generate_once_with_retry(
            ctx=ctx,
            prompt_setting=prompt_setting,
            prompt_variant=prompt_variant,
        )

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
            mock=0,
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
                active_set = set(active_subset)
                scenario: Dict[str, Any] = {}

                for rid in resource_ids:
                    if rid in active_set:
                        scenario[rid] = base_parsed_json.get(rid, "")
                    else:
                        scenario[rid] = ""

                placeholder_tag = placeholder_tag_of_obj(scenario)
                eval_success = None

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
                    mock=0,
                )

    def run_ablation_prompt(
        self,
        *,
        ctx: PromptContext,
        attempt_id: int = -1,
        invalid_index: int = -1,
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
                invalid_index=invalid_index,
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
) -> None:
    for i, app_id in enumerate(target_ids, start=1):
        print(f"[RQ1_VALID] {i}/{len(target_ids)} -> {app_id}")
        ctx = prompt_repo.get_context(VALID_TASK, app_id)

        for attempt_id in VALID_ATTEMPT_IDS:
            print(f"  [ATTEMPT] valid attempt_id={attempt_id}")

            engine.run_single_generation_and_store(
                experiment=EXPERIMENT_RQ1_VALID,
                ctx=ctx,
                prompt_setting=PROMPT_SETTING_EXTRACTED,
                prompt_variant=PROMPT_VARIANT_FULL,
                attempt_id=attempt_id,
                invalid_index=-1,
                tau=-1,
                combo_index=-1,
                auto_expand_multi_component=True,
            )
            engine.run_single_generation_and_store(
                experiment=EXPERIMENT_RQ1_VALID,
                ctx=ctx,
                prompt_setting=PROMPT_SETTING_XML,
                prompt_variant=PROMPT_VARIANT_XML,
                attempt_id=attempt_id,
                invalid_index=-1,
                tau=-1,
                combo_index=-1,
                auto_expand_multi_component=False,
            )
            engine.run_single_generation_and_store(
                experiment=EXPERIMENT_RQ1_VALID,
                ctx=ctx,
                prompt_setting=PROMPT_SETTING_VISION,
                prompt_variant=PROMPT_VARIANT_VISION,
                attempt_id=attempt_id,
                invalid_index=-1,
                tau=-1,
                combo_index=-1,
                auto_expand_multi_component=False,
            )

            engine.run_ablation_prompt(
                ctx=ctx,
                attempt_id=attempt_id,
                invalid_index=-1,
            )


def run_rq1_invalid(
    engine: GenerationEngine,
    prompt_repo: PromptRepository,
    target_ids: Sequence[str],
) -> None:
    for i, page_id in enumerate(target_ids, start=1):
        print(f"[RQ1_INVALID] {i}/{len(target_ids)} -> {page_id}")
        ctx = prompt_repo.get_context(INVALID_TASK, page_id)

        for invalid_index in INVALID_INDICES:
            print(f"  [INVALID_INDEX] invalid_index={invalid_index}")

            engine.run_single_generation_and_store(
                experiment=EXPERIMENT_RQ1_INVALID,
                ctx=ctx,
                prompt_setting=PROMPT_SETTING_EXTRACTED,
                prompt_variant=PROMPT_VARIANT_FULL,
                attempt_id=-1,
                invalid_index=invalid_index,
                tau=-1,
                combo_index=-1,
                auto_expand_multi_component=False,
            )
            engine.run_single_generation_and_store(
                experiment=EXPERIMENT_RQ1_INVALID,
                ctx=ctx,
                prompt_setting=PROMPT_SETTING_XML,
                prompt_variant=PROMPT_VARIANT_XML,
                attempt_id=-1,
                invalid_index=invalid_index,
                tau=-1,
                combo_index=-1,
                auto_expand_multi_component=False,
            )
            engine.run_single_generation_and_store(
                experiment=EXPERIMENT_RQ1_INVALID,
                ctx=ctx,
                prompt_setting=PROMPT_SETTING_VISION,
                prompt_variant=PROMPT_VARIANT_VISION,
                attempt_id=-1,
                invalid_index=invalid_index,
                tau=-1,
                combo_index=-1,
                auto_expand_multi_component=False,
            )

            engine.run_ablation_prompt(
                ctx=ctx,
                attempt_id=-1,
                invalid_index=invalid_index,
            )


# ============================================================
# ID loaders
# ============================================================

def list_all_valid_ids() -> List[str]:
    records = read_json_file(PPTR_PROMPTS_JSON)
    return [str(item["app_id"]) for item in records]


def list_all_invalid_ids() -> List[str]:
    records = read_json_file(BUG_PROMPTS_JSON)
    ids: List[str] = []
    for item in records:
        ids.append(safe_int_str(item["page_id"]))
    return ids


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
        help="Comma-separated app_ids or page_ids. Empty means all.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Model type for API payload.",
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
            timeout=60,
            verify_ssl=False,
        )
        writer = ResultWriter(conn)
        engine = GenerationEngine(
            prompt_repo=prompt_repo,
            llm_client=llm_client,
            result_writer=writer,
            max_retries=MAX_RETRIES,
        )

        if args.task == "rq1_valid":
            ids = [x.strip() for x in args.ids.split(",") if x.strip()] if args.ids.strip() else list_all_valid_ids()
            run_rq1_valid(engine, prompt_repo, ids)

        elif args.task == "rq1_invalid":
            ids = [x.strip() for x in args.ids.split(",") if x.strip()] if args.ids.strip() else list_all_invalid_ids()
            run_rq1_invalid(engine, prompt_repo, ids)

        elif args.task == "all":
            valid_ids = [x.strip() for x in args.ids.split(",") if x.strip()] if args.ids.strip() else list_all_valid_ids()
            invalid_ids = list_all_invalid_ids()
            run_rq1_valid(engine, prompt_repo, valid_ids)
            run_rq1_invalid(engine, prompt_repo, invalid_ids)

        conn.commit()
        elapsed = time.time() - start_time
        print(f"[DONE] elapsed={elapsed:.2f}s")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
