"""Reference director recovery; all models/media are mocked and scratch files retained."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from cores import story_video_reference as reference
from cores import story_video_characters as characters
try:
    from tests.test_story_video_reference_runtime import fixture, Handle
except ImportError:
    from test_story_video_reference_runtime import fixture, Handle


class ReferenceDirectorRecoveryTests(unittest.TestCase):
    def _fixture(self, people=1):
        folder, task, plan = fixture(scene=False)
        cards = task['inputs']['character_cards'][:people]
        task['inputs']['character_cards'] = cards
        plan['characters'] = plan['characters'][:people]
        beat = plan['beats'][0]
        beat.update(cast=[card['id'] for card in cards],
                    dialogue=[dict(speaker=cards[-1]['name'], text='嗯。')])
        plan['beats'] = [beat]
        previous = folder / 'fake_previous_frame.png'
        previous.write_bytes(b'fixture-only previous frame')
        refs = characters.references_for_segment(cards, previous_frame=str(previous))
        request = reference.prompts.write_request(plan, beat, {}, 15, mode='ref2va',
            references=refs, visual_style=task['inputs']['style'])
        raw = dict(integrated_multimodal_description='[Shot 1] 中景呈现人物自然观察并回应。',
            overall_soundscape='自然风声。', non_diegetic_music='N/A', brief='观察并回应。',
            expected_end_state='人物保持自然互动。',
            timeline=[dict(start=start, end=start + 5, action='自然观察', visible_result='反应清楚')
                      for start in (0, 5, 10)],
            dialogue=[dict(speaker=cards[-1]['name'], text='嗯。', start=1, end=2,
                           delivery='自然清楚地回应。')], continuity_notes=[])
        session = reference._Session(task['id'], Handle(task, []), folder, task['inputs'], {})
        candidates = []

        def validate(candidate, payload):
            characters.bind_delivery(candidate, dict(payload, speech_budget_policy='advisory'),
                                     cards, refs, compiler=reference.prompts.compile_written)
            candidates.append(copy.deepcopy(candidate))
            reference.prompts.validate_written(candidate, payload)
            characters.bind_delivery(candidate, payload, cards, refs,
                                     compiler=reference.prompts.compile_written)

        return folder, session, request, cards, refs, raw, candidates, validate

    def _run(self, data, work):
        _, session, request, cards, refs, _, candidates, validate = data
        return reference._request_reference_director(session, request, work,
                                                     cards, refs, validate, candidates)

    def _native(self, candidate, calls, failure=None):
        def text(system, payload, directory, **options):
            calls.append(copy.deepcopy(payload))
            trace = Path(directory) / 'text_fixture'
            reference._save(trace / 'request.json', dict(system=system, payload=payload))
            if failure is not None:
                reference._save(trace / 'error.json', dict(error=str(failure)))
                raise failure
            value = candidate(len(calls)) if callable(candidate) else candidate
            raw = json.dumps(value, ensure_ascii=False)
            reference._save(trace / 'response.json', dict(text=raw, meta=dict(finish_reason='stop')))
            return raw
        return text

    def _seed_failed(self, data, work, candidate):
        request = data[2]
        reference._save(work / 'binding.json', dict(system=request[0], payload=request[1],
            schema=request[2], images=[]))
        for attempt in (1, 2):
            call = work / ('call_%d_fixture' % attempt)
            trace = call / 'text_fixture'
            reference._save(trace / 'request.json', dict(system=request[0], payload=request[1]))
            reference._save(trace / 'response.json', dict(text=json.dumps(candidate, ensure_ascii=False),
                meta=dict(finish_reason='stop')))
            reference._save(call / 'contract_error.json', dict(error='引用了未绑定素材'))

    def _bad_subject(self, raw, number=2):
        bad = copy.deepcopy(raw)
        bad['integrated_multimodal_description'] += ' <Subject %d>抬起头。' % number
        return bad

    def test_two_bad_subjects_fall_back_without_binding_the_previous_picture_as_actor(self):
        data = self._fixture()
        folder, session, request, cards, refs, raw, _, _ = data
        before = copy.deepcopy((request, cards, refs, raw))
        calls, replies = [], []

        def candidate(attempt):
            value = self._bad_subject(raw, attempt + 1)
            replies.append(copy.deepcopy(value))
            return value

        work = folder / 'writing'
        with patch.object(session, 'guard'), patch.object(reference.client, 'text',
                side_effect=self._native(candidate, calls)):
            adopted, advisory, report = self._run(data, work)
        self.assertEqual(len(calls), 2)
        self.assertIn('引用了未绑定素材', calls[1]['correction'])
        self.assertEqual([ref['role'] for ref in refs], ['character', 'previous_frame'])
        self.assertEqual(refs[1]['picture'], 2)
        self.assertNotIn('subject', refs[1])
        self.assertTrue(advisory)
        self.assertEqual(report['policy'], 'saved-beat-director-fallback-v1')
        self.assertFalse(report['quality_gate'])
        self.assertNotIn('passed', report)
        self.assertEqual(adopted['integrated_multimodal_description'], '[Shot 1] ' + request[1]['beat']['action'])
        compiled = characters.bind_delivery(adopted, request[1], cards, refs)
        self.assertIn('<Picture 2>', compiled['prompt'])
        self.assertNotIn('<Subject 2>', compiled['prompt'])
        self.assertNotIn('<Subject 3>', compiled['prompt'])
        self.assertEqual(compiled['voice_binding_audit'][0]['character_id'], cards[0]['id'])
        self.assertEqual(compiled['voice_binding_audit'][0]['subject'], 1)
        self.assertEqual([(line['speaker'], line['text']) for line in compiled['dialogue']],
                         [(cards[0]['name'], '嗯。')])
        self.assertEqual((request, cards, refs, raw), before)
        self.assertEqual(len(report['raw_response_paths']), 2)
        retained = [json.loads(reference._read(path)['text']) for path in report['raw_response_paths']]
        self.assertEqual(retained, replies)

    def test_valid_native_draft_is_used_once_without_fallback(self):
        data = self._fixture()
        folder, session, _, _, _, raw, _, _ = data
        calls = []
        work = folder / 'writing'
        with patch.object(session, 'guard'), patch.object(reference.client, 'text',
                side_effect=self._native(raw, calls)):
            adopted, advisory, report = self._run(data, work)
        self.assertEqual(adopted, raw)
        self.assertEqual(len(calls), 1)
        self.assertFalse(advisory)
        self.assertIsNone(report)
        self.assertFalse((work / 'source_fallback.json').exists())

    def test_old_exhausted_bad_replies_use_fallback_without_model_call(self):
        data = self._fixture()
        folder, session, _, _, _, raw, _, _ = data
        work = folder / 'writing'
        self._seed_failed(data, work, self._bad_subject(raw))
        with patch.object(session, 'guard'), patch.object(reference.client, 'text') as model:
            adopted, advisory, report = self._run(data, work)
        model.assert_not_called()
        self.assertTrue(advisory)
        self.assertEqual(adopted, report['candidate'])

    def test_old_complete_usable_response_is_revalidated_before_fallback(self):
        data = self._fixture()
        folder, session, _, _, _, raw, _, _ = data
        work = folder / 'writing'
        self._seed_failed(data, work, raw)
        with patch.object(session, 'guard'), patch.object(reference.client, 'text') as model:
            adopted, advisory, report = self._run(data, work)
        model.assert_not_called()
        self.assertEqual(adopted, raw)
        self.assertFalse(advisory)
        self.assertIsNone(report)
        self.assertFalse((work / 'source_fallback.json').exists())

    def test_saved_fallback_is_reused_without_model_call(self):
        data = self._fixture()
        folder, session, _, _, _, raw, candidates, _ = data
        work = folder / 'writing'
        self._seed_failed(data, work, self._bad_subject(raw))
        with patch.object(session, 'guard'), patch.object(reference.client, 'text') as model:
            first = self._run(data, work)
            candidates.clear()
            second = self._run(data, work)
        model.assert_not_called()
        self.assertEqual(second, first)

    def test_wrong_bound_delivery_is_rejected_and_not_silently_reassigned(self):
        data = self._fixture(people=2)
        folder, session, request, cards, refs, raw, _, _ = data
        bad = copy.deepcopy(raw)
        bad['dialogue'][0]['delivery'] = '<Subject 1>，自然回应。'
        before = copy.deepcopy(bad)
        with self.assertRaisesRegex(ValueError, '说话者不符'):
            characters.bind_delivery(bad, request[1], cards, refs)
        calls = []
        with patch.object(session, 'guard'), patch.object(reference.client, 'text',
                side_effect=self._native(bad, calls)):
            adopted, _, report = self._run(data, folder / 'writing')
        self.assertEqual(len(calls), 2)
        self.assertEqual(bad, before)
        self.assertEqual(adopted['dialogue'][0]['speaker'], cards[1]['name'])
        compiled = characters.bind_delivery(adopted, request[1], cards, refs)
        self.assertEqual(compiled['voice_binding_audit'][0]['subject'], 2)
        self.assertEqual(compiled['voice_binding_audit'][0]['character_id'], cards[1]['id'])
        self.assertIn('说话者不符', report['reason'])

    def test_changed_request_binding_is_not_swallowed(self):
        data = self._fixture()
        folder, session, request, _, _, _, _, _ = data
        work = folder / 'writing'
        reference._save(work / 'binding.json', dict(system='changed', payload=request[1],
            schema=request[2], images=[]))
        with patch.object(session, 'guard'), patch.object(reference.client, 'text') as model:
            with self.assertRaisesRegex(RuntimeError, '变化'):
                self._run(data, work)
        model.assert_not_called()
        self.assertFalse((work / 'source_fallback.json').exists())

    def test_tampered_fallback_request_or_candidate_is_rejected(self):
        for field in ('request_sha256', 'candidate'):
            with self.subTest(field=field):
                data = self._fixture()
                folder, session, _, _, _, raw, _, _ = data
                work = folder / 'writing'
                self._seed_failed(data, work, self._bad_subject(raw))
                with patch.object(session, 'guard'), patch.object(reference.client, 'text') as model:
                    self._run(data, work)
                    marker = work / 'source_fallback.json'
                    report = reference._read(marker)
                    if field == 'candidate':
                        report['candidate']['dialogue'][0]['text'] = '被改动。'
                    else:
                        report[field] = 'changed'
                    reference._save(marker, report)
                    with self.assertRaisesRegex(RuntimeError, '缓存或输入发生变化'):
                        self._run(data, work)
                model.assert_not_called()

    def test_service_cancel_and_pubg_failures_propagate_without_fallback(self):
        errors = (RuntimeError('本地文字模型服务不可用'), reference.core.ControlStop(),
                  RuntimeError('PUBG正在运行，任务已保留并暂停'))
        for error in errors:
            with self.subTest(error=type(error).__name__ + str(error)):
                data = self._fixture()
                folder, session, _, _, _, raw, _, _ = data
                calls = []
                work = folder / 'writing'
                with patch.object(session, 'guard'), patch.object(reference.client, 'text',
                        side_effect=self._native(raw, calls, error)):
                    with self.assertRaises(type(error)):
                        self._run(data, work)
                self.assertEqual(len(calls), 1)
                self.assertFalse((work / 'source_fallback.json').exists())

    def test_newer_service_error_is_not_hidden_by_old_replies_or_fallback(self):
        for cached_fallback in (False, True):
            with self.subTest(cached_fallback=cached_fallback):
                data = self._fixture()
                folder, session, _, _, _, raw, _, _ = data
                work = folder / 'writing'
                self._seed_failed(data, work, self._bad_subject(raw))
                with patch.object(session, 'guard'):
                    if cached_fallback:
                        self._run(data, work)
                trace = work / 'call_3_fixture' / 'text_fixture'
                reference._save(trace / 'request.json', {})
                reference._save(trace / 'error.json', dict(error='本地文字模型服务不可用'))
                calls = []
                error = RuntimeError('本地文字模型服务不可用')
                with patch.object(session, 'guard'), patch.object(reference.client, 'text',
                        side_effect=self._native(raw, calls, error)):
                    with self.assertRaisesRegex(RuntimeError, '服务不可用'):
                        self._run(data, work)
                self.assertEqual(len(calls), 1)

    def test_session_failure_prevents_optional_format_recovery(self):
        data = self._fixture()
        folder, session, _, _, _, _, _, _ = data
        session.failure = RuntimeError('本地服务错误')
        work = folder / 'writing'
        error = RuntimeError('本地模型格式修复仍未通过：引用了未绑定素材')
        with patch.object(session, 'guard'), patch.object(session, 'ask', side_effect=error):
            with self.assertRaisesRegex(RuntimeError, '格式修复仍未通过'):
                self._run(data, work)
        self.assertFalse((work / 'source_fallback.json').exists())

    def test_game_or_cancel_guard_still_runs_before_cached_fallback(self):
        for error in (RuntimeError('PUBG正在运行，任务已保留并暂停'), reference.core.ControlStop()):
            with self.subTest(error=type(error).__name__):
                data = self._fixture()
                folder, session, _, _, _, raw, _, _ = data
                work = folder / 'writing'
                self._seed_failed(data, work, self._bad_subject(raw))
                with patch.object(session, 'guard'):
                    self._run(data, work)
                with patch.object(session, 'guard', side_effect=error), \
                        patch.object(reference.client, 'text') as model:
                    with self.assertRaises(type(error)):
                        self._run(data, work)
                model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
