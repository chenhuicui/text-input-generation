from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register
from text_input_eval.validators.common import is_email


@register
class Page042Validator(BaseValidator):
    case_id = "P042"
    task = "valid_input"

    TARGET_ID = "com.immediasemi.android.blink:id/email_address"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    INVALID_DOMAIN_KEYWORDS = {
        "example",
        "test",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID).strip()

        if not is_email(value):
            return False

        _, domain = value.rsplit("@", 1)
        domain_lower = domain.lower()

        return not any(keyword in domain_lower for keyword in self.INVALID_DOMAIN_KEYWORDS)