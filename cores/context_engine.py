# -*- coding: utf-8 -*-
"""context_engine.py — 统一上下文组装（V4.1：每次只拿当前任务真正需要的信息）。"""
import json

def story_package(story):
    return {
        "story_id": story.get("story_id"), "title": story.get("title"),
        "one_line": story.get("one_line"), "mode": story.get("mode"),
        "world_rules": story.get("world_rules") or [],
    }

def master_plan_package(story):
    return story.get("master_plan") or {}

def episode_package(story_id, episode_no):
    from . import narrative_core
    ep = narrative_core.load_episode(story_id, episode_no)
    if not ep:
        return None
    return {"brief_id": ep["brief"].get("brief_id"), "title": ep["brief"].get("title"),
            "synopsis": ep["brief"].get("synopsis"), "event_count": len(ep["events"]),
            "unit_count": len(ep["units"])}

def character_package(story, character_ids=None):
    from . import asset_core
    chars = []
    for cid in (character_ids or story.get("character_ids") or []):
        c = asset_core.get_asset(story["story_id"], "characters", cid)
        if c:
            chars.append({
                "name": c.get("name"), "char_type": c.get("char_type"),
                "identity_anchor": c.get("identity_anchor"),
                "behavior_anchor": c.get("behavior_anchor"),
                "clothing": c.get("clothing"), "frozen": c.get("frozen", False),
            })
    return {"characters": chars}

def state_package(story_id):
    from . import state_core
    st = state_core.latest_state(story_id)
    if not st:
        return None
    return {"state_id": st.get("state_id"), "story_clock": st.get("story_clock"),
            "plot": st.get("plot_state"), "characters": st.get("character_states"),
            "clothing_states": st.get("clothing_states"),
            "injury_states": st.get("injury_states"),
            "anatomy_states": st.get("anatomy_states"),
            "consequences": st.get("consequences")}

def build_context(task_type, story, episode_no=None, extra=None):
    """返回 {system, user} 最小上下文包。"""
    sp = story_package(story)
    if task_type == "master_plan":
        return {"system": "你是故事策划。", "user": json.dumps(sp, ensure_ascii=False)}
    if task_type == "episode":
        mp = master_plan_package(story)
        return {"system": "你是编剧。", "user": json.dumps({"story": sp, "plan": mp}, ensure_ascii=False)}
    if task_type == "narrative":
        ep = episode_package(story["story_id"], episode_no)
        return {"system": "你是编剧。", "user": json.dumps({"story": sp, "episode": ep, "extra": extra or {}}, ensure_ascii=False)}
    if task_type == "ask_ai":
        st = state_package(story["story_id"])
        chars = character_package(story)
        return {"system": "你是创作助手。", "user": json.dumps({"story": sp, "characters": chars, "state": st, "extra": extra or {}}, ensure_ascii=False)}
    return {"system": "", "user": ""}
