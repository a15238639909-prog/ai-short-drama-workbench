"""Flow-v2 runtime contracts; all inference and media operations are synthetic."""
from contextlib import ExitStack, nullcontext
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from cores import story_video_core as core
from cores import story_video_reference as reference
from cores import story_video_asr as asr
from cores import story_video_media as media
from tests import test_story_video_mode_runtime as mode_fixtures
from tests.test_story_video_core import observation_fixture
from tests.test_story_video_reference_runtime import fixture as reference_fixture, Handle


PROFILE = 'story-video-flow-v2'
ENDPOINT_FIELDS = ('actual_visible', 'characters', 'props', 'scene', 'water', 'unknowns', 'continuity_risks')


class FlowOptimizationTests(unittest.TestCase):
    def reference_flow(self, *, optimized=True, audio=False, missing_endpoint=False,
                       source_warning=False, scene=True, resume=False):
        folder, task, plan = reference_fixture(scene=scene)
        if optimized:
            task['inputs'].update(runtime_optimization=PROFILE, audio_review_enabled=audio)
        if source_warning:
            plan['beats'][0]['dialogue'][0]['text'] = '我看见了。'
        stages, requests, generated, joined, replies = [], [], [], [], []

        def observation(index, *, endpoint=False):
            value = observation_fixture(index, segment=not endpoint)
            value['actual_visible'] = ('endpoint_only_' if endpoint else 'sequence_only_') + str(index)
            value['scene'] = '桥边终点' if endpoint else '途经庭院'
            for row in value['characters']:
                row['id'] = 'char_01'
            return value

        def request_json(request, directory, images=None, validator=None):
            system, payload, _ = request
            directory = Path(directory)
            stage = payload.get('stage')
            requests.append(dict(path=str(directory), payload=copy.deepcopy(payload), images=list(images or [])))
            if stage == 'references':
                value = dict(actual_visible='white_card_background', characters=[dict(
                    id='char_01', appearance='棕发，蓝色外衣', position='white_card_background',
                    pose='站姿', hands='空手', visibility='visible', certainty='observed')],
                    props=[], scene='new_fantasy_scene' if scene else 'white_card_background', water='unknown', actions=[], completed=[],
                    unknowns=[], continuity_risks=[], beat_status='unknown', keep_end_seconds=None,
                    keep_end_reason='素材识别')
            elif 'count' in payload and stage != 'review':
                value = copy.deepcopy(plan)
                for attempt in range(2):
                    call = directory / ('call_%d_fixture' % (attempt + 1))
                    trace = call / 'text_fixture'
                    reference._save(trace / 'request.json', dict(system=system, payload=payload))
                    reference._save(trace / 'response.json', dict(text=json.dumps(value, ensure_ascii=False),
                        meta=dict(finish_reason='stop')))
                    try:
                        validator(value, payload)
                        break
                    except ValueError as error:
                        reference._save(call / 'contract_error.json', dict(error=str(error)))
                        if attempt:
                            raise RuntimeError('本地模型格式修复仍未通过：' + str(error)) from error
                return value
            elif stage == 'review':
                value = dict(passed=True, issues=[], strengths=['完整故事'])
            elif 'dialogue_slots' in payload:
                value = dict(integrated_multimodal_description='[Shot 1] 真人写实摄影，中景跟随同行者观察桥边线索。\n\n[Shot 2] At 00:10.000, 近景呈现人物自然回应。',
                    overall_soundscape='水声和脚步声。', non_diegetic_music='N/A', brief='自然观察与回应。',
                    expected_end_state='同行者站在桥边。',
                    timeline=[dict(start=start, end=start + 5, action='观察线索', visible_result='人物回应')
                              for start in (0, 5, 10)],
                    dialogue=[dict(speaker=line['speaker'], text=line['text'], start=.5 + index * 3,
                        end=2 + index * 3, delivery='自然回应') for index, line in enumerate(payload['dialogue_slots'])],
                    continuity_notes=[])
            elif stage == 'storyboard_review':
                value = dict(passed=True, issues=[], event_contract=[])
            elif stage in ('segment', 'opening'):
                index = payload['beat']['index']
                value = observation(index, endpoint=stage == 'opening')
                if optimized and stage == 'segment' and not missing_endpoint:
                    endpoint = observation(index, endpoint=True)
                    value['endpoint_state'] = {key: endpoint[key] for key in ENDPOINT_FIELDS}
            else:
                raise AssertionError((stage, directory))
            before = copy.deepcopy(value)
            reference._save(directory / 'accepted.json', value)
            replies.append((directory, before))
            return value

        def generate(mode, prompt, **options):
            reference.h3_client._http('POST', '/prompt', {})
            generated.append(dict(mode=mode, prompt=prompt, options=copy.deepcopy(options)))
            raw = folder / ('native_%d.mp4' % len(generated))
            raw.write_bytes(prompt.encode('utf-8'))
            return dict(output_path=str(raw))

        def adopt(raw, directory, end_seconds):
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            clip, anchor = directory / 'clip.mp4', directory / 'anchor.png'
            clip.write_bytes(Path(raw).read_bytes())
            anchor.write_bytes(('exact_last_frame:' + str(directory)).encode())
            return dict(raw_path=str(raw), clip_path=str(clip), anchor_path=str(anchor), kept_seconds=end_seconds,
                frame_index=359, frame_time_seconds=359 / 24,
                clip_sha256=reference._sha(clip), anchor_sha256=reference._sha(anchor))

        def samples(clip, directory, seconds, **kwargs):
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            result = []
            for index in (0, 6, 90, 180, 270, 359):
                path = directory / ('sample_%06d.png' % index)
                path.write_bytes(('sample:' + str(index)).encode())
                result.append(dict(path=str(path), frame_index=index, time_seconds=index / 24))
            return result

        def join(rows, directory):
            joined.append(copy.deepcopy(rows))
            return dict(output_path=str(directory / 'film.mp4'), seconds=sum(row['kept_seconds'] for row in rows))

        patches = [patch.object(core.runtime, 'load', side_effect=lambda tid: task),
            patch.object(core.runtime, 'TaskHandle', side_effect=lambda tid: Handle(task, stages)),
            patch.object(reference.runtime_reset, 'bind_thread'), patch.object(reference.runtime_reset, 'assert_current'),
            patch.object(reference.gpu_manager, 'assert_local_run_allowed'), patch.object(reference.gpu_manager, 'acquire'),
            patch.object(reference.client, 'preserve_release'),
            patch.object(reference.client, '_native_profile', side_effect=lambda *args: nullcontext()),
            patch.object(core, 'request_json', side_effect=request_json),
            patch.object(reference.h3_client, '_http', return_value={}),
            patch.object(reference.h3_client, 'generate', side_effect=generate),
            patch.object(media, 'adopt', side_effect=adopt), patch.object(media, 'sample_frames', side_effect=samples),
            patch.object(media, 'join', side_effect=join), patch.object(core, '_url', side_effect=lambda path: '/files/' + Path(path).name)]
        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            capability = stack.enter_context(patch.object(asr, 'capability', return_value=dict(available=True)))
            review = stack.enter_context(patch.object(asr, 'review', return_value=dict(
                status='unavailable', issues=[dict(reason='合成ASR未知')], audio_listened=False)))
            stack.enter_context(patch.object(Path, 'unlink', side_effect=AssertionError('no permanent unlink')))
            stack.enter_context(patch('shutil.rmtree', side_effect=AssertionError('no permanent deletion')))
            output = reference.run_job(task['id'])
            manifest = reference._read(folder / 'manifest.json')
            self.assertEqual(len(generated), 2)
            self.assertEqual(len(joined), 1)
            before = copy.deepcopy(manifest['segments'])
            raw_replies = {str(directory): (directory / 'accepted.json').read_bytes()
                           for directory, _ in replies}
            for directory, original in replies:
                self.assertEqual(reference._read(directory / 'accepted.json'), original)
            if resume:
                counts = (len(generated), len(requests), capability.call_count, review.call_count)
                reference.run_job(task['id'])
                self.assertEqual(counts, (len(generated), len(requests), capability.call_count, review.call_count))
                self.assertEqual(reference._read(folder / 'manifest.json')['segments'], before)
                self.assertTrue(all((Path(path) / 'accepted.json').read_bytes() == data
                                    for path, data in raw_replies.items()))
            calls = dict(capability=capability.call_count, review=review.call_count)
        return folder, manifest, output, requests, generated, joined, calls

    def test_reference_one_observation_exact_tail_no_asr_and_source_warning_continues(self):
        folder, manifest, output, requests, generated, joined, calls = self.reference_flow(source_warning=True, resume=True)
        self.assertTrue(output['generation_completed'])
        self.assertEqual(len(generated), 2)
        # A completed rerun may reuse the join, but must never regenerate any clip.
        self.assertTrue(joined)
        self.assertEqual(calls, dict(capability=0, review=0))
        segment = [row for row in requests if row['payload'].get('stage') == 'segment']
        self.assertEqual(len(segment), 2)
        self.assertEqual([len(row['images']) for row in segment], [3, 3])
        self.assertFalse(any(row['payload'].get('stage') == 'opening' for row in requests))
        for index, row in enumerate(segment):
            self.assertEqual(row['images'][-1], manifest['segments'][index]['anchor_path'])
        self.assertEqual(generated[1]['options']['images'][-1], manifest['segments'][0]['anchor_path'])
        first_refs = reference._read(Path(manifest['segments'][0]['prompt_path']).with_name('inputs.json'))['references']
        second_refs = reference._read(Path(manifest['segments'][1]['prompt_path']).with_name('inputs.json'))['references']
        self.assertEqual([r['role'] for r in first_refs].count('scene_anchor'), 1)
        self.assertNotIn('scene_anchor', [r['role'] for r in second_refs])
        self.assertEqual(second_refs[-1]['sha256'], manifest['segments'][0]['anchor_sha256'])
        self.assertEqual(manifest['source_validation']['status'], 'warning')
        raw = reference._read(folder / 'reference_observation/result.json')
        self.assertEqual(raw['actual_visible'], 'white_card_background')
        self.assertEqual(raw['scene'], 'new_fantasy_scene')
        routed = [row['payload'] for row in requests
                  if 'count' in row['payload'] or 'dialogue_slots' in row['payload']]
        self.assertTrue(all('white_card_background' not in json.dumps(row, ensure_ascii=False) for row in routed))
        writer = [row['payload'] for row in requests if 'dialogue_slots' in row['payload']]
        self.assertEqual(writer[1]['actual_state']['actual_visible'], 'endpoint_only_1')
        self.assertTrue(all(row['asr']['status'] in ('disabled', 'skipped') for row in manifest['segments']))
        self.assertTrue(all('passed' not in row['asr'] for row in manifest['segments']))
        self.assertFalse(any(item['stage'].endswith('asr') for item in manifest.get('continuation_warnings', [])))

    def test_reference_missing_endpoint_is_unknown_not_sequence_fact(self):
        folder, manifest, _, requests, generated, joined, calls = self.reference_flow(missing_endpoint=True, scene=False)
        writer = [row['payload'] for row in requests if 'dialogue_slots' in row['payload']]
        self.assertEqual(writer[1]['actual_state']['beat_status'], 'unknown')
        self.assertEqual(writer[1]['actual_state']['characters'], [])
        self.assertEqual(writer[1]['actual_state']['completed'], [])
        self.assertEqual(writer[1]['actual_state']['scene'], 'unknown')
        self.assertNotEqual(writer[1]['actual_state']['actual_visible'], 'sequence_only_1')
        self.assertEqual(generated[1]['options']['images'][-1], manifest['segments'][0]['anchor_path'])
        self.assertEqual(len(joined), 1)
        self.assertEqual(calls['review'], 0)
        self.assertEqual(reference._read(folder / 'reference_observation/result.json')['scene'], 'white_card_background')
        routed = [row['payload'] for row in requests
                  if 'count' in row['payload'] or 'dialogue_slots' in row['payload']]
        self.assertTrue(all('white_card_background' not in json.dumps(row, ensure_ascii=False) for row in routed))

    def test_old_reference_without_marker_keeps_independent_endpoint_and_asr(self):
        _, manifest, _, requests, generated, joined, calls = self.reference_flow(optimized=False)
        self.assertEqual(calls, dict(capability=1, review=2))
        self.assertEqual(len([row for row in requests if row['payload'].get('stage') == 'segment']), 2)
        self.assertEqual(len([row for row in requests if row['payload'].get('stage') == 'opening']), 2)
        self.assertEqual(manifest['segments'][0]['observation']['actual_visible'], 'endpoint_only_1')
        self.assertEqual(len(generated), 2)
        self.assertEqual(len(joined), 1)

    def core_case(self, *, image=False, optimized=True, audio=False, missing_endpoint=False, guided=False):
        previous = getattr(self, '_core_support', None)
        if previous is not None:
            previous.doCleanups()
        support = mode_fixtures.ModeRuntimeTests(methodName='runTest')
        case, requests = support.fixture(image=image, automatic=not guided)
        self._core_support = support
        self.addCleanup(support.doCleanups)
        if optimized:
            case.task['inputs'].update(runtime_optimization=PROFILE, audio_review_enabled=audio)
        original_vision = case.vision.side_effect
        observations = []

        def vision(system, payload, images, directory, **options):
            value = json.loads(original_vision(system, payload, images, directory, **options))
            if payload.get('stage') == 'segment':
                observations.append(dict(payload=copy.deepcopy(payload), images=list(images)))
                value['actual_visible'] = 'sequence_only_' + str(payload['beat']['index'])
                if optimized and not missing_endpoint:
                    endpoint = observation_fixture(payload['beat']['index'])
                    endpoint['actual_visible'] = 'endpoint_only_' + str(payload['beat']['index'])
                    if image:
                        endpoint['scene_anchors'] = ['石台与溪流']
                        for row in endpoint['characters']:
                            row.update(face_view='three_quarter', face_clarity='clear')
                    endpoint_keys = ENDPOINT_FIELDS + (('scene_anchors',) if image else ())
                    value['endpoint_state'] = {key: endpoint[key] for key in endpoint_keys}
            return json.dumps(value, ensure_ascii=False)

        def samples(video, directory, **kwargs):
            directory.mkdir(parents=True, exist_ok=True)
            result = []
            for frame in (0, 6, 48, 96, 144, 191):
                path = directory / ('sample_%06d.png' % frame)
                path.write_bytes(('sample:' + str(frame)).encode())
                result.append(dict(path=str(path), time_seconds=frame / 24, frame_index=frame))
            return result

        original_adopt = media.adopt.side_effect

        def adopt(video, directory, **kwargs):
            value = original_adopt(video, directory, **kwargs)
            value.setdefault('kept_seconds', value.get('duration', 8))
            value.setdefault('frame_index', round(value['kept_seconds'] * 24) - 1)
            value.setdefault('frame_time_seconds', value['frame_index'] / 24)
            value.setdefault('clip_path', value.get('video_path'))
            return value

        case.vision.side_effect = vision
        case.mock(media, 'sample_frames', side_effect=samples)
        case.mock(media, 'adopt', side_effect=adopt)
        capability = case.mock(asr, 'capability', return_value=dict(available=True))
        if not hasattr(case, 'audio'):
            case.audio = case.mock(asr, 'review', return_value=dict(status='passed', issues=[]))
        return case, requests, observations, capability

    def test_core_text_and_image_one_observation_and_no_asr(self):
        for image in (False, True):
            with self.subTest(mode='image' if image else 'text'):
                case, requests, observations, capability = self.core_case(image=image)
                output = core.run_job(case.task['id'])
                manifest = case.manifest()
                self.assertTrue(output['generation_completed'])
                self.assertEqual(len(case.video_calls), 2)
                case.join.assert_called_once()
                self.assertEqual(len(observations), 2)
                self.assertEqual([len(row['images']) for row in observations], [3, 3])
                self.assertEqual([row['images'][-1] for row in observations],
                                 [row['anchor_path'] for row in manifest['segments']])
                self.assertEqual(case.writer_states[1]['actual_state']['actual_visible'], 'endpoint_only_1')
                self.assertEqual(case.video_calls[1]['first_frame'], manifest['segments'][0]['anchor_path'])
                capability.assert_not_called()
                case.audio.assert_not_called()
                self.assertTrue(all(row['quality']['audio']['status'] in ('disabled', 'skipped')
                                    and 'passed' not in row['quality']['audio'] for row in manifest['segments']))
                self.assertTrue(all(row['quality']['passed'] for row in manifest['segments']))
                post_opening = [p for p in requests if p.get('stage') == 'opening' and p.get('beat')]
                self.assertEqual(post_opening, [])

    def test_core_missing_endpoint_does_not_promote_sequence_to_current_state(self):
        case, requests, observations, _ = self.core_case(missing_endpoint=True)
        core.run_job(case.task['id'])
        state = case.writer_states[1]['actual_state']
        self.assertEqual(state['beat_status'], 'unknown')
        self.assertEqual(state['characters'], [])
        self.assertEqual(state['completed'], [])
        self.assertEqual(state['scene'], 'unknown')
        self.assertNotEqual(state['actual_visible'], 'sequence_only_1')
        self.assertGreaterEqual(len(observations), 2)
        self.assertFalse(any(p.get('stage') == 'opening' and p.get('beat') for p in requests))
        self.assertEqual(len(case.video_calls), 2)
        case.join.assert_called_once()

    def test_old_core_without_marker_keeps_endpoint_and_asr(self):
        case, requests, observations, _ = self.core_case(optimized=False)
        core.run_job(case.task['id'])
        self.assertEqual(len(observations), 2)
        self.assertEqual(len([p for p in requests if p.get('stage') == 'opening' and p.get('beat')]), 2)
        self.assertEqual(case.audio.call_count, 2)
        self.assertNotIn('runtime_optimization', case.manifest()['inputs'])

    def test_guided_text_preview_keeps_pause_with_single_observation_per_segment(self):
        case, requests, observations, capability = self.core_case(guided=True)
        with self.assertRaises(core.ControlStop):
            core.run_job(case.task['id'])
        first = copy.deepcopy(case.manifest()['segments'][0])
        self.assertEqual(len(case.video_calls), 1)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]['images'][-1], first['anchor_path'])
        core.action(case.task['id'], 'approve')
        case.task['status'] = 'running'
        output = core.run_job(case.task['id'])
        self.assertEqual(output['segments'], 2)
        self.assertEqual(len(case.video_calls), 2)
        self.assertEqual(case.manifest()['segments'][0], first)
        self.assertEqual(len(observations), 2)
        self.assertEqual([len(row['images']) for row in observations], [3, 3])
        self.assertEqual(case.writer_states[1]['actual_state']['actual_visible'], 'endpoint_only_1')
        self.assertFalse(any(p.get('stage') == 'opening' and p.get('beat') for p in requests))
        capability.assert_not_called()
        case.audio.assert_not_called()
        case.join.assert_called_once()

    def test_reference_opt_in_audio_stays_nonblocking(self):
        _, manifest, output, _, generated, joined, calls = self.reference_flow(audio=True)
        self.assertEqual(calls, dict(capability=1, review=2))
        self.assertTrue(output['generation_completed'])
        self.assertEqual([row['asr']['status'] for row in manifest['segments']], ['unavailable', 'unavailable'])
        self.assertEqual(len(generated), 2)
        self.assertEqual(len(joined), 1)

    def test_core_opt_in_audio_keeps_unknown_report_without_retakes(self):
        case, _, observations, _ = self.core_case(audio=True)
        case.audio_outcomes = ['unavailable', 'unavailable']
        output = core.run_job(case.task['id'])
        self.assertTrue(output['generation_completed'])
        self.assertEqual(case.audio.call_count, 2)
        self.assertEqual(len(case.video_calls), 2)
        self.assertEqual(len(observations), 2)
        self.assertEqual([row['quality']['audio']['status'] for row in case.manifest()['segments']],
                         ['unavailable', 'unavailable'])
        case.join.assert_called_once()


if __name__ == '__main__':
    unittest.main()
