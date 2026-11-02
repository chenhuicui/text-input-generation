from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page086Validator(BaseValidator):
    case_id = "P086"
    task = "valid_input"

    TARGET_ID = "com.snowcorp.stickerly.android:id/searchEdit"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    PLACEHOLDERS = {
        "generated_value",
        "generate_value",
        "value",
        "test",
        "text",
        "",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.TARGET_ID, "")).strip()
        if not value:
            return False

        if value.lower() in self.PLACEHOLDERS:
            return False

        return True