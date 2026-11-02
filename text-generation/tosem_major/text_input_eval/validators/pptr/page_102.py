from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page102Validator(BaseValidator):
    case_id = "P102"
    task = "valid_input"

    NAME_ID = "com.wondershare.famisafe:id/et_name"

    REQUIRED_IDS = {
        NAME_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        name = str(generated_inputs.get(self.NAME_ID, "")).strip()

        return bool(name)