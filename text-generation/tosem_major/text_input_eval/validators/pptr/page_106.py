import re
import zipcodes

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page106Validator(BaseValidator):
    case_id = "P106"
    task = "valid_input"

    SEARCH_ID = "com.zillow.android.zillowmap:id/search_src_text"

    REQUIRED_IDS = {
        SEARCH_ID,
    }

    PLACEHOLDERS = {
        "generated_value",
        "generate_value",
        "value",
        "text",
        "test",
    }

    PURE_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
    STREET_ADDRESS_RE = re.compile(
        r"^\d+\s+.+\b("
        r"st|street|ave|avenue|blvd|boulevard|rd|road|dr|drive|ln|lane|"
        r"ct|court|pl|place|way|pkwy|parkway|hwy|highway"
        r")\b",
        re.IGNORECASE,
    )

    def _is_valid_zip(self, value: str) -> bool:
        if not self.PURE_ZIP_RE.fullmatch(value):
            return False

        zip5 = value[:5]
        return bool(zipcodes.matching(zip5))

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.SEARCH_ID, "")).strip()
        lower_value = value.lower()

        if not value:
            return False

        if lower_value in self.PLACEHOLDERS:
            return False

        if "generated_value" in lower_value:
            return False

        # 纯 ZIP：必须是真实 ZIP，但该页面实验里纯 ZIP 仍判失败
        # 例如 90210 / 85001 都应失败
        if self.PURE_ZIP_RE.fullmatch(value):
            return False

        # 如果后续想允许纯 ZIP，则改成：
        # if self.PURE_ZIP_RE.fullmatch(value):
        #     return self._is_valid_zip(value)

        # 明确街道地址不通过
        if self.STREET_ADDRESS_RE.search(value):
            return False

        bad_contains = [
            "school district",
            "tucson unified school district",
            "downtown tucson",
        ]
        if any(term in lower_value for term in bad_contains):
            return False

        return True