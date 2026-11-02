from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page031Validator(BaseValidator):
    case_id = "P031"
    task = "valid_input"

    TARGET_ID = "com.frameme.photoeditor.collagemaker.effects:id/search_edit_text"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    INVALID_EXACT_VALUES = {
        "vintage",
        "blur",
        "vignette",
        "sepia",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID).strip()

        if value == "":
            return False

        if value.lower() in self.INVALID_EXACT_VALUES:
            return False

        return True