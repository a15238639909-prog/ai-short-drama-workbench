"""Additional actual POV entrance checks with normal adult source fixtures only.

All models and media boundaries are simulated. No production project is read.
"""
from contextlib import ExitStack
import copy
import os
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from api import segment_api
from cores import authoring, seam_v2
from tests import test_pov_spoken_camera_prompt as speech_support
from tests import test_workbench_director_dispatch as dispatch_support
from tests.test_workbench_story_director_release import fixture


class POVIntegrityEdgeTests(unittest.TestCase):
    def support(self):
        case = speech_support.SpokenCameraPromptTests(methodName='runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_numeric_english_and_punctuation_lines_survive_both_real_compilers(self):
        case = self.support()
        for text in ('12345。', 'OK。', '3点15分。', '……！'):
            for director in (False, True):
                with self.subTest(text=text, director=director):
                    result = case.compile_prompt([('阿宁', text)], director=director, omit_speech=True)
                    prompt = case.assert_gate(result)
                    self.assertEqual([m[3] for m in authoring._H3_SAY.finditer(prompt)], [text])

    def test_unknown_and_camera_voice_keep_scene_picture_numbers_for_each_scene(self):
        case = self.support()
        for director in (False, True):
            with self.subTest(director=director):
                prompt = case.assert_gate(case.compile_prompt(
                    [('待核对说话人', '先到门口等。'), ('我', '知道了。')],
                    director=director, omit_speech=True, scenes=['修表店', '门口']))
                self.assertIn('<Subject 4>是<Picture 2>', prompt)
                self.assertIn('<Subject 5>是<Picture 3>', prompt)
                self.assertNotIn('<Picture 4>', prompt)

    def public_rows(self, quote, *, sex):
        rows, _, _ = fixture()
        rows = rows[:1]
        rows[0]['text'] = '我主观视角，阿岚扶稳茶杯。\n阿岚：' + quote
        rows[0]['director']['says'] = [['阿岚', quote]]
        rows[0]['director']['camera'] = '我主观视角，眼睛高度'
        rows[0]['director']['blocks'][1][2] = '【D9】阿岚：' + quote
        helper = dispatch_support.WorkbenchDirectorDispatchTests(methodName='runTest')
        self.addCleanup(helper.doCleanups)
        settings = helper.context(dict(pov='第一视角POV', camera_owner='我'),
            slices_override=rows, prose_override='阿岚说：“' + quote + '”')
        reply = ('【机位】我主观视角，眼睛高度。\n0—6秒：我主观视角，阿岚扶稳茶杯。'
                 '\n这一段结束时：茶杯在桌上。')
        with ExitStack() as stack:
            stack.enter_context(patch.object(authoring, '_q', return_value=reply))
            stack.enter_context(patch.object(authoring, 'plan_segments', return_value={}))
            stack.enter_context(patch.object(authoring, 'fresh_shotlist', return_value=None))
            stack.enter_context(patch.object(authoring, 'h3_voice_tech', return_value='Natural adult voice.'))
            stack.enter_context(patch.object(authoring, 'card_sex', return_value=sex))
            stack.enter_context(patch.object(seam_v2, 'on', return_value=False))
            result = authoring.make_h3_prompts('SAFE_POV_PRONOUN_FIXTURE', 1, start=0, count=1)
        self.assertEqual(len(result), 1)
        return result[0], settings

    def test_single_gender_visual_correction_never_changes_source_dialogue_words(self):
        for sex, quote in (('男', '她明天会来。'), ('女', '他明天会来。')):
            with self.subTest(sex=sex):
                row, settings = self.public_rows(quote, sex=sex)
                self.assertEqual([m[3] for m in authoring._H3_SAY.finditer(row['prompt'])], [quote])
                self.assertEqual(row['critical_problems'], [])
                self.assertEqual(segment_api._render_critical_problems(
                    dict(settings=settings), {}, row, row['prompt'], 1, 1), [])

    def test_director_skeleton_pov_timeout_keeps_source_and_speech(self):
        person = dict(name='阿宁', age=30, appearance='成年修表师，普通工作服',
                      voice_h3_en='Natural adult voice.', voice_h3_age='30')
        director = dict(camera='我主观视角，眼睛高度', cast=['阿宁'], seconds=8,
            blocks=[[0, 8, '我主观视角，阿宁扶稳手表。', [1]]],
            says=[['待核对说话人', '请拿好。']], end_state={})
        settings = dict(pov='第一视角POV', camera_owner='我',
            _beat=dict(director=copy.deepcopy(director), act='阿宁扶稳手表。', cast=['阿宁']))
        source = '我主观视角，阿宁扶稳手表。\n待核对说话人：请拿好。'
        with patch.object(authoring, '_q', side_effect=TimeoutError('synthetic text outage')), \
             patch.object(seam_v2, 'on', return_value=False):
            prompt = authoring.h3_prompt(source, [person], '修表店', settings,
                                         scene_image=True, seconds=8)
        self.assertIn('<d>[Chinese]请拿好。</d>', prompt)
        self.assertEqual(authoring.h3_problems(prompt, source, [person], settings), [])
        self.assertNotIn('待核对说话人嘴唇开合', prompt)

    def test_frozen_director_subject_does_not_mutate_when_voice_is_anonymous(self):
        case = self.support()
        result = case.compile_prompt([('待核对说话人', '请拿好。')], director=True, omit_speech=True)
        case.assert_gate(result)


if __name__ == '__main__':
    unittest.main()
