# -*- coding: utf-8 -*-
"""director_shots.py — 导演分段（P435，2026-09-19）。

用户 9-19 看了手写 13 段的前 5 段成片后定：本地链路按手写提示词的格式改。
手写版和旧「一句一拍」的差别只有一个：输入单位。旧法拿一句话出一拍（4～6 秒硬切，全员参考图，
结束状态由模型每段现编，程序事后查矛盾）；导演分段拿**一件事**（正文一个段落）出 2～3 段镜头单，
每段一个连续镜头 11～15 秒、段内机位只运动不切、只列画面里的人、段末状态在同一次调用里连着写。

流程：
  shot_list(pics, scenes, chars, q)        剧本按空行分件 → 每件一次模型调用（带上一段结束状态）→ 解析 → 程序修
      → [slice]   每段：text / seconds / scene_hint / shot / establish / cut=True / who / pace="导演" / director={...}
  director 字段：{purpose, camera, cast, blocks:[[a,b,text,[D编号]]], end_state:{名:{pos,facing,hands,posture,side}}, says:[(名,句)]}
  提示词那步（authoring._h3_timeline_body）拿 director 当骨架：模型只扩写时间块，【机位】行和「这一段结束时」由程序写死。

程序做的（模型不做）：秒数=最后一块止点、超 15 按块拆两段、不足 8 并进同地点下一段；台词编号每个只用一次按顺序，
漏的补到说话人所在的块；在场只认卡名；地点对场景卡（对不上沿用上一段）；结束状态每个在场的人一行，缺的沿用上一段。"""
import json
import os
import re
import time

from . import oral_story as _os_

_HEAD = re.compile(r"^\s*【?段\s*\d*】?\s*[：:]?\s*(.*)$")
MAX_DIDS = 4
_KV = re.compile(r"(地点|在场|秒|机位|目的|结束)\s*[=＝：:]\s*")
_BLOCK = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[-—–~～]\s*(\d+(?:\.\d+)?)\s*秒?\s*[=＝：:]\s*(.+?)\s*$")
_BLOCK_INLINE = re.compile(r"[；;｜|]\s*(?=\d+(?:\.\d+)?\s*[-—–~～]\s*\d+(?:\.\d+)?\s*秒?\s*[=＝：:])")   # P454⑤：一行多块
_DREF = re.compile(r"[（(【\[]?\s*D\s*(\d+)\s*[）)】\]]?")
_DQUOTE = re.compile(r"[，,]?\s*[（(【\[]?\s*D\s*(\d+)\s*[）)】\]]?\s*(?:[^：:，。；！？\s]{1,8}[：:]\s*[^。！？\n]*[。！？]?)?")   # P436：「【D1】张明：风浪有点大。」整块
_SIDE = re.compile(r"画面?(左|右)|(左|右)侧")
_SHOT_MAP = (("全景", r"全景|远景|大全景|大远景"), ("特写", r"特写|近景|大特写"), ("中景", r"中景|中近景|半身"))
_POSTURE = re.compile(r"躺|趴|卧|坐|跪|蹲|站|立|走|跑|行走|骑")
MIN_SEC, MAX_SEC, TARGET_LO = 8, 15, 11


def _hz(t):
    return re.sub(r"[^一-龥]", "", str(t or ""))


def shot_of(camera):
    t = str(camera or "")
    for k, rx in _SHOT_MAP:
        if re.search(rx, t):
            return k
    return "中景"


def split_events(pics, n_events=0, event_map=None):
    """剧本分成「件」。P443：先按忠实核对的「事件→段落」映射表分（段落按空行数，1 起），没映射到的段落归前一件；
    没有映射表：段落数 ≤ 事件数×1.5 就一段落一件，否则按事件数把段落按字数均分；没有事件数就按空行。"""
    t = str(pics or "").replace("\r", "")
    parts = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    if len(parts) <= 1:
        parts = [p.strip() for p in re.split(r"(?=^── )", t, flags=re.M) if p.strip()]
    parts = parts or ([t.strip()] if t.strip() else [])
    n_events = int(n_events or 0)
    if isinstance(event_map, dict) and event_map and len(parts) > 1:
        owner = {}
        for k, idxs in sorted(event_map.items(), key=lambda kv: int(re.sub(r"\D", "", str(kv[0])) or 0)):
            for i in idxs or []:
                try:
                    owner.setdefault(int(i), int(re.sub(r"\D", "", str(k)) or 0))
                except Exception:
                    pass
        if owner:
            groups, cur = {}, None
            for i, p in enumerate(parts, 1):
                ev = owner.get(i, cur if cur is not None else min(owner.values()))
                cur = ev
                groups.setdefault(ev, []).append(p)
            return ["\n".join(groups[k]) for k in sorted(groups)]
    if n_events and len(parts) > n_events * 1.5:
        total = sum(len(_hz(p)) for p in parts)
        target = total / n_events
        groups, cur, acc = [], [], 0
        for p in parts:
            cur.append(p)
            acc += len(_hz(p))
            if acc >= target and len(groups) < n_events - 1:
                groups.append("\n".join(cur))
                cur, acc = [], 0
        if cur:
            groups.append("\n".join(cur))
        return groups
    return parts


SPLIT_OVER = 450      # 一件事的剧本超过这么多汉字就拆
SPLIT_TARGET = 300    # 拆成约这么多汉字一份


def split_long_events(events, event_points=None):
    """P458：正文很长但归并只分了几件事（项目4：3 件事、第 1 件 1328 字）→ 每件事按段落再拆成约 300 字一份，
    每份当一件事去拍（2～3 段）。返回 (新事件列表, 对齐的要点列表)。要点只留给每件事的第一份。"""
    out, pts = [], []
    for i, ev in enumerate(events or []):
        pt = (event_points[i] if event_points and i < len(event_points) else "")
        if len(_hz(ev)) <= SPLIT_OVER:
            out.append(ev)
            pts.append(pt)
            continue
        paras = [p for p in str(ev).split("\n") if p.strip()]
        n_parts = max(2, round(len(_hz(ev)) / SPLIT_TARGET))
        target = len(_hz(ev)) / n_parts
        chunks, cur, acc = [], [], 0
        for p in paras:
            cur.append(p)
            acc += len(_hz(p))
            if acc >= target * 0.85 and len(chunks) < n_parts - 1:
                chunks.append("\n".join(cur))
                cur, acc = [], 0
        if cur:
            if chunks and len(_hz("\n".join(cur))) < 80:
                chunks[-1] = chunks[-1] + "\n" + "\n".join(cur)               # 尾巴太短并进上一份
            else:
                chunks.append("\n".join(cur))
        for k, c in enumerate(chunks):
            out.append(c)
            pts.append(pt if k == 0 else "")
    return out, pts


POV_MODES = ("第一视角POV", "自拍手持")


def pov_clause(pov, owner):
    """P461：给导演的视点规矩。"""
    if not owner or pov not in POV_MODES:
        return ""
    if pov == "第一视角POV":
        return ("9. 视点：整话是%s的第一视角，镜头就是%s的眼睛。在场栏不写%s（他在镜头后面）；每块写%s看到的：对方看向镜头说话、对方的位置用「正前方/左手边/越来越近」；"
                "%s自己只以手、袖口、手里的东西入画，不写他的脸和表情；机位行只写「%s主观视角」加景别。\n" % ((owner,) * 6))
    return ("9. 视点：整话是%s单手举着相机自拍。在场栏必须有%s，机位行只写「%s手持自拍」加景别；每块里%s的脸在前景看着镜头，另一个人要入镜就是凑到%s身边一起对着镜头；"
            "背景在身后。\n" % ((owner,) * 5))


