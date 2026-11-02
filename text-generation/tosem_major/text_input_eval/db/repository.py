import json
from typing import Any, Dict, List, Optional

from text_input_eval.db.connection import get_conn
from text_input_eval.db.models import TABLE_LLM_TEXT_GENERATION


TABLE_NAME = TABLE_LLM_TEXT_GENERATION


class LLMTextGenerationRepository:
    def fetch_mock_pending(self, limit: int = 100) -> List[Dict[str, Any]]:
        sql = f"""
        SELECT *
        FROM {TABLE_NAME}
        WHERE mock = 1
          AND parse_success = 1
          AND eval_success IS NULL
        ORDER BY id ASC
        LIMIT %s
        """
        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, (limit,))
                return cursor.fetchall()
        finally:
            conn.close()

    def fetch_pending(self, limit: int = 100) -> List[Dict[str, Any]]:
        sql = f"""
        SELECT *
        FROM {TABLE_NAME}
        WHERE parse_success = 1
          AND eval_success IS NULL
        ORDER BY id ASC
        LIMIT %s
        """
        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, (limit,))
                return cursor.fetchall()
        finally:
            conn.close()

    def update_eval_success(self, row_id: int, eval_success: bool) -> None:
        sql = f"""
        UPDATE {TABLE_NAME}
        SET eval_success = %s
        WHERE id = %s
        """
        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, (1 if eval_success else 0, row_id))
            conn.commit()
        finally:
            conn.close()

    def update_placeholder_tag(self, row_id: int, placeholder_tag: int) -> None:
        sql = f"""
        UPDATE {TABLE_NAME}
        SET placeholder_tag = %s
        WHERE id = %s
        """
        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, (placeholder_tag, row_id))
            conn.commit()
        finally:
            conn.close()

    def update_parse_failure(self, row_id: int, parse_error: str) -> None:
        sql = f"""
        UPDATE {TABLE_NAME}
        SET parse_success = 0,
            parse_error = %s
        WHERE id = %s
        """
        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, (parse_error, row_id))
            conn.commit()
        finally:
            conn.close()

    def insert_mock_row(
        self,
        experiment: str,
        case_id: str,
        task: str,
        model: str,
        prompt_setting: str,
        prompt_variant: str,
        feedback_mode: str,
        attempt_id: int,
        invalid_index: int,
        session_ids_json: list[str],
        raw_response: str,
        parsed_input_json: Optional[dict],
        parse_success: bool,
        parse_error: Optional[str],
        placeholder_tag: int,
    ) -> None:
        sql = f"""
        INSERT INTO {TABLE_NAME} (
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
            raw_response,
            parsed_input_json,
            parse_success,
            parse_error,
            placeholder_tag,
            eval_success,
            mock
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, 1)
        """
        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    sql,
                    (
                        experiment,
                        case_id,
                        task,
                        model,
                        prompt_setting,
                        prompt_variant,
                        feedback_mode,
                        attempt_id,
                        invalid_index,
                        json.dumps(session_ids_json, ensure_ascii=False),
                        raw_response,
                        json.dumps(parsed_input_json, ensure_ascii=False) if parsed_input_json is not None else None,
                        1 if parse_success else 0,
                        parse_error,
                        placeholder_tag,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def fetch_feedback_history(
        self,
        case_id: str,
        model: str,
        prompt_setting: str,
        prompt_variant: str,
        task: str,
    ) -> List[Dict[str, Any]]:
        sql = f"""
        SELECT *
        FROM {TABLE_NAME}
        WHERE case_id = %s
          AND model = %s
          AND prompt_setting = %s
          AND prompt_variant = %s
          AND task = %s
          AND feedback_mode = 'feedback'
        ORDER BY attempt_id ASC, invalid_index ASC, id ASC
        """
        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, (case_id, model, prompt_setting, prompt_variant, task))
                return cursor.fetchall()
        finally:
            conn.close()

    def fetch_rows_by_case_id(self, case_id: str, only_mock: bool = True, limit: int = 500):
        if only_mock:
            sql = f"""
            SELECT *
            FROM {TABLE_NAME}
            WHERE case_id = %s
              AND mock = 1
              AND parse_success = 1
            ORDER BY id ASC
            LIMIT %s
            """
            params = (case_id, limit)
        else:
            sql = f"""
            SELECT *
            FROM {TABLE_NAME}
            WHERE case_id = %s
              AND parse_success = 1
            ORDER BY id ASC
            LIMIT %s
            """
            params = (case_id, limit)

        conn = get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()
        finally:
            conn.close()