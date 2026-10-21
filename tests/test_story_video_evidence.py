"""Actual frame selection for the bounded adoption-evidence supplement."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from cores import story_video_media as media


class EvidenceFramesTests(unittest.TestCase):
    def test_supplement_uses_real_adopted_pts_and_rejects_outside_frames(self):
        root = Path(tempfile.mkdtemp(prefix='story_evidence_retained_'))
        source = root/'source.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i',
            'testsrc2=size=128x72:rate=24:duration=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
            '-frames:v', '24', str(source)], check=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        before = media._hash(source)
        frames = media.sample_at_times(source, root/'extra', [.249, .501, 23/24])
        self.assertEqual([row['frame_index'] for row in frames], [6, 12, 23])
        self.assertAlmostEqual(frames[0]['time_seconds'], .25, places=4)
        self.assertAlmostEqual(frames[-1]['time_seconds'], 23/24, places=4)
        self.assertTrue(all(Path(row['path']).is_file() for row in frames))
        with self.assertRaises(ValueError):
            media.sample_at_times(source, root/'outside', [1])
        with self.assertRaises(ValueError):
            media.sample_at_times(source, root/'too_many', [0, .2, .4, .6])
        self.assertEqual(media._hash(source), before)


if __name__ == '__main__':
    unittest.main()
