"""CPU-only source identity/weather and positive scene wording regressions."""
import copy
import unittest

from cores.image_prompt_content import normalize_positive_description
from cores.scene_condition import condition_for_scene, positive_condition, weather_state
from cores.scene_prompt_content import ScenePromptContentError, validate_scene_prompt


class SceneSourceContractTests(unittest.TestCase):
    def condition(self, text, name='库房', names=('库房', '走廊', '庭院')):
        return condition_for_scene({'settings': {'initial_story_input': text}},
                                   {'name': name, 'auto_made': True}, scene_names=names)

    def test_global_weather_reaches_connected_scene_with_no_local_weather(self):
        c = self.condition('暴雨持续。人物从胶片库房经走廊走到庭院。')
        self.assertEqual(c['weather']['kind'], 'rain')
        self.assertEqual(c['weather']['description'], '暴雨天气')
        self.assertEqual(c['place_identity'], '胶片库房')
        self.assertIn('室外为暴雨天气', positive_condition(c))
        self.assertIn('地点为胶片库房', positive_condition(c))

    def test_explicit_local_weather_change_and_unknown_defaults(self):
        c = self.condition('暴雨中穿过走廊。雨停后进入庭院。', '庭院')
        self.assertEqual(c['weather']['kind'], 'clear')
        self.assertEqual(c['weather']['description'], '降雨已经停止')
        self.assertEqual(self.condition('人物从库房走到庭院。')['weather'], {})
        self.assertEqual(weather_state('暴雨过后放晴'), {})
        self.assertEqual(weather_state('雨衣挂在雨棚内'), {})

    def test_professional_place_and_fixed_structure_are_not_lost(self):
        c = self.condition('暴雨持续。胶片库房有一扇玻璃门。')
        self.assertIn('玻璃门', c['fixed_facts'])
        validate_scene_prompt('胶片库房，玻璃门完整，窗外暴雨。', {}, condition=c)
        for bad in ('工业库房，玻璃门完整，窗外暴雨。',
                    '胶片库房，窗外暴雨。',
                    '胶片库房，玻璃门完整，雨后初晴。'):
            with self.subTest(bad=bad), self.assertRaises(ScenePromptContentError):
                validate_scene_prompt(bad, {}, condition=c)

    def test_specialized_place_uses_original_words_across_different_genres(self):
        c = self.condition('弟子进入青云宗议事大殿。', '大殿', ('大殿', '山门'))
        self.assertEqual(c['place_identity'], '青云宗议事大殿')
        c = self.condition('研究员进入无菌实验室。', '实验室', ('实验室', '走廊'))
        self.assertEqual(c['place_identity'], '无菌实验室')

    def test_sheltered_loading_area_retains_roof_without_freezing_carried_umbrella(self):
        c = self.condition('暴雨持续，人物来到有顶、能够遮雨的装卸台。', '装卸台', ('装卸台', '走廊'))
        self.assertIn('有顶遮雨结构', c['fixed_facts'])
        validate_scene_prompt('装卸台上方雨棚完整，室外暴雨，通道开阔。', {}, condition=c)
        with self.assertRaises(ScenePromptContentError):
            validate_scene_prompt('装卸台，室外暴雨，地面整洁。', {}, condition=c)

    def test_negative_tidy_fact_is_not_misread_as_actual_disorder(self):
        c = self.condition('库房干净整洁。')
        for text in ('库房无杂乱陈设。', '库房没有杂物堆积。', '库房没有任何杂乱。'):
            with self.subTest(text=text):
                self.assertEqual(validate_scene_prompt(text, {}, condition=c), text)
        with self.assertRaises(ScenePromptContentError):
            validate_scene_prompt('库房并非杂乱，而是墙面生锈。', {}, condition=c)
        messy = self.condition('库房东西杂乱。')
        with self.assertRaises(ScenePromptContentError):
            validate_scene_prompt('库房无杂乱陈设。', {}, condition=messy)

    def test_positive_conversion_keeps_semantics_and_literal_sign_text(self):
        for negative in ('无杂乱陈设', '没有杂物堆积', '无散落的杂物'):
            with self.subTest(negative=negative):
                self.assertEqual(normalize_positive_description(negative), '陈设井然有序')
        literal = '标牌写着“无杂乱陈设”，一旁无杂物堆积。'
        self.assertEqual(normalize_positive_description(literal),
                         '标牌写着“无杂乱陈设”，一旁陈设井然有序。')

    def test_source_resolution_is_pure(self):
        story = {'one_line': '暴雨中的青云宗议事大殿，北侧木门完整。'}
        card = {'name': '大殿', 'space': '自动写出的雨后初晴的工业大厅'}
        before = copy.deepcopy((story, card))
        c = condition_for_scene(story, card)
        self.assertEqual((story, card), before)
        self.assertEqual(c['weather']['kind'], 'rain')
        self.assertNotIn('工业', c['place_identity'])

    def test_sect_and_master_are_not_invented_architectural_doors(self):
        c = self.condition('掌门让弟子在宗门大殿会合。', '大殿', ('大殿', '庭院'))
        self.assertEqual(c['place_identity'], '宗门大殿')
        self.assertNotIn('门', c['fixed_facts'])

    def test_real_scene_extra_furnishing_exclusion_becomes_positive(self):
        for phrase in ('无多余陈设', '没有多余陈设'):
            source = '场景内' + phrase + '，仅见必要的排水沟槽与完整雨棚。'
            self.assertEqual(normalize_positive_description(source),
                             '场景内陈设精简有序，仅见必要的排水沟槽与完整雨棚。')
        literal = '标牌写着“无多余陈设”，场景内没有多余陈设。'
        self.assertEqual(normalize_positive_description(literal),
                         '标牌写着“无多余陈设”，场景内陈设精简有序。')


if __name__ == '__main__':
    unittest.main()
