"""Targeted story-core contracts and recovery checks, entirely without models."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cores import story_video_core as core
from cores import story_video_media as media
from cores import story_video_prompts as prompts
from cores import story_video_worker as worker
from models import story_video_client as client


def plan_fixture():
    return {
        'title': 'A clear spring', 'logline': 'Two connected actions.', 'ending': 'Water runs freely.',
        'characters': [{'id': 'c1', 'name': 'Yunlan', 'appearance': 'adult woman in blue', 'role': 'lead'}],
        'props': [{'id': 'p1', 'name': 'ring', 'appearance': 'green jade', 'count': 1}],
        'invariants': ['one woman and one ring'],
        'beats': [dict(index=n, seconds=8, goal='advance %d' % n, action='turn the ring',
                       turn='water moves', emotion='focused', ending='planned ending %d' % n,
                       cast=['c1'], dialogue=[]) for n in (1, 2)],
    }


def review_fixture(passed=True):
    return {'passed': passed, 'issues': [] if passed else [
        {'beat': 1, 'category': 'causality', 'reason': 'The visible transition is unclear.',
         'repair_direction': 'Make this transition physically readable.'}],
        'strengths': ['The intended ending is clear.']}


def observation_fixture(index, segment=False):
    return {
        'actual_visible': 'actual endpoint %d: ring stays on the stone rim' % index,
        'unknowns': [],
        'characters': [dict(id='c1', appearance='adult woman in blue', position='beside dais',
                            pose='hand lowered', hands='empty', visibility='visible', certainty='observed')],
        'props': [dict(id='p1', appearance='green jade', count=1, holder='stone', location='near rim',
                       visibility='visible', certainty='observed')],
        'scene': 'connected terrace', 'water': 'water remains visibly flowing',
        'actual_visual_style': 'cinematic 3D animated film', 'style_alignment': 'matching',
        'actions': ['turn observed'] if segment else [],
        'completed': ['visible turn'] if segment else [],
        'missing': [], 'continuity_risks': [], 'beat_status': 'achieved' if segment else 'unknown',
        'keep_end_seconds': 8 if segment else None, 'keep_end_reason': 'last supplied evidence',
    }


def written_fixture(payload):
    dialogue = [dict(line, start=1, end=3, delivery='The adult woman speaks quietly while turning toward her companion')
                for line in payload['beat']['dialogue']]
    return dict(integrated_multimodal_description='[Shot 1] Cinematic 3D, the woman turns the ring.',
                overall_soundscape='Water flows and cloth rustles.', non_diegetic_music='N/A',
                expected_end_state='planned target only', brief='转环',
                timeline=[dict(start=0, end=8, action='turn', visible_result='flow changes')],
                dialogue=dialogue, continuity_notes=[])


class StoryContractsTests(unittest.TestCase):
    def plan_request(self):
        return prompts.plan_request('疏通山泉并完成结局', '3D film', 2, 8)

    def test_schema_types_bounds_required_and_unknown_fields(self):
        schema = {'type': 'object', 'properties': {
            'n': {'type': 'integer', 'minimum': 1, 'maximum': 2},
            'items': {'type': 'array', 'items': {'type': 'string', 'minLength': 1},
                      'minItems': 1, 'uniqueItems': True}},
            'required': ['n', 'items'], 'additionalProperties': False}
        core.validate_schema({'n': 1, 'items': ['a']}, schema)
        for value in ({'n': True, 'items': ['a']}, {'n': 3, 'items': ['a']},
                      {'n': 1}, {'n': 1, 'items': ['a', 'a']},
                      {'n': 1, 'items': ['']}, {'n': 1, 'items': ['a'], 'extra': 1}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                core.validate_schema(value, schema)
        with self.assertRaises(ValueError):
            core.validate_schema(float('nan'), {'type': 'number'})

    def test_plan_indices_cast_identity_and_speaker(self):
        _, payload, schema = self.plan_request()
        plan = plan_fixture()
        core.validate_schema(plan, schema)
        core.validate_plan(plan, payload)
        core.validate_plan_review(review_fixture(True), {})
        core.validate_plan_review(review_fixture(False), {})
        for invalid_review in (dict(review_fixture(False), passed=True), dict(review_fixture(True), passed=False)):
            with self.assertRaises(ValueError):
                core.validate_plan_review(invalid_review, {})
        changes = [lambda p: p['beats'][1].update(index=1),
                   lambda p: p['characters'].append(copy.deepcopy(p['characters'][0])),
                   lambda p: p['beats'][0].update(cast=['ghost']),
                   lambda p: p['beats'][0].update(seconds=7),
                   lambda p: p['beats'][0].update(dialogue=[{'speaker': 'ghost', 'text': '走吧。'}])]
        for change in changes:
            invalid = copy.deepcopy(plan)
            change(invalid)
            with self.subTest(change=change), self.assertRaises(ValueError):
                core.validate_plan(invalid, payload)
        for action in ("她说'放心。'后转环", '她低声说放心，随后转环'):
            invalid = copy.deepcopy(plan)
            invalid['beats'][1]['dialogue'] = [{'speaker': 'c1', 'text': '放心。'}]
            invalid['beats'][0]['action'] = action
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, '其他段原话'):
                core.validate_plan(invalid, payload)

    def test_short_dialogue_can_also_be_an_unspoken_action_verb(self):
        plan = plan_fixture()
        plan['beats'][0]['dialogue'] = [{'speaker': 'c1', 'text': '稳住'}]
        plan['beats'][1]['action'] = '前爪稳住持环的手腕'
        core.validate_plan(plan, self.plan_request()[1])

    def test_written_timing_dialogue_and_reference_contracts(self):
        plan = plan_fixture()
        plan['beats'][0]['dialogue'] = [{'speaker': 'c1', 'text': '走吧。'}]
        _, payload, schema = prompts.write_request(plan, plan['beats'][0], {}, 8, 'i2va')
        valid = written_fixture(payload)
        core.validate_schema(valid, schema)
        core.validate_written(valid, payload)
        self.assertNotIn('prompt', valid)
        compiled = prompts.compile_written(valid, payload)
        self.assertTrue(compiled['prompt'].startswith(prompts.I2VA_FIRST_FRAME))
        self.assertIn('<d>[Chinese] 走吧。</d>', compiled['prompt'])
        natural_line = [{'speaker': 'c1', 'text': '雨停之前我们再走', 'start': 4, 'end': 6}]
        core._dialogue(natural_line, payload['timing_budget'], 8)
        natural_line[0]['end'] = 4.7
        with self.assertRaisesRegex(ValueError, '需要约1.78秒，实际0.70秒'):
            core._dialogue(natural_line, payload['timing_budget'], 8)
        changes = [
            lambda x: x['timeline'].append(dict(start=7, end=8, action='overlap', visible_result='bad')),
            lambda x: x['timeline'][0].update(end=9),
            lambda x: x['dialogue'][0].update(end=1.1),
            lambda x: x['dialogue'][0].update(text='擅改原话'),
            lambda x: x.update(integrated_multimodal_description=x['integrated_multimodal_description']+' <d>[Chinese] 多了一句。</d>'),
            lambda x: x.update(integrated_multimodal_description=x['integrated_multimodal_description']+' {{D2}}'),
            lambda x: x.update(integrated_multimodal_description=x['integrated_multimodal_description']+' She says 走吧。'),
            lambda x: x['dialogue'][0].update(delivery=''),
            lambda x: x.update(integrated_multimodal_description=x['integrated_multimodal_description']+' <Picture 2>'),
            lambda x: x.update(integrated_multimodal_description=x['integrated_multimodal_description']+' <Subject 1>'),
            lambda x: x.update(integrated_multimodal_description=x['integrated_multimodal_description']+' <Video 1>'),
            lambda x: x.update(integrated_multimodal_description=x['integrated_multimodal_description']+
                ' [Shot 2] At 00:08.000, the camera cuts to water.'),
            lambda x: x.update(overall_soundscape='timeline: copied metadata'),
        ]
        for change in changes:
            invalid = copy.deepcopy(valid)
            change(invalid)
            with self.subTest(change=change), self.assertRaises(ValueError):
                core.validate_written(invalid, payload)
        text_payload = prompts.write_request(plan, plan['beats'][0], None, 8, 't2va')[1]
        bad = written_fixture(text_payload)
        bad['integrated_multimodal_description'] += ' <Audio 1>'
        with self.assertRaises(ValueError):
            core.validate_written(bad, text_payload)


class FakeHandle:
    def __init__(self, case, tid):
        self.case, self.tid = case, tid

    def set(self, **values):
        self.case.task.update(copy.deepcopy(values))

    def step(self, label, done, total, detail=''):
        self.case.task.update(label=label, done=done, total=total, detail=detail)
        if self.case.after_step:
            self.case.after_step(label, done)


class StoryFlowTests(unittest.TestCase):
    def setUp(self):
        # Keep tiny fixtures and traces for explicit recycling; never rmtree.
        self.root = Path(tempfile.mkdtemp(prefix='story_core_zero_'))
        self.folder = self.root/'job'
        self.folder.mkdir()
        self.task = {'id': 'story-test', 'kind': core.KIND, 'status': 'running',
                     'checkpoint': {'story_video_folder': str(self.folder)},
                     'inputs': dict(story_direction='一位女修恢复流水后离开', style='3D film',
                                    segment_count=2, segment_seconds=8, opening_image='')}
        self.after_step = None
        self.writer_states, self.video_calls, self.vision_calls = [], [], []
        self.review_payloads, self.revision_payloads, self.review_replies = [], [], []
        self.patches = []
        self.mock(core.runtime, 'load', side_effect=lambda tid: copy.deepcopy(self.task))
        self.mock(core.runtime, 'save', side_effect=self.save_task)
        self.mock(core.runtime, 'TaskHandle', side_effect=lambda tid: FakeHandle(self, tid))
        self.mock(core, '_save', side_effect=self.save_json)
        self.mock(core, '_url', side_effect=lambda p: '/mock/'+Path(p).name)
        self.text = self.mock(client, 'text', side_effect=self.model_text)
        self.vision = self.mock(client, 'vision', side_effect=self.model_vision)
        self.video = self.mock(client, 'video', side_effect=self.model_video)
        self.mock(client, 'preserve_release')
        self.mock(media, 'sample_frames', side_effect=self.samples)
        self.mock(media, 'adopt', side_effect=self.adopt)
        self.join = self.mock(media, 'join', return_value={'video_url': '/mock/final.mp4', 'duration': 16})
        self.mock(Path, 'unlink', side_effect=AssertionError('no permanent unlink'))
        self.patches.append(patch('shutil.rmtree', side_effect=AssertionError('no rmtree')))
        self.patches[-1].start()
        self.patches.append(patch('builtins.print'))
        self.patches[-1].start()
        self.addCleanup(self.stop_patches)

    def mock(self, obj, name, *args, **kwargs):
        item = patch.object(obj, name, *args, **kwargs)
        self.patches.append(item)
        return item.start()

    def stop_patches(self):
        for item in reversed(self.patches):
            item.stop()

    def save_task(self, value):
        self.task = copy.deepcopy(value)

    @staticmethod
    def save_json(path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def model_text(self, system, payload, trace_dir, **kwargs):
        Path(trace_dir).mkdir(parents=True, exist_ok=True)
        if payload.get('stage') == 'review':
            self.review_payloads.append(copy.deepcopy(payload))
            return json.dumps(self.review_replies.pop(0) if self.review_replies else review_fixture())
        if 'previous_draft' in payload:
            self.revision_payloads.append(copy.deepcopy(payload))
            revised = plan_fixture()
            revised['title'] = 'A locally revised clear spring'
            return json.dumps(revised)
        if 'count' in payload:
            return json.dumps(plan_fixture())
        self.writer_states.append(copy.deepcopy(payload))
        return json.dumps(written_fixture(payload), ensure_ascii=False)

    def model_vision(self, system, payload, images, trace_dir, **kwargs):
        Path(trace_dir).mkdir(parents=True, exist_ok=True)
        self.vision_calls.append(copy.deepcopy(payload))
        value = observation_fixture(payload['beat']['index'], payload['stage'] == 'segment')
        if payload['stage'] == 'segment':
            value.update(retry_required=False, retry_reason='No critical failure.')
        return json.dumps(value)

    def model_video(self, prompt, **kwargs):
        self.video_calls.append(copy.deepcopy(kwargs))
        source = self.root/('model_%d.mp4' % len(self.video_calls))
        source.write_bytes(b'retained model fixture')
        return {'output_path': str(source), 'frames': 192, 'seconds': 8}

    def samples(self, video, folder, **kwargs):
        folder.mkdir(parents=True, exist_ok=True)
        result = []
        for index, seconds in enumerate((0.25, 7.75)):
            path = folder/('%d.png' % index)
            path.write_bytes(b'fixture')
            result.append({'path': str(path), 'time_seconds': seconds})
        return result

    def adopt(self, video, folder, **kwargs):
        folder.mkdir(parents=True, exist_ok=True)
        anchor = folder/'anchor.png'
        anchor.write_bytes(b'actual endpoint fixture')
        result = folder/'adopted.mp4'
        result.write_bytes(b'retained adopted fixture')
        return {'anchor_path': str(anchor), 'video_path': str(result), 'duration': 8}

    def manifest(self):
        return json.loads((self.folder/'manifest.json').read_text(encoding='utf-8'))

    def test_two_segments_use_actual_endpoint_and_resume_skips_completed(self):
        output = core.run_job(self.task['id'])
        self.assertEqual(output['segments'], 2)
        self.assertEqual([p['mode'] for p in self.writer_states], ['t2va', 'i2va'])
        self.assertIsNone(self.writer_states[0]['actual_state'])
        state = self.writer_states[1]['actual_state']
        self.assertEqual(self.writer_states[1]['visual_style'], self.task['inputs']['style'])
        self.assertEqual(state['actual_visible'], 'actual endpoint 1: ring stays on the stone rim')
        self.assertEqual(state['water'], 'water remains visibly flowing')
        self.assertEqual(state['actual_visual_style'], 'cinematic 3D animated film')
        self.assertEqual(state['style_alignment'], 'matching')
        self.assertEqual(state['completed'], ['visible turn'])
        manifest = self.manifest()
        self.assertTrue(manifest['plan_review']['passed'])
        self.assertEqual(manifest['plan_review']['plan_signature'], core.plan_signature(manifest['plan']))
        self.assertEqual(len(self.review_payloads), 1)
        self.assertEqual(self.video_calls[1]['first_frame'], manifest['segments'][0]['anchor_path'])
        calls = (self.text.call_count, self.vision.call_count, self.video.call_count)
        core.run_job(self.task['id'])
        self.assertEqual(calls, (self.text.call_count, self.vision.call_count, self.video.call_count))
        self.assertEqual([row['index'] for row in self.manifest()['segments']], [1, 2])
        legacy_manifest = self.manifest()
        legacy_manifest.pop('plan_review')
        self.save_json(self.folder/'manifest.json', legacy_manifest)
        core.run_job(self.task['id'])
        self.assertEqual(calls, (self.text.call_count, self.vision.call_count, self.video.call_count))
        invalid_manifest = self.manifest()
        invalid_manifest['plan']['characters'][0]['appearance'] = None
        self.save_json(self.folder/'manifest.json', invalid_manifest)
        with self.assertRaisesRegex(RuntimeError, '已有视频的故事契约失效'):
            core.run_job(self.task['id'])
        self.assertEqual(calls, (self.text.call_count, self.vision.call_count, self.video.call_count))

    def test_failed_review_revises_once_then_resumes_at_second_review(self):
        self.review_replies = [review_fixture(False), review_fixture(True)]
        def pause_before_second_review(label, done):
            if label == '本地AI按审阅修改整稿（仅一次）':
                self.task['pause_requested'] = True
        self.after_step = pause_before_second_review
        with self.assertRaises(core.ControlStop):
            core.run_job(self.task['id'])
        pending = self.manifest()
        self.assertEqual(len(self.video_calls), 0)
        self.assertEqual(len(self.review_payloads), 1)
        self.assertEqual(len(self.revision_payloads), 1)
        self.assertEqual(pending['plan_review']['round'], 2)
        self.assertTrue(pending['plan_review']['pending'])
        self.assertEqual(self.revision_payloads[0]['previous_draft'], plan_fixture())
        self.assertEqual(self.revision_payloads[0]['review_feedback'], review_fixture(False))
        self.task.update(pause_requested=False, status='running')
        self.after_step = None
        output = core.run_job(self.task['id'])
        self.assertEqual(output['segments'], 2)
        self.assertEqual(len(self.revision_payloads), 1)
        self.assertEqual(len(self.review_payloads), 2)
        manifest = self.manifest()
        self.assertTrue(manifest['plan_review']['passed'])
        self.assertFalse(manifest['plan_review']['pending'])
        self.assertEqual(manifest['plan_review']['round'], 2)
        self.assertEqual(len(manifest['rejected_plan_versions']), 1)
        self.assertEqual(manifest['rejected_plan_versions'][0]['plan'], plan_fixture())
        self.assertEqual(manifest['plan_review']['plan_signature'], core.plan_signature(manifest['plan']))
        self.assertTrue(manifest['plan_source'].endswith('revision_1'+os.sep+'accepted.json'))
        calls = self.text.call_count
        core.run_job(self.task['id'])
        self.assertEqual(self.text.call_count, calls)

    def test_second_failed_review_stops_before_video_and_reuses_review(self):
        self.review_replies = [review_fixture(False), review_fixture(False)]
        with self.assertRaisesRegex(RuntimeError, '复审仍未通过'):
            core.run_job(self.task['id'])
        manifest = self.manifest()
        self.assertFalse(manifest['plan_review']['passed'])
        self.assertEqual(manifest['plan_review']['round'], 2)
        self.assertEqual(len(self.revision_payloads), 1)
        self.assertEqual(len(self.video_calls), 0)
        self.assertEqual(self.writer_states, [])
        self.assertEqual(len(manifest['rejected_plan_versions']), 1)
        calls = self.text.call_count
        with self.assertRaisesRegex(RuntimeError, '复审仍未通过'):
            core.run_job(self.task['id'])
        self.assertEqual(self.text.call_count, calls)
        self.assertEqual(len(self.revision_payloads), 1)
        self.assertEqual(len(self.video_calls), 0)

    def test_pause_preserves_completed_segment_and_resume_continues_once(self):
        def pause(label, done):
            if label == '已完成1/2段':
                self.task['pause_requested'] = True
        self.after_step = pause
        with self.assertRaises(core.ControlStop):
            core.run_job(self.task['id'])
        self.assertEqual(self.task['status'], 'paused')
        first = self.manifest()['segments'][0]
        before = Path(first['video_path']).read_bytes()
        self.assertEqual(len(self.video_calls), 1)
        self.task.update(pause_requested=False, status='running')
        self.after_step = None
        core.run_job(self.task['id'])
        self.assertEqual(len(self.video_calls), 2)
        self.assertEqual(Path(first['video_path']).read_bytes(), before)
        self.assertEqual(self.writer_states[-1]['actual_state']['actual_visible'], first['observation']['actual_visible'])

    def test_cancel_at_checkpoint_stays_cancelled_and_keeps_media(self):
        def cancel(label, done):
            if label == '已完成1/2段':
                self.task['cancel_requested'] = True
        self.after_step = cancel
        with self.assertRaises(core.ControlStop):
            core.run_job(self.task['id'])
        self.assertEqual(self.task['status'], 'cancelled')
        adopted = Path(self.manifest()['segments'][0]['video_path'])
        before = adopted.read_bytes()
        with self.assertRaises(core.ControlStop):
            core.run_job(self.task['id'])
        self.assertEqual(self.task['status'], 'cancelled')
        self.assertEqual(len(self.video_calls), 1)
        self.assertEqual(adopted.read_bytes(), before)

    def test_request_repairs_once_then_reuses_accepted_json(self):
        schema = {'type': 'object', 'properties': {'answer': {'type': 'string', 'minLength': 1}},
                  'required': ['answer'], 'additionalProperties': False}
        request = ('Reply JSON.', {}, schema)
        with patch.object(client, 'text', side_effect=['{"answer": 3}', '{"answer": "ok"}']) as ask:
            value = core.request_json(request, self.root/'request')
            self.assertEqual(value, {'answer': 'ok'})
            self.assertEqual(ask.call_count, 2)
            self.assertIn('correction', ask.call_args.args[1])
            self.assertEqual(core.request_json(request, self.root/'request'), value)
            self.assertEqual(ask.call_count, 2)
        with patch.object(client, 'text', return_value='{}') as ask:
            with self.assertRaisesRegex(RuntimeError, '修复仍未通过'):
                core.request_json(request, self.root/'failed_request')
            self.assertEqual(ask.call_count, 2)

    def test_worker_does_not_overwrite_paused_or_cancelled_status(self):
        with patch.object(worker.sys, 'argv', ['worker', self.task['id']]):
            self.task['status'] = 'paused'
            with patch.object(core, 'run_job', side_effect=core.ControlStop()):
                self.assertEqual(worker.main(), 0)
            self.assertEqual(self.task['status'], 'paused')
            self.task['status'] = 'cancelled'
            with patch.object(core, 'run_job', side_effect=RuntimeError('retained failure')), patch.object(worker.traceback, 'print_exc'):
                self.assertEqual(worker.main(), 1)
            self.assertEqual(self.task['status'], 'cancelled')
            self.assertEqual(self.task['error'], 'retained failure')


if __name__ == '__main__':
    unittest.main()
