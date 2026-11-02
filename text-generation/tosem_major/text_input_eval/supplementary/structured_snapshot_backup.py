"""Create a verified APFS clone backup for the 152-case Android AVD set.

The emulator snapshot directories depend on the surrounding AVD disk images.
For that reason this command clones every AVD directory referenced by the
frozen case registry instead of copying only ``snapshots/<name>``.  The source
AVDs must be stopped.  A backup is published only after all 152 registered
snapshot paths have been resolved and the cloned trees have identical file
inventories and selected-file hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HASH_LIMIT_BYTES = 16 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def running_avds() -> dict[str, str]:
    devices = subprocess.run(
        ["adb", "devices"], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=20, check=True,
    ).stdout.splitlines()
    result: dict[str, str] = {}
    for line in devices[1:]:
        fields = line.split()
        if len(fields) < 2 or fields[1] != "device" or not fields[0].startswith("emulator-"):
            continue
        serial = fields[0]
        query = subprocess.run(
            ["adb", "-s", serial, "emu", "avd", "name"], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, check=False,
        )
        name = next((row.strip() for row in query.stdout.splitlines() if row.strip() != "OK"), "")
        if name:
            result[serial] = name
    return result


def avd_snapshot_roots(source_root: Path) -> list[Path]:
    candidates = [source_root / "avd" / "snapshots"]
    candidates.extend(sorted(source_root.glob("*_avd/snapshots")))
    return [path.resolve() for path in candidates if path.is_dir()]


def resolve_snapshot(source_root: Path, mirror: dict[str, Any]) -> Path:
    declared = mirror.get("snapshot_path")
    if isinstance(declared, str) and declared:
        path = Path(declared).resolve()
        if path.is_dir():
            return path
    name = mirror.get("snapshot")
    if not isinstance(name, str) or not name:
        raise RuntimeError("case mirror has neither a snapshot path nor a snapshot name")
    matches = [root / name for root in avd_snapshot_roots(source_root) if (root / name).is_dir()]
    if len(matches) != 1:
        raise RuntimeError(f"snapshot {name!r} resolved to {len(matches)} directories")
    return matches[0].resolve()


def containing_avd(source_root: Path, snapshot_path: Path) -> Path:
    try:
        relative = snapshot_path.relative_to(source_root)
    except ValueError as exc:
        raise RuntimeError(f"snapshot is outside the source root: {snapshot_path}") from exc
    if len(relative.parts) < 3 or relative.parts[1] != "snapshots":
        raise RuntimeError(f"unexpected AVD snapshot layout: {snapshot_path}")
    avd = source_root / relative.parts[0]
    if not (avd / "config.ini").is_file():
        raise RuntimeError(f"AVD config is missing: {avd}")
    return avd.resolve()


def tree_inventory(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            rows.append({"path": relative, "kind": "symlink", "target": os.readlink(path)})
        elif path.is_file():
            rows.append({"path": relative, "kind": "file", "size": path.stat().st_size})
    return rows


def snapshot_signature(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        row: dict[str, Any] = {
            "path": str(item.relative_to(path)),
            "size": item.stat().st_size,
        }
        if row["size"] <= HASH_LIMIT_BYTES:
            row["sha256"] = sha256_file(item)
        rows.append(row)
    return rows


def clone_directory(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["cp", "-cR", str(source), str(destination)], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=None, check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"APFS clone failed for {source}: {message}")


def build_backup(registry_path: Path, source_root: Path, destination: Path) -> dict[str, Any]:
    if destination.exists():
        raise FileExistsError(f"backup destination already exists: {destination}")
    registry = read_json(registry_path)
    cases = registry.get("cases")
    if not isinstance(cases, list) or len(cases) != 152:
        raise RuntimeError("the frozen registry must contain exactly 152 cases")

    resolved: list[dict[str, Any]] = []
    source_avds: set[Path] = set()
    seen_cases: set[str] = set()
    seen_snapshots: set[Path] = set()
    for case in cases:
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or case_id in seen_cases:
            raise RuntimeError(f"invalid or duplicate case ID: {case_id!r}")
        seen_cases.add(case_id)
        mirror = case.get("mirror")
        if not isinstance(mirror, dict) or mirror.get("status") != "ready":
            raise RuntimeError(f"case mirror is not ready: {case_id}")
        snapshot = resolve_snapshot(source_root, mirror)
        if not (snapshot / "snapshot.pb").is_file():
            raise RuntimeError(f"snapshot metadata is missing: {snapshot}")
        if snapshot in seen_snapshots:
            raise RuntimeError(f"two cases share one snapshot directory: {snapshot}")
        seen_snapshots.add(snapshot)
        avd = containing_avd(source_root, snapshot)
        source_avds.add(avd)
        resolved.append({
            "case_id": case_id,
            "snapshot": mirror.get("snapshot"),
            "source_snapshot_path": str(snapshot),
            "source_avd": str(avd),
            "source_manifest_path": mirror.get("manifest_path"),
        })

    active = running_avds()
    registered_names = {
        str(case.get("mirror", {}).get("device", {}).get("avd"))
        for case in cases
        if case.get("mirror", {}).get("device", {}).get("avd")
    }
    conflicts = {serial: name for serial, name in active.items() if name in registered_names}
    if conflicts:
        raise RuntimeError(f"source AVDs are still running: {conflicts}")

    partial = destination.with_name(f".{destination.name}.partial-{uuid.uuid4().hex[:8]}")
    partial.mkdir(parents=True)
    try:
        avd_rows = []
        for source_avd in sorted(source_avds):
            backup_avd = partial / "avds" / source_avd.name
            source_inventory = tree_inventory(source_avd)
            clone_directory(source_avd, backup_avd)
            backup_inventory = tree_inventory(backup_avd)
            if backup_inventory != source_inventory:
                raise RuntimeError(f"cloned AVD inventory mismatch: {source_avd}")
            avd_rows.append({
                "source": str(source_avd),
                "backup": str(destination / "avds" / source_avd.name),
                "file_count": sum(row["kind"] == "file" for row in source_inventory),
                "logical_bytes": sum(row.get("size", 0) for row in source_inventory),
                "inventory_sha256": hashlib.sha256(
                    json.dumps(source_inventory, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
            })

        manifest_dir = partial / "case_manifests"
        manifest_dir.mkdir()
        case_rows = []
        for row in resolved:
            source_snapshot = Path(row["source_snapshot_path"])
            source_avd = Path(row["source_avd"])
            backup_snapshot = partial / "avds" / source_avd.name / source_snapshot.relative_to(source_avd)
            source_signature = snapshot_signature(source_snapshot)
            if snapshot_signature(backup_snapshot) != source_signature:
                raise RuntimeError(f"snapshot verification mismatch: {row['case_id']}")
            source_manifest_value = row.get("source_manifest_path")
            manifest_sha256 = None
            if isinstance(source_manifest_value, str) and Path(source_manifest_value).is_file():
                source_manifest = Path(source_manifest_value)
                copied_manifest = manifest_dir / f"{row['case_id']}.json"
                shutil.copy2(source_manifest, copied_manifest)
                manifest_sha256 = sha256_file(copied_manifest)
                if manifest_sha256 != sha256_file(source_manifest):
                    raise RuntimeError(f"case manifest copy mismatch: {row['case_id']}")
            case_rows.append({
                **row,
                "backup_snapshot_path": str(
                    destination / "avds" / source_avd.name / source_snapshot.relative_to(source_avd)
                ),
                "source_manifest_sha256": manifest_sha256,
                "snapshot_signature": source_signature,
            })

        shutil.copy2(registry_path, partial / "case_registry.json")
        registry_sha256 = sha256_file(registry_path)
        if sha256_file(partial / "case_registry.json") != registry_sha256:
            raise RuntimeError("case registry copy mismatch")
        manifest = {
            "schema_version": "e2-structure-v1-avd-backup-1",
            "status": "complete",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "copy_method": "APFS clone via cp -cR",
            "source_root": str(source_root),
            "case_registry": str(registry_path),
            "case_registry_sha256": registry_sha256,
            "case_count": len(case_rows),
            "unique_snapshot_count": len(seen_snapshots),
            "source_avd_count": len(avd_rows),
            "source_avds": avd_rows,
            "cases": case_rows,
            "verification": {
                "source_avds_stopped": True,
                "avd_file_inventory_equal": True,
                "snapshot_signatures_equal": True,
                "case_manifest_hashes_equal": True,
            },
        }
        atomic_json(partial / "backup_manifest.json", manifest)
        os.replace(partial, destination)
        return manifest
    except Exception:
        print(f"incomplete backup retained for diagnosis: {partial}", file=sys.stderr)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Clone and verify the frozen 152-case Android AVDs")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_backup(
        args.registry.resolve(), args.source_root.resolve(), args.destination.resolve()
    )
    print(json.dumps({
        "status": manifest["status"],
        "destination": str(args.destination.resolve()),
        "case_count": manifest["case_count"],
        "unique_snapshot_count": manifest["unique_snapshot_count"],
        "source_avd_count": manifest["source_avd_count"],
        "verification": manifest["verification"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
