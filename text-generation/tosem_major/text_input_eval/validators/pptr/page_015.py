import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page015Validator(BaseValidator):
    case_id = "P015"
    task = "valid_input"

    PASSWORD_ID = "com.beint.zangi:id/password_edit_text"
    CONFIRM_PASSWORD_ID = "com.beint.zangi:id/password_confirm_edit_text"

    REQUIRED_IDS = {
        PASSWORD_ID,
        CONFIRM_PASSWORD_ID,
    }

    NO_WHITESPACE_RE = re.compile(r"^[^\s]+$")

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        password = self.value_by_id(generated_inputs, self.PASSWORD_ID)
        confirm_password = self.value_by_id(generated_inputs, self.CONFIRM_PASSWORD_ID)

        if password == "" or confirm_password == "":
            return False

        if password != confirm_password:
            return False

        if len(password) < 6:
            return False

        if not self.NO_WHITESPACE_RE.fullmatch(password):
            return False

        return True