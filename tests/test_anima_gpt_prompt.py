"""GPT Anima instruction routing with every model call mocked."""
import os
import unittest
from unittest.mock import Mock, patch

from cores import authoring
from models import anima_client as anima


GPT_SYSTEM = '【GPT优化指令词：Anima】\n依据用户需求输出英文标签与短句，保持人物和场景事实。'
SHEET_OUTPUT = 'front full-body view, right-facing profile full-body view, back view, left panel'


class AnimaGptPromptTests(unittest.TestCase):
    def setUp(self):
        self.default_instruction = '默认场景主指令'
        self.sheet_instruction = '旧版人物三视图主指令'
        default_path, sheet_path = Mock(), Mock()
        default_path.read_text.return_value = self.default_instruction
        sheet_path.read_text.return_value = self.sheet_instruction
        patches = [
            patch.dict(os.environ, {'V41_NO_MODEL': '1'}),
            patch.object(anima, 'SYSTEM', default_path),
            patch.object(anima, 'SHEET_SYSTEM', sheet_path),
            patch.object(anima, 'local_guard'),
            patch.object(anima, 'idle_guard'),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        query_patch = patch.object(authoring, '_q', return_value='stone bridge, Soft sunlight illuminates the stone arch.')
        self.query = query_patch.start()
        self.addCleanup(query_patch.stop)

    def test_gpt_scene_preserves_main_instruction_and_checks_capacity(self):
        result = anima.write_prompt('晨光下的石桥。', GPT_SYSTEM)
        self.assertEqual(result, anima.FIXED_PREFIX + ', stone bridge, Soft sunlight illuminates the stone arch.')
        self.assertEqual(self.query.call_args.args, (GPT_SYSTEM + anima.BODY_INSTRUCTION, '晨光下的石桥。'))
        self.assertEqual(self.query.call_args.kwargs,
                         {'mt': 1100, 'temperature': 0.5, '_tries': 1, '_check_context': True})

    def test_gpt_three_view_keeps_custom_main_and_appends_layout(self):
        self.query.return_value = SHEET_OUTPUT
        result = anima.write_prompt('成年研究员的人设三视图。', GPT_SYSTEM)
        instruction = self.query.call_args.args[0]
        self.assertEqual(instruction, GPT_SYSTEM + '\n本次布局：三区：正面全身、90度侧面全身、背面全身。' + anima.BODY_INSTRUCTION)
        self.assertNotIn(self.sheet_instruction, instruction)
        self.assertNotIn('用户附加指令', instruction)
        self.assertTrue(self.query.call_args.kwargs['_check_context'])
        self.assertEqual(result, anima.FIXED_PREFIX + ', ' + SHEET_OUTPUT)

    def test_gpt_four_view_keeps_custom_main_and_appends_portrait_layout(self):
        self.query.return_value = SHEET_OUTPUT + ', headshot'
        result = anima.write_prompt('成年研究员的人设三视图与正脸特写。', GPT_SYSTEM)
        self.assertEqual(self.query.call_args.args[0], GPT_SYSTEM +
                         '\n本次布局：四区：正面全身、90度侧面全身、背面全身、单张正脸特写。' + anima.BODY_INSTRUCTION)
        self.assertTrue(anima.sheet_layout_valid(result, portrait=True))

    def test_gpt_missing_required_views_or_portrait_remains_rejected(self):
        cases = [('成年研究员的人设三视图。', 'front full-body view, left panel'),
                 ('成年研究员的人设三视图与正脸特写。', SHEET_OUTPUT)]
        for text, output in cases:
            with self.subTest(text=text):
                self.query.return_value = output
                with self.assertRaisesRegex(ValueError, '缺少正面、侧面、背面或分区信息'):
                    anima.write_prompt(text, GPT_SYSTEM)

    def test_default_scene_keeps_previous_instruction_and_arguments(self):
        anima.write_prompt('晨光下的石桥。')
        self.assertEqual(self.query.call_args.args[0], self.default_instruction + anima.BODY_INSTRUCTION)
        self.assertEqual(self.query.call_args.kwargs, {'mt': 1100, 'temperature': 0.5, '_tries': 1})

    def test_default_sheet_still_uses_fixed_sheet_instruction(self):
        self.query.return_value = SHEET_OUTPUT
        anima.write_prompt('成年研究员的人设三视图。', self.default_instruction)
        self.assertEqual(self.query.call_args.args[0], self.sheet_instruction +
                         '\n本次布局：三区：正面全身、90度侧面全身、背面全身。' + anima.BODY_INSTRUCTION)
        self.assertNotIn('_check_context', self.query.call_args.kwargs)

    def test_legacy_custom_sheet_stays_an_additional_instruction(self):
        self.query.return_value = SHEET_OUTPUT
        anima.write_prompt('成年研究员的人设三视图。', '保留蓝色工作外套。')
        self.assertEqual(self.query.call_args.args[0], self.sheet_instruction +
                         '\n用户附加指令：\n保留蓝色工作外套。' +
                         '\n本次布局：三区：正面全身、90度侧面全身、背面全身。' + anima.BODY_INSTRUCTION)
        self.assertNotIn('_check_context', self.query.call_args.kwargs)

    def test_legacy_four_view_preset_stays_unchanged(self):
        custom = '【Anima预设：人设三视图＋面部特写】\n沿用既有四区人物板。'
        self.query.return_value = SHEET_OUTPUT + ', headshot'
        anima.write_prompt('成年研究员。', custom)
        self.assertEqual(self.query.call_args.args[0], custom + anima.BODY_INSTRUCTION)
        self.assertNotIn('_check_context', self.query.call_args.kwargs)


if __name__ == '__main__':
    unittest.main()
