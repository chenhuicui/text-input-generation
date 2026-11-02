from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register
from text_input_eval.validators.common import is_email


@register
class Bug036Validator(BaseValidator):
    case_id = "B036"
    task = "invalid_bug"

    FIRST_NAME_ID = "com.eventyay.attendee:id/firstName"
    LAST_NAME_ID = "com.eventyay.attendee:id/lastName"
    EMAIL_ID_1 = "com.eventyay.attendee:id/email__bounds_42_1266_1398_1434"
    EMAIL_ID_2 = "com.eventyay.attendee:id/email__bounds_70_1644_1370_1812"

    REQUIRED_IDS = {
        FIRST_NAME_ID,
        LAST_NAME_ID,
        EMAIL_ID_1,
        EMAIL_ID_2,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        email_1 = self.value_by_id(generated_inputs, self.EMAIL_ID_1).strip()
        email_2 = self.value_by_id(generated_inputs, self.EMAIL_ID_2).strip()

        return (
            (email_1 != "" and not is_email(email_1))
            or
            (email_2 != "" and not is_email(email_2))
        )