"""Source-first writing and bounded output recovery, without a model or project writes."""
import copy
import os
os.environ['V41_NO_MODEL'] = '1'
import unittest
from unittest.mock import patch

from cores import authoring, manual_story, universal_writer as writer, oral_story
from models import qwen_client
from api import saga_api


class SourceAuthorityTests(unittest.TestCase):
    def test_writer_uses_idea_once_and_never_rechecks_finished_source(self):
        prose = '张明走进大厅。\n\n他举手说：“大家开始吧。”\n工人们齐声说：“好。”'
        with patch.object(writer, 'call', return_value=prose) as call, \
                patch.object(writer, 'review_prose', side_effect=AssertionError('No source recheck')), \
                patch.object(writer, 'merge_consistency', side_effect=AssertionError('No idea veto')):
            result = writer.write({'story_mode': 'single', 'one_line': '张明与工人在大厅集合。'},
                                  brief='张明与工人在大厅集合。')
        self.assertEqual(result['prose'], prose)
        self.assertTrue(result['review']['source_authority'])
        self.assertEqual(call.call_count, 1)
        self.assertIn('张明与工人在大厅集合', call.call_args.args[1])
        self.assertNotIn('核对目标', call.call_args.args[1])

    def test_manual_writer_saves_exact_literary_prose_without_fidelity_calls(self):
        prose = '张明站在大厅里。\n\n张明说：“好。”\n工人们齐声回答：“开始吧。”'
        episode = {'no': 1, 'status': 'PLANNED', 'brief': '在大厅集合。'}
        saga = {'episodes': [episode]}
        story = {'settings': {'story_mode': 'single', 'writer_table': False}, 'saga': saga}
        result = {'prose': prose, 'review': {'verdict': 'accepted', 'source_authority': True}}
        with patch.object(manual_story.story_core, 'get_story', return_value=story), \
                patch.object(manual_story.saga_core, 'get_saga', return_value=saga), \
                patch.object(manual_story.saga_core, 'save_saga'), \
                patch.object(oral_story, 'plan_episode', return_value={'mode': 'idea', 'context': [], 'events': []}), \
                patch.object(oral_story, 'fidelity_pass', side_effect=AssertionError('No fidelity review')), \
                patch.object(writer, 'write', return_value=result) as write, \
                patch('cores.asset_core.list_assets', return_value=[]):
            write.last_request = 'neutral source request'
            saved = manual_story.write('NEUTRAL_FIXTURE', 1)
        self.assertEqual(saved, prose)
        self.assertEqual(episode['prose'], prose)
        self.assertEqual(episode['writing_review']['source_contract']['source_original'], prose)
        self.assertEqual(episode['fidelity']['verdict'], 'not_reviewed')

    def test_imported_source_without_old_review_can_be_approved(self):
        episode = {'no': 1, 'brief': '集合', 'prose': '张明：好。'}
        with patch.object(manual_story.saga_core, 'get_saga', return_value={'episodes': [episode]}), \
                patch.object(manual_story.saga_core, 'save_saga'):
            approved = manual_story.approve('NEUTRAL_FIXTURE', 1)
        self.assertTrue(approved['prose_approved_sig'])

    def test_real_idea_change_still_protects_existing_source(self):
        episode = {'no': 1, 'brief': '新要求', 'prose': '旧正文',
                   'writing_review': {'brief_sig': writer.signature('旧要求')}}
        with patch.object(manual_story.saga_core, 'get_saga', return_value={'episodes': [episode]}):
            with self.assertRaisesRegex(ValueError, '已经改动'):
                manual_story.approve('NEUTRAL_FIXTURE', 1)
        self.assertEqual(episode['prose'], '旧正文')

    def test_source_episode_does_not_reenter_legacy_preflight(self):
        story = {'settings': {'story_mode': 'single'},
                 'saga': {'episodes': [{'no': 1, 'prose': '张明：好。'}]}}
        with patch.object(saga_api.story_core, 'get_story', return_value=story), \
                patch.object(authoring, 'fresh_shotlist', side_effect=AssertionError('No legacy review')), \
                patch.object(saga_api, '_job_event'):
            result = saga_api._preflight_gate('NEUTRAL_FIXTURE', {}, [], ep=1)
        self.assertFalse(result['blocked'])
        self.assertTrue(result['source_authority'])

    def test_cancel_is_still_respected(self):
        story = {'settings': {'story_mode': 'single'},
                 'saga': {'episodes': [{'no': 1, 'prose': '张明：好。'}]}}
        with patch.object(saga_api.story_core, 'get_story', return_value=story):
            with self.assertRaises(saga_api.JobCancelled):
                saga_api._preflight_gate('NEUTRAL_FIXTURE', {'cancel': True}, [], ep=1)


