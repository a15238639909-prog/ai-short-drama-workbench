"""Original-story and item-transfer regressions; retained fixtures, mocked models."""
import copy
import json
import unittest

from cores import long_comic_core as comic, long_comic_continuity as continuity
from tests import test_long_comic_character_reuse as fixture


STORY = '''严格3页，每页2格。动作和以下对白原样保留。
第一页第一格：阿青和阿白查看木桌。第二格：阿青对阿白说“钥匙找到了。”阿白说“该出发了。”
第二页第一格：阿青和阿白走向门口。第二格：阿白说“交给我。”
第三页第一格：阿青和阿白走到门前。第二格：阿青说“灯亮了！”'''


def supplied_chapter():
    pages = []
    for number in range(1, 4):
        rows = [fixture.panel(index, ['c01', 'c02'], '阿青和阿白看向门外。') for index in (1, 2)]
        pages.append(dict(number=number, summary='两位修复师查看现场。', scene='修复室门边', panels=rows))
    pages[0]['panels'][1]['dialogue'] = [dict(speaker='c01', text='钥匙找到了。'), dict(speaker='c02', text='该出发了。')]
    pages[1]['panels'][1]['dialogue'] = [dict(speaker='c02', text='交给我。')]
    pages[2]['panels'][1]['dialogue'] = [dict(speaker='c01', text='灯亮了！')]
    return dict(pages=pages)


