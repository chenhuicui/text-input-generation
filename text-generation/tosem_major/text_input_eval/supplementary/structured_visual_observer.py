"""Observe an App-owned integer rendered only in screenshot pixels."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def observe_image(path: Path, config: dict[str, Any]) -> dict[str, Any]:
    import cv2

    crop = config.get("crop")
    pattern = config.get("pattern")
    minimum = config.get("minimum")
    maximum = config.get("maximum")
    if (
        not isinstance(crop, list)
        or len(crop) != 4
        or any(not isinstance(value, int) for value in crop)
        or not isinstance(pattern, str)
        or not isinstance(minimum, int)
        or not isinstance(maximum, int)
        or minimum > maximum
    ):
        raise ValueError("invalid visual integer observer config")
    image = cv2.imread(str(path))
    if image is None:
        raise RuntimeError("visual observer screenshot is unreadable")
    x1, y1, x2, y2 = crop
    height, width = image.shape[:2]
    if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
        raise ValueError("visual observer crop is outside the screenshot")
    region = image[y1:y2, x1:x2]
    scale = int(config.get("scale", 4))
    if not 1 <= scale <= 8:
        raise ValueError("visual observer scale must be in [1,8]")
    region = cv2.resize(region, None, fx=scale, fy=scale)
    region = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    processed = path.with_name(f"{path.stem}_ocr.png")
    if not cv2.imwrite(str(processed), region):
        raise RuntimeError("visual observer could not write its OCR crop")
    tesseract = shutil.which("tesseract")
    if tesseract is None:
        raise RuntimeError("tesseract is unavailable")
    completed = subprocess.run(
        [tesseract, str(processed), "stdout", "--psm", str(int(config.get("psm", 6)))],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("tesseract failed for the visual observer")
    text = " ".join(completed.stdout.split())
    match = re.search(pattern, text, flags=re.IGNORECASE)
    observed = int(match.group(1)) if match is not None else None
    passed = observed is not None and minimum <= observed <= maximum
    return {
        "passed": passed,
        "observed": observed,
        "ocr_text": text,
        "crop_path": str(processed),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Observe screenshot-only App feedback")
    parser.add_argument("--config-json", required=True)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        config = json.loads(args.config_json)
        request = payload["request"]
        oracle = request["oracle"]
        serial = oracle.get("device_serial") or request["case"]["mirror"]["device"]["serial"]
        evidence_dir = Path(request["evidence_dir"])
        evidence_dir.mkdir(parents=True, exist_ok=True)
        screenshot = evidence_dir / "visual_observer.png"
        import uiautomator2 as u2

        u2.connect(serial).screenshot(str(screenshot))
        observed = observe_image(screenshot, config)
        result = {
            "verdict": "pass" if observed["passed"] else "fail",
            "observation_complete": True,
            "diagnostics": [{
                "type": "observed_ui_message",
                "detail": (
                    f"visual integer={observed['observed']}; "
                    f"ocr={observed['ocr_text']}"
                ),
                "public": True,
                "source": "screenshot_ocr",
                "checker_id": "visual_integer_range",
            }],
        }
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        sys.stderr.write(json.dumps({
            "error_type": type(exc).__name__, "message": str(exc),
        }, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
