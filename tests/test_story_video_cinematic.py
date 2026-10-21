"""Cinematic dialogue contracts and legacy continuation, without model calls."""
import copy
import os
import re
import unittest

os.environ['V41_NO_MODEL'] = '1'

from cores import story_video_core as core, story_video_prompts as prompts
from tests.test_story_video_core import plan_fixture, written_fixture


TWO_LINES = [
    {'speaker': 'c1', 'text': '别担心，先跟着水声走。'},
    {'speaker': 'c2', 'text': '只要我们一起往前走，就一定能够找到山泉。'},
]

FOUR_LINES = [
    {'speaker': 'c1', 'text': '看，前面有船！'},
    {'speaker': 'c2', 'text': '先别急，礁石太近。'},
    {'speaker': 'c1', 'text': '你掌舵，我来放帆。'},
    {'speaker': 'c2', 'text': '好，我们绕过去。'},
]


def spoken_characters(text):
    return len(re.sub(r'[^\w\u4e00-\u9fff]', '', text))


def cinematic_plan(seconds=15, lines=None):
    plan = plan_fixture()
    # Card order deliberately differs from the first vocal appearance.
    plan['characters'].insert(0, {
        'id': 'c2', 'name': 'Shuo', 'appearance': 'adult man in grey', 'role': 'companion',
    })
    plan['invariants'] = ['one woman, one man and one ring']
    for beat in plan['beats']:
        beat.update(seconds=seconds, cast=['c1', 'c2'])
    plan['beats'][0]['dialogue'] = copy.deepcopy(TWO_LINES if lines is None else lines)
    return plan


def cinematic_written(payload, shot_count=2):
    result = written_fixture(payload)
    seconds = payload['seconds']
    views = ['a wide shot of the two adults beside the stream',
             'a medium shot of the woman pointing upstream',
             'a close-up of the man responding',
             'a wide shot of their coordinated movement toward the spring']
    shots = ['[Shot 1] ' + views[0].capitalize() + '.']
    for index in range(1, shot_count):
        shots.append('[Shot %d] At 00:%06.3f, the camera cuts to %s.' % (
            index + 1, seconds * index / shot_count, views[index]))
    result['integrated_multimodal_description'] = '\n'.join(shots)
    result['timeline'] = [dict(
        start=phase['start'], end=phase['end'],
        action='follow the water together', visible_result='reach the next visible landmark',
    ) for phase in payload['timing_budget']['motion_phases']]
    result['dialogue'] = []
    for index, line in enumerate(payload['beat']['dialogue']):
        start = seconds * index / len(payload['beat']['dialogue'])
        result['dialogue'].append(dict(
            line, start=start, end=start + spoken_characters(line['text']) / 4.5,
            delivery='The adult woman reassures her companion' if line['speaker'] == 'c1' else
                     'The adult man replies with quiet confidence',
        ))
    return result


