"""Persisted runtime cancellation regressions, isolated from production tasks.

Only tiny in-process runners execute. No worker or model is started; temporary
task JSON files are deliberately retained for inspection.
"""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from cores import runtime_core as runtime, runtime_reset


class StoryVideoRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = Path(tempfile.mkdtemp(prefix='story_video_runtime_'))

    @classmethod
    def tearDownClass(cls):
        print('RETAINED_RUNTIME_TEST_TASKS=' + str(cls.folder))

    def run_isolated(self, runner, suffix):
        folder = self.folder / self._testMethodName / suffix
        folder.mkdir(parents=True)
        original_tasks, original_runners = runtime.TASKS_DIR, runtime.RUNNERS
        previous_epoch = getattr(runtime_reset._local, 'generation', None)
        with patch.object(runtime, 'TASKS_DIR', folder), patch.object(
                runtime, 'RUNNERS', {'story_video_isolated': runner}), patch.object(
                runtime_reset, 'STATE', folder / 'runtime_reset.json'):
            epoch = 'isolated-runtime-test-epoch'
            runtime_reset._write(dict(generation=epoch, clearing=False))
            task = {'id': 'task_00001', 'kind': 'story_video_isolated', 'status': 'queued',
                    'title': '隔离状态测试', 'inputs': {}, 'checkpoint': {}, 'output': None,
                    'runtime_generation': epoch,
                    'created': time.time(), 'updated': time.time(), 'error': '', 'detail': '排队中'}
            runtime.save(task)
            try:
                runtime._run(task['id'])
            finally:
                runtime_reset.bind_thread(previous_epoch)
            actual = runtime.load(task['id'])
            self.assertTrue((folder / 'task_00001.json').is_file())
        self.assertIs(runtime.TASKS_DIR, original_tasks)
        self.assertIs(runtime.RUNNERS, original_runners)
        return actual

    def test_runner_return_preserves_cancelled_or_paused(self):
        for status in ('cancelled', 'paused'):
            with self.subTest(status=status):
                def runner(handle, task):
                    self.assertEqual(runtime.load(task['id'])['status'], 'running')
                    handle.set(status=status, detail='保留控制状态')
                    return {'must_not_be_published': True}

                actual = self.run_isolated(runner, status)
                self.assertEqual(actual['status'], status)
                self.assertEqual(actual['detail'], '保留控制状态')
                self.assertIsNone(actual['output'])

    def test_taskpaused_does_not_replace_cancelled(self):
        def runner(handle, task):
            handle.set(status='cancelled', detail='已取消')
            raise runtime.TaskPaused()

        actual = self.run_isolated(runner, 'cancelled_exception')
        self.assertEqual(actual['status'], 'cancelled')
        self.assertEqual(actual['detail'], '已取消')
        self.assertIsNone(actual['output'])
        self.assertGreaterEqual(actual['ended'], actual['started'])

    def test_normal_runner_still_completes(self):
        output = {'result': 'isolated success'}

        def runner(handle, task):
            handle.step('完成隔离步骤', 1, 1)
            return output

        actual = self.run_isolated(runner, 'normal')
        self.assertEqual(actual['status'], 'completed')
        self.assertEqual(actual['output'], output)
        self.assertEqual(actual['done'], 1)
        self.assertEqual(actual['total'], 1)
        self.assertGreaterEqual(actual['ended'], actual['started'])


if __name__ == '__main__':
    unittest.main()
