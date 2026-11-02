from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page048Validator(BaseValidator):
    case_id = "P048"
    task = "valid_input"

    TARGET_ID = "personal-info-input"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    BLOCKED_DOMAIN_KEYWORDS = {
        "example",
        "test",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.TARGET_ID, "")).strip()
        if not self.is_email(value):
            return False

        domain = value.rsplit("@", 1)[-1].lower()
        return not any(keyword in domain for keyword in self.BLOCKED_DOMAIN_KEYWORDS)