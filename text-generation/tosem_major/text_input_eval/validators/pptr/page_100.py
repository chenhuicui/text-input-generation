import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page100Validator(BaseValidator):
    case_id = "P100"
    task = "valid_input"

    SEARCH_ID = "com.wizards.winter_orb:id/search_src_text"

    REQUIRED_IDS = {
        SEARCH_ID,
    }

    PLACEHOLDERS = {
        "generated_value",
        "generate_value",
        "value",
        "text",
        "test",
        "example",
        "hello",
    }

    # 这些在样例中基本是失败：过于泛化、像提示词/类别词，而不是具体搜索词
    INVALID_EXACT = {
        "magic card",
        "magic cards",
        "magic the gathering cards",
        "magic card search term",
        "fireball spell card",
        "winter orb card",
        "mtg",
        "outdoor gear",
        "nearby restaurants",
    }

    INVALID_CONTAINS = {
        "spell card",
        "card search term",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.SEARCH_ID, "")).strip()
        if not value:
            return False

        normalized = re.sub(r"\s+", " ", value.lower()).strip()

        if normalized in self.PLACEHOLDERS:
            return False

        if "generated_value" in normalized:
            return False

        if normalized in self.INVALID_EXACT:
            return False

        for bad in self.INVALID_CONTAINS:
            if bad in normalized:
                return False

        return True