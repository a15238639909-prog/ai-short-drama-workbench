# -*- coding: utf-8 -*-
"""arrangement.py — 本话安排：一段想法 → 一张能看懂、能改的安排卡 → 确认后写作链服从它。

【为什么要有这一层】（用户 2026-09-10 定）
新手只会写一段想法，写不出结构表那十几栏。以前想法直接进模型，模型自己补的和用户要的
混在一起，用户看不出哪句是自己说的、哪句是模型编的，想改也无从下手。

这一层做的事：
  · 模型把想法补成六项（讲什么/拍什么/怎么拍/节奏/谁/哪里）+ 四段（开始/经过/变化/结束）+ 时间预算。
  · 每条打「你的要求」还是「建议」——**由代码判，不让模型自报**：模型说"这是你要的"不可信，
    判法是内容的二字词有一半以上出现在用户原话里就算用户要的。
  · 修改走点路径：模型只返回改了哪几条，未提到的一个字不动（整份重出会把没提的也顺手改掉）。
  · 确认时翻成现有写作链认的 plan_rows / story_design / one_line，写作代码一行不改：
    universal_writer.writing_input() 把 rows[0] 当「本话安排」、design.people 当「统一人物表」喂给千问。

不做的事：
  · 不在这里写故事正文、不生成人物卡——那是确认之后的事。
  · 不猜用户没说的取舍（时长、感觉从页面来，缺了用默认值，但标成「建议」）。
"""
import copy
import json
import re
import time
from pathlib import Path

# ─────────────────────────── 常量 ───────────────────────────

STAGES = ("开始", "经过", "变化", "结束")      # 单条想法起草的安排：固定四段（给新手看的格子）


def stages_of(arr):
    """这份安排的段名列表。整部规划派生的安排（beats_free）段数按内容定；其余固定四段（P285）。"""
    if isinstance(arr, dict) and arr.get("beats_free"):
        names = [_s(b.get("stage")) for b in (arr.get("beats") or []) if isinstance(b, dict)]
        names = [n for n in names if n]
        if len(names) >= 1:
            return tuple(names)
    return STAGES


def ending_of(arr):
    """本话结尾＝最后一段的描述（固定四段时就是「结束」）。"""
    beats = (arr or {}).get("beats") if isinstance(arr, dict) else None
    if isinstance(beats, list) and beats:
        last = beats[-1]
        return _s(last.get("text")) if isinstance(last, dict) else _s(last)
    return ""
ITEM_KEYS = ("what", "shoot", "how", "pace", "who", "where")
ITEM_LABELS = {"what": "第一话讲什么", "shoot": "主要拍什么", "how": "怎么拍",
               "pace": "节奏快还是慢", "who": "主角是谁", "where": "用哪些场景"}
PACES = ("日常", "推进", "高潮")
GEOMETRIES = ("对谈", "行进", "对峙", "追逐")
SRC_USER = "你的要求"
SRC_MODEL = "建议"
BUDGET_TOL = 0.15            # 各段秒数之和允许偏离总时长的比例

# 模型写的段名五花八门（开头/起/转折/结尾），都归到四段
_STAGE_ALIAS = {"开始": "开始", "开头": "开始", "起": "开始", "起点": "开始",
                "经过": "经过", "过程": "经过", "承": "经过", "中间": "经过", "发展": "经过",
                "变化": "变化", "转折": "变化", "转": "变化", "转变": "变化",
                "结束": "结束", "结尾": "结束", "合": "结束", "收尾": "结束", "落点": "结束"}

_INS_DIR = Path(__file__).resolve().parent.parent / "presets" / "instructions"


# ─────────────────────────── 小工具 ───────────────────────────

def _s(v):
    return str(v if v is not None else "").strip()


def _int(v, default=0):
    try:
        return int(float(str(v).strip()))
    except Exception:
        return default


def _bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return _s(v).lower() in ("true", "1", "yes", "是", "对", "真")


def _secs(v):
    """秒数：数字直接用；"8秒"、"约10s" 这种取里面的第一个数。判不出算 0（校验会拦）。"""
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return int(round(v))
    m = re.search(r"\d+(?:\.\d+)?", _s(v))
    return int(round(float(m.group(0)))) if m else 0


def _ins(name):
    """读指令词。走 authoring._ins 和别的角色一致；文件不在就用内置兜底，别让整条链断在读文件上。"""
    try:
        from . import authoring as _au
        return _au._ins(name)
    except Exception:
        try:
            return (_INS_DIR / name).read_text(encoding="utf-8")
        except Exception:
            return _FALLBACK_INS.get(name, "")


def _default_call(mt, temperature):
    """缺省走 authoring._q：有退避重试，而且进 _calls.log（直接调 qwen_client 日志里查不到）。"""
    from . import authoring as _au

    def call_model(system, user, **kw):
        return _au._q(system, user, mt=mt, temperature=temperature)
    return call_model


# ─────────────────────────── 规整（chat_json 用）───────────────────────────

def _people(v):
    """人物表掰成固定形状。没名字只有身份的，用身份词当名字并标 unnamed——
    正文写作认的是 name，空名字会让在场栏对不上人物表。名字身份都没有的丢掉。"""
    out = []
    if isinstance(v, dict):
        v = [v]
    for p in (v if isinstance(v, list) else []):
        if isinstance(p, str):
            p = {"name": p}
        if not isinstance(p, dict):
            continue
        name, role = _s(p.get("name")), _s(p.get("role") or p.get("identity"))
        unnamed = _bool(p.get("unnamed"))
        if not name and role:
            name, unnamed = role, True
        if not name:
            continue
        out.append({"name": name, "role": role, "relation": _s(p.get("relation")),
                    "look": _s(p.get("look") or p.get("appearance")),
                    "personality": _s(p.get("personality")), "unnamed": unnamed,
                    "sex": _s(p.get("sex")), "age": _s(p.get("age")), "voice": _s(p.get("voice"))})   # P316/P317：性别年龄声线跟着人物表走到建卡
    return out


