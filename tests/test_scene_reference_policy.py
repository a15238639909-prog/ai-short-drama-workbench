"""Scene entrance and image-token regression tests; no media or model calls."""
import copy
import os
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from cores import asset_core, reference_ready, saga_core
from cores import scene_reference_policy as policy


class SceneEntranceTests(unittest.TestCase):
    def setUp(self):
        self.cards = [{'name': '修表店', 'scene_id': 'SHOP'}, {'name': '小院', 'scene_id': 'YARD'}]
        self.aliases = {'修表店': self.cards[0], '工作台前': self.cards[0],
                        '店门内': self.cards[0], '小院': self.cards[1]}
        self.assets = patch.object(asset_core, 'list_assets', return_value=self.cards)
        self.resolve = patch.object(reference_ready, 'resolve_scene_card',
                                    side_effect=lambda sid, ep, name, cards: self.aliases.get(name))
        self.assets.start()
        self.resolve.start()
        self.addCleanup(self.assets.stop)
        self.addCleanup(self.resolve.stop)

    @staticmethod
    def timeline(names):
        return {'scenes': [{'no': 1, 'segments': [dict(no=i + 1, scene=name)
                                                for i, name in enumerate(names)]}]}

    def decisions(self, timeline):
        return [policy.include_scene('TEST', row, ep=2, timeline=timeline)
                for scene in timeline['scenes'] for row in scene['segments']]

    def test_each_scene_uses_one_reference_only_on_its_first_planned_use(self):
        self.assertEqual(self.decisions(self.timeline(['修表店', '修表店', '小院', '小院', '修表店'])),
                         [True, False, True, False, False])

    def test_returning_camera_alias_is_the_same_previously_used_scene_id(self):
        timeline = self.timeline(['修表店', '小院', '店门内', '工作台前'])
        self.assertEqual(self.decisions(timeline), [True, True, False, False])

    def test_legacy_source_scope_cannot_force_scene_images_on_every_segment(self):
        timeline = self.timeline(['修表店', '修表店', '小院', '小院', '修表店'])
        for row in timeline['scenes'][0]['segments']:
            row['plan'] = {'flow': 'source-v1', 'scene_reference_scope': 'current-scene-v1'}
        self.assertEqual(self.decisions(timeline), [True, False, True, False, False])

    def test_canonical_ids_merge_camera_position_aliases(self):
        self.assertEqual(self.decisions(self.timeline(['修表店', '工作台前', '店门内', '小院'])),
                         [True, False, False, True])

    def test_no_generated_video_is_required_or_consulted(self):
        timeline = self.timeline(['修表店', '修表店'])
        expected = self.decisions(timeline)
        timeline['scenes'][0]['segments'][0].update(video='missing.mp4', stale_video=True,
                                                 render_request={'status': 'failed'})
        self.assertEqual(self.decisions(timeline), expected)

    def test_same_set_across_container_scenes_does_not_restart_reference(self):
        timeline = {'scenes': [{'no': 1, 'location': '修表店', 'segments': [{'no': 1}]},
                               {'no': 2, 'name': '工作台前', 'segments': [{'no': 2}, {'no': 3}]}]}
        self.assertEqual(self.decisions(timeline), [True, False, False])

    def test_segment_scene_wins_over_stale_container_location(self):
        timeline = self.timeline(['修表店', '小院', '小院'])
        timeline['scenes'][0]['location'] = '修表店'
        self.assertEqual(self.decisions(timeline), [True, True, False])

    def test_unique_copy_and_stable_id_can_be_located(self):
        timeline = self.timeline(['修表店', '修表店'])
        copied = copy.deepcopy(timeline['scenes'][0]['segments'][1])
        self.assertFalse(policy.include_scene('TEST', copied, ep=1, timeline=timeline))
        copied['segment_id'] = 'NEXT'
        timeline['scenes'][0]['segments'][1]['segment_id'] = 'NEXT'
        copied.pop('no')
        self.assertFalse(policy.include_scene('TEST', copied, ep=1, timeline=timeline))

    def test_ambiguous_or_missing_rows_keep_their_original_reference(self):
        timeline = {'scenes': [{'segments': [{'no': 1, 'scene': '修表店'},
                                            {'no': 1, 'scene': '修表店'}]}]}
        self.assertTrue(policy.include_scene('TEST', {'no': 1, 'scene': '修表店'}, timeline=timeline))
        self.assertTrue(policy.include_scene('TEST', {'no': 9, 'scene': '修表店'}, timeline=timeline))
        self.assertTrue(policy.include_scene('TEST', {}, timeline={'scenes': []}))

    def test_missing_scene_between_rows_is_conservative(self):
        timeline = self.timeline(['修表店', '', '修表店'])
        self.assertEqual(self.decisions(timeline), [True, True, True])

    def test_known_continuation_after_unknown_boundary_does_not_repeat_again(self):
        timeline = self.timeline(['修表店', '', '修表店', '工作台前'])
        self.assertEqual(self.decisions(timeline), [True, True, True, False])

    def test_explicit_legacy_location_names_use_exact_equality(self):
        timeline = self.timeline(['北海港', '北海港', '北海港灯塔'])
        self.assertEqual(self.decisions(timeline), [True, False, True])

    def test_timeline_is_loaded_only_when_not_supplied(self):
        timeline = self.timeline(['修表店', '修表店'])
        row = timeline['scenes'][0]['segments'][1]
        with patch.object(saga_core, 'ep_timeline', return_value=timeline) as loaded:
            self.assertFalse(policy.include_scene('TEST', row, ep=2))
            loaded.assert_called_once_with('TEST', 2)
        with patch.object(saga_core, 'ep_timeline', side_effect=AssertionError('unnecessary read')):
            self.assertFalse(policy.include_scene('TEST', row, ep=2, timeline=timeline))

    def test_planned_segment_numbers_define_order_inside_each_scene(self):
        timeline = self.timeline(['修表店', '修表店', '小院'])
        timeline['scenes'][0]['segments'].reverse()
        self.assertEqual(self.decisions(timeline), [True, False, True])

    def test_scene_only_requires_explicit_empty_cast_evidence(self):
        for row in ({'no_chars': True}, {'shot': '远景', 'chars': []},
                    {'chars': [], 'plan': {'flow': 'source-v1'}}):
            self.assertTrue(policy.is_scene_only(row))
        for row in ({}, {'chars': []}, {'shot': '近景', 'chars': ['成年人甲']}):
            self.assertFalse(policy.is_scene_only(row))


