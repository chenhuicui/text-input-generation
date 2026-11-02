import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page009Validator(BaseValidator):
    case_id = "P009"
    task = "valid_input"

    TARGET_ID = "bible.offline.kingjames.holy.kjv.bible.daily.verse:id/search_src_text"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    SINGLE_WORD_RE = re.compile(r"^[A-Za-z]+$")

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID).strip()

        return bool(self.SINGLE_WORD_RE.fullmatch(value))