def _places(v):
    out = []
    if isinstance(v, str):
        v = re.split(r"[、，,；;／/\n]", v)
    for x in (v if isinstance(v, list) else []):
        if isinstance(x, dict):
            x = x.get("name") or x.get("place") or x.get("text")
        x = _s(x)
        if x and x not in out:
            out.append(x)
    return out


def _stage_of(name):
    n = _s(name)
    if n in _STAGE_ALIAS:
        return _STAGE_ALIAS[n]
    for k, v in _STAGE_ALIAS.items():
        if k in n:
            return v
    return ""


def _beats_free(v):
    """段数按内容定的情节（整部规划派生，P285）：按顺序原样保留，段名没有就编号。"""
    out = []
    for k, b in enumerate((v if isinstance(v, list) else []), 1):
        if isinstance(b, str):
            b = {"text": b}
        if not isinstance(b, dict):
            continue
        text = _s(b.get("text") or b.get("content"))
        if not text:
            continue
        out.append({"stage": _s(b.get("stage")) or ("第%d段" % k), "text": text,
                    "weight": _s(b.get("weight")),
                    "source": b.get("source") if b.get("source") in (SRC_USER, SRC_MODEL) else "",
                    "added": [str(x).strip() for x in (b.get("added") or []) if isinstance(x, str) and str(x).strip()]})
    return out


def _beats(v):
    """四段情节：认段名归位，认不出的按顺序填空位，不足四段补空条（校验拦）。
    多出来的一律丢——四段是给新手看的固定格子，多了就看不懂了。"""
    if isinstance(v, dict):
        v = [{"stage": k, "text": x} for k, x in v.items()]
    filled = {st: None for st in STAGES}
    rest = []
    for b in (v if isinstance(v, list) else []):
        if isinstance(b, str):
            b = {"text": b}
        if not isinstance(b, dict):
            continue
        text = _s(b.get("text") or b.get("content"))
        src = b.get("source") if b.get("source") in (SRC_USER, SRC_MODEL) else ""
        st = _stage_of(b.get("stage") or b.get("name"))
        entry = {"text": text, "source": src, "added": [str(x).strip() for x in (b.get("added") or []) if isinstance(x, str) and str(x).strip()]}
        if st and filled[st] is None:
            filled[st] = entry
        else:
            rest.append(entry)
    for st in STAGES:
        if filled[st] is None and rest:
            filled[st] = rest.pop(0)
    return [{"stage": st, "text": (filled[st] or {}).get("text", ""),
             "source": (filled[st] or {}).get("source", ""),
             "added": (filled[st] or {}).get("added", [])} for st in STAGES]


def _budget(v):
    out = []
    for b in (v if isinstance(v, list) else []):
        if isinstance(b, str):
            b = {"beat": b}
        if not isinstance(b, dict):
            continue
        out.append({"beat": _s(b.get("beat") or b.get("name") or b.get("stage") or b.get("text")),
                    "seconds": _secs(b.get("seconds") if "seconds" in b else b.get("sec", b.get("time"))),
                    "focus": _s(b.get("focus") or b.get("note"))})
    return out


def _item(k, v):
    """一项掰成 {text, source, 结构字段}。模型常把一项直接写成字符串或数组，都收。"""
    if isinstance(v, str):
        v = {"text": v}
    elif isinstance(v, list):
        v = {"people": v} if k == "who" else ({"places": v} if k == "where" else {"text": "；".join(_s(x) for x in v)})
    if not isinstance(v, dict):
        v = {}
    it = {"text": _s(v.get("text") or v.get("desc")),
          "source": v.get("source") if v.get("source") in (SRC_USER, SRC_MODEL) else "",
          "added": [str(x).strip() for x in (v.get("added") or []) if isinstance(x, str) and str(x).strip()]}
    if k == "how":
        g = _s(v.get("geometry"))
        if g not in GEOMETRIES:
            g = next((x for x in GEOMETRIES if x in g), "")        # 「对谈式」→ 对谈；判不出留空（软核）
        it["geometry"] = g
    elif k == "pace":
        val = _s(v.get("value"))
        if not val:
            val = next((x for x in PACES if x in it["text"]), "")   # 只在没填时从描述里认；填错了让校验说
        it["value"] = val
    elif k == "who":
        it["people"] = _people(v.get("people"))
    elif k == "where":
        it["places"] = _places(v.get("places"))
    return it


def normalize(d):
    """把模型 JSON（或存过盘的安排）掰成固定形状；缺的补空/补默认，不在这里判对错。"""
    d = d if isinstance(d, dict) else {}
    raw_items = d.get("items")
    if not isinstance(raw_items, dict):
        raw_items = {k: d.get(k) for k in ITEM_KEYS}              # 六项被写在顶层
    hist = [h for h in (d.get("history") or []) if isinstance(h, dict)] if isinstance(d.get("history"), list) else []
    free = _bool(d.get("beats_free"))
    return {
        "schema": 1,
        "beats_free": free,
        "episode_no": _int(d.get("episode_no"), 0),
        "episode_uid": _s(d.get("episode_uid")),
        "start_state": _s(d.get("start_state")),
        "link_in": _s(d.get("link_in")),
        "link_out": _s(d.get("link_out")),
        "version": max(1, _int(d.get("version"), 1)),
        "status": "confirmed" if _s(d.get("status")) == "confirmed" else "draft",
        "idea": _s(d.get("idea")),
        "duration_sec": _int(d.get("duration_sec"), 0),
        "mood": _s(d.get("mood")),
        "whole_story": _bool(d.get("whole_story")),
        "first_episode_ends_at": _s(d.get("first_episode_ends_at")),
        "items": {k: _item(k, raw_items.get(k)) for k in ITEM_KEYS},
        "beats": _beats_free(d.get("beats")) if free else _beats(d.get("beats")),
        "budget": _budget(d.get("budget")),
        "history": hist,
        "confirmed_at": d.get("confirmed_at") or 0,
        # 「把后半部分留到下一话」剪在哪个 beat 之后（-1 不剪）。放在这里是为了
        # 修改/确认走 normalize 时不被丢掉——丢了就等于用户选过的"留到下一话"作废。
        "cut_after_beat": _cut(d.get("cut_after_beat"), len(_beats_free(d.get("beats"))) if free else None),
    }


