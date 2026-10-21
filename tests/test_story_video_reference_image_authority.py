"""Image authority and legacy-cache compatibility; no model or production media."""
import copy
import hashlib
import json
import unittest

from cores import story_video_reference_prompts as prompts


def fixture():
    person = dict(id='char_01', name='林舟', appearance='成年男子，黑发，灰色衬衣', role='访客')
    beat = dict(index=1, seconds=15, goal='找到方向', action='观察并转身', turn='确认方向',
                emotion='平静', ending='转向前方', cast=['char_01'], dialogue=[])
    plan = dict(title='普通人物测试', logline='访客观察并转身', ending='确认方向',
                characters=[person], props=[], invariants=[],
                beats=[beat, dict(beat, index=2)])
    # A deliberate observation mismatch stays evidence; it must not redefine the reference.
    state = dict(characters=[dict(id='char_01', appearance='红发，蓝色外套', position='画面左侧',
        pose='侧身站立', hands='放在身旁', visibility='visible', certainty='observed')],
        actual_visible='人物侧身站立', scene='unknown', unknowns=[])
    first = [dict(role='character', picture=1, subject=1, character_id='char_01',
                  name='林舟', path='ordinary_character.png')]
    scene = first + [dict(role='scene_anchor', picture=2, path='ordinary_background.png')]
    later = first + [dict(role='previous_frame', picture=2, path='actual_last_frame.png')]
    raw = dict(integrated_multimodal_description='[Shot 1] 中景呈现<Subject 1>观察前方，前五秒抬眼，中间五秒侧身。\n\n[Shot 2] At 00:10.000, 镜头切至近景，最后五秒呈现自然回应。',
        overall_soundscape='轻微衣物摩擦声。', non_diegetic_music='N/A',
        expected_end_state='人物侧身面向前方。', brief='观察后确认方向。',
        timeline=[dict(start=start, end=start + 5, action='自然观察', visible_result='方向清楚')
                  for start in (0, 5, 10)], dialogue=[], continuity_notes=[])
    return plan, beat, state, first, scene, later, raw


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def legacy_requests(module, optimized):
    plan, beat, state, first, scene, later, raw = fixture()
    planned = module.plan_request('访客观察后确认方向。', '真人写实', 2, 15, state,
                                  flow_optimization=optimized)
    reviewed = module.review_request(plan, '访客观察后确认方向。', '真人写实', 2, 15,
                                     state, blocking_only=True)
    written = module.write_request(plan, beat, state, 15, mode='ref2va',
        visual_style='真人写实', references=later, flow_optimization=optimized)
    revised = module.revision_request(raw, written[1], dict(passed=False, issues=[]))
    compiled = module.compile_written(raw, written[1])
    return planned, reviewed, written, revised, compiled


