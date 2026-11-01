"""Version-two character snapshots and panel continuity; every model is mocked."""
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import unittest

from cores import long_comic_core as comic, long_comic_characters as sources, long_comic_continuity as continuity
from tests import test_long_comic_core as legacy_fixture


def change(character_id, field, value, evidence):
    return dict(character_id=character_id, field=field, value=value, evidence=evidence)


def panel(number, cast, description, changes=None, speaker=None):
    return dict(number=number, character_ids=cast, description=description,
        state_changes=changes or [], dialogue=[dict(speaker=speaker, text='找到路了。')] if speaker else [], narration='')


def chapter_v2(payload):
    pages = [dict(number=1, summary='两位修复师分头查看房间。', scene='修复室门边',
        character_ids=['invented'], ending_state={'invented': '忽略模型自报状态'},
        panels=[panel(1, ['c01'], '阿青拿起钥匙，走到门旁，抬眼查看门锁。', [
            change('c01', 'held_items', '右手拿着钥匙', '拿起钥匙'),
            change('c01', 'location', '门旁', '走到门旁')], speaker='c01'),
                panel(2, ['c02'], '阿白看着木桌上的钟表，轻轻点头。')]),
        dict(number=2, summary='阿青为出门做准备。', scene='修复室门边',
            panels=[panel(1, ['c01'], '阿青脱下蓝色外套，穿好黄色雨衣，放下钥匙后抬起空手。', [
                change('c01', 'clothing', '黄色雨衣', '穿好黄色雨衣'),
                change('c01', 'held_items', '双手空空', '放下钥匙')])]),
        dict(number=3, summary='阿青观察门外。', scene='修复室门边',
            panels=[panel(1, ['c01'], '阿青侧头望向窗外，神情专注。')])]
    if payload['chapter']['number'] > 1:
        for page in pages:
            page['panels'] = [panel(1, ['c01'], '阿青望向室外，观察接下来要走的路。')]
    return dict(pages=pages[:payload['pages_per_chapter']], ending_state='模型的错误总结不会覆盖程序状态')


