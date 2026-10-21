"""A fresh POV project retains visible actors without naming the camera operator."""
from contextlib import ExitStack
import copy
import os
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from cores import asset_core, authoring, camera_view, video_continuity


class DefaultPOVOwnerTests(unittest.TestCase):
    def setUp(self):
        self.cards = [dict(name='阿宁', age=30, appearance='深蓝工作服的成年修表师'),
                      dict(name='林海', age=32, appearance='浅灰外套的成年顾客')]
        self.settings = dict(pov='第一视角POV', camera_owner='')

    def test_default_pov_is_external_viewer_with_one_visible_card(self):
        before = copy.deepcopy((self.cards, self.settings))
        self.assertEqual(camera_view.owner(self.cards[:1], self.settings), '我')
        self.assertEqual(authoring.camera_owner_for('SAFE_NEW_POV', self.settings,
                                                   self.cards[:1]), '我')
        ordered, camera = authoring.h3_char_order(self.cards[:1], self.settings)
        self.assertEqual(camera, '我')
        self.assertEqual(ordered, self.cards[:1])
        self.assertEqual((self.cards, self.settings), before)

    def test_default_does_not_depend_on_card_order_or_story_mentions(self):
        with (patch.object(asset_core, 'list_assets', side_effect=AssertionError('no inferred owner')),
                patch.object(authoring, '_ep', side_effect=AssertionError('no prose owner inference'))):
            for cards in (None, [], self.cards, list(reversed(self.cards))):
                with self.subTest(cards=cards):
                    self.assertEqual(authoring.camera_owner_for('SAFE_NEW_POV', self.settings,
                                                               cards), '我')
                    self.assertEqual(camera_view.owner(cards, self.settings), '我')

    def test_absent_whitespace_and_blank_camera_owner_use_same_default(self):
        for value in (None, '', '  '):
            settings = dict(pov='第一视角POV', camera_owner=value)
            with self.subTest(value=value):
                self.assertEqual(camera_view.owner(self.cards, settings), '我')
                self.assertEqual(authoring.camera_owner_for('SAFE_NEW_POV', settings,
                                                           self.cards), '我')
        self.assertEqual(camera_view.owner(self.cards, dict(pov='第一视角POV')), '我')

    def test_explicit_named_actor_keeps_camera_identity_and_reference_exclusion(self):
        settings = dict(self.settings, camera_owner='阿宁')
        self.assertEqual(authoring.camera_owner_for('SAFE_NEW_POV', settings, self.cards), '阿宁')
        ordered, camera = authoring.h3_char_order(self.cards, settings)
        self.assertEqual(camera, '阿宁')
        self.assertEqual([row['name'] for row in ordered], ['林海', '阿宁'])

    def test_explicit_camera_outside_cast_is_preserved(self):
        settings = dict(self.settings, camera_owner='成年游客')
        self.assertEqual(camera_view.owner(self.cards, settings), '成年游客')
        self.assertEqual(authoring.camera_owner_for('SAFE_NEW_POV', settings, []), '成年游客')
        ordered, camera = authoring.h3_char_order(self.cards, settings)
        self.assertEqual((ordered, camera), (self.cards, '成年游客'))

    def test_selfie_keeps_first_visible_card_as_default_operator(self):
        settings = dict(pov='自拍手持')
        self.assertEqual(camera_view.owner(self.cards, settings), '阿宁')
        self.assertEqual(authoring.camera_owner_for('SAFE_NEW_POV', settings, self.cards), '阿宁')
        self.assertEqual(authoring.h3_char_order(self.cards, settings), (self.cards, ''))

    def test_standard_camera_keeps_all_actor_references(self):
        settings = dict(pov='电影第三人称')
        self.assertEqual(authoring.h3_char_order(self.cards, settings), (self.cards, ''))

    def test_writer_receives_the_same_external_operator_for_fresh_project(self):
        settings = dict(self.settings)
        authoring._set_camera_owner('SAFE_NEW_POV', settings, self.cards)
        self.assertEqual(settings['camera_owner'], '我')
        self.assertEqual(camera_view.owner(self.cards[:1], settings), '我')

    def test_default_pov_prompt_binds_visible_actor_and_scene_to_two_images(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(authoring, '_h3_timeline_body', return_value=(
                '0—8秒：我主观视角，阿宁把手表放在柜台上。\n这一段结束时：手表在柜台上。',
                '手表在柜台上。')))
            stack.enter_context(patch.object(authoring, 'use_timeline_director', return_value=True))
            stack.enter_context(patch.object(authoring, '_h3_cine', return_value='真实摄影。'))
            prompt = authoring.h3_prompt('阿宁把修好的手表放在柜台上。', self.cards[:1],
                                         '修表店', self.settings, scene_image=True, seconds=8)
        self.assertRegex(prompt, r'<Subject\s*1>阿宁是<Picture 1>')
        self.assertIn('<Subject 2>是<Picture 2>', prompt)
        self.assertNotIn('阿宁是这段的拍摄者', prompt)
        video_continuity.validate_prompt_references(prompt, ['adult_actor.png', 'shop.png'])


if __name__ == '__main__':
    unittest.main()
