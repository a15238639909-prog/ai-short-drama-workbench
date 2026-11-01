"""Batch merge recovery with mocked probes/encoder; no media is produced."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cores import episode_merge as merge, video_continuity


class MergeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='merge_recovery_code_'))
        self.target = self.root / 'accepted.mp4'
        self.target.write_bytes(b'previous accepted file')
        self.rows = [(no, str(self.root / ('segment_%d.mp4' % no))) for no in (1, 2, 3)]
        for _, path in self.rows:
            Path(path).write_bytes(b'retained opaque fixture, not encoded media')
        self.encodes = []

    def encoder(self, args, **kwargs):
        if args[0] != 'ffmpeg':
            raise AssertionError('all probes must use mocked validator')
        self.encodes.append(args)
        Path(args[-1]).write_bytes(b'mocked encoder result, not encoded media')
        return subprocess.CompletedProcess(args, 0, stdout='', stderr='')

    def validate(self, path, expected_seconds=None):
        if str(path) == self.rows[1][1]:
            raise ValueError('fixture decoder failed')
        return dict(duration=5.0, audio=True)

    def run_merge(self, report=None, validate=None, rows=None):
        with patch.object(merge, 'episode_videos', return_value=self.rows if rows is None else rows), \
             patch.object(merge, 'film_title', return_value='safe'), \
             patch.object(merge, '_target_size', return_value=(160, 96)), \
             patch.object(video_continuity, 'validate_video', side_effect=validate or self.validate), \
             patch.object(merge.subprocess, 'run', side_effect=self.encoder):
            return merge.merge_episode('SAFE_TEST', 1, dst=str(self.target),
                                       skip_invalid=True, report=report)

    def test_broken_middle_clip_exports_good_clips_and_reports_gap(self):
        report = {}
        output = self.run_merge(report)
        self.assertEqual(report['merged_segments'], [1, 3])
        self.assertEqual([row['segment_no'] for row in report['skipped']], [2])
        self.assertTrue(report['partial'])
        self.assertEqual(report['output'], output)
        self.assertIn('_部分', Path(output).stem)
        self.assertEqual(len(self.encodes), 1)
        args = self.encodes[0]
        self.assertNotIn(self.rows[1][1], args)
        self.assertIn('concat=n=2:', args[args.index('-filter_complex') + 1])
        self.assertEqual(self.target.read_bytes(), b'previous accepted file')
        self.assertTrue(all(Path(path).exists() for _, path in self.rows))

    def test_all_invalid_returns_no_movie_without_encoding(self):
        report = {}
        output = self.run_merge(report, validate=lambda *a: (_ for _ in ()).throw(ValueError('bad fixture')))
        self.assertEqual(output, '')
        self.assertEqual(report['merged_segments'], [])
        self.assertEqual(len(report['skipped']), 3)
        self.assertEqual(self.encodes, [])
        self.assertEqual(self.target.read_bytes(), b'previous accepted file')

    def test_complete_batch_is_not_marked_partial_or_overwritten(self):
        report = {'partial': True, 'skipped': ['stale'], 'output': 'old'}
        output = self.run_merge(report, validate=lambda *a: dict(duration=5.0, audio=False))
        self.assertFalse(report['partial'])
        self.assertEqual(report['skipped'], [])
        self.assertEqual(report['merged_segments'], [1, 2, 3])
        self.assertNotIn('_部分', Path(output).stem)
        self.assertNotEqual(output, str(self.target))
        self.assertEqual(self.target.read_bytes(), b'previous accepted file')

    def test_missing_encoder_remains_infrastructure_error(self):
        with self.assertRaises(FileNotFoundError):
            self.run_merge({}, validate=lambda *a: (_ for _ in ()).throw(FileNotFoundError('ffprobe missing')))
        self.assertFalse(self.encodes)

    def test_per_clip_probe_timeout_skips_only_that_input(self):
        def validate(path, *a):
            if str(path) == self.rows[1][1]:
                raise subprocess.TimeoutExpired('mock probe', 60)
            return dict(duration=5.0, audio=True)
        report = {}
        self.run_merge(report, validate=validate)
        self.assertEqual(report['merged_segments'], [1, 3])

    def test_non_finite_duration_is_never_used_as_concat_time(self):
        def validate(path, *a):
            return dict(duration=float('nan') if str(path) == self.rows[1][1] else 5.0, audio=True)
        report = {}
        self.run_merge(report, validate=validate)
        self.assertEqual(report['merged_segments'], [1, 3])

    def test_no_inputs_returns_no_movie_and_clears_report(self):
        report = {'output': 'stale', 'partial': True}
        self.assertEqual(self.run_merge(report, rows=[]), '')
        self.assertEqual(report, dict(skipped=[], merged_segments=[], partial=False, output=''))
        self.assertFalse(self.encodes)

    def test_manual_merge_still_rejects_invalid_input_before_encoding(self):
        result = subprocess.CompletedProcess(['mock'], 1, stdout='{}', stderr='bad fixture')
        with patch.object(merge, 'episode_videos', return_value=self.rows), \
             patch.object(merge, 'film_title', return_value='safe'), \
             patch.object(merge.subprocess, 'run', return_value=result) as process:
            with self.assertRaisesRegex(RuntimeError, '合成输入视频无效'):
                merge.merge_episode('SAFE_TEST', 1, dst=str(self.target))
        self.assertEqual(process.call_count, 1)
        self.assertEqual(self.target.read_bytes(), b'previous accepted file')


if __name__ == '__main__':
    unittest.main()
