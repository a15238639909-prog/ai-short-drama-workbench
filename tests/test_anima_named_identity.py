"""All Anima Qwen routes share name-based IP identity and preserve custom input."""
import os
import unittest
from unittest.mock import patch

from cores import authoring
from models import anima_client as anima


class AnimaNamedIdentityTests(unittest.TestCase):
    def setUp(self):
        for item in (patch.dict(os.environ, {'V41_NO_MODEL': '1'}),
                     patch.object(anima, 'local_guard'), patch.object(anima, 'idle_guard')):
            item.start()
            self.addCleanup(item.stop)

    def test_identity_rule_is_final_instruction_for_all_preset_routes(self):
        prompt_body = ('Dragon Ball, Android 18, left panel front full body view, '
                       'center panel right profile full body, right panel back full body, '
                       'fourth panel front-facing close-up portrait')
        presets = [None, anima.SYSTEM.read_text(encoding='utf-8'),
                   anima.SHEET_SYSTEM.read_text(encoding='utf-8'),
                   (anima.ROOT / '_legacy_8848/presets/anima/Anima·人设图.txt').read_text(encoding='utf-8'),
                   (anima.ROOT / '_legacy_8848/presets/anima/GPT优化指令词.txt').read_text(encoding='utf-8'),
                   (anima.ROOT / '_legacy_8848/presets/anima/Anima·常规单图.txt').read_text(encoding='utf-8'),
                   '用户以前保存的自定义指令']
        for system in presets:
            with self.subTest(system=str(system)[:35]), \
                 patch.object(authoring, '_q', return_value=prompt_body) as query:
                result = anima.write_prompt('七龙珠人造人18号站立三视图加正脸特写人设图', system)
                instruction = query.call_args.args[0]
                self.assertTrue(instruction.endswith(anima.BODY_INSTRUCTION))
                self.assertIn('Dragon Ball, Android 18', instruction)
                self.assertIn('仅翻译用户明确给出的定制', instruction)
                self.assertEqual(anima.compose_prompt(result)['body'], prompt_body)

    def test_explicit_appearance_customization_and_scene_are_preserved(self):
        request = '七龙珠人造人18号，改穿红色宇航服，站在月球表面，写实画风'
        body = 'Dragon Ball, Android 18, red spacesuit, standing on the Moon, photorealistic'
        with patch.object(authoring, '_q', return_value=body) as query:
            result = anima.write_prompt(request)
        self.assertEqual(query.call_args.args[1], request)
        self.assertEqual(anima.compose_prompt(result)['body'], body)

    def test_manual_saved_prompt_is_not_rewritten_by_identity_policy(self):
        body = 'Dragon Ball, Android 18, red jacket, blonde hair, blue eyes'
        self.assertEqual(anima.compose_prompt(body)['body'], body)


if __name__ == '__main__':
    unittest.main()
