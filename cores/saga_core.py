# -*- coding: utf-8 -*-
"""saga_core.py — 长篇故事系统：故事圣经 + 三层精度 + 锁定推进。

核心设计（用户方案 + 两处加固）：

    LOCKED     已发生的集：正文和事实**永不重写**
    PLANNED    当前季的未来集：可调整，越近越详细
    DIRECTION  后面几季：只存大方向、人物终点、真相终点

三层精度对应三种数据：
    saga    整部方向（几季，每季一句话 + 人物终点 + 伏笔终点）
    season  当季集纲（每集一句话路标）
    episode 当前集详细剧情（分阶段）

【加固一】状态推进是机械的。锁定一集时，AI 只产出"本集新增了什么"的增量，
由代码 append 进状态档——**绝不让 AI 重写整份状态**，它一重写就篡改历史，
这是 AI 长篇跑歪的头号原因。

【加固二】写第 N 集时喂给模型的上下文是分层的，不是全量正文：
    故事圣经 + 本季路线 + 最近 3 集摘要 + 当前状态档 + 本集旧规划 + 前瞻 2 集
Qwen3.6 的上下文越干净，质量越稳。
"""
import copy
import json
import re
import time
from pathlib import Path

INS_DIR = Path(__file__).resolve().parent.parent / "presets" / "instructions"

LOCKED, PLANNED, DIRECTION = "LOCKED", "PLANNED", "DIRECTION"

# 空状态档的形状。每一项都是"已经确定的事实"，只增不改。
EMPTY_STATE = {
    "at": "",                  # 现在走到哪（地点/时间）
    "protagonist": {"level": "", "abilities": [], "items": []},
    "party": [],               # 当前队伍成员
    "relations": {},           # "A→B": "关系描述"
    "world_facts": [],         # 已确立的世界规则
    "revealed": [],            # 观众已经知道的
    "hidden": [],              # 角色还不知道的
    "open_threads": [],        # 未解决的线
    "dead": [],                # 死了的人
    "promises": [],            # 说过的重要承诺
}


def _saga(story):
    return story.setdefault("saga", {
        "bible": {}, "seasons": [], "episodes": [], "state": copy.deepcopy(EMPTY_STATE),
        "plugins": [], "created": time.time()})


def get_saga(sid):
    from . import story_core
    st = story_core.get_story(sid) or {}
    return _saga(st)


def save_saga(sid, saga):
    from . import story_core
    st = story_core.get_story(sid)
    if not st:
        return False
    st["saga"] = saga
    story_core.save_story(st)
    return True


def episode(saga, no):
    return next((e for e in saga.get("episodes") or [] if int(e.get("no")) == int(no)), None)


# ---------- 上下文分层（加固二） ----------

def build_context(saga, no, recent=3, lookahead=2):
    """写第 no 集要喂给模型的上下文。不给全量正文，只给分层摘要。"""
    b = saga.get("bible") or {}
    eps = saga.get("episodes") or []
    cur = episode(saga, no) or {}
    cur_season = cur.get("season") or 1
    season = next((s for s in saga.get("seasons") or []
                   if int(s.get("no")) == int(cur_season)), {})
    # 保存过的上一话已经是用户确认的剧情事实，不必等到锁定才允许续写。
    past = [e for e in eps if int(e.get("no")) < int(no)
            and (e.get("status") == LOCKED
                 or (str(e.get("body") or "").strip() and e.get("edited_by_user")))]
    past = sorted(past, key=lambda e: int(e["no"]))[-recent:]
    ahead = [e for e in eps if int(no) < int(e.get("no")) <= int(no) + lookahead]
    lines = [
        "【故事圣经】" + json.dumps(b, ensure_ascii=False),
        "【本季路线】第%s季：%s" % (season.get("no"), season.get("goal") or ""),
        "【本季集纲】" + "；".join("%s.%s" % (e.get("no"), e.get("logline") or "")
                                for e in eps if e.get("season") == cur_season),
        "【最近%d集已经发生的事实】" % len(past) + "\n".join(
            "第%s集：%s" % (e.get("no"),
                            str(e.get("body") or e.get("summary") or
                                e.get("brief") or e.get("logline") or "")[:1800])
            for e in past),
        "【当前故事状态（这些是已经确定的事实，不许改）】"
        + json.dumps(saga.get("state") or {}, ensure_ascii=False),
        "【当前要写的第%s话】\n标题：%s\n一句集纲：%s\n故事简介：%s\n结尾钩子：%s\n"
        "出场人物：%s\n发生地点：%s" % (
            no, cur.get("title") or "", cur.get("logline") or "",
            cur.get("brief") or "", cur.get("hook") or "",
            "、".join(str(x) for x in (cur.get("chars") or [])),
            "、".join(str(x) for x in (cur.get("locations") or []))),
        "【接下来两集要用到的】" + "；".join(
            "%s.%s" % (e.get("no"), e.get("logline") or "") for e in ahead),
    ]
    return "\n\n".join(x for x in lines if x)


