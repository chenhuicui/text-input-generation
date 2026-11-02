from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page066Validator(BaseValidator):
    case_id = "P066"
    task = "valid_input"

    TARGET_ID = "com.opentable:id/phone_input_field"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    ALLOWED_CHARS = set("0123456789+-() ")

    INVALID_EXACT_DIGITS = {
        "2123456789",
    }

    def _normalize_us_number(self, value: str) -> str | None:
        value = str(value).strip()
        if not value:
            return None

        if any(ch not in self.ALLOWED_CHARS for ch in value):
            return None

        digits = "".join(ch for ch in value if ch.isdigit())
        if not digits:
            return None

        if value.startswith("+"):
            if len(digits) != 11 or not digits.startswith("1"):
                return None
            return digits[1:]

        if len(digits) == 10:
            return digits

        return None

    def _is_valid_nanp_like(self, number: str) -> bool:
        if number in self.INVALID_EXACT_DIGITS:
            return False

        area = number[:3]
        prefix = number[3:6]

        if area in {"123", "555"}:
            return False

        if prefix == "123":
            return False

        if area[0] in {"0", "1"}:
            return False

        if prefix[0] in {"0", "1"}:
            return False

        return True

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        number = self._normalize_us_number(generated_inputs.get(self.TARGET_ID, ""))
        if number is None:
            return False

        return self._is_valid_nanp_like(number)