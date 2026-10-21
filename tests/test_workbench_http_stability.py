"""Real HTTP routing, isolated task state; no startup, worker, models or deletion."""
import importlib
import json
import os
import sys
import threading
import types
import unittest
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

os.environ['V41_NO_MODEL'] = '1'


class WorkbenchHTTPStabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The optional legacy tools are outside this test's scope. Importing
        # main's Handler must never run server.main() or restore saved jobs.
        prior_legacy = sys.modules.get('app')
        sys.modules['app'] = types.ModuleType('app')
        try:
            cls.app = importlib.import_module('server')
        finally:
            if prior_legacy is None:
                sys.modules.pop('app', None)
            else:
                sys.modules['app'] = prior_legacy
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), cls.app.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:%d' % cls.http.server_port

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(5)

    def request(self, path, body=None):
        request = Request(self.base + path,
                          data=None if body is None else json.dumps(body).encode('utf-8'),
                          headers={'Content-Type': 'application/json'})
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def test_current_story_script_is_served_without_cache(self):
        status, headers, body = self.request('/pages/story_video.js')
        self.assertEqual(status, 200)
        self.assertIn('no-store', headers.get('Cache-Control', ''))
        self.assertEqual(body, (Path(self.app.WEB) / 'pages/story_video.js').read_bytes())

    def test_capabilities_keep_audio_review_optional(self):
        status, _, body = self.request('/api/story-video/capabilities')
        self.assertEqual(status, 200)
        value = json.loads(body)
        self.assertTrue(value['ok'])
        self.assertEqual(value['data']['runtime_optimization'], 'story-video-flow-v2')
        self.assertTrue(value['data']['audio_review_optional'])
        self.assertFalse(value['data']['audio_review_enabled_default'])

    def test_synthetic_task_status_does_not_start_or_restore_work(self):
        from cores import runtime_core as runtime
        task = dict(id='task_safe_http', kind='story_video', status='paused',
                    inputs={}, checkpoint={}, done=1, total=2)
        with ExitStack() as stack:
            stack.enter_context(patch.object(runtime, 'list_tasks', return_value=[task]))
            stack.enter_context(patch.object(runtime, 'load', return_value=task))
            queue = stack.enter_context(patch.object(runtime, '_queue'))
            status, _, body = self.request('/api/story-video/status?id=task_safe_http')
        self.assertEqual(status, 200)
        value = json.loads(body)['data']
        self.assertEqual(value['task']['status'], 'paused')
        self.assertIsNone(value['output'])
        queue.assert_not_called()

    def test_unknown_action_is_rejected_without_queuing(self):
        from cores import runtime_core as runtime
        with patch.object(runtime, '_queue') as queue:
            status, _, body = self.request('/api/story-video/action',
                                           {'id': 'task_safe_http', 'action': 'unknown'})
        self.assertEqual(status, 400)
        self.assertFalse(json.loads(body)['ok'])
        queue.assert_not_called()

    def test_main_failure_reason_survives_http_progress(self):
        from api import saga_api
        job = dict(running=False, status='partial', done=1, total=2,
                   step='本轮已处理，第1段待补',
                   failure_reason='安全测试：输入参考绑定待修复')
        with patch.object(saga_api, '_JOBS', {'SAFE_HTTP': job}):
            status, _, body = self.request('/api/saga/SAFE_HTTP/progress', {})
        self.assertEqual(status, 200)
        value = json.loads(body)['data']
        self.assertIn('参考绑定', value['err'])
        self.assertIn('生成未完成', value['step'])
        self.assertLess(value['percent'], 100)
        self.assertEqual(job['step'], '本轮已处理，第1段待补')


if __name__ == '__main__':
    unittest.main()
