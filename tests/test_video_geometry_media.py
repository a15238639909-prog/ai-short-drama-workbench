import json
from fractions import Fraction
from pathlib import Path
import subprocess
import tempfile
import unittest
from cores import video_geometry as geo, story_video_media as media


class RatioMediaTests(unittest.TestCase):
    def test_portrait_and_off_grid_ratio_survive_adoption_and_join(self):
        root=Path(tempfile.mkdtemp(prefix='ratio_media_retained_'))
        for width,height in [(900,1600),(1001,777)]:
            g=geo.dimensions(width,height)
            work=root/str(width);work.mkdir()
            src=work/'generated.mp4';raw=work/'raw.mp4'
            subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i',
                'testsrc2=s=%dx%d:r=24:d=1' % (g['width'],g['height']),'-c:v','libx264','-preset','ultrafast',str(src)],check=True)
            geo.preserve_display_ratio(src,raw,g)
            adopted=media.adopt(raw,work/'adopted',end_seconds=1)
            result=media.join([adopted],work/'movie')
            video=next(s for s in media.probe(result['output_path'])['streams'] if s['codec_type']=='video')
            self.assertEqual(Fraction(video['display_aspect_ratio'].replace(':','/')),Fraction(width,height))
            self.assertTrue(result['full_decode_passed'])
            self.assertTrue(adopted['anchor_matches_clip_last_frame'])
