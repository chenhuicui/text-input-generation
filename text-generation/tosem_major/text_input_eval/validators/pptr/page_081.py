import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page081Validator(BaseValidator):
    case_id = "P081"
    task = "valid_input"

    TRACKING_ID = "TRACKING_NUMBER_INPUT"
    DELIVERY_ID = "DELIVERY_NAME_INPUT"
    CARRIER_ID = "CARRIER_NAME_INPUT"

    REQUIRED_IDS = {
        TRACKING_ID,
        DELIVERY_ID,
        CARRIER_ID,
    }

    VALID_CARRIERS = {
        "ups",
        "fedex",
        "usps",
        "global express",
    }

    INVALID_VALUES = {
        "",
        "generated_value",
        "generated-value",
        "generated value",
        "generatedvalue",
        "test carrier",
        "example carrier",
    }

    TRACKING_RE = re.compile(r"^[A-Za-z0-9]{6,30}$")

    def _norm(self, value: str) -> str:
        return str(value or "").strip()

    def _is_invalid_literal(self, value: str) -> bool:
        return self._norm(value).lower() in self.INVALID_VALUES

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        carrier = self._norm(generated_inputs.get(self.CARRIER_ID, ""))
        tracking = self._norm(generated_inputs.get(self.TRACKING_ID, ""))
        delivery = self._norm(generated_inputs.get(self.DELIVERY_ID, ""))

        # carrier 必须是固定可识别运营商
        if carrier.lower() not in self.VALID_CARRIERS:
            return False

        # tracking number 必须非空、非 generated_value，且像 tracking code
        if self._is_invalid_literal(tracking):
            return False

        if not self.TRACKING_RE.fullmatch(tracking):
            return False

        # delivery name 可为空；但如果填了，不能是 generated/test/example 这类占位值
        if delivery and self._is_invalid_literal(delivery):
            return False

        return True