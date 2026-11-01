"""Lossless source and continuity contracts shared by first and later episodes.

This module never rewrites a plot or guesses a speaker from a preceding line.
Uncertain metadata is returned for bounded repair by the caller.
"""
import copy
import hashlib
import re


VERSION = 'episode-source-v2'


class ContractError(ValueError):
    def __init__(self, message, *, stage='source', code='source_contract', details=None):
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.retryable = True
        self.details = details or {}


def _aliases(chars):
    from . import oral_story
    aliases = oral_story.card_aliases(chars)
    owners = {}
    for card in chars or []:
        if not isinstance(card, dict) or not card.get('name'):
            continue
        for alias in card.get('aliases') or []:
            owners.setdefault(str(alias), set()).add(card['name'])
    for alias, names in owners.items():
        if len(names) == 1:
            aliases[alias] = next(iter(names))
    return aliases


def speech_seconds(words):
    return len(re.findall(r'[一-龥A-Za-z0-9]', str(words or ''))) / 4.5 + .5


def split_speech(words, limit=15):
    """Split for the spoken-time budget, preserving exact original text.

    Prefer written punctuation, then whitespace. A long Chinese clause without
    punctuation can continue across clips at a character boundary; it does not
    require rewriting the approved dialogue or stopping the whole story.
    """
    words = str(words or '')
    if speech_seconds(words) <= limit:
        return [words]
    fragments, cursor = [], 0
    boundaries = [match.end() for match in re.finditer(r'[，,。！？!?；;：:](?:[”」\"\']*)', words)]
    while cursor < len(words):
        if speech_seconds(words[cursor:]) <= limit:
            fragments.append(words[cursor:])
            break
        usable = [end for end in boundaries if cursor < end < len(words)
                  and speech_seconds(words[cursor:end]) <= limit]
        if not usable:
            # Advance over at least one spoken character without losing any
            # punctuation, space, or newline. The estimator and splitter use
            # the same alphabet, so the resulting fragment obeys its budget.
            hard_end, spoken = cursor, 0
            for position in range(cursor, len(words)):
                char = words[position]
                increment = bool(re.match(r'[一-龥A-Za-z0-9]', char))
                if (spoken + increment) / 4.5 + .5 > limit:
                    break
                spoken += increment
                hard_end = position + 1
            if hard_end <= cursor or spoken == 0:
                # An impossible caller budget is reported by the contract; it
                # must not cause an infinite loop or discard original words.
                return [words]
            spaces = [m.end() + cursor for m in re.finditer(r'\s+', words[cursor:hard_end])
                      if speech_seconds(words[cursor:cursor + m.end()]) > .5]
            end = spaces[-1] if spaces else hard_end
            # Preserve common Unicode combining marks/variation selectors and
            # a CRLF pair with the preceding chunk rather than bisecting them.
            while end < len(words) and re.match(r'[\u0300-\u036f\ufe00-\ufe0f]', words[end]):
                end += 1
            if end < len(words) and words[end - 1:end + 1] == '\r\n':
                end += 1
        else:
            end = usable[-1]
        fragments.append(words[cursor:end])
        cursor = end
    assert ''.join(fragments) == words
    return fragments


def _action_attached_speech(body, aliases):
    """A single explicit actor can own speech after their completed action.

    Keep the action prefix verbatim. Thoughts, signs, plans, and multiple
    possible actors remain outside this deterministic attribution.
    """
    colon = re.search('[：:]', body)
    if not colon or not aliases:
        return None
    head, speech = body[:colon.start()], body[colon.end():].strip()
    # Quoted speech already has the established attribution/classification
    # path, including collective speakers, signs and remembered quotations.
    if speech.startswith(('“', '「', '"')):
        return None
    if not speech or re.search(r'心想|心里|想起|回忆|默念|写着|刻着|贴着|标着|显示|牌上|屏幕|计划|打算|准备', head):
        return None
    labels = '|'.join(re.escape(name) for name in sorted(aliases, key=len, reverse=True))
    subject = re.match(r'^\s*(?:(?:随后|接着|这时|此时|于是)[，,]?\s*)?(' + labels + r')(?P<action>.+)$', head)
    cue = r'(?:点头|摇头|颔首|开口|说道|问道|答道|喊道|应道|回道|说|问|答|喊|回应|回答|低语|嘱咐|补充|提醒)(?:[了，,\s]*)$'
    if not subject or not re.search(cue, subject['action']):
        return None
    owners = {aliases[match[0]] for match in re.finditer(labels, head)}
    who = aliases[subject[1]] if len(owners) == 1 else '待核对说话人'
    if len(speech) > 1 and (speech[0], speech[-1]) in (('“', '”'), ('「', '」'), ('"', '"')):
        speech = speech[1:-1]
    return body[:colon.end()], who, speech, colon.end()


