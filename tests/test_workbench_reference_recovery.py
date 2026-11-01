"""Workbench reference recovery integration, with safe synthetic adults only.

Real reference packing, request binding and batch status run against retained
fixture files. All project reads, HTTP, models and media processes are mocked.
"""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from api import saga_api as saga, segment_api as segment
from cores import (asset_core, authoring, episode_ledger, episode_merge,
                   manual_story, reference_ready, saga_core, shot_edit,
                   story_core, video_continuity)
from models import h3_client
try:
    from tests import test_generation_recovery as recovery_fixture
    from tests import test_segment_render_recovery as render_fixture
    from tests import test_h3_request_recovery as h3_fixture
except ImportError:
    import test_generation_recovery as recovery_fixture
    import test_segment_render_recovery as render_fixture
    import test_h3_request_recovery as h3_fixture

REAL_NEW_PACK = segment.new_pack
REAL_ASSERT_REFERENCES = reference_ready.assert_references


class WorkbenchReferenceRecoveryTests(unittest.TestCase):
    job = recovery_fixture.RecoveryTests.job
    save = render_fixture.SegmentRenderRecoveryTests.save
    current = render_fixture.SegmentRenderRecoveryTests.current
    result = render_fixture.SegmentRenderRecoveryTests.result

    def process_result(self, command, **options):
        if command[0] == 'ffprobe':
            self.duration = self.media_durations.get(str(command[-1]), 8)
        return render_fixture.SegmentRenderRecoveryTests.process_result(self, command, **options)

    def setUp(self):
        recovery_fixture.RecoveryTests.setUp(self)
        render_fixture.SegmentRenderRecoveryTests.setUp(self)
        self.sid = 'WORKBENCH_REFERENCE_FIXTURE'
        self.count = 11
        self.media_durations = {}
        self.generated_rows = []
        self.submission_snapshots = {}
        self.story = dict(story_id=self.sid, settings=dict(pov='第一视角POV', camera_owner='林舟'),
            saga=dict(episodes=[dict(no=2, prose='三十岁的林舟与二十八岁的夏禾在图书馆讨论旅行路线。')]))
        self.prompt = ('subject_definitions:\n'
            '<Subject 1> is the adult traveller Xiahe from <Picture 1>.\n'
            '<Subject 2> is the environment from <Picture 2>.\n\n'
            'summary:\nTwo adults discuss a route in the library.\n\n'
            'retention_analysis:\n<Subject 1>: identity. <Subject 2>: architecture.\n\n'
            'detailed_description:\n[Shot 1] Xiahe points at the map and says hello.\n\n'
            'overall_soundscape:\nQuiet footsteps and ordinary adult conversation.\n\n'
            'non_diegetic_music:\nN/A')
        base = dict(no=1, prompt=self.prompt, seconds=8, chars=['夏禾'], scene='图书馆',
                    source_text='二十八岁的夏禾指着地图，对三十岁的林舟说：“我们明天出发。”',
                    plan=dict(flow='source-v1'))
        self.timeline = dict(scenes=[dict(no=1, segments=[dict(copy.deepcopy(base), no=n)
            for n in range(1, self.count + 1)])])
        self.assets = dict(characters=[dict(name='林舟', character_id='CAMERA', age=30),
                                      dict(name='夏禾', character_id='VISIBLE', age=28)],
            scenes=[dict(name='图书馆', scene_id='LIBRARY')],
            visuals=[dict(owner_id='VISIBLE', status='adopted', kind='character', path=str(self.refs[0])),
                     dict(owner_id='LIBRARY', status='adopted', kind='scene', path=str(self.refs[1]))])
        self.merged = self.directory / 'joined.mp4'
        self.merged.write_bytes(b'mocked merged adult conversation')
        patches = [
            patch.object(story_core, 'get_story', side_effect=lambda sid: copy.deepcopy(self.story)),
            patch.object(asset_core, 'list_assets', side_effect=lambda sid, kind: copy.deepcopy(self.assets[kind])),
            patch.object(saga_core, 'ep_timeline', side_effect=lambda *a: copy.deepcopy(self.timeline)),
            patch.object(saga_core, 'seg_video_path', side_effect=lambda sid, ep, scene_no, no, **kw:
                         str(self.directory / ('episode_%d_segment_%d.mp4' % (ep, no)))),
            patch.object(segment, 'new_pack', side_effect=REAL_NEW_PACK),
            patch.object(reference_ready, 'assert_references', side_effect=REAL_ASSERT_REFERENCES),
            patch.object(reference_ready, 'resolve_scene_card', return_value=self.assets['scenes'][0]),
            patch.object(reference_ready, 'ensure_characters'),
            patch.object(authoring, 'use_new_pipeline', return_value=True),
            patch.object(authoring, 'total_segments', side_effect=lambda *a: self.count),
            patch.object(authoring, 'next_prompts', side_effect=lambda *a, **kw: ([], self.count)),
            patch.object(episode_ledger, 'build'), patch.object(saga, '_ep_scene_images'),
            patch.object(segment, 'render_opening_all', return_value=(0, '')),
            patch.object(manual_story, 'enabled', return_value=False), patch.object(shot_edit, 'approve'),
            patch.object(episode_merge, 'episode_videos', side_effect=lambda *a: [
                (row['no'], row['video']) for row in self.rows() if row.get('video') and not row.get('stale_video')]),
        ]
        for item in patches:
            self.stack.enter_context(item)
        self.merge = self.stack.enter_context(patch.object(episode_merge, 'merge_episode', return_value=str(self.merged)))
        self.generate.side_effect = self.generate_fixture

    def rows(self):
        return self.timeline['scenes'][0]['segments']

    def generate_fixture(self, mode, prompt, **options):
        video = self.directory / ('native_%02d.mp4' % (len(self.generated_rows) + 1))
        video.write_bytes(b'mocked adult conversation')
        key = options['recovery_key']
        pending = next(row['render_request'] for row in self.rows()
                       if (row.get('render_request') or {}).get('key') == key)
        self.assertEqual(pending['final_prompt'], prompt)
        self.assertEqual(pending['final_refs'], options['images'])
        self.assertEqual(options['images'][:2], list(map(str, self.refs)))
        video_continuity.validate_prompt_references(prompt, options['images'])
        self.submission_snapshots[key] = copy.deepcopy(pending)
        self.generated_rows.append(dict(key=key, path=str(video), images=list(options['images'])))
        result = dict(self.result(), output_path=str(video))
        self.receipts[key] = dict(status='completed', result=copy.deepcopy(result))
        return result

    def run_batch(self):
        job = self.job(sid=self.sid, phase='video')
        saga._run_registered_make(self.sid, job)
        return job

    def test_real_two_picture_pack_generates_eleven_segments_and_one_merge(self):
        self.assertEqual(segment.new_pack(self.sid, self.rows()[0], ep=2), list(map(str, self.refs)))
        old_bytes = self.old.read_bytes()
        self.rows()[0].update(video=str(self.old), stale_video=True, end_frame=str(self.old_frame))
        job = self.run_batch()
        self.assertEqual(job['status'], 'completed', job.get('err'))
        self.assertEqual(len(self.generated_rows), 11)
        self.merge.assert_called_once_with(self.sid, 2, dst=None, skip_invalid=True, report={})
        self.assertEqual(len({row['video'] for row in self.rows()}), 11)
        self.assertEqual(self.old.read_bytes(), old_bytes)
        self.assertEqual(self.rows()[0]['render_history'][0]['video'], str(self.old))
        for row in self.rows():
            request = row['render_request']
            before = self.submission_snapshots[request['key']]
            for key in ('key', 'input_signature', 'final_prompt', 'final_refs', 'resolved_params'):
                self.assertEqual(request[key], before[key])
            self.assertEqual(request['status'], 'completed')

    def test_preparation_reference_error_continues_ten_other_segments_and_partial_merge(self):
        self.rows()[0]['prompt'] = self.prompt.replace('<Picture 2>', '<Picture 3>')
        job = self.run_batch()
        self.assertEqual(self.generate.call_count, 10)
        self.merge.assert_called_once()
        self.assertIn('_部分', self.merge.call_args.kwargs['dst'])
        self.assertEqual(len(self.generated_rows), 10)
        self.assertEqual(job['status'], 'partial')
        self.assertEqual(job['saved_video_count'], 10)
        self.assertEqual(job['expected_segments'], 11)
        self.assertEqual(job['failed_render_segments'], [1])
        self.assertEqual(job['unexecuted_render_segments'], [])
        self.assertEqual(job['pending_render_segments'], [1])
        self.assertEqual(job['render_submission_state'], 'not_submitted')
        self.assertEqual(job['failure_stage'], 'video_preflight')
        self.assertLess(saga._job_view(job)['percent'], 100)
        self.assertIn('未绑定的图片编号', str(job))
        self.assertNotIn('已完成视频保留', job.get('err', ''))
        self.assertFalse(job['_recovery'].get('external_inflight'))
        self.assertFalse(self.rows()[0].get('video'))
        self.assertFalse(self.rows()[0].get('render_request'))
        self.assertTrue(all(row.get('video') for row in self.rows()[1:]))

    def test_second_segment_terminal_failure_recovers_without_replacing_first(self):
        self.count = 2
        self.timeline['scenes'][0]['segments'] = self.rows()[:2]
        failed = []
        def fail_second(mode, prompt, **options):
            if len(self.generated_rows) == 1:
                failed.append(options['recovery_key'])
                raise RuntimeError('H3 工作流失败：fixture temporary technical failure')
            return self.generate_fixture(mode, prompt, **options)
        self.generate.side_effect = fail_second
        first_job = self.run_batch()
        self.assertNotEqual(first_job['status'], 'completed')
        self.assertEqual(len(failed), 3, first_job.get('err'))
        first = copy.deepcopy(self.rows()[0])
        frozen_second = copy.deepcopy(self.rows()[1]['render_request'])
        first_bytes = Path(first['video']).read_bytes()
        self.generate.side_effect = self.generate_fixture
        second_job = self.run_batch()
        self.assertEqual(second_job['status'], 'completed')
        self.assertEqual(len(self.generated_rows), 2)
        self.assertEqual(self.rows()[0], first)
        self.assertEqual(Path(first['video']).read_bytes(), first_bytes)
        self.assertEqual(self.generated_rows[0]['key'], first['render_request']['key'])
        for key in ('key', 'input_signature', 'final_prompt', 'final_refs', 'resolved_params'):
            self.assertEqual(self.rows()[1]['render_request'][key], frozen_second[key])
        self.assertEqual(self.merge.call_count, 2)
        self.assertIn('_部分', self.merge.call_args_list[0].kwargs['dst'])
        self.assertIsNone(self.merge.call_args_list[1].kwargs['dst'])


class WorkbenchExistingRequestTests(unittest.TestCase):
    setUp = h3_fixture.H3RequestRecoveryTests.setUp
    receipt = h3_fixture.H3RequestRecoveryTests.receipt
    success = h3_fixture.H3RequestRecoveryTests.success
    generate = h3_fixture.H3RequestRecoveryTests.generate

    def test_existing_external_request_is_queried_without_another_post(self):
        before = self.receipt('submitted')
        queried = []
        def http(method, path, *args, **options):
            queried.append((method, path))
            self.assertEqual(method, 'GET')
            if path == '/queue':
                return dict(queue_running=[[0, 'old-id']], queue_pending=[])
            self.assertEqual(path, '/history/old-id')
            return self.success('old-id')
        self.http.side_effect = http
        result = self.generate()
        self.assertTrue(Path(result['output_path']).is_file())
        self.assertTrue(queried)
        self.assertFalse(any(method == 'POST' for method, _ in queried))
        self.assertEqual(h3_client.recovery_receipt(self.key)['prompt_id'], before['prompt_id'])
        self.build.assert_not_called()


if __name__ == '__main__':
    unittest.main()
