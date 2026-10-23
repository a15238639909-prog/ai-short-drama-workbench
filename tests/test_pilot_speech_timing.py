"""Retained-response and safety checks; no local AI or video is invoked."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from cores import story_video_prompts as base
from cores import workbench_pilot_continuity as continuity


ROOT = Path(__file__).resolve().parents[1]


def speech_hash(lines):
    words = [(row['speaker'], row['text']) for row in lines]
    return hashlib.sha256(json.dumps(words, ensure_ascii=False).encode()).hexdigest()


class PilotSpeechTimingTests(unittest.TestCase):
    def test_saved_third_director_reply_compiles_with_exact_words_and_shifted_slots(self):
        folder = ROOT / 'outputs/story_video/fantasy_combat_compare_20261022/segments/03/writing'
        response = next(folder.glob('call_2_*/text_*41250a31/response.json'), None)
        if response is None:
            self.skipTest('已保留的实跑回复未安装，不生成任何替代模型回复')
        raw = json.loads(json.loads(response.read_text(encoding='utf-8-sig'))['text'])
        payload = json.loads((folder / 'binding.json').read_text(encoding='utf-8-sig'))['payload']
        original = copy.deepcopy((raw, payload))
        normalized, timing_payload, audit = continuity.normalize_director_speech_timing(raw, payload)
        self.assertEqual(normalized['dialogue'][0]['start'], 3.5)
        self.assertEqual(normalized['dialogue'][0]['end'], 7.278)
        self.assertEqual(normalized['dialogue'][1]['start'], 7.778)
        self.assertEqual(normalized['dialogue'][1]['end'], 8.778)
        self.assertNotEqual(timing_payload.get('speech_budget_policy'), 'advisory')
        self.assertEqual(speech_hash(normalized['dialogue']), speech_hash(raw['dialogue']))
        for field in ('integrated_multimodal_description', 'timeline', 'expected_end_state'):
            self.assertEqual(normalized[field], raw[field])
        compiled = continuity.compile_director_reply(raw, payload)
        self.assertEqual(speech_hash(compiled['dialogue']), speech_hash(raw['dialogue']))
        self.assertEqual(compiled['dialogue'][0]['end'], 7.278)
        self.assertEqual(compiled['dialogue'][1]['start'], 7.778)
        self.assertEqual(compiled['prompt'].count('<d>'), 2)
        self.assertEqual(len(audit), 2)
        self.assertEqual((raw, payload), original)

    def test_adequate_slots_stay_exact_and_unavailable_budget_is_only_advisory(self):
        payload = dict(seconds=15, timing_budget=base._timing_budget(15))
        adequate = dict(dialogue=[dict(speaker='a', text='一起走。', start=2, end=4),
                                  dict(speaker='b', text='好。', start=7, end=9)])
        same, unchanged_payload, audit = continuity.normalize_director_speech_timing(adequate, payload)
        self.assertEqual(same, adequate)
        self.assertEqual(unchanged_payload, payload)
        self.assertEqual(audit, [])
        crowded = dict(dialogue=[dict(speaker='a', text='甲' * 80, start=13, end=14),
                                 dict(speaker='b', text='乙' * 20, start=14, end=15)])
        normalized, advisory, audit = continuity.normalize_director_speech_timing(crowded, payload)
        self.assertEqual(normalized, crowded)
        self.assertEqual(advisory['speech_budget_policy'], 'advisory')
        compiled = base._automatic_normalize_written(normalized, advisory)
        self.assertEqual(speech_hash(compiled['dialogue']), speech_hash(crowded['dialogue']))
        self.assertTrue(any(row['kind'] == 'speech_budget_warning' for row in compiled['automatic_adjustments']))
        for invalid in (float('nan'), -1, 16):
            unsafe = copy.deepcopy(crowded)
            unsafe['dialogue'][0]['start'] = invalid
            with self.subTest(time=invalid), self.assertRaises(ValueError):
                continuity.normalize_director_speech_timing(unsafe, payload)


if __name__ == '__main__':
    unittest.main()
