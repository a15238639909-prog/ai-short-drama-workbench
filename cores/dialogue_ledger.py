# -*- coding: utf-8 -*-
"""台词清单（P310，用户 2026-09-11 定）：正文里每一句引号文字的**唯一来源**，后面几层（补台词、拍摄清单、检查、提示词）都从这里拿，不再各自猜。

每一条：位置、说话人（有依据才填）、实际文字、是不是当前说出口的话。
  kind = spoken   当前实际说出的话 → 进对白
         unspoken 没说出口 / 心里想的（「那句没说出口的“对不起”」「心里默念“…”」）→ 不进对白，保留原本含义
         recalled 回忆里的话（「当年他留了句“…”」）→ 不自动补进当前对白，进了就提示
         signage  招牌/登记簿/标签上的字 → 不是台词
         onomat   拟声/字形（“哐当”一声、皱成“川”字）→ 不是台词
说话人只认**明写的依据**：引号前后紧挨着的「名字＋说/问/道…」或「名字：」；用代词、没写的 → 说话人空、confidence=存疑，
不借用上一句的人。同一句话正文说了几次就记几次（count），剧本里只能出现这么多次。

零模型，确定性。
"""
import re

KIND_SPOKEN, KIND_UNSPOKEN, KIND_RECALLED, KIND_SIGNAGE, KIND_ONOMAT = "spoken", "unspoken", "recalled", "signage", "onomat"
KIND_TERM = "term"                                        # P316：引称——引号里是个短名词（的“商品”/所谓“朋友”/叫作“疯子”），不是话
_TERM_BEFORE = re.compile(r"(的|所谓|叫做|叫作|唤作|称为|被称为|称作|名为|俗称|号称|自称|视为|当作|当成|看作|算是|成了|成为|变成|是个|是种|一种|这种|那种|种|(?:叫|喊|称|骂)(?:他|她|它|我|你|人|们|自己)?)\s*$")
_TERM_WORDS = r"的|二字|两个字|三个字|四个字|这两个字|这三个字|这几个字|一词|这个词|这种词|这类词|这词|字眼|这种说法|这个说法|这种叫法|这个叫法|这称呼|之类的|什么的"
_TERM_AFTER = re.compile(r"^\s*(" + _TERM_WORDS + r"|这样的|那样的|们|，|。|；|、)")
_TERM_AFTER_WORD = re.compile(r"^\s*(" + _TERM_WORDS + r")")          # P334：“新地图”这种词 / “朋友”之类的 → 引称
# P334b：「把“对不起”这三个字说出口」「“别怕”这两个字，他说得很轻」——字数词后面紧跟说/念/出口，还是台词
_SAY_AFTER_TERM = re.compile(r"^\s*(?:" + _TERM_WORDS + r")[^。！？；\n]{0,8}?(?:说|道|念|喊|吐|吼|嚷|唤|开口|出口|出声|脱口)")
_SAY_BEFORE_TERM = re.compile(r"(把|将|念出|挤出|说出|喊出|叫出|吐出|憋出)\s*$")
_TERM_MAXLEN = 6

_QUOTE_RE = re.compile(r"[「“]([^」”\n]{1,200})[」”]")
_SAY_V = (r"(?:说道|问道|答道|喊道|叹道|笑道|应道|接话道|开口道|低声道|轻声道|扬声道|回答道|反问道|补充道|"
          r"说|问|喊|道|答|叹|笑|应|接话|接过话头|开口|低声|轻声|扬声|回答|反问|补充|嘟囔|嘀咕|唤|轻呼|惊呼|低语|吐出|重复|念|招呼|嚷|吼|催|劝|解释|感叹|嘟哝|回应|回道|应声)")
_UNSPOKEN = re.compile(r"(没说出口|没有说出口|没能说出|说不出口|咽了回去|咽回|心里|心想|暗想|默念|腹诽|在心底|心底|脑子里|脑海里|想说又|没说出来|藏在心里|没敢说|没开口|欲言又止|无声地|默默地想|想道|暗道)")
_RECALL = re.compile(r"(当年|那年|那时候|那时|那天|那晚|那夜|十年前|三十年前|几年前|多年前|记得|回忆|想起|曾经|当初|那次|临走前|留了句|留言|留过|说过|提过|小时候)")
_ONOMAT_TAIL = "字声样般似的响"


def _n(s):
    return re.sub(r"[^\w一-龥]", "", str(s or ""))


_ONO_CHARS = set("当咚哐啪嗒嘀滴叮铃噔砰轰嗡呜哗嗖唰咔嚓嘭吱嘎咕嘟哒锵咣噗嘶咻嗤啾喀嗵咯哔滋唧")
_ONO_CTX = re.compile(r"第[一二三四五六七八九十两\d]+声|钟鸣|钟声|声响|响起|响了|作响|声音")


