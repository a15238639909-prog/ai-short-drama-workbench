"""First-use policy must not rewrite a previously submitted scene-image request.

Only the existing isolated fake-H3 fixture is used. Temporary files are retained;
these tests never delete files, start a local model, or touch a real project.
"""
import copy
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from api import segment_api as api
from cores import scene_reference_policy as policy
from cores import video_continuity as continuity
from tests import test_workbench_scene_entry_render as entry_fixtures


class WorkbenchFirstUseRecoveryTests(unittest.TestCase):
    def setUp(self):
        # Compose the fixture instead of inheriting its historical test cases.
        self.fixture = entry_fixtures.WorkbenchSceneEntryRenderTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.case = self.fixture.case

    def freeze_old_scene_request(self):
        row = self.fixture.row(2)
        previous = continuity.previous_segment(self.case.story, self.case.timeline, 2, 1, 2)
        self.assertIsNotNone(previous)
        self.assertFalse(policy.include_scene('TEST', row, ep=2, timeline=self.case.timeline))
        # This is the original policy's signature: scene included, with no tail
        # transport slot. Its metadata is authoritative even at current VERSION.
        parameters = dict(size_tier='0.7', ratio='16:9', steps=25,
                          scene_reference_policy=policy.VERSION)
        original_refs = [self.fixture.paths['Alice'], self.fixture.paths['Hall']]
        signature = continuity.segment_signature(row, parameters, previous, original_refs)
        request = dict(key='submitted-with-original-scene-image', status='pending',
            input_signature=signature, requested_seed=None,
            final_prompt=row['prompt'], final_refs=original_refs,
            resolved_params=dict(mode='ref2va', seconds=8, ratio='1:1',
                                 size_tier='0.5', steps=31, seed=None),
            scene_reference_policy=policy.VERSION, scene_image_included=True,
            reference_scope_version='original-scene-image-request',
            snapshot_origin='before_submission', handoff_version='none')
        row['render_request'] = copy.deepcopy(request)
        self.case.receipts[request['key']] = dict(status='submitted', prompt_id='known-scene-job')
        return request

    def test_pending_current_version_with_scene_image_restores_exact_original_submission(self):
        request = self.freeze_old_scene_request()
        with patch.object(policy, 'strip_scene_images',
                          side_effect=AssertionError('pending prompt must remain frozen')), \
             patch.object(continuity, 'scope_prompt',
                          side_effect=AssertionError('pending prompt must not be rebuilt')):
            result = self.fixture.render(2)
        self.case.generate.assert_called_once_with('ref2va', request['final_prompt'],
            images=request['final_refs'], recovery_key=request['key'],
            **{key: value for key, value in request['resolved_params'].items() if key != 'mode'})
        sent = self.case.generate.call_args
        self.assertNotIn(str(self.case.old_frame), sent.kwargs['images'])
        self.assertEqual(result['render_input_signature'], request['input_signature'])
        saved = self.fixture.row(2)['render_request']
        for key in ('key', 'final_prompt', 'final_refs', 'resolved_params',
                    'scene_reference_policy', 'scene_image_included', 'handoff_version'):
            self.assertEqual(saved[key], request[key])
        self.fixture.prepare.assert_not_called()
        self.fixture.handoff.assert_not_called()

    def assert_changed_source_blocks_submission(self, request):
        with self.assertRaises(ValueError):
            self.fixture.render(2)
        self.case.generate.assert_not_called()
        saved = self.fixture.row(2)['render_request']
        for key in ('key', 'input_signature', 'final_prompt', 'final_refs', 'resolved_params'):
            self.assertEqual(saved[key], request[key])
        self.assertEqual(self.case.receipts[request['key']]['prompt_id'], 'known-scene-job')

    def test_changed_source_prompt_does_not_rewrite_or_resubmit_pending_request(self):
        request = self.freeze_old_scene_request()
        self.fixture.row(2)['prompt'] += '\nThe worker opens a different cabinet.'
        self.assert_changed_source_blocks_submission(request)

    def test_changed_reference_selection_does_not_rewrite_or_resubmit_pending_request(self):
        request = self.freeze_old_scene_request()
        self.fixture.row(2)['refs'] = [{'n': 'Hall'}, {'n': 'Alice'}]
        self.assert_changed_source_blocks_submission(request)

    def test_replaced_character_image_does_not_rewrite_or_resubmit_pending_request(self):
        request = self.freeze_old_scene_request()
        Path(self.fixture.paths['Alice']).write_bytes(b'changed character image content and size')
        self.assert_changed_source_blocks_submission(request)


if __name__ == '__main__':
    unittest.main()
