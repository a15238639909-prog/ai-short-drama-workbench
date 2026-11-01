"""Optional report outages must not poison required generation; zero inference."""
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from cores import story_video_continuation as continuation
from cores import story_video_reference as reference
from cores import runtime_reset
from models import qwen_client


class OptionalTransportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='optional_evidence_transport_'))
        self.patch = patch.object(runtime_reset, 'STATE', self.root / 'reset.json')
        self.patch.start()
        self.addCleanup(self.patch.stop)
        previous = getattr(runtime_reset._local, 'generation', None)
        runtime_reset.bind_thread(None)
        self.addCleanup(runtime_reset.bind_thread, previous)

    def session(self):
        manifest = dict(actual_model_calls=0, reference_policy=dict(max_text_vision_calls=4))
        session = reference._Session('zero_model_fixture', None, self.root,
                                     dict(segment_count=2), manifest)
        guard = patch.object(session, 'guard')
        guard.start()
        self.addCleanup(guard.stop)
        session.stage = lambda *args, **kwargs: None
        return session

    def test_real_wrapper_optional_timeout_is_recorded_then_required_cache_can_proceed(self):
        session = self.session()
        error = TimeoutError('mock review service timed out')
        request = ('可选观察', dict(stage='segment'), {})
        with patch.object(reference.qwen_client, 'chat', side_effect=error), session.models(), \
                patch.object(reference.core, 'request_json',
                             side_effect=lambda *a, **k: reference.qwen_client.chat('可选观察', '{}')):
            observed = session.cached('reference_observation', dict(request=request),
                lambda: session.review(request, self.root / 'review'))
        self.assertEqual(observed['status'], 'unavailable')
        self.assertNotIn('passed', observed)
        self.assertIsNone(session.failure)
        self.assertEqual(session.manifest['actual_model_calls'], 1)
        trace = reference._read(self.root / 'actual_calls/001/error.json')
        self.assertEqual(trace['type'], 'TimeoutError')
        following = session.cached('required_next_stage', {}, lambda: dict(story='原故事'))
        self.assertEqual(following['story'], '原故事')

    def test_unavailable_marker_covers_failed_call_without_reasking_on_resume(self):
        session = self.session()
        request = ('可选观察', dict(stage='segment'), {})
        work = self.root / 'report'
        def fail(*a, **k):
            trace = work / 'call_1_fixture/vision_fixture'
            reference._save(trace / 'request.json', dict(stage='segment'))
            reference._save(trace / 'error.json', dict(type='TimeoutError'))
            raise TimeoutError('optional model unavailable')
        with patch.object(session, 'ask', side_effect=fail) as ask:
            first = session.review(request, work)
            second = session.review(request, work)
        self.assertEqual(second, first)
        self.assertEqual(first['status'], 'unavailable')
        ask.assert_called_once()

    def test_policy_binding_and_filesystem_errors_are_not_optional(self):
        for error in (reference.core.ControlStop(), runtime_reset.Cancelled('任务取消'),
                RuntimeError('PUBG正在运行'), RuntimeError('V41_NO_MODEL=1：禁止真实调用'),
                RuntimeError('显卡正被另一个进程使用'), RuntimeError('缓存或输入发生变化'),
                RuntimeError('模型请求或证据变化，未复用旧回复'),
                PermissionError('sharing violation'), FileNotFoundError('reference missing')):
            with self.subTest(error=type(error).__name__, message=str(error)):
                self.assertFalse(continuation.is_optional_evidence_error(error))
                wrapped = RuntimeError('本地模型格式修复仍未通过：包装错误')
                wrapped.__cause__ = error
                self.assertFalse(continuation.is_optional_evidence_error(wrapped))

    def test_optional_network_context_and_shape_errors_are_unknown_not_validations_passed(self):
        for error in (TimeoutError('timeout'), ConnectionRefusedError('refused'),
                urllib.error.URLError('connection reset'), KeyError('choices'),
                TypeError('bad choice shape'), IndexError('empty choices'),
                qwen_client.ModelHTTPError(503, 'model busy', '{}'),
                qwen_client.ModelContextError('上下文超限')):
            with self.subTest(error=type(error).__name__):
                self.assertTrue(continuation.is_optional_evidence_error(error))
                self.assertFalse(continuation.is_optional_error(error))
        self.assertFalse(continuation.is_optional_evidence_error(RuntimeError('unknown required failure')))

    def test_prior_required_failure_remains_fatal(self):
        session = self.session()
        error = TimeoutError('required story writer failed')
        session.failure = error
        with self.assertRaises(TimeoutError) as caught, patch.object(session, 'ask') as ask:
            session.review(('可选观察', {}, {}), self.root / 'report')
        self.assertIs(caught.exception, error)
        ask.assert_not_called()

    def test_exhausted_optional_budget_after_an_interrupted_review_stays_unknown(self):
        session = self.session()
        session.manifest['actual_model_calls'] = 4
        request = ('可选观察', dict(stage='segment'), {})
        work = self.root / 'interrupted_review'
        reference._save(work / 'binding.json', dict(system=request[0], payload=request[1],
                                                   schema=request[2], images=[]))
        trace = work / 'call_1_fixture/vision_fixture'
        reference._save(trace / 'request.json', {})
        reference._save(trace / 'error.json', dict(type='TimeoutError'))
        with patch.object(session, 'ask') as ask:
            result = session.review(request, work)
        ask.assert_not_called()
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn('预算', result['reason'])
        self.assertNotIn('passed', result)


if __name__ == '__main__':
    unittest.main()
