from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug009Validator(BaseValidator):
    case_id = "B009"
    task = "invalid_bug"

    TARGET_ID = "android:id/edit"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        # 必须包含所有要求的 id，否则直接失败
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID)

        # bug 条件：
        # 输入中包含 ":" 会触发
        return ":" in value