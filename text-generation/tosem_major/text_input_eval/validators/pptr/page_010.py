from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page010Validator(BaseValidator):
    case_id = "P010"
    task = "valid_input"

    TITLE_ID = "co.fable.fable:id/list_title_edit_text"
    DESCRIPTION_ID = "co.fable.fable:id/list_description_edit_text"

    REQUIRED_IDS = {
        TITLE_ID,
        DESCRIPTION_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        title_value = self.value_by_id(generated_inputs, self.TITLE_ID).strip()

        return title_value != ""