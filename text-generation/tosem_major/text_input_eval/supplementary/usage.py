"""Provider token-usage normalization for supplementary experiments."""
from __future__ import annotations


def normalize_usage(response):
    """Return canonical token counters without estimating missing values."""
    if not isinstance(response, dict):
        return None
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    candidates = (("response.usage", response.get("usage")),
                  ("response.tokenUsage", response.get("tokenUsage")),
                  ("response.usageMetadata", response.get("usageMetadata")),
                  ("response.tokens", response.get("tokens")),
                  ("data.usage", data.get("usage")),
                  ("data.tokenUsage", data.get("tokenUsage")),
                  ("data.usageMetadata", data.get("usageMetadata")),
                  ("data.tokens", data.get("tokens")),
                  ("response", response), ("data", data))
    aliases = {
        "prompt_tokens": ("prompt_tokens", "input_tokens", "promptTokens", "inputTokens",
                          "promptToken", "inputToken", "promptTokenCount", "inputTokenCount"),
        "completion_tokens": ("completion_tokens", "output_tokens", "completionTokens", "outputTokens",
                              "completionToken", "outputToken", "candidatesTokenCount", "outputTokenCount"),
        "total_tokens": ("total_tokens", "totalTokens", "totalToken", "totalTokenCount"),
    }
    for source, container in candidates:
        if not isinstance(container, dict):
            continue
        normalized = {}
        for target, names in aliases.items():
            value = next((container[name] for name in names if name in container), None)
            if type(value) is int and value >= 0:
                normalized[target] = value
            elif isinstance(value, float) and value >= 0 and value.is_integer():
                normalized[target] = int(value)
        if normalized:
            if "total_tokens" not in normalized and {"prompt_tokens", "completion_tokens"} <= set(normalized):
                normalized["total_tokens"] = normalized["prompt_tokens"] + normalized["completion_tokens"]
            return {**normalized, "source": source}
    return None
