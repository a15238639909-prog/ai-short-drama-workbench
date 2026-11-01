"""Positive opening wardrobe projected from earlier source, never observed progress.

Only build_start_plan's earlier slices establish planned changes.  The current
slice supplies visibility context; its own changes remain actions to be filmed.
Verified wardrobe transition records can override the plan without admitting
single-frame appearance drift.  No source text or caller state is mutated.
"""
import copy
import re

from . import wardrobe_continuity as wardrobe


VERSION = 'source-wardrobe-plan-v1'
_EXTRA = {'outerwear': r'外袍|罩袍', 'top': r'中衣|内衫|里衣|内袍'}
_KINDS = {slot: '(?:' + '|'.join(patterns) +
          ('|' + _EXTRA[slot] if slot in _EXTRA else '') + ')'
          for slot, patterns in wardrobe._GARMENT_KINDS.items()}
_GARMENT = re.compile('|'.join(_KINDS.values()), re.I)
_CHANGE = re.compile(r'(?P<remove>脱下|脱掉|脱去|褪下|褪去|解下|卸下|摘下|摘掉|取下)|'
                     r'(?P<replace>换上|换成|换为|改穿)|'
                     r'(?P<put_on>穿上|穿好|披上|套上|戴上|戴好)')
_NOT_DONE = re.compile(r'并未|从未|未曾|还没|还未|尚未|没有|没能|未能|不曾|不要|不能|别|'
                       r'准备|打算|计划|试图|尝试|想要|想|将要|正要|即将|如果|假如|一旦|回忆|记得|想起|'
                       r'(?:^|[，,])不(?:再|去)?$')
_MODIFIER = re.compile(r'(?:(?:深|浅|暗|亮|淡)?(?:玄青|靛青|藏青|藏蓝|米白|月白|黛青|象牙白|卡其|'
                        r'黑|白|蓝|红|绿|棕|褐|紫|黄|青|粉|银|金|灰)(?:色)?|'
                        r'长款|短款|修身|宽松|棉质|羊毛|亚麻|呢料|皮质|针织|布质|丝绸|丝质|'
                        r'深灰|浅灰|长袖|短袖|无袖|高领|圆领|旧|新|薄|厚|的)+$')
_WRAP = re.compile(r'包住|包裹(?:住)?|裹住|裹着|裹在|裹进|裹好|裹紧|包好|包紧|包着')
_GIVE = re.compile(r'递给|交给|递到|交到|递过去|交过去|递出|交出')
_TAKE = re.compile(r'接过|接住|接到')
_PLACE = re.compile(r'放在|放到|放入|放进|置于|安放在|安放于|安放入|安放进|放置在|放置于|放置入|放置进')
_CUT = r'[，,。！？!?；;\n]'


def _slot(word):
    # 外袍 is an outer layer; removing it must preserve 中衣 and trousers.
    for slot in ('outerwear', 'top', 'bottom', 'shoes', 'accessory', 'outfit'):
        if re.search(_KINDS[slot], word, re.I):
            return slot
    return None


def _pieces(value):
    result = []
    for part in wardrobe._clothing_pieces(value):
        cursor = 0
        for boundary in re.finditer(r'[，,、；;。\n]|以及|和|及|与', part):
            if _GARMENT.search(part[cursor:boundary.start()]) and _GARMENT.search(part[boundary.end():]):
                result.append(part[cursor:boundary.start()].strip(' ：:'))
                cursor = boundary.end()
        if part[cursor:].strip(' ：:'):
            result.append(part[cursor:].strip(' ：:'))
    return result


def _matches(slot, item, piece):
    if slot in _EXTRA and (re.search(_EXTRA[slot], item) or re.search(_EXTRA[slot], piece)):
        return bool(re.search(_EXTRA[slot], item) and re.search(_EXTRA[slot], piece))
    return wardrobe._garment_matches(dict(slot=slot, item=item), piece)


def _actions(segment, people):
    """Exclude formal dialogue, quoted requests and DID-only blocks."""
    blocks = (segment.get('director') or {}).get('blocks') or []
    text = '\n'.join(str(block[2]) for block in blocks
                     if isinstance(block, (list, tuple)) and len(block) >= 3)
    if not blocks:
        text = str(segment.get('text') or '')
    names = '|'.join(re.escape(row['name']) for row in people.values())
    text = re.sub(r'<d>.*?</d>|“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"', '', text, flags=re.S)
    text = re.sub(r'\[D\w+\][^\n]*', '', text)
    if names:
        text = re.sub(r'(?m)^\s*(?:' + names + r')\s*[：:][^\n]*', '', text)
    return text


