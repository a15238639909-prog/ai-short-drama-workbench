"""CPU-only regression checks: incremental assets preserve accepted identities."""
import copy
import os
os.environ['V41_NO_MODEL'] = '1'
import unittest
from unittest.mock import Mock, patch

from cores import authoring, asset_core, story_core, oral_story, universal_writer, director_flow, saga_core, opening
from cores import continuation_assets as continuity


class IdentityTests(unittest.TestCase):
    def test_parenthesized_outfits_keep_each_person_and_every_garment_colour(self):
        source = '阿宁（28岁女性，蓝色外套、黑色长裤）和林海（30岁男性，灰色衬衫、黑色长裤）。'
        hints = oral_story.outfit_hints(source, ['阿宁', '林海'])
        self.assertEqual(hints, {'阿宁': ['蓝色外套、黑色长裤'], '林海': ['灰色衬衫、黑色长裤']})
        self.assertEqual(oral_story.outfit_words(hints['林海']), ['衬衫', '长裤'])
        wrong = '内搭：灰色衬衫。外层：深灰马甲。下装：深灰长裤。配件：耳钉。标志物：手表。'
        fixed, facts = oral_story.fixed_outfit_description(hints['林海'], wrong)
        self.assertEqual(fixed, '灰色衬衫；黑色长裤')
        self.assertEqual(facts, ['灰色衬衫', '黑色长裤'])
        contradictory = '黑色高领薄针织衫，露出锁骨。深蓝外套。黑色长裤。'
        self.assertEqual(oral_story.fixed_outfit_description(hints['阿宁'], contradictory)[0], '蓝色外套；黑色长裤')

    def test_partial_clothing_requirement_leaves_other_unconstrained_slots(self):
        fixed, _ = oral_story.fixed_outfit_description(['蓝色外套'], '外层：红色外套。下装：黑色长裤。鞋履：白色运动鞋。')
        self.assertIn('蓝色外套', fixed)
        self.assertNotIn('红色外套', fixed)
        self.assertIn('黑色长裤', fixed)
        self.assertIn('白色运动鞋', fixed)

    def test_explicit_alias_reuses_id_without_substring_merge(self):
        cards = [{'name': '小法师', 'character_id': 'C1', 'aliases': ['艾琳']}]
        self.assertIs(continuity.match_character('艾琳', cards), cards[0])
        self.assertIsNone(continuity.match_character('法师', cards))
        self.assertIsNone(continuity.match_character('大法师', cards))
        self.assertIsNone(continuity.match_character('艾琳', cards + [{'name': '另一人', 'aliases': ['艾琳']}]))

    def test_different_locations_of_same_kind_are_separate(self):
        cards = [{'name': '北方森林', 'scene_id': 'S1', 'aliases': ['北林']}]
        self.assertIsNone(continuity.match_scene('南方森林', cards))
        self.assertIsNone(continuity.match_scene('森林入口', cards))
        self.assertIs(continuity.match_scene('北林', cards), cards[0])
        self.assertIs(continuity.match_scene('北方森林内', cards), cards[0])

    def test_missing_mentioned_and_future_entities_are_not_present(self):
        source = '林北已经失踪。阿宁寻找林北。阿宁说：“林北可能在钟楼。”阿宁准备进入钟楼。'
        self.assertFalse(continuity.appears_in_source('林北', source))
        self.assertFalse(continuity.appears_in_source('钟楼', source, kind='场景'))
        self.assertTrue(continuity.appears_in_source('阿宁', source))
        self.assertTrue(continuity.appears_in_source('林北', '林北走出大门，此后消失在雨幕中。'))

    def test_script_dialogue_mentions_are_not_cast_or_locations(self):
        source = '阿宁：带去给周师傅看看。\n林海目光落在闭合的盒盖上，双手端起红盒。\n林海：我们明天去钟楼。'
        self.assertFalse(continuity.appears_in_source('周师傅', source, evidence='阿宁：带去给周师傅看看。'))
        self.assertFalse(continuity.appears_in_source('钟楼', source, kind='场景', evidence='林海：我们明天去钟楼。'))
        self.assertTrue(continuity.appears_in_source('阿宁', source))
        self.assertTrue(continuity.appears_in_source('林海', source))
        self.assertTrue(continuity.appears_in_source('周师傅', '周师傅：我看看。'))
        self.assertFalse(continuity.appears_in_source('周师傅', '【D3】阿宁（合上盒盖）：带去给周师傅看看。'))
        self.assertTrue(continuity.appears_in_source('林海', '阿宁（拉住林海）：先等一下。'))

    def test_same_interior_position_needs_explicit_continuity_and_old_landmark(self):
        old = {'scene_id': 'S1', 'name': '店里', 'space': '室内修表店，中央是一张木工作台。', 'visuals': ['ADOPTED']}
        before = copy.deepcopy(old)
        context = '紧接上一话，同一修表店、同一张工作台。'
        self.assertIs(continuity.match_scene('店内工作台前', [old], context=context), old)
        self.assertIs(continuity.match_scene('修表店内工作台前', [old], context=context), old)
        self.assertIsNone(continuity.match_scene('店内工作台前', [old]))
        self.assertIsNone(continuity.match_scene('另一家店内工作台前', [old], context=context))
        self.assertIsNone(continuity.match_scene('店外工作台前', [old], context=context))
        self.assertIsNone(continuity.match_scene('店内二楼卧室前', [old], context=context))
        self.assertIsNone(continuity.match_scene('店内工作台前', [old, dict(old, scene_id='S2')], context=context))
        self.assertEqual(old, before)

    def test_bare_furniture_requires_same_landmark_and_same_parent_scene(self):
        old = {'scene_id': 'S1', 'name': '店里', 'space': '室内修表店，中央是一张木工作台。', 'visuals': ['ADOPTED']}
        context = '同一修表店、同一张工作台。'
        self.assertIs(continuity.match_scene('工作台', [old], context=context), old)
        self.assertIs(continuity.match_scene('工作台前', [old], context=context), old)
        self.assertIsNone(continuity.match_scene('工作台', [old], context='同一修表店。'))
        self.assertIsNone(continuity.match_scene('工作台', [old], context='同一张工作台。'))
        self.assertIsNone(continuity.match_scene('工作台', [dict(old, space='室内修表店，中央有书桌。')], context=context))
        for place in ('二楼卧室', '另一家修表店', '店外院子', '天台'):
            self.assertIsNone(continuity.match_scene(place, [dict(old, space=old['space'] + place)],
                context=context + '同一' + place + '。'))
        self.assertIsNone(continuity.match_scene('工作台', [old, dict(old, scene_id='S2')], context=context))


