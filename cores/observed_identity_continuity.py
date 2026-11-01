"""Separate observed motion from character design without rewriting source events."""
import copy
import re


POLICY = 'source-and-reference-identity-v1'
RULES = (
    '身份与外观权威：canonical_references绑定稳定人物身份、脸与外观基准。'
    'actual_state仅证明实际位置、朝向、姿势、持物、环境和动作进度；观测中的错脸、发色、'
    '发型或衣着漂移不能变成新人设。observation_id只是观察编号，不等于本段Subject编号，'
    '只有明确character_id才能直接绑定人物，不因编号相同冒认身份。'
    '原文明确且已经发生的换装、发型或发色变化继续有效，不能一律恢复参考图旧衣或旧发型；'
    'appearance_source_evidence是前段原文证据，须结合当前完整原文判断其实际时序。'
    '每条证据只对active_attributes列出的属性仍有效，原句中已被后续事件替代的旧属性不得恢复。'
    '对白请求、否定、回忆及尚未发生的计划不是已完成变化；本段新变化按原文动作拍出，'
    '不能提前套到开场。没有原文变化依据时遵循对应人设图。'
    '人物参考不恢复旧站姿或旧持物，真实承托、手中物、位置与已完成动作仍须承接。')

_GARMENTS = {
    'outfit': r'连衣裙|礼服|制服|套装|长袍|一身衣服|衣服|衣物|服装',
    'outerwear': r'外套|大衣|夹克|风衣|披风|斗篷|马甲',
    'top': r'上衣|衬衫|衬衣|T恤|毛衣|卫衣|背心',
    'bottom': r'长裤|短裤|裤子|裙子|长裙|短裙|裙',
    'shoes': r'鞋子|皮鞋|运动鞋|鞋|靴子|长靴|靴',
    'headwear': r'帽子|头盔|帽|面具',
    'scarf': r'围巾|领带',
    'gloves': r'手套',
    'necklace': r'项链|吊坠',
    'earrings': r'耳环|耳饰',
    'belt': r'腰带',
}
_GARMENT = '(?:' + '|'.join(_GARMENTS.values()) + ')'
_ATTIRE_MODIFIER = (
    r'(?:一件|一条|一套|一身|了|的|深色|浅色|暗色|亮色|彩色|'
    r'(?:深|浅|亮|暗|淡)?[黑白蓝红绿紫银金灰棕褐黄橙青粉米]色?|'
    r'丝质|丝绸|棉质|棉布|皮革|皮质|缎面|亚麻|羊毛|针织|绒面|'
    r'宽松|修身|紧身|短款|长款|高领|圆领|长袖|短袖|细肩带|无袖|普通|朴素|整洁|破旧|崭新|薄|厚|旧|新)')
_DESIGN_PHRASES = re.compile(
    r'(?:[黑白银金红棕褐紫蓝绿灰]{1,3}色?的?)(?:盘发|长发|短发|卷发|直发|头发|秀发|发)'
    r'(?:盘起|披散|扎起)?(?:清晰可见|明显)?|'
    r'(?:头发|秀发)(?:盘起|披散|扎起)|盘发(?:清晰可见|明显)?|'
    r'(?:身穿|身着|穿着)' + _ATTIRE_MODIFIER + r'*' + _GARMENT + r'|'
    r'(?:[黑白银金红棕褐紫蓝绿灰]{1,3}色?)?(?:衣着|服装|服饰)(?:明显|清晰可见)?|'
    r'(?:发型|发色|肤色|肤质|脸型|五官|面容|容貌|瞳色)(?:是|为|呈)?'
    r'(?:黑色|白色|银色|金色|棕色|苍白|白皙|黝黑|冷峻|精致|柔和)?|'
    r'\b(?:black|white|silver|blond|brown|red|grey|gray)(?:\s+(?:long|short|curly|straight))?\s+hair\b', re.I)
_WARDROBE_CHANGE = re.compile(r'换上|换成|换为|改穿|穿上|穿好|脱下|脱掉|脱去|披上|戴上|戴好|摘下|摘掉|取下|换装|更换衣服')
_HAIR_STYLE_CHANGE = re.compile(r'剪短|剪成|剪掉|剃光|剃短|扎成|扎起|盘起|盘成|解开发辫|解开马尾|披散头发')
_HAIR_COLOR_CHANGE = re.compile(r'染成|染为|染上|染发')
_NOT_OCCURRED = re.compile(r'没有|还没|尚未|并未|未曾|不曾|不要|不必|无需|打算|准备|计划|将要|'
                           r'下一场|下次|以后|如果|假如|想要|希望|回忆|记得|想起|请求|要求|应该|必须|不能|别')
MAX_SOURCE_EVIDENCE_CHARS = 4096


class AppearanceEvidenceLimitError(RuntimeError):
    code = 'source_appearance_context_limit'
    retryable = False


def _dynamic_text(value):
    if not isinstance(value, str):
        return copy.deepcopy(value)
    cleaned, count = _DESIGN_PHRASES.subn('', value)
    if not count:
        return value
    cleaned = re.sub(r'(^|[，,。；;\n])的(?=女子|女人|男性|女性|男子|男人|人物|角色)', r'\1', cleaned)
    cleaned = re.sub(r'[，,。；;\n]+(?=[，,。；;\n]|$)', '', cleaned)
    return cleaned.strip(' ，,。；;\n')


