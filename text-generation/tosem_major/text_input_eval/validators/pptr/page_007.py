from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page007Validator(BaseValidator):
    case_id = "P007"
    task = "valid_input"

    TARGET_ID = "ion-input-0"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        return False