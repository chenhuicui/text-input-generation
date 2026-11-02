from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page049Validator(BaseValidator):
    case_id = "P049"
    task = "valid_input"

    TARGET_ID = "com.limebike:id/phone_number_input"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        return False