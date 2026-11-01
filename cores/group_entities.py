"""Source-backed collective identities, separate from individual character cards.

Registration is deterministic and incremental. It never changes existing cards
or references, never guesses unnamed members, and does not turn quoted mentions
or unresolved pronouns into visible actors.
"""
import re


_PRONOUNS = {"他们", "她们", "它们", "我们", "你们", "他", "她", "它", "我", "你"}
_COLLECTIVES = {"众人", "大家", "所有人", "在场的人", "在场众人", "两人", "二人", "三人"}
_ORDINARY_COLLECTIVES = {"两人", "二人", "三人", "他们", "她们", "它们"}
_ROLES = ("志愿者", "工作人员", "维修员", "服务员", "安保人员", "研究人员", "队员", "工人",
          "游客", "顾客", "旅客", "观众", "村民", "居民", "路人", "同学", "学生", "老师",
          "护士", "医生", "警察", "士兵", "卫兵", "守卫", "侍卫", "侍女", "女仆", "仆人",
          "弟子", "师兄弟", "修士", "骑士", "船员", "水手", "运动员", "乐手", "演员",
          "孩子", "老人", "妇女", "男子", "女子", "女孩", "男孩", "少女", "少年", "青年",
          "精灵", "矮人", "村民代表", "机器人", "伙伴", "同伴", "成员", "人")
_ROLE_RE = "|".join(re.escape(v) for v in sorted(_ROLES, key=len, reverse=True))
_COUNT = r"(?:\d{1,3}|[一二两三四五六七八九十百]{1,4})"
_PREFIX = r"(?:(?:" + _COUNT + r"|几|数)\s*(?:个|名|位)|一群|一队|一排|一众|一批|一伙|一组|一队队)"
_GROUP_RE = re.compile(r"(?P<count>" + _PREFIX + r")?\s*(?P<role>" + _ROLE_RE + r")(?P<plural>们)?")
_ACTION = re.compile(r"^(?:\s|[（(][^）)]*[）)])*(?:(?:一起|一齐|齐声|异口同声|同时|纷纷|都|共同|随即|随后|正在|仍然|继续|正|已|已经|开始|快步|快速|缓缓|缓慢|迅速|急忙|径直|一同|结伴)\s*)*(?:"
    r"说|问|答|喊|叫|应|回应|回答|开口|低声|高声|点头|摇头|鼓掌|欢呼|站|坐|走|跑|来到|进入|抵达|等候|"
    r"围|聚|排|抬|搬|拿|举|伸|转|看|望|对视|相视|对望|互望|立于|立在|立定|低头|抬头|端|扶|拉|推|跟|接|递|交|捧|握|收|放|守|穿|身穿|穿着|戴|离开|退|散|跪|停|挤|忙|[：:])")
_SPEECH = r"(?:(?:一起|一齐|齐声|异口同声|同时|纷纷|都|共同)\s*)*(?:说道|问道|答道|喊道|应道|回道|说|问|答|喊|叫|回应|回答|开口|应声|低声说|高声说)"
_MENTION = re.compile(r"(?:寻找|找不到|寻觅|想起|提到|谈起|说起|怀念|听说|关于|等待|等着|担心|呼唤|没有见到|未见到|打算|准备|计划|想要|将要|邀请|招募)\s*$")
_ABSENT = re.compile(r"^\s*(?:(?:已经|早已|仍然|仍|一直|已)\s*)*(?:失踪|不在|下落不明|未出现|没出现|没有出现|不见了|早已离开|已离开|还没来|尚未到场|没有来|未到场)")
_FUTURE = re.compile(r"^\s*(?:计划|准备|打算|想要|将要|即将|还没|尚未|正要)")
_LEFT = re.compile(r"^\s*(?:(?:已经|已|随后|接着|终于)\s*)*(?:(?:驾车|开车|乘车|御剑|骑马|转身)\s*)*(?:离开|离去|走出|跑出|退出|退场|离场|消失|驶离|驶出|飞走|远去)")


