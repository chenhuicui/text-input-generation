-- RQ1 rerun status dashboard for DataGrip.
-- Run this script in database: tosem_text_input_db.
-- Scope:
--   1) Real LLM-call rows whose latest/current row still has empty session_ids_json.
--   2) Kimi missing slots for rq1_valid, rq1_invalid, and ablation_prompt.
--   3) ablation_multi_component / tau auto-expanded row coverage.
--
-- Notes:
--   - ablation_multi_component rows normally have session_ids_json = [] because they are generated
--     from parsed_input_json instead of calling an LLM. Do NOT count their empty session as API failure.
--   - Context-limit rows such as "Prompt contains ... too large for model" are terminal failures
--     for paper analysis, not slots that should be rerun.
--   - The model_family column merges known aliases for ERNIE and Mistral.

SET SESSION group_concat_max_len = 1000000;

-- ----------------------------------------------------------------------
-- 1. Current rerunnable real LLM-call failures: empty session_ids_json.
--    After rerun, this result should shrink. Rows under duplicate aliases are marked optional.
--    Context-limit failures are excluded here and reported in the next result set.
-- ----------------------------------------------------------------------
SELECT
    CASE
        WHEN model IN ('baidu/ernie-5.0-thinking-preview', 'ernie-5.0-thinking-preview')
            THEN 'ernie-5.0-thinking-preview'
        WHEN model IN ('mistralai/mistral-large-2512', 'mistral-large-2512', 'mistral/mistral-large-2512')
            THEN 'mistral-large-2512'
        ELSE model
    END AS model_family,
    model AS exact_model,
    CASE
        WHEN model IN ('ernie-5.0-thinking-preview', 'mistral-large-2512', 'mistral/mistral-large-2512')
            THEN 'duplicate_alias_optional'
        ELSE 'paper_representative'
    END AS scope_hint,
    experiment,
    prompt_setting,
    prompt_variant,
    COUNT(*) AS empty_session_rows,
    COUNT(DISTINCT case_id) AS case_count,
    GROUP_CONCAT(DISTINCT case_id ORDER BY case_id SEPARATOR ',') AS case_ids,
    GROUP_CONCAT(DISTINCT attempt_id ORDER BY attempt_id SEPARATOR ',') AS attempt_ids,
    GROUP_CONCAT(DISTINCT invalid_index ORDER BY invalid_index SEPARATOR ',') AS invalid_indices
FROM t_llm_text_generation
WHERE experiment IN ('rq1_valid', 'rq1_invalid', 'ablation_prompt')
  AND (
        session_ids_json IS NULL
        OR TRIM(session_ids_json) = ''
        OR LOWER(TRIM(session_ids_json)) = 'null'
        OR TRIM(session_ids_json) = '[]'
      )
  AND NOT (
        raw_response LIKE '%Prompt contains%'
        AND raw_response LIKE '%too large for model%'
      )
GROUP BY
    model_family,
    exact_model,
    scope_hint,
    experiment,
    prompt_setting,
    prompt_variant
ORDER BY
    scope_hint DESC,
    model_family,
    exact_model,
    experiment,
    prompt_setting,
    prompt_variant;


-- ----------------------------------------------------------------------
-- 1b. Terminal context-limit failures: count as model failures, do not rerun.
-- ----------------------------------------------------------------------
SELECT
    CASE
        WHEN model IN ('baidu/ernie-5.0-thinking-preview', 'ernie-5.0-thinking-preview')
            THEN 'ernie-5.0-thinking-preview'
        WHEN model IN ('mistralai/mistral-large-2512', 'mistral-large-2512', 'mistral/mistral-large-2512')
            THEN 'mistral-large-2512'
        ELSE model
    END AS model_family,
    model AS exact_model,
    experiment,
    task,
    prompt_setting,
    prompt_variant,
    COUNT(*) AS context_limit_rows,
    COUNT(DISTINCT case_id) AS case_count,
    GROUP_CONCAT(DISTINCT case_id ORDER BY case_id SEPARATOR ',') AS case_ids,
    GROUP_CONCAT(DISTINCT attempt_id ORDER BY attempt_id SEPARATOR ',') AS attempt_ids,
    GROUP_CONCAT(DISTINCT invalid_index ORDER BY invalid_index SEPARATOR ',') AS invalid_indices
FROM t_llm_text_generation
WHERE experiment IN ('rq1_valid', 'rq1_invalid', 'ablation_prompt')
  AND raw_response LIKE '%Prompt contains%'
  AND raw_response LIKE '%too large for model%'
