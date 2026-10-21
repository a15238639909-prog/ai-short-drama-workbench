"""Reference precision transport with fake HTTP/GPU and retained fixtures."""
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from cores import store
from models import gpu_manager
from models import h3_client as h3


class H3ReferenceImageSizeTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='h3_reference_size_'))
        self.output = self.root / 'h3_output'
        self.output.mkdir()
        self.movie = self.output / 'mock-result.mp4'
        self.movie.write_bytes(b'fake-model-result')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(store, 'DATA', self.root / 'data'))
        self.stack.enter_context(patch.object(h3, 'H3_OUTPUT', self.output))
        self.stack.enter_context(patch.object(h3, 'OUTPUT_ROOT', self.root / 'copies'))
        self.copy_input = self.stack.enter_context(patch.object(
            h3, '_copy_to_input', side_effect=lambda source, tag: tag + '.png'))
        self.up = self.stack.enter_context(patch.object(h3, 'up', return_value=True))
        self.start = self.stack.enter_context(patch.object(
            h3, 'start_h3', side_effect=AssertionError('real model must never start')))
        self.acquire = self.stack.enter_context(patch.object(gpu_manager, 'acquire'))
        self.http = self.stack.enter_context(patch.object(
            h3, '_http', side_effect=AssertionError('unexpected HTTP call')))
        self.stack.enter_context(patch.object(h3, '_record_timing'))
        self.stack.enter_context(patch.object(h3, 'CANCEL', {'flag': False}))
        self.stack.enter_context(patch('cores.runtime_reset.assert_current'))
        # All model, GPU and HTTP routes are replaced before exercising generate.
        self.stack.enter_context(patch.dict(os.environ, {'V41_NO_MODEL': ''}))

    def build(self, mode='ref2va', **kwargs):
        return h3.build_workflow(mode, 'ordinary adult character',
                                 images=['character.png', 'continuation.png'],
                                 seconds=15, ratio='16:9', seed=42,
                                 filename_prefix='test', steps=10, **kwargs)

    @staticmethod
    def reference_node(graph):
        return h3._find_node(graph, 'MiniMaxH3ReferenceToVideo')[1]['inputs']

    def test_legacy_default_matches_explicit_match(self):
        old_default = self.build()
        explicit = self.build(reference_image_size='match')
        self.assertEqual(old_default, explicit)
        self.assertEqual(self.reference_node(old_default[0])['ref_image_size'], 'match')
        self.assertEqual(old_default[1]['reference_image_size'], 'match')

    def test_max_changes_only_reference_precision_not_image_count_or_order(self):
        default_graph, default_meta = self.build()
        max_graph, max_meta = self.build(reference_image_size='max')
        max_inputs = self.reference_node(max_graph)
        self.assertEqual(max_inputs['ref_image_size'], 'max')
        self.assertEqual(max_meta['reference_image_size'], 'max')
        image_slots = {k: v for k, v in max_inputs.items() if k.startswith('ref_images.')}
        self.assertEqual(image_slots, {
            k: v for k, v in self.reference_node(default_graph).items()
            if k.startswith('ref_images.')})
        self.assertEqual(len(image_slots), 2)
        self.assertEqual(sum(n['class_type'] == 'LoadImage' for n in max_graph.values()), 2)
        max_inputs['ref_image_size'] = 'match'
        max_meta['reference_image_size'] = 'match'
        self.assertEqual((max_graph, max_meta), (default_graph, default_meta))

    def test_other_modes_keep_the_same_workflow(self):
        for mode in ('t2va', 'i2va', 'fl2va'):
            with self.subTest(mode=mode):
                default = self.build(mode=mode)
                explicit = self.build(mode=mode, reference_image_size='max')
                self.assertEqual(default, explicit)
                self.assertNotIn('reference_image_size', default[1])

    def test_invalid_values_fail_before_image_copy_or_gpu_or_http(self):
        for value in ('native', '', None, 1, [], {}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'match或max'):
                    self.build(reference_image_size=value)
                with self.assertRaisesRegex(ValueError, 'match或max'):
                    h3.generate('ref2va', 'ordinary adult character',
                                reference_image_size=value)
        self.copy_input.assert_not_called()
        self.up.assert_not_called()
        self.acquire.assert_not_called()
        self.http.assert_not_called()
        self.start.assert_not_called()

    def test_generate_submits_selected_precision_once_and_reuses_receipt(self):
        for selection in (None, 'match', 'max'):
            with self.subTest(selection=selection):
                self.http.reset_mock()
                self.copy_input.reset_mock()
                submitted = []

                def fake_http(method, path, data=None, **kwargs):
                    if method == 'POST':
                        self.assertEqual(path, '/prompt')
                        submitted.append(data)
                        return {'prompt_id': data['prompt_id']}
                    self.assertEqual(path, '/history/' + submitted[0]['prompt_id'])
                    return {submitted[0]['prompt_id']: {
                        'status': {'status_str': 'success'},
                        'outputs': {'video': {'videos': [{'filename': self.movie.name}]}}}}

                self.http.side_effect = fake_http
                key = 'reference-size-' + str(selection)
                kwargs = {} if selection is None else {'reference_image_size': selection}
                result = h3.generate('ref2va', 'ordinary adult character',
                                     images=['character.png', 'continuation.png'],
                                     recovery_key=key, timeout=1, seed=42, **kwargs)
                selected = selection or 'match'
                self.assertEqual(len(submitted), 1)
                graph = submitted[0]['prompt']
                self.assertEqual(self.reference_node(graph)['ref_image_size'], selected)
                self.assertEqual(sum(n['class_type'] == 'LoadImage' for n in graph.values()), 2)
                self.assertEqual(self.copy_input.call_count, 2)
                self.assertEqual(result['reference_image_size'], selected)
                self.assertEqual(h3.recovery_receipt(key)['meta']['reference_image_size'], selected)
                self.assertEqual(h3.generate('ref2va', 'ordinary adult character',
                                            recovery_key=key, **kwargs), result)
                self.assertEqual(sum(c.args[0] == 'POST' for c in self.http.call_args_list), 1)
                self.assertEqual(self.copy_input.call_count, 2)
        self.start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
