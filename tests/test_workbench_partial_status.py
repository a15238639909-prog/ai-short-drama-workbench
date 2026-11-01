"""Mock-only workbench failures: no models, files, media or real projects."""
import os
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

os.environ['V41_NO_MODEL'] = '1'

from api import saga_api as sa, segment_api
from cores import authoring, saga_core, reference_ready, episode_ledger


class WorkbenchPartialStatusTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(sa, '_persist_job'))
        self.events = self.stack.enter_context(patch.object(sa, '_job_event'))
        self.stack.enter_context(patch.object(sa, 'log'))

    def job(self, **changes):
        job = dict(running=True, status='running', done=3, total=3, percent=100,
                   logs=[], _recovery=dict(external_inflight='', input_hash='original-input'))
        job.update(changes)
        return job

    def chain(self, job, saved=(), failure_at=1, request=None):
        self.rows = [dict(no=no, prompt='两位成年旅人在桥边交换路线信息。',
                         video='clip%d.mp4' % no if no in saved else '') for no in (1, 2, 3)]
        if request is not None:
            self.rows[failure_at - 1]['render_request'] = request
        timeline = dict(scenes=[dict(segments=self.rows)])
        self.stack.enter_context(patch.object(saga_core, 'ep_timeline', return_value=timeline))
        self.stack.enter_context(patch.object(reference_ready, 'ensure_characters'))
        self.stack.enter_context(patch.object(sa, '_ep_scene_images'))
        self.stack.enter_context(patch.object(authoring, 'total_segments', return_value=3))
        self.stack.enter_context(patch.object(authoring, 'use_new_pipeline', return_value=True))
        self.stack.enter_context(patch.object(authoring, 'next_prompts', return_value=([], 3)))
        self.stack.enter_context(patch.object(episode_ledger, 'build'))
        self.stack.enter_context(patch.object(segment_api, 'render_opening_all', return_value=(0, '')))
        self.stack.enter_context(patch.object(segment_api, 'is_segment_current',
            side_effect=lambda sid, ep, scene, no, **kwargs: no in saved))
        self.stack.enter_context(patch.object(sa, '_set_step',
            side_effect=lambda current, message: current.update(step=message)))

        def apply(sid, scene, no, patch_data, ep=None):
            row = self.rows[no - 1]
            row.update(patch_data)
            return row

        self.stack.enter_context(patch.object(segment_api, '_apply', side_effect=apply))

        def step(current, key, fn, **kwargs):
            current['_recovery']['external_inflight'] = kwargs.get('external', '')
            result = fn()
            current['_recovery']['external_inflight'] = ''
            return result

        self.stack.enter_context(patch.object(sa, '_generation_step', side_effect=step))
        self.render = self.stack.enter_context(patch.object(segment_api, 'render_segment_checked',
            side_effect=ValueError('最终视频提示词含未绑定的图片编号，已暂停生成')))
        return sa._first_video_chain('STORY_SAFE_FIXTURE', job, ep=1, n=None, approved=True)

    def test_zero_clips_reports_each_failure_after_trying_all_segments(self):
        job = self.job()
        self.assertEqual(self.chain(job), [])
        self.assertEqual([call.args[3] for call in self.render.call_args_list], [1, 2, 3])
        self.assertEqual(job['saved_video_count'], 0)
        self.assertEqual(job['failed_render_segments'], [1, 2, 3])
        self.assertEqual(job['unexecuted_render_segments'], [])
        self.assertEqual(job['pending_render_segments'], [1, 2, 3])
        self.assertIn('当前0/3段视频', job['partial'])
        self.assertIn('第1、2、3段待重试', job['partial'])
        self.assertIn('未绑定的图片编号', job['partial'])
        self.assertNotIn('已保存视频保留', job['partial'])
        self.assertIn('本轮已处理全部目标段', job['partial'])
        sa._finish_job(job)
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['err'], job['failure_reason'])
        self.assertLess(job['percent'], 100)
        self.assertLess(sa._job_view(job)['percent'], 100)

    def test_actual_saved_clips_are_preserved_and_counted(self):
        job = self.job()
        self.assertEqual(self.chain(job, saved=(1,), failure_at=2), ['clip1.mp4'])
        self.assertEqual([call.args[3] for call in self.render.call_args_list], [2, 3])
        self.assertEqual(job['saved_video_segments'], [1])
        self.assertEqual(job['saved_video_count'], 1)
        self.assertEqual(job['unexecuted_render_segments'], [])
        self.assertIn('已保存1/3段', job['partial'])
        self.assertIn('已保存视频保留', job['partial'])
        sa._finish_job(job)
        self.assertEqual(job['status'], 'partial')
        self.assertTrue(job['err'])
        self.assertLess(job['percent'], 100)

    def test_valid_later_existing_clip_is_counted_without_rendering_it(self):
        job = self.job()
        self.chain(job, saved=(3,))
        self.assertEqual(job['saved_video_segments'], [3])
        self.assertEqual(job['unexecuted_render_segments'], [])
        self.assertEqual(job['pending_render_segments'], [1, 2])
        self.assertEqual([call.args[3] for call in self.render.call_args_list], [1, 2])

    def test_confirmed_pre_submit_failure_clears_only_false_inflight_marker(self):
        job = self.job()
        self.chain(job)
        self.assertEqual(job['_recovery']['external_inflight'], '')
        self.assertEqual(job['_recovery']['input_hash'], 'original-input')
        self.assertEqual(job['failure_stage'], 'video_preflight')
        self.assertEqual(job['render_submission_state'], 'not_submitted')
        self.assertEqual(self.rows[0]['render_failure_stage'], 'video_preflight')

    def test_persisted_pending_or_unknown_request_is_never_cleared(self):
        for state in ('pending', 'submitted', 'unknown'):
            with self.subTest(state=state), ExitStack() as local:
                # Each batch installs mocks only for this subtest.
                previous = self.stack
                self.stack = local
                try:
                    job = self.job()
                    self.chain(job, request=dict(key='persisted-original-request', status=state))
                    self.assertEqual(job['_recovery']['pending_external']['video:1:1']['request_key'], 'persisted-original-request')
                    self.assertEqual(job['pending_render_requests']['video:1:1']['status'], state)
                    self.assertEqual([call.args[3] for call in self.render.call_args_list], [1, 2, 3])
                finally:
                    self.stack = previous

    def test_partial_finish_keeps_specific_error_and_never_marks_full_success(self):
        job = self.job(partial='第2段尚未完成', failure_reason='参考图路径已变化', saved_video_count=1,
                       failed_render_segments=[2])
        sa._finish_job(job)
        self.assertEqual(job['err'], '参考图路径已变化')
        self.assertEqual(job['status'], 'partial')
        self.assertLess(job['percent'], 100)

    def test_malformed_request_metadata_is_unknown_not_proof_of_no_submission(self):
        job = self.job()
        self.chain(job, request='unreadable-request-metadata')
        self.assertEqual(job['_recovery']['pending_external']['video:1:1']['status'], 'unknown')
        self.assertEqual(job['pending_render_requests']['video:1:1']['request_key'], '')
        self.assertIn('未绑定的图片编号', job['failure_reason'])

    def test_all_valid_existing_clips_reuse_without_render_and_may_continue_merge(self):
        job = self.job()
        result = self.chain(job, saved=(1, 2, 3))
        self.assertEqual(result, ['clip1.mp4', 'clip2.mp4', 'clip3.mp4'])
        self.render.assert_not_called()
        self.assertNotIn('partial', job)
        self.assertNotIn('failed_render_segments', job)

    def test_partial_registered_run_checks_saved_clips_for_merge_without_claiming_complete(self):
        from cores import shot_edit, manual_story
        job = self.job(_recovery=dict(phase='render', request=dict(ep=1, stage='video', n=None)))

        def partial(*args, **kwargs):
            job.update(partial='当前0/3段视频；第1段提交前检查失败',
                       failure_reason='图片编号未绑定', failed_render_segments=[1], saved_video_count=0)
            return []

        with patch.object(sa, '_generation_checkpoint'), patch.object(sa, '_first_video_chain', side_effect=partial), \
                patch.object(sa.story_core, 'get_story', return_value={}), \
                patch.object(manual_story, 'enabled', return_value=False), patch.object(shot_edit, 'approve'), \
                patch.object(sa, '_merge_generated_episode') as merge:
            sa._run_registered_make('STORY_SAFE_FIXTURE', job)
        merge.assert_called_once()
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['err'], '图片编号未绑定')


if __name__ == '__main__':
    unittest.main()
