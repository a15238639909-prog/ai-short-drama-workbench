"""Route contracts for explicit comic references, edit candidates and adoption."""
import unittest
from unittest.mock import patch

import api
from api import long_comic_api as endpoints
from cores import long_comic_core as comic

IDENTITY = 'LC_123456abcdef'
BASE = '/api/long-comic/project/' + IDENTITY


class ComicEditApiTests(unittest.TestCase):
    def test_reference_is_bound_to_the_requested_project_and_character(self):
        with patch.object(comic, 'bind_reference', return_value={'id': IDENTITY}) as bind:
            response = endpoints.bind_reference(None, BASE+'/reference',
                {'character_id': 'c01', 'image_data': 'data:image/png;base64,eA=='})
            self.assertTrue(response['ok'])
            bind.assert_called_once_with(IDENTITY, 'c01', 'data:image/png;base64,eA==')

    def test_edit_is_an_explicit_job_with_frozen_page_and_base_version(self):
        options = {'base_version': 3, 'region': [20,30,120,180],
                   'character_ids': ['c01'], 'prompt': '修正发型，保留黄外套', 'seed': 21}
        with patch.object(comic, 'start_job', return_value={'id': IDENTITY}) as start:
            response = endpoints.edit_page(None, BASE+'/edit', dict(options, chapter=2, page=4))
            self.assertTrue(response['ok'])
            start.assert_called_once_with(IDENTITY, 'edit_page', chapter=2, page=4, edit=options)

    def test_adoption_targets_only_the_explicit_page_version(self):
        with patch.object(comic, 'adopt_version', return_value={'id': IDENTITY}) as adopt:
            self.assertTrue(endpoints.adopt_version(None, BASE+'/adopt',
                {'chapter': 2, 'page': 4, 'version': 1})['ok'])
            adopt.assert_called_once_with(IDENTITY, 2, 4, 1)

    def test_malformed_bodies_and_ids_never_dispatch(self):
        calls = [(endpoints.bind_reference, 'bind_reference', '/reference'),
                 (endpoints.edit_page, 'start_job', '/edit'),
                 (endpoints.adopt_version, 'adopt_version', '/adopt')]
        for endpoint, method, suffix in calls:
            with self.subTest(method=method), patch.object(comic, method) as dispatch:
                self.assertEqual(endpoint(None, BASE+suffix, [])['_status'], 400)
                self.assertEqual(endpoint(None, '/api/long-comic/project/../private'+suffix, {})['_status'], 400)
                dispatch.assert_not_called()

    def test_invalid_or_busy_edits_return_visible_errors_without_retry(self):
        for error, status in [(ValueError('区域无效'),400),(comic.BusyError('漫画任务运行中'),409)]:
            with self.subTest(status=status), patch.object(comic,'start_job',side_effect=error) as start:
                self.assertEqual(endpoints.edit_page(None,BASE+'/edit',{})['_status'],status)
                self.assertEqual(start.call_count,1)

    def test_resume_of_an_edit_cannot_start_chapter_generation(self):
        with patch.object(comic,'get_project',return_value={'job': {'status':'failed','action':'edit_page'}}), \
             patch.object(comic,'start_job') as start:
            response=endpoints.task_action(None,BASE+'/action',{'action':'resume'})
            self.assertEqual(response['_status'],400)
            start.assert_not_called()

    def test_new_routes_have_exactly_one_registered_handler(self):
        for suffix in ('/reference','/edit','/adopt'):
            matching=[handler for rule,handler in api.POST_ROUTES
                      if handler.__module__==endpoints.__name__ and api._match(rule,BASE+suffix)]
            self.assertEqual(len(matching),1,suffix)


if __name__=='__main__':
    unittest.main()
