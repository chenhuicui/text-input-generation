from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page056Validator(BaseValidator):
    case_id = "P056"
    task = "valid_input"

    PASSWORD_ID = "com.myfitnesspal.android:id/newPassword"
    RETYPE_PASSWORD_ID = "com.myfitnesspal.android:id/retypePassword"

    REQUIRED_IDS = {
        PASSWORD_ID,
        RETYPE_PASSWORD_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        password = str(generated_inputs.get(self.PASSWORD_ID, ""))
        retype_password = str(generated_inputs.get(self.RETYPE_PASSWORD_ID, ""))

        if " " in password or " " in retype_password:
            return False

        return len(password) >= 10 and password == retype_password