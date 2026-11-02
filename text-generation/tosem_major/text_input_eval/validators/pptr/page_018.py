import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page018Validator(BaseValidator):
    case_id = "P018"
    task = "valid_input"

    TARGET_ID = "com.chewy.android:id/searchInputText"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    PET_KEYWORD_RE = re.compile(
        r"\b(dog|dogs|cat|cats|puppy|puppies|kitten|kittens|pet|pets)\b",
        re.IGNORECASE,
    )

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID).strip()

        return value != "" and bool(self.PET_KEYWORD_RE.search(value))
