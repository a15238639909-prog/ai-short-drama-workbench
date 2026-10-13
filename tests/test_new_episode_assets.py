"""Explicit first-appearance facts and reached new places, CPU/mocks only."""
import copy
import os
os.environ['V41_NO_MODEL'] = '1'
import unittest
from unittest.mock import patch

from cores import authoring, asset_core, story_core, oral_story, character_facts
from cores import continuation_assets as ca


BRIEF = '新增人物周师傅，55岁男性，白发、棕色夹克和深色长裤，原先就在小院的木长凳旁等候。'
PROSE = '阿宁推开修表店的木门。两人跨过门槛，踏上门外青灰色的石板小院。周师傅在木长凳旁等候。'


class NewEpisodeAssetTests(unittest.TestCase):
    def test_chinese_explicit_age_and_comma_definition_have_per_person_ownership(self):
        self.assertEqual(character_facts.explicit_ages(BRIEF, ['周师傅']), {'周师傅': '55'})
        self.assertEqual(character_facts.explicit_ages('周师傅（五十五岁男性）。', ['周师傅']), {'周师傅': '55'})
        self.assertEqual(character_facts.explicit_ages('周师傅，55岁男性，林海，30岁男性。', ['周师傅', '林海']),
                         {'周师傅': '55', '林海': '30'})
        self.assertEqual(oral_story.outfit_hints(BRIEF, ['周师傅']), {'周师傅': ['棕色夹克和深色长裤']})
        hints = oral_story.outfit_hints('周师傅，55岁男，棕色夹克和深色长裤，林海，30岁男，灰色衬衫和黑色长裤。', ['周师傅', '林海'])
        self.assertEqual(hints['周师傅'], ['棕色夹克和深色长裤'])
        self.assertEqual(hints['林海'], ['灰色衬衫和黑色长裤'])

    def test_new_card_prioritizes_first_episode_brief_over_old_plan_and_prose(self):
        story = {'settings': {'one_line': '周师傅，40岁男性，绿色衬衫和白色长裤。'},
                 'saga': {'episodes': [{'no': 3, 'brief': BRIEF, 'prose': '周师傅五十出头，穿着运动服。'}]}}
        made = {}
        def create(sid, fields):
            made.update(fields, character_id='NEW')
            return copy.deepcopy(made)
        source = {'name': '周师傅', 'age': '五十出头', 'sex': '男', 'first_episode': 3,
                  'clothing': '绿色运动夹克与白色长裤'}
        before = copy.deepcopy(source)
        with patch.object(story_core, 'get_story', return_value=story), \
             patch.object(asset_core, 'create_character', side_effect=create), \
             patch.object(asset_core, 'get_asset', side_effect=lambda *a: copy.deepcopy(made)), \
             patch.object(authoring, 'normalize_cards_for_world'), patch.object(authoring, '_dbg'), \
             patch.object(authoring, '_q', side_effect=AssertionError('model forbidden')):
            authoring._create_cards('TEST', [source])
        self.assertEqual(made['age'], '55')
        self.assertEqual(made['clothing'], '棕色夹克；深色长裤')
        self.assertEqual(made['clothing_requirement'], '自定义')
        self.assertTrue(made['clothing_from_brief'])
        self.assertEqual(source, before)

    def test_arrival_is_grounded_and_planned_or_spoken_places_are_excluded(self):
        arrival = ca.arrived_locations(PROSE)
        self.assertEqual([x['name'] for x in arrival], ['石板小院'])
        self.assertIn(arrival[0]['evidence'], PROSE)
        self.assertEqual(ca.arrived_locations('阿宁：走进仓库等我。\n林海打算走进另一家商店。'), [])
        self.assertEqual(ca.arrived_locations('林海走进另一家商店。')[0]['name'], '另一家商店')

    def test_generic_old_location_uses_only_its_primary_saved_space_title(self):
        old = {'name': '店里', 'scene_id': 'OLD', 'space': '室内修表店，中央有工作台，门外是石板小院。'}
        self.assertIs(ca.match_scene('修表店', [old]), old)
        self.assertIsNone(ca.match_scene('石板小院', [old]))
        self.assertIsNone(ca.match_scene('另一家修表店', [old]))
        self.assertIsNone(ca.match_scene('修表店', [old, dict(old, scene_id='OTHER')]))

    def test_both_asset_entries_keep_old_shop_and_add_the_reached_courtyard(self):
        for entry in ('extract', 'new_places'):
            with self.subTest(entry=entry):
                old = {'name': '店里', 'scene_id': 'OLD', 'space': '室内修表店，中央有工作台。', 'visuals': ['ACCEPTED']}
                old_before = copy.deepcopy(old)
                cards, made, saved = [old], [], []
                def create(sid, fields):
                    card = dict(fields, scene_id='NEW')
                    cards.append(card)
                    made.append(card)
                    return card
                with patch.object(authoring, '_ep', return_value=({}, {'no': 3, 'brief': BRIEF, 'prose': PROSE})), \
                     patch.object(authoring, '_extract_cards_raw', return_value=[('场景', '修表店', {'空间': PROSE})]), \
                     patch.object(authoring, 'scenes_from_pictures', return_value=[{'name': '修表店', 'space': PROSE}]), \
                     patch.object(authoring, 'confirmed_arrangement', return_value=None), \
                     patch.object(story_core, 'get_story', return_value={'settings': {}}), \
                     patch.object(asset_core, 'list_assets', side_effect=lambda sid, kind: cards if kind == 'scenes' else []), \
                     patch.object(asset_core, 'create_scene', side_effect=create), \
                     patch.object(asset_core, 'save_asset', side_effect=lambda sid, kind, card: saved.append(card)), \
                     patch.object(authoring, '_dbg'), patch.object(authoring, '_q', side_effect=AssertionError('model forbidden')):
                    if entry == 'extract':
                        authoring.prepare_episode_assets('TEST', 3)
                    else:
                        authoring.new_places_for_episode('TEST', 3)
                self.assertEqual([x['name'] for x in made], ['石板小院'])
                self.assertTrue(made[0]['explicit_destination'])
                self.assertEqual(made[0]['first_episode'], 3)
                self.assertTrue(all(c['scene_id'] == 'NEW' for c in saved))
                self.assertEqual(old, old_before)


if __name__ == '__main__':
    unittest.main()
