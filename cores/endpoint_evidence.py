"""Bind dynamic continuity to one sampled endpoint; never infer an unseen action."""
import copy
import re

VERSION = 'explicit-final-frame-v1'
_UNKNOWN = {'', 'unknown', '未知', '无法确认', '不明', 'none', 'null'}
_STYLE = re.compile(r'实拍感|电影感|电影质感|画风|摄影风格|photorealistic|cinematic style', re.I)
_DYNAMIC = re.compile(r'站|坐|躺|卧|跪|持|拿|握|扶|倚|倒|门|窗|室|厅|街|山|海|桌|床|地面|standing|sitting|holding|lying|room|street', re.I)


def known(value):
    return isinstance(value, str) and value.strip().lower() not in _UNKNOWN


def request_contract(request, characters, frame_bindings):
    """The two images have different jobs; each dynamic record names the final one."""
    from . import wardrobe_continuity as wardrobe
    system, payload, schema = copy.deepcopy(request)
    frames = [row for row in frame_bindings if isinstance(row, dict)
              and type(row.get('frame_index')) is int]
    if not frames:
        return system, payload, schema
    final = max(frames, key=lambda row: row['frame_index'])
    final_index = final['frame_index']
    payload['endpoint_evidence'] = dict(version=VERSION, **final)
    people = wardrobe._people(characters)
    payload['endpoint_identity_bindings'] = list(people.values())
    properties = schema.setdefault('properties', {})
    properties['endpoint_frame_index'] = dict(type='integer', enum=[final_index])
    properties['endpoint_conflicts'] = dict(type='array', items=dict(type='string'))
    for key in ('endpoint_frame_index', 'endpoint_conflicts'):
        if key not in schema.setdefault('required', []):
            schema['required'].append(key)
    for field in ('characters', 'props'):
        item = properties.get(field, {}).get('items')
        if not isinstance(item, dict):
            continue
        item.setdefault('properties', {})['frame_index'] = dict(type='integer', enum=[final_index])
        if 'frame_index' not in item.setdefault('required', []):
            item['required'].append('frame_index')
        if field == 'characters':
            item['properties']['id'] = dict(type='string', enum=list(people) + ['unknown'])
    system += ('\n末态时间绑定：endpoint_frame_index及每条characters/props的frame_index均填写'
        'endpoint_evidence指定的真实末帧编号。只在该图核实姿势、位置、持物及可见性；'
        '首帧仅用于衣着前后比较。人物id从endpoint_identity_bindings复制，无法确认身份填unknown。'
        'actual_visible写末帧的动作空间事实，画风和实拍质感另有来源。'
        '摘要、人物字段、风险说明须指向同一时点；存在互相矛盾的证据时将相应字段填unknown，'
        '把矛盾写入endpoint_conflicts。未见的动作不填为已完成。')
    return system, payload, schema


def _pose(text):
    patterns = (('stand', r'站立|站在|站于|standing'), ('sit', r'坐着|坐在|坐姿|坐于|sitting|seated'),
                ('lie', r'躺|倒地|倒在|卧|lying'), ('kneel', r'跪|kneel'))
    found = {kind for kind, pattern in patterns if re.search(pattern, str(text), re.I)}
    return next(iter(found)) if len(found) == 1 else ''


