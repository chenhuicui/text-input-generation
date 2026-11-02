SELECT
    curated_id,
    source_id,
    model,
    exact_model,
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
    completion_status,
    generation_status,
    CASE
        WHEN raw_response IS NULL OR TRIM(raw_response) = '' THEN 'empty_raw_response'
        WHEN JSON_VALID(raw_response)
         AND JSON_UNQUOTE(JSON_EXTRACT(raw_response, '$.success')) = 'false' THEN 'success_false'
        ELSE 'other'
    END AS rerun_reason,
    CASE
        WHEN JSON_VALID(raw_response) THEN JSON_UNQUOTE(JSON_EXTRACT(raw_response, '$.code'))
        ELSE NULL
    END AS response_code,
    CASE
        WHEN JSON_VALID(raw_response) THEN LEFT(JSON_UNQUOTE(JSON_EXTRACT(raw_response, '$.msg')), 500)
        ELSE NULL
    END AS response_msg,
    LEFT(raw_response, 800) AS raw_response_excerpt
FROM t_llm_text_generation_curated
WHERE experiment IN ('rq1_valid', 'rq1_invalid', 'ablation_prompt')
  AND completion_status IN ('incomplete_generation', 'missing_slot')

  AND (
      raw_response IS NULL
      OR TRIM(raw_response) = ''
      OR (
          JSON_VALID(raw_response)
          AND JSON_UNQUOTE(JSON_EXTRACT(raw_response, '$.success')) = 'false'
      )
  )


  AND (
      (model = 'kimi/kimi-k2.5' AND experiment IN ('rq1_valid', 'ablation_prompt'))
      OR (model = 'baidu/ernie-5.0-thinking-preview' AND experiment = 'rq1_invalid')
      OR (model = 'grok-4.20-reasoning' AND experiment = 'rq1_invalid')
      OR (model = 'mistralai/mistral-large-2512' AND experiment IN ('rq1_invalid', 'rq1_valid'))
      OR (model = 'claude-opus-4-6' AND experiment = 'rq1_invalid')
      OR (model = 'Doubao-Seed-2.0-pro' AND experiment = 'rq1_invalid')
  )

ORDER BY
    model,
    experiment,
    prompt_setting,
    prompt_variant,
    case_id,
    attempt_id,
    invalid_index;
