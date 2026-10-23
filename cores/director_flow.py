"""Source-owned director flow. One plan, one writer; legacy frozen shots stay legacy.

This module owns structural checks and reference transport, not story repair.
No model-written action is run through the legacy pose/scene/clothing rewriters.
"""
import copy
import json
import math
import re
import time
from pathlib import Path
from . import episode_contract as contract

VERSION = 'source-v1'
FIELDS = ('subject_definitions', 'summary', 'retention_analysis',
          'detailed_description', 'overall_soundscape', 'non_diegetic_music')
INSTRUCTIONS = Path(__file__).resolve().parent.parent / 'presets' / 'instructions'


def instruction(stage):
    return (INSTRUCTIONS / ('导演_连续分镜.txt' if stage == 'plan' else '导演_连续镜头写词.txt')).read_text(encoding='utf-8')


def active(slices):
    return bool(slices) and all((s.get('director') or {}).get('flow') == VERSION for s in slices)


def units_of(script):
    units = []
    for line in str(script).splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.match(r'^【D(\d+)】', line)
        for part in ([line] if match else re.split(r'(?<=[。！？!?])', line)):
            if part.strip():
                units.append({'id': len(units)+1, 'text': part.strip(),
                              'dialogue_id': int(match[1]) if match else None})
    return units


def source_windows(script, budget=900):
    """Bound local-model context, not shots: blank lines never force a cut.

    Balanced windows avoid a tiny last batch. Dialogue stays indivisible;
    the director chooses the actual shot boundaries inside each window.
    """
    units = units_of(script)
    if not units:
        return []
    weights = [max(24, len(u['text'])) for u in units]
    count = min(len(units), max(1, math.ceil(sum(weights) / budget)))
    windows, start, consumed = [], 0, 0
    target = sum(weights) / count
    for index, weight in enumerate(weights):
        consumed += weight
        if len(windows) < count - 1 and consumed >= target * (len(windows) + 1):
            windows.append('\n'.join(u['text'] for u in units[start:index+1]))
            start = index + 1
    if start < len(units):
        windows.append('\n'.join(u['text'] for u in units[start:]))
    return windows


def ordered_content(director):
    """Current edited blocks are authoritative; there is no second stale source copy."""
    d = director or {}
    says = iter(d.get('says') or [])
    content = []
    for block in d.get('blocks') or []:
        lines = {did: next(says) for did in block[3]}
        used = []
        for item in units_of(block[2]):
            did = item['dialogue_id']
            if did:
                if did not in lines or did in used:
                    raise ValueError('分镜对白编号与正文不一致，请重新保存该段')
                name, text = lines[did]
                item.update(speaker=name, text=text)
                used.append(did)
            content.append(item)
        # Old editor rows may place dialogue after an action block.
        for did, (name, text) in lines.items():
            if did not in used:
                content.append({'text': text, 'speaker': name, 'dialogue_id': did})
    return content


def edit_text(director):
    """Keep action/dialogue order visible in the existing block editor."""
    lines = []
    for block in director.get('blocks') or []:
        body = re.sub(r'【D\d+】', '', block[2]).strip()
        lines.append('%g-%g秒=%s' % (block[0], block[1], body))
    return '\n'.join(lines)


def sync_edit(director, editor_text):
    """Rebuild markers from what the user actually saved, not the old model plan."""
    from . import oral_story
    from .shot_edit import _BLOCK_RE
    blocks, says = [], []
    for line in str(editor_text).splitlines():
        m = _BLOCK_RE.match(line)
        if m:
            blocks.append([float(m[1]), float(m[2]), m[3], []])
        elif line.strip() and blocks:
            blocks[-1][2] += '\n' + line.strip()
    for block in blocks:
        text, quoted = oral_story.numbered_script(block[2])
        offset = len(says)
        block[2] = re.sub(r'【D(\d+)】', lambda m: '【D%d】' % (int(m[1])+offset), text)
        block[3] = list(range(offset+1, offset+len(quoted)+1))
        says.extend(quoted)
    director.update(blocks=blocks, says=says)
    director['timing_source'] = 'user'
    # State summaries become unreliable once the user changes a shot.
    director.pop('target_state', None)
    director.pop('start_state', None)
    for key in ('end_state_text', 'presence', 'source_contract_version', 'source_contract'):
        director.pop(key, None)
    return director


def _request(q, stage, payload, schema, validate, trace=None, repair=None, fallback=None):
    system = instruction(stage)
    current = copy.deepcopy(payload)
    if stage == 'write':
        current['output_contract'] = {
            'clip_id': payload['clip_id'],
            'dialogue_placeholders': ['{{D%d}}' % u['dialogue_id'] for u in payload['source_order'] if u.get('dialogue_id')],
            'character_labels': [r['subject_label'] for r in payload['references'] if r['role'] == 'character'],
        }
    def source_format(trigger):
        if fallback is None:
            return None
        entry = {'stage': 'source_fallback' if stage == 'write' else 'source_plan_fallback',
                 'clip_id': payload.get('clip_id'), 'trigger': str(trigger), 'status': 'checking'}
        if trace is not None:
            trace.append(entry)
        try:
            fixed = fallback(payload)
            result = validate(fixed)
            entry.update(status='accepted', repaired_data=fixed,
                         auto_repair='文字结构回复不完整，已按当前原文直接完成格式转换；动作、对白和顺序保留')
            return result
        except (ValueError, KeyError, TypeError, IndexError) as error:
            entry.update(status='blocked', validation_error=str(error))
            raise contract.ContractError('当前原文格式转换未完成：' + str(error), stage=stage,
                code='prompt_contract' if stage == 'write' else 'plan_contract',
                details={'clip_id': payload.get('clip_id'), 'error': str(error)}) from error

    # One initial request and at most two local repairs; never retry the whole story.
    for attempt in range(3):
        try:
            raw = q(system, json.dumps(current, ensure_ascii=False), mt=4000 if stage == 'plan' else 5000,
                    temperature=.2, _tries=1, response_format={'type': 'json_object', 'schema': schema})
        except RuntimeError as error:
            if not hasattr(error, 'partial_text'):
                raise
            if trace is not None:
                trace.append({'stage': stage, 'attempt': attempt + 1, 'length_error': str(error),
                              'discarded_partial_reply': True, 'scope': payload.get('current_scope')})
            result = source_format(error)
            if result is not None:
                return result
            raise
        if trace is not None:
            trace.append({'stage': stage, 'attempt': attempt+1, 'raw': raw,
                          'scope': payload.get('current_scope'), 'clip_id': payload.get('clip_id'),
                          'output_contract': current.get('output_contract')})
        try:
            parsed = _response_object(raw)
            fixed = contract.normalize_anchors(parsed) if stage == 'write' else parsed
            result = validate(fixed)
            if trace is not None and fixed != parsed:
                trace[-1]['auto_repair'] = '已规范本段对白编号写法，原话与顺序保留'
                trace[-1]['repaired_data'] = fixed
            return result
        except (ValueError, KeyError, TypeError, IndexError, StopIteration) as error:
            if trace is not None:
                trace[-1]['validation_error'] = str(error)
            if attempt == 2:
                if repair is not None:
                    try:
                        fixed = repair(_response_object(raw))
                        result = validate(fixed)
                        if trace is not None:
                            trace[-1]['auto_repair'] = '已按本段原文修复结构或对白标记；原话保留，镜内时机需留意'
                            trace[-1]['repaired_data'] = fixed
                        return result
                    except (ValueError, KeyError, TypeError, IndexError):
                        pass
                result = source_format(error)
                if result is not None:
                    return result
                raise contract.ContractError('导演文字格式自动修复后仍未通过：'+str(error),
                    stage=stage, code='prompt_contract' if stage == 'write' else 'plan_contract',
                    details={'clip_id': payload.get('clip_id'), 'error': str(error)}) from error
            # A failed global plan is not a useful draft for a local source window.
            # Retry the same bounded task, rather than priming the repeated wrong plan.
            if stage == 'plan':
                current['validation_feedback'] = {'error': str(error), 'task': '重新为当前units规划；按编号分配全部当前原文。'}
            else:
                current['validation_feedback'] = {
                    'error': str(error), 'draft': raw,
                    'task': '只修当前clip_id的写词JSON。以source_order作为唯一对白和动作来源，保留原剧情和镜头。text按output_contract的顺序各放一次对白占位符；不得保留未知编号、重复编号或发明台词。dialogue_placeholders为空表示本段没有可说出的对白，须依据原动作重新写本段，不得为配合草稿创造对白。人物动作使用对应character_labels。输出完整的本段JSON。',
                }