class OriginalStoryAndPropsTests(unittest.TestCase):
    setUp = fixture.LongComicCharacterReuseTests.setUp
    write = fixture.LongComicCharacterReuseTests.write
    create = fixture.LongComicCharacterReuseTests.create
    query = fixture.LongComicCharacterReuseTests.query
    generate = fixture.LongComicCharacterReuseTests.generate
    run_job = fixture.LongComicCharacterReuseTests.run_job

    def plan_story(self, edit=None):
        project = self.create(story=STORY, chapter_count=1)
        def answer(system, payload, value):
            if 'pages' in value:
                value = supplied_chapter()
                if edit:
                    value = edit(payload, value)
            return value
        self.query_hook = answer
        return self.run_job(project['id'], 'plan_chapter')

    def test_raw_story_and_positioned_exact_dialogue_reach_chapter_call(self):
        result = self.plan_story()
        self.assertEqual(result['job']['status'], 'completed')
        system, payload, _ = self.queries[-1]
        self.assertEqual(payload['story'], STORY)
        self.assertEqual(payload['source_requirements']['panels_per_page'], 2)
        self.assertEqual(payload['source_requirements']['dialogue'][0], dict(page=1, panel=2,
            lines=[dict(speaker='c01', text='钥匙找到了。'), dict(speaker='c02', text='该出发了。')]))
        self.assertIn('优先于chapter摘要', system)
        self.assertEqual([len(page['panels']) for page in result['chapters'][0]['pages']], [2, 2, 2])
        self.assertEqual(self.images, [])

    def test_wrong_panel_count_gets_one_targeted_repair_with_both_audits(self):
        def edit(payload, value):
            if 'validation_error' not in payload:
                value['pages'][0]['panels'].append(fixture.panel(3, [], '门口空镜。'))
            return value
        result = self.plan_story(edit)
        self.assertEqual(result['job']['status'], 'completed')
        calls = [row[1] for row in self.queries if 'pages_per_chapter' in row[1] and 'chapter_count' not in row[1]]
        self.assertEqual(len(calls), 2)
        self.assertIn('每页2格', calls[1]['validation_error'])
        self.assertEqual(calls[1]['story'], STORY)
        self.assertEqual(len(calls[1]['previous_result']['pages'][0]['panels']), 3)
        audits = [json.loads(path.read_text(encoding='utf-8')) for path in
                  (comic.DATA_ROOT / result['id'] / 'audit').glob('*.json')]
        chapter_audits = [row for row in audits if row['system'] == comic.CHAPTER_SYSTEM_V2]
        self.assertEqual(len(chapter_audits), 2)
        self.assertEqual(sorted(len(json.loads(row['raw'])['pages'][0]['panels']) for row in chapter_audits), [2, 3])

    def test_changed_dialogue_fails_after_exactly_one_repair_without_saving_pages(self):
        def edit(payload, value):
            value['pages'][0]['panels'][1]['dialogue'][0]['text'] = '改写后的台词。'
            return value
        result = self.plan_story(edit)
        self.assertEqual(result['job']['status'], 'failed')
        self.assertIn('逐字保留', result['job']['error'])
        self.assertEqual(len(self.queries), 3)  # 全书一次，本章初稿和修正各一次。
        self.assertEqual(result['chapters'][0]['pages'], [])
        self.assertEqual(self.images, [])

    def test_missing_giver_change_is_repaired_once_and_carries_across_three_pages(self):
        def edit(payload, value):
            first = value['pages'][0]['panels'][0]
            first.update(description='阿青拿起黄铜钥匙，阿白看着木桌。', state_changes=[
                fixture.change('c01', 'held_items', '右手握着黄铜钥匙', '拿起黄铜钥匙')])
            transfer = value['pages'][1]['panels'][1]
            transfer.update(description='阿青把黄铜钥匙递给阿白，阿白接过黄铜钥匙，阿青双手空着。',
                state_changes=[fixture.change('c02', 'held_items', '右手握着黄铜钥匙', '接过黄铜钥匙')])
            if 'validation_error' in payload:
                transfer['state_changes'].append(fixture.change('c01', 'held_items', '双手空空', '递给阿白'))
            return value
        result = self.plan_story(edit)
        self.assertEqual(result['job']['status'], 'completed')
        self.assertEqual(len(self.queries), 3)
        self.assertIn('同时更新原持有人和接收者', self.queries[-1][1]['validation_error'])
        final = result['chapters'][0]['pages'][2]
        self.assertEqual(final['opening_state']['c01']['held_items'], '双手空空')
        self.assertEqual(final['ending_state']['c02']['held_items'], '右手握着黄铜钥匙')
        self.assertEqual(self.images, [])

    def test_explicit_chapter_dialogue_does_not_leak_into_other_chapters(self):
        project = self.create(story='每页两格。第一章\n第一页第一格：阿青说“第一章。”\n第二章\n第一页第一格：阿白说“第二章。”')
        required = comic._source_requirements(project, 2)
        self.assertEqual(required['panels_per_page'], 2)
        self.assertEqual(required['dialogue'], [dict(page=1, panel=1, lines=[dict(speaker='c02', text='第二章。')])])
        project['story'] = STORY
        self.assertEqual(comic._source_requirements(project, 2)['dialogue'], [])

    def recalculate(self, rows, opening=None):
        people = [dict(id='c01', name='阿青', clothing='外套'), dict(id='c02', name='阿白', clothing='夹克')]
        state = continuity.initial_state(people)
        if opening:
            for identity, held in opening.items():
                state[identity]['held_items'] = held
        return continuity.recalculate([dict(number=1, panels=rows)], people, state, comic._text)

    def test_receiving_same_object_requires_giver_update_in_same_panel(self):
        row = fixture.panel(1, ['c01', 'c02'], '阿青把黄铜钥匙递给阿白，阿白接过黄铜钥匙。',
            [fixture.change('c02', 'held_items', '右手拿着一把黄铜钥匙', '接过黄铜钥匙')])
        with self.assertRaisesRegex(ValueError, '同时更新原持有人和接收者'):
            self.recalculate([row], {'c01': '右手握着黄铜钥匙'})

    def test_complete_transfer_clears_giver_and_preserves_other_held_object(self):
        row = fixture.panel(1, ['c01', 'c02'], '阿青把黄铜钥匙递给阿白，阿白接过黄铜钥匙，阿青仍拿着手电筒。',
            [fixture.change('c01', 'held_items', '左手拿着手电筒', '递给阿白'),
             fixture.change('c02', 'held_items', '右手握着黄铜钥匙', '接过黄铜钥匙')])
        following = fixture.panel(2, ['c01', 'c02'], '阿青和阿白看向门口。')
        rows, ending = self.recalculate([row, following], {'c01': '右手握着黄铜钥匙，左手拿着手电筒'})
        self.assertEqual(ending['c01']['held_items'], '左手拿着手电筒')
        self.assertEqual(ending['c02']['held_items'], '右手握着黄铜钥匙')
        self.assertEqual(rows[0]['panels'][1]['opening_state'], ending)
        row['state_changes'][0]['value'] = '双手空空'
        _, empty_ending = self.recalculate([row], {'c01': '右手握着黄铜钥匙'})
        self.assertEqual(empty_ending['c01']['held_items'], '双手空空')

    def test_same_object_is_not_duplicated_even_when_both_states_are_written(self):
        row = fixture.panel(1, ['c01', 'c02'], '阿青拿起黄铜钥匙，阿白也拿起黄铜钥匙。',
            [fixture.change('c01', 'held_items', '右手握着黄铜钥匙', '拿起黄铜钥匙'),
             fixture.change('c02', 'held_items', '黄铜钥匙（左手）', '拿起黄铜钥匙')])
        with self.assertRaisesRegex(ValueError, '不能同时由多个人持有'):
            self.recalculate([row])

    def test_different_items_and_explicit_multiple_same_kind_are_allowed(self):
        for first, second, description in (
            ('黄铜钥匙', '手电筒', '阿青拿起黄铜钥匙，阿白拿起手电筒。'),
            ('红色钥匙', '蓝色钥匙', '阿青拿起红色钥匙，阿白拿起蓝色钥匙。'),
            ('黄铜钥匙', '黄铜钥匙', '阿青和阿白各自拿起一把黄铜钥匙。')):
            with self.subTest(first=first, second=second):
                row = fixture.panel(1, ['c01', 'c02'], description,
                    [fixture.change('c01', 'held_items', '右手握着' + first, '拿起' + ('一把' if first == second else '') + first),
                     fixture.change('c02', 'held_items', '左手握着' + second, '拿起' + ('一把' if first == second else '') + second)])
                following = fixture.panel(2, ['c01', 'c02'], '阿青和阿白站在门边。')
                rows, _ = self.recalculate([row, following])
                self.assertEqual(rows[0]['panels'][1]['opening_state'], rows[0]['panels'][0]['ending_state'])

    def test_second_same_kind_item_can_be_acquired_with_explicit_another_item_evidence(self):
        row = fixture.panel(1, ['c02'], '阿白拿起另一把黄铜钥匙。',
            [fixture.change('c02', 'held_items', '右手握着黄铜钥匙', '拿起另一把黄铜钥匙')])
        _, ending = self.recalculate([row], {'c01': '右手握着黄铜钥匙'})
        self.assertEqual(ending['c01']['held_items'], ending['c02']['held_items'])


if __name__ == '__main__':
    unittest.main()
