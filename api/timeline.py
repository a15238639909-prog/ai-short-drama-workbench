import threading
import json
import re
import os
# -*- coding: utf-8 -*-
"""timeline.py — 时间轴（场 → 节拍 → 分镜）的读写与生成接口。

用户定的工作方式：**先规划时间轴和分镜，每个分镜的参考单独选，
检查没问题再开始生成。** 所以这里的接口分成三类：

  规划类  拆场 / 拆节拍 / 拆分镜  —— 只产出文字，不碰 GPU 出片
  编辑类  改时长、改景别、改参考、删镜头、插镜头
  生成类  按段出视频 —— 只有用户点了才跑

时间轴存在 story 里，和资源、成品共用同一个项目文件。
"""
import time
import copy

from api import post
from api._shared import _RESP, ctx

globals().update(ctx())

_KEY = "timeline"


# 【按话存】原来 timeline 是全局单槽：做第二话会覆盖第一话的分镜。
# 现在每一话有自己的 timeline，"当前是第几话"由前端显式传 ep，不许猜。
_CUR_EP = {}          # sid -> 最近一次操作的话号，只作兜底


def _ep_of(sid, d=None):
    n = (d or {}).get("ep")
    try:
        n = int(n)
    except Exception:
        n = None
    if n:
        _CUR_EP[sid] = n
        return n
    return _CUR_EP.get(sid, 1)


def _ep_body(sid, no):
    """这一话的正文。分镜必须拆**当前话**，不是全局那个单槽——
    否则做第二话时还在拆第一话。"""
    from cores import saga_core as _sc
    e = _sc.episode(_sc.get_saga(sid), no) or {}
    if e.get("body"):
        return e["body"]
    return story_layer.get_story_body(sid) or ""     # 老项目兜底


def _ep_meta(sid, no):
    """这一话的元信息：标题 / 锁定 / 正文是否改过但分镜还是旧的。"""
    from cores import saga_core as _sc
    saga = _sc.get_saga(sid)
    e = _sc.episode(saga, no) or {}
    return {"title": e.get("title") or e.get("logline") or "",
            "locked": e.get("status") == _sc.LOCKED,
            "stale": bool(any((sc0.get("segments") or []) for sc0 in ((e.get("timeline") or {}).get("scenes") or []))) and (bool(e.get("timeline_stale")) or _sc.timeline_stale(sid, no))}    # P321：没有分镜不报


def _load(sid, ep=None):
    from cores import saga_core as _sc
    st = story_core.get_story(sid) or {}
    n = ep or _CUR_EP.get(sid, 1)
    return st, _sc.ep_timeline(sid, n)


def _save(sid, tl, ep=None):
    from cores import saga_core as _sc
    n = ep or _CUR_EP.get(sid, 1)
    tl["updated"] = time.time()
    return _sc.save_ep_timeline(sid, n, tl)


def _names(sid):
    return [c.get("name") for c in asset_core.list_assets(sid, "characters") if c.get("name")]


def _scene_names(sid):
    return [s.get("name") for s in asset_core.list_assets(sid, "scenes") if s.get("name")]


def _scene_key(value):
    """同一场景的稳定比较键。只折叠空白，不做模糊猜测。"""
    return "".join(str(value or "").split()).lower()


def _coalesce_adjacent_scenes(tl):
    """把相邻且地点相同的旧“场”合成一个真实场景，保留视频与提示词。

    旧链曾按字数硬切，S01/S02 和段号会在每个碎片里重新开始。合并时必须同步
    重编号，并更新 segment.shot_ids；视频文件路径、提示词、参考图和采用状态
    原样保留。
    """
    src = list((tl or {}).get("scenes") or [])
    if len(src) < 2:
        return False
    groups = []
    for sc in src:
        key = _scene_key(sc.get("location") or sc.get("title"))
        if key and groups and groups[-1][0] == key:
            groups[-1][1].append(sc)
        else:
            groups.append([key, [sc]])
    if all(len(items) == 1 for _, items in groups):
        return False

    merged = []
    for new_no, (_, items) in enumerate(groups, 1):
        if len(items) == 1:
            one = copy.deepcopy(items[0])
            one["no"] = new_no
            merged.append(one)
            continue
        first = items[0]
        out = copy.deepcopy(first)
        out["no"] = new_no
        out["location"] = first.get("location") or first.get("title") or "第%d场景" % new_no
        out["title"] = out["location"]
        out["text"] = "\n\n".join(str(x.get("text") or "").strip()
                                     for x in items if str(x.get("text") or "").strip())
        out["source_scene_nos"] = [x.get("no") for x in items]
        times = []
        for x in items:
            t = str(x.get("time_of_day") or "").strip()
            if t and t not in times:
                times.append(t)
        out["time_of_day"] = " / ".join(times)

        beats, shots, segments = [], [], []
        beat_offset = 0
        shot_counter = 0
        seg_counter = 0
        for old in items:
            old_beats = list(old.get("beats") or [])
            beat_map = {}
            for i, b0 in enumerate(old_beats, 1):
                b = copy.deepcopy(b0)
                old_beat = int(b.get("beat") or i)
                new_beat = beat_offset + i
                beat_map[old_beat] = new_beat
                b["beat"] = new_beat
                beats.append(b)
            shot_map = {}
            for s0 in (old.get("shots") or []):
                s = copy.deepcopy(s0)
                old_id = str(s.get("shot_id") or "")
                shot_counter += 1
                new_id = "S%02d" % shot_counter
                if old_id:
                    shot_map[old_id] = new_id
                s["shot_id"] = new_id
                try:
                    s["beat"] = beat_map.get(int(s.get("beat") or 0), s.get("beat"))
                except Exception:
                    pass
                shots.append(s)
            for g0 in (old.get("segments") or []):
                g = copy.deepcopy(g0)
                seg_counter += 1
                g["no"] = seg_counter
                g["shot_ids"] = [shot_map.get(str(x), str(x))
                                   for x in (g.get("shot_ids") or [])]
                g["source_scene_no"] = old.get("no")
                segments.append(g)
            beat_offset += len(old_beats)
        out["beats"], out["shots"], out["segments"] = beats, shots, segments
        merged.append(out)
    tl["scenes"] = merged
    return True


@post(("/api/timeline/", "/get"))
def tl_get(h, path, d):
    sid = path.split("/")[3]
    _ep_of(sid, d)
    st, tl = _load(sid)
    if not st:
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    _rec = _recover(sid, tl, _ep_of(sid))     # P237 找回要按话号找
    return _RESP({"ok": True, "data": {
        "timeline": tl,
        "recover_notes": _rec,                # 老片子认不出属于哪一话时，页面要能看见

        "characters": asset_core.list_assets(sid, "characters"),
        "scenes": asset_core.list_assets(sid, "scenes"),
        "visuals": asset_core.list_assets(sid, "visuals"),
        "ref_images": _ref_images(sid),
        "story_body": story_layer.get_story_body(sid),
        # 当前是第几话（第一公民）：话名、锁没锁、正文改过没有
        "ep": _ep_of(sid, d),
        "ep_title": _ep_meta(sid, _ep_of(sid, d)).get("title"),
        "ep_locked": _ep_meta(sid, _ep_of(sid, d)).get("locked"),
        "stale": _ep_meta(sid, _ep_of(sid, d)).get("stale"),
        # 话次下拉用：有哪几话、各话有没有剧本/分镜（用户定：点第几话出第几话，互不牵扯）
        "episodes": _ep_list(sid),
    }})


def _ep_list(sid):
    """所有话的极简清单，给分镜页的话次下拉用。"""
    from cores import saga_core as _sc
    out = []
    for e in sorted((_sc.get_saga(sid).get("episodes") or []),
                    key=lambda x: int(x.get("no") or 0)):
        tl = e.get("timeline") or {}
        segs = sum(len(s.get("segments") or []) for s in (tl.get("scenes") or []))
        out.append({"no": int(e.get("no") or 0),
                    "title": str(e.get("title") or e.get("logline") or "")[:20],
                    "has_body": bool(str(e.get("body") or "").strip()),
                    "segments": segs})
    return out


def _ref_images(sid):
    """名字 → 已采用的设定图路径。参考图要在页面上看得见，不能只是一个勾选框。"""
    adopted = {}
    for v in asset_core.list_assets(sid, "visuals"):
        if v.get("status") == "adopted" and v.get("owner_id") and v.get("path"):
            adopted[v["owner_id"]] = v["path"]
    out = {}
    for kind, key in (("characters", "character_id"), ("scenes", "scene_id")):
        for a in asset_core.list_assets(sid, kind):
            p = adopted.get(a.get(key))
            if p:
                out[a.get("name")] = str(p).replace("\\", "/")
    return out


def _ref_library(sid):
    """名字 → 这个人物/场景名下**所有**可用图（生成的候选 + 上传的），带类型。

    分镜页选参考图用：人物卡只列人物名下的图、场景卡只列场景名下的图——
    类型隔离靠归属关系天然成立，人物图和场景图互相选不到。"""
    import os as _os
    by_owner = {}
    for v in asset_core.list_assets(sid, "visuals"):
        if v.get("owner_id") and v.get("path") and _os.path.exists(v.get("path")):
            by_owner.setdefault(v["owner_id"], []).append(v)
    lib = {}
    for kind, key in (("characters", "character_id"), ("scenes", "scene_id")):
        for a in asset_core.list_assets(sid, kind):
            items = by_owner.get(a.get(key)) or []
            if items:
                lib[a.get("name")] = [
                    {"vid": v.get("visual_id"), "path": str(v["path"]).replace("\\", "/"),
                     "adopted": v.get("status") == "adopted",
                     "label": ("已采用" if v.get("status") == "adopted" else
                               ("上传" if v.get("source") == "upload" else "候选"))}
                    for v in items]
    return lib


@post(("/api/timeline/", "/split-scenes"))
def tl_split(h, path, d):
    """把故事正文切成一场一场。只切，不拆节拍——那一步用户挑哪一场再单独做。"""
    sid = path.split("/")[3]
    _ep_of(sid, d)
    from cores import scene_layer
    body = d.get("body") or _ep_body(sid, _ep_of(sid, d)) or ""
    if not body.strip():
        return _RESP({"ok": False, "error": "故事栏还没有正文"}, 400)
    parts = scene_layer.split_scenes(body, max_chars=int(d.get("max_chars") or 400))
    st, tl = _load(sid)
    old = {s.get("no"): s for s in (tl.get("scenes") or [])}
    scenes = []
    for i, text in enumerate(parts, 1):
        prev = old.get(i) or {}
        scenes.append({"no": i, "text": text,
                       "title": prev.get("title") or ("第%d场" % i),
                       "location": prev.get("location") or "",
                       "beats": prev.get("beats") or [],
                       "shots": prev.get("shots") or []})
    tl["scenes"] = scenes
    _save(sid, tl)
    return _RESP({"ok": True, "data": {"timeline": tl}})


