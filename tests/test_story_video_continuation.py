"""No-model optional-review boundaries; fixtures are kept for recycling."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from urllib.error import URLError

from cores import story_video_continuation as continuation


class ContinuationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='story_continue_evidence_'))
        self.request = ('报告系统', {'stage': 'review'}, {'type': 'object'})

    @staticmethod
    def save(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def ask(self, mock, **options):
        return continuation.optional_request(self.request, self.folder, mock, self.save, **options)

    def test_only_profile_automatic_image_and_text_enable_policy(self):
        inputs = dict(director_profile='story-mode-director-zh-v1', quality_mode='automatic', input_mode='text')
        self.assertTrue(continuation.enabled(inputs))
        self.assertTrue(continuation.enabled(dict(inputs, input_mode='image')))
        for changed in (dict(inputs, input_mode='reference'), dict(inputs, quality_mode='guided'),
                        dict(inputs, director_profile='old'), {}):
            self.assertFalse(continuation.enabled(changed))

    def test_real_quality_failure_is_returned_unchanged_not_forged_passed(self):
        reply = dict(passed=False, issues=[{'severity': 'blocking', 'reason': '需改进'}])
        original = copy.deepcopy(reply)
        result = self.ask(Mock(return_value=reply))
        self.assertIs(result, reply)
        self.assertEqual(reply, original)
        self.assertFalse(result['passed'])

    def test_exhausted_format_repair_is_cached_as_unavailable_without_passed(self):
        ask = Mock(side_effect=RuntimeError('本地模型格式修复仍未通过：无效建议引文'))
        first = self.ask(ask)
        second = self.ask(ask)
        self.assertEqual(first, second)
        self.assertEqual(ask.call_count, 1)
        self.assertEqual(first['status'], 'unavailable')
        self.assertFalse(first['quality_gate'])
        self.assertNotIn('passed', first)
        self.assertEqual(len(list(self.folder.glob('unavailable_*.json'))), 1)

    def test_empty_report_can_be_unavailable_but_changed_request_is_not_reused(self):
        ask = Mock(side_effect=RuntimeError('本地故事模型回复为空或被截断，已保留原始回复'))
        self.ask(ask)
        self.request[1]['stage'] = 'new-review'
        self.ask(ask)
        self.assertEqual(ask.call_count, 2)
        self.assertEqual(len(list(self.folder.glob('unavailable_*.json'))), 2)

    def test_evidence_image_content_is_bound_not_only_its_filename(self):
        image = self.folder/'sample.png'
        image.write_bytes(b'first retained sample')
        ask = Mock(side_effect=RuntimeError('本地模型格式修复仍未通过：报告字段'))
        self.ask(ask, images=[str(image)])
        image.write_bytes(b'different sample, same path')
        self.ask(ask, images=[str(image)])
        self.assertEqual(ask.call_count, 2)

    def test_missing_image_remains_real_failure_without_asking_model(self):
        ask = Mock()
        with self.assertRaises(FileNotFoundError):
            self.ask(ask, images=[str(self.folder/'absent.png')])
        ask.assert_not_called()

    def test_gpu_cancel_and_signature_errors_are_never_swallowed(self):
        class LocalASRBlocked(RuntimeError):
            pass
        class ControlStop(Exception):
            pass
        errors = [LocalASRBlocked('PUBG正在运行'), ControlStop('cancelled'),
            RuntimeError('PUBG正在运行'),
            RuntimeError('本地模型格式修复仍未通过：质量回复与当前请求签名不一致，不能复用旧缓存')]
        for error in errors:
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                self.ask(Mock(side_effect=error))
        self.assertFalse(list(self.folder.glob('unavailable_*.json')))

    def test_optional_report_service_outage_is_unknown_and_does_not_claim_pass(self):
        for number, error in enumerate((URLError('service offline'), TimeoutError('service timeout'),
                                       RuntimeError('模型文件缺失'))):
            with self.subTest(error=type(error).__name__):
                self.request[1]['stage'] = 'optional_%d' % number
                report = self.ask(Mock(side_effect=error))
                self.assertEqual(report['status'], 'unavailable')
                self.assertEqual(report['reason'], str(error))
                self.assertNotIn('passed', report)

    def test_wrapped_real_fault_is_not_mistaken_for_report_formatting(self):
        try:
            raise OSError('video corrupted')
        except OSError as cause:
            error = RuntimeError('本地模型格式修复仍未通过：其他错误')
            error.__cause__ = cause
        self.assertFalse(continuation.is_optional_error(error))

    def test_unknown_observation_does_not_assert_old_visible_facts_or_success(self):
        old = dict(characters=[dict(id='c1', appearance='黑发', pose='抬手')], completed=['打开盒子'])
        snapshot = copy.deepcopy(old)
        report = continuation.unknown_observation(dict(reason='观察报告格式不可用'), old)
        self.assertEqual(report['characters'], [])
        self.assertEqual(report['completed'], [])
        self.assertEqual(report['actions'], [])
        self.assertEqual(report['beat_status'], 'unknown')
        report['last_known_state']['characters'][0]['pose'] = '不得污染输入'
        self.assertEqual(old, snapshot)

    def test_audio_quality_or_unavailable_is_a_warning_but_missing_source_is_not(self):
        for status in ('failed', 'unavailable'):
            decision = continuation.adoption_decision(dict(passed=False, events=[], critical_issues=[]),
                dict(status=status, issues=[dict(code='asr_unmatched', reason='原话识别不确定')]))
            self.assertFalse(decision['passed'])
            self.assertTrue(decision['adopted'])
            self.assertTrue(decision['warnings'])
        for issue in (dict(code='missing_video', reason='视频不存在'),
                      dict(code='asr_worker_error', reason='音轨提取失败：解码错误')):
            with self.subTest(issue=issue), self.assertRaisesRegex(RuntimeError, '文件处理失败'):
                continuation.assert_audio_source_usable(dict(status='unavailable', issues=[issue]))

    def test_completion_keeps_quality_failure_separate_from_generation_completion(self):
        rows = [dict(index=1, quality=dict(passed=False, adopted=True, warnings=['镜头建议']))]
        summary = continuation.completion_summary(rows, ['预审记录不可用', '镜头建议'])
        self.assertFalse(summary['passed'])
        self.assertTrue(summary['generation_completed'])
        self.assertEqual(summary['status'], 'completed_with_warnings')
        self.assertEqual(summary['warnings'], ['预审记录不可用', '镜头建议'])
        clean = continuation.completion_summary([dict(quality=dict(passed=True))])
        self.assertTrue(clean['passed'])
        self.assertEqual(clean['status'], 'passed')


if __name__ == '__main__':
    unittest.main()
