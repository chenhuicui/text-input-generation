#!/usr/bin/env python3
"""Fetch the complete public GitHub evidence chain for the 37 Bug cases.

The script uses the authenticated ``gh api`` client, records issue/PR bodies,
comments, linked PR metadata and changed-file summaries, and never treats the
presence of an issue as a formal specification by itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GITHUB_RE = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<kind>issues|pull)/(?P<number>\d+)(?:/.*)?$"
)


def run_gh(endpoint: str, *, paginate: bool = False, headers: list[str] | None = None) -> Any:
    command = ["gh", "api"]
    for header in headers or []:
        command.extend(["-H", header])
    if paginate:
        command.append("--paginate")
    command.append(endpoint)
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        return {
            "_fetch_error": completed.stderr.strip() or completed.stdout.strip(),
            "_endpoint": endpoint,
        }
    text = completed.stdout.strip()
    if not text:
        return []
    if not paginate:
        return json.loads(text)
    # gh --paginate can emit one JSON array per page. Decode every document and
    # flatten arrays while preserving objects.
    decoder = json.JSONDecoder()
    offset = 0
    values: list[Any] = []
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset >= len(text):
            break
        value, offset = decoder.raw_decode(text, offset)
        values.extend(value if isinstance(value, list) else [value])
    return values


def compact_user(value: Any) -> str | None:
    if isinstance(value, dict):
        login = value.get("login")
        return login if isinstance(login, str) else None
    return None


def compact_comment(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": value.get("id"),
        "html_url": value.get("html_url"),
        "user": compact_user(value.get("user")),
        "created_at": value.get("created_at"),
        "updated_at": value.get("updated_at"),
        "body": value.get("body"),
        "author_association": value.get("author_association"),
    }


def compact_issue(value: dict[str, Any]) -> dict[str, Any]:
    labels = []
    for label in value.get("labels") or []:
        labels.append(label.get("name") if isinstance(label, dict) else label)
    milestone = value.get("milestone")
    return {
        "id": value.get("id"),
        "number": value.get("number"),
        "html_url": value.get("html_url"),
        "title": value.get("title"),
        "state": value.get("state"),
        "state_reason": value.get("state_reason"),
        "user": compact_user(value.get("user")),
        "created_at": value.get("created_at"),
        "updated_at": value.get("updated_at"),
        "closed_at": value.get("closed_at"),
        "body": value.get("body"),
        "labels": labels,
        "milestone": milestone.get("title") if isinstance(milestone, dict) else None,
        "comments": value.get("comments"),
        "pull_request": value.get("pull_request"),
    }


def compact_pull(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": value.get("id"),
        "number": value.get("number"),
        "html_url": value.get("html_url"),
        "title": value.get("title"),
        "state": value.get("state"),
        "user": compact_user(value.get("user")),
        "created_at": value.get("created_at"),
        "updated_at": value.get("updated_at"),
        "closed_at": value.get("closed_at"),
        "merged_at": value.get("merged_at"),
        "merge_commit_sha": value.get("merge_commit_sha"),
        "base_sha": (value.get("base") or {}).get("sha"),
        "head_sha": (value.get("head") or {}).get("sha"),
        "body": value.get("body"),
        "changed_files": value.get("changed_files"),
        "commits": value.get("commits"),
    }


def compact_file(value: dict[str, Any]) -> dict[str, Any]:
    patch = value.get("patch")
    return {
        "filename": value.get("filename"),
        "status": value.get("status"),
        "sha": value.get("sha"),
        "additions": value.get("additions"),
        "deletions": value.get("deletions"),
        "changes": value.get("changes"),
        "raw_url": value.get("raw_url"),
        "patch": patch,
        "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest()
        if isinstance(patch, str)
        else None,
    }


def compact_timeline(value: dict[str, Any]) -> dict[str, Any]:
    source_issue = ((value.get("source") or {}).get("issue") or {})
    return {
        "event": value.get("event"),
        "created_at": value.get("created_at"),
        "actor": compact_user(value.get("actor")),
        "commit_id": value.get("commit_id"),
        "commit_url": value.get("commit_url"),
        "source_issue_url": source_issue.get("html_url"),
        "source_issue_title": source_issue.get("title"),
        "source_is_pull_request": bool(source_issue.get("pull_request")),
    }


def fetch_pull(owner: str, repo: str, number: str) -> dict[str, Any]:
    pull_endpoint = f"repos/{owner}/{repo}/pulls/{number}"
    pull_raw = run_gh(pull_endpoint)
    files_raw = run_gh(f"{pull_endpoint}/files?per_page=100", paginate=True)
    issue_comments = run_gh(
        f"repos/{owner}/{repo}/issues/{number}/comments?per_page=100", paginate=True
    )
    review_comments = run_gh(f"{pull_endpoint}/comments?per_page=100", paginate=True)
    result: dict[str, Any] = {
        "url": f"https://github.com/{owner}/{repo}/pull/{number}",
        "files": [compact_file(item) for item in files_raw if isinstance(item, dict)],
        "issue_comments": [
            compact_comment(item) for item in issue_comments if isinstance(item, dict)
        ],
        "review_comments": [
            compact_comment(item) for item in review_comments if isinstance(item, dict)
        ],
    }
    if isinstance(pull_raw, dict) and not pull_raw.get("_fetch_error"):
        result["pull"] = compact_pull(pull_raw)
    else:
        result["pull_error"] = pull_raw
    return result


def fetch_url(url: str) -> dict[str, Any]:
    match = GITHUB_RE.match(url)
    if not match:
        return {"url": url, "status": "unsupported_url"}
    owner = match.group("owner")
    repo = match.group("repo")
    kind = match.group("kind")
    number = match.group("number")
    issue_endpoint = f"repos/{owner}/{repo}/issues/{number}"
    issue_raw = run_gh(issue_endpoint)
    if isinstance(issue_raw, dict) and issue_raw.get("_fetch_error"):
        return {"url": url, "status": "fetch_error", "error": issue_raw}
    comments_raw = run_gh(f"{issue_endpoint}/comments?per_page=100", paginate=True)
    timeline_raw = run_gh(
        f"{issue_endpoint}/timeline?per_page=100",
        paginate=True,
        headers=["Accept: application/vnd.github+json"],
    )
    timeline = [compact_timeline(item) for item in timeline_raw if isinstance(item, dict)]
    result: dict[str, Any] = {
        "url": url,
        "status": "retrieved",
        "repository": f"{owner}/{repo}",
        "number": int(number),
        "url_kind": kind,
        "issue": compact_issue(issue_raw),
        "comments": [compact_comment(item) for item in comments_raw if isinstance(item, dict)],
        "timeline": timeline,
    }
    is_pull = kind == "pull" or bool(issue_raw.get("pull_request"))
    if is_pull:
        direct_pull = fetch_pull(owner, repo, number)
        result.update({key: value for key, value in direct_pull.items() if key != "url"})
    linked_pull_urls = sorted(
        {
            item["source_issue_url"]
            for item in timeline
            if item.get("source_is_pull_request")
            and isinstance(item.get("source_issue_url"), str)
            and "/pull/" in item["source_issue_url"]
        }
    )
    result["linked_pull_requests"] = []
    for linked_url in linked_pull_urls:
        linked_match = GITHUB_RE.match(linked_url)
        if not linked_match:
            continue
        result["linked_pull_requests"].append(
            fetch_pull(
                linked_match.group("owner"),
                linked_match.group("repo"),
                linked_match.group("number"),
            )
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    urls = sorted({url for case in inventory for url in case.get("urls", [])})
    sources = [fetch_url(url) for url in urls]
    url_map = {item["url"]: item for item in sources}
    cases = []
    for case in inventory:
        cases.append(
            {
                "case_id": case["id"],
                "app": case.get("app"),
                "category": case.get("category"),
                "description": case.get("description"),
                "urls": case.get("urls", []),
                "sources": [url_map[url] for url in case.get("urls", []) if url in url_map],
            }
        )
    document = {
        "schema_version": "de-spec-audit-github-sources-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "inventory": str(args.inventory),
        "case_count": len(cases),
        "url_count": len(urls),
        "retrieved_count": sum(item.get("status") == "retrieved" for item in sources),
        "sources": sources,
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "cases": len(cases),
                "urls": len(urls),
                "retrieved": document["retrieved_count"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
