from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.common import is_email
from text_input_eval.validators.registry import register


@register
class Page058Validator(BaseValidator):
    case_id = "P058"
    task = "valid_input"

    NAME_ID = "name"
    EMAIL_ID = "email"
    PASSWORD_ID = "password"

    REQUIRED_IDS = {
        NAME_ID,
        EMAIL_ID,
        PASSWORD_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        name = self.value_by_id(generated_inputs, self.NAME_ID)
        email = self.value_by_id(generated_inputs, self.EMAIL_ID)
        password = self.value_by_id(generated_inputs, self.PASSWORD_ID)
        return bool(name) and is_email(email) and len(password) >= 8
