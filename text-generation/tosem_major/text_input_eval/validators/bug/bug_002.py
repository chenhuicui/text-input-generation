# bug_002.py
import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug002Validator(BaseValidator):
    case_id = "B002"
    task = "invalid_bug"

    AMOUNT_ID = "org.ebur.debitum:id/edit_amount"
    DESCRIPTION_ID = "org.ebur.debitum:id/edit_description"
    DATE_ID = "org.ebur.debitum:id/edit_date_editText"

    REQUIRED_IDS = {
        AMOUNT_ID,
        DESCRIPTION_ID,
        DATE_ID,
    }

    # 只允许数字、空格、最多一个小数点
    AMOUNT_ALLOWED_RE = re.compile(r"^\d[\d ]*(\.\d+)?$")

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        amount_value = self.value_by_id(generated_inputs, self.AMOUNT_ID).strip()

        if not amount_value:
            return False

        # 必须是带空格分组的格式
        if " " not in amount_value:
            return False

        # 不能出现逗号、货币符号、字母等
        if not self.AMOUNT_ALLOWED_RE.fullmatch(amount_value):
            return False

        normalized = amount_value.replace(" ", "")

        try:
            return float(normalized) > 999
        except ValueError:
            return False