def is_group(card):
    if not isinstance(card, dict):
        return False
    if str(card.get("entity_type") or "").lower() == "group":
        return True
    # Old saved plural cards can be read as collective identities without
    # changing their IDs, facts, files, or already adopted references.
    name = str(card.get("name") or "").strip()
    return name not in _PRONOUNS and bool(re.fullmatch(r"(?:" + _ROLE_RE + r")们", name))


def group_aliases(card):
    """Never expose a collective's singular role as an individual alias."""
    values = card.get("aliases") or []
    if isinstance(values, str):
        values = re.split(r"[、，,；;|]", values)
    name = str(card.get("name") or "").strip()
    return {str(x).strip() for x in [name, *values]
            if str(x or "").strip() and str(x).strip() not in _PRONOUNS
            and (str(x).strip() == name or str(x).strip() not in _ROLES)}


def _number(value):
    if str(value).isdigit():
        return int(value)
    numbers = {v: i for i, v in enumerate("零一二三四五六七八九")}
    numbers["两"] = 2
    total, part = 0, 0
    for char in str(value):
        if char in numbers:
            part = numbers[char]
        elif char in "十百":
            total += (part or 1) * (10 if char == "十" else 100)
            part = 0
        else:
            return None
    return total + part or None


def member_names(card, cards):
    values = card.get("group_members") or card.get("members") or []
    if isinstance(values, str):
        values = re.split(r"[、，,；;|]", values)
    known = {str(c.get("name") or ""): c for c in cards if isinstance(c, dict) and not is_group(c)}
    ids = {str(c.get("character_id") or ""): c.get("name") for c in known.values()}
    result = []
    for value in values:
        value = str(value.get("name") or value.get("character_id") or "") if isinstance(value, dict) else str(value)
        name = value if value in known else ids.get(value)
        if name and name not in result:
            result.append(name)
    return result


def reference_cards(cards, all_cards=None):
    """Use member references in canonical asset order, matching the image pack."""
    all_cards = list(all_cards if all_cards is not None else cards)
    by_name = {c.get("name"): c for c in all_cards}
    result, seen = [], set()
    for card in cards:
        members = member_names(card, all_cards) if is_group(card) else []
        count = card.get('group_count')
        complete_members = len(members) >= 2 and (count is None or count == len(members))
        for candidate in ([by_name[name] for name in members] if complete_members else [card]):
            key = candidate.get("character_id") or candidate.get("name")
            if key not in seen:
                result.append(candidate)
                seen.add(key)
    # A source label may list 阿宁和林海 while saved assets are 林海、阿宁.
    # Subject/Picture numbering must follow the same asset order as new_pack,
    # not the textual order of members or selected groups.
    order = {c.get('character_id') or c.get('name'): index for index, c in enumerate(all_cards)}
    return sorted(result, key=lambda c: order.get(c.get('character_id') or c.get('name'), len(order)))


def _later_anonymous_subject(narrative, last_named_action):
    """A newer anonymous group cannot inherit an earlier named pair's IDs.

    This is a boundary, not a guessed roster.  Mentions, dialogue and plans do
    not create it; a later explicit named action can establish a pair again.
    """
    for match in _GROUP_RE.finditer(narrative):
        if match.start() <= last_named_action or not (match['count'] or match['plural']):
            continue
        after = re.split(r'[，,。！？!?；;\n]', narrative[match.end():], maxsplit=1)[0]
        if not _ACTION.match(after):
            continue
        before = re.split(r'[，,。！？!?；;\n]', narrative[:match.start()])[-1].strip()
        if _MENTION.search(before):
            continue
        if before and not re.search(
                r'(?:有|来了|来了一群|身后的|门口的|在场的|这些|那些|其余的?|剩下的?|'
                r'随后|接着|这时|此时|而|与此同时|另一处的|另一边的|另一侧的)$', before):
            continue
        return True
    return False


