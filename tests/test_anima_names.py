"""Local catalog lookup tests; temporary fixture directories are retained."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from cores import anima_names as names


class AnimaNamesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_root = Path(tempfile.mkdtemp(prefix='anima_names_retained_'))
        cls.database = cls.fixture_root / 'characters.sqlite3'
        connection = sqlite3.connect(cls.database)
        connection.executescript('''
            CREATE TABLE characters (character_tag TEXT NOT NULL, series_tag TEXT NOT NULL,
                trigger TEXT NOT NULL, character_name TEXT NOT NULL, series_name TEXT NOT NULL,
                source TEXT NOT NULL, appearance TEXT, PRIMARY KEY(character_tag, series_tag));
            CREATE TABLE aliases (alias TEXT NOT NULL, character_tag TEXT NOT NULL,
                series_tag TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY(alias, character_tag, series_tag));
            CREATE INDEX character_name_lookup ON characters(character_name COLLATE NOCASE);
            CREATE INDEX series_name_lookup ON characters(series_name COLLATE NOCASE);
        ''')
        rows = [
            ('android_18', 'dragon_ball', 'android 18, dragon ball', 'Android 18', 'Dragon Ball'),
            ('son_goku', 'dragon_ball', 'son goku, dragon ball', 'Son Goku', 'Dragon Ball'),
            ('bulma', 'dragon_ball', 'bulma, dragon ball', 'Bulma', 'Dragon Ball'),
            ('alex_a', 'dragon_ball', 'alex a, dragon ball', 'Alex', 'Dragon Ball'),
            ('alex_b', 'other_series', 'alex b, other series', 'Alex', 'Other Series'),
            ('sakura_(naruto)', 'naruto', 'sakura (naruto), naruto', 'Sakura (Naruto)', 'Naruto'),
            ('sakura_(other)', 'other_series', 'sakura (other), other series', 'Sakura (Other)', 'Other Series'),
            ('standing', 'fixture', 'standing, fixture', 'standing', 'Fixture'),
            ('walking', 'fixture', 'walking, fixture', 'Walking', 'Fixture'),
            ('blue_sky', 'fixture', 'blue sky, fixture', 'Blue Sky', 'Fixture'),
        ]
        connection.executemany('INSERT INTO characters VALUES (?,?,?,?,?,?,?)',
                               [(*row, 'fixture', 'must not be returned') for row in rows])
        aliases = [('18号', 'android_18'), ('人造人18号', 'android_18'), ('人造人十八号', 'android_18'),
                   ('十八号', 'android_18'), ('卡卡罗特', 'son_goku'), ('孙悟空', 'son_goku'),
                   ('悟空', 'son_goku'), ('布尔玛', 'bulma'), ('布玛', 'bulma'), ('布玛尔', 'bulma')]
        connection.executemany('INSERT INTO aliases VALUES (?,?,?,?)',
                               [(alias, character, 'dragon_ball', 'user_context_and_live_identity') for alias, character in aliases])
        connection.commit()
        connection.close()

    def resolve(self, text):
        return names.resolve(text, self.database)

    def test_chinese_names_and_explicit_series_resolve_in_input_order(self):
        records = self.resolve('七龙珠18号、悟空和布玛在街边。')
        self.assertEqual([row['character_tag'] for row in records], ['android_18', 'son_goku', 'bulma'])
        self.assertTrue(all(set(row) == set(names.FIELDS) for row in records))

    def test_long_aliases_cover_short_matches_and_repeated_identities(self):
        records = self.resolve('龙珠人造人十八号、孙悟空与悟空、布玛尔和布玛。')
        self.assertEqual([row['character_tag'] for row in records], ['android_18', 'son_goku', 'bulma'])

    def test_number_boundary_unknown_chinese_and_empty_have_no_match(self):
        for text in ('118号在街上', '未知的中文人物在街上', '十八在街上', ''):
            with self.subTest(text=text):
                self.assertEqual(self.resolve(text), [])

    def test_english_exact_names_tags_and_proper_name_prose(self):
        for text, expected in (('son goku', ['son_goku']), ('ANDROID_18', ['android_18']),
                               ('Bulma walks beside Son Goku.', ['bulma', 'son_goku']),
                               ('Bulma站在窗边。', ['bulma'])):
            with self.subTest(text=text):
                self.assertEqual([row['character_tag'] for row in self.resolve(text)], expected)

    def test_substrings_artist_tags_and_ordinary_actions_are_not_characters(self):
        for text in ('bulmaria', 'Android 180', '@Bulma', 'walking, standing, blue sky',
                     'Walking by the window beneath a Blue Sky.'):
            with self.subTest(text=text):
                self.assertEqual(self.resolve(text), [])

    def test_same_name_requires_series_and_resolves_with_explicit_series(self):
        with self.assertRaisesRegex(ValueError, '多个同名'):
            self.resolve('Alex')
        self.assertEqual(self.resolve('龙珠Alex')[0]['character_tag'], 'alex_a')
        self.assertEqual(self.resolve('Alex, Other Series')[0]['character_tag'], 'alex_b')

    def test_qualified_names_are_not_stripped_before_disambiguation(self):
        with self.assertRaisesRegex(ValueError, '多个同名'):
            self.resolve('Sakura')
        self.assertEqual(self.resolve('Sakura, Naruto')[0]['character_tag'], 'sakura_(naruto)')
        self.assertEqual(self.resolve('Sakura (Other)')[0]['character_tag'], 'sakura_(other)')

    def test_conflicting_work_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '作品限定冲突'):
            self.resolve('Naruto, 悟空')

    def test_missing_or_corrupt_database_is_not_created_or_replaced(self):
        missing = self.fixture_root / 'missing.sqlite3'
        with self.assertRaisesRegex(ValueError, '不存在'):
            names.resolve('悟空', missing)
        self.assertFalse(missing.exists())
        corrupt = self.fixture_root / 'corrupt.sqlite3'
        corrupt.write_bytes(b'not a database')
        with self.assertRaisesRegex(ValueError, '无法读取'):
            names.resolve('悟空', corrupt)
        self.assertEqual(corrupt.read_bytes(), b'not a database')

    def test_lookup_is_read_only_and_leaves_database_bytes_unchanged(self):
        before = self.database.read_bytes()
        self.resolve('七龙珠18号、Bulma')
        self.assertEqual(self.database.read_bytes(), before)

    def test_output_identity_accepts_case_and_underscores_without_changing_body(self):
        records = self.resolve('七龙珠18号、悟空和布玛')
        output = 'ANDROID_18, SON_GOKU, Bulma, DRAGON_BALL, blue coat, standing near a window'
        names.validate_output(records, output)
        self.assertEqual(output, 'ANDROID_18, SON_GOKU, Bulma, DRAGON_BALL, blue coat, standing near a window')

    def test_output_missing_character_work_or_exact_boundary_is_rejected(self):
        record = self.resolve('七龙珠18号')
        for output in ('android 18, blue coat', 'android 180, dragon ball', 'bulma, dragon ball'):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, '缺少已确认身份标签'):
                names.validate_output(record, output)


if __name__ == '__main__':
    unittest.main()
