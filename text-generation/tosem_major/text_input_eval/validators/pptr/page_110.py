import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page110Validator(BaseValidator):
    case_id = "P110"
    task = "valid_input"

    USERNAME_ID = "SignInPageLabelUsername"
    PASSWORD_ID = "SignInPageLabelPassword"

    REQUIRED_IDS = {
        USERNAME_ID,
        PASSWORD_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        digits = re.sub(
            r"\D", "", self.value_by_id(generated_inputs, self.USERNAME_ID)
        )
        password = self.value_by_id(generated_inputs, self.PASSWORD_ID)
        return 10 <= len(digits) <= 15 and bool(password)
