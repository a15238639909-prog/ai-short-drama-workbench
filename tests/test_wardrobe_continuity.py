"""Pure wardrobe-state regressions without models, services or disk writes."""
import copy
import json
import os
import unittest

os.environ['V41_NO_MODEL'] = '1'
os.environ['V41_NO_BROWSER'] = '1'

from cores import wardrobe_continuity as wardrobe


class WardrobeContinuityTests(unittest.TestCase):
    def setUp(self):
        self.characters = [
            dict(character_id='adult_a', name='陆衡', role='character', subject=2),
            dict(character_id='adult_b', name='宁岚', role='character', subject=1),
        ]
        self.frames = [dict(frame_index=0), dict(frame_index=24), dict(frame_index=49)]

    def event(self, **changes):
        event = dict(character_id='adult_a', action='remove', slot='outerwear', item='灰色外套',
            certainty='observed', before='第0帧陆衡穿着灰色外套。',
            after='第24帧陆衡已脱下灰色外套。', evidence='实际视频第0帧至第24帧可见脱下动作。',
            before_frame=0, after_frame=24, holder='adult_a', location='右手')
        event.update(changes)
        return event

    def sequence(self, *events, **changes):
        sequence = dict(wardrobe_evidence_scope='transition', wardrobe_events=list(events))
        sequence.update(changes)
        return sequence

    def advance(self, previous=None, *, segment=1, events=None, **changes):
        return wardrobe.advance(previous, characters=self.characters, segment=segment,
            sequence=self.sequence(*(events if events is not None else [self.event()])),
            evidence_frames=self.frames, **changes)

    def test_policy_is_explicit_opt_in_and_unknown_versions_are_rejected(self):
        for record in ({}, None, dict(wardrobe_policy=None), dict(wardrobe_policy=''),
                       dict(wardrobe_policy='future-version'), dict(created=9999999999)):
            with self.subTest(record=record):
                self.assertFalse(wardrobe.enabled(record))
        self.assertTrue(wardrobe.enabled(dict(wardrobe_policy=wardrobe.POLICY)))
        for value in (None, '', wardrobe.POLICY):
            self.assertEqual(wardrobe.validate_policy(value), value)
        with self.assertRaises(ValueError):
            wardrobe.validate_policy('future-version')

    def test_initial_reference_observation_cannot_invent_current_clothing(self):
        reference = dict(wardrobe_events=[self.event()], actual_visible='陆衡穿白衬衫。')
        before = copy.deepcopy((self.characters, reference))
        state = wardrobe.initial_state(self.characters, reference)
        self.assertEqual(state['last_segment'], 0)
        self.assertEqual(state['characters']['adult_a']['slots'], {})
        self.assertEqual(state['characters']['adult_b']['slots'], {})
        self.assertEqual((self.characters, reference), before)

    def test_confirmed_remove_and_later_rewear_keep_actual_order(self):
        initial = wardrobe.initial_state(self.characters)
        removed = self.advance(initial)
        self.assertEqual(initial['characters']['adult_a']['slots'], {})
        slot = removed['characters']['adult_a']['slots']['outerwear']
        self.assertEqual((slot['status'], slot['item'], slot['holder'], slot['location']),
                         ('removed', '灰色外套', 'adult_a', '右手'))
        self.assertEqual((slot['before_frame'], slot['after_frame'], slot['segment']), (0, 24, 1))
        reworn = self.advance(removed, segment=2, events=[self.event(action='put_on',
            before='第24帧外套拿在手中。', after='第49帧陆衡重新穿好灰色外套。',
            before_frame=24, after_frame=49, holder='unknown', location='unknown')])
        self.assertEqual(removed['characters']['adult_a']['slots']['outerwear']['status'], 'removed')
        self.assertEqual(reworn['characters']['adult_a']['slots']['outerwear']['status'], 'worn')
        self.assertEqual(reworn['last_segment'], 2)

    def test_replacement_overrides_only_the_observed_layer(self):
        state = self.advance(events=[self.event(action='put_on', slot='top', item='白色衬衫')])
        changed = self.advance(state, segment=2, events=[self.event(action='replace',
            item='蓝色外套', before='第0帧穿灰色外套。', after='第24帧换好蓝色外套。')])
        slots = changed['characters']['adult_a']['slots']
        self.assertEqual(slots['top']['item'], '白色衬衫')
        self.assertEqual(slots['outerwear']['item'], '蓝色外套')
        self.assertEqual(slots['outerwear']['status'], 'worn')
        self.assertNotIn('bottom', slots)
        self.assertTrue(wardrobe.prompt_contract(changed, self.characters)['characters'][0]['reference_clothing_baseline'])

    def test_whole_outfit_replacement_drops_old_layers_not_shoes_or_accessories(self):
        events = [self.event(action='put_on', slot=slot, item=item) for slot, item in (
            ('outerwear', '灰色外套'), ('top', '白色衬衫'), ('bottom', '深色长裤'),
            ('shoes', '棕色皮鞋'), ('accessory', '银色腕表'))]
        original = self.advance(events=events)
        changed = self.advance(original, segment=2, events=[self.event(action='replace', slot='outfit',
            item='蓝色工作连体服', before='第0帧穿原来的上衣和长裤。',
            after='第24帧换成蓝色工作连体服。', holder='unknown', location='unknown')])
        self.assertEqual(set(changed['characters']['adult_a']['slots']), {'outfit', 'shoes', 'accessory'})
        contract = wardrobe.prompt_contract(changed, self.characters)['characters'][0]
        self.assertFalse(contract['reference_clothing_baseline'])
        self.assertEqual(contract['slots']['shoes']['item'], '棕色皮鞋')
        self.assertNotIn('白色衬衫', json.dumps(contract, ensure_ascii=False))
        self.assertIn('top', original['characters']['adult_a']['slots'])

    def test_unknown_occluded_or_unavailable_observations_inherit_confirmed_state(self):
        previous = self.advance()
        before = copy.deepcopy(previous)
        sequences = [
            self.sequence(self.event(action='put_on'), status='unavailable'),
            self.sequence(self.event(action='put_on'), wardrobe_evidence_scope='single_frame'),
            self.sequence(self.event(action='put_on', certainty='unknown')),
            self.sequence(self.event(action='put_on', before='unknown')),
            self.sequence(self.event(action='put_on', after='无法确认')),
            self.sequence(self.event(action='put_on', item='不明')),
            self.sequence(self.event(action='put_on', before='衣物被遮挡。', after='衣物被遮挡。')),
            self.sequence(),
        ]
        for sequence in sequences:
            with self.subTest(sequence=sequence):
                actual = wardrobe.advance(previous, characters=self.characters, sequence=sequence,
                    segment=2, evidence_frames=self.frames)
                self.assertEqual(actual, before)
                self.assertIsNot(actual, previous)
        self.assertEqual(previous, before)

    def test_same_reversed_unknown_and_noninteger_event_frames_are_rejected(self):
        previous = self.advance()
        for before_frame, after_frame in ((24, 24), (49, 0), (0, 99), (-1, 24),
                                          ('0', 24), (False, 24), (0, True), (None, 24)):
            with self.subTest(before=before_frame, after=after_frame):
                actual = self.advance(previous, segment=2, events=[self.event(action='put_on',
                    before_frame=before_frame, after_frame=after_frame)])
                self.assertEqual(actual, previous)

    def test_missing_single_or_duplicate_trusted_frames_cannot_confirm_change(self):
        previous = self.advance()
        for frames in ((), (0,), (0, 0), (False, 24), (0, '24'), (None, 24)):
            with self.subTest(frames=frames):
                actual = wardrobe.advance(previous, characters=self.characters,
                    sequence=self.sequence(self.event(action='put_on')), segment=2,
                    evidence_frames=frames)
                self.assertEqual(actual, previous)

    def test_endpoint_and_source_plan_do_not_count_as_transition_proof(self):
        previous = self.advance()
        actual = wardrobe.advance(previous, characters=self.characters, segment=2,
            endpoint=dict(wardrobe_events=[self.event(action='put_on')]),
            source_events=[self.event(action='put_on')], evidence_frames=self.frames)
        self.assertEqual(actual, previous)

    def test_canonical_identity_is_not_rebound_from_subject_or_ambiguous_name(self):
        characters = [dict(character_id='adult_a', name='同名', subject=2),
                      dict(character_id='adult_b', name='同名', subject=1)]
        for identity in ('同名', 'S2', 'Subject_2', '2', 'unknown'):
            with self.subTest(identity=identity):
                state = wardrobe.advance(None, characters=characters, segment=1,
                    sequence=self.sequence(self.event(character_id=identity)), evidence_frames=self.frames)
                self.assertTrue(all(not row['slots'] for row in state['characters'].values()))
        canonical = wardrobe.advance(None, characters=characters, segment=1,
            sequence=self.sequence(self.event(character_id='adult_b')), evidence_frames=self.frames)
        self.assertEqual(canonical['characters']['adult_a']['slots'], {})
        self.assertEqual(canonical['characters']['adult_b']['slots']['outerwear']['status'], 'removed')
        duplicate = wardrobe.advance(None, characters=characters + [copy.deepcopy(characters[0])],
            sequence=self.sequence(self.event()), segment=1, evidence_frames=self.frames)
        self.assertNotIn('adult_a', duplicate['characters'])
        self.assertEqual(duplicate['characters']['adult_b']['slots'], {})

    def test_contract_filters_to_current_binding_and_maps_reordered_subject(self):
        state = self.advance(events=[self.event(), self.event(character_id='adult_b', item='棕色外套')])
        before = copy.deepcopy(state)
        bindings = [dict(character_id='adult_b', name='宁岚', role='character', subject=4),
                    dict(character_id='adult_a', name='外套道具图', role='prop', subject=1)]
        contract = wardrobe.prompt_contract(state, bindings)
        self.assertEqual(len(contract['characters']), 1)
        row = contract['characters'][0]
        self.assertEqual((row['character_id'], row['subject']), ('adult_b', 4))
        self.assertEqual(row['slots']['outerwear']['item'], '棕色外套')
        self.assertEqual(state, before)

    def test_model_state_removes_observation_evidence_without_mutating_persistence(self):
        state = self.advance()
        before = copy.deepcopy(state)
        model = wardrobe.model_state(state, self.characters[:1])
        slot = model['characters']['adult_a']['slots']['outerwear']
        self.assertEqual(set(slot), {'status', 'item', 'holder', 'location', 'segment'})
        self.assertEqual((model['policy'], model['last_segment']), (wardrobe.POLICY, 1))
        self.assertNotIn('adult_b', model['characters'])
        for excluded in ('evidence', 'before', 'after', 'before_frame', 'after_frame'):
            self.assertNotIn(excluded, slot)
        self.assertEqual(state, before)
        self.assertIn('before_frame', state['characters']['adult_a']['slots']['outerwear'])

    def test_compiled_six_fields_stay_synchronized_dialogue_unchanged_and_idempotent(self):
        dialogue = '陆衡站在门边并把外套放到椅背上，说：“我已经放好外套，你先关门。”'
        fields = dict(subject_definitions='Subject 1：陆衡。', summary='陆衡整理工具。',
            retention_analysis='镜头接续前段的真实位置。\nIdentity authority (source-and-reference-identity-v1): 旧说明。',
            detailed_description=dialogue, overall_soundscape='门轴发出轻响。', non_diegetic_music='无配乐。')
        fields['subject_definitions'] += '\nCharacter references define stable face, hair and wardrobe identity.'
        compiled = dict(fields=fields, prompt='尚未同步的旧提示词', references=['reference-a'], extra=dict(seed=42))
        before = copy.deepcopy(compiled)
        result = wardrobe.apply_compiled(compiled, self.advance(), self.characters[:1])
        expected = '\n\n'.join(key + ':\n' + result['fields'][key] for key in wardrobe.FIELDS)
        self.assertEqual(result['prompt'], expected)
        self.assertEqual(list(result['fields']), list(wardrobe.FIELDS))
        self.assertEqual(result['fields']['detailed_description'], dialogue)
        self.assertEqual(result['fields']['overall_soundscape'], fields['overall_soundscape'])
        self.assertEqual(result['fields']['non_diegetic_music'], fields['non_diegetic_music'])
        self.assertEqual(result['references'], before['references'])
        self.assertEqual(result['extra'], before['extra'])
        self.assertNotIn(wardrobe.MARKER, result['prompt'])
        self.assertNotIn('Current wardrobe contract:', result['prompt'])
        self.assertIn('陆衡的右手拿着灰色外套', result['prompt'])
        self.assertNotIn('Identity authority (source-and-reference-identity-v1):', result['prompt'])
        self.assertNotIn('hair and wardrobe identity.', result['prompt'])
        self.assertIn('镜头接续前段的真实位置。', result['fields']['retention_analysis'])
        self.assertEqual(wardrobe.apply_compiled(result, self.advance(), self.characters[:1]), result)
        self.assertEqual(compiled, before)

    def test_observation_request_binds_actual_frames_and_preserves_request(self):
        request = ('只描述最后画面。', dict(task='observe'),
                   dict(type='object', properties=dict(actual_visible=dict(type='string')), required=['actual_visible']))
        before = copy.deepcopy(request)
        system, payload, schema = wardrobe.observation_request(request, self.characters,
            transition_evidence=True, frame_bindings=[dict(frame_index=49), dict(frame_index=0)])
        self.assertEqual(payload['wardrobe_frame_bindings'],
                         [dict(image_index=1, frame_index=49), dict(image_index=2, frame_index=0)])
        self.assertEqual(payload['wardrobe_policy'], wardrobe.POLICY)
        self.assertEqual([row['character_id'] for row in payload['wardrobe_identity_bindings']], ['adult_a', 'adult_b'])
        events = schema['properties']['wardrobe_events']
        self.assertGreater(events['maxItems'], 0)
        for field in ('before_frame', 'after_frame'):
            self.assertEqual(events['items']['properties'][field]['enum'], [0, 49, None])
            self.assertIn(field, events['items']['required'])
        self.assertEqual(schema['properties']['wardrobe_evidence_scope']['enum'], ['transition'])
        self.assertIn('不能按左右位置、衣色或Subject编号猜身份', system)
        self.assertIn('before_frame与after_frame', system)
        self.assertEqual(request, before)

    def test_observation_request_requires_real_distinct_frames_not_boolean_permission(self):
        request = ('原系统规则。', {}, dict(type='object', properties={}))
        cases = [(True, ()), (True, (0,)), (True, (0, 0)), (True, (False, 24)),
                 (True, (0, '24')), (True, (-1, 24)), (False, (0, 24))]
        for permission, frames in cases:
            with self.subTest(permission=permission, frames=frames):
                system, payload, schema = wardrobe.observation_request(request, self.characters,
                    transition_evidence=permission, frame_bindings=frames)
                self.assertEqual(schema['properties']['wardrobe_events']['maxItems'], 0)
                self.assertEqual(schema['properties']['wardrobe_evidence_scope']['enum'], ['single_frame'])
                self.assertIn('wardrobe_events必须为空', system)
                self.assertEqual(payload['wardrobe_policy'], wardrobe.POLICY)

    def test_large_current_contract_fails_clearly_without_truncating_state(self):
        state = wardrobe.initial_state(self.characters)
        for person in state['characters'].values():
            person['slots'] = {slot: dict(status='worn', item='有来源的衣物细节' * 300,
                holder='unknown', location='unknown', segment=1) for slot in wardrobe.SLOTS}
        before = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, '超过安全上下文额度'):
            wardrobe.prompt_contract(state, self.characters)
        self.assertEqual(state, before)


if __name__ == '__main__':
    unittest.main()
