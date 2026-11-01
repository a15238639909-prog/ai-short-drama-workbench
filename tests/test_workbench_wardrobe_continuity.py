"""Main-workbench wardrobe evidence and legacy isolation without model calls."""
import copy
import hashlib
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from cores import asset_core, saga_core, store, story_core
from cores import story_video_core as video_core, story_video_media as media
from cores import video_continuity as continuity, wardrobe_continuity as wardrobe
from cores import workbench_director_runtime as runtime, workbench_story_director as writer
from tests import test_workbench_story_director_release as director_cases
from tests import test_workbench_scene_entry_render as scene_cases


def removal(identity='a', before_frame=0, after_frame=2):
    return dict(character_id=identity, action='remove', slot='outerwear', item='蓝色外套',
        before='阿岚穿着蓝色外套。', after='阿岚已脱下蓝色外套。',
        evidence='实际开始帧和结束帧可见同一人物脱下外套前后的变化。',
        before_frame=before_frame, after_frame=after_frame, holder='unknown', location='unknown', certainty='observed')


def observation(events=(), *, visible=True):
    return dict(actual_visible='阿岚站在竹庭。' if visible else '当前人物被门框遮挡。',
        endpoint_frame_index=2, endpoint_conflicts=[],
        characters=[dict(id='a', frame_index=2, appearance='短发成年青年', position='竹庭', pose='站立', hands='unknown',
            visibility='visible', certainty='observed')] if visible else [],
        props=[], scene='竹庭', water='unknown', unknowns=[] if visible else ['当前穿戴被遮挡。'],
        actions=[], completed=[], missing=[], continuity_risks=[], beat_status='unknown',
        keep_end_seconds=None, keep_end_reason='采用真实末帧。',
        wardrobe_evidence_scope='transition', wardrobe_events=copy.deepcopy(list(events)))


class WorkbenchWardrobeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(__file__).resolve().parent / '.retained' / ('wardrobe_runtime_%d' % time.time_ns())
        self.folder.mkdir(parents=True)
        for name in ('stories', 'assets', 'tasks', 'narrative', 'states', 'profiles', 'manifests'):
            (self.folder / name).mkdir()
        for active in (patch.object(store, 'DATA', self.folder),
                       patch.object(Path, 'unlink', side_effect=AssertionError('no permanent deletion')),
                       patch('shutil.rmtree', side_effect=AssertionError('no permanent deletion'))):
            active.start()
            self.addCleanup(active.stop)
        self.sid = 'WARDROBE_RUNTIME_FIXTURE'
        self.cards = director_cases.fixture()[1]
        self.cards[0]['role'] = '主角'
        self.cards[1]['role'] = '同行者'
        self.project(new=True)
        for card in self.cards:
            asset_core.save_asset(self.sid, 'characters', dict(card, story_id=self.sid))

    def project(self, *, new):
        settings = dict(story_mode='single')
        story = dict(story_id=self.sid, settings=settings,
            saga=dict(episodes=[dict(no=1), dict(no=2)]))
        if new:
            story['wardrobe_policy'] = settings['wardrobe_policy'] = wardrobe.POLICY
        story_core.save_story(story)

    def segment(self, ep, number):
        video = self.folder / ('episode_%d_segment_%d.mp4' % (ep, number))
        video.write_bytes(('mock video %d/%d' % (ep, number)).encode())
        timeline = saga_core.ep_timeline(self.sid, ep)
        if not timeline.get('scenes'):
            timeline['scenes'] = [dict(no=1, segments=[])]
        timeline['scenes'][0]['segments'].append(dict(no=number, video=str(video),
            prompt='普通人物动作。', source_text='阿岚脱下蓝色外套。', chars=['阿岚', '顾亭'], plan=dict(flow='source-v1')))
        saga_core.save_ep_timeline(self.sid, ep, timeline)
        return video

    def observe(self, ep, number, reply, *, times=(0, .5, 1)):
        extracted = []

        def extract(video, index, target):
            extracted.append(index)
            Path(target).write_bytes(('mock actual frame %d' % index).encode())

        with patch.object(media, '_timeline', return_value=({}, {}, list(times))), \
                patch.object(media, '_extract', side_effect=extract), \
                patch.object(video_core, 'request_json', return_value=copy.deepcopy(reply)) as request:
            actual = runtime.after_render(self.sid, ep, number)
        return actual, request, extracted

    def test_new_project_observes_actual_first_and_last_once_and_reuses_cache(self):
        self.segment(1, 1)
        actual, request, extracted = self.observe(1, 1, observation([removal()]))
        self.assertEqual(extracted, [0, 2])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(len(request.call_args.kwargs['images']), 2)
        payload = request.call_args.args[0][1]
        self.assertEqual([row['frame_index'] for row in payload['image_role_bindings']], [0, 2])
        schema = request.call_args.args[0][2]
        self.assertEqual(schema['properties']['wardrobe_evidence_scope']['enum'], ['transition'])
        self.assertEqual(actual['wardrobe_state']['characters']['a']['slots']['outerwear']['status'], 'removed')
        row = runtime._segment(self.sid, 1, 1)[2]
        self.assertEqual(row['director_actual_state']['evidence_frames'], [0, 2])
        self.assertNotIn('wardrobe_state', row['director_actual_state']['observation'])
        self.assertEqual(row['wardrobe_video_stamp'], continuity.media_stamp(row['video']))
        with patch.object(video_core, 'request_json', side_effect=AssertionError('cached observation must not call a model')), \
                patch.object(media, '_extract', side_effect=AssertionError('cached frame must not be extracted')):
            self.assertEqual(runtime.actual_state_for(self.sid, 1, 1), actual)

    def test_confirmed_wardrobe_crosses_segments_episodes_and_occluded_frames(self):
        self.segment(1, 1)
        first, _, _ = self.observe(1, 1, observation([removal()]))
        self.segment(1, 2)
        second, _, _ = self.observe(1, 2, observation(visible=False))
        self.assertEqual(second['wardrobe_state'], first['wardrobe_state'])
        before = copy.deepcopy(story_core.get_story(self.sid))
        with patch.object(video_core, 'request_json', side_effect=AssertionError('state replay is model-free')):
            state = runtime.wardrobe_state_for(self.sid, 2, 0)
            opening = runtime.actual_state_for(self.sid, 2, 0)
        self.assertEqual(state, first['wardrobe_state'])
        self.assertEqual(opening['wardrobe_state'], state)
        self.assertEqual(opening['actual_visible'], 'unknown')
        self.assertEqual(story_core.get_story(self.sid), before)

    def test_replay_uses_current_video_stamp_not_tampered_checkpoint(self):
        self.segment(1, 1)
        actual, _, _ = self.observe(1, 1, observation([removal()]))
        timeline, _, row = runtime._segment(self.sid, 1, 1)
        row['wardrobe_state'] = dict(policy=wardrobe.POLICY, characters={'intruder': {'slots': {}}})
        row['wardrobe_video_stamp'] = {'path': 'wrong'}
        saga_core.save_ep_timeline(self.sid, 1, timeline)
        self.assertEqual(runtime.wardrobe_state_for(self.sid, 1, 1), actual['wardrobe_state'])
        timeline, _, row = runtime._segment(self.sid, 1, 1)
        row['director_actual_state']['video_stamp']['bytes'] += 1
        saga_core.save_ep_timeline(self.sid, 1, timeline)
        state = runtime.wardrobe_state_for(self.sid, 1, 1)
        self.assertEqual(state['characters']['a']['slots'], {})
        timeline, _, row = runtime._segment(self.sid, 1, 1)
        row['director_actual_state']['video_stamp'] = continuity.media_stamp(row['video'])
        row['stale_video'] = True
        saga_core.save_ep_timeline(self.sid, 1, timeline)
        self.assertEqual(runtime.wardrobe_state_for(self.sid, 1, 1)['characters']['a']['slots'], {})

    def test_new_replay_does_not_accept_old_single_frame_observer(self):
        self.segment(1, 1)
        timeline, _, row = runtime._segment(self.sid, 1, 1)
        row['director_actual_state'] = dict(policy='single-true-final-frame-v1',
            video_stamp=continuity.media_stamp(row['video']), observation=observation([removal()]), evidence_frames=[0, 2])
        saga_core.save_ep_timeline(self.sid, 1, timeline)
        self.assertEqual(runtime.wardrobe_state_for(self.sid, 1, 1)['characters']['a']['slots'], {})

    def test_old_project_stays_single_frame_and_gets_no_wardrobe_state(self):
        self.project(new=False)
        self.segment(1, 1)
        reply = observation()
        reply.pop('wardrobe_events')
        reply.pop('wardrobe_evidence_scope')
        actual, request, extracted = self.observe(1, 1, reply)
        self.assertEqual(extracted, [2])
        self.assertEqual(len(request.call_args.kwargs['images']), 1)
        self.assertNotIn('wardrobe_state', actual)
        self.assertNotIn('wardrobe_policy', request.call_args.args[0][1])
        self.assertIsNone(runtime.wardrobe_state_for(self.sid, 2, 0))
        self.assertNotIn('wardrobe_state', runtime.actual_state_for(self.sid, 2, 0))
        self.assertEqual(runtime._segment(self.sid, 1, 1)[2]['director_actual_state']['policy'], 'single-true-final-frame-v1')

    def test_same_or_nonexistent_sample_frame_events_cannot_commit_wardrobe(self):
        for number, event in enumerate((removal(before_frame=2, after_frame=2), removal(after_frame=999)), 1):
            self.segment(1, number)
            actual, _, _ = self.observe(1, number, observation([event]))
            self.assertEqual(actual['wardrobe_state']['characters']['a']['slots'], {})

    def test_stale_clip_cannot_reset_confirmed_wardrobe_or_trigger_new_observation(self):
        self.segment(1, 1)
        actual, _, _ = self.observe(1, 1, observation([removal()]))
        self.segment(1, 2)
        timeline, _, row = runtime._segment(self.sid, 1, 2)
        row['stale_video'] = True
        row['director_actual_state'] = dict(policy=wardrobe.POLICY, video_stamp=continuity.media_stamp(row['video']),
            observation=observation([dict(removal(), action='put_on', after='阿岚又穿上蓝色外套。')]),
            evidence_frames=[0, 2])
        saga_core.save_ep_timeline(self.sid, 1, timeline)
        before = copy.deepcopy(row)
        with patch.object(video_core, 'request_json', side_effect=AssertionError('stale video must not be observed')), \
                patch.object(media, '_timeline', side_effect=AssertionError('stale video must not be sampled')):
            result = runtime.after_render(self.sid, 1, 2)
        self.assertEqual(result['wardrobe_state'], actual['wardrobe_state'])
        self.assertEqual(runtime.wardrobe_state_for(self.sid, 1, 2), actual['wardrobe_state'])
        self.assertEqual(runtime._segment(self.sid, 1, 2)[2], before)

    def test_frozen_pending_request_is_not_rewritten_for_new_policy(self):
        from api import segment_api
        from cores import authoring
        frozen = dict(key='existing-frozen-request', status='pending', final_prompt='旧冻结提示词',
            final_refs=['original-character.png'], seed=41)
        saga_core.save_ep_timeline(self.sid, 1, dict(scenes=[dict(no=1, segments=[
            dict(no=1, prompt='', render_request=copy.deepcopy(frozen))])]))
        rendered = []

        def render(scene, number):
            row = runtime._segment(self.sid, 1, number)[2]
            self.assertEqual(row['prompt'], frozen['final_prompt'])
            self.assertEqual(row['render_request'], frozen)
            rendered.append(number)
            return dict(video='mock-recovered-existing-request.mp4')

        with patch.object(segment_api, 'is_segment_current', return_value=False), \
                patch.object(authoring, 'make_h3_prompts', side_effect=AssertionError('pending snapshot must not be rewritten')), \
                patch.object(runtime, 'after_render', return_value=runtime.unknown()), \
                patch.object(runtime, 'explicit_opening'):
            runtime.run_sequence(self.sid, 1, [1], render=render)
        self.assertEqual(rendered, [1])
        self.assertEqual(runtime._segment(self.sid, 1, 1)[2]['render_request'], frozen)


