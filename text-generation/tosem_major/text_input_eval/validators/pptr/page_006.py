import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page006Validator(BaseValidator):
    case_id = "P006"
    task = "valid_input"

    TARGET_ID = "app.kids360.kid:id/codeInput"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID)
        return re.fullmatch(r"[A-Za-z0-9]{6,12}", value) is not None
