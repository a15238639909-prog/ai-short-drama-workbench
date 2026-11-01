"""Bounded automatic story orchestration; all model and media work is mocked."""
import copy
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

os.environ['V41_NO_MODEL'] = '1'

from PIL import Image
from cores import runtime_reset, story_video_asr as asr
from cores import story_video_core as core, story_video_media as media
from cores import video_geometry as geometry
from tests.test_story_video_core import StoryFlowTests as _StoryFlowTests
from tests.test_story_video_core import observation_fixture, review_fixture


VALIDATION = Path(os.environ.get(
    'V41_STORY_AUTO_TEST_ROOT',
    r'D:\Claude code\story_auto_quality_20261017\validation')) / 'automatic_core'


class AutomaticStoryFlowTests(_StoryFlowTests):
    def setUp(self):
        isolated = VALIDATION / uuid.uuid4().hex
        isolated.mkdir(parents=True)
        state_patch = patch.object(runtime_reset, 'STATE', isolated/'runtime_reset.json')
        state_patch.start()
        self.addCleanup(state_patch.stop)
        previous_epoch = getattr(runtime_reset._local, 'generation', None)
        runtime_reset.bind_thread(None)
        self.addCleanup(runtime_reset.bind_thread, previous_epoch)
        fixture_root = isolated/'fixtures'
        fixture_root.mkdir()
        # The inherited fixture retains its files, and cannot touch real runtime state.
        with patch('tempfile.mkdtemp', return_value=str(fixture_root)):
            super().setUp()
        opening = self.root/'source.png'
        Image.new('RGB', (96, 54), 'blue').save(opening)
        self.task['inputs'].update(flow_version=2, quality_mode='automatic',
            opening_image=str(opening), aspect_ratio='16:9')
        self.storyboard_outcomes, self.visual_outcomes, self.audio_outcomes = [], [], []
        self.with_dialogue = False
        self.storyboard_calls, self.acceptance_calls = [], []
        self.sampled_clips, self.adopted_clips = [], []
        self.mock(geometry, 'preserve_display_ratio', side_effect=lambda src, dst, size: shutil.copy2(src, dst))
        self.extra = self.mock(media, 'sample_at_times', side_effect=self.extra_samples)
        self.audio = self.mock(asr, 'review', side_effect=self.audio_review)
        self.original_request_json = core.request_json
        self.mock(core, 'request_json', side_effect=self.request_json)

    def model_text(self, system, payload, trace_dir, **kwargs):
        reply = super().model_text(system, payload, trace_dir, **kwargs)
        if payload.get('review_policy') == 'core_errors_only_v1':
            value = json.loads(reply)
            for issue in value['issues']:
                issue.setdefault('severity', 'blocking')
                issue.setdefault('source_quote', payload['plan']['beats'][0]['action'])
            return json.dumps(value, ensure_ascii=False)
        if not self.with_dialogue:
            return reply
        value = json.loads(reply)
        if 'beats' in value:
            value['beats'][0]['dialogue'] = [{'speaker': 'c1', 'text': '先别碰它。'}]
        elif 'integrated_multimodal_description' in value:
            value['integrated_multimodal_description'] = (
                '[Shot 1] Cinematic 3D, the woman studies the ring during the first four seconds. '
                'During the final four seconds, she turns the ring and watches the water move.')
            value['timeline'] = [
                dict(start=0, end=4, action='study the ring', visible_result='notice the resting ring'),
                dict(start=4, end=8, action='turn the ring', visible_result='the water changes')]
        return json.dumps(value, ensure_ascii=False)

    def model_vision(self, system, payload, images, trace_dir, **kwargs):
        if payload['beat'] is None:
            Path(trace_dir).mkdir(parents=True, exist_ok=True)
            return json.dumps(observation_fixture(0))
        value = json.loads(super().model_vision(system, payload, images, trace_dir, **kwargs))
        take = next(part for part in Path(trace_dir).parts if '_take_' in part)
        value['actual_visible'] = 'actual endpoint ' + take
        return json.dumps(value)

    def adopt(self, video, folder, **kwargs):
        result = super().adopt(video, folder, **kwargs)
        result.update(clip_path=result['video_path'], frame_time_seconds=7.958333,
                      kept_frames=192, fps=24)
        self.adopted_clips.append(result['clip_path'])
        return result

    def samples(self, video, folder, **kwargs):
        self.sampled_clips.append(str(video))
        return super().samples(video, folder, **kwargs)

    def extra_samples(self, video, folder, targets):
        folder.mkdir(parents=True, exist_ok=True)
        rows = []
        for number, seconds in enumerate(targets):
            path = folder/('extra_%d.png' % number)
            path.write_bytes(b'additional adopted-clip evidence')
            rows.append(dict(path=str(path), time_seconds=seconds,
                             frame_index=round(seconds*24)))
        return rows

    def audio_review(self, clip_path, dialogue, directory):
        outcome = self.audio_outcomes.pop(0) if self.audio_outcomes else 'passed'
        return dict(status=outcome, issues=[] if outcome == 'passed' else
                    [{'reason': 'local recognizer unavailable'}], audio_listened=False)

    @staticmethod
    def storyboard_reply(payload, passed):
        return dict(request_signature=payload['request_signature'], passed=passed,
            issues=[] if passed else [dict(severity='blocking',
                reason='The current action omits a necessary cause.',
                correction='Make the current action and its visible result clear.')],
            dialogue_timing=[dict(line_index=index, relation='before', timeline_index=1,
                boundary='start', reason='The warning comes before turn the ring.')
                for index, _ in enumerate(payload['written']['dialogue'])],
            event_contract=[dict(id='required_turn', source_quote=payload['brief'],
                before='The ring rests in place.', action='The woman turns the ring.',
                after='The ring has visibly turned.', start=1, end=7, kind='transition')])

    @staticmethod
    def visual_reply(payload, outcome):
        options = outcome if isinstance(outcome, dict) else {}
        status = options.get('status', 'observed' if outcome is True else 'contradicted')
        first, last = payload['sample_records'][0], payload['sample_records'][-1]
        evidence = lambda row: [dict(sample_id=row['id'], visible='The ring position is visible.')]
        return dict(request_signature=payload['request_signature'], passed=status == 'observed',
            events=[dict(id=event['id'], status=status, confirmation_mode='decisive_result',
                before=evidence(first) if status == 'observed' else [], action=[],
                after=evidence(last) if status != 'insufficient_evidence' else [],
                explanation='The required result is visible.' if status == 'observed' else
                            'The required result is not established.')
                for event in payload['event_contract']],
            critical_issues=[], follow_up_times=options.get('follow_up_times', []))

    def request_json(self, request, directory, images=None, validator=None):
        payload, schema = request[1:]
        if payload.get('stage') == 'storyboard_review':
            self.storyboard_calls.append((Path(directory), copy.deepcopy(payload)))
            passed = self.storyboard_outcomes.pop(0) if self.storyboard_outcomes else True
            value = self.storyboard_reply(payload, passed)
        elif payload.get('stage') == 'segment_acceptance':
            self.acceptance_calls.append((Path(directory), copy.deepcopy(payload), list(images or [])))
            outcome = self.visual_outcomes.pop(0) if self.visual_outcomes else True
            value = self.visual_reply(payload, outcome)
        else:
            return self.original_request_json(request, directory, images=images, validator=validator)
        core.validate_schema(value, schema)
        if validator:
            validator(value, payload)
        self.save_json(Path(directory)/'accepted.json', value)
        return copy.deepcopy(value)

    def test_image_automatic_revises_and_reviews_whole_plan_before_rendering(self):
        self.review_replies = [review_fixture(False), review_fixture(True)]
        result = core.run_job(self.task['id'])
        manifest = self.manifest()
        self.assertEqual(result['segments'], 2)
        self.assertEqual(len(self.review_payloads), 2)
        self.assertEqual(len(self.revision_payloads), 1)
        self.assertEqual(manifest['plan_review']['round'], 2)
        self.assertTrue(manifest['plan_review']['passed'])
        self.assertEqual(manifest['approval_source'], 'local_plan_review')
        self.assertNotEqual(self.task['status'], 'waiting_user')
        self.assertNotIn('decision', manifest)
        self.assertTrue(manifest['quality_summary']['passed'])
        self.assertTrue(all(row['quality']['passed'] for row in manifest['segments']))

    def test_advisories_pass_without_rewrite_and_reach_only_relevant_writer(self):
        review = review_fixture(True)
        review['issues'] = [dict(beat=beat, category='capacity', severity='advisory',
            source_quote='turn the ring', reason='A softer transition could help.',
            repair_direction='Use a readable transition without changing the core action.')
            for beat in (0, 1, 2)]
        self.review_replies = [review]
        result = core.run_job(self.task['id'])
        self.assertEqual(result['segments'], 2)
        self.assertEqual(len(self.revision_payloads), 0)
        self.assertEqual(len(self.manifest()['plan_advisories']), 3)
        self.assertEqual([item['beat'] for item in self.writer_states[0]['plan_advisories']], [0, 1])
        self.assertEqual([item['beat'] for item in self.writer_states[1]['plan_advisories']], [0, 2])
        self.assertTrue(self.manifest()['quality_summary']['passed'])

    def test_second_whole_plan_rejection_warns_and_keeps_generating(self):
        self.review_replies = [review_fixture(False), review_fixture(False)]
        result = core.run_job(self.task['id'])
        self.assertEqual(len(self.review_payloads), 2)
        self.assertEqual(len(self.revision_payloads), 1)
        self.assertEqual(len(self.video_calls), 2)
        self.assertEqual(len(self.storyboard_calls), 2)
        self.assertEqual(result['quality_status'], 'completed_with_warnings')
        self.assertFalse(self.manifest()['quality_summary']['passed'])

    def test_rejected_take_repairs_only_current_segment_from_same_first_frame(self):
        self.visual_outcomes = [False, True, True]
        core.run_job(self.task['id'])
        manifest = self.manifest()
        self.assertEqual(len(self.video_calls), 3)
        self.assertEqual(self.video_calls[0]['first_frame'], self.video_calls[1]['first_frame'])
        self.assertEqual(self.writer_states[0]['actual_state'], self.writer_states[1]['actual_state'])
        self.assertIn('previous_take_feedback', self.writer_states[1])
        self.assertEqual(self.writer_states[2]['actual_state']['actual_visible'], 'actual endpoint 01_take_2')
        self.assertEqual(self.video_calls[2]['first_frame'], manifest['segments'][0]['anchor_path'])
        self.assertEqual([row['take'] for row in manifest['segments']], [2, 1])
        self.assertTrue((self.folder/'segments/01_take_1/raw.mp4').exists())
        self.assertEqual(manifest['quality_summary']['rejected_takes'], 1)

    def test_two_failed_takes_stay_exhausted_when_task_resumes(self):
        self.visual_outcomes = [False, False]
        for _ in range(2):
            output = core.run_job(self.task['id'])
            self.assertFalse(output['generation_completed'])
            self.assertEqual(output['missing_segments'], [1])
            self.assertEqual(len(self.video_calls), 3)
        manifest = self.manifest()
        self.assertEqual([row['index'] for row in manifest['segments']], [2])
        self.assertEqual([entry['status'] for entry in
                          manifest['quality_attempts']['1:1']['attempts'].values()],
                         ['rejected', 'rejected'])
        self.assertTrue((self.folder/'segments/02_take_1').exists())

    def test_legacy_unavailable_asr_keeps_slot_pending_and_other_slot_continues(self):
        self.audio_outcomes = ['unavailable']
        partial = core.run_job(self.task['id'])
        self.assertFalse(partial['generation_completed'])
        self.assertEqual(partial['missing_segments'], [1])
        self.assertEqual(len(self.video_calls), 2)
        self.assertEqual(len(self.acceptance_calls), 1)
        self.assertEqual(self.manifest()['quality_attempts']['1:1']['attempts']['1']['status'],
                         'audio_unavailable')
        first_frame = self.folder/'opening.png'
        initial_bytes = first_frame.read_bytes()
        first_frame.write_bytes(b'changed first frame for existing raw')
        try:
            with self.assertRaisesRegex(RuntimeError, '首帧或计划输入发生变化|分段续接输入已变化'):
                core.run_job(self.task['id'])
            self.assertEqual(len(self.video_calls), 2)
        finally:
            first_frame.write_bytes(initial_bytes)
        result = core.run_job(self.task['id'])
        self.assertEqual(result['segments'], 2)
        self.assertEqual(len(self.video_calls), 2)
        self.assertEqual(self.manifest()['segments'][0]['take'], 1)

    def test_storyboard_can_be_repaired_once_before_dispatch(self):
        self.with_dialogue = True
        self.storyboard_outcomes = [False, True, True]
        core.run_job(self.task['id'])
        self.assertEqual(len(self.video_calls), 2)
        self.assertEqual(len(self.writer_states), 3)
        self.assertIn('storyboard_feedback', self.writer_states[1])
        self.assertIn('previous_reply', self.writer_states[1])
        self.assertEqual(self.writer_states[0]['actual_state'], self.writer_states[1]['actual_state'])
        self.assertEqual([line['text'] for line in self.writer_states[0]['dialogue_slots']], ['先别碰它。'])
        self.assertEqual(self.writer_states[0]['dialogue_slots'], self.writer_states[1]['dialogue_slots'])
        for _, payload in self.storyboard_calls[:2]:
            self.assertEqual(len(payload['written']['dialogue']), 1)
            self.assertEqual(payload['written']['timeline'][1]['start'], 4)
        self.assertEqual(self.manifest()['segments'][0]['take'], 1)

    def test_legacy_blocked_storyboard_only_skips_its_slot_and_continues(self):
        self.storyboard_outcomes = [False, False]
        output = core.run_job(self.task['id'])
        self.assertFalse(output['generation_completed'])
        self.assertEqual(output['missing_segments'], [1])
        self.assertEqual([row['index'] for row in self.manifest()['segments']], [2])
        self.assertEqual(len(self.writer_states), 3)
        self.assertEqual(len(self.storyboard_calls), 3)
        self.assertEqual(len(self.video_calls), 1)
        self.assertEqual(self.audio.call_count, 1)
        self.assertEqual(self.manifest()['quality_attempts']['1:1']['attempts']['1']['status'],
                         'blocked_before_generation')

    def test_evidence_supplement_is_once_per_take_and_uses_only_adopted_clip(self):
        self.visual_outcomes = [
            dict(status='insufficient_evidence', follow_up_times=[4.0]),
            dict(status='insufficient_evidence', follow_up_times=[5.0]), True, True]
        core.run_job(self.task['id'])
        self.assertEqual(self.extra.call_count, 1)
        self.assertEqual(self.extra.call_args.args[0], self.adopted_clips[0])
        self.assertEqual(self.sampled_clips, self.adopted_clips)
        self.assertEqual([call.args[0] for call in self.audio.call_args_list], self.adopted_clips)
        first_take_reviews = [call for call in self.acceptance_calls if '01_take_1' in call[0].parts]
        self.assertEqual(len(first_take_reviews), 2)
        self.assertEqual(len(first_take_reviews[0][1]['sample_records']), 2)
        self.assertEqual(len(first_take_reviews[1][1]['sample_records']), 3)
        for _, payload, images in self.acceptance_calls:
            self.assertEqual(images, [row['path'] for row in payload['sample_records']])
            self.assertTrue(all(row['time_seconds'] <= 8 for row in payload['sample_records']))
        self.assertEqual(self.manifest()['segments'][0]['take'], 2)

    def test_passed_segments_resume_without_h3_or_repeating_acceptance(self):
        core.run_job(self.task['id'])
        before = (len(self.video_calls), len(self.acceptance_calls), self.audio.call_count)
        core.run_job(self.task['id'])
        self.assertEqual((len(self.video_calls), len(self.acceptance_calls), self.audio.call_count), before)
        self.assertEqual([row['index'] for row in self.manifest()['segments']], [1, 2])

    def test_changed_clip_anchor_first_frame_or_plan_blocks_resume_without_render(self):
        core.run_job(self.task['id'])
        original = self.manifest()
        clip = Path(original['segments'][0]['clip_path'])
        anchor = Path(original['segments'][0]['anchor_path'])
        first_frame = self.folder/'opening.png'
        original_bytes = {path: path.read_bytes() for path in (clip, anchor, first_frame)}
        for change in ('clip', 'anchor', 'first_frame', 'plan_signature'):
            manifest = copy.deepcopy(original)
            if change == 'plan_signature':
                manifest['segments'][0]['quality']['plan_signature'] = 'wrong-plan'
            else:
                {'clip': clip, 'anchor': anchor, 'first_frame': first_frame}[change].write_bytes(b'changed evidence')
            self.save_json(self.folder/'manifest.json', manifest)
            try:
                with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, '自动验收记录或输入已变化|分段续接输入已变化'):
                    core.run_job(self.task['id'])
                self.assertEqual(len(self.video_calls), 2)
            finally:
                for path, contents in original_bytes.items():
                    path.write_bytes(contents)


def load_tests(loader, tests, pattern):
    """Run this module's ten new cases, never the inherited legacy suite again."""
    return unittest.TestSuite(AutomaticStoryFlowTests(name)
        for name in sorted(AutomaticStoryFlowTests.__dict__) if name.startswith('test_'))


if __name__ == '__main__':
    unittest.main()