def make_episode_brief(saga, no, title="", logline="", call_model=None, *, brief="", hook=""):
    """根据整部框架和用户当前填写的方向，生成一话的四项规划。

    这里只产出集纲，不写正文，也不直接落盘。调用方确认成功后再保存，
    避免模型失败时覆盖用户正在编辑的内容。
    """
    from .model_json import chat_json
    e = episode(saga, no)
    if not e:
        raise ValueError("没有第 %d 话" % int(no))
    draft_title = str(title or e.get("title") or "").strip()
    draft_logline = str(logline or e.get("logline") or "").strip()
    draft_brief = str(brief or e.get("brief") or "").strip()
    draft_hook = str(hook or e.get("hook") or "").strip()
    from . import story_gen
    engine = str((saga.get("bible") or {}).get("engine") or "").strip()
    beat = story_gen.engine_beat(engine or None)
    ins = (
        "你是长篇故事的分话策划。只设计指定这一话，不写正文，不改其他话。"
        "用户填写的标题、一句集纲、故事简介和结尾钩子优先级最高；"
        "它们与已锁定事实不冲突时必须保留。"
        "输出一个 JSON 对象：{\"title\":\"2到10字标题\","
        "\"logline\":\"20到60字的一句集纲\",\"brief\":\"300到600字的完整故事\","
        "\"hook\":\"本话结尾留下的具体悬念或空字符串\"}。"
        "brief 是给人读的完整故事，不是梗概。要写清：这一话里每个出场人物是谁、"
        "什么身份、和主角什么关系（第一次出场的人物尤其要交代清楚，别让读者不知道这人是干嘛的）；"
        "这一话从哪开始、中间发生了什么、人物之间有什么冲突或推进、结尾停在哪。"
        "按事情发生的先后顺序讲，像讲故事一样自然，不要写成条目。"
        "不得把后续话的重要剧情提前用掉。\n"
        "【这一话的情绪节拍】%s\n"
        "按这个节拍写：要有铺垫、有起伏、有一个让读者有感觉的节点，"
        "不要一上来就到高潮，也不要平铺直叙赶进度。一话装一个完整的情绪循环就够，"
        "不要把该分几话的事挤进这一话。" % beat
    )
    user = "\n\n".join([
        build_context(saga, no, recent=3, lookahead=2),
        "【要设计的就是第%d话】" % int(no),
        "【用户当前标题】%s" % (draft_title or "未填写，请根据原集纲生成"),
        "【用户当前一句集纲】%s" % (draft_logline or "未填写，请使用原集纲"),
        "【用户当前故事简介】%s" % (draft_brief or "未填写，请根据集纲生成"),
        "【用户当前结尾钩子】%s" % (draft_hook or "未填写，可按本话需要生成或留空"),
    ])

    def _need(d):
        if not str(d.get("title") or "").strip():
            raise ValueError("缺 title")
        if not str(d.get("logline") or "").strip():
            raise ValueError("缺 logline")
        if not str(d.get("brief") or "").strip():
            raise ValueError("缺 brief")
        if "hook" not in d:
            raise ValueError("缺 hook")

    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=900, **kw)
    d, _ = chat_json(ins, user, call_model, validate=_need, tries=2, temperature=0.55)
    return {
        "title": str(d.get("title") or "").strip(),
        "logline": str(d.get("logline") or "").strip(),
        "brief": str(d.get("brief") or "").strip(),
        "hook": str(d.get("hook") or "").strip(),
    }


# ---------- 锁定与状态推进（加固一） ----------

