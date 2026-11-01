"""Collective source identity/reference regressions; CPU and mocks only."""
import copy
import os
os.environ['V41_NO_MODEL'] = '1'
import unittest
from unittest.mock import Mock, patch

from cores import asset_core, authoring, episode_contract, group_entities as groups
from cores import oral_story, reference_ready, saga_core, story_core


PEOPLE = [{'name': '林海', 'character_id': 'C1', 'age': '30', 'sex': '男', 'visuals': ['OLD1']},
          {'name': '阿宁', 'character_id': 'C2', 'age': '28', 'sex': '女', 'visuals': ['OLD2']}]


class CollectiveIdentityTests(unittest.TestCase):
    def test_plural_voice_stays_collective_when_a_single_same_role_exists(self):
        single = {'name': '小陈', 'char_type': '工人', 'character_id': 'C0', 'sex': '男'}
        source = '三名工人站在门边。工人们齐声说：“辛苦了。”'
        detected = groups.detect_groups(source, [single])
        self.assertEqual(len(detected), 1)
        self.assertEqual(detected[0]['name'], '工人们')
        self.assertEqual(detected[0]['group_count'], 3)
        _, dialogue, report = episode_contract.prepare_source(source, [single, *detected])
        self.assertEqual(dialogue, [('工人们', '辛苦了。')])
        self.assertEqual(report['dialogue'][0]['entity_type'], 'group')
        self.assertEqual(report['dialogue'][0]['group_count'], 3)

    def test_unknown_count_remains_unknown(self):
        for source in ('工人们站在门边。', '几名维修员穿着蓝色外套，站在门边。', '游客们：到了。'):
            with self.subTest(source=source):
                self.assertIsNone(groups.detect_groups(source)[0]['group_count'])

    def test_current_script_collective_label_is_direct_evidence(self):
        for source in ('工人们：大家辛苦了。', '【D2】维修员们（齐声）：收到。'):
            with self.subTest(source=source):
                group = groups.detect_groups(source)[0]
                self.assertTrue(groups.is_group(group))
                _, dialogue, _ = episode_contract.prepare_source(source, [group])
                self.assertEqual(dialogue[0][0], group['name'])

    def test_numbered_role_label_and_narration_share_identity(self):
        source = '三名志愿者：收到。\n志愿者们点头。'
        found = groups.detect_groups(source)
        self.assertEqual([c['name'] for c in found], ['志愿者们'])
        self.assertEqual(found[0]['group_count'], 3)
        self.assertIn('三名志愿者', found[0]['aliases'])
        self.assertEqual(episode_contract.prepare_source(source, found)[1], [('志愿者们', '收到。')])

    def test_mentions_absence_plans_and_singular_do_not_add_groups(self):
        cases = ('阿宁说：“工人们都在外面。”', '阿宁想起工人们。', '阿宁等待工人们。',
                 '阿宁计划招募三名工人。', '工人们已经失踪。', '工人们尚未到场。',
                 '工人们的照片放在桌上。', '一名工人站在门边。', '他们：收到。',
                 '我们一起站在门边。')
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(groups.detect_groups(source, PEOPLE), [])

    def test_generic_collective_is_visible_but_never_borrows_whole_roster(self):
        for source in ('众人一起说：“开始吧。”', '林海想起阿宁，众人一起说：“开始吧。”',
                       '林海站在门边。阿宁尚未到场。众人说：“开始吧。”'):
            with self.subTest(source=source):
                group = groups.detect_groups(source, PEOPLE)[0]
                self.assertEqual(group['name'], '众人')
                self.assertEqual(group['group_members'], [])
                self.assertIsNone(group['group_count'])

    def test_generic_mentioned_or_negated_group_is_not_added(self):
        for source in ('林海说：“众人正在大厅。”', '众人没有回答。', '众人尚未到场。',
                       '林海等待众人。', '林海准备邀请众人。'):
            with self.subTest(source=source):
                self.assertEqual(groups.detect_groups(source, PEOPLE), [])

    def test_future_departed_and_later_people_are_not_current_collective_members(self):
        for source in ('林海站在门边。阿宁计划走进房间，众人齐声说：“开始吧。”',
                       '林海和阿宁站在门边。阿宁离开了，众人齐声说：“开始吧。”',
                       '林海站在门边。众人齐声说：“开始吧。”阿宁随后走进房间。'):
            with self.subTest(source=source):
                self.assertEqual(groups.detect_groups(source, PEOPLE)[0]['group_members'], [])

    def test_generic_members_follow_current_episode_without_editing_saved_group(self):
        zhou = {'name': '小周', 'character_id': 'C3'}
        old = {'name': '众人', 'character_id': 'G1', 'entity_type': 'group', 'group_count': 2,
               'group_members': ['林海', '阿宁'], 'visuals': ['OLD_GROUP'], 'frozen': True, 'version': 4}
        cards = [*PEOPLE, zhou, old]
        before = copy.deepcopy(cards)
        current = groups.scoped_cards(cards, '林海和小周站在门边，众人齐声说：“走吧。”')
        group = current[-1]
        self.assertEqual(group['group_members'], ['林海', '小周'])
        self.assertEqual(groups.reference_cards([group], current), [PEOPLE[0], zhou])
        self.assertEqual(cards, before)
        unknown = groups.scoped_cards(cards, '众人齐声说：“走吧。”')[-1]
        self.assertEqual(unknown['group_members'], [])
        self.assertIsNone(unknown['group_count'])

    def test_repeated_collective_labels_use_members_at_current_source_prefix(self):
        zhou = {'name': '小周', 'character_id': 'C3'}
        old = {'name': '众人', 'character_id': 'G1', 'entity_type': 'group', 'group_count': 2,
               'group_members': ['林海', '阿宁']}
        source = '林海和阿宁站在门边。\n众人：走吧。\n阿宁离开了。小周走进门。\n众人：走吧。'
        current = groups.scoped_cards([*PEOPLE, zhou, old], source)[-1]
        self.assertEqual(current['group_members'], ['林海', '小周'])
        _, dialogue, report = episode_contract.prepare_source(source, [*PEOPLE, zhou, old])
        self.assertEqual(dialogue, [('众人', '走吧。'), ('众人', '走吧。')])
        self.assertEqual([d['group_members'] for d in report['dialogue']], [['林海', '阿宁'], ['林海', '小周']])

    def test_explicit_plural_unknown_profession_has_source_count_and_voice(self):
        source = '三名魔法师站在门边。魔法师们齐声说：“开始吧。”'
        group = groups.detect_groups(source)[0]
        self.assertEqual(group['name'], '魔法师们')
        self.assertEqual(group['group_count'], 3)
        self.assertIn('三名魔法师', group['aliases'])
        self.assertEqual(episode_contract.presence_evidence('三名魔法师站在门边。', [group])['actors'], ['魔法师们'])
        self.assertEqual(episode_contract.prepare_source(source, [group])[1], [('魔法师们', '开始吧。')])

    def test_named_people_form_collective_without_duplicate_identity_reference(self):
        source = '林海和阿宁站在门边，众人一起说：“走吧。”'
        group = groups.detect_groups(source, PEOPLE)[0]
        self.assertEqual(group['group_members'], ['林海', '阿宁'])
        self.assertEqual(group['group_count'], 2)
        self.assertEqual(groups.reference_cards([group, PEOPLE[0]], [*PEOPLE, group]), PEOPLE)
        _, dialogue, report = episode_contract.prepare_source(source, [*PEOPLE, group])
        self.assertEqual(dialogue, [('众人', '走吧。')])
        self.assertEqual(report['dialogue'][0]['group_members'], ['林海', '阿宁'])

    def test_explicit_joined_named_speakers_keep_both_existing_identities(self):
        for source in ('林海和阿宁齐声说：“走吧。”', '林海和阿宁：走吧。'):
            with self.subTest(source=source):
                group = groups.detect_groups(source, PEOPLE)[0]
                self.assertEqual(group['group_members'], ['林海', '阿宁'])
                self.assertEqual(group['name'], '林海和阿宁')
                self.assertEqual(groups.reference_cards([group], [*PEOPLE, group]), PEOPLE)
                _, dialogue, _ = episode_contract.prepare_source(source, [*PEOPLE, group])
                self.assertEqual(dialogue, [('林海和阿宁', '走吧。')])

    def test_reversed_collective_label_uses_canonical_asset_reference_order(self):
        source = '阿宁和林海齐声说：“走吧。”'
        group = groups.detect_groups(source, PEOPLE)[0]
        self.assertEqual(group['group_members'], ['阿宁', '林海'])
        cards = [*PEOPLE, dict(group, character_id='GROUP')]
        reference_order = groups.reference_cards([cards[-1]], cards)
        self.assertEqual([c['name'] for c in reference_order], ['林海', '阿宁'])
        segment_names = [c['name'] for c in reference_order]
        image_pack_cards = [c for c in cards if c['name'] in segment_names]
        self.assertEqual([c['character_id'] for c in reference_order], [c['character_id'] for c in image_pack_cards])
        # Duplicate selected members and reversed selection cannot renumber the
        # references or turn source member ordering into a different identity.
        self.assertEqual(groups.reference_cards([cards[-1], PEOPLE[1], PEOPLE[0]], cards), PEOPLE)

    def test_no_derived_single_person_alias_for_group(self):
        group = {'name': '护士们', 'entity_type': 'group', 'group_count': 3,
                 'age': '28', 'sex': '女', 'char_type': '护士', 'aliases': ['三名护士', '护士']}
        aliases = oral_story.card_aliases([group])
        self.assertEqual(aliases['护士们'], '护士们')
        self.assertEqual(aliases['三名护士'], '护士们')
        for label in ('护士', '女主', '姑娘', '女子', '们'):
            self.assertNotIn(label, aliases)

    def test_legacy_plural_card_reuses_saved_reference_without_rewriting(self):
        legacy = {'name': '游客们', 'character_id': 'OLD', 'visuals': ['ADOPTED'],
                  'clothing': '蓝色外套', 'frozen': True, 'version': 4}
        before = copy.deepcopy(legacy)
        found = groups.detect_groups('游客们走进大厅。', [legacy])
        self.assertEqual(found[0]['name'], '游客们')
        self.assertTrue(groups.is_group(legacy))
        self.assertEqual(legacy, before)

    def test_group_presence_is_multiple_people_not_one_slot(self):
        group = {'name': '工人们', 'entity_type': 'group', 'group_count': 3, 'aliases': ['三名工人']}
        presence = episode_contract.presence_evidence('三名工人一起站在门边。', [group])
        self.assertEqual(presence['actors'], ['工人们'])
        self.assertEqual(presence['minimum_action_participants'], 3)
        self.assertEqual(presence['group_entities'][0]['count'], 3)
        unknown = dict(group, group_count=None)
        presence = episode_contract.presence_evidence('工人们一起站在门边。', [unknown])
        self.assertEqual(presence['minimum_action_participants'], 2)
        self.assertIsNone(presence['group_entities'][0]['count'])

    def test_member_collective_is_not_counted_twice(self):
        group = {'name': '众人', 'entity_type': 'group', 'group_members': ['林海', '阿宁'], 'group_count': 2}
        presence = episode_contract.presence_evidence('林海站在门边。众人一起站在门边。', [*PEOPLE, group])
        self.assertEqual(presence['minimum_action_participants'], 2)

    def test_partially_known_group_does_not_discard_anonymous_members_reference(self):
        group = {'name': '工人们', 'character_id': 'GROUP', 'entity_type': 'group',
                 'group_members': ['林海', '阿宁'], 'group_count': 5}
        self.assertEqual(groups.reference_cards([group], [*PEOPLE, group]), [group])
        presence = episode_contract.presence_evidence('工人们一起站在门边。', [*PEOPLE, group])
        self.assertEqual(presence['minimum_action_participants'], 5)


