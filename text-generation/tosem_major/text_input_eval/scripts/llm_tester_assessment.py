from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pymysql
import requests
import urllib3
from pymysql.cursors import DictCursor

from tosem_major.text_input_eval.services.one_shot import (
    BUG_PROMPTS_JSON,
    INVALID_TASK,
    PPTR_PROMPTS_JSON,
    VALID_TASK,
    load_base64_text,
    normalize_case_id_for_invalid,
    parse_component_resource_ids_from_restrictive,
    read_json_file,
)


SOURCE_TABLE_DEFAULT = "t_llm_text_generation_curated"
CALL_TABLE_DEFAULT = "t_llm_tester_assessment_call"
SCORE_TABLE_DEFAULT = "t_llm_tester_assessment_score"
PROMPT_VERSION_DEFAULT = "llm_tester_must_v1"
API_URL_DEFAULT = "http://must.lab.help1.com.cn/api/chat/completions"
DEFAULT_JUDGE_MODEL = "gpt-5.4-2026-03-05"
SCORE_JUDGE_MODEL_DEFAULT = "llm-tester"

EXPERIMENT_RQ1_VALID = "rq1_valid"
EXPERIMENT_RQ1_INVALID = "rq1_invalid"

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
VALID_ATTEMPTS = (1, 2, 3)
INVALID_INDICES = tuple(range(1, 31))

PERSONAS = (
    "A mobile app QA tester focusing on whether inputs are practical for real app testing.",
    "A form-validation tester focusing on field semantics and common validation rules.",
    "A security-oriented tester focusing on malformed, adversarial, and injection-like inputs.",
    "A usability-oriented tester focusing on realistic values that match visible UI context.",
    "A regression tester focusing on stable and reproducible text-input behavior.",
    "An exploratory tester focusing on diverse edge cases and unexpected field interactions.",
    "A localization-sensitive tester focusing on language, locale, and format assumptions.",
    "A boundary-value tester focusing on length, range, empty, and special-character cases.",
    "A domain-aware tester focusing on app-specific labels, hints, and usage scenarios.",
    "A robustness tester focusing on inputs likely to expose fragile UI or validation code.",
    "A conservative QA reviewer focusing on whether generated values are immediately usable.",
    "A negative-testing specialist focusing on invalid inputs that stress validation logic.",
    "A mobile automation tester focusing on values that can be inserted and evaluated reliably.",
    "A product-quality tester focusing on whether values match user-facing app workflows.",
    "A data-quality tester focusing on completeness, correctness, and resource-id coverage.",
    "A bug-reproduction tester focusing on inputs likely to make observed bugs visible.",
    "A compatibility tester focusing on Android UI conventions and input-widget behavior.",
    "A test-design reviewer focusing on diversity and non-redundancy across candidate inputs.",
    "A field-format tester focusing on email, phone, date, number, password, and search formats.",
    "A senior QA lead balancing realism, bug-finding potential, and reproducibility.",
)

IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
BARE_JSON_RE = re.compile(r"(\{.*\})", re.DOTALL)


@dataclass(frozen=True)
class ContextRecord:
    task: str
    target_id: str
    case_id: str
    global_text: str
    component_text: str
    adjacent_text: str
    restrictive_text: str
    guiding_text: str
    resource_ids: List[str]
    image_64: str


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    model: str
    prompt_setting: str
    prompt_variant: str
    attempt_id: int
    curated_ids: List[int]
    payload: Dict[str, Any]


def log(message: str) -> None:
    print(message, flush=True)


def compact_log_text(value: Any, limit: int = 200) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def ensure_identifier(value: str) -> str:
    if not IDENT_RE.match(value):
        raise ValueError(f"Unsafe SQL identifier: {value}")
    return value


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


def ensure_conn_alive(conn) -> None:
    conn.ping(reconnect=True)


