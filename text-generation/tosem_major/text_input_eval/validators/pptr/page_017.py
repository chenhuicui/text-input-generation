from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page017Validator(BaseValidator):
    case_id = "P017"
    task = "valid_input"

    CAR_PRICE_ID = "com.cars.android:id/carPrice_or_monthlyPayment_input"
    DOWN_PAYMENT_ID = "com.cars.android:id/down_payment_input"
    TRADE_IN_VALUE_ID = "com.cars.android:id/trade_in_value_input"
    SALES_TAX_ID = "com.cars.android:id/sales_tax_input"
    INTEREST_RATE_ID = "com.cars.android:id/interest_rate_input"

    REQUIRED_IDS = {
        CAR_PRICE_ID,
        DOWN_PAYMENT_ID,
        TRADE_IN_VALUE_ID,
        SALES_TAX_ID,
        INTEREST_RATE_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        return False