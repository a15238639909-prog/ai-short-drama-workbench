"""Pure reference numbering and pending-request protection; safe adult fixtures."""
from contextlib import ExitStack
import copy
import os
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from cores import authoring, video_continuity
from api import saga_api
from tests import test_segment_render_recovery as render_cases


class POVPictureSlotsTests(unittest.TestCase):
    def prompt(self, names, mode='第一视角POV', scenes='修表店', scene_image=True):
        settings = dict(pov=mode, camera_owner='林海')
        cards = [dict(name=name, age=32, appearance='正常工作服的成年工作人员') for name in names]
        with ExitStack() as stack:
            stack.enter_context(patch.object(authoring, '_pov_spec', return_value=(mode, {})))
            stack.enter_context(patch.object(authoring, '_h3_timeline_body',
                return_value=('0—8秒：镜头自然观察柜台上的手表。\n这一段结束时：手表仍在柜台上。', '手表在柜台上。')))
            stack.enter_context(patch.object(authoring, 'use_timeline_director', return_value=True))
            stack.enter_context(patch.object(authoring, '_h3_cine', return_value='真实摄影。'))
            return authoring.h3_prompt('林海观察柜台上的手表。', cards, scenes, settings,
                                      scene_image=scene_image, seconds=8)

    def test_camera_subject_does_not_take_an_image_slot(self):
        prompt = self.prompt(['林海'])
        self.assertIn('<Subject 2>是<Picture 1>', prompt)
        self.assertNotIn('<Picture 2>', prompt)
        video_continuity.validate_prompt_references(prompt, ['ordinary_scene.png'])

    def test_partner_and_camera_keep_separate_subject_and_picture_counts(self):
        prompt = self.prompt(['林海', '阿宁'])
        self.assertRegex(prompt, r'<Subject\s*1>阿宁')
        self.assertIn('<Subject 3>是<Picture 2>', prompt)
        video_continuity.validate_prompt_references(prompt, ['adult_partner.png', 'ordinary_scene.png'])

    def test_explicit_camera_not_in_current_cast_does_not_remove_partner_image(self):
        prompt = self.prompt(['阿宁'])
        self.assertIn('<Subject 2>是<Picture 2>', prompt)
        video_continuity.validate_prompt_references(prompt, ['adult_partner.png', 'ordinary_scene.png'])

    def test_multiple_scenes_get_consecutive_actual_picture_slots(self):
        prompt = self.prompt(['林海', '阿宁'], scenes=['修表店', '门口'])
        self.assertIn('<Subject 3>是<Picture 2>', prompt)
        self.assertIn('<Subject 4>是<Picture 3>', prompt)
        video_continuity.validate_prompt_references(prompt, ['adult_partner.png', 'shop.png', 'door.png'])

    def test_external_camera_numbering_remains_unchanged(self):
        prompt = self.prompt(['林海', '阿宁'], mode='第三视角')
        self.assertIn('<Subject 3>是<Picture 3>', prompt)
        video_continuity.validate_prompt_references(prompt, ['adult_one.png', 'adult_two.png', 'shop.png'])

    def test_scene_without_image_does_not_claim_a_picture(self):
        prompt = self.prompt(['林海'], scene_image=False)
        self.assertNotIn('<Picture', prompt)


class PendingBindingProtectionTests(unittest.TestCase):
    def test_old_partial_view_reveals_reason_without_mutating_history(self):
        job = dict(running=False, status='partial', step='本轮已处理，第1段待补，已完成视频保留',
                   failure_reason='最终视频提示词含未绑定的图片编号，已暂停生成', err='',
                   failed_render_segments=[1], total=11, done=11, logs=[])
        before = copy.deepcopy(job)
        view = saga_api._job_view(job)
        self.assertEqual(view['err'], job['failure_reason'])
        self.assertTrue(view['step'].startswith('生成未完成：'))
        self.assertLess(view['percent'], 100)
        self.assertEqual(job, before)

    def test_unknown_pending_input_mismatch_never_creates_another_request(self):
        case = render_cases.SegmentRenderRecoveryTests(methodName='runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.current()['render_request'] = dict(key='already-submitted', status='pending',
            input_signature='different-reference-contract', requested_seed=None)
        case.receipts['already-submitted'] = dict(status='submitted', prompt_id='existing-task')
        before = copy.deepcopy(case.current()['render_request'])
        with self.assertRaisesRegex(ValueError, '未新建重复任务'):
            case.render()
        case.generate.assert_not_called()
        self.assertEqual(case.current()['render_request'], before)


if __name__ == '__main__':
    unittest.main()
