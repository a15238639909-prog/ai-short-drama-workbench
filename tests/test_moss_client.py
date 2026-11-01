"""Mocked Moss checks; no files are deleted and no model is invoked."""
from contextlib import ExitStack
import io
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import Mock, patch

from models import moss_client as moss


class MossWorkflowTests(unittest.TestCase):
    def test_original_graph_and_parameters_are_retained(self):
        original = json.loads(moss.WORKFLOW.read_text(encoding='utf-8'))
        workflow = moss.build_workflow('a red toy car on a white table', seed=42)
        for node_id in original:
            if node_id not in ('129', '132', '141'):
                self.assertEqual(workflow[node_id], original[node_id])
        self.assertEqual(workflow['129']['inputs']['text'], 'a red toy car on a white table')
        self.assertEqual(workflow['132']['inputs']['seed'], 42)
        self.assertEqual(workflow['122']['inputs']['noise_seed'], 666)
        self.assertEqual(workflow['121']['inputs']['scale_by'], 1.2)
        self.assertEqual(workflow['158']['inputs']['single_layers'], '22')
        self.assertEqual(workflow['133']['inputs']['type'], 'moss_image')

    def test_dimensions_match_verified_flux2_latent_output(self):
        self.assertEqual(moss.dimensions(), (768, 1360))
        self.assertEqual(moss.output_dimensions(), (928, 1632))
        self.assertEqual(moss.output_dimensions(1024, 1024), (1232, 1232))
        workflow = moss.build_workflow('test', 1024, 1024, 3)
        self.assertEqual(workflow['126']['inputs'], {'width': 1024, 'height': 1024, 'batch_size': 1})

    def test_invalid_size_is_rejected_before_submission(self):
        for width, height in ((None, 1024), (1024, None), (128, 1024),
                              (4096, 256), (1920, 1080), (True, 1024), ('1.2', 1024)):
            with self.subTest(width=width, height=height), self.assertRaises(ValueError):
                moss.build_workflow('test', width, height)

    def test_prompt_language_is_preserved(self):
        text = '普通白底上的红色玩具车'
        self.assertEqual(moss.build_workflow(text, seed=1)['129']['inputs']['text'], text)
        with self.assertRaises(ValueError):
            moss.build_workflow(' ')

    def test_explicit_two_times_supports_4k_without_changing_sampling(self):
        original = moss.build_workflow('肖像', 1024, 1024, seed=42)
        graph = moss.build_workflow('肖像', 1440, 1920, seed=42, upscale=2)
        self.assertEqual(moss.output_dimensions(1440, 1920, 2), (2880, 3840))
        self.assertEqual(moss.output_dimensions(1920, 1088, 2), (3840, 2176))
        self.assertEqual(moss.output_dimensions(1920, 1920, 2), (3840, 3840))
        self.assertEqual(graph['121']['inputs']['scale_by'], 2)
        for node in ('120', '122', '130', '158'):
            self.assertEqual(graph[node], original[node])
        self.assertEqual(original['121']['inputs']['scale_by'], 1.2)
        for factor in (None, 1.2):
            with self.assertRaises(ValueError):
                moss.build_workflow('肖像', 1440, 1920, upscale=factor)
        for factor in (True, 0, 1, 3, 'bad', []):
            with self.subTest(factor=factor), self.assertRaises(ValueError):
                moss.build_workflow('肖像', upscale=factor)
        with self.assertRaises(ValueError):
            moss.build_workflow('肖像', 2048, 1920, upscale=2)

    def test_seed_validation_and_template_independence(self):
        for seed in (-1, True, 'bad', 1125899906842625):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                moss.build_workflow('test', seed=seed)
        first = moss.build_workflow('first', seed=1)
        second = moss.build_workflow('second', seed=2)
        self.assertEqual(first['132']['inputs']['seed'], 1)
        self.assertEqual(second['132']['inputs']['seed'], 2)
        self.assertNotEqual(first['141']['inputs']['filename_prefix'], second['141']['inputs']['filename_prefix'])
        original = json.loads(moss.WORKFLOW.read_text(encoding='utf-8'))
        self.assertEqual(original['129']['inputs']['text'], '')

    def test_all_links_resolve_in_the_frozen_graph(self):
        workflow = moss.build_workflow('test', seed=1)
        for node in workflow.values():
            for value in node['inputs'].values():
                if isinstance(value, list):
                    self.assertIn(value[0], workflow)


