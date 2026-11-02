import zipcodes

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page063Validator(BaseValidator):
    case_id = "P063"
    task = "valid_input"

    TARGET_ID = "com.olo.ihop:id/pickUpSearchBar"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    INVALID_VALUES = {
        "generated_value",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.TARGET_ID, "")).strip()
        if not value:
            return False

        if value.lower() in self.INVALID_VALUES:
            return False

        if zipcodes.matching(value):
            return True

        return any(ch.isalpha() for ch in value)