def _object(**fields):
    return {'type': 'object', 'properties': fields, 'required': list(fields), 'additionalProperties': False}


def _response_object(raw):
    """Allow a fenced object or surrounding explanation, not arbitrary text as a prompt."""
    text = str(raw or '').strip()
    try:
        data = json.loads(text)
    except ValueError:
        start = text.find('{')
        if start < 0:
            raise ValueError('模型未返回可用JSON对象')
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(data, dict):
        raise ValueError('模型未返回JSON对象')
    return data


def source_fallback(clip):
    """Minimal writer data from the validated source, never the failed draft."""
    seconds = clip['seconds']
    if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds) or not 4 <= seconds <= 15):
        raise ValueError('原文保底写词要求有效的4～15秒分段')
    refs = clip['references']
    if not isinstance(refs, list) or not refs or len(refs) > 9:
        raise ValueError('原文保底写词的参考素材数量无效')
    by_name = {}
    for index, ref in enumerate(refs, 1):
        name = ref['name']
        if (not isinstance(name, str) or not name.strip() or name in by_name
                or type(ref['subject']) is not int or ref['subject'] != index
                or type(ref['picture']) is not int or ref['picture'] != index
                or ref['subject_label'] != '<Subject %d>' % index
                or ref['role'] not in ('character', 'scene')):
            raise ValueError('原文保底写词的身份或图片引用无效')
        by_name[name] = ref
    scenes = [r for r in refs if r['role'] == 'scene']
    if len(scenes) != 1 or scenes[0]['name'] != clip['scene']:
        raise ValueError('原文保底写词缺少当前场景引用')
    characters = {name: ref for name, ref in by_name.items() if ref['role'] == 'character'}
    presence = clip.get('presence_contract') or {}
    needed = list(presence.get('actors') or []) + [row['name'] for row in presence.get('visual_identity_candidates') or []]
    collectives = {row['name']: row for row in clip.get('collectives') or []}
    if any(name not in characters and name not in collectives for name in needed):
        raise ValueError('原文保底写词缺少行动或可见人物引用')
    name_pattern = re.compile('|'.join(re.escape(name) for name in sorted(characters, key=len, reverse=True))) if characters else None
    def bind_names(text):
        return name_pattern.sub(lambda match: characters[match[0]]['subject_label'], text) if name_pattern else text
    content = clip['source_order']
    if not isinstance(content, list) or not content:
        raise ValueError('原文保底写词没有已确认原文')
    lines, ids, speech = [], [], 0
    for unit in content:
        words = unit['text']
        if not isinstance(words, str) or not words.strip():
            raise ValueError('原文保底写词发现空白原文')
        did = unit.get('dialogue_id')
        if did is not None:
            prefix = unit.get('speech_prefix', '')
            ref = characters.get(unit.get('speaker'))
            identity = re.escape(ref['subject_label']) if ref else None
            if unit.get('binding') in ('source_voice', 'collective'):
                identity = re.escape(unit.get('speech_identity', ''))
            if (type(did) is not int or did <= 0 or did in ids or not identity
                    or not re.fullmatch(identity + r' \(S[1-9]\d*(?:,S[1-9]\d*)*\)', prefix)):
                raise ValueError('原文保底写词的对白编号或说话人未确认')
            ids.append(did)
            speech += contract.speech_seconds(words)
            lines.append('{{D%d}}' % did)
        else:
            # An unlifted source quote must be repaired at the source, not made
            # into untracked dialogue by this last formatting fallback.
            if contract.prepare_source(words, list(characters.values()))[1]:
                raise ValueError('原文保底写词发现尚未编号的原对白')
            lines.append(bind_names(words))
    if speech > seconds:
        raise ValueError('原文保底写词不能在本段秒数内读完对白')
    # Keep only optical vocabulary from the confirmed camera description. A
    # plan may paraphrase speech or action; copying it would duplicate a line
    # or add an event not present in source_order. The source supplies the end.
    optics = re.findall(r'(?:近景|中景|全景)(?:至|转为|转|到)(?:特写|近景|中景|远景)|'
        r'(?:缓慢|缓缓|慢速|快速)?(?:推近|拉远|推镜|拉镜|摇镜|移镜|跟拍|平移|变焦)|'
        r'(?:焦点|背景)(?:虚化|转移)|浅景深|深景深|(?:大|极)?特写|近景|中景|远景|全景|俯拍|仰拍|平视|固定机位|'
        r'close-up|medium shot|wide shot|low angle|high angle|shallow depth of field',
        str(clip.get('camera') or ''), flags=re.I)
    camera = '摄影：' + '；'.join(dict.fromkeys(optics)) + '。\n' if optics else ''
    return {'clip_id': clip['clip_id'], 'summary': '按已确认原文顺序呈现本段行动和对白。',
            'style': str(clip.get('style') or '沿用参考素材的画面风格与光线。'),
            'text': camera + '\n'.join(lines),
            'overall_soundscape': '保留原文明确写出的环境声和动作声；对白仅按正文标记发声。'}


