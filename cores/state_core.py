# -*- coding: utf-8 -*-
"""state_core.py — StateSnapshot / State Commit（V4.1 clean-room，只有 Adopt 才写 canon）。"""
import re, time
from . import store

def state_path(story_id, state_id):
    return store.DATA / "states" / story_id / (state_id + ".json")

def latest_state(story_id):
    d = store.DATA / "states" / story_id
    if not d.exists():
        return None
    files = sorted(d.glob("STATE_*.json"))
    if not files:
        return None
    return store.load_json(files[-1], None)

def commit_state(story, episode_no, source_note=""):
    """由 Adopt 后的 Episode 生成 canonical StateSnapshot。"""
    from . import narrative_core
    ep = narrative_core.load_episode(story["story_id"], episode_no)
    if not ep or not ep.get("adopted"):
        raise ValueError("只有已采用的 Episode 才能 State Commit")
    seq = len(list((store.DATA / "states" / story["story_id"]).glob("STATE_*.json"))) + 1
    state_id = store.seq_id("STATE", seq)
    plan = story.get("master_plan") or {}
    resolved = sorted(set([e["event_id"] for e in ep["events"] if e.get("status") == "happened"]))
    # 未完成故事：master_plan.must_pay_off 中未被本话兑现的关键词
    open_threads = list(plan.get("must_pay_off") or [])
    resolved_text = " ".join(resolved)
    for k in list(open_threads):
        # 若本话关键瞬间/结果文本包含伏笔名，视为已回收
        if k and any(k in str(u.get("key_moment") or "") or k in str(u.get("result") or "")
                     for u in ep["units"]):
            open_threads.remove(k)
    character_states = {}
    for u in ep["units"]:
        for name, goal in (u.get("character_goals") or {}).items():
            key = name.strip()
            if not key:
                continue
            cs = character_states.setdefault(key, {
                "location": u.get("start_state") or "", "body_condition": "正常",
                "injuries": [], "wardrobe_id": "", "temporary_appearance": "",
                "held_objects": [], "abilities": [], "knowledge": [],
                "unknowns": [], "relationships": {}, "current_goal": goal,
                "last_event_id": ""})
            cs["current_goal"] = goal
            if not cs.get("location"):
                cs["location"] = u.get("start_state") or ""
    scene_states = {}
    for e in ep["events"]:
        loc = e.get("location") or ""
        if loc:
            ss = scene_states.setdefault(loc, {"time": "", "weather": "", "damage": "",
                                               "active_objects": [], "character_positions": {},
                                               "temporary_lighting_state": "", "last_event_id": e["event_id"]})
            ss["last_event_id"] = e["event_id"]
    # v4.2 Causal State：服装/伤势/断肢/时间/后果由引擎处理，后续内容继承
    prev = latest_state(story["story_id"])
    prev_resolved = set(((prev or {}).get("plot_state") or {}).get("resolved_events", []))
    causal = CausalStateEngine().apply_episode(prev or {}, ep, prev_resolved)
    resolved = sorted(set(resolved) | prev_resolved)
    snapshot = {
        "state_id": state_id, "story_id": story["story_id"], "episode_no": int(episode_no),
        "version": seq, "canonical": True, "source_note": source_note,
        "world_state": {"world_rules": story.get("world_rules") or plan.get("world_rules") or []},
        "character_states": character_states,
        "scene_states": scene_states,
        "plot_state": {"resolved_events": resolved, "open_threads": open_threads,
                       "foreshadowing": [], "promises": [], "secrets": [], "deadlines": []},
        "story_clock": causal.get("story_clock"),
        "clothing_states": causal.get("clothing_states"),
        "injury_states": causal.get("injury_states"),
        "anatomy_states": causal.get("anatomy_states"),
        "consequences": causal.get("consequences"),
        "created": time.time(),
    }
    store.save_json(state_path(story["story_id"], state_id), snapshot)
    story["last_state_id"] = state_id
    from . import story_core
    story_core.save_story(story)
    return snapshot

# ================= v4.2 Causal State Engine + Custom Content Profile =================
CLOTHING_PARTS = ("upper_body", "lower_body", "underwear", "outerwear", "footwear", "accessories")
CLOTHING_STATES = ("worn", "removed", "partially_removed", "torn", "lost", "destroyed",
                   "wet", "bloodied", "rolled_up", "unbuttoned", "open", "tied", "hung", "on")
PERMANENT_MARKERS = ("永久", "缺失", "断肢", "截肢", "失明")

def fresh_clothing(character):
    return {p: "worn" for p in CLOTHING_PARTS}

def fresh_anatomy(character):
    return {"left_arm": "intact", "right_arm": "intact", "left_leg": "intact", "right_leg": "intact",
            "left_eye": "intact", "right_eye": "intact"}

def _clock_str(c):
    return "第%d日 %s" % (c.get("day", 1), c.get("time", ""))

def _day_of(clock):
    return int(clock.get("day", 1))

