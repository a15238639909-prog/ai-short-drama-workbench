"""Retained isolated fixtures; models are mocked and no files are deleted."""
import base64
import copy
import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops
from cores import long_comic_core as comic, long_comic_edit as editing, runtime_core, runtime_reset
from models import anima_client, gpu_manager, moss_client, qwen_client, qwen_image21_client


def image_data(color='orange', image_format='PNG'):
    buffer = io.BytesIO()
    Image.new('RGB', (24, 24), color).save(buffer, format=image_format)
    mime = {'PNG': 'png', 'JPEG': 'jpeg', 'WEBP': 'webp'}[image_format]
    return 'data:image/%s;base64,%s' % (mime, base64.b64encode(buffer.getvalue()).decode('ascii'))


class LongComicEditTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='long_comic_edit_retained_'))
        self.model_calls = []
        self.model_hook = None
        runtime_reset.bind_thread(None)
        self.addCleanup(runtime_reset.bind_thread, None)
        for active in (
            patch.object(comic, 'ROOT', self.folder),
            patch.object(comic, 'DATA_ROOT', self.folder / 'data/long_comics'),
            patch.object(comic, 'OUTPUT_ROOT', self.folder / 'outputs/long_comics'),
            patch.object(comic, '_RUN_LOCK', threading.Lock()), patch.object(comic, '_THREADS', {}),
            patch.object(runtime_reset, 'STATE', self.folder / 'cache/runtime_reset.json'),
            patch.object(gpu_manager, 'GPU_LOCK', threading.RLock()), patch.object(gpu_manager, 'release'),
            patch.object(moss_client, 'local_guard'), patch.object(moss_client, '_resource_guard'),
            patch.object(anima_client, 'idle_guard'), patch.object(runtime_core, 'list_tasks', return_value=[]),
            patch.dict(comic.sys.modules, {'api.saga_api': SimpleNamespace(_JOBS={}),
                'api.timeline': SimpleNamespace(_JOBS={}), 'server': SimpleNamespace(_APP=None), 'app': SimpleNamespace()}),
            patch.object(qwen_image21_client, 'edit', side_effect=self.fake_edit),
            patch.object(moss_client, 'generate', side_effect=AssertionError('editing must not call Moss')),
            patch.object(qwen_client, 'chat', side_effect=AssertionError('editing must not replan the story')),
            patch.object(Path, 'unlink', side_effect=AssertionError('no permanent deletion')),
            patch('shutil.rmtree', side_effect=AssertionError('no permanent deletion')),
        ):
            active.start()
            self.addCleanup(active.stop)
        self.project = comic.create_project(dict(title='人物修正验收', story='两位成年修复师在工作室修理旧钟。',
            chapter_count=1, pages_per_chapter=1, characters=[
                dict(id='c01', name='阿青', appearance='成年短发修复师', clothing='蓝色外套', personality='细心'),
                dict(id='c02', name='阿白', appearance='成年长发修复师', clothing='绿色外套', personality='沉稳')]))
        self.project.update(outline=dict(premise='修理旧钟', ending='旧钟修好'), chapters=[dict(
            number=1, title='工作室', summary='修理旧钟', status='completed', opening_state={}, ending_state={},
            pages=[dict(number=1, summary='二人检查旧钟', scene='工作室', character_ids=['c01', 'c02'],
                panels=[dict(number=1, character_ids=['c01', 'c02'], description='二人站在木桌旁',
                             dialogue=[], narration='', state_changes=[])],
                prompt='原始Moss漫画提示词', prompt_dirty=False, prompt_edited=False, needs_redo=False,
                status='completed', versions=[], current_version=1)])])
        self.identity = self.project['id']
        self.source = self.make_version(1, 'lightblue')
        comic._save(self.project)

    def page(self, project=None):
        return (project or comic.get_project(self.identity))['chapters'][0]['pages'][0]

    def make_version(self, number, color, *, signature=False):
        directory = comic.OUTPUT_ROOT / self.identity / 'chapter_001/page_001'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ('v%03d.png' % number)
        image = Image.new('RGB', (100, 80), color)
        image.putpixel((0, 0), (1, 2, 3))
        image.save(path)
        row = dict(version=number, status='completed', model='moss', prompt=self.page(self.project)['prompt'],
                   path=str(path), url=comic._url(path), sha256=comic._sha(path))
        if signature:
            row['content_signature'] = editing.content_signature(self.project, self.page(self.project))
        self.page(self.project)['versions'].append(row)
        return path

    def bind(self, identity='c01', color='orange'):
        return comic.bind_reference(self.identity, identity, image_data(color))

    def options(self, **changes):
        return dict(base_version=1, region=[24, 20, 60, 56], character_ids=['c01'], prompt='只修正脸部身份',
                    seed=42, **changes)

    def run_edit(self, options=None):
        project = comic.start_job(self.identity, 'edit_page', chapter=1, page=1,
                                  edit=options or self.options())
        thread = comic._THREADS.get(project['job']['id'])
        if thread:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), 'mock editing worker did not finish')
        return comic.get_project(self.identity)

    def fake_edit(self, image_path, prompt, references=(), seed=None, resolution=0):
        with Image.open(image_path) as image:
            size = image.size
        self.model_calls.append(dict(path=str(image_path), prompt=prompt, references=list(references),
                                     seed=seed, resolution=resolution, size=size))
        if self.model_hook:
            self.model_hook()
        path = self.folder / ('mock_qwen_%d.png' % len(self.model_calls))
        Image.new('RGBA', size, 'green').save(path)
        return dict(output_path=str(path), prompt_id='mock-qwen-%d' % len(self.model_calls), seed=seed)

    def test_reference_binding_and_replacement_preserve_old_images_and_story(self):
        before = comic.get_project(self.identity)
        first = self.bind()
        first_ref = first['character_references']['c01']
        original = Path(first_ref['path']).read_bytes()
        second = self.bind(color='purple')
        self.assertNotEqual(first_ref['id'], second['character_references']['c01']['id'])
        self.assertEqual(Path(first_ref['path']).read_bytes(), original)
        self.assertEqual(second['characters'], before['characters'])
        self.assertEqual(second['chapters'], before['chapters'])
        self.assertEqual(second['story'], before['story'])
        self.assertEqual(self.model_calls, [])

    def test_supported_reference_formats_are_normalized_to_png(self):
        for image_format in ('PNG', 'JPEG', 'WEBP'):
            with self.subTest(image_format=image_format):
                project = comic.bind_reference(self.identity, 'c01', image_data(image_format=image_format))
                with Image.open(project['character_references']['c01']['path']) as image:
                    self.assertEqual(image.format, 'PNG')
                    self.assertEqual(image.size, (24, 24))

    def test_invalid_reference_format_size_pixels_and_character_are_rejected(self):
        for data in ('file:///private.png', 'data:image/png;base64,???',
                     'data:image/svg+xml;base64,PHN2Zz4=', image_data().replace('image/png', 'image/jpeg')):
            with self.subTest(data=data[:40]), self.assertRaises(ValueError):
                comic.bind_reference(self.identity, 'c01', data)
        with patch.object(editing, 'MAX_REFERENCE_BYTES', 20), self.assertRaisesRegex(ValueError, '8MiB'):
            self.bind()
        with patch.object(editing, 'MAX_IMAGE_PIXELS', 1), self.assertRaisesRegex(ValueError, '像素'):
            self.bind()
        with self.assertRaisesRegex(ValueError, '人物不属于'):
            self.bind('another-project-character')
        self.assertNotIn('character_references', comic.get_project(self.identity))

    def test_edit_saves_candidate_preserves_outside_pixels_and_requires_adoption(self):
        self.bind()
        original_bytes = self.source.read_bytes()
        final = self.run_edit()
        self.assertEqual(final['job']['status'], 'completed', final['job'])
        page = self.page(final)
        self.assertEqual(page['current_version'], 1)
        self.assertEqual(len(page['versions']), 2)
        candidate = page['versions'][-1]
        self.assertTrue(candidate['candidate'])
        self.assertEqual((candidate['kind'], candidate['base_version'], candidate['outside_pixels_changed']), ('edit', 1, 0))
        self.assertEqual(candidate['prompt'], page['prompt'])
        self.assertEqual(candidate['edit_prompt'], '只修正脸部身份')
        self.assertEqual(self.source.read_bytes(), original_bytes)
        self.assertEqual(candidate['reference_snapshots'][0]['sha256'], final['character_references']['c01']['sha256'])
        with Image.open(self.source) as source, Image.open(candidate['path']) as target:
            source, target = source.convert('RGBA'), target.convert('RGBA')
            for y in range(source.height):
                for x in range(source.width):
                    if not (24 <= x < 60 and 20 <= y < 56):
                        self.assertEqual(source.getpixel((x, y)), target.getpixel((x, y)))
            self.assertNotEqual(source.getpixel((42, 38)), target.getpixel((42, 38)))
        call = self.model_calls[0]
        self.assertIn('图1', call['prompt'])
        self.assertIn('图2是人物阿青', call['prompt'])
        self.assertIn('保留图1当前衣着、持物、伤势', call['prompt'])
        self.assertEqual((call['seed'], call['resolution']), (42, 0))
        self.assertLessEqual(max(call['size']), 1024)
        self.assertEqual(len(call['references']), 1)
        adopted = comic.adopt_version(self.identity, 1, 1, candidate['version'])
        self.assertEqual(self.page(adopted)['current_version'], 2)
        restored = comic.adopt_version(self.identity, 1, 1, 1)
        self.assertEqual(self.page(restored)['current_version'], 1)
        self.assertEqual(len(self.page(restored)['versions']), 2)

    def test_multiple_references_are_frozen_in_character_order(self):
        self.bind('c01')
        self.bind('c02', 'purple')
        options = self.options()
        options['character_ids'] = ['c02', 'c01']
        final = self.run_edit(options)
        self.assertEqual(final['job']['status'], 'completed', final['job'])
        frozen = final['job']['edit']
        self.assertEqual([item['character_id'] for item in frozen['references']], ['c02', 'c01'])
        self.assertIn('图2是人物阿白', self.model_calls[0]['prompt'])
        self.assertIn('图3是人物阿青', self.model_calls[0]['prompt'])

    def test_bad_region_base_version_and_missing_references_reject_before_worker(self):
        with self.assertRaisesRegex(ValueError, '绑定参考'):
            comic.start_job(self.identity, 'edit_page', page=1, edit=self.options())
        self.bind()
        for region in ([-1, 0, 20, 20], [0, 0, 101, 20], [5, 5, 5, 10], [0.5, 0, 10, 10], [True, 0, 10, 10]):
            payload = self.options()
            payload['region'] = region
            with self.subTest(region=region), self.assertRaisesRegex(ValueError, '选区'):
                comic.start_job(self.identity, 'edit_page', page=1, edit=payload)
        payload = self.options()
        payload['base_version'] = 2
        with self.assertRaisesRegex(ValueError, '已完成版本'):
            comic.start_job(self.identity, 'edit_page', page=1, edit=payload)
        self.assertEqual(self.model_calls, [])

    def test_stale_page_or_chapter_blocks_adoption_and_edit_without_clearing_flags(self):
        self.bind()
        for flag in ('needs_redo', 'prompt_dirty', 'continuity_review_required'):
            project = comic.get_project(self.identity)
            owner = project['chapters'][0] if flag == 'continuity_review_required' else self.page(project)
            owner[flag] = True
            comic._save(project)
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                comic.adopt_version(self.identity, 1, 1, 1)
            with self.assertRaises(ValueError):
                comic.start_job(self.identity, 'edit_page', page=1, edit=self.options())
            saved = comic.get_project(self.identity)
            self.assertTrue((saved['chapters'][0] if flag == 'continuity_review_required' else self.page(saved))[flag])
            owner[flag] = False
            comic._save(project)
        self.assertEqual(self.model_calls, [])

    def test_changed_content_signature_and_legacy_prompt_are_rejected(self):
        self.make_version(2, 'pink', signature=True)
        comic._save(self.project)
        project = comic.get_project(self.identity)
        self.page(project)['summary'] = '剧情已变化'
        comic._save(project)
        with self.assertRaisesRegex(ValueError, '旧内容'):
            comic.adopt_version(self.identity, 1, 1, 2)
        self.page(project)['prompt'] = '改变后的提示词'
        comic._save(project)
        with self.assertRaisesRegex(ValueError, '旧内容'):
            comic.adopt_version(self.identity, 1, 1, 1)

    def test_cross_project_version_and_damaged_hash_are_rejected(self):
        project = comic.get_project(self.identity)
        foreign = comic.OUTPUT_ROOT / 'LC_000000000000' / 'foreign.png'
        foreign.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (10, 10), 'red').save(foreign)
        version = self.page(project)['versions'][0]
        version.update(path=str(foreign), sha256=comic._sha(foreign))
        comic._save(project)
        with self.assertRaisesRegex(ValueError, '不属于本漫画'):
            comic.adopt_version(self.identity, 1, 1, 1)
        version.update(path=str(self.source), sha256='bad hash')
        comic._save(project)
        with self.assertRaisesRegex(ValueError, '校验失败'):
            comic.adopt_version(self.identity, 1, 1, 1)

    def test_forged_or_damaged_reference_paths_are_rejected(self):
        project = self.bind()
        reference = project['character_references']['c01']
        original = dict(reference)
        reference['path'] = str(self.source)
        reference['sha256'] = comic._sha(self.source)
        comic._save(project)
        with self.assertRaisesRegex(ValueError, '参考图不属于'):
            comic.start_job(self.identity, 'edit_page', page=1, edit=self.options())
        project['character_references']['c01'] = dict(original, sha256='bad hash')
        comic._save(project)
        with self.assertRaisesRegex(ValueError, '校验失败'):
            comic.start_job(self.identity, 'edit_page', page=1, edit=self.options())

    def test_resource_busy_and_cancel_leave_original_and_adoption_unchanged(self):
        self.bind()
        original_bytes = self.source.read_bytes()
        with patch.object(comic, '_model_call', side_effect=comic.BusyError('resource busy')):
            busy = self.run_edit()
        self.assertEqual(busy['job']['status'], 'failed')
        self.assertEqual(self.model_calls, [])
        self.assertEqual(len(self.page(busy)['versions']), 1)
        self.model_hook = lambda: comic.cancel_job(self.identity)
        cancelled = self.run_edit()
        self.assertEqual(cancelled['job']['status'], 'cancelled')
        self.assertEqual(self.page(cancelled)['current_version'], 1)
        self.assertEqual(len(self.page(cancelled)['versions']), 1)
        self.assertEqual(self.source.read_bytes(), original_bytes)

    def test_busy_project_blocks_reference_and_adoption(self):
        self.bind()
        entered, release = threading.Event(), threading.Event()
        self.model_hook = lambda: (entered.set(), release.wait(3))
        started = comic.start_job(self.identity, 'edit_page', page=1, edit=self.options())
        try:
            self.assertTrue(entered.wait(3))
            with self.assertRaises(comic.BusyError):
                self.bind(color='purple')
            with self.assertRaises(comic.BusyError):
                comic.adopt_version(self.identity, 1, 1, 1)
        finally:
            release.set()
            thread = comic._THREADS.get(started['job']['id'])
            if thread:
                thread.join(5)

    def test_large_crop_and_references_are_limited_for_model_input(self):
        project = self.bind()
        Image.new('RGB', (1800, 1800), 'lightblue').save(self.source)
        self.page(project)['versions'][0]['sha256'] = comic._sha(self.source)
        comic._save(project)
        options = self.options()
        options['region'] = [100, 100, 1600, 1600]
        final = self.run_edit(options)
        self.assertEqual(final['job']['status'], 'completed', final['job'])
        self.assertLessEqual(max(self.model_calls[0]['size']), 1024)
        self.assertLessEqual(self.model_calls[0]['size'][0] * self.model_calls[0]['size'][1], 1024 ** 2)
        with Image.open(self.page(final)['versions'][-1]['path']) as image:
            self.assertEqual(image.size, (1800, 1800))

    def test_target_crop_has_resolution_floor_and_alignment_without_upscaling_references(self):
        for size in ((336, 384), (100, 80), (928, 1632), (1800, 1800), (783, 401)):
            with self.subTest(size=size):
                image = Image.new('RGBA', size, 'white')
                target = editing._fit(image, target=True)
                reference = editing._fit(image)
                self.assertGreaterEqual(max(target.size), 768)
                self.assertLessEqual(max(target.size), 1024)
                self.assertTrue(all(edge % 32 == 0 for edge in target.size))
                self.assertLessEqual(target.width * target.height, 1024 ** 2)
                self.assertLessEqual(reference.width, image.width)
                self.assertLessEqual(reference.height, image.height)
                self.assertLessEqual(max(reference.size), 1024)
                if max(size) <= 1024:
                    self.assertEqual(reference.size, size)
                self.assertEqual(image.size, size)

    def test_model_failure_preserves_known_remote_task_id(self):
        self.bind()
        with patch.object(qwen_image21_client, 'edit', side_effect=qwen_image21_client.QwenImage21Error(
                '后台状态未知', prompt_id='known-remote-task')):
            final = self.run_edit()
        self.assertEqual(final['job']['status'], 'failed')
        self.assertEqual(final['job']['model_prompt_id'], 'known-remote-task')
        self.assertEqual(len(self.page(final)['versions']), 1)


if __name__ == '__main__':
    unittest.main()
