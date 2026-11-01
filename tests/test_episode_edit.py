"""Isolated edit-list tests plus real FFmpeg trim/sync checks; fixtures are retained."""
import array
import copy
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cores import episode_edit as edit, episode_merge as merge, store, story_core
from api import episode_edit_api


class EditListTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='episode_edit_isolated_'))
        self.available = [dict(segment_no=1, scene_no=1, path='first.mp4', duration=4.0,
                               has_audio=True, fingerprint='version1', usable=True, error=''),
                          dict(segment_no=2, scene_no=1, path='second.mp4', duration=3.0,
                               has_audio=False, fingerprint='version2', usable=True, error='')]
        self.items = [dict(segment_no=1, fingerprint='version1', **{'in': 1, 'out': 2}),
                      dict(segment_no=2, fingerprint='version2', **{'in': 0, 'out': 1}),
                      dict(segment_no=1, fingerprint='version1', **{'in': 3, 'out': 4})]
        for manager in (patch.object(store, 'DATA', self.root),
                        patch.object(edit, 'sources', side_effect=lambda *args: copy.deepcopy(self.available))):
            manager.start()
            self.addCleanup(manager.stop)

    def test_save_order_multiple_ranges_same_source_and_reset(self):
        before = edit.get_edit('EDIT_TEST')
        self.assertFalse(before['enabled'])
        saved = edit.save_edit('EDIT_TEST', 1, self.items, 0)
        self.assertEqual(saved['revision'], 1)
        self.assertEqual(saved['total_duration'], 3)
        self.assertEqual([x['segment_no'] for x in saved['items']], [1, 2, 1])
        plan = edit.render_plan('EDIT_TEST', 1)
        self.assertEqual([x['in'] for x in plan], [1, 0, 3])
        self.assertEqual([x['segment_no'] for x in edit.render_plan('EDIT_TEST', 1, [1])], [1, 1])
        reset = edit.reset_edit('EDIT_TEST', 1, 1)
        self.assertFalse(reset['enabled'])
        self.assertEqual(reset['items'], [])
        self.assertEqual(reset['revision'], 2)
        self.assertIsNone(edit.render_plan('EDIT_TEST', 1))
        self.assertTrue(edit._path('EDIT_TEST', 1).is_file())

    def test_invalid_ranges_and_versions_do_not_save(self):
        for changes in ({'in': -1}, {'out': 5}, {'in': 2, 'out': 2}, {'out': .01, 'in': 0},
                        {'in': float('nan')}, {'out': float('inf')}, {'in': True},
                        {'out': 'invalid'}, {'fingerprint': 'old'}, {'segment_no': 999}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    edit.save_edit('EDIT_TEST', 1, [dict(self.items[0], **changes)], 0)
        with self.assertRaises(ValueError):
            edit.save_edit('EDIT_TEST', 1, [], 0)
        self.assertFalse(edit._path('EDIT_TEST', 1).exists())

    def test_revision_conflict_keeps_last_save(self):
        edit.save_edit('EDIT_TEST', 1, self.items, 0)
        for operation in (lambda: edit.save_edit('EDIT_TEST', 1, self.items[:1], 0),
                          lambda: edit.reset_edit('EDIT_TEST', 1, 0)):
            with self.assertRaisesRegex(ValueError, '已改变'):
                operation()
        self.assertEqual(len(edit.get_edit('EDIT_TEST')['items']), 3)

    def test_stale_missing_and_unusable_inputs_block_even_recovering_merge(self):
        edit.save_edit('EDIT_TEST', 1, self.items, 0)
        original = copy.deepcopy(self.available)
        for invalid in ([dict(original[0], fingerprint='regenerated'), original[1]],
                        [dict(original[0], usable=False, error='源视频已过期'), original[1]],
                        [original[1]]):
            self.available = invalid
            self.assertTrue(edit.get_edit('EDIT_TEST')['errors'])
            with patch.object(merge.subprocess, 'run') as encoder:
                with self.assertRaises(ValueError):
                    merge.merge_episode('EDIT_TEST', 1, dst=str(self.root / 'must_not_export.mp4'),
                                        skip_invalid=True)
                encoder.assert_not_called()

    def test_corrupt_edit_file_does_not_fall_back_to_whole_clip(self):
        path = edit._path('EDIT_TEST', 1)
        path.parent.mkdir(parents=True)
        path.write_text('{broken', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '损坏'):
            edit.render_plan('EDIT_TEST', 1)

    def test_api_routes_and_payload(self):
        with patch.object(edit, 'get_edit', return_value={'revision': 0}) as get_edit:
            response = episode_edit_api.get_edit(None, '/api/episode/edit', 'sid=EDIT_TEST&ep=2')
            self.assertTrue(response['ok'])
            get_edit.assert_called_once_with('EDIT_TEST', '2')
        result = episode_edit_api.get_edit(None, '', {'sid': ['EDIT_TEST'], 'ep': ['1']})
        self.assertTrue(result['ok'])
        saved = episode_edit_api.save_edit(None, '', dict(sid='EDIT_TEST', ep=1, revision=0, items=self.items))
        self.assertTrue(saved['ok'])
        conflict = episode_edit_api.reset_edit(None, '', dict(sid='EDIT_TEST', ep=1, revision=0))
        self.assertFalse(conflict['ok'])
        reset = episode_edit_api.reset_edit(None, '', dict(sid='EDIT_TEST', ep=1, revision=1))
        self.assertFalse(reset['data']['enabled'])

    def test_invalid_project_identity_cannot_escape_storage(self):
        for sid in ('../outside', r'..\outside', 'C:outside', ''):
            with self.assertRaises(ValueError):
                edit._path(sid, 1)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg tools unavailable')
class RealMediaEditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='episode_edit_real_media_'))
        cls.voice = cls.root / 'red440_blue880.mp4'
        cls.silent = cls.root / 'silent_green.mp4'
        cls.run_process(['ffmpeg', '-nostdin', '-v', 'error',
                         '-f', 'lavfi', '-i', 'color=c=red:s=160x96:r=24:d=1',
                         '-f', 'lavfi', '-i', 'color=c=blue:s=160x96:r=24:d=1',
                         '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1',
                         '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000:duration=1',
                         '-filter_complex', '[0:v][2:a][1:v][3:a]concat=n=2:v=1:a=1[v][a]',
                         '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                         '-c:a', 'aac', str(cls.voice)])
        cls.run_process(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                         'color=c=green:s=160x96:r=24:d=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                         str(cls.silent)])
        cls.initial_hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (cls.voice, cls.silent)]

    @staticmethod
    def run_process(args):
        result = subprocess.run(args, capture_output=True, timeout=90)
        if result.returncode:
            raise AssertionError(result.stderr.decode('utf-8', 'replace')[-2000:])
        return result.stdout

    def setUp(self):
        self.data = self.root / self._testMethodName
        story = dict(story_id='EDIT_REAL', title='隔离剪辑测试', saga={'episodes': [dict(no=1, timeline={
            'scenes': [dict(no=1, segments=[dict(no=1, video=str(self.voice), size='160x96'),
                                           dict(no=2, video=str(self.silent), size='160x96')])]})]})
        self.story = story
        for manager in (patch.object(store, 'DATA', self.data),
                        patch.object(story_core, 'get_story', return_value=story)):
            manager.start()
            self.addCleanup(manager.stop)

    def test_actual_audio_video_ranges_silent_and_reset_preserve_originals(self):
        available = edit.get_edit('EDIT_REAL')['sources']
        first, silent = available
        self.assertTrue(first['has_audio'])
        self.assertFalse(silent['has_audio'])
        def interval(source, start, end):
            return dict(segment_no=source['segment_no'], fingerprint=source['fingerprint'],
                        **{'in': start, 'out': end})
        edit.save_edit('EDIT_REAL', 1, [interval(first, 1, 1.5), interval(silent, .25, .75),
                                      interval(first, .25, .75)], 0)
        report = {}
        target = self.data / 'edited.mp4'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'previous film stays intact')
        output = merge.merge_episode('EDIT_REAL', 1, dst=str(target), report=report)
        self.assertEqual(report['merged_segments'], [1, 2, 1])
        self.assertNotEqual(output, str(target))
        self.assertEqual(target.read_bytes(), b'previous film stays intact')
        probed = json.loads(self.run_process(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', output]))
        durations = [float(s['duration']) for s in probed['streams']]
        for duration in durations:
            self.assertAlmostEqual(duration, 1.5, delta=.05)
        self.assertLess(max(durations) - min(durations), .05)
        # Correct visual content follows the requested source intervals and order.
        for second, channel in ((.25, 2), (.75, 1), (1.25, 0)):
            pixel = self.run_process(['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(second), '-i', output,
                                      '-vf', 'scale=1:1', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
            self.assertGreater(pixel[channel], max(pixel[x] for x in range(3) if x != channel) + 50)
        pcm = array.array('h', self.run_process(['ffmpeg', '-nostdin', '-v', 'error', '-i', output,
                                               '-map', '0:a:0', '-ar', '48000', '-ac', '1', '-f', 's16le', 'pipe:1']))
        for start, frequency in ((.1, 880), (.6, 0), (1.1, 440)):
            samples = pcm[int(start * 48000):int((start + .3) * 48000)]
            rms = math.sqrt(sum(x*x for x in samples) / len(samples))
            if not frequency:
                self.assertLess(rms, 5)
            else:
                self.assertGreater(rms, 500)
                rising = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
                self.assertAlmostEqual(rising / .3, frequency, delta=8)
        edit.reset_edit('EDIT_REAL', 1, 1)
        reset = merge.merge_episode('EDIT_REAL', 1, dst=str(self.data / 'original_order.mp4'))
        probe = json.loads(self.run_process(['ffprobe', '-v', 'error', '-show_format', '-of', 'json', reset]))
        self.assertAlmostEqual(float(probe['format']['duration']), 4, delta=.05)
        self.assertEqual([hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.voice, self.silent)],
                         self.initial_hashes)

    def test_actual_regenerated_file_changes_version(self):
        copy_path = self.root / 'regenerated_source.mp4'
        copy_path.write_bytes(self.voice.read_bytes())
        self.story['saga']['episodes'][0]['timeline']['scenes'][0]['segments'][0]['video'] = str(copy_path)
        source = edit.get_edit('EDIT_REAL')['sources'][0]
        edit.save_edit('EDIT_REAL', 1, [dict(segment_no=1, fingerprint=source['fingerprint'],
                                           **{'in': 0, 'out': 1})], 0)
        copy_path.write_bytes(self.silent.read_bytes())
        with self.assertRaisesRegex(ValueError, '版本已改变'):
            edit.render_plan('EDIT_REAL', 1)


if __name__ == '__main__':
    unittest.main()
