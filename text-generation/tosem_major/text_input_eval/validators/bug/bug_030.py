from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug030Validator(BaseValidator):
    case_id = "B030"
    task = "invalid_bug"

    TARGET_ID = "com.example.healthlog:id/loginActivity_login_editText"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        hospital_id = self.value_by_id(generated_inputs, self.TARGET_ID)

        return hospital_id.strip() == ""