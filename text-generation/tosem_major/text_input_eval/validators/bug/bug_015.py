from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug015Validator(BaseValidator):
    case_id = "B015"
    task = "invalid_bug"

    TARGET_ID = "no_id_EditText_48_283_1392_441"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        # 比 Java 多一层保护：必须包含要求的 id
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID)

        # 采用 Java 的核心逻辑：
        # 输入字符串仅为 "[" 时触发
        return len(value) == 1 and value == "["