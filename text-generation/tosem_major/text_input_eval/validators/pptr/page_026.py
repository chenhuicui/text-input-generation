import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page026Validator(BaseValidator):
    case_id = "P026"
    task = "valid_input"

    TARGET_ID = "com.family.locator.find.my.kids:id/et_invitation_code"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID)
        return re.fullmatch(r"\d{6}", value) is not None
