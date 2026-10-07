"""Portable regression checks: no model calls and no private test media."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from cores import motion_core as motion
from models import anima_client as anima


class MotionReleaseTests(unittest.TestCase):
    def test_contiguous_native_grid(self):
        for total in (1, 24, 95, 96, 107, 124, 125, 352, 366, 480, 552, 1440, 2880):
            with self.subTest(total=total):
                plan = motion.segment_plan(total)
                self.assertEqual(sum(p['frames'] for p in plan), total)
                self.assertEqual([n for p in plan for n in range(p['start_frame'], p['start_frame']+p['frames'])], list(range(total)))
                for p in plan:
                    self.assertEqual((p['model_frames']-5) % 17, 0)
                    self.assertTrue(107 <= p['model_frames'] <= 124)
                    self.assertEqual(p['model_frames'], p['frames']+p['leading_frames']+p['padding_frames'])
                    self.assertGreaterEqual(p['context_start'], 0)

    def test_long_video_segment_counts(self):
        self.assertEqual([len(motion.segment_plan(s*24)) for s in (20,60,120)], [4,12,24])

    def test_23_second_tail(self):
        plan = motion.segment_plan(552)
        self.assertEqual([p['frames'] for p in plan], [124,124,124,124,56])
        self.assertEqual((plan[-1]['context_start'],plan[-1]['leading_frames']), (445,51))

    def test_default_ten_steps(self):
        info = {'format':{'duration':'23'},'streams':[{'codec_type':'video'}]}
        with patch.object(motion,'media_path',side_effect=lambda value,*args:Path(value) if value else None), patch.object(motion,'probe',return_value=info):
            data=motion.validate({'video':'source.mp4','character':'character.png','range_mode':'full',
                                  'character_details':'short brown hair, blue jacket','seed':42})
        self.assertEqual((data['steps'],data['size_tier'],data['seconds']), (10,'0.4',23))

    def test_reference_roles(self):
        for scene in (False,True):
            prompt=motion.reference_prompt(124/24,scene,'brown hair, blue jacket','stone bridge')
            self.assertIn('<Picture 1>',prompt)
            self.assertIn('<Video 1>',prompt)
            self.assertEqual('<Picture 2>' in prompt,scene)


class AnimaReleaseTests(unittest.TestCase):
    def test_english_only(self):
        self.assertEqual(anima.english_prompt(' blue sky, mountains '),'blue sky, mountains')
        for value in ('','中文需求'):
            with self.assertRaises(ValueError):
                anima.english_prompt(value)

    def test_layout_description(self):
        sheet='front full-body view, right-facing profile full-body view, back view, left panel'
        self.assertTrue(anima.sheet_layout_valid(sheet))
        self.assertFalse(anima.sheet_layout_valid(sheet,portrait=True))
        self.assertTrue(anima.sheet_layout_valid(sheet+', headshot',portrait=True))

    def test_independent_workflow(self):
        workflow=anima.build_workflow('stone bridge, blue sky',1024,1024,seed=42)
        self.assertEqual(workflow['11']['inputs']['text'],'stone bridge, blue sky')
        self.assertEqual(workflow['63']['inputs']['seed'],42)
        self.assertEqual(workflow['28']['inputs']['width'],1024)
        with self.assertRaises(ValueError):
            anima.build_workflow('blue sky',4096,4096)


class ReferenceFileTests(unittest.TestCase):
    def test_only_adopted_nonempty_files(self):
        from cores import asset_core, reference_ready
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'present.png').write_bytes(b'fixture')
            (root/'empty.png').touch()
            records=[{'status':'adopted','kind':'character','owner_id':'valid','path':str(root/'present.png')},
                     {'status':'adopted','kind':'character','owner_id':'missing','path':str(root/'missing.png')},
                     {'status':'adopted','kind':'character','owner_id':'empty','path':str(root/'empty.png')},
                     {'status':'draft','kind':'character','owner_id':'draft','path':str(root/'present.png')}]
            with patch.object(asset_core,'list_assets',return_value=records):
                self.assertEqual(set(reference_ready.adopted_paths('test','character')),{'valid'})


if __name__=='__main__':
    unittest.main()
