"""Suppress high-confidence repeated leading actions on a working slice only.

Written events establish a planned source state, never an observed completion.
Only explicit standing and arrival facts are recognized. Unknown language is
retained, and the first novel action or dialogue closes the suppression window.
"""
import copy
import re


_CUT = r'，。；,;\n'
_HEADER = re.compile(r'^\s*──[^\n]*──\s*\n')
_QUOTE = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"|‘[^’]*’')
_JUMP = re.compile(r'次日|翌日|第二天|隔天|数日后|几天后|多年后|数年后|稍后|片刻后|过了一会|'
                   r'[一二三四五六七八九十几数\d]+(?:小时|分钟|天|年)后|另一边|与此同时')
_UNREALIZED = re.compile(r'准备|打算|试图|想要|将要|即将|还没|尚未|未曾|没有|没能|未能|并未|从未|不要|不能|别起|'
                         r'(?:不|没|未|想|要|将|欲)(?:起身|站起|走到|来到|到达)')
_NEW_ROUND = re.compile(r'再次|再度|重新|又一次|第二次|又|再起|再站')
_FILL = r'(?:(?:随后|接着|然后|于是|便|就|缓缓|慢慢|轻轻|渐渐|平稳地|迅速|立刻|了))'
_RISE = re.compile(r'^(?:(?:从|由)[^，。；\n]{1,10}(?:上|中|里))?' + _FILL +
                   r'*(?:站起(?:身|来)?|站了起来|起身|起立|站起来)(?:了)?$')
_POSE = re.compile(r'站起(?:身|来)?|起身|起立|站起来|站了起来|站立|站在|站于|'
                   r'坐下|坐回|坐在|坐着|坐姿|蹲下|蹲在|蹲着|跪下|跪在|躺下|躺着|躺在')
_ARRIVE = re.compile(r'(?:走到(?:了)?|来到(?:了)?|到达|抵达|行至|走至)(?P<target>[^，。；\n]{1,18})')
_PLACE = re.compile(r'^[\u4e00-\u9fff\d]{0,10}(?:门口|门旁|门边|门前|门外|门内|'
                    r'窗边|窗旁|窗前|桌旁|桌边|床旁|床边|台阶旁|走廊尽头|大厅中央|柜台前|屋内)$')
_STATIC = re.compile(r'^(?:站立|站着|站在.+|站于.+|位于.+|坐在.+|坐着|蹲在.+|蹲着|躺在.+|躺着)$')
_LITERAL_EVENT = re.compile(r'开|关|递|交|放|拿|捡|拾|推|拉|按|敲|拍|握|抓|转|拧|穿|脱|戴|摘|'
                            r'取|举|抬|伸|走|跑|坐|站|起身|停|挡|抱|松|踢|刺|砍|攻击')
_STATE_RESET = re.compile(r'起身|站起|起立|走到|来到|到达|抵达|走至|行至|坐下|坐回|蹲下|跪下|躺下')


def _names(characters):
    return list(dict.fromkeys(item if isinstance(item, str) else str(item.get('name') or '')
                             for item in characters if isinstance(item, (str, dict))))


def _scene(row):
    return str((row or {}).get('scene_hint') or (row or {}).get('scene') or
               (row or {}).get('location') or '').strip()


def _body(text):
    match = _HEADER.match(text)
    return (text[:match.end()], text[match.end():]) if match else ('', text)


def _tokens(text):
    return re.findall(r'[^' + _CUT + r']+(?:[' + _CUT + r']+|$)|[' + _CUT + r']+', text)


def _core(text):
    return text.strip(' \t\r\n，。；,;')


def _actor(text, context, names, scope):
    core = _core(text)
    core = re.sub(r'^(?:随后|接着|然后|于是)', '', core)
    for name in sorted(names, key=len, reverse=True):
        if name and core.startswith(name):
            return name, core[len(name):].lstrip(), name
    if core.startswith(('她', '他')):
        if len(scope) == 1:
            return scope[0], core[1:].lstrip(), scope[0]
        return '', core, context
    return context or '', core, context


def _scope(row, names):
    selected = (row.get('director') or {}).get('cast') or row.get('chars') or []
    return [name for name in selected if name in names] or names


def _target(value):
    value = re.sub(r'(?:停下|站定|停住|站住|驻足)(?:脚步)?$', '', value).strip()
    return re.sub(r'门边$', '门旁', value) if _PLACE.fullmatch(value) else ''


