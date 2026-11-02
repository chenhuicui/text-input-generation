from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page054Validator(BaseValidator):
    case_id = "P054"
    task = "valid_input"

    TARGET_ID = "com.musicplayer.playermusic:id/etName"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        return False