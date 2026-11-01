"""In-memory adapter checks: no service/model calls and no file deletion."""
from collections import deque
from contextlib import ExitStack
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from cores import paths, runtime_reset
from models import anima_client, gpu_manager, krea_client, moss_client
from models import qwen_image21_client as client


def png(color, size=(8, 10)):
    stream = io.BytesIO()
    Image.new('RGB', size, color).save(stream, format='PNG')
    return stream.getvalue()


class _BytesWriter(io.BytesIO):
    def __init__(self, files, key):
        super().__init__()
        self.files, self.key = files, key

    def close(self):
        if not self.closed:
            self.files[self.key] = self.getvalue()
        super().close()


class _TextWriter(io.StringIO):
    def __init__(self, files, key):
        super().__init__()
        self.files, self.key = files, key

    def close(self):
        if not self.closed:
            self.files[self.key] = self.getvalue().encode('utf-8')
        super().close()


class Harness:
    """A real PNG is verified/copied through an entirely in-memory filesystem."""
    def __enter__(self):
        self.stack = ExitStack()
        self.target = Path('D:/q21-mock/target.png').resolve()
        self.references = [Path('D:/q21-mock/ref%d.png' % index).resolve() for index in range(3)]
        self.output_root = Path('D:/q21-mock/comfy/ComfyUI/output').resolve()
        self.artifacts = Path('D:/q21-mock/artifacts').resolve()
        self.source = self.output_root / 'result.png'
        self.files = {str(self.target): png('red'), str(self.source): png('white', (12, 14))}
        self.files.update({str(path): png(color) for path, color in zip(self.references, ('blue', 'green', 'black'))})
        self.original = dict(self.files)
        self.queues = deque()
        self.histories = deque()
        self.posted = False
        self.response = {'prompt_id': 'own-task'}
        self.completed = {'own-task': {'status': {'status_str': 'success', 'completed': True},
            'outputs': {client.SAVE_NODE: {'images': [{'filename': 'result.png', 'subfolder': '', 'type': 'output'}]}}}}
        self.available = {name: {} for name in ('UNETLoader', 'CLIPLoader', 'VAELoader',
            'QwenImage21Cache', 'TextEncodeQwenImage21', 'KSampler', 'VAEDecode', 'SaveImage', 'LoadImage')}
        for node, field, value in (('UNETLoader', 'unet_name', client.MODEL_FILE),
                                    ('CLIPLoader', 'clip_name', client.ENCODER_FILE),
                                    ('VAELoader', 'vae_name', client.VAE_FILE)):
            self.available[node] = {'input': {'required': {field: [[value]]}}}
        self.stack.enter_context(patch.object(Path, 'open', autospec=True, side_effect=self.open))
        self.stack.enter_context(patch.object(Path, 'mkdir', autospec=True))
        self.stack.enter_context(patch.object(Path, 'is_file', autospec=True,
                                            side_effect=lambda path: str(path.resolve()) in self.files))
        self.stack.enter_context(patch.object(Path, 'is_dir', autospec=True, return_value=True))
        self.stack.enter_context(patch.object(client, 'OUTPUT_ROOT', self.artifacts))
        self.stack.enter_context(patch.object(paths, 'get', side_effect=lambda key: str(self.output_root)
                                             if key == 'comfy_output' else ''))
        self.stack.enter_context(patch.object(krea_client, 'COMFY_URL', 'http://127.0.0.1:8188'))
        self.stack.enter_context(patch.object(krea_client, 'COMFY_DIR', 'D:/q21-mock/comfy'))
        self.local = self.stack.enter_context(patch.object(moss_client, 'local_guard'))
        self.resource = self.stack.enter_context(patch.object(moss_client, '_resource_guard'))
        self.stack.enter_context(patch.object(anima_client, 'idle_guard'))
        self.current = self.stack.enter_context(patch.object(runtime_reset, 'assert_current'))
        self.acquire = self.stack.enter_context(patch.object(gpu_manager, 'acquire'))
        self.release = self.stack.enter_context(patch.object(gpu_manager, 'release'))
        self.up = self.stack.enter_context(patch.object(krea_client, 'up', return_value=True))
        self.http = self.stack.enter_context(patch.object(krea_client, '_http', side_effect=self.request))
        self.stack.enter_context(patch('cores.age_policy.assert_model_request'))
        self.stack.enter_context(patch.object(client.time, 'sleep'))
        self.forbidden = []
        for module, name in ((gpu_manager, 'claim'), (gpu_manager, 'guard'), (gpu_manager, 'spawn'),
                              (gpu_manager, 'stop_comfy'), (gpu_manager, 'stop_qwen'), (gpu_manager, 'stop_h3'),
                              (krea_client, 'start_comfy')):
            self.forbidden.append(self.stack.enter_context(patch.object(module, name,
                side_effect=AssertionError('The editor must not start or stop services'))))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def open(self, path, mode='r', *args, **kwargs):
        key = str(path.resolve())
        if mode in ('rb', 'r'):
            if key not in self.files:
                raise FileNotFoundError(key)
            return io.BytesIO(self.files[key]) if 'b' in mode else io.StringIO(self.files[key].decode('utf-8'))
        if mode in ('xb', 'x'):
            if key in self.files:
                raise FileExistsError(key)
            return _BytesWriter(self.files, key) if 'b' in mode else _TextWriter(self.files, key)
        raise AssertionError('Unexpected filesystem mutation: ' + mode)

    def request(self, method, path, data=None, timeout=60):
        if method == 'GET' and path == '/queue':
            return self.queues.popleft() if self.queues else {'queue_running': [], 'queue_pending': []}
        if method == 'GET' and path == '/object_info':
            return self.available
        if method == 'POST' and path == '/prompt':
            self.posted = True
            return self.response
        if method == 'GET' and path == '/history/own-task':
            return self.histories.popleft() if self.histories else self.completed
        raise AssertionError('Unexpected service request: %s %s' % (method, path))

    def run(self, **kwargs):
        return client.edit(self.target, '只修正人物面部，保留原分镜', **kwargs)

    def assert_originals(self, case):
        for name, content in self.original.items():
            case.assertEqual(self.files[name], content)
        for call in self.forbidden:
            call.assert_not_called()