GROUP BY
    model_family,
    exact_model,
    experiment,
    task,
    prompt_setting,
    prompt_variant
ORDER BY
    model_family,
    exact_model,
    experiment,
    prompt_setting,
    prompt_variant;


-- ----------------------------------------------------------------------
-- 2. Kimi expected slots that are still missing or still empty-session.
--    This is the key monitor for the Kimi rerun:
--      - rq1_valid: P001-P115 x attempts 1-3 x extracted/xml/vision
--      - rq1_invalid: B030 x vision x invalid_index 30
--      - ablation_prompt: P001-P115 x attempts 1-3 x 4 prompt variants
-- ----------------------------------------------------------------------
WITH RECURSIVE
nums AS (
    SELECT 1 AS n
    UNION ALL
    SELECT n + 1
    FROM nums
    WHERE n < 115
),
attempts AS (
    SELECT 1 AS attempt_id
    UNION ALL SELECT 2
    UNION ALL SELECT 3
),
rq1_valid_settings AS (
    SELECT 'extracted' AS prompt_setting, 'full' AS prompt_variant
    UNION ALL SELECT 'xml', 'xml'
    UNION ALL SELECT 'vision', 'vision'
),
ablation_variants AS (
    SELECT 'extracted' AS prompt_setting, 'no_global' AS prompt_variant
    UNION ALL SELECT 'extracted', 'no_component'
    UNION ALL SELECT 'extracted', 'no_adjacent'
    UNION ALL SELECT 'extracted', 'no_guiding'
),
expected_slots AS (
    SELECT
        'rq1_valid' AS experiment,
        CONCAT('P', LPAD(n.n, 3, '0')) AS case_id,
        'valid_pptr' AS task,
        'kimi/kimi-k2.5' AS model,
        s.prompt_setting,
        s.prompt_variant,
        a.attempt_id,
        -1 AS invalid_index,
        -1 AS tau,
        -1 AS combo_index,
        n.n AS numeric_id
    FROM nums n
    JOIN attempts a
    JOIN rq1_valid_settings s
    WHERE n.n <= 115

    UNION ALL

    SELECT
        'rq1_invalid',
        'B030',
        'invalid_bug',
        'kimi/kimi-k2.5',
        'vision',
        'vision',
        -1,
        30,
        -1,
        -1,
        30

    UNION ALL

    SELECT
        'ablation_prompt',
        CONCAT('P', LPAD(n.n, 3, '0')),
        'valid_pptr',
        'kimi/kimi-k2.5',
        v.prompt_setting,
        v.prompt_variant,
        a.attempt_id,
        -1,
        -1,
        -1,
        n.n
    FROM nums n
    JOIN attempts a
    JOIN ablation_variants v
    WHERE n.n <= 115
),
latest_rows AS (
    SELECT
        t.*,
        ROW_NUMBER() OVER (
            PARTITION BY
                t.experiment,
                t.case_id,
                t.task,
                t.model,
                t.prompt_setting,
                t.prompt_variant,
                t.feedback_mode,
                t.attempt_id,
                t.invalid_index,
                t.tau,
                t.combo_index
            ORDER BY t.id DESC
        ) AS rn
    FROM t_llm_text_generation t
    JOIN expected_slots e
      ON t.experiment = e.experiment
     AND t.case_id = e.case_id
     AND t.task = e.task
     AND t.model = e.model
     AND t.prompt_setting = e.prompt_setting
     AND t.prompt_variant = e.prompt_variant
     AND t.feedback_mode = 'none'
     AND t.attempt_id = e.attempt_id
     AND t.invalid_index = e.invalid_index
     AND t.tau = e.tau
     AND t.combo_index = e.combo_index
),
judged AS (
    SELECT
        e.*,
        lr.id AS row_id,
        lr.session_ids_json,
        lr.parse_success,
        CASE
            WHEN lr.raw_response LIKE '%Prompt contains%'
                AND lr.raw_response LIKE '%too large for model%'
                THEN 1
            ELSE 0
        END AS is_context_limit_failure,
        CASE
            WHEN lr.id IS NULL THEN 0
            WHEN lr.session_ids_json IS NULL
                OR TRIM(lr.session_ids_json) = ''
                OR LOWER(TRIM(lr.session_ids_json)) = 'null'
                OR TRIM(lr.session_ids_json) = '[]'
                THEN 0
            ELSE 1
        END AS has_non_empty_session
    FROM expected_slots e
    LEFT JOIN latest_rows lr
      ON lr.rn = 1
     AND lr.experiment = e.experiment
     AND lr.case_id = e.case_id
     AND lr.task = e.task
     AND lr.model = e.model
     AND lr.prompt_setting = e.prompt_setting
     AND lr.prompt_variant = e.prompt_variant
     AND lr.attempt_id = e.attempt_id
     AND lr.invalid_index = e.invalid_index
     AND lr.tau = e.tau
     AND lr.combo_index = e.combo_index
)
SELECT
    experiment,
    model,
    prompt_setting,
    prompt_variant,
    COUNT(*) AS remaining_slots,
    SUM(CASE WHEN row_id IS NULL THEN 1 ELSE 0 END) AS missing_rows,
    SUM(CASE WHEN row_id IS NOT NULL AND has_non_empty_session = 0 THEN 1 ELSE 0 END) AS empty_session_rows,
    GROUP_CONCAT(
        CASE
            WHEN experiment = 'rq1_invalid'
                THEN CONCAT(case_id, ':', invalid_index)
            ELSE CONCAT(case_id, ':a', attempt_id)
        END
        ORDER BY numeric_id, attempt_id, invalid_index
        SEPARATOR ','
    ) AS remaining_slots_preview
