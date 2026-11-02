from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from text_input_eval.supplementary.abcdef_contract import sha256_json
from text_input_eval.supplementary.abcdef_evidence import build_feedback_artifacts


def write_frame(
    directory: Path,
    name: str,
    text: str,
    seconds: float,
    *,
    size: tuple[int, int] = (8, 12),
) -> dict:
    xml = directory / f"{name}.xml"
    png = directory / f"{name}.png"
    xml.write_text(
        '<hierarchy><node package="example.app" class="android.widget.TextView" '
        f'resource-id="message" text="{text}" /></hierarchy>',
        encoding="utf-8",
    )
    Image.new("RGB", size, "white").save(png)
    return {
        "label": name,
        "scheduled_seconds": seconds,
        "actual_seconds": seconds,
        "state_hash": name,
        "xml_path": str(xml),
        "png_path": str(png),
        "foreground": {"package": "example.app", "activity": ".Main"},
        "evidence_ref": f"request:r1:{png.name}",
    }


class ABCDEFEvidenceTests(unittest.TestCase):
    def test_artifacts_capture_timeline_and_public_ui_delta(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / "r1"
            request.mkdir()
            frames = [
                write_frame(request, "pre_action", "Ready", -0.1),
                write_frame(request, "frame_000", "Invalid value", 0.0),
                write_frame(request, "frame_001", "Invalid value", 1.0),
            ]
            candidate = {"field": "bad"}
            result = build_feedback_artifacts(
                evidence_root=root,
                request_dir=request,
                request_id="r1",
                candidate=candidate,
                oracle_version="oracle-v1",
                binding={"field": {"resource-id": "field"}},
                frames=frames,
            )
            self.assertEqual(result["candidate_sha256"], sha256_json(candidate))
            self.assertEqual(
                {row["role"] for row in result["frames"]},
                {"before", "first_changed", "transient_message_peak", "settled"},
            )
            observations = json.loads((root / result["observation_path"]).read_text())
            self.assertEqual(observations["observations"]["public_error_messages"], ["Invalid value"])
            self.assertTrue((root / result["composite_path"]).read_bytes().startswith(b"\x89PNG"))
            self.assertEqual([row["label"] for row in result["capture_timeline"]], [
                "pre_action", "frame_000", "frame_001",
            ])

    def test_composite_is_bounded_to_common_subject_width(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / "r1"
            request.mkdir()
            frames = [
                write_frame(request, "pre_action", "Ready", -0.1, size=(720, 1600)),
                write_frame(request, "frame_000", "Invalid value", 0.0, size=(720, 1600)),
            ]
            result = build_feedback_artifacts(
                evidence_root=root,
                request_dir=request,
                request_id="r1",
                candidate={"field": "bad"},
                oracle_version="oracle-v1",
                binding={"field": {"resource-id": "field"}},
                frames=frames,
            )
            with Image.open(root / result["composite_path"]) as composite:
                self.assertEqual(composite.width, 512)
            self.assertEqual(result["composite_max_width"], 512)

    def test_request_directory_must_stay_under_trajectory_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            outside = Path(directory) / "outside"
            root.mkdir()
            outside.mkdir()
            with self.assertRaisesRegex(ValueError, "escapes"):
                build_feedback_artifacts(
                    evidence_root=root,
                    request_dir=outside,
                    request_id="r1",
                    candidate={"field": "bad"},
                    oracle_version="oracle-v1",
                    binding={},
                    frames=[],
                )


if __name__ == "__main__":
    unittest.main()
