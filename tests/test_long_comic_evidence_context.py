"""Exact original-story evidence with page boundaries; mocked runtime only."""
import copy
import unittest
from unittest.mock import patch

from cores import long_comic_core as comic, long_comic_continuity as continuity
from tests import test_long_comic_character_reuse as fixture


STORY = '第一页第一格：阿青拿起桌上的黄铜钥匙，阿白站在木桌旁。第二页第一格：阿青放下黄铜钥匙。'


class EvidenceContextTests(unittest.TestCase):
    setUp = fixture.LongComicCharacterReuseTests.setUp
    write = fixture.LongComicCharacterReuseTests.write
    create = fixture.LongComicCharacterReuseTests.create
    query = fixture.LongComicCharacterReuseTests.query
    generate = fixture.LongComicCharacterReuseTests.generate
    run_job = fixture.LongComicCharacterReuseTests.run_job

    def row(self, evidence='拿起桌上的黄铜钥匙', field='held_items'):
        return fixture.panel(1, ['c01', 'c02'], '阿青右手握着黄铜钥匙，阿白站在木桌旁。',
            [fixture.change('c01', field, '右手握着黄铜钥匙', evidence)])

    def people(self):
        return [dict(id='c01', name='阿青', clothing='蓝色外套'), dict(id='c02', name='阿白', clothing='绿色工作服')]

    def calculate(self, row, *, page=1, story=STORY):
        people = self.people()
        return continuity.recalculate([dict(number=page, panels=[row])], people,
            continuity.initial_state(people), comic._text, evidence_context=story)

    def test_completed_pose_accepts_exact_same_page_story_action_but_default_remains_strict(self):
        with self.assertRaisesRegex(ValueError, '动作依据'):
            continuity.panels([self.row()], self.people(), comic._text)
        rows, ending = self.calculate(self.row())
        self.assertEqual(rows[0]['panels'][0]['state_changes'][0]['evidence'], '拿起桌上的黄铜钥匙')
        self.assertEqual(ending['c01']['held_items'], '右手握着黄铜钥匙')

    def test_invented_static_or_wrong_field_evidence_is_rejected(self):
        for evidence, field in (('拿起另一只箱子', 'held_items'), ('右手握着黄铜钥匙', 'held_items'),
                                ('拿起桌上的黄铜钥匙', 'clothing'), ('阿白站在木桌旁', 'location')):
            with self.subTest(evidence=evidence, field=field), self.assertRaisesRegex(ValueError, '动作依据'):
                self.calculate(self.row(evidence, field))

    def test_another_page_or_missing_numbered_page_cannot_supply_evidence(self):
        for page, evidence in ((1, '放下黄铜钥匙'), (2, '拿起桌上的黄铜钥匙'), (3, '拿起桌上的黄铜钥匙')):
            with self.subTest(page=page, evidence=evidence), self.assertRaisesRegex(ValueError, '动作依据'):
                self.calculate(self.row(evidence), page=page)

    def test_explicit_chapter_is_selected_before_numbered_page(self):
        story = '第一章\n第一页：阿青拿起桌上的黄铜钥匙。\n第二章\n第一页：阿青放下黄铜钥匙。'
        context = continuity.source_evidence_context(story, chapter_number=2)
        self.assertNotIn('拿起', context)
        with self.assertRaisesRegex(ValueError, '动作依据'):
            self.calculate(self.row(), story=context)
        self.calculate(self.row('放下黄铜钥匙'), story=context)
        self.assertEqual(continuity.source_evidence_context(story, 1, 3), '')

    def test_story_without_page_markers_is_available_as_exact_chapter_evidence(self):
        self.calculate(self.row(), story='在修复室，阿青拿起桌上的黄铜钥匙。')

    def planned_source(self):
        project = self.create(story='第一页第一格：阿青拿起桌上的黄铜钥匙，阿白站在木桌旁。',
                              chapter_count=1, pages_per_chapter=1)
        def answer(system, payload, value):
            if 'pages' in value:
                value = dict(pages=[dict(number=1, summary='阿青找到钥匙。', scene='修复室木桌旁', panels=[self.row()])])
            return value
        self.query_hook = answer
        return self.run_job(project['id'], 'plan_chapter')

    def test_planning_and_manual_panel_edit_both_use_saved_original_story(self):
        result = self.planned_source()
        self.assertEqual(result['job']['status'], 'completed')
        self.assertEqual(len(self.queries), 2)  # 无需消耗额外一次修正。
        panels = copy.deepcopy(result['chapters'][0]['pages'][0]['panels'])
        panels[0]['description'] = '阿青稳稳握着黄铜钥匙，阿白看向窗外。'
        changed = comic.update_project(result['id'], dict(chapter=1, page=1, panels=panels))
        self.assertEqual(changed['chapters'][0]['ending_state']['c01']['held_items'], '右手握着黄铜钥匙')
        self.assertEqual(self.images, [])

    def test_saved_response_normalization_does_not_save_start_jobs_call_models_or_mutate_inputs(self):
        project = self.planned_source()
        raw = dict(pages=[dict(number=1, summary='阿青找到钥匙。', scene='修复室木桌旁', panels=[self.row()])])
        before_project, before_raw = copy.deepcopy(project), copy.deepcopy(raw)
        with patch.object(comic, '_save', side_effect=AssertionError('must not save')), \
                patch.object(comic, '_query', side_effect=AssertionError('must not query')), \
                patch.object(comic, 'start_job', side_effect=AssertionError('must not start')):
            normalized = comic.normalize_chapter_plan(project, 1, raw)
        self.assertEqual(project, before_project)
        self.assertEqual(raw, before_raw)
        self.assertEqual(normalized['ending_state']['c01']['held_items'], '右手握着黄铜钥匙')
        self.assertEqual(len(normalized['pages']), 1)

    def test_multiple_object_evidence_can_come_from_story_with_completed_pose(self):
        row = self.row()
        row['description'] = '阿青和阿白各自右手握着一把黄铜钥匙。'
        row['state_changes'] = [fixture.change(identity, 'held_items', '右手握着黄铜钥匙', '各自拿起一把黄铜钥匙')
                                for identity in ('c01', 'c02')]
        self.calculate(row, story='第一页：阿青和阿白各自拿起一把黄铜钥匙。')


if __name__ == '__main__':
    unittest.main()
