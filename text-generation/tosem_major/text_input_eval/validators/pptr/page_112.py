import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page112Validator(BaseValidator):
    case_id = "P112"
    task = "valid_input"

    SEARCH_ID = "sanity.podcast.freak:id/search_bar_text"

    REQUIRED_IDS = {
        SEARCH_ID,
    }

    PLACEHOLDERS = {
        "generated_value",
        "generate_value",
        "value",
        "text",
        "test",
    }

    INVALID_EXACT = {
        "generated_value",
        "podcast episode 123",
        "testpodcast123",
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

        # 明显的测试/编号型伪搜索词
        if re.fullmatch(r"[a-z]*test[a-z]*\d*", normalized):
            return False

        return True