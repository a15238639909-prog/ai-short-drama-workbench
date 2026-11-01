"""New-project POV portrait entry points, with real isolated asset adoption.

Only the language/image models are replaced. Fixtures and generated PNGs are
retained under a temporary directory; no production projects or models are used.
"""
from contextlib import ExitStack
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from api import saga_api, segment_api
from cores import (asset_core, authoring, director_shots, gen_history,
                   project_settings, reference_ready, saga_core, store, story_core)
from models import krea_client


class NewProjectPortraitTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='new_project_portraits_'))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(store, 'DATA', self.folder / 'data'))
        # authoring's output path is based on its module location. Keep the real
        # copy/adopt implementation, but route outputs into the same fixture.
        self.stack.enter_context(patch.object(authoring, '__file__',
            str(self.folder / 'application' / 'cores' / 'authoring.py')))
        self.stack.enter_context(patch.object(Path, 'unlink',
            side_effect=AssertionError('permanent deletion forbidden in this test')))
        self.stack.enter_context(patch('shutil.rmtree',
            side_effect=AssertionError('permanent deletion forbidden in this test')))
        self.stack.enter_context(patch.object(authoring, '_q',
            side_effect=AssertionError('real language model forbidden in this test')))
        self.stack.enter_context(patch.object(gen_history, 'record'))
        self.write_sheet = self.stack.enter_context(patch.object(authoring, 'write_char_sheet',
            return_value='三十岁成年修表师阿宁，蓝色工作服，完整全身三视图与面部特写，白色背景。'))
        self.generated = self.folder / 'model_result.png'
        # Valid one-pixel PNG: fixture represents a model response, not quality.
        import base64
        self.generated.write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII='))
        self.generate = self.stack.enter_context(patch.object(krea_client, 'generate',
            return_value={'output_path': str(self.generated)}))

    def project(self, *, source=None, character=True, legacy_empty_cast=False, no_images=False):
        source = source if source is not None else '我走进修表店，阿宁抬头看向我，把修好的手表放在柜台上。'
        settings = project_settings.defaults()
        settings.update(pov='第一视角POV', camera_owner='', world_type='东方现代',
                        one_line=source, no_images=no_images)
        store.save_json(store.DATA / 'global_defaults.json', settings)
        project = story_core.create_story(source, title='正常成人修表店测试')
        self.sid = project['story_id']
        self.assertEqual(project['settings']['camera_owner'], '')
        self.assertEqual(project['settings']['pov'], '第一视角POV')
        self.character = None
        if character:
            self.character = asset_core.create_character(self.sid, dict(
                name='阿宁', age=30, sex='女', char_type='人类',
                hair='黑色侧分短发', clothing='蓝色长袖工作服、灰色长裤、工作鞋',
                appearance_details='成年东亚女性，黑色眼睛，普通整洁工作装'))
            self.character['face_spec'] = {'face_shape': '椭圆脸'}
            asset_core.save_asset(self.sid, 'characters', self.character)
        project = story_core.get_story(self.sid)
        project['saga'] = {'episodes': [dict(no=1, season=1, brief=source,
            prose=source, pictures=source, timeline={'scenes': []})]}
        story_core.save_story(project)
        cards = asset_core.list_assets(self.sid, 'characters')
        effective = project_settings.get(project)
        owner = authoring.camera_owner_for(self.sid, effective, cards)
        shots = [dict(cast=['阿宁'] if character and '阿宁' in source else [],
                      camera='中景', end_state={}, blocks=[], seconds=8)]
        shots = director_shots.apply_pov(shots, '第一视角POV', owner)
        self.director = copy.deepcopy(shots[0])
        if legacy_empty_cast:
            # Exact missing-portrait shape from old plans: frozen director cast
            # empty and no timeline segments exist yet. No fake valid owner.
            shots[0]['cast'] = []
        authoring.freeze_slicing(self.sid, 1, source,
            [dict(text=source, seconds=8, scene_hint='修表店', director=shots[0])])
        self.assertEqual(saga_core.ep_timeline(self.sid, 1).get('scenes'), [])
        return project

    def assert_adopted_portrait(self):
        visuals = asset_core.list_assets(self.sid, 'visuals')
        self.assertEqual(len(visuals), 1)
        visual = visuals[0]
        self.assertEqual(visual['status'], 'adopted')
        self.assertEqual(visual['owner_id'], self.character['character_id'])
        actual = Path(visual['path'])
        self.assertTrue(actual.is_relative_to(self.folder))
        self.assertEqual(actual.read_bytes(), self.generated.read_bytes())
        self.assertEqual(reference_ready.adopted_paths(self.sid, 'character'),
                         {self.character['character_id']: str(actual)})
        self.assertTrue(asset_core.get_asset(self.sid, 'characters',
                                           self.character['character_id'])['frozen'])

    def test_new_project_default_pov_keeps_visible_partner_and_adopts_portrait(self):
        self.project()
        self.assertEqual(self.director['cast'], ['阿宁'])
        self.assertEqual([c['name'] for c in reference_ready.required_characters(self.sid, 1)], ['阿宁'])
        result = authoring.gen_char_images(self.sid, ep=1)
        self.assertEqual(result['ok'], 1)
        self.generate.assert_called_once()
        self.write_sheet.assert_called_once()
        self.assert_adopted_portrait()

    def test_empty_legacy_cast_without_timeline_still_generates_source_partner(self):
        self.project(legacy_empty_cast=True)
        result = reference_ready.ensure_characters(self.sid, 1)
        self.assertEqual(result['ok'], 1)
        self.assert_adopted_portrait()
        # Retry reads the real adopted visual; it never spends a second image.
        reference_ready.ensure_characters(self.sid, 1)
        self.generate.assert_called_once()

    def test_no_images_cannot_report_portraits_ready_when_required_image_missing(self):
        self.project(legacy_empty_cast=True, no_images=True)
        with self.assertRaisesRegex(reference_ready.MissingReferencesError, 'no_images'):
            reference_ready.ensure_characters(self.sid, 1)
        self.generate.assert_not_called()
        self.assertEqual(asset_core.list_assets(self.sid, 'visuals'), [])

    def test_failed_model_image_is_reported_as_missing_portrait(self):
        self.project(legacy_empty_cast=True)
        self.generate.side_effect = RuntimeError('模拟图像服务超时')
        with self.assertRaisesRegex(reference_ready.MissingReferencesError, '阿宁'):
            reference_ready.ensure_characters(self.sid, 1)
        self.assertEqual(asset_core.list_assets(self.sid, 'visuals'), [])

    def test_images_stage_continues_scenes_after_portrait_failure_but_stays_partial(self):
        self.project(legacy_empty_cast=True)
        self.generate.side_effect = RuntimeError('模拟图像服务不可用')
        job = dict(running=True, status='running', done=0, total=2, logs=[],
            _recovery={'phase': 'images', 'request': {'ep': 1, 'stage': 'images'}})
        with patch.object(saga_api, '_generation_step', side_effect=lambda j, key, fn, **kw: fn()), \
             patch.object(saga_api, '_generation_checkpoint') as checkpoint, \
             patch.object(saga_api, '_ep_scene_images') as scene_images, \
             patch.object(saga_api, '_job_event'), patch.object(saga_api, '_persist_job'):
            saga_api._run_registered_make(self.sid, job)
        self.assertNotEqual(job['status'], 'completed')
        self.assertFalse(job['running'])
        self.assertEqual(job['done'], 2)
        self.assertIn('阿宁', job['err'])
        checkpoint.assert_called_once()
        scene_images.assert_called_once()

    def test_real_environment_only_project_needs_no_character_portrait(self):
        self.project(character=False, source='空无一人的修表店，窗外雨滴落在玻璃上，墙钟指针缓慢转动。')
        self.assertEqual(reference_ready.required_characters(self.sid, 1), [])
        result = reference_ready.ensure_characters(self.sid, 1)
        self.assertEqual(result['ok'], 0)
        self.generate.assert_not_called()

    def test_explicit_character_camera_still_gets_identity_portrait_but_not_video_reference(self):
        self.project(source='阿宁以自己的视角检查柜台上的手表，镜头只拍到她戴手套的手。')
        project = story_core.get_story(self.sid)
        project_settings.save(project, {'camera_owner': '阿宁'})
        # Camera ownership affects this video's ref pack, not whether the
        # character library should contain a reusable identity image.
        self.assertEqual(reference_ready.required_characters(self.sid, 1), [])
        self.assertEqual([c['name'] for c in reference_ready.portrait_characters(self.sid, 1)], ['阿宁'])
        result = reference_ready.ensure_characters(self.sid, 1)
        self.assertEqual(result['ok'], 1)
        self.assert_adopted_portrait()
        self.assertEqual(segment_api.new_pack(self.sid, dict(chars=['阿宁'],
            scene='', source_text='阿宁戴手套的手拨动手表指针。'), ep=1), [])
        self.generate.assert_called_once()

    def test_future_episode_card_waits_while_existing_current_portrait_is_reused(self):
        self.project()
        future = asset_core.create_character(self.sid, dict(name='周师傅', age=45,
            sex='男', char_type='人类', hair='灰黑色侧分短发',
            clothing='灰色长袖工作服、黑色长裤', first_episode=2))
        self.assertEqual([c['name'] for c in reference_ready.portrait_characters(self.sid, 1)], ['阿宁'])
        reference_ready.ensure_characters(self.sid, 1)
        self.assert_adopted_portrait()
        reference_ready.ensure_characters(self.sid, 1)
        self.generate.assert_called_once()
        self.write_sheet.assert_called_once()
        self.assertNotIn(future['character_id'], reference_ready.adopted_paths(self.sid, 'character'))
        self.assertFalse(asset_core.get_asset(self.sid, 'characters', future['character_id'])['frozen'])


if __name__ == '__main__':
    unittest.main()
