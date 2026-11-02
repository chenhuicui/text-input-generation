from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page008Validator(BaseValidator):
    case_id = "P008"
    task = "valid_input"

    TARGET_ID = "audioeditor.musiceditor.soundeditor.songeditor:id/et_search"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        return self.has_non_empty_value(generated_inputs, self.TARGET_ID)
