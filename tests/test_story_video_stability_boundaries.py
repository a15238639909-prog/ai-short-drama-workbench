"""Corrupt progress, completion, controls and policy-text regressions; no AI."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from cores import story_video_core as core, episode_contract as contract
from cores import runtime_core as runtime, runtime_reset


class StoryStabilityBoundaries(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='story_stability_boundary_'))

    def test_corrupt_existing_manifest_is_preserved_and_never_treated_as_empty(self):
        target = self.folder / 'manifest.json'
        for raw in ('{incomplete', '[]', 'null'):
            target.write_text(raw, encoding='utf-8')
            with patch.object(core.time, 'sleep'), self.assertRaisesRegex(RuntimeError, '原文件与片段保留'):
                core._load(target)
            self.assertEqual(target.read_text(encoding='utf-8'), raw)

    def test_transient_manifest_read_retries_and_missing_file_stays_compatible(self):
        self.assertEqual(core._load(self.folder / 'manifest.json'), {})
        error = PermissionError('temporary sharing conflict')
        error.winerror = 32
        with patch.object(Path, 'read_text', side_effect=[error, '{"segments":[]}']), patch.object(core.time, 'sleep'):
            self.assertEqual(core._load(self.folder / 'manifest.json'), dict(segments=[]))

    def test_completion_requires_all_segments_existing_film_and_decode_evidence(self):
        film = self.folder / 'film.mp4'
        film.write_bytes(b'fake completed media; no encoder')
        output = dict(output_path=str(film), full_decode_passed=True, audio_consistency_passed=True)
        manifest = dict(segments=[dict(index=1), dict(index=2)], output=output)
        task = dict(inputs=dict(segment_count=2))
        self.assertEqual(core._completed_output(manifest, task), output)
        for change in (dict(segments=[dict(index=1)]), dict(output=dict(anything=True)),
                       dict(output=dict(output, full_decode_passed=False)),
                       dict(output=dict(output, audio_consistency_passed=False))):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                core._completed_output(dict(manifest, **change), task)
        empty = self.folder / 'empty.mp4'
        empty.write_bytes(b'')
        with self.assertRaisesRegex(RuntimeError, '不存在或为空'):
            core._completed_output(dict(manifest, output=dict(output, output_path=str(empty))), task)

    def test_newer_failed_call_cannot_be_hidden_by_old_accepted_model_reply(self):
        from models import story_video_client
        work = self.folder / 'model'
        work.mkdir()
        (work/'accepted.json').write_text('{"ok":"old"}', encoding='utf-8')
        trace = work/'call_3_new/text_fixture'
        trace.mkdir(parents=True)
        (trace/'request.json').write_text('{}', encoding='utf-8')
        newer = (work/'accepted.json').stat().st_mtime_ns + 1_000_000_000
        os.utime(trace/'request.json', ns=(newer, newer))
        (trace/'error.json').write_text('{"error":"service unavailable"}', encoding='utf-8')
        schema = dict(type='object', properties=dict(ok=dict(type='string')), required=['ok'], additionalProperties=False)
        with patch.object(story_video_client, 'text', side_effect=RuntimeError('本地文字模型服务不可用')) as text:
            with self.assertRaisesRegex(RuntimeError, '服务不可用'):
                core.request_json(('检查', {}, schema), work)
        text.assert_called_once()

    def test_truncated_native_reply_is_not_reused_as_a_complete_success(self):
        from models import story_video_client
        work = self.folder / 'model'
        trace = work/'call_1_old/text_fixture'
        trace.mkdir(parents=True)
        (trace/'request.json').write_text('{}', encoding='utf-8')
        (trace/'response.json').write_text(json.dumps(dict(text='{"ok":"old"}',
            meta=dict(finish_reason='length'))), encoding='utf-8')
        schema = dict(type='object', properties=dict(ok=dict(type='string')), required=['ok'], additionalProperties=False)
        with patch.object(story_video_client, 'text', return_value='{"ok":"fresh"}') as text:
            self.assertEqual(core.request_json(('检查', {}, schema), work), dict(ok='fresh'))
        text.assert_called_once()

    def test_departure_policy_is_not_a_scene_action_but_real_exit_is_still_checked(self):
        template = ('subject_definitions:\n人物说明\n\nsummary:\n人物交谈\n\n'
                    'retention_analysis:\n遮挡、离场、观察失败时保留已知衣着。\n\n'
                    'detailed_description:\n%s\n\noverall_soundscape:\n环境声音\n\n'
                    'non_diegetic_music:\n无配乐')
        source = '她仍站在原处，轻轻点头。'
        self.assertEqual(contract.unsupported_departure(template % source, source), '')
        self.assertTrue(contract.unsupported_departure(template % '她转身离场。', source))
        self.assertTrue(contract.unsupported_departure((template % source).replace('人物交谈', '她离场'), source))
        self.assertEqual(contract.unsupported_departure(template % '她离场。', '她转身离场。'), '')

    def test_saved_departure_false_positive_is_rechecked_at_render_boundary(self):
        from api import segment_api
        prompt = ('subject_definitions:\n人物\n\nsummary:\n交谈\n\nretention_analysis:\n'
                  '遮挡、离场、观察失败时保留衣着。\n\ndetailed_description:\n'
                  '她仍在原地。\n\noverall_soundscape:\n风声\n\nnon_diegetic_music:\n无配乐')
        row = dict(plan=dict(flow='source-v1'), source_text='她仍在原地。', seconds=15,
                   scene='庭院', critical_problems=['本段原文未发生离场，提示词不可新增或预演离场动作：离场'])
        self.assertEqual(segment_api._render_critical_problems({}, {}, row, prompt, 1, 1), [])
        self.assertTrue(segment_api._render_critical_problems({}, {}, row,
            prompt.replace('她仍在原地。', '她转身离场。'), 1, 1))

    def test_late_exception_keeps_story_control_and_old_generation_cannot_finish(self):
        previous = getattr(runtime_reset._local, 'generation', None)
        try:
            with patch.object(runtime, 'TASKS_DIR', self.folder), patch.object(runtime_reset, 'STATE', self.folder/'reset.json'):
                runtime_reset._write(dict(generation='stability', clearing=False))
                runtime_reset.bind_thread('stability')
                for flag, expected in [('pause_requested', 'paused'), ('cancel_requested', 'cancelled')]:
                    task = dict(id='task_'+flag, kind='story_video', status='queued', inputs={}, output=None,
                                runtime_generation='stability', checkpoint={})
                    runtime.save(task)
                    def runner(handle, snapshot):
                        runtime.update(snapshot['id'], **{flag: True})
                        raise RuntimeError('late transport error')
                    with patch.object(runtime, 'RUNNERS', dict(story_video=runner)):
                        runtime._run(task['id'])
                    actual = runtime.load(task['id'])
                    self.assertEqual(actual['status'], expected)
                    self.assertIsNone(actual['output'])
                task = dict(id='task_old', kind='story_video', status='queued', inputs={}, output=None,
                            runtime_generation='stability', checkpoint={})
                runtime.save(task)
                def old_runner(handle, snapshot):
                    runtime_reset._write(dict(generation='new-generation', clearing=False))
                    return dict(must_not_publish=True)
                with patch.object(runtime, 'RUNNERS', dict(story_video=old_runner)):
                    runtime._run(task['id'])
                actual = runtime.load(task['id'])
                self.assertNotEqual(actual['status'], 'completed')
                self.assertIsNone(actual['output'])
        finally:
            runtime_reset.bind_thread(previous)

    def test_parent_worker_task_updates_are_cross_process_transactions(self):
        reset = self.folder / 'reset.json'
        reset.write_text(json.dumps(dict(generation='transaction-test', clearing=False)), encoding='utf-8')
        task = dict(id='task_atomic', kind='story_video', status='running', inputs={}, output=None,
                    runtime_generation='transaction-test', checkpoint={})
        (self.folder / 'task_atomic.json').write_text(json.dumps(task), encoding='utf-8')
        code = '''import os,sys,time
from pathlib import Path
from cores import runtime_core as r,runtime_reset as reset
r.TASKS_DIR=Path(sys.argv[1]);reset.STATE=r.TASKS_DIR/'reset.json'
reset.bind_thread('transaction-test')
handle=r.TaskHandle('task_atomic')
for index in range(30):
 if sys.argv[2]=='progress':handle.set(done=index,step_label='test-progress')
 else:r.update('task_atomic',pause_requested=True,detail='test-control')
 time.sleep(.001)
'''
        workers = [subprocess.Popen([sys.executable, '-B', '-c', code, str(self.folder), mode],
            cwd=str(core.ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) for mode in ('progress', 'control')]
        for worker in workers:
            out, error = worker.communicate(timeout=20)
            self.assertEqual(worker.returncode, 0, error.decode('utf-8', 'replace'))
        actual = json.loads((self.folder / 'task_atomic.json').read_text(encoding='utf-8'))
        self.assertEqual(actual['done'], 29)
        self.assertTrue(actual['pause_requested'])
        self.assertTrue((self.folder / '.task_state.lock').is_file())


if __name__ == '__main__':
    unittest.main()
