"""Local format cleanup only: ordinary adult scenes, no model or production data."""
import copy
import re
import unittest

from cores import story_video_reference_prompts as prompts


def fixture(mode='t2va', words=('我们出发吧。',)):
    names = ('林舟', '陆青')
    slots = [dict(speaker=names[index % 2], text=text, speaker_id='S%d' % (index + 1),
                  language='Chinese') for index, text in enumerate(words)]
    payload = dict(mode=mode, seconds=15, dialogue_slots=slots)
    if mode == 'ref2va':
        payload['canonical_references'] = [dict(role='character', picture=index + 1,
            subject=index + 1, character_id='char_%d' % index,
            name=name, path='adult_%d.png' % index) for index, name in enumerate(names)]
    raw = dict(integrated_multimodal_description='[Shot 1] 中景中成年林舟把书放到桌上，陆青在旁边观察。'
        '\n\n[Shot 2] At 00:10.000, 镜头推近两人的表情，两人转向门口。',
        overall_soundscape='轻微脚步与木桌摩擦声。', non_diegetic_music='舒缓的弦乐。',
        brief='成年人整理书本后一起出门。', expected_end_state='两人面向门口。',
        timeline=[dict(start=start, end=start + 5, action='整理书本', visible_result='书本归位')
                  for start in (0, 5, 10)], continuity_notes=[],
        dialogue=[dict(speaker=row['speaker'], text=row['text'], start=1 + index * 5,
                       end=4 + index * 5, delivery='自然清楚地回应') for index, row in enumerate(slots)])
    return raw, payload


