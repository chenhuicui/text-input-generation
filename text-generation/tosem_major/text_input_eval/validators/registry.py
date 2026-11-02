from __future__ import annotations

from typing import Dict, Type

from text_input_eval.validators.base import BaseValidator


_REGISTRY: Dict[str, Type[BaseValidator]] = {}


def register(cls: Type[BaseValidator]) -> Type[BaseValidator]:
    case_id = getattr(cls, "case_id", None)
    if not case_id or case_id == "UNKNOWN":
        raise ValueError(f"Invalid validator case_id: {cls.__name__}")

    if case_id in _REGISTRY:
        raise ValueError(f"Duplicate validator for case_id={case_id}")

    _REGISTRY[case_id] = cls
    return cls


def get_validator(case_id: str) -> BaseValidator:
    if case_id not in _REGISTRY:
        raise KeyError(f"No validator found for case_id={case_id}")
    return _REGISTRY[case_id]()


def list_registered_case_ids() -> list[str]:
    return sorted(_REGISTRY.keys())