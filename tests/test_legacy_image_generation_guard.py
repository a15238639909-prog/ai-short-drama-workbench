import ast
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class LegacyImageGenerationGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parents[1] / '_legacy_8848' / 'app.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'comfy_generate')
        cls.code = compile(ast.Module(body=[definition], type_ignores=[]), str(source), 'exec')

    def run_entry(self, guard, unload, no_model=''):
        namespace = {'os': os, '_local_model_guard': guard, 'ollama_unload': unload}
        exec(self.code, namespace)
        age_policy = types.ModuleType('cores.age_policy')
        age_policy.assert_model_request = lambda *args, **kwargs: None
        with patch.dict(sys.modules, {'cores.age_policy': age_policy}), \
             patch.dict(os.environ, {'V41_NO_MODEL': no_model}):
            namespace['comfy_generate']('成年人物穿日常服装站立')

    def test_running_game_blocks_before_model_unload(self):
        calls = []

        def guard():
            calls.append('guard')
            raise RuntimeError('PUBG_RUNNING')

        with self.assertRaisesRegex(RuntimeError, 'PUBG_RUNNING'):
            self.run_entry(guard, lambda: calls.append('unload'))
        self.assertEqual(calls, ['guard'])

    def test_every_request_checks_before_unload(self):
        calls = []

        def unload():
            calls.append('unload')
            raise RuntimeError('STOP_BEFORE_ANY_MODEL_OPERATION')

        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, 'STOP_BEFORE_ANY_MODEL_OPERATION'):
                self.run_entry(lambda: calls.append('guard'), unload)
        self.assertEqual(calls, ['guard', 'unload', 'guard', 'unload'])

    def test_zero_model_mode_rejects_before_guard_and_unload(self):
        calls = []
        with self.assertRaisesRegex(RuntimeError, 'V41_NO_MODEL'):
            self.run_entry(lambda: calls.append('guard'), lambda: calls.append('unload'), no_model='1')
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
