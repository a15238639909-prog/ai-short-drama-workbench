"""Zero-model regression for explicit, warning-only speech estimates."""
import copy
import math
import unittest

from cores import story_video_prompts as base
from cores import story_video_reference_prompts as reference
from cores import story_video_characters as characters
from tests.test_story_video_reference_prompts import cards, plan_and_raw


def fixture(lines=None):
    return (dict(dialogue=copy.deepcopy(lines or [dict(speaker='甲', text='字' * 14, start=5.5, end=8)])),
            dict(seconds=8, visual_style='', timing_budget=base._timing_budget(8)))


class SpeechAdvisoryTests(unittest.TestCase):
    def warnings(self, result):
        return [row for row in result.get('automatic_adjustments', [])
                if row['kind'] == 'speech_budget_warning']

    def test_default_stays_strict_for_all_estimated_budget_failures(self):
        cases = [
            ([dict(speaker='甲', text='字' * 40, start=0, end=1)], '超过自然表演预算'),
            ([dict(speaker='甲', text='字' * 17, start=0, end=1),
              dict(speaker='甲', text='字' * 16, start=2, end=3)], '无足够停顿时间'),
            ([dict(speaker='甲', text='字' * 14, start=0, end=2.5),
              dict(speaker='甲', text='好', start=3, end=4)], '下一句start'),
            ([dict(speaker='甲', text='字' * 14, start=5.5, end=8)], '片段结尾seconds'),
            ([dict(speaker='甲', text='字' * 14, start=0, end=3.6),
              dict(speaker='甲', text='字' * 14, start=3.6, end=7.2)], '实际发声与逐句停顿'),
        ]
        for lines, reason in cases:
            for policy in (None, 'strict', True):
                with self.subTest(reason=reason, policy=policy):
                    raw, payload = fixture(lines)
                    payload['speech_budget_policy'] = policy
                    with self.assertRaisesRegex(ValueError, reason):
                        base._automatic_normalize_written(raw, payload)

    def test_advisory_preserves_original_intervals_when_expansion_has_no_space(self):
        for lines, reason in (
            ([dict(speaker='甲', text='字' * 14, start=0, end=2.5),
              dict(speaker='甲', text='好', start=3, end=4)], '下一句start'),
            ([dict(speaker='甲', text='字' * 14, start=5.5, end=8)], '片段结尾seconds'),
        ):
            with self.subTest(reason=reason):
                raw, payload = fixture(lines)
                payload['speech_budget_policy'] = 'advisory'
                snapshot = copy.deepcopy((raw, payload))
                result = base._automatic_normalize_written(raw, payload)
                self.assertEqual(result['dialogue'], lines)
                self.assertTrue(any(reason in row['reason'] for row in self.warnings(result)))
                self.assertEqual((raw, payload), snapshot)
                self.assertNotIn('passed', result)

    def test_count_and_pause_estimates_are_explicit_warnings(self):
        raw, payload = fixture([dict(speaker='甲', text='字' * 40, start=0, end=8)])
        payload['speech_budget_policy'] = 'advisory'
        result = base._automatic_normalize_written(raw, payload)
        reasons = [row['reason'] for row in self.warnings(result)]
        for part in ('超过自然表演预算', '无足够停顿时间', '片段结尾seconds', '实际发声与逐句停顿'):
            self.assertTrue(any(part in reason for reason in reasons), part)
        self.assertEqual(result['dialogue'], raw['dialogue'])

    def test_available_space_retains_existing_finite_extension(self):
        raw, payload = fixture([dict(speaker='甲', text='字' * 14, start=0, end=2.5)])
        payload['speech_budget_policy'] = 'advisory'
        result = base._automatic_normalize_written(raw, payload)
        self.assertEqual(result['dialogue'][0]['start'], 0)
        self.assertEqual(result['dialogue'][0]['end'], 3.112)
        self.assertEqual(result['automatic_adjustments'], [dict(
            kind='dialogue_duration', line_index=0, from_end=2.5, to_end=3.112)])

    def test_actual_invalid_numeric_timing_is_still_hard_failure(self):
        invalid = [(True, 2), (0, False), (math.nan, 2), (0, math.inf),
                   (-1, 2), (3, 2), (1, 1), (0, 8.1), ('0', 2)]
        for start, end in invalid:
            with self.subTest(start=start, end=end):
                raw, payload = fixture([dict(speaker='甲', text='好', start=start, end=end)])
                payload['speech_budget_policy'] = 'advisory'
                with self.assertRaises(ValueError):
                    base._automatic_normalize_written(raw, payload)
        raw, payload = fixture([dict(speaker='甲', text='好', start=0, end=3),
                                dict(speaker='乙', text='好', start=2, end=4)])
        payload['speech_budget_policy'] = 'advisory'
        with self.assertRaisesRegex(ValueError, '时间顺序'):
            base._automatic_normalize_written(raw, payload)

    def test_warning_normalization_is_immutable_and_idempotent(self):
        raw, payload = fixture()
        payload['speech_budget_policy'] = 'advisory'
        snapshot = copy.deepcopy((raw, payload))
        first = base._automatic_normalize_written(raw, payload)
        second = base._automatic_normalize_written(first, payload)
        self.assertEqual(first, second)
        self.assertEqual((raw, payload), snapshot)

    def test_chinese_reference_full_compiler_preserves_words_and_marks_warning(self):
        plan, beat, raw = plan_and_raw(order=(0,))
        text = '我们沿着这条小路慢慢走到前面的村庄看看吧'
        beat['dialogue'][0]['text'] = text
        raw['dialogue'][0].update(text=text, start=14, end=15)
        refs = characters.references_for_segment(cards())
        _, payload, _ = reference.write_request(plan, beat, {}, 15, mode='ref2va', references=refs)
        payload['speech_budget_policy'] = 'advisory'
        snapshot = copy.deepcopy((raw, payload))
        result = characters.bind_delivery(raw, payload, cards(), refs)
        self.assertEqual(result['dialogue'][0]['text'], text)
        self.assertEqual((result['dialogue'][0]['start'], result['dialogue'][0]['end']), (14, 15))
        self.assertIn(text, result['prompt'])
        self.assertTrue(any('片段结尾seconds' in row['reason'] for row in self.warnings(result)))
        self.assertEqual((raw, payload), snapshot)
        self.assertNotIn('passed', result)


if __name__ == '__main__':
    unittest.main()
