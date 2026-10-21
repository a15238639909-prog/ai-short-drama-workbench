"""Image-only authority reaches new reference jobs without any inference."""
from contextlib import redirect_stdout
import copy
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from PIL import Image
from cores import story_video_core as core, story_video_reference as reference
from cores import story_video_reference_prompts as prompts, universal_writer as uw
from tests import test_story_video_reference_runtime as runtime_tests


class ImageAuthorityRuntimeTests(unittest.TestCase):
    def test_reference_default_is_image_authority_without_new_user_settings(self):
        folder = Path(tempfile.mkdtemp(prefix='image_authority_input_retained_'))
        image = folder / 'reference.png'
        Image.new('RGB', (32, 32), 'white').save(image)
        inputs = dict(input_mode='reference', segment_count=2, segment_seconds=15,
            story_direction='两位成年朋友一起寻找书签。', character_cards=[dict(
                image_path=str(image), name='林青', personality='细心友善', voice='温和的成年女声')])
        normalized = core.validate_inputs(inputs)
        self.assertEqual(normalized['reference_visual_policy'], prompts.REFERENCE_VISUAL_POLICY)
        self.assertEqual(normalized['reference_image_size'], 'match')
        self.assertEqual(set(normalized['character_cards'][0]),
                         {'id', 'name', 'image_path', 'personality', 'voice', 'sha256'})
        for mode in ('text', 'image'):
            other = core.validate_inputs(dict(inputs, input_mode=mode, opening_image=str(image)))
            self.assertNotIn('reference_visual_policy', other)

    def _flow(self, scene=False, new_policy=True, pause=True):
        original = runtime_tests.fixture

        def fixture(*args, **kwargs):
            folder, task, plan = original(*args, **kwargs)
            if new_policy:
                task['inputs']['reference_visual_policy'] = prompts.REFERENCE_VISUAL_POLICY
            return folder, task, plan

        helper = runtime_tests.ReferenceRuntimeTests(methodName='runTest')
        with patch.object(runtime_tests, 'fixture', side_effect=fixture), redirect_stdout(io.StringIO()):
            return helper._flow(scene=scene, pause_second=pause)

    def test_two_segments_and_resume_keep_image_rule_and_story_generated_background(self):
        folder, task, manifest, output, stages, requests, generated = self._flow()
        self.assertEqual(len(generated), 2)
        self.assertEqual(output['segments'], 2)
        relevant = [r['payload'] for r in requests if 'dialogue_slots' in r['payload']
                    or ('count' in r['payload'] and r['payload'].get('stage') != 'references')]
        self.assertTrue(relevant)
        for payload in relevant:
            self.assertEqual(payload['reference_visual_policy'], prompts.REFERENCE_VISUAL_POLICY)
            self.assertIs(payload['scene_reference_provided'], False)
        for call in generated:
            self.assertIn('全部按各自人设图', call['prompt'])
            self.assertIn('根据故事', call['prompt'])
            self.assertNotIn('使用空白背景', call['prompt'])
        self.assertEqual(len(generated[0]['options']['images']), 4)
        self.assertEqual(len(generated[1]['options']['images']), 5)

    def test_scene_reference_policy_survives_after_initial_scene_image_is_replaced_by_tail(self):
        folder, task, manifest, output, stages, requests, generated = self._flow(scene=True)
        for request in requests:
            if request['payload'].get('reference_visual_policy') == prompts.REFERENCE_VISUAL_POLICY:
                self.assertIs(request['payload']['scene_reference_provided'], True)
        for call in generated:
            self.assertIn(prompts.reference_visual_rule(True), call['prompt'])
            self.assertIn('对应地点', call['prompt'])
            self.assertNotIn('使用空白背景', call['prompt'])
        self.assertIn('uploaded_scene.png', str(task['inputs']))
        self.assertNotIn('uploaded_scene.png', str(generated[1]['options']['images']))
        self.assertEqual(len(generated[0]['options']['images']), 5)
        self.assertEqual(len(generated[1]['options']['images']), 5)

    def test_existing_tasks_keep_cached_requests_and_legacy_prompts(self):
        folder, task, manifest, output, stages, requests, generated = self._flow(new_policy=False)
        self.assertEqual(reference._reference_visual_options(task['inputs']), {})
        self.assertNotIn('reference_visual_policy', manifest['inputs'])
        for request in requests:
            self.assertNotIn('reference_visual_policy', request['payload'])
            self.assertNotIn('scene_reference_provided', request['payload'])
        for call in generated:
            self.assertNotIn('全部按各自人设图', call['prompt'])
        self.assertEqual(len(generated), 2)

    def test_idea_authoring_receives_same_simple_rule_without_image_redesign(self):
        folder, task, plan = runtime_tests.fixture(scene=False)
        task['inputs'].update(story_input='idea', reference_visual_policy=prompts.REFERENCE_VISUAL_POLICY)
        captured = {}

        class Session:
            inputs = task['inputs']
            manifest = {}
            writer = object()

            def cached(self, stage, binding, callback):
                captured[stage] = copy.deepcopy(binding)
                if stage == 'author_plan':
                    return dict(n=1, design=dict(people=[]), rows=[])
                return dict(prose='两位成年朋友互相招手，找到遗落的书签。')

        session = Session()
        session.folder = folder
        with patch.object(uw, 'save_design'), patch.object(uw, 'plan') as plan_model, \
                patch.object(uw, 'write') as prose_model:
            reference._story(session, task['inputs']['character_cards'], dict(scene='unknown'))
        for settings in captured.values():
            self.assertIn(prompts.reference_visual_rule(False), settings['extra_requirements'])
        plan_model.assert_not_called()
        prose_model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
