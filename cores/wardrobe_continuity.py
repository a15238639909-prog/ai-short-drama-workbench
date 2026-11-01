"""Versioned, evidence-backed wardrobe state independent of reference identity."""
import copy
import json
import re


POLICY = 'confirmed-wardrobe-state-v1'
MARKER = 'Wardrobe continuity (' + POLICY + '):'
FIELDS = ('subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
          'overall_soundscape', 'non_diegetic_music')
SLOTS = ('outerwear', 'top', 'bottom', 'outfit', 'shoes', 'accessory')
MAX_CONTRACT_CHARS = 6000
_UNKNOWN = {'', 'unknown', 'n/a', 'none', '未知', '不明', '无法确认'}


def validate_policy(value):
    if value not in (None, '', POLICY):
        raise ValueError('不支持的服装连续性版本，未修改原项目或任务')
    return value


def enabled(record):
    return isinstance(record, dict) and record.get('wardrobe_policy') == POLICY


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _known(value):
    return bool(_text(value)) and _text(value).lower() not in _UNKNOWN


def _people(characters):
    people, duplicates = {}, set()
    for row in characters or []:
        if (not isinstance(row, dict)
                or row.get('role') in ('scene', 'scene_anchor', 'previous_frame', 'prop', 'environment')
                or row.get('kind') in ('scene', 'prop', 'environment')):
            continue
        identity = _text(row.get('character_id') or row.get('id') or row.get('name'))
        if not _known(identity):
            continue
        if identity in people:
            duplicates.add(identity)
        people[identity] = dict(character_id=identity, name=_text(row.get('name')) or identity)
    return {identity: row for identity, row in people.items() if identity not in duplicates}


def _resolve(value, people):
    value = _text(value)
    if value in people:
        return value
    matches = [identity for identity, row in people.items() if value and value == row['name']]
    return matches[0] if len(matches) == 1 else None


def initial_state(characters, reference_observation=None):
    people = _people(characters)
    state = dict(policy=POLICY, last_segment=0, characters={identity: dict(name=row['name'], slots={})
                 for identity, row in people.items()})
    for card in characters or []:
        if not isinstance(card, dict):
            continue
        identity = _resolve(card.get('character_id') or card.get('id') or card.get('name'), people)
        clothing = _text(card.get('clothing'))
        if identity and _known(clothing):
            # A written design is an initial baseline, never an observed event.
            state['characters'][identity]['baseline_design'] = clothing
    return state


_GARMENT_KINDS = {
    'outerwear': (r'外套|外衣|outerwear', r'大衣|\bcoat\b', r'风衣|trench coat',
                  r'夹克|皮衣|jacket', r'雨衣|raincoat', r'披风|斗篷|cloak|cape'),
    'top': (r'上衣|上装|\btop\b', r'衬衫|衬衣|\bshirt\b|blouse', r'[Tt]恤|t-shirt',
            r'背心|马甲|\bvest\b', r'毛衣|针织衫|sweater', r'卫衣|hoodie'),
    'bottom': (r'下装|\bbottoms?\b', r'长裤|短裤|裤子|牛仔裤|trousers|pants|shorts|jeans',
               r'半身裙|短裙|长裙|(?<!连衣)裙|\bskirt\b', r'内裤|底裤|underwear'),
    'outfit': (r'衣服|衣物|衣装|服装|套装|clothes|outfit', r'连体服|连衣裙|jumpsuit|\bdress\b',
               r'制服|校服|工作服|运动服|病号服|uniform', r'宇航服|太空服|防护服|spacesuit',
               r'长袍|道袍|袈裟|汉服|旗袍|和服|\brobe\b', r'盔甲|铠甲|皮甲|\barmou?r\b'),
    'shoes': (r'鞋|鞋履|footwear|shoes', r'靴|boots'),
    'accessory': (r'配饰|饰品|accessor', r'帽|\bhat\b|\bcap\b', r'围巾|scarf',
                  r'手套|gloves', r'眼镜|glasses|goggles', r'项链|necklace',
                  r'手表|腕表|\bwatch\b', r'腰带|皮带|\bbelt\b', r'袜|socks'),
}


