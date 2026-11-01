"""Normal adult source fixtures: actual POV compiler, repair and render gate."""
import copy
import os
import re
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from api import segment_api
from cores import authoring, camera_view


class POVSourceVoiceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.cards = [dict(name='阿宁', age=30, appearance='成年修表师，深蓝工作服',
                          voice_h3_en='Natural adult voice.', voice_h3_age='30')]
        self.settings = dict(pov='第一视角POV', camera_owner='我')
        self.source = '我主观视角，阿宁扶稳柜台上的手表。\n待核对说话人：请拿好。'

    def compile(self, reply='', error=None):
        before = copy.deepcopy((self.cards, self.settings))
        with patch.object(authoring, '_q', return_value=reply, side_effect=error) as q:
            prompt = authoring.h3_prompt(self.source, self.cards, '修表店', self.settings,
                                         scene_image=True, seconds=8)
        self.assertEqual(self.cards, before[0])
        self.assertEqual(self.settings['camera_owner'], before[1]['camera_owner'])
        row = dict(no=1, source_text=self.source, chars=['阿宁'], scene='修表店', seconds=8, plan={})
        self.assertEqual(segment_api._render_critical_problems(dict(settings=self.settings),
            {}, row, prompt, 1, 1), [])
        return prompt, q.call_count

    def test_empty_model_replies_use_saved_actions_and_restore_speech_without_media(self):
        prompt, calls = self.compile()
        self.assertEqual(calls, 2)
        self.assertIn('0—8秒：我主观视角', prompt)
        self.assertIn('阿宁扶稳柜台上的手表', prompt)
        self.assertIn('<d>[Chinese]请拿好。</d>', prompt)
        self.assertNotIn('待核对说话人嘴唇开合', prompt)
        self.assertTrue(camera_view.generate.last_problems)

    def test_text_service_timeout_uses_source_once_and_keeps_warning(self):
        prompt, calls = self.compile(error=TimeoutError('simulated text outage'))
        self.assertEqual(calls, 1)
        self.assertIn('<d>[Chinese]请拿好。</d>', prompt)
        self.assertTrue(any('simulated text outage' in reason for reason in camera_view.generate.last_problems))

    def test_control_and_game_guard_are_never_swallowed_by_source_fallback(self):
        for error in (RuntimeError('PUBG正在运行'), RuntimeError('任务已取消')):
            with self.subTest(message=str(error)), patch.object(authoring, '_q', side_effect=error):
                with self.assertRaises(RuntimeError):
                    authoring.h3_prompt(self.source, self.cards, '修表店', self.settings, seconds=8)

    def test_repair_restores_exact_order_counts_and_speakers_when_model_repeats(self):
        canon = ['我说：<Subject 2> (S2) says:<d>[Chinese]好了。</d>',
                 '阿宁说：<Subject 1> (S1) says:<d>[Chinese]好了。</d>',
                 '待核对说话人说：<Subject 3> (S3) says:<d>[Chinese]谢谢。</d>']
        cards = self.cards + [dict(name='待核对说话人', _h3_voice_only=True)]
        bad = ('0—8秒：我主观视角，阿宁收好工具。\n' + canon[2] + '\n' + canon[0]
               + '\n' + canon[0] + '\n' + canon[0] + '\n这一段结束时：手表在柜台上。')
        result = camera_view.format_dialogue(bad, canon, cards, '我')
        self.assertEqual([m[0] for m in re.finditer(r'[^\n]+says:<d>[^\n]+', result)], canon)
        self.assertNotIn('待核对说话人嘴唇开合', result)
        self.assertNotIn('我嘴唇开合', result)
        self.assertIn('阿宁嘴唇开合。', result)

    def test_written_sign_stays_visual_and_does_not_create_speech(self):
        self.source = '我主观视角，阿宁把写着“营业中”的木牌翻到正面。'
        prompt, _ = self.compile(reply='0—8秒：我主观视角，阿宁翻正写着“营业中”的木牌。\n这一段结束时：木牌正面朝外。')
        self.assertNotIn('<d>', prompt)
        self.assertNotIn('身份未核定', prompt)


if __name__ == '__main__':
    unittest.main()
