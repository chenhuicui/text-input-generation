from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from text_input_eval.supplementary.spec_feasibility_contract import candidate_partition, render_prompt, sha256_json, usage_tokens
from text_input_eval.supplementary.spec_feasibility_pilot import _schedule, _write_new, _write_sha256s
from text_input_eval.supplementary.spec_source_backed_repeat import (
    EXECUTOR_SOURCE_SHA256,
    android_ready, audit_extension, audit_recovery, capture_logcat, ensure_date_text_mode, extension_configs,
    install_rpc_forward_guard, install_hierarchy_read_guard,
    save_b002_after_edit,
    wait_b002_settings_navigation_ready, prepare_b002_zero_decimal_preference,
    wait_logcat_transport_ready, wait_baseline_foreground_ready,
    install_capture_read_guard, install_foreground_read_guard, install_input_method_read_guard, repeat_schedule, repeat_summary,
    recovery_protocol_cost_amendment, run_parallel, set_auxiliary_text, set_date,
    trajectory_complete, xml_rendered_text,
)


ROOT = Path(__file__).resolve().parents[3]


class RepeatProtocolTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "configs/supplementary/spec_source_backed_smoke_v1.json").read_text())

    @staticmethod
    def decimal_navigation_xml(bounds='[80,80][100,100]', enabled='true'):
        return ('<hierarchy><node resource-id="org.ebur.debitum:id/fab" package="org.ebur.debitum" '
                'enabled="true" clickable="true" bounds="[40,40][60,60]"/>'
                '<node resource-id="org.ebur.debitum:id/settings_dest" package="org.ebur.debitum" '
                f'enabled="{enabled}" clickable="true" bounds="{bounds}"/></hierarchy>')

    def decimal_navigation_device(self, xmls=None):
        from unittest.mock import Mock
        nodes = {name: Mock() for name in ('fab','settings','decimal','checked','zero')}
        nodes['fab'].wait.return_value = True
        nodes['decimal'].wait.return_value = True
        nodes['checked'].count = 1
        nodes['checked'].get_text.return_value = '0'
        def select(**kw):
            if 'resourceId' in kw:
                return nodes['fab' if kw['resourceId'].endswith(':id/fab') else 'settings']
            if kw.get('checked'):
                return nodes['checked']
            return nodes['decimal' if kw.get('text') == 'Nr of decimals' else 'zero']
        device = Mock(side_effect=select)
        device.dump_hierarchy.side_effect = xmls or [self.decimal_navigation_xml()] * 3
        return device, nodes

    def test_decimal_navigation_waits_for_two_actual_stable_reads_before_one_click(self):
        device, nodes = self.decimal_navigation_device()
        events, receipt = [], {}
        with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):
            prepare_b002_zero_decimal_preference(device, 'org.ebur.debitum', receipt, events)
        self.assertEqual(device.dump_hierarchy.call_count, 2)
        nodes['settings'].click.assert_called_once_with()
        nodes['decimal'].click.assert_called_once_with()
        nodes['zero'].click.assert_not_called()
        self.assertTrue(receipt['frozen_zero_decimal_preference_verified'])
        self.assertEqual([e['stable'] for e in events if e['event']=='b002_settings_navigation_readiness'], [False,True])

    def test_decimal_navigation_changed_bounds_need_another_actual_stable_read(self):
        first, second = self.decimal_navigation_xml(), self.decimal_navigation_xml('[70,70][90,90]')
        device, nodes = self.decimal_navigation_device([first, second, second])
        with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):
            prepare_b002_zero_decimal_preference(device, 'org.ebur.debitum', {}, [])
        self.assertEqual(device.dump_hierarchy.call_count, 3)
        nodes['settings'].click.assert_called_once_with()

    def test_decimal_navigation_unstable_after_three_reads_sends_no_navigation(self):
        xmls=[self.decimal_navigation_xml(f'[{x},{x}][100,100]') for x in (60,70,80)]
        device, nodes = self.decimal_navigation_device(xmls)
        with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):
            with self.assertRaisesRegex(RuntimeError,'not stably ready'):
                prepare_b002_zero_decimal_preference(device, 'org.ebur.debitum', {}, [])
        self.assertEqual(device.dump_hierarchy.call_count, 3)
        nodes['settings'].click.assert_not_called()
        nodes['decimal'].click.assert_not_called()

    def test_decimal_navigation_disabled_or_invalid_bounds_never_guessed_ready(self):
        for xml in (self.decimal_navigation_xml(enabled='false'), self.decimal_navigation_xml('[0,0][0,0]'), '<hierarchy/>'):
            device, nodes = self.decimal_navigation_device([xml]*3)
            with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):
                with self.assertRaisesRegex(RuntimeError,'not stably ready'):
                    prepare_b002_zero_decimal_preference(device, 'org.ebur.debitum', {}, [])
            self.assertEqual(device.dump_hierarchy.call_count, 3)
            nodes['settings'].click.assert_not_called()

    def test_decimal_navigation_uncertain_click_is_never_replayed(self):
        device, nodes = self.decimal_navigation_device()
        nodes['settings'].click.side_effect=RuntimeError('uncertain navigation')
        with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):
            with self.assertRaisesRegex(RuntimeError,'uncertain navigation'):
                prepare_b002_zero_decimal_preference(device, 'org.ebur.debitum', {}, [])
        nodes['settings'].click.assert_called_once_with()
        nodes['decimal'].click.assert_not_called()

    def test_decimal_navigation_missing_postcondition_keeps_xml_and_no_click_retry(self):
        device, nodes = self.decimal_navigation_device()
        nodes['decimal'].wait.return_value = False
        events=[]
        with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):
            with self.assertRaisesRegex(RuntimeError,'after one navigation'):
                prepare_b002_zero_decimal_preference(device, 'org.ebur.debitum', {}, events)
        nodes['settings'].click.assert_called_once_with()
        nodes['decimal'].click.assert_not_called()
        evidence=[e for e in events if e['event']=='b002_settings_navigation_postcondition_failed']
        self.assertEqual(evidence[0]['xml'],self.decimal_navigation_xml())
        self.assertFalse(evidence[0]['input_redelivered'])

    def test_decimal_navigation_preserves_original_zero_setting_and_reopen_once(self):
        device, nodes = self.decimal_navigation_device()
        nodes['checked'].get_text.side_effect=['2','0']
        receipt={}
        with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):
            prepare_b002_zero_decimal_preference(device, 'org.ebur.debitum', receipt, [])
        nodes['settings'].click.assert_called_once_with()
        nodes['zero'].click.assert_called_once_with()
        self.assertEqual(nodes['decimal'].click.call_count,2)
        self.assertTrue(receipt['zero_decimal_preference_reapplied'])

    def test_decimal_navigation_read_error_is_not_converted_to_ready_or_input_retry(self):
        device, nodes = self.decimal_navigation_device()
        device.dump_hierarchy.side_effect=ValueError('semantic hierarchy failure')
        with self.assertRaisesRegex(ValueError,'semantic hierarchy'):
            prepare_b002_zero_decimal_preference(device, 'org.ebur.debitum', {}, [])
        device.dump_hierarchy.assert_called_once_with()
        nodes['settings'].click.assert_not_called()

    def test_android_serialization_preserves_each_exact_request_once(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        import time
        from text_input_eval.supplementary.spec_source_backed_repeat import execute
        seen, active, peak = [], 0, 0
        guard = threading.Lock()
        requests = [{'case': {'case_id': 'B002'}, 'candidate': {'amount': str(i)}} for i in range(5)]
        def action(request):
            nonlocal active, peak
            with guard:
                active += 1; peak = max(peak, active); seen.append(request)
            time.sleep(.01)
            with guard:
                active -= 1
            return {'request': request, 'o_spec': 'PASS'}
        with patch('text_input_eval.supplementary.spec_source_backed_repeat._execute_candidate', side_effect=action), ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(execute, requests))
        self.assertEqual(peak, 1)
        self.assertEqual(len(seen), 5)
        self.assertEqual({id(r) for r in seen}, {id(r) for r in requests})
        self.assertTrue(all(r['android_execution_serialized'] for r in results))
        self.assertEqual([r['request'] for r in results], requests)

    def test_android_serialization_never_replays_uncertain_failure(self):
        from text_input_eval.supplementary.spec_source_backed_repeat import execute
        with patch('text_input_eval.supplementary.spec_source_backed_repeat._execute_candidate', side_effect=RuntimeError('uncertain input')) as action:
            with self.assertRaisesRegex(RuntimeError, 'uncertain input'):
                execute({'candidate': {'amount': '1000'}})
            action.assert_called_once()

    def test_save_preparation_does_not_navigate_back_when_keyboard_already_hidden(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        driver=SimpleNamespace(_ime_visible=Mock(return_value=False))
        device,events=Mock(),[]
        save_b002_after_edit(device,driver,'org.ebur.debitum',events)
        driver._ime_visible.assert_called_once_with('org.ebur.debitum')
        device.press.assert_not_called()
        device.assert_called_once_with(resourceId='org.ebur.debitum:id/miSaveTransaction')
        device.return_value.click.assert_called_once_with()
        self.assertFalse(events[0]['administrative_back_requested'])
        self.assertEqual(events[-1]['save_action_count'],1)

    def test_save_preparation_hides_visible_keyboard_once_before_original_save(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        order=[]
        driver=SimpleNamespace(_ime_visible=Mock(side_effect=lambda p: order.append('read_ime') or True))
        device=Mock();device.press.side_effect=lambda key:order.append(key)
        device.return_value.click.side_effect=lambda:order.append('save')
        save_b002_after_edit(device,driver,'org.ebur.debitum',[])
        self.assertEqual(order,['read_ime','back','save'])
        device.press.assert_called_once_with('back')
        device.return_value.click.assert_called_once_with()

    def test_save_preparation_missing_visibility_is_not_assumed_hidden(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from adbutils import AdbError
        for observed in (None,AdbError()):
            reader=Mock(side_effect=observed) if isinstance(observed,Exception) else Mock(return_value=observed)
            driver,device=SimpleNamespace(_ime_visible=reader),Mock()
            with self.assertRaises((RuntimeError,AdbError)):
                save_b002_after_edit(device,driver,'org.ebur.debitum',[])
            device.press.assert_not_called();device.assert_not_called()

    def test_save_preparation_never_replays_an_uncertain_back(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        driver,device=SimpleNamespace(_ime_visible=Mock(return_value=True)),Mock()
        device.press.side_effect=RuntimeError('uncertain back acknowledgement')
        with self.assertRaisesRegex(RuntimeError,'uncertain back'):
            save_b002_after_edit(device,driver,'org.ebur.debitum',[])
        device.press.assert_called_once_with('back');device.assert_not_called()

    def test_save_preparation_never_replays_an_uncertain_save(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        driver,device=SimpleNamespace(_ime_visible=Mock(return_value=False)),Mock()
        device.return_value.click.side_effect=RuntimeError('uncertain save acknowledgement')
        with self.assertRaisesRegex(RuntimeError,'uncertain save'):
            save_b002_after_edit(device,driver,'org.ebur.debitum',[])
        device.return_value.click.assert_called_once_with();device.press.assert_not_called()

    def test_pre_rpc_forward_guard_retries_only_endpoint_preparation(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from adbutils import AdbError
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        endpoint = Mock(side_effect=[AdbError(), 12345])
        device, events = SimpleNamespace(forward_port=endpoint), []
        install_rpc_forward_guard(device, 'emulator-5580', events)
        with patch(module + '.time.sleep'):
            self.assertEqual(device.forward_port(9008), 12345)
        self.assertEqual(endpoint.call_count, 2)
        self.assertTrue(events[0]['before_http_rpc_send'])
        self.assertFalse(events[0]['input_redelivered'])

    def test_pre_rpc_forward_guard_uses_exact_existing_mapping_after_three_sdk_failures(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from adbutils import AdbError
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        original = Mock(side_effect=AdbError())
        device, events = SimpleNamespace(forward_port=original), []
        install_rpc_forward_guard(device, 'emulator-5580', events)
        listed = SimpleNamespace(returncode=0, stdout=b'emulator-5564 tcp:23456 tcp:9008\nemulator-5580 tcp:12345 tcp:9008\n', stderr=b'')
        with patch(module + '.subprocess.run', return_value=listed) as cli, patch(module + '.time.sleep'):
            self.assertEqual(device.forward_port(9008), 12345)
        self.assertEqual(original.call_count, 3)
        self.assertEqual(cli.call_args.args[0], ['adb','-s','emulator-5580','forward','--list'])
        self.assertFalse(events[-1]['created_mapping'])

    def test_pre_rpc_forward_guard_allocates_and_independently_verifies_one_mapping(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from adbutils import AdbError
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        device, events = SimpleNamespace(forward_port=Mock(side_effect=AdbError())), []
        install_rpc_forward_guard(device, 'emulator-5580', events)
        responses = [SimpleNamespace(returncode=0,stdout=b'',stderr=b''),
                     SimpleNamespace(returncode=0,stdout=b'12345\n',stderr=b''),
                     SimpleNamespace(returncode=0,stdout=b'emulator-5580 tcp:12345 tcp:9008\n',stderr=b'')]
        with patch(module + '.subprocess.run', side_effect=responses) as cli, patch(module + '.time.sleep'):
            self.assertEqual(device.forward_port(9008),12345)
        self.assertEqual([call.args[0][3:] for call in cli.call_args_list],
                         [['forward','--list'],['forward','tcp:0','tcp:9008'],['forward','--list']])
        self.assertTrue(events[-1]['created_mapping'])

    def test_pre_rpc_forward_guard_does_not_accept_mapping_for_wrong_device(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from adbutils import AdbError
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        device=SimpleNamespace(forward_port=Mock(side_effect=AdbError()))
        install_rpc_forward_guard(device,'emulator-5580',[])
        responses=[SimpleNamespace(returncode=0,stdout=b'',stderr=b''),
                   SimpleNamespace(returncode=0,stdout=b'12345\n',stderr=b''),
                   SimpleNamespace(returncode=0,stdout=b'emulator-5564 tcp:12345 tcp:9008\n',stderr=b'')]
        with patch(module+'.subprocess.run',side_effect=responses),patch(module+'.time.sleep'):
            with self.assertRaisesRegex(RuntimeError,'not independently verified'):device.forward_port(9008)

    def test_pre_rpc_forward_guard_does_not_retry_nonempty_or_other_remote_errors(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from adbutils import AdbError
        for remote in (9008,9999):
            device=SimpleNamespace(forward_port=Mock(side_effect=AdbError('permission denied')))
            original=device.forward_port
            install_rpc_forward_guard(device,'emulator-5580',[])
            with self.assertRaisesRegex(AdbError,'permission denied'):device.forward_port(remote)
            original.assert_called_once_with(remote)

    def test_pre_rpc_recovery_never_resends_the_actual_http_candidate(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from adbutils import AdbError
        from uiautomator2.core import _http_request
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        device=SimpleNamespace(forward_port=Mock(side_effect=AdbError()))
        install_rpc_forward_guard(device,'emulator-5580',[])
        listed=SimpleNamespace(returncode=0,stdout=b'emulator-5580 tcp:12345 tcp:9008\n',stderr=b'')
        response=Mock(content=b'{"result":true}',status_code=200)
        payload={'method':'click','params':[10,20]}
        with patch(module+'.subprocess.run',return_value=listed),patch(module+'.time.sleep'),patch('uiautomator2.core.requests.request',return_value=response) as http:
            result=_http_request(device,'POST','/jsonrpc/0',data=payload)
        self.assertEqual(result.content,b'{"result":true}')
        http.assert_called_once()
        self.assertEqual(http.call_args.kwargs['json'],payload)

    def test_pre_rpc_guard_never_touches_other_experiment_device(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        device=SimpleNamespace(forward_port=Mock())
        with self.assertRaisesRegex(RuntimeError,'dedicated batch device'):
            install_rpc_forward_guard(device,'emulator-5564',[])
        device.forward_port.assert_not_called()

    def test_phone_partition_is_specific_to_phone(self):
        case = self.config["cases"][1]
        case["input"]["predicate"] = "masked_phone_repeated_symbols_v2"
        candidate = dict(case["hidden_smoke_fixtures"]["control"])
        candidate["mo.must.maskededittextbug:id/first_name"] = "Test,.-"
        self.assertEqual(candidate_partition(case, candidate), "CONTROL")
        candidate["mo.must.maskededittextbug:id/phone"] = ","
        self.assertEqual(candidate_partition(case, candidate), "OUTSIDE")
        candidate["mo.must.maskededittextbug:id/phone"] = ",.,"
        self.assertEqual(candidate_partition(case, candidate), "TARGET")

    def test_amount_partition_accepts_auto_grouping_inputs(self):
        case = self.config["cases"][0]
        case["input"]["predicate"] = "debitum_edit_grouped_amount_v2"
        candidate = dict(case["hidden_smoke_fixtures"]["control"])
        for value in ("1000", "1 000", "1\u00a0000", "1\u202f000", "1000,50"):
            candidate["org.ebur.debitum:id/edit_amount"] = value
            self.assertEqual(candidate_partition(case, candidate), "TARGET")
        for value in ("12", "999"):
            candidate["org.ebur.debitum:id/edit_amount"] = value
            self.assertEqual(candidate_partition(case, candidate), "CONTROL")

    def test_zero_alias_does_not_discard_actual_completion_tokens(self):
        self.assertEqual(usage_tokens({"input_tokens": 26, "output_tokens": 0,
                                      "prompt_tokens": 26, "completion_tokens": 14}), (26, 14))
        with self.assertRaises(ValueError):
            usage_tokens({"input_tokens": None, "output_tokens": 2})

    def test_android_ready_waits_past_service_transport_failure(self):
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        responses = ["1\n", RuntimeError("Broken pipe (32)"),
                     "1\n", "Service activity: found\n", "package:android\n"]
        with patch(module + ".command", side_effect=responses) as command, patch(module + ".time.sleep"):
            android_ready("emulator-5580", timeout=5)
        self.assertEqual(command.call_count, 5)

    def test_android_ready_does_not_accept_adb_without_completed_boot(self):
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        responses = ["0\n", "Service activity: found\n", "package:android\n",
                     "1\n", "Service activity: found\n", "package:android\n"]
        with patch(module + ".command", side_effect=responses) as command, patch(module + ".time.sleep"):
            android_ready("emulator-5580", timeout=5)
        self.assertEqual(command.call_count, 6)

    @staticmethod
    def logcat_snapshot_transport(raw, *, failed_raw_transfers=(), launch_code=0,
                                 metadata_failures=0, logcat_exit=0, empty_metadata=False):
        """Model one committed guest snapshot, never a new capture on retry."""
        from types import SimpleNamespace
        import re
        calls, counters = [], {'raw': 0, 'stderr': 0, 'metadata': 0}
        remote = None
        def run(argv, **kwargs):
            nonlocal remote
            calls.append(argv)
            out, code = b'', 0
            if argv[3] == 'get-state':
                out = b'device\n'
            elif argv[3] == 'shell' and argv[4].startswith('umask 077; logcat '):
                remote = re.search(r'(/data/local/tmp/spec_logcat_[0-9a-f]+)\.raw', argv[4])[1]
                code = launch_code
            elif argv[3] == 'shell':
                counters['metadata'] += 1
                if empty_metadata or counters['metadata'] <= metadata_failures:
                    return SimpleNamespace(returncode=255, stdout=b'partial metadata', stderr=b'')
                token = remote.rsplit('_', 1)[-1]
                out = (f'{logcat_exit}\n{len(raw)} {remote}.raw\n'
                       f'{hashlib.sha256(raw).hexdigest()}  {remote}.raw\n'
                       f'0 {remote}.stderr\n{hashlib.sha256(b"").hexdigest()}  {remote}.stderr\n'
                       f'SPEC_LOGCAT_END_{token}\n').encode()
            elif argv[3] in ('pull', 'exec-out'):
                source = argv[4] if argv[3] == 'pull' else argv[5]
                label = 'raw' if source.endswith('.raw') else 'stderr'
                counters[label] += 1
                payload = raw if label == 'raw' else b''
                if label == 'raw' and counters[label] in failed_raw_transfers:
                    payload, code = raw[:3], 255
                if argv[3] == 'pull':
                    Path(argv[5]).write_bytes(payload)
                else:
                    out = payload
            else:
                raise AssertionError(f'unexpected transport: {argv}')
            return SimpleNamespace(returncode=code, stdout=out, stderr=b'')
        return run, calls, counters

    def test_logcat_prelaunch_offline_read_recovers_without_repeating_capture(self):
        from types import SimpleNamespace
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        transport, calls, _ = self.logcat_snapshot_transport(b'original evidence')
        reads = []
        def run(argv, **kwargs):
            if argv[3] == 'get-state':
                reads.append(argv)
                if len(reads) == 1:
                    return SimpleNamespace(returncode=1, stdout=b'', stderr=b'adb: device offline\n')
            return transport(argv, **kwargs)
        with tempfile.TemporaryDirectory() as directory, patch(module+'.subprocess.run',side_effect=run), patch(module+'.time.sleep'):
            self.assertEqual(capture_logcat('emulator-5582',Path(directory)), 'original evidence')
            self.assertEqual((Path(directory)/'logcat.prelaunch_transport_01.stderr.bin').read_bytes(), b'adb: device offline\n')
        self.assertEqual(len(reads),2)
        self.assertEqual(sum(a[3]=='shell' and 'logcat -d ' in a[4] for a in calls),1)

    def test_logcat_prelaunch_persistent_offline_never_launches_logcat(self):
        from types import SimpleNamespace
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        response=SimpleNamespace(returncode=1,stdout=b'',stderr=b'adb: device offline\n')
        with tempfile.TemporaryDirectory() as directory, patch(module+'.subprocess.run',return_value=response) as run, patch(module+'.time.sleep'):
            with self.assertRaisesRegex(RuntimeError,'offline after 3 reads'):
                capture_logcat('emulator-5582',Path(directory))
            self.assertEqual(run.call_count,3)
            self.assertTrue(all(c.args[0][3]=='get-state' for c in run.call_args_list))
            self.assertFalse((Path(directory)/'logcat.snapshot_start.json').exists())

    def test_logcat_prelaunch_error_prefix_offline_recovers_with_one_capture(self):
        from types import SimpleNamespace
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        transport, calls, _ = self.logcat_snapshot_transport(b'original evidence')
        reads = []
        def run(argv, **kwargs):
            if argv[3] == 'get-state':
                reads.append(argv)
                if len(reads) == 1:
                    return SimpleNamespace(returncode=1, stdout=b'', stderr=b'error: device offline\n')
            return transport(argv, **kwargs)
        with tempfile.TemporaryDirectory() as directory, patch(module+'.subprocess.run',side_effect=run), patch(module+'.time.sleep'):
            self.assertEqual(capture_logcat('emulator-5580',Path(directory)), 'original evidence')
            self.assertEqual((Path(directory)/'logcat.prelaunch_transport_01.stderr.bin').read_bytes(), b'error: device offline\n')
        self.assertEqual(len(reads),2)
        self.assertEqual(sum(a[3]=='shell' and 'logcat -d ' in a[4] for a in calls),1)

    def test_logcat_prelaunch_persistent_error_prefix_offline_is_three_reads(self):
        from types import SimpleNamespace
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        response=SimpleNamespace(returncode=1,stdout=b'',stderr=b'error: device offline\n')
        with tempfile.TemporaryDirectory() as directory, patch(module+'.subprocess.run',return_value=response) as run, patch(module+'.time.sleep'):
            with self.assertRaisesRegex(RuntimeError,'offline after 3 reads'):
                capture_logcat('emulator-5580',Path(directory))
            self.assertEqual(run.call_count,3)
            self.assertTrue(all(c.args[0][3]=='get-state' for c in run.call_args_list))
            self.assertFalse((Path(directory)/'logcat.snapshot_start.json').exists())

    def test_logcat_prelaunch_offline_prefixes_share_one_three_read_budget(self):
        from types import SimpleNamespace
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        responses=[SimpleNamespace(returncode=1,stdout=b'',stderr=message)
                   for message in (b'error: device offline\n',b'adb: device offline\n')]
        responses.append(SimpleNamespace(returncode=0,stdout=b'device\n',stderr=b''))
        with tempfile.TemporaryDirectory() as directory, patch(module+'.subprocess.run',side_effect=responses) as run, patch(module+'.time.sleep'):
            wait_logcat_transport_ready('emulator-5580',Path(directory))
            self.assertEqual(run.call_count,3)
            self.assertTrue(all(c.args[0]==['adb','-s','emulator-5580','get-state'] for c in run.call_args_list))
            self.assertFalse((Path(directory)/'logcat.snapshot_start.json').exists())

    def test_logcat_prelaunch_error_prefix_unauthorized_not_retried(self):
        from types import SimpleNamespace
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        response=SimpleNamespace(returncode=1,stdout=b'',stderr=b'error: device unauthorized\n')
        with tempfile.TemporaryDirectory() as directory, patch(module+'.subprocess.run',return_value=response) as run:
            with self.assertRaisesRegex(RuntimeError,'unrecognized'):
                wait_logcat_transport_ready('emulator-5580',Path(directory))
        self.assertEqual(run.call_count,1)

    def test_logcat_prelaunch_offline_substring_is_not_an_exact_alias(self):
        from types import SimpleNamespace
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        response=SimpleNamespace(returncode=1,stdout=b'',stderr=b'error: device offline; unknown failure\n')
        with tempfile.TemporaryDirectory() as directory, patch(module+'.subprocess.run',return_value=response) as run:
            with self.assertRaisesRegex(RuntimeError,'unrecognized'):
                wait_logcat_transport_ready('emulator-5580',Path(directory))
        self.assertEqual(run.call_count,1)

    def test_logcat_prelaunch_offline_then_exact_serial_absent_recovers_once(self):
        from types import SimpleNamespace
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        transport, calls, _ = self.logcat_snapshot_transport(b'original evidence')
        reads = []
        def run(argv, **kwargs):
            if argv[3] == 'get-state':
                reads.append(argv)
                if len(reads) <= 2:
                    err = b'error: device offline\n' if len(reads) == 1 else b"error: device 'emulator-5582' not found\n"
                    return SimpleNamespace(returncode=1, stdout=b'', stderr=err)
            return transport(argv, **kwargs)
        with tempfile.TemporaryDirectory() as directory, patch(module+'.subprocess.run',side_effect=run), patch(module+'.time.sleep'):
            self.assertEqual(capture_logcat('emulator-5582',Path(directory)), 'original evidence')
            self.assertEqual((Path(directory)/'logcat.prelaunch_transport_02.stderr.bin').read_bytes(), b"error: device 'emulator-5582' not found\n")
        self.assertEqual(len(reads),3)
        self.assertEqual(sum(a[3]=='shell' and 'logcat -d ' in a[4] for a in calls),1)

    def test_logcat_prelaunch_persistent_exact_serial_absent_is_three_reads(self):
        from types import SimpleNamespace
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        response=SimpleNamespace(returncode=1,stdout=b'',stderr=b"error: device 'emulator-5582' not found\n")
        with tempfile.TemporaryDirectory() as directory, patch(module+'.subprocess.run',return_value=response) as run, patch(module+'.time.sleep'):
            with self.assertRaisesRegex(RuntimeError,'after 3 reads'):
                capture_logcat('emulator-5582',Path(directory))
            self.assertEqual(run.call_count,3)
            self.assertTrue(all(c.args[0]==['adb','-s','emulator-5582','get-state'] for c in run.call_args_list))
            self.assertFalse((Path(directory)/'logcat.snapshot_start.json').exists())

    def test_logcat_prelaunch_absent_offline_timeout_share_three_reads(self):
        import subprocess
        from types import SimpleNamespace
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        responses=[SimpleNamespace(returncode=1,stdout=b'',stderr=b"error: device 'emulator-5582' not found\n"),
                   SimpleNamespace(returncode=1,stdout=b'',stderr=b'error: device offline\n'),
                   subprocess.TimeoutExpired(['adb'],60)]
        with tempfile.TemporaryDirectory() as directory, patch(module+'.subprocess.run',side_effect=responses) as run, patch(module+'.time.sleep'):
            with self.assertRaisesRegex(RuntimeError,'after 3 reads'):
                capture_logcat('emulator-5582',Path(directory))
            self.assertEqual(run.call_count,3)
            self.assertFalse((Path(directory)/'logcat.snapshot_start.json').exists())

    def test_logcat_prelaunch_different_serial_absent_is_not_retried(self):
        from types import SimpleNamespace
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        response=SimpleNamespace(returncode=1,stdout=b'',stderr=b"error: device 'emulator-5580' not found\n")
        with tempfile.TemporaryDirectory() as directory, patch(module+'.subprocess.run',return_value=response) as run:
            with self.assertRaisesRegex(RuntimeError,'unrecognized'):
                wait_logcat_transport_ready('emulator-5582',Path(directory))
        self.assertEqual(run.call_count,1)

    def test_logcat_prelaunch_absent_substring_is_not_an_exact_match(self):
        from types import SimpleNamespace
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        response=SimpleNamespace(returncode=1,stdout=b'',stderr=b"error: device 'emulator-5582' not found; unknown failure\n")
        with tempfile.TemporaryDirectory() as directory, patch(module+'.subprocess.run',return_value=response) as run:
            with self.assertRaisesRegex(RuntimeError,'unrecognized'):
                wait_logcat_transport_ready('emulator-5582',Path(directory))
        self.assertEqual(run.call_count,1)

    def test_logcat_prelaunch_semantic_error_is_not_retried(self):
        from types import SimpleNamespace
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        response=SimpleNamespace(returncode=1,stdout=b'',stderr=b'adb: device unauthorized\n')
        with tempfile.TemporaryDirectory() as directory, patch(module+'.subprocess.run',return_value=response) as run:
            with self.assertRaisesRegex(RuntimeError,'unrecognized'):
                wait_logcat_transport_ready('emulator-5582',Path(directory))
        self.assertEqual(run.call_count,1)

    def test_logcat_prelaunch_timeout_reads_bounded_no_capture(self):
        import subprocess
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        with tempfile.TemporaryDirectory() as directory, patch(module+'.subprocess.run',side_effect=subprocess.TimeoutExpired(['adb'],60)) as run, patch(module+'.time.sleep'):
            with self.assertRaisesRegex(RuntimeError,'after 3 reads'):
                capture_logcat('emulator-5582',Path(directory))
            self.assertEqual(run.call_count,3)
            self.assertTrue(all(c.args[0][3]=='get-state' for c in run.call_args_list))
            self.assertFalse((Path(directory)/'logcat.snapshot_start.json').exists())

    def test_logcat_prelaunch_rejects_other_device(self):
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        with tempfile.TemporaryDirectory() as directory, patch(module+'.subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError,'dedicated batch device'):
                wait_logcat_transport_ready('emulator-5564',Path(directory))
        run.assert_not_called()

    def test_baseline_foreground_waits_only_reads_after_original_launch(self):
        from unittest.mock import Mock
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        target={'package':'example.app','activity':'.Main','pid':123}
        device=Mock();device.app_current.side_effect=[{'package':'launcher','pid':2},target]
        events=[]
        with patch(module+'.time.sleep'):
            self.assertIs(wait_baseline_foreground_ready(device,'example.app',events),target)
        self.assertEqual(device.app_current.call_count,2)
        self.assertEqual(len(device.mock_calls),2)
        self.assertEqual([e['ready'] for e in events],[False,True])
        self.assertTrue(all(e['read_only'] and not e['input_redelivered'] for e in events))

    def test_baseline_foreground_persistent_mismatch_stays_unknown(self):
        from unittest.mock import Mock
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        device=Mock();device.app_current.return_value={'package':'launcher','pid':2}
        events=[]
        with patch(module+'.time.sleep'):
            with self.assertRaisesRegex(RuntimeError,'differs after 3'):
                wait_baseline_foreground_ready(device,'example.app',events)
        self.assertEqual(device.app_current.call_count,3)
        self.assertEqual(len(events),3)
        self.assertTrue(all(e['observed_foreground']=={'package':'launcher','pid':2} for e in events))

    def test_baseline_foreground_read_exception_is_preserved_not_retried(self):
        from unittest.mock import Mock
        device=Mock();device.app_current.side_effect=RuntimeError('semantic read error')
        events=[]
        with self.assertRaisesRegex(RuntimeError,'semantic read error'):
            wait_baseline_foreground_ready(device,'example.app',events)
        self.assertEqual(device.app_current.call_count,1)
        self.assertIn('RuntimeError: semantic read error',events[0]['traceback'])
        self.assertFalse(events[0]['input_redelivered'])

    def test_baseline_foreground_first_expected_read_is_returned_unmodified(self):
        from unittest.mock import Mock
        target={'package':'example.app','activity':'.Exact','pid':456,'extra':'preserved'}
        device=Mock();device.app_current.return_value=target
        events=[]
        self.assertIs(wait_baseline_foreground_ready(device,'example.app',events),target)
        device.app_current.assert_called_once_with()
        self.assertEqual(events[0]['observed_foreground'],target)

    def test_logcat_preserves_modified_utf8_bytes_and_ascii_evidence(self):
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        raw = b'App input: \xc0\x80\xff\nFATAL EXCEPTION: main\njava.lang.IndexOutOfBoundsException\n'
        transport, calls, _ = self.logcat_snapshot_transport(raw)
        with tempfile.TemporaryDirectory() as directory, patch(module + ".subprocess.run", side_effect=transport):
            rendered = capture_logcat("emulator-5582", Path(directory))
            self.assertEqual((Path(directory) / "logcat.raw.bin").read_bytes(), raw)
            self.assertIn("FATAL EXCEPTION: main", rendered)
            self.assertIn("\\xc0\\x80\\xff", rendered)
            receipt = json.loads((Path(directory) / "logcat_capture_receipt.json").read_text())
            self.assertFalse(receipt["strict_utf8"])
            self.assertEqual(receipt["raw_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertTrue(receipt['remote_hash_and_size_verified'])
            self.assertEqual(receipt['snapshot']['logcat_invocations'], 1)
        self.assertEqual(sum(a[3] == 'shell' and 'logcat -d ' in a[4] for a in calls), 1)

    def test_valid_utf8_logcat_view_is_exact(self):
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        raw = 'ordinary log \U0001f4a3'.encode()
        transport, _, _ = self.logcat_snapshot_transport(raw)
        with tempfile.TemporaryDirectory() as directory, patch(module + ".subprocess.run", side_effect=transport):
            self.assertEqual(capture_logcat("emulator-5580", Path(directory)), raw.decode())

    def test_logcat_uncertain_capture_start_is_not_reissued_when_status_committed(self):
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        transport, calls, _ = self.logcat_snapshot_transport(b'FATAL EXCEPTION: main\n', launch_code=255)
        with tempfile.TemporaryDirectory() as directory, patch(module + '.subprocess.run', side_effect=transport):
            self.assertIn('FATAL EXCEPTION', capture_logcat('emulator-5580', Path(directory)))
            receipt = json.loads((Path(directory) / 'logcat_capture_receipt.json').read_text())
            self.assertEqual(receipt['snapshot']['snapshot_start_transport_exit_code'], 255)
        self.assertEqual(sum(a[3] == 'shell' and 'logcat -d ' in a[4] for a in calls), 1)

    def test_logcat_partial_transfer_is_preserved_and_only_same_file_is_reread(self):
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        raw = b'complete original log evidence\n'
        transport, calls, counts = self.logcat_snapshot_transport(raw, failed_raw_transfers=(1,))
        with tempfile.TemporaryDirectory() as directory, patch(module + '.subprocess.run', side_effect=transport), patch(module + '.time.sleep'):
            self.assertEqual(capture_logcat('emulator-5580', Path(directory)), raw.decode())
            self.assertEqual((Path(directory) / 'logcat.transfer_raw_01.bin').read_bytes(), raw[:3])
            self.assertEqual((Path(directory) / 'logcat.raw.bin').read_bytes(), raw)
            receipt = json.loads((Path(directory) / 'logcat_capture_receipt.json').read_text())
            self.assertEqual(receipt['read_attempts'], 2)
            self.assertFalse(receipt['failed_read_receipts'][0]['input_redelivered'])
        self.assertEqual(counts['raw'], 2)
        self.assertEqual(len({a[4] for a in calls if a[3] == 'pull' and a[4].endswith('.raw')}), 1)
        self.assertEqual(sum(a[3] == 'shell' and 'logcat -d ' in a[4] for a in calls), 1)

    def test_logcat_incomplete_transfers_remain_unknown_after_three(self):
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        transport, calls, counts = self.logcat_snapshot_transport(b'original evidence', failed_raw_transfers=(1,2,3))
        with tempfile.TemporaryDirectory() as directory, patch(module + '.subprocess.run', side_effect=transport), patch(module + '.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'after 3 reads'):
                capture_logcat('emulator-5580', Path(directory))
            self.assertFalse((Path(directory) / 'logcat.txt').exists())
            self.assertTrue((Path(directory) / 'logcat_snapshot_failure.json').exists())
        self.assertEqual(counts['raw'], 3)
        self.assertEqual(sum(a[3] == 'shell' and 'logcat -d ' in a[4] for a in calls), 1)

    def test_logcat_empty_pull_failure_uses_same_file_binary_fallback(self):
        from types import SimpleNamespace
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        raw=b'lossless modified UTF8: \xed\xa0\x80\x00\n'
        transport,calls,counts=self.logcat_snapshot_transport(raw)
        def fail_empty_pull(argv,**kw):
            if argv[3]=='pull' and argv[4].endswith('.raw'):
                calls.append(argv);counts['raw']+=1
                return SimpleNamespace(returncode=1,stdout=b'',stderr=b'')
            return transport(argv,**kw)
        with tempfile.TemporaryDirectory() as directory,patch(module+'.subprocess.run',side_effect=fail_empty_pull),patch(module+'.time.sleep'):
            capture_logcat('emulator-5580',Path(directory))
            self.assertEqual((Path(directory)/'logcat.raw.bin').read_bytes(),raw)
            first=json.loads((Path(directory)/'logcat.transfer_raw_01.verification.json').read_text())
            second=json.loads((Path(directory)/'logcat.transfer_raw_02.verification.json').read_text())
            self.assertFalse(first['file_exists'])
            self.assertEqual(second['transport_method'],'adb-exec-out-cat')
            self.assertTrue(second['verified'])
        pull=next(a for a in calls if a[3]=='pull' and a[4].endswith('.raw'))
        direct=next(a for a in calls if a[3]=='exec-out' and a[5].endswith('.raw'))
        self.assertEqual(pull[4],direct[5])
        self.assertEqual(counts['raw'],2)
        self.assertEqual(sum(a[3]=='shell' and 'logcat -d ' in a[4] for a in calls),1)

    def test_logcat_partial_binary_fallback_keeps_shared_three_read_budget(self):
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        raw=b'complete immutable log evidence'
        transport,calls,counts=self.logcat_snapshot_transport(raw,failed_raw_transfers=(1,2))
        with tempfile.TemporaryDirectory() as directory,patch(module+'.subprocess.run',side_effect=transport),patch(module+'.time.sleep'):
            capture_logcat('emulator-5580',Path(directory))
            self.assertEqual((Path(directory)/'logcat.transfer_raw_02.stdout.bin').read_bytes(),raw[:3])
            self.assertEqual((Path(directory)/'logcat.transfer_raw_02.bin').read_bytes(),raw[:3])
            self.assertEqual((Path(directory)/'logcat.raw.bin').read_bytes(),raw)
            self.assertFalse((Path(directory)/'logcat.transfer_raw_04.json').exists())
        self.assertEqual(counts['raw'],3)
        self.assertEqual(sum(a[3]=='exec-out' and a[5].endswith('.raw') for a in calls),2)

    def test_logcat_short_binary_fallback_exit_zero_is_still_rejected(self):
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        raw=b'original full log evidence'
        transport,calls,counts=self.logcat_snapshot_transport(raw,failed_raw_transfers=(1,))
        def short(argv,**kw):
            response=transport(argv,**kw)
            if argv[3]=='exec-out' and argv[5].endswith('.raw'):
                response.stdout=raw[:3]
            return response
        with tempfile.TemporaryDirectory() as directory,patch(module+'.subprocess.run',side_effect=short),patch(module+'.time.sleep'):
            with self.assertRaisesRegex(RuntimeError,'after 3 reads'):
                capture_logcat('emulator-5580',Path(directory))
            self.assertFalse((Path(directory)/'logcat.raw.bin').exists())
            self.assertFalse((Path(directory)/'logcat.transfer_raw_04.json').exists())
        self.assertEqual(counts['raw'],3)

    def test_logcat_empty_stderr_fallback_requires_real_empty_file_hash(self):
        from types import SimpleNamespace
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        transport,calls,counts=self.logcat_snapshot_transport(b'original evidence')
        def fail_stderr(argv,**kw):
            if argv[3]=='pull' and argv[4].endswith('.stderr'):
                calls.append(argv);counts['stderr']+=1
                return SimpleNamespace(returncode=1,stdout=b'',stderr=b'')
            return transport(argv,**kw)
        with tempfile.TemporaryDirectory() as directory,patch(module+'.subprocess.run',side_effect=fail_stderr),patch(module+'.time.sleep'):
            capture_logcat('emulator-5580',Path(directory))
            self.assertEqual((Path(directory)/'logcat.stderr.bin').read_bytes(),b'')
            receipt=json.loads((Path(directory)/'logcat.transfer_stderr_02.verification.json').read_text())
            self.assertTrue(receipt['verified'])
            self.assertEqual(receipt['sha256'],hashlib.sha256(b'').hexdigest())
        self.assertEqual(counts['stderr'],2)

    def test_logcat_successful_native_pull_does_not_add_fallback_reads(self):
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        transport,calls,counts=self.logcat_snapshot_transport(b'original evidence')
        with tempfile.TemporaryDirectory() as directory,patch(module+'.subprocess.run',side_effect=transport):
            capture_logcat('emulator-5580',Path(directory))
        self.assertFalse(any(a[3]=='exec-out' for a in calls))
        self.assertEqual((counts['raw'],counts['stderr']),(1,1))

    def test_logcat_metadata_partial_read_never_repeats_capture(self):
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        transport, calls, counts = self.logcat_snapshot_transport(b'original evidence', metadata_failures=1)
        with tempfile.TemporaryDirectory() as directory, patch(module + '.subprocess.run', side_effect=transport), patch(module + '.time.sleep'):
            self.assertEqual(capture_logcat('emulator-5580', Path(directory)), 'original evidence')
            self.assertEqual((Path(directory) / 'logcat.metadata_01.stdout.bin').read_bytes(), b'partial metadata')
        self.assertEqual(counts['metadata'], 2)
        self.assertEqual(sum(a[3] == 'shell' and 'logcat -d ' in a[4] for a in calls), 1)

    def test_logcat_missing_completion_status_never_launches_second_capture(self):
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        transport, calls, counts = self.logcat_snapshot_transport(b'original evidence', empty_metadata=True)
        with tempfile.TemporaryDirectory() as directory, patch(module + '.subprocess.run', side_effect=transport), patch(module + '.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'capture NOT repeated'):
                capture_logcat('emulator-5580', Path(directory))
        self.assertEqual(counts['raw'], 0)
        self.assertEqual(counts['metadata'], 3)
        self.assertEqual(sum(a[3] == 'shell' and 'logcat -d ' in a[4] for a in calls), 1)

    def test_logcat_remote_error_is_not_reinterpreted_as_empty_success(self):
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        transport, calls, _ = self.logcat_snapshot_transport(b'partial failure', logcat_exit=1)
        with tempfile.TemporaryDirectory() as directory, patch(module + '.subprocess.run', side_effect=transport):
            with self.assertRaisesRegex(RuntimeError, 'command failed: exit=1'):
                capture_logcat('emulator-5580', Path(directory))
            self.assertFalse((Path(directory) / 'logcat.txt').exists())
            self.assertTrue((Path(directory) / 'logcat.transfer_raw_01.bin').exists())
        self.assertEqual(sum(a[3] == 'shell' and 'logcat -d ' in a[4] for a in calls), 1)

    def test_logcat_does_not_touch_another_experiment_device(self):
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        with tempfile.TemporaryDirectory() as directory, patch(module + '.subprocess.run') as transport:
            with self.assertRaisesRegex(RuntimeError, 'dedicated batch device'):
                capture_logcat('emulator-5564', Path(directory))
            transport.assert_not_called()

    def test_logcat_success_exit_with_wrong_bytes_is_never_accepted(self):
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        transport, calls, counts = self.logcat_snapshot_transport(b'original evidence')
        def corrupt(argv, **kwargs):
            response = transport(argv, **kwargs)
            if argv[3] == 'pull' and argv[4].endswith('.raw'):
                Path(argv[5]).write_bytes(b'x' * len(b'original evidence'))
            elif argv[3] == 'exec-out' and argv[5].endswith('.raw'):
                response.stdout = b'x' * len(b'original evidence')
            return response
        with tempfile.TemporaryDirectory() as directory, patch(module + '.subprocess.run', side_effect=corrupt), patch(module + '.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'after 3 reads'):
                capture_logcat('emulator-5580', Path(directory))
            self.assertFalse((Path(directory) / 'logcat.raw.bin').exists())
            for i in (1,2,3):
                verification=json.loads((Path(directory) / f'logcat.transfer_raw_{i:02d}.verification.json').read_text())
                self.assertFalse(verification['verified'])
                self.assertEqual(verification['exit_code'], 0)
        self.assertEqual(counts['raw'], 3)

    def test_logcat_capture_timeout_checks_committed_snapshot_without_relaunch(self):
        import subprocess
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        transport, calls, _ = self.logcat_snapshot_transport(b'original evidence')
        def timeout(argv, **kwargs):
            response = transport(argv, **kwargs)
            if argv[3] == 'shell' and argv[4].startswith('umask '):
                raise subprocess.TimeoutExpired(argv, 60, output=b'partial acknowledgement')
            return response
        with tempfile.TemporaryDirectory() as directory, patch(module + '.subprocess.run', side_effect=timeout):
            self.assertEqual(capture_logcat('emulator-5580', Path(directory)), 'original evidence')
            self.assertEqual((Path(directory) / 'logcat.snapshot_start.stdout.bin').read_bytes(), b'partial acknowledgement')
            start=json.loads((Path(directory) / 'logcat.snapshot_start.json').read_text())
            self.assertTrue(start['timed_out'])
        self.assertEqual(sum(a[3] == 'shell' and 'logcat -d ' in a[4] for a in calls), 1)

    def test_logcat_snapshot_metadata_from_another_path_is_rejected(self):
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        transport, calls, counts = self.logcat_snapshot_transport(b'original evidence')
        def wrong_identity(argv, **kwargs):
            response = transport(argv, **kwargs)
            if argv[3] == 'shell' and not argv[4].startswith('umask '):
                response.stdout=response.stdout.replace(b'.raw',b'.wrong')
            return response
        with tempfile.TemporaryDirectory() as directory, patch(module + '.subprocess.run', side_effect=wrong_identity), patch(module + '.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'metadata incomplete'):
                capture_logcat('emulator-5580', Path(directory))
        self.assertEqual(counts['raw'], 0)
        self.assertEqual(counts['metadata'], 3)

    @staticmethod
    def malformed_ime_read(command="settings get secure default_input_method; echo X4EXIT:$?"):
        from adbutils import AdbError
        from uiautomator2.exceptions import AdbShellError
        return AdbShellError(AdbError("shell output invalid", command,
                                     "com.github.uiautomator/.AdbKeyboard"))

    def test_ime_guard_retries_read_without_accepting_unacknowledged_value(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        original = Mock(side_effect=[self.malformed_ime_read(), "verified-ime"])
        device, events = SimpleNamespace(current_ime=original), []
        install_input_method_read_guard(device, events)
        with patch("text_input_eval.supplementary.spec_source_backed_repeat.time.sleep") as sleep:
            self.assertEqual(device.current_ime(), "verified-ime")
        self.assertEqual(original.call_count, 2)
        sleep.assert_called_once_with(.2)
        self.assertTrue(events[0]["read_only"])
        self.assertFalse(events[0]["input_redelivered"])

    def test_ime_guard_is_bounded_and_preserves_failure(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from uiautomator2.exceptions import AdbShellError
        original = Mock(side_effect=self.malformed_ime_read())
        device, events = SimpleNamespace(current_ime=original), []
        install_input_method_read_guard(device, events)
        with patch("text_input_eval.supplementary.spec_source_backed_repeat.time.sleep") as sleep:
            with self.assertRaises(AdbShellError):
                device.current_ime()
        self.assertEqual(original.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertFalse(events[-1]["will_retry"])

    def test_ime_guard_does_not_retry_a_write_or_another_command(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from uiautomator2.exceptions import AdbShellError
        for command in ("am broadcast -a ADB_KEYBOARD_INPUT_TEXT; echo X4EXIT:$?",
                        "settings put secure default_input_method ime; echo X4EXIT:$?",
                        "getprop sys.boot_completed; echo X4EXIT:$?"):
            original = Mock(side_effect=self.malformed_ime_read(command))
            device, events = SimpleNamespace(current_ime=original), []
            install_input_method_read_guard(device, events)
            with self.subTest(command=command), self.assertRaises(AdbShellError):
                device.current_ime()
            self.assertEqual(original.call_count, 1)
            self.assertEqual(events, [])

    def test_ime_guard_does_not_retry_an_unrelated_transport_error(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from adbutils import AdbError
        from uiautomator2.exceptions import AdbShellError
        original = Mock(side_effect=AdbShellError(AdbError("broken pipe")))
        device, events = SimpleNamespace(current_ime=original), []
        install_input_method_read_guard(device, events)
        with self.assertRaises(AdbShellError):
            device.current_ime()
        self.assertEqual(original.call_count, 1)
        self.assertEqual(events, [])

    def test_ime_guard_does_not_duplicate_character_delivery(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        device = SimpleNamespace(current_ime=Mock(side_effect=[self.malformed_ime_read(), "ime", "ime"]))
        delivered = []
        def send_keys(value):
            device.current_ime()
            delivered.append(value)
        device.send_keys = send_keys
        install_input_method_read_guard(device, [])
        with patch("text_input_eval.supplementary.spec_source_backed_repeat.time.sleep"):
            for character in "ab":
                device.send_keys(character)
        self.assertEqual(delivered, ["a", "b"])

    def test_ime_guard_is_instance_scoped_and_returns_other_ime_unchanged(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        first = SimpleNamespace(current_ime=Mock(return_value="another-ime"))
        second = SimpleNamespace(current_ime=Mock(return_value="untouched-ime"))
        second_original = second.current_ime
        install_input_method_read_guard(first, [])
        self.assertEqual(first.current_ime(), "another-ime")
        self.assertIs(second.current_ime, second_original)

    def test_real_ime_broadcast_is_sent_once_after_a_read_retry(self):
        import base64
        import uiautomator2 as u2
        from unittest.mock import Mock
        device = object.__new__(u2.Device)
        device.current_ime = Mock(side_effect=[self.malformed_ime_read(),
                                               "com.github.uiautomator/.AdbKeyboard"])
        device._must_broadcast = Mock(return_value=True)
        install_input_method_read_guard(device, [])
        with patch("text_input_eval.supplementary.spec_source_backed_repeat.time.sleep"):
            device._send_keys_with_ime("Ö")
        device._must_broadcast.assert_called_once_with(
            "ADB_KEYBOARD_INPUT_TEXT", {"text": base64.b64encode("Ö".encode()).decode()})

    def test_real_ime_write_error_is_not_retried(self):
        import uiautomator2 as u2
        from unittest.mock import Mock
        from uiautomator2.exceptions import AdbShellError
        device = object.__new__(u2.Device)
        device.current_ime = Mock(return_value="com.github.uiautomator/.AdbKeyboard")
        device._must_broadcast = Mock(side_effect=self.malformed_ime_read(
            "am broadcast -a ADB_KEYBOARD_INPUT_TEXT; echo X4EXIT:$?"))
        install_input_method_read_guard(device, [])
        with self.assertRaises(AdbShellError):
            device._send_keys_with_ime("a")
        self.assertEqual(device._must_broadcast.call_count, 1)

    def test_capture_guard_retries_only_the_read_and_preserves_arguments(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        image = object()
        original = Mock(side_effect=[json.JSONDecodeError("truncated", '{"result":"', 10), image])
        device, events = SimpleNamespace(screenshot=original, send_keys=Mock()), []
        install_capture_read_guard(device, events)
        with patch("text_input_eval.supplementary.spec_source_backed_repeat.time.sleep"):
            self.assertIs(device.screenshot("after.png", format="pillow"), image)
        self.assertEqual(original.call_count, 2)
        self.assertTrue(all(call.args == ("after.png",) and call.kwargs == {"format": "pillow"}
                            for call in original.call_args_list))
        device.send_keys.assert_not_called()
        self.assertTrue(events[0]["read_only"])
        self.assertFalse(events[0]["input_redelivered"])

    def test_capture_guard_is_bounded_and_keeps_unknown_on_persistent_failure(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        original = Mock(side_effect=json.JSONDecodeError("empty response", "", 0))
        device, events = SimpleNamespace(screenshot=original), []
        install_capture_read_guard(device, events)
        with patch("text_input_eval.supplementary.spec_source_backed_repeat.time.sleep") as sleep:
            with self.assertRaises(json.JSONDecodeError):
                device.screenshot()
        self.assertEqual(original.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertFalse(events[-1]["will_retry"])

    def test_capture_guard_does_not_retry_an_unrelated_failure(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        original = Mock(side_effect=RuntimeError("capture unavailable"))
        device, events = SimpleNamespace(screenshot=original), []
        install_capture_read_guard(device, events)
        with self.assertRaisesRegex(RuntimeError, "capture unavailable"):
            device.screenshot()
        original.assert_called_once()
        self.assertEqual(events, [])

    def test_capture_guard_recovers_empty_adb_error_without_redelivering_input(self):
        from adbutils import AdbError
        from types import SimpleNamespace
        from unittest.mock import Mock
        pixels = object()
        original = Mock(side_effect=[AdbError(), pixels])
        device, events = SimpleNamespace(screenshot=original, send_keys=Mock()), []
        install_capture_read_guard(device, events)
        with patch("text_input_eval.supplementary.spec_source_backed_repeat.time.sleep"):
            self.assertIs(device.screenshot("after.png", format="pillow"), pixels)
        self.assertEqual(original.call_count, 2)
        self.assertTrue(all(c.args == ("after.png",) and c.kwargs == {"format": "pillow"}
                            for c in original.call_args_list))
        device.send_keys.assert_not_called()
        self.assertEqual(events[0]["error_type"], "AdbError")
        self.assertEqual(events[0]["error_message"], "")
        self.assertFalse(events[0]["input_redelivered"])

    def test_capture_guard_persistent_adb_error_is_bounded(self):
        from adbutils import AdbError
        from types import SimpleNamespace
        from unittest.mock import Mock
        original = Mock(side_effect=AdbError())
        device, events = SimpleNamespace(screenshot=original), []
        install_capture_read_guard(device, events)
        with patch("text_input_eval.supplementary.spec_source_backed_repeat.time.sleep") as sleep:
            with self.assertRaises(AdbError):
                device.screenshot()
        self.assertEqual(original.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertFalse(events[-1]["will_retry"])

    def test_capture_guard_mixed_read_failures_share_one_budget(self):
        from adbutils import AdbError
        from types import SimpleNamespace
        from unittest.mock import Mock
        pixels = object()
        original = Mock(side_effect=[AdbError(), json.JSONDecodeError("empty", "", 0), pixels])
        device, events = SimpleNamespace(screenshot=original), []
        install_capture_read_guard(device, events)
        with patch("text_input_eval.supplementary.spec_source_backed_repeat.time.sleep"):
            self.assertIs(device.screenshot(display_id=0), pixels)
        self.assertEqual(original.call_count, 3)
        self.assertEqual([e["error_type"] for e in events], ["AdbError", "JSONDecodeError"])

    def test_real_sdk_screenshot_read_recovers_before_one_image_save(self):
        import base64
        import io
        import uiautomator2 as u2
        from adbutils import AdbError
        from PIL import Image
        from types import SimpleNamespace
        from unittest.mock import Mock
        png = io.BytesIO()
        Image.new("RGB", (2, 2), (12, 34, 56)).save(png, format="PNG")
        rpc = Mock(side_effect=[AdbError(), base64.b64encode(png.getvalue()).decode()])
        device = SimpleNamespace(jsonrpc=SimpleNamespace(takeScreenshot=rpc), send_keys=Mock())
        device.screenshot = lambda *a, **kw: u2.Device.screenshot(device, *a, **kw)
        events = []
        install_capture_read_guard(device, events)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "after.png"
            with patch("text_input_eval.supplementary.spec_source_backed_repeat.time.sleep"):
                device.screenshot(str(output))
            with Image.open(output) as pixels:
                self.assertEqual(pixels.getpixel((0, 0)), (12, 34, 56))
        self.assertEqual(rpc.call_count, 2)
        device.send_keys.assert_not_called()
        self.assertEqual(len(events), 1)

    def test_cost_amendment_preserves_all_non_cost_protocol_values(self):
        old = copy.deepcopy(self.config)
        old["extension"] = {"cumulative_cost_cap_usd": 20., "max_new_paid_requests": 720}
        old["budget"]["max_total_cost_usd"] = 20.
        new = copy.deepcopy(old)
        new["extension"]["cumulative_cost_cap_usd"] = 30.
        new["budget"]["max_total_cost_usd"] = 30.
        amendment = recovery_protocol_cost_amendment([old] * 5, [new] * 5)
        self.assertEqual(amendment["new_cost_cap_usd"], 30.)
        self.assertIsNone(recovery_protocol_cost_amendment([old] * 5, [old] * 5))
        for path, value in ((["budget", "turns_per_trajectory"], 9),
                            (["model", "id"], "different-model"),
                            (["extension", "max_new_paid_requests"], 900)):
            changed = copy.deepcopy(new)
            changed[path[0]][path[1]] = value
            with self.assertRaisesRegex(RuntimeError, "only increase"):
                recovery_protocol_cost_amendment([old] * 5, [changed] * 5)

    def test_direct_screenshot_fallback_uses_one_read_and_real_pixels(self):
        from adbutils import AdbError
        from io import BytesIO
        from PIL import Image
        from types import SimpleNamespace
        from unittest.mock import Mock
        png = BytesIO()
        Image.new("RGB", (2, 2), (12, 34, 56)).save(png, format="PNG")
        original = Mock(side_effect=AdbError())
        device, events = SimpleNamespace(screenshot=original, send_keys=Mock()), []
        install_capture_read_guard(device, events, serial="emulator-5580")
        response = SimpleNamespace(returncode=0, stdout=png.getvalue(), stderr=b"")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "after.png"
            with patch("text_input_eval.supplementary.spec_source_backed_repeat.time.sleep"), \
                 patch("text_input_eval.supplementary.spec_source_backed_repeat.subprocess.run", return_value=response) as read:
                self.assertIsNone(device.screenshot(str(output)))
                self.assertEqual(read.call_args.args[0], ["adb", "-s", "emulator-5580", "exec-out", "screencap", "-p"])
                read.assert_called_once()
            with Image.open(output) as pixels:
                self.assertEqual(pixels.getpixel((0, 0)), (12, 34, 56))
        self.assertEqual(original.call_count, 3)
        device.send_keys.assert_not_called()
        self.assertEqual(events[-1]["direct_capture_attempts"], 1)

    def test_direct_screenshot_fallback_never_accepts_empty_or_synthetic_pixels(self):
        from adbutils import AdbError
        from types import SimpleNamespace
        from unittest.mock import Mock
        for exit_code, data in ((0, b""), (1, b"error"), (0, b"not a PNG")):
            device = SimpleNamespace(screenshot=Mock(side_effect=AdbError()))
            install_capture_read_guard(device, [], serial="emulator-5580")
            response = SimpleNamespace(returncode=exit_code, stdout=data, stderr=b"")
            with patch("text_input_eval.supplementary.spec_source_backed_repeat.time.sleep"), \
                 patch("text_input_eval.supplementary.spec_source_backed_repeat.subprocess.run", return_value=response) as read:
                with self.assertRaisesRegex(RuntimeError, "direct screenshot read failed"):
                    device.screenshot()
                read.assert_called_once()

    def test_direct_screenshot_fallback_does_not_change_multidisplay_or_opencv_api(self):
        from adbutils import AdbError
        from types import SimpleNamespace
        from unittest.mock import Mock
        for kwargs in ({"format": "opencv"}, {"display_id": 2}):
            device = SimpleNamespace(screenshot=Mock(side_effect=AdbError()))
            install_capture_read_guard(device, [], serial="emulator-5580")
            with patch("text_input_eval.supplementary.spec_source_backed_repeat.time.sleep"), \
                 patch("text_input_eval.supplementary.spec_source_backed_repeat.subprocess.run") as read:
                with self.assertRaises(AdbError):
                    device.screenshot(**kwargs)
                read.assert_not_called()

    def test_direct_screenshot_transient_device_loss_retries_only_pixels(self):
        from adbutils import AdbError
        from io import BytesIO
        from PIL import Image
        from types import SimpleNamespace
        from unittest.mock import Mock
        png = BytesIO(); Image.new('RGB', (2, 2), (1, 2, 3)).save(png, format='PNG')
        device = SimpleNamespace(screenshot=Mock(side_effect=AdbError()), send_keys=Mock())
        events = []; install_capture_read_guard(device, events, serial='emulator-5580')
        responses = [SimpleNamespace(returncode=255, stdout=b'', stderr=b"error: device 'emulator-5580' not found"),
                     SimpleNamespace(returncode=0, stdout=png.getvalue(), stderr=b'')]
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        with patch(module + '.subprocess.run', side_effect=responses) as reads, patch(module + '.time.sleep'):
            picture = device.screenshot()
            self.assertEqual(picture.getpixel((0, 0)), (1, 2, 3))
            self.assertEqual(reads.call_count, 2)
        device.send_keys.assert_not_called()
        self.assertEqual(events[-1]['direct_capture_attempts'], 2)

    def test_direct_screenshot_persistent_transport_failure_is_bounded(self):
        from adbutils import AdbError
        from types import SimpleNamespace
        from unittest.mock import Mock
        device = SimpleNamespace(screenshot=Mock(side_effect=AdbError()), send_keys=Mock())
        install_capture_read_guard(device, [], serial='emulator-5580')
        module = 'text_input_eval.supplementary.spec_source_backed_repeat'
        response = SimpleNamespace(returncode=255, stdout=b'', stderr=b'device offline')
        with patch(module + '.subprocess.run', return_value=response) as reads, patch(module + '.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'direct screenshot read failed'):
                device.screenshot()
            self.assertEqual(reads.call_count, 3)
        device.send_keys.assert_not_called()

    @staticmethod
    def screenshot_http_failure(cause=None):
        from uiautomator2.exceptions import HTTPError
        error = HTTPError('HTTP screenshot read failed')
        error.__cause__ = cause
        return error

    def test_screenshot_http_disconnect_retries_only_pixels_with_exact_arguments(self):
        from requests.exceptions import ConnectionError
        from types import SimpleNamespace
        from unittest.mock import Mock
        pixels = object()
        original = Mock(side_effect=[self.screenshot_http_failure(ConnectionError('RemoteDisconnected')), pixels])
        device, events = SimpleNamespace(screenshot=original, send_keys=Mock()), []
        install_capture_read_guard(device, events)
        with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):
            self.assertIs(device.screenshot('after.png', format='pillow'), pixels)
        self.assertEqual(original.call_count, 2)
        self.assertTrue(all(c.args == ('after.png',) and c.kwargs == {'format':'pillow'} for c in original.call_args_list))
        self.assertEqual(events[0]['transport_cause_type'], 'ConnectionError')
        self.assertFalse(events[0]['input_redelivered'])
        device.send_keys.assert_not_called()

    def test_screenshot_http_timeout_without_fallback_remains_unknown_after_three(self):
        from requests.exceptions import Timeout
        from uiautomator2.exceptions import HTTPError
        from types import SimpleNamespace
        from unittest.mock import Mock
        original = Mock(side_effect=self.screenshot_http_failure(Timeout('read timeout')))
        device, events = SimpleNamespace(screenshot=original, send_keys=Mock()), []
        install_capture_read_guard(device, events)
        with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):
            with self.assertRaises(HTTPError):device.screenshot()
        self.assertEqual(original.call_count, 3)
        self.assertEqual(len(events), 3)
        self.assertFalse(events[-1]['will_retry'])
        device.send_keys.assert_not_called()

    def test_screenshot_http_semantic_or_unclassified_failure_is_not_retried(self):
        from requests.exceptions import HTTPError as SemanticHTTPError
        from uiautomator2.exceptions import HTTPError
        from types import SimpleNamespace
        from unittest.mock import Mock
        for cause in (None, SemanticHTTPError('403 forbidden'), ValueError('bad response')):
            original = Mock(side_effect=self.screenshot_http_failure(cause))
            device, events = SimpleNamespace(screenshot=original), []
            install_capture_read_guard(device, events)
            with self.assertRaises(HTTPError):device.screenshot()
            original.assert_called_once()
            self.assertEqual(events, [])

    def test_screenshot_http_failures_use_same_three_read_budget_then_real_png_fallback(self):
        from io import BytesIO
        from PIL import Image
        from requests.exceptions import ConnectionError
        from types import SimpleNamespace
        from unittest.mock import Mock
        buffer=BytesIO();Image.new('RGB',(2,2),(7,8,9)).save(buffer,format='PNG')
        original=Mock(side_effect=self.screenshot_http_failure(ConnectionError('RemoteDisconnected')))
        device,events=SimpleNamespace(screenshot=original,send_keys=Mock()),[]
        install_capture_read_guard(device,events,serial='emulator-5580')
        response=SimpleNamespace(returncode=0,stdout=buffer.getvalue(),stderr=b'')
        module='text_input_eval.supplementary.spec_source_backed_repeat'
        with patch(module+'.subprocess.run',return_value=response) as direct,patch(module+'.time.sleep'):
            picture=device.screenshot()
        self.assertEqual(picture.getpixel((0,0)),(7,8,9))
        self.assertEqual(original.call_count,3)
        direct.assert_called_once()
        self.assertEqual(direct.call_args.args[0],['adb','-s','emulator-5580','exec-out','screencap','-p'])
        self.assertEqual(events[-1]['event'],'screenshot_direct_adb_fallback')
        device.send_keys.assert_not_called()

    def test_screenshot_mixed_http_json_adb_failures_do_not_gain_extra_sdk_attempts(self):
        from adbutils import AdbError
        from requests.exceptions import ConnectionError
        from types import SimpleNamespace
        from unittest.mock import Mock
        original=Mock(side_effect=[json.JSONDecodeError('truncated','{',1),
                                  self.screenshot_http_failure(ConnectionError('closed')),AdbError()])
        device,events=SimpleNamespace(screenshot=original,send_keys=Mock()),[]
        install_capture_read_guard(device,events)
        with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):
            with self.assertRaises(AdbError):device.screenshot()
        self.assertEqual(original.call_count,3)
        self.assertEqual([e['error_type'] for e in events],['JSONDecodeError','HTTPError','AdbError'])
        device.send_keys.assert_not_called()

    def test_hierarchy_http_disconnect_retries_exact_read_arguments_only(self):
        from requests.exceptions import ConnectionError
        from types import SimpleNamespace
        from unittest.mock import Mock
        xml = '<hierarchy><node text="real" /></hierarchy>'
        original = Mock(side_effect=[self.screenshot_http_failure(ConnectionError('closed')), xml])
        device, events = SimpleNamespace(dump_hierarchy=original, send_keys=Mock()), []
        install_hierarchy_read_guard(device, events)
        with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):
            self.assertIs(device.dump_hierarchy(compressed=True, max_depth=40), xml)
        self.assertEqual(original.call_count, 2)
        self.assertTrue(all(c.kwargs == {'compressed': True, 'max_depth': 40} for c in original.call_args_list))
        self.assertEqual(events[0]['transport_cause_type'], 'ConnectionError')
        self.assertFalse(events[0]['input_redelivered'])
        device.send_keys.assert_not_called()

    def test_hierarchy_persistent_transport_failure_stops_after_three_without_fabrication(self):
        from requests.exceptions import Timeout
        from types import SimpleNamespace
        from unittest.mock import Mock
        from uiautomator2.exceptions import HTTPError
        original = Mock(side_effect=self.screenshot_http_failure(Timeout('read timeout')))
        device, events = SimpleNamespace(dump_hierarchy=original, send_keys=Mock()), []
        install_hierarchy_read_guard(device, events)
        with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):
            with self.assertRaises(HTTPError):
                device.dump_hierarchy()
        self.assertEqual(original.call_count, 3)
        self.assertEqual(len(events), 3)
        self.assertFalse(events[-1]['will_retry'])
        device.send_keys.assert_not_called()

    def test_hierarchy_semantic_or_unclassified_http_error_is_not_retried(self):
        from requests.exceptions import HTTPError as SemanticHTTPError
        from types import SimpleNamespace
        from unittest.mock import Mock
        from uiautomator2.exceptions import HTTPError
        for cause in (None, SemanticHTTPError('403 forbidden'), ValueError('bad response')):
            original = Mock(side_effect=self.screenshot_http_failure(cause))
            device, events = SimpleNamespace(dump_hierarchy=original), []
            install_hierarchy_read_guard(device, events)
            with self.assertRaises(HTTPError):
                device.dump_hierarchy()
            original.assert_called_once()
            self.assertEqual(events, [])

    def test_hierarchy_guard_installation_is_idempotent_and_does_not_gain_extra_reads(self):
        from requests.exceptions import ConnectionError
        from types import SimpleNamespace
        from unittest.mock import Mock
        from uiautomator2.exceptions import HTTPError
        original = Mock(side_effect=self.screenshot_http_failure(ConnectionError('closed')))
        device, events = SimpleNamespace(dump_hierarchy=original), []
        install_hierarchy_read_guard(device, events)
        first = device.dump_hierarchy
        install_hierarchy_read_guard(device, events)
        self.assertIs(device.dump_hierarchy, first)
        with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):
            with self.assertRaises(HTTPError):
                device.dump_hierarchy()
        self.assertEqual(original.call_count, 3)
        self.assertEqual(len(events), 3)

    def test_hierarchy_guard_does_not_wrap_or_retry_uncertain_input(self):
        from requests.exceptions import ConnectionError
        from types import SimpleNamespace
        from unittest.mock import Mock
        from uiautomator2.exceptions import HTTPError
        click = Mock(side_effect=self.screenshot_http_failure(ConnectionError('write unacknowledged')))
        original = Mock(return_value='<hierarchy />')
        device = SimpleNamespace(dump_hierarchy=original, click=click)
        install_hierarchy_read_guard(device, [])
        self.assertEqual(device.dump_hierarchy(), '<hierarchy />')
        with self.assertRaises(HTTPError):
            device.click(1, 2)
        click.assert_called_once_with(1, 2)
        original.assert_called_once()

    def test_foreground_read_guard_retries_only_empty_transport_reads(self):
        from adbutils import AdbError
        from types import SimpleNamespace
        from unittest.mock import Mock
        info = {"package": "app", "activity": "MainActivity", "pid": 123}
        original = Mock(side_effect=[RuntimeError(), AdbError(), info])
        device, events = SimpleNamespace(app_current=original, send_keys=Mock()), []
        install_foreground_read_guard(device, events)
        with patch("text_input_eval.supplementary.spec_source_backed_repeat.time.sleep"):
            self.assertIs(device.app_current(), info)
        self.assertEqual(original.call_count, 3)
        device.send_keys.assert_not_called()
        self.assertTrue(all(e["read_only"] and not e["input_redelivered"] for e in events))

    def test_foreground_read_guard_never_fabricates_metadata_on_persistent_failure(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        original = Mock(side_effect=RuntimeError())
        device, events = SimpleNamespace(app_current=original), []
        install_foreground_read_guard(device, events)
        with patch("text_input_eval.supplementary.spec_source_backed_repeat.time.sleep"):
            with self.assertRaises(RuntimeError):
                device.app_current()
        self.assertEqual(original.call_count, 3)
        self.assertFalse(events[-1]["will_retry"])

    def test_foreground_read_guard_does_not_retry_semantic_or_nonempty_errors(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        original = Mock(side_effect=RuntimeError("wrong focused app"))
        device = SimpleNamespace(app_current=original)
        install_foreground_read_guard(device, [])
        with self.assertRaisesRegex(RuntimeError, "wrong focused app"):
            device.app_current()
        original.assert_called_once()

    def test_foreground_fallback_uses_real_bounded_system_read(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        device = SimpleNamespace(app_current=Mock(side_effect=RuntimeError()), send_keys=Mock())
        events = []; install_foreground_read_guard(device, events, serial='emulator-5580')
        actual = {'package': 'real.app', 'activity': '.RealActivity', 'pid': 45}
        with patch('text_input_eval.supplementary.structured_android._adb_foreground_app', return_value=actual) as read, patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):
            self.assertIs(device.app_current(), actual)
            read.assert_called_once_with('emulator-5580')
        device.send_keys.assert_not_called()
        self.assertEqual(events[-1]['event'], 'foreground_direct_adb_fallback')

    def test_foreground_fallback_never_fabricates_when_system_read_fails(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        device = SimpleNamespace(app_current=Mock(side_effect=RuntimeError()))
        install_foreground_read_guard(device, [], serial='emulator-5580')
        with patch('text_input_eval.supplementary.structured_android._adb_foreground_app', side_effect=RuntimeError('no focus')), patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'no focus'):
                device.app_current()

    def test_cost_amendment_rejects_inconsistent_or_lower_caps(self):
        old = copy.deepcopy(self.config)
        old["extension"] = {"cumulative_cost_cap_usd": 20.}
        old["budget"]["max_total_cost_usd"] = 20.
        for cap, budget_cap in ((30., 20.), (10., 10.), (float("inf"), float("inf"))):
            changed = copy.deepcopy(old)
            changed["extension"]["cumulative_cost_cap_usd"] = cap
            changed["budget"]["max_total_cost_usd"] = budget_cap
            with self.assertRaisesRegex(RuntimeError, "only increase"):
                recovery_protocol_cost_amendment([old] * 5, [changed] * 5)

    def test_cost_amendment_without_matching_cli_override_never_calls_provider(self):
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for lane in range(5):
                config = copy.deepcopy(self.config)
                config["extension"] = {"cumulative_cost_cap_usd": 30.}
                config["budget"]["max_total_cost_usd"] = 30.
                for case in config["cases"]:
                    case["execution"]["device_serial"] = f"emulator-{5580 + 2 * lane}"
                path = Path(directory) / f"port_{lane}.json"
                path.write_text(json.dumps(config))
                paths.append(path)
            plan = {"cost_cap_amendment": {"new_cost_cap_usd": 30.}}
            with patch(module + ".validate_config"), \
                 patch(module + ".local_preflight", return_value={"ready_for_live_run": True}), \
                 patch(module + ".audit_recovery", return_value=plan), \
                 patch(module + "._gateway") as gateway:
                with self.assertRaisesRegex(ValueError, "explicit matching CLI"):
                    run_parallel(paths, Path(directory) / "run", recovery_from=Path(directory) / "source")
                gateway.assert_not_called()
                self.assertFalse((Path(directory) / "run").exists())

    def test_cold_recovery_cannot_reuse_native_ram_qualification(self):
        import os
        from unittest.mock import Mock
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        with tempfile.TemporaryDirectory() as temporary:
            paths, configs, source, qualification, baselines = self.recovery_fixture(Path(temporary))
            with patch.dict(os.environ, {"SPEC_SOURCE_BACKED_DISK_ONLY_RESET": "1"}), \
                 patch(module + ".validate_config"), \
                 patch(module + ".local_preflight", return_value={"ready_for_live_run": True}), \
                 patch(module + "._gateway") as gateway:
                with self.assertRaisesRegex(RuntimeError, "qualification missing or stale"):
                    run_parallel(paths, Path(temporary) / "run", source, qualification, 5.)
                gateway.assert_not_called()
                self.assertFalse((Path(temporary) / "run").exists())

    def test_parallel_runner_rejects_duplicate_devices_before_model(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for i in range(5):
                path = Path(directory) / f"config_{i}.json"
                path.write_text(json.dumps(self.config))
                paths.append(path)
            with self.assertRaisesRegex(ValueError, "distinct devices"):
                run_parallel(paths, Path(directory) / "run")
            self.assertFalse((Path(directory) / "run").exists())

    def test_parallel_runner_rejects_different_specifications(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for i in range(5):
                config = copy.deepcopy(self.config)
                config["cases"][0]["execution"]["device_serial"] = f"emulator-{5580+2*i}"
                if i == 4:
                    config["cases"][0]["generation_contract"]["scope"] = "a different requirement"
                path = Path(directory) / f"config_{i}.json"
                path.write_text(json.dumps(config))
                paths.append(path)
            with self.assertRaisesRegex(ValueError, "same frozen"):
                run_parallel(paths, Path(directory) / "run")

    def test_xml_rendering_does_not_change_delivered_value(self):
        from unittest.mock import Mock
        value = "control\u0000\u0001\U0001f4a3"
        observed = "control...."
        driver = Mock()
        events = []
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        with patch(module + ".field_value", side_effect=["", observed]), patch(module + ".time.sleep"):
            set_auxiliary_text(driver, {"package": "app"}, value, events)
        driver.device._send_keys_with_ime.assert_called_once_with(value)
        driver.device._clear_text_with_ime.assert_called_once()
        self.assertTrue(events[0]["xml_rendering_only"])
        self.assertEqual(xml_rendered_text("\t\n\rvalid"), "\t\n\rvalid")

    def test_xml_rendering_still_rejects_arbitrary_truncation(self):
        from unittest.mock import Mock
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        with patch(module + ".field_value", side_effect=["", "cont"] * 3), patch(module + ".time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "outside calibrated"):
                set_auxiliary_text(Mock(), {"package": "app"}, "control\u0000", [])

    def test_terminal_rules_exclude_interrupted_and_infrastructure(self):
        row = {"turn": 1, "candidate_target_partition": "OUTSIDE", "o_spec": "FAIL", "execution_run_status": "ok"}
        self.assertFalse(trajectory_complete([row], 3))
        self.assertTrue(trajectory_complete([{**row, "candidate_target_partition": "TARGET"}], 3))
        self.assertFalse(trajectory_complete([{**row, "candidate_target_partition": "TARGET", "execution_run_status": "infrastructure_error"}], 3))
        self.assertTrue(trajectory_complete([{**row, "turn": t} for t in (1, 2, 3)], 3))

    def test_invalid_date_is_submitted_verbatim_then_app_rejection_is_recorded(self):
        from unittest.mock import Mock
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        driver = Mock()
        driver.device.return_value.info = {"enabled": False}
        driver.device.return_value.exists = True
        events = []
        with patch(module + ".field_value", side_effect=["2026-10-14", "2026-10-14"]), \
             patch(module + ".set_auxiliary_text") as write, patch(module + ".ensure_date_text_mode", return_value={"package": "app"}), patch(module + ".time.sleep"):
            set_date(driver, {"package": "app"}, "2026-13-40", events)
        self.assertEqual(write.call_args.args[2], "2026-13-40")
        self.assertEqual(events[-1]["outcome"], "app_rejected")
        self.assertEqual(driver.action_object.call_args.args[0]["resource-id"], "app:id/cancel_button")

    def test_valid_date_uses_locale_format_without_changing_calendar_value(self):
        from unittest.mock import Mock
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        driver = Mock()
        driver.device.return_value.info = {"enabled": True}
        driver.device.return_value.exists = True
        events = []
        with patch(module + ".field_value", side_effect=["2026-10-14", "2025-01-02"]), \
             patch(module + ".set_auxiliary_text") as write, patch(module + ".ensure_date_text_mode", return_value={"package": "app"}), patch(module + ".time.sleep"):
            set_date(driver, {"package": "app"}, "2025-01-02", events)
        self.assertEqual(write.call_args.args[2], "02.01.2025")
        self.assertEqual(events[-1]["observed_value"], "2025-01-02")
        self.assertEqual(events[-1]["outcome"], "accepted")

    def test_date_letters_rejected_by_app_filter_are_not_transport_failures(self):
        from unittest.mock import Mock
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        driver = Mock()
        driver.device.return_value.info = {"enabled": False}
        driver.device.return_value.exists = True
        events = []
        with patch(module + ".field_value", side_effect=["2026-10-14", "2026-10-14"]), \
             patch(module + ".set_auxiliary_text", side_effect=RuntimeError("auxiliary field readback mismatch outside calibrated XML rendering")) as write, \
             patch(module + ".auxiliary_readback", return_value=(True, "--", False)) as read, patch(module + ".ensure_date_text_mode", return_value={"package": "app"}), patch(module + ".time.sleep"):
            set_date(driver, {"package": "app"}, "not-a-date", events)
        self.assertEqual(write.call_args.args[2], "not-a-date")
        self.assertEqual(read.call_args.args[2], "--")
        self.assertEqual(events[-1]["outcome"], "app_rejected")
        self.assertEqual(events[-2]["event"], "date_app_input_filter_rejection")

    def test_date_navigation_retries_only_a_verified_missed_calendar_click(self):
        from unittest.mock import Mock
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        pane = Mock(exists=False)
        pane.wait.side_effect = [False, True]
        toggle = Mock(info={"checked": False})
        calendar = Mock(exists=True)
        driver = Mock()
        objects = {"app:id/mtrl_picker_text_input_date": pane,
                   "app:id/mtrl_picker_header_toggle": toggle,
                   "app:id/mtrl_calendar_main_pane": calendar}
        driver.device.side_effect = lambda **kw: objects[kw["resourceId"]]
        events = []
        with patch(module + ".time.sleep"):
            selector = ensure_date_text_mode(driver, "app", events)
        self.assertEqual(toggle.click.call_count, 2)
        self.assertEqual([e["text_mode_verified"] for e in events], [False, True])
        self.assertEqual(selector["ancestor-resource-id"], "app:id/mtrl_picker_text_input_date")

    def test_date_navigation_never_retoggles_an_ambiguous_mode(self):
        from unittest.mock import Mock
        pane = Mock(exists=False)
        toggle = Mock(info={"checked": True})
        driver = Mock()
        driver.device.side_effect = [pane, toggle, Mock(exists=True)]
        with self.assertRaisesRegex(RuntimeError, "refusing blind toggle"):
            ensure_date_text_mode(driver, "app", [])
        toggle.click.assert_not_called()

    def test_date_navigation_does_not_toggle_an_existing_text_pane(self):
        from unittest.mock import Mock
        pane, toggle = Mock(exists=True), Mock()
        driver = Mock()
        driver.device.side_effect = [pane, toggle, Mock()]
        ensure_date_text_mode(driver, "app", [])
        toggle.click.assert_not_called()

    def test_empty_material_date_placeholder_is_not_a_value(self):
        from unittest.mock import Mock
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        driver = Mock()
        driver.hierarchy.return_value = '<hierarchy><node resource-id="app:id/mtrl_picker_text_input_date"><node package="app" class="android.widget.EditText" text="Datum, dd.mm.rr" hint="Datum"/><node resource-id="app:id/textinput_placeholder" text="dd.mm.rr"/></node></hierarchy>'
        selector = {"package": "app", "class": "android.widget.EditText", "ancestor-resource-id": "app:id/mtrl_picker_text_input_date"}
        events = []
        with patch(module + ".field_value", side_effect=["14.10.26", "Datum, dd.mm.rr"]), patch(module + ".time.sleep"):
            set_auxiliary_text(driver, selector, "", events)
        self.assertTrue(events[0]["material_placeholder_only"])

    def recovery_fixture(self, root):
        from text_input_eval.supplementary.spec_source_backed_repeat import _sha256_file
        configs, paths = [], []
        for i in range(5):
            config = copy.deepcopy(self.config)
            config["budget"].update(turns_per_trajectory=3, max_experimental_paid_requests=18, max_total_paid_requests=18)
            config["model"].update(id="stub", revision="stub", max_request_cost_usd=.25,
                                    pricing_usd_per_million_tokens={"input": 1., "output": 1.})
            for case in config["cases"]:
                case["execution"]["device_serial"] = f"emulator-{5580 + 2*i}"
                case["input"]["predicate"] = "debitum_edit_grouped_amount_v2" if case["case_id"] == "B002" else "masked_phone_repeated_symbols_v2"
            path = root / f"config_{i}.json"
            _write_new(path, config)
            configs.append(config); paths.append(path)
        source = root / "source"
        _write_new(source / "parallel_protocols.json", configs)
        schedule = _schedule(configs[0])
        _write_new(source / "execution_order.json", {"trajectories": schedule})
        baselines = {"B002": "BASE B002", "B018": "BASE B018"}
        for cell in schedule:
            if cell["case_id"] == "B002" and cell["arm"] == "P0":
                continue
            lane = schedule.index(cell) % 5
            case = next(c for c in configs[lane]["cases"] if c["case_id"] == cell["case_id"])
            previous = []
            for turn in range(1, 3 if cell["case_id"] == "B018" and cell["arm"] == "P0" else 2):
                directory = source / "trajectories" / cell["case_id"] / cell["arm"] / "replicate_001" / f"turn_{turn:02d}"
                candidate = copy.deepcopy(case["hidden_smoke_fixtures"]["control"])
                if turn == 2:
                    candidate[case["input"]["resource_ids"][-1]] = ",.,"
                raw = json.dumps(candidate)
                digest = hashlib.sha256(raw.encode()).hexdigest()
                response = {"run_status": "ok", "response_model": "stub", "raw_response": raw,
                            "raw_response_sha256": digest, "usage": {"input_tokens": 10, "output_tokens": 5}, "elapsed_ms": 1}
                prompt, prompt_audit = render_prompt(case, cell["arm"], previous, baselines[cell["case_id"]])
                unknown = cell["case_id"] == "B002"
                record = {**cell, "turn": turn, "paid_model_request": True, "model_request_origin": "new_provider_call",
                          "model_response_sha256": digest, "request_cost_usd": .000015, "usage": response["usage"],
                          "model_elapsed_ms": 1, "execution_elapsed_ms": 1, "candidate": candidate, "usable_response": True,
                          "candidate_target_partition": candidate_partition(case, candidate), "o_spec": "UNKNOWN" if unknown else "FAIL",
                          "execution_run_status": "infrastructure_error" if unknown else "ok", "public_observations": [],
                          "historical_issue_reproduced": not unknown}
                _write_new(directory / "prompt.txt", prompt)
                _write_new(directory / "prompt_audit.json", prompt_audit)
                _write_new(directory / "model_request_reserved.json", {**cell, "turn": turn, "prompt_sha256": prompt_audit["prompt_sha256"]})
                _write_new(directory / "model_result.json", response)
                _write_new(directory / "attempt_record.json", record)
                _write_new(directory / "execution_result.json", {"candidate_sha256": sha256_json(candidate),
                    "run_status": record["execution_run_status"], "o_spec": record["o_spec"]})
                previous.append(record)
        _write_sha256s(source)
        qualification = root / "qualification"
        for config in configs:
            serial = config["cases"][0]["execution"]["device_serial"]
            _write_new(qualification / serial / "report.json", {"passed": True, "no_model_call": True,
                "executor_sha256": EXECUTOR_SOURCE_SHA256,
                "operational_reset_mode": "native-memory-snapshot"})
        return paths, configs, source, qualification, baselines

    def test_recovery_reuses_six_responses_and_skips_completed_execution(self):
        from unittest.mock import Mock
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths, configs, source, qualification, baselines = self.recovery_fixture(root)
            plan = audit_recovery(source, configs)
            self.assertEqual(plan["reuse_model_responses"], 6)
            self.assertEqual(plan["repair_android_executions"], 2)
            self.assertEqual(plan["completed_trajectories_not_rerun"], 1)
            gateways = []
            def gateway(config):
                obj = Mock()
                def generate(model, prompt):
                    case = config["cases"][0 if "B002" in prompt else 1]
                    candidate = copy.deepcopy(case["hidden_smoke_fixtures"]["control"])
                    key = case["input"]["resource_ids"][0 if case["case_id"] == "B002" else -1]
                    candidate[key] = "1000" if case["case_id"] == "B002" else ",.,"
                    raw = json.dumps(candidate)
                    return {"run_status": "ok", "response_model": "stub", "raw_response": raw,
                        "raw_response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                        "usage": {"input_tokens": 10, "output_tokens": 5}, "elapsed_ms": 1}
                obj.generate.side_effect = generate
                gateways.append(obj)
                return obj
            def materialize(output, *_):
                _write_new(output / "model_qualification_receipt.json", {"request_cost_usd": 0})
            def execute_stub(request):
                return {"run_status": "ok", "o_spec": "FAIL", "unknown_reason": None,
                    "public_observations": [], "elapsed_ms": 1, "historical_issue_reproduced": True}
            output = root / "recovery"
            with patch(module + ".validate_config"), patch(module + ".local_preflight", return_value={"ready_for_live_run": True}), \
                 patch(module + "._frozen_baselines", return_value=baselines), patch(module + "._materialize_frozen_inputs", side_effect=materialize), \
                 patch(module + "._gateway", side_effect=gateway), patch(module + ".execute", side_effect=execute_stub) as android:
                summary = run_parallel(paths, output, source, qualification, 5.)
            self.assertEqual(summary["completed_trajectories"], 6)
            self.assertTrue(summary["complete"])
            self.assertEqual(summary["reused_paid_model_responses"], 6)
            self.assertEqual(sum(g.generate.call_count for g in gateways), 5)
            self.assertEqual(android.call_count, 7)  # Two interrupted Android executions, five new turns.
            for path in (source / "trajectories/B018/P0/replicate_001").rglob("*"):
                if path.is_file():
                    self.assertEqual(path.read_bytes(), (output / path.relative_to(source)).read_bytes())

    def test_recovery_rejects_drift_and_missing_paid_response(self):
        with tempfile.TemporaryDirectory() as directory:
            _, configs, source, _, _ = self.recovery_fixture(Path(directory))
            response = next(source.glob("trajectories/**/model_result.json"))
            response.write_text("{}")
            with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                audit_recovery(source, configs)

    def test_http524_retry_requires_exact_authorization_and_preserves_unknown_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            _, configs, source, _, _ = self.recovery_fixture(Path(directory))
            relative = "trajectories/B002/P1/replicate_001/turn_01"
            turn = source / relative
            (turn / "attempt_record.json").unlink()
            (turn / "model_result.json").write_text(json.dumps({"run_status": "infrastructure_error",
                "error_type": "provider_error_response", "provider_response": {"msg": "HTTP 524: upstream timeout"},
                "transport_attempts": [{"attempt": 1, "status": "response_received"}]}))
            (source / "SHA256SUMS").unlink()
            _write_sha256s(source)
            with self.assertRaisesRegex(RuntimeError, "explicit HTTP524 retry authorization"):
                audit_recovery(source, configs)
            plan = audit_recovery(source, configs, [relative])
            self.assertEqual(plan["prior_paid_requests"], 6)
            self.assertEqual(plan["reuse_model_responses"], 5)
            self.assertEqual(plan["retry_provider_requests"], 1)
            self.assertEqual(plan["unknown_request_cost_reserve_usd"], .25)
            self.assertTrue(plan["turns"][relative]["retry_provider"])
            with self.assertRaisesRegex(RuntimeError, "explicit HTTP524 retry authorization"):
                audit_recovery(source, configs, ["a different request"])

    def test_transport_retry_whitelist_requires_one_failed_attempt_and_no_output(self):
        for error_type in ("TimeoutError", "ConnectionResetError"):
            with self.subTest(error_type=error_type), tempfile.TemporaryDirectory() as directory:
                _, configs, source, _, _ = self.recovery_fixture(Path(directory))
                relative = "trajectories/B002/P1/replicate_001/turn_01"
                turn = source / relative
                (turn / "attempt_record.json").unlink()
                failure = {"run_status": "infrastructure_error", "error_type": error_type,
                           "transport_attempts": [{"attempt": 1, "status": "transport_error"}]}
                (turn / "model_result.json").write_text(json.dumps(failure))
                (source / "SHA256SUMS").unlink(); _write_sha256s(source)
                with self.assertRaisesRegex(RuntimeError, "exact transport retry authorization"):
                    audit_recovery(source, configs)
                plan = audit_recovery(source, configs, authorized_provider_retries=[relative])
                self.assertEqual(plan["retry_provider_requests"], 1)
                self.assertEqual(plan["unknown_request_cost_reserve_usd"], .25)
                self.assertEqual(plan["turns"][relative]["retry_kind"], error_type)
                failure["usage"] = {"input_tokens": 1, "output_tokens": 1}
                (turn / "model_result.json").write_text(json.dumps(failure))
                (source / "SHA256SUMS").unlink(); _write_sha256s(source)
                with self.assertRaisesRegex(RuntimeError, "exact transport retry authorization"):
                    audit_recovery(source, configs, authorized_provider_retries=[relative])

    def test_two_authorized_transport_retries_do_not_resubmit_saved_good_responses(self):
        from unittest.mock import Mock
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths, configs, source, qualification, baselines = self.recovery_fixture(root)
            authorized = []
            for arm, error_type in (("P1", "TimeoutError"), ("P2", "ConnectionResetError")):
                relative = f"trajectories/B002/{arm}/replicate_001/turn_01"
                authorized.append(relative)
                turn = source / relative
                (turn / "attempt_record.json").unlink()
                (turn / "model_result.json").write_text(json.dumps({"run_status": "infrastructure_error",
                    "error_type": error_type, "transport_attempts": [{"attempt": 1, "status": "transport_error"}]}))
            (source / "SHA256SUMS").unlink(); _write_sha256s(source)
            original_digest = hashlib.sha256((source / "SHA256SUMS").read_bytes()).hexdigest()
            gateways = []
            def gateway(config):
                obj = Mock()
                def generate(model, prompt):
                    case = config["cases"][0 if "B002" in prompt else 1]
                    candidate = copy.deepcopy(case["hidden_smoke_fixtures"]["control"])
                    key = case["input"]["resource_ids"][0 if case["case_id"] == "B002" else -1]
                    candidate[key] = "1000" if case["case_id"] == "B002" else ",.,"
                    raw = json.dumps(candidate)
                    return {"run_status": "ok", "response_model": "stub", "raw_response": raw,
                            "raw_response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                            "usage": {"input_tokens": 10, "output_tokens": 5}, "elapsed_ms": 1,
                            "transport_attempts": [{"attempt": 1, "status": "response_received"}]}
                obj.generate.side_effect = generate
                gateways.append(obj)
                return obj
            output = root / "recovery"
            with patch(module + ".validate_config"), patch(module + ".local_preflight", return_value={"ready_for_live_run": True}), \
                 patch(module + "._frozen_baselines", return_value=baselines), \
                 patch(module + "._materialize_frozen_inputs", side_effect=lambda out, *_: _write_new(out / "model_qualification_receipt.json", {"request_cost_usd": 0})), \
                 patch(module + "._gateway", side_effect=gateway), \
                 patch(module + ".execute", return_value={"run_status": "ok", "o_spec": "FAIL", "unknown_reason": None,
                      "public_observations": [], "elapsed_ms": 1, "historical_issue_reproduced": True}) as android:
                summary = run_parallel(paths, output, source, qualification, 5., authorized_provider_retries=authorized)
            self.assertTrue(summary["complete"])
            self.assertEqual(summary["reused_paid_model_responses"], 4)
            self.assertEqual(summary["new_provider_responses"], 5)
            self.assertEqual(summary["cumulative_paid_experimental_request_count"], 11)
            self.assertEqual(summary["unknown_request_cost_reserve_usd"], .5)
            self.assertEqual(android.call_count, 5)
            self.assertEqual(sum(g.generate.call_count for g in gateways), 5)
            self.assertEqual(hashlib.sha256((source / "SHA256SUMS").read_bytes()).hexdigest(), original_digest)
            for relative in authorized:
                self.assertEqual((source / relative / "prompt.txt").read_bytes(), (output / relative / "prompt.txt").read_bytes())
                reservation = json.loads((output / relative / "model_request_reserved.json").read_text())
                self.assertEqual(reservation["user_authorized_provider_retry_source"], str(source / relative))

    def completed_fixture(self, root):
        from unittest.mock import Mock
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        paths, configs, source, qualification, baselines = self.recovery_fixture(root)
        def make_gateway(config):
            obj = Mock()
            def generate(model, prompt):
                case = config["cases"][0 if "B002" in prompt else 1]
                candidate = copy.deepcopy(case["hidden_smoke_fixtures"]["control"])
                key = case["input"]["resource_ids"][0 if case["case_id"] == "B002" else -1]
                candidate[key] = "1000" if case["case_id"] == "B002" else ",.,"
                raw = json.dumps(candidate)
                return {"run_status": "ok", "response_model": "stub", "raw_response": raw,
                        "raw_response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                        "usage": {"input_tokens": 10, "output_tokens": 5}, "elapsed_ms": 1,
                        "transport_attempts": [{"attempt": 1, "status": "response_received"}]}
            obj.generate.side_effect = generate
            return obj
        def execute_stub(request):
            return {"candidate_sha256": sha256_json(request["candidate"]), "run_status": "ok", "o_spec": "FAIL",
                    "unknown_reason": None, "public_observations": [], "elapsed_ms": 1, "historical_issue_reproduced": True}
        complete = root / "completed"
        with patch(module + ".validate_config"), patch(module + ".local_preflight", return_value={"ready_for_live_run": True}), \
             patch(module + "._frozen_baselines", return_value=baselines), \
             patch(module + "._materialize_frozen_inputs", side_effect=lambda out, *_: _write_new(out / "model_qualification_receipt.json", {"request_cost_usd": 0})), \
             patch(module + "._gateway", side_effect=make_gateway), patch(module + ".execute", side_effect=execute_stub):
            run_parallel(paths, complete, source, qualification, 5.)
        return complete, configs, qualification, baselines, make_gateway, execute_stub

    def test_extension_rejects_incomplete_parent_without_model_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, source, _, _ = self.recovery_fixture(Path(directory))
            with self.assertRaisesRegex(RuntimeError, "not fully completed"):
                extension_configs(source, 2, 20.)

    def test_extension_keeps_prefix_and_only_generates_new_independent_replicates(self):
        module = "text_input_eval.supplementary.spec_source_backed_repeat"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, originals, qualification, baselines, gateway, executor = self.completed_fixture(root)
            configs = extension_configs(source, 2, 20.)
            self.assertEqual(repeat_schedule(configs[0])[:6], _schedule(originals[0]))
            self.assertEqual(len(repeat_schedule(configs[0])), 18)
            self.assertEqual({r["replicate_id"] for r in repeat_schedule(configs[0])[6:]}, {2, 3})
            self.assertEqual(audit_extension(source, configs)["completed_trajectories_not_rerun"], 6)
            for before, after in zip(originals, configs):
                self.assertEqual(before["cases"], after["cases"])
                self.assertEqual(before["model"], after["model"])
                for case_before, case_after in zip(before["cases"], after["cases"]):
                    for arm in ("P0", "P1", "P2"):
                        self.assertEqual(render_prompt(case_before, arm, [], baselines[case_before["case_id"]]),
                                         render_prompt(case_after, arm, [], baselines[case_after["case_id"]]))
            paths = []
            for i, config in enumerate(configs):
                path = root / f"extension_{i}.json"
                _write_new(path, config); paths.append(path)
            output = root / "extended"
            with patch(module + ".validate_config"), patch(module + ".local_preflight", return_value={"ready_for_live_run": True}), \
                 patch(module + "._frozen_baselines", return_value=baselines), \
                 patch(module + "._materialize_frozen_inputs", side_effect=lambda out, *_: _write_new(out / "model_qualification_receipt.json", {"request_cost_usd": 0})), \
                 patch(module + "._gateway", side_effect=gateway), patch(module + ".execute", side_effect=executor) as android:
                summary = run_parallel(paths, output, repair_qualification=qualification, cost_cap=20., extension_from=source)
            self.assertTrue(summary["complete"])
            self.assertEqual(summary["completed_trajectories"], 18)
            self.assertEqual(summary["completed_new_trajectories"], 12)
            self.assertEqual(summary["new_provider_responses"], 12)
            self.assertEqual(android.call_count, 12)
            for m in summary["extension_new_case_arm_metrics"].values():
                self.assertEqual((m["completed"], m["expected"]), (2, 2))
            for path in (source / "trajectories").rglob("*"):
                if path.is_file():
                    self.assertEqual(path.read_bytes(), (output / path.relative_to(source)).read_bytes())
            drift = copy.deepcopy(configs)
            drift[0]["cases"][0]["generation_contract"]["scope"] = "changed specification"
            with self.assertRaisesRegex(RuntimeError, "beyond the authorized"):
                audit_extension(source, drift)


class FixedPngTransportTests(unittest.TestCase):
    def setUp(self):
        import io
        from PIL import Image
        from text_input_eval.supplementary import spec_source_backed_repeat as m
        self.m = m
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = patch.object(m, 'CAPTURE_EVIDENCE_ROOT', self.root)
        self.scope.start(); self.addCleanup(self.scope.stop)
        buf = io.BytesIO(); Image.new('RGB', (13, 17), (10, 20, 30)).save(buf, format='PNG')
        self.png = buf.getvalue()
        self.calls = []
        self.partial_pulls = 0
        self.bad_all = False
        self.missing_metadata = False
        self.status = 0
        self.launch_code = 0

    def transport(self, argv, directory, stem):
        self.calls.append(argv)
        if stem == 'screenshot.snapshot_start':
            self.remote = argv[-1].split(' > ', 1)[1].split('.png', 1)[0]
            return self.launch_code, b'', b''
        if stem.startswith('screenshot.metadata_'):
            if self.missing_metadata:
                return 0, b'', b''
            token = self.remote.rsplit('_', 1)[1]
            text = (f'{self.status}\n{len(self.png)} {self.remote}.png\n'
                    f'{hashlib.sha256(self.png).hexdigest()} {self.remote}.png\n'
                    f'0 {self.remote}.stderr\n{hashlib.sha256(b"").hexdigest()} {self.remote}.stderr\n'
                    f'SPEC_SCREENSHOT_END_{token}\n')
            return 0, text.encode(), b''
        raw = b'' if '.stderr' in argv[-2 if 'pull' in argv else -1] else self.png
        if '.png' in argv[-2 if 'pull' in argv else -1]:
            if self.bad_all or ('pull' in argv and self.partial_pulls):
                raw = raw[:7]
                if self.partial_pulls: self.partial_pulls -= 1
        if 'pull' in argv:
            with Path(argv[-1]).open('xb') as stream: stream.write(raw)
            return 0, b'', b''
        return 0, raw, b''

    def sync_transport(self, serial, remote, destination, directory, stem):
        code, raw, _ = self.transport(['adb', '-s', serial, 'exec-out', 'cat', remote], directory, stem)
        with destination.open('xb') as stream:
            stream.write(raw)
        return code

    def capture(self):
        with patch.object(self.m, '_logcat_transport', side_effect=self.transport), \
             patch.object(self.m, '_screenshot_sync_recv', side_effect=self.sync_transport), patch.object(self.m.time, 'sleep'):
            return self.m.capture_fixed_png('emulator-5580', self.root/'snapshot')

    def test_one_capture_is_verified_against_guest_size_sha_and_png(self):
        image, receipt = self.capture()
        self.assertEqual(image.size, (13, 17))
        self.assertEqual(image.getpixel((0, 0)), (10, 20, 30))
        self.assertTrue(receipt['remote_hash_and_size_verified'])
        self.assertEqual(receipt['screencap_invocations'], 1)
        self.assertEqual(len(receipt['png_transfer_attempts']), 1)
        self.assertEqual(sum('screencap -p' in a[-1] for a in self.calls), 1)

    def test_short_pull_rereads_same_file_not_another_capture(self):
        self.partial_pulls = 1
        _, receipt = self.capture()
        self.assertEqual([t['verified'] for t in receipt['png_transfer_attempts']], [False, True])
        self.assertEqual([t['transport_method'] for t in receipt['png_transfer_attempts']], ['adb-pull','adb-sync-v1-recv'])
        paths = [a[-2] if 'pull' in a else a[-1] for a in self.calls if 'pull' in a or 'exec-out' in a]
        self.assertEqual(paths[:2], [self.remote+'.png']*2)
        self.assertEqual(sum('screencap -p' in a[-1] for a in self.calls), 1)

    def test_persistent_partial_bytes_stop_after_three_transfers_no_fourth(self):
        self.bad_all = True
        with self.assertRaisesRegex(RuntimeError, 'capture NOT repeated'): self.capture()
        self.assertEqual(sum('pull' in a or 'exec-out' in a for a in self.calls), 3)
        self.assertEqual(sum('screencap -p' in a[-1] for a in self.calls), 1)
        self.assertFalse(json.loads((self.root/'snapshot/screenshot_snapshot_failure.json').read_text())['verified'])

    def test_same_size_wrong_hash_is_not_accepted(self):
        expected={'bytes':8,'sha256':hashlib.sha256(b'original').hexdigest(),'remote_path':'/data/local/tmp/spec_screenshot_test.png'}
        directory=self.root/'transfer';directory.mkdir()
        def wrong(argv, directory, stem):
            if 'pull' in argv:
                with Path(argv[-1]).open('xb') as stream:stream.write(b'tampered')
                return 0,b'',b''
            return 0,b'tampered',b''
        def sync_wrong(serial, remote, destination, directory, stem):
            with destination.open('xb') as stream:stream.write(b'tampered')
            return 0
        with patch.object(self.m,'_logcat_transport',side_effect=wrong) as transfer, \
             patch.object(self.m,'_screenshot_sync_recv',side_effect=sync_wrong) as sync,patch.object(self.m.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'3 reads'):self.m._screenshot_snapshot_file('emulator-5580',expected,directory,'png')
        self.assertEqual(transfer.call_count,1)
        self.assertEqual(sync.call_count,2)

    def test_three_file_reads_are_spaced_beyond_observed_cold_transport_bounce(self):
        from unittest.mock import call
        expected={'bytes':1,'sha256':hashlib.sha256(b'x').hexdigest(),'remote_path':'/data/local/tmp/spec_screenshot_test.png'}
        directory=self.root/'timed-transfer';directory.mkdir()
        with patch.object(self.m,'_logcat_transport',return_value=(255,b'',b'')) as transfer, \
             patch.object(self.m,'_screenshot_sync_recv',return_value=1) as sync,patch.object(self.m.time,'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError,'3 reads'):self.m._screenshot_snapshot_file('emulator-5580',expected,directory,'png')
        self.assertEqual(transfer.call_count,1)
        self.assertEqual(sync.call_count,2)
        self.assertEqual(sleep.call_args_list,[call(1),call(1)])

    def test_wrong_snapshot_metadata_identity_is_rejected(self):
        directory=self.root/'metadata';directory.mkdir()
        digest=hashlib.sha256(b'').hexdigest()
        raw=f'0\n0 /wrong.png\n{digest} /wrong.png\n0 /right.stderr\n{digest} /right.stderr\nSPEC_SCREENSHOT_END_token\n'.encode()
        with patch.object(self.m,'_logcat_transport',return_value=(0,raw,b'')) as reads,patch.object(self.m.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'3 reads'):self.m._screenshot_snapshot_metadata('emulator-5580','/right','token',directory)
        self.assertEqual(reads.call_count,3)

    def test_missing_status_is_not_accepted_or_recaptured(self):
        self.missing_metadata = True
        with self.assertRaisesRegex(RuntimeError, 'capture NOT repeated'): self.capture()
        self.assertEqual(sum(a[-1].startswith('test -f') for a in self.calls), 3)
        self.assertFalse(any('pull' in a or 'exec-out' in a for a in self.calls))

    def test_nonzero_capture_is_unknown_without_transfer(self):
        self.status = 1
        with self.assertRaises(RuntimeError): self.capture()
        self.assertFalse(any('pull' in a or 'exec-out' in a for a in self.calls))

    def test_transport_exit_does_not_override_independent_committed_status(self):
        self.launch_code = 255
        _, receipt = self.capture()
        self.assertTrue(receipt['verified'])
        self.assertEqual(receipt['snapshot_start_transport_exit_code'], 255)

    def test_matching_size_sha_cannot_make_invalid_png_valid(self):
        self.png = b'not actual pixels'
        with self.assertRaisesRegex(RuntimeError, 'capture NOT repeated'): self.capture()

    def test_missing_iend_even_with_matching_guest_hash_is_rejected(self):
        self.png = self.png[:-12]
        with self.assertRaises(RuntimeError): self.capture()

    def test_foreign_serial_and_evidence_path_are_rejected_before_commands(self):
        with patch.object(self.m, '_logcat_transport') as transport:
            with self.assertRaises(RuntimeError): self.m.capture_fixed_png('emulator-5560', self.root/'snapshot')
            with self.assertRaises(RuntimeError): self.m.capture_fixed_png('emulator-5580', self.root.parent/'outside')
            transport.assert_not_called()

    def test_default_capture_does_not_use_http_or_original_sdk(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        device = SimpleNamespace(screenshot=Mock(), send_keys=Mock())
        original = device.screenshot
        events = []
        self.m.install_fixed_png_capture(device, 'emulator-5580', events)
        with patch.object(self.m, '_logcat_transport', side_effect=self.transport), patch.object(self.m.time, 'sleep'):
            picture = device.screenshot()
        self.assertEqual(picture.size, (13, 17)); original.assert_not_called();device.send_keys.assert_not_called()
        self.assertEqual(events[0]['http_screenshot_requests'], 0)
        self.assertTrue(Path(events[0]['transport_evidence_directory']).is_dir())

    def test_filename_contract_saves_once_and_returns_none(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        device=SimpleNamespace(screenshot=Mock());events=[]
        self.m.install_fixed_png_capture(device,'emulator-5580',events)
        image=Mock()
        with patch.object(self.m,'capture_fixed_png',return_value=(image,{'verified':True})):
            self.assertIsNone(device.screenshot(self.root/'after.png'))
        image.save.assert_called_once_with(self.root/'after.png')

    def test_nondefault_format_or_display_preserves_original_api(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        for kwargs in ({'format':'opencv'},{'display_id':0}):
            original=Mock();device=SimpleNamespace(screenshot=original)
            self.m.install_fixed_png_capture(device,'emulator-5580',[])
            with patch.object(self.m,'capture_fixed_png') as snapshot:
                self.assertIs(device.screenshot(**kwargs),original.return_value)
            original.assert_called_once_with(None,format=kwargs.get('format','pillow'),display_id=kwargs.get('display_id'))
            snapshot.assert_not_called()

    def test_installation_is_instance_scoped_and_idempotent(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        device=SimpleNamespace(screenshot=Mock());other=SimpleNamespace(screenshot=Mock())
        other_original=other.screenshot
        self.m.install_fixed_png_capture(device,'emulator-5580',[]);first=device.screenshot
        self.m.install_fixed_png_capture(device,'emulator-5580',[])
        self.assertIs(device.screenshot,first);self.assertIs(other.screenshot,other_original)

    def test_original_exact_two_read_one_event_injection_remains_strict(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from adbutils import AdbError
        for error in (AdbError(),json.JSONDecodeError('injected','',0)):
            device=SimpleNamespace(screenshot=Mock());transport_events=[]
            self.m.install_fixed_png_capture(device,'emulator-5580',transport_events)
            capture=device.screenshot;count=[0]
            def injected(*args,**kwargs):
                count[0]+=1
                if count[0]==1:raise error
                return capture(*args,**kwargs)
            device.screenshot=injected;events=[]
            self.m.install_capture_read_guard(device,events)
            with patch.object(self.m,'_logcat_transport',side_effect=self.transport),patch.object(self.m.time,'sleep'):
                self.assertEqual(device.screenshot().size,(13,17))
            self.assertEqual(count[0],2);self.assertEqual(len(events),1)
            self.assertEqual(len(transport_events),1)

    def test_snapshot_failure_is_not_reclassified_for_outer_recapture(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        device=SimpleNamespace(screenshot=Mock())
        self.m.install_fixed_png_capture(device,'emulator-5580',[])
        events=[];self.m.install_capture_read_guard(device,events,serial='emulator-5580')
        with patch.object(self.m,'capture_fixed_png',side_effect=RuntimeError('capture NOT repeated')) as snapshot:
            with self.assertRaises(RuntimeError):device.screenshot()
        snapshot.assert_called_once();self.assertEqual(events,[])


class HierarchyJsonReadGuardTests(unittest.TestCase):
    def test_json_read_failure_then_real_xml_keeps_arguments_and_no_input(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        original=Mock(side_effect=[json.JSONDecodeError('bad','',0),'<hierarchy/>'])
        device=SimpleNamespace(dump_hierarchy=original,click=Mock(),send_keys=Mock());events=[]
        install_hierarchy_read_guard(device,events)
        with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep') as sleep:
            self.assertEqual(device.dump_hierarchy(compressed=False,max_depth=17),'<hierarchy/>')
        self.assertEqual(original.call_count,2)
        self.assertTrue(all(c.kwargs=={'compressed':False,'max_depth':17} for c in original.call_args_list))
        self.assertEqual(len(events),1);self.assertEqual(events[0]['invalid_rpc_response_utf8_bytes'],0)
        self.assertEqual(events[0]['invalid_rpc_response_sha256'],hashlib.sha256(b'').hexdigest())
        device.click.assert_not_called();device.send_keys.assert_not_called();sleep.assert_called_once_with(1)

    def test_whole_decoded_invalid_document_is_preserved_not_only_a_hash(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        document='{"result":"截断'
        device=SimpleNamespace(dump_hierarchy=Mock(side_effect=[json.JSONDecodeError('truncated',document,10),'<hierarchy/>']))
        events=[];install_hierarchy_read_guard(device,events)
        with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):device.dump_hierarchy()
        self.assertEqual(bytes.fromhex(events[0]['invalid_rpc_response_utf8_hex']).decode(),document)
        self.assertFalse(events[0]['original_http_headers_or_wire_bytes_archived'])

    def test_mixed_http_and_json_reads_share_one_three_call_budget(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from requests.exceptions import ConnectionError
        from uiautomator2.exceptions import HTTPError
        error=HTTPError('read disconnected');error.__cause__=ConnectionError('closed')
        device=SimpleNamespace(dump_hierarchy=Mock(side_effect=[error,json.JSONDecodeError('bad','',0),'<hierarchy/>']))
        events=[];install_hierarchy_read_guard(device,events)
        with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):self.assertEqual(device.dump_hierarchy(),'<hierarchy/>')
        self.assertEqual(device.dump_hierarchy.__name__,'guarded_hierarchy')
        self.assertEqual(len(events),2);self.assertEqual([e['failed_read_attempt'] for e in events],[1,2])

    def test_persistent_json_failure_stops_at_three_without_fake_xml(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        original=Mock(side_effect=json.JSONDecodeError('bad','not JSON',0))
        device=SimpleNamespace(dump_hierarchy=original);events=[]
        install_hierarchy_read_guard(device,events)
        with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):
            with self.assertRaises(json.JSONDecodeError):device.dump_hierarchy()
        self.assertEqual(original.call_count,3);self.assertEqual(len(events),3);self.assertFalse(events[-1]['will_retry'])

    def test_semantic_or_unclassified_http_error_still_fails_immediately(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from uiautomator2.exceptions import HTTPError
        original=Mock(side_effect=HTTPError('semantic response'))
        device=SimpleNamespace(dump_hierarchy=original);events=[];install_hierarchy_read_guard(device,events)
        with self.assertRaises(HTTPError):device.dump_hierarchy()
        original.assert_called_once();self.assertEqual(events,[])

    def test_installing_twice_does_not_nest_the_three_read_budget(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        original=Mock(side_effect=json.JSONDecodeError('bad','',0))
        device=SimpleNamespace(dump_hierarchy=original);events=[]
        install_hierarchy_read_guard(device,events);first=device.dump_hierarchy
        install_hierarchy_read_guard(device,events);self.assertIs(device.dump_hierarchy,first)
        with patch('text_input_eval.supplementary.spec_source_backed_repeat.time.sleep'):
            with self.assertRaises(json.JSONDecodeError):device.dump_hierarchy()
        self.assertEqual(original.call_count,3)


if __name__ == "__main__":
    unittest.main()