def _merge_state(state, delta):
    """把一话事实增量机械合并进状态；输入不会被就地复用。"""
    st = state
    d = delta or {}
    st.setdefault("protagonist", {"level": "", "abilities": [], "items": []})
    for k in ("abilities", "items"):
        st["protagonist"].setdefault(k, [])
        for v in d.get(k) or []:
            if v not in st["protagonist"][k]:
                st["protagonist"][k].append(v)
    if d.get("level"):
        st["protagonist"]["level"] = d["level"]
    for k in ("party", "world_facts", "revealed", "hidden", "open_threads",
              "dead", "promises"):
        st.setdefault(k, [])
        for v in d.get(k) or []:
            if v not in st[k]:
                st[k].append(v)
    st.setdefault("relations", {})
    for k, v in (d.get("relations") or {}).items():
        st["relations"][k] = v
    for v in d.get("resolved_threads") or []:
        if v in st["open_threads"]:
            st["open_threads"].remove(v)
    if d.get("at"):
        st["at"] = d["at"]
    return st


def rebuild_state(saga):
    """按仍处于 LOCKED 的话次重放增量，解锁后不会残留旧事实。"""
    locked = sorted((e for e in saga.get("episodes") or [] if e.get("status") == LOCKED),
                    key=lambda x: int(x.get("no") or 0))
    if "state_base" not in saga:
        # 老项目的已锁定话没有逐话 state_delta，无法倒推出各话贡献。
        # 第一次迁移时把现有总状态当作兼容基线，之后的新话增量再逐话重放，
        # 这样启用新逻辑不会把用户已经确认的历史事实清空。
        legacy_locked = any("state_delta" not in e for e in locked)
        saga["state_base"] = copy.deepcopy(
            (saga.get("state") or EMPTY_STATE) if legacy_locked else EMPTY_STATE)
    st = copy.deepcopy(saga.get("state_base") or EMPTY_STATE)
    for e in locked:
        if "state_delta" in e:
            _merge_state(st, copy.deepcopy(e.get("state_delta") or {}))
    saga["state"] = st
    return st


def lock_episode(sid, no, delta=None):
    """锁定一集：状态改为 LOCKED，把增量事实并进状态档。

    delta 形如 {"abilities": ["风刃"], "party": ["莉娅"], "relations": {...},
                "revealed": [...], "open_threads": [...], "resolved_threads": [...]}
    代码只做 append / 定向删除，绝不整体替换——历史不可篡改。
    """
    saga = get_saga(sid)
    e = episode(saga, no)
    if not e:
        return None
    e["state_delta"] = copy.deepcopy(delta or {})
    e["status"] = LOCKED
    e["locked_at"] = time.time()
    rebuild_state(saga)
    save_saga(sid, saga)
    return saga


def revise_future(sid, from_no, changes):
    """局部修正未来规划：只动 PLANNED 的集，LOCKED 一个字不碰。

    changes: {集号: 新的 logline}
    """
    saga = get_saga(sid)
    touched = []
    for e in saga.get("episodes") or []:
        if int(e.get("no")) <= int(from_no) or e.get("status") == LOCKED:
            continue
        if int(e["no"]) in {int(k) for k in changes}:
            e["logline"] = changes[int(e["no"])] if int(e["no"]) in changes \
                else changes[str(e["no"])]
            touched.append(int(e["no"]))
    save_saga(sid, saga)
    return touched


def progress(saga):
    """给界面用的进度概览。"""
    eps = saga.get("episodes") or []
    return {"total": len(eps),
            "locked": sum(1 for e in eps if e.get("status") == LOCKED),
            "seasons": len(saga.get("seasons") or []),
            "next": next((int(e["no"]) for e in sorted(eps, key=lambda x: int(x["no"]))
                          if e.get("status") != LOCKED), None)}


# ---------- 四级精度（用户+GPT 共识） ----------
#   方向级   整部 N 季，每季一句话 + 人物终点 + 真相终点
#   集纲级   当前季每集一句路标
#   近景级   当前集之后 2~3 集，稍微详细（防止为了这集爽把下集的料用光）
#   详细级   当前正在做的这一集
#
# 三条铁律（定死不再改）：
#   1 已发生事实不可变（LOCKED）
#   2 当前集可以精细修改
#   3 未来只是动态规划，不是已经写完的故事

NEAR_AHEAD = 3          # 近景规划覆盖的集数


