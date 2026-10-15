"""Story-first character images, explicit later edits, and selected styles; no AI."""
import copy
import os
os.environ['V41_NO_MODEL'] = '1'
import unittest
from unittest.mock import patch

from cores import authoring, asset_core, gen_history, project_settings, prompt_writer as pw
from cores import reference_ready, story_core, style_presets as sp
from models import krea_client


class StoryFirstCharacterTests(unittest.TestCase):
    def card(self):
        return dict(character_id='TEST_PERSON', name='测试人物', age='中年', sex='男',
                    hair='黑色短发', face_spec={'face_shape': '圆脸', 'eye_len': '短',
                    'eye_tail': '平', 'brow': '平眉', 'brow_bone': '低', 'nose_bridge': '低',
                    'nose_tip': '圆', 'lip': '薄', 'jaw': '圆'}, clothing='蓝色长衫',
                    appearance_details='身形矮胖，左眉有一道白色疤痕', build='偏胖',
                    sheet_body_ratio='高挑修长')

    def test_first_image_keeps_story_without_expanding_automatic_presets(self):
        card = self.card()
        text = authoring._char_one_line(card, {'style': '平面动漫'})
        self.assertIn(card['appearance_details'], text)
        self.assertIn('原始身材记录（只保留原意，不扩写预设）：偏胖', text)
        self.assertNotIn(sp.body_def('男', '偏胖'), text)
        self.assertNotIn(sp.body_ratio_def('高挑修长'), text)
        for label in ('明确身高（按人物卡原值）：', '手动覆盖身体比例（', '手动覆盖身材（'):
            self.assertNotIn(label, text)

    def test_unrelated_user_edits_do_not_enable_body_presets(self):
        card = self.card()
        card.update(edited_by_user=True, name='新名字', hair='银色短发')
        text = authoring._char_one_line(card, {})
        self.assertIn('新名字', text)
        self.assertNotIn(sp.body_ratio_def('高挑修长'), text)
        self.assertNotIn(sp.body_def('男', '偏胖'), text)

    def test_explicit_height_ratio_and_build_reach_both_text_stages(self):
        card = self.card()
        asset_core.apply_fields(card, {'height': '175cm', 'sheet_ratio_override': '高挑修长',
                                      'sheet_body_override': '偏胖'}, asset_core.CHARACTER_FIELDS)
        original = copy.deepcopy(card)
        facts = {'appearance': {'肤色': '小麦色'}, 'expression': '平静', 'clothing': []}
        with patch.object(pw, 'prepare_character_facts', return_value=facts) as prepare, \
             patch.object(pw, 'write_character', return_value=('穿着蓝色长衫的人物。', '')) as write, \
             patch.object(asset_core, 'get_asset', return_value=None):
            authoring.write_char_sheet('TEST', card, {'style': '平面动漫'})
        for text in (prepare.call_args.args[0], write.call_args.args[0]):
            self.assertIn('明确身高（按人物卡原值）：175cm', text)
            self.assertIn(sp.body_ratio_def('高挑修长'), text)
            self.assertIn('手动覆盖身材（仅用于人设图，以本项为准）：', text)
        self.assertEqual(card, original)

    def test_height_save_invalidates_old_prompt_and_can_be_cleared(self):
        card = dict(height='', image_prompt='old prompt')
        asset_core.apply_fields(card, {'height': '175cm'}, asset_core.CHARACTER_FIELDS)
        self.assertEqual(card, {'height': '175cm', 'image_prompt': ''})
        asset_core.apply_fields(card, {'height': ''}, asset_core.CHARACTER_FIELDS)
        self.assertNotIn('明确身高（按人物卡原值）：', authoring._char_one_line(card, {}))

    def test_selected_style_reaches_the_actual_character_instruction(self):
        for style, medium in [('网红自然感', '真实摄影成像'), ('平面动漫', '二维绘画成像'),
                              ('3D游戏', '三维角色与场景渲染')]:
            with self.subTest(style=style), patch.object(pw, '_call', return_value='【出图提示词】人物。') as call:
                pw.write_character('人物事实', {'style': style, 'world_type': '东方'})
                instruction = call.call_args.args[0]
                self.assertIn(style, instruction)
                self.assertIn(medium, instruction)
                self.assertIn('不把自动标签扩写成预设正文', instruction)

    def test_batch_visual_records_keep_the_selected_style(self):
        card = self.card()
        card['prompt_override'] = '穿着蓝色长衫的中年人物'
        for style in ('网红自然感', '平面动漫', '3D游戏'):
            with self.subTest(style=style), \
                 patch.object(authoring, 'hair_is_designed', return_value=True), \
                 patch.object(asset_core, 'list_assets', return_value=[]), \
                 patch.object(asset_core, 'add_approved_visual', return_value={'visual_id': 'V1'}) as add, \
                 patch.object(asset_core, 'adopt'), patch.object(gen_history, 'record'), \
                 patch.object(krea_client, 'generate', return_value={'output_path': 'mock.png'}), \
                 patch('os.makedirs'), patch('os.path.exists', return_value=False), patch('shutil.copy2'):
                result = authoring._gen_char_images('TEST', [card], {'style': style})
            self.assertEqual(result['failed'], [])
            self.assertEqual(add.call_args.kwargs['style_profile'], style)

    def test_generation_uses_the_saved_project_selection(self):
        story = {'settings': {'style': '网红自然感'}, 'project_settings': {'style': '平面动漫'}}
        with patch.object(story_core, 'get_story', return_value=story), \
             patch.object(reference_ready, 'adopted_paths', return_value={}), \
             patch.object(asset_core, 'list_assets', return_value=[self.card()]), \
             patch.object(authoring, '_gen_char_images', return_value={'ok': 1, 'failed': []}) as generate:
            authoring.gen_char_images('TEST')
        self.assertEqual(generate.call_args.args[2]['style'], '平面动漫')


if __name__ == '__main__':
    unittest.main()
