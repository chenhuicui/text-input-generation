from __future__ import annotations
import copy
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from text_input_eval.supplementary.spec_source_backed_disk_reset import has_snapshot_tag, scoped_target, matching_process
from text_input_eval.supplementary.spec_source_backed_repeat import operational_reset_mode


class FrozenDiskRestoreTests(unittest.TestCase):
    def case(self):
        return {'case_id': 'B018', 'execution': {
            'device_serial': 'emulator-5580', 'avd_name': 'cd_spec_repeat10_5580',
            'snapshot_path': '/Volumes/Extreme Pro/emulator/spec_repeat10_5580/snapshots/B018_spec_native_20261014T023049Z_5580'}}

    def test_exact_snapshot_tag_not_substring(self):
        listing = 'Snapshot list:\n109 B018_spec_native_20261014T023049Z_5580 276M date clock\n'
        self.assertTrue(has_snapshot_tag(listing, 'B018_spec_native_20261014T023049Z_5580'))
        self.assertFalse(has_snapshot_tag(listing, 'B018'))
        self.assertFalse(has_snapshot_tag(listing, 'B002_spec_native_20261014T023049Z_5580'))

    def test_other_emulator_is_never_a_cold_restore_target(self):
        case = self.case();case['execution']['device_serial'] = 'emulator-5560'
        with self.assertRaisesRegex(RuntimeError, 'limited to the five'):
            scoped_target(case)

    def test_avd_identity_mismatch_is_rejected(self):
        case = self.case();case['execution']['avd_name'] = 'other-avd'
        with self.assertRaisesRegex(RuntimeError, 'identity mismatch'):
            scoped_target(case)

    def test_alternative_or_new_snapshot_is_rejected(self):
        case = self.case();case['execution']['snapshot_path'] += '_new'
        with self.assertRaisesRegex(RuntimeError, 'original snapshot tag'):
            scoped_target(case)

    def test_process_matching_preserves_unrelated_emulators(self):
        text = ('777 qemu-system-aarch64-headless -avd cd_google_migrated_api35 -port 5564\n'
                '888 qemu-system-aarch64-headless -avd cd_spec_repeat10_5580 -port 5580\n')
        with patch('subprocess.check_output', return_value=text):
            self.assertEqual(matching_process(5580, 'cd_spec_repeat10_5580')[0], 888)
            self.assertIsNone(matching_process(5582, 'cd_spec_repeat10_5582'))

    def test_process_with_wrong_port_is_not_stopped(self):
        with patch('subprocess.check_output', return_value='888 qemu-system-aarch64-headless -avd cd_spec_repeat10_5580 -port 5564\n'):
            with self.assertRaisesRegex(RuntimeError, 'port mismatch'):
                matching_process(5580, 'cd_spec_repeat10_5580')

    def test_reset_mode_is_explicit_not_inferred(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(operational_reset_mode(), 'native-memory-snapshot')
        with patch.dict(os.environ, {'SPEC_SOURCE_BACKED_DISK_ONLY_RESET': '1'}):
            self.assertEqual(operational_reset_mode(), 'original-qcow2-snapshot-cold-boot')

    def test_executor_fingerprint_covers_both_reset_files(self):
        import hashlib
        import text_input_eval.supplementary.spec_source_backed_repeat as module
        main = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
        helper = hashlib.sha256(Path(module.__file__).with_name('spec_source_backed_disk_reset.py').read_bytes()).hexdigest()
        self.assertEqual(module.DISK_RESET_SOURCE_SHA256, helper)
        self.assertEqual(module.EXECUTOR_SOURCE_SHA256, hashlib.sha256((main + helper).encode()).hexdigest())
