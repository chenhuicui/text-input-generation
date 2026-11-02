from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page091Validator(BaseValidator):
    case_id = "P091"
    task = "valid_input"

    TITLE_ID = "reviewTitleField"
    BODY_ID = "reviewBodyField"
    SITE_ID = "reviewSiteField"

    REQUIRED_IDS = {
        TITLE_ID,
        BODY_ID,
        SITE_ID,
    }

    PLACEHOLDERS = {
        "generated_value",
        "generate_value",
        "value",
        "text",
        "test",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        title = str(generated_inputs.get(self.TITLE_ID, "")).strip()
        body = str(generated_inputs.get(self.BODY_ID, "")).strip()
        site = str(generated_inputs.get(self.SITE_ID, "")).strip()

        # title 和 body 是核心必填项
        if not title or not body:
            return False

        # site 可以为空；但如果有值，也不能是明显占位符
        values = [title, body, site]
        for v in values:
            lv = v.lower().strip()
            if lv in self.PLACEHOLDERS:
                return False
            if "generated_value" in lv:
                return False

        return True