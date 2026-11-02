from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug014Validator(BaseValidator):
    case_id = "B014"
    task = "invalid_bug"

    TARGET_ID = "fr.gaulupeau.apps.InThePoche:id/search_src_text"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID)
        s = value.strip()

        if not s:
            return False

        if '"' in s:
            return True

        if s.startswith("-") and len(s) > 1 and (not s[1].isspace()):
            return True

        return False