from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page067Validator(BaseValidator):
    case_id = "P067"
    task = "valid_input"

    FIRST_NAME_ID = "com.peerspace.app:id/firstNameTxt"
    LAST_NAME_ID = "com.peerspace.app:id/lastNameTxt"
    PROFESSION_ID = "com.peerspace.app:id/professionTxt"
    ORGANIZATION_ID = "com.peerspace.app:id/organizationTxt"

    REQUIRED_IDS = {
        FIRST_NAME_ID,
        LAST_NAME_ID,
        PROFESSION_ID,
        ORGANIZATION_ID,
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        return all(
            str(generated_inputs.get(target_id, "")).strip()
            for target_id in self.REQUIRED_IDS
        )