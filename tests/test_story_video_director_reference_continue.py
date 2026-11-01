"""Reference-label failures continue through source fallback; all media is fake."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'
from cores import story_video_reference as reference
from tests import test_story_video_reference_continue as flow_cases


class DirectorReferenceContinueTests(unittest.TestCase):
    def test_exhausted_unbound_director_finishes_without_relabeling_unknown_actor(self):
        original_ask = reference._Session.ask

        def broken_writer(session, request, directory, images=None, validator=None):
            result = original_ask(session, request, directory, images=images, validator=validator)
            if 'dialogue_slots' not in request[1]:
                return result
            for attempt in range(2):
                raw = copy.deepcopy(result)
                raw['integrated_multimodal_description'] = '[Shot 1] <Subject 5>转身，<Subject 6>跟随。'
                trace = Path(directory) / ('call_%d_broken_reference' % (attempt + 1)) / 'text_fixture'
                reference._save(trace / 'request.json', dict(system=request[0], payload=request[1]))
                reference._save(trace / 'response.json', dict(text=json.dumps(raw, ensure_ascii=False),
                    meta=dict(finish_reason='stop')))
                try:
                    validator(raw, request[1])
                except ValueError as error:
                    reference._save(trace.parent / 'contract_error.json', dict(error=str(error)))
                    if attempt:
                        raise RuntimeError('本地模型格式修复仍未通过：' + str(error)) from error
                else:
                    self.fail('未知Subject应由严格编译器拒绝')
        with patch.object(reference._Session, 'ask', new=broken_writer):
            folder, _, manifest, output, _, generated, joined = flow_cases.ReferenceContinueTests._flow(self)
        self.assertTrue(output['generation_completed'])
        self.assertEqual(len(generated), 2)
        self.assertEqual(len(joined), 1)
        self.assertEqual(output['quality_status'], 'completed_with_warnings')
        self.assertTrue(any('writing_fallback' in item['stage'] for item in manifest['continuation_warnings']))
        for beat, row in zip(manifest['plan']['beats'], manifest['segments']):
            compiled = reference._read(row['prompt_path'])
            self.assertNotIn('<Subject 5>', compiled['prompt'])
            self.assertNotIn('<Subject 6>', compiled['prompt'])
            self.assertIn(beat['action'], compiled['prompt'])
            self.assertNotIn('转身', compiled['prompt'])
            self.assertEqual([(x['speaker'], x['text']) for x in compiled['dialogue']],
                             [(x['speaker'], x['text']) for x in beat['dialogue']])
            self.assertEqual(reference._read(Path(row['raw_path']).with_name('video_submission.json'))['submissions'], 1)
            self.assertTrue((Path(row['prompt_path']).parent / 'writing_fallback_warning.json').exists())
        self.assertTrue(list(folder.glob('segments/*/writing/call_2_*/*/response.json')))

    def test_resume_keeps_existing_video_and_recovers_failed_reply_without_new_writing(self):
        def seed_failed_writer(folder, task, first):
            work = folder / 'segments/02_take_1/writing'
            binding = reference._read(work / 'binding.json')
            payload = binding['payload']
            raw = reference._source_director_fallback(payload)
            raw['integrated_multimodal_description'] = '[Shot 1] <Subject 5>转身。'
            for attempt in (1, 2):
                trace = work / ('call_%d_existing' % attempt) / 'text_fixture'
                reference._save(trace / 'request.json', dict(system=binding['system'], payload=payload))
                reference._save(trace / 'response.json', dict(text=json.dumps(raw, ensure_ascii=False),
                    meta=dict(finish_reason='stop')))
                reference._save(trace.parent / 'contract_error.json', dict(error="引用了未绑定素材：[('subject', '5')]"))
        folder, _, manifest, output, requests, generated, joined = flow_cases.ReferenceContinueTests._flow(
            self, pause=True, mutate=seed_failed_writer)
        self.assertTrue(output['generation_completed'])
        self.assertEqual(len(generated), 2)
        self.assertEqual(len(joined), 1)
        self.assertEqual(len(manifest['segments']), 2)
        # The only writer request for segment 2 is the paused one. On resume,
        # the same bound replies produce a fallback without a new writer call.
        second_writes = [row for row in requests if 'dialogue_slots' in row['payload']
                         and row['payload']['beat']['index'] == 2]
        self.assertEqual(len(second_writes), 1)
        self.assertTrue((folder / 'segments/02_take_1/writing/source_fallback.json').exists())
        self.assertFalse((folder / 'segments/01_take_1/writing/source_fallback.json').exists())


if __name__ == '__main__':
    unittest.main()
