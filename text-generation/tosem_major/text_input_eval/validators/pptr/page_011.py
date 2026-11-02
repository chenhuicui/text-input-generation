import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page011Validator(BaseValidator):
    case_id = "P011"
    task = "valid_input"

    FIRST_NAME_ID = "com.aiphone.secondphonenumber:id/firstNameEt"
    LAST_NAME_ID = "com.aiphone.secondphonenumber:id/lastNameEt"
    NOTE_ID = "com.aiphone.secondphonenumber:id/et_note"
    NUMBER_ID = "com.aiphone.secondphonenumber:id/numberEt"

    REQUIRED_IDS = {
        FIRST_NAME_ID,
        LAST_NAME_ID,
        NOTE_ID,
        NUMBER_ID,
    }

    NUMBER_RE = re.compile(r"^[0-9()\-\+\s]+$")

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        first_name = self.value_by_id(generated_inputs, self.FIRST_NAME_ID).strip()
        number = self.value_by_id(generated_inputs, self.NUMBER_ID).strip()

        if first_name == "" or number == "":
            return False

        if not self.NUMBER_RE.fullmatch(number):
            return False

        digit_count = sum(ch.isdigit() for ch in number)
        return digit_count > 0