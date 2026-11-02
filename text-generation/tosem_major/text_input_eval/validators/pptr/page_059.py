import re

import zipcodes

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page059Validator(BaseValidator):
    case_id = "P059"
    task = "valid_input"

    FIRST_NAME_ID = "com.nihaojewelry.nihao:id/et_first_name"
    LAST_NAME_ID = "com.nihaojewelry.nihao:id/et_last_name"
    PHONE_ID = "com.nihaojewelry.nihao:id/et_phone_number"
    SEARCH_ADDRESS_ID = "com.nihaojewelry.nihao:id/et_search_address"
    ZIP_CODE_ID = "com.nihaojewelry.nihao:id/et_zip_code"
    CITY_ID = "com.nihaojewelry.nihao:id/et_city"
    ADDRESS_LINE_2_ID = "com.nihaojewelry.nihao:id/et_address_line_2"
    ADDRESS_LINE_1_ID = "com.nihaojewelry.nihao:id/et_address_line_1"

    REQUIRED_IDS = {
        FIRST_NAME_ID,
        LAST_NAME_ID,
        PHONE_ID,
        SEARCH_ADDRESS_ID,
        ZIP_CODE_ID,
        CITY_ID,
        ADDRESS_LINE_2_ID,
        ADDRESS_LINE_1_ID,
    }

    NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]*$")
    PHONE_ALLOWED_RE = re.compile(r"^[0-9+\-() ]+$")

    def _value(self, generated_inputs: dict, target_id: str) -> str:
        return str(generated_inputs.get(target_id, "")).strip()

    def _is_name(self, value: str) -> bool:
        return bool(value and self.NAME_RE.fullmatch(value))

    def _is_phone(self, value: str) -> bool:
        if not value or not self.PHONE_ALLOWED_RE.fullmatch(value):
            return False
        digits = "".join(ch for ch in value if ch.isdigit())
        return len(digits) >= 10

    def _is_zipcode(self, value: str) -> bool:
        return bool(value and zipcodes.matching(value))

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        first_name = self._value(generated_inputs, self.FIRST_NAME_ID)
        last_name = self._value(generated_inputs, self.LAST_NAME_ID)
        phone = self._value(generated_inputs, self.PHONE_ID)
        zip_code = self._value(generated_inputs, self.ZIP_CODE_ID)
        city = self._value(generated_inputs, self.CITY_ID)
        address_line_1 = self._value(generated_inputs, self.ADDRESS_LINE_1_ID)

        if not self._is_name(first_name):
            return False

        if not self._is_name(last_name):
            return False

        if not self._is_phone(phone):
            return False

        if not self._is_zipcode(zip_code):
            return False

        if not city:
            return False

        if not address_line_1:
            return False

        return True