TEXT = {'type': 'string', 'minLength': 1}


def source_plan_fallback(payload):
    """Only lay out the current source units; never draft a replacement story."""
    units, places = payload['units'], payload['scenes']
    spoken = {row['id']: row for row in payload.get('dialogue') or []}
    names = set(payload.get('characters') or [])
    scene = next((p for p in places if p in str(payload.get('state_context') or '')), places[0])
    shots, current, speech = [], [], 0.0

    def finish(end):
        nonlocal current, speech
        if not current:
            return
        body = '\n'.join(u['text'] for u in current)
        presence = contract.presence_evidence(body, [{'name': name} for name in names])
        cast = list(dict.fromkeys(presence['actors'] + presence['visible'] + [spoken[u['dialogue_id']]['speaker']
            for u in current if u.get('dialogue_id') and spoken[u['dialogue_id']]['speaker'] in names]))
        narrative = [u['text'] for u in current if not u.get('dialogue_id')]
        end_state = '\n'.join(narrative[-2:]) or '当前原文第%d条台词已说完，承接上一镜状态。' % end
        shots.append({'scene_name': scene, 'cast': cast, 'camera': '平视中景，连续呈现当前原文行动。',
                      'purpose': '按原文顺序呈现当前行动与台词。', 'end_unit': end,
                      'seconds': max(4, math.ceil(speech + .4 * len(narrative))), 'end_state': end_state})
        current, speech = [], 0.0

    for index, unit in enumerate(units):
        did = unit.get('dialogue_id')
        words = spoken[did]['text'] if did else ''
        duration = contract.speech_seconds(words) if did else 0.0
        next_scene = scene
        if not did:
            matches = [(unit['text'].find(p), p) for p in places if p in unit['text']]
            if matches:
                next_scene = min(matches)[1]
        if current and (speech + duration + .4 * sum(not u.get('dialogue_id') for u in current) > 12
                        or sum(len(u['text']) for u in current) + len(unit['text']) > 320
                        or next_scene != scene):
            finish(index)
        scene = next_scene
        current.append(unit)
        speech += duration
        if speech >= 12:
            finish(index + 1)
    finish(len(units))
    return {'initial_state': str(payload.get('state_context') or places[0]), 'shots': shots}


def _source_speaker(script, dialogue_id, chars, aliases):
    """Recover an explicit source label, without an extra model judgement."""
    marker = re.search(r'^【D%d】[^\n]*' % dialogue_id, script, re.M)
    if not marker:
        return '未具名说话者', ''
    before = script[:marker.start()]
    # Quoted dialogue cannot supply an identity for the following spoken line.
    before = re.sub(r'^【D\d+】[^\n]*', '', before, flags=re.M)
    tail = before[-400:].rstrip()
    cue = re.search(r'([^。！？!?；;\n：:]*?)(?:说道|问道|答道|喊道|叫道|说|问|答|喊|道|回应|提醒|宣布)[：:]?\s*$', tail)
    if not cue:
        return '未具名说话者', ''
    label = re.split(r'[，,]', cue[1])[-1].strip()
    direct = [(a, n) for a, n in aliases.items() if a and a not in ('我', '你', '他', '她', '他们', '她们') and label.startswith(a)]
    if direct and not re.search(r'(?:和|与|及|、).*(?:齐声|异口同声|同时|一起)', label):
        alias, name = max(direct, key=lambda item: len(item[0]))
        return name, cue[0]
    # A named addressee is not the speaker: "她向陈安说" must not be
    # rebound to 陈安 simply because he is the only name in that clause.
    subject = re.split(r'(?:向|对|朝|冲着|望着|看着)', label, maxsplit=1)[0]
    pronoun = re.match(r'^(他|她)(?!们)', subject)
    if pronoun:
        label = subject = pronoun[1]
    candidates = [(a, n) for a, n in aliases.items() if a and a not in ('我', '你', '他', '她', '他们', '她们') and a in subject]
    owners = {n for _, n in candidates}
    if len(owners) == 1:
        return next(iter(owners)), cue[0]
    # A pronoun with one explicitly named matching antecedent is source metadata,
    # not a second editorial/model review. Multiple candidates stay anonymous.
    if label in ('他', '她'):
        preceding = tail[:cue.start()].rstrip()
        clauses = [c.strip() for c in re.split(r'[。！？!?；;\n]', preceding) if c.strip()]
        previous = clauses[-1] if clauses else ''
        mentioned = {n for a, n in aliases.items() if a and a not in ('我', '你', '他', '她') and a in previous}
        gender = '女' if label == '她' else '男'
        matches = {c['name'] for c in chars if isinstance(c, dict) and c.get('name') in mentioned
                   and c.get('entity_type') != 'group' and str(c.get('sex') or c.get('gender') or '') == gender}
        if len(matches) == 1:
            evidence_start = script[:marker.start()].rfind(previous)
            return next(iter(matches)), script[evidence_start:marker.start()].strip() if evidence_start >= 0 else cue[0]
    return label or '未具名说话者', cue[0]


def resolve_speakers(script, dialogue, chars, q, on_step=None, trace=None):
    """Use the generated source as authority; never re-review it with a model.

    Names/aliases and explicit nearby source cues can normalize metadata. An
    anonymous voice keeps its source label, complete words and order. It does
    not trigger an extra model call, an output-limit failure or a manual gate.
    """
    from . import oral_story
    names = [n for n, _, _ in oral_story._char_items(chars)]
    aliases = contract._aliases(chars)
    fixed, bindings = [], []
    for did, (name, text) in enumerate(dialogue, 1):
        who = name if name in names else aliases.get(name, name)
        evidence = ''
        if not who or who == '待核对说话人':
            who, evidence = _source_speaker(script, did, chars, aliases)
        fixed.append((who, text))
        bindings.append({'dialogue_id': did, 'speaker': who, 'text': text,
                         'source_evidence': evidence, 'binding': 'character' if who in names else 'source_voice'})
    changes = [{'dialogue_id': i + 1, 'from': old[0], 'to': new[0]}
               for i, (old, new) in enumerate(zip(dialogue, fixed)) if old[0] != new[0]]
    if changes:
        # Only the numbered speaker prefix changes, never dialogue words.
        script = re.sub(r'^(【D(\d+)】)[^：:\n]+[：:]',
                        lambda m: m[1] + fixed[int(m[2]) - 1][0] + '：', script, flags=re.M)
    if trace is not None:
        trace.append({'stage': 'source_speaker_bindings', 'model_review': False,
                      'bindings': bindings, 'changes': changes})
    if on_step and changes:
        on_step('已按正文绑定%d句台词，继续分镜' % len(changes))
    return script, fixed