def _dynamic_value(value):
    if isinstance(value, list):
        return [_dynamic_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _dynamic_value(item) for key, item in value.items()}
    return _dynamic_text(value)


def dynamic_state(observation, references):
    observation = observation if isinstance(observation, dict) else {}
    aliases = {}
    for ref in references or []:
        if ref.get('role') != 'character':
            continue
        identity = ref.get('character_id') or ref.get('name')
        for alias in (identity, ref.get('name')):
            if alias:
                aliases[alias] = identity
    result = {}
    for key in ('actual_visible', 'scene', 'water', 'unknowns', 'continuity_risks',
                'beat_status', 'keep_end_seconds', 'keep_end_reason', 'missing'):
        if key in observation:
            result[key] = _dynamic_value(observation[key])
    for key in ('actions', 'completed'):
        if key in observation:
            result[key] = _dynamic_value(observation[key])
    for key in ('props',):
        if key in observation:
            result[key] = copy.deepcopy(observation[key])
    result['characters'] = []
    for row in observation.get('characters') or []:
        if not isinstance(row, dict):
            continue
        character = {key: _dynamic_value(row[key]) for key in (
            'position', 'pose', 'hands', 'visibility', 'certainty', 'facing', 'held_objects') if key in row}
        if row.get('id'):
            character['observation_id'] = row['id']
        identity = next((aliases[value] for value in (
            row.get('character_id'), row.get('name'), row.get('id')) if value in aliases), None)
        if identity:
            character['character_id'] = identity
        result['characters'].append(character)
    if not result.get('actual_visible'):
        result['actual_visible'] = ('实际位置、姿势与持物见characters；外观不作为身份依据。'
                                    if result['characters'] else 'unknown')
    return result


def source_appearance_evidence(slices, references, characters=()):
    import json
    wanted = {ref['name'] for ref in references if ref.get('role') == 'character' and ref.get('name')}
    if not wanted:
        return []
    names = wanted | {row['name'] for row in characters if isinstance(row, dict) and row.get('name')}
    latest, last_owner = {}, None
    for index, segment in enumerate(slices, 1):
        text = re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"', '', str(segment.get('text') or ''))
        for clause_index, match in enumerate(re.finditer(r'[^，,。！？!?；;\n]+', text), 1):
            clause = match[0].strip()
            mentioned = [name for name in names if name in clause]
            if len(mentioned) == 1:
                last_owner = mentioned[0]
            elif len(mentioned) > 1:
                recipient = next((name for name in mentioned if re.search(r'(?:给|帮|替|为)' + re.escape(name), clause)), None)
                last_owner = recipient
            owner = last_owner
            if owner not in wanted or re.match(r'^(?:' + '|'.join(map(re.escape, names)) + r')\s*[：:]', clause):
                continue
            attributes, changes = [], []
            wardrobe = _WARDROBE_CHANGE.search(clause)
            if wardrobe:
                changes.append(wardrobe)
                attributes.extend(key for key, pattern in _GARMENTS.items() if re.search(pattern, clause[wardrobe.start():]))
                if not attributes:
                    attributes.append('outfit')
            hair_style = _HAIR_STYLE_CHANGE.search(clause)
            if hair_style and re.search(r'发|马尾|辫|头', clause):
                changes.append(hair_style)
                attributes.append('hair_style')
            hair_color = _HAIR_COLOR_CHANGE.search(clause)
            if hair_color and re.search(r'发|头', clause):
                changes.append(hair_color)
                attributes.append('hair_color')
            if not changes or _NOT_OCCURRED.search(clause[:min(change.start() for change in changes)]):
                continue
            event = dict(segment=index, clause_index=clause_index, source_clause=clause)
            if 'outfit' in attributes:
                for key in ('outfit', 'top', 'bottom'):
                    latest.pop((owner, key), None)
            for attribute in attributes:
                latest[(owner, attribute)] = event
    grouped = {}
    for (owner, attribute), event in latest.items():
        key = (owner, event['segment'], event['source_clause'])
        row = grouped.setdefault(key, dict(character=owner, **event, active_attributes=[]))
        row['active_attributes'].append(attribute)
    result = sorted(grouped.values(), key=lambda row: (row['segment'], row['clause_index'], row['character']))
    if len(json.dumps(result, ensure_ascii=False)) > MAX_SOURCE_EVIDENCE_CHARS:
        raise AppearanceEvidenceLimitError('原文有效造型事件仍超出安全上下文额度；已保留原文，请分段处理，未截断或丢弃合法变化。')
    return result


def apply_compiled_authority(compiled):
    result = copy.deepcopy(compiled)
    fields = result['fields']
    fields['subject_definitions'] = fields['subject_definitions'].replace(
        '人物外观完整沿用对应人设图；当前姿势、表情、持物和位置服从故事及实际状态。',
        '人物身份与面部沿用对应人设图。')
    # Observation/source precedence is enforced by the caller.  The renderer
    # receives the concrete shot and reference roles, rather than that policy.
    result['prompt'] = '\n\n'.join(field + ':\n' + fields[field] for field in (
        'subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
        'overall_soundscape', 'non_diegetic_music'))
    return result
