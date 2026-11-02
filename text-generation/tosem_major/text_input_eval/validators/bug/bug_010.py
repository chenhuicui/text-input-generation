from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug010Validator(BaseValidator):
    case_id = "B010"
    task = "invalid_bug"

    TARGET_ID = "fr.neamar.kiss:id/searchEditText"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID)

        if len(value) < 2:
            return False

        for i, ch in enumerate(value[:-1]):
            if ch in {"a", "A"} and value[i + 1].isalnum():
                return True

        return False