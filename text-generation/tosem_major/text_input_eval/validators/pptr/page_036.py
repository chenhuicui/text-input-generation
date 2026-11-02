import re
from urllib.parse import urlparse

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page036Validator(BaseValidator):
    case_id = "P036"
    task = "valid_input"

    TITLE_ID = "wish-form-title-input"
    PRICE_ID = "wish-form-price-input"
    DESCRIPTION_ID = "wish-form-description-input"
    URL_ID = "wish-form-url-input"

    REQUIRED_IDS = {
        TITLE_ID,
        PRICE_ID,
        DESCRIPTION_ID,
        URL_ID,
    }

    PRICE_RE = re.compile(r"^\d+(?:\.\d+)?$")

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        title = self.value_by_id(generated_inputs, self.TITLE_ID).strip()
        price = self.value_by_id(generated_inputs, self.PRICE_ID).strip()
        url = self.value_by_id(generated_inputs, self.URL_ID).strip()

        if title == "":
            return False

        if not self._is_valid_url(url):
            return False

        if price != "" and not self.PRICE_RE.fullmatch(price):
            return False

        return True

    @staticmethod
    def _is_valid_url(value: str) -> bool:
        if value == "":
            return False
        try:
            parsed = urlparse(value)
            return parsed.scheme in {"http", "https"} and parsed.netloc != ""
        except Exception:
            return False