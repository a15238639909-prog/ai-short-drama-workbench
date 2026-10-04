import os
import unittest
os.environ['V41_NO_MODEL'] = '1'
from cores import oral_story as oral, authoring as au, director_shots as ds


class DialogueTest(unittest.TestCase):
    def test_cues_preserved_outside_speech(self):
        source = '田丰： （看向萌萌）萌萌，你愿意跟我走吗？\n萌萌（点头）：（轻声）我愿意。'
        numbered, lines = oral.numbered_script(source)
        self.assertEqual(lines, [('田丰', '萌萌，你愿意跟我走吗？'), ('萌萌', '我愿意。')])
        self.assertIn('田丰，看向萌萌。', numbered)
        self.assertIn('萌萌，点头，轻声。', numbered)
        self.assertEqual(au._pic_parts_dialogue(source), lines)
        parts = au._pic_parts(source)
        self.assertIn((False, '萌萌，点头，轻声。'), parts)

    def test_old_project_examples(self):
        for cue in ('低喝', '护住萌萌', '愣住', '平静', '咬牙', '怒吼', '喘息', '摇头', '微笑', '眯起眼睛'):
            self.assertEqual(oral.dialogue_performance('（' + cue + '）跟我走！'), ('跟我走！', [cue]))

    def test_meaningful_parentheses_preserved(self):
        for text in ('（明天或后天）我们再去。', '你说的（东门）我找到了。', '“（低声）是舞台提示。”'):
            self.assertEqual(oral.dialogue_performance(text), (text, []))

    def test_no_invented_dialogue(self):
        text = '田丰迈上台阶，右手提着剑。'
        self.assertEqual(oral.numbered_script(text), (text, []))
        self.assertEqual(au._pic_parts_dialogue(text), [])

    def test_dialogue_budget_and_camera_rules_coexist(self):
        rules = ds.instruction(['大殿'], [('田丰', '男', 24)])
        self.assertIn('每秒约4～5个汉字', rules)
        self.assertIn('4～15 秒', rules)
        self.assertIn('移动交锋用全景跟拍', rules)

    def test_redistribute_long_line_without_changing_actions_or_total(self):
        d = {'blocks': [[0, 3, '萌萌跑去', [9]], [3, 6, '张林起身', [10]],
                        [6, 12, '弟子围上来', []], [12, 15, '田丰格挡', []]],
             'says': [('萌萌', '我愿意。'), ('张林', '这里是你想来就来，想走就走？非要坏我好事，拿下他们！')]}
        fixed = ds.fit_dialogue_timing(d)
        self.assertGreaterEqual(fixed['blocks'][1][1] - fixed['blocks'][1][0], 6)
        self.assertEqual(fixed['blocks'][-1][1], 15)
        self.assertEqual([b[2:] for b in fixed['blocks']], [b[2:] for b in d['blocks']])
        self.assertEqual(d['blocks'][1][:2], [3, 6])
        self.assertEqual(ds.fit_dialogue_timing(fixed), fixed)

    def test_impossible_speech_stops_before_video(self):
        with self.assertRaisesRegex(ValueError, '对白时间不足'):
            ds.fit_dialogue_timing({'blocks': [[0, 3, '开口', [1]]], 'says': [('甲', '字' * 40)]})


if __name__ == '__main__':
    unittest.main()
