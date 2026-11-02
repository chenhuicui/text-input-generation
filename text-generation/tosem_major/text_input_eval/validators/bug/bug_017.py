from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug017Validator(BaseValidator):
    case_id = "B017"
    task = "invalid_bug"

    FROM_ID = "it.sasabz.android.sasabus:id/from_text"
    TO_ID = "it.sasabz.android.sasabus:id/to_text"

    REQUIRED_IDS = {
        FROM_ID,
        TO_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        # 按你当前统一规则：必须包含所有要求的 id，否则直接失败
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        from_value = self.value_by_id(generated_inputs, self.FROM_ID)
        to_value = self.value_by_id(generated_inputs, self.TO_ID)

        # 采用 Java 的核心逻辑：
        # 只要 From 或 To 任一为空，就判定触发 bug
        return (from_value.strip() == "") or (to_value.strip() == "")