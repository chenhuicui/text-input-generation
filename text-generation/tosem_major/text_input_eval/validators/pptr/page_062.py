from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page062Validator(BaseValidator):
    case_id = "P062"
    task = "valid_input"

    USERNAME_ID = "com.okta.android.auth:id/username"
    SECRET_ID = "com.okta.android.auth:id/secret"

    REQUIRED_IDS = {
        USERNAME_ID,
        SECRET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        username = self.value_by_id(generated_inputs, self.USERNAME_ID)
        secret = self.value_by_id(generated_inputs, self.SECRET_ID)
        return bool(username) and len(secret) >= 6