def parse_csv(value: str) -> List[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def expand_int_ranges(value: str, *, min_value: int, max_value: int) -> List[int]:
    out: List[int] = []
    for part in parse_csv(value):
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = int(start_s)
            end = int(end_s)
            step = 1 if start <= end else -1
            out.extend(range(start, end + step, step))
        else:
            out.append(int(part))
    deduped = []
    seen = set()
    for item in out:
        if item < min_value or item > max_value:
            raise ValueError(f"Value out of range: {item} not in [{min_value}, {max_value}]")
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def expand_case_ids(value: str, *, task: str) -> List[str]:
    items = parse_csv(value)
    if not items:
        if task == "valid":
            return [f"P{i:03d}" for i in range(1, 116)]
        if task == "invalid":
            return [f"B{i:03d}" for i in range(1, 38)]
        raise ValueError(f"Unsupported task: {task}")

    prefix = "P" if task == "valid" else "B"
    out: List[str] = []
    for item in items:
        token = item.strip().upper()
        if "-" in token:
            start_s, end_s = token.split("-", 1)
            start = int(start_s.lstrip("PB"))
            end = int(end_s.lstrip("PB"))
            step = 1 if start <= end else -1
            out.extend(f"{prefix}{i:03d}" for i in range(start, end + step, step))
        else:
            if token[0].isdigit():
                out.append(f"{prefix}{int(token):03d}")
            else:
                out.append(f"{prefix}{int(token[1:]):03d}")
    return out


def prompt_variant_for_setting(setting: str) -> str:
    try:
        return PROMPT_VARIANT_BY_SETTING[setting]
    except KeyError:
        raise ValueError(f"Unsupported prompt_setting: {setting}")


def load_valid_context_maps() -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    records = read_json_file(PPTR_PROMPTS_JSON)
    by_app_id: Dict[str, Dict[str, Any]] = {}
    case_to_app_id: Dict[str, str] = {}
    for idx, item in enumerate(records, start=1):
        app_id = str(item["app_id"])
        case_id = f"P{idx:03d}"
        by_app_id[app_id] = item
        case_to_app_id[case_id] = app_id
    return by_app_id, case_to_app_id


def load_invalid_context_map() -> Dict[str, Dict[str, Any]]:
    records = read_json_file(BUG_PROMPTS_JSON)
    out: Dict[str, Dict[str, Any]] = {}
    for item in records:
        case_id = normalize_case_id_for_invalid(item["page_id"])
        out[case_id] = item
    return out


def build_context(case_id: str, task: str) -> ContextRecord:
    if task == "valid":
        valid_by_app, case_to_app = load_valid_context_maps()
        app_id = case_to_app[case_id]
        row = valid_by_app[app_id]
        target_id = app_id
        task_name = VALID_TASK
    elif task == "invalid":
        invalid_by_case = load_invalid_context_map()
        row = invalid_by_case[case_id]
        target_id = str(int(case_id[1:]))
        task_name = INVALID_TASK
    else:
        raise ValueError(f"Unsupported task: {task}")

    restrictive = str(row["restrictive"])
    return ContextRecord(
        task=task_name,
        target_id=target_id,
        case_id=case_id,
        global_text=str(row["global"]),
        component_text=str(row["component"]),
        adjacent_text=str(row["adjacent"]),
        restrictive_text=restrictive,
        guiding_text=str(row["guiding"]),
        resource_ids=parse_component_resource_ids_from_restrictive(restrictive),
        image_64=load_base64_text(task_name, target_id),
    )


def stable_attempt(case_id: str, model: str, setting: str, seed: int) -> int:
    digest = hashlib.sha256(f"{seed}|{case_id}|{model}|{setting}".encode("utf-8")).hexdigest()
    return VALID_ATTEMPTS[int(digest[:8], 16) % len(VALID_ATTEMPTS)]


def normalize_json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return json.loads(value)
    return value


def truncate_for_prompt(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        if max_chars <= 0:
            return value
        if len(value) <= max_chars:
            return value
        return f"{value[:max_chars]}...[truncated; original_chars={len(value)}]"
    if isinstance(value, list):
        return [truncate_for_prompt(item, max_chars) for item in value]
    if isinstance(value, dict):
        return {str(key): truncate_for_prompt(item, max_chars) for key, item in value.items()}
    return value


def parsed_payload_from_row(row: Optional[Dict[str, Any]], max_value_chars: int) -> Dict[str, Any]:
    if row is None:
        return {
            "status": "missing_generation",
            "text_inputs": None,
        }
    parse_success = int(row.get("parse_success") or 0)
    parsed = row.get("parsed_input_json")
    if parse_success != 1 or parsed is None:
        return {
            "status": str(row.get("generation_status") or "not_parsed"),
            "text_inputs": None,
            "parse_error": str(row.get("parse_error") or "")[:500],
        }
    try:
        parsed_obj = normalize_json_value(parsed)
    except Exception as exc:
        return {
            "status": "json_decode_error",
            "text_inputs": None,
            "parse_error": f"{type(exc).__name__}: {exc}"[:500],
        }
    if not isinstance(parsed_obj, dict) or len(parsed_obj) == 0:
        return {
            "status": "empty_or_invalid_parsed_input_json",
            "text_inputs": None,
        }
    return {
        "status": "ok",
        "text_inputs": truncate_for_prompt(parsed_obj, max_value_chars),
    }


def fetch_valid_rows(conn, source_table: str, case_id: str, models: Sequence[str], settings: Sequence[str]) -> Dict[Tuple[str, str, int], Dict[str, Any]]:
    source_table = ensure_identifier(source_table)
    placeholders_models = ",".join(["%s"] * len(models))
    placeholders_settings = ",".join(["%s"] * len(settings))
    sql = f"""
        SELECT
            curated_id,
            canonical_model,
            prompt_setting,
            prompt_variant,
            attempt_id,
            parse_success,
            parsed_input_json,
            parse_error,
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
          AND attempt_id IN (1, 2, 3)
        ORDER BY curated_id DESC
    """
    params = [EXPERIMENT_RQ1_VALID, VALID_TASK, case_id, *models, *settings]
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    out: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    for row in rows:
        variant = prompt_variant_for_setting(str(row["prompt_setting"]))
        if str(row["prompt_variant"]) != variant:
            continue
        key = (str(row["canonical_model"]), str(row["prompt_setting"]), int(row["attempt_id"]))
        out.setdefault(key, row)
    return out


def fetch_invalid_rows(conn, source_table: str, case_id: str, models: Sequence[str], settings: Sequence[str]) -> Dict[Tuple[str, str, int], Dict[str, Any]]:
    source_table = ensure_identifier(source_table)
    placeholders_models = ",".join(["%s"] * len(models))
    placeholders_settings = ",".join(["%s"] * len(settings))
    sql = f"""
        SELECT
            curated_id,
            canonical_model,
            prompt_setting,
            prompt_variant,
            invalid_index,
            parse_success,
            parsed_input_json,
            parse_error,
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
          AND invalid_index BETWEEN 1 AND 30
        ORDER BY curated_id DESC
    """
    params = [EXPERIMENT_RQ1_INVALID, INVALID_TASK, case_id, *models, *settings]
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    out: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    for row in rows:
        variant = prompt_variant_for_setting(str(row["prompt_setting"]))
        if str(row["prompt_variant"]) != variant:
            continue
        key = (
            str(row["canonical_model"]),
            str(row["prompt_setting"]),
            int(row["invalid_index"]),
        )
        out.setdefault(key, row)
    return out


def build_valid_candidates(
        conn,
        source_table: str,
        case_id: str,
        models: Sequence[str],
        settings: Sequence[str],
        attempt_seed: int,
        fixed_attempt: int,
        max_value_chars: int,
) -> List[Candidate]:
    rows = fetch_valid_rows(conn, source_table, case_id, models, settings)
    candidates: List[Candidate] = []
    candidate_index = 0
    for model in models:
        for setting in settings:
            candidate_index += 1
            attempts: List[Dict[str, Any]] = []
            curated_ids: List[int] = []
            ok_count = 0
            for attempt_id in VALID_ATTEMPTS:
                row = rows.get((model, setting, attempt_id))
                if row:
                    curated_ids.append(int(row["curated_id"]))
                item = {
                    "attempt_id": attempt_id,
                    **parsed_payload_from_row(row, max_value_chars),
                }
                if item["status"] == "ok":
                    ok_count += 1
                attempts.append(item)
            payload = {
                "candidate_id": f"C{candidate_index:03d}",
                "num_attempts": len(attempts),
                "num_parse_success": ok_count,
                "attempts": attempts,
            }
            candidates.append(
                Candidate(
                    candidate_id=f"C{candidate_index:03d}",
                    model=model,
                    prompt_setting=setting,
                    prompt_variant=prompt_variant_for_setting(setting),
                    attempt_id=-1,
                    curated_ids=curated_ids,
                    payload=payload,
                )
            )
    return candidates


def build_invalid_candidates(
        conn,
        source_table: str,
        case_id: str,
        models: Sequence[str],
        settings: Sequence[str],
        max_value_chars: int,
) -> List[Candidate]:
    rows = fetch_invalid_rows(conn, source_table, case_id, models, settings)
    candidates: List[Candidate] = []
    candidate_index = 0
    for model in models:
        for setting in settings:
            candidate_index += 1
            input_sets: List[Dict[str, Any]] = []
            curated_ids: List[int] = []
            ok_count = 0
            for invalid_index in INVALID_INDICES:
                row = rows.get((model, setting, invalid_index))
                if row:
                    curated_ids.append(int(row["curated_id"]))
                item = {
                    "invalid_index": invalid_index,
                    **parsed_payload_from_row(row, max_value_chars),
                }
                if item["status"] == "ok":
                    ok_count += 1
                input_sets.append(item)
            payload = {
                "candidate_id": f"C{candidate_index:03d}",
                "num_invalid_inputs": len(input_sets),
                "num_parse_success": ok_count,
                "input_sets": input_sets,
            }
            candidates.append(
                Candidate(
                    candidate_id=f"C{candidate_index:03d}",
                    model=model,
                    prompt_setting=setting,
                    prompt_variant=prompt_variant_for_setting(setting),
                    attempt_id=-1,
                    curated_ids=curated_ids,
                    payload=payload,
                )
            )
    return candidates


def build_system_prompt(persona: str) -> str:
    return (
        f"You are {persona}\n"
        "You are judging Android text-input test data. Score strictly from 1 to 5.\n"
        "Return JSON only. Do not include Markdown.\n"
        "Do not reward candidates for coming from any particular model or prompt setting; "
        "the candidates are anonymized and should be judged only by their text-input quality."
    )


def task_instruction_for_assessment(assessment_task: str) -> str:
    if assessment_task == "valid":
        return (
            "Task: valid page pass-through assessment.\n"
            "Each candidate is a SET of 3 valid text-input generations for the same UI page. "
            "Give one overall score for the set. Assess whether the generated text inputs are "
            "context-aware, semantically appropriate for the visible UI, practical for passing "
            "through the page. Do not penalize repeated or similar values across the three attempts "
            "when the values are context-aware, semantically appropriate, and practical for page "
            "pass-through. Missing or malformed attempts should lower the overall score; give 1 if "
            "the set has no usable parsed text inputs."
        )
    if assessment_task == "invalid":
        return (
            "Task: invalid-input bug-testing assessment.\n"
            "Each candidate is a SET of 30 invalid text-input generations for the same bug case. "
            "Give one overall score for the set. Reward field-specific invalid values, "
            "boundary/malformed/adversarial values, and plausible bug-triggering potential. "
            "Do not penalize repeated or similar values across the 30 generations when the values "
            "are field-specific invalid inputs with plausible bug-triggering potential. Do not "
            "reward realistic valid values. Give 1 if the set is mostly missing, malformed, "
            "wrong-resource, or unrelated."
        )
    raise ValueError(f"Unsupported assessment_task: {assessment_task}")


def scoring_rubric_text() -> str:
    return (
        "Scoring rubric:\n"
        "5 = excellent and highly targeted for the task.\n"
        "4 = good with minor weaknesses.\n"
        "3 = acceptable but generic or partially weak.\n"
        "2 = poorly matched or weakly useful.\n"
        "1 = missing, malformed, wrong resource IDs, unusable, or unrelated."
    )


def response_format_text() -> str:
    return (
        "Return exactly this JSON shape:\n"
        "{\"scores\":[{\"candidate_id\":\"C001\",\"score\":1,\"reason\":\"brief reason\"}]}\n"
        "Include exactly one score object for every candidate_id listed below.\n"
        "Return JSON only. Do not include Markdown."
    )


def context_payload_for_prompt(ctx: ContextRecord) -> Dict[str, Any]:
    return {
        "case_id": ctx.case_id,
        "ui_context": {
            "global": ctx.global_text,
            "components": ctx.component_text,
            "adjacent_labels": ctx.adjacent_text,
            "required_resource_ids": ctx.resource_ids,
            "original_generation_format": ctx.restrictive_text,
        },
    }


def candidate_payload_for_prompt(candidates: Sequence[Candidate]) -> Dict[str, Any]:
    candidate_payloads = anonymized_candidate_payloads(candidates)
    expected_ids = [candidate.candidate_id for candidate in candidates]
    return {
        "candidate_ids": expected_ids,
        "candidates": candidate_payloads,
    }


def build_user_prompt(ctx: ContextRecord, assessment_task: str, candidates: Sequence[Candidate]) -> str:
    context_payload = {
        **context_payload_for_prompt(ctx),
        **candidate_payload_for_prompt(candidates),
    }
    return (
        f"{task_instruction_for_assessment(assessment_task)}\n\n"
        f"{scoring_rubric_text()}\n\n"
        f"{response_format_text()}\n\n"
        f"{json.dumps(context_payload, ensure_ascii=False, separators=(',', ':'))}"
    )


def build_session_context_prompt(ctx: ContextRecord, assessment_task: str, persona: str) -> str:
    return (
        f"{build_system_prompt(persona)}\n\n"
        f"{task_instruction_for_assessment(assessment_task)}\n\n"
        "You will receive candidate text inputs in the next message. "
        "For now, remember the UI screenshot and the following UI context.\n\n"
        f"{json.dumps(context_payload_for_prompt(ctx), ensure_ascii=False, separators=(',', ':'))}"
    )


def build_session_scoring_prompt(assessment_task: str, candidates: Sequence[Candidate]) -> str:
    return (
        f"{task_instruction_for_assessment(assessment_task)}\n\n"
        f"{scoring_rubric_text()}\n\n"
        f"{response_format_text()}\n\n"
        "Candidate metadata such as model names and prompt settings is intentionally hidden. "
        "Judge candidates only by their anonymized candidate_id and text inputs.\n\n"
        f"{json.dumps(candidate_payload_for_prompt(candidates), ensure_ascii=False, separators=(',', ':'))}"
    )


def is_auto_score_one_candidate(candidate: Candidate, assessment_task: str) -> bool:
    if assessment_task == "valid":
        return int(candidate.payload.get("num_parse_success") or 0) == 0
    if assessment_task == "invalid":
        return int(candidate.payload.get("num_parse_success") or 0) == 0
    raise ValueError(f"Unsupported assessment_task: {assessment_task}")


def auto_score_one_items(candidates: Sequence[Candidate]) -> List[Dict[str, Any]]:
    return [
        {
            "candidate_id": candidate.candidate_id,
            "score": 1,
            "reason": "No usable parsed_input_json is available for this candidate; assigned the minimum score without calling the judge model.",
        }
        for candidate in candidates
    ]


def anonymized_candidate_payloads(candidates: Sequence[Candidate]) -> List[Dict[str, Any]]:
    return [candidate.payload for candidate in candidates]


def normalize_judge_json_obj(obj: Any) -> Optional[Dict[str, Any]]:
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list):
        return {"scores": obj}
    return None


def extract_json_object(text: str) -> Dict[str, Any]:
    try:
        obj = json.loads(text)
        normalized = normalize_judge_json_obj(obj)
        if normalized is not None:
            return normalized
    except Exception:
        pass

    candidates: List[str] = []
    for match in JSON_BLOCK_RE.finditer(text):
        candidates.append(match.group(1))
    if not candidates:
        for match in BARE_JSON_RE.finditer(text):
            candidates.append(match.group(1))
    last_error: Optional[Exception] = None
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
            normalized = normalize_judge_json_obj(obj)
            if normalized is not None:
                return normalized
        except Exception as exc:
            last_error = exc

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[index:])
            normalized = normalize_judge_json_obj(obj)
            if normalized is not None:
                return normalized
        except Exception as exc:
            last_error = exc

    if last_error:
        raise ValueError(f"Cannot parse JSON object: {last_error}")
    raise ValueError("Cannot parse JSON object from judge response")


