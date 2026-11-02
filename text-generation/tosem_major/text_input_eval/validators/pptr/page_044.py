from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page044Validator(BaseValidator):
    case_id = "P044"
    task = "valid_input"

    TARGET_ID = "com.jeyluta.timestampcamerafree:id/custom_editText"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID).strip()
        return bool(value) and value.lower() not in {
            "generated_value", "generated value", "placeholder", "text",
        }