def plan(pics, scenes, chars, q, on_step=None, prev_ledger='', debug_path=''):
    from . import director_shots as ds, oral_story, group_entities
    chars = group_entities.scoped_cards(chars, pics)
    script, dialogue, source_contract = contract.prepare_source(pics, chars, split_long_dialogue=True)
    names = [n for n, _, _ in oral_story._char_items(chars)]
    places = [n for n, _ in oral_story._scene_items(scenes)]
    if not places:
        raise ValueError('请先建立本话场景卡，再自动分段')
    all_shots, state, log = [], str(prev_ledger or ''), [{'stage': 'source_contract', 'report': source_contract}]
    try:
        too_long = [issue for issue in source_contract['issues'] if issue['code'] == 'dialogue_timing']
        if too_long:
            raise contract.ContractError('第%d句原话超过单段15秒，且没有可靠标点拆分位置；原文已保留，需补充分句标点后继续' %
                too_long[0]['dialogue_id'], stage='plan', code='dialogue_timing', details={'dialogue': too_long})
        script, dialogue = resolve_speakers(script, dialogue, chars, q, on_step, log)
        events = source_windows(script)
        aliases = contract._aliases(chars)
        for index, event in enumerate(events):
            units = units_of(event)
            payload = {'whole_story': script, 'state_context': state, 'scenes': places, 'characters': names,
                       'current_scope': {'part': index+1, 'parts': len(events), 'unit_count': len(units),
                           'last_end_unit': len(units), 'task': '只规划下方units；whole_story仅用于理解背景。'},
                       'units': units,
                       'continuity_contract': {
                           'source_presence': contract.presence_evidence(event, chars),
                           'rules': 'cast包含本镜实际行动者和说话人，以及原文视线目标、明确同框对象的身份参考；参考槽位不要求全员出镜。仅被提及、被寻找或已失踪的人不能因此在场。承接state_context中的人数、姿势、携带关系、道具归属和地点；发生变化必须有原文动作依据。end_state明确留下谁、谁离开以及手中物和姿势，不能把整个项目人物表当作本镜在场表。'},
                       'dialogue': [{'id': u['dialogue_id'], 'speaker': dialogue[u['dialogue_id']-1][0],
                                     'text': dialogue[u['dialogue_id']-1][1]}
                                    for u in units if u['dialogue_id']]}
            schema = _object(initial_state=TEXT, shots={'type': 'array', 'minItems': 1, 'maxItems': len(units), 'items': _object(
                scene_name={'type': 'string', 'enum': places},
                cast={'type': 'array', 'items': {'type': 'string', 'enum': names} if names else {'type':'string'}, 'uniqueItems': True},
                camera=TEXT, purpose=TEXT, end_unit={'type':'integer','minimum':1,'maximum':len(units)},
                seconds={'type':'integer','minimum':4,'maximum':15}, end_state=TEXT)})

            def validate(data):
                cursor, result = 0, []
                if not isinstance(data['shots'], list) or not 1 <= len(data['shots']) <= len(units):
                    raise ValueError('本次只有%d条原文，镜头数须为1～%d；只规划当前units，不规划整篇背景' % (len(units),len(units)))
                for shot_index, shot in enumerate(data['shots'], 1):
                    end = shot['end_unit']
                    if type(end) is not int or not cursor < end <= len(units):
                        raise ValueError('第%d镜end_unit=%s无效：前镜已分配到%d，本次共%d条；结束编号须递增，最后一镜结束于%d' %
                                         (shot_index,end,cursor,len(units),len(units)))
                    cast = list(dict.fromkeys(n if n in names else aliases.get(n, n) for n in shot['cast']))
                    if shot['scene_name'] not in places or not set(cast) <= set(names):
                        raise ValueError('地点和人物须来自给定卡片')
                    current = units[cursor:end]
                    presence = contract.presence_evidence('\n'.join(u['text'] for u in current), chars)
                    # Keep explicit departures active in later shots, until a
                    # new action/return in the source establishes presence again.
                    progress = contract.presence_evidence(state + '\n' + '\n'.join(u['text'] for u in units[:end]), chars)
                    presence['absent'] = progress['absent']
                    ids = [u['dialogue_id'] for u in current if u['dialogue_id']]
                    visible = contract.camera_identity_evidence(shot.get('camera'), chars, presence['absent'])
                    presence['visible'] = [name for name in presence['visible'] if name not in presence['absent']]
                    presence['visual_identity_candidates'] = [row for row in presence['visual_identity_candidates']
                        if row['name'] not in presence['absent']] + visible['evidence']
                    required = list(dict.fromkeys([dialogue[d-1][0] for d in ids] + presence['actors']
                                                 + presence['visible'] + visible['names']))
                    # A source voice without an individual card is still valid
                    # speech. It is not an instruction to invent another person.
                    required = [name for name in required if name in names]
                    missing = [name for name in required if name not in cast]
                    if missing:
                        cast.extend(missing)
                        log.append({'stage': 'cast_repair', 'part': index+1, 'shot': shot_index, 'added': missing})
                        if on_step:
                            on_step('第%d部分第%d镜已补入行动、说话或可能可见人物的身份参考：%s' % (index+1, shot_index, '、'.join(missing)))
                    absent_only = [name for name in presence['absent'] if name not in required]
                    cast = [name for name in cast if name not in absent_only]
                    shot = dict(shot, cast=cast)
                    seconds = shot['seconds']
                    if isinstance(seconds, bool) or not isinstance(seconds, (int,float)) or not math.isfinite(seconds):
                        raise ValueError('秒数无效')
                    speech = sum(contract.speech_seconds(dialogue[d-1][1]) for d in ids)
                    seconds = max(4, math.ceil(seconds), math.ceil(speech))
                    if seconds > 15:
                        raise ValueError('原对白需%d秒，请按行动拆开，单段最多15秒' % seconds)
                    if any(not isinstance(shot[k],str) or not shot[k].strip() for k in ('camera','purpose','end_state')):
                        raise ValueError('机位、目的和结束进度须为非空文字')
                    result.append(dict(shot, place=shot['scene_name'], seconds=seconds,
                        blocks=[[0,seconds,'\n'.join(u['text'] for u in current),ids]],
                        says=[dialogue[d-1] for d in ids], shot=ds.shot_of(shot['camera']),
                        target_state=shot['end_state'], end_state_text=shot['end_state'],
                        presence=presence, end_state={}))
                    cursor=end
                if cursor != len(units) or not result:
                    raise ValueError('当前原文尚未完整分配：已到%d，须继续分配至%d；最后end_unit须为%d' % (cursor,len(units),len(units)))
                return result

            if on_step:
                on_step('连续导演分段：第%d/%d部分' % (index+1,len(events)))
            shots = _request(q,'plan',payload,schema,validate,log, fallback=source_plan_fallback)
            for shot in shots:
                shot['start_state']=state
                state=shot['target_state']
            all_shots.extend(shots)
    finally:
        if debug_path:
            Path(debug_path).write_text(json.dumps(log,ensure_ascii=False,indent=2),encoding='utf-8')
    slices=ds.to_slices(all_shots)
    for sl,shot in zip(slices,all_shots):
        sl['director'].update(flow=VERSION,start_state=shot['start_state'],target_state=shot['target_state'],
                             end_state_text=shot['end_state_text'], presence=shot['presence'],
                             source_contract_version=contract.VERSION, timing_source='auto',
                             source_dialogue=[row for row in source_contract['dialogue']
                                              if row['id'] in shot['blocks'][0][3]])
        # to_slices normally appends dialogue; here it is already in its source position.
        sl['text']='── %s ──\n%s' % (sl['scene_hint'],re.sub(r'【D\d+】','',shot['blocks'][0][2]))
    return slices


