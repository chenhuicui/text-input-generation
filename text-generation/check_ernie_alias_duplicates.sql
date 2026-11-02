-- Check whether ERNIE exact model aliases duplicate the same logical RQ1 slots.
-- Run in database: tosem_text_input_db.
--
-- Duplicate definition:
--   the same experiment/case/task/prompt/feedback/attempt/invalid/tau/combo slot
--   has non-empty LLM sessions under both:
--     - baidu/ernie-5.0-thinking-preview
--     - ernie-5.0-thinking-preview

SET SESSION group_concat_max_len = 1000000;

-- 1. Raw non-empty session rows by exact model name.
SELECT
    model,
    COUNT(*) AS rows_with_non_empty_session
FROM t_llm_text_generation
WHERE experiment LIKE 'rq1%'
  AND model IN ('baidu/ernie-5.0-thinking-preview', 'ernie-5.0-thinking-preview')
  AND session_ids_json IS NOT NULL
  AND TRIM(session_ids_json) <> ''
  AND LOWER(TRIM(session_ids_json)) <> 'null'
  AND TRIM(session_ids_json) <> '[]'
GROUP BY model
ORDER BY model;

-- 2. Cross-alias duplicate slots by experiment and prompt.
WITH alias_rows AS (
    SELECT
        id,
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
    FROM t_llm_text_generation
    WHERE experiment LIKE 'rq1%'
      AND model IN ('baidu/ernie-5.0-thinking-preview', 'ernie-5.0-thinking-preview')
      AND session_ids_json IS NOT NULL
      AND TRIM(session_ids_json) <> ''
      AND LOWER(TRIM(session_ids_json)) <> 'null'
      AND TRIM(session_ids_json) <> '[]'
),
slot_groups AS (
    SELECT
        experiment,
        case_id,
        task,
        prompt_setting,
        prompt_variant,
        feedback_mode,
        attempt_id,
        invalid_index,
        tau,
        combo_index,
        COUNT(*) AS row_count,
        COUNT(DISTINCT model) AS model_count,
        SUM(model = 'baidu/ernie-5.0-thinking-preview') AS canonical_rows,
        SUM(model = 'ernie-5.0-thinking-preview') AS bare_alias_rows
    FROM alias_rows
    GROUP BY
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
)
SELECT
    experiment,
    task,
    prompt_setting,
    prompt_variant,
    COUNT(*) AS duplicated_slots,
    SUM(canonical_rows) AS canonical_rows_in_duplicated_slots,
    SUM(bare_alias_rows) AS bare_alias_rows_in_duplicated_slots,
    SUM(row_count) AS total_rows_in_duplicated_slots
FROM slot_groups
WHERE model_count = 2
GROUP BY
    experiment,
    task,
    prompt_setting,
    prompt_variant
ORDER BY
    experiment,
    task,
    prompt_setting,
    prompt_variant;

-- 3. Concrete duplicate rows for inspection.
WITH alias_rows AS (
    SELECT
        id,
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
        combo_index,
        session_ids_json
    FROM t_llm_text_generation
    WHERE experiment LIKE 'rq1%'
      AND model IN ('baidu/ernie-5.0-thinking-preview', 'ernie-5.0-thinking-preview')
      AND session_ids_json IS NOT NULL
      AND TRIM(session_ids_json) <> ''
      AND LOWER(TRIM(session_ids_json)) <> 'null'
      AND TRIM(session_ids_json) <> '[]'
),
slot_groups AS (
    SELECT
        experiment,
        case_id,
        task,
        prompt_setting,
        prompt_variant,
        feedback_mode,
        attempt_id,
        invalid_index,
        tau,
        combo_index,
        COUNT(DISTINCT model) AS model_count
    FROM alias_rows
    GROUP BY
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
    HAVING model_count = 2
)
SELECT a.*
FROM alias_rows a
JOIN slot_groups g
  ON a.experiment = g.experiment
 AND a.case_id = g.case_id
 AND a.task = g.task
 AND a.prompt_setting = g.prompt_setting
 AND a.prompt_variant = g.prompt_variant
 AND a.feedback_mode = g.feedback_mode
 AND a.attempt_id = g.attempt_id
 AND a.invalid_index = g.invalid_index
 AND a.tau = g.tau
 AND a.combo_index = g.combo_index
ORDER BY
    a.experiment,
    a.case_id,
    a.prompt_setting,
    a.prompt_variant,
    a.attempt_id,
    a.invalid_index,
    a.tau,
    a.combo_index,
    a.model,
    a.id
LIMIT 200;
