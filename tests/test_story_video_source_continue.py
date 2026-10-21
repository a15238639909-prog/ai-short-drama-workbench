"""Source-plan continuation checks. No local model, real video or deletion."""
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
    from tests import test_story_video_reference_continue as continuation_cases
    from tests.test_story_video_reference_runtime import fixture, Handle
except ImportError:
    import test_story_video_reference_continue as continuation_cases
    from test_story_video_reference_runtime import fixture, Handle


class SourcePlanContinuationTests(unittest.TestCase):
    def _fixture(self):
        folder, task, plan = fixture()
        cards = task['inputs']['character_cards']
        source = task['inputs']['story_direction']
        contract = characters.prepare_source_contract(source, cards)
        request = reference.prompts.plan_request(source, task['inputs']['style'], 2, 15, {})
        request[1].update(source_contract=copy.deepcopy(contract),
            canonical_characters=characters.authoring_characters(cards))
        session = reference._Session(task['id'], Handle(task, []), folder, task['inputs'], {})
        return folder, session, request, contract, cards, plan

    def _native(self, candidate, calls, failure=None):
        def text(system, payload, directory, **options):
            calls.append(copy.deepcopy(payload))
            trace = Path(directory) / 'text_fixture'
            reference._save(trace / 'request.json', dict(system=system, payload=payload))
            if failure:
                reference._save(trace / 'error.json', dict(error=str(failure)))
                raise failure
            value = candidate(len(calls)) if callable(candidate) else candidate
            raw = json.dumps(value, ensure_ascii=False)
            reference._save(trace / 'response.json', dict(text=raw, meta=dict(finish_reason='stop')))
            return raw
        return text

    def test_bounded_repair_adopts_latest_native_plan_with_source_warning(self):
        folder, session, request, contract, cards, original = self._fixture()
        before = copy.deepcopy((request, contract, cards))
        def candidate(attempt):
            value = copy.deepcopy(original)
            value['beats'][0]['dialogue'][0]['text'] = '再看看。' if attempt == 1 else '一起看看。'
            return value
        calls = []
        work = folder / 'planning' / 'model'
        with patch.object(session, 'guard'), patch.object(reference.client, 'text',
                side_effect=self._native(candidate, calls)):
            adopted = reference._request_plan(session, request, work, contract, cards)
        self.assertEqual(len(calls), 2)
        self.assertEqual(adopted, candidate(2))
        self.assertEqual((request, contract, cards), before)
        report = reference._read(work / 'source_validation.json')
        self.assertEqual(report['status'], 'warning')
        self.assertFalse(report['quality_gate'])
        self.assertNotIn('passed', report)
        self.assertTrue(report['issues'])
        self.assertNotEqual(report['expected'], report['actual'])
        self.assertTrue(report['raw_response_paths'])
        for path in report['raw_response_paths']:
            self.assertTrue(Path(path).exists())

    def test_valid_strict_plan_is_verified_without_repair(self):
        folder, session, request, contract, cards, plan = self._fixture()
        calls = []
        work = folder / 'planning' / 'model'
        with patch.object(session, 'guard'), patch.object(reference.client, 'text',
                side_effect=self._native(plan, calls)):
            adopted = reference._request_plan(session, request, work, contract, cards)
        self.assertEqual(adopted, plan)
        self.assertEqual(len(calls), 1)
        report = reference._read(work / 'source_validation.json')
        self.assertEqual(report['status'], 'verified')
        self.assertEqual(report['issues'], [])

    def test_cross_segment_action_and_speech_estimate_are_warnings_after_bounded_repair(self):
        def cross_segment(value):
            value['beats'][0]['action'] = '诺亚转身说：“明白。”'
        def speech_budget(value):
            for line in value['beats'][0]['dialogue']:
                line['text'] = '我们先查看路牌。' * 6
        for mutate, issue_text in ((cross_segment, '其他段'), (speech_budget, '预算')):
            with self.subTest(case=mutate.__name__):
                folder, session, request, contract, cards, plan = self._fixture()
                mutate(plan)
                before = copy.deepcopy(plan)
                calls = []
                work = folder / 'plan'
                with patch.object(session, 'guard'), patch.object(reference.client, 'text',
                        side_effect=self._native(plan, calls)):
                    adopted = reference._request_plan(session, request, work, contract, cards)
                self.assertEqual(adopted, before)
                self.assertEqual(len(calls), 2)
                report = reference._read(work / 'source_validation.json')
                self.assertEqual(report['status'], 'warning')
                self.assertIn(issue_text, str(report['issues']))
                self.assertNotIn('passed', report)

    def test_existing_exhausted_reply_is_reused_without_another_model_call(self):
        folder, session, request, contract, cards, plan = self._fixture()
        plan['beats'][0]['dialogue'][0]['text'] = '再观察一下。'
        work = folder / 'planning' / 'model'
        reference._save(work / 'binding.json', dict(system=request[0], payload=request[1],
            schema=request[2], images=[]))
        for attempt in (1, 2):
            call = work / ('call_%d_fixture' % attempt)
            trace = call / 'text_fixture'
            reference._save(trace / 'request.json', dict(system=request[0], payload=request[1]))
            reference._save(trace / 'response.json', dict(text=json.dumps(plan, ensure_ascii=False),
                meta=dict(finish_reason='stop')))
            reference._save(call / 'contract_error.json', dict(error='计划对白与正文发声来源不一致'))
        with patch.object(session, 'guard'), patch.object(reference.core, 'request_json') as ask:
            adopted = reference._request_plan(session, request, work, contract, cards)
        self.assertEqual(adopted, plan)
        ask.assert_not_called()
        self.assertEqual(reference._read(work / 'source_validation.json')['status'], 'warning')

    def test_estimated_schema_dialogue_capacity_is_warning_after_two_repairs(self):
        folder, session, request, contract, cards, plan = self._fixture()
        text_limit = request[2]['properties']['beats']['items']['properties']['dialogue']['items']['properties']['text']['maxLength']
        plan['beats'][0]['dialogue'][0]['text'] = '路' * (text_limit + 1)
        calls = []
        work = folder / 'plan'
        with patch.object(session, 'guard'), patch.object(reference.client, 'text',
                side_effect=self._native(plan, calls)):
            adopted = reference._request_plan(session, request, work, contract, cards)
        self.assertEqual(len(calls), 2)
        self.assertEqual(adopted, plan)
        report = reference._read(work / 'source_validation.json')
        self.assertEqual(report['status'], 'warning')
        self.assertFalse(report['quality_gate'])
        self.assertNotIn('passed', report)

    def test_newer_native_service_failure_is_not_hidden_by_old_exhausted_responses(self):
        folder, session, request, contract, cards, plan = self._fixture()
        plan['beats'][0]['dialogue'][0]['text'] = '再观察一下。'
        work = folder / 'planning' / 'model'
        reference._save(work / 'binding.json', dict(system=request[0], payload=request[1],
            schema=request[2], images=[]))
        for attempt in (1, 2):
            call = work / ('call_%d_fixture' % attempt)
            trace = call / 'text_fixture'
            reference._save(trace / 'request.json', dict(system=request[0], payload=request[1]))
            reference._save(trace / 'response.json', dict(text=json.dumps(plan, ensure_ascii=False),
                meta=dict(finish_reason='stop')))
            reference._save(call / 'contract_error.json', dict(error='计划对白与正文发声来源不一致'))
        latest = work / 'call_3_fixture' / 'text_fixture'
        reference._save(latest / 'request.json', dict(system=request[0], payload=request[1]))
        reference._save(latest / 'error.json', dict(error='本地文字模型服务不可用'))
        calls = []
        with patch.object(session, 'guard'), patch.object(reference.client, 'text',
                side_effect=self._native(plan, calls, RuntimeError('本地文字模型服务不可用'))):
            with self.assertRaisesRegex(RuntimeError, '服务不可用'):
                reference._request_plan(session, request, work, contract, cards)
        self.assertEqual(len(calls), 1)
        self.assertFalse((work / 'source_validation.json').exists())

    def test_unknown_duplicate_identity_and_invalid_numeric_time_are_not_softened(self):
        def unknown(value):
            value['characters'][0]['id'] = 'unknown_person'
        def duplicate(value):
            value['characters'][1]['id'] = value['characters'][0]['id']
        def changed_name(value):
            value['characters'][0]['name'] = '错误人物'
        def seconds(value):
            value['beats'][0]['seconds'] = 99
        def numeric_time(value):
            value['beats'][0]['dialogue'][0].update(start=10, end=1)
        def missing_schema(value):
            value.pop('beats')
        for mutate in (unknown, duplicate, changed_name, seconds, numeric_time, missing_schema):
            with self.subTest(case=mutate.__name__):
                folder, session, request, contract, cards, plan = self._fixture()
                mutate(plan)
                calls = []
                with patch.object(session, 'guard'), patch.object(reference.client, 'text',
                        side_effect=self._native(plan, calls)), self.assertRaises((RuntimeError, ValueError)):
                    reference._request_plan(session, request, folder / 'plan', contract, cards)
                self.assertLessEqual(len(calls), 2)
                report = folder / 'plan' / 'source_validation.json'
                if report.exists():
                    self.assertNotEqual(reference._read(report).get('status'), 'warning')

    def test_service_cancel_and_game_guards_propagate(self):
        for error in (RuntimeError('本地文字模型服务不可用'), reference.core.ControlStop(),
                      RuntimeError('PUBG正在运行，任务已保留并暂停')):
            with self.subTest(error=type(error).__name__ + str(error)):
                folder, session, request, contract, cards, plan = self._fixture()
                calls = []
                with patch.object(session, 'guard'), patch.object(reference.client, 'text',
                        side_effect=self._native(plan, calls, error)), self.assertRaises(type(error)):
                    reference._request_plan(session, request, folder / 'plan', contract, cards)
                self.assertEqual(len(calls), 1)

    def test_changed_cached_request_is_not_swallowed(self):
        folder, session, request, contract, cards, _ = self._fixture()
        work = folder / 'planning' / 'model'
        reference._save(work / 'binding.json', dict(system='different', payload=request[1],
            schema=request[2], images=[]))
        with patch.object(session, 'guard'), patch.object(reference.core, 'request_json') as ask:
            with self.assertRaisesRegex(RuntimeError, '变化|签名'):
                reference._request_plan(session, request, work, contract, cards)
        ask.assert_not_called()


