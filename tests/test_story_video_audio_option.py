"""Optional speech review admission. No models, server calls or task creation."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from PIL import Image
from cores import story_video_core as core


class AudioOptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = Path(tempfile.mkdtemp(prefix='story_audio_option_zero_model_'))
        cls.image = cls.folder / 'portrait.png'
        Image.new('RGB', (32, 32), 'blue').save(cls.image)

    def inputs(self, mode='text', **changes):
        result = dict(input_mode=mode, story_direction='旅人发现一块指路牌，随后与伙伴出发。',
            segment_count=2, segment_seconds=15, style='电影写实', quality_mode='automatic')
        if mode == 'image':
            result['opening_image'] = str(self.image)
        if mode == 'reference':
            result.update(story_input='story', initial_scene='', character_cards=[dict(
                id='traveller', name='旅人', image_path=str(self.image), personality='耐心细致', voice='清朗自然的声线')])
        result.update(changes)
        return result

    def test_all_modes_default_to_disabled_and_fixed_runtime_profile(self):
        for mode in ('text', 'image', 'reference'):
            with self.subTest(mode=mode):
                original = self.inputs(mode)
                before = copy.deepcopy(original)
                result = core.validate_inputs(original)
                self.assertIs(result['audio_review_enabled'], False)
                self.assertEqual(result['runtime_optimization'], 'story-video-flow-v2')
                self.assertEqual(original, before)
                self.assertEqual(result['input_mode'], mode)

    def test_explicit_boolean_choice_survives_all_modes(self):
        for mode in ('text', 'image', 'reference'):
            for choice in (False, True):
                with self.subTest(mode=mode, choice=choice):
                    result = core.validate_inputs(self.inputs(mode, audio_review_enabled=choice))
                    self.assertIs(result['audio_review_enabled'], choice)

    def test_truthy_strings_numbers_null_and_objects_are_rejected(self):
        for mode in ('text', 'image', 'reference'):
            for value in ('true', 'false', '', 0, 1, None, [], {}):
                with self.subTest(mode=mode, value=value), self.assertRaisesRegex(ValueError, '语音核对'):
                    core.validate_inputs(self.inputs(mode, audio_review_enabled=value))

    def test_unknown_runtime_profile_is_rejected(self):
        for value in ('old-version', '', None, True, 1, {}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '优化版本未知'):
                core.validate_inputs(self.inputs(runtime_optimization=value))
        accepted = core.validate_inputs(self.inputs(runtime_optimization='story-video-flow-v2'))
        self.assertEqual(accepted['runtime_optimization'], 'story-video-flow-v2')

    def test_capability_distinguishes_optional_default_from_available_recognizer(self):
        with patch('cores.paths.get', return_value=str(self.image)), patch(
                'cores.story_video_asr.capability', return_value=dict(available=False, reason='fixture')):
            result = core.capabilities()
        self.assertTrue(result['available'])
        self.assertTrue(result['audio_review_optional'])
        self.assertIs(result['audio_review_enabled_default'], False)
        self.assertFalse(result['audio_review']['available'])
        self.assertEqual(result['runtime_optimization'], 'story-video-flow-v2')

    def test_start_with_review_disabled_does_not_require_asr(self):
        capability = dict(missing=[], audio_review=dict(available=False, reason='fixture missing recognizer'))
        with patch.object(core, '_busy'), patch.object(core, 'capabilities', return_value=capability), \
                patch.dict(core.runtime.RUNNERS, {}, clear=False), \
                patch.object(core.runtime, 'create_task', side_effect=lambda kind, title, inputs: inputs) as create:
            result = core.start(self.inputs())
        create.assert_called_once()
        self.assertIs(result['audio_review_enabled'], False)


if __name__ == '__main__':
    unittest.main()
