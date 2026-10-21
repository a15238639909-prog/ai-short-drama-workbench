"""A first-person viewer is not automatically the only visible adult character.

Safe synthetic projects only. No model, media generation, or production reads.
Temporary fixtures are retained rather than permanently deleted.
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
from cores import asset_core, authoring, camera_view, project_settings, reference_ready, saga_core, story_core, store
from cores import video_continuity


class ExternalViewerReferenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='pov_external_adult_'))
        self.portrait = self.folder / 'adult_watchmaker.png'
        self.scene_image = self.folder / 'watch_shop.png'
        self.portrait.write_bytes(b'safe adult portrait fixture')
        self.scene_image.write_bytes(b'safe ordinary scene fixture')
        self.person = dict(name='阿宁', character_id='ADULT_NING', age='30',
                           appearance='黑色短发，深蓝色工作服的成年修表师')
        self.cards = [self.person]
        self.scene = dict(name='修表店', scene_id='SAFE_SHOP')
        self.settings = dict(pov='第一视角POV', camera_owner='我')
        self.source = '我站在柜台前，阿宁抬头看向我，把修好的手表放在柜台上。'
        self.episode = dict(no=1, brief='我在修表店看阿宁修表，阿宁需要露脸。',
                            prose=self.source, pictures=self.source)
        self.story = dict(story_id='SAFE_EXTERNAL_POV', settings=self.settings,
                          saga=dict(episodes=[self.episode]))
        self.segment = dict(no=1, chars=['阿宁'], scene='修表店', source_text=self.source,
                            plan=dict(flow='source-v1'))
        self.timeline = dict(scenes=[dict(no=1, segments=[self.segment])])
        self.slices = None
        self.visuals = [dict(owner_id='SAFE_SHOP', kind='scene', status='adopted',
                             path=str(self.scene_image))]
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        patches = (
            patch.object(store, 'DATA', self.folder / 'isolated_data'),
            patch.object(asset_core, 'list_assets', side_effect=self.assets),
            patch.object(story_core, 'get_story', side_effect=lambda sid: self.story),
            patch.object(saga_core, 'get_saga', side_effect=lambda sid: self.story['saga']),
            patch.object(saga_core, 'ep_timeline', side_effect=lambda sid, ep: self.timeline),
            patch.object(authoring, '_ep', side_effect=lambda *args, **kwargs:
                         (self.story['saga'], self.episode)),
            patch.object(authoring, 'cast_cards', side_effect=lambda sid: self.cards),
            patch.object(reference_ready, '_current_slices', side_effect=lambda *args: self.slices),
            patch.object(authoring, '_pov_spec', side_effect=lambda settings:
                         (str((settings or {}).get('pov') or ''), {})),
            patch.object(Path, 'unlink', side_effect=AssertionError('no permanent unlink')),
            patch('shutil.rmtree', side_effect=AssertionError('no permanent deletion')),
        )
        for item in patches:
            self.stack.enter_context(item)

    def assets(self, sid, kind):
        self.assertEqual(sid, 'SAFE_EXTERNAL_POV')
        return dict(characters=self.cards, scenes=[self.scene], visuals=self.visuals)[kind]

    def adopt_portrait(self):
        self.visuals.append(dict(owner_id='ADULT_NING', kind='character', status='adopted',
                                 path=str(self.portrait)))

    def test_explicit_external_viewer_is_not_the_only_visible_person(self):
        self.assertEqual(camera_view.owner(self.cards, self.settings), '我')

    def test_first_mention_or_most_mentions_do_not_override_explicit_external_viewer(self):
        self.episode['brief'] = '阿宁对我展示修好的手表。'
        self.episode['prose'] = '阿宁抬头。阿宁朝我微笑。阿宁举起手表。'
        before = copy.deepcopy((self.story, self.cards))
        owner = authoring.camera_owner_for('SAFE_EXTERNAL_POV', self.settings, self.cards)
        self.assertEqual(owner, '我')
        self.assertEqual((self.story, self.cards), before)

    def test_writer_camera_settings_keep_explicit_external_viewer(self):
        settings = copy.deepcopy(self.settings)
        authoring._set_camera_owner('SAFE_EXTERNAL_POV', settings, self.cards)
        self.assertEqual(settings['camera_owner'], '我')
        effective = reference_ready.reference_camera_settings('SAFE_EXTERNAL_POV', settings,
                                                             self.cards)
        self.assertEqual(effective['camera_owner'], '我')

    def test_missing_visible_portrait_is_required_for_external_viewer(self):
        with self.assertRaisesRegex(reference_ready.MissingReferencesError, '阿宁'):
            reference_ready.assert_references('SAFE_EXTERNAL_POV', [self.segment], ep=1)
        with self.assertRaisesRegex(reference_ready.MissingReferencesError, '阿宁'):
            segment_api.new_pack('SAFE_EXTERNAL_POV', self.segment, ep=1)

    def test_external_viewer_keeps_character_before_scene_in_reference_pack(self):
        self.adopt_portrait()
        before = copy.deepcopy((self.settings, self.segment, self.cards))
        images = segment_api.new_pack('SAFE_EXTERNAL_POV', self.segment, ep=1)
        self.assertEqual(images, [str(self.portrait), str(self.scene_image)])
        self.assertEqual((self.settings, self.segment, self.cards), before)

    def test_character_image_readiness_does_not_treat_visible_partner_as_camera(self):
        result = reference_ready.required_characters('SAFE_EXTERNAL_POV', 1)
        self.assertEqual([row['name'] for row in result], ['阿宁'])

    def test_empty_frozen_director_cast_cannot_override_visible_timeline_character(self):
        self.slices = [dict(text=self.source, director=dict(flow='source-v1', cast=[]))]
        self.settings['camera_owner'] = '我'
        before = copy.deepcopy((self.timeline, self.slices))
        result = reference_ready.required_characters('SAFE_EXTERNAL_POV', 1)
        self.assertEqual([row['name'] for row in result], ['阿宁'])
        self.assertEqual((self.timeline, self.slices), before)

    def test_explicit_named_camera_still_needs_no_portrait(self):
        self.settings['camera_owner'] = '阿宁'
        self.segment['chars'] = ['阿宁']
        self.assertEqual(segment_api.new_pack('SAFE_EXTERNAL_POV', self.segment, ep=1),
                         [str(self.scene_image)])
        self.assertEqual(reference_ready.required_characters('SAFE_EXTERNAL_POV', 1), [])

    def test_explicit_external_camera_is_not_replaced_with_visible_partner(self):
        self.settings['camera_owner'] = '我'
        self.assertEqual(authoring.camera_owner_for('SAFE_EXTERNAL_POV', self.settings, self.cards), '我')
        self.assertEqual(camera_view.owner(self.cards, self.settings), '我')
        self.assertEqual([row['name'] for row in reference_ready.required_characters(
            'SAFE_EXTERNAL_POV', 1)], ['阿宁'])

    def test_explicit_external_camera_survives_an_environment_only_segment(self):
        self.assertEqual(authoring.camera_owner_for('SAFE_EXTERNAL_POV', self.settings, []), '我')
        settings = dict(self.settings)
        authoring._set_camera_owner('SAFE_EXTERNAL_POV', settings, [])
        self.assertEqual(settings['camera_owner'], '我')

    def test_selfie_keeps_the_named_visible_photographer(self):
        self.settings['pov'] = '自拍手持'
        self.settings.pop('camera_owner')
        self.assertEqual(camera_view.owner(self.cards, self.settings), '阿宁')
        self.assertEqual(authoring.camera_owner_for('SAFE_EXTERNAL_POV', self.settings, self.cards), '阿宁')
        self.adopt_portrait()
        self.assertEqual(segment_api.new_pack('SAFE_EXTERNAL_POV', self.segment, ep=1),
                         [str(self.portrait), str(self.scene_image)])

    def test_intentional_empty_shot_remains_scene_only(self):
        self.segment.update(chars=[], no_chars=True)
        self.slices = [dict(text='柜台上的手表静静放着。',
                            director=dict(flow='source-v1', cast=[]))]
        self.assertEqual(reference_ready.required_characters('SAFE_EXTERNAL_POV', 1), [])
        self.assertEqual(segment_api.new_pack('SAFE_EXTERNAL_POV', self.segment, ep=1),
                         [str(self.scene_image)])

    def test_external_viewer_prompt_uses_portrait_instead_of_partner_camera_binding(self):
        with patch.object(authoring, '_h3_timeline_body', return_value=(
                '0—8秒：我主观视角，阿宁看向镜头，把手表放在柜台上。\n这一段结束时：手表在柜台上。',
                '手表在柜台上。')), \
             patch.object(authoring, 'use_timeline_director', return_value=True), \
             patch.object(authoring, '_h3_cine', return_value='真实摄影。'):
            prompt = authoring.h3_prompt(self.source, self.cards, '修表店', self.settings,
                                         scene_image=True, seconds=8)
        self.assertRegex(prompt, r'<Subject\s*1>阿宁')
        self.assertIn('<Picture 1>', prompt)
        self.assertIn('<Subject 2>是<Picture 2>', prompt)
        self.assertNotIn('阿宁是这段的拍摄者', prompt)
        self.assertNotIn('says:<d>', prompt)
        video_continuity.validate_prompt_references(prompt,
                                                   [str(self.portrait), str(self.scene_image)])

    def test_partial_project_settings_save_preserves_explicit_external_viewer(self):
        for storage in ('settings', 'project_settings'):
            with self.subTest(storage=storage):
                story = dict(story_id='SAFE_EXTERNAL_POV', **{
                    storage: dict(pov='第一视角POV', camera_owner='我')})
                self.assertEqual(project_settings.get(story)['camera_owner'], '我')
                with patch.object(story_core, 'save_story') as persist:
                    project_settings.save(story, dict(video_steps='20'))
                persist.assert_called_once_with(story)
                self.assertEqual(project_settings.get(story)['camera_owner'], '我')
                self.assertEqual(story['settings']['camera_owner'], '我')
                self.assertEqual(story['project_settings']['camera_owner'], '我')
                self.assertEqual(story['settings']['video_steps'], '20')

    def test_character_image_route_never_marks_failed_or_exception_as_completed(self):
        from models import gpu_manager

        class ImmediateThread:
            def __init__(self, *, target, daemon):
                self.target = target

            def start(self):
                self.target()

        outcomes = (
            ('failed_result', dict(ok=0, total=1, failed=['阿宁：模拟出图失败']), None),
            ('exception', None, RuntimeError('模拟服务暂不可用')),
            # A true empty shot is successful because nobody needs a portrait;
            # it must retain that explanation instead of claiming all cards have images.
            ('no_required_characters', dict(ok=0, failed=[],
                                           note='本次没有需要生成人设图的出镜人物'), None),
        )
        for label, result, error in outcomes:
            with self.subTest(outcome=label), ExitStack() as stack:
                job = dict(running=False, status='idle', done=0, total=1, percent=0,
                           logs=[], err='')

                def start(*args):
                    job.update(running=True, status='running')
                    return job

                stack.enter_context(patch.object(saga_api, '_job', return_value=job))
                stack.enter_context(patch.object(saga_api, '_start_job', side_effect=start))
                stack.enter_context(patch.object(saga_api, '_persist_job'))
                events = stack.enter_context(patch.object(saga_api, '_job_event'))
                stack.enter_context(patch.object(saga_api.threading, 'Thread', ImmediateThread))
                stack.enter_context(patch.object(saga_api.runtime_reset, 'worker', side_effect=lambda fn: fn))
                stack.enter_context(patch.object(saga_api.runtime_reset, 'assert_current', return_value=True))
                stack.enter_context(patch.object(gpu_manager, 'assert_local_run_allowed',
                                                side_effect=AssertionError('must not check or launch a real model')))
                generate = stack.enter_context(patch.object(authoring, 'gen_char_images',
                    return_value=result, side_effect=error))
                reply = saga_api.saga_gen_char_images(None,
                    '/api/saga/SAFE_EXTERNAL_POV/gen-char-images', {})
                self.assertTrue(reply['ok'])
                generate.assert_called_once()
                self.assertFalse(job['running'])
                if error or (result or {}).get('failed'):
                    self.assertEqual(job['status'], 'failed')
                    self.assertTrue(job['err'])
                    self.assertLess(job['percent'], 100)
                else:
                    self.assertEqual(job['status'], 'completed')
                    self.assertTrue(any('本次没有需要生成人设图的出镜人物' in str(call.args)
                                        for call in events.call_args_list))


if __name__ == '__main__':
    unittest.main()