def _is_onomat(q, src, end):
    nq = re.sub(r"[^一-龥]", "", str(q or ""))
    if not nq or len(nq) > 4:
        return False
    tail = str(src or "")[end:end + 3]
    if tail and tail[0] in _ONOMAT_TAIL:
        return True
    if re.match(r"^\s*[的一了]{0,2}\s*[声响]", tail):
        return True
    q = str(q or "").strip()
    # 纯拟声即使带句号也不是台词（正文常写“咔嚓。”作为独立一句）。
    # 先判字形，再看句末标点，避免把它分给最近的人物开口念。
    if set(nq) <= _ONO_CHARS:
        return True
    if q[-1:] in "！!？?。.":
        return False
    # P337：“当——当——”“咚咚咚”——全是拟声字；或字很少、后文说的是钟鸣/第几声
    return len(set(nq)) <= 2 and bool(_ONO_CTX.search(str(src or "")[end:end + 14]))


def _is_term(q, src, start, end):
    """引称：≤6 个字、里面没有标点、前面紧挨 的/所谓/叫作/称为… 或后面紧挨 的/二字/一词…，并且前面不是冒号（冒号后面是台词）。"""
    t = str(q or "").strip()
    if not t or len(t) > _TERM_MAXLEN or re.search(r"[，。！？；：…—,.!?]", t):
        return False
    before = str(src or "")[max(0, start - 8):start]
    after = str(src or "")[end:end + 5]
    if re.search(r"[：:]\s*$", before):
        return False
    if _TERM_BEFORE.search(before):
        return True
    # 后面紧挨「的/二字/一词」且前面不是说话动词
    if _TERM_AFTER.match(after) and not re.search(_SAY_V + r"\s*$", before):
        if _SAY_AFTER_TERM.match(str(src or "")[end:end + 16]) or _SAY_BEFORE_TERM.search(before):
            return False
        return bool(_TERM_AFTER_WORD.match(after))
    return False


def _is_signage(src, start, end):
    try:
        from .shotlist import is_signage_quote
        return is_signage_quote(src, start, end)
    except Exception:
        before = src[max(0, start - 6):start]
        return bool(re.search(r"(刻|写|印|标|绣|画|题|贴)(着|有|了|上|成)?[：:]?\s*$", before))


def _name_pat(names):
    names = sorted({str(x).strip() for x in (names or []) if str(x).strip()}, key=len, reverse=True)
    if not names:
        return None
    return "(" + "|".join(re.escape(x) for x in names) + ")"


def _find_speaker(src, start, end, names):
    """引号前后紧挨着的明写依据 → (名字, 依据句)；没有 → ("", "")。
    前：…名字[不超过 24 字]说/问/道[：，]$ ；名字：$ ；后：」名字[不超过 12 字]说/问/道。
    没有人物表时退回「2～4 个汉字＋说/问/道」，但代词（他/她/两人…）永远不算。"""
    before = src[max(0, start - 60):start]
    after = src[end:end + 60]
    # 只看本句范围：前面截到上一个句末，后面截到下一个句末
    before = re.split(r"[。！？\n]", before)[-1]
    after = re.split(r"[。！？\n]", after)[0]
    np_ = _name_pat(names)
    cands = []
    if np_:
        m = re.search(np_ + r"[^，。！？“”「」]{0,24}?" + _SAY_V + r"[：:，,]?\s*$", before)
        if m:
            cands.append((m.group(1), before[m.start():]))
        m = re.search(np_ + r"\s*[：:]\s*$", before)
        if m:
            cands.append((m.group(1), before[m.start():]))
        m = re.match(r"^\s*[，,]?\s*" + np_ + r"[^，。！？“”「」]{0,12}?" + _SAY_V, after)
        if m:
            cands.append((m.group(1), after[:m.end()]))
    else:
        m = re.search(r"([一-龥]{2,4})[^，。！？“”「」]{0,10}?" + _SAY_V + r"[：:，,]?\s*$", before)
        if m and not re.match(r"^(他们|她们|两人|众人|三人|大家|自己)", m.group(1)):
            cands.append((m.group(1), before[m.start():]))
        m = re.match(r"^\s*[，,]?\s*([一-龥]{2,4})[^，。！？“”「」]{0,10}?" + _SAY_V, after)
        if m and not re.match(r"^(他们|她们|两人|众人|三人|大家|自己)", m.group(1)):
            cands.append((m.group(1), after[:m.end()]))
    if np_ and not cands:
        # 前：上一句引号收尾后紧接着名字、中间只有动作、以逗号或冒号结尾（「“嗯。”林川应道，顺手把手机扣在腿上，“…”」
        #     「“教书好。”林川点点头，…，“稳定…”」「周屿低头理了理带子，笑了笑：“…”」）→ 这句还是那人在说
        # 上一句引号收尾之后的那截才是本句的归属依据（「“这书签……”林川开口，声音低了些，“还是老样子？”」）
        if "”" in before or "」" in before:
            _cut = max(before.rfind("”"), before.rfind("」"))
            b2, lead = before[_cut + 1:].lstrip(" "), before[:_cut + 1]
        else:
            b2, lead = before.lstrip(" "), ""
        mb = re.match(r"^\s*" + np_, b2)
        if mb and not re.search(np_, b2[mb.end():]) and "“" not in b2 and "「" not in b2:
            if re.search(r"[：:]\s*$", b2) or (lead.strip() and re.search(r"[，,]\s*$", b2)):
                cands.append((mb.group(1), (lead + b2).strip()[:40]))
        # 后：名字紧贴引号、到下一句引号之前没有别的人名（「“回去办点事。”林川扯了扯嘴角」）→ 动作归属就是说话人
        a2 = re.split(r"[“「]", after)[0]
        ma = re.match(r"^\s*[，,]?\s*" + np_, a2)
        if ma and not re.search(np_, a2[ma.end():]):
            cands.append((ma.group(1), a2[:min(len(a2), ma.end() + 8)]))
    if not cands:
        return "", ""
    # 前后都有依据但不是同一个人 → 存疑
    if len({c[0] for c in cands}) > 1:
        return "", "前后依据不一致：" + " / ".join(c[1][:20] for c in cands)
    return cands[0]


