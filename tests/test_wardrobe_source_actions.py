"""Authored clothing actions must match actor, garment and affirmative action."""
import copy
import unittest

from cores import wardrobe_continuity as wardrobe


class WardrobeSourceActionTests(unittest.TestCase):
    def setUp(self):
        self.people = [dict(character_id='hero', name='艾尔', clothing='灰色外套、白衬衫、黑长裤'),
                       dict(character_id='doctor', name='医生')]
        self.initial = wardrobe.initial_state(self.people)

    def change(self, source, **changes):
        event = dict(character_id='hero', action='remove', slot='outerwear', item='灰色外套',
            before='原衣物穿在身上', after='原衣物已脱下', evidence='前后实际帧明确可见变化',
            before_frame=0, after_frame=24, certainty='observed')
        event.update(changes)
        return wardrobe.advance(self.initial, characters=self.people, segment=1,
            sequence=dict(wardrobe_evidence_scope='transition', wardrobe_events=[event]),
            evidence_frames=[0, 24], source_events=source)

    def test_negated_and_intended_actions_cannot_confirm_removal(self):
        for text in ('艾尔没有脱下灰色外套。', '艾尔并未脱下灰色外套。',
                     '艾尔准备脱下灰色外套，但还没动手。', '艾尔想要脱下灰色外套。',
                     '艾尔试图脱下灰色外套。', '如果艾尔脱下灰色外套，她就会觉得冷。',
                     '艾尔脱下灰色外套的念头一闪而过。'):
            with self.subTest(text=text):
                self.assertEqual(self.change(text), self.initial)

    def test_another_garment_or_slot_cannot_authorize_change(self):
        for text in ('艾尔摘下帽子，放在桌上。', '艾尔脱下黑长裤。', '艾尔脱下鞋子。'):
            with self.subTest(text=text):
                self.assertEqual(self.change(text), self.initial)
        self.assertEqual(self.change('艾尔脱下灰色外套。', slot='bottom', item='黑长裤'), self.initial)

    def test_accessory_changes_refer_to_the_same_item(self):
        self.assertEqual(self.change('艾尔摘下围巾。', slot='accessory', item='黑色帽子'), self.initial)
        actual = self.change('艾尔摘下黑色帽子。', slot='accessory', item='黑色帽子')
        self.assertEqual(actual['characters']['hero']['slots']['accessory']['status'], 'removed')

    def test_positive_action_keeps_modifiers_and_shared_actor_clause(self):
        for text in ('艾尔缓缓脱下灰色外套。', '艾尔停住，脱下灰色外套。',
                     '艾尔没有再犹豫，脱下灰色外套。', '艾尔不得不脱下灰色外套。',
                     '艾尔毫不犹豫地脱下灰色外套。', '艾尔二话不说地脱下灰色外套。',
                     '艾尔准备脱下外套，随后她真的脱下了外套。'):
            with self.subTest(text=text):
                actual = self.change(text)
                self.assertEqual(actual['characters']['hero']['slots']['outerwear']['status'], 'removed')

    def test_other_actor_and_quoted_action_stay_unconfirmed(self):
        for text in ('医生脱下灰色外套，艾尔抬头。', '艾尔说：“我脱下灰色外套了吗？”',
                     '艾尔说：“我脱下灰色外套。”',
                     '艾尔看着医生。脱下灰色外套。'):
            with self.subTest(text=text):
                self.assertEqual(self.change(text), self.initial)

    def test_damage_can_name_the_garment_before_the_verb(self):
        actual = self.change('艾尔的白衬衫袖口被撕破。', action='damage', slot='top', item='白衬衫')
        self.assertEqual(actual['characters']['hero']['slots']['top']['status'], 'damaged')
        self.assertEqual(self.change('艾尔的外套袖口被撕破。', action='damage', slot='top', item='白衬衫'), self.initial)

    def test_complete_outfit_change_and_explicit_component_change(self):
        actual = self.change('艾尔换上蓝色工作连体服。', action='replace', slot='outfit', item='蓝色工作连体服')
        self.assertTrue(actual['characters']['hero']['outfit_replaced'])
        self.assertEqual(self.change('艾尔换上蓝色外套。', action='replace', slot='outfit', item='蓝色工作连体服'), self.initial)
        actual = self.change('艾尔更衣。', action='replace', slot='outfit', item='蓝色工作连体服')
        self.assertTrue(actual['characters']['hero']['outfit_replaced'])

    def test_input_is_not_mutated(self):
        before = copy.deepcopy((self.initial, self.people))
        self.change('艾尔脱下灰色外套。')
        self.assertEqual((self.initial, self.people), before)


if __name__ == '__main__':
    unittest.main()
