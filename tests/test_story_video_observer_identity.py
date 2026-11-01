"""First-person reference identity regressions; all AI/media calls are mocked."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from cores import story_video_reference as reference
from tests import test_story_video_source_continue as source_cases
from tests import test_story_video_reference_continue as flow_cases


class ObserverIdentityTests(unittest.TestCase):
    def _fixture(self):
        return source_cases.SourcePlanContinuationTests._fixture(self)

    def _native(self, *args, **kwargs):
        return source_cases.SourcePlanContinuationTests._native(self, *args, **kwargs)

    def _observer(self, plan, spoken=True):
        plan['characters'].append(dict(id='observer_01', name='我',
            appearance='仅作为叙述视角', role='主导者/叙述者'))
        for beat in plan['beats']:
            beat['cast'].append('observer_01')
            if spoken:
                beat['dialogue'].append(dict(speaker='我' if beat['index'] == 1 else 'observer_01',
                    text='让我看看。' if beat['index'] == 1 else '原来如此。'))

    def test_new_reply_keeps_source_and_raw_speech_but_only_binds_pictured_people(self):
        folder, session, request, contract, cards, plan = self._fixture()
        self._observer(plan)
        contract.setdefault('unresolved_sources', []).append(dict(text='原来如此。', reason='speaker_unresolved'))
        before = copy.deepcopy((plan, request, contract, cards))
        calls = []
        work = folder / 'planning/model'
        with patch.object(session, 'guard'), patch.object(reference.client, 'text',
                side_effect=self._native(plan, calls)):
            adopted = reference._request_plan(session, request, work, contract, cards)
        self.assertEqual(len(calls), 1)
        self.assertEqual((plan, request, contract, cards), before)
        self.assertEqual(adopted['characters'], plan['characters'][:-1])
        for old, new in zip(plan['beats'], adopted['beats']):
            self.assertEqual(new['cast'], old['cast'][:-1])
            self.assertEqual(new['dialogue'], old['dialogue'][:-1])
            self.assertEqual(new['action'], old['action'])
        response = next(work.glob('call_*/*/response.json'))
        self.assertEqual(json.loads(reference._read(response)['text']), plan)
        self.assertEqual(reference._read(work / 'accepted.json'), adopted)
        audit = reference._read(work / 'identity_normalization.json')
        self.assertEqual(len(audit['unbound_dialogue']), 2)
        self.assertEqual(audit['effective_plan_sha256'], reference._signature(adopted))
        report = reference._read(work / 'source_validation.json')
        self.assertEqual(report['status'], 'warning')
        self.assertFalse(report['quality_gate'])
        self.assertIn('first_person_observer_unbound', [row['code'] for row in report['issues']])
        self.assertEqual(reference._normalize_reference_observer(adopted, cards, contract, work), adopted)
        self.assertEqual(reference._read(work / 'identity_normalization.json'), audit)

    def test_silent_observer_is_removed_without_altering_pictured_actor_or_actions(self):
        folder, _, request, contract, cards, plan = self._fixture()
        self._observer(plan, spoken=False)
        expected = copy.deepcopy(plan)
        result = reference._normalize_reference_observer(plan, cards, contract, folder / 'model')
        reference._validate_usable_reference_plan(result, request[1], cards)
        self.assertEqual(plan, expected)
        self.assertEqual(result['characters'], expected['characters'][:-1])
        self.assertEqual([b['dialogue'] for b in result['beats']], [b['dialogue'] for b in plan['beats']])

    def test_existing_exhausted_and_accepted_replies_reuse_without_any_model_call(self):
        for cached in ('exhausted', 'accepted'):
            with self.subTest(cached=cached):
                folder, session, request, contract, cards, plan = self._fixture()
                self._observer(plan)
                work = folder / 'planning/model'
                reference._save(work / 'binding.json', dict(system=request[0], payload=request[1],
                    schema=request[2], images=[]))
                trace = work / 'call_2_fixture/text_fixture'
                reference._save(trace / 'request.json', dict(system=request[0], payload=request[1]))
                raw = dict(text=json.dumps(plan, ensure_ascii=False), meta=dict(finish_reason='stop'))
                reference._save(trace / 'response.json', raw)
                reference._save(trace.parent / 'contract_error.json', dict(error='计划人物ID或名字与固定人物卡不一致'))
                if cached == 'accepted':
                    reference._save(work / 'accepted.json', plan)
                with patch.object(session, 'guard'), patch.object(reference.client, 'text') as text:
                    result = reference._request_plan(session, request, work, contract, cards)
                text.assert_not_called()
                self.assertNotIn('observer_01', [row['id'] for row in result['characters']])
                self.assertEqual(reference._read(trace / 'response.json'), raw)
                self.assertEqual(reference._read(work / 'source_validation.json')['status'], 'warning')

    def test_pictured_me_unknown_people_and_identity_conflicts_are_not_removed(self):
        folder, _, _, contract, cards, plan = self._fixture()
        cases = []
        pictured = copy.deepcopy(plan)
        pictured['characters'][0]['name'] = '我'
        pictured['characters'][0]['role'] = '叙述者'
        me_cards = copy.deepcopy(cards)
        me_cards[0]['name'] = '我'
        cases.append((pictured, me_cards))
        for changes in (dict(name='新同伴'), dict(role='正脸出镜的叙述者'),
                        dict(role='同伴', appearance='普通同行者'), dict(id=cards[0]['name'])):
            value = copy.deepcopy(plan)
            self._observer(value)
            value['characters'][-1].update(changes)
            cases.append((value, cards))
        conflict = copy.deepcopy(plan)
        self._observer(conflict)
        conflict['props'].append(dict(id='observer_01', name='路牌', appearance='木制路牌'))
        cases.append((conflict, cards))
        for value, fixed in cases:
            with self.subTest(character=value['characters'][-1]):
                self.assertEqual(reference._normalize_reference_observer(value, fixed, contract,
                    folder / 'model'), value)

    def test_fallback_to_first_usable_draft_keeps_its_own_warning_record(self):
        folder, session, request, contract, cards, original = self._fixture()
        def candidate(attempt):
            plan = copy.deepcopy(original)
            self._observer(plan)
            plan['beats'][0]['dialogue'][0]['text'] = '先观察。'
            if attempt == 2:
                plan['beats'][0]['seconds'] = 99
                plan['beats'][0]['dialogue'][-1]['text'] = '第二稿的观察者原话。'
            return plan
        calls = []
        work = folder / 'planning/model'
        with patch.object(session, 'guard'), patch.object(reference.client, 'text',
                side_effect=self._native(candidate, calls)):
            adopted = reference._request_plan(session, request, work, contract, cards)
        self.assertEqual(len(calls), 2)
        self.assertEqual(adopted['beats'][0]['seconds'], 15)
        self.assertEqual(reference._read(work / 'effective_plan.json'), adopted)
        report = reference._read(work / 'identity_normalization.json')
        self.assertEqual(report['effective_plan_sha256'], reference._signature(adopted))
        self.assertEqual(report['unbound_dialogue'][0]['dialogue']['text'], '让我看看。')
        self.assertIn('first_person_observer_unbound', [row['code'] for row in
            reference._read(work / 'source_validation.json')['issues']])

    def test_stage_cache_is_verified_then_normalized_without_rewriting_original_receipt(self):
        folder, session, request, contract, cards, plan = self._fixture()
        self._observer(plan)
        work = folder / 'planning'
        cache_inputs = dict(request=request)
        reference._save(work / 'result.json', plan)
        receipt = dict(input_sha256=reference._signature(cache_inputs),
                       result_sha256=reference._sha(work / 'result.json'))
        reference._save(work / 'receipt.json', receipt)
        action = unittest.mock.Mock()
        cached = session.cached('planning', cache_inputs, action)
        result = reference._normalize_reference_observer(cached, cards, contract, work / 'model')
        reference._validate_usable_reference_plan(result, request[1], cards)
        action.assert_not_called()
        self.assertEqual(reference._read(work / 'result.json'), plan)
        self.assertEqual(reference._read(work / 'receipt.json'), receipt)
        self.assertEqual(reference._read(work / 'model/effective_plan.json'), result)
        broken = copy.deepcopy(plan)
        broken['title'] = '篡改缓存'
        reference._save(work / 'result.json', broken)
        with self.assertRaisesRegex(RuntimeError, '缓存或输入发生变化'):
            session.cached('planning', cache_inputs, action)

    def test_full_mock_flow_completes_and_observer_never_enters_voice_or_picture_slots(self):
        result = flow_cases.ReferenceContinueTests._flow(self, mutate_plan=self._observer)
        folder, _, manifest, output, requests, generated, joined = result
        self.assertTrue(output['generation_completed'])
        self.assertEqual(len(generated), 2)
        self.assertEqual(len(joined), 1)
        self.assertEqual(len(manifest['plan']['characters']), 4)
        self.assertIn('first_person_observer_unbound', [row['code'] for row in manifest['source_validation']['issues']])
        self.assertTrue(manifest['continuation_warnings'])
        for request in requests:
            if 'dialogue_slots' in request['payload']:
                self.assertTrue(all(line['speaker'] not in ('我', 'observer_01')
                    for line in request['payload']['dialogue_slots']))
        self.assertEqual(len(reference._read(folder / 'planning/model/identity_normalization.json')['unbound_dialogue']), 2)


if __name__ == '__main__':
    unittest.main()
