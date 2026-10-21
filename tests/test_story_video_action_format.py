"""Pure plan validation: action metadata is preserved, speech still checked."""
import copy
import json
import os
import unittest

os.environ['V41_NO_MODEL'] = '1'

from cores import story_video_core as core
from cores import story_video_prompts as prompts
from cores import story_video_reference as reference
from tests.test_story_video_core import plan_fixture
from tests.test_story_video_reference_prompts import plan_and_raw, cards


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def phases(description='同伴抬手转动石环，水流随动作改变。'):
    return [dict(phase='0～5秒', description='  中景：人物来到石台旁，观察水流。  '),
            dict(phase='5～10秒', description=description),
            dict(phase='10～15秒', description='近景：人物放低手掌，抬头看向同伴。')]


class ActionFormatTests(unittest.TestCase):
    def fixture(self):
        plan = plan_fixture()
        for beat in plan['beats']:
            beat['seconds'] = 15
        payload = prompts.plan_request('同伴转动石环，疏通山泉后离开。', '自然电影质感', 2, 15)[1]
        return plan, payload

    def test_three_phase_json_is_metadata_not_dialogue_and_preserves_original_bytes(self):
        plan, payload = self.fixture()
        rows = phases()
        original_action = json.dumps(rows, ensure_ascii=False, indent=2)
        plan['beats'][0]['action'] = original_action
        before = encoded(plan)
        view = core._plan_action_text(original_action)
        self.assertEqual(view, '\n'.join(row['phase'] + '：' + row['description'] for row in rows))
        self.assertEqual([view.index(row['phase']) for row in rows],
                         sorted(view.index(row['phase']) for row in rows))
        self.assertEqual(list(core._action_spoken_quotes(view)), [])
        core.validate_plan(plan, payload)
        self.assertEqual(encoded(plan), before)
        self.assertEqual(plan['beats'][0]['action'].encode('utf-8'), original_action.encode('utf-8'))
        for row in rows:
            self.assertIn(row['description'], view)

    def test_single_phase_object_uses_the_same_read_only_view(self):
        plan, payload = self.fixture()
        row = dict(phase='0～15秒', description='中景跟随人物转环，随后观察水流。')
        plan['beats'][0]['action'] = json.dumps(row, ensure_ascii=False)
        before = copy.deepcopy(plan)
        core.validate_plan(plan, payload)
        self.assertEqual(core._plan_action_text(plan['beats'][0]['action']),
                         row['phase'] + '：' + row['description'])
        self.assertEqual(plan, before)

    def test_unknown_or_nested_dialogue_fields_cannot_be_silently_dropped(self):
        plan, payload = self.fixture()
        invalid_rows = (
            dict(phases()[0], dialogue='她说了一句未规划的话。'),
            dict(phases()[0], metadata=dict(dialogue='隐藏声音')),
            dict(phase='0～5秒', description=dict(dialogue='嵌套对白')),
            dict(phase='0～5秒'),
            dict(phase=5, description='转环'),
        )
        for row in invalid_rows:
            plan['beats'][0]['action'] = json.dumps([row], ensure_ascii=False)
            before = encoded(plan)
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, '结构化action'):
                core.validate_plan(plan, payload)
            self.assertEqual(encoded(plan), before)

    def test_ordinary_quoted_labels_visible_writing_and_names_are_not_spoken_lines(self):
        plan, payload = self.fixture()
        for action in ('镜头标注“远景”，人物从门口走到石台旁。',
                       '墙上写着“请保持安静”，人物观察门框。',
                       '门牌刻着：「黄金神殿」。人物沿台阶上行。',
                       '她翻开小说“山海”，书页被风吹动。',
                       '一行说明“安全出口”映入眼中，人物看向走廊。',
                       '人物看向名为“晨光”的石环，随后抬手。',
                       '[Shot 1] A label reads "wide shot" as the camera follows the traveler.',
                       '镜头切到标有\'北门\'的石碑，人物继续向前。'):
            plan['beats'][0]['action'] = action
            before = encoded(plan)
            with self.subTest(action=action):
                core.validate_plan(plan, payload)
                self.assertEqual(list(core._action_spoken_quotes(action)), [])
            self.assertEqual(encoded(plan), before)

    def test_explicit_new_chinese_and_english_speech_is_still_rejected(self):
        plan, payload = self.fixture()
        for action in ('她说：“跟我来。”随后转身。',
                       '她喊着“不要碰它！”并伸手阻拦。',
                       '她问「门在哪里？」随后看向同伴。',
                       '她低声道：“我会回来的。”',
                       '“跟我来。”，她说。',
                       'She says "Come with me." and turns toward the gate.',
                       'He whispers softly, "Stay here."',
                       '"Come with me," she said.'):
            plan['beats'][0]['action'] = action
            before = encoded(plan)
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, '新增台词'):
                core.validate_plan(plan, payload)
            self.assertEqual(encoded(plan), before)

    def test_speech_inside_json_descriptions_is_checked_after_metadata_extraction(self):
        plan, payload = self.fixture()
        for description in ('她说：“一切交给我。”随后转环。',
                            'She says "Follow me." while raising her hand.'):
            plan['beats'][0]['action'] = json.dumps(phases(description), ensure_ascii=False)
            before = encoded(plan)
            with self.subTest(description=description), self.assertRaisesRegex(ValueError, '新增台词'):
                core.validate_plan(plan, payload)
            self.assertEqual(encoded(plan), before)

    def test_only_known_character_name_or_id_colon_marks_a_spoken_quote(self):
        plan, payload = self.fixture()
        plan['characters'][0]['name'] = '云岚'
        for description in ('云岚：“跟我来。”', 'c1: "Come with me."', '中景：云岚：“别靠近。”'):
            plan['beats'][0]['action'] = json.dumps(phases(description), ensure_ascii=False)
            with self.subTest(description=description), self.assertRaisesRegex(ValueError, '新增台词'):
                core.validate_plan(plan, payload)
        for description in ('镜头：“近景”。人物转环。', '路牌：“山泉入口”。人物继续前行。'):
            plan['beats'][0]['action'] = json.dumps(phases(description), ensure_ascii=False)
            before = encoded(plan)
            with self.subTest(description=description):
                core.validate_plan(plan, payload)
            self.assertEqual(encoded(plan), before)

    def test_future_speech_remains_rejected_with_single_quotes_or_no_quotes(self):
        plan, payload = self.fixture()
        plan['beats'][1]['dialogue'] = [dict(speaker='c1', text='放心。')]
        for description in ("她说'放心。'后转环", '她低声说放心，随后转环'):
            plan['beats'][0]['action'] = json.dumps(phases(description), ensure_ascii=False)
            with self.subTest(description=description), self.assertRaisesRegex(ValueError, '其他段原话'):
                core.validate_plan(plan, payload)
        plan['beats'][1]['dialogue'] = [dict(speaker='c1', text='先停下来让我看看。')]
        plan['beats'][0]['action'] = json.dumps(phases('人物突然先停下来让我看看，随后转环。'), ensure_ascii=False)
        with self.assertRaisesRegex(ValueError, '其他段原话'):
            core.validate_plan(plan, payload)

    def test_current_beat_original_speech_and_short_action_verbs_keep_existing_rules(self):
        plan, payload = self.fixture()
        plan['beats'][0]['dialogue'] = [dict(speaker='c1', text='稳住。')]
        plan['beats'][0]['action'] = json.dumps(phases('她说“稳住。”后扶住石环。'), ensure_ascii=False)
        plan['beats'][1]['action'] = '前爪稳住持环的手腕。'
        before = encoded(plan)
        core.validate_plan(plan, payload)
        self.assertEqual(encoded(plan), before)

    def test_reference_entry_reuses_action_view_without_changing_plan_or_source_speech(self):
        plan, _, _ = plan_and_raw()
        for beat in plan['beats']:
            beat['dialogue'] = []
            beat['action'] = json.dumps(phases(), ensure_ascii=False, indent=2)
        payload = prompts.plan_request('同伴观察线索后继续前进。', '自然电影质感', 2, 15)[1]
        before = encoded(plan)
        source = dict(dialogue=[])
        reference.validate_reference_plan(plan, payload, source, cards())
        self.assertEqual(encoded(plan), before)
        wrong = copy.deepcopy(plan)
        wrong['beats'][0]['action'] = json.dumps(phases('她说：“未经原文允许的新对白。”'), ensure_ascii=False)
        with self.assertRaisesRegex(ValueError, '新增台词'):
            reference.validate_reference_plan(wrong, payload, source, cards())


if __name__ == '__main__':
    unittest.main()
