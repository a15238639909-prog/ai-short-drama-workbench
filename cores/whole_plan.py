# -*- coding: utf-8 -*-
"""whole_plan.py — 整部规划（用户 2026-09-11 定的默认流程）

一段故事想法 → AI 补齐完整剧情 → 自动分话、分配时长 → 展示整部方案 → 用一句话改并确认
→ 按确认方案逐话生成正文、剧本。

复用 universal_writer.plan（完整梗概 + 分话结构，两次调用），分话结构的指令词已扩成带
本话作用 / 场景顺序 / 话内段落与快慢 / 预计时长 / 前后承接。本模块负责：
  · 规整与校验（normalize / validate / problems）
  · 页面视图（summary）
  · 聊天式修改（revise，一次模型调用）和直接编辑（edit，零模型）
  · 改动影响（impact：改了哪些话、为什么影响后面、哪些旧稿要更新/退休）
  · 确认落盘（confirm：写进 plan_rows / story_design，按话次 uid 给旧稿标过期或退休）
  · 派生某一话的「本话安排」（episode_arrangement：段数按内容定，喂给现有正文/剧本链）

不做的：不重写工作台；不改写作链的接口——写作仍读 plan_rows[ep-1]，剧本仍读 confirmed_arrangement。
"""
import copy
import json
import re
import time
import uuid

SCHEMA = 2
STATUS_DRAFT = "draft"
STATUS_CONFIRMED = "confirmed"
WEIGHT_FOCUS = "重点"
WEIGHT_PASS = "带过"
PACES = ("舒缓", "适中", "紧凑")                                    # P300：每话节奏三选一
_PACE_LEGACY = {"日常": "舒缓", "推进": "适中", "高潮": "紧凑"}           # 旧规划/旧模板的词照映射
PACE_TO_LEGACY = {"舒缓": "日常", "适中": "推进", "紧凑": "高潮"}          # 投影给旧的安排卡/结构表用
PACE_MEANING = {"舒缓": "多展开互动、观察和情绪变化，给关键交流留空间",
                "适中": "交代、交流和事件推进相对均衡",
                "紧凑": "过渡更简洁，行动和回应衔接更快，关键因果仍然完整"}


def norm_pace(v, default="适中"):
    v = _s(v)
    v = _PACE_LEGACY.get(v, v)
    return v if v in PACES else default
EP_MIN_SEC, EP_MAX_SEC = 15, 400          # 一话的合理区间（外面的当规划写错，报出来）
BEAT_MIN, BEAT_MAX = 1, 8

# ─────────────────────────── 小工具 ───────────────────────────

_MALE_W = re.compile(r"少年|男孩|男主|男人|男子|男生|少爷|公子|王子|父亲|爸|爷|叔|舅|伯|兄|弟|哥|儿子|丈夫|夫君|先生|老板(?!娘)|和尚|男|他")
_FEMALE_W = re.compile(r"少女|女孩|女主|女人|女子|女生|小姐|公主|母亲|妈|奶|姨|婶|姐|妹|女儿|妻|娘|夫人|太太|女仆|侍女|猫娘|尼姑|修女|女|她")


def norm_sex(v):
    v = str(v or "").strip()
    if v in ("男", "女"):
        return v
    if v.lower() in ("male", "m", "man", "boy"):
        return "男"
    if v.lower() in ("female", "f", "woman", "girl"):
        return "女"
    return ""


def norm_age(v, role=""):
    """保留明确年龄或年龄段；不从身份关系猜一个数字。"""
    return str(v if v is not None else "").strip()


def guess_sex(name, role="", relation="", idea=""):
    """人物表没写性别时按身份词推（判断题给代码）：先看身份/关系里的词，再看想法里紧挨名字的称呼。推不出就空，不默认女。"""
    txt = "%s %s" % (role or "", relation or "")
    m, f = bool(_MALE_W.search(txt)), bool(_FEMALE_W.search(txt))
    if m != f:
        return "男" if m else "女"
    if name and idea:
        for mm in re.finditer(re.escape(str(name)), str(idea)):
            near = str(idea)[max(0, mm.start() - 6):mm.end() + 6]
            m2, f2 = bool(_MALE_W.search(near)), bool(_FEMALE_W.search(near))
            if m2 != f2:
                return "男" if m2 else "女"
    return ""


def _s(x):
    return str(x if x is not None else "").strip()


def _int(x, default=0):
    try:
        return int(float(x))
    except Exception:
        return default


_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:[-~～—至到]|到)\s*(\d+(?:\.\d+)?)")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def parse_range(v, default=(0, 0)):
    """「40-60」「40～60秒」「约 50 秒」「[40, 60]」「{lo,hi}」→ (lo, hi) 整数秒。解析不出 → default。"""
    if isinstance(v, dict):
        lo, hi = _int(v.get("lo") or v.get("min"), 0), _int(v.get("hi") or v.get("max"), 0)
        if lo or hi:
            return (min(lo, hi) if lo and hi else (lo or hi), max(lo, hi) if lo and hi else (lo or hi))
        v = v.get("seconds") or v.get("text") or ""
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        lo, hi = _int(v[0], 0), _int(v[1], 0)
        if lo or hi:
            return (min(lo, hi), max(lo, hi))
    if isinstance(v, (int, float)):
        n = int(v)
        return (n, n) if n > 0 else default
    t = _s(v)
    if not t:
        return default
    m = _RANGE_RE.search(t)
    if m:
        lo, hi = _int(m.group(1)), _int(m.group(2))
        return (min(lo, hi), max(lo, hi))
    nums = _NUM_RE.findall(t)
    if nums:
        n = _int(nums[0])
        return (n, n)
    return default


def mid(rng):
    lo, hi = rng if isinstance(rng, (list, tuple)) and len(rng) == 2 else (0, 0)
    return int(round((lo + hi) / 2.0)) if (lo or hi) else 0


def fmt_range(rng):
    lo, hi = rng
    if not lo and not hi:
        return ""
    return "%d 秒" % lo if lo == hi else "%d～%d 秒" % (lo, hi)


def new_uid():
    return "E" + uuid.uuid4().hex[:8]


def episode_sig(ep):
    """这一话内容的指纹：改了这些，正文/剧本就要更新。"""
    keys = ("title", "one_line", "purpose", "scenes", "start", "landing", "change", "events", "beats", "cast", "place", "link_in", "link_out",
            "focus", "pace")                                                # P300：节奏和表现重点变了正文也要更新
    return _hash({k: ep.get(k) for k in keys})


def _hash(obj):
    import hashlib
    return hashlib.md5(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]


# ─────────────────────────── 规整 ───────────────────────────

def _beats(v):
    out = []
    if isinstance(v, dict):
        v = [dict(text=x, weight=k) for k, x in v.items()]
    for b in (v if isinstance(v, list) else []):
        if isinstance(b, str):
            b = {"text": b}
        if not isinstance(b, dict):
            continue
        text = _s(b.get("text") or b.get("content") or b.get("beat"))
        if not text:
            continue
        w = _s(b.get("weight") or b.get("pace") or "")
        w = WEIGHT_FOCUS if ("重" in w or "focus" in w.lower() or "详" in w) else (WEIGHT_PASS if ("带" in w or "过" in w or "pass" in w.lower() or "略" in w) else WEIGHT_FOCUS)
        out.append({"text": text, "weight": w, "seconds": list(parse_range(b.get("seconds") if b.get("seconds") is not None else b.get("time"), (0, 0)))})
    return out[:BEAT_MAX]


def _scenes(v, fallback=""):
    if isinstance(v, str):
        v = re.split(r"[、，,；;→>\n]", v)
    out = []
    for x in (v if isinstance(v, list) else []):
        x = _s(x)
        if x and x not in out:
            out.append(x)
    if not out and fallback:
        out = [x for x in re.split(r"[、，,；;→>\n]", _s(fallback)) if _s(x)]
        out = list(dict.fromkeys(_s(x) for x in out))
    return out


def _cast(v):
    if isinstance(v, str):
        v = re.split(r"[、，,；;\n]", v)
    return list(dict.fromkeys(_s(x) for x in (v if isinstance(v, list) else []) if _s(x)))


def normalize_episode(raw, no):
    """一话的规整：缺的栏补空、区间解析、段落规整。source 记谁写的（模型/用户）。"""
    raw = raw if isinstance(raw, dict) else {}
    ep = {
        "uid": _s(raw.get("uid")) or new_uid(),
        "no": int(no),
        "title": _s(raw.get("title")) or ("第%d话" % no),
        "one_line": _s(raw.get("one_line") or raw.get("summary") or raw.get("brief")),
        "purpose": _s(raw.get("purpose")),
        "pace": norm_pace(raw.get("pace")),
        "pace_why": _s(raw.get("pace_why")),
        "focus": [_s(x) for x in (raw.get("focus") or []) if _s(x)] if isinstance(raw.get("focus"), list) else ([_s(raw.get("focus"))] if _s(raw.get("focus")) else []),
        "cast": _cast(raw.get("cast")),
        "place": _s(raw.get("place")),
        "scenes": _scenes(raw.get("scenes"), fallback=raw.get("place")),
        "start": _s(raw.get("start")),
        "goal": _s(raw.get("goal")),
        "resistance": _s(raw.get("resistance")),
        "events": [_s(x) for x in (raw.get("events") or []) if _s(x)] if isinstance(raw.get("events"), list) else [],
        "beats": _beats(raw.get("beats")),
        "duration": {"seconds": [0, 0], "where": ""},
        "landing": _s(raw.get("landing")),
        "change": _s(raw.get("change")),
        "link_in": _s(raw.get("link_in")),
        "link_out": _s(raw.get("link_out")),
        "geometry": _s(raw.get("geometry")),
        "covers": [_int(x) for x in (raw.get("covers") or []) if _int(x)] if isinstance(raw.get("covers"), list) else [],
        "source": _s(raw.get("source")) or "model",
    }
    d = raw.get("duration")
    if isinstance(d, dict):
        ep["duration"] = {"seconds": list(parse_range(d.get("seconds") if d.get("seconds") is not None else d, (0, 0))),
                          "where": _s(d.get("where"))}
    else:
        ep["duration"] = {"seconds": list(parse_range(d, (0, 0))), "where": ""}
    # 时长没给就按段落加总；段落也没给就留 0（校验会报）
    if not any(ep["duration"]["seconds"]) and ep["beats"]:
        lo = sum(b["seconds"][0] for b in ep["beats"])
        hi = sum(b["seconds"][1] for b in ep["beats"])
        ep["duration"]["seconds"] = [lo, hi]
    if not ep["events"] and ep["beats"]:
        ep["events"] = [b["text"] for b in ep["beats"]]
    ep["sig"] = episode_sig(ep)
    return ep


