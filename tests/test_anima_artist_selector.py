"""Anima artist composition and API transport without model calls or files."""
import io
import json
import os
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from api import image_api
from cores import authoring, gen_history, runtime_reset
from models import anima_client as anima, gpu_manager, krea_client


class AnimaArtistSelectorTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'V41_NO_MODEL': '1'})
        environment.start()
        self.addCleanup(environment.stop)

    def test_config_exposes_nine_artists_and_exact_default_prefix(self):
        result = image_api.anima_config(None, '', {})['data']
        self.assertEqual(result['artists'], list(anima.ARTISTS))
        self.assertEqual(len(result['artists']), 9)
        self.assertEqual(len({item['value'] for item in result['artists']}), 9)
        self.assertTrue(all(item['label'] for item in result['artists']))
        self.assertEqual(result['default_artist'], '@chigusa_minori')
        self.assertEqual(result['quality_prefix'], ['masterpiece', 'best quality', 'score_9', 'score_8',
                                                  'highres', 'absurdres', 'anime screenshot', 'official art'])
        self.assertEqual(result['fixed_prefix'],
                         '@chigusa_minori, masterpiece, best quality, score_9, score_8, highres, absurdres, anime screenshot, official art')

    def test_default_composition_retains_body_and_is_idempotent(self):
        body = '1girl, blue coat, The woman holds a map near the window.'
        result = anima.compose_prompt(body)
        self.assertEqual(result, {'prompt': anima.FIXED_PREFIX + ', ' + body,
                                  'body': body, 'artist': anima.DEFAULT_ARTIST})
        self.assertEqual(anima.compose_prompt(result['prompt']), result)

    def test_explicit_artist_replaces_old_tags_and_deduplicates_only_managed_tags(self):
        selected = anima.ARTISTS[1]['value']
        body = 'blue coat, blue coat, A sign reads @someone.'
        prompt = '@old_artist, masterpiece, BEST QUALITY, score_9, masterpiece, @other_artist, ' + body
        result = anima.compose_prompt(prompt, selected)
        self.assertEqual(result['artist'], selected)
        self.assertEqual(result['body'], body)
        self.assertEqual(result['prompt'], ', '.join((selected, *anima.QUALITY_PREFIX, body)))
        self.assertEqual(anima.compose_prompt(result['prompt'], selected), result)

    def test_missing_artist_field_preserves_legacy_unknown_and_multiple_artists(self):
        for artists in ('@legacy_artist', '@legacy_one, @legacy_two', '@legacy_artist, @legacy_artist'):
            with self.subTest(artists=artists):
                result = anima.compose_prompt(artists + ', best quality, blue coat')
                self.assertEqual(result['artist'], artists)
                self.assertEqual(result['body'], 'blue coat')
                self.assertEqual(result['prompt'], ', '.join((artists, *anima.QUALITY_PREFIX, 'blue coat')))
                self.assertNotIn(anima.DEFAULT_ARTIST, result['prompt'])
                self.assertEqual(anima.compose_prompt(result['prompt']), result)

    def test_invalid_explicit_artists_are_rejected(self):
        for value in ('@unlisted_artist', '', [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                anima.compose_prompt('blue coat', value)

    def test_fullwidth_tag_separators_do_not_duplicate_original_quality_prefix(self):
        body = 'blue coat, A sign reads @someone.'
        original = ('@chigusa_minori, masterpiece, best quality, score_9, score_8，highres, '
                    'absurdres, anime screenshot，official art，' + body)
        result = anima.compose_prompt(original)
        self.assertEqual(result, {'prompt': anima.FIXED_PREFIX + ', ' + body,
                                  'body': body, 'artist': anima.DEFAULT_ARTIST})
        self.assertEqual(anima.compose_prompt(result['prompt']), result)

    def test_prefix_without_body_is_rejected_before_generation(self):
        for text in (anima.FIXED_PREFIX, '@legacy_one, @legacy_two', 'masterpiece，best quality', ',，，'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, '不能只有画师或质量前缀'):
                anima.compose_prompt(text)
        with patch.object(anima, 'local_guard') as guard, patch.object(anima, 'idle_guard') as idle:
            with self.assertRaisesRegex(ValueError, '不能只有画师或质量前缀'):
                anima.generate(anima.FIXED_PREFIX)
        guard.assert_not_called()
        idle.assert_not_called()
        response = image_api.anima_compose(None, '', {'text': anima.FIXED_PREFIX})
        self.assertEqual(response['_status'], 400)
        self.assertIn('不能只有画师或质量前缀', response['error'])

    def test_compose_endpoint_does_not_call_model_or_change_runtime(self):
        selected = anima.ARTISTS[1]['value']
        with patch.object(authoring, '_q', side_effect=AssertionError('compose must not call Qwen')), \
             patch.object(anima, 'generate', side_effect=AssertionError('compose must not generate')), \
             patch.object(anima, 'local_guard') as guard:
            result = image_api.anima_compose(None, '', {'text': '@old_artist, blue coat', 'artist': selected})
        guard.assert_not_called()
        self.assertTrue(result['ok'])
        self.assertEqual(result['data'], anima.compose_prompt('blue coat', selected))
        self.assertEqual(image_api.anima_compose(None, '', {'text': 'blue coat', 'artist': 'invalid'})['_status'], 400)

    def test_writer_asks_for_body_then_composes_exact_selected_prefix(self):
        selected = anima.ARTISTS[1]['value']
        with patch.object(anima, 'local_guard'), patch.object(anima, 'idle_guard'), \
             patch.object(authoring, '_q', return_value='@wrong_artist, masterpiece, blue coat') as query:
            result = anima.write_prompt('成年研究员站在窗前。', '【GPT优化指令词：Anima】\n保留用户画面。', artist=selected)
        instruction = query.call_args.args[0]
        self.assertIn(anima.BODY_INSTRUCTION, instruction)
        self.assertNotIn(selected, instruction)
        self.assertNotIn(anima.FIXED_PREFIX, instruction)
        self.assertEqual(query.call_args.args[1], '成年研究员站在窗前。')
        self.assertTrue(query.call_args.kwargs['_check_context'])
        self.assertEqual(result, anima.compose_prompt('blue coat', selected)['prompt'])

    def test_prompt_api_passes_artist_and_returns_complete_body_contract(self):
        selected = anima.ARTISTS[1]['value']
        composed = anima.compose_prompt('blue coat', selected)
        with patch.object(anima, 'write_prompt', return_value=composed['prompt']) as writer:
            result = image_api.anima_prompt(None, '', {'text': '成年研究员。', 'system': '自定义正文指令', 'artist': selected})
        writer.assert_called_once_with('成年研究员。', '自定义正文指令', artist=selected)
        self.assertEqual(result['data'], dict(composed, model='anima'))

    def test_workflow_changes_only_existing_runtime_fields_and_positive_prompt(self):
        original = json.loads(anima.WORKFLOW.read_text(encoding='utf-8'))
        selected = anima.ARTISTS[1]['value']
        workflow = anima.build_workflow('@old_artist, blue coat', 768, 1024, seed=42, artist=selected)
        original['11']['inputs']['text'] = anima.compose_prompt('blue coat', selected)['prompt']
        original['12']['inputs']['text'] = ''
        original['28']['inputs'].update(width=768, height=1024)
        original['63']['inputs']['seed'] = 42
        original['99']['inputs']['filename_prefix'] = workflow['99']['inputs']['filename_prefix']
        self.assertEqual(workflow, original)

    def test_generate_sends_and_returns_the_same_complete_prompt(self):
        selected = anima.ARTISTS[1]['value']
        expected = anima.compose_prompt('blue coat', selected)
        history = {'fixture': {'outputs': {'99': {'images': [{'filename': 'fixture.png', 'subfolder': ''}]}}}}
        with patch.object(anima, 'local_guard'), patch.object(anima, 'idle_guard'), \
             patch.object(Path, 'is_file', return_value=True), patch.object(Path, 'mkdir'), \
             patch.object(anima.shutil, 'copy2'), patch.object(krea_client, 'up', return_value=True), \
             patch.object(krea_client, '_http', side_effect=[{'prompt_id': 'fixture'}, history]) as http, \
             patch.object(gpu_manager, 'acquire'), patch.object(gpu_manager, 'claim'), \
             patch.object(gpu_manager, 'release'), patch.object(runtime_reset, 'assert_current'):
            result = anima.generate('@old_artist, blue coat', 768, 1024, seed=42, artist=selected)
        submitted = http.call_args_list[0].args[2]['prompt']['11']['inputs']['text']
        self.assertEqual(submitted, expected['prompt'])
        self.assertEqual(result['prompt'], submitted)
        self.assertEqual(result['artist'], selected)

    def test_image_api_history_and_response_use_actual_final_prompt(self):
        selected = anima.ARTISTS[1]['value']
        expected = anima.compose_prompt('blue coat', selected)
        output = str(image_api.ROOT / 'outputs' / 'anima' / 'fixture.png')
        generated = {'output_path': output, 'seed': 42, 'prompt_id': 'fixture',
                     'prompt': expected['prompt'], 'artist': selected}
        header = b'\x89PNG\r\n\x1a\n' + b'\0' * 8 + struct.pack('>II', 768, 1024)
        with patch.object(anima, 'generate', return_value=generated) as generate, \
             patch('builtins.open', return_value=io.BytesIO(header)), patch.object(gen_history, 'record') as record:
            result = image_api.anima_image({'text': '@old_artist, blue coat', 'artist': selected,
                                           'base_w': 768, 'base_h': 1024, 'seed': 42})
        self.assertTrue(result['ok'])
        generate.assert_called_once_with('@old_artist, blue coat', base_w=768, base_h=1024, seed=42, artist=selected)
        self.assertEqual(record.call_args.args[2], expected['prompt'])
        self.assertEqual(record.call_args.kwargs['extra']['artist'], selected)
        self.assertEqual(result['data']['prompt'], expected['prompt'])
        self.assertEqual(result['data']['artist'], selected)


if __name__ == '__main__':
    unittest.main()
