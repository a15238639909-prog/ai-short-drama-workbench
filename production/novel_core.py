# -*- coding: utf-8 -*-
"""novel_core.py — V4.1 Novel Production：章节规划 / 正文 / State Commit / 伏笔 / 导出（clean-room）。"""
import json, re, time
from pathlib import Path
from cores import store
from cores import narrative_core
from cores import state_core

NOVEL_ROOT = Path(__file__).resolve().parent.parent / "productions" / "novel"

NOVEL_PLAN_SYSTEM = """你是小说编辑。基于整部计划与第1话事件，为同一部作品规划连续 10 章。
每章 brief 写清“本章发生什么”（80-160字），并列出本章必须承接的事件 ID（可空）。
只输出 JSON：{"chapters":[{"title":"章节名","brief":"本章发生什么","event_ids":[]}]}，必须恰好 10 章。"""

NOVEL_WRITE_SYSTEM = """你是小说作家。写一章正文（800-1500字），文风统一、人物一致、有画面感与对白，紧扣本章 brief，承接当前状态与未完成伏笔。
只输出 JSON：
{"content":"本章正文","state_after":{"location":"位置","body_condition":"身体状况","injuries":["伤势"],"held_objects":["持有物"],"knowledge":["本章新增已知信息"],"relationships":{},"events":["本章发生的重要事件"]}}
不要解释。"""

def _extract_json(text):
    t = text.strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    a, b = t.find("{"), t.rfind("}")
    if a >= 0 and b > a:
        try:
            return json.loads(t[a:b + 1])
        except Exception:
            pass
    raise ValueError("模型输出不是 JSON")

def plan_chapters(story, episode, chapter_count=10, temperature=0.7):
    from models import qwen_client
    plan = story.get("master_plan") or {}
    ev_lines = ["%s [%s] %s" % (e["event_id"], e["importance"], e["title"]) for e in episode["events"]]
    user = ("整部计划：核心冲突=%s；主角目标=%s；阶段=%s；必须回收伏笔=%s\n第1话事件：%s\n\n请规划连续 %d 章。"
            % (plan.get("core_conflict", ""), plan.get("protagonist_goal", ""),
               "、".join(p.get("name", "") for p in (plan.get("phases") or [])),
               "、".join(plan.get("must_pay_off") or []), "；".join(ev_lines), int(chapter_count)))
    raw = qwen_client.chat_for("novel_chapter_plan", NOVEL_PLAN_SYSTEM, user, temperature=temperature)
    d = _extract_json(raw)
    chs = d.get("chapters") or []
    if len(chs) != int(chapter_count) or any(len(str(c.get("brief") or "")) < 40 for c in chs):
        raise ValueError("章节规划不符合要求（需 %d 章、brief 不少于 40 字）" % int(chapter_count))
    return chs

def _state_text(state):
    if not state:
        return "故事开始前，无特殊状态"
    cs = state.get("character_states") or {}
    ps = state.get("plot_state") or {}
    parts = []
    for name, s in cs.items():
        parts.append("%s：位置=%s；身体=%s；伤势=%s；持有=%s" % (
            name, s.get("location", ""), s.get("body_condition", ""),
            "、".join(s.get("injuries") or []), "、".join(s.get("held_objects") or [])))
    if ps.get("open_threads"):
        parts.append("未完成伏笔：" + "、".join(ps["open_threads"]))
    return "；".join(parts) or "无特殊状态"

def write_chapter(story, ch, state_text, temperature=0.75):
    from models import qwen_client
    chars = "；".join((c.get("name") or "") + "：" + "，".join(x for x in [c.get("char_type"), c.get("personality")] if x)
                      for c in [] )  # Phase 6 人物来自 story.character_ids 的 asset
    from cores import asset_core
    cs = [asset_core.get_asset(story["story_id"], "characters", cid) for cid in (story.get("character_ids") or [])]
    cs = [c for c in cs if c]
    chars = "；".join((c.get("name") or "") + "：" + "，".join(x for x in [c.get("char_type"), c.get("personality"), c.get("look")] if x) for c in cs)
    user = ("故事：%s\n世界：%s\n人物：%s\n本章：%s：%s\n当前状态：%s\n\n请写本章正文。"
            % (story.get("one_line", ""), "、".join(story.get("world_rules") or []), chars or "无",
               ch.get("title", ""), ch.get("brief", ""), state_text))
    raw = qwen_client.chat_for("novel_chapter", NOVEL_WRITE_SYSTEM, user, temperature=temperature)
    d = _extract_json(raw)
    content = str(d.get("content") or "").strip()
    if len(content) < 500:
        raise ValueError("正文过短（需不少于 500 字）")
    return content, d.get("state_after") or {}