def _clip(sl, number, chars, speakers, prior, settings, direction):
    from . import group_entities
    from .authoring import h3_char_order
    d=sl['director']
    names=set(d['cast'])
    content=ordered_content(d)
    chars=group_entities.scoped_cards(chars, '\n'.join((prior or '', d.get('start_state') or '', sl.get('text') or '')))
    occurrences={row['id']: row for row in d.get('source_dialogue') or []
                 if row.get('entity_type') == 'group' and row.get('id') in {u.get('dialogue_id') for u in content}}
    # First vocal occurrence, rather than the end of the whole episode, owns
    # this shot's initial group. Later occurrences add their own real refs.
    seen_groups=set()
    for unit in content:
        row=occurrences.get(unit.get('dialogue_id'))
        if row and row['speaker'] not in seen_groups:
            chars=_group_occurrence_cards(chars, row)
            seen_groups.add(row['speaker'])
    names.update(u['speaker'] for u in content if u.get('dialogue_id'))
    camera=direction or d['camera']
    # Re-evaluate frozen slices too: an earlier source-contract version may
    # already be valid while its cast omitted a hand, silhouette or background
    # person explicitly visible in the camera description.
    presence=contract.presence_evidence(sl.get('text'), chars)
    progress=contract.presence_evidence('\n'.join((prior or '', d.get('start_state') or '', sl.get('text') or '')), chars)
    presence['absent']=list(dict.fromkeys(progress['absent'] + [n for n in (d.get('presence') or {}).get('absent', [])
                                                             if n not in presence['actors']]))
    visible=contract.camera_identity_evidence(camera, chars, presence['absent'])
    presence['visible']=[name for name in presence['visible'] if name not in presence['absent']]
    presence['visual_identity_candidates']=[row for row in presence['visual_identity_candidates']
        if row['name'] not in presence['absent']] + visible['evidence']
    names.update(presence['actors'])
    names.update(presence['visible'])
    names.update(visible['names'])
    current_speakers={u['speaker'] for u in content if u.get('dialogue_id')}
    names.difference_update(name for name in presence['absent']
                            if name not in presence['actors'] and name not in current_speakers)
    d['cast']=[c['name'] for c in chars if c['name'] in names]
    d['presence']=presence
    # Same expanded card order as segment_api.new_pack; collective member cards
    # retain their own identities instead of receiving a made-up group picture.
    selected = [c for c in chars if c['name'] in names]
    for row in occurrences.values():
        card=next((c for c in _group_occurrence_cards(chars, row) if c.get('name') == row['speaker']), None)
        if card and card['name'] in names:
            selected.append(card)
    ordered, camera_owner = h3_char_order(group_entities.reference_cards(selected, chars), settings)
    ordered = [c for c in ordered if c['name'] != camera_owner]
    refs=[{'name':c['name'],'subject':i,'picture':i,'role':'character','voice':c.get('voice',''),
           'entity_type': 'group' if group_entities.is_group(c) else 'person',
           'group_count': c.get('group_count'), 'group_appearance': c.get('group_appearance') or c.get('appearance') or ''}
          for i,c in enumerate(ordered,1)]
    refs.append({'name':sl['scene_hint'],'subject':len(refs)+1,'picture':len(refs)+1,'role':'scene'})
    for ref in refs:
        ref['subject_label']='<Subject %d>' % ref['subject']
        ref['picture_label']='<Picture %d>' % ref['picture']
    by_name={r['name']:r for r in refs}
    collectives=[]
    for card in selected:
        if group_entities.is_group(card):
            members=group_entities.member_names(card, chars)
            complete=len(members) >= 2 and (card.get('group_count') is None or card.get('group_count') == len(members))
            descriptor={'name':card['name'], 'members':members, 'complete_members':complete,
                'subject_labels':[by_name[n]['subject_label'] for n in members if n in by_name],
                'group_count':card.get('group_count'), 'group_appearance':card.get('group_appearance') or ''}
            if descriptor not in collectives:
                collectives.append(descriptor)
    for unit in content:
        if unit.get('dialogue_id'):
            name=unit['speaker']
            local_chars=_group_occurrence_cards(chars, occurrences[unit['dialogue_id']]) if unit['dialogue_id'] in occurrences else chars
            local_card=next((c for c in local_chars if c.get('name') == name), None)
            local_members=group_entities.member_names(local_card, local_chars) if local_card and group_entities.is_group(local_card) else []
            complete=len(local_members) >= 2 and (local_card.get('group_count') is None or local_card.get('group_count') == len(local_members))
            _assign_speaker(speakers, name, local_chars)
            sid=speakers[name]
            vocal_ids=sid if isinstance(sid, tuple) else (sid,)
            identifier='(' + ','.join('S%d' % value for value in vocal_ids) + ')'
            if complete:
                labels=[by_name[n]['subject_label'] for n in local_members if n in by_name]
                identity='The collective "%s" comprising %s' % (name, ' and '.join(labels) or 'the source-defined members')
                unit.update(binding='collective', speech_identity=identity, speech_delivery='say together in unison')
            elif name in by_name:
                identity=by_name[name]['subject_label']
                if by_name[name].get('entity_type') == 'group':
                    unit.update(binding='collective', speech_identity=identity, speech_delivery='say together in unison')
            else:
                identity='The source voice "%s"' % name
                unit.update(binding='source_voice', speech_identity=identity, speech_delivery='says')
            unit['speech_prefix']=identity + ' ' + identifier
    return {'clip_id':number,'seconds':sl['seconds'],'scene':sl['scene_hint'], 'references':refs,
            'collectives':collectives,
            'camera':camera,'purpose':d['purpose'],'source_order':content,
            'earlier_source':prior,'start_state':d.get('start_state',''), 'end_state':contract.ending_text(d),
            'presence_contract': presence,
            'continuity_rules': '仅绘制当前动作或机位可能看到的人物。身份参考槽位不要求所有人物同时出镜，也不要求每帧都显示；背影或身体局部若可见，须沿用对应人物身份。失踪或离场者不可因拥有参考图重新出现；衣着、携带关系、道具归属、人数和姿势承接开始状态，变化须拍出动作。',
            'style':settings.get('style') or settings.get('visual_style') or settings.get('art_style') or '',
            'extra_requirements':settings.get('extra_requirements') or ''}


