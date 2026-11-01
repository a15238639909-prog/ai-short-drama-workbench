"""Adjacent literary dialogue attribution, using synthetic text only.

No models, media, saved projects, or temporary-file cleanup are involved.
"""
import os
from pathlib import Path
import sys
import unittest

os.environ['V41_NO_MODEL'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cores import dialogue_ledger as ledger


PEOPLE = ('赵禾', '钱川', '孙宁')


class AdjacentSpeechAttributionTests(unittest.TestCase):
    def spoken(self, text, people=PEOPLE):
        return ledger.spoken(ledger.ledger(text, people))

    def test_three_people_speak_in_sequence_on_one_line_or_separate_lines(self):
        for separator in ('', ' ', '\n'):
            with self.subTest(separator=repr(separator)):
                source = separator.join(('赵禾说：“到齐了。”', '钱川说：“可以开始。”',
                                         '孙宁说：“我来记录。”'))
                rows = self.spoken(source)
                self.assertEqual([(row['speaker'], row['text']) for row in rows],
                                 [('赵禾', '到齐了。'), ('钱川', '可以开始。'),
                                  ('孙宁', '我来记录。')])
                self.assertTrue(all(row['confidence'] == '确定' for row in rows))

    def test_two_people_say_the_same_words_without_merging_their_identities(self):
        for separator in ('', '\n'):
            with self.subTest(separator=repr(separator)):
                rows = self.spoken('赵禾举起一张纸说：“好了。”' + separator +
                                   '钱川放下笔说：“好了。”')
                self.assertEqual([row['speaker'] for row in rows], ['赵禾', '钱川'])
                self.assertEqual([row['count'] for row in rows], [2, 2])

    def test_next_quote_intro_accepts_colon_comma_or_no_separator(self):
        for marker in ('：', ':', '，', ',', ''):
            with self.subTest(marker=marker):
                rows = self.spoken('赵禾说：“先核对。”钱川说' + marker + '“我来记录。”')
                self.assertEqual([row['speaker'] for row in rows], ['赵禾', '钱川'])

    def test_next_bare_name_colon_is_not_previous_quote_attribution(self):
        rows = self.spoken('赵禾：“先核对。”钱川：“我来记录。”')
        self.assertEqual([row['speaker'] for row in rows], ['赵禾', '钱川'])

    def test_next_speaker_does_not_resolve_an_earlier_unknown_quote(self):
        rows = self.spoken('“先核对。”钱川说：“我来记录。”')
        self.assertEqual([row['speaker'] for row in rows], ['', '钱川'])
        self.assertEqual(rows[0]['confidence'], '存疑')

    def test_real_conflicting_prefix_and_suffix_remain_unresolved(self):
        for source in ('赵禾说：“先核对。”钱川说。', '赵禾：“先核对。”钱川回答。'):
            with self.subTest(source=source):
                row = self.spoken(source)[0]
                self.assertEqual(row['speaker'], '')
                self.assertIn('前后依据不一致', row['evidence'])

    def test_true_postposed_speaker_is_preserved(self):
        for source in ('“先核对。”赵禾说。', '“先核对。”，赵禾回答。',
                       '“先核对。”赵禾说。钱川说：“我来记录。”'):
            with self.subTest(source=source):
                self.assertEqual(self.spoken(source)[0]['speaker'], '赵禾')

    def test_a_true_suffix_before_another_quote_intro_still_belongs_to_this_quote(self):
        rows = self.spoken('“先核对。”赵禾说，钱川说：“我来记录。”')
        self.assertEqual([row['speaker'] for row in rows], ['赵禾', '钱川'])

    def test_pronouns_and_missing_attribution_stay_unknown(self):
        for source in ('她说：“先核对。”', '“先核对。”',
                       '“先核对。”她说：“我来记录。”'):
            with self.subTest(source=source):
                self.assertEqual(self.spoken(source)[0]['speaker'], '')

    def test_named_speakers_work_without_a_character_table(self):
        rows = self.spoken('赵禾说：“先核对。”钱川说：“我来记录。”', people=())
        self.assertEqual([row['speaker'] for row in rows], ['赵禾', '钱川'])

    def test_written_words_and_inner_thought_are_not_current_speech(self):
        for source, kind in (
                ('赵禾在纸牌上写着“禁止入内”。', ledger.KIND_SIGNAGE),
                ('赵禾心想“先核对数据”，随后整理文件。', ledger.KIND_UNSPOKEN)):
            with self.subTest(source=source):
                rows = ledger.ledger(source, PEOPLE)
                self.assertEqual([row['kind'] for row in rows], [kind])
                self.assertEqual(ledger.spoken(rows), [])


if __name__ == '__main__':
    unittest.main()
