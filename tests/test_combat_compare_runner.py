"""Retained, no-model checks for the frozen combat comparison entry."""
from contextlib import nullcontext
import copy
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('combat_pilot_fixture', ROOT / 'tests/test_fantasy_combat_pilot.py')
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
pilot = helpers.pilot


def frozen_fixture():
    baseline, old_config = helpers.fixture()
    plan = helpers.story_plan()
    for index, beat in enumerate(plan['beats'], 1):
        beat['scene'] = pilot.scene_for_index(old_config, index)['name']
    source = '雷恩说：“一起上。”\n两位主角打败魔像。\n'
    (baseline / 'config.json').write_text(json.dumps(old_config, ensure_ascii=False), encoding='utf-8')
    (baseline / 'story.txt').write_text(source, encoding='utf-8')
    (baseline / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=3), encoding='utf-8')
    output = baseline.with_name(baseline.name + '_comparison')
    output.mkdir()
    config = copy.deepcopy(old_config)
    config['frozen_input_dir'] = str(baseline)
    (output / 'config.json').write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
    return baseline, output, config, plan, source


class CombatCompareRunnerTests(unittest.TestCase):
    def test_frozen_import_is_exact_and_never_rewrites_or_imports_old_state(self):
        baseline, output, config, plan, source = frozen_fixture()
        (baseline / 'manifest.json').write_text('{"observation":"旧末态"}', encoding='utf-8')
        before = {p.name: pilot._sha(p) for p in baseline.iterdir() if p.is_file()}
        manifest = {}
        result = pilot.freeze_baseline_inputs(config, output, manifest)
        self.assertEqual(result, (source, plan))
        self.assertEqual(pilot.freeze_baseline_inputs(config, output, manifest), result)
        self.assertEqual([pilot._sha(output / name) for name in ('story.txt', 'plan.json')],
                         [pilot._sha(baseline / name) for name in ('story.txt', 'plan.json')])
        self.assertFalse((output / 'manifest.json').exists())
        self.assertEqual(manifest['frozen_baseline']['author_and_planning_calls'], 0)
        self.assertFalse(manifest['frozen_baseline']['creative_changes'])
        self.assertEqual({p.name: pilot._sha(p) for p in baseline.iterdir() if p.is_file()}, before)
        changed = copy.deepcopy(config)
        changed['seeds'][0] += 1
        with self.assertRaises(ValueError): pilot.freeze_baseline_inputs(changed, output, manifest)
        changed = copy.deepcopy(config)
        changed['cards'][0]['name'] = '另一人'
        with self.assertRaises(ValueError): pilot.freeze_baseline_inputs(changed, output, manifest)
        with self.assertRaises(ValueError): pilot.freeze_baseline_inputs(config, baseline, {})
        (baseline / 'story.txt').write_text(source + '改变原稿', encoding='utf-8')
        with self.assertRaises(ValueError): pilot.freeze_baseline_inputs(config, output, manifest)

    def test_scene_cut_does_not_inject_empty_old_scene_into_new_beginning(self):
        _, config = helpers.fixture()
        beat = helpers.story_plan()['beats'][2]
        beat.update(scene='水晶圣殿', action='【0-5s】水晶圣殿边缘亮起紫光，两人举剑。')
        state = dict(actual_visible='天空竞技场空场，当前未见人物。', scene='天空竞技场', characters=[])
        before = copy.deepcopy(state)
        _, payload, _ = pilot.scene_write_request(('', {}, {}), config, beat, state, has_previous=True)
        self.assertEqual(payload['actual_state'], before)
        self.assertEqual(payload['scene_continuity_contract']['start_state'], '')
        self.assertEqual(payload['scene_continuity_contract']['scene_cut']['to'], '水晶圣殿')
        self.assertNotIn('scene_transition', payload['scene_continuity_contract'])
        beat['action'] = '两人从天空竞技场穿过蓝色传送门，进入水晶圣殿。'
        transition = pilot.scene_director_contract(config, beat, state, has_previous=True)
        self.assertEqual(transition['start_state'], before['actual_visible'])
        self.assertIn('scene_transition', transition)
        self.assertNotIn('scene_cut', transition)
        self.assertEqual(state, before)

    def test_frozen_mock_pipeline_skips_writer_and_planning_and_observes_fresh_frames(self):
        from cores import store, saga_core, story_video_reference as reference, story_video_media as media
        from cores import story_video_reference_prompts as prompts, story_video_characters as characters
        from models import gpu_manager, story_video_client as client, h3_client
        from api import segment_api as segment
        baseline, output, config, plan, source = frozen_fixture()
        config['final_anchor_policy'] = 'render-anchors-v1'
        (output / 'config.json').write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        before = {p.name: pilot._sha(p) for p in baseline.iterdir() if p.is_file()}
        contexts, observed, rendered = [], [], []
        class Session(reference._Session):
            def guard(self): pass
            def models(self): return nullcontext()
            def ask(self, request, directory, images=None, validator=None):
                self_test.assertNotIn('count', request[1])
                contexts.append(copy.deepcopy(request[1]))
                result = helpers.director_reply(request[1])
                if validator: validator(result, request[1])
                return result
            def review(self, request, directory, images=None, validator=None):
                observed.append(list(images))
                return dict(actual_visible='本轮真实末帧%d' % len(observed), scene='unknown',
                            characters=[], completed=[], actions=[])
        self_test = self
        def render(sid, ep, scene_no, index, **kwargs):
            timeline = saga_core.ep_timeline(sid, ep)
            row = pilot._row(timeline, index)[1]
            actual = segment._render_context(sid, ep, scene_no, index, size_tier='0.7',
                override=kwargs['override'], fresh=True)
            rendered.append(dict(index=index, scene=scene_no, refs=actual[6]))
            self.assertTrue((output / 'segments' / ('%02d' % index) / 'compiled_prompt.txt').is_file())
            h3_client._http('POST', '/prompt')
            clip = output / 'segments' / ('%02d' % index) / 'mock_main.mp4'
            clip.write_bytes(b'mock-video-only')
            row.update(video=str(clip), used_refs=row['refs'], render_request=dict(status='completed'))
            saga_core.save_ep_timeline(sid, ep, timeline)
            return dict(video=str(clip))
        original_data, original_path = store.DATA, saga_core.seg_video_path
        try:
            with patch.object(reference, '_Session', Session), \
                 patch.object(reference, '_story', side_effect=AssertionError('冻结对照不得重新写小说')), \
                 patch.object(characters, 'prepare_source_contract', side_effect=AssertionError('冻结计划不重新审稿')), \
                 patch.object(prompts, 'sanitize_reference_observation', side_effect=lambda material, _: material), \
                 patch.object(segment, '_render_critical_problems', return_value=[]), \
                 patch.object(segment, 'is_segment_current', return_value=False), \
                 patch.object(segment, 'render_segment', side_effect=render), \
                 patch.object(gpu_manager, 'acquire'), \
                 patch.object(client, '_native_profile', side_effect=lambda *_: nullcontext()), \
                 patch.object(client, 'preserve_release'), \
                 patch.object(media, 'adopt', side_effect=lambda path, _: dict(clip_path=path, anchor_path='fresh-end.png')), \
                 patch.object(media, 'sample_at_times', return_value=[dict(path='fresh-sample.png')]), \
                 patch.object(media, 'join', return_value=dict(path='joined_mock.mp4')), \
                 patch.object(h3_client, '_http', return_value='queued'), patch('cores.story_video_offline.install'):
                pilot.run(output)
        finally:
            store.DATA, saga_core.seg_video_path = original_data, original_path
        self.assertEqual(len(contexts), 5)
        self.assertEqual([len(row['refs']) for row in rendered], [3, 2, 3, 2, 2])
        self.assertEqual([row['scene'] for row in rendered], [1, 1, 2, 2, 2])
        self.assertEqual([ctx['actual_state']['actual_visible'] for ctx in contexts],
                         ['本轮真实末帧%d' % index for index in range(1, 6)])
        self.assertEqual(len(observed), 6)
        self.assertTrue(all(images == ['fresh-end.png'] for images in observed[1:]))
        manifest = pilot._read(output / 'manifest.json')
        self.assertEqual(manifest['status'], 'completed')
        self.assertEqual(manifest['actual_h3_submissions'], 5)
        self.assertEqual(manifest['frozen_baseline']['author_and_planning_calls'], 0)
        for index in range(1, 6):
            preflight = pilot._read(output / 'segments' / ('%02d' % index) / 'final_anchor_preflight.json')
            self.assertTrue(preflight['scene_description_present'])
        self.assertEqual(pilot._sha(output / 'plan.json'), before['plan.json'])
        self.assertEqual({p.name: pilot._sha(p) for p in baseline.iterdir() if p.is_file()}, before)


if __name__ == '__main__':
    os.environ['V41_NO_MODEL'] = '1'
    unittest.main()