def merge_state(prev, after):
    prev = dict(prev or {})
    for k in ("location", "body_condition", "injuries", "held_objects", "knowledge", "relationships", "events"):
        v = after.get(k)
        if v in (None, ""):
            continue
        prev[k] = v
    return prev

def _latest_partial_outdir(title):
    """找到最近一次“章节还没写完”的目录，实现断点续写/最小重算。"""
    pattern = title + "_*"
    cands = sorted([p for p in NOVEL_ROOT.glob(pattern) if p.is_dir()],
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for c in cands:
        cf = c / "chapters.json"
        if cf.exists():
            chs = store.load_json(cf, [])
            if chs and any(not ch.get("content") for ch in chs):
                return c
    return None

def produce_novel(story, episode_no=1, chapter_count=10, task=None):
    ep = narrative_core.load_episode(story["story_id"], episode_no)
    if not ep or not ep.get("adopted"):
        raise ValueError("请先采用该话")
    title = re.sub(r'[\\/:*?"<>|]', "", story.get("title") or "novel")
    outdir = _latest_partial_outdir(title)
    if outdir is None:
        outdir = NOVEL_ROOT / (title + "_" + time.strftime("%Y%m%d_%H%M%S"))
        outdir.mkdir(parents=True, exist_ok=True)
    chapters = []
    chf = outdir / "chapters.json"
    if chf.exists():
        chapters = store.load_json(chf, [])
    if not chapters:
        chapters = [{"index": i + 1, **c} for i, c in enumerate(plan_chapters(story, ep, int(chapter_count)))]
        store.save_json(chf, chapters)
    state = state_core.latest_state(story["story_id"])
    running = dict(state.get("character_states") or {}) if state else {}
    running_plot = dict(state.get("plot_state") or {}) if state else {}
    open_threads = list(running_plot.get("open_threads") or (story.get("master_plan") or {}).get("must_pay_off") or [])
    state_text = _state_text({"character_states": running, "plot_state": {"open_threads": open_threads}})
    for idx, ch in enumerate(chapters):
        if ch.get("content"):
            continue
        if task:
            task.check_pause()
            task.step("写章节", idx, len(chapters))
        content, after = write_chapter(story, ch, state_text)
        ch["content"] = content
        ch["state_after"] = after
        ch["status"] = "已写"
        # 真正把本章结果串给下一章（连续性），并回收已兑现伏笔
        after = after or {}
        parts = []
        if after.get("location"): parts.append("位置：" + str(after["location"]))
        if after.get("body_condition"): parts.append("身体：" + str(after["body_condition"]))
        if after.get("injuries"): parts.append("伤势：" + "、".join(after["injuries"]))
        if after.get("held_objects"): parts.append("持有：" + "、".join(after["held_objects"]))
        if after.get("knowledge"): parts.append("已知：" + "、".join(after["knowledge"]))
        if after.get("relationships"): parts.append("关系：" + json.dumps(after["relationships"], ensure_ascii=False))
        parts.append("未完成伏笔：" + "、".join(open_threads))
        state_text = "；".join(parts) or "无特殊状态"
        for ev in after.get("events") or []:
            for t in list(open_threads):
                if t and t in str(ev):
                    open_threads.remove(t)
        store.save_json(chf, chapters)
        if task:
            task.checkpoint = {"done": idx + 1}
            task.set(checkpoint=task.checkpoint)
    # 导出文本
    txt = ["# " + (story.get("title") or "小说"), "", "> " + str(story.get("one_line") or ""), ""]
    for ch in chapters:
        txt += ["## 第%d章 %s" % (ch["index"], ch.get("title", "")), "", str(ch.get("content") or ""), ""]
    out = outdir / "novel.md"
    out.write_text("\n".join(txt), encoding="utf-8")
    rec = {"production_id": store.seq_id("NOVEL", int(time.time())), "story_id": story["story_id"],
           "episode_no": int(episode_no), "chapters": len([c for c in chapters if c.get("content")]),
           "file": str(out), "open_threads": open_threads, "created": time.time()}
    store.save_json(store.DATA / "productions" / (rec["production_id"] + ".json"), rec)
    return rec
