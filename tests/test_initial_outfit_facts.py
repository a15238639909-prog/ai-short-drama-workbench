"""Initial wardrobe facts must not borrow later clothing actions. CPU only."""
import copy
import os
os.environ['V41_NO_MODEL'] = '1'
import unittest
from unittest.mock import patch

from cores import oral_story, authoring, asset_core, story_core


class InitialOutfitFactsTests(unittest.TestCase):
    def facts(self, text):
        return oral_story.outfit_hints(text, ['阿宁', '林海'],
                                      sexes={'阿宁': '女', '林海': '男'})

    def test_later_change_does_not_join_initial_card(self):
        source = '阿宁穿蓝色衬衫和灰色长裤。她走进更衣室换上红色连衣裙。'
        hints = self.facts(source)['阿宁']
        clothing, _ = oral_story.fixed_outfit_description(hints, '红色连衣裙和白色靴子')
        self.assertEqual(clothing, '蓝色衬衫；灰色长裤')
        self.assertEqual(source, '阿宁穿蓝色衬衫和灰色长裤。她走进更衣室换上红色连衣裙。')

    def test_removal_and_later_visible_clothes_are_not_initial(self):
        hints = self.facts('阿宁穿蓝色外套和灰色长裤。她随后脱下蓝色外套，露出白色衬衫。她穿红色靴子。')['阿宁']
        self.assertEqual(hints, ['穿蓝色外套和灰色长裤'])

    def test_same_sentence_change_is_not_copied_into_initial_hint(self):
        hints = self.facts('阿宁穿蓝色衬衫和灰色长裤，然后换上红色连衣裙。')['阿宁']
        clothing, _ = oral_story.fixed_outfit_description(hints)
        self.assertEqual(clothing, '蓝色衬衫；灰色长裤')

    def test_one_person_changing_does_not_hide_another_person_first_outfit(self):
        hints = self.facts('阿宁穿蓝色外套，随后脱下蓝色外套。林海穿灰色衬衫和黑色长裤。')
        self.assertIn('蓝色外套', str(hints['阿宁']))
        self.assertNotIn('脱', str(hints['阿宁']))
        self.assertEqual(hints['林海'], ['穿灰色衬衫和黑色长裤'])

    def test_question_plan_negation_and_quoted_claims_are_not_worn_facts(self):
        for source in ('林海是否要穿红色外套？', '林海打算穿红色外套。',
                       '林海没穿红色外套。', '林海不穿红色外套。',
                       '林海穿红色外套吗？', '林海说：“他穿红色外套。”'):
            with self.subTest(source=source):
                self.assertNotIn('林海', self.facts(source))

    def test_planned_change_does_not_block_initial_worn_fact(self):
        hints = self.facts('阿宁打算换上红色连衣裙。她穿蓝色衬衫和灰色长裤。')['阿宁']
        self.assertEqual(hints, ['穿蓝色衬衫和灰色长裤'])

    def test_explicit_wardrobe_definition_needs_no_wear_verb(self):
        hints = self.facts('阿宁的服装是蓝色衬衫和灰色长裤。林海开场衣着为棕色夹克和黑色长裤。')
        self.assertEqual(hints['阿宁'], ['蓝色衬衫和灰色长裤'])
        self.assertEqual(hints['林海'], ['棕色夹克和黑色长裤'])

    def test_parenthesized_definition_before_changes_stays_authoritative(self):
        hints = self.facts('阿宁（28岁女性，蓝色衬衫、灰色长裤）。她随后换上红色连衣裙。')['阿宁']
        self.assertEqual(hints, ['蓝色衬衫、灰色长裤'])

    def test_create_cards_keeps_user_initial_facts_and_original_later_action(self):
        initial = '阿宁的初始服装是蓝色衬衫和灰色长裤。'
        prose = '阿宁穿蓝色衬衫和灰色长裤。她走进更衣室换上红色连衣裙。'
        story = {'settings': {'one_line': initial, 'initial_story_input': initial},
                 'saga': {'episodes': [{'no': 1, 'brief': '阿宁脱下外套。', 'prose': prose}]}}
        before = copy.deepcopy(story)
        made = {}
        def create(sid, fields):
            made.update(fields, character_id='TEST_NEW')
            return copy.deepcopy(made)
        source = {'name': '阿宁', 'age': '28', 'sex': '女', 'clothing': '红色连衣裙', 'first_episode': 1}
        with patch.object(story_core, 'get_story', return_value=story), \
             patch.object(asset_core, 'create_character', side_effect=create), \
             patch.object(asset_core, 'get_asset', side_effect=lambda *a: copy.deepcopy(made)), \
             patch.object(authoring, 'normalize_cards_for_world'), patch.object(authoring, '_dbg'), \
             patch.object(authoring, '_q', side_effect=AssertionError('model forbidden')):
            authoring._create_cards('TEST', [source])
        self.assertEqual(made['clothing'], '蓝色衬衫；灰色长裤')
        self.assertTrue(made['clothing_from_brief'])
        self.assertEqual(story, before)


if __name__ == '__main__':
    unittest.main()
