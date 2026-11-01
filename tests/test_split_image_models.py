"""Independent character/scene routing using retained, model-free fixtures."""
import copy
import unittest
from unittest.mock import patch

from tests import test_main_images as image_fixture
from tests import test_main_moss_api as api_fixture
from api import saga_api, video_api
from cores import asset_core, main_images, project_settings, story_core
from models import krea_client, moss_client


def settings(character='krea2', scene='moss'):
    return dict(image_fixture.MOSS, character_image_model=character,
                scene_image_model=scene)


class SplitImageModelTests(unittest.TestCase):
    setUp = image_fixture.MainImageTests.setUp
    character = image_fixture.MainImageTests.character

    def test_each_type_inherits_legacy_only_when_its_selection_is_absent(self):
        selected = settings()
        self.assertEqual(main_images.current(selected)['model'], 'krea2')
        self.assertEqual(main_images.current(selected, kind='scene')['model'], 'moss')
        selected['character_image_model'] = ''
        self.assertEqual(main_images.current(selected)['model'], 'moss')
        selected['scene_image_model'] = ''
        self.assertEqual(main_images.snapshot(selected), main_images.snapshot(image_fixture.MOSS))
        self.assertEqual(main_images.current({}, kind='scene')['model'], 'krea2')

    def test_dual_snapshot_freezes_both_models_and_legacy_snapshots_still_restore(self):
        selected = settings()
        original = copy.deepcopy(selected)
        snapshot = main_images.snapshot(selected)
        self.assertEqual(snapshot['character']['model'], 'krea2')
        self.assertEqual(snapshot['scene']['model'], 'moss')
        with main_images.frozen(snapshot):
            selected.update(character_image_model='moss', scene_image_model='krea2',
                            image_quality='4k')
            self.assertEqual(main_images.current(selected), snapshot['character'])
            self.assertEqual(main_images.current(selected, kind='scene'), snapshot['scene'])
            with main_images.frozen(None):
                self.assertEqual(main_images.current(selected)['model'], 'krea2')
                self.assertEqual(main_images.current(selected, kind='scene')['model'], 'krea2')
            with main_images.frozen(main_images.snapshot(image_fixture.MOSS)):
                self.assertEqual(main_images.current(selected)['model'], 'moss')
                self.assertEqual(main_images.current(selected, kind='scene')['model'], 'moss')
            self.assertEqual(main_images.current(selected, kind='scene'), snapshot['scene'])
        self.assertEqual(main_images.current(selected)['model'], 'moss')
        self.assertEqual(main_images.current(selected, kind='scene')['model'], 'krea2')
        self.assertEqual(main_images.snapshot(original), snapshot)

    def test_model_bound_cache_invalidates_only_the_changed_asset_type(self):
        selected = settings('moss', 'krea2')
        character = main_images.bind_prompt(self.character(), image_fixture.layout(), selected)
        scene = main_images.bind_prompt({'scene_id': 'S1'}, '整洁修表店空间', selected)
        self.assertEqual(character['image_prompt_model'], 'moss')
        self.assertEqual(scene['image_prompt_model'], 'krea2')
        self.assertTrue(main_images.cached_prompt(character, selected))
        self.assertEqual(main_images.cached_prompt(scene, selected), '整洁修表店空间')
        changed = dict(selected, character_image_model='krea2')
        self.assertEqual(main_images.cached_prompt(character, changed), '')
        self.assertEqual(main_images.cached_prompt(scene, changed), '整洁修表店空间')
        changed = dict(selected, scene_image_model='moss')
        self.assertEqual(main_images.cached_prompt(character, changed), image_fixture.layout())
        self.assertEqual(main_images.cached_prompt(scene, changed), '')
        manual = main_images.bind_prompt({'scene_id': 'S1'}, '手写完整场景', selected,
                                        field='prompt_override')
        self.assertEqual(manual['prompt_override_model'], 'krea2')
        self.assertEqual(main_images.cached_prompt(manual, changed, 'prompt_override'), '')

    def test_real_asset_entrypoints_send_each_combination_to_the_correct_client(self):
        self.character()
        asset_core.save_asset('TEST', 'scenes', {'scene_id': 'S1', 'name': '修表店',
                                              'space': '整洁明亮的工作间', 'version': 1})
        for character_model, scene_model in (('krea2', 'moss'), ('moss', 'krea2')):
            with self.subTest(character=character_model, scene=scene_model):
                selected = settings(character_model, scene_model)
                story_core.save_story({'story_id': 'TEST', 'settings': selected})
                with patch.object(krea_client, 'generate', return_value=self.result) as krea, \
                     patch.object(moss_client, 'generate', return_value=self.result) as moss:
                    char_visual, char_prompt = asset_core.generate_character_master(
                        'TEST', 'C1', custom_prompt='成年修表师穿蓝色工作服')
                    scene_visual, scene_prompt = asset_core.generate_scene_master(
                        'TEST', 'S1', custom_prompt='整洁明亮的工作间')
                self.assertEqual(char_visual['model'], character_model)
                self.assertEqual(scene_visual['model'], scene_model)
                self.assertEqual(char_prompt, '成年修表师穿蓝色工作服')
                self.assertEqual(scene_prompt, '整洁明亮的工作间')
                self.assertEqual(krea.call_count, 1)
                self.assertEqual(moss.call_count, 1)
                expected_moss_prompt = char_prompt if character_model == 'moss' else scene_prompt
                self.assertEqual(moss.call_args.args[0], expected_moss_prompt)
                self.assertEqual(moss.call_args.kwargs,
                                 {'base_w': 1360, 'base_h': 768, 'upscale': 2})
                expected_krea_prompt = char_prompt if character_model == 'krea2' else scene_prompt
                self.assertEqual(krea.call_args.args[0], expected_krea_prompt)

    def test_split_settings_save_read_and_partial_save_preserve_both_choices(self):
        story = story_core.get_story('TEST')
        project_settings.save(story, {'character_image_model': 'krea2',
                                      'scene_image_model': 'moss'})
        restored = story_core.get_story('TEST')
        for key in ('settings', 'project_settings'):
            self.assertEqual(restored[key]['character_image_model'], 'krea2')
            self.assertEqual(restored[key]['scene_image_model'], 'moss')
        project_settings.save(restored, {'style': '电影级写实'})
        read = project_settings.get(story_core.get_story('TEST'))
        self.assertEqual(read['character_image_model'], 'krea2')
        self.assertEqual(read['scene_image_model'], 'moss')


