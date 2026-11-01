"""User-edited action block boundaries survive the full source writer adapter."""
import copy
import json
import unittest

from cores import workbench_story_director as writer
from tests.test_source_shot_compiler import model_reply
from tests.test_workbench_story_director_release import fixture


class UserBlockTimingTests(unittest.TestCase):
    def write(self, blocks, says=(), query=None, timing_source='user'):
        slices, cards, settings = fixture()
        settings.pop('video_action_compiler')
        row = copy.deepcopy(slices[0])
        row.update(seconds=12, text='\n'.join(block[2] for block in blocks))
        row['director'].update(blocks=blocks, says=list(says), timing_source=timing_source)
        self.calls = []
        def q(system, user, **kwargs):
            payload = json.loads(user)
            self.calls.append(payload)
            reply = query(payload, len(self.calls)) if query else model_reply(payload)
            return json.dumps(reply, ensure_ascii=False)
        result = writer.make_prompts([row], cards, ['竹庭'], q, settings=settings)
        return result[0]

    def blocks(self):
        return [[0, 1, '阿岚抬手。', []], [1, 12, '阿岚慢慢走向石桌。', []]]

    def test_explicit_one_second_action_stays_one_second_in_prompt_and_audit(self):
        row = self.write(self.blocks())
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(row['source_fallback'])
        units = self.calls[0]['source_units']
        self.assertEqual([(u['timing_block'], u['block_start'], u['block_end']) for u in units],
                         [('B001', 0, 1), ('B002', 1, 12)])
        shots = row['director_compilation']['source_shot_audit']['shots']
        self.assertEqual([(s['start'], s['end']) for s in shots], [(0, 1), (1, 12)])
        self.assertIn('[Shot 2] At 00:01.000', row['prompt'])
        self.assertEqual(row['director_contract']['blocks'], self.blocks())

    def test_cross_block_shot_is_repaired_and_persistent_bad_group_falls_back_safely(self):
        def invalid(payload, attempt):
            plan = model_reply(payload)
            plan['shots'] = [dict(plan['shots'][0], unit_ids=[u['id'] for u in payload['source_units']])]
            return plan
        for repair in (True, False):
            with self.subTest(repair=repair):
                def query(payload, attempt):
                    return model_reply(payload) if repair and attempt > 1 else invalid(payload, attempt)
                row = self.write(self.blocks(), query=query)
                self.assertEqual(len(self.calls), 2)
                self.assertEqual(row['source_fallback'], not repair)
                shots = row['director_compilation']['source_shot_audit']['shots']
                self.assertEqual([(s['start'], s['end']) for s in shots], [(0, 1), (1, 12)])
                self.assertEqual(row['prompt'].count('抬手。'), 1)

    def test_multiple_camera_shots_share_only_their_own_block_time(self):
        row = self.write([[0, 3, '阿岚抬手。阿岚转身。', []],
                          [3, 12, '阿岚慢慢走向石桌。', []]])
        shots = row['director_compilation']['source_shot_audit']['shots']
        self.assertEqual([(s['start'], s['end']) for s in shots], [(0, 1.5), (1.5, 3), (3, 12)])

    def test_dialogue_cannot_borrow_time_from_another_user_block(self):
        words = '请把杯子放回桌上。'
        with self.assertRaisesRegex(ValueError, '用户时间块B001.*原对白.*秒'):
            self.write([[0, 1, '【D1】阿岚：' + words, [1]],
                        [1, 12, '阿岚走向石桌。', []]], [['阿岚', words]])
        self.assertEqual(self.calls, [])

    def test_user_block_dialogue_keeps_its_start_and_natural_pause(self):
        row = self.write([[0, 3, '阿岚放下茶杯。', []],
                          [3, 12, '【D1】顾亭：我们现在出发。', [1]]], [['顾亭', '我们现在出发。']])
        line = row['director_compilation']['dialogue'][0]
        self.assertEqual(line['start'], 3)
        self.assertLessEqual(line['end'], 11.5)
        self.assertEqual(row['prompt'].count('我们现在出发。'), 1)

    def test_invalid_user_gap_stops_before_camera_request_instead_of_shifting_it(self):
        with self.assertRaisesRegex(ValueError, '时间块.*连续覆盖'):
            self.write([[0, 1, '阿岚抬手。', []], [2, 12, '阿岚走向石桌。', []]])
        self.assertEqual(self.calls, [])

    def test_automatic_block_times_remain_camera_weights(self):
        row = self.write(self.blocks(), timing_source='auto')
        self.assertNotIn('timing_block', self.calls[0]['source_units'][0])
        shots = row['director_compilation']['source_shot_audit']['shots']
        self.assertEqual([(s['start'], s['end']) for s in shots], [(0, 6), (6, 12)])


if __name__ == '__main__':
    unittest.main()