def normalize(plan):
    p = plan if isinstance(plan, dict) else {}
    out = {
        "schema": SCHEMA,
        "status": _s(p.get("status")) or STATUS_DRAFT,
        "version": _int(p.get("version"), 0),
        "idea": _s(p.get("idea")),
        "prefs": {k: _s((p.get("prefs") or {}).get(k)) for k in ("style", "total_sec", "pace")} if isinstance(p.get("prefs"), dict) else {"style": "", "total_sec": "", "pace": ""},
        "synopsis": _s(p.get("synopsis")),
        "tone": _s(p.get("tone")),
        "reason": _s(p.get("reason")),                                     # P300：建议分几话的理由
        "presentation": _s(p.get("presentation")),                         # P316：整体怎么展示（用户写的，写作/修改都带着）
        "people": [],
        "issues": [x for x in (p.get("issues") or []) if isinstance(x, dict)],
        "episodes": [],
        "total": {"seconds": list(parse_range(p.get("total") if p.get("total") is not None else p.get("total_seconds"), (0, 0)))},
        "history": [x for x in (p.get("history") or []) if isinstance(x, dict)],
        "story_edited": [_s(x) for x in (p.get("story_edited") or []) if _s(x)],   # P316：用户手改过整部级哪几栏
        "chain": [x for x in (p.get("chain") or []) if isinstance(x, dict)],        # B 方案的因果链（有就带着，给人看）
        "confirmed_at": p.get("confirmed_at"),
        "source_sig": _s(p.get("source_sig")),
        "warnings": [_s(x) for x in (p.get("warnings") or []) if _s(x)],
    }
    seen = set()
    for x in (p.get("people") or []):
        if isinstance(x, str):
            x = {"name": x}
        if not isinstance(x, dict) or not _s(x.get("name")) or _s(x.get("name")) in seen:
            continue
        seen.add(_s(x.get("name")))
        _sex = norm_sex(x.get("sex")) or guess_sex(_s(x.get("name")), _s(x.get("role")), _s(x.get("relation")), _s(p.get("idea")))
        out["people"].append({"name": _s(x.get("name")), "role": _s(x.get("role")), "relation": _s(x.get("relation")),
                              "unnamed": bool(x.get("unnamed")), "look": _s(x.get("look")), "personality": _s(x.get("personality")),
                              "voice": _s(x.get("voice")),                                      # P317：声线一句，建卡带进去
                              "sex": _sex, "age": norm_age(x.get("age"), _s(x.get("role")))})   # P316/P317：性别进表；年龄规整成数字
    eps = p.get("episodes")
    if not isinstance(eps, list):
        eps = p.get("rows") if isinstance(p.get("rows"), list) else []
    for i, raw in enumerate(eps, 1):
        if isinstance(raw, dict) and (_s(raw.get("one_line")) or raw.get("events") or raw.get("beats")):
            out["episodes"].append(normalize_episode(raw, i))
    for i, ep in enumerate(out["episodes"], 1):
        ep["no"] = i
    # 整部时长一律按各话加总（模型给的顶层数字常和各话之和对不上：实测 180～240 vs 195～268）
    if out["episodes"] and any(any(e["duration"]["seconds"]) for e in out["episodes"]):
        out["total"]["seconds"] = [sum(e["duration"]["seconds"][0] for e in out["episodes"]),
                                   sum(e["duration"]["seconds"][1] for e in out["episodes"])]
    # 承接：没写的按上一话落点 / 下一话开始推导（确定的推导，不是编）
    for i, ep in enumerate(out["episodes"]):
        if not ep["start"] and i > 0:
            ep["start"] = out["episodes"][i - 1]["landing"]
        if not ep["link_in"]:
            ep["link_in"] = "故事从这里开始" if i == 0 else ("接上一话：" + out["episodes"][i - 1]["landing"][:40] if out["episodes"][i - 1]["landing"] else "")
        if not ep["link_out"]:
            ep["link_out"] = "故事到此结束" if i == len(out["episodes"]) - 1 else ("下一话：" + out["episodes"][i + 1]["one_line"][:40] if out["episodes"][i + 1]["one_line"] else "")
        ep["sig"] = episode_sig(ep)
    return out


# ─────────────────────────── 校验 ───────────────────────────

def validate(plan):
    """硬错误：抛 ValueError。下游写不出来的才算硬：没话、话没内容、没落点、人物表空。"""
    p = plan if isinstance(plan, dict) else {}
    eps = p.get("episodes") or []
    if not eps:
        raise ValueError("一话都没有")
    if not p.get("people"):
        raise ValueError("人物表为空")
    for ep in eps:
        if not _s(ep.get("one_line")):
            raise ValueError("第%d话没写发生什么" % ep.get("no"))
        if not _s(ep.get("landing")):
            raise ValueError("第%d话没写结束状态" % ep.get("no"))
        if not (ep.get("events") or ep.get("beats")):
            raise ValueError("第%d话没有事件表也没有段落" % ep.get("no"))
    return True


def problems(plan):
    """软问题（只报不拦）：时长离谱、段落秒数和话时长对不上、承接断了、话与话重复、结局提前。"""
    out = []
    eps = (plan or {}).get("episodes") or []
    prefs = (plan or {}).get("prefs") or {}
    for i, ep in enumerate(eps):
        # P300：规划阶段不设时长。旧规划带着秒数的只在明显离谱时提一句；新规划没有秒数不报。
        lo, hi = ep["duration"]["seconds"]
        if (lo or hi) and (hi < EP_MIN_SEC or lo > EP_MAX_SEC):
            out.append("第%d话预计 %s，超出一话的合理范围（%d～%d 秒）" % (ep["no"], fmt_range((lo, hi)), EP_MIN_SEC, EP_MAX_SEC))
        if not ep["events"] and not ep["beats"]:
            out.append("第%d话没写主要过程（不知道中间怎样一步步发生）" % ep["no"])
        if not ep.get("focus") and not any(b["weight"] == WEIGHT_FOCUS for b in ep["beats"]):
            out.append("第%d话没写表现重点（哪里值得详细看）" % ep["no"])
        if i > 0:
            prev = eps[i - 1]
            # "接不接得上"字面判不出（意思连贯、措辞不同的居多，实测三处全误报）——程序只报能确定的：开始状态空着
            if not _s(ep["start"]):
                out.append("第%d话没写开始状态（接不上第%d话）" % (ep["no"], prev["no"]))
            if _overlap(ep["one_line"], prev["one_line"]) > 0.6:
                out.append("第%d话和第%d话讲的几乎是同一件事" % (ep["no"], prev["no"]))
    last = eps[-1] if eps else None
    for ep in eps[:-1]:
        if last and _s(last["landing"]) and _overlap(last["landing"], ep["landing"]) > 0.7:
            out.append("第%d话的结束已经是整部结局，后面几话没事可讲" % ep["no"])
    out.extend(reveal_problems(plan))                                    # P296：规划自己泄底
    _tail = tail_only_episode(plan)                                       # P318：最后一话只有收尾动作
    if _tail:
        out.append("第%d话只有收尾动作（回头、牵手、相视而笑），撑不起一话的发展——建议并入第%d话的结尾（下面「并入上一话」一键合并）" % (_tail, _tail - 1))
    # P307：同一地点、同一段时间的小故事被拆成三四话（候车室一小时被分成四话）——提醒可以合并，不拦
    if len(eps) >= 3:
        _places = set()
        for ep in eps:
            _pl = re.split(r"->|→|—>|、|，|,", _s(ep.get("place")) or (_s((ep.get("scenes") or [""])[0])))[0]
            _pl = re.sub(r"[（(].*", "", _pl).strip()
            if _pl:
                _places.add(_pl[:8])
        _avg_ev = sum(len(ep.get("events") or []) for ep in eps) / float(len(eps))
        if len(_places) <= 2 and _avg_ev <= 6:
            out.append("这个故事集中在 %d 个地点、一段时间里，分成 %d 话偏碎（每话只有几件事）——可以在下面说「合并成两话」或「合成一话」" % (max(1, len(_places)), len(eps)))
    return out


def _bigrams(s):
    s = re.sub(r"[^\w一-龥]", "", _s(s))
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _overlap(a, b):
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / float(min(len(A), len(B)))


# ─────────────────────────── 视图 ───────────────────────────

def summary(plan):
    """页面顶部那一行 + 话次列表（默认只显示简短剧情和时长，展开看场景与节奏）。"""
    p = plan or {}
    eps = p.get("episodes") or []
    # 总时长每次按各话现算（存盘的顶层数字可能是旧的）
    tot = ([sum((e.get("duration") or {}).get("seconds", [0, 0])[0] for e in eps),
            sum((e.get("duration") or {}).get("seconds", [0, 0])[1] for e in eps)]
           if eps and any(any((e.get("duration") or {}).get("seconds") or [0, 0]) for e in eps)
           else (p.get("total", {}).get("seconds") or [0, 0]))
    return {
        "status": p.get("status"), "version": p.get("version"),
        "what": _s(p.get("synopsis"))[:160] + ("…" if len(_s(p.get("synopsis"))) > 160 else ""),
        "people": ["%s（%s）" % (x["name"], "、".join(y for y in (x.get("sex"), x.get("age"), x["role"]) if y)) if (x.get("role") or x.get("sex")) else x["name"] for x in p.get("people") or []],
        "people_rows": [{"name": x["name"], "sex": x.get("sex", ""), "age": x.get("age", ""), "role": x.get("role", ""), "relation": x.get("relation", "")} for x in p.get("people") or []],
        "synopsis": _s(p.get("synopsis")),
        "presentation": _s(p.get("presentation")),
        "n": len(eps),
        "reason": p.get("reason") or "",
        "total": fmt_range(tuple(tot)) if any(tot) else "",                  # P300：新规划没有秒数，页面不再突出
        "episodes": [{
            "no": e["no"], "uid": e["uid"], "title": e["title"], "one_line": e["one_line"],
            "duration": fmt_range(tuple(e["duration"]["seconds"])) if any(e["duration"]["seconds"]) else "", "purpose": e["purpose"],
            "scenes": e["scenes"], "beats": e["beats"], "where": e["duration"]["where"],
            "events": e["events"], "focus": e.get("focus") or [], "pace_why": e.get("pace_why", ""),
            "start": e["start"], "landing": e["landing"], "link_in": e["link_in"], "link_out": e["link_out"],
            "cast": e["cast"], "pace": norm_pace(e["pace"]), "pace_meaning": PACE_MEANING.get(norm_pace(e["pace"]), ""), "source": e.get("source"),
        } for e in eps],
        "problems": problems(p),
        "facts": fact_checks(p),                                   # P292：想法核对（待审核）
        "warnings": p.get("warnings") or [],
    }


# ─────────────────────────── 起草（两次模型调用，复用 universal_writer.plan）───────────────────────────

