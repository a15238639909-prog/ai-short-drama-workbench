"""Tiny synthetic-video checks; no local model is imported or started."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from cores import story_video_media as media


class StoryVideoMediaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Deliberately retained; tests never delete the user's media or fixtures.
        cls.folder = Path(tempfile.mkdtemp(prefix='story_video_media_'))
        cls.source = cls.folder / 'source.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i',
                        'testsrc2=size=128x72:rate=24:duration=1.5', '-f', 'lavfi', '-i',
                        'sine=frequency=440:sample_rate=48000:duration=1.5', '-map', '0:v:0', '-map', '1:a:0',
                        '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                        '-frames:v', '36', '-t', '1.5', str(cls.source)], check=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def test_complete_media_contract_and_reuse(self):
        original_hash = media._hash(self.source)
        info = media.probe(self.source)
        self.assertEqual(info['streams'][0]['avg_frame_rate'], '24/1')
        samples = media.sample_frames(self.source, self.folder / 'samples', seconds=1.5)
        self.assertEqual(len(samples), 6)
        self.assertEqual(samples[-1]['frame_index'], 35)
        self.assertTrue((self.folder / 'samples' / 'contact.jpg').is_file())
        adopted = media.adopt(self.source, self.folder / 'adopted', end_seconds=1)
        self.assertEqual(adopted['frame_index'], 23)
        self.assertEqual(adopted['kept_frames'], 24)
        self.assertEqual(adopted['kept_seconds'], 1)
        self.assertTrue(adopted['anchor_matches_clip_last_frame'])
        self.assertEqual(media._frame_hash(self.source, 23), media._frame_hash(adopted['anchor_path'], 0))
        self.assertEqual(media._frame_hash(adopted['clip_path'], 23), media._frame_hash(adopted['anchor_path'], 0))
        clip_hash = media._hash(adopted['clip_path'])
        repeated = media.adopt(self.source, self.folder / 'adopted', end_seconds=1)
        self.assertEqual(media._hash(repeated['clip_path']), clip_hash)
        with self.assertRaises(ValueError):
            media.adopt(self.source, self.folder / 'adopted', end_seconds=0.5)
        with self.assertRaises(ValueError):
            media.adopt(self.source, self.folder / 'outside', end_seconds=2)
        inputs = [{'raw_path': str(self.source), 'clip_path': adopted['clip_path'], 'kept_seconds': 1}] * 2
        result = media.join(inputs, self.folder / 'joined')
        self.assertEqual(result['seconds'], 2)
        self.assertEqual(result['raw_seconds'], 3)
        self.assertTrue(result['audio_consistency_passed'])
        self.assertTrue(result['audio_present'])
        self.assertTrue(result['full_decode_passed'])
        film_hash = media._hash(result['output_path'])
        again = media.join(inputs, self.folder / 'joined')
        self.assertEqual(media._hash(again['output_path']), film_hash)
        with self.assertRaises(ValueError):
            media.join(inputs[:1], self.folder / 'joined')
        self.assertEqual(media._hash(self.source), original_hash)
        report = json.loads(Path(result['report_path']).read_text(encoding='utf-8'))
        self.assertTrue(report['raw']['full_decode_passed'])
        self.assertTrue(report['film']['full_decode_passed'])
        print('RETAINED_SYNTHETIC_ARTIFACTS=' + str(self.folder))

    def test_fractional_frame_cut_uses_video_duration_not_aac_rounding(self):
        source = self.folder / 'source_32k_stereo.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-n', '-i', str(self.source), '-map', '0:v:0', '-map', '0:a:0',
                        '-c:v', 'copy', '-c:a', 'aac', '-ar', '32000', '-ac', '2', str(source)], check=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        first = media.adopt(source, self.folder / 'fractional_first', end_seconds=1.45)
        second = media.adopt(source, self.folder / 'fractional_second')
        self.assertEqual(first['kept_frames'], 35)
        self.assertEqual(second['kept_frames'], 36)
        joined = media.join([first, second], self.folder / 'fractional_join')
        video = next(stream for stream in media.probe(joined['output_path'])['streams'] if stream['codec_type'] == 'video')
        self.assertEqual(video['avg_frame_rate'], '24/1')
        self.assertEqual(int(video['nb_frames']), 71)
        self.assertEqual(joined['seconds'], 71 / 24)
        self.assertTrue(joined['full_decode_passed'])
        concat = (self.folder / 'fractional_join' / 'film_concat_frames_v2.txt').read_text(encoding='utf-8')
        self.assertIn('duration 1.458333333333', concat)


if __name__ == '__main__':
    unittest.main()