def validate_scores(obj: Dict[str, Any], expected_candidate_ids: Sequence[str]) -> List[Dict[str, Any]]:
    scores = obj.get("scores")
    if not isinstance(scores, list):
        raise ValueError("Judge response does not contain scores list")
    expected = set(expected_candidate_ids)
    seen = set()
    out: List[Dict[str, Any]] = []
    for item in scores:
        if not isinstance(item, dict):
            raise ValueError("Each score item must be an object")
        candidate_id = str(item.get("candidate_id") or "")
        if candidate_id not in expected:
            raise ValueError(f"Unexpected candidate_id in judge response: {candidate_id}")
        if candidate_id in seen:
            raise ValueError(f"Duplicate candidate_id in judge response: {candidate_id}")
        score = int(item.get("score"))
        if score < 1 or score > 5:
            raise ValueError(f"Score out of range for {candidate_id}: {score}")
        reason = str(item.get("reason") or "").strip()
        if not reason:
            raise ValueError(f"Missing reason for {candidate_id}")
        seen.add(candidate_id)
        out.append({"candidate_id": candidate_id, "score": score, "reason": reason})
    missing = sorted(expected - seen)
    if missing:
        raise ValueError(f"Missing candidate scores: {missing}")
    return out


class MustLabJudgeClient:
    def __init__(
            self,
            *,
            api_key: str,
            model_type: str,
            base_url: str,
            timeout: float,
            verify_ssl: bool = False,
    ) -> None:
        if not api_key:
            raise RuntimeError("MUST_LAB_API_TOKEN is empty. Export it before running the script.")
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
            *,
            message: str,
            image_64: Optional[str] = None,
            session_id: str = "",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "modelType": self.model_type,
            "sessionId": session_id,
            "message": message,
        }
        if image_64:
            payload["imageUrl"] = f"data:image/png;base64,{image_64}"

        response = self.session.post(
            self.base_url,
            json=payload,
            timeout=self.timeout,
            verify=self.verify_ssl,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def response_chat_text(response: Dict[str, Any]) -> str:
        data = response.get("data") if isinstance(response, dict) else None
        if isinstance(data, dict) and data.get("chat") is not None:
            return str(data.get("chat") or "")
        return json.dumps(response, ensure_ascii=False)

    @staticmethod
    def response_session_id(response: Dict[str, Any]) -> str:
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, dict):
            return ""
        return str(data.get("id") or data.get("sessionId") or "").strip()

    def judge_once(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            image_64: str,
    ) -> Tuple[str, Dict[str, Any]]:
        response = self.chat_completion(
            message=f"{system_prompt}\n\n{user_prompt}",
            image_64=image_64,
            session_id="",
        )
        return self.response_chat_text(response), {"api_response": response, "session_id": self.response_session_id(response)}

    def judge_with_session_context(
            self,
            *,
            context_prompt: str,
            scoring_prompt: str,
            image_64: str,
    ) -> Tuple[str, Dict[str, Any]]:
        context_response = self.chat_completion(
            message=context_prompt,
            image_64=image_64,
            session_id="",
        )
        session_id = self.response_session_id(context_response)
        if not session_id:
            raise RuntimeError(f"Context setup did not return session id: {context_response}")
        judge_response = self.chat_completion(
            message=scoring_prompt,
            image_64=None,
            session_id=session_id,
        )
        return self.response_chat_text(judge_response), {
            "context_response": context_response,
            "judge_response": judge_response,
            "setup_session_id": session_id,
            "judge_session_id": self.response_session_id(judge_response),
        }


