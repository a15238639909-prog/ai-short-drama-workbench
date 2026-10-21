"""No-model request, schema-boundary and speech-timing tests."""
import copy
import json
import math
import os
import re
import unittest

os.environ['V41_NO_MODEL'] = '1'
from cores import story_video_prompts as prompts


def check_instance(value, schema):
    """Validate the JSON Schema subset used here without optional dependencies."""
    types = schema.get('type')
    if types:
        types = types if isinstance(types, list) else [types]
        predicates = {'object': lambda x: isinstance(x, dict),
                      'array': lambda x: isinstance(x, list),
                      'string': lambda x: isinstance(x, str),
                      'integer': lambda x: isinstance(x, int) and not isinstance(x, bool),
                      'number': lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x),
                      'boolean': lambda x: isinstance(x, bool),
                      'null': lambda x: x is None}
        if not any(predicates[k](value) for k in types):
            raise ValueError('type')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('enum')
    if isinstance(value, dict):
        if not set(schema.get('required', [])) <= value.keys():
            raise ValueError('required')
        properties = schema.get('properties', {})
        if schema.get('additionalProperties') is False and value.keys() - properties.keys():
            raise ValueError('additionalProperties')
        for key in value.keys() & properties.keys():
            check_instance(value[key], properties[key])
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', math.inf):
            raise ValueError('items length')
        if schema.get('uniqueItems') and len({json.dumps(x, sort_keys=True) for x in value}) != len(value):
            raise ValueError('uniqueItems')
        for item in value:
            check_instance(item, schema.get('items', {}))
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', math.inf):
            raise ValueError('string length')
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get('minimum', -math.inf) or value > schema.get('maximum', math.inf):
            raise ValueError('numeric range')
        if 'exclusiveMinimum' in schema and value <= schema['exclusiveMinimum']:
            raise ValueError('exclusiveMinimum')


