import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page019Validator(BaseValidator):
    case_id = "P019"
    task = "valid_input"

    TARGET_ID = "com.citymapper.app.release:id/search_box"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    INVALID_LITERAL_VALUES = {
        "generated_value",
    }

    # 允许常见地点名字符：
    # 字母、数字、空格、逗号、点、撇号、连字符、括号
    PLACE_RE = re.compile(r"^[A-Za-z0-9 ,.'()\-]+$")

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID).strip()

        if value == "":
            return False

        if value.lower() in self.INVALID_LITERAL_VALUES:
            return False

        if not self.PLACE_RE.fullmatch(value):
            return False

        # 至少要包含一个字母，避免纯符号/纯数字
        if not any(ch.isalpha() for ch in value):
            return False

        return True