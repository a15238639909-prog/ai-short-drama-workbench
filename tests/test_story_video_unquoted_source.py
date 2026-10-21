"""Source-only, zero-model regression for opt-in continuation evidence."""
import copy
import hashlib
import unittest

from cores import episode_contract
from cores import story_video_characters as characters


def cards(names=('阿澈', '星砂')):
    return [dict(id='person_%d' % index, name=name, image_path='fixture_%d.png' % index,
                 personality='关心同伴', voice='清朗自然的声音')
            for index, name in enumerate(names, 1)]


class UnquotedSourceTests(unittest.TestCase):
    def test_strict_default_is_unchanged(self):
        source = '阿澈走来，说路上小心，然后转身出门。星砂笑着说已经准备好了。'
        contract = characters.prepare_source_contract(source, cards())
        self.assertEqual(contract['dialogue'], [])
        self.assertNotIn('evidence_status', contract)
        with self.assertRaises(episode_contract.ContractError):
            characters.prepare_source_contract('她说：“门锁住了。”', cards())

    def test_named_subject_and_same_subject_action_keep_exact_spans(self):
        source = '阿澈走来，说路上小心，然后转身出门。星砂笑着说已经准备好了。'
        _, _, raw_report = episode_contract.prepare_source(source,
            [{'name': row['name']} for row in cards()], split_long_dialogue=False)
        snapshot = copy.deepcopy(raw_report)
        contract = characters.prepare_source_contract(source, cards(), strict=False)
        self.assertEqual([(row['speaker_id'], row['text']) for row in contract['dialogue']],
                         [('person_1', '路上小心'), ('person_2', '已经准备好了')])
        report = contract['source_report']
        self.assertEqual(report['original_classifier_report'], snapshot)
        self.assertEqual(raw_report, snapshot)
        self.assertEqual(report['source_original'], source)
        self.assertEqual(report['source_digest'], hashlib.sha256(source.encode('utf-8')).hexdigest())
        self.assertEqual(contract['evidence_status'], 'verified')
        self.assertEqual(contract['unresolved_sources'], [])
        for row in report['unquoted_attribution_audit']:
            self.assertEqual(source[row['text_start']:row['text_end']], row['text'])
            self.assertEqual(source[row['source_start']:row['source_end']], row['evidence'])
        self.assertTrue(contract['numbered_source'].startswith(source))
        self.assertIn('【D1】阿澈：路上小心', contract['numbered_source'])

    def test_mixed_quoted_and_unquoted_dialogue_retains_source_order(self):
        source = '阿澈说出发。星砂说：“等等。”阿澈笑着说已经准备好了。'
        contract = characters.prepare_source_contract(source, cards(), strict=False)
        self.assertEqual([(row['speaker'], row['text']) for row in contract['dialogue']],
                         [('阿澈', '出发'), ('星砂', '等等。'), ('阿澈', '已经准备好了')])
        self.assertEqual(len(contract['source_report']['unquoted_attribution_audit']), 2)

    def test_explicit_script_is_not_extracted_twice(self):
        source = '阿澈：我说出发。\n星砂说已经准备好了。'
        contract = characters.prepare_source_contract(source, cards(), strict=False)
        self.assertEqual([(row['speaker'], row['text']) for row in contract['dialogue']],
                         [('阿澈', '我说出发。'), ('星砂', '已经准备好了')])

    def test_unknown_quoted_speaker_keeps_evidence_and_never_guesses(self):
        source = '她说：“门锁住了。”阿澈说走另一边。'
        contract = characters.prepare_source_contract(source, cards(), strict=False)
        self.assertEqual(contract['dialogue'],
                         [dict(speaker_id='person_1', speaker='阿澈', text='走另一边')])
        self.assertEqual(contract['evidence_status'], 'partially_unavailable')
        self.assertTrue(contract['unresolved_sources'])
        self.assertEqual(contract['source_report']['source_original'], source)
        self.assertTrue(contract['source_report']['issues'])

    def test_uncertain_pronoun_never_inherits_named_object(self):
        for source in ('她说门锁住了。', '阿澈看着星砂的手，她轻声说门锁住了。',
                       '陌生人说门锁住了。'):
            with self.subTest(source=source):
                contract = characters.prepare_source_contract(source, cards(), strict=False)
                self.assertEqual(contract['dialogue'], [])
                self.assertEqual(contract['evidence_status'], 'partially_unavailable')
                self.assertTrue(contract['unresolved_sources'])

    def test_writing_thought_negation_future_and_conditional_are_not_speech(self):
        for source in ('纸上写着阿澈说出发。', '阿澈在纸上写下说出发。',
                       '阿澈心里说出发。', '阿澈没有说出发。', '阿澈不会说出发。',
                       '阿澈准备说出发。', '阿澈正要说出发。', '阿澈明天说出发。',
                       '如果阿澈说出发。', '阿澈说了一个故事。', '阿澈说话声音很低。', '阿澈回答问题。'):
            with self.subTest(source=source):
                contract = characters.prepare_source_contract(source, cards(), strict=False)
                self.assertEqual(contract['dialogue'], [])
                self.assertEqual(contract['source_report']['source_original'], source)

    def test_clause_boundary_and_speech_word_inside_line_do_not_add_dialogue(self):
        source = '阿澈说你说什么，然后转身出门。星砂说今天不走。'
        contract = characters.prepare_source_contract(source, cards(), strict=False)
        self.assertEqual([row['text'] for row in contract['dialogue']], ['你说什么', '今天不走'])
        self.assertNotIn('然后转身出门', str(contract['dialogue']))

    def test_character_name_is_not_negation_and_tomorrow_can_be_spoken(self):
        contract = characters.prepare_source_contract('未央说今天不走。星砂说明天再来。',
                                                     cards(('未央', '星砂')), strict=False)
        self.assertEqual([row['text'] for row in contract['dialogue']], ['今天不走', '明天再来'])

    def test_no_implicit_first_person_voice_and_card_errors_still_fail(self):
        contract = characters.prepare_source_contract('我说出发。', cards(), strict=False)
        self.assertEqual(contract['dialogue'], [])
        with self.assertRaises(ValueError):
            characters.prepare_source_contract('阿澈说出发。', [], strict=False)


if __name__ == '__main__':
    unittest.main()
