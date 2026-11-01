"""Pure CPU regressions for independent scene state and source authority."""
import copy
import unittest

from cores.scene_condition import build_condition, condition_for_scene, positive_condition, project_condition_facts


class SceneConditionTests(unittest.TestCase):
    def test_five_requested_acceptance_cases(self):
        cases = [
            ('宫殿大厅', 'auto', 'tidy', set()),
            ('宫殿大厅，气氛黑暗', 'dark', 'tidy', set()),
            ('宫殿大厅，柱子有烧焦痕迹', 'auto', 'tidy', {('scorch', '柱子')}),
            ('宫殿大厅，东西杂乱', 'auto', 'messy', set()),
            ('宫殿大厅，气氛黑暗，建筑有烧焦痕迹，东西杂乱破败', 'dark', 'messy',
             {('scorch', '建筑'), ('wear', '陈设物品'), ('peeling', '陈设物品'), ('crack', '陈设物品')}),
        ]
        for source, lighting, order, effects in cases:
            with self.subTest(source=source):
                story = {'settings': {'initial_story_input': source, 'world': '东方', 'style': '电影级写实'}}
                c = condition_for_scene(story, {'name': '宫殿大厅', 'space': '自动设计的坍塌废墟'})
                self.assertEqual((c['lighting'], c['order'], c['structure'], c['spatial']),
                                 (lighting, order, 'intact', 'open'))
                self.assertEqual({(e['kind'], e['target']) for e in c['surface_effects']}, effects)

    def test_default_scene_is_maintained_and_scale_follows_use(self):
        for name, spatial in [('宫殿大厅', 'open'), ('广场', 'open'), ('卧室', 'by_use'), ('船舱', 'by_use')]:
            with self.subTest(name=name):
                c = build_condition(name)
                self.assertEqual((c['lighting'], c['order'], c['structure'], c['spatial']),
                                 ('auto', 'tidy', 'intact', spatial))
                self.assertEqual(c['surface_effects'], [])
                self.assertIn('干净整洁', positive_condition(c))

    def test_darkness_only_changes_lighting(self):
        c = build_condition('宫殿大厅', '黑暗的宫殿大厅')
        self.assertEqual(c['lighting'], 'dark')
        self.assertEqual(c['order'], 'tidy')
        self.assertEqual(c['structure'], 'intact')
        self.assertEqual(c['surface_effects'], [])
        prompt = positive_condition(c)
        self.assertIn('照明昏暗', prompt)
        self.assertIn('干净整洁', prompt)

    def test_scorch_is_surface_only_and_retains_target(self):
        for text, target in [('宫殿大厅北侧木门被烧焦', '北侧木门'),
                             ('宫殿大厅墙面烧焦', '墙面'),
                             ('建筑有烧焦痕迹', '建筑')]:
            with self.subTest(text=text):
                c = build_condition('宫殿大厅', text)
                self.assertEqual(c['surface_effects'][0]['target'], target)
                self.assertEqual({e['kind'] for e in c['surface_effects']}, {'scorch'})
                self.assertEqual((c['lighting'], c['order'], c['structure']), ('auto', 'tidy', 'intact'))

    def test_disorder_is_not_damage(self):
        c = build_condition('卧室', '卧室里东西杂乱')
        self.assertEqual(c['order'], 'messy')
        self.assertEqual(c['structure'], 'intact')
        self.assertEqual(c['surface_effects'], [])

    def test_dilapidated_objects_allow_finite_surface_damage(self):
        c = build_condition('大厅', '大厅里东西杂乱破败')
        self.assertEqual(c['order'], 'messy')
        self.assertEqual(c['structure'], 'intact')
        self.assertEqual({e['kind'] for e in c['surface_effects']}, {'wear', 'peeling', 'crack'})
        self.assertEqual({e['target'] for e in c['surface_effects']}, {'陈设物品'})

    def test_explicit_collapse_is_separate_from_scorch_and_wear(self):
        for text in ('大厅破败', '大厅烧焦', '大厅荒废失修'):
            self.assertEqual(build_condition('大厅', text)['structure'], 'intact')
        self.assertEqual(build_condition('大厅', '大厅屋顶已经坍塌')['structure'], 'collapsed')
        self.assertEqual(build_condition('大厅', '大厅屋顶损坏')['structure'], 'damaged')

    def test_explicit_compact_room_overrides_open_default(self):
        self.assertEqual(build_condition('大厅', '狭小的大厅')['spatial'], 'compact')
        self.assertEqual(build_condition('卧室', '宽敞的卧室')['spatial'], 'open')

    def test_explicit_negation_does_not_authorize_decay(self):
        c = build_condition('大厅', '大厅并不黑暗，没有烧焦，不要坍塌，墙面未剥落')
        self.assertEqual((c['lighting'], c['structure']), ('auto', 'intact'))
        self.assertEqual(c['surface_effects'], [])

    def test_auto_design_is_not_authority_when_story_exists(self):
        story = {'settings': {'initial_story_input': '他们在宫殿大厅见面。',
                              'one_line': '宫殿大厅烧焦倒塌。'}}
        card = {'name': '宫殿大厅', 'auto_made': True, 'space': '破败焦黑的大厅，屋顶坍塌',
                'design': '黑暗杂乱废墟', 'scene_condition': build_condition('大厅', '大厅坍塌')}
        c = condition_for_scene(story, card)
        self.assertEqual((c['lighting'], c['order'], c['structure']), ('auto', 'tidy', 'intact'))
        self.assertEqual(c['surface_effects'], [])
        self.assertEqual(c['origin'], 'story')

    def test_manual_card_and_user_edit_have_priority(self):
        story = {'one_line': '大厅明亮整洁。'}
        manual = {'name': '大厅', 'user_made': True, 'space': '大厅墙面烧焦'}
        self.assertEqual(condition_for_scene(story, manual)['surface_effects'][0]['kind'], 'scorch')
        edited = {'name': '大厅', 'space': '大厅昏暗', 'light': '明亮',
                  'design_fact_snapshot': {'space': '大厅破败', 'light': '明亮'}}
        c = condition_for_scene(story, edited)
        self.assertEqual(c['origin'], 'user_edit')
        self.assertEqual(c['lighting'], 'dark')
        self.assertEqual(c['surface_effects'], [])

    def test_marked_user_edit_does_not_reauthorize_unchanged_design(self):
        card = {'name': '大厅', 'edited_by_user': True, 'space': '大厅焦黑破败',
                'design_fact_snapshot': {'space': '大厅焦黑破败'}}
        c = condition_for_scene({'one_line': '大厅里来了一位客人。'}, card)
        self.assertEqual(c['origin'], 'story')
        self.assertEqual(c['surface_effects'], [])

    def test_lighting_only_edit_preserves_other_original_dimensions(self):
        story = {'one_line': '大厅昏暗，墙面有烧焦痕迹，东西杂乱。'}
        for field in ('light', 'main_light', 'base_light_color'):
            with self.subTest(field=field):
                card = {'name': '大厅', field: '明亮',
                        'space': '自动设计写入屋顶坍塌',
                        'design_fact_snapshot': {field: '昏暗', 'space': '自动设计写入屋顶坍塌'}}
                c = condition_for_scene(story, card)
                self.assertEqual((c['lighting'], c['order'], c['structure'], c['origin']),
                                 ('bright', 'messy', 'intact', 'user_edit'))
                self.assertEqual({(e['kind'], e['target']) for e in c['surface_effects']}, {('scorch', '墙面')})

    def test_whole_space_edit_can_restore_normal_conditions(self):
        story = {'one_line': '大厅昏暗，墙面烧焦，东西杂乱。'}
        card = {'name': '大厅', 'space': '大厅干净整洁，结构完整，照明明亮',
                'design_fact_snapshot': {'space': '大厅昏暗，墙面烧焦，东西杂乱'}}
        c = condition_for_scene(story, card)
        self.assertEqual((c['lighting'], c['order'], c['structure']), ('bright', 'tidy', 'intact'))
        self.assertEqual(c['surface_effects'], [])

    def test_legacy_user_edit_without_snapshot_and_direct_card(self):
        c = condition_for_scene({'one_line': '大厅完好。'},
                                {'name': '大厅', 'edited_by_user': True, 'space': '大厅杂乱'})
        self.assertEqual((c['origin'], c['order']), ('user_edit', 'messy'))
        c = condition_for_scene({}, {'name': '船舱', 'space': '狭小船舱，墙面生锈'})
        self.assertEqual((c['origin'], c['spatial']), ('direct_card', 'compact'))
        self.assertEqual(c['surface_effects'][0]['kind'], 'rust')

    def test_multi_scene_scope_follows_commas_and_periods(self):
        story = {'one_line': '书房墙面烧焦，门框有焦痕。大厅照明昏暗，里面有一张长桌。卧室宽敞。'}
        names = ('书房', '大厅', '卧室')
        library = condition_for_scene(story, {'name': '书房'}, scene_names=names)
        hall = condition_for_scene(story, {'name': '大厅'}, scene_names=names)
        bedroom = condition_for_scene(story, {'name': '卧室'}, scene_names=names)
        self.assertEqual({e['target'] for e in library['surface_effects']}, {'墙面', '门框'})
        self.assertEqual(hall['lighting'], 'dark')
        self.assertEqual(hall['surface_effects'], [])
        self.assertEqual(bedroom['lighting'], 'auto')
        self.assertEqual(bedroom['spatial'], 'open')

    def test_multi_scene_inline_modifiers_and_shared_state(self):
        story = {'one_line': '黑暗的大厅和烧焦的书房。'}
        hall = condition_for_scene(story, {'name': '大厅'}, scene_names=('大厅', '书房'))
        library = condition_for_scene(story, {'name': '书房'}, scene_names=('大厅', '书房'))
        self.assertEqual(hall['lighting'], 'dark')
        self.assertEqual(hall['surface_effects'], [])
        self.assertEqual(library['lighting'], 'auto')
        self.assertEqual(library['surface_effects'][0]['kind'], 'scorch')
        shared = {'one_line': '大厅和书房都很暗，家具排列整齐。'}
        # "很暗" is intentionally a small, explicit lighting phrase.
        for name in ('大厅', '书房'):
            c = condition_for_scene(shared, {'name': name}, scene_names=('大厅', '书房'))
            self.assertEqual(c['lighting'], 'dark')

    def test_calls_are_pure_and_positive_text_uses_no_negative_list(self):
        story = {'settings': {'one_line': '大厅北侧木门烧焦'}}
        card = {'name': '大厅', 'space': '屋顶坍塌'}
        before = copy.deepcopy((story, card))
        c = condition_for_scene(story, card)
        prompt = positive_condition(c)
        self.assertEqual((story, card), before)
        self.assertNotIn('不要', prompt)
        self.assertNotIn('禁止', prompt)
        self.assertNotIn('无坍塌', prompt)
        self.assertIn('北侧木门保留烧焦痕迹', prompt)

    def test_dark_projection_replaces_old_light_and_retains_window_subject(self):
        facts = {'space': '大厅两侧高窗透入明亮自然光，石柱整齐排列。',
                 'light': '明亮窗光', 'main_light': '自然光', 'base_light_color': '暖黄灯光'}
        original = copy.deepcopy(facts)
        result = project_condition_facts(facts, {'lighting': 'dark'})
        self.assertEqual(facts, original)
        for field in ('light', 'main_light', 'base_light_color'):
            self.assertIn('照明昏暗', result[field])
        self.assertIn('大厅两侧高窗', result['space'])
        self.assertIn('石柱整齐排列', result['space'])
        self.assertNotIn('明亮', result['space'])
        self.assertNotIn('自然光', result['space'])

    def test_dark_projection_keeps_scale_material_and_surface_states(self):
        facts = {'contract_text': '三十米宽大厅，朱红石柱与青铜门，北墙保留焦痕，陈设杂乱',
                 'depth': '高窗｜自然光从高窗透入｜柱列向远处收缩',
                 'materials': '灰蓝石地板，暗红色挂毯'}
        result = project_condition_facts(facts, {'lighting': 'dark'})
        self.assertEqual(result['contract_text'], facts['contract_text'])
        self.assertEqual(result['materials'], facts['materials'])
        self.assertIn('高窗', result['depth'])
        self.assertIn('柱列向远处收缩', result['depth'])
        self.assertNotIn('自然光', result['depth'])

    def test_bright_projection_removes_old_dim_light_but_keeps_construction(self):
        facts = {'space': '昏暗的大厅，柱列高耸', 'fixtures': ['青铜壁灯发出微弱烛光', '整齐的木椅'],
                 'main_light': '暗淡微光'}
        result = project_condition_facts(facts, {'lighting': 'bright'})
        self.assertIn('照明明亮', result['main_light'])
        self.assertIn('大厅', result['space'])
        self.assertIn('柱列高耸', result['space'])
        self.assertIn('青铜壁灯', result['fixtures'][0])
        self.assertNotIn('微弱烛光', result['fixtures'][0])
        self.assertEqual(result['fixtures'][1], '整齐的木椅')

    def test_auto_lighting_projection_is_an_independent_unchanged_copy(self):
        facts = {'space': '明亮的大厅', 'fixtures': [{'name': '青铜灯具'}]}
        result = project_condition_facts(facts, {'lighting': 'auto'})
        self.assertEqual(result, facts)
        self.assertIsNot(result, facts)
        self.assertIsNot(result['fixtures'], facts['fixtures'])
        self.assertIsNot(result['fixtures'][0], facts['fixtures'][0])

    def test_dark_projection_preserves_compatible_dim_and_unknown_descriptions(self):
        facts = {'space': '高窗透入微弱冷光，柱列间摆放青铜烛台',
                 'fixtures': {'curtain': '灰蓝色帷幔', 'floor': '光滑大理石'},
                 'scene_condition': {'lighting': 'dark', 'source_text': '用户说明里提及明亮往事'}}
        result = project_condition_facts(facts, {'lighting': 'dark'})
        self.assertEqual(result['space'], facts['space'])
        self.assertEqual(result['fixtures'], facts['fixtures'])
        self.assertEqual(result['scene_condition'], facts['scene_condition'])

    def test_messy_projection_removes_old_arrangement_and_preserves_architecture(self):
        facts = {'space': '整体整洁，朱漆柱列对称排列整齐，青铜器物摆放整齐，空间开阔',
                 'fixtures': ['桌椅整齐有序', '木制家具井然有序'], 'light': '明亮自然光'}
        original = copy.deepcopy(facts)
        result = project_condition_facts(facts, {'lighting': 'auto', 'order': 'messy'})
        self.assertEqual(facts, original)
        self.assertNotIn('整体整洁', result['space'])
        self.assertIn('朱漆柱列对称排列整齐', result['space'])
        self.assertIn('青铜器物', result['space'])
        self.assertNotIn('青铜器物摆放整齐', result['space'])
        self.assertEqual(result['fixtures'], ['桌椅', '木制家具'])
        self.assertEqual(result['light'], facts['light'])

    def test_messy_positive_condition_is_visible_and_keeps_structure_intact(self):
        c = build_condition('大厅', '大厅东西杂乱')
        prompt = positive_condition(c)
        self.assertIn('活动陈设错位摆放，局部物件散置，建筑轮廓和主要通行关系清楚', prompt)
        self.assertIn('主体结构完整', prompt)
        self.assertEqual(c['surface_effects'], [])

    def test_tidy_projection_preserves_arrangement_and_messy_keeps_clean_surfaces(self):
        facts = {'space': '大厅整体整洁，桌椅整齐，地面干净，朱漆柱列对称'}
        self.assertEqual(project_condition_facts(facts, {'order': 'tidy'}), facts)
        result = project_condition_facts(facts, {'order': 'messy'})
        self.assertIn('地面干净', result['space'])
        self.assertIn('朱漆柱列对称', result['space'])
        self.assertNotIn('整体整洁', result['space'])
        self.assertNotIn('桌椅整齐', result['space'])


if __name__ == '__main__':
    unittest.main()
