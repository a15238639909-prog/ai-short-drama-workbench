# -*- coding: utf-8 -*-
"""P462：剧本页的「视频分段」——用户出片前先看、先改。

分段就是时间轴里冻结的切片（authoring.freeze_slicing 那份，导演段带 director 骨架）。
这里做三件事：
  rows_of      把切片摊成人能读会改的行（地点 / 秒 / 在场 / 机位 / 时间块+台词）
  save_rows    把改过的行写回切片并冻结；从第一处改动起作废后面已写的提示词
  reslice      扔掉冻结的切片，按现在的剧本重新让导演分一遍

时间块的文本格式就是导演分段那份（用户在日志里见过的）：
  0-4秒=林小雨单手举手机，指尖在屏幕滑动调整滤镜
    林小雨：苏苏，你看这个角度。
  4-9秒=苏苏凑近镜头，双手叉腰皱眉
块行 = 起-止秒=动作；紧跟的「名：台词」行是这一块末尾说的话。
"""
import os
import re

_BLOCK_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[-~～—]\s*(\d+(?:\.\d+)?)\s*秒?\s*[=＝:：]\s*(.*)$")
_SAY_RE = re.compile(r"^\s*([^：:\s]{1,12})\s*[：:]\s*(.+?)\s*$")


def _split_names(t):
    return [x.strip() for x in re.split(r"[、，,;；/ ]+", str(t or "")) if x.strip()]


def blocks_to_text(director):
    """director 骨架 → 时间块文本（含台词行）。"""
    d = director or {}
    says = [list(x) for x in (d.get("says") or [])]
    flat = [x for b in (d.get("blocks") or []) for x in (b[3] if len(b) > 3 else [])]
    by_d = {}
    for did, sq in zip(flat, says):
        by_d[did] = sq
    lines = []
    for b in (d.get("blocks") or []):
        a, z, t = b[0], b[1], b[2]
        lines.append("%s-%s秒=%s" % (_num(a), _num(z), str(t or "").strip()))
        for did in (b[3] if len(b) > 3 else []):
            sq = by_d.get(did)
            if sq:
                lines.append("    %s：%s" % (sq[0], sq[1]))
    return "\n".join(lines)


def _num(x):
    try:
        f = float(x)
    except Exception:
        return str(x)
    return str(int(f)) if f == int(f) else ("%.1f" % f)


def text_to_blocks(txt):
    """时间块文本 → (blocks, says)。编号按出现顺序 1..k，和 says 同序（authoring 里 zip 对应）。"""
    blocks, says = [], []
    for ln in str(txt or "").splitlines():
        if not ln.strip():
            continue
        m = _BLOCK_RE.match(ln)
        if m:
            blocks.append([float(m.group(1)), float(m.group(2)), m.group(3).strip().rstrip("。；;"), []])
            continue
        m2 = _SAY_RE.match(ln)
        if m2 and blocks:
            says.append([m2.group(1), m2.group(2).strip().strip("“”「」\"")])
            blocks[-1][3].append(len(says))
        elif blocks:
            blocks[-1][2] = (blocks[-1][2] + "，" + ln.strip().rstrip("。；;")).strip("，")
    return blocks, says


