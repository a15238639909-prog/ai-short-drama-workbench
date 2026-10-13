"""Regenerating a rejected setting image must retain the previous file."""
import os
os.environ['V41_NO_MODEL'] = '1'
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cores import authoring, asset_core, gen_history
from models import krea_client


class ImageVersionTests(unittest.TestCase):
    def test_regeneration_keeps_previous_image_and_visual_version(self):
        root = Path(tempfile.mkdtemp(prefix='character_image_version_'))
        images = root / 'outputs' / 'krea'
        images.mkdir(parents=True)
        previous = images / 'TEST_CHAR_阿宁.png'
        previous.write_bytes(b'original reference image')
        generated = root / 'generated.png'
        generated.write_bytes(b'new reference image')
        card = {'name': '阿宁', 'character_id': 'C1', 'age': 28, 'sex': '女',
                'hair': '低马尾', 'face_spec': {'face_shape': '鹅蛋脸'}, 'prompt_override': 'clothed person'}
        visuals = [{'owner_id': 'C1', 'visual_version': 1, 'path': str(previous), 'status': 'candidate'}]
        with patch.object(authoring, '__file__', str(root / 'cores' / 'authoring.py')), \
             patch.object(authoring, 'hair_is_designed', return_value=True), \
             patch.object(asset_core, 'list_assets', return_value=visuals), \
             patch.object(asset_core, 'add_approved_visual', return_value={'visual_id': 'V2'}) as add, \
             patch.object(asset_core, 'adopt'), patch.object(gen_history, 'record'), \
             patch.object(krea_client, 'generate', return_value={'output_path': str(generated)}):
            result = authoring._gen_char_images('TEST', [card], {})
        self.assertFalse(result['failed'])
        self.assertEqual(previous.read_bytes(), b'original reference image')
        target = images / 'TEST_CHAR_阿宁__v2.png'
        self.assertEqual(target.read_bytes(), b'new reference image')
        self.assertEqual(add.call_args.args[3], 2)
        self.assertEqual(add.call_args.args[4], str(target))


if __name__ == '__main__':
    unittest.main()
