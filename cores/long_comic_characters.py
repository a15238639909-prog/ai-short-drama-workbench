"""Read main-project character cards without changing their files."""
import copy
import hashlib
import json
from pathlib import Path
import re


DATA_ROOT = Path(__file__).resolve().parents[1] / 'data'


def _read(path, root):
    path = path.resolve()
    if not root.resolve().is_relative_to(DATA_ROOT.resolve()) or not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('来源项目或人物卡不存在，原文件未修改')
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ValueError('来源项目或人物卡无法读取') from error
    if not isinstance(result, dict):
        raise ValueError('来源项目或人物卡格式无效')
    return result


def _story(story_id):
    if not isinstance(story_id, str) or not re.fullmatch(r'STORY_\d+', story_id):
        raise ValueError('来源项目ID无效')
    story = _read(DATA_ROOT / 'stories' / (story_id + '.json'), DATA_ROOT / 'stories')
    if story.get('story_id') != story_id or not isinstance(story.get('character_ids'), list):
        raise ValueError('来源项目人物清单无效')
    return story


def _card(story, character_id):
    if not isinstance(character_id, str) or character_id not in story['character_ids']:
        raise ValueError('所选人物不属于来源项目')
    if not re.fullmatch(r'[A-Za-z0-9_\u4e00-\u9fff-]{1,160}', character_id):
        raise ValueError('来源人物ID无效')
    root = DATA_ROOT / 'assets' / story['story_id'] / 'characters'
    card = _read(root / (character_id + '.json'), root)
    if card.get('character_id') != character_id or card.get('story_id') not in (None, story['story_id']):
        raise ValueError('来源人物卡归属不一致')
    return card


def _text(value):
    if isinstance(value, str):
        return value.strip()
    return str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else ''


def _mapped(card):
    appearance = _text(card.get('appearance_details')) or '；'.join(dict.fromkeys(
        value for value in (_text(card.get('look')), _text(card.get('other'))) if value))
    details = []
    for label, key in (('年龄', 'age'), ('性别', 'sex'), ('身高', 'height'), ('人物类型', 'char_type'),
                       ('脸型', 'face_type'), ('身材', 'build'), ('发型', 'hair')):
        value = _text(card.get(key))
        if value:
            details.append(label + '：' + value)
    if appearance:
        details.append('外貌与识别特征：' + appearance)
    face = card.get('face_features')
    if isinstance(face, dict):
        details.extend(str(key) + '：' + value for key, raw in face.items() if (value := _text(raw)))
    return dict(character_id=card['character_id'], name=_text(card.get('name')),
        appearance='；'.join(details) or _text(card.get('identity_anchor')),
        clothing=_text(card.get('clothing')),
        personality=_text(card.get('behavior_anchor')) or _text(card.get('personality')),
        updated=card.get('updated'), edited_by_user=bool(card.get('edited_by_user')))


def list_character_sources():
    root = DATA_ROOT / 'stories'
    if not root.is_dir():
        return []
    result = []
    for path in sorted(root.glob('STORY_*.json')):
        if not re.fullmatch(r'STORY_\d+', path.stem):
            continue
        try:
            story = _story(path.stem)
        except ValueError:
            continue
        if story.get('archived'):
            continue
        result.append(dict(story_id=story['story_id'], title=_text(story.get('title')) or story['story_id'],
                           character_count=len(story['character_ids'])))
    return result


def get_source_characters(story_id):
    story = _story(story_id)
    return [_mapped(_card(story, identity)) for identity in story['character_ids']]


def import_characters(story_id, character_ids):
    story = _story(story_id)
    if (not isinstance(character_ids, list) or not 1 <= len(character_ids) <= 12
            or not all(isinstance(identity, str) for identity in character_ids)
            or len(set(character_ids)) != len(character_ids)):
        raise ValueError('请选择1至12个不重复的来源人物')
    characters, provenance = [], []
    for number, identity in enumerate(character_ids, 1):
        snapshot = _card(story, identity)
        mapped = _mapped(snapshot)
        comic_id = 'c%02d' % number
        characters.append(dict(id=comic_id, **{key: mapped[key] for key in ('name', 'appearance', 'clothing', 'personality')}))
        digest = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                          separators=(',', ':')).encode('utf-8')).hexdigest()
        provenance.append(dict(source_character_id=identity, comic_character_id=comic_id, hash=digest,
            updated=snapshot.get('updated'), version=snapshot.get('version'),
            edited_by_user=bool(snapshot.get('edited_by_user')), snapshot=copy.deepcopy(snapshot)))
    return characters, dict(story_id=story_id, characters=provenance)
