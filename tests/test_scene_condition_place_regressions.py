"""Pure place-noun extraction regressions, including the retained STORY_071 source."""
import copy
import json
from pathlib import Path
import unittest

from cores.scene_condition import condition_for_scene, positive_condition, project_condition_facts
from cores.scene_prompt_content import validate_scene_prompt


STORY_PATH = Path('D:/AI视频工作台/data/stories/STORY_071.json')


class PlaceIdentityRegressions(unittest.TestCase):
    def condition(self, source, name, names=()):
        return condition_for_scene({'settings': {'initial_story_input': source}},
                                   {'name': name, 'auto_made': True}, scene_names=names)

    def test_obligation_sentence_does_not_become_named_place(self):
        source = '暴雨中，三人必须把藏印阁内的玉匣交给渡舟使。'
        condition = self.condition(source, '藏印阁', ('藏印阁', '有顶回廊', '观云渡台'))
        self.assertEqual(condition['place_identity'], '藏印阁')
        self.assertEqual(condition['weather']['kind'], 'rain')
        self.assertNotIn('三人必须把', positive_condition(condition))

    def test_named_place_keeps_identity_across_actions_and_repeated_mentions(self):
        for source in ('三人需要将青冥殿中的物件搬走。', '她推开藏印阁的大门，后来返回藏印阁。',
                       '顾霄准备前往观云渡台，岑月守住观云渡台。'):
            name = next(name for name in ('青冥殿', '藏印阁', '观云渡台') if name in source)
            with self.subTest(source=source):
                self.assertEqual(self.condition(source, name)['place_identity'], name)

    def test_generic_room_does_not_capture_actor_or_obligation(self):
        for source in ('三人必须把库房里的盒子带走。', '陆衡需要将大厅里的木箱搬走。',
                       '弟子负责保护仓库，随后进去。'):
            name = next(name for name in ('库房', '大厅', '仓库') if name in source)
            with self.subTest(source=source):
                self.assertEqual(self.condition(source, name)['place_identity'], name)

    def test_source_backed_professional_and_sect_qualifiers_remain(self):
        for source, name, expected in (
                ('研究员进入无菌实验室。', '实验室', '无菌实验室'),
                ('弟子进入青云宗议事大殿。', '大殿', '青云宗议事大殿'),
                ('掌门让弟子在宗门大殿会合。', '大殿', '宗门大殿'),
                ('暴雨持续，人物从胶片库房走向走廊。', '库房', '胶片库房'),
                ('人物来到有顶装卸台。', '装卸台', '有顶装卸台')):
            with self.subTest(expected=expected):
                self.assertEqual(self.condition(source, name)['place_identity'], expected)

    def test_known_professional_catalog_name_is_preferred_to_a_story_prefix(self):
        condition = self.condition('三人必须把胶片库房内的盒子带走。', '库房', ('胶片库房', '走廊'))
        self.assertEqual(condition['place_identity'], '胶片库房')

    def test_exact_known_scene_id_can_supply_a_missing_label_without_mutation(self):
        story = {'one_line': '暴雨持续，三人必须把藏印阁内的玉匣送走。'}
        card = {'scene_id': 'SCENE_藏印阁', 'auto_made': True}
        before = copy.deepcopy((story, card))
        condition = condition_for_scene(story, card, scene_names=('藏印阁', '有顶回廊'))
        self.assertEqual(condition['place_identity'], '藏印阁')
        self.assertEqual((story, card), before)
        unknown = condition_for_scene(story, {'scene_id': 'SCENE_任意动作句'}, scene_names=('藏印阁',))
        self.assertEqual(unknown['place_identity'], '')

    def test_existing_explicit_name_takes_priority_over_a_different_id(self):
        condition = condition_for_scene({'one_line': '暴雨中经过有顶回廊。'},
                                        {'name': '有顶回廊', 'scene_id': 'SCENE_藏印阁'},
                                        scene_names=('藏印阁', '有顶回廊'))
        self.assertEqual(condition['place_identity'], '有顶回廊')

    def test_place_projection_keeps_interior_and_original_weather(self):
        condition = self.condition('暴雨中，三人必须把藏印阁内的玉匣送走。', '藏印阁')
        facts = {'name': '藏印阁', 'interior': '室内', 'space': '整洁完整的木构楼阁。'}
        before = copy.deepcopy(facts)
        projected = project_condition_facts(facts, condition)
        self.assertEqual(projected['name'], '藏印阁')
        self.assertEqual(projected['interior'], '室内')
        self.assertEqual(condition['weather']['kind'], 'rain')
        self.assertEqual(facts, before)

    def test_descriptive_darkness_is_not_lost_when_named_site_stays_exact(self):
        condition = self.condition('昏暗的藏印阁内窗外暴雨持续。', '藏印阁')
        self.assertEqual(condition['place_identity'], '藏印阁')
        self.assertEqual(condition['lighting'], 'dark')
        self.assertEqual(condition['weather']['kind'], 'rain')

    def test_later_film_wide_windows_do_not_become_landing_stage_doors(self):
        source = ('三人从藏印阁经有顶回廊到观云渡台。\n'
                  '暴雨贯穿全片；有顶室内与回廊维持正常状态，屋外雨势通过门窗和檐边呈现。')
        for value in (source, source.replace('\n', '')):
            with self.subTest(single_line='\n' not in value):
                condition = self.condition(value, '观云渡台', ('藏印阁', '有顶回廊', '观云渡台'))
                self.assertNotIn('门', condition['fixed_facts'])
                self.assertNotIn('有顶遮雨结构', condition['fixed_facts'])
                self.assertEqual(condition['weather']['kind'], 'rain')
        corridor = self.condition(source, '有顶回廊', ('藏印阁', '有顶回廊', '观云渡台'))
        self.assertIn('有顶遮雨结构', corridor['fixed_facts'])

    def test_sect_family_and_disciples_are_not_building_doors(self):
        condition = self.condition('宗门的师门长辈让同门与门下弟子在观云渡台等候。', '观云渡台')
        self.assertNotIn('门', condition['fixed_facts'])

    def test_real_local_door_and_frame_remain_required(self):
        condition = self.condition('观云渡台北侧设一扇木门，门框完整。', '观云渡台')
        self.assertIn('木门', condition['fixed_facts'])
        self.assertIn('门框', condition['fixed_facts'])

    def test_another_named_scene_door_does_not_leak_across_paragraphs(self):
        source = '藏印阁有一扇玻璃门。\n观云渡台的台阶完整。'
        names = ('藏印阁', '观云渡台')
        stage = self.condition(source, '观云渡台', names)
        self.assertNotIn('门', stage['fixed_facts'])
        self.assertNotIn('玻璃门', stage['fixed_facts'])
        self.assertIn('台阶', stage['fixed_facts'])
        self.assertIn('玻璃门', self.condition(source, '藏印阁', names)['fixed_facts'])

    def test_manual_card_structures_remain_authoritative_without_source_anchor(self):
        condition = condition_for_scene({}, {'name': '观云渡台', 'user_made': True,
                                             'space': '北侧木门完整，门框坚实。'})
        self.assertIn('北侧木门', condition['fixed_facts'])
        self.assertIn('门框', condition['fixed_facts'])