class MossSafetyTests(unittest.TestCase):
    def test_no_model_mode_blocks_local_guard(self):
        with patch.dict('os.environ', {'V41_NO_MODEL': '1'}), \
                patch('cores.runtime_reset.assert_current'), \
                patch('models.gpu_manager.assert_local_run_allowed') as allowed:
            with self.assertRaisesRegex(RuntimeError, 'V41_NO_MODEL'):
                moss.local_guard()
        allowed.assert_not_called()

    def test_game_and_runtime_guards_are_reused(self):
        with patch.dict('os.environ', {}, clear=True), \
                patch('cores.runtime_reset.assert_current') as current, \
                patch('models.krea_client._game_mode_guard') as game, \
                patch('models.gpu_manager.assert_local_run_allowed') as allowed:
            moss.local_guard()
        current.assert_called_once()
        game.assert_called_once()
        allowed.assert_called_once()

    def test_missing_models_are_reported(self):
        workflow = moss.build_workflow('test', seed=1)
        with patch.object(Path, 'is_file', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Moss-dirtyMoss'):
                moss._required_models(workflow)

    def test_missing_node_or_moss_encoder_is_reported(self):
        workflow = moss.build_workflow('test', seed=1)
        available = {node['class_type']: {} for node in workflow.values()}
        available['CLIPLoader'] = {'input': {'required': {'type': [['krea2', 'moss_image']]}}}
        with patch('models.krea_client._http', return_value=available):
            moss._required_nodes(workflow)
        del available['SkipLayerGuidanceDiTSimple']
        with patch('models.krea_client._http', return_value=available):
            with self.assertRaisesRegex(RuntimeError, 'SkipLayerGuidance'):
                moss._required_nodes(workflow)
        available['SkipLayerGuidanceDiTSimple'] = {}
        available['CLIPLoader']['input']['required']['type'][0] = ['krea2']
        with patch('models.krea_client._http', return_value=available):
            with self.assertRaisesRegex(RuntimeError, '文本编码器'):
                moss._required_nodes(workflow)

    def test_unload_accepts_comfy_empty_http_200_response(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch('cores.runtime_reset.assert_current'), \
                patch('urllib.request.urlopen', return_value=response) as request:
            moss._unload_idle_models()
        posted = request.call_args.args[0]
        self.assertTrue(posted.full_url.endswith('/free'))
        self.assertEqual(json.loads(posted.data), {'unload_models': True, 'free_memory': True})

    def test_output_uses_actual_png_size_and_safe_path(self):
        workflow = moss.build_workflow('test', seed=123)
        header = b'\x89PNG\r\n\x1a\n' + b'\0' * 8 + struct.pack('>II', 928, 1632)
        folder = str(moss.ROOT / 'outputs' / 'mock-comfy')
        with patch('cores.paths.get', return_value=folder), \
                patch('cores.runtime_reset.assert_current'), \
                patch.object(Path, 'is_file', return_value=True), \
                patch.object(Path, 'open', side_effect=lambda *args, **kwargs: io.BytesIO(header)), \
                patch.object(Path, 'mkdir'), patch('shutil.copy2') as copied:
            result = moss._image_result({'filename': 'result.png'}, workflow, 'job-id', None, None)
        self.assertEqual((result['width'], result['height'], result['seed']), (928, 1632, 123))
        self.assertEqual(result['model'], 'moss')
        self.assertEqual((result['base_width'], result['base_height']), (768, 1360))
        copied.assert_called_once()
        with patch('cores.paths.get', return_value=folder):
            with self.assertRaisesRegex(RuntimeError, '超出'):
                moss._image_result({'filename': 'result.png', 'subfolder': '../../outside'}, workflow,
                                   'job-id', None, None)


class MossResourceGuardTests(unittest.TestCase):
    def test_actual_qwen_endpoint_is_checked_first(self):
        with patch('models.qwen_client.QWEN_URL', 'http://localhost:18081'):
            endpoints = moss._qwen_endpoints()
        self.assertEqual(endpoints[0], ('http://localhost:18081', 'localhost', 18081))
        self.assertTrue({8080, 18080}.issubset({endpoint[2] for endpoint in endpoints}))

    def test_missing_qwen_port_is_not_an_error(self):
        with patch('socket.create_connection', side_effect=ConnectionRefusedError), \
                patch('urllib.request.urlopen') as request:
            moss._qwen_slots_idle('http://127.0.0.1:8080', '127.0.0.1', 8080)
        request.assert_not_called()

    def test_qwen_slot_state_must_be_known_and_idle(self):
        for payload in ([], {}, [{'is_processing': None}], [{'id': 0}]):
            with self.subTest(payload=payload), \
                    patch('socket.create_connection'), \
                    patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())):
                with self.assertRaisesRegex(RuntimeError, '无法确认空闲'):
                    moss._qwen_slots_idle('http://127.0.0.1:8080', '127.0.0.1', 8080)

    def test_qwen_connection_unknown_is_not_treated_as_absent(self):
        with patch('socket.create_connection', side_effect=TimeoutError):
            with self.assertRaisesRegex(RuntimeError, '无法确认文字服务'):
                moss._qwen_slots_idle('http://127.0.0.1:8080', '127.0.0.1', 8080)

    def test_live_other_thread_owner_is_respected(self):
        other = Mock(ident=moss.threading.get_ident() + 1)
        other.is_alive.return_value = True
        current = {'pid': moss.os.getpid(), 'thread_id': other.ident,
                   'who': 'h3', 'runtime_generation': 'current'}
        with patch('models.gpu_manager.owner', return_value=current), \
                patch('cores.runtime_reset.capture', return_value='current'), \
                patch('threading.enumerate', return_value=[other]), \
                patch.object(moss, '_qwen_slots_idle') as slots:
            with self.assertRaisesRegex(RuntimeError, '另一项模型任务'):
                moss._resource_guard()
        slots.assert_not_called()

    def test_confirmed_idle_qwen_sticky_owner_is_allowed(self):
        other = Mock(ident=moss.threading.get_ident() + 1)
        other.is_alive.return_value = True
        current = {'pid': moss.os.getpid(), 'thread_id': other.ident,
                   'who': 'qwen', 'runtime_generation': 'current'}
        with patch('models.gpu_manager.owner', return_value=current), \
                patch('cores.runtime_reset.capture', return_value='current'), \
                patch('threading.enumerate', return_value=[other]), \
                patch.object(moss, '_windows_listening_ports', return_value={8080}), \
                patch.object(moss, '_qwen_endpoints', return_value=[('http://127.0.0.1:8080', '127.0.0.1', 8080)]), \
                patch('socket.create_connection'), \
                patch('urllib.request.urlopen', return_value=io.BytesIO(b'[{"is_processing": false}]')):
            moss._resource_guard()

    def test_windows_snapshot_distinguishes_listening_from_connections(self):
        snapshot = ('Active Connections\nProto Local Address Foreign Address State PID\n'
                    'TCP 0.0.0.0:8188 0.0.0.0:0 LISTENING 36696\n'
                    'TCP [::]:18080 [::]:0 LISTENING 444\n'
                    'TCP 127.0.0.1:8080 127.0.0.1:50500 ESTABLISHED 444\n')
        result = Mock(returncode=0, stdout=snapshot)
        with patch('subprocess.run', return_value=result):
            self.assertEqual(moss._windows_listening_ports(), {8188, 18080})

    def test_windows_snapshot_failures_remain_unknown(self):
        for result in (Mock(returncode=1, stdout=''),
                       Mock(returncode=0, stdout='TCP invalid LISTENING')):
            with self.subTest(result=result), patch('subprocess.run', return_value=result):
                with self.assertRaisesRegex(RuntimeError, '监听状态'):
                    moss._windows_listening_ports()
        with patch('subprocess.run', side_effect=TimeoutError):
            with self.assertRaisesRegex(RuntimeError, '监听状态'):
                moss._windows_listening_ports()

    def test_old_runtime_owner_does_not_block_new_request(self):
        other = Mock(ident=moss.threading.get_ident() + 1)
        other.is_alive.return_value = True
        current = {'pid': moss.os.getpid(), 'thread_id': other.ident,
                   'who': 'krea', 'runtime_generation': 'old'}
        with patch('models.gpu_manager.owner', return_value=current), \
                patch('cores.runtime_reset.capture', return_value='current'), \
                patch('threading.enumerate', return_value=[other]), \
                patch.object(moss, '_qwen_endpoints', return_value=[]):
            moss._resource_guard()


