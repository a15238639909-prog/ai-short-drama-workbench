"""Stable POV reference slots, using safe adult fixtures and no model calls."""
from contextlib import ExitStack
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from api import segment_api as api
from cores import asset_core, authoring, reference_ready, story_core, saga_core, store
from cores import video_continuity as continuity


class POVReferenceBindingTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='pov_reference_binding_'))
        self.partner_image = self.folder / 'partner.png'
        self.scene_image = self.folder / 'workshop.png'
        self.partner_image.write_bytes(b'adult partner reference')
        self.scene_image.write_bytes(b'ordinary workshop reference')
        self.camera = dict(name='林海', character_id='CAM', age='32', created=1)
        self.partner = dict(name='阿宁', character_id='PARTNER', age='30', created=2)
        self.cards = [self.camera, self.partner]
        self.scene = dict(name='修表店', scene_id='WORKSHOP')
        self.visuals = [dict(owner_id='PARTNER', kind='character', status='adopted', path=str(self.partner_image)),
                        dict(owner_id='WORKSHOP', kind='scene', status='adopted', path=str(self.scene_image))]
        self.settings = dict(pov='第一视角POV')
        self.episode = dict(no=1, brief='林海走进修表店，阿宁把修好的手表放上柜台。',
                            prose='林海抬起手，阿宁示意柜台上的手表。')
        self.story = dict(story_id='SAFE_POV_TEST', settings=self.settings,
                          saga=dict(episodes=[self.episode]))
        self.segment = dict(no=1, chars=['阿宁'], scene='修表店',
                            source_text=self.episode['prose'], plan=dict(flow='source-v1'))
        self.prompt = ('subject_definitions:\n<Subject 1>阿宁是<Picture 1>中的成年修表师。\n'
                       '<Subject 2>是<Picture 2>中的修表店。')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(store, 'DATA', self.folder / 'isolated_data'))
        self.stack.enter_context(patch.object(asset_core, 'list_assets', side_effect=self.assets))
        self.stack.enter_context(patch.object(story_core, 'get_story', side_effect=lambda sid: self.story))
        self.stack.enter_context(patch.object(saga_core, 'get_saga', side_effect=lambda sid: self.story['saga']))
        self.stack.enter_context(patch.object(authoring, '_ep', side_effect=lambda *args, **kwargs:
            (self.story['saga'], self.episode)))
        self.stack.enter_context(patch.object(authoring, 'cast_cards', side_effect=lambda sid: self.cards))
        self.stack.enter_context(patch.object(reference_ready, '_current_slices', return_value=None))
        self.stack.enter_context(patch.object(authoring, '_pov_spec', side_effect=lambda settings:
            (str((settings or {}).get('pov') or ''), {})))
        self.stack.enter_context(patch.object(Path, 'unlink', side_effect=AssertionError('no permanent unlink')))
        self.stack.enter_context(patch('shutil.rmtree', side_effect=AssertionError('no permanent deletion')))

    def assets(self, sid, kind):
        self.assertEqual(sid, 'SAFE_POV_TEST')
        return {'characters': self.cards, 'scenes': [self.scene], 'visuals': self.visuals}[kind]

    def test_single_visible_partner_keeps_character_then_scene_and_original_inputs(self):
        before = copy.deepcopy((self.segment, self.cards, self.settings))
        # The default viewer is external, even when only one partner is visible.
        order, camera = authoring.h3_char_order([self.partner], self.settings)
        self.assertEqual(camera, '我')
        self.assertEqual([row['name'] for row in order if row['name'] != camera], ['阿宁'])
        with self.assertRaisesRegex(ValueError, '未绑定的图片编号'):
            continuity.validate_prompt_references(self.prompt, [str(self.scene_image)])
        images = api.new_pack('SAFE_POV_TEST', self.segment, ep=1)
        self.assertEqual(images, [str(self.partner_image), str(self.scene_image)])
        continuity.validate_prompt_references(self.prompt, images)
        reference_ready.assert_references('SAFE_POV_TEST', [self.segment], ep=1)
        self.assertEqual((self.segment, self.cards, self.settings), before)

    def test_missing_partner_image_is_detected_instead_of_skipped_as_camera(self):
        self.visuals = [row for row in self.visuals if row['owner_id'] != 'PARTNER']
        with self.assertRaisesRegex(reference_ready.MissingReferencesError, '阿宁'):
            reference_ready.assert_references('SAFE_POV_TEST', [self.segment], ep=1)
        with self.assertRaisesRegex(reference_ready.MissingReferencesError, '阿宁'):
            api.new_pack('SAFE_POV_TEST', self.segment, ep=1)

    def test_explicit_camera_outside_segment_is_respected_even_if_not_first_card(self):
        self.cards = [self.partner, self.camera]
        self.settings['camera_owner'] = '林海'
        self.assertEqual(api.new_pack('SAFE_POV_TEST', self.segment, ep=1),
                         [str(self.partner_image), str(self.scene_image)])
        reference_ready.assert_references('SAFE_POV_TEST', [self.segment], ep=1)
        self.assertEqual(self.settings['camera_owner'], '林海')

    def test_existing_story_owner_resolution_does_not_depend_on_asset_creation_order(self):
        self.cards = [self.partner, self.camera]
        before = copy.deepcopy(self.settings)
        self.assertEqual(api.new_pack('SAFE_POV_TEST', self.segment, ep=1),
                         [str(self.partner_image), str(self.scene_image)])
        reference_ready.assert_references('SAFE_POV_TEST', [self.segment], ep=1)
        self.assertEqual(self.settings, before)

    def test_non_pov_keeps_existing_character_and_scene_order(self):
        self.settings['pov'] = '电影叙事'
        self.assertEqual(api.new_pack('SAFE_POV_TEST', self.segment, ep=1),
                         [str(self.partner_image), str(self.scene_image)])
        reference_ready.assert_references('SAFE_POV_TEST', [self.segment], ep=1)

    def test_empty_cast_and_camera_hands_do_not_require_camera_portrait(self):
        self.settings['camera_owner'] = '林海'
        for chars in ([], ['林海']):
            with self.subTest(chars=chars):
                segment = dict(self.segment, chars=chars)
                self.assertEqual(api.new_pack('SAFE_POV_TEST', segment, ep=1), [str(self.scene_image)])
                reference_ready.assert_references('SAFE_POV_TEST', [segment], ep=1)

    def test_explicit_reference_selection_keeps_user_order(self):
        segment = dict(self.segment, refs=[dict(n='工作间'), dict(n='搭档')])
        with patch.object(api, '_adopted_paths', return_value={
                '搭档': str(self.partner_image), '工作间': str(self.scene_image)}), \
                patch.object(api, 'new_pack', side_effect=AssertionError('explicit refs must not be rebuilt')):
            self.assertEqual(api._render_references('SAFE_POV_TEST', segment, ep=1),
                             [str(self.scene_image), str(self.partner_image)])

    def test_single_segment_button_keeps_episode_for_scene_alias_resolution(self):
        segment = dict(self.segment, scene='工作台', prompt=self.prompt)
        timeline = dict(scenes=[dict(no=1, segments=[segment])])
        request = dict(ep=2, scene_no=1, seg_no=1)
        with patch.object(api, '_load', return_value=(self.story, timeline)), \
                patch.object(api, '_ep_of', return_value=2), \
                patch.object(api, '_render_critical_problems', return_value=[]), \
                patch.object(reference_ready, 'resolve_scene_card', return_value=self.scene) as resolve, \
                patch.object(api, 'render_segment', return_value=dict(video='mock_result.mp4')) as render:
            result = api.gen_segment(None, '/api/timeline/SAFE_POV_TEST/generate-segment', request)
        self.assertTrue(result['ok'])
        resolve.assert_any_call('SAFE_POV_TEST', 2, '工作台', [self.scene])
        render.assert_called_once_with('SAFE_POV_TEST', 2, 1, 1, hd=False, override=request)

    def test_opening_reference_pack_keeps_episode_for_scene_alias_resolution(self):
        opening = dict(self.segment, k=1, chars=[], no_chars=True, scene='工作台',
                       prompt='<Subject 1>是<Picture 1>中的修表店。', seconds=4)
        timeline = dict(scenes=[], opening=[opening])
        with patch.object(api, '_load', return_value=(self.story, timeline)), \
                patch.object(api, '_render_params', return_value=('0.4', '16:9', 10)), \
                patch.object(reference_ready, 'resolve_scene_card', return_value=self.scene) as resolve, \
                patch('models.h3_client.generate', side_effect=RuntimeError('MOCK_RENDER_REACHED')) as generate:
            with self.assertRaisesRegex(RuntimeError, '^MOCK_RENDER_REACHED$'):
                api.render_opening('SAFE_POV_TEST', 2, 1)
        resolve.assert_any_call('SAFE_POV_TEST', 2, '工作台', [self.scene])
        generate.assert_called_once()
        self.assertEqual(generate.call_args.kwargs['images'], [str(self.scene_image)])


if __name__ == '__main__':
    unittest.main()