@unittest.skipUnless(STORY_PATH.is_file(), 'retained real source project unavailable')
class RealStory071PlaceRegression(unittest.TestCase):
    def test_real_source_all_explicit_places_stay_nouns_and_rain_survives(self):
        story = json.loads(STORY_PATH.read_text(encoding='utf-8'))
        before = copy.deepcopy(story)
        names = ('藏印阁', '有顶回廊', '观云渡台', '玄舟')
        for name in names:
            with self.subTest(name=name):
                condition = condition_for_scene(story, {'name': name, 'scene_id': 'SCENE_' + name},
                                                scene_names=names)
                self.assertEqual(condition['place_identity'], name)
                self.assertEqual(condition['weather']['kind'], 'rain')
                self.assertNotRegex(positive_condition(condition), r'地点为[^；。]*(?:三人|必须|交给|才能)')
                if name == '观云渡台':
                    self.assertNotIn('门', condition['fixed_facts'])
                    self.assertNotIn('有顶遮雨结构', condition['fixed_facts'])
        # The real source used to require the impossible literal obligation
        # sentence. A simple correct scene can now satisfy that same contract.
        condition = condition_for_scene(story, {'name': '藏印阁'}, scene_names=names)
        valid = '藏印阁木构主体完整，木柜、大门、门窗与檐边结构清楚，室内整洁，室外暴雨持续。'
        validate_scene_prompt(valid, {'name': '藏印阁'}, condition=condition)
        self.assertEqual(story, before)


if __name__ == '__main__':
    unittest.main()
