"""A valid short episode is reusable; word count is not a missing-data flag."""
import os
os.environ['V41_NO_MODEL'] = '1'
import unittest
from contextlib import ExitStack
from unittest.mock import patch
from api import saga_api as sa
from cores import authoring, shot_edit, manual_story


class ShortEpisodeTests(unittest.TestCase):
    def fixtures(self, conversion=None):
        stack = ExitStack()
        self.addCleanup(stack.close)
        episode = {'prose': '阿宁：好。\n林海：好。', 'body': '阿宁：好。\n林海：好。',
                   'text_brief_sig': 'same', 'pictures_conversion_version': conversion}
        stack.enter_context(patch.object(sa, '_light_status', return_value={
            'prose': 13, 'script': 13, 'script_stale': False, 'characters': 2}))
        stack.enter_context(patch.object(authoring, '_ep', return_value=({}, episode)))
        stack.enter_context(patch.object(sa, '_brief_sig', return_value='same'))
        stack.enter_context(patch.object(sa.story_core, 'get_story', return_value={'settings': {'story_mode': 'single'}}))
        stack.enter_context(patch.object(authoring, '_update_ep'))
        stack.enter_context(patch.object(authoring, 'prepare_episode_assets', return_value={}))
        stack.enter_context(patch.object(authoring, 'ensure_scenes', return_value=[]))
        stack.enter_context(patch.object(manual_story, 'approve'))
        stack.enter_context(patch.object(sa, '_preflight_gate'))
        stack.enter_context(patch.object(sa, '_job_event'))
        stack.enter_context(patch.object(sa, '_complete_step'))
        stack.enter_context(patch.object(sa, '_set_step'))
        stack.enter_context(patch.object(shot_edit, 'rows_of', return_value={'n': 1, 'stale': False}))
        generate = stack.enter_context(patch.object(authoring, 'gen_prose'))
        pictures = stack.enter_context(patch.object(authoring, 'make_pictures'))
        return generate, pictures

    def test_valid_short_prose_and_current_script_are_not_regenerated(self):
        generate, pictures = self.fixtures(authoring.LOSSLESS_PICTURES_VERSION)
        self.assertEqual(sa._text_chain_to_shots('TEST', {}, 1, 'same'), 1)
        generate.assert_not_called()
        pictures.assert_not_called()

    def test_old_script_upgrades_without_rewriting_short_original(self):
        generate, pictures = self.fixtures('legacy-lossy')
        self.assertEqual(sa._text_chain_to_shots('TEST', {}, 2, 'same'), 1)
        generate.assert_not_called()
        pictures.assert_called_once_with('TEST', 2)


if __name__ == '__main__':
    unittest.main()
