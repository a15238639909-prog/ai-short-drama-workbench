"""Conservative identity matching for incremental episode assets.

These helpers do not infer cast presence from the project's roster, and never
change an existing card. A source mention and an on-screen appearance differ.
"""
import re


def aliases(card):
    name = str(card.get("name") or "").strip()
    values = card.get("aliases") or []
    if isinstance(values, str):
        values = re.split(r"[、，,；;|]", values)
    result = {name} | {str(x).strip() for x in values if str(x).strip()}
    short = re.sub(r"[（(][^）)]*[）)]", "", name).strip()
    if short:
        result.add(short)
    return result - {""}


def match_character(name, cards, stated_aliases=""):
    """Only unique explicit identities match; roles/substrings are not IDs."""
    name = str(name or "").strip()
    names = {name} | {x.strip() for x in re.split(r"[、，,；;|]", str(stated_aliases or "")) if x.strip()}
    exact = [c for c in cards if str(c.get("name") or "").strip() == name]
    if len(exact) == 1:
        return exact[0]
    found = [c for c in cards if names & aliases(c)]
    return found[0] if len(found) == 1 else None


def match_scene(name, cards, context=""):
    """Explicit aliases and an unambiguous positional suffix reuse a scene."""
    name = str(name or "").strip()
    exact = [c for c in cards if name in aliases(c)]
    if len(exact) == 1:
        return exact[0]
    # Indoor/outdoor and directional locations can have different lighting and
    # layouts: do not collapse them or unrelated locations in the same class.
    def base(value):
        return re.sub(r"(?:里面|之中|内)$", "", value).strip()
    found = [c for c in cards if any(base(name) == base(x) for x in aliases(c))]
    if len(found) == 1:
        return found[0]
    # A generic existing label (e.g. “店里”) may have a precise location as the
    # opening of its saved space description. Only that primary title is an
    # identity alias; later references to other places are not.
    generic = {"店里": "店", "店内": "店", "屋里": "屋", "屋内": "屋", "院内": "院", "院里": "院"}
    primary = []
    for card in cards:
        suffix = generic.get(str(card.get("name") or ""))
        if not suffix or not base(name).endswith(suffix):
            continue
        title = re.split(r"[，,。；;\n]", str(card.get("space") or card.get("contract_text") or ""))[0].strip()
        title = re.sub(r"^(?:室内|室外|现代|一间|一家|一座)+", "", title)
        if title == base(name):
            primary.append(card)
    if len(primary) == 1:
        return primary[0]
    if re.search(r"另一|另外|别的|其他|别处", name):
        return None
    # An interior camera position is not a new set when the current episode
    # explicitly keeps the same place and that landmark belongs to the old card.
    # Never infer identity from a shared location type alone.
    anchors = re.findall(r"同一(?:个|间|家|座|片|处|张)?([^\s，,。！？!?；;、：:]{1,20})", str(context or ""))
    anchors = [re.sub(r"(?:里面|内部|内|里)$", "", a) for a in anchors]
    if not anchors:
        return None

    def position(value):
        match = re.fullmatch(r"(.+?)(?:里面|内部|内|里)(.*)", value)
        return (match[1], match[2]) if match else (value, "")

    def anchored(root, anchor):
        return root == anchor or (root in {"店", "屋", "馆", "厅", "殿", "堂", "房", "院", "林", "洞", "室"}
                                  and anchor.endswith(root))

    def landmark_exists(rest, description):
        if not rest:
            return True
        match = re.fullmatch(r"(.+?)(?:左侧|右侧|左边|右边|附近|前|后|旁|边)", rest)
        return bool(match and match[1] in description)

    root, rest = position(name)
    # A model may shorten “店内工作台前” to just “工作台”. Reuse only an
    # explicitly unchanged furnishing within an explicitly unchanged parent set;
    # rooms/floors/exteriors are independent locations, never furniture aliases.
    landmark = re.sub(r"(?:左侧|右侧|左边|右边|附近|前|后|旁|边)$", "", name)
    furnishing = bool(landmark in anchors and not re.search(r"房|室|院|楼|层|外|另一|别的", landmark)
                      and re.search(r"(?:工作台|操作台|修理台|检修台|实验台|控制台|吧台|讲台|柜台|书桌|餐桌|办公桌|桌子|茶几|沙发|长椅|座椅|椅子|床|柜子|货架|书架)$", landmark))
    found = []
    for card in cards:
        description = "\n".join(str(card.get(k) or "") for k in
                                ("name", "space", "contract_text", "landmarks", "fixed_landmarks", "furniture", "layout"))
        if not landmark_exists(rest, description):
            continue
        for old_name in aliases(card):
            old_root, old_rest = position(old_name)
            if furnishing and landmark in description and landmark_exists(old_rest, description) and any(
                    a != landmark and a in description and anchored(old_root, a) for a in anchors):
                found.append(card)
                break
            if landmark_exists(old_rest, description) and any(
                    a in description and anchored(root, a) and anchored(old_root, a) for a in anchors):
                found.append(card)
                break
    return found[0] if len(found) == 1 else None


