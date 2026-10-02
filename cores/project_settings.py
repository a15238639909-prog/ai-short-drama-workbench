# -*- coding: utf-8 -*-
"""project_settings.py — 项目级设定（8848 漫画板块的项目设定搬到新平台）。

唯一职责：定义默认值、读写 data/stories/<ID>.json 的 project_settings 段；
视频/漫画/小说三条线从这里共读，不各自存一份。
"""
from . import story_core, style_presets


def defaults():
    """唯一负责：项目设定的默认值（选项逐字来自 style_presets）。"""
    return {
        "one_line": "",
        "world_type": "西方",
        "visual_strength": "美型（默认）",
        "style": "网红自然感",
        # 【五维改造 2026-08-29】新增三维，与 world_type / style 合成五维：
        # genre 影片类型＝叙事结构（治「换题材还是一个味」）
        # look  角色审美＝长相与体型（网红脸／沧桑／夸张体型…）
        # pov   视点＝摄影机身份（第三人称／第一视角／自拍…）
        # 留空＝沿用旧行为，老项目不受影响。
        "genre": "",
        "look": "",
        "pov": "电影第三人称",
        "kit": "",
        "extra_requirements": "",
        "content_tendencies": [],
        "custom_content_scale": "",
        "custom_guidance": "",
        # 题材引擎决定节拍形状和秒数配比。留空=自动判定；判错了用户要能手动改回来。
        # 原来它只在第一次生成时写进去，之后永远不再重判，界面上也看不见——
        # 判错一次就锁死了。
        "genre_engine": "",
        # 旧 designDirection/world/characters 只在 get() 迁移读取，不再返回或写入。
        "look_panel": {"look": "", "hero_style": ""},
        "plan": "",
        "episode1_plan": "",
        # 参考图给 H3 时的绑定强度：strict=长相服装以图为准；
        # loose=只对空间光线和身份大方向，表演按剧本走。
        # 【为什么这里必须有】get() 只返回 defaults() 里有的键。
        # 界面存进去了、文件里也有，但读不出来——实测 A/B 跑了一轮
        # 才发现两边生成的是同一个模式，12 分钟 GPU 白费。
        # 默认 loose：实测 loose 的调度和景深明显比 strict 好（strict 会照抄
        # 人设图的正面站姿，画成合影），用户看过 A/B 后定的。
        "ref_mode": "loose",
        # 出视频的三个参数（用户 2026-09-09 定，设定页可改）。
        # 【必须登记在这里】get() 只遍历 defaults() 的键，没登记的
        # **存得进读不出**——ref_mode 那次就是这么踩的（见上面那段注释）。
        # 一律用字符串，配合 <select>.value。
        "video_size_tier": "0.4",
        "video_steps": "10",
        "video_ratio": "16:9",
        # 视频提示词默认先做一次内部导演推演；只影响镜头演法，不改故事和剧本。
        "video_director_mode": "standard",   # P365：深度导演未经验证（两段测试里它输出为空），默认关，设定页可选
        # 故事默认按用户逐话输入直写；split 仅用于打开已封存的旧分话项目。
        "story_mode": "single",
        "initial_story_input": "",
        "story_pace": "适中",
        "prose_words": "3000～4000",
    }


def get(story):
    """唯一负责：读项目设定（缺字段用默认值补，不写坏原数据）。

    【一句话为什么会"写了看不见"】历史上存在两套字段：新链写 story["settings"]，
    这里只读 story["project_settings"]。用户在设定页写完一句话，切回来框是空的。
    现在两套都读，project_settings 优先，settings 兜底，顶层 one_line 最后兜。
    """
    s = story or {}
    ps = s.get("project_settings") or {}
    alt = s.get("settings") or {}
    d = defaults()
    for k in d:
        if k in ps and ps[k] not in (None, "", []):
            d[k] = ps[k]
        elif k in alt and alt[k] not in (None, "", []):
            d[k] = alt[k]
    if not ps.get("story_mode") and not alt.get("story_mode"):
        d["story_mode"] = "split" if any(alt.get(k) or ps.get(k) for k in ("story_plan", "plan_rows", "arrangement")) else "single"
    if not str(d.get("one_line") or "").strip() and s.get("one_line"):
        d["one_line"] = s["one_line"]
    raw_lp = dict((alt.get("look_panel") or {}))
    raw_lp.update(ps.get("look_panel") or {})
    look = str(raw_lp.get("look") or "").strip()
    old3 = [str(raw_lp.get(k) or "").strip()
            for k in ("designDirection", "world", "characters")]
    if not look:
        look = "；".join(x for x in old3 if x)
    d["look_panel"] = {"look": look, "hero_style": str(raw_lp.get("hero_style") or "")}
    d["visual_strength"] = style_presets.normalize_visual_strength(d.get("visual_strength"))
    d["content_tendencies"] = [
        "成人向·情爱（诱惑/暧昧/亲密关系）" if str(x) == "成人向" else str(x)
        for x in (d.get("content_tendencies") or [])]
    return d