def _known_members(context, cards, count=None):
    """Only explicitly named, currently present people can form a collective.

    The current source prefix does not import everyone from the project roster
    or earlier episodes. Absence and plans never establish membership.
    """
    from .continuation_assets import appears_in_source, source_narration
    narrative = source_narration(context)
    present, last_actions = [], {}
    for card in cards:
        if not isinstance(card, dict) or is_group(card):
            continue
        values = card.get("aliases") or []
        if isinstance(values, str):
            values = re.split(r"[、，,；;|]", values)
        aliases = {str(card.get("name") or ""), *values}
        active = False
        for clause_match in re.finditer(r'[^。！？!?；;\n]+', narrative):
            clause = clause_match[0]
            for alias in aliases:
                if not alias:
                    continue
                for match in re.finditer(re.escape(alias), clause):
                    before, after = clause[:match.start()], clause[match.end():]
                    # source_narration retains a speaker label but removes the
                    # words. An off-screen voice does not re-enter the room.
                    if not before.strip() and not after.strip():
                        continue
                    if _MENTION.search(re.split(r"[，,]", before)[-1]):
                        continue
                    if _ABSENT.match(after) or _LEFT.match(after):
                        active = False
                    elif _FUTURE.match(after):
                        continue
                    elif not _MENTION.search(re.split(r"[，,]", before)[-1]) and appears_in_source(alias, clause):
                        active = True
                        last_actions[card.get('name')] = clause_match.start() + match.start()
        if active:
            present.append(card.get("name"))
    if _later_anonymous_subject(narrative, max(last_actions.values(), default=-1)):
        return []
    if count is not None and len(present) != count:
        # An explicit local pair can identify "两人" even if a third person's
        # departure was described through their vehicle or a pronoun. Never
        # take the first N entries from the whole project roster.
        names = '|'.join(re.escape(name) for name in sorted(present, key=len, reverse=True))
        if names:
            joint = re.compile(r'(?P<label>(?:' + names + r')(?:\s*[、和与及]\s*(?:' + names + r'))+)(?P<after>[^。！？!?；;\n]*)')
            matches = []
            for match in joint.finditer(narrative):
                members = list(dict.fromkeys(re.findall(names, match['label'])))
                before = re.split(r'[，,。！？!?；;\n]', narrative[:match.start()])[-1].strip()
                latest = max((last_actions.get(name, -1) for name in present if name not in members), default=-1)
                if (len(members) == count and match.end('label') >= latest
                        and not _MENTION.search(before) and _ACTION.match(match['after'])):
                    matches.append(members)
            if matches:
                return matches[-1]
        return []
    return present


def collective_actions(source, cards=(), context=''):
    """Resolve ordinary action subjects to known local people, with evidence.

    Numeric subjects require exactly that many supported identities. Unresolved
    plurals stay unresolved; anonymous crowds are handled by detect_groups.
    """
    from .continuation_assets import source_narration
    narrative = source_narration(source)
    labels = '|'.join(re.escape(name) for name in sorted(_ORDINARY_COLLECTIVES, key=len, reverse=True))
    pattern = re.compile(r'(?:^|[。！？!?；;\n，,])\s*(?:随后|接着|这时|此时|而)?\s*'
                         r'(?P<label>' + labels + r')(?P<after>[^。！？!?；;\n，,]*)')
    result = []
    for match in pattern.finditer(narrative):
        if not _ACTION.match(match['after']):
            continue
        label = match['label']
        count = _number(label[:-1]) if re.fullmatch(r'[两二三]人', label) else None
        prefix = '\n'.join((str(context or ''), narrative[:match.start('label')]))
        members = _known_members(prefix, cards, count=count)
        if len(members) < 2:
            members = []
        result.append(dict(label=label, count=count, members=members,
                           evidence=label + match['after'], pos=match.start('label')))
    return result


def _appearance(evidence):
    matches = re.findall(r"(?:身穿|穿着|统一穿|都穿|穿)\s*([^，,。！？!?；;：:\n]{1,45})", evidence)
    return "；".join(matches)