def prepare_source(source, chars=(), *, split_long_dialogue=False):
    """Return numbered source, (speaker, exact words) rows, and source evidence.

    Accept both script lines and literary quotation dialogue. Narrative, signs,
    recalled speech and thoughts remain narrative. Repeated/short utterances are
    not deduplicated. The complete input is retained in the report.
    """
    from . import oral_story, dialogue_ledger, group_entities
    source = str(source or '')
    aliases = _aliases(chars)
    people = [{'name': name, 'aliases': [a for a, n in aliases.items() if n == name]}
              for name in dict.fromkeys(aliases.values())]
    dialogue, lines, evidence, issues = [], [], [], []
    source_dialogue_count = 0

    def add(who, words, proof, pos, source_end=None):
        nonlocal source_dialogue_count
        who = aliases.get(who, who) or '待核对说话人'
        words, cues = oral_story.dialogue_performance(words)
        if cues:
            lines.append(who + '，' + '，'.join(cues) + '。')
        source_dialogue_count += 1
        fragments = split_speech(words) if split_long_dialogue else [words]
        for index, fragment in enumerate(fragments, 1):
            dialogue.append((who, fragment))
            number = len(dialogue)
            lines.append('【D%d】%s：%s' % (number, who, fragment))
            evidence.append({'id': number, 'speaker': who, 'text': fragment,
                             'evidence': proof, 'pos': pos, 'source_dialogue_id': source_dialogue_count,
                             'fragment_index': index, 'fragment_count': len(fragments), 'original_text': words})
            card = next((c for c in chars if isinstance(c, dict) and c.get('name') == who), None)
            if card and group_entities.is_group(card):
                # A generic collective is scoped to this dialogue occurrence,
                # before any later arrivals/departures in the source.
                scoped = group_entities.scoped_cards(chars, source[:source_end] if source_end is not None else source[:pos] + proof)
                card = next((c for c in scoped if isinstance(c, dict) and c.get('name') == who), card)
                evidence[-1].update(entity_type='group', group_count=card.get('group_count'),
                                    group_members=group_entities.member_names(card, chars))
            if who == '待核对说话人':
                issues.append({'code': 'speaker_unresolved', 'dialogue_id': number, 'pos': pos})
            if split_long_dialogue and speech_seconds(fragment) > 15:
                issues.append({'code': 'dialogue_timing', 'dialogue_id': number,
                               'required_seconds': speech_seconds(fragment), 'reason': '原话没有可用的标点拆分位置'})

    segments, offset = [], 0
    for raw_line in source.splitlines(keepends=True):
        cuts = [0]
        if aliases:
            labels = '|'.join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
            # An exact card label after punctuation is also a script line. Keep
            # every character; unlike lift_inline_dialogue this does not dedupe.
            inline = re.compile(r'(?<=[。！？!?；;，,])\s*(?:' + labels + r')(?:（[^）\n]*）)?[：:]')
            cuts.extend(match.start() for match in inline.finditer(raw_line) if match.start())
        cuts.append(len(raw_line))
        segments.extend((offset + start, raw_line[start:end]) for start, end in zip(cuts, cuts[1:]))
        offset += len(raw_line)
    for offset, line in segments:
        body = line.rstrip('\r\n')
        explicit = oral_story._DLG_LINE.match(re.sub(r'^\s*【D\d+】\s*', '', body).strip())
        attached = _action_attached_speech(body, aliases)
        # These labels describe narration, not an on-screen speaking character.
        if explicit and explicit[1] in ('旁白', '画外音', '内心', '心声', '音效', '场景', '地点', '时间'):
            lines.append(body)
        elif attached:
            prefix, who, speech, position = attached
            lines.append(prefix)
            add(who, speech, body, offset + position, source_end=offset + len(body))
        elif (explicit and explicit[1] not in aliases
              and re.search(r'心想|准备|打算|计划|写着|刻着|贴着|标着|显示', explicit[1])):
            lines.append(body)
        elif (explicit and not body.lstrip().startswith('──') and
              (explicit[1] in aliases or not re.search(r'(?:说|道|问|答|喊|写|刻|想|念|标|印|贴|叫|声|着)$', explicit[1]))):
            who, words = explicit[1], explicit[3]
            if explicit[2]:
                lines.append(who + '，' + explicit[2][1:-1] + '。')
            if len(words) > 1 and (words[0], words[-1]) in (('“', '”'), ('「', '」'), ('"', '"')):
                words = words[1:-1]
            add(who, words, body, offset, source_end=offset + len(body))
        else:
            # Use the established quote classifier, not every pair of quotation marks.
            entries = dialogue_ledger.ledger(body, people)
            # The legacy classifier limits quotes to 200 chars. Long quotations
            # use the identical surrounding context and classifier, then restore
            # their full words; this also preserves thoughts/signage as narrative.
            full_quote = re.compile(r'[「“]([^」”\n]+)[」”]')
            for match in full_quote.finditer(body):
                if len(match[1]) <= 200:
                    continue
                shortened = body[:match.start()] + body[match.start()] + match[1][:160] + body[match.end()-1:]
                entry = next((row for row in dialogue_ledger.ledger(shortened, people) if row['pos'] == match.start()), None)
                if entry:
                    entries.append(dict(entry, text=match[1]))
            entries.sort(key=lambda row: row['pos'])
            quotes = [e for e in entries if e['kind'] == dialogue_ledger.KIND_SPOKEN]
            cursor = 0
            for row in quotes:
                match = full_quote.match(body, row['pos'])
                if not match:
                    continue
                before = body[cursor:match.start()]
                if before.strip():
                    lines.append(before)
                collective, proof = group_entities.collective_speaker(body, row['pos'], chars, end=match.end())
                add(collective or row['speaker'], row['text'], proof or row.get('evidence', ''), offset + row['pos'], source_end=offset + match.end())
                cursor = match.end()
            if body[cursor:].strip() or not quotes:
                lines.append(body[cursor:])
    report = {'version': VERSION, 'source_original': source,
              'source_digest': hashlib.sha256(source.encode('utf-8')).hexdigest(),
              'dialogue': evidence, 'issues': issues}
    return '\n'.join(lines), dialogue, report


