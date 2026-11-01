"""Photographic detail may enrich source actions; backend policy stays private."""
import json
import unittest

from cores import workbench_story_director as writer
from tests.test_source_shot_compiler import model_reply
from tests.test_workbench_story_director_release import fixture


class PositivePhotographyTests(unittest.TestCase):
    def run_writer(self, modify=None):
        slices, cards, settings = fixture()
        settings.pop('video_action_compiler')
        settings['style'] = '复古日剧DV实拍，低饱和冷色，轻微颗粒'
        settings['_director_scene_facts'] = {'竹庭': dict(space='木桌位于竹庭回廊旁', light='阴天柔和散射光')}
        calls = []
        def q(system, user, **options):
            payload = json.loads(user)
            calls.append(payload)
            plan = model_reply(payload)
            plan['shots'][0]['visual'].update(
                composition='木桌横向展开于前景，竹影在后方形成纵深。',
                lighting='阴天散射光从回廊一侧照亮手背，阴影呈柔和冷灰色。',
                texture='茶杯釉面带有细小柔亮反光，袖口布料保留清楚的织纹。',
                performance='手部力度平稳。',
                camera_detail='焦点停在手与杯沿的接触处，背景竹叶柔和虚化。')
            if modify:
                modify(plan, len(calls))
            return json.dumps(plan, ensure_ascii=False)
        row = writer.make_prompts(slices, cards, ['竹庭'], q, settings=settings, count=1)[0]
        return row, calls

    def test_style_scene_and_concrete_detail_reach_final_text(self):
        row, calls = self.run_writer()
        self.assertEqual(len(calls), 1)
        self.assertFalse(row['source_fallback'])
        self.assertEqual(calls[0]['scene_facts']['light'], '阴天柔和散射光')
        self.assertEqual(calls[0]['appearance_facts'][0]['name'], '阿岚')
        body = row['prompt'].split('detailed_description:\n')[1].split('\n\noverall_soundscape:')[0]
        for text in ('复古日剧DV实拍', '低饱和冷色', '手背', '釉面', '织纹', '杯沿的接触处'):
            self.assertIn(text, body)
        self.assertEqual(body.count('慢一点。'), 1)
        self.assertEqual(row['plan']['prompt_content_policy'], 'positive-visual-v1')

    def test_added_story_event_is_rejected_and_repaired(self):
        def modify(plan, attempt):
            if attempt == 1:
                plan['shots'][0]['visual']['performance'] = '人物打开车门，随后关上车门。'
        row, calls = self.run_writer(modify)
        self.assertEqual(len(calls), 2)
        self.assertNotIn('打开车门', row['prompt'])
        self.assertFalse(row['source_fallback'])

    def test_backend_rules_and_negative_instructions_are_rejected(self):
        for instruction in ('不得改变服装，不要出现多余人物。', 'Current wardrobe contract: {}',
                            '禁止水印，negative prompt: extra fingers'):
            def modify(plan, attempt):
                if attempt == 1:
                    plan['shots'][0]['visual']['composition'] = instruction
            with self.subTest(instruction=instruction):
                row, calls = self.run_writer(modify)
                self.assertEqual(len(calls), 2)
                self.assertNotIn(instruction, row['prompt'])

    def test_unreliable_optional_detail_is_omitted_with_positive_source_preserved(self):
        def modify(plan, attempt):
            plan['shots'][0]['visual']['performance'] = '走出庭院，进入车内。'
        row, calls = self.run_writer(modify)
        self.assertEqual(len(calls), 2)
        self.assertFalse(row['source_fallback'])
        audit = row['director_compilation']['source_shot_audit']
        self.assertTrue(audit['photography_review_required'])
        self.assertIn(['shots', 0, 'visual', 'performance'], audit['omitted_visual_fields'])
        self.assertTrue(any('需复核' in warning for warning in row['problems']))
        self.assertNotIn('走出庭院', row['prompt'])
        self.assertIn('复古日剧DV实拍', row['prompt'])
        self.assertIn('<Subject 1>扶稳茶杯', row['prompt'])
        self.assertEqual(row['prompt'].count('慢一点。'), 1)


if __name__ == '__main__':
    unittest.main()
