# -*- coding: utf-8 -*-
"""V4.2.2 Canon Guard：生成前硬拦截（Production 进 H3 前强制校验，FAIL 即阻断）。"""
import json, time, uuid
from pathlib import Path
from . import store, story_core, narrative_core, state_core, asset_core

IRREVERSIBLE_ANATOMY = {"missing", "blind", "amputated"}
IRREVERSIBLE_TYPES = ("missing limb", "断肢", "缺失", "失明", "blindness", "death", "destroyed", "revealed secret")
LEGAL_REVERSAL_EVENTS = ("regeneration_event", "surgery_event", "prosthetic_event", "healing_magic_event",
                         "resurrection_event", "repair_event", "reacquire_event", "redress_event")

def _norm(s):
    return str(s or "").strip()

def check(request):
    """request: {story_id, sequence_id, unit_ids, event_ids, director_id, character_ids,
                 state_snapshot_id, intended_state_deltas, new_events, prompt_text, assets_ok}
    返回 canon_guard_report.json 结构；violations 非空即 FAIL（不提交 H3）。"""
    rid = _norm(request.get("request_id")) or ("REQ_" + uuid.uuid4().hex[:8])
    story_id = request["story_id"]
    violations, warnings = [], []
    story = story_core.get_story(story_id)
    if not story:
        violations.append("story 不存在：%s" % story_id)
        return _report(rid, story_id, request, violations, warnings)
    ep = narrative_core.load_episode(story_id, story.get("current_episode") or 1)
    ledger_event_ids = {e.get("event_id") for e in (ep or {}).get("events", [])}
    ledger_unit_ids = {u.get("unit_id") for u in (ep or {}).get("units", [])}
    char_ids = set(story.get("character_ids") or [])

    for eid in request.get("event_ids", []):
        if _norm(eid) and eid not in ledger_event_ids:
            violations.append("event_id 不在 Canon Event Ledger：%s" % eid)
    for uid in request.get("unit_ids", []):
        if _norm(uid) and uid not in ledger_unit_ids:
            violations.append("unit_id 不在 Canon Units：%s" % uid)
        elif uid in ledger_unit_ids:
            u = next((x for x in (ep or {}).get("units", []) if x.get("unit_id") == uid), {})
            for eid in (u.get("must_include_event_ids") or []):
                if eid not in ledger_event_ids:
                    violations.append("unit %s 引用无来源事件 %s" % (uid, eid))
    for cid in request.get("character_ids", []):
        if _norm(cid) and cid not in char_ids:
            violations.append("character_id 不存在：%s" % cid)
    # 新增 core event 拦截
    for ev in request.get("new_events", []):
        if (ev or {}).get("importance") == "core":
            violations.append("Production 试图创建新的 Core Event：%s" % ev.get("title"))
    # 不可逆状态逆转检查（anatomy）
    legal = [x for x in request.get("event_ids", []) if x in LEGAL_REVERSAL_EVENTS]
    deltas = request.get("intended_state_deltas") or {}
    anatomy = deltas.get("anatomy") or {}
    for cid, parts in anatomy.items():
        for part, state in parts.items():
            if str(state).lower() in IRREVERSIBLE_ANATOMY:
                continue  # 保持缺失/失明 = 合法
            # 若想从 missing/blind 恢复为 intact/seeing：必须出现合法恢复事件
            if not legal:
                violations.append("不可逆状态逆转：%s.%s -> %s，缺少合法恢复事件（%s）"
                                  % (cid, part, state, "/".join(LEGAL_REVERSAL_EVENTS)))
    # 服装逆转检查：outerwear removed -> worn 需要 redress/redress_event
    clothing = deltas.get("clothing") or {}
    for cid, parts in clothing.items():
        for part, state in parts.items():
            if part == "outerwear" and str(state).lower() == "worn" and not legal:
                violations.append("服装无因果恢复：%s.%s -> worn，缺少 redress 事件" % (cid, part))
    # 持有物：道具消失需要事件
    held = deltas.get("held_objects") or {}
    for cid, items in held.items():
        if items is not None and "KEY_001" in (state_core.latest_state(story_id) or {}).get(
                "character_states", {}).get(cid, {}).get("held_objects", []) and "KEY_001" not in items:
            if not legal:
                violations.append("持有物无因果消失：%s 失去 KEY_001" % cid)
    # Prompt 未授权内容（粗检：未知角色名）
    prompt = _norm(request.get("prompt_text"))
    known_names = {asset_core.get_asset(story_id, "characters", c).get("name", "")
                   for c in char_ids if asset_core.get_asset(story_id, "characters", c)}
    if prompt:
        for n in known_names:
            if n and n not in prompt and request.get("character_ids"):
                warnings.append("Prompt 未提及已参与角色显示名：%s" % n)
    if not request.get("assets_ok", True):
        violations.append("Reference Pack 缺必需资产（assets_ok=False）")
    result = "FAIL" if violations else "PASS"
    report = {
        "request_id": rid,
        "story_id": story_id,
        "sequence_id": request.get("sequence_id", ""),
        "state_snapshot_id": request.get("state_snapshot_id", ""),
        "checked_event_ids": sorted(set(request.get("event_ids", [])) & ledger_event_ids),
        "checked_character_ids": sorted(set(request.get("character_ids", [])) & char_ids),
        "violations": violations,
        "warnings": warnings,
        "result": result,
        "checked_at": time.time(),
    }
    return report

def _report(rid, story_id, request, violations, warnings):
    return {
        "request_id": rid, "story_id": story_id,
        "sequence_id": request.get("sequence_id", ""),
        "state_snapshot_id": request.get("state_snapshot_id", ""),
        "checked_event_ids": [], "checked_character_ids": [],
        "violations": violations, "warnings": warnings,
        "result": "FAIL" if violations else "PASS", "checked_at": time.time(),
    }

def save_report(report, sub="01_canon_guard"):
    out = Path(__file__).resolve().parent.parent / "reports" / "v422" / "final_review" / sub
    out.mkdir(parents=True, exist_ok=True)
    name = "canon_guard_report_%s.json" % report.get("request_id", report.get("sequence_id", "x"))
    (out / name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out / name
