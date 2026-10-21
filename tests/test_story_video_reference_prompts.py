"""Zero-model contracts for production multi-character reference authoring."""
import copy
import unittest

from cores import story_video_characters as characters
from cores import story_video_reference_prompts as prompts
from cores import story_video_prompts as original_prompts


def cards(count=4):
    names = ('伊莱恩', '赛琳娜', '诺亚', '莉娅')
    return characters.normalize_cards([
        dict(image_path='fixture_person_%d.png' % (index + 1), name=names[index] if index < 4 else '人物%d' % (index + 1),
             personality='好奇但会观察同伴的回应',
             voice='清朗的少年男声' if index % 2 == 0 else '明亮自然的少女声')
        for index in range(count)])


def plan_and_raw(order=(3, 1, 2, 0)):
    cast = cards()
    texts = ('嗯。', '好。', '明白。', '可以。')
    dialogue = [dict(speaker=cast[index]['name'], text=texts[index]) for index in order]
    beat = dict(index=1, seconds=15, goal='契约检查', action='观察并自然交流', turn='理解线索',
                emotion='专注', ending='保持自然状态', cast=[row['id'] for row in cast], dialogue=dialogue)
    plan = dict(title='零模型素材测试', logline='仅用于契约测试', ending='测试结束', props=[], invariants=[],
        characters=[dict(id=row['id'], name=row['name'], appearance='14～15岁少年，外貌与服装依参考图', role='同行者') for row in cast],
        beats=[beat, dict(beat, index=2, dialogue=[])])
    raw = dict(integrated_multimodal_description='[Shot 1] 中景呈现四位14～15岁少年在同一空间自然交流。前五秒观察线索，中间五秒彼此回应。\n\n[Shot 2] At 00:10.000, 镜头切至侧前方近景，最后五秒表情反应清楚可见。',
        overall_soundscape='自然风声与轻微脚步声。', non_diegetic_music='N/A', expected_end_state='角色仍在原处交流。',
        brief='观察后理解并回应。',
        timeline=[dict(start=start, end=start + 5, action='自然交流', visible_result='表情可辨') for start in (0, 5, 10)],
        dialogue=[dict(line, start=index * 3 + .5, end=index * 3 + 2, delivery='自然清楚地回应') for index, line in enumerate(dialogue)],
        continuity_notes=[])
    return plan, beat, raw


