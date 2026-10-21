"""Zero-model checks. Fixtures stay in temp storage under the recycle-bin rule."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from models import story_video_client as client


class StoryVideoClientTests(unittest.TestCase):
    def setUp(self):
        # Do not use TemporaryDirectory: its implicit rmtree permanently deletes
        # fixtures. These small local traces are retained for explicit cleanup.
        self.root = Path(tempfile.mkdtemp(prefix='story_video_zero_'))
        self.owner_path = self.root / 'gpu_owner.json'
        self.owner_path.write_text('{}', encoding='utf-8')
        self.calls = []
        self.stack = []
        self._patch(client.gpu_manager, '_OWNER_FILE', self.owner_path)
        self._patch(client.gpu_manager, 'owner', side_effect=self.owner)
        self.acquire = self._patch(client.gpu_manager, 'acquire', side_effect=self.acquire_call)
        self.guard = self._patch(client.gpu_manager, 'guard', side_effect=RuntimeError('only 0.4 GB free after model load'))
        self.chat = self._patch(client.qwen_client, 'chat', return_value=(' {"ok": true} ', {'finish_reason': 'stop'}))
        self._patch(client.qwen_client, 'QWEN_URL', 'http://127.0.0.1:8080')
        self._patch(client.h3_client, 'H3_URL', 'http://127.0.0.1:8190')
        self._patch(client.gpu_manager, 'release', side_effect=AssertionError('shared release must not run'))
        self._patch(Path, 'unlink', side_effect=AssertionError('permanent unlink must not run'))
        self.stack.append(patch('shutil.rmtree', side_effect=AssertionError('rmtree must not run')))
        self.stack[-1].start()
        self.addCleanup(self.stop_patches)

    def _patch(self, target, name, *args, **kwargs):
        value = patch.object(target, name, *args, **kwargs)
        self.stack.append(value)
        return value.start()

    def stop_patches(self):
        for value in reversed(self.stack):
            value.stop()

    def owner(self):
        value = json.loads(self.owner_path.read_text(encoding='utf-8'))
        return value if value.get('pid') else None

    def acquire_call(self, who, **kwargs):
        self.calls.append(who)
        self.assertEqual(os.environ.get('HF_HUB_OFFLINE'), '1')
        self.assertEqual(os.environ.get('TRANSFORMERS_OFFLINE'), '1')
        self.owner_path.write_text(json.dumps({'who': who, 'pid': os.getpid()}), encoding='utf-8')

    def test_text_schema_trace_and_preserved_release(self):
        schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}}
        for _ in range(2):
            self.assertEqual(client.text('Write JSON.', {'goal': 'continue'}, self.root, schema=schema), ' {"ok": true} ')
        self.assertEqual(self.calls, ['qwen', 'qwen'])
        self.assertEqual(self.chat.call_args.kwargs['response_format'], {'type': 'json_object', 'schema': schema})
        folders = sorted(self.root.glob('text_*'))
        self.assertEqual(len(folders), 2)
        self.assertNotEqual(folders[0], folders[1])
        request = json.loads((folders[0] / 'request.json').read_text(encoding='utf-8'))
        self.assertEqual(json.loads(request['messages'][1]['content']), {'goal': 'continue'})
        self.assertEqual((folders[0] / 'response.txt').read_text(encoding='utf-8'), ' {"ok": true} ')
        self.assertIsNone(json.loads(self.owner_path.read_text(encoding='utf-8'))['pid'])
        self.assertTrue(self.owner_path.is_file())

    def test_vision_uses_local_image_content(self):
        from PIL import Image
        image_path = self.root / 'opening.png'
        Image.new('RGB', (64, 32), 'blue').save(image_path)
        client.vision('Record facts.', {'frame': 'opening'}, [image_path], self.root)
        content = self.chat.call_args.args[1]
        self.assertEqual(content[0]['type'], 'text')
        self.assertEqual(content[-1]['type'], 'image_url')
        self.assertTrue(content[-1]['image_url']['url'].startswith('data:image/jpeg;base64,'))
        with self.assertRaises(ValueError):
            client.vision('Observe.', {}, ['https://example.com/image.png'], self.root)
        self.assertEqual(self.chat.call_count, 1)

    def test_only_text_planning_and_correction_enable_deliberation(self):
        for payload in ({'count': 2}, {'correction': 'Repair the current JSON only.'}):
            with self.subTest(payload=payload):
                client.text('Plan or repair.', payload, self.root, max_tokens=700)
                options = self.chat.call_args.kwargs
                self.assertTrue(options['reasoning'])
                self.assertEqual(options['temperature'], 0.5)
                self.assertGreaterEqual(options['max_tokens'], 10000)
        client.text('Write this beat.', {'beat': {'index': 1}}, self.root, max_tokens=700)
        options = self.chat.call_args.kwargs
        self.assertFalse(options['reasoning'])
        self.assertEqual((options['temperature'], options['max_tokens']), (0.1, 700))
        from PIL import Image
        image_path = self.root/'policy_frame.png'
        Image.new('RGB', (16, 16), 'white').save(image_path)
        client.vision('Observe this frame.', {'stage': 'segment', 'correction': 'Keep visual facts.'},
                      [image_path], self.root, max_tokens=900)
        options = self.chat.call_args.kwargs
        self.assertFalse(options['reasoning'])
        self.assertEqual((options['temperature'], options['max_tokens']), (0.1, 900))

    def test_remote_endpoints_fail_before_model_calls(self):
        for target, name in ((client.qwen_client, 'QWEN_URL'), (client.h3_client, 'H3_URL')):
            with self.subTest(name=name), patch.object(target, name, 'https://api.example.com'):
                with self.assertRaises(ValueError):
                    if name == 'QWEN_URL':
                        client.text('Write.', {}, self.root)
                    else:
                        client.video('Continue.', trace_dir=self.root, recovery_key='clip1')
        self.chat.assert_not_called()
        self.acquire.assert_not_called()
        self.guard.assert_not_called()

    def test_truncated_thinking_retries_the_identical_request_once_without_thinking(self):
        schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}}
        metadata = {'finish_reason': 'length', 'reasoning': True,
                    'reasoning_content_len': 20000, 'content_len': 7}
        self.chat.side_effect = [('partial', metadata), ('{"ok":true}', {'finish_reason': 'stop', 'reasoning': False})]
        result = client.text('Plan the same complete story.', {'count': 10, 'previous_draft': 'unchanged'},
                             self.root, max_tokens=6000, schema=schema)
        self.assertEqual(result, '{"ok":true}')
        self.assertEqual(self.calls, ['qwen', 'qwen'])
        first_call, second_call = self.chat.call_args_list
        self.assertEqual(first_call.args, second_call.args)
        self.assertEqual(first_call.kwargs['max_tokens'], 10000)
        self.assertTrue(first_call.kwargs['reasoning'])
        self.assertFalse(second_call.kwargs['reasoning'])
        self.assertEqual({k: v for k, v in first_call.kwargs.items() if k != 'reasoning'},
                         {k: v for k, v in second_call.kwargs.items() if k != 'reasoning'})
        folders = sorted(self.root.glob('text_*'))
        self.assertEqual(len(folders), 2)
        first_request = json.loads((folders[0]/'request.json').read_text(encoding='utf-8'))
        retry_request = json.loads((folders[1]/'request.json').read_text(encoding='utf-8'))
        self.assertEqual(retry_request['messages'], first_request['messages'])
        self.assertEqual(retry_request['retry_reason'], 'thinking_output_truncated')
        self.assertEqual(retry_request['parent_trace'], str(folders[0].resolve()))
        self.assertEqual(json.loads((folders[0]/'response.json').read_text(encoding='utf-8'))['meta'], metadata)
        self.assertEqual((folders[0]/'response.txt').read_text(encoding='utf-8'), 'partial')
        self.assertTrue((folders[0]/'retry.json').is_file())
        self.assertIsNone(self.owner())

    def test_without_thinking_retry_is_bounded_and_incomplete_results_still_fail(self):
        for index, second in enumerate((('partial again', {'finish_reason': 'length', 'reasoning': False}),
                                        (' ', {'finish_reason': 'stop', 'reasoning': False}))):
            trace = self.root/str(index)
            before = self.chat.call_count
            self.chat.side_effect = [('partial', {'finish_reason': 'length', 'reasoning': True}), second]
            with self.subTest(second=second), self.assertRaisesRegex(RuntimeError, '为空或被截断'):
                client.text('Plan.', {'count': 10}, trace)
            self.assertEqual(self.chat.call_count-before, 2)
            self.assertEqual(len(list(trace.glob('text_*/response.json'))), 2)
            self.assertEqual(len(list(trace.glob('text_*/error.json'))), 1)

    def test_empty_normal_reply_reported_no_thinking_or_runtime_error_does_not_trigger_retry(self):
        cases = [(' ', {'finish_reason': 'stop', 'reasoning': True}),
                 ('partial', {'finish_reason': 'length', 'reasoning': False})]
        for reply in cases:
            before = self.chat.call_count
            self.chat.side_effect = None
            self.chat.return_value = reply
            with self.subTest(reply=reply), self.assertRaises(RuntimeError):
                client.text('Plan.', {'count': 10}, self.root)
            self.assertEqual(self.chat.call_count-before, 1)
        before = self.chat.call_count
        self.chat.side_effect = RuntimeError('backend connection failed')
        with self.assertRaisesRegex(RuntimeError, 'backend connection failed'):
            client.text('Plan.', {'count': 10}, self.root)
        self.assertEqual(self.chat.call_count-before, 1)

    def test_pubg_blocks_the_retry_before_a_second_model_call(self):
        self.chat.return_value = ('partial', {'finish_reason': 'length', 'reasoning': True})
        count = 0
        def acquire(who, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError('PUBG_RUNNING')
            return self.acquire_call(who, **kwargs)
        self.acquire.side_effect = acquire
        with self.assertRaisesRegex(RuntimeError, 'PUBG'):
            client.text('Plan.', {'count': 10}, self.root)
        self.assertEqual(self.acquire.call_count, 2)
        self.assertEqual(self.chat.call_count, 1)
        self.assertEqual(len(list(self.root.glob('text_*'))), 2)
        self.assertTrue(any('PUBG' in path.read_text(encoding='utf-8') for path in self.root.glob('text_*/error.json')))
        self.assertIsNone(self.owner())

    def test_pubg_block_and_incomplete_reply_remain_logged(self):
        with patch.object(client.gpu_manager, 'acquire', side_effect=RuntimeError('PUBG_RUNNING')):
            with self.assertRaisesRegex(RuntimeError, 'PUBG'):
                client.text('Write.', {}, self.root)
        self.chat.assert_not_called()
        self.chat.return_value = ('partial draft', {'finish_reason': 'length'})
        with self.assertRaisesRegex(RuntimeError, '截断'):
            client.text('Write.', {}, self.root)
        self.assertEqual(len(list(self.root.glob('text_*/error.json'))), 2)
        responses = list(self.root.glob('text_*/response.txt'))
        self.assertEqual(responses[0].read_text(encoding='utf-8'), 'partial draft')
        self.assertEqual(self.chat.call_count, 1)

    def test_warm_models_do_not_repeat_startup_memory_guard(self):
        client.text('Write using the loaded model.', {}, self.root)
        with patch.object(client.h3_client, 'generate', return_value={'output_path': 'warm.mp4'}):
            client.video('Continue using loaded H3.', trace_dir=self.root, recovery_key='warm-clip')
        self.assertEqual(self.calls, ['qwen', 'h3'])
        self.assertEqual(self.acquire.call_count, 2)
        self.guard.assert_not_called()

    def test_native_video_profile_and_restoration(self):
        original = (client.h3_client._sized, client.h3_client._aligned_frames,
                    client.h3_client.build_workflow)
        seen = []

        def generate(mode, prompt, **kwargs):
            seen.append(mode)
            self.assertEqual(kwargs['steps'], 10)
            self.assertEqual(client.h3_client._sized('16:9', '0.7'), (1152, 640))
            self.assertEqual(client.h3_client._aligned_frames(kwargs['seconds']) % 17, 5)
            return {'mode': mode, 'seconds': 15, 'frames': 360, 'output_path': 'raw.mp4'}

        with patch.object(client.h3_client, 'generate', side_effect=generate):
            first = client.video('Opening.', trace_dir=self.root, recovery_key='clip1')
            first_frame = self.root / 'last.png'
            first_frame.write_bytes(b'local fixture')
            second = client.video('Continue.', first_frame, 15, self.root, 'clip2')
        self.assertEqual(seen, ['t2va', 'i2va'])
        self.assertEqual((first['frames'], first['seconds']), (362, 362 / 24))
        self.assertEqual(first['requested_seconds'], 15)
        self.assertEqual((second['frames'], second['seconds']), (362, 362 / 24))
        self.assertEqual(second['requested_seconds'], 15)
        self.assertEqual(original, (client.h3_client._sized, client.h3_client._aligned_frames,
                                    client.h3_client.build_workflow))
        self.assertIsNone(self.owner())

    def test_video_error_restores_profile_and_other_owners_survive(self):
        original = (client.h3_client._sized, client.h3_client._aligned_frames,
                    client.h3_client.build_workflow)
        with patch.object(client.h3_client, 'generate', side_effect=RuntimeError('retain recovery receipt')):
            with self.assertRaisesRegex(RuntimeError, 'recovery'):
                client.video('Continue.', trace_dir=self.root, recovery_key='clip3')
        self.assertEqual(original, (client.h3_client._sized, client.h3_client._aligned_frames,
                                    client.h3_client.build_workflow))
        self.owner_path.write_text(json.dumps({'who': 'qwen', 'pid': os.getpid() + 1000}), encoding='utf-8')
        before = self.owner_path.read_text(encoding='utf-8')
        client.preserve_release()
        self.assertEqual(self.owner_path.read_text(encoding='utf-8'), before)


if __name__ == '__main__':
    unittest.main()
