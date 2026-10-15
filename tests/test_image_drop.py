import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from cores import image_drop


class ImageDropTests(unittest.TestCase):
    def test_private_targets_and_redirect_are_rejected(self):
        private=[(2,1,6,'',('127.0.0.1',80))]
        for url in ('http://localhost/a.png','http://192.168.1.2/x','https://example.com/x'):
            with patch.object(image_drop.socket,'getaddrinfo',return_value=private):
                with self.assertRaises(ValueError): image_drop.public_url(url)
        for url in ('file:///c:/secret.png','http://name:pass@example.com/x','http://example.com:8853/x'):
            with self.assertRaises(ValueError):image_drop.public_url(url)

    def test_local_preview_and_format_validation(self):
        root=Path(tempfile.mkdtemp(prefix='image_drop_retained_'))
        image=root/'sample.png';Image.new('RGB',(40,70),'red').save(image)
        with patch.object(image_drop,'ROOT',root):
            value=image_drop.read_image('http://127.0.0.1:18853/files/sample.png','http://127.0.0.1:18853')
            self.assertEqual(value['mime'],'image/png')
            self.assertTrue(value['base64'])
            with self.assertRaises(ValueError):image_drop.read_image('http://127.0.0.1:18853/files/../secret.png','http://127.0.0.1:18853')

    def test_remote_bytes_are_validated_without_model_or_download_file(self):
        data=io.BytesIO();Image.new('RGB',(12,14),'blue').save(data,format='PNG')
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        with patch.object(image_drop,'public_url',side_effect=lambda u:u),patch.object(image_drop,'build_opener') as opener:
            opener.return_value.open.return_value=Response(data.getvalue())
            value=image_drop.read_image('https://example.com/photo','http://localhost:18853')
            self.assertEqual(value['mime'],'image/png')
            opener.return_value.open.return_value=Response(b'<html>not an image</html>')
            with self.assertRaises(Exception):image_drop.read_image('https://example.com/x','http://localhost:18853')
