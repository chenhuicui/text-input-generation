from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page032Validator(BaseValidator):
    case_id = "P032"
    task = "valid_input"

    TARGET_ID = "searchBar"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    INVALID_LITERAL_VALUES = {
        "generated_value",
        "team, performer, or venue",
        "local sports teams",
    }

    INVALID_EXACT_VALUES = {
        "lionel messi",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID).strip()
        value_lower = value.lower()

        if value == "":
            return False

        if value_lower in self.INVALID_LITERAL_VALUES:
            return False

        if value_lower in self.INVALID_EXACT_VALUES:
            return False

        return True