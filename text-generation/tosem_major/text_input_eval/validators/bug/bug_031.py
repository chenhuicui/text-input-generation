from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug031Validator(BaseValidator):
    case_id = "B031"
    task = "invalid_bug"

    WORKOUT_ID = "org.secuso.privacyfriendlyintervaltimer:id/main_workout_interval_time"
    REST_ID = "org.secuso.privacyfriendlyintervaltimer:id/main_rest_interval_time"
    SETS_ID = "org.secuso.privacyfriendlyintervaltimer:id/main_sets_amount"

    REQUIRED_IDS = {
        WORKOUT_ID,
        REST_ID,
        SETS_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        # 与 Java 逻辑一致：只检查前两个时间字段
        for field_id in (self.WORKOUT_ID, self.REST_ID):
            value = self.value_by_id(generated_inputs, field_id)

            # Expected format: MM:SS
            if value is None or len(value) != 5 or value[2] != ":":
                return False

            mm = value[:2]
            ss = value[3:]

            if not (mm.isdigit() and ss.isdigit()):
                return False

            minutes = int(mm)
            seconds = int(ss)

            if not (0 <= minutes <= 60 and 0 <= seconds <= 60):
                return True

        return False