def _item(phrase, worn):
    matches = list(_GARMENT.finditer(phrase))
    if not matches:
        return None
    match = matches[0]
    word = match.group()
    slot = _slot(word)
    prefix = _MODIFIER.search(phrase[:match.start()])
    item = (prefix.group() if prefix else '') + word
    if not prefix:
        baselines = [piece for piece in worn if _matches(slot, item, piece)]
        if len(baselines) == 1:
            item = baselines[0]
    return slot, item, word


def _remove(person, slot, item):
    matched = [piece for piece in person['worn'] if _matches(slot, item, piece)]
    person['worn'] = [piece for piece in person['worn'] if piece not in matched]
    return matched


def _source_change(plan, identity, action, slot, item, segment, clause):
    person = plan['characters'][identity]
    if (action == 'remove' and not any(_matches(slot, item, piece) for piece in person['worn']) and
            any(prop['original_owner'] == identity and _matches(slot, item, prop['item'])
                for prop in plan['props'])):
        # "把怀里的外套解下递过去" can loosen an existing bundle.  It does
        # not recreate another worn coat or discard the known wrapped object.
        return
    if action == 'replace' and slot == 'outfit':
        person['worn'] = [piece for piece in person['worn'] if _slot(piece) in ('shoes', 'accessory')]
    elif action == 'replace' and slot != 'accessory':
        person['worn'] = [piece for piece in person['worn'] if _slot(piece) != slot]
    else:
        _remove(person, slot, item)
    if action != 'remove':
        person['worn'].append(item)
    person['worn'] = list(dict.fromkeys(person['worn']))
    person['changed'] = True
    person['source_kind'] = 'planned_source'
    person['changes'].append(dict(action=action, slot=slot, item=item, source_segment=segment,
                                  source_clause=clause, source_kind='planned_source'))
    if action == 'remove':
        plan['props'].append(dict(item=item, slot=slot, original_owner=identity,
                                  holder=None, object='', location='', source_segment=segment,
                                  source_kind='planned_source'))
    else:
        # Rewearing a removed garment ends its role as a carried object.
        plan['props'] = [prop for prop in plan['props']
                         if prop['original_owner'] != identity or not _matches(slot, item, prop['item'])]


def _object(text, garment_word=''):
    text = text.strip(' 的这那一只个件条套在到进里外，,。；;')
    text = re.sub(r'^(?:用|把|将|着|住|得|好|紧|着的|在|进|了)+', '', text)
    text = re.sub(r'(?:严严实实|稳妥地|牢牢|紧紧|轻轻|慢慢|随即|随后|又|并)', '', text)
    text = re.sub(r'(?:(?:仔细|小心|认真|细心|迅速|稳妥|严严实实|牢牢|紧紧|轻轻|慢慢|稳稳)(?:地)?)+$', '', text)
    text = re.split(r'包住|包裹|裹住|裹着|裹在|裹进|裹好|裹紧|包好|包紧|包着|的(?:外套|外袍|衣料)|'
                    r'递给|交给|递过去|交过去|抱在|捧在|扣紧|确保|确认', text)[0]
    text = text.strip(' 的这那一只个件条套在到进里外，,。；;')
    if garment_word:
        text = text.replace(garment_word, '').strip(' 的这那一只个件条套用将把')
    if re.fullmatch(r'(?:严实|稳固|完整|完好|牢固)(?:后|时|了|无误)?', text):
        return ''
    return text if 1 < len(text) <= 24 and not re.search(r'他|她|自己|身体|怀中|怀里', text) else ''


def _prop_for_clause(plan, clause, identity):
    candidates = [prop for prop in plan['props'] if prop['item'] in clause or
                  (_GARMENT.search(prop['item']) and _GARMENT.search(prop['item']).group() in clause) or
                  (prop['object'] and prop['object'] in clause)]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        owned = [prop for prop in plan['props'] if prop['holder'] == identity or
                 (prop['holder'] is None and prop['original_owner'] == identity)]
        if len(owned) == 1:
            return owned[0]
    return None


def _receipt_identifies(clause, prop):
    """An explicitly different received object cannot take the pending bundle."""
    receipt = _TAKE.search(clause)
    if not receipt:
        return False
    obj = clause[receipt.end():].strip(' 了的这那一个件只方')
    if not obj or obj in ('它', '包裹', '布包', '衣料'):
        return True
    names = [prop.get('object') or '', prop.get('item') or '']
    garment = _GARMENT.search(prop.get('item') or '')
    if garment:
        names.append(garment.group())
    return any(name and name in obj for name in names)


