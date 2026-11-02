from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page020Validator(BaseValidator):
    case_id = "P020"
    task = "valid_input"

    TARGET_ID = "SearchBox__input-field"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    # Empirically observed valid queries from prior runs.
    VALID_QUERIES = {
        "vampire romance",
        "enemies to lovers",
        "mafia",
        "mafia romance",
        "opposites attract romance",
        "mystery thrillers by agatha christie",
        "pride and prejudice",
        "the great gatsby",
        "crime novel",
        "mafia in literature",
        "mafia stories",
        "mafia boss",
        "3",
        "30 mafia",
        "mafia books",
        "search: mystery",
        "romance",
        "jane austen",
        "love",
        "fantasy",
        "mystery",
        "crime thriller",
        "crime thriller by john doe",
        "billionaire romance",
        "mafia boss revenge",
        "romance novel series",
        "romance novel",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID).strip().lower()

        if value == "":
            return False

        return value in self.VALID_QUERIES