def ledger(prose, people=()):
    """正文 → 台词清单 [{"i","pos","text","norm","kind","speaker","evidence","confidence","count"}]。"""
    src = str(prose or "")
    names = []
    for p in people or ():
        if isinstance(p, dict):
            nm = str(p.get("name") or "").strip()
            if nm:
                names.append(nm)
            for a in (p.get("aliases") or []):
                if str(a).strip():
                    names.append(str(a).strip())
        elif str(p or "").strip():
            names.append(str(p).strip())
    out = []
    for m in _QUOTE_RE.finditer(src):
        q = m.group(1).strip()
        nq = _n(q)
        if not nq:
            continue
        start, end = m.start(), m.end()
        if _is_signage(src, start, end) or re.match(r"^\s*的(名字|字样|字|名)", src[end:end + 4]):
            kind = KIND_SIGNAGE
        elif _is_onomat(q, src, end):
            kind = KIND_ONOMAT
        else:
            ctx_b = re.split(r"[。！？\n]", src[max(0, start - 40):start])[-1]
            ctx_a = re.split(r"[。！？\n]", src[end:end + 24])[0]
            if _UNSPOKEN.search(ctx_b[-18:]) or _UNSPOKEN.search(ctx_a[:10]):
                kind = KIND_UNSPOKEN
            elif _RECALL.search(ctx_b) and not re.search(_SAY_V + r"[：:]?\s*$", ctx_b[-3:]):
                kind = KIND_RECALLED
            elif len(nq) == 1 and q[-1:] not in "！!？?。.":
                kind = KIND_ONOMAT                      # 单个字、没有句末标点：字形/量词，不是话
            elif _is_term(q, src, start, end):
                kind = KIND_TERM                        # P316：引称（的“商品”/所谓“朋友”）不是台词；没说出口/回忆先判
            else:
                kind = KIND_SPOKEN
        who, ev = ("", "")
        if kind in (KIND_SPOKEN, KIND_RECALLED):
            who, ev = _find_speaker(src, start, end, names)
        out.append({"i": len(out) + 1, "pos": start, "text": q, "norm": nq, "kind": kind,
                    "speaker": who, "evidence": ev, "confidence": "确定" if who else "存疑", "count": 0})
    counts = {}
    for e in out:
        if e["kind"] == KIND_SPOKEN:
            counts[e["norm"]] = counts.get(e["norm"], 0) + 1
    for e in out:
        e["count"] = counts.get(e["norm"], 0) if e["kind"] == KIND_SPOKEN else 0
    return out


def spoken(led):
    return [e for e in led if e["kind"] == KIND_SPOKEN]


def _same(a, b):
    a, b = _n(a), _n(b)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 4 and len(short) >= 0.6 * len(long_) and short in long_


def find(led, text):
    """一句剧本台词对应清单里的哪一条（同一句才算；有多条同文取第一条）。"""
    for e in led:
        if _same(e["text"], text):
            return e
    return None


def not_dialogue(led):
    """不该当台词的（没说出口 / 招牌 / 拟声 / 引称）。"""
    return [e for e in led if e["kind"] in (KIND_UNSPOKEN, KIND_SIGNAGE, KIND_ONOMAT, KIND_TERM)]


def summary(led):
    from collections import Counter
    c = Counter(e["kind"] for e in led)
    return {"总数": len(led), "说出口": c.get(KIND_SPOKEN, 0), "没说出口": c.get(KIND_UNSPOKEN, 0), "回忆": c.get(KIND_RECALLED, 0),
            "招牌": c.get(KIND_SIGNAGE, 0), "拟声": c.get(KIND_ONOMAT, 0), "引称": c.get(KIND_TERM, 0),
            "说话人存疑": sum(1 for e in led if e["kind"] == KIND_SPOKEN and not e["speaker"])}
