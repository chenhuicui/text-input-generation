from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug011Validator(BaseValidator):
    case_id = "B011"
    task = "invalid_bug"

    TARGET_ID = "fr.neamar.kiss:id/searchEditText"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    SPECIAL_SYMBOLS = set("%&€^¡¿~®©™¢¥$¦¬°¶§×")

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID)

        if not value:
            return False

        for ch in value:
            if self.is_english_non_a(ch) or self.is_special_symbol(ch):
                return True

        return False

    @staticmethod
    def is_ascii_english_letter(ch: str) -> bool:
        return ("A" <= ch <= "Z") or ("a" <= ch <= "z")

    def is_english_non_a(self, ch: str) -> bool:
        return self.is_ascii_english_letter(ch) and ch not in {"a", "A"}

    def is_special_symbol(self, ch: str) -> bool:
        return ch in self.SPECIAL_SYMBOLS