def _garment_matches(event, object_text):
    """A hat action cannot authorize an unrelated coat or another accessory."""
    slot, item = event.get('slot'), _text(event.get('item'))
    patterns = _GARMENT_KINDS.get(slot, ())
    item_kinds = {i for i, pattern in enumerate(patterns) if re.search(pattern, item, re.I)}
    source_kinds = {i for i, pattern in enumerate(patterns) if re.search(pattern, object_text, re.I)}
    if item_kinds and source_kinds:
        return bool(item_kinds & source_kinds or 0 in item_kinds or 0 in source_kinds)
    if any(re.search(pattern, item, re.I) for kinds in _GARMENT_KINDS.values() for pattern in kinds):
        return False  # A recognized garment cannot be filed in another slot.
    # Explicitly named fictional garments remain usable without a vocabulary list.
    return len(item) >= 2 and item.casefold() in object_text.casefold()


def _source_allows(event, source, identity=None, people=None):
    """Written action is a prerequisite, not proof that the action happened."""
    if source is None:
        return True  # Keep the API for existing independently verified callers.
    if not isinstance(source, str):
        return False
    source = re.sub(r'<d>.*?</d>|“[^”]*”|「[^」]*」|"[^"\n]*"', '', source, flags=re.S)
    patterns = dict(remove=r'脱下|脱掉|脱去|解下|卸下|摘下|扯下|剥下|remove|take off',
        put_on=r'穿上|穿好|披上|戴上|套上|put on|dress',
        replace=r'换上|换成|换衣|换装|更衣|change.*(?:clothes|outfit)',
        damage=r'撕裂|撕开|撕破|扯破|扯裂|烧破|破损|torn|tear|rip')
    pattern = patterns.get(event.get('action'))
    if not pattern:
        return False
    people = people or {}
    for change in re.finditer(pattern, source, re.I):
        # Only an affirmative action in this sentence can authorize a change.
        # Commas can retain the same grammatical actor; a new sentence cannot.
        sentence_start = max([m.end() for m in re.finditer(r'[。！？!?；;\n]', source[:change.start()])] or [0])
        prefix = source[sentence_start:change.start()]
        clause_prefix = re.split(r'[，,]', prefix)[-1]
        affirmative = re.sub(r'不得不|不能不', '', clause_prefix)
        if (re.search(r'(?:没有|并未|尚未|还未|未曾|不曾|未能|没能|不能|不要|不肯|不愿|拒绝|没|未|不)'
                      r'(?:再|真的|完全|成功|立即|立刻|马上|亲手|动手|继续|去|将|要|想|能|会|敢|肯|地|\s)*$', affirmative)
                or re.search(r'准备|打算|想要?|计划|试图|尝试|试着|将要|正要|即将|如果|假如|一旦|(?:^|[，,])若', affirmative)
                or re.search(r'\b(?:not|never|without|intend\w*|plan\w*|try|tries|trying|will|would|if)\b', affirmative, re.I)):
            continue
        end = re.search(r'[，,。！？!?；;\n]', source[change.end():])
        suffix = source[change.end():change.end() + end.start()] if end else source[change.end():]
        if re.search(r'的(?:念头|想法|打算|冲动)|之前|以前', suffix):
            continue
        suffix = re.split(r'然后|接着|随后|之后|并|交给|放在|放到|拿起|握住|捡起|看着|转身|走向|露出', suffix, maxsplit=1)[0]
        object_text = suffix
        if re.search(r'把|将|被', clause_prefix) or event.get('action') == 'damage':
            object_text = clause_prefix + suffix
        if not _garment_matches(event, object_text):
            # Bare 更衣/换衣/换装 refers to a whole outfit, not a chosen layer.
            if not (event.get('action') == 'replace' and event.get('slot') == 'outfit'
                    and change.group() in ('更衣', '换衣', '换装')):
                continue
        mentions = [(match.start(), key) for key, person in people.items()
                    for match in re.finditer(re.escape(person['name']), prefix) if person.get('name')]
        owner = max(mentions)[1] if mentions else (next(iter(people)) if len(people) == 1 else None)
        if owner == identity:
            return True
    return False


def _frame_ids(frames):
    values = [row.get('frame_index') if isinstance(row, dict) else row for row in frames or ()]
    return sorted({value for value in values if isinstance(value, int) and not isinstance(value, bool) and value >= 0})