class DialogueTextNormalizationTests(unittest.TestCase):
    def spoken(self, result):
        return re.findall(r'<d>\s*\[[^\]]+\]\s*(.*?)\s*</d>', result['prompt'], re.S)

    def adjustments(self, result, kind):
        return [row for row in result.get('automatic_adjustments', []) if row['kind'] == kind]

    def test_quoted_spoken_copies_all_three_modes_and_all_transport_fields(self):
        for mode in ('t2va', 'i2va', 'ref2va'):
            with self.subTest(mode=mode):
                raw, payload = fixture(mode)
                raw['integrated_multimodal_description'] += '林舟轻声说：“我们出发吧！”随后放下手。'
                raw['overall_soundscape'] += '他回应：「我们出发吧」。'
                raw['non_diegetic_music'] += '背景人声唱出：“我们出发吧。”'
                raw['dialogue'][0]['delivery'] += '，说：“我们出发吧！”'
                before, payload_before = copy.deepcopy(raw), copy.deepcopy(payload)
                result = prompts.compile_written(raw, payload)
                self.assertEqual(self.spoken(result), ['我们出发吧。'])
                self.assertEqual(result['prompt'].count('我们出发吧'), 1)
                self.assertIn('随后放下手', result['prompt'])
                self.assertEqual(result['dialogue'][0]['start'], 1)
                self.assertEqual(result['dialogue'][0]['end'], 4)
                self.assertEqual(raw, before)
                self.assertEqual(payload, payload_before)
                audit = self.adjustments(result, 'dialogue_text_deduplicated')
                self.assertEqual(sum(row['count'] for row in audit), 4)
                for row in audit:
                    original = before['dialogue'][0]['delivery'] if row['field'].startswith('dialogue[') else before[row['field']]
                    for match in row['matches']:
                        self.assertEqual(original[match['source_start']:match['source_end']], match['from_text'])

    def test_unquoted_whole_utterance_requires_speech_cue_and_boundary(self):
        raw, payload = fixture()
        raw['integrated_multimodal_description'] += '林舟回答：我们出发吧！随后推门。'
        result = prompts.compile_written(raw, payload)
        self.assertEqual(result['prompt'].count('我们出发吧'), 1)
        self.assertIn('随后推门', result['prompt'])
        self.assertEqual(sum(row['count'] for row in self.adjustments(result, 'dialogue_text_deduplicated')), 1)

    def test_comma_after_speaking_verb_is_removed_with_copy(self):
        raw, payload = fixture(words=('好。',))
        raw['integrated_multimodal_description'] += '林舟说，“好！”然后抬头。'
        result = prompts.compile_written(raw, payload)
        self.assertNotIn('林舟说，', result['prompt'])
        self.assertIn('然后抬头', result['prompt'])
        self.assertEqual(self.spoken(result), ['好。'])

    def test_short_word_inside_action_is_not_erased(self):
        raw, payload = fixture(words=('好',))
        raw['integrated_multimodal_description'] += '林舟好奇地看着好看的封面。'
        result = prompts.compile_written(raw, payload)
        self.assertIn('好奇地看着好看的封面', result['prompt'])
        self.assertFalse(self.adjustments(result, 'dialogue_text_deduplicated'))
        warnings = self.adjustments(result, 'dialogue_text_overlap_warning')
        self.assertTrue(warnings)
        self.assertEqual(warnings[0]['count'], 2)
        self.assertNotIn('passed', warnings[0])

    def test_phrase_overlap_in_non_speech_action_stays_with_warning(self):
        raw, payload = fixture(words=('稳住',))
        raw['integrated_multimodal_description'] += '她伸手稳住石环，水流逐渐变细。'
        result = prompts.compile_written(raw, payload)
        self.assertIn('她伸手稳住石环，水流逐渐变细。', result['prompt'])
        self.assertTrue(self.adjustments(result, 'dialogue_text_overlap_warning'))

    def test_unquoted_normalizer_does_not_join_separate_sentences(self):
        raw, payload = fixture(words=('我来',))
        raw['integrated_multimodal_description'] += '林舟说我。来，镜头向门口移动。'
        result = prompts.compile_written(raw, payload)
        self.assertIn('林舟说我。来，镜头向门口移动。', result['prompt'])
        self.assertFalse(self.adjustments(result, 'dialogue_text_deduplicated'))

    def test_voice_metadata_and_subject_prefix_are_preserved(self):
        raw, payload = fixture('ref2va', words=('温柔',))
        delivery = '<Subject 1>，音色描述：温柔；自然清楚地回应'
        raw['dialogue'][0]['delivery'] = delivery
        result = prompts.compile_written(raw, payload)
        self.assertEqual(result['dialogue'][0]['delivery'], delivery)
        self.assertIn(delivery, result['prompt'])
        self.assertFalse(self.adjustments(result, 'dialogue_text_deduplicated'))
        self.assertTrue(self.adjustments(result, 'dialogue_text_overlap_warning'))

    def test_quoted_voice_attribute_is_also_preserved(self):
        raw, payload = fixture('ref2va', words=('温柔',))
        delivery = '<Subject 1>，音色描述：“温柔”；自然回应'
        raw['dialogue'][0]['delivery'] = delivery
        result = prompts.compile_written(raw, payload)
        self.assertEqual(result['dialogue'][0]['delivery'], delivery)

    def test_visible_written_words_are_preserved_including_single_character(self):
        raw, payload = fixture(words=('好。',))
        raw['integrated_multimodal_description'] += '纸上写着“好”，镜头停在书页。'
        result = prompts.compile_written(raw, payload)
        self.assertIn('纸上写着“好”', result['prompt'])
        self.assertTrue(self.adjustments(result, 'dialogue_text_overlap_warning'))
        self.assertFalse(self.adjustments(result, 'dialogue_text_deduplicated'))

    def test_quoted_object_name_without_speech_cue_is_preserved(self):
        raw, payload = fixture(words=('小船',))
        raw['integrated_multimodal_description'] += '她举起“小船”模型，随后转向同伴。'
        before = copy.deepcopy(raw)
        result = prompts.compile_written(raw, payload)
        self.assertIn('她举起“小船”模型，随后转向同伴。', result['prompt'])
        self.assertEqual(raw, before)
        self.assertFalse(self.adjustments(result, 'dialogue_text_deduplicated'))
        self.assertTrue(self.adjustments(result, 'dialogue_text_overlap_warning'))

    def test_written_line_named_dialogue_does_not_count_as_speaking_verb(self):
        raw, payload = fixture(words=('我们出发吧。',))
        raw['integrated_multimodal_description'] += '纸上写着台词：“我们出发吧。”林舟把纸放下。'
        before = copy.deepcopy(raw)
        result = prompts.compile_written(raw, payload)
        self.assertIn('纸上写着台词：“我们出发吧。”林舟把纸放下。', result['prompt'])
        self.assertEqual(raw, before)
        self.assertFalse(self.adjustments(result, 'dialogue_text_deduplicated'))
        self.assertTrue(self.adjustments(result, 'dialogue_text_overlap_warning'))

    def test_repeated_utterance_multiple_speakers_preserves_all_fixed_dialogue(self):
        raw, payload = fixture(words=('好。', '好。'))
        raw['integrated_multimodal_description'] += '林舟说：“好！”陆青回答：“好。”随后两人转身。'
        result = prompts.compile_written(raw, payload)
        self.assertEqual(self.spoken(result), ['好。', '好。'])
        self.assertEqual([row['speaker'] for row in result['dialogue']], ['林舟', '陆青'])
        self.assertEqual([(row['start'], row['end']) for row in result['dialogue']], [(1, 4), (6, 9)])
        removed = self.adjustments(result, 'dialogue_text_deduplicated')
        self.assertEqual(sum(row['count'] for row in removed), 2)
        self.assertEqual(removed[0]['matches'][0]['line_indices'], [0, 1])
        self.assertIn('随后两人转身', result['prompt'])

    def test_quote_only_delivery_gets_neutral_performance(self):
        for delivery in ('“我们出发吧。”', '<Subject 1>：“我们出发吧！”'):
            raw, payload = fixture('ref2va')
            raw['dialogue'][0]['delivery'] = delivery
            result = prompts.compile_written(raw, payload)
            self.assertIn('自然清楚地说话', result['dialogue'][0]['delivery'])
            if '<Subject' in delivery:
                self.assertIn('<Subject 1>', result['dialogue'][0]['delivery'])
            self.assertEqual(self.spoken(result), ['我们出发吧。'])

    def test_quote_only_sound_and_music_are_still_compilable(self):
        raw, payload = fixture()
        raw['overall_soundscape'] = '“我们出发吧。”'
        raw['non_diegetic_music'] = '“我们出发吧。”'
        result = prompts.compile_written(raw, payload)
        self.assertEqual(result['overall_soundscape'], raw['overall_soundscape'])
        self.assertEqual(result['non_diegetic_music'], raw['non_diegetic_music'])
        self.assertFalse(self.adjustments(result, 'dialogue_text_deduplicated'))
        self.assertEqual(len(self.adjustments(result, 'dialogue_text_overlap_warning')), 2)
        self.assertEqual(self.spoken(result), ['我们出发吧。'])

    def test_valid_fields_without_overlap_remain_verbatim(self):
        raw, payload = fixture()
        result = prompts.compile_written(raw, payload)
        for field in prompts.CONTENT_FIELDS:
            self.assertEqual(result[field], raw[field])
        self.assertEqual(result['dialogue'], raw['dialogue'])
        self.assertNotIn('automatic_adjustments', result)

    def test_unknown_reference_is_rejected_even_if_near_duplicate(self):
        raw, payload = fixture('ref2va')
        raw['dialogue'][0]['delivery'] = '<Subject 9>说：“我们出发吧。”'
        with self.assertRaisesRegex(ValueError, '未绑定素材'):
            prompts.compile_written(raw, payload)

    def test_unknown_speaker_and_changed_words_are_not_silently_repaired(self):
        for field, value in (('speaker', '未知人物'), ('text', '临时新台词。')):
            raw, payload = fixture()
            raw['dialogue'][0][field] = value
            with self.assertRaisesRegex(ValueError, '说话者、原字和顺序'):
                prompts.compile_written(raw, payload)

    def test_invalid_time_and_handwritten_tags_are_still_rejected(self):
        for mutation in ('time', 'body_tag', 'delivery_tag'):
            raw, payload = fixture()
            if mutation == 'time':
                raw['dialogue'][0]['start'] = -1
            elif mutation == 'body_tag':
                raw['integrated_multimodal_description'] += '<d>我们出发吧。</d>'
            else:
                raw['dialogue'][0]['delivery'] += '<d>我们出发吧。</d>'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                prompts.compile_written(raw, payload)


if __name__ == '__main__':
    unittest.main()