class IncrementalAssetTests(unittest.TestCase):
    def extract(self, ep=2, old_chars=None, old_scenes=None, raw=None, prose=None, arrangement=None, brief='继续前情'):
        old_chars = copy.deepcopy(old_chars if old_chars is not None else [{'name': '阿宁', 'character_id': 'C1',
            'age': '28', 'clothing': '', 'visuals': ['ACCEPTED'], 'aliases': ['小宁']}])
        old_scenes = copy.deepcopy(old_scenes if old_scenes is not None else [{'name': '北方森林', 'scene_id': 'S1',
            'space': '', 'visuals': ['ACCEPTED_SCENE']}])
        before = copy.deepcopy((old_chars, old_scenes))
        prose = prose or '小宁和埃德在南方森林交谈。埃德打开木箱，取出路线图。林北已经失踪。小宁说：“我们找不到林北。”'
        raw = raw if raw is not None else [('人物', '小宁', {'年龄': '99'}),
            ('人物', '埃德', {'年龄': '32', '身份': '守门人', '衣着': '灰色斗篷；此刻沾满泥污'}),
            ('人物', '林北', {}), ('人物', '凭空人物', {}),
            ('场景', '北方森林', {'空间': '改写的旧场景'}),
            ('场景', '南方森林', {'空间': '松林'}), ('场景', '南方森林', {'空间': '重复结果'})]
        create = Mock(side_effect=lambda sid, rows: copy.deepcopy(rows))
        scene = Mock(side_effect=lambda sid, fields: dict(fields, scene_id='NEW'))
        with patch.object(authoring, '_ep', return_value=({}, {'brief': brief, 'prose': prose})), \
             patch.object(authoring, '_extract_cards_raw', return_value=raw), \
             patch.object(authoring, 'confirmed_arrangement', return_value=arrangement), \
             patch.object(authoring, '_create_cards', create), patch.object(authoring, '_dbg'), \
             patch.object(story_core, 'get_story', return_value={'settings': {}}), \
             patch.object(asset_core, 'list_assets', side_effect=lambda sid, kind: old_chars if kind == 'characters' else old_scenes), \
             patch.object(asset_core, 'create_scene', scene), \
             patch.object(asset_core, 'save_asset') as save, patch.object(asset_core, 'delete_asset') as delete:
            result = authoring.prepare_episode_assets('TEST', ep=ep)
        self.assertEqual((old_chars, old_scenes), before)
        for call in save.call_args_list:
            self.assertEqual(call.args[1], 'scenes')
            self.assertEqual(call.args[2].get('scene_id'), 'NEW')
        delete.assert_not_called()
        return result, create, scene

    def test_first_and_later_episodes_use_same_incremental_rules(self):
        for ep in (1, 2, 8):
            with self.subTest(ep=ep):
                result, create, scene = self.extract(ep=ep)
                self.assertEqual(result['added'], ['埃德'])
                self.assertEqual(result['kept'], ['阿宁'])
                self.assertEqual(result['scenes'], ['南方森林'])
                self.assertEqual(scene.call_count, 1)
                new = create.call_args.args[1][0]
                self.assertEqual(new['first_episode'], ep)
                self.assertEqual(new['clothing'], '灰色斗篷')
                self.assertEqual(new['age'], '32')

    def test_old_arrangement_does_not_hide_valid_new_actor(self):
        result, create, _ = self.extract(arrangement={'items': {'who': {'people': [{'name': '阿宁'}]},
                                                                 'where': {'places': ['北方森林']}}})
        self.assertEqual(result['added'], ['埃德'])
        self.assertEqual(result['scenes'], ['南方森林'])

    def test_second_episode_mention_waits_for_actual_third_episode_appearance(self):
        raw = [('人物', '周师傅', {'出场证据': '阿宁：带去给周师傅看看。'})]
        result, create, _ = self.extract(ep=2, raw=raw,
            prose='阿宁：带去给周师傅看看。\n林海端起红盒，留在原地。')
        self.assertEqual(result['added'], [])
        self.assertEqual(result['skipped'][0]['name'], '周师傅')
        create.assert_not_called()
        result, create, _ = self.extract(ep=3, raw=raw,
            prose='周师傅走进店门，接过林海手里的红盒。\n周师傅：我看看。')
        self.assertEqual(result['added'], ['周师傅'])
        self.assertEqual(create.call_args.args[1][0]['first_episode'], 3)

    def test_both_scene_preparation_entries_reuse_explicit_same_interior(self):
        old = {'scene_id': 'S1', 'name': '店里', 'space': '室内修表店，中央是一张木工作台。', 'visuals': ['ADOPTED']}
        brief = '紧接上一话，同一修表店、同一张工作台。'
        prose = '林海站在店内工作台前，双手端起红盒。'
        result, _, create = self.extract(old_scenes=[old], brief=brief, prose=prose,
            raw=[('场景', '店内工作台前', {'空间': '工作台上暖光台灯亮着。'})])
        self.assertEqual(result['scenes'], [])
        self.assertEqual(result['merged'][0]['to'], '店里')
        create.assert_not_called()
        with patch.object(authoring, '_ep', return_value=({}, {'brief': brief, 'prose': prose})), \
             patch.object(authoring, 'scenes_from_pictures', return_value=[{'name': '店内工作台前'}]), \
             patch.object(asset_core, 'list_assets', return_value=[old]), \
             patch.object(asset_core, 'create_scene') as create, patch.object(asset_core, 'save_asset') as save:
            result = authoring.new_places_for_episode('TEST', 2)
        self.assertEqual(result, [])
        create.assert_not_called()
        save.assert_not_called()

    def test_bare_furniture_does_not_create_a_scene_in_either_asset_entry(self):
        old = {'scene_id': 'S1', 'name': '店里', 'space': '室内修表店，中央是一张木工作台。', 'visuals': ['ADOPTED']}
        brief = '同一修表店、同一张工作台。'
        prose = '林海站在工作台前，双手端起红盒。'
        result, _, create = self.extract(old_scenes=[old], brief=brief, prose=prose,
            raw=[('场景', '工作台', {'出场证据': prose})])
        self.assertEqual(result['scenes'], [])
        self.assertEqual(result['merged'][0]['to'], '店里')
        create.assert_not_called()
        with patch.object(authoring, '_ep', return_value=({}, {'brief': brief, 'prose': prose})), \
             patch.object(authoring, 'scenes_from_pictures', return_value=[{'name': '工作台'}]), \
             patch.object(asset_core, 'list_assets', return_value=[old]), \
             patch.object(asset_core, 'create_scene') as create, patch.object(asset_core, 'save_asset') as save:
            self.assertEqual(authoring.new_places_for_episode('TEST', 2), [])
        create.assert_not_called()
        save.assert_not_called()

    def test_repeated_preparation_does_not_create_or_edit_existing_cards(self):
        result, create, scene = self.extract(old_chars=[{'name': '阿宁', 'aliases': ['小宁']}, {'name': '埃德'}],
            old_scenes=[{'name': '北方森林'}, {'name': '南方森林'}])
        self.assertEqual(result['added'], [])
        self.assertEqual(result['scenes'], [])
        create.assert_not_called()
        scene.assert_not_called()

    def test_an_active_unnamed_role_is_not_discarded_as_incidental(self):
        result, create, _ = self.extract(raw=[('人物', '守卫', {'身份': '守卫'})],
            prose='守卫打开门，把木牌递给阿宁。阿宁收下木牌，然后走进大厅。')
        self.assertEqual(result['added'], ['守卫'])

    def test_ensure_scenes_keeps_unselected_empty_historical_cards(self):
        cards = [{'name': '大厅', 'scene_id': 'S1'}, {'name': '旧房间', 'scene_id': 'S2', 'space': ''}]
        before = copy.deepcopy(cards)
        with patch.object(authoring, 'plan_places', return_value=['大厅']), \
             patch.object(asset_core, 'list_assets', return_value=cards), \
             patch.object(asset_core, 'delete_asset') as delete, patch.object(asset_core, 'save_asset') as save:
            result = authoring.ensure_scenes('TEST', ep=2)
        self.assertEqual(result, [cards[0]])
        self.assertEqual(cards, before)
        delete.assert_not_called()
        save.assert_not_called()

    def test_new_card_does_not_inherit_another_episodes_temporary_damage(self):
        story = {'settings': {}, 'saga': {'episodes': [
            {'no': 1, 'prose': '阿宁的衣服已撕破湿透。'}, {'no': 2, 'prose': '埃德走进大厅。'}]}}
        create = Mock(side_effect=lambda sid, fields: dict(fields, character_id='NEW'))
        with patch.object(story_core, 'get_story', return_value=story), \
             patch.object(asset_core, 'create_character', create), \
             patch.object(asset_core, 'get_asset', return_value={'name': '埃德', 'character_id': 'NEW'}), \
             patch.object(authoring, 'normalize_cards_for_world'), patch.object(authoring, '_dbg'), \
             patch.object(oral_story, 'outfit_hints', return_value={}), \
             patch.object(authoring, '_q', side_effect=AssertionError('model forbidden')) as model:
            authoring._create_cards('TEST', [{'name': '埃德', 'age': '32', 'sex': '男',
                                             'clothing': '灰色斗篷', 'first_episode': 2}])
        self.assertEqual(create.call_args.args[1]['clothing'], '灰色斗篷')
        model.assert_not_called()

    def test_roster_does_not_force_absent_cast_into_writing(self):
        data = universal_writer.writing_input({'story_mode': 'single', '_cast_names': ['阿宁', '林北'],
                                               '_events': []}, ep=3, brief='林北失踪，阿宁向守卫问路。')
        self.assertEqual(data['已有人物身份表（复用设定，不代表全员在场）'], ['阿宁', '林北'])
        self.assertIn('失踪', data['人物规则'])
        self.assertIn('新人物或新身份可以登场', data['人物规则'])
        self.assertIn('不凭空添加人物、新事件或新主线', data['人物规则'])

    def test_new_cast_is_available_even_with_an_old_arrangement(self):
        cards = [{'name': '阿宁'}, {'name': '埃德'}]
        story = {'settings': {}, 'saga': {'episodes': [{'no': 2, 'prose': '埃德打开门。'}]}}
        with patch.object(asset_core, 'list_assets', return_value=cards), \
             patch.object(story_core, 'get_story', return_value=story), \
             patch.object(authoring, 'confirmed_arrangement', return_value={'items': {'who': {'people': [{'name': '阿宁'}]}}}):
            self.assertEqual(authoring.cast_cards('TEST'), cards)

    def test_missing_image_design_does_not_edit_existing_faces(self):
        cards = [{'name': '阿宁', 'character_id': 'OLD', 'age': '28', 'sex': '男', 'visuals': ['ACCEPTED']},
                 {'name': '埃德', 'character_id': 'NEW', 'age': '32', 'sex': '男'}]
        original = copy.deepcopy(cards[0])
        with patch.object(asset_core, 'list_assets', return_value=cards), \
             patch.object(asset_core, 'save_asset') as save, \
             patch.object(authoring, '_q', return_value='[{"name":"埃德"}]'):
            result = authoring.design_faces('TEST', character_ids={'NEW'})
        self.assertEqual(result['ok'], 1)
        self.assertEqual(cards[0], original)
        self.assertEqual(save.call_args.args[2]['character_id'], 'NEW')
        self.assertEqual(save.call_count, 1)

    def test_new_character_uses_first_appearance_brief_and_locks_clothing(self):
        story = {'settings': {'one_line': '阿宁（28岁女性，蓝色外套、黑色长裤）。'},
                 'saga': {'episodes': [{'no': 1, 'brief': '阿宁在店里。'},
                    {'no': 3, 'brief': '周师傅（55岁男性，棕色夹克、深色长裤）来到店里。', 'prose': '周师傅打开工具箱。'}]}}
        create = Mock(side_effect=lambda sid, fields: dict(fields, character_id='NEW'))
        with patch.object(story_core, 'get_story', return_value=story), \
             patch.object(asset_core, 'create_character', create), \
             patch.object(asset_core, 'get_asset', return_value={'name': '周师傅', 'character_id': 'NEW'}), \
             patch.object(authoring, 'normalize_cards_for_world'), patch.object(authoring, '_dbg'), \
             patch.object(authoring, '_q', side_effect=AssertionError('model forbidden')) as model:
            authoring._create_cards('TEST', [{'name': '周师傅', 'age': '55', 'sex': '男',
                'clothing': '绿色毛衣，白色长裤，金手链。', 'first_episode': 3}])
        fields = create.call_args.args[1]
        self.assertEqual(fields['clothing'], '棕色夹克；深色长裤')
        self.assertTrue(fields['clothing_from_brief'])
        self.assertEqual(fields['clothing_requirement'], '自定义')
        self.assertEqual(fields['first_episode'], 3)
        model.assert_not_called()