def advance(previous, *, characters, endpoint=None, sequence=None, segment=0, source_events=None,
            evidence_frames=()):
    people = _people(characters)
    state = (copy.deepcopy(previous) if isinstance(previous, dict) and previous.get('policy') == POLICY
             and isinstance(previous.get('characters'), dict) else initial_state(characters))
    for identity, row in people.items():
        state['characters'].setdefault(identity, dict(name=row['name'], slots={}))
    sequence = sequence if isinstance(sequence, dict) else {}
    if sequence.get('status') == 'unavailable' or sequence.get('wardrobe_evidence_scope') != 'transition':
        return state
    events = sequence.get('wardrobe_events')
    if not isinstance(events, list):
        return state
    frames = _frame_ids(evidence_frames)
    if len(frames) < 2:
        return state
    for event in events:
        if not isinstance(event, dict) or event.get('certainty') != 'observed':
            continue
        identity = _resolve(event.get('character_id'), people)
        action, slot = event.get('action'), event.get('slot')
        if not identity or action not in ('remove', 'put_on', 'replace', 'damage') or slot not in SLOTS:
            continue
        if not _source_allows(event, source_events, identity, people):
            continue
        if not all(_known(event.get(field)) for field in ('item', 'evidence', 'before', 'after')):
            continue
        before_frame, after_frame = event.get('before_frame'), event.get('after_frame')
        if (isinstance(before_frame, bool) or isinstance(after_frame, bool)
                or not isinstance(before_frame, int) or not isinstance(after_frame, int)
                or before_frame not in frames or after_frame not in frames or before_frame >= after_frame):
            continue
        if any(len(_text(event.get(field))) > 512 for field in ('item', 'evidence', 'before', 'after', 'holder', 'location')):
            continue
        if _text(event['before']) == _text(event['after']):
            continue
        person = state['characters'][identity]
        if action == 'replace' and slot == 'outfit':
            person['slots'] = {key: value for key, value in person['slots'].items()
                               if key in ('shoes', 'accessory')}
            person['outfit_replaced'] = True
        person['slots'][slot] = dict(status={'remove': 'removed', 'damage': 'damaged'}.get(action, 'worn'),
            item=_text(event['item']), holder=_text(event.get('holder')) or 'unknown',
            location=_text(event.get('location')) or 'unknown', segment=int(segment or 0),
            evidence=_text(event['evidence']), before=_text(event['before']), after=_text(event['after']),
            before_frame=before_frame, after_frame=after_frame)
        state['last_segment'] = int(segment or 0)
    return state


def prompt_contract(state, bindings):
    state = state if isinstance(state, dict) and state.get('policy') == POLICY else {}
    people = _people(bindings)
    rows = []
    for identity, person in (state.get('characters') or {}).items():
        if identity not in people or not isinstance(person, dict):
            continue
        slots = {slot: {key: value[key] for key in ('status', 'item', 'holder', 'location', 'segment') if key in value}
                 for slot, value in (person.get('slots') or {}).items() if slot in SLOTS and isinstance(value, dict)}
        baseline = _text(person.get('baseline_design')) if not person.get('outfit_replaced') else ''
        if not slots and not baseline:
            continue
        binding = next((row for row in bindings if isinstance(row, dict)
                        and _resolve(row.get('character_id') or row.get('id') or row.get('name'), people) == identity), {})
        rows.append(dict(character_id=identity, name=people[identity]['name'], subject=binding.get('subject'),
            reference_clothing_baseline=not bool(person.get('outfit_replaced')), slots=slots))
        if baseline:
            rows[-1]['baseline_design'] = baseline
    contract = dict(policy=POLICY, characters=rows)
    if len(json.dumps(contract, ensure_ascii=False)) > MAX_CONTRACT_CHARS:
        raise ValueError('已确认穿戴状态超过安全上下文额度；未截断或恢复人设图旧衣，请缩小当前镜头人物范围')
    return contract


def has_observed_changes(contract):
    return any(row.get('slots') for row in (contract or {}).get('characters') or [] if isinstance(row, dict))