class TextOutputRecoveryTests(unittest.TestCase):
    def test_length_increases_output_budget_even_with_one_transport_attempt(self):
        responses = [('incomplete', {'finish_reason': 'length', 'content_len': 10}),
                     ('complete response', {'finish_reason': 'stop'})]
        with patch.object(qwen_client, 'chat', side_effect=responses) as chat, \
                patch.object(authoring, '_log_slow_call'):
            result = authoring._q('neutral formatting', 'neutral source', mt=2200, _tries=1)
        self.assertEqual(result, 'complete response')
        self.assertEqual([x.kwargs['max_tokens'] for x in chat.call_args_list], [2200, 4400])

    def test_budget_recovery_is_bounded_and_retains_partial_metadata(self):
        with patch.object(qwen_client, 'chat', return_value=('partial', {'finish_reason': 'length'})) as chat, \
                patch.object(authoring, '_log_slow_call'):
            with self.assertRaises(RuntimeError) as caught:
                authoring._q('neutral formatting', 'neutral source', mt=2200, _tries=1)
        self.assertEqual(chat.call_count, 4)
        self.assertEqual(caught.exception.code, 'model_output_limit')
        self.assertEqual(caught.exception.partial_text, 'partial')
        self.assertEqual(chat.call_args.kwargs['max_tokens'], 16000)

    def test_structured_reply_contract_is_preserved_during_budget_recovery(self):
        schema = {'type': 'json_object', 'schema': {'type': 'object'}}
        with patch.object(qwen_client, 'chat', side_effect=[
                ('{', {'finish_reason': 'length'}), ('{"ok":true}', {'finish_reason': 'stop'})]) as chat, \
                patch.object(authoring, '_log_slow_call'):
            self.assertEqual(authoring._q('format', 'source', mt=1000, _tries=1,
                                         response_format=schema), '{"ok":true}')
        self.assertTrue(all(x.kwargs['response_format'] == schema for x in chat.call_args_list))

    def test_card_extraction_length_keeps_source_speakers_and_arrival(self):
        source = '张明走进大厅，他放下工具并看向门口，准备开始今天的工作。\n张明：大家开始吧。\n李青：好，我先整理工具。'
        error = RuntimeError('metadata output incomplete')
        error.code = 'model_output_limit'
        error.partial_text = '人物：张明\n身份：工人\n'
        with patch.object(authoring, '_q', side_effect=error) as call, \
                patch.object(authoring, 'extract_dialogue', side_effect=AssertionError('No model speaker recheck')), \
                patch.object(authoring, '_dbg'):
            cards = authoring._extract_cards_raw(source)
        self.assertIn('张明', [name for kind, name, _ in cards if kind == '人物'])
        self.assertIn('李青', [name for kind, name, _ in cards if kind == '人物'])
        self.assertIn('大厅', [name for kind, name, _ in cards if kind == '场景'])
        self.assertEqual(call.call_count, 1)

    def test_empty_metadata_can_still_derive_written_opening_location(self):
        source = '张明站在大厅里，他把今天需要使用的工具放到桌边，然后看着面前的工作台。\n张明：开始吧。\n李青：好。'
        with patch.object(authoring, '_q', return_value=''), patch.object(authoring, '_dbg'):
            cards = authoring._extract_cards_raw(source)
        self.assertIn('大厅', [name for kind, name, _ in cards if kind == '场景'])

    def test_service_error_does_not_become_fake_metadata_success(self):
        with patch.object(authoring, '_q', side_effect=RuntimeError('connection refused')):
            with self.assertRaisesRegex(RuntimeError, 'connection refused'):
                authoring._extract_cards_raw('张明在大厅整理工具，然后等待其他工作人员来到现场开始工作。' * 3)


if __name__ == '__main__':
    unittest.main()
