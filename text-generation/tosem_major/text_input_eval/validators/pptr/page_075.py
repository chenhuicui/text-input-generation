from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page075Validator(BaseValidator):
    case_id = "P075"
    task = "valid_input"

    TARGET_ID = "com.rnovel.android.app:id/et_input"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.TARGET_ID, "")).strip()
        return self.is_email(value)