def authority_rules():
    return ('人物身份、面部与稳定外形仍按各自人设图；人设图中的衣服仅是初始基线，不是永久穿戴。'
        'wardrobe_state记录已被实际视频前后画面证实的穿戴变化，下一段开场延续它：脱掉的外套不能自动穿回，'
        '换装后不能恢复原图旧衣。遮挡、离场、观察失败或本段未提衣服时保留最后已确认状态。'
        '只延续状态中当前出镜人物的衣物，不增加人物。未发生变化的层可以沿用参考图；'
        '整套换装已替代的参考衣物不得重新叠加。衣物的去向、内搭和配饰看不清就保持unknown，不补造。'
        '本段新脱穿或换装按动作顺序发生，不能提前应用到第一帧；剧情计划不能冒充实拍完成。'
        'baseline_design是人物卡的初始衣装文字，仅用于尚未发生变化的层；slots中的已确认变化优先。'
        'damaged表示原衣物在画面中已破损，保留破损状态，不补成完好的新衣。'
        '单帧发色衣色差异或随机外观漂移不是换装证据，不改变脸或身份。')


def model_state(state, bindings):
    contract = prompt_contract(state, bindings)
    # Resolve only explicitly evidenced outer-layer replacements before the
    # transport contract omits long before/after observations. Stored baseline
    # designs and the event-merging rules remain untouched.
    raw_people = (state or {}).get('characters') or {}
    for row in contract['characters']:
        baseline = row.get('baseline_design')
        if baseline:
            row['baseline_design'] = '、'.join(_confirmed_outerwear_baseline(
                raw_people.get(row['character_id']) or {}, _clothing_pieces(baseline)))
    return dict(policy=POLICY, last_segment=(state or {}).get('last_segment', 0),
        characters={row['character_id']: dict(name=row['name'], slots=row['slots'],
                    **({'baseline_design': row['baseline_design']} if row.get('baseline_design') else {}),
                    outfit_replaced=not row['reference_clothing_baseline']) for row in contract['characters']})


def observation_request(request, characters, *, transition_evidence, frame_bindings=(), source_text=None):
    system, payload, schema = copy.deepcopy(request)
    people = _people(characters)
    frames = _frame_ids(frame_bindings)
    transition_evidence = bool(transition_evidence and len(frames) >= 2)
    payload['wardrobe_identity_bindings'] = list(people.values())
    payload['wardrobe_policy'] = POLICY
    if source_text is not None:
        payload['authored_clothing_action_context'] = str(source_text)
    payload['wardrobe_frame_bindings'] = [dict(image_index=index + 1,
        frame_index=row.get('frame_index') if isinstance(row, dict) else row)
        for index, row in enumerate(frame_bindings) if (row.get('frame_index') if isinstance(row, dict) else row) in frames]
    properties = schema.setdefault('properties', {})
    event_fields = {key: {'type': 'string'} for key in (
        'character_id', 'item', 'evidence', 'before', 'after', 'holder', 'location')}
    event_fields['character_id'] = dict(type='string', enum=list(people) + ['unknown'])
    event_fields.update(action={'type': 'string', 'enum': ['remove', 'put_on', 'replace', 'damage']},
        slot={'type': 'string', 'enum': list(SLOTS)}, certainty={'type': 'string', 'enum': ['observed', 'unknown']})
    for field in ('before_frame', 'after_frame'):
        event_fields[field] = dict(type=['integer', 'null'], enum=frames + [None])
    properties['wardrobe_events'] = dict(type='array', maxItems=24 if transition_evidence else 0,
        items=dict(type='object', properties=event_fields, required=list(event_fields), additionalProperties=False))
    properties['wardrobe_evidence_scope'] = dict(type='string', enum=['transition' if transition_evidence else 'single_frame'])
    required = schema.setdefault('required', [])
    for field in ('wardrobe_events', 'wardrobe_evidence_scope'):
        if field not in required:
            required.append(field)
    if transition_evidence:
        system += ('\n衣着变化只依据附图中至少两个不同实际视频时刻：wardrobe_events只记录明确脱穿/更换，'
            'before和after分别写图中可见前后证据，evidence注明实际帧及变化；不能从计划补出动作。'
            'before_frame与after_frame使用wardrobe_frame_bindings的真实frame_index且前者小于后者，不是图片序号。'
            '使用wardrobe_identity_bindings中的确切character_id，不能按左右位置、衣色或Subject编号猜身份。'
            '无法可靠绑定、仅一帧差异、遮挡或看不清则不输出事件；item写具体衣物，未知内搭/去向写unknown。'
            '其他状态字段只描述最后实际帧，不能把早先姿势当作末态。')
        if source_text is not None:
            system += ('\nauthored_clothing_action_context只用于确认剧本确实安排了脱穿、换装或破损。'
                '剧本没有衣着动作时，画面变衣只能记风险，wardrobe_events填空；'
                '剧本有动作仍需前后图片同时证实。撕裂或扯破用damage，不能写成脱掉或换成其他款式。')
    else:
        system += '\n只有单帧或参考资料，不能证明脱穿过程；wardrobe_events必须为空，不能从外观差异或剧情推断换装。'
    return system, payload, schema


