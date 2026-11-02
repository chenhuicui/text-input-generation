import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page097Validator(BaseValidator):
    case_id = "P097"
    task = "valid_input"

    SUBTITLE_ID = "com.virtual.video.i18n:id/etSubtitleInput"

    REQUIRED_IDS = {
        SUBTITLE_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.SUBTITLE_ID, "")).strip()
        if not value:
            return False

        return True