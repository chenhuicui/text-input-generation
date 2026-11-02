from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug020Validator(BaseValidator):
    case_id = "B020"
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

        dom_selector = self.value_by_id(generated_inputs, self.SELECTOR_ID)

        return dom_selector.strip() == ""