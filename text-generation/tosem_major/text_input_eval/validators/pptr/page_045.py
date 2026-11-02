from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page045Validator(BaseValidator):
    case_id = "P045"
    task = "valid_input"

    TITLE_ID = "com.kajda.fuelio:id/titleReminder"
    NOTES_ID = "com.kajda.fuelio:id/reminder_notes"
    REPEAT_ODO_ID = "com.kajda.fuelio:id/repeatOdoRemindUi"
    REPEAT_MONTHS_ID = "com.kajda.fuelio:id/repeatMonthsUi"
    INITIAL_ODO_ID = "com.kajda.fuelio:id/initialOdo"

    REQUIRED_IDS = {
        TITLE_ID,
        NOTES_ID,
        REPEAT_ODO_ID,
        REPEAT_MONTHS_ID,
        INITIAL_ODO_ID,
    }

    def _is_nonnegative_int_or_empty(self, value: str) -> bool:
        value = value.strip()
        return value == "" or value.isdigit()

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        title_value = self.value_by_id(generated_inputs, self.TITLE_ID).strip()
        repeat_odo_value = self.value_by_id(generated_inputs, self.REPEAT_ODO_ID)
        repeat_months_value = self.value_by_id(generated_inputs, self.REPEAT_MONTHS_ID)
        initial_odo_value = self.value_by_id(generated_inputs, self.INITIAL_ODO_ID)

        if title_value == "":
            return False

        if not self._is_nonnegative_int_or_empty(repeat_odo_value):
            return False
        if not self._is_nonnegative_int_or_empty(repeat_months_value):
            return False
        if not self._is_nonnegative_int_or_empty(initial_odo_value):
            return False

        return True