def draft(idea, settings=None, prefs=None, log=None, model=None):
    """一段想法 → 整部规划草稿。走 universal_writer.plan（完整梗概 + 分话结构）。
    prefs：{style, total_sec, pace}，空 = 由 AI 建议。返回 normalize 过的 plan（status=draft）。"""
    from . import universal_writer as uw
    idea = _s(idea)
    if not idea:
        raise ValueError("先写一段故事想法")
    prefs = {k: _s((prefs or {}).get(k)) for k in ("style", "notes", "total_sec", "pace")}   # total_sec/pace 已停用（P300），留空
    s = dict(settings or {})
    s["one_line"] = idea
    notes = []
    if prefs["notes"]:
        notes.append("用户的补充要求：" + prefs["notes"])
    if prefs["style"]:
        notes.append("画风：%s" % prefs["style"])
    res = uw.plan(idea, s, n_eps=0, notes="；".join(notes), log=log, model=model)
    if res.get("hard") and not res.get("rows"):
        err = RuntimeError("；".join(res["hard"])[:300])
        err.partial = {"synopsis": res.get("draft") or "", "structure_draft": res.get("structure_draft") or "",
                       "calls": res.get("calls")}                 # 失败稿也要留（用户定）
        raise err
    raw_rows = []
    chain = []
    try:
        obj = uw.parse_object(res.get("structure_draft") or "")
        raw_rows = obj.get("rows") if isinstance(obj.get("rows"), list) else []
        total_seconds = obj.get("total_seconds")
        chain = obj.get("chain") if isinstance(obj.get("chain"), list) else []
    except Exception:
        total_seconds = None
    # 规整过的行（normalize_rows 只留标准栏）和模型原始行（带新栏）按序合并
    rows = []
    for i, r in enumerate(res.get("rows") or []):
        raw = raw_rows[i] if i < len(raw_rows) and isinstance(raw_rows[i], dict) else {}
        merged = dict(raw)
        merged.update({k: r.get(k) for k in ("one_line", "cast", "place", "start", "goal", "resistance", "landing", "change", "geometry") if r.get(k)})
        if not _s(raw.get("pace")):
            merged["pace"] = r.get("pace")
        merged["events"] = r.get("events") or raw.get("events") or []
        rows.append(merged)
    try:
        _reason = _s(obj.get("reason"))
    except Exception:
        _reason = ""
    plan = normalize({
        "status": STATUS_DRAFT, "version": 0, "idea": idea, "prefs": prefs, "reason": _reason,
        "synopsis": res.get("draft") or (res.get("design") or {}).get("synopsis") or "",
        "tone": res.get("tone"), "people": res.get("people") or [],
        "issues": res.get("model_notes") or [], "rows": rows, "total_seconds": total_seconds, "chain": chain,
        "warnings": [w for w in (res.get("warnings") or []) if "已完成" not in w] + list(res.get("hard") or []),
        "source_sig": res.get("source_sig"),
    })
    plan["calls"] = int(res.get("calls") or 0)
    return plan


# ─────────────────────────── 修改 ───────────────────────────

def edit(plan, no, fields):
    """直接编辑某一话（零模型）：只改传进来的栏；改了内容的话 source=user、重算指纹；时长可改。"""
    p = copy.deepcopy(plan)
    ep = next((e for e in p.get("episodes") or [] if int(e.get("no")) == int(no)), None)
    if ep is None:
        raise ValueError("没有第 %s 话" % no)
    allowed = ("title", "one_line", "purpose", "start", "landing", "change", "link_in", "link_out", "place", "goal", "resistance", "pace_why")
    _old_one = _s(ep.get("one_line"))
    _old_landing = _s(ep.get("landing"))
    _old_cast = list(ep.get("cast") or [])
    _old_purpose = _s(ep.get("purpose"))
    for k in allowed:
        if k in fields:
            ep[k] = _s(fields[k])
    if "events" in fields and isinstance(fields["events"], str):
        fields = dict(fields, events=[x for x in str(fields["events"]).splitlines()])          # P319：表单一行一条
    _events_given = False
    if "events" in fields and isinstance(fields["events"], list):
        _ev = [re.sub(r"^\s*\d+[.、．)]\s*", "", _s(x)).strip() for x in fields["events"] if _s(x)]
        _ev = [x for x in _ev if x]
        if _ev and _ev != list(ep.get("events") or []):
            ep["events"] = _ev
            _events_given = True
        elif not _ev and (not ep.get("events") or "one_line" not in fields):
            ep["events"] = one_line_events(_s(fields.get("one_line") or ep.get("one_line"))) or list(ep.get("events") or [])   # 清空了过程 → 按讲什么重切，不留空
    if "scenes" in fields:
        ep["scenes"] = _scenes(fields["scenes"])
    if "cast" in fields:
        ep["cast"] = _cast(fields["cast"])
    # P318：只改了「这一话讲什么」、没单独给主要过程 → 旧的 events/landing 不能留着一起发（项目185：手改说先逛市场再遇见，旧过程直接从相遇开始）
    if "one_line" in fields and _s(fields["one_line"]) != _old_one and not _events_given:
        ep["events"] = one_line_events(_s(fields["one_line"])) or list(ep.get("events") or [])
        if ep["events"] and ("landing" not in fields or _s(fields.get("landing")) == _old_landing):
            ep["landing"] = ep["events"][-1]                               # 页面会把没动的结束状态原样传上来，那也算"没单独给"
        ep["change"] = ""                                                  # 旧的"变化"讲的是旧过程
        if "purpose" not in fields or _s(fields.get("purpose")) == _old_purpose:
            ep["purpose"] = ""                                             # P319：旧的"本话作用"也是讲旧过程的（表单原样传回也算没改）
        if "cast" not in fields or _cast(fields.get("cast")) == list(_old_cast):
            _names = [x["name"] for x in (p.get("people") or []) if _s(x.get("name")) and x["name"] in _s(fields["one_line"])]
            if _names:
                ep["cast"] = _names                                        # P319：在场按新文字里点到的人重算
        ep["events_from"] = "one_line"
    if "focus" in fields:
        fl = fields["focus"] if isinstance(fields["focus"], list) else str(fields["focus"] or "").splitlines()
        ep["focus"] = [_s(x).lstrip("·•-").strip() for x in fl if _s(x).lstrip("·•-").strip()]
    if "beats" in fields:
        ep["beats"] = _beats(fields["beats"])
    if "duration" in fields:
        rng = parse_range(fields["duration"], (0, 0))
        if any(rng):
            ep["duration"]["seconds"] = list(rng)
    if "pace" in fields and norm_pace(fields["pace"], "") in PACES:
        if norm_pace(fields["pace"]) != ep.get("pace") and "pace_why" not in fields:
            ep["pace_why"] = "你改的"                                    # 模型写的理由是给原节奏的，换了节奏就不再挂着
        ep["pace"] = norm_pace(fields["pace"])
    ep["source"] = "user"
    ep["sig"] = episode_sig(ep)
    p["total"]["seconds"] = [sum(e["duration"]["seconds"][0] for e in p["episodes"]), sum(e["duration"]["seconds"][1] for e in p["episodes"])]
    return normalize(p)


def edit_story(plan, fields):
    """P316：整部级两栏直接编辑（零模型）：synopsis（整个故事怎么发展）、presentation（整体怎么展示）。
    改了梗概只是换文字，各话指纹不变（各话内容没动）；写作输入用的是这份新梗概。"""
    p = copy.deepcopy(plan)
    changed = []
    for k in ("synopsis", "presentation"):
        if k in (fields or {}) and _s(fields[k]) != _s(p.get(k)):
            p[k] = _s(fields[k])
            changed.append(k)
    if changed:
        p["story_edited"] = list(dict.fromkeys(list(p.get("story_edited") or []) + changed))
    return normalize(p), changed


def one_line_events(text):
    """「这一话讲什么」按句切成主要过程（P318，零模型）：句号/问号/叹号/分号断句，去掉太短的碎片，最多 10 条。"""
    parts = [x.strip() for x in re.split(r"(?<=[。！？；!?;])", str(text or "")) if x.strip()]
    out = []
    for p in parts:
        p = p.strip("。！？；!?; ")
        if len(p) >= 6:
            out.append(p + "。")
    if not out and _s(text):
        out = [_s(text)]
    return out[:10]


def merge_into_prev(plan, no):
    """P318：把第 no 话并进第 no-1 话（零模型）：过程接在后面、结束状态取后一话的、承接取后一话的 link_out、场景/人物合并。返回新规划。"""
    p = copy.deepcopy(normalize(plan))
    eps = p["episodes"]
    i = next((k for k, e in enumerate(eps) if int(e.get("no") or 0) == int(no)), -1)
    if i <= 0:
        raise ValueError("第 %s 话没有上一话可并" % no)
    a, b = eps[i - 1], eps[i]
    a["one_line"] = (_s(a["one_line"]).rstrip("。") + "。" + _s(b["one_line"])).strip()
    a["events"] = list(a.get("events") or []) + list(b.get("events") or [])
    a["focus"] = list(dict.fromkeys(list(a.get("focus") or []) + list(b.get("focus") or [])))
    a["landing"] = _s(b.get("landing")) or a.get("landing")
    a["link_out"] = _s(b.get("link_out")) or a.get("link_out")
    a["change"] = _s(b.get("change")) or a.get("change")
    a["scenes"] = list(dict.fromkeys(list(a.get("scenes") or []) + list(b.get("scenes") or [])))
    a["cast"] = list(dict.fromkeys(list(a.get("cast") or []) + list(b.get("cast") or [])))
    a["source"] = "user"
    a["sig"] = ""
    p.setdefault("retired_uids", []).append(b.get("uid"))
    del eps[i]
    p["reason"] = ""
    q = normalize(p)
    q["history"] = list(q.get("history") or []) + [{"at": time.time(), "instruction": "把第 %d 话并入第 %d 话（只有收尾动作）" % (int(no), int(no) - 1),
                                                    "note": "零模型合并：过程接在后面，结束状态取后一话的", "applied": True,
                                                    "before": [{"uid": e["uid"], "no": e["no"], "title": e["title"], "sig": e["sig"]} for e in normalize(plan)["episodes"]]}]
    return q


_WRAP_W = re.compile(r"回头|回望|牵手|勾住|相扣|缠绕|相视|一笑|微笑|定格|依偎|拥抱|抱住|望着|看着|落幕|结束|尾声|收尾|落日|夕阳|晚风|烟火|安宁|幸福|圆满|余晖")