class CollectiveRegistrationTests(unittest.TestCase):
    def test_incremental_registration_reuses_group_and_preserves_all_old_cards(self):
        cards = copy.deepcopy(PEOPLE)
        before = copy.deepcopy(cards)
        create = Mock(side_effect=lambda sid, fields: dict(fields, character_id='NEW'))
        with patch.object(asset_core, 'list_assets', side_effect=lambda *a: copy.deepcopy(cards)), \
             patch.object(asset_core, 'create_character', create), \
             patch.object(asset_core, 'save_asset') as save:
            first = groups.ensure_group_cards('TEST', 2, source='三名工人站在门边。')
            cards.append(create.call_args.args[1] | {'character_id': 'NEW'})
            second = groups.ensure_group_cards('TEST', 3, source='工人们端起工具箱。')
        self.assertEqual(first['added'], ['工人们'])
        self.assertEqual(second['added'], [])
        self.assertEqual(second['reused'], ['工人们'])
        self.assertEqual(create.call_count, 1)
        self.assertEqual(cards[:2], before)
        self.assertEqual(cards[-1]['first_episode'], 2)
        save.assert_not_called()

    def test_saved_prose_primary_over_brief_and_old_picture(self):
        source = {'prose': '三名工人站在门边。', 'pictures': '游客们离开。', 'brief': '只有林海一个人'}
        with patch.object(saga_core, 'get_saga', return_value={'episodes': [dict(source, no=2)]}), \
             patch.object(asset_core, 'list_assets', return_value=[]), \
             patch.object(asset_core, 'create_character', side_effect=lambda sid, fields: dict(fields, character_id='NEW')):
            result = groups.ensure_group_cards('TEST', 2)
        self.assertEqual(result['added'], ['工人们'])

    def test_character_storage_preserves_group_contract_fields(self):
        fields = {'name': '工人们', 'entity_type': 'group', 'aliases': ['三名工人'], 'group_count': 3,
                  'group_members': [], 'group_appearance': '蓝色工作服', 'source_evidence': '三名工人站在门边。'}
        with patch.object(asset_core, 'get_asset', return_value=None), \
             patch.object(asset_core, 'save_asset') as save, patch.object(story_core, 'get_story', return_value=None):
            made = asset_core.create_character('TEST', fields)
        for key, value in fields.items():
            self.assertEqual(made[key], value)
        self.assertEqual(save.call_args.args[2], made)

    def test_member_collective_readiness_uses_each_existing_member_reference(self):
        group = {'name': '众人', 'character_id': 'GROUP', 'entity_type': 'group', 'group_members': ['林海', '阿宁']}
        cards = [*PEOPLE, group]
        with patch.object(asset_core, 'list_assets', side_effect=lambda sid, kind: cards if kind == 'characters' else []), \
             patch.object(reference_ready, 'adopted_paths', side_effect=lambda sid, kind=None: {'C1': 'a.png', 'C2': 'b.png'} if kind == 'character' else {}), \
             patch.object(story_core, 'get_story', return_value={'settings': {}}):
            reference_ready.assert_references('TEST', [{'chars': ['众人']}])
        with patch.object(asset_core, 'list_assets', side_effect=lambda sid, kind: cards if kind == 'characters' else []), \
             patch.object(reference_ready, 'adopted_paths', return_value={'C1': 'a.png'}), \
             patch.object(story_core, 'get_story', return_value={'settings': {}}):
            with self.assertRaisesRegex(reference_ready.MissingReferencesError, '阿宁'):
                reference_ready.assert_references('TEST', [{'chars': ['众人']}])

    def test_group_only_requires_its_own_group_reference(self):
        group = {'name': '工人们', 'character_id': 'GROUP', 'entity_type': 'group', 'group_count': 3}
        with patch.object(asset_core, 'list_assets', side_effect=lambda sid, kind: [group] if kind == 'characters' else []), \
             patch.object(reference_ready, 'adopted_paths', return_value={'GROUP': 'group.png'}), \
             patch.object(story_core, 'get_story', return_value={'settings': {}}):
            reference_ready.assert_references('TEST', [{'chars': ['工人们']}])


