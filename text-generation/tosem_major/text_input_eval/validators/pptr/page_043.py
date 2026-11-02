import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page043Validator(BaseValidator):
    case_id = "P043"
    task = "valid_input"

    TARGET_ID = "textInputPhonenumber"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    PHONE_RE = re.compile(r"^[0-9()\-\+\s]+$")

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID).strip()

        if value == "":
            return False

        if not self.PHONE_RE.fullmatch(value):
            return False

        digit_count = sum(ch.isdigit() for ch in value)
        return digit_count >= 10