def tail_only_episode(plan):
    """最后一话是不是只有收尾动作（P318）：事件 ≤2 或一句话不到 90 字，且和上一话同地点、没有新人物，且事件词多是回头/牵手/相视/定格。返回话号或 0。"""
    eps = (plan or {}).get("episodes") or []
    if len(eps) < 2:
        return 0
    a, b = eps[-2], eps[-1]
    evs = [x for x in (b.get("events") or []) if _s(x)]
    _wrap_n = sum(1 for x in evs if _WRAP_W.search(_s(x)))
    short = len(evs) <= 2 or (evs and _wrap_n / float(len(evs)) >= 0.6)      # 事件很少，或大半事件都是收尾动作
    if not short:
        return 0
    def _toks(e):
        raw = re.split(r"->|→|—>|、|，|,|；|;", _s(e.get("place"))) + list(e.get("scenes") or [])
        return {re.sub(r"[（(].*", "", _s(x)).strip() for x in raw if _s(x).strip()}
    _ta, _tb = _toks(a), _toks(b)
    same_place = (not _tb) or (not _ta) or bool(_ta & _tb) or any((x in y or y in x) for x in _tb for y in _ta)
    new_cast = [c for c in (b.get("cast") or []) if c not in (a.get("cast") or [])]
    wrap = len(_WRAP_W.findall(_s(b.get("one_line")) + "".join(evs))) >= 2
    if same_place and not new_cast and wrap:
        return int(b.get("no") or 0)
    return 0


def revise(plan, instruction, settings=None, model=None, only_no=0):
    """聊天式修改：一句话 → 模型改整部规划（可合并/拆分/调顺序/改某一话/改结尾），返回 (新规划, 说明)。
    一次模型调用。改动之后由 impact() 算影响，页面显示给用户确认。
    only_no>0（P300 单话修改）：要求只改这一话，其他话只在承接接不上时改开始条件。"""
    from . import universal_writer as uw
    instruction = _s(instruction)
    if not instruction:
        raise ValueError("先说要改什么")
    if int(only_no or 0) > 0:
        instruction = "只改第 %d 话：%s（其他话保持原样；第 %d 话的结束结果变了才改后面话的开始条件，并写进 note）" % (int(only_no), instruction, int(only_no))
    p = normalize(plan)
    task = {
        "修改要求": instruction,
        "完整故事梗概": p["synopsis"],
        "人物表": _people_for_model(p["people"]),
        "现在的分话（rows）": [_row_for_model(e) for e in p["episodes"]],
    }
    if _s(p.get("presentation")):
        task["整体怎么展示（用户写的，改出来的话要照这个）"] = p["presentation"]
    raw = uw.call(_ins("整部规划修改"), uw.dump(task), 7000, 0.3, model, task="universal_split")
    obj = uw.parse_object(raw)
    rows = obj.get("rows")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("模型没有给出修改后的分话")
    # P316：单话修改，目标话一个字没变（项目183：要求第 2 话加洗澡互动，模型 note 说改了、rows 原样）→ 再要一次
    _target = next((e for e in p["episodes"] if int(e.get("no") or 0) == int(only_no or 0)), None) if int(only_no or 0) > 0 else None
    if _target is not None and not _row_changed(rows, _target):
        task["修改要求"] = instruction + "（注意：上一次你把第 %d 话原样交回来了，没有改。这一次第 %d 话的 one_line/events/focus/landing 必须按要求重写，和原来不同）" % (int(only_no), int(only_no))
        try:
            raw2 = uw.call(_ins("整部规划修改"), uw.dump(task), 7000, 0.3, model, task="universal_split")
            obj2 = uw.parse_object(raw2)
            rows2 = obj2.get("rows")
            if isinstance(rows2, list) and rows2 and _row_changed(rows2, _target):
                obj, rows = obj2, rows2
        except Exception:
            pass
    # P308：要求里写明了话数（合并成两话/分成三话/合成一话），模型给的数不对就再要一次（最多一次）；还不对就在说明里写明
    _want_n = _wanted_count(instruction)
    if _want_n and len([r for r in rows if isinstance(r, dict)]) != _want_n:
        task["修改要求"] = instruction + "（注意：改完必须正好 %d 话，rows 里只能有 %d 项；上一次你给了 %d 话，不对）" % (_want_n, _want_n, len(rows))
        raw2 = uw.call(_ins("整部规划修改"), uw.dump(task), 7000, 0.3, model, task="universal_split")
        obj2 = uw.parse_object(raw2)
        rows2 = obj2.get("rows")
        if isinstance(rows2, list) and len([r for r in rows2 if isinstance(r, dict)]) == _want_n:
            obj, rows = obj2, rows2
    note = _s(obj.get("note") or obj.get("change_note") or obj.get("说明"))
    _not_applied = bool(_target is not None and not _row_changed(rows, _target))
    if _not_applied:
        note = ("模型没有改第 %d 话（要了两次，内容和原来一样）。可以换个说法再试，或点「编辑这一话」直接改。" % int(only_no)) + (("模型自己的说明：" + note) if note else "")
    if _want_n and len([r for r in rows if isinstance(r, dict)]) != _want_n:
        note = ("你要 %d 话，模型给了 %d 话——可以再说一次，或直接说「第几话和第几话合并」。" % (_want_n, len(rows))) + note
    # 保住 uid：模型按 uid 回传就沿用；没回传的按位置/一句话相似度对旧话
    old_by_uid = {e["uid"]: e for e in p["episodes"]}
    new_eps = []
    used = set()
    _same_count = len([r for r in rows if isinstance(r, dict)]) == len(p["episodes"])
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        uid = _s(r.get("uid"))
        if uid not in old_by_uid or uid in used:
            uid = _match_uid(r, p["episodes"], used)
        if not uid and _same_count and i < len(p["episodes"]) and p["episodes"][i]["uid"] not in used:
            uid = p["episodes"][i]["uid"]                                  # P303：话数没变、这一话被重写 → 按位置认同一话（旧稿标过期而不是退休）
        if uid:
            used.add(uid)
        old_e = old_by_uid.get(uid) or {}
        base = dict(old_e)
        _upd = {k: v for k, v in r.items() if v not in (None, "", [])}
        # P316：只改第 N 话时，别的话只许动承接两栏（start/link_in）；用户手改过（source=user）的话，模型改的内容一律不收
        if _target is not None and old_e and int(old_e.get("no") or 0) != int(only_no):
            _upd = {k: v for k, v in _upd.items() if k in ("start", "link_in")}
        elif old_e.get("source") == "user" and _target is None:
            _upd = {k: v for k, v in _upd.items() if k in ("start", "link_in")}
        base.update(_upd)
        base["uid"] = uid or new_uid()
        _same = bool(old_e) and episode_sig(normalize_episode(base, int(old_e.get("no") or 1))) == old_e.get("sig")
        base["source"] = old_e.get("source", "model") if _same else "model"      # 没改的话不重新盖 model 标（手改标记保住）
        new_eps.append(base)
    q = dict(p)
    q["episodes"] = new_eps
    if _s(obj.get("synopsis")):
        q["synopsis"] = _s(obj.get("synopsis"))
    q["total_seconds"] = obj.get("total_seconds")
    if len(new_eps) != len(p["episodes"]):
        q["reason"] = ""                                                    # 话数变了，原来"为什么分成这几话"的理由不再成立
    q = normalize(q)
    q["history"] = list(p.get("history") or []) + [{"at": time.time(), "instruction": instruction, "note": note, "applied": not _not_applied,
                                                    "before": [{"uid": e["uid"], "no": e["no"], "title": e["title"], "sig": e["sig"]} for e in p["episodes"]]}]
    return q, note


def _row_changed(rows, old_ep):
    """模型交回的 rows 里，这一话（按 uid，找不到按 no）的内容和原来一样吗。"""
    r = next((x for x in rows if isinstance(x, dict) and _s(x.get("uid")) == old_ep.get("uid")), None)
    if r is None:
        r = next((x for x in rows if isinstance(x, dict) and _int(x.get("no"), 0) == int(old_ep.get("no") or 0)), None)
    if r is None:
        return True                                                         # 这一话没交回来（合并/删掉了）→ 算变了
    for k in ("one_line", "events", "focus", "landing", "start", "purpose", "change", "title"):
        a, b = r.get(k), old_ep.get(k)
        if isinstance(a, list) or isinstance(b, list):
            if [_s(x) for x in (a or [])] != [_s(x) for x in (b or [])]:
                return True
        elif _s(a) and _s(a) != _s(b):
            return True
    return False


_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _wanted_count(instruction):
    """修改要求里写明的目标话数：合并成两话 / 合成一话 / 分成三话 / 改成 4 话 → 2/1/3/4；没写 → 0。"""
    m = re.search(r"(合并成|合成|并成|分成|拆成|改成|变成|做成|压成)\s*([一两二三四五六七八九十\d]+)\s*话", _s(instruction))
    if not m:
        return 0
    v = m.group(2)
    return int(v) if v.isdigit() else _CN_NUM.get(v, 0)


def _people_for_model(people):
    return [{k: v for k, v in x.items() if k in ("name", "sex", "age", "role", "relation", "look", "personality", "voice", "unnamed")} for x in (people or [])]


def _row_for_model(e):
    return {"uid": e["uid"], "no": e["no"], "title": e["title"], "one_line": e["one_line"], "purpose": e["purpose"],
            "pace": e["pace"], "pace_why": e.get("pace_why", ""), "cast": "、".join(e["cast"]), "scenes": e["scenes"], "start": e["start"], "goal": e["goal"],
            "resistance": e["resistance"], "events": e["events"], "focus": e.get("focus", []),
            "landing": e["landing"], "change": e["change"], "link_in": e["link_in"], "link_out": e["link_out"]}


def _match_uid(r, old_eps, used):
    """模型没回传 uid 时：一句话最像的那一话（重合 ≥0.5）就是它；否则是新话。"""
    best, best_v = "", 0.0
    for e in old_eps:
        if e["uid"] in used:
            continue
        v = _overlap(_s(r.get("one_line")), e["one_line"])
        if v > best_v:
            best, best_v = e["uid"], v
    return best if best_v >= 0.5 else ""


def _ins(name):
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "presets" / "instructions" / ("通用写作_%s.txt" % name)
    if p.exists():
        return p.read_text(encoding="utf-8")
    return _FALLBACK_REVISE


_FALLBACK_REVISE = """你是小说结构编辑。用户给你现在的分话（rows，每话带 uid）和一句修改要求。按要求改分话：
可以合并、拆分、调整顺序、改某一话的剧情/结尾/时长/节奏；没提到的话保持原样、保留原 uid；合并出来的新话不写 uid。
每话字段和原来一样（title/one_line/purpose/pace/cast/scenes/start/goal/resistance/events/beats/duration/landing/change/link_in/link_out）。
改完检查前后承接：每话的 start 接上一话的 landing。
只输出 JSON：{"note": "改了哪几话、为什么后面的话跟着变，两三句", "total_seconds": "区间", "rows": [...]}"""


# ─────────────────────────── 影响 ───────────────────────────

