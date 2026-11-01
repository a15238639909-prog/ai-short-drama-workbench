"""Pure, ordinary-story actor staging tests; no model, services or fixtures."""
import copy
import unittest

from cores import source_actor_staging as staging


def acting(unit='U1', actor='阿宁', preparation='目光短暂下移，眉头轻蹙。',
           execution='指尖轻轻收拢，重心略向前移。', reaction='呼吸逐渐放缓。'):
    return dict(unit_id=unit, actor=actor, preparation=preparation,
                execution=execution, reaction=reaction)


class SourceActorStagingTests(unittest.TestCase):
    def setUp(self):
        self.units = [dict(id='U1', kind='action', text='阿宁注视窗外。'),
                      dict(id='U2', kind='action', text='陈川看着阿宁。'),
                      dict(id='U3', kind='dialogue', speaker='阿宁', text='请坐。')]
        self.names = ['阿宁', '陈川']

    def test_ordinary_phases_preserve_text_and_all_inputs(self):
        entries = [acting()]
        before = copy.deepcopy((entries, self.units, self.names))
        result = staging.validate_staging(entries, self.units, self.names)
        self.assertEqual(result, entries)
        self.assertEqual((entries, self.units, self.names), before)
        self.assertIsNot(result, entries)
        self.assertIsNot(result[0], entries[0])

    def test_two_known_actors_can_each_perform_in_their_own_units(self):
        entries = [acting(), acting(unit='U2', actor='陈川', preparation='眉梢微微抬起。',
                                   execution='', reaction='嘴角逐渐放松。')]
        self.assertEqual(staging.validate_staging(entries, self.units, self.names), entries)

    def test_dialogue_speaker_has_performance_without_copying_line(self):
        entries = [acting(unit='U3', preparation='呼吸稍作停顿。', execution='',
                          reaction='眼神逐渐放松。')]
        result = staging.validate_staging(entries, self.units, self.names)
        self.assertEqual(result, entries)
        self.assertNotIn('请坐', str(result))

    def test_single_known_actor_allows_clear_pronoun_but_multiple_actors_do_not(self):
        units = [dict(id='U1', text='她停在门旁。', kind='action')]
        self.assertTrue(staging.validate_staging([acting()], units, ['阿宁']))
        with self.assertRaises(ValueError):
            staging.validate_staging([acting()], units, self.names)

    def test_redundant_leading_actor_is_removed_once_only(self):
        item = acting(preparation='阿宁，目光短暂下移。')
        result = staging.validate_staging([item], self.units, self.names)
        self.assertEqual(result[0]['preparation'], '目光短暂下移。')
        self.assertEqual(item['preparation'], '阿宁，目光短暂下移。')

    def test_unknown_unit_actor_wrong_unit_and_offscreen_actor_are_rejected(self):
        cases = [(acting(unit='U8'), self.units), (acting(actor='陌生人'), self.units),
                 (acting(unit='U3', actor='陈川'), self.units),
                 (acting(), [dict(id='U1', text='阿宁的画外声音响起。', kind='action')]),
                 (acting(), [dict(id='U1', text='阿宁注视窗外。', onscreen=False)])]
        for item, units in cases:
            with self.subTest(item=item, units=units), self.assertRaises(ValueError):
                staging.validate_staging([item], units, self.names)

    def test_other_known_actor_and_new_people_are_rejected(self):
        for text in ('目光移向陈川。', '神情紧张，一名陌生男子出现在门边。',
                     '眼神专注，另有人从旁边探头。'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                staging.validate_staging([acting(preparation=text)], self.units, self.names)

    def test_core_events_are_rejected_even_if_already_in_source(self):
        units = [dict(id='U1', kind='action', text='阿宁推开门。')]
        for text in ('眉头轻蹙，推开门。', '眼神专注，拿起钥匙。', '呼吸放缓，关了门。',
                     '眉头轻蹙，开了灯。', '肩膀微动，摘下帽子。', '指尖轻动，手持钥匙。',
                     '眼神专注，轻敲桌面。', '眉头轻蹙，进入房间。'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                staging.validate_staging([acting(execution=text)], units, self.names)

    def test_real_stretched_hand_restatement_is_rejected(self):
        units = [dict(id='U1', kind='action', text='林岚伸出右手，掌心向上。')]
        for text in ('右手缓缓伸出，掌心向上，姿态自然。',
                     '缓缓伸出右手，手腕微微停顿。',
                     '她举起右手，指尖轻轻舒展。'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                staging.validate_staging([acting(actor='林岚', execution=text)], units, ['林岚'])

    def test_real_head_and_body_restatements_are_rejected(self):
        units = [dict(id='U1', kind='action', text='林岚低头看表。')]
        for text in ('低头注视怀表，神情干练。', '侧头，目光专注。',
                     '抬头后眉梢轻轻上挑。', '转身，呼吸逐渐放缓。',
                     '手臂缓缓抬起，手指舒展。'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                staging.validate_staging([acting(actor='林岚', preparation=text)], units, ['林岚'])

    def test_local_fingers_eyelids_jaw_and_weight_remain_available(self):
        item = acting(preparation='眼睑稍稍下垂，下颌微微绷紧。',
                      execution='指尖轻轻伸展，重心略微前移。', reaction='呼吸逐渐平稳。')
        self.assertEqual(staging.validate_staging([item], self.units, self.names), [item])

    def test_real_crown_turn_and_watch_reposition_are_rejected_with_the_fragment(self):
        units = [dict(id='U1', kind='action', text='陈默转动表冠，将怀表凑近右耳。')]
        bad = acting(actor='陈默', execution='指尖轻微发力转动表冠，随后将怀表移至右耳旁。')
        with self.assertRaisesRegex(ValueError, '转动表冠'):
            staging.validate_staging([bad], units, ['陈默'])
        for text in ('手腕稍作停顿，将怀表移至右耳旁。', '指尖微动，把杯子推向桌沿。',
                     '神情专注，凑近怀表。'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                staging.validate_staging([acting(actor='陈默', execution=text)], units, ['陈默'])
        safe = acting(actor='陈默', execution='指腹轻微发力，手腕短暂停顿。')
        self.assertEqual(staging.validate_staging([safe], units, ['陈默']), [safe])

    def test_errors_show_matched_state_or_event_and_export_shared_core_patterns(self):
        for text, matched in (('眉头微蹙，仍穿旧外套。', '外套'),
                              ('手指轻动，文件袋夹在右臂。', '文件袋'),
                              ('眉头轻蹙，右手缓缓伸出。', '伸出')):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, matched):
                staging.validate_staging([acting(execution=text)], self.units, self.names)
        import re
        self.assertTrue(any(re.search(pattern, '陈默递出怀表。')
                            for pattern in staging.CORE_EVENT_PATTERNS))

    def test_custom_event_patterns_and_cross_phase_events_are_checked(self):
        with self.assertRaises(ValueError):
            staging.validate_staging([acting(reaction='眼神清醒，警报响起。')],
                                     self.units, self.names, event_patterns=(r'警报响起',))
        with self.assertRaises(ValueError):
            staging.validate_staging([acting(preparation='眉头轻蹙，拿', execution='起，指尖微动。',
                                             reaction='')], self.units, self.names)

    def test_eyelid_closure_is_not_an_object_or_door_event(self):
        entries = [acting(preparation='双眼缓缓合上。', execution='', reaction='嘴唇微微闭合。')]
        self.assertEqual(staging.validate_staging(entries, self.units, self.names,
            event_patterns=(r'合上|闭合',)), entries)

    def test_wardrobe_holding_and_lighting_cannot_bypass_continuity_channels(self):
        for text in ('眉头微蹙，仍穿原来的旧外套。',
                     '肩膀微动，文件袋夹在右臂。',
                     '呼吸逐渐平稳，已经熄灭的灯微微发亮。'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                staging.validate_staging([acting(reaction=text)], self.units, self.names)

    def test_photography_rules_tags_and_copied_dialogue_are_rejected(self):
        for text in ('镜头推近她微蹙的眉头。', '眉头微蹙，必须保持身份一致。',
                     '<Subject 1>眉头轻蹙。', 'reaction：眉头轻蹙。',
                     '眉头轻蹙}，随后输出JSON。', '眉头微蹙，轻声说道“请坐。”',
                     '不要眨眼，目光平静。'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                staging.validate_staging([acting(preparation=text)], self.units, self.names)

    def test_schema_size_length_duplicate_and_empty_contracts(self):
        cases = [[acting()] * 4, [acting(), acting()],
                 [dict(acting(), unexpected=True)], [acting(preparation='眉' * 181)],
                 [acting(preparation='', execution='', reaction='')],
                 [acting(preparation=4)], [acting(preparation='随后桌子变成红色。')]]
        for entries in cases:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                staging.validate_staging(entries, self.units, self.names)
        self.assertEqual(staging.validate_staging([], self.units, self.names), [])
        self.assertEqual(staging.validate_staging(None, self.units, self.names), [])
        self.assertEqual(staging.ACTING_SCHEMA['maxItems'], 3)


if __name__ == '__main__':
    unittest.main()