def detect_groups(source, cards=()):
    """Return source-backed group descriptors; no models and no mutation."""
    from .continuation_assets import source_narration
    from .oral_story import _DLG_LINE
    source = str(source or "")
    narrative = source_narration(source)
    found = {}
    named_aliases = {}
    for card in cards:
        if not isinstance(card, dict) or is_group(card):
            continue
        values = card.get('aliases') or []
        if isinstance(values, str):
            values = re.split(r"[、，,；;|]", values)
        for name in [card.get('name'), *values]:
            if name and name not in _PRONOUNS:
                named_aliases.setdefault(str(name), set()).add(card['name'])
    named_aliases = {name: next(iter(owners)) for name, owners in named_aliases.items() if len(owners) == 1}
    named_pattern = '(?:' + '|'.join(re.escape(n) for n in sorted(named_aliases, key=len, reverse=True)) + ')' if named_aliases else None
    joint_pattern = re.compile(r'(?P<label>' + named_pattern + r'(?:\s*[、和与及]\s*' + named_pattern + r')+)\s*(?:' + _SPEECH + r'|[：:])') if named_pattern else None

    def add(name, alias, evidence, count=None, members=()):
        if name in _PRONOUNS:
            return
        existing = next((c for c in cards if is_group(c) and (name in group_aliases(c) or alias in group_aliases(c))), None)
        if existing:
            name = existing["name"]
        row = found.setdefault(name, {"name": name, "entity_type": "group", "aliases": [],
            "group_count": count, "group_members": list(members), "group_appearance": _appearance(evidence),
            "source_evidence": evidence})
        if name in _COLLECTIVES:
            # Generic labels describe the current occurrence, not a roster
            # locked by their first occurrence earlier in the same source.
            row['group_members'] = list(members)
            row['group_count'] = count
            row['source_evidence'] = evidence
        if alias != name and alias not in row["aliases"]:
            row["aliases"].append(alias)
        if row["group_count"] is None and count is not None:
            row["group_count"] = count
        if not row["group_appearance"]:
            row["group_appearance"] = _appearance(evidence)

    # Keep exact speaker labels before source_narration strips the spoken words.
    # A label is direct evidence of a current collective voice, not a mention.
    source_offset = 0
    for raw_line in source.splitlines(keepends=True):
        position = source_offset
        source_offset += len(raw_line)
        body = re.sub(r"^\s*【D\d+】\s*", "", raw_line).strip()
        explicit = _DLG_LINE.match(body)
        if not explicit:
            continue
        label = explicit[1].strip()
        if label in _PRONOUNS:
            continue
        if joint_pattern and joint_pattern.fullmatch(label + ':'):
            members = list(dict.fromkeys(named_aliases[m[0]] for m in re.finditer(named_pattern, label)))
            if len(members) >= 2:
                add(label, label, body, len(members), members)
        elif label in _COLLECTIVES:
            context = source[:position]
            explicit_count = _number(label[:-1]) if re.fullmatch(r'[两二三]人', label) else None
            members = _known_members(context, cards, count=explicit_count)
            members = members if len(members) >= 2 else []
            add(label, label, body, explicit_count if explicit_count is not None else (len(members) or None), members)
        elif re.fullmatch(r"[^\s，,。！？!?；;：:]{1,10}们", label):
            add(label, label, body)
        else:
            group = _GROUP_RE.fullmatch(label)
            if group and group["count"]:
                count = re.search(_COUNT, group["count"])
                count = _number(count[0]) if count else None
                if count is None or count >= 2:
                    add(group["role"] + "们", label, body, count)

    paragraph_offset = 0
    for paragraph in narrative.splitlines(keepends=True):
        for clause in re.split(r"[。！？!?；;]", paragraph):
            if joint_pattern:
                for joint in joint_pattern.finditer(clause):
                    before = re.split(r"[，,]", clause[:joint.start()])[-1]
                    if _MENTION.search(before) or before.strip() not in ('', '随后', '接着', '这时', '此时', '而'):
                        continue
                    label = joint['label'].strip()
                    members = list(dict.fromkeys(named_aliases[m[0]] for m in re.finditer(named_pattern, label)))
                    if len(members) >= 2:
                        add(label, label, clause.strip(), len(members), members)
            for match in _GROUP_RE.finditer(clause):
                if not match["plural"] and not match["count"]:
                    continue
                before, after = clause[:match.start()].strip(), clause[match.end():].strip()
                lead = re.split(r"[，,]", before)[-1]
                if _MENTION.search(lead) or _ABSENT.match(after) or re.match(r"^的(?:名字|消息|踪迹|线索|传说|故事|遗物|失踪|画像|照片)", after):
                    continue
                if not _ACTION.match(after):
                    continue
                # Lexical suffix matching must not turn a person's name or
                # modified identity into a second unrelated group.
                if before and not (re.search(r"[，,、：:（(]$", before) or re.search(r"(?:有|来了|来了一群|身后的|门口的|在场的|这些|那些|其余的?|剩下的?|随后|接着|这时|此时|而|与|和|及)$", before)):
                    continue
                count = re.search(_COUNT, match["count"] or "")
                count = _number(count[0]) if count else None
                if count is not None and count < 2:
                    continue
                name = match["role"] + "们"
                add(name, match[0].strip(), clause.strip(), count)
            # An explicit plural source subject is enough even when its role
            # isn't in the conservative lexicon (e.g. 魔法师们). Require an
            # actual following action, not a mention or a name inside speech.
            plural = re.match(r'^\s*(?:随后|接着|这时|此时|而|这些|那些|其余的?|剩下的?)?\s*([一-龥]{1,10}们)(.*)$', clause)
            if plural and plural[1] not in _PRONOUNS and _ACTION.match(plural[2]):
                name = plural[1]
                stem = name[:-1]
                preceding = paragraph[:paragraph.find(clause)]
                counter = re.search('(?P<label>(?P<prefix>' + _PREFIX + r')\s*' + re.escape(stem) + r')(?!们)(?P<after>[^。！？!?；;]*)', preceding)
                count = None
                alias = name
                if (counter and _ACTION.match(counter['after'])
                        and not _MENTION.search(re.split(r'[，,。！？!?；;]', preceding[:counter.start()])[-1])):
                    numeric = re.search(_COUNT, counter['prefix'])
                    count = _number(numeric[0]) if numeric else None
                    alias = counter['label']
                add(name, alias, clause.strip(), count)
            for label in sorted(_COLLECTIVES, key=len, reverse=True):
                match = re.search(r"(?:^|[，,])\s*" + re.escape(label) + r"(?P<after>.*)", clause)
                if not match or not _ACTION.match(match["after"]):
                    continue
                explicit_count = _number(label[:-1]) if re.fullmatch(r'[两二三]人', label) else None
                members = _known_members(narrative[:paragraph_offset + paragraph.find(clause) + match.start()], cards, count=explicit_count)
                members = members if len(members) >= 2 else []
                add(label, label, clause.strip(), explicit_count if explicit_count is not None else (len(members) or None), members)
            # Script labels can contain a role not in the conservative lexicon.
            explicit = re.match(r"^\s*(?:【D\d+】\s*)?([^：:\n]{2,12}们)(?:（[^）]*）)?\s*[：:]", clause)
            if explicit and explicit[1] not in _PRONOUNS and not re.search(r"说|问|答|喊|叫|想|听|看|提到|谈起", explicit[1]):
                add(explicit[1], explicit[1], clause.strip())
        paragraph_offset += len(paragraph)
    return list(found.values())


