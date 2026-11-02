from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page055Validator(BaseValidator):
    case_id = "P055"
    task = "valid_input"

    TARGET_ID = "com.mxtech.videoplayer.ad:id/edit"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.TARGET_ID, "")).strip()
        return bool(value)