def _cut(v, n=None):
    """cut_after_beat：段数之外（含 None、空串）一律当 -1（不剪）。"""
    c = _int(v, -1)
    n = n if n else len(STAGES)
    return c if 0 <= c < n else -1


# ─────────────────────────── 校验 ───────────────────────────

def validate(d, duration_sec=0):
    """格式对不够，内容要齐。抛 ValueError 让 chat_json 带着原因重试。

    硬拦的都是下游会直接出错的：六项空一项写作就少一栏；四段缺一段结构表缺一列；
    没人或人没名字在场栏对不上；节奏不在三种之内 normalize_rows 不认；
    预算和时长差太多剪出来的片子长度不对；整部构想没说第一话讲到哪，写作会把整部塞进一话。
    镜头几何是软核：不在四个值里就置空，剧本层自己会判。
    """
    if not isinstance(d, dict):
        raise ValueError("安排不是一个对象")
    items = d.get("items") if isinstance(d.get("items"), dict) else {}
    for k in ITEM_KEYS:
        it = items.get(k) if isinstance(items.get(k), dict) else {}
        if not _s(it.get("text")):
            raise ValueError("「%s」这一项还没写" % ITEM_LABELS[k])
    beats = d.get("beats") if isinstance(d.get("beats"), list) else []
    if d.get("beats_free"):
        if not beats:
            raise ValueError("这一话一段情节都没有")
        for k, b in enumerate(beats, 1):
            if not isinstance(b, dict) or not _s(b.get("text")):
                raise ValueError("情节第 %d 段是空的" % k)
    else:
        if len(beats) != len(STAGES):
            raise ValueError("情节要正好四段：开始、经过、变化、结束（给了 %d 段）" % len(beats))
        for st, b in zip(STAGES, beats):
            if not isinstance(b, dict) or not _s(b.get("text")):
                raise ValueError("情节的「%s」那一段是空的" % st)
    people = (items.get("who") or {}).get("people") or []
    if not people:
        raise ValueError("「主角是谁」里至少要有一个人")
    for p in people:
        if not isinstance(p, dict) or not _s(p.get("name")):
            raise ValueError("有个人物既没名字也没身份")
    pace = _s((items.get("pace") or {}).get("value"))
    if pace not in PACES:
        raise ValueError("节奏只能是 日常／推进／高潮 三选一，给的是「%s」" % pace)
    budget = d.get("budget") if isinstance(d.get("budget"), list) else []
    if not budget and _bool(d.get("beats_free")):
        # P300：整部规划派生的安排没有时间预算——时长是剧本切出来之后的结果，不在规划阶段定
        budget = []
    elif not budget:
        raise ValueError("时间预算是空的")
    if any(_secs(b.get("seconds")) <= 0 for b in budget if isinstance(b, dict)):
        raise ValueError("时间预算里有一段是 0 秒")
    total = sum(_secs(b.get("seconds")) for b in budget if isinstance(b, dict))
    dur = _int(duration_sec, 0) or _int(d.get("duration_sec"), 0)
    if dur > 0 and abs(total - dur) > BUDGET_TOL * dur:
        raise ValueError("时间预算各段加起来 %d 秒，和 %d 秒差太多（允许上下 %d%%）"
                         % (total, dur, int(BUDGET_TOL * 100)))
    how = items.get("how") if isinstance(items.get("how"), dict) else {}
    if _s(how.get("geometry")) not in GEOMETRIES:
        how["geometry"] = ""
    if _bool(d.get("whole_story")) and not _s(d.get("first_episode_ends_at")):
        raise ValueError("想法是整部构想时，要写清第一话讲到哪里（first_episode_ends_at）")
    return d


# ─────────────────────────── 打标签（纯代码）───────────────────────────

# 停用词：先去多字的，再把单字虚词当分隔符——「老板和年轻学徒」切成「老板」「年轻学徒」，
# 二字词里就没有「板和」「和年」这种跨词碎片，比对不会被它们稀释。
_STOP_WORDS = ("为什么", "什么", "怎么", "这样", "那样", "这个", "那个", "这些", "那些",
               "一点", "一些", "一下", "一起", "然后", "后来", "最后", "开始", "起来", "下去", "出来",
               "已经", "正在", "还是", "就是", "但是", "可是", "不过", "因为", "所以", "如果", "虽然",
               "而且", "或者", "以及", "并且", "自己", "他们", "她们", "我们", "你们", "多一点", "多一些")
_STOP_CHARS = set("的了着过和与及是在有也都就要把被让给很更多不没个这那他她它我你们吧呢啊吗又还再才却地得之其而或并对向从到比将一")
_WORD_CHAR = re.compile(r"[\w一-龥]")


def _segments(text):
    t = _s(text)
    for w in _STOP_WORDS:
        t = t.replace(w, " ")
    out, buf = [], []
    for ch in t:
        if ch in _STOP_CHARS or not _WORD_CHAR.match(ch):
            if buf:
                out.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def _bigrams(text):
    s = set()
    for seg in _segments(text):
        for i in range(len(seg) - 1):
            s.add(seg[i:i + 2])
    return s