def _pose(value):
    if value.startswith(('站', '起身', '起立')):
        return 'stand'
    if value.startswith('坐'):
        return 'sit'
    if value.startswith(('蹲', '跪')):
        return 'low'
    if value.startswith('躺'):
        return 'lie'
    return ''


def _source_text(row):
    blocks = (row.get('director') or {}).get('blocks') or []
    text = '\n'.join(str(block[2]) for block in blocks if isinstance(block, (list, tuple)) and len(block) >= 3)
    return text if blocks else _body(str(row.get('text') or ''))[1]


def _planned(previous, names):
    text = re.sub(r'(?m)^\s*【D\d+】[^\n]*', '', _source_text(previous))
    text = _QUOTE.sub('', text)
    states, context = {}, None
    for token in _tokens(text):
        if re.search(r'【D\d+】', token):
            continue
        actor, rest, context = _actor(token, context, names, _scope(previous, names))
        if not actor or re.match(r'^\s*[:：]', rest) or _UNREALIZED.search(rest):
            continue
        if any(other != actor and other in rest for other in names):
            # "陈默看见林岚站起来" is not Chen standing; do not transfer
            # another person's posture through a free-form relative clause.
            context = None
            continue
        speech = re.search(r'说|回答|问道|喊道|表示|宣称', rest)
        if speech:
            rest = rest[:speech.start()]
        state = states.setdefault(actor, {})
        poses = list(_POSE.finditer(rest))
        if poses:
            state.update(pose=_pose(poses[-1].group()), pose_origin='planned_source')
        arrival = _ARRIVE.search(rest)
        if arrival:
            target = _target(arrival['target'])
            if target:
                state.update(position=target, position_origin='planned_source')
                # Walking to a place establishes upright arrival unless a later
                # explicit posture reset in the same clause says otherwise.
                if not poses or poses[-1].start() < arrival.start():
                    state.update(pose='stand', pose_origin='planned_source')
        elif re.search(r'走向|走近|跑向|离开|走出|退后|后退', rest):
            state.pop('position', None)
        static = re.search(r'(?:站在|站于|位于|坐在|蹲在|躺在)([^，。；\n]{1,18})', rest)
        if static and _target(static[1]):
            state.update(position=_target(static[1]), position_origin='planned_source')
    return states


def _observed(states, text, names):
    for row in re.split(r'[；;\n]', text):
        for name in names:
            match = re.match(r'\s*' + re.escape(name) + r'\s*[:：]\s*(.*)$', row)
            if not match:
                continue
            state = states.setdefault(name, {})
            # A verified current row supersedes a planned location even when
            # its literal location is outside our small supported vocabulary.
            state.pop('position', None)
            state.pop('position_origin', None)
            parts = [part.strip() for part in re.split(r'[，,]', match[1])]
            for part in parts:
                target = _target(re.sub(r'^(?:站在|站于|位于|坐在|蹲在|躺在)', '', part))
                if target:
                    state.update(position=target, position_origin='observed_start_state')
                pose = re.search(r'站立|站在|站于|坐姿|坐在|坐着|蹲|跪|躺', part)
                if pose:
                    state.update(pose=_pose(pose.group()), pose_origin='observed_start_state')


def _static(actor, state):
    position, pose = state.get('position'), state.get('pose')
    if position:
        verb = {'stand': '站在', 'sit': '坐在', 'low': '蹲在', 'lie': '躺在'}.get(pose, '位于')
        return actor + verb + position
    return actor + {'stand': '站立', 'sit': '坐着', 'low': '蹲着', 'lie': '躺着'}.get(pose, '')


