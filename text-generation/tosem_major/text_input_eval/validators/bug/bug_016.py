import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug016Validator(BaseValidator):
    case_id = "B016"
    task = "invalid_bug"

    TARGET_ID = "org.kontalk.debug:id/text_editor"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    NON_EMOJI_CONTEXT_TRIGGER = re.compile(
        r"[\w.+-]+:/"
        r"|"
        r"[\w.+-]+\s+:/"
        r"|"
        r"[\w.+-]+：/"
        r"|"
        r"\([^)]{1,80}:\)"
        r"|"
        r"\[[^\]]{1,80}:\]"
        r"|"
        r"\{[^}]{1,80}:\}"
        r"|"
        r"<[^>]{1,80}:>"
        r"|"
        r"\"[^\"]{1,80}:\""
        r"|"
        r"'[^']{1,80}:'"
        r"|"
        r"（[^）]{1,80}:）"
        r"|"
        r"【[^】]{1,80}:】"
        r"|"
        r"「[^」]{1,80}:」"
        r"|"
        r"『[^』]{1,80}:』"
        r"|"
        r"（[^）]{1,80}：）"
        r"|"
        r"【[^】]{1,80}：】"
        r"|"
        r"「[^」]{1,80}：」"
        r"|"
        r"『[^』]{1,80}：』",
        re.UNICODE,
    )

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID).strip()
        if not value:
            return False

        return bool(self.NON_EMOJI_CONTEXT_TRIGGER.search(value))