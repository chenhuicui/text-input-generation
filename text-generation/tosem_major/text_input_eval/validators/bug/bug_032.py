from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Bug032Validator(BaseValidator):
    case_id = "B032"
    task = "invalid_bug"

    HEX_ID = "io.mrarm.irc:id/value_hex"
    R_ID = "io.mrarm.irc:id/value_r"
    G_ID = "io.mrarm.irc:id/value_g"
    B_ID = "io.mrarm.irc:id/value_b"
    A_ID = "io.mrarm.irc:id/value_a"

    REQUIRED_IDS = {
        HEX_ID,
        R_ID,
        G_ID,
        B_ID,
        A_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        hex_value = self.value_by_id(generated_inputs, self.HEX_ID)

        return hex_value == "#"