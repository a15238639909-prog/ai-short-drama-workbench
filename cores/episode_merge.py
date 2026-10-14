# -*- coding: utf-8 -*-
"""一话的段合成一条成片（P342）：outputs/成片_<项目>_第N话.mp4。ffmpeg，不碰模型。"""
import json, os, re, subprocess, uuid
from pathlib import Path


def _target_size(sid, ep):
    """出片时记在段上的尺寸（"WxH"），第一段有记录就用它；没有按 0.7 档 1120x624。"""
    from . import saga_core as _sc
    tl = _sc.ep_timeline(sid, int(ep or 1)) or {}
    for g in sorted([g for s0 in (tl.get("scenes") or []) for g in (s0.get("segments") or [])], key=lambda g: int(g.get("no") or 0)):
        m = re.match(r"^(\d+)x(\d+)$", str(g.get("size") or ""))
        if m and str(g.get("video") or "").strip() and not g.get("stale_video"):
            return int(m.group(1)), int(m.group(2))
    return 1120, 624


def episode_videos(sid, ep, segs=None):
    """这一话按段号排好的视频（跳过没出/过期的）。返回 [(段号, 路径)]。segs 给了就只要这几段（P399 选段合成）。"""
    from . import saga_core as _sc
    tl = _sc.ep_timeline(sid, int(ep or 1)) or {}
    want = set(int(x) for x in (segs or []))
    segs_all = sorted([g for s0 in (tl.get("scenes") or []) for g in (s0.get("segments") or [])], key=lambda g: int(g.get("no") or 0))
    out = []
    for g in segs_all:
        v = str(g.get("video") or "").strip()
        if want and int(g.get("no") or 0) not in want:
            continue
        if v and os.path.isfile(v) and not g.get("stale_video"):
            out.append((int(g.get("no") or 0), v))
    return out


def film_title(sid):
    from . import story_core
    return str((story_core.get_story(sid) or {}).get("title") or sid).replace(" ", "")


def list_films(sid):
    """本项目所有成片（整话 + 选段），新的在前。[{name, path, size, mtime}]"""
    import glob, time as _t
    title = film_title(sid)
    out = []
    for p in glob.glob(os.path.join("outputs", "成片_%s_*.mp4" % title)):
        try:
            stt = os.stat(p)
        except OSError:
            continue
        out.append({"name": os.path.basename(p), "path": p.replace("\\", "/"), "size": stt.st_size, "mtime": stt.st_mtime,
                    "when": _t.strftime("%m-%d %H:%M", _t.localtime(stt.st_mtime))})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def merge_episode(sid, ep, dst=None, segs=None):
    """合成。返回成片路径；一段都没有返回 ""。失败抛 RuntimeError。segs 给了就只合这几段（文件名带段号）。"""
    vids = episode_videos(sid, ep, segs=segs)
    if not vids:
        return ""
    title = film_title(sid)
    if segs and not dst:
        _ns = sorted(int(x) for x in segs)
        dst = os.path.join("outputs", "成片_%s_第%d话_段%s.mp4" % (title, int(ep or 1), ("%d-%d" % (_ns[0], _ns[-1])) if len(_ns) > 1 else str(_ns[0])))
    dst = dst or os.path.join("outputs", "成片_%s_第%d话.mp4" % (title, int(ep or 1)))
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    target = Path(dst).resolve()
    # Encode separately: a failed retry must never overwrite an accepted film.
    pending = target.parent / ("合成中_" + uuid.uuid4().hex + ".mp4")
    args = ["ffmpeg", "-loglevel", "error", "-nostdin"]
    media = []
    for _, v in vids:
        probed = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                                 "-of", "json", str(v)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60)
        try:
            info = json.loads(probed.stdout)
            video = next(s for s in info.get("streams", []) if s.get("codec_type") == "video")
            duration = float(video.get("duration") or info["format"]["duration"])
            if probed.returncode or duration <= 0:
                raise ValueError()
            media.append((duration, any(s.get("codec_type") == "audio" for s in info["streams"])))
        except (ValueError, KeyError, StopIteration, TypeError):
            raise RuntimeError("合成输入视频无效，已有成片保留：" + str(v)) from None
        args += ["-i", v]
    parts, maps = [], ""
    W, H = _target_size(sid, ep)
    for i in range(len(vids)):
        parts.append("[%d:v]scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,setpts=PTS-STARTPTS[v%d]" % (i, W, H, W, H, i))
        duration, has_audio = media[i]
        source = ("[%d:a]aresample=48000,aformat=channel_layouts=stereo,apad" % i
                  if has_audio else "anullsrc=r=48000:cl=stereo")
        parts.append(source + ",atrim=duration=%.6f,asetpts=PTS-STARTPTS[a%d]" % (duration, i))
        maps += "[v%d][a%d]" % (i, i)
    fc = ";".join(parts) + ";" + maps + "concat=n=%d:v=1:a=1[v][a]" % len(vids)
    args += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "19", "-preset", "medium",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", str(pending)]
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg 合并失败：%s" % (r.stderr or "")[-300:])
    from .video_continuity import validate_video
    validate_video(str(pending), sum(seconds for seconds, _ in media))
    if target.exists():
        target = target.with_name(target.stem + "__" + uuid.uuid4().hex[:8] + target.suffix)
    if target.parent != pending.parent:
        raise RuntimeError("合成目标目录改变，已保留待合成文件")
    pending.rename(target)
    return str(target)
