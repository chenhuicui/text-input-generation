import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class PageXXXValidator(BaseValidator):
    case_id = "P082"
    task = "valid_input"

    COST_ID = "Screen.ProductPricingScreen.costPerItemInput"
    PRICE_ID = "Screen.ProductPricingScreen.productPricingInput"
    COMPARED_ID = "Screen.ProductPricingScreen.comparedAtPriceInput"

    REQUIRED_IDS = {
        COST_ID,
        PRICE_ID,
        COMPARED_ID,
    }

    PRICE_RE = re.compile(r"^(?:HK\$)?\d+(?:\.\d{1,2})?$", re.IGNORECASE)

    INVALID_VALUES = {
        "",
        "generated_value",
        "generated-value",
        "generated value",
        "generatedvalue",
    }

    def _is_valid_price(self, value: str) -> bool:
        value = str(value or "").strip()
        if value.lower() in self.INVALID_VALUES:
            return False
        return bool(self.PRICE_RE.fullmatch(value))

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        values = [
            generated_inputs.get(self.COST_ID, ""),
            generated_inputs.get(self.PRICE_ID, ""),
            generated_inputs.get(self.COMPARED_ID, ""),
        ]

        # 任意一个字段是合法价格即可通过
        return any(self._is_valid_price(v) for v in values)