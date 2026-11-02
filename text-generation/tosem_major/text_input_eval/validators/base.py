from __future__ import annotations

from typing import Any, Dict, Iterable


class BaseValidator:
    case_id: str = "UNKNOWN"
    task: str = "unknown"  # valid_pptr / invalid_bug

    def validate(self, generated_inputs: Dict[str, Any]) -> bool:
        raise NotImplementedError

    @staticmethod
    def text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def value_by_id(self, generated_inputs: Dict[str, Any], resource_id: str) -> str:
        return self.text(generated_inputs.get(resource_id))

    def has_id(self, generated_inputs: Dict[str, Any], resource_id: str) -> bool:
        return resource_id in generated_inputs

    def all_values(self, generated_inputs: Dict[str, Any]) -> list[str]:
        vals: list[str] = []
        for v in generated_inputs.values():
            if v is None:
                continue
            vals.append(self.text(v))
        return vals

    def contains_all_required_ids(
        self,
        generated_inputs: Dict[str, Any],
        required_ids: Iterable[str],
    ) -> bool:
        """
        Backward-compatible name, but now enforces exact key matching.

        Return True only if the JSON object contains exactly the required
        resource-ids:
        - every required id must be present;
        - no unsupported / extra id is allowed.

        This checks key existence only, not whether values are non-empty.
        """
        return set(generated_inputs.keys()) == set(required_ids)

    def matches_required_ids_exactly(
        self,
        generated_inputs: Dict[str, Any],
        required_ids: Iterable[str],
    ) -> bool:
        """
        Preferred explicit name.

        Return True only if the JSON object contains exactly the required
        resource-ids:
        - every required id must be present;
        - no unsupported / extra id is allowed.

        This checks key existence only, not whether values are non-empty.
        """
        return set(generated_inputs.keys()) == set(required_ids)

    def has_non_empty_value(
        self,
        generated_inputs: Dict[str, Any],
        resource_id: str,
    ) -> bool:
        """
        Return True if the given resource-id exists and its normalized text
        value is non-empty.
        """
        return self.has_id(generated_inputs, resource_id) and bool(
            self.value_by_id(generated_inputs, resource_id)
        )

    def all_required_values_non_empty(
        self,
        generated_inputs: Dict[str, Any],
        required_ids: Iterable[str],
    ) -> bool:
        """
        Return True only if all required ids exist and their normalized text
        values are non-empty.
        """
        return all(self.has_non_empty_value(generated_inputs, rid) for rid in required_ids)

    def any_required_value_non_empty(
        self,
        generated_inputs: Dict[str, Any],
        required_ids: Iterable[str],
    ) -> bool:
        """
        Return True if at least one required id exists and its normalized text
        value is non-empty.
        """
        return any(self.has_non_empty_value(generated_inputs, rid) for rid in required_ids)

    def count_non_empty_required_values(
        self,
        generated_inputs: Dict[str, Any],
        required_ids: Iterable[str],
    ) -> int:
        """
        Count how many required ids exist and have non-empty normalized values.
        """
        return sum(
            1
            for rid in required_ids
            if self.has_non_empty_value(generated_inputs, rid)
        )