class WorkflowTests(unittest.TestCase):
    def test_original_sampling_and_direct_latent_are_retained(self):
        graph = client.build_workflow('target.png', '编辑说明', seed=42)
        self.assertEqual(graph['679']['inputs'], dict(model=['681', 0], positive=['705', 0],
            negative=['705', 1], latent_image=['705', 2], seed=42, steps=25, cfg=1,
            sampler_name='euler', scheduler='simple', denoise=1))
        self.assertEqual(graph['681']['inputs'], dict(model=['685', 0], device='auto', dtype='default'))
        self.assertEqual(graph['705']['inputs']['resolution'], 0)
        self.assertEqual(graph['685']['inputs']['unet_name'], 'Qwen21-rayQwen21_v1_int8_convrot.safetensors')
        self.assertEqual(graph['675']['inputs']['clip_name'], 'qwen3vl_8b_int8_convrot.safetensors')
        self.assertEqual(graph['676']['inputs']['vae_name'], 'qwen_image_2.1_vae_bf16.safetensors')
        self.assertFalse(any('lora' in row['class_type'].lower() for row in graph.values()))

    def test_target_is_first_and_three_references_have_separate_inputs(self):
        graph = client.build_workflow('target.png', '编辑说明', ['r1.png', 'r2.png', 'r3.png'], seed=1)
        for index, filename in enumerate(('target.png', 'r1.png', 'r2.png', 'r3.png'), 1):
            node = str(800 + index)
            self.assertEqual(graph['705']['inputs']['images.image_%d' % index], [node, 0])
            self.assertEqual(graph[node]['inputs']['image'], filename)
        with self.assertRaises(ValueError):
            client.build_workflow('target.png', '说明', ['r.png'] * 4)

    def test_invalid_inputs_do_not_create_files(self):
        with patch.object(Path, 'open', side_effect=AssertionError('build must not do I/O')):
            for value in ('../image.png', '/image.png', 'D:/image.png', 'a\\b.png'):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    client.build_workflow(value, '说明')
            for seed in (True, -1, 2 ** 64, '42'):
                with self.subTest(seed=seed), self.assertRaises(ValueError):
                    client.build_workflow('image.png', '说明', seed=seed)
            for resolution in (-1, True, 1.5):
                with self.subTest(resolution=resolution), self.assertRaises(ValueError):
                    client.build_workflow('image.png', '说明', resolution=resolution)
            with self.assertRaises(ValueError):
                client.build_workflow('image.png', ' ')
            graph = client.build_workflow('image.png', '可正常编译', seed=2)
            self.assertEqual(graph['705']['inputs']['prompt'], '可正常编译')

    def test_graphs_are_independent_and_all_links_resolve(self):
        first = client.build_workflow('target.png', '第一版', seed=1)
        second = client.build_workflow('target.png', '第二版', seed=2)
        self.assertNotEqual(first[client.SAVE_NODE], second[client.SAVE_NODE])
        self.assertEqual(first['679']['inputs']['seed'], 1)
        for row in second.values():
            for value in row['inputs'].values():
                if isinstance(value, list):
                    self.assertIn(value[0], second)


