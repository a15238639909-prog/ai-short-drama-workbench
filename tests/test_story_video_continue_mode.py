"""Bounded repair then continue, with all inference/media calls mocked."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from cores import story_video_core as core
from cores import story_video_continuation as continuation
from cores import story_video_media as media
from tests import test_story_video_mode_runtime as mode_fixtures
from tests.test_story_video_core import plan_fixture, review_fixture


FORMAT_ERROR = '本地模型格式修复仍未通过：专项测试模拟报告格式错误'


class ContinueModeTests(unittest.TestCase):
    def fixture(self, **kwargs):
        previous = getattr(self, '_active_support', None)
        if previous is not None:
            previous.doCleanups()
        support = mode_fixtures.ModeRuntimeTests(methodName='runTest')
        case, requests = support.fixture(automatic=True, **kwargs)
        self._active_support = support
        self.addCleanup(support.doCleanups)
        return case, requests

    def fail_requests(self, predicate, error=None):
        original = core.request_json

        def ask(request, directory, images=None, validator=None):
            if predicate(request[1], Path(directory)):
                raise error or RuntimeError(FORMAT_ERROR)
            return original(request, directory, images=images, validator=validator)

        return patch.object(core, 'request_json', side_effect=ask)

    def assert_completed(self, case, output, *, warnings=True):
        manifest = case.manifest()
        self.assertEqual(output['segments'], 2)
        self.assertEqual(len(case.video_calls), 2)
        self.assertEqual([row['take'] for row in manifest['segments']], [1, 1])
        self.assertEqual(manifest['continuation_policy'], continuation.POLICY)
        self.assertNotIn('continuation_policy', case.task['inputs'])
        self.assertNotIn('continuation_policy', manifest['inputs'])
        self.assertTrue(output['generation_completed'])
        self.assertEqual(manifest['quality_summary']['generation_completed'], True)
        self.assertEqual(manifest['quality_summary']['status'],
                         'completed_with_warnings' if warnings else 'passed')
        self.assertEqual(output['quality_passed'], not warnings)
        self.assertEqual(case.video_calls[1]['first_frame'], manifest['segments'][0]['anchor_path'])
        self.assertTrue(all(row['retention_policy'] == 'full_duration' for row in manifest['segments']))
        self.assertTrue(all(row['quality']['adopted'] for row in manifest['segments']))
        self.assertTrue(all(row['quality']['adoption_policy'] == continuation.POLICY
                            for row in manifest['segments']))
        case.join.assert_called_once()
        return manifest

    def test_failed_plan_preflight_visual_audio_finish_without_retakes_or_false_pass(self):
        case, _ = self.fixture()
        case.review_replies = [review_fixture(False), review_fixture(False)]
        case.storyboard_outcomes = [False, False, False, False]
        case.visual_outcomes = [False, False]
        case.audio_outcomes = ['unavailable', 'failed']
        output = core.run_job(case.task['id'])
        manifest = self.assert_completed(case, output)
        self.assertEqual(len(case.revision_payloads), 1)
        self.assertEqual(len(case.writer_states), 4)
        self.assertEqual(len(case.storyboard_calls), 4)
        self.assertFalse(manifest['plan_review']['passed'])
        self.assertFalse(manifest['plan_review']['quality_gate'])
        self.assertTrue(manifest['continuation_warnings'])
        for row in manifest['segments']:
            self.assertFalse(row['quality']['passed'])
            self.assertEqual(row['quality']['quality_status'], 'warning')
            self.assertTrue(row['quality']['warnings'])
            self.assertFalse(row['quality']['storyboard']['passed'])
            self.assertFalse(row['quality']['visual']['passed'])
            self.assertNotEqual(row['observation']['beat_status'], 'achieved')

    def test_image_failed_reviews_finish_from_opening_and_exact_tail_then_resume_without_retakes(self):
        case, _ = self.fixture(image=True)
        source_opening = Path(case.task['inputs']['opening_image']).read_bytes()
        case.review_replies = [review_fixture(False), review_fixture(False)]
        case.storyboard_outcomes = [False, False, False, False]
        case.visual_outcomes = [False, False]
        case.audio_outcomes = ['unavailable', 'failed']
        output = core.run_job(case.task['id'])
        manifest = self.assert_completed(case, output)
        self.assertEqual(output['duration'], 16)
        self.assertEqual([row['duration'] for row in manifest['segments']], [8, 8])
        self.assertEqual(len(case.revision_payloads), 1)
        self.assertEqual(len(case.writer_states), 4)
        self.assertEqual(len(case.storyboard_calls), 4)
        self.assertEqual([row['mode'] for row in case.writer_states], ['i2va'] * 4)
        self.assertEqual(case.video_calls[0]['first_frame'], str(case.folder/'opening.png'))
        self.assertEqual(Path(case.video_calls[0]['first_frame']).read_bytes(), source_opening)
        self.assertEqual(case.video_calls[1]['first_frame'], manifest['segments'][0]['anchor_path'])
        self.assertFalse(manifest['plan_review']['passed'])
        self.assertFalse(json.loads((case.folder/'planning/review_2/accepted.json').read_text(
            encoding='utf-8'))['passed'])
        for row in manifest['segments']:
            self.assertFalse(row['quality']['passed'])
            self.assertFalse(row['quality']['storyboard']['passed'])
            self.assertFalse(row['quality']['visual']['passed'])
            self.assertTrue(row['quality']['adopted'])
        self.assertEqual([row['quality']['audio']['status'] for row in manifest['segments']],
                         ['unavailable', 'failed'])
        before = (len(case.video_calls), len(case.writer_states), len(case.review_payloads),
                  len(case.storyboard_calls), len(case.acceptance_calls), case.audio.call_count)
        resumed = core.run_job(case.task['id'])
        self.assertEqual((len(case.video_calls), len(case.writer_states), len(case.review_payloads),
            len(case.storyboard_calls), len(case.acceptance_calls), case.audio.call_count), before)
        self.assertEqual(resumed['quality_status'], 'completed_with_warnings')
        self.assertFalse(resumed['quality_passed'])

    def test_image_rejected_ledger_reuses_retained_raw_without_another_submission(self):
        case, _ = self.fixture(image=True)
        case.visual_outcomes = [False, False]
        first = core.run_job(case.task['id'])
        manifest = self.assert_completed(case, first)
        retained = {row['raw_path']: Path(row['raw_path']).read_bytes()
                    for row in manifest['segments']}
        first_frame = case.video_calls[0]['first_frame']
        first_tail = manifest['segments'][0]['anchor_path']
        # Simulate a retained rejected take whose adoption row was not yet
        # committed. No model or production artifact participates in this case.
        manifest['segments'] = []
        manifest.pop('output', None)
        for batch in manifest['quality_attempts'].values():
            for entry in batch['attempts'].values():
                entry.update(status='rejected', decision=dict(passed=False,
                    reasons=['Retained synthetic take lacks conclusive visual evidence.']))
        case.save_json(case.folder/'manifest.json', manifest)
        case.visual_outcomes = [False, False]
        case.video.side_effect = AssertionError('retained image takes must not be submitted again')
        resumed = core.run_job(case.task['id'])
        self.assertEqual(resumed['segments'], 2)
        self.assertEqual(len(case.video_calls), 2)
        self.assertFalse(resumed['quality_passed'])
        self.assertEqual(resumed['quality_status'], 'completed_with_warnings')
        adopted = case.manifest()
        self.assertEqual([row['take'] for row in adopted['segments']], [1, 1])
        self.assertEqual(case.video_calls[0]['first_frame'], first_frame)
        self.assertEqual(case.video_calls[1]['first_frame'], first_tail)
        self.assertTrue(all(Path(path).read_bytes() == raw for path, raw in retained.items()))
        self.assertTrue(all(row['quality']['adopted'] for row in adopted['segments']))
        self.assertTrue(all(not row['quality']['passed'] for row in adopted['segments']))

    def test_format_failed_plan_review_is_recorded_without_rewriting(self):
        case, _ = self.fixture()
        with self.fail_requests(lambda payload, path: payload.get('stage') == 'review'):
            output = core.run_job(case.task['id'])
        manifest = self.assert_completed(case, output)
        self.assertEqual(case.revision_payloads, [])
        self.assertEqual(manifest['plan'], plan_fixture())
        self.assertEqual(manifest['plan_review']['status'], 'unavailable')
        self.assertNotIn('passed', manifest['plan_review'])
        self.assertTrue(list((case.folder/'planning/review_1').glob('unavailable_*.json')))
        self.assertNotIn('plan_advisories', case.writer_states[0])

    def test_unusable_plan_revision_preserves_existing_valid_plan(self):
        case, _ = self.fixture()
        case.review_replies = [review_fixture(False)]
        with self.fail_requests(lambda payload, path: 'previous_draft' in payload):
            output = core.run_job(case.task['id'])
        manifest = self.assert_completed(case, output)
        self.assertEqual(manifest['plan'], plan_fixture())
        self.assertEqual(manifest['plan_revision_warning']['status'], 'unavailable')
        self.assertFalse(manifest['plan_review']['passed'])
        self.assertEqual(len(case.review_payloads), 1)

    def test_unavailable_preflight_has_no_invented_contract_or_visual_review(self):
        case, _ = self.fixture()
        with self.fail_requests(lambda payload, path: payload.get('stage') == 'storyboard_review'):
            output = core.run_job(case.task['id'])
        manifest = self.assert_completed(case, output)
        self.assertEqual(case.acceptance_calls, [])
        self.assertEqual(len(case.writer_states), 2)
        for row in manifest['segments']:
            report = row['quality']['storyboard']
            self.assertEqual(report['status'], 'unavailable')
            self.assertNotIn('event_contract', report)
            self.assertEqual(row['quality']['visual']['status'], 'unavailable')
            self.assertNotIn('passed', row['quality']['visual'])
            self.assertEqual(row['observation']['beat_status'], 'unknown')

    def test_unusable_writer_revision_keeps_first_valid_prompt(self):
        case, _ = self.fixture()
        case.storyboard_outcomes = [False, False]
        with self.fail_requests(lambda payload, path: path.name == 'writing_revision_1'):
            output = core.run_job(case.task['id'])
        manifest = self.assert_completed(case, output)
        self.assertEqual(len(case.writer_states), 2)
        for row in manifest['segments']:
            self.assertTrue(Path(row['prompt_path']).is_file())
            self.assertEqual(row['quality']['quality_status'], 'warning')
            self.assertFalse(row['quality']['passed'])
            self.assertTrue(any('原提示词' in reason for reason in row['quality']['reasons']))

    def test_unavailable_observation_is_unknown_and_real_tail_still_drives_next_clip(self):
        case, _ = self.fixture(image=True)
        with self.fail_requests(lambda payload, path: payload.get('stage') in ('opening', 'segment')):
            output = core.run_job(case.task['id'])
        manifest = self.assert_completed(case, output)
        self.assertEqual(manifest['opening_observation']['status'], 'unavailable')
        self.assertEqual(manifest['opening_observation']['characters'], [])
        for row in manifest['segments']:
            self.assertEqual(row['observation']['status'], 'unavailable')
            self.assertEqual(row['observation']['beat_status'], 'unknown')
            self.assertEqual(row['observation']['actions'], [])
            self.assertEqual(row['observation']['completed'], [])
            self.assertEqual(row['observation']['characters'], [])
        self.assertEqual(case.writer_states[1]['actual_state']['beat_status'], 'unknown')

    def test_visual_format_failure_is_warning_not_extra_generation(self):
        case, _ = self.fixture()
        with self.fail_requests(lambda payload, path: payload.get('stage') == 'segment_acceptance'):
            output = core.run_job(case.task['id'])
        manifest = self.assert_completed(case, output)
        self.assertTrue(all(row['quality']['visual']['status'] == 'unavailable'
                            for row in manifest['segments']))
        self.assertEqual(case.extra.call_count, 0)

    def test_evidence_overflow_retains_all_files_without_sending_ten_images_or_retaking(self):
        case, _ = self.fixture()
        case.visual_outcomes = [dict(status='insufficient_evidence',
            follow_up_times=[.5, 2.5, 4.5]), True]

        def seven_samples(video, folder, **kwargs):
            folder.mkdir(parents=True, exist_ok=True)
            rows = []
            for index, seconds in enumerate((0, 1, 2, 3, 4, 6, 7.75)):
                image = folder/('source_%d.png' % index)
                image.write_bytes(b'retained real-sample fixture')
                rows.append(dict(path=str(image), time_seconds=seconds))
            return rows

        with patch.object(media, 'sample_frames', side_effect=seven_samples):
            output = core.run_job(case.task['id'])
        manifest = self.assert_completed(case, output)
        report = manifest['segments'][0]['quality']['visual']
        self.assertEqual(report['status'], 'unavailable')
        self.assertIn('补证超出本轮检查上限', report['reason'])
        self.assertEqual(len(report['sample_records']), 10)
        self.assertTrue(all(Path(row['path']).is_file() for row in report['sample_records']))
        self.assertEqual(case.extra.call_count, 1)
        self.assertEqual(len(case.acceptance_calls), 2)
        self.assertTrue(all(len(call[2]) <= 9 for call in case.acceptance_calls))

    def test_warning_segments_resume_without_retake_but_hash_changes_still_fail(self):
        case, _ = self.fixture()
        case.visual_outcomes = [False, False]
        core.run_job(case.task['id'])
        manifest = case.manifest()
        before = (len(case.video_calls), len(case.acceptance_calls), case.audio.call_count)
        core.run_job(case.task['id'])
        self.assertEqual((len(case.video_calls), len(case.acceptance_calls), case.audio.call_count), before)
        previous = None
        inputs = dict(case.task['inputs'], director_profile=mode_fixtures.modes.PROFILE)
        for row in manifest['segments']:
            core._automatic_resume_check(row, manifest['plan'], previous, inputs)
            for field in ('clip_path', 'anchor_path'):
                target = Path(row[field])
                retained = target.read_bytes()
                target.write_bytes(retained + b'changed')
                try:
                    with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, '输入已变化'):
                        core._automatic_resume_check(row, manifest['plan'], previous, inputs)
                finally:
                    target.write_bytes(retained)
            with self.assertRaises(RuntimeError):
                core._automatic_resume_check(row, manifest['plan'], previous, {})
            wrong = copy.deepcopy(row)
            wrong['quality']['adoption_policy'] = 'untrusted'
            with self.assertRaises(RuntimeError):
                core._automatic_resume_check(wrong, manifest['plan'], previous, inputs)
            previous = row['anchor_path']
        changed = copy.deepcopy(manifest['plan'])
        changed['title'] += ' changed'
        with self.assertRaises(RuntimeError):
            core._automatic_resume_check(manifest['segments'][0], changed, None, inputs)

    def test_initial_unusable_story_or_prompt_remains_a_real_failure(self):
        for which in ('plan', 'writing'):
            case, _ = self.fixture()
            predicate = (lambda payload, path: path.name == 'planning') if which == 'plan' else (
                lambda payload, path: path.name == 'writing')
            with self.fail_requests(predicate), self.assertRaisesRegex(RuntimeError, '格式修复'):
                core.run_job(case.task['id'])
            self.assertEqual(case.video_calls, [])
            case.join.assert_not_called()

    def test_service_pubg_cancel_and_bad_media_errors_are_never_optional(self):
        for message in ('本地模型服务不可用', 'PUBG_RUNNING：显卡正被游戏占用', '任务取消'):
            case, _ = self.fixture()
            with self.fail_requests(lambda payload, path: payload.get('stage') == 'review',
                    RuntimeError(message)), self.assertRaisesRegex(RuntimeError, message.split('：')[0]):
                core.run_job(case.task['id'])
            self.assertEqual(case.video_calls, [])
            case.join.assert_not_called()
        case, _ = self.fixture()
        with patch.object(media, 'adopt', side_effect=RuntimeError('视频完整解码失败')):
            with self.assertRaisesRegex(RuntimeError, '解码失败'):
                core.run_job(case.task['id'])
        self.assertEqual(len(case.video_calls), 1)
        case.join.assert_not_called()

    def test_unusable_audio_source_and_join_failure_remain_real_failures(self):
        case, _ = self.fixture()
        case.audio.side_effect = lambda *args, **kwargs: dict(status='unavailable',
            issues=[dict(code='asr_worker_error', reason='音轨提取失败：损坏视频')])
        with self.assertRaisesRegex(RuntimeError, '音轨文件处理失败'):
            core.run_job(case.task['id'])
        self.assertEqual(len(case.video_calls), 1)
        case.join.assert_not_called()
        case, _ = self.fixture()
        case.join.side_effect = RuntimeError('最终完整音画解码失败')
        with self.assertRaisesRegex(RuntimeError, '完整音画解码失败'):
            core.run_job(case.task['id'])
        self.assertFalse(case.manifest()['quality_summary']['passed'])
        self.assertNotIn('output', case.manifest())

    def test_legacy_manifest_and_reference_keep_existing_dispatch_rules(self):
        case, _ = self.fixture()
        case.save_json(case.folder/'manifest.json', dict(version=1,
            task_id=case.task['id'], inputs=copy.deepcopy(case.task['inputs']),
            segments=[], logs=[], created=0))
        case.review_replies = [review_fixture(False), review_fixture(False)]
        with self.assertRaisesRegex(RuntimeError, '复审仍未通过'):
            core.run_job(case.task['id'])
        self.assertEqual(case.video_calls, [])
        self.assertNotIn('continuation_policy', case.manifest())
        case, _ = self.fixture()
        case.task['inputs']['input_mode'] = 'reference'
        from cores import story_video_reference as reference
        with patch.object(reference, 'run_job', return_value=dict(reference='unchanged')) as run:
            self.assertEqual(core.run_job(case.task['id']), dict(reference='unchanged'))
        run.assert_called_once_with(case.task['id'])
        self.assertFalse((case.folder/'manifest.json').exists())


if __name__ == '__main__':
    unittest.main()