@post(("/api/timeline/", "/coalesce-scenes"))
def tl_coalesce_scenes(h, path, d):
    """显式迁移旧分镜：相邻同地点场次归并；原数据快照留在 timeline 内。"""
    sid = path.split("/")[3]
    _ep_of(sid, d)
    st, tl = _load(sid)
    if not tl.get("legacy_scenes_before_location_merge"):
        tl["legacy_scenes_before_location_merge"] = copy.deepcopy(tl.get("scenes") or [])
    changed = _coalesce_adjacent_scenes(tl)
    if changed:
        tl["scene_merge_version"] = 1
        _save(sid, tl)
    return _RESP({"ok": True, "data": {"changed": changed, "timeline": tl}})


@post(("/api/timeline/", "/beats"))
def tl_beats(h, path, d):
    """把某一场拆成节拍表。"""
    sid = path.split("/")[3]
    _ep_of(sid, d)
    from cores import scene_layer
    no = int(d.get("scene_no") or 1)
    st, tl = _load(sid)
    sc = next((x for x in tl.get("scenes") or [] if int(x.get("no")) == no), None)
    if not sc:
        return _RESP({"ok": False, "error": "没有第 %d 场，先点「拆成场」" % no}, 400)
    try:
        got = scene_layer.beats_for_scene(
            sc.get("text") or "", d.get("location") or sc.get("location") or
            (_scene_names(sid) or [""])[0], _names(sid))
    except Exception as e:
        return _RESP({"ok": False, "error": str(e)[:300]}, 500)
    sc["beats"] = got.get("beats") or []
    sc["title"] = got.get("scene_title") or sc.get("title")
    sc["location"] = got.get("location") or sc.get("location")
    sc["time_of_day"] = got.get("time_of_day") or ""
    sc["shots"] = []          # 节拍变了，旧分镜作废
    _save(sid, tl)
    return _RESP({"ok": True, "data": {"scene": sc}})


@post(("/api/timeline/", "/shots"))
def tl_shots(h, path, d):
    """把某一场的节拍拆成分镜。beat_no 给了就只拆那一拍，不给就整场。"""
    sid = path.split("/")[3]
    _ep_of(sid, d)
    from cores import scene_layer
    no = int(d.get("scene_no") or 1)
    only = d.get("beat_no")
    st, tl = _load(sid)
    sc = next((x for x in tl.get("scenes") or [] if int(x.get("no")) == no), None)
    if not sc or not sc.get("beats"):
        return _RESP({"ok": False, "error": "第 %d 场还没有节拍，先点「拆节拍」" % no}, 400)
    names = _names(sid)
    info = "%s，%s" % (sc.get("location") or "", sc.get("time_of_day") or "")
    beats = sc["beats"] if only is None else [b for b in sc["beats"]
                                              if int(b.get("beat")) == int(only)]
    keep = [s for s in (sc.get("shots") or [])
            if only is not None and int(s.get("beat") or 0) != int(only)]
    on_stage, out = [], []
    for b in sc["beats"]:
        for nm in names:
            if nm in str(b.get("who") or "") and nm not in on_stage:
                on_stage.append(nm)
        if b not in beats:
            continue
        try:
            got = scene_layer.shots_for_beat(b, info, names, None, None, on_stage=on_stage)
        except Exception as e:
            return _RESP({"ok": False, "error": "第%s拍拆镜失败：%s" % (b.get("beat"), str(e)[:200])}, 500)
        for s in got:
            s["location"] = sc.get("location") or ""
            s.setdefault("refs", [])       # 参考图由用户单独挑
        out += got
    allshots = sorted(keep + out, key=lambda s: (int(s.get("beat") or 0),))
    for i, s in enumerate(allshots, 1):
        s["shot_id"] = "S%02d" % i
    sc["shots"] = allshots
    _save(sid, tl)
    return _RESP({"ok": True, "data": {"scene": sc}})


@post(("/api/timeline/", "/save-scene"))
def tl_save_scene(h, path, d):
    """用户在界面上改完时长、景别、参考图之后保存。"""
    sid = path.split("/")[3]
    _ep_of(sid, d)
    st, tl = _load(sid)
    no = int(d.get("scene_no") or 1)
    sc = next((x for x in tl.get("scenes") or [] if int(x.get("no")) == no), None)
    if not sc:
        return _RESP({"ok": False, "error": "没有第 %d 场" % no}, 400)
    for k in ("title", "location", "time_of_day", "beats", "shots", "text", "segments"):
        if k in d:
            sc[k] = d[k]
    _save(sid, tl)
    return _RESP({"ok": True, "data": {"scene": sc}})


@post(("/api/timeline/", "/segments"))
def tl_segments(h, path, d):
    """按 15 秒 + 地点边界把某一场的分镜切成待生成的段。只算，不生成。"""
    sid = path.split("/")[3]
    _ep_of(sid, d)
    from cores import video_layer as vl
    no = int(d.get("scene_no") or 1)
    st, tl = _load(sid)
    sc = next((x for x in tl.get("scenes") or [] if int(x.get("no")) == no), None)
    if not sc or not sc.get("shots"):
        return _RESP({"ok": False, "error": "第 %d 场还没有分镜" % no}, 400)
    segs = vl.split_segments(sc["shots"])
    out, prev_loc = [], None
    for i, g in enumerate(segs):
        plan = vl.generation_plan(i, g.get("location"), prev_loc,
                                  "有" if i else None, _names(sid), g.get("location"))
        out.append({"no": i + 1, "seconds": g["seconds"], "location": g.get("location"),
                    "shot_ids": [s.get("shot_id") for s in g["shots"]],
                    "mode": plan["mode"], "why": plan["why"], "over": g.get("over")})
        prev_loc = g.get("location")
    return _RESP({"ok": True, "data": {"segments": out,
                                       "total": round(sum(x["seconds"] for x in out), 1)}})

# ---------- 一键跑完全链 ----------
#
# 用户定的工作方式：**这个平台是用来检查和修改的**——
# 做一次设定，剩下的 Qwen 全部生成完，人来逐个检查，不合格的单独重做。
#
# 所以拆场/拆节拍/拆分镜/排段不该是四个手点的步骤，而是一个按钮跑完，
# 然后每一级都能单独重做：整场、单拍、单镜、单段的提示词。

import threading

_JOBS = {}          # story_id -> {"step":…, "done":n, "total":n, "note":…, "err":…}


def _job(sid):
    return _JOBS.setdefault(sid, {"running": False, "step": "", "done": 0,
                                  "total": 0, "note": "", "err": ""})


def _engine_grammar(settings):
    """题材引擎的镜头语法段，给导演内核。"""
    import re as _re
    eng = str((settings or {}).get("genre_engine") or "")
    try:
        gram = (Path(__file__).resolve().parent.parent / "presets" / "instructions"
                / "节拍_分镜.txt").read_text(encoding="utf-8")
        if eng == "感官特写型":
            m = _re.search(r"## 感官特写型题材的镜头语法.*", gram, _re.S)
            if m:
                return m.group(0)
    except Exception:
        pass
    return ""


