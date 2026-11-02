import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page098Validator(BaseValidator):
    case_id = "P098"
    task = "valid_input"

    PHONE_ID = "com.vkontakte.android:id/phone_edit_text"

    REQUIRED_IDS = {
        PHONE_ID,
    }

    PLACEHOLDERS = {
        "generated_value",
        "generate_value",
        "value",
        "text",
        "test",
    }

    # 10-digit phone, e.g., 4155551234, 5551234567
    US_10_DIGIT_RE = re.compile(r"^\d{10}$")

    # +1 (555) 123-4567 / +1 555 012 3456
    US_PLUS_RE = re.compile(
        r"^\+1\s*(?:\(\d{3}\)|\d{3})[-\s]?\d{3}[-\s]?\d{4}$"
    )

    # Russian mobile-like format observed as valid: +79123456789, +79161234567
    RU_PLUS_RE = re.compile(r"^\+79\d{9}$")

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        phone = str(generated_inputs.get(self.PHONE_ID, "")).strip()
        if not phone:
            return False

        lower_phone = phone.lower()
        if lower_phone in self.PLACEHOLDERS:
            return False
        if "generated_value" in lower_phone:
            return False

        if self.US_10_DIGIT_RE.fullmatch(phone):
            # 排除明显无效/占位式号码
            if phone.startswith("123"):
                return False
            return True

        if self.US_PLUS_RE.fullmatch(phone):
            # +1 123... 这类观察中基本失败
            digits = re.sub(r"\D", "", phone)
            if digits.startswith("1123"):
                return False
            return True

        if self.RU_PLUS_RE.fullmatch(phone):
            return True

        return False