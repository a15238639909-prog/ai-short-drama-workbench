"""Rebind proven image identities to user-selected slots; no story/model mutation."""
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
VERSION = 'explicit-reference-bindings-v1'
PICTURE = re.compile(r'<Picture\s+(\d+)>')
FIELDS = ('subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
          'overall_soundscape', 'non_diegetic_music')


def _path(value):
    return os.path.normcase(os.path.abspath(str(value)))


def adopted_catalog(sid):
    """Keep owner identity even when a character and a set share a display name."""
    from . import asset_core
    adopted = {}
    for visual in asset_core.list_assets(sid, 'visuals') or []:
        if visual.get('status') != 'adopted' or not visual.get('path'):
            continue
        raw = Path(str(visual['path']))
        candidates = [raw] if raw.is_absolute() else [ROOT / 'outputs' / raw, ROOT / raw]
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is not None:
            adopted.setdefault(str(visual.get('owner_id') or ''), str(path))
    catalog = []
    for kind, key, role in (('characters', 'character_id', 'character'), ('scenes', 'scene_id', 'scene')):
        for card in asset_core.list_assets(sid, kind) or []:
            identity, name = str(card.get(key) or ''), str(card.get('name') or '')
            if identity and name:
                catalog.append(dict(owner_id=identity, name=name, role=role, path=adopted.get(identity)))
    return catalog


def _definition_lines(prompt):
    headers = list(re.finditer(r'(?m)^(' + '|'.join(FIELDS) + r'):[ \t]*', prompt))
    if headers:
        first = next((index for index, match in enumerate(headers) if match[1] == 'subject_definitions'), None)
        if first is None:
            return []
        start = headers[first].end()
        end = headers[first + 1].start() if first + 1 < len(headers) else len(prompt)
        prompt = prompt[start:end]
    return [line.strip() for line in prompt.splitlines()
            if re.match(r'^\s*<Subject\s*\d+>', line) and '<d>' not in line]


def _picture_for(line, asset, unique_name):
    name = re.escape(asset['name'])
    subject = r'^<Subject\s*\d+>\s*'
    picture = r'<Picture\s+(\d+)>'
    patterns = []
    if asset['role'] == 'character':
        patterns.extend((subject + name + r'是\s*' + picture,
                         subject + r'is\s+' + name + r',\s*the character from\s+' + picture))
    else:
        patterns.extend((subject + r'是\s*' + picture + r'\s*' + name + r'场景(?=[，,。;；\s]|$)',
                         subject + r'is\s+' + name + r',\s*the environment from\s+' + picture))
    if unique_name:
        patterns.append(subject + r'is\s+' + name + r'\s+from\s+' + picture)
    for pattern in patterns:
        match = re.match(pattern, line)
        if match:
            return int(match[1])
    return None


def rebind_prompt(prompt, images, default_images, catalog):
    """Use only explicit known definitions; unknown custom prose stays untouched.

    A same-slot custom request needs no name parser. For a changed selection,
    every remapped Picture must have an exact asset identity in a standard
    definition. All replacements happen in one pass so swaps cannot cascade.
    """
    audit = dict(version=VERSION, status='unchanged', bindings=[])
    if [_path(path) for path in images] == [_path(path) for path in default_images]:
        return prompt, audit
    lines = _definition_lines(prompt)
    name_counts = {row['name']: sum(item['name'] == row['name'] for item in catalog) for row in catalog}
    mapping, paths_by_picture = {}, {}
    for line in lines:
        for asset in catalog:
            old = _picture_for(line, asset, name_counts[asset['name']] == 1)
            if old is None:
                continue
            asset_path = _path(asset['path']) if asset.get('path') else None
            positions = [index for index, path in enumerate(images, 1) if _path(path) == asset_path]
            if not positions:
                raise ValueError('自选参考图缺少提示词已绑定的%s「%s」，已保留分镜，未提交视频' %
                                 ('人物' if asset['role'] == 'character' else '场景', asset['name']))
            # Duplicate selections of the identical file are equivalent image
            # evidence; use its first actual slot without dropping user images.
            new = positions[0]
            if old in paths_by_picture and paths_by_picture[old] != asset_path:
                raise ValueError('提示词同一图片编号绑定了不同素材，已保留分镜，未提交视频')
            paths_by_picture[old], mapping[old] = asset_path, new
            audit['bindings'].append(dict(owner_id=asset['owner_id'], name=asset['name'], role=asset['role'],
                original_picture=old, submitted_picture=new))
    referenced = set(map(int, PICTURE.findall(prompt)))
    if not referenced or not referenced <= set(mapping):
        audit.update(status='unverified_custom', reason='自定义引用未能全部关联到明确素材，保留用户原提示词')
        return prompt, audit
    updated = PICTURE.sub(lambda match: '<Picture %d>' % mapping[int(match[1])], prompt)
    audit['status'] = 'rebound' if updated != prompt else 'confirmed'
    return updated, audit