def overlap(text, ref):
    """text 去停用词后的二字词，有多大比例出现在 ref 里（0～1）。不看语序，换个说法也对得上。"""
    b = _bigrams(text)
    if not b:
        return 0.0
    r = _bigrams(ref)
    return sum(1 for g in b if g in r) / float(len(b))


_CLAUSE_SPLIT = re.compile(r"[，。；：、！？,.;:!?\n]+")


def clauses(text):
    """按标点拆分句。纯数字/单字的碎片（「1.」「2.」这种编号）不算分句——
    真跑时「其中系统补的：1；…；2；…」把编号也列了出来。"""
    out = []
    for c in _CLAUSE_SPLIT.split(_s(text)):
        c = c.strip()
        k = re.sub(r"[^\w一-龥]", "", c)
        if len(k) < 2 or k.isdigit():
            continue
        out.append(c)
    return out


_PUNCT = re.compile(r"[^\w一-龥]+")


def _runs(text, n=3):
    """原文（只去标点空白）里的连续 n 字片段——判"这几个字是不是照抄了用户的话"。
    这里**不去**"的/了"这类字：用户写「想换新的」、模型写「建议换新的」，共同的正是「换新的」。"""
    out = set()
    for seg in _PUNCT.split(_s(text)):
        for i in range(len(seg) - n + 1):
            out.add(seg[i:i + n])
    return out


def grounded(clause, ref):
    """这个分句有没有用户原话的依据：≥3 字连续相同，或 ≥2 个二字词相同。"""
    if not _s(clause) or not _s(ref):
        return False
    if _runs(clause, 3) & _runs(ref, 3):
        return True
    return len(_bigrams(clause) & _bigrams(ref)) >= 2


def source_of(text, ref):
    """整条的标签：有任一分句有依据就是「你的要求」，一个都没有才是「建议」。"""
    return SRC_USER if any(grounded(c, ref) for c in clauses(text)) else SRC_MODEL


def added_of(text, ref):
    """「你的要求」里系统补的那几个分句（没依据的）。整条都是建议时返回空（整条本来就标了）。"""
    cs = clauses(text)
    if not any(grounded(c, ref) for c in cs):
        return []
    return [c for c in cs if not grounded(c, ref)]


def _label_keys(paths):
    """点路径 → 要重打标签的格子（items.what / beats[2]）。budget 等不打标签的路径给 None。"""
    if paths is None:
        return None
    keys = set()
    for p in paths:
        p = _s(p)
        m = re.match(r"^items\.(what|shoot|how|pace|who|where)", p)
        if m:
            keys.add("items." + m.group(1))
        elif p == "items":
            keys.update("items." + k for k in ITEM_KEYS)
        else:
            m = re.match(r"^beats\[(\d+)\]", p)
            if m:
                keys.add("beats[%d]" % int(m.group(1)))
            elif p == "beats":
                keys.update("beats[%d]" % i for i in range(len(stages_of(arr))))
    return keys


def label(arr, idea, extra_text="", only=None):
    """给六项和四段打 source。ref = 想法原文 + 修改时那句指令。
    人物名字在原话里出现，「主角是谁」也算用户要的（起名的是用户，其余描述是模型补的没关系）。
    only：只重打这些点路径对应的格子（修改时用），None 全打。"""
    arr = normalize(arr)
    ref = _s(idea) + "\n" + _s(extra_text)
    keys = _label_keys(only)
    for k in ITEM_KEYS:
        if keys is not None and ("items." + k) not in keys:
            continue
        it = arr["items"][k]
        src = source_of(it["text"], ref)
        if k == "who" and any(len(p["name"]) >= 2 and p["name"] in ref for p in it["people"]):
            src = SRC_USER
        if k == "who":
            # 名字是不是系统起的由代码判：原话里没有这个名字就是系统起的（P256，模型自报的 unnamed 不可靠）
            for p in it["people"]:
                nm = _s(p.get("name"))
                p["unnamed"] = bool(nm) and nm not in ref
        it["source"] = src
        it["added"] = added_of(it["text"], ref) if src == SRC_USER else []
    for i, b in enumerate(arr["beats"]):
        if keys is not None and ("beats[%d]" % i) not in keys:
            continue
        b["source"] = source_of(b["text"], ref)
        b["added"] = added_of(b["text"], ref) if b["source"] == SRC_USER else []
    return arr


# ─────────────────────────── 情节归属（切段 / 提示词 / 检查器共用）───────────────────────────
# 【为什么是纯代码】画面块属于四段里的哪一段，是"这块和哪段的描述最像"——二字词重合就能判，
# 让模型判既慢又会在四段之间来回跳。情节顺序是固定的（开始→经过→变化→结束），
# 所以还加一条**单调约束**：后一块的段号不许比前一块小；判不出的块沿用前一块。

def confirmed_of(settings):
    """settings 里已确认的安排（status=="confirmed"），没有就 None。
    返回的是存着的那个字典本身（不 normalize）：arrangement_ops 后加的字段不能在这里被洗掉。"""
    a = settings.get("arrangement") if isinstance(settings, dict) else None
    if isinstance(a, dict) and _s(a.get("status")) == "confirmed":
        return a
    return None


def cut_of(arr):
    """cut_after_beat（-1 不剪；0..3 只出到这个 beat）。"""
    return _cut((arr or {}).get("cut_after_beat")) if isinstance(arr, dict) else -1


def stage_name(i, arr=None):
    """段号 → 段名；其余 → 空串。"""
    try:
        i = int(i)
    except Exception:
        return ""
    st = stages_of(arr)
    return st[i] if 0 <= i < len(st) else ""


