from __future__ import annotations

import argparse
import itertools
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pymysql
from pymysql.cursors import DictCursor


PROJECT_ROOT = Path(
    "/Users/cuichenhui/Documents/local-repositories/"
    "llm-empirical-study-workspace/llm-empirical-study"
)
CACHE_ROOT = PROJECT_ROOT / "tosem_major" / "apk_info" / "cache"
PPTR_PROMPTS_JSON = CACHE_ROOT / "pptr_prompts.json"

SOURCE_TABLE_DEFAULT = "t_llm_text_generation"
TARGET_TABLE_DEFAULT = "t_llm_text_generation_curated"

VALID_TASK = "valid_pptr"
INVALID_TASK = "invalid_bug"

EXPERIMENT_RQ1_VALID = "rq1_valid"
EXPERIMENT_RQ1_INVALID = "rq1_invalid"
EXPERIMENT_ABLATION_PROMPT = "ablation_prompt"
EXPERIMENT_ABLATION_MULTI_COMPONENT = "ablation_multi_component"
EXPERIMENT_RQ2_VALID = "rq2_valid_feedback"
EXPERIMENT_RQ2_INVALID = "rq2_invalid_feedback"

FEEDBACK_MODE_NONE = "none"

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

MODEL_ALIAS_TO_CANONICAL = {
    "baidu/ernie-5.0-thinking-preview": "baidu/ernie-5.0-thinking-preview",
    "ernie-5.0-thinking-preview": "baidu/ernie-5.0-thinking-preview",
    "claude-opus-4-6": "claude-opus-4-6",
    "Doubao-Seed-2.0-pro": "Doubao-Seed-2.0-pro",
    "gemini-3.1-pro-preview": "gemini-3.1-pro-preview",
    "gpt-5.4-2026-03-05": "gpt-5.4-2026-03-05",
    "grok-4.20-reasoning": "grok-4.20-reasoning",
    "kimi/kimi-k2.5": "kimi/kimi-k2.5",
    "kimi-k2.5": "kimi/kimi-k2.5",
    "mistralai/mistral-large-2512": "mistralai/mistral-large-2512",
    "mistral-large-2512": "mistralai/mistral-large-2512",
    "mistral/mistral-large-2512": "mistralai/mistral-large-2512",
    "qwen3.5-plus": "qwen3.5-plus",
}

RQ1_EXPERIMENTS = (
    EXPERIMENT_RQ1_VALID,
    EXPERIMENT_RQ1_INVALID,
    EXPERIMENT_ABLATION_PROMPT,
    EXPERIMENT_ABLATION_MULTI_COMPONENT,
)
RQ2_EXPERIMENTS = (EXPERIMENT_RQ2_VALID, EXPERIMENT_RQ2_INVALID)

JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
BARE_JSON_RE = re.compile(r"(\{.*\})", re.DOTALL)
IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
GENERATED_VALUE_RE = re.compile(r"\bgenerated_value\b", re.IGNORECASE)


def progress(message: str) -> None:
    print(f"[curate] {message}", flush=True)


@dataclass(frozen=True)
class Slot:
    canonical_model: str
    experiment: str
    case_id: str
    task: str
    prompt_setting: str
    prompt_variant: str
    feedback_mode: str
    attempt_id: int
    invalid_index: int
    tau: int
    combo_index: int


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


def ensure_identifier(value: str) -> str:
    if not IDENT_RE.match(value):
        raise ValueError(f"Unsafe SQL identifier: {value}")
    return value


def read_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_resource_ids(restrictive: str) -> List[str]:
    match = JSON_BLOCK_RE.search(restrictive) or BARE_JSON_RE.search(restrictive)
    if not match:
        raise ValueError("Cannot parse resource-id JSON from restrictive prompt")
    obj = json.loads(match.group(1))
    if not isinstance(obj, dict) or not obj:
        raise ValueError("Restrictive prompt JSON must be a non-empty object")
    return list(obj.keys())


def load_valid_case_resource_ids() -> Dict[str, List[str]]:
    records = read_json_file(PPTR_PROMPTS_JSON)
    out: Dict[str, List[str]] = {}
    for idx, item in enumerate(records, start=1):
        case_id = f"P{idx:03d}"
        out[case_id] = parse_resource_ids(str(item["restrictive"]))
    if len(out) != 115:
        raise RuntimeError(f"Expected 115 valid cases, found {len(out)}")
    return out


