"""A failed duplicate-dialogue reply can resume without another model call."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from cores import story_video_core as core, story_video_mode_prompts as modes
from models import story_video_client as client
from tests.test_story_video_reference_prompts import plan_and_raw


class CachedDialogueRecoveryTests(unittest.TestCase):
    def test_previously_rejected_reply_is_revalidated_without_rewriting_story(self):
        plan, beat, raw = plan_and_raw(order=(0,))
        quote = raw['dialogue'][0]['text']
        raw['integrated_multimodal_description'] += '同伴轻声说：“' + quote + '”，随后抬手示意。'
        raw['dialogue'][0]['delivery'] = '轻声说：“' + quote + '”，随后点头'
        snapshot = copy.deepcopy(raw)
        request = modes.for_mode('image').write_request(plan, beat, {}, 15, mode='i2va')
        directory = Path(tempfile.mkdtemp(prefix='dialogue_cached_reply_retained_'))

        def old_validator(obj, payload):
            raise ValueError('对白原字仅放在dialogue，不能在正文或delivery重复')

        def stored_reply(system, payload, call_dir, **options):
            text = json.dumps(raw, ensure_ascii=False)
            core._save(Path(call_dir)/'text_fixture/response.json', dict(text=text))
            return text

        with patch.object(client, 'text', side_effect=stored_reply) as writer:
            with self.assertRaisesRegex(RuntimeError, '格式修复仍未通过'):
                core.request_json(request, directory, validator=old_validator)
        self.assertEqual(writer.call_count, 2)
        self.assertFalse((directory/'accepted.json').exists())
        with patch.object(client, 'text', side_effect=AssertionError('must reuse cached reply')) as writer, \
                patch.object(client, 'vision', side_effect=AssertionError('must not invoke vision')) as vision:
            accepted = core.request_json(request, directory, validator=core.validate_written)
        writer.assert_not_called()
        vision.assert_not_called()
        self.assertEqual(accepted, snapshot)
        self.assertTrue((directory/'accepted.json').exists())
        result = modes.for_mode('image').compile_written(accepted, request[1])
        self.assertEqual([(d['speaker'], d['text'], d['start'], d['end']) for d in result['dialogue']],
                         [(d['speaker'], d['text'], d['start'], d['end']) for d in snapshot['dialogue']])
        self.assertEqual(result['prompt'].count(quote), 1)
        self.assertIn('随后抬手示意', result['prompt'])
        self.assertIn('随后点头', result['dialogue'][0]['delivery'])
        self.assertEqual(raw, snapshot)


if __name__ == '__main__':
    unittest.main()
