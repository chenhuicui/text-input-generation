import re

import zipcodes

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page040Validator(BaseValidator):
    case_id = "P040"
    task = "valid_input"

    TARGET_ID = "com.hoopladigital.android:id/search"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    TEXT_RE = re.compile(r"^[A-Za-z0-9 ,.'&-]+$")

    INVALID_LITERAL_VALUES = {
        "generated_value",
        "example",
        "movie title or book name",
        "test search query",
        "popular books",
        "digital marketing",
        "johndoe123",
        "fiction",
        "harry potter",
        "to kill a mockingbird",
    }

    INVALID_EXACT_LIBRARY_NAMES = {
        "central library",
        "central public library",
        "main street library",
        "main public library",
        "my city library",
    }

    def _is_zipcode(self, value: str) -> bool:
        return bool(zipcodes.matching(value))

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID).strip()
        value_lower = value.lower()

        if not value:
            return False

        if value_lower in self.INVALID_LITERAL_VALUES:
            return False

        if value_lower in self.INVALID_EXACT_LIBRARY_NAMES:
            return False

        if self._is_zipcode(value):
            return True

        if not self.TEXT_RE.fullmatch(value):
            return False

        if "library" in value_lower:
            prefixes = {"central", "main", "my city"}
            return not any(
                value_lower == f"{prefix} library"
                or value_lower == f"{prefix} public library"
                for prefix in prefixes
            )

        return True