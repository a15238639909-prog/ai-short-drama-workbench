"""Retained, isolated long-comic fixtures; no model, service or media process runs."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

os.environ['V41_NO_MODEL'] = '1'

from PIL import Image
from cores import long_comic_core as comic, runtime_core, runtime_reset, store
from models import anima_client, gpu_manager, moss_client, qwen_client


def book(count):
    return dict(premise='两位成年修复师寻找旧钟的零件。', ending='旧钟修好，双方完成合作。',
        characters=[dict(id='c01', name='阿青', appearance='短发成年修复师', clothing='蓝色外套', personality='细心'),
                    dict(id='c02', name='阿白', appearance='长发成年修复师', clothing='绿色工作服', personality='沉稳')],
        chapters=[dict(number=number, title='修复章节%d' % number, summary='寻找并修好第%d处零件。' % number,
                       ending_state='第%d处零件修好。' % number) for number in range(1, count + 1)])


def chapter(payload):
    pages = []
    for number in range(1, payload['pages_per_chapter'] + 1):
        pages.append(dict(number=number, summary='阿青检查旧钟的第%d处零件。' % number,
            scene='钟表修复室，木桌与窗光', character_ids=['c01'],
            wardrobe={'c01': '外套已经脱下，延续已知穿戴'} if number == 1 and payload['chapter']['number'] == 1 else {},
            opening_state='承接已保存的旧钟状态。', ending_state='第%d页零件已检查。' % number,
            panels=[dict(number=1, description='阿青站在木桌旁仔细检查旧钟，手指轻触表壳。',
                         dialogue=[dict(speaker='c01', text='找到了。')], narration='午后。')]))
    return dict(ending_state='旧钟暂放木桌，阿青的外套已脱下。', pages=pages)


class LongComicCoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='long_comic_retained_'))
        self.image = self.folder / 'mock_moss.png'
        with Image.new('RGB', (768, 1360), 'lightblue') as image:
            image.save(self.image)
        self.queries, self.images = [], []
        self.image_hook = None
        self.query_hook = None
        self.addCleanup(runtime_reset.bind_thread, None)
        runtime_reset.bind_thread(None)
        patches = [patch.object(comic, 'ROOT', self.folder),
            patch.object(comic, 'DATA_ROOT', self.folder / 'data/long_comics'),
            patch.object(comic, 'OUTPUT_ROOT', self.folder / 'outputs/long_comics'),
            patch.object(comic, '_RUN_LOCK', threading.Lock()), patch.object(comic, '_THREADS', {}),
            patch.object(runtime_reset, 'STATE', self.folder / 'cache/runtime_reset.json'),
            patch.object(gpu_manager, 'GPU_LOCK', threading.RLock()),
            patch.object(gpu_manager, 'release'), patch.object(moss_client, 'local_guard'),
            patch.object(moss_client, '_resource_guard'), patch.object(anima_client, 'idle_guard'),
            patch.object(runtime_core, 'list_tasks', return_value=[]),
            patch.dict(comic.sys.modules, {'api.saga_api': SimpleNamespace(_JOBS={}),
                'api.timeline': SimpleNamespace(_JOBS={}), 'server': SimpleNamespace(_APP=None),
                'app': SimpleNamespace()}),
            patch.object(qwen_client, 'chat', side_effect=self.query),
            patch.object(moss_client, 'generate', side_effect=self.generate),
            patch.object(Path, 'unlink', side_effect=AssertionError('no permanent deletion')),
            patch('shutil.rmtree', side_effect=AssertionError('no permanent deletion'))]
        for active in patches:
            active.start()
            self.addCleanup(active.stop)

    def query(self, system, user, **options):
        payload = json.loads(user)
        self.queries.append((system, copy.deepcopy(payload), copy.deepcopy(options)))
        value = book(payload['chapter_count']) if system == comic.BOOK_SYSTEM else chapter(payload)
        if self.query_hook:
            value = self.query_hook(system, payload, value)
        return json.dumps(value, ensure_ascii=False)

    def generate(self, prompt, **options):
        self.images.append(dict(prompt=prompt, **options))
        if self.image_hook:
            self.image_hook(len(self.images))
        return dict(output_path=str(self.image), seed=options['seed'], prompt_id='mock_%d' % len(self.images))

    def create(self, **changes):
        project = comic.create_project(dict(title='旧钟修复记', story='两位成年修复师合作修复一座旧钟。',
            chapter_count=2, pages_per_chapter=2, **changes))
        project.pop('continuity_version', None)
        comic._save(project)
        return project

    def run_job(self, project_id, action='generate', chapter_number=1, page=None):
        project = comic.start_job(project_id, action, chapter=chapter_number, page=page)
        thread = comic._THREADS.get(project['job']['id'])
        if thread:
            thread.join(timeout=8)
            self.assertFalse(thread.is_alive(), 'mock worker did not finish')
        return comic.get_project(project_id)

    def test_create_update_archive_validation_and_separate_roots(self):
        project = self.create()
        self.assertEqual((project['model'], project['base_w'], project['base_h']), ('moss', 768, 1360))
        self.assertTrue((comic.DATA_ROOT / project['id'] / 'project.json').is_file())
        self.assertEqual(comic.list_projects()[0]['id'], project['id'])
        updated = comic.update_project(project['id'], dict(title='新标题', characters='阿青：短发成年修复师'))
        self.assertEqual(updated['characters_text'], '阿青：短发成年修复师')
        self.assertEqual(updated['characters'], [])
        comic.update_project(project['id'], dict(archived=True))
        with self.assertRaisesRegex(ValueError, '归档'):
            comic.start_job(project['id'], 'plan')
        for changes in ({'chapter_count': 21}, {'pages_per_chapter': 13}, {'chapter_count': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                comic.create_project(dict(title='无效', story='普通事件', **changes))
        for identity in ('../outside', 'LC_../../outside', ''):
            with self.assertRaises(ValueError):
                comic.get_project(identity)
        self.assertEqual(self.images, [])

    def test_full_chapter_freezes_page_parameters_versions_and_exports(self):
        project = self.create()
        final = self.run_job(project['id'])
        self.assertEqual(final['job']['status'], 'completed')
        self.assertEqual(len(self.queries), 2)
        self.assertEqual(len(self.images), 2)
        self.assertTrue(all(row[2]['check_context'] for row in self.queries))
        pages = final['chapters'][0]['pages']
        for page, generated in zip(pages, self.images):
            version = page['versions'][0]
            self.assertEqual((version['prompt'], version['seed']), (generated['prompt'], generated['seed']))
            self.assertEqual(version['status'], 'completed')
            self.assertEqual((generated['base_w'], generated['base_h']), (768, 1360))
            self.assertEqual(comic._sha(Path(version['path'])), version['sha256'])
            self.assertTrue(Path(version['raw_path']).is_file())
            self.assertEqual(Path(version['path']).read_bytes(), self.image.read_bytes())
            self.assertIn('阿青的气泡逐字写“找到了。”', version['prompt'])
            self.assertNotIn('长发成年修复师', generated['prompt'])
            self.assertIn('外套已经脱下', generated['prompt'])
        archive = comic.export_project(project['id'], chapter=1, kind='zip')
        with zipfile.ZipFile(archive['path']) as zipper:
            self.assertEqual(zipper.namelist(), ['chapter_001/page_001.png', 'chapter_001/page_002.png'])
            self.assertIsNone(zipper.testzip())
        pdf = comic.export_project(project['id'], chapter=1, kind='pdf')
        self.assertTrue(Path(pdf['path']).read_bytes().startswith(b'%PDF'))
        self.assertEqual(pdf['pages'], 2)
        self.assertEqual(len(list((comic.DATA_ROOT / project['id'] / 'audit').glob('*.json'))), 2)
        with self.assertRaisesRegex(ValueError, '规划'):
            comic.update_project(project['id'], dict(style='另一个画风'))
        with self.assertRaisesRegex(ValueError, '完成'):
            comic.export_project(project['id'])

    def test_pause_finishes_current_page_resume_skips_valid_output(self):
        project = self.create()
        self.image_hook = lambda count: comic.pause_job(project['id']) if count == 1 else None
        paused = self.run_job(project['id'])
        self.assertEqual(paused['job']['status'], 'paused')
        self.assertEqual(len(self.images), 1)
        first = copy.deepcopy(paused['chapters'][0]['pages'][0])
        self.image_hook = None
        resumed = self.run_job(project['id'])
        self.assertEqual(resumed['job']['status'], 'completed')
        self.assertEqual(resumed['chapters'][0]['pages'][0], first)
        self.assertEqual(len(self.images), 2)
        self.assertEqual(len(self.queries), 2)

    def test_redo_keeps_previous_images_prompt_and_seed_as_immutable_version(self):
        project = self.create()
        complete = self.run_job(project['id'])
        old = copy.deepcopy(complete['chapters'][0]['pages'][0]['versions'][0])
        old_bytes = Path(old['path']).read_bytes()
        comic.update_project(project['id'], dict(chapter=1, page=1, prompt='本页新版正向描述，阿青在木桌旁检查表壳。'))
        redone = self.run_job(project['id'], 'redo_page', page=1)
        page = redone['chapters'][0]['pages'][0]
        self.assertEqual(redone['job']['status'], 'completed')
        self.assertEqual(page['versions'][0], old)
        self.assertEqual(Path(old['path']).read_bytes(), old_bytes)
        self.assertEqual(page['current_version'], 2)
        self.assertNotEqual(page['versions'][1]['seed'], old['seed'])
        self.assertIn('新版正向描述', page['versions'][1]['prompt'])
        self.assertEqual(len(self.images), 3)

    def test_failed_page_resume_does_not_replan_or_overwrite_success(self):
        project = self.create()

        def fail_second(count):
            if count == 2:
                raise RuntimeError('mock image failure')

        self.image_hook = fail_second
        failed = self.run_job(project['id'])
        self.assertEqual(failed['job']['status'], 'partial')
        self.assertEqual(failed['chapters'][0]['pages'][1]['versions'][0]['status'], 'failed')
        old = copy.deepcopy(failed['chapters'][0]['pages'][0])
        self.image_hook = None
        resumed = self.run_job(project['id'])
        self.assertEqual(resumed['job']['status'], 'completed')
        self.assertEqual(resumed['chapters'][0]['pages'][0], old)
        self.assertEqual(len(resumed['chapters'][0]['pages'][1]['versions']), 2)
        self.assertEqual(len(self.queries), 2)

    def test_panel_edits_recompile_for_explicit_redo_and_keep_old_image(self):
        project = self.create()
        complete = self.run_job(project['id'])
        old = copy.deepcopy(complete['chapters'][0]['pages'][0]['versions'][0])
        panels = copy.deepcopy(complete['chapters'][0]['pages'][0]['panels'])
        panels[0]['dialogue'][0]['text'] = '这次看清了。'
        changed = comic.update_project(project['id'], dict(chapter=1, page=1, panels=panels, summary='修改对白。'))
        page = changed['chapters'][0]['pages'][0]
        self.assertTrue(page['needs_redo'])
        self.assertTrue(page['prompt_dirty'])
        self.assertEqual(page['prompt'], '')
        self.assertEqual(page['versions'][0], old)
        skipped = self.run_job(project['id'])
        self.assertTrue(skipped['chapters'][0]['pages'][0]['needs_redo'])
        self.assertEqual(len(self.images), 2)
        redone = self.run_job(project['id'], 'redo_page', page=1)
        page = redone['chapters'][0]['pages'][0]
        self.assertFalse(page['needs_redo'])
        self.assertIn('这次看清了。', page['versions'][1]['prompt'])
        self.assertEqual(page['versions'][0], old)

    def test_cross_chapter_passes_saved_narrative_state_and_wardrobe(self):
        project = self.create()
        first = self.run_job(project['id'])
        final = self.run_job(project['id'], 'plan_chapter', chapter_number=2)
        self.assertEqual(final['job']['status'], 'completed')
        second_payload = self.queries[-1][1]
        self.assertEqual(second_payload['previous_chapter_state'], first['chapters'][0]['ending_state'])
        self.assertIn('not visual observation', second_payload['state_evidence'])
        self.assertIn('外套已经脱下', final['chapters'][1]['pages'][0]['prompt'])
        self.assertNotIn('本页穿戴：蓝色外套', final['chapters'][1]['pages'][0]['prompt'])

    def test_chapter_two_does_not_silently_generate_previous_chapter(self):
        project = self.create()
        result = self.run_job(project['id'], chapter_number=2)
        self.assertEqual(result['job']['status'], 'failed')
        self.assertIn('上一章', result['job']['error'])
        self.assertEqual(len(self.queries), 1)
        self.assertEqual(self.images, [])

    def test_restart_marks_unfinished_job_interrupted_without_model_calls(self):
        project = self.create()
        raw = comic._load(project['id'])
        raw['job'] = dict(id='orphan', status='running', action='generate')
        comic._save(raw)
        result = comic.get_project(project['id'])
        self.assertEqual(result['job']['status'], 'interrupted')
        self.assertEqual(self.images, [])
        self.assertEqual(self.queries, [])

    def test_clear_during_model_result_prevents_adoption_and_next_dispatch(self):
        project = self.create()

        def clear(count):
            if count == 1:
                comic.cancel_all_jobs()
                epoch = runtime_reset.begin()
                runtime_reset.finish(epoch)

        self.image_hook = clear
        result = self.run_job(project['id'])
        self.assertEqual(result['job']['status'], 'cancelled')
        self.assertEqual(len(self.images), 1)
        self.assertIsNone(result['chapters'][0]['pages'][0]['current_version'])
        self.assertEqual(result['chapters'][0]['pages'][1]['versions'], [])

    def test_busy_or_unknown_external_resource_fails_before_any_model(self):
        project = self.create()
        with patch.object(moss_client, '_resource_guard', side_effect=RuntimeError('已有任务或无法确认空闲')):
            result = self.run_job(project['id'])
        self.assertEqual(result['job']['status'], 'failed')
        self.assertIn('空闲', result['job']['error'])
        self.assertEqual(self.queries, [])
        self.assertEqual(self.images, [])

    def test_concurrent_project_or_book_job_is_rejected_without_dispatch(self):
        first, second = self.create(), self.create()
        entered, released = threading.Event(), threading.Event()

        def hold(count):
            if count == 1:
                entered.set()
                released.wait(timeout=5)

        self.image_hook = hold
        job = comic.start_job(first['id'], 'generate')
        thread = comic._THREADS[job['job']['id']]
        try:
            self.assertTrue(entered.wait(timeout=5))
            with self.assertRaises(comic.BusyError):
                comic.start_job(first['id'], 'generate')
            with self.assertRaises(comic.BusyError):
                comic.start_job(second['id'], 'plan')
            with self.assertRaises(comic.BusyError):
                comic.update_project(first['id'], dict(title='不可并发修改'))
        finally:
            released.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(comic.get_project(first['id'])['job']['status'], 'completed')

    def test_invalid_cast_or_page_count_stops_before_image_generation(self):
        for problem in ('cast', 'count'):
            project = self.create()

            def invalid(system, payload, value):
                if system == comic.CHAPTER_SYSTEM:
                    if problem == 'cast':
                        value['pages'][0]['character_ids'].append('unknown')
                    else:
                        value['pages'].pop()
                return value

            self.query_hook = invalid
            result = self.run_job(project['id'])
            self.assertEqual(result['job']['status'], 'failed')
            self.assertEqual(result['chapters'][0]['pages'], [])
        self.assertEqual(self.images, [])

    def test_edited_or_missing_output_is_not_trusted_for_resume_or_export(self):
        project = self.create()
        complete = self.run_job(project['id'])
        path = Path(complete['chapters'][0]['pages'][0]['versions'][0]['path'])
        path.write_bytes(b'mocked external file damage')
        with self.assertRaisesRegex(ValueError, '校验'):
            comic.export_project(project['id'], chapter=1)
        recovered = self.run_job(project['id'])
        self.assertEqual(recovered['job']['status'], 'completed')
        self.assertEqual(len(self.images), 3)
        self.assertEqual(path.read_bytes(), b'mocked external file damage')
        self.assertEqual(recovered['chapters'][0]['pages'][0]['current_version'], 2)

    def test_active_summary_and_cancel_current_project_stop_following_dispatch(self):
        first, second = self.create(), self.create()
        entered, released = threading.Event(), threading.Event()

        def hold(count):
            if count == 1:
                entered.set()
                released.wait(timeout=5)

        self.image_hook = hold
        started = comic.start_job(first['id'], 'generate')
        thread = comic._THREADS[started['job']['id']]
        try:
            self.assertTrue(entered.wait(timeout=5))
            active = comic.active_tasks()
            self.assertEqual(len(active), 1)
            self.assertEqual((active[0]['id'], active[0]['project_id'], active[0]['kind']),
                             (first['id'], first['id'], 'long_comic'))
            self.assertEqual((active[0]['action'], active[0]['chapter'], active[0]['total']), ('generate', 1, 2))
            comic.cancel_job(second['id'])
            self.assertEqual(len(comic.active_tasks()), 1)
            cancelled = comic.cancel_job(first['id'])
            self.assertTrue(cancelled['job']['cancel_requested'])
            self.assertEqual(comic.active_tasks(), [])
        finally:
            released.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        final = comic.get_project(first['id'])
        self.assertEqual(final['job']['status'], 'cancelled')
        self.assertEqual(len(self.images), 1)
        self.assertIsNone(final['chapters'][0]['pages'][0]['current_version'])
        self.assertEqual(final['chapters'][0]['pages'][1]['versions'], [])

    def test_runtime_task_admission_and_midjob_guard_respect_paused_tools(self):
        project = self.create()
        for status in ('running', 'queued', 'loading'):
            with self.subTest(status=status), patch.object(runtime_core, 'list_tasks', return_value=[
                    dict(id='other', kind='video_formal', status=status)]):
                with self.assertRaises(comic.BusyError):
                    comic.start_job(project['id'], 'plan')
        with patch.object(runtime_core, 'list_tasks', return_value=[
                dict(id='old', kind='video_formal', status='paused', running=True),
                dict(id=project['id'], kind='long_comic', status='running')]):
            planned = self.run_job(project['id'], 'plan')
        self.assertEqual(planned['job']['status'], 'completed')
        self.queries.clear()

        def became_busy(system, payload, value):
            runtime_core.list_tasks.return_value = [dict(kind='image', status='running')]
            return value

        self.query_hook = became_busy
        result = self.run_job(project['id'])
        self.assertEqual(result['job']['status'], 'failed')
        self.assertEqual(len(self.queries), 1)
        self.assertEqual(self.images, [])
        self.assertIn('其他工具', result['job']['error'])

    def test_loaded_saga_and_legacy_jobs_are_checked_without_importing_tools(self):
        project = self.create()
        saga = comic.sys.modules['api.saga_api']
        saga._JOBS = {'other_story': dict(running=True, status='running')}
        with self.assertRaisesRegex(comic.BusyError, '主流程'):
            comic.start_job(project['id'], 'plan')
        saga._JOBS['other_story']['status'] = 'paused'
        app = comic.sys.modules['app']
        app.AUTO_JOB = dict(status='running')
        with self.assertRaisesRegex(comic.BusyError, '内嵌'):
            comic.start_job(project['id'], 'plan')
        app.AUTO_JOB['status'] = 'paused'
        app.OPS = {'draw': dict(active=True)}
        with self.assertRaisesRegex(comic.BusyError, '内嵌'):
            comic.start_job(project['id'], 'plan')
        app.OPS['draw']['active'] = False
        app.ACTIVITY = dict(active=False)
        result = self.run_job(project['id'], 'plan')
        self.assertEqual(result['job']['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
