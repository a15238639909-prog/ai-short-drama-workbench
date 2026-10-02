# -*- coding: utf-8 -*-
"""story_core.py — Story / MasterPlan（V4.1 clean-room）。"""
import json, re, time, uuid
from . import store

MASTER_PLAN_SYSTEM = """你是故事策划。根据用户的一句话故事，生成整部作品计划（Master Plan）。
只输出一个合法 JSON，不要解释、不要 Markdown 代码块：
{
  "core_conflict": "核心冲突",
  "protagonist_goal": "主角长期目标",
  "relationships": [{"a": "角色", "b": "角色", "relation": "关系", "note": "说明"}],
  "world_rules": ["世界硬规则，不随剧情漂移"],
  "phases": [{"name": "阶段名", "goal": "阶段目标", "key_turns": ["关键转折"]}],
  "key_twists": ["关键转折"],
  "important_reveals": ["重要揭示"],
  "climax_direction": "高潮方向",
  "ending_direction": "结局方向",
  "must_pay_off": ["必须回收的重要伏笔"]
}
短篇可精简（phases 1-2 个），长篇必须完整。必须保留用户一句话的核心语义，不得改掉原始核心。"""

def story_path(sid):
    return store.DATA / "stories" / (sid + ".json")

def create_story(one_line, title="", mode="short"):
    # 【编号必须避开所有已存在的项目】原来是「文件数＋1」——只要删过项目，
    # 文件数就比最大编号小，新建出来的 ID 会撞上已有项目并**直接把它覆盖**
    # （2026-08-29 实测：移走 062/063 后新建，连续三次都算出 064，
    # 把用户的项目「异世界的美妙之路」整个盖掉了）。改成最大编号＋1，
    # 再逐个跳过已占用的号，绝不复用。
    used = set()
    for p in (store.DATA / "stories").glob("STORY_*.json"):
        m = re.match(r"STORY_(\d+)$", p.stem)
        if m:
            used.add(int(m.group(1)))
    n = (max(used) + 1) if used else 1
    while n in used or story_path("STORY_%03d" % n).exists():
        n += 1
    sid = "STORY_%03d" % n
    story = {
        # 名字没填 → 按数字编号（用户定），不截一句话
        "story_id": sid, "title": title or ("项目%d" % n),
        "one_line": one_line, "mode": mode, "world_rules": [],
        "master_plan": None, "character_ids": [], "scene_ids": [],
        "current_episode": None, "production_stage": "draft",
        "previs": None, "created": time.time(), "updated": time.time(),
    }
    # 设置页里定的「全局默认」（世界/画风/尺度等）带进新项目
    try:
        gd = store.load_json(store.DATA / "global_defaults.json", {}) or {}
        if gd:
            story["settings"] = dict(gd)
    except Exception:
        pass
    story.setdefault("settings", {}).update(story_mode="single", one_line=one_line)
    store.save_json(story_path(sid), story)
    return story

def list_stories(include_archived=False):
    out = []
    for p in (store.DATA / "stories").glob("*.json"):
        d = store.load_json(p, {})
        if d and (include_archived or not d.get("archived")):
            out.append({"story_id": d.get("story_id"), "title": d.get("title"),
                        "one_line": d.get("one_line"), "mode": d.get("mode"),
                        "updated": d.get("updated"), "has_plan": bool(d.get("master_plan"))})
    return sorted(out, key=lambda x: x.get("updated", 0), reverse=True)

def get_story(sid):
    return store.load_json(story_path(sid), None)

def save_story(story):
    story["updated"] = time.time()
    store.save_json(story_path(story["story_id"]), story)
    return story

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

def generate_master_plan(story, temperature=0.7):
    from models import qwen_client
    user = "一句话故事：%s\n模式：%s\n请生成整部作品计划。" % (story.get("one_line", ""), story.get("mode", "short"))
    raw = qwen_client.chat_for("master_plan", MASTER_PLAN_SYSTEM, user, temperature=temperature)
    plan = _extract_json(raw)
    for key in ("core_conflict", "protagonist_goal", "phases", "must_pay_off"):
        if key not in plan:
            raise ValueError("Master Plan 缺少字段：" + key)
    if not isinstance(plan.get("phases"), list) or not plan.get("phases"):
        raise ValueError("Master Plan phases 为空")
    if not str(plan.get("core_conflict") or "").strip():
        raise ValueError("core_conflict 为空")
    story["master_plan"] = plan
    if not story.get("world_rules"):
        story["world_rules"] = plan.get("world_rules") or []
    save_story(story)
    return plan

# ================= v4.2 Story Architect 预览 =================
PREVIEW_SYSTEM = """你是 Story Architect。根据一句话，写 600-1200 字的故事摘要（一分钟可读完）。
必须包含：主角是谁、现在要什么、为什么必须行动、核心冲突、主要人物关系、故事大概怎么发展、关键转折、高潮、结局方向，以及黑暗/恐怖/成人关系/暴力在这个故事里的功能（不是只列标签）。
使用成熟黑暗叙事：允许成人关系的性感张力（低频、有场景理由、推动剧情）、恐怖信息差、暴力因果与后果，但不要撰写露骨性行为或性器官细节。
只输出正文，不要标题和解释。"""

def generate_preview(story, temperature=0.7):
    from models import qwen_client
    raw = qwen_client.chat_for("story_preview", PREVIEW_SYSTEM,
                               "一句话：" + str(story.get("one_line", "")) + "\n模式：" + str(story.get("mode", "short")) + "\n请写故事摘要。",
                               temperature=temperature)
    raw = raw.strip()
    if len(raw) < 400:
        raise ValueError("故事摘要过短")
    story["preview_summary"] = raw
    save_story(story)
    return raw
