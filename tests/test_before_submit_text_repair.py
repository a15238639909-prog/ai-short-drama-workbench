"""Shared button/batch pre-submit text repair; normal sources, no media IO."""
import copy
import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from api import segment_api as api
from cores import authoring, reference_ready
from models import h3_client


class BeforeSubmitTextRepairTests(unittest.TestCase):
    def setUp(self):
        self.source = '我主观视角，阿宁收好修表工具。\n待核对说话人：修好了。'
        self.good = ('<Subject1>阿宁是<Picture 1>成年修表师。\n'
            '<Subject2>待核对说话人是本段原文的声音标签；身份未核定。\n'
            '<Subject 3>是<Picture 2>修表店场景。\n'
            '【机位】我主观视角。\n0—8秒：我主观视角，阿宁收好修表工具。\n'
            '待核对说话人说：<Subject 2> (S2) says:<d>[Chinese]修好了。</d>\n'
            '这一段结束时：工具在柜台上。')
        self.bad = self.good.split('待核对说话人说：')[0]
        self.story = dict(settings=dict(pov='第一视角POV', camera_owner='我'))
        self.timeline = dict(scenes=[dict(no=2, segments=[dict(no=n, prompt=self.good,
            source_text=self.source, chars=['阿宁'], scene='修表店', seconds=8,
            plan={}, video='saved-%d.mp4' % n if n != 2 else '') for n in (1, 2, 3)])])
        self.timeline['scenes'][0]['segments'][1]['prompt'] = self.bad
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(api, '_load', side_effect=lambda *args:
            (copy.deepcopy(self.story), copy.deepcopy(self.timeline))))
        self.stack.enter_context(patch.object(api, '_save', side_effect=self.save))
        self.native = self.stack.enter_context(patch.object(h3_client, 'generate',
            side_effect=AssertionError('real video generation prohibited')))
        self.writer = self.stack.enter_context(patch.object(authoring, 'make_h3_prompts',
            return_value=[dict(n=2, prompt=self.good, source_text=self.source,
                chars=['阿宁'], scene='修表店', seconds=8, plan={})]))

    def save(self, sid, timeline, ep):
        self.timeline = copy.deepcopy(timeline)
        return True

    def row(self, n=2):
        return self.timeline['scenes'][0]['segments'][n - 1]

    def prepare(self, n=2):
        result = api._prepare_render_prompt('SAFE_PRE_SUBMIT', 1, 2, n)
        self.native.assert_not_called()
        return result

    def test_only_broken_target_is_repaired_and_real_gate_passes(self):
        first, third = copy.deepcopy(self.row(1)), copy.deepcopy(self.row(3))
        st, tl, _, row, prompt, critical = self.prepare()
        self.assertEqual(critical, [])
        self.assertEqual(api._render_critical_problems(st, tl, row, prompt, 1, 2), [])
        self.assertEqual(self.row(1), first); self.assertEqual(self.row(3), third)
        self.assertEqual(self.row()['prompt_repair_history'][0]['prompt'], self.bad)
        self.writer.assert_called_once_with('SAFE_PRE_SUBMIT', 1, start=1, count=1)

    def test_valid_target_needs_no_writer_or_new_receipt(self):
        self.assertEqual(self.prepare(1)[-1], [])
        self.writer.assert_not_called()

    def test_repair_saves_compiler_audit_profile_false_flag_and_explicit_empty_cast(self):
        audit = dict(source_shot_audit=dict(policy='source-shots-v1', units=['U001']), dialogue=[])
        self.row().update(chars=['旧人物'], source_fallback=True, director_profile='old-profile',
                          director_compilation=dict(old_audit=True))
        self.writer.return_value[0].update(chars=[], director_compilation=copy.deepcopy(audit),
            director_profile='original-story-zh-v1', source_fallback=False)
        with patch.object(api, '_render_critical_problems', side_effect=[['旧词需修复'], [], []]):
            self.assertEqual(self.prepare()[-1], [])
        self.assertEqual(self.row()['chars'], [])
        self.assertEqual(self.row()['director_compilation'], audit)
        self.assertEqual(self.row()['director_profile'], 'original-story-zh-v1')
        self.assertIs(self.row()['source_fallback'], False)

    def test_pending_submission_and_manual_edits_are_never_rewritten(self):
        for protected in (dict(render_request=dict(key='same-original-request', status='pending')),
                          dict(prompt_edited=True)):
            with self.subTest(protected=protected):
                self.row().update(protected)
                before = copy.deepcopy(self.timeline)
                self.assertTrue(self.prepare()[-1])
                self.assertEqual(self.timeline, before)
                self.writer.assert_not_called()
                self.row().pop('render_request', None); self.row().pop('prompt_edited', None)

    def test_failed_proposal_is_bounded_and_preserves_original(self):
        self.writer.return_value = [dict(n=2, prompt=self.bad)]
        before = copy.deepcopy(self.timeline)
        self.assertTrue(self.prepare()[-1])
        self.assertEqual(self.timeline, before)
        self.writer.assert_called_once()

    def test_manual_edit_arriving_during_repair_wins(self):
        def writer(*args, **kwargs):
            self.row().update(prompt='new manual version', prompt_edited=True)
            return [dict(n=2, prompt=self.good)]
        self.writer.side_effect = writer
        self.assertTrue(self.prepare()[-1])
        self.assertEqual(self.row()['prompt'], 'new manual version')
        self.assertTrue(self.row()['prompt_edited'])

    def test_single_button_reaches_shared_repair_instead_of_early_400(self):
        def render(*args, **kwargs):
            self.assertEqual(self.prepare()[-1], [])
            return dict(video='native-boundary-simulated')
        with patch.object(api, '_ep_of', return_value=1), \
             patch.object(api, '_include_scene_reference', return_value=True), \
             patch.object(reference_ready, 'assert_references'), \
             patch.object(api, 'render_segment', side_effect=render) as boundary, \
             patch.object(api, '_RESP', side_effect=lambda body, status=200: body):
            result = api.gen_segment(None, '/api/timeline/SAFE_PRE_SUBMIT/generate-segment',
                dict(ep=1, scene_no=2, seg_no=2, prompt=self.bad))
        self.assertTrue(result['ok'])
        self.assertFalse(self.row().get('prompt_edited'))
        self.writer.assert_called_once()
        boundary.assert_called_once()


if __name__ == '__main__':
    unittest.main()