def _trim_text(text, states, names, scope, segment):
    header, body = _body(text)
    tokens, audit, context, anchor = _tokens(body), [], None, None
    closed = False
    for index, token in enumerate(tokens):
        core = _core(token)
        if not core:
            continue
        if re.search(r'【D\d+】|[“「『"‘]', core):
            closed = True
            break
        actor, rest, context = _actor(core, context, names, scope)
        if not actor or re.match(r'^[:：]', rest):
            closed = True
            break
        state = states.get(actor) or {}
        if _STATIC.fullmatch(rest):
            # Static carry-in prose is idempotent and may precede another
            # duplicate that a later verified endpoint can resolve.
            if state and (not state.get('position') or _target(re.sub(r'^(?:站在|站于|位于|坐在|蹲在|躺在)', '', rest)) in ('', state.get('position'))):
                anchor = (index, actor)
                continue
            closed = True
            break
        rise = _RISE.fullmatch(rest)
        arrival = _ARRIVE.fullmatch(rest)
        if not (rise or arrival):
            closed = True
            break
        if _NEW_ROUND.search(rest) or _UNREALIZED.search(rest):
            closed = True
            break
        origin = state.get('pose_origin') if rise else state.get('position_origin')
        repeated = bool(state.get('pose') == 'stand') if rise else bool(
            _target(arrival['target']) and _target(arrival['target']) == state.get('position'))
        if not repeated:
            audit.append(dict(method='preserve_unknown_prefix', source_kind='unknown', actor=actor,
                              removed='', replacement=core, source_segment=segment))
            closed = True
            break
        static = _static(actor, state)
        delimiter = re.search(r'[' + _CUT + r']*$', token).group()
        leading = re.match(r'\s*', token).group()
        replacement = static
        if anchor and anchor[1] == actor:
            anchor_index = anchor[0]
            old_delimiter = re.search(r'[' + _CUT + r']*$', tokens[anchor_index]).group()
            tokens[anchor_index] = static + old_delimiter
            tokens[index] = ''
            replacement = ''
        else:
            tokens[index] = leading + static + delimiter
            anchor = (index, actor)
        audit.append(dict(method='suppress_repeated_prefix', source_kind=origin or 'planned_source',
                          actor=actor, removed=core, replacement=replacement, source_segment=segment))
    rewritten = ''.join(tokens)
    if any(event['method'] == 'suppress_repeated_prefix' for event in audit):
        rewritten = re.sub(r'[，,；;](\s*)$', r'。\1', rewritten)
    return header + rewritten, audit, closed


def _literal_records(text, names, scope, *, prefix=False, states=None, context=None):
    """Literal actor/event keys; no synonym or object inference in this path."""
    _, body = _body(text)
    tokens, records = _tokens(body), []
    for index, token in enumerate(tokens):
        core = _core(token)
        if not core:
            continue
        if re.search(r'【D\d+】|[“「『"‘]', core):
            if prefix:
                break
            context = None
            continue  # Quoted speech never contributes a physical event key.
        actor, rest, context = _actor(core, context, names, scope)
        if actor and _STATIC.fullmatch(rest):
            if prefix and not records and (states or {}).get(actor):
                state = states[actor]
                pose = _pose(rest)
                position = _target(re.sub(r'^(?:站在|站于|位于|坐在|蹲在|躺在)', '', rest))
                if ((pose and state.get('pose') and pose != state['pose']) or
                        (position and state.get('position') and position != state['position'])):
                    break
                continue  # An already emitted, compatible carry-in is not new.
            if not prefix:
                records = []
                continue
        invalid = (not actor or re.match(r'^[:：]', rest) or _UNREALIZED.search(rest) or
                   _NEW_ROUND.search(rest) or any(other != actor and other in rest for other in names) or
                   re.search(r'说|回答|问道|喊道|表示|宣称', rest) or not _LITERAL_EVENT.search(rest))
        # Specialized posture/arrival checks own these, particularly when the
        # verified start contradicts the previous written posture or position.
        invalid = invalid or (prefix and bool(_STATE_RESET.search(rest)))
        if invalid:
            if prefix:
                break
            records, context = [], None
            continue
        records.append(dict(index=index, actor=actor, key=(actor, rest), removed=core))
    return tokens, records


def _apply_literal(text, keys, states, names, scope, segment, *, context=None):
    header, _ = _body(text)
    tokens, records = _literal_records(text, names, scope, prefix=True, states=states, context=context)
    audit, consumed, anchor = [], 0, None
    for record in records:
        if consumed >= len(keys) or record['key'] != keys[consumed]:
            break
        index, actor = record['index'], record['actor']
        static = _static(actor, states.get(actor) or {})
        if static == actor:
            static = actor + '的面部近景'
        delimiter = re.search(r'[' + _CUT + r']*$', tokens[index]).group()
        replacement = static
        if anchor and anchor[1] == actor:
            tokens[index] = ''
            replacement = ''
        else:
            tokens[index] = re.match(r'\s*', tokens[index]).group() + static + delimiter
            anchor = (index, actor)
        audit.append(dict(method='suppress_exact_tail_head', source_kind='planned_source', actor=actor,
                          removed=record['removed'], replacement=replacement, source_segment=segment))
        consumed += 1
    body = ''.join(tokens)
    if consumed:
        body = re.sub(r'[，,；;](\s*)$', r'。\1', body)
    return header + body, audit, consumed


