import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page088Validator(BaseValidator):
    case_id = "P088"
    task = "valid_input"

    TARGET_ID = "text-field-input-Email address or mobile number"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    PLACEHOLDERS = {
        "test",
        "text",
        "generated_value",
        "generate_value",
        "value",
        "email",
        "phone",
        "mobile",
    }

    EMAIL_RE = re.compile(
        r"^[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})$"
    )

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.TARGET_ID, "")).strip()
        if not value:
            return False

        lower_value = value.lower()
        if lower_value in self.PLACEHOLDERS:
            return False

        if any(ph in lower_value for ph in ["generated_value", "test", "placeholder"]):
            return False

        # Email path
        email_match = self.EMAIL_RE.match(value)
        if email_match:
            domain = email_match.group(1).lower()
            if domain == "sunrun.com":
                return False
            return True

        # Phone path
        normalized_phone = re.sub(r"[()\-\s.]", "", value)
        if normalized_phone.startswith("+"):
            digits = normalized_phone[1:]
        else:
            digits = normalized_phone

        if digits.isdigit() and len(digits) >= 10:
            return True

        return False