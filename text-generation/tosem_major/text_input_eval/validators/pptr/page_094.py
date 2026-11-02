import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page094Validator(BaseValidator):
    case_id = "P094"
    task = "valid_input"

    SEARCH_ID = "play_search_box"

    REQUIRED_IDS = {
        SEARCH_ID,
    }

    PLACEHOLDERS = {
        "generated_value",
        "generate_value",
        "value",
        "text",
        "test",
        "artist",
        "song",
        "music",
        "search",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        query = str(generated_inputs.get(self.SEARCH_ID, "")).strip()

        if not query:
            return False

        lv = query.lower()
        if lv in self.PLACEHOLDERS:
            return False
        if "generated_value" in lv:
            return False

        # 非空艺人名：允许字母、数字、空格、常见艺名符号
        if not re.search(r"[A-Za-z]", query):
            return False

        return True