class EditTests(unittest.TestCase):
    def test_success_preserves_inputs_and_saves_verified_unique_outputs(self):
        with Harness() as h:
            first = h.run(references=h.references, seed=42)
            second = h.run(seed=43)
            self.assertNotEqual(first['output_path'], second['output_path'])
            self.assertEqual(h.files[first['output_path']], h.original[str(h.source)])
            self.assertEqual((first['width'], first['height'], first['input_count']), (12, 14, 4))
            metadata_text = h.files[first['metadata_path']].decode('utf-8')
            metadata = json.loads(metadata_text)
            self.assertEqual(metadata['sha256'], hashlib.sha256(h.original[str(h.source)]).hexdigest())
            self.assertEqual([item['sha256'] for item in metadata['inputs']],
                [hashlib.sha256(h.original[str(path)]).hexdigest() for path in [h.target] + h.references])
            self.assertNotIn(str(h.target), metadata_text)
            self.assertNotIn('只修正人物面部', metadata_text)
            self.assertEqual(h.local.call_count, 4)
            self.assertEqual(h.acquire.call_count, 2)
            self.assertEqual(h.release.call_count, 2)
            h.assert_originals(self)

    def test_busy_queue_rejects_before_input_copy_and_releases(self):
        for field in ('queue_running', 'queue_pending'):
            with self.subTest(field=field), Harness() as h:
                queue = {'queue_running': [], 'queue_pending': []}
                queue[field] = [[7, 'someone-else']]
                h.queues.append(queue)
                with patch.object(client, '_copy_inputs') as copy, self.assertRaisesRegex(RuntimeError, '已有任务'):
                    h.run()
                copy.assert_not_called()
                self.assertFalse(h.posted)
                h.release.assert_called_once_with('krea')
                h.assert_originals(self)

    def test_unknown_queue_state_is_not_treated_as_idle(self):
        for queue in (None, {}, {'queue_running': [], 'queue_pending': None}, []):
            with self.subTest(queue=queue), Harness() as h:
                h.queues.append(queue)
                with self.assertRaisesRegex(RuntimeError, '队列状态未知'):
                    h.run()
                self.assertFalse(h.posted)
                h.release.assert_called_once_with('krea')
                self.assertEqual(h.files, h.original)

    def test_queue_is_checked_again_after_inputs_are_copied(self):
        with Harness() as h:
            h.queues.extend(({'queue_running': [], 'queue_pending': []},
                             {'queue_running': [], 'queue_pending': [[1, 'foreign']]}))
            with self.assertRaisesRegex(RuntimeError, '已有任务'):
                h.run()
            self.assertFalse(h.posted)
            self.assertGreater(len(h.files), len(h.original))
            h.release.assert_called_once_with('krea')
            h.assert_originals(self)

    def test_queue_network_failure_is_not_treated_as_idle(self):
        with Harness() as h:
            h.http.side_effect = OSError('unavailable')
            with self.assertRaisesRegex(RuntimeError, '无法确认ComfyUI队列'):
                h.run()
            self.assertFalse(h.posted)
            h.release.assert_called_once_with('krea')

    def test_absent_service_does_not_start_or_acquire(self):
        with Harness() as h:
            h.up.return_value = False
            with self.assertRaisesRegex(RuntimeError, '不会自动启动'):
                h.run()
            h.acquire.assert_not_called()
            h.release.assert_not_called()
            h.assert_originals(self)

    def test_remote_or_different_port_cannot_use_local_files(self):
        for url in ('http://example.com:8188', 'http://127.0.0.1:8190',
                    'http://user:secret@127.0.0.1:8188', 'http://127.0.0.1:8188/proxy'):
            with self.subTest(url=url), Harness() as h, patch.object(krea_client, 'COMFY_URL', url):
                with self.assertRaisesRegex(RuntimeError, '仅连接本机'):
                    h.run()
                h.http.assert_not_called()
                h.acquire.assert_not_called()

    def test_missing_model_fails_before_input_copy(self):
        with Harness() as h:
            h.available['UNETLoader']['input']['required']['unet_name'] = [['another-model']]
            with self.assertRaisesRegex(RuntimeError, '未发现所需模型'):
                h.run()
            self.assertEqual(h.files, h.original)
            self.assertFalse(h.posted)
            h.release.assert_called_once_with('krea')

    def test_pubg_is_rechecked_before_sampling(self):
        with Harness() as h:
            h.local.side_effect = [None, RuntimeError('PUBG_RUNNING')]
            with self.assertRaisesRegex(RuntimeError, 'PUBG_RUNNING'):
                h.run()
            self.assertFalse(h.posted)
            h.release.assert_called_once_with('krea')

    def test_execution_error_is_never_adopted_as_success_or_retried(self):
        with Harness() as h:
            h.completed['own-task']['status'] = {'status_str': 'error', 'completed': False}
            with self.assertRaises(client.QwenImage21Error) as caught:
                h.run()
            self.assertEqual(caught.exception.prompt_id, 'own-task')
            self.assertEqual(sum(call.args[:2] == ('POST', '/prompt') for call in h.http.call_args_list), 1)
            self.assertFalse(any(str(h.artifacts) in key for key in h.files))
            h.release.assert_called_once_with('krea')
            h.assert_originals(self)

    def test_foreign_history_is_ignored_until_own_result_arrives(self):
        with Harness() as h:
            h.histories.append({'foreign-task': h.completed['own-task']})
            result = h.run()
            self.assertEqual(result['prompt_id'], 'own-task')
            self.assertEqual(sum(call.args[:2] == ('GET', '/history/own-task') for call in h.http.call_args_list), 2)

    def test_history_transport_error_retains_task_id_without_resubmission(self):
        with Harness() as h:
            def request(method, path, data=None, timeout=60):
                if path == '/history/own-task':
                    raise TimeoutError('connection timed out')
                return h.request(method, path, data, timeout)
            h.http.side_effect = request
            with self.assertRaisesRegex(client.QwenImage21Error, '后台任务状态未知') as caught:
                h.run()
            self.assertEqual(caught.exception.prompt_id, 'own-task')
            self.assertIsInstance(caught.exception.__cause__, TimeoutError)
            self.assertEqual(sum(call.args[:2] == ('POST', '/prompt') for call in h.http.call_args_list), 1)
            self.assertFalse(any(str(h.artifacts) in key for key in h.files))
            h.release.assert_called_once_with('krea')
            h.assert_originals(self)

    def test_completed_without_final_output_is_an_error(self):
        with Harness() as h:
            h.completed['own-task']['outputs'] = {'preview': {'images': [{'filename': 'result.png'}]}}
            with self.assertRaisesRegex(RuntimeError, '没有唯一的最终PNG'):
                h.run()
            h.release.assert_called_once_with('krea')

    def test_server_output_must_stay_in_configured_output_directory(self):
        bad_images = ({'filename': '../result.png'}, {'filename': 'D:\\outside.png'},
                      {'filename': 'result.png', 'subfolder': '..'},
                      {'filename': 'result.png', 'subfolder': 'D:/outside'},
                      {'filename': 'result.png', 'type': 'temp'})
        for image in bad_images:
            with self.subTest(image=image), Harness() as h:
                h.completed['own-task']['outputs'][client.SAVE_NODE]['images'] = [image]
                with self.assertRaises(client.QwenImage21Error):
                    h.run()
                self.assertFalse(any(str(h.artifacts) in key for key in h.files))
                h.release.assert_called_once_with('krea')

    def test_invalid_png_is_rejected_before_artifact_adoption(self):
        with Harness() as h:
            h.files[str(h.source)] = b'not a PNG image'
            with self.assertRaisesRegex(RuntimeError, '有效PNG'):
                h.run()
            self.assertFalse(any(str(h.artifacts) in key for key in h.files))
            h.release.assert_called_once_with('krea')

    def test_cancellation_preserves_task_id_and_releases_without_interrupt(self):
        with Harness() as h:
            def current():
                if h.posted:
                    raise runtime_reset.Cancelled('cancelled for test')
            h.current.side_effect = current
            with self.assertRaises(runtime_reset.Cancelled) as caught:
                h.run()
            self.assertEqual(caught.exception.prompt_id, 'own-task')
            self.assertFalse(any(str(h.artifacts) in key for key in h.files))
            h.release.assert_called_once_with('krea')
            h.assert_originals(self)

    def test_timeout_retains_remote_task_without_retry_or_global_interrupt(self):
        with Harness() as h, patch.object(client.time, 'monotonic', side_effect=[0, 0, 0, 2]):
            h.histories.append({})
            with self.assertRaisesRegex(client.QwenImage21Error, '等待超时') as caught:
                h.run(timeout=1)
            self.assertEqual(caught.exception.prompt_id, 'own-task')
            self.assertEqual(sum(call.args[:2] == ('POST', '/prompt') for call in h.http.call_args_list), 1)
            h.release.assert_called_once_with('krea')
            h.assert_originals(self)


if __name__ == '__main__':
    unittest.main()