def _coerce_bible(d):
    """顶层被写成数组、seasons/episodes 元素被写成字符串——先掰正再校验。"""
    while isinstance(d, list) and d:
        nxt = next((x for x in d if isinstance(x, (dict, list))), None)
        if nxt is None:
            return {}
        d = nxt
    if not isinstance(d, dict):
        return {}
    if isinstance(d.get("bible"), list):
        d["bible"] = next((x for x in d["bible"] if isinstance(x, dict)), {})
    for k in ("seasons", "episodes"):
        v = d.get(k)
        if isinstance(v, dict):
            v = [v]
        if isinstance(v, list) and len(v) == 1 and isinstance(v[0], list):
            v = v[0]
        d[k] = [x for x in (v or []) if isinstance(x, dict)]
    return d


def _fallback_bible(one_line, settings=None):
    """模型彻底写不出框架时的确定性兜底。纯拼装、不调模型，一定成功。"""
    from . import project_prompt, style_presets
    s = settings or {}
    one = str(one_line or "").strip()
    world = project_prompt.resolved_world(s)
    return {
        "bible": {"premise": one, "protagonist": {"name": "", "lack": "", "arc": ""},
                  "opponent": {"name": "", "just_goal": ""},
                  "world": style_presets.world_def(world) or world,
                  "truth": "", "endings": {}},
        "seasons": [{"no": 1, "title": "第一季", "goal": one, "episodes_count": 1}],
        "episodes": [{"no": 1, "season": 1, "title": "第一话",
                      "logline": one, "brief": one,
                      "chars": [], "locations": [], "hook": "", "status": EMPTY}],
    }


