# -*- coding: utf-8 -*-
"""scene_layer.py — 场 → 节拍 → 分镜。

用户实测发现的问题：**9 个镜头拍完 2302 字的故事**，等于把短片压成预告片。

查了行业数据：
  · 现代电影平均镜头长度（ASL）**4–6 秒**
  · 一部电影约 **1250 个镜头**，动作片 3000+
  · 一个节拍（beat）通常拆成 **3–4 个镜头**

按这个算，一段一百多字的正文（"下午三点…各自散去"）应该是：
  10 个节拍 × 2~4 镜 ≈ **27 个镜头 ≈ 120 秒**

所以层级必须是三级，一次只做一场：

    故事正文  →  分场（地点+时间+连续动作）
    一场      →  节拍表（这场里一件件发生了什么）
    一个节拍  →  分镜（每拍 1~5 镜，按分量给）

**一次只做一场**是关键。让模型一口气写完整个故事的分镜，它会自动压缩成梗概。
"""
import json
import re
from pathlib import Path

INS_DIR = Path(__file__).resolve().parent.parent / "presets" / "instructions"

# 分量 → 镜头数区间。来自行业惯例：一个 beat 拆 3–4 镜，轻重再上下浮动。
SHOTS_PER_BEAT = {"轻": (1, 1), "中": (2, 3), "重": (3, 5)}

# 现代电影 ASL 4–6 秒。低于 1 秒是错帧，高于 12 秒观众会走神。
MIN_SHOT_SEC, MAX_SHOT_SEC = 1.0, 12.0
TARGET_ASL = 4.5


# ---------- 分场 ----------

