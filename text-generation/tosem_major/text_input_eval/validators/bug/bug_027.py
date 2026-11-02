from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug027Validator(BaseValidator):
    case_id = "B027"
    task = "invalid_bug"

    EMAIL_ID = "communi.dog.aplicatiion:id/input_email_login"
    PASSWORD_ID = "communi.dog.aplicatiion:id/user_password"

    REQUIRED_IDS = {
        EMAIL_ID,
        PASSWORD_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        email_value = self.value_by_id(generated_inputs, self.EMAIL_ID)
        password_value = self.value_by_id(generated_inputs, self.PASSWORD_ID)

        return email_value.strip() == "" or password_value.strip() == ""