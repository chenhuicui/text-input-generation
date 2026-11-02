import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page107Validator(BaseValidator):
    case_id = "P107"
    task = "valid_input"

    WEIGHT_ID = "heartratemonitor.heartrate.pulse.pulseapp:id/etWeight"
    HEIGHT_FT_ID = "heartratemonitor.heartrate.pulse.pulseapp:id/etHeightFt"
    HEIGHT_IN_ID = "heartratemonitor.heartrate.pulse.pulseapp:id/etHeightIn"

    REQUIRED_IDS = {
        WEIGHT_ID,
        HEIGHT_FT_ID,
        HEIGHT_IN_ID,
    }

    NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")
    INTEGER_RE = re.compile(r"^\d+$")

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        # 结构必须满足：三个字段都要在 JSON 里
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        weight = str(generated_inputs.get(self.WEIGHT_ID, "")).strip()
        height_ft = str(generated_inputs.get(self.HEIGHT_FT_ID, "")).strip()
        height_in = str(generated_inputs.get(self.HEIGHT_IN_ID, "")).strip()

        # 页面本身有默认值，所以空字符串允许通过
        # 但如果填写了内容，就必须是合法数字

        if weight:
            if not self.NUMBER_RE.fullmatch(weight):
                return False
            if float(weight) <= 0:
                return False

        if height_ft:
            if not self.INTEGER_RE.fullmatch(height_ft):
                return False
            if int(height_ft) <= 0:
                return False

        if height_in:
            if not self.INTEGER_RE.fullmatch(height_in):
                return False
            if not (0 <= int(height_in) <= 11):
                return False

        return True