def _slice_from_row(old, row):
    """一行编辑结果 → 切片（在旧切片上改，没给的字段照旧）。"""
    from . import director_shots as _ds, oral_story as _os_
    s = dict(old or {})
    d = dict((old or {}).get("director") or {})
    place = str(row.get("place") if row.get("place") is not None else s.get("scene_hint") or "").strip()
    cast = _split_names(row.get("cast")) if row.get("cast") is not None else list(d.get("cast") or [])
    camera = str(row.get("camera") if row.get("camera") is not None else d.get("camera") or "").strip()
    if row.get("blocks_text") is not None:
        blocks, says = text_to_blocks(row.get("blocks_text"))
    else:
        blocks, says = [list(b) for b in (d.get("blocks") or [])], [list(x) for x in (d.get("says") or [])]
    if not blocks:
        raise ValueError("第 %s 段一个时间块都没有（每行要写成「0-4秒=动作」）" % row.get("no", "?"))
    secs = float(blocks[-1][1])
    try:
        if row.get("seconds") not in (None, ""):
            secs = float(row.get("seconds"))
    except Exception:
        pass
    if secs <= 0:
        secs = float(blocks[-1][1])
    # 结束状态：被删掉的人去掉，新加的人给个空的（提示词写手会照块里写的补）
    es = dict(d.get("end_state") or {})
    es = {k: v for k, v in es.items() if k in cast}
    for n in cast:
        es.setdefault(n, {"pos": "", "facing": "", "hands": "", "posture": "", "side": ""})
    d.update({"camera": camera, "cast": cast, "blocks": [[b[0], b[1], b[2], list(b[3])] for b in blocks],
              "says": [list(x) for x in says], "end_state": es})
    d.setdefault("purpose", "")
    body = "；".join(str(b[2]).rstrip("。") for b in blocks)
    text = "── %s ──\n%s。" % (place, body)
    for nm, q in says:
        text += "\n%s：%s" % (_os_.short_name(nm), q)
    s.update({"text": text, "seconds": secs, "scene_hint": place, "shot": _ds.shot_of(camera) if camera else (s.get("shot") or "中景"),
              "who": "、".join(_os_.short_name(n) for n in cast), "director": d, "pace": "导演", "cut": True})
    s.setdefault("beat", "")
    s.setdefault("beat_index", -1)
    return s


def rows_of(sid, ep=1):
    """分段行 + 状态。stale=剧本改过、这份分段是按旧剧本分的；approved=用户已认可。"""
    from . import authoring as _au
    ep = int(ep or 1)
    fr = _au.frozen_slicing(sid, ep)
    _saga, e = _au._ep(sid, ep, create=False)
    pics = str((e or {}).get("pictures") or "")
    rows = []
    for i, s in enumerate((fr or {}).get("slices") or []):
        d = s.get("director") or {}
        rows.append({"no": i + 1, "place": str(s.get("scene_hint") or ""), "seconds": s.get("seconds"),
                     "cast": "、".join(d.get("cast") or _split_names(s.get("who"))), "camera": str(d.get("camera") or ""),
                     "blocks_text": blocks_to_text(d) if d else str(s.get("text") or ""), "director": bool(d),
                     "purpose": str(d.get("purpose") or "")})
    return {"rows": rows, "n": len(rows), "total_seconds": round(sum(float(r.get("seconds") or 0) for r in rows), 1),
            "stale": bool(fr and pics.strip() and fr.get("sig") != _au._pics_sig(pics)),
            "approved": bool((e or {}).get("shots_approved")) and bool(fr) and (e or {}).get("shots_approved_sig") == _slices_sig(fr),
            "has_script": bool(pics.strip())}


