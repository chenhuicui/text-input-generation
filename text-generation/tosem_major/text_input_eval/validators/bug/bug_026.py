from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register
from text_input_eval.validators.common import is_email


@register
class Bug026Validator(BaseValidator):
    case_id = "B026"
    task = "invalid_bug"

    TO_ID = "com.fsck.k9:id/to"
    SUBJECT_ID = "com.fsck.k9:id/subject"
    MESSAGE_ID = "com.fsck.k9:id/message_content"

    REQUIRED_IDS = {
        TO_ID,
        SUBJECT_ID,
        MESSAGE_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        recipient = self.value_by_id(generated_inputs, self.TO_ID).strip()

        return recipient != "" and (not is_email(recipient))