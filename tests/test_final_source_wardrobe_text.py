"""Final text boundary only: no models, media or project writes."""
import copy
import unittest

from api import segment_api
from cores import source_shot_compiler, wardrobe_continuity as wardrobe


class FinalSourceWardrobeTextTests(unittest.TestCase):
    def setUp(self):
        self.cards = [dict(character_id='a', name='林岚', clothing='深灰外套、白衬衫、黑长裤'),
                      dict(character_id='b', name='周远', clothing='灰衬衫、黑长裤')]
        self.bindings = [dict(self.cards[0], role='character', subject=1, picture=1),
                         dict(self.cards[1], role='character', subject=2, picture=2)]
        self.fields = dict.fromkeys(wardrobe.FIELDS, '')
        self.fields.update(subject_definitions='<Subject 1> is 林岚, the character from <Picture 1>.',
                           retention_analysis='<Subject 1>：当前衣着为深灰外套、白衬衫、黑长裤。',
                           detailed_description='[Shot 1] 林岚抱着胶片盒走向出口。',
                           overall_soundscape='雨声。', non_diegetic_music='N/A')
        self.prompt = '\n\n'.join(k + ':\n' + self.fields[k] for k in wardrobe.FIELDS)
        self.segment = dict(director_compilation=dict(source_shot_audit=dict(policy=source_shot_compiler.VERSION)))

    def apply(self, first, current, *, version='episode-source-v2', segment=None):
        slices = [dict(text=first, director=dict(source_contract_version=version)),
                  dict(text=current, director=dict(source_contract_version=version))]
        args = (self.prompt, segment or self.segment, slices, 1, self.cards, None, self.bindings)
        before = copy.deepcopy(args)
        result = segment_api._source_opening_wardrobe_prompt(*args)
        self.assertEqual(args, before)
        return result

    def test_reference_baseline_cannot_rewear_removed_coat_at_send(self):
        prompt, state = self.apply('林岚脱下深灰外套，将胶片盒裹好。', '林岚抱着胶片盒走向出口。')
        retained = wardrobe._fields(prompt)['retention_analysis']
        self.assertIn('当前衣着为白衬衫、黑长裤。', retained)
        self.assertNotIn('当前衣着为深灰外套', retained)
        self.assertIn('胶片盒外裹着深灰外套', retained)
        self.assertEqual(wardrobe._fields(prompt)['detailed_description'], self.fields['detailed_description'])
        self.assertEqual(state['evidence_kind'], 'planned_source')

    def test_current_removal_is_not_applied_to_opening(self):
        prompt, _ = self.apply('林岚走近工作台。', '林岚脱下深灰外套。')
        self.assertIn('当前衣着为深灰外套、白衬衫、黑长裤。', wardrobe._fields(prompt)['retention_analysis'])

    def test_unversioned_manual_source_is_unchanged(self):
        prompt, state = self.apply('林岚脱下深灰外套。', '林岚走开。', version=None)
        self.assertEqual(prompt, self.prompt)
        self.assertIsNone(state)

    def test_non_source_compiler_is_unchanged(self):
        prompt, state = self.apply('林岚脱下深灰外套。', '林岚走开。', segment={'director_compilation': {}})
        self.assertEqual(prompt, self.prompt)
        self.assertIsNone(state)


class FinalSourceSceneTextTests(unittest.TestCase):
    def test_continuation_replaces_only_saved_inventory_not_current_action(self):
        fields = dict.fromkeys(wardrobe.FIELDS, '')
        inventory = '临云渡台四周石栏，右侧停靠飞舟。'
        fields['detailed_description'] = '[Shot 1] ' + inventory + '飞舟驶入云层。\n[Shot 2] 两人走向回廊。'
        fields['summary'] = '临云渡台。'
        prompt = '\n\n'.join(k + ':\n' + fields[k] for k in wardrobe.FIELDS)
        segment = dict(scene='临云渡台', director_compilation=dict(source_shot_audit=dict(
            policy=source_shot_compiler.VERSION, scene_description=inventory)))
        condition = dict(structure='intact', order='tidy', spatial='open',
                         weather=dict(description='暴雨天气'), fixed_facts=['石栏'])
        result = segment_api._source_continuation_scene_prompt(prompt, segment, condition, False)
        self.assertNotIn('右侧停靠飞舟', result)
        self.assertIn('飞舟驶入云层。', result)
        self.assertIn('两人走向回廊。', result)
        self.assertIn('暴雨天气', result)
        self.assertIn('石栏', result)
        self.assertEqual(segment_api._source_continuation_scene_prompt(prompt, segment, condition, True), prompt)

    def test_unmatched_or_ambiguous_manual_inventory_is_not_guessed(self):
        fields = dict.fromkeys(wardrobe.FIELDS, '')
        fields['detailed_description'] = '[Shot 1] 湖畔有人整理缆绳。'
        prompt = '\n\n'.join(k + ':\n' + fields[k] for k in wardrobe.FIELDS)
        segment = dict(scene='湖畔', director_compilation=dict(source_shot_audit=dict(
            policy=source_shot_compiler.VERSION, scene_description='一艘小舟停在岸边。')))
        self.assertEqual(segment_api._source_continuation_scene_prompt(prompt, segment, {'structure': 'intact'}, False), prompt)


if __name__ == '__main__':
    unittest.main()
