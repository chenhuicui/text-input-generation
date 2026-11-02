import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page114Validator(BaseValidator):
    case_id = "P114"
    task = "valid_input"

    EMAIL_ID = "tv.telepathic.hooked:id/emailInput"

    REQUIRED_IDS = {
        EMAIL_ID,
    }

    EMAIL_RE = re.compile(
        r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
    )

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        email = str(generated_inputs.get(self.EMAIL_ID, "")).strip()


        if not email:
            return False

        if not self.EMAIL_RE.fullmatch(email):
            return False

        return True