def _build_all(sid, max_chars):
    """后台跑（分镜内核 v2）：骨架/拆场 → 每场导演压缩出分镜 → 排段。

    旧内核"逐拍拆镜"把 2302 字拆成 143 镜、15 秒塞 4~5 镜——节奏像 PPT，
    每切一镜人脸重抽一次。现在整场交给 director_layer 做视觉压缩：
    一镜 6~12 秒连续表演，起止状态镜镜相接，连续性和提示词卫生机械校验。"""
    from cores import scene_layer, video_layer as vl, director_layer as dl
    j = _job(sid)
    try:
        j.update(running=True, err="", step="准备", done=0, total=0, note="", cancel=False)
        # 【没剧本就别静默空跑】新流水线里分镜读的是剧本(body)。剧本还没生成时，
        # 导演读不到、旧逻辑也没料，job 会 0 段跑完——用户看着就是「点了没反应」。
        # 这里直接给一句明确提示，告诉他先去生成剧本。
        _ep_chk = _CUR_EP.get(sid, 1)
        if not (_ep_body(sid, _ep_chk) or "").strip():
            j.update(running=False, step="缺剧本", done=0, total=0,
                     err="第%d话还没有剧本。先去 ⚙️设定 页点「🎬 按原文生成剧本」，"
                         "生成剧本后再来这里分镜。" % _ep_chk)
            return
        # 【新链路】剧本(画面稿) → 时长切段 → 每段 H3 提示词，建表保住已出的视频。
        # 原来这个按钮一直跑下面的旧导演内核：提示词是「本段拍到/机位」的旧格式，
        # 和「全部生成」出的 <SubjectN> 模板不是一套（2026-09-02 项目87 查出）。
        from cores import authoring as _au
        # P166①：拆分镜之前先对齐上游——正文改过就先把剧本重出，
        # 不然这一话的分镜是照旧剧本拆的，用户看不出来。
        try:
            _rl = []
            _au.refresh_upstream(sid, _ep_chk, on_step=lambda m: j.update(step=m), log=_rl)
            if _rl:
                j["note"] = "；".join(_rl)
        except Exception as _ex:
            j["note"] = "对齐上游失败：%s" % str(_ex)[:100]
        if _au.use_new_pipeline():
            _epn = _CUR_EP.get(sid, 1)
            j["step"] = "按时长切段…"
            def _step(m):
                if j.get("cancel"):
                    raise _Cancelled()
                j["step"] = m
            try:
                try:
                    _au.apply_slicing_drift(sid, _epn)                      # P339：剧本改了先作废漂掉的段，超出的段删掉
                except Exception:
                    pass
                _ps = _au.make_h3_prompts(sid, _epn, on_step=_step) or []
            except _Cancelled:
                j.update(running=False, step="已取消", err="")
                return
            if not _ps:
                j.update(running=False, step="没切出段", err="剧本切不出任何一段")
                return
            _au.build_timeline(sid, _epn, _ps)
            j["step"] = "完成"
            j["done"] = j["total"] = len(_ps)
            j["note"] = "共 %d 段，提示词已写好；已出过的视频原样保留" % len(_ps)
            j["running"] = False
            return
        # 【导演内核·旧路径】Qwen 当导演读剧本、按戏剧节拍分段+导成电影感分镜
        # （cores/director.py）。成功就用导演分好的段直接返回；失败再回退下面的
        # 旧转写逻辑，保证任务永不中断。
        try:
            from cores import director as _dir
            from cores import saga_core as _sc0
            _ep0 = _CUR_EP.get(sid, 1)
            j["step"] = "导演读剧本、按戏剧节拍分镜…"
            _segs, _raw = _dir.direct_episode(sid, _ep0)
            if _segs and len(_segs) >= 2:
                _dir.save_to_timeline(sid, _ep0, _segs)
                try:
                    _sc0.stamp_source(sid, _ep0)
                    _sg = _sc0.get_saga(sid)
                    _de0 = _sc0.episode(_sg, _ep0)
                    _shoot0 = str((_de0 or {}).get("shooting_notes") or "").strip()
                    if _shoot0:
                        _tl0 = _sc0.ep_timeline(sid, _ep0) or {"scenes": []}
                        for _scn0 in (_tl0.get("scenes") or []):
                            for _g0 in (_scn0.get("segments") or []):
                                _g0["shooting_notes"] = _shoot0
                        _sc0.save_ep_timeline(sid, _ep0, _tl0)
                    if _de0 is not None:
                        _de0["timeline_stale"] = False
                        _sc0.save_saga(sid, _sg)
                except Exception:
                    pass
                j["step"] = "完成"
                j["done"] = j["total"] = len(_segs)
                j["note"] = "导演分镜：%d 段（每段一个戏剧节拍，视频提示词已写好）" % len(_segs)
                return
        except Exception as _de:
            j["note"] = "导演内核未跑通，回退旧分镜：" + str(_de)[:120]
        # ↓↓↓ 以下是旧的"代码转写镜头表"逻辑，仅作回退
        st, tl = _load(sid)
        settings = (st or {}).get("settings") or {}
        chars_all = asset_core.list_assets(sid, "characters")
        scene_cards = {x.get("name"): x for x in asset_core.list_assets(sid, "scenes")}
        names, scn = _names(sid), _scene_names(sid)
        grammar = _engine_grammar(settings)

        # 顶层 skeleton 是最近写的那一话留下的。要是话次对不上，
        # 就回 saga 里按话号取，免得拿第 3 话的骨架去拆第 1 话的分镜。
        # 正文被用户改过后，skeleton_stale 会明确标记旧骨架已失效；这时必须
        # 从当前正文重新拆场，不能为了“有骨架”而继续沿用改动前的剧情。
        _ep_now = _CUR_EP.get(sid, 1)
        from cores import saga_core as _sc
        _e = _sc.episode(_sc.get_saga(sid), _ep_now) or {}
        _shooting_notes = str(_e.get("shooting_notes") or "").strip()
        skeleton_stale = bool(_e.get("skeleton_stale"))
        skeleton = (st or {}).get("skeleton") or {}
        if int((st or {}).get("skeleton_episode") or 0) != int(_ep_now):
            try:
                skeleton = _e.get("skeleton") or {}
            except Exception:
                skeleton = {}
        if skeleton_stale:
            skeleton = {}
        # 【首选：直接转写剧本，不重编】剧本本身就是镜头级的——每一行
        # `（景别，运镜）动作` 就是一个镜头，台词按标准剧本格式挂在下面。
        # 让导演层"重新理解"一遍是净损失，实测对照第一场：
        #   剧本 6 句台词 → 分镜只剩 2 句，其中「别磨蹭了，盗贼。」被改写成
        #   「过来。」；还凭空多出一整镜"整理披风、卷羊皮卷轴、摩挲剑柄"。
        # 转写之后 12 句台词一句不丢，秒数和骨架完全对上。
        body_now = _ep_body(sid, _ep_now) or ""
        parsed = []
        if not skeleton_stale and body_now.strip():
            try:
                secs = {x.get("no"): x.get("seconds")
                        for x in (skeleton.get("scenes") or [])}
                parsed = scene_layer.shots_from_screenplay(body_now, names, secs)
            except Exception:
                parsed = []
        # 只有真的解析出镜头才走这条路；旧的小说体正文解析不出括号镜头提示，
        # 会得到一堆空场次，那时必须回退到导演层。
        if parsed and sum(len(x.get("shots") or []) for x in parsed) >= 3:
            tl["scenes"] = []
            for x in parsed:
                shots = x["shots"]
                for y in shots:
                    y.setdefault("refs", [])
                    y["camera_movement"] = y.get("camera") or ""
                    y.setdefault("start_state", "")
                    y.setdefault("end_state", "")
                tl["scenes"].append({
                    "no": x["scene_no"], "text": "",
                    "title": x.get("location") or ("第%d场" % x["scene_no"]),
                    "location": x.get("location") or "",
                    "time_of_day": x.get("time_of_day") or "",
                    "interior": x.get("interior") or "",
                    "beats": [], "shots": shots, "warn": "",
                    "state_block": x.get("blocking") or "",
                    "segments": [
                        {"no": i + 1, "seconds": g["seconds"],
                         "location": x.get("location") or "",
                         "shot_ids": [y.get("shot_id") for y in g["shots"]],
                         "shooting_notes": _shooting_notes,
                         "prompt": "", "video": ""}
                        for i, g in enumerate(vl.split_segments(shots))]})
            _coalesce_adjacent_scenes(tl)
            _save(sid, tl)
            try:
                _sc.stamp_source(sid, _ep_now)
                _saga = _sc.get_saga(sid)
                _done_ep = _sc.episode(_saga, _ep_now)
                if _done_ep is not None:
                    _done_ep["timeline_stale"] = False
                    _sc.save_saga(sid, _saga)
            except Exception:
                pass
            j["step"] = "完成"
            j["done"] = j["total"] = len(tl["scenes"])
            j["note"] = "剧本直接转写：%d 场 · %d 镜 · %d 段（台词逐字保留）" % (
                len(tl["scenes"]),
                sum(len(y.get("shots") or []) for y in tl["scenes"]),
                sum(len(y.get("segments") or []) for y in tl["scenes"]))
            return
        if skeleton.get("scenes"):
            # 次选：故事层定稿的骨架就是场次和节拍，不再从正文反推
            sk_scenes = skeleton["scenes"]
            tl["scenes"] = [{"no": x.get("no") or i, "text": "",
                             "title": "第%d场" % (x.get("no") or i),
                             "location": x.get("location") or "",
                             "beats": x.get("beats") or [], "shots": []}
                            for i, x in enumerate(sk_scenes, 1)]
        else:
            # 没骨架的旧故事：拆场 + 拆节拍，再喂给同一个导演内核
            body = _ep_body(sid, _CUR_EP.get(sid, 1)) or ""
            if not body.strip():
                raise ValueError("故事栏还没有正文，先去 📖 故事 生成完整故事")
            parts = scene_layer.split_scenes(body, max_chars=max_chars)
            tl["scenes"] = [{"no": i, "text": t, "title": "第%d场" % i,
                             "location": "", "beats": [], "shots": []}
                            for i, t in enumerate(parts, 1)]
            _save(sid, tl)
            sec_by_weight = {"轻": 5, "中": 9, "重": 14}
            for sc in tl["scenes"]:
                j["step"] = "第%d场 · 拆节拍" % sc["no"]
                got = scene_layer.beats_for_scene(sc["text"], scn[0] if scn else "", names)
                bs = got.get("beats") or []
                for b in bs:
                    b["seconds"] = b.get("seconds") or sec_by_weight.get(b.get("weight") or "中", 9)
                    b["chars"] = [n for n in names if n in str(b.get("who") or "")]
                sc["beats"] = bs
                sc["title"] = got.get("scene_title") or sc["title"]
                sc["location"] = got.get("location") or (scn[0] if scn else "")
                sc["time_of_day"] = got.get("time_of_day") or ""
                _save(sid, tl)
            sk_scenes = [{"no": x["no"], "location": x["location"], "beats": x["beats"],
                          "seconds": sum(float(b.get("seconds") or 9) for b in x["beats"]),
                          "chars": names} for x in tl["scenes"]]
        # 场次以真实地点为边界。旧骨架或长正文即使被拆成多个片段，只要相邻地点
        # 相同就先合成一个场景，再交给导演层，避免重复场景卡和重复设定图。
        _coalesce_adjacent_scenes(tl)
        sk_scenes = [{"no": x["no"], "location": x.get("location") or "",
                      "beats": x.get("beats") or [],
                      "seconds": sum(float(b.get("seconds") or 9)
                                     for b in (x.get("beats") or [])),
                      "chars": names} for x in tl["scenes"]]
        _save(sid, tl)

        j["total"] = len(tl["scenes"])
        director_failures = 0
        for sc, sk in zip(tl["scenes"], sk_scenes):
            j["step"] = "第%d场 · 导演压缩分镜（%d 拍）" % (sc["no"], len(sk.get("beats") or []))
            sc["warn"] = ""
            try:
                r = dl.direct_scene(sk, scene_cards.get(sc["location"]),
                                    [c for c in chars_all
                                     if c.get("name") in (sk.get("chars") or names)],
                                    engine_grammar=grammar,
                                    scene_id="%02d" % sc["no"])
            except Exception as e:
                sc["warn"] = "导演分镜失败：" + str(e)[:150]
                director_failures += 1
                j["done"] += 1
                _save(sid, tl)
                continue
            shots = r.get("shots") or []
            for x in shots:
                x.setdefault("refs", [])
                # 段编译层读 action 当镜头内容，framing/camera 是运镜
                x.setdefault("framing", "")
                x.setdefault("camera_movement", x.get("camera") or "")
            sc["shots"] = shots
            sc["state_block"] = r.get("state_block") or ""
            # 排段：只算，不生成
            sc["segments"] = [
                {"no": i + 1, "seconds": g["seconds"], "location": g.get("location"),
                 "shot_ids": [y.get("shot_id") for y in g["shots"]],
                 "shooting_notes": _shooting_notes, "prompt": "", "video": ""}
                for i, g in enumerate(vl.split_segments(shots))]
            j["done"] += 1
            _save(sid, tl)
        # 盖正文快照：以后正文改了能检测出"分镜是旧的"，不再默默用陈旧分镜。
        # 只要有场次失败，就保留 timeline_stale，避免部分结果被误报为已经同步。
        if not director_failures:
            try:
                _sc.stamp_source(sid, _ep_now)
                _saga = _sc.get_saga(sid)
                _done_ep = _sc.episode(_saga, _ep_now)
                if _done_ep is not None:
                    _done_ep["timeline_stale"] = False
                    _sc.save_saga(sid, _saga)
            except Exception:
                pass
        else:
            try:
                _saga = _sc.get_saga(sid)
                _done_ep = _sc.episode(_saga, _ep_now)
                if _done_ep is not None:
                    _done_ep["timeline_stale"] = True
                    _sc.save_saga(sid, _saga)
            except Exception:
                pass
        j["step"] = "部分失败" if director_failures else "完成"
        j["note"] = "%d 场 · %d 镜 · %d 段" % (
            len(tl["scenes"]),
            sum(len(x.get("shots") or []) for x in tl["scenes"]),
            sum(len(x.get("segments") or []) for x in tl["scenes"]))
        if director_failures:
            j["note"] += " · %d 场需重试" % director_failures
    except Exception as e:
        j["err"] = str(e)[:300]
        j["step"] = "失败"
        try:
            _save(sid, tl)      # 已经做完的几场要保住，别因为后面失败全丢了
        except Exception:
            pass
    finally:
        j["running"] = False