class CausalStateEngine:
    """事件改变状态，后续内容继承状态；没有新事件就不能自动恢复。"""
    def apply_episode(self, prev, episode, skip_events=None):
        cur = {
            "story_clock": dict(prev.get("story_clock") or {"day": 1, "time": "夜"}),
            "clothing_states": {k: dict(v) for k, v in (prev.get("clothing_states") or {}).items()},
            "injury_states": {k: [dict(x) for x in v] for k, v in (prev.get("injury_states") or {}).items()},
            "anatomy_states": {k: dict(v) for k, v in (prev.get("anatomy_states") or {}).items()},
            "consequences": [dict(x) for x in (prev.get("consequences") or [])],
        }
        skip_events = set(skip_events or [])
        for ev in episode.get("events", []):
            if ev.get("event_id") in skip_events:
                continue
            st = ev.get("state") or {}
            if st.get("story_time"):
                self._advance_clock(cur, st["story_time"])
            self._apply_clothing(cur, st.get("clothing_changes") or [])
            self._apply_injuries(cur, st.get("injuries") or [], ev)
            self._apply_anatomy(cur, st.get("anatomy_changes") or [])
            self._apply_derived_anatomy(cur, st.get("injuries") or [])
            self._apply_consequences(cur, st.get("consequences") or [], ev)
        self._heal_check(cur)
        return cur

    def _advance_clock(self, cur, time_text):
        m = re.search(r"第\s*(\d+)\s*日", str(time_text))
        if m:
            cur["story_clock"]["day"] = max(cur["story_clock"].get("day", 1), int(m.group(1)))
        t = re.search(r"(晨|上午|午后|傍晚|夜|深夜)", str(time_text))
        if t:
            cur["story_clock"]["time"] = t.group(1)

    def _apply_clothing(self, cur, changes):
        for c in changes:
            name = str(c.get("character") or "").strip()
            part = str(c.get("part") or "").strip()
            state = str(c.get("state") or "").strip()
            if not name or not part:
                continue
            if state not in CLOTHING_STATES:
                state = "worn" if state in ("on", "worn") else "removed"
            cs = cur["clothing_states"].setdefault(name, fresh_clothing(name))
            cs[part] = state  # 无事件不得自动恢复（部件名可扩展）

    def _apply_injuries(self, cur, injuries, ev):
        for inj in injuries:
            name = str(inj.get("character") or "").strip()
            if not name:
                continue
            rec = {
                "injury_id": store.seq_id("INJ", len([x for xs in cur["injury_states"].values() for x in xs]) + 1),
                "character_id": name,
                "type": inj.get("type", "伤"), "location": inj.get("location", ""),
                "severity": inj.get("severity", "轻"), "bleeding": bool(inj.get("bleeding")),
                "pain": inj.get("pain", ""), "mobility_effect": inj.get("mobility_effect", ""),
                "created_story_time": inj.get("created_story_time") or _clock_str(cur["story_clock"]),
                "expected_recovery": inj.get("expected_recovery", ""),
                "medical_state": inj.get("medical_state", "未处理"),
                "natural_healing": True, "status": "open",
                "source_event": ev.get("event_id", ""),
            }
            cur["injury_states"].setdefault(name, []).append(rec)

    def _apply_anatomy(self, cur, changes):
        for c in changes:
            name = str(c.get("character") or "").strip()
            part = str(c.get("part") or "").strip()
            state = str(c.get("state") or "").strip()
            if not name or not part or not state:
                continue
            cur["anatomy_states"].setdefault(name, fresh_anatomy(name))[part] = state  # 永久

    @staticmethod
    def _anatomy_part_from_injury(inj):
        text = str(inj.get("type") or "") + str(inj.get("location") or "")
        for marker, part in (("左臂", "left_arm"), ("右臂", "right_arm"),
                             ("左腿", "left_leg"), ("右腿", "right_leg"),
                             ("左眼", "left_eye"), ("右眼", "right_eye")):
            if marker in text:
                return part
        return ""

    def _apply_derived_anatomy(self, cur, injuries):
        """断肢/永久损伤必须进入 anatomy_states，后续无事件不得自动恢复。"""
        for inj in injuries:
            name = str(inj.get("character") or "").strip()
            if not name:
                continue
            expected = str(inj.get("expected_recovery") or "")
            typ = str(inj.get("type") or "")
            if "永久" not in expected and not any(m in typ for m in PERMANENT_MARKERS):
                continue
            part = self._anatomy_part_from_injury(inj)
            if not part:
                continue
            cur["anatomy_states"].setdefault(name, fresh_anatomy(name))[part] = "missing"

    def _apply_consequences(self, cur, cons, ev):
        for c in cons:
            cur["consequences"].append({
                "irreversibility": c.get("irreversibility", "low"),
                "type": c.get("type", ""), "text": c.get("text", ""),
                "event_id": ev.get("event_id", ""), "story_time": _clock_str(cur["story_clock"]),
            })

    def _heal_check(self, cur):
        # 世界规则：自然愈合需要经过足够天数；医疗处理可缩短。
        day = _day_of(cur["story_clock"])
        for name, lst in cur["injury_states"].items():
            for inj in lst:
                if inj.get("status") != "open":
                    continue
                expected = str(inj.get("expected_recovery") or "")
                if "永久" in expected or any(m in str(inj.get("type") or "") for m in PERMANENT_MARKERS):
                    continue  # 断肢/永久损伤不参与自动愈合
                created = inj.get("created_story_time", "")
                cm = re.search(r"第\s*(\d+)\s*日", str(created))
                cday = int(cm.group(1)) if cm else day
                em = re.search(r"第\s*(\d+)\s*日", str(expected))
                eday = int(em.group(1)) if em else cday + 3
                if day >= eday:
                    inj["status"] = "healed"
                    inj["medical_state"] = inj.get("medical_state") or "已愈合"
                    inj["scar_or_permanent_effect"] = "轻度疤痕" if inj.get("severity") in ("重", "危") else "无"

def causal_state_package(story_id):
    st = latest_state(story_id)
    if not st:
        return None
    return {"story_clock": st.get("story_clock"), "clothing": st.get("clothing_states"),
            "injuries": st.get("injury_states"), "anatomy": st.get("anatomy_states"),
            "consequences": st.get("consequences")}

import re as _re
def re_sub2(s):
    return _re.sub(r'[\\/:*?"<>|]', "", str(s or ""))[:40]
