#!/usr/bin/env python3
"""Fetch compact current Google Play provenance for all Page packages.

Play listings are external public sources, but they are not assumed to match
the frozen APK version.  The audit keeps that limitation explicit.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import html as html_module
import http.client
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CONSTRAINT_RE = re.compile(
    r"(?:at least|at most|minimum|maximum|required|must|no more than|"
    r"\b\d+\s*(?:characters?|digits?|words?)\b|valid (?:email|phone|zip|code)|single[- ]line)",
    re.IGNORECASE,
)
META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
ATTR_RE = re.compile(r"([\w:-]+)\s*=\s*(['\"])(.*?)\2", re.DOTALL)
TITLE_RE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
ANCHOR_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


def attributes(fragment: str) -> dict[str, str]:
    return {name.casefold(): html_module.unescape(value) for name, _, value in ATTR_RE.findall(fragment)}


def fetch(case: dict[str, Any], timeout: int) -> dict[str, Any]:
    case_id = case["case_id"]
    package = case["source_target"]
    url = f"https://play.google.com/store/apps/details?id={package}&hl=en&gl=US"
    base = {"case_id": case_id, "package": package, "url": url}
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; DE-spec-audit/1.0)",
                    "Accept-Encoding": "gzip",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                payload = response.read()
                html = gzip.decompress(payload) if response.headers.get("Content-Encoding") == "gzip" else payload
                status = response.status
            break
        except HTTPError as exc:
            return {**base, "status": "http_error", "http_status": exc.code, "error": str(exc)}
        except (URLError, TimeoutError, OSError, http.client.IncompleteRead) as exc:
            last_error = exc
    else:
        return {**base, "status": "network_error", "attempts": 3, "error": str(last_error)}
    document = html.decode("utf-8", errors="replace")
    meta: dict[str, str] = {}
    for tag in META_TAG_RE.findall(document):
        attrs = attributes(tag)
        key = attrs.get("property") or attrs.get("name")
        if key and attrs.get("content"):
            meta[key.casefold()] = attrs["content"]
    title_match = TITLE_RE.search(document)
    title = meta.get("og:title") or (
        html_module.unescape(TAG_RE.sub("", title_match.group(1))).strip() if title_match else None
    )
    description = meta.get("og:description") or meta.get("description")
    developer_websites: list[str] = []
    support_emails: list[str] = []
    for anchor in ANCHOR_RE.finditer(document):
        attrs = attributes(anchor.group("attrs"))
        href = attrs.get("href")
        if not href:
            continue
        label = " ".join(
            html_module.unescape(TAG_RE.sub(" ", anchor.group("body"))).split()
        ).casefold()
        if "website" in label and href.startswith(("http://", "https://")):
            developer_websites.append(href)
        if "support email" in label and href.startswith("mailto:"):
            support_emails.append(href.removeprefix("mailto:"))
    description_constraints = sorted(set(CONSTRAINT_RE.findall(description or "")))
    unavailable = not title or title.casefold().startswith("not found")
    return {
        **base,
        "status": "listing_unavailable" if unavailable else "retrieved",
        "http_status": status,
        "title": title,
        "description": description,
        "developer_websites": sorted(set(developer_websites)),
        "support_emails": sorted(set(support_emails)),
        "description_constraint_matches": description_constraints,
        "e_status": (
            "current_listing_constraint_candidate_requires_screen_and_version_alignment"
            if description_constraints
            else "no_field_constraint_in_current_listing_description"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    page_cases = [case for case in registry["cases"] if case["task"] == "valid_pptr"]
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_map = {executor.submit(fetch, case, args.timeout): case["case_id"] for case in page_cases}
        for future in concurrent.futures.as_completed(future_map):
            item = future.result()
            results.append(item)
            print(json.dumps({"case_id": item["case_id"], "status": item["status"]}), flush=True)
    results.sort(key=lambda item: item["case_id"])
    summary = {
        "schema_version": "page-play-source-audit-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_count": len(results),
        "status_counts": dict(sorted(Counter(item["status"] for item in results).items())),
        "cases_with_developer_website": sum(bool(item.get("developer_websites")) for item in results),
        "cases_with_listing_constraint_candidate": sum(bool(item.get("description_constraint_matches")) for item in results),
        "ordinary_e_ready": 0,
        "scope_warning": "Current Play listings are not version-matched field specifications for frozen APK screens.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"summary": summary, "cases": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