@post(("/api/timeline/", "/build-all"))
def tl_build_all(h, path, d):
    sid = path.split("/")[3]
    _ep_of(sid, d)
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": True, "data": {"job": j, "note": "已经在跑了"}})
    try:
        from api.saga_api import _job as _sjob1
        if (_sjob1(sid) or {}).get("running"):
            return _RESP({"ok": False, "error": "设定页的任务还在跑，等它结束再点"}, 400)          # P353
    except Exception:
        pass
    t = threading.Thread(target=_build_all, args=(sid, int(d.get("max_chars") or 400)),
                         daemon=True)
    t.start()
    return _RESP({"ok": True, "data": {"job": _job(sid)}})


@post(("/api/timeline/", "/direct-next"))
def tl_direct_next(h, path, d):
    """逐段导演：只生成下一段分镜（把已确认的前段当上下文），追加进 timeline。"""
    sid = path.split("/")[3]
    _ep_of(sid, d)
    ep = _CUR_EP.get(sid, 1)
    from cores import director as _dir
    try:
        seg, _raw = _dir.direct_next(sid, ep)
    except RuntimeError as ex:
        # 校验拒绝是**业务结果**不是服务器故障——三轮没写合格而已。
        # 返回 400 并把原因原样带出，界面才能显示「运镜超限」这种可读信息，
        # 而不是一句「服务器错误」让人以为系统崩了（2026-08-29）。
        return _RESP({"ok": False, "error": str(ex)[:300], "kind": "reject"}, 400)
    except Exception as ex:
        return _RESP({"ok": False, "error": str(ex)[:200]}, 500)
    if seg is None:
        return _RESP({"ok": True, "data": {"done": True}})
    return _RESP({"ok": True, "data": {"done": False, "segment": seg, "no": seg.get("no")}})


@post(("/api/timeline/", "/segment-save"))
def tl_segment_save(h, path, d):
    """存用户改过的某段分镜文字（改完自动存，下一段就按改后的接）。"""
    sid = path.split("/")[3]
    _ep_of(sid, d)
    ep = _CUR_EP.get(sid, 1)
    no = int(d.get("no") or 0)
    prompt = str(d.get("prompt") or "")
    from cores import saga_core as _sc
    tl = _sc.ep_timeline(sid, ep) or {"scenes": []}
    for scn in tl.get("scenes") or []:
        for g in scn.get("segments") or []:
            if int(g.get("no") or 0) == no:
                if prompt.strip() != str(g.get("prompt") or "").strip():
                    if not g.get("prompt_edited"):
                        g["prompt_auto"] = str(g.get("prompt") or "")      # 留一份程序写的版本
                    g["prompt_edited"] = True                            # P312：手改过——重写提示词时不覆盖
                    g["prompt_edited_at"] = time.time()
                g["prompt"] = prompt
                m = re.search(r"本段时长[：:]\s*([\d.]+)", prompt)
                if m:
                    g["seconds"] = float(m.group(1))
                _sc.save_ep_timeline(sid, ep, tl)
                return _RESP({"ok": True, "data": {"no": no, "saved_at": time.time(), "prompt_edited": bool(g.get("prompt_edited"))}})
    return _RESP({"ok": False, "error": "没有这一段"}, 404)


@post(("/api/timeline/", "/direct-reset"))
def tl_direct_reset(h, path, d):
    """清空这一话的逐段分镜，从头再来。"""
    sid = path.split("/")[3]
    _ep_of(sid, d)
    ep = _CUR_EP.get(sid, 1)
    from cores import saga_core as _sc
    _sc.save_ep_timeline(sid, ep, {"scenes": []})
    return _RESP({"ok": True, "data": {"cleared": True}})


@post(("/api/timeline/", "/segment-delete"))
def tl_segment_delete(h, path, d):
    """删段：只能从最后一段往回删（3→2→1），不能删中间、不能跳。"""
    sid = path.split("/")[3]
    _ep_of(sid, d)
    ep = _CUR_EP.get(sid, 1)
    from cores import saga_core as _sc
    tl = _sc.ep_timeline(sid, ep) or {"scenes": []}
    all_no = [int(g.get("no") or 0) for scn in (tl.get("scenes") or [])
              for g in (scn.get("segments") or [])]
    if not all_no:
        return _RESP({"ok": False, "error": "没有可删的段"}, 400)
    last = max(all_no)
    want = int(d.get("no") or last)
    if want != last:
        return _RESP({"ok": False, "error": "只能从最后一段开始删（现在末段是第 %d 段）" % last}, 400)
    for scn in (tl.get("scenes") or []):
        scn["segments"] = [g for g in (scn.get("segments") or [])
                           if int(g.get("no") or 0) != last]
    _sc.save_ep_timeline(sid, ep, tl)
    return _RESP({"ok": True, "data": {"deleted": last}})


@post(("/api/timeline/", "/progress"))
def tl_progress(h, path, d):
    sid = path.split("/")[3]
    _ep_of(sid, d)
    st, tl = _load(sid)
    _rec = _recover(sid, tl, _ep_of(sid))     # P237 找回要按话号找
    return _RESP({"ok": True, "data": {"job": _job(sid), "timeline": tl,
                                       "recover_notes": _rec}})


@post(("/api/timeline/", "/redo-shot"))
def tl_redo_shot(h, path, d):
    """重做单个镜头：拿它所属的那一拍重拆，只替换这一个。"""
    sid = path.split("/")[3]
    _ep_of(sid, d)
    from cores import scene_layer
    st, tl = _load(sid)
    no, sid_ = int(d.get("scene_no") or 1), str(d.get("shot_id") or "")
    sc = next((x for x in tl.get("scenes") or [] if int(x.get("no")) == no), None)
    if not sc:
        return _RESP({"ok": False, "error": "没有第 %d 场" % no}, 400)
    idx = next((i for i, x in enumerate(sc.get("shots") or []) if x.get("shot_id") == sid_), -1)
    if idx < 0:
        return _RESP({"ok": False, "error": "找不到镜头 " + sid_}, 400)
    old = sc["shots"][idx]
    beat = next((b for b in sc.get("beats") or []
                 if str(b.get("beat")) == str(old.get("beat"))), None)
    if not beat:
        return _RESP({"ok": False, "error": "这一镜没有对应的节拍"}, 400)
    names = _names(sid)
    info = "%s，%s" % (sc.get("location") or "", sc.get("time_of_day") or "")
    on_stage = [n for n in names if n in str(beat.get("who") or "")]
    try:
        got = scene_layer.shots_for_beat(beat, info, names,
                                         sc["shots"][idx - 1] if idx else None,
                                         None, on_stage=on_stage)
    except Exception as e:
        return _RESP({"ok": False, "error": str(e)[:250]}, 500)
    new = got[0]
    new["shot_id"] = old.get("shot_id")
    new["location"] = old.get("location")
    new["refs"] = old.get("refs") or []
    sc["shots"][idx] = new
    _save(sid, tl)
    return _RESP({"ok": True, "data": {"shot": new}})


def _recover(sid, tl, ep=None):
    """段的 video 字段空了、盘上却有片子 → 填回去并存盘。
    （2026-09-02：重建分镜把路径抹掉了，片子文件都还在。）
    ep 必须传：文件名带话号之后，不传就只会去找第一话的片子（P237）。"""
    from cores import saga_core as _sc
    notes = []
    try:
        if _sc.recover_segment_media(sid, tl, ep=ep, notes=notes):
            _save(sid, tl, ep)
    except Exception:
        pass
    return notes


# ─────────── 生成下面 N 段：提示词 + 视频，一个按钮 ───────────
# 用户 2026-09-02：把「全部提示词」「全部视频」合成一个，选 1~10 再点确定；
# 选 5 就接着已出的往后写 5 段提示词，写完连着出这 5 段视频。

def _confirmed_arrangement(sid, ep=1):
    """已确认的安排或 None（按话取，P288）。本体在 saga_api（它兼容另一个人的 authoring.confirmed_arrangement）；
    懒加载防循环 import；取不到就当没有安排——不崩，只是少查三项。"""
    try:
        from api.saga_api import _confirmed_arrangement as _ca
        return _ca(sid, ep)
    except Exception:
        return None


def _cut_limit(slices, arrangement):
    """安排剪到下一话（cut_after_beat 0..3）时，本话最多出到第几段（1 起）；不剪返回 0。
    beat_index 单调不减（契约 A），所以第一个越过剪点的切片之前都是本话的；
    切片还没有 beat_index（切段那边没接上）→ 判不出 → 不剪。"""
    from cores import shotlist as SL
    cut = SL.cut_after_beat_of(arrangement)
    if cut < 0 or not slices:
        return 0
    if not any(SL.beat_index_of(s) >= 0 for s in slices):
        return 0
    for k, s in enumerate(slices):
        if SL.beat_index_of(s) > cut:
            return k
    return len(slices)


class _SubJob(dict):
    """P266：给 saga_api 那边的出图函数（_ep_scene_images 用 _set_step/_complete_step 记进度）当 job 用的代理。

    step / err / note / cancel / logs / durations 直通主任务 j——页面看到的步骤、日志、取消标志都是主任务的；
    total / done / percent / updated_at / step_started_at 自己存——_ep_scene_images 一进来就 j["total"]=场景数、
    每张图 done+1，直接给主任务会把「n 段视频」的进度条改成「3 张图」，出完图又跳回去。
    """
    _FWD = ("step", "err", "note", "cancel", "logs", "durations")

    def __init__(self, main):
        dict.__init__(self, total=0, done=0, percent=0, updated_at=time.time(), step_started_at=time.time())
        self._main = main

    def __getitem__(self, k):
        if k in self._FWD:
            return self._main[k]
        return dict.__getitem__(self, k)

    def __setitem__(self, k, v):
        if k in self._FWD:
            self._main[k] = v
        else:
            dict.__setitem__(self, k, v)

    def get(self, k, default=None):
        if k in self._FWD:
            return self._main.get(k, default)
        return dict.get(self, k, default)

    def setdefault(self, k, default=None):
        if k in self._FWD:
            return self._main.setdefault(k, default)
        return dict.setdefault(self, k, default)

    def update(self, *a, **kw):
        for k, v in dict(*a, **kw).items():
            self[k] = v


