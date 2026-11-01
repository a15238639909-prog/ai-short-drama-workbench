"""Reference recovery boundaries; no models/services, scratch evidence retained."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from cores import story_video_reference as reference
from cores import story_video_characters as characters


class ReferenceReviewStabilityTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='reference_stability_zero_model_'))
        self.request = ('只记录证据', dict(stage='review'), dict(type='object'))
        self.manifest = dict(actual_model_calls=0,
            reference_policy=dict(max_text_vision_calls=2))
        self.session = reference._Session('fixture', None, self.folder,
            dict(segment_count=1), self.manifest)
        self.work = self.folder / 'review'
        self.binding = dict(system=self.request[0], payload=self.request[1],
                            schema=self.request[2], images=[])

    def _trace(self, label, *, text='{}', error=False, order=1):
        trace = self.work / label / 'text_fixture'
        reference._save(trace / 'request.json', dict(fixture=True))
        os.utime(trace / 'request.json', ns=(order * 1_000_000_000,) * 2)
        if text is not None:
            reference._save(trace / 'response.json',
                dict(text=text, meta=dict(finish_reason='stop')))
        if error:
            reference._save(trace / 'error.json', dict(error='本地文字模型服务不可用'))
        return trace

    def _exhausted(self):
        reference._save(self.work / 'binding.json', self.binding)
        self._trace('call_2_old', order=1)
        reference._save(self.work / 'call_2_old' / 'contract_error.json',
                        dict(error='旧审稿格式错误'))

    def _unavailable(self):
        identity = reference._signature(dict(request=self.request, images=[]))
        report = dict(status='unavailable', request_sha256=identity,
                      reason='旧的可选格式提醒', quality_gate=False)
        reference._save(self.work / 'unavailable.json', report)
        return report

    def test_newer_service_error_is_not_hidden_by_old_format_exhaustion(self):
        self._exhausted()
        self._trace('call_1_new', text=None, error=True, order=2)
        with patch.object(self.session, 'guard'), patch.object(self.session, 'ask',
                side_effect=RuntimeError('本地文字模型服务不可用')) as ask:
            report = self.session.review(self.request, self.work)
        ask.assert_called_once()
        self.assertEqual(report['status'], 'unavailable')
        self.assertEqual(report['reason'], '本地文字模型服务不可用')
        self.assertNotIn('passed', report)

    def test_error_next_to_empty_response_does_not_count_as_format_evidence(self):
        reference._save(self.work / 'binding.json', self.binding)
        self._trace('call_1_error', text='', error=True)
        with patch.object(self.session, 'guard'), patch.object(self.session, 'ask',
                side_effect=RuntimeError(reference.EMPTY_OR_LENGTH)):
            report = self.session.review(self.request, self.work)
        self.assertEqual(report['status'], 'unavailable')
        self.assertEqual(report['technical_evidence'], dict(error_type='RuntimeError'))

    def test_current_service_failure_cannot_be_hidden_by_saved_unavailable(self):
        self._unavailable()
        self.session.failure = RuntimeError('当前服务不可用')
        with patch.object(self.session, 'guard'):
            with self.assertRaisesRegex(RuntimeError, '当前服务不可用'):
                self.session.review(self.request, self.work)

    def test_saved_unavailable_does_not_hide_a_newer_service_error(self):
        self._unavailable()
        trace = self._trace('call_1_new', text=None, error=True, order=2)
        newer = (self.work / 'unavailable.json').stat().st_mtime_ns + 1_000_000_000
        os.utime(trace / 'request.json', ns=(newer, newer))
        with patch.object(self.session, 'guard'), patch.object(self.session, 'ask',
                side_effect=RuntimeError('本地文字模型服务不可用：新的请求')) as ask:
            report = self.session.review(self.request, self.work)
        ask.assert_called_once()
        self.assertEqual(report['reason'], '本地文字模型服务不可用：新的请求')
        self.assertNotIn('passed', report)

    def test_cached_unavailable_still_honors_cancel_guard(self):
        self._unavailable()
        with patch.object(self.session, 'guard', side_effect=reference.core.ControlStop()):
            with self.assertRaises(reference.core.ControlStop):
                self.session.review(self.request, self.work)

    def test_complete_exhausted_format_remains_optional_without_model_call(self):
        self._exhausted()
        with patch.object(self.session, 'guard'), patch.object(self.session, 'ask') as ask:
            report = self.session.review(self.request, self.work)
        ask.assert_not_called()
        self.assertEqual(report['status'], 'unavailable')
        self.assertFalse(report['quality_gate'])

    def test_no_remaining_optional_budget_records_unknown_without_calling(self):
        self.manifest['actual_model_calls'] = 2
        with patch.object(self.session, 'guard'), patch.object(self.session, 'ask') as ask:
            report = self.session.review(self.request, self.work)
        ask.assert_not_called()
        self.assertEqual(report['status'], 'unavailable')
        self.assertIn('预算', report['reason'])
        self.assertEqual(self.manifest['actual_model_calls'], 2)
        self.assertIsNone(self.session.failure)

    def test_budget_skip_does_not_hide_changed_binding(self):
        reference._save(self.work / 'binding.json', dict(self.binding, system='已改变'))
        self.manifest['actual_model_calls'] = 2
        with patch.object(self.session, 'guard'), patch.object(self.session, 'ask') as ask:
            with self.assertRaisesRegex(RuntimeError, '证据变化'):
                self.session.review(self.request, self.work)
        ask.assert_not_called()
        self.assertFalse((self.work / 'unavailable.json').exists())

    def test_budget_during_optional_format_repair_does_not_latch_service_failure(self):
        self.manifest['reference_policy']['max_text_vision_calls'] = 1

        def ask_twice(*args, **kwargs):
            reference.qwen_client.chat('模拟首次审稿', '模拟格式回复')
            reference.qwen_client.chat('模拟有限修复', '同一份审稿')

        with patch.object(self.session, 'guard'), \
                patch.object(reference.core, 'request_json', side_effect=ask_twice), \
                patch.object(reference.qwen_client, 'chat', return_value=('{}', {})) as native:
            with self.session.models():
                report = self.session.review(self.request, self.work)
        self.assertEqual(report['status'], 'unavailable')
        self.assertIn('预算', report['reason'])
        self.assertEqual(self.manifest['actual_model_calls'], 1)
        native.assert_called_once()
        self.assertIsNone(self.session.failure)


class ReferenceSubjectBindingStabilityTests(unittest.TestCase):
    def test_spaced_subject_cannot_bypass_exact_speaker_binding(self):
        folder = Path(tempfile.mkdtemp(prefix='reference_binding_zero_model_'))
        cards = []
        for index, name in enumerate(('阿明', '小林'), 1):
            portrait = folder / ('portrait_%d.png' % index)
            portrait.write_bytes(b'fixture portrait')
            cards.append(dict(id='char_%d' % index, name=name, image_path=str(portrait),
                              personality='沉稳', voice='清朗自然的青年声音'))
        refs = characters.references_for_segment(cards)
        payload = dict(mode='ref2va', dialogue_slots=[dict(speaker='小林', text='好。',
                       speaker_id='S1', language='Chinese')])
        for tag in ('<Subject 1 >', '< Subject 1>', '<  Subject 1  >'):
            with self.subTest(tag=tag):
                raw = dict(dialogue=[dict(speaker='小林', text='好。',
                           delivery=tag + '，自然回应。')])
                before = copy.deepcopy(raw)
                with self.assertRaisesRegex(ValueError, '说话者不符'):
                    characters.bind_delivery(raw, payload, cards, refs,
                                              compiler=lambda value, _: value)
                self.assertEqual(raw, before)

    def test_matching_spaced_subject_is_bound_once_without_changing_line(self):
        folder = Path(tempfile.mkdtemp(prefix='reference_binding_match_zero_model_'))
        portrait = folder / 'portrait.png'
        portrait.write_bytes(b'fixture portrait')
        cards = [dict(id='char_1', name='阿明', image_path=str(portrait),
                      personality='沉稳', voice='清朗自然的青年声音')]
        refs = characters.references_for_segment(cards)
        payload = dict(mode='ref2va', dialogue_slots=[dict(speaker='阿明', text='好。',
                       speaker_id='S1', language='Chinese')])
        raw = dict(dialogue=[dict(speaker='阿明', text='好。',
                   delivery='< Subject 1 >，自然回应。')])
        result = characters.bind_delivery(raw, payload, cards, refs,
                                           compiler=lambda value, _: value)
        self.assertEqual(result['dialogue'][0]['speaker'], '阿明')
        self.assertEqual(result['dialogue'][0]['text'], '好。')
        self.assertEqual(result['dialogue'][0]['delivery'].count('Subject'), 1)
        self.assertIn('自然回应。', result['dialogue'][0]['delivery'])


if __name__ == '__main__':
    unittest.main()