def create_tables(conn, call_table: str, score_table: str) -> None:
    call_table = ensure_identifier(call_table)
    score_table = ensure_identifier(score_table)
    ensure_conn_alive(conn)
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {call_table} (
                call_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                assessment_task VARCHAR(16) NOT NULL,
                case_id VARCHAR(64) NOT NULL,
                tester_id VARCHAR(32) NOT NULL,
                tester_persona TEXT NOT NULL,
                judge_model VARCHAR(128) NOT NULL,
                prompt_version VARCHAR(64) NOT NULL,
                prompt_hash CHAR(64) NOT NULL,
                candidate_count INT NOT NULL,
                candidate_map_json JSON NOT NULL,
                request_text LONGTEXT NOT NULL,
                raw_response LONGTEXT NULL,
                usage_json JSON NULL,
                status VARCHAR(32) NOT NULL,
                error_message TEXT NULL,
                elapsed_ms BIGINT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                KEY idx_call_task_case (assessment_task, case_id, tester_id),
                KEY idx_call_status (status),
                KEY idx_call_prompt (judge_model, prompt_version)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """
        )
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {score_table} (
                score_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                call_id BIGINT NULL,
                assessment_task VARCHAR(16) NOT NULL,
                case_id VARCHAR(64) NOT NULL,
                task VARCHAR(32) NOT NULL,
                model VARCHAR(128) NOT NULL,
                prompt_setting VARCHAR(32) NOT NULL,
                prompt_variant VARCHAR(64) NOT NULL,
                tester_id VARCHAR(32) NOT NULL,
                tester_persona TEXT NOT NULL,
                judge_model VARCHAR(128) NOT NULL,
                prompt_version VARCHAR(64) NOT NULL,
                candidate_id VARCHAR(16) NOT NULL,
                attempt_id INT NOT NULL DEFAULT -1,
                candidate_count INT NOT NULL,
                candidate_curated_ids_json JSON NULL,
                score TINYINT NOT NULL,
                reason TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_assessment_score (
                    assessment_task,
                    case_id,
                    model,
                    prompt_setting,
                    prompt_variant,
                    tester_id,
                    judge_model,
                    prompt_version
                ),
                KEY idx_score_case (assessment_task, case_id),
                KEY idx_score_model_setting (model, prompt_setting),
                KEY idx_score_tester (tester_id),
                KEY idx_score_call (call_id)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """
        )
    conn.commit()


