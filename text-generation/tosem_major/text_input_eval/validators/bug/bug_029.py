from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug029Validator(BaseValidator):
    case_id = "B029"
    task = "invalid_bug"

    TARGET_ID = "io.github.rosemoe.sora.app:id/editor"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        text = self.value_by_id(generated_inputs, self.TARGET_ID)
        if text is None:
            return False

        text = text.strip()
        if text == "":
            return False

        lines = text.splitlines()
        non_empty_lines = sum(1 for line in lines if line.strip() != "")
        if non_empty_lines >= 2:
            return True

        return len(text) >= 80