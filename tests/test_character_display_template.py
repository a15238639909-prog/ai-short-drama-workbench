"""CPU-only checks for the character display-template contract."""
import os
os.environ['V41_NO_MODEL'] = '1'
import unittest
from unittest.mock import patch
from pathlib import Path
from cores import authoring, style_presets as sp, prompt_writer as pw
from api import settings_api as api, download_api

ROOT = Path(__file__).resolve().parents[1]
HEADER = '人设图展示模板（姿势＋版式）：'

class DisplayTemplateTests(unittest.TestCase):
    def test_normal_template_is_first_before_and_after_fact_preparation(self):
        facts = {'appearance': {'肤色': '小麦色'}, 'expression': '神情平静', 'clothing': []}
        for sex in ('男', '女'):
            card = {'name': '测试人物', 'sex': sex, 'age': 24, 'char_type': '人类',
                    'sheet_body_ratio': '标准成人', 'build': '标准', 'clothing': '衬衫和长裤'}
            template = sp.sheet_pose_def(sex)
            self.assertEqual(template.count('16:9'), 1)
            for prepared in (None, facts):
                text = authoring._char_one_line(card, {}, prepared)
                self.assertTrue(text.startswith(HEADER + template + '\n'))
                self.assertEqual(text.count('16:9'), 1)

    def test_special_template_replaces_the_whole_display_template(self):
        template = '人物坐在椅子上。左右两格展示正面和侧面。'
        with patch.object(sp, 'character_figure_variant', return_value={'pose': template, 'clothing': ''}):
            text = authoring._char_one_line({'name': '测试', 'sex': '男', 'age': 24}, {})
        self.assertTrue(text.startswith(HEADER + template + '\n'))
        self.assertNotIn('16:9', text)

    def test_main_instruction_references_instead_of_repeating_layout(self):
        text = (pw.INS_DIR / pw.INSTRUCTIONS['character']).read_text(encoding='utf-8')
        self.assertIn('人设图展示模板（姿势＋版式）', text)
        for fixed in ('16:9', '四格', '头肩特写', '人设图姿势'):
            self.assertNotIn(fixed, text)

    def test_old_and_new_settings_labels_save_to_same_key(self):
        for cn, en in (('男性', 'male'), ('女性', 'female')):
            key = 'figure_pose_' + en
            self.assertEqual(api._LAYER_CN[key], '人设图·' + cn + '姿势＋版式')
            self.assertEqual(api._LAYER_EN['人设图·' + cn + '姿势＋版式'], key)
            self.assertEqual(api._LAYER_EN['人设图·' + cn + '姿势替换'], key)

    def test_downloader_is_bundled(self):
        if not os.environ.get('DOUYIN_DOWNLOADER_ROOT'):
            self.assertEqual(download_api.TOOL_ROOT, ROOT / 'tools' / 'douyin-downloader')
        for name in ('app.py', 'downloader.py', 'index.html', 'folder_picker.py'):
            self.assertTrue((ROOT / 'tools' / 'douyin-downloader' / name).is_file())

if __name__ == '__main__':
    unittest.main()