def apply_pov(shots, pov, owner):
    """P461：分段后按视点钉死：POV → 拍摄者出在场/结束状态、机位行=「X主观视角，<景别>」；自拍 → 拍摄者必在在场、机位行=「X手持自拍，<景别>」。"""
    if not owner or pov not in POV_MODES:
        return shots
    for s in shots:
        sz = shot_of(s.get("camera") or "")
        if pov == "第一视角POV":
            s["cast"] = [n for n in s["cast"] if n != owner]
            s["end_state"].pop(owner, None)
            s["camera"] = "%s主观视角（镜头就是%s的眼睛，眼睛高度，跟着头轻微动），%s" % (owner, owner, sz)
        else:
            if owner not in s["cast"]:
                s["cast"] = [owner] + list(s["cast"])
            s["camera"] = "%s单手举相机手持自拍（一臂距离，略高角度俯拍，%s的脸在前景看着镜头），%s" % (owner, owner, sz)
    return shots


def instruction(scene_names, char_items, pov="", owner=""):
    ch = "、".join("%s（%s%s）" % (n, s or "", (str(a) + "岁") if a and a > 0 else "") for n, s, a in char_items) or "剧本里的人"
    return ("你是短剧导演。我给你这一话里的一件事（剧本的一段）、上一段结束时每个人的状态、可用的地点和人物。"
            "你把这件事拍成 2～3 段视频：每段是一个连续镜头，11～15 秒，一段只讲一个目的。\n"
            "每段固定写这几行（行首字样一字不改，<>里换成这一件事的内容）：\n"
            "【段】地点=<地点名>｜在场=<画面里的人名，顿号分开>｜秒=<11～15>｜机位=<景别，机位高低，固定或推/拉/摇/跟，谁在画面左侧谁在右侧>\n"
            "目的=<这一段让观众知道的一件事，一句>\n"
            "0-4秒=<谁做什么，对方什么反应；这一块有台词就在句末写编号，如 D3>\n"
            "4-9秒=<…>\n"
            "9-13秒=<…>\n"
            "结束=<人名>：<位置>，<面朝谁或哪边>，<手里什么>，<姿势：站/坐/蹲/躺/趴/走>，<画面左侧/右侧/中央>；<下一个人>：…\n"
            "规矩：\n"
            "1. 每件事 2～3 段；每段 11～15 秒；换地点就换段。\n"
            "2. 一段一个机位：段内可以推、拉、摇、跟，景别随运动变化；每段 3～4 个时间块，第一块 0 秒就有动作，最后一块的止点等于秒数。\n"
            "3. 每块一句话：谁做什么、对方什么反应，都是看得见的动作；心理、比喻、观众已经知道的事（报名字）不拍；同一种情绪只拍一次。\n"
            "4. 台词已编号（D1、D2…），写在它该说的那一块句末；每个编号用一次、按顺序、不跳过；一段最多 3 个编号。\n"
            "5. 在场=只写这一段画面里的人；画面外的人只在动作里写「望向画外」。地点只从这些名字里选：%s。人只能是：%s。"
            "剧本里没在这个名单上的群众（村民、路人、几名女子）在时间块里按剧本里的称呼写（几名＋剧本的称呼＋从哪侧走过），在场栏不写他们；名单上的人没在这件事的剧本里出现就不要拉进画面。\n"
            "6. 结束=每个在场的人一条，从上一段结束时的状态起，本段没改变的项照抄。\n"
            "7. 姿势和地点相配：水里的人趴着或抱着木板漂，地上的人才站、坐、蹲。\n"
            "8. 同一地点连着的几段，每个人的画面左右和上一段结束时一样；要换边就在时间块里写出他走过去。\n"
            + pov_clause(pov, owner) +
            "只输出段，不解释。") % ("、".join(scene_names or []) or "剧本里的地点", ch)


_EXTRA_PHRASE = re.compile(r"(?:几名|几个|一群|数名|两名|三名|四名|五名|众多|一些|一名|一个)[一-龥]{1,6}?(?:精灵|女子|男子|村民|路人|行人|女人|男人|士兵|守卫|孩子|老人|人)|(?:精灵|村民|女子|女人|男人|路人|士兵|孩子|女孩|姑娘|村人|众人)们")


def extras_phrases(d, names):
    """P454④：骨架块里写到的群众（几名女精灵、女子们）→ 短语表；含卡名的不算。"""
    have = [_os_.short_name(n) for n in (names or []) if n]
    out = []
    for _a, _b, txt, _ds in (d or {}).get("blocks") or []:
        for m in _EXTRA_PHRASE.finditer(str(txt or "")):
            w = m.group(0)
            if len(w) > 8 or w in out or any(h and (h in w or w in h) for h in have) or re.fullmatch(r"(他们|她们|我们|你们|人们)", w):
                continue
            out.append(w)
    return out[:4]


def _canon_names(text, aliases):
    out = []
    for a in sorted(aliases, key=len, reverse=True):
        if a and a in str(text or "") and aliases[a] not in out:
            out.append(aliases[a])
    return out


def _strip_quotes(txt, D):
    """P442：块文本里抄的台词整句（「小可：张哥，开饭」「说：“鱼烤好了。”」）去掉；按台词正文的汉字对。"""
    t = str(txt or "")
    for spk, q in D or []:
        qh = re.sub(r"[^一-龥]", "", q)
        if len(qh) < 2:
            continue
        pat = r"[^。；，]{0,6}[：:]?\s*[“\"「]?" + ".{0,2}".join(map(re.escape, list(qh))) + r"[！!？?。]?[”\"」]?[。；，]?"
        t = re.sub(pat, lambda m: "" if re.sub(r"[^一-龥]", "", m.group(0)).endswith(qh) or qh in re.sub(r"[^一-龥]", "", m.group(0)) else m.group(0), t)
    return re.sub(r"[，；]{2,}", "；", t).strip(" ，。；,;")


def parse(raw, scene_names, char_names, D, aliases=None):
    """模型输出 → [shot]。shot = {place, cast, seconds, camera, purpose, blocks:[[a,b,text,[dids]]], end_state:{}}"""
    aliases = aliases or _os_.aliases_of(char_names)
    shots, cur = [], None
    _lines = []
    for ln in str(raw or "").splitlines():
        _lines.extend(_BLOCK_INLINE.split(ln) if _BLOCK.match(ln.strip()) else [ln])                  # P454⑤：「0-4秒=…；4-9秒=…」拆行
    for ln in _lines:
        st = ln.strip().replace("<", "").replace(">", "").replace("＜", "").replace("＞", "")      # P439：抄的占位尖括号
        if not st:
            continue
        if (re.match(r"^【?段\s*\d*】?", st) and _KV.search(st)) or re.fullmatch(r"【?段\s*\d*】?\s*[：:]?", st):
            cur = {"place": "", "cast": [], "seconds": 0, "camera": "", "purpose": "", "blocks": [], "end_state": {}}
            shots.append(cur)
            body = _HEAD.match(st).group(1)
            for k, v in _kv_pairs(body):                                       # P448③：「【段1】」单独一行时这里没有键值，下面几行会补
                _set_kv(cur, k, v, aliases)
            continue
        if cur is None:
            continue
        mb = _BLOCK.match(st)
        if mb:
            a, b, txt = float(mb.group(1)), float(mb.group(2)), mb.group(3)
            dids = [int(x) for x in _DREF.findall(txt) if 1 <= int(x) <= len(D)]
            txt = _DQUOTE.sub("", txt).replace("｜", "；").replace("|", "；").strip(" ，。；,;")   # P437：块里的「｜」换成「；」
            txt = re.sub(r"[。；，]\s*；", "；", txt)
            txt = _strip_quotes(txt, D) or txt
            txt = re.sub(r"[；，]\s*画面(?:左|右|中)(?:侧|央)?的[^；，。]{1,8}(?=[；，。]|$)", "", txt).strip(" ，。；")   # P442b：导演写在块尾的站位注
            if txt:
                cur["blocks"].append([a, b, txt, dids])
            continue
        m = re.match(r"^(目的|结束|机位|地点|在场|秒)\s*[=＝：:]\s*(.*)$", st) or re.match(r"^(结束)\s*([^\s=＝：:].*)$", st)   # P437：「结束张明：…」漏了 =
        if m:
            if m.group(1) != "结束" and _KV.search(st[m.end(1):]):              # P448③：一行里跟着「｜在场=…｜秒=…」→ 整行按键值对拆
                for k, v in _kv_pairs(st):
                    _set_kv(cur, k, v, aliases)
            else:
                _set_kv(cur, m.group(1), m.group(2), aliases)
    return shots


