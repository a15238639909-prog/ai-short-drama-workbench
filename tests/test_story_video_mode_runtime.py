"""Mode routing and recovery contracts; every model/media call is mocked."""
import copy
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from PIL import Image
from cores import story_video_core as core, story_video_prompts as legacy
from cores import story_video_mode_prompts as modes, video_geometry as geometry
from tests import test_story_video_core as fixtures
from tests import test_story_video_automatic as automatic_fixtures


def chinese_written(payload):
    value = fixtures.written_fixture(payload)
    value.update(integrated_multimodal_description='[Shot 1] 真人写实摄影，中景跟随人物观察石台。人物伸手转环，水流在身旁持续变化，最后自然抬眼回应。',
        overall_soundscape='流水声、脚步声和衣料摩擦声。', non_diegetic_music='N/A',
        expected_end_state='人物在石台旁自然回应，面部可辨。', brief='转环并观察水流。')
    for row in value['dialogue']:
        row['delivery'] = '人物轻声提醒同伴，语气自然。'
    return value


class ModeRuntimeTests(unittest.TestCase):
    def fixture(self, *, image=False, automatic=False):
        kind = automatic_fixtures.AutomaticStoryFlowTests if automatic else fixtures.StoryFlowTests
        case = kind(methodName='runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        previous = getattr(core.runtime_reset._local, 'generation', None)
        self.addCleanup(core.runtime_reset.bind_thread, previous)
        case.mock(core.runtime_reset, 'STATE', case.root/'isolated_runtime_generation.json')
        case.task['runtime_generation'] = 'initial'
        mode = 'image' if image else 'text'
        case.task['inputs'].update(input_mode=mode, flow_version=2,
            quality_mode='automatic' if automatic else 'guided', aspect_ratio='16:9')
        if image:
            opening = case.root/'opening.png'
            Image.new('RGB', (96, 54), 'blue').save(opening)
            case.task['inputs']['opening_image'] = str(opening)
        else:
            case.task['inputs']['opening_image'] = ''
        if not automatic:
            case.mock(geometry, 'preserve_display_ratio', side_effect=lambda src, dst, g: shutil.copy2(src, dst))
            case.mock(core, '_busy')
            case.mock(core.runtime, '_queue')
        requests = []
        old_text = case.model_text
        old_vision = case.model_vision

        def text(system, payload, *args, **kwargs):
            requests.append(copy.deepcopy(payload))
            reply = old_text(system, payload, *args, **kwargs)
            if 'dialogue_slots' in payload and payload.get('director_profile') == modes.PROFILE:
                return json.dumps(chinese_written(payload), ensure_ascii=False)
            return reply

        def vision(system, payload, *args, **kwargs):
            requests.append(copy.deepcopy(payload))
            if payload['beat'] is None and not automatic:
                value = fixtures.observation_fixture(0)
            else:
                value = json.loads(old_vision(system, payload, *args, **kwargs))
            if payload.get('director_profile') == modes.PROFILE and payload.get('story_input_mode') == 'image':
                value['scene_anchors'] = ['石台位于人物身旁，流水从台边经过。']
                for character in value['characters']:
                    character.update(face_view='three_quarter', face_clarity='clear')
            return json.dumps(value)

        case.text.side_effect, case.vision.side_effect = text, vision
        return case, requests

    def finish_guided(self, case):
        with self.assertRaises(core.ControlStop):
            core.run_job(case.task['id'])
        core.action(case.task['id'], 'approve')
        case.task['status'] = 'running'
        return core.run_job(case.task['id'])

    def assert_profile(self, case, requests, mode):
        manifest = case.manifest()
        self.assertEqual(manifest['director_profile'], modes.PROFILE)
        self.assertNotIn('director_profile', case.task['inputs'])
        self.assertNotIn('director_profile', manifest['inputs'])
        for request in requests:
            self.assertEqual(request.get('director_profile'), modes.PROFILE)
            self.assertEqual(request.get('story_input_mode'), mode)
        self.assertTrue(any('count' in request for request in requests))
        self.assertTrue(any(request.get('stage') == 'review' for request in requests))
        self.assertTrue(any(request.get('stage') == 'segment' for request in requests))

    def test_new_guided_text_keeps_text_policy_after_switch_to_i2va(self):
        case, requests = self.fixture()
        result = self.finish_guided(case)
        self.assertEqual(result['segments'], 2)
        self.assert_profile(case, requests, 'text')
        self.assertEqual([row['mode'] for row in case.writer_states], ['t2va', 'i2va'])
        self.assertEqual(case.video_calls[1]['first_frame'], case.manifest()['segments'][0]['anchor_path'])
        self.assertTrue(all('真人写实摄影' in json.loads(Path(row['prompt_path']).read_text(encoding='utf-8'))['prompt']
                            for row in case.manifest()['segments']))

    def test_new_guided_image_uses_opening_then_exact_real_tail(self):
        case, requests = self.fixture(image=True)
        self.finish_guided(case)
        self.assert_profile(case, requests, 'image')
        self.assertEqual([row['mode'] for row in case.writer_states], ['i2va', 'i2va'])
        self.assertEqual(case.video_calls[0]['first_frame'], str(case.folder/'opening.png'))
        self.assertEqual(case.video_calls[1]['first_frame'], case.manifest()['segments'][0]['anchor_path'])
        observed = case.writer_states[1]['actual_state']
        self.assertEqual(observed['characters'][0]['face_view'], 'three_quarter')
        self.assertEqual(observed['characters'][0]['face_clarity'], 'clear')
        self.assertEqual(observed['scene_anchors'], ['石台位于人物身旁，流水从台边经过。'])

    def test_automatic_image_routes_plan_revision_review_and_writing(self):
        case, requests = self.fixture(image=True, automatic=True)
        case.review_replies = [fixtures.review_fixture(False), fixtures.review_fixture(True)]
        result = core.run_job(case.task['id'])
        self.assertEqual(result['segments'], 2)
        self.assert_profile(case, requests, 'image')
        self.assertEqual(len(case.revision_payloads), 1)
        self.assertEqual(case.revision_payloads[0]['director_profile'], modes.PROFILE)
        self.assertEqual(len(case.video_calls), 2)
        self.assertTrue(case.manifest()['quality_summary']['passed'])

    def test_automatic_text_keeps_text_policy_on_i2va_continuation(self):
        case, requests = self.fixture(automatic=True)
        core.run_job(case.task['id'])
        self.assert_profile(case, requests, 'text')
        self.assertEqual([row['mode'] for row in case.writer_states], ['t2va', 'i2va'])
        self.assertIsNone(case.video_calls[0]['first_frame'])
        self.assertEqual(case.video_calls[1]['first_frame'], case.manifest()['segments'][0]['anchor_path'])

    def test_existing_manifest_without_profile_keeps_original_contract(self):
        case, requests = self.fixture()
        case.save_json(case.folder/'manifest.json', dict(version=1, task_id=case.task['id'],
            inputs=copy.deepcopy(case.task['inputs']), segments=[], logs=[], created=0))
        with patch.object(modes, 'for_mode', side_effect=AssertionError('old task must keep original prompts')):
            self.finish_guided(case)
        self.assertNotIn('director_profile', case.manifest())
        self.assertTrue(all('director_profile' not in request for request in requests))
        director = json.loads(Path(case.manifest()['segments'][0]['prompt_path']).read_text(encoding='utf-8'))
        self.assertIn('Cinematic 3D', director['prompt'])

    def test_reference_dispatch_happens_before_profile_routing(self):
        case, _ = self.fixture()
        case.task['inputs']['input_mode'] = 'reference'
        from cores import story_video_reference as reference
        with patch.object(reference, 'run_job', return_value={'reference': 'unchanged'}) as run, \
                patch.object(core, '_prompt_builder', side_effect=AssertionError('reference must not use legacy mode router')):
            self.assertEqual(core.run_job(case.task['id']), {'reference': 'unchanged'})
        run.assert_called_once_with(case.task['id'])
        self.assertFalse((case.folder/'manifest.json').exists())

    def test_chinese_compiler_and_style_gate_preserve_original_dialogue(self):
        plan = fixtures.plan_fixture()
        plan['beats'][0]['dialogue'] = [dict(speaker='c1', text='先等等。')]
        request = modes.for_mode('image').write_request(plan, plan['beats'][0], {}, 8, 'i2va', visual_style='真人写实')
        written = chinese_written(request[1])
        before = copy.deepcopy(written)
        core.validate_written(written, request[1])
        compiled = modes.for_mode('image').compile_written(written, request[1])
        self.assertTrue(compiled['prompt'].startswith(legacy.I2VA_FIRST_FRAME))
        self.assertIn('<d>[Chinese] 先等等。</d>', compiled['prompt'])
        self.assertEqual(written, before)
        old_payload = legacy.write_request(plan, plan['beats'][0], {}, 8, 'i2va', visual_style='真人写实')[1]
        with self.assertRaises(ValueError):
            core.validate_written(written, old_payload)

    def test_unknown_profile_is_rejected_instead_of_silent_fallback(self):
        with self.assertRaisesRegex(RuntimeError, '导演版本未知'):
            core._prompt_builder(dict(input_mode='image', director_profile='unsupported'))
        self.assertIs(core._prompt_builder(dict(input_mode='image')), legacy)


if __name__ == '__main__':
    unittest.main()
