"""CPU-only empty-set opening-state regressions; no live projects or models."""
import copy
import unittest

from cores.scene_condition import (build_condition, missing_opening_residue, opening_scene_source,
                                   positive_condition, project_condition_facts,
                                   unsupported_opening_residue)


class SceneOpeningProjectionTests(unittest.TestCase):
    def condition(self, source='城堡卧室'):
        return build_condition('卧室', source)

    def test_observed_neutral_room_contamination_is_removed(self):
        facts = {'space': '室内房间，中央偏后为木质四柱床，铺着布满褶皱与湿痕的白色粗糙床单。',
                 'depth': '远景为石砌壁炉。前景床尾散落的皮带与领带。',
                 'ground': '深色木地板上散落着一条皮带和一条领带。',
                 'light': '壁炉与两侧壁灯提供暖光。'}
        original = copy.deepcopy(facts)
        result = project_condition_facts(facts, self.condition())
        text = str(result)
        for bad in ('湿痕', '散落', '皮带', '领带'):
            self.assertNotIn(bad, text)
        for fact in ('中央偏后', '木质四柱床', '白色粗糙床单', '深色木地板', '石砌壁炉'):
            self.assertIn(fact, text)
        self.assertEqual(result['light'], facts['light'])
        self.assertEqual(facts, original)

    def test_both_bedding_word_orders_are_detected(self):
        for text in ('布满褶皱与湿痕的白色粗糙床单', '白色床单布满湿痕'):
            with self.subTest(text=text):
                self.assertIn('床品湿痕', unsupported_opening_residue(text, self.condition()))

    def test_fixed_bed_anchor_location_material_survive_same_clause(self):
        text = '中央偏后的深棕木质四柱床铺着布满湿痕的白色床单。'
        result = project_condition_facts({'space': text}, self.condition())['space']
        self.assertIn('中央偏后的深棕木质四柱床', result)
        self.assertIn('白色床单', result)
        self.assertNotIn('湿痕', result)

    def test_common_wet_bedding_forms_are_bounded_to_bedding(self):
        for text in ('湿床单', '床单变湿', '床单湿了', '潮湿的床单', '湿的白色床单'):
            with self.subTest(text=text):
                self.assertIn('床品湿痕', unsupported_opening_residue(text, self.condition()))
                c = self.condition('卧室开场放着' + text)
                self.assertFalse(unsupported_opening_residue(text, c))
        self.assertFalse(unsupported_opening_residue('白色床单有自然褶皱。窗外湿地水面清楚。', self.condition()))

    def test_scattered_belt_and_tie_do_not_become_fixed_decor(self):
        for text in ('前景床尾散落的皮带与领带', '地板上散落着一条皮带和一条领带'):
            with self.subTest(text=text):
                self.assertEqual(set(unsupported_opening_residue(text, self.condition())),
                                 {'散落皮带', '散落领带'})

    def test_normal_folds_materials_and_natural_water_survive(self):
        facts = {'materials': '床单有自然褶皱，粗糙石材，亚麻织纹清楚。',
                 'space': '窗外雨水打湿石路，落叶散落在庭院，星光散落在湖面。',
                 'fixtures': '桌上是一杯水，衣帽架上陈列外套。'}
        self.assertEqual(project_condition_facts(facts, self.condition()), facts)

    def test_ordinary_scattered_tabletop_books_and_cups_survive(self):
        facts = {'fixtures': '桌面散落着翻开的羊皮卷、黄铜烛台和水晶酒杯。'}
        self.assertFalse(unsupported_opening_residue(facts['fixtures'], self.condition()))
        self.assertEqual(project_condition_facts(facts, self.condition()), facts)

    def test_explicit_same_subject_wet_source_is_preserved(self):
        c = self.condition('卧室的白色床单有水渍，枕头保持干燥。')
        facts = {'fixtures': '白色床单带有湿痕。'}
        self.assertFalse(unsupported_opening_residue(facts['fixtures'], c))
        self.assertEqual(project_condition_facts(facts, c), facts)
        self.assertNotIn('场景干净整洁', positive_condition(c))
        self.assertTrue(unsupported_opening_residue('枕头带有湿痕。', c))

    def test_explicit_opening_wet_bedding_is_required_in_positive_output(self):
        c = self.condition('卧室白色床单有水渍。')
        self.assertIn('床单留有湿痕', positive_condition(c))
        self.assertFalse(missing_opening_residue('白色床单带有湿痕。', c))
        self.assertIn('缺少床单湿痕', missing_opening_residue('床单洁净平整。', c))
        self.assertIn('缺少床单湿痕', missing_opening_residue('枕头有湿痕。', c))

    def test_required_wet_state_does_not_accept_dirty_or_blood_replacement(self):
        c = self.condition('卧室床单有清水水渍。')
        for text in ('床单布满污渍。', '床单留有血渍。'):
            with self.subTest(text=text):
                self.assertTrue(missing_opening_residue(text, c))
        for source, expected in (('床单留有污渍', '床单留有污渍'),
                                 ('床单留有血迹', '床单留有血渍')):
            c = self.condition(source)
            self.assertIn(expected, positive_condition(c))
            self.assertFalse(missing_opening_residue(expected, c))

    def test_explicit_clothing_scatter_required_but_generic_mess_needs_no_clothing(self):
        c = self.condition('卧室地板上散落皮带。')
        self.assertIn('可见散落的皮带', positive_condition(c))
        self.assertTrue(missing_opening_residue('卧室地板整洁。', c))
        self.assertFalse(missing_opening_residue('地板上散落着皮带。', c))
        self.assertFalse(missing_opening_residue('家具错位，物件散置。', self.condition('房间杂乱。')))

    def test_later_wet_result_does_not_create_opening_requirement(self):
        c = self.condition('卧室开场整洁，随后床单变湿。')
        self.assertFalse(missing_opening_residue('白色床单洁净平整。', c))
        self.assertNotIn('床单留有湿痕', positive_condition(c))

    def test_water_state_does_not_authorize_dirty_or_bloody_bedding(self):
        c = self.condition('卧室床单有清水水渍。')
        self.assertTrue(unsupported_opening_residue('床单布满污渍。', c))
        self.assertTrue(unsupported_opening_residue('床单布满血迹。', c))

    def test_explicit_same_scattered_object_does_not_allow_other_belongings(self):
        c = build_condition('书房', '书桌上散落羊皮卷。')
        self.assertFalse(unsupported_opening_residue('桌面上散落着翻开的羊皮卷。', c))
        self.assertTrue(unsupported_opening_residue('地板散落着皮带和衣物。', c))

    def test_explicit_messy_room_allows_disorder_but_not_wet_bed(self):
        c = self.condition('房间杂乱，床品凌乱。')
        text = '床单凌乱，地板散落着皮带和领带。'
        self.assertFalse(unsupported_opening_residue(text, c))
        self.assertTrue(unsupported_opening_residue('床单布满湿痕。', c))
        self.assertEqual(project_condition_facts({'space': text}, c)['space'], text)

    def test_later_wet_result_cannot_authorize_opening(self):
        c = self.condition('卧室开场整洁，随后床单变湿。')
        self.assertNotIn('变湿', opening_scene_source(c))
        self.assertTrue(unsupported_opening_residue('白色床单带有湿痕。', c))
        c = self.condition('卧室开场整洁，使用后床单湿润，随后房间变得凌乱。')
        self.assertTrue(unsupported_opening_residue('床单湿润，地面散落衣物。', c))

    def test_later_disorder_does_not_change_opening_condition(self):
        c = self.condition('卧室开场整洁，随后房间变得凌乱。')
        self.assertEqual(c['order'], 'tidy')
        self.assertIn('场景干净整洁', positive_condition(c))
        self.assertNotIn('错位摆放', positive_condition(c))

    def test_marked_later_residue_is_rejected_even_when_messy(self):
        c = self.condition('卧室杂乱。')
        self.assertIn('后续动作痕迹', unsupported_opening_residue(
            '使用后床单湿润。', c))

    def test_explicit_dark_damage_and_weather_are_independent(self):
        c = self.condition('卧室昏暗，墙面烧焦，窗外下雨。')
        facts = {'space': '卧室墙面烧焦，深色石墙与木梁。', 'fixtures': '床单布满湿痕。',
                 'light': '高窗透入明亮日光。', 'ground': '深色木地板。'}
        result = project_condition_facts(facts, c)
        self.assertIn('墙面烧焦', result['space'])
        self.assertIn('照明昏暗', result['light'])
        self.assertNotIn('湿痕', result['fixtures'])

    def test_nested_facts_copy_and_projection_are_idempotent(self):
        facts = {'fixtures': [{'bed': '白色床单布满湿痕。'}, '地板散落皮带。'],
                 'scene_condition': {'source_text': '床单湿润'}}
        original = copy.deepcopy(facts)
        c = self.condition()
        projected = project_condition_facts(facts, c)
        self.assertEqual(project_condition_facts(projected, c), projected)
        self.assertEqual(facts, original)
        self.assertEqual(projected['scene_condition'], facts['scene_condition'])


if __name__ == '__main__':
    unittest.main()