def beat_sig(arr):
    """影响切段结果的那部分安排（四段文本 + 主要拍什么）的指纹。
    _sliced 的缓存键要带上它：安排改了，切段的边界也会变。没有安排 → 空串。"""
    if not isinstance(arr, dict):
        return ""
    import hashlib
    txt = "\n".join(_ref_texts(arr))
    if not txt.strip():
        return ""
    return hashlib.md5(txt.encode("utf-8", "ignore")).hexdigest()[:8]


_PT_SPLIT = re.compile(r"[。；;\n！!？?、，,]")


def _ref_texts(arr):
    """四段各自的参考文本：段描述 + 「主要拍什么」里最像它的要点。
    要点按顿号/逗号也拆——「两人边修边聊、学徒态度变化、最后灯亮」是三件事，
    整条塞给一段会把另外两段的词也带过去。"""
    beats = (arr or {}).get("beats") if isinstance(arr, dict) else None
    beats = beats if isinstance(beats, list) else []
    refs = []
    for i in range(len(stages_of(arr))):
        b = beats[i] if i < len(beats) else None
        refs.append(_s(b.get("text")) if isinstance(b, dict) else _s(b))
    it = ((arr or {}).get("items") or {}).get("shoot") if isinstance(arr, dict) else None
    shoot = _s(it.get("text")) if isinstance(it, dict) else _s(it)
    for p in (x.strip() for x in _PT_SPLIT.split(shoot)):
        if len(p) < 2:
            continue
        pb = _bigrams(p)
        sc = [len(pb & _bigrams(r)) for r in refs]
        top = max(sc)
        # 并列（「学徒态度变化」既像开始也像变化）不归任何一段：归错比不归更坏
        if top > 0 and sc.count(top) == 1:
            k = sc.index(top)
            refs[k] = refs[k].rstrip("。") + "。" + p
    return refs


def _noise_bigrams(arr):
    """人名和地点的二字词——「谁」和「哪里」不是情节信号：地点行「修理店内」的「修理」
    会撞上「变化」里的「修理工具」，把开场的空镜判成变化（实测）。"""
    out = set()
    items = (arr or {}).get("items") if isinstance(arr, dict) else None
    items = items if isinstance(items, dict) else {}
    who = items.get("who") if isinstance(items.get("who"), dict) else {}
    for p in (who.get("people") or []):
        nm = _s(p.get("name") if isinstance(p, dict) else p)
        if nm:
            out |= _bigrams(nm)
    where = items.get("where") if isinstance(items.get("where"), dict) else {}
    for pl in (where.get("places") or []):
        out |= _bigrams(_s(pl))
    return out


def _weighted_refs(refs, noise=()):
    """每段参考文本的二字词 → 权重。四段都有的词（人名、「旧灯」）权重 1/4，
    只有一段有的权重 1——不这样人名会让每块都"像所有段"，判不出谁是谁。
    noise 里的词（人名/地点）整个不算。"""
    sets = [_bigrams(r) - set(noise or ()) for r in refs]
    cnt = {}
    for s in sets:
        for g in s:
            cnt[g] = cnt.get(g, 0) + 1
    return [{g: 1.0 / cnt[g] for g in s} for s in sets]


def _score_rows(texts, wrefs):
    """每个文本对四段的得分（减掉本行最小值：四段一样像＝没有信号）。"""
    rows = []
    for t in texts:
        bg = _bigrams(t)
        row = [sum(w[g] for g in bg if g in w) for w in wrefs]
        lo = min(row) if row else 0.0
        rows.append([x - lo for x in row])
    return rows


def _monotone_assign(rows):
    """单调约束下的最优归属（动态规划）：总得分最大，且段号不减。
    并列时一律取**更早**的段（没证据不往后跳）；全 0 的行沿用前一行；
    开头就全 0 的行算「开始」；整份没有一行有信号 → 全 -1。"""
    n = len(rows)
    m = len(rows[0]) if rows and rows[0] else len(STAGES)     # 列数就是段数（P285）
    if not n:
        return []
    if not any(any(x > 0 for x in r) for r in rows):
        return [-1] * n
    dp = [[0.0] * m for _ in range(n)]
    bk = [[0] * m for _ in range(n)]
    dp[0] = list(rows[0])
    for i in range(1, n):
        best, bj = float("-inf"), 0
        for j in range(m):
            if dp[i - 1][j] > best:               # 严格大于：并列留在更早的 k
                best, bj = dp[i - 1][j], j
            dp[i][j] = rows[i][j] + best
            bk[i][j] = bj
    last = dp[n - 1]
    j = last.index(max(last))                    # 并列取最小：没证据不往后跳
    out = [0] * n
    for i in range(n - 1, -1, -1):
        out[i] = j
        j = bk[i][j]
    prev = 0
    for i in range(n):
        if any(x > 0 for x in rows[i]):
            prev = out[i]
        else:
            out[i] = prev
    return out


def beat_map(blocks, arr):
    """画面块 → 段号列表（0～3；没安排或判不出 → -1）。blocks 是画面块文本列表。"""
    blocks = [_s(b) for b in (blocks or [])]
    if not blocks or not isinstance(arr, dict):
        return [-1] * len(blocks)
    refs = _ref_texts(arr)
    if not any(refs):
        return [-1] * len(blocks)
    rows = _score_rows(blocks, _weighted_refs(refs, _noise_bigrams(arr)))
    for i, b in enumerate(blocks):
        if b.startswith("──"):                    # 地点行只说在哪，不说发生什么：没信号
            rows[i] = [0.0] * len(stages_of(arr))
    return _monotone_assign(rows)


