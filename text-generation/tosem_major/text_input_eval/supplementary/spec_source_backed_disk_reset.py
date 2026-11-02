"""Scoped cold restore of the original QCOW2 snapshot; never restore GPU RAM."""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path

SDK = Path('/Users/cuichenhui/Library/Android/sdk')
PORTS = (5580, 5582, 5584, 5586, 5588)
IMAGE_NAMES = ('userdata-qemu.img.qcow2', 'cache.img.qcow2', 'encryptionkey.img.qcow2')
_locks = {port: threading.Lock() for port in PORTS}
# Stagger expensive cold boots, without changing the five-device worker map.
_boot_lock = threading.Lock()
_children: dict[int, subprocess.Popen] = {}


def scoped_target(case: dict) -> tuple[int, Path, str]:
    e = case['execution']
    serial = e['device_serial']
    if serial not in {f'emulator-{p}' for p in PORTS}:
        raise RuntimeError('cold restore is limited to the five dedicated devices')
    port = int(serial.split('-')[1])
    if e['avd_name'] != f'cd_spec_repeat10_{port}':
        raise RuntimeError('cold restore AVD identity mismatch')
    root = Path(f'/Volumes/Extreme Pro/emulator/spec_repeat10_{port}').resolve()
    snapshot = Path(e['snapshot_path']).resolve()
    if snapshot.parent != root / 'snapshots' or snapshot.name != f"{case['case_id']}_spec_native_20261014T023049Z_{port}":
        raise RuntimeError('cold restore must use this batch original snapshot tag')
    ini = Path('/Users/cuichenhui/.android/avd') / f"{e['avd_name']}.ini"
    if not ini.is_file() or f'path={root}' not in ini.read_text().splitlines():
        raise RuntimeError('cold restore disk identity mismatch')
    if not (snapshot / 'snapshot.pb').is_file():
        raise RuntimeError('original snapshot metadata missing')
    return port, root, snapshot.name


def has_snapshot_tag(output: str, name: str) -> bool:
    return any(len(parts := line.split()) >= 2 and parts[0].isdigit() and parts[1] == name
               for line in output.splitlines())


def matching_process(port: int, name: str) -> tuple[int, str] | None:
    rows = subprocess.check_output(['ps', '-axo', 'pid=,command='], text=True).splitlines()
    found = []
    for row in rows:
        pid_text, _, command = row.strip().partition(' ')
        args = shlex.split(command)
        if 'qemu-system' not in command or '-avd' not in args or args[args.index('-avd') + 1] != name:
            continue
        if '-port' not in args or args[args.index('-port') + 1] != str(port):
            raise RuntimeError('dedicated emulator process port mismatch')
        if '-datadir' in args and Path(args[args.index('-datadir') + 1]).resolve() != Path(f'/Volumes/Extreme Pro/emulator/spec_repeat10_{port}').resolve():
            raise RuntimeError('dedicated emulator process disk override mismatch')
        found.append((int(pid_text), command))
    if len(found) > 1:
        raise RuntimeError('multiple processes own the dedicated AVD')
    return found[0] if found else None


def cold_restore(case: dict) -> dict:
    from .spec_source_backed_repeat import android_ready, command
    port, root, tag = scoped_target(case)
    log_root = Path(os.environ.get('SPEC_SOURCE_BACKED_RESET_LOG_ROOT', ''))
    batch = Path('/Volumes/Extreme Pro/experiment-materials/llm-android-feedback-experiment-apks/Android_Empirical_Study/source-backed-repeat10/batches/20261014T023049Z')
    if not log_root.is_absolute() or not log_root.resolve().is_relative_to(batch):
        raise RuntimeError('cold restore logs must stay in this external-drive batch')
    serial, name = case['execution']['device_serial'], case['execution']['avd_name']
    images = [root / filename for filename in IMAGE_NAMES]
    if any(not image.is_file() or image.resolve().parent != root for image in images):
        raise RuntimeError('cold restore requires the three exact dedicated QCOW2 images')
    with _locks[port], _boot_lock:
        old = matching_process(port, name)
        if old:
            pid, expected_command = old
            observed = subprocess.check_output(['ps', '-p', str(pid), '-o', 'command='], text=True).strip()
            if observed != expected_command:
                raise RuntimeError('emulator PID identity changed before scoped stop')
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline and matching_process(port, name):
                owned = _children.get(port)
                if owned is not None:
                    owned.poll()
                time.sleep(.2)
            if matching_process(port, name):
                raise RuntimeError('dedicated emulator did not exit; disk not touched')
        # Validate every frozen tag before mutating any working image.
        for image in images:
            listing = command([str(SDK / 'emulator/qemu-img'), 'snapshot', '-l', str(image)], 30)
            if not has_snapshot_tag(listing, tag):
                raise RuntimeError(f'original frozen disk snapshot missing: {image.name}/{tag}')
        for image in images:
            command([str(SDK / 'emulator/qemu-img'), 'snapshot', '-a', tag, str(image)], 60)
        log_root.mkdir(parents=True, exist_ok=True)
        log_path = log_root / f'{serial}_{case["case_id"]}_{time.time_ns()}.log'
        argv = [str(SDK / 'emulator/emulator'), '-avd', name, '-port', str(port),
                '-no-window', '-no-audio', '-no-boot-anim', '-gpu', 'swiftshader',
                '-feature', '-Vulkan', '-no-snapshot-load', '-no-snapshot-save']
        with log_path.open('xb') as output:
            child = subprocess.Popen(argv, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        _children[port] = child
        android_ready(serial, timeout=120)
        # The first boot-completed response can precede a transport bounce.
        # Repeat only OS readiness reads, not inputs or package installation.
        for _ in range(2):
            time.sleep(1)
            android_ready(serial, timeout=30)
        if child.poll() is not None:
            raise RuntimeError(f'cold emulator exited: {child.returncode}')
        actual = command(['adb', '-s', serial, 'emu', 'avd', 'name'], 10).splitlines()[0].strip()
        if actual != name:
            raise RuntimeError('cold emulator AVD identity differs')
        return {'operational_reset_mode': 'original-qcow2-snapshot-cold-boot',
                'native_memory_restored': False, 'frozen_disk_snapshot_tag': tag,
                'restored_working_images': [str(image) for image in images],
                'emulator_pid': child.pid, 'emulator_log': str(log_path),
                'gpu': 'swiftshader', 'vulkan': False, 'original_snapshot_files_replaced': False}
