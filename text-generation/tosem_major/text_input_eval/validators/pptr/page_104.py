from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page104Validator(BaseValidator):
    case_id = "P104"
    task = "valid_input"

    # The frozen prompt serialises the duplicate first/last-name resource ID
    # as one JSON key.  The historical executor therefore addresses the first
    # matching editor and leaves the already populated last name unchanged.
    FIELD_ID = "com.yelp.android.biz:id/full_bleed_edit_text"

    REQUIRED_IDS = {
        FIELD_ID,
    }

    PLACEHOLDERS = {
        "generated_value",
        "generate_value",
        "value",
        "text",
        "test",
        "none",
        "null",
    }

    def _valid_text(self, value) -> bool:
        s = str(value).strip()
        if not s:
            return False

        low = s.lower()
        if low in self.PLACEHOLDERS:
            return False
        if "generated_value" in low:
            return False

        return True

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        return self._valid_text(generated_inputs.get(self.FIELD_ID, ""))
