"""Two compact Anima presets assemble local context and retain named identity."""
import os
import unittest
from unittest.mock import patch

from cores import authoring, anima_names
from models import anima_client as anima


IDENTITY = dict(character_tag='android_18', series_tag='dragon_ball',
                character_name='Android 18', series_name='Dragon Ball', trigger='android 18, dragon ball')
SHEET = ('android 18, dragon ball, character turnaround sheet, same character in every panel, '
         'first panel front full-body view, second panel right-facing profile full-body view, '
         'third panel back full-body view, fourth panel front-facing close-up portrait')


class AnimaTwoPresetsTests(unittest.TestCase):
    def setUp(self):
        for item in (patch.dict(os.environ, {'V41_NO_MODEL': '1'}),
                     patch.object(anima, 'local_guard'), patch.object(anima, 'idle_guard'),
                     patch.object(anima_names, 'resolve', return_value=[IDENTITY])):
            item.start()
            self.addCleanup(item.stop)

    def test_regular_prompt_gets_names_relevant_modules_and_capacity_check(self):
        text = '七龙珠18号，改穿红色宇航服，站在月球表面，写实画风'
        body = 'android 18, dragon ball, red spacesuit, standing on the Moon, photorealistic'
        with patch.object(authoring, '_q', return_value=body) as query:
            result = anima.write_prompt(text)
        system, user = query.call_args.args
        self.assertEqual(user, text)
        self.assertIn('【本地已确认身份】', system)
        self.assertIn(IDENTITY['trigger'], system)
        self.assertIn('把用户指定的外观或服装改动绑定到对应角色', system)
        self.assertTrue(query.call_args.kwargs['_check_context'])
        self.assertEqual(anima.compose_prompt(result)['body'], body)

    def test_sheet_preset_without_layout_words_defaults_to_four_views(self):
        system = anima.SHEET_SYSTEM.read_text(encoding='utf-8')
        with patch.object(authoring, '_q', return_value=SHEET) as query:
            result = anima.write_prompt('七龙珠18号', system)
        self.assertIn('fourth panel', query.call_args.args[0])
        self.assertTrue(query.call_args.kwargs['_check_context'])
        self.assertTrue(anima.sheet_layout_valid(result, portrait=True))

    def test_sheet_rejects_missing_face_before_image_generation(self):
        incomplete = SHEET.split(', fourth panel')[0]
        with patch.object(authoring, '_q', return_value=incomplete), self.assertRaisesRegex(ValueError, '缺少'):
            anima.write_prompt('七龙珠18号', anima.SHEET_SYSTEM.read_text(encoding='utf-8'))

    def test_locked_character_cannot_be_silently_replaced_by_qwen(self):
        with patch.object(authoring, '_q', return_value='bulma, dragon ball, standing'), self.assertRaises(ValueError):
            anima.write_prompt('七龙珠18号')

    def test_scene_loads_only_relevant_rules(self):
        modules, content = anima.prompt_modules('森林中的古老工坊，水彩画风')
        self.assertEqual(modules, ['01_common', '06_scene', '07_rendering'])
        self.assertNotIn('发型', content)
        self.assertLess(len(content), 600)

    def test_current_presets_stay_short(self):
        for path in (anima.SYSTEM, anima.SHEET_SYSTEM):
            with self.subTest(path=path):
                self.assertLess(len(path.read_text(encoding='utf-8')), 1000)


if __name__ == '__main__':
    unittest.main()
