from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page085Validator(BaseValidator):
    case_id = "P085"
    task = "valid_input"

    TARGET_ID = "com.smule.singandroid:id/search_edit_text"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    BAD_PHRASES = [
        "generated_value",
        "hypothetical",
        "hit song",
        "hit songs",
        "top song",
        "top songs",
        "song list",
        "songs list",
        "recent top songs",
        "recent hits",
        "latest hits",
        "best songs",
        "popular songs",
    ]

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.TARGET_ID, "")).strip()
        if not value:
            return False

        lower = value.lower().strip()
        if any(phrase in lower for phrase in self.BAD_PHRASES):
            return False

        return True