def _ensure_refs(sid, ep, targets, j, chk, JC):
    """P266：出片前把目标段绑的人物/场景里**还没有已采用设定图的**先出图。

    浏览器实测 STORY_164：分镜页第二次确认 → 写提示词 → 出片前门 → 直接出片，一张设定图都没出过，
    segment_api 抛「第 1 段没有参考图」。设定图本来是「全部生成」链里出的，走分镜页这条路没人管。
    · 人物：候选 = cast_cards 里名字在目标段 chars 里的（chars 全空则全部 cast_cards），去掉已有已采用图的；
      调 authoring._gen_char_images（设计脸 → 写提示词 → Krea 出图 → 采用）。
    · 场景：目标段 scene 对应的场景卡没有已采用图 → 用 _SubJob 调 saga_api._ep_scene_images（自己设计场景、出图、采用）。
    取消：JC（saga 的 JobCancelled）映射成本模块的 _Cancelled；其他异常记日志后照旧尝试出片（缺图那步会自己报）。
    """
    import os as _os
    from cores import authoring as _au
    _jc = JC if isinstance(JC, type) and issubclass(JC, BaseException) else _Cancelled
    try:
        from cores import saga_core as _sc
        tl = _sc.ep_timeline(sid, ep) or {"scenes": []}
        want = {int(t) for t in (targets or [])}
        tg = [g for sc0 in (tl.get("scenes") or []) for g in (sc0.get("segments") or [])
              if int(g.get("no") or 0) in want]
        try:
            from cores import opening as _op0
            if 1 in want or not want:
                tg = list(tl.get("opening") or _op0.ensure(sid, ep) or []) + tg     # P316：开场段绑的人物/场景一起补图
        except Exception:
            pass
        chars = []
        scenes = []
        for g in tg:
            for nm in (g.get("chars") or []):
                nm = str(nm or "").strip()
                if nm and nm not in chars:
                    chars.append(nm)
            s = str(g.get("scene") or "").strip()
            if s and s not in scenes:
                scenes.append(s)
        adopted = set()
        for v in asset_core.list_assets(sid, "visuals"):
            if v.get("status") == "adopted" and v.get("owner_id") and v.get("path") and _os.path.exists(str(v["path"])):
                adopted.add(v["owner_id"])
    except _jc:
        raise _Cancelled()
    except _Cancelled:
        raise
    except Exception as ex:
        j.setdefault("logs", []).append({"level": "error", "msg": "补参考图失败（照旧尝试出片）：%s" % str(ex)[:120]})
        return

    # 人物
    try:
        cast = _au.cast_cards(sid) or []
        try:
            _st0 = story_core.get_story(sid) or {}
            _order0, _cam = _au.h3_char_order(cast, dict(_st0.get("settings") or {}))
        except Exception:
            _cam = ""
        cand = [c for c in cast if str(c.get("name") or "").strip() and (not chars or str(c.get("name") or "").strip() in chars)
                and str(c.get("name") or "").strip() != _cam                       # 第一视角的摄像机主角不带图（和预览缺图口径一致）
                and c.get("character_id") not in adopted]
        if cand:
            names = [str(c.get("name") or "") for c in cand]
            j["total"] = int(j.get("total") or 0) + len(cand)
            chk("补出缺的人设图：%s" % "、".join(names))
            st = story_core.get_story(sid) or {}
            r = _au._gen_char_images(sid, cand, st.get("settings") or {}, on_step=chk) or {}
            j["done"] = int(j.get("done") or 0) + len(cand)
            if r.get("failed"):
                _msg = "这几个人设图没出来：%s" % "、".join(str(x) for x in r["failed"][:4])
                j.setdefault("logs", []).append({"level": "error", "msg": _msg})
                j["note"] = (j.get("note") or "") + "；" + _msg
    except _jc:
        raise _Cancelled()
    except _Cancelled:
        raise
    except Exception as ex:
        j.setdefault("logs", []).append({"level": "error", "msg": "补参考图失败（照旧尝试出片）：%s" % str(ex)[:120]})

    # 场景
    try:
        by_name = {str(x.get("name") or "").strip(): x for x in (asset_core.list_assets(sid, "scenes") or [])}
        miss = [nm for nm in scenes if nm in by_name and by_name[nm].get("scene_id") not in adopted]
        if miss:
            j["total"] = int(j.get("total") or 0) + len(miss)
            chk("补出缺的场景图：%s" % "、".join(miss))
            from api.saga_api import _ep_scene_images as _esi
            _esi(sid, ep, _SubJob(j), skip_existing=True)
            j["done"] = int(j.get("done") or 0) + len(miss)
    except _jc:
        raise _Cancelled()
    except _Cancelled:
        raise
    except Exception as ex:
        j.setdefault("logs", []).append({"level": "error", "msg": "补参考图失败（照旧尝试出片）：%s" % str(ex)[:120]})


