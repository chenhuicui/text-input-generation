from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page014Validator(BaseValidator):
    case_id = "P014"
    task = "valid_input"

    START_ID = "ion-input-0"
    END_ID = "ion-input-1"
    CONTAIN_ID = "ion-input-2"

    REQUIRED_IDS = {
        START_ID,
        END_ID,
        CONTAIN_ID,
    }

    # Empirically confirmed satisfiable constraint triples from prior runs.
    # Matching is case-insensitive after strip().
    VALID_TRIPLES = {
        ("a", "son", "er"),
        ("a", "bert", "ert"),
        ("ma", "a", "ar"),
        ("a", "a", "el"),
        ("s", "r", "bjart"),
        ("j", "son", "a"),
        ("sig", "tur", "bjart"),
        ("s", "ur", "bjart"),
        ("s", "ur", "al"),
        ("a", "a", "an"),
        ("si", "ur", "bjart"),
        ("sig", "tur", "bjar"),
        ("sig", "ur", "bjar"),
        ("sigur", "artur", "bjart"),
        ("sigur", "artur", "bj"),
        ("a", "n", "a"),
        ("s", "ur", "art"),
        ("s", "r", "ig"),
        ("s", "r", "igur"),
        ("a", "r", "ur"),
        ("a", "r", "l"),
        ("a", "rt", "ar"),
        ("a", "z", "a"),
        ("a", "z", "ab"),
        ("a", "y", "am"),
        ("j", "a", "el"),
        ("a", "a", "li"),
        ("a", "n", "li"),
        ("a", "a", "e"),
        ("a", "y", "e"),
        ("a", "n", "an"),
        ("a", "son", "an"),
        ("am", "ia", "el"),
        ("al", "ia", "ex"),
        ("j", "n", "ar"),
        ("j", "a", "li"),
        ("j", "r", "a"),
        ("a", "r", "art"),
        ("a", "n", "ea"),
        ("a", "e", "am"),
        ("a", "n", "e"),
        ("a", "n", "ar"),
        ("a", "n", "ma"),
        ("a", "o", "an"),
        ("a", "son", "el"),
        ("a", "a", "a"),
        ("el", "ne", "iz"),
        ("li", "a", "n"),
        ("o'", "ey", "con"),
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        start_value = self.value_by_id(generated_inputs, self.START_ID).strip()
        end_value = self.value_by_id(generated_inputs, self.END_ID).strip()
        contain_value = self.value_by_id(generated_inputs, self.CONTAIN_ID).strip()

        if start_value == "" or end_value == "" or contain_value == "":
            return False

        triple = (
            start_value.lower(),
            end_value.lower(),
            contain_value.lower(),
        )
        return triple in self.VALID_TRIPLES