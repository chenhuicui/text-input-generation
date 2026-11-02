#!/usr/bin/env python3
"""Mine version-matched APK resources for D diagnostics and E constraints.

This is a resource-only audit.  It records facts that can be tied to the exact
APK/XAPK bytes in the frozen case registry, but it does not claim that every
resource is reachable on the benchmark page.  Page binding is therefore kept
as a separate evidence field.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


ANDROID_NS = "http://schemas.android.com/apk/res/android"
CONSTRAINT_RE = re.compile(
    r"(?:\binvalid\b|\bnot (?:a )?valid\b|\brequired\b|\bcompulsory\b|"
    r"\bmust\b|\bat least\b|\bat most\b|\bminimum\b|\bmaximum\b|"
    r"\bmin(?:imum)?\.?\s*\d+\b|\bmax(?:imum)?\.?\s*\d+\b|"
    r"\bbetween\s+\d|\bno more than\b|\bup to\s+\d|\bonly\s+\d|"
    r"\b\d+\s*(?:characters?|digits?|words?|seconds?|minutes?)\b|"
    r"\btoo (?:short|long|many|few)\b|\bdoes? not match\b|"
    r"\bnot allowed\b|\bcannot be\b|\bcan't be\b|\bplease enter\b|"
    r"\bno (?:results?|languages?) found\b|\bnot found\b|\bsingle[- ]line\b)",
    re.IGNORECASE,
)
NAME_RE = re.compile(
    r"(?:error|invalid|required|validation|wrong|minimum|maximum|min_|max_|"
    r"password|email|phone|postal|zip|coupon|promo|group_code|input_.*(?:hint|error))",
    re.IGNORECASE,
)
INTERESTING_LAYOUT_ATTRS = {
    "digits",
    "ems",
    "hint",
    "imeOptions",
    "inputType",
    "max",
    "maxEms",
    "maxLength",
    "maxLines",
    "min",
    "minEms",
    "minLength",
    "minLines",
    "numeric",
    "password",
    "singleLine",
}
CONSTRAINT_ATTR_RE = re.compile(
    r"(?:digits|inputType|singleLine|(?:^|_)(?:min|max)|min[A-Z]|max[A-Z]|"
    r"valid|regex|pattern|constraint|length|lines)",
    re.IGNORECASE,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plain_text(element: ET.Element) -> str:
    return " ".join("".join(element.itertext()).split())


def resource_name(reference: str) -> str | None:
    match = re.fullmatch(r"@(?:\+)?(?:[^:]+:)?(?:id|string)/(.+)", reference)
    return match.group(1) if match else None


def default_strings(resource_root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    candidates = [resource_root / "res/values/strings.xml"]
    candidates.extend(sorted((resource_root / "res").glob("values-en*/strings.xml")))
    for path in candidates:
        if not path.is_file():
            continue
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            continue
        for item in root.findall("string"):
            name = item.attrib.get("name")
            if name and name not in result:
                result[name] = plain_text(item)
    return result


def resolve(value: str, strings: dict[str, str]) -> str:
    name = resource_name(value)
    if name and "/" in value and "string" in value:
        return strings.get(name, value)
    return value


def target_tokens(field: str) -> set[str]:
    local = field.rsplit("/", 1)[-1]
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", local).casefold()
    return {
        token
        for token in re.split(r"[^a-z0-9]+", expanded)
        if len(token) >= 3 and token not in {"edit", "text", "input", "field", "view"}
    }


def layout_bindings(resource_root: Path, expected_fields: list[str], strings: dict[str, str]) -> list[dict[str, Any]]:
    expected_names = {field: field.rsplit("/", 1)[-1] for field in expected_fields}
    records: list[dict[str, Any]] = []
    for path in sorted((resource_root / "res").glob("layout*/*.xml")):
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            continue
        for element in root.iter():
            raw_id = element.attrib.get(f"{{{ANDROID_NS}}}id", "")
            element_id = resource_name(raw_id)
            matched = [field for field, name in expected_names.items() if element_id == name]
            if not matched:
                continue
            attributes: dict[str, str] = {}
            for raw_key, raw_value in element.attrib.items():
                key = raw_key.split("}", 1)[-1]
                if key in INTERESTING_LAYOUT_ATTRS or CONSTRAINT_ATTR_RE.search(key):
                    attributes[key] = resolve(raw_value, strings)
            records.append(
                {
                    "expected_fields": matched,
                    "layout": str(path.relative_to(resource_root)),
                    "element": element.tag.split("}", 1)[-1],
                    "resource_id": raw_id,
                    "attributes": attributes,
                    "has_explicit_constraint_attribute": any(
                        key not in {"hint", "imeOptions", "ems"} and CONSTRAINT_ATTR_RE.search(key)
                        for key in attributes
                    ),
                }
            )
    return records


def candidate_strings(strings: dict[str, str], expected_fields: list[str]) -> list[dict[str, Any]]:
    tokens = set().union(*(target_tokens(field) for field in expected_fields))
    records: list[dict[str, Any]] = []
    for name, value in strings.items():
        if not value or len(value) > 500:
            continue
        reasons: list[str] = []
        if CONSTRAINT_RE.search(value):
            reasons.append("constraint_language")
        if NAME_RE.search(name):
            reasons.append("validation_resource_name")
        lowered = f"{name} {value}".casefold()
        matched_tokens = sorted(token for token in tokens if token in lowered)
        if matched_tokens:
            reasons.append("target_token_overlap")
        if not reasons or (reasons == ["target_token_overlap"] and not CONSTRAINT_RE.search(value)):
            continue
        records.append(
            {
                "resource_name": name,
                "text": value,
                "reasons": reasons,
                "target_tokens": matched_tokens,
            }
        )
    return sorted(
        records,
        key=lambda item: (
            "constraint_language" not in item["reasons"],
            "target_token_overlap" not in item["reasons"],
            item["resource_name"],
        ),
    )


def mine_case(case: dict[str, Any], jadx: str, timeout: int) -> dict[str, Any]:
    case_id = case["case_id"]
    manifest_path = Path(case["mirror"]["manifest_path"])
    base: dict[str, Any] = {
        "case_id": case_id,
        "task": case["task"],
        "manifest_path": str(manifest_path),
        "registry_source_sha256": case["mirror"].get("source_sha256"),
        "version_code": case["mirror"].get("version_code"),
        "expected_fields": case.get("required_resource_ids") or case["mirror"].get("expected_fields") or [],
    }
    if not manifest_path.is_file():
        return {**base, "status": "manifest_missing"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = Path(manifest["source"])
    base.update({"source": str(source), "source_format": manifest.get("source_format")})
    if not source.is_file():
        return {**base, "status": "source_missing"}
    actual_sha = sha256_file(source)
    base["actual_source_sha256"] = actual_sha
    if base["registry_source_sha256"] and actual_sha != base["registry_source_sha256"]:
        return {**base, "status": "source_hash_mismatch"}
    temp_root = Path(tempfile.mkdtemp(prefix=f"de-apk-{case_id}-"))
    resource_root = temp_root / "resources"
    try:
        completed = subprocess.run(
            [jadx, "--no-src", "--threads-count", "1", "--output-dir-res", str(resource_root), str(source)],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if completed.returncode != 0:
            return {
                **base,
                "status": "jadx_failed",
                "returncode": completed.returncode,
                "stderr_tail": completed.stderr[-2000:],
            }
        strings = default_strings(resource_root)
        layouts = layout_bindings(resource_root, base["expected_fields"], strings)
        candidates = candidate_strings(strings, base["expected_fields"])
        return {
            **base,
            "status": "mined",
            "default_string_count": len(strings),
            "target_layout_bindings": layouts,
            "target_layout_binding_count": len(layouts),
            "explicit_layout_constraint_count": sum(item["has_explicit_constraint_attribute"] for item in layouts),
            "candidate_resource_strings": candidates,
            "candidate_resource_string_count": len(candidates),
            "evidence_scope": (
                "version-matched packaged resources; string candidates require page/reachability binding before E use"
            ),
        }
    except subprocess.TimeoutExpired as exc:
        return {**base, "status": "jadx_timeout", "timeout_seconds": timeout, "stderr_tail": str(exc.stderr or "")[-2000:]}
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--jadx", default=shutil.which("jadx") or "jadx")
    args = parser.parse_args()

    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    cases = registry["cases"]
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_map = {
            executor.submit(mine_case, case, args.jadx, args.timeout): case["case_id"]
            for case in cases
        }
        for future in concurrent.futures.as_completed(future_map):
            result = future.result()
            results.append(result)
            print(json.dumps({"case_id": result["case_id"], "status": result["status"]}), flush=True)
    results.sort(key=lambda item: item["case_id"])
    counts = Counter(item["status"] for item in results)
    summary = {
        "schema_version": "de-apk-resource-audit-1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_count": len(results),
        "status_counts": dict(sorted(counts.items())),
        "cases_with_target_layout_binding": sum(item.get("target_layout_binding_count", 0) > 0 for item in results),
        "cases_with_explicit_layout_constraint": sum(item.get("explicit_layout_constraint_count", 0) > 0 for item in results),
        "cases_with_candidate_resource_strings": sum(item.get("candidate_resource_string_count", 0) > 0 for item in results),
        "scope_warning": "Packaged resources are source evidence, not proof of runtime reachability or benchmark-page binding.",
    }
    document = {"summary": summary, "cases": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if counts == {"mined": len(results)} else 2


if __name__ == "__main__":
    raise SystemExit(main())
