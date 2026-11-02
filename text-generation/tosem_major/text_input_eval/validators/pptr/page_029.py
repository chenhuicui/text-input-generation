from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page029Validator(BaseValidator):
    case_id = "P029"
    task = "valid_input"

    TARGET_ID = "com.forecasts.noaa.live.weather:id/et_input"

    REQUIRED_IDS = {
        TARGET_ID,
    }

    INVALID_LITERAL_VALUES = {
        "generated_value",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.TARGET_ID).strip()

        if value == "":
            return False

        if value.lower() in self.INVALID_LITERAL_VALUES:
            return False

        return True