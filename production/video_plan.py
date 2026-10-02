# -*- coding: utf-8 -*-
"""V4.2.1 Video Plan：Director→Video 追踪 / 强类型 Reference Pack / 上一段结束帧抽取。"""
import json, os, shutil, subprocess, time
from pathlib import Path
from cores import store, story_core, narrative_core, quality_core, asset_core

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.path.join(os.path.dirname(FFMPEG), "ffprobe.exe")

REF_ROLES = ("character_identity", "character_current_look", "scene_master",
             "previous_end_frame", "director_keyframe", "prop",
             "secondary_scene_view", "special_state")

def seq_id(prefix, n):
    return "%s_%05d" % (prefix, n)

def unit_to_event_ids(ep, unit):
    """单元 → Canon Event IDs（优先 must_include_event_ids，其次按标题匹配）。"""
    ids = unit.get("must_include_event_ids") or []
    if ids:
        return ids
    titles = [e.get("title", "") for e in (ep or {}).get("events", [])]
    for t in (unit.get("event_titles") or []):
        for e in (ep or {}).get("events", []):
            if e.get("title") == t:
                ids.append(e.get("event_id"))
    return ids

def build_sequence_plan(story, ep, unit, seq_no):
    """生成 Sequence Execution Plan：可回溯 event_ids / unit_ids / director_id / state_snapshot_id。"""
    director = (story.get("previs") or {})
    shots = director.get("shots", [])
    return {
        "sequence_no": seq_no,
        "sequence_id": seq_id("VIDEO_SEQ", seq_no),
        "unit_id": unit.get("unit_id"),
        "event_ids": unit_to_event_ids(ep, unit),
        "director_id": "DIRECTOR_00001",
        "director_shots_count": len(shots),
        "state_snapshot_id": story.get("last_state_id", ""),
        "director_brief": {
            "story_script": director.get("story_script", ""),
            "shots": shots,
        },
    }

