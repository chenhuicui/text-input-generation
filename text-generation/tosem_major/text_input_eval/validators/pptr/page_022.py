import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page022Validator(BaseValidator):
    case_id = "P022"
    task = "valid_input"

    TARGET_ID = "search-input"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    INVALID_LITERALS = {
        "generated_value",
        "example query",
        "example search term",
        "sample keyword",
        "can't find what you read?",
        "deepstash",
        "deepstash tips",
        "deepstash's new feature",
        "book recommendations",
        "books",
        "recent articles",
        "recent article on ai",
        "recent article on ai advancements",
        "recent article on artificial intelligence",
        "deepstash article on productivity hacks",
    }

    POSITIVE_PATTERNS = [
        re.compile(r"\bproductivity\b", re.IGNORECASE),
        re.compile(r"\bmotivation\b", re.IGNORECASE),
        re.compile(r"\binspiration\b", re.IGNORECASE),
        re.compile(r"\bpersonal growth\b", re.IGNORECASE),
        re.compile(r"\btime management\b", re.IGNORECASE),
        re.compile(r"\bartificial intelligence\b", re.IGNORECASE),
        re.compile(r"\bmachine learning\b", re.IGNORECASE),
        re.compile(r"\bdeep learning\b", re.IGNORECASE),
        re.compile(r"\barticle\b", re.IGNORECASE),
        re.compile(r"\blove\b", re.IGNORECASE),
        re.compile(r"\bromantic\b", re.IGNORECASE),
    ]

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID).strip()
        value_lower = value.lower()

        if value == "":
            return False

        if value_lower in self.INVALID_LITERALS:
            return False

        return any(pattern.search(value) for pattern in self.POSITIVE_PATTERNS)