class SceneImagePromptTests(unittest.TestCase):
    @staticmethod
    def six_fields(definitions, text='<Subject 1>观察<Subject 2>。'):
        return ('subject_definitions:\n' + definitions + '\n\nsummary:\n继续\n\n'
                'retention_analysis:\n<Picture 1>固定身份；<Picture 2>固定空间。\n\n'
                'detailed_description:\n[Shot 1] ' + text + '\n\n'
                'overall_soundscape:\n钟表走动。\n\nnon_diegetic_music:\nN/A')

    def test_english_environment_subject_remains_and_other_tokens_close_gaps(self):
        original = self.six_fields('<Subject 1> is 成人甲, the character from <Picture 1>.\n'
                                   '<Subject 2> is 修表店, the environment from <Picture 2>.\n'
                                   '<Subject 3> is 成人乙, the character from <Picture 3>.',
                                   '<Subject 1>把手表交给<Subject 3>。<Subject 3> says, <d>修好了。</d>')
        updated = policy.strip_scene_images(original, [2])
        self.assertIn('<Subject 2> is 修表店, the environment.', updated)
        self.assertIn('<Subject 3> is 成人乙, the character from <Picture 2>.', updated)
        self.assertIn('<Subject 1>把手表交给<Subject 3>。<Subject 3> says, <d>修好了。</d>', updated)
        self.assertIn('<Subject 2>固定空间。', updated)
        self.assertIn('from <Picture 2>', original)

    def test_chinese_environment_keeps_subject_location_and_authored_text(self):
        original = ('<Subject1>成人甲是<Picture 1>保留参考图中的面部特征和工作服。\n'
                    '<Subject 3>是<Picture 2>修表店场景，保留参考图中的空间结构、材质、光线和色调。\n'
                    '0—8秒：<Subject1> (S1)观察柜台上的手表。\n'
                    '<Subject1> says, <d>已经修好了。</d>')
        updated = policy.strip_scene_images(original, [2])
        self.assertIn('<Subject 3>是修表店场景。', updated)
        self.assertNotIn('保留参考图中的空间结构', updated)
        self.assertIn('0—8秒：<Subject1> (S1)观察柜台上的手表。', updated)
        self.assertIn('<d>已经修好了。</d>', updated)
        self.assertIn('<Subject1>成人甲是<Picture 1>保留参考图中的面部特征和工作服。', updated)

    def test_chinese_authored_set_details_survive(self):
        original = '<Subject 2>是<Picture 2>修表店场景，窗户在左侧，柜台在右侧。'
        self.assertEqual(policy.strip_scene_images(original, [2]),
                         '<Subject 2>是修表店场景，窗户在左侧，柜台在右侧。')

    def test_two_removed_scene_images_use_one_pass_numbering(self):
        original = ('<Subject 1> is A, the environment from <Picture 1>.\n'
                    '<Subject 2> is 成人甲, the character from <Picture 2>.\n'
                    '<Subject 3> is B, the environment from <Picture 3>.\n'
                    '<Subject 4> is 成人乙, the character from <Picture 4>.')
        updated = policy.strip_scene_images(original, [1, 3, 3])
        self.assertIn('<Subject 2> is 成人甲, the character from <Picture 1>.', updated)
        self.assertIn('<Subject 4> is 成人乙, the character from <Picture 2>.', updated)
        self.assertNotIn('environment from', updated)

    def test_character_or_unproven_custom_picture_is_never_silently_removed(self):
        cases = ('<Subject 1> is 成人甲, the character from <Picture 1>.',
                 '<Subject 1> looks at <Picture 1>.',
                 '<Subject 1>是<Picture 1>修表店场景。\n<Subject 2>成人甲是<Picture 1>人物。',
                 '<Subject 1> is A, the environment from <Picture 1>.\n'
                 '<Subject 2> is B, the environment from <Picture 1>.')
        for original in cases:
            with self.subTest(original=original), self.assertRaises(ValueError):
                policy.strip_scene_images(original, [1])

    def test_definition_cannot_be_taken_from_a_spoken_line(self):
        original = self.six_fields('<Subject 1> is 成人甲, the character from <Picture 1>.',
                                   '<d><Subject 2> is A, the environment from <Picture 2>.</d>')
        with self.assertRaisesRegex(ValueError, '明确场景绑定'):
            policy.strip_scene_images(original, [2])

    def test_picture_tokens_inside_dialogue_are_preserved_by_fallback(self):
        original = self.six_fields('<Subject 1> is 成人甲, the character from <Picture 1>.\n'
                                   '<Subject 2> is A, the environment from <Picture 2>.',
                                   '<d>标签写着<Picture 2>。</d>')
        with self.assertRaisesRegex(ValueError, '对白含图片标记'):
            policy.strip_scene_images(original, [2])

    def test_empty_removal_is_an_exact_no_op(self):
        self.assertEqual(policy.strip_scene_images('自定义原提示词', []), '自定义原提示词')

    def test_director_then_renderer_can_remove_same_trailing_scene_slot(self):
        original = self.six_fields('<Subject 1> is 成人甲, the character from <Picture 1>.\n'
                                   '<Subject 2> is 修表店, the environment from <Picture 2>.')
        written = policy.strip_scene_images(original, [2])
        self.assertEqual(policy.strip_scene_images(written, [2]), written)

    def test_unbound_environment_does_not_authorize_deleting_character_or_custom_slot(self):
        for use in ('<Subject 1> is 成人甲, the character from <Picture 1>.',
                    '<Picture 1> is a custom reference.'):
            with self.assertRaises(ValueError):
                policy.strip_scene_images(use + '\n<Subject 2> is 修表店, the environment.', [1])


if __name__ == '__main__':
    unittest.main()