def normalize(observation, characters, frames, *, single_frame=False):
    """Reject ambiguity instead of repairing a model observation with guessed facts."""
    from . import wardrobe_continuity as wardrobe
    result = copy.deepcopy(observation) if isinstance(observation, dict) else {}
    indexes = wardrobe._frame_ids(frames)
    final = max(indexes) if indexes else None
    scoped = final is not None and (single_frame or result.get('endpoint_frame_index') == final)
    conflicts = result.get('endpoint_conflicts') or []
    people = wardrobe._people(characters)
    risk_text = '。'.join(str(x) for x in result.get('continuity_risks') or [])
    endpoint_risks = '。'.join(part for part in re.split(r'[。；;\n]', risk_text)
                              if re.search(r'末帧|最后|结束|endpoint|final frame', part, re.I))
    summary = str(result.get('actual_visible') or '')
    mixed_summary = bool(re.search(r'图\s*[1-9]|首帧|第一帧', summary))
    valid_rows, rejected = [], False
    for row in result.get('characters') or []:
        if not isinstance(row, dict):
            continue
        identity = wardrobe._resolve(row.get('id') or row.get('character_id'), people)
        valid = scoped and not conflicts and identity and (single_frame or row.get('frame_index') == final)
        expected_pose = _pose(row.get('pose'))
        names = (identity, people.get(identity, {}).get('name'))
        for clause in re.split(r'[。；;\n]', summary + '。' + endpoint_risks):
            if not any(name and name in clause for name in names):
                continue
            reported_pose = _pose(clause)
            if expected_pose and reported_pose and expected_pose != reported_pose:
                valid = False
            if row.get('visibility') in ('visible', 'partial') and re.search(r'消失|未见|不在画面|出画|offscreen|not visible', clause, re.I):
                valid = False
        if not valid:
            rejected = True
            row.update(position='unknown', pose='unknown', hands='unknown', certainty='unknown')
            reason = '人物末态缺少一致的时间或身份依据：' + str(row.get('id') or 'unknown')
            if reason not in result.setdefault('unknowns', []):
                result['unknowns'].append(reason)
        elif identity:
            row['id'] = identity
        row['frame_index'] = final
        valid_rows.append(row)
    result['characters'] = valid_rows
    result['props'] = [row for row in result.get('props') or [] if isinstance(row, dict)
                       and scoped and not conflicts and (single_frame or row.get('frame_index') == final)]
    if not scoped or conflicts or rejected or mixed_summary or (_STYLE.search(summary) and not _DYNAMIC.search(summary)):
        result['actual_visible'] = 'unknown'
    if not scoped or conflicts:
        result['scene'] = 'unknown'
    result['actions'], result['completed'] = [], []
    result['endpoint_evidence'] = dict(version=VERSION, frame_index=final, verified=bool(scoped and not conflicts))
    return result


def start_state(state, bindings=()):
    """Short, current, visible facts only; no style, appearance or finished-event claim."""
    from . import wardrobe_continuity as wardrobe
    if not isinstance(state, dict) or state.get('status') == 'unavailable':
        return ''
    evidence = state.get('endpoint_evidence') or {}
    if evidence.get('version') != VERSION or evidence.get('verified') is not True:
        return ''
    people = wardrobe._people(bindings)
    parts = []
    for row in state.get('characters') or []:
        if (not isinstance(row, dict) or row.get('certainty') != 'observed'
                or row.get('visibility') not in ('visible', 'partial')
                or row.get('frame_index') != evidence.get('frame_index')):
            continue
        identity = row.get('id') or row.get('character_id')
        if people and identity not in people:
            continue
        facts = [str(row[key]).strip() for key in ('position', 'pose', 'hands') if known(row.get(key))]
        if facts:
            name = people.get(identity, {}).get('name') or identity
            parts.append(str(name) + '：' + '，'.join(dict.fromkeys(facts)))
    for row in state.get('props') or []:
        if (isinstance(row, dict) and row.get('certainty') == 'observed'
                and row.get('visibility') in ('visible', 'partial')
                and row.get('frame_index') == evidence.get('frame_index')):
            facts = [str(row[key]).strip() for key in ('holder', 'location') if known(row.get(key))]
            if known(row.get('id')) and facts:
                parts.append(row['id'] + '：' + '，'.join(facts))
    # An empty shot can still have a proven location, but a style summary cannot.
    scene = state.get('scene')
    if not parts and not state.get('characters') and known(scene) and not _STYLE.search(scene):
        parts.append('场景：' + scene.strip())
    return '；'.join(parts)