def rewrite_rules(text):
    replacements = {
        '人物的脸、发型、发色、肤色、体型、服装和饰品全部按各自人设图':
            '人物身份、脸、发型、发色、肤色和体型按人设图；服装和配饰按当前已确认穿戴状态及本段实际动作',
        '人物外观完整沿用对应人设图；当前姿势、表情、持物和位置服从故事及实际状态。':
            '人物身份与面部沿用对应人设图；衣着延续已确认穿戴状态，姿势、表情、持物和位置服从当前故事。',
        'Character references define stable face, hair and wardrobe identity.':
            'Character references define stable face and hair identity; clothing follows confirmed current wardrobe state.',
        '没有原文变化依据时遵循对应人设图。':
            '没有有效变化证据时保留最后已确认穿戴，初始人设衣服不覆盖已经发生的变化。',
        '人物参考只固定身份、脸、发型、体态和服装，不永久锁定站位、姿势或持物。':
            '人物参考固定身份、脸、发型和体态；衣着延续已确认状态，不永久锁定站位、姿势或持物。',
        '此优先级只用于姿势、位置、持物和已发生的动作，人物固定外观只按对应人设图。':
            '此优先级用于实际动作状态；稳定身份按人设图，当前衣着按已确认穿戴状态延续。',
        '人物外形漂移如实记录，后续外观仍按对应人设图。':
            '身份漂移不重定义人物，已确认衣着变化继续有效，不恢复原图旧衣。',
        '人物身份、脸、发型和服装以人物设定为准；':
            '人物身份、脸和发型以人物设定为准，衣着按已确认穿戴状态延续；',
        '看不清或本地观察未知时直接遵循原图，不补写外貌。':
            '身份看不清时不猜测；衣着观察未知时延续最后已确认穿戴，不恢复已脱换的原图衣物。',
    }
    for before, after in replacements.items():
        text = text.replace(before, after)
    return text


def bindings_from_prompt(prompt, characters):
    people = _people(characters)
    result = []
    for subject, name in re.findall(r'<Subject\s+(\d+)> is ([^\n,]+), the character from <Picture\s+\d+>', prompt):
        identity = _resolve(name.strip(), people)
        if identity:
            result.append(dict(people[identity], role='character', subject=int(subject)))
    return result


def _fields(prompt):
    headers = list(re.finditer(r'(?m)^(' + '|'.join(FIELDS) + r'):[ \t]*', prompt))
    if [match[1] for match in headers] != list(FIELDS):
        raise ValueError('服装连续性要求完整的六区视频提示词')
    return {match[1]: prompt[match.end():headers[index + 1].start() if index + 1 < len(headers)
                            else len(prompt)].strip('\n') for index, match in enumerate(headers)}


