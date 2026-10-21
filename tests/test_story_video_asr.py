"""Local ASR contracts with fake recognizers/processes; no model inference."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cores import story_video_asr as asr, story_video_offline


def expected(text='别怕，跟我来。', speaker='c1', start=1, end=3):
    return dict(text=text, speaker=speaker, start=start, end=end)


def recognized(text, start=1, end=3, **extra):
    return dict(text=text, start=start, end=end, avg_logprob=-.2, no_speech_prob=.01, **extra)


class StoryVideoASRTests(unittest.TestCase):
    def setUp(self):
        # Retain fixtures instead of implicitly permanently deleting directories.
        self.root = Path(tempfile.mkdtemp(prefix='story_video_asr_zero_'))
        self.video = self.root/'clip.mp4'
        self.video.write_bytes(b'fixture video v1')
        self.model = self.root/'model'
        self.model.mkdir()
        for name in asr.REQUIRED_MODEL_FILES:
            (self.model/name).write_bytes(b'local fixture, not a real model')
        self.output = self.root/'review'

    def capability(self):
        return dict(available=True, model_path=str(self.model), reason='')

    def process(self, command, **kwargs):
        request = json.loads(Path(command[-2]).read_text(encoding='utf-8'))
        segments = [recognized(line['text'], line.get('start', 0), line.get('end', 1))
                    for line in request['dialogue']]
        Path(command[-1]).write_text(json.dumps(dict(segments=segments, language='zh')),
                                     encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    def test_capability_is_local_and_does_not_import_or_load_whisper(self):
        with patch.dict(os.environ, V41_ASR_MODEL_PATH=str(self.model)), \
             patch.object(asr.importlib.util, 'find_spec', return_value=object()) as find, \
             patch.object(asr, '_transcribe', side_effect=AssertionError('no inference')):
            result = asr.capability()
        self.assertTrue(result['available'])
        self.assertEqual(result['model_path'], str(self.model.resolve()))
        find.assert_called_once_with('faster_whisper')
        with patch.dict(os.environ, V41_ASR_MODEL_PATH=str(self.root/'missing')):
            self.assertFalse(asr.capability()['available'])

    def test_normalized_original_words_are_matched_in_order_without_input_mutation(self):
        dialogue = [expected('別怕，跟我來。'), expected('好！', 'c2', 5, 6)]
        segments = [recognized('别怕跟我来。 好。', 1, 6)]
        original = copy.deepcopy((dialogue, segments))
        result = asr._assess(segments, dialogue)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual([line['expected'] for line in result['lines']], ['別怕，跟我來。', '好！'])
        self.assertEqual(result['transcript'], segments[0]['text'])
        self.assertTrue(all(line['matched'] for line in result['lines']))
        self.assertFalse(result['audio_listened'])
        self.assertEqual((dialogue, segments), original)

    def test_missing_or_reversed_speech_does_not_pass(self):
        dialogue = [expected('先靠岸。'), expected('然后收帆。', 'c2')]
        for text in ('先靠岸。', '然后收帆。先靠岸。', ''):
            with self.subTest(text=text):
                result = asr._assess([recognized(text)] if text else [], dialogue)
                self.assertEqual(result['status'], 'failed')
                self.assertTrue(any(issue['code'] == 'missing_dialogue' for issue in result['issues']))

    def test_lines_use_actual_word_times_when_asr_combines_multiple_lines_in_one_segment(self):
        segment = recognized('先停船。再靠岸。', 0, 15)
        segment['words'] = [dict(word='先停船。', start=1.2, end=2.2, probability=.98),
                            dict(word='再靠岸。', start=6.1, end=7.4, probability=.97)]
        result = asr._assess([segment], [expected('先停船。'), expected('再靠岸。', 'c2', 6, 8)])
        self.assertEqual(result['status'], 'passed')
        self.assertEqual([(line['start'], line['end']) for line in result['lines']], [(1.2, 2.2), (6.1, 7.4)])
        self.assertTrue(all(line['timing_precision'] == 'word' for line in result['lines']))

    def test_short_negation_cannot_be_omitted_and_homophones_are_optional_evidence(self):
        result = asr._assess([recognized('路飞，那是宝藏。')], [expected('路飞，那不是宝藏。')])
        self.assertEqual(result['status'], 'failed')
        with patch.object(asr, '_phonetic', side_effect=lambda value: tuple(value.replace('菲', '飞'))):
            result = asr._assess([recognized('路菲，跟我来。')], [expected('路飞，跟我来。')])
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['lines'][0]['recognized'], '路菲，跟我来')

    def test_extra_spoken_words_are_reported(self):
        result = asr._assess([recognized('别怕跟我来，谢谢观看。')], [expected()])
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(any(issue['code'] == 'unexpected_dialogue' for issue in result['issues']))

    def test_low_confidence_or_missing_confidence_cannot_pass_even_with_matching_words(self):
        for confidence in (dict(avg_logprob=-4, no_speech_prob=.95), {},
                           dict(avg_logprob=float('nan'), no_speech_prob=.01)):
            segment = dict(text='别怕，跟我来。', start=1, end=3, **confidence)
            with self.subTest(confidence=confidence):
                self.assertEqual(asr._assess([segment], [expected()])['status'], 'failed')

    def test_no_expected_dialogue_still_checks_actual_speech(self):
        silent = asr._assess([], [])
        self.assertEqual(silent['status'], 'passed')
        for text in ('快走！', 'Hello there!'):
            result = asr._assess([recognized(text)], [])
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['issues'][0]['code'], 'unexpected_dialogue')
        uncertain = recognized('疑似声音')
        uncertain.update(avg_logprob=-3, no_speech_prob=.95)
        self.assertEqual(asr._assess([uncertain], [])['status'], 'failed')

    def test_non_chinese_dialogue_is_matched_and_language_can_be_automatic(self):
        line = expected('Stay close, follow me!')
        self.assertIsNone(asr._language([line]))
        self.assertIsNone(asr._language([]))
        self.assertEqual(asr._language([expected('ついてきて。')]), 'ja')
        self.assertEqual(asr._assess([recognized('Stay close. Follow me!')], [line])['status'], 'passed')
        self.assertEqual(asr._assess([recognized('Keep walking.')], [line])['status'], 'failed')

    def test_review_caches_only_the_same_video_and_dialogue_signature(self):
        dialogue = [expected()]
        with patch.object(asr, 'capability', side_effect=self.capability), \
             patch.object(asr, '_guard') as guard, \
             patch.object(asr.subprocess, 'run', side_effect=self.process) as process:
            first = asr.review(self.video, dialogue, self.output)
            cached = asr.review(self.video, dialogue, self.output)
            self.assertEqual(first['cache_key'], cached['cache_key'])
            self.assertEqual(process.call_count, 1)
            self.video.write_bytes(b'fixture video v2')
            changed_video = asr.review(self.video, dialogue, self.output)
            self.assertNotEqual(first['cache_key'], changed_video['cache_key'])
            changed_words = asr.review(self.video, [expected('赶快上船。')], self.output)
            self.assertNotEqual(changed_words['cache_key'], changed_video['cache_key'])
            self.assertEqual(process.call_count, 3)
            self.assertEqual(guard.call_count, 3)
            self.assertEqual(process.call_args.args[0][0], sys.executable)
            self.assertEqual(process.call_args.kwargs['env']['HF_HUB_OFFLINE'], '1')

    def test_unavailable_missing_video_and_timeout_cannot_pass(self):
        with patch.object(asr, 'capability', return_value=dict(available=False, model_path='', reason='missing')):
            self.assertEqual(asr.review(self.video, [], self.output)['status'], 'unavailable')
        self.assertEqual(asr.review(self.root/'absent.mp4', [], self.output)['status'], 'failed')
        with patch.object(asr, 'capability', side_effect=self.capability), patch.object(asr, '_guard'), \
             patch.object(asr.subprocess, 'run', side_effect=subprocess.TimeoutExpired('asr', 1)):
            result = asr.review(self.video, [expected()], self.output/'timeout')
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['issues'][0]['code'], 'asr_timeout')
        self.assertTrue(Path(result['report_path']).is_file())

    def test_pubg_block_is_raised_not_downgraded_to_optional_success(self):
        with patch.object(asr, 'capability', side_effect=self.capability), \
             patch.object(asr, '_guard', side_effect=asr.LocalASRBlocked('PUBG正在运行')), \
             patch.object(asr.subprocess, 'run') as process:
            with self.assertRaisesRegex(asr.LocalASRBlocked, 'PUBG'):
                asr.review(self.video, [expected()], self.output)
            process.assert_not_called()

    def test_transient_process_failure_is_not_replayed_as_a_permanent_cached_failure(self):
        with patch.object(asr, 'capability', side_effect=self.capability), patch.object(asr, '_guard'):
            with patch.object(asr.subprocess, 'run', side_effect=subprocess.TimeoutExpired('asr', 1)):
                self.assertEqual(asr.review(self.video, [expected()], self.output)['status'], 'unavailable')
            with patch.object(asr.subprocess, 'run', side_effect=self.process) as process:
                self.assertEqual(asr.review(self.video, [expected()], self.output)['status'], 'passed')
                process.assert_called_once()

    def test_child_pubg_block_is_also_raised(self):
        def blocked(command, **kwargs):
            Path(command[-1]).write_text(json.dumps(dict(blocked=True, error='PUBG正在运行')), encoding='utf-8')
            return SimpleNamespace(returncode=3, stdout='', stderr='')
        with patch.object(asr, 'capability', side_effect=self.capability), patch.object(asr, '_guard'), \
             patch.object(asr.subprocess, 'run', side_effect=blocked):
            with self.assertRaisesRegex(asr.LocalASRBlocked, 'PUBG'):
                asr.review(self.video, [expected()], self.output)

    def test_child_infrastructure_failure_is_unavailable_but_no_audio_is_content_failure(self):
        for code, status in (('asr_worker_error', 'unavailable'), ('no_audio', 'failed')):
            def failed(command, **kwargs):
                Path(command[-1]).write_text(json.dumps(dict(code=code, error='fixture failure')), encoding='utf-8')
                return SimpleNamespace(returncode=1, stdout='', stderr='fixture log')
            with self.subTest(code=code), patch.object(asr, 'capability', side_effect=self.capability), \
                 patch.object(asr, '_guard'), patch.object(asr.subprocess, 'run', side_effect=failed):
                self.assertEqual(asr.review(self.video, [expected()], self.output/code)['status'], status)

    def test_cpu_model_and_transcription_each_check_gate_and_use_no_download(self):
        events, options = [], {}
        class FakeModel:
            def __init__(self, path, **kwargs):
                events.append('load')
                options.update(kwargs)
            def transcribe(self, path, **kwargs):
                events.append('transcribe')
                options.update(kwargs)
                return [SimpleNamespace(start=0, end=1, text='你好', avg_logprob=-.1,
                                        no_speech_prob=.01)], SimpleNamespace(language='zh')
        with patch.dict(sys.modules, faster_whisper=SimpleNamespace(WhisperModel=FakeModel)), \
             patch.object(asr, '_guard', side_effect=lambda: events.append('guard')):
            result = asr._transcribe(self.root/'audio.wav', self.model, 'zh')
        self.assertEqual(events, ['guard', 'load', 'guard', 'transcribe'])
        self.assertEqual(options['device'], 'cpu')
        self.assertEqual(options['compute_type'], 'int8')
        self.assertEqual(options['cpu_threads'], 4)
        self.assertTrue(options['local_files_only'])
        self.assertTrue(options['word_timestamps'])
        self.assertFalse(options['condition_on_previous_text'])
        self.assertEqual(result['segments'][0]['text'], '你好')

    def test_worker_no_audio_fails_without_loading_asr(self):
        folder = self.root/'worker'
        folder.mkdir()
        request, response = folder/'request.json', folder/'response.json'
        request.write_text(json.dumps(dict(output_dir=str(folder), video_path=str(self.video),
                                           model_path=str(self.model))), encoding='utf-8')
        with patch.object(story_video_offline, 'install'), \
             patch.object(asr.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{"streams":[]}', stderr='')), \
             patch.object(asr, '_transcribe') as transcribe:
            self.assertEqual(asr._worker(request, response), 1)
        transcribe.assert_not_called()
        self.assertEqual(json.loads(response.read_text(encoding='utf-8'))['code'], 'no_audio')


if __name__ == '__main__':
    unittest.main()