def impact(old_plan, new_plan, saga=None):
    """新旧规划对照：改了哪些话、为什么影响后面；已有正文/剧本哪些要更新、哪些退休。
    saga：story["saga"]（episodes 带 plan_uid 时才能对上旧稿）。"""
    old = normalize(old_plan) if old_plan else {"episodes": []}
    new = normalize(new_plan)
    ob = {e["uid"]: e for e in old["episodes"]}
    nb = {e["uid"]: e for e in new["episodes"]}
    changed, added, removed, moved = [], [], [], []
    details = {}
    _LABEL = {"title": "标题", "one_line": "剧情", "purpose": "作用", "scenes": "场景", "start": "开始状态", "landing": "结束状态",
              "change": "变化", "events": "主要过程", "beats": "段落/节奏", "focus": "表现重点", "pace": "节奏", "pace_why": "节奏说明", "cast": "在场", "place": "地点", "link_in": "承接", "link_out": "之后", "duration": "时长"}
    for e in new["episodes"]:
        o = ob.get(e["uid"])
        if o is None:
            added.append(e["no"])
        else:
            if o["sig"] != e["sig"] or o["duration"] != e["duration"]:
                changed.append(e["no"])
                details[str(e["no"])] = [_LABEL[k] for k in _LABEL if o.get(k) != e.get(k)]
            if o["no"] != e["no"]:
                moved.append((o["no"], e["no"]))
    for e in old["episodes"]:
        if e["uid"] not in nb:
            removed.append(e["no"])
    reasons = []
    # P303：改动后原来的事件在整份新方案里都找不到了 → 明说（实测单话修改把"去修车铺送照片"整段弄丢，页面上看不出来）
    _new_text = "".join(_ep_plan_text(e) for e in new["episodes"])
    lost = []
    for o in old["episodes"]:
        n_e = nb.get(o["uid"])
        if n_e is not None and o["sig"] == n_e["sig"]:
            continue
        for ev in (o.get("events") or [])[:12]:
            kws = _key_words(ev, 4)
            if kws and _hit_ratio(kws, _new_text) < 0.34:
                lost.append((o["no"], ev))
    for no, ev in lost[:4]:
        reasons.append("原第%d话的「%s」在新方案里找不到了——请看是不是被改丢了" % (no, ev[:30]))
    first = min(changed + added + [x[1] for x in moved] + [10 ** 6]) if (changed or added or moved) else 0
    if first and first < len(new["episodes"]):
        reasons.append("第%d话变了，它之后的话开始状态都跟着变（第%d话起）" % (first, first + 1))
    for k in removed:
        reasons.append("原第%d话被合并或删除，它的旧稿会退休保留，不挂到别的话上" % k)
    # 已有产物
    stale, retire, keep = [], [], []
    for se in ((saga or {}).get("episodes") or []):
        uid = _s(se.get("plan_uid"))
        has = bool(_s(se.get("prose")) or _s(se.get("pictures")) or _s(se.get("body")))
        if not has:
            continue
        if uid and uid in nb:
            if _s(se.get("plan_sig")) and se.get("plan_sig") != nb[uid]["sig"]:
                stale.append(nb[uid]["no"])
            else:
                keep.append(nb[uid]["no"])
        elif uid:
            retire.append(int(se.get("no") or 0))
        else:
            # 旧项目：没 uid 的按话号对——话号还在就当同一话，内容变了标更新
            no = int(se.get("no") or 0)
            ne = next((x for x in new["episodes"] if x["no"] == no), None)
            oe = next((x for x in old["episodes"] if x["no"] == no), None)
            if ne is None:
                retire.append(no)
            elif oe is None or oe["sig"] != ne["sig"]:
                stale.append(no)
            else:
                keep.append(no)
    return {"changed": changed, "added": added, "removed": removed, "moved": moved, "reasons": reasons,
            "details": details,
            "stale": sorted(set(stale)), "retire": sorted(set(retire)), "keep": sorted(set(keep))}


# ─────────────────────────── 确认落盘 ───────────────────────────

def to_rows(plan):
    """整部规划 → universal_writer 认的 plan_rows（标准栏 + 附加栏）。"""
    rows = []
    for e in plan["episodes"]:
        rows.append({
            "no": e["no"], "one_line": e["one_line"], "pace": e["pace"], "cast": "、".join(e["cast"]),
            "place": "、".join(e["scenes"]) or e["place"], "start": e["start"], "goal": e["goal"],
            "resistance": e["resistance"], "events": list(e["events"]), "landing": e["landing"],
            "change": e["change"], "geometry": e["geometry"],
            # 附加栏（normalize_rows 原样带着，写作层按需读）
            "uid": e["uid"], "title": e["title"], "purpose": e["purpose"], "scenes": list(e["scenes"]),
            "beats": copy.deepcopy(e["beats"]), "duration": copy.deepcopy(e["duration"]),
            "link_in": e["link_in"], "link_out": e["link_out"],
            "focus": list(e.get("focus") or []), "pace_why": e.get("pace_why", ""),
        })
    return rows


def confirm(settings, plan, saga=None):
    """确认整部规划：状态 confirmed、版本 +1，写进 plan_rows / story_design / plan_people（写作链读这些）；
    给已有旧稿按 uid 标过期（plan_stale）或退休（retired_episodes）。返回 (plan, impact)。
    settings / saga 原地改；调用方负责 save_story。"""
    from . import universal_writer as uw
    p = normalize(plan)
    validate(p)
    old = settings.get("story_plan") if isinstance(settings.get("story_plan"), dict) else None
    imp = impact(old, p, saga) if old else impact(None, p, saga)
    if not _s(settings.get("one_line")):
        settings["one_line"] = p["idea"]
    design = {"schema": 1, "synopsis": p["synopsis"],
              "people": [{"name": x["name"], "role": x["role"], "relation": x["relation"], "unnamed": x["unnamed"],
                          "sex": x.get("sex", ""), "age": x.get("age", "")} for x in p["people"]],         # P316：性别年龄进统一人物表
              "notes": "；".join(x for x in ("基调：" + p["tone"] if p["tone"] else "",
                                            ("整体怎么展示（用户写的）：" + p["presentation"]) if _s(p.get("presentation")) else "",
                                            "整部预计 " + fmt_range(tuple(p["total"]["seconds"])) if any(p["total"]["seconds"]) else "") if x)}
    if settings.get("plan_rows"):
        settings.setdefault("plan_history", []).append({"at": time.time(), "rows": settings["plan_rows"],
                                                         "design": settings.get("story_design"), "version": settings.get("plan_version")})
    uw.save_design(settings, to_rows(p), design)
    settings["plan_version"] = int(settings.get("plan_version") or 0) + 1
    settings["plan_problems"] = []
    settings.pop("prose_words_auto", None)                                 # P300：字数不再按时长自动；默认 1500～2500 作参考
    if not _s(settings.get("prose_words")) or settings.get("prose_words_from_duration"):
        settings["prose_words"] = "1500～2500"
        settings.pop("prose_words_from_duration", None)
    p["status"] = STATUS_CONFIRMED
    p["version"] = int(p.get("version") or 0) + 1
    settings.pop("story_plan_last_change", None)                          # P293：确认后上次草稿改动的说明没用了
    p["confirmed_at"] = time.time()
    settings["story_plan"] = p
    # 旧的单话安排卡作废（整部规划派生的安排按话取，见 episode_arrangement）
    settings.pop("arrangement", None)
    # 旧稿处理
    if saga is not None:
        _apply_to_saga(settings, p, saga, imp)
    return p, imp


def _apply_to_saga(settings, plan, saga, imp):
    """按 uid 把 saga.episodes 对到新规划：对得上的保留（内容变了标 plan_stale）；对不上的退休；重新编号。"""
    eps = saga.get("episodes") or []
    nb = {e["uid"]: e for e in plan["episodes"]}
    pv = int(settings.get("plan_version") or 0)                       # confirm 已经 +1 过了
    retired = settings.setdefault("retired_episodes", [])
    kept = []
    for se in eps:
        uid = _s(se.get("plan_uid"))
        has = bool(_s(se.get("prose")) or _s(se.get("pictures")) or _s(se.get("body")))
        if uid and uid in nb:
            ne = nb[uid]
            if has and _s(se.get("plan_sig")) and se.get("plan_sig") != ne["sig"]:
                se["plan_stale"] = True
                se["timeline_stale"] = True
            elif has and not se.get("plan_stale"):
                # P295：这一话没变、稿子还是当前的 → 版本号跟着盖成新版，前情门才不会把它当旧稿拦下
                se["plan_version"] = pv
                if isinstance(se.get("writing_review"), dict):
                    se["writing_review"]["plan_version"] = pv
            se["no"] = ne["no"]
            kept.append(se)
        elif not uid:
            no = int(se.get("no") or 0)
            ne = next((x for x in plan["episodes"] if x["no"] == no), None)
            if ne is not None:
                se["plan_uid"] = ne["uid"]
                if has and int(se.get("plan_version") or 0) and int(se.get("plan_version") or 0) != int(settings.get("plan_version") or 0):
                    se["plan_stale"] = True
                    se["timeline_stale"] = True
                kept.append(se)
            elif has:
                retired.append(_retire(se, "规划里没有这一话了"))
        else:
            if has:
                retired.append(_retire(se, "这一话被合并或删除"))
    by_no = {int(se.get("no")): se for se in kept}
    new_eps = []
    for e in plan["episodes"]:
        se = by_no.get(e["no"])
        if se is None:
            se = {"no": e["no"], "status": "planned", "plan_uid": e["uid"]}
        se["plan_uid"] = e["uid"]
        se["title"] = e["title"]
        new_eps.append(se)
    saga["episodes"] = new_eps


def _retire(se, why):
    return {"at": time.time(), "why": why, "no": se.get("no"), "plan_uid": se.get("plan_uid"), "title": se.get("title"),
            "prose": se.get("prose"), "pictures": se.get("pictures"), "body": se.get("body"),
            "shotlist": se.get("shotlist"), "timeline": se.get("timeline")}


def confirmed(settings):
    p = (settings or {}).get("story_plan")
    return p if isinstance(p, dict) and p.get("status") == STATUS_CONFIRMED and p.get("episodes") else None


def episode_of(plan, no):
    return next((e for e in (plan or {}).get("episodes") or [] if int(e.get("no")) == int(no)), None)


# ─────────────────────────── 派生某一话的「本话安排」 ───────────────────────────