class WorkbenchWardrobeWriterTests(unittest.TestCase):
    def run_writer(self, *, new=True, fallback=False):
        slices, cards, settings = director_cases.fixture()
        if new:
            cards[0]['role'] = '主角'
        state = wardrobe.advance(wardrobe.initial_state(cards), characters=cards,
            sequence=observation([removal()]), segment=1, evidence_frames=(0, 2))
        if new:
            settings['wardrobe_policy'] = wardrobe.POLICY
        requests = []

        def query(system, user, **options):
            payload = json.loads(user)
            requests.append(dict(system=system, payload=payload))
            return '模型答复格式错误' if fallback else json.dumps(director_cases.response(payload), ensure_ascii=False)

        actual = dict(writer._unknown(), wardrobe_state=state) if new else writer._unknown()
        rows = writer.make_prompts(slices, cards, ['竹庭'], query, settings=settings, count=1,
            actual_state_for=lambda index: copy.deepcopy(actual))
        return requests, rows

    def test_new_writer_and_source_fallback_keep_only_controlled_state_then_scope(self):
        for fallback in (False, True):
            with self.subTest(fallback=fallback):
                requests, rows = self.run_writer(fallback=fallback)
                self.assertEqual(rows[0]['source_fallback'], fallback)
                self.assertEqual(rows[0]['plan']['wardrobe_policy'], wardrobe.POLICY)
                for request in requests:
                    payload = request['payload']
                    self.assertNotIn('看不清或本地观察未知时直接遵循原图，不补写外貌。', request['system'])
                    self.assertEqual(payload['wardrobe_policy'], wardrobe.POLICY)
                    self.assertNotIn('_observation_raw', payload)
                    self.assertNotIn('wardrobe_state', payload['actual_state'])
                    slot = payload['wardrobe_state']['characters']['a']['slots']['outerwear']
                    self.assertEqual((slot['status'], slot['item']), ('removed', '蓝色外套'))
                    self.assertFalse({'evidence', 'before', 'after'} & set(slot))
                prompt = rows[0]['prompt']
                self.assertNotIn(wardrobe.MARKER, prompt)
                self.assertNotIn('Current wardrobe contract:', prompt)
                self.assertNotIn('蓝色外套', prompt)
                self.assertIn('<Subject 1>：面容、发型与体型沿用人物参考。', prompt)
                self.assertNotIn('Character references define stable face, hair and wardrobe identity.', prompt)
                scoped = continuity.scope_prompt(prompt, rows[0], rows[0]['director_contract'])
                self.assertNotIn(wardrobe.MARKER, scoped)
                self.assertNotIn('Character references define stable face, hair and wardrobe identity.', scoped)
                self.assertEqual(scoped.count('慢一点。'), 1)
                self.assertEqual(continuity.scope_prompt(scoped, rows[0], rows[0]['director_contract']), scoped)

    def test_old_writer_fallback_and_final_scope_match_prechange_baselines(self):
        # Fresh compiler presentation changed to concise positive reference roles.
        # This fixture does not load or rewrite a previously saved prompt/video.
        expected = {False: 'f435571430eaed9144f001c6e3141ce07233e74fba31fb61178755037845623b',
                    True: '913c3e3bc33606f17bbf5cbcdb9e2bb6d9ec163bb1c85e9578a79624f8d8940d'}

        def clean(value):
            if isinstance(value, dict):
                return {key: clean(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [clean(item) for item in value]
            return value.replace(director_cases.__file__, '<FIXTURE_REFERENCE>') if isinstance(value, str) else value

        for fallback, digest in expected.items():
            with self.subTest(fallback=fallback):
                requests, rows = self.run_writer(new=False, fallback=fallback)
                scoped = continuity.scope_prompt(rows[0]['prompt'], rows[0], rows[0]['director_contract'])
                value = clean(dict(requests=requests, rows=rows, scoped=scoped))
                actual = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                self.assertEqual(actual, digest)
                self.assertTrue(all('wardrobe_policy' not in row['payload'] for row in requests))
                self.assertNotIn(wardrobe.MARKER, scoped)


class WorkbenchWardrobeRenderTests(unittest.TestCase):
    def setUp(self):
        self.scene = scene_cases.WorkbenchSceneEntryRenderTests(methodName='runTest')
        self.scene.setUp()
        self.addCleanup(self.scene.doCleanups)
        self.case = self.scene.case
        self.case.story['wardrobe_policy'] = wardrobe.POLICY
        self.case.story['settings']['wardrobe_policy'] = wardrobe.POLICY
        event = dict(removal('adult_alice'), before='Alice穿着蓝色外套。', after='Alice已脱下蓝色外套。')
        self.state = wardrobe.advance(wardrobe.initial_state(self.scene.characters),
            characters=self.scene.characters, sequence=observation([event]), segment=2, evidence_frames=(0, 2))
        self.evidence = self.case.directory / 'wardrobe_evidence'
        self.evidence.mkdir()
        self.anchor = self.evidence / 'actual_frame_000002.png'
        self.anchor.write_bytes(b'mock true preceding final frame for wardrobe only')
        previous = self.scene.row(2)
        previous['director_actual_state'] = dict(policy=wardrobe.POLICY,
            video_stamp=continuity.media_stamp(previous['video']), evidence_frames=[0, 2],
            evidence_directory=str(self.evidence), observation=observation([event]))
        self.previous = copy.deepcopy(previous)
        stack = self.case.stack
        self.actual = stack.enter_context(patch.object(runtime, 'actual_state_for', return_value=dict(
            runtime.unknown(), wardrobe_state=copy.deepcopy(self.state))))
        self.segment_lookup = stack.enter_context(patch.object(runtime, '_segment', side_effect=lambda sid, ep, number:
            (copy.deepcopy(self.case.timeline), 1, copy.deepcopy(self.scene.row(number)))))

    def test_fresh_request_keeps_scene_reference_and_adds_one_wardrobe_only_tail(self):
        self.scene.render(3)
        submitted = self.case.generate.call_args
        expected = [self.scene.paths['Alice'], self.scene.paths['Courtyard'], str(self.anchor)]
        self.assertEqual(submitted.kwargs['images'], expected)
        self.assertIn('图中可见衣物提供当前穿戴细节', submitted.args[1])
        self.assertNotIn('Do not copy its framing, pose, people count', submitted.args[1])
        self.assertIn('weak_reference - 当前可见衣物细节', submitted.args[1])
        self.assertIn('the environment from <Picture 2>', submitted.args[1])
        self.scene.assert_bound(submitted.args[1], expected)
        self.assertEqual(self.scene.row(3)['render_request']['wardrobe_reference'], str(self.anchor))
        self.assertEqual(self.scene.row(2), self.previous)
        self.actual.assert_called_once_with('TEST', 2, 2)
        self.scene.prepare.assert_not_called()
        self.scene.handoff.assert_not_called()

    def test_pending_snapshot_reuses_exact_tail_without_observing_or_appending_again(self):
        self.case.generate.side_effect = RuntimeError('fixture disconnect after frozen submission')
        with self.assertRaisesRegex(RuntimeError, 'fixture disconnect'):
            self.scene.render(3)
        frozen = copy.deepcopy(self.scene.row(3)['render_request'])
        self.assertEqual(frozen['final_refs'].count(str(self.anchor)), 1)
        self.case.receipts[frozen['key']] = dict(status='submitted', prompt_id='fixture-original-task')
        self.case.generate.side_effect = None
        with patch.object(runtime, 'actual_state_for', side_effect=AssertionError('frozen snapshot must not re-observe')), \
                patch.object(runtime, '_segment', side_effect=AssertionError('frozen snapshot must not append evidence')):
            self.scene.render(3)
        submitted = self.case.generate.call_args
        self.assertEqual(submitted.args, ('ref2va', frozen['final_prompt']))
        self.assertEqual(submitted.kwargs['images'], frozen['final_refs'])
        self.assertEqual(submitted.kwargs['recovery_key'], frozen['key'])
        self.assertEqual(submitted.kwargs['images'].count(str(self.anchor)), 1)

    def test_old_project_scene_entry_keeps_original_references_without_tail(self):
        self.case.story.pop('wardrobe_policy')
        self.case.story['settings'].pop('wardrobe_policy')
        self.actual.side_effect = AssertionError('old project must not enable wardrobe observation')
        self.scene.render(3)
        self.assertEqual(self.case.generate.call_args.kwargs['images'],
            [self.scene.paths['Alice'], self.scene.paths['Courtyard']])
        self.assertNotIn('wardrobe_reference', self.scene.row(3)['render_request'])

    def test_no_confirmed_change_does_not_add_a_tail_image(self):
        self.actual.return_value = dict(runtime.unknown(), wardrobe_state=wardrobe.initial_state(self.scene.characters))
        self.segment_lookup.side_effect = AssertionError('no wardrobe change requires no tail lookup')
        self.scene.render(3)
        self.assertEqual(self.case.generate.call_args.kwargs['images'],
            [self.scene.paths['Alice'], self.scene.paths['Courtyard']])
        self.assertNotIn('wardrobe_reference', self.scene.row(3)['render_request'])

    def test_full_reference_slots_keep_all_images_and_record_text_only_continuation(self):
        from api import segment_api
        images = [self.scene.paths['Alice'], self.scene.paths['Courtyard']]
        for index in range(7):
            path = self.case.directory / ('retained_reference_%d.png' % index)
            path.write_bytes(('mock required reference %d' % index).encode())
            images.append(str(path))
        with patch.object(segment_api, 'new_pack', return_value=images):
            self.scene.render(3)
        self.assertEqual(self.case.generate.call_args.kwargs['images'], images)
        request = self.scene.row(3)['render_request']
        self.assertNotIn('wardrobe_reference', request)
        self.assertIn('图片槽位已满', request['wardrobe_reference_note'])
        self.assertEqual(self.state['characters']['adult_alice']['slots']['outerwear']['status'], 'removed')
        self.assertNotIn('蓝色外套', request['final_prompt'])
        self.assertNotIn('Current wardrobe contract:', request['final_prompt'])


if __name__ == '__main__':
    unittest.main()
