"""Main-workbench scene-entry transport, with retained files and fake H3 only.

These cases exercise the real request policy, scene-slot rewriting and recovery
boundary.  They do not start a local model, a media process or the server.
"""
import copy
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from api import segment_api as api, timeline
from cores import asset_core, authoring, opening, reference_bindings, reference_ready, saga_core, story_core
from cores import scene_reference_policy as policy
from cores import video_continuity as continuity
from tests import test_segment_render_recovery as render_cases


_REAL_SCENE_POLICY_ACTIVE = api._scene_policy_active
_REAL_SCOPE_PROMPT = continuity.scope_prompt
_REAL_NEW_PACK = api.new_pack
_REAL_ASSERT_REFERENCES = reference_ready.assert_references


class WorkbenchSceneEntryRenderTests(unittest.TestCase):
    def setUp(self):
        self.case = render_cases.SegmentRenderRecoveryTests(methodName='runTest')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.scene_b = self.case.directory / 'courtyard.png'
        self.scene_b.write_bytes(b'courtyard reference fixture')
        self.paths = {'Alice': str(self.case.refs[0]),
                      'Hall': str(self.case.refs[1]), 'Courtyard': str(self.scene_b)}
        self.characters = [dict(character_id='adult_alice', name='Alice')]
        self.scenes = [dict(scene_id='hall', name='Hall'),
                       dict(scene_id='courtyard', name='Courtyard')]
        self.catalog = [dict(owner_id='adult_alice', name='Alice', role='character',
                             path=self.paths['Alice'])] + [
            dict(owner_id=row['scene_id'], name=row['name'], role='scene', path=self.paths[row['name']])
            for row in self.scenes]
        self.case.timeline = {'scenes': [{'no': 1, 'segments': []}]}
        for number, location in enumerate(('Hall', 'Hall', 'Courtyard', 'Courtyard', 'Hall'), 1):
            row = copy.deepcopy(self.case.segment)
            row.update(no=number, scene=location, prompt=self.prompt(location), stale_video=False)
            self.case.timeline['scenes'][0]['segments'].append(row)

        stack = self.case.stack
        # The reused recovery fixture intentionally pins the legacy contract.
        # Restore the actual functions for these new-policy integration cases.
        stack.enter_context(patch.object(api, '_scene_policy_active', side_effect=_REAL_SCENE_POLICY_ACTIVE))
        stack.enter_context(patch.object(continuity, 'scope_prompt', side_effect=_REAL_SCOPE_PROMPT))
        stack.enter_context(patch.object(api, 'new_pack', side_effect=self.pack))
        stack.enter_context(patch.object(api, '_adopted_paths', return_value=self.paths))
        stack.enter_context(patch.object(reference_bindings, 'adopted_catalog', return_value=self.catalog))
        stack.enter_context(patch.object(asset_core, 'list_assets', side_effect=self.assets))
        stack.enter_context(patch.object(saga_core, 'get_saga', return_value={'episodes': [{'no': 2, 'brief': ''}]}))
        stack.enter_context(patch.object(saga_core, 'seg_video_path', side_effect=lambda sid, ep, scene, segment, **kw:
                                        str(self.case.directory / ('rendered_%s_%s_%s.mp4' % (ep, scene, segment)))))
        self.prepare = stack.enter_context(patch.object(continuity, 'prepare_handoff',
            side_effect=AssertionError('new policy must not append historical image evidence')))
        self.handoff = stack.enter_context(patch.object(continuity, 'handoff_prompt',
            wraps=continuity.handoff_prompt))
        stack.enter_context(patch.object(Path, 'unlink', side_effect=AssertionError('no permanent unlink')))
        stack.enter_context(patch('shutil.rmtree', side_effect=AssertionError('no permanent deletion')))

    def assets(self, sid, kind):
        if kind == 'characters':
            return copy.deepcopy(self.characters)
        if kind == 'scenes':
            return copy.deepcopy(self.scenes)
        return []

    def pack(self, sid, segment, ep=None, include_scene=True, require_scene=True):
        result = [] if policy.is_scene_only(segment) else [self.paths['Alice']]
        if include_scene:
            result.append(self.paths[segment['scene']])
        return result

    @staticmethod
    def prompt(location='Hall', empty=False):
        people = '' if empty else '<Subject 1> is Alice, the character from <Picture 1>. Adult worker in ordinary clothes.\n'
        number = 1 if empty else 2
        return ('subject_definitions:\n' + people +
                '<Subject %d> is %s, the environment from <Picture %d>.\n\n' % (number, location, number) +
                'summary:\n[reference generation] Observe a clock being repaired.\n\n'
                'retention_analysis:\n<Picture %d> establishes the room.\n\n' % number +
                'detailed_description:\n[Shot 1] ' +
                ('Dust drifts through <Subject 1>.' if empty else '<Subject 1> (S1) places the clock on a table in <Subject 2>.') +
                '\n\noverall_soundscape:\nA clock ticks.\n\nnon_diegetic_music:\nN/A')

    def row(self, number):
        return self.case.timeline['scenes'][0]['segments'][number - 1]

    def render(self, number):
        return api.render_segment('TEST', 2, 1, number)

    def assert_bound(self, submitted, images):
        numbers = set(map(int, re.findall(r'<Picture\s+(\d+)>', submitted)))
        self.assertLessEqual(numbers, set(range(1, len(images) + 1)))
        continuity.validate_prompt_references(submitted, images)

    def test_a_a_b_b_a_renders_first_scene_use_and_actual_predecessor_tails(self):
        original = [self.row(number)['prompt'] for number in range(1, 6)]
        expected = [('Hall', True), ('Hall', False), ('Courtyard', True),
                    ('Courtyard', False), ('Hall', False)]
        for number, (location, entrance) in enumerate(expected, 1):
            with self.subTest(segment=number):
                self.render(number)
                call = self.case.generate.call_args
                images = [self.paths['Alice']] + ([self.paths[location]] if entrance else [])
                tail = self.row(number - 1)['end_frame'] if number > 1 else ''
                if tail:
                    images.append(tail)
                self.assertEqual(call.args[0], 'ref2va')
                self.assertEqual(call.kwargs['images'], images)
                self.assert_bound(call.args[1], images)
                request = self.row(number)['render_request']
                self.assertEqual(request['scene_reference_policy'], policy.VERSION)
                self.assertEqual(request['scene_image_included'], entrance)
                self.assertEqual(request['handoff_version'], 'h3-handoff-v2' if tail else 'none')
                self.assertEqual(request['tail_reference'], tail)
                self.assertEqual(request['continuity_policy'], 'scene-first-use-tail-v2')
                self.assertEqual(self.row(number)['prompt'], original[number - 1])
                if not entrance:
                    self.assertIn('the environment.', call.args[1])
                    self.assertNotIn('environment from <Picture', call.args[1])
                    self.assertIn('the environment.', call.args[1])
                    self.assertIn('places the clock on a table in <Subject 2>', call.args[1])
                    self.assertNotIn('Reference scope (', call.args[1])
        self.prepare.assert_not_called()
        self.assertEqual(self.handoff.call_count, 4)
        self.assertEqual(self.case.generate.call_count, 5)

    def test_entry_is_determined_by_plan_even_when_rendered_out_of_order(self):
        for number in (4, 2, 3, 1, 5):
            self.render(number)
            scene_image = self.paths[self.row(number)['scene']]
            self.assertEqual(scene_image in self.case.generate.call_args.kwargs['images'], number in (1, 3))

    def test_old_source_owned_marker_cannot_restore_scene_image_on_continuation(self):
        row = self.row(2)
        row['plan'] = dict(flow='source-v1', source_shot_policy='source-shots-v1', scene_reference_scope='current-scene-v1')
        self.render(2)
        call = self.case.generate.call_args
        self.assertEqual(call.kwargs['images'], [self.paths['Alice'], self.row(1)['end_frame']])
        self.assertNotIn(self.paths['Courtyard'], call.kwargs['images'])
        self.assertNotIn('environment from <Picture', call.args[1])
        self.assertIn('视频末帧', call.args[1])

    def test_unused_missing_set_does_not_block_continuation_but_entry_still_requires_it(self):
        visuals = [dict(status='adopted', owner_id='adult_alice', kind='character', path=self.paths['Alice'])]
        def actual_assets(sid, kind):
            return visuals if kind == 'visuals' else self.assets(sid, kind)
        with patch.object(asset_core, 'list_assets', side_effect=actual_assets), \
                patch.object(story_core, 'get_story', return_value=self.case.story), \
                patch.object(api, 'new_pack', side_effect=_REAL_NEW_PACK), \
                patch.object(reference_ready, 'assert_references', side_effect=_REAL_ASSERT_REFERENCES):
            self.render(2)
            self.assertEqual(self.case.generate.call_args.kwargs['images'],
                             [self.paths['Alice'], self.row(1)['end_frame']])
            with self.assertRaisesRegex(reference_ready.MissingReferencesError, 'Hall'):
                self.render(1)
        self.assertEqual(self.case.generate.call_count, 1)

    def test_batch_preflight_only_prepares_scene_images_for_entrances(self):
        visuals = [dict(status='adopted', owner_id='adult_alice', kind='character', path=self.paths['Alice'])]
        def actual_assets(sid, kind):
            return visuals if kind == 'visuals' else self.assets(sid, kind)
        with patch.object(asset_core, 'list_assets', side_effect=actual_assets), \
                patch.object(saga_core, 'ep_timeline', side_effect=lambda *_: copy.deepcopy(self.case.timeline)), \
                patch.object(story_core, 'get_story', return_value=self.case.story), \
                patch.object(opening, 'ensure', return_value=[]), \
                patch.object(authoring, 'cast_cards', return_value=self.characters), \
                patch.object(authoring, '_gen_char_images', side_effect=AssertionError('no model generation')), \
                patch('api.saga_api._ep_scene_images') as scenes:
            check = reference_ready.assert_references
            timeline._ensure_refs('TEST', 2, [2], dict(done=0, total=0), lambda *_: None, RuntimeError)
            scenes.assert_not_called()
            self.assertFalse(check.call_args.kwargs['include_scene'])
            timeline._ensure_refs('TEST', 2, [3], dict(done=0, total=0), lambda *_: None, RuntimeError)
            scenes.assert_called_once()
            self.assertTrue(check.call_args.kwargs['include_scene'])

    def test_continuation_reversed_user_selection_keeps_character_identity(self):
        original = self.prompt('Hall')
        self.row(2).update(refs=[dict(n='Hall'), dict(n='Alice')], prompt=original)
        self.render(2)
        call = self.case.generate.call_args
        self.assertEqual(call.kwargs['images'], [self.paths['Alice'], self.row(1)['end_frame']])
        self.assertIn('<Subject 1> is Alice, the character from <Picture 1>.', call.args[1])
        self.assertIn('<Subject 2> is Hall, the environment.', call.args[1])
        self.assertNotIn('environment from <Picture 2>', call.args[1])
        self.assertIn('<Picture 2>是紧接本段', call.args[1])
        self.assertEqual(self.row(2)['prompt'], original)
        self.assert_bound(call.args[1], call.kwargs['images'])

    def test_entrance_reversed_selection_rebinds_actual_scene_and_character_slots(self):
        original = self.prompt('Courtyard')
        self.row(3).update(refs=[dict(n='Courtyard'), dict(n='Alice')], prompt=original)
        self.render(3)
        call = self.case.generate.call_args
        self.assertEqual(call.kwargs['images'],
                         [self.paths['Courtyard'], self.paths['Alice'], self.row(2)['end_frame']])
        self.assertIn('<Subject 1> is Alice, the character from <Picture 2>.', call.args[1])
        self.assertIn('<Subject 2> is Courtyard, the environment from <Picture 1>.', call.args[1])
        self.assert_bound(call.args[1], call.kwargs['images'])
        self.assertEqual(self.row(3)['prompt'], original)

    def test_scene_only_continuation_uses_actual_tail_without_repeating_set_image(self):
        self.row(2).update(chars=[], no_chars=True, prompt=self.prompt('Hall', empty=True))
        self.render(2)
        call = self.case.generate.call_args
        self.assertEqual(call.args[0], 'ref2va')
        self.assertEqual(call.kwargs['images'], [self.row(1)['end_frame']])
        self.assertIn('<Subject 1> is Hall, the environment.', call.args[1])
        self.assertNotIn('environment from <Picture', call.args[1])
        self.assertIn('<Picture 1>是紧接本段', call.args[1])
        self.assert_bound(call.args[1], call.kwargs['images'])

    def test_continuation_without_predecessor_video_stops_before_submission(self):
        self.row(1).update(video=str(self.case.directory / 'not_generated.mp4'), end_frame='')
        with self.assertRaisesRegex(ValueError, '紧邻上一段'):
            self.render(2)
        self.case.generate.assert_not_called()
        self.assertNotIn('render_request', self.row(2))

    def test_missing_actual_tail_is_extracted_from_immediate_predecessor(self):
        self.row(1)['end_frame'] = str(self.case.directory / 'missing_tail.png')
        self.render(2)
        tail = self.row(1)['end_frame']
        self.assertTrue(Path(tail).is_file())
        self.assertEqual(self.case.generate.call_args.kwargs['images'], [self.paths['Alice'], tail])
        self.assertEqual(self.row(2)['render_request']['tail_reference'], tail)
        self.assertTrue(any('-sseof' in call.args[0] for call in self.case.process.call_args_list))

    def test_full_reference_budget_cannot_silently_drop_required_tail(self):
        with patch.object(api, '_render_references', return_value=[self.paths['Alice']] * 9):
            with self.assertRaisesRegex(ValueError, '实际尾帧保留一个'):
                self.render(2)
        self.case.generate.assert_not_called()
        self.assertNotIn('render_request', self.row(2))

    def pending(self, number, *, new_policy=False):
        row = self.row(number)
        request = dict(key='frozen-scene-test-%s' % number, status='pending', requested_seed=None)
        if new_policy:
            request['scene_reference_policy'] = policy.VERSION
            request['scene_image_included'] = False
        row['render_request'] = request
        request['input_signature'] = api._render_context('TEST', 2, 1, number)[-1]
        images = [self.paths['Alice']]
        prompt = policy.strip_scene_images(row['prompt'], {2})
        if not new_policy:
            images += [self.paths[row['scene']], str(self.case.old_frame)]
            prompt = row['prompt'] + '\n<Picture 3> preserves the original submitted tail.'
        request.update(final_prompt=prompt, final_refs=images,
                       resolved_params=dict(mode='ref2va', seconds=8, ratio='1:1', size_tier='0.5', steps=31, seed=None),
                       reference_scope_version='frozen-original-policy', snapshot_origin='before_submission')
        self.case.receipts[request['key']] = dict(status='submitted', prompt_id='existing-external-task')
        return copy.deepcopy(request)

    def test_legacy_pending_snapshot_keeps_exact_prompt_all_images_and_key(self):
        request = self.pending(2)
        self.render(2)
        call = self.case.generate.call_args
        self.assertEqual(call.args, ('ref2va', request['final_prompt']))
        self.assertEqual(call.kwargs['images'], request['final_refs'])
        self.assertEqual(call.kwargs['recovery_key'], request['key'])
        self.assertEqual(call.kwargs['ratio'], '1:1')
        self.assertEqual(call.kwargs['steps'], 31)
        self.assertNotIn('scene_reference_policy', self.row(2)['render_request'])

    def test_new_policy_pending_snapshot_keeps_character_only_pack_and_key(self):
        request = self.pending(2, new_policy=True)
        self.render(2)
        call = self.case.generate.call_args
        self.assertEqual(call.args[1], request['final_prompt'])
        self.assertEqual(call.kwargs['images'], [self.paths['Alice']])
        self.assertEqual(call.kwargs['recovery_key'], request['key'])
        self.assertEqual(self.row(2)['render_request']['scene_reference_policy'], policy.VERSION)

    def test_unrecognized_custom_binding_stops_instead_of_restoring_scene_image(self):
        original = ('<Subject 1> is Alice, the character from <Picture 1>.\n'
                    '<Subject 2> follows the colors and framing in <Picture 2>.\n'
                    'Alice examines a repaired clock.')
        self.row(2).update(prompt=original, plan={})
        with self.assertRaisesRegex(ValueError, '场景图片绑定无法确认'):
            self.render(2)
        self.case.generate.assert_not_called()
        self.assertEqual(self.row(2)['prompt'], original)
        self.assertNotIn('render_request', self.row(2))

    def test_literal_picture_marker_in_dialogue_is_preserved_and_stops_unsafe_submission(self):
        original = self.prompt().replace('[Shot 1]', '[Shot 1] <Subject 1> (S1) says <d><Picture 2></d>.')
        self.row(2)['prompt'] = original
        with self.assertRaisesRegex(ValueError, '场景图片绑定无法确认'):
            self.render(2)
        self.case.generate.assert_not_called()
        self.assertEqual(self.row(2)['prompt'], original)
        self.assertNotIn('render_request', self.row(2))

    def test_legacy_completed_clip_remains_current_without_regeneration(self):
        row = self.row(2)
        row.update(render_request=dict(key='old-completed-task', status='completed'),
                   render_input_signature='compute-using-legacy-inputs')
        row['render_input_signature'] = api._render_context('TEST', 2, 1, 2)[-1]
        row['render_media'] = dict(continuity.media_stamp(row['video']), check='decoded')
        self.assertFalse(_REAL_SCENE_POLICY_ACTIVE(row))
        self.assertTrue(api.is_segment_current('TEST', 2, 1, 2))
        self.case.generate.assert_not_called()
        self.assertEqual(self.row(2)['render_request']['key'], 'old-completed-task')

    def test_fresh_rerender_uses_new_policy_after_legacy_completed_request(self):
        row = self.row(2)
        row.update(render_request=dict(key='old-completed-task', status='completed'),
                   render_input_signature='old-signature')
        self.assertTrue(_REAL_SCENE_POLICY_ACTIVE(row, fresh=True))
        self.render(2)
        self.assertEqual(self.case.generate.call_args.kwargs['images'],
                         [self.paths['Alice'], self.row(1)['end_frame']])
        self.assertEqual(self.row(2)['render_request']['scene_reference_policy'], policy.VERSION)


if __name__ == '__main__':
    unittest.main()
