import zipcodes

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page078Validator(BaseValidator):
    case_id = "P078"
    task = "valid_input"

    TARGET_ID = "com.servicemagic.consumer:id/zip_code_et"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.TARGET_ID, "")).strip()
        return bool(zipcodes.matching(value))