def assemble(data, clip):
    data = contract.normalize_anchors(data)
    if data['clip_id'] != clip['clip_id']:
        raise ValueError('镜头编号不符')
    raw={k:data[k] for k in ('summary','style','text','overall_soundscape')}
    for k,text in raw.items():
        if not isinstance(text,str) or not text.strip():
            raise ValueError(k+'为空')
        text = re.sub(r'<\s*(subject|picture)\s*(\d+)\s*>',
                      lambda m: '<%s %d>' % (m[1].capitalize(), int(m[2])), text, flags=re.I)
        raw[k]=re.sub(r'(<Subject \d+>)\s*\(S\d+\)',r'\1',text)
    # A model may put its own quoted translation immediately before a D anchor.
    # That slot belongs to the original dialogue, just like speaker metadata.
    # Remove only this duplicate slot, not surrounding action prose or signage.
    raw['text']=re.sub(r'<Subject \d+>\s*:?\s*["“][^"”\n]*["”]\s*(?=\{\{D\d+\}\})', '', raw['text'])
    joined='\n'.join(raw.values())
    for field in ('summary', 'style', 'overall_soundscape'):
        if re.search(r'\{\{D\d+\}\}', raw[field]):
            raise ValueError('对白占位符只能写在text中，不能藏在摘要、风格或声音字段')
    if re.search(r'(?:\{\{?|\[|【|<)\s*[dD]\s*[\d?A-Za-z]', re.sub(r'\{\{D\d+\}\}', '', raw['text'])):
        raise ValueError('对白占位符格式不完整，请按源编号使用{{D数字}}')
    allowed={(kind,str(ref[key])) for ref in clip['references'] for kind,key in (('Subject','subject'),('Picture','picture'))}
    if not set(re.findall(r'<(Subject|Picture|Audio|Video)\s*(\d+)>',joined)) <= allowed:
        raise ValueError('正文引用了本段未绑定素材')
    if re.search(r'<d>|\(S\d+\)|\[Shot\s+\d+\]',joined):
        raise ValueError('对白及镜头标记由程序绑定，正文用D占位符')
    departure = contract.unsupported_departure(joined, '\n'.join(
        unit['text'] for unit in clip['source_order'] if not unit.get('dialogue_id')))
    if departure:
        raise ValueError(departure)
    dialogue=[u for u in clip['source_order'] if u.get('dialogue_id')]
    if any(line['text'] in joined for line in dialogue):
        raise ValueError('原对白只由D占位符承担；删除正文重复写出的原话，保留占位符')
    actual = [int(n) for n in re.findall(r'\{\{D(\d+)\}\}',raw['text'])]
    expected = [u['dialogue_id'] for u in dialogue]
    if actual != expected:
        raise ValueError('对白占位符须按原文顺序各出现一次：应为%s，实际为%s' % (expected, actual))
    text=raw['text']
    for line in dialogue:
        text=text.replace('{{D%d}}'%line['dialogue_id'],line['speech_prefix']+' '+line.get('speech_delivery','says')+', <d>[Chinese] '+line['text']+'</d>')
    # Cast/reference slots can include off-screen people. Availability is not
    # an instruction that every card must appear in every frame or action.
    definitions=[]
    for r in clip['references']:
        role='environment' if r['role']=='scene' else 'character'
        definitions.append('<Subject %d> is %s, the %s from <Picture %d>.' % (r['subject'],r['name'],role,r['picture']))
        if r['role']=='scene':
            definitions[-1] += ' Reference scope: fixed architecture, fixed furniture layout and lighting only; movable props and any people in the image are not fixed scene content.'
        else:
            definitions[-1] += ' Reference scope: character identity, face, hair and clothing only; pose, hand occupancy and carried objects follow the current action and preceding state.'
            if r.get('entity_type') == 'group':
                definitions[-1] += ' This subject is a collective group, not one individual. Preserve the source-defined group count%s, appearance and shared positions: %s.' % (
                    ' (%s people)' % r['group_count'] if r.get('group_count') else '', r.get('group_appearance') or 'follow the current source')
        if r.get('voice') and r['name'] in {u.get('speaker') for u in dialogue}:
            definitions[-1] += ' Voice reference: '+str(r['voice'])+'.'
    for collective in clip.get('collectives') or []:
        if collective.get('members') and collective.get('complete_members', True):
            definitions.append('The collective "%s" consists of %s; these are the same source-defined people, not additional background duplicates. Use their own identities and voices when speaking together.' % (
                collective['name'], ' and '.join(collective['subject_labels']) or 'the source-defined members'))
    object_scope = ('Movable object count, location, holder and completed actions follow the confirmed source and preceding state, '
                    'advanced only by the written actions; do not duplicate an object or restore its earlier placement from a reference image.')
    fields=dict(subject_definitions='\n'.join(definitions),summary='[reference generation] '+raw['summary'],
        retention_analysis='\n'.join(
            ('<Subject %d> (appears in [Shot 1]): partially_preserved - retain fixed architecture, fixed furniture layout and lighting only. ' % r['subject'] + object_scope)
            if r['role']=='scene' else
            ('<Subject %d> (appears in [Shot 1]): fully_preserved - character identity, face, hair and clothing only; pose and hand occupancy follow the current action and preceding state.' % r['subject'])
            if r['subject_label'] in text else
            ('<Subject %d> (identity reference only): fully_preserved - retain identity, face, hair and clothing if visible; pose and hand occupancy follow the current action and preceding state; no additional entrance is specified.' % r['subject'])
            for r in clip['references']),
        detailed_description=raw['style']+'\n[Shot 1]\nFrom 0 to %g seconds, '%clip['seconds']+text,
        overall_soundscape=raw['overall_soundscape'], non_diegetic_music='N/A')
    return '\n\n'.join(k+':\n'+fields[k] for k in FIELDS)


def repair_missing_dialogue(data, clip):
    """Recover an anchor at its exact source words; never invent a speech position."""
    fixed = contract.normalize_anchors(data)
    text = fixed['text']
    expected = [u['dialogue_id'] for u in clip['source_order'] if u.get('dialogue_id')]
    actual = [int(n) for n in re.findall(r'\{\{D(\d+)\}\}', text)]
    if actual != [n for n in expected if n in actual]:
        raise ValueError('未知、重复或乱序对白须由本段原文重新修复')
    for line in clip['source_order']:
        did = line.get('dialogue_id')
        if did and did not in actual:
            if text.count(line['text']) != 1:
                raise ValueError('缺失对白位置没有唯一原文依据，保留本段等待修复')
            text = text.replace(line['text'], '{{D%d}}' % did, 1)
    if [int(n) for n in re.findall(r'\{\{D(\d+)\}\}', text)] != expected:
        raise ValueError('对白来源顺序不一致')
    fixed['text'] = text
    return fixed