def _scan(plan, text, segment, people):
    owner = None
    last_subject = plan.get('_last_source_subject')
    pending_props = [prop for prop in plan['props'] if prop.get('awaiting_receiver')]
    pending = pending_props[0] if len(pending_props) == 1 else None
    last_removed = None
    names = {row['name']: key for key, row in people.items()}
    for token in re.finditer(r'[^，,。！？!?；;\n]+(?P<end>[，,。！？!?；;\n]*)', text):
        clause = token.group().strip(' ，,。！？!?；;\n')
        mentions = [(match.start(), key) for name, key in names.items()
                    for match in re.finditer(re.escape(name), clause)]
        direct_actor = next((key for pos, key in sorted(mentions) if
                             not re.search(r'给|到|向|替|帮|为|看着|看见', clause[:pos])), None)
        if direct_actor:
            owner = direct_actor
            last_subject = direct_actor if len(mentions) == 1 else None
        elif len(mentions) == 1 and clause.startswith(('他', '她')):
            # Named recipient after a pronoun is not a new grammatical actor.
            pass
        elif not mentions and clause.startswith(('他', '她', '其')) and last_subject:
            owner = last_subject
        if not owner and len(people) == 1 and clause.startswith(('他', '她', '其')):
            owner = next(iter(people))
        if re.match(r'^(?:两人|三人|众人|他们|她们)', clause):
            owner = None
            last_subject = None
        not_done = bool(_NOT_DONE.search(clause))
        if owner and not not_done:
            events = list(_CHANGE.finditer(clause))
            for event_index, event in enumerate(events):
                action = event.lastgroup
                suffix = clause[event.end():events[event_index + 1].start() if event_index + 1 < len(events) else len(clause)]
                found = _item(suffix, plan['characters'][owner]['worn'])
                if not found:
                    prefix = clause[:event.start()]
                    found = _item(re.split(r'把|将', prefix)[-1], plan['characters'][owner]['worn'])
                if not found:
                    continue
                slot, item, word = found
                # Reuse the existing negative/conditional/source-owner gate.
                # It lacks 褪下/外袍; normalize only for that validation copy.
                gate = clause.replace('褪下', '脱下').replace('褪去', '脱去')
                gate = gate.replace('外袍', '外套').replace('中衣', '衬衫').replace('内衫', '衬衫').replace('里衣', '衬衫')
                gate_item = item.replace('外袍', '外套').replace('中衣', '衬衫').replace('内衫', '衬衫').replace('里衣', '衬衫')
                gate = people[owner]['name'] + gate
                if not wardrobe._source_allows(dict(action=action, slot=slot, item=gate_item), gate, owner, people):
                    continue
                _source_change(plan, owner, action, slot, item, segment, clause)
                if action == 'remove':
                    last_removed = next((prop for prop in reversed(plan['props'])
                                         if prop['original_owner'] == owner and _matches(slot, item, prop['item'])), None)
            prop = _prop_for_clause(plan, clause, owner)
            wrap = _WRAP.search(clause)
            # Attributive "裹着/裹好的玉匣" and checking a noun "包裹"
            # describe an existing bundle, not a new wrapping/ownership event.
            wrapping = (wrap and wrap.group() not in ('裹着', '包着')
                        and not re.match(r'^(?:了)?的|^着|^好的', clause[wrap.end():])
                        and not re.search(r'(?:确认|检查|查看|检视|确保)\s*$', clause[:wrap.start()]))
            if wrapping:
                prop = prop or (last_removed if last_removed and last_removed['original_owner'] == owner else None)
                if prop:
                    garment = _GARMENT.search(prop['item']).group()
                    prefix = clause[:wrap.start()]
                    obj = _object(re.split(r'把|将', prefix)[-1], garment) if re.search(r'把|将', prefix) else ''
                    obj = obj or _object(clause[wrap.end():], garment)
                    if obj:
                        prop.update(object=obj, holder=owner, source_segment=segment)
            give = _GIVE.search(clause)
            if give and prop:
                recipients = [key for name, key in names.items()
                              if name in clause[give.start():] and key != owner]
                if len(recipients) == 1:
                    prop.update(holder=recipients[0], location='', source_segment=segment)
                    prop.pop('awaiting_receiver', None)
                    pending = None
                elif give.group() in ('递过去', '交过去', '递出', '交出'):
                    # Destination is unresolved until the next explicitly named 接过.
                    prop['holder'] = None
                    prop['awaiting_receiver'] = True
                    pending = prop
            if pending and owner and _receipt_identifies(clause, pending):
                pending.update(holder=owner, location='', source_segment=segment)
                pending.pop('awaiting_receiver', None)
                pending = None
            place = _PLACE.search(clause)
            if place and prop:
                location = re.split(r'并|然后|接着|随后', clause[place.end():])[0].strip(' 的了')
                if 1 < len(location) <= 32:
                    prop.update(holder=None, location=location, source_segment=segment)
        # Carry an owner across a comma or an explicit personal pronoun, but do
        # not guess bare subjectless actions after a separate full sentence.
        if re.search(r'[。！？!?；;\n]', token['end']):
            owner = None
    plan['_last_source_subject'] = last_subject


