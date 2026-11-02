import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page053Validator(BaseValidator):
    case_id = "P053"
    task = "valid_input"

    TARGET_ID = "Explore search input"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

    INVALID_EXACT_VALUES = {
        "bohemian rhapsody",
        "beethoven moonlight sonata",
        "unlock free trial",
        "free trial activation",
        "generated_value",
        "explorenew",
        "classical music for piano",
    }

    INVALID_KEYWORDS = {
        "nba",
        "basketball",
        "champions league",
        "finals",
        "scores",
        "score",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.TARGET_ID, "")).strip()
        if not value:
            return False

        value_lower = value.lower()

        if self.YEAR_RE.search(value_lower):
            return False

        if value_lower in self.INVALID_EXACT_VALUES:
            return False

        if any(keyword in value_lower for keyword in self.INVALID_KEYWORDS):
            return False

        return True