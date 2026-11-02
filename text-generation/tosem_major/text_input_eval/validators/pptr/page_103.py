from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page103Validator(BaseValidator):
    case_id = "P103"
    task = "valid_input"

    USER_FIRST_NAME_ID = "com.xogrp.planner:id/et_user_first_name"
    USER_LAST_NAME_ID = "com.xogrp.planner:id/et_user_last_name"
    PARTNER_FIRST_NAME_ID = "com.xogrp.planner:id/et_partner_first_name"
    PARTNER_LAST_NAME_ID = "com.xogrp.planner:id/et_partner_last_name"

    REQUIRED_IDS = {
        USER_FIRST_NAME_ID,
        USER_LAST_NAME_ID,
        PARTNER_FIRST_NAME_ID,
        PARTNER_LAST_NAME_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        return self.all_required_values_non_empty(generated_inputs, self.REQUIRED_IDS)