def _merge_verified(plan, observed_state):
    state = observed_state or {}
    if isinstance(state, dict) and isinstance(state.get('wardrobe_state'), dict):
        state = state['wardrobe_state']
    if not isinstance(state, dict) or state.get('policy') != wardrobe.POLICY:
        return
    for identity, actual in (state.get('characters') or {}).items():
        if identity not in plan['characters'] or not isinstance(actual, dict):
            continue
        person = plan['characters'][identity]
        for slot, value in (actual.get('slots') or {}).items():
            if slot not in wardrobe.SLOTS or not isinstance(value, dict):
                continue
            before, after = value.get('before_frame'), value.get('after_frame')
            if (type(before) is not int or type(after) is not int or before >= after or
                    not all(wardrobe._known(value.get(key)) for key in ('item', 'evidence', 'before', 'after')) or
                    value['before'] == value['after']):
                continue
            source_segment = max((change['source_segment'] for change in person['changes']
                                  if change['slot'] == slot), default=0)
            if value.get('segment', 0) < source_segment:
                continue  # An older observed event is not a fresh endpoint observation.
            status, item = value.get('status'), value['item']
            if status not in ('worn', 'removed', 'damaged'):
                continue
            if actual.get('outfit_replaced') and slot == 'outfit':
                person['worn'] = [piece for piece in person['worn'] if _slot(piece) in ('shoes', 'accessory')]
            _remove(person, slot, item)
            if status != 'removed':
                person['worn'].append(item + ('（可见破损）' if status == 'damaged' else ''))
            person.update(changed=True, source_kind='observed_transition')
            plan['audit'].append(dict(method='verified_transition_override', actor=person['name'],
                                      source_kind='observed_transition', slot=slot, status=status,
                                      item=item, source_segment=value.get('segment', 0)))


def _merge_stored_opening(plan, observed_state):
    """Keep an existing persisted wardrobe contract when source has no change.

    Legacy saved contracts predate per-frame transition records. They must not
    be lost to a new reference baseline, nor override an explicit source change.
    """
    state = observed_state or {}
    if isinstance(state, dict) and isinstance(state.get('wardrobe_state'), dict):
        state = state['wardrobe_state']
    if not isinstance(state, dict) or state.get('policy') != wardrobe.POLICY:
        return
    if not isinstance(state.get('last_segment'), int) or state['last_segment'] < 1:
        return
    for identity, actual in (state.get('characters') or {}).items():
        person = plan['characters'].get(identity)
        if not person or person['changes'] or not isinstance(actual, dict):
            continue
        valid = {slot: copy.deepcopy(value) for slot, value in (actual.get('slots') or {}).items()
                 if isinstance(value, dict) and value.get('status') in ('worn', 'removed', 'damaged')
                 and type(value.get('segment')) is int and 0 < value['segment'] <= state['last_segment']
                 and wardrobe._known(value.get('item'))}
        if not valid:
            continue
        saved = copy.deepcopy(state)
        saved['characters'] = {identity: dict(actual, slots=valid)}
        bindings = [dict(character_id=identity, name=person['name'], role='character')]
        for line in wardrobe.positive_clothing_lines(saved, bindings):
            _, marker, clothing = line.partition('：当前衣着为')
            if marker:
                person.update(worn=_pieces(clothing), changed=True, source_kind='stored_wardrobe_state')
                plan['audit'].append(dict(method='stored_wardrobe_opening', actor=person['name'],
                    source_kind='stored_wardrobe_state', source_segment=state['last_segment']))