def split_scenes(body, max_chars=400):
    """把故事正文切成一场一场。

    切的依据只看地点/时间转换。max_chars 仅保留为旧调用兼容参数，不再按字数
    硬切。过去同一间酒馆每超过约 400 字就被切成新场，导致一处场景出现四个
    “第 N 场”；这既不符合电影场次定义，也会重复生成同一张场景设定图。
    电影里一"场"= 同一地点、同一段连续时间里发生的事。
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", str(body or "")) if p.strip()]
    marks = ("第二天", "傍晚", "深夜", "清晨", "下午", "早上", "夜里", "几天后",
             "一周后", "回到", "走进", "推开门", "另一边", "与此同时")
    scenes, cur = [], []
    for p in paras:
        starts_new = any(p.startswith(m) or p[:12].find(m) >= 0 for m in marks)
        if cur and starts_new:
            scenes.append("\n".join(cur))
            cur = []
        cur.append(p)
    if cur:
        scenes.append("\n".join(cur))
    return scenes


# ---------- 节拍 ----------

def beats_for_scene(scene_text, scene_name, characters, call_model=None):
    """把一场戏拆成节拍表。返回 dict。"""
    from .model_json import chat_json
    ins = (INS_DIR / "场景_节拍拆解.txt").read_text(encoding="utf-8")
    user = "场景名：%s\n可用人物：%s\n这一场的正文：\n%s" % (
        scene_name, "、".join(characters or []), scene_text)

    def _need(x):
        bs = x.get("beats")
        if not isinstance(bs, list) or len(bs) < 4:
            raise ValueError("节拍太少（%d 个）。正文里明写的每个动作、每次视线变化、"
                             "每个环境变化都要有自己的节拍" % len(bs or []))
        for b in bs:
            if not str(b.get("content") or "").strip():
                raise ValueError("第 %s 拍没写 content" % b.get("beat"))

    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=3000, **kw)

    d, _ = chat_json(ins, user, call_model, validate=_need, tries=3, temperature=0.6)
    for i, b in enumerate(d.get("beats") or [], 1):
        b.setdefault("beat", i)
        b["weight"] = b.get("weight") if b.get("weight") in SHOTS_PER_BEAT else "中"
    return d


# ---------- 分镜 ----------

def shots_for_beat(beat, scene_info, characters, prev_shot=None, call_model=None,
                   on_stage=None, ever=None):
    """把一个节拍拆成镜头。返回 [shot]。

    on_stage: 到这一拍为止**已经出场**的人物名单。不给的话模型会把还没登场的人
    画进去——实测里"起风卷落叶"那一拍写了"李哲和艾莉丝站在原地"，
    而艾莉丝下一拍才骑车切入。
    """
    from .model_json import chat_json
    ins = (INS_DIR / "节拍_分镜.txt").read_text(encoding="utf-8")
    # 骨架给了秒数的拍，镜头数按秒数定（ASL 4~6），不再走轻中重三档——
    # 实测"细节堆叠 36 秒"被"中"档压成 3 镜 16 秒，整场比目标短了半分钟。
    if beat.get("seconds"):
        sec = float(beat["seconds"])
        lo, hi = max(1, int(round(sec / 6.0))), max(2, int(round(sec / 4.0)))
    else:
        lo, hi = SHOTS_PER_BEAT.get(beat.get("weight") or "中", (2, 3))
    here = str(beat.get("who") or "").strip()
    # allowed = 建议这一拍画谁（给模型看的），ever = 整个故事里出现过谁（用来拦）
    allowed = [n for n in (characters or []) if n in here or n in (on_stage or [])]
    ever = list(ever if ever is not None else (on_stage or characters or []))
    user = ("场景：%s\n可用人物：%s\n"
            "**这一拍画面里只能出现**：%s\n"
            "这一拍：第%s拍「%s」分量：%s\n内容：%s\n上一拍的最后一个镜头是：%s\n"
            "【只拍这一拍写到的东西】不要加内容里没有的动作、道具或人物。"
            "内容里没写「拿出纸条」，画面里就不能有纸条。"
            % (scene_info, "、".join(characters or []),
               "、".join(allowed) if allowed else "只有环境和静物，没有人物",
               beat.get("beat"), beat.get("title", ""), beat.get("weight", "中"),
               beat.get("content", ""),
               (prev_shot or {}).get("action") or "这是第一拍"))

    def _need(x):
        sh = x.get("shots")
        if not isinstance(sh, list) or not sh:
            raise ValueError("没给 shots")
        if not (lo <= len(sh) <= hi + 1):
            raise ValueError("这一拍应该给 %d~%d 个镜头，你给了 %d 个" % (lo, hi, len(sh)))
        if beat.get("seconds"):
            tot = sum(float(str(x.get("duration") or 0).rstrip("s秒") or 0) for x in sh)
            if tot < float(beat["seconds"]) * 0.8:
                raise ValueError("这一拍目标 %s 秒，你的镜头加起来只有 %.0f 秒。"
                                 "加镜头或拉长镜头" % (beat["seconds"], tot))
        for s in sh:
            for k in ("framing", "camera_movement", "action", "cut_reason"):
                if not str(s.get(k) or "").strip():
                    raise ValueError("镜头 %s 缺 %s" % (s.get("shot_id"), k))
            # 还没登场的人物不许进画面。实测里"起风卷落叶"那一拍写了
            # "李哲和艾莉丝站在原地"，而艾莉丝下一拍才骑车切入。
            blob = " ".join(str(s.get(k) or "") for k in
                            ("subject", "action", "foreground", "midground", "background"))
            # 出场时机：只在**这个人在整个故事里还没出现过**时才拦。
            # 节拍的 who 字段是模型填的，漏填很常见；拿它当硬约束会把
            # 前面几场一直在场的人判成"还没登场"，整条链断在半路。
            early = [n for n in (characters or []) if n in blob and n not in ever]
            if early:
                raise ValueError("镜头 %s 把还没登场的 %s 画进去了。这一拍画面里只能有：%s"
                                 % (s.get("shot_id"), "、".join(early),
                                    "、".join(allowed) or "只有环境"))

    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=2600, **kw)

    # 三次都不合格也要往下走——拦截是设计失败。拿最后一稿，把问题记在镜头上，
    # 界面里那一镜会标出来，用户自己决定要不要单独重做。
    try:
        d, _ = chat_json(ins, user, call_model, validate=_need, tries=3, temperature=0.6)
        note = ""
    except Exception as e:
        d, _ = chat_json(ins, user, call_model, validate=None, tries=1, temperature=0.6)
        note = str(e)[:160]
    out = []
    for s in d["shots"]:
        try:
            sec = float(str(s.get("duration") or TARGET_ASL).rstrip("s秒"))
        except Exception:
            sec = TARGET_ASL
        s["duration"] = max(MIN_SHOT_SEC, min(MAX_SHOT_SEC, sec))
        s["beat"] = beat.get("beat")
        s["beat_title"] = beat.get("title")
        if note:
            s["warn"] = note
        out.append(s)
    return out


def build_scene(scene_text, scene_name, characters, scene_info="", call_model=None,
                on_progress=None):
    """一场戏的完整拆解：节拍表 + 全部镜头。"""
    bs = beats_for_scene(scene_text, scene_name, characters, call_model)
    info = scene_info or "%s，%s" % (bs.get("location") or scene_name,
                                     bs.get("time_of_day") or "")
    shots, prev, n = [], None, 0
    on_stage = []          # 累计已登场的人物，后面的拍次可以继续用
    for b in bs["beats"]:
        for nm in (characters or []):
            if nm in str(b.get("who") or "") and nm not in on_stage:
                on_stage.append(nm)
        got = shots_for_beat(b, info, characters, prev, call_model,
                             on_stage=on_stage, ever=on_stage)
        for s in got:
            n += 1
            s["shot_id"] = "S%02d" % n
            s["location"] = bs.get("location") or scene_name
        shots += got
        prev = got[-1] if got else prev
        if on_progress:
            on_progress(b, got, len(shots))
    total = sum(float(s["duration"]) for s in shots)
    return {"scene": bs, "shots": shots, "total_seconds": round(total, 1),
            "shot_count": len(shots),
            "asl": round(total / len(shots), 1) if shots else 0}


# ---------- 场景卡同步（骨架 → 卡） ----------


_INTERIOR_WORDS = ("内", "外")
_TIME_WORDS = ("黄昏", "傍晚", "清晨", "凌晨", "深夜", "白天", "晚上",
               "日", "夜", "晨", "昏")


def _split_time_interior(part):
    """从「日内」「内/黄昏」「外 夜」这种挤在一起的字段里拆出 (内外, 时间)。

    只有整段都由时间词和内外词组成时才拆——否则「室内对峙」这种功能描述
    会被误判成内外。拆不出来返回 (None, None)，让调用方当普通字段处理。
    """
    t = str(part or "").strip()
    if not t or len(t) > 6:
        return None, None
    rest = t
    it = ""
    tm = ""
    for w in _TIME_WORDS:                 # 长词优先，"黄昏"不能被"昏"拆散
        if w in rest and not tm:
            tm = w
            rest = rest.replace(w, "", 1)
    for w in _INTERIOR_WORDS:
        if w in rest and not it:
            it = w
            rest = rest.replace(w, "", 1)
    rest = rest.strip(" 　/／、,，.-·")
    if rest or not (it or tm):
        return None, None
    return (it or None), (tm or None)

def _scene_headers(body):
    """从剧本正文取每一场的场景头和【空间】【站位】。

    正文里的场景头是 `## 场景N｜地点｜内｜日｜功能`，【空间】那一行是模型
    真正想象出来的空间描述——它比建卡时凭空写的那份准，因为它是照着这一场
    的戏写的。auto 建卡时拿它当 space 原文，不用再问一次模型。
    """
    out = []
    cur = None
    for raw in str(body or "").splitlines():
        line = raw.strip()
        m = re.match(r"^#+\s*场景\s*(\d+)\s*[｜|]\s*(.+)$", line)
        if m:
            parts = [x.strip() for x in re.split(r"[｜|]", m.group(2))]
            cur = {"no": int(m.group(1)), "location": parts[0] if parts else "",
                   "interior": "", "time_of_day": "", "purpose": "",
                   "space": "", "layout": "", "blocking": "", "cast": ""}
            for x in parts[1:]:
                # 【时间和内外可能挤在一个字段里】实测模型写过 `｜日内｜`。
                # 只做精确匹配的话，内外和时间双双落空，"日内"还被当成了功能。
                _it, _tm = _split_time_interior(x)
                if _it or _tm:
                    if _it and not cur["interior"]:
                        cur["interior"] = _it
                    if _tm and not cur["time_of_day"]:
                        cur["time_of_day"] = _tm
                    continue
                if not cur["purpose"]:
                    cur["purpose"] = x
            out.append(cur)
            continue
        if cur is None:
            continue
        # 【布局】是这一场剧情要用到的绝对方位（街东侧／街西侧…）。
        # 【空间】只说"长什么样"，方位得单独有一行，否则下游只能自己编方向
        # （2026-08-27 实测：三个镜头都往"小巷"走，【空间】里根本没有小巷）。
        for tag, key in (("【空间】", "space"), ("【布局】", "layout"),
                         ("【站位】", "blocking"), ("【在场】", "cast")):
            if line.startswith(tag):
                cur[key] = line[len(tag):].strip()
    return out



# 室外证据：写了这些东西就说明头顶是天，不是顶棚。
_OUT_WORDS = ("天空", "阳光", "日光", "月光", "星空", "云", "树冠", "林间",
              "山脊", "崖", "街道", "广场", "旷野", "田", "海", "河岸",
              "露天", "屋顶上", "雪地", "沙地", "草地")
# 室内证据：这些是围合空间才有的构件。
_IN_WORDS = ("顶棚", "层高", "天花", "梁架", "藻井", "屋顶下", "墙面",
             "四壁", "室内", "厅内", "殿内", "舱内", "走廊", "甬道", "地板",
             "墙壁", "大厅", "木梁", "石板地", "壁炉", "烛台", "拱顶")


def _text_interior(card):
    """只看这张卡自己的文字判内外，**不看 interior 标志位**。

    【为什么要绕开标志位】拆卡时要决定"老卡归哪一边"。而老卡的 interior 正是
    被覆盖坏了的那个字段——实测里一张写着"森林深处、阳光透过树冠"的室外卡，
    标志位是"内"。照标志位分，室外的图就会被分到室内那张卡上。
    卡上的空间描述是建卡时按实景写的，比标志位可信。
    """
    txt = " ".join(str((card or {}).get(k) or "") for k in
                   ("name", "space", "contract_text", "landmarks", "main_light"))
    out = sum(1 for w in _OUT_WORDS if w in txt)
    ins = sum(1 for w in _IN_WORDS if w in txt)
    if out > ins:
        return "外"
    if ins > out:
        return "内"
    return ""


# 主光兜底：新建的卡没有光源方向，出图就没有投影方向，下游每一格的人物
# 打光也就无从对齐。按内外和时间给一条确定的默认，比留空强。
_DEFAULT_LIGHT = {
    ("外", "日"): "自然天光（冷白，来自高处斜上方）",
    ("外", "黄昏"): "低角度夕阳（暖橙，来自侧后方，投影拉长）",
    ("外", "夜"): "月光（冷蓝，来自高处）与零星人造光源（暖黄）",
    ("内", "日"): "从门洞或高处开口透进的自然光（冷白，来自侧上方）",
    ("内", "黄昏"): "从门洞或高处开口斜射进来的夕照（暖橙，来自侧向，在地面形成明显光斑）",
    ("内", "夜"): "室内灯火（暖黄，来自侧上方）",
}


def default_light(interior, time_of_day):
    it = "外" if str(interior or "").strip() in ("外", "室外") else "内"
    t = str(time_of_day or "日").strip()
    t = "夜" if t in ("夜", "晚上", "深夜") else ("黄昏" if t in ("黄昏", "傍晚") else "日")
    return _DEFAULT_LIGHT.get((it, t), _DEFAULT_LIGHT[(it, "日")])


# 子区域分隔符。"林婉的公寓-卧室"里的"-"，"云岚宗·主殿"里的"·"。
_SUB_SEPS = ("-", "－", "—", "·", "・", "：", ":", "/", "／")


def _sub_area(location):
    """把「林婉的公寓-卧室/衣柜」拆成 (主地点, 子区域)。没有子区域就返回 (原名, "")。"""
    t = str(location or "").strip()
    for sep in _SUB_SEPS:
        if sep in t:
            a, b = t.split(sep, 1)
            a, b = a.strip(), b.strip()
            if a and b:
                return a, b
    return t, ""


def _same_space(loc_a, loc_b):
    """两个地点名指的是不是同一个拍摄空间。

    【为什么不能只看主地点】玄关、客厅、卧室都属于同一套公寓，但它们是
    三个完全不同的画面。共用一张参考图的话，卧室那场戏会拍成客厅。
    所以：主地点相同而子区域不同 → **不是**同一个空间。
    """
    a1, a2 = _sub_area(loc_a)
    b1, b2 = _sub_area(loc_b)
    if a2 or b2:
        return a1 == b1 and a2 == b2
    return str(loc_a or "").strip() == str(loc_b or "").strip()


# 回指开头：剧本里同一地点重复出现时的省略写法。
_BACKREF_HEADS = ("同场景", "同上", "同前", "同第", "承上", "见场景", "参见",
                  "同一场景", "与场景", "如场景", "同该场景")


def _is_backref(text):
    """这句【空间】是不是"同场景1"这类回指，而不是真的空间描述。"""
    t = str(text or "").strip().lstrip("：:， ")
    return any(t.startswith(w) for w in _BACKREF_HEADS)

def _display_name(location, interior, needs_suffix):
    """同一地点一内一外时，卡名带后缀区分；只有一种就用原名。"""
    base = str(location or "").strip()
    if not needs_suffix or not interior:
        return base
    if base.endswith(("（内）", "（外）", "(内)", "(外)")):
        return base
    return "%s（%s）" % (base, interior)



def _park_orphans(story_id, used_names, episode_no):
    """把这一话没人用、也没画过图的自动卡挪到"以后再说"，别占一次出图。

    【为什么会有孤儿卡】骨架把「林婉的公寓」细化成了玄关、客厅、卧室三个子区域，
    于是原来那张「林婉的公寓」没有任何一场戏用它了。它的 episode 还是 1，
    "只画第一话用得上的"那一步照样会给它渲染一张——白花一次显卡。

    不删卡：用户可能手动改过、可能后面的话次要用。只把 episode 推后，
    并记下是被谁取代的，界面上能看出来。
    """
    from . import asset_core
    moved = []
    for c in asset_core.list_assets(story_id, "scenes"):
        nm = str(c.get("name") or "")
        if nm in used_names or not c.get("auto_made") or c.get("edited_by_user"):
            continue
        if int(c.get("episode") or 1) > int(episode_no or 1):
            continue
        # 已经画过图的不动——图是花过成本的事实
        has_img = any(v.get("owner_id") == c.get("scene_id") and v.get("status") == "adopted"
                      for v in asset_core.list_assets(story_id, "visuals"))
        if has_img:
            continue
        # 【方向不能反】要挪走的是"被细化取代的那张笼统卡"。
        # 原来只比主地点相同就挪，结果反过来把三张子区域卡挪走了——
        # 因为它们的主地点「林婉的公寓」正好还在用。
        # 正确条件：**自己没有子区域**，而在用的名字里有同一主地点的子区域卡。
        base, mine_sub = _sub_area(nm)
        if mine_sub:
            continue          # 自己就是子区域卡，不是被取代的那一张
        heirs = [x for x in used_names
                 if x != nm and _sub_area(x)[0] == base and _sub_area(x)[1]]
        if not heirs:
            continue          # 不是被细化取代的，可能只是这一话没用到，先留着
        c["episode"] = 999
        c["superseded_by"] = "、".join(heirs)
        asset_core.save_asset(story_id, "scenes", c)
        moved.append(nm)
    return moved

def sync_scene_cards(story_id, skeleton, body, episode_no, settings=None):
    """把这一话的拍摄条件落到场景卡上，缺的卡当场补建。

    【为什么要重写】原来这段代码按地点名找卡，找不到就 `continue` 跳过，
    找到就直接覆盖 interior/scale_class。实测里踩了两个坑：

      1. 骨架里「迷宫入口 外 中」和「迷宫入口 内 小」是两场，地点名一样，
         于是都写到同一张卡上，后写的把前面的覆盖掉——卡上最后是
         `interior=内、scale_class=小`，而 space 文字还是建卡时的
         "上方是茂密的树冠，透过缝隙洒下斑驳阳光"。一张卡自己内外打架。
      2. 剧本里真实存在的第三场（迷宫入口内侧）从来没有卡，也就没有参考图，
         视频到这一场只能靠模型现编。

    现在按 **(地点, 内外)** 认卡：一内一外是两个空间，各一张卡；
    骨架点到而卡里没有的，用剧本【空间】原文当场建一张。
    返回 {"updated": [...], "created": [...], "renamed": [...]}。
    """
    from . import asset_core, project_prompt as pp
    scenes = list((skeleton or {}).get("scenes") or [])
    if not scenes:
        return {"updated": [], "created": [], "renamed": [], "parked": []}
    heads = {h["no"]: h for h in _scene_headers(body)}

    # 同一地点在本话出现了几种内外——只有两种都出现时才给卡名加后缀，
    # 否则每张卡都变成"酒馆（内）"这种没必要的长名字。
    kinds = {}
    for s in scenes:
        loc = str(s.get("location") or "").strip()
        it = str(s.get("interior") or "").strip()
        if loc and it:
            kinds.setdefault(loc, set()).add(it)

    cards = list(asset_core.list_assets(story_id, "scenes"))
    by_name = {str(c.get("name") or ""): c for c in cards}
    res = {"updated": [], "created": [], "renamed": [], "parked": []}

    for s in scenes:
        loc = str(s.get("location") or "").strip()
        if not loc:
            continue
        interior = str(s.get("interior") or "").strip()
        want_cls = str(s.get("scale") or "").strip()
        head = heads.get(int(s.get("no") or 0)) or {}
        needs_suffix = len(kinds.get(loc, set())) > 1
        want_name = _display_name(loc, interior, needs_suffix)

        card = by_name.get(want_name)
        if card is None and not _sub_area(loc)[1]:
            # 名字没直接对上，就在"内外相容"的卡里找一张认领。
            # 【带子区域的地点不走模糊匹配】"林婉的公寓-卧室"会被子串匹配认到
            # "林婉的公寓"上，三场戏共用一张客厅的参考图。子区域是独立空间。
            cand = pp.match_scene_name(loc, list(by_name))
            c2 = by_name.get(cand) if cand else None
            if c2 is not None:
                # 【老卡归它自己文字所说的那一边】不能照 interior 标志位分——
                # 那个字段正是被覆盖坏的那个。实测里一张写着"森林深处、阳光透过
                # 树冠"的卡标志位是"内"，照它分，室外那张已经画好的图就被分到
                # 室内卡上去了。卡上的空间描述是按实景写的，可信得多。
                c2_int = _text_interior(c2) or str(c2.get("interior") or "").strip()
                if not c2_int or not interior or c2_int == interior:
                    card = c2

        if card is None:
            fields = {
                "name": want_name,
                "space": head.get("space") or "",
                "layout": head.get("layout") or "",
                "contract_text": head.get("space") or "",
                # 【站位不进 landmarks】站位是"这一场四个人怎么站"，
                # 是这一场的调度，不是这个空间的固定标志物。写进 landmarks 会被
                # 场景基准图的第 1 段当成陈设画出来——实测出现了
                # "四人站在巨石前…" 紧接着 "画面里空无一人" 的自相矛盾。
                # 单独存一个字段，给分镜层用，不进出图提示词。
                "blocking_hint": head.get("blocking") or "",
                "interior": interior, "time_of_day": str(s.get("time_of_day") or ""),
                "episode": episode_no, "auto_made": True,
            }
            cls, txt = pp.merge_scale(None, "", want_cls, fields, settings)
            fields["scale_class"], fields["scale"] = cls, txt
            fields["main_light"] = default_light(interior, s.get("time_of_day"))
            made = asset_core.create_scene(story_id, fields)
            if made:
                extra = {k: v for k, v in fields.items() if v and k not in made}
                if extra:
                    made.update(extra)
                asset_core.save_asset(story_id, "scenes", made)
                by_name[want_name] = made
                res["created"].append(want_name)
            continue

        old_name = str(card.get("name") or "")
        if needs_suffix and old_name != want_name and want_name not in by_name:
            card["name"] = want_name
            by_name.pop(old_name, None)
            by_name[want_name] = card
            res["renamed"].append("%s→%s" % (old_name, want_name))
        if interior:
            card["interior"] = interior
        if s.get("time_of_day"):
            card["time_of_day"] = str(s["time_of_day"])
        cls, txt = pp.merge_scale(card.get("scale_class"), card.get("scale"),
                                  want_cls, card, settings)
        if cls:
            card["scale_class"], card["scale"] = cls, txt
        # 剧本里的【空间】是照着这一场的戏写的，比建卡时凭空写的准。
        # 只在卡还是自动建的（用户没手改过）时才覆盖。
        # 【但回指句不能抄】同一地点第二次出现时，剧本会写"同场景1，但光线更压抑"——
        # 那是给人读的引用，不是空间描述。抄到卡上，这张卡当出图提示词就等于没内容。
        # 实测：净水厂控制室的 space 被写成了"同场景1，但光线因外部混乱而显得更加压抑。"
        # 【布局也要回填】场景卡在第一步（从正文提取设定）就建好了，那时还没有剧本，
        # 所以卡上没有【布局】。剧本写完才有绝对方位表，必须在这里补上，
        # 否则卡上的 layout 永远是空的，场景参考板画不出正确方位
        # （2026-08-27 发现：只回填了 space，layout 漏了）。
        _new_layout = str(head.get("layout") or "").strip()
        if _new_layout and not card.get("edited_by_user"):
            # 同一地点第二次出现时布局往往写得简（夜戏只提床和门），
            # 短版覆盖长版会把方桌、庭院这些方位丢掉（2026-08-28 实测客房卡）。
            _old_layout = str(card.get("layout") or "").strip()
            if len(_new_layout) >= len(_old_layout):
                card["layout"] = _new_layout
        _new_space = str(head.get("space") or "").strip()
        _old_space = str(card.get("space") or "").strip()
        if _new_space and not card.get("edited_by_user") and not _is_backref(_new_space):
            # 新文本比旧的短很多时也别覆盖——多半是省略写法
            if not _old_space or len(_new_space) >= len(_old_space) * 0.6:
                card["space"] = _new_space
                card["contract_text"] = _new_space
        # 【主光要跟着内外走】实测里 interior 被改成"内"之后，main_light 还留着
        # "自然漫射光（冷白，来自上方树冠缝隙）"——室内卡带着室外的光源，
        # 出图时"树冠缝隙"就被当成画面内容画进了封闭大厅里。
        # 【室内卡不许留室外标志物】原来那张卡的 landmarks 里挂着"树冠缝隙"，
        # 它会被场景基准图的第 1 段当成要画的陈设——于是封闭大厅的头顶
        # 又长出了树。内外一旦定死，标志物就得跟着筛一遍。
        _lm = str(card.get("landmarks") or "")
        if _lm and str(card.get("interior") or "").strip() == "内":
            _kept = [x for x in re.split(r"[，、]", _lm)
                     if x.strip() and not any(w in x for w in _OUT_WORDS)]
            if len(_kept) != len([x for x in re.split(r"[，、]", _lm) if x.strip()]):
                card["landmarks"] = "、".join(_kept)
        _light = str(card.get("main_light") or "").strip()
        _it = str(card.get("interior") or "").strip()
        _outdoorish = any(w in _light for w in ("天光", "阳光", "日光", "树冠",
                                                "天空", "月光", "星", "露天"))
        if not _light or (_it == "内" and _outdoorish):
            card["main_light"] = default_light(_it, card.get("time_of_day"))
        card["episode"] = min(int(card.get("episode") or episode_no), episode_no)
        asset_core.save_asset(story_id, "scenes", card)
        res["updated"].append(str(card.get("name")))
    used = set(res["updated"]) | set(res["created"])
    parked = _park_orphans(story_id, used, episode_no)
    if parked:
        res["parked"] = parked
    return res


# ---------- 剧本 → 镜头表（不重编，直接解析） ----------
#
# 【为什么必须解析而不是重新生成】实测对照，同一话第一场：
#
#   剧本写了 6 句台词          分镜只剩 2 句，其中 1 句还被改写了
#     亚瑟：别磨蹭了，盗贼。      →  亚瑟：过来。      （改写）
#     罗格：酒好，人慢。急什么？  →  丢
#     亚瑟：迷宫入口。这是最后一块拼图。 → 丢
#     加雷斯：秩序井然。          →  丢
#     艾莉丝：魔力波动稳定。      →  丢
#     亚瑟：出发。                →  保留
#
#   而且分镜自己编了一整镜"整理披风、卷羊皮卷轴、摩挲剑柄、旋转酒杯"——
#   剧本里根本没有这个节拍。
#
# 根因是分镜层在"重新理解"剧本，而不是"转写"剧本。但剧本本身早就是镜头级的
# （每行 `（景别，运镜）动作` 就是一个镜头，台词按标准剧本格式挂在下面），
# 信息是齐的，没有任何需要再创作的地方。所以这里直接解析。

# 景别词按从长到短匹配，"大远景"不能被"远景"抢先命中
# 从长到短匹配，"大远景"不能被"远景"抢先命中。
# 「过肩」「双人」「主观」实测里模型会用，漏了就全退回默认的中景。
_FRAMINGS = ("大远景", "大特写", "大全景", "全景", "远景", "中景", "近景", "特写",
             "胸像", "半身", "过肩", "双人", "主观", "俯拍", "仰拍", "定格")


def _split_cue(cue):
    """把「（大全景，穿越云层）」拆成 (景别, 运镜)。"""
    body = str(cue or "").strip().strip("（）()")
    # 剧本现在写成「（特写｜8秒）」——秒数是给导演排时长用的，
    # 不是运镜说明，拆的时候要连 ｜ 一起断开，纯秒数的那一节丢掉。
    parts = [x.strip() for x in re.split(r"[，,、｜|]", body) if x.strip()]
    parts = [x for x in parts if not re.fullmatch(r"[\d.]+\s*秒?", x)]
    framing, moves = "", []
    for x in parts:
        if not framing:
            hit = next((f for f in _FRAMINGS if f in x), "")
            if hit:
                framing = hit
                rest = x.replace(hit, "").strip()
                if rest:
                    moves.append(rest)
                continue
        moves.append(x)
    return (framing or "中景"), ("，".join(moves) if moves else "固定机位")


def shots_from_screenplay(body, speakers, seconds_by_scene=None):
    """把剧本正文直接转写成镜头表。返回 [{scene_no, shots:[...]}]。

    剧本格式（生成器已经按这个写）：
        ## 场景1｜断剑酒馆｜内｜日｜功能
        【在场】…  【空间】…  【站位】…
        （全景，横摇）酒馆内人声嘈杂，镜头从中央长桌扫过。
        亚瑟
        （手指轻叩桌面）
        别磨蹭了，盗贼。
        （近景）罗格抬起眼皮。

    规则：
      · 行首是「（…）描述」→ 新开一个镜头，括号里拆成景别和运镜
      · 整行只有人名 → 下面的台词挂到**当前这个镜头**上（标准剧本就是这么读的）
      · 秒数按各镜文本长度加权分配，有台词的镜头按语速另算下限
    """
    speakers = tuple(x for x in (speakers or []) if x)
    heads = _scene_headers(body)
    bounds = []
    lines = str(body or "").splitlines()
    head_idx = [i for i, l in enumerate(lines)
                if re.match(r"^#+\s*场景\s*\d+\s*[｜|]", l.strip())]
    for k, i in enumerate(head_idx):
        j = head_idx[k + 1] if k + 1 < len(head_idx) else len(lines)
        bounds.append((i, j))

    # 【漏写场景头也不能整篇丢掉】模型偶尔会忘了写 `## 场景N｜…`。
    # 原来 bounds 为空就直接返回空列表，一整话的镜头和台词全没了。
    # 兜底：把整篇当成一场，地点等信息交给上游从骨架补。
    if not bounds:
        bounds = [(-1, len(lines))]
        heads = heads or [{"no": 1, "location": "", "interior": "",
                           "time_of_day": "", "space": "", "layout": "",
                           "blocking": "", "cast": ""}]

    out = []
    for k, (i, j) in enumerate(bounds):
        head = heads[k] if k < len(heads) else {}
        shots, cur, pending = [], None, None
        for raw in lines[i + 1:j]:
            line = raw.strip()
            if not line or line.startswith("【") or line.startswith("#"):
                continue
            m = re.match(r"^[（(]([^）)]{1,40})[）)]\s*(.*)$", line)
            if m and not pending:
                # 行首括号 + 后面有描述 = 一个新镜头
                # （只有括号没描述的，是台词上方的表演提示，交给 pending 分支）
                framing, camera = _split_cue(m.group(1))
                cur = {"shot_id": "S%02d" % (len(shots) + 1),
                       "framing": framing, "camera": camera,
                       "action": m.group(2).strip(), "dialogue": "",
                       "parenthetical": "", "chars": []}
                shots.append(cur)
                continue
            if line.rstrip("：:").strip() in speakers:
                pending = line.rstrip("：:").strip()
                continue
            # 形式二：「人名：台词」写在同一行。标准剧本两种写法都合法，
            # 模型两种都会出——只认一种就会整句丢掉。
            inline = None
            for _sp in speakers:
                for _sep in ("：", ":"):
                    if line.startswith(_sp + _sep) and len(line) > len(_sp) + 1:
                        inline = (_sp, line[len(_sp) + 1:].strip())
                        break
                if inline:
                    break
            if inline and inline[1]:
                if cur is None:
                    cur = {"shot_id": "S%02d" % (len(shots) + 1),
                           "framing": "中景", "camera": "固定机位",
                           "action": "", "dialogue": "", "parenthetical": "",
                           "chars": []}
                    shots.append(cur)
                _d = "%s：%s" % (inline[0], inline[1])
                cur["dialogue"] = ((cur["dialogue"] + chr(10) + _d).strip()
                                   if cur["dialogue"] else _d)
                if inline[0] not in cur["chars"]:
                    cur["chars"].append(inline[0])
                pending = None
                continue
            if pending:
                if line.startswith(("（", "(")):
                    if cur is not None:
                        cur["parenthetical"] = line.strip("（）()")
                    continue
                # 这是台词本身
                if cur is None:
                    cur = {"shot_id": "S%02d" % (len(shots) + 1),
                           "framing": "中景", "camera": "固定机位",
                           "action": "", "dialogue": "", "parenthetical": "",
                           "chars": []}
                    shots.append(cur)
                d = "%s：%s" % (pending, line)
                cur["dialogue"] = (cur["dialogue"] + chr(10) + d).strip() if cur["dialogue"] else d
                if pending not in cur["chars"]:
                    cur["chars"].append(pending)
                pending = None
                continue
            # 普通描写行：并进当前镜头的动作
            if cur is not None:
                cur["action"] = (cur["action"] + line) if cur["action"] else line

        # 在场人物：镜头里点到名的 + 【在场】里写的
        cast = [x.strip() for x in re.split(r"[、,，]", str(head.get("cast") or ""))
                if x.strip() in speakers]
        for sh in shots:
            named = [n for n in speakers if n in (sh.get("action") or "")]
            sh["chars"] = list(dict.fromkeys((sh.get("chars") or []) + named)) or list(cast)

        shots = _split_multi_speaker(shots)
        total = None
        if seconds_by_scene:
            total = seconds_by_scene.get(head.get("no")) or seconds_by_scene.get(k + 1)
        _assign_seconds(shots, total)
        out.append({"scene_no": head.get("no") or (k + 1),
                    "location": head.get("location") or "",
                    "interior": head.get("interior") or "",
                    "time_of_day": head.get("time_of_day") or "",
                    "space": head.get("space") or "",
                    "layout": head.get("layout") or "",
                    "blocking": head.get("blocking") or "",
                    "shots": shots})
    return out



def _split_multi_speaker(shots):
    """一个镜头下面挂了两个以上不同的人说话时，拆成一人一镜。

    标准剧本里，一个镜头提示下面连着写三个人的对白，意思是这三句都在这一镜里。
    但那是给现场看的——现场可以一个长镜头摇过去。AI 生成一段十几秒、
    三个人轮流开口的单镜，口型和视线必然对不上，而且这一镜会长到 12 秒。
    实际剪出来本来就是三个镜头，这里按人拆开，景别和运镜继承原镜。
    """
    out = []
    for sh in shots:
        lines = [x for x in str(sh.get("dialogue") or "").split(chr(10)) if x.strip()]
        who = []
        for l in lines:
            n = l.split("：")[0].strip()
            if n and n not in who:
                who.append(n)
        if len(who) <= 1:
            out.append(sh)
            continue
        for k, l in enumerate(lines):
            nm = l.split("：")[0].strip()
            out.append(dict(sh, dialogue=l, chars=[nm] if nm else sh.get("chars") or [],
                            action=(sh.get("action") or "") if k == 0 else "",
                            parenthetical=sh.get("parenthetical") if k == 0 else ""))
    for i, sh in enumerate(out, 1):
        sh["shot_id"] = "S%02d" % i
    return out

# 中文语速约每秒 4~5 字；一句台词至少要说得完，不然口型对不上。
_CHARS_PER_SEC = 4.5
_MIN_SEC, _MAX_SEC = 1.5, 12.0


def _assign_seconds(shots, total=None):
    """按文本量加权分秒数，有台词的先保证说得完。"""
    if not shots:
        return
    floors = []
    for sh in shots:
        d = len(re.sub(r"^[^：]*：", "", str(sh.get("dialogue") or ""), flags=re.M))
        say = (d / _CHARS_PER_SEC + 0.8) if d else 0.0
        body = len(str(sh.get("action") or ""))
        floors.append(max(_MIN_SEC, say, min(_MAX_SEC, 1.5 + body / 28.0)))
    if total:
        k = float(total) / max(0.001, sum(floors))
        floors = [max(_MIN_SEC, min(_MAX_SEC, f * k)) for f in floors]
        # 缩放后总量会有偏差，差额补到最长的那一镜上，保证总秒数对得上
        gap = float(total) - sum(floors)
        if abs(gap) > 0.05:
            i = floors.index(max(floors))
            floors[i] = max(_MIN_SEC, floors[i] + gap)
    for sh, f in zip(shots, floors):
        sh["seconds"] = round(f, 1)
