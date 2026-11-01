"""Save/reload prompt metadata must describe the accepted text and image pack."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from cores import authoring, asset_core, opening, reference_ready, saga_core, story_core
from api import segment_api


class WorkbenchTextPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.timeline = {'scenes': [{'no': 1, 'segments': [{
            'no': 1, 'prompt': 'old person prompt', 'chars': ['林岚'],
            'scene': '庭院', 'plan': {'flow': 'source-v1'}, 'seconds': 7,
            'video': 'preserved-original.mp4',
        }]}]}
        self.character = dict(name='林岚', character_id='lin')
        self.scene = dict(name='庭院', scene_id='yard')
        self.character_path = str(Path(__file__).resolve())
        self.scene_path = str(Path(authoring.__file__).resolve())
        self.visuals = [dict(owner_id='lin', status='adopted', path=self.character_path),
                        dict(owner_id='yard', status='adopted', path=self.scene_path)]
        self.addCleanup(patch.stopall)
        patch.object(saga_core, 'ep_timeline', side_effect=lambda *a: copy.deepcopy(self.timeline)).start()
        patch.object(saga_core, 'save_ep_timeline', side_effect=self.save).start()
        patch.object(saga_core, 'stamp_source').start()
        patch.object(opening, 'enabled', return_value=False).start()
        patch.object(asset_core, 'list_assets', side_effect=self.assets).start()
        patch.object(story_core, 'get_story', return_value={'settings': {}}).start()
        patch.object(reference_ready, 'assert_references').start()
        patch.object(reference_ready, 'reference_camera_settings', side_effect=lambda sid, settings, cards: settings).start()
        patch.object(reference_ready, 'resolve_scene_card', return_value=self.scene).start()

    def assets(self, sid, kind):
        return copy.deepcopy({'characters': [self.character], 'scenes': [self.scene],
                              'visuals': self.visuals}.get(kind, []))

    def save(self, sid, ep, timeline):
        self.timeline = copy.deepcopy(timeline)
        return True

    def row(self):
        return self.timeline['scenes'][0]['segments'][0]

    def proposal(self):
        return dict(n=1, prompt='new empty yard prompt', chars=[], scene='庭院', seconds=7,
            plan={'flow': 'source-v1', 'source_shot_policy': 'source-shots-v1',
                  'scene_reference_scope': 'current-scene-v1'},
            source_text='晨光落在空庭院。', source_fallback=False,
            director_profile='original-story-zh-v1',
            director_compilation={'dialogue': [], 'voice_binding_audit': [],
                'source_shot_audit': {'policy': 'source-shots-v1',
                    'units': [{'id': 'U001', 'text': '晨光落在空庭院。', 'kind': 'action'}],
                    'shots': [{'shot': 1, 'unit_ids': ['U001'], 'start': 0, 'end': 7}]}})

    def test_saved_empty_cast_removes_previous_person_from_actual_reference_pack(self):
        authoring.build_timeline('MEMORY', 1, [self.proposal()])
        saved = self.row()
        self.assertEqual(saved['chars'], [])
        self.assertEqual(segment_api.new_pack('MEMORY', saved, ep=1), [self.scene_path])
        self.assertEqual(saved['video'], 'preserved-original.mp4')
        self.assertTrue(saved['stale_video'])

    def test_saved_source_audit_survives_reload_and_is_not_shared_with_proposal(self):
        proposal = self.proposal()
        authoring.build_timeline('MEMORY', 1, [proposal])
        saved = self.row()
        for key in ('director_compilation', 'director_profile', 'source_fallback'):
            self.assertEqual(saved.get(key), proposal[key])
        proposal['director_compilation']['source_shot_audit']['shots'][0]['unit_ids'].append('U999')
        self.assertEqual(saved['director_compilation']['source_shot_audit']['shots'][0]['unit_ids'], ['U001'])

    def test_accepting_legacy_text_clears_source_audit_for_replaced_prompt(self):
        self.row().update(director_compilation={'old': 'audit'}, director_profile='old-profile', source_fallback=True)
        authoring.build_timeline('MEMORY', 1, [dict(n=1, prompt='legacy replacement', chars=[])])
        for key in ('director_compilation', 'director_profile', 'source_fallback'):
            self.assertNotIn(key, self.row())

    def test_manual_prompt_keeps_its_image_pack_and_audit_in_both_save_modes(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                previous = dict(no=1, prompt='manual actor prompt', prompt_edited=True,
                    prompt_edited_at=12, chars=['林岚'], scene='庭院', seconds=6,
                    plan={'flow': 'source-v1', 'manual': True},
                    source_text='林岚站在庭院。', director_compilation={'manual_origin': 'original'},
                    director_profile='old-profile', source_fallback=True,
                    video='preserved-original.mp4')
                self.timeline = {'scenes': [{'no': 1, 'segments': [copy.deepcopy(previous)]}]}
                proposal = self.proposal()
                self.assertEqual(authoring.build_timeline('MEMORY', 1, [proposal], replace=replace), [1])
                saved = self.row()
                for key in ('prompt', 'chars', 'scene', 'seconds', 'plan', 'source_text',
                            'director_compilation', 'director_profile', 'source_fallback', 'video'):
                    self.assertEqual(saved[key], previous[key], key)
                self.assertEqual(saved['prompt_auto'], proposal['prompt'])
                self.assertEqual(segment_api.new_pack('MEMORY', saved, ep=1),
                                 [self.character_path, self.scene_path])


if __name__ == '__main__':
    unittest.main()
