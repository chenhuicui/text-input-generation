"""Build candidate-bound visual and deterministic UI-change feedback artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .abcdef_contract import sha256_json


TEXT_ATTRIBUTES = ("text", "content-desc", "hint")
CONTACT_SHEET_MAX_WIDTH = 512
MESSAGE_MARKERS = (
    "error", "invalid", "failed", "unable", "cannot", "required", "network",
    "错误", "无效", "失败", "无法", "必填", "网络",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _nodes(path: Path) -> list[dict[str, str]]:
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    return [dict(node.attrib) for node in root.iter("node")]


def _visible_text(nodes: list[dict[str, str]]) -> list[str]:
    output: list[str] = []
    for node in nodes:
        for attribute in TEXT_ATTRIBUTES:
            value = " ".join(str(node.get(attribute, "")).split())
            if value and value not in output:
                output.append(value[:300])
    return output[:80]


def _visual_change_score(before: Path, after: Path) -> float:
    from PIL import Image, ImageChops

    with Image.open(before) as left_image, Image.open(after) as right_image:
        left = left_image.convert("L").resize((128, 256))
        right = right_image.convert("L").resize((128, 256))
        difference = ImageChops.difference(left, right)
        histogram = difference.histogram()
        return sum(level * count for level, count in enumerate(histogram)) / (128 * 256)


def _role_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not frames or frames[0].get("label") != "pre_action":
        raise ValueError("capture timeline must begin with pre_action")
    before = frames[0]
    baseline_xml = _sha256(Path(before["xml_path"]))
    baseline_png = _sha256(Path(before["png_path"]))
    changed = next(
        (
            row for row in frames[1:]
            if _sha256(Path(row["xml_path"])) != baseline_xml
            or _sha256(Path(row["png_path"])) != baseline_png
        ),
        frames[-1],
    )
    transient = max(
        frames[1:] or [before],
        key=lambda row: _visual_change_score(Path(before["png_path"]), Path(row["png_path"])),
    )
    selected = [
        ("before", before),
        ("first_changed", changed),
        ("transient_message_peak", transient),
        ("settled", frames[-1]),
    ]
    return [{"role": role, **row} for role, row in selected]


def _field_states(
    before: list[dict[str, str]],
    after: list[dict[str, str]],
    binding: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for logical_id, selector in binding.items():
        def match(nodes: list[dict[str, str]]) -> dict[str, str] | None:
            found = [node for node in nodes if all(node.get(key) == value for key, value in selector.items())]
            return found[0] if len(found) == 1 else None

        left, right = match(before), match(after)
        if left is None and right is None:
            continue
        before_text = (left or {}).get("text", "")
        after_text = (right or {}).get("text", "")
        rows.append({
            "field": logical_id,
            "present_before": left is not None,
            "present_after": right is not None,
            "empty_before": before_text == "",
            "empty_after": after_text == "",
            "text_changed": before_text != after_text,
            "password_masked": (right or left or {}).get("password") == "true",
        })
    return rows


def _ui_observations(
    selected: list[dict[str, Any]],
    binding: dict[str, dict[str, str]],
) -> dict[str, Any]:
    before_nodes = _nodes(Path(selected[0]["xml_path"]))
    after_nodes = _nodes(Path(selected[-1]["xml_path"]))
    before_text = _visible_text(before_nodes)
    after_text = _visible_text(after_nodes)
    before_set, after_set = set(before_text), set(after_text)
    added = [text for text in after_text if text not in before_set][:30]
    removed = [text for text in before_text if text not in after_set][:30]
    public_errors = [
        text for text in added
        if any(marker in text.lower() for marker in MESSAGE_MARKERS)
    ]
    dialog_text: list[str] = []
    for row in selected[1:]:
        for node in _nodes(Path(row["xml_path"])):
            identity = " ".join((node.get("class", ""), node.get("resource-id", ""))).lower()
            if any(marker in identity for marker in ("toast", "snackbar", "dialog")):
                value = " ".join(node.get("text", "").split())
                if value and value not in dialog_text:
                    dialog_text.append(value[:300])
    before_foreground = selected[0].get("foreground") or {}
    after_foreground = selected[-1].get("foreground") or {}
    navigation = {
        "changed": before_foreground != after_foreground,
        "before": before_foreground,
        "after": after_foreground,
    }
    return {
        "navigation_observed": navigation,
        "screen_before": selected[0]["label"],
        "screen_after": selected[-1]["label"],
        "added_visible_text": added,
        "removed_visible_text": removed,
        "public_error_messages": public_errors,
        "dialog_or_toast": dialog_text[:20],
        "field_state_changes": _field_states(before_nodes, after_nodes, binding),
        "timeline": [
            {
                "role": row["role"],
                "label": row["label"],
                "scheduled_seconds": row.get("scheduled_seconds"),
                "actual_seconds": row.get("actual_seconds"),
                "evidence_ref": row.get("evidence_ref"),
            }
            for row in selected
        ],
    }


def _contact_sheet(selected: list[dict[str, Any]], output: Path) -> None:
    from PIL import Image, ImageDraw

    images = [Image.open(row["png_path"]).convert("RGB") for row in selected]
    try:
        # All evaluated subject models receive the same bounded visual artifact.
        # Raw full-resolution frames remain alongside the composite for audit.
        target_width = min(max(image.width for image in images), CONTACT_SHEET_MAX_WIDTH)
        prepared = []
        for image in images:
            ratio = target_width / image.width
            prepared.append(image.resize((target_width, max(1, int(image.height * ratio)))))
        label_height = 36
        height = sum(image.height + label_height for image in prepared)
        sheet = Image.new("RGB", (target_width, height), "white")
        draw = ImageDraw.Draw(sheet)
        y = 0
        for row, image in zip(selected, prepared):
            label = f"{row['role']} | t={float(row.get('actual_seconds', 0.0)):.3f}s"
            draw.text((10, y + 10), label, fill="black")
            y += label_height
            sheet.paste(image, (0, y))
            y += image.height
        output.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(output, format="PNG", optimize=False)
    finally:
        for image in images:
            image.close()


def build_feedback_artifacts(
    *,
    evidence_root: Path,
    request_dir: Path,
    request_id: str,
    candidate: dict[str, str],
    oracle_version: str,
    binding: dict[str, dict[str, str]],
    frames: list[dict[str, Any]],
) -> dict[str, Any]:
    root = evidence_root.resolve()
    request_dir = request_dir.resolve()
    if not request_dir.is_relative_to(root):
        raise ValueError("request evidence directory escapes the trajectory root")
    selected = _role_frames(frames)
    composite = request_dir / "feedback_frames.png"
    _contact_sheet(selected, composite)
    observations = {
        "schema_version": "e2-abcdef-v1-ui-observations-1",
        "request_id": request_id,
        "candidate_sha256": sha256_json(candidate),
        "oracle_version": oracle_version,
        "hierarchy_sources": sorted({
            row.get("hierarchy_source", "live_uiautomator2") for row in frames
        }),
        "observations": _ui_observations(selected, binding),
    }
    observation_path = request_dir / "ui_observations.json"
    observation_path.write_text(
        json.dumps(observations, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    compact_frames = [
        {
            "role": row["role"],
            "label": row["label"],
            "scheduled_seconds": row.get("scheduled_seconds"),
            "actual_seconds": row.get("actual_seconds"),
            "evidence_ref": row.get("evidence_ref"),
            "hierarchy_source": row.get("hierarchy_source", "live_uiautomator2"),
        }
        for row in selected
    ]
    capture_timeline = [
        {
            "label": row["label"],
            "scheduled_seconds": row.get("scheduled_seconds"),
            "actual_seconds": row.get("actual_seconds"),
            "capture_lag_seconds": (
                None
                if row.get("scheduled_seconds") is None or row.get("actual_seconds") is None
                else round(float(row["actual_seconds"]) - float(row["scheduled_seconds"]), 6)
            ),
            "evidence_ref": row.get("evidence_ref"),
            "timing_source": row.get("timing_source", "live_screenshot"),
            "alignment_uncertainty_seconds": row.get("alignment_uncertainty_seconds", 0.0),
            "hierarchy_source": row.get("hierarchy_source", "live_uiautomator2"),
        }
        for row in frames
    ]
    return {
        "request_id": request_id,
        "candidate_sha256": sha256_json(candidate),
        "oracle_version": oracle_version,
        "composite_path": str(composite.relative_to(root)),
        "composite_sha256": _sha256(composite),
        "observation_path": str(observation_path.relative_to(root)),
        "observation_sha256": _sha256(observation_path),
        "frames": compact_frames,
        "capture_timeline": capture_timeline,
        "composite_max_width": CONTACT_SHEET_MAX_WIDTH,
    }