def existing_score_slots_for_candidates(
        conn,
        score_table: str,
        assessment_task: str,
        case_id: str,
        tester_id: str,
        prompt_version: str,
        candidates: Sequence[Candidate],
) -> set[Tuple[str, str, str]]:
    if not candidates:
        return set()
    score_table = ensure_identifier(score_table)
    ensure_conn_alive(conn)
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT model, prompt_setting, prompt_variant
            FROM {score_table}
            WHERE assessment_task = %s
              AND case_id = %s
              AND tester_id = %s
              AND prompt_version = %s
            """,
            [assessment_task, case_id, tester_id, prompt_version],
        )
        rows = cursor.fetchall()
    existing_slots = {
        (
            str(row["model"]),
            str(row["prompt_setting"]),
            str(row["prompt_variant"]),
        )
        for row in rows
    }
    candidate_slots = {
        (
            candidate.model,
            candidate.prompt_setting,
            candidate.prompt_variant,
        )
        for candidate in candidates
    }
    return existing_slots & candidate_slots


def insert_call_start(
        conn,
        call_table: str,
        *,
        assessment_task: str,
        case_id: str,
        tester_id: str,
        tester_persona: str,
        judge_model: str,
        prompt_version: str,
        prompt_hash: str,
        candidates: Sequence[Candidate],
        request_text: str,
) -> int:
    call_table = ensure_identifier(call_table)
    ensure_conn_alive(conn)
    candidate_map = [
        {
            "candidate_id": candidate.candidate_id,
            "model": candidate.model,
            "prompt_setting": candidate.prompt_setting,
            "prompt_variant": candidate.prompt_variant,
            "attempt_id": candidate.attempt_id,
            "curated_ids": candidate.curated_ids,
        }
        for candidate in candidates
    ]
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {call_table} (
                assessment_task,
                case_id,
                tester_id,
                tester_persona,
                judge_model,
                prompt_version,
                prompt_hash,
                candidate_count,
                candidate_map_json,
                request_text,
                status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CAST(%s AS JSON), %s, 'started')
            """,
            [
                assessment_task,
                case_id,
                tester_id,
                tester_persona,
                judge_model,
                prompt_version,
                prompt_hash,
                len(candidates),
                json.dumps(candidate_map, ensure_ascii=False),
                request_text,
            ],
        )
        call_id = int(cursor.lastrowid)
    conn.commit()
    return call_id


