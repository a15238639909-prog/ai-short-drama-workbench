"""Context-aware retries without model calls or project writes."""
import os
os.environ['V41_NO_MODEL'] = '1'
import unittest
from unittest.mock import patch

from cores import authoring
from models import qwen_client


class AuthoringContextLimitTests(unittest.TestCase):
    def test_director_enables_capacity_check(self):
        with patch.object(qwen_client, 'chat', return_value=('complete', {'finish_reason': 'stop'})) as chat, \
             patch.object(authoring, '_log_slow_call'):
            result = authoring._q('format', 'source', _check_context=True)
        self.assertEqual(result, 'complete')
        self.assertTrue(chat.call_args.kwargs['check_context'])

    def test_capacity_error_is_not_retried_and_keeps_diagnostics(self):
        error = RuntimeError('输入17381 tokens超过16384 tokens，无法预留5000 tokens输出。')
        error.code = 'model_context_limit'
        error.retryable = False
        with patch.object(qwen_client, 'chat', side_effect=error) as chat, \
             patch.object(authoring, '_log_slow_call') as log, \
             patch.object(authoring.time, 'sleep') as sleep:
            with self.assertRaises(RuntimeError) as caught:
                authoring._q('format', 'source', _tries=3, _check_context=True)
        self.assertIs(caught.exception, error)
        self.assertEqual(chat.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(log.call_args.kwargs['err'], str(error))

    def test_length_retry_stops_at_available_context_and_retains_partial(self):
        meta = {'finish_reason': 'length', 'context_output_limit': 6000}
        with patch.object(qwen_client, 'chat', return_value=('partial', meta)) as chat, \
             patch.object(authoring, '_log_slow_call'):
            with self.assertRaises(RuntimeError) as caught:
                authoring._q('format', 'source', mt=5000, _tries=1, _check_context=True)
        self.assertEqual([call.kwargs['max_tokens'] for call in chat.call_args_list], [5000, 6000])
        self.assertEqual(caught.exception.code, 'model_output_limit')
        self.assertEqual(caught.exception.partial_text, 'partial')

    def test_length_at_context_ceiling_does_not_increase_output(self):
        with patch.object(qwen_client, 'chat', return_value=(
                'partial', {'finish_reason': 'length', 'context_output_limit': 4000})) as chat, \
             patch.object(authoring, '_log_slow_call'):
            with self.assertRaises(RuntimeError):
                authoring._q('format', 'source', mt=4000, _tries=1, _check_context=True)
        self.assertEqual(chat.call_count, 1)


if __name__ == '__main__':
    unittest.main()
