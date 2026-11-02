from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page003Validator(BaseValidator):
    case_id = "P003"
    task = "valid_input"

    TARGET_ID = "ai.photo.enhancer.photoclear:id/et_input"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID)
        non_space_len = len(value.replace(" ", ""))

        return non_space_len >= 6