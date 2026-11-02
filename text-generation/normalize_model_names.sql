-- Build normalized analysis tables without modifying the original generation table.
-- Run in database: tosem_text_input_db.
--
-- Source table:
--   t_llm_text_generation
--
-- Output tables:
--   t_llm_text_generation_analysis_all
--     Full normalized copy of necessary fields. Keeps all exact-model alias rows.
--   t_llm_text_generation_analysis
--     Deduplicated paper-analysis table. One row per normalized logical slot.
--
-- Convention:
--   exact_model = the original API/model string stored by the runner.
--   model       = normalized model name used by paper analysis and figures.
--   generation_status marks terminal API/model outcomes without copying raw_response.
--
-- Deduplication rule:
--   For the same normalized logical slot, prefer the canonical exact API name:
--     baidu/ernie-5.0-thinking-preview over ernie-5.0-thinking-preview
--     mistralai/mistral-large-2512 over mistral-large-2512 or mistral/mistral-large-2512
--   Then prefer the latest id.

DROP TABLE IF EXISTS t_llm_text_generation_analysis;
DROP TABLE IF EXISTS t_llm_text_generation_analysis_all;

CREATE TABLE t_llm_text_generation_analysis_all AS
SELECT
    id,
    experiment,
    case_id,
    task,
    CASE
        WHEN model IN ('baidu/ernie-5.0-thinking-preview', 'ernie-5.0-thinking-preview')
            THEN 'baidu/ernie-5.0-thinking-preview'
        WHEN model IN ('mistralai/mistral-large-2512', 'mistral-large-2512', 'mistral/mistral-large-2512')
            THEN 'mistralai/mistral-large-2512'
        ELSE model
    END AS model,
    model AS exact_model,
    CASE
        WHEN model IN ('ernie-5.0-thinking-preview', 'mistral-large-2512', 'mistral/mistral-large-2512')
            THEN 'alias_normalized'
        ELSE 'canonical_exact'
    END AS model_alias_status,
    CASE
        WHEN model IN ('baidu/ernie-5.0-thinking-preview', 'mistralai/mistral-large-2512')
            THEN 0
        WHEN model IN ('ernie-5.0-thinking-preview', 'mistral-large-2512', 'mistral/mistral-large-2512')
            THEN 1
        ELSE 0
    END AS model_alias_rank,
    prompt_setting,
    prompt_variant,
    feedback_mode,
    attempt_id,
    invalid_index,
    session_ids_json,
    parsed_input_json,
    parse_success,
    parse_error,
    CASE
        WHEN raw_response LIKE '%Prompt contains%'
            AND raw_response LIKE '%too large for model%'
            THEN 'api_context_limit'
        WHEN raw_response LIKE '%reject_no_credit%'
            OR raw_response LIKE '%HTTP 402%'
            THEN 'api_no_credit'
        WHEN session_ids_json IS NULL
            OR TRIM(session_ids_json) = ''
            OR LOWER(TRIM(session_ids_json)) = 'null'
            OR TRIM(session_ids_json) = '[]'
            THEN 'api_empty_session'
        WHEN parse_success = 1 THEN 'parse_success'
        ELSE 'parse_failure'
    END AS generation_status,
    placeholder_tag,
    eval_success,
    tau,
    combo_index,
    elapsed_ms
FROM t_llm_text_generation;

CREATE TABLE t_llm_text_generation_analysis AS
SELECT
    id,
    experiment,
    case_id,
    task,
    model,
    exact_model,
    model_alias_status,
    prompt_setting,
    prompt_variant,
    feedback_mode,
    attempt_id,
    invalid_index,
    session_ids_json,
    parsed_input_json,
    parse_success,
    parse_error,
    generation_status,
    placeholder_tag,
    eval_success,
    tau,
    combo_index,
    elapsed_ms
FROM (
    SELECT
        a.*,
        ROW_NUMBER() OVER (
            PARTITION BY
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
                combo_index
            ORDER BY
                model_alias_rank ASC,
                id DESC
        ) AS rn
    FROM t_llm_text_generation_analysis_all a
) ranked
WHERE rn = 1;

CREATE INDEX idx_llm_analysis_all_main
    ON t_llm_text_generation_analysis_all (
        experiment,
        task,
        model,
        prompt_setting,
        prompt_variant,
        case_id
    );

CREATE INDEX idx_llm_analysis_main
    ON t_llm_text_generation_analysis (
        experiment,
        task,
        model,
        prompt_setting,
        prompt_variant,
        case_id
    );

CREATE INDEX idx_llm_analysis_attempt
    ON t_llm_text_generation_analysis (
        experiment,
        task,
        model,
        case_id,
        prompt_setting,
        attempt_id,
        invalid_index,
        tau,
        combo_index
    );

CREATE INDEX idx_llm_analysis_exact_model
    ON t_llm_text_generation_analysis (exact_model, model_alias_status);

CREATE INDEX idx_llm_analysis_generation_status
    ON t_llm_text_generation_analysis (generation_status, model, experiment, prompt_setting);

-- Summary: how many rows were retained after canonicalization and deduplication.
SELECT
    'all_normalized_rows' AS table_scope,
    model,
    exact_model,
    model_alias_status,
    generation_status,
    COUNT(*) AS rows_in_table
FROM t_llm_text_generation_analysis_all
GROUP BY
    model,
    exact_model,
    model_alias_status,
    generation_status

UNION ALL

SELECT
    'dedup_analysis_rows' AS table_scope,
    model,
    exact_model,
    model_alias_status,
    generation_status,
    COUNT(*) AS rows_in_table
FROM t_llm_text_generation_analysis
GROUP BY
    model,
    exact_model,
    model_alias_status,
    generation_status
ORDER BY
    table_scope,
    model,
    exact_model,
    generation_status;