def _literal_overlap(current, previous, states, names, scope, segment):
    _, tail = _literal_records(_source_text(previous), names, _scope(previous, names))
    _, head = _literal_records(str(current.get('text') or ''), names, scope, prefix=True, states=states)
    old_keys, new_keys = [r['key'] for r in tail], [r['key'] for r in head]
    count = next((n for n in range(min(len(old_keys), len(new_keys)), 0, -1)
                  if old_keys[-n:] == new_keys[:n]), 0)
    if not count:
        return current, []
    keys = new_keys[:count]
    result = copy.deepcopy(current)
    result['text'], text_audit, _ = _apply_literal(str(current.get('text') or ''), keys, states, names, scope, segment)
    blocks, block_audit, consumed = (result.get('director') or {}).get('blocks') or [], [], 0
    for block in blocks:
        if consumed >= count:
            break
        if not isinstance(block, list) or len(block) < 3 or not isinstance(block[2], str):
            return current, [dict(method='preserve_unsynchronized_overlap', source_kind='unknown')]
        # Whole-text matching already bound an implicit continuation to this
        # named actor; retain that binding when a time block splits the clause.
        context = keys[consumed][0] if consumed else None
        body, events, used = _apply_literal(block[2], keys[consumed:], states, names, scope, segment, context=context)
        block[2] = body
        block_audit.extend(events)
        consumed += used
        if not used:
            break
    if blocks and (consumed != count or [(a['actor'], a['removed']) for a in block_audit] !=
                    [(a['actor'], a['removed']) for a in text_audit]):
        return current, [dict(method='preserve_unsynchronized_overlap', source_kind='unknown')]
    return result, text_audit


def trim_slice(current, previous, characters, *, verified_start_text=''):
    """Return a deep copy plus audit; never mutate stored story or observations."""
    result = copy.deepcopy(current)
    if not isinstance(current, dict):
        return result, []
    if not isinstance(previous, dict):
        if not verified_start_text:
            return result, []
        previous = dict(scene_hint=_scene(current))
    scene = _scene(current)
    if not scene or scene != _scene(previous):
        return result, [dict(method='preserve_boundary', source_kind='unknown', reason='different_or_unknown_scene')]
    current_body = _body(str(current.get('text') or _source_text(current)))[1]
    if (_JUMP.search(current_body[:45]) or current.get('time_jump') or
            (current.get('director') or {}).get('time_jump')):
        return result, [dict(method='preserve_boundary', source_kind='unknown', reason='time_jump')]
    names = [name for name in _names(characters) if name]
    if not names:
        return result, []
    states = _planned(previous, names)
    _observed(states, str(verified_start_text or ''), names)
    segment = previous.get('no', previous.get('index', previous.get('id')))
    scope = _scope(current, names)
    original_text = str(current.get('text') or '')
    new_text, text_audit, _ = _trim_text(original_text, states, names, scope, segment)
    blocks = (result.get('director') or {}).get('blocks') or []
    block_audit, closed = [], False
    for block in blocks:
        if closed or not isinstance(block, list) or len(block) < 3 or not isinstance(block[2], str):
            continue
        new, events, closed_here = _trim_text(block[2], states, names, scope, segment)
        block[2] = new
        block_audit.extend(events)
        # A block with no leading repeated action is the first new event. It
        # closes the prefix window for later blocks, including dialogue blocks.
        closed = closed_here
    suppressed = lambda rows: [(a['actor'], a['removed']) for a in rows if a['method'] == 'suppress_repeated_prefix']
    if blocks and suppressed(text_audit) != suppressed(block_audit):
        return copy.deepcopy(current), [dict(method='preserve_unsynchronized_source', source_kind='unknown',
                                             reason='text_and_blocks_prefix_differ', source_segment=segment)]
    result['text'] = new_text
    result, literal_audit = _literal_overlap(result, previous, states, names, scope, segment)
    return result, (text_audit if original_text else block_audit) + literal_audit
