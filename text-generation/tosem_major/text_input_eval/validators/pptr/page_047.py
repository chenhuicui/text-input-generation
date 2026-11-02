from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page047Validator(BaseValidator):
    case_id = "P047"
    task = "valid_input"

    TARGET_ID = "com.kw.literie:id/etSearch"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    VALID_TERMS = {
        "werewolf romance",
        "fiction",
        "lighting",
        "fantasy",
        "love",
        "romance",
        "craving the wrong brother",
        "novel",
        "bed",
        "mystery",
        "sample",
        "alpha",
        "bride of the cursed alpha",
        "romance novel",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.TARGET_ID, "")).strip().lower()
        return value in self.VALID_TERMS