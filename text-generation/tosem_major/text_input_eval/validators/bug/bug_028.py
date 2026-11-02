from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug028Validator(BaseValidator):
    case_id = "B028"
    task = "invalid_bug"

    TARGET_ID = "com.faanggang.wisetrack:id/activity_search_search_edit_text"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        # This predicate intentionally distinguishes one literal space from an
        # empty string.  BaseValidator.value_by_id normalizes with strip(), so
        # read the frozen JSON string directly for this case.
        value = generated_inputs.get(self.TARGET_ID)

        return value == " "
