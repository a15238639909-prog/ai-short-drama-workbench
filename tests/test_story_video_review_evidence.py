"""Review evidence recovery, without inference, video IO or fixture deletion."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ['V41_NO_MODEL'] = '1'

from cores import story_video_core as core
from cores import story_video_mode_prompts as modes
from cores import story_video_prompts as legacy
from tests.test_story_video_core import plan_fixture, review_fixture


BRIEF = '疏通山泉并完成结局，保持原有角色与因果动作。'
INVALID_PARAMETER_QUOTE = '"seconds": 15.0'


def issue(severity='advisory', quote='turn the ring'):
    return dict(beat=1, category='capacity', severity=severity,
        source_quote=quote, reason='让转环后的回应更清楚。',
        repair_direction='在当前段展示动作结果，不添加新剧情。')


def result(issues=()):
    issues = copy.deepcopy(list(issues))
    return dict(passed=not any(item['severity'] == 'blocking' for item in issues),
                issues=issues, strengths=[])


class ReviewEvidenceTests(unittest.TestCase):
    def setUp(self):
        # Retain small raw, accepted and audit files for explicit recycling.
        self.root = Path(tempfile.mkdtemp(prefix='story_review_evidence_zero_'))
        self.plan = plan_fixture()
        self.plan['beats'] = [dict(copy.deepcopy(self.plan['beats'][0]),
            index=index, seconds=15) for index in range(1, 6)]
        self.inputs = dict(story_direction=BRIEF, style='自然电影质感',
            segment_count=5, segment_seconds=15, flow_version=2,
            quality_mode='automatic', input_mode='text', director_profile=modes.PROFILE)
        self.raw_replies = []

    def request(self, *, blocking_only=True):
        return modes.for_mode('text').review_request(self.plan, BRIEF,
            self.inputs['style'], 5, 15, blocking_only=blocking_only)

    def manifest(self):
        return dict(plan=copy.deepcopy(self.plan), segments=[])

    def mock_requests(self, replies):
        replies = iter(copy.deepcopy(replies))

        def respond(request, directory, images=None, validator=None):
            value = next(replies)
            core.validate_schema(value, request[2])
            if validator:
                validator(value, request[1])
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            raw = json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')
            for name in ('raw_reply.json', 'accepted.json'):
                (directory/name).write_bytes(raw)
            self.raw_replies.append((value, copy.deepcopy(value), directory, raw))
            return value

        return respond

    def assert_raw_retained(self):
        for value, snapshot, directory, raw in self.raw_replies:
            self.assertEqual(value, snapshot)
            self.assertEqual((directory/'raw_reply.json').read_bytes(), raw)
            self.assertEqual((directory/'accepted.json').read_bytes(), raw)

    def test_sources_use_story_strings_and_verified_duration_not_arbitrary_payload(self):
        payload = self.request()[1]
        sources = core._plan_review_sources(payload)
        self.assertIn(BRIEF, sources)
        self.assertIn('turn the ring', sources)
        self.assertIn(payload['duration_contract_text'], sources)
        self.assertNotIn(INVALID_PARAMETER_QUOTE, sources)
        self.assertNotIn(self.inputs['style'], sources)
        payload['other_evidence'] = '不能凭新增字段变成证据'
        self.assertNotIn(payload['other_evidence'], core._plan_review_sources(payload))
        self.assertNotIn('15.0', core._plan_review_sources(payload))

    def test_canonical_parameter_quote_is_recomputed_for_all_contract_fields(self):
        payload = self.request()[1]
        self.assertEqual(payload['duration_contract_text'],
            '共5段，每段15秒，全片75秒。seconds与timing_budget仅约束单段。')
        quote = '每段15秒，全片75秒'
        core.validate_plan_review(result([issue('blocking', quote)]), payload)
        changes = (
            lambda p: p.update(duration_contract_text='全片15秒'),
            lambda p: p['timing_contract'].update(total_seconds=15),
            lambda p: p['timing_contract'].update(seconds_scope='whole_film'),
            lambda p: p.update(count=4),
            lambda p: p.update(seconds=8),
            lambda p: p.update(parameter_sources=[p['duration_contract_text'], INVALID_PARAMETER_QUOTE]),
            lambda p: p.update(parameter_sources=[INVALID_PARAMETER_QUOTE]),
            lambda p: p.pop('review_evidence_policy'),
        )
        for change in changes:
            changed = copy.deepcopy(payload)
            change(changed)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'source_quote'):
                core.validate_plan_review(result([issue('blocking', quote)]), changed)
        forged = copy.deepcopy(payload)
        forged['parameter_sources'] = [INVALID_PARAMETER_QUOTE]
        with self.assertRaisesRegex(ValueError, 'source_quote'):
            core.validate_plan_review(result([issue('blocking', INVALID_PARAMETER_QUOTE)]), forged)

    def test_invalid_advisory_is_quarantined_with_unchanged_model_verdict_and_raw(self):
        invalid = issue(quote=INVALID_PARAMETER_QUOTE)
        invalid.update(reason='全片只能15秒，应把5段各缩短为3秒。',
                       repair_direction='缩短每段时长为3秒。')
        original = result([invalid])
        request = self.request()
        payload_before = copy.deepcopy(request[1])
        with patch.object(core, 'request_json', side_effect=self.mock_requests([original])):
            adopted = core._request_plan_review(request, self.root/'review')
        self.assertTrue(adopted['passed'])
        self.assertEqual(adopted['issues'], [])
        self.assertEqual(adopted['evidence_status'], 'partially_unavailable')
        self.assertEqual(adopted['unverified_advisories'][0]['issue'], invalid)
        audit = json.loads(Path(adopted['evidence_audit_path']).read_text(encoding='utf-8'))
        self.assertTrue(audit['model_passed'])
        self.assertTrue(audit['adopted_passed'])
        self.assertEqual(audit['unverified_advisories'], adopted['unverified_advisories'])
        self.assertEqual(Path(audit['raw_review_path']), self.root/'review/accepted.json')
        self.assertEqual(request[1], payload_before)
        self.assert_raw_retained()

    def test_gate_does_not_rewrite_story_or_send_unverified_advice_to_writer(self):
        manifest = self.manifest()
        before = copy.deepcopy((manifest['plan'], self.inputs))
        valid = issue()
        invalid = issue(quote=INVALID_PARAMETER_QUOTE)
        with patch.object(core, 'request_json',
                side_effect=self.mock_requests([result([valid, invalid])])) as request:
            adopted_plan = core._quality_gate(manifest, self.root, self.inputs, None, Mock())
        self.assertEqual(request.call_count, 1)
        self.assertEqual(adopted_plan, before[0])
        self.assertEqual((manifest['plan'], self.inputs), before)
        self.assertEqual(manifest['plan_advisories'], [valid])
        self.assertNotIn('rejected_plan_versions', manifest)
        self.assertEqual(manifest['plan_review']['unverified_advisories'][0]['issue'], invalid)
        self.assert_raw_retained()
        captured = []

        class StopBeforeModel(Exception):
            pass

        def capture(request, *args, **kwargs):
            captured.append(copy.deepcopy(request[1]))
            raise StopBeforeModel()

        with patch.object(core, 'request_json', side_effect=capture):
            with self.assertRaises(StopBeforeModel):
                core._automatic_segment('zero-model-only', manifest, self.root,
                    self.inputs, manifest['plan'], manifest['plan']['beats'][0],
                    None, {}, Mock(), Mock())
        self.assertEqual(captured[0]['plan_advisories'], [valid])
        self.assertNotIn(invalid, captured[0]['plan_advisories'])
        self.assertNotIn('unverified_advisories', captured[0])
        self.assertNotIn('plan_review', captured[0])

    def test_new_policy_repairs_once_then_records_second_blocking_without_stopping(self):
        failed = result([issue('blocking')])
        revised = copy.deepcopy(self.plan)
        revised['title'] = 'An explicitly repaired spring'
        for second in (result([issue()]), failed):
            manifest = self.manifest()
            folder = self.root/('warnings' if not second['passed'] else 'passed')
            with patch.object(core, 'request_json',
                    side_effect=self.mock_requests([failed, revised, second])) as request:
                self.assertEqual(core._quality_gate(manifest, folder,
                    self.inputs, None, Mock()), revised)
            self.assertEqual(request.call_count, 3)
            feedback = request.call_args_list[1].args[0][1]['review_feedback']
            self.assertEqual(feedback['issues'], failed['issues'])
            self.assertFalse(feedback['passed'])
            self.assertEqual(len(manifest['rejected_plan_versions']), 1)
            self.assertEqual(manifest['plan_review']['round'], 2)
            self.assertEqual(manifest['plan_review']['passed'], second['passed'])
            self.assertFalse(manifest['plan_review']['quality_gate'])
            if not second['passed']:
                self.assertTrue(manifest['continuation_warnings'])
            self.assertEqual(manifest['segments'], [])
        self.assert_raw_retained()

    def test_invalid_blocking_and_inconsistent_passed_cannot_be_forged_as_success(self):
        payload = self.request()[1]
        invalid_blocking = result([issue('blocking', INVALID_PARAMETER_QUOTE)])
        with self.assertRaisesRegex(ValueError, 'source_quote'):
            core.validate_plan_review(invalid_blocking, payload)
        for bad in (dict(result([issue(quote=INVALID_PARAMETER_QUOTE)]), passed=False),
                    dict(result([issue('blocking')]), passed=True)):
            before = copy.deepcopy(bad)
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'blocking'):
                core.validate_plan_review(bad, payload)
            self.assertEqual(bad, before)

    def test_mixed_blocking_revision_receives_only_verified_feedback_and_retains_audit(self):
        blocking = issue('blocking')
        invalid = issue(quote=INVALID_PARAMETER_QUOTE)
        invalid['reason'] = '错误地建议全片15秒、每段缩到3秒。'
        failed = result([blocking, invalid])
        revised = copy.deepcopy(self.plan)
        revised['title'] = 'A repaired plan with verified feedback'
        manifest = self.manifest()
        with patch.object(core, 'request_json',
                side_effect=self.mock_requests([failed, revised, result()])) as request:
            self.assertEqual(core._quality_gate(manifest, self.root,
                self.inputs, None, Mock()), revised)
        self.assertEqual(request.call_count, 3)
        feedback = request.call_args_list[1].args[0][1]['review_feedback']
        self.assertEqual(set(feedback), {'passed', 'issues', 'strengths'})
        self.assertEqual(feedback['issues'], [blocking])
        self.assertFalse(feedback['passed'])
        self.assertNotIn('unverified_advisories', feedback)
        self.assertNotIn(INVALID_PARAMETER_QUOTE,
                         json.dumps(feedback, ensure_ascii=False))
        self.assertNotIn(invalid['reason'], json.dumps(feedback, ensure_ascii=False))
        old_review = manifest['rejected_plan_versions'][0]['review']
        self.assertEqual(old_review['unverified_advisories'][0]['issue'], invalid)
        audit = json.loads(Path(old_review['evidence_audit_path']).read_text(encoding='utf-8'))
        self.assertEqual(audit['unverified_advisories'][0]['issue'], invalid)
        self.assertFalse(audit['model_passed'])
        self.assertFalse(audit['adopted_passed'])
        self.assertTrue(manifest['plan_review']['passed'])
        self.assertEqual(manifest['plan_review']['round'], 2)
        self.assert_raw_retained()

    def test_old_policy_remains_strict_for_unverifiable_advisory(self):
        old = legacy.review_request(self.plan, BRIEF, '', 5, 15, blocking_only=True)
        with self.assertRaisesRegex(ValueError, 'source_quote'):
            core.validate_plan_review(result([issue(quote=INVALID_PARAMETER_QUOTE)]), old[1])
        with patch.object(core, 'request_json', side_effect=self.mock_requests([result([issue()])])):
            reply = core._request_plan_review(old, self.root/'old')
        self.assertNotIn('unverified_advisories', reply)
        self.assertFalse((self.root/'old/evidence_audit.json').exists())

    def test_guided_new_policy_keeps_old_schema_without_severity_or_rewrite(self):
        request = self.request(blocking_only=False)
        self.assertEqual(request[1]['review_evidence_policy'], 'story-review-evidence-v2')
        self.assertNotIn('review_policy', request[1])
        self.assertNotIn('severity', request[2]['properties']['issues']['items']['properties'])
        reply = review_fixture(False)
        manifest = self.manifest()
        inputs = dict(self.inputs, quality_mode='guided')
        with patch.object(core, 'request_json',
                side_effect=self.mock_requests([reply])) as request_json:
            self.assertEqual(core._quality_gate(manifest, self.root/'guided',
                inputs, None, Mock()), self.plan)
        self.assertEqual(request_json.call_count, 1)
        self.assertTrue(manifest['plan_review']['advisory'])
        self.assertNotIn('unverified_advisories', manifest['plan_review'])
        self.assertNotIn('rejected_plan_versions', manifest)
        self.assert_raw_retained()


if __name__ == '__main__':
    unittest.main()
