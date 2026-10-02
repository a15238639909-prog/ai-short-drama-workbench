# -*- coding: utf-8 -*-
"""V4.2.3 Cinematic Director Engine：Director's Read / Acting / Blocking / Cinematography / Shot Rhythm。"""
import json, time
from pathlib import Path
from models import qwen_client
from production import director_core, video_plan

DIRECTOR_V2_SYSTEM = """你是电影导演（风格参考 Hell Grind / Seedance 公开导演思路，但不复制其模型链）。
对给定 Scene 输出导演决策 JSON，只输出一个合法 JSON：
{
  "director_read": {"scene_purpose": "", "audience_before": "", "audience_after": "",
    "character_goal": "", "obstacle": "", "power_shift": "", "emotional_turn": "",
    "decisive_moment": "", "hidden_information": "", "end_state": ""},
  "acting": {"<角色名>": ["可拍摄行为（当前目标/压制情绪/看谁/是否避目/呼吸/手部/重心/距离/说话前反应/倾听反应/说完反应）"]},
  "spatial": {"start_positions": "", "paths": "", "layers": "", "props": "",
    "entrance": "", "exit": "", "axis_180": "", "eyeline": "", "screen_direction": ""},
  "shot_rhythm": {"scene_type": "quiet_dialogue|emotional_turn|secret_reveal|movement|action",
    "shots": [
      {"shot_id": "S01", "duration": 3, "framing": "", "camera_position": "", "camera_height": "",
       "camera_angle": "", "lens_character": "", "camera_distance": "", "focus_subject": "",
       "depth_of_field": "", "movement_path": "", "movement_speed": "", "movement_end_point": "",
       "dominant_light_source": "", "subject_light_side": "", "foreground": "", "midground": "",
       "background": "", "narrative_purpose": "", "event_ids": [], "dialogue": "",
       "sound": "", "cut_reason": ""}
    ]},
  "audio_direction": {"dialogue_blocks": ["台词与说话方式"], "ambience": "", "foley": "", "music": ""},
  "physics_notes": "重量/惯性/脚步/接触/布料/门的反馈等"
}
规则：
1. 镜头数量按场景类型：quiet_dialogue 1-3 镜；emotional_turn / secret_reveal 2-4 镜；movement 2-4 镜；action 4-7 镜。
2. 15 秒禁止机械塞 8-10 镜；每次 cut 必须有 cut_reason（information reveal / reaction / power shift / action completion / spatial clarification / emotional turn）。
3. 表演禁止只写“微笑/震惊/生气”，必须写可拍摄行为。
4. 镜头语言必须服务剧情；不写“高级感/电影感”这类抽象词。
5. 对白写在 dialogue；不要塞满 15 秒，保留停顿。"""

def generate(events, premise, characters, scene, state_text, target=15):
    ev_lines = "；".join("%s：%s" % (e[0], e[1]) for e in events)
    allowed = ",".join(e[0] for e in events)
    system = DIRECTOR_V2_SYSTEM + "\n本场景固定事件：%s。shot_rhythm.shots[].event_ids 必须只从 [%s] 选择。" % (ev_lines, allowed)
    user = ("人物：%s\n场景：%s\n人物状态：%s\n固定事件：%s\n场景任务：%s\n目标时长：%d 秒。"
            % (characters, scene, state_text, ev_lines, premise, target))
    for attempt in range(3):
        raw = qwen_client.chat_for("director", system, user)
        try:
            d = director_core._extract_json(raw)
            break
        except Exception:
            d = None
    if not d:
        raise RuntimeError("Cinematic Director 输出解析失败")
    shots = d.get("shot_rhythm", {}).get("shots", [])
    if not shots:
        raise RuntimeError("无分镜")
    allowed_set = {e[0] for e in events}
    for s in shots:
        ids = [x for x in (s.get("event_ids") or []) if x in allowed_set]
        if not ids:
            ids = [events[min(shots.index(s), len(events) - 1)][0]]
        s["event_ids"] = ids
    dc = video_plan.duration_contract(shots, target)
    d["_duration_contract"] = dc
    return d, raw
