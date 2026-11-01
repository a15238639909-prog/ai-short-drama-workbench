"""CPU regressions: material detail must preserve the scene's condition."""
import copy
import os
os.environ['V41_NO_MODEL'] = '1'
import unittest
from unittest.mock import patch

from cores import quality_core, store


class SceneMaterialConditionTests(unittest.TestCase):
    STYLES = ('电影级写实', '平面动漫', '3D游戏')
    UNREQUESTED_AGING = ('氧化', '磨损', '裂纹', '开裂', '污渍', '脏色', '积灰', '旧感')

    def compile(self, card, style):
        # The compiler is pure; fail loudly if a future change tries to persist.
        with patch.object(store, 'save_json', side_effect=AssertionError('no production writes')):
            return quality_core.compile_scene_prompt(
                dict(card, _project={'style': style}), style=style, appearance_only=True)

    def card(self):
        return {
            'name': '公寓客厅',
            'space': '室内公寓客厅，完整的暖白墙面与胡桃木柜体，深灰石板地面沿窗边展开',
            'materials': '暖白墙面、胡桃木柜体、深灰石板地面、透明玻璃窗与织物沙发',
            'light': '夜晚右侧落地灯照亮沙发一角，其余区域沉入深灰阴影',
        }

    def test_maintained_scene_does_not_gain_default_aging_in_any_medium(self):
        card = self.card()
        for style in self.STYLES:
            with self.subTest(style=style):
                prompt = self.compile(card, style)
                self.assertIn('胡桃木柜体', prompt)
                self.assertIn('深灰石板地面', prompt)
                for word in self.UNREQUESTED_AGING:
                    self.assertNotIn(word, prompt)

    def test_explicit_old_damage_is_preserved_in_any_medium(self):
        card = self.card()
        card.update(name='废弃值班室',
                    space='室内废弃值班室，木门漆面开裂，金属桌腿有锈迹，墙面残留污渍',
                    materials='木门漆面开裂，金属桌腿有锈迹，墙面残留污渍，石板地面有磨损')
        before = copy.deepcopy(card)
        for style in self.STYLES:
            with self.subTest(style=style):
                prompt = self.compile(card, style)
                for fact in ('木门漆面开裂', '金属桌腿有锈迹', '墙面残留污渍', '石板地面有磨损'):
                    self.assertIn(fact, prompt)
                self.assertNotIn('豪华', prompt)
                self.assertNotIn('崭新', prompt)
        self.assertEqual(card, before)

    def test_existing_dark_lighting_survives_material_change(self):
        card = self.card()
        for style in self.STYLES:
            with self.subTest(style=style):
                prompt = self.compile(card, style)
                self.assertIn(card['light'], prompt)
                self.assertNotIn('阳光明媚', prompt)
                self.assertNotIn('均匀明亮', prompt)

    def test_material_language_matches_selected_medium(self):
        card = self.card()
        photo = self.compile(card, '电影级写实')
        drawn = self.compile(card, '平面动漫')
        cg = self.compile(card, '3D游戏')
        self.assertIn(quality_core.MATERIAL_REAL, photo)
        self.assertIn(quality_core.MATERIAL_DRAWN, drawn)
        self.assertIn(quality_core.MATERIAL_CG, cg)
        self.assertNotIn(quality_core.MATERIAL_DRAWN, photo)
        self.assertNotIn(quality_core.MATERIAL_REAL, drawn)
        self.assertNotIn(quality_core.MATERIAL_DRAWN, cg)

    def test_render_notes_do_not_reintroduce_aging_for_photo_presets(self):
        card = self.card()
        for style in ('电影级写实', 'Krea2·超仿真生活照'):
            with self.subTest(style=style), \
                 patch.object(store, 'save_json', side_effect=AssertionError('no production writes')):
                prompt = quality_core.compile_scene_prompt(
                    dict(card, _project={'style': style}), style=style)
                for word in self.UNREQUESTED_AGING + ('磕碰', '指印', '岁月痕迹'):
                    self.assertNotIn(word, prompt)
                self.assertIn(card['light'], prompt)


if __name__ == '__main__':
    unittest.main()