def _slices_sig(fr):
    import hashlib
    import json
    return hashlib.sha256(json.dumps((fr or {}).get("slices") or [], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def void_segments_from(sid, ep, d, total):
    """第 d 段起已写的提示词作废（有视频标过期）、超出 total 的段删掉——和 apply_slicing_drift 同一口径。返回作废段数。"""
    from . import saga_core as _sgd
    ep = int(ep or 1)
    tl = _sgd.ep_timeline(sid, ep) or {"scenes": []}
    n_void = 0
    for sc0 in (tl.get("scenes") or []):
        keep = []
        for g in (sc0.get("segments") or []):
            no = int(g.get("no") or 0)
            if d and no >= d:
                n_void += 1
                if no > total:
                    for _p in {str(g.get("video") or ""), str(g.get("video_hd") or ""), str(g.get("end_frame") or "")}:
                        try:
                            if _p and os.path.isfile(_p):
                                os.replace(_p, _p + ".dropped")
                        except Exception:
                            pass
                    continue
                for k in ("prompt", "prompt_auto", "prompt_edited", "prompt_edited_at", "tail", "relay", "done_all", "seam", "problems", "critical_problems", "source_text", "plan"):
                    g.pop(k, None)
                if str(g.get("video") or "").strip():
                    g["stale_video"] = True
            keep.append(g)
        sc0["segments"] = keep
    _sgd.save_ep_timeline(sid, ep, tl)
    return n_void


def save_rows(sid, ep, rows):
    """写回分段。rows 按顺序就是新的分段表（少了的段=删了，多出来的=新加）。返回 {n, changed_from, voided}。"""
    from . import authoring as _au, saga_core as _sg
    ep = int(ep or 1)
    fr = _au.frozen_slicing(sid, ep) or {}
    old = list(fr.get("slices") or [])
    _saga, e = _au._ep(sid, ep, create=False)
    pics = str((e or {}).get("pictures") or "")
    if not pics.strip():
        raise ValueError("这一话还没有剧本")
    new = []
    for i, row in enumerate(rows or []):
        src = None
        try:
            k = int(row.get("src_no") or 0)
            if 1 <= k <= len(old):
                src = old[k - 1]
        except Exception:
            src = None
        row = dict(row, no=i + 1)
        new.append(_slice_from_row(src, row))
    if not new:
        raise ValueError("至少留一段")
    # 换场景的段要「亮相」（establish），和导演 to_slices 同口径
    last = ""
    for s in new:
        s["establish"] = bool(s.get("scene_hint") != last)
        last = s.get("scene_hint")
    changed_from = 0
    for k in range(max(len(old), len(new))):
        if k >= len(old) or k >= len(new) or _norm(old[k]) != _norm(new[k]):
            changed_from = k + 1
            break
    voided = void_segments_from(sid, ep, changed_from, len(new)) if changed_from else 0
    tl = _sg.ep_timeline(sid, ep) or {"scenes": []}
    tl["slicing"] = _au._slicing_record(pics, new)
    _sg.save_ep_timeline(sid, ep, tl)
    try:
        _au._BEAT_CACHE.clear()
    except Exception:
        pass
    _au._update_ep(sid, ep, shots_approved=False, shots_edited=True)
    return {"n": len(new), "changed_from": changed_from, "voided": voided}


def _norm(s):
    d = (s or {}).get("director") or {}
    return (re.sub(r"\s+", "", str((s or {}).get("text") or "")), float((s or {}).get("seconds") or 0), str((s or {}).get("scene_hint") or ""),
            str(d.get("camera") or ""), tuple(d.get("cast") or []),
            tuple((float(b[0]), float(b[1]), str(b[2])) for b in (d.get("blocks") or [])),
            tuple((str(x[0]), str(x[1])) for x in (d.get("says") or [])))


def reslice(sid, ep=1, on_step=None):
    """扔掉冻结的分段，按现在的剧本重分（已写的提示词全作废）。"""
    from . import authoring as _au, saga_core as _sg
    ep = int(ep or 1)
    _saga, e = _au._ep(sid, ep, create=False)
    pics = str((e or {}).get("pictures") or "")
    if not pics.strip():
        raise ValueError("这一话还没有剧本")
    tl = _sg.ep_timeline(sid, ep) or {"scenes": []}
    tl.pop("slicing", None)
    _sg.save_ep_timeline(sid, ep, tl)
    try:
        _au._BEAT_CACHE.clear()
    except Exception:
        pass
    sl = _au.episode_slices(sid, ep, pics=pics, cut=False)
    if not sl:
        raise RuntimeError("导演没分出段来")
    void_segments_from(sid, ep, 1, len(sl))
    _au.freeze_slicing(sid, ep, pics, sl)
    _au._update_ep(sid, ep, shots_approved=False, shots_edited=False)
    return len(sl)


def approve(sid, ep=1):
    """用户认可这份分段：记指纹，出片链按这份走、不再自修剧本。"""
    from . import authoring as _au
    ep = int(ep or 1)
    fr = _au.frozen_slicing(sid, ep)
    if not fr:
        raise ValueError("还没有分段，先点「生成剧本和分段」")
    _au._update_ep(sid, ep, shots_approved=True, shots_approved_sig=_slices_sig(fr))
    return len(fr.get("slices") or [])
