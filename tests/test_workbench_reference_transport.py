"""Safe reference transport regressions; model/media work is fully mocked."""
import copy
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from api import segment_api as api
from cores import reference_bindings as bindings, video_continuity as continuity
from tests import test_segment_render_recovery as render_cases


class WorkbenchReferenceTransportTests(unittest.TestCase):
    def setUp(self):
        self.case = render_cases.SegmentRenderRecoveryTests(methodName='runTest')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.catalog = [dict(owner_id='ADULT_ALICE', name='Alice', role='character', path=str(self.case.refs[0])),
                        dict(owner_id='WORKSHOP', name='Hall', role='scene', path=str(self.case.refs[1]))]
        self.case.stack.enter_context(patch.object(bindings, 'adopted_catalog', return_value=self.catalog))
        self.case.stack.enter_context(patch.object(api, '_adopted_paths', side_effect=lambda sid: {
            'Alice': str(self.case.refs[0]), 'Hall': str(self.case.refs[1])}))
        self.case.stack.enter_context(patch.object(Path, 'unlink', side_effect=AssertionError('no permanent unlink')))
        self.case.stack.enter_context(patch('shutil.rmtree', side_effect=AssertionError('no permanent deletion')))

    @staticmethod
    def standard_prompt():
        return ('subject_definitions:\n'
                '<Subject 1> is Alice, the character from <Picture 1>. Adult worker wearing ordinary clothes.\n'
                '<Subject 2> is Hall, the environment from <Picture 2>.\n\n'
                'summary:\n[reference generation] Observe the repaired watch.\n\n'
                'retention_analysis:\n<Picture 1> character identity; <Picture 2> workshop.\n\n'
                'detailed_description:\n[Shot 1] <Subject 1> (S1) holds the repaired watch.\n\n'
                'overall_soundscape:\nQuiet room and a ticking clock.\n\n'
                'non_diegetic_music:\nN/A')

    def select(self, names, prompt=None):
        self.case.current()['refs'] = [dict(n=name) for name in names]
        self.case.current()['prompt'] = prompt or self.standard_prompt()

    def test_legacy_pov_handoff_uses_real_adapter_without_rewriting_original_prompt(self):
        original = ('<Subject1>林海是这段的成年拍摄者，镜头就是他的眼睛。\n'
                    '<Subject 2>是<Picture 1>修表店场景。\n'
                    '0—8秒：林海主观视角，观察柜台上的手表。')
        self.case.current().update(prompt=original, plan={})
        previous, _ = self.case.previous_fixture()
        with patch.object(continuity, 'previous_segment', return_value=previous):
            result = self.case.render()
        self.case.generate.assert_called_once()
        self.assertTrue(result['h3_prompt_sent'].startswith(original + '\n\n'))
        self.assertIn('Continuity handoff: <Picture 3>', result['h3_prompt_sent'])
        self.assertEqual(self.case.generate.call_args.kwargs['images'][-1], str(self.case.old_frame))
        self.assertEqual(self.case.current()['prompt'], original)
        self.assertNotIn('subject_definitions:', result['h3_prompt_sent'])

    def test_incomplete_six_section_document_is_not_mistaken_for_legacy(self):
        previous, _ = self.case.previous_fixture()
        self.case.current()['prompt'] = 'subject_definitions:\n<Subject 1> is Alice from <Picture 1>.'
        with patch.object(continuity, 'previous_segment', return_value=previous), \
                self.assertRaisesRegex(ValueError, '六字段顺序无效'):
            self.case.render()
        self.case.generate.assert_not_called()

    def test_reversed_selection_rebinds_picture_tokens_once_and_preserves_subject_voice(self):
        original = self.standard_prompt()
        self.select(['Hall', 'Alice'], original)
        result = self.case.render()
        submitted = result['h3_prompt_sent']
        self.assertIn('<Subject 1> is Alice, the character from <Picture 2>.', submitted)
        self.assertIn('<Subject 2> is Hall, the environment from <Picture 1>.', submitted)
        self.assertIn('<Subject 1> (S1) holds the repaired watch.', submitted)
        self.assertEqual(self.case.generate.call_args.kwargs['images'],
                         [str(self.case.refs[1]), str(self.case.refs[0])])
        self.assertEqual(self.case.current()['prompt'], original)
        request = self.case.current()['render_request']
        self.assertEqual(request['reference_binding_audit']['status'], 'rebound')
        self.assertEqual(request['final_prompt'], submitted)

    def test_chinese_standard_definitions_rebind_without_changing_character_identity(self):
        original = ('<Subject1>Alice是<Picture 1>保留参考图中的面部和普通工作服。\n'
                    '<Subject 3>是<Picture 2>Hall场景，保留房间结构。\n'
                    '0—8秒：<Subject1> (S1)观察柜台上的手表。')
        updated, audit = bindings.rebind_prompt(original, [str(self.case.refs[1]), str(self.case.refs[0])],
                                               list(map(str, self.case.refs)), self.catalog)
        self.assertIn('<Subject1>Alice是<Picture 2>', updated)
        self.assertIn('<Subject 3>是<Picture 1>Hall场景', updated)
        self.assertIn('<Subject1> (S1)', updated)
        self.assertEqual(audit['status'], 'rebound')

    def test_missing_proven_identity_does_not_submit_even_if_numeric_slots_fit(self):
        extra = self.case.directory / 'other_room.png'
        extra.write_bytes(b'ordinary extra room')
        self.select(['Hall', 'Other'])
        with patch.object(api, '_adopted_paths', return_value={
                'Alice': str(self.case.refs[0]), 'Hall': str(self.case.refs[1]), 'Other': str(extra)}), \
                self.assertRaisesRegex(ValueError, '缺少提示词已绑定的.*Alice'):
            self.case.render()
        self.case.generate.assert_not_called()
        self.assertNotIn('render_request', self.case.current())

    def test_one_old_picture_cannot_silently_point_at_two_different_assets(self):
        conflict = self.standard_prompt().replace('Hall, the environment from <Picture 2>',
                                                  'Hall, the environment from <Picture 1>')
        self.select(['Hall', 'Alice'], conflict)
        with self.assertRaisesRegex(ValueError, '同一图片编号绑定了不同素材'):
            self.case.render()
        self.case.generate.assert_not_called()
        self.assertNotIn('render_request', self.case.current())

    def test_same_order_custom_prompt_does_not_require_name_parser(self):
        original = '<Subject 1> watches the object in <Picture 1> while <Picture 2> supplies its backdrop.'
        self.select(['Alice', 'Hall'], original)
        result = self.case.render()
        self.case.generate.assert_called_once()
        self.assertEqual(result['h3_prompt_sent'], original)
        self.assertEqual(result['render_request']['reference_binding_audit']['status'], 'unchanged')

    def test_unknown_custom_reverse_selection_is_preserved_without_guessed_replacement(self):
        original = '<Subject 1> follows the composition in <Picture 1> and the colors in <Picture 2>.'
        self.select(['Hall', 'Alice'], original)
        result = self.case.render()
        self.case.generate.assert_called_once()
        self.assertEqual(result['h3_prompt_sent'], original)
        self.assertEqual(result['render_request']['reference_binding_audit']['status'], 'unverified_custom')

    def test_frozen_explicit_selection_keeps_original_snapshot_and_key(self):
        self.select(['Hall', 'Alice'])
        signature = api._render_context('TEST', 2, 1, 1)[-1]
        final_prompt = self.standard_prompt()
        final_refs = list(map(str, self.case.refs))
        request = dict(key='original-frozen-task', status='pending', input_signature=signature,
                       requested_seed=None, final_prompt=final_prompt, final_refs=final_refs,
                       resolved_params=dict(mode='ref2va', seconds=8, ratio='16:9', size_tier='0.7', steps=25, seed=None))
        self.case.current()['render_request'] = copy.deepcopy(request)
        self.case.receipts[request['key']] = dict(status='submitted', prompt_id='original-task-id')
        with patch.object(bindings, 'rebind_prompt', side_effect=AssertionError('frozen prompt must stay frozen')):
            result = self.case.render()
        self.assertEqual(result['h3_prompt_sent'], final_prompt)
        self.assertEqual(self.case.generate.call_args.kwargs['images'], final_refs)
        self.assertEqual(self.case.generate.call_args.kwargs['recovery_key'], request['key'])
        self.assertNotIn('reference_binding_audit', result['render_request'])

    def test_legacy_pending_selection_keeps_its_existing_key_and_assembly(self):
        self.select(['Hall', 'Alice'])
        signature = api._render_context('TEST', 2, 1, 1)[-1]
        self.case.current()['render_request'] = dict(key='legacy-submitted-task', status='pending',
            input_signature=signature, requested_seed=None)
        self.case.receipts['legacy-submitted-task'] = dict(status='submitted', prompt_id='legacy-task-id')
        with patch.object(bindings, 'rebind_prompt', side_effect=AssertionError('pending prompt must not be rebound')):
            result = self.case.render()
        self.assertEqual(result['h3_prompt_sent'], self.standard_prompt())
        self.assertEqual(self.case.generate.call_args.kwargs['recovery_key'], 'legacy-submitted-task')
        self.assertNotIn('reference_binding_audit', result['render_request'])


if __name__ == '__main__':
    unittest.main()
