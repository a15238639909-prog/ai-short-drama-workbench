"""Cancellation/reset integration with retained fixtures and mocked model processes."""
import copy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

os.environ['V41_NO_MODEL'] = '1'
from api import gpu_api, saga_api, timeline, video_api, story
from cores import runtime_core as runtime, runtime_reset as reset, store, story_video_core
from cores.generation_recovery import RecoveryStore
from models import gpu_manager as gpu, krea_client


class RuntimeCacheResetTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='workbench_reset_', dir=os.environ.get('V41_RESET_TEST_ROOT')))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        reset.bind_thread(None)
        self.addCleanup(lambda: reset.bind_thread(None))
        for obj, name, value in ((reset, 'STATE', self.root/'cache/runtime_reset.json'),
                                 (store, 'DATA', self.root/'data'),
                                 (runtime, 'TASKS_DIR', self.root/'data/tasks'),
                                 (runtime, '_QUEUE', []),
                                 (gpu, '_OWNER_FILE', self.root/'cache/gpu_owner.json'),
                                 (gpu_api, 'FLAG', self.root/'cache/game_mode.flag'),
                                 (gpu_api, 'TASK', self.root/'cache/current_task.json')):
            self.stack.enter_context(patch.object(obj, name, value))
        runtime.TASKS_DIR.mkdir(parents=True)
        for module, name in ((saga_api, '_JOBS'), (timeline, '_JOBS'),
                             (video_api, '_PRODUCE_JOBS'), (story, '_S1_JOBS')):
            self.stack.enter_context(patch.object(module, name, {}))
        self.stack.enter_context(patch.object(saga_api, 'log'))

    def task(self, status='queued', number=1):
        value = {'id': 'task_%05d' % number, 'kind': 'fake', 'status': status,
                 'created': number, 'inputs': {}, 'done': 2, 'total': 5,
                 'runtime_generation': reset.generation(), 'output': {'path': 'keep.mp4'}}
        runtime.save(value)
        return value

    def mock_models(self):
        for name in ('stop_registered_models', 'stop_qwen', 'stop_comfy', 'stop_h3'):
            self.stack.enter_context(patch.object(gpu, name, return_value=[] if name=='stop_registered_models' else None))
        self.stack.enter_context(patch.object(gpu, 'pid_on_port', return_value=None))
        self.stack.enter_context(patch.object(gpu, 'stray_llama_pids', return_value=[]))
        self.stack.enter_context(patch.object(story_video_core, 'cancel_all_children', return_value={'errors': []}))
        self.stack.enter_context(patch.object(gpu_api, '_legacy_clear', return_value=0))

    def test_reset_cancels_all_queues_and_keeps_files_and_outputs(self):
        self.mock_models()
        active = self.task('running')
        complete = self.task('completed', 2)
        runtime._QUEUE.extend([active['id']])
        saga_api._JOBS['STORY_TEST'] = {'running': True, 'done': 3, 'total': 7}
        video_api._PRODUCE_JOBS['x'] = {'running': True}
        record = RecoveryStore().register('STORY_TEST', {'stage':'images'}, 'same')
        media = self.root/'saved.mp4'; media.write_bytes(b'preserve')
        result = gpu_api.r_clear(None, '/api/gpu/clear', {})
        self.assertTrue(result['ok'], result)
        self.assertEqual(runtime.list_tasks(), [])
        self.assertEqual(runtime._QUEUE, [])
        self.assertEqual(runtime.load(active['id'])['status'], 'cancelled')
        self.assertEqual(runtime.load(complete['id'])['output'], complete['output'])
        self.assertEqual(media.read_bytes(), b'preserve')
        self.assertTrue(RecoveryStore().load('STORY_TEST')['cancel'])
        self.assertIsNone(gpu.owner())
        self.assertFalse(json.loads(reset.STATE.read_text())['clearing'])
        self.assertEqual(result['data']['percent'], 0)

    def test_more_than_200_tasks_are_cleared_and_history_retained(self):
        for number in range(1, 206): self.task('paused', number)
        result = runtime.clear_all_tasks()
        self.assertEqual(result['cleared'], 205)
        self.assertEqual(len(runtime.list_tasks(None, include_cleared=True)), 205)
        self.assertEqual(runtime.list_tasks(), [])

    def test_late_handle_save_and_runner_error_cannot_revive_task(self):
        task = self.task('running')
        stale = copy.deepcopy(task)
        runtime.clear_all_tasks()
        with self.assertRaises(runtime.TaskPaused): runtime.TaskHandle(task['id']).step('late', 3, 5)
        stale.update(status='completed', done=5)
        runtime.save(stale)
        self.assertEqual(runtime.load(task['id'])['status'], 'cancelled')
        self.assertEqual(runtime.load(task['id'])['done'], 0)
        self.assertEqual(runtime.action(task['id'], 'retry')['status'], 'cancelled')
        self.assertEqual(runtime._QUEUE, [])

    def test_inflight_runner_failure_preserves_reset_tombstone(self):
        entered, proceed = threading.Event(), threading.Event()
        task = self.task()
        def runner(handle, value):
            entered.set(); proceed.wait(5)
            raise RuntimeError('late model connection error')
        with patch.object(runtime, 'RUNNERS', {'fake': runner}):
            worker = threading.Thread(target=runtime._run, args=(task['id'],))
            worker.start(); self.assertTrue(entered.wait(5))
            epoch = reset.begin(); runtime.clear_all_tasks(); reset.finish(epoch)
            proceed.set(); worker.join(5)
            self.assertFalse(worker.is_alive())
        actual = runtime.load(task['id'])
        self.assertEqual(actual['status'], 'cancelled')
        self.assertEqual(actual['error'], '')

    def test_old_epoch_cannot_launch_but_new_task_can_run(self):
        reset.assert_current()
        submitted = reset.worker(lambda: self.fail('old worker executed'))
        epoch = reset.begin()
        with self.assertRaises(reset.Cancelled): reset.assert_current(epoch)
        reset.finish(epoch)
        with self.assertRaises(reset.Cancelled): reset.assert_current()
        submitted()
        reset.bind_thread(None)
        with patch.object(runtime, 'RUNNERS', {'fake': lambda handle, task: {'new':True}}):
            task = runtime.create_task('fake', 'new', {})
            runtime._run(task['id'])
        self.assertEqual(runtime.load(task['id'])['status'], 'completed')

    def test_recovery_cannot_overwrite_cancel_or_new_registration(self):
        recovery = RecoveryStore()
        old = recovery.register('STORY_TEST', {}, 'one')
        stale = copy.deepcopy(old)
        old.update(status='cancelled', cancel=True, cleared_at=10)
        recovery.save(old)
        stale.update(status='running', cancel=False)
        recovery.save(stale)
        self.assertTrue(recovery.load('STORY_TEST')['cancel'])
        newer = recovery.register('STORY_TEST', {}, 'two')
        recovery.save(stale)
        self.assertEqual(recovery.load('STORY_TEST')['id'], newer['id'])
        self.assertTrue(list((recovery.root/'history').glob('*.json')))

    def test_cleared_job_view_ignores_late_progress_and_new_job_is_distinct(self):
        old = saga_api._start_job('STORY_TEST', 'old', 5)
        saga_api.clear_runtime_jobs()
        old.update(running=True, done=5, percent=100, err='late')
        view = saga_api._job_view(old)
        self.assertFalse(view['running']); self.assertEqual(view['percent'], 0)
        new = saga_api._start_job('STORY_TEST', 'new', 3)
        self.assertIsNot(new, old)
        self.assertTrue(new['running'])

    def test_cleanup_failure_keeps_gate_closed_and_retry_recovers(self):
        self.mock_models()
        gpu_api.FLAG.parent.mkdir(parents=True)
        gpu_api.FLAG.write_text('keep flag')
        with patch.object(reset, 'recycle_transient', side_effect=RuntimeError('Recycle Bin unavailable')):
            failed = gpu_api.r_clear(None, '/api/gpu/clear', {})
        self.assertFalse(failed['ok']); self.assertTrue(gpu_api.FLAG.exists())
        with self.assertRaises(reset.Cancelled): reset.assert_current(reset.generation())
        def fake_recycle(path):
            if path.exists(): path.rename(path.with_suffix('.recycled')); return True
            return False
        with patch.object(reset, 'recycle_transient', side_effect=fake_recycle):
            retried = gpu_api.r_clear(None, '/api/gpu/clear', {})
        self.assertTrue(retried['ok'], retried)
        self.assertFalse(gpu_api.FLAG.exists())

    def test_gpu_release_retains_file_and_respects_owner_generation(self):
        gpu._OWNER_FILE.parent.mkdir(parents=True)
        claim = {'pid':os.getpid(),'who':'krea','runtime_generation':reset.generation()}
        gpu._OWNER_FILE.write_text(json.dumps(claim))
        self.assertTrue(gpu.release('krea'))
        self.assertTrue(gpu._OWNER_FILE.exists())
        self.assertIsNone(json.loads(gpu._OWNER_FILE.read_text())['pid'])
        claim['runtime_generation']='newer'; gpu._OWNER_FILE.write_text(json.dumps(claim))
        self.assertFalse(gpu.release('krea'))

    def test_owner_reset_refuses_live_model(self):
        with patch.object(gpu,'pid_on_port',return_value=123), patch.object(gpu,'stray_llama_pids',return_value=[]):
            with self.assertRaises(RuntimeError): gpu.reset_owner()
        self.assertFalse(gpu._OWNER_FILE.exists())

    def test_cancelled_worker_cannot_stop_the_next_tasks_model(self):
        reset.assert_current()
        epoch = reset.begin(); reset.finish(epoch)
        with patch.object(gpu, 'pid_on_port') as probe:
            with self.assertRaises(reset.Cancelled): gpu.stop_qwen()
            probe.assert_not_called()

    def test_krea_always_releases_on_success_and_failure(self):
        with patch.dict(os.environ, {'V41_NO_MODEL':''}), patch.object(gpu, 'acquire'), patch.object(gpu,'release') as release:
            with patch.object(krea_client,'_generate',return_value={'output_path':'fake.png'}):
                self.assertEqual(krea_client.generate('test')['output_path'], 'fake.png')
            with patch.object(krea_client,'_generate',side_effect=RuntimeError('fake error')):
                with self.assertRaises(RuntimeError): krea_client.generate('test')
            self.assertEqual(release.call_count, 2)

    def test_registered_child_stopped_without_touching_other_processes(self):
        child = Mock(); child.poll.return_value=None
        with patch.object(story_video_core,'_CHILDREN', {'task_test':child}):
            result = story_video_core.cancel_all_children()
        child.terminate.assert_called_once(); child.wait.assert_called_once()
        self.assertEqual(result['terminated'], 1)

    def test_recycle_scope_excludes_projects_and_cache_media(self):
        for target in (self.root/'data/story.json', reset.STATE.parent/'image.png'):
            with self.assertRaises(ValueError): reset.recycle_transient(target)


if __name__ == '__main__': unittest.main()
