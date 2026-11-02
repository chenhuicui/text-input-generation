from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pymysql
import requests
from PIL import Image
from pymysql.cursors import DictCursor

try:
    import tiktoken
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing dependency: tiktoken. Install it in the current Python environment with:\n"
        "  python -m pip install tiktoken\n"
    ) from exc


PROJECT_ROOT = Path(
    "/Users/cuichenhui/Documents/local-repositories/"
    "llm-empirical-study-workspace/llm-empirical-study"
)

DEFAULT_SOURCE_TABLE = "t_llm_text_generation_curated"
DEFAULT_GENERATION_TABLE = "t_llm_text_generation"
DEFAULT_TOKEN_TABLE = "t_llm_text_generation_curated_token"
DEFAULT_TARGET_TABLE = "t_llm_text_generation_curated_token_recomputed"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output" / "recomputed_token_costs"
TOSEM_MAJOR_ROOT = PROJECT_ROOT / "tosem_major"
CACHE_ROOT = TOSEM_MAJOR_ROOT / "apk_info" / "cache"
PPTR_PROMPTS_JSON = CACHE_ROOT / "pptr_prompts.json"
BUG_PROMPTS_JSON = CACHE_ROOT / "bug_prompts.json"
PPTR_CACHE_DIR = CACHE_ROOT / "pptr"
BUG_CACHE_DIR = CACHE_ROOT / "bug"

DEFAULT_EXPERIMENTS = (
    "rq1_valid",
    "rq1_invalid",
    "rq2_valid_feedback",
    "rq2_invalid_feedback",
)

PROMPT_SETTINGS = ("extracted", "xml", "vision")
PROMPT_VARIANT_BY_SETTING = {
    "extracted": "full",
    "xml": "xml",
    "vision": "vision",
}

FINAL_MODELS = (
    "gemini-3.1-pro-preview",
    "claude-opus-4-6",
    "grok-4.20-reasoning",
    "gpt-5.4-2026-03-05",
    "Doubao-Seed-2.0-pro",
    "qwen3.5-plus",
    "baidu/ernie-5.0-thinking-preview",
    "kimi/kimi-k2.5",
    "mistralai/mistral-large-2512",
)

# Prices are USD per one million input/output tokens. Values marked None are
# intentionally left unpriced because the paper baseline table records N/A.
MODEL_PRICES_USD_PER_1M: Dict[str, Tuple[Optional[float], Optional[float], str]] = {
    "gemini-3.1-pro-preview": (2.00, 12.00, "paper_baseline_table"),
    "claude-opus-4-6": (5.00, 25.00, "paper_baseline_table"),
    "grok-4.20-reasoning": (5.00, 6.00, "paper_baseline_table"),
    "gpt-5.4-2026-03-05": (2.50, 15.00, "paper_baseline_table"),
    "Doubao-Seed-2.0-pro": (None, None, "paper_baseline_table_na"),
    "qwen3.5-plus": (0.39, 2.34, "paper_baseline_table"),
    "baidu/ernie-5.0-thinking-preview": (None, None, "paper_baseline_table_na"),
    "kimi/kimi-k2.5": (0.60, 3.00, "paper_baseline_table"),
    "mistralai/mistral-large-2512": (0.50, 1.50, "paper_baseline_table"),
}


@dataclass(frozen=True)
class PromptContext:
    task: str
    case_id: str
    target_id: str
    global_text: str
    component_text: str
    adjacent_text: str
    restrictive_text: str
    guiding_text: str
    xml_text: str
    image_64: str


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]


@dataclass(frozen=True)
class RequestPayload:
    message: str
    image_64: Optional[str]
    request_message_type: str
    calc_note: str


@dataclass
class CountResult:
    latest_input_tokens: Optional[int]
    latest_text_tokens: Optional[int]
    latest_image_tokens: Optional[int]
    latest_image_width: Optional[int]
    latest_image_height: Optional[int]
    text_token_method: str
    image_token_method: str
    payload_cache_key: str
    request_message_type: str
    calc_note: str
    status: str


def get_conn(*, autocommit: bool = False):
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "101.200.47.200"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "must_user"),
        password=os.getenv("MYSQL_PASSWORD", "REDACTED_USE_ENV_MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE", "tosem_text_input_db"),
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=autocommit,
    )


def log(message: str) -> None:
    print(message, flush=True)


def placeholders(items: Sequence[Any]) -> str:
    return ",".join(["%s"] * len(items))


