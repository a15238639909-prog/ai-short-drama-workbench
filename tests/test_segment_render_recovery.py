"""Segment rendering recovery boundaries, with fake models and retained fixtures.

No model/server is started. Media inspection is exercised with deterministic
ffprobe/ffmpeg responses; all file copies and metadata use retained temp files.
"""
import copy
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

os.environ['V41_NO_MODEL'] = '1'

from api import segment_api as api
from cores import authoring, asset_core, reference_ready, saga_core, story_core, store
from cores import video_continuity as vc
from models import h3_client as h3


class SegmentRenderRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix='segment_render_recovery_'))
        self.refs = [self.directory / 'character.png', self.directory / 'scene.png']
        for path in self.refs:
            path.write_bytes(b'reference fixture')
        self.generated = self.directory / 'generated.mp4'
        self.generated.write_bytes(b'new generated media fixture')
        self.old = self.directory / 'episode_2_segment_1.mp4'
        self.old.write_bytes(b'old accepted media must remain intact')
        self.old_frame = self.directory / 'old_end.png'
        self.old_frame.write_bytes(b'old frame fixture')
        self.segment = {'no': 1, 'prompt': '<Subject 1> is Alice from <Picture 1>.',
                        'source_text': 'Alice turns toward the door.', 'seconds': 8,
                        'chars': ['Alice'], 'scene': 'Hall', 'plan': {'flow': 'source-v1'},
                        'video': str(self.old), 'end_frame': str(self.old_frame), 'seed': 11,
                        'stale_video': True}
        self.story = {'story_id': 'TEST', 'settings': {}, 'saga': {'episodes': []}}
        self.timeline = {'scenes': [{'no': 1, 'segments': [copy.deepcopy(self.segment)]}]}
        self.receipts = {}
        self.duration = 8
        self.audio = True
        self.decode_error = False
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)  # Close mocks only; fixture files are retained.
        self.stack.enter_context(patch.object(store, 'DATA', self.directory / 'data'))
        self.stack.enter_context(patch.object(api, '_load', side_effect=lambda *a: (copy.deepcopy(self.story), copy.deepcopy(self.timeline))))
        self.stack.enter_context(patch.object(api, '_save', side_effect=self.save))
        self.stack.enter_context(patch.object(api, '_render_params', return_value=('0.7', '16:9', 25)))
        self.stack.enter_context(patch.object(api, '_render_critical_problems', return_value=[]))
        # These recovery fixtures retain the legacy relay contract. The scene
        # entry contract is exercised separately with its real policy enabled.
        self.stack.enter_context(patch.object(api, '_scene_policy_active', return_value=False))
        # These fixtures test renderer boundaries, not six-field prompt syntax.
        self.scope = self.stack.enter_context(patch.object(vc, 'scope_prompt', side_effect=lambda prompt, *_: prompt))
        self.stack.enter_context(patch.object(api, 'new_pack', side_effect=lambda *a, **kw: [str(p) for p in self.refs]))
        self.stack.enter_context(patch.object(reference_ready, 'assert_references'))
        self.stack.enter_context(patch.object(saga_core, 'seg_video_path', return_value=str(self.old)))
        self.stack.enter_context(patch.object(h3, 'recovery_receipt', side_effect=lambda key: copy.deepcopy(self.receipts.get(key, {}))))
        self.stack.enter_context(patch.object(h3, '_save_receipt', side_effect=lambda key, receipt: self.receipts.update({key: copy.deepcopy(receipt)})))
        self.generate = self.stack.enter_context(patch.object(h3, 'generate', return_value=self.result()))
        self.process = self.stack.enter_context(patch.object(subprocess, 'run', side_effect=self.process_result))

    def result(self):
        return {'output_path': str(self.generated), 'seed': 42, 'width': 640, 'height': 360,
                'steps': 25, 'size_tier': '0.7', 'ratio': '16:9'}

    def save(self, _sid, timeline, _ep=None):
        self.timeline = copy.deepcopy(timeline)

    def current(self):
        return self.timeline['scenes'][0]['segments'][0]

    def process_result(self, command, **kwargs):
        if command[0] == 'ffprobe':
            streams = [{'codec_type': 'video', 'width': 640, 'height': 360, 'duration': str(self.duration)}]
            if self.audio:
                streams.append({'codec_type': 'audio'})
            data = {'streams': streams}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(data), stderr='')
        if '-f' in command and 'null' in command:
            return subprocess.CompletedProcess(command, int(self.decode_error), stdout=b'', stderr=b'')
        if '-frames:v' in command:
            Path(command[-1]).write_bytes(b'new end frame fixture')
            return subprocess.CompletedProcess(command, 0, stdout=b'', stderr=b'')
        raise AssertionError('Unexpected external command: ' + repr(command))

    def render(self):
        return api.render_segment('TEST', 2, 1, 1)

    def test_pending_request_is_saved_before_submission_and_reused_after_crash(self):
        def crash(*args, **kwargs):
            self.assertEqual(self.current()['render_request']['key'], kwargs['recovery_key'])
            raise RuntimeError('connection lost after submission')
        self.generate.side_effect = crash
        with self.assertRaisesRegex(RuntimeError, 'connection lost'):
            self.render()
        key = self.current()['render_request']['key']
        self.assertEqual(self.current()['render_request']['status'], 'pending')
        self.receipts[key] = {'status': 'submitted', 'prompt_id': 'known-external-job'}
        self.generate.side_effect = None
        self.generate.return_value = self.result()
        rendered = self.render()
        self.assertEqual([call.kwargs['recovery_key'] for call in self.generate.call_args_list], [key, key])
        self.assertEqual(rendered['render_request']['status'], 'completed')
        self.assertEqual(self.current()['render_request']['key'], key)

    def test_completed_external_receipt_keeps_pending_request_key(self):
        signature = api._render_context('TEST', 2, 1, 1)[-1]
        self.current()['render_request'] = {'key': 'already-finished', 'status': 'pending',
                                            'input_signature': signature, 'requested_seed': None}
        self.receipts['already-finished'] = {'status': 'completed', 'result': self.result()}
        rendered = self.render()
        self.assertEqual(self.generate.call_args.kwargs['recovery_key'], 'already-finished')
        self.assertEqual(Path(rendered['video']).read_bytes(), self.generated.read_bytes())

    def test_new_request_freezes_final_scoped_prompt_before_submission(self):
        scoped = self.segment['prompt'] + '\nReference scope (' + vc.REFERENCE_SCOPE_VERSION + '): fixture state rules.'
        self.scope.side_effect = lambda *_: scoped

        def inspect_submission(mode, prompt, **kwargs):
            request = self.current()['render_request']
            self.assertEqual(request['final_prompt'], scoped)
            self.assertEqual(request['final_refs'], kwargs['images'])
            self.assertEqual(request['resolved_params'], dict(mode=mode, **{
                k: kwargs[k] for k in ('seconds', 'ratio', 'size_tier', 'steps', 'seed')}))
            self.assertEqual(request['reference_scope_version'], vc.REFERENCE_SCOPE_VERSION)
            self.assertEqual(request['snapshot_origin'], 'before_submission')
            self.assertEqual(prompt, request['final_prompt'])
            return self.result()

        self.generate.side_effect = inspect_submission
        rendered = self.render()
        self.assertEqual(rendered['h3_prompt_sent'], scoped)
        self.assertEqual(rendered['h3_prompt_sha256'], hashlib.sha256(scoped.encode('utf-8')).hexdigest())
        self.assertEqual(rendered['h3_prompt_snapshot_origin'], 'before_submission')
        self.assertEqual(rendered['reference_scope_version'], vc.REFERENCE_SCOPE_VERSION)

    def test_pending_snapshot_keeps_prompt_references_and_parameters(self):
        signature = api._render_context('TEST', 2, 1, 1)[-1]
        frozen_prompt = '<Subject 1> retained original <Picture 1> request.'
        frozen_refs = [str(path) for path in reversed(self.refs)]
        resolved = {'mode': 'ref2va', 'seconds': 8, 'ratio': '1:1',
                    'size_tier': '0.5', 'steps': 31, 'seed': None}
        self.current()['render_request'] = {
            'key': 'original-submission', 'status': 'pending', 'input_signature': signature,
            'requested_seed': None, 'final_prompt': frozen_prompt, 'final_refs': frozen_refs,
            'resolved_params': resolved, 'reference_scope_version': 'original-policy',
            'snapshot_origin': 'before_submission'}
        self.receipts['original-submission'] = {'status': 'submitted', 'prompt_id': 'original-pid'}
        self.scope.side_effect = AssertionError('must not rebuild a pending prompt')
        rendered = self.render()
        self.generate.assert_called_once_with('ref2va', frozen_prompt, images=frozen_refs,
                                             **{k: v for k, v in resolved.items() if k != 'mode'},
                                             recovery_key='original-submission')
        self.assertEqual(rendered['h3_prompt_sent'], frozen_prompt)
        self.assertEqual(rendered['reference_scope_version'], 'original-policy')
        self.assertEqual(rendered['render_input_signature'], signature)
        self.scope.assert_not_called()

    def test_legacy_pending_is_reconstructed_without_claiming_new_policy(self):
        signature = api._render_context('TEST', 2, 1, 1)[-1]
        self.current()['render_request'] = {'key': 'legacy-submission', 'status': 'pending',
                                           'input_signature': signature, 'requested_seed': None}
        self.receipts['legacy-submission'] = {'status': 'submitted', 'prompt_id': 'legacy-pid'}
        self.scope.side_effect = AssertionError('legacy pending must keep legacy prompt assembly')
        rendered = self.render()
        self.assertEqual(self.generate.call_args.args[1], self.segment['prompt'])
        self.assertEqual(self.generate.call_args.kwargs['recovery_key'], 'legacy-submission')
        self.assertEqual(rendered['reference_scope_version'], 'legacy')
        self.assertEqual(rendered['h3_prompt_snapshot_origin'], 'legacy_reconstructed')
        self.assertEqual(rendered['render_request']['snapshot_origin'], 'legacy_reconstructed')
        self.scope.assert_not_called()

    def test_new_scope_policy_metadata_does_not_depend_on_visible_marker(self):
        rendered = self.render()
        self.assertEqual(rendered['reference_scope_version'], vc.REFERENCE_SCOPE_VERSION)

    def previous_fixture(self):
        video = self.directory / 'preceding_segment.mp4'
        video.write_bytes(b'preceding accepted media')
        appearance = self.directory / 'preceding_appearance.png'
        appearance.write_bytes(b'earlier appearance evidence')
        previous = {'episode': 1, 'scene_no': 1, 'segment_no': 3,
                    'segment': {'video': str(video), 'end_frame': str(self.old_frame), 'scene': 'Hall'}}
        self.current()['director_contract'] = {
            'scene_transition': {'from': 'Hall', 'to': 'Courtyard', 'source_evidence': 'They step outside.'}}
        return previous, str(appearance)

    def test_new_handoff_binds_tail_and_appearance_slots_before_submission(self):
        previous, appearance = self.previous_fixture()
        expected_refs = [str(path) for path in self.refs] + [str(self.old_frame), appearance]

        def add_handoff(prompt, frame_number, _segment, _previous, _director, appearance_number=None):
            self.assertEqual(frame_number, 3)
            self.assertEqual(appearance_number, 4)
            return prompt + '\n<Picture 3> final state; <Picture 4> historical appearance.'

        def inspect_submission(*args, **kwargs):
            request = self.current()['render_request']
            self.assertEqual(kwargs['images'], expected_refs)
            self.assertEqual(request['final_refs'], expected_refs)
            self.assertEqual(request['appearance_frame'], appearance)
            self.assertEqual(request['handoff_version'], 'h3-handoff-v3')
            self.assertEqual(request['final_prompt'], args[1])
            return self.result()

        self.generate.side_effect = inspect_submission
        with patch.object(vc, 'previous_segment', return_value=previous), \
             patch.object(vc, 'prepare_handoff', return_value=appearance, create=True) as prepare, \
             patch.object(vc, 'handoff_prompt', side_effect=add_handoff):
            rendered = self.render()
            self.assertTrue(api.is_segment_current('TEST', 2, 1, 1))
        prepare.assert_called_once_with(previous)
        self.assertEqual(rendered['render_request']['handoff_version'], 'h3-handoff-v3')

    def test_handoff_appearance_over_reference_limit_keeps_tail_and_continues(self):
        previous, appearance = self.previous_fixture()
        # Eight declared slots plus tail and appearance would exceed H3's limit.
        self.refs = [self.refs[0]] * 8
        with patch.object(vc, 'previous_segment', return_value=previous), \
             patch.object(vc, 'prepare_handoff', return_value=appearance, create=True) as prepare, \
             patch.object(vc, 'handoff_prompt', side_effect=lambda prompt, *a, **kw: prompt):
            rendered = self.render()
        prepare.assert_not_called()
        self.generate.assert_called_once()
        self.assertEqual(len(rendered['render_request']['final_refs']), 9)
        self.assertEqual(rendered['render_request']['handoff_version'], 'h3-handoff-v2')

    def test_full_identity_reference_slots_continue_without_visual_relay(self):
        previous, _ = self.previous_fixture()
        self.refs = [self.refs[0]] * 9
        with patch.object(vc, 'previous_segment', return_value=previous), \
             patch.object(vc, 'prepare_handoff', side_effect=AssertionError('no free slot')) as prepare, \
             patch.object(vc, 'handoff_prompt', side_effect=AssertionError('no invented picture')):
            rendered = self.render()
        prepare.assert_not_called()
        self.generate.assert_called_once()
        self.assertEqual(rendered['render_request']['final_refs'], [str(path) for path in self.refs])

    def test_unavailable_optional_appearance_keeps_actual_tail_and_continues(self):
        previous, _ = self.previous_fixture()
        with patch.object(vc, 'previous_segment', return_value=previous), \
             patch.object(vc, 'prepare_handoff', side_effect=RuntimeError('optional frame unavailable')) as prepare, \
             patch.object(vc, 'handoff_prompt', side_effect=lambda prompt, *a, **kw: prompt):
            rendered = self.render()
        prepare.assert_called_once()
        self.generate.assert_called_once()
        self.assertEqual(len(rendered['render_request']['final_refs']), 3)
        self.assertEqual(rendered['render_request']['handoff_version'], 'h3-handoff-v2')

    def test_pending_handoff_snapshot_does_not_add_new_appearance(self):
        previous, _appearance = self.previous_fixture()
        old_refs = [str(path) for path in self.refs] + [str(self.old_frame)]
        frozen_prompt = self.segment['prompt'] + '\n<Picture 3> final state.'
        with patch.object(vc, 'previous_segment', return_value=previous), \
             patch.object(vc, 'prepare_handoff', side_effect=AssertionError('must keep pending references'), create=True) as prepare, \
             patch.object(vc, 'handoff_prompt', side_effect=AssertionError('must keep pending prompt')) as handoff:
            signature = api._render_context('TEST', 2, 1, 1)[-1]
            self.current()['render_request'] = {
                'key': 'pending-tail-only', 'status': 'pending', 'input_signature': signature,
                'requested_seed': None, 'final_prompt': frozen_prompt, 'final_refs': old_refs,
                'resolved_params': {'mode': 'ref2va', 'seconds': 8, 'ratio': '16:9',
                                    'size_tier': '0.7', 'steps': 25, 'seed': None},
                'reference_scope_version': 'legacy', 'snapshot_origin': 'before_submission',
                'handoff_version': 'h3-handoff-v2'}
            self.receipts['pending-tail-only'] = {'status': 'submitted', 'prompt_id': 'old-pid'}
            rendered = self.render()
        self.assertEqual(self.generate.call_args.kwargs['images'], old_refs)
        self.assertEqual(self.generate.call_args.args[1], frozen_prompt)
        self.assertEqual(rendered['render_request']['handoff_version'], 'h3-handoff-v2')
        prepare.assert_not_called()
        handoff.assert_not_called()

    def test_legacy_pending_handoff_retains_tail_only(self):
        previous, _appearance = self.previous_fixture()
        with patch.object(vc, 'previous_segment', return_value=previous), \
             patch.object(vc, 'prepare_handoff', side_effect=AssertionError('legacy task cannot add references'), create=True) as prepare, \
             patch.object(vc, 'handoff_prompt', side_effect=lambda prompt, *args, **kw: prompt) as handoff:
            signature = api._render_context('TEST', 2, 1, 1)[-1]
            self.current()['render_request'] = {'key': 'legacy-tail-only', 'status': 'pending',
                                               'input_signature': signature, 'requested_seed': None}
            self.receipts['legacy-tail-only'] = {'status': 'submitted', 'prompt_id': 'old-pid'}
            rendered = self.render()
        self.assertEqual(self.generate.call_args.kwargs['images'], [str(path) for path in self.refs] + [str(self.old_frame)])
        self.assertEqual(handoff.call_args.args[1], 3)
        self.assertIsNone(handoff.call_args.kwargs['appearance_number'])
        self.assertEqual(rendered['render_request']['handoff_version'], 'h3-handoff-v2')
        self.assertEqual(rendered['h3_prompt_snapshot_origin'], 'legacy_reconstructed')
        prepare.assert_not_called()

    def test_new_source_shot_without_transition_keeps_only_state_tail(self):
        previous, _appearance = self.previous_fixture()
        self.current()['director_contract'] = {}
        with patch.object(vc, 'previous_segment', return_value=previous), \
             patch.object(vc, 'prepare_handoff', side_effect=AssertionError('must not restore an old setting')) as prepare, \
             patch.object(vc, 'handoff_prompt', side_effect=lambda prompt, *args, **kw: prompt) as handoff:
            rendered = self.render()
        self.assertEqual(self.generate.call_args.kwargs['images'], [str(path) for path in self.refs] + [str(self.old_frame)])
        self.assertEqual(handoff.call_args.args[1], 3)
        self.assertIsNone(handoff.call_args.kwargs['appearance_number'])
        self.assertEqual(rendered['render_request']['handoff_version'], 'h3-handoff-v2')
        self.assertEqual(rendered['render_request']['appearance_frame'], '')
        prepare.assert_not_called()

    def test_valid_result_is_versioned_and_preserves_old_video_and_history(self):
        old_bytes = self.old.read_bytes()
        rendered = self.render()
        self.assertNotEqual(Path(rendered['video']), self.old)
        self.assertTrue(Path(rendered['video']).name.startswith(self.old.stem + '__'))
        self.assertEqual(self.old.read_bytes(), old_bytes)
        self.assertEqual(rendered['render_history'][0]['video'], str(self.old))
        self.assertEqual(rendered['render_history'][0]['end_frame'], str(self.old_frame))
        self.assertEqual(rendered['render_media']['check'], 'decoded')
        self.assertTrue(Path(rendered['end_frame']).is_file())
        self.assertNotIn('stale_video', self.current())

    def test_decoded_current_result_is_recognized_without_another_generation(self):
        self.render()
        count = self.process.call_count
        self.assertTrue(api.is_segment_current('TEST', 2, 1, 1))
        self.assertEqual(self.process.call_count, count)
        self.generate.assert_called_once()
        self.current()['prompt'] += 'Changed action.'
        self.assertFalse(api.is_segment_current('TEST', 2, 1, 1))

    def test_wrong_duration_does_not_replace_old_media(self):
        self.duration = 2
        old_bytes = self.old.read_bytes()
        with self.assertRaisesRegex(ValueError, '视频时长异常'):
            self.render()
        self.assertEqual(self.current()['video'], str(self.old))
        self.assertEqual(self.old.read_bytes(), old_bytes)
        self.assertTrue(self.current()['stale_video'])
        key = self.current()['render_request']['key']
        self.assertEqual(self.receipts[key]['status'], 'invalid_media')
        self.assertFalse(list(self.directory.glob(self.old.stem + '__*.mp4')))

    def test_decode_failure_does_not_replace_old_media(self):
        self.decode_error = True
        with self.assertRaisesRegex(ValueError, '解码失败'):
            self.render()
        self.assertEqual(self.current()['video'], str(self.old))
        self.assertEqual(self.current()['end_frame'], str(self.old_frame))
        self.assertFalse(list(self.directory.glob(self.old.stem + '__*.mp4')))

    def test_expected_audio_missing_does_not_replace_old_media(self):
        self.audio = False
        self.generate.return_value = dict(self.result(), expect_audio=True)
        with self.assertRaisesRegex(ValueError, '应有的音轨'):
            self.render()
        self.assertEqual(self.current()['video'], str(self.old))

    def test_explicit_missing_reference_stops_without_shifting_slots(self):
        self.current()['refs'] = [{'n': 'Alice'}, {'n': 'Bob'}, {'n': 'Hall'}]
        with patch.object(api, '_adopted_paths', return_value={'Alice': str(self.refs[0]), 'Hall': str(self.refs[1])}):
            with self.assertRaisesRegex(ValueError, 'Bob'):
                self.render()
        self.generate.assert_not_called()
        self.assertNotIn('render_request', self.current())

    def test_reference_disappearing_after_validation_does_not_shift_slots(self):
        isfile = os.path.isfile
        vanished = str(self.refs[0])
        with patch.object(api.os.path, 'isfile', side_effect=lambda path: False if str(path) == vanished else isfile(path)):
            with self.assertRaisesRegex((ValueError, RuntimeError), '参考'):
                self.render()
        self.generate.assert_not_called()
        self.assertEqual(self.current()['video'], str(self.old))

    def test_invalid_media_receipt_gets_a_new_request_for_retry(self):
        self.duration = 2
        with self.assertRaises(ValueError):
            self.render()
        first_key = self.current()['render_request']['key']
        self.duration = 8
        self.render()
        self.assertNotEqual(self.current()['render_request']['key'], first_key)
        self.assertEqual(self.receipts[first_key]['status'], 'invalid_media')

    def test_edit_during_generation_preserves_current_input_and_old_video(self):
        changed = self.current()['prompt'] + ' User changed the action while rendering.'
        def finish_after_edit(*args, **kwargs):
            self.current()['prompt'] = changed
            return self.result()
        self.generate.side_effect = finish_after_edit
        with self.assertRaisesRegex((ValueError, RuntimeError), '输入.*(?:改变|变化|修改)|(?:改变|变化|修改).*输入'):
            self.render()
        self.assertEqual(self.current()['prompt'], changed)
        self.assertEqual(self.current()['video'], str(self.old))
        self.assertEqual(self.old.read_bytes(), b'old accepted media must remain intact')

    def test_single_segment_button_calls_shared_renderer_with_request_settings(self):
        request = {'ep': 2, 'scene_no': 1, 'seg_no': 1, 'size_tier': '1.0', 'steps': 30, 'seed': 91}
        with patch.object(api, '_ep_of', return_value=2), \
             patch.object(api, 'render_segment', return_value={'video': 'rendered'}) as render:
            result = api.gen_segment(None, '/api/timeline/TEST/generate-segment', request)
        render.assert_called_once_with('TEST', 2, 1, 1, hd=False, override=request)
        self.assertTrue(result['ok'])
        self.generate.assert_not_called()