def build_bible(one_line, settings=None, call_model=None):
    """一句话 + 已有设定 → 故事圣经 + 分季方向 + 第一季集纲。

    settings 里用户已经填过的东西（人物、世界观、美学、备注）是**事实**，
    只能被使用，不许被改写——这是全站基本法。
    """
    from .model_json import chat_json
    from . import story_gen
    ins = (INS_DIR / "长篇_故事圣经.txt").read_text(encoding="utf-8")
    s = settings or {}
    from . import project_prompt
    # 题材引擎：从一句话自动判定（用户不选），它决定整部/每话的情绪节拍。
    # detect_engine 返回 (引擎名, 判定理由)，取名字。
    engine, _why = story_gen.detect_engine(str(one_line or ""), s)
    beat = story_gen.engine_beat(engine)
    given = []
    given.append(
        "题材引擎（系统自动判定，用来定情绪节拍，不是硬设定，与用户已定内容冲突时以用户为准）：%s。\n"
        "这一题材每一话的情绪节拍是：%s\n"
        "分季分话时按这个节拍排——每一话要能走完一个完整的情绪循环，"
        "不要把该铺几话的事挤进一话，也不要一话流水账赶进度。" % (engine, beat))
    if s.get("world_type") and s["world_type"] != "自动判断":
        given.append("世界类型（用户已定，不许改）：" + s["world_type"])
    if s.get("content_tendencies"):
        given.append("题材尺度（用户已定）：" + "、".join(s["content_tendencies"]))
    if s.get("plan"):
        given.append("篇章框架（用户已定，不许改）：" + str(s["plan"]))
    if s.get("extra_requirements"):
        given.append("用户不可更改的故事要求（必须满足）：" + str(s["extra_requirements"]))
        given.append("第一话集纲硬约束：episodes 中 no=1 的 logline 必须明确写出并满足下面每一条，"
                     "关键名词不得换成近义词，也不得挪到第二话以后：" + str(s["extra_requirements"]))
    given.append("项目故事规则：\n" + project_prompt.story_generation_block(s))
    for c in s.get("_characters") or []:
        given.append("用户已定的人物（名字外貌性格一个字不许改，故事要围绕他们写）：%s（%s%s，%s，%s）"
                     % (c.get("name"), c.get("age") or "", c.get("sex") or "",
                        (c.get("look") or "")[:40], (c.get("clothing") or "")[:40]))
    user = "一句话：" + str(one_line or "")
    if given:
        user += "\n\n" + "\n".join(given)

    # 【硬 / 软 之分】用户定的规矩：拦截是设计失败。
    # 硬 = 缺了整条链跑不下去（没有 bible、没有 seasons、没有 episodes）。
    # 软 = 质量问题（禁词、要求没满足、某集少写了出场人物）——重试，
    #      试到头就交付最好的一版并记一笔，不把「全部生成」打断在半路。
    def _hard(d):
        # 模型偶尔把 bible 写成列表、把 episodes 元素写成字符串。
        # 不先掰形状的话，AttributeError 会被当成"JSON 不合格"重试三次然后报错，
        # 而真正的原因（形状对不上）永远看不见。
        if isinstance(d.get("bible"), list):
            d["bible"] = next((x for x in d["bible"] if isinstance(x, dict)), {})
        for k in ("seasons", "episodes"):
            v = d.get(k)
            if isinstance(v, dict):
                v = [v]
            if isinstance(v, list) and len(v) == 1 and isinstance(v[0], list):
                v = v[0]
            d[k] = [x for x in (v or []) if isinstance(x, dict)]
        if not (d.get("bible") or {}).get("premise"):
            raise ValueError("缺 bible.premise")
        if not (d.get("seasons") or []):
            raise ValueError("缺 seasons")
        if not (d.get("episodes") or []):
            raise ValueError("缺第一季的 episodes 集纲")

    def _soft(d):
        why = []
        serialized = json.dumps(d, ensure_ascii=False)
        bad = project_prompt.story_world_violations(serialized, s)
        if bad:
            why.append("整部框架混入了与世界时代冲突的内容（%s），"
                       "用这个世界已有的手段替代" % "、".join(bad))
        for x in d.get("seasons") or []:
            if not str(x.get("goal") or "").strip():
                why.append("第 %s 季没写 goal" % x.get("no"))
        eps = d.get("episodes") or []
        first = next((e for e in eps if int(e.get("no") or 1) == 1), eps[0])
        hard = project_prompt.story_requirement_violations(
            json.dumps(first, ensure_ascii=False), s)
        if hard:
            why.append("第一话集纲违反不可更改要求：" + "；".join(hard))
        cast = ((d.get("bible") or {}).get("cast")) or []
        if not cast:
            why.append("bible 里缺 cast 人物表。凡是会出场的名字都要在 cast 里，"
                       "并写清 sex / age / role")
        else:
            named = {str(x.get("name") or "").strip() for x in cast if isinstance(x, dict)}
            for x in cast:
                if not isinstance(x, dict):
                    continue
                miss = [k for k in ("name", "sex", "age", "role") if not str(x.get(k) or "").strip()]
                if miss:
                    why.append("cast 里「%s」缺：%s" % (x.get("name") or "?", "、".join(miss)))
            appeared = {n2 for e in (d.get("episodes") or [])
                        for n2 in (e.get("chars") or []) if str(n2 or "").strip()}
            ghost = [n2 for n2 in appeared if n2 not in named]
            if ghost:
                why.append("这些人在集纲里出场了但 cast 里没有：" + "、".join(ghost[:6]))
        # 新增：每一集必须写满出场人物 / 地点 / 钩子 / 剧情简介。
        # 下游要靠它们建人物卡、建场景卡、判断这一集画哪些图——缺一项后面就没法接。
        for e in eps:
            miss = [k for k, label in (("brief", "剧情简介"), ("chars", "出场人物"),
                                       ("locations", "地点"), ("hook", "结尾钩子"))
                    if not (e.get(k) or "")]
            if miss:
                why.append("第 %s 集缺：%s" % (e.get("no"), "、".join(miss)))
        return why

    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=4000, **kw)
    # 【任务永不中断】用户定的死规矩：不管怎么样都不能让任务停在半路。
    # 这里没有任何一条 raise 能逃出去：
    #   结构合格            → 用它
    #   结构不合格          → 带着原因重试
    #   三轮都不行、连 JSON 都吐不出来 → 用确定性兜底框架，标一笔 _issues
    # 兜底是纯拼装、不调模型，所以一定成功。用户拿到的至少是一个能往下走的骨架，
    # 而不是一句报错加三分钟白等。
    best, best_n, u = None, None, user
    for _ in range(3):
        try:
            d, _raw = chat_json(ins, u, call_model, validate=_hard, tries=3,
                                normalize=_coerce_bible, temperature=0.7)
        except Exception as ex:
            d = None
            u = user + ("\n\n上一次完全没能给出可用的 JSON（%s）。"
                        "这次务必只输出一个 JSON 对象，数组里的每个元素都必须是对象 {}。"
                        % str(ex)[:120])
            continue
        why = _soft(d)
        if not why:
            break
        if best_n is None or len(why) < best_n:
            best, best_n = d, len(why)
        u = user + "\n\n上一稿的问题（必须改掉）：" + "；".join(why)
    else:
        d = best if isinstance(best, dict) else d
    if not isinstance(d, dict) or not (d.get("episodes") or []):
        d = _fallback_bible(one_line, s)
        d["_issues"] = "模型没能给出可用的整部框架，已按一句话拼了一个最小框架，请手动补"
    else:
        left = _soft(d)
        if left:
            d["_issues"] = "；".join(left)
    def _as_list(v):
        if isinstance(v, (list, tuple)):
            return [str(x).strip() for x in v if str(x or "").strip()]
        return [x.strip() for x in re.split(r"[、,，;；/]+", str(v or "")) if x.strip()]

    b = d.get("bible") or {}
    cast = b.get("cast")
    if isinstance(cast, dict):
        cast = [cast]
    b["cast"] = [x for x in (cast or []) if isinstance(x, dict) and str(x.get("name") or "").strip()]
    b["engine"] = engine        # 自动判定的题材引擎，每话生成时读它拿节拍
    d["bible"] = b
    for i, e in enumerate(d.get("episodes") or [], 1):
        e.setdefault("no", i)
        e.setdefault("season", 1)
        e["status"] = EMPTY
        e["chars"] = _as_list(e.get("chars"))
        e["locations"] = _as_list(e.get("locations"))
        e["brief"] = str(e.get("brief") or "").strip()
        e["hook"] = str(e.get("hook") or "").strip()
    return d


