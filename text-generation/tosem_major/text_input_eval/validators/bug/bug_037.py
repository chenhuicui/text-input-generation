from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug037Validator(BaseValidator):
    case_id = "B037"
    task = "invalid_bug"

    RECOVERY_TOKEN_ID = "com.eventyay.organizer:id/recoveryToken"
    NEW_PASSWORD_ID = "com.eventyay.organizer:id/newPassword"
    CONFIRM_PASSWORD_ID = "com.eventyay.organizer:id/confirmPassword"

    REQUIRED_IDS = {
        RECOVERY_TOKEN_ID,
        NEW_PASSWORD_ID,
        CONFIRM_PASSWORD_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        new_password = self.value_by_id(generated_inputs, self.NEW_PASSWORD_ID)
        confirm_password = self.value_by_id(generated_inputs, self.CONFIRM_PASSWORD_ID)

        return new_password != confirm_password