"""New-card initial outfit facts; all model replies and storage are CPU mocks."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from cores import authoring, asset_core, character_facts, one_shot_setup, story_core
from cores import source_wardrobe_plan as wardrobe


STORY = Path('D:/AI视频工作台/data/stories/STORY_071.json')
INITIAL = ['玄青外袍', '月白窄袖中衣', '靛青长裤', '黑布靴']


class InitialWardrobeFactsTests(unittest.TestCase):
    def test_explicit_initial_items_keep_order_colours_and_later_undressing_separate(self):
        cards = [dict(name='岑月', clothing='月白中衣；靛青长裤；黑布靴', hair='黑发玉簪')]
        source = '岑月，28岁女性，初始衣着为' + '、'.join(INITIAL) + '。随后她脱下外袍裹住玉匣。'
        result = character_facts.lock_initial_clothing(cards, source, '岑月站在木柜前。随后她脱下外袍。')
        self.assertEqual(result[0]['clothing'].split('；'), INITIAL)
        self.assertEqual(result[0]['hair'], '黑发玉簪')
        self.assertEqual(cards[0]['clothing'], '月白中衣；靛青长裤；黑布靴')

    def test_bare_character_definition_preserves_cloak_without_initial_label(self):
        source = '顾霄，45岁男性，深绿斗篷，是驾舟的渡舟使。'
        result = character_facts.lock_initial_clothing(
            [dict(name='顾霄', clothing='绿色上衣；深棕护衣；黑长裤')], source, '顾霄站在船头。')
        self.assertTrue(result[0]['clothing'].startswith('深绿斗篷'))
        self.assertEqual(result[0]['clothing'].count('深绿斗篷'), 1)

    def test_bare_age_definition_also_recognizes_outer_and_middle_robe_terms(self):
        source = '沈青，26岁男性，玄青外袍、月白中衣、靛青长裤、黑布靴。'
        result = character_facts.lock_initial_clothing([dict(name='沈青', clothing='白色中衣')], source)
        self.assertEqual(result[0]['clothing'].split('；'), ['玄青外袍', '月白中衣', '靛青长裤', '黑布靴'])

    def test_first_appearance_wear_is_used_when_user_has_no_outfit_definition(self):
        prose = '沈青身穿玄青外袍、月白中衣，站在门旁。随后他脱下玄青外袍。'
        result = character_facts.lock_initial_clothing([dict(name='沈青', clothing='月白中衣；黑长裤')],
                                                       '沈青走进大厅。', prose)
        self.assertIn('玄青外袍', result[0]['clothing'])
        self.assertIn('月白中衣', result[0]['clothing'])
        self.assertIn('黑长裤', result[0]['clothing'])

    def test_later_damage_does_not_rewrite_opening_clothing(self):
        result = character_facts.lock_initial_clothing([dict(name='林岚', clothing='残破的蓝色外套；黑长裤')],
            '林岚，34岁女性，初始衣着为蓝色外套、黑长裤。',
            '林岚站在门旁。随后她跌进雨水，衣服已经湿透，衣袖被撕破。')
        self.assertEqual(result[0]['clothing'], '蓝色外套；黑长裤')
        self.assertNotRegex(result[0]['clothing'], r'湿透|撕破|残破')

    def test_explicit_opening_damage_and_wet_clothes_stay_visible(self):
        result = character_facts.lock_initial_clothing([dict(name='林岚', clothing='完好的蓝色外套；干爽的黑长裤')],
            '林岚，34岁女性，初始衣着为蓝色外套、黑长裤。',
            '林岚站在门旁，衣服已经湿透，衣袖已被撕破。')
        self.assertIn('衣服已经湿透', result[0]['clothing'])
        self.assertIn('衣袖已被撕破', result[0]['clothing'])
        self.assertNotRegex(result[0]['clothing'], r'完好|干爽')

    def test_unknown_initial_clothes_leave_model_design_unchanged(self):
        cards = [dict(name='沈青', clothing='灰色道袍', voice='成年男声')]
        result = character_facts.lock_initial_clothing(cards, '沈青寻找玉匣。', '沈青推开木门。')
        self.assertEqual(result, cards)
        self.assertNotIn('_initial_clothing_locked', result[0])


@unittest.skipUnless(STORY.is_file(), 'retained real source project unavailable')
class RealStory071InitialWardrobeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.story = json.loads(STORY.read_text(encoding='utf-8'))
        cls.prose = cls.story['saga']['episodes'][0]['prose']
        folder = STORY.parents[1] / 'assets' / 'STORY_071' / 'characters'
        cls.designed = [json.loads(path.read_text(encoding='utf-8')) for path in folder.glob('*.json')]
        cls.designed = [card for card in cls.designed if card.get('name') in {'岑月', '陆衡', '顾霄'}]

    def design(self):
        payloads = []

        def q(system, user, **options):
            payloads.append(user)
            return json.dumps(self.designed, ensure_ascii=False)

        with patch.object(authoring, '_ins', return_value='CPU character designer'), \
             patch.object(authoring, '_fill', side_effect=lambda text, *a, **k: text), \
             patch.object(one_shot_setup, '_enum_rules', return_value=''), \
             patch.object(authoring, '_q', side_effect=q):
            result = authoring.design_characters(self.prose, self.story['settings'])
        self.assertEqual(len(payloads), 1)
        self.assertIn('首次出场', payloads[0])
        return result

    def test_real_bad_model_design_restores_all_four_initial_items_and_cloak(self):
        before = copy.deepcopy(self.designed)
        result = self.design()
        cen = next(card for card in result if card['name'] == '岑月')
        gu = next(card for card in result if card['name'] == '顾霄')
        self.assertEqual(cen['clothing'].split('；'), INITIAL)
        self.assertTrue(gu['clothing'].startswith('深绿斗篷'))
        self.assertEqual(self.designed, before)
        initial = wardrobe.build_start_plan([dict(text='岑月走到木柜旁。')], 0, result)
        self.assertIn('玄青外袍', initial['characters'][cen['character_id']]['worn'])

    def test_real_initial_lock_survives_card_creation_without_persisting_private_marker(self):
        designed = self.design()
        created = {}

        def create(sid, fields):
            card = dict(fields, character_id='CPU_' + fields['name'])
            created[card['character_id']] = copy.deepcopy(card)
            return card

        with patch.object(story_core, 'get_story', return_value=copy.deepcopy(self.story)), \
             patch.object(asset_core, 'create_character', side_effect=create), \
             patch.object(asset_core, 'get_asset', side_effect=lambda sid, kind, aid: copy.deepcopy(created.get(aid))), \
             patch.object(authoring, 'normalize_cards_for_world'), patch.object(authoring, '_dbg'), \
             patch.object(authoring, '_q', side_effect=AssertionError('storage must not call a model')):
            result = authoring._create_cards('CPU_ONLY', designed)
        cen = next(card for card in result if card['name'] == '岑月')
        gu = next(card for card in result if card['name'] == '顾霄')
        self.assertEqual(cen['clothing'].split('；'), INITIAL)
        self.assertTrue(gu['clothing'].startswith('深绿斗篷'))
        self.assertTrue(cen['clothing_from_brief'])
        self.assertFalse(any('_initial_clothing_locked' in card for card in result))


if __name__ == '__main__':
    unittest.main()
