import ast
import io
import os
from pathlib import Path
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / '_legacy_8848' / 'app.py'


class MossPromptPresetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        names = {'_preset_model', '_preset_path', 'load_presets'}
        constants = {'ANIMA_PRESET_ALIASES', 'ANIMA_BUILTIN_PRESETS', 'MOSS_PRESET_ALIASES', 'MOSS_BUILTIN_PRESETS'}
        definitions = [node for node in tree.body if
                       isinstance(node, ast.FunctionDef) and node.name in names or
                       isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in constants for target in node.targets)]
        cls.namespace = {'os': os, 'PRESET_DIR': '/virtual/presets', 'DEFAULT_OPT': '中文默认',
                         '_package_paths': types.SimpleNamespace(ROOT=Path('/virtual/app'))}
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(SOURCE), 'exec'), cls.namespace)

    def setUp(self):
        self.files = {
            '/virtual/presets/通用提示词优化.txt': '通用中文',
            '/virtual/presets/krea2/人物.txt': '三视图中文',
            '/virtual/presets/krea2/场景.txt': '原作画风中文',
            '/virtual/presets/anima/自定义.txt': 'English tags',
            '/virtual/presets/anima动漫模型提示词.txt': 'archived long template',
            '/virtual/presets/Anima·人设三视图＋面部特写.txt': 'archived sheet',
            '/virtual/presets/anima/Anima·英文词组.txt': 'archived default',
            '/virtual/presets/anima/GPT优化指令词.txt': 'archived GPT',
            '/virtual/app/presets/instructions/Anima_英文词组.txt': 'English default',
            '/virtual/app/presets/instructions/Anima_人物三视图.txt': 'English sheet',
            '/virtual/presets/moss/GPT优化指令词.txt': 'Moss独立中文',
            '/virtual/presets/moss/Moss·人物人设图.txt': 'Moss人物布局',
            '/virtual/presets/moss/Moss·场景图.txt': 'Moss场景中文',
            '/virtual/presets/moss/三视图.txt': '归档副本',
            '/virtual/presets/moss/Krea2·作品角色人设四视图.txt': '归档副本2',
        }

    def normalize(self, value):
        return str(value).replace('\\', '/')

    def listdir(self, folder):
        prefix = self.normalize(folder).rstrip('/') + '/'
        return sorted({filename[len(prefix):].split('/')[0] for filename in self.files if filename.startswith(prefix)})

    def open_file(self, filename, mode='r', encoding=None):
        self.assertEqual(mode, 'r')
        return io.StringIO(self.files[self.normalize(filename)])

    def load(self):
        with patch('os.listdir', side_effect=self.listdir), \
             patch('os.path.isdir', side_effect=lambda folder: bool(self.listdir(folder))), \
             patch('os.path.isfile', side_effect=lambda filename: self.normalize(filename) in self.files), \
             patch('builtins.open', side_effect=self.open_file):
            return {(row['model'], row['name']): row for row in self.namespace['load_presets']()}

    def test_moss_independent_presets_and_archives_unchanged(self):
        before = dict(self.files)
        presets = self.load()
        for name in ('通用提示词优化', '人物', '场景'):
            self.assertIn(('krea2', name), presets)
            self.assertNotIn(('moss', name), presets)
        self.assertEqual({name for model, name in presets if model == 'moss'},
                         {'GPT优化指令词', 'Moss·人物人设图', 'Moss·场景图'})
        self.assertTrue(presets['moss', 'Moss·人物人设图']['builtin'])
        self.assertEqual(presets['anima', '自定义']['system'], 'English tags')
        self.assertEqual(presets['anima', 'Anima·常规单图']['system'], 'English default')
        self.assertEqual(presets['anima', 'Anima·人设图']['system'], 'English sheet')
        self.assertNotIn(('moss', 'Anima·常规单图'), presets)
        self.assertEqual(self.files, before)

    def test_anima_archived_names_hidden_without_reading_or_changing_files(self):
        before = dict(self.files)
        presets = self.load()
        names = {name for model, name in presets if model == 'anima'}
        self.assertEqual(names, {'Anima·常规单图', 'Anima·人设图', '自定义'})
        self.assertEqual(self.files, before)
        self.assertEqual({name for model, name in presets if model == 'anima' and presets[model, name].get('builtin')},
                         {'Anima·常规单图', 'Anima·人设图'})

    def test_anima_new_files_override_fallback_and_remain_editable_builtins(self):
        self.files['/virtual/presets/anima/Anima·常规单图.txt'] = 'new single image'
        self.files['/virtual/presets/anima/Anima·人设图.txt'] = 'new character sheet'
        self.files['/virtual/presets/krea2/GPT优化指令词.txt'] = 'Krea GPT'
        self.files['/virtual/presets/moss/GPT优化指令词.txt'] = 'Moss GPT'
        presets = self.load()
        for name, system in (('Anima·常规单图', 'new single image'), ('Anima·人设图', 'new character sheet')):
            self.assertEqual(presets['anima', name]['system'], system)
            self.assertTrue(presets['anima', name]['builtin'])
            with patch('os.path.isfile', side_effect=lambda filename: self.normalize(filename) in self.files):
                filename, safe = self.namespace['_preset_path'](name, 'anima', action='save')
            self.assertEqual(self.normalize(filename), '/virtual/presets/anima/' + name + '.txt')
            self.assertEqual(safe, name)
        self.assertEqual(presets['krea2', 'GPT优化指令词']['system'], 'Krea GPT')
        self.assertEqual(presets['moss', 'GPT优化指令词']['system'], 'Moss GPT')

    def test_anima_archives_and_builtin_deletion_are_protected(self):
        resolve = self.namespace['_preset_path']
        for name in self.namespace['ANIMA_PRESET_ALIASES']:
            for action in ('save', 'delete'):
                with self.subTest(name=name, action=action), self.assertRaisesRegex(ValueError, '原文件保留'):
                    resolve(name, 'anima', action=action)
        for name in self.namespace['ANIMA_BUILTIN_PRESETS']:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, '不能删除'):
                resolve(name, 'anima', action='delete')
        with patch('os.path.isfile', return_value=False):
            self.assertEqual(self.normalize(resolve('自定义', 'anima', action='save')[0]), '/virtual/presets/anima/自定义.txt')
            self.assertEqual(self.normalize(resolve('自定义', 'anima', action='delete')[0]), '/virtual/presets/anima/自定义.txt')
            self.assertEqual(self.normalize(resolve('GPT优化指令词', 'krea2', action='save')[0]), '/virtual/presets/krea2/GPT优化指令词.txt')
            with self.assertRaisesRegex(ValueError, '不能删除'):
                resolve('GPT优化指令词', 'moss', action='delete')
            with self.assertRaisesRegex(ValueError, '原文件保留'):
                resolve('三视图', 'moss', action='save')

    def test_api_save_delete_select_protected_path_actions(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        for route, action in (('/api/savepreset', 'save'), ('/api/delpreset', 'delete')):
            branches = [node for node in ast.walk(tree) if isinstance(node, ast.If) and
                        isinstance(node.test, ast.Compare) and any(isinstance(value, ast.Constant) and value.value == route for value in node.test.comparators)]
            self.assertEqual(len(branches), 1)
            calls = [node for statement in branches[0].body for node in ast.walk(statement) if
                     isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_preset_path']
            self.assertEqual(len(calls), 1)
            self.assertEqual({kw.arg: ast.literal_eval(kw.value) for kw in calls[0].keywords}, {'action': action})

    def test_moss_override_does_not_replace_krea(self):
        self.files['/virtual/presets/moss/人物.txt'] = 'Moss本地覆盖'
        presets = self.load()
        self.assertEqual(presets['moss', '人物']['system'], 'Moss本地覆盖')
        self.assertFalse(presets['moss', '人物'].get('builtin', False))
        self.assertEqual(presets['krea2', '人物']['system'], '三视图中文')

    def test_moss_save_path_is_isolated(self):
        with patch('os.path.isfile', side_effect=lambda filename: self.normalize(filename) in self.files):
            filename, name = self.namespace['_preset_path']('通用提示词优化', 'moss')
            old_filename, old_name = self.namespace['_preset_path']('通用提示词优化', 'krea2')
        self.assertEqual(self.normalize(filename), '/virtual/presets/moss/通用提示词优化.txt')
        self.assertEqual(name, '通用提示词优化')
        self.assertEqual(self.normalize(old_filename), '/virtual/presets/通用提示词优化.txt')
        self.assertEqual(old_name, name)

    def test_explicit_and_inferred_models(self):
        resolve = self.namespace['_preset_model']
        self.assertEqual(resolve('Moss新预设'), 'moss')
        self.assertEqual(resolve('Anima新预设'), 'anima')
        self.assertEqual(resolve('人物'), 'krea2')
        self.assertEqual(resolve('Krea2人物', 'moss'), 'moss')
        with self.assertRaises(ValueError):
            resolve('人物', 'unknown')


if __name__ == '__main__':
    unittest.main()
