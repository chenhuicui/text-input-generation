import re

from text_input_eval.validators.base import BaseValidator
from text_input_eval.validators.registry import register


@register
class Page096Validator(BaseValidator):
    case_id = "P096"
    task = "valid_input"

    SEARCH_ID = "search_input"

    REQUIRED_IDS = {
        SEARCH_ID,
    }

    PLACEHOLDERS = {
        "generated_value",
        "generate_value",
        "value",
        "text",
        "test",
        "search",
    }

    # 这些更像泛化推荐/分类/教程查询，在样本里多为失败
    BLOCKED_CONTAINS = {
        "discover new",
        "travel destination",
        "travel destinations",
        "trending playlist",
        "trending playlists",
        "trending topics",
        "top hits",
        "top travel",
        "summer fashion",
        "popular podcasts",
        "ai tools",
        "technology",
        "music genres",
        "new releases",
        "the dark knight",
        "mystery movie",
        "music playlist",
        "chill vibes playlist",
        "chill jazz playlist",
        "relaxing piano playlist",
        "indie rock playlist",
        "classic rock playlist",
        "guitar tabs",
        "guitar chords",
        "learn guitar",
        "learn to play guitar",
        "beethoven symphony",
        "rock classics",
    }

    def validate(self, generated_inputs: dict) -> bool:
        if not isinstance(generated_inputs, dict):
            return False

        if not self.contains_all_required_ids(generated_inputs, self.REQUIRED_IDS):
            return False

        value = str(generated_inputs.get(self.SEARCH_ID, "")).strip()
        if not value:
            return False

        lv = re.sub(r"\s+", " ", value.lower()).strip()

        if lv in self.PLACEHOLDERS:
            return False
        if "generated_value" in lv:
            return False

        for bad in self.BLOCKED_CONTAINS:
            if bad in lv:
                return False

        return True