"""Model-free regressions for mixed-frame state and source-backed wardrobe."""
import copy
import unittest

from cores import endpoint_evidence as endpoint, wardrobe_continuity as wardrobe
from cores import story_video_reference_prompts as prompts


class EndpointEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.people = [dict(character_id='hero', name='艾尔', clothing='白衬衫与黑长裤'),
                       dict(character_id='doctor', name='医生')]
        self.frames = [dict(image_index=1, frame_index=0, time_seconds=0),
                       dict(image_index=2, frame_index=249, time_seconds=9.96)]

    def observation(self, **changes):
        result = dict(endpoint_frame_index=249, endpoint_conflicts=[], actual_visible='艾尔站在床边。',
            scene='诊室', characters=[dict(id='hero', frame_index=249, position='床边', pose='站立',
                hands='扶着床沿', visibility='visible', certainty='observed')], props=[],
            continuity_risks=[], unknowns=[], actions=['计划动作不能算完成'], completed=['计划事件'])
        result.update(changes)
        return result

    def test_request_binds_exact_final_frame_and_canonical_id(self):
        original = prompts.observe_request('opening')
        before = copy.deepcopy(original)
        system, payload, schema = endpoint.request_contract(original, self.people, self.frames)
        self.assertEqual(payload['endpoint_evidence']['image_index'], 2)
        self.assertEqual(schema['properties']['endpoint_frame_index']['enum'], [249])
        item = schema['properties']['characters']['items']
        self.assertEqual(item['properties']['id']['enum'], ['hero', 'doctor', 'unknown'])
        self.assertEqual(item['properties']['frame_index']['enum'], [249])
        self.assertIn('首帧仅用于衣着前后比较', system)
        self.assertEqual(original, before)

    def test_project62_standing_vs_fallen_and_sitting_are_unknown(self):
        for risk in ('末帧艾尔倒地，医生消失。', '实际最后画面艾尔坐在地面。'):
            raw = self.observation(continuity_risks=[risk])
            state = endpoint.normalize(raw, self.people, self.frames)
            self.assertEqual(state['characters'][0]['pose'], 'unknown')
            self.assertEqual(state['actual_visible'], 'unknown')
            self.assertEqual(endpoint.start_state(state, self.people), '')
            self.assertEqual(raw['characters'][0]['pose'], '站立')

    def test_unscoped_old_cache_first_frame_and_style_cannot_be_start_state(self):
        cases = [self.observation(endpoint_frame_index=0), self.observation(endpoint_frame_index=None),
                 self.observation(endpoint_conflicts=['人物字段和末帧冲突'])]
        wrong_row = self.observation(); wrong_row['characters'][0]['frame_index'] = 0
        cases.append(wrong_row)
        cases.append(self.observation(actual_visible='真人实拍感', scene='unknown', characters=[]))
        for raw in cases:
            self.assertEqual(endpoint.start_state(endpoint.normalize(raw, self.people, self.frames), self.people), '')

    def test_only_supported_dynamic_facts_cross_into_next_start_state(self):
        state = endpoint.normalize(self.observation(), self.people, self.frames)
        self.assertEqual(endpoint.start_state(state, self.people), '艾尔：床边，站立，扶着床沿')
        self.assertEqual(state['actions'], [])
        self.assertEqual(state['completed'], [])
        self.assertEqual(endpoint.normalize(state, self.people, self.frames), state)

    def test_empty_environment_still_has_usable_endpoint_without_person(self):
        state = endpoint.normalize(self.observation(actual_visible='空旷的机库', scene='机库', characters=[]),
                                   self.people, self.frames)
        self.assertEqual(endpoint.start_state(state), '场景：机库')

    def test_initial_design_is_not_a_verified_change_or_reason_to_append_tail(self):
        initial = wardrobe.initial_state(self.people)
        contract = wardrobe.prompt_contract(initial, self.people)
        self.assertEqual(contract['characters'][0]['baseline_design'], '白衬衫与黑长裤')
        self.assertEqual(contract['characters'][0]['slots'], {})
        self.assertFalse(wardrobe.has_observed_changes(contract))

    def test_drift_unknown_identity_and_other_actor_action_cannot_change_clothing(self):
        event = dict(character_id='hero', action='remove', slot='outerwear', item='外套',
            before='艾尔穿着外套', after='艾尔脱下外套', evidence='前后实际帧显示变化',
            before_frame=0, after_frame=249, certainty='observed')
        sequence = dict(wardrobe_evidence_scope='transition', wardrobe_events=[event])
        initial = wardrobe.initial_state(self.people)
        for source in ('艾尔走向窗边。', '医生脱下外套，艾尔抬头。', '艾尔说：“我脱下外套了吗？”'):
            state = wardrobe.advance(initial, characters=self.people, sequence=sequence,
                evidence_frames=self.frames, source_events=source)
            self.assertEqual(state, initial)
        sequence['wardrobe_events'][0]['character_id'] = 'CHAR_未知1'
        state = wardrobe.advance(initial, characters=self.people, sequence=sequence,
            evidence_frames=self.frames, source_events='艾尔脱下外套。')
        self.assertEqual(state, initial)

    def test_planned_change_alone_is_not_fact_but_observed_damage_can_continue(self):
        initial = wardrobe.initial_state(self.people)
        self.assertEqual(wardrobe.advance(initial, characters=self.people, evidence_frames=self.frames,
            source_events='艾尔的衬衫被撕破。'), initial)
        event = dict(character_id='hero', action='damage', slot='top', item='白衬衫',
            before='衬衫袖口完整', after='原衬衫袖口撕裂', evidence='首末实际帧可见同件衣服破损',
            before_frame=0, after_frame=249, certainty='observed')
        state = wardrobe.advance(initial, characters=self.people,
            sequence=dict(wardrobe_evidence_scope='transition', wardrobe_events=[event]),
            evidence_frames=self.frames, source_events='艾尔的衬衫袖口被撕破。')
        self.assertEqual(state['characters']['hero']['slots']['top']['status'], 'damaged')
        self.assertTrue(wardrobe.has_observed_changes(wardrobe.prompt_contract(state, self.people)))


if __name__ == '__main__':
    unittest.main()
