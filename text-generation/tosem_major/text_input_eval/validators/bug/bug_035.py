from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug035Validator(BaseValidator):
    case_id = "B035"
    task = "invalid_bug"

    TARGET_ID = "com.eventyay.attendee:id/discountCodeEditText"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False
        value = self.value_by_id(generated_inputs, self.TARGET_ID)

        return ("\n" in value) or ("\r" in value)