def scoped_cards(cards, source):
    """Bind generic collectives to this source only, without editing saved cards.

    “众人” is an occurrence label, not a permanently fixed team. Later episodes
    may have a different cast; stale saved members must not enter current refs.
    """
    descriptors = {row['name']: row for row in detect_groups(source, cards)}
    result = []
    for card in cards:
        if not isinstance(card, dict):
            result.append(card)
            continue
        descriptor = descriptors.get(card.get('name'))
        if is_group(card) and card.get('name') in _COLLECTIVES and descriptor:
            result.append(dict(card, group_members=list(descriptor['group_members']),
                               group_count=descriptor['group_count']))
        else:
            result.append(card)
    return result


def ensure_group_cards(sid, ep=1, source=None, on_step=None):
    """Add only missing collective identities; retain all existing cards/images."""
    from . import asset_core, saga_core
    if source is None:
        episode = saga_core.episode(saga_core.get_saga(sid), int(ep or 1)) or {}
        source = episode.get("prose") or episode.get("pictures") or episode.get("body") or ""
    cards = asset_core.list_assets(sid, "characters") or []
    groups = detect_groups(source, cards)
    added, reused = [], []
    for descriptor in groups:
        if (descriptor['name'] in _ORDINARY_COLLECTIVES
                and len(descriptor.get('group_members') or []) >= 2
                and descriptor.get('group_count') == len(descriptor['group_members'])):
            # Ordinary action pronouns reuse their individual reference cards.
            # They do not introduce another person/group card in new projects.
            continue
        existing = next((c for c in cards if is_group(c) and group_aliases(c) & group_aliases(descriptor)), None)
        if existing:
            reused.append(existing["name"])
            continue
        # A legacy individual with the same plural spelling is not silently
        # rewritten. New group names reuse source identity in separate storage.
        fields = dict(descriptor, first_episode=int(ep or 1),
                      char_type="群体", appearance_details=descriptor["group_appearance"],
                      clothing=descriptor["group_appearance"], sheet_body_ratio="自动")
        card = asset_core.create_character(sid, fields)
        cards.append(card)
        added.append(card["name"])
        if on_step:
            on_step("补齐群体设定：" + card["name"])
    return {"added": added, "reused": reused, "groups": groups}


