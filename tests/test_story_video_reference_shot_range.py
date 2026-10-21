"""No-model format compatibility; exact failed local reply is an immutable fixture."""
import copy
import json
import re
import unittest

from cores import story_video_characters as characters
from cores import story_video_reference_prompts as prompts
from tests.test_story_video_reference_prompts import cards, plan_and_raw

ACTUAL_FAILED_RAW = json.loads('{"integrated_multimodal_description": "[Shot 1] At 00:00.000, 镜头切至中景。遗迹大厅内，诺尔（年轻男性，红色卷发，身穿绿色斗篷与棕色上衣，背负皮制箭筒）位于石像左腿后方。他迅速搭箭上弦，拉动轻便木弓射出一支绳箭。绳箭破空飞出，精准缠住石像倾斜的巨斧斧柄。诺尔双脚死死蹬住碎石地面，身体后仰发力猛拉。石像的巨斧被绳索拽偏，斧头重重砸入左侧石柱缝隙，卡死不动。石像胸口的紫色光团随之剧烈闪烁。5～10秒：[Shot 2] 镜头切至中近景。莱恩（年轻男性，棕色卷发，身穿米色衬衫与深棕色皮甲背心）从画面左侧前景冲出，他松开握持的带鞘长剑，任由剑鞘落在碎石上。莱恩加速冲刺，右脚猛踏在倾斜的斧柄上借力腾空，身体如箭般跃向石像胸口。镜头跟随他的起跳轨迹平稳上摇。10～15秒：[Shot 3] 镜头切至特写。莱恩双手握住剑柄，将铁剑狠狠楔入胸甲的旧裂缝中。他双脚抵住石像肩甲，双手向后死死扳住剑柄。无光泽的铁刃因巨大的受力明显绷弯，发出沉闷的金属摩擦声。莱恩面部肌肉紧绷，双眼紧盯裂缝，身体悬停在石像胸前，维持着发力状态。", "overall_soundscape": "弓弦紧绷与绳箭破空声，巨斧撞击石柱的沉闷轰鸣，莱恩奔跑的脚步声与剑鞘落地声，铁剑楔入石缝的尖锐摩擦声，剑刃绷弯的细微金属呻吟。", "non_diegetic_music": "N/A", "expected_end_state": "铁剑插入裂缝并明显绷弯，莱恩双手紧握手柄，身体悬于石像胸前维持发力姿态。", "brief": "诺尔射绳箭卡住石像巨斧，莱恩弃鞘冲上斧柄跃至石像胸前，将铁剑插入旧裂缝并扳弯剑刃。", "timeline": [{"start": 0.0, "end": 5.0, "action": "诺尔射绳箭缠住巨斧斧柄并用力后拉，斧头卡入石柱", "visible_result": "石像巨斧被绳索限制卡死，胸口紫光闪烁"}, {"start": 5.0, "end": 10.0, "action": "莱恩丢弃剑鞘，冲刺踏斧柄借力跃向石像胸口", "visible_result": "莱恩腾空跃至石像胸前，剑鞘落地"}, {"start": 10.0, "end": 15.0, "action": "莱恩双手将铁剑插入旧裂缝并反向扳拽", "visible_result": "铁剑楔入裂缝，剑刃明显绷弯，莱恩悬停发力"}], "dialogue": [], "continuity_notes": ["承接上一段实际状态，石像胸口光团保持紫色，双眼亮起红光。诺尔位于石像左后方，利用木弓射出绳箭。", "莱恩起始手持带鞘长剑，本段第5秒处主动松开剑鞘落地，转为双手持无鞘铁剑，符合实际持物状态转换。", "巨斧被绳箭拉偏后卡入左侧石柱，限制石像挥砍，为莱恩提供踏脚点。", "铁剑插入裂缝后受力绷弯，莱恩双手紧握手柄维持悬停姿态，为下一段撬晶体预留物理支点。"]}')


def payload(lines=()):
    plan, beat, _ = plan_and_raw()
    beat['dialogue'] = [dict(speaker=speaker, text=text) for speaker, text in lines]
    return prompts.write_request(plan, beat, {}, 15, mode='ref2va',
        visual_style='真人写实电影摄影', references=characters.references_for_segment(cards()))[1]