def plain_script(script):
    return re.sub(r'^【D\d+】', '', str(script or ''), flags=re.M)


def ending_text(director):
    """Read the source-owned text or the legacy structured end state."""
    director = director or {}
    for key in ('end_state_text', 'target_state'):
        if isinstance(director.get(key), str) and director[key].strip():
            return director[key].strip()
    old = director.get('end_state')
    if isinstance(old, str):
        return old.strip()
    if isinstance(old, dict):
        return '；'.join('%s：%s' % (name, '，'.join(str(v) for v in state.values() if v))
                       for name, state in old.items() if isinstance(state, dict))
    return ''


_DEPARTURE = re.compile(
    r'\b(?:walk(?:s|ed|ing)?|step(?:s|ped|ping)?|run(?:s|ning)?|ran|go(?:es|ing)?|went|mov(?:e|es|ed|ing))\s+'
    r'(?:(?:slowly|fully|completely|quietly|finally|directly|carefully|all the way)\s+)*out(?:side)?\b|'
    r'\b(?:exit(?:s|ed|ing)?|leave[sd]?|leaving|left)\s+(?:(?:through|out of)\s+)?(?:the\s+|an?\s+)?'
    r'(?:room|shop|store|house|building|interior|cafe|restaurant|hall|doorway|door)\b|'
    r'(?:走|跑|迈|跨|踏|冲|退)出(?=门|屋|房|店|室|大楼|建筑|餐厅|咖啡馆|去|外|[。！？!?；;，,\n]|$)|'
    r'出门|离场|离开(?:这里|此处|[一-龥]{0,8}(?:房间|店铺|店|屋子|室内|房屋|大厅|建筑|大楼|餐厅|咖啡馆))', re.I)
