"""Resolve confirmed Anima identities from a local read-only catalog."""
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import unicodedata


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / 'data' / 'anima_names' / '20261026' / 'characters.sqlite3'
FIELDS = ('character_tag', 'series_tag', 'trigger', 'character_name', 'series_name')
SERIES_ALIASES = {'七龙珠': 'dragon_ball', '龙珠': 'dragon_ball'}
_COLUMNS = ', '.join(FIELDS)
_ORDINARY_WORDS = frozenset(('a an the and or of from with in on at by for to is are '
    'adult boy girl man woman person character scene portrait front back side left right '
    'view panel full body face hair eyes blue red green black white sky sun light sunlight '
    'sunset dawn night forest tree stone bridge window background').split())
_ACTION_WORDS = frozenset(('stand standing walk walking run running sit sitting smile smiling '
    'wear wearing hold holding look looking poses pose facing illuminated lighting').split())
_LATIN_FRAGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_'’(). \-]*")
_TITLE_NAME = re.compile(r"(?<![A-Za-z0-9_@])(?:[A-Z][A-Za-z'’\-]*|\d+)(?:[ \t]+(?:[A-Z][A-Za-z'’\-]*|\d+))*(?![A-Za-z0-9_])")


def _normalized(value):
    return unicodedata.normalize('NFKC', value)


def _boundary_pattern(value):
    before = r'(?<![A-Za-z0-9_@])' if re.match(r'[A-Za-z0-9_]', value) else ''
    after = r'(?![A-Za-z0-9_])' if re.search(r'[A-Za-z0-9_]$', value) else ''
    return before + re.escape(value) + after


def _name_candidates(text):
    result = set()
    for pattern in (_LATIN_FRAGMENT, _TITLE_NAME):
        for match in pattern.finditer(text):
            raw = match.group()
            value = raw.strip(' .-')
            words = re.findall(r'[a-z]+', value.casefold().replace('_', ' '))
            if not words or all(word in _ORDINARY_WORDS for word in words) or any(word in _ACTION_WORDS for word in words):
                continue
            if match.start() and re.match(r'[A-Za-z0-9_@]', text[match.start() - 1]):
                continue
            start = match.start() + len(raw) - len(raw.lstrip(' .-'))
            result.add((start, start + len(value), value))
    return sorted(result, key=lambda item: (item[0], -len(item[2])))


def _record(row):
    result = {key: row[key] for key in FIELDS}
    if any(not isinstance(value, str) or not value.strip() for value in result.values()):
        raise ValueError('Anima名字库的身份记录不完整，请检查本地数据')
    return result


def _english_matches(connection, value):
    display = value.replace('_', ' ')
    rows = connection.execute('SELECT ' + _COLUMNS + ' FROM characters WHERE character_name = ? COLLATE NOCASE',
                              (display,)).fetchall()
    if not rows:
        rows = connection.execute('SELECT ' + _COLUMNS + ' FROM characters WHERE character_tag = ?',
                                  (value.casefold().replace(' ', '_'),)).fetchall()
    if not rows:
        escaped = display.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        rows = connection.execute('SELECT ' + _COLUMNS + " FROM characters WHERE character_name LIKE ? ESCAPE '\\'",
                                  (escaped + ' (%',)).fetchall()
    return [_record(row) for row in rows]


def resolve(text, database=None):
    if not isinstance(text, str):
        raise ValueError('Anima人物需求必须为文字')
    text = _normalized(text).strip()
    if not text:
        return []
    path = Path(DATABASE if database is None else database).resolve()
    if not path.is_file():
        raise ValueError('Anima本地名字库不存在，请先确认名字库文件')
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
            connection.execute('PRAGMA query_only=ON')
            connection.row_factory = sqlite3.Row
            aliases = connection.execute('SELECT alias, ' + ', '.join('characters.' + key for key in FIELDS) +
                ' FROM aliases JOIN characters USING (character_tag, series_tag)').fetchall()
            matches = {}
            for row in aliases:
                alias = _normalized(row['alias'])
                if not alias or not re.search(r'[\u3400-\u9fff]', alias):
                    continue
                for match in re.finditer(_boundary_pattern(alias), text, re.I):
                    matches.setdefault((match.start(), match.end(), alias), []).append(_record(row))
            series = {tag for alias, tag in SERIES_ALIASES.items() if alias in text}
            for start, end, value in _name_candidates(text):
                rows = _english_matches(connection, value)
                if rows:
                    matches.setdefault((start, end, value), []).extend(rows)
                series.update(row['series_tag'] for row in connection.execute(
                    'SELECT DISTINCT series_tag FROM characters WHERE series_name = ? COLLATE NOCASE',
                    (value.replace('_', ' '),)))
            chosen, occupied, identities = [], [], set()
            for (start, end, label), candidates in sorted(matches.items(), key=lambda item: (-(item[0][1] - item[0][0]), item[0][0])):
                if any(start < other_end and end > other_start for other_start, other_end in occupied):
                    continue
                unique = {(row['character_tag'], row['series_tag']): row for row in candidates}
                if series:
                    unique = {identity: row for identity, row in unique.items() if row['series_tag'] in series}
                    if not unique:
                        raise ValueError('人物“%s”与输入中的作品限定冲突，请确认作品和人物名' % label)
                if len(unique) != 1:
                    raise ValueError('人物“%s”有多个同名记录，请补充作品名或完整英文人物标签' % label)
                identity, record = next(iter(unique.items()))
                occupied.append((start, end))
                if identity not in identities:
                    chosen.append((start, record))
                    identities.add(identity)
            return [record for start, record in sorted(chosen, key=lambda item: item[0])]
    except sqlite3.Error as error:
        raise ValueError('Anima本地名字库无法读取，请检查文件和表结构：' + str(error)) from error


def validate_output(records, output):
    if not isinstance(output, str):
        raise ValueError('Anima写词结果必须为文字')
    normalized = re.sub(r'\s+', ' ', _normalized(output).replace('_', ' ')).casefold()
    for record in records:
        trigger = record.get('trigger')
        if not isinstance(trigger, str) or not trigger.strip():
            raise ValueError('Anima已确认身份缺少trigger，不能核验结果')
        for part in re.split(r'[,，]', trigger):
            token = re.sub(r'\s+', ' ', _normalized(part).replace('_', ' ')).strip().casefold()
            if token and not re.search(_boundary_pattern(token), normalized):
                raise ValueError('Anima写词缺少已确认身份标签“%s”，请重新生成，未自动修改正文' % part.strip())