def _kv_pairs(body):
    pos = [(m.start(), m.end(), m.group(1)) for m in _KV.finditer(body)]
    for i, (s, e, k) in enumerate(pos):
        end = pos[i + 1][0] if i + 1 < len(pos) else len(body)
        yield k, body[e:end].strip(" ｜|，,")


def _set_kv(cur, k, v, aliases):
    v = str(v or "").strip()
    if k == "地点":
        cur["place"] = v.strip("，。 ")
    elif k == "在场":
        cur["cast"] = _canon_names(v, aliases)
        cur["_cast_raw"] = v
    elif k == "秒":
        m = re.search(r"\d+(?:\.\d+)?", v)
        cur["seconds"] = float(m.group(0)) if m else 0
    elif k == "机位":
        cur["camera"] = v.strip("。")
    elif k == "目的":
        cur["purpose"] = v.strip("。")
    elif k == "结束":
        cur["end_state"].update(parse_end_state(v, aliases))


def parse_end_state(v, aliases):
    out = {}
    for item in re.split(r"[；;]\s*", str(v or "")):
        m = re.match(r"^\s*([^：:]{1,10})[：:]\s*(.+)$", item.strip())
        if not m:
            continue
        nm = _os_.canon(m.group(1), aliases)
        if not nm:
            continue
        parts = [re.sub(r"^(位置|面朝|朝向|手里|手|姿势|画面位置)\s*[：:]\s*", "", x.strip("。 ")) for x in re.split(r"[，,｜|]", m.group(2)) if x.strip("。 ")]
        # P436：按标签认栏，不按顺序：最后一个带「左/右/中央」的是画面位置；「面朝/朝/背对」是朝向；带「手/无」是手里；站坐蹲躺趴跪走是姿势
        side = next((x for x in reversed(parts) if _SIDE.search(x) or "中央" in x), "")
        rest = [x for x in parts if x is not side]
        facing = next((x for x in rest if re.match(r"^(面朝|朝|背对|侧对|看向|望向)", x)), "")
        rest2 = [x for x in rest if x != facing]
        posture = next((x for x in rest2 if re.search(r"^(站|坐|蹲|躺|趴|跪|走|立|半跪|半蹲|俯身|弯腰)", x) or re.fullmatch(r"(站立|站着|坐着|蹲着|躺着|趴着|跪着|行走)", x)), "")
        rest3 = [x for x in rest2 if x != posture]
        hands = next((x for x in rest3 if re.search(r"手|无|空着|握|拿|提|抓|捧|扶|端|攥|抱", x)), "")
        rest4 = [x for x in rest3 if x != hands]
        pos = rest4[0] if rest4 else (side if side else "")
        if not hands and len(rest4) >= 2:
            hands = rest4[1]                                                    # P448⑥：「木板」这种没动词的手里物
        out[nm] = {"pos": pos, "facing": facing, "hands": hands, "posture": _norm_posture(posture), "side": norm_side(side)}
    return out


def _norm_posture(p):
    p = str(p or "").strip()
    for k, v in (("站", "站立"), ("坐", "坐着"), ("蹲", "蹲着"), ("躺", "躺着"), ("趴", "趴着"), ("跪", "跪着"), ("走", "行走"), ("立", "站立")):
        if p.startswith(k) and len(p) <= 3:
            return v
    return p


def norm_side(s):
    t = str(s or "")
    if "左" in t:
        return "画面左侧"
    if "右" in t:
        return "画面右侧"
    return "画面中央"


# ─────────── 程序修 ───────────

_TIME_W = re.compile(r"清晨|早晨|早上|黎明|破晓|正午|中午|午后|黄昏|傍晚|夜晚|夜里|深夜|夜")


def _time_fallback(place, scene_names, ev_text):
    """P441：卡名带时间词（沙滩·清晨），这件事的剧本没写那个时间 → 换成同名不带时间的卡（沙滩）。"""
    m = _TIME_W.search(str(place or ""))
    if not m or m.group(0) in str(ev_text or ""):
        return place
    base = re.sub(r"[·\-—（(].*$", "", place).strip()
    base = _TIME_W.sub("", base).strip("·-—（）() ")
    for n in scene_names:
        if n != place and (n == base or (base and n.startswith(base) and not _TIME_W.search(n))):
            return n
    _cls = _os_.place_class(place)
    for n in scene_names:                                                     # P441b：同类里不带时间词的卡
        if n != place and not _TIME_W.search(n) and _cls and _os_.place_class(n) == _cls:
            return n
    return place


_HE = re.compile(r"他")
_SHE = re.compile(r"她")


def _mentioned(ev_text, char_names, char_sex, aliases):
    """这件事的剧本提到了谁：卡名/短名/短名的头两字和尾两字；只有一个男卡时「他」算提到他，只有一个女卡时「她」算提到她。"""
    t = str(ev_text or "")
    out = set(_canon_names(t, aliases))
    for n in char_names:
        sn = _os_.short_name(n)
        for frag in {sn[:2], sn[-2:], sn[-3:]} if len(sn) >= 3 else set():
            if len(frag) >= 2 and frag in t and not re.search(r"[的之个了着]", frag):
                out.add(n)
    sx = char_sex or {}
    males = [n for n in char_names if sx.get(n) == "男"]
    females = [n for n in char_names if sx.get(n) == "女"]
    if len(males) == 1 and _HE.search(t):
        out.add(males[0])
    if len(females) == 1 and _SHE.search(t):
        out.add(females[0])
    return out


_GROUP_IN_TEXT = re.compile(r"几名|几个|一群|数名|众人|她们|他们|村民|路人|众精灵|精灵们|女子们|女人们|人群|人影")
_SPECIES = re.compile(r"精灵|兽人|矮人|人鱼|机器人|吸血鬼|狼人|龙人|天使|恶魔")


def _person_keys(nm, aliases):
    ks = {nm, _os_.short_name(nm)} | {a for a, full in (aliases or {}).items() if full == nm and len(a) >= 2}
    return sorted((k for k in ks if k), key=len, reverse=True)


def _scrub_person(blocks, nm, aliases=None):
    """块文本里写这个人的分句去掉（他不在这一段画面里）：按逗号/分号分句，只删提到他的那几句；整块都是他的就删块。"""
    keys = _person_keys(nm, aliases)
    out = []
    for a, b, txt, ds in blocks:
        parts = [p for p in re.split(r"[；;，,]", str(txt or "")) if p.strip()]
        if parts and any(k in parts[0] for k in keys) and not ds:
            continue                                                            # 第一句就是他（整块是他的动作）→ 整块删
        keep = [p for p in parts if not any(k in p for k in keys)]
        if keep:
            out.append([a, b, "，".join(x.strip() for x in keep), ds])
        elif ds:
            out.append([a, b, parts[0] if parts else txt, ds])                 # 带台词的块不删，留原文
    return out


def _as_extra(blocks, nm, sex, char_type, aliases=None):
    """这个人不在场、但剧本里有群众 → 名字换成「一名女精灵」这类群演称呼，动作留下（P454⑫）。"""
    sp = _SPECIES.search(str(char_type or "") + str(nm or ""))
    word = "一名" + (("女" if sex == "女" else ("男" if sex == "男" else "")) + sp.group(0) if sp else ("女子" if sex == "女" else ("男子" if sex == "男" else "路人")))
    keys = _person_keys(nm, aliases)
    out = []
    for a, b, txt, ds in blocks:
        t = str(txt or "")
        for k in keys:
            t = t.replace(k, word)
        t = re.sub(r"(" + re.escape(word) + r")(?:与|和|、)" + re.escape(word), r"\1和另一名" + word[2:], t)   # 「一名女精灵与一名女精灵」
        out.append([a, b, t, ds])
    return out


def _retime(s):
    t = 0.0
    for b in s["blocks"]:
        dur = max(2.0, round(b[1] - b[0])) if b[1] > b[0] else 3.0
        b[0], b[1] = t, t + dur
        t += dur
    s["seconds"] = t


