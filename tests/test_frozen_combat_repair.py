"""Pure scope edits and a fully mocked two-take run; evidence is retained."""
from contextlib import nullcontext
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import run_frozen_combat_repair as repair
spec = importlib.util.spec_from_file_location('frozen_helpers', ROOT / 'tests/test_combat_compare_runner.py')
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
ORIGINAL = ROOT / 'outputs/story_video/fantasy_combat_5clips_20261021'


def original_prompt(index):
    return repair.pilot._read(ORIGINAL / 'segments' / ('%02d' % index) / 'main_result.json')['render_request']['final_prompt']


class FrozenCombatRepairTests(unittest.TestCase):
    def test_real_frozen_prompts_only_change_allowed_injected_scope(self):
        for index, arrival_count in ((3, 2), (5, 0)):
            original = original_prompt(index)
            visible = dict(actual_visible='紫晶竞技场中两人站在棱柱旁，未见敌人。')
            updated, audit = repair.patch_frozen_prompt(original, index, visible)
            self.assertEqual(audit['removed_arrival_rule_count'], arrival_count)
            self.assertTrue(audit['authored_shots_cuts_dialogue_audio_unchanged'])
            self.assertEqual(original[original.index('[Shot 1]'):], updated[updated.index('[Shot 1]'):])
            self.assertEqual(updated.count(repair.BEGINNING), 1)
            self.assertIn(visible['actual_visible'], updated)
            self.assertNotIn('图1显示', updated.split('[Shot 1]')[0])
            omitted, unknown = repair.patch_frozen_prompt(original, index, dict(status='unavailable'))
            self.assertNotIn(repair.BEGINNING, omitted)
            self.assertFalse(unknown['true_endpoint_facts_used'])
        self.assertEqual(repair.endpoint_facts(dict(actual_visible='图1-4显示旧战斗。')), '')
        self.assertEqual(repair.endpoint_facts(dict(actual_visible='unknown')), '')

    def test_actual_payload_mismatch_is_rejected_before_counting_submission(self):
        from models import h3_client
        folder, _ = helpers.helpers.fixture()
        class Session:
            manifest = dict(actual_h3_submissions=0, reference_policy=dict(max_h3_submissions=2))
            def guard(self): pass
            def checkpoint(self): pass
        session = Session()
        with patch.object(h3_client, '_http', return_value='queued') as http:
            with repair.exact_submission(session, folder, 3, 'expected prompt'):
                with self.assertRaises(RuntimeError):
                    h3_client._http('POST', '/prompt', dict(prompt={'1': dict(inputs=dict(prompt='changed prompt'))}))
                self.assertEqual(session.manifest['actual_h3_submissions'], 0)
                self.assertEqual(h3_client._http('POST', '/prompt', dict(prompt={'1': dict(inputs=dict(prompt='expected prompt'))})), 'queued')
                with self.assertRaises(RuntimeError):
                    h3_client._http('POST', '/prompt', dict(prompt={'1': dict(inputs=dict(prompt='expected prompt'))}))
            self.assertEqual(http.call_count, 1)
        self.assertEqual(session.manifest['actual_h3_submissions'], 1)

    def test_mock_run_reuses_three_original_clips_and_has_only_two_observation_and_h3_calls(self):
        from cores import store, saga_core, story_video_reference as reference, story_video_media as media
        from models import h3_client, gpu_manager, story_video_client as client
        from api import segment_api as segment
        baseline, folder, config, plan, source = helpers.frozen_fixture()
        rows = []
        for index in range(1, 6):
            work = baseline / 'segments' / ('%02d' % index)
            work.mkdir(parents=True)
            (work / 'clip.mp4').write_bytes(('original clip %d' % index).encode())
            (work / 'anchor.png').write_bytes(('actual anchor %d' % index).encode())
            (work / 'director.json').write_bytes(('frozen director %d' % index).encode())
            (work / 'main_result.json').write_text(json.dumps(dict(render_request=dict(final_prompt=original_prompt(index)))), encoding='utf-8')
            rows.append(dict(index=index, clip_path=str(work / 'clip.mp4'), raw_path=str(work / 'clip.mp4'),
                anchor_path=str(work / 'anchor.png'), kept_seconds=15, kept_frames=360, frame_index=359,
                frame_time_seconds=14.958333, observation=dict(actual_visible='旧混合观察，不能复用。')))
        (baseline / 'manifest.json').write_text(json.dumps(dict(segments=rows)), encoding='utf-8')
        before = {str(p.relative_to(baseline)): repair.pilot._sha(p) for p in baseline.rglob('*') if p.is_file()}
        # This fixture output has a comparison config; preserve it outside the repaired output.
        folder = folder.with_name(folder.name + '_repair')
        folder.mkdir()
        observations, renders = [], []
        class Session(reference._Session):
            def guard(self): pass
            def models(self): return nullcontext()
            def ask(self, *args, **kwargs): raise AssertionError('不能请求任何作者或导演')
            def review(self, request, directory, images=None, validator=None):
                observations.append(list(images))
                return dict(actual_visible='本次单张末帧，两位主角在紫晶平台。')
        def render(sid, ep, scene_no, index, **kwargs):
            timeline = saga_core.ep_timeline(sid, ep)
            row = repair.pilot._row(timeline, index)[1]
            renders.append(index)
            h3_client._http('POST', '/prompt', dict(prompt={'1': dict(inputs=dict(prompt=row['prompt']))}))
            clip = folder / 'segments' / ('%02d' % index) / 'main_render.mp4'
            clip.write_bytes(('new scope repair clip %d' % index).encode())
            row.update(video=str(clip), render_request=dict(final_prompt=row['prompt']))
            saga_core.save_ep_timeline(sid, ep, timeline)
            return dict(video=str(clip), render_request=row['render_request'])
        original_data, original_path = store.DATA, saga_core.seg_video_path
        try:
            with patch.object(reference, '_Session', Session), \
                 patch.object(segment, '_render_critical_problems', return_value=[]), \
                 patch.object(segment, 'is_segment_current', return_value=False), \
                 patch.object(segment, 'render_segment', side_effect=render), \
                 patch.object(gpu_manager, 'acquire'), \
                 patch.object(client, '_native_profile', side_effect=lambda *_: nullcontext()), \
                 patch.object(client, 'preserve_release'), \
                 patch.object(media, 'adopt', side_effect=lambda path, _: dict(clip_path=path, raw_path=path, kept_seconds=15)), \
                 patch.object(media, 'sample_at_times', return_value=[]), \
                 patch.object(media, 'join', return_value=dict(path='mock_joined.mp4', full_decode_passed=True)), \
                 patch.object(h3_client, '_http', return_value='queued'), patch('cores.story_video_offline.install'):
                repair.run(folder, baseline)
                # Resume only reuses receipts and assembled output, without a new model/paid call.
                repair.run(folder, baseline)
        finally:
            store.DATA, saga_core.seg_video_path = original_data, original_path
        self.assertEqual(renders, [3, 5])
        self.assertEqual(observations, [[rows[1]['anchor_path']], [rows[3]['anchor_path']]])
        manifest = repair.pilot._read(folder / 'manifest.json')
        self.assertEqual(manifest['actual_h3_submissions'], 2)
        self.assertEqual(manifest['status'], 'completed')
        for index in (1, 2, 4):
            self.assertEqual(repair.pilot._sha(folder / 'segments' / ('%02d' % index) / 'adopted/clip.mp4'),
                             repair.pilot._sha(rows[index - 1]['clip_path']))
        self.assertEqual({str(p.relative_to(baseline)): repair.pilot._sha(p) for p in baseline.rglob('*') if p.is_file()}, before)


if __name__ == '__main__':
    os.environ['V41_NO_MODEL'] = '1'
    unittest.main()
