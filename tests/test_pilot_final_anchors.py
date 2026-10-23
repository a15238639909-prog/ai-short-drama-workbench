"""Pure checks for the opt-in final H3 visual anchors; never call a model."""
import copy
import hashlib
import json
import re
import unittest
from unittest.mock import patch

from cores import workbench_pilot_continuity as continuity
from cores import story_video_reference_prompts as director
from cores import video_continuity


def fixture():
    fields = dict(subject_definitions=(
        '<Subject 1> is 剑修, the character from <Picture 1>.\n'
        '<Subject 2> is 方士, the character from <Picture 2>.\n'
        '<Subject 3> is 广场, the environment.'), summary='两人配合脱困。',
        retention_analysis='人物沿用已绑定参考。',
        detailed_description=('[Shot 1] <Subject 1>疾行格挡，<Subject 2>从腰间取下玉环。\n'
            '[Shot 2] At 00:06.000, 三具铜甲卫士挥刃，两人协作脱困。'),
        overall_soundscape='金属碰撞。<Subject 1>说：<d>一起走。<d>', non_diegetic_music='N/A')
    compiled = dict(fields=fields, dialogue=[dict(speaker='hero', text='一起走。',
        start=4, end=5, delivery='坚定地发声。')], timeline=[dict(start=0, end=15,
        action='两人配合脱困。')], environment_subject=3)
    compiled['prompt'] = '\n\n'.join(key + ':\n' + fields[key] for key in director.FIELDS)
    payload = dict(final_anchor_policy='render-anchors-v1', seconds=15,
        beat=dict(index=1, goal='两人脱困。', action='方士取下玉环，三具铜甲卫士围攻。',
                  ending='两人站定。', cast=['hero', 'mage', 'guards']),
        stable_visual_contract=dict(scene=dict(id='B', name='广场',
            description='金色黄昏，白玉地面，四根青铜柱与中央矮台。'),
            props=[dict(id='ring', name='玉环', appearance='乳白半透明玉质，内侧细金纹。', count=1),
                   dict(id='guards', name='铜甲卫士', appearance='铜绿甲片，胸口青色方形核心。', count=7),
                   dict(id='later', name='赤晶棱柱', appearance='红色晶体，六棱长柱。', count=1)]))
    return compiled, payload


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class FinalPromptAnchorTests(unittest.TestCase):
    def test_final_fields_keep_exact_shots_speech_bindings_and_only_current_props(self):
        compiled, payload = fixture()
        before = copy.deepcopy((compiled, payload))
        result = continuity.finalize_prompt_anchors(compiled, payload)
        self.assertEqual(set(result['fields']), set(director.FIELDS))
        self.assertIn(payload['stable_visual_contract']['scene']['description'], result['prompt'])
        self.assertIn('乳白半透明玉质，内侧细金纹。', result['prompt'])
        self.assertIn('铜绿甲片，胸口青色方形核心。', result['prompt'])
        self.assertNotIn('赤晶棱柱', result['prompt'])
        self.assertNotIn('7', result['prompt'])
        self.assertIn('三具铜甲卫士', result['prompt'])
        self.assertIn('跨场景总量不展开为当前出镜数量', result['prompt'])
        self.assertEqual(result['final_anchor_audit']['prop_ids'], ['ring', 'guards'])
        self.assertEqual(digest(result['dialogue']), digest(compiled['dialogue']))
        self.assertEqual(result['timeline'], compiled['timeline'])
        for field in ('detailed_description', 'overall_soundscape', 'non_diegetic_music', 'summary'):
            self.assertEqual(result['fields'][field], compiled['fields'][field])
        labels = lambda value: re.findall(r'<(?:Subject|Picture)\s+\d+>', value)
        self.assertEqual(labels(result['prompt']), labels(compiled['prompt']))
        self.assertEqual(continuity.finalize_prompt_anchors(result, payload), result)
        self.assertEqual((compiled, payload), before)

    def test_real_main_scope_retains_final_anchors_and_arrival_scoping(self):
        compiled, payload = fixture()
        result = continuity.finalize_prompt_anchors(compiled, payload)
        prompt = video_continuity.scope_prompt(result['prompt'],
            dict(plan=dict(flow='source-v1'), source_text='两人从门口进入广场。'),
            dict(scene_transition=dict(from_scene='门口', to='广场')))
        self.assertIn(payload['stable_visual_contract']['scene']['description'], prompt)
        self.assertIn('乳白半透明玉质，内侧细金纹。', prompt)
        self.assertIn('arrival destination only', prompt)
        self.assertEqual(prompt.count('固定地点外观】'), 1)
        self.assertEqual(re.findall(r'(?m)^([a-z_]+):$', prompt), list(director.FIELDS))

    def test_opt_in_compiler_wiring_and_old_request_is_unchanged(self):
        compiled, payload = fixture()
        old_payload = copy.deepcopy(payload)
        old_payload.pop('final_anchor_policy')
        self.assertEqual(continuity.finalize_prompt_anchors(compiled, old_payload), compiled)
        with patch('cores.workbench_story_director_trial.compile_reply', return_value=compiled):
            result = continuity.compile_director_reply({}, payload)
        self.assertIn('白玉地面，四根青铜柱', result['prompt'])
        self.assertEqual(digest(result['dialogue']), digest(compiled['dialogue']))
        config = dict(final_anchor_policy='render-anchors-v1')
        plan = dict(props=payload['stable_visual_contract']['props'], beats=[], invariants=[])
        _, actual_payload, _ = continuity.apply_director_context(('', {}, {}), config, plan,
            payload['stable_visual_contract']['scene'], [])
        self.assertEqual(actual_payload['final_anchor_policy'], 'render-anchors-v1')


if __name__ == '__main__':
    unittest.main()
