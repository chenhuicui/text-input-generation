from urllib.parse import urlparse

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug021Validator(BaseValidator):
    case_id = "B021"
    task = "invalid_bug"

    NAME_ID = "io.gresse.hugo.anecdote:id/nameEditText"
    PAGE_NAME_ID = "io.gresse.hugo.anecdote:id/pageNameEditText"
    URL_ID = "io.gresse.hugo.anecdote:id/urlEditText"
    URL_SUFFIX_ID = "io.gresse.hugo.anecdote:id/urlSuffixEditText"
    SELECTOR_ID = "io.gresse.hugo.anecdote:id/selectorEditText"

    REQUIRED_IDS = {
        NAME_ID,
        PAGE_NAME_ID,
        URL_ID,
        URL_SUFFIX_ID,
        SELECTOR_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.URL_ID).strip()

        if value == "":
            return False

        return not self.is_valid_url(value)

    @staticmethod
    def is_valid_url(value: str) -> bool:
        try:
            parsed = urlparse(value)
            return bool(parsed.scheme and parsed.netloc)
        except Exception:
            return False