def budget_map(arr):
    """预算每条 → 段号。正好四条就按顺序一一对应；条数不对（模型常写五条）
    就按条目名和四段描述的相似度加单调约束判。"""
    budget = (arr or {}).get("budget") if isinstance(arr, dict) else None
    budget = [b for b in budget if isinstance(b, dict)] if isinstance(budget, list) else []
    if not budget:
        return []
    if len(budget) == len(stages_of(arr)):
        return list(range(len(stages_of(arr))))
    refs = _ref_texts(arr)
    if not any(refs):
        return [-1] * len(budget)
    return _monotone_assign(_score_rows([_s(b.get("beat")) for b in budget],
                                        _weighted_refs(refs, _noise_bigrams(arr))))


def focus_of(arr, beat_index):
    """这一段对应预算条的 focus（多条用「；」连）。没有 → 空串。"""
    budget = (arr or {}).get("budget") if isinstance(arr, dict) else None
    budget = [b for b in budget if isinstance(b, dict)] if isinstance(budget, list) else []
    try:
        bi = int(beat_index)
    except Exception:
        return ""
    if bi < 0 or not budget:
        return ""
    got = []
    for b, k in zip(budget, budget_map(arr)):
        f = _s(b.get("focus"))
        if k == bi and f and f not in got:
            got.append(f)
    return "；".join(got)


def budget_seconds(arr):
    """四段各自的预算秒数 [开始, 经过, 变化, 结束]（没预算的段是 0）。"""
    out = [0] * len(stages_of(arr))
    budget = (arr or {}).get("budget") if isinstance(arr, dict) else None
    budget = [b for b in budget if isinstance(b, dict)] if isinstance(budget, list) else []
    for b, k in zip(budget, budget_map(arr)):
        if 0 <= k < len(stages_of(arr)):
            out[k] += _secs(b.get("seconds"))
    return out


SLICE_MIN_SEC = 5.0      # 太短的镜头 H3 出不来东西
SLICE_MAX_SEC = 15.0     # H3 硬上限


def apply_budget_to_slices(slices, arr):
    """把每段情节的预算秒数按文字量分给它下面的切片（P260）。返回新列表，不改传入的。

    模型估的动作时长忽长忽短（同样 30 秒的安排，一篇切出 15 秒、一篇 154 秒）；
    安排里每段情节有预算，就按预算分：多给关键事件，小动作用剩余时间。
    判不出情节段的切片（beat_index=-1）保留原秒数。每片 5～15 秒。
    """
    out = [dict(x) for x in (slices or [])]
    budget = budget_seconds(arr)
    if not out or not any(budget):
        return out
    groups = {}
    for i, x in enumerate(out):
        try:
            bi = int(x.get("beat_index", -1))
        except Exception:
            bi = -1
        if 0 <= bi < len(budget):
            groups.setdefault(bi, []).append(i)
    for bi, idxs in groups.items():
        total = float(budget[bi] or 0)
        if total <= 0:
            continue
        weights = [max(1, len(_s(out[i].get("text")))) for i in idxs]
        wsum = float(sum(weights))
        secs = [max(SLICE_MIN_SEC, min(SLICE_MAX_SEC, total * w / wsum)) for w in weights]
        for i, v in zip(idxs, secs):
            out[i]["seconds"] = round(v, 1)
            out[i]["over"] = v > SLICE_MAX_SEC + 0.01
            out[i]["seconds_from"] = "预算"
    return out


_SENT_END = re.compile(r"(?<=[。！？!?])")


def _prose_sents(prose):
    out = []
    for para in _s(prose).split("\n"):
        for x in _SENT_END.split(para):
            x = x.strip()
            if len(re.sub(r"[^\w一-龥]", "", x)) >= 4:
                out.append(x)
    return out


def missing_beats(pictures, arr):
    """安排的四段情节里，哪几段在画面稿里没有任何镜头。返回 [段名]。

    P278：原来这里会把原文里最像的一句插成一幅画面。实测会和模型已经写过的重复
    （同一个动作演两遍），而"换了说法算不算拍到"字面判不出——只查不改，交给检查器问用户。
    """
    pics = _s(pictures)
    if not pics.strip() or not isinstance(arr, dict):
        return []
    blocks = [b for b in re.split(r"\n\s*\n", pics) if b.strip()]
    if not blocks:
        return []
    bm = beat_map(blocks, arr)
    beats = arr.get("beats") if isinstance(arr.get("beats"), list) else []
    out = []
    _st = stages_of(arr)
    for bi in range(len(_st)):
        if bi in bm:
            continue
        bt = _s((beats[bi] or {}).get("text")) if bi < len(beats) and isinstance(beats[bi], dict) else ""
        if bt:
            out.append(_st[bi])
    return out


# ─────────────────────────── 起草（问千问）───────────────────────────

def _project_context(settings):
    """项目里已经定了的东西给模型看一眼：世界、题材、已确认事实、已有人物。没有就不给。"""
    s = settings if isinstance(settings, dict) else {}
    lines = []
    for key, name in (("world_type", "世界"), ("genre", "题材"), ("story_canon", "已确认事实")):
        v = s.get(key)
        v = "；".join(_s(x) for x in v if _s(x)) if isinstance(v, list) else _s(v)
        if v:
            lines.append("%s：%s" % (name, v))
    names = [_s(p.get("name")) for p in (s.get("plan_people") or []) if isinstance(p, dict) and _s(p.get("name"))]
    if names:
        lines.append("已有人物：" + "、".join(names))
    return "\n".join(lines)


