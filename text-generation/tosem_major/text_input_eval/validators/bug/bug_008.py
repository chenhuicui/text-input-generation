from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug008Validator(BaseValidator):
    case_id = "B008"
    task = "invalid_bug"

    TARGET_ID = "android:id/input"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        # 必须包含所有要求的 id，否则直接失败
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID).strip()

        # bug 条件：
        # 输入非数字 app-op 名称（如 WRITE_SETTINGS）会触发
        return value != "" and (not value.isdigit())