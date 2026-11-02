from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page035Validator(BaseValidator):
    case_id = "P035"
    task = "valid_input"

    TARGET_ID = "com.golftripgenius.android.leaguegenius:id/login_edit_ggid"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    VALID_VALUES = {
        "123456",
        "987654",
        "123456789",
        "A1B2C3",
        "456789",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID).strip()

        return value in self.VALID_VALUES