"""Pure adapter for an isolated workbench trial of the Chinese story director.

This module neither selects a production route nor runs a model.  Source-owned
actions/dialogue and the caller's actual image pack remain authoritative.  The
only main-workbench adaptation is its already-supported Subject definition form.
"""
import copy
import re

from . import director_flow, episode_contract
from . import story_video_reference_prompts as director


VERSION = 'workbench-story-director-trial-v1'
WORKBENCH_FLOW = director_flow.VERSION
_TRIAL_SPEECH_CUE_END = re.compile(
    r'(?:开口(?:道|说|说道)?|接话(?:道|说|说道)?)(?P<separator>\s*[：:]?\s*)$')


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _characters(rows):
    result, used = [], {}
    for number, row in enumerate(rows or [], 1):
        if not isinstance(row, dict) or not _text(row.get('name')):
            raise ValueError('试验人物须有明确名字')
        name = _text(row['name'])
        identity = _text(row.get('id') or row.get('character_id')) or 'character_%d' % number
        for alias in (identity, name):
            if alias in used and used[alias] != identity:
                raise ValueError('试验人物名字与身份不能有歧义')
            used[alias] = identity
        if any(old['id'] == identity for old in result):
            raise ValueError('试验人物身份不能重复')
        result.append(dict(id=identity, name=name,
            appearance=_text(row.get('appearance')) or '人物外观以对应人设原图为准。',
            role=_text(row.get('role')) or '原文人物',
            voice=_text(row.get('voice'))))
    return result


def _source_order(sl, characters):
    contract = sl.get('director') or sl.get('director_contract') or {}
    if isinstance(sl.get('source_order'), list):
        rows = copy.deepcopy(sl['source_order'])
    elif contract.get('blocks'):
        rows = director_flow.ordered_content(copy.deepcopy(contract))
    else:
        source = _text(sl.get('text') or sl.get('source_text'))
        if not source:
            raise ValueError('试验分段缺少原文，不能自行编造情节')
        numbered, _, report = episode_contract.prepare_source(source, characters)
        if report.get('issues'):
            raise ValueError('试验分段的说话者尚未明确，须使用已确定的原文分镜')
        speech = {line['id']: line for line in report['dialogue']}
        rows = []
        for unit in director_flow.units_of(numbered):
            if unit.get('dialogue_id'):
                line = speech[unit['dialogue_id']]
                unit.update(speaker=line['speaker'], text=line['text'])
            rows.append(unit)
    for row in rows:
        if not isinstance(row, dict) or not _text(row.get('text')):
            raise ValueError('试验原文顺序项目须有非空文字')
        if row.get('dialogue_id') or row.get('speaker'):
            if not _text(row.get('speaker')):
                raise ValueError('试验原对白缺少明确说话者')
            row['is_dialogue'] = True
    return rows, contract


def build_plan(slices, characters=(), *, title='主流程中文导演隔离测试', visual_style=''):
    """Mechanically map existing workbench slices, without asking for a story.

    Supports source_order, source-v1 director blocks/says, and ordinary source
    text whose dialogue ownership can already be resolved by the main parser.
    Existing target endings remain targets, never observed actual states.
    """
    if not isinstance(slices, list) or not slices:
        raise ValueError('试验须有至少一段当前分镜')
    people, beats = _characters(characters), []
    aliases = {key: row['id'] for row in people for key in (row['id'], row['name'])}
    for number, sl in enumerate(slices, 1):
        if not isinstance(sl, dict):
            raise ValueError('试验分段须为对象')
        source, contract = _source_order(sl, people)
        dialogue = [dict(speaker=row['speaker'], text=row['text'])
                    for row in source if row.get('is_dialogue')]
        action = '\n'.join(row['text'] for row in source if not row.get('is_dialogue'))
        seconds = director.base._seconds(sl.get('seconds'))
        scene = _text(sl.get('scene_hint') or sl.get('scene') or sl.get('location'))
        if not scene:
            raise ValueError('试验分段须有当前场景名称')
        cast = contract.get('cast') or sl.get('chars') or []
        ending = episode_contract.ending_text(contract) or _text(sl.get('tail')) or 'unknown'
        beats.append(dict(index=number, seconds=seconds,
            goal=_text(contract.get('purpose')) or action or '落实当前原对白。',
            action=action or '只落实当前原对白及自然表演。',
            turn=_text(contract.get('turn')) or ending,
            emotion=_text(contract.get('emotion')) or '表演服从当前原文。',
            ending=ending, cast=[aliases.get(name, name) for name in cast],
            dialogue=dialogue, scene=scene, source_order=source,
            source_text=_text(sl.get('text') or sl.get('source_text')) or '\n'.join(
                ('%s：%s' % (row['speaker'], row['text']) if row.get('is_dialogue') else row['text'])
                for row in source),
            source_contract=copy.deepcopy(contract),
            intended_start_state=_text(contract.get('start_state'))))
    return dict(title=_text(title), logline='按当前主流程分段原文进行中文导演试验。',
        ending=beats[-1]['ending'], characters=people, props=[], invariants=[],
        beats=beats, style=_text(visual_style), adapter_version=VERSION)


