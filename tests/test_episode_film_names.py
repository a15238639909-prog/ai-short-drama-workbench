"""Filename boundaries and literal title lookup; retained fixtures, no AI."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cores import episode_merge, story_core


class EpisodeFilmNameTests(unittest.TestCase):
    def test_normal_title_is_preserved(self):
        with patch.object(story_core, 'get_story', return_value={'title': '幽灵岛的 黄金秘密'}):
            self.assertEqual(episode_merge.film_title('SAFE'), '幽灵岛的黄金秘密')

    def test_story_title_is_one_windows_filename_component(self):
        story = {'title': 'Voyage: Home/Part\\2?*<>|"\n'}
        original = dict(story)
        with patch.object(story_core, 'get_story', return_value=story):
            title = episode_merge.film_title('SAFE')
        self.assertTrue(title)
        for char in '<>:"/\\|?*\n':
            self.assertNotIn(char, title)
        self.assertEqual(story, original)

    def test_brackets_in_title_are_literal_and_film_stays_visible(self):
        folder = Path(tempfile.mkdtemp(prefix='episode_film_names_'))
        output = folder / 'outputs'
        output.mkdir()
        expected = output / '成片_航海[终章]_第1话.mp4'
        expected.write_bytes(b'retained synthetic fixture')
        # A near-match must not be substituted for the literal bracketed title.
        (output / '成片_航海终_第1话.mp4').write_bytes(b'other fixture')
        prior = Path.cwd()
        try:
            os.chdir(folder)
            with patch.object(story_core, 'get_story', return_value={'title': '航海[终章]'}):
                films = episode_merge.list_films('SAFE')
        finally:
            os.chdir(prior)
        self.assertEqual([row['name'] for row in films], [expected.name])
        self.assertEqual(expected.read_bytes(), b'retained synthetic fixture')


if __name__ == '__main__':
    unittest.main()