class MossGenerationTests(unittest.TestCase):
    def mocked_generation(self, response=None, history=None):
        stack = ExitStack()
        self.addCleanup(stack.close)
        mocks = {}
        for target in ('_required_models', 'local_guard', '_required_nodes', '_unload_idle_models'):
            mocks[target] = stack.enter_context(patch.object(moss, target))
        for target in ('acquire', 'claim', 'release'):
            mocks[target] = stack.enter_context(patch('models.gpu_manager.' + target))
        mocks['idle'] = stack.enter_context(patch('models.anima_client.idle_guard'))
        mocks['owner'] = stack.enter_context(patch('models.gpu_manager.owner', return_value=None))
        mocks['listening'] = stack.enter_context(patch.object(moss, '_windows_listening_ports', return_value=set()))
        mocks['connection'] = stack.enter_context(patch('socket.create_connection', side_effect=ConnectionRefusedError))
        mocks['up'] = stack.enter_context(patch('models.krea_client.up', return_value=True))
        mocks['start'] = stack.enter_context(patch('models.krea_client.start_comfy'))
        stack.enter_context(patch('cores.runtime_reset.assert_current'))
        stack.enter_context(patch('cores.age_policy.assert_model_request'))
        mocks['result'] = stack.enter_context(patch.object(moss, '_image_result',
                                                          return_value={'model': 'moss', 'seed': 123}))
        mocks['http'] = stack.enter_context(patch('models.krea_client._http', side_effect=[
            response if response is not None else {'prompt_id': 'job-id'},
            history if history is not None else {'job-id': {'status': {'status_str': 'success'},
                                      'outputs': {'141': {'images': [{'filename': 'result.png'}]}}}}
        ]))
        return mocks

    def test_existing_comfy_is_reused_without_restart(self):
        mocks = self.mocked_generation()
        result = moss.generate('a red toy car', seed=123)
        self.assertEqual(result, {'model': 'moss', 'seed': 123})
        mocks['start'].assert_not_called()
        mocks['acquire'].assert_called_once_with('krea', note='Moss image')
        mocks['claim'].assert_called_once_with('krea')
        mocks['release'].assert_called_once_with('krea')
        mocks['_unload_idle_models'].assert_called_once()
        self.assertEqual(mocks['http'].call_args_list[0].args[:2], ('POST', '/prompt'))

    def test_absent_comfy_uses_existing_launcher(self):
        mocks = self.mocked_generation()
        mocks['up'].return_value = False
        moss.generate('a blue cup', seed=123)
        mocks['start'].assert_called_once()

    def test_external_queue_is_not_interrupted(self):
        mocks = self.mocked_generation()
        mocks['idle'].side_effect = RuntimeError('已有绘图或视频任务')
        with self.assertRaisesRegex(RuntimeError, '已有'):
            moss.generate('a blue cup', seed=123)
        mocks['claim'].assert_not_called()
        mocks['http'].assert_not_called()
        mocks['_unload_idle_models'].assert_not_called()
        mocks['release'].assert_called_once_with('krea')

    def test_failed_submission_releases_gpu_owner(self):
        mocks = self.mocked_generation(response={'node_errors': {'141': 'missing'}})
        with self.assertRaisesRegex(RuntimeError, '未被接受'):
            moss.generate('a blue cup', seed=123)
        mocks['release'].assert_called_once_with('krea')

    def test_execution_error_does_not_copy_preview(self):
        mocks = self.mocked_generation(history={'job-id': {'status': {'status_str': 'error',
                      'messages': [['execution_error', {'exception_message': 'mock sampling failure'}]]}}})
        with self.assertRaisesRegex(RuntimeError, 'mock sampling failure'):
            moss.generate('a blue cup', seed=123)
        mocks['result'].assert_not_called()
        mocks['release'].assert_called_once_with('krea')

    def test_runtime_reset_before_submit_preserves_safety(self):
        mocks = self.mocked_generation()
        mocks['_unload_idle_models'].side_effect = RuntimeError('运行状态已更新')
        with self.assertRaisesRegex(RuntimeError, '运行状态'):
            moss.generate('a blue cup', seed=123)
        mocks['http'].assert_not_called()
        mocks['release'].assert_called_once_with('krea')

    def test_busy_qwen_is_not_stopped_or_overwritten(self):
        mocks = self.mocked_generation()
        mocks['listening'].return_value = {8080}
        mocks['connection'].side_effect = None
        with patch('urllib.request.urlopen', side_effect=lambda *args, **kwargs:
                   io.BytesIO(b'[{"is_processing": true}]')):
            with self.assertRaisesRegex(RuntimeError, '正在处理请求'):
                moss.generate('a blue cup', seed=123)
        mocks['acquire'].assert_not_called()
        mocks['claim'].assert_not_called()
        mocks['http'].assert_not_called()
        mocks['_unload_idle_models'].assert_not_called()

    def test_unknown_qwen_is_not_stopped_or_overwritten(self):
        mocks = self.mocked_generation()
        mocks['listening'].return_value = {8080}
        mocks['connection'].side_effect = None
        with patch('urllib.request.urlopen', side_effect=TimeoutError):
            with self.assertRaisesRegex(RuntimeError, '无法确认空闲'):
                moss.generate('a blue cup', seed=123)
        mocks['acquire'].assert_not_called()
        mocks['claim'].assert_not_called()
        mocks['http'].assert_not_called()

    def test_confirmed_idle_qwen_can_be_unloaded_by_existing_claim(self):
        mocks = self.mocked_generation()
        mocks['listening'].return_value = {8080}
        mocks['connection'].side_effect = None
        with patch('urllib.request.urlopen', side_effect=lambda *args, **kwargs:
                   io.BytesIO(b'[{"is_processing": false}]')):
            moss.generate('a blue cup', seed=123)
        mocks['acquire'].assert_called_once()
        mocks['claim'].assert_called_once_with('krea')
        self.assertEqual(mocks['http'].call_args_list[0].args[:2], ('POST', '/prompt'))

    def test_slot_becomes_busy_before_claim_is_not_interrupted(self):
        mocks = self.mocked_generation()
        mocks['listening'].return_value = {8080}
        mocks['connection'].side_effect = None
        with patch.object(moss, '_qwen_endpoints',
                          return_value=[('http://127.0.0.1:8080', '127.0.0.1', 8080)]), \
                patch('urllib.request.urlopen', side_effect=[
                    io.BytesIO(b'[{"is_processing": false}]'), io.BytesIO(b'[{"is_processing": true}]')]):
            with self.assertRaisesRegex(RuntimeError, '正在处理请求'):
                moss.generate('a blue cup', seed=123)
        mocks['acquire'].assert_called_once()
        mocks['claim'].assert_not_called()
        mocks['http'].assert_not_called()
        mocks['release'].assert_called_once_with('krea')

    def test_windows_absent_qwen_passes_even_when_tcp_probe_would_timeout(self):
        mocks = self.mocked_generation()
        mocks['listening'].return_value = {8188}
        mocks['connection'].side_effect = TimeoutError
        with patch.object(moss.os, 'name', 'nt'):
            result = moss.generate('a blue cup', seed=123)
        self.assertEqual(result['model'], 'moss')
        mocks['connection'].assert_not_called()
        mocks['claim'].assert_called_once_with('krea')

    def test_windows_unknown_snapshot_does_not_stop_or_submit(self):
        mocks = self.mocked_generation()
        mocks['listening'].side_effect = RuntimeError('无法确认本机文字服务监听状态，Moss已暂停')
        with patch.object(moss.os, 'name', 'nt'):
            with self.assertRaisesRegex(RuntimeError, '监听状态'):
                moss.generate('a blue cup', seed=123)
        mocks['acquire'].assert_not_called()
        mocks['claim'].assert_not_called()
        mocks['http'].assert_not_called()

    def test_windows_listener_checks_slots_without_tcp_probe(self):
        mocks = self.mocked_generation()
        mocks['listening'].return_value = {8080}
        mocks['connection'].side_effect = TimeoutError
        with patch.object(moss.os, 'name', 'nt'), \
                patch('urllib.request.urlopen', side_effect=lambda *args, **kwargs:
                      io.BytesIO(b'[{"is_processing": true}]')):
            with self.assertRaisesRegex(RuntimeError, '正在处理请求'):
                moss.generate('a blue cup', seed=123)
        mocks['connection'].assert_not_called()
        mocks['claim'].assert_not_called()
        mocks['http'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
