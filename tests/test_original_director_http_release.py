"""Live HTTP smoke check on an ephemeral isolated test server, no models/jobs."""
import json
import os
os.environ['V41_NO_MODEL']='1'
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from contextlib import ExitStack

from tests.test_workbench_http_stability import WorkbenchHTTPStabilityTests as HTTP
from cores import store, story_core, workbench_story_director as release
from models import qwen_client


class OriginalDirectorHTTPReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        HTTP.setUpClass()

    @classmethod
    def tearDownClass(cls):
        HTTP.tearDownClass()

    def test_actual_http_profile_and_frontend_match_the_released_engine_without_generation(self):
        folder=Path(__file__).resolve().parent/'.retained'/('original_release_http_%d'%time.time_ns())
        folder.mkdir(parents=True,exist_ok=True)
        sid='DIRECTOR_RELEASE_HTTP'
        with ExitStack() as stack:
            stack.enter_context(patch.object(store,'DATA',folder/'data'))
            model=stack.enter_context(patch.object(qwen_client,'chat',side_effect=AssertionError('no model in release smoke')))
            story=dict(story_id=sid,title='原版导演正式接口检查',
                settings=dict(story_mode='single',style='写实摄影'),
                saga=dict(episodes=[dict(no=1,prose='成年修表师把工具放回桌面。',pictures='',timeline=dict(scenes=[]))]))
            story_core.save_story(story)
            client=HTTP()
            for route,body in [('/api/project/settings',dict(story_id=sid)),
                               ('/api/timeline/%s/get'%sid,dict(ep=1))]:
                status,_,data=client.request(route,body)
                self.assertEqual(status,200)
                value=json.loads(data)
                self.assertTrue(value['ok'])
                self.assertEqual(value['data']['director_profile'],release.profile_info())
            for path in ('pages/settings.js','pages/timeline.js'):
                status,headers,data=client.request('/'+path)
                self.assertEqual(status,200)
                self.assertIn('no-store',headers.get('Cache-Control',''))
                self.assertEqual(data,(Path(HTTP.app.WEB)/path).read_bytes())
                self.assertIn(b'original-story-zh-v1',data)
            story['settings']['video_director_profile']='legacy'
            story_core.save_story(story)
            status,_,data=client.request('/api/project/settings',dict(story_id=sid))
            self.assertEqual(status,200)
            self.assertIsNone(json.loads(data)['data']['director_profile'])
            model.assert_not_called()


if __name__=='__main__':
    unittest.main()