class PromptRecoveryTests(unittest.TestCase):
    def test_nonempty_invalid_prompt_is_repaired_and_old_media_preserved(self):
        source = '阿宁：小心脚下。'
        good = '\n\n'.join(field + ':\n' + ('<d>[Chinese] 小心脚下。</d>' if field == 'detailed_description' else 'test')
                            for field in director_flow.FIELDS)
        broken = good.replace('<d>[Chinese] 小心脚下。</d>', '阿宁静静站着。')
        rows = [{'no': 1, 'prompt': broken, 'video': 'accepted_clip_1.mp4', 'source_text': source,
                 'seconds': 8, 'scene': '大厅', 'plan': {'flow': director_flow.VERSION}},
                {'no': 2, 'prompt': good, 'video': 'accepted_clip_2.mp4', 'source_text': source,
                 'seconds': 8, 'scene': '大厅', 'plan': {'flow': director_flow.VERSION}}]
        timeline = {'scenes': [{'segments': rows}]}
        timeline['scenes'][0]['segments'][1]['prompt_repair_pending'] = '已手动修正的旧错误'
        timeline['prompt_failures'] = {'2': '旧错误'}
        rows = copy.deepcopy(rows)
        rows[1].pop('prompt_repair_pending')
        def save(_sid, _ep, data):
            timeline.clear()
            timeline.update(copy.deepcopy(data))
            return True
        def generate(_sid, _ep, **kwargs):
            self.assertEqual((kwargs['start'], kwargs['count']), (0, 1))
            return [{'n': 1, 'prompt': good, 'source_text': source, 'scene': '大厅', 'seconds': 8,
                     'source_contract': {'source_original': source},
                     'director_contract': {'source_contract_version': 1}}]
        with patch.object(authoring, 'apply_slicing_drift', return_value=(0, 0)), \
             patch.object(authoring, 'total_segments', return_value=2), \
             patch.object(authoring, 'make_h3_prompts', side_effect=generate) as generate_mock, \
             patch.object(saga_core, 'ep_timeline', side_effect=lambda *args: copy.deepcopy(timeline)), \
             patch.object(saga_core, 'save_ep_timeline', side_effect=save), \
             patch.object(saga_core, 'merge_segment_media'), patch.object(saga_core, 'stamp_source'), \
             patch.object(asset_core, 'list_assets', return_value=[]), \
             patch.object(story_core, 'get_story', return_value={}), patch.object(opening, 'enabled', return_value=False):
            self.assertEqual(authoring.next_prompts('TEST', 2, 2), ([1], 2))
        generate_mock.assert_called_once()
        result = timeline['scenes'][0]['segments']
        self.assertEqual(result[0]['video'], 'accepted_clip_1.mp4')
        self.assertTrue(result[0]['stale_video'])
        self.assertEqual(result[0]['source_contract']['source_original'], source)
        self.assertEqual(result[0]['director_contract']['source_contract_version'], 1)
        self.assertEqual(result[1], rows[1])
        self.assertFalse(timeline.get('prompt_failures'))

    def test_duration_change_preserves_and_marks_old_video(self):
        old = {'scenes': [{'segments': [{'no': 1, 'prompt': 'same prompt', 'video': 'old.mp4', 'seconds': 8}]}]}
        with patch.object(saga_core, 'ep_timeline', return_value=old), \
             patch.object(saga_core, 'save_ep_timeline', return_value=True) as save, \
             patch.object(saga_core, 'merge_segment_media'), patch.object(saga_core, 'stamp_source'), \
             patch.object(asset_core, 'list_assets', return_value=[]), \
             patch.object(story_core, 'get_story', return_value={}), patch.object(opening, 'enabled', return_value=False):
            authoring.build_timeline('TEST', 2, [{'n': 1, 'prompt': 'same prompt', 'seconds': 12}])
        segment = save.call_args.args[2]['scenes'][0]['segments'][0]
        self.assertEqual(segment['video'], 'old.mp4')
        self.assertEqual(segment['seconds'], 12)
        self.assertTrue(segment['stale_video'])

    def test_invalid_user_prompt_is_retained_with_actionable_failure(self):
        original = {'no': 1, 'prompt': '用户编辑的提示词', 'prompt_edited': True, 'video': 'kept.mp4',
                    'source_text': '阿宁：快走。', 'scene': '大厅', 'seconds': 8,
                    'plan': {'flow': director_flow.VERSION}}
        timeline = {'scenes': [{'segments': [copy.deepcopy(original)]}]}
        def save(_sid, _ep, data):
            timeline.clear()
            timeline.update(copy.deepcopy(data))
            return True
        with patch.object(authoring, 'apply_slicing_drift', return_value=(0, 0)), \
             patch.object(authoring, 'total_segments', return_value=1), \
             patch.object(authoring, 'make_h3_prompts') as generate, \
             patch.object(saga_core, 'ep_timeline', side_effect=lambda *args: copy.deepcopy(timeline)), \
             patch.object(saga_core, 'save_ep_timeline', side_effect=save):
            self.assertEqual(authoring.next_prompts('TEST', 2, 1), ([], 1))
        generate.assert_not_called()
        self.assertEqual(timeline['scenes'][0]['segments'][0]['prompt'], original['prompt'])
        self.assertIn('手动修改', timeline['prompt_failures']['1'])


if __name__ == '__main__':
    unittest.main()
