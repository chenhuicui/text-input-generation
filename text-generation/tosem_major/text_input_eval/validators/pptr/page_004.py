from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page004Validator(BaseValidator):
    case_id = "P004"
    task = "valid_input"

    TARGET_ID = "ai.socialapps.speakmaster:id/edit_text"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID)

        return value.strip() != ""