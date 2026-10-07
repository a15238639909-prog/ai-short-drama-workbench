"""Run in an isolated source copy: V41_RELEASE_TEST=1 V41_NO_MODEL=1.

This exercises real HTTP routing and local save/read operations, without inference.
"""
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import urllib.request


@unittest.skipUnless(os.environ.get('V41_RELEASE_TEST') == '1', 'Use an isolated release copy')
class BundledToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert os.environ.get('V41_NO_MODEL') == '1'
        import server
        cls.server = server
        cls.app = server._APP
        assert cls.app is not None, 'Bundled app failed to import'
        assert Path(cls.app.__file__).parent == server.ROOT / '_legacy_8848'
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.origin = 'http://127.0.0.1:%s' % cls.http.server_address[1]
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=5)

    def request(self, route, payload=None, raw=False):
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(self.origin + route, data=data,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=5) as response:
            result = response.read().decode('utf-8')
        return result if raw else json.loads(result)

    def test_all_five_entries(self):
        shell = self.request('/', raw=True)
        self.assertIn('/pages/legacy.js', shell)
        navigation = self.request('/shell.js', raw=True)
        for tab in ('chat', 'vchar', 'prompt', 'motion', 'director'):
            self.assertTrue('"#' + tab + '"' in navigation, tab)
        for tab in ('chat', 'vchar', 'prompt', 'director'):
            html = self.request('/legacy/?tab=' + tab, raw=True)
            self.assertIn("var P='/legacy'", html)
            self.assertIn('showTab(t)', html)
            self.assertIn('promptModel', html)
        self.assertTrue(self.request('/api/motion/status')['ok'])

    def test_chat_save_and_read(self):
        self.assertEqual(self.request('/legacy/api/chat_persona')['persona'], '')
        saved = self.request('/legacy/api/chat_save', {
            'title': 'release-test', 'messages': [{'role': 'user', 'content': 'hello'}]})
        self.assertTrue(saved['ok'])
        loaded = self.request('/legacy/api/chat_load?id=' + saved['id'])
        self.assertIn('hello', json.dumps(loaded))

    def test_character_save_and_edit(self):
        rec = self.request('/legacy/api/vchar_save', {'name': 'Release Test', 'age': '25',
                           'persona': 'A botanist', 'visualCard': 'Brown hair, green coat'})['char']
        self.request('/legacy/api/vchar_save', {'id': rec['id'], 'name': 'Updated Test'})
        saved = next(c for c in self.request('/legacy/api/vchar/list') if c['id'] == rec['id'])
        self.assertEqual(saved['name'], 'Updated Test')
        self.assertEqual(saved['age'], '25')

    def test_model_scoped_presets(self):
        for model, value in (('krea2', '中文测试'), ('anima', 'test, forest')):
            self.assertTrue(self.request('/legacy/api/savepreset', {
                'model': model, 'name': 'release-test', 'system': value})['ok'])
        presets = self.request('/legacy/api/presets')
        by_key = {(p['model'], p['name']): p['system'] for p in presets}
        self.assertEqual(by_key['krea2', 'release-test'], '中文测试')
        self.assertEqual(by_key['anima', 'release-test'], 'test, forest')
        self.assertIn(('anima', 'Anima·人设三视图＋面部特写'), by_key)
        self.assertTrue(self.request('/api/image/anima/config')['ok'])

    def test_director_workflows(self):
        core = self.app.video_gen_core
        self.assertIsNotNone(core)
        self.assertTrue(self.request('/legacy/api/video_presets'))
        for mode, images in (('t2va', []), ('i2va', ['first.png']),
                             ('fl2va', ['first.png', 'last.png']), ('ref2va', ['ref.png'])):
            with patch.object(core, '_copy_asset_to_input', side_effect=lambda value, label: value):
                graph, meta = core.build_workflow('A bird flies through a forest', mode=mode,
                    images=images, image_ratio=16/9, mp=.4, seed=1, seconds=5)
            self.assertEqual(meta['mode'], mode)
            core._validate_graph(graph)
        self.assertTrue(self.request('/legacy/api/video_gen_list')['ok'])

    def test_shared_model_paths(self):
        from cores import paths
        self.assertEqual(self.app.Q36_MODEL_GGUF, paths.get('model_gguf'))
        self.assertEqual(self.app.COMFY_DIR, paths.get('comfy_dir'))
        self.assertNotIn('F:', str(self.app.video_gen_core.H3_ROOT))
        self.assertTrue(Path(self.app.WF_FILE).is_file())

    def test_generation_guards(self):
        for fn in (self.app.qwen36_start, self.app.qwen25_start,
                   self.app.comfy_start, self.app.video_gen_core.service_start):
            with self.assertRaisesRegex(RuntimeError, 'V41_NO_MODEL'):
                fn()

    def test_empty_missing_models_no_side_effect(self):
        with patch.object(self.app, 'qwen36_up', return_value=False), \
             patch.object(self.app, 'Q36_SERVER_EXE', ''), \
             patch.object(self.app.video_gen_core, 'service_stop') as stop:
            with self.assertRaisesRegex(RuntimeError, 'config.json'):
                self.app._qwen36_start_locked()
            stop.assert_not_called()


if __name__ == '__main__':
    unittest.main()
