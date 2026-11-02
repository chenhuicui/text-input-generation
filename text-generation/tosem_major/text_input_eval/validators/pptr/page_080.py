import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page080Validator(BaseValidator):
    case_id = "P080"
    task = "valid_input"

    TARGET_ID = "text-input"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID)
        digits = re.sub(r"\D", "", value)
        return 10 <= len(digits) <= 15
