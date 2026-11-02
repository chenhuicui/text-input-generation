import re

import zipcodes

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page027Validator(BaseValidator):
    case_id = "P027"
    task = "valid_input"

    TARGET_ID = "com.fandango:id/editText"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    TEXT_RE = re.compile(r"^[A-Za-z0-9 .,'&()\-:]+$")

    def _is_zipcode(self, value: str) -> bool:
        return bool(zipcodes.matching(value))

    def _is_location_or_keyword(self, value: str) -> bool:
        if not self.TEXT_RE.fullmatch(value):
            return False

        return any(ch.isalpha() for ch in value)

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID).strip()
        if not value:
            return False

        return self._is_zipcode(value) or self._is_location_or_keyword(value)