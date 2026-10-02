# -*- coding: utf-8 -*-
"""V4.2.1 Canon Core：唯一事实链 / 稳定 ID / Canon State Snapshot / 完整性校验。"""
import json, time
from pathlib import Path
from . import store, story_core, narrative_core, state_core, asset_core

REPORT_ROOT = Path(__file__).resolve().parent.parent / "reports" / "v421"

NAME_TO_CHAR = {
    "艾达": "CHAR_艾达灰鸦", "艾达·灰鸦": "CHAR_艾达灰鸦", "阿达": "CHAR_艾达灰鸦",
    "卡恩": "CHAR_卡恩雾刃", "卡恩·雾刃": "CHAR_卡恩雾刃",
    "阿莫斯": "CHAR_老仆阿莫斯", "老仆": "CHAR_老仆阿莫斯", "老仆阿莫斯": "CHAR_老仆阿莫斯",
}

LOCATION_IDS = {
    "古堡一楼大厅入口": "LOC_古堡一楼大厅入口",
    "古堡一楼大厅": "LOC_古堡一楼大厅",
    "古堡一楼餐厅": "LOC_古堡一楼餐厅",
    "灰鸦堡宴会厅": "SCENE_灰鸦堡宴会厅",
}

def char_id(name):
    return NAME_TO_CHAR.get(str(name or "").strip(), str(name or "").strip())

def location_id(loc):
    return LOCATION_IDS.get(str(loc or "").strip(), str(loc or "").strip())

def migrate_episode_ids(story_id, episode_no=1):
    """给 episode 事件/单元补充 participant_ids / location_id，名字仅用于显示。"""
    ep = narrative_core.load_episode(story_id, episode_no)
    if not ep:
        return None
    for e in ep.get("events", []):
        e["participant_ids"] = [char_id(x) for x in (e.get("participants") or [])]
        e["location_id"] = location_id(e.get("location", ""))
        for inj in (e.get("state") or {}).get("injuries", []):
            inj["character_id"] = char_id(inj.get("character", ""))
        for cc in (e.get("state") or {}).get("clothing_changes", []):
            cc["character_id"] = char_id(cc.get("character", ""))
        for ac in (e.get("state") or {}).get("anatomy_changes", []):
            ac["character_id"] = char_id(ac.get("character", ""))
    for u in ep.get("units", []):
        u["character_ids"] = [char_id(x) for x in (u.get("character_goals") or {}).keys()]
    ep["updated"] = time.time()
    narrative_core.save_episode(ep)
    return ep

def build_story_chain(story_id):
    """唯一事实链：one_line → Preview(Candidate/Adopted) → Plan → Events → Units → Director → Productions。"""
    st = story_core.get_story(story_id)
    if not st:
        return None
    ep = narrative_core.load_episode(story_id, st.get("current_episode") or 1)
    stage = st.get("production_stage", "draft")
    prods = []
    for p in (store.DATA / "productions").glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (d or {}).get("story_id") == story_id:
            prods.append(d.get("production_id"))
    chain = {
        "story_id": story_id,
        "one_line": st.get("one_line", ""),
        "preview_candidate": st.get("preview_summary", ""),
        "preview_adopted": stage in ("adopted", "production"),
        "master_plan_canon": bool(st.get("master_plan")),
        "canon_plan_phases": [p.get("name") for p in (st.get("master_plan") or {}).get("phases", [])],
        "episode_no": st.get("current_episode", 1),
        "canon_events": [{
            "event_id": e.get("event_id"), "title": e.get("title"), "importance": e.get("importance"),
            "participant_ids": e.get("participant_ids") or [char_id(x) for x in (e.get("participants") or [])],
            "location_id": e.get("location_id") or location_id(e.get("location", "")),
            "status": e.get("status"),
        } for e in (ep or {}).get("events", [])],
        "canon_units": [{
            "unit_id": u.get("unit_id"), "purpose": u.get("purpose"),
            "must_include_event_ids": u.get("must_include_event_ids", []),
            "adopted": u.get("adopted", False),
        } for u in (ep or {}).get("units", [])],
        "adopted_director_plan": bool(st.get("previs")),
        "director_plan_event_refs": sorted({
            eid for s in (st.get("previs") or {}).get("shots", []) for eid in (s.get("event_ids") or [])}),
        "canon_state_id": st.get("last_state_id"),
        "productions": prods,
    }
    return chain

