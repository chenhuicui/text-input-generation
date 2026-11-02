from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page111Validator(BaseValidator):
    case_id = "P111"
    task = "valid_input"

    SEARCH_ID = "org.wikipedia:id/search_src_text"

    REQUIRED_IDS = {
        SEARCH_ID,
    }

    VALID_TERMS = {
        # English
        "english",
        "eng",
        "en",

        # Chinese
        "chinese",
        "chi",
        "chin",
        "中文",

        # Other observed valid localized language names / abbreviations
        "español",
        "português",
        "ita",
    }

    PLACEHOLDERS = {
        "generated_value",
        "generate_value",
        "value",
        "text",
        "test",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.SEARCH_ID, "")).strip()
        if not value:
            return False

        normalized = value.lower().strip()

        if normalized in self.PLACEHOLDERS:
            return False
        if "generated_value" in normalized:
            return False

        return normalized in self.VALID_TERMS