from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register
from text_input_eval.validators.common import is_email


@register
class Page001Validator(BaseValidator):
    case_id = "P001"
    task = "valid_input"

    EMAIL_ID = "ai.chat.gpt.bot:id/inputEditText"

    REQUIRED_IDS = {
        EMAIL_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.EMAIL_ID).strip()

        return value != "" and is_email(value)