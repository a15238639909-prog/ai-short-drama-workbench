"""Actual terminal wording, CPU-only projection with no assets or model calls."""
import copy
import unittest

from cores import source_wardrobe_plan as planner, wardrobe_continuity as wardrobe


GU_CLOTHING = '''深绿斗篷；【主导剪影】肩宽腰窄，倒三角形轮廓，充满力量感。
【核心脸部特征】方圆形脸型，下颌宽大有力，颧骨突出，眉骨高，浓眉，眼裂短小，眼神锐利，瞳孔深黑，鼻梁宽厚，唇薄，嘴角常微扬，显出自信与野性。
【专属色板】主色深绿（沉稳自然），副色褐黄（大地色），点缀色铜色（金属感）。
【唯一记忆点】左臂外侧有一道从肩头延伸至肘部的旧疤，呈浅色，在袖口下隐约可见。
【具体服装】；1. 内层：褐黄色棉麻交领短衫，面料粗糙耐磨，领口敞开。
2. 外层：深绿色半臂短褂，袖长至肘部，露出结实小臂，衣襟在右侧以铜扣固定，下摆及腰。
3. 下装：深色束脚长裤；面料厚实，裤腿塞入高筒皮靴；
4. 鞋履：黑色厚底皮靴；靴筒高至小腿中部；带有绑带。
5. 细节：腰间系宽皮带，挂有铜哨和皮囊。'''
CARDS = [dict(character_id='cen', name='岑月', subject=1, role='character',
              clothing='玄青外袍、月白窄袖中衣、靛青长裤、黑布靴'),
         dict(character_id='gu', name='顾霄', subject=2, role='character', clothing=GU_CLOTHING)]
WRAP = '岑月取出玉匣。她迅速解开玄青外袍的系带，褪下外袍。她动作利落地将玉匣仔细裹进玄青外袍中，双手捧稳。'
GIVE = ('岑月上前半步，双手将裹着外袍的玉匣平稳递出。'
        '顾霄伸手稳稳接住，指节用力确认包裹严实后，向两人微微颔首致意。岑月双手已空。')
PLACE = '顾霄转身，迈步登上悬浮的玄舟。他将玉匣轻轻放入船舱底部的特制凹槽。'