def save(story, settings):
    """唯一负责：写项目设定到 data/stories/<ID>.json。

    两套字段同写：project_settings（本模块的正门）和 settings（长篇链在读）。
    只写一套的话，另一条链读到的还是旧值——一句话"写了看不见"就是这么来的。
    """
    # 页面有“完整保存”和“点击生成前的快捷保存”两个入口。快捷保存只带当前可见字段，
    # 过去会把 custom_guidance / look_panel 等没带来的字段整个抹掉。这里统一做深合并。
    incoming = dict(settings or {})
    ps = get(story)
    if ps.get("story_mode") in ("single", "episode", "manual") and ps.get("initial_story_input"):
        incoming["one_line"] = ps["initial_story_input"]
        incoming["initial_story_input"] = ps["initial_story_input"]
    # P316：一句话故事框已删，one_line 由整部规划的想法回填；页面带上来的空值不许把它盖掉（项目183 写正文时故事要求为空）
    if not str(incoming.get("one_line") or "").strip() and str(ps.get("one_line") or (story.get("settings") or {}).get("one_line") or "").strip():
        incoming.pop("one_line", None)
    old_lp = dict(ps.get("look_panel") or {})
    ps.update({k: v for k, v in incoming.items() if k != "look_panel"})
    if "look_panel" in incoming:
        old_lp.update(incoming.get("look_panel") or {})
    look = str(old_lp.get("look") or "").strip()
    if not look:
        look = "；".join(str(old_lp.get(k) or "").strip()
                         for k in ("designDirection", "world", "characters")
                         if str(old_lp.get(k) or "").strip())
    ps["look_panel"] = {"look": look,
                        "hero_style": str(old_lp.get("hero_style") or "")}
    ps["visual_strength"] = style_presets.normalize_visual_strength(ps.get("visual_strength"))
    story["project_settings"] = ps
    alt = dict(story.get("settings") or {})
    alt.update(ps)
    story["settings"] = alt
    if ps.get("one_line"):
        story["one_line"] = ps["one_line"]        # 顶层也同步，列表页显示用
    story_core.save_story(story)
    return ps


def style_of(story):
    """三条成品线取项目画风；保留完整 Krea 名，避免 12 种画风被压成 3 种。"""
    ps = get(story)
    return ps.get("style") or style_presets.DEFAULT_STYLE


# ---------- 设定冻结线（用户定的流程） ----------
# 一句话 → 框架 → 设定 → 【确定设定】→ 才写正文。
# 冻结之前不写一个字正文，所以你随便改都不心疼；冻结之后系统不再回头猜设定。
#
# 冻结的是**世界、画风、人物**。场景不冻——场景由剧本和故事决定，跟着话走。

FROZEN_KEYS = ("world_type", "visual_strength", "style", "extra_requirements",
               "content_tendencies", "custom_content_scale", "look_panel", "one_line")


def lock_state(story):
    s = story or {}
    return {"locked": bool(s.get("settings_locked")),
            "locked_at": s.get("settings_locked_at") or 0,
            "snapshot": s.get("settings_snapshot") or {}}


def lock(story):
    """确定设定。存一份快照，解冻时用它比对哪些话次受影响。"""
    ps = get(story)
    story["settings_locked"] = True
    story["settings_locked_at"] = __import__("time").time()
    story["settings_snapshot"] = {k: copy_value(ps.get(k)) for k in FROZEN_KEYS}
    story_core.save_story(story)
    return lock_state(story)


def copy_value(v):
    import copy as _c
    return _c.deepcopy(v)


def unlock(story, saga=None):
    """解冻。返回哪些话次可能受影响，由用户自己挑要不要重写。

    用户选的是第 3 种做法：可以解冻，解冻后系统列出受影响的话次，
    正文一律保留，改不改由人决定——系统不替他删东西。
    """
    story["settings_locked"] = False
    story_core.save_story(story)
    return {"locked": False, "affected": affected_episodes(story, saga)}


def changed_keys(story):
    """冻结之后，哪些设定项被改动过。"""
    snap = (story or {}).get("settings_snapshot") or {}
    if not snap:
        return []
    now = get(story)
    out = []
    for k in FROZEN_KEYS:
        if snap.get(k) != now.get(k):
            out.append(k)
    return out


LABELS = {"world_type": "世界类型", "visual_strength": "视觉设计强度", "style": "画风",
          "extra_requirements": "不可更改的故事要求", "content_tendencies": "内容尺度",
          "custom_content_scale": "自定义尺度", "look_panel": "整部作品长什么样",
          "one_line": "一句话故事"}
# 哪些设定改了会波及正文（故事层），哪些只波及图（视觉层）。
STORY_KEYS = ("world_type", "extra_requirements", "content_tendencies",
              "custom_content_scale", "one_line")


def affected_episodes(story, saga=None):
    """列出：改了哪些设定、已写正文的哪几话可能对不上、要不要重画图。"""
    ks = changed_keys(story)
    if not ks:
        return {"keys": [], "labels": [], "episodes": [], "need_repaint": False}
    written = [int(e.get("no") or 0) for e in ((saga or {}).get("episodes") or [])
               if str(e.get("body") or "").strip()]
    story_hit = [k for k in ks if k in STORY_KEYS]
    return {"keys": ks,
            "labels": [LABELS.get(k, k) for k in ks],
            "episodes": sorted(written) if story_hit else [],
            "need_repaint": bool([k for k in ks if k not in ("extra_requirements", "one_line")])}