class ReferenceImageAuthorityTests(unittest.TestCase):
    def request(self, *, scene=False, later=False, mode='ref2va'):
        plan, beat, state, first, scene_refs, later_refs, raw = fixture()
        refs = later_refs if later else scene_refs if scene else first
        return prompts.write_request(plan, beat, state, 15, mode=mode,
            visual_style='真人写实', references=refs, flow_optimization=True,
            reference_visual_policy=prompts.REFERENCE_VISUAL_POLICY,
            scene_reference_provided=scene), raw

    def test_planning_and_review_share_image_rule_and_story_backgrounds(self):
        plan, _, state, _, _, _, _ = fixture()
        for scene in (False, True):
            with self.subTest(scene=scene):
                options = dict(reference_visual_policy=prompts.REFERENCE_VISUAL_POLICY,
                               scene_reference_provided=scene)
                planned = prompts.plan_request('访客观察后确认方向。', '真人写实', 2, 15,
                    state, flow_optimization=True, **options)
                reviewed = prompts.review_request(plan, '访客观察后确认方向。', '真人写实',
                    2, 15, state, blocking_only=True, **options)
                for system, payload, _ in (planned, reviewed):
                    self.assertIn(prompts.reference_visual_rule(scene), system)
                    self.assertEqual(payload['reference_visual_policy'], prompts.REFERENCE_VISUAL_POLICY)
                    self.assertEqual(payload['scene_reference_provided'], scene)
                self.assertIn('故事留白时补足', planned[0])

    def test_first_without_scene_uses_story_background_and_keeps_observation(self):
        request, _ = self.request()
        system, payload, _ = request
        self.assertIn('未提供场景参考时，根据故事地点、时代与剧情生成场景', system)
        self.assertIn('不重新设计、美化或补造服饰配饰', system)
        self.assertNotIn('t2va首段必须读取', system)
        self.assertNotIn('事实优先级：当前实际画面与actual_state', system)
        self.assertIn('故事留白时补足', system)
        self.assertNotIn('未提供场景参考图时使用空白背景', system)
        self.assertEqual(payload['actual_state'], fixture()[2])
        self.assertEqual(payload['visual_style'], '真人写实')
        self.assertEqual(len(payload['canonical_references']), 1)
        self.assertIn('dialogue_slots是唯一逐字对白来源', system)

    def test_background_reference_survives_later_segment_without_scene_slot(self):
        for later in (False, True):
            with self.subTest(later=later):
                (system, payload, _), raw = self.request(scene=True, later=later)
                self.assertIn('场景参考用于其对应地点，其他地点按故事生成场景，回访时沿用参考', system)
                self.assertNotIn('未提供场景参考图时使用空白背景', system)
                if later:
                    self.assertIn('展示反应或改变地点', system)
                self.assertEqual(payload['canonical_references'][-1]['role'],
                                 'previous_frame' if later else 'scene_anchor')
                final = prompts.compile_written(raw, payload)
                for field in ('subject_definitions', 'retention_analysis'):
                    self.assertIn(prompts.reference_visual_rule(True), final['fields'][field])
                if not later:
                    self.assertIn('允许剧情转去其他地点', final['prompt'])
                self.assertNotIn('不自行新增地点、建筑或陈设', final['prompt'])

    def test_final_h3_binds_rule_and_does_not_insert_restyled_appearance(self):
        (_, payload, _), raw = self.request(later=True)
        before = copy.deepcopy((payload, raw))
        result = prompts.compile_written(raw, payload)
        for field in ('subject_definitions', 'retention_analysis'):
            self.assertIn(prompts.reference_visual_rule(False), result['fields'][field])
        self.assertIn('<Subject 1>是人物char_01', result['prompt'])
        self.assertIn('外观完整沿用<Picture 1>', result['fields']['subject_definitions'])
        self.assertNotIn('年龄以设定为准', result['fields']['retention_analysis'])
        self.assertIn('<Picture 2>是本组上一段的真实最后一帧', result['prompt'])
        self.assertNotIn('真人写实电影摄影', result['prompt'])
        self.assertFalse(any(row.get('kind') == 'requested_live_action_style_zh'
            for row in result.get('automatic_adjustments', [])))
        self.assertEqual((payload, raw), before)

    def test_revision_preserves_policy_background_and_dialogue_slots(self):
        request, raw = self.request(scene=True, later=True)
        revised = prompts.revision_request(raw, request[1], dict(passed=False, issues=[]))
        self.assertIn(prompts.reference_visual_rule(True), revised[0])
        for key in ('reference_visual_policy', 'scene_reference_provided', 'dialogue_slots',
                    'actual_state', 'canonical_references', 'continuation_contract'):
            self.assertEqual(revised[1][key], request[1][key])
        self.assertEqual(revised[2], request[2])
        self.assertIn(prompts.reference_visual_rule(True),
                      prompts.compile_written(raw, revised[1])['prompt'])

    def test_reference_policy_does_not_change_text_or_image_mode(self):
        plan, beat, state, _, _, _, _ = fixture()
        for mode in ('i2va', 't2va'):
            with self.subTest(mode=mode):
                old = prompts.write_request(plan, beat, state, 15, mode=mode,
                                             visual_style='真人写实', flow_optimization=True)
                new, _ = self.request(mode=mode)
                self.assertEqual(new, old)

    def test_legacy_request_and_compiled_prompt_signatures_are_unchanged(self):
        # Frozen before this patch, covering request strings/schema/payload and H3 output.
        expected = {
            False: 'ec36d978fec9442d15ee123e96287f1a18a627ee2dd593c03189239dd8be6783',
            True: '20b9d3f801d8649649abd0ec9a430c72b93492f57f79f9f30b01165a381a7ab5',
        }
        for optimized in (False, True):
            with self.subTest(optimized=optimized):
                self.assertEqual(digest(legacy_requests(prompts, optimized)), expected[optimized])
        plan, beat, state, _, _, later, _ = fixture()
        old = prompts.write_request(plan, beat, state, 15, mode='ref2va', references=later)
        unknown = prompts.write_request(plan, beat, state, 15, mode='ref2va', references=later,
                                        reference_visual_policy='unrecognized', scene_reference_provided=True)
        self.assertEqual(unknown, old)


if __name__ == '__main__':
    unittest.main()
