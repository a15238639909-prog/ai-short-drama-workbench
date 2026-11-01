"""Route adapters and shared cancellation checks without model calls."""
import ast
import unittest
from pathlib import Path
from unittest.mock import patch

import api
from api import long_comic_api as endpoints
from cores import long_comic_core as comic

IDENTITY = 'LC_123456abcdef'
BASE = '/api/long-comic/project/' + IDENTITY


class LongComicApiTests(unittest.TestCase):
    def test_list_create_get_and_save_return_project_contract(self):
        project = {'id': IDENTITY, 'title': '测试漫画'}
        with patch.object(comic, 'list_projects', return_value=[project]):
            self.assertEqual(endpoints.list_projects(None, '', {})['data']['projects'], [project])
        with patch.object(comic, 'create_project', return_value=project) as create:
            payload = {'story': '两位成年人修复一座钟楼'}
            self.assertEqual(endpoints.create_project(None, '', payload)['data']['project'], project)
            create.assert_called_once_with(payload)
        with patch.object(comic, 'get_project', return_value=project) as get:
            self.assertEqual(endpoints.get_project(None, BASE, {})['data']['project'], project)
            get.assert_called_once_with(IDENTITY)
        with patch.object(comic, 'update_project', return_value=project) as save:
            self.assertTrue(endpoints.save_project(None, BASE + '/save', {'title': '新标题'})['ok'])
            save.assert_called_once_with(IDENTITY, {'title': '新标题'})

    def test_run_and_export_preserve_target_and_options(self):
        with patch.object(comic, 'start_job', return_value={'id': IDENTITY}) as start:
            response = endpoints.run_project(None, BASE + '/run', {'action': 'redo_page', 'chapter': 2, 'page': 3})
            self.assertTrue(response['ok'])
            start.assert_called_once_with(IDENTITY, 'redo_page', chapter=2, page=3)
        with patch.object(comic, 'export_project', return_value={'url': '/files/outputs/comic.zip'}) as export:
            response = endpoints.export_project(None, BASE + '/export', {'chapter': 2, 'kind': 'pdf'})
            self.assertEqual(response['data']['url'], '/files/outputs/comic.zip')
            export.assert_called_once_with(IDENTITY, chapter=2, kind='pdf')

    def test_sources_are_read_only_and_preserve_source_identity(self):
        sources = [{'story_id': 'STORY_123', 'title': '测试来源', 'character_count': 1}]
        characters = [{'character_id': 'CHAR_abc', 'name': '甲', 'appearance': '短发', 'clothing': '蓝外套'}]
        with patch.object(comic, 'list_character_sources', return_value=sources) as listing:
            self.assertEqual(endpoints.list_sources(None, '/api/long-comic/sources', {})['data']['sources'], sources)
            listing.assert_called_once_with()
        with patch.object(comic, 'get_source_characters', return_value=characters) as read:
            response = endpoints.source_characters(None, '/api/long-comic/sources/STORY_123/characters', {})
            self.assertEqual(response['data']['characters'], characters)
            read.assert_called_once_with('STORY_123')

    def test_source_payload_fields_reach_core_without_rewriting(self):
        payload = {'title': '新漫画', 'source_story_id': 'STORY_123', 'source_character_ids': ['CHAR_b', 'CHAR_a']}
        with patch.object(comic, 'create_project', return_value={}) as create:
            self.assertTrue(endpoints.create_project(None, '', payload)['ok'])
            create.assert_called_once_with(payload)
        with patch.object(comic, 'update_project', return_value={}) as update:
            self.assertTrue(endpoints.save_project(None, BASE + '/save', payload)['ok'])
            update.assert_called_once_with(IDENTITY, payload)

    def test_source_validation_errors_are_visible_without_model_dispatch(self):
        with patch.object(comic, 'get_source_characters') as read:
            for identity in ('..', '%2e%2e%2fprivate', '%5cprivate', ''):
                with self.subTest(identity=identity):
                    result = endpoints.source_characters(None, '/api/long-comic/sources/' + identity + '/characters', {})
                    self.assertEqual(result['_status'], 400)
            read.assert_not_called()
        with patch.object(comic, 'get_source_characters', side_effect=ValueError('来源项目不可用')):
            self.assertEqual(endpoints.source_characters(None, '/api/long-comic/sources/STORY_123/characters', {})['_status'], 400)

    def test_taskbar_pause_cancel_and_resume_target_same_project(self):
        for action, method in (('pause', 'pause_job'), ('cancel', 'cancel_job')):
            with self.subTest(action=action), patch.object(comic, method, return_value={}) as operate:
                self.assertTrue(endpoints.task_action(None, BASE + '/action', {'action': action})['ok'])
                operate.assert_called_once_with(IDENTITY)
        project = {'job': {'status': 'paused', 'action': 'redo_page', 'chapter': 2, 'page': 4}}
        with patch.object(comic, 'get_project', return_value=project), patch.object(comic, 'start_job', return_value={}) as start:
            self.assertTrue(endpoints.task_action(None, BASE + '/action', {'action': 'resume'})['ok'])
            start.assert_called_once_with(IDENTITY, 'redo_page', chapter=2, page=4)

    def test_bad_identity_or_body_never_calls_core_create_or_run(self):
        with patch.object(comic, 'create_project') as create:
            self.assertEqual(endpoints.create_project(None, '', ['bad'])['_status'], 400)
            create.assert_not_called()
        with patch.object(comic, 'start_job') as start:
            response = endpoints.run_project(None, '/api/long-comic/project/../../bad/run', {'action': 'generate'})
            self.assertEqual(response['_status'], 400)
            start.assert_not_called()
        self.assertEqual(endpoints.task_action(None, BASE + '/action', {'action': 'delete'})['_status'], 400)

    def test_errors_are_visible_and_busy_is_conflict(self):
        for error, status in ((comic.BusyError('模型忙'), 409), (ValueError('页码无效'), 400), (OSError('磁盘写入失败'), 500)):
            with self.subTest(status=status), patch.object(comic, 'start_job', side_effect=error):
                response = endpoints.run_project(None, BASE + '/run', {'action': 'generate'})
                self.assertFalse(response['ok'])
                self.assertEqual(response['_status'], status)

    def test_route_shapes_and_shared_runtime_clear_hook(self):
        for path in (BASE + '/save', BASE + '/run', BASE + '/pause', BASE + '/action', BASE + '/export'):
            self.assertTrue(any(api._match(rule, path) for rule, handler in api.POST_ROUTES if handler.__module__ == endpoints.__name__), path)
        for path in ('/api/long-comic/sources', '/api/long-comic/sources/STORY_123/characters'):
            matching = [handler for rule, handler in api.GET_ROUTES if handler.__module__ == endpoints.__name__ and api._match(rule, path)]
            self.assertEqual(len(matching), 1, path)
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / 'api/gpu_api.py').read_text(encoding='utf-8'))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        self.assertTrue(any(isinstance(node.func.value, ast.Name) and node.func.value.id == 'long_comic_core' and node.func.attr == 'cancel_all_jobs' for node in calls))
        source = (root / 'server.py').read_text(encoding='utf-8')
        self.assertIn('active.extend(long_comic_core.active_tasks())', source)


if __name__ == '__main__':
    unittest.main()
