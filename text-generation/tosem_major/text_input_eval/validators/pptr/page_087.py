from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page087Validator(BaseValidator):
    case_id = "P087"
    task = "valid_input"

    TITLE_ID = "com.spotify.music:id/title_edit_text"
    DESCRIPTION_ID = "com.spotify.music:id/description_edit_text"

    REQUIRED_IDS = {
        TITLE_ID,
        DESCRIPTION_ID,
    }

    PLACEHOLDERS = {
        "generated_value",
        "generate_value",
        "value",
        "test",
        "text",
        "",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        title = str(generated_inputs.get(self.TITLE_ID, "")).strip()
        description = str(generated_inputs.get(self.DESCRIPTION_ID, "")).strip()

        # title 是必填；description 可空
        if not title:
            return False

        if title.lower() in self.PLACEHOLDERS:
            return False

        if description and description.lower() in self.PLACEHOLDERS:
            return False

        return True