class ReferenceShotRangeTests(unittest.TestCase):
    def test_actual_failed_reply_compiles_with_exact_five_and_ten_second_cuts(self):
        raw = copy.deepcopy(ACTUAL_FAILED_RAW)
        original = copy.deepcopy(raw)
        result = prompts.compile_written(raw, payload())
        self.assertEqual(raw, original)
        description = result['integrated_multimodal_description']
        self.assertEqual(re.findall(r'\[Shot [23]\] At (\d{2}:\d{2}\.\d{3}),', description),
                         ['00:05.000', '00:10.000'])
        expected = original['integrated_multimodal_description'].replace('[Shot 1] At 00:00.000, ', '[Shot 1] ')
        expected = expected.replace('5～10秒：[Shot 2]', '[Shot 2] At 00:05.000,')
        expected = expected.replace('10～15秒：[Shot 3]', '[Shot 3] At 00:10.000,')
        expected = expected.replace('[Shot 1] ', '[Shot 1] 真人写实电影摄影。', 1)
        self.assertEqual(description, expected)
        for field in ('timeline', 'dialogue', 'continuity_notes', 'overall_soundscape', 'non_diegetic_music'):
            self.assertEqual(result[field], original[field])
        audit = [row for row in result['automatic_adjustments'] if row['kind'] == 'explicit_chinese_shot_range']
        self.assertEqual([row['start_seconds'] for row in audit], [5, 10])
        for row in audit:
            self.assertEqual(original['integrated_multimodal_description'][row['source_start']:row['source_end']], row['from_text'])

    def test_already_valid_shots_and_matching_existing_at_are_preserved(self):
        raw = copy.deepcopy(ACTUAL_FAILED_RAW)
        text = raw['integrated_multimodal_description']
        text = text.replace('5～10秒：[Shot 2]', '[Shot 2] At 00:05.000,')
        text = text.replace('10～15秒：[Shot 3]', '[Shot 3] At 00:10.000,')
        normalized, audit = prompts._normalize_chinese_shot_ranges(text, 15)
        self.assertEqual(normalized, text)
        self.assertEqual(audit, [])
        redundant = text.replace('[Shot 2] At 00:05.000,', '5～10秒：[Shot 2] At 00:05.000,')
        normalized, audit = prompts._normalize_chinese_shot_ranges(redundant, 15)
        self.assertEqual(normalized, text)
        self.assertEqual(len(audit), 1)
        prompts.compile_written(dict(raw, integrated_multimodal_description=text), payload())

    def test_missing_cut_is_not_inferred_from_complete_timeline(self):
        raw = copy.deepcopy(ACTUAL_FAILED_RAW)
        raw['integrated_multimodal_description'] = raw['integrated_multimodal_description'].replace('5～10秒：', '')
        with self.assertRaisesRegex(ValueError, '后续Shot须在编号后给出At切点时间'):
            prompts.compile_written(raw, payload())

    def test_conflicting_invalid_reversed_and_out_of_bounds_times_are_rejected(self):
        for old, new in (
            ('5～10秒：[Shot 2]', '5～10秒：[Shot 2] At 00:06.000,'),
            ('5～10秒：[Shot 2]', '5～10秒：[Shot 2] At 不明确,'),
            ('10～15秒：[Shot 3]', '15～18秒：[Shot 3]'),
            ('10～15秒：[Shot 3]', '10～9秒：[Shot 3]'),
            ('5～10秒：[Shot 2]', '11～10秒：[Shot 2]'),
            ('5～10秒：[Shot 2]', '5～12秒：[Shot 2]'),
            ('10～15秒：[Shot 3]', '3～15秒：[Shot 3]'),
        ):
            raw = copy.deepcopy(ACTUAL_FAILED_RAW)
            raw['integrated_multimodal_description'] = raw['integrated_multimodal_description'].replace(old, new)
            with self.subTest(new=new), self.assertRaises(ValueError):
                prompts.compile_written(raw, payload())

    def test_dialogue_is_inserted_in_the_shot_containing_its_original_time(self):
        raw = copy.deepcopy(ACTUAL_FAILED_RAW)
        raw['dialogue'] = [dict(speaker='伊莱恩', text='好。', start=6, end=7, delivery='清楚自然地回应'),
                           dict(speaker='赛琳娜', text='明白。', start=11, end=12, delivery='清楚自然地回应')]
        original = copy.deepcopy(raw)
        result = prompts.compile_written(raw, payload((('伊莱恩', '好。'), ('赛琳娜', '明白。'))))
        blocks = re.split(r'(?=\[Shot \d+\])', result['fields']['detailed_description'])[1:]
        self.assertNotIn('<d>', blocks[0])
        self.assertIn('<d>[Chinese] 好。</d>', blocks[1])
        self.assertNotIn('明白。', blocks[1])
        self.assertIn('<d>[Chinese] 明白。</d>', blocks[2])
        self.assertNotIn('好。', blocks[2])
        self.assertEqual(raw, original)


if __name__ == '__main__':
    unittest.main()