def repair_slice_source(sl, chars, q, on_step=None, trace=None):
    """Upgrade old slices that treated literary dialogue as silent action.

    Existing D rows remain authoritative. Only metadata/layout changes; the
    original block and its complete source evidence remain available for review.
    """
    director = sl['director']
    if director.get('source_contract_version') == contract.VERSION:
        return False
    candidate = copy.deepcopy(director)
    old_says = list(candidate.get('says') or [])
    offset, says, blocks, reports = 0, [], [], []
    for block in candidate.get('blocks') or []:
        ids = block[3]
        if offset + len(ids) > len(old_says):
            raise contract.ContractError('分镜原话记录不完整，已保留分镜供修复', code='dialogue_source_mismatch')
        assigned = {did: old_says[offset + index] for index, did in enumerate(ids)}
        offset += len(ids)
        body = block[2]
        seen = []

        def old_line(match):
            did = int(match[1])
            if did not in assigned or did in seen:
                raise contract.ContractError('分镜对白编号与原话记录不符', code='dialogue_source_mismatch')
            seen.append(did)
            return '%s：%s' % tuple(assigned[did])

        body = re.sub(r'^【D(\d+)】[^\n]*', old_line, body, flags=re.M)
        # Legacy editor blocks kept some dialogue separately from action prose.
        for did, line in assigned.items():
            if did not in seen:
                _, found, _ = contract.prepare_source(body, chars)
                if tuple(line) not in found:
                    body += '\n%s：%s' % tuple(line)
        script, quoted, report = contract.prepare_source(body, chars)
        script, quoted = resolve_speakers(script, quoted, chars, q, on_step, trace)
        first = len(says)
        script = re.sub(r'【D(\d+)】', lambda m: '【D%d】' % (int(m[1]) + first), script)
        blocks.append([block[0], block[1], script, list(range(first + 1, first + len(quoted) + 1))])
        says.extend(quoted)
        reports.append(report)
    if offset != len(old_says):
        raise contract.ContractError('分镜原话没有全部归属到动作块', code='dialogue_source_mismatch')
    changed = blocks != candidate.get('blocks') or [list(x) for x in says] != [list(x) for x in old_says]
    candidate.update(blocks=blocks, says=says, source_contract_version=contract.VERSION,
                     source_contract={'original_blocks': copy.deepcopy(director.get('blocks')), 'reports': reports})
    candidate['presence'] = contract.presence_evidence('\n'.join(b[2] for b in blocks), chars)
    required = list(dict.fromkeys([name for name, _ in says] + candidate['presence']['actors']
                                 + candidate['presence']['visible']))
    candidate['cast'] = list(dict.fromkeys((candidate.get('cast') or []) + required))
    candidate['cast'] = [name for name in candidate['cast']
                         if name not in candidate['presence']['absent'] or name in required]
    candidate['end_state_text'] = contract.ending_text(candidate)
    # Recovered speech must fit the existing shot; do not render clipped dialogue.
    speech_seconds = sum(contract.speech_seconds(words) for _, words in says)
    original_seconds = float(sl['seconds'])
    extra = 0.0
    retimed = copy.deepcopy(blocks)
    for block in retimed:
        begin, finish = float(block[0]), float(block[1])
        if begin < 0 or finish <= begin:
            raise contract.ContractError('本段时间块无效，保留原段待修复', code='dialogue_timing')
        needed = sum(contract.speech_seconds(says[did - 1][1]) for did in block[3])
        width = max(finish - begin, needed)
        block[0], block[1] = begin + extra, begin + extra + width
        extra += width - (finish - begin)
    minimum_seconds = max(speech_seconds, original_seconds + extra, float(retimed[-1][1]) if retimed else 0)
    required_seconds = math.ceil(minimum_seconds) if minimum_seconds > original_seconds + 1e-9 else original_seconds
    if required_seconds > original_seconds:
        automatic = director.get('flow') == VERSION and director.get('timing_source') != 'user'
        if not automatic or required_seconds > 15:
            raise contract.ContractError('恢复原话后本段需%d秒，原分镜只有%s秒；%s，已保留原段' %
                (required_seconds, sl['seconds'], '需局部重新分镜' if required_seconds > 15 else '保留你设置的时间，需调整本段时长'),
                code='dialogue_timing', details={'required_seconds': required_seconds,
                                                'original_seconds': original_seconds, 'automatic': automatic})
        if retimed:
            retimed[-1][1] = required_seconds
        candidate['blocks'] = retimed
        candidate['seconds'] = required_seconds
        candidate['timing_repair'] = {'original_seconds': original_seconds, 'seconds': required_seconds,
                                      'reason': '恢复原话后按发声时长延长自动分镜；原动作与原话保留'}
        sl['seconds'] = required_seconds
        changed = True
        message = '已按原话发声时长将本段从%g秒延长到%d秒' % (original_seconds, required_seconds)
        if trace is not None:
            trace.append({'stage': 'timing_repair', 'auto_repair': message,
                          'original_seconds': original_seconds, 'seconds': required_seconds})
        if on_step:
            on_step(message)
    sl['director'] = candidate
    if changed:
        sl['text'] = '── %s ──\n%s' % (sl['scene_hint'], contract.plain_script('\n'.join(b[2] for b in blocks)))
        if trace is not None:
            trace.append({'stage': 'source_repair', 'auto_repair': '已从本段原文恢复对白结构，原话与动作保留',
                          'dialogue_before': len(old_says), 'dialogue_after': len(says)})
    return changed


def _group_occurrence_cards(chars, row):
    """Apply already-saved source occurrence facts to a runtime card copy."""
    return [dict(c, group_members=list(row.get('group_members') or []), group_count=row.get('group_count'))
            if c.get('name') == row.get('speaker') else c for c in chars]


def _assign_speaker(speakers, name, chars):
    """Assign a stable ID per actual voice; named collectives reuse member IDs."""
    from . import group_entities
    card=next((c for c in chars if c.get('name') == name), None)
    members=group_entities.member_names(card, chars) if card and group_entities.is_group(card) else []
    if card and card.get('group_count') is not None and card.get('group_count') != len(members):
        members=[]
    def individual(who):
        if who not in speakers:
            used=[value for sid in speakers.values() for value in (sid if isinstance(sid, tuple) else (sid,))]
            speakers[who]=max(used, default=0) + 1
        return speakers[who]
    if members:
        speakers[name]=tuple(individual(who) for who in members)
    elif card and group_entities.is_group(card):
        # A partly known collective has its own stable combined vocal source;
        # two named members cannot stand in for the rest of a larger crowd.
        speakers[name]=individual(('collective_voice', name))
    else:
        individual(name)