_DEPARTURE_NEGATION = re.compile(
    r'还未|尚未|仍未|未曾|没有|并未|不曾|不再|不会|并不|不能|不可|不要|尚不|仍不|不$|未$|别$|'
    r'\b(?:not|never|without|no longer|cannot|can.t|don.t|doesn.t|didn.t|won.t|shouldn.t)\b', re.I)
_DEPARTURE_INTENT = re.compile(r'准备|打算|想要|将要|即将|正要|快要|计划|考虑|试图|说要|谈到|提到|'
                               r'\b(?:before|plans? to|intends? to|about to|wants? to|will|would)\b', re.I)
_FRAME_ONLY = re.compile(r'^\s+of\s+(?:the\s+)?(?:frame|focus|view|shot|camera.s field of view)\b', re.I)
_SOURCE_OUTWARD_TRANSITION = re.compile(r'(?:踏上|走到|走入|走进|进入|来到)(?:了)?门外|'
                                        r'(?:向外|往外)(?:跨过|跨出|迈过|越过|穿过)(?:了)?门槛')


def _departure_matches(text, source=False):
    # Spoken/quoted plans are not physical actions. H3 dialogue is removed as
    # a block before splitting sentences, so punctuation inside it is harmless.
    text = re.sub(r'<d>.*?</d>|“[^”]*”|「[^」]*」|‘[^’]*’|"[^"\n]*"', '', str(text or ''), flags=re.S)
    for clause in re.split(r'[。！？!?；;，,\n]|(?<=[.!?])\s+|\bbut\b', text, flags=re.I):
        matches = list(_DEPARTURE.finditer(clause))
        if source:
            # Crossing a threshold alone could mean entering. Only explicit
            # outward movement / arrival outside permits an exit description.
            matches.extend(_SOURCE_OUTWARD_TRANSITION.finditer(clause))
        for match in matches:
            before, after = clause[:match.start()], clause[match.end():]
            if match[0] == '出门' and re.search(r'(?:勾勒|描绘|映|透|露|突|显)$', before):
                # "勾勒出门外的光" describes an image, not an actor leaving.
                # A separate physical exit in the same text is still checked.
                continue
            if _FRAME_ONLY.match(after) or _DEPARTURE_NEGATION.search(before):
                continue
            if source and _DEPARTURE_INTENT.search(before):
                continue
            yield match[0]


def unsupported_departure(model_text, source_text):
    """Small physical-exit guard, not general action or actor inference."""
    # Reference definitions/retention describe rules, not events in this clip.
    # In particular wardrobe retention mentions "离场" conditionally; checking
    # the complete six-field envelope mistakes that policy for a staged exit.
    fields = re.compile(r'(?im)^\s*(subject_definitions|summary|retention_analysis|'
                        r'detailed_description|overall_soundscape|non_diegetic_music)\s*:\s*')
    headers = list(fields.finditer(str(model_text or '')))
    if any(match[1].lower() == 'detailed_description' for match in headers):
        model_text = '\n'.join(str(model_text)[match.end():headers[index + 1].start()
            if index + 1 < len(headers) else len(str(model_text))]
            for index, match in enumerate(headers)
            if match[1].lower() in ('summary', 'detailed_description'))
    script, _, _ = prepare_source(source_text)
    narrative = '\n'.join(line for line in script.splitlines() if not line.startswith('【D'))
    if next(_departure_matches(narrative, source=True), None):
        return ''
    departure = next(_departure_matches(model_text), None)
    return ('本段原文未发生离场，提示词不可新增或预演离场动作：' + departure) if departure else ''


