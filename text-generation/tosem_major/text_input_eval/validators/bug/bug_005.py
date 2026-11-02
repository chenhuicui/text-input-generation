from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug005Validator(BaseValidator):
    case_id = "B005"
    task = "invalid_bug"

    TARGET_ID = "org.schabi.newpipe:id/toolbar_search_edit_text"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        # 必须包含所有要求的 id，否则直接失败
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID)

        # bug 条件：
        # 搜索字符串长度达到 100 或以上时触发，空格也计入长度
        return len(value) >= 100