"""User-controlled two-stage workflow; all AI calls mocked, files retained."""
import copy
import shutil
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from tests.test_story_video_core import StoryFlowTests, observation_fixture, review_fixture
from cores import story_video_core as core, video_geometry as geometry


class StagedFlowTests(StoryFlowTests):
    def stage_inputs(self, image=False):
        self.task['inputs']['flow_version'] = 2
        if image:
            path=self.root/'portrait.png'
            Image.new('RGB',(900,1600),'blue').save(path)
            self.task['inputs']['opening_image']=str(path)
        self.mock(geometry,'preserve_display_ratio',side_effect=lambda src,dst,g:shutil.copy2(src,dst))
        old=self.vision.side_effect
        self.vision.side_effect=lambda system,payload,*args,**kwargs: (
            __import__('json').dumps(observation_fixture(0)) if payload.get('beat') is None else old(system,payload,*args,**kwargs))
        self.mock(core,'_busy')
        self.mock(core.runtime,'_queue')

    def approve(self):
        result=core.action(self.task['id'],'approve')
        self.assertEqual(result['status'],'queued')
        self.task['status']='running'

    def test_image_first_shows_story_even_when_review_rejects(self):
        self.stage_inputs(True)
        self.task['inputs']['aspect_ratio']='16:9'  # The image still controls portrait geometry.
        self.review_replies=[review_fixture(False)]
        with self.assertRaises(core.ControlStop):core.run_job(self.task['id'])
        self.assertEqual(self.task['status'],'waiting_user')
        self.assertEqual(self.manifest()['decision'],'story')
        self.assertFalse(self.manifest()['plan_review']['passed'])
        self.assertEqual(len(self.video_calls),0)
        self.assertEqual(len(self.revision_payloads),0)
        self.approve()
        result=core.run_job(self.task['id'])
        self.assertEqual(result['segments'],2)
        self.assertEqual([r['first_frame'] for r in self.video_calls][1],self.manifest()['segments'][0]['anchor_path'])
        g=self.video_calls[0]['geometry']
        self.assertEqual(g['width']*16,g['height']*9)

    def test_text_selected_ratio_survives_preview_and_continuation(self):
        self.stage_inputs()
        self.task['inputs']['aspect_ratio']='9:16'
        with self.assertRaises(core.ControlStop):core.run_job(self.task['id'])
        self.assertEqual(len(self.video_calls),1)
        first=copy.deepcopy(self.video_calls[0]['geometry'])
        self.assertEqual(first['ratio'],'9:16')
        self.assertEqual(first['width']*16,first['height']*9)
        self.approve()
        core.run_job(self.task['id'])
        self.assertEqual(len(self.video_calls),2)
        self.assertEqual(self.video_calls[1]['geometry'],first)
        self.assertEqual(self.manifest()['geometry'],first)
        self.assertEqual(self.join.call_count,1)

    def test_text_preview_then_continue_and_manual_redo(self):
        self.stage_inputs()
        original=self.vision.side_effect
        def warn(system,payload,*args,**kwargs):
            import json
            value=json.loads(original(system,payload,*args,**kwargs))
            if payload['stage']=='segment':value.update(retry_required=True,retry_reason='Consider redo')
            return json.dumps(value)
        self.vision.side_effect=warn
        with self.assertRaises(core.ControlStop):core.run_job(self.task['id'])
        self.assertEqual(self.manifest()['decision'],'preview')
        self.assertEqual(len(self.video_calls),1)  # Warning never silently renders another take.
        first=self.manifest()['segments'][0]
        before=Path(first['raw_path']).read_bytes()
        self.approve()
        core.run_job(self.task['id'])
        self.assertEqual(len(self.video_calls),2)
        self.assertEqual(Path(first['raw_path']).read_bytes(),before)
        self.task['status']='completed'
        core.action(self.task['id'],'redo',1)
        self.assertEqual(self.manifest()['segments'],[])
        self.assertEqual(len(self.manifest()['history']),1)
        self.task['status']='running'
        core.run_job(self.task['id'])
        self.assertEqual(len(self.video_calls),4)
        self.assertNotEqual(first['raw_path'],self.manifest()['segments'][0]['raw_path'])
        self.assertEqual(Path(first['raw_path']).read_bytes(),before)

    def test_resume_cannot_skip_story_approval(self):
        self.stage_inputs(True)
        for _ in range(2):
            self.task['status']='running'
            with self.assertRaises(core.ControlStop):core.run_job(self.task['id'])
        self.assertEqual(len(self.video_calls),0)


class GeometryTests(__import__('unittest').TestCase):
    def test_text_ratio_validation_and_default(self):
        inputs={'input_mode':'text','story_direction':'女修恢复山泉后返回村庄'}
        self.assertEqual(core.validate_inputs(inputs)['aspect_ratio'],'16:9')
        for ratio in core.ASPECT_RATIOS:
            with self.subTest(ratio=ratio):
                value=core.validate_inputs(dict(inputs,aspect_ratio=ratio))
                self.assertEqual(value['aspect_ratio'],ratio)
                w,h=map(int,ratio.split(':'))
                g=geometry.dimensions(w,h)
                self.assertEqual(g['width']*h,g['height']*w)
        with self.assertRaisesRegex(ValueError,'视频比例'):
            core.validate_inputs(dict(inputs,aspect_ratio='unknown'))

    def test_native_grid_and_common_aspects(self):
        for w,h in [(900,1600),(1920,1080),(2880,1632),(1000,1000),(4000,3000),(1001,777)]:
            g=geometry.dimensions(w,h)
            self.assertEqual(g['width']%32,0)
            self.assertEqual(g['height']%32,0)
            self.assertLessEqual(g['width']*g['height'],1152*640)
            if (w,h)!=(1001,777):self.assertEqual(g['width']*h,g['height']*w)