def _clothing_pieces(text):
    """Split the confirmed design into garment phrases, keeping their details."""
    value, result, cursor = _text(text), [], 0
    garment = '|'.join(pattern for patterns in _GARMENT_KINDS.values() for pattern in patterns)
    garment += r'|外袍|罩袍|中衣|内衫|里衣|内袍|短衫|长衫|短褂|长褂'
    # Some clothing fields contain a full character-design worksheet. Its
    # face/body/palette headings are not another garment in every worn layer.
    # Keep any explicit garment fact even in a memory-point section.
    def clothing_only(match):
        return '；'.join(part.strip(' ；;') for part in re.split(r'[。；;\n]', match.group(1))
                        if re.search(garment, part, re.I))
    value = re.sub(r'(?m)(?:^|(?<=[；;\n]))\s*【(?:主导剪影|核心脸部特征|专属色板|唯一记忆点)】(.*?)(?=【|$)',
                   clothing_only, value, flags=re.S)
    value = re.sub(r'【(?:具体服装|服装|衣装|上装|下装|鞋履|配饰|细节)】', '', value)
    value = re.sub(r'(^|[；;\n])\s*\d+[.、]\s+(?=\D)', r'\1', value)
    value = re.sub(r'[；;\n]+', '；', value).strip(' ；;')
    for boundary in re.finditer(r'[，,、；;。\n]|\s+and\s+|以及|和|及|与', value, re.I):
        # “蓝色和白色相间的外套” is one garment, not a loose “蓝色” item.
        if (re.search(garment, value[cursor:boundary.start()], re.I)
                and re.search(garment, value[boundary.end():], re.I)):
            result.append(value[cursor:boundary.start()].strip(' ：:'))
            cursor = boundary.end()
    if value[cursor:].strip(' ：:'):
        result.append(value[cursor:].strip(' ：:'))
    return result


def _confirmed_outerwear_baseline(person, pieces):
    """Exclude a before-only old coat after an explicit cross-kind replacement."""
    slot = (person.get('slots') or {}).get('outerwear') or {}
    before, after, item = (_text(slot.get(key)) for key in ('before', 'after', 'item'))
    if (slot.get('status') != 'worn' or not all(_known(text) for text in (before, after, item))
            or before == after or not _garment_matches(dict(slot='outerwear', item=item), after)):
        return pieces
    result = []
    for piece in pieces:
        old = dict(slot='outerwear', item=piece)
        # Same-kind matching retains its existing behavior. This small repair
        # covers e.g. a trench coat replaced by a jacket, not every slot/design.
        if (_garment_matches(dict(slot='outerwear', item=item), piece)
                or not _garment_matches(old, before)):
            result.append(piece)
            continue
        remaining = [part for part in re.split(r'[，,。；;\n]', after) if _garment_matches(old, part)]
        # Mentioning the old layer without an explicit removal remains
        # ambiguous or layered wear, so it is deliberately preserved.
        if remaining and not all(re.search(r'脱下|脱掉|脱去|换下|移除|取下|替换', part) for part in remaining):
            result.append(piece)
    return result


def positive_clothing_lines(state, bindings):
    """Project internal evidence into current visible clothing, never policy prose."""
    contract = prompt_contract(state, bindings)
    people = _people(bindings)
    names = {identity: person['name'] for identity, person in people.items()}
    lines = []
    for person in contract['characters']:
        subject = person.get('subject')
        label = '<Subject %d>' % subject if type(subject) is int and subject > 0 else person['name']
        slots = person['slots']
        raw_person = ((state or {}).get('characters') or {}).get(person['character_id']) or {}
        pieces = _confirmed_outerwear_baseline(raw_person, _clothing_pieces(person.get('baseline_design')))
        # A changed layer replaces only the corresponding confirmed garment.
        # Accessory categories stay separate (a hat change preserves a watch).
        pieces = [piece for piece in pieces if not any(
            value.get('status') in ('worn', 'removed', 'damaged')
            and _garment_matches(dict(slot=slot, item=value.get('item')), piece)
            for slot, value in slots.items())]
        placement = []
        for slot, value in slots.items():
            item = _text(value.get('item'))
            if not _known(item):
                continue
            if value.get('status') == 'worn':
                pieces.append(item)
            elif value.get('status') == 'damaged':
                pieces.append(item + '（可见破损）')
            elif value.get('status') == 'removed':
                holder, location = _text(value.get('holder')), _text(value.get('location'))
                if holder in names and _known(location):
                    placement.append('%s的%s拿着%s' % (names[holder], location, item))
                elif _known(location):
                    placement.append('%s位于%s' % (item, location))
        pieces = list(dict.fromkeys(pieces))
        if pieces:
            lines.append(label + '：当前衣着为' + '、'.join(pieces) + '。')
        if placement:
            lines.append(label + '：持物与放置为' + '；'.join(placement) + '。')
    return lines


