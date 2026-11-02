"""Archive and validate the six public sources used by the pilot contracts.

This command performs only authenticated GitHub API reads and local artifact
writes.  It does not call a model, Android, Docker, or an App.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SOURCES = (
    {
        "case_id": "B014",
        "source_id": "issue-1125",
        "source_type": "public_issue",
        "url": "https://github.com/wallabag/android-app/issues/1125",
        "kind": "issue",
        "endpoint": "repos/wallabag/android-app/issues/1125",
    },
    {
        "case_id": "B014",
        "source_id": "pull-1149",
        "source_type": "merged_fix_pr",
        "url": "https://github.com/wallabag/android-app/pull/1149",
        "kind": "pull",
        "endpoint": "repos/wallabag/android-app/pulls/1149",
    },
    {
        "case_id": "B014",
        "source_id": "tag-2.4.2-build-gradle",
        "source_type": "version_tag",
        "url": "https://github.com/wallabag/android-app/blob/2.4.2/app/build.gradle",
        "kind": "content",
        "endpoint": "repos/wallabag/android-app/contents/app/build.gradle?ref=2.4.2",
    },
    {
        "case_id": "B023",
        "source_id": "issue-668",
        "source_type": "public_issue",
        "url": "https://github.com/nextcloud/notes-android/issues/668",
        "kind": "issue",
        "endpoint": "repos/nextcloud/notes-android/issues/668",
    },
    {
        "case_id": "B023",
        "source_id": "commit-b295bc9",
        "source_type": "fix_commit",
        "url": "https://github.com/nextcloud/notes-android/commit/b295bc90503cc71cdacdaffb36ed8cb89b46fe6c",
        "kind": "commit",
        "endpoint": "repos/nextcloud/notes-android/commits/b295bc90503cc71cdacdaffb36ed8cb89b46fe6c",
    },
    {
        "case_id": "B023",
        "source_id": "tag-v2.4.0-build-gradle",
        "source_type": "version_tag",
        "url": "https://github.com/nextcloud/notes-android/blob/v2.4.0/app/build.gradle",
        "kind": "content",
        "endpoint": "repos/nextcloud/notes-android/contents/app/build.gradle?ref=v2.4.0",
    },
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gh_api(endpoint: str) -> Any:
    completed = subprocess.run(
        ["gh", "api", endpoint],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"GitHub API read failed for {endpoint}: {completed.stderr.strip()}")
    return json.loads(completed.stdout)


def _issue_archive(source: dict[str, str]) -> dict[str, Any]:
    issue = gh_api(source["endpoint"])
    comments = gh_api(source["endpoint"] + "/comments?per_page=100")
    return {"issue": issue, "comments": comments}


def _pull_archive(source: dict[str, str]) -> dict[str, Any]:
    pull = gh_api(source["endpoint"])
    files = gh_api(source["endpoint"] + "/files?per_page=100")
    issue_endpoint = source["endpoint"].replace("/pulls/", "/issues/")
    comments = gh_api(issue_endpoint + "/comments?per_page=100")
    return {"pull": pull, "files": files, "comments": comments}


def _commit_archive(source: dict[str, str]) -> dict[str, Any]:
    return {"commit": gh_api(source["endpoint"])}


def _content_archive(source: dict[str, str]) -> dict[str, Any]:
    response = gh_api(source["endpoint"])
    if response.get("encoding") != "base64" or not isinstance(response.get("content"), str):
        raise RuntimeError("GitHub content endpoint did not return base64 content")
    raw = base64.b64decode(response["content"])
    text = raw.decode("utf-8")
    return {
        "content_metadata": {
            key: response.get(key)
            for key in ("name", "path", "sha", "size", "url", "html_url", "download_url", "type")
        },
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "content_text": text,
    }


FETCHERS: dict[str, Callable[[dict[str, str]], dict[str, Any]]] = {
    "issue": _issue_archive,
    "pull": _pull_archive,
    "commit": _commit_archive,
    "content": _content_archive,
}


def _rendered(payload: dict[str, Any]) -> str:
    return canonical_json(payload).lower()


def validate_claims(source_id: str, payload: dict[str, Any]) -> list[str]:
    rendered = _rendered(payload)
    checks: dict[str, list[tuple[str, bool]]] = {
        "issue-1125": [
            ("affected version 2.4.2 code 228", "2.4.2 (228)" in rendered),
            ("quote crash", 'character \\"' in rendered or 'character "' in rendered),
            ("leading minus example", "-foo" in rendered),
            ("malformed MATCH stack", "malformed match expression" in rendered),
            ("expected no crash", "no crash" in rendered),
        ],
        "pull-1149": [
            ("merged", payload.get("payload", {}).get("pull", {}).get("merged_at") is not None),
            ("fix commit", "809738ea5f301e5023beacb4aab7cce0bc9e50d1" in rendered),
            ("malformed MATCH handling", "malformed match" in rendered),
            ("empty list fallback", "new arraylist" in rendered or "empty list" in rendered),
        ],
        "tag-2.4.2-build-gradle": [
            ("versionCode 228", re.search(r"versioncode\s+228\b", rendered) is not None),
            ("versionName 2.4.2", re.search(r'versionname\s+[\\\"]*2\.4\.2', rendered) is not None),
        ],
        "issue-668": [
            ("affected version 2.4.0", "nextcloud notes 2.4.0" in rendered),
            ("at least two digits", "at least two digits" in rendered),
            ("no actual text", "no actual text" in rendered),
            ("preview action", "preview button" in rendered),
            ("minimal 10. example", "10." in rendered),
        ],
        "commit-b295bc9": [
            ("exact commit", "b295bc90503cc71cdacdaffb36ed8cb89b46fe6c" in rendered),
            ("boundary exception caught", "stringindexoutofboundsexception" in rendered),
            ("raw content fallback", "notecontent.settext(note.getcontent())" in rendered),
            ("version 2.4.3", "2.4.3" in rendered),
        ],
        "tag-v2.4.0-build-gradle": [
            ("versionCode 63", re.search(r"versioncode\s+63\b", rendered) is not None),
            ("versionName 2.4.0", re.search(r'versionname\s+[\\\"]*2\.4\.0', rendered) is not None),
        ],
    }
    results = checks[source_id]
    failures = [label for label, passed in results if not passed]
    if failures:
        raise RuntimeError(f"{source_id} no longer supports frozen claims: {failures}")
    return [label for label, _ in results]


def archive_sources(output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError("source archive directory already exists")
    output_dir.mkdir(parents=True)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    manifest_rows = []
    for source in SOURCES:
        payload = {
            "schema_version": "spec-feasibility-public-source-archive-1",
            "retrieved_at_utc": retrieved_at,
            "case_id": source["case_id"],
            "source_id": source["source_id"],
            "source_type": source["source_type"],
            "url": source["url"],
            "api_endpoint": source["endpoint"],
            "payload": FETCHERS[source["kind"]](source),
        }
        supported_claims = validate_claims(source["source_id"], payload)
        payload["validated_supported_claims"] = supported_claims
        path = output_dir / source["case_id"] / f"{source['source_id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        manifest_rows.append({
            "case_id": source["case_id"],
            "source_id": source["source_id"],
            "source_type": source["source_type"],
            "url": source["url"],
            "path": str(path.relative_to(output_dir)),
            "sha256": sha256_file(path),
            "validated_supported_claims": supported_claims,
        })
    manifest = {
        "schema_version": "spec-feasibility-public-source-manifest-1",
        "retrieved_at_utc": retrieved_at,
        "source_count": len(manifest_rows),
        "all_claim_checks_passed": True,
        "sources": manifest_rows,
    }
    manifest_path = output_dir / "source_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    checksum_rows = [
        f"{sha256_file(path)}  {path.relative_to(output_dir)}"
        for path in sorted(output_dir.rglob("*.json"))
        if path.name != "source_manifest.json"
    ]
    checksum_rows.append(f"{sha256_file(manifest_path)}  source_manifest.json")
    (output_dir / "SHA256SUMS").write_text("\n".join(checksum_rows) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive pilot public sources")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    manifest = archive_sources(args.output_dir.resolve())
    print(json.dumps({
        "output_dir": str(args.output_dir.resolve()),
        "source_count": manifest["source_count"],
        "all_claim_checks_passed": manifest["all_claim_checks_passed"],
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
