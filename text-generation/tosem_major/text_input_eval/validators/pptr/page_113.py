from urllib.parse import urlparse

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page113Validator(BaseValidator):
    case_id = "P113"
    task = "valid_input"

    URL_ID = "tunein.player:id/add_custom_url_id"

    REQUIRED_IDS = {
        URL_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.URL_ID, "")).strip()
        if not value:
            return False

        parsed = urlparse(value)

        if parsed.scheme not in {"http", "https"}:
            return False

        if not parsed.netloc or not parsed.hostname:
            return False

        host = parsed.hostname.lower()

        # 至少像一个域名，比如 example.com / stream.example.com
        if "." not in host:
            return False

        # 排除明显测试占位
        if "generated_value" in value.lower():
            return False
        if "testurl" in host:
            return False

        try:
            if parsed.port is not None and not (1 <= parsed.port <= 65535):
                return False
        except ValueError:
            return False

        return True