import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page090Validator(BaseValidator):
    case_id = "P090"
    task = "valid_input"

    FIRST_NAME_ID = "TextInput.firstName"
    LAST_NAME_ID = "TextInput.lastName"
    PHONE_ID = "TextInput.phoneNumber"

    REQUIRED_IDS = {
        FIRST_NAME_ID,
        LAST_NAME_ID,
        PHONE_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        first = self.value_by_id(generated_inputs, self.FIRST_NAME_ID)
        last = self.value_by_id(generated_inputs, self.LAST_NAME_ID)
        digits = re.sub(r"\D", "", self.value_by_id(generated_inputs, self.PHONE_ID))
        return bool(first) and bool(last) and len(digits) == 10
