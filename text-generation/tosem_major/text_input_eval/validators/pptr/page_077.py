from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page077Validator(BaseValidator):
    case_id = "P077"
    task = "valid_input"

    TARGET_ID = "com.scribd.app.reader0:id/searchView"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    VALID_VALUES = {
        "to kill a mockingbird",
        "the great gatsby",
        "stephen king fiction",
        "fantasy",
        "the hunger games",
        "the hunger games by suzanne collins",
        "dystopian novels",
        "everand tutorial",
        "everand chronicles",
        "mystery novel",
        "harry potter j.k. rowling fantasy",
        "pride and prejudice",
        "classic american literature",
        "adventure books",
        "adventure mode",
        "the hobbit",
        "the great gatsby by f. scott fitzgerald",
        "artificial intelligence: a modern approach",
        "mystery novels by agatha christie",
        "1984",
        "everand test",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.TARGET_ID, "")).strip().lower()
        return value in self.VALID_VALUES