class SourceContinuationFlowTests(unittest.TestCase):
    def test_over_capacity_dialogue_finishes_and_keeps_native_director_draft(self):
        words = '我们看看这路牌' * 10
        self.assertEqual(len(words), 70)
        def capacity(plan):
            plan['beats'][0]['dialogue'][0]['text'] = words
        original_ask = reference._Session.ask
        strict_errors = []
        def validated_ask(session, request, directory, images=None, validator=None):
            result = original_ask(session, request, directory, images=images, validator=validator)
            if 'dialogue_slots' not in request[1] or validator is None:
                return result
            for attempt in range(2):
                call = Path(directory) / ('call_%d_director_fixture' % (attempt + 1))
                trace = call / 'text_fixture'
                reference._save(trace / 'request.json', dict(system=request[0], payload=request[1]))
                reference._save(trace / 'response.json', dict(text=json.dumps(result, ensure_ascii=False),
                    meta=dict(finish_reason='stop')))
                try:
                    validator(result, request[1])
                    return result
                except ValueError as error:
                    strict_errors.append(str(error))
                    reference._save(call / 'contract_error.json', dict(error=str(error)))
                    if attempt:
                        raise RuntimeError('本地模型格式修复仍未通过：' + str(error)) from error
        with patch.object(reference._Session, 'ask', new=validated_ask):
            result = continuation_cases.ReferenceContinueTests._flow(self, mutate_plan=capacity)
        folder, task, manifest, output, requests, generated, joined = result
        self.assertEqual(len(generated), 2)
        self.assertEqual(len(joined), 1)
        self.assertEqual(output['seconds'], 30)
        self.assertTrue(output['generation_completed'])
        self.assertEqual(output['quality_status'], 'completed_with_warnings')
        self.assertNotIn('quality_passed', output)
        self.assertEqual(manifest['plan']['beats'][0]['dialogue'][0]['text'], words)
        director = reference._read(Path(manifest['segments'][0]['prompt_path']).with_name('director_raw.json'))
        self.assertEqual(director['dialogue'][0]['text'], words)
        self.assertEqual((director['dialogue'][0]['start'], director['dialogue'][0]['end']), (.5, 2))
        self.assertEqual(len(strict_errors), 2)
        report = reference._read(Path(manifest['segments'][0]['prompt_path']).with_name('speech_budget_warning.json'))
        self.assertEqual(report['status'], 'warning')
        self.assertFalse(report['quality_gate'])
        self.assertNotIn('passed', report)
        self.assertTrue(report['issues'])
        self.assertIn('预算', str(manifest['continuation_warnings']))
        self.assertEqual((folder / 'story.txt').read_text(encoding='utf-8'),
                         task['inputs']['story_direction'])
        for row in manifest['segments']:
            ledger = reference._read(Path(row['raw_path']).with_name('video_submission.json'))
            self.assertEqual(ledger['submissions'], 1)

    def test_unquoted_and_unknown_source_evidence_does_not_stop_full_reference_flow(self):
        source = '伊莱恩走向路牌，说我们换条路，然后查看远山。她轻声说门锁住了。'
        result = continuation_cases.ReferenceContinueTests._flow(self, source_text=source)
        folder, task, manifest, output, requests, generated, joined = result
        self.assertEqual((folder / 'story.txt').read_text(encoding='utf-8'), source)
        self.assertEqual(manifest['source_contract']['dialogue'], [dict(
            speaker_id='char_01', speaker='伊莱恩', text='我们换条路')])
        self.assertEqual(manifest['source_contract']['evidence_status'], 'partially_unavailable')
        self.assertTrue(manifest['source_contract']['unresolved_sources'])
        self.assertEqual(len(generated), 2)
        self.assertEqual(len(joined), 1)
        self.assertEqual(output['quality_status'], 'completed_with_warnings')
        self.assertTrue(output['generation_completed'])

    def test_reference_source_mismatch_finishes_two_clips_one_join_without_editing_source(self):
        def mismatch(plan):
            plan['beats'][0]['dialogue'][0]['text'] = '一起看看。'
        result = continuation_cases.ReferenceContinueTests._flow(self, mutate_plan=mismatch)
        folder, task, manifest, output, requests, generated, joined = result
        source = task['inputs']['story_direction']
        self.assertEqual((folder / 'story.txt').read_text(encoding='utf-8'), source)
        self.assertEqual(manifest['source_story'], source)
        self.assertEqual(manifest['source_contract']['dialogue'][0]['text'], '可以。')
        self.assertEqual(manifest['plan']['beats'][0]['dialogue'][0]['text'], '一起看看。')
        self.assertEqual(len(generated), 2)
        self.assertEqual(len(joined), 1)
        self.assertEqual(output['seconds'], 30)
        self.assertTrue(output['generation_completed'])
        self.assertEqual(output['quality_status'], 'completed_with_warnings')
        self.assertNotIn('quality_passed', output)
        report = reference._read(folder / 'planning' / 'model' / 'source_validation.json')
        self.assertEqual(report['status'], 'warning')
        self.assertNotIn('passed', report)
        self.assertTrue(manifest['continuation_warnings'])
        self.assertEqual(len(list((folder / 'planning' / 'model').glob('call_*/contract_error.json'))), 2)
        for index in (1, 2):
            row = manifest['segments'][index - 1]
            ledger = reference._read(Path(row['raw_path']).with_name('video_submission.json'))
            self.assertEqual(ledger['submissions'], 1)


if __name__ == '__main__':
    unittest.main()