def extract_end_frame(video_path, out_png, at_offset=-0.2):
    """抽取上一段视频结束帧（previous_end_frame）。"""
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    dur = None
    try:
        out = subprocess.check_output([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                                       "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
                                      timeout=30, text=True, errors="replace")
        dur = float(out.strip())
    except Exception:
        dur = None
    ts = max(0.05, (dur or 0) + at_offset)
    subprocess.check_call([FFMPEG, "-y", "-ss", "%.3f" % ts, "-i", str(video_path),
                           "-frames:v", "1", "-vf", "scale=1152:640", str(out_png)],
                          creationflags=0x08000000 if os.name == "nt" else 0,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return str(out_png), ts

def build_typed_ref_pack(story_id, unit, refs, pack_no):
    """强类型 Reference Pack：每项带 ref_id/role/asset_id/character_id/scene_id/version/source/purpose。"""
    pack_id = seq_id("REF", pack_no)
    rdir = store.DATA / "assets" / story_id / "reference_packs"
    rdir.mkdir(parents=True, exist_ok=True)
    pack = {
        "reference_pack_id": pack_id,
        "story_id": story_id,
        "unit_id": unit.get("unit_id"),
        "refs": refs,
        "created": time.time(),
    }
    store.save_json(rdir / (pack_id + ".json"), pack)
    return pack

def video_character_refs(story_id, current_look_by_char=None, scene_id=None):
    """按角色生成 character_identity（Master Sheet）与 character_current_look 引用。"""
    story = story_core.get_story(story_id)
    current_look_by_char = current_look_by_char or {}
    refs = []
    for cid in (story.get("character_ids") or []):
        adopted = None
        for v in asset_core.list_assets(story_id, "visuals"):
            if v.get("status") == "adopted" and v.get("owner_id") == cid and v.get("kind") == "character_master":
                adopted = v
        if adopted:
            refs.append({
                "ref_id": "REF_ROLE_" + cid + "_IDENTITY",
                "role": "character_identity",
                "asset_id": adopted.get("visual_id"),
                "character_id": cid, "scene_id": None,
                "version": adopted.get("visual_version"), "source": adopted.get("path"),
                "purpose": "角色身份锚点（Master Sheet）",
            })
        look = current_look_by_char.get(cid)
        if look:
            refs.append({
                "ref_id": "REF_ROLE_" + cid + "_LOOK",
                "role": "character_current_look",
                "asset_id": look.get("asset_id"), "character_id": cid, "scene_id": None,
                "version": look.get("version"), "source": look.get("path"),
                "purpose": "角色当前造型（清晰脸/身体/当前伤势/服装）",
            })
    if scene_id:
        for v in asset_core.list_assets(story_id, "visuals"):
            if v.get("status") == "adopted" and v.get("owner_id") == scene_id and v.get("kind") == "scene_master":
                refs.append({
                    "ref_id": "REF_ROLE_" + scene_id,
                    "role": "scene_master",
                    "asset_id": v.get("visual_id"), "character_id": None, "scene_id": scene_id,
                    "version": v.get("visual_version"), "source": v.get("path"),
                    "purpose": "场景固定结构",
                })
    return refs

def write_trace(story_id, sequences):
    trace = {
        "story_id": story_id,
        "chain": "Canon Events -> Narrative Units -> Adopted Director Plan -> Video Sequence Execution Plan -> H3 Prompt -> H3",
        "director_id": "DIRECTOR_00001",
        "state_snapshot_id": story_core.get_story(story_id).get("last_state_id", ""),
        "sequences": sequences,
        "written_at": time.time(),
    }
    out = ROOT / "reports" / "v421" / "final_review" / "director_to_video_trace.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    return trace

# ================= 从 V4.2.3 毕业轮合并进来的通用规则（Phase 0） =================

def duration_contract(shots, target=15):
    """唯一负责：把镜头时长程序化压缩/对齐到目标时长合约，输出带起止时间的时间轴。"""
    total = sum(float(s.get("duration") or 2) for s in shots)
    scale = 1.0
    if total > target:
        scale = target / total
    timeline = []
    t = 0.0
    for s in shots:
        d = max(0.8, float(s.get("duration") or 2) * scale)
        timeline.append({"shot": s.get("shot_id"), "start": round(t, 2),
                         "end": round(min(t + d, target), 2), "duration": round(d, 2),
                         "narrative_purpose": s.get("narrative_purpose", ""),
                         "event_ids": s.get("event_ids", []),
                         "framing": s.get("framing", ""),
                         "camera_movement": s.get("camera_movement", ""),
                         "performance": s.get("performance", ""),
                         "dialogue": s.get("dialogue", ""),
                         "sound": s.get("sound", ""),
                         "cut_reason": s.get("cut_reason", "")})
        t += d
    return {"target": target, "sum": round(total, 2), "scaled": round(sum(x["duration"] for x in timeline), 2),
            "over_budget": total > target, "timeline": timeline}

def reference_binder(refs, prev_end_role="previous_end_frame"):
    """唯一负责：按参考角色优先级把参考图编成程序化 Picture 编号（Qwen 不参与）。"""
    ordered = sorted(refs, key=lambda r: (REF_ROLES.index(r.get("role")) if r.get("role") in REF_ROLES else 99,
                                          r.get("ref_id", "")))
    bound = []
    for i, r in enumerate(ordered, 1):
        bound.append({"picture": "Picture %d" % i, "role": r.get("role"),
                      "source": r.get("source"), "natural_name": r.get("natural_name", ""),
                      "purpose": r.get("purpose", "")})
    return bound

def required_role_gate(ctx, bound_roles):
    """唯一负责：双人连续段必需角色检查（缺则 FAIL），供文字验收与生成前拦截用。"""
    need = []
    n = int(ctx.get("participant_count") or 1)
    for i in range(n):
        need.append("character_identity")
        need.append("character_current_look")
    need.append("scene_master")
    if ctx.get("continuous") and ctx.get("previous_end_frame"):
        need.append("previous_end_frame")
    if ctx.get("anatomy_special"):
        need.append("special_state")
    have = set(bound_roles)
    missing = [r for r in need if r not in have]
    return {"ok": not missing, "missing": missing, "required": need, "have": sorted(have)}

def anatomy_visual_text(anatomy):
    """唯一负责：把解剖硬事实（断肢等）展开成 H3 可见的连续文字事实，无故事字面量。"""
    parts = []
    for cid, st in (anatomy or {}).items():
        if st.get("left_arm") == "missing":
            parts.append("%s左前臂自肘下永久缺失，旧伤已愈合；左袖下半段空瘪自然垂落" % cid)
        if st.get("left_arm") == "intact":
            parts.append("%s左臂完整" % cid)
    return "；".join(parts) or "无解剖特殊状态"

def clothing_visual_text(clothing):
    """唯一负责：把服装状态槽展开成 H3 可见文字（外衣穿着/脱下），无故事字面量。"""
    parts = []
    for cid, slots in (clothing or {}).items():
        ow = slots.get("outerwear")
        if ow == "worn":
            parts.append("%s外衣穿着" % cid)
        elif ow == "removed":
            parts.append("%s外套已脱下，只穿内层，轮廓与脱下前一致" % cid)
    return "；".join(parts) or "无服装特殊状态"

def sound_layers(dialogue_blocks, ambience="", foley="", music="N/A"):
    """唯一负责：组装声音层（音乐默认取项目策略 N/A，禁止生成器里写死音乐）。"""
    return {
        "dialogue": dialogue_blocks,
        "ambience": ambience,
        "foley": foley,
        "sound_bridge": "镜头切换时环境声连续，不突断",
        "silence": "对白之间保留 0.3-0.6s 自然静默",
        "music": music,
    }
