"""An unchanged shop at a different camera position must retain its adopted set."""
import copy
import unittest
from unittest.mock import patch

from cores import asset_core, reference_ready as refs, saga_core


class EpisodeSceneReferenceTests(unittest.TestCase):
    def setUp(self):
        self.cards = [{'name': '店里', 'scene_id': 'OLD_SHOP',
                       'space': '室内修表店，中央有木工作台。'}]
        self.episode = {'no': 2, 'brief': '同一修表店、同一张工作台，两人还没有离开。'}
        self.slices = [{'scene_hint': '店内工作台前', 'text': '原文不能被重写',
                        'director': {'flow': 'source-v1'}}]

    def test_camera_position_resolves_old_set_without_mutating_source_or_card(self):
        before = copy.deepcopy(self.cards)
        with patch.object(saga_core, 'get_saga', return_value={'episodes': [self.episode]}):
            mapped = refs.canonical_scene_slices('TEST', 2, self.slices, self.cards)
        self.assertEqual(mapped[0]['scene_hint'], '店里')
        self.assertEqual(mapped[0]['text'], self.slices[0]['text'])
        self.assertEqual(self.slices[0]['scene_hint'], '店内工作台前')
        self.assertEqual(self.cards, before)

    def test_image_readiness_uses_the_old_adopted_image(self):
        with patch.object(saga_core, 'get_saga', return_value={'episodes': [self.episode]}), \
             patch.object(saga_core, 'ep_timeline', return_value={}), \
             patch.object(refs, '_current_slices', return_value=self.slices), \
             patch.object(asset_core, 'list_assets', side_effect=lambda sid, kind: self.cards if kind == 'scenes' else []), \
             patch.object(refs, 'adopted_paths', side_effect=lambda sid, kind: {'OLD_SHOP': 'adopted.png'} if kind == 'scene' else {}):
            self.assertEqual(refs.scene_ids_for_episode('TEST', 2), {'OLD_SHOP'})
            refs.assert_references('TEST', [{'scene': '店内工作台前', 'chars': [], 'shot': '近景'}], ep=2)

    def test_an_adjacent_courtyard_is_not_the_old_shop(self):
        with patch.object(saga_core, 'get_saga', return_value={'episodes': [self.episode]}):
            self.assertIsNone(refs.resolve_scene_card('TEST', 2, '石板小院', self.cards))

    def test_bare_workbench_resolves_old_image_through_the_reference_chain(self):
        slices = [dict(self.slices[0], scene_hint='工作台')]
        with patch.object(saga_core, 'get_saga', return_value={'episodes': [self.episode]}), \
             patch.object(saga_core, 'ep_timeline', return_value={}), \
             patch.object(refs, '_current_slices', return_value=slices), \
             patch.object(asset_core, 'list_assets', side_effect=lambda sid, kind: self.cards if kind == 'scenes' else []), \
             patch.object(refs, 'adopted_paths', side_effect=lambda sid, kind: {'OLD_SHOP': 'adopted.png'} if kind == 'scene' else {}):
            self.assertEqual(refs.canonical_scene_slices('TEST', 2, slices, self.cards)[0]['scene_hint'], '店里')
            self.assertEqual(refs.scene_ids_for_episode('TEST', 2), {'OLD_SHOP'})
            refs.assert_references('TEST', [{'scene': '工作台', 'chars': [], 'shot': '近景'}], ep=2)
        self.assertEqual(slices[0]['scene_hint'], '工作台')

    def test_explicit_arrival_keeps_transition_and_following_location_without_source_rewrite(self):
        cards = self.cards + [{'name': '石板小院', 'scene_id': 'NEW_YARD', 'space': '店门外石板小院'}]
        slices = [
            {'scene_hint': '店里', 'text': '── 店里 ──\n阿宁推开店门，两人跨过门槛，踏上门外青灰色的石板小院。',
             'director': {'flow': 'source-v1', 'start_state': '两人在店门边', 'target_state': '两人在小院'}},
            {'scene_hint': '店里', 'text': '── 店里 ──\n两人站在木长凳旁交出盒子。', 'director': {'flow': 'source-v1'}},
        ]
        before = copy.deepcopy(slices)
        with patch.object(saga_core, 'get_saga', return_value={'episodes': [self.episode]}):
            mapped = refs.canonical_scene_slices('TEST', 2, slices, cards)
        self.assertEqual([r['scene_hint'] for r in mapped], ['石板小院', '石板小院'])
        self.assertEqual(mapped[0]['director']['scene_transition']['from'], '店里')
        self.assertEqual(mapped[0]['director']['start_state'], '两人在店门边')
        self.assertEqual(slices, before)
        self.assertEqual([r['text'] for r in mapped], [r['text'] for r in before])

    def test_explicit_return_or_changed_heading_is_not_overridden(self):
        cards = self.cards + [{'name': '石板小院', 'scene_id': 'NEW_YARD'}]
        slices = [
            {'scene_hint': '店里', 'text': '两人走到石板小院。', 'director': {'flow': 'source-v1'}},
            {'scene_hint': '店里', 'text': '店里，另一人在工作台前等候。', 'director': {'flow': 'source-v1'}},
        ]
        with patch.object(saga_core, 'get_saga', return_value={'episodes': [self.episode]}):
            mapped = refs.canonical_scene_slices('TEST', 2, slices, cards)
        self.assertEqual([r['scene_hint'] for r in mapped], ['石板小院', '店里'])

    def test_image_readiness_requires_arrived_set_and_not_stale_heading(self):
        cards = self.cards + [{'name': '石板小院', 'scene_id': 'NEW_YARD'}]
        slices = [{'scene_hint': '店里', 'text': '两人踏上门外石板小院。', 'director': {'flow': 'source-v1'}}]
        with patch.object(saga_core, 'get_saga', return_value={'episodes': [self.episode]}), \
             patch.object(saga_core, 'ep_timeline', return_value={}), \
             patch.object(refs, '_current_slices', return_value=slices), \
             patch.object(asset_core, 'list_assets', return_value=cards):
            self.assertEqual(refs.scene_ids_for_episode('TEST', 2), {'NEW_YARD'})


if __name__ == '__main__':
    unittest.main()
