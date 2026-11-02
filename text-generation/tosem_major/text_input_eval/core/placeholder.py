from typing import Any


def contains_generated_value(obj: Any) -> bool:
    if obj is None:
        return False
    if isinstance(obj, dict):
        return any(contains_generated_value(v) for v in obj.values())
    if isinstance(obj, list):
        return any(contains_generated_value(v) for v in obj)
    return "generated_value" in str(obj)