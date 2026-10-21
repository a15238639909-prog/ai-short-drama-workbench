"""Zero-model tests for role-scoped scenery and actual-tail continuation."""
import copy
import unittest

from cores import story_video_prompts as base
from cores import story_video_reference_prompts as prompts
from cores import story_video_characters as characters
from tests.test_story_video_reference_prompts import cards, plan_and_raw


def material():
    return dict(actual_visible='人物站在纯白背景前。', unknowns=['白色摄影棚的尺寸未知。'],
        characters=[dict(id='char_01', appearance='黑色短发，蓝色外套，身后纯白背景，脸上有浅色雀斑',
            position='摄影棚中央', pose='正面站立', hands='握着旧书', visibility='visible', certainty='observed')],
        props=[dict(id='book', appearance='旧书', holder='char_01')], scene='纯白背景的摄影棚。',
        water='地面没有水。', actions=['走到中央'], completed=['已经迎接同伴'], missing=['摄影棚边界'],
        continuity_risks=['纯白背景需要保留。'], beat_status='achieved', keep_end_seconds=15,
        keep_end_reason='已经完成动作。')


class ReferenceSceneContinuityTests(unittest.TestCase):
    def test_character_only_reference_cannot_supply_world_or_actions(self):
        raw = material()
        bindings = [dict(image_index=1, role='character', character_id='char_01', subject=1)]
        snapshot = copy.deepcopy((raw, bindings))
        result = prompts.sanitize_reference_observation(raw, bindings)
        self.assertEqual(result['scene'], 'unknown')
        self.assertEqual(result['water'], 'unknown')
        self.assertEqual(result['props'], [])
        self.assertEqual(result['actions'], [])
        self.assertEqual(result['completed'], [])
        self.assertEqual(result['beat_status'], 'unknown')
        self.assertIsNone(result['keep_end_seconds'])
        self.assertNotIn('纯白', str(result))
        self.assertNotIn('摄影棚', str(result))
        self.assertIn('黑色短发', result['characters'][0]['appearance'])
        self.assertIn('蓝色外套', result['characters'][0]['appearance'])
        self.assertIn('雀斑', result['characters'][0]['appearance'])
        for field in ('position', 'pose', 'hands'):
            self.assertEqual(result['characters'][0][field], 'unknown')
        self.assertEqual((raw, bindings), snapshot)

    def test_scene_role_preserves_environment_without_card_poses(self):
        for role in ('scene', 'scene_anchor'):
            with self.subTest(role=role):
                raw = material()
                raw.update(scene='林间溪谷中有石桥。', water='溪流向石桥下方流动。')
                result = prompts.sanitize_reference_observation(raw,
                    [dict(role='character', character_id='char_01'), dict(role=role)])
                self.assertEqual((result['scene'], result['water']), (raw['scene'], raw['water']))
                self.assertEqual(result['characters'][0]['pose'], 'unknown')
                self.assertEqual(result['actions'], [])

    def test_scene_alone_does_not_create_a_character(self):
        result = prompts.sanitize_reference_observation(material(), [dict(role='scene_anchor')])
        self.assertEqual(result['characters'], [])

    def test_nonmatching_identity_and_background_only_appearance_remain_unknown(self):
        raw = material()
        raw['characters'][0]['appearance'] = '纯白背景，摄影棚中的人物'
        result = prompts.sanitize_reference_observation(raw, [dict(role='character', character_id='char_01')])
        self.assertEqual(result['characters'][0]['appearance'], 'unknown')
        result = prompts.sanitize_reference_observation(raw, [dict(role='character', character_id='char_02')])
        self.assertEqual(result['characters'], [])

    def test_no_scene_planner_and_director_use_story_world_instead_of_card_background(self):
        plan, beat, _ = plan_and_raw()
        planned = prompts.plan_request('两位旅人穿越溪谷，在石桥旁找到丢失的铃铛。', '真人写实', 2, 15,
                                       flow_optimization=True)
        refs = characters.references_for_segment(cards())
        written = prompts.write_request(plan, beat, {}, 15, mode='ref2va', references=refs,
                                        flow_optimization=True)
        for request in (planned, written):
            self.assertIn('没有场景参考图时，按照本轮故事', request[0])
            self.assertIn('只有故事明确要求白底空间时才安排白底', request[0])
            self.assertIn('人物卡背景重置实际场景', request[0])
        self.assertNotIn('continuation_contract', written[1])

    def test_previous_tail_contract_contains_actual_facts_and_soft_reference(self):
        plan, beat, _ = plan_and_raw()
        state = dict(scene='石桥旁', characters=[dict(id='char_01', pose='坐在桥边', hands='拿着铃铛')],
                     actions=['坐下'], completed=['已经将铃铛交还同伴'])
        refs = characters.references_for_segment(cards(), previous_frame='actual_last_frame.png')
        snapshot = copy.deepcopy((state, refs, plan))
        system, payload, _ = prompts.write_request(plan, beat, state, 15, mode='ref2va', references=refs,
                                                  flow_optimization=True)
        contract = payload['continuation_contract']
        self.assertEqual(contract['previous_frame_path'], 'actual_last_frame.png')
        self.assertEqual(contract['previous_observation'], state)
        self.assertEqual(contract['reference_kind'], 'soft')
        self.assertIn('本段首镜先接续尾帧', system)
        self.assertIn('已实际完成的动作不重新开始', system)
        self.assertIn('接续后可按剧情切多个景别', system)
        self.assertIn('不宣称多图参考硬锁首帧', system)
        self.assertEqual((state, refs, plan), snapshot)
        contract['previous_observation']['scene'] = 'changed'
        self.assertEqual(state['scene'], '石桥旁')

    def test_revision_preserves_runtime_enriched_continuation_contract(self):
        plan, beat, raw = plan_and_raw()
        refs = characters.references_for_segment(cards(), previous_frame='real_tail.png')
        _, payload, _ = prompts.write_request(plan, beat, {}, 15, mode='ref2va', references=refs,
                                              flow_optimization=True)
        payload['continuation_contract']['previous_observation'] = dict(completed=['已经坐下'])
        payload['continuation_contract']['previous_frame_path'] = 'verified_real_tail.png'
        system, revised, _ = prompts.revision_request(raw, payload, dict(passed=False, issues=[]))
        self.assertEqual(revised['continuation_contract'], payload['continuation_contract'])
        self.assertIn('本段首镜先接续尾帧', system)

    def test_base_prompt_is_not_changed_and_default_modes_stay_compatible(self):
        original = base._WRITE_SYSTEM
        plan, beat, _ = plan_and_raw()
        for mode in ('i2va', 't2va'):
            system, payload, _ = prompts.write_request(plan, beat, {}, 15, mode=mode)
            self.assertNotIn('continuation_contract', payload)
            self.assertNotIn('本段首镜先接续尾帧', system)
        self.assertEqual(base._WRITE_SYSTEM, original)

    def test_reference_default_and_false_flag_keep_legacy_request_and_retention(self):
        plan, beat, raw = plan_and_raw()
        refs = characters.references_for_segment(cards(), previous_frame='real_tail.png')
        default = prompts.write_request(plan, beat, {}, 15, mode='ref2va', references=refs)
        explicit = prompts.write_request(plan, beat, {}, 15, mode='ref2va', references=refs,
                                          flow_optimization=False)
        self.assertEqual(default, explicit)
        self.assertNotIn('flow_optimization', default[1])
        self.assertNotIn('continuation_contract', default[1])
        self.assertNotIn('本段首镜先接续尾帧', default[0])
        self.assertNotIn('没有场景参考图时，按照本轮故事', default[0])
        result = characters.bind_delivery(raw, default[1], cards(), refs)
        self.assertIn('部分保留实际末态与动作关系', result['prompt'])
        enabled = prompts.write_request(plan, beat, {}, 15, mode='ref2va', references=refs,
                                         flow_optimization=True)
        result = characters.bind_delivery(raw, enabled[1], cards(), refs)
        self.assertIn('优先完整承接上一帧可见的姿态、位置布局', result['prompt'])
        self.assertIn('软参考，不是硬首帧保证', result['prompt'])
        planned = prompts.plan_request('旅人寻找铃铛。', '真人写实', 2, 15)
        self.assertNotIn('flow_optimization', planned[1])
        self.assertNotIn('没有场景参考图时，按照本轮故事', planned[0])


if __name__ == '__main__':
    unittest.main()
