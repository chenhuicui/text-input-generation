import re

import zipcodes

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page095Validator(BaseValidator):
    case_id = "P095"
    task = "valid_input"

    LOCATION_ID = "com.trubeacon.scooters_mobile_android:id/locationSearchEditText"

    REQUIRED_IDS = {
        LOCATION_ID,
    }

    PLACEHOLDERS = {
        "generated_value",
        "generate_value",
        "value",
        "text",
        "test",
        "search",
        "location",
        "address",
        "zip",
        "zipcode",
    }

    BLOCKED_LOCATIONS = {
        "omaha, ne",
        "new york, ny",
        "denver, co",
        "austin, tx",
    }

    ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        location = str(generated_inputs.get(self.LOCATION_ID, "")).strip()
        if not location:
            return False

        lv = location.lower().strip()

        if lv in self.PLACEHOLDERS:
            return False
        if "generated_value" in lv:
            return False

        if lv in self.BLOCKED_LOCATIONS:
            return False

        # 如果是 ZIP / ZIP+4，则用 zipcodes 包判定前 5 位是否真实存在
        if self.ZIP_RE.fullmatch(location):
            zip5 = location[:5]
            return bool(zipcodes.matching(zip5))

        # 非 ZIP：作为地址/城市搜索词，只要求非空、非占位符、非禁用城市
        return True