class CinematicDialogueContractTests(unittest.TestCase):
    def validate_plan(self, plan, seconds=15):
        _, payload, schema = prompts.plan_request('两位成年人沿溪寻找山泉', '自然电影质感', 2, seconds)
        core.validate_schema(plan, schema)
        core.validate_plan(plan, payload)
        return payload

    def validate_written(self, plan, state=None, shot_count=2):
        _, payload, schema = prompts.write_request(
            plan, plan['beats'][0], state, plan['beats'][0]['seconds'], 'i2va')
        written = cinematic_written(payload, shot_count)
        core.validate_schema(written, schema)
        core.validate_written(written, payload)
        return payload, written

    def test_three_and_four_natural_turns_fit_planning_and_multi_shot_directing(self):
        for count in (3, 4):
            with self.subTest(turns=count):
                plan = cinematic_plan(lines=FOUR_LINES[:count])
                original = copy.deepcopy(plan)
                planned = self.validate_plan(plan)
                payload, written = self.validate_written(plan, shot_count=count)
                self.assertEqual(planned['timing_budget'], payload['timing_budget'])
                self.assertEqual(payload['timing_budget']['max_dialogue_seconds'], 15)
                self.assertEqual(len(written['dialogue']), count)
                self.assertEqual(plan, original)

    def test_full_clip_character_capacity_is_checked_by_plan_and_writer(self):
        valid = cinematic_plan(lines=[{'speaker': 'c1', 'text': '字' * 65}])
        self.validate_plan(valid)
        self.validate_written(valid)
        plan = cinematic_plan(lines=[{'speaker': 'c1', 'text': '字' * 33},
                                    {'speaker': 'c2', 'text': '字' * 33}])
        _, planned, plan_schema = prompts.plan_request('两位成年人寻找山泉', '', 2, 15)
        # Each individual line fits; the aggregate spoken budget must reject it.
        core.validate_schema(plan, plan_schema)
        with self.assertRaisesRegex(ValueError, '对白超过自然表演预算'):
            core.validate_plan(plan, planned)
        _, payload, schema = prompts.write_request(plan, plan['beats'][0], {}, 15)
        written = cinematic_written(payload)
        core.validate_schema(written, schema)
        with self.assertRaisesRegex(ValueError, '对白超过自然表演预算'):
            core.validate_written(written, payload)

    def test_turn_pauses_cannot_exceed_clip_even_when_character_total_fits(self):
        # 64 characters / 4.5 + two 0.5-second pauses needs over 15 seconds.
        plan = cinematic_plan(lines=[{'speaker': 'c1', 'text': '字' * 32},
                                    {'speaker': 'c2', 'text': '字' * 32}])
        _, payload, schema = prompts.plan_request('两位成年人寻找山泉', '', 2, 15)
        core.validate_schema(plan, schema)
        with self.assertRaisesRegex(ValueError, '对白无足够停顿时间'):
            core.validate_plan(plan, payload)
        _, payload, schema = prompts.write_request(plan, plan['beats'][0], {}, 15)
        written = cinematic_written(payload)
        core.validate_schema(written, schema)
        with self.assertRaisesRegex(ValueError, '对白无足够停顿时间'):
            core.validate_written(written, payload)

    def test_timed_speech_plus_pauses_cannot_exceed_clip(self):
        plan = cinematic_plan(lines=FOUR_LINES)
        payload, written = self.validate_written(plan, shot_count=4)
        # Short words spoken slowly still need their real intervals plus pauses.
        for line, (start, end) in zip(written['dialogue'],
                                      ((0, 3.4), (3.5, 6.9), (7, 10.4), (10.5, 14.5))):
            line.update(start=start, end=end)
        with self.assertRaises(ValueError):
            core.validate_written(written, payload)

    def test_parenthetical_stage_notes_are_not_spoken_dialogue(self):
        for note in ('（呼吸声加重，目光锁定雾中）', '(breathing harder, watching the fog)'):
            plan = cinematic_plan(lines=[{'speaker': 'c1', 'text': note}])
            with self.subTest(note=note), self.assertRaisesRegex(ValueError, '移入action或delivery'):
                self.validate_plan(plan)
            payload = prompts.write_request(plan, plan['beats'][0], {}, 15)[1]
            with self.subTest(written_note=note), self.assertRaisesRegex(ValueError, '移入action或delivery'):
                core.validate_written(cinematic_written(payload), payload)
        words = '别怕（压低声音），跟我来。'
        plan = cinematic_plan(lines=[{'speaker': 'c1', 'text': words}])
        self.validate_plan(plan)
        payload, written = self.validate_written(plan)
        self.assertEqual(payload['dialogue_slots'][0]['text'], words)
        self.assertIn('<d>[Chinese] %s</d>' % words, prompts.compile_written(written, payload)['prompt'])

    def test_timed_turns_reject_rushing_overlap_and_out_of_bounds(self):
        plan = cinematic_plan(lines=FOUR_LINES)
        payload, written = self.validate_written(plan, shot_count=4)
        for defect in ('rushed', 'overlapping', 'out_of_bounds'):
            changed = copy.deepcopy(written)
            if defect == 'rushed':
                changed['dialogue'][0]['end'] = .1
            elif defect == 'overlapping':
                changed['dialogue'][1]['start'] = .5
            else:
                changed['dialogue'][-1]['end'] = 15.1
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                core.validate_written(changed, payload)

    def test_turn_count_is_bounded_only_by_physical_speech_and_pause_capacity(self):
        # Twenty one-character turns fit 15 seconds; twenty-one do not.
        lines = [dict(speaker='c1' if index % 2 == 0 else 'c2', text='走')
                 for index in range(20)]
        plan = cinematic_plan(lines=lines)
        self.validate_plan(plan)
        self.validate_written(plan)
        plan['beats'][0]['dialogue'].append({'speaker': 'c1', 'text': '好'})
        schema = prompts.plan_request('两位成年人寻找山泉', '', 2, 15)[2]
        with self.assertRaisesRegex(ValueError, '项目数不符'):
            core.validate_schema(plan, schema)
        payload = prompts.plan_request('两位成年人寻找山泉', '', 2, 15)[1]
        with self.assertRaisesRegex(ValueError, '对白无足够停顿时间'):
            core.validate_plan(plan, payload)

    def test_original_words_and_speakers_are_injected_into_four_actual_shots(self):
        plan = cinematic_plan(lines=FOUR_LINES)
        payload, written = self.validate_written(plan, shot_count=4)
        original = copy.deepcopy((plan, payload, written))
        compiled = prompts.compile_written(written, payload)
        description = compiled['prompt'].split('integrated_multimodal_description:', 1)[1]
        description = description.split('overall_soundscape:', 1)[0]
        blocks = [block for block in re.split(r'(?=\[Shot \d+\])', description) if block.strip()]
        self.assertEqual(len(blocks), 4)
        for index, (line, block) in enumerate(zip(FOUR_LINES, blocks)):
            tag = '<d>[Chinese] %s</d>' % line['text']
            speaker_id = 'S1' if line['speaker'] == 'c1' else 'S2'
            self.assertIn('(%s), %s' % (speaker_id, tag), block)
            self.assertEqual(compiled['prompt'].count(tag), 1)
            for other_index, other in enumerate(FOUR_LINES):
                if other_index != index:
                    self.assertNotIn(other['text'], block)
        # Each turn starts at its corresponding shot boundary, including cuts.
        for index, block in enumerate(blocks):
            self.assertIn('At 00:%06.3f,' % (15 * index / 4), block)
        self.assertEqual((plan, payload, written), original)

    def test_all_cut_points_must_be_in_range_increasing_and_consecutively_numbered(self):
        plan = cinematic_plan(lines=FOUR_LINES)
        payload, written = self.validate_written(plan, shot_count=4)
        replacements = [('At 00:11.250,', 'At 00:15.000,'),
                        ('At 00:11.250,', 'At 00:07.500,'),
                        ('At 00:11.250,', 'At 00:06.000,'),
                        ('At 00:03.750,', 'At 00:00.000,'),
                        ('[Shot 3]', '[Shot 5]')]
        for old, new in replacements:
            changed = copy.deepcopy(written)
            changed['integrated_multimodal_description'] = (
                changed['integrated_multimodal_description'].replace(old, new))
            with self.subTest(replacement=new), self.assertRaises(ValueError):
                core.validate_written(changed, payload)

    def test_a_spoken_line_can_continue_across_a_cut_without_duplication(self):
        plan = cinematic_plan()
        payload, written = self.validate_written(plan)
        written['dialogue'][0].update(start=6, end=8)
        written['dialogue'][1].update(start=9, end=13)
        core.validate_written(written, payload)
        prompt = prompts.compile_written(written, payload)['prompt']
        first, second = prompt.split('[Shot 2]', 1)
        self.assertIn('At 00:06.000,', first)
        self.assertIn(TWO_LINES[0]['text'], first)
        self.assertNotIn(TWO_LINES[0]['text'], second)
        self.assertIn('At 00:09.000,', second)
        self.assertIn(TWO_LINES[1]['text'], second)

    def test_old_eight_second_and_eighteen_character_plans_continue_unchanged(self):
        cases = [(8, [{'speaker': 'c1', 'text': '别担心先跟着水声走'}]),
                 (15, [{'speaker': 'c1', 'text': '别担心先跟着水声走'},
                       {'speaker': 'c2', 'text': '我会陪你走到山泉边'}])]
        for seconds, lines in cases:
            with self.subTest(seconds=seconds):
                plan = cinematic_plan(seconds, lines)
                state = {'actual_visible': 'two adults beside the stream',
                         'unknowns': ['the spring is still out of view']}
                original = copy.deepcopy((plan, state))
                self.validate_plan(plan, seconds)
                payload, written = self.validate_written(plan, state)
                compiled = prompts.compile_written(written, payload)
                self.assertTrue(compiled['prompt'].startswith(prompts.I2VA_FIRST_FRAME + '\n\n'))
                self.assertEqual(payload['actual_state'], state)
                self.assertEqual([(line['speaker'], line['text']) for line in compiled['dialogue']],
                                 [(line['speaker'], line['text']) for line in lines])
                self.assertEqual((plan, state), original)

    def test_short_clips_also_allow_natural_multiple_turns(self):
        for seconds in (4, 8, 10):
            with self.subTest(seconds=seconds):
                plan = cinematic_plan(seconds, [{'speaker': 'c1', 'text': '走吧。'},
                                                {'speaker': 'c2', 'text': '好。'}])
                payload = self.validate_plan(plan, seconds)
                self.validate_written(plan)
                self.assertEqual(payload['timing_budget']['max_dialogue_seconds'], seconds)


if __name__ == '__main__':
    unittest.main()