FROM judged
WHERE row_id IS NULL
   OR (
        has_non_empty_session = 0
        AND is_context_limit_failure = 0
      )
GROUP BY
    experiment,
    model,
    prompt_setting,
    prompt_variant
ORDER BY
    experiment,
    prompt_setting,
    prompt_variant;


-- ----------------------------------------------------------------------
-- 3. Tau / ablation_multi_component coverage by model family.
--    For each model family, actual_slots should match expected_slots.
--    For Kimi, this should become complete after rq1_valid extracted/full succeeds.
-- ----------------------------------------------------------------------
WITH
model_families AS (
    SELECT 'qwen3.5-plus' AS model_family
    UNION ALL SELECT 'gemini-3.1-pro-preview'
    UNION ALL SELECT 'Doubao-Seed-2.0-pro'
    UNION ALL SELECT 'kimi/kimi-k2.5'
    UNION ALL SELECT 'grok-4.20-reasoning'
    UNION ALL SELECT 'gpt-5.4-2026-03-05'
    UNION ALL SELECT 'claude-opus-4-6'
    UNION ALL SELECT 'mistral-large-2512'
    UNION ALL SELECT 'ernie-5.0-thinking-preview'
),
expected_tau AS (
    SELECT 0 AS tau, 93 AS expected_slots
    UNION ALL SELECT 1, 294
    UNION ALL SELECT 2, 405
    UNION ALL SELECT 3, 372
    UNION ALL SELECT 4, 285
    UNION ALL SELECT 5, 180
    UNION ALL SELECT 6, 84
    UNION ALL SELECT 7, 24
    UNION ALL SELECT 8, 3
),
actual_tau AS (
    SELECT
        CASE
            WHEN model IN ('baidu/ernie-5.0-thinking-preview', 'ernie-5.0-thinking-preview')
                THEN 'ernie-5.0-thinking-preview'
            WHEN model IN ('mistralai/mistral-large-2512', 'mistral-large-2512', 'mistral/mistral-large-2512')
                THEN 'mistral-large-2512'
            ELSE model
        END AS model_family,
        tau,
        COUNT(DISTINCT CONCAT_WS('|', case_id, attempt_id, invalid_index, tau, combo_index, prompt_setting, prompt_variant)) AS actual_slots
    FROM t_llm_text_generation
    WHERE experiment = 'ablation_multi_component'
      AND task = 'valid_pptr'
      AND prompt_setting = 'extracted'
      AND prompt_variant = 'full'
      AND feedback_mode = 'none'
    GROUP BY model_family, tau
)
SELECT
    mf.model_family,
    et.tau,
    et.expected_slots,
    COALESCE(at.actual_slots, 0) AS actual_slots,
    GREATEST(et.expected_slots - COALESCE(at.actual_slots, 0), 0) AS missing_slots
FROM model_families mf
CROSS JOIN expected_tau et
LEFT JOIN actual_tau at
  ON at.model_family = mf.model_family
 AND at.tau = et.tau
WHERE GREATEST(et.expected_slots - COALESCE(at.actual_slots, 0), 0) > 0
ORDER BY
    mf.model_family,
    et.tau;
