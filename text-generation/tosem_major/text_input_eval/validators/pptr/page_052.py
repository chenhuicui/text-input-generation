from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register

import pycountry


@register
class Page052Validator(BaseValidator):
    case_id = "P052"
    task = "valid_input"

    TARGET_ID = "com.mobillium.airalo:id/text_input_search"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    COUNTRY_ALIASES = {
        "usa": "United States",
        "us": "United States",
        "u.s.": "United States",
        "u.s.a.": "United States",
        "america": "United States",
        "uk": "United Kingdom",
        "u.k.": "United Kingdom",
        "england": "United Kingdom",
        "uae": "United Arab Emirates",
        "korea": "South Korea",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.TARGET_ID, "")).strip()
        if not value:
            return False

        value = self.COUNTRY_ALIASES.get(value.lower(), value)

        try:
            pycountry.countries.lookup(value)
            return True
        except LookupError:
            return False