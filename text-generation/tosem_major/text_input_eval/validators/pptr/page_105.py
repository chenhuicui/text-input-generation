from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page105Validator(BaseValidator):
    case_id = "P105"
    task = "valid_input"

    SEARCH_ID = "com.zennioptical.app:id/search_edit_text"

    REQUIRED_IDS = {
        SEARCH_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.SEARCH_ID, "")).strip()

        return bool(value)