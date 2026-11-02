from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.common import is_email
from text_input_eval.validators.registry import register


@register
class Page084Validator(BaseValidator):
    case_id = "P084"
    task = "valid_input"

    REQUIRED_IDS = {
        "first_name",
        "last_name",
        "email",
        "password",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False
        return (
            bool(self.value_by_id(generated_inputs, "first_name"))
            and bool(self.value_by_id(generated_inputs, "last_name"))
            and is_email(self.value_by_id(generated_inputs, "email"))
            and len(self.value_by_id(generated_inputs, "password")) >= 8
        )
