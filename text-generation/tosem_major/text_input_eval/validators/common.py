from __future__ import annotations

import re


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
INTEGER_RE = re.compile(r"^-?\d+$")
DECIMAL_RE = re.compile(r"^-?\d+(\.\d+)?$")
URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


def non_empty(value: str, min_len: int = 1) -> bool:
    return len(value.strip()) >= min_len


def is_email(value: str) -> bool:
    return bool(EMAIL_RE.match(value.strip()))


def is_integer(value: str) -> bool:
    return bool(INTEGER_RE.match(value.strip()))


def is_decimal(value: str) -> bool:
    return bool(DECIMAL_RE.match(value.strip()))


def is_url(value: str) -> bool:
    return bool(URL_RE.match(value.strip()))


def length_between(value: str, low: int, high: int) -> bool:
    n = len(value.strip())
    return low <= n <= high


def contains_any(value: str, needles: list[str]) -> bool:
    s = value.lower()
    return any(x.lower() in s for x in needles)


def regex_search(value: str, pattern: str, flags: int = 0) -> bool:
    return bool(re.search(pattern, value, flags))