def source_narration(source):
    """Retain speakers and action lines, excluding people named inside dialogue."""
    narrative = re.sub(r'[“「『\"]([^”」』\"\n]*)[”」』\"]', "", str(source or ""))
    from .oral_story import _DLG_LINE
    lines = []
    metadata = {"旁白", "画外音", "内心", "心声", "音效", "场景", "地点", "时间", "画面", "镜头", "人物", "道具", "说明"}
    for line in narrative.splitlines():
        body = re.sub(r"^\s*【D\d+】\s*", "", line)
        match = _DLG_LINE.match(body.strip())
        if match and match[1] not in metadata:
            # A speaking person is an asset; the people/places in their words are
            # not. Parenthesized stage directions remain action evidence.
            lines.append(match[1] + (match[2] or ""))
        else:
            lines.append(line)
    return "\n".join(lines)


def arrived_locations(source):
    """Explicit completed movement destinations, with source evidence, no model.

    This supplements extraction when its first-location label misses a later
    arrival. It never treats dialogue plans or ordinary furniture as new sets.
    """
    narrative = source_narration(source)
    ending = r"(?:小院|庭院|院子|花园|卧室|书房|客厅|厨房|办公室|大厅|房间|店铺|修表店|商店|酒馆|客栈|仓库|广场|街道|巷子|山谷|森林|海滩|码头|车站|院|店|厅|室)"
    pattern = re.compile(r"(?:走到|走进|进入|来到|抵达|踏上|步入|走入|跨入|到达)\s*([^，,。！？!?；;\n]{1,28}?" + ending + r")")
    result = []
    for match in pattern.finditer(narrative):
        lead = re.split(r"[，,。！？!?；;\n]", narrative[:match.start()])[-1]
        if re.search(r"(?:准备|打算|计划|将要|想要|尚未|还没|还未|没有|未曾)[^，,]{0,12}$", lead):
            continue
        name = match[1].strip()
        # Direction and surface adjectives do not identify another location;
        # named ownership (e.g. 林海的店) remains intact.
        name = re.sub(r"^(?:门外|屋外|相邻|旁边|前方|后方)(?:[^的]{0,8}的)?", "", name)
        if not name or any(item["name"] == name for item in result):
            continue
        result.append({"name": name, "evidence": match[0], "pos": match.start()})
    return result


def appears_in_source(name, source, kind="人物", evidence=""):
    """Reject invented and mention-only extraction results, without adding actors.

    This is an asset check, not a frame cast resolver. Leaving/vanishing on camera
    still needs an asset; a person merely described as missing does not.
    """
    name = str(name or "").strip()
    source = str(source or "")
    if not name or name not in source:
        return False
    # Dialogue can refer to absent people/places. Keep speaker labels and narration.
    narrative = source_narration(source)
    for clause in re.split(r"[。！？!?；;\n]", narrative):
        for match in re.finditer(re.escape(name), clause):
            before, after = clause[:match.start()], clause[match.end():]
            tail = re.split(r"[，,]", after, maxsplit=1)[0]
            lead = re.split(r"[，,]", before)[-1]
            if re.search(r"(?:寻找|找不到|寻觅|想起|提到|谈起|说起|怀念|听说|关于|等待|等着|担心|呼唤|没有见到|未见到)\s*$", lead):
                continue
            if re.match(r"\s*(?:已经|早已|仍然|仍|一直|已)?(?:失踪|不在|下落不明|未出现|没出现|没有出现|不见了|已离开|早已离开)", tail):
                continue
            if re.match(r"\s*的(?:名字|消息|踪迹|线索|传说|故事|遗物|失踪|画像|照片)", tail):
                continue
            if kind == "场景":
                if re.search(r"(?:打算|准备|计划|想要|将要|尚未|还没|还未)[^，,]{0,12}$", lead):
                    continue
                if not (re.search(r"(?:在|到|抵达|进入|走进|穿过|离开|走出|返回)\s*$", lead)
                        or re.match(r"\s*(?:里|内|中|外|前|后|旁|：|:|｜|—)", tail)
                        or (not lead.strip("─—- 《【[（(") and not tail.strip("─—- 》】]）)"))
                        or (evidence and evidence in source and name in evidence)):
                    continue
            return True
    return False