def _gen_next(sid, ep, n, size_tier=None, force=False, prompts_only=False):
    from cores import authoring as _au, saga_core as _sc
    from api import segment_api as _seg
    j = _job(sid)
    try:
        j.pop("blocked", None)          # 上一次停在"需要你确认"，这次重跑要把它清掉，页面才不会一直画旧问题
        j.update(running=True, err="", step="准备", done=0, total=n * 2, note="",
                 cancel=False)

        def _chk(msg=None):
            """每步之间看一眼取消标志；on_step 也走这里。"""
            if j.get("cancel"):
                raise _Cancelled()
            if msg is not None:
                j["step"] = msg

        if not (_ep_body(sid, ep) or "").strip():
            j.update(running=False, step="缺剧本",
                     err="第%d话还没有剧本。先去 ⚙️设定 页点「按原文生成剧本」。" % ep)
            return
        tl = _sc.ep_timeline(sid, ep) or {"scenes": []}
        _rec_notes = []
        _sc.recover_segment_media(sid, tl, ep=ep, notes=_rec_notes)
        for _m in _rec_notes:
            j.setdefault("logs", []).append({"level": "warn", "msg": _m})
        # 【剧本改过就全部重写提示词】P268：next_prompts 只补没提示词的段，剧本重生成后旧提示词原封不动，
        # 预览说的是新剧本、片子出的是旧剧本（浏览器实测 STORY_164：5 段旧提示词 vs 4 段新画面稿）。
        # 已出的视频按段号保留（build_timeline 的规矩），stamp_source 顺手清掉 stale。
        _rewrote = 0
        try:
            _had_ps = any(str(g.get("prompt") or "").strip()
                          for sc0 in (tl.get("scenes") or []) for g in (sc0.get("segments") or []))
            if _had_ps and _sc.timeline_stale(sid, ep):
                # P339：不再整表重写——找第一个变了的切片，从那一段起作废（有视频的标过期），后面 next_prompts 按新剧本补写
                _d, _nv = _au.apply_slicing_drift(sid, ep)
                if _d:
                    _chk("剧本改过了，从第 %d 段起重写提示词" % _d)
                    _rewrote = _nv
                    j.setdefault("logs", []).append({"level": "info", "msg": "剧本改过了：第 %d 段起作废 %d 段，按新剧本重写（前面的段和已出的视频不动）" % (_d, _nv)})
                else:
                    j.setdefault("logs", []).append({"level": "info", "msg": "剧本改过了，但切片没变，提示词不用重写"})
                try:
                    _sc.stamp_source(sid, ep)
                except Exception:
                    pass
                tl = _sc.ep_timeline(sid, ep) or {"scenes": []}
        except _Cancelled:
            raise
        except Exception as _sx:
            # P354：不再停下问（界面没有确认入口了）——报成失败，进度条出 ❌ 和原因，再点 🎬 就重试
            j.setdefault("logs", []).append({"level": "error", "msg": "按新剧本重写提示词失败：%s" % str(_sx)[:120]})
            j.update(running=False, step="剧本改了但提示词没重写成",
                     err="按新剧本重写提示词失败：%s（再点一次 🎬 重试）" % str(_sx)[:200])
            return
        # P326d：答过「谁说的」之类改了画面稿的段，先按段重写提示词（带上一段 tail）
        try:
            _saga_r, _e_r = _au._ep(sid, ep, create=False)
            _redo_r = sorted({int(x) for x in ((_e_r or {}).get("redo_segments") or []) if int(x) > 0})
            if _redo_r:
                from api.saga_api import _prev_tail_of as _ptail
                _chk("按你答的重写第 %s 段提示词" % "、".join(str(x) for x in _redo_r))
                for _i in _redo_r:
                    _ps_r = _au.make_h3_prompts(sid, ep, start=_i - 1, count=1, prev_tail=_ptail(sid, ep, _i), on_step=_chk) or []
                    if _ps_r:
                        _au.build_timeline(sid, ep, _ps_r)
                _au._update_ep(sid, ep, redo_segments=[])
                tl = _sc.ep_timeline(sid, ep) or {"scenes": []}
        except _Cancelled:
            raise
        except Exception as _rx:
            j.setdefault("logs", []).append({"level": "error", "msg": "按答案重写提示词失败：%s" % str(_rx)[:100]})
        segs = [g for sc0 in (tl.get("scenes") or []) for g in (sc0.get("segments") or [])]
        # 从最后一段有视频的往后数 N 段
        # P282：按旧剧本出的段（stale_video）不算"已出"，它们要按新剧本重出（旧文件留着，出成功才顶替）
        done_v = [int(g.get("no") or 0) for g in segs
                  if str(g.get("video") or "").strip() and os.path.exists(str(g.get("video")))
                  and not g.get("stale_video")]
        first = (max(done_v) if done_v else 0) + 1
        if prompts_only:
            # P381：只写提示词时从最后一段有提示词的往后数（一次最多 10 段，页面循环叫；按视频数算会永远卡在前 10 段）
            done_p = [int(g.get("no") or 0) for g in segs if str(g.get("prompt") or "").strip()]
            first = max(first, (max(done_p) if done_p else 0) + 1)
        targets = list(range(first, first + n))

        # 【剪到下一话】安排说只出到某个 beat：目标段只到剪的位置，提示词也只写到那儿（契约 D）
        _arr = _confirmed_arrangement(sid, ep)
        _limit = 0
        if _arr is not None:
            try:
                _saga0, _e0 = _au._ep(sid, ep, create=False)
                _limit = _cut_limit(_au.episode_slices(sid, ep, cut=False), _arr)   # 剪点要按情节段算；P336：和出提示词同一份切片（含并短段）
            except Exception:
                _limit = 0
        _cut_note = ""
        if _limit:
            from cores import shotlist as _SL0
            _cut_note = "后半部分留到下一话（本话只出到「%s」，共 %d 段）" % (
                _SL0.BEAT_STAGES[_SL0.cut_after_beat_of(_arr)], _limit)
            if targets[0] > _limit:
                j.update(running=False, step="已到本话结尾",
                         note="%s；前 %d 段视频都已出完" % (_cut_note, _limit))
                return
            targets = [t for t in targets if t <= _limit]

        # ① 提示词：补到第 first+n-1 段为止（已有的不重写）
        j["step"] = "写第 %d~%d 段的视频提示词" % (targets[0], targets[-1])
        new_ps, total = _au.next_prompts(sid, ep, targets[-1], on_step=_chk)
        _chk()
        if total and targets[0] > total:
            j.update(running=False, step="已到结尾",
                     note="这一话剧本只够切 %d 段，前 %d 段视频都已出完" % (total, total))
            return
        targets = [t for t in targets if t <= (total or targets[-1])]
        j["total"] = n + len(targets)
        j["done"] = n
        j["note"] = ("新写提示词 %d 段" % len(new_ps)) if new_ps else \
            (("按新剧本重写提示词 %d 段" % _rewrote) if _rewrote else "提示词已有，直接出片")
        if _cut_note:
            j["note"] += "；" + _cut_note

        # ①b 出片前门（第二次确认，契约 H）：写完提示词、烧 GPU 之前先看这条片子能不能讲清故事。
        #    懒加载 saga_api 防循环 import；门本身出错照旧出片，不让检查器把出片链拖死。
        _chk("出片前检查")
        try:
            from api.saga_api import _preflight_gate as _gate_fn, JobCancelled as _JC
        except Exception:
            _gate_fn, _JC = None, None
        if _gate_fn is not None:
            try:
                _gate = _gate_fn(sid, j, targets, ep=ep, force=force, fix_only=True)   # P353：只修不问，永远不停
            except _JC:
                raise _Cancelled()
            except _Cancelled:
                raise
            except Exception as _gx:
                # P353：检查器自己炸了只记日志，照旧出片（用户不再确认）
                j.setdefault("logs", []).append({"level": "warn", "msg": "出片前自修没跑起来（照旧出片）：%s" % str(_gx)[:120]})
                _gate = {"blocked": False}
            if _gate.get("blocked"):
                j["note"] = (j.get("note") or "") + "；出片前自修没完成，先不出片"
                return
            _chk()

        if prompts_only:
            # P312：只写/重写提示词，不出图不出视频（文字按钮、验收用）
            j.update(running=False, step="提示词已写好（没出图没出视频）", note=(j.get("note") or "") + "；只写提示词")
            return
        # ①c 参考图（P266）：目标段绑的人物/场景还没有设定图的先出，不然出片那步只会抛「没有参考图」
        _ensure_refs(sid, ep, targets, j, _chk, _JC)
        _chk()

        # ①d 开场段（P316）：场景板全景 + 人物亮相，先于第 1 段出；出不来只记日志不拦正片
        try:
            _n_op, _e_op = _seg.render_opening_all(sid, ep, size_tier=size_tier, log_fn=_chk, chk=_chk)
            if _n_op:
                j.setdefault("logs", []).append({"level": "info", "msg": "开场段出了 %d 段（场景和人物亮相）" % _n_op})
            if _e_op:
                j.setdefault("logs", []).append({"level": "error", "msg": _e_op})
        except _Cancelled:
            raise
        except Exception as _ox:
            if j.get("cancel") or "已取消" in str(_ox):
                raise _Cancelled()
            j.setdefault("logs", []).append({"level": "error", "msg": "开场段：%s" % str(_ox)[:120]})
        _chk()

        # ② 视频：目标段里还没片子的，依次出
        outs = []
        for t in targets:
            tl = _sc.ep_timeline(sid, ep) or {"scenes": []}
            g = next((x for sc0 in (tl.get("scenes") or []) for x in (sc0.get("segments") or [])
                      if int(x.get("no") or 0) == t), None)
            if not g or not str(g.get("prompt") or "").strip():
                j["note"] += "；第 %d 段没有提示词，跳过" % t
                j["done"] += 1
                continue
            if str(g.get("video") or "").strip() and os.path.exists(str(g.get("video")))                     and not g.get("stale_video"):
                j["done"] += 1
                continue
            _chk("生成第 %d 段视频（%d/%d）" % (t, len(outs) + 1, len(targets)))
            try:
                patch = _seg.render_segment_checked(sid, ep, 1, t, size_tier=size_tier,
                                                    log_fn=_chk)
                outs.append(patch.get("video"))
                # 重出成功了：这一段不再是"按旧剧本出的"（P282）
                try:
                    _tl_now = _sc.ep_timeline(sid, ep) or {"scenes": []}
                    for _sc0 in (_tl_now.get("scenes") or []):
                        for _x in (_sc0.get("segments") or []):
                            if int(_x.get("no") or 0) == t:
                                _x.pop("stale_video", None)
                    _sc.save_ep_timeline(sid, ep, _tl_now)
                except Exception:
                    pass
            except _Cancelled:
                raise
            except Exception as ex:
                if j.get("cancel") or "已取消" in str(ex):
                    raise _Cancelled()
                j.update(running=False, step="第 %d 段出片失败" % t, err=str(ex)[:200],
                         note=j["note"] + "；已出 %d 段" % len(outs))
                return
            j["done"] += 1
        j.update(running=False, step="完成",
                 note=j["note"] + "；本次出片 %d 段（第 %d~%d 段）" % (
                     len(outs), targets[0], targets[-1]) if targets else j["note"])
    except _Cancelled:
        j.update(running=False, step="已取消", err="",
                 note=(j.get("note") or "") + "；已生成的部分保留")
    except Exception as ex:
        j.update(running=False, step="出错", err=str(ex)[:200])


class _Cancelled(Exception):
    """用户点了取消。"""


def cancel_project(sid):
    """取消这个项目所有正在跑的生成：分镜页任务 + 设定页任务 + 正在生的 H3。"""
    out = {"timeline": False, "saga": False}
    j = _job(sid)
    if j.get("running"):
        j["cancel"] = True
        j["step"] = "正在取消…"
        out["timeline"] = True
    try:
        from api import saga_api as _sa
        js = _sa._job(sid)
        if js.get("running"):
            js["cancel"] = True
            js["step"] = "正在取消…"
            out["saga"] = True
    except Exception:
        pass
    try:
        from models import h3_client as _h3
        _h3.request_cancel()
    except Exception:
        pass
    return out


@post(("/api/timeline/", "/cancel"))
def tl_cancel(h, path, d):
    return _RESP({"ok": True, "data": cancel_project(path.split("/")[3])})


@post(("/api/timeline/", "/voice-check"))
def timeline_voice_check(h, path, d):
    """旧接口已停用，避免旧页面或调用方再次启动语音识别。"""
    return _RESP({"ok": False, "error": "语音核对已停用，视频生成后保留原始音轨。"}, 410)


@post(("/api/timeline/", "/merge"))
def timeline_merge(h, path, d):
    """P399：合成成片（后台）。不传 segs＝整话；传 segs=[3,4,5] 只合这几段。"""
    sid = path.split("/")[3]
    _ep_of(sid, d)
    ep = _CUR_EP.get(sid, 1)
    segs = [int(x) for x in ((d or {}).get("segs") or []) if str(x).strip()]
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "分镜任务还在跑，等它结束再合成"}, 400)
    j.update(running=True, err="", step="合成成片", done=0, total=1, note="", cancel=False)

    def _run():
        try:
            from cores import episode_merge as _em
            dst = _em.merge_episode(sid, ep, segs=segs or None)
            if not dst:
                j.update(running=False, err="没有可合成的段视频", step="没有可合成的段视频")
                return
            j["merged"] = dst
            j.update(running=False, done=1, step="合成完成：" + os.path.basename(dst))
        except Exception as e:
            j.update(running=False, err=str(e)[:200], step="合成失败")

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True, "segs": segs}})


@post(("/api/timeline/", "/films"))
def timeline_films(h, path, d):
    """P399：本项目所有成片。"""
    sid = path.split("/")[3]
    from cores import episode_merge as _em
    items = _em.list_films(sid)
    for it in items:
        it["url"] = "/files/" + it["path"]
    return _RESP({"ok": True, "data": {"items": items}})


@post(("/api/timeline/", "/rerender"))
def timeline_rerender(h, path, d):
    """重渲指定几段（后台），用现有提示词。body: {"segs": [4, 8]}"""
    from api import segment_api as _seg
    sid = path.split("/")[3]
    _ep_of(sid, d)
    segs = [int(x) for x in ((d or {}).get("segs") or [])]
    if not segs:
        return _RESP({"ok": False, "error": "没指定段"}, 400)
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    ep = _CUR_EP.get(sid, 1)

    def _run():
        try:
            j.update(running=True, err="", step="准备重渲", done=0, total=len(segs), note="", cancel=False)
            st, tl = _load(sid)
            for k, n in enumerate(segs):
                if j.get("cancel"):
                    break
                sc = next((s for s in tl.get("scenes") or [] if any(int(g.get("no") or 0) == n for g in s.get("segments") or [])), None)
                if not sc:
                    continue
                j["step"] = "重渲第 %d 段" % n
                _seg.render_segment_checked(sid, ep, int(sc.get("no") or 1), n, log_fn=lambda m: j.__setitem__("step", "第 %d 段：%s" % (n, str(m)[:60])))   # P394：重渲也核对台词
                j["done"] = k + 1
            j.update(running=False, step="重渲完成")
        except Exception as e:
            j.update(running=False, err="重渲失败：" + str(e)[:200])

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


# ─────────── 出片前预览：每段发生什么／谁出场／在哪里／关键台词／几秒（契约 G）───────────

_SHOT_LINE = ("镜头", "画面：", "画面:", "【", "#")


# 动作词：判"这句是不是在演事"，不认任何故事内容（P281）
_ACT_RE = __import__("re").compile(
    "走|跑|跳|蹲|站|坐|躺|转身|回身|抬|低|伸|抓|握|捏|抱|举|放|推|拉|拨|摸|碰|扶|递|接|拿|搬|端|"
    "看|望|盯|瞥|扫|瞧|注视|打量|皱|笑|哭|喊|叫|说|问|答|点头|摇头|叹|吸|呼|喘|咬|吃|啃|喝|"
    "开门|关门|敲|拧|翻|撕|扔|丢|捡|落|掉|停|等|跟|追|躲|藏|指|挥|握紧|松开|探|钻|窜|叼")
