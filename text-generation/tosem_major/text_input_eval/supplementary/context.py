"""Load the same cached prompts and reuse the original one-shot templates."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from xml.etree import ElementTree as ET

from text_input_eval.services.one_shot import (
    PromptContext, PromptGenerator, parse_component_resource_ids_from_restrictive,
)

ROOT = Path(__file__).resolve().parents[3]


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value) -> str:
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


@dataclass
class Case:
    context: PromptContext
    aut: str
    source_hash: str
    binding_issues: list[str]

    @property
    def case_id(self):
        return self.context.case_id

    @property
    def task(self):
        return self.context.task


def binding_issues(ctx: PromptContext) -> list[str]:
    nodes = list(ET.fromstring(ctx.xml_text).iter("node"))
    issues = []
    for rid in ctx.resource_ids:
        parts = rid.split("::#::", 1)  # Existing StrUtils / executor convention.
        count = sum(n.get("resource-id") == parts[0] and
                    (len(parts) == 1 or n.get("index") == parts[1]) for n in nodes)
        if len(parts) == 2:
            matches = [n for n in nodes if n.get("resource-id") == parts[0]]
            # The extractor appends XML index, while the executor uses match
            # occurrence. Only accept when those two conventions agree.
            index = int(parts[1]) if parts[1].isdigit() else -1
            if index < 0 or index >= len(matches) or matches[index].get("index") != parts[1]:
                count = 0
        if count != 1:
            issues.append(f"binding_count:{rid}:{count}")
    # No guessed interpretation of bare historical numeric suffixes.
    return issues


def load_cases(cache_root: Path) -> list[Case]:
    cases = []
    for task, stem, directory in (("valid_pptr", "pptr_prompts", "pptr"),
                                  ("invalid_bug", "bug_prompts", "bug")):
        rows = json.loads((cache_root / f"{stem}.json").read_text())
        for index, row in enumerate(rows, 1):
            target = str(row["app_id"]) if task == "valid_pptr" else str(int(float(row["page_id"])))
            case_id = f"P{index:03d}" if task == "valid_pptr" else f"B{int(target):03d}"
            xml = (cache_root / directory / f"hierarchy_{target}.xml").read_text()
            ctx = PromptContext(task, target, case_id, *(str(row[k]) for k in
                ("global", "component", "adjacent", "restrictive", "guiding")),
                xml, "", parse_component_resource_ids_from_restrictive(row["restrictive"]))
            # For bugs the cache has no app_id. Prefer packages of actual input nodes.
            tree = ET.fromstring(xml)
            packages = Counter(n.get("package") for n in tree.iter("node")
                               if n.get("resource-id") in ctx.resource_ids and n.get("package"))
            if not packages:
                remaining = {n.get("package") for n in tree.iter("node") if n.get("package")
                             and n.get("package") not in {"com.android.systemui", "android", "com.google.android.inputmethod.latin"}}
                if len(remaining) == 1:
                    packages.update(remaining)
            aut = target if task == "valid_pptr" else (packages.most_common(1)[0][0] if packages else "unknown")
            cases.append(Case(ctx, aut, digest(asdict(ctx)), binding_issues(ctx)))
    if len({c.case_id for c in cases}) != len(cases):
        raise ValueError("Duplicate case IDs in cache")
    return cases


def replace_ids(text: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return text
    # One pass, longest first, with resource-ID token boundaries: no cascading
    # replacements, and id/a must never rename the prefix of id/abc.
    token = r"[\w./:#-]"
    pattern = r"(?<!" + token + r")(" + "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)) + r")(?!" + token + r")"
    return re.sub(pattern, lambda m: mapping[m.group(0)], text)


def prepare(case: Case, representation: str, condition: str, noise_nodes: int = 20) -> dict:
    ctx = case.context
    if representation not in ("extracted", "xml"):
        raise ValueError(f"Unsupported representation {representation}")
    if condition not in ("original", "id_rename", "irrelevant_nodes"):
        raise ValueError(f"Unsupported condition {condition}")
    # Normalize XML identically across all E1 conditions, including original.
    tree = ET.fromstring(ctx.xml_text)
    ctx = replace(ctx, xml_text=ET.tostring(tree, encoding="unicode"))
    variant = "full" if representation == "extracted" else "xml"
    baseline = PromptGenerator.build_prompt(ctx, representation, variant)[0]
    mapping = {}
    reason = None
    if condition == "id_rename":
        if case.binding_issues:
            reason = ";".join(case.binding_issues)
        else:
            ids = sorted({n.get("resource-id") for n in tree.iter("node") if n.get("resource-id")})
            prefix = "supplementary:id/"
            while any(r.startswith(prefix) for r in ids):
                prefix = "x" + prefix
            mapping = {rid: f"{prefix}node_{i:04d}" for i, rid in enumerate(ids)}
            for rid in ctx.resource_ids:
                if "::#::" in rid:
                    raw, index = rid.split("::#::", 1)
                    mapping[rid] = mapping[raw] + "::#::" + index
            fields = {key: replace_ids(getattr(ctx, key), mapping) for key in
                      ("global_text", "component_text", "adjacent_text", "restrictive_text", "guiding_text")}
            for node in tree.iter("node"):
                rid = node.get("resource-id")
                if rid in mapping:
                    node.set("resource-id", mapping[rid])
            ctx = replace(ctx, **fields, xml_text=ET.tostring(tree, encoding="unicode"),
                          resource_ids=[mapping[rid] for rid in ctx.resource_ids])
            if parse_component_resource_ids_from_restrictive(ctx.restrictive_text) != ctx.resource_ids:
                raise ValueError(f"Transformed schema differs: {case.case_id}")
    elif condition == "irrelevant_nodes":
        # Structural distractors: non-input, non-TextView, empty text/hint.
        # The original extractor only reads input classes and adjacent TextViews;
        # these nodes therefore cannot change any extracted subprompt. Preserve
        # cached extracted text byte-for-byte and explicitly mark this no-op.
        for i in range(noise_nodes):
            ET.SubElement(tree, "node", {"index": str(i), "class": "android.view.View",
                "resource-id": "", "package": case.aut, "text": "", "hint": "",
                "clickable": "false", "focusable": "false", "bounds": "[0,0][0,0]"})
        ctx = replace(ctx, xml_text=ET.tostring(tree, encoding="unicode"))
    prompt = PromptGenerator.build_prompt(ctx, representation, variant)[0]
    effective = prompt != baseline
    if condition != "original" and not effective and not reason:
        reason = "identical_prompt_after_extraction"
    return {"prompt": prompt, "prompt_hash": digest(prompt), "baseline_hash": digest(baseline),
            "resource_ids": ctx.resource_ids,
            "inverse_map": {v: k for k, v in mapping.items()}, "id_map": mapping,
            "xml": ctx.xml_text, "effective": effective, "exclusion_reason": reason}
