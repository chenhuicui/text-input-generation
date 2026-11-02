from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page064Validator(BaseValidator):
    case_id = "P064"
    task = "valid_input"

    SYSTOLIC_ID = "com.omronhealthcare.omronconnect:id/edt_systolic"
    DIASTOLIC_ID = "com.omronhealthcare.omronconnect:id/edt_diastolic"
    PULSE_ID = "com.omronhealthcare.omronconnect:id/edt_pulse"

    REQUIRED_IDS = {
        SYSTOLIC_ID,
        DIASTOLIC_ID,
        PULSE_ID,
    }

    def _is_positive_int(self, value: str) -> bool:
        value = str(value).strip()
        return value.isdigit() and int(value) > 0

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        systolic = generated_inputs.get(self.SYSTOLIC_ID, "")
        diastolic = generated_inputs.get(self.DIASTOLIC_ID, "")
        pulse = generated_inputs.get(self.PULSE_ID, "")

        return (
            self._is_positive_int(systolic)
            and self._is_positive_int(diastolic)
            and self._is_positive_int(pulse)
        )