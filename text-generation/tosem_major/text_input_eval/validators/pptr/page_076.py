import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page076Validator(BaseValidator):
    case_id = "P076"
    task = "valid_input"

    TARGET_ID = "com.roomthermometer.interior:id/places_autocomplete_search_bar"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    CITY_RE = re.compile(r"^[A-Za-z]+(?:[ .'-][A-Za-z]+)*$")

    INVALID_VALUES = {
        "generated_value",
        "generated-value",
        "generated value",
        "generatedvalue",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.TARGET_ID, "")).strip()
        if not value:
            return False

        if value.lower() in self.INVALID_VALUES:
            return False

        return bool(self.CITY_RE.fullmatch(value))