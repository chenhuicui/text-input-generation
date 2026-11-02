import json

import text_input_eval.validators  # noqa: F401
from text_input_eval.core.placeholder import contains_generated_value
from text_input_eval.validators.registry import get_validator


def normalize_parsed_input_json(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    raise TypeError(f"Unsupported parsed_input_json type: {type(value)}")


def evaluate_row(row: dict):
    parsed_input_json = normalize_parsed_input_json(row["parsed_input_json"])
    placeholder_tag = 1 if contains_generated_value(parsed_input_json) else 0

    validator = get_validator(row["case_id"])
    eval_success = validator.validate(parsed_input_json)
    return eval_success, placeholder_tag