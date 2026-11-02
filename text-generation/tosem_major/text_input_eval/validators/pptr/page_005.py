from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page005Validator(BaseValidator):
    case_id = "P005"
    task = "valid_input"

    TITLE_ID = "app.hallow.android:id/title_edit_text"
    JOURNAL_ID = "app.hallow.android:id/journal_edit_text"

    REQUIRED_IDS = {
        TITLE_ID,
        JOURNAL_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        journal_value = self.value_by_id(generated_inputs, self.JOURNAL_ID)

        return journal_value.strip() != ""