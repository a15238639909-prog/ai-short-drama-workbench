import unittest

from cores import source_shot_compiler as compiler, source_wardrobe_plan as wardrobe


class SourcePlanPropertyTests(unittest.TestCase):
    def test_initial_xianxia_colours_and_wrap_word_are_preserved(self):
        cards = [dict(character_id='a', name='岑月', clothing='玄青外袍、月白中衣、靛青长裤、黑布靴')]
        plan = wardrobe.build_start_plan([dict(text='岑月脱下玄青外袍，把玉匣裹好。'),
                                           dict(text='岑月走向回廊。')], 1, cards)
        self.assertEqual(plan['characters']['a']['worn'], ['月白中衣', '靛青长裤', '黑布靴'])
        self.assertEqual(plan['props'][0]['item'], '玄青外袍')
        self.assertEqual(plan['props'][0]['object'], '玉匣')

    def test_wrapper_is_not_rebound_to_another_robe_wearer(self):
        cards = [dict(character_id='a', name='岑月', clothing='青色外袍、月白中衣'),
                 dict(character_id='b', name='陆衡', clothing='绿色外袍、白色中衣')]
        slices = [dict(text='岑月脱下青色外袍，将玉匣包好。'), dict(text='岑月走向陆衡。')]
        plan = wardrobe.build_start_plan(slices, 1, cards)
        refs = [dict(character_id='a', name='岑月', subject=1, role='character'),
                dict(character_id='b', name='陆衡', subject=2, role='character')]
        ownership = compiler._clothing_ownership(dict(source_wardrobe_plan=plan, canonical_references=refs))
        text = '包裹玉匣的青色外袍纹理清楚。'
        self.assertEqual(compiler._garment_owner_text(text, ownership, {'岑月', '陆衡'}, '', prop_plan=plan), text)
        with self.assertRaises(ValueError):
            compiler._planned_garment_text('岑月的外套下摆轻轻摆动。', plan)
        self.assertEqual(compiler._planned_garment_text('陆衡的外套下摆纹理清楚。', plan),
                         '陆衡的外套下摆纹理清楚。')

    def test_source_changes_are_not_overridden_by_a_stripped_old_slot(self):
        cards = [dict(character_id='a', name='岑月', clothing='青色外袍、月白中衣')]
        state = dict(policy='confirmed-wardrobe-state-v1', last_segment=1,
                     characters={'a': dict(name='岑月', baseline_design='青色外袍、月白中衣', slots={
                         'outerwear': dict(status='worn', item='青色外袍', segment=1)})})
        plan = wardrobe.build_start_plan([dict(text='岑月脱下青色外袍。'), dict(text='岑月走到门边。')],
                                         1, cards, observed_state=state)
        self.assertEqual(plan['characters']['a']['worn'], ['月白中衣'])


if __name__ == '__main__':
    unittest.main()
