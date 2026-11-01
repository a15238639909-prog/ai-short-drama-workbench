"""CPU-only scene failure reporting and adopted-reference preservation."""
from contextlib import ExitStack
import copy
import os
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from api import saga_api
from cores import asset_core, authoring, project_settings, reference_ready, saga_core, scene_layer, story_core


class SceneAssetRecoveryTests(unittest.TestCase):
    def fixtures(self, error):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.cards = [dict(scene_id='S1', name='走廊', space='宽阔的石构走廊'),
                      dict(scene_id='S2', name='会客室', space='维护完好的木构会客室')]
        self.before = copy.deepcopy(self.cards)
        stack.enter_context(patch.object(saga_core, 'get_saga', return_value={
            'episodes': [dict(no=1, body='两位成年访客从走廊走到会客室。')]}))
        stack.enter_context(patch.object(story_core, 'get_story', return_value={'settings': {}}))
        stack.enter_context(patch.object(project_settings, 'get', return_value={}))
        stack.enter_context(patch.object(authoring, 'use_new_pipeline', return_value=False))
        stack.enter_context(patch.object(authoring, 'plan_places', return_value=[]))
        stack.enter_context(patch.object(authoring, 'fix_scene_spaces', return_value=[]))
        stack.enter_context(patch.object(scene_layer, '_scene_headers', return_value=[
            dict(location=card['name'], space=card['space']) for card in self.cards]))
        stack.enter_context(patch.object(reference_ready, 'scene_ids_for_episode', return_value={'S1', 'S2'}))
        stack.enter_context(patch.object(saga_api, '_sync_scene_cards_from_body'))
        self.events = stack.enter_context(patch.object(saga_api, '_job_event'))
        stack.enter_context(patch.object(saga_api, '_set_step'))
        stack.enter_context(patch.object(saga_api, '_complete_step'))
        stack.enter_context(patch.object(asset_core, 'list_assets', side_effect=lambda sid, kind:
            self.cards if kind == 'scenes' else [dict(owner_id='S2', status='adopted', path='adopted.png')]
            if kind == 'visuals' else []))
        stack.enter_context(patch('os.path.exists', return_value=True))
        self.generate = stack.enter_context(patch.object(asset_core, 'generate_scene_master', side_effect=error))
        self.adopt = stack.enter_context(patch.object(asset_core, 'adopt'))

    def test_prompt_error_names_missing_scene_and_preserves_adopted_room(self):
        self.fixtures(ValueError('Moss提示词格式：item.text须为非空文字'))
        job = {}
        saga_api._ep_scene_images('SCENE_FIXTURE', 1, job)
        self.generate.assert_called_once()
        self.assertEqual(self.generate.call_args.args[:2], ('SCENE_FIXTURE', 'S1'))
        self.adopt.assert_not_called()
        self.assertEqual(self.cards, self.before)
        failure = job['scene_image_failures']['S1']
        self.assertEqual(failure['stage'], 'prompt')
        warning = saga_api._missing_scene_image_warning({'S1'}, self.cards, job['scene_image_failures'])
        self.assertIn('走廊', warning)
        self.assertIn('尚未提交生图', warning)
        self.assertIn('item.text', warning)
        self.assertNotIn('会客室', warning)

    def test_game_stop_propagates_without_recording_a_retryable_asset_failure(self):
        self.fixtures(RuntimeError('PUBG正在运行'))
        job = {}
        with self.assertRaisesRegex(RuntimeError, 'PUBG'):
            saga_api._ep_scene_images('SCENE_FIXTURE', 1, job)
        self.generate.assert_called_once()
        self.assertEqual(job['scene_image_failures'], {})

    def test_image_error_is_not_misreported_as_pre_submission_prompt_error(self):
        job = {}
        saga_api._scene_image_failure(job, {'scene_id': 'S1', 'name': '走廊'}, RuntimeError('ComfyUI连接断开'))
        self.assertEqual(job['scene_image_failures']['S1']['stage'], 'image')
        self.assertNotIn('尚未提交', job['scene_image_failures']['S1']['message'])
        self.assertEqual(saga_api._missing_scene_image_warning({'UNKNOWN'}, [], {}),
                         '本话仍缺少有效场景图：UNKNOWN')

    def test_wrong_output_mode_is_a_prompt_stage_failure(self):
        job = {}
        saga_api._scene_image_failure(job, {'scene_id': 'S1', 'name': '走廊'},
            ValueError('Qwen未按本次画面格式输出；本次未提交图片任务'))
        self.assertEqual(job['scene_image_failures']['S1']['stage'], 'prompt')

    def test_scene_content_error_is_reported_before_image_submission(self):
        from cores.scene_prompt_content import ScenePromptContentError
        job = {}
        saga_api._scene_image_failure(job, {'scene_id': 'S1', 'name': '走廊'},
            ScenePromptContentError('场景原文未要求室内青苔'))
        self.assertEqual(job['scene_image_failures']['S1']['stage'], 'prompt')


if __name__ == '__main__':
    unittest.main()
