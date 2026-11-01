"""CPU-only terminal pair resolution, including the real unedited source."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

os.environ['V41_NO_MODEL'] = '1'
from cores import asset_core, group_entities as groups


PEOPLE = [dict(name='阿岚', character_id='A'), dict(name='陆宁', character_id='B'),
          dict(name='顾亭', character_id='C'), dict(name='沈墨', character_id='D')]
REAL_STORY = Path('D:/AI视频工作台/data/stories/STORY_071.json')
REAL_CARDS = REAL_STORY.parent.parent / 'assets/STORY_071/characters'


class TerminalCollectiveTests(unittest.TestCase):
    def no_new_card(self, source, cards):
        before = copy.deepcopy(cards)
        create = Mock(side_effect=AssertionError('a named pair reuses its own individual cards'))
        with patch.object(asset_core, 'list_assets', return_value=cards), patch.object(asset_core, 'create_character', create):
            result = groups.ensure_group_cards('CPU_TERMINAL', source=source)
        create.assert_not_called()
        self.assertEqual(cards, before)
        return result

    def test_vehicle_subject_departure_then_explicit_pair_gaze_is_not_new_people(self):
        source = ('阿岚、陆宁与顾亭站在渡口。顾亭登舟启动机关。'
                  '阿岚与陆宁立于渡台边缘。船身亮起，仙舟驶入云海。'
                  '阿岚与陆宁对视一眼。陆宁抬手擦去雨水。阿岚松开指尖。'
                  '陆宁：走吧。\n两人转身，沿来路走回长廊。')
        descriptor = next(row for row in groups.detect_groups(source, PEOPLE) if row['name'] == '两人')
        self.assertEqual(descriptor['group_members'], ['阿岚', '陆宁'])
        self.assertEqual(groups.collective_actions(source, PEOPLE)[-1]['members'], ['阿岚', '陆宁'])
        self.assertEqual(self.no_new_card(source, PEOPLE)['added'], [])

    def test_latest_pair_does_not_need_a_vehicle_or_a_short_context_window(self):
        source = '阿岚、陆宁与顾亭站在大厅。顾亭登车。' + '雨水落在窗外的青石路上。' * 80
        source += '车灯消失在远处。阿岚与陆宁相视一笑。两人转身走回门内。'
        self.assertEqual(groups.collective_actions(source, PEOPLE)[-1]['members'], ['阿岚', '陆宁'])

    def test_later_named_arrival_keeps_unresolved_pair_unresolved(self):
        source = '阿岚与陆宁对视一眼。顾亭走进大厅。两人转身。'
        occurrence = groups.collective_actions(source, PEOPLE)[-1]
        self.assertEqual(occurrence['members'], [])

    def test_anonymous_pair_elsewhere_does_not_borrow_prior_named_pair(self):
        for source in (
            '阿岚与陆宁对视一眼。与此同时，另一处的两名守卫站在门边。两人转身。',
            '阿岚与陆宁站在院内。另一边，两名工人举起木架。两人放下木架。',
        ):
            with self.subTest(source=source):
                occurrence = groups.collective_actions(source, PEOPLE)[-1]
                self.assertEqual(occurrence['members'], [])
                pair = next(row for row in groups.detect_groups(source, PEOPLE) if row['name'] == '两人')
                self.assertEqual(pair['group_members'], [])

    def test_true_anonymous_two_are_still_registered(self):
        source = '两名守卫站在西门。两人转身走进门内。'
        create = Mock(side_effect=lambda sid, fields: dict(fields, character_id='G_' + fields['name']))
        with patch.object(asset_core, 'list_assets', return_value=copy.deepcopy(PEOPLE)), patch.object(asset_core, 'create_character', create):
            result = groups.ensure_group_cards('CPU_TERMINAL', source=source)
        self.assertIn('守卫们', result['added'])
        self.assertTrue(create.called)
        self.assertEqual(next(row for row in result['groups'] if row['name'] == '两人')['group_members'], [])

    def test_two_named_pairs_in_different_places_use_latest_explicit_pair(self):
        source = '阿岚与陆宁站在东门。与此同时，顾亭与沈墨站在西门。两人转身。'
        self.assertEqual(groups.collective_actions(source, PEOPLE)[-1]['members'], ['顾亭', '沈墨'])
        source = '阿岚与陆宁对视。两名守卫站在西门。阿岚与陆宁相视一笑。两人转身。'
        self.assertEqual(groups.collective_actions(source, PEOPLE)[-1]['members'], ['阿岚', '陆宁'])

    def test_mentioned_future_or_quoted_strangers_do_not_break_known_pair(self):
        for middle in ('阿岚想起两名守卫。', '阿岚说：“两名守卫站在西门。”',
                       '两名守卫准备走进西门。', '两名守卫尚未到场。'):
            with self.subTest(middle=middle):
                source = '阿岚与陆宁对视一眼。' + middle + '\n两人转身。'
                self.assertEqual(groups.collective_actions(source, PEOPLE)[-1]['members'], ['阿岚', '陆宁'])

    @unittest.skipUnless(REAL_STORY.is_file() and REAL_CARDS.is_dir(), 'real local acceptance source is optional')
    def test_real_production_source_does_not_create_terminal_two_card_or_mutate_old_card(self):
        story = json.loads(REAL_STORY.read_text(encoding='utf-8'))
        episode = next(row for row in story['saga']['episodes'] if row['no'] == 1)
        cards = [json.loads(path.read_text(encoding='utf-8')) for path in REAL_CARDS.glob('*.json')]
        before = copy.deepcopy(cards)
        pair = next(row for row in groups.detect_groups(episode['prose'], cards) if row['name'] == '两人')
        self.assertEqual(pair['group_members'], ['岑月', '陆衡'])
        self.assertEqual(pair['group_count'], 2)
        result = self.no_new_card(episode['prose'], cards)
        self.assertEqual(result['added'], [])
        scoped = groups.scoped_cards(cards, episode['prose'])
        pair_card = next(card for card in scoped if card.get('name') == '两人')
        self.assertEqual([card['name'] for card in groups.reference_cards([pair_card], scoped)], ['岑月', '陆衡'])
        self.assertEqual(cards, before)


if __name__ == '__main__':
    unittest.main()