def episode_arrangement(plan, no):
    """整部规划的第 no 话 → 现有正文/剧本链认的「本话安排」（cores.arrangement 的形状）。
    段数按内容定（beats_free=True，段名取自各段内容）；最后一段就是本话结尾；预算按各段秒数。"""
    e = episode_of(plan, no)
    if e is None:
        return None
    beats = e.get("beats") or []
    if not beats:
        _foc = [_s(x) for x in (e.get("focus") or [])]
        def _is_focus(txt):
            return (not _foc) or any(_overlap(txt, f) >= 0.3 for f in _foc)
        beats = [{"text": x, "weight": (WEIGHT_FOCUS if _is_focus(x) else WEIGHT_PASS), "seconds": [0, 0]} for x in (e.get("events") or [])]
        if beats and not any(b["weight"] == WEIGHT_FOCUS for b in beats):
            beats[-1]["weight"] = WEIGHT_FOCUS
    if not beats:
        beats = [{"text": e["one_line"], "weight": WEIGHT_FOCUS, "seconds": list(e["duration"]["seconds"])}]
    # 结尾一定要在最后一段里：最后一段的文字对不上落点，就把落点并进最后一段（段数仍按内容定）
    beats = [dict(b) for b in beats]
    if _s(e.get("landing")) and _overlap(e["landing"], beats[-1]["text"]) < 0.2:
        beats[-1]["text"] = beats[-1]["text"].rstrip("。；;") + "；" + e["landing"]
    dur = mid(tuple(e["duration"]["seconds"])) or sum(mid(tuple(b["seconds"])) for b in beats)
    stages = [_stage_name(b["text"], k) for k, b in enumerate(beats, 1)]
    people = [{"name": x["name"], "role": x["role"], "relation": x["relation"], "unnamed": x["unnamed"],
               "look": x.get("look", ""), "personality": x.get("personality", ""), "voice": x.get("voice", ""),
               "sex": x.get("sex", ""), "age": x.get("age", "")}
              for x in (plan.get("people") or []) if not e["cast"] or x["name"] in e["cast"]]
    if not people:
        people = [{"name": x["name"], "role": x["role"], "relation": x["relation"], "unnamed": x["unnamed"],
                   "sex": x.get("sex", ""), "age": x.get("age", "")} for x in (plan.get("people") or [])]
    budget_secs = [mid(tuple(b["seconds"])) for b in beats]
    if not any(budget_secs) and dur:
        # 旧规划带总时长没带分段秒数：重点段多分、带过段少分（重点 3 份、带过 1 份）
        w = [3 if b["weight"] == WEIGHT_FOCUS else 1 for b in beats]
        budget_secs = [int(round(dur * x / float(sum(w)))) for x in w]
    if any(budget_secs):
        budget_secs = [x if x > 0 else max(3, int(round((dur or 30) * 0.1))) for x in budget_secs]
    else:
        budget_secs = []                                                  # P300：新规划没有秒数 → 不给预算，时长是剧本切出来的结果
    arr = {
        "status": STATUS_CONFIRMED, "version": int(plan.get("version") or 1), "idea": plan.get("idea", ""),
        "duration_sec": int(dur or 0), "mood": _s((plan.get("prefs") or {}).get("pace")) or _s(plan.get("tone")),
        "whole_story": True, "first_episode_ends_at": e["landing"], "episode_no": int(no), "episode_uid": e["uid"],
        "beats_free": True,
        "items": {
            "what": {"text": e["one_line"], "source": "建议", "added": []},
            "shoot": {"text": "；".join([e["purpose"]] + [b["text"] for b in beats if b["weight"] == WEIGHT_FOCUS]).strip("；"), "source": "建议", "added": []},
            "how": {"text": e["duration"].get("where", "") or ("节奏%s：%s%s" % (e["pace"], PACE_MEANING.get(e["pace"], ""), ("；" + e["pace_why"]) if e.get("pace_why") else "")),
                    "geometry": e.get("geometry", ""), "source": "建议", "added": []},
            "pace": {"text": "；".join("%s：%s" % (b["weight"], b["text"][:16]) for b in beats), "value": PACE_TO_LEGACY.get(norm_pace(e["pace"]), "推进"), "source": "建议", "added": []},
            "who": {"text": "、".join(x["name"] for x in people), "people": people, "source": "建议", "added": []},
            "where": {"text": "、".join(e["scenes"]), "places": list(e["scenes"]), "source": "建议", "added": []},
        },
        "beats": [{"stage": st, "text": b["text"], "weight": b["weight"], "source": "建议", "added": []} for st, b in zip(stages, beats)],
        "budget": ([{"beat": st, "seconds": sec, "focus": (b["text"] if b["weight"] == WEIGHT_FOCUS else "")}
                    for st, b, sec in zip(stages, beats, budget_secs)] if budget_secs else []),
        "cut_after_beat": -1,
        "start_state": e["start"], "link_in": e["link_in"], "link_out": e["link_out"],
        "history": [],
    }
    return arr


def _stage_name(text, k):
    t = re.sub(r"[，。！？、；：,.!?;:\s]", "", _s(text))
    return "%d·%s" % (k, t[:8]) if t else "第%d段" % k


def later_episodes_brief(plan, no):
    """第 no 话之后各话的要点（正文写作时告诉作者：这些是后话，本话结束时还没发生）。"""
    out = []
    for e in (plan or {}).get("episodes") or []:
        if int(e["no"]) > int(no):
            out.append({"话": e["no"], "标题": e["title"], "发生": e["one_line"][:80], "结束于": e["landing"][:60]})
    return out


# ─────────────────────────── 阶段 3：正文/剧本有没有执行规划（零模型，只报待审核/有问题）───────────────────────────

REVIEW_PROBLEM = "有问题"
REVIEW_PENDING = "待审核"


def _entity_tokens(text):
    """一句话里的实体词：2～4 字的汉字连串（粗切），用来做"提前泄底/重演"的字面线索。"""
    t = re.sub(r"[^一-龥]", " ", _s(text))
    toks = set()
    for w in t.split():
        for n in (4, 3, 2):
            for i in range(0, max(0, len(w) - n + 1)):
                toks.add(w[i:i + n])
    return {x for x in toks if len(x) >= 2}


def _hit_ratio(needles, hay):
    ns = [n for n in needles if n]
    if not ns:
        return 0.0
    h = re.sub(r"[^一-龥]", "", _s(hay))
    return sum(1 for n in ns if n in h) / float(len(ns))


def _key_words(text, k=8):
    """一句规划文字的关键词：去掉高频虚词后最长的几个连串（≥2 字），最多 k 个。"""
    words = re.findall(r"[一-龥]{2,}", _s(text))
    stop = ("孙女", "两人", "他们", "她们", "自己", "然后", "开始", "结束", "已经", "没有", "什么", "这个", "那个", "一个", "一起", "准备")
    out = []
    for w in sorted(set(words), key=len, reverse=True):
        if any(x in w for x in stop) and len(w) <= 2:
            continue
        for n in (4, 3, 2):
            if len(w) >= n:
                piece = w[:n]
                if piece not in out:
                    out.append(piece)
                break
        if len(out) >= k:
            break
    return out


# ─────────────────────────── P292 想法核对 / 道具去向（零模型，只报待审核）───────────────────────────

_PSEG = None


def _pseg():
    """jieba 词性切分（本机已装；没装就返回 None，两项检查跳过——已知限制）。"""
    global _PSEG
    if _PSEG is None:
        try:
            import jieba
            import jieba.posseg as pseg
            jieba.setLogLevel(60)
            _PSEG = pseg
        except Exception:
            _PSEG = False
    return _PSEG or None


FACT_STOP = set("两人 他们 她们 自己 一起 第一次 一个 一把 一封 一张 一天 之内 一次 里面 上面 下面 那个 这个 东西 时候 事情 一点 多一点 之后 之前 最后 其中".split())
FACT_GENERIC_V = set("是 有 在 做 去 来 到 把 被 给 让 说 想 要 用 开始 结束 发现 知道 觉得 成为 进行 出现 决定 变成 看到 听到 走到 回到".split())


def _people_text(plan):
    return " ".join(_s(x.get("name")) + " " + _s(x.get("role")) + " " + _s(x.get("relation")) for x in (plan or {}).get("people") or [])


def idea_facts(idea, plan=None):
    """想法拆成子句，留下「有名词也有动词」的（像一件事）；名词里去掉人物身份词。
    返回 [{"text","nouns","verbs"}]。jieba 不在 → []。"""
    pseg = _pseg()
    if not pseg:
        return []
    ppl = _people_text(plan)
    out = []
    for c in re.split(r"[。；！？\n，,;]", _s(idea)):
        c = c.strip()
        if not c:
            continue
        nouns, verbs = [], []
        for w, f in pseg.cut(c):
            if len(w) < 2 or w in FACT_STOP:
                continue
            if f == "nr" or f == "t":                                   # 人名、时间词不当道具
                continue
            if f.startswith("n"):
                if w not in ppl and w not in nouns and not _is_person_noun(w):     # 指人的词不是道具
                    nouns.append(w)
            elif f.startswith("v") and w not in FACT_GENERIC_V and w not in verbs:
                verbs.append(w)
        if nouns and verbs:
            out.append({"text": c, "nouns": nouns, "verbs": verbs})
    return out


_PERSON_TAIL = tuple("人员者主生客儿女妈爸哥姐弟妹爷奶婆公叔姨师匠长兵官娘汉仔童徒友")


def _is_person_noun(w):
    return w.endswith(_PERSON_TAIL)


def _noun_in(n, s):
    """名词对上：整词在；或末字在（旧屋/老屋、铁盒/盒子、柜子/木柜）——只看字面，宁可多放过也不乱报。"""
    if n in s:
        return True
    head = n[:-1] if n.endswith("子") else n[-1]
    return bool(head) and head in s


def _plan_sentences(plan):
    out = []
    for e in (plan or {}).get("episodes") or []:
        for ev in e.get("events") or []:
            out.append((e["no"], "事件", _s(ev)))
        for b in e.get("beats") or []:
            out.append((e["no"], "段落", _s(b.get("text"))))
        for s in re.split(r"[。；！？]", _s(e.get("one_line"))):
            if s.strip():
                out.append((e["no"], "剧情", s.strip()))
    return out


_MODAL = re.compile(r"(能|会|可以|要|打算|准备|将要|想|应该|正能|得以|得知|确认|说明|解释|告知|提到|回忆|想起)")