def main_source_text(beat):
    """Keep existing source speech once when adapting a B beat for main checks.

    No action or source utterance is rewritten.  A fully silent action gets the
    fixed dialogue appended.  Partial/mismatching/ambiguous speech is retained
    in exception.evidence, rather than guessed or silently duplicated.
    """
    if not isinstance(beat, dict):
        raise ValueError('试验分段须为对象')
    existing = beat.get('source_text')
    if isinstance(existing, str) and existing.strip():
        return existing
    action = beat.get('action')
    if not isinstance(action, str):
        raise ValueError('试验分段须有动作原文')
    declared = beat.get('dialogue') or []
    if not isinstance(declared, list) or any(
            not isinstance(line, dict) or not all(_text(line.get(key)) for key in ('speaker', 'text'))
            for line in declared):
        raise ValueError('试验分段须有明确的固定对白数组')
    people = [dict(name=name) for name in dict.fromkeys(line['speaker'] for line in declared)]
    _, parsed, report = episode_contract.prepare_source(action, people)
    expected = [(line['speaker'], line['text']) for line in declared]

    def ambiguous(reason):
        error = ValueError('分段原文对白不能安全映射：' + reason + '；保留当前动作和对白，建议核对明确说话者及逐句分配，不重新生成视频。')
        error.evidence = dict(action=action, declared_dialogue=copy.deepcopy(declared),
            parsed_dialogue=parsed, source_report=report, reason=reason)
        raise error

    if parsed:
        if [words for _, words in parsed] != [words for _, words in expected]:
            ambiguous('动作中的发声只覆盖部分固定原话，或原字/顺序不一致')
        for index, ((speaker, words), (expected_speaker, _)) in enumerate(zip(parsed, expected)):
            if speaker == expected_speaker:
                continue
            if speaker != '待核对说话人':
                ambiguous('动作中的说话者与固定对白不一致')
            # The established parser can lose a name across an intervening
            # action, e.g. "苏晚棠看着果核，轻声开口". Confirm only a unique
            # explicit name in that same sentence, never infer a pronoun.
            evidence = report['dialogue'][index]
            prefix = re.split(r'[。！？!?\r\n]', action[:evidence['pos']])[-1]
            named = [person['name'] for person in people if person['name'] in prefix]
            if (len(named) != 1 or named[0] != expected_speaker
                    or sum(line['text'] == words for line in declared) != 1):
                ambiguous('动作中原话的说话者仍有歧义')
        return action
    if report.get('issues'):
        ambiguous('动作中的发声来源尚未解析清楚')
    # An unclassified explicit speech cue is not evidence of a silent action.
    # Written labels and ordinary quoted object names are not speaking cues.
    for quote in director._DIALOGUE_QUOTE.finditer(action):
        clause = re.split(r'[。！？!?；;\r\n]', action[:quote.start()])[-1]
        if (_TRIAL_SPEECH_CUE_END.search(clause)
                and not director._WRITTEN_CONTEXT.search(clause)
                and not director._VOICE_CONTEXT.search(clause)):
            ambiguous('动作带有明确发声提示但解析器尚未提取原话')
    appended = '\n'.join('%s说：“%s”' % line for line in expected)
    return action + ('\n' if action and appended else '') + appended