def update_call_done(
        conn,
        call_table: str,
        *,
        call_id: int,
        status: str,
        raw_response: Optional[str],
        usage: Optional[Dict[str, Any]],
        elapsed_ms: int,
        error_message: Optional[str],
) -> None:
    call_table = ensure_identifier(call_table)
    ensure_conn_alive(conn)
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE {call_table}
            SET status = %s,
                raw_response = %s,
                usage_json = CAST(%s AS JSON),
                elapsed_ms = %s,
                error_message = %s
            WHERE call_id = %s
            """,
            [
                status,
                raw_response,
                json.dumps(usage or {}, ensure_ascii=False),
                elapsed_ms,
                error_message,
                call_id,
            ],
        )
    conn.commit()


def upsert_scores(
        conn,
        score_table: str,
        *,
        call_id: int,
        assessment_task: str,
        case_id: str,
        task: str,
        tester_id: str,
        tester_persona: str,
        judge_model: str,
        prompt_version: str,
        candidates: Sequence[Candidate],
        scores: Sequence[Dict[str, Any]],
) -> None:
    score_table = ensure_identifier(score_table)
    ensure_conn_alive(conn)
    by_candidate = {candidate.candidate_id: candidate for candidate in candidates}
    rows = []
    for item in scores:
        candidate = by_candidate[str(item["candidate_id"])]
        rows.append(
            [
                call_id,
                assessment_task,
                case_id,
                task,
                candidate.model,
                candidate.prompt_setting,
                candidate.prompt_variant,
                tester_id,
                tester_persona,
                judge_model,
                prompt_version,
                candidate.candidate_id,
                candidate.attempt_id,
                len(candidate.curated_ids),
                json.dumps(candidate.curated_ids, ensure_ascii=False),
                int(item["score"]),
                str(item["reason"]),
            ]
        )
    sql = f"""
        INSERT INTO {score_table} (
            call_id,
            assessment_task,
            case_id,
            task,
            model,
            prompt_setting,
            prompt_variant,
            tester_id,
            tester_persona,
            judge_model,
            prompt_version,
            candidate_id,
            attempt_id,
            candidate_count,
            candidate_curated_ids_json,
            score,
            reason
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CAST(%s AS JSON), %s, %s
        )
        ON DUPLICATE KEY UPDATE
            call_id = VALUES(call_id),
            tester_persona = VALUES(tester_persona),
            candidate_id = VALUES(candidate_id),
            attempt_id = VALUES(attempt_id),
            candidate_count = VALUES(candidate_count),
            candidate_curated_ids_json = VALUES(candidate_curated_ids_json),
            score = VALUES(score),
            reason = VALUES(reason),
            updated_at = CURRENT_TIMESTAMP
    """
    with conn.cursor() as cursor:
        cursor.executemany(sql, rows)
    conn.commit()


def run_db_write_with_retry(
        conn,
        *,
        label: str,
        max_attempts: int,
        sleep_seconds: float,
        write_fn,
) -> None:
    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            ensure_conn_alive(conn)
            write_fn()
            return
        except pymysql.err.OperationalError as exc:
            last_error = exc
            try:
                conn.rollback()
            except Exception:
                pass
            log(
                f"[DB_RETRY] label={label} attempt={attempt}/{max_attempts} "
                f"error={type(exc).__name__}: {str(exc)[:300]}"
            )
            if attempt < max_attempts:
                time.sleep(sleep_seconds)
        except pymysql.err.InterfaceError as exc:
            last_error = exc
            try:
                conn.rollback()
            except Exception:
                pass
            log(
                f"[DB_RETRY] label={label} attempt={attempt}/{max_attempts} "
                f"error={type(exc).__name__}: {str(exc)[:300]}"
            )
            if attempt < max_attempts:
                time.sleep(sleep_seconds)
    if last_error:
        raise last_error


def prompt_hash(system_prompt: str, user_prompt: str) -> str:
    return hashlib.sha256((system_prompt + "\n---\n" + user_prompt).encode("utf-8")).hexdigest()


def chunked(items: Sequence[Candidate], batch_size: int) -> Iterable[List[Candidate]]:
    if batch_size <= 0:
        yield list(items)
        return
    for start in range(0, len(items), batch_size):
        yield list(items[start:start + batch_size])


def persona_for_tester(tester_num: int) -> Tuple[str, str]:
    tester_id = f"llm_tester_{tester_num:02d}"
    persona = PERSONAS[(tester_num - 1) % len(PERSONAS)]
    return tester_id, persona


def build_candidates_for_task(
        conn,
        *,
        task: str,
        source_table: str,
        case_id: str,
        models: Sequence[str],
        settings: Sequence[str],
        attempt_seed: int,
        fixed_attempt: int,
        max_value_chars: int,
) -> List[Candidate]:
    if task == "valid":
        return build_valid_candidates(
            conn,
            source_table,
            case_id,
            models,
            settings,
            attempt_seed,
            fixed_attempt,
            max_value_chars,
        )
    if task == "invalid":
        return build_invalid_candidates(
            conn,
            source_table,
            case_id,
            models,
            settings,
            max_value_chars,
        )
    raise ValueError(f"Unsupported task: {task}")


def run_one_call(
        *,
        conn,
        client: Optional[MustLabJudgeClient],
        args: argparse.Namespace,
        task: str,
        case_id: str,
        tester_num: int,
) -> bool:
    tester_id, persona = persona_for_tester(tester_num)
    ctx = build_context(case_id, task)
    candidates = build_candidates_for_task(
        conn,
        task=task,
        source_table=args.source_table,
        case_id=case_id,
        models=args.models,
        settings=args.settings,
        attempt_seed=args.valid_attempt_seed,
        fixed_attempt=args.valid_attempt,
        max_value_chars=args.invalid_max_value_chars if task == "invalid" else args.max_value_chars,
    )
    expected_count = len(candidates)
    existing_slots = existing_score_slots_for_candidates(
        conn,
        args.score_table,
        task,
        case_id,
        tester_id,
        args.prompt_version,
        candidates,
    )
    existing = len(existing_slots)
    if existing >= expected_count and not args.rerun:
        log(
            f"[SKIP] task={task} case_id={case_id} tester={tester_id} "
            f"existing_scores={existing}/{expected_count} skip_ignores_judge_model=1"
        )
        return False

    active_candidates = candidates if args.rerun else [
        candidate
        for candidate in candidates
        if (
            candidate.model,
            candidate.prompt_setting,
            candidate.prompt_variant,
        )
        not in existing_slots
    ]
    auto_candidates = [
        candidate
        for candidate in active_candidates
        if is_auto_score_one_candidate(candidate, task)
    ]
    judge_candidates = [
        candidate
        for candidate in active_candidates
        if not is_auto_score_one_candidate(candidate, task)
    ]

    system_prompt = build_system_prompt(persona)
    if task == "invalid" and args.invalid_judge_batch_size > 0:
        judge_batches = list(chunked(judge_candidates, args.invalid_judge_batch_size))
    else:
        judge_batches = [list(judge_candidates)] if judge_candidates else []

    if task == "invalid":
        ok_inputs = sum(
            int(c.payload.get("num_parse_success") or 0)
            for c in active_candidates
        )
        total_inputs = len(active_candidates) * len(INVALID_INDICES)
        status_hint = (
            f"candidate_groups={len(active_candidates)}/{expected_count} "
            f"existing={existing} parsed_invalid_inputs={ok_inputs}/{total_inputs} "
            f"invalid_batch_size={args.invalid_judge_batch_size} "
            f"invalid_max_value_chars={args.invalid_max_value_chars}"
        )
    else:
        ok_attempts = sum(
            int(c.payload.get("num_parse_success") or 0)
            for c in active_candidates
        )
        total_attempts = len(active_candidates) * len(VALID_ATTEMPTS)
        status_hint = (
            f"candidate_groups={len(active_candidates)}/{expected_count} "
            f"existing={existing} parsed_valid_attempts={ok_attempts}/{total_attempts}"
        )

    log(
        f"[CALL_START] task={task} case_id={case_id} tester={tester_id} "
        f"{status_hint} auto_score_1={len(auto_candidates)} "
        f"judge_candidates={len(judge_candidates)} judge_batches={len(judge_batches)}"
    )

    if args.dry_run:
        for batch_index, batch_candidates in enumerate(judge_batches, start=1):
            if args.use_session_context:
                user_prompt = build_session_scoring_prompt(task, batch_candidates)
            else:
                user_prompt = build_user_prompt(ctx, task, batch_candidates)
            candidate_range = f"{batch_candidates[0].candidate_id}-{batch_candidates[-1].candidate_id}" if batch_candidates else "none"
            log(
                f"[DRY_RUN_BATCH] task={task} case_id={case_id} tester={tester_id} "
                f"batch={batch_index}/{len(judge_batches)} candidates={candidate_range} "
                f"would_call_model={args.judge_model} prompt_chars={len(user_prompt)}"
            )
        if auto_candidates:
            log(
                f"[DRY_RUN_AUTO] task={task} case_id={case_id} tester={tester_id} "
                f"auto_score_1={len(auto_candidates)}"
            )
        return True

    any_changed = False

    if auto_candidates:
        auto_request_text = "AUTO_SCORE_1: no usable parsed_input_json is available for these candidates."
        auto_call_id = insert_call_start(
            conn,
            args.call_table,
            assessment_task=task,
            case_id=case_id,
            tester_id=tester_id,
            tester_persona=persona,
            judge_model=args.judge_model,
            prompt_version=args.prompt_version,
            prompt_hash=prompt_hash(system_prompt, auto_request_text),
            candidates=auto_candidates,
            request_text=auto_request_text,
        )
        started = time.perf_counter()
        scores = auto_score_one_items(auto_candidates)
        raw_response = json.dumps({"scores": scores, "mode": "auto_score_1"}, ensure_ascii=False)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        run_db_write_with_retry(
            conn,
            label=f"auto_score call_id={auto_call_id}",
            max_attempts=3,
            sleep_seconds=args.retry_sleep_seconds,
            write_fn=lambda: (
                upsert_scores(
                    conn,
                    args.score_table,
                    call_id=auto_call_id,
                    assessment_task=task,
                    case_id=case_id,
                    task=ctx.task,
                    tester_id=tester_id,
                    tester_persona=persona,
                    judge_model=args.score_judge_model,
                    prompt_version=args.prompt_version,
                    candidates=auto_candidates,
                    scores=scores,
                ),
                update_call_done(
                    conn,
                    args.call_table,
                    call_id=auto_call_id,
                    status="auto_scored",
                    raw_response=raw_response,
                    usage={},
                    elapsed_ms=elapsed_ms,
                    error_message=None,
                ),
            ),
        )
        any_changed = True
        log(
            f"[CALL_AUTO_DONE] task={task} case_id={case_id} tester={tester_id} "
            f"call_id={auto_call_id} auto_score_1={len(scores)} elapsed_ms={elapsed_ms}"
        )

    for batch_index, batch_candidates in enumerate(judge_batches, start=1):
        if not batch_candidates:
            continue
        if args.use_session_context:
            context_prompt = build_session_context_prompt(ctx, task, persona)
            user_prompt = build_session_scoring_prompt(task, batch_candidates)
            request_text = (
                f"{context_prompt}\n\n---SESSION_SCORING_MESSAGE---\n\n{user_prompt}"
            )
        else:
            context_prompt = ""
            user_prompt = build_user_prompt(ctx, task, batch_candidates)
            request_text = f"{system_prompt}\n\n{user_prompt}"
        digest = prompt_hash(system_prompt, request_text)
        candidate_range = f"{batch_candidates[0].candidate_id}-{batch_candidates[-1].candidate_id}"
        log(
            f"[CALL_BATCH_START] task={task} case_id={case_id} tester={tester_id} "
            f"batch={batch_index}/{len(judge_batches)} candidates={candidate_range} "
            f"judge_candidates={len(batch_candidates)} prompt_chars={len(user_prompt)}"
        )

        call_id = insert_call_start(
            conn,
            args.call_table,
            assessment_task=task,
            case_id=case_id,
            tester_id=tester_id,
            tester_persona=persona,
            judge_model=args.judge_model,
            prompt_version=args.prompt_version,
            prompt_hash=digest,
            candidates=batch_candidates,
            request_text=request_text,
        )

        last_error: Optional[Exception] = None
        raw_response: Optional[str] = None
        usage: Dict[str, Any] = {}
        started = time.perf_counter()
        batch_success = False
        for attempt in range(1, args.max_retries + 1):
            try:
                if client is None:
                    raise RuntimeError("MustLab client is not initialized")
                if args.use_session_context:
                    raw_response, usage = client.judge_with_session_context(
                        context_prompt=context_prompt,
                        scoring_prompt=user_prompt,
                        image_64=ctx.image_64,
                    )
                else:
                    raw_response, usage = client.judge_once(
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        image_64=ctx.image_64,
                    )
                parsed = extract_json_object(raw_response)
                judged_scores = validate_scores(
                    parsed,
                    [candidate.candidate_id for candidate in batch_candidates],
                )
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                run_db_write_with_retry(
                    conn,
                    label=f"success call_id={call_id}",
                    max_attempts=3,
                    sleep_seconds=args.retry_sleep_seconds,
                    write_fn=lambda: (
                        upsert_scores(
                            conn,
                            args.score_table,
                            call_id=call_id,
                            assessment_task=task,
                            case_id=case_id,
                            task=ctx.task,
                            tester_id=tester_id,
                            tester_persona=persona,
                            judge_model=args.score_judge_model,
                            prompt_version=args.prompt_version,
                            candidates=batch_candidates,
                            scores=judged_scores,
                        ),
                        update_call_done(
                            conn,
                            args.call_table,
                            call_id=call_id,
                            status="success",
                            raw_response=raw_response,
                            usage=usage,
                            elapsed_ms=elapsed_ms,
                            error_message=None,
                        ),
                    ),
                )
                score_counts = ",".join(
                    f"{score}={sum(1 for item in judged_scores if int(item['score']) == score)}"
                    for score in range(1, 6)
                )
                log(
                    f"[CALL_BATCH_DONE] task={task} case_id={case_id} tester={tester_id} "
                    f"call_id={call_id} batch={batch_index}/{len(judge_batches)} "
                    f"scores={len(judged_scores)} {score_counts} elapsed_ms={elapsed_ms}"
                )
                any_changed = True
                batch_success = True
                if args.sleep_seconds > 0:
                    time.sleep(args.sleep_seconds)
                break
            except Exception as exc:
                last_error = exc
                response_hint = ""
                if raw_response:
                    response_hint = f" response_excerpt={compact_log_text(raw_response)}"
                log(
                    f"[CALL_RETRY] task={task} case_id={case_id} tester={tester_id} "
                    f"call_id={call_id} batch={batch_index}/{len(judge_batches)} "
                    f"attempt={attempt}/{args.max_retries} "
                    f"error={type(exc).__name__}: {str(exc)[:300]}{response_hint}"
                )
                if attempt < args.max_retries:
                    time.sleep(args.retry_sleep_seconds)

        if not batch_success:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            run_db_write_with_retry(
                conn,
                label=f"failed call_id={call_id}",
                max_attempts=3,
                sleep_seconds=args.retry_sleep_seconds,
                write_fn=lambda: update_call_done(
                    conn,
                    args.call_table,
                    call_id=call_id,
                    status="failed",
                    raw_response=raw_response,
                    usage=usage,
                    elapsed_ms=elapsed_ms,
                    error_message=f"{type(last_error).__name__}: {last_error}" if last_error else "unknown",
                ),
            )
            if args.fail_fast and last_error:
                raise last_error

    log(
        f"[CALL_DONE] task={task} case_id={case_id} tester={tester_id} "
        f"auto_score_1={len(auto_candidates)} judge_batches={len(judge_batches)} changed={int(any_changed)}"
    )
    return any_changed

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run GPT-based tester assessment over curated one-shot RQ1 data. "
            "Valid uses one call per page/tester; invalid uses one or more batched calls per bug/tester."
        )
    )
    parser.add_argument("--source-table", default=SOURCE_TABLE_DEFAULT)
    parser.add_argument("--call-table", default=CALL_TABLE_DEFAULT)
    parser.add_argument("--score-table", default=SCORE_TABLE_DEFAULT)
    parser.add_argument("--tasks", default="valid,invalid", help="valid, invalid, or both.")
    parser.add_argument("--case-ids", default="", help="Comma-separated case IDs/ranges, e.g. P001-P010 or B001,B009.")
    parser.add_argument("--models", default=",".join(FINAL_MODELS))
    parser.add_argument("--settings", default=",".join(PROMPT_SETTINGS))
    parser.add_argument("--tester-ids", default="1", help="Tester IDs/ranges, e.g. 1 or 1-20.")
    parser.add_argument("--judge-model", default=os.getenv("MUST_LAB_JUDGE_MODEL", DEFAULT_JUDGE_MODEL))
    parser.add_argument(
        "--score-judge-model",
        default=os.getenv("LLM_TESTER_SCORE_JUDGE_MODEL", SCORE_JUDGE_MODEL_DEFAULT),
        help=(
            "Logical judge label stored in the score table. "
            "Skip/resume ignores the actual --judge-model and uses the logical assessment slot instead."
        ),
    )
    parser.add_argument("--api-url", default=os.getenv("MUST_LAB_API_URL", API_URL_DEFAULT))
    parser.add_argument("--api-token", default=os.getenv("MUST_LAB_API_TOKEN", ""))
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--verify-ssl", action="store_true")
    parser.add_argument(
        "--use-session-context",
        action="store_true",
        help="First send image/UI context to create a session, then score anonymized candidates with that session id.",
    )
    parser.add_argument("--prompt-version", default=PROMPT_VERSION_DEFAULT)
    parser.add_argument("--valid-attempt-seed", type=int, default=20260525)
    parser.add_argument("--valid-attempt", type=int, default=0, help="0 means deterministic seeded choice; otherwise use 1, 2, or 3.")
    parser.add_argument(
        "--max-value-chars",
        type=int,
        default=200,
        help="Truncate each generated text value in the judge prompt; original length is preserved in the marker.",
    )
    parser.add_argument(
        "--invalid-max-value-chars",
        type=int,
        default=0,
        help="Truncate each invalid generated text value in the judge prompt. 0 means no truncation.",
    )
    parser.add_argument(
        "--invalid-judge-batch-size",
        type=int,
        default=2,
        help="For invalid assessment, score this many candidate groups per judge call. 0 disables batching.",
    )
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-sleep-seconds", type=float, default=5.0)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--limit", type=int, default=0, help="Maximum GPT calls in this process. 0 means no limit.")
    parser.add_argument("--rerun", action="store_true", help="Overwrite existing score rows for the same analytical slot.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--create-tables-only", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    args = parser.parse_args()

    tasks = parse_csv(args.tasks)
    if not tasks:
        raise ValueError("--tasks cannot be empty")
    for task in tasks:
        if task not in {"valid", "invalid"}:
            raise ValueError(f"Unsupported task: {task}")

    args.models = parse_csv(args.models)
    args.settings = parse_csv(args.settings)
    for setting in args.settings:
        prompt_variant_for_setting(setting)
    args.tester_nums = expand_int_ranges(args.tester_ids, min_value=1, max_value=20)
    if args.valid_attempt not in (0, 1, 2, 3):
        raise ValueError("--valid-attempt must be 0, 1, 2, or 3")

    conn = get_conn()
    try:
        create_tables(conn, args.call_table, args.score_table)
        if args.create_tables_only:
            log(
                f"[CREATE_TABLES_DONE] call_table={args.call_table} score_table={args.score_table}"
            )
            return

        client: Optional[MustLabJudgeClient] = None
        if not args.dry_run:
            client = MustLabJudgeClient(
                api_key=args.api_token,
                model_type=args.judge_model,
                base_url=args.api_url,
                timeout=args.timeout,
                verify_ssl=args.verify_ssl,
            )

        total_planned = 0
        plan: List[Tuple[str, str, int]] = []
        for task in tasks:
            case_ids = expand_case_ids(args.case_ids, task=task)
            for tester_num in args.tester_nums:
                for case_id in case_ids:
                    plan.append((task, case_id, tester_num))
        total_planned = len(plan)
        if args.limit:
            plan = plan[:args.limit]

        log(
            f"[ASSESS_START] tasks={','.join(tasks)} testers={args.tester_ids} "
            f"planned_calls={len(plan)}/{total_planned} judge_model={args.judge_model} "
            f"score_judge_model={args.score_judge_model} "
            f"source_table={args.source_table} score_table={args.score_table}"
        )

        done = 0
        attempted = 0
        started = time.perf_counter()
        for task, case_id, tester_num in plan:
            attempted += 1
            changed = run_one_call(
                conn=conn,
                client=client,
                args=args,
                task=task,
                case_id=case_id,
                tester_num=tester_num,
            )
            if changed:
                done += 1
            elapsed = time.perf_counter() - started
            log(
                f"[PROGRESS] attempted={attempted}/{len(plan)} called_or_dry={done} "
                f"elapsed={elapsed:.1f}s"
            )

        elapsed = time.perf_counter() - started
        log(
            f"[ASSESS_DONE] attempted={attempted} called_or_dry={done} "
            f"elapsed={elapsed:.1f}s"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("[INTERRUPTED]")
        sys.exit(130)
