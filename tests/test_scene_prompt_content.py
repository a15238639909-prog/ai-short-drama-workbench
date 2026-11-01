"""Pure text checks: no models, files, services, or production projects."""
import copy
import unittest

from cores.scene_prompt_content import ScenePromptContentError, validate_scene_prompt
from cores.scene_condition import build_condition, condition_for_scene


class ScenePromptContentTests(unittest.TestCase):
    def check(self, prompt, space='中世纪城堡里正常使用的长廊', name='城堡长廊'):
        return validate_scene_prompt(prompt, {'name': name, 'space': space})

    def reject(self, prompt, space='中世纪城堡里正常使用的长廊', name='城堡长廊'):
        with self.assertRaises(ScenePromptContentError) as caught:
            self.check(prompt, space, name)
        self.assertEqual(caught.exception.code, 'scene_prompt_content')
        self.assertTrue(caught.exception.issues)
        return str(caught.exception)

    def test_actual_castle_failure_is_rejected(self):
        message = self.reject('地面铺设带有岁月磨损痕迹的石板，缝隙间有细微青苔或灰尘。墙上挂着褪色的挂毯，边缘略有磨损。')
        for word in ('磨损', '青苔', '褪色'):
            self.assertIn(word, message)

    def test_ancient_medieval_and_old_style_do_not_authorize_damage(self):
        for source in ('古老城堡', '中世纪城堡', '旧式城堡', '有人居住的老宅'):
            with self.subTest(source=source):
                self.reject('木门漆面开裂，墙面有污渍。', source)

    def test_plain_materials_and_shadow_are_not_damage(self):
        prompt = '粗糙石材的晶粒清楚。斑驳阳光落在地面，空气中微尘在光束里漂浮。木纹自然，金属反光细小。'
        self.assertIs(self.check(prompt), prompt)

    def test_inhabited_dark_room_keeps_darkness(self):
        prompt = '深夜的会客室，壁炉是唯一光源。木质扶手椅沉入深暗阴影，地毯织纹在火光下可辨。'
        self.assertEqual(self.check(prompt, '深夜有人居住的会客室，只有壁炉火光', '会客室'), prompt)

    def test_explicit_abandonment_authorizes_old_condition(self):
        for source in ('废弃多年的医院走廊', '年久失修的城堡长廊', '破败的旧大厅'):
            with self.subTest(source=source):
                prompt = '墙漆剥落，木门漆面开裂。金属生锈，地面覆盖灰尘，砖缝生长青苔。'
                self.assertEqual(self.check(prompt, source), prompt)

    def test_specific_surface_wear_is_preserved_without_authorizing_other_damage(self):
        source = '石板地面有轻微磨损，墙面维护完好'
        self.assertEqual(self.check('石板地面留有轻微磨损。', source), '石板地面留有轻微磨损。')
        self.reject('木门漆面开裂。', source)
        self.reject('挂毯边缘有磨损。', source)

    def test_explicit_fading_rust_and_wall_peeling_are_preserved(self):
        for text in ('挂毯褪色', '金属推车生锈', '墙漆剥落'):
            with self.subTest(text=text):
                self.assertEqual(self.check(text, text), text)

    def test_negated_source_damage_is_not_authority(self):
        self.reject('墙面有污渍。', '正常会客室，墙面没有污渍')
        self.reject('木门漆面开裂。', '古老的大厅，不要木门开裂')

    def test_natural_moss_and_rough_stone_survive(self):
        prompt = '溪边岩石的缝隙覆盖青苔，林间透下斑驳阳光。'
        self.assertEqual(self.check(prompt, '雨后森林溪流', '森林溪边'), prompt)

    def test_explicit_indoor_moss_is_preserved(self):
        prompt = '室内石壁缝隙长着青苔。'
        self.assertEqual(self.check(prompt, '城堡长廊石壁缝隙长有青苔'), prompt)

    def test_ordinary_living_room_magazine_and_water_are_valid_set_details(self):
        prompt = '茶几上随意摆放着翻开的杂志与一杯水。'
        self.assertEqual(self.check(prompt, '普通家庭客厅', '客厅'), prompt)

    def test_cup_saucer_and_water_in_cup_are_valid_set_details(self):
        for prompt in ('茶几上摆放一套白色骨瓷杯碟。', '杯中盛有清水。', '玻璃杯子装有红酒。'):
            with self.subTest(prompt=prompt):
                self.assertEqual(self.check(prompt, '普通家庭客厅', '客厅'), prompt)

    def test_bookcase_and_tabletop_drink_can_share_an_ordinary_set(self):
        prompt = '右侧书架陈列藏书，桌面上摆着一只水晶酒杯。'
        self.assertEqual(self.check(prompt, '普通会客室', '会客室'), prompt)

    def test_actual_person_and_meta_instruction_are_rejected_together(self):
        prompt = ('一位身着深色丝绒长袍的人物（具体形象待后续卡片定义，此处表现为剪影）'
                  '正坐在壁炉旁的高背扶手椅上。')
        message = self.reject(prompt, '深夜有人居住的城堡会客室，只有壁炉火光', '会客室')
        self.assertIn('场景底图中的人物', message)
        self.assertIn('制作元指令', message)

    def test_portrait_silhouette_is_not_an_empty_architectural_board(self):
        for prompt in ('壁炉旁的女子剪影清晰。', '一个男子坐在扶手椅上。', '站在窗边的人影被照亮。'):
            with self.subTest(prompt=prompt):
                self.assertIn('场景底图中的人物', self.reject(prompt))

    def test_inhabited_state_art_and_architectural_shadows_are_preserved(self):
        for prompt in ('有人居住的城堡会客室，壁炉旁摆着高背扶手椅。',
                       '墙上油画中的人物肖像清晰。', '壁画上绘有女子剪影。',
                       '壁炉旁摆着一尊男子雕像。', '窗框与建筑塔楼的剪影投在墙上。'):
            with self.subTest(prompt=prompt):
                self.assertEqual(self.check(prompt), prompt)

    def test_person_standing_before_painting_is_still_a_person(self):
        self.assertIn('场景底图中的人物', self.reject('一位男子站在油画前。'))

    def test_long_use_wear_synonym_is_still_source_bound(self):
        self.assertIn('磨损旧化', self.reject('地板表面因长期使用呈现自然的磨痕与哑光质感。'))

    def test_scrolls_and_cup_can_express_ordinary_room_use(self):
        prompt = '桌面上散落着翻开的羊皮卷、黄铜烛台和一只水晶酒杯。'
        self.assertEqual(self.check(prompt, '深夜城堡会客室，壁炉照明', '会客室'), prompt)

    def test_abandoned_hospital_can_include_environmental_medicine_props(self):
        prompt = '推车层板上散落着几个破碎的玻璃药瓶和一张病历单。'
        self.assertEqual(self.check(prompt, '废弃医院，推车生锈', '医院走廊'), prompt)

    def test_source_requested_tabletop_items_are_preserved(self):
        prompt = '茶几上摆放翻开的杂志和一杯水。'
        self.assertEqual(self.check(prompt, '普通客厅，茶几上有杂志和水杯', '客厅'), prompt)
        prompt = '桌面上散落着羊皮卷。'
        self.assertEqual(self.check(prompt, '桌面散落羊皮卷', '书房'), prompt)

    def test_fixed_books_garment_display_and_decor_are_preserved(self):
        for prompt in ('书架上按序陈列藏书，墙上挂着油画。',
                       '服装店橱窗陈列外套，衣帽架排列整齐。',
                       '玻璃柜里陈列一套水晶酒杯。',
                       '固定黄铜烛台位于壁炉两侧。'):
            with self.subTest(prompt=prompt):
                self.assertEqual(self.check(prompt), prompt)

    def test_tabletop_magazine_needs_no_individual_source_enumeration(self):
        prompt = '茶几上摆着翻开的杂志。'
        self.assertEqual(self.check(prompt, '客厅墙边有杂志架', '客厅'), prompt)

    def test_explicit_completed_action_residues_are_rejected(self):
        for prompt in ('椅背上搭着刚脱下的外套。', '桌上是某人刚放下的杯子。',
                       '茶几上留着刚喝过的水杯。', '桌面有主人拿过的书信。',
                       '地毯上是动作结束后留下的衣物。'):
            with self.subTest(prompt=prompt):
                self.assertIn('剧情动作残留', self.reject(prompt))

    def test_all_major_violations_are_aggregated_in_one_error(self):
        message = self.reject('一位男子坐在壁炉旁。木门漆面开裂。桌上是刚放下的杯子。具体形象待后续卡片定义。')
        for label in ('场景底图中的人物', '裂损', '剧情动作残留', '制作元指令'):
            self.assertIn(label, message)

    def test_success_does_not_rewrite_caption_or_facts(self):
        facts = {'name': '会客室', 'space': '正常使用的会客室', 'layout': '左侧木门，右侧书架'}
        before = copy.deepcopy(facts)
        prompt = '室内会客室，左侧木门，右侧书架。'
        self.assertIs(validate_scene_prompt(prompt, facts), prompt)
        self.assertEqual(facts, before)

    def test_system_and_cached_prompt_fields_do_not_authorize_damage(self):
        facts = {'name': '会客室', 'space': '普通会客室', 'style': '磨损风格',
                 'image_prompt': '墙面污渍', 'design': '模板：允许木门开裂'}
        with self.assertRaises(ScenePromptContentError):
            validate_scene_prompt('木门开裂，墙面污渍。', facts)

    def test_empty_or_non_string_caption_is_invalid(self):
        for prompt in ('', ' ', None, 12):
            with self.subTest(prompt=prompt), self.assertRaises(ScenePromptContentError):
                validate_scene_prompt(prompt, {})


