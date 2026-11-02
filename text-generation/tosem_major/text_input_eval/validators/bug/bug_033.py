import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug033Validator(BaseValidator):
    case_id = "B033"
    task = "invalid_bug"

    FIELD_1 = "app.organicmaps:id/input__bounds_84_784_1356_933"
    FIELD_2 = "app.organicmaps:id/input__bounds_273_1610_1356_1759"
    FIELD_3 = "app.organicmaps:id/input__bounds_273_1890_1356_2039"

    REQUIRED_IDS = {
        FIELD_1,
        FIELD_2,
        FIELD_3,
    }

    PROHIBITED_CHAR_RE = re.compile(r"[^a-zA-Z0-9 _-]")

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        for field_id in self.REQUIRED_IDS:
            value = self.value_by_id(generated_inputs, field_id)
            if value and self.PROHIBITED_CHAR_RE.search(value):
                return True

        return False