"""Entry-point contracts with normal fixtures; no workers, models or media."""
import copy
import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from api import saga_api, segment_api
from cores import reference_ready, saga_core
from models import h3_client
from tests import test_generation_recovery as recovery_fixture


class EntryPointContractEdgeTests(unittest.TestCase):
    def test_registered_pending_request_in_second_scene_resumes_original_scene(self):
        support = recovery_fixture.RecoveryTests(methodName='runTest')
        support.setUp()
        self.addCleanup(support.doCleanups)
        job = support.job(sid='SAFE_MULTI_SCENE_RECOVERY', phase='render')
        job['status'] = 'waiting_task'
        job['_recovery']['external_inflight'] = 'video:2:4'
        saga_api._persist_job(job)
        saga_api._JOBS.clear()
        request = dict(key='same-acknowledged-request', status='pending')
        timeline = dict(scenes=[dict(no=2, segments=[dict(no=4,
            prompt='成年修表师将修好的手表放在柜台上。', render_request=copy.deepcopy(request))])])
        current_check = segment_api.can_resume_segment
        with patch.object(saga_core, 'ep_timeline', return_value=timeline), \
             patch.object(segment_api, '_load', return_value=({}, timeline)), \
             patch.object(segment_api, 'is_segment_current', return_value=False), \
             patch.object(segment_api, 'can_resume_segment', wraps=current_check) as check, \
             patch.object(h3_client, 'recovery_receipt', return_value=dict(status='submitted')), \
             patch.object(h3_client, 'generate', side_effect=AssertionError('no real generation')) as generate, \
             patch.object(saga_api.threading, 'Thread') as thread, \
             patch.object(saga_api, '_run_registered_make', side_effect=AssertionError('no worker execution')) as worker:
            restored = saga_api.restore_generation_jobs(start_monitor=False)
        self.assertEqual(restored, ['SAFE_MULTI_SCENE_RECOVERY'])
        self.assertEqual(check.call_args.kwargs.get('scene_no'), 2)
        thread.return_value.start.assert_called_once()
        generate.assert_not_called()
        worker.assert_not_called()
        self.assertEqual(timeline['scenes'][0]['segments'][0]['render_request'], request)

    def test_single_generate_edit_preserves_original_auto_prompt_and_marks_manual(self):
        old = '<Subject 1>阿宁是<Picture 1>成年修表师。\n0—8秒：阿宁收好工具。'
        new = '<Subject 1>阿宁是<Picture 1>成年修表师。\n0—8秒：阿宁把手表放在柜台上。'
        row = dict(no=1, prompt=old, seconds=8, chars=['阿宁'], scene='修表店')
        timeline = dict(scenes=[dict(no=1, segments=[row])])
        edits = []
        def apply(sid, scene, number, fields, **kwargs):
            edits.append(copy.deepcopy(fields))
            row.update(fields)
            return row
        with ExitStack() as stack:
            stack.enter_context(patch.object(segment_api, '_ep_of', return_value=1))
            stack.enter_context(patch.object(segment_api, '_load', return_value=({}, timeline)))
            stack.enter_context(patch.object(segment_api, '_apply', side_effect=apply))
            stack.enter_context(patch.object(segment_api, '_include_scene_reference', return_value=True))
            stack.enter_context(patch.object(reference_ready, 'assert_references'))
            stack.enter_context(patch.object(segment_api, '_render_critical_problems', return_value=[]))
            stack.enter_context(patch.object(segment_api, '_RESP', side_effect=lambda body, status=200: body))
            render = stack.enter_context(patch.object(segment_api, 'render_segment', return_value=dict(video='fake-boundary-only')))
            result = segment_api.gen_segment(None, '/api/timeline/SAFE_ADULT_PROMPT/generate-segment',
                dict(ep=1, scene_no=1, seg_no=1, prompt=new))
        self.assertTrue(result['ok'])
        self.assertEqual(row['prompt'], new)
        self.assertTrue(row.get('prompt_edited'), 'direct generation must protect a user edit just like Save')
        self.assertEqual(row.get('prompt_auto'), old)
        self.assertTrue(row.get('prompt_edited_at'))
        self.assertTrue(edits)
        render.assert_called_once()


if __name__ == '__main__':
    unittest.main()