class SceneConditionContentTests(unittest.TestCase):
    """Condition-aware checks share no model, service, or project fixture."""

    def check(self, prompt, source='宫殿大厅', name='宫殿大厅', condition=None):
        facts = {'name': name, 'space': source}
        return validate_scene_prompt(prompt, facts, condition=(
            build_condition(name, source) if condition is None else condition))

    def reject(self, prompt, source='宫殿大厅', name='宫殿大厅', condition=None):
        with self.assertRaises(ScenePromptContentError) as caught:
            self.check(prompt, source, name, condition)
        self.assertEqual(caught.exception.code, 'scene_prompt_content')
        return str(caught.exception)

    def test_five_user_palace_scenarios_keep_independent_dimensions(self):
        cases = (
            ('宫殿大厅', '宫殿大厅干净整洁，石柱完整，陈设整齐，空间开阔。'),
            ('宫殿大厅，气氛黑暗', '大厅光线昏暗，石柱完整，陈设整齐，空间开阔。'),
            ('宫殿大厅，柱子有烧焦痕迹', '柱子表面保留烧焦痕迹，大厅结构完整，陈设整齐。'),
            ('宫殿大厅，东西杂乱', '大厅结构完整，陈设杂乱，墙面和地面干净。'),
            ('宫殿大厅，气氛黑暗，建筑有烧焦痕迹，东西杂乱破败',
             '昏暗的大厅内，柱子表面保留烧焦痕迹，家具排列杂乱，桌子表面磨损，椅子表层剥落。'),
        )
        for source, prompt in cases:
            with self.subTest(source=source):
                self.assertIs(self.check(prompt, source), prompt)

    def test_default_and_darkness_do_not_authorize_damage_or_mess(self):
        prompt = '柱子表面烧焦，地面覆盖灰尘，家具排列杂乱，屋顶坍塌。'
        for source in ('宫殿大厅', '宫殿大厅，气氛黑暗'):
            with self.subTest(source=source):
                message = self.reject(prompt, source)
                for label in ('烧焦痕迹', '积灰', '陈设杂乱', '坍塌损毁'):
                    self.assertIn(label, message)

    def test_dark_condition_rejects_bright_primary_space_and_broad_daylight(self):
        source = '宫殿大厅，气氛黑暗'
        for prompt in ('高窗透入明亮的自然光，中央通道形成明亮光斑。',
                       '整个大厅灯火通明。', '宫殿大厅充满阳光。', '中央主空间十分明亮。'):
            with self.subTest(prompt=prompt):
                self.assertIn('光照明暗矛盾', self.reject(prompt, source))

    def test_dark_condition_keeps_small_light_sources_and_local_highlights(self):
        source = '宫殿大厅，气氛黑暗'
        for prompt in ('昏暗的大厅里一盏明亮的灯火照着局部桌面。',
                       '墙面金属装饰反射明亮高光。', '窗边一束明亮的自然光照在局部地面。'):
            with self.subTest(prompt=prompt):
                self.assertIs(self.check(prompt, source), prompt)

    def test_bright_condition_rejects_whole_room_darkness(self):
        for prompt in ('整个大厅漆黑，伸手不见五指。', '大厅环境昏暗。'):
            with self.subTest(prompt=prompt):
                self.assertIn('光照明暗矛盾', self.reject(prompt, '宫殿大厅灯火通明'))

    def test_bright_condition_keeps_shadows_and_dark_corners(self):
        prompt = '明亮的大厅里，柱后阴影浓重，室内角落较为昏暗。'
        self.assertIs(self.check(prompt, '宫殿大厅灯火通明'), prompt)

    def test_scorching_only_authorizes_its_surface_and_kind(self):
        source = '宫殿大厅，柱子有烧焦痕迹'
        self.check('石柱表面有焦黑痕迹。', source)
        for prompt in ('墙壁烧焦。', '木门有焦黑痕迹。', '柱子表面开裂。',
                       '柱子有锈蚀。', '地面有污渍。', '家具杂乱。', '柱子倒塌。'):
            with self.subTest(prompt=prompt):
                self.reject(prompt, source)

    def test_scorching_does_not_turn_into_an_active_fire(self):
        source = '宫殿大厅，柱子有烧焦痕迹'
        for prompt in ('柱子正在燃烧。', '大火吞噬大厅。', '大厅陷入火海。'):
            with self.subTest(prompt=prompt):
                self.assertIn('正在着火', self.reject(prompt, source))
        self.check('壁炉内火焰跳动，为完整的大厅提供暖光。柱子表面有烧焦痕迹。', source)

    def test_explicit_active_fire_is_preserved(self):
        self.check('宫殿大厅正在燃烧。', '宫殿大厅正在燃烧')

    def test_mess_is_arrangement_only(self):
        source = '宫殿大厅，东西杂乱'
        self.check('桌面摆放杂乱的书卷，椅子排列凌乱。', source)
        for prompt in ('家具表面磨损。', '桌面覆盖灰尘。', '墙面污渍清楚。', '屋顶坍塌。'):
            with self.subTest(prompt=prompt):
                self.reject(prompt, source)

    def test_messy_condition_requires_visible_arrangement(self):
        source = '宫殿大厅，东西杂乱'
        self.assertIn('缺少指定陈设状态', self.reject('大厅整体整洁，桌椅排列整齐。', source))
        for prompt in ('桌椅错位摆放。', '陈设物品散置在桌边。', '家具排列杂乱。'):
            with self.subTest(prompt=prompt):
                self.check(prompt, source)

    def test_beam_scorch_can_inherit_same_sentence_building_subject(self):
        prompt = '立柱支撑木构梁架，梁架结构清晰可见，保留着烧焦的黑色痕迹。'
        self.check(prompt, '宫殿大厅，横梁有烧焦痕迹')

    def test_inherited_effect_cannot_cross_sentence_or_a_new_subject(self):
        source = '宫殿大厅，横梁有烧焦痕迹'
        for prompt in ('梁架结构清晰。保留着烧焦痕迹。', '梁架结构清晰；保留着烧焦痕迹。',
                       '梁架结构清晰，墙壁保留烧焦痕迹。', '梁架结构清晰，门框保留烧焦痕迹。',
                       '梁架结构清晰，挂毯保留烧焦痕迹。'):
            with self.subTest(prompt=prompt):
                self.reject(prompt, source)

    def test_dilapidated_props_allow_local_wear_but_not_building_damage(self):
        source = '宫殿大厅，气氛黑暗，建筑有烧焦痕迹，东西杂乱破败'
        for prompt in ('木椅表层剥落。', '桌面有裂纹。', '家具表面磨损。', '挂毯边缘磨损。'):
            with self.subTest(prompt=prompt):
                self.check(prompt + '陈设物品排列杂乱。柱子表面保留烧焦痕迹。', source)
        for prompt in ('屋顶坍塌。', '墙壁开裂。', '家具烧焦。', '地面覆盖灰尘。', '大火吞噬大厅。'):
            with self.subTest(prompt=prompt):
                self.reject(prompt, source)

    def test_general_dilapidation_does_not_authorize_every_kind_of_damage(self):
        source = '宫殿大厅破败'
        self.check('墙面表层剥落，地板局部磨损，木门有裂纹。', source)
        for prompt in ('屋顶坍塌。', '建筑化为废墟。', '柱子烧焦。', '金属烛台锈蚀。'):
            with self.subTest(prompt=prompt):
                self.reject(prompt, source)
        self.reject('墙面有污渍，柱子烧焦。', '宫殿大厅废弃')

    def test_explicit_collapse_remains_valid_but_damage_is_not_collapse(self):
        self.check('大厅屋顶坍塌，建筑化为废墟。', '宫殿大厅屋顶坍塌')
        self.reject('屋顶坍塌。', '宫殿大厅建筑损坏')

    def test_specific_effects_do_not_spread_between_parts_or_directions(self):
        source = '宫殿大厅，北侧木门有烧焦痕迹，门框有裂纹'
        self.check('北边木门表面焦黑，门框留有裂纹。', source)
        for prompt in ('南侧木门烧焦。', '木门有裂纹。', '门框烧焦。',
                       '北侧木门和南侧木门均有烧焦痕迹。', '墙面和北侧木门均有烧焦痕迹。'):
            with self.subTest(prompt=prompt):
                self.reject(prompt, source)

    def test_building_effect_does_not_authorize_furniture(self):
        source = '宫殿大厅，建筑有烧焦痕迹'
        self.check('墙面与柱子有烧焦痕迹。', source)
        self.reject('挂毯有烧焦痕迹。', source)

    def test_one_known_target_does_not_authorize_another_target_in_same_clause(self):
        self.reject('柱子和墙壁有烧焦痕迹。', '宫殿大厅，柱子有烧焦痕迹')
        self.reject('烧焦的柱子和墙壁清晰。', '宫殿大厅，柱子有烧焦痕迹')

    def test_multiple_scenes_do_not_share_damage_authority(self):
        story = {'settings': {'initial_story_input': '宫殿大厅柱子有烧焦痕迹，随后进入干净的书房。'}}
        names = ('宫殿大厅', '书房')
        hall = condition_for_scene(story, {'name': names[0]}, scene_names=names)
        study = condition_for_scene(story, {'name': names[1]}, scene_names=names)
        self.check('柱子表面有烧焦痕迹。', condition=hall)
        self.reject('柱子表面有烧焦痕迹。', name='书房', condition=study)

    def test_facts_or_cached_prose_cannot_expand_condition_authority(self):
        facts = {'name': '宫殿大厅', 'space': '墙面烧焦，废弃多年，屋顶坍塌',
                 'design': '家具杂乱，墙壁开裂', 'image_prompt': '墙面烧焦'}
        condition = build_condition('宫殿大厅', '宫殿大厅，气氛黑暗')
        with self.assertRaises(ScenePromptContentError):
            validate_scene_prompt('墙面烧焦，家具杂乱，屋顶坍塌。', facts, condition=condition)

    def test_plain_set_art_and_normal_environment_details_survive(self):
        prompt = ('茶几上摆放翻开的杂志和一杯水，桌面散落着羊皮卷。'
                  '墙上油画中的人物肖像清晰，壁炉旁有一尊男子雕像。'
                  '木纹自然，斑驳阳光落在地面，空气中微尘随光束漂浮。')
        self.assertIs(self.check(prompt), prompt)

    def test_natural_valley_and_compact_cabin_survive(self):
        self.check('溪边石缝覆盖青苔，山谷开阔，岩石纹理粗糙。', '自然山谷', '自然山谷')
        self.check('船舱狭小紧凑，桌椅排列整齐，木质墙面完整。', '狭小船舱', '船舱')

    def test_all_independent_violations_are_aggregated(self):
        prompt = ('一位男子坐在椅子上，墙面烧焦，家具杂乱，屋顶坍塌。'
                  '桌上是刚放下的杯子，具体形象待后续卡片定义。')
        message = self.reject(prompt, '宫殿大厅，气氛黑暗')
        for label in ('场景底图中的人物', '烧焦痕迹', '陈设杂乱', '坍塌损毁', '剧情动作残留', '制作元指令'):
            self.assertIn(label, message)

    def test_condition_validation_does_not_change_inputs(self):
        condition = build_condition('宫殿大厅', '宫殿大厅，柱子有烧焦痕迹')
        before = copy.deepcopy(condition)
        prompt = '柱子有烧焦痕迹，大厅结构完整，陈设整齐。'
        self.assertIs(self.check(prompt, condition=condition), prompt)
        self.assertEqual(condition, before)

    def test_explicit_surface_kind_and_target_must_be_visible(self):
        source = '宫殿大厅，北侧木门有烧焦痕迹，门框有裂纹'
        for prompt in ('北侧木门完整，门框留有裂纹。', '南侧木门烧焦，门框留有裂纹。',
                       '北侧木门没有烧焦，门框留有裂纹。', '北侧木门烧焦，门框完整。'):
            with self.subTest(prompt=prompt):
                self.assertIn('缺少指定表面状态', self.reject(prompt, source))
        self.check('北侧木门保留烧焦痕迹，门框留有裂纹。', source)
        self.check('北侧木门和南侧木门均保留烧焦痕迹。',
                   '宫殿大厅，北侧木门有烧焦痕迹，南侧木门有烧焦痕迹')

    def test_derived_dilapidation_needs_one_old_surface_instead_of_all_kinds(self):
        source = '宫殿大厅，东西杂乱破败'
        self.assertIn('缺少指定表面状态', self.reject('家具排列杂乱，表面光洁。', source))
        for prompt in ('家具排列杂乱，椅子表面磨损。', '家具排列杂乱，桌面留有裂纹。',
                       '家具排列杂乱，挂毯边缘剥落。'):
            with self.subTest(prompt=prompt):
                self.check(prompt, source)

    def test_missing_surface_groups_and_order_are_aggregated(self):
        source = '宫殿大厅，建筑有烧焦痕迹，东西杂乱破败'
        message = self.reject('大厅整洁，家具排列整齐。', source)
        self.assertIn('缺少指定陈设状态', message)
        self.assertIn('缺少指定表面状态「建筑烧焦痕迹」', message)
        self.assertIn('缺少指定表面状态「陈设物品局部旧损」', message)
        self.check('柱子留有烧焦痕迹，家具排列杂乱，桌面表层剥落。', source)

    def test_prop_subject_can_continue_but_stops_at_new_subject_or_sentence(self):
        source = '宫殿大厅，建筑有烧焦痕迹，东西杂乱破败'
        self.check('梁架保留烧焦痕迹。陈设物品错位散置，保留着旧损与岁月痕迹。', source)
        for prompt in ('梁架保留烧焦痕迹。陈设物品错位散置。保留着旧损与岁月痕迹。',
                       '梁架保留烧焦痕迹。陈设物品错位散置，墙面保留着旧损与岁月痕迹。'):
            with self.subTest(prompt=prompt):
                self.reject(prompt, source)

    def test_scorch_allows_only_local_minor_coating_flakes_and_fine_cracks(self):
        source = '宫殿大厅，柱子有烧焦痕迹'
        self.check('柱子漆层局部剥落，可见细微裂纹，柱子保留烧焦痕迹。', source)
        for detail in ('墙面漆层局部剥落', '门框留有细小裂纹', '柱子严重剥落',
                       '柱子积灰', '柱子生锈', '柱子长有青苔', '屋顶坍塌'):
            with self.subTest(detail=detail):
                self.reject('柱子保留烧焦痕迹。' + detail + '。', source)
        self.reject('柱子漆层局部剥落，可见细微裂纹。')


if __name__ == '__main__':
    unittest.main()
