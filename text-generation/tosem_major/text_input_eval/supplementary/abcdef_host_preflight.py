"""Host-service prerequisites for A-F blocks with external reset services."""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


B019_RESET = Path(
    "/Volumes/Extreme Pro/emulator/CD_115page_37bug/scripts/b019_matrix_reset.py"
)
B019_BASELINE = Path(
    "/Volumes/Extreme Pro/emulator/CD_115page_37bug/google_eval/services/"
    "B019/matrix_baseline.tar.gz"
)
B019_PROXY = Path(
    "/Volumes/Extreme Pro/emulator/CD_115page_37bug/scripts/b019_matrix_proxy.py"
)
B019_VOLUME = "cd_matrix_b019_data"
B019_CONTAINER = "cd_matrix_b019"
B019_IMAGE = "matrixdotorg/synapse:latest"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _docker_value(args: list[str]) -> str:
    completed = subprocess.run(
        ["docker", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value:
        detail = (completed.stderr or completed.stdout).strip().replace("\n", " ")
        raise RuntimeError(
            f"docker {' '.join(args[:2])} preflight failed: {detail[:500] or 'no detail'}"
        )
    return value


def b019_docker_preflight(reset_command: list[str]) -> dict[str, Any]:
    """Verify every immutable host prerequisite used by B019's reset helper."""
    checked_at = datetime.now(timezone.utc).isoformat()
    expected = ["/opt/anaconda3/bin/python3", str(B019_RESET)]
    try:
        if reset_command != expected:
            raise RuntimeError("B019 reset command differs from the qualified helper")
        for label, path in {
            "reset_helper": B019_RESET,
            "matrix_baseline": B019_BASELINE,
            "matrix_proxy": B019_PROXY,
        }.items():
            if not path.is_file():
                raise RuntimeError(f"B019 {label} asset is unavailable")
        server_version = _docker_value(["info", "--format", "{{.ServerVersion}}"])
        volume = _docker_value([
            "volume", "inspect", B019_VOLUME, "--format", "{{.Name}}",
        ])
        container = _docker_value([
            "container", "inspect", B019_CONTAINER,
            "--format", "{{.Name}} {{.State.Status}}",
        ])
        image = _docker_value([
            "image", "inspect", B019_IMAGE, "--format", "{{.Id}}",
        ])
        if volume != B019_VOLUME:
            raise RuntimeError("B019 Docker volume identity differs from the reset contract")
        container_tokens = container.split()
        if (
            len(container_tokens) != 2
            or container_tokens[0].lstrip("/") != B019_CONTAINER
            or container_tokens[1] not in {"created", "running", "exited"}
        ):
            raise RuntimeError("B019 Docker container identity/state is not resettable")
        return {
            "schema_version": "e2-abcdef-host-preflight-1",
            "run_status": "ok",
            "case_id": "B019",
            "preflight_kind": "docker_reset_prerequisites",
            "checked_at_utc": checked_at,
            "docker": {
                "server_version": server_version.strip('"'),
                "volume": volume,
                "container": container_tokens[0].lstrip("/"),
                "container_state": container_tokens[1],
                "image_id": image,
            },
            "assets": {
                "reset_helper": {"path": str(B019_RESET), "sha256": _sha256(B019_RESET)},
                "matrix_baseline": {"path": str(B019_BASELINE), "sha256": _sha256(B019_BASELINE)},
                "matrix_proxy": {"path": str(B019_PROXY), "sha256": _sha256(B019_PROXY)},
            },
        }
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return {
            "schema_version": "e2-abcdef-host-preflight-1",
            "run_status": "infrastructure_error",
            "case_id": "B019",
            "preflight_kind": "docker_reset_prerequisites",
            "checked_at_utc": checked_at,
            "error_type": type(exc).__name__,
            "error_message": str(exc)[:2000],
        }


def main() -> None:
    receipt = b019_docker_preflight([
        "/opt/anaconda3/bin/python3", str(B019_RESET),
    ])
    print(json.dumps(receipt, ensure_ascii=True, sort_keys=True))
    raise SystemExit(0 if receipt.get("run_status") == "ok" else 1)


if __name__ == "__main__":
    main()
