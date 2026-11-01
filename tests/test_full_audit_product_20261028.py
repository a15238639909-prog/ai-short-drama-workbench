"""Product-side audit cases: normal fixtures and simulated native boundaries."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from cores import long_comic_core as comic, store
from models import gpu_manager, krea_client, krea_edit_client, moss_client
from production import album_core, novel_core
from tests import test_album_novel_batch_continue as production_fixture


class ProductAudit20261028Tests(unittest.TestCase):
    def production_support(self):
        helper = production_fixture.AlbumNovelContinueTests(methodName='runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        return helper

    def test_same_second_album_records_never_overwrite_other_story_record(self):
        helper = self.production_support()
        with patch.object(album_core.time, 'time', return_value=1793100000):
            first = helper.album(task=False)
            helper.story['story_id'] = 'SAFE_DIFFERENT_ALBUM'
            second = helper.album(task=False)
        self.assertNotEqual(first['production_id'], second['production_id'])
        retained = store.load_json(store.DATA / 'productions' / (first['production_id'] + '.json'), {})
        self.assertEqual(retained['story_id'], 'STORY_FIXTURE')

    def test_same_second_novel_records_never_overwrite_other_story_record(self):
        helper = self.production_support()
        with patch.object(novel_core.time, 'time', return_value=1793100000):
            first = helper.novel(task=False)
            helper.story['story_id'] = 'SAFE_DIFFERENT_NOVEL'
            second = helper.novel(task=False)
        self.assertNotEqual(first['production_id'], second['production_id'])
        retained = store.load_json(store.DATA / 'productions' / (first['production_id'] + '.json'), {})
        self.assertEqual(retained['story_id'], 'STORY_FIXTURE')

    def test_resuming_same_album_or_novel_keeps_its_production_record_id(self):
        for kind in ('album', 'novel'):
            with self.subTest(kind=kind):
                helper = self.production_support()
                produce = getattr(helper, kind)
                first = produce((2, ValueError('simulated middle item failure')))
                self.assertFalse(first['generation_completed'])
                second = produce()
                self.assertTrue(second['generation_completed'])
                self.assertEqual(first['production_id'], second['production_id'])
                retained = store.load_json(store.DATA / 'productions' / (second['production_id'] + '.json'), {})
                self.assertTrue(retained['generation_completed'])

    def test_warm_image_edit_checks_game_before_any_native_submission(self):
        root = Path(tempfile.mkdtemp(prefix='safe_image_edit_guard_'))
        comfy = root / 'comfy'
        source = comfy / 'ComfyUI/output/fixture.bin'
        source.parent.mkdir(parents=True)
        source.write_bytes(b'simulated image receipt')
        calls = []
        def http(method, path, *args, **kwargs):
            calls.append((method, path))
            if path == '/prompt':
                return dict(prompt_id='simulated-receipt')
            return {'simulated-receipt': dict(outputs=dict(node=dict(images=[dict(filename=source.name)])))}
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'V41_NO_MODEL': ''}))
            stack.enter_context(patch.object(krea_client, 'up', return_value=True))
            stack.enter_context(patch.object(krea_client, 'start_comfy', side_effect=AssertionError('no startup')))
            stack.enter_context(patch.object(krea_client, '_http', side_effect=http))
            stack.enter_context(patch.object(krea_edit_client, 'COMFY_DIR', str(comfy)))
            stack.enter_context(patch.object(krea_edit_client, 'OUTPUT_ROOT', root / 'copies'))
            stack.enter_context(patch.object(krea_edit_client, 'build_graph', return_value=({}, {})))
            guard = stack.enter_context(patch.object(gpu_manager, 'assert_local_run_allowed',
                side_effect=RuntimeError('PUBG正在运行：模拟保护')))
            with self.assertRaisesRegex(RuntimeError, 'PUBG'):
                krea_edit_client.edit('safe-original.png', '把成年修表师工作服改成深蓝色')
        self.assertGreaterEqual(guard.call_count, 1)
        self.assertEqual(calls, [], 'No POST may precede the game guard')

    def test_failed_image_edit_acquisition_never_releases_another_owner(self):
        with patch.dict(os.environ, {'V41_NO_MODEL': ''}), \
             patch.object(gpu_manager, 'acquire', side_effect=RuntimeError('显卡正被另一个进程使用')), \
             patch.object(gpu_manager, 'release') as release, \
             patch.object(krea_client, '_http', side_effect=AssertionError('no native submission')) as http:
            with self.assertRaisesRegex(RuntimeError, '另一个进程'):
                krea_edit_client.edit('safe-original.png', '把成年修表师工作服改成深蓝色')
        release.assert_not_called()
        http.assert_not_called()

    def test_image_edit_failure_releases_only_the_acquired_model_kind(self):
        with patch.dict(os.environ, {'V41_NO_MODEL': ''}), \
             patch.object(gpu_manager, 'acquire', return_value=True), \
             patch.object(gpu_manager, 'release') as release, \
             patch.object(krea_client, 'up', return_value=True), \
             patch.object(krea_edit_client, 'build_graph', side_effect=ValueError('simulated invalid input')):
            with self.assertRaisesRegex(ValueError, 'invalid input'):
                krea_edit_client.edit('safe-original.png', '把成年修表师工作服改成深蓝色')
        release.assert_called_once_with('krea')

    def test_long_comic_copy_retry_reuses_successful_native_result(self):
        root = Path(tempfile.mkdtemp(prefix='safe_comic_copy_retry_'))
        raw = root / 'completed-native-result.bin'
        raw.write_bytes(b'simulated completed image, not AI media')
        page = dict(number=1, seed=10, prompt='成年修表师整理木桌上的工具。',
                    versions=[], current_version=None, prompt_dirty=False)
        project = dict(id='SAFE_COMIC_COPY', base_w=768, base_h=1360, chapter_count=1,
                       chapters=[dict(number=1, pages=[page])])
        class ImageReceipt:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def verify(self): pass
        import shutil
        original_copy = shutil.copyfileobj
        copies = []
        def copy(reader, target, *args, **kwargs):
            copies.append(target.name)
            if len(copies) == 1:
                raise PermissionError('simulated temporary copy sharing violation')
            return original_copy(reader, target, *args, **kwargs)
        with ExitStack() as stack:
            stack.enter_context(patch.object(comic, 'ROOT', root))
            stack.enter_context(patch.object(comic, 'OUTPUT_ROOT', root / 'outputs'))
            stack.enter_context(patch.object(comic, '_ensure_job', return_value=project))
            stack.enter_context(patch.object(comic, '_save'))
            stack.enter_context(patch.object(comic, '_model_call', side_effect=lambda pid, jid, callback: callback()))
            generation = stack.enter_context(patch.object(moss_client, 'generate',
                return_value=dict(output_path=str(raw), prompt_id='same-native-receipt')))
            stack.enter_context(patch('PIL.Image.open', return_value=ImageReceipt()))
            stack.enter_context(patch.object(shutil, 'copyfileobj', side_effect=copy))
            with self.assertRaises(PermissionError):
                comic._render_page(project['id'], 'job', 1, 1)
            # The scheduler may have marked the interrupted adoption failed.
            for version in page['versions']:
                if version['status'] == 'generating':
                    version['status'] = 'failed'
            comic._render_page(project['id'], 'job', 1, 1)
        self.assertEqual(generation.call_count, 1, 'A copy failure must not spend another native generation')
        self.assertEqual(raw.read_bytes(), b'simulated completed image, not AI media')
        self.assertEqual(page['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
