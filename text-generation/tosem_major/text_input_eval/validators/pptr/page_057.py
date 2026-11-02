from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.common import is_email
from text_input_eval.validators.registry import register


@register
class Page057Validator(BaseValidator):
    case_id = "P057"
    task = "valid_input"

    TARGET_ID = "com.mysugr.android.companion:id/emailEditText"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.TARGET_ID, "")).strip()
        return is_email(value)
