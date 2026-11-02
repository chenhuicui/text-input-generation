"""B002/B018 keyboard execution and five-device, immutable repeat runner."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from .spec_feasibility_android import _capture, _crash_signature, _installed_receipt, _pid
from .spec_feasibility_contract import (
    ARMS, candidate_partition, parse_candidate, render_prompt, request_cost_usd,
    required_resource_ids, sha256_json, usage_tokens, validate_config,
)
from .spec_feasibility_pilot import (
    _frozen_baselines, _gateway, _materialize_frozen_inputs, _schedule,
    _sha256_file, _write_new, _write_sha256s, build_summary, local_preflight, write_report,
)
from .structured_android import AndroidDriver, binding_nodes, logical_binding_value

DISK_RESET_SOURCE_SHA256 = _sha256_file(Path(__file__).with_name('spec_source_backed_disk_reset.py'))
EXECUTOR_SOURCE_SHA256 = hashlib.sha256((
    _sha256_file(Path(__file__)) + DISK_RESET_SOURCE_SHA256).encode()).hexdigest()
# The model workers and frozen device/lane assignments stay independent.
# Serialize only the local Android stage to avoid concurrent cold-boot/capture
# transport pressure; never replay an uncertain input or change its sequence.
ANDROID_OPERATION_LOCK = threading.RLock()
CAPTURE_EVIDENCE_ROOT = Path('/Volumes/Extreme Pro/experiment-materials/llm-android-feedback-experiment-apks/'
                             'Android_Empirical_Study/source-backed-repeat10/batches/20261014T023049Z')


def operational_reset_mode() -> str:
    return ('original-qcow2-snapshot-cold-boot' if os.environ.get('SPEC_SOURCE_BACKED_DISK_ONLY_RESET') == '1'
            else 'native-memory-snapshot')


def install_rpc_forward_guard(adb_device, serial: str, events: list) -> None:
    """Recover only SDK endpoint preparation, BEFORE any HTTP/RPC is sent.

    uiautomator2 resolves forward_port(9008) before requests.request(). An
    empty AdbError here is a failed host control connection, not an uncertain
    click/keypress. Never wrap or retry the HTTP/RPC/candidate action itself.
    This guard belongs to this exact device object, not the shared ADB server.
    """
    from adbutils import AdbError
    if serial not in {f'emulator-{p}' for p in (5580, 5582, 5584, 5586, 5588)}:
        raise RuntimeError('RPC endpoint guard requires a dedicated batch device')
    original = adb_device.forward_port

    def listed_forward():
        result = subprocess.run(['adb', '-s', serial, 'forward', '--list'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        if result.returncode:
            raise RuntimeError('RPC endpoint direct mapping verification failed')
        mappings = []
        for row in result.stdout.decode('ascii').splitlines():
            fields = row.split()
            if len(fields) == 3 and fields[0] == serial and fields[2] == 'tcp:9008':
                port = int(fields[1].removeprefix('tcp:'))
                if not fields[1].startswith('tcp:') or not 0 < port <= 65535:
                    raise RuntimeError('invalid RPC endpoint mapping')
                mappings.append(port)
        return mappings

    def guarded_forward_port(remote):
        if remote not in (9008, 'tcp:9008'):
            return original(remote)
        for attempt in range(1, 4):
            try:
                return original(remote)
            except AdbError as exc:
                if str(exc).strip():
                    raise
                events.append({'event': 'sdk_rpc_endpoint_prepare_retry', 'attempt': attempt,
                               'device_serial': serial, 'before_http_rpc_send': True,
                               'input_redelivered': False, 'error_type': 'AdbError',
                               'will_retry_sdk': attempt < 3})
                if attempt < 3:
                    time.sleep(1)
        mappings = listed_forward()
        created = False
        if not mappings:
            # One idempotent control-plane setup; no app command is present.
            result = subprocess.run(['adb', '-s', serial, 'forward', 'tcp:0', 'tcp:9008'],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
            if result.returncode or not result.stdout.strip().isdigit():
                raise RuntimeError('RPC endpoint direct allocation unacknowledged; no RPC sent')
            port = int(result.stdout.strip())
            if not 0 < port <= 65535:
                raise RuntimeError('RPC endpoint direct allocation invalid')
            mappings = listed_forward()
            if port not in mappings:
                raise RuntimeError('RPC endpoint allocation not independently verified')
            created = True
        else:
            port = mappings[0]
        events.append({'event': 'sdk_rpc_endpoint_direct_verified', 'device_serial': serial,
                       'local_port': port, 'remote': 'tcp:9008', 'created_mapping': created,
                       'before_http_rpc_send': True, 'input_redelivered': False,
                       'shared_adb_server_restarted': False, 'other_device_mappings_modified': False})
        return port

    adb_device.forward_port = guarded_forward_port


def install_hierarchy_read_guard(device, events: list) -> None:
    """Bound only dumpWindowHierarchy transport/JSON reads; never replay input.

    JSON decode failures occur while interpreting this read's response, not
    while sending a click/key. They share the SAME three API-call budget as
    explicit connection/timeout failures. Valid semantic RPC errors still
    propagate. Preserve the whole decoded error document, without claiming
    that exception text is the unrecorded original HTTP headers/wire bytes.
    """
    from requests.exceptions import ConnectionError as TransportConnectionError, Timeout as TransportTimeout
    from uiautomator2.exceptions import HTTPError

    original = device.dump_hierarchy
    if getattr(original, '_spec_hierarchy_read_guard', False) is True:
        return

    def guarded_hierarchy(*args, **kwargs):
        for attempt in range(1, 4):
            try:
                return original(*args, **kwargs)
            except (HTTPError, json.JSONDecodeError) as exc:
                if isinstance(exc, HTTPError) and not isinstance(
                        exc.__cause__, (TransportConnectionError, TransportTimeout)):
                    raise
                event = {'event': 'hierarchy_read_retry', 'failed_read_attempt': attempt,
                         'read_only': True, 'input_redelivered': False,
                         'will_retry': attempt < 3, 'error_type': type(exc).__name__,
                         'error_message': str(exc)[:2000]}
                if isinstance(exc, json.JSONDecodeError):
                    document = exc.doc.encode('utf-8', errors='backslashreplace')
                    event.update(invalid_rpc_response_characters=len(exc.doc),
                                 invalid_rpc_response_utf8_bytes=len(document),
                                 invalid_rpc_response_sha256=hashlib.sha256(document).hexdigest(),
                                 invalid_rpc_response_utf8_hex=document.hex(),
                                 json_error_position=exc.pos,
                                 original_http_headers_or_wire_bytes_archived=False)
                else:
                    event['transport_cause_type'] = type(exc.__cause__).__name__
                events.append(event)
                if attempt == 3:
                    raise
                # Same bounded read budget, spaced beyond the directly
                # observed approximately 0.65-second transport bounce.
                time.sleep(1)

    guarded_hierarchy._spec_hierarchy_read_guard = True
    device.dump_hierarchy = guarded_hierarchy


def connect_batch_device(serial: str, events: list):
    import adbutils
    import uiautomator2 as u2
    # The exact serial is already verified by the reset/preflight. Avoid a
    # global device-list discovery, and install the pre-RPC guard before the
    # SDK's own ping/setup, not after an uncertain action.
    adb_device = adbutils.adb.device(serial=serial)
    install_rpc_forward_guard(adb_device, serial, events)
    device = u2.connect(adb_device)
    install_hierarchy_read_guard(device, events)
    install_fixed_png_capture(device, serial, events)
    return device


def command(argv: list[str], timeout: float = 60) -> str:
    result = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-2000:])
    return result.stdout


def _logcat_transport(argv: list[str], directory: Path, stem: str) -> tuple[int, bytes, bytes]:
    """Archive each physical transport, including timeout/partial bytes."""
    timed_out = False
    try:
        response = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        code, out, err = response.returncode, response.stdout, response.stderr
    except subprocess.TimeoutExpired as exc:
        code, out, err, timed_out = -1, exc.stdout or b'', exc.stderr or b'', True
    for label, data in (("stdout", out), ("stderr", err)):
        with (directory / f"{stem}.{label}.bin").open("xb") as stream:
            stream.write(data)
    _write_new(directory / f"{stem}.json", {
        "argv": argv, "exit_code": code, "timed_out": timed_out,
        "stdout_bytes": len(out), "stdout_sha256": hashlib.sha256(out).hexdigest(),
        "stderr_bytes": len(err), "stderr_sha256": hashlib.sha256(err).hexdigest(),
        "input_redelivered": False,
    })
    return code, out, err


def _logcat_snapshot_metadata(serial: str, remote: str, token: str, directory: Path) -> dict:
    # The status file is committed only AFTER logcat closes both output files.
    # Reading these immutable diagnostic files is not another logcat invocation.
    script = (f"test -f {remote}.status && cat {remote}.status && "
              f"wc -c {remote}.raw && sha256sum {remote}.raw && "
              f"wc -c {remote}.stderr && sha256sum {remote}.stderr && "
              f"printf 'SPEC_LOGCAT_END_{token}\\n'")
    for attempt in range(1, 4):
        code, out, _ = _logcat_transport(
            ["adb", "-s", serial, "shell", script], directory, f"logcat.metadata_{attempt:02d}")
        try:
            lines = out.decode('ascii').strip().splitlines()
            if code or len(lines) != 6 or lines[-1] != f'SPEC_LOGCAT_END_{token}':
                raise ValueError('incomplete snapshot metadata')
            status = int(lines[0])
            if not 0 <= status <= 255:
                raise ValueError('invalid remote logcat status')
            result = {'logcat_exit_code': status, 'metadata_read_attempts': attempt}
            for label, index, suffix in (('raw', 1, '.raw'), ('stderr', 3, '.stderr')):
                size, size_path = lines[index].split()
                digest, hash_path = lines[index + 1].split()
                if (size_path != remote + suffix or hash_path != remote + suffix
                        or not size.isdecimal() or len(digest) != 64
                        or any(c not in '0123456789abcdef' for c in digest)):
                    raise ValueError('invalid snapshot file identity')
                result[label] = {'bytes': int(size), 'sha256': digest, 'remote_path': remote + suffix}
            return result
        except (UnicodeError, ValueError):
            if attempt == 3:
                raise RuntimeError('logcat snapshot metadata incomplete after 3 reads; capture NOT repeated')
            time.sleep(1)
    raise AssertionError('unreachable')


def _pull_logcat_snapshot(serial: str, expected: dict, directory: Path, label: str) -> tuple[bytes, list]:
    attempts = []
    for attempt in range(1, 4):
        # Each attempt has its OWN output; never overwrite or accept partial evidence.
        destination = directory / f'logcat.transfer_{label}_{attempt:02d}.bin'
        if attempt == 1:
            method = 'adb-pull'
            code, _, _ = _logcat_transport(
                ['adb', '-s', serial, 'pull', expected['remote_path'], str(destination)],
                directory, f'logcat.transfer_{label}_{attempt:02d}')
        else:
            # Alternate READ transport within the SAME three-transfer budget.
            # This reads the immutable UUID file, never logcat or fresh logs.
            method = 'adb-exec-out-cat'
            code, out, _ = _logcat_transport(
                ['adb', '-s', serial, 'exec-out', 'cat', expected['remote_path']],
                directory, f'logcat.transfer_{label}_{attempt:02d}')
            with destination.open('xb') as stream:
                stream.write(out)
        raw = destination.read_bytes() if destination.is_file() else b''
        digest = hashlib.sha256(raw).hexdigest()
        verified = (code == 0 and destination.is_file() and len(raw) == expected['bytes']
                    and digest == expected['sha256'])
        receipt = {'attempt': attempt, 'transport_method': method,
                   'exit_code': code, 'local_path': destination.name,
                   'file_exists': destination.is_file(), 'bytes': len(raw), 'sha256': digest,
                   'expected_bytes': expected['bytes'], 'expected_sha256': expected['sha256'],
                   'verified': verified, 'read_only': True, 'input_redelivered': False,
                   'same_immutable_snapshot': True, 'logcat_redelivered': False}
        _write_new(directory / f'logcat.transfer_{label}_{attempt:02d}.verification.json', receipt)
        attempts.append(receipt)
        if verified:
            return raw, attempts
        if attempt < 3:
            time.sleep(1)
    raise RuntimeError(f'logcat snapshot {label} transfer incomplete after 3 reads; capture NOT repeated')


def wait_logcat_transport_ready(serial: str, directory: Path) -> None:
    """Observe this serial BEFORE the one logcat launch; never retry a launch."""
    if serial not in {f'emulator-{p}' for p in (5580, 5582, 5584, 5586, 5588)}:
        raise RuntimeError('logcat transport readiness requires a dedicated batch device')
    for attempt in range(1, 4):
        code, out, err = _logcat_transport(
            ['adb', '-s', serial, 'get-state'], directory,
            f'logcat.prelaunch_transport_{attempt:02d}')
        if code == 0 and out.strip() == b'device':
            return
        # Only known offline/absent THIS serial or timed-out READ may be observed again.
        # Semantic errors (including unauthorized/unknown state) fail closed.
        if not (err.strip() in (b'adb: device offline', b'error: device offline',
                               f"error: device '{serial}' not found".encode('ascii')) or code == -1):
            raise RuntimeError('logcat prelaunch transport state unrecognized; no capture launched')
        if attempt == 3:
            raise RuntimeError('logcat prelaunch transport offline after 3 reads; no capture launched')
        time.sleep(.3)


def capture_logcat(serial: str, directory: Path) -> str:
    """Capture once; retry only verified transfers of that fixed snapshot.

    Direct shell streaming sometimes returned exit=255 with only 16 KiB.
    Never re-run logcat after partial output. Instead, the original command
    writes a unique guest diagnostic file once, closes it, then commits its
    exit status. Host bytes must match the guest SHA256 AND size. JNI modified
    UTF-8 stays lossless in the binary artifact; rendering/scoring is unchanged.
    """
    if serial not in {f'emulator-{p}' for p in (5580, 5582, 5584, 5586, 5588)}:
        raise RuntimeError('logcat snapshot requires a dedicated batch device')
    wait_logcat_transport_ready(serial, directory)
    token = uuid.uuid4().hex
    remote = f'/data/local/tmp/spec_logcat_{token}'
    # No untrusted candidate text enters this script, no log is cleared and
    # no background collector survives the synchronous command. Diagnostic
    # files are discarded naturally by the next original frozen disk reset.
    script = (f'umask 077; logcat -d -v epoch > {remote}.raw 2> {remote}.stderr; '
              f'logcat_status=$?; printf "%s\\n" "$logcat_status" > {remote}.status')
    launch_code, _, _ = _logcat_transport(
        ['adb', '-s', serial, 'shell', script], directory, 'logcat.snapshot_start')
    snapshot = {'method': 'single-guest-snapshot-sha256-verified-pull',
                'device_serial': serial, 'remote_base': remote, 'logcat_invocations': 1,
                'snapshot_start_transport_exit_code': launch_code, 'no_model_call': True,
                'input_redelivered': False, 'logcat_repeated_after_partial_output': False}
    try:
        metadata = _logcat_snapshot_metadata(serial, remote, token, directory)
        snapshot['metadata'] = metadata
        raw, transfers = _pull_logcat_snapshot(serial, metadata['raw'], directory, 'raw')
        stderr, stderr_transfers = _pull_logcat_snapshot(serial, metadata['stderr'], directory, 'stderr')
        snapshot.update(raw_transfer_attempts=transfers, stderr_transfer_attempts=stderr_transfers)
        with (directory / 'logcat.stderr.bin').open('xb') as stream:
            stream.write(stderr)
        if metadata['logcat_exit_code']:
            raise RuntimeError(f"logcat snapshot command failed: exit={metadata['logcat_exit_code']}")
    except Exception as exc:
        snapshot.update(verified=False, error_type=type(exc).__name__, error_message=str(exc))
        _write_new(directory / 'logcat_snapshot_failure.json', snapshot)
        raise
    with (directory / "logcat.raw.bin").open("xb") as stream:
        stream.write(raw)
    strict_utf8 = True
    try:
        rendered = raw.decode("utf-8")
    except UnicodeDecodeError:
        strict_utf8 = False
        rendered = raw.decode("utf-8", errors="backslashreplace")
    _write_new(directory / "logcat.txt", rendered)
    _write_new(directory / "logcat_capture_receipt.json", {
        "raw_bytes": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "strict_utf8": strict_utf8, "text_decode_errors": "backslashreplace",
        "raw_bytes_preserved": True,
        "read_attempts": len(transfers), "failed_read_receipts": [r for r in transfers if not r['verified']],
        "snapshot": snapshot, "remote_hash_and_size_verified": True,
    })
    return rendered


def _screenshot_snapshot_metadata(serial: str, remote: str, token: str, directory: Path) -> dict:
    script = (f"test -f {remote}.status && cat {remote}.status && "
              f"wc -c {remote}.png && sha256sum {remote}.png && "
              f"wc -c {remote}.stderr && sha256sum {remote}.stderr && "
              f"printf 'SPEC_SCREENSHOT_END_{token}\\n'")
    for attempt in range(1, 4):
        code, out, _ = _logcat_transport(['adb', '-s', serial, 'shell', script], directory,
                                        f'screenshot.metadata_{attempt:02d}')
        try:
            lines = out.decode('ascii').strip().splitlines()
            if code or len(lines) != 6 or lines[-1] != f'SPEC_SCREENSHOT_END_{token}':
                raise ValueError('incomplete screenshot metadata')
            status = int(lines[0])
            if not 0 <= status <= 255:
                raise ValueError('invalid screenshot exit status')
            result = {'capture_exit_code': status, 'metadata_read_attempts': attempt}
            for label, index, suffix in (('png', 1, '.png'), ('stderr', 3, '.stderr')):
                size, size_path = lines[index].split()
                digest, hash_path = lines[index + 1].split()
                if (size_path != remote + suffix or hash_path != remote + suffix
                        or not size.isdecimal() or len(digest) != 64
                        or any(c not in '0123456789abcdef' for c in digest)):
                    raise ValueError('invalid screenshot snapshot identity')
                result[label] = {'bytes': int(size), 'sha256': digest, 'remote_path': remote + suffix}
            return result
        except (UnicodeError, ValueError):
            if attempt == 3:
                raise RuntimeError('screenshot metadata incomplete after 3 reads; capture NOT repeated')
            time.sleep(.2)
    raise AssertionError('unreachable')


def _screenshot_sync_recv(serial: str, remote: str, destination: Path,
                          directory: Path, stem: str) -> int:
    """One uncompressed, framed RECV of one fixed screenshot file; no retries.

    No CLI exit status or socket EOF implies success: require full DATA frames
    and explicit DONE. The caller still requires the committed guest size/SHA.
    Connect only to the existing local ADB server, never start/restart it.
    Partial payload and wire bytes remain archived even on timeout/EOF/FAIL.
    """
    import socket
    import struct
    if (serial not in {f'emulator-{p}' for p in (5580, 5582, 5584, 5586, 5588)}
            or re.fullmatch(r'/data/local/tmp/spec_screenshot_[0-9a-f]{32}\.(png|stderr)', remote) is None):
        raise RuntimeError('framed screenshot RECV requires exact dedicated serial and UUID file')
    if any(os.environ.get(k) for k in ('ADB_SERVER_SOCKET', 'ANDROID_ADB_SERVER_HOST', 'ANDROID_ADB_SERVER_PORT')):
        raise RuntimeError('framed screenshot RECV requires the existing default local ADB endpoint')
    deadline = time.monotonic() + 60
    sock = None
    record = {'device_serial': serial, 'remote_path': remote, 'adb_endpoint': '127.0.0.1:5037',
              'transport_method': 'adb-sync-v1-recv', 'physical_file_reads': 1,
              'read_only': True, 'input_redelivered': False, 'screencap_repeated': False,
              'adb_server_started_or_restarted': False, 'data_frame_sizes': [], 'done_received': False}
    code = 1
    with destination.open('xb') as payload, (directory / f'{stem}.wire.bin').open('xb') as wire:
        def remaining():
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise TimeoutError('framed screenshot transfer deadline exhausted')
            return seconds

        def exact(size, is_payload=False):
            result = bytearray()
            while len(result) < size:
                sock.settimeout(remaining())
                chunk = sock.recv(size - len(result))
                if not chunk:
                    raise EOFError(f'premature EOF reading {size} bytes; received {len(result)}')
                wire.write(chunk)
                if is_payload:
                    payload.write(chunk)
                result.extend(chunk)
            return bytes(result)

        def service(command):
            raw = command.encode('ascii')
            sock.settimeout(remaining())
            sock.sendall(f'{len(raw):04x}'.encode('ascii') + raw)
            status = exact(4)
            if status == b'FAIL':
                length = int(exact(4), 16)
                if length > 65536:
                    raise RuntimeError('oversized ADB service failure')
                raise RuntimeError('ADB service rejected: ' + exact(length).decode('utf-8', 'backslashreplace'))
            if status != b'OKAY':
                raise RuntimeError(f'ADB service unrecognized status: {status!r}')

        try:
            sock = socket.create_connection(('127.0.0.1', 5037), timeout=remaining())
            service(f'host:transport:{serial}')
            service('sync:')
            path = remote.encode('ascii')
            sock.settimeout(remaining())
            sock.sendall(b'RECV' + struct.pack('<I', len(path)) + path)
            while True:
                tag, length = struct.unpack('<4sI', exact(8))
                if tag == b'DONE':
                    record['done_received'] = True
                    code = 0
                    break
                if tag == b'FAIL':
                    if length > 65536:
                        raise RuntimeError('oversized SYNC failure')
                    raise RuntimeError('SYNC RECV rejected: ' + exact(length).decode('utf-8', 'backslashreplace'))
                if tag != b'DATA' or not 0 < length <= 65536:
                    raise RuntimeError(f'invalid SYNC DATA frame: {tag!r}/{length}')
                exact(length, is_payload=True)
                record['data_frame_sizes'].append(length)
        except Exception as exc:
            record.update(error_type=type(exc).__name__, error_message=str(exc), traceback=traceback.format_exc())
        finally:
            if sock is not None:
                sock.close()
    record.update(exit_code=code, bytes=destination.stat().st_size,
                  sha256=_sha256_file(destination), wire_sha256=_sha256_file(directory / f'{stem}.wire.bin'))
    _write_new(directory / f'{stem}.json', record)
    return code


def _screenshot_snapshot_file(serial: str, expected: dict, directory: Path, label: str) -> tuple[bytes, list]:
    attempts = []
    for attempt in range(1, 4):
        destination = directory / f'screenshot.transfer_{label}_{attempt:02d}.bin'
        if attempt == 1:
            method = 'adb-pull'
            code, _, _ = _logcat_transport(
                ['adb', '-s', serial, 'pull', expected['remote_path'], str(destination)],
                directory, f'screenshot.transfer_{label}_{attempt:02d}')
        else:
            # Alternate framed, uncompressed READ within the SAME total3.
            # No shell cat/recapture, no new status/metadata request here.
            method = 'adb-sync-v1-recv'
            code = _screenshot_sync_recv(serial, expected['remote_path'], destination,
                                         directory, f'screenshot.transfer_{label}_{attempt:02d}')
        raw = destination.read_bytes() if destination.is_file() else b''
        digest = hashlib.sha256(raw).hexdigest()
        verified = (code == 0 and destination.is_file() and len(raw) == expected['bytes']
                    and digest == expected['sha256'])
        receipt = {'attempt': attempt, 'transport_method': method, 'exit_code': code,
                   'file_exists': destination.is_file(), 'bytes': len(raw), 'sha256': digest,
                   'expected_bytes': expected['bytes'], 'expected_sha256': expected['sha256'],
                   'remote_path': expected['remote_path'], 'verified': verified,
                   'read_only': True, 'input_redelivered': False, 'same_immutable_snapshot': True,
                   'screencap_repeated': False}
        _write_new(directory / f'screenshot.transfer_{label}_{attempt:02d}.verification.json', receipt)
        attempts.append(receipt)
        if verified:
            return raw, attempts
        if attempt < 3:
            # A directly observed cold transport bounce lasted beyond the
            # former 0.2/0.4-second retry window. Space the SAME three file
            # reads; never add a fourth read or capture a different frame.
            time.sleep(1)
    raise RuntimeError(f'screenshot {label} transfer incomplete after 3 reads; capture NOT repeated')


def capture_fixed_png(serial: str, directory: Path):
    """One real frame, not repeated captures after partial HTTP/ADB output.

    A guest UUID snapshot and its committed status/size/SHA make transfer
    completeness independently checkable. Failed reads can only reread the
    SAME bytes; missing status, nonzero capture, hash mismatch or invalid PNG
    remain infrastructure errors. No model, app input or logcat is invoked.
    """
    from io import BytesIO
    from PIL import Image
    if serial not in {f'emulator-{p}' for p in (5580, 5582, 5584, 5586, 5588)}:
        raise RuntimeError('fixed screenshot requires a dedicated batch device')
    batch = CAPTURE_EVIDENCE_ROOT
    if not directory.resolve().is_relative_to(batch.resolve()):
        raise RuntimeError('screenshot transport evidence must stay in this external-drive batch')
    directory.mkdir(parents=True, exist_ok=False)
    token = uuid.uuid4().hex
    remote = f'/data/local/tmp/spec_screenshot_{token}'
    script = (f'umask 077; screencap -p > {remote}.png 2> {remote}.stderr; '
              f'capture_status=$?; printf "%s\\n" "$capture_status" > {remote}.status')
    code, _, _ = _logcat_transport(['adb', '-s', serial, 'shell', script], directory,
                                  'screenshot.snapshot_start')
    receipt = {'device_serial': serial, 'method': 'single-guest-png-snapshot-sha256-verified-transfer',
               'screencap_invocations': 1, 'snapshot_start_transport_exit_code': code,
               'remote_base': remote, 'read_only': True, 'input_redelivered': False,
               'screencap_repeated_after_partial_output': False}
    try:
        metadata = _screenshot_snapshot_metadata(serial, remote, token, directory)
        receipt['metadata'] = metadata
        if metadata['capture_exit_code']:
            raise RuntimeError(f"screenshot capture failed: exit={metadata['capture_exit_code']}")
        raw, transfers = _screenshot_snapshot_file(serial, metadata['png'], directory, 'png')
        stderr, stderr_transfers = _screenshot_snapshot_file(serial, metadata['stderr'], directory, 'stderr')
        receipt.update(png_transfer_attempts=transfers, stderr_transfer_attempts=stderr_transfers)
        for name, data in (('screenshot.raw.png', raw), ('screenshot.stderr.bin', stderr)):
            with (directory / name).open('xb') as stream:
                stream.write(data)
        if (not raw.startswith(b'\x89PNG\r\n\x1a\n')
                or not raw.endswith(b'\x00\x00\x00\x00IEND\xaeB\x60\x82')):
            raise RuntimeError('screenshot snapshot is not a complete PNG')
        with Image.open(BytesIO(raw)) as check:
            check.verify()
        picture = Image.open(BytesIO(raw))
        picture.load()
        if picture.format != 'PNG' or min(picture.size) <= 0:
            raise RuntimeError('screenshot snapshot has invalid pixel dimensions')
        if picture.mode == 'RGBA':
            picture = picture.convert('RGB')
        receipt.update(verified=True, raw_png_bytes=len(raw), raw_png_sha256=hashlib.sha256(raw).hexdigest(),
                       image_size=list(picture.size), image_mode=picture.mode,
                       pixels_resized_or_synthesized=False, remote_hash_and_size_verified=True)
        _write_new(directory / 'screenshot_capture_receipt.json', receipt)
        _write_sha256s(directory)
        return picture, receipt
    except Exception as exc:
        receipt.update(verified=False, error_type=type(exc).__name__, error_message=str(exc))
        _write_new(directory / 'screenshot_snapshot_failure.json', receipt)
        # Do not let an outer SDK/HTTP read guard recapture a different frame.
        raise RuntimeError('fixed screenshot snapshot failed; capture NOT repeated') from exc


def install_fixed_png_capture(device, serial: str, events: list) -> None:
    """Instance-scoped default screenshot transport, no global SDK patch.

    The public filename/Pillow contract and true full-size screen remain.
    Default captures no longer use mutable JSON/base64 HTTP responses. Legacy
    harness read counters count this capture API, not hidden extra SDK calls;
    all original exact injection counts and fail-closed gates remain intact.
    Other formats/displays retain the original SDK behavior.
    """
    if serial not in {f'emulator-{p}' for p in (5580, 5582, 5584, 5586, 5588)}:
        raise RuntimeError('fixed screenshot transport requires a dedicated batch device')
    original = device.screenshot
    if getattr(original, '_spec_fixed_png_capture', False) is True:
        return

    def screenshot(filename=None, format='pillow', display_id=None):
        if format != 'pillow' or display_id is not None:
            return original(filename, format=format, display_id=display_id)
        batch = CAPTURE_EVIDENCE_ROOT
        parent = Path(filename).parent if filename else batch / 'capture_transport_logs'
        transport_dir = parent / f'screenshot_transport_{uuid.uuid4().hex}'
        picture, receipt = capture_fixed_png(serial, transport_dir)
        events.append({'event': 'screenshot_fixed_snapshot_verified', 'read_only': True,
                       'input_redelivered': False, 'transport_evidence_directory': str(transport_dir),
                       'snapshot_receipt': receipt, 'http_screenshot_requests': 0})
        if filename:
            picture.save(filename)
            return None
        return picture

    screenshot._spec_fixed_png_capture = True
    device.screenshot = screenshot


def install_input_method_read_guard(device, events: list) -> None:
    """Retry only a malformed, read-only default-IME status response.

    uiautomator2 reads this setting before an IME broadcast. adbutils can
    return the setting value without its shell exit marker. Repeat that
    query, not send_keys or a broadcast: an uncertain write is never replayed.
    This wrapper belongs to this execution's Device instance only.
    """
    from adbutils import AdbError
    from uiautomator2.exceptions import AdbShellError

    original = device.current_ime

    def guarded_current_ime():
        for attempt in range(1, 4):
            try:
                return original()
            except AdbShellError as exc:
                cause = exc.args[0] if len(exc.args) == 1 else None
                details = cause.args if isinstance(cause, AdbError) else ()
                if (len(details) != 3 or details[0] != "shell output invalid"
                        or details[1] != "settings get secure default_input_method; echo X4EXIT:$?"):
                    raise
                events.append({"event": "input_method_state_read_retry",
                               "failed_read_attempt": attempt, "read_only": True,
                               "input_redelivered": False, "will_retry": attempt < 3,
                               "error_type": "AdbShellError",
                               "reason": "default input method query lacked its exit-status marker"})
                if attempt == 3:
                    raise
                time.sleep(.2)

    device.current_ime = guarded_current_ime


def install_capture_read_guard(device, events: list, serial: str | None = None) -> None:
    """Retry read-only screenshot JSON/ADB transport failures, never input.

    The SDK parses takeScreenshot's JSON/base64 response before writing a
    file. A truncated response therefore has no input or file-write effect.
    Keep the original capture API, arguments, pixels and fail-closed outcome.
    An AdbError or explicitly transport-caused HTTPError from this API occurs
    while obtaining pixels, before PIL saves the destination. Other failures,
    including semantic HTTP and image/file errors, propagate.
    """
    from adbutils import AdbError
    from requests.exceptions import ConnectionError as TransportConnectionError, Timeout as TransportTimeout
    from uiautomator2.exceptions import HTTPError

    original = device.screenshot

    def guarded_screenshot(*args, **kwargs):
        for attempt in range(1, 4):
            try:
                return original(*args, **kwargs)
            except (json.JSONDecodeError, AdbError, HTTPError) as exc:
                # Only the screenshot READ is retried. The SDK's HTTPError
                # must retain an explicit connection/timeout cause: semantic
                # HTTP errors and unrelated exceptions still fail closed.
                if isinstance(exc, HTTPError) and not isinstance(
                        exc.__cause__, (TransportConnectionError, TransportTimeout)):
                    raise
                event = {"event": "screenshot_read_retry",
                         "failed_read_attempt": attempt, "read_only": True,
                         "input_redelivered": False, "will_retry": attempt < 3,
                         "error_type": type(exc).__name__}
                if isinstance(exc, json.JSONDecodeError):
                    event.update({"invalid_rpc_response_length": len(exc.doc),
                                  "invalid_rpc_response_sha256": hashlib.sha256(
                                      exc.doc.encode("utf-8", errors="backslashreplace")).hexdigest()})
                else:
                    event["error_message"] = str(exc)[:2000]
                    if isinstance(exc, HTTPError):
                        event['transport_cause_type'] = type(exc.__cause__).__name__
                events.append(event)
                if attempt == 3:
                    filename = args[0] if args else kwargs.get("filename")
                    image_format = args[1] if len(args) > 1 else kwargs.get("format", "pillow")
                    display_id = args[2] if len(args) > 2 else kwargs.get("display_id")
                    if (serial is not None and len(args) <= 3 and image_format == "pillow"
                            and display_id is None and not set(kwargs) - {"filename", "format", "display_id"}):
                        # An independent read transport, not a synthetic image.
                        # No device file, input command, or uncertain write is replayed.
                        from io import BytesIO
                        from PIL import Image
                        for direct_attempt in range(1, 4):
                            response = subprocess.run(["adb", "-s", serial, "exec-out", "screencap", "-p"],
                                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
                            if not response.returncode:
                                break
                            detail = response.stderr.decode(errors='backslashreplace')[-1000:]
                            transient = (not response.stdout and (not detail.strip() or any(
                                marker in detail.lower() for marker in ('not found', 'offline', 'closed', 'broken pipe'))))
                            events.append({'event': 'screenshot_direct_read_failure', 'read_only': True,
                                           'input_redelivered': False, 'attempt': direct_attempt,
                                           'exit_code': response.returncode,
                                           'stdout_sha256': hashlib.sha256(response.stdout).hexdigest(),
                                           'stderr_sha256': hashlib.sha256(response.stderr).hexdigest(),
                                           'will_retry': transient and direct_attempt < 3})
                            if not transient or direct_attempt == 3:
                                break
                            time.sleep(1)
                        if response.returncode or not response.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
                            raise RuntimeError(f"direct screenshot read failed: exit={response.returncode}, "
                                               f"bytes={len(response.stdout)}, "
                                               f"stderr={response.stderr.decode(errors='backslashreplace')[-1000:]}") from exc
                        picture = Image.open(BytesIO(response.stdout))
                        picture.load()
                        if picture.mode == "RGBA":
                            picture = picture.convert("RGB")
                        events.append({"event": "screenshot_direct_adb_fallback", "read_only": True,
                                       "input_redelivered": False, "original_sdk_attempts": 3,
                                       "direct_capture_attempts": direct_attempt, "raw_png_bytes": len(response.stdout),
                                       "raw_png_sha256": hashlib.sha256(response.stdout).hexdigest(),
                                       "image_size": list(picture.size)})
                        if filename:
                            picture.save(filename)
                            return None
                        return picture
                    raise
                time.sleep(.2)

    device.screenshot = guarded_screenshot


def install_foreground_read_guard(device, events: list, serial: str | None = None) -> None:
    """Bounded retry of SDK foreground metadata reads in the evidence capture.

    Only transport AdbError or an empty RuntimeError is retried. No guessed
    package/PID is returned; a persistent read failure still stops UNKNOWN.
    """
    from adbutils import AdbError
    original = device.app_current

    def guarded_app_current():
        for attempt in range(1, 4):
            try:
                return original()
            except (AdbError, RuntimeError) as exc:
                if isinstance(exc, RuntimeError) and str(exc):
                    raise
                events.append({"event": "foreground_metadata_read_retry", "read_only": True,
                               "input_redelivered": False, "failed_read_attempt": attempt,
                               "will_retry": attempt < 3, "error_type": type(exc).__name__,
                               "error_message": str(exc)[:2000]})
                if attempt == 3:
                    if serial is not None:
                        from .structured_android import _adb_foreground_app
                        observed = _adb_foreground_app(serial)
                        events.append({'event': 'foreground_direct_adb_fallback', 'read_only': True,
                                       'input_redelivered': False, 'original_sdk_attempts': 3,
                                       'observed_foreground': observed})
                        return observed
                    raise
                time.sleep(.2)

    device.app_current = guarded_app_current


def android_ready(serial: str, timeout: float = 60) -> None:
    """Wait for services, rather than treating ADB transport as a ready OS."""
    deadline = time.monotonic() + timeout
    last = "Android services unavailable"
    while time.monotonic() < deadline:
        try:
            boot = command(["adb", "-s", serial, "shell", "getprop", "sys.boot_completed"], 10).strip()
            activity = command(["adb", "-s", serial, "shell", "service", "check", "activity"], 10)
            package = command(["adb", "-s", serial, "shell", "cmd", "package", "list", "packages", "android"], 10)
            if boot == "1" and "found" in activity and "package:android" in package:
                return
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            last = str(exc)
        time.sleep(.5)
    raise RuntimeError(f"Android readiness timed out: {last}")


def launch_baseline(case: dict):
    """Reconstruct each editor from the restored app state and a cold Activity."""
    execution = case["execution"]
    serial, package = execution["device_serial"], execution["package"]
    suffix = "/.ui.MainActivity" if case["case_id"] == "B002" else "/.MainActivity"
    command(["adb", "-s", serial, "shell", "am", "force-stop", package])
    last = "launch unavailable"
    for _ in range(3):
        try:
            android_ready(serial)
            output = command(["adb", "-s", serial, "shell", "am", "start", "-W", "-n", package + suffix])
            if "Error:" in output or "Failure" in output:
                raise RuntimeError(output)
            break
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            last = str(exc)
            time.sleep(1)
    else:
        raise RuntimeError(last)
    device = connect_batch_device(serial, [])
    if case["case_id"] == "B002":
        fab = device(resourceId=package + ":id/fab")
        if not fab.wait(timeout=10):
            raise RuntimeError("Debitum main page did not expose Create Transaction")
        fab.click()
    # A successful click returns before the fragment's editors are published.
    # Wait for the target editor rather than inspecting the old main-page XML.
    anchor = package + (":id/edit_amount" if case["case_id"] == "B002" else ":id/phone")
    if not device(resourceId=anchor).wait(timeout=10):
        raise RuntimeError(f"baseline editor did not appear: {anchor}")
    return device


def prepare_baselines(config_path: Path, output: Path) -> dict:
    """Create new snapshots on each actual AVD; copied RAM is not portable."""
    config = json.loads(config_path.read_text())
    validate_config(config)
    output.mkdir(parents=True, exist_ok=False)
    serial = config["cases"][0]["execution"]["device_serial"]
    android_ready(serial)
    locale = command(["adb", "-s", serial, "shell", "am", "get-config"]).splitlines()[0]
    if "cs-rCZ" not in locale:
        command(["adb", "-s", serial, "shell", "settings", "put", "system", "system_locales", "cs-CZ"])
        command(["adb", "-s", serial, "reboot"])
        time.sleep(2)
        command(["adb", "-s", serial, "wait-for-device"])
        android_ready(serial, 120)
    rows = []
    for case in config["cases"]:
        e = case["execution"]
        print(f"{serial}: preparing {case['case_id']} native baseline", flush=True)
        command(["adb", "-s", serial, "install", "-r", "-t", e["apk_path"]])
        _installed_receipt(serial, e)
        if case["case_id"] == "B002":
            # Set the documented decimal preference through the application.
            package = e["package"]
            command(["adb", "-s", serial, "shell", "am", "force-stop", package])
            command(["adb", "-s", serial, "shell", "am", "start", "-W", "-n", package + "/.ui.MainActivity"])
            import uiautomator2 as u2
            device = u2.connect(serial)
            device(resourceId=package + ":id/settings_dest").click()
            device(text="Nr of decimals").click()
            device(text="0", className="android.widget.CheckedTextView").click()
        device = launch_baseline(case)
        snapshot = Path(e["snapshot_path"])
        if snapshot.exists():
            raise FileExistsError(f"refusing to replace baseline: {snapshot}")
        case_output = output / case["case_id"]
        _capture(device, case_output, "baseline")
        response = command(["adb", "-s", serial, "emu", "avd", "snapshot", "save", snapshot.name], 180)
        if "KO" in response or not (snapshot / "snapshot.pb").is_file():
            raise RuntimeError(f"baseline snapshot save failed: {response.strip()}")
        rows.append({"case_id": case["case_id"], "snapshot": str(snapshot),
                     "snapshot_pb_sha256": _sha256_file(snapshot / "snapshot.pb")})
        print(f"{serial}: saved {case['case_id']} baseline", flush=True)
    report = {"no_model_call": True, "device_serial": serial, "snapshots": rows}
    _write_new(output / "report.json", report)
    return report


def wait_b002_settings_navigation_ready(device, package: str, events: list) -> None:
    """Observe two stable, enabled main-page targets before one admin click.

    This never sends or retries input. Three distinct hierarchy observations
    are allowed; each keeps the existing bounded read-only transport guard.
    """
    previous = None
    for observation in range(1, 4):
        xml = device.dump_hierarchy()
        root = ET.fromstring(xml)
        observed = []
        for resource in ('fab', 'settings_dest'):
            nodes = [n for n in root.iter('node') if n.get('resource-id') == package + ':id/' + resource]
            valid = len(nodes) == 1
            node = nodes[0] if valid else None
            bounds = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', node.get('bounds', '')) if node is not None else None
            valid = bool(valid and bounds and node.get('package') == package
                         and node.get('enabled') == 'true' and node.get('clickable') == 'true'
                         and node.get('visible-to-user', 'true') == 'true'
                         and int(bounds[3]) > int(bounds[1]) and int(bounds[4]) > int(bounds[2]))
            observed.append((resource, node.get('bounds') if valid else None))
        ready = all(bounds is not None for _, bounds in observed)
        stable = ready and observed == previous
        events.append({'event': 'b002_settings_navigation_readiness', 'stage': 'administrative_reset',
                       'read_only': True, 'input_redelivered': False, 'observation': observation,
                       'ready': ready, 'stable': stable, 'targets': observed,
                       'xml_sha256': hashlib.sha256(xml.encode()).hexdigest(), 'xml': xml})
        if stable:
            return
        previous = observed if ready else None
        if observation < 3:
            time.sleep(.3)
    raise RuntimeError('B002 settings navigation targets not stably ready; no navigation click sent')


def prepare_b002_zero_decimal_preference(device, package: str, cold_receipt: dict, events: list) -> None:
    """Keep the original preference actions, with pre-click readiness/evidence."""
    if not device(resourceId=package + ':id/fab').wait(timeout=30):
        raise RuntimeError('B002 main page not ready for original decimal preference')
    wait_b002_settings_navigation_ready(device, package, events)
    events.append({'event': 'b002_settings_navigation_requested', 'stage': 'administrative_reset',
                   'navigation_click_count': 1, 'candidate_input_started': False})
    device(resourceId=package + ':id/settings_dest').click()
    if not device(text='Nr of decimals').wait(timeout=30):
        # Preserve a true current hierarchy without re-clicking navigation.
        try:
            xml = device.dump_hierarchy()
            events.append({'event': 'b002_settings_navigation_postcondition_failed', 'read_only': True,
                           'input_redelivered': False, 'xml': xml,
                           'xml_sha256': hashlib.sha256(xml.encode()).hexdigest()})
        except Exception:
            events.append({'event': 'b002_settings_navigation_diagnostic_read_failed',
                           'read_only': True, 'input_redelivered': False,
                           'traceback': traceback.format_exc()})
        raise RuntimeError('B002 decimal preference page not ready after one navigation')
    events.append({'event': 'b002_settings_navigation_confirmed', 'navigation_click_count': 1})
    device(text='Nr of decimals').click()
    checked = device(className='android.widget.CheckedTextView', checked=True)
    cold_receipt['zero_decimal_preference_reapplied'] = False
    if checked.count == 1 and checked.get_text() != '0':
        # Same setting/action as prepare_baselines(), not a new condition.
        device(text='0', className='android.widget.CheckedTextView').click()
        if not device(text='Nr of decimals').wait(timeout=30):
            raise RuntimeError('B002 decimal preference page not ready after original setting')
        device(text='Nr of decimals').click()
        checked = device(className='android.widget.CheckedTextView', checked=True)
        cold_receipt['zero_decimal_preference_reapplied'] = True
    if checked.count != 1 or checked.get_text() != '0':
        raise RuntimeError('frozen B002 zero-decimal preference differs after cold disk restore')
    cold_receipt['frozen_zero_decimal_preference_verified'] = True
    cold_receipt['settings_navigation_stable_readiness_verified'] = True


def wait_baseline_foreground_ready(device, package: str, events: list) -> dict:
    """Observe readiness after the original launch; do not relaunch or input."""
    for attempt in range(1, 4):
        try:
            observed = device.app_current()
        except Exception:
            events.append({'event': 'baseline_foreground_read_failed', 'observation': attempt,
                           'read_only': True, 'input_redelivered': False,
                           'traceback': traceback.format_exc()})
            raise
        ready = observed.get('package') == package
        events.append({'event': 'baseline_foreground_readiness', 'observation': attempt,
                       'read_only': True, 'input_redelivered': False,
                       'expected_package': package, 'observed_foreground': observed,
                       'ready': ready})
        if ready:
            return observed
        if attempt < 3:
            time.sleep(.3)
    raise RuntimeError('baseline foreground differs after 3 readiness observations')


def reset(case: dict, preparation_events: list | None = None) -> dict:
    execution = case["execution"]
    serial = execution["device_serial"]
    snapshot = Path(execution["snapshot_path"])
    cold_receipt = {}
    if operational_reset_mode() == 'original-qcow2-snapshot-cold-boot':
        from .spec_source_backed_disk_reset import cold_restore
        cold_receipt = cold_restore(case)
    else:
        name = command(["adb", "-s", serial, "emu", "avd", "name"])
        if execution["avd_name"] not in name.splitlines():
            raise RuntimeError("AVD name differs")
        output = command(["adb", "-s", serial, "emu", "avd", "snapshot", "load", snapshot.name], 180)
        if "KO" in output:
            raise RuntimeError(f"snapshot load failed: {output.strip()}")
        command(["adb", "-s", serial, "wait-for-device"])
    android_ready(serial)
    command(["adb", "-s", serial, "shell", "cmd", "alarm", "set-time", str(int(time.time() * 1000))])
    if cold_receipt:
        registered = subprocess.run(['adb', '-s', serial, 'shell', 'pm', 'path', execution['package']],
                                    capture_output=True, text=True, timeout=30)
        cold_receipt['original_apk_registration_repaired'] = False
        if registered.returncode and not registered.stdout.strip() and not registered.stderr.strip():
            # RAM snapshots may contain a package registration not yet flushed
            # to packages.xml. Re-register only the exact original APK, keeping
            # its existing data (-r), never replacing it with a fixed version.
            if _sha256_file(Path(execution['apk_path'])) != execution['apk_sha256']:
                raise RuntimeError('original APK hash differs before registration repair')
            command(['adb', '-s', serial, 'install', '-r', '-t', execution['apk_path']], 120)
            cold_receipt['original_apk_registration_repaired'] = True
    installed = _installed_receipt(serial, execution)
    if cold_receipt and case['case_id'] == 'B002':
        package = execution['package']
        command(['adb', '-s', serial, 'shell', 'am', 'force-stop', package])
        command(['adb', '-s', serial, 'shell', 'am', 'start', '-W', '-n', package + '/.ui.MainActivity'])
        preference_device = connect_batch_device(serial, [])
        prepare_b002_zero_decimal_preference(preference_device, package, cold_receipt,
                                            preparation_events if preparation_events is not None else [])
    device = launch_baseline(case)
    driver = AndroidDriver(serial, device=device)
    root = ET.fromstring(driver.hierarchy())
    values = {}
    for key, selector in execution["bindings"].items():
        nodes = binding_nodes(root, selector)
        if len(nodes) != 1:
            raise RuntimeError(f"baseline binding unavailable: {key}")
        values[key] = logical_binding_value(nodes[0].get("text", ""), nodes[0].get("hint", ""))
    foreground = wait_baseline_foreground_ready(
        device, execution["package"],
        preparation_events if preparation_events is not None else [])
    locale = command(["adb", "-s", serial, "shell", "am", "get-config"]).splitlines()[0]
    if case["case_id"] == "B002" and "cs-rCZ" not in locale:
        raise RuntimeError("B002 requires cs-CZ")
    if case["case_id"] == "B018" and values[required_resource_ids(case)[-1]] != "+7 (   )    -  -  ":
        raise RuntimeError("B018 baseline mask differs")
    return {
        "case_id": case["case_id"], "reset_id": execution["reset_id"],
        "reset_verified": True, "snapshot": snapshot.name,
        "snapshot_pb_sha256": _sha256_file(snapshot / "snapshot.pb"),
        "installed": installed, "foreground": foreground,
        "baseline_values": values, "locale": locale, **cold_receipt,
    }


def field_value(driver: AndroidDriver, selector: dict) -> str | None:
    nodes = binding_nodes(ET.fromstring(driver.hierarchy()), selector)
    return logical_binding_value(nodes[0].get("text", ""), nodes[0].get("hint", "")) if len(nodes) == 1 else None


def xml_rendered_text(value: str) -> str:
    """UIAutomator's XML-safe UTF-16 rendering, not an input transformation.

    The original value is always delivered unchanged. XML-illegal code units
    become dots in the hierarchy; supplementary characters occupy two units.
    Never accept truncation, whitespace folding, or arbitrary mismatches.
    """
    return "".join(
        ".." if ord(c) > 0xFFFF else
        "." if (ord(c) < 32 and c not in "\t\n\r") or ord(c) in {0xFFFE, 0xFFFF} else c
        for c in value
    )


def auxiliary_readback(driver: AndroidDriver, selector: dict, value: str) -> tuple[bool, str | None, bool]:
    observed = field_value(driver, selector)
    expected = xml_rendered_text(value)
    placeholder_only = False
    if not value and selector.get("ancestor-resource-id", "").endswith("mtrl_picker_text_input_date"):
        root = ET.fromstring(driver.hierarchy())
        nodes = binding_nodes(root, selector)
        placeholders = [n.get("text", "") for n in root.iter("node")
                        if n.get("resource-id") == selector["package"] + ":id/textinput_placeholder"]
        placeholder_only = (len(nodes) == len(placeholders) == 1
                            and nodes[0].get("text") == nodes[0].get("hint", "") + ", " + placeholders[0])
    return observed in {value, expected} or placeholder_only, observed, placeholder_only


def set_auxiliary_text(driver: AndroidDriver, selector: dict, value: str, events: list) -> None:
    # These idempotent retries precede the monetary action; they neither call
    # the model again nor repeat an already observed experimental outcome.
    for attempt in range(1, 4):
        before = field_value(driver, selector)
        if before != value:
            obj = driver.binding_object(selector)
            obj.click()
            # clearInputText queues keyboard events. Keep clear and commit on
            # the same acknowledged IME transport to prevent late Deletes.
            driver.device._clear_text_with_ime()
            driver.device._send_keys_with_ime(value)
            time.sleep(.3)
        matches, observed, placeholder_only = auxiliary_readback(driver, selector, value)
        events.append({"event": "auxiliary_text", "binding": selector,
                       "requested_sha256": sha256_json(value), "requested_length": len(value),
                       "observed_value": observed, "xml_rendering_only": matches and observed != value,
                       "material_placeholder_only": placeholder_only,
                       "transport_attempt": attempt, "readback_verified": matches})
        if matches:
            return
        time.sleep(.3)
    raise RuntimeError("auxiliary field readback mismatch outside calibrated XML rendering")


def ensure_date_text_mode(driver: AndroidDriver, package: str, events: list) -> dict:
    """Retry a missed navigation click only while the picker is still a calendar.

    The dialog can expose its toggle before its opening animation settles.
    A missing text pane alone is not enough to click again: confirm calendar
    mode and an unchecked toggle first. No candidate has been typed yet.
    """
    selector = {"package": package, "class": "android.widget.EditText",
                "ancestor-resource-id": package + ":id/mtrl_picker_text_input_date"}
    device = driver.device
    pane = device(resourceId=selector["ancestor-resource-id"])
    toggle = device(resourceId=package + ":id/mtrl_picker_header_toggle")
    calendar = device(resourceId=package + ":id/mtrl_calendar_main_pane")
    for attempt in range(1, 4):
        if pane.exists:
            return selector
        if not toggle.wait(timeout=5):
            raise RuntimeError("date picker did not expose its text-mode toggle")
        info = toggle.info
        if info.get("checked") is not False or not calendar.exists:
            raise RuntimeError("date picker mode is ambiguous; refusing blind toggle")
        # The original date-field click opens an animated Material dialog.
        time.sleep(.5)
        toggle.click()
        appeared = bool(pane.wait(timeout=5))
        events.append({"event": "date_text_mode_navigation", "transport_attempt": attempt,
                       "calendar_mode_verified": True, "toggle_checked_before": False,
                       "text_mode_verified": appeared})
        if appeared:
            return selector
    raise RuntimeError("date picker text input did not appear after verified calendar clicks")


def set_date(driver: AndroidDriver, selector: dict, value: str, events: list) -> None:
    """Submit dates through the App's picker; preserve App-owned rejection.

    The visible date is not a free-text editor. Invalid model values are sent
    verbatim to the picker's text mode, recorded as rejected if its confirmation
    is disabled, then cancelled. They are not silently replaced by a valid date.
    This auxiliary field is outside the monetary oracle's frozen scope.
    """
    before = field_value(driver, selector)
    if before == value:
        events.append({"event": "date_input", "requested_value": value,
                       "outcome": "already_present", "observed_value": before})
        return
    package, device = selector["package"], driver.device
    driver.binding_object(selector).click()
    picker_selector = ensure_date_text_mode(driver, package, events)
    parsed = None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        pass
    delivered = parsed.strftime("%d.%m.%Y") if parsed else value
    try:
        set_auxiliary_text(driver, picker_selector, delivered, events)
    except RuntimeError as exc:
        # Android's date-key listener rejects letters before DatePicker's
        # parser sees them. An acknowledged original write of "not-a-date"
        # is therefore visibly "--", not a failed text transport. Only the
        # calibrated numeric/date character filter plus disabled confirmation
        # can opt into this auxiliary-field rejection path.
        filtered = "".join(c for c in delivered if c in "0123456789-./, ")
        matches_filter, observed_input, _ = auxiliary_readback(driver, picker_selector, filtered)
        confirm = device(resourceId=package + ":id/confirm_button")
        if (str(exc) != "auxiliary field readback mismatch outside calibrated XML rendering"
                or parsed is not None or filtered == delivered or not matches_filter
                or not confirm.exists or confirm.info.get("enabled")):
            raise
        events.append({"event": "date_app_input_filter_rejection", "delivered_value": delivered,
                       "observed_input": observed_input, "original_value_delivered_unchanged": True})
    # MaterialDatePicker performs delayed text validation (about one second).
    time.sleep(1.2)
    confirm = device(resourceId=package + ":id/confirm_button")
    if not confirm.exists:
        raise RuntimeError("date picker confirmation is unavailable")
    accepted = bool(confirm.info.get("enabled"))
    action = "confirm_button" if accepted else "cancel_button"
    driver.action_object({"package": package, "resource-id": package + ":id/" + action}).click()
    time.sleep(.3)
    observed = field_value(driver, selector)
    if observed is None:
        raise RuntimeError("date picker did not return to the transaction form")
    if accepted and parsed and observed != parsed.strftime("%Y-%m-%d"):
        raise RuntimeError("date picker committed a different calendar date")
    events.append({"event": "date_input", "requested_value": value,
                   "delivered_value": delivered,
                   "outcome": "accepted" if accepted else "app_rejected",
                   "observed_value": observed})


def save_b002_after_edit(device, driver, package: str, events: list) -> None:
    """Perform the frozen Save action without accidentally navigating Back.

    The frozen input_protocol says characters -> one Backspace -> Save if
    alive. Back is only an administrative keyboard-dismiss preparation, not
    part of that input sequence. When the IME is already hidden, an extra
    Back exits Create Transaction and prevents the specified Save action.
    Use the same actual-hierarchy IME read already used for auxiliary fields;
    no input, Back, or Save operation is retried.
    """
    visible = driver._ime_visible(package)
    if not isinstance(visible, bool):
        raise RuntimeError('keyboard visibility was not established')
    events.append({'event': 'keyboard_preparation_before_save', 'ime_visible': visible,
                   'administrative_back_requested': visible,
                   'frozen_action': 'Save if alive', 'input_redelivered': False})
    if visible:
        device.press('back')
    device(resourceId=package + ':id/miSaveTransaction').click()
    events.append({'event': 'save_after_edit_completed', 'save_action_count': 1,
                   'input_redelivered': False})


def execute(request: dict) -> dict:
    queued_at = time.monotonic()
    with ANDROID_OPERATION_LOCK:
        queue_ms = round((time.monotonic() - queued_at) * 1000)
        result = _execute_candidate(request)
        result['android_execution_serialized'] = True
        result['android_execution_queue_ms'] = queue_ms
        return result


def _execute_candidate(request: dict) -> dict:
    started = time.monotonic()
    case = request["case"]
    candidate = request["candidate"]
    execution = case["execution"]
    directory = Path(request["artifact_dir"])
    directory.mkdir(parents=True, exist_ok=False)
    serial, package = execution["device_serial"], execution["package"]
    result = {"schema_version": "spec-source-backed-key-execution-3", "case_id": case["case_id"],
              "candidate_sha256": sha256_json(candidate), "run_status": "infrastructure_error",
              "o_spec": "UNKNOWN", "historical_issue_reproduced": False,
              "public_observations": [], "unknown_reason": "execution incomplete"}
    events, captures = [], []
    try:
        receipt = reset(case, preparation_events=events)
        _write_new(directory / "reset_receipt.json", receipt)
        device = connect_batch_device(serial, events)
        install_input_method_read_guard(device, events)
        install_capture_read_guard(device, events, serial=serial)
        install_foreground_read_guard(device, events, serial=serial)
        driver = AndroidDriver(serial, device=device)
        initial_pid = _pid(serial, package)
        captures.append(_capture(device, directory, "before"))
        command(["adb", "-s", serial, "logcat", "-c"])
        keys = required_resource_ids(case)
        special = keys[0] if case["case_id"] == "B002" else keys[-1]
        interaction_error = None
        action_completed = False
        sent = 0
        observed = None
        try:
            auxiliary_keys = [key for key in keys if key != special]
            if case["case_id"] == "B002":
                auxiliary_keys.sort(key=lambda key: not key.endswith("edit_date_editText"))
            for key in auxiliary_keys:
                if key == special:
                    continue
                if case["case_id"] == "B018":
                    device(resourceId=key).set_text(candidate[key], timeout=3)
                elif key.endswith("edit_date_editText"):
                    set_date(driver, execution["bindings"][key], candidate[key], events)
                else:
                    set_auxiliary_text(driver, execution["bindings"][key], candidate[key], events)
                    # Empty idempotent writes need not focus the editor. An
                    # unconditional Back would dismiss the transaction form.
                    if driver._ime_visible(package):
                        device.press("back")
            selector = execution["bindings"][special]
            obj = driver.binding_object(selector)
            obj.click()
            if case["case_id"] == "B002" and field_value(driver, selector):
                obj.clear_text()
            device.set_fastinput_ime(True)
            for index, character in enumerate(candidate[special], 1):
                device.send_keys(character, clear=False)
                sent = index
                if index <= 30 or index == len(candidate[special]):
                    observed = field_value(driver, selector)
                    events.append({"event": "typed_character", "index": index,
                                   "character": character, "observed_value": observed})
                if not _pid(serial, package):
                    break
            if _pid(serial, package) and case["case_id"] == "B002":
                observed = field_value(driver, selector)
                captures.append(_capture(device, directory, "before_edit"))
                device.press("delete")
                events.append({"event": "backspace_after_entry", "pre_edit_value": observed})
                if _pid(serial, package):
                    save_b002_after_edit(device, driver, package, events)
            action_completed = sent == len(candidate[special])
        except Exception as exc:
            interaction_error = f"{type(exc).__name__}: {exc}"
            _write_new(directory / "interaction_error.txt", interaction_error)
            _write_new(directory / 'interaction_traceback.txt', traceback.format_exc())
        time.sleep(float(execution["observation_seconds"]))
        final_pid = _pid(serial, package)
        foreground = device.app_current()
        captures.append(_capture(device, directory, "after"))
        logcat = capture_logcat(serial, directory)
        crash = _crash_signature(logcat, package)
        issue = crash and (
            ("NumberFormatException" in logcat and "formatArbitraryDecimalInput" in logcat)
            if case["case_id"] == "B002" else
            ("IndexOutOfBoundsException" in logcat and "MaskedInputFilter.filter" in logcat)
        )
        mask_corrupted = False
        if case["case_id"] == "B018" and final_pid:
            observed = field_value(driver, execution["bindings"][special])
            mask_corrupted = observed is not None and (
                not observed.startswith("+7 (") or observed.count(")") != 1 or observed.count("-") != 2
            )
        if crash or mask_corrupted:
            verdict, reason = "FAIL", None
            public = ["The App crashed while processing the submitted input."] if crash else ["The phone input lost fixed mask characters while processing the submitted input."]
            status = "ok"
        elif interaction_error or not action_completed or final_pid != initial_pid or foreground.get("package") != package:
            verdict, reason, status = "UNKNOWN", "input/action or process evidence incomplete", "infrastructure_error"
            public = ["The execution did not establish the input-handling outcome."]
        else:
            verdict, reason, status = "PASS", None, "ok"
            public = ["The input and subsequent action completed; the same App process remained alive and foreground."]
        result.update(run_status=status, o_spec=verdict, unknown_reason=reason,
                      historical_issue_reproduced=bool(issue), public_observations=public,
                      observation={"initial_pid": initial_pid, "final_pid": final_pid,
                                   "foreground_package": foreground.get("package"), "crash_evidence": crash,
                                   "mask_corrupted": mask_corrupted, "characters_sent": sent,
                                   "requested_characters": len(candidate[special]), "action_completed": action_completed,
                                   "last_observed_value": observed, "interaction_error": interaction_error},
                      reset_receipt=receipt, captures=captures)
    except Exception as exc:
        result.update(error_type=type(exc).__name__, error_message=str(exc)[:2000])
        _write_new(directory / "adapter_error.txt", f"{type(exc).__name__}: {exc}")
        _write_new(directory / "adapter_traceback.txt", traceback.format_exc())
    _write_new(directory / "input_events.json", events)
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    result["evidence"] = [{"path": str(path.relative_to(directory)), "sha256": _sha256_file(path)}
                          for path in sorted(directory.rglob("*")) if path.is_file()]
    return result


def qualify(config_path: Path, output: Path) -> dict:
    config = json.loads(config_path.read_text())
    validate_config(config)
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for case in config["cases"]:
        outcomes = {}
        for label, candidate in case["hidden_smoke_fixtures"].items():
            print(f"{case['execution']['device_serial']}: {case['case_id']} {label} starting", flush=True)
            result = execute({"case": case, "candidate": candidate,
                              "artifact_dir": str(output / case["case_id"] / label)})
            _write_new(output / case["case_id"] / f"{label}.json", result)
            outcomes[label] = result
            print(f"{case['execution']['device_serial']}: {case['case_id']} {label} {result['o_spec']} {result.get('error_message', result.get('unknown_reason'))}", flush=True)
        passed = (outcomes["control"]["o_spec"] == "PASS"
                  and outcomes["control"]["run_status"] == "ok"
                  and outcomes["target"]["historical_issue_reproduced"] is True)
        if case["case_id"] == "B002":
            for cycle, date in enumerate(("2026-13-40", "9999-99-99", "2025-01-02", "not-a-date", "2026-13-01"), 1):
                edge = copy.deepcopy(case["hidden_smoke_fixtures"]["control"])
                edge["org.ebur.debitum:id/edit_description"] = "XML control \u0000\u0001 and emoji \U0001f4a3" + ("A" * 250 if cycle == 2 else "")
                if cycle == 5:
                    edge["org.ebur.debitum:id/edit_description"] = "   "
                edge["org.ebur.debitum:id/edit_date_editText"] = date
                label = f"auxiliary_edges_{cycle}"
                result = execute({"case": case, "candidate": edge,
                                  "artifact_dir": str(output / case["case_id"] / label)})
                _write_new(output / case["case_id"] / f"{label}.json", result)
                events = json.loads((output / case["case_id"] / label / "input_events.json").read_text())
                expected_date_outcome = "accepted" if cycle == 3 else "app_rejected"
                edge_passed = (result["run_status"] == "ok" and result["o_spec"] == "PASS"
                               and any(e["event"] == "date_input" and e["outcome"] == expected_date_outcome for e in events)
                               and any(e["event"] == "auxiliary_text" and
                                       (e["readback_verified"] if cycle == 5 else e["xml_rendering_only"]) for e in events))
                passed = passed and edge_passed
                print(f"{case['execution']['device_serial']}: auxiliary regression {cycle} passed={edge_passed}", flush=True)
        else:
            edge = copy.deepcopy(case["hidden_smoke_fixtures"]["control"])
            edge["mo.must.maskededittextbug:id/first_name"] = "JNI\u0000control"
            result = execute({"case": case, "candidate": edge,
                              "artifact_dir": str(output / case["case_id"] / "logcat_edge")})
            _write_new(output / case["case_id"] / "logcat_edge.json", result)
            edge_passed = result["run_status"] == "ok" and result["o_spec"] == "PASS"
            passed = passed and edge_passed
            print(f"{case['execution']['device_serial']}: raw logcat regression passed={edge_passed}", flush=True)
        rows.append({"case_id": case["case_id"], "passed": passed,
                     "control": outcomes["control"]["o_spec"], "target": outcomes["target"]["o_spec"]})
        if passed:
            e = case["execution"]
            receipt = outcomes["control"]["reset_receipt"]
            _write_new(output / case["case_id"] / "runtime_receipt.json", {
                "schema_version": "spec-feasibility-runtime-qualification-1",
                "protocol_id": config["protocol_id"], "case_id": case["case_id"], "no_model_call": True,
                "device_serial": e["device_serial"], "target_resource_ids": required_resource_ids(case),
                "target_fields_present": True, "reset_command": e["reset_command"],
                "reset_receipt": receipt, "installed": receipt["installed"],
                "local_apk_sha256": e["apk_sha256"], "local_apk_signer_sha256": e["apk_signer_sha256"],
                "control_target_evidence": rows[-1],
            })
    report = {"no_model_call": True, "passed": all(row["passed"] for row in rows), "cases": rows}
    report["executor_sha256"] = EXECUTOR_SOURCE_SHA256
    report['android_execution_serialized'] = True
    report['operational_reset_mode'] = operational_reset_mode()
    report['executor_disk_reset_dependency_sha256'] = DISK_RESET_SOURCE_SHA256
    _write_new(output / "report.json", report)
    return report


def trajectory_complete(rows: list[dict], turn_budget: int) -> bool:
    if not rows or [r["turn"] for r in rows] != list(range(1, len(rows) + 1)):
        return False
    if any(r.get("execution_run_status") == "infrastructure_error" for r in rows):
        return False
    return (any(r["candidate_target_partition"] == "TARGET" and r["o_spec"] == "FAIL" for r in rows)
            or rows[-1]["turn"] == turn_budget)


def repeat_schedule(config: dict) -> list[dict]:
    """Append new randomized cells without reshuffling the accepted prefix."""
    extension = config.get("extension")
    if not extension:
        return _schedule(config)
    prior = copy.deepcopy(config)
    prior["budget"]["replicates_per_case_arm"] = extension["prior_replicates_per_case_arm"]
    return _schedule(prior) + [r for r in _schedule(config)
                               if r["replicate_id"] > extension["prior_replicates_per_case_arm"]]


def extension_configs(source: Path, additional_replicates: int, cost_cap: float) -> list[dict]:
    """Construct a count-only extension of an immutable, completed study."""
    source = source.resolve()
    if type(additional_replicates) is not int or additional_replicates < 1 or cost_cap <= 0:
        raise ValueError("extension requires a positive replicate count and cost ceiling")
    originals = json.loads((source / "parallel_protocols.json").read_text())
    if any(c.get("extension") for c in originals):
        raise RuntimeError("nested extensions require a separately reviewed contract")
    parent = audit_recovery(source, originals)
    if not (source / "COMPLETED.json").is_file():
        raise RuntimeError("extension source is not fully completed and checksummed")
    completion = json.loads((source / "COMPLETED.json").read_text())
    if (completion.get("summary_sha256") != _sha256_file(source / "summary.json")
            or (source / "STOPPED.json").exists()
            or parent["completed_trajectories_not_rerun"] != len(originals[0]["cases"]) * len(ARMS) * originals[0]["budget"]["replicates_per_case_arm"]):
        raise RuntimeError("extension source is not fully completed and checksummed")
    before = originals[0]["budget"]["replicates_per_case_arm"]
    after = before + additional_replicates
    turns = originals[0]["budget"]["turns_per_trajectory"]
    size = len(originals[0]["cases"]) * len(ARMS)
    metadata = {"source": str(source), "source_checksum_sha256": parent["source_checksum_sha256"],
                "prior_replicates_per_case_arm": before, "added_replicates_per_case_arm": additional_replicates,
                "cumulative_replicates_per_case_arm": after, "max_new_paid_requests": size * additional_replicates * turns,
                "prior_paid_requests": parent["prior_paid_requests"], "cumulative_cost_cap_usd": cost_cap}
    result = []
    for original in originals:
        config = copy.deepcopy(original)
        config["extension"] = metadata
        config["budget"].update(replicates_per_case_arm=after, max_experimental_paid_requests=size * after * turns,
                                max_total_paid_requests=size * after * turns + original["budget"]["max_qualification_paid_requests"],
                                max_total_cost_usd=cost_cap)
        config["scope"]["interpretation"] = (
            f"{after} replicates per case-arm, including {before} immutable prior and {additional_replicates} newly sampled replicates; "
            f"{size * after} cumulative trajectories, at most {turns} turns each.")
        result.append(config)
    return result


def audit_extension(source: Path, configs: list[dict]) -> dict:
    metadata = configs[0].get("extension", {})
    expected = extension_configs(source, metadata.get("added_replicates_per_case_arm"), metadata.get("cumulative_cost_cap_usd", 0))
    if configs != expected:
        raise RuntimeError("extension changed fields beyond the authorized count/cost metadata")
    originals = json.loads((source / "parallel_protocols.json").read_text())
    plan = audit_recovery(source, originals)
    plan.update(schema_version="source-backed-extension-plan-1", extension_from=str(source.resolve()),
                prior_replicates_per_case_arm=metadata["prior_replicates_per_case_arm"],
                added_replicates_per_case_arm=metadata["added_replicates_per_case_arm"],
                expected_new_trajectories=len(configs[0]["cases"]) * len(ARMS) * metadata["added_replicates_per_case_arm"],
                max_new_paid_requests=metadata["max_new_paid_requests"])
    return plan


def recovery_protocol_cost_amendment(source_configs: list[dict], configs: list[dict]) -> dict | None:
    """Allow only an explicitly applied cumulative-cost increase on extension recovery.

    Immutable source protocols remain untouched. Counts, requests, models,
    prompts, execution and scoring must match byte-for-byte JSON values.
    run_parallel additionally requires the CLI cost override to agree.
    """
    if source_configs == configs:
        return None
    if len(source_configs) != len(configs):
        raise RuntimeError("recovery must use the original five frozen protocols")
    caps = []
    for old, new in zip(source_configs, configs):
        before, after = copy.deepcopy(old), copy.deepcopy(new)
        if not before.get("extension") or not after.get("extension"):
            raise RuntimeError("recovery must use the original five frozen protocols")
        old_cap = before["extension"].pop("cumulative_cost_cap_usd")
        new_cap = after["extension"].pop("cumulative_cost_cap_usd")
        old_budget_cap = before["budget"].pop("max_total_cost_usd")
        new_budget_cap = after["budget"].pop("max_total_cost_usd")
        if (type(old_cap) not in (int, float) or type(new_cap) not in (int, float)
                or not 0 < old_cap < new_cap < float("inf")
                or old_budget_cap != old_cap or new_budget_cap != new_cap or before != after):
            raise RuntimeError("recovery protocol amendment may only increase the cumulative cost cap")
        caps.append((old_cap, new_cap))
    if len(set(caps)) != 1:
        raise RuntimeError("five devices must share the same cumulative cost amendment")
    return {"source_cost_cap_usd": caps[0][0], "new_cost_cap_usd": caps[0][1],
            "other_protocol_fields_unchanged": True}


def audit_recovery(source: Path, configs: list[dict], authorized_http524_retries: list[str] | None = None,
                   authorized_provider_retries: list[str] | None = None) -> dict:
    """Fail closed on ambiguous paid requests or drift; never resubmit them."""
    source = source.resolve()
    cost_amendment = recovery_protocol_cost_amendment(
        json.loads((source / "parallel_protocols.json").read_text()), configs)
    checksum = source / "SHA256SUMS"
    verified = {}
    for line in checksum.read_text().splitlines():
        digest, relative = line.split("  ", 1)
        path = source / relative
        if not path.resolve().is_relative_to(source) or _sha256_file(path) != digest:
            raise RuntimeError(f"recovery evidence SHA-256 mismatch: {relative}")
        verified[relative] = digest
    schedule = repeat_schedule(configs[0])
    if json.loads((source / "execution_order.json").read_text())["trajectories"] != schedule:
        raise RuntimeError("recovery schedule differs")
    requests = sorted(source.glob("trajectories/**/turn_*/model_request_reserved.json"))
    records = sorted(source.glob("trajectories/**/turn_*/attempt_record.json"))
    responses = sorted(source.glob("trajectories/**/turn_*/model_result.json"))
    if len(requests) != len(responses):
        raise RuntimeError("ambiguous paid request: request/response/record counts differ")
    http524_authorized = set(authorized_http524_retries or [])
    transport_authorized = set(authorized_provider_retries or [])
    authorized = http524_authorized | transport_authorized
    if http524_authorized & transport_authorized:
        raise RuntimeError("retry request has duplicate authorization types")
    inherited_path = source / "historical_provider_failures.json"
    inherited = json.loads(inherited_path.read_text()) if inherited_path.exists() else []
    failures = []
    total_cost = 0.0
    groups, turn_rows = {}, {}
    repair_count = 0
    for reservation_path in requests:
        directory = reservation_path.parent
        relative = str(directory.relative_to(source))
        required = ["model_request_reserved.json", "model_result.json", "prompt.txt", "prompt_audit.json"]
        if any(str((directory / name).relative_to(source)) not in verified for name in required):
            raise RuntimeError("recovery turn is absent from the immutable checksum ledger")
        reservation = json.loads(reservation_path.read_text())
        response = json.loads((directory / "model_result.json").read_text())
        prompt = (directory / "prompt.txt").read_text()
        prompt_audit = json.loads((directory / "prompt_audit.json").read_text())
        if response.get("run_status") != "ok":
            message = response.get("provider_response", {}).get("msg", "")
            attempts = response.get("transport_attempts", [])
            is_http524 = (relative in http524_authorized and response.get("error_type") == "provider_error_response"
                          and "HTTP 524:" in message)
            is_transport_failure = (relative in transport_authorized
                                    and response.get("error_type") in {"TimeoutError", "ConnectionResetError"}
                                    and len(attempts) == 1 and attempts[0].get("status") == "transport_error"
                                    and attempts[0].get("attempt") == 1)
            if (not (is_http524 or is_transport_failure) or (directory / "attempt_record.json").exists()
                    or len(response.get("transport_attempts", [])) != 1
                    or any(response.get(k) is not None for k in ("usage", "raw_response", "candidate"))
                    or hashlib.sha256(prompt.encode()).hexdigest() != reservation["prompt_sha256"]
                    or reservation["prompt_sha256"] != prompt_audit["prompt_sha256"]):
                raise RuntimeError(f"failed paid request needs explicit HTTP524 retry authorization or exact transport retry authorization: {relative}")
            retry_kind = "HTTP524" if is_http524 else response["error_type"]
            failures.append({"source": str(directory), "relative_turn": relative,
                             "provider_response_file_sha256": _sha256_file(directory / "model_result.json"),
                             "request_cost_unknown": True,
                             "conservative_cost_reserve_usd": configs[0]["model"]["max_request_cost_usd"],
                             "failure_kind": retry_kind,
                             "retry_authorization": "explicit user authorization for one retry of this failed request"})
            turn_rows[relative] = {"reuse_execution": False, "retry_provider": True, "retry_kind": retry_kind}
            continue
        if str((directory / "attempt_record.json").relative_to(source)) not in verified:
            raise RuntimeError("successful paid response is missing a checksummed attempt record")
        record = json.loads((directory / "attempt_record.json").read_text())
        raw_digest = hashlib.sha256(response["raw_response"].encode("utf-8")).hexdigest()
        model = configs[0]["model"]
        cost = request_cost_usd(response["usage"], model["pricing_usd_per_million_tokens"])
        if (response.get("run_status") != "ok" or response.get("response_model") not in {model["id"], model["revision"]}
                or raw_digest != response.get("raw_response_sha256") or raw_digest != record.get("model_response_sha256")
                or hashlib.sha256(prompt.encode()).hexdigest() != reservation["prompt_sha256"]
                or reservation["prompt_sha256"] != prompt_audit["prompt_sha256"]
                or abs(cost - record["request_cost_usd"]) > 1e-9):
            raise RuntimeError(f"recovery paid response audit failed: {relative}")
        cell = {k: record[k] for k in ("case_id", "arm", "replicate_id")}
        if cell not in schedule or any(reservation[k] != record[k] for k in (*cell, "turn")):
            raise RuntimeError("recovery cell identity differs")
        lane = schedule.index(cell) % len(configs)
        case = next(c for c in configs[lane]["cases"] if c["case_id"] == cell["case_id"])
        if record.get("usable_response") is True and parse_candidate(response["raw_response"], case) != record["candidate"]:
            raise RuntimeError("recovery candidate differs from saved response")
        reusable_execution = (record.get("execution_run_status") == "ok" and record["o_spec"] in {"PASS", "FAIL"})
        if reusable_execution:
            result = json.loads((directory / "execution_result.json").read_text())
            if (result["candidate_sha256"] != sha256_json(record["candidate"])
                    or result["run_status"] != "ok" or result["o_spec"] != record["o_spec"]):
                raise RuntimeError("recovery execution receipt differs")
        if not reusable_execution and record.get("usable_response"):
            repair_count += 1
        key = (record["case_id"], record["arm"], record["replicate_id"])
        groups.setdefault(key, []).append(record)
        turn_rows[relative] = {"reuse_execution": reusable_execution or bool(record.get("parse_error")),
                               "response_sha256": raw_digest}
        total_cost += cost
    if len(records) + len(failures) != len(requests) or authorized != {f["relative_turn"] for f in failures}:
        raise RuntimeError("unreconciled or unused provider retry authorization")
    historical_failures = inherited + failures
    for rows in groups.values():
        rows.sort(key=lambda r: r["turn"])
        if [r["turn"] for r in rows] != list(range(1, len(rows) + 1)):
            raise RuntimeError("recovery turns are not contiguous")
        for i, row in enumerate(rows[:-1]):
            if trajectory_complete(rows[:i + 1], configs[0]["budget"]["turns_per_trajectory"]):
                raise RuntimeError("source contains turns after a terminal outcome")
    return {"schema_version": "source-backed-recovery-plan-1", "source": str(source),
            "source_checksum_sha256": _sha256_file(checksum), "verified_files": len(verified),
            "prior_paid_requests": len(requests) + len(inherited), "prior_experimental_cost_usd": total_cost,
            "reuse_model_responses": len(responses) - len(failures), "repair_android_executions": repair_count,
            "retry_provider_requests": len(failures), "historical_provider_failures": historical_failures,
            "unknown_request_cost_reserve_usd": sum(f["conservative_cost_reserve_usd"] for f in historical_failures),
            "reuse_execution_records": len(records) - repair_count,
            "completed_trajectories_not_rerun": sum(trajectory_complete(r, configs[0]["budget"]["turns_per_trajectory"]) for r in groups.values()),
            "turns": turn_rows, **({"cost_cap_amendment": cost_amendment} if cost_amendment else {})}


def case_arm_metrics(config: dict, groups: dict, minimum_replicate: int = 1) -> dict:
    metrics = {}
    for case in config["cases"]:
        for arm in ARMS:
            rows_by_rep = [rows for (c, a, replicate), rows in groups.items()
                           if c == case["case_id"] and a == arm and replicate >= minimum_replicate]
            rows = [row for group in rows_by_rep for row in group]
            counts = {v: sum(r["o_spec"] == v for r in rows) for v in ("PASS", "FAIL", "UNKNOWN")}
            metrics[f"{case['case_id']}/{arm}"] = {
                "completed": sum(trajectory_complete(r, config["budget"]["turns_per_trajectory"]) for r in rows_by_rep),
                "expected": config["budget"]["replicates_per_case_arm"] - minimum_replicate + 1, "started": len(rows_by_rep), "attempts": len(rows),
                "usable_responses": sum(r["usable_response"] for r in rows), "o_spec_counts": counts,
                "target_at_1": sum(g[0]["candidate_target_partition"] == "TARGET" for g in rows_by_rep),
                "target_at_3": sum(any(r["candidate_target_partition"] == "TARGET" for r in g) for g in rows_by_rep),
                "violation_at_1": sum(g[0]["candidate_target_partition"] == "TARGET" and g[0]["o_spec"] == "FAIL" for g in rows_by_rep),
                "violation_at_3": sum(any(r["candidate_target_partition"] == "TARGET" and r["o_spec"] == "FAIL" for r in g) for g in rows_by_rep),
                "historical_issue_trajectories": sum(any(r.get("historical_issue_reproduced") for r in g) for g in rows_by_rep),
                "input_tokens": sum(usage_tokens(r["usage"])[0] for r in rows),
                "output_tokens": sum(usage_tokens(r["usage"])[1] for r in rows),
                "cost_usd": sum(r["request_cost_usd"] for r in rows),
            }
    return metrics


def repeat_summary(output: Path, config: dict, recovery_plan: dict | None = None) -> dict:
    summary = build_summary(output)
    groups = {}
    for path in output.glob("trajectories/**/turn_*/attempt_record.json"):
        row = json.loads(path.read_text())
        groups.setdefault((row["case_id"], row["arm"], row["replicate_id"]), []).append(row)
    for rows in groups.values():
        rows.sort(key=lambda row: row["turn"])
    metrics = case_arm_metrics(config, groups)
    summary["case_arm_metrics"] = metrics
    summary["completed_trajectories"] = sum(m["completed"] for m in metrics.values())
    summary["complete"] = (all(m["completed"] == m["expected"] for m in metrics.values())
                           and not (output / "STOPPED.json").exists())
    if config.get("extension"):
        cutoff = config["extension"]["prior_replicates_per_case_arm"]
        added_metrics = case_arm_metrics(config, groups, cutoff + 1)
        added = {key: row["completed"] for key, row in added_metrics.items()}
        summary.update(extension_case_arm_completed=added, extension_new_case_arm_metrics=added_metrics,
                       completed_new_trajectories=sum(added.values()),
                       expected_new_trajectories=len(config["cases"]) * len(ARMS) * config["extension"]["added_replicates_per_case_arm"])
    if recovery_plan:
        reused = sum(str(path.parent.relative_to(output)) in recovery_plan["turns"]
                     and not recovery_plan["turns"][str(path.parent.relative_to(output))].get("retry_provider")
                     for path in output.glob("trajectories/**/turn_*/attempt_record.json"))
        summary.update(reused_paid_model_responses=reused,
                       new_provider_responses=summary["paid_experimental_requests_with_attempt_records"] - reused,
                       recovered_source=recovery_plan["source"],
                       prior_experimental_cost_usd=recovery_plan["prior_experimental_cost_usd"],
                       additional_experimental_cost_usd=summary["experimental_cost_usd"] - recovery_plan["prior_experimental_cost_usd"])
        failures = recovery_plan.get("historical_provider_failures", [])
        summary["historical_provider_failures"] = len(failures)
        summary["cumulative_paid_experimental_request_count"] = summary["paid_experimental_request_reservations"] + len(failures)
        summary["requests_with_unknown_historical_cost"] = len(failures)
        summary["unknown_request_cost_reserve_usd"] = recovery_plan.get("unknown_request_cost_reserve_usd", 0.)
        summary["total_cost_with_conservative_reserve_usd"] = summary["total_known_cost_usd"] + summary["unknown_request_cost_reserve_usd"]
    return summary


def run_parallel(config_paths: list[Path], output: Path, recovery_from: Path | None = None,
                 repair_qualification: Path | None = None, cost_cap: float | None = None,
                 authorized_http524_retries: list[str] | None = None,
                 authorized_provider_retries: list[str] | None = None, extension_from: Path | None = None) -> dict:
    if recovery_from and extension_from:
        raise ValueError("recovery and extension sources are mutually exclusive")
    configs = [json.loads(path.read_text()) for path in config_paths]
    if len(configs) != 5 or len({config["cases"][0]["execution"]["device_serial"] for config in configs}) != 5:
        raise ValueError("this protocol requires five distinct devices")
    normalized = []
    for config in configs:
        same = copy.deepcopy(config)
        for case in same["cases"]:
            for key in ("device_serial", "avd_name", "snapshot_path", "reset_command",
                        "runtime_qualification_receipt", "runtime_qualification_receipt_sha256"):
                case["execution"].pop(key, None)
        normalized.append(same)
    if any(value != normalized[0] for value in normalized[1:]):
        raise ValueError("five devices must share the same frozen experimental contract")
    gates = []
    for path, config in zip(config_paths, configs):
        validate_config(config, require_live=True)
        gate = local_preflight(path, config)
        if not gate["ready_for_live_run"]:
            raise RuntimeError(f"local preflight blocked: {path}")
        gates.append(gate)
    config = configs[0]
    if extension_from:
        if authorized_http524_retries or authorized_provider_retries:
            raise ValueError("a completed-source extension cannot retry old paid requests")
        plan = audit_extension(extension_from, configs)
        recovery_from = extension_from
    else:
        plan = audit_recovery(recovery_from, configs, authorized_http524_retries, authorized_provider_retries) if recovery_from else None
    if config.get("extension") and cost_cap is not None and cost_cap != config["extension"]["cumulative_cost_cap_usd"]:
        raise ValueError("extension cost override differs from the authorized cumulative ceiling")
    if plan and plan.get("cost_cap_amendment") and cost_cap != plan["cost_cap_amendment"]["new_cost_cap_usd"]:
        raise ValueError("recovery cost amendment requires an explicit matching CLI cost override")
    if recovery_from:
        if repair_qualification is None:
            raise RuntimeError("recovery requires fresh zero-provider executor qualification")
        for local in configs:
            serial = local["cases"][0]["execution"]["device_serial"]
            report = json.loads((repair_qualification / serial / "report.json").read_text())
            if (not report.get("passed") or report.get("no_model_call") is not True
                    or report.get("executor_sha256") != EXECUTOR_SOURCE_SHA256
                    or report.get('operational_reset_mode', 'native-memory-snapshot') != operational_reset_mode()):
                raise RuntimeError(f"repaired executor qualification missing or stale: {serial}")
    output.mkdir(parents=True, exist_ok=False)
    _materialize_frozen_inputs(output, config_paths[0], config)
    for name in ("spec_source_backed_repeat.py", "spec_feasibility_contract.py", "spec_feasibility_pilot.py",
                 "structured_android.py", "abcdef_gateway.py", "spec_source_backed_disk_reset.py"):
        _write_new(output / "code" / name, Path(__file__).with_name(name).read_text())
    _write_new(output / "parallel_protocols.json", configs)
    _write_new(output / "local_preflights.json", gates)
    if plan:
        _write_new(output / "recovery_plan.json", plan)
        if plan.get("historical_provider_failures"):
            _write_new(output / "historical_provider_failures.json", plan["historical_provider_failures"])
            for index, failure in enumerate(plan["historical_provider_failures"], 1):
                source_failure = Path(failure["source"])
                if _sha256_file(source_failure / "model_result.json") != failure["provider_response_file_sha256"]:
                    raise RuntimeError("historical provider failure evidence changed")
                shutil.copytree(source_failure, output / "failed_requests" / f"provider_attempt_{index:03d}")
    schedule = repeat_schedule(config)
    _write_new(output / "execution_order.json", {"schedule_seed": config["schedule_seed"], "trajectories": schedule})
    _write_new(output / "run_manifest.json", {"protocol_id": config["protocol_id"], "started_at": datetime.now(timezone.utc).isoformat(),
                "devices": [c["cases"][0]["execution"]["device_serial"] for c in configs],
                "shared_request_and_cost_caps": config["budget"], "fresh_context_per_request": True,
                "recovery_from": str(recovery_from) if recovery_from else None,
                "extension_from": str(extension_from) if extension_from else None,
                "effective_cost_cap_usd": cost_cap if cost_cap is not None else config["budget"]["max_total_cost_usd"],
                "executor_sha256": EXECUTOR_SOURCE_SHA256,
                "operational_reset_mode": operational_reset_mode(),
                "android_execution_serialized": True,
                "executor_disk_reset_dependency_sha256": DISK_RESET_SOURCE_SHA256})
    baselines = _frozen_baselines(config_paths[0], config)
    model = config["model"]
    budget = config["budget"]
    qualification = json.loads((output / "model_qualification_receipt.json").read_text())
    state = {"calls": plan["prior_paid_requests"] if plan else 0,
             "cost": float(qualification["request_cost_usd"]) + (plan["prior_experimental_cost_usd"] + plan.get("unknown_request_cost_reserve_usd", 0.) if plan else 0), "inflight": 0}
    lock, stop = threading.Lock(), threading.Event()
    capacity = threading.Condition(lock)
    effective_cost_cap = cost_cap if cost_cap is not None else budget["max_total_cost_usd"]
    stopped = []

    def halt(reason: dict) -> None:
        with lock:
            stopped.append(reason)
            stop.set()
            capacity.notify_all()

    previous_signal = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, lambda *_: halt({"reason": "user_interrupt"}))

    def worker(lane: int) -> None:
        local = configs[lane]
        gateway = _gateway(local)
        cases = {c["case_id"]: c for c in local["cases"]}
        for index, cell in enumerate(schedule):
            if index % len(configs) != lane:
                continue
            case = cases[cell["case_id"]]
            previous = []
            for turn in range(1, budget["turns_per_trajectory"] + 1):
                if stop.is_set():
                    return
                directory = output / "trajectories" / cell["case_id"] / cell["arm"] / f"replicate_{cell['replicate_id']:03d}" / f"turn_{turn:02d}"
                prompt, audit = render_prompt(case, cell["arm"], previous, baselines[cell["case_id"]])
                relative = str(directory.relative_to(output))
                source = recovery_from / relative if plan and relative in plan["turns"] else None
                retry_provider = bool(source and plan["turns"][relative].get("retry_provider"))
                if source:
                    if (source / "prompt.txt").read_text() != prompt:
                        halt({**cell, "turn": turn, "reason": "saved_prompt_differs_no_resubmission"})
                        return
                    if plan["turns"][relative]["reuse_execution"]:
                        shutil.copytree(source, directory)
                        record = json.loads((directory / "attempt_record.json").read_text())
                        previous.append(record)
                        print(json.dumps({**cell, "turn": turn, "reused_execution": True}), flush=True)
                        if trajectory_complete(previous, budget["turns_per_trajectory"]):
                            break
                        continue
                directory.mkdir(parents=True, exist_ok=False)
                _write_new(directory / "prompt.txt", prompt)
                _write_new(directory / "prompt_audit.json", audit)
                reuse_response = bool(source and not retry_provider)
                if reuse_response:
                    shutil.copy2(source / "model_request_reserved.json", directory / "model_request_reserved.json")
                    response = json.loads((source / "model_result.json").read_text())
                    _write_new(directory / "model_reuse_receipt.json", {"source": str(source),
                               "source_response_file_sha256": _sha256_file(source / "model_result.json"),
                               "prompt_matches_exactly": True, "new_provider_call": False})
                else:
                    with capacity:
                        while not stop.is_set() and state["cost"] + (state["inflight"] + 1) * model["max_request_cost_usd"] > effective_cost_cap and state["inflight"]:
                            capacity.wait(timeout=1)
                        if stop.is_set():
                            return
                        extension_request_cap = (config["extension"]["prior_paid_requests"] + config["extension"]["max_new_paid_requests"]
                                                 if config.get("extension") else budget["max_experimental_paid_requests"])
                        if state["calls"] >= min(budget["max_experimental_paid_requests"], extension_request_cap) or state["cost"] + model["max_request_cost_usd"] > effective_cost_cap:
                            stopped.append({"reason": "shared_budget_cap", "state": dict(state)})
                            stop.set()
                            capacity.notify_all()
                            return
                        state["calls"] += 1
                        state["inflight"] += 1
                    _write_new(directory / "model_request_reserved.json", {**cell, "turn": turn, "lane": lane,
                        "device_serial": case["execution"]["device_serial"], "prompt_sha256": audit["prompt_sha256"],
                        "model_request_origin": "new_provider_call", "fresh_provider_context_value": "",
                        "user_authorized_http524_retry_source": str(source) if retry_provider and plan["turns"][relative].get("retry_kind") == "HTTP524" else None,
                        "user_authorized_provider_retry_source": str(source) if retry_provider else None})
                    response = gateway.generate(model, prompt)
                _write_new(directory / "model_result.json", response)
                if response.get("run_status") != "ok" or response.get("response_model") not in {model["id"], model["revision"]}:
                    halt({**cell, "turn": turn, "reason": "provider_failure_or_revision_drift", "error_type": response.get("error_type")})
                    return
                try:
                    cost = request_cost_usd(response["usage"], model["pricing_usd_per_million_tokens"])
                except Exception as exc:
                    halt({**cell, "turn": turn, "reason": "missing_cost_telemetry", "error_type": type(exc).__name__})
                    return
                if not reuse_response:
                    with capacity:
                        state["inflight"] -= 1
                        state["cost"] += cost
                        capacity.notify_all()
                record = {**cell, "turn": turn, "paid_model_request": True, "lane": lane,
                    "device_serial": case["execution"]["device_serial"],
                    "model_request_origin": "reused_stopped_run" if reuse_response else "new_provider_call",
                    "model_response_sha256": response["raw_response_sha256"], "usage": response["usage"],
                    "request_cost_usd": cost, "model_elapsed_ms": response.get("elapsed_ms"), "usable_response": False,
                    "candidate": None, "candidate_target_partition": "UNKNOWN", "o_spec": "UNKNOWN", "public_observations": []}
                try:
                    candidate = parse_candidate(response["raw_response"], case)
                except ValueError as exc:
                    record.update(parse_error=str(exc), unknown_reason="candidate did not satisfy the frozen output schema")
                    _write_new(directory / "attempt_record.json", record)
                    previous.append(record)
                    continue
                result = execute({"case": case, "candidate": candidate, "artifact_dir": str(directory / "execution")})
                _write_new(directory / "execution_result.json", result)
                record.update(usable_response=True, candidate=candidate, candidate_target_partition=candidate_partition(case, candidate),
                    o_spec=result["o_spec"], unknown_reason=result.get("unknown_reason"), public_observations=result["public_observations"],
                    execution_run_status=result["run_status"], execution_elapsed_ms=result["elapsed_ms"],
                    historical_issue_reproduced=result["historical_issue_reproduced"])
                _write_new(directory / "attempt_record.json", record)
                print(json.dumps({**cell, "turn": turn, "lane": lane, "o_spec": record["o_spec"],
                                  "target": record["candidate_target_partition"], "historical_issue": record["historical_issue_reproduced"]}), flush=True)
                previous.append(record)
                if cost > model["max_request_cost_usd"]:
                    halt({"reason": "per_request_cost_cap", **cell, "cost": cost})
                    return
                if result["run_status"] != "ok" or result["o_spec"] == "UNKNOWN":
                    halt({**cell, "turn": turn, "reason": "execution_evidence_incomplete", "detail": result.get("error_message", result.get("unknown_reason"))})
                    return
                if record["candidate_target_partition"] == "TARGET" and result["o_spec"] == "FAIL":
                    break

    try:
        with ThreadPoolExecutor(max_workers=len(configs)) as pool:
            futures = [pool.submit(worker, lane) for lane in range(len(configs))]
            for future in futures:
                try:
                    future.result()
                except Exception as exc:
                    halt({"reason": "worker_exception", "error_type": type(exc).__name__, "detail": str(exc)[:2000]})
    finally:
        signal.signal(signal.SIGINT, previous_signal)
    if stopped:
        _write_new(output / "STOPPED.json", {"reasons": stopped, "state": state})
    summary = repeat_summary(output, config, plan)
    write_report(output, summary)
    lines = ["# B002/B018 source-backed repeated experiment", "",
             "Descriptive two-case study; candidate targeting and observed App violations are distinct.", "",
             "| Case / Arm | Completed | Attempts | Target @1 / @3 | Target and FAIL @1 / @3 | Historical issue trajectories | PASS / FAIL / UNKNOWN |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for key, m in summary["case_arm_metrics"].items():
        v = m["o_spec_counts"]
        lines.append(f"| {key} | {m['completed']}/{m['expected']} | {m['attempts']} | {m['target_at_1']}/{m['target_at_3']} | "
                     f"{m['violation_at_1']}/{m['violation_at_3']} | {m['historical_issue_trajectories']} | {v['PASS']}/{v['FAIL']}/{v['UNKNOWN']} |")
    if summary.get("extension_new_case_arm_metrics"):
        lines.extend(["", "## Newly sampled extension only (old replicates excluded)", "",
                      "| Case / Arm | New completed | Attempts | Target @1 / @3 | Target and FAIL @1 / @3 | Historical issue trajectories | PASS / FAIL / UNKNOWN |",
                      "|---|---:|---:|---:|---:|---:|---:|"])
        for key, m in summary["extension_new_case_arm_metrics"].items():
            v = m["o_spec_counts"]
            lines.append(f"| {key} | {m['completed']}/{m['expected']} | {m['attempts']} | {m['target_at_1']}/{m['target_at_3']} | "
                         f"{m['violation_at_1']}/{m['violation_at_3']} | {m['historical_issue_trajectories']} | {v['PASS']}/{v['FAIL']}/{v['UNKNOWN']} |")
    lines.extend(["", f"Complete: {summary['complete']}; completed trajectories: {summary['completed_trajectories']}/{summary['expected_trajectories']}.",
                  f"Unique experimental requests across the recovery lineage (including failed requests): {summary.get('cumulative_paid_experimental_request_count', summary['paid_experimental_request_reservations'])}.",
                  f"Reused responses: {summary['reused_paid_model_responses']}; new responses: {summary['new_provider_responses']}.",
                  f"Experimental cost estimate: USD {summary['experimental_cost_usd']:.6f}. The model qualification receipt is historical, not a new call.",
                  f"Historical requests with unknown cost: {summary.get('requests_with_unknown_historical_cost', 0)}; conservative reserve: USD {summary.get('unknown_request_cost_reserve_usd', 0):.6f}.",
                  "", "A trajectory finishes after TARGET plus FAIL, or all three allowed turns. OUTSIDE plus FAIL alone is not an early terminal condition.",
                  "Interrupted executions reuse saved model responses; all valid prior execution records are copied without rerunning Android.",
                  "Only auxiliary-field transport/readback was repaired. The APKs, specifications, prompts, target predicate, monetary/phone action and oracle remain frozen.", ""])
    _write_new(output / "case_arm_report.md", "\n".join(lines))
    if summary["complete"]:
        _write_new(output / "COMPLETED.json", {"summary_sha256": _sha256_file(output / "summary.json")})
    _write_sha256s(output)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("qualify", "run", "reset", "prepare", "extend-prepare"))
    parser.add_argument("--config", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case-id", choices=("B002", "B018"))
    parser.add_argument("--recovery-from", type=Path)
    parser.add_argument("--extension-from", type=Path)
    parser.add_argument("--additional-replicates", type=int)
    parser.add_argument("--repair-qualification", type=Path)
    parser.add_argument("--cost-cap-usd", type=float)
    parser.add_argument("--authorize-http524-retry", action="append", default=[])
    parser.add_argument("--authorize-provider-retry", action="append", default=[])
    args = parser.parse_args()
    if args.mode == "extend-prepare":
        configs = extension_configs(args.extension_from.resolve(), args.additional_replicates, args.cost_cap_usd)
        args.output.mkdir(parents=True, exist_ok=False)
        for config in configs:
            port = config["cases"][0]["execution"]["device_serial"].split("-")[-1]
            _write_new(args.output / f"port_{port}.json", config)
        result = audit_extension(args.extension_from.resolve(), configs)
        _write_new(args.output / "extension_plan.json", result)
    elif args.mode == "reset":
        config = json.loads(args.config[0].read_text())
        result = reset(next(case for case in config["cases"] if case["case_id"] == args.case_id))
    elif args.mode == "prepare":
        result = prepare_baselines(args.config[0].resolve(), args.output.resolve())
    elif args.mode == "qualify":
        result = qualify(args.config[0].resolve(), args.output.resolve())
    else:
        result = run_parallel([path.resolve() for path in args.config], args.output.resolve(),
                              args.recovery_from.resolve() if args.recovery_from else None,
                              args.repair_qualification.resolve() if args.repair_qualification else None,
                              args.cost_cap_usd, args.authorize_http524_retry, args.authorize_provider_retry,
                              args.extension_from.resolve() if args.extension_from else None)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
