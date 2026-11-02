# bug_018.py
from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug018Validator(BaseValidator):
    case_id = "B018"
    task = "invalid_bug"

    FIRST_NAME_ID = "mo.must.maskededittextbug:id/first_name"
    LAST_NAME_ID = "mo.must.maskededittextbug:id/last_name"
    EMAIL_ID = "mo.must.maskededittextbug:id/email"
    PHONE_ID = "mo.must.maskededittextbug:id/phone"

    REQUIRED_IDS = {
        FIRST_NAME_ID,
        LAST_NAME_ID,
        EMAIL_ID,
        PHONE_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        # 只要任意字段包含非数字字符，就判触发
        for field_id in self.REQUIRED_IDS:
            value = self.value_by_id(generated_inputs, field_id)
            if value != "" and (not value.isdigit()):
                return True

        return False