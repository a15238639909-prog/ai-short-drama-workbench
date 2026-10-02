# -*- coding: utf-8 -*-
"""narrative_core.py — EpisodeBrief / Event Ledger / Narrative Unit / Coverage（V4.1 clean-room）。"""
import json, time
from . import store

EPISODE_SYSTEM = """你是编剧。基于整部作品计划，为第 N 话/章生成：本话 Brief + 关键事件 + 叙事单元（Narrative Units）。
只输出一个合法 JSON：
{
  "brief": {"title": "本话标题", "synopsis": "本话具体发生什么（事件范围，不写镜头/漫画格/文学正文）"},
  "events": [
    {"title": "事件名", "desc": "发生什么", "importance": "core|support",
     "participants": ["角色名"], "location": "地点",
     "state": {"story_time": "第1日，夜", "clothing_changes": [{"character": "角色名", "part": "upper_body", "state": "removed"}],
               "injuries": [{"character": "角色名", "type": "割伤", "location": "左前臂", "severity": "轻", "bleeding": true,
                             "pain": "中", "mobility_effect": "左臂活动受限", "created_story_time": "第1日，夜",
                             "expected_recovery": "第3日", "medical_state": "未处理"}],
               "anatomy_changes": [{"character": "角色名", "part": "left_eye", "state": "blind"}],
               "consequences": [{"irreversibility": "high", "type": "背叛", "text": "……"}]}}
  ],
  "units": [
    {"purpose": "这一单元在故事里负责什么", "takeaway": "观众/读者看完必须知道或感受到什么",
     "start_state": "开始状态", "character_goals": {"角色名": "目标"},
     "obstacle": "阻力", "event_titles": ["必须发生的事件名（必须引用上面的 events）"],
     "key_moment": "关键瞬间", "state_change": "谁/什么发生什么变化",
     "result": "结果", "caused_by": "承接什么", "leads_to": "引出什么"}
  ]
}
规则：
- Core Event 必须至少被一个 Unit 引用；事件不得重复执行；不得出现无来源的新事件。
- 不得把整部计划的后续转折提前到第 1 话；第 1 话只做本话范围。
- 事件和 Unit 的先后顺序即因果顺序；换媒介不得改剧情。
- v4.2 成熟黑暗叙事规则：事件必须遵守因果链（事件改变状态，后续继承）；服装被脱下后不得在无重新穿上事件时自动恢复；伤势/断肢/后果必须进入 state；性感暗示要低频、有场景理由并推动人物或剧情，禁止每场机械重复同一种手法；恐怖使用信息差与铺垫；暴力必须有起因、冲击和后果；不得撰写露骨性行为或性器官细节（该层由用户自定义内容规则单独提供）。"""

def episode_path(story_id, episode_no):
    return store.DATA / "narrative" / story_id / ("episode_%03d.json" % int(episode_no))