def collective_speaker(source, pos, chars, end=None):
    """Return exact collective label near a quote, never a nearest-person guess."""
    source = str(source or "")
    end = end if end is not None else next((i + 1 for i in range(pos + 1, len(source)) if source[i] in "”」"), pos)
    before = re.split(r"[。！？!?；;\n]", source[max(0, pos - 90):pos])[-1]
    after = re.split(r"[。！？!?；;\n]", source[end:end + 90])[0]
    candidates = []
    for card in chars:
        if not is_group(card):
            continue
        for alias in sorted(group_aliases(card), key=len, reverse=True):
            pattern = re.escape(alias)
            head = re.search(pattern + r"\s*(?:" + _SPEECH + r")?\s*[：:，,]?\s*$", before)
            tail = re.match(r"\s*[，,]?\s*" + pattern + r"\s*" + _SPEECH, after)
            if head or tail:
                candidates.append((card["name"], (head or tail)[0]))
    return candidates[0] if candidates and len({name for name, _ in candidates}) == 1 else ("", "")


def group_sheet_prompt(card, settings=None):
    """A collective reference uses a natural shared frame, not a single-person sheet."""
    settings = settings or {}
    name = str(card.get("name") or "群体")
    count = card.get("group_count")
    quantity = "人数按已确认设定为%d名" % count if isinstance(count, int) and not isinstance(count, bool) and count >= 2 else "多名成员组成同一群体，人数呈现为自然群像"
    facts = "；".join(str(card.get(k) or "").strip() for k in ("group_appearance", "appearance_details", "clothing") if card.get(k))
    facts = "；".join(dict.fromkeys(facts.split("；")))
    style = str(settings.get("style") or settings.get("visual_style") or "").strip()
    return ("群体参考图：%s。%s。%s%s"
            "所有成员在一张自然构图的中远景合影中同框，成员具有自然区分的面孔和体态，整体身份、服装风格保持一致。"
            "完整展示成员及彼此间距，简洁中性背景，均匀照明。单幅连续的自然群像，每位成员占据独立位置，画面聚焦人物与衣装。"
            % (name, quantity, (facts + "。") if facts else "", ("画风：" + style + "。") if style else ""))
