from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register
from text_input_eval.validators.common import is_email


@register
class Page012Validator(BaseValidator):
    case_id = "P012"
    task = "valid_input"

    SUBJECT_ID = "com.applabstudios.ai.mail.homescreen.inbox:id/et_email_composer_subject"
    EDITOR_ID = "editor"
    TO_ID = "com.applabstudios.ai.mail.homescreen.inbox:id/auto_tv_email_composer_contact"
    CC_ID = "com.applabstudios.ai.mail.homescreen.inbox:id/auto_tv_email_componser_cc_contact"
    BCC_ID = "com.applabstudios.ai.mail.homescreen.inbox:id/auto_tv_email_componser_bcc_contact"

    REQUIRED_IDS = {
        SUBJECT_ID,
        EDITOR_ID,
        TO_ID,
        CC_ID,
        BCC_ID,
    }

    @staticmethod
    def _is_email_list(value: str) -> bool:
        value = value.strip()
        if value == "":
            return False

        parts = [part.strip() for part in value.split(",")]
        if any(part == "" for part in parts):
            return False

        return all(is_email(part) for part in parts)

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        subject = self.value_by_id(generated_inputs, self.SUBJECT_ID).strip()
        editor = self.value_by_id(generated_inputs, self.EDITOR_ID).strip()
        to_value = self.value_by_id(generated_inputs, self.TO_ID).strip()
        cc_value = self.value_by_id(generated_inputs, self.CC_ID).strip()
        bcc_value = self.value_by_id(generated_inputs, self.BCC_ID).strip()

        if subject == "" or editor == "":
            return False

        if not self._is_email_list(to_value):
            return False

        if cc_value != "" and not self._is_email_list(cc_value):
            return False

        if bcc_value != "" and not self._is_email_list(bcc_value):
            return False

        return True