"""CPU regressions for local collectives and action-attached source speech."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

os.environ['V41_NO_MODEL'] = '1'
from cores import asset_core, director_flow as director, episode_contract as contract, group_entities as groups

PEOPLE = [dict(name='林岚', character_id='LIN', age=34, sex='女'),
          dict(name='周远', character_id='ZHOU', age=38, sex='男'),
          dict(name='陈川', character_id='CHEN', age=45, sex='男')]
ARCHIVE = Path('D:/Claude code/full_main_text_acceptance_20261031/text_stage.json')


class CollectiveContinuityTests(unittest.TestCase):
    def test_resolved_three_people_use_existing_cards_not_a_new_group(self):
        source = '林岚、周远和陈川站在门边。三人快步穿过长廊。'
        create = Mock(side_effect=AssertionError('ordinary pronouns reuse known identities'))
        with patch.object(asset_core, 'list_assets', return_value=PEOPLE), patch.object(asset_core, 'create_character', create):
            result = groups.ensure_group_cards('CPU', source=source)
        self.assertEqual(result['added'], [])
        self.assertEqual(groups.collective_actions(source, PEOPLE)[0]['members'], ['林岚', '周远', '陈川'])

    def test_departure_and_numeric_pair_keep_two_actual_members(self):
        source = '林岚、周远和陈川站在门边。陈川开车驶离装卸台。两人转身走回馆内。'
        occurrence = groups.collective_actions(source, PEOPLE)[0]
        self.assertEqual(occurrence['count'], 2)
        self.assertEqual(occurrence['members'], ['林岚', '周远'])
        descriptor = next(row for row in groups.detect_groups(source, PEOPLE) if row['name'] == '两人')
        self.assertEqual((descriptor['group_count'], descriptor['group_members']), (2, ['林岚', '周远']))

    def test_they_after_explicit_departure_do_not_restore_departed_actor(self):
        context = '林岚、周远和陈川站在门边。陈川御剑离开。'
        occurrence = groups.collective_actions('他们一起走进大厅。', PEOPLE, context=context)[0]
        self.assertEqual(occurrence['members'], ['林岚', '周远'])

    def test_roster_and_mentions_do_not_supply_missing_third_member(self):
        source = '林岚和周远站在门边，林岚想起陈川。三人走过长廊。'
        self.assertEqual(groups.collective_actions(source, PEOPLE)[0]['members'], [])
        self.assertEqual(groups.collective_actions('三人走过长廊。', PEOPLE)[0]['members'], [])

    def test_earlier_pair_does_not_override_a_later_arrival(self):
        source = '林岚和周远站在门边。陈川走进大厅。两人转身走回门边。'
        self.assertEqual(groups.collective_actions(source, PEOPLE)[0]['members'], [])

    def test_local_collective_members_are_added_to_action_presence(self):
        evidence = contract.presence_evidence('三人快步穿过长廊。', PEOPLE,
                                             context='林岚、周远和陈川站在门边。')
        self.assertEqual(evidence['actors'], ['林岚', '周远', '陈川'])
        self.assertEqual(evidence['minimum_action_participants'], 3)

    def test_earlier_collective_action_cannot_erase_a_later_explicit_departure(self):
        evidence = contract.presence_evidence('三人走过长廊。陈川离开了。两人走进大厅。', PEOPLE,
                                             context='林岚、周远和陈川站在门边。')
        self.assertIn('陈川', evidence['absent'])
        occurrences = groups.collective_actions('三人走过长廊，随后两人转身。', PEOPLE,
                                               context='林岚、周远和陈川站在门边。')
        self.assertEqual([item['label'] for item in occurrences], ['三人', '两人'])

    def test_unnamed_crowd_stays_a_real_group(self):
        descriptor = groups.detect_groups('三名弟子走进大厅。弟子们齐声说：“明白。”', PEOPLE)[0]
        self.assertEqual(descriptor['name'], '弟子们')
        self.assertEqual(descriptor['group_count'], 3)
        self.assertEqual(descriptor['group_members'], [])

    def test_group_image_prompt_contains_positive_visible_content(self):
        for count in (3, None):
            prompt = groups.group_sheet_prompt(dict(name='弟子们', entity_type='group', group_count=count,
                                                    clothing='青色道袍'), {'style': '写实仙侠'})
            self.assertIn('单幅连续', prompt)
            self.assertIn('青色道袍', prompt)
            self.assertNotRegex(prompt, r'不使用|不增加|不显示|禁止|不得|不要|避免|排除|不固定|未规定')


class AttachedSpeechTests(unittest.TestCase):
    def test_explicit_actor_action_then_colon_preserves_action_and_exact_speech(self):
        source = '陈川接过，指尖按了按厚实的布料，确认形状稳固，点头：收到，辛苦。'
        script, says, report = contract.prepare_source(source, PEOPLE)
        self.assertEqual(says, [('陈川', '收到，辛苦。')])
        self.assertIn(source.split('：')[0] + '：', script)
        self.assertEqual(script.count('收到，辛苦。'), 1)
        self.assertEqual(report['source_original'], source)
        self.assertFalse(report['issues'])

    def test_repeated_inline_speech_keeps_order_and_distinct_ids(self):
        source = '陈川接过，点头：好。\n陈川停下，开口：好。\n林岚：走。'
        _, says, report = contract.prepare_source(source, PEOPLE)
        self.assertEqual(says, [('陈川', '好。'), ('陈川', '好。'), ('林岚', '走。')])
        self.assertEqual([entry['id'] for entry in report['dialogue']], [1, 2, 3])

    def test_multiple_possible_actors_are_reported_instead_of_guessed(self):
        source = '陈川和周远对视，点头：收到。'
        _, says, report = contract.prepare_source(source, PEOPLE)
        self.assertEqual(says, [('待核对说话人', '收到。')])
        self.assertTrue(any(entry['code'] == 'speaker_unresolved' for entry in report['issues']))

    def test_thought_sign_and_planned_speech_are_not_new_spoken_lines(self):
        for source in ('林岚心想：不能弄湿。', '林岚看着门上写着：禁止入内。',
                       '林岚准备开口：我们走。'):
            with self.subTest(source=source):
                self.assertEqual(contract.prepare_source(source, PEOPLE)[1], [])


@unittest.skipUnless(ARCHIVE.is_file(), 'retained real archive acceptance fixture unavailable')
class RealArchiveRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(ARCHIVE.read_text(encoding='utf-8'))
        cls.cards = [card for card in cls.fixture['characters'] if card.get('name') in {'林岚', '周远', '陈川'}]

    def test_real_old_tail_pair_has_two_members_and_no_new_group_card(self):
        source = '\n'.join(item['text'] for item in self.fixture['slices'])
        pair = next(row for row in groups.detect_groups(source, self.cards) if row['name'] == '两人')
        self.assertEqual(pair['group_count'], 2)
        self.assertEqual(pair['group_members'], ['林岚', '周远'])
        with patch.object(asset_core, 'list_assets', return_value=self.cards), \
             patch.object(asset_core, 'create_character', side_effect=AssertionError('no synthetic ordinary group')):
            self.assertEqual(groups.ensure_group_cards('CPU', source=source)['added'], [])

    def test_real_segment_three_restores_zhou_yuan_from_collective_source(self):
        segment = copy.deepcopy(self.fixture['slices'][2])
        segment['director']['cast'] = ['林岚', '陈川']
        prior = '\n'.join(item['text'] for item in self.fixture['slices'][:2])
        director._clip(segment, 3, self.cards, {}, prior, {}, '')
        self.assertIn('周远', segment['director']['cast'])
        self.assertEqual(set(segment['director']['presence']['actors']), {'林岚', '周远', '陈川'})

    def test_real_inline_line_becomes_spoken_without_rewriting_source(self):
        source = self.fixture['slices'][3]['text']
        _, says, report = contract.prepare_source(source, self.cards)
        self.assertEqual(says, [('林岚', '给，胶片在这。'), ('陈川', '收到，辛苦。'), ('周远', '钥匙交完了？')])
        self.assertEqual(report['source_original'], source)

    def test_legacy_source_version_repairs_inline_line_without_a_model(self):
        segment = copy.deepcopy(self.fixture['slices'][3])
        segment['director']['source_contract_version'] = 'episode-source-v1'
        q = Mock(side_effect=AssertionError('clear speaker requires no model'))
        director.repair_slice_source(segment, self.cards, q)
        self.assertEqual([line[1] for line in segment['director']['says']],
                         ['给，胶片在这。', '收到，辛苦。', '钥匙交完了？'])
        q.assert_not_called()

    def test_released_writer_upgrades_real_v1_slice_before_voice_slots_and_final_validation(self):
        from cores import workbench_story_director as writer
        from tests import test_source_shot_compiler as source_fixture
        slices = copy.deepcopy(self.fixture['slices'])
        before = copy.deepcopy(slices)
        places = [card['name'] for card in self.fixture['scenes']]
        settings = dict(style='写实摄影', _director_reference_paths={
            name: __file__ for name in places + [card['name'] for card in self.cards]})
        requests = []

        def q(system, user, **options):
            payload = json.loads(user)
            self.assertIn('source_units', payload)
            self.assertNotIn('field_values', payload)
            requests.append(copy.deepcopy(payload))
            reply = source_fixture.model_reply(payload)
            reply['soundscape'] = '现场轻微的环境底噪。'
            for shot in reply['shots']:
                shot['acting'] = []
                shot['visual']['lighting'] = '柔和环境光照亮人物与地面。'
            return json.dumps(reply, ensure_ascii=False)

        rows = writer.make_prompts(slices, copy.deepcopy(self.cards), places, q,
                                  settings=settings, start=3, count=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(requests), 1)
        expected = [('林岚', '给，胶片在这。'), ('陈川', '收到，辛苦。'), ('周远', '钥匙交完了？')]
        self.assertEqual([(unit['speaker'], unit['text']) for unit in requests[0]['source_units']
                          if unit['kind'] == 'dialogue'], expected)
        self.assertFalse(rows[0]['source_fallback'])
        self.assertEqual(rows[0]['director_contract']['source_contract_version'], contract.VERSION)
        self.assertEqual([tuple(line) for line in rows[0]['director_contract']['says']], expected)
        self.assertEqual(director.prompt_problems(rows[0]['prompt'], before[3]['text'],
                                                rows[0]['seconds'], rows[0]['scene']), [])
        self.assertEqual(rows[0]['prompt'].count('<d>[Chinese] 收到，辛苦。</d>'), 1)
        binding = rows[0]['director_compilation']['voice_binding_audit'][1]
        self.assertEqual(binding['character_id'], next(card['character_id'] for card in self.cards
                                                     if card['name'] == '陈川'))
        self.assertEqual(rows[0]['source_text'], before[3]['text'])
        self.assertEqual(slices[3]['director']['blocks'], before[3]['director']['blocks'])
        self.assertEqual(slices[3]['director']['says'], before[3]['director']['says'])


if __name__ == '__main__':
    unittest.main()