def guard_cast(shots, ev_text, char_names, char_sex, aliases, prev_place, prev_cast, char_type=None):
    """P454⑥：在场 ⊆ 剧本提到的人 ∪ 上一段同地点在场的人（项目1：少年在木屋醒来，导演把矮个子女精灵拉进每一段）。
    剪掉的人：块里写他的分句去掉，结束状态里去掉。全剪光就不剪（不留空镜）。返回剪掉的名单。"""
    if not shots or not char_names or not str(ev_text or "").strip():
        return []
    ment = _mentioned(ev_text, char_names, char_sex, aliases)
    removed_all = []
    carry = set(prev_cast or [])
    carry_place = prev_place
    for s in shots:
        allowed = set(ment) | (carry if (carry_place and s.get("place") == carry_place) else set())
        spk = {(_os_.canon(D0[0], aliases) or D0[0]) for D0 in (s.get("says") or [])}
        allowed |= spk
        cut = [n for n in s["cast"] if n not in allowed]
        if cut and len(cut) < len(s["cast"]):
            s["cast"] = [n for n in s["cast"] if n not in cut]
            _grp = bool(_GROUP_IN_TEXT.search(str(ev_text or "")))
            for n in cut:
                nb = (_as_extra(s["blocks"], n, (char_sex or {}).get(n, ""), (char_type or {}).get(n, ""), aliases) if _grp
                      else _scrub_person(s["blocks"], n, aliases))
                if nb:
                    s["blocks"] = nb
                s["end_state"].pop(n, None)
                _sn = _os_.short_name(n)
                _keys = [k for k in {_sn, _sn[-2:], _sn[-3:]} if len(k) >= 2 and k not in ("女性", "男性")]
                s["camera"] = "，".join(x for x in re.split(r"[，,]", str(s.get("camera") or "")) if x.strip() and not any(k in x for k in _keys)).strip("，, ")
            _retime(s)
            removed_all.extend(cut)
        carry, carry_place = set(s["cast"]), s.get("place")
    return removed_all


_MOVE_W = re.compile(r"走出|走进|走到|走向|走回|出门|出去|出来|进屋|进门|进去|进来|来到|到了|抵达|跨出|跨过|跨进|推开|离开|回到|回房|押回|押进|押到|带到|带回|带进|带他|领他|"
                     r"进入|穿过|穿行|奔|跑到|跑进|跑出|爬上|爬下|下山|上山|登上|落下|坠|摔进|掉进|醒来|醒了|醒过来|睁开双眼|睁开眼|睁眼|苏醒|意识回|意识浮|意识渐|昏迷|昏过去|昏了|转移|搬到|换到|拖到|拖进|扛到|抬到|抬进|送到|送进|飞到|冲进|冲出|逃到|逃进|逃出|躲进|钻进|翻过|越过|渡过|上船|下船|上车|下车|上路|启程|出发|回来|回去")


def _place_hits(text, name, char_names=()):
    """剧本文字里出现这个地点的证据：整名、名字的两字片段（村庄/街道/木屋/客房）、同类词（滩/岸/路/街…）。
    P454⑯：场景名里含人名的部分（「小个子精灵的房间」）先去掉，再取片段。"""
    t = str(text or "")
    nm = re.sub(r"[·\-—（(].*$", "", str(name or "")).strip()
    n = t.count(nm) if nm else 0
    for _cn in sorted({x for c in (char_names or []) for x in (c, _os_.short_name(c)) if x}, key=len, reverse=True):
        if _cn and _cn in nm and _cn != nm:
            nm = nm.replace(_cn, "")
    for i in range(len(nm) - 1):
        frag = nm[i:i + 2]
        if not re.search(r"[的之处里内外上下]", frag):
            n += t.count(frag)
    cls = _os_.place_class(nm)
    for c, rx in _os_._PLACE_CLASSES:
        if c == cls:
            n += len(re.findall(rx, t))
            break
    return n


def guard_place(shots, ev_text, scene_names, prev_place, char_names=()):
    """P454⑦：模型选的地点在这件事的剧本里一个证据都没有、别的地点有 → 换成证据最多的那个（唯一最多）；都没证据不动。
    （项目1：出木屋被拦在街上，模型写成林地。）"""
    if not shots or not scene_names:
        return
    _narr = "\n".join(l for l in str(ev_text or "").splitlines() if not re.match(r"^\s*【D\d+】", l))   # 台词里提到的地名不算证据（「你擅自闯入我们的村庄」）
    for _n in sorted({x for nm in (char_names or []) for x in (nm, _os_.short_name(nm)) if x}, key=len, reverse=True):
        _narr = _narr.replace(_n, "")                                            # 人名不算地名证据（「林恩」里的「林」曾把房间判成森林）
    hits = {n: _place_hits(_narr, n, char_names) for n in scene_names}
    best = sorted(hits.items(), key=lambda kv: -kv[1])
    top = best[0][0] if best and best[0][1] > 0 and (len(best) == 1 or best[0][1] > best[1][1]) else ""
    _moved = bool(_MOVE_W.search(_narr))
    for s in shots:
        p = s.get("place") or ""
        if p not in scene_names or hits.get(p, 0) > 0 or p == prev_place:
            continue                                                            # 有证据、或接着上一件事的地点 → 不动
        if top:
            s["place"] = top
        elif prev_place in scene_names and not _moved:
            s["place"] = prev_place                                             # P454⑯：谁都没证据、剧本里也没人换地方 → 接着上一处；有「醒来/走出」才信模型


