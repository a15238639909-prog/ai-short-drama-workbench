"""Source-only planned wardrobe; no models, services or project writes."""
import copy
import json
import unittest

from cores import source_wardrobe_plan as planner
from cores import wardrobe_continuity as wardrobe


class SourceWardrobePlanTests(unittest.TestCase):
    def setUp(self):
        self.chars = [
            dict(character_id='lin', name='林岚', role='character', subject=1,
                 clothing='深灰外套、白衬衫、黑长裤、黑皮鞋'),
            dict(character_id='zhou', name='周远', role='character', subject=2,
                 clothing='深灰外套、灰衬衫、黑长裤'),
            dict(character_id='chen', name='陈川', role='character', subject=3,
                 clothing='深绿雨衣、黑长裤'),
        ]

    def plan(self, *texts, index=None, chars=None, observed=None):
        slices = [dict(text=text) for text in texts]
        return planner.build_start_plan(slices, len(slices) - 1 if index is None else index,
                                        self.chars if chars is None else chars, observed_state=observed)

    def test_first_opening_and_current_changes_do_not_happen_early(self):
        plan = self.plan('林岚脱下深灰外套，包住胶片盒。', index=0)
        self.assertIn('深灰外套', plan['characters']['lin']['worn'])
        self.assertFalse(plan['characters']['lin']['changed'])
        plan = self.plan('林岚脱下深灰外套。', '林岚穿上蓝色夹克。')
        self.assertNotIn('深灰外套', plan['characters']['lin']['worn'])
        self.assertNotIn('蓝色夹克', plan['characters']['lin']['worn'])

    def test_remove_coat_preserves_inner_layers_and_other_same_coat_owner(self):
        plan = self.plan('林岚脱下深灰外套。', '林岚与周远走到门旁。')
        self.assertEqual(plan['characters']['lin']['worn'], ['白衬衫', '黑长裤', '黑皮鞋'])
        self.assertIn('深灰外套', plan['characters']['zhou']['worn'])
        self.assertTrue(plan['characters']['lin']['changed'])
        self.assertTrue(all(row['source_kind'] == 'planned_source' for row in plan['audit']))
        self.assertNotIn('actual', json.dumps(plan, ensure_ascii=False))
        self.assertNotEqual(plan.get('policy'), wardrobe.POLICY)

    def test_xianxia_outer_robe_keeps_middle_clothing_trousers_boots(self):
        chars = [dict(id='a', name='沈青', role='character', subject=1,
                      clothing='青色外袍、月白中衣、深色长裤、布靴')]
        plan = self.plan('沈青脱下青色外袍。', '沈青走到石阶旁。', chars=chars)
        self.assertEqual(plan['characters']['a']['worn'], ['月白中衣', '深色长裤', '布靴'])

    def test_explicit_put_on_and_replace_use_new_item_not_baseline(self):
        plan = self.plan('林岚脱下深灰外套。', '林岚换上蓝色夹克。', '林岚走进走廊。')
        self.assertIn('蓝色夹克', plan['characters']['lin']['worn'])
        self.assertNotIn('深灰外套', plan['characters']['lin']['worn'])
        self.assertIn('白衬衫', plan['characters']['lin']['worn'])
        plan = self.plan('林岚换上蓝色连体服。', '林岚走进走廊。')
        self.assertEqual(plan['characters']['lin']['worn'], ['黑皮鞋', '蓝色连体服'])
        chars = [dict(id='a', name='林岚', clothing='灰色大衣、白衬衫、黑长裤')]
        plan = self.plan('林岚换上蓝色夹克。', '林岚站在门旁。', chars=chars)
        self.assertEqual(plan['characters']['a']['worn'], ['白衬衫', '黑长裤', '蓝色夹克'])

    def test_wrapped_object_stays_prop_and_named_transfer_changes_holder(self):
        plan = self.plan('林岚脱下深灰外套，将胶片盒严严实实裹进衣料里。',
                         '林岚将裹着外套的胶片盒递给陈川。', '陈川抱着胶片盒走向车旁。')
        prop = plan['props'][0]
        self.assertEqual((prop['object'], prop['holder']), ('胶片盒', 'chen'))
        lines = planner.positive_clothing_lines(plan, self.chars)
        self.assertIn('胶片盒外裹着深灰外套，由<Subject 3>拿着。', lines)
        self.assertNotIn('深灰外套', lines[0])
        self.assertNotIn('深灰外套', plan['characters']['chen']['worn'])

    def test_ambiguous_handoff_resolves_only_when_named_receiver_takes_it(self):
        plan = self.plan('林岚脱下深灰外套，将胶片盒裹进衣料里。林岚将外套递过去。陈川接过。',
                         '陈川抱着胶片盒。')
        self.assertEqual(plan['props'][0]['holder'], 'chen')

    def test_original_pronoun_removal_and_cross_segment_receipt(self):
        plan = self.plan('林岚伸手接过钥匙，转身将工作台上的胶片盒捞起。'
                         '她利落地褪下深灰外套，将胶片盒严严实实裹进衣料里。',
                         '林岚将怀里的外套解下递过去。', '陈川接过，按了按厚实的布料。',
                         '陈川抱着胶片盒走向汽车。')
        self.assertNotIn('深灰外套', plan['characters']['lin']['worn'])
        self.assertEqual(plan['props'][0]['object'], '胶片盒')
        self.assertEqual(plan['props'][0]['holder'], 'chen')

    def test_source_pronoun_across_slice_and_wrap_object_before_verb(self):
        plan = self.plan('林岚从木架上取下木匣。', '她脱下外套，用外套将木匣包好。',
                         '林岚抱着木匣站在门旁。')
        self.assertNotIn('深灰外套', plan['characters']['lin']['worn'])
        self.assertEqual(plan['props'][0]['object'], '木匣')

    def test_negative_planned_quoted_dialogue_and_other_actors_do_not_remove(self):
        for source in ('林岚没有脱下外套。', '林岚准备脱下外套。', '林岚：我脱下外套。',
                       '周远说：“林岚脱下外套。”', '林岚看着周远脱下外套。', '林岚不脱下外套。'):
            with self.subTest(source=source):
                plan = self.plan(source, '林岚站在门旁。')
                self.assertIn('深灰外套', plan['characters']['lin']['worn'])

    def test_director_blocks_used_without_dialogue_and_inputs_unchanged(self):
        slices = [dict(text='林岚脱下外套。[D001]', director=dict(blocks=[[0, 2, '林岚脱下外套。'],
                      [2, 3, '[D001]']], says=[dict(dialogue_id='D001', speaker='林岚', text='我穿上外套了。')])),
                  dict(text='林岚走向窗边。')]
        original = copy.deepcopy((slices, self.chars))
        plan = planner.build_start_plan(slices, 1, self.chars)
        self.assertNotIn('深灰外套', plan['characters']['lin']['worn'])
        self.assertEqual((slices, self.chars), original)

    def test_unverified_raw_appearance_or_stripped_model_contract_cannot_override(self):
        observed = dict(characters=[dict(name='林岚', clothing='深灰外套')])
        plan = self.plan('林岚脱下外套。', '林岚走向门旁。', observed=observed)
        self.assertNotIn('深灰外套', plan['characters']['lin']['worn'])
        observed = wardrobe.initial_state(self.chars)
        observed['characters']['lin']['slots']['outerwear'] = dict(status='worn', item='深灰外套')
        plan = self.plan('林岚脱下外套。', '林岚走向门旁。', observed=observed)
        self.assertNotIn('深灰外套', plan['characters']['lin']['worn'])

    def test_verified_transition_has_priority_without_inventing_observation(self):
        observed = wardrobe.initial_state(self.chars)
        observed['characters']['lin']['slots']['outerwear'] = dict(status='worn', item='蓝色夹克',
            before='手持蓝色夹克', after='穿好蓝色夹克', evidence='第0到第24帧明确穿上',
            before_frame=0, after_frame=24, segment=1)
        snapshot = copy.deepcopy(observed)
        plan = self.plan('林岚换上蓝色夹克。', '林岚站在门旁。', observed=observed)
        self.assertIn('蓝色夹克', plan['characters']['lin']['worn'])
        self.assertEqual(plan['characters']['lin']['source_kind'], 'observed_transition')
        self.assertEqual(observed, snapshot)

    def test_compile_replaces_clothing_only_and_filters_absent_prop_holder(self):
        plan = self.plan('林岚脱下深灰外套，将胶片盒裹进衣料里。林岚将胶片盒递给陈川。',
                         '林岚站在窗旁。')
        fields = dict.fromkeys(wardrobe.FIELDS, '保留原内容')
        fields['retention_analysis'] = '<Subject 1>：面容与发型沿用人物参考。\n<Subject 1>：当前衣着为深灰外套、白衬衫。'
        compiled = dict(fields=fields, prompt='old')
        original = copy.deepcopy(compiled)
        out = planner.apply_compiled(compiled, plan, self.chars[:1])
        self.assertIn('当前衣着为白衬衫、黑长裤、黑皮鞋', out['fields']['retention_analysis'])
        self.assertNotIn('深灰外套', out['fields']['retention_analysis'])
        self.assertEqual(out['fields']['detailed_description'], '保留原内容')
        self.assertEqual(compiled, original)
        contract = planner.model_state(plan, self.chars[:1])
        self.assertEqual(set(contract['characters']), {'lin'})
        self.assertEqual(contract['props'], [])


if __name__ == '__main__':
    unittest.main()