EMPTY = "EMPTY"
DRAFT = "DRAFT"


def replan_forward(sid, from_no, call_model=None):
    """「重新规划后续」：从这一集往后重新规划，已发生的一个字不动。

    做四件事（GPT 的修正，我认同）：
      · 第 from_no 之前：绝对不动
      · 之后 2~3 集：重新规划得详细一点（近景级）
      · 再往后到本季末：只重算一句话集纲
      · 后面几季：只检查大方向要不要调
    """
    from .model_json import chat_json
    saga = get_saga(sid)
    eps = sorted(saga.get("episodes") or [], key=lambda e: int(e.get("no") or 0))
    cur = episode(saga, from_no) or {}
    season_no = cur.get("season") or 1
    future = [e for e in eps if int(e.get("no")) > int(from_no)
              and e.get("status") != LOCKED]
    if not future:
        return {"near": [], "outline": [], "note": "后面没有可规划的集"}
    ins = (INS_DIR / "长篇_重新规划后续.txt").read_text(encoding="utf-8")
    user = "\n\n".join([
        build_context(saga, from_no, recent=2, lookahead=0),
        "【第%s集改成了什么】%s" % (from_no, (cur.get("body") or cur.get("logline") or "")[:1500]),
        "【要重新规划的集】近景（详细一点）：%s；其余只要一句话集纲：%s" % (
            "、".join(str(e["no"]) for e in future[:NEAR_AHEAD]),
            "、".join(str(e["no"]) for e in future[NEAR_AHEAD:]) or "无"),
    ])

    def _need(d):
        if not d.get("episodes"):
            raise ValueError("没给 episodes")

    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=3000, **kw)
    d, _ = chat_json(ins, user, call_model, validate=_need, tries=2, temperature=0.6)
    got = {int(x.get("no")): x for x in d.get("episodes") or [] if x.get("no")}
    touched = []
    for e in future:
        n = int(e["no"])
        if n in got:
            # 已有正文的未来集：正文作废（前提变了），但存进历史版本，可回滚
            if e.get("body"):
                e.setdefault("versions", []).append(
                    {"at": time.time(), "body": e["body"], "logline": e.get("logline")})
                e["body"] = ""
                e["status"] = EMPTY
            e["logline"] = got[n].get("logline") or e.get("logline")
            if got[n].get("plan"):
                e["plan"] = got[n]["plan"]
            touched.append(n)
    # 后面几季的大方向调整
    for s2 in saga.get("seasons") or []:
        if int(s2.get("no") or 0) <= int(season_no):
            continue
        upd = (d.get("seasons") or {}) if isinstance(d.get("seasons"), dict) else {}
        if str(s2["no"]) in upd:
            s2["goal"] = upd[str(s2["no"])]
    save_saga(sid, saga)
    return {"touched": touched, "near": [e for e in future[:NEAR_AHEAD]],
            "outline": [e["no"] for e in future[NEAR_AHEAD:]]}


