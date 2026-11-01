"""Text-only regressions: real compiler and adapter, fake camera planner."""
import copy
import json
import unittest

from cores import source_shot_compiler as compiler
from cores import workbench_story_director as writer
from tests.test_source_shot_compiler import model_reply
from tests.test_workbench_story_director_release import fixture


class TextReviewTests(unittest.TestCase):
    def write(self, speech, seconds=8, action='', groups='one', **kwargs):
        slices, cards, settings = fixture()
        settings.pop('video_action_compiler')
        row = copy.deepcopy(slices[0])
        lines, ids = [], []
        if action:
            lines.append(action)
        for index, (who, words) in enumerate(speech, 1):
            lines.append('【D%d】%s：%s' % (index, who, words))
            ids.append(index)
        row.update(seconds=seconds, text='\n'.join(lines))
        row['director'].update(blocks=[[0, seconds, '\n'.join(lines), ids]], says=speech)
        calls = []

        def q(system, user, **options):
            payload = json.loads(user)
            calls.append(payload)
            plan = model_reply(payload)
            if groups == 'one':
                plan['shots'] = [dict(plan['shots'][0], unit_ids=[u['id'] for u in payload['source_units']])]
            return json.dumps(plan, ensure_ascii=False)

        rows = writer.make_prompts([row], cards, ['竹庭'], q, settings=settings, **kwargs)
        return rows[0], calls

    def test_dialogue_only_line_keeps_pause_and_exact_words(self):
        row, calls = self.write([['阿岚', '先把杯子放好，我们再出发。']])
        self.assertEqual(len(calls), 1)
        self.assertFalse(row['source_fallback'])
        lines = row['director_compilation']['dialogue']
        self.assertEqual(lines[0]['text'], '先把杯子放好，我们再出发。')
        self.assertLessEqual(lines[0]['end'], row['seconds'] - .5 + .001)
        self.assertEqual(row['prompt'].count('先把杯子放好，我们再出发。'), 1)

    def test_multispeaker_dialogue_has_natural_speech_and_separate_pauses(self):
        speech = [['阿岚', '杯子已经放好了。'], ['顾亭', '好，我们现在出发。']]
        for groups in ('one', 'each'):
            with self.subTest(groups=groups):
                row, calls = self.write(speech, seconds=9, groups=groups)
                lines = row['director_compilation']['dialogue']
                self.assertEqual(len(calls), 1)
                self.assertEqual([(x['speaker'], x['text']) for x in lines], [tuple(x) for x in speech])
                self.assertGreaterEqual(lines[1]['start'] - lines[0]['end'], .499)
                self.assertLessEqual(sum(x['end'] - x['start'] for x in lines) + len(lines) * .5, 9.001)

    def test_impossible_original_speech_stops_before_request(self):
        calls = []
        slices, cards, settings = fixture()
        settings.pop('video_action_compiler')
        words = '请仔细检查每一件物品然后把所有文件完整交给门外等候的同事。' * 3
        row = slices[0]
        row.update(seconds=4, text='阿岚：' + words)
        row['director'].update(blocks=[[0, 4, '【D1】阿岚：' + words, [1]]], says=[['阿岚', words]])
        def q(*args, **kwargs):
            calls.append(True)
            return '{}'
        with self.assertRaisesRegex(ValueError, '原对白.*秒'):
            writer.make_prompts([row], cards, ['竹庭'], q, settings=settings)
        self.assertEqual(calls, [])

    def test_silent_action_sound_does_not_request_speech(self):
        row, _ = self.write([], action='阿岚把茶杯放在木桌上。')
        sound = row['prompt'].split('overall_soundscape:\n')[1].split('\n\nnon_diegetic_music:')[0]
        self.assertNotIn('对白', sound)
        self.assertEqual(row['director_compilation']['dialogue'], [])

    def test_current_camera_direction_reaches_planner_without_future_plot(self):
        row, calls = self.write([], action='阿岚把茶杯放在木桌上。', direction='固定机位，手部特写')
        self.assertIn('固定机位，手部特写', json.dumps(calls[0], ensure_ascii=False))
        self.assertNotIn('秘密石台', json.dumps(calls[0], ensure_ascii=False))
        self.assertNotIn('story_plan', calls[0])

    def test_scene_change_drops_previous_location_but_same_set_can_relay(self):
        state = dict(endpoint_evidence=dict(version='explicit-final-frame-v1', frame_index=7, verified=True),
            characters=[dict(id='a', frame_index=7, certainty='observed', visibility='visible',
                position='竹庭木桌旁', pose='坐着', hands='握着茶杯')])
        for scene, expected in (('竹庭', True), ('站台', False)):
            with self.subTest(scene=scene):
                slices, cards, settings = fixture()
                settings.pop('video_action_compiler')
                settings['_director_reference_paths'][scene] = __file__
                slices[1]['scene_hint'] = scene
                calls = []
                def q(system, user, **options):
                    payload = json.loads(user); calls.append(payload)
                    return json.dumps(model_reply(payload), ensure_ascii=False)
                row = writer.make_prompts(slices, cards, ['竹庭', scene], q, settings=settings,
                    start=1, count=1, actual_state_for=lambda index: state)[0]
                self.assertEqual(bool(calls[0]['endpoint_start_state']), expected)
                self.assertEqual(bool(row['director_contract']['start_state']), expected)
                if not expected:
                    self.assertNotIn('竹庭木桌旁', row['prompt'])

    def test_explicit_next_day_in_same_scene_drops_previous_pose(self):
        state = dict(endpoint_evidence=dict(version='explicit-final-frame-v1', frame_index=7, verified=True),
            characters=[dict(id='a', frame_index=7, certainty='observed', visibility='visible',
                position='木桌旁', pose='坐着', hands='握着茶杯')])
        slices, cards, settings = fixture()
        settings.pop('video_action_compiler')
        slices[1]['text'] = '第二天清晨，阿岚走进竹庭。'
        slices[1]['director'].update(blocks=[[0, 9, slices[1]['text'], []]], says=[])
        calls = []
        def q(system, user, **options):
            payload = json.loads(user); calls.append(payload)
            return json.dumps(model_reply(payload), ensure_ascii=False)
        row = writer.make_prompts(slices, cards, ['竹庭'], q, settings=settings,
            start=1, count=1, actual_state_for=lambda index: state)[0]
        self.assertEqual(calls[0]['endpoint_start_state'], '')
        self.assertEqual(row['director_contract']['start_state'], '')
        self.assertNotIn('握着茶杯', row['prompt'])


if __name__ == '__main__':
    unittest.main()
