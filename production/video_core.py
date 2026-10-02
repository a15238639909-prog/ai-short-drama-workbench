# -*- coding: utf-8 -*-
"""video_core.py — V4.1 Video Production：Reference Pack / Sequence / Take / Timeline / concat。"""
import json, os, re, shutil, subprocess, time
from pathlib import Path
from cores import store
from cores import quality_core

FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE") or (os.path.splitext(FFMPEG)[0] + ".exe".replace(".exe", "_probe.exe") if False else os.path.join(os.path.dirname(FFMPEG), "ffprobe.exe"))
VIDEO_ROOT = Path(__file__).resolve().parent.parent / "productions" / "video"

def build_h3_prompt(unit, refs, mode="ref2va", extra_text=""):
    """按 MiniMax H3 官方提示词指南（h3-prompt-writing skill）用 Qwen 写提示词。"""
    from cores import quality_core
    from models import qwen_client
    sys = quality_core.H3_REF2VA_SYSTEM if mode == "ref2va" else quality_core.H3_T2VA_SYSTEM
    ref_lines = ["%s -> %s" % (k, Path(v).name) for k, v in (refs or {}).items() if v]
    user = ("Sequence facts:\n- purpose: %s\n- key moment: %s\n- result: %s\n- leads to: %s\n- story: %s\n"
            "Reference images: %s\nPlease write the %s prompt."
            % (unit.get("purpose", ""), unit.get("key_moment", ""), unit.get("result", ""),
               unit.get("leads_to", ""), extra_text, "; ".join(ref_lines),
               "full-reference prompt with six sections" if mode == "ref2va" else "text-to-video prompt with three fields"))
    raw = qwen_client.chat_for("h3_prompt", sys, user)
    if mode == "ref2va":
        need = ["subject_definitions", "summary", "retention_analysis",
                "detailed_description", "overall_soundscape", "non_diegetic_music"]
        if not all(k in raw for k in need) or len(raw) < 600:
            raise ValueError("H3 Ref2VA 提示词缺少必需段落或过短")
    elif "integrated_multimodal_description" not in raw:
        raise ValueError("H3 T2VA 提示词缺少 integrated_multimodal_description")
    return raw

def _adopted_visual(story_id, owner_id, kinds):
    from cores import asset_core
    for v in asset_core.list_assets(story_id, "visuals"):
        if v.get("status") == "adopted" and v.get("owner_id") == owner_id and v.get("kind") in kinds:
            return v
    return None

def build_reference_pack(story_id, unit, character_ids, scene_id, previous_end_state=None):
    """为单个 Video Sequence 自动组装 Reference Pack（只选相关资产）。"""
    refs = {}
    pack_id = store.seq_id("REF", len(list((store.DATA / "assets" / story_id / "reference_packs").glob("*.json"))) + 1) if (store.DATA / "assets" / story_id / "reference_packs").exists() else "REF_00001"
    rdir = store.DATA / "assets" / story_id / "reference_packs"
    rdir.mkdir(parents=True, exist_ok=True)
    for cid in character_ids:
        v = _adopted_visual(story_id, cid, ["character_master"])
        if v:
            refs["REF_ROLE_" + cid] = v.get("path")
    if scene_id:
        v = _adopted_visual(story_id, scene_id, ["scene_master"])
        if v:
            refs["REF_ROLE_" + scene_id] = v.get("path")
    if previous_end_state:
        refs["REF_ROLE_CONTINUITY"] = previous_end_state
    pack = {"reference_pack_id": pack_id, "story_id": story_id, "unit_id": unit.get("unit_id"),
            "refs": refs, "created": time.time()}
    store.save_json(rdir / (pack_id + ".json"), pack)
    return pack

def probe_duration(path):
    try:
        out = subprocess.check_output([FFPROBE, "-v", "error",
                                       "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                                       str(path)], timeout=30, text=True, errors="replace")
        return float(out.strip())
    except Exception:
        return None

def concat_sequences(seq_files, target_seconds, out_title):
    """ffmpeg concat + 记录真实时长；不做无脑拼接。"""
    if not FFMPEG or not os.path.exists(FFMPEG):
        raise RuntimeError("ffmpeg 不存在")
    tmp = Path(__file__).resolve().parent.parent / "cache" / ("concat_" + str(int(time.time())))
    tmp.mkdir(parents=True, exist_ok=True)
    segs = []
    for i, f in enumerate(seq_files, 1):
        src = Path(f)
        dst = tmp / ("seg_%02d.mp4" % i)
        shutil.copy2(str(src), str(dst))
        segs.append(dst.name)
    lst = tmp / "concat.txt"
    lst.write_text("\n".join("file '" + s + "'" for s in segs), encoding="utf-8")
    outdir = VIDEO_ROOT / (re.sub(r'[\\/:*?"<>|]', "", out_title or "video") + "_" + time.strftime("%Y%m%d_%H%M%S"))
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / "final.mp4"
    subprocess.check_call([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                           "-vf", "hqdn3d=2:1.5:2:2.5",
                           "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                           "-c:a", "copy", "-movflags", "+faststart", str(out)], cwd=str(tmp),
                          creationflags=0x08000000 if os.name == "nt" else 0,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    real = probe_duration(out)
    return {"path": str(out), "duration": real, "target": target_seconds,
            "tolerance_ok": real is not None and abs(real - target_seconds) <= 8}

def produce_sequence(story_id, unit, character_ids, scene_id, prompt, seconds, mode="ref2va",
                     previous_end_state=None, take=1):
    """生成一个 ≤15s Sequence（默认全能参考），返回 Take。"""
    from models import h3_client
    pack = build_reference_pack(story_id, unit, character_ids, scene_id, previous_end_state)
    images = list(pack["refs"].values())
    try:
        prompt = build_h3_prompt(unit, pack["refs"], mode="ref2va", extra_text=prompt)
    except Exception as e:
        log_hint = str(e)
        prompt = prompt  # 回退到程序化提示词
    res = h3_client.generate(mode, prompt, images=images, seconds=seconds, ratio="16:9")
    manifest = quality_core.build_manifest(
        output_id=store.seq_id("VIDEO_SEQ", int(time.time())), story_id=story_id,
        production_type="video_sequence", source_narrative_ids=[unit.get("unit_id", "")],
        event_ids=unit.get("must_include_event_ids", []),
        reference_pack_ids=[pack["reference_pack_id"]],
        style_profile_name="电影级写实", model="minimax_h3", model_mode=mode,
        prompt=prompt, output_path=res["output_path"])
    return {"take_id": store.seq_id("TAKE", take), "take_no": take, "path": res["output_path"],
            "mode": mode, "seconds": seconds, "pack": pack, "manifest": manifest}
