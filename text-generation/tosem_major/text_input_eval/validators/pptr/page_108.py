import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page108Validator(BaseValidator):
    case_id = "P108"
    task = "valid_input"

    SEARCH_ID = "net.wordbit.enes:id/view_auto_search"

    REQUIRED_IDS = {
        SEARCH_ID,
    }

    PLACEHOLDERS = {
        "generated_value",
        "generate_value",
        "value",
        "text",
        "test",
        "testword",
    }

    # 从失败样本归纳出的无效搜索词/课程名。
    # 用 contains 关系，不做完全等值匹配。
    INVALID_CONTAINS = {
        "casa",
        "amigo",
        "palabra",
        "saludar",
        "basic english",
        "básico",
        "basico",
    }

    # 允许普通单词/短词，如 hello, hola, book, house, cat, gato, example, translation, apple, learn, hel
    # 不允许 basic1 / Básico 1 (A1) 这种带数字或括号的课程/等级文本
    HAS_DIGIT_OR_BRACKET_RE = re.compile(r"[\d()（）]")

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.SEARCH_ID, "")).strip()
        if not value:
            return False

        lower = value.lower().strip()

        if lower in self.PLACEHOLDERS:
            return False

        if "generated_value" in lower:
            return False

        # 这里用包含关系：例如 "Básico 1 (A1)" 会因为包含 "básico" 失败
        for bad in self.INVALID_CONTAINS:
            if bad in lower:
                return False

        if self.HAS_DIGIT_OR_BRACKET_RE.search(value):
            return False

        return True