# Deliberately conservative: a named subject performing a current action is
# evidence; a name merely mentioned, sought, recalled or known absent is not.
_ACTION = (r'递|接|拿|握|持|提|抱|背(?!影)|扶|拉|推|扛|举|放|捡|拾|搬|扔|藏|取|收|端|托|垂|'
           r'走|跑|跳|踏|迈|爬|穿|坐|站|躺|蹲|跪|靠|倚|转|回头|起身|停|'
           r'看|望|瞥|盯|发现|察觉|听|摸|伸|抬|低头|点头|摇头|喝|吃|'
           r'说|问|答|喊|叫|笑|哭|敲|拍|按|拧|打开|关闭|挡|挥|刺|砍|搜|寻找|检查|进入|离开|离场|走远|出现|回来|侧身|俯身|躬身')
_ABSENT = re.compile(r'^(?:(?:已经|已|仍然|仍|早已|突然|却|也|并|不知何时|此时|现在|竟然|竟)\s*)*'
                     r'(?:失踪|不见了|不在(?:场|这里|此处|屋内)|消失(?:了|无踪)|下落不明|尚未到场)')
_MENTION = re.compile(r'(?:想起|想念|回忆|记得|寻找|找到|提起|提到|关于|听说|梦见|照片中的|画像上的)$')
_ACTION_MODIFIER = r'(?:(?:正在|正|又|已经|已|先|再|随即|缓缓|慢慢|轻轻|稳稳|立刻|连忙|悄悄|仍然|仍|也|一边|一起|一齐|齐声|异口同声|同时|纷纷|都|共同)\s*)*'
_BODY_PART = r'(?:左手|右手|双手|手指|指尖|左臂|右臂|双臂|左脚|右脚|双脚|双腿|头部|肩膀|肩头)'
_NAMED_ACTION = re.compile(r'^' + _ACTION_MODIFIER + r'(?:(?:的)?' + _BODY_PART + r'\s*' + _ACTION_MODIFIER + r')?(?:' + _ACTION + r')')
_GAZE_ACTION = re.compile(r'^' + _ACTION_MODIFIER + r'(?:的)?(?:目光|视线|眼神)' + _ACTION_MODIFIER + r'(?:落在|移向|转向|停在|扫过|追随|注视|从)')
_VISUAL_TARGET = re.compile(
    r'(?:看向|看着|看回|望向|望着|望回|盯着|注视|凝视|打量|面对|对着|'
    r'(?:目光|视线|眼神)' + _ACTION_MODIFIER + r'(?:落在|移向|转向|停在|扫过|追随|注视|从))'
    r'(?:(?:身旁|面前|对面|眼前|一旁)(?:的)?)?$')
_VISUAL_CONTEXT_SKIP = re.compile(
    r'想起|想念|回忆|回想|忆起|记得|梦见|梦到|想象|设想|假如|如果|要是|'
    r'曾经|当年|照片中|照片里|画像上|画像中|屏幕中|屏幕里|画外|镜头外|'
    r'提起|提到|听说|寻找|失踪|已离场|已经离场|缺席|不在场|不在画面|尚未到场|'
    r'不要|不能|不应|不得|避免|禁止|不拍|不显示|不出现|没有|并未|未曾|尚未|'
    r'不曾|不再|(?:不|未|别)(?:看|望|盯|注视|凝视|打量|面对|对视|与|和|跟|同)')
_VISUAL_PAIR_ACTION = r'(?:(?:一起|共同|正在|正|仍然|仍|并肩|面对面|相对)\s*)*(?:同框|对视|对话|交谈|说话|相视|交换目光|站|坐)'


