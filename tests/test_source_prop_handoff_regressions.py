"""Real terminal failures reduced to source-only wardrobe/prop contracts."""
import copy
import unittest

from cores import source_wardrobe_plan as planner


class SourcePropHandoffRegressions(unittest.TestCase):
    def setUp(self):
        self.chars = [
            dict(character_id='lin', name='林岚', role='character', subject=1,
                 clothing='深灰外套、白衬衫、黑长裤'),
            dict(character_id='chen', name='陈川', role='character', subject=2,
                 clothing='深绿雨衣、黑长裤'),
        ]

    def opening(self, *texts):
        return planner.build_start_plan([dict(text=text) for text in texts],
                                        len(texts) - 1, self.chars)

    def test_forward_handoff_with_pronoun_and_bare_receipt_clause(self):
        plan = self.opening(
            '林岚脱下深灰外套，将胶片盒裹好。',
            '林岚双手捧出裹着外套的胶片盒，向前递出。'
            '陈川从阴影中走出。他伸出双手，稳稳接住胶片盒。',
            '陈川抱着包裹。')
        prop = plan['props'][0]
        self.assertEqual((prop['object'], prop['holder']), ('胶片盒', 'chen'))
        self.assertNotIn('深灰外套', plan['characters']['chen']['worn'])
        self.assertIn('胶片盒外裹着深灰外套，由<Subject 2>拿着。',
                      planner.positive_clothing_lines(plan, self.chars))

    def test_placed_bundle_ends_handheld_state_with_actual_wording(self):
        plan = self.opening(
            '林岚脱下深灰外套，将胶片盒裹好。',
            '林岚将胶片盒交给陈川。',
            '陈川踏上跳板。他将胶片盒安放于驾驶位旁的石台上。',
            '陈川启动胶片盒旁的符纹。')
        prop = plan['props'][0]
        self.assertIsNone(prop['holder'])
        self.assertEqual(prop['location'], '驾驶位旁的石台上')
        self.assertIn('胶片盒外裹着深灰外套，放在驾驶位旁的石台上。',
                      planner.positive_clothing_lines(plan, self.chars))

    def test_explicit_other_object_cannot_claim_pending_bundle(self):
        plan = self.opening(
            '林岚脱下深灰外套，将胶片盒裹好。',
            '林岚向前递出胶片盒。陈川接过铜钥匙。',
            '陈川走向船。')
        self.assertIsNone(plan['props'][0]['holder'])
        self.assertTrue(plan['props'][0]['awaiting_receiver'])
        advanced = planner.advance_source(plan, '陈川接住包裹。')
        self.assertEqual(advanced['props'][0]['holder'], 'chen')

    def test_pronoun_action_at_current_slice_keeps_source_actor(self):
        plan = self.opening('林岚取出胶片盒。', '她脱下深灰外套，将胶片盒裹好。')
        before = copy.deepcopy(plan)
        first = planner.advance_source(plan, '她脱下深灰外套。')
        # The current actor is identified by the source's explicit pronoun.
        second = planner.advance_source(first, '她将胶片盒裹好。')
        self.assertNotIn('深灰外套', first['characters']['lin']['worn'])
        self.assertEqual(second['props'][0]['object'], '胶片盒')
        self.assertEqual(plan, before)
        self.assertNotIn('_last_source_subject', planner.model_state(plan, self.chars))

    def test_planned_outgoing_handoff_does_not_advance_state(self):
        plan = self.opening('林岚脱下深灰外套，将胶片盒裹好。',
                            '林岚准备向前递出胶片盒。', '陈川站在一旁。')
        self.assertEqual(plan['props'][0]['holder'], 'lin')
        self.assertNotIn('awaiting_receiver', plan['props'][0])


if __name__ == '__main__':
    unittest.main()
