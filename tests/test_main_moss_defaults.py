import unittest
from unittest.mock import MagicMock, patch

from cores import project_settings, story_core, store


class MainMossDefaultsTest(unittest.TestCase):
    def new_story(self, defaults):
        data = MagicMock()
        (data / 'stories').glob.return_value = []
        target = MagicMock()
        target.exists.return_value = False
        with patch.object(store, 'DATA', data), patch.object(store, 'load_json', return_value=defaults), \
             patch.object(store, 'save_json') as save, patch.object(story_core, 'story_path', return_value=target):
            story = story_core.create_story('普通成年人在旧书店寻找一本书。')
        self.assertEqual(save.call_count, 1)
        return story

    def test_new_story_has_moss_and_daily_2x(self):
        settings = project_settings.get(self.new_story({}))
        self.assertEqual(settings['image_model'], 'moss')
        self.assertEqual(settings['image_quality'], '2x')

    def test_explicit_global_choice_is_preserved(self):
        settings = project_settings.get(self.new_story({'image_model': 'krea2'}))
        self.assertEqual(settings['image_model'], 'krea2')
        settings = project_settings.get(self.new_story({'image_model': 'moss', 'image_quality': '4k'}))
        self.assertEqual(settings['image_model'], 'moss')
        self.assertEqual(settings['image_quality'], '4k')

    def test_old_story_fallback_is_krea_and_read_only(self):
        old = {'settings': {'style': '电影级写实'}}
        with patch.object(store, 'save_json') as save:
            self.assertEqual(project_settings.get(old)['image_model'], 'krea2')
        save.assert_not_called()
        self.assertNotIn('image_model', old['settings'])


if __name__ == '__main__':
    unittest.main()