def camera_identity_evidence(camera, chars=(), absent=()):
    """Bind explicit visual candidates, without requiring them all to appear."""
    aliases = {a: n for a, n in _aliases(chars).items() if a and a not in ('他', '她', '你', '我')}
    result = {'names': [], 'evidence': []}
    if not aliases:
        return result
    # A camera note may quote dialogue. Those words cannot establish visibility.
    camera = re.sub(r'“[^”]*”|「[^」]*」|‘[^’]*’|"[^"\n]*"', '', str(camera or ''))
    camera = re.sub(r'(?:台词|旁白|画外音|说|问|答)[：:][^。！？!?；;\n]*', '', camera)
    pattern = re.compile('|'.join(re.escape(a) for a in sorted(aliases, key=len, reverse=True)))
    paired_after = re.compile(r'^(?:与|和|跟|同|、)(?:' + pattern.pattern + r')' + _VISUAL_PAIR_ACTION)
    excluded = set(absent)
    for clause in re.split(r'[。！？!?；;\n，,]', camera):
        for match in pattern.finditer(clause):
            who = aliases[match[0]]
            before, after = clause[:match.start()].strip(), clause[match.end():].strip()
            if who in excluded or _MENTION.search(before) or _ABSENT.match(after):
                continue
            if (_VISUAL_CONTEXT_SKIP.search(before)
                    or re.match(r'^(?:的)?(?:照片|画像|名字|消息|传闻|声音|声线|台词|录音)', after)
                    or re.match(r'^(?:(?:的)?(?:背影|身影|侧影|面部|脸部|侧脸|双手|手部))?(?:(?:仍然|仍|保持|继续|已|已经|昨天|昨日|当年|曾经)\s*)*(?:不出镜|不出现|不在画面|在画外|在镜头外|位于画外|失踪|缺席|离场|离去|离开|走远|消失)', after)):
                continue
            body = re.match(r'^(?:的)?(?:背影|身影|侧影|面部|脸部|侧脸|正脸|双手|左手|右手|手部|手指|指尖|上半身|全身|肩膀|肩头)', after)
            framing = (re.search(r'(?:跟随|跟拍|对准|聚焦|拍摄|捕捉|推近至|拉近至|反打|特写|近景|中景|远景|画面中|背景中|前景中|镜头中的?)$', before)
                       or re.match(r'^(?:的)?(?:特写|近景|中景|远景)', after))
            action = not before and (_NAMED_ACTION.match(after) or _GAZE_ACTION.match(after))
            target = _VISUAL_TARGET.search(before)
            paired = paired_after.match(after) or (re.search(r'(?:与|和|跟|同|、)$', before)
                                                   and re.match(r'^' + _VISUAL_PAIR_ACTION, after))
            if body or framing or action or target or paired:
                if who not in result['names']:
                    result['names'].append(who)
                result['evidence'].append({'name': who, 'evidence': clause.strip(), 'role': 'visible_if_framed'})
    return result


