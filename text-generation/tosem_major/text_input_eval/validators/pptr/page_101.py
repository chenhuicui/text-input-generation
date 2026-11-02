from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page101Validator(BaseValidator):
    case_id = "P101"
    task = "valid_input"

    EDIT_ID = "android:id/edit"

    REQUIRED_IDS = {
        EDIT_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        # JSON 结构仍然要求满足
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.EDIT_ID, "")).strip()
        return bool(value) and value.lower() not in {"generated_value", "test", "none", "null"}