_SENT_END_RE = __import__("re").compile(r"(?<=[。！？!?；;])")


def _card_text(text):
    """一个切片 → (这一段实际要拍什么 ≤60 字, 第一句台词「名字：台词」≤30 字)。纯代码，不问模型。

    P281：原来只取第一行非台词文字，164 第 1 段那行是环境描写（「面包店内光线昏黄…」），
    用户在预览上看不出这一段到底拍什么。现在按顺序挑**带动作的句子**拼起来；
    一句都挑不出来（整段都是环境描写）才退回第一行。
    """
    from cores import shotlist as SL
    what, line, first = "", "", ""
    picked = []
    for raw in str(text or "").splitlines():
        s = raw.strip()
        if not s or s.startswith("──") or s.startswith("—") or any(s.startswith(p) for p in _SHOT_LINE):
            continue
        if SL._PIC_LINE_RE.match(s):
            continue
        if not first:
            first = s
        for sent in _SENT_END_RE.split(s):
            sent = sent.strip()
            if len(sent) < 4:
                continue
            if _ACT_RE.search(sent):
                picked.append(sent)
                if sum(len(x) for x in picked) >= 60:
                    break
        if sum(len(x) for x in picked) >= 60:
            break
    what = ("".join(picked) or first)
    dl = SL.pictures_dialogue(text)
    if dl:
        _i, w, q = dl[0]
        line = "%s：%s" % (w, q)
    return what[:60], line[:30]


def _images_missing(sid, where_names):
    """P267：预览要告诉用户「还没有设定图的人物/场景」——出片时这些会先出图，时间要算进去。
    返回 {"characters": [名字], "scenes": [名字]}。
    · 人物限 cast_cards（本话安排人物表里的人；过期的自动卡不算）；第一视角的摄像机主角不带图，不算缺。
    · 场景只看 where_names（预览卡上各段在哪）里**是场景卡名**且没图的，去重保序；不是卡名的地点不算——
      那是清单/门要问用户的事，不是缺图。
    """
    from cores import authoring as _au
    have = {nm for nm, p in (_ref_images(sid) or {}).items() if p and os.path.exists(str(p))}
    st = story_core.get_story(sid) or {}
    settings = dict(st.get("settings") or {})
    try:
        settings.update(project_settings.get(st))
    except Exception:
        pass
    cards = _au.cast_cards(sid) or []
    try:
        _order, cam = _au.h3_char_order(cards, settings)
    except Exception:
        cam = ""
    chars = []
    for c in cards:
        nm = str(c.get("name") or "").strip()
        if nm and nm != cam and nm not in have and nm not in chars:
            chars.append(nm)
    scn = set(_scene_names(sid))
    scenes = []
    for w in where_names or []:
        w = str(w or "").strip()
        if w and w in scn and w not in have and w not in scenes:
            scenes.append(w)
    return {"characters": chars, "scenes": scenes}


_ETA_MAX_SEGS = 10        # 「生成下面 N 段」一次最多 10 段，预估也只算前 10 段


def _eta_sec(cards, cut, rate):
    """P267：待出片段的渲染时间预估 → (预估秒数, 待出段数, 待出视频总秒数)。
    待出 = 没有 has_video、且没被剪到下一话（cut>=0 时 beat_index>cut 的不算）的前 10 段；
    每段的视频秒数按出片那边的口径 clamp(round(seconds or 12), 4, 15)（segment_api.render_segment 传给 H3 的就是这个）。
    rate = 每 1 秒视频要渲染几秒（h3_client.rate_for）。
    """
    from cores import shotlist as SL
    pend = []
    for c in cards or []:
        if not isinstance(c, dict) or c.get("has_video"):
            continue
        if cut >= 0 and SL.beat_index_of(c) > cut:
            continue
        pend.append(c)
    pend = pend[:_ETA_MAX_SEGS]
    vsec = 0
    for c in pend:
        try:
            s = float(c.get("seconds") or 0) or 12.0
        except Exception:
            s = 12.0
        vsec += max(4, min(15, int(round(s))))
    try:
        r = float(rate or 0)
    except Exception:
        r = 0.0
    return int(round(vsec * r)), len(pend), vsec


_IMAGE_ETA_SEC = 60       # 一张设定图（Qwen 写提示词 + Krea 出图，含切模型）按 60 秒估


@post(("/api/timeline/", "/preview"))
def tl_preview(h, path, d):
    """出片前一份简单预览：每段一张卡 + 清单检查结果（不自动修）+ 安排（预计时长 / 剪点 / 四段）。
    没有拍摄清单 → checked=false、report=null，页面要显示"未检查"，不能假装通过。"""
    from cores import authoring as _au, saga_core as _sc, shotlist as SL
    sid = path.split("/")[3]
    ep = _ep_of(sid, d)
    try:
        _saga, e = _au._ep(sid, ep, create=False)
    except Exception:
        e = None
    e = e or {}
    pics = str(e.get("pictures") or "")
    if not pics.strip():
        return _RESP({"ok": False, "error": "第%d话还没有剧本，先生成剧本" % ep}, 400)
    try:
        slices = list(_au._sliced(pics, _confirmed_arrangement(sid, ep)))   # 带安排切，切片才有情节段（预览实测全是 -1）
    except Exception as ex:
        return _RESP({"ok": False, "error": "切段失败：%s" % str(ex)[:160]}, 500)
    tl = _sc.ep_timeline(sid, ep) or {"scenes": []}
    segs = {int(g.get("no") or 0): g for sc0 in (tl.get("scenes") or []) for g in (sc0.get("segments") or [])}
    # P282：剧本改过、分镜还没跟上 → 已有的片子都是按旧剧本出的，预览里就得标出来，不许照打 ✅
    try:
        _tl_stale = bool(_sc.timeline_stale(sid, ep))
    except Exception:
        _tl_stale = False
    try:
        sl = _au.fresh_shotlist(sid, ep)
    except Exception:
        sl = None
    ref = _ref_images(sid)
    char_names = _names(sid)
    first_char_img = next((ref[nm] for nm in char_names if ref.get(nm)), "")
    arr = _confirmed_arrangement(sid, ep)

    cards, segs_v, slices_v = [], [], []
    for k, s in enumerate(slices):
        no = k + 1
        g = segs.get(no) or {}
        text = str(s.get("text") or "")
        what, line = _card_text(text)
        who = [str(x) for x in (g.get("chars") or []) if str(x)]
        if not who:
            # 段还没写提示词时没有 chars：按人物卡名字在这段文字里出现认；有清单就把别称归到本名
            body = SL.canon_text(sl, text) if sl else text
            who = [nm for nm in char_names if nm and nm in body]
        where = str(g.get("scene") or s.get("scene_hint") or "")
        if sl and (not where or not who):
            scn, score = SL.scene_for_text(sl, text)
            # 只有一场戏 → 每段都是它；判不出 → 沿用上一段（和出片前检查的口径一样，预览实测 12 段有 11 段空着）
            _scenes_all = sl.get("scenes") or []
            if (not scn or not score) and len(_scenes_all) == 1:
                scn, score = _scenes_all[0], 1
            if (not scn or not score) and cards:
                where = where or str(cards[-1].get("where") or "")
            if scn and score:
                where = where or str(scn.get("location") or "")
                # 这段只写「两人」没点名：按清单这场戏的在场名单给（清单有原文依据，不是猜）
                who = who or [SL.name_of_id(sl, c) for c in (scn.get("present") or []) if SL.name_of_id(sl, c)]
        seconds = g.get("seconds") or s.get("seconds") or 0
        try:
            seconds = round(float(seconds), 1)
        except Exception:
            seconds = 0
        thumb = ref.get(where) or next((ref[w] for w in who if ref.get(w)), "") or first_char_img
        video = str(g.get("video") or "")
        card = {"no": no, "beat": str(s.get("beat") or ""), "beat_index": SL.beat_index_of(s),
                "what": what, "who": who, "where": where, "line": line, "seconds": seconds,
                "thumb": ("/files/" + thumb) if thumb else "",
                "has_prompt": bool(str(g.get("prompt") or "").strip()),
                "has_video": bool(video.strip() and os.path.exists(video)) and not (g.get("stale_video") or _tl_stale),
                "stale_video": bool(g.get("stale_video") or _tl_stale) and bool(video.strip() and os.path.exists(video))}
        cards.append(card)
        slices_v.append({"no": no, "text": text, "seconds": seconds, "beat": card["beat"], "beat_index": card["beat_index"]})
        if g:
            segs_v.append({"no": no, "text": text, "scene": g.get("scene"), "chars": g.get("chars"),
                           "prompt": g.get("prompt"), "seconds": seconds,
                           "beat": card["beat"], "beat_index": card["beat_index"]})

    report, checked = None, False
    arr_view = None
    if arr is not None:
        arr_view = {"duration_sec": int(float(arr.get("duration_sec") or 0)),
                    "cut_after_beat": SL.cut_after_beat_of(arr),
                    "beats": [{"stage": str(b.get("stage") or ""), "text": str(b.get("text") or "")}
                              for b in (arr.get("beats") or []) if isinstance(b, dict)],
                    "budget": [dict(b) for b in (arr.get("budget") or []) if isinstance(b, dict)],
                    # 每个 beat 实际切出来多少秒，页面和预算对照；"未归属"非 0 说明切段还没接上 beat
                    "actual": SL.beat_seconds(slices_v),
                    "total_sec": int(round(sum(float(x.get("seconds") or 0) for x in slices_v)))}
    if sl:
        checked = True
        cards_n = [str(c.get("name") or "") for c in (_au.cast_cards(sid) or [])]      # 和出片前门同口径（P269）
        scards = _scene_names(sid)
        try:
            report = SL.check(sl, pics, segs_v, cards_n, scards, prose=str(e.get("prose") or ""),
                              arrangement=arr, slices=slices_v)
        except Exception as ex:
            checked, report = False, None
            arr_view = dict(arr_view or {}, error="检查器出错：%s" % str(ex)[:120])
    elif arr is not None:
        # 没清单：清单六项没法核（report 保持 null，页面显示"未检查"），安排的三项单独给
        try:
            a_items, a_asks = SL.arrangement_checks(arr, slices_v)
            arr_view["items"], arr_view["asks"] = a_items, a_asks
        except Exception:
            pass
    # P267：预估出片时间 + 还没有的设定图。档位/步数按出片那边同一个口径（segment_api._render_params），
    # 速率优先实测（h3_client.rate_for）。原来页面写死「约 n×1.5 分钟」，0.7 档实测每 1 秒视频约 55 秒。
    est = {"eta_sec": None, "eta_video_sec": None, "images_eta_sec": None, "pending": 0, "pending_video_sec": 0,
           "rate": None, "images_missing": {"characters": [], "scenes": []}}
    try:
        from api import segment_api as _seg
        from models import h3_client as _h3
        st = story_core.get_story(sid) or {}
        _tier, _ratio, _steps = _seg._render_params(st)
        _tier = _tier or _h3.SIZE_TIER
        _steps = _steps or _h3.TEMPLATE_STEPS
        rate, src = _h3.rate_for(_tier, _steps)
        cut = SL.cut_after_beat_of(arr) if arr is not None else -1
        eta_v, pending, pend_v = _eta_sec(cards, cut, rate)
        miss = _images_missing(sid, [c.get("where") for c in cards])
        n_img = len(miss["characters"]) + len(miss["scenes"])
        est = {"eta_sec": eta_v + n_img * _IMAGE_ETA_SEC, "eta_video_sec": eta_v,
               "images_eta_sec": n_img * _IMAGE_ETA_SEC, "pending": pending, "pending_video_sec": pend_v,
               "rate": {"tier": str(_tier), "steps": int(_steps), "sec_per_sec": rate, "source": src},
               "images_missing": miss}
    except Exception as ex:
        est["eta_error"] = "预估出错：%s" % str(ex)[:120]
    out = {"ok": True, "ep": ep, "cards": cards, "checked": checked, "report": report,
           "arrangement": arr_view, "total": len(cards)}
    out.update(est)
    return _RESP(out)


