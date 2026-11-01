"""Actual three-mode slot orchestration; failed middle slots never end the batch."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL']='1'
from cores import story_video_core as core, story_video_continuation as continuation
from cores import story_video_reference as reference, runtime_reset
from tests import test_story_video_mode_runtime as mode_fixtures
from tests import test_story_video_reference_continue as ref_fixtures


class SlotFailureContinueTests(unittest.TestCase):
    def mode_fixture(self,image=False,automatic=True):
        support=mode_fixtures.ModeRuntimeTests(methodName='runTest')
        case,requests=support.fixture(automatic=automatic,image=image)
        self.addCleanup(support.doCleanups)
        case.task['inputs']['segment_count']=3
        original=case.text.side_effect
        def text(*args,**kwargs):
            raw=original(*args,**kwargs)
            value=json.loads(raw)
            if 'beats' in value:
                beat=copy.deepcopy(value['beats'][-1]);beat['index']=3
                value['beats'].append(beat)
            return json.dumps(value,ensure_ascii=False)
        case.text.side_effect=text
        return case,requests

    def test_text_and_image_native_middle_failure_continue_from_last_confirmed_frame(self):
        for image in (False,True):
            with self.subTest(image=image):
                case,_=self.mode_fixture(image)
                generate=case.video.side_effect
                attempts=[]
                def video(prompt,**kwargs):
                    number=int(Path(kwargs['trace_dir']).parent.name[:2]);attempts.append(number)
                    if number==2:raise RuntimeError('本段提交回执暂时未知，保留原任务')
                    return generate(prompt,**kwargs)
                case.video.side_effect=video
                output=core.run_job(case.task['id']);manifest=case.manifest()
                self.assertEqual(attempts,[1,2,3])
                self.assertEqual([row['index'] for row in manifest['segments']],[1,3])
                self.assertFalse(output['generation_completed'])
                self.assertEqual(output['missing_segments'],[2])
                self.assertEqual(output['partial_policy'],continuation.PARTIAL_POLICY)
                self.assertEqual(case.join.call_count,1)
                self.assertEqual(case.video_calls[1]['first_frame'],manifest['segments'][0]['anchor_path'])
                third=[row for row in case.writer_states if row['beat']['index']==3][0]
                self.assertEqual(third['actual_state']['missing_segments'],[2])
                self.assertIn('不能把其计划当作已发生',str(third['actual_state']))
                preserved=copy.deepcopy(manifest['segments'])
                # Explicit resume recovers only the missing slot; later accepted
                # clips retain their original first-frame contract and are not rerendered.
                case.video.side_effect=generate
                result=core.run_job(case.task['id'])
                final=case.manifest()
                self.assertTrue(result['generation_completed'])
                self.assertEqual([row['index'] for row in final['segments']],[1,2,3])
                self.assertEqual(final['segments'][0],preserved[0])
                self.assertEqual(final['segments'][2],preserved[1])
                self.assertEqual(len(case.video_calls),3)

    def test_text_and_image_middle_writer_failure_preserves_original_story_and_continues(self):
        for image in (False,True):
            with self.subTest(image=image):
                case,_=self.mode_fixture(image)
                original=core._request_director
                def writer(request,directory):
                    if request[1]['beat']['index']==2:raise TimeoutError('one writer failed')
                    return original(request,directory)
                with patch.object(core,'_request_director',side_effect=writer):
                    output=core.run_job(case.task['id'])
                self.assertFalse(output['generation_completed'])
                self.assertEqual([row['index'] for row in case.manifest()['segments']],[1,3])
                self.assertEqual(len(case.video_calls),2)

    def test_legacy_template_keeps_original_profile_and_continues_after_native_failure(self):
        for image in (False, True):
            with self.subTest(image=image):
                case,_ = self.mode_fixture(image, automatic=False)
                case.task['inputs']['flow_version'] = 1
                generate = case.video.side_effect
                attempts = []
                def video(prompt, **kwargs):
                    number = int(Path(kwargs['trace_dir']).parent.name[:2])
                    attempts.append(number)
                    if number == 2:
                        raise RuntimeError('单段生成服务返回失败')
                    return generate(prompt, **kwargs)
                case.video.side_effect = video
                output = core.run_job(case.task['id'])
                self.assertEqual(attempts, [1, 2, 3])
                self.assertFalse(output['generation_completed'])
                self.assertEqual(output['missing_segments'], [2])
                self.assertNotIn('director_profile', case.manifest())

    def reference_fixture(self):
        original=ref_fixtures.fixture
        def fixture():
            folder,task,plan=original()
            task['inputs']['segment_count']=3
            beat=copy.deepcopy(plan['beats'][-1]);beat.update(index=3,dialogue=[])
            plan['beats'].append(beat)
            return folder,task,plan
        return patch.object(ref_fixtures,'fixture',side_effect=fixture)

    def test_reference_middle_native_failure_preserves_submission_and_continues(self):
        support=ref_fixtures.ReferenceContinueTests(methodName='runTest')
        generate=reference._generate_once
        attempts=[];failed=[False];first_result=[]
        def once(session,work,written,refs,geometry,index,take,signature):
            attempts.append(index)
            result=generate(session,work,written,refs,geometry,index,take,signature)
            if index==2 and not failed[0]:
                failed[0]=True
                raise RuntimeError('已提交，回执待恢复，不再重复提交')
            return result
        def resume(folder,task,output):
            first_result.append(copy.deepcopy(output))
            return reference.run_job(task['id'])
        with self.reference_fixture(),patch.object(reference,'_generate_once',side_effect=once):
            folder,_,manifest,output,requests,generated,joined=support._flow(after_run=resume)
        self.assertEqual(attempts,[1,2,3,2]);self.assertEqual(len(generated),3)
        self.assertEqual([row['index'] for row in manifest['segments']],[1,2,3])
        self.assertEqual(first_result[0]['missing_segments'],[2]);self.assertFalse(first_result[0]['generation_completed'])
        self.assertTrue(output['generation_completed']);self.assertEqual(len(joined),2)
        third=[row for row in requests if (row['payload'].get('beat') or {}).get('index')==3
               and 'dialogue_slots' in row['payload']][0]
        self.assertEqual(third['payload']['actual_state']['missing_segments'],[2])
        self.assertTrue((folder/'segments/02_take_1/raw_receipt.json').is_file())
        self.assertTrue((folder/'segments/02_take_1/video_submission.json').is_file())
        self.assertEqual(reference._read(folder/'segments/02_take_1/video_submission.json')['submissions'],1)

    def test_reference_middle_writer_failure_does_not_poison_later_reviews(self):
        support=ref_fixtures.ReferenceContinueTests(methodName='runTest')
        original=reference._request_reference_director
        def writer(session,request,*args,**kwargs):
            if request[1]['beat']['index']==2:
                error=TimeoutError('one writer failed');session.failure=error;raise error
            return original(session,request,*args,**kwargs)
        with self.reference_fixture(),patch.object(reference,'_request_reference_director',side_effect=writer):
            _,_,manifest,output,_,generated,joined=support._flow()
        self.assertEqual([row['index'] for row in manifest['segments']],[1,3])
        self.assertEqual(len(generated),2);self.assertEqual(len(joined),1)
        self.assertFalse(output['generation_completed'])

    def test_partial_publisher_requires_explicit_policy_and_valid_saved_clips(self):
        folder=Path(tempfile.mkdtemp(prefix='partial_story_publish_'))
        clip=folder/'clip.bin';clip.write_bytes(b'saved clip receipt')
        task=dict(inputs=dict(segment_count=3))
        manifest=dict(segments=[dict(index=1,clip_path=str(clip))],output=dict(
            generation_completed=False,partial_policy=continuation.PARTIAL_POLICY,
            segments=1,missing_segments=[2,3]))
        self.assertEqual(core._completed_output(manifest,task),manifest['output'])
        for changed in (dict(partial_policy='unknown'),dict(segments=3),dict(missing_segments=[])):
            with self.subTest(changed=changed),self.assertRaises(RuntimeError):
                core._completed_output(dict(manifest,output=dict(manifest['output'],**changed)),task)


if __name__=='__main__':unittest.main()