def fix_shots(shots, D, expect_dids, scene_names, char_names, prev_state, prev_place, aliases=None, ev_text="", char_sex=None, prev_cast=None, char_type=None):
    aliases = aliases or _os_.aliases_of(char_names)
    shots = [s for s in shots if s.get("blocks")]
    # ① 块头归整：按顺序连续、最后止点=秒；块内空秒去掉
    for s in shots:
        bl = s["blocks"]
        t = 0.0
        for b in bl:
            dur = max(2.0, round(b[1] - b[0])) if b[1] > b[0] else 3.0
            b[0], b[1] = t, t + dur
            t += dur
        s["seconds"] = t
    # ①b 地点对场景卡（P454⑩：提前到拆段/并段之前，守卫剪人后变短的段能被并掉）
    last = prev_place
    for s in shots:
        p = _match_place(s["place"], scene_names) or last or (scene_names[0] if scene_names else s["place"])
        p = _time_fallback(p, scene_names, ev_text)
        s["place"] = p
        last = p
    guard_place(shots, ev_text, scene_names, prev_place, char_names)           # P454⑦
    for s in shots:
        s["says"] = [(_os_.canon(D[d - 1][0], aliases) or D[d - 1][0], D[d - 1][1]) for b in s["blocks"] for d in b[3] if 1 <= d <= len(D) and d in expect_dids]   # 别件事的编号不算
    fix_shots.last_cut = guard_cast(shots, ev_text, char_names, char_sex, aliases, prev_place, prev_cast, char_type)   # P454⑥
    shots = [s for s in shots if s.get("blocks")]
    # ② 超 15 拆两段（按块边界，靠近一半处）；结束状态前一半留空（后面按上一段补）
    out = []
    for s in shots:
        if s["seconds"] > MAX_SEC and len(s["blocks"]) == 1:                    # P448⑤：一块 20 秒 → 对半拆成两块再走拆段
            a, b, t, ds = s["blocks"][0]
            mid = round((a + b) / 2)
            s["blocks"] = [[a, mid, t, ds], [mid, b, t, []]]
        while s["seconds"] > MAX_SEC and len(s["blocks"]) >= 2:
            half, acc, k = s["seconds"] / 2, 0.0, 0
            for i, b in enumerate(s["blocks"]):
                acc += b[1] - b[0]
                k = i + 1
                if acc >= half:
                    break
            k = min(max(1, k), len(s["blocks"]) - 1)
            _later = " ".join(b[2] for b in s["blocks"][k:])
            # 拆开的前一半：后一半没再提到的人，状态就是整段的结束状态
            first = dict(s, blocks=[list(b) for b in s["blocks"][:k]], purpose=s["purpose"],
                         end_state={nm: dict(e) for nm, e in s["end_state"].items() if _os_.short_name(nm) not in _later})
            first["seconds"] = sum(b[1] - b[0] for b in first["blocks"])
            rest = [list(b) for b in s["blocks"][k:]]
            off = rest[0][0]
            for b in rest:
                b[0], b[1] = b[0] - off, b[1] - off
            out.append(first)
            s = dict(s, blocks=rest, seconds=sum(b[1] - b[0] for b in rest), purpose=s["purpose"] + "（续）")
        out.append(s)
    shots = out
    # ③ 不足 8 秒：并进同地点、同在场的下一段（合起来 ≤15）
    out = []
    i = 0
    while i < len(shots):
        s = shots[i]
        nx = shots[i + 1] if i + 1 < len(shots) else None
        if (s["seconds"] < MIN_SEC and nx and nx["place"] == s["place"] and (set(nx["cast"]) == set(s["cast"]) or s["seconds"] <= 6)
                and s["seconds"] + nx["seconds"] <= MAX_SEC):
            off = s["seconds"]
            merged = dict(nx, blocks=[list(b) for b in s["blocks"]] + [[b[0] + off, b[1] + off, b[2], list(b[3])] for b in nx["blocks"]],
                          seconds=s["seconds"] + nx["seconds"], camera=s["camera"] or nx["camera"],
                          cast=list(dict.fromkeys(list(s["cast"]) + list(nx["cast"]))),
                          purpose=s["purpose"] if not nx["purpose"] else (s["purpose"] + "；" + nx["purpose"]) if s["purpose"] else nx["purpose"])
            shots[i + 1] = merged
            i += 1
            continue
        if (s["seconds"] < MIN_SEC and out and out[-1]["place"] == s["place"] and s["seconds"] + out[-1]["seconds"] <= MAX_SEC
                and (set(out[-1]["cast"]) == set(s["cast"]) or s["seconds"] <= 6)):
            pv = out[-1]                                                        # P454⑩：没有可并的下一段 → 并进上一段
            off = pv["seconds"]
            pv["blocks"] = pv["blocks"] + [[b[0] + off, b[1] + off, b[2], list(b[3])] for b in s["blocks"]]
            pv["seconds"] = pv["seconds"] + s["seconds"]
            pv["cast"] = list(dict.fromkeys(list(pv["cast"]) + list(s["cast"])))
            pv["end_state"] = dict(pv.get("end_state") or {}, **(s.get("end_state") or {}))
            i += 1
            continue
        out.append(s)
        i += 1
    shots = out
    for s in shots:                                                             # P455⑫：并不进去的短段拉到 8 秒
        if 0 < s["seconds"] < MIN_SEC and s["blocks"]:
            _sc = MIN_SEC / s["seconds"]
            t0 = 0.0
            for b in s["blocks"]:
                d = max(2.0, round((b[1] - b[0]) * _sc))
                b[0], b[1] = t0, t0 + d
                t0 += d
            s["seconds"] = t0
    # ④ 台词编号：只留这一件事的、每个一次按顺序；漏的补到说话人所在的最后一块（没有就最后一段最后一块）
    seen = set()
    for s in shots:
        for b in s["blocks"]:
            keep = []
            for d in b[3]:
                if d in expect_dids and d not in seen:
                    keep.append(d)
                    seen.add(d)
            b[3] = keep
    flat_blocks = [b for s in shots for b in s["blocks"]]
    if not flat_blocks:
        return shots, dict(prev_state or {})                                   # P448①：一块都没有就别补台词了（min([]) 曾崩掉整话）
    for d in sorted(expect_dids):
        if d in seen:
            continue
        spk = _os_.canon(D[d - 1][0], aliases) or D[d - 1][0]
        # P437：只能落在前一个编号所在块 和 后一个编号所在块 之间（含），这一段里优先说话人出现的块
        lo = max([i for i, b in enumerate(flat_blocks) if any(x < d for x in b[3])] or [0])
        hi = min([i for i, b in enumerate(flat_blocks) if any(x > d for x in b[3])] or [len(flat_blocks) - 1])
        cand = flat_blocks[lo:hi + 1] or flat_blocks[-1:]
        _with = [b for b in cand if spk in b[2] or _os_.short_name(spk) in b[2]]
        _pool = _with or cand
        tgt = next((b for b in _pool if len(b[3]) < 2), None) or min(_pool, key=lambda b: len(b[3]))      # P440：说话人在场、台词不到两句的最早那块
        tgt[3].append(d)
        seen.add(d)
    # P455④：一段硬上限 4 句台词（给导演的规矩是 3，程序兜底 4）——超了按块拆段（块里超的，多的挪到下一块）
    _out = []
    for s in shots:
        while sum(len(b[3]) for b in s["blocks"]) > MAX_DIDS and len(s["blocks"]) >= 1:
            acc, k = 0, 0
            for i, b in enumerate(s["blocks"]):
                if acc + len(b[3]) > MAX_DIDS:
                    if i == 0:
                        extra = b[3][MAX_DIDS - acc:]
                        b[3] = b[3][:MAX_DIDS - acc]
                        if len(s["blocks"]) == 1:
                            mid = round((b[0] + b[1]) / 2)
                            s["blocks"] = [[b[0], mid, b[2], b[3]], [mid, b[1], b[2], extra]]
                        else:
                            s["blocks"][1][3] = extra + s["blocks"][1][3]
                        k = 1
                    else:
                        k = i
                    break
                acc += len(b[3])
            if k <= 0 or k >= len(s["blocks"]):
                break
            first = dict(s, blocks=[list(b) for b in s["blocks"][:k]], end_state={})
            first["seconds"] = sum(b[1] - b[0] for b in first["blocks"])
            rest = [list(b) for b in s["blocks"][k:]]
            off = rest[0][0]
            for b in rest:
                b[0], b[1] = b[0] - off, b[1] - off
            for part in (first,):
                if part["seconds"] < MIN_SEC and part["blocks"]:
                    _sc = MIN_SEC / max(1.0, part["seconds"])
                    t0 = 0.0
                    for b in part["blocks"]:
                        d = round((b[1] - b[0]) * _sc)
                        b[0], b[1] = t0, t0 + d
                        t0 += d
                    part["seconds"] = t0
            _out.append(first)
            s = dict(s, blocks=rest, seconds=sum(b[1] - b[0] for b in rest), purpose=s["purpose"] + "（续）")
        _out.append(s)
    shots = _out
    # 台词按编号顺序：块里编号排序；跨块乱序的（后块编号小于前块）换到前块
    for s in shots:
        flat = [d for b in s["blocks"] for d in b[3]]
        if flat != sorted(flat):
            ds = sorted(flat)
            n = len(s["blocks"])
            for b in s["blocks"]:
                b[3] = []
            for j, d in enumerate(ds):
                s["blocks"][min(n - 1, j * n // max(1, len(ds)))][3].append(d)
    # ⑤ 在场：只认卡名；空就从块文本里找；再空＝空镜
    for s in shots:
        _spk = [(_os_.canon(D[d - 1][0], aliases) or D[d - 1][0]) for b in s["blocks"] for d in b[3] if 1 <= d <= len(D)]
        s["cast"] = list(dict.fromkeys(s["cast"] + [x for x in _spk if x in char_names]))      # P448②：说台词的人必须在画面里
        if not s["cast"] or re.search(r"其余|其他|三人|众人|大家|所有人|全员|女孩们|她们|他们", str(s.get("_cast_raw") or "")):
            s["cast"] = list(dict.fromkeys(s["cast"] + _canon_names(" ".join(b[2] for b in s["blocks"]) + " " + str(s.get("_cast_raw") or ""), aliases)))
            if re.search(r"其余|其他|众人|大家|所有人|全员", str(s.get("_cast_raw") or "")):
                s["cast"] = list(char_names)                                    # P436：「其余三人」→ 全员
        s["cast"] = [n for n in s["cast"] if n in char_names]
    # ⑦ 结束状态：每个在场的人一行；缺的沿用上一段（或默认）；姿势没写按最后一块推
    state = dict(prev_state or {})
    prev_place_now = prev_place
    for s in shots:
        es = {}
        last_txt = s["blocks"][-1][2] if s["blocks"] else ""
        cam_side = {}
        for nm in s["cast"]:
            mc = re.search(re.escape(_os_.short_name(nm)) + r"[^，。；]{0,6}?(?:画面)?(左|右)", str(s.get("camera") or ""))   # P436：「张明在左」也算
            if mc:
                cam_side[nm] = "画面%s侧" % mc.group(1)                        # 机位行写了「张明在画面左侧」→ 默认左右按它
        for nm in s["cast"]:
            e = dict(s["end_state"].get(nm) or {})
            base = state.get(nm) or {}
            for k, dflt in (("pos", "画面中央"), ("facing", "面朝对方"), ("hands", "无"), ("posture", "站立"), ("side", cam_side.get(nm, "画面中央"))):
                if not e.get(k):
                    e[k] = base.get(k) or dflt
            if not (s["end_state"].get(nm) or {}).get("posture"):
                mp = re.search(re.escape(_os_.short_name(nm)) + r"[^。；，]{0,14}?(躺|趴|坐|跪|蹲|站起|起身|站)", last_txt)
                if mp:
                    e["posture"] = {"站起": "站立", "起身": "站立", "站": "站立", "坐": "坐着", "蹲": "蹲着", "跪": "跪着", "躺": "躺着", "趴": "趴着"}[mp.group(1)]
            e["side"] = norm_side(e.get("side"))
            # P439：同一地点连着的段，左右沿用上一段（骨架里这个人没写走位/换边就不许换）
            _pv = (state.get(nm) or {}).get("side")
            if _pv and _pv != e["side"] and s["place"] == (prev_place_now or s["place"]) and not re.search(
                    re.escape(_os_.short_name(nm)) + r"[^。；]{0,12}(走到|走向|换到|绕到|退到|挪到|移到|跑到|站到|坐到|转到|来到)", " ".join(b[2] for b in s["blocks"])):
                e["side"] = _pv
                _w = "左" if "左" in _pv else "右"
                s["camera"] = re.sub(r"(" + re.escape(_os_.short_name(nm)) + r"(?:在|位于|处于)(?:画面)?)(左|右)", lambda mm: mm.group(1) + _w, str(s.get("camera") or ""))   # 机位行跟着改
                e["pos"] = re.sub(r"(左|右)(侧|边|方)?$", lambda mm: _w + (mm.group(2) or ""), str(e.get("pos") or "")) if re.search(r"(左|右)(侧|边|方)?$", str(e.get("pos") or "")) else e.get("pos")
            es[nm] = e
        s["end_state"] = es
        state.update(es)
        prev_place_now = s["place"]
        s["shot"] = shot_of(s["camera"])
        s["says"] = [(_os_.canon(D[d - 1][0], aliases) or D[d - 1][0], D[d - 1][1]) for b in s["blocks"] for d in b[3]]
    return shots, state


def _match_place(p, scene_names):
    p = str(p or "").strip()
    if not p or not scene_names:
        return ""
    if p in scene_names:
        return p
    for n in sorted(scene_names, key=len, reverse=True):
        if n in p or p in n:
            return n
    cls = _os_.place_class(p)
    same = [n for n in scene_names if _os_.place_class(n) == cls] if cls else []
    return same[0] if len(same) == 1 else ""


# ─────────── 输出：切片 ───────────

def to_slices(shots, prev_place=""):
    out, last = [], prev_place
    for s in shots:
        cast_txt = "、".join(_os_.short_name(n) for n in s["cast"])
        body = "；".join(b[2].rstrip("。") for b in s["blocks"])
        text = "── %s ──\n%s。" % (s["place"], body)
        for nm, q in s["says"]:
            text += "\n%s：%s" % (_os_.short_name(nm), q)
        out.append({"text": text, "seconds": float(s["seconds"]), "scene_hint": s["place"], "beat": "", "beat_index": -1,
                    "shot": s["shot"], "establish": bool(last != s["place"]), "cut": True, "who": cast_txt, "pace": "导演",
                    "director": {"purpose": s["purpose"], "camera": s["camera"], "cast": list(s["cast"]),
                                 "blocks": [[b[0], b[1], b[2], list(b[3])] for b in s["blocks"]],
                                 "end_state": s["end_state"], "says": [list(x) for x in s["says"]]}})
        last = s["place"]
    return out


def state_text(state, names=None):
    lines = []
    for nm, e in (state or {}).items():
        if names and nm not in names:
            continue
        lines.append("%s：%s，%s，%s，%s，%s" % (_os_.short_name(nm), e.get("pos", ""), e.get("facing", ""), e.get("hands", ""), e.get("posture", ""), e.get("side", "")))
    return "；".join(lines)


def _bigrams(t):
    t = re.sub(r"[^一-龥]", "", str(t or ""))
    return {t[i:i + 2] for i in range(len(t) - 1)}


_ACT_V = re.compile(r"做|捕|搭|砍|捡|晾|烤|递|抓|扶|漂|跑|走|推|拉|找|采|煮|背|藏|敷|收拾|跟|追|还|打电话|送|抱|摸|碰|躲|开|指|拽|跳|爬|翻|挖|烧|洗|切|扛|叉|钓|织|缝|绑|架|盖|铺|爬上|跃|穿过|争执|停下|亮起|震动|落下|浮")
_STOP_N = re.compile(r"^(经验|很多|一个|一起|一下|一些|这个|那个|他们|她们|自己|岛上|开始|慢慢|主动|合作|已经|然后|终于)$")


def missing_points(point, shots):
    """P441b：口述要点里「有动作动词 + 两字以上名词」的分句，名词没在这件事的块文本里出现、两字连词也对不上 → 漏了。返回漏的分句。"""
    txt = " ".join(b[2] for s in shots for b in s.get("blocks") or [])
    tb = _bigrams(txt)
    out = []
    for cl in re.split(r"[，,；;。！？、]", str(point or "")):
        cl = cl.strip()
        if len(re.sub(r"[^一-龥]", "", cl)) < 4 or not _ACT_V.search(cl):
            continue
        nouns = [w for w in re.findall(r"[一-龥]{2,3}", cl) if not _STOP_N.match(w) and not _ACT_V.fullmatch(w)]
        if any(w in txt for w in nouns) or len(_bigrams(cl) & tb) >= 2:
            continue
        out.append(cl)
    return out


def _fallback_shot(place, cast, clauses, char_names, aliases):
    """要点补不上时程序自己写一段骨架：三块=要点分句。"""
    cl = [c for c in clauses if c][:3] or ["按要点做事"]
    n = len(cl)
    secs = 12.0
    blocks = []
    t = 0.0
    for i, c in enumerate(cl):
        dur = round(secs / n)
        blocks.append([t, t + dur, c, []])
        t += dur
    blocks[-1][1] = secs
    who = _canon_names(" ".join(cl), aliases) or list(cast)
    return {"place": place, "cast": who, "seconds": secs, "camera": "中景，平视，固定", "purpose": cl[0], "blocks": blocks, "end_state": {}}


def shot_list(pics, scenes, chars, q, debug_path="", on_step=None, event_points=None, event_map=None, prev_ledger="", pov="", owner=""):
    """剧本 → 导演分段切片。scenes: 卡列表或名字；chars: 卡列表或名字。"""
    scene_items = _os_._scene_items(scenes)
    scene_names = [n for n, _ in scene_items]
    char_items = _os_._char_items(chars)
    char_names = [n for n, _, _ in char_items]
    aliases = _os_.card_aliases(chars)                                        # P454⑨：人物类型词也认（精灵队长 → 瑟琳娜）
    script, D = _os_.numbered_script(pics)
    events = split_events(script, n_events=len(event_points or []), event_map=event_map)
    events, event_points = split_long_events(events, event_points)            # P458：长事拆份
    ins = instruction(scene_names, char_items, pov=pov, owner=owner)        # P461：视点规矩
    all_shots, state, prev_place, log = [], {}, "", []
    char_sex = {n: sx for n, sx, _a in char_items}
    char_type = {str(c.get("name") or ""): str(c.get("char_type") or c.get("identity") or "") for c in (chars or []) if isinstance(c, dict)}
    prev_cast = []
    for i, ev in enumerate(events):
        dids = sorted({int(x) for x in re.findall(r"【D(\d+)】", ev)})
        dl = "\n".join("D%d %s：%s" % (d, D[d - 1][0], D[d - 1][1]) for d in dids) or "无"
        _kmin = max(2, -(-len(dids) // 3))
        _tailrule = ("这是最后一件事：最后一段可以用拉远或定格收尾。" if i == len(events) - 1 else "这不是最后一件事：不写拉远、定格、日出这类收尾镜头。")
        _cnt = ("【这件事有 %d 句台词 → 至少分 %d 段，每段最多 3 句台词】%s" % (len(dids), _kmin, _tailrule)) if dids else ("【这件事没有台词】" + _tailrule)
        _pt = (event_points[i] if event_points and i < len(event_points) else "")
        user = ("【这一件事的剧本】（第 %d 件，共 %d 件）\n%s\n%s%s\n【上一段结束时】%s\n\n【地点】%s\n【人物】%s\n【台词编号】\n%s"
                % (i + 1, len(events), ev, ("\n【这件事的口述要点（要点里的每个动作至少占一个时间块）】" + str(_pt).strip() + "\n") if _pt else "", _cnt + "\n",
                   state_text(state) or (("（本话开头）" + str(prev_ledger).strip()) if (i == 0 and str(prev_ledger or "").strip()) else "（全片开头）"),
                   "；".join("%s（%s）" % (n, d) if d else n for n, d in scene_items) or "、".join(scene_names),
                   "、".join(char_names), dl))
        if on_step:
            on_step("导演分段：第 %d/%d 件" % (i + 1, len(events)))
        raw = q(ins, user, mt=2200, temperature=0.35)
        shots = parse(raw, scene_names, char_names, D, aliases)
        if not shots:
            raw = q(ins, user + "\n\n（严格按格式：每段以「【段】地点=」开头，每块「0-4秒=」开头）", mt=2200, temperature=0.5)
            shots = parse(raw, scene_names, char_names, D, aliases)
        # P441：口述要点没拍到 → 带着漏的清单让导演把这件事重出一次；仍漏就程序补段
        _miss = missing_points(_pt, shots) if _pt else []
        if _miss:
            raw2 = q(ins, user + "\n\n【上一次分段漏了这些要点，这次每个要点至少占一个时间块，其余照旧】\n" + "\n".join("· " + x for x in _miss), mt=2200, temperature=0.4)
            shots2 = parse(raw2, scene_names, char_names, D, aliases)
            if shots2 and len(missing_points(_pt, shots2)) <= len(_miss):
                shots, raw = shots2, raw + "\n\n===== 补要点重出 =====\n" + raw2          # P441b：不比原来差就用重出的
            _miss = missing_points(_pt, shots) if shots else _miss
        shots, state = fix_shots(shots, D, set(dids), scene_names, char_names, state, prev_place, aliases, ev_text=ev, char_sex=char_sex, prev_cast=prev_cast, char_type=char_type)
        shots = apply_pov(shots, pov, owner)                                 # P461：按视点钉死在场和机位行
        log.append({"event": i + 1, "raw": raw, "shots": shots, "missing": _miss, "cut_cast": list(getattr(fix_shots, "last_cut", []) or [])})
        if shots:
            prev_place = shots[-1]["place"]
            prev_cast = list(shots[-1]["cast"])
        all_shots.extend(shots)
    if debug_path:
        try:
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(ins + "\n\n")
                for l in log:
                    f.write("===== 第 %d 件 模型输出 =====\n%s\n\n===== 修后%s =====\n%s\n\n" % (l["event"], l["raw"], ("（剪掉不在场的：%s）" % "、".join(l["cut_cast"])) if l.get("cut_cast") else "", json.dumps(l["shots"], ensure_ascii=False, indent=1)))
        except Exception:
            pass
    shot_list.last_log = log
    return to_slices(all_shots)


# ─────────── 提示词那步：骨架 → 成品（程序部分） ───────────

def skeleton_text(d, idx):
    """给写手看的【本段骨架】。idx: 名→Subject 编号。"""
    lines = ["【机位】" + str(d.get("camera") or "")]
    if d.get("purpose"):
        lines.append("【目的】" + str(d["purpose"]))
    for a, b, txt, dids in d.get("blocks") or []:
        _sp = [str(x[0]) for x in (d.get("says") or [])]
        _all = [x for _a, _b, _t, _ds in (d.get("blocks") or []) for x in _ds]
        _who = [_os_.short_name(_sp[_all.index(x)]) if x in _all and _all.index(x) < len(_sp) else "" for x in dids]
        tag = ("　← 这一块末尾%s各说一句，先写「X嘴唇开合。」" % "、".join(w for w in _who if w)) if dids and any(_who) else ""
        lines.append("%d—%d秒：%s%s" % (int(a), int(b), txt, tag))
    return "\n".join(lines)


def render_tail(d, idx):
    ppl = []
    for nm in d.get("cast") or []:
        e = (d.get("end_state") or {}).get(nm) or {}
        ppl.append("· %s：%s｜%s｜%s｜%s｜%s" % (_os_.short_name(nm), e.get("pos", "画面中央"), e.get("facing", "面朝对方"), e.get("hands", "无"), e.get("posture", "站立"), e.get("side", "画面中央")))
    bl = d.get("blocks") or []
    ongoing = bl[-1][2].rstrip("。") if bl else ""
    done = "；".join(b[2].rstrip("。") for b in bl[:-1]) or "无"
    return "这一段结束时：\n· 景别：%s\n%s\n· 正在进行：%s\n· 本段已完成：%s" % (shot_of(d.get("camera")), "\n".join(ppl), ongoing, done)


_ACT_RE = re.compile(r"砸|攥|抓|抹|扶|拽|站起|坐下|走|跑|蹲|递|接|看|望|转|伸|拍|扯|踩|翻|爬|捡|削|叉|指|点头|开口|抬|低头|退|挡|靠|摸|搭|端|拨|收|叠|系|提|迈|环顾|抱|打|掀|滑|涌|折|散|趴|漂|起伏|推|忙|串|拉|摇|涉水|弯腰|起身|探|举|挥|放下|松开|握|拨开|扑|跳|蹬|咬|嚼|吃|喝|睁|闭|擦|拧|敷|盖|搬|守|吹|燃|亮|落|飞|走进|走出|进|出")


def final_tidy(body):
    """P439：所有守卫跑完后——同一人的「嘴唇开合」在一块里只留一个。"""
    out = []
    for ln in str(body or "").split("\n"):
        if "says:<d>" in ln or ln.strip().startswith(("【", "·", "这一段结束时")):
            out.append(ln)
            continue
        seen = set()

        def _one(m):
            k = m.group(1)
            if k in seen:
                return ""
            seen.add(k)
            return m.group(0)
        ln = re.sub(r"([^。；，\s]{1,6})嘴唇开合。", _one, ln)
        out.append(ln)
    return "\n".join(out)


def env_sentence(scene_space):
    """场景卡空间描述 → 一句环境（优先「中景」那句，其次第一句），≤40 字。"""
    t = str(scene_space or "").strip()
    if not t:
        return ""
    parts = [p.strip("，。； ") for p in re.split(r"[，。；]", t) if p.strip("，。； ")]
    # P446b：先取「远景」那句（背景是什么），再取「中景」——只给地面和蕨类，模型会把背景画成一面墙
    pick = next((p for p in parts if p.startswith(("远景", "远处", "视野尽头", "天际"))), "") or next((p for p in parts if p.startswith(("中景", "中景处", "中景是"))), "") or next((p for p in parts if not p.startswith(("室外", "室内"))), "") or (parts[0] if parts else "")
    pick = re.sub(r"^(中景处?(?:是|有|可见)?|远景处?(?:是|有|可见)?|远处(?:是|有|可见)?|视野尽头(?:是|有)?|前景处?(?:是|有|可见)?)", "", pick).strip("，。； ")
    return pick[:40]


def ensure_env(block_text, scene_space):
    """P446：这一块没有环境词（和场景卡空间描述没有两字连词重合）→ 块末补一句「周围是…」。"""
    env = env_sentence(scene_space)
    if not env:
        return block_text
    tb = _bigrams(block_text)
    sb = _bigrams(str(scene_space or ""))
    if len(tb & sb) >= 2:
        return block_text
    return block_text.rstrip("。；，") + "。人物身后一直到画面深处都是" + env + "。"


# P454：块开头本来就是别的主语（房门被推开、几名女村民鱼贯而入）→ 不再往前加卡名（曾出「林恩房门被推开」）
_NO_SUBJ_HEAD = re.compile(r"^(镜头|画面|前景|背景|远处|近处|房门|木门|门|窗|几名|几个|一名|一个|一群|数名|两名|三名|四名|众|村民|路人|精灵|女子|女人|男人|士兵|守卫|孩子|老人|人群|人影|身影|脚步声|声音|"
                           r"海|风|雨|火|浪|阳光|晨光|夜|沙滩|帐篷|四人|三人|两人|众人|她们|他们|长枪|枪|绳|网|一根|一张|一道|一阵|一片|一只|一辆|车|马|狗|猫|鸟)")


def assemble(body, d, chars, say_lines_by_d, scene_space=""):
    """模型写的正文 → 按骨架钉死：【机位】行 = 导演的；块头按骨架（多写的块并进最后一块、少写的用骨架句补）；
    台词行放进骨架标的那一块末尾（前面补口型）；结尾状态程序写。"""
    from .authoring import _tl_blocks, _TL_BLOCK
    blocks, head, tail = _tl_blocks(str(body or ""))
    sk = d.get("blocks") or []
    texts = []
    for _, _, t in blocks:
        t = "\n".join(l for l in t.splitlines() if "says:<d>" not in l and not l.strip().startswith("这一段结束时"))
        t = _TL_BLOCK.sub("", t, count=1).strip()
        texts.append(t)
    if not texts:
        texts = [t for _, _, t, _ in sk]
    if len(texts) > len(sk):
        texts = texts[:len(sk) - 1] + ["".join(texts[len(sk) - 1:])]
    while len(texts) < len(sk):
        texts.append(sk[len(texts)][2])
    out = ["【机位】" + str(d.get("camera") or "").strip("。") + "。"]
    names = [str(c.get("name") if isinstance(c, dict) else c) for c in (chars or [])] or list(d.get("cast") or [])
    shorts = [_os_.short_name(n) for n in names]
    try:
        _alias_marks = [a for a, full in _os_.card_aliases([c for c in (chars or []) if isinstance(c, dict)]).items() if a not in names and len(a) >= 2]
    except Exception:
        _alias_marks = []
    for (a, b, sktxt, dids), t in zip(sk, texts):
        t = re.sub(r"[（(【\[]?\s*D\s*\d+\s*[）)】\]]?[^。；，]{0,12}(嘴唇开合)?[。；，]?", "", t).strip()   # P437：写手把 D6 当人写进画面
        t = re.sub(r"[（(][^）)]*(假设|S\d|观察者|即观众|摄像机)[^）)]*[）)]", "", t)                        # P438：写手的推理括号
        t = re.sub(r"【[^】\n]{2,10}】", "", t)                                                              # P439c：写手加的【站位与朝向】小标题
        t = re.sub(r"<d>.*?</d>[。，；]?|<Subject\s*\d+>\s*\(S\d+\)\s*says:", "", t)                      # P440：残留的台词碎片
        t = re.sub(r"[；;，,]\s*无\s*[。；]?\s*$", "。", t)                                                # P438：块末抄的「；无」
        t = re.sub(r"(?m)[；;，,]\s*无\s*$", "。", t)
        if not t:
            t = sktxt
        t = ensure_env(t, scene_space)                                                              # P446：每块带环境
        if a == 0 and not _ACT_RE.search(t[:30]) and _ACT_RE.search(sktxt):
            _k8 = _hz(sktxt)[:8]
            _sents = [x for x in re.split(r"(?<=[。；！？])", t) if x.strip()]
            _hit = next((i for i, x in enumerate(_sents) if _k8 and _k8 in _hz(x)), -1)
            if _hit > 0:
                t = "".join([_sents[_hit]] + _sents[:_hit] + _sents[_hit + 1:])                     # P444：骨架句已在块里 → 挪到最前，不重复
            elif _hit < 0:
                t = sktxt.rstrip("。") + "。" + t                                                    # P439：0 秒先写动作，站位描述往后放
        for nm in (d.get("cast") or []):
            _sd = ((d.get("end_state") or {}).get(nm) or {}).get("side", "")
            _want = "左" if "左" in _sd else ("右" if "右" in _sd else "")
            if _want and not re.search(re.escape(_os_.short_name(nm)) + r"[^。；]{0,12}(走到|走向|换到|绕到|退到|挪到|移到|跑到|站到|坐到|转到|来到)", sktxt):
                t = re.sub(r"(" + re.escape(_os_.short_name(nm)) + r"(?:（S\d）)?[^。；，]{0,10}?(?:位于|站在|坐在|蹲在|在|处于)画面)(左|右)(侧|方|边)",
                           lambda mm: mm.group(1) + _want + mm.group(3), t)                          # P439：正文左右按账本
        for nm in (d.get("cast") or []):
            _po = ((d.get("end_state") or {}).get(nm) or {}).get("posture", "")
            _sn = _os_.short_name(nm)
            if _po in ("趴着", "躺着", "坐着", "蹲着", "跪着") and not re.search(re.escape(_sn) + r"[^。；]{0,12}(站起|起身|站直|站定)", sktxt + " " + " ".join(x[2] for x in sk)):
                _w = {"趴着": "趴", "躺着": "躺", "坐着": "坐", "蹲着": "蹲", "跪着": "跪"}[_po]
                t = re.sub(r"(" + re.escape(_sn) + r"(?:（S\d）)?[^。；]{0,24}?)站(在|立|着|直|定)", lambda mm: mm.group(1) + _w + ("在" if mm.group(2) == "在" else "着"), t)   # P440：姿势按账本；P454：中间允许隔一两个逗号句
        _head12 = t[:14]
        _marks = shorts + [n[-2:] for n in shorts if len(n) >= 3] + _alias_marks                          # P442b：「老者」也算「青云门老者」；P454：卡的类型词（小个子精灵）也算
        if shorts and not any(n and n in _head12 for n in _marks) and not _NO_SUBJ_HEAD.match(t):
            _subj = next((n for n in shorts if n and n in sktxt), shorts[0] if len(shorts) == 1 else "")
            if _subj:
                t = _subj + ("的" if re.match(r"^(深|浅|黑|白|亚麻|湿|短|长|头发|发丝|衣|裙|T恤|外套|靴|鞋|手|脸|眼|嘴|肩|背|腿|脚|身)", t) else "") + t   # P438：块开头没主语
        lines = ["%d—%d秒：%s" % (int(a), int(b), t)]
        for x in dids:
            sl = say_lines_by_d.get(x)
            if sl:
                spk = sl.split("说：", 1)[0]
                if not re.search(re.escape(spk) + r"[^。]{0,6}嘴唇开合", lines[0]):
                    lines[0] = lines[0].rstrip("。") + "。" + spk + "嘴唇开合。"
                lines.append(sl)
        lines[0] = re.sub(r"((?:[^。；，\n]{1,6})嘴唇开合。)(?:\s*\1)+", r"\1", lines[0])          # P437：同一个人的口型只留一个
        out.append("\n".join(lines))
    out.append(render_tail(d, None))
    return "\n\n".join(out)
