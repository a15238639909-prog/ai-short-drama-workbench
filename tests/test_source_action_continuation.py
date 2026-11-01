"""Working-copy action continuation checks; no models or production data."""
import copy
import unittest

from cores.source_action_continuation import trim_slice


CHARS = [dict(name='阿岚'), dict(name='顾亭')]


def sl(text, scene='大厅', cast=None, says=None):
    return dict(text='── %s ──\n%s' % (scene, text.replace('【D1】', '')), scene_hint=scene,
                director=dict(cast=cast or ['阿岚'], says=says or [],
                              blocks=[[0, 8, text, [1] if '【D1】' in text else []]]))


class SourceActionContinuationTests(unittest.TestCase):
    def test_previous_rise_becomes_static_and_preserves_new_action(self):
        previous, current = sl('阿岚起身。'), sl('阿岚站起来，走向门口。')
        before = copy.deepcopy((previous, current, CHARS))
        result, audit = trim_slice(current, previous, CHARS)
        self.assertIn('阿岚站立，走向门口。', result['text'])
        self.assertNotIn('站起来', result['text'])
        self.assertEqual(audit[0]['source_kind'], 'planned_source')
        self.assertEqual((previous, current, CHARS), before)
        self.assertEqual(result['director']['blocks'][0][:2], [0, 8])

    def test_arrival_synonyms_and_multiple_repeated_prefix_steps(self):
        previous = sl('阿岚起身，走到门旁。')
        current = sl('阿岚起身，来到门边，压下门把。')
        result, audit = trim_slice(current, previous, CHARS)
        self.assertIn('阿岚站在门旁，压下门把。', result['text'])
        self.assertEqual(len([a for a in audit if a['method'] == 'suppress_repeated_prefix']), 2)
        self.assertEqual(result['director']['blocks'][0][2], '阿岚站在门旁，压下门把。')

    def test_other_person_other_target_approach_and_new_round_are_retained(self):
        for previous, current in ((sl('顾亭起身。'), sl('阿岚起身。')),
                                  (sl('阿岚走到前门旁。'), sl('阿岚来到后门旁。')),
                                  (sl('阿岚走向门旁。'), sl('阿岚走到门旁。')),
                                  (sl('阿岚起身。'), sl('阿岚再次起身。')),
                                  (sl('阿岚起身。'), sl('阿岚又站起来。'))):
            with self.subTest(previous=previous['text'], current=current['text']):
                self.assertEqual(trim_slice(current, previous, CHARS)[0], current)

    def test_current_posture_reset_stops_prefix_suppression(self):
        previous, current = sl('阿岚起身。'), sl('阿岚坐下，然后起身。')
        self.assertEqual(trim_slice(current, previous, CHARS)[0], current)
        previous = sl('阿岚起身，又坐下。')
        self.assertEqual(trim_slice(sl('阿岚起身。'), previous, CHARS)[0], sl('阿岚起身。'))

    def test_first_new_action_stops_later_repetition_removal(self):
        previous = sl('阿岚起身，来到门旁。')
        current = sl('阿岚起身，按下门铃，来到门旁。')
        result, _ = trim_slice(current, previous, CHARS)
        self.assertIn('按下门铃，来到门旁。', result['text'])

    def test_dialogue_and_quoted_future_rising_never_establish_state(self):
        for text in ('【D1】阿岚：“我起身去门口。”', '阿岚说：“我起身去门口。”',
                     '阿岚准备起身。', '阿岚打算站起来。', '阿岚还没起身。', '阿岚没有起身。'):
            previous = sl(text, says=[('阿岚', '我起身去门口。')] if '【D1】' in text else [])
            current = sl('阿岚起身。')
            with self.subTest(text=text):
                self.assertEqual(trim_slice(current, previous, CHARS)[0], current)

    def test_dialogue_marker_says_and_timing_remain_unchanged(self):
        previous = sl('阿岚起身。')
        current = sl('阿岚起身。\n【D1】阿岚：“我又起身了。”', says=[('阿岚', '我又起身了。')])
        result, _ = trim_slice(current, previous, CHARS)
        self.assertIn('【D1】阿岚：“我又起身了。”', result['director']['blocks'][0][2])
        self.assertEqual(result['director']['says'], current['director']['says'])
        self.assertEqual(result['director']['blocks'][0][3], [1])
        self.assertEqual(result['director']['blocks'][0][:2], [0, 8])

    def test_scene_time_boundaries_and_ambiguous_pronouns_are_unchanged(self):
        previous = sl('阿岚起身。')
        for current in (sl('阿岚起身。', scene='庭院'), sl('次日，阿岚起身。'),
                        sl('她起身。', cast=['阿岚', '顾亭'])):
            with self.subTest(current=current):
                self.assertEqual(trim_slice(current, previous, CHARS)[0], current)

    def test_verified_start_only_proves_current_pose_and_location(self):
        previous, current = sl('阿岚等待。'), sl('阿岚起身，走到门旁，按下门铃。')
        result, audit = trim_slice(current, previous, CHARS,
                                  verified_start_text='阿岚：门旁，站立，左手持包；顾亭：桌旁，坐姿')
        self.assertIn('阿岚站在门旁，按下门铃。', result['text'])
        self.assertTrue(all(a['source_kind'] == 'observed_start_state' for a in audit))
        self.assertNotIn('completed', str(audit))

    def test_verified_first_start_does_not_require_a_previous_slice(self):
        current = sl('阿岚起身，走到门旁，按下门铃。')
        result, audit = trim_slice(current, None, CHARS, verified_start_text='阿岚：门旁，站立')
        self.assertIn('阿岚站在门旁，按下门铃。', result['text'])
        self.assertTrue(all(a['source_kind'] == 'observed_start_state' for a in audit))
        self.assertEqual(trim_slice(current, None, CHARS)[0], current)

    def test_observed_sitting_overrides_previous_planned_standing(self):
        previous, current = sl('阿岚起身。'), sl('阿岚起身。')
        self.assertEqual(trim_slice(current, previous, CHARS,
            verified_start_text='阿岚：桌旁，坐姿')[0], current)

    def test_entire_repetition_is_concrete_static_and_idempotent(self):
        previous, current = sl('阿岚起身，走到门旁。'), sl('阿岚起身，来到门旁。')
        result, _ = trim_slice(current, previous, CHARS)
        self.assertEqual(result['director']['blocks'][0][2], '阿岚站在门旁。')
        self.assertEqual(trim_slice(result, previous, CHARS)[0], result)

    def test_novel_action_closes_prefix_window_across_later_blocks(self):
        previous = sl('阿岚起身。')
        current = sl('阿岚起身，压住门把。\n阿岚起身。')
        current['director']['blocks'] = [[0, 4, '阿岚起身，压住门把。', []],
                                         [4, 8, '阿岚起身。', []]]
        result, audit = trim_slice(current, previous, CHARS)
        self.assertEqual(result['director']['blocks'][0][2], '阿岚站立，压住门把。')
        self.assertEqual(result['director']['blocks'][1], current['director']['blocks'][1])
        self.assertEqual(sum(a['method'] == 'suppress_repeated_prefix' for a in audit), 1)

    def test_observing_another_actor_and_negative_actions_do_not_establish_pose(self):
        for text in ('阿岚看见顾亭站起来。', '阿岚不起身。', '阿岚不站起。',
                     '阿岚并未起身。', '阿岚从未站起来。'):
            current = sl('阿岚起身。')
            with self.subTest(text=text):
                self.assertEqual(trim_slice(current, sl(text), CHARS)[0], current)

    def test_stale_static_sitting_setup_before_repeated_rise_is_replaced(self):
        current = sl('阿岚坐在椅子上，起身，走向窗边。')
        result, _ = trim_slice(current, sl('阿岚起身。'), CHARS)
        self.assertEqual(result['director']['blocks'][0][2], '阿岚站立，走向窗边。')

    def test_exact_tail_to_head_open_and_put_events_are_suppressed(self):
        previous = sl('阿岚推开木门，放下文件袋。')
        current = sl('阿岚推开木门，放下文件袋，检查地图。')
        result, audit = trim_slice(current, previous, CHARS)
        self.assertNotIn('推开木门', result['text'])
        self.assertNotIn('放下文件袋', result['text'])
        self.assertIn('检查地图', result['text'])
        self.assertEqual(sum(a['method'] == 'suppress_exact_tail_head' for a in audit), 2)

    def test_exact_overlap_uses_longest_tail_and_does_not_skip_new_event(self):
        previous = sl('阿岚推开木门，放下文件袋。')
        current = sl('阿岚放下文件袋，压住地图，推开木门。')
        result, audit = trim_slice(current, previous, CHARS)
        self.assertEqual(sum(a['method'] == 'suppress_exact_tail_head' for a in audit), 1)
        self.assertIn('压住地图，推开木门', result['text'])
        self.assertIn('推开木门', trim_slice(sl('阿岚推开木门。'), previous, CHARS)[0]['text'])

    def test_exact_events_keep_other_objects_people_and_explicit_new_round(self):
        previous = sl('阿岚递出文件袋。')
        for current in (sl('阿岚递出地图。'), sl('顾亭递出文件袋。', cast=['顾亭']),
                        sl('阿岚再次递出文件袋。'), sl('阿岚压住地图，递出文件袋。')):
            with self.subTest(current=current['text']):
                self.assertEqual(trim_slice(current, previous, CHARS)[0], current)

    def test_entire_exact_repeat_keeps_time_dialogue_and_known_static_place(self):
        previous = sl('阿岚站在门旁，递出文件袋。')
        current = sl('阿岚递出文件袋。\n【D1】阿岚：“请收好。”', says=[('阿岚', '请收好。')])
        result, audit = trim_slice(current, previous, CHARS)
        self.assertIn('阿岚站在门旁。', result['text'])
        self.assertIn('【D1】阿岚：“请收好。”', result['director']['blocks'][0][2])
        self.assertEqual(result['director']['blocks'][0][:2], [0, 8])
        self.assertEqual(result['director']['says'], current['director']['says'])
        self.assertEqual(audit[-1]['source_kind'], 'planned_source')

    def test_exact_repeat_unknown_state_uses_face_without_a_new_event(self):
        previous, current = sl('阿岚推开木门。'), sl('阿岚推开木门。')
        before = copy.deepcopy((previous, current, CHARS))
        result, _ = trim_slice(current, previous, CHARS)
        self.assertEqual(result['director']['blocks'][0][2], '阿岚的面部近景。')
        self.assertEqual((previous, current, CHARS), before)
        self.assertEqual(trim_slice(result, previous, CHARS)[0], result)

    def test_unknown_observed_location_clears_planned_arrival_and_exact_cannot_override(self):
        previous = sl('阿岚起身，走到门旁。')
        current = sl('阿岚起身，走到门旁。')
        result, _ = trim_slice(current, previous, CHARS, verified_start_text='阿岚：椅子上，坐姿')
        self.assertEqual(result, current)
        current = sl('阿岚坐在椅子上，推开木门。')
        previous = sl('阿岚站在门旁，推开木门。')
        self.assertEqual(trim_slice(current, previous, CHARS)[0], current)

    def test_mismatched_text_and_blocks_are_retained_as_unknown(self):
        previous, current = sl('阿岚起身。'), sl('阿岚起身。')
        current['text'] = '阿岚检查地图。'
        result, audit = trim_slice(current, previous, CHARS)
        self.assertEqual(result, current)
        self.assertEqual(audit[0]['source_kind'], 'unknown')


if __name__ == '__main__':
    unittest.main()