def impact_of(sid, from_no):
    """改这一集会影响谁——点按钮之前先让用户看清楚。"""
    saga = get_saga(sid)
    eps = sorted(saga.get("episodes") or [], key=lambda e: int(e.get("no") or 0))
    future = [e for e in eps if int(e.get("no")) > int(from_no)]
    near = [e for e in future if e.get("status") != LOCKED][:NEAR_AHEAD]
    rest = [e for e in future if e.get("status") != LOCKED][NEAR_AHEAD:]
    locked_after = [int(e["no"]) for e in future if e.get("status") == LOCKED]
    has_body = [int(e["no"]) for e in future if e.get("body")]
    return {
        "keep": "第 1~%s 集已发生，一个字不动" % from_no,
        "rewrite_detail": [int(e["no"]) for e in near],
        "rewrite_outline": [int(e["no"]) for e in rest],
        "locked_after": locked_after,
        "will_lose_body": has_body,      # 这些集已有正文，会作废（存历史版本可回滚）
    }


# ---------- 按话存分镜（第一公民：当前是第几话） ----------
# 原来 timeline 是全局单槽：做第二话会覆盖第一话的分镜，180 集必然互相踩。
# 现在每一话有自己的 timeline，互不干扰。

def ep_timeline(sid, no, create=False):
    """取第 no 话的分镜。老项目的全局 timeline 自动迁移到第 1 话。"""
    from . import story_core
    st = story_core.get_story(sid) or {}
    saga = _saga(st)
    e = episode(saga, no)
    if e is None:
        # 没有 saga 结构的老项目：退回全局槽，保证旧数据还能用
        return st.get("timeline") or {"scenes": []}
    if e.get("timeline"):
        return e["timeline"]
    if int(no) == 1 and st.get("timeline", {}).get("scenes"):
        # 迁移：老的全局分镜归入第 1 话，原槽保留不动（回滚用）
        e["timeline"] = st["timeline"]
        e["timeline"]["migrated_from_global"] = True
        save_saga(sid, saga)
        return e["timeline"]
    if create:
        e["timeline"] = {"scenes": []}
        save_saga(sid, saga)
        return e["timeline"]
    return {"scenes": []}


def save_ep_timeline(sid, no, tl):
    """写回第 no 话的分镜。"""
    from . import story_core
    st = story_core.get_story(sid)
    if not st:
        return False
    saga = _saga(st)
    e = episode(saga, no)
    if e is None:
        st["timeline"] = tl          # 老项目继续写全局槽
        story_core.save_story(st)
        return True
    e["timeline"] = tl
    e["timeline"]["updated_at"] = time.time()
    st["saga"] = saga
    story_core.save_story(st)
    return True


def timeline_stale(sid, no):
    """正文改了但分镜还是旧的？返回 True 表示该提示用户重拆。"""
    saga = get_saga(sid)
    e = episode(saga, no)
    if not e:
        return False
    if e.get("timeline_stale"):
        return True
    tl = e.get("timeline") or {}
    snap = tl.get("source_body")
    if not snap or not tl.get("scenes"):
        return False
    return snap != (e.get("body") or "")


def stamp_source(sid, no):
    """拆完分镜后盖上正文快照。"""
    saga = get_saga(sid)
    e = episode(saga, no)
    if not e or not e.get("timeline"):
        return
    e["timeline"]["source_body"] = e.get("body") or ""
    e["timeline_stale"] = False
    save_saga(sid, saga)


def missing_assets(sid, no):
    """生成分镜前的一致性检查：本话的人物和场景有没有设定图。"""
    from . import asset_core
    saga = get_saga(sid)
    e = episode(saga, no) or {}
    body = e.get("body") or ""
    adopted = {v.get("owner_id") for v in asset_core.list_assets(sid, "visuals")
               if v.get("status") == "adopted"}
    miss = []
    for c in asset_core.list_assets(sid, "characters"):
        if c.get("name") and c["name"] in body and c.get("character_id") not in adopted:
            miss.append({"kind": "character", "id": c["character_id"], "name": c["name"]})
    for s in asset_core.list_assets(sid, "scenes"):
        if int(s.get("episode") or 0) == int(no) and s.get("scene_id") not in adopted:
            miss.append({"kind": "scene", "id": s["scene_id"], "name": s.get("name")})
    return miss


# ─────────── 段上的"成片字段"：重建分镜时要保住，丢了要能找回 ───────────
# 2026-09-02 用户实测：点「全部生成提示词」后视频全没了。
# 重建 timeline 的地方（导演层 save_to_timeline、新链路建表）每段都写 video=""，
# 把已出片子的路径抹掉。片子文件还在盘上，只是段不再指向它。
_SEG_MEDIA = ("video", "video_hd", "end_frame", "seed", "size", "size_hd",
              "mode", "used_refs", "adopted", "refs", "ref_hide",
              # P242：这一段是用哪个档、哪个比例、多少步出的，也要跟着搬，
              # 否则跨话恢复后看不出一话里混了几种档
              "size_tier", "ratio", "steps", "size_tier_hd")