class ReferencePromptContracts(unittest.TestCase):
    def test_full_director_rules_keep_ages_and_original_module_untouched(self):
        before = original_prompts._WRITE_SYSTEM
        plan, beat, raw = plan_and_raw()
        refs = characters.references_for_segment(cards())
        system, payload, _ = prompts.write_request(plan, beat, {}, 15, mode='ref2va',
            visual_style='西方奇幻、日漫美型设计、真人电影写实质感', references=refs)
        for rule in ('当前实际画面与actual_state', '景别服务于本段goal', '施力和结果必须看得清',
                     'dialogue_slots是唯一逐字对白来源', 'timeline列真实开始'):
            self.assertIn(rule, system)
        self.assertNotIn('real adult actors', system)
        final = characters.bind_delivery(raw, payload, cards(), refs)
        self.assertIn('14～15岁', final['prompt'])
        self.assertIn('真人写实电影摄影', final['prompt'])
        for unwanted in ('real adult actors', '林老师', '小夏', '教室', '成年身份'):
            self.assertNotIn(unwanted, final['prompt'])
        self.assertEqual(original_prompts._WRITE_SYSTEM, before)

    def test_four_speakers_voices_follow_identity_not_first_speech_number(self):
        for order in ((3, 1, 2, 0), (1, 0, 3, 2)):
            with self.subTest(order=order):
                plan, beat, raw = plan_and_raw(order)
                refs = characters.references_for_segment(cards(), 'fixture_scene.png')
                _, payload, _ = prompts.write_request(plan, beat, {}, 15, mode='ref2va', references=refs)
                raw_before, payload_before = copy.deepcopy(raw), copy.deepcopy(payload)
                final = characters.bind_delivery(raw, payload, cards(), refs)
                for position, audit in enumerate(final['voice_binding_audit']):
                    self.assertEqual(audit['character_id'], cards()[order[position]]['id'])
                    self.assertEqual(audit['subject'], order[position] + 1)
                    self.assertEqual(audit['speaker_id'], 'S%d' % (position + 1))
                    self.assertEqual(audit['voice'], cards()[order[position]]['voice'])
                self.assertEqual(raw, raw_before)
                self.assertEqual(payload, payload_before)
                self.assertEqual([row['text'] for row in final['dialogue']], [row['text'] for row in raw['dialogue']])

    def test_scene_optional_and_later_tail_removes_scene_definition(self):
        portrait_only = characters.references_for_segment(cards())
        self.assertEqual(len(portrait_only), 4)
        self.assertTrue(all(row['role'] == 'character' for row in portrait_only))
        first = characters.references_for_segment(cards(), {'path': 'fixture_scene.png'})
        later = characters.references_for_segment(cards(), 'fixture_scene.png', {'anchor_path': 'real_tail.png'})
        self.assertEqual(first[-1]['role'], 'scene_anchor')
        self.assertEqual((later[-1]['role'], later[-1]['picture']), ('previous_frame', 5))
        self.assertNotIn('fixture_scene.png', str(later))
        plan, beat, raw = plan_and_raw()
        for refs in (portrait_only, later):
            _, payload, _ = prompts.write_request(plan, beat, {}, 15, mode='ref2va', references=refs)
            final = characters.bind_delivery(raw, payload, cards(), refs)
            self.assertNotIn('是场景参考', final['prompt'])
        self.assertIn('<Picture 5>是本组上一段的真实最后一帧', final['prompt'])

    def test_reference_count_is_one_through_eight(self):
        one = characters.references_for_segment(cards(1))
        self.assertEqual(len(prompts._references(one)), 1)
        eight = characters.references_for_segment(cards(8))
        self.assertEqual(len(prompts._references(eight)), 8)
        for invalid in ([], eight + [dict(role='scene_anchor', picture=9, path='too_many.png')]):
            with self.assertRaises(ValueError):
                prompts._references(invalid)
        with self.assertRaisesRegex(ValueError, '合计'):
            characters.references_for_segment(cards(8), 'ninth.png')

    def test_source_speech_is_exact_and_written_text_is_not_added(self):
        source = '纸上写着“返回”。门上刻着：“请安静”。\n莉娅说：“好。”\n伊莱恩说：“嗯。”'
        contract = characters.prepare_source_contract(source, cards())
        self.assertEqual([(row['speaker_id'], row['text']) for row in contract['dialogue']],
                         [('char_04', '好。'), ('char_01', '嗯。')])
        plan, _, _ = plan_and_raw()
        plan['beats'][0]['dialogue'] = [dict(speaker='char_04', text='好。')]
        plan['beats'][1]['dialogue'] = [dict(speaker='伊莱恩', text='嗯。')]
        characters.validate_plan_source(plan, contract, cards())
        for change in ('punctuation', 'order', 'written_text'):
            wrong = copy.deepcopy(plan)
            if change == 'punctuation':
                wrong['beats'][0]['dialogue'][0]['text'] = '好！'
            elif change == 'order':
                wrong['beats'].reverse()
            else:
                wrong['beats'][1]['dialogue'].append(dict(speaker='伊莱恩', text='返回'))
            with self.subTest(change=change), self.assertRaises(ValueError):
                characters.validate_plan_source(wrong, contract, cards())

    def test_unknown_speaker_and_complex_writing_remain_unresolved(self):
        for source in ('她说：“门锁住了。”',
                       '伊莱恩在纸片背面写下“返回”，又添上一行小字：“天亮以后。”'):
            with self.subTest(source=source), self.assertRaises(ValueError) as caught:
                characters.prepare_source_contract(source, cards())
            self.assertEqual(caught.exception.code, 'speaker_unresolved')
            self.assertEqual(caught.exception.details['source_report']['source_original'], source)

    def test_personality_is_authoring_context_voice_is_final_only(self):
        authoring = characters.authoring_characters(cards())
        self.assertTrue(all(set(row) == {'id', 'name', 'personality'} for row in authoring))
        self.assertNotIn('少年男声', str(authoring))
        self.assertIn('总时长为60秒', characters.shortfilm_addendum(4, 15))

    def test_local_review_and_revision_reuse_original_quality_contract(self):
        plan, beat, raw = plan_and_raw()
        refs = characters.references_for_segment(cards())
        system, payload, _ = prompts.write_request(plan, beat, {}, 15, mode='ref2va', references=refs)
        final = characters.bind_delivery(raw, payload, cards(), refs)
        review = prompts.storyboard_request('按原文完整呈现互动。', '真人写实', plan, beat, final, {}, 15)
        self.assertIn('dialogue_timing', review[2]['properties'])
        revised_system, revised_payload, _ = prompts.revision_request(raw, payload, {'passed': False, 'issues': []})
        self.assertIn('dialogue_slots是唯一逐字对白来源', revised_system)
        self.assertEqual(revised_payload['dialogue_slots'], payload['dialogue_slots'])
        self.assertIn('唯一一次局部修订', revised_payload['correction'])


if __name__ == '__main__':
    unittest.main()
