"""Empty reference-board facts: no model, media, project or disk mutations."""
import copy
import os
import unittest

os.environ['V41_NO_MODEL'] = '1'

from cores import asset_core, authoring


class SceneBoardCurationTests(unittest.TestCase):
    def test_temporary_coat_keeps_the_chair_and_its_material(self):
        card = {'name': '会客室', 'space': '室内，前景是右侧椅背上搭着的黑色外套。',
                'fixtures': '木椅｜靠右墙，椅背搭着刚脱下的外套｜深棕色橡木，表面完好。'}
        result = asset_core.scene_image_card(card)
        self.assertNotIn('外套', result['space'] + result['fixtures'])
        self.assertIn('右侧椅背', result['space'])
        self.assertIn('深棕色橡木', result['fixtures'])
        self.assertIn('靠右墙', result['fixtures'])

    def test_explicitly_removed_clothes_are_not_restored_as_scene_props(self):
        card = {'space': '左侧是木制沙发。刚脱下的上衣堆在地毯上。',
                'furniture': '黑色外套搭在右侧木椅上。'}
        result = asset_core.scene_image_card(card)
        self.assertNotIn('上衣', result['space'])
        self.assertNotIn('外套', result['furniture'])
        self.assertIn('右侧木椅', result['furniture'])

    def test_lock_result_is_removed_but_door_and_hardware_remain(self):
        card = {'door': '左侧厚重雕花木门已反锁，门上装有黄铜门锁和反锁装置。',
                'space': '左侧是深色木门，已反锁。右侧是壁炉。'}
        result = asset_core.scene_image_card(card)
        self.assertNotIn('已反锁', result['door'] + result['space'])
        self.assertIn('左侧厚重雕花木门', result['door'])
        self.assertIn('黄铜门锁和反锁装置', result['door'])
        self.assertIn('右侧是壁炉', result['space'])

    def test_plain_closed_door_and_closed_shop_are_source_conditions(self):
        card = {'space': '关闭的店铺，左侧木门关闭，门上配铜制门锁。'}
        self.assertEqual(asset_core.scene_image_card(card)['space'], card['space'])

    def test_clothing_stock_and_intentional_display_are_preserved(self):
        card = {'name': '服装店', 'fixtures': '衣帽架上整齐挂着外套。',
                'furniture': '展示用木椅上搭着一件蓝色外套。'}
        result = asset_core.scene_image_card(card)
        self.assertEqual(result['fixtures'], card['fixtures'])
        self.assertEqual(result['furniture'], card['furniture'])

    def test_human_reflection_is_removed_but_architecture_reflection_remains(self):
        card = {'space': '石墙上投着两人交叠的影子，地面映出高窗和拱顶。其他窗户的倒影清晰。',
                'light': '暖光照亮木栏，林岚的倒影映在玻璃上。'}
        result = asset_core.scene_image_card(card, ['林岚'])
        self.assertNotIn('影子', result['space'])
        self.assertNotIn('倒影', result['light'])
        self.assertIn('地面映出高窗和拱顶', result['space'])
        self.assertIn('其他窗户的倒影清晰', result['space'])
        self.assertIn('暖光照亮木栏', result['light'])

    def test_existing_damage_and_maintenance_are_preserved(self):
        for state in ('石墙局部开裂，窗框锈蚀，旧地毯有磨损。',
                      '浅色石墙维护完好，木门漆面细腻，地毯洁净。'):
            with self.subTest(state=state):
                self.assertEqual(asset_core.scene_image_card({'space': state})['space'], state)

    def design_card(self):
        design = ('【景】会客室\n【固有陈设】\n木椅｜右墙边｜橡木，维护完好\n'
                  '【人物痕迹】\n椅背上的外套｜右墙边\n'
                  '【一句话空间】\n室内，左侧雕花木门，已反锁，右侧是壁炉。')
        fields = authoring._design_fields(authoring.parse_design(design)[0])
        return dict(fields, name='会客室', scene_id='S1')

    def test_design_story_trace_column_is_excluded_from_board(self):
        card = self.design_card()
        result = asset_core.scene_image_card(card)
        self.assertEqual(result['furniture'], '')
        self.assertIn('木椅', result['fixtures'])
        self.assertNotIn('已反锁', result['space'])

    def test_manually_changed_furniture_is_preserved_after_design(self):
        card = self.design_card()
        card['furniture'] = '左侧固定书架｜深色橡木，书籍整齐陈列。'
        self.assertEqual(asset_core.scene_image_card(card)['furniture'], card['furniture'])

    def test_source_and_design_snapshot_are_not_mutated(self):
        card = self.design_card()
        source = copy.deepcopy(card)
        asset_core.scene_image_card(card)
        self.assertEqual(card, source)


if __name__ == '__main__':
    unittest.main()
