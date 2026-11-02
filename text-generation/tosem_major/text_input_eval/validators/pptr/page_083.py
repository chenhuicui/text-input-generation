from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page083Validator(BaseValidator):
    case_id = "P083"
    task = "valid_input"

    TARGET_ID = "com.sillens.shapeupclub:id/search_edit_text"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    INVALID_VALUES = {
        "",
        "generated_value",
        "generated-value",
        "generated value",
        "generatedvalue",
    }

    FOOD_KEYWORDS = {
        "apple", "banana", "pizza", "oatmeal", "eggs", "egg",
        "bagel", "pasta", "chicken", "salad", "smoothie",
        "quaker oats", "chicken breast", "chicken salad",
        "scrambled eggs", "grilled chicken salad"
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.TARGET_ID, "")).strip()
        value_l = value.lower()

        if value_l in self.INVALID_VALUES:
            return False

        # 太长的复杂描述容易不通过，限制一下更贴近样例
        if len(value.split()) > 5:
            return False

        return any(keyword in value_l for keyword in self.FOOD_KEYWORDS)