def presence_evidence(source, chars=(), context=''):
    """Return only explicit actor/absence evidence, never all named entities."""
    aliases = {a: n for a, n in _aliases(chars).items() if a and a not in ('他', '她', '你', '我')}
    if not aliases:
        return {'actors': [], 'absent': [], 'evidence': [], 'visible': [], 'visual_identity_candidates': []}
    actions, absent, facts = [], [], []
    # Dialogue content can mention missing people; do not treat it as action.
    script, _, _ = prepare_source(source, chars)
    narrative = '\n'.join(line for line in script.splitlines() if not line.startswith('【D'))
    pattern = re.compile('(' + '|'.join(re.escape(a) for a in sorted(aliases, key=len, reverse=True)) + ')')
    for clause in re.split(r'[。！？!?；;\n，,]', narrative):
        for match in pattern.finditer(clause):
            before, after = clause[:match.start()].strip(), clause[match.end():].strip()
            who = aliases[match[1]]
            # Only standalone subjects (possibly after a transition) are certain.
            subject = not before or bool(re.fullmatch(r'(?:随后|接着|这时|此时|终于|而|但|只有|只剩|剩下的|其余的?)', before))
            interaction = (re.search(r'(?:递给|交给|递向|扶住|抱住|抱起|背起|拉住|牵着|搀扶|抱着|背着|扶着)$', before)
                           or (before.endswith('把') and re.match(r'(?:抱|背|扶|拉|推|扛|抬|放)', after)))
            if interaction and not re.match(r'的(?:画像|照片|名字|消息|传闻)', after):
                if who not in actions:
                    actions.append(who)
                if who in absent:
                    absent.remove(who)
                facts.append({'name': who, 'presence': 'interaction', 'evidence': clause.strip()})
            if not subject or _MENTION.search(before):
                continue
            if _ABSENT.match(after) or re.match(r'^(?:已经|已|早已)(?:离开|离场|走远)', after):
                if who not in absent:
                    absent.append(who)
                facts.append({'name': who, 'presence': 'absent', 'evidence': clause.strip()})
            elif _NAMED_ACTION.match(after) or _GAZE_ACTION.match(after):
                if who not in actions:
                    actions.append(who)
                departing = (next(_departure_matches(who + after, source=True), None)
                             or re.match(r'^' + _ACTION_MODIFIER + r'(?:离开|离场|走远)', after))
                if departing and who not in absent:
                    absent.append(who)
                elif not departing and who in absent:
                    absent.remove(who)
                facts.append({'name': who, 'presence': 'action', 'evidence': clause.strip()})
    from . import group_entities
    for occurrence in group_entities.collective_actions(source, chars, context=context):
        for name in occurrence['members']:
            if name not in actions:
                actions.append(name)
            facts.append({'name': name, 'presence': 'collective_action',
                          'evidence': occurrence['evidence'], 'collective': occurrence['label']})
    counts = []
    digits = {'一': 1, '两': 2, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
    for match in re.finditer(r'(?<![\w一-龥])(?:其余|剩下的?|在场的?)?([一两二三四五六七八九十]|\d+)人', narrative):
        count = digits.get(match[1], int(match[1]) if match[1].isdigit() else None)
        counts.append({'count': count, 'evidence': match[0], 'pos': match.start()})
    from . import group_entities
    by_name = {c.get('name'): c for c in chars if isinstance(c, dict)}
    participants, anonymous_minimum, groups = set(), 0, []
    for name in actions:
        card = by_name.get(name) or {}
        if group_entities.is_group(card):
            members = group_entities.member_names(card, chars)
            count = card.get('group_count')
            count = count if isinstance(count, int) and not isinstance(count, bool) and count >= 2 else None
            groups.append({'name': name, 'count': count, 'members': members})
            if members:
                participants.update(members)
                anonymous_minimum += max(0, (count if count is not None else 2) - len(members))
            else:
                # A plural identity needs more than one actor. This lower bound
                # is not a fabricated exact count for an unknown-size group.
                anonymous_minimum += count if count is not None else 2
        else:
            participants.add(name)
    visible = camera_identity_evidence(narrative, chars, absent)
    return {'actors': actions, 'absent': absent, 'evidence': facts,
            'visible': visible['names'], 'visual_identity_candidates': visible['evidence'],
            'minimum_action_participants': len(participants) + anonymous_minimum,
            'group_entities': groups, 'explicit_group_counts': counts}


def normalize_anchors(data):
    """Canonicalize unambiguous spelling only; unknown IDs remain errors."""
    fixed = copy.deepcopy(data)
    text = fixed.get('text')
    if not isinstance(text, str):
        return fixed
    variants = (r'\{\{\s*[dD]\s*0*(\d+)\s*\}\}', r'(?<!\{)\{\s*D\s*0*(\d+)\s*\}(?!\})',
                r'【\s*D\s*0*(\d+)\s*】', r'\[\s*D\s*0*(\d+)\s*\]', r'<D\s*0*(\d+)\s*>')
    for pattern in variants:
        text = re.sub(pattern, lambda m: '{{D%d}}' % int(m[1]), text)
    fixed['text'] = text
    return fixed
