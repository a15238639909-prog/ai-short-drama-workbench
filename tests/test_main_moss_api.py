"""Main asset API contracts; all model work and threads are replaced."""
from contextlib import ExitStack
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ['V41_NO_MODEL'] = '1'

from api import video_api, saga_api
from cores import asset_core, generation_recovery, main_images, project_settings
from cores import quality_core, runtime_reset, saga_core, story_core


class MainMossApiTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.story = {'story_id': 'STORY_MOSS_API', 'settings': {
            'image_model': 'moss', 'image_quality': '2x', 'style': '电影级写实'}}
        self.card = {'character_id': 'CHAR_1', 'scene_id': 'SCENE_1',
                     'name': '成年修表师', 'age': 30}
        self.stack.enter_context(patch.object(story_core, 'get_story', return_value=self.story))
        self.stack.enter_context(patch.object(project_settings, 'get',
            side_effect=lambda story: dict(story.get('settings') or {})))
        self.stack.enter_context(patch.object(asset_core, 'get_asset',
            side_effect=lambda *args: copy.deepcopy(self.card)))
        self.saved = self.stack.enter_context(patch.object(asset_core, 'save_asset'))
        self.character_writer = self.stack.enter_context(patch.object(main_images, 'write_character',
            return_value='Moss人物正式自动稿'))
        self.scene_writer = self.stack.enter_context(patch.object(main_images, 'write_scene',
            return_value='Moss场景正式自动稿'))
        self.compile_scene = self.stack.enter_context(patch.object(quality_core, 'compile_scene_prompt',
            return_value='Krea场景自动稿'))
        self.stack.enter_context(patch.object(video_api, '_PRODUCE_JOBS', {}))
        self.stack.enter_context(patch.object(runtime_reset, 'assert_current'))
        self.stack.enter_context(patch.object(runtime_reset, 'worker', side_effect=lambda target: target))

    def request(self, **extra):
        return dict(story_id=self.story['story_id'], owner_id='CHAR_1', kind='character', **extra)

    def prompt(self, **extra):
        return video_api.asset_prompt(None, '/api/asset/prompt', self.request(**extra))['data']

    def test_read_and_reset_never_generate_or_reuse_another_models_draft(self):
        self.card['image_prompt'] = '历史Krea人物稿'
        self.card['prompt_override'] = '历史Krea手稿'
        for scene in (False, True):
            data = self.prompt() if not scene else video_api.asset_prompt(None,
                '/api/asset/prompt', dict(self.request(), kind='scene'))['data']
            self.assertEqual(data['prompt'], '')
            self.assertTrue(data['needs_generation'])
        self.card = main_images.bind_prompt(self.card, 'Moss手改', self.story['settings'],
                                           field='prompt_override')
        self.assertEqual(self.prompt(reset=True, generate=True)['prompt'], '')
        self.character_writer.assert_not_called()
        self.scene_writer.assert_not_called()
        self.compile_scene.assert_not_called()
        self.assertNotIn('prompt_override_model', self.saved.call_args.args[2])

    def test_explicit_generate_uses_the_same_moss_writers_as_generation(self):
        self.assertEqual(self.prompt(generate=True)['prompt'], 'Moss人物正式自动稿')
        self.character_writer.assert_called_once_with(self.story['story_id'], self.card,
                                                       self.story['settings'])
        data = video_api.asset_prompt(None, '/api/asset/prompt',
            dict(self.request(generate=True), kind='scene'))['data']
        self.assertEqual(data['prompt'], 'Moss场景正式自动稿')
        self.scene_writer.assert_called_once_with(self.story['story_id'], self.card,
                                                  self.story['settings'])
        self.compile_scene.assert_not_called()

    def test_manual_save_preserves_text_and_binds_model_and_canvas(self):
        text = '  第一行\n第二行  \n'
        result = video_api.asset_prompt_save(None, '/api/asset/prompt/save',
                                             self.request(prompt=text))
        self.assertTrue(result['ok'])
        self.card = self.saved.call_args.args[2]
        self.assertEqual(self.card['prompt_override'], text)
        self.assertEqual(self.card['prompt_override_model'], 'moss')
        self.assertEqual((self.card['prompt_override_base_w'],
                          self.card['prompt_override_base_h'],
                          self.card['prompt_override_upscale']), (1360, 768, 2))
        self.assertEqual(self.prompt()['prompt'], text)
        self.story['settings']['image_model'] = 'krea2'
        self.assertEqual(self.prompt()['prompt'], '')
        self.character_writer.assert_not_called()

    def test_existing_krea_scene_read_keeps_deterministic_compiler(self):
        self.story['settings']['image_model'] = 'krea2'
        result = video_api.asset_prompt(None, '/api/asset/prompt',
            dict(self.request(), kind='scene'))['data']
        self.assertEqual(result['prompt'], 'Krea场景自动稿')
        self.compile_scene.assert_called_once()
        self.scene_writer.assert_not_called()

    def test_produce_freezes_options_before_thread_and_preserves_explicit_prompt(self):
        prompt = '  用户完整手稿\n'
        seen = []

        def generate(*args, **kwargs):
            seen.append((main_images.current(self.story['settings']), kwargs['custom_prompt']))
            return {'visual_id': 'VISUAL_1', 'path': 'mock.png'}

        with patch.object(video_api.threading, 'Thread') as thread, \
             patch.object(asset_core, 'generate_character_master', side_effect=generate), \
             patch.object(asset_core, 'adopt'):
            result = video_api.r_035(None, '/api/produce', self.request(prompt=prompt))
            self.assertTrue(result['ok'])
            worker = thread.call_args.kwargs['target']
            self.story['settings'].update(image_model='krea2', image_quality='4k')
            worker()
        self.assertEqual(seen, [(main_images.snapshot({'image_model': 'moss'}), prompt)])
        self.assertFalse(video_api._PRODUCE_JOBS['STORY_MOSS_API:CHAR_1']['running'])

    def test_produce_does_not_promote_old_krea_override_to_moss(self):
        self.card['prompt_override'] = '旧Krea手稿'
        with patch.object(video_api.threading, 'Thread') as thread, \
             patch.object(asset_core, 'generate_character_master', return_value={}) as generate:
            video_api.r_035(None, '/api/produce', self.request())
            thread.call_args.kwargs['target']()
        self.assertIsNone(generate.call_args.kwargs['custom_prompt'])

    def test_legacy_saga_image_worker_freezes_at_submission(self):
        seen = []
        worker = saga_api._main_image_worker(self.story['story_id'],
            lambda: seen.append(main_images.current(self.story['settings'])))
        self.story['settings'].update(image_model='krea2', image_quality='4k')
        worker()
        self.assertEqual(seen, [main_images.snapshot({'image_model': 'moss'})])

    def test_make_persists_snapshot_and_recovery_uses_it(self):
        folder = Path(tempfile.mkdtemp(prefix='moss_api_recovery_'))
        recovery = generation_recovery.RecoveryStore(folder)
        sid = self.story['story_id']
        with patch.object(generation_recovery, 'RecoveryStore', return_value=recovery), \
             patch.object(saga_api, '_job', return_value={}), \
             patch.object(saga_api, '_start_job', return_value={}), \
             patch.object(saga_api, '_persist_job'), \
             patch.object(saga_api, '_timeline_busy', return_value=False), \
             patch.object(saga_api, '_generation_input_signature', return_value='input-v1'), \
             patch.object(saga_core, 'get_saga', return_value={'episodes': [{'no': 1, 'brief': '测试故事'}]}), \
             patch.object(saga_api.threading, 'Thread'):
            result = saga_api.saga_make(None, '/api/saga/%s/make' % sid, {'stage': 'images'})
        self.assertTrue(result['ok'])
        record = recovery.load(sid)
        options = main_images.snapshot(self.story['settings'])
        self.assertEqual(record['request']['main_image_options'], options)
        self.story['settings'].update(image_model='krea2', image_quality='4k')
        with patch.object(saga_api, '_run_registered_make_bound',
                side_effect=lambda *args: main_images.current(self.story['settings'])):
            self.assertEqual(saga_api._run_registered_make(sid, {'_recovery': record}), options)

    def test_old_registered_requests_remain_krea_even_when_project_now_moss(self):
        with patch.object(saga_api, '_run_registered_make_bound',
                side_effect=lambda *args: main_images.current(self.story['settings'])):
            result = saga_api._run_registered_make(self.story['story_id'],
                                                  {'_recovery': {'request': {}}})
        self.assertEqual(result, {'model': 'krea2', 'quality': 'workflow'})


if __name__ == '__main__':
    unittest.main()