def fact_checks(plan):
    """想法里写明的事，规划有没有照样写（P292）。只报「待审核」，每条带最接近的原句。"""
    p = plan or {}
    facts = idea_facts(p.get("idea"), p)
    if not facts:
        return []
    sents = _plan_sentences(p)
    alltext = "".join(s for _, _, s in sents) + _s(p.get("synopsis"))
    ppl_all = _people_text(p)
    items = []
    for f in facts:
        nouns, verbs = f["nouns"], f["verbs"]
        if not any(_noun_in(n, alltext) for n in nouns):                          # 连末字都没有才算没提到
            items.append({"status": REVIEW_PENDING, "check": "想法",
                          "detail": "想法里「%s」整部规划没提到（%s）——请看是不是漏了或换了说法" % (f["text"], "、".join(nouns))})
            continue
        best, bs = -1.0, None
        full = []
        for no, kind, s in sents:
            score = sum(2 for n in nouns if _noun_in(n, s)) + sum(1 for v in verbs if v in s)
            if score > best:
                best, bs = score, (no, kind, s)
            if (all(_noun_in(n, s) for n in nouns) and all(v in s for v in verbs) and kind == "事件" and no not in full
                    and not _MODAL.search(s)):                    # "确认钥匙能打开铁盒"是在说明，不是开了
                full.append(no)
        miss = [n for n in nouns if not _noun_in(n, bs[2])] if bs else list(nouns)
        if len(miss) * 2 >= len(nouns):
            items.append({"status": REVIEW_PENDING, "check": "想法",
                          "detail": "想法里「%s」没有哪一句是照样写的：最接近的是第%d话%s「%s」，里面没有「%s」——请看是换了说法还是改了做法" % (
                              f["text"], bs[0], bs[1], bs[2][:40], "、".join(miss))})
        elif len(full) >= 2:
            items.append({"status": REVIEW_PENDING, "check": "想法",
                          "detail": "想法里「%s」在第%s话都完整写了一遍——请看是不是发生了两次" % (f["text"], "、".join(str(x) for x in full))})
        elif bs and not any(v in bs[2] for v in verbs):
            # 东西都在、做法的动词一个都不在，而且这句用了想法里没有的工具（用铁丝撬开）——才像改了做法；只是换个动词不报
            tools = [m for m in re.findall(r"用([一-龥]{1,3}?)(?:[，、和与]|撬|打|开|锁|割|敲|砸|挑|捅|撑)", bs[2]) if m and m not in nouns and m not in ppl_all]
            if tools:
                items.append({"status": REVIEW_PENDING, "check": "想法",
                              "detail": "想法里「%s」：最接近的一句是第%d话%s「%s」，做法变成了用「%s」，没有「%s」——请看是不是改了做法" % (
                                  f["text"], bs[0], bs[1], bs[2][:40], "、".join(tools), "、".join(verbs))})
    return items


def plan_props(plan):
    """道具 = 想法和规划事件里的名词（去掉人名、人物身份词、地点、时间）。"""
    pseg = _pseg()
    p = plan or {}
    if not pseg:
        return []
    ppl = _people_text(p)
    places = " ".join(" ".join(e.get("scenes") or []) + " " + _s(e.get("place")) for e in p.get("episodes") or [])
    texts = [_s(p.get("idea"))] + [_s(ev) for e in p.get("episodes") or [] for ev in (e.get("events") or [])]
    out = []
    for t in texts:
        for w, f in pseg.cut(t):
            if len(w) < 2 or w in FACT_STOP or not f.startswith("n") or f in ("nr", "ns", "nt"):
                continue
            if w in ppl or w in places or w in out or _is_person_noun(w):
                continue
            out.append(w)
    return out


_GIVE = r"(递给|交给|递到|塞给|交到|塞进|放到|放进|递向|递还给|还给)"
_TAKE = r"(摸出|掏出|拿出|取出|抽出|拿着|握着|捏着|攥着|举起|拈起|捡起|拿起|拾起|用|将|把)"
_KEEP = r"(收进|揣进|收好|收起|收回|放回|装进|塞回|别在|收入)"
_RECV = r"(接过|接住|接了|接下)"


def track_prop(prop, names, episodes):
    """一件道具在正文里的去向。episodes: [(no, prose)]。返回 [(no, detail)]。
    规则：谁收起 / 谁被递给 / 谁接过 → 持有人；拿出来用的人不是持有人、这句里又没写交接 → 待审核。"""
    names = [n for n in names if n]
    if not names or not prop:
        return []
    nm = "(" + "|".join(re.escape(n) for n in sorted(set(names), key=len, reverse=True)) + ")"
    r_keep = re.compile(nm + r"[^，]{0,20}" + _KEEP)
    r_give = re.compile(_GIVE + r"[^，]{0,4}" + nm)
    r_recv = re.compile(nm + r"[^，]{0,8}" + _RECV)
    r_take = re.compile(nm + r"[^，]{0,12}" + _TAKE + r"[^，]{0,8}" + re.escape(prop))
    holder, where, flags = None, None, []
    for no, prose in episodes:
        for s in re.split(r"[。！？；\n]", _s(prose)):
            if prop not in s:
                continue
            m_keep, m_give, m_recv, m_take = r_keep.search(s), r_give.search(s), r_recv.search(s), r_take.search(s)
            actor = m_take.group(1) if m_take else None
            if actor and holder and actor != holder and not (m_give or m_recv):
                flags.append((no, "「%s」上次在%s手里（第%d话：%s），这里%s拿出来用了，中间没写交接：%s" % (
                    prop, holder, where[0], where[1][:30], actor, s.strip()[:50])))
            new = m_give.group(2) if m_give else (m_recv.group(1) if m_recv else (m_keep.group(1) if m_keep else actor))
            if new:
                holder, where = new, (no, s.strip())
    return flags


_HANDOFF = re.compile(r"(递上|递过|递给|递到|交给|交到|送上|接过|接了)")
_KEPT = re.compile(r"(一直|保存|收着|留着|妥帖地躺|正躺在|夹在|藏在|放在这儿最保险|放这儿最保险|留了|存着|保管了|收了.{0,3}年)")


def handoff_then_kept(prop, prose):
    """道具刚递出/接过，同一话后文又写成对方"一直保存/正躺在…里"（P311：照片刚送到，对方却像收藏了几十年）。
    返回 (递出句, 保存句) 或 None。"""
    sents = [x.strip() for x in re.split(r"(?<=[。！？])", _s(prose)) if x.strip()]
    hand_i = None
    for i, snt in enumerate(sents):
        if prop in snt and _HANDOFF.search(snt):
            hand_i = i
            break
    if hand_i is None:
        return None
    for snt in sents[hand_i + 1:]:
        if prop in snt and _KEPT.search(snt) and not _HANDOFF.search(snt):
            return sents[hand_i], snt
    return None


def prop_checks(plan, saga):
    """{话号: [items]}：道具去向（P292）。只看有正文的话，按话号顺序追。"""
    p = plan or {}
    names = [re.sub(r"[（(].*", "", _s(x.get("name"))) for x in p.get("people") or []]
    eps = sorted(((int(e.get("no") or 0), _s(e.get("prose"))) for e in ((saga or {}).get("episodes") or []) if _s(e.get("prose"))),
                 key=lambda x: x[0])
    out = {}
    if not eps or not names:
        return out
    for prop in plan_props(p):
        for no, detail in track_prop(prop, names, eps):
            out.setdefault(no, []).append({"status": REVIEW_PENDING, "check": "道具", "detail": detail})
        for no, prose in eps:
            hk = handoff_then_kept(prop, prose)
            if hk:
                out.setdefault(no, []).append({"status": REVIEW_PENDING, "check": "道具",
                                               "detail": "「%s」这一话刚递出（%s），后文却写成对方一直保存着（%s）——请核对" % (prop, hk[0][:30], hk[1][:34])})
    return out


_NIGHT = re.compile(r"(深夜|夜里|夜晚|晚上|半夜|凌晨|午夜|入夜|每晚|夜色|夜风|夜深|熄灯|睡不着|睡下)")
_DAY = re.compile(r"(夕阳|余晖|晨光|朝阳|阳光|日光|正午|晌午|午后|清晨|早晨|上午|下午|黄昏|傍晚|日头|太阳|烈日|日落|晨曦)")


_SCENE_W = re.compile(r"光|灯|窗|门|墙|街|路|桥|河|海|山|林|树|屋|房|厅|室|院|店|铺|馆|楼|台|廊|栏|摊|市|集|巷|广场|车站|教室|走廊|天空|云|雨|雪|风|气味|味道|空气|石|木|砖|地板|地面|桌|椅|床|柜")
_LOOK_W = re.compile(r"头发|发丝|长发|短发|马尾|辫|眼睛|眼睛|眉|瞳|脸|面孔|肤|身材|个子|高挑|瘦|胖|穿着|身穿|衣|裙|袍|外套|衬衫|袖|靴|鞋|帽|围巾|项圈|神情|神色|表情|眼神")


def first_appearance_check(ep, prose, people=(), prev_prose=""):
    """P321（用户 9-12）：写故事的常识——每一话里**第一次出现**的场景和**第一次出场**的人物，出场时先详细描写。
    人物：人物表里的人，前面各话正文没出现过、本话第一次提到 → 首次提到之后 260 字内要有外貌词（头发/穿着/身形/神情…）。
    场景：本话安排的场景/地点，前面各话没出现过 → 本话第一次提到之后 200 字内要有场景词 ≥2。第一话开头另查前 500 字有没有场景。
    只报待审核，不拦。"""
    txt = str(prose or "")
    if not txt.strip():
        return []
    prev = str(prev_prose or "")
    out = []
    names = [str(x.get("name") if isinstance(x, dict) else x or "").strip() for x in (people or [])]
    names = [n for n in names if n and len(n) >= 2]
    cast = [n for n in (ep.get("cast") or []) if n in names] or names
    for n in cast:
        if n in prev or n not in txt:
            continue
        i = txt.index(n)
        win = txt[i:i + 260]
        if not _LOOK_W.search(win):
            out.append({"status": REVIEW_PENDING, "check": "出场描写",
                        "detail": "%s 是这一话第一次出场，出场后 260 字内没写外貌/穿着/神情——首次出场要先详细描写" % n})
    places = [_s(x) for x in (ep.get("scenes") or [])] or [_s(ep.get("place"))]
    for pl in [x for x in places if x]:
        key = re.sub(r"[（(].*", "", pl)[:4]
        if not key or key in prev or key not in txt:
            continue
        i = txt.index(key)
        win = txt[i:i + 200]
        if len(_SCENE_W.findall(win)) < 2:
            out.append({"status": REVIEW_PENDING, "check": "场景描写",
                        "detail": "「%s」是这一话第一次出现的场景，第一次提到后 200 字内没有场景描写——新场景出现时要先写它的样子" % pl})
    if int(ep.get("no") or 0) == 1:
        head = txt[:500]
        if len(_SCENE_W.findall(head)) < 2:
            out.append({"status": REVIEW_PENDING, "check": "开场", "detail": "第一话前 500 字没写开场的场景——故事开始先让读者看到地方"})
    return out


