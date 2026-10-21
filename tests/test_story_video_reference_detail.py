"""Reference precision transport and old-job compatibility, without inference."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from PIL import Image
from cores import story_video_core as core, story_video_reference as reference
from tests import test_story_video_reference_runtime as runtime_tests


class ReferenceDetailTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='reference_detail_retained_'))
        self.image = self.folder / 'ordinary_reference.png'
        Image.new('RGB', (32, 32), 'gray').save(self.image)
        self.inputs = dict(input_mode='reference', story_input='story',
            story_direction='两位成年朋友一起寻找遗失的书签。', segment_count=2,
            segment_seconds=15, initial_scene='', character_cards=[dict(
                id='char_01', name='林青', image_path=str(self.image),
                personality='细心友善', voice='温和清晰的成年女声')])

    def test_new_reference_tasks_validate_precision_and_default_to_standard(self):
        self.assertEqual(core.validate_inputs(self.inputs)['reference_image_size'], 'match')
        for value in ('match', 'max'):
            with self.subTest(value=value):
                result = core.validate_inputs(dict(self.inputs, reference_image_size=value))
                self.assertEqual(result['reference_image_size'], value)
                self.assertEqual(len(result['character_cards']), 1)
                self.assertEqual(result['character_cards'][0]['image_path'], str(self.image.resolve()))
        for value in ('native', '', None, True, [], {}):
            with self.subTest(invalid=value):
                with self.assertRaisesRegex(ValueError, '参考图精度'):
                    core.validate_inputs(dict(self.inputs, reference_image_size=value))

    def test_text_and_image_tasks_do_not_acquire_reference_option(self):
        common = dict(story_direction='两位成年朋友一起寻找遗失的书签。',
                      segment_count=2, segment_seconds=15, reference_image_size='max')
        text = core.validate_inputs(dict(common, input_mode='text'))
        image = core.validate_inputs(dict(common, input_mode='image', opening_image=str(self.image)))
        self.assertNotIn('reference_image_size', text)
        self.assertNotIn('reference_image_size', image)

    def _flow(self, precision=None, pause=True):
        original_fixture = runtime_tests.fixture

        def fixture(*args, **kwargs):
            folder, task, plan = original_fixture(*args, **kwargs)
            if precision is not None:
                task['inputs']['reference_image_size'] = precision
            return folder, task, plan

        helper = runtime_tests.ReferenceRuntimeTests(methodName='runTest')
        with patch.object(runtime_tests, 'fixture', side_effect=fixture):
            return helper._flow(scene=False, pause_second=pause)

    def test_high_detail_flows_to_every_submission_and_checkpoint_across_resume(self):
        folder, task, manifest, output, stages, requests, generated = self._flow('max')
        self.assertEqual(len(generated), 2)
        self.assertEqual(output['segments'], 2)
        self.assertEqual(manifest['inputs']['reference_image_size'], 'max')
        for row, call in zip(manifest['segments'], generated):
            binding = reference._read(Path(row['prompt_path']).with_name('inputs.json'))
            self.assertEqual(binding['reference_image_size'], 'max')
            self.assertEqual(row['input_signature'], reference._signature(binding))
            self.assertEqual(call['options']['reference_image_size'], 'max')
        self.assertEqual(len(generated[0]['options']['images']), 4)
        self.assertEqual(len(generated[1]['options']['images']), 5)

    def test_old_missing_option_keeps_existing_binding_and_call_shape(self):
        folder, task, manifest, output, stages, requests, generated = self._flow()
        self.assertEqual(len(generated), 2)
        self.assertNotIn('reference_image_size', manifest['inputs'])
        for row, call in zip(manifest['segments'], generated):
            binding = reference._read(Path(row['prompt_path']).with_name('inputs.json'))
            self.assertNotIn('reference_image_size', binding)
            self.assertNotIn('reference_image_size', call['options'])
            self.assertEqual(row['input_signature'], reference._signature(binding))

    def test_changing_precision_cannot_reuse_old_media_as_new_precision(self):
        folder, task, manifest, *_ = self._flow('max', pause=False)
        old_bytes = (folder / 'manifest.json').read_bytes()
        task = copy.deepcopy(task)
        task['inputs']['reference_image_size'] = 'match'
        with patch.object(core.runtime, 'load', return_value=task), \
                patch.object(core.runtime, 'TaskHandle'), \
                patch.object(reference.runtime_reset, 'bind_thread'), \
                patch.object(reference.runtime_reset, 'assert_current'), \
                patch.object(reference.h3_client, 'generate') as generate:
            with self.assertRaisesRegex(RuntimeError, '任务输入变化'):
                reference.run_job(task['id'])
        generate.assert_not_called()
        self.assertEqual((folder / 'manifest.json').read_bytes(), old_bytes)

    def test_invalid_precision_is_rejected_before_gpu_or_submission(self):
        class Session:
            inputs = dict(reference_image_size='invalid')

        with patch.object(reference.gpu_manager, 'acquire') as acquire, \
                patch.object(reference.h3_client, 'generate') as generate:
            with self.assertRaisesRegex(ValueError, '参考图精度'):
                reference._generate_once(Session(), self.folder, {}, [], {}, 1, 1, 'fixture')
        acquire.assert_not_called()
        generate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
