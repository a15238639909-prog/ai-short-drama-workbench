"""The four visible worlds must expose their complete outfit libraries; no AI."""
import copy
import os
os.environ['V41_NO_MODEL'] = '1'
import unittest
from unittest.mock import patch

from cores import authoring, asset_core, project_prompt as pp


class OutfitWorldOptionsTests(unittest.TestCase):
    WORLDS = {'东方': '东方仙侠', '西方': '西方魔幻', '未来': '未来科幻', '现代': '现代都市'}

    def test_world_labels_settings_and_legacy_keys_return_the_same_full_options(self):
        library = pp.load_outfit_library()
        for world, key in self.WORLDS.items():
            with self.subTest(world=world):
                expected = ['自动'] + [entry['name'] for entry in library[key]] + ['自定义']
                self.assertEqual(pp.outfit_preset_options(world), expected)
                self.assertEqual(pp.outfit_preset_options({'world_type': world}), expected)
                self.assertEqual(pp.outfit_preset_options(key), expected)

    def test_every_selectable_outfit_resolves_to_its_own_full_description(self):
        for world in self.WORLDS:
            with self.subTest(world=world):
                definitions = pp.outfit_preset_defs(world)
                self.assertEqual(definitions, pp.outfit_preset_defs({'world_type': world}))
                options = pp.outfit_preset_options(world)[1:-1]
                self.assertEqual(set(options), set(definitions))
                self.assertEqual(len(options), len(set(options)))
                for name in options:
                    entry, _ = pp._choose_outfit_entry({'outfit_preset': name}, {'world_type': world})
                    self.assertEqual(entry['name'], name)
                    self.assertEqual(entry['prompt'], definitions[name])

    def test_story_specified_outfit_is_not_replaced_by_the_larger_library(self):
        for world in self.WORLDS:
            with self.subTest(world=world):
                card = dict(name='测试人物', sex='男', age='中年', char_type='商人',
                            clothing='蓝色外套；黑色长裤；白色运动鞋', clothing_requirement='自定义',
                            clothing_from_brief=True)
                original = copy.deepcopy(card)
                with patch.object(asset_core, 'save_asset') as save:
                    authoring.normalize_cards_for_world('TEST', {'world_type': world}, cards=[card])
                self.assertEqual(card, original)
                save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
