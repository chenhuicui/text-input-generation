from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page023Validator(BaseValidator):
    case_id = "P023"
    task = "valid_input"

    LOGIN_ID = "com.duolingo:id/loginView"
    PASSWORD_ID = "com.duolingo:id/passwordView"

    REQUIRED_IDS = {
        LOGIN_ID,
        PASSWORD_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        login = self.value_by_id(generated_inputs, self.LOGIN_ID)
        password = self.value_by_id(generated_inputs, self.PASSWORD_ID)
        return len(login) >= 3 and len(password) >= 6