@post(("/api/timeline/", "/shotlist-scene"))
def tl_shotlist_scene(h, path, d):
    """P263：用户答「这场戏用哪张场景卡」→ 写进拍摄清单那场戏的 card。入参 {ep, scene_id, card}。
    出片前门的「地点」项对没卡的场戏出 asks kind=scene ref=场景id options=卡名单；页面选一个就打这里。
    存进清单（不是段）：下次门再查，scene_card 优先读 card，改绑/重写提示词都按它来。"""
    from cores import authoring as _au
    sid = path.split("/")[3]
    ep = _ep_of(sid, d)
    scene_id = str((d or {}).get("scene_id") or "").strip()
    card = str((d or {}).get("card") or "").strip()
    sl = _au.fresh_shotlist(sid, ep)
    if not sl:
        return _RESP({"ok": False, "error": "第%d话没有拍摄清单（或清单不是照现在这份剧本做的）" % ep}, 400)
    import re as _re
    _create = bool((d or {}).get("create"))
    _mnew = _re.match(r"^新建场景卡「(.+)」$", card)
    if _mnew:
        card, _create = _mnew.group(1).strip(), True
    if not card:
        return _RESP({"ok": False, "error": "没有这张场景卡：（空）"}, 400)
    s = next((x for x in (sl.get("scenes") or []) if isinstance(x, dict) and str(x.get("id") or "") == scene_id), None)
    if s is None:
        return _RESP({"ok": False, "error": "清单里没有这场戏：%s" % (scene_id or "（空）")}, 400)
    if card not in _scene_names(sid):
        if not _create:
            return _RESP({"ok": False, "error": "没有这张场景卡：%s" % card}, 400)
        # P269：安排地点表之外的地点（后巷、猫窝）原来没出路——候选只有现有卡。选「新建场景卡」就照清单那场戏建一张
        try:
            # P316b：用户点名新建的卡标 edited_by_user——美术指导归并时只标 merged_into、不删（实测被归并删掉后清单绑着空名出片）
            asset_core.create_scene(sid, {"name": card, "space": str(s.get("spot") or "").strip(),
                                          "contract_text": str(s.get("evidence") or "").strip()[:200],
                                          "edited_by_user": True, "user_made": True, "episode": int(ep or 1)})
        except Exception as ex:
            return _RESP({"ok": False, "error": "建场景卡失败：%s" % str(ex)[:120]}, 500)
    s["card"] = card
    s["place_unresolved"] = False
    _au._update_ep(sid, ep, shotlist=sl)
    return _RESP({"ok": True, "data": {"ep": ep, "scene_id": scene_id, "card": card,
                                       "location": str(s.get("location") or "")}})


@post(("/api/timeline/", "/shotlist-speaker"))
def tl_shotlist_speaker(h, path, d):
    """P323d：用户答「这一句谁说的」→ 写回清单那一行（speaker=卡 id、confidence=确定）和画面稿（说话人待定：… → 名字：…）。入参 {ep, line_n, name}。"""
    from cores import authoring as _au, shotlist as _SL
    sid = path.split("/")[3]
    ep = _ep_of(sid, d)
    name = str((d or {}).get("name") or "").strip()
    ln_n = str((d or {}).get("line_n") or "").strip()
    if not name:
        return _RESP({"ok": False, "error": "没选人"}, 400)
    _saga, e = _au._ep(sid, ep)
    sl = (e or {}).get("shotlist") if isinstance((e or {}).get("shotlist"), dict) else None
    if not sl:
        return _RESP({"ok": False, "error": "第%d话没有拍摄清单" % ep}, 400)
    ln = next((x for x in (sl.get("lines") or []) if str(x.get("n") or "") == ln_n), None)
    if ln is None:
        return _RESP({"ok": False, "error": "清单里没有这一句：%s" % ln_n}, 400)
    cid = _SL.id_of_name(sl, name)
    if not cid:
        # 候选里没有这个名字：按名字新增一个人物条目（不建卡，配角）
        sl.setdefault("cast", []).append({"id": "C%d" % (len(sl.get("cast") or []) + 1), "name": name, "aliases": [], "card": False})
        cid = sl["cast"][-1]["id"]
    ln["speaker"] = cid
    ln["confidence"] = "确定"
    ln["evidence"] = "用户指定"
    pics = str(e.get("pictures") or "")
    txt = str(ln.get("text") or "")
    new_pics, n = [], 0
    for row in pics.split("\n"):
        m = _SL._PIC_LINE_RE.match(row.strip())
        if m and _SL._same_line(m.group(3), txt) and m.group(1).strip() != name:
            row = "%s：%s" % (name, m.group(3).strip())
            n += 1
        new_pics.append(row)
    pics2 = "\n".join(new_pics)
    if pics2 != pics:
        sl["pictures_sig"] = _SL.content_sig(pics2)
        # P339：改了说话人 → 切法漂移处理（从变了的段起作废，出片前 next_prompts 补写），并盖快照
        _au._update_ep(sid, ep, pictures=pics2, body=pics2, shotlist=sl)
        try:
            _au.apply_slicing_drift(sid, ep)
        except Exception:
            pass
        try:
            from cores import saga_core as _sc2
            _sc2.stamp_source(sid, ep)
        except Exception:
            pass
    else:
        _au._update_ep(sid, ep, shotlist=sl)
    return _RESP({"ok": True, "data": {"ep": ep, "line_n": ln_n, "speaker": name, "patched_lines": n}})


@post(("/api/timeline/", "/opening-render"))
def tl_opening_render(h, path, d):
    """P316：单独重出某个开场段 {ep, k}；k 不传 = 没出的全出。后台跑。"""
    sid = path.split("/")[3]
    ep = _ep_of(sid, d)
    k = int(d.get("k") or 0)
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    j.update(running=True, err="", step="开场段", done=0, total=1, note="", cancel=False)

    def _run():
        from api import segment_api as _seg
        try:
            if k:
                _seg.render_opening(sid, ep, k, log_fn=lambda m: j.__setitem__("step", m))
                j.update(running=False, step="开场段 %d 出好了" % k)
            else:
                n, err = _seg.render_opening_all(sid, ep, log_fn=lambda m: j.__setitem__("step", m), chk=lambda m: j.__setitem__("step", m))
                j.update(running=False, step="开场段出了 %d 段" % n, err=err)
        except Exception as ex:
            j.update(running=False, step="开场段出片失败", err=str(ex)[:200])

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


@post(("/api/timeline/", "/opening-rebuild"))
def tl_opening_rebuild(h, path, d):
    """P316：按当前第 1 段的场景/人物重建开场段提示词（成片按提示词是否变化保留）。"""
    sid = path.split("/")[3]
    ep = _ep_of(sid, d)
    from cores import opening as _op
    try:
        op = _op.ensure(sid, ep, rebuild=True)
    except Exception as ex:
        return _RESP({"ok": False, "error": str(ex)[:200]}, 500)
    return _RESP({"ok": True, "data": {"opening": op}})


@post(("/api/timeline/", "/gate-chat"))
def tl_gate_chat(h, path, d):
    """P328②：出片前门的对话框。{ep, text?}：text 空只读当前问题和对话；有 text 就解析执行并回复。"""
    from api import saga_api as _sa
    sid = path.split("/")[3]
    ep = _ep_of(sid, d)
    try:
        out = _sa.gate_chat(sid, ep, str((d or {}).get("text") or ""), n=int((d or {}).get("n") or 5), page_asks=(d or {}).get("asks"))
    except Exception as ex:
        return _RESP({"ok": False, "error": "对话处理出错：%s" % str(ex)[:160]}, 500)
    return _RESP({"ok": True, "data": out})


@post(("/api/timeline/", "/gen-next"))
def tl_gen_next(h, path, d):
    """生成下面 N 段（提示词 + 视频）。n 取 1~10；size_tier 传 "1.0" 出成片档。"""
    sid = path.split("/")[3]
    ep = _ep_of(sid, d)
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": True, "data": {"job": j, "note": "已经在跑了"}})
    try:
        n = max(1, min(10, int(d.get("n") or 5)))
    except Exception:
        n = 5
    tier = str(d.get("size_tier") or "").strip() or None
    try:
        from api.saga_api import _job as _sjob0
        if (_sjob0(sid) or {}).get("running"):
            return _RESP({"ok": False, "error": "设定页的任务还在跑（写正文/提示词/出片），等它结束再点"}, 400)   # P353：同一话一个写手
    except Exception:
        pass
    # 页面上选了「就按现在的出片」：跳过出片前门那道拦截，照用户说的出（P280）
    force = bool((d or {}).get("force"))
    try:
        from api.saga_api import _job as _sjob
        _sj = _sjob(sid)
        if not _sj.get("running") and _sj.get("blocked"):
            _sj["blocked"] = []                                   # P328②：设定页那次停下的旧问题清单作废（用户已在对话框里答完接着出）
    except Exception:
        pass
    _po = bool((d or {}).get("prompts_only"))
    j.update(running=True, step="正在启动…", err="")             # 线程还没跑起来前就占住，连点两次不会起两个任务
    t = threading.Thread(target=_gen_next, args=(sid, ep, n, tier, force, _po), daemon=True)
    t.start()
    return _RESP({"ok": True, "data": {"job": _job(sid), "n": n, "ep": ep}})
