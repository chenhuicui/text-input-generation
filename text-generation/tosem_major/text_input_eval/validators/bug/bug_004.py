from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug004Validator(BaseValidator):
    case_id = "B004"
    task = "invalid_bug"

    TARGET_ID = "org.mozc.android.inputmethod.japanese:id/user_dictionary_tool_dictionary_name_dialog_name"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID).strip()

        return "\u3081" in value