class CollectiveImageTests(unittest.TestCase):
    def test_group_sheet_is_shared_frame_not_single_person_four_views(self):
        group = {'name': '工人们', 'entity_type': 'group', 'group_count': 3, 'group_appearance': '蓝色工作服'}
        from cores import prompt_writer
        with patch.object(asset_core, 'get_asset', return_value=None), \
             patch.object(prompt_writer, 'write_character', side_effect=AssertionError('individual template forbidden')):
            prompt = authoring.write_char_sheet('TEST', group, {'style': '电影级写实'})
        self.assertIn('3名', prompt)
        self.assertIn('蓝色工作服', prompt)
        self.assertIn('自然构图', prompt)
        self.assertIn('所有成员', prompt)
        self.assertIn('单幅连续的自然群像', prompt)
        self.assertNotRegex(prompt, r'不使用|不增加|不显示|禁止|不得|不要')

    def test_unknown_count_sheet_has_no_invented_exact_count(self):
        prompt = groups.group_sheet_prompt({'name': '游客们', 'entity_type': 'group'})
        self.assertIn('人数呈现为自然群像', prompt)
        self.assertNotRegex(prompt, r'\d+名')

    def test_individual_trait_design_never_rewrites_group(self):
        group = {'name': '游客们', 'character_id': 'GROUP', 'entity_type': 'group', 'age': '', 'hair': '', 'voice': ''}
        before = copy.deepcopy(group)
        with patch.object(asset_core, 'list_assets', return_value=[group]), \
             patch.object(asset_core, 'get_asset', return_value=group), \
             patch.object(asset_core, 'save_asset') as save, \
             patch.object(authoring, '_q', side_effect=AssertionError('individual model forbidden')):
            self.assertEqual(authoring.design_faces('TEST')['ok'], 0)
            self.assertEqual(authoring.design_hair('TEST')['ok'], 0)
            result = authoring.complete_cards('TEST', [group], '游客们站在门边。', {})
        self.assertEqual(group, before)
        save.assert_not_called()
        self.assertFalse(result.get('failed'))

    def test_lock_line_keeps_group_plural_and_individual_unique(self):
        prompt = authoring.chars_lock_line([PEOPLE[0], {'name': '工人们', 'entity_type': 'group', 'group_count': 3}])
        self.assertIn('3名成员', prompt)
        self.assertIn('每名个人只有一位', prompt)
        self.assertNotIn('每个角色只出现一个', prompt)


if __name__ == '__main__':
    unittest.main()