def normalize_observed_silent_entities(plan, cards, actual_state):
    """Move proven non-reference silent entities into the schema's prop list.

    This is a JSON category adapter, not story editing: the same ID can remain
    in cast, and every action/dialogue string stays byte-for-byte unchanged.
    Unknown, voiced, reference-owned or conflicting identities are retained.
    """
    if not isinstance(plan, dict) or not isinstance(actual_state, dict):
        raise ValueError('计划和实际观察须为对象')
    result = copy.deepcopy(plan)
    rows, props = result.get('characters'), result.get('props', [])
    if not isinstance(rows, list) or not isinstance(props, list):
        raise ValueError('计划人物及道具须为数组')
    fixed = {str(row[key]) for row in cards for key in ('id', 'character_id', 'name')
             if isinstance(row, dict) and row.get(key)}
    observed = actual_state.get('characters') or []
    speech = [str(line[key]) for beat in result.get('beats') or []
              for line in beat.get('dialogue') or []
              for key in ('speaker', 'speaker_id', 'character_id', 'id') if line.get(key)]
    audit = dict(version='observed-silent-entities-v1', migrated=[], retained=[])
    keep = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('计划人物项目须为对象')
        identity, name, appearance = (row.get(key) for key in ('id', 'name', 'appearance'))
        if identity in fixed or name in fixed:
            keep.append(row)
            continue
        reason = None
        if any(not isinstance(value, str) or not value.strip()
               or value.strip().lower() in ('unknown', 'n/a', '未知')
               for value in (identity, name, appearance)):
            reason = '缺少完整原始身份、名字或外观'
        elif ('count' in row and (type(row['count']) is not int or row['count'] != 1)):
            reason = '原实体数量不是可原样迁移的单一对象'
        elif any(identity in value or name in value for value in speech):
            reason = '实体在对白发声身份中出现'
        elif (sum(item.get('id') == identity or item.get('name') == name
                  for item in rows if isinstance(item, dict)) != 1
              or any(item.get('id') == identity or item.get('name') == name
                     for item in props if isinstance(item, dict))):
            reason = '人物或道具身份发生冲突'
        evidence = [item for item in observed if isinstance(item, dict) and item.get('id') == identity]
        if reason is None and (len(evidence) != 1
                or evidence[0].get('certainty') != 'observed'
                or evidence[0].get('visibility') not in ('visible', 'partial')
                or not _text(evidence[0].get('appearance'))
                or _text(evidence[0].get('appearance')).lower() in ('unknown', 'n/a', '未知')):
            reason = '实际画面尚未明确观察到唯一的该实体'
        if reason:
            keep.append(row)
            audit['retained'].append(dict(id=identity, reason=reason))
            continue
        prop = dict(id=identity, name=name, appearance=appearance, count=1)
        props.append(prop)
        audit['migrated'].append(dict(id=identity, original_character=copy.deepcopy(row),
            normalized_prop=copy.deepcopy(prop), observed_evidence=copy.deepcopy(evidence[0]),
            preserved='原ID、名字、外观、剧情、cast及对白全部保留；只调整无人物参考的静默实体JSON分类。'))
    result['characters'] = keep
    result['props'] = props
    return result, audit


def _references(references, plan):
    if not isinstance(references, list) or not references:
        raise ValueError('全能参考试验须提供当前实际参考映射')
    people = {key: row for row in plan.get('characters') or []
              for key in (row.get('id'), row.get('name')) if key}
    result = []
    for row in references:
        if not isinstance(row, dict):
            raise ValueError('实际参考映射须为对象')
        ref = copy.deepcopy(row)
        if ref.get('role') == 'scene':
            ref['role'] = 'scene_anchor'
            ref.pop('subject', None)
        if ref.get('role') == 'previous_frame':
            raise ValueError('本次主流程试验只用当前人物及进入场景时的场景图，不增加尾帧')
        if ref.get('role') == 'character':
            person = people.get(ref.get('character_id')) or people.get(ref.get('name'))
            if person is None:
                raise ValueError('当前人物参考无法对应故事人物：' + str(ref.get('name')))
            ref.update(character_id=person['id'], name=person['name'],
                       voice=_text(ref.get('voice')) or _text(person.get('voice')))
        result.append(ref)
    return director._references(result)