def build_start_plan(slices, index, characters, *, observed_state=None):
    """Return the wardrobe *before* zero-based index, from prior explicit events.

    observed_state accepts only the existing wardrobe.advance transition ledger,
    not raw endpoint character clothing or single-frame appearance descriptions.
    """
    people = wardrobe._people(characters)
    plan = dict(version=VERSION, evidence_kind='planned_source', characters={}, props=[], audit=[],
                opening_segment=index + 1, current_text=str((slices[index] if 0 <= index < len(slices) else {}).get('text') or ''))
    for card in characters or ():
        if not isinstance(card, dict):
            continue
        identity = wardrobe._resolve(card.get('character_id') or card.get('id') or card.get('name'), people)
        if identity:
            plan['characters'][identity] = dict(name=people[identity]['name'], worn=_pieces(card.get('clothing') or ''),
                                                changed=False, source_kind='reference_baseline', changes=[])
    for segment_index, segment in enumerate(slices[:max(0, index)], 1):
        _scan(plan, _actions(segment, people), segment_index, people)
    # Preserve the source actor for pronouns in this slice's first action and
    # within-shot prefixes. model_state exposes only the public state fields.
    _merge_stored_opening(plan, observed_state)
    _merge_verified(plan, observed_state)
    for person in plan['characters'].values():
        plan['audit'].extend(dict(method='source_wardrobe_event', actor=person['name'], **change)
                             for change in person['changes'])
    return plan


def _visible_props(plan, bindings):
    people = wardrobe._people(bindings)
    text = plan.get('current_text') or ''
    return [prop for prop in plan.get('props') or []
            if prop.get('object') and (prop.get('holder') in people or prop['object'] in text)]


def advance_source(plan, text):
    """Project a source prefix within the current clip, without observation."""
    result = copy.deepcopy(plan)
    people = result.get('characters') or {}
    _scan(result, str(text or ''), result.get('opening_segment'), people)
    return result


def model_state(plan, bindings):
    """Independent planned state for Qwen; never a confirmed-wardrobe contract."""
    people = wardrobe._people(bindings)
    return dict(version=VERSION, evidence_kind='planned_source',
                characters={identity: {key: copy.deepcopy(person[key])
                                      for key in ('name', 'worn', 'changed', 'source_kind')}
                            for identity, person in (plan.get('characters') or {}).items() if identity in people},
                props=copy.deepcopy(_visible_props(plan, bindings)),
                opening_segment=plan.get('opening_segment'))


def positive_clothing_lines(plan, bindings):
    people = wardrobe._people(bindings)
    labels = {}
    for ref in bindings or ():
        if not isinstance(ref, dict):
            continue
        identity = wardrobe._resolve(ref.get('character_id') or ref.get('id') or ref.get('name'), people)
        if identity:
            labels[identity] = '<Subject %d>' % ref['subject'] if type(ref.get('subject')) is int else people[identity]['name']
    lines = []
    for identity, person in (plan.get('characters') or {}).items():
        if identity in labels and person.get('worn'):
            lines.append(labels[identity] + '：当前衣着为' + '、'.join(person['worn']) + '。')
    for prop in _visible_props(plan, bindings):
        line = prop['object'] + '外裹着' + prop['item']
        if prop.get('holder') in labels:
            line += '，由' + labels[prop['holder']] + '拿着'
        elif prop.get('location'):
            line += '，放在' + prop['location']
        lines.append(line + '。')
    return list(dict.fromkeys(lines))


def apply_compiled(compiled, plan, bindings=()):
    """Replace prior clothing projection, preserving all story actions/dialogue."""
    result = copy.deepcopy(compiled)
    fields = result['fields']
    lines, old_clothing = [], False
    for line in fields.get('retention_analysis', '').splitlines():
        quoted = bool(re.search(r'[“”「」『』"]', line))
        if not quoted and re.match(r'^\s*【(?:主导剪影|核心脸部特征|专属色板|唯一记忆点|具体服装)】', line):
            old_clothing = True
            continue
        if re.search(r'：当前衣着为|：持物与放置为', line):
            old_clothing = True
            continue
        if re.match(r'^\s*<(?:Subject|Picture)\s+\d+>', line):
            old_clothing = False
        if old_clothing and not quoted and re.match(r'^\s*(?:【|\d+[.、])', line):
            continue
        if not quoted and re.fullmatch(r'.+外裹着.+(?:，由.+拿着|，放在.+)?。', line.strip()):
            continue
        lines.append(line)
    lines.extend(positive_clothing_lines(plan, bindings))
    fields['retention_analysis'] = '\n'.join(dict.fromkeys(lines))
    result['prompt'] = '\n\n'.join(field + ':\n' + fields[field] for field in wardrobe.FIELDS)
    return result
