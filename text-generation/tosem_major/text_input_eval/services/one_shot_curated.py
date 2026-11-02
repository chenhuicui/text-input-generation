from __future__ import annotations

import argparse
import json
import time
from typing import Any, Dict, List, Optional, Sequence

from . import one_shot as base


CURATED_TABLE = "t_llm_text_generation_curated"


class CuratedResultWriter(base.ResultWriter):
    """Update curated slots; derived multi-component rows may be inserted if absent."""

    def __init__(self, conn, *, force: bool = False, table: str = CURATED_TABLE) -> None:
        super().__init__(conn)
        self.force = force
        self.table = table
        self.last_skip_reason: Optional[str] = None

    @staticmethod
    def _slot_params(
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
    ) -> Dict[str, Any]:
        return {
            "experiment": experiment,
            "case_id": base.clean_surrogates(case_id),
            "task": base.clean_surrogates(task),
            "model": base.clean_surrogates(model),
            "prompt_setting": base.clean_surrogates(prompt_setting),
            "prompt_variant": base.clean_surrogates(prompt_variant),
            "feedback_mode": base.clean_surrogates(feedback_mode),
            "attempt_id": attempt_id,
            "invalid_index": invalid_index,
            "tau": tau,
            "combo_index": combo_index,
        }

    def lookup_curated_id(self, params: Dict[str, Any]) -> Optional[int]:
        sql = f"""
            SELECT curated_id
            FROM {self.table}
            WHERE experiment = %(experiment)s
              AND case_id = %(case_id)s
              AND task = %(task)s
              AND model = %(model)s
              AND canonical_model = %(model)s
              AND prompt_setting = %(prompt_setting)s
              AND prompt_variant = %(prompt_variant)s
              AND feedback_mode = %(feedback_mode)s
              AND attempt_id = %(attempt_id)s
              AND invalid_index = %(invalid_index)s
              AND tau = %(tau)s
              AND combo_index = %(combo_index)s
            ORDER BY curated_id DESC
            LIMIT 2
        """
        self.ensure_conn_alive()
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()

        if not rows:
            return None
        if len(rows) > 1:
            raise RuntimeError(
                "Duplicate curated slot: "
                f"model={params['model']} experiment={params['experiment']} "
                f"case_id={params['case_id']} setting={params['prompt_setting']}/"
                f"{params['prompt_variant']} attempt_id={params['attempt_id']} "
                f"invalid_index={params['invalid_index']} tau={params['tau']} "
                f"combo_index={params['combo_index']}"
            )
        return int(rows[0]["curated_id"])

    def find_curated_id(self, params: Dict[str, Any]) -> int:
        curated_id = self.lookup_curated_id(params)
        if curated_id is None:
            raise RuntimeError(
                "Missing curated slot: "
                f"model={params['model']} experiment={params['experiment']} "
                f"case_id={params['case_id']} setting={params['prompt_setting']}/"
                f"{params['prompt_variant']} attempt_id={params['attempt_id']} "
                f"invalid_index={params['invalid_index']} tau={params['tau']} "
                f"combo_index={params['combo_index']}"
            )
        return curated_id

    def delete_exact_generation_record(self, **_: Any) -> int:
        return 0

    def delete_multi_component_records_for_attempt(self, **_: Any) -> int:
        return 0

    @staticmethod
    def raw_response_requires_rerun(raw_response: Any) -> bool:
        text = str(raw_response or "").strip()
        if not text:
            return True

        try:
            payload = json.loads(text)
        except Exception:
            return False

        if not isinstance(payload, dict):
            return False
        return payload.get("success") is False

    @staticmethod
    def is_context_limit_failure(
            *,
            raw_response: Any,
            parse_error: Any,
            generation_status: Any,
    ) -> bool:
        if str(generation_status or "").strip() == "api_context_limit":
            return True
        text = f"{raw_response or ''}\n{parse_error or ''}".lower()
        return (
            "prompt contains" in text
            and (
                "too large for model" in text
                or "maximum context length" in text
                or "context length" in text
            )
        )

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
        self.last_skip_reason = None
        if experiment == base.EXPERIMENT_ABLATION_MULTI_COMPONENT:
            return False

        params = self._slot_params(
            experiment=experiment,
            case_id=case_id,
            task=task,
            model=model,
            prompt_setting=prompt_setting,
            prompt_variant=prompt_variant,
            feedback_mode=feedback_mode,
            attempt_id=attempt_id,
            invalid_index=invalid_index,
            tau=tau,
            combo_index=combo_index,
        )
        if self.force:
            sql = f"""
                SELECT
                    JSON_LENGTH(session_ids_json) AS session_len,
                    raw_response,
                    parse_error,
                    generation_status
                FROM {self.table}
                WHERE experiment = %(experiment)s
                  AND case_id = %(case_id)s
                  AND task = %(task)s
                  AND model = %(model)s
                  AND canonical_model = %(model)s
                  AND prompt_setting = %(prompt_setting)s
                  AND prompt_variant = %(prompt_variant)s
                  AND feedback_mode = %(feedback_mode)s
                  AND attempt_id = %(attempt_id)s
                  AND invalid_index = %(invalid_index)s
                  AND tau = %(tau)s
                  AND combo_index = %(combo_index)s
                LIMIT 1
            """
            self.ensure_conn_alive()
            with self.conn.cursor() as cursor:
                cursor.execute(sql, params)
                row = cursor.fetchone()

            if not row:
                return False

            session_len = int(row.get("session_len") or 0)
            if session_len > 0:
                self.last_skip_reason = "session_ids_json_non_empty"
                return True

            if self.is_context_limit_failure(
                    raw_response=row.get("raw_response"),
                    parse_error=row.get("parse_error"),
                    generation_status=row.get("generation_status"),
            ):
                self.last_skip_reason = "api_context_limit"
                return True

            if not self.raw_response_requires_rerun(row.get("raw_response")):
                self.last_skip_reason = "raw_response_is_not_failed_api_wrapper"
                return True

            return False

        sql = f"""
            SELECT 1
            FROM {self.table}
            WHERE experiment = %(experiment)s
              AND case_id = %(case_id)s
              AND task = %(task)s
              AND model = %(model)s
              AND canonical_model = %(model)s
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
        self.ensure_conn_alive()
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        if row is not None:
            self.last_skip_reason = "already_has_parsed_input_or_session"
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
        params = self._slot_params(
            experiment=experiment,
            case_id=case_id,
            task=task,
            model=model,
            prompt_setting=prompt_setting,
            prompt_variant=prompt_variant,
            feedback_mode=feedback_mode,
            attempt_id=attempt_id,
            invalid_index=invalid_index,
            tau=tau,
            combo_index=combo_index,
        )
        curated_id = self.lookup_curated_id(params)
        if curated_id is None and experiment != base.EXPERIMENT_ABLATION_MULTI_COMPONENT:
            curated_id = self.find_curated_id(params)
        curation_status, completion_status, generation_status = self.status_for_result(
            parse_success=parse_success,
            parsed_input_json=parsed_input_json,
            raw_response=raw_response,
            parse_error=parse_error,
        )

        params.update(
            {
                "curated_id": curated_id,
                "id": None,
                "source_id": None,
                "derived_from_source_id": None,
                "exact_model": base.clean_surrogates(model),
                "session_ids_json": json.dumps(
                    base.clean_surrogates(session_ids_json),
                    ensure_ascii=False,
                )
                if session_ids_json is not None
                else None,
                "raw_response": base.clean_surrogates(raw_response),
                "parsed_input_json": json.dumps(
                    base.clean_surrogates(parsed_input_json),
                    ensure_ascii=False,
                )
                if parsed_input_json is not None
                else None,
                "parse_success": parse_success,
                "parse_error": base.clean_surrogates(parse_error),
                "placeholder_tag": placeholder_tag,
                "eval_success": eval_success,
                "elapsed_ms": elapsed_ms,
                "curation_status": curation_status,
                "completion_status": completion_status,
                "generation_status": generation_status,
                "selection_reason": "rerun_update_curated",
            }
        )
        if curated_id is None:
            sql = f"""
                INSERT INTO {self.table} (
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
                    %(model)s,
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
                UPDATE {self.table}
                SET id = NULL,
                    source_id = NULL,
                    derived_from_source_id = NULL,
                    exact_model = %(exact_model)s,
                    session_ids_json = %(session_ids_json)s,
                    raw_response = %(raw_response)s,
                    parsed_input_json = %(parsed_input_json)s,
                    parse_success = %(parse_success)s,
                    parse_error = %(parse_error)s,
                    placeholder_tag = %(placeholder_tag)s,
                    eval_success = %(eval_success)s,
                    elapsed_ms = %(elapsed_ms)s,
                    curation_status = %(curation_status)s,
                    completion_status = %(completion_status)s,
                    generation_status = %(generation_status)s,
                    selection_reason = %(selection_reason)s
                WHERE curated_id = %(curated_id)s
            """
        self.ensure_conn_alive()
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)


def parse_ablation_variants(variants_text: str) -> List[str]:
    valid = {
        base.PROMPT_VARIANT_NO_GLOBAL,
        base.PROMPT_VARIANT_NO_COMPONENT,
        base.PROMPT_VARIANT_NO_ADJACENT,
        base.PROMPT_VARIANT_NO_GUIDING,
    }
    variants = [item.strip() for item in variants_text.split(",") if item.strip()]
    if not variants:
        raise ValueError("--variants cannot be empty")
    unknown = sorted(set(variants) - valid)
    if unknown:
        raise ValueError(f"Unknown ablation variants: {unknown}. Expected: {sorted(valid)}")
    return variants


def run_ablation_prompt(
        engine: base.GenerationEngine,
        prompt_repo: base.PromptRepository,
        target_ids: Sequence[str],
        model: str,
        attempt_ids: Sequence[int],
        variants: Sequence[str],
) -> None:
    for i, app_id in enumerate(target_ids, start=1):
        print(f"[{model}][ABLATION_PROMPT] {i}/{len(target_ids)} -> {app_id}")
        ctx = prompt_repo.get_context(base.VALID_TASK, app_id)

        for attempt_id in attempt_ids:
            print(f"  [{model}][ATTEMPT] ablation_prompt attempt_id={attempt_id}")
            for variant in variants:
                engine.run_single_generation_and_store(
                    experiment=base.EXPERIMENT_ABLATION_PROMPT,
                    ctx=ctx,
                    prompt_setting=base.PROMPT_SETTING_EXTRACTED,
                    prompt_variant=variant,
                    attempt_id=attempt_id,
                    invalid_index=-1,
                    tau=-1,
                    combo_index=-1,
                    auto_expand_multi_component=False,
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--task",
        required=True,
        choices=["rq1_valid", "rq1_invalid", "ablation_prompt", "all"],
        help="Which batch to run against the curated table.",
    )
    parser.add_argument(
        "--ids",
        default="",
        help=(
            "Comma-separated ids. For valid/ablation, supports app_id, numeric index, or Pxxx. "
            "For invalid, supports numeric id or Bxxx. Empty means all unless --range is set."
        ),
    )
    parser.add_argument("--model", default=base.DEFAULT_MODEL, help="Model type for API payload.")
    parser.add_argument(
        "--range",
        default="",
        help="Closed numeric range START,END. Used only when --ids is empty, e.g., --range 9,37.",
    )
    parser.add_argument(
        "--settings",
        default="extracted,xml,vision",
        help="Comma-separated prompt settings for rq1_valid/rq1_invalid: extracted,xml,vision.",
    )
    parser.add_argument(
        "--variants",
        default="no_global,no_component,no_adjacent,no_guiding",
        help="Comma-separated ablation_prompt variants.",
    )
    parser.add_argument(
        "--attempts",
        default="",
        help="Valid-task attempt ids. Supports comma values and ranges like 1,3 or 1-3. Empty means 1-3.",
    )
    parser.add_argument(
        "--invalid-indices",
        default="",
        help="Invalid-task indices. Supports comma values and ranges like 1,30 or 20-30. Empty means 1-30.",
    )
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="Force rerun selected slots. Existing rows are updated; derived multi-component rows are inserted only if absent.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Alias for --rerun.",
    )
    parser.add_argument(
        "--skip-ablation-prompt",
        action="store_true",
        help="For rq1_valid, do not run ablation_prompt variants.",
    )
    args = parser.parse_args()

    api_key = base.get_api_key()
    conn = base.get_conn()
    start_time = time.time()

    try:
        prompt_repo = base.PromptRepository()
        llm_client = base.LLMClient(
            api_key=api_key,
            model_type=args.model,
            base_url=base.API_URL,
            timeout=500,
            verify_ssl=False,
        )
        writer = CuratedResultWriter(conn, force=args.rerun or args.force)
        engine = base.GenerationEngine(
            prompt_repo=prompt_repo,
            llm_client=llm_client,
            result_writer=writer,
        )

        settings = base.parse_settings(args.settings)
        attempt_ids = base.parse_numeric_selection(
            args.attempts,
            min_value=1,
            max_value=3,
            label="attempt_id",
        )
        invalid_indices = base.parse_numeric_selection(
            args.invalid_indices,
            min_value=1,
            max_value=30,
            label="invalid_index",
        )
        variants = parse_ablation_variants(args.variants)

        print(
            f"[CURATED_MODE] table={CURATED_TABLE} force={args.rerun or args.force} "
            "write_mode=update_existing_rows"
        )

        if args.task == "rq1_valid":
            ids = base.selected_valid_ids(args.ids, args.range)
            base.run_rq1_valid(
                engine,
                prompt_repo,
                ids,
                model=args.model,
                rerun=False,
                settings=settings,
                attempt_ids=attempt_ids,
                run_ablation_prompt=not args.skip_ablation_prompt,
            )
        elif args.task == "rq1_invalid":
            ids = base.selected_invalid_ids(args.ids, args.range)
            base.run_rq1_invalid(
                engine,
                prompt_repo,
                ids,
                model=args.model,
                rerun=False,
                settings=settings,
                invalid_indices=invalid_indices,
            )
        elif args.task == "ablation_prompt":
            ids = base.selected_valid_ids(args.ids, args.range)
            run_ablation_prompt(
                engine,
                prompt_repo,
                ids,
                model=args.model,
                attempt_ids=attempt_ids,
                variants=variants,
            )
        elif args.task == "all":
            if args.ids.strip():
                raw_ids = [x.strip() for x in args.ids.split(",") if x.strip()]
                for x in raw_ids:
                    if not base.re.fullmatch(r"\d+", x):
                        raise ValueError(
                            "--task all with --ids only supports numeric ids, e.g., --ids 3,4,5. "
                            "Use rq1_valid/rq1_invalid/ablation_prompt for Pxxx/Bxxx/app_id ids."
                        )
                valid_ids = base.normalize_valid_ids(raw_ids)
                invalid_ids = base.normalize_invalid_ids(raw_ids)
            elif args.range.strip():
                valid_ids = base.normalize_valid_ids(
                    base.expand_numeric_range(args.range, len(base.list_all_valid_ids()))
                )
                invalid_ids = base.normalize_invalid_ids(
                    base.expand_numeric_range(args.range, len(base.list_all_invalid_ids()))
                )
            else:
                valid_ids = base.list_all_valid_ids()
                invalid_ids = base.list_all_invalid_ids()

            base.run_rq1_valid(
                engine,
                prompt_repo,
                valid_ids,
                model=args.model,
                rerun=False,
                settings=settings,
                attempt_ids=attempt_ids,
                run_ablation_prompt=not args.skip_ablation_prompt,
            )
            base.run_rq1_invalid(
                engine,
                prompt_repo,
                invalid_ids,
                model=args.model,
                rerun=False,
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