def _positive_reference_retention(fields, bindings):
    lines, seen = [], set()
    for row in bindings:
        if not isinstance(row, dict):
            continue
        role = row.get('role')
        subject, picture = row.get('subject'), row.get('picture')
        if role == 'character' and type(subject) is int:
            line = '<Subject %d>：面容、发型与体型沿用人物参考。' % subject
        elif role in ('scene', 'scene_anchor') and type(picture) is int:
            line = '<Picture %d>：提供当前地点的空间结构、陈设与现场光线。' % picture
        elif role == 'previous_frame' and type(picture) is int:
            line = '<Picture %d>：提供首镜的可见姿态、位置与持物。' % picture
        else:
            continue
        if line not in seen:
            lines.append(line); seen.add(line)
    # Retain caller-written positive scene facts, while replacing generated
    # reference and bookkeeping lines with the concise presentation above.
    old = fields['retention_analysis'].split(MARKER, 1)[0]
    for line in old.splitlines():
        if (not line.strip() or re.match(r'^\s*<(?:Subject|Picture)\s+\d+>', line)
                or line.startswith(('Identity authority (', 'Current wardrobe contract:',
                    '人物身份、脸、发型', '人物的脸、发型', '场景参考用于其对应地点', '未提供场景参考时'))):
            continue
        lines.append(line)
    return lines


def apply_prompt(prompt, state=None, bindings=()):
    fields = _fields(prompt)
    # All decisions remain in state and the observer/compiler validators. The
    # video model receives only reference roles and their current visible result.
    lines = _positive_reference_retention(fields, bindings)
    lines.extend(positive_clothing_lines(state, bindings))
    fields['retention_analysis'] = '\n'.join(lines) or '人物与环境按本镜头的具体画面描述呈现。'
    definitions = []
    for line in fields['subject_definitions'].splitlines():
        if line.startswith(('人物身份、脸、发型', '人物的脸、发型',
                            'Character references define stable face, hair and wardrobe identity.')):
            continue
        character = re.match(r'^(<Subject\s+\d+> is [^\n]+?, the character from <Picture\s+\d+>\.)', line)
        environment = re.match(r'^(<Subject\s+\d+> is [^\n]+?, the environment(?: from <Picture\s+\d+>)?\.)', line)
        if character:
            voice = (re.search(r'Voice reference:\s*([^\n]+?)\.(?:\s|$)', line)
                     or re.search(r'音色为([^\n]+?)。', line))
            line = character[1] + ' 面容、发型与体型沿用对应人设图。'
            if voice and _known(voice[1]):
                line += ' 音色为' + voice[1].strip() + '。'
        elif environment:
            line = environment[1] + ' 本镜头的空间结构、陈设与现场美术。'
        line = line.replace('人物外观完整沿用对应人设图；当前姿势、表情、持物和位置服从故事及实际状态。',
                            '面容、发型与体型沿用对应人设图。')
        line = line.replace('人物身份与面部沿用对应人设图；衣着延续已确认穿戴状态，姿势、表情、持物和位置服从当前故事。',
                            '面容、发型与体型沿用对应人设图。')
        line = line.replace('环境参考只约束当前地点的固定结构及现场美术；可移动物件、人物及动作服从当前故事状态。',
                            '本镜头的空间结构、陈设与现场美术。')
        definitions.append(line)
    fields['subject_definitions'] = '\n'.join(definitions)
    return '\n\n'.join(key + ':\n' + fields[key] for key in FIELDS)


def apply_compiled(compiled, state=None, bindings=()):
    result = copy.deepcopy(compiled)
    prompt = '\n\n'.join(key + ':\n' + result['fields'][key] for key in FIELDS)
    result['prompt'] = apply_prompt(prompt, state, bindings)
    result['fields'] = _fields(result['prompt'])
    return result


def attach_reference(prompt, picture):
    fields = _fields(prompt)
    note = ('<Picture %d> is the actual final frame of the preceding adopted video. '
        '图中可见衣物提供当前穿戴细节。' % picture)
    fields['subject_definitions'] += '\n' + note
    fields['retention_analysis'] += '\n<Picture %d>：weak_reference - 当前可见衣物细节。' % picture
    return '\n\n'.join(key + ':\n' + fields[key] for key in FIELDS)