def write_request(plan, beat, actual_state, references, *, seconds=None,
                  visual_style='', user_brief='', scene_name=None):
    """Use the production B director with exactly the caller's submitted pack.

    actual_state must contain observed evidence supplied by the caller.  This
    adapter never upgrades a planned beginning/ending into an observation.
    """
    refs = _references(references, plan)
    scene = _text(scene_name or beat.get('scene') or beat.get('scene_hint'))
    if not scene:
        raise ValueError('主流程试验须提供当前场景名称')
    seconds = beat.get('seconds') if seconds is None else seconds
    has_scene = any(row['role'] == 'scene_anchor' for row in refs)
    system, payload, schema = director.write_request(plan, beat, actual_state, seconds,
        mode='ref2va', visual_style=visual_style,
        references=refs, flow_optimization=True,
        reference_visual_policy=director.REFERENCE_VISUAL_POLICY,
        scene_reference_provided=has_scene)
    payload.update(user_brief=_text(user_brief),
        workbench_trial=dict(version=VERSION, flow=WORKBENCH_FLOW, scene=scene,
            references=copy.deepcopy(refs), no_extra_frame=True))
    system += ('\n这是主工作台的隔离导演试验。继续忠实执行当前原文及固定对白；'
        '多镜头与景别变化服从剧情，不增加新情节。仅使用canonical_references中的实际图片，'
        '没有尾帧图片时不得宣称存在尾帧图；actual_state只承接实际观察证据，未知保持未知。')
    return system, payload, schema


def _bind_speakers(raw, payload):
    result = copy.deepcopy(raw)
    refs = {row['character_id']: row for row in payload['canonical_references']
            if row['role'] == 'character'}
    aliases = {key: row['id'] for row in payload['story_plan'].get('characters') or []
               for key in (row.get('id'), row.get('name')) if key}
    lines = result.get('dialogue')
    slots = payload.get('dialogue_slots') or []
    if not isinstance(lines, list) or len(lines) != len(slots):
        raise ValueError('试验词稿须逐项覆盖固定原对白')
    audit = []
    for index, (line, slot) in enumerate(zip(lines, slots)):
        if not isinstance(line, dict) or any(line.get(key) != slot[key] for key in ('speaker', 'text')):
            raise ValueError('试验词稿不能改动说话者、原话或顺序')
        ref = refs.get(aliases.get(slot['speaker']))
        if ref is None:
            # Source-owned off-screen speech retains its source identity.  A
            # missing visible reference is never silently created here.
            if re.search(r'<Subject\s+\d+>', str(line.get('delivery') or ''), re.I):
                raise ValueError('画外说话者不能借用其他人物的Subject身份')
            continue
        delivery = line.get('delivery')
        if not isinstance(delivery, str) or not delivery.strip():
            raise ValueError('试验对白须有当句表演说明')
        labels = re.findall(r'<Subject\s+(\d+)>', delivery, re.I)
        if any(int(label) != ref['subject'] for label in labels):
            raise ValueError('当句说话者与人物参考Subject不符')
        performance = re.sub(r'<Subject\s+\d+>\s*[，,:：;；]?\s*', '', delivery, flags=re.I).strip()
        if not re.search(r'[\u4e00-\u9fff]', performance):
            raise ValueError('试验对白不能只有人物标签')
        voice = _text(ref.get('voice'))
        line['delivery'] = '<Subject %d>，%s%s' % (ref['subject'],
            ('音色描述：' + voice + '；') if voice else '', performance)
        audit.append(dict(line_index=index, character_id=ref['character_id'],
            subject=ref['subject'], speaker_id=slot['speaker_id'], voice=voice))
    return result, audit