def _all_segments(tl):
    return [g for s in ((tl or {}).get("scenes") or []) for g in (s.get("segments") or [])]


def merge_segment_media(sid, no, tl_new):
    """把旧分镜里同段号的成片字段搬进新分镜。新分镜里已有值的不覆盖。

    段号对得上就搬——提示词重写了，片子还是那一段的片子，
    要不要重出由用户在分镜页点「重新生成」决定，不替他删。"""
    old = {int(g.get("no") or 0): g for g in _all_segments(ep_timeline(sid, no))}
    n = 0
    for g in _all_segments(tl_new):
        o = old.get(int(g.get("no") or 0))
        if not o:
            continue
        for k in _SEG_MEDIA:
            if o.get(k) not in (None, "", [], {}) and g.get(k) in (None, "", [], {}):
                g[k] = o[k]
                n += 1
    return n


def seg_video_path(sid, ep, scene_no, seg_no, hd=False):
    """一段视频的存盘路径（P237）。

    【文件名必须带话号】原来是 seg_{sid}_{场号}_{段号}.mp4，
    第一话和第二话的「场1段1」撞成同一个文件，后生成的把先生成的物理覆盖掉，
    没有任何提示。高清版、结束帧、重试备份都从这个路径派生，自动跟着走。
    """
    import os
    return os.path.join("outputs", "video",
                        "seg_%s_e%d_%d_%d%s.mp4"
                        % (sid, int(ep or 1), int(scene_no or 1), int(seg_no),
                           "_hd" if hd else ""))


def seg_video_legacy_path(sid, scene_no, seg_no, hd=False):
    """老规则的路径（不带话号）。**只用于读旧项目**，不再往这儿写。"""
    import os
    return os.path.join("outputs", "video",
                        "seg_%s_%d_%d%s.mp4"
                        % (sid, int(scene_no or 1), int(seg_no), "_hd" if hd else ""))


def _eps_claiming(sid, scene_no, seg_no, skip_ep):
    """除了 skip_ep，还有哪几话在同一个（场号，段号）上有段——老路径归谁说不清（P237）。"""
    out = []
    try:
        saga = get_saga(sid) or {}
        for e in (saga.get("episodes") or []):
            no = int(e.get("no") or 0)
            if not no or no == int(skip_ep or 0):
                continue
            tl2 = ep_timeline(sid, no) or {}
            for sc2 in (tl2.get("scenes") or []):
                if int(sc2.get("no") or 1) != int(scene_no or 1):
                    continue
                for g2 in (sc2.get("segments") or []):
                    if int(g2.get("no") or 0) == int(seg_no):
                        out.append(no)
                        break
    except Exception:
        pass
    return sorted(set(out))


def recover_segment_media(sid, tl, ep=None, notes=None):
    """字段是空的、盘上却有片子——把路径填回去。返回补了几段。

    先按新规则（带话号）找；找不到再退回老规则（不带话号）。
    **老路径上如果别的话也有同坐标的段，就不认领**——不猜它属于哪一话，
    把冲突写进 notes 由调用方提示（P237）。
    """
    import os
    n = 0
    for sc in ((tl or {}).get("scenes") or []):
        for g in (sc.get("segments") or []):
            if str(g.get("video") or "").strip():
                continue
            sno, gno = sc.get("no") or 1, g.get("no")
            p = seg_video_path(sid, ep or 1, sno, gno)
            if not os.path.exists(p):
                q = seg_video_legacy_path(sid, sno, gno)
                if not os.path.exists(q):
                    continue
                others = _eps_claiming(sid, sno, gno, ep or 1)
                if others:
                    if notes is not None:
                        notes.append("场%s段%s：盘上这个片子是老规则存的（没有话号），"
                                     "第%s话也有同一坐标的段，认不出它属于哪一话——"
                                     "没有自动认领，需要人工确认：%s"
                                     % (sno, gno, "、".join(str(x) for x in others), q))
                    continue
                p = q
            g["video"] = p
            ef = p[:-4] + "_end.png"
            if os.path.exists(ef) and not g.get("end_frame"):
                g["end_frame"] = ef
            n += 1
    return n
