from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register
from text_input_eval.validators.common import is_email


@register
class Page021Validator(BaseValidator):
    case_id = "P021"
    task = "valid_input"

    NAME_ID = "input:name"
    EMAIL_ID = "input:email"

    REQUIRED_IDS = {
        NAME_ID,
        EMAIL_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        name_value = self.value_by_id(generated_inputs, self.NAME_ID).strip()
        email_value = self.value_by_id(generated_inputs, self.EMAIL_ID).strip()

        if name_value == "":
            return False

        if email_value == "":
            return True

        return is_email(email_value)