def build_state_snapshot(story_id):
    """单角色单一事实源 Canon State Snapshot（character_id / location_id / anatomy / injuries / clothing…）。"""
    st = story_core.get_story(story_id)
    snap = state_core.latest_state(story_id) or {}
    ep = narrative_core.load_episode(story_id, st.get("current_episode") or 1)
    chars = {}
    for cid in (st.get("character_ids") or []):
        c = asset_core.get_asset(story_id, "characters", cid)
        if not c:
            continue
        name = c.get("name", "")
        chars[cid] = {
            "character_id": cid, "display_name": name,
            "story_time": (snap.get("story_clock") or {}).get("day", 1),
            "location_id": "",
            "anatomy": (snap.get("anatomy_states") or {}).get(name, {}),
            "injuries": [],
            "healing": {},
            "clothing": (snap.get("clothing_states") or {}).get(name, {}),
            "wardrobe_id": "",
            "wetness": "", "dirt": "", "blood": "",
            "held_objects": [], "mobility": "", "consciousness": "清醒",
            "knowledge": [], "relationships": {}, "current_goal": "",
            "last_event_id": "",
        }
        injs = (snap.get("injury_states") or {}).get(name, [])
        for inj in injs:
            chars[cid]["injuries"].append({
                "injury_id": inj.get("injury_id"), "type": inj.get("type"),
                "location": inj.get("location"), "severity": inj.get("severity"),
                "created_story_time": inj.get("created_story_time"),
                "expected_recovery": inj.get("expected_recovery"),
                "status": inj.get("status"), "medical_state": inj.get("medical_state"),
                "source_event_id": inj.get("source_event"),
            })
        # 定位：取该角色最后参与的 canon 事件 location_id
        last_eid = ""
        for e in (ep or {}).get("events", []):
            if cid in (e.get("participant_ids") or []):
                chars[cid]["location_id"] = e.get("location_id", "")
                last_eid = e.get("event_id", "")
        chars[cid]["last_event_id"] = last_eid
        cs = (snap.get("character_states") or {}).get(name, {})
        chars[cid]["current_goal"] = cs.get("current_goal", "")
        chars[cid]["knowledge"] = cs.get("knowledge", [])
        chars[cid]["relationships"] = cs.get("relationships", {})
        chars[cid]["held_objects"] = cs.get("held_objects", [])
    snapshot = {
        "snapshot_id": "CANON_STATE_00001",
        "story_id": story_id,
        "story_clock": snap.get("story_clock"),
        "resolved_event_ids": (snap.get("plot_state") or {}).get("resolved_events", []),
        "open_threads": (snap.get("plot_state") or {}).get("open_threads", []),
        "consequences": snap.get("consequences", []),
        "characters": chars,
        "source_state_id": snap.get("state_id"),
        "built_at": time.time(),
    }
    return snapshot

def validate_ids(story_id):
    """完整性校验：悬空引用检测。"""
    st = story_core.get_story(story_id)
    ep = narrative_core.load_episode(story_id, st.get("current_episode") or 1)
    dangling = []
    char_ids = set(st.get("character_ids") or [])
    event_ids = {e.get("event_id") for e in (ep or {}).get("events", [])}
    unit_ids = {u.get("unit_id") for u in (ep or {}).get("units", [])}
    for e in (ep or {}).get("events", []):
        for cid in (e.get("participant_ids") or []):
            if cid and cid not in char_ids:
                dangling.append("event %s participant %s" % (e.get("event_id"), cid))
    for u in (ep or {}).get("units", []):
        for eid in (u.get("must_include_event_ids") or []):
            if eid and eid not in event_ids:
                dangling.append("unit %s must_include %s" % (u.get("unit_id"), eid))
    for mf in (store.DATA / "manifests").glob("*.json"):
        m = json.loads(mf.read_text(encoding="utf-8"))
        if m.get("story_id") != story_id:
            continue
        for eid in (m.get("event_ids") or []):
            if eid and eid not in event_ids:
                dangling.append("manifest %s event %s" % (m.get("manifest_id"), eid))
        for nid in (m.get("source_narrative_ids") or []):
            if nid and nid not in unit_ids:
                dangling.append("manifest %s unit %s" % (m.get("manifest_id"), nid))
    for v in asset_core.list_assets(story_id, "visuals"):
        if v.get("owner_id") and v.get("kind", "").startswith("character") and v["owner_id"] not in char_ids:
            dangling.append("visual %s owner %s" % (v.get("visual_id"), v.get("owner_id")))
    return {"ok": not dangling, "dangling": dangling,
            "char_ids": sorted(char_ids), "event_ids": sorted(event_ids), "unit_ids": sorted(unit_ids)}

def write_canon_artifacts(story_id):
    out = REPORT_ROOT / "final_review"
    out.mkdir(parents=True, exist_ok=True)
    chain = build_story_chain(story_id)
    snap = build_state_snapshot(story_id)
    if chain:
        (out / "canon_story_chain.json").write_text(json.dumps(chain, ensure_ascii=False, indent=2), encoding="utf-8")
    if snap:
        (out / "canon_state_snapshot.json").write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    integrity = validate_ids(story_id)
    (out / "canon_integrity.json").write_text(json.dumps(integrity, ensure_ascii=False, indent=2), encoding="utf-8")
    return chain, snap, integrity