def parse_csv(value: str) -> List[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def safe_json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    text = str(value).strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def parse_session_ids(value: Any) -> List[str]:
    data = safe_json_loads(value, default=[])
    if not isinstance(data, list):
        return []
    out: List[str] = []
    for item in data:
        sid = str(item or "").strip()
        if sid:
            out.append(sid)
    return out


def unique_keep_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except Exception:
        return None


def strip_data_url(image_url: Optional[str]) -> Optional[str]:
    if not image_url:
        return None
    text = str(image_url).strip()
    marker = ";base64,"
    if marker in text:
        return text.split(marker, 1)[1].strip()
    if text.startswith("data:image/") and "," in text:
        return text.split(",", 1)[1].strip()
    return text


def image_data_url(image_64: Optional[str], media_type: str = "image/png") -> Optional[str]:
    data = strip_data_url(image_64)
    if not data:
        return None
    return f"data:{media_type};base64,{data}"


def first_env(*names: str) -> Optional[str]:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def usage_prompt_tokens(usage: Optional[TokenUsage]) -> Optional[int]:
    if usage is None:
        return None
    if usage.prompt_tokens is not None:
        return int(usage.prompt_tokens)
    if usage.total_tokens is not None and usage.completion_tokens is not None:
        return max(0, int(usage.total_tokens) - int(usage.completion_tokens))
    return None


def image_size_from_base64(image_64: str) -> Tuple[int, int]:
    raw = base64.b64decode(strip_data_url(image_64) or "", validate=False)
    with Image.open(io.BytesIO(raw)) as image:
        return image.size


def load_image_64_from_cache(ctx: PromptContext) -> str:
    base_dir = PPTR_CACHE_DIR if ctx.task == "valid_pptr" else BUG_CACHE_DIR
    file_path = base_dir / f"base64_{ctx.target_id}.txt"
    if not file_path.exists():
        raise FileNotFoundError(f"Base64 image cache not found: {file_path}")
    return file_path.read_text(encoding="utf-8").strip()


def load_xml_text_from_cache(ctx: PromptContext) -> str:
    base_dir = PPTR_CACHE_DIR if ctx.task == "valid_pptr" else BUG_CACHE_DIR
    file_path = base_dir / f"hierarchy_{ctx.target_id}.xml"
    if not file_path.exists():
        raise FileNotFoundError(f"XML hierarchy cache not found: {file_path}")
    return file_path.read_text(encoding="utf-8")


def openai_gpt5_high_detail_image_tokens(width: int, height: int) -> int:
    # Official GPT-5/Chat image accounting for high-detail images: fit inside
    # 2048x2048, scale the shortest side to 768px, count 512px tiles, then add
    # model-specific base and per-tile costs.
    w = float(width)
    h = float(height)
    if max(w, h) > 2048:
        scale = 2048 / max(w, h)
        w *= scale
        h *= scale

    if min(w, h) > 0:
        scale = 768 / min(w, h)
        w *= scale
        h *= scale

    tiles = int(math.ceil(w / 512) * math.ceil(h / 512))
    return int(70 + 140 * tiles)


def gemini_image_tokens(width: int, height: int) -> int:
    # Gemini public docs describe image tiling for larger images. We use a
    # deterministic local estimator: one 258-token tile per 768px tile.
    if width <= 384 and height <= 384:
        return 258
    return int(math.ceil(width / 768) * math.ceil(height / 768) * 258)


def claude_image_tokens(width: int, height: int) -> int:
    # Anthropic's public approximation for image tokens is width * height / 750.
    return int(math.ceil((width * height) / 750))


def model_family(model: str) -> str:
    lower = model.lower()
    if "gpt-" in lower or lower.startswith("openai/"):
        return "openai"
    if "claude" in lower:
        return "claude"
    if "gemini" in lower:
        return "gemini"
    if "grok" in lower:
        return "grok"
    if "doubao" in lower:
        return "doubao"
    if "qwen" in lower:
        return "qwen"
    if "ernie" in lower or "baidu/" in lower:
        return "ernie"
    if "kimi" in lower:
        return "kimi"
    if "mistral" in lower:
        return "mistral"
    return "generic"


def count_image_tokens(model: str, image_64: Optional[str]) -> Tuple[int, Optional[int], Optional[int], str]:
    if not image_64:
        return 0, None, None, "none"
    width, height = image_size_from_base64(image_64)
    family = model_family(model)
    if family == "openai":
        return openai_gpt5_high_detail_image_tokens(width, height), width, height, "openai_gpt5_high_detail_70_plus_140_tiles"
    if family == "claude":
        return claude_image_tokens(width, height), width, height, "claude_width_times_height_div_750"
    if family == "gemini":
        return gemini_image_tokens(width, height), width, height, "gemini_384_or_768_tile_258"
    return 0, width, height, "image_tokens_require_provider_counter_or_usage"


class OfficialTokenCounter:
    def __init__(
        self,
        *,
        encoding: Any,
        mode: str,
        allow_api_calls: bool,
        use_db_prompt_fallback: bool,
        allow_approximate_fallback: bool,
        timeout: float,
        retries: int,
    ) -> None:
        self.encoding = encoding
        self.mode = mode
        self.allow_api_calls = allow_api_calls
        self.use_db_prompt_fallback = use_db_prompt_fallback
        self.allow_approximate_fallback = allow_approximate_fallback
        self.timeout = timeout
        self.retries = max(1, retries)
        self.http = requests.Session()
        self.hf_tokenizers: Dict[str, Any] = {}

    @staticmethod
    def _model_for_env(model: str, family: str) -> str:
        env_name = f"TOKEN_COUNT_MODEL_{family.upper()}"
        value = os.getenv(env_name, "").strip()
        if value:
            return value
        if family == "kimi":
            return model.split("/", 1)[-1]
        if family == "xai" or family == "grok":
            return "grok-4"
        return model

    @staticmethod
    def _ok(
        *,
        latest_input_tokens: int,
        latest_text_tokens: Optional[int],
        latest_image_tokens: Optional[int],
        latest_image_width: Optional[int],
        latest_image_height: Optional[int],
        text_token_method: str,
        image_token_method: str,
        payload_cache_key: str,
        request_message_type: str,
        calc_note: str,
    ) -> CountResult:
        return CountResult(
            latest_input_tokens=int(latest_input_tokens),
            latest_text_tokens=latest_text_tokens,
            latest_image_tokens=latest_image_tokens,
            latest_image_width=latest_image_width,
            latest_image_height=latest_image_height,
            text_token_method=text_token_method,
            image_token_method=image_token_method,
            payload_cache_key=payload_cache_key,
            request_message_type=request_message_type,
            calc_note=calc_note,
            status="computed",
        )

    @staticmethod
    def _missing(
        *,
        payload_cache_key: str,
        request_message_type: str,
        calc_note: str,
        text_token_method: str = "official_counter_unavailable",
        image_token_method: str = "official_counter_unavailable",
    ) -> CountResult:
        return CountResult(
            latest_input_tokens=None,
            latest_text_tokens=None,
            latest_image_tokens=None,
            latest_image_width=None,
            latest_image_height=None,
            text_token_method=text_token_method,
            image_token_method=image_token_method,
            payload_cache_key=payload_cache_key,
            request_message_type=request_message_type,
            calc_note=calc_note,
            status="missing_official_input",
        )

    def _post_json(self, url: str, *, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self.http.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
                if response.status_code >= 400:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
                data = response.json()
                if not isinstance(data, dict):
                    raise RuntimeError("response is not a JSON object")
                return data
            except Exception as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(min(2.0, 0.5 * attempt))
        raise RuntimeError(str(last_error))

    def _db_usage_result(
        self,
        *,
        latest_usage: Optional[TokenUsage],
        payload_cache_key: str,
        request_message_type: str,
        calc_note: str,
    ) -> Optional[CountResult]:
        prompt_tokens = usage_prompt_tokens(latest_usage)
        if prompt_tokens is None:
            return None
        return self._ok(
            latest_input_tokens=prompt_tokens,
            latest_text_tokens=None,
            latest_image_tokens=None,
            latest_image_width=None,
            latest_image_height=None,
            text_token_method="provider_reported_usage_prompt_tokens",
            image_token_method="included_in_provider_reported_usage",
            payload_cache_key=payload_cache_key,
            request_message_type=request_message_type,
            calc_note=f"{calc_note}; provider-reported usage prompt_tokens fallback",
        )

    def _local_openai_count(
        self,
        *,
        model: str,
        message: str,
        image_64: Optional[str],
        payload_cache_key: str,
        request_message_type: str,
        calc_note: str,
    ) -> CountResult:
        try:
            encoding = tiktoken.encoding_for_model(model)
            text_method = f"tiktoken_encoding_for_model:{model}"
        except Exception:
            encoding = self.encoding
            text_method = "tiktoken_o200k_base_openai_fallback"
        text_tokens = len(encoding.encode(message or ""))
        image_tokens, width, height, image_method = count_image_tokens(model, image_64)
        return self._ok(
            latest_input_tokens=text_tokens + image_tokens,
            latest_text_tokens=text_tokens,
            latest_image_tokens=image_tokens,
            latest_image_width=width,
            latest_image_height=height,
            text_token_method=text_method,
            image_token_method=image_method,
            payload_cache_key=payload_cache_key,
            request_message_type=request_message_type,
            calc_note=f"{calc_note}; OpenAI official local text/image token rules",
        )

    def _anthropic_count(
        self,
        *,
        model: str,
        message: str,
        image_64: Optional[str],
        payload_cache_key: str,
        request_message_type: str,
        calc_note: str,
    ) -> Optional[CountResult]:
        api_key = first_env("ANTHROPIC_API_KEY", "CLAUDE_API_KEY")
        if not api_key or not self.allow_api_calls:
            return None
        content: Any
        if image_64:
            content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": strip_data_url(image_64),
                    },
                },
                {"type": "text", "text": message or ""},
            ]
        else:
            content = message or ""
        count_model = self._model_for_env(model, "claude")
        payload = {
            "model": count_model,
            "messages": [{"role": "user", "content": content}],
        }
        data = self._post_json(
            "https://api.anthropic.com/v1/messages/count_tokens",
            headers={
                "x-api-key": api_key,
                "anthropic-version": os.getenv("ANTHROPIC_VERSION", "2023-06-01"),
                "content-type": "application/json",
            },
            payload=payload,
        )
        tokens = safe_int(data.get("input_tokens"))
        if tokens is None:
            raise RuntimeError(f"Anthropic count_tokens response missing input_tokens: {data}")
        width = height = None
        if image_64:
            width, height = image_size_from_base64(image_64)
        return self._ok(
            latest_input_tokens=tokens,
            latest_text_tokens=None,
            latest_image_tokens=None,
            latest_image_width=width,
            latest_image_height=height,
            text_token_method="anthropic_messages_count_tokens_api",
            image_token_method="included_in_anthropic_count_tokens_api",
            payload_cache_key=payload_cache_key,
            request_message_type=request_message_type,
            calc_note=f"{calc_note}; model_for_count={count_model}",
        )

    def _gemini_count(
        self,
        *,
        model: str,
        message: str,
        image_64: Optional[str],
        payload_cache_key: str,
        request_message_type: str,
        calc_note: str,
    ) -> Optional[CountResult]:
        api_key = first_env("GEMINI_API_KEY", "GOOGLE_API_KEY")
        if not api_key or not self.allow_api_calls:
            return None
        parts: List[Dict[str, Any]] = [{"text": message or ""}]
        if image_64:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": strip_data_url(image_64),
                    }
                }
            )
        count_model = self._model_for_env(model, "gemini")
        data = self._post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{count_model}:countTokens?key={api_key}",
            headers={"content-type": "application/json"},
            payload={"contents": [{"role": "user", "parts": parts}]},
        )
        tokens = safe_int(data.get("totalTokens") or data.get("total_tokens"))
        if tokens is None:
            raise RuntimeError(f"Gemini countTokens response missing totalTokens: {data}")
        width = height = None
        if image_64:
            width, height = image_size_from_base64(image_64)
        return self._ok(
            latest_input_tokens=tokens,
            latest_text_tokens=None,
            latest_image_tokens=None,
            latest_image_width=width,
            latest_image_height=height,
            text_token_method="gemini_count_tokens_api",
            image_token_method="included_in_gemini_count_tokens_api",
            payload_cache_key=payload_cache_key,
            request_message_type=request_message_type,
            calc_note=f"{calc_note}; model_for_count={count_model}",
        )

    def _kimi_count(
        self,
        *,
        model: str,
        message: str,
        image_64: Optional[str],
        payload_cache_key: str,
        request_message_type: str,
        calc_note: str,
    ) -> Optional[CountResult]:
        api_key = first_env("MOONSHOT_API_KEY", "KIMI_API_KEY")
        if not api_key or not self.allow_api_calls:
            return None
        if image_64:
            content: Any = [
                {"type": "text", "text": message or ""},
                {"type": "image_url", "image_url": {"url": image_data_url(image_64)}},
            ]
        else:
            content = message or ""
        count_model = self._model_for_env(model, "kimi")
        data = self._post_json(
            os.getenv("MOONSHOT_TOKEN_COUNT_URL", "https://api.moonshot.ai/v1/tokenizers/estimate-token-count"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            payload={
                "model": count_model,
                "messages": [{"role": "user", "content": content}],
            },
        )
        body = data.get("data") if isinstance(data.get("data"), dict) else data
        tokens = safe_int(body.get("total_tokens") if isinstance(body, dict) else None)
        if tokens is None:
            raise RuntimeError(f"Kimi estimate-token-count response missing total_tokens: {data}")
        width = height = None
        if image_64:
            width, height = image_size_from_base64(image_64)
        return self._ok(
            latest_input_tokens=tokens,
            latest_text_tokens=None,
            latest_image_tokens=None,
            latest_image_width=width,
            latest_image_height=height,
            text_token_method="moonshot_estimate_token_count_api",
            image_token_method="included_in_moonshot_estimate_token_count_api",
            payload_cache_key=payload_cache_key,
            request_message_type=request_message_type,
            calc_note=f"{calc_note}; model_for_count={count_model}",
        )

    def _xai_count(
        self,
        *,
        model: str,
        message: str,
        image_64: Optional[str],
        payload_cache_key: str,
        request_message_type: str,
        calc_note: str,
    ) -> Optional[CountResult]:
        if image_64:
            return None
        api_key = first_env("XAI_API_KEY")
        if not api_key or not self.allow_api_calls:
            return None
        count_model = self._model_for_env(model, "grok")
        data = self._post_json(
            os.getenv("XAI_TOKENIZE_URL", "https://api.x.ai/v1/tokenize-text"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            payload={"model": count_model, "text": message or ""},
        )
        token_ids = data.get("token_ids")
        if not isinstance(token_ids, list):
            raise RuntimeError(f"xAI tokenize-text response missing token_ids: {data}")
        return self._ok(
            latest_input_tokens=len(token_ids),
            latest_text_tokens=len(token_ids),
            latest_image_tokens=0,
            latest_image_width=None,
            latest_image_height=None,
            text_token_method="xai_tokenize_text_api",
            image_token_method="none",
            payload_cache_key=payload_cache_key,
            request_message_type=request_message_type,
            calc_note=f"{calc_note}; model_for_count={count_model}",
        )

    def _doubao_count(
        self,
        *,
        model: str,
        message: str,
        image_64: Optional[str],
        payload_cache_key: str,
        request_message_type: str,
        calc_note: str,
    ) -> Optional[CountResult]:
        if image_64:
            return None
        api_key = first_env("ARK_API_KEY", "VOLCENGINE_API_KEY")
        if not api_key or not self.allow_api_calls:
            return None
        count_model = self._model_for_env(model, "doubao")
        data = self._post_json(
            os.getenv("ARK_TOKENIZATION_URL", "https://ark.cn-beijing.volces.com/api/v3/tokenization"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            payload={"model": count_model, "text": message or ""},
        )
        tokens = (
            safe_int(data.get("total_tokens"))
            or safe_int(data.get("totalTokens"))
            or safe_int(data.get("token_count"))
            or safe_int(data.get("tokenCount"))
        )
        if tokens is None and isinstance(data.get("tokens"), list):
            tokens = len(data["tokens"])
        if tokens is None and isinstance(data.get("token_ids"), list):
            tokens = len(data["token_ids"])
        if tokens is None:
            raise RuntimeError(f"Ark tokenization response missing token count: {data}")
        return self._ok(
            latest_input_tokens=tokens,
            latest_text_tokens=tokens,
            latest_image_tokens=0,
            latest_image_width=None,
            latest_image_height=None,
            text_token_method="volcengine_ark_tokenization_api",
            image_token_method="none",
            payload_cache_key=payload_cache_key,
            request_message_type=request_message_type,
            calc_note=f"{calc_note}; model_for_count={count_model}",
        )

    def _hf_tokenizer_count(
        self,
        *,
        family: str,
        model: str,
        default_model: Optional[str],
        message: str,
        image_64: Optional[str],
        payload_cache_key: str,
        request_message_type: str,
        calc_note: str,
    ) -> Optional[CountResult]:
        if image_64:
            return None
        env_model = os.getenv(f"{family.upper()}_TOKENIZER_MODEL", "").strip()
        env_path = os.getenv(f"{family.upper()}_TOKENIZER_PATH", "").strip()
        tokenizer_name = env_path or env_model or default_model
        if not tokenizer_name:
            return None
        try:
            from transformers import AutoTokenizer
        except Exception as exc:
            raise RuntimeError(f"transformers unavailable for {family} tokenizer: {exc}")
        if tokenizer_name not in self.hf_tokenizers:
            self.hf_tokenizers[tokenizer_name] = AutoTokenizer.from_pretrained(
                tokenizer_name,
                trust_remote_code=True,
            )
        tokenizer = self.hf_tokenizers[tokenizer_name]
        messages = [{"role": "user", "content": message or ""}]
        try:
            token_ids = tokenizer.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
            )
        except Exception:
            token_ids = tokenizer.encode(message or "", add_special_tokens=False)
        return self._ok(
            latest_input_tokens=len(token_ids),
            latest_text_tokens=len(token_ids),
            latest_image_tokens=0,
            latest_image_width=None,
            latest_image_height=None,
            text_token_method=f"transformers_auto_tokenizer:{family}:{tokenizer_name}",
            image_token_method="none",
            payload_cache_key=payload_cache_key,
            request_message_type=request_message_type,
            calc_note=f"{calc_note}; model={model}; tokenizer={tokenizer_name}",
        )

    def _approximate_count(
        self,
        *,
        model: str,
        message: str,
        image_64: Optional[str],
        payload_cache_key: str,
        request_message_type: str,
        calc_note: str,
    ) -> CountResult:
        text_tokens = len(self.encoding.encode(message or ""))
        image_tokens, width, height, image_method = count_image_tokens(model, image_64)
        return self._ok(
            latest_input_tokens=text_tokens + image_tokens,
            latest_text_tokens=text_tokens,
            latest_image_tokens=image_tokens,
            latest_image_width=width,
            latest_image_height=height,
            text_token_method="approximate_tiktoken_o200k_base",
            image_token_method=f"approximate:{image_method}",
            payload_cache_key=payload_cache_key,
            request_message_type=request_message_type,
            calc_note=f"{calc_note}; approximate fallback explicitly enabled",
        )

    def count(
        self,
        *,
        model: str,
        message: str,
        image_64: Optional[str],
        payload_cache_key: str,
        request_message_type: str,
        calc_note: str,
        latest_usage: Optional[TokenUsage],
    ) -> CountResult:
        if self.mode == "db_first" and self.use_db_prompt_fallback:
            db_result = self._db_usage_result(
                latest_usage=latest_usage,
                payload_cache_key=payload_cache_key,
                request_message_type=request_message_type,
                calc_note=calc_note,
            )
            if db_result is not None:
                return db_result

        family = model_family(model)
        errors: List[str] = []

        try:
            if family == "openai":
                return self._local_openai_count(
                    model=model,
                    message=message,
                    image_64=image_64,
                    payload_cache_key=payload_cache_key,
                    request_message_type=request_message_type,
                    calc_note=calc_note,
                )
            if family == "claude":
                result = self._anthropic_count(
                    model=model,
                    message=message,
                    image_64=image_64,
                    payload_cache_key=payload_cache_key,
                    request_message_type=request_message_type,
                    calc_note=calc_note,
                )
                if result is not None:
                    return result
            elif family == "gemini":
                result = self._gemini_count(
                    model=model,
                    message=message,
                    image_64=image_64,
                    payload_cache_key=payload_cache_key,
                    request_message_type=request_message_type,
                    calc_note=calc_note,
                )
                if result is not None:
                    return result
            elif family == "kimi":
                result = self._kimi_count(
                    model=model,
                    message=message,
                    image_64=image_64,
                    payload_cache_key=payload_cache_key,
                    request_message_type=request_message_type,
                    calc_note=calc_note,
                )
                if result is not None:
                    return result
            elif family == "grok":
                result = self._xai_count(
                    model=model,
                    message=message,
                    image_64=image_64,
                    payload_cache_key=payload_cache_key,
                    request_message_type=request_message_type,
                    calc_note=calc_note,
                )
                if result is not None:
                    return result
            elif family == "doubao":
                result = self._doubao_count(
                    model=model,
                    message=message,
                    image_64=image_64,
                    payload_cache_key=payload_cache_key,
                    request_message_type=request_message_type,
                    calc_note=calc_note,
                )
                if result is not None:
                    return result
            elif family == "qwen":
                result = self._hf_tokenizer_count(
                    family="qwen",
                    model=model,
                    default_model=os.getenv("QWEN_DEFAULT_TOKENIZER_MODEL", "Qwen/Qwen2.5-72B-Instruct"),
                    message=message,
                    image_64=image_64,
                    payload_cache_key=payload_cache_key,
                    request_message_type=request_message_type,
                    calc_note=calc_note,
                )
                if result is not None:
                    return result
            elif family == "ernie":
                result = self._hf_tokenizer_count(
                    family="ernie",
                    model=model,
                    default_model=os.getenv("ERNIE_DEFAULT_TOKENIZER_MODEL", "baidu/ERNIE-4.5-0.3B-PT"),
                    message=message,
                    image_64=image_64,
                    payload_cache_key=payload_cache_key,
                    request_message_type=request_message_type,
                    calc_note=calc_note,
                )
                if result is not None:
                    return result
            elif family == "mistral":
                result = self._hf_tokenizer_count(
                    family="mistral",
                    model=model,
                    default_model=os.getenv("MISTRAL_DEFAULT_TOKENIZER_MODEL", ""),
                    message=message,
                    image_64=image_64,
                    payload_cache_key=payload_cache_key,
                    request_message_type=request_message_type,
                    calc_note=calc_note,
                )
                if result is not None:
                    return result
        except Exception as exc:
            errors.append(f"{family}:{type(exc).__name__}: {exc}")

        if self.use_db_prompt_fallback:
            db_result = self._db_usage_result(
                latest_usage=latest_usage,
                payload_cache_key=payload_cache_key,
                request_message_type=request_message_type,
                calc_note=f"{calc_note}; official counter failed_or_unavailable={errors or family}",
            )
            if db_result is not None:
                return db_result

        if self.allow_approximate_fallback:
            return self._approximate_count(
                model=model,
                message=message,
                image_64=image_64,
                payload_cache_key=payload_cache_key,
                request_message_type=request_message_type,
                calc_note=f"{calc_note}; official counter failed_or_unavailable={errors or family}",
            )

        return self._missing(
            payload_cache_key=payload_cache_key,
            request_message_type=request_message_type,
            calc_note=f"{calc_note}; official counter failed_or_unavailable={errors or family}",
        )


def load_contexts(conn) -> Tuple[Dict[str, PromptContext], Dict[str, PromptContext]]:
    return load_contexts_from_db(conn)


def load_contexts_from_local() -> Tuple[Dict[str, PromptContext], Dict[str, PromptContext]]:
    valid_contexts: Dict[str, PromptContext] = {}
    invalid_contexts: Dict[str, PromptContext] = {}

    valid_records = json.loads(PPTR_PROMPTS_JSON.read_text(encoding="utf-8"))
    for idx, row in enumerate(valid_records, start=1):
        case_id = f"P{idx:03d}"
        valid_contexts[case_id] = PromptContext(
            task="valid_pptr",
            case_id=case_id,
            target_id=str(row.get("app_id") or ""),
            global_text=str(row.get("global") or ""),
            component_text=str(row.get("component") or ""),
            adjacent_text=str(row.get("adjacent") or ""),
            restrictive_text=str(row.get("restrictive") or ""),
            guiding_text=str(row.get("guiding") or ""),
            xml_text="",
            image_64="",
        )

    invalid_records = json.loads(BUG_PROMPTS_JSON.read_text(encoding="utf-8"))
    for row in invalid_records:
        page_id = int(float(row["page_id"]))
        case_id = f"B{page_id:03d}"
        invalid_contexts[case_id] = PromptContext(
            task="invalid_bug",
            case_id=case_id,
            target_id=str(page_id),
            global_text=str(row.get("global") or ""),
            component_text=str(row.get("component") or ""),
            adjacent_text=str(row.get("adjacent") or ""),
            restrictive_text=str(row.get("restrictive") or ""),
            guiding_text=str(row.get("guiding") or ""),
            xml_text="",
            image_64="",
        )

    return valid_contexts, invalid_contexts


def load_contexts_from_db(conn) -> Tuple[Dict[str, PromptContext], Dict[str, PromptContext]]:
    valid_contexts: Dict[str, PromptContext] = {}
    invalid_contexts: Dict[str, PromptContext] = {}
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT app_id, TRIM(index_id) AS index_id, `global`, component, adjacent,
                   restrictive, guiding
            FROM t_valid_prompt_context
            """
        )
        for row in cursor.fetchall():
            case_id = str(row.get("index_id") or "").strip()
            if not case_id:
                continue
            valid_contexts[case_id] = PromptContext(
                task="valid_pptr",
                case_id=case_id,
                target_id=str(row.get("app_id") or ""),
                global_text=str(row.get("global") or ""),
                component_text=str(row.get("component") or ""),
                adjacent_text=str(row.get("adjacent") or ""),
                restrictive_text=str(row.get("restrictive") or ""),
                guiding_text=str(row.get("guiding") or ""),
                xml_text="",
                image_64="",
            )

        cursor.execute(
            """
            SELECT page_id, `global`, component, adjacent, restrictive, guiding
            FROM t_invalid_prompt_context
            """
        )
        for row in cursor.fetchall():
            page_id = int(row["page_id"])
            case_id = f"B{page_id:03d}"
            invalid_contexts[case_id] = PromptContext(
                task="invalid_bug",
                case_id=case_id,
                target_id=str(page_id),
                global_text=str(row.get("global") or ""),
                component_text=str(row.get("component") or ""),
                adjacent_text=str(row.get("adjacent") or ""),
                restrictive_text=str(row.get("restrictive") or ""),
                guiding_text=str(row.get("guiding") or ""),
                xml_text="",
                image_64="",
            )
    return valid_contexts, invalid_contexts


def build_prompt(ctx: PromptContext, prompt_setting: str, prompt_variant: str) -> Tuple[str, Optional[str]]:
    if prompt_setting == "extracted":
        if prompt_variant == "full":
            parts = [ctx.global_text, ctx.component_text, ctx.adjacent_text, ctx.restrictive_text, ctx.guiding_text]
        elif prompt_variant == "no_global":
            parts = [ctx.component_text, ctx.adjacent_text, ctx.restrictive_text, ctx.guiding_text]
        elif prompt_variant == "no_component":
            parts = [ctx.global_text, ctx.adjacent_text, ctx.restrictive_text, ctx.guiding_text]
        elif prompt_variant == "no_adjacent":
            parts = [ctx.global_text, ctx.component_text, ctx.restrictive_text, ctx.guiding_text]
        elif prompt_variant == "no_guiding":
            parts = [ctx.global_text, ctx.component_text, ctx.adjacent_text, ctx.restrictive_text]
        else:
            raise ValueError(f"Unsupported extracted prompt_variant: {prompt_variant}")
        return "\n".join([p for p in parts if str(p).strip()]).strip(), None

    if prompt_setting == "xml":
        xml_text = ctx.xml_text or load_xml_text_from_cache(ctx)
        parts = [ctx.global_text, xml_text, ctx.restrictive_text, ctx.guiding_text]
        return "\n".join([p for p in parts if str(p).strip()]).strip(), None

    if prompt_setting == "vision":
        parts = [ctx.global_text, ctx.restrictive_text, ctx.guiding_text]
        image_64 = ctx.image_64 or load_image_64_from_cache(ctx)
        return "\n".join([p for p in parts if str(p).strip()]).strip(), image_64

    raise ValueError(f"Unsupported prompt_setting: {prompt_setting}")


def context_for_row(
    row: Dict[str, Any],
    valid_contexts: Dict[str, PromptContext],
    invalid_contexts: Dict[str, PromptContext],
) -> PromptContext:
    case_id = str(row["case_id"])
    task = str(row["task"])
    if task == "valid_pptr":
        return valid_contexts[case_id]
    if task == "invalid_bug":
        return invalid_contexts[case_id]
    if case_id.startswith("P"):
        return valid_contexts[case_id]
    return invalid_contexts[case_id]


def latest_feedback_payload(row: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], str, str]:
    attached_message = row.get("latest_feedback_message")
    if attached_message is not None:
        message = str(attached_message or "")
        if not message:
            return None, None, "unknown", "attached_feedback_payload_empty"
        message, compacted = compact_feedback_message(message)
        note = "from_source_feedback_prompt_text" if row.get("latest_feedback_payload_source") == "source_feedback_prompt_text" else "from_json_extract_feedback_payload"
        if compacted:
            note = f"{note}; compacted_failed_history_last5_no_raw_output"
        return (
            message,
            strip_data_url(row.get("latest_feedback_image_url")),
            str(row.get("latest_request_message_type") or "unknown"),
            note,
        )

    raw = safe_json_loads(row.get("raw_response"), default={})
    if not isinstance(raw, dict):
        return None, None, "unknown", "raw_response_not_json"
    latest_turn = raw.get("latest_turn") or {}
    if not latest_turn and isinstance(raw.get("turns"), list) and raw["turns"]:
        latest_turn = raw["turns"][-1] or {}
    if not isinstance(latest_turn, dict):
        return None, None, "unknown", "latest_turn_missing"
    payload = latest_turn.get("request_payload") or {}
    if isinstance(payload, str):
        payload = safe_json_loads(payload, default={})
    if not isinstance(payload, dict):
        return None, None, "unknown", "request_payload_missing"
    message = str(payload.get("message") or "")
    if not message:
        return None, None, "unknown", "request_payload_message_missing"
    message, compacted = compact_feedback_message(message)
    note = "from_feedback_raw_response"
    if compacted:
        note = f"{note}; compacted_failed_history_last5_no_raw_output"
    return (
        message,
        strip_data_url(payload.get("imageUrl")),
        str(latest_turn.get("request_message_type") or payload.get("request_message_type") or "unknown"),
        note,
    )


def compact_feedback_message(message: str) -> Tuple[str, bool]:
    """Normalize feedback prompts to the current bounded-history policy.

    Older RQ2.2 rows may contain verbose raw model outputs inside
    [FAILED HISTORY].  The current feedback builder keeps only the recent
    failed-turn metadata and previous parsed JSON.  Reconstruct that section
    before official token counting so GPT/Claude use the same accounting
    strategy.
    """
    if "[FAILED HISTORY]" not in message:
        return message, False

    marker = "[FAILED HISTORY]"
    start = message.find(marker)
    content_start = start + len(marker)
    if content_start < len(message) and message[content_start:content_start + 1] == "\n":
        content_start += 1

    section_starts = [
        pos for pos in [
            message.find("\n[LATEST ERROR DETAIL]", content_start),
            message.find("\n[REGENERATION REQUIREMENTS]", content_start),
            message.find("\n[REQUIRED RESOURCE IDS]", content_start),
            message.find("\n[REQUIRED JSON SCHEMA]", content_start),
        ]
        if pos != -1
    ]
    history_end = min(section_starts) if section_starts else len(message)
    history_text = message[content_start:history_end].strip()
    turn_blocks = list(
        re.finditer(
            r"(?ms)^\[TURN\s+([^\]]+)\]\n(.*?)(?=^\[TURN\s+[^\]]+\]\n|\Z)",
            history_text,
        )
    )
    if not turn_blocks:
        return message, False

    normalized_turns: List[str] = []
    for match in turn_blocks[-5:]:
        turn = match.group(1).strip()
        block = match.group(2)
        parse_success = _extract_feedback_scalar(block, "parse_success", "(unknown)")
        eval_success = _extract_feedback_scalar(block, "eval_success", "(unknown)")
        error_type = _extract_feedback_scalar(block, "error_type", "(unknown)")
        error_detail = _extract_feedback_scalar(block, "error_detail", "")
        if len(error_detail) > 500:
            error_detail = error_detail[:500] + "\n...[truncated]"
        previous_json = _extract_previous_json(block)
        normalized_turns.append(
            f"[TURN {turn}]\n"
            f"- parse_success: {parse_success}\n"
            f"- eval_success: {eval_success}\n"
            f"- error_type: {error_type}\n"
            f"- error_detail: {error_detail}\n"
            f"- previous_json:\n{previous_json}"
        )

    new_history = "\n\n".join(normalized_turns)
    compacted = f"{message[:content_start]}{new_history}{message[history_end:]}"
    return compacted, compacted != message


def _extract_feedback_scalar(block: str, field: str, default: str) -> str:
    match = re.search(rf"(?m)^- {re.escape(field)}:\s*(.*)$", block)
    if not match:
        return default
    return match.group(1).strip()


def _extract_previous_json(block: str) -> str:
    marker = "- previous_json:"
    pos = block.find(marker)
    if pos == -1:
        return "(not available)"
    value = block[pos + len(marker):].strip()
    if not value:
        return "(not available)"
    return value


def count_payload(
    *,
    model: str,
    message: str,
    image_64: Optional[str],
    encoding: Any,
    payload_cache_key: str,
    request_message_type: str,
    calc_note: str,
) -> CountResult:
    text_tokens = len(encoding.encode(message or ""))
    try:
        image_tokens, width, height, image_method = count_image_tokens(model, image_64)
    except Exception as exc:
        image_tokens, width, height, image_method = 0, None, None, f"image_count_error:{type(exc).__name__}"
        calc_note = f"{calc_note}; image_count_error={exc}"
    return CountResult(
        latest_input_tokens=int(text_tokens + image_tokens),
        latest_text_tokens=int(text_tokens),
        latest_image_tokens=int(image_tokens),
        latest_image_width=width,
        latest_image_height=height,
        text_token_method="tiktoken_o200k_base",
        image_token_method=image_method,
        payload_cache_key=payload_cache_key,
        request_message_type=request_message_type,
        calc_note=calc_note,
        status="computed",
    )


def fetch_rows(
    conn,
    *,
    source_table: str,
    experiments: Sequence[str],
    models: Sequence[str],
    settings: Sequence[str],
    case_ids: Sequence[str],
    limit: int,
    fetch_batch_size: int,
) -> List[Dict[str, Any]]:
    clauses = [
        f"experiment IN ({placeholders(experiments)})",
        "tau = -1",
        "combo_index = -1",
    ]
    params: List[Any] = list(experiments)
    if models:
        clauses.append(f"canonical_model IN ({placeholders(models)})")
        params.extend(models)
    if settings:
        clauses.append(f"prompt_setting IN ({placeholders(settings)})")
        params.extend(settings)
    if case_ids:
        clauses.append(f"case_id IN ({placeholders(case_ids)})")
        params.extend(case_ids)

    base_where = " AND ".join(clauses)
    fetch_size = max(1, int(fetch_batch_size or 1000))
    rows: List[Dict[str, Any]] = []
    last_curated_id = 0
    page = 0

    while True:
        remaining = limit - len(rows) if limit > 0 else fetch_size
        if limit > 0 and remaining <= 0:
            break
        page_limit = min(fetch_size, remaining) if limit > 0 else fetch_size
        page_params = [*params, last_curated_id, page_limit]
        sql = f"""
            SELECT
                curated_id,
                experiment,
                case_id,
                task,
                canonical_model,
                model,
                exact_model,
                prompt_setting,
                prompt_variant,
                feedback_mode,
                attempt_id,
                invalid_index,
                tau,
                combo_index,
                session_ids_json,
                NULL AS raw_response
            FROM {source_table}
            WHERE {base_where}
              AND curated_id > %s
            ORDER BY curated_id
            LIMIT %s
        """
        with conn.cursor() as cursor:
            cursor.execute(sql, page_params)
            page_rows = list(cursor.fetchall())
        if not page_rows:
            break
        rows.extend(page_rows)
        last_curated_id = int(page_rows[-1]["curated_id"])
        page += 1
        log(
            f"[FETCH_PROGRESS] page={page} rows={len(rows)} "
            f"last_curated_id={last_curated_id}"
        )
        if limit > 0 and len(rows) >= limit:
            break

    return rows


def row_needs_raw_response(row: Dict[str, Any]) -> bool:
    experiment = str(row.get("experiment") or "")
    if experiment == "rq2_valid_feedback":
        return int(row.get("attempt_id") or -1) > 1
    if experiment == "rq2_invalid_feedback":
        return int(row.get("invalid_index") or -1) > 1
    return False


def fetch_feedback_payload_chunk(
    conn,
    *,
    source_table: str,
    generation_table: str,
    chunk: Sequence[int],
) -> List[Dict[str, Any]]:
    if not chunk:
        return []

    sql = f"""
        SELECT
            c.curated_id,
            COALESCE(
                CASE WHEN JSON_VALID(s.feedback_prompt_text)
                     THEN JSON_UNQUOTE(JSON_EXTRACT(s.feedback_prompt_text, '$.message'))
                     ELSE NULL
                END,
                CASE WHEN JSON_VALID(c.raw_response)
                     THEN COALESCE(
                         JSON_UNQUOTE(JSON_EXTRACT(c.raw_response, '$.latest_turn.request_payload.message')),
                         JSON_UNQUOTE(JSON_EXTRACT(c.raw_response, '$.turns[last].request_payload.message'))
                     )
                     ELSE NULL
                END
            ) AS latest_feedback_message,
            COALESCE(
                CASE WHEN JSON_VALID(s.feedback_prompt_text)
                     THEN JSON_UNQUOTE(JSON_EXTRACT(s.feedback_prompt_text, '$.imageUrl'))
                     ELSE NULL
                END,
                CASE WHEN JSON_VALID(c.raw_response)
                     THEN COALESCE(
                         JSON_UNQUOTE(JSON_EXTRACT(c.raw_response, '$.latest_turn.request_payload.imageUrl')),
                         JSON_UNQUOTE(JSON_EXTRACT(c.raw_response, '$.turns[last].request_payload.imageUrl'))
                     )
                     ELSE NULL
                END
            ) AS latest_feedback_image_url,
            COALESCE(
                CASE WHEN JSON_VALID(s.feedback_prompt_text)
                     THEN JSON_UNQUOTE(JSON_EXTRACT(s.feedback_prompt_text, '$.request_message_type'))
                     ELSE NULL
                END,
                CASE WHEN JSON_VALID(c.raw_response)
                     THEN COALESCE(
                         JSON_UNQUOTE(JSON_EXTRACT(c.raw_response, '$.latest_turn.request_message_type')),
                         JSON_UNQUOTE(JSON_EXTRACT(c.raw_response, '$.turns[last].request_message_type'))
                     )
                     ELSE NULL
                END
            ) AS latest_request_message_type,
            CASE WHEN JSON_VALID(s.feedback_prompt_text)
                 AND JSON_UNQUOTE(JSON_EXTRACT(s.feedback_prompt_text, '$.message')) IS NOT NULL
                 THEN 'source_feedback_prompt_text'
                 WHEN JSON_VALID(c.raw_response)
                 THEN COALESCE(
                     'curated_raw_response',
                     'curated_raw_response'
                 )
                 ELSE NULL
            END AS latest_feedback_payload_source
        FROM {source_table} c
        LEFT JOIN {generation_table} s ON s.id = c.source_id
        WHERE c.curated_id IN ({placeholders(chunk)})
    """

    for attempt in range(1, 4):
        try:
            conn.ping(reconnect=True)
            with conn.cursor() as cursor:
                cursor.execute(sql, list(chunk))
                return list(cursor.fetchall())
        except pymysql.err.OperationalError as exc:
            log(
                f"[FEEDBACK_PAYLOAD_RETRY] rows={len(chunk)} "
                f"attempt={attempt}/3 error={type(exc).__name__}: {exc}"
            )
            time.sleep(min(5, attempt))

    if len(chunk) > 1:
        mid = len(chunk) // 2
        return [
            *fetch_feedback_payload_chunk(conn, source_table=source_table, generation_table=generation_table, chunk=chunk[:mid]),
            *fetch_feedback_payload_chunk(conn, source_table=source_table, generation_table=generation_table, chunk=chunk[mid:]),
        ]

    log(f"[FEEDBACK_PAYLOAD_SKIP] curated_id={chunk[0]} reason=mysql_query_failed")
    return []


def attach_feedback_payloads(
    conn,
    *,
    source_table: str,
    generation_table: str,
    rows: Sequence[Dict[str, Any]],
    chunk_size: int = 50,
) -> int:
    ids = [int(row["curated_id"]) for row in rows if row_needs_raw_response(row)]
    if not ids:
        return 0

    payload_by_id: Dict[int, Dict[str, Any]] = {}
    fetch_size = max(1, int(chunk_size or 50))
    for offset in range(0, len(ids), fetch_size):
        chunk = ids[offset: offset + fetch_size]
        for item in fetch_feedback_payload_chunk(
            conn,
            source_table=source_table,
            generation_table=generation_table,
            chunk=chunk,
        ):
            payload_by_id[int(item["curated_id"])] = item
        log(
            f"[FEEDBACK_PAYLOAD_PROGRESS] attached={len(payload_by_id)}/{len(ids)} "
            f"last_offset={offset + len(chunk)}"
        )

    for row in rows:
        curated_id = int(row["curated_id"])
        payload = payload_by_id.get(curated_id)
        if payload:
            row["latest_feedback_message"] = payload.get("latest_feedback_message")
            row["latest_feedback_image_url"] = payload.get("latest_feedback_image_url")
            row["latest_request_message_type"] = payload.get("latest_request_message_type")
            row["latest_feedback_payload_source"] = payload.get("latest_feedback_payload_source")
    return len(payload_by_id)


def fetch_token_usage(
    conn,
    *,
    token_table: str,
    session_ids: Sequence[str],
    chunk_size: int = 1000,
) -> Tuple[Dict[str, TokenUsage], int, int]:
    usage: Dict[str, TokenUsage] = {}
    duplicate_rows = 0
    conflict_rows = 0
    wanted = unique_keep_order([sid for sid in session_ids if sid])
    if not wanted:
        return usage, duplicate_rows, conflict_rows
    with conn.cursor() as cursor:
        for offset in range(0, len(wanted), chunk_size):
            chunk = wanted[offset: offset + chunk_size]
            cursor.execute(
                f"""
                SELECT session_id, prompt_tokens, completion_tokens, total_tokens
                FROM {token_table}
                WHERE session_id IN ({placeholders(chunk)})
                """,
                list(chunk),
            )
            for row in cursor.fetchall():
                sid = str(row.get("session_id") or "").strip()
                item = TokenUsage(
                    prompt_tokens=safe_int(row.get("prompt_tokens")),
                    completion_tokens=safe_int(row.get("completion_tokens")),
                    total_tokens=safe_int(row.get("total_tokens")),
                )
                if sid in usage:
                    duplicate_rows += 1
                    old = usage[sid]
                    if old != item:
                        conflict_rows += 1
                        usage[sid] = TokenUsage(
                            prompt_tokens=max(v for v in [old.prompt_tokens, item.prompt_tokens] if v is not None)
                            if old.prompt_tokens is not None or item.prompt_tokens is not None
                            else None,
                            completion_tokens=max(
                                v for v in [old.completion_tokens, item.completion_tokens] if v is not None
                            )
                            if old.completion_tokens is not None or item.completion_tokens is not None
                            else None,
                            total_tokens=max(v for v in [old.total_tokens, item.total_tokens] if v is not None)
                            if old.total_tokens is not None or item.total_tokens is not None
                            else None,
                        )
                    continue
                usage[sid] = item
    return usage, duplicate_rows, conflict_rows


def ensure_target_table(conn, *, target_table: str) -> None:
    sql = f"""
        CREATE TABLE IF NOT EXISTS {target_table} (
            curated_id BIGINT NOT NULL PRIMARY KEY,
            experiment VARCHAR(64) NOT NULL,
            case_id VARCHAR(64) NOT NULL,
            task VARCHAR(32) NOT NULL,
            canonical_model VARCHAR(128) NOT NULL,
            prompt_setting VARCHAR(32) NOT NULL,
            prompt_variant VARCHAR(64) NOT NULL,
            feedback_mode VARCHAR(32) NOT NULL,
            attempt_id INT NOT NULL,
            invalid_index INT NOT NULL,
            tau INT NOT NULL,
            combo_index INT NOT NULL,
            session_ids_json JSON NULL,
            latest_session_id VARCHAR(64) NULL,
            session_count INT NOT NULL DEFAULT 0,
            token_records_found INT NOT NULL DEFAULT 0,
            missing_token_sessions_json JSON NULL,
            latest_recomputed_input_tokens BIGINT NULL,
            latest_text_tokens BIGINT NULL,
            latest_image_tokens BIGINT NULL,
            latest_image_width INT NULL,
            latest_image_height INT NULL,
            recomputed_input_tokens BIGINT NULL,
            input_sessions_found INT NOT NULL DEFAULT 0,
            missing_input_sessions_json JSON NULL,
            latest_completion_tokens BIGINT NULL,
            completion_tokens BIGINT NULL,
            latest_db_total_tokens BIGINT NULL,
            db_total_tokens BIGINT NULL,
            estimated_total_tokens BIGINT NULL,
            input_price_per_1m DECIMAL(12,6) NULL,
            output_price_per_1m DECIMAL(12,6) NULL,
            estimated_input_cost_usd DECIMAL(18,8) NULL,
            estimated_output_cost_usd DECIMAL(18,8) NULL,
            estimated_total_cost_usd DECIMAL(18,8) NULL,
            price_source VARCHAR(128) NOT NULL,
            text_token_method VARCHAR(128) NOT NULL,
            image_token_method VARCHAR(128) NOT NULL,
            payload_cache_key VARCHAR(512) NOT NULL,
            request_message_type VARCHAR(32) NOT NULL,
            status VARCHAR(64) NOT NULL,
            calc_note TEXT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            KEY idx_exp_model_setting (experiment, canonical_model, prompt_setting),
            KEY idx_case (case_id),
            KEY idx_latest_session (latest_session_id)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """
    with conn.cursor() as cursor:
        cursor.execute(sql)
    conn.commit()


def cost(tokens: Optional[int], price_per_1m: Optional[float]) -> Optional[float]:
    if tokens is None or price_per_1m is None:
        return None
    return float(tokens) / 1_000_000 * float(price_per_1m)


def sum_optional(values: Iterable[Optional[int]]) -> Optional[int]:
    found = [int(v) for v in values if v is not None]
    if not found:
        return None
    return int(sum(found))


def upsert_rows(conn, *, target_table: str, rows: Sequence[Dict[str, Any]]) -> None:
    if not rows:
        return
    columns = [
        "curated_id",
        "experiment",
        "case_id",
        "task",
        "canonical_model",
        "prompt_setting",
        "prompt_variant",
        "feedback_mode",
        "attempt_id",
        "invalid_index",
        "tau",
        "combo_index",
        "session_ids_json",
        "latest_session_id",
        "session_count",
        "token_records_found",
        "missing_token_sessions_json",
        "latest_recomputed_input_tokens",
        "latest_text_tokens",
        "latest_image_tokens",
        "latest_image_width",
        "latest_image_height",
        "recomputed_input_tokens",
        "input_sessions_found",
        "missing_input_sessions_json",
        "latest_completion_tokens",
        "completion_tokens",
        "latest_db_total_tokens",
        "db_total_tokens",
        "estimated_total_tokens",
        "input_price_per_1m",
        "output_price_per_1m",
        "estimated_input_cost_usd",
        "estimated_output_cost_usd",
        "estimated_total_cost_usd",
        "price_source",
        "text_token_method",
        "image_token_method",
        "payload_cache_key",
        "request_message_type",
        "status",
        "calc_note",
    ]
    update_cols = [col for col in columns if col != "curated_id"]
    sql = f"""
        INSERT INTO {target_table} ({",".join(columns)})
        VALUES ({",".join(["%s"] * len(columns))})
        ON DUPLICATE KEY UPDATE
            {",".join([f"{col}=VALUES({col})" for col in update_cols])}
    """
    values = [[row.get(col) for col in columns] for row in rows]
    conn.ping(reconnect=True)
    with conn.cursor() as cursor:
        cursor.executemany(sql, values)
    conn.commit()


def compute_latest_count(
    row: Dict[str, Any],
    *,
    valid_contexts: Dict[str, PromptContext],
    invalid_contexts: Dict[str, PromptContext],
    token_counter: OfficialTokenCounter,
    rq1_cache: Dict[str, CountResult],
    latest_usage: Optional[TokenUsage],
) -> CountResult:
    experiment = str(row["experiment"])
    model = str(row["canonical_model"])
    prompt_setting = str(row["prompt_setting"])
    prompt_variant = str(row["prompt_variant"])
    case_id = str(row["case_id"])

    if experiment in ("rq2_valid_feedback", "rq2_invalid_feedback"):
        message, image_64, request_message_type, note = latest_feedback_payload(row)
        if message is None:
            ctx = context_for_row(row, valid_contexts, invalid_contexts)
            message, fallback_image = build_prompt(ctx, prompt_setting, prompt_variant)
            image_64 = fallback_image
            request_message_type = "fallback_initial"
            note = f"{note}; fallback_to_reconstructed_prompt"
        key = f"rq2:{row['curated_id']}"
        return token_counter.count(
            model=model,
            message=message or "",
            image_64=image_64,
            payload_cache_key=key,
            request_message_type=request_message_type,
            calc_note=note,
            latest_usage=latest_usage,
        )

    key = f"rq1:{model}:{row['task']}:{case_id}:{prompt_setting}:{prompt_variant}"
    if key in rq1_cache:
        cached = rq1_cache[key]
        return CountResult(
            latest_input_tokens=cached.latest_input_tokens,
            latest_text_tokens=cached.latest_text_tokens,
            latest_image_tokens=cached.latest_image_tokens,
            latest_image_width=cached.latest_image_width,
            latest_image_height=cached.latest_image_height,
            text_token_method=cached.text_token_method,
            image_token_method=cached.image_token_method,
            payload_cache_key=key,
            request_message_type="rq1_cached_payload",
            calc_note="same reconstructed RQ1 payload reused across attempts/indices",
            status=cached.status,
        )

    ctx = context_for_row(row, valid_contexts, invalid_contexts)
    message, image_64 = build_prompt(ctx, prompt_setting, prompt_variant)
    result = token_counter.count(
        model=model,
        message=message,
        image_64=image_64,
        payload_cache_key=key,
        request_message_type="rq1_reconstructed_payload",
        calc_note="reconstructed from prompt context table",
        latest_usage=latest_usage,
    )
    rq1_cache[key] = result
    return result


def build_output_rows(
    rows: Sequence[Dict[str, Any]],
    *,
    valid_contexts: Dict[str, PromptContext],
    invalid_contexts: Dict[str, PromptContext],
    token_usage: Dict[str, TokenUsage],
    token_counter: OfficialTokenCounter,
    progress_every: int,
) -> List[Dict[str, Any]]:
    start = time.time()
    rq1_cache: Dict[str, CountResult] = {}
    interim: List[Dict[str, Any]] = []
    latest_input_by_session: Dict[str, Optional[int]] = {}

    for index, row in enumerate(rows, start=1):
        sessions = unique_keep_order(parse_session_ids(row.get("session_ids_json")))
        latest_session = sessions[-1] if sessions else None
        token_items = [token_usage.get(sid) for sid in sessions]
        latest_usage = token_usage.get(latest_session or "")

        if not sessions:
            count_result = CountResult(
                latest_input_tokens=None,
                latest_text_tokens=None,
                latest_image_tokens=None,
                latest_image_width=None,
                latest_image_height=None,
                text_token_method="none",
                image_token_method="none",
                payload_cache_key="skipped_empty_session",
                request_message_type="none",
                calc_note="session_ids_json is empty; skipped by design",
                status="skipped_empty_session",
            )
        else:
            try:
                count_result = compute_latest_count(
                    row,
                    valid_contexts=valid_contexts,
                    invalid_contexts=invalid_contexts,
                    token_counter=token_counter,
                    rq1_cache=rq1_cache,
                    latest_usage=latest_usage,
                )
            except Exception as exc:
                count_result = CountResult(
                    latest_input_tokens=None,
                    latest_text_tokens=None,
                    latest_image_tokens=None,
                    latest_image_width=None,
                    latest_image_height=None,
                    text_token_method="official_counter_error",
                    image_token_method="error",
                    payload_cache_key=f"error:{row['curated_id']}",
                    request_message_type="unknown",
                    calc_note=f"{type(exc).__name__}: {exc}",
                    status="count_error",
                )

        if latest_session:
            latest_input_by_session[latest_session] = count_result.latest_input_tokens

        token_records_found = sum(1 for item in token_items if item is not None)
        missing_token_sessions = [
            sid for sid, item in zip(sessions, token_items)
            if item is None
        ]
        latest_completion = latest_usage.completion_tokens if latest_usage else None
        latest_db_total = latest_usage.total_tokens if latest_usage else None
        completion_sum = sum_optional(item.completion_tokens if item else None for item in token_items)
        db_total_sum = sum_optional(item.total_tokens if item else None for item in token_items)

        input_price, output_price, price_source = MODEL_PRICES_USD_PER_1M.get(
            str(row["canonical_model"]),
            (None, None, "unknown_price"),
        )

        interim.append(
            {
                "curated_id": int(row["curated_id"]),
                "experiment": str(row["experiment"]),
                "case_id": str(row["case_id"]),
                "task": str(row["task"]),
                "canonical_model": str(row["canonical_model"]),
                "prompt_setting": str(row["prompt_setting"]),
                "prompt_variant": str(row["prompt_variant"]),
                "feedback_mode": str(row["feedback_mode"]),
                "attempt_id": int(row["attempt_id"]),
                "invalid_index": int(row["invalid_index"]),
                "tau": int(row["tau"]),
                "combo_index": int(row["combo_index"]),
                "session_ids_json": json.dumps(sessions, ensure_ascii=False),
                "latest_session_id": latest_session,
                "session_count": len(sessions),
                "token_records_found": token_records_found,
                "missing_token_sessions_json": json.dumps(missing_token_sessions, ensure_ascii=False),
                "latest_recomputed_input_tokens": count_result.latest_input_tokens,
                "latest_text_tokens": count_result.latest_text_tokens,
                "latest_image_tokens": count_result.latest_image_tokens,
                "latest_image_width": count_result.latest_image_width,
                "latest_image_height": count_result.latest_image_height,
                "latest_completion_tokens": latest_completion,
                "completion_tokens": completion_sum,
                "latest_db_total_tokens": latest_db_total,
                "db_total_tokens": db_total_sum,
                "input_price_per_1m": input_price,
                "output_price_per_1m": output_price,
                "price_source": price_source,
                "text_token_method": count_result.text_token_method,
                "image_token_method": count_result.image_token_method,
                "payload_cache_key": count_result.payload_cache_key,
                "request_message_type": count_result.request_message_type,
                "status": count_result.status,
                "calc_note": count_result.calc_note,
                "_sessions": sessions,
            }
        )

        if progress_every > 0 and (index % progress_every == 0 or index == len(rows)):
            log(
                f"[COUNT_PROGRESS] rows={index}/{len(rows)} "
                f"rq1_payload_cache={len(rq1_cache)} elapsed={time.time() - start:.1f}s"
            )

    output_rows: List[Dict[str, Any]] = []
    for item in interim:
        sessions = item.pop("_sessions")
        input_values = [latest_input_by_session.get(sid) for sid in sessions]
        missing_input_sessions = [
            sid for sid, value in zip(sessions, input_values)
            if value is None
        ]
        recomputed_input_sum = sum_optional(input_values)
        item["recomputed_input_tokens"] = recomputed_input_sum
        item["input_sessions_found"] = len(sessions) - len(missing_input_sessions)
        item["missing_input_sessions_json"] = json.dumps(missing_input_sessions, ensure_ascii=False)
        item["estimated_total_tokens"] = (
            recomputed_input_sum + item["completion_tokens"]
            if recomputed_input_sum is not None and item["completion_tokens"] is not None
            else None
        )
        item["estimated_input_cost_usd"] = cost(recomputed_input_sum, item["input_price_per_1m"])
        item["estimated_output_cost_usd"] = cost(item["completion_tokens"], item["output_price_per_1m"])
        if item["estimated_input_cost_usd"] is not None and item["estimated_output_cost_usd"] is not None:
            item["estimated_total_cost_usd"] = item["estimated_input_cost_usd"] + item["estimated_output_cost_usd"]
        else:
            item["estimated_total_cost_usd"] = None
        if item["status"] == "computed" and missing_input_sessions:
            item["status"] = "computed_with_missing_input_session"
            item["calc_note"] = f"{item['calc_note']}; missing input sessions for cumulative sum"
        output_rows.append(item)

    return output_rows


def write_summary_csv(output_dir: Path, rows: Sequence[Dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    import csv

    detail_path = output_dir / "recomputed_token_cost_detail.csv"
    summary_path = output_dir / "recomputed_token_cost_summary.csv"
    if rows:
        columns = list(rows[0].keys())
        with detail_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    groups: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in rows:
        key = (row["experiment"], row["canonical_model"], row["prompt_setting"])
        group = groups.setdefault(
            key,
            {
                "experiment": key[0],
                "canonical_model": key[1],
                "prompt_setting": key[2],
                "rows": 0,
                "computed_rows": 0,
                "skipped_empty_session": 0,
                "input_tokens": 0,
                "completion_tokens": 0,
                "estimated_total_cost_usd": 0.0,
                "priced_rows": 0,
            },
        )
        group["rows"] += 1
        if str(row.get("status")) == "skipped_empty_session":
            group["skipped_empty_session"] += 1
        if row.get("recomputed_input_tokens") is not None:
            group["computed_rows"] += 1
            group["input_tokens"] += int(row["recomputed_input_tokens"])
        if row.get("completion_tokens") is not None:
            group["completion_tokens"] += int(row["completion_tokens"])
        if row.get("estimated_total_cost_usd") is not None:
            group["estimated_total_cost_usd"] += float(row["estimated_total_cost_usd"])
            group["priced_rows"] += 1

    with summary_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "experiment",
            "canonical_model",
            "prompt_setting",
            "rows",
            "computed_rows",
            "skipped_empty_session",
            "input_tokens",
            "completion_tokens",
            "estimated_total_cost_usd",
            "priced_rows",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for key in sorted(groups):
            writer.writerow(groups[key])


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Recompute input token counts from reconstructed prompts, join output tokens "
            "by session id, and write a new token/cost table."
        )
    )
    parser.add_argument("--source-table", default=DEFAULT_SOURCE_TABLE)
    parser.add_argument(
        "--generation-table",
        default=DEFAULT_GENERATION_TABLE,
        help="Original generation table used to recover feedback_prompt_text via curated.source_id.",
    )
    parser.add_argument("--token-table", default=DEFAULT_TOKEN_TABLE)
    parser.add_argument("--target-table", default=DEFAULT_TARGET_TABLE)
    parser.add_argument("--experiments", default=",".join(DEFAULT_EXPERIMENTS))
    parser.add_argument("--models", default=",".join(FINAL_MODELS))
    parser.add_argument("--settings", default=",".join(PROMPT_SETTINGS))
    parser.add_argument("--case-ids", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument(
        "--fetch-batch-size",
        type=int,
        default=1000,
        help="Number of curated rows to fetch per DB page, with progress output.",
    )
    parser.add_argument(
        "--feedback-payload-batch-size",
        type=int,
        default=50,
        help="Number of RQ2 feedback rows to fetch per JSON_EXTRACT payload query.",
    )
    parser.add_argument("--progress-every", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--token-counter-mode",
        choices=("official_first", "db_first"),
        default="official_first",
        help=(
            "official_first reconstructs payloads and uses model-specific official "
            "counting APIs/tokenizers first; db_first uses provider-reported prompt_tokens first."
        ),
    )
    parser.add_argument(
        "--disable-api-token-counters",
        action="store_true",
        help="Do not call official remote token-count/tokenize endpoints; use local official tokenizers and fallbacks only.",
    )
    parser.add_argument(
        "--no-db-prompt-fallback",
        action="store_true",
        help="Do not use provider-reported prompt_tokens from the existing token table when official counting is unavailable.",
    )
    parser.add_argument(
        "--allow-approximate-fallback",
        action="store_true",
        help="As a last resort, allow approximate o200k_base text counting and available public image estimates.",
    )
    parser.add_argument("--token-counter-timeout", type=float, default=30.0)
    parser.add_argument("--token-counter-retries", type=int, default=2)
    parser.add_argument(
        "--context-source",
        choices=("local", "db"),
        default="local",
        help="Load prompt text from local cache JSON by default; db is kept for spot checks.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    experiments = parse_csv(args.experiments)
    models = parse_csv(args.models)
    settings = parse_csv(args.settings)
    case_ids = parse_csv(args.case_ids)
    start = time.time()

    log(
        f"[RECOMPUTE_START] source={args.source_table} token={args.token_table} "
        f"target={args.target_table} experiments={','.join(experiments)} dry_run={args.dry_run}"
    )

    conn = get_conn(autocommit=False)
    try:
        if not args.dry_run:
            ensure_target_table(conn, target_table=args.target_table)

        if args.context_source == "db":
            valid_contexts, invalid_contexts = load_contexts_from_db(conn)
        else:
            valid_contexts, invalid_contexts = load_contexts_from_local()
        log(
            f"[CONTEXTS] source={args.context_source} "
            f"valid={len(valid_contexts)} invalid={len(invalid_contexts)}"
        )

        rows = fetch_rows(
            conn,
            source_table=args.source_table,
            experiments=experiments,
            models=models,
            settings=settings,
            case_ids=case_ids,
            limit=args.limit,
            fetch_batch_size=args.fetch_batch_size,
        )
        log(f"[ROWS] fetched={len(rows)}")
        feedback_payloads_attached = attach_feedback_payloads(
            conn,
            source_table=args.source_table,
            generation_table=args.generation_table,
            rows=rows,
            chunk_size=args.feedback_payload_batch_size,
        )
        log(f"[FEEDBACK_PAYLOAD] attached={feedback_payloads_attached}")

        row_session_ids: List[str] = []
        for row in rows:
            row_session_ids.extend(parse_session_ids(row.get("session_ids_json")))
        unique_session_count = len(unique_keep_order(row_session_ids))
        log(f"[SESSIONS] referenced={unique_session_count}")

        token_usage, duplicate_rows, conflict_rows = fetch_token_usage(
            conn,
            token_table=args.token_table,
            session_ids=row_session_ids,
        )
        log(
            f"[TOKEN_USAGE] distinct_sessions={len(token_usage)} "
            f"duplicate_rows={duplicate_rows} conflict_rows={conflict_rows}"
        )

        encoding = tiktoken.get_encoding("o200k_base")
        token_counter = OfficialTokenCounter(
            encoding=encoding,
            mode=args.token_counter_mode,
            allow_api_calls=not args.disable_api_token_counters,
            use_db_prompt_fallback=not args.no_db_prompt_fallback,
            allow_approximate_fallback=args.allow_approximate_fallback,
            timeout=args.token_counter_timeout,
            retries=args.token_counter_retries,
        )
        output_rows = build_output_rows(
            rows,
            valid_contexts=valid_contexts,
            invalid_contexts=invalid_contexts,
            token_usage=token_usage,
            token_counter=token_counter,
            progress_every=args.progress_every,
        )

        write_summary_csv(Path(args.output_dir), output_rows)

        if not args.dry_run:
            for offset in range(0, len(output_rows), args.batch_size):
                batch = output_rows[offset: offset + args.batch_size]
                upsert_rows(conn, target_table=args.target_table, rows=batch)
                log(
                    f"[WRITE] rows={offset + len(batch)}/{len(output_rows)} "
                    f"elapsed={time.time() - start:.1f}s"
                )
        else:
            log("[DRY_RUN] target table was not written")

        status_counts: Dict[str, int] = {}
        for row in output_rows:
            status_counts[str(row["status"])] = status_counts.get(str(row["status"]), 0) + 1
        log(f"[SUMMARY] status_counts={json.dumps(status_counts, ensure_ascii=False)}")
        log(f"[DONE] elapsed={time.time() - start:.1f}s output_dir={args.output_dir}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