def opening_check(ep, prose, people=()):
    """P316（用户 2026-09-11）：每话开头先写这一话第一个场景的样子和出场人物的外貌，再进事件和对话。
    看第一句台词之前（最多前 500 字）：有没有场景词、有没有人名+外貌词。缺哪个报待审核，不拦。"""
    txt = str(prose or "")
    if not txt.strip():
        return None
    m = re.search(r"[“「]", txt)
    head = txt[:m.start()] if m else txt[:500]
    head = head[:500]
    names = [str(x.get("name") if isinstance(x, dict) else x or "").strip() for x in (people or [])]
    names = [n for n in names if n]
    cast = [n for n in (ep.get("cast") or []) if n] or names
    has_scene = len(_SCENE_W.findall(head)) >= 2
    has_person = any(n in head for n in cast) and bool(_LOOK_W.search(head))
    if has_scene and has_person:
        return None
    lack = []
    if not has_scene:
        lack.append("场景的样子")
    if not has_person:
        lack.append("出场人物的外貌")
    return {"status": REVIEW_PENDING, "check": "开场",
            "detail": "第一句台词之前（前 %d 字）没写%s——开场应先让读者看到场景和人物再进事件；请看是不是一上来就进对话了" % (len(head), "和".join(lack))}


def day_night_check(ep, prose):
    """规划说这一话在夜里、正文却写白天的光（P294 实测：深夜十一点的屋里"夕阳的余晖斜照进来"）。只报第一句。"""
    plan_text = " ".join([_s(ep.get("one_line")), _s(ep.get("start")), " ".join(_s(x) for x in ep.get("events") or []),
                          " ".join(_s(b.get("text")) for b in ep.get("beats") or [])])
    if not _NIGHT.search(plan_text) or _DAY.search(plan_text):
        return None
    for s in re.split(r"[。！？\n]", _s(prose)):
        m = _DAY.search(s)
        if m and not _NIGHT.search(s):
            return {"status": REVIEW_PENDING, "check": "昼夜",
                    "detail": "规划里这一话在夜里，正文出现「%s」：%s——请看时间对不对" % (m.group(1), s.strip()[:50])}
    return None


def repeated_sentences(prose, earlier, min_len=14, thresh=0.8):
    """本话里和前面几话几乎一样的长句（P294 实测：第 4 话把第 3 话描写琴声的整句又写了一遍）。
    earlier: [(话号, 正文)]。最多报 2 条。"""
    out = []
    prev = []
    for no, text in earlier:
        for s in re.split(r"[。！？\n；]", _s(text)):
            s = s.strip()
            if len(s) >= min_len:
                prev.append((no, s, _bigrams(s)))
    if not prev:
        return out
    for s in re.split(r"[。！？\n；]", _s(prose)):
        s = s.strip()
        if len(s) < min_len:
            continue
        bg = _bigrams(s)
        for no, ps, pb in prev:
            if bg and pb and len(bg & pb) / float(min(len(bg), len(pb))) >= thresh:
                out.append({"status": REVIEW_PENDING, "check": "重复句",
                            "detail": "这句和第%d话几乎一样：「%s」——请看是不是重复写了" % (no, s[:40])})
                break
        if len(out) >= 2:
            break
    return out


# ─────────────────────────── P296 揭晓名词：后话才揭晓的东西不能提前出现 ───────────────────────────

_REVEAL = re.compile(r"(揭晓|揭示|揭开|得知|发现|真相|原来|才知道|认出|暴露|坦白|承认|露出|现身|身份)")


def _ep_plan_text(ep):
    return " ".join([_s(ep.get("one_line")), _s(ep.get("start")), _s(ep.get("landing"))] +
                    [_s(x) for x in ep.get("events") or []] + [_s(b.get("text")) for b in ep.get("beats") or []])


def _nouns_in(text, names):
    pseg = _pseg()
    if not pseg:
        return []
    out = []
    for w, f in pseg.cut(_s(text)):
        if len(w) < 2 or w in FACT_STOP or f == "nr" or not f.startswith("n"):
            continue
        if w in names or w in out or _REVEAL.search(w):
            continue
        out.append(w)
    return out


def secret_nouns(plan):
    """故事的秘密：用户想法里带 发现/原来/真相/才知道/认出… 的句子里的名词（「最后发现是新搬来的盲人调音师」→ 盲人调音师）。
    只认想法，不认规划自己写的揭晓句（那样会把钢琴/阳台/父亲都当秘密）。"""
    names = {_s(x.get("name")) for x in (plan or {}).get("people") or []}
    out = []
    for sent in re.split(r"[。；！？\n]", _s((plan or {}).get("idea"))):
        if not _REVEAL.search(sent):
            continue
        for w in _nouns_in(sent, names):
            if w not in out:
                out.append(w)
    return out


def reveal_episode(plan, w):
    """规划里第一次在揭晓句里提到 w 的那一话号；没有 → 0。"""
    for ep in (plan or {}).get("episodes") or []:
        for src in [_s(ep.get("landing")), _s(ep.get("one_line"))] + [_s(x) for x in ep.get("events") or []]:
            for sent in re.split(r"[。；！？\n]", src):
                if w in sent and _REVEAL.search(sent):
                    return ep["no"]
    return 0


def reveal_nouns(ep, plan):
    """这一话揭晓的秘密（想法定义的秘密里，揭晓话正是这一话的）。"""
    return [w for w in secret_nouns(plan) if reveal_episode(plan, w) == ep["no"]]


def reveal_problems(plan):
    """规划自己就泄底：想法定义的秘密，规划第 i 话才揭晓，第 j<i 话的剧情/事件/段落里已经写了。返回字符串列表（进 problems）。"""
    eps = (plan or {}).get("episodes") or []
    out = []
    for w in secret_nouns(plan):
        ri = reveal_episode(plan, w)
        if not ri:
            continue
        for prev in eps:
            if prev["no"] >= ri:
                break
            txt = _ep_plan_text(prev)
            if w in txt:
                m = re.search(r"[^。；！？\n]*" + re.escape(w) + r"[^。；！？\n]*", txt)
                out.append("第%d话才揭晓的「%s」，第%d话里已经写了：%s" % (ri, w, prev["no"], (m.group(0).strip() if m else "")[:40]))
                break
    return out


def spoiler_check(no, eps, prose, plan):
    """正文层：秘密的揭晓话在本话之后，本话正文却写到了 → 待审核。只报第一条。"""
    for w in secret_nouns(plan):
        ri = reveal_episode(plan, w)
        if not ri or ri <= no or w not in prose:
            continue
        m = re.search(r"[^。；！？\n]*" + re.escape(w) + r"[^。；！？\n]*", prose)
        return {"status": REVIEW_PENDING, "check": "泄底",
                "detail": "「%s」是第%d话才揭晓的，本话正文已经写到：%s——请看是不是提前说了" % (w, ri, (m.group(0).strip() if m else "")[:50])}
    return None


def review_episodes(settings, saga):
    """{话号: [{"status","check","detail"}]}。只看有正文的话；剧本项只在有剧本时查。"""
    plan = confirmed(settings)
    out = {}
    if not plan:
        return out
    eps = plan["episodes"]
    by_no = {int(e.get("no") or 0): e for e in ((saga or {}).get("episodes") or [])}
    props = prop_checks(plan, saga)                                   # P292：道具去向，按话挂
    for e in eps:
        no = e["no"]
        se = by_no.get(no) or {}
        prose = _s(se.get("prose"))
        items = []
        if not prose:
            continue
        if se.get("plan_stale"):
            items.append({"status": REVIEW_PROBLEM, "check": "规划", "detail": "规划改过了，这一话正文还是按旧规划写的，要更新"})
        # 提前泄底：后话的结束状态/关键事件
        for later in eps:
            if later["no"] <= no:
                continue
            kws = _key_words(later["landing"]) + [w for ev in later.get("events", [])[:3] for w in _key_words(ev, 3)]
            r = _hit_ratio(kws, prose)
            if kws and r >= 0.5:
                items.append({"status": REVIEW_PENDING, "check": "泄底",
                              "detail": "第%d话的内容（%s）在本话正文里出现了不少字眼，请看是不是提前写了" % (later["no"], later["title"])})
                break
        sp = spoiler_check(no, eps, prose, plan)                          # P296：后话才揭晓的名词
        if sp and not any(x["check"] == "泄底" for x in items):
            items.append(sp)
        # 重演上一话
        if no > 1:
            prev = next((x for x in eps if x["no"] == no - 1), None)
            if prev:
                kws = [w for ev in prev.get("events", []) for w in _key_words(ev, 3)]
                r = _hit_ratio(kws, prose)
                if kws and r >= 0.6:
                    items.append({"status": REVIEW_PENDING, "check": "重演",
                                  "detail": "上一话的事件字眼在本话里大量出现（%d%%），请看是不是把上一话又演了一遍" % int(r * 100)})
        # 结尾
        tail = prose[-300:]
        # 落点的二字词一个都没在结尾出现才报（四字片段换个说法就全对不上，实测每话都报=没报）
        _lb = [x for x in _bigrams(e["landing"]) if x not in ("完成", "已经", "两人", "他们", "开始", "结束", "回到", "决定", "准备")]
        if _s(e["landing"]) and _lb and not any(x in re.sub(r"[^一-龥]", "", tail) for x in _lb):
            items.append({"status": REVIEW_PENDING, "check": "结尾",
                          "detail": "正文结尾和规划的结束状态字面对不上（规划：%s）——可能只是换了说法，请看" % e["landing"][:30]})
        # 重点段没对白
        paras = [p for p in re.split(r"\n\s*\n", prose) if p.strip()]
        for b in e.get("beats") or []:
            if b.get("weight") != WEIGHT_FOCUS:
                continue
            kws = _key_words(b["text"], 4)
            hit = [p for p in paras if _hit_ratio(kws, p) >= 0.5]
            if hit and not any(re.search(r"[“「\"]", p) for p in hit):
                items.append({"status": REVIEW_PENDING, "check": "重点段",
                              "detail": "规划标为重点的「%s」，对应的段落里一句台词都没有，请看是不是被描写挤占了" % b["text"][:20]})
        # 剧本漏事件
        pics = _s(se.get("pictures"))
        sl = se.get("shotlist") if isinstance(se.get("shotlist"), dict) else None
        if pics and sl:
            try:
                from . import shotlist as _slm
                miss = _slm.missing_events(sl, pics, prose)
                for m in miss[:4]:
                    items.append({"status": REVIEW_PENDING, "check": "剧本",
                                  "detail": "正文里的这件事剧本里按字面找不到：%s" % str(m.get("ev"))[:30]})
            except Exception:
                pass
        items.extend(props.get(no) or [])
        dn = day_night_check(e, prose)                                    # P294 昼夜
        if dn:
            items.append(dn)
        _prev_prose = "\n".join(_s(v.get("prose")) for k, v in sorted(by_no.items()) if k < no and _s(v.get("prose")))
        items.extend(first_appearance_check(e, prose, plan.get("people") or [], _prev_prose))   # P321：首次出现的场景/人物要先描写
        items.extend(repeated_sentences(prose, [(int(k), _s(v.get("prose"))) for k, v in sorted(by_no.items()) if k < no and _s(v.get("prose"))]))
        if items:
            out[no] = items
    return out
