import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page089Validator(BaseValidator):
    case_id = "P089"
    task = "valid_input"

    GROUP_NAME_ID = "com.teamreach.app:id/et_group_name"
    GROUP_ID_ID = "com.teamreach.app:id/et_group_id"

    REQUIRED_IDS = {
        GROUP_NAME_ID,
        GROUP_ID_ID,
    }

    PLACEHOLDERS = {
        "generated_value",
        "generate_value",
        "value",
        "test",
        "text",
        "group",
        "group name",
        "group id",
    }

    GROUP_ID_RE = re.compile(r"^[A-Za-z0-9]{7,20}$")

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        group_name = str(generated_inputs.get(self.GROUP_NAME_ID, "")).strip()
        group_id = str(generated_inputs.get(self.GROUP_ID_ID, "")).strip()

        if not group_name or not group_id:
            return False

        group_name_l = group_name.lower()
        group_id_l = group_id.lower()

        if group_name_l in self.PLACEHOLDERS or group_id_l in self.PLACEHOLDERS:
            return False

        if any(ph in group_name_l for ph in ["generated_value", "placeholder"]):
            return False
        if any(ph in group_id_l for ph in ["generated_value", "placeholder"]):
            return False

        # group name: 非空、不能太短
        if len(group_name) < 3:
            return False

        # group id: 字母数字组合，不能有特殊符号，长度 7-20
        if not self.GROUP_ID_RE.fullmatch(group_id):
            return False

        # 不能是纯数字
        if group_id.isdigit():
            return False

        # 至少包含字母和数字
        if not any(ch.isalpha() for ch in group_id):
            return False
        if not any(ch.isdigit() for ch in group_id):
            return False

        return True