"""Stale browser scene saves must not discard newly accepted model receipts."""
import copy
import os
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from api import timeline


class BrowserSceneSaveIntegrityTests(unittest.TestCase):
    def test_explicit_reference_patch_keeps_other_segment_and_backend_receipt(self):
        rows = [dict(no=n, refs=[], video='saved-%d.mp4' % n,
            render_request=dict(key='original-%d' % n)) for n in (4, 5)]
        rows[1]['refs'] = [dict(n='其他成年修表师', k='character')]
        latest = dict(scenes=[dict(no=2, segments=copy.deepcopy(rows))])
        saved = []
        with patch.object(timeline, '_ep_of', return_value=2), \
             patch.object(timeline, '_load', return_value=({}, latest)), \
             patch.object(timeline, '_save', side_effect=lambda sid, value, *args: saved.append(copy.deepcopy(value)) or True):
            result = timeline.tl_save_scene(None, '/api/timeline/SAFE_NORMAL_UI/save-scene',
                dict(ep=2, scene_no=2, segment_patch=dict(no=4, adopted=True,
                    refs=[dict(n='阿宁', k='character')])) )
        self.assertTrue(result['ok'])
        actual = saved[-1]['scenes'][0]['segments']
        self.assertEqual(actual[1], rows[1])
        self.assertEqual(actual[0]['video'], rows[0]['video'])
        self.assertEqual(actual[0]['render_request'], rows[0]['render_request'])
        self.assertTrue(actual[0]['adopted'])

    def test_missing_project_cannot_report_successful_save(self):
        latest = dict(scenes=[dict(no=2, segments=[dict(no=4, refs=[])])])
        with patch.object(timeline, '_ep_of', return_value=2), \
             patch.object(timeline, '_load', return_value=({}, latest)), \
             patch.object(timeline, '_save', return_value=False), \
             patch.object(timeline, '_RESP', side_effect=lambda body, status=200: body):
            result = timeline.tl_save_scene(None, '/api/timeline/SAFE_NORMAL_UI/save-scene',
                dict(ep=2, scene_no=2, segment_patch=dict(no=4, refs=[])))
        self.assertFalse(result['ok'])

    def test_reference_save_preserves_current_video_receipt_and_latest_manual_words(self):
        old_row = dict(no=4, prompt='成年修表师收好工具。', seconds=8,
                       chars=['阿宁'], scene='修表店', refs=[], video='', end_frame='')
        browser_row = copy.deepcopy(old_row)
        browser_row['refs'] = [dict(k='character', n='阿宁', p='')]
        browser_row['adopted'] = True
        current_row = dict(old_row, video='saved_normal_clip.mp4', end_frame='saved_end_frame.png',
            render_request=dict(key='keep-original-request', status='pending', final_prompt='bound original words'),
            render_history=[dict(video='older_accepted_clip.mp4')],
            prompt='用户刚保存的成年修表师递表动作。', prompt_edited=True,
            prompt_auto='成年修表师收好工具。', prompt_edited_at=123)
        latest = dict(scenes=[dict(no=2, segments=[copy.deepcopy(current_row)])])
        saved = []
        with patch.object(timeline, '_ep_of', return_value=2), \
             patch.object(timeline, '_load', return_value=({}, latest)), \
             patch.object(timeline, '_save', side_effect=lambda sid, value, *args, **kwargs: saved.append(copy.deepcopy(value)) or True):
            result = timeline.tl_save_scene(None, '/api/timeline/SAFE_NORMAL_UI/save-scene',
                dict(ep=2, scene_no=2, segments=[browser_row]))
        self.assertTrue(result['ok'])
        self.assertTrue(saved)
        actual = saved[-1]['scenes'][0]['segments'][0]
        for field in ('video', 'end_frame', 'render_request', 'render_history', 'prompt',
                      'prompt_edited', 'prompt_auto', 'prompt_edited_at'):
            with self.subTest(field=field):
                self.assertEqual(actual.get(field), current_row[field])
        self.assertEqual(actual['refs'], browser_row['refs'])
        self.assertTrue(actual['adopted'])


if __name__ == '__main__':
    unittest.main()
