from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug001Validator(BaseValidator):
    case_id = "B001"
    task = "invalid_bug"

    EMAIL_ID = "com.example.unserhoersaal:id/loginFragmentUserEmailEditText"
    PASSWORD_ID = "com.example.unserhoersaal:id/loginFragmentPasswordEditText"

    REQUIRED_IDS = {
        EMAIL_ID,
        PASSWORD_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        # 必须包含所有要求的 id，否则直接失败
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        email_value = self.value_by_id(generated_inputs, self.EMAIL_ID)
        password_value = self.value_by_id(generated_inputs, self.PASSWORD_ID)

        # 只要任意一个字段为空，就判定触发 bug
        return (email_value == "") or (password_value == "")