def draft(idea, duration_sec=60, mood="", settings=None, call_model=None):
    """一段想法 → 安排卡草稿（模型出草案 → normalize → validate → label）。

    call_model(system, user, **kw) -> str；不传就用 authoring._q（mt=4000, temperature=0.3）。
    """
    idea = _s(idea)
    if not idea:
        raise ValueError("想法是空的，先写一段想法")
    duration_sec = _int(duration_sec, 60)
    if duration_sec <= 0:
        duration_sec = 60
    mood = _s(mood)
    from .model_json import chat_json
    if call_model is None:
        call_model = _default_call(4000, 0.3)
    user = "【想法】\n%s\n\n【时长】%d 秒\n【感觉】%s" % (idea, duration_sec, mood or "按想法定")
    ctx = _project_context(settings)
    if ctx:
        user += "\n\n【项目已有设定】\n" + ctx

    def _v(d):
        validate(d, duration_sec)

    obj, _raw = chat_json(_ins("本话安排_指令词.txt"), user, call_model, validate=_v, tries=3, normalize=normalize)
    arr = normalize(obj)
    arr.update({"idea": idea, "duration_sec": duration_sec, "mood": mood,
                "version": 1, "status": "draft", "confirmed_at": 0,
                "history": [{"version": 1, "instruction": "", "changed": [], "at": time.time()}]})
    return label(arr, idea)


# ─────────────────────────── 修改（点路径）───────────────────────────

_PATH_RE = re.compile(
    r"^(?:items(?:\.(?:what|shoot|how|pace|who|where)(?:\.(?:text|geometry|value|people|places))?)?"
    r"|beats(?:\[[0-3]\](?:\.text)?)?"
    r"|budget(?:\[\d+\](?:\.(?:beat|seconds|focus))?)?"
    r"|whole_story|first_episode_ends_at|duration_sec|mood)$")


def model_view(arr):
    """给模型看的安排：去掉 source/history 这些它不该管的。"""
    a = normalize(arr)
    return {"idea": a["idea"], "duration_sec": a["duration_sec"], "mood": a["mood"],
            "whole_story": a["whole_story"], "first_episode_ends_at": a["first_episode_ends_at"],
            "items": {k: {x: y for x, y in a["items"][k].items() if x != "source"} for k in ITEM_KEYS},
            "beats": [{"stage": b["stage"], "text": b["text"]} for b in a["beats"]],
            "budget": a["budget"]}


def _norm_changes(d):
    """模型有时把 changes 平铺在顶层（键就是点路径），或把整份包在别的键下——掰成 {changes, why}。"""
    if isinstance(d, list):
        d = {"changes": {}}
    d = dict(d or {})
    if not isinstance(d.get("changes"), dict):
        flat = {k: v for k, v in d.items() if isinstance(k, str) and _PATH_RE.match(k)}
        d = {"changes": flat, "why": d.get("why", "")}
    return d


def _changes_of(obj):
    changes = (obj or {}).get("changes")
    if not isinstance(changes, dict) or not changes:
        raise ValueError("没有 changes，或它是空的——要以点路径为键给出改动")
    bad = [k for k in changes if not _PATH_RE.match(_s(k))]
    if bad:
        raise ValueError("不认识的点路径：%s（可用的如 items.what.text、beats[2].text、budget）" % "、".join(bad[:4]))
    return {_s(k): v for k, v in changes.items()}


def _set_path(arr, path, value):
    """按点路径落一个值。格子是对象、给的是字符串 → 改它的 text；都是对象 → 合并；其余整个替换。"""
    toks = [int(a) if a else b for a, b in re.findall(r"\[(\d+)\]|([A-Za-z_]+)", path)]
    node = arr
    for k in toks[:-1]:
        if isinstance(k, int):
            if not isinstance(node, list) or k >= len(node):
                raise ValueError("路径 %s 的下标超出范围" % path)
            node = node[k]
        else:
            if not isinstance(node, dict):
                raise ValueError("路径 %s 走不通" % path)
            node = node.setdefault(k, {})
    last = toks[-1]
    if isinstance(last, int):
        if not isinstance(node, list):
            raise ValueError("路径 %s 走不通" % path)
        if last < len(node):
            cur = node[last]
            if isinstance(cur, dict) and isinstance(value, dict):
                cur.update(value)
            elif isinstance(cur, dict) and isinstance(value, str):
                cur["text"] = value
            else:
                node[last] = value
        elif last == len(node):
            node.append(value)
        else:
            raise ValueError("路径 %s 的下标超出范围" % path)
    else:
        cur = node.get(last) if isinstance(node, dict) else None
        if isinstance(cur, dict) and isinstance(value, dict):
            cur.update(value)
        elif isinstance(cur, dict) and isinstance(value, str):
            cur["text"] = value
        else:
            node[last] = value


def apply_changes(arr, changes):
    """把 {点路径: 新值} 落到安排上，未提到的字段一律不动。返回规整后的新安排。"""
    a = normalize(arr)
    for path, value in (changes or {}).items():
        _set_path(a, path, value)
    return normalize(a)


def revise(arr, instruction, settings=None, call_model=None):
    """一句修改指令 → 模型只回 {changes, why} → 代码应用、重打标签、version+1、history 追加。
    改过的卡回到 draft：确认过的安排被改了，落盘的结构表已经不是这张卡，得再确认一次。"""
    instruction = _s(instruction)
    if not instruction:
        raise ValueError("没说要改什么")
    cur = normalize(arr)
    from .model_json import chat_json
    if call_model is None:
        call_model = _default_call(2500, 0.2)
    user = ("【当前安排】\n%s\n\n【用户的修改要求】\n%s"
            % (json.dumps(model_view(cur), ensure_ascii=False, indent=1), instruction))
    got = {}

    def _v(obj):
        changes = _changes_of(obj)
        trial = apply_changes(copy.deepcopy(cur), changes)
        validate(trial, trial["duration_sec"])                  # 改完整张卡仍要成立（预算和时长对得上）
        got.update({"arr": trial, "changes": changes, "why": _s(obj.get("why"))})

    chat_json(_ins("本话安排修改_指令词.txt"), user, call_model, validate=_v, tries=3, normalize=_norm_changes)
    new = got["arr"]
    changed = list(got["changes"].keys())
    new["idea"] = cur["idea"]
    new = label(new, new["idea"], extra_text=instruction, only=changed)
    new["version"] = cur["version"] + 1
    new["status"] = "draft"
    new["confirmed_at"] = 0
    new["history"] = list(cur["history"]) + [{"version": new["version"], "instruction": instruction,
                                              "changed": changed, "why": got["why"], "at": time.time()}]
    return new


