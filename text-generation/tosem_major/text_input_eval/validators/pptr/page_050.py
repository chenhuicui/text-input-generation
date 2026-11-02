import re

import zipcodes

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page050Validator(BaseValidator):
    case_id = "P050"
    task = "valid_input"

    TARGET_ID = "com.michaels.michaelsstores:id/etSearch"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    CITY_RE = re.compile(r"^[A-Za-z]+(?:[ .'-][A-Za-z]+)*$")
    CITY_STATE_RE = re.compile(
        r"^[A-Za-z]+(?:[ .'-][A-Za-z]+)*,\s*[A-Za-z]{2,}(?:\s+\d{5}(?:-\d{4})?)?$"
    )

    def _is_zipcode(self, value: str) -> bool:
        return bool(zipcodes.matching(value))

    def _is_city_or_city_state(self, value: str) -> bool:
        return bool(
            self.CITY_RE.fullmatch(value)
            or self.CITY_STATE_RE.fullmatch(value)
        )

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.TARGET_ID, "")).strip()
        if not value:
            return False

        return self._is_zipcode(value) or self._is_city_or_city_state(value)