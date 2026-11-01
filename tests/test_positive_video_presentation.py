"""Final H3 presentation contains visible facts, while state stays internal."""
import copy
import json
import unittest

from cores import wardrobe_continuity as wardrobe
from cores import story_video_reference_prompts as reference


class PositiveVideoPresentationTests(unittest.TestCase):
    def setUp(self):
        self.bindings = [dict(character_id='a', name='林岚', role='character', subject=1, picture=1),
                         dict(name='雨夜旧街', role='scene_anchor', picture=2)]
        self.state = wardrobe.initial_state([dict(character_id='a', name='林岚',
            clothing='灰色风衣、白色衬衫、深色长裤、黑色短靴、银色腕表')])
        self.fields = dict(subject_definitions='<Subject 1> is 林岚, the character from <Picture 1>. '
            '人物外观完整沿用对应人设图；当前姿势、表情、持物和位置服从故事及实际状态。\n'
            '<Picture 2>提供雨夜旧街的空间。', summary='林岚走向车门。',
            retention_analysis='<Subject 1>：保留人设身份，不因人设图自动恢复已脱下或已更换的衣物。\n'
            '人物身份、脸、发型、发色、肤色和体型按各自人设图。\n'
            + wardrobe.MARKER + ' ' + wardrobe.authority_rules() + '\nCurrent wardrobe contract: {"characters": []}',
            detailed_description='[Shot 1] 林岚停在车门旁，说：<d>[Chinese] 不要走。</d>',
            overall_soundscape='雨点落在车顶。', non_diegetic_music='N/A')

    def prompt(self, state=None):
        source = '\n\n'.join(key + ':\n' + self.fields[key] for key in wardrobe.FIELDS)
        return wardrobe.apply_prompt(source, self.state if state is None else state, self.bindings)

    def slot(self, status, item, **kwargs):
        return dict(status=status, item=item, holder='unknown', location='unknown', **kwargs)

    def test_baseline_is_concrete_and_internal_rules_are_not_transmitted(self):
        result = self.prompt()
        self.assertIn('当前衣着为灰色风衣、白色衬衫、深色长裤、黑色短靴、银色腕表', result)
        for value in (wardrobe.MARKER, 'Current wardrobe contract', 'wardrobe_state',
                      'baseline_design', 'unknown', '不能', '不因', '不得'):
            self.assertNotIn(value, result)
        self.assertIn('<Picture 2>', result)
        self.assertIn('<d>[Chinese] 不要走。</d>', result)
        self.assertIn('wardrobe_state', wardrobe.authority_rules())

    def test_removed_coat_preserves_remaining_clothing_without_reintroducing_it(self):
        self.state['characters']['a']['slots']['outerwear'] = self.slot('removed', '灰色风衣')
        result = self.prompt()
        self.assertNotIn('灰色风衣', result)
        for item in ('白色衬衫', '深色长裤', '黑色短靴', '银色腕表'):
            self.assertIn(item, result)
        self.assertNotIn('裸', result)

    def test_known_removed_item_position_is_presented_as_a_visible_prop(self):
        self.state['characters']['a']['slots']['outerwear'] = dict(status='removed', item='灰色风衣',
            holder='a', location='右手')
        result = self.prompt()
        self.assertIn('林岚的右手拿着灰色风衣', result)
        self.assertNotIn('当前衣着为灰色风衣', result)
        self.assertNotIn('removed', result)

    def test_replacement_and_damage_describe_current_garments(self):
        person = self.state['characters']['a']
        person['outfit_replaced'] = True
        person['slots'] = dict(outfit=self.slot('worn', '蓝色工作连体服'),
                               shoes=self.slot('damaged', '黑色短靴'))
        result = self.prompt()
        self.assertIn('蓝色工作连体服', result)
        self.assertIn('黑色短靴（可见破损）', result)
        self.assertNotIn('白色衬衫', result)
        self.assertNotIn('灰色风衣', result)

    def test_hat_removal_does_not_remove_a_confirmed_watch(self):
        self.state['characters']['a']['baseline_design'] += '、灰色帽子'
        self.state['characters']['a']['slots']['accessory'] = self.slot('removed', '灰色帽子')
        result = self.prompt()
        self.assertNotIn('灰色帽子', result)
        self.assertIn('银色腕表', result)

    def test_multicolour_garment_is_not_split_into_a_loose_colour(self):
        person = self.state['characters']['a']
        person['baseline_design'] = '蓝色和白色相间的外套、黑色长裤'
        person['slots']['outerwear'] = self.slot('removed', '蓝白外套')
        result = self.prompt()
        self.assertIn('当前衣着为黑色长裤', result)
        self.assertNotIn('蓝色', result)
        self.assertNotIn('白色', result)

    def test_presentation_is_idempotent_and_does_not_mutate_evidence_or_dialogue(self):
        before = copy.deepcopy(self.state)
        result = self.prompt()
        self.assertEqual(wardrobe.apply_prompt(result, self.state, self.bindings), result)
        self.assertEqual(self.state, before)
        fields = wardrobe._fields(result)
        self.assertEqual(fields['detailed_description'], self.fields['detailed_description'])
        self.assertEqual(fields['overall_soundscape'], self.fields['overall_soundscape'])

    def test_all_reference_modes_present_positive_roles(self):
        refs = self.bindings + [dict(role='previous_frame', picture=3)]
        for policy in (None, wardrobe.POLICY):
            for optimized in (False, True):
                definitions, retention = reference._reference_fields(refs,
                    flow_optimization=optimized, reference_visual_policy=reference.REFERENCE_VISUAL_POLICY,
                    scene_reference_provided=True, wardrobe_policy=policy)
                for bad in ('不提供', '不能', '不把', '不保证', '未提供', '不重启', 'wardrobe_state'):
                    self.assertNotIn(bad, definitions + retention)
                self.assertIn('<Picture 3>', definitions + retention)

    def test_optional_tail_reference_has_only_its_positive_visual_role(self):
        result = wardrobe.attach_reference(self.prompt(), 3)
        self.assertIn('<Picture 3>', result)
        self.assertIn('图中可见衣物提供当前穿戴细节', result)
        for bad in ('cannot', 'Do not', 'unknown', 'current confirmed wardrobe contract', 'not an identity'):
            self.assertNotIn(bad, result)

    def test_legacy_reference_explanations_become_compact_visible_roles(self):
        self.fields['subject_definitions'] = (
            '<Subject 1> is 林岚, the character from <Picture 1>. Reference scope: face only; '
            'it must not restore an old outfit. Voice reference: 清晰沉稳的成年女声.\n'
            '<Subject 2> is 雨夜旧街, the environment from <Picture 2>. '
            'Movable props are not fixed content.')
        result = self.prompt()
        self.assertIn('音色为清晰沉稳的成年女声', result)
        self.assertNotIn('must not', result)
        self.assertNotIn('are not', result)
        self.assertIn('the environment from <Picture 2>', result)
        self.assertEqual(wardrobe.apply_prompt(result, self.state, self.bindings), result)


if __name__ == '__main__':
    unittest.main()
