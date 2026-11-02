from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug006Validator(BaseValidator):
    case_id = "B006"
    task = "invalid_bug"

    START_DATE_ID = "de.symeda.sormas.app:id/immunization_startDate"
    END_DATE_ID = "de.symeda.sormas.app:id/immunization_endDate"
    VALID_FROM_ID = "de.symeda.sormas.app:id/immunization_validFrom"
    VALID_UNTIL_ID = "de.symeda.sormas.app:id/immunization_validUntil"
    NUMBER_OF_DOSES_ID = "de.symeda.sormas.app:id/immunization_numberOfDoses"

    REQUIRED_IDS = {
        START_DATE_ID,
        END_DATE_ID,
        VALID_FROM_ID,
        VALID_UNTIL_ID,
        NUMBER_OF_DOSES_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        # 必须包含所有要求的 id，否则直接失败
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = self.value_by_id(generated_inputs, self.NUMBER_OF_DOSES_ID).strip()

        # bug 条件：
        # 在 number-of-doses 字段中输入任意非数字字符会触发
        return value != "" and (not value.isdigit())