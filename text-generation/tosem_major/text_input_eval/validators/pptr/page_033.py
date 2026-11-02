import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.common import is_email
from text_input_eval.validators.registry import register


@register
class Page033Validator(BaseValidator):
    case_id = "P033"
    task = "valid_input"

    FIELD_0 = "compassTextInput::#::0"
    FIELD_1 = "compassTextInput::#::1"
    FIELD_2 = "compassTextInput::#::2"
    FIELD_3 = "compassTextInput::#::3"
    FIELD_4 = "compassTextInput::#::4"

    REQUIRED_IDS = {
        FIELD_0,
        FIELD_1,
        FIELD_2,
        FIELD_3,
        FIELD_4,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False
        first = self.value_by_id(generated_inputs, self.FIELD_0)
        last = self.value_by_id(generated_inputs, self.FIELD_1)
        email = self.value_by_id(generated_inputs, self.FIELD_2)
        phone = self.value_by_id(generated_inputs, self.FIELD_3)
        birth_date = self.value_by_id(generated_inputs, self.FIELD_4)
        phone_digits = re.sub(r"\D", "", phone)
        date_ok = not birth_date or bool(
            re.fullmatch(r"(?:\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})", birth_date)
        )
        phone_ok = not phone or 7 <= len(phone_digits) <= 15
        return bool(first and last and is_email(email) and phone_ok and date_ok)
