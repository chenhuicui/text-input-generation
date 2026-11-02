from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page099Validator(BaseValidator):
    case_id = "P099"
    task = "valid_input"

    EMAIL_ID = "signup-options-email-text-field"

    REQUIRED_IDS = {
        EMAIL_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.EMAIL_ID, "")).strip()
        if not value:
            return False

        return True