class LongComicCharacterReuseTests(unittest.TestCase):
    generate = legacy_fixture.LongComicCoreTests.generate
    run_job = legacy_fixture.LongComicCoreTests.run_job

    def setUp(self):
        legacy_fixture.LongComicCoreTests.setUp(self)
        self.source_root = self.folder / 'source_data'
        source_patch = patch.object(sources, 'DATA_ROOT', self.source_root)
        source_patch.start()
        self.addCleanup(source_patch.stop)
        self.cards = {}
        for number, name, appearance, clothing in ((1, '阿青', '原卡确认的黑色短发', '蓝色外套'),
                                                    (2, '阿白', '原卡确认的棕色长发', '绿色工作服')):
            identity = 'CHAR_' + name
            card = dict(character_id=identity, story_id='STORY_001', name=name, age='35', sex='男',
                height='178cm', hair='沿用原卡发型', appearance_details=appearance, clothing=clothing,
                behavior_anchor='认真谨慎', version=3, updated=100 + number, edited_by_user=True,
                image_prompt='三视图白底排版，不得混入漫画页')
            self.cards[identity] = card
            self.write(self.source_root / 'assets/STORY_001/characters' / (identity + '.json'), card)
        self.write(self.source_root / 'stories/STORY_001.json',
                   dict(story_id='STORY_001', title='来源项目', character_ids=list(self.cards)))

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def query(self, system, user, **options):
        payload = json.loads(user)
        self.queries.append((system, copy.deepcopy(payload), copy.deepcopy(options)))
        if 'chapter_count' in payload:
            value = legacy_fixture.book(payload['chapter_count'])
            if payload['characters']:
                value['characters'] = copy.deepcopy(payload['characters'])
                for person in value['characters']:
                    person.update(appearance='Qwen企图重写外观', clothing='Qwen企图恢复旧衣', personality='Qwen企图改性格')
        else:
            value = chapter_v2(payload)
        if self.query_hook:
            value = self.query_hook(system, payload, value)
        return json.dumps(value, ensure_ascii=False)

    def create(self, **updates):
        fields = dict(title='逐格接续', story='两位成年修复师准备出门。', chapter_count=2, pages_per_chapter=3,
                      source_story_id='STORY_001', source_character_ids=list(self.cards))
        fields.update(updates)
        return comic.create_project(fields)

    def test_source_listing_and_mapping_have_no_directory_or_model_side_effects(self):
        before = {path: path.read_bytes() for path in self.source_root.rglob('*.json')}
        with patch.object(Path, 'mkdir', side_effect=AssertionError('source reads must not create directories')):
            listed = comic.list_character_sources()
            cards = comic.get_source_characters('STORY_001')
        self.assertEqual(listed, [dict(story_id='STORY_001', title='来源项目', character_count=2)])
        self.assertEqual(cards[0]['character_id'], 'CHAR_阿青')
        self.assertIn('原卡确认的黑色短发', cards[0]['appearance'])
        self.assertIn('35', cards[0]['appearance'])
        self.assertEqual(cards[0]['personality'], '认真谨慎')
        self.assertNotIn('image_prompt', cards[0])
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertEqual(self.queries, [])

    def test_import_copies_snapshot_hash_and_source_ids_without_mutating_source(self):
        before = {path: path.read_bytes() for path in self.source_root.rglob('*.json')}
        project = self.create()
        self.assertEqual(project['continuity_version'], 2)
        origin = project['character_source']['characters'][0]
        self.assertEqual((origin['source_character_id'], origin['comic_character_id']), ('CHAR_阿青', 'c01'))
        snapshot = origin['snapshot']
        digest = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                          separators=(',', ':')).encode('utf-8')).hexdigest()
        self.assertEqual(origin['hash'], digest)
        self.assertEqual((origin['version'], origin['updated'], origin['edited_by_user']), (3, 101, True))
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        changed = dict(self.cards['CHAR_阿青'], appearance_details='来源卡后来已改变')
        self.write(self.source_root / 'assets/STORY_001/characters/CHAR_阿青.json', changed)
        saved = comic.get_project(project['id'])
        self.assertEqual(saved['characters'], project['characters'])
        self.assertEqual(saved['character_source']['characters'][0]['snapshot'], snapshot)

    def test_source_validation_rejects_path_escape_unknown_ids_and_empty_selection(self):
        for story_id, identities in (('../outside', ['CHAR_阿青']), ('STORY_001', ['../outside']),
                                      ('STORY_001', ['CHAR_不存在']), ('STORY_001', []),
                                      ('STORY_001', ['CHAR_阿青', 'CHAR_阿青'])):
            with self.subTest(story_id=story_id, identities=identities), self.assertRaises(ValueError):
                self.create(source_story_id=story_id, source_character_ids=identities)

    def test_planning_preserves_all_provided_character_fields(self):
        project = self.create()
        result = self.run_job(project['id'], 'plan')
        self.assertEqual(result['job']['status'], 'completed')
        self.assertEqual(result['characters'], project['characters'])
        with self.assertRaisesRegex(ValueError, '规划'):
            comic.update_project(project['id'], dict(source_story_id='STORY_001', source_character_ids=['CHAR_阿白']))

    def test_independent_new_project_uses_v2_without_source(self):
        project = comic.create_project(dict(title='独立', story='两位成年修复师出门。', chapter_count=1, pages_per_chapter=3))
        self.assertEqual(project['continuity_version'], 2)
        self.assertNotIn('character_source', project)
        result = self.run_job(project['id'], 'plan_chapter')
        self.assertEqual(result['job']['status'], 'completed')

    def test_empty_source_fields_preserve_independent_supplied_characters(self):
        people = legacy_fixture.book(1)['characters']
        project = comic.create_project(dict(title='独立', story='普通修复故事。', characters=people,
                                           source_story_id='', source_character_ids=[]))
        self.assertEqual(project['characters'], people)
        self.assertNotIn('character_source', project)

    def test_panel_union_state_carry_and_compile_do_not_spread_all_characters(self):
        project = self.create()
        result = self.run_job(project['id'], 'plan_chapter')
        self.assertEqual(result['job']['status'], 'completed')
        first, second, third = result['chapters'][0]['pages']
        self.assertEqual(first['character_ids'], ['c01', 'c02'])
        self.assertEqual(second['character_ids'], ['c01'])
        self.assertEqual(first['panels'][1]['opening_state']['c01']['held_items'], '右手拿着钥匙')
        self.assertEqual(third['opening_state']['c01']['clothing'], '黄色雨衣')
        self.assertEqual(third['ending_state']['c01']['held_items'], '双手空空')
        self.assertEqual(result['chapters'][0]['ending_state'], third['ending_state'])
        self.assertNotIn('阿白', third['prompt'])
        self.assertNotIn('认真谨慎', first['prompt'])
        self.assertNotIn('神态：', first['prompt'])
        self.assertNotIn('未知', first['prompt'])
        self.assertNotIn('三视图', first['prompt'])
        self.assertNotIn('黄色雨衣', first['prompt'])
        self.assertIn('黄色雨衣', json.loads(third['prompt'])['items'][0]['caption'])
        self.assertNotIn('蓝色外套', third['prompt'])

    def test_second_chapter_receives_saved_computed_state(self):
        project = self.create()
        first = self.run_job(project['id'], 'plan_chapter')
        second = self.run_job(project['id'], 'plan_chapter', chapter_number=2)
        self.assertEqual(second['job']['status'], 'completed')
        self.assertEqual(self.queries[-1][1]['previous_chapter_state'], first['chapters'][0]['ending_state'])
        self.assertEqual(second['chapters'][1]['opening_state'], first['chapters'][0]['ending_state'])
        self.assertIn('黄色雨衣', json.loads(second['chapters'][1]['pages'][0]['prompt'])['items'][0]['caption'])

    def test_dialogue_speaker_must_appear_in_that_panel(self):
        project = self.create()
        def invalid(system, payload, value):
            if 'pages' in value:
                value['pages'][0]['panels'][0]['dialogue'][0]['speaker'] = 'c02'
            return value
        self.query_hook = invalid
        result = self.run_job(project['id'])
        self.assertEqual(result['job']['status'], 'failed')
        self.assertIn('本格', result['job']['error'])
        self.assertEqual(self.images, [])

    def test_state_changes_require_field_action_and_exact_same_panel_evidence(self):
        people = [dict(id='c01', clothing='蓝色外套')]
        cases = [change('c01', 'clothing', '雨衣', '穿着蓝色外套'),
                 change('c01', 'location', '门旁', '穿好雨衣'),
                 change('c01', 'unsupported', '雨衣', '穿好雨衣'),
                 change('c02', 'clothing', '雨衣', '穿好雨衣'),
                 change('c01', 'clothing', '雨衣', '别格才穿好雨衣')]
        for invalid in cases:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                continuity.panels([panel(1, ['c01'], '穿着蓝色外套，接着穿好雨衣。', [invalid])], people, comic._text)
        valid = [change('c01', 'injury', '左手已包扎', '包扎左手'),
                 change('c01', 'location', '门边', '走到门边')]
        self.assertEqual(len(continuity.panels([panel(1, ['c01'], '包扎左手后走到门边。', valid)], people, comic._text)[0]['state_changes']), 2)

    def test_visible_known_names_and_ids_require_matching_panel_cast(self):
        people = [dict(id='c01', name='阿青'), dict(id='c02', name='阿白'), dict(id='c03', name='小阿青')]
        for description in ('阿青与阿白在桌边交换目光。', 'c02走到窗边。'):
            with self.subTest(description=description), self.assertRaisesRegex(ValueError, '加入本格出场人物'):
                continuity.panels([panel(1, ['c01'], description)], people, comic._text)
        accepted = panel(1, ['c03'], '小阿青微笑。', speaker='c03')
        accepted['dialogue'][0]['text'] = '我想问阿白一件事。'
        accepted['narration'] = '阿白还在别处。'
        self.assertEqual(continuity.panels([accepted], people, comic._text)[0]['character_ids'], ['c03'])

    def test_edit_recalculates_following_pages_flags_images_and_preserves_versions(self):
        project = self.create()
        complete = self.run_job(project['id'])
        self.assertEqual(complete['job']['status'], 'completed')
        before = copy.deepcopy(complete['chapters'][0]['pages'])
        panels = copy.deepcopy(before[1]['panels'])
        panels[0]['description'] = panels[0]['description'].replace('黄色雨衣', '绿色雨衣')
        panels[0]['state_changes'][0].update(value='绿色雨衣', evidence='穿好绿色雨衣')
        changed = comic.update_project(project['id'], dict(chapter=1, page=2, panels=panels))
        pages = changed['chapters'][0]['pages']
        self.assertFalse(pages[0]['needs_redo'])
        self.assertTrue(pages[1]['needs_redo'])
        self.assertTrue(pages[2]['needs_redo'])
        self.assertEqual(pages[2]['opening_state']['c01']['clothing'], '绿色雨衣')
        self.assertIn('绿色雨衣', json.loads(pages[2]['prompt'])['items'][0]['caption'])
        self.assertEqual([row['versions'] for row in pages], [row['versions'] for row in before])
        self.assertEqual(len(self.images), 3)
        skipped = self.run_job(project['id'])
        self.assertEqual(len(self.images), 3)
        self.assertTrue(skipped['chapters'][0]['pages'][2]['needs_redo'])
        self.assertEqual(skipped['chapters'][0]['status'], 'partial')
        self.assertEqual(skipped['job']['step'], '有修改页待单页重做')
        with self.assertRaisesRegex(ValueError, '单页重做'):
            comic.export_project(project['id'], chapter=1)

    def test_legacy_project_cannot_be_silently_migrated_by_source_selection(self):
        project = self.create()
        project.pop('continuity_version')
        comic._save(project)
        with self.assertRaisesRegex(ValueError, '旧版漫画'):
            comic.update_project(project['id'], dict(source_story_id='STORY_001', source_character_ids=['CHAR_阿青']))
        self.assertNotIn('continuity_version', comic.get_project(project['id']))

    def mark_second_chapter_stale(self, **updates):
        project = self.create(**updates)
        self.run_job(project['id'], 'plan_chapter')
        planned = self.run_job(project['id'], 'plan_chapter', chapter_number=2)
        panels = copy.deepcopy(planned['chapters'][0]['pages'][1]['panels'])
        panels[0]['description'] = panels[0]['description'].replace('黄色雨衣', '绿色雨衣')
        panels[0]['state_changes'][0].update(value='绿色雨衣', evidence='穿好绿色雨衣')
        return comic.update_project(project['id'], dict(chapter=1, page=2, panels=panels))

    def test_earlier_state_edit_blocks_later_planned_chapter_dispatch_and_exports(self):
        changed = self.mark_second_chapter_stale()
        self.assertTrue(changed['chapters'][1]['continuity_review_required'])
        for action in ('generate', 'plan_chapter', 'redo_page'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, '重新规划'):
                comic.start_job(changed['id'], action, chapter=2, page=1)
        for scope in (2, None):
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, '重新规划'):
                comic.export_project(changed['id'], chapter=scope)
        self.assertEqual(self.images, [])

    def test_replan_archives_old_pages_versions_and_uses_current_previous_state(self):
        changed = self.mark_second_chapter_stale()
        raw = comic._load(changed['id'])
        raw['chapters'][1]['pages'][0]['versions'] = [dict(version=1, status='completed', path='retained_original.png')]
        comic._save(raw)
        old = copy.deepcopy(raw['chapters'][1])
        result = self.run_job(changed['id'], 'replan_chapter', chapter_number=2)
        self.assertEqual(result['job']['status'], 'completed')
        chapter = result['chapters'][1]
        self.assertNotIn('continuity_review_required', chapter)
        self.assertEqual(chapter['plan_versions'][0]['chapter'], old)
        self.assertEqual(chapter['pages'][0]['versions'], [])
        self.assertEqual(chapter['opening_state']['c01']['clothing'], '绿色雨衣')
        self.assertEqual(self.queries[-1][1]['previous_chapter_state'], result['chapters'][0]['ending_state'])
        self.assertEqual(result['characters'], changed['characters'])
        self.assertEqual(result['character_source'], changed['character_source'])

    def test_failed_replan_keeps_previous_plan_and_versions_unchanged(self):
        changed = self.mark_second_chapter_stale()
        old = copy.deepcopy(changed['chapters'][1])
        def invalid(system, payload, value):
            if system == comic.CHAPTER_SYSTEM_V2:
                value['pages'][0]['panels'][0]['character_ids'] = ['unknown']
            return value
        self.query_hook = invalid
        result = self.run_job(changed['id'], 'replan_chapter', chapter_number=2)
        self.assertEqual(result['job']['status'], 'failed')
        self.assertEqual(result['chapters'][1], old)
        self.assertEqual(self.images, [])

    def test_later_marked_chapters_must_be_replanned_in_order(self):
        project = self.create(chapter_count=3)
        for number in (1, 2, 3):
            planned = self.run_job(project['id'], 'plan_chapter', chapter_number=number)
        panels = copy.deepcopy(planned['chapters'][0]['pages'][1]['panels'])
        panels[0]['description'] = panels[0]['description'].replace('黄色雨衣', '绿色雨衣')
        panels[0]['state_changes'][0].update(value='绿色雨衣', evidence='穿好绿色雨衣')
        comic.update_project(project['id'], dict(chapter=1, page=2, panels=panels))
        with self.assertRaisesRegex(ValueError, '第2章'):
            comic.start_job(project['id'], 'replan_chapter', chapter=3)
        result = self.run_job(project['id'], 'replan_chapter', chapter_number=2)
        self.assertNotIn('continuity_review_required', result['chapters'][1])
        self.assertTrue(result['chapters'][2]['continuity_review_required'])

    def test_manual_prompt_change_and_dirty_only_images_are_not_exported_as_fresh(self):
        project = self.create()
        complete = self.run_job(project['id'])
        comic.update_project(project['id'], dict(chapter=1, page=1, prompt='阿青在门旁检查钥匙。'))
        with self.assertRaisesRegex(ValueError, '单页重做'):
            comic.export_project(project['id'], chapter=1)
        raw = comic._load(project['id'])
        first = raw['chapters'][0]['pages'][0]
        first.update(needs_redo=False, prompt_dirty=True)
        comic._save(raw)
        with self.assertRaisesRegex(ValueError, '单页重做'):
            comic.export_project(project['id'], chapter=1)
        result = self.run_job(project['id'])
        self.assertEqual(result['job']['step'], '有修改页待单页重做')
        self.assertEqual(result['chapters'][0]['status'], 'partial')
        self.assertEqual(len(self.images), 3)
        self.assertEqual(result['chapters'][0]['pages'][0]['versions'], complete['chapters'][0]['pages'][0]['versions'])


if __name__ == '__main__':
    unittest.main()
