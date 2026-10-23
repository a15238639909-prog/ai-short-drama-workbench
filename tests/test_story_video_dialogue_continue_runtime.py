"""Duplicate dialogue is repaired locally across real runtime/compiler paths.

All source stories and media are tiny synthetic fixtures. Every local-model,
H3 and media operation is mocked; scratch records are retained for inspection.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

os.environ['V41_NO_MODEL'] = '1'

from cores import story_video_core as core
from cores import story_video_reference as reference
from tests import test_story_video_mode_runtime as modes_fixture
from tests import test_story_video_reference_runtime as reference_fixture


def duplicate_words(value, payload):
    """A plausible local reply repeats the exact line in narration and delivery."""
    result = copy.deepcopy(value)
    for line in result['dialogue']:
        result['integrated_multimodal_description'] += ' 人物轻声说：“' + line['text'] + '”随后放下手。'
        line['delivery'] += '，轻声说：“' + line['text'] + '”'
    return result


class DuplicateDialogueRuntimeTests(unittest.TestCase):
    def mode_fixture(self, *, image=False, short_action=False, failed_revision=False):
        case, requests = modes_fixture.ModeRuntimeTests.fixture(self, image=image, automatic=True)
        case.task['inputs']['story_direction'] = '成年女子云岚先说：“先等等。”再说：“可以转了。”随后转动石环。'
        original_reply = case.text.side_effect
        drafts = []
        original_storyboard = case.storyboard_reply

        def storyboard_reply(payload, passed):
            result = original_storyboard(payload, passed)
            for row in result['dialogue_timing']:
                row['reason'] = '此句提醒发生在“' + payload['written']['timeline'][1]['action'] + '”之前。'
            return result

        case.storyboard_reply = storyboard_reply

        def reply(system, payload, *args, **kwargs):
            raw = original_reply(system, payload, *args, **kwargs)
            value = json.loads(raw)
            if 'beats' in value:
                for index, beat in enumerate(value['beats']):
                    beat['dialogue'] = [dict(speaker='c1', text=('先等等。', '可以转了。')[index])]
                if short_action:
                    value['beats'][0]['dialogue'][0]['text'] = '稳住'
                return json.dumps(value, ensure_ascii=False)
            if 'dialogue_slots' not in payload:
                return raw
            if failed_revision and payload.get('storyboard_feedback'):
                return '{}'
            # The shared automatic storyboard fixture puts a warning before
            # timeline index 1; give that existing fixture a real second phase.
            value['timeline'] = [
                dict(start=0, end=4, action='观察石环', visible_result='看清静止的石环'),
                dict(start=4, end=8, action='转动石环', visible_result='水流产生变化')]
            if short_action and payload['beat']['index'] == 1:
                value['integrated_multimodal_description'] += ' 她伸手稳住石环，水流逐渐变细。'
                value['dialogue'][0]['delivery'] = '人物轻声提醒，目光看向石环。'
            else:
                value = duplicate_words(value, payload)
            drafts.append(copy.deepcopy(value))
            return json.dumps(value, ensure_ascii=False)

        case.text.side_effect = reply
        if failed_revision:
            case.storyboard_outcomes = [False, True]
        return case, requests, drafts

    def assert_compiled_dialogue(self, path, expected):
        director = json.loads(Path(path).read_text(encoding='utf-8'))
        self.assertEqual([(row['speaker'], row['text']) for row in director['dialogue']], expected)
        spoken = re.findall(r'<d>\s*\[[^\]\n]+\]\s*(.*?)\s*</d>', director['prompt'], re.S)
        self.assertEqual(spoken, [text for _, text in expected])
        self.assertEqual(director['prompt'].count('<d>'), len(expected))
        return director

    def assert_adjustment_report(self, director_path, *, warning=False):
        report = json.loads(Path(director_path).with_name('dialogue_text_adjustments.json').read_text(encoding='utf-8'))
        self.assertEqual(report['status'], 'warning' if warning else 'normalized')
        self.assertFalse(report['quality_gate'])
        self.assertTrue(report['adjustments'])
        self.assertIn('dialogue_text_overlap_warning' if warning else 'dialogue_text_deduplicated',
                      [row['kind'] for row in report['adjustments']])

    def run_mode_and_resume(self, *, image=False):
        case, requests, drafts = self.mode_fixture(image=image)

        def pause_after_first(label, done):
            if label.startswith('第2/2段：本地AI编写'):
                case.task['pause_requested'] = True
                raise core.ControlStop()

        case.after_step = pause_after_first
        with self.assertRaises(core.ControlStop):
            core.run_job(case.task['id'])
        first = copy.deepcopy(case.manifest()['segments'][0])
        kept = Path(first['clip_path'])
        digest = hashlib.sha256(kept.read_bytes()).hexdigest()
        self.assertEqual(len(case.video_calls), 1)
        case.task.update(pause_requested=False, status='running')
        case.after_step = None
        result = core.run_job(case.task['id'])
        manifest = case.manifest()
        self.assertEqual(result['segments'], 2)
        self.assertEqual(manifest['segments'][0], first)
        self.assertEqual(hashlib.sha256(kept.read_bytes()).hexdigest(), digest)
        self.assertEqual(len(case.video_calls), 2)
        self.assertEqual(case.join.call_count, 1)
        writing = [item for item in requests if 'dialogue_slots' in item]
        self.assertEqual(len(writing), 2, '重复原话应在本地整理，不再额外要求模型修词')
        self.assertTrue(all('correction' not in item for item in writing))
        self.assertEqual(len(drafts), 2)
        for index, row in enumerate(manifest['segments']):
            director = self.assert_compiled_dialogue(row['prompt_path'],
                [('c1', ('先等等。', '可以转了。')[index])])
            self.assertIn('随后放下手', director['prompt'])
            self.assertNotIn('轻声说：“', director['prompt'])
            self.assert_adjustment_report(row['prompt_path'])
        self.assertTrue(manifest['quality_summary']['passed'], '确定机械去重不能被伪装成验收失败')
        self.assertEqual([item['mode'] for item in case.writer_states],
                         ['i2va', 'i2va'] if image else ['t2va', 'i2va'])
        before = (case.text.call_count, case.video.call_count)
        core.run_job(case.task['id'])
        self.assertEqual((case.text.call_count, case.video.call_count), before)

    def test_text_duplicates_continue_merge_and_reuse_completed_first_segment(self):
        self.run_mode_and_resume()

    def test_image_duplicates_continue_merge_and_reuse_completed_first_segment(self):
        self.run_mode_and_resume(image=True)

    def test_short_dialogue_word_inside_unspoken_action_preserves_action(self):
        case, requests, drafts = self.mode_fixture(short_action=True)
        result = core.run_job(case.task['id'])
        self.assertEqual(result['segments'], 2)
        self.assertEqual(len(case.video_calls), 2)
        self.assertEqual(case.join.call_count, 1)
        self.assertEqual(len([row for row in requests if 'dialogue_slots' in row]), 2)
        first = self.assert_compiled_dialogue(case.manifest()['segments'][0]['prompt_path'], [('c1', '稳住')])
        self.assertIn('伸手稳住石环，水流逐渐变细', first['prompt'])
        self.assert_adjustment_report(case.manifest()['segments'][0]['prompt_path'], warning=True)
        self.assertFalse(case.manifest()['quality_summary']['passed'])
        self.assertEqual(case.manifest()['quality_summary']['status'], 'completed_with_warnings')

    def test_optional_revision_format_failure_reuses_valid_original_without_reroll(self):
        case, requests, drafts = self.mode_fixture(failed_revision=True)
        result = core.run_job(case.task['id'])
        self.assertEqual(result['segments'], 2)
        self.assertEqual(len(case.video_calls), 2)
        self.assertEqual(case.join.call_count, 1)
        writing = [row for row in requests if 'dialogue_slots' in row]
        self.assertEqual(len(writing), 4)  # first draft + bounded revision twice + next draft
        self.assertEqual(len([row for row in writing if row.get('storyboard_feedback')]), 2)
        first = self.assert_compiled_dialogue(case.manifest()['segments'][0]['prompt_path'], [('c1', '先等等。')])
        self.assertIn('随后放下手', first['prompt'])

    def test_reference_duplicates_keep_speakers_order_and_resume_without_extra_model_calls(self):
        calls = []
        real_request_json = core.request_json

        def write_reply(value, request, directory, validator):
            value = duplicate_words(value, request[1])
            original = copy.deepcopy(value)

            def model_text(system, payload, *args, **kwargs):
                calls.append(copy.deepcopy(payload))
                return json.dumps(value, ensure_ascii=False)

            with patch.object(reference.client, 'text', side_effect=model_text):
                accepted = real_request_json(request, directory, validator=validator)
            self.assertEqual(value, original, '整理过程不得原地破坏模型原稿')
            return accepted

        helper = reference_fixture.ReferenceRuntimeTests(methodName='runTest')
        folder, task, manifest, output, stages, requests, generated = helper._flow(
            pause_second=True, writer_reply_hook=write_reply)
        self.assertEqual(output['segments'], 2)
        self.assertEqual(len(generated), 2)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all('correction' not in row for row in calls))
        self.assertEqual(len([row for row in requests if row['payload'].get('stage') == 'references']), 1)
        for index, segment in enumerate(manifest['segments']):
            expected = ([("莉娅", "可以。"), ("赛琳娜", "好。")],
                        [("诺亚", "明白。"), ("伊莱恩", "嗯。")])[index]
            director = self.assert_compiled_dialogue(segment['prompt_path'], expected)
            self.assertIn('随后放下手', director['prompt'])
            self.assertNotIn('轻声说：“', director['prompt'])
            self.assertEqual([item['character_id'] for item in director['voice_binding_audit']],
                             ['char_04', 'char_02'] if index == 0 else ['char_03', 'char_01'])
            self.assert_adjustment_report(segment['prompt_path'])
            self.assertEqual([item['final_delivery'] for item in director['voice_binding_audit']],
                             [line['delivery'] for line in director['dialogue']])
            self.assertTrue(all(item.get('pre_normalization_delivery') for item in director['voice_binding_audit']))
        self.assertTrue((folder/'movie').exists() or output['seconds'] == 30)


if __name__ == '__main__':
    unittest.main()
