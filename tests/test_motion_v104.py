"""Targeted CPU regression: no model calls, no production data writes."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from cores import motion_core as m, motion_timeline as tl, motion_prompts as mp


class Handle:
    def __init__(self): self.checks = 0
    def check_pause(self): self.checks += 1
    def step(self, *args, **kwargs): pass
    def set(self, **kwargs): pass


class MotionTests(unittest.TestCase):
    def test_disjoint_coverage_and_shot_context(self):
        for total in [1, 23, 24, 25, 95, 96, 107, 124, 125, 148, 149, 415, 572, 722, 2880]:
            for cuts in [[], [23, 24, 96, 101, 102, 200]]:
                with self.subTest(total=total, cuts=cuts):
                    parts = tl.plan(total, cuts)
                    self.assertEqual(sum(p['frames'] for p in parts), total)
                    cursor = 0
                    for p in parts:
                        self.assertEqual(p['start_frame'], cursor)
                        self.assertGreaterEqual(p['context_start'], p['shot_start'])
                        self.assertLessEqual(cursor+p['frames'], p['shot_end'])
                        self.assertEqual((p['model_frames']-5) % 17, 0)
                        cursor += p['frames']

    def test_single_frame_tail_merged(self):
        self.assertEqual([p['frames'] for p in tl.plan(125)], [125])
        self.assertEqual([p['frames'] for p in tl.plan(249)], [124, 125])

    def test_label_validation(self):
        good = '\n'.join([mp.HEADS[0], '<Video 1> is the source. <Subject 1> from <Picture 1>.',
            *mp.HEADS[1:3], 'attribute_transfer', mp.HEADS[3], '[Shot 1] Move.', *mp.HEADS[4:]])
        self.assertFalse(mp.errors(good, 1))
        self.assertTrue(mp.errors(good+' <Picture 2>', 1))
        self.assertTrue(mp.errors(good+' [Shot 2]', 1))

    def test_validate_second_person_and_seed_zero(self):
        with patch.object(m, 'media_path', side_effect=lambda v, video=False, optional=False: Path(v) if v else None), patch.object(m, 'probe', return_value={'format': {'duration': '9'}, 'streams': [{'codec_type':'video'}]}):
            base = {'video':'v.mp4', 'character':'a.png', 'range_mode':'full', 'seed':0}
            self.assertEqual(m.validate(base)['seed'], 0)
            with self.assertRaisesRegex(ValueError, '哪个人'):
                m.validate(dict(base, character2='b.png'))
            val = m.validate(dict(base, character2='b.png', source_person='blue shirt', source_person2='grey shirt'))
            self.assertEqual(val['steps'], 10)

    def test_cpu_complete_run_cut_and_original_audio(self):
        from models import anima_client, h3_client, gpu_manager
        from cores import gen_history
        with tempfile.TemporaryDirectory(prefix='motion104_') as tmp:
            root = Path(tmp)
            source = root/'input.mp4'
            m.ffmpeg(['-f','lavfi','-i','testsrc2=s=96x160:r=24:d=2.3',
                      '-f','lavfi','-i','sine=frequency=330:sample_rate=48000:duration=2.3',
                      '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',source])
            pic = root/'person.png'
            m.ffmpeg(['-i',source,'-frames:v',1,pic])
            expected = __import__('math').ceil(float(m.probe(source)['format']['duration'])*24-1e-6)
            calls = []
            def render(mode, prompt, **kwargs):
                calls.append(kwargs)
                return {'output_path': kwargs['reference_videos'][0]}
            out = root/'outputs'
            with patch.object(m, 'ROOT', root), patch.object(m, 'OUTPUT', out), \
                 patch.object(anima_client, 'local_guard'), patch.object(anima_client, 'idle_guard'), \
                 patch.object(gpu_manager, 'release'), patch.object(h3_client, '_sized', return_value=(96,160)), \
                 patch.object(h3_client, 'generate', side_effect=render), patch.object(m, 'release_idle_cache'), \
                 patch.object(gen_history, 'record'), patch.object(tl, 'detect_cuts', return_value=[1,25]), \
                 patch.object(mp, 'ask', side_effect=AssertionError('Manual prompt must skip AI')):
                result = m.run(Handle(), {'inputs':{'video':str(source), 'character':str(pic),
                    'character2':str(pic), 'prompt':'manual unchanged', 'range_mode':'full', 'seed':0}})
            self.assertEqual(result['frames'], expected)
            self.assertEqual(result['audio_mode'], 'source')
            self.assertEqual(len(calls), 3)
            for filename in [result['output_path'], result['comparison']]:
                info = m.probe(filename)
                video = next(s for s in info['streams'] if s['codec_type']=='video')
                self.assertEqual(video['r_frame_rate'], '24/1')
                self.assertEqual(int(video['nb_frames']), expected)
                self.assertTrue(any(s['codec_type']=='audio' for s in info['streams']))
            self.assertTrue(all(len(c['images'])==2 and c['steps']==10 for c in calls))


if __name__ == '__main__': unittest.main(verbosity=2)
