"""Targeted zero-model checks for the shared workbench video buttons."""
import copy
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cores import workbench_director_runtime as runtime, store, saga_core, authoring


class WorkbenchRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.folder = ROOT / 'tests/.retained' / ('original_runtime_%d' % time.time_ns())
        self.folder.mkdir(parents=True)
        self.original_data = store.DATA
        store.DATA = self.folder
        for name in ('stories', 'assets', 'tasks', 'narrative', 'states', 'profiles', 'manifests'):
            (store.DATA / name).mkdir()
        self.sid = 'RUNTIME_TEST'
        from cores import story_core
        story_core.save_story(dict(story_id=self.sid, settings=dict(story_mode='single'),
            saga=dict(episodes=[dict(no=1)])))

    def tearDown(self):
        store.DATA = self.original_data

    def test_sequence_keeps_existing_and_manual_pending_words_then_observes_before_next_writer(self):
        from api import segment_api
        timeline = dict(scenes=[dict(no=1, segments=[dict(no=1, video='old-one.mp4', prompt='old frozen words'),
            dict(no=2, prompt='hand edited words', prompt_edited=True),
            dict(no=3, prompt='', render_request=dict(key='already-submitted', final_prompt='pending frozen words'))])])
        saga_core.save_ep_timeline(self.sid, 1, timeline)
        order = []
        def build(sid, ep, rows):
            tl = saga_core.ep_timeline(sid, ep)
            for row in rows:
                tl['scenes'][0]['segments'].append(dict(no=row['n'], prompt=row['prompt']))
            saga_core.save_ep_timeline(sid, ep, tl)
        def write(sid, ep, **kw):
            no = kw['start'] + 1
            order.append('write%d' % no)
            return [dict(n=no, prompt='new original Chinese words')]
        def render(scene, no):
            order.append('render%d' % no)
            tl, _, row = runtime._segment(self.sid, 1, no)
            self.assertEqual(row['prompt'], {2:'hand edited words', 3:'pending frozen words', 4:'new original Chinese words'}[no])
            row['video'] = 'video%d.mp4' % no
            saga_core.save_ep_timeline(self.sid, 1, tl)
            return dict(video=row['video'])
        with patch.object(authoring, 'make_h3_prompts', side_effect=write), \
             patch.object(authoring, 'build_timeline', side_effect=build), \
             patch.object(segment_api, 'is_segment_current', side_effect=lambda sid, ep, sc, no, **kw: no == 1), \
             patch.object(runtime, 'after_render', side_effect=lambda sid, ep, no, *args: order.append('observe%d' % no)):
            outputs = runtime.run_sequence(self.sid, 1, [1,2,3,4], render=render)
        self.assertEqual(order, ['render2','observe2','render3','observe3','write4','render4','observe4'])
        self.assertEqual(outputs, ['old-one.mp4','video2.mp4','video3.mp4','video4.mp4'])
        self.assertTrue(runtime._segment(self.sid,1,2)[2]['prompt_edited'])
        from cores import story_core
        story = story_core.get_story(self.sid)
        with patch.object(segment_api, 'render_opening_all', return_value=(0,'')) as opening:
            runtime.explicit_opening(self.sid,1)
            opening.assert_not_called()
            story['settings']['opening_show'] = True
            story_core.save_story(story)
            runtime.explicit_opening(self.sid,1)
            opening.assert_called_once()

    def test_single_frame_format_failure_is_cached_unknown_and_does_not_block(self):
        from cores import story_video_media as media, story_video_core as core, video_continuity as vc
        video = self.folder / 'clip.mp4'
        video.write_bytes(b'fake video for mocked extractor')
        saga_core.save_ep_timeline(self.sid,1,dict(scenes=[dict(no=1,segments=[dict(no=1,video=str(video))])]))
        warnings = []
        def extract(path, index, target):
            self.assertEqual(index, 1)
            Path(target).write_bytes(b'last frame')
        with patch.object(media,'_timeline',return_value=({}, {}, [0, 1])), \
             patch.object(media,'_extract',side_effect=extract), \
             patch.object(core,'request_json',side_effect=RuntimeError('本地观察格式错误')) as ask:
            actual = runtime.after_render(self.sid,1,1,warnings.append)
            self.assertEqual(actual['actual_visible'],'unknown')
            self.assertEqual(runtime.actual_state_for(self.sid,1,1),actual)
            self.assertEqual(ask.call_count,1)
            self.assertEqual(len(ask.call_args.kwargs['images']),1)
        self.assertTrue(warnings)
        self.assertEqual(runtime.actual_state_for(self.sid,1,0)['actual_visible'],'unknown')

    def test_relative_adopted_image_uses_shared_resolved_path_qualification(self):
        from cores import reference_ready
        # The shared resolver handles stored outputs/... paths. Runtime must
        # consume its result rather than testing the raw relative spelling.
        resolved = {'主角': 'D:/resolved-workbench/outputs/reference.png'}
        slices = [dict(director=dict(flow='source-v1'))]
        with patch.object(reference_ready, 'adopted_paths', return_value=resolved) as paths, \
             patch.object(authoring, 'use_new_pipeline', return_value=True), \
             patch.object(authoring, '_pov_spec', return_value=('常规', '')), \
             patch.object(authoring, 'episode_slices', return_value=slices):
            self.assertTrue(runtime.enabled(self.sid,1))
            paths.assert_called_once_with(self.sid)

    def test_both_user_buttons_use_shared_sequence_while_text_only_keeps_batch(self):
        from api import timeline, saga_api, segment_api
        from cores import story_core, reference_ready, episode_ledger
        story_core.save_story(dict(story_id=self.sid,settings=dict(story_mode='single'),saga=dict(episodes=[dict(no=1)])))
        saga_core.save_ep_timeline(self.sid,1,dict(scenes=[dict(no=1,segments=[])]))
        calls = []
        def sequence(sid,ep,targets,**kwargs):
            calls.append(list(targets))
            return ['mock.mp4']
        with patch.object(runtime,'enabled',return_value=True), patch.object(runtime,'run_sequence',side_effect=sequence), \
             patch.object(authoring,'total_segments',return_value=2), patch.object(authoring,'apply_slicing_drift',return_value=(0,0)), \
             patch.object(authoring,'refresh_upstream',return_value={}), patch.object(authoring,'use_new_pipeline',return_value=True), \
             patch.object(authoring,'next_prompts',side_effect=AssertionError('视频串行不能先批写')), \
             patch.object(reference_ready,'ensure_characters'), patch.object(saga_api,'_ep_scene_images'), \
             patch.object(saga_api,'_preflight_gate',return_value=dict(blocked=False)), \
             patch.object(timeline,'_ep_body',return_value='原始剧本'), \
             patch.object(timeline,'_confirmed_arrangement',return_value=None), patch.object(saga_core,'recover_segment_media'), \
             patch.object(saga_core,'timeline_stale',return_value=False), \
             patch.object(episode_ledger,'build') as ledger:
            timeline._gen_next(self.sid,1,2)
            self.assertEqual(timeline._job(self.sid)['status'],'completed')
            job = dict(total=0,done=0,logs=[])
            self.assertEqual(saga_api._first_video_chain(self.sid,job,n=2,ep=1,approved=True),['mock.mp4'])
            self.assertEqual(calls,[[1,2],[1,2]])
            ledger.assert_called_once_with(self.sid,1)
        # Branch predicates explicitly leave text-only and prompts-only calls out.
        self.assertIn('if not prompts_only and _original_runtime.enabled',Path(timeline.__file__).read_text(encoding='utf-8'))
        self.assertIn('if not text_only and _original_runtime.enabled',Path(saga_api.__file__).read_text(encoding='utf-8'))


if __name__ == '__main__':
    os.environ['V41_NO_MODEL']='1'
    unittest.main()
