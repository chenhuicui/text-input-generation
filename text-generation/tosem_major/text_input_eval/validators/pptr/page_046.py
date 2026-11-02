from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page046Validator(BaseValidator):
    case_id = "P046"
    task = "valid_input"

    TARGET_ID = "com.kroger.mobile:id/generic_input"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        return False