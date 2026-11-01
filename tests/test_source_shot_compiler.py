"""Current-source ownership, real compiler/voice integration, no model calls."""
import copy
import json
import unittest
from cores import source_shot_compiler as compiler
from cores import workbench_story_director as release
from cores import scene_reference_policy
from tests.test_workbench_story_director_release import fixture


def model_reply(payload):
    return dict(style_description=payload.get('visual_style') or '自然细腻的影像质感。',
        scene_description=payload['scene'] + '的空间层次清楚。', soundscape='现场风声与衣料的轻微摩擦声。',
        shots=[dict(unit_ids=[u['id']], framing='中景' if i == 0 else '近景',
            angle='平视', movement='固定机位', weight=2, delivery='自然清楚',
            visual=dict(composition='主体轮廓与前后空间层次清楚。', lighting='侧光柔和，暗部细节自然。',
                texture='可见表面呈现细腻的材质纹理。', performance='',
                camera_detail='焦点清晰，背景柔和。')) for i, u in enumerate(payload['source_units'])])


class SourceShotTests(unittest.TestCase):
    def run_writer(self, query=None, **kwargs):
        slices, cards, settings = fixture()
        settings.pop('video_action_compiler')
        calls = []
        def q(system, user, **options):
            payload = json.loads(user); calls.append(payload)
            self.assertEqual(options['response_format']['schema'], compiler.SCHEMA)
            return json.dumps(query(payload, len(calls)) if query else model_reply(payload), ensure_ascii=False)
        rows = release.make_prompts(slices, cards, ['竹庭'], q, settings=settings, **kwargs)
        return rows, calls

    def test_default_route_binds_exact_current_actions_and_dialogue(self):
        rows, calls = self.run_writer()
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(not r['source_fallback'] for r in rows))
        for index, row in enumerate(rows):
            self.assertEqual(row['plan']['source_shot_policy'], compiler.VERSION)
            self.assertEqual(row['plan']['scene_reference_scope'], scene_reference_policy.SCOPE)
            self.assertIn('environment from <Picture 3>' if index == 0 else 'the environment.', row['prompt'])
            if index:
                self.assertNotIn('environment from <Picture', row['prompt'])
            audit = row['director_compilation']['source_shot_audit']
            self.assertEqual([u for shot in audit['shots'] for u in shot['unit_ids']], [u['id'] for u in audit['units']])
            self.assertEqual(audit['shots'][-1]['end'], row['seconds'])
        self.assertEqual(rows[0]['prompt'].count('慢一点。'), 1)
        self.assertNotIn('这回稳了', json.dumps(calls[0], ensure_ascii=False))
        self.assertNotIn('story_plan', calls[0])
        self.assertNotIn('秘密石台', json.dumps(calls, ensure_ascii=False))
        self.assertIn('<Subject 1>扶稳茶杯', rows[0]['prompt'])
        self.assertIn('温柔清晰的成年女声', rows[1]['prompt'])
        self.assertIn('(S2)', rows[1]['prompt'])

    def test_free_action_prose_is_rejected_then_repaired(self):
        def reply(payload, attempt):
            value = model_reply(payload)
            if attempt == 1:
                value['integrated_multimodal_description'] = '提前破门走出医院'
            return value
        rows, calls = self.run_writer(reply, count=1)
        self.assertEqual(len(calls), 2)
        self.assertFalse(rows[0]['source_fallback'])
        self.assertNotIn('破门', rows[0]['prompt'])

    def test_missing_repeated_reordered_or_future_unit_rejected(self):
        for ids in (['U002'], ['U001', 'U001', 'U002'], ['U002', 'U001'], ['U001', 'U002', 'U003']):
            def reply(payload, attempt):
                value = model_reply(payload)
                value['shots'] = [dict(value['shots'][0], unit_ids=ids)]
                return value
            with self.subTest(ids=ids):
                rows, calls = self.run_writer(reply, count=1)
                self.assertEqual(len(calls), 2)
                self.assertTrue(rows[0]['source_fallback'])
                self.assertEqual(rows[0]['prompt'].count('慢一点。'), 1)
                self.assertIn('source_shot_audit', rows[0]['director_compilation'])

    def test_reliable_endpoint_is_in_first_shot_not_planned_tail(self):
        state = dict(endpoint_evidence=dict(version='explicit-final-frame-v1', frame_index=7, verified=True),
            characters=[dict(id='a', frame_index=7, certainty='observed', visibility='visible',
                position='木桌旁', pose='站立', hands='握着茶杯')])
        rows, calls = self.run_writer(count=1, actual_state_for=lambda index: state)
        self.assertIn('木桌旁', calls[0]['endpoint_start_state'])
        self.assertIn('起镜状态：<Subject 1>：木桌旁', rows[0]['prompt'])
        self.assertNotIn('秘密石台', rows[0]['prompt'])

    def test_silent_long_single_action_no_fake_actor_or_repeated_action(self):
        sl = dict(seconds=12, scene_hint='山谷', text='薄雾沿溪水移动。', chars=[],
            director=dict(flow='source-v1', cast=[], blocks=[[0,12,'薄雾沿溪水移动。',[]]], says=[]))
        def q(system,user,**options): return json.dumps(model_reply(json.loads(user)), ensure_ascii=False)
        row = release.make_prompts([sl], [], ['山谷'], q,
            settings=dict(_director_reference_paths={'山谷':__file__}, style='写实摄影'))[0]
        self.assertFalse(row['source_fallback'])
        self.assertEqual(row['prompt'].count('薄雾沿溪水移动。'), 1)
        self.assertEqual(row['director_compilation']['dialogue'], [])
        self.assertNotIn('character from', row['prompt'])

    def test_camera_fallback_never_swallows_game_or_cancel(self):
        slices,cards,settings=fixture();settings.pop('video_action_compiler')
        for message in ('PUBG正在运行','已取消'):
            def q(*a, **k): raise RuntimeError(message)
            with self.assertRaisesRegex(RuntimeError,message):
                release.make_prompts(slices,cards,['竹庭'],q,settings=settings,count=1)

    def test_return_to_a_previous_scene_and_partial_rewrite_keep_scene_unbound(self):
        for start in (0, 2):
            with self.subTest(start=start):
                slices, cards, settings = fixture()
                settings.pop('video_action_compiler')
                settings['_director_reference_paths']['石桥'] = __file__
                slices[1]['scene_hint'] = '石桥'
                slices.append(copy.deepcopy(slices[0]))
                original = copy.deepcopy(slices)
                def q(system, user, **options):
                    return json.dumps(model_reply(json.loads(user)), ensure_ascii=False)
                rows = release.make_prompts(slices, cards, ['竹庭', '石桥'], q,
                    settings=settings, start=start)
                self.assertEqual([row['text'] for row in slices], [row['text'] for row in original])
                self.assertEqual([(row['director']['blocks'], row['director']['says']) for row in slices],
                                 [(row['director']['blocks'], row['director']['says']) for row in original])
                self.assertEqual([row['source_text'] for row in rows],
                                 [row['text'] for row in original[start:]])
                self.assertIn('the environment.', rows[-1]['prompt'])
                self.assertNotIn('environment from <Picture', rows[-1]['prompt'])
                self.assertEqual(rows[-1]['plan']['scene_reference_scope'], scene_reference_policy.SCOPE)
                if start == 0:
                    self.assertIn('environment from <Picture 3>', rows[0]['prompt'])
                    self.assertIn('environment from <Picture 3>', rows[1]['prompt'])

    def test_empty_scene_continuation_has_no_fake_scene_picture(self):
        def empty_slice(text):
            return dict(seconds=8, scene_hint='山谷', chars=[], text=text,
                director=dict(flow='source-v1', cast=[], camera='远景', purpose='展示环境变化。',
                    start_state='以实际观察为准。', target_state=text,
                    blocks=[[0, 8, text, []]], says=[]))
        slices = [empty_slice('薄雾沿溪水移动。'), empty_slice('水面映出山坡的轮廓。')]
        def q(system, user, **options):
            return json.dumps(model_reply(json.loads(user)), ensure_ascii=False)
        rows = release.make_prompts(slices, [], ['山谷'], q,
            settings=dict(_director_reference_paths={'山谷': __file__}, style='写实摄影'))
        self.assertIn('environment from <Picture 1>', rows[0]['prompt'])
        self.assertIn('the environment.', rows[1]['prompt'])
        self.assertNotIn('<Picture', rows[1]['prompt'])
        self.assertEqual(rows[1]['prompt'].count('水面映出山坡的轮廓。'), 1)
        self.assertEqual(rows[1]['plan']['scene_reference_scope'], scene_reference_policy.SCOPE)


if __name__ == '__main__': unittest.main()