def canonical_model(model: str) -> Optional[str]:
    return MODEL_ALIAS_TO_CANONICAL.get(model)


def slot_from_row(row: Dict[str, Any]) -> Optional[Slot]:
    canonical = canonical_model(str(row["model"]))
    if canonical is None:
        return None
    return Slot(
        canonical_model=canonical,
        experiment=str(row["experiment"]),
        case_id=str(row["case_id"]),
        task=str(row["task"]),
        prompt_setting=str(row["prompt_setting"]),
        prompt_variant=str(row["prompt_variant"]),
        feedback_mode=str(row["feedback_mode"]),
        attempt_id=int(row["attempt_id"]),
        invalid_index=int(row["invalid_index"]),
        tau=int(row["tau"]),
        combo_index=int(row["combo_index"]),
    )


def parse_json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        text = value.strip()
        if text == "" or text.lower() == "null":
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return value
    return value


def json_for_db(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text == "" or text.lower() == "null":
            return None
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def is_empty_session(value: Any) -> bool:
    parsed = parse_json_value(value)
    if parsed is None:
        return True
    if isinstance(parsed, list):
        return len(parsed) == 0
    if isinstance(parsed, str):
        text = parsed.strip()
        return text == "" or text.lower() == "null" or text == "[]"
    return False


def is_usable_parsed_json(row: Optional[Dict[str, Any]]) -> bool:
    if not row:
        return False
    if int(row.get("parse_success") or 0) != 1:
        return False
    parsed = parse_json_value(row.get("parsed_input_json"))
    if parsed is None:
        return False
    if isinstance(parsed, dict):
        return len(parsed) > 0
    if isinstance(parsed, list):
        return len(parsed) > 0
    return False


def normalize_parsed_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    parsed = parse_json_value(row.get("parsed_input_json"))
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError("Expected non-empty parsed_input_json object")
    return parsed


def placeholder_tag_of_obj(obj: Dict[str, Any]) -> int:
    return int(any(GENERATED_VALUE_RE.search(str(value or "")) for value in obj.values()))


def raw_text(row: Optional[Dict[str, Any]]) -> str:
    if not row:
        return ""
    return str(row.get("raw_response") or row.get("raw_response_probe") or "")


def classify_generation(row: Optional[Dict[str, Any]], *, is_multi_component: bool = False) -> str:
    if row is None:
        return "missing"
    raw = raw_text(row)
    raw_lower = raw.lower()
    if "prompt contains" in raw_lower and "too large for model" in raw_lower:
        return "api_context_limit"
    if "reject_no_credit" in raw_lower or "http 402" in raw_lower:
        return "api_no_credit"
    if '"code": "900001"' in raw or '"code":"900001"' in raw or "system error" in raw_lower:
        return "api_system_error"
    if is_usable_parsed_json(row):
        return "parse_success"
    if not is_multi_component and is_empty_session(row.get("session_ids_json")):
        return "api_empty_session"
    if int(row.get("parse_success") or 0) == 1:
        return "parse_empty_json"
    return "parse_failure"


def curation_status_for_generation(generation_status: str, *, missing: bool = False) -> Tuple[str, str]:
    if missing:
        return "placeholder_missing", "missing_slot"
    if generation_status in ("parse_success", "derived_from_extracted"):
        return "selected_usable", "ready_for_eval"
    if generation_status == "api_context_limit":
        return "selected_terminal_failure", "terminal_failure"
    return "selected_incomplete_generation", "incomplete_generation"


def refresh_record_status(record: Dict[str, Any]) -> None:
    generation_status = classify_generation(
        record,
        is_multi_component=record.get("experiment") == EXPERIMENT_ABLATION_MULTI_COMPONENT,
    )
    record["generation_status"] = generation_status
    if record.get("curation_status") == "rq2_actual_row":
        if generation_status == "api_context_limit":
            record["completion_status"] = "terminal_failure"
        elif generation_status == "parse_success":
            record["completion_status"] = "ready_for_eval"
        else:
            record["completion_status"] = "incomplete_generation"
        return

    curation_status, completion_status = curation_status_for_generation(generation_status)
    record["curation_status"] = curation_status
    record["completion_status"] = completion_status


def choose_source_row(candidates: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None
    usable = [row for row in candidates if is_usable_parsed_json(row)]
    pool = usable if usable else list(candidates)
    return max(pool, key=lambda row: int(row["id"]))


def combinations_for_tau(resource_ids: Sequence[str], tau: int) -> Iterable[Tuple[str, ...]]:
    return itertools.combinations(resource_ids, tau)


def expected_rq1_slots(case_resource_ids: Dict[str, List[str]]) -> List[Slot]:
    slots: List[Slot] = []
    valid_settings = (
        ("extracted", "full"),
        ("xml", "xml"),
        ("vision", "vision"),
    )
    ablation_prompt_variants = (
        "no_global",
        "no_component",
        "no_adjacent",
        "no_guiding",
    )

    for model in FINAL_MODELS:
        for case_num in range(1, 116):
            case_id = f"P{case_num:03d}"
            for attempt_id in (1, 2, 3):
                for prompt_setting, prompt_variant in valid_settings:
                    slots.append(
                        Slot(
                            model,
                            EXPERIMENT_RQ1_VALID,
                            case_id,
                            VALID_TASK,
                            prompt_setting,
                            prompt_variant,
                            FEEDBACK_MODE_NONE,
                            attempt_id,
                            -1,
                            -1,
                            -1,
                        )
                    )
                for prompt_variant in ablation_prompt_variants:
                    slots.append(
                        Slot(
                            model,
                            EXPERIMENT_ABLATION_PROMPT,
                            case_id,
                            VALID_TASK,
                            "extracted",
                            prompt_variant,
                            FEEDBACK_MODE_NONE,
                            attempt_id,
                            -1,
                            -1,
                            -1,
                        )
                    )
                resource_ids = case_resource_ids[case_id]
                if len(resource_ids) < 2:
                    continue
                for tau in range(0, len(resource_ids) + 1):
                    for combo_index, _ in enumerate(combinations_for_tau(resource_ids, tau), start=1):
                        slots.append(
                            Slot(
                                model,
                                EXPERIMENT_ABLATION_MULTI_COMPONENT,
                                case_id,
                                VALID_TASK,
                                "extracted",
                                "full",
                                FEEDBACK_MODE_NONE,
                                attempt_id,
                                -1,
                                tau,
                                combo_index,
                            )
                        )

        for bug_num in range(1, 38):
            case_id = f"B{bug_num:03d}"
            for invalid_index in range(1, 31):
                for prompt_setting, prompt_variant in valid_settings:
                    slots.append(
                        Slot(
                            model,
                            EXPERIMENT_RQ1_INVALID,
                            case_id,
                            INVALID_TASK,
                            prompt_setting,
                            prompt_variant,
                            FEEDBACK_MODE_NONE,
                            -1,
                            invalid_index,
                            -1,
                            -1,
                        )
                    )
    return slots


def fetch_rows(conn, source_table: str, experiments: Sequence[str], models: Sequence[str]) -> List[Dict[str, Any]]:
    placeholders_models = ", ".join(["%s"] * len(models))
    sql = f"""
        SELECT
            id,
            experiment,
            case_id,
            task,
            model,
            prompt_setting,
            prompt_variant,
            feedback_mode,
            attempt_id,
            invalid_index,
            session_ids_json,
            NULL AS raw_response,
            parsed_input_json,
            parse_success,
            parse_error,
            placeholder_tag,
            eval_success,
            tau,
            combo_index,
            elapsed_ms
        FROM {source_table}
        WHERE experiment = %s
          AND model IN ({placeholders_models})
    """
    rows: List[Dict[str, Any]] = []
    with conn.cursor() as cursor:
        for experiment in experiments:
            progress(f"fetch_source_rows_start experiment={experiment}")
            cursor.execute(sql, [experiment] + list(models))
            experiment_rows = list(cursor.fetchall())
            progress(f"fetch_source_rows_done experiment={experiment} rows={len(experiment_rows)}")
            rows.extend(experiment_rows)
    return rows


def hydrate_raw_response_probes(
    conn,
    source_table: str,
    records: Sequence[Dict[str, Any]],
    batch_size: int,
) -> None:
    source_ids = sorted({
        int(record["source_id"])
        for record in records
        if record.get("source_id") is not None
        and record.get("completion_status") != "ready_for_eval"
    })
    if not source_ids:
        progress("hydrate_raw_response_probes skipped source_ids=0")
        return

    progress(f"hydrate_raw_response_probes_start source_ids={len(source_ids)} batch_size={batch_size}")
    probe_by_id: Dict[int, str] = {}
    with conn.cursor() as cursor:
        for start in range(0, len(source_ids), batch_size):
            chunk = source_ids[start:start + batch_size]
            placeholders = ", ".join(["%s"] * len(chunk))
            cursor.execute(
                f"""
                    SELECT id, LEFT(raw_response, 8192) AS raw_response_probe
                    FROM {source_table}
                    WHERE id IN ({placeholders})
                """,
                chunk,
            )
            for row in cursor.fetchall():
                probe_by_id[int(row["id"])] = row["raw_response_probe"] or ""
            progress(
                "hydrate_raw_response_probes_progress "
                f"fetched={min(start + len(chunk), len(source_ids))}/{len(source_ids)}"
            )

    for record in records:
        source_id = record.get("source_id")
        if source_id is not None and int(source_id) in probe_by_id:
            record["raw_response_probe"] = probe_by_id[int(source_id)]
            refresh_record_status(record)
    progress(f"hydrate_raw_response_probes_done hydrated={len(probe_by_id)}")


def hydrate_raw_responses(
    conn,
    source_table: str,
    records: Sequence[Dict[str, Any]],
    batch_size: int,
) -> None:
    source_ids = sorted({
        int(record["source_id"])
        for record in records
        if record.get("source_id") is not None and record.get("raw_response") is None
    })
    if not source_ids:
        progress("hydrate_raw_responses skipped source_ids=0")
        return

    progress(f"hydrate_raw_responses_start source_ids={len(source_ids)} batch_size={batch_size}")
    raw_by_id: Dict[int, str] = {}
    with conn.cursor() as cursor:
        for start in range(0, len(source_ids), batch_size):
            chunk = source_ids[start:start + batch_size]
            placeholders = ", ".join(["%s"] * len(chunk))
            cursor.execute(
                f"SELECT id, raw_response FROM {source_table} WHERE id IN ({placeholders})",
                chunk,
            )
            for row in cursor.fetchall():
                raw_by_id[int(row["id"])] = row["raw_response"]
            progress(
                "hydrate_raw_responses_progress "
                f"fetched={min(start + len(chunk), len(source_ids))}/{len(source_ids)}"
            )

    for record in records:
        source_id = record.get("source_id")
        if source_id is not None and record.get("raw_response") is None:
            record["raw_response"] = raw_by_id.get(int(source_id))
    progress(f"hydrate_raw_responses_done hydrated={len(raw_by_id)}")


def build_candidate_groups(rows: Sequence[Dict[str, Any]]) -> Dict[Slot, List[Dict[str, Any]]]:
    grouped: Dict[Slot, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        slot = slot_from_row(row)
        if slot is not None:
            grouped[slot].append(row)
    progress(f"candidate_groups_built slots={len(grouped)} source_rows={len(rows)}")
    return grouped


def copy_fields_from_row(row: Optional[Dict[str, Any]], slot: Slot) -> Dict[str, Any]:
    if row is None:
        return {
            "id": None,
            "source_id": None,
            "derived_from_source_id": None,
            "experiment": slot.experiment,
            "case_id": slot.case_id,
            "task": slot.task,
            "model": slot.canonical_model,
            "canonical_model": slot.canonical_model,
            "exact_model": None,
            "prompt_setting": slot.prompt_setting,
            "prompt_variant": slot.prompt_variant,
            "feedback_mode": slot.feedback_mode,
            "attempt_id": slot.attempt_id,
            "invalid_index": slot.invalid_index,
            "session_ids_json": None,
            "raw_response": None,
            "parsed_input_json": None,
            "parse_success": None,
            "parse_error": None,
            "placeholder_tag": None,
            "eval_success": None,
            "tau": slot.tau,
            "combo_index": slot.combo_index,
            "elapsed_ms": None,
        }

    source_id = int(row["id"])
    return {
        "id": source_id,
        "source_id": source_id,
        "derived_from_source_id": None,
        "experiment": row["experiment"],
        "case_id": row["case_id"],
        "task": row["task"],
        "model": slot.canonical_model,
        "canonical_model": slot.canonical_model,
        "exact_model": row["model"],
        "prompt_setting": row["prompt_setting"],
        "prompt_variant": row["prompt_variant"],
        "feedback_mode": row["feedback_mode"],
        "attempt_id": row["attempt_id"],
        "invalid_index": row["invalid_index"],
        "session_ids_json": json_for_db(row.get("session_ids_json")),
        "raw_response": row.get("raw_response"),
        "parsed_input_json": json_for_db(row.get("parsed_input_json")),
        "parse_success": row.get("parse_success"),
        "parse_error": row.get("parse_error"),
        "placeholder_tag": row.get("placeholder_tag"),
        "eval_success": row.get("eval_success"),
        "tau": row["tau"],
        "combo_index": row["combo_index"],
        "elapsed_ms": row.get("elapsed_ms"),
    }


def make_curated_source_record(row: Optional[Dict[str, Any]], slot: Slot) -> Dict[str, Any]:
    record = copy_fields_from_row(row, slot)
    generation_status = classify_generation(
        row,
        is_multi_component=slot.experiment == EXPERIMENT_ABLATION_MULTI_COMPONENT,
    )
    missing = row is None
    curation_status, completion_status = curation_status_for_generation(
        generation_status,
        missing=missing,
    )
    record.update(
        {
            "curation_status": curation_status,
            "completion_status": completion_status,
            "generation_status": generation_status,
            "selection_reason": "missing_slot" if missing else "selected_from_source",
        }
    )
    return record


def make_derived_multi_component_record(
    *,
    slot: Slot,
    base_record: Dict[str, Any],
    resource_ids: Sequence[str],
) -> Dict[str, Any]:
    base_json = parse_json_value(base_record["parsed_input_json"])
    if not isinstance(base_json, dict) or not base_json:
        raise ValueError("Base extracted record is not usable")

    active_subset = list(combinations_for_tau(resource_ids, slot.tau))[slot.combo_index - 1]
    active_set = set(active_subset)
    scenario = {
        rid: base_json.get(rid, "") if rid in active_set else ""
        for rid in resource_ids
    }
    raw_response = json.dumps(
        {
            "source": "auto_expand_multi_component_from_curated_extracted",
            "tau": slot.tau,
            "combo_index": slot.combo_index,
            "active_ids": list(active_subset),
            "scenario": scenario,
        },
        ensure_ascii=False,
    )

    return {
        "id": None,
        "source_id": None,
        "derived_from_source_id": base_record["source_id"],
        "experiment": slot.experiment,
        "case_id": slot.case_id,
        "task": slot.task,
        "model": slot.canonical_model,
        "canonical_model": slot.canonical_model,
        "exact_model": base_record["exact_model"],
        "prompt_setting": slot.prompt_setting,
        "prompt_variant": slot.prompt_variant,
        "feedback_mode": slot.feedback_mode,
        "attempt_id": slot.attempt_id,
        "invalid_index": slot.invalid_index,
        "session_ids_json": "[]",
        "raw_response": raw_response,
        "parsed_input_json": json_for_db(scenario),
        "parse_success": 1,
        "parse_error": None,
        "placeholder_tag": placeholder_tag_of_obj(scenario),
        "eval_success": None,
        "tau": slot.tau,
        "combo_index": slot.combo_index,
        "elapsed_ms": 0,
        "curation_status": "selected_usable",
        "completion_status": "ready_for_eval",
        "generation_status": "derived_from_extracted",
        "selection_reason": "derived_from_curated_extracted",
    }


def make_missing_derived_record(slot: Slot) -> Dict[str, Any]:
    record = copy_fields_from_row(None, slot)
    record.update(
        {
            "curation_status": "missing_derived_from_extracted",
            "completion_status": "missing_slot",
            "generation_status": "missing_derived_from_extracted",
            "selection_reason": "missing_extracted_source",
        }
    )
    return record


def curate_rq1(rows: Sequence[Dict[str, Any]], case_resource_ids: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    progress(f"curate_rq1_start source_rows={len(rows)}")
    groups = build_candidate_groups(rows)
    expected_slots = expected_rq1_slots(case_resource_ids)
    if len(expected_slots) != 67365:
        raise RuntimeError(f"Expected 67365 RQ1 slots, built {len(expected_slots)}")
    progress(f"curate_rq1_expected_slots slots={len(expected_slots)}")

    curated_by_slot: Dict[Slot, Dict[str, Any]] = {}
    multi_component_slots: List[Slot] = []

    for index, slot in enumerate(expected_slots, start=1):
        if slot.experiment == EXPERIMENT_ABLATION_MULTI_COMPONENT:
            multi_component_slots.append(slot)
            continue
        chosen = choose_source_row(groups.get(slot, []))
        curated_by_slot[slot] = make_curated_source_record(chosen, slot)
        if index % 10000 == 0:
            progress(f"curate_rq1_base_progress processed={index}/{len(expected_slots)}")

    progress(
        "curate_rq1_base_done "
        f"base_slots={len(curated_by_slot)} multi_component_slots={len(multi_component_slots)}"
    )

    existing_multi = 0
    derived_multi = 0
    failed_source_multi = 0
    missing_multi = 0
    for index, slot in enumerate(multi_component_slots, start=1):
        candidates = groups.get(slot, [])
        chosen = choose_source_row(candidates)
        if chosen is not None and is_usable_parsed_json(chosen):
            curated_by_slot[slot] = make_curated_source_record(chosen, slot)
            existing_multi += 1
            if index % 5000 == 0:
                progress(
                    "curate_rq1_multi_component_progress "
                    f"processed={index}/{len(multi_component_slots)}"
                )
            continue

        base_slot = Slot(
            canonical_model=slot.canonical_model,
            experiment=EXPERIMENT_RQ1_VALID,
            case_id=slot.case_id,
            task=VALID_TASK,
            prompt_setting="extracted",
            prompt_variant="full",
            feedback_mode=FEEDBACK_MODE_NONE,
            attempt_id=slot.attempt_id,
            invalid_index=-1,
            tau=-1,
            combo_index=-1,
        )
        base_record = curated_by_slot.get(base_slot)
        if base_record and base_record["completion_status"] == "ready_for_eval":
            curated_by_slot[slot] = make_derived_multi_component_record(
                slot=slot,
                base_record=base_record,
                resource_ids=case_resource_ids[slot.case_id],
            )
            derived_multi += 1
        elif chosen is not None:
            curated_by_slot[slot] = make_curated_source_record(chosen, slot)
            failed_source_multi += 1
        else:
            curated_by_slot[slot] = make_missing_derived_record(slot)
            missing_multi += 1
        if index % 5000 == 0:
            progress(
                "curate_rq1_multi_component_progress "
                f"processed={index}/{len(multi_component_slots)}"
            )

    progress(
        "curate_rq1_multi_component_done "
        f"existing={existing_multi} derived={derived_multi} "
        f"failed_source={failed_source_multi} missing_derived={missing_multi}"
    )
    progress(f"curate_rq1_done curated_rows={len(curated_by_slot)}")
    return [curated_by_slot[slot] for slot in expected_slots]


def curate_rq2(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    progress(f"curate_rq2_start source_rows={len(rows)}")
    curated: List[Dict[str, Any]] = []
    for row in rows:
        canonical = canonical_model(str(row["model"]))
        if canonical not in FINAL_MODELS or canonical != row["model"]:
            continue
        slot = slot_from_row(row)
        if slot is None:
            continue
        record = copy_fields_from_row(row, slot)
        generation_status = classify_generation(row)
        if generation_status == "api_context_limit":
            completion_status = "terminal_failure"
        elif generation_status == "parse_success":
            completion_status = "ready_for_eval"
        else:
            completion_status = "incomplete_generation"
        record.update(
            {
                "curation_status": "rq2_actual_row",
                "completion_status": completion_status,
                "generation_status": generation_status,
                "selection_reason": "rq2_final_model_row",
            }
        )
        curated.append(record)
    progress(f"curate_rq2_done curated_rows={len(curated)}")
    return curated


def create_target_table(conn, target_table: str) -> None:
    progress(f"create_target_table_start table={target_table}")
    sql = f"""
        CREATE TABLE {target_table} (
            curated_id BIGINT AUTO_INCREMENT PRIMARY KEY,
            id BIGINT NULL,
            source_id BIGINT NULL,
            derived_from_source_id BIGINT NULL,
            experiment VARCHAR(64) NOT NULL,
            case_id VARCHAR(64) NOT NULL,
            task VARCHAR(32) NOT NULL,
            model VARCHAR(128) NOT NULL,
            canonical_model VARCHAR(128) NOT NULL,
            exact_model VARCHAR(128) NULL,
            prompt_setting VARCHAR(32) NOT NULL,
            prompt_variant VARCHAR(64) NOT NULL,
            feedback_mode VARCHAR(32) NOT NULL,
            attempt_id INT NOT NULL,
            invalid_index INT NOT NULL,
            session_ids_json JSON NULL,
            raw_response LONGTEXT NULL,
            parsed_input_json JSON NULL,
            parse_success TINYINT(1) NULL,
            parse_error TEXT NULL,
            placeholder_tag TINYINT NULL,
            eval_success TINYINT(1) NULL,
            tau INT NOT NULL,
            combo_index INT NOT NULL,
            elapsed_ms BIGINT NULL,
            curation_status VARCHAR(64) NOT NULL,
            completion_status VARCHAR(64) NOT NULL,
            generation_status VARCHAR(64) NOT NULL,
            selection_reason VARCHAR(128) NOT NULL,
            KEY idx_curated_main (
                experiment,
                task,
                model,
                prompt_setting,
                prompt_variant,
                case_id
            ),
            KEY idx_curated_slot (
                model,
                experiment,
                case_id,
                task,
                prompt_setting,
                prompt_variant,
                feedback_mode,
                attempt_id,
                invalid_index,
                tau,
                combo_index
            ),
            KEY idx_curated_status (
                completion_status,
                generation_status,
                curation_status
            ),
            KEY idx_curated_source_id (source_id),
            KEY idx_curated_exact_model (exact_model)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """
    with conn.cursor() as cursor:
        cursor.execute(sql)
    progress(f"create_target_table_done table={target_table}")


def insert_records(conn, target_table: str, records: Sequence[Dict[str, Any]], batch_size: int) -> None:
    columns = (
        "id",
        "source_id",
        "derived_from_source_id",
        "experiment",
        "case_id",
        "task",
        "model",
        "canonical_model",
        "exact_model",
        "prompt_setting",
        "prompt_variant",
        "feedback_mode",
        "attempt_id",
        "invalid_index",
        "session_ids_json",
        "raw_response",
        "parsed_input_json",
        "parse_success",
        "parse_error",
        "placeholder_tag",
        "eval_success",
        "tau",
        "combo_index",
        "elapsed_ms",
        "curation_status",
        "completion_status",
        "generation_status",
        "selection_reason",
    )
    placeholders = ", ".join([f"%({column})s" for column in columns])
    column_sql = ", ".join(columns)
    sql = f"INSERT INTO {target_table} ({column_sql}) VALUES ({placeholders})"

    progress(
        f"insert_records_start table={target_table} rows={len(records)} batch_size={batch_size}"
    )
    with conn.cursor() as cursor:
        for start in range(0, len(records), batch_size):
            batch = records[start:start + batch_size]
            cursor.executemany(sql, batch)
            progress(
                "insert_records_progress "
                f"inserted={min(start + len(batch), len(records))}/{len(records)}"
            )
    progress(f"insert_records_done table={target_table} rows={len(records)}")


def copy_raw_responses_server_side(
    conn,
    source_table: str,
    target_table: str,
    batch_size: int,
) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
                SELECT
                    MIN(curated_id) AS min_id,
                    MAX(curated_id) AS max_id,
                    COUNT(*) AS row_count
                FROM {target_table}
                WHERE source_id IS NOT NULL
                  AND raw_response IS NULL
            """
        )
        bounds = cursor.fetchone()

    total = int((bounds or {}).get("row_count") or 0)
    if total == 0:
        progress("copy_raw_responses_server_side skipped rows=0")
        return

    min_id = int(bounds["min_id"])
    max_id = int(bounds["max_id"])
    progress(
        "copy_raw_responses_server_side_start "
        f"rows={total} curated_id_range={min_id}-{max_id} batch_size={batch_size}"
    )

    processed_range = 0
    updated_rows = 0
    with conn.cursor() as cursor:
        for start in range(min_id, max_id + 1, batch_size):
            end = min(start + batch_size - 1, max_id)
            cursor.execute(
                f"""
                    UPDATE {target_table} AS target
                    JOIN {source_table} AS source
                      ON target.source_id = source.id
                    SET target.raw_response = source.raw_response
                    WHERE target.curated_id BETWEEN %s AND %s
                      AND target.source_id IS NOT NULL
                      AND target.raw_response IS NULL
                """,
                (start, end),
            )
            updated_rows += int(cursor.rowcount)
            processed_range = end - min_id + 1
            conn.commit()
            progress(
                "copy_raw_responses_server_side_progress "
                f"curated_id={end}/{max_id} range_processed={processed_range} "
                f"updated_rows={updated_rows}/{total}"
            )

    progress(
        "copy_raw_responses_server_side_done "
        f"updated_rows={updated_rows}/{total}"
    )


def summarize(records: Sequence[Dict[str, Any]]) -> Dict[Tuple[str, str], int]:
    summary: Dict[Tuple[str, str], int] = defaultdict(int)
    for record in records:
        summary[(str(record["experiment"]), str(record["completion_status"]))] += 1
    return dict(summary)


def print_summary(records: Sequence[Dict[str, Any]]) -> None:
    progress(f"summary curated_rows={len(records)}")
    for (experiment, completion_status), count in sorted(summarize(records).items()):
        print(f"{experiment}\t{completion_status}\t{count}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a curated TOSEM text-generation table for RQ1/RQ2 analysis."
    )
    parser.add_argument("--source-table", default=SOURCE_TABLE_DEFAULT)
    parser.add_argument("--target-table", default=TARGET_TABLE_DEFAULT)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually create and populate the target table. Without this, only prints a dry-run summary.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Drop target table first. Required when --execute and target table already exists.",
    )
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()

    source_table = ensure_identifier(args.source_table)
    target_table = ensure_identifier(args.target_table)

    progress(
        "start "
        f"source_table={source_table} target_table={target_table} "
        f"execute={int(args.execute)} replace={int(args.replace)} batch_size={args.batch_size}"
    )
    progress("load_valid_case_resource_ids_start")
    case_resource_ids = load_valid_case_resource_ids()
    progress(f"load_valid_case_resource_ids_done cases={len(case_resource_ids)}")
    progress("connect_db_start")
    conn = get_conn()
    progress("connect_db_done")
    try:
        all_alias_models = tuple(MODEL_ALIAS_TO_CANONICAL.keys())
        rq1_rows = fetch_rows(conn, source_table, RQ1_EXPERIMENTS, all_alias_models)
        progress(f"fetch_rq1_done rows={len(rq1_rows)}")
        rq2_rows = fetch_rows(conn, source_table, RQ2_EXPERIMENTS, FINAL_MODELS)
        progress(f"fetch_rq2_done rows={len(rq2_rows)}")

        curated_records = curate_rq1(rq1_rows, case_resource_ids)
        curated_records.extend(curate_rq2(rq2_rows))
        progress(f"curation_done total_rows={len(curated_records)}")
        hydrate_raw_response_probes(conn, source_table, curated_records, args.batch_size)
        print_summary(curated_records)

        if not args.execute:
            progress("dry_run=1; pass --execute to create/populate the table")
            return

        with conn.cursor() as cursor:
            if args.replace:
                progress(f"drop_target_table_start table={target_table}")
                cursor.execute(f"DROP TABLE IF EXISTS {target_table}")
                progress(f"drop_target_table_done table={target_table}")
            progress(f"check_target_table_start table={target_table}")
            cursor.execute("SHOW TABLES LIKE %s", (target_table,))
            if cursor.fetchone():
                raise RuntimeError(
                    f"Target table {target_table} already exists. "
                    "Use --replace to drop it first."
                )
            progress(f"check_target_table_done table={target_table} exists=0")

        create_target_table(conn, target_table)
        insert_records(conn, target_table, curated_records, args.batch_size)
        progress("commit_insert_records_start")
        conn.commit()
        progress("commit_insert_records_done")
        copy_raw_responses_server_side(conn, source_table, target_table, args.batch_size)
        progress(f"created_table={target_table}")
    except Exception:
        progress("error_rollback_start")
        conn.rollback()
        progress("error_rollback_done")
        raise
    finally:
        conn.close()
        progress("db_closed")


if __name__ == "__main__":
    main()
