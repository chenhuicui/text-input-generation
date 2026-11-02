import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug012Validator(BaseValidator):
    case_id = "B012"
    task = "invalid_bug"

    FIELD_1 = "no_id_EditText_244_486_1196_654"
    FIELD_2 = "no_id_EditText_244_804_1196_972"
    FIELD_3 = "no_id_EditText_244_1122_1196_1290"
    FIELD_4 = "no_id_EditText_244_1440_1196_1608"

    REQUIRED_IDS = {
        FIELD_1,
        FIELD_2,
        FIELD_3,
        FIELD_4,
    }

    # 任意单词以小写 auto 开头
    PATTERN = re.compile(r"(^|\s)auto\w*")

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        for field_id in self.REQUIRED_IDS:
            value = self.value_by_id(generated_inputs, field_id)
            if value and self.PATTERN.search(value):
                return True

        return False