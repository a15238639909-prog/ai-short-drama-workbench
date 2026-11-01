"""Resume input/media integrity; synthetic inference and retained fixture files."""
import copy
import os
from pathlib import Path
import unittest

os.environ['V41_NO_MODEL'] = '1'
from cores import story_video_core as core
from tests import test_story_video_flow_optimization as flow_fixtures


class StoryResumeIntegrityTests(unittest.TestCase):
    core_case = flow_fixtures.FlowOptimizationTests.core_case

    def test_text_and_image_changed_inputs_do_not_reuse_or_regenerate_cached_video(self):
        for image in (False, True):
            with self.subTest(mode='image' if image else 'text'):
                case, _, _, _ = self.core_case(image=image)
                core.run_job(case.task['id'])
                original = case.manifest()
                retained = {row['clip_path']: Path(row['clip_path']).read_bytes() for row in original['segments']}
                counts = (len(case.video_calls), len(case.writer_states), case.join.call_count)
                case.task['inputs']['style'] += '，新的画面风格'
                with self.assertRaisesRegex(RuntimeError, '输入.*变化'):
                    core.run_job(case.task['id'])
                self.assertEqual(counts, (len(case.video_calls), len(case.writer_states), case.join.call_count))
                self.assertEqual(case.manifest()['segments'], original['segments'])
                self.assertTrue(all(Path(path).read_bytes() == data for path, data in retained.items()))

    def test_guided_text_changed_adopted_tail_stops_before_next_generation(self):
        case, _, _, _ = self.core_case(guided=True)
        with self.assertRaises(core.ControlStop):
            core.run_job(case.task['id'])
        first = copy.deepcopy(case.manifest()['segments'][0])
        anchor = Path(first['anchor_path'])
        changed = anchor.read_bytes() + b'changed fixture tail'
        anchor.write_bytes(changed)
        core.action(case.task['id'], 'approve')
        case.task['status'] = 'running'
        with self.assertRaisesRegex(RuntimeError, '尾帧.*变化|末帧.*变化|素材.*变化'):
            core.run_job(case.task['id'])
        self.assertEqual(len(case.video_calls), 1)
        self.assertEqual(case.manifest()['segments'], [first])
        self.assertEqual(anchor.read_bytes(), changed)
        case.join.assert_not_called()

    def test_guided_missing_adopted_media_does_not_start_replacement_generation(self):
        case, _, _, _ = self.core_case(guided=True)
        with self.assertRaises(core.ControlStop):
            core.run_job(case.task['id'])
        first = copy.deepcopy(case.manifest()['segments'][0])
        clip = Path(first['clip_path'])
        retained = clip.with_name(clip.name + '.retained')
        original_bytes = clip.read_bytes()
        clip.rename(retained)  # Retain the file; simulate a missing expected path.
        core.action(case.task['id'], 'approve')
        case.task['status'] = 'running'
        with self.assertRaisesRegex(RuntimeError, '采用视频文件缺失'):
            core.run_job(case.task['id'])
        self.assertEqual(len(case.video_calls), 1)
        self.assertEqual(retained.read_bytes(), original_bytes)
        self.assertEqual(case.manifest()['segments'], [first])
        case.join.assert_not_called()

    def test_legacy_guided_rows_without_hashes_reuse_files_without_fabricating_old_evidence(self):
        case, _, _, _ = self.core_case(guided=True)
        with self.assertRaises(core.ControlStop):
            core.run_job(case.task['id'])
        core.action(case.task['id'], 'approve')
        case.task['status'] = 'running'
        core.run_job(case.task['id'])
        legacy = case.manifest()
        legacy.pop('inputs_sha256', None)
        for row in legacy['segments']:
            for key in ('raw_sha256', 'clip_sha256', 'anchor_sha256'):
                row.pop(key, None)
        case.save_json(case.folder / 'manifest.json', legacy)
        before = copy.deepcopy(legacy['segments'])
        counts = (len(case.video_calls), len(case.writer_states))
        core.run_job(case.task['id'])
        self.assertEqual(counts, (len(case.video_calls), len(case.writer_states)))
        self.assertEqual(case.manifest()['segments'], before)
        self.assertNotIn('inputs_sha256', case.manifest())

    def test_guided_image_changed_adopted_clip_stops_before_next_generation(self):
        case, _, _, _ = self.core_case(image=True, guided=True)
        with self.assertRaises(core.ControlStop):
            core.run_job(case.task['id'])
        core.action(case.task['id'], 'approve')
        case.task['status'] = 'running'
        def pause_after_first(label, done):
            if label in ('已完成1/2段', '已保存1/2段'):
                case.task['pause_requested'] = True
        case.after_step = pause_after_first
        with self.assertRaises(core.ControlStop):
            core.run_job(case.task['id'])
        first = copy.deepcopy(case.manifest()['segments'][0])
        clip = Path(first['clip_path'])
        changed = clip.read_bytes() + b'changed fixture clip'
        clip.write_bytes(changed)
        case.after_step = None
        case.task.update(status='running', pause_requested=False)
        with self.assertRaisesRegex(RuntimeError, '视频.*变化|素材.*变化'):
            core.run_job(case.task['id'])
        self.assertEqual(len(case.video_calls), 1)
        self.assertEqual(case.manifest()['segments'], [first])
        self.assertEqual(clip.read_bytes(), changed)
        case.join.assert_not_called()


if __name__ == '__main__':
    unittest.main()