def generate_episode(story, episode_no=1, temperature=0.7):
    from models import qwen_client
    plan = story.get("master_plan") or {}
    user = ("作品计划：\n核心冲突：%s\n主角长期目标：%s\n阶段：%s\n关键转折：%s\n必须回收伏笔：%s\n\n请生成第 %d 话的 Brief+Events+Units。"
            % (plan.get("core_conflict", ""), plan.get("protagonist_goal", ""),
               json.dumps(plan.get("phases", []), ensure_ascii=False),
               json.dumps(plan.get("key_twists", []), ensure_ascii=False),
               json.dumps(plan.get("must_pay_off", []), ensure_ascii=False), int(episode_no)))
    raw = qwen_client.chat_for("episode", EPISODE_SYSTEM, user, temperature=temperature)
    d = story_core_extract(raw)
    brief = d.get("brief") or {}
    events_raw = d.get("events") or []
    units_raw = d.get("units") or []
    if not brief.get("synopsis") or not events_raw or not units_raw:
        raise ValueError("Episode 生成缺少 brief/events/units")
    # 分配稳定 ID
    events = []
    for i, ev in enumerate(events_raw, 1):
        events.append({
            "event_id": store.seq_id("EVENT", i),
            "story_id": story["story_id"],
            "importance": "core" if str(ev.get("importance", "core")).lower() == "core" else "support",
            "title": ev.get("title") or "", "desc": ev.get("desc") or "",
            "participants": ev.get("participants") or [], "location": ev.get("location") or "",
            "state_changes": ev.get("state_changes") or [],
            "state": ev.get("state") or {},
            "status": "planned", "canon_source": "",
        })
    title_index = {e.get("title"): e["event_id"] for e in events}
    units = []
    for i, un in enumerate(units_raw, 1):
        event_titles = un.get("event_titles") or []
        ids = [title_index.get(t) for t in event_titles if t in title_index]
        units.append({
            "unit_id": store.seq_id("UNIT", i),
            "story_id": story["story_id"],
            "episode_no": int(episode_no),
            "purpose": un.get("purpose") or "", "takeaway": un.get("takeaway") or "",
            "start_state": un.get("start_state") or "", "character_goals": un.get("character_goals") or {},
            "obstacle": un.get("obstacle") or "", "must_include_event_ids": ids,
            "key_moment": un.get("key_moment") or "", "state_change": un.get("state_change") or "",
            "result": un.get("result") or "", "caused_by": un.get("caused_by") or "",
            "leads_to": un.get("leads_to") or "", "importance": "core" if any(
                e.get("importance") == "core" for e in events if e["event_id"] in ids) else "support",
            "adopted": False,
        })
    data = {"story_id": story["story_id"], "episode_no": int(episode_no),
            "brief": {"brief_id": store.seq_id("EPISODE", int(episode_no)),
                      "title": brief.get("title") or "", "synopsis": brief.get("synopsis") or ""},
            "events": events, "units": units, "adopted": False,
            "created": time.time(), "updated": time.time()}
    store.save_json(episode_path(story["story_id"], episode_no), data)
    story["current_episode"] = int(episode_no)
    from . import story_core
    story_core.save_story(story)
    return data

def story_core_extract(text):
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

def load_episode(story_id, episode_no):
    return store.load_json(episode_path(story_id, episode_no), None)

def save_episode(ep):
    store.save_json(episode_path(ep["story_id"], ep.get("episode_no") or 1), ep)
    return ep

def adopt_episode(story_id, episode_no):
    ep = load_episode(story_id, episode_no)
    if not ep:
        raise ValueError("Episode 不存在")
    ep["adopted"] = True
    for u in ep["units"]:
        u["adopted"] = True
    for e in ep["events"]:
        if any(u["adopted"] for u in ep["units"] if e["event_id"] in u.get("must_include_event_ids", [])):
            e["status"] = "happened"
    ep["updated"] = time.time()
    store.save_json(episode_path(story_id, episode_no), ep)
    return ep

class CoverageEngine:
    def report(self, story_id, episode_no):
        ep = load_episode(story_id, episode_no)
        if not ep:
            return {"ok": False, "errors": ["Episode 不存在"]}
        events = ep["events"]
        units = ep["units"]
        core = [e for e in events if e.get("importance") == "core"]
        errors, warnings = [], []
        covered = set()
        for u in units:
            for eid in u.get("must_include_event_ids", []):
                if eid in covered:
                    errors.append("事件 %s 被多个 Unit 重复执行" % eid)
                covered.add(eid)
        for e in core:
            if e["event_id"] not in covered:
                errors.append("Core Event %s（%s）未被任何 Unit 覆盖" % (e["event_id"], e.get("title")))
        ledger_ids = {e["event_id"] for e in events}
        for u in units:
            for eid in u.get("must_include_event_ids", []):
                if eid not in ledger_ids:
                    errors.append("Unit %s 引用了无来源事件 %s" % (u["unit_id"], eid))
        for u in units:
            if not str(u.get("purpose") or "").strip() or not str(u.get("result") or "").strip():
                warnings.append("Unit %s 缺少 purpose 或 result" % u["unit_id"])
        order_ok = True
        seen = []
        for u in units:
            for eid in u.get("must_include_event_ids", []):
                if eid in seen:
                    continue
                seen.append(eid)
        ids = [e["event_id"] for e in events]
        pos = {eid: i for i, eid in enumerate(ids)}
        last = -1
        for eid in seen:
            if eid in pos and pos[eid] < last:
                order_ok = False
                errors.append("事件顺序违反 Event Ledger 顺序：" + eid)
            last = max(last, pos.get(eid, last))
        return {"ok": not errors, "errors": errors, "warnings": warnings,
                "core_total": len(core), "core_covered": len([e for e in core if e["event_id"] in covered]),
                "units": len(units), "events": len(events)}