class StoryVideoRequestTests(unittest.TestCase):
    def plan_fixture(self):
        return {'title': '测试', 'logline': '按用户目标推进', 'ending': '冲突解决',
                'characters': [], 'props': [], 'invariants': [],
                'beats': [{'index': i, 'seconds': 8, 'goal': '出现可见进展',
                           'action': '执行当前动作', 'turn': '局面变化',
                           'emotion': '通过动作表现', 'ending': '清楚的续接状态',
                           'cast': [], 'dialogue': []} for i in (1, 2)]}

    def test_exact_clip_count_and_duration_contract(self):
        _, payload, schema = prompts.plan_request('按我的构思写故事', '国风', 2, 8)
        check_instance(self.plan_fixture(), schema)
        self.assertEqual(payload['brief'], '按我的构思写故事')
        for change in ('missing_clip', 'extra_clip', 'out_of_range_index', 'wrong_duration'):
            example = self.plan_fixture()
            if change == 'missing_clip':
                example['beats'].pop()
            elif change == 'extra_clip':
                example['beats'].append(copy.deepcopy(example['beats'][0]))
            elif change == 'out_of_range_index':
                example['beats'][0]['index'] = 3
            else:
                example['beats'][0]['seconds'] = 15
            with self.subTest(change=change), self.assertRaises(ValueError):
                check_instance(example, schema)

    def test_eight_seconds_optional_dialogue_uses_natural_clip_capacity(self):
        from cores import story_video_core as core

        _, payload, schema = prompts.plan_request('无旁白的短片', '', 2, 8)
        budget = payload['timing_budget']
        self.assertEqual(budget['max_dialogue_seconds'], 8)
        example = self.plan_fixture()
        example['characters'] = [{'id': 'C1', 'name': '同行者',
                                  'appearance': '成年旅人', 'role': '主角'}]
        for beat in example['beats']:
            beat['cast'] = ['C1']
        # Silence stays valid, while a natural exchange need not stop at one line.
        check_instance(example, schema)
        core.validate_plan(example, payload)
        example['beats'][0]['dialogue'] = [
            {'speaker': 'C1', 'text': '水声就在前面。'},
            {'speaker': 'C1', 'text': '好，我们过去看看。'},
        ]
        check_instance(example, schema)
        core.validate_plan(example, payload)
        # Two individually valid lines still cannot exceed the shared clip budget.
        example['beats'][0]['dialogue'] = [
            {'speaker': 'C1', 'text': '字' * 17},
            {'speaker': 'C1', 'text': '字' * 17},
        ]
        check_instance(example, schema)
        with self.assertRaisesRegex(ValueError, '对白超过自然表演预算'):
            core.validate_plan(example, payload)
        # Even 32 characters fit only if their two pauses fit within eight seconds.
        for line in example['beats'][0]['dialogue']:
            line['text'] = '字' * 16
        check_instance(example, schema)
        with self.assertRaisesRegex(ValueError, '对白无足够停顿时间'):
            core.validate_plan(example, payload)

    def test_requests_are_independent_and_do_not_mutate_sources(self):
        actual = {'unknowns': ['道具的持有人不清楚']}
        plan = self.plan_fixture()
        original = copy.deepcopy((actual, plan))
        _, planned, _ = prompts.plan_request('任意题材', '', 2, 8, actual)
        _, observed, _ = prompts.observe_request('segment', actual, plan['beats'][0])
        _, written, first_schema = prompts.write_request(plan, plan['beats'][0], actual, 8)
        planned['opening_observation']['unknowns'].clear()
        observed['beat']['goal'] = 'changed'
        written['story_plan']['beats'].clear()
        first_schema['properties']['dialogue'].clear()
        self.assertEqual((actual, plan), original)
        self.assertIn('maxItems', prompts.write_request(plan, plan['beats'][0], actual, 8)[2]['properties']['dialogue'])

    def test_opening_cannot_certify_past_actions_or_invent_cut_time(self):
        system, payload, schema = prompts.observe_request('opening')
        self.assertEqual(payload['stage'], 'opening')
        check_instance([], schema['properties']['completed'])
        check_instance(None, schema['properties']['keep_end_seconds'])
        with self.assertRaises(ValueError):
            check_instance(['已交接'], schema['properties']['completed'])
        with self.assertRaises(ValueError):
            check_instance(6.5, schema['properties']['keep_end_seconds'])
        self.assertIn('未知不得自动升级为成功', system)

    def test_offscreen_state_and_unknown_water_are_representable(self):
        _, _, schema = prompts.observe_request('segment')
        prop = {'id': 'P1', 'appearance': '最后已知外观', 'count': None,
                'holder': 'unknown', 'location': '最后已知在台沿',
                'visibility': 'offscreen', 'certainty': 'last_known'}
        check_instance(prop, schema['properties']['props']['items'])
        check_instance(None, schema['properties']['keep_end_seconds'])
        check_instance('未见水，不能判定已干', schema['properties']['water'])
        with self.assertRaises(ValueError):
            check_instance(0, schema['properties']['keep_end_seconds'])

    def test_i2va_and_t2va_have_distinct_alignment(self):
        plan = self.plan_fixture()
        for mode in ('i2va', 't2va'):
            system, payload, schema = prompts.write_request(plan, plan['beats'][0], {}, 8, mode)
            self.assertEqual(payload['mode'], mode)
            self.assertNotIn('first_frame_instruction', payload)
            self.assertFalse(payload['is_final'])
            self.assertIn('integrated_multimodal_description', system)
            self.assertNotIn('prompt', schema['properties'])
            self.assertEqual(schema['properties']['dialogue']['maxItems'], 0)
        self.assertTrue(prompts.write_request(plan, plan['beats'][-1], {}, 8)[1]['is_final'])

    def test_write_schema_rejects_dialogue_outside_clip_and_extra_lines(self):
        plan = self.plan_fixture()
        plan['beats'][0]['dialogue'] = [{'speaker': 'C1', 'text': '快走。'}]
        _, _, schema = prompts.write_request(plan, plan['beats'][0], {}, 8)
        line_schema = schema['properties']['dialogue']
        line = {'speaker': 'C1', 'text': '快走。', 'start': 1, 'end': 3,
                'delivery': 'The adult woman says urgently'}
        check_instance([line], line_schema)
        for invalid in ([dict(line, end=8.1)], [dict(line, start=-.1)], [line, line]):
            with self.assertRaises(ValueError):
                check_instance(invalid, line_schema)

    def written_fixture(self, dialogue=None):
        dialogue = [] if dialogue is None else dialogue
        return {'integrated_multimodal_description':
                    '[Shot 1] Cinematic, a medium shot follows the adult turning toward the doorway. '
                    + 'The adult steps through the doorway and keeps one hand on its frame.',
                'overall_soundscape': 'Footsteps cross stone while fabric rustles.',
                'non_diegetic_music': 'N/A',
                'expected_end_state': 'META_END_STATE', 'brief': 'META_BRIEF',
                'timeline': [{'start': 0, 'end': 8, 'action': 'META_ACTION',
                              'visible_result': 'META_RESULT'}],
                'dialogue': copy.deepcopy(dialogue), 'continuity_notes': ['META_NOTE']}

    def test_compile_binds_chinese_once_and_keeps_metadata_out_of_prompt(self):
        plan = self.plan_fixture()
        plan['beats'][0]['dialogue'] = [{'speaker': 'C1', 'text': '先等等！'}]
        _, payload, schema = prompts.write_request(plan, plan['beats'][0], {}, 8)
        raw = self.written_fixture([{'speaker': 'C1', 'text': '先等等！', 'start': 1, 'end': 3,
                                    'delivery': 'The adult woman says quietly'}])
        check_instance(raw, schema)
        original = copy.deepcopy(raw)
        compiled = prompts.compile_written(raw, payload)
        self.assertEqual(raw, original)
        self.assertNotIn('prompt', raw)
        self.assertEqual(compiled['prompt'].count('<d>[Chinese] 先等等！</d>'), 1)
        self.assertIn('At 00:01.000, The adult woman says quietly (S1)', compiled['prompt'])
        self.assertTrue(compiled['prompt'].startswith(prompts.I2VA_FIRST_FRAME + '\n\n'))
        self.assertNotIn('{{D1}}', compiled['prompt'])
        self.assertNotIn('META_', compiled['prompt'])
        self.assertEqual(compiled['prompt'].count('integrated_multimodal_description:'), 1)

    def test_compile_t2va_has_no_frame_preamble_and_empty_dialogue_is_fixed(self):
        plan = self.plan_fixture()
        _, payload, schema = prompts.write_request(plan, plan['beats'][0], {}, 8, 't2va')
        raw = self.written_fixture()
        compiled = prompts.compile_written(raw, payload)
        self.assertTrue(compiled['prompt'].startswith('integrated_multimodal_description:'))
        self.assertNotIn('Picture', compiled['prompt'])
        self.assertNotIn('For the target video', compiled['prompt'])
        self.assertEqual(schema['properties']['dialogue']['minItems'], 0)
        with self.assertRaises(ValueError):
            check_instance([{'speaker': 'C1', 'text': '新增台词', 'start': 1, 'end': 2}],
                           schema['properties']['dialogue'])

    def test_compile_normalizes_only_the_matching_delivery_speaker_annotation(self):
        plan = self.plan_fixture()
        plan['beats'][0]['dialogue'] = [{'speaker': 'C1', 'text': '先等等。'},
                                      {'speaker': 'C2', 'text': '好。'},
                                      {'speaker': 'C3', 'text': '跟我来。'}]
        _, payload, _ = prompts.write_request(plan, plan['beats'][0], {}, 15)
        raw = self.written_fixture([
            {'speaker': line['speaker'], 'text': line['text'], 'start': index * 3,
             'end': index * 3 + 2, 'delivery': 'The adult (S%d), speaking excitedly' % (index + 1)}
            for index, line in enumerate(plan['beats'][0]['dialogue'])])
        original = copy.deepcopy((raw, payload))
        compiled = prompts.compile_written(raw, payload)
        for number, line in enumerate(compiled['dialogue'], 1):
            self.assertEqual(line['delivery'], 'The adult, speaking excitedly')
            self.assertEqual(compiled['prompt'].count('(S%d)' % number), 1)
            self.assertEqual(compiled['prompt'].count('<d>[Chinese] %s</d>' % line['text']), 1)
        self.assertEqual((raw, payload), original)

    def test_compile_delivery_normalization_keeps_wrong_ids_slots_and_tags_rejected(self):
        plan = self.plan_fixture()
        plan['beats'][0]['dialogue'] = [{'speaker': 'C1', 'text': '先等等。'}]
        _, payload, _ = prompts.write_request(plan, plan['beats'][0], {}, 8)
        for delivery in ('The adult (S2), speaking excitedly',
                         'The adult (S99), speaking excitedly',
                         'The adult (S1) (S2), speaking excitedly',
                         'The adult (S1), {{D1}}',
                         'The adult (S1), <d>[Chinese] forged words</d>', '(S1)'):
            raw = self.written_fixture([{'speaker': 'C1', 'text': '先等等。', 'start': 1, 'end': 3,
                                        'delivery': delivery}])
            original = copy.deepcopy(raw)
            with self.subTest(delivery=delivery), self.assertRaises(ValueError):
                prompts.compile_written(raw, payload)
            self.assertEqual(raw, original)
        raw = self.written_fixture([{'speaker': 'C2', 'text': '先等等。', 'start': 1, 'end': 3,
                                    'delivery': 'The adult (S1), speaking excitedly'}])
        with self.assertRaises(ValueError):
            prompts.compile_written(raw, payload)

    def test_fixed_dialogue_schema_rejects_translation_and_tags(self):
        plan = self.plan_fixture()
        plan['beats'][0]['dialogue'] = [{'speaker': 'C1', 'text': '先等等！'}]
        _, payload, schema = prompts.write_request(plan, plan['beats'][0], {}, 8)
        self.assertNotIn('placeholder', payload['dialogue_slots'][0])
        self.assertEqual(payload['dialogue_slots'][0]['index'], 1)
        for text in ('Wait!', '<d>[Chinese]', '<d>[Chinese] 先等等！</d>'):
            line = {'speaker': 'C1', 'text': text, 'start': 1, 'end': 3,
                    'delivery': 'The adult woman says quietly'}
            with self.subTest(text=text), self.assertRaises(ValueError):
                check_instance([line], schema['properties']['dialogue'])
        with self.assertRaises(ValueError):
            prompts.compile_written(self.written_fixture(
                [{'speaker': 'C1', 'text': 'Wait!', 'start': 1, 'end': 3,
                  'delivery': 'The adult woman says quietly'}]), payload)

    def test_compile_rejects_all_handwritten_slots(self):
        plan = self.plan_fixture()
        plan['beats'][0]['dialogue'] = [{'speaker': 'C1', 'text': '等等。'},
                                      {'speaker': 'C2', 'text': '好。'}]
        _, payload, _ = prompts.write_request(plan, plan['beats'][0], {}, 15)
        lines = [{'speaker': 'C1', 'text': '等等。', 'start': 1, 'end': 2,
                  'delivery': 'The adult woman says gently'},
                 {'speaker': 'C2', 'text': '好。', 'start': 3, 'end': 4,
                  'delivery': 'The adult man answers softly'}]
        for placeholders in ('{{D1}}', '{{D1}} {{D1}} {{D2}}',
                             '{{D1}} {{D9}}', '{{D2}} {{D1}}', '{{ D1 }} {{D2}}'):
            raw = self.written_fixture(lines)
            raw['integrated_multimodal_description'] = '[Shot 1] ' + placeholders
            with self.subTest(placeholders=placeholders), self.assertRaises(ValueError):
                prompts.compile_written(raw, payload)

    def test_compile_rejects_transport_meta_or_literal_dialogue_in_content(self):
        plan = self.plan_fixture()
        _, payload, _ = prompts.write_request(plan, plan['beats'][0], {}, 8)
        for leak in ('timeline: [{start: 0}]', 'brief: 制作说明',
                     'overall_soundscape: ambient', prompts.I2VA_FIRST_FRAME,
                     '<d>[Chinese] 未授权</d>'):
            raw = self.written_fixture()
            raw['integrated_multimodal_description'] += ' ' + leak
            with self.subTest(leak=leak), self.assertRaises(ValueError):
                prompts.compile_written(raw, payload)

    def test_compile_preserves_original_english_when_source_is_english(self):
        plan = self.plan_fixture()
        plan['beats'][0]['dialogue'] = [{'speaker': 'C1', 'text': 'Wait!'}]
        _, payload, _ = prompts.write_request(plan, plan['beats'][0], {}, 8)
        raw = self.written_fixture([{'speaker': 'C1', 'text': 'Wait!', 'start': 1, 'end': 3,
                                    'delivery': 'The adult woman says quietly'}])
        result = prompts.compile_written(raw, payload)
        self.assertIn('<d>[English] Wait!</d>', result['prompt'])

    def test_dialogue_injection_obeys_shot_cuts_and_existing_time_anchors(self):
        plan = self.plan_fixture()
        plan['beats'][0]['dialogue'] = [{'speaker': 'C1', 'text': '等等。'},
                                      {'speaker': 'C2', 'text': '好。'}]
        _, payload, _ = prompts.write_request(plan, plan['beats'][0], {}, 15)
        raw = self.written_fixture([
            {'speaker': 'C1', 'text': '等等。', 'start': 1, 'end': 2,
             'delivery': 'The adult woman says gently'},
            {'speaker': 'C2', 'text': '好。', 'start': 5, 'end': 6,
             'delivery': 'The adult man answers softly'}])
        raw['integrated_multimodal_description'] = (
            '[Shot 1] Live-action, the woman approaches the doorway. '
            'At 00:03.000, she reaches the frame. '
            '[Shot 2] At 00:04.000, the camera cuts to the man inside. '
            'At 00:07.000, he steps back to leave room.')
        prompt = prompts.compile_written(raw, payload)['prompt']
        self.assertLess(prompt.index('<d>[Chinese] 等等。</d>'), prompt.index('At 00:03.000'))
        self.assertLess(prompt.index('[Shot 2]'), prompt.index('<d>[Chinese] 好。</d>'))
        self.assertLess(prompt.index('<d>[Chinese] 好。</d>'), prompt.index('At 00:07.000'))
        self.assertEqual(prompt.count('(S1)'), 1)
        self.assertEqual(prompt.count('(S2)'), 1)

    def test_speaker_ids_follow_first_speech_in_entire_plan_not_character_order(self):
        plan = self.plan_fixture()
        plan['characters'] = [{'id': 'C2', 'name': '男主'}, {'id': 'C1', 'name': '女主'},
                              {'id': 'C3', 'name': '无对白者'}]
        plan['beats'][0]['dialogue'] = [{'speaker': '女主', 'text': '等等。'}]
        plan['beats'][1]['dialogue'] = [{'speaker': 'C2', 'text': '好。'}]
        first = prompts.write_request(plan, plan['beats'][0], {}, 8)[1]['dialogue_slots']
        second = prompts.write_request(plan, plan['beats'][1], {}, 8)[1]['dialogue_slots']
        self.assertEqual(first[0]['speaker_id'], 'S1')
        self.assertEqual(second[0]['speaker_id'], 'S2')

    def test_body_end_anchors_allow_cut_boundary_and_clip_end_without_moving_dialogue(self):
        plan = self.plan_fixture()
        plan['beats'][0]['dialogue'] = [{'speaker': 'C1', 'text': '跟我来。'}]
        _, payload, _ = prompts.write_request(plan, plan['beats'][0], {}, 15)
        raw = self.written_fixture([{'speaker': 'C1', 'text': '跟我来。', 'start': 5, 'end': 6,
                                    'delivery': 'The adult woman says urgently'}])
        raw['integrated_multimodal_description'] = (
            '[Shot 1] A wide shot follows the adults through the rising water. '
            'At 00:05.000, the water reaches their knees. '
            '[Shot 2] At 00:05.000, the camera cuts to a medium shot of their escape. '
            'At 00:15.000, both adults reach the raised stone platform.')
        original = copy.deepcopy(raw)
        prompt = prompts.compile_written(raw, payload)['prompt']
        first, second = prompt.split('[Shot 2]', 1)
        self.assertIn('At 00:05.000, the water reaches their knees.', first)
        self.assertNotIn('<d>[Chinese] 跟我来。</d>', first)
        self.assertEqual(second.count('<d>[Chinese] 跟我来。</d>'), 1)
        self.assertIn('At 00:15.000, both adults reach the raised stone platform.', second)
        self.assertEqual(raw, original)

    def test_body_anchor_past_the_shot_end_is_still_rejected(self):
        plan = self.plan_fixture()
        _, payload, _ = prompts.write_request(plan, plan['beats'][0], {}, 15)
        raw = self.written_fixture()
        raw['integrated_multimodal_description'] = (
            '[Shot 1] A wide shot follows the adults through the rising water. '
            'At 00:05.001, the water reaches their knees. '
            '[Shot 2] At 00:05.000, the camera cuts to a medium shot of their escape. '
            'At 00:15.000, both adults reach the raised stone platform.')
        with self.assertRaisesRegex(ValueError, '已有At时间标记不在对应Shot范围内'):
            prompts.compile_written(raw, payload)

    def test_compile_normalizes_shot_spaces_and_cut_precision_without_changing_raw(self):
        plan = self.plan_fixture()
        _, payload, _ = prompts.write_request(plan, plan['beats'][0], {}, 8)
        for cut, expected in (('0:4.5', '00:04.500'), ('00:04.50', '00:04.500'),
                              ('00:4', '00:04.000'), ('00:04.125', '00:04.125')):
            raw = self.written_fixture()
            raw['integrated_multimodal_description'] = (
                '[Shot1] A medium shot follows the adult toward the doorway. '
                '[ shot  2 ] At %s, the camera cuts to the other side.' % cut)
            original = copy.deepcopy(raw)
            result = prompts.compile_written(raw, payload)
            with self.subTest(cut=cut):
                self.assertIn('[Shot 1]', result['prompt'])
                self.assertIn('[Shot 2] At ' + expected + ',', result['prompt'])
                self.assertEqual(raw, original)
                before_minute, before_second = cut.split(':')
                after_minute, after_second = expected.split(':')
                self.assertEqual(int(before_minute)*60 + float(before_second),
                                 int(after_minute)*60 + float(after_second))

    def test_visual_style_is_transported_to_writer_and_delivery_is_required(self):
        plan = self.plan_fixture()
        plan['beats'][0]['dialogue'] = [{'speaker': 'C1', 'text': '等等。'}]
        system, payload, schema = prompts.write_request(
            plan, plan['beats'][0], {}, 8, visual_style='真人写实电影')
        self.assertEqual(payload['visual_style'], '真人写实电影')
        self.assertIn('live-action, photorealistic, real adult actors', system)
        self.assertIn('关键发色、服装、已设定的面部特征及角色相对位置', system)
        line = {'speaker': 'C1', 'text': '等等。', 'start': 1, 'end': 2}
        with self.assertRaises(ValueError):
            check_instance([line], schema['properties']['dialogue'])
        line['delivery'] = ''
        with self.assertRaises(ValueError):
            check_instance([line], schema['properties']['dialogue'])

    def test_compile_moves_a_leading_style_declaration_inside_shot_one_without_loss(self):
        plan = self.plan_fixture()
        plan['beats'][0]['dialogue'] = [{'speaker': 'C1', 'text': '等等。'}]
        _, payload, _ = prompts.write_request(plan, plan['beats'][0], {}, 8)
        prefix = 'Live-action, photorealistic, real adult actors; cinematic 35mm photography in sunlight.'
        raw = self.written_fixture([{'speaker': 'C1', 'text': '等等。', 'start': 1, 'end': 2,
                                    'delivery': 'The adult woman says urgently'}])
        raw['integrated_multimodal_description'] = (
            prefix + ' [Shot 1] At 00:00.000, the woman reaches the doorway. '
            '[Shot 2] At 00:04.000, the camera cuts to the man waiting outside.')
        original = copy.deepcopy((raw, payload))
        prompt = prompts.compile_written(raw, payload)['prompt']
        description = prompt.split('integrated_multimodal_description: ', 1)[1]
        self.assertTrue(description.startswith('[Shot 1] ' + prefix))
        self.assertEqual(description.count(prefix), 1)
        self.assertIn('the woman reaches the doorway.', description)
        self.assertIn('[Shot 2] At 00:04.000, the camera cuts to the man waiting outside.', description)
        self.assertEqual(description.count('<d>[Chinese] 等等。</d>'), 1)
        self.assertLess(description.index('<d>[Chinese] 等等。</d>'), description.index('[Shot 2]'))
        self.assertEqual((raw, payload), original)

    def test_compile_keeps_unknown_or_timed_preambles_rejected(self):
        plan = self.plan_fixture()
        _, payload, _ = prompts.write_request(plan, plan['beats'][0], {}, 8)
        for prefix in ('Background events happen before the scene.',
                       'Live-action, photorealistic. At 00:01.000, the man crosses the room.',
                       'Live-action, photorealistic. At 0:1, the man crosses the room.'):
            raw = self.written_fixture()
            raw['integrated_multimodal_description'] = prefix + ' ' + raw['integrated_multimodal_description']
            with self.subTest(prefix=prefix), self.assertRaisesRegex(ValueError, '镜头正文须从'):
                prompts.compile_written(raw, payload)

    def test_compile_rejects_raw_source_repetition_and_overlapping_dialogue(self):
        plan = self.plan_fixture()
        plan['beats'][0]['dialogue'] = [{'speaker': 'C1', 'text': '等等。'},
                                      {'speaker': 'C2', 'text': '好。'}]
        _, payload, _ = prompts.write_request(plan, plan['beats'][0], {}, 15)
        lines = [{'speaker': 'C1', 'text': '等等。', 'start': 1, 'end': 3,
                  'delivery': 'The adult woman says gently'},
                 {'speaker': 'C2', 'text': '好。', 'start': 4, 'end': 5,
                  'delivery': 'The adult man replies calmly'}]
        repeated = self.written_fixture(lines)
        repeated['integrated_multimodal_description'] += ' 等等。'
        with self.assertRaises(ValueError):
            prompts.compile_written(repeated, payload)
        overlapping = self.written_fixture(lines)
        overlapping['dialogue'][1]['start'] = 2
        with self.assertRaises(ValueError):
            prompts.compile_written(overlapping, payload)

    def test_invalid_inputs_fail_before_any_model_call(self):
        for count in (True, 0, -1, 1.5):
            with self.subTest(count=count), self.assertRaises(ValueError):
                prompts.plan_request('故事', '', count, 8)
        for seconds in (True, 0, 3.9, 16, float('nan'), float('inf'), '8'):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                prompts.plan_request('故事', '', 2, seconds)
        with self.assertRaises(ValueError):
            prompts.observe_request('anything')
        with self.assertRaises(ValueError):
            prompts.write_request({}, {}, {}, 8, 'ref2va')
        with self.assertRaises(ValueError):
            prompts.plan_request('  ', '', 2, 8)

    def test_requests_and_schemas_are_json_serializable(self):
        plan = self.plan_fixture()
        for request in (prompts.plan_request('故事', '', 2, 8),
                        prompts.review_request(plan, '故事', '', 2, 8),
                        prompts.observe_request('segment', {}, plan['beats'][0]),
                        prompts.write_request(plan, plan['beats'][0], {}, 8)):
            self.assertEqual(json.loads(json.dumps(request, ensure_ascii=False)), list(request))

    def test_review_is_independent_request_with_explicit_categories(self):
        plan = self.plan_fixture()
        observation = {'actual_visible': '只有可见的当前状态', 'unknowns': ['道具位置未知']}
        system, payload, schema = prompts.review_request(
            plan, '安静的关系短片', '自然光线', 2, 8, observation)
        self.assertEqual(payload['stage'], 'review')
        self.assertEqual(payload['brief'], '安静的关系短片')
        self.assertEqual(payload['plan'], plan)
        self.assertEqual(payload['opening_observation'], observation)
        self.assertEqual(set(schema['properties']), {'passed', 'issues', 'strengths'})
        self.assertIn('静谧关系片', system)
        self.assertIn('不要写替代情节', system)
        self.assertIn('passed仅在issues为空时为true', system)
        payload['plan']['beats'].clear()
        payload['opening_observation']['unknowns'].clear()
        self.assertEqual(len(plan['beats']), 2)
        self.assertEqual(observation['unknowns'], ['道具位置未知'])

    def test_review_schema_allows_factual_findings_and_rejects_invalid_verdict_fields(self):
        _, _, schema = prompts.review_request(self.plan_fixture(), '故事', '', 2, 8)
        check_instance({'passed': True, 'issues': [], 'strengths': []}, schema)
        issue = {'beat': 0, 'category': 'causality', 'reason': '结果缺少必要的动作依据',
                 'repair_direction': '明确现有动作与结果的关系，不添加替代故事'}
        check_instance({'passed': False, 'issues': [issue], 'strengths': ['角色数量明确']}, schema)
        for changes in ({'beat': 3}, {'beat': -1}, {'category': 'taste'},
                        {'reason': ''}, {'repair_direction': ''}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                check_instance({'passed': False, 'issues': [dict(issue, **changes)], 'strengths': []}, schema)
        with self.assertRaises(ValueError):
            check_instance({'passed': 'true', 'issues': [], 'strengths': []}, schema)

    def test_blocking_review_is_opt_in_and_preserves_default_contract(self):
        plan = self.plan_fixture()
        default = prompts.review_request(plan, '完整原故事', '', 2, 8)
        explicit_legacy = prompts.review_request(plan, '完整原故事', '', 2, 8, blocking_only=False)
        self.assertEqual(default, explicit_legacy)
        self.assertNotIn('review_policy', default[1])
        self.assertEqual(set(default[2]['properties']['issues']['items']['properties']),
                         {'beat', 'category', 'reason', 'repair_direction'})
        self.assertIn('passed仅在issues为空时为true', default[0])

    def test_blocking_review_schema_requires_severity_and_verifiable_source_field(self):
        plan = self.plan_fixture()
        system, payload, schema = prompts.review_request(
            plan, '执行当前动作后离开', '', 2, 8, blocking_only=True)
        self.assertEqual(payload['review_policy'], 'core_errors_only_v1')
        issue = dict(beat=1, category='capacity', severity='advisory', source_quote='执行当前动作',
                     reason='该动作的细节可以在后续分镜展开。', repair_direction='补充可见的动作过程。')
        check_instance(dict(passed=True, issues=[issue], strengths=[]), schema)
        check_instance(dict(passed=False, issues=[dict(issue, severity='blocking')], strengths=[]), schema)
        for change in ({'severity': 'warning'}, {'source_quote': ''}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                check_instance(dict(passed=True, issues=[dict(issue, **change)], strengths=[]), schema)
        missing = dict(issue)
        missing.pop('source_quote')
        with self.assertRaises(ValueError):
            check_instance(dict(passed=True, issues=[missing], strengths=[]), schema)
        self.assertIn('连续原文片段，逐字引用', system)
        self.assertIn('空泛推测不得blocking', system)
        self.assertIn('仅有advisory时passed必须为true，issues可以非空', system)
        self.assertNotIn('passed仅在issues为空时为true', system)
        self.assertNotIn('有任何需修复问题必须为false', system)

    def test_blocking_review_advice_is_local_and_does_not_mutate_sources(self):
        plan = self.plan_fixture()
        observation = {'actual_visible': '成年人物站在台旁', 'unknowns': ['出口未知']}
        original = copy.deepcopy((plan, observation))
        system, payload, schema = prompts.review_request(
            plan, '按原故事完成', '自然表演', 2, 8, observation, blocking_only=True)
        self.assertIn('可以在后续分镜补充的空间镜头', system)
        self.assertIn('行动者、受事、核心动作、承托关系和触发顺序不能被创作者互换或替代', system)
        self.assertIn('没有详写距离、镜头参照或仅感觉', system)
        self.assertIn('根据明确时间标记或自然发声预算可确定的时长越界', system)
        self.assertIn('不重写用户故事', system)
        payload['plan']['beats'].clear()
        payload['opening_observation']['unknowns'].clear()
        schema['properties']['issues']['items']['properties']['severity']['enum'].clear()
        self.assertEqual((plan, observation), original)
        fresh = prompts.review_request(plan, '按原故事完成', '', 2, 8, blocking_only=True)
        self.assertEqual(fresh[2]['properties']['issues']['items']['properties']['severity']['enum'],
                         ['blocking', 'advisory'])

    def test_planning_keeps_mutable_state_out_of_fixed_identity_and_has_sustained_progress(self):
        system, _, _ = prompts.plan_request('故事', '', 10, 8)
        self.assertIn('characters.appearance只写身份与稳定外貌', system)
        self.assertIn('invariants不能一面永久锁死它的位置', system)
        self.assertIn('不要在中段彻底解决核心问题', system)
        self.assertIn('静谧或关系片', system)

    def test_full_source_story_preflight_preserves_events_words_and_causal_order(self):
        source = '三人看清石碑后撤离。先展示宝石睁眼，再说完原话，最后骤黑。'
        plan_system, payload, _ = prompts.plan_request(source, '真人写实', 10, 15)
        review_system, review_payload, review_schema = prompts.review_request(
            self.plan_fixture(), source, '真人写实', 10, 15)
        self.assertEqual(payload['brief'], source)
        self.assertEqual(review_payload['brief'], source)
        for system in (plan_system, review_system):
            self.assertIn('brief已给完整原故事时', system)
            self.assertIn('关键事件、行动者、触发关系', system)
            self.assertIn('判断', system)
            self.assertIn('确认', system)
        self.assertIn('石碑、标识', plan_system)
        self.assertIn('发现与阅读动作', plan_system)
        self.assertIn('画内文字不自动转成角色台词', plan_system)
        self.assertIn('真正遗漏、改动核心事实或指定结尾才列阻塞问题', review_system)
        self.assertIn('不得要求作者把指定反转改成另一个结局', review_system)
        self.assertEqual(set(review_schema['properties']), {'passed', 'issues', 'strengths'})
        self.assertEqual(review_schema['properties']['issues']['items']['properties']['category']['enum'],
                         ['pacing', 'causality', 'continuity', 'ending', 'capacity'])

    def test_final_writer_orders_reveal_speech_and_visible_black_within_clip(self):
        plan = self.plan_fixture()
        plan['beats'][-1]['dialogue'] = [{'speaker': 'C1', 'text': '那不是宝藏。'}]
        system, payload, schema = prompts.write_request(plan, plan['beats'][-1], {}, 15)
        self.assertTrue(payload['is_final'])
        self.assertEqual(payload['seconds'], 15)
        self.assertIn('dialogue的start/end须与timeline及Shot正文的语义时序相符', system)
        self.assertIn('所有对白end不晚于黑场开始', system)
        self.assertIn('在seconds以内预留数帧黑场', system)
        self.assertIn('黑场开始时间必须小于seconds', system)
        self.assertIn('不互相依赖的自然动作与短对白并行', system)
        self.assertEqual(payload['dialogue_slots'][0]['text'], '那不是宝藏。')
        self.assertEqual(set(schema['properties']), {
            'integrated_multimodal_description', 'overall_soundscape', 'non_diegetic_music',
            'expected_end_state', 'brief', 'timeline', 'dialogue', 'continuity_notes'})

    def test_pacing_budget_is_computed_consistently_for_plan_and_review(self):
        for count, resolution, epilogue in ((2, None, None), (5, None, None),
                                            (6, 5, 1), (10, 8, 2), (11, 9, 2)):
            plan_payload = prompts.plan_request('完整故事', '', count, 8)[1]
            review_payload = prompts.review_request(self.plan_fixture(), '完整故事', '', count, 8)[1]
            expected = {'enforced': count >= 6,
                        'main_resolution_not_before': resolution,
                        'max_epilogue_beats': epilogue}
            with self.subTest(count=count):
                self.assertEqual(plan_payload['pacing_budget'], expected)
                self.assertEqual(review_payload['pacing_budget'], expected)

    def test_revision_rebuilds_rejected_main_line_and_exceptions_use_user_evidence(self):
        plan_system = prompts.plan_request('允许有明确理由的长尾声', '', 10, 8)[0]
        review_system = prompts.review_request(self.plan_fixture(), '允许有明确理由的长尾声', '', 10, 8)[0]
        self.assertIn('review_feedback与previous_draft', plan_system)
        self.assertIn('表示旧稿已被拒绝', plan_system)
        self.assertIn('新稿须重建主线解决过程', plan_system)
        self.assertIn('最早已经彻底解决主线的段号', review_system)
        self.assertIn('用户在brief明确要求长尾声', review_system)
        self.assertIn('在strengths说明引用依据', review_system)

    def test_only_immediate_zero_time_after_first_shot_is_implicit(self):
        plan = self.plan_fixture()
        _, payload, _ = prompts.write_request(plan, plan['beats'][0], {}, 8)
        raw = self.written_fixture()
        raw['integrated_multimodal_description'] = (
            '[Shot1] At 00:00.000, the adult stands by the doorway. '
            'At 00:02.500, the adult turns toward it. '
            '[Shot2] At 00:04.0, the camera cuts to the opposite side.')
        original = copy.deepcopy(raw)
        prompt = prompts.compile_written(raw, payload)['prompt']
        self.assertNotIn('At 00:00.000', prompt)
        self.assertIn('[Shot 1] the adult stands', prompt)
        self.assertIn('At 00:02.500', prompt)
        self.assertIn('[Shot 2] At 00:04.000', prompt)
        self.assertEqual(raw, original)


if __name__ == '__main__':
    unittest.main()