def make_prompts(slices, chars, scenes, q, settings=None, start=0, count=None, on_step=None, prev_tail='', direction=None,
                 on_prompt=None, trace_dir=None, on_error=None):
    """Return the existing make_h3_prompts contract; one writer call per requested clip."""
    from .age_policy import assert_allowed
    settings=settings or {}
    from .character_facts import check_design_scale
    check_design_scale(chars, settings)
    assert_allowed(cards=chars,settings=settings,text='\n'.join(s['text'] for s in slices),media='video_prompt')
    speakers={}
    from . import group_entities
    prior_source=''
    for sl in slices:
        d=sl.get('director') or {}
        scoped=group_entities.scoped_cards(chars, '\n'.join((prior_source, d.get('start_state') or '', sl.get('text') or '')))
        occurrences=[row for row in d.get('source_dialogue') or [] if row.get('entity_type') == 'group']
        for line in d.get('says') or []:
            if isinstance(line, (list, tuple)) and len(line) == 2:
                row=next((r for r in occurrences if r.get('speaker') == line[0] and r.get('text') == line[1]), None)
                local=_group_occurrence_cards(scoped, row) if row else scoped
                _assign_speaker(speakers,line[0],local)
        prior_source+='\n' + str(sl.get('text') or '')
    end=len(slices) if count is None else min(len(slices),start+max(0,int(count)))
    out=[]
    for k in range(start,end):
        sl=slices[k]
        if on_step:
            on_step('连续导演写词：第%d/%d段' % (k+1,len(slices)))
        trace = []
        try:
            if sl['scene_hint'] not in scenes:
                raise ValueError('分段场景已不存在，请重新选择场景')
            repair_slice_source(sl, chars, q, on_step, trace)
            for name, _ in sl['director'].get('says') or []:
                _assign_speaker(speakers,name,chars)
            prior='\n'.join(s['text'] for s in slices[:k])
            clip=_clip(sl,k+1,chars,speakers,prior,settings,str(direction or '').strip())
            if k==start and prev_tail:
                clip['rendered_previous_state']=str(prev_tail)
            schema=_object(clip_id={'type':'integer','enum':[k+1]}, summary=TEXT,style=TEXT,text=TEXT,overall_soundscape=TEXT)
            prompt=_request(q,'write',clip,schema,lambda data:assemble(data,clip),trace,
                            repair=lambda data: repair_missing_dialogue(data, clip), fallback=source_fallback)
        except (ValueError, KeyError, TypeError, IndexError, StopIteration) as error:
            from .age_policy import AgePolicyError
            if isinstance(error, AgePolicyError):
                raise
            if on_error is not None:
                on_error(k+1, '第%d段视频提示词待补：%s' % (k+1, error))
                continue
            raise ValueError('第%d段视频提示词失败：%s' % (k+1, error)) from error
        finally:
            if trace_dir:
                trace_root = Path(trace_dir)
                trace_root.mkdir(parents=True, exist_ok=True)
                (trace_root / ('clip_%03d_%d.json' % (k+1,time.time_ns()))).write_text(
                    json.dumps({'clip_id': k+1, 'attempts': trace},ensure_ascii=False,indent=2),encoding='utf-8')
        from .authoring import desex_text
        prompt=desex_text(prompt,settings)
        assert_allowed(cards=chars,settings=settings,text=prompt,media='video_prompt')
        repairs = [t['auto_repair'] for t in trace if t.get('auto_repair')]
        fallback_used = any(t['stage'] == 'source_fallback' and t.get('status') == 'accepted' for t in trace)
        if fallback_used:
            sl['director']['prompt_recovery'] = {'mode': 'source_fallback', 'reason': repairs[-1]}
        else:
            sl['director'].pop('prompt_recovery', None)
        body = prompt.split('detailed_description:', 1)[-1]
        unused = [r['name'] for r in clip['references'] if r['role']=='character' and r['subject_label'] not in body]
        if unused:
            repairs.append('本镜未明确使用这些人物标签，保留参考槽位并继续：'+'、'.join(unused))
        if repairs and on_step:
            on_step(('第%d段使用原文保底写词（source_fallback）：原动作和对白顺序已校验，继续生成' if fallback_used
                     else '第%d段格式已兼容处理，继续生成') % (k+1))
        row = {'n':k+1,'prompt':prompt,'problems':repairs,'critical_problems':[],
            'source_text':sl['text'],'seconds':sl['seconds'],'over':False,'tail':contract.ending_text(sl['director']),
            'source_contract': copy.deepcopy(sl['director'].get('source_contract') or {}),
            'source_fallback': fallback_used,
            'director_contract': copy.deepcopy(sl['director']),
            'scene':sl['scene_hint'],'chars':[r['name'] for r in clip['references'] if r['role']=='character'],
            'cut':bool(sl.get('cut')),'establish':bool(sl.get('establish')),'shot':sl.get('shot',''),
            'direction':str(direction or ''),'plan':{'flow':VERSION,'camera':clip['camera'],'purpose':clip['purpose']},
            'relay':{},'done_all':[],'wardrobe':{},'beat':sl.get('beat',''),'beat_index':sl.get('beat_index',-1)}
        if on_prompt:
            on_prompt(row)
        out.append(row)
    return out


def prompt_problems(prompt, source_text, seconds, scene):
    """Six-field text uses <d> dialogue, not the legacy Chinese 镜头N parser."""
    from . import oral_story
    problems=[]
    departure = contract.unsupported_departure(prompt, source_text)
    if departure:
        problems.append(departure)
    _, source, _=contract.prepare_source(source_text)
    spoken=re.findall(r'<d>\[Chinese\]\s*(.*?)</d>',prompt,re.S)
    if spoken != [line for _,line in source]:
        problems.append('视频提示词对白与当前分段原话或顺序不一致')
    if any(field+':' not in prompt for field in FIELDS):
        problems.append('视频提示词六字段不完整')
    if not 4 <= float(seconds or 0) <= 15:
        problems.append('本段秒数须为4～15秒')
    if not scene:
        problems.append('本段没有绑定场景')
    return problems


def with_pose_reference(prompt, number, mode):
    """Append the actual extra frame without breaking six-section ordering."""
    if mode == 'none':
        return prompt
    role = ('The first frame continues from this image; current action advances from it.' if mode == 'first' else
            'Use only room geometry and light continuity; framing and current action follow this shot.' if mode == 'space' else
            'Use spatial relationships at the previous ending as context; current action follows this shot.')
    definition='<Picture %d> is the previous rendered frame. %s\n' % (number,role)
    prompt=prompt.replace('\n\nsummary:', '\n'+definition+'\nsummary:', 1)
    retention='<Picture %d> ([Shot 1] continuity): weak_reference - %s\n' % (number,role)
    return prompt.replace('\n\ndetailed_description:', '\n'+retention+'\ndetailed_description:',1)
