from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page109Validator(BaseValidator):
    case_id = "P109"
    task = "valid_input"

    TITLE_ID = "notes.notepad.checklist.calendar.todolist.notebook:id/tv_title"
    NOTE_ID = "notes.notepad.checklist.calendar.todolist.notebook:id/et_note"

    REQUIRED_IDS = {
        TITLE_ID,
        NOTE_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        title = str(generated_inputs.get(self.TITLE_ID, "")).strip()
        note = str(generated_inputs.get(self.NOTE_ID, "")).strip()

        return bool(title or note)