class TextOnlyWardrobeProjectionTests(unittest.TestCase):
    def plan(self, *earlier):
        slices = [dict(text=text) for text in earlier] + [dict(text='顾霄站在玉匣旁。')]
        return planner.build_start_plan(slices, len(earlier), CARDS)

    def test_clothing_design_metadata_is_removed_but_explicit_clothes_kept(self):
        pieces = wardrobe._clothing_pieces(GU_CLOTHING)
        text = '、'.join(pieces)
        for required in ('深绿斗篷', '褐黄色棉麻交领短衫', '深绿色半臂短褂', '深色束脚长裤', '黑色厚底皮靴', '宽皮带'):
            self.assertIn(required, text)
        for metadata in ('主导剪影', '核心脸部特征', '专属色板', '唯一记忆点', '具体服装', '瞳孔', '旧疤', '倒三角'):
            self.assertNotIn(metadata, text)
        state = wardrobe.initial_state(CARDS)
        visible = '\n'.join(wardrobe.positive_clothing_lines(state, CARDS))
        self.assertEqual(visible.count('深绿斗篷'), 1)
        self.assertNotIn('【', visible)

    def test_a_garment_memory_point_keeps_its_actual_garment_fact(self):
        text = '白衬衫；【唯一记忆点】红色围巾。\n【具体服装】黑色长裤。'
        self.assertIn('红色围巾', '、'.join(wardrobe._clothing_pieces(text)))

    def test_actual_adverb_does_not_become_part_of_wrapped_object(self):
        plan = self.plan(WRAP)
        self.assertEqual(plan['props'][0]['object'], '玉匣')
        self.assertEqual(plan['props'][0]['holder'], 'cen')
        self.assertNotIn('玄青外袍', plan['characters']['cen']['worn'])
        self.assertIn('月白窄袖中衣', plan['characters']['cen']['worn'])

    def test_attributive_wrap_and_receipt_check_do_not_rename_or_reassign(self):
        plan = self.plan(WRAP, GIVE)
        prop = plan['props'][0]
        self.assertEqual((prop['object'], prop['holder']), ('玉匣', 'gu'))
        for source in ('岑月看着裹着外袍的玉匣。', '岑月看着裹好的玉匣。', '岑月检查包裹严实后点头。'):
            with self.subTest(source=source):
                advanced = planner.advance_source(plan, source)
                self.assertEqual((advanced['props'][0]['object'], advanced['props'][0]['holder']), ('玉匣', 'gu'))

    def test_actual_place_inside_ship_retains_stable_object_and_location(self):
        plan = self.plan(WRAP, GIVE, PLACE)
        prop = plan['props'][0]
        self.assertEqual(prop['object'], '玉匣')
        self.assertIsNone(prop['holder'])
        self.assertEqual(prop['location'], '船舱底部的特制凹槽')
        self.assertIn('玉匣外裹着玄青外袍，放在船舱底部的特制凹槽。',
                      planner.positive_clothing_lines(plan, CARDS))

    def test_pending_negative_and_unresolved_pronoun_do_not_place_bundle(self):
        plan = self.plan(WRAP, GIVE)
        for text in ('顾霄准备将玉匣放入船舱底部的特制凹槽。',
                     '顾霄没有将玉匣放入船舱底部的特制凹槽。',
                     '两人站在船口。他将玉匣放入凹槽。'):
            with self.subTest(text=text):
                updated = planner.advance_source(plan, text)
                self.assertEqual(updated['props'][0]['holder'], 'gu')
                self.assertEqual(updated['props'][0]['location'], '')

    def test_old_repeated_retention_is_rebuilt_idempotently_without_touching_story(self):
        plan = self.plan(WRAP, GIVE, PLACE)
        fields = dict.fromkeys(wardrobe.FIELDS, '保留原故事与对白。')
        polluted = '<Subject 2>：当前衣着为' + GU_CLOTHING + '\n'
        fields['retention_analysis'] = ('<Subject 1>：面容、发型与体型沿用人物参考。\n' +
            polluted * 4 + '玉匣仔细外裹着玄青外袍，由<Subject 1>拿着。\n'
            '严实后外裹着玄青外袍，由<Subject 2>拿着。\n渡台栏杆为汉白玉。')
        source = dict(fields=fields, prompt='old')
        snapshot = copy.deepcopy(source)
        updated = planner.apply_compiled(source, plan, CARDS)
        again = planner.apply_compiled(updated, plan, CARDS)
        retention = updated['fields']['retention_analysis']
        self.assertEqual(updated, again)
        self.assertEqual(source, snapshot)
        self.assertEqual(retention.count('深绿斗篷'), 1)
        self.assertEqual(retention.count('玉匣外裹着玄青外袍'), 1)
        self.assertNotIn('玉匣仔细', retention)
        self.assertNotIn('严实后', retention)
        self.assertNotIn('【', retention)
        self.assertIn('渡台栏杆为汉白玉。', retention)
        self.assertIn('面容、发型与体型沿用人物参考', retention)
        self.assertEqual(updated['fields']['detailed_description'], fields['detailed_description'])

    def test_literal_quoted_words_and_other_positive_facts_are_preserved(self):
        plan = self.plan(WRAP)
        literal = '牌上写着“【核心脸部特征】玉匣外裹着玄青外袍。”'
        fields = dict.fromkeys(wardrobe.FIELDS, '原文')
        fields['retention_analysis'] = literal + '\n石柱在画面右侧。'
        result = planner.apply_compiled(dict(fields=fields), plan, CARDS)
        self.assertIn(literal, result['fields']['retention_analysis'])
        self.assertIn('石柱在画面右侧。', result['fields']['retention_analysis'])


if __name__ == '__main__':
    unittest.main()