class ReferencePackTests(unittest.TestCase):
    def test_source_flow_empty_cast_uses_only_scene_reference(self):
        directory = Path(tempfile.mkdtemp(prefix='empty_cast_references_'))
        char = directory / 'person.png'
        scene = directory / 'room.png'
        char.write_bytes(b'person')
        scene.write_bytes(b'room')
        assets = {'characters': [{'name': 'Alice', 'character_id': 'C1'}],
                  'scenes': [{'name': 'Hall', 'scene_id': 'S1'}],
                  'visuals': [{'owner_id': 'C1', 'status': 'adopted', 'path': str(char)},
                              {'owner_id': 'S1', 'status': 'adopted', 'path': str(scene)}]}
        with patch.object(asset_core, 'list_assets', side_effect=lambda sid, kind: assets[kind]), \
             patch.object(story_core, 'get_story', return_value={'settings': {}}), \
             patch.object(reference_ready, 'assert_references'), \
             patch.object(authoring, 'h3_char_order', side_effect=lambda cards, settings: (cards, '')):
            self.assertEqual(api.new_pack('TEST', {'chars': [], 'scene': 'Hall', 'plan': {'flow': 'source-v1'}}), [str(scene)])


class CompletedReceiptReuseTests(unittest.TestCase):
    def test_completed_receipt_returns_before_any_model_service_or_gpu_call(self):
        # Exercise the adapter's cache return, while forbidding every model entry.
        # The environment guard is empty only within this fully mocked boundary.
        directory = Path(tempfile.mkdtemp(prefix='completed_render_receipt_'))
        output = directory / 'finished.mp4'
        output.write_bytes(b'already completed external result')
        result = {'output_path': str(output), 'seed': 73}
        from models import gpu_manager
        with patch.dict(os.environ, {'V41_NO_MODEL': ''}), \
             patch.object(h3, 'recovery_receipt', return_value={'status': 'completed', 'result': result}), \
             patch.object(h3, 'up', side_effect=AssertionError('must not contact model service')) as up, \
             patch.object(h3, 'start_h3', side_effect=AssertionError('must not start model')) as start, \
             patch.object(h3, '_http', side_effect=AssertionError('must not submit generation')) as http, \
             patch.object(gpu_manager, 'acquire', side_effect=AssertionError('must not acquire GPU')) as acquire:
            self.assertEqual(h3.generate('ref2va', 'existing request', recovery_key='completed'), result)
        up.assert_not_called()
        start.assert_not_called()
        http.assert_not_called()
        acquire.assert_not_called()


if __name__ == '__main__':
    unittest.main()
