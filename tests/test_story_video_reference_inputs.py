"""Reference-mode admission and dispatch; no models or live tasks."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from cores import story_video_core as core


class ReferenceInputsTests(unittest.TestCase):
    def setUp(self):
        # Retain fixtures; never delete user files as part of a test.
        self.root = Path(tempfile.mkdtemp(prefix='story_reference_inputs_'))
        self.image = self.root / 'person.png'
        self.scene = self.root / 'scene.png'
        Image.new('RGB', (32, 32), 'blue').save(self.image)
        Image.new('RGB', (48, 32), 'green').save(self.scene)
        self.data = dict(input_mode='reference', story_direction='少年与伙伴走进旧城。',
            segment_count=3, segment_seconds=15, style='电影写实', quality_mode='automatic',
            story_input='story', initial_scene=str(self.scene), character_cards=[
                dict(id='hero_15', image_path=str(self.image), name='亚伦',
                     personality='十五岁少年，勇敢细心', voice='清朗自然的少年声')])

    def test_four_fields_and_identity_survive_admission(self):
        result = core.validate_inputs(self.data)
        self.assertEqual((result['input_mode'], result['flow_version']), ('reference', 3))
        self.assertEqual(result['story_input'], 'story')
        for key in ('id', 'name', 'personality', 'voice'):
            self.assertEqual(result['character_cards'][0][key], self.data['character_cards'][0][key])
        self.assertEqual(result['character_cards'][0]['image_path'], str(self.image.resolve()))
        self.assertEqual(result['character_cards'][0]['sha256'], core._frame_signature(self.image))
        self.assertEqual(result['initial_scene_sha256'], core._frame_signature(self.scene))

    def test_scene_is_optional_and_idea_is_explicit(self):
        result = core.validate_inputs(dict(self.data, initial_scene='', story_input='idea'))
        self.assertEqual(result['initial_scene'], '')
        self.assertEqual(result['initial_scene_sha256'], '')
        self.assertEqual(result['story_input'], 'idea')

    def test_character_and_dialogue_identity_errors_are_rejected(self):
        for changes in ({'character_cards': []}, {'story_input': 'rewrite'},
                        {'segment_seconds': 10}, {'initial_scene': {'path': str(self.scene)}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                core.validate_inputs(dict(self.data, **changes))
        cards = self.data['character_cards'] * 2
        with self.assertRaises(ValueError):
            core.validate_inputs(dict(self.data, character_cards=cards))

    def test_eighth_character_is_rejected_before_generation(self):
        cards = [dict(self.data['character_cards'][0], id='c%d' % n, name='人物%d' % n)
                 for n in range(8)]
        with self.assertRaisesRegex(ValueError, '1～7'):
            core.validate_inputs(dict(self.data, character_cards=cards))

    def test_invalid_local_image_is_rejected_without_model_call(self):
        for path in ('not-an-upload.png', str(self.root / 'missing.png')):
            card = dict(self.data['character_cards'][0], image_path=path)
            with self.subTest(path=path), self.assertRaises(ValueError):
                core.validate_inputs(dict(self.data, character_cards=[card]))

    def test_legacy_text_and_image_modes_keep_their_versions(self):
        text = core.validate_inputs(dict(self.data, input_mode='text'))
        image = core.validate_inputs(dict(self.data, input_mode='image', opening_image=str(self.image)))
        self.assertEqual((text['input_mode'], text['flow_version']), ('text', 2))
        self.assertEqual((image['input_mode'], image['flow_version']), ('image', 2))
        self.assertNotIn('character_cards', text)
        self.assertNotIn('character_cards', image)

    def test_reference_dispatch_does_not_enter_legacy_pipeline(self):
        task = dict(id='task_reference_fixture', kind=core.KIND,
            checkpoint={'story_video_folder': str(self.root)}, inputs=copy.deepcopy(self.data))
        with patch.object(core.runtime, 'load', return_value=task), \
                patch.object(core.runtime_reset, 'bind_thread'), \
                patch.object(core.runtime_reset, 'assert_current'), \
                patch('cores.story_video_reference.run_job', return_value={'fixture': True}) as run:
            self.assertEqual(core.run_job(task['id']), {'fixture': True})
        run.assert_called_once_with(task['id'])


if __name__ == '__main__':
    unittest.main()
