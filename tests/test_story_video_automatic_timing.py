"""Mechanical automatic timing/style normalization, without model execution."""
import copy
import os
import unittest

os.environ['V41_NO_MODEL'] = '1'

from cores import story_video_core as core, story_video_prompts as prompts
from tests.test_story_video_core import plan_fixture, written_fixture


class AutomaticTimingTests(unittest.TestCase):
    def fixture(self, lines=None, style='3D film'):
        plan = plan_fixture()
        plan['beats'][0]['dialogue'] = lines or [{'speaker': 'c1', 'text': '太阳落下时整座岛屿将沉入海底'}]
        _, payload, _ = prompts.write_request(plan, plan['beats'][0], {}, 8, visual_style=style)
        payload['automatic_normalization'] = True
        raw = written_fixture(payload)
        raw['integrated_multimodal_description'] = '[Shot 1] A medium shot follows the woman reading the stone.'
        for line in raw['dialogue']:
            line.update(start=0, end=2.5)
        return raw, payload

    def test_short_utterance_extends_to_ceiling_millisecond_with_fixed_source_and_start(self):
        raw, payload = self.fixture()
        self.assertEqual(len(raw['dialogue'][0]['text']), 14)
        compiled = prompts.compile_written(raw, payload)
        self.assertEqual(compiled['dialogue'][0]['end'], 3.112)
        self.assertEqual(compiled['dialogue'][0]['start'], 0)
        self.assertEqual([(line['speaker'], line['text']) for line in compiled['dialogue']],
                         [(line['speaker'], line['text']) for line in raw['dialogue']])
        self.assertEqual(compiled['automatic_adjustments'], [dict(
            kind='dialogue_duration', line_index=0, from_end=2.5, to_end=3.112)])
        core.validate_written(raw, payload)

    def test_short_utterance_cannot_cross_next_start_or_clip_end(self):
        raw, payload = self.fixture(lines=[{'speaker': 'c1', 'text': '字'*14},
                                          {'speaker': 'c1', 'text': '好'}])
        raw['dialogue'][1].update(start=3, end=4)
        with self.assertRaisesRegex(ValueError, '下一句start.*空间不足'):
            prompts.compile_written(raw, payload)
        raw, payload = self.fixture()
        raw['dialogue'][0].update(start=5.5, end=8)
        with self.assertRaisesRegex(ValueError, '片段结尾seconds.*空间不足'):
            prompts.compile_written(raw, payload)

    def test_full_spoken_intervals_still_require_all_pauses_in_shared_budget(self):
        raw, payload = self.fixture(lines=[{'speaker': 'c1', 'text': '字'*14},
                                          {'speaker': 'c1', 'text': '文'*14}])
        raw['dialogue'][0].update(start=0, end=3.6)
        raw['dialogue'][1].update(start=3.6, end=7.2)
        with self.assertRaisesRegex(ValueError, '实际发声与逐句停顿超过本段时长预算'):
            prompts.compile_written(raw, payload)

    def test_guided_mode_keeps_original_duration_and_does_not_add_style(self):
        for flag in (None, False):
            raw, payload = self.fixture(style='真人写实电影')
            if flag is None:
                payload.pop('automatic_normalization')
            else:
                payload['automatic_normalization'] = flag
            compiled = prompts.compile_written(raw, payload)
            self.assertEqual(compiled['dialogue'][0]['end'], 2.5)
            self.assertNotIn('Live-action', compiled['integrated_multimodal_description'])
            self.assertNotIn('automatic_adjustments', compiled)
            with self.assertRaisesRegex(ValueError, '用户要求真人写实'):
                core.validate_written(raw, payload)

    def test_normalization_is_immutable_idempotent_and_style_stays_outside_injected_speech(self):
        raw, payload = self.fixture(style='真人写实、自然表演')
        original = copy.deepcopy((raw, payload))
        compiled = prompts.compile_written(raw, payload)
        second = prompts.compile_written(compiled, payload)
        self.assertEqual((raw, payload), original)
        self.assertEqual(second, compiled)
        self.assertEqual(compiled['integrated_multimodal_description'].count(
            'Live-action, photorealistic, real adult actors.'), 1)
        self.assertNotIn('<d>', compiled['integrated_multimodal_description'])
        self.assertEqual(compiled['prompt'].count('<d>'), 1)
        self.assertEqual(len(compiled['automatic_adjustments']), 2)
        core.validate_written(raw, payload)

    def test_sufficient_duration_and_non_live_action_style_remain_unchanged(self):
        raw, payload = self.fixture(style='动漫风格')
        raw['dialogue'][0]['end'] = 4
        compiled = prompts.compile_written(raw, payload)
        self.assertEqual(compiled['dialogue'][0], raw['dialogue'][0])
        self.assertEqual(compiled['integrated_multimodal_description'], raw['integrated_multimodal_description'])
        self.assertNotIn('automatic_adjustments', compiled)
        core.validate_written(raw, payload)


if __name__ == '__main__':
    unittest.main()