def _normalize_trial_spoken_copies(raw, payload):
    """Remove exact fixed-slot copies after additional explicit speech cues.

    This narrow trial-only normalization keeps the cue itself, all visible
    actions, and the structured dialogue/timing untouched.  Ordinary quotes,
    written words and voice descriptions remain evidence, not inferred speech.
    """
    candidate, adjustments = copy.deepcopy(raw), []
    slots = payload.get('dialogue_slots') or []
    copies = {}
    for index, slot in enumerate(slots):
        copies.setdefault(slot['text'], []).append(index)
    if not copies:
        return candidate, adjustments
    for field in director.CONTENT_FIELDS:
        original = candidate.get(field)
        if not isinstance(original, str):
            continue
        spans = []
        for quote in director._DIALOGUE_QUOTE.finditer(original):
            words = quote.group()[1:-1]
            if words not in copies:
                continue
            prefix = original[:quote.start()]
            clause = re.split(r'[。！？!?；;\r\n]', prefix)[-1]
            cue = _TRIAL_SPEECH_CUE_END.search(prefix)
            if (not cue or director._WRITTEN_CONTEXT.search(clause)
                    or director._VOICE_CONTEXT.search(clause)):
                continue
            start, stop = cue.start('separator'), quote.end()
            after = original[stop:]
            replacement = '' if after and re.match(r'[，,。.!！?？；;：:、]', after) else '。'
            spans.append(dict(source_start=start, source_end=stop,
                from_text=original[start:stop], to_text=replacement,
                text=words, line_indices=copies[words]))
        value = original
        for span in reversed(spans):
            value = value[:span['source_start']] + span['to_text'] + value[span['source_end']:]
        if spans:
            candidate[field] = value
            adjustments.append(dict(kind='trial_fixed_dialogue_copy_removed',
                field=field, count=len(spans), matches=spans,
                reason='明确开口/接话提示后的完整原话已由固定dialogue槽发声，保留动作和发声提示。'))
    return candidate, adjustments


def compile_reply(raw, payload):
    """Keep B's source/timing/reference checks; adapt definitions for main H3.

    source-v1's main renderer recognizes these exact identity definitions and
    can therefore apply its existing set scope and reference rebinding rules.
    Scene-only Subject labels are transport metadata, not an invented person.
    """
    trial = payload.get('workbench_trial') or {}
    if trial.get('version') != VERSION:
        raise ValueError('缺少隔离导演试验请求标记')
    normalized, adjustments = _normalize_trial_spoken_copies(raw, payload)
    bound, audit = _bind_speakers(normalized, payload)
    result = director.compile_written(bound, payload)
    if adjustments:
        result.setdefault('automatic_adjustments', []).extend(adjustments)
    refs = payload['canonical_references']
    fields = copy.deepcopy(result['fields'])
    definitions = []
    for ref in refs:
        name = director._label(ref.get('name') or ref.get('character_id'))
        if ref['role'] == 'character':
            definitions.append('<Subject %d> is %s, the character from <Picture %d>. '
                '人物外观完整沿用对应人设图；当前姿势、表情、持物和位置服从故事及实际状态。' %
                (ref['subject'], name, ref['picture']))
    scene_ref = next((row for row in refs if row['role'] == 'scene_anchor'), None)
    scene_subject = len([row for row in refs if row['role'] == 'character']) + 1
    scene_name = director._label(trial['scene'])
    definitions.append('<Subject %d> is %s, the environment%s. '
        '环境参考只约束当前地点的固定结构及现场美术；可移动物件、人物及动作服从当前故事状态。' %
        (scene_subject, scene_name,
         (' from <Picture %d>' % scene_ref['picture']) if scene_ref else ''))
    fields['subject_definitions'] = '\n'.join(definitions)
    result['fields'] = fields
    result['prompt'] = '\n\n'.join(field + ':\n' + fields[field] for field in director.FIELDS)
    expected_pictures = {row['picture'] for row in refs}
    pictures = set(map(int, re.findall(r'<Picture\s+(\d+)>', result['prompt'])))
    subjects = set(map(int, re.findall(r'<Subject\s+(\d+)>', result['prompt'])))
    if pictures != expected_pictures or subjects - set(range(1, scene_subject + 1)):
        raise ValueError('试验编译后的图片或主体绑定不完整')
    result.update(workbench_flow=WORKBENCH_FLOW, director_trial=VERSION,
        voice_binding_audit=audit, environment_subject=scene_subject)
    return result
