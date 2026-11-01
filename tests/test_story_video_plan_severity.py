"""Targeted severity policy and plan-gate checks; no inference or media IO."""
import copy
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

os.environ['V41_NO_MODEL'] = '1'

from cores import story_video_core as core, story_video_prompts as prompts
from tests.test_story_video_core import plan_fixture, review_fixture


BRIEF = '疏通山泉并完成结局，保持原有角色与因果动作。'


def issue(severity='advisory', beat=1):
    return dict(beat=beat, category='capacity', severity=severity,
                source_quote='turn the ring', reason='可以让转环后的回应更清楚',
                repair_direction='在本段写词时明确动作结果，不添加新任务')


def review(issues=None):
    issues = [] if issues is None else copy.deepcopy(issues)
    return dict(passed=not any(item['severity'] == 'blocking' for item in issues),
                issues=issues, strengths=[])


class PlanSeverityTests(unittest.TestCase):
    def inputs(self, automatic=True, version=2):
        return dict(story_direction=BRIEF, style='自然电影质感', segment_count=2,
                    segment_seconds=8, flow_version=version,
                    quality_mode='automatic' if automatic else 'guided')

    def request(self, blocking_only=True):
        return prompts.review_request(plan_fixture(), BRIEF, '', 2, 8,
                                      blocking_only=blocking_only)

    def manifest(self):
        return dict(plan=plan_fixture(), segments=[])

    def mock_requests(self, replies):
        replies = iter(copy.deepcopy(replies))

        def respond(request, directory, images=None, validator=None):
            reply = next(replies)
            core.validate_schema(reply, request[2])
            if validator:
                validator(reply, request[1])
            return reply

        return respond

    def test_new_policy_passed_depends_on_blocking_not_advisory_count(self):
        _, payload, schema = self.request()
        self.assertEqual(payload['review_policy'], 'core_errors_only_v1')
        for severity in ('advisory', 'blocking'):
            with self.subTest(severity=severity):
                reply = review([issue(severity)])
                core.validate_schema(reply, schema)
                core.validate_plan_review(reply, payload)
                reply['passed'] = not reply['passed']
                with self.assertRaisesRegex(ValueError, 'blocking'):
                    core.validate_plan_review(reply, payload)

    def test_new_policy_checks_severity_and_real_brief_or_plan_provenance(self):
        _, payload, _ = self.request()
        reply = review([issue()])
        # The source is nested in a beat, outside the brief.
        core.validate_plan_review(reply, payload)
        reply['issues'][0]['source_quote'] = BRIEF
        core.validate_plan_review(reply, payload)
        for defect in ('missing_severity', 'invented_quote', 'empty_quote'):
            changed = review([issue()])
            if defect == 'missing_severity':
                changed['issues'][0].pop('severity')
            else:
                changed['issues'][0]['source_quote'] = '原文与计划均不存在的事件' if defect == 'invented_quote' else ''
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                core.validate_plan_review(changed, payload)

    def test_advisory_only_gate_skips_revision_and_restores_advisories_on_resume(self):
        manifest = self.manifest()
        original = copy.deepcopy(manifest['plan'])
        notes = [issue(beat=0), issue(beat=1), issue(beat=2)]
        stage = Mock()
        with patch.object(core, '_save'), patch.object(core, 'request_json',
                side_effect=self.mock_requests([review(notes)])) as request:
            result = core._quality_gate(manifest, Path('D:/unit-quality-only'), self.inputs(), None, stage)
            self.assertEqual(result, original)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(manifest['plan_advisories'], notes)
            self.assertNotIn('rejected_plan_versions', manifest)
            # Reopening a passed plan must restore its notes without new calls.
            manifest.pop('plan_advisories')
            core._quality_gate(manifest, Path('D:/unit-quality-only'), self.inputs(), None, stage)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(manifest['plan_advisories'], notes)
            self.assertEqual(manifest['plan_review']['round'], 1)

    def test_blocking_review_revises_once_then_allows_advisory_only_revision(self):
        manifest = self.manifest()
        revised = plan_fixture()
        revised['title'] = 'A repaired spring'
        failed, passed = review([issue('blocking')]), review([issue()])
        with patch.object(core, '_save'), patch.object(core, 'request_json',
                side_effect=self.mock_requests([failed, revised, passed])) as request:
            result = core._quality_gate(manifest, Path('D:/unit-quality-only'), self.inputs(), None, Mock())
        self.assertEqual(result, revised)
        self.assertEqual(request.call_count, 3)
        revision_payload = request.call_args_list[1].args[0][1]
        self.assertEqual(revision_payload['previous_draft'], plan_fixture())
        self.assertEqual(revision_payload['review_feedback'], failed)
        self.assertEqual(len(manifest['rejected_plan_versions']), 1)
        self.assertEqual(manifest['plan_review']['round'], 2)
        self.assertEqual(manifest['plan_advisories'], passed['issues'])

    def test_second_blocking_review_continues_with_versions_and_warning_preserved(self):
        manifest = self.manifest()
        revised = plan_fixture()
        revised['title'] = 'A still blocked draft'
        failed = review([issue('blocking')])
        with patch.object(core, '_save'), patch.object(core, 'request_json',
                side_effect=self.mock_requests([failed, revised, failed])) as request:
            result = core._quality_gate(manifest, Path('D:/unit-quality-only'), self.inputs(), None, Mock())
        self.assertEqual(request.call_count, 3)
        self.assertEqual(manifest['segments'], [])
        self.assertFalse(manifest['plan_review']['passed'])
        self.assertEqual(manifest['plan_review']['round'], 2)
        self.assertEqual(len(manifest['rejected_plan_versions']), 1)
        self.assertEqual(result, revised)
        self.assertTrue(manifest['continuation_warnings'])
        with patch.object(core, 'request_json') as request:
            self.assertEqual(core._quality_gate(manifest, Path('D:/unit-quality-only'),
                self.inputs(), None, Mock()), revised)
            request.assert_not_called()

    def test_legacy_review_timeout_keeps_valid_plan_and_unknown_warning(self):
        manifest = self.manifest()
        with patch.object(core, '_save'), patch.object(core, 'request_json',
                side_effect=TimeoutError('review service unavailable')) as request:
            result = core._quality_gate(manifest, Path('D:/unit-quality-only'),
                self.inputs(version=1), None, Mock())
        self.assertEqual(result, plan_fixture())
        self.assertEqual(request.call_count, 1)
        self.assertEqual(manifest['plan_review']['status'], 'unavailable')
        self.assertNotIn('passed', manifest['plan_review'])
        self.assertTrue(manifest['continuation_warnings'])

    def test_legacy_revision_timeout_keeps_valid_source_and_does_not_retry_review(self):
        manifest = self.manifest()
        failed = review([issue('blocking')])
        with patch.object(core, '_save'), patch.object(core, 'request_json',
                side_effect=[failed, TimeoutError('revision service unavailable')]) as request:
            result = core._quality_gate(manifest, Path('D:/unit-quality-only'),
                self.inputs(version=1), None, Mock())
        self.assertEqual(result, plan_fixture())
        self.assertEqual(request.call_count, 2)
        self.assertFalse(manifest['plan_review']['passed'])
        self.assertEqual(manifest['plan_revision_warning']['status'], 'unavailable')
        self.assertNotIn('rejected_plan_versions', manifest)
        with patch.object(core, 'request_json') as request:
            self.assertEqual(core._quality_gate(manifest, Path('D:/unit-quality-only'),
                self.inputs(version=1), None, Mock()), result)
            request.assert_not_called()

    def test_guided_and_legacy_keep_the_original_schema_and_gate_behavior(self):
        _, old_payload, old_schema = self.request(blocking_only=False)
        self.assertNotIn('review_policy', old_payload)
        self.assertNotIn('severity', old_schema['properties']['issues']['items']['properties'])
        for old_review in (review_fixture(True), review_fixture(False)):
            core.validate_schema(old_review, old_schema)
            core.validate_plan_review(old_review, old_payload)
        invalid = dict(review_fixture(False), passed=True)
        with self.assertRaises(ValueError):
            core.validate_plan_review(invalid, old_payload)
        # Guided flow still displays old-style concerns without rewriting.
        guided = self.manifest()
        with patch.object(core, '_save'), patch.object(core, 'request_json',
                side_effect=self.mock_requests([review_fixture(False)])) as request:
            self.assertEqual(core._quality_gate(guided, Path('D:/unit-quality-only'),
                self.inputs(False), None, Mock()), plan_fixture())
            self.assertEqual(request.call_count, 1)
        self.assertTrue(guided['plan_review']['advisory'])
        # Legacy strict flow retains its bounded revision using the old schema.
        legacy = self.manifest()
        with patch.object(core, '_save'), patch.object(core, 'request_json',
                side_effect=self.mock_requests([review_fixture(False), plan_fixture(), review_fixture(True)])) as request:
            core._quality_gate(legacy, Path('D:/unit-quality-only'), self.inputs(False, 1), None, Mock())
            self.assertEqual(request.call_count, 3)
        self.assertTrue(legacy['plan_review']['passed'])

    def test_only_global_and_current_beat_advisories_reach_current_writer(self):
        manifest = self.manifest()
        manifest['plan_advisories'] = [issue(beat=0), issue(beat=1), issue(beat=2)]
        original = copy.deepcopy(manifest['plan_advisories'])
        captured = []

        class StopAfterWriter(Exception):
            pass

        def capture(request, *args, **kwargs):
            captured.append(copy.deepcopy(request[1]))
            raise StopAfterWriter()

        with patch.object(core, '_save'), patch.object(Path, 'mkdir'), \
             patch.object(core, 'request_json', side_effect=capture):
            with self.assertRaises(StopAfterWriter):
                core._automatic_segment('unit', manifest, Path('D:/unit-quality-only'),
                    self.inputs(), manifest['plan'], manifest['plan']['beats'][0],
                    None, {}, Mock(), Mock())
        self.assertEqual([item['beat'] for item in captured[0]['plan_advisories']], [0, 1])
        self.assertTrue(captured[0]['advisory_scope'])
        self.assertEqual(manifest['plan_advisories'], original)


if __name__ == '__main__':
    unittest.main()