class SplitImageModelApiTests(unittest.TestCase):
    setUp = api_fixture.MainMossApiTests.setUp
    request = api_fixture.MainMossApiTests.request

    def test_scene_prompt_uses_its_own_model_in_both_combinations(self):
        self.card = {'scene_id': 'SCENE_1', 'name': '修表店'}
        for character_model, scene_model in (('krea2', 'moss'), ('moss', 'krea2')):
            with self.subTest(character=character_model, scene=scene_model):
                self.story['settings'] = settings(character_model, scene_model)
                self.scene_writer.reset_mock()
                self.compile_scene.reset_mock()
                result = video_api.asset_prompt(None, '/api/asset/prompt',
                    dict(self.request(), kind='scene', owner_id='SCENE_1', generate=True))
                if scene_model == 'moss':
                    self.assertEqual(result['data']['prompt'], 'Moss场景正式自动稿')
                    self.scene_writer.assert_called_once()
                    self.compile_scene.assert_not_called()
                else:
                    self.assertEqual(result['data']['prompt'], 'Krea场景自动稿')
                    self.compile_scene.assert_called_once()
                    self.scene_writer.assert_not_called()
                self.character_writer.assert_not_called()

    def test_submitted_worker_and_recovery_keep_both_frozen_choices(self):
        self.story['settings'] = settings()
        seen = []

        def pair():
            return tuple(main_images.current(self.story['settings'], kind=kind)['model']
                         for kind in ('character', 'scene'))

        worker = saga_api._main_image_worker(self.story['story_id'],
                                             lambda: seen.append(pair()))
        snapshot = main_images.snapshot(self.story['settings'])
        self.story['settings'].update(character_image_model='moss', scene_image_model='krea2')
        worker()
        self.assertEqual(seen, [('krea2', 'moss')])
        with patch.object(saga_api, '_run_registered_make_bound', side_effect=lambda *args: pair()):
            recovered = saga_api._run_registered_make(self.story['story_id'],
                {'_recovery': {'request': {'main_image_options': snapshot}}})
        self.assertEqual(recovered, ('krea2', 'moss'))

    def test_produce_scene_freezes_split_snapshot_before_thread_starts(self):
        self.story['settings'] = settings()
        self.card = {'scene_id': 'SCENE_1', 'name': '修表店'}
        seen = []

        def generate(*args, **kwargs):
            seen.append(main_images.current(self.story['settings'], kind='scene')['model'])
            return {'visual_id': 'VISUAL_1', 'path': 'mock.png'}

        with patch.object(video_api.threading, 'Thread') as thread, \
             patch.object(asset_core, 'generate_scene_master', side_effect=generate), \
             patch.object(asset_core, 'adopt'):
            response = video_api.r_035(None, '/api/produce',
                dict(self.request(), kind='scene', owner_id='SCENE_1', prompt='整洁工作间'))
            self.assertTrue(response['ok'])
            self.story['settings'].update(character_image_model='moss', scene_image_model='krea2')
            thread.call_args.kwargs['target']()
        self.assertEqual(seen, ['moss'])


if __name__ == '__main__':
    unittest.main()