# ─────────────────────────── 确认落盘（对接写作链）───────────────────────────

def _points(text):
    return [x.strip() for x in re.split(r"[。；;\n！!？?]", _s(text)) if x.strip()]


def words_for(duration_sec):
    """本话时长 → 正文字数范围。约 11 字/秒：60 秒 ≈ 660 字，给 ±25%。下限 200，上限 4000。
    8 字/秒时 30 秒只写了 222 字、切出 15 秒（浏览器实测）；无台词的动作句演得快，按 11 字/秒更接近。"""
    d = _int(duration_sec, 60)
    mid = max(200, min(4000, d * 11))
    lo, hi = int(mid * 0.75), int(mid * 1.25)
    return "%d～%d" % (lo, hi)


def to_plan(arr):
    """安排卡 → (结构表第一行, 设计稿)。形状照 universal_writer.normalize_rows / writing_input 认的来。"""
    a = normalize(arr)
    it = a["items"]
    beats = {b["stage"]: b["text"] for b in a["beats"]}
    names = [p["name"] for p in it["who"]["people"] if p.get("name")]
    events = [e for e in dict.fromkeys([beats.get("经过", "")] + _points(it["shoot"]["text"])) if e]
    row = {"no": 1, "one_line": it["what"]["text"], "pace": it["pace"]["value"],
           "cast": "、".join(names), "place": "、".join(it["where"]["places"]),
           "start": beats.get("开始", ""), "events": events, "change": beats.get("变化", ""),
           "landing": beats.get("结束", ""), "geometry": it["how"]["geometry"],
           "resistance": "", "goal": ""}
    synopsis = ""
    if a["whole_story"]:
        # 整部构想：用户原话就是梗概；第一话到哪停一并带上，写作才知道后面的留给后话
        synopsis = a["idea"] + ("\n第一话讲到：" + a["first_episode_ends_at"] if a["first_episode_ends_at"] else "")
    design = {"schema": 1, "synopsis": synopsis,
              "people": [{"name": p["name"], "role": p["role"], "relation": p["relation"], "unnamed": p["unnamed"],
                          "sex": p.get("sex", ""), "age": p.get("age", "")}
                         for p in it["who"]["people"]],
              "notes": "感觉：%s；时长：%d秒" % (a["mood"], a["duration_sec"])}
    return row, design


def confirm(settings, arr):
    """确认：写 one_line（空时）、save_design 落 plan_rows/story_design，状态置 confirmed。返回安排卡。
    one_line 必须先写再 save_design——设计稿的 source_sig 是按 one_line 算的，反了整份设计会被当成过期摘掉。"""
    if not isinstance(settings, dict):
        raise ValueError("没有项目设置，无法落盘")
    from . import universal_writer as uw
    a = normalize(arr)
    validate(a, a["duration_sec"])
    if not _s(settings.get("one_line")):
        settings["one_line"] = a["idea"]
    # 【时长 → 正文字数】不传的话写作按默认 1500～2500 字写，60 秒的本话切出 150 秒（P258）。
    # 约 8 字/秒，给 ±25%；用户自己填过就不动。
    if not _s(settings.get("prose_words")) or settings.get("prose_words_auto"):
        settings["prose_words"] = words_for(a["duration_sec"])
        settings["prose_words_auto"] = True
    row, design = to_plan(a)
    uw.save_design(settings, [row], design)
    if not uw.active_design(settings):
        raise RuntimeError("安排没能落进写作链：设计稿和结构表指纹对不上")
    a["status"] = "confirmed"
    a["confirmed_at"] = time.time()
    settings["arrangement"] = a
    return a


# ─────────────────────────── 兜底指令词（文件缺失时）───────────────────────────

_FALLBACK_INS = {
    "本话安排_指令词.txt": """你是短片策划。用户只给一段想法，你补成一张新手能看懂的本话安排，只输出 JSON：
{"whole_story":false,"first_episode_ends_at":"",
 "items":{"what":{"text":"第一话讲什么，一两句"},"shoot":{"text":"主要拍什么"},
  "how":{"text":"怎么拍","geometry":"对谈/行进/对峙/追逐之一，都不像留空"},
  "pace":{"text":"为什么是这个节奏","value":"日常/推进/高潮之一"},
  "who":{"text":"主角是谁","people":[{"name":"","role":"","relation":"","look":"","personality":"","unnamed":false}]},
  "where":{"text":"用哪些场景","places":["地点"]}},
 "beats":[{"stage":"开始","text":""},{"stage":"经过","text":""},{"stage":"变化","text":""},{"stage":"结束","text":""}],
 "budget":[{"beat":"","seconds":8,"focus":""}]}
各段秒数加起来等于给定时长。想法里的人没名字就起一个好记的。想法是整部构想时 whole_story 填 true 并写 first_episode_ends_at。""",
    "本话安排修改_指令词.txt": """用户对这张本话安排说了一句要改什么。只改被要求的那几条，其余保持原样。
只输出 JSON：{"changes":{"<点路径>":<新值>},"why":"一句话"}。
点路径如 items.what.text、items.who.people、beats[3].text（0开始 1经过 2变化 3结束）、budget、first_episode_ends_at。""",
}
