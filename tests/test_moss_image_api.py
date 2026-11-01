"""Image routing and history checks using retained fixtures and mocked models."""
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import image_api
from cores import gen_history


class MossImageApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = Path(tempfile.mkdtemp(prefix='moss_image_api_retained_'))
        cls.output = cls.folder / 'outputs' / 'moss' / 'sample.png'
        cls.output.parent.mkdir(parents=True)
        cls.output.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 8 + struct.pack('>II', 1224, 688))

    def test_moss_route_uses_frozen_prompt_dimensions_and_seed(self):
        from models import moss_client
        result = {'output_path': str(self.output), 'seed': 42}
        request = {'model': 'moss', 'text': '  白色茶杯，木桌，自然光  ',
                   'base_w': 1024, 'base_h': 576, 'seed': 42, 'kind': 'free'}
        with patch.object(image_api, 'ROOT', self.folder), \
             patch.object(moss_client, 'generate', return_value=result) as generate, \
             patch.object(gen_history, 'record') as record:
            response = image_api.image_generate(None, '/api/image/generate', request)
        self.assertTrue(response['ok'])
        generate.assert_called_once_with('白色茶杯，木桌，自然光', base_w=1024, base_h=576, seed=42)
        self.assertEqual(response['data']['model'], 'moss')
        self.assertEqual(response['data']['seed'], 42)
        self.assertEqual((response['data']['width'], response['data']['height']), (1224, 688))
        self.assertEqual(response['data']['url'], '/files/outputs/moss/sample.png')
        self.assertEqual(record.call_args.kwargs['extra']['model'], 'moss')
        self.assertEqual(record.call_args.kwargs['source'], '出图词·Moss')

    def test_empty_prompt_rejected_before_model(self):
        from models import moss_client
        with patch.object(moss_client, 'generate') as generate:
            response = image_api.image_generate(None, '', {'model': 'moss', 'text': ' '})
        self.assertEqual(response['_status'], 400)
        generate.assert_not_called()

    def test_explicit_moss_upscale_is_forwarded_and_other_models_keep_their_contract(self):
        from models import moss_client
        with patch.object(image_api, 'ROOT', self.folder), \
             patch.object(moss_client, 'generate', return_value={'output_path': str(self.output), 'seed': 42}) as generate, \
             patch.object(gen_history, 'record'):
            response = image_api.image_generate(None, '', {'model': 'moss', 'text': '写真',
                'base_w': 1440, 'base_h': 1920, 'seed': 42, 'upscale': 2})
        self.assertTrue(response['ok'])
        generate.assert_called_once_with('写真', base_w=1440, base_h=1920, seed=42, upscale=2)

    def test_default_dimensions_are_left_to_moss_workflow(self):
        from models import moss_client
        with patch.object(image_api, 'ROOT', self.folder), \
             patch.object(moss_client, 'generate', return_value={'output_path': str(self.output), 'seed': 9}) as generate, \
             patch.object(gen_history, 'record'):
            response = image_api.image_generate(None, '', {'model': 'moss', 'text': '山间小屋'})
        self.assertTrue(response['ok'])
        generate.assert_called_once_with('山间小屋', base_w=None, base_h=None, seed=None)

    def test_busy_model_returns_conflict(self):
        from models import moss_client
        image_api._MOSS_LOCK.acquire()
        try:
            with patch.object(moss_client, 'generate') as generate:
                response = image_api.image_generate(None, '', {'model': 'moss', 'text': '茶杯'})
            self.assertEqual(response['_status'], 409)
            generate.assert_not_called()
        finally:
            image_api._MOSS_LOCK.release()

    def test_model_errors_release_lock(self):
        from models import moss_client
        for error, status in ((ValueError('尺寸超范围'), 400), (RuntimeError('工作流失败'), 500)):
            with self.subTest(error=type(error).__name__), \
                 patch.object(moss_client, 'generate', side_effect=error), \
                 patch.object(gen_history, 'record') as record:
                response = image_api.image_generate(None, '', {'model': 'moss', 'text': '茶杯'})
            self.assertEqual(response['_status'], status)
            record.assert_not_called()
            self.assertTrue(image_api._MOSS_LOCK.acquire(blocking=False))
            image_api._MOSS_LOCK.release()

    def test_non_png_output_does_not_enter_history(self):
        from models import moss_client
        invalid = self.folder / 'not_an_image.png'
        invalid.write_bytes(b'not a png')
        with patch.object(image_api, 'ROOT', self.folder), \
             patch.object(moss_client, 'generate', return_value={'output_path': str(invalid), 'seed': 1}), \
             patch.object(gen_history, 'record') as record:
            response = image_api.image_generate(None, '', {'model': 'moss', 'text': '茶杯'})
        self.assertEqual(response['_status'], 500)
        record.assert_not_called()

    def test_anima_and_unsupported_routing_remain_separate(self):
        request = {'model': 'anima', 'text': 'blue sky'}
        with patch.object(image_api, 'anima_image', return_value={'ok': True}) as anima:
            self.assertTrue(image_api.image_generate(None, '', request)['ok'])
        anima.assert_called_once_with(request)
        response = image_api.image_generate(None, '', {'model': 'unknown', 'text': '茶杯'})
        self.assertEqual(response['_status'], 400)

    def test_legacy_request_keeps_krea_default(self):
        from models import krea_client
        with patch.object(image_api, 'ROOT', self.folder), \
             patch.object(krea_client, 'generate', return_value={'output_path': str(self.output)}) as krea, \
             patch.object(gen_history, 'record'):
            response = image_api.image_generate(None, '', {'text': '茶杯', 'kind': 'free'})
        self.assertTrue(response['ok'])
        krea.assert_called_once_with('茶杯', base_w=None, base_h=None)

    def test_explicit_krea_seed_is_forwarded(self):
        from models import krea_client
        with patch.object(image_api, 'ROOT', self.folder), \
             patch.object(krea_client, 'generate', return_value={'output_path': str(self.output)}) as krea, \
             patch.object(gen_history, 'record'):
            response = image_api.image_generate(None, '', {'model': 'krea2', 'text': '茶杯',
                'kind': 'free', 'base_w': 1536, 'base_h': 864, 'seed': 42})
        self.assertTrue(response['ok'])
        krea.assert_called_once_with('茶杯', base_w=1536, base_h=864, seed=42)


if __name__ == '__main__':
    unittest.main()
