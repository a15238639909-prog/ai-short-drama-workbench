"""Opt-in wardrobe continuation across reference jobs; all model/media work is mocked."""
from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from cores import story_video_characters as characters
from cores import story_video_flow as flow
from cores import story_video_reference as reference
from cores import story_video_reference_prompts as prompts
from cores import wardrobe_continuity as wardrobe
from tests import test_story_video_reference_runtime as runtime_cases
from tests.test_story_video_reference_prompts import cards, plan_and_raw


def removal(identity='char_01', before_frame=0, after_frame=5):
    return dict(character_id=identity, action='remove', slot='outerwear', item='蓝色外套',
        evidence='本段开始与结束图片之间可见该人物完成脱下外套的动作。',
        before='开始图片中该人物穿着蓝色外套。', after='结束图片中该人物已脱下蓝色外套。',
        holder='unknown', location='unknown', certainty='observed',
        before_frame=before_frame, after_frame=after_frame)


class StoryVideoWardrobeTests(unittest.TestCase):
    def test_old_requests_and_compiled_prompts_are_byte_compatible(self):
        expected = {
            None: '9e0680462380c3a987fef877ef4327a48d54795cbdd9fa863a00449c5be7d4a9',
            prompts.REFERENCE_VISUAL_POLICY: '5a619e758b6b542f65c33c64d67ea9990390d8ee840fbd8d8c26826f1ea14c0a'}
        plan, beat, raw = plan_and_raw()
        refs = characters.references_for_segment(cards(), previous_frame='actual_tail.png')
        for policy, digest in expected.items():
            with self.subTest(policy=policy):
                request = prompts.write_request(plan, beat, {'actual_visible': '普通末帧'}, 15,
                    mode='ref2va', references=refs, flow_optimization=True, reference_visual_policy=policy)
                with patch.object(wardrobe, 'apply_prompt', side_effect=AssertionError('legacy request changed')):
                    final = characters.bind_delivery(raw, request[1], cards(), refs)
                value = dict(request=request, final=final)
                actual = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
                self.assertEqual(actual, digest)

    def test_new_planning_writing_and_revision_do_not_freeze_reference_clothes(self):
        plan, beat, raw = plan_and_raw()
        refs = characters.references_for_segment(cards(), previous_frame='actual_tail.png')
        options = dict(reference_visual_policy=prompts.REFERENCE_VISUAL_POLICY, wardrobe_policy=wardrobe.POLICY)
        writing = prompts.write_request(plan, beat, {}, 15, mode='ref2va', references=refs,
            wardrobe_state=wardrobe.initial_state(cards()), **options)
        requests = [prompts.plan_request('普通交流故事', '', 2, 15, **options),
            prompts.review_request(plan, '普通交流故事', '', 2, 15, **options), writing,
            prompts.revision_request(raw, writing[1], dict(issues=[]))]
        for system, payload, _ in requests:
            self.assertEqual(payload['wardrobe_policy'], wardrobe.POLICY)
            self.assertIn(wardrobe.authority_rules(), system)
            for fixed in ('服装和饰品全部按各自人设图', '只固定身份、脸、发型、体态和服装',
                          '人物身份、脸、发型和服装以人物设定为准'):
                self.assertNotIn(fixed, system)
        self.assertEqual(requests[-1][1]['wardrobe_state'], writing[1]['wardrobe_state'])

    def test_new_compile_keeps_six_fields_original_dialogue_and_creative_body(self):
        plan, beat, raw = plan_and_raw()
        refs = characters.references_for_segment(cards(), previous_frame='actual_tail.png')
        initial = wardrobe.initial_state(cards())
        state = wardrobe.advance(initial, characters=cards(), sequence=dict(
            wardrobe_evidence_scope='transition', wardrobe_events=[removal()]), segment=1, evidence_frames=(0, 5))
        request = prompts.write_request(plan, beat, {}, 15, mode='ref2va', references=refs,
            reference_visual_policy=prompts.REFERENCE_VISUAL_POLICY, wardrobe_policy=wardrobe.POLICY,
            wardrobe_state=state)
        original = copy.deepcopy(raw)
        final = characters.bind_delivery(raw, request[1], cards(), refs)
        self.assertEqual(raw, original)
        self.assertEqual(list(final['fields']), list(prompts.FIELDS))
        self.assertEqual(final['prompt'], '\n\n'.join(field + ':\n' + final['fields'][field] for field in prompts.FIELDS))
        self.assertIn('Wardrobe continuity (' + wardrobe.POLICY + '):', final['prompt'])
        self.assertIn('蓝色外套', final['prompt'])
        self.assertNotIn('服装和饰品全部按各自人设图', final['prompt'])
        self.assertIn('身份、面部、发型', final['fields']['subject_definitions'])
        self.assertEqual([line['text'] for line in final['dialogue']], [line['text'] for line in raw['dialogue']])

    def test_compact_observation_retains_transition_events_only_at_sequence_level(self):
        request = wardrobe.observation_request(prompts.observe_request('segment'), cards(), transition_evidence=True,
            frame_bindings=[dict(frame_index=0), dict(frame_index=359)])
        original = copy.deepcopy(request)
        samples = [dict(path='frame_%d.png' % index, frame_index=index, time_seconds=index / 24,
                        role=role) for index, role in ((0, 'opening'), (180, 'middle'), (359, 'endpoint'))]
        compact = flow.compact_request(request, samples)
        properties = compact[2]['properties']
        self.assertIn('wardrobe_events', properties)
        self.assertEqual(properties['wardrobe_evidence_scope']['enum'], ['transition'])
        self.assertNotIn('wardrobe_events', properties['endpoint_state']['properties'])
        self.assertIn('wardrobe_events只在顶层', compact[0])
        self.assertEqual(request, original)
        single = wardrobe.observation_request(prompts.observe_request('opening'), cards(), transition_evidence=False)
        self.assertEqual(single[2]['properties']['wardrobe_evidence_scope']['enum'], ['single_frame'])
        repeated = wardrobe.observation_request(prompts.observe_request('segment'), cards(), transition_evidence=True,
            frame_bindings=[dict(frame_index=5), dict(frame_index=5)])
        self.assertEqual(repeated[2]['properties']['wardrobe_evidence_scope']['enum'], ['single_frame'])
        self.assertEqual(repeated[2]['properties']['wardrobe_events']['maxItems'], 0)

    def test_confirmed_outfit_replacement_does_not_reapply_reference_clothes(self):
        plan, beat, raw = plan_and_raw()
        refs = characters.references_for_segment(cards(), previous_frame='actual_tail.png')
        event = dict(removal(), action='replace', slot='outfit', item='绿色工作服',
            after='结束图片中该人物已经换好绿色工作服。')
        state = wardrobe.advance(wardrobe.initial_state(cards()), characters=cards(), sequence=dict(
            wardrobe_evidence_scope='transition', wardrobe_events=[event]), segment=1, evidence_frames=(0, 5))
        request = prompts.write_request(plan, beat, {}, 15, mode='ref2va', references=refs,
            reference_visual_policy=prompts.REFERENCE_VISUAL_POLICY, wardrobe_policy=wardrobe.POLICY,
            wardrobe_state=state)
        final = characters.bind_delivery(raw, request[1], cards(), refs)
        contract = wardrobe.prompt_contract(state, refs)
        self.assertEqual(len(contract['characters']), 1)
        self.assertIs(contract['characters'][0]['reference_clothing_baseline'], False)
        self.assertEqual(contract['characters'][0]['slots']['outfit']['item'], '绿色工作服')
        self.assertIn('绿色工作服', final['fields']['retention_analysis'])
        self.assertNotIn('外观完整沿用', final['fields']['subject_definitions'])

    def _flow(self, *, new=True, optimized=False, resume=False, event=True, identity='char_01', event_frames=None):
        fixture = runtime_cases.fixture
        review = reference._Session.review
        replies = []

        def marked_fixture(*arguments, **options):
            folder, task, plan = fixture(*arguments, **options)
            if new:
                task['inputs'].update(wardrobe_policy=wardrobe.POLICY,
                    reference_visual_policy=prompts.REFERENCE_VISUAL_POLICY)
            if optimized:
                task['inputs'].update(runtime_optimization=flow.PROFILE, audio_review_enabled=False)
            return folder, task, plan

        def observed(session, request, directory, *arguments, **options):
            result = review(session, request, directory, *arguments, **options)
            if not wardrobe.enabled(request[1]) or request[1].get('stage') not in ('references', 'segment', 'opening'):
                return result
            result = copy.deepcopy(result)
            scope = request[2]['properties']['wardrobe_evidence_scope']['enum'][0]
            index = (request[1].get('beat') or {}).get('index', 0)
            result.update(wardrobe_evidence_scope=scope, wardrobe_events=[], props=[], unknowns=[], water='unknown')
            result['characters'] = [dict(id='char_01', appearance='人设相貌，红色外套' if not event else '人设相貌',
                position='桥边' if index <= 1 else 'unknown', pose='站立' if index <= 1 else 'unknown',
                hands='unknown', visibility='visible' if index <= 1 else 'offscreen',
                certainty='observed' if index <= 1 else 'last_known')]
            if index == 1 and scope == 'transition' and event:
                before_frame, after_frame = event_frames or (0, 359 if optimized else 5)
                result['wardrobe_events'] = [removal(identity, before_frame, after_frame)]
            if 'endpoint_state' in request[2]['properties']:
                result['endpoint_state'] = {key: copy.deepcopy(result[key]) for key in
                    request[2]['properties']['endpoint_state']['properties']}
            reference._save(Path(directory) / 'wardrobe_fixture_response.json', result)
            replies.append((result, copy.deepcopy(result['wardrobe_events'])))
            return result

        helper = runtime_cases.ReferenceRuntimeTests(methodName='runTest')
        with patch.object(runtime_cases, 'fixture', side_effect=marked_fixture), \
                patch.object(reference._Session, 'review', new=observed), \
                patch.object(Path, 'unlink', side_effect=AssertionError('no permanent deletion')), \
                patch('shutil.rmtree', side_effect=AssertionError('no permanent deletion')), redirect_stdout(io.StringIO()):
            result = helper._flow(pause_second=resume)
        for response, events in replies:
            self.assertEqual(response['wardrobe_events'], events)
            self.assertNotIn('wardrobe_state', response)
        return result

    def test_observed_removal_survives_pause_resume_and_next_segment_occlusion(self):
        folder, task, manifest, _, _, requests, generated = self._flow(resume=True)
        first = manifest['segments'][0]['wardrobe_state']
        self.assertIn('蓝色外套', json.dumps(first, ensure_ascii=False))
        writing = [row['payload'] for row in requests if 'dialogue_slots' in row['payload']]
        second = [payload for payload in writing if payload['beat']['index'] == 2]
        self.assertTrue(second)
        for payload in second:
            self.assertEqual(payload['wardrobe_state'], wardrobe.model_state(first, payload['canonical_references']))
            self.assertNotIn('wardrobe_continuity', payload)
            self.assertNotIn('evidence', json.dumps(payload['wardrobe_state']))
        self.assertEqual(len(generated), 2)
        self.assertIn('蓝色外套', generated[1]['prompt'])
        self.assertEqual(manifest['segments'][1]['wardrobe_state'], first)
        self.assertEqual(len(task['inputs']['character_cards']), 4)
        self.assertNotIn('白色衬衫', generated[1]['prompt'])
        self.assertTrue((folder / 'segments/01_take_1/observation/wardrobe_fixture_response.json').exists())

    def test_optimized_sequence_updates_state_without_adding_endpoint_events(self):
        _, _, manifest, _, _, requests, generated = self._flow(optimized=True)
        self.assertIn('蓝色外套', generated[1]['prompt'])
        self.assertIn('蓝色外套', json.dumps(manifest['segments'][0]['wardrobe_state'], ensure_ascii=False))
        sequences = [row['payload'] for row in requests if row['payload'].get('stage') == 'segment']
        self.assertTrue(all(payload['endpoint_image_index'] == 3 for payload in sequences))

    def test_unknown_identity_or_single_appearance_drift_does_not_update_wardrobe(self):
        for options in (dict(identity='unknown_character'), dict(event=False)):
            with self.subTest(options=options):
                _, _, manifest, _, _, _, generated = self._flow(**options)
                self.assertEqual(manifest['segments'][0]['wardrobe_state'], manifest['initial_wardrobe_state'])
                self.assertNotIn('蓝色外套', generated[1]['prompt'])
                self.assertNotIn('红色外套', generated[1]['prompt'])

    def test_same_or_nonexistent_evidence_frames_do_not_commit_removal(self):
        for frames in ((5, 5), (0, 999999)):
            with self.subTest(frames=frames):
                _, _, manifest, _, _, _, generated = self._flow(event_frames=frames)
                self.assertEqual(manifest['segments'][0]['wardrobe_state'], manifest['initial_wardrobe_state'])
                self.assertNotIn('蓝色外套', generated[1]['prompt'])

    def test_existing_reference_task_does_not_gain_policy_or_state_on_resume(self):
        _, task, manifest, _, _, requests, generated = self._flow(new=False, resume=True)
        self.assertNotIn('wardrobe_policy', task['inputs'])
        self.assertNotIn('initial_wardrobe_state', manifest)
        self.assertTrue(all('wardrobe_state' not in row for row in manifest['segments']))
        self.assertTrue(all('wardrobe_policy' not in row['payload'] for row in requests))
        self.assertTrue(all('Wardrobe continuity (' not in row['prompt'] for row in generated))


if __name__ == '__main__':
    unittest.main()
