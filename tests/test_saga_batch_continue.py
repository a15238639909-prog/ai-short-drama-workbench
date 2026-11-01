"""Exercise the real batch scheduler with fake model/encoder boundaries only."""
import copy
import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from api import saga_api as sa, segment_api
from cores import (authoring, saga_core, reference_ready, episode_ledger,
                   episode_merge, video_continuity, workbench_director_runtime as runtime,
                   shot_edit, manual_story)


class SagaBatchContinueTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.rows = [dict(no=no, prompt='已保存的成年旅人路线讨论镜头。', seconds=8) for no in (1, 2, 3)]
        self.timeline = dict(scenes=[dict(no=no, segments=[self.rows[no - 1]]) for no in (1, 2, 3)])
        self.job = dict(running=True, status='running', done=0, total=0, logs=[],
            _recovery=dict(phase='render', external_inflight='',
                request=dict(stage='video', ep=1, n=3)))
        self.stack.enter_context(patch.object(sa, '_persist_job'))
        self.stack.enter_context(patch.object(sa, '_job_event'))
        self.stack.enter_context(patch.object(sa, '_set_step', side_effect=lambda j, msg: j.update(step=msg)))
        self.stack.enter_context(patch.object(sa, '_generation_checkpoint'))
        self.stack.enter_context(patch.object(saga_core, 'ep_timeline', side_effect=lambda *a: self.timeline))
        self.stack.enter_context(patch.object(saga_core, 'save_ep_timeline'))
        self.stack.enter_context(patch.object(reference_ready, 'ensure_characters'))
        self.scenes = self.stack.enter_context(patch.object(sa, '_ep_scene_images'))
        self.stack.enter_context(patch.object(authoring, 'total_segments', side_effect=lambda *a: len(self.rows)))
        self.stack.enter_context(patch.object(authoring, 'use_new_pipeline', return_value=True))
        self.writer = self.stack.enter_context(patch.object(authoring, 'next_prompts', return_value=([], 3)))
        self.ledger = self.stack.enter_context(patch.object(episode_ledger, 'build'))
        self.stack.enter_context(patch.object(runtime, 'enabled', return_value=False))
        self.stack.enter_context(patch.object(runtime, 'after_render'))
        self.stack.enter_context(patch.object(segment_api, 'render_opening_all', return_value=(0, '')))
        self.stack.enter_context(patch.object(segment_api, 'is_segment_current',
            side_effect=lambda sid, ep, sc, no, **kw: bool(self.rows[no - 1].get('video'))))
        self.stack.enter_context(patch.object(segment_api, '_apply',
            side_effect=lambda sid, sc, no, data, **kw: self.rows[no - 1].update(data)))
        self.stack.enter_context(patch.object(sa, '_generation_step', side_effect=lambda j, key, fn, **kw: fn()))
        self.render = self.stack.enter_context(patch.object(segment_api, 'render_segment_checked', side_effect=self.render_ok))

    def render_ok(self, sid, ep, scene, number, **kwargs):
        self.assertEqual(scene, number)
        self.rows[number - 1]['video'] = 'clip%d.mp4' % number
        return dict(video='clip%d.mp4' % number)

    def run_chain(self, **kwargs):
        return sa._first_video_chain('SYNTHETIC_BATCH', self.job, ep=1, n=None, approved=True, **kwargs)

    def test_unknown_middle_receipt_continues_later_scene_and_survives_merge(self):
        original_request = dict(key='original-2', status='submitted', prompt_id='same-task')
        self.rows[1]['render_request'] = copy.deepcopy(original_request)
        def render(*args, **kwargs):
            if args[3] == 2:
                raise RuntimeError('任务查询暂时失败，原任务仍在处理')
            return self.render_ok(*args, **kwargs)
        self.render.side_effect = render
        self.assertEqual(self.run_chain(), ['clip1.mp4', 'clip3.mp4'])
        self.assertEqual([call.args[3] for call in self.render.call_args_list], [1, 2, 3])
        self.assertEqual(self.rows[1]['render_request'], original_request)
        self.assertEqual(self.job['_recovery']['pending_external']['video:1:2']['request_key'], 'original-2')
        self.assertEqual(self.job['pending_render_requests']['video:1:2']['status'], 'submitted')
        sa._finish_job(self.job)
        self.assertEqual(self.job['status'], 'waiting_task')

    def test_multiple_uncertain_receipts_are_independent_and_success_removes_only_its_entry(self):
        for row in self.rows[:2]:
            row['render_request'] = dict(key='key%d' % row['no'], status='unknown')
        def render(*args, **kwargs):
            if args[3] in (1, 2):
                raise TimeoutError('H3 生成超时，提交结果待核对')
            return self.render_ok(*args, **kwargs)
        self.render.side_effect = render
        self.assertEqual(self.run_chain(), ['clip3.mp4'])
        self.assertEqual(set(self.job['_recovery']['pending_external']), {'video:1:1', 'video:1:2'})
        self.render.side_effect = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('任务状态暂时无法确认')) if a[3] == 2 else self.render_ok(*a, **kw)
        self.assertEqual(self.run_chain(), ['clip1.mp4', 'clip3.mp4'])
        self.assertEqual(set(self.job['pending_render_requests']), {'video:1:2'})
        self.assertEqual(set(self.job['_recovery']['pending_external']), {'video:1:2'})
        self.render.side_effect = self.render_ok
        self.assertEqual(self.run_chain(), ['clip1.mp4', 'clip2.mp4', 'clip3.mp4'])
        self.assertFalse(self.job['pending_render_requests'])
        self.assertFalse(self.job['_recovery']['pending_external'])
        self.assertNotIn('partial', self.job)
        sa._finish_job(self.job)
        self.assertEqual(self.job['status'], 'completed')

    def test_original_director_callback_preserves_partial_and_receipt(self):
        self.rows[1]['render_request'] = dict(key='original-2', status='pending')
        def render(*args, **kwargs):
            if args[3] == 2:
                raise RuntimeError('提交结果待核对')
            return self.render_ok(*args, **kwargs)
        self.render.side_effect = render
        with patch.object(runtime, 'enabled', return_value=True), patch.object(runtime, 'explicit_opening'):
            self.assertEqual(self.run_chain(), ['clip1.mp4', 'clip3.mp4'])
        self.assertEqual(self.job['saved_video_segments'], [1, 3])
        self.assertEqual(self.job['failed_render_segments'], [2])
        self.assertEqual(self.job['_recovery']['pending_external']['video:1:2']['request_key'], 'original-2')

    def test_writer_failure_keeps_ready_first_and_third_without_rendering_missing_second(self):
        self.rows[1]['prompt'] = ''
        self.writer.side_effect = ValueError('第2段输出不是完整结构')
        self.assertEqual(self.run_chain(), ['clip1.mp4', 'clip3.mp4'])
        self.assertEqual([call.args[3] for call in self.render.call_args_list], [1, 3])
        self.assertEqual(self.job['missing_prompt_segments'], [2])
        self.assertEqual(self.job['pending_render_segments'], [2])

    def test_optional_ledger_and_global_reference_failure_do_not_stop_other_slots(self):
        self.ledger.side_effect = ValueError('观察记录格式错误')
        with patch.object(reference_ready, 'ensure_characters', side_effect=reference_ready.MissingReferencesError('配角图未就绪')):
            self.assertEqual(self.run_chain(), ['clip1.mp4', 'clip2.mp4', 'clip3.mp4'])
        self.scenes.assert_called_once()
        self.assertNotIn('partial', self.job)

    def test_gap_merge_uses_partial_filename_and_keeps_missing_slot(self):
        self.job['expected_segments'] = 3
        def merge(*args, **kwargs):
            self.assertTrue(kwargs['skip_invalid'])
            self.assertIn('_部分', kwargs['dst'])
            kwargs['report'].update(merged_segments=[1, 3], skipped=[], partial=False)
            return kwargs['dst']
        with patch.object(episode_merge, 'episode_videos', return_value=[(1, 'one.mp4'), (3, 'three.mp4')]), \
             patch.object(episode_merge, 'film_title', return_value='测试'), \
             patch.object(episode_merge, 'merge_episode', side_effect=merge), \
             patch.object(video_continuity, 'validate_video') as validate:
            result = sa._merge_generated_episode('SYNTHETIC_BATCH', 1, self.job)
        self.assertIn('_部分', result)
        self.assertTrue(self.job['merged_partial'])
        self.assertEqual(self.job['merged_segments'], [1, 3])
        self.assertEqual(self.job['pending_render_segments'], [2])
        validate.assert_called_once_with(result)
        self.render.assert_not_called()

    def test_registered_partial_still_merges_then_keeps_render_recovery_phase(self):
        self.job['pending_render_requests'] = {'video:1:2': dict(request_key='saved-key')}
        def partial(*args, **kwargs):
            self.job.update(partial='第2段等待查询', saved_video_count=2, failed_render_segments=[2])
            return ['one.mp4', 'three.mp4']
        with patch.object(sa.story_core, 'get_story', return_value={}), \
             patch.object(manual_story, 'enabled', return_value=False), patch.object(shot_edit, 'approve'), \
             patch.object(sa, '_first_video_chain', side_effect=partial), \
             patch.object(sa, '_merge_generated_episode', return_value='partial.mp4') as merge, \
             patch.object(sa, '_generation_checkpoint') as checkpoint:
            sa._run_registered_make('SYNTHETIC_BATCH', self.job)
        merge.assert_called_once()
        self.assertEqual(self.job['status'], 'waiting_task')
        checkpoint.assert_any_call(self.job, 'render', external='video:1:2')

    def test_corrupt_clip_skipped_by_merge_is_still_listed_for_retry(self):
        self.job['expected_segments'] = 3
        def merge(*args, **kwargs):
            kwargs['report'].update(merged_segments=[1, 3], partial=True,
                skipped=[dict(segment_no=2, path='bad.mp4', error='invalid stream')])
            return 'partial.mp4'
        with patch.object(episode_merge, 'episode_videos',
                return_value=[(1, 'one.mp4'), (2, 'bad.mp4'), (3, 'three.mp4')]), \
             patch.object(episode_merge, 'merge_episode', side_effect=merge), \
             patch.object(video_continuity, 'validate_video'):
            self.assertEqual(sa._merge_generated_episode('SYNTHETIC_BATCH', 1, self.job), 'partial.mp4')
        self.assertEqual(self.job['pending_render_segments'], [2])
        self.assertEqual(self.job['merged_segments'], [1, 3])
        self.assertTrue(self.job['merged_partial'])


if __name__ == '__main__':
    unittest.main()
