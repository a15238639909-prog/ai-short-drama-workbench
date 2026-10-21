"""No-model Chinese director-policy isolation and transport contracts."""
import copy
import unittest

from cores import story_video_mode_prompts as modes
from cores import story_video_prompts as base
from cores import story_video_reference_prompts as reference
from tests.test_story_video_reference_prompts import plan_and_raw


class StoryVideoModePromptTests(unittest.TestCase):
    def test_profile_and_factory_reject_other_input_modes(self):
        self.assertEqual(modes.PROFILE, 'story-mode-director-zh-v1')
        self.assertEqual(modes.I2VA_FIRST_FRAME, base.I2VA_FIRST_FRAME)
        for invalid in ('reference', 'i2va', '', None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                modes.for_mode(invalid)

    def test_every_request_has_fixed_task_mode_markers_and_isolated_inputs(self):
        for mode in ('image', 'text'):
            adapter = modes.for_mode(mode)
            plan, beat, _ = plan_and_raw()
            state = dict(characters=[dict(id='char_01', pose='抬头')], scene='桥边')
            snapshot = copy.deepcopy((plan, beat, state))
            requests = [adapter.plan_request('同伴发现线索后回家。', '真人写实', 2, 15, state),
                adapter.review_request(plan, '同伴发现线索后回家。', '真人写实', 2, 15, state, blocking_only=True),
                adapter.write_request(plan, beat, state, 15, mode='i2va', visual_style='真人写实'),
                adapter.observe_request('segment', state, beat)]
            for _, payload, schema in requests:
                self.assertEqual(payload['director_profile'], modes.PROFILE)
                self.assertEqual(payload['story_input_mode'], mode)
                if isinstance(payload.get('previous_state'), dict):
                    payload['previous_state']['characters'][0]['pose'] = '不得污染输入'
                schema['required'].append('只改本次返回副本')
            self.assertEqual((plan, beat, state), snapshot)

    def test_text_continuation_i2va_retains_text_policy(self):
        plan, beat, _ = plan_and_raw()
        system, payload, _ = modes.for_mode('text').write_request(plan, beat, {}, 15, mode='i2va')
        self.assertEqual(payload['mode'], 'i2va')
        self.assertEqual(payload['story_input_mode'], 'text')
        for rule in ('主体有真实动态并与环境互动', '前后景纵深', '尊重安静题材', '不规定镜头数'):
            self.assertIn(rule, system)
        self.assertNotIn('尽可能延续当前场景及空间关系', system)
        image, _, _ = modes.for_mode('image').write_request(plan, beat, {}, 15)
        self.assertIn('尽可能延续当前场景及空间关系', image)
        self.assertIn('不强制转正、直视镜头、冻结或摆拍', image)
        self.assertNotIn('文生导演策略', image)

    def test_image_observer_records_current_face_facts_and_visible_scene_anchors(self):
        old = dict(characters=[dict(id='char_01', face_view='front', face_clarity='clear')],
                   scene_anchors=['旧桥'])
        system, payload, schema = modes.for_mode('image').observe_request('segment', old)
        person = schema['properties']['characters']['items']
        self.assertEqual(person['properties']['face_view']['enum'],
                         ['front', 'three_quarter', 'profile', 'back', 'occluded', 'offscreen', 'unknown'])
        self.assertEqual(person['properties']['face_clarity']['enum'],
                         ['clear', 'blurred', 'occluded', 'too_small', 'not_visible', 'unknown'])
        self.assertTrue({'face_view', 'face_clarity'} <= set(person['required']))
        self.assertEqual(schema['properties']['scene_anchors']['type'], 'array')
        self.assertIn('scene_anchors', schema['required'])
        for evidence_rule in ('不复制previous_state中的脸部结论', '背影不写正面', '当前出画写offscreen/not_visible',
                              '不能复制旧场景的不可见地标', '仅记录风险，不自动拒绝视频或要求重拍'):
            self.assertIn(evidence_rule, system)
        self.assertNotIn('default', person['properties']['face_clarity'])
        payload['previous_state']['scene_anchors'].clear()
        self.assertEqual(old['scene_anchors'], ['旧桥'])

    def test_text_observer_adds_no_face_schema_or_face_obligation(self):
        system, _, schema = modes.for_mode('text').observe_request('opening')
        person = schema['properties']['characters']['items']
        self.assertNotIn('face_view', person['properties'])
        self.assertNotIn('face_clarity', person['required'])
        self.assertNotIn('scene_anchors', schema['properties'])
        self.assertNotIn('逐一在appearance、pose或continuity_risks', system)
        self.assertIn('不额外逐人检查露脸角度或清晰度', system)
        self.assertEqual(schema['properties']['actions']['maxItems'], 0)

    def test_reviews_keep_director_preferences_advisory_and_real_contracts_intact(self):
        plan, _, _ = plan_and_raw()
        for mode in ('image', 'text'):
            system, payload, _ = modes.for_mode(mode).review_request(plan, '完整故事', '真人写实', 2, 15,
                                                                    blocking_only=True)
            self.assertEqual(payload['review_policy'], 'core_errors_only_v1')
            self.assertIn('不能成为新增阻断标准', system)
            self.assertIn('原对白、因果、结尾与实际时长', system)
            self.assertIn('仅审美偏好不加入要求修复的issues', system)

    def test_review_separates_per_segment_and_total_duration_evidence(self):
        plan, _, _ = plan_and_raw()
        for mode in ('image', 'text'):
            system, payload, schema = modes.for_mode(mode).review_request(
                plan, '完整故事', '真人写实', 5, 15, blocking_only=True)
            self.assertEqual(payload['review_evidence_policy'], 'story-review-evidence-v2')
            self.assertEqual(payload['timing_contract'], dict(segment_count=5,
                segment_seconds=15.0, total_seconds=75.0, seconds_scope='per_segment'))
            self.assertEqual(payload['duration_contract_text'],
                '共5段，每段15秒，全片75秒。seconds与timing_budget仅约束单段。')
            self.assertEqual(payload['parameter_sources'], [payload['duration_contract_text']])
            self.assertEqual(payload['timing_budget']['seconds'], 15.0)
            self.assertIn('或parameter_sources中单条字符串的连续原文片段', system)
            self.assertNotIn(modes._REVIEW_SOURCE_RULE, system)
            for rule in ('timing_budget只约束单段，不是全片总容量',
                         '不得把多段时长相加后与单段seconds比较',
                         '不引用JSON键、数字或自行拼接的字段表达式',
                         '任何建议若没有可靠且可逐字引用的依据就不列issues',
                         '对确有明确依据的核心错误仍按原blocking规则处理'):
                self.assertIn(rule, system)
            self.assertIn('source_quote', schema['properties']['issues']['items']['required'])

    def test_review_duration_evidence_is_calculated_from_actual_request(self):
        plan, _, _ = plan_and_raw()
        for count, seconds, expected_total in ((2, 15, 30), (3, 7.5, 22.5)):
            for blocking_only in (False, True):
                _, payload, _ = modes.for_mode('text').review_request(
                    plan, '完整故事', '真人写实', count, seconds, blocking_only=blocking_only)
                self.assertEqual(payload['timing_contract']['segment_count'], count)
                self.assertEqual(payload['timing_contract']['segment_seconds'], seconds)
                self.assertEqual(payload['timing_contract']['total_seconds'], expected_total)
                expected = (f'共{count}段，每段{seconds:g}秒，全片{expected_total:g}秒。'
                            'seconds与timing_budget仅约束单段。')
                self.assertEqual(payload['duration_contract_text'], expected)
                self.assertEqual(payload['parameter_sources'], [expected])

    def test_chinese_i2va_t2va_compile_uses_only_official_fixed_english_prefix(self):
        for task_mode in ('image', 'text'):
            for video_mode in ('i2va', 't2va'):
                plan, beat, raw = plan_and_raw()
                adapter = modes.for_mode(task_mode)
                _, payload, _ = adapter.write_request(plan, beat, {}, 15, mode=video_mode, visual_style='真人写实')
                snapshot = copy.deepcopy((raw, payload))
                actual = adapter.compile_written(raw, payload)
                expected = reference.compile_written(raw, payload)
                if video_mode == 'i2va':
                    expected['prompt'] = base.I2VA_FIRST_FRAME + expected['prompt'][len(reference.I2VA_FIRST_FRAME):]
                    self.assertTrue(actual['prompt'].startswith(base.I2VA_FIRST_FRAME + '\n\n'))
                else:
                    self.assertNotIn(base.I2VA_FIRST_FRAME, actual['prompt'])
                    self.assertNotIn('<Picture 1>', actual['prompt'])
                self.assertEqual(actual, expected)
                self.assertEqual((raw, payload), snapshot)
                for field in ('integrated_multimodal_description', 'overall_soundscape', 'non_diegetic_music'):
                    self.assertIn(field + ': ', actual['prompt'])
                self.assertIn('真人写实电影摄影', actual['prompt'])
                self.assertIn('自然清楚地回应', actual['prompt'])
                self.assertNotIn('real adult actors', actual['prompt'])

    def test_unbound_references_and_field_injection_still_fail(self):
        adapter = modes.for_mode('image')
        plan, beat, raw = plan_and_raw()
        _, payload, _ = adapter.write_request(plan, beat, {}, 15)
        for invalid in ('<Subject 1>正在回应。', '<Picture 2>提供背景。', 'prompt: 未经允许的包装'):
            wrong = copy.deepcopy(raw)
            wrong['integrated_multimodal_description'] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                adapter.compile_written(wrong, payload)
        with self.assertRaises(ValueError):
            adapter.compile_written(raw, dict(payload, mode='ref2va'))

    def test_chinese_explicit_ranges_are_supported_without_rewriting_dialogue(self):
        plan, beat, raw = plan_and_raw()
        raw['integrated_multimodal_description'] = raw['integrated_multimodal_description'].replace(
            '[Shot 2] At 00:10.000,', '10～15秒：[Shot 2]')
        before = copy.deepcopy(raw)
        adapter = modes.for_mode('text')
        _, payload, _ = adapter.write_request(plan, beat, {}, 15, mode='t2va')
        result = adapter.compile_written(raw, payload)
        self.assertIn('[Shot 2] At 00:10.000,', result['prompt'])
        self.assertTrue(any(row['kind'] == 'explicit_chinese_shot_range'
                            for row in result['automatic_adjustments']))
        self.assertEqual(raw, before)
        self.assertEqual([row['text'] for row in result['dialogue']], [row['text'] for row in raw['dialogue']])

    def test_reference_and_base_requests_are_unchanged_after_adapter_calls(self):
        plan, beat, raw = plan_and_raw()
        original_systems = (reference.WRITE_SYSTEM, base._WRITE_SYSTEM, reference.I2VA_FIRST_FRAME)
        before = (reference.plan_request('完整故事', '真人写实', 2, 15), reference.observe_request('segment'),
                  reference.review_request(plan, '完整故事', '真人写实', 2, 15, blocking_only=True),
                  base.review_request(plan, '完整故事', '真人写实', 2, 15, blocking_only=True))
        for mode in ('image', 'text'):
            adapter = modes.for_mode(mode)
            adapter.observe_request('segment')
            adapter.review_request(plan, '完整故事', '真人写实', 5, 15, blocking_only=True)
            _, payload, _ = adapter.write_request(plan, beat, {}, 15)
            adapter.compile_written(raw, payload)
        after = (reference.plan_request('完整故事', '真人写实', 2, 15), reference.observe_request('segment'),
                 reference.review_request(plan, '完整故事', '真人写实', 2, 15, blocking_only=True),
                 base.review_request(plan, '完整故事', '真人写实', 2, 15, blocking_only=True))
        self.assertEqual(before, after)
        self.assertEqual(original_systems, (reference.WRITE_SYSTEM, base._WRITE_SYSTEM, reference.I2VA_FIRST_FRAME))


if __name__ == '__main__':
    unittest.main()
