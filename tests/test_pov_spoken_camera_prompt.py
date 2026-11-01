"""Real H3 prompt writer -> real render gate; only the text model is simulated.

Synthetic adult fixtures only. No media, saved projects, services or local AI.
"""
import copy
import os
import re
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from api import segment_api
from cores import authoring, director_shots, video_continuity


class SpokenCameraPromptTests(unittest.TestCase):
    def setUp(self):
        self.person = dict(name='阿宁', age=30, appearance='成年修表师，普通工作服',
                           voice_h3_en='Warm voice, medium pitch, clear articulation.',
                           voice_h3_age='30')
        self.settings = dict(pov='第一视角POV', camera_owner='我')

    def compile_prompt(self, turns, *, director, camera='我', cards=None, scenes='修表店', omit_speech=False):
        cards = [copy.deepcopy(self.person)] if cards is None else cards
        settings = dict(self.settings, camera_owner=camera)
        visible = [row['name'] for row in cards if row['name'] != camera]
        action = ('%s主观视角，阿宁看向镜头，轻轻点头。' if visible else
                  '%s主观视角，镜头自然观察柜台上的手表。') % camera
        source = action
        if turns:
            source += '\n' + '\n'.join('%s：%s' % turn for turn in turns)
        count = max(1, len(turns))
        seconds = count * 4
        actions = [action] * count
        blocks = [[i * 4, (i + 1) * 4, action, [i + 1] if turns else []]
                  for i, action in enumerate(actions)]
        d = dict(camera='%s主观视角，眼睛高度' % camera,
                 cast=[row['name'] for row in cards if row['name'] != camera],
                 seconds=seconds, blocks=blocks, says=copy.deepcopy(turns),
                 end_state={self.person['name']: dict(pos='柜台后', facing='面朝镜头',
                            hands='空手', posture='站立', side='画面中央')})
        if director:
            settings['_beat'] = dict(director=d, act=actions[0], cast=d['cast'])
        ordered, _ = authoring.h3_prompt_char_order(source, cards, settings)
        indices = {row['name']: i for i, row in enumerate(ordered, 1)}
        response = '【机位】%s主观视角，眼睛高度。\n' % camera
        for i, action in enumerate(actions):
            response += '%d—%d秒：%s\n' % (i * 4, (i + 1) * 4, action)
            if turns and not omit_speech:
                speaker, quote = turns[i]
                n = indices[speaker]
                response += '%s说：<Subject %d> (S%d) says:<d>[Chinese]%s</d>\n' % (
                    speaker, n, n, quote)
        response += '这一段结束时：\n· 手表仍在柜台上。'
        before = copy.deepcopy((cards, d))
        with patch.object(authoring, '_q', return_value=response) as call:
            prompt = authoring.h3_prompt(source, cards, scenes, settings,
                                         scene_image=True, seconds=seconds)
        self.assertTrue(call.called)
        self.assertTrue(all(entry.args[0] != authoring._VOICE_TECH_SYS
                            for entry in call.call_args_list), 'No voice conversion call is needed')
        self.assertEqual((cards, d), before, 'Voice-only camera must never mutate cards or director')
        return prompt, source, settings, cards, seconds

    def assert_gate(self, result):
        prompt, source, settings, cards, seconds = result
        problems = authoring.h3_problems(prompt, source, cards, settings)
        self.assertEqual(problems, [])
        names = [row['name'] for row in cards if row['name'] != settings['camera_owner']]
        row = dict(no=1, source_text=source, chars=names, scene='修表店', seconds=seconds,
                   problems=[], critical_problems=[], plan={})
        story = dict(settings=settings)
        self.assertEqual(segment_api._render_critical_problems(story, {}, row, prompt, 1, 1), [])
        return prompt

    def test_interleaved_visible_and_offscreen_lines_reach_actual_gate_both_routes(self):
        turns = [('我', '修好了吗？'), ('阿宁', '已经修好了。'), ('我', '谢谢你。')]
        for director in (False, True):
            with self.subTest(director=director):
                result = self.compile_prompt(turns, director=director)
                prompt = self.assert_gate(result)
                spoken = [(int(match.group(1)), match.group(3))
                          for match in authoring._H3_SAY.finditer(prompt)]
                self.assertEqual(spoken, [(2, turns[0][1]), (1, turns[1][1]), (2, turns[2][1])])
                self.assertNotIn('我嘴唇开合', prompt)
                self.assertNotRegex(prompt, r'我(?:的)?(?:脸|面部|背影|全身|表情)')
                if director:
                    self.assertIn('阿宁嘴唇开合', prompt)
                self.assertIn('<Subject 3>是<Picture 2>', prompt)
                self.assertNotIn('<Picture 3>', prompt)
                video_continuity.validate_prompt_references(prompt, ['adult_portrait.png', 'shop.png'])

    def test_only_offscreen_camera_speaks_and_takes_no_portrait_slot(self):
        for director in (False, True):
            with self.subTest(director=director):
                result = self.compile_prompt([('我', '时间正好。')], director=director, cards=[])
                prompt = self.assert_gate(result)
                self.assertIn('我说：<Subject 1> (S1) says:<d>[Chinese]时间正好。</d>', prompt)
                self.assertIn('<Subject 2>是<Picture 1>', prompt)
                self.assertNotIn('<Picture 2>', prompt)
                self.assertNotIn('我嘴唇开合', prompt)
                video_continuity.validate_prompt_references(prompt, ['shop.png'])

    def test_silent_camera_stays_without_subject_or_voice(self):
        for turns in ([], [('阿宁', '请收好。')]):
            for director in (False, True):
                with self.subTest(turns=turns, director=director):
                    result = self.compile_prompt(turns, director=director)
                    prompt = self.assert_gate(result)
                    self.assertNotIn('我是这段的拍摄者', prompt)
                    self.assertNotIn('Neutral voice', prompt)
                    self.assertNotIn('我说：<Subject', prompt)
                    self.assertIn('<Subject 2>是<Picture 2>', prompt)

    def test_unresolved_source_labels_survive_both_pov_builders_and_actual_gate(self):
        turns = [('待核对说话人', '手表已经修好了。'), ('说话人待定', '请放在柜台上。')]
        for director in (False, True):
            with self.subTest(director=director):
                result = self.compile_prompt(turns, director=director, omit_speech=True)
                prompt = self.assert_gate(result)
                self.assertEqual([m[3] for m in authoring._H3_SAY.finditer(prompt)],
                                 [text for _, text in turns])
                for label, _ in turns:
                    self.assertNotIn(label + '嘴唇开合', prompt)
                    self.assertNotRegex(prompt, re.escape(label) + r'是<Picture')
                self.assertIn('画面里只有阿宁这1个角色', prompt)
                self.assertIn('<Subject 4>是<Picture 2>', prompt)
                self.assertNotIn('<Picture 3>', prompt)
                video_continuity.validate_prompt_references(prompt, ['adult.png', 'shop.png'])

    def test_mixed_visible_camera_and_unresolved_voices_keep_identical_header_body_ids(self):
        turns = [('我', '请帮我看看。'), ('待核对说话人', '稍等一下。'), ('阿宁', '现在好了。')]
        for director in (False, True):
            with self.subTest(director=director):
                result = self.compile_prompt(turns, director=director, omit_speech=True)
                prompt = self.assert_gate(result)
                self.assertEqual([(int(m[1]), m[3]) for m in authoring._H3_SAY.finditer(prompt)],
                                 [(2, turns[0][1]), (3, turns[1][1]), (1, turns[2][1])])
                self.assertIn('<Subject 4>是<Picture 2>', prompt)
                self.assertNotIn('待核对说话人嘴唇开合', prompt)

    def test_source_repeated_words_keep_each_occurrence_and_speaker(self):
        for turns in ([('我', '好了。'), ('阿宁', '好了。')],
                      [('待核对说话人', '好了。'), ('待核对说话人', '好了。')]):
            for director in (False, True):
                with self.subTest(turns=turns, director=director):
                    result = self.compile_prompt(turns, director=director, omit_speech=True)
                    prompt = self.assert_gate(result)
                    self.assertEqual([m[3] for m in authoring._H3_SAY.finditer(prompt)], ['好了。', '好了。'])

    def test_explicit_named_camera_has_voice_subject_without_image(self):
        for director in (False, True):
            with self.subTest(director=director):
                result = self.compile_prompt([('林海', '表走得很准。')], director=director, camera='林海')
                prompt = self.assert_gate(result)
                self.assertIn('林海说：<Subject 2> (S2) says:', prompt)
                self.assertNotIn('林海嘴唇开合', prompt)
                self.assertIn('<Subject 3>是<Picture 2>', prompt)

    def test_existing_camera_card_retains_original_voice_and_is_not_duplicated(self):
        camera = dict(name='林海', age=32, voice_h3_en='Original low warm voice.', voice_h3_age='32')
        result = self.compile_prompt([('林海', '准时到了。')], director=True, camera='林海',
                                     cards=[camera, copy.deepcopy(self.person)])
        prompt = self.assert_gate(result)
        self.assertIn('Original low warm voice.', prompt)
        self.assertNotIn('Neutral voice', prompt)
        ordered, _ = authoring.h3_prompt_char_order(result[1], result[3], result[2])
        self.assertEqual([row['name'] for row in ordered], ['阿宁', '林海'])
        self.assertIs(ordered[-1], camera)

    def test_multiple_scenes_keep_actual_picture_numbers(self):
        result = self.compile_prompt([('我', '从这里走。')], director=True, scenes=['修表店', '门口'])
        prompt = self.assert_gate(result)
        self.assertIn('<Subject 3>是<Picture 2>', prompt)
        self.assertIn('<Subject 4>是<Picture 3>', prompt)
        self.assertNotIn('<Picture 4>', prompt)
        video_continuity.validate_prompt_references(prompt, ['adult_portrait.png', 'shop.png', 'door.png'])

    def test_mentions_inside_visible_dialogue_do_not_create_camera_voice(self):
        source = '阿宁：我刚才说过“我修好了”。'
        cards = [copy.deepcopy(self.person)]
        ordered, _ = authoring.h3_prompt_char_order(source, cards, self.settings)
        self.assertEqual([row['name'] for row in ordered], ['阿宁'])

    def test_other_camera_modes_do_not_invent_an_unpictured_role(self):
        for mode in ('第三视角', '自拍手持'):
            with self.subTest(mode=mode):
                settings = dict(pov=mode, camera_owner='我')
                ordered, _ = authoring.h3_prompt_char_order('我：已经修好。', [self.person], settings)
                self.assertEqual(ordered, [self.person])

    def test_assemble_optional_offscreen_argument_preserves_visible_mouth_default(self):
        d = dict(camera='中景', cast=['阿宁'], blocks=[[0, 4, '阿宁点头。', [1]]],
                 end_state={}, says=[('阿宁', '请收好。')])
        line = '阿宁说：<Subject 1> (S1) says:<d>[Chinese]请收好。</d>'
        normal = director_shots.assemble('', d, [self.person], {1: line})
        offscreen = director_shots.assemble('', d, [self.person], {1: line}, offscreen_speakers={'阿宁'})
        self.assertIn('阿宁嘴唇开合', normal)
        self.assertNotIn('阿宁嘴唇开合', offscreen)
        self.assertIn(line, offscreen)


if __name__ == '__main__':
    unittest.main()
