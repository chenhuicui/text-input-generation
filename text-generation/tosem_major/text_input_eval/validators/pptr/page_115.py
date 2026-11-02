import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page115Validator(BaseValidator):
    case_id = "P115"
    task = "valid_input"

    EMAIL_ID = "us.mitene:id/email"
    PASSWORD_ID = "us.mitene:id/password"

    REQUIRED_IDS = {
        EMAIL_ID,
        PASSWORD_ID,
    }

    EMAIL_RE = re.compile(
        r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
    )

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        email = str(generated_inputs.get(self.EMAIL_ID, "")).strip()
        password = str(generated_inputs.get(self.PASSWORD_ID, "")).strip()

        if not email or not password:
            return False

        if not self.EMAIL_RE.fullmatch(email):
            return False

        # 从样本看，密码只需要非空且长度达到常见注册最低要求；
        # 不强制必须包含特殊字符，因为 Password123 / Test1234 / Pass1234 均通过。
        if len(password) < 8:
            return False

        return True