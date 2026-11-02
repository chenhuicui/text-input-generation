from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page068Validator(BaseValidator):
    case_id = "P068"
    task = "valid_input"

    TARGET_ID = "com.podbean.app.podcast:id/pop_et_epi_title"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    INVALID_VALUES = {
        "generated_value",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.TARGET_ID, "")).strip()
        if not value:
            return False

        return value.lower() not in self.INVALID_VALUES