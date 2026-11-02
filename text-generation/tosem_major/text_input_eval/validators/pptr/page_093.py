import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page093Validator(BaseValidator):
    case_id = "P093"
    task = "valid_input"

    PHONE_ID = "phoneInput"

    REQUIRED_IDS = {
        PHONE_ID,
    }

    PLACEHOLDERS = {
        "generated_value",
        "generate_value",
        "value",
        "text",
        "test",
        "phone",
        "phone number",
    }

    # 支持：
    # 1) 中国手机号：13812345678 / 138 1234 5678
    # 2) 中国座机：010 1234 5678 / 021 2555 5678 / (021) 2555-1234
    # 3) 美国电话：(415) 783-9246 / 5551234567
    CN_MOBILE_RE = re.compile(r"^1[3-9]\d(?:\s?\d{4}){2}$")
    CN_LANDLINE_RE = re.compile(r"^(?:0\d{2,3}|\(0\d{2,3}\))[-\s]?\d{3,4}[-\s]?\d{4,5}$")
    US_PHONE_RE = re.compile(r"^(?:\(?[2-9]\d{2}\)?[-\s]?)\d{3}[-\s]?\d{4}$")

    INVALID_FAKE_PREFIXES = {
        "123",
        "234",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        phone = str(generated_inputs.get(self.PHONE_ID, "")).strip()

        if not phone:
            return False

        lv = phone.lower()
        if lv in self.PLACEHOLDERS:
            return False
        if "generated_value" in lv:
            return False

        # 先不接受国家码；样本里 +86 基本失败
        if phone.startswith("+"):
            return False

        digits = re.sub(r"\D", "", phone)

        # 排除明显假号，例如 (123) 456-7890 / (234) 567-8901
        if len(digits) >= 3 and digits[:3] in self.INVALID_FAKE_PREFIXES:
            return False

        if self.CN_MOBILE_RE.fullmatch(phone):
            return True

        if self.CN_LANDLINE_RE.fullmatch(phone):
            return True

        if self.US_PHONE_RE.fullmatch(phone):
            return True

        return False