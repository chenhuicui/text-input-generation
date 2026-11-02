import re

import zipcodes

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page092Validator(BaseValidator):
    case_id = "P092"
    task = "valid_input"

    SEARCH_ID = "com.thumbtack.consumer.search:id/searchField"
    ZIP_ID = "com.thumbtack.consumer.search:id/zipCodeField"

    REQUIRED_IDS = {
        SEARCH_ID,
        ZIP_ID,
    }

    PLACEHOLDERS = {
        "generated_value",
        "generate_value",
        "value",
        "text",
        "test",
        "search",
        "zip",
        "zipcode",
        "zip code",
    }

    ZIP_FORMAT_RE = re.compile(r"^\d{5}$")

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        search = str(generated_inputs.get(self.SEARCH_ID, "")).strip()
        zip_code = str(generated_inputs.get(self.ZIP_ID, "")).strip()

        if not search or not zip_code:
            return False

        for v in [search, zip_code]:
            lv = v.lower().strip()
            if lv in self.PLACEHOLDERS:
                return False
            if "generated_value" in lv:
                return False

        if not self.ZIP_FORMAT_RE.fullmatch(zip_code):
            return False

        if not zipcodes.matching(zip_code):
            return False

        return True