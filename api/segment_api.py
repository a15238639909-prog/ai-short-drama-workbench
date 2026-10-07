# -*- coding: utf-8 -*-
"""segment_api.py — 段级操作：编提示词 / 出视频 / 采用。

时间轴上的一格 = 一段视频（≤15秒）。用户在页面上点「生成这一段」走这里。

参考图怎么给，沿用 A/B 实测的结论（见 cores/video_layer.generation_plan）：
  · 新场景的第一段 → ref2va，人物图 + 场景图把人和空间立起来
  · 同地点接着上一段 → i2va，上一段的结束帧直接当第 1 帧，接缝无痕
"""
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from api import post
from cores import director
from api._shared import _RESP, ctx

globals().update(ctx())

_KEY = "timeline"

# 读改写整个故事对象不是原子的。并发重编时每个请求开头读的都是旧状态，
# 谁后保存谁把别人的成果抹掉。所以：慢活（Qwen/H3）在锁外干，
# 落盘前在锁里**重读最新状态**、只改自己那一段、立刻写回。
_SAVE_LOCK = threading.Lock()


def _apply(sid, scene_no, seg_no, patch, ep=None):
    """锁内读-改-写：把 patch 里的键写进指定段。返回最新的段。
    ep 不传就用最近一次请求的话号；后台任务（没有请求上下文）必须显式传。"""
    with _SAVE_LOCK:
        st, tl = _load(sid, ep)
        sc, g = _seg_of(tl, scene_no, seg_no)
        if not g:
            return None
        if str((patch or {}).get("video") or "").strip():
            g.pop("stale_video", None)                     # P339：按新剧本重出成功 → 不再是"按旧剧本出的"
        g.update(patch)
        _save(sid, tl, ep)
        return g


def new_pack(sid, g):
    """新链路（<SubjectN> 提示词）的参考图包：人物卡顺序 + 场景卡，
    和提示词里 <Subject 1..N> / <Picture 1..N> 一一对应，顺序不能乱。
    图在独立的 visuals 表里按 owner_id 关联，只认已采用版。"""
    from cores import reference_ready
    reference_ready.assert_references(sid, [g])
    adopted = {}
    for v in (asset_core.list_assets(sid, "visuals") or []):
        if str(v.get("status") or "") != "adopted":
            continue
        p = str(v.get("path") or "")
        if p and not os.path.isabs(p):
            p2 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "outputs", p)
            p = p2 if os.path.exists(p2) else p
        if p and os.path.exists(p):
            adopted.setdefault(str(v.get("owner_id") or ""), p)
    imgs = []
    # 人物顺序和"谁不带图"由 authoring.h3_char_order 统一决定（第一视角：主角是摄像机，不带图）
    from cores import authoring as _au
    _settings = (story_core.get_story(sid) or {}).get("settings") or {}
    _all = asset_core.list_assets(sid, "characters") or []
    _want_chars = [str(x) for x in ((g or {}).get("chars") or [])]      # 这一段只绑出场的人（提示词里也只列了他们）
    if (g or {}).get("no_chars") or ((g or {}).get("shot") and not _want_chars):
        _all = []                                                          # P316：开场的空场段不带任何人物图；P388：节拍段的空镜（在场为空）也不带——带了 Picture 1 就不是场景
    elif _want_chars:
        _all = [c for c in _all if str(c.get("name") or "") in _want_chars] or _all
    _order, _cam = _au.h3_char_order(_all, _settings)
    for c in _order:
        if c.get("name") == _cam:
            continue
        p = adopted.get(str(c.get("character_id") or ""))
        if p:
            imgs.append(p)
    # 场景图：这一段绑了哪张（g["scene"]）就只带那张；没绑就带全部有图的（老段兼容）
    _want = str((g or {}).get("scene") or "")
    for sc in (asset_core.list_assets(sid, "scenes") or []):
        if _want and str(sc.get("name") or "") != _want:
            continue
        p = adopted.get(str(sc.get("scene_id") or ""))
        if p:
            imgs.append(p)
    return imgs[:9]


def is_new_prompt(text):
    return "<Subject" in str(text or "")


def _with_pose_ref(prompt, n, scene_changed=False, prev_tail="", mode=None):
    """在场景行后面插一句姿势参考的说明。n = 这张图是第几张参考图。

    scene_changed：这一段换了场景板。换景是正常的（追逐戏本来就一路换），
    但要让观众看出是**人开过去了**，不是切到了另一个片子。
    """
    # 第一帧里有谁，要点名说。只说"和上一段末帧一致"时，模型会拿**场景参考板**当第一帧，
    # 开头 1.5 秒是一段没有人的空镜（2026-09-05 项目102 段4 实测）。
    _mode = str(mode or os.environ.get("V41_RELAY") or "soft").strip().lower()       # P330/P332：soft=默认（只参考姿势位置构图）/ first=旧硬接力 / space=只锁空间 / none=不加；P392：同场景硬切传 space
    if _mode == "none":
        return prompt
    if _mode == "soft":
        line = ("<Picture %d>是上一段结束时的画面，只用它参考在场人物的姿势、位置关系和构图（谁抱着谁、手在哪、脸朝哪、机位多远）；"
                "人物的脸、发型和衣服一律以前面的人设参考图为准，画面里每个角色只出现一个。" % n)
        out, done = [], False
        for l in str(prompt or "").split("\n"):
            out.append(l)
            if not done and re.match(r"^<Subject \d+>是.*场景", l):
                out.append(line)
                done = True
        if not done:
            out.insert(0, line)
        return "\n".join(out)
    if _mode == "space":
        line = ("<Picture %d>是这个地方上一段的成片帧：**只用来对齐房间结构、门窗家具的位置、光线方向和色调**，"
                "不是这一段的第一帧，也不规定景别和机位；图里出现的人不算参考，人物的脸、发型、身材一律以前面的人设参考图为准，"
                "画面里每个角色只出现一个。" % n)
        out, done = [], False
        for l in str(prompt or "").split("\n"):
            out.append(l)
            if not done and re.match(r"^<Subject \d+>是.*场景", l):
                out.append(line)
                done = True
        if not done:
            out.insert(0, line)
        return "\n".join(out)
    who = re.findall(r"^<Subject\s*\d+>([^是\n]{1,8})是<Picture", str(prompt or ""), re.M)
    who = [w.strip() for w in who if w.strip()]
    if str(prev_tail or "").strip():
        # P322⑨：只点名末帧里真有的人（上一段「这一段结束时」提到谁）——192 第 1 段末帧只有她的耳后特写，却被写成「第一帧里就有她、你」
        _in_tail = [w for w in who if w in str(prev_tail)]
        if _in_tail:
            who = _in_tail
    first = ("**第一帧的画面里就有%s**，他们在画面里的大小和位置和这张图上一样。"
             % "、".join(who)) if who else "**第一帧就是有人物在演的画面**，不是空镜。"
    line = (("<Picture %d>是上一段的最后一帧。**这一段的第一帧就是它的下一帧**："
             "景别（远景/全景/中景/近景/特写）、机位角度和高度、主体在画面里的大小和位置、"
             "人物朝向、光线方向和背景，第一帧全部和它一致，从这一帧直接往下动。" % n)
            + first +
            "这一段要换景别或换机位的话，先用这个景别起手，再用一次看得见的运镜"
            "（推、拉、跟、摇）过去，中途不要硬切。"
            + ("这一段的地方和上一段不一样了：**背景变是因为人一路开过去了**——"
               "第一帧仍然接着上一段最后一帧，新地方在这一段里由远及近**驶入画面**，"
               "人物、载具和运动方向一路不断。不是切到另一个地方重新开场。"
               if scene_changed else "")
            + "它只管接戏——人物的脸、发型和衣服一律以前面的人设参考图为准。")
    out, done = [], False
    for l in str(prompt or "").split("\n"):
        out.append(l)
        if not done and re.match(r"^<Subject \d+>是.*场景", l):
            out.append(line)
            done = True
    if not done:
        out.insert(0, line)
    return "\n".join(out)


def render_opening(sid, ep, k, size_tier=None, log_fn=None):
    """P316：出第 k 个开场段（timeline["opening"][k-1]）。参考图按段的 chars/scene 配包，文件名 seg_<sid>_e<ep>_open_<k>.mp4。"""
    from models import h3_client as h3
    from cores import saga_core as _sgc
    st, tl = _load(sid, ep)
    op = list(tl.get("opening") or [])
    g = next((x for x in op if int(x.get("k") or 0) == int(k)), None)
    if not g:
        raise RuntimeError("没有第 %s 个开场段" % k)
    prompt = str(g.get("prompt") or "")
    if not prompt.strip():
        raise RuntimeError("开场段 %s 没有提示词" % k)
    imgs = [x for x in new_pack(sid, g) if x and os.path.exists(x)][:9]
    if not imgs:
        raise RuntimeError("开场段 %s 没有参考图——先出人物/场景的设定图" % k)
    secs = float(g.get("seconds") or 4)
    if log_fn:
        log_fn("开场段 %s（%s %s）：%d 张参考图，%.0f 秒" % (k, "场景" if g.get("kind") == "scene" else "人物", g.get("name") or "", len(imgs), secs))
    _tier, _ratio, _steps = _render_params(st, size_tier)
    r = h3.generate("ref2va", prompt, images=imgs, seconds=min(15, max(4, int(round(secs)))),
                    ratio=_ratio, size_tier=_tier, steps=_steps)
    src = r.get("output_path") or r.get("path")
    out = os.path.join("outputs", "video", "seg_%s_e%d_open_%d.mp4" % (sid, int(ep or 1), int(k)))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    shutil.copy2(src, out)
    patch = {"mode": "ref2va", "seed": r.get("seed"), "used_refs": [os.path.basename(x) for x in imgs],
             "video": out, "size": "%sx%s" % (r.get("width"), r.get("height"))}
    for _k, _v in (("size_tier", r.get("size_tier")), ("ratio", r.get("ratio")), ("steps", r.get("steps"))):
        if _v not in (None, ""):
            patch[_k] = _v
    with _SAVE_LOCK:
        st2, tl2 = _load(sid, ep)
        for x in (tl2.get("opening") or []):
            if int(x.get("k") or 0) == int(k):
                x.update(patch)
        _save(sid, tl2, ep)
    return patch


def render_opening_all(sid, ep, size_tier=None, log_fn=None, chk=None):
    """P316：这一话还没出的开场段全出。返回 (出了几段, 错误信息)。开场段出不来不拦正片。"""
    from cores import opening as _op, story_core as _stc
    try:
        _settings = (_stc.get_story(sid) or {}).get("settings") or {}
        if not _op.enabled(_settings):
            return 0, ""
        op = _op.ensure(sid, ep)
    except Exception as ex:
        return 0, "开场段没建成：%s" % str(ex)[:100]
    n = 0
    for g in op:
        v = str(g.get("video") or "")
        if v and os.path.exists(v):
            continue
        if chk:
            chk("出开场段 %d/%d（%s %s）" % (int(g.get("k") or 0), len(op), "场景" if g.get("kind") == "scene" else "人物", g.get("name") or ""))
        try:
            render_opening(sid, ep, int(g.get("k") or 0), size_tier=size_tier, log_fn=log_fn)
            n += 1
        except Exception as ex:
            if "已取消" in str(ex):
                raise
            return n, "开场段 %s 出片失败：%s" % (g.get("k"), str(ex)[:120])
    return n, ""


def render_segment_checked(sid, ep, scene_no, seg_no, size_tier=None, hd=False, log_fn=None):
    """2026-09-22：停用语音核对；保留调用入口，单次生成并原样返回视频和音轨。"""
    return render_segment(sid, ep, scene_no, seg_no, size_tier=size_tier,
                          hd=hd, log_fn=log_fn)


def _render_params(st, size_tier=None, override=None):
    """这一段用哪个分辨率档、哪个画幅比、多少步（P242）。

    优先级：显式传参 > 项目设定 > h3_client 模块默认（环境变量/内置）。
    【为什么写在这儿而不是各调用点】render_segment 上游有四条路：
    批量出片、「下面N段」、单段重做 /rerender、语音不合格的自动重渲。
    写在调用点上，后两条必漏——设定页选了档，重做一段却还是老档。
    """
    from cores import project_settings as _ps
    o = override or {}
    try:
        p = _ps.get(st) or {}
    except Exception:
        p = {}
    tier = (str(o.get("size_tier") or "").strip() or str(size_tier or "").strip()
            or str(p.get("video_size_tier") or "").strip() or None)
    ratio = (str(o.get("ratio") or "").strip()
             or str(p.get("video_ratio") or "").strip() or None)
    raw = (str(o.get("steps") or "").strip() or str(p.get("video_steps") or "").strip()
           or str(os.environ.get("V41_H3_STEPS") or "").strip())
    try:
        steps = int(raw) or None
    except Exception:
        steps = None
    return tier, ratio, steps


def _render_critical_problems(st, tl, g, prompt, ep, seg_no):
    """新旧时间轴共用的出片门禁；网页单段按钮和批量出片必须走同一判断。"""
    from cores import authoring as _au
    critical = list(g.get("critical_problems") or [])
    if critical:
        return critical
    source_text = str(g.get("source_text") or "")
    if not source_text:
        slices = ((tl.get("slicing") or {}).get("slices") or [])
        try:
            source_text = str(slices[int(seg_no) - 1].get("text") or "")
        except (IndexError, TypeError, ValueError, AttributeError):
            source_text = ""
    current_problems = list(g.get("problems") or [])
    if source_text:
        episodes = (((st.get("saga") or {}).get("episodes")) or [])
        episode_row = next((x for x in episodes
                            if int(x.get("no") or 0) == int(ep or 1)), {})
        prose = str(episode_row.get("prose") or "")
        names = [str(x) for x in (g.get("chars") or []) if str(x).strip()]
        try:
            _is_dir = bool((((tl.get("slicing") or {}).get("slices") or [])[int(seg_no) - 1] or {}).get("director"))
        except Exception:
            _is_dir = False
        if prose and names and not _is_dir:
            source_text, _ = _au.canonicalize_picture_speakers(source_text, prose, names)      # P455⑥：导演段的说话人来自剧本，不按正文重猜
        try:
            current_problems += _au.h3_problems(
                prompt, source_text, [{"name": x} for x in names])
        except Exception:
            pass
    return _au.h3_critical_problems(
        prompt, source_text, current_problems,
        {"seconds": g.get("seconds"), "over": g.get("over")}, g.get("scene") or "")


def render_segment(sid, ep, scene_no, seg_no, size_tier=None, hd=False, log_fn=None):
    """出这一段的视频并写回段。返回写回的 patch（含 video 路径）。出不了就抛错。

    参考图按提示词格式分流：<SubjectN> 是新链路 → new_pack；
    「本段拍到」是旧导演段 → director_pack。两种都恒走 ref2va。
    """
    from models import h3_client as h3
    st, tl = _load(sid, ep)
    sc, g = _seg_of(tl, int(scene_no or 1), int(seg_no))
    if not g:
        raise RuntimeError("找不到第 %s 段" % seg_no)
    prompt = str(g.get("prompt") or "")
    if not prompt.strip():
        raise RuntimeError("第 %s 段还没有提示词" % seg_no)
    if is_new_prompt(prompt):
        from cores import reference_ready
        reference_ready.assert_references(sid, [g])
    # 严重问题不能只标红后继续烧视频额度。新提示词会把确定性问题写进
    # critical_problems；旧时间轴也在这里按现有字段补算一次。
    critical = _render_critical_problems(st, tl, g, prompt, ep, seg_no)
    if critical:
        # P365：门只记录不拦（用户 9-14 定）——记进段的 problems，照常出片，分镜页能看到
        if log_fn:
            log_fn("第 %s 段提示词有严重问题（照常出片）：%s" % (seg_no, "；".join(str(x) for x in critical[:3])))
        try:
            _apply(sid, int(scene_no or 1), int(seg_no), {"critical_problems": list(critical)}, ep=ep)
        except Exception:
            pass
    if is_new_prompt(prompt):
        # 【用户挑过就听用户的】分镜页每段能单独挑/换参考图，存在 g["refs"]。
        # 原来这里只走 new_pack，把用户挑的整个丢掉——单段重渲认、批量出片不认，
        # 「自定义作品」传了自己的图也白传（2026-09-05 查出）。和 gen_segment 同一套规则。
        _picked = [r for r in (g.get("refs") or []) if r.get("n")]
        if _picked:
            _byname = _adopted_paths(sid)
            imgs = [_byname[r["n"]] for r in _picked if _byname.get(r["n"])]
            if log_fn and len(imgs) < len(_picked):
                log_fn("第 %s 段：挑了 %d 张参考图，有 %d 张还没采用，跳过"
                       % (seg_no, len(_picked), len(_picked) - len(imgs)))
        else:
            imgs = new_pack(sid, g)
        if not imgs:                                   # 挑的全没采用 → 退回自动配包，别让这段出不了片
            imgs = new_pack(sid, g)
        h3p = prompt
        # 【姿势参考】上一段的最后一帧当第 N 张参考图，只参考姿势/位置/构图
        # （用户 2026-09-02 提法，实验 V13a：接口几乎无缝，脸没换）。
        prev = next((x for x in (sc.get("segments") or [])
                     if int(x.get("no") or 0) == int(seg_no) - 1), None)
        pef = str((prev or {}).get("end_frame") or "")
        _sc_now = str(g.get("scene") or "")
        _sc_prev = str((prev or {}).get("scene") or "")
        _changed = bool(_sc_now and _sc_prev and _sc_now != _sc_prev) or bool((g.get("seam") or {}).get("scene_changed"))
        _soft = str(os.environ.get("V41_RELAY") or "soft").strip().lower() == "soft"
        _ssc = bool((g.get("seam") or {}).get("same_scene_cut"))
        if pef and os.path.exists(pef) and len(imgs) < 9 and not (_soft and _changed):
            # P332：同场景才带上一段末帧（软句：只参考姿势位置构图）；换场景不带——出场拍/入场拍在提示词里
            # P392②：同场景的硬切（换机位/换人）也带，但只对齐空间、家具位置和光（space 口径），机位按本段【机位】
            imgs = imgs + [pef]
            h3p = _with_pose_ref(h3p, len(imgs), scene_changed=_changed,
                                 prev_tail=str((prev or {}).get("tail") or ""), mode=("space" if _ssc else None))
    else:
        _ok, _names_, imgs, h3p = director_pack(sid, g)
    imgs = [x for x in imgs if x and os.path.exists(x)][:9]
    if not imgs:
        raise RuntimeError("第 %s 段没有参考图——先去 🗂 资源 生成并采用人物和场景的设定图" % seg_no)
    secs = float(g.get("seconds") or 12)
    if log_fn:
        log_fn("第 %s 段：%d 张参考图，%.0f 秒" % (seg_no, len(imgs), secs))
    # 分辨率档 / 画幅比 / 步数：设定页可改，见 _render_params（P242）。
    # 【原注释是错的】不设步数时用的是工作流模板里的 **25**，不是 10——
    # 三份模板的 BasicScheduler 实测都是 25，数值唯一出处是 models/video_workflows/*.json。
    _tier, _ratio, _steps = _render_params(st, size_tier)
    r = h3.generate("ref2va", h3p, images=imgs,
                    seconds=min(15, max(4, int(round(secs)))),
                    ratio=_ratio, size_tier=("1.0" if hd else _tier), steps=_steps)
    src = r.get("output_path") or r.get("path")
    from cores import saga_core as _sgc
    out = _sgc.seg_video_path(sid, ep, scene_no, seg_no, hd=hd)   # P237 文件名带话号
    os.makedirs(os.path.dirname(out), exist_ok=True)
    shutil.copy2(src, out)
    patch = {"mode": "ref2va", "seed": r.get("seed"),
             "used_refs": [os.path.basename(x) for x in imgs],
             "h3_prompt_sent": h3p[:12000]}                                # P322⑤：实际发给 H3 的那份（含末帧接力句），验收查这个
    # 这一段是用什么参数出的，记下来——不记就没法判断一话里混没混档（P242）
    for _k, _v in (("size_tier_hd" if hd else "size_tier", r.get("size_tier")),
                   ("ratio", r.get("ratio")), ("steps", r.get("steps"))):
        if _v not in (None, ""):
            patch[_k] = _v
    if hd:
        patch["video_hd"] = out
        patch["size_hd"] = "%sx%s" % (r.get("width"), r.get("height"))
    else:
        patch["video"] = out
        patch["size"] = "%sx%s" % (r.get("width"), r.get("height"))
        patch["video_hd"] = ""
        ef = out[:-4] + "_end.png"
        try:
            subprocess.run(["ffmpeg", "-y", "-sseof", "-0.1", "-i", out,
                            "-frames:v", "1", ef], capture_output=True, timeout=120)
            if os.path.exists(ef):
                patch["end_frame"] = ef
        except Exception:
            pass
    _apply(sid, int(scene_no or 1), int(seg_no), patch, ep=ep)
    return patch


def _match_scene(location, scene_names):
    """地点 → 场景资产名。精确优先，其次互为包含（「公寓」↔「公寓卧室」）。

    导演层写的地点和资产名经常差一两个字，精确匹配一失手，
    场景定义和场景参考图就全丢了——视频只能靠镜头文字硬扛空间。"""
    loc = str(location or "").strip()
    if not loc:
        return None
    for n in scene_names:
        if n == loc:
            return n
    for n in scene_names:
        if loc in n or n in loc:
            return n
    # 【差异在中间时互相包含会失手】"云岚宗·订婚宴大殿" 和 "云岚宗大殿" 谁也不包含谁，
    # 于是场景参考图整个丢掉，H3 没有大殿可参考就自己编了个山景——
    # 人物一致但背景完全跑掉。改成先剥掉标点分隔符再比，还不行就按字重合率挑最像的。
    import re as _re
    def _norm(x):
        return _re.sub(r"[·・\-—－_、，,。.\s（）()【】\[\]]+", "", str(x or ""))
    ln = _norm(loc)
    for n in scene_names:
        nn = _norm(n)
        if nn and (ln in nn or nn in ln):
            return n
    best, best_r = None, 0.0
    for n in scene_names:
        nn = _norm(n)
        if not nn:
            continue
        share = len(set(ln) & set(nn))
        r = share / max(1, min(len(set(ln)), len(set(nn))))
        if r > best_r:
            best, best_r = n, r
    return best if best_r >= 0.6 else None


# 按话存：和 timeline.py 同一套。ep 由请求带过来，不猜。
_CUR_EP = {}


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


def _load(sid, ep=None):
    from cores import saga_core as _sc
    st = story_core.get_story(sid) or {}
    return st, _sc.ep_timeline(sid, ep or _CUR_EP.get(sid, 1))


def _save(sid, tl, ep=None):
    from cores import saga_core as _sc
    return _sc.save_ep_timeline(sid, ep or _CUR_EP.get(sid, 1), tl)


def _names(sid):
    return [c.get("name") for c in asset_core.list_assets(sid, "characters") if c.get("name")]


_NAME_SEP = re.compile(r"[·•‧・・\s\-—_/、]+")


def _name_keys(name):
    """一个人物在正文里可能被写成的所有写法：全名 + 去掉间隔号后的每一节。
    「埃里克·瓦伦丁」→ ["埃里克·瓦伦丁", "埃里克", "瓦伦丁"]。"""
    n = str(name or "").strip()
    if not n:
        return []
    keys = [n]
    for part in _NAME_SEP.split(n):
        part = part.strip()
        if len(part) >= 2 and part != n and part not in keys:
            keys.append(part)
    return keys


def _names_in(sid, text):
    """从分镜正文里认出这一段有谁出场——**姓名分段匹配**。

    以前是全名精确匹配：卡上是「埃里克·瓦伦丁」，导演写「埃里克」就匹配不上，
    这个人的人设图进不了参考包，H3 只能自己编他的脸（2026-08-27 用户实测：
    视频里男主的侧脸和人设图对不上）。当时的补法是逼导演一律写全名，
    现在改成这里认分段，导演写全名还是写简称都行。

    两条防错：
      · **多人共用的一节不算数**：两个角色都姓「瓦伦丁」时，光凭姓分不出是谁，
        这一节就作废（写名字仍然认得出）。宁可少给一张参考图，不能给错人。
      · **长的写法先吃，吃掉就从文本里划掉**：「咪娅·罗兰」和「咪娅」并存时，
        正文写了罗兰，不能把「咪娅」也蹭上——先匹配长的，再拿剩下的文本匹配短的。
    """
    t = str(text or "")
    names = [n for n in _names(sid) if n]
    used = {}
    for n in names:
        for k in _name_keys(n):
            used[k] = used.get(k, 0) + 1
    pairs = []
    for n in names:
        for k in _name_keys(n):
            if k != n and used.get(k, 0) > 1:
                continue
            pairs.append((k, n))
    pairs.sort(key=lambda kv: -len(kv[0]))
    hit = set()
    for k, n in pairs:
        if k and k in t:
            hit.add(n)
            t = t.replace(k, "￿")
    return [n for n in names if n in hit]


_WIDE_FRAMING = ("大全景", "全景", "远景", "中远景", "中景")
_TIGHT_FRAMING = ("特写", "大特写", "近景", "中近景")


def _wants_scene_ref(text):
    """这一段要不要挂场景参考板：看它有没有中景以上的镜头。

    2026-08-26 实测：给每一段都挂场景图，四段质量全下降（用户原话"都不如原来的"）。
    根因是场景板是远景静态图，当强参考会把导演写的贴脸特写构图冲回全景。
    所以只在**画面里真的看得见环境**的段落挂——有中景/全景/远景的挂，
    整段都是特写/近景的不挂（那种段落背景本来就虚掉了，挂了只有害处）。
    """
    t = str(text or "")
    # 复合镜头头「特写→中景，拉远」要按**全部**景别词判——懒惰匹配只取第一个词，
    # 拉远到中景的段落被误判成纯特写段、场景板漏挂（2026-08-28 实测 060 seg1）
    inner = " ".join(re.findall(r"[（(]([^）)]*)[）)]", t))
    return any(w in inner for w in _WIDE_FRAMING)


def _seg_of(tl, no, seg_no):
    sc = next((x for x in tl.get("scenes") or [] if int(x.get("no")) == int(no)), None)
    if not sc:
        return None, None
    g = next((x for x in sc.get("segments") or [] if int(x.get("no")) == int(seg_no)), None)
    return sc, g


def _on_screen_chars(shots, names):
    """这一段画面里到底有谁。

    导演内核 v2 的每个镜头有 chars 字段（明确写的"画面里的人"）——它是权威。
    靠"名字出现在文字里"判断会把画外人拉进来：「望向艾莉丝离去的方向」
    她人已经走了，名字还在动作里，按文字匹配她就又被定义、又被排站位——
    第 4 段（只拍裙摆）就这样多定义了一个李哲，summary 直接把裙子写到他身上。"""
    listed = [y.get("chars") for y in shots if isinstance(y.get("chars"), list)]
    if listed:
        seen = []
        for lst in listed:
            for n in lst:
                if n in names and n not in seen:
                    seen.append(n)
        return seen
    blob = " ".join(" ".join(str(y.get(k) or "") for k in
                             ("subject", "action", "A_action", "B_action",
                              "foreground", "midground", "background", "blocking"))
                    for y in shots)
    return [n for n in names if n in blob]


# 逐字固定的尾部（来自用户验证过的 MiniMax 文戏 skill，一字不许改）
_FIXED_TAIL = """【禁止项】

文字/UI/水印/Logo/角标/可读文字/真实UI

【强制声明】

音轨内容:人物按台词开口说话的说话声,以及这个场景本身的环境音和动作音效;
整条音轨从头到尾只有这三种声音,没有任何配乐、器乐、旋律、鼓点或歌声;
画面禁字幕/文字/水印/Logo;禁止可读文字(指画面字幕文字,不含人声台词)"""


def _char_brief(card):
    """站位行用的人物短描述：发型 + 服装 + 身份小物。外观细节交给参考图，
    这里只写服装道具——旧格式那个三百字解剖块就是提示词失败的一部分。"""
    look = str(card.get("look") or "")
    hair = ""
    for kw in ("短发", "长发", "卷发", "微卷"):
        i = look.find(kw)
        if i >= 0:
            j = max(look.rfind("，", 0, i), look.rfind("、", 0, i)) + 1
            hair = look[j:i + len(kw)]
            break
    small = "".join(w for w in ("耳钉", "手表", "项链") if w in look)
    items = [x for x in (hair, str(card.get("clothing") or "")) if x]
    if "耳钉" in look:
        items.append("右耳银色小耳钉" if "银色" in look else "耳钉")
    if "手表" in look:
        items.append("左腕旧皮质表带手表" if "皮质" in look else "手表")
    return "，".join(items)



# ==================== 参考图绑定语 ====================
# H3 收到的参考图是一串图，提示词里如果一个字都不提它们，模型只能自己猜
# 哪张是谁。博主实测有效的写法是在提示词开头点名：
#   「@图片1是女孩A —— 目标视频中女孩A的形象、服装与姿态基准完全参照@图片1，
#     并在全部镜头中保持一致。」
#
# 两种绑定强度，用户要 A/B 对比：
#   strict（现在的做法）：参考图是身份基准，人要跟图上长得一样
#   loose （用户提的备选）：基准图只提供空间、站位和光线，人物按剧本演，
#          不要求跟参考图一致——用户的原话是"大致参考空间和站位还有光线用的"
#
# 参考图的顺序由 video_layer.reference_pack 定死：人物（按出场顺序）→ 场景 →
# 上一段结束帧。这里按同一个顺序编号，两边不会错位。


def _join_prompt(*parts):
    """把提示词各块拼起来。参考图绑定语放在项目视觉块之后、镜头描述之前——
    它是"这几张图分别是谁"，属于事实层，必须在模型读到镜头动作之前先看到。"""
    return (chr(10) * 2).join(str(x).strip() for x in parts if str(x or "").strip())

def _ref_manifest(sid, on_screen, scene_name, mode="strict", has_prev_frame=False):
    """生成「@图片N 是谁」的绑定语。返回一段文字，放在提示词最前面。"""
    from cores import video_layer
    pics = video_layer.reference_pack(
        1 if has_prev_frame else 0, list(on_screen), scene_name,
        prev_end_frame="x" if has_prev_frame else None,
        same_location_as_prev=has_prev_frame)
    lines, n = [], 0
    for it in pics:
        n += 1
        tag = "@图片%d" % n
        if it.get("kind") == "character":
            nm = it.get("name")
            if mode == "loose":
                lines.append("%s是%s的人设图，用来大致对上他的身份和服装配色；"
                             "具体的表情、姿态和站位按剧本演，不必和图上一致。" % (tag, nm))
            else:
                # 【只说参照哪几项】人设图是白底四视图的正面站姿——那是档案照。
                # 笼统写"完全参照"，模型会连姿势和机位一起照搬，
                # 实测出来四个人正面朝镜头排成一排，像合影。
                lines.append("%s是%s的身份基准图——%s的脸型、五官、发型发色、"
                             "服装款式与配色以%s为准，全部镜头保持一致；"
                             "%s在这一段里的姿势、朝向、视线和站位，"
                             "以下面每一镜的描述为准。"
                             % (tag, nm, nm, tag, nm))
        elif it.get("kind") == "scene":
            if mode == "loose":
                lines.append("%s是「%s」的空间基准图——参照它的空间结构、纵深关系、"
                             "主光方向和色温；人物在这个空间里怎么站、怎么走，"
                             "按剧本演，不必和图上一致。" % (tag, it.get("name")))
            else:
                lines.append("%s是「%s」的空间基准图——本段的空间结构、材质、"
                             "光线方向与色温以%s为准，就是这个地方；"
                             "机位、景别和人物在空间里的位置，以每一镜的描述为准。"
                             % (tag, it.get("name"), tag))
        else:
            lines.append("%s是上一段的最后一帧——本段从这一帧的构图和光线接着往下拍，"
                         "保证接缝处没有跳变。" % tag)
    if not lines:
        return ""
    head = ("参考图（宽松参考：只对空间、光线和身份大方向，表演按剧本走）："
            if mode == "loose" else
            "参考图（身份基准：长相服装和空间以图为准，姿势机位以镜头描述为准）：")
    return head + chr(10) + chr(10).join(lines)

def _compile_prompt(sid, sc, g, base=None):
    """把这一段编成 MiniMax 10–15 秒单元提示词（中文，时间片镜头）。

    旧的六段式（英文 summary + [Shot N] + Static Shot 术语）实测完全失败：
    一段 12 秒一镜到底，成片是会动的照片。MiniMax 的正确用法是
    单元内部用「镜头N（a–b秒，景别）」时间片切 2~4 个机位，它原生支持。

    事实层（代码定死）：场景状态、人物服装道具、秒数、逐字固定尾部。
    指令层（Qwen）：人物站位行 + 镜头时间片。
    """
    import re as _re
    from models import qwen_client as qc
    from cores import project_settings, project_prompt

    _story = story_core.get_story(sid) or {}
    _ps = project_settings.get(_story)
    _project_block = project_prompt.video_visual_block(_ps)

    names = _names(sid)
    chars = {c["name"]: c for c in asset_core.list_assets(sid, "characters")}
    _ids = g.get("shot_ids") or []
    _segshots = [y for y in (sc.get("shots") or []) if y.get("shot_id") in _ids]
    on_screen = _on_screen_chars(_segshots, names)
    sec = int(round(float(g.get("seconds") or 12)))

    scenes_all = asset_core.list_assets(sid, "scenes")
    hit = _match_scene(g.get("location"), [x.get("name") for x in scenes_all])
    sc_card = next((x for x in scenes_all if x.get("name") == hit), {}) or {}
    scene_line = "%s：%s；标志物：%s" % (
        sc_card.get("name") or g.get("location") or "",
        (sc_card.get("contract_text") or sc_card.get("space") or "")[:80],
        sc_card.get("landmarks") or sc_card.get("fixed_landmarks") or "")

    who = "\n".join("%s：%s" % (n, _char_brief(chars.get(n) or {}))
                    for n in on_screen) or "（本段没有人物，只有环境）"
    # 逐条编号 + 带景别和运镜：翻译员按号一一对应，不许增删合并
    acts = "\n".join(
        "镜头%d｜%s｜%.1f秒｜%s" % (_i, (y.get("framing") or "中景"),
                                float(y.get("duration") or y.get("seconds") or 4),
                                (y.get("action") or "").strip() or "（承接上一镜）")
        for _i, y in enumerate(_segshots, 1))
    # 【对白逐字从剧本取】分镜里的 dialogue 是导演层自己编的复述版，跟剧本对不上，
    # 而且经常是空的——视频里就一句话都没有。cores/video_layer.dialogue_for 早就
    # 写好了"拿分镜的说话人去剧本里核对，对不上以剧本为准"，但从来没被调用过。
    # 【台词绑镜头，不按段平均分】原来把整场台词按段序均分给各段——
    # 结果剧本前 8 个纯建置镜头一句台词都没有，却被硬塞了第一句；
    # 真正该说话的镜头反倒没有。台词本来就写在剧本的具体某一行下面，
    # 解析剧本时已经挂在对应镜头上了，直接用。
    _dlg_lines = []
    for _i, _y in enumerate(_segshots, 1):
        _d = str(_y.get("dialogue") or "").strip()
        if _d:
            for _ln in _d.split("\n"):
                if _ln.strip():
                    _dlg_lines.append("镜头%d：%s" % (_i, _ln.strip()))
    dlg = "\n".join(_dlg_lines) if _dlg_lines else "无"

    # 参考图绑定语：告诉 H3 哪张图是谁。模式由项目设定 ref_mode 决定，
    # 默认 strict；用户要对比的宽松模式是 loose。
    _ref_mode = str(_ps.get("ref_mode") or "loose").strip() or "loose"   # 缺省与前端/project_settings 一致（2026-09-04）
    _ref_text = _ref_manifest(sid, on_screen, sc_card.get("name") or hit or "",
                              mode=_ref_mode)

    ins = (Path(__file__).resolve().parent.parent / "presets" / "instructions"
           / "视频_MiniMax单元_指令词.txt").read_text(encoding="utf-8")
    user = ("%s\n\n目标秒数：%d｜镜头数：%d（必须输出同样数量的镜头，一一对应）\n"
            "场景：%s\n出场人物（服装道具，外观交给参考图）：\n%s\n"
            "镜头表（定稿，逐条翻译，不许增删改）：\n%s\n台词：%s"
            % (_project_block, sec, len(_segshots), scene_line, who, acts, dlg))
    _shooting_notes = str(g.get("shooting_notes") or "").strip()
    if _shooting_notes:
        user += ("\n\n用户对这一话的拍摄想法（只决定机位、节奏和表演感觉，"
                 "不得改变镜头表的事件、人物和台词）：\n" + _shooting_notes[:800])
    if base:
        user += "\n\n用户改写稿（画面内容以此为准，在它基础上补全，不许推翻）：\n" + str(base)[:3000]

    last_err = ""
    for _ in range(3):
        u = user + (("\n\n上一稿的问题（必须改掉）：" + last_err) if last_err else "")
        raw = (qc.chat(ins, u, temperature=0.6, max_tokens=1200, reasoning=False) or "").strip()
        # Qwen 偶尔把字面 \n 当文本写进行尾，清掉；顺手去掉它自己复述的固定尾部
        body = raw.replace("\\n", "").split("【禁止项】")[0].strip()
        spans = [(float(a), float(b)) for a, b in
                 _re.findall(r"镜头\d+（(\d+(?:\.\d+)?)[–-](\d+(?:\.\d+)?)秒", body)]
        why = []
        if "人物站位：" not in body:
            why.append("缺「人物站位：」行")
        if not spans:
            why.append("没有「镜头N（a–b秒，景别）」时间片")
        else:
            if abs(spans[-1][1] - sec) > 2:
                why.append("时间片只到 %s 秒，目标 %d 秒" % (spans[-1][1], sec))
            for i in range(1, len(spans)):
                if abs(spans[i][0] - spans[i - 1][1]) > 0.51:
                    why.append("镜头时间不连续（%s 到 %s）" % (spans[i - 1][1], spans[i][0]))
            # 【镜头数必须一一对应】剧本已经把镜头定死了，翻译员不许增删合并。
            # 原来只查"2~5镜"这个范围，于是 2 个镜头被扩写成 4 个也照样放行——
            # 我写的"云海翻涌"变成了"流苏宫灯残片飘落"就是这么来的。
            if len(spans) != len(_segshots):
                why.append("镜头数不对：我给了 %d 个镜头，你输出了 %d 个。"
                           "必须一一对应，不许增加、合并或拆分"
                           % (len(_segshots), len(spans)))
        # 【台词不许自己编】只能出现我给的那几句
        _tail = body.split("台词")[-1] if "台词" in body else ""
        _said = [x.strip() for x in _re.split(r"[\n；;]+", _tail) if x.strip()]
        # 【台词必须内联进镜头行】实测：台词只写在末尾的「台词：」块里时，
        # H3 关联不上时间点，把整段台词攒到最后几秒一起说完——
        # 段3 前 7 秒近乎数字静音(-58dB)，三句全挤在最后 6 秒。
        if _dlg_lines:
            _shot_lines = _re.findall(r"^镜头\d+（[^）]*）：(.*)$", body, _re.M)
            _inlined = sum(1 for _sd in _dlg_lines
                           for _ln in _shot_lines
                           if _sd.split("：", 2)[-1].strip()[:8] and
                           _sd.split("：", 2)[-1].strip()[:8] in _ln)
            if _inlined < len(_dlg_lines):
                why.append("台词没内联到镜头行里（%d/%d）。有台词的那一镜，"
                           "台词原文要直接写在「镜头N（…）：」这一行的描述里，"
                           "不能只列在末尾的台词表里——那样模型不知道这句话"
                           "该在第几秒说，会把整段台词攒到最后一起说完"
                           % (_inlined, len(_dlg_lines)))
        if _dlg_lines:
            _allowed = "".join(_dlg_lines)
            for _sd in _said:
                _t = _sd.split("：")[-1].strip().strip("。，、 " + chr(8220) + chr(8221))
                if len(_t) >= 4 and _t not in _allowed and "无" not in _t:
                    why.append("台词「%s」不在我给的台词表里。只能逐字用我给的，没给台词的镜头写「台词：无」" % _t[:20])
                    break
        else:
            for _sd in _said:
                _t = _sd.split("：")[-1].strip()
                if len(_t) >= 4 and "无" not in _t:
                    why.append("这一段我没给任何台词，你却写了台词。应该写「台词：无」")
                    break
        if not why:
            return _join_prompt(_project_block, _ref_text, body, _FIXED_TAIL)
        last_err = "；".join(why)
    return _join_prompt(_project_block, _ref_text, body, _FIXED_TAIL)   # 三次仍不完美：拿最后一稿，不拦


@post(("/api/timeline/", "/segment-prompt"))
def seg_prompt(h, path, d):
    sid = path.split("/")[3]
    _ep_of(sid, d)
    st, tl = _load(sid)
    sc, g = _seg_of(tl, d.get("scene_no") or 1, d.get("seg_no") or 1)
    if not g:
        return _RESP({"ok": False, "error": "找不到这一段"}, 400)
    if d.get("direction") is not None or g.get("shot") or g.get("cut") is not None:
        # P397：节拍段——按这一拍（或用户口述的拍法）只重写这一段；写手照口述写，程序钉景别/角度/运动
        from cores import authoring as _au
        from api.saga_api import _prev_tail_of as _ptail
        ep = int(d.get("ep") or 1)
        no = int(g.get("no") or 0)
        _direction = str(d.get("direction") or "").strip() or None
        try:
            ps = _au.make_h3_prompts(sid, ep, start=no - 1, count=1, prev_tail=_ptail(sid, ep, no), direction=_direction) or []
            if not ps:
                return _RESP({"ok": False, "error": "这一段没写出提示词"}, 500)
            _au.build_timeline(sid, ep, ps)
        except Exception as e:
            return _RESP({"ok": False, "error": str(e)[:300]}, 500)
        st, tl = _load(sid)
        sc, g = _seg_of(tl, d.get("scene_no") or 1, d.get("seg_no") or 1)
        return _RESP({"ok": True, "data": {"segment": g}})
    try:
        newp = _compile_prompt(sid, sc, g, base=str(d.get("base") or "").strip() or None)
    except Exception as e:
        return _RESP({"ok": False, "error": str(e)[:300]}, 500)
    # P312：用户主动「重编提示词」→ 程序版本覆盖，手改标记清掉（下次剧本重写照常）
    g = _apply(sid, d.get("scene_no") or 1, d.get("seg_no") or 1, {"prompt": newp, "prompt_edited": False, "prompt_auto": ""})
    return _RESP({"ok": True, "data": {"segment": g}})


def _adopted_paths(sid):
    """已采用的设定图：名字 → 文件路径。没采用的不参与，避免拿草稿当参考。"""
    adopted = {}
    for v in asset_core.list_assets(sid, "visuals"):
        # 注释一直写"已采用的"，代码却什么状态都收——候选图和废稿也会被当参考图。
        if v.get("status") == "adopted" and v.get("owner_id") and v.get("path"):
            adopted.setdefault(v["owner_id"], v["path"])
    out = {}
    for kind, key in (("characters", "character_id"), ("scenes", "scene_id")):
        for a in asset_core.list_assets(sid, kind):
            p = adopted.get(a.get(key))
            if p and os.path.exists(p):
                out[a.get("name")] = p
    return out


# —— 分镜正文的字段头（解析用） ——
_H3_FIELD = re.compile(r"^(本段时长|本段场景|本段背景锚|本段布局|开场状态|人物站位|人物衣着"
                       r"|台词|环境音|配乐|收尾状态|站位表)[：:]")
_H3_SHOT = re.compile(r"^镜头(\d+)（([\d.]+)\s*[–\-—~]\s*([\d.]+)秒[，,]?([^）]*)）[：:]?\s*(.*)$")
# 台词两种写法：`X（语气）说，「…」…` 和 `X的心声（语气）响起，「…」…`
# 编排形：`名（语气）说，「X」`／`名（语气）：「X」`（冒号直引）／
# 心声的两种写法 `名的心声（语气）` 和 `名（心声）（语气）`。
# 名字字符类必须把右括号也排掉——实测 `凯尔（心声）（低沉）说` 被切出个
# 说话人叫「心声）」，还按张嘴模板渲染了心声（2026-08-28）。
_H3_CUE = re.compile(r"([^\s（(）)「」。，；\[\]【】]{1,12}?)(的心声|（心声）)?（([^）]*)）"
                     r"(?:(?:说道|说|响起)[，,：:]?|[：:])\s*"
                     r"「([^」]+)」[^\n]*")
_H3_META = re.compile(
    # 「用的是 A 位置接／无接法／开场」——变体层出不穷，认关键词不认全拼。
    # 「使」要一起吃掉：导演写「**使**用的是建立镜头（无过渡拍）」时，
    # 从"用的是"起匹配会把前面那个「使」留成孤字，成片提示词里出现「…阴影里。使。」
    # （2026-08-30 逐字读 STORY_065 发现）。"建立镜头"也补进认得的词里。
    r"(使?用的是[^\n。]{0,16}(?:接法?|开场|镜头)[^\n。]*。?"
    r"|[承延顺紧]?[接续]上一段[^，。\n]*[，。]?"      # 半句吃干净，不留"承""使"孤字
    r"|下一段要从这里接上[^\n]*"
    r"|切必换档[，,]?\s*|景别跳至[^，。）\n]*[，,]?\s*"
    r"|[（(]接法[AB][：:][^）)]*[）)]\s*|接法[AB][：:]?[^，。\n]{0,8}[，。]?"
    r"|180度轴线（[^）]*）[；;，,。]?\s*|180度轴线[；;，,。]?\s*)")
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"


def _h3_fields(body):
    """把分镜正文拆成 {字段名: 文本} + [(起,止,景别运镜,镜头文本), …]。"""
    fields, shots, cur = {}, [], None
    for raw in str(body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _H3_SHOT.match(line)
        if m:
            cur = ("shot", len(shots))
            shots.append([float(m.group(2)), float(m.group(3)),
                          m.group(4).strip(), m.group(5).strip()])
            continue
        f = _H3_FIELD.match(line)
        if f:
            cur = ("field", f.group(1))
            fields[f.group(1)] = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            continue
        if cur and cur[0] == "shot":
            shots[cur[1]][3] = (shots[cur[1]][3] + "\n" + line).strip()
        elif cur and cur[0] == "field":
            fields[cur[1]] = (fields[cur[1]] + "\n" + line).strip()
    return fields, shots


_ANIME_STYLES = ("日漫", "赛璐璐", "插画", "水墨", "手绘", "像素", "动画", "漫画")
_CG_STYLES = ("3D", "游戏模型")

# 共通的表演/运镜要求——三种画风都要，只有"画面是什么材质"不同
_COMMON_TEXTURE = ("人物表演克制，用微表情；所有动作带惯性：衣摆有重量，头发随动作轻摆，"
                   "人物自然呼吸、眨眼、眼神移动。{lip}镜头运动缓慢、稳定、有重量。")
# 口型同步只对**有对白的段**说。无对白段前面已经写了"所有人物保持闭口"，
# 后面再要求"说话人口型与语音精确同步"就是同一份提示词里自己打自己
# （2026-08-30 逐字读 STORY_065 第 1 段发现，自检器的对撞表里没有这一对）。
_LIP_SYNC = "说话人口型与语音精确同步，说完嘴唇自然合拢。"


# ── 送 H3 之前的自检（2026-08-29）──────────────────────────
# 起因：厚涂插画的提示词里同时出现「没有线稿」和「干净利落的线条／赛璐璐平涂」，
# 是我按名字猜画风族、写死三段质感块造成的，用户一眼看出来我却没查。
# 以后不靠人读，靠这几条对撞检查。
_CONTRA_PAIRS = (
    (("没有线稿", "不画轮廓线"), ("干净利落的线条", "轮廓线闭合", "手绘线稿"), "线稿"),
    (("赛璐璐平涂", "平涂上色"), ("厚涂", "笔触堆", "绘画笔触"), "上色方式"),
    (("真人实拍", "真实皮肤纹理", "保留毛孔"), ("二维动画", "赛璐璐", "动画上色",
                                                "绘制出来的"), "媒介"),
    (("三维渲染", "次表面散射"), ("二维动画", "赛璐璐平涂"), "媒介"),
    (("阳光被遮蔽", "阳光几乎被完全遮蔽", "不见天日"), ("阳光下", "阳光洒"), "光照"),
)
_TRAD_CHARS = "臉們個來這對時實現學經萬與說話東車馬鳥魚點無"


def _positively_says(t, word):
    """这个词是**正面出现**的吗。「不是真人实拍」「绝不是三维渲染」属于否定式，
    不算矛盾——不排掉的话每一段都会误报（2026-08-29 自检器首测就报了假警）。"""
    for m in re.finditer(re.escape(word), t):
        pre = t[max(0, m.start() - 6):m.start()]
        if not re.search(r"(不是|绝不|不许|不要|禁止|没有|避免|而非|不能)\s*$", pre):
            return True
    return False


def prompt_selfcheck(p, style=""):
    """返回问题清单（空 = 干净）。只报能对撞验证的，不做主观判断。"""
    t = str(p or "")
    out = []
    for a_words, b_words, name in _CONTRA_PAIRS:
        a = [w for w in a_words if _positively_says(t, w)]
        b = [w for w in b_words if _positively_says(t, w)]
        if a and b:
            out.append("%s自相矛盾：同时出现「%s」和「%s」" % (name, a[0], b[0]))
    if "（）" in t or "()" in t:
        out.append("有空括号——罗盘词转译留下的空壳没清干净")
    if re.search(r"[。\n]\s*[：:]", t):
        out.append("有孤立冒号——机位标签被剥掉后留下的残渣")
    tr = sorted({c for c in t if c in _TRAD_CHARS})
    if tr:
        out.append("混入繁体字：%s" % "、".join(tr))
    # 以下三条来自 2026-08-30 逐字读 STORY_065 第 1 段——自检器当时一条都没报，
    # 而它们都会实打实影响成片。补进来，以后不靠人读。
    # 只在**整段无对白**时才算矛盾。有对白的段落里「其他人保持闭口并自然反应」
    # 和口型同步并存是对的——我最初把判据写成只要出现"保持闭口"就报，
    # 结果 5 段里误报 3 段（2026-08-30 用户实测 STORY_066）。
    if "本段没有对白，所有人物保持闭口" in t and "口型与语音精确同步" in t:
        out.append("口型自相矛盾：本段没有对白、要求所有人闭口，却又要求口型与语音同步")
    corpus = _instruction_corpus()
    if corpus:
        _n = lambda x: re.sub(r"[^\w一-龥]", "", str(x or ""))   # 标点不参与比对
        # **只扫导演写的部分**：影像质感／表演要求／音频契约／一致性这几段是
        # 我们自己固定写进去的，它们本来就和指令词有重合（「衣摆有重量，头发随
        # 动作轻摆」两边都有），扫进去就是误报（2026-08-30 自检器首次报假警）。
        head = t.split("影像质感：")[0]
        core, nc = _n(head), _n(corpus)
        leak = next((core[i:i + 14] for i in range(0, max(0, len(core) - 13), 4)
                     if core[i:i + 14] in nc), "")
        if leak:
            out.append("混进了指令词原文（写给导演看的说明漏给了 H3）：「%s…」" % leak)
    _meta_verb = re.search(r"(一律写|绝不写|必须写|不许写|照抄|下游会)", t)
    if _meta_verb:
        out.append("混进了写给导演的指令语气（「%s」）——这是对写的人说的话，"
                   "对视频模型没有意义" % _meta_verb.group(0))
    # 判据按**真实产物**的写法来：图片行是「图片1：雷恩的人设参考图。…」
    if "与其参考图一致" in t and not re.search(r"^图片\d+：[^\n]*人设参考图", t, re.M):
        out.append("提示词要求「与其参考图一致」，但没有附任何人物参考图——"
                   "人物长相会由模型现编，段与段之间会换脸")
    return out


def _style_kind(style):
    """这个画风是 photo / 2d / 3d——**以 render_notes.json 的 kind 字段为准**，
    不按名字猜（按名字猜会把「厚涂插画」归成动画，措辞就错了）。"""
    try:
        import json as _j
        _rn = _j.loads((Path(__file__).resolve().parent.parent / "presets"
                        / "render_notes.json").read_text(encoding="utf-8"))
        return str((_rn.get(str(style or "")) or {}).get("kind") or "photo").strip()
    except Exception:
        return "photo"


_INS_TEXT = None


def _instruction_corpus():
    """指令词里**只该由导演读、不该出现在产物里**的说明文字。

    模板里有两类内容，必须分开：
    ① 带 `<占位符>` 的**范例行**——导演照着范例写出来的画面描述是对的
       （「他的嘴唇自始至终紧紧闭合，声音从画面之外传来」正是心声镜头该有的写法）；
    ② ★／·／- 开头的**说明行**——写给导演看的规则和理由，出现在产物里就是泄漏。
    原来拿整份模板去比对，把①也算成泄漏，段3 被误报（2026-08-30 用户实测）。
    """
    global _INS_TEXT
    if _INS_TEXT is None:
        try:
            raw = (Path(__file__).resolve().parent.parent / "presets"
                   / "instructions" / "导演_分镜_指令词.txt"
                   ).read_text(encoding="utf-8")
            keep = []
            for ln in raw.split("\n"):
                c = ln.strip()
                if not c or len(c) < 10:
                    continue
                if "<" in c or "＜" in c or "`" in c:
                    continue                       # 范例行：照着写是对的
                keep.append(c)
            _INS_TEXT = "\n".join(keep)
        except Exception:
            _INS_TEXT = ""
    return _INS_TEXT


def _strip_instruction_text(s, ngram=12):
    """把**指令词原文**从产物字段里剥掉。

    导演会把模板里写给它自己看的说明当成内容抄进来，一路进到发给 H3 的提示词：
    「…左侧是便利店遮雨棚的边缘结构。★门窗一律写"紧闭，不透光"，绝不写"门缝透光"
    这类漏光描述——下游会把它放大成门大开…这是防止每段自己换拍摄方向、背景乱跳的锚，
    比"机位在哪"对下游有效得多>」——只有第一句是画面，后面全是我写的注释，
    连模板残留的 > 都抄进去了（2026-08-30 逐字读发现）。
    规则定成"提示词里不许出现指令词原文"：按句切，任何一句和指令词有 12 字以上
    连续重合就整句丢掉。这样以后模板里加什么注释都不会漏下去。
    """
    t = str(s or "")
    corpus = _instruction_corpus()
    if not t.strip() or not corpus:
        return t

    def _norm(x):
        # **标点一律去掉再比**：指令词模板里是英文直角引号，导演写出来是中文弯引号，
        # 12 字窗口就对不上，「门窗一律写"紧闭，不透光"」原样漏进了成片提示词
        # （2026-08-30 实测）。别让标点差异决定判断。
        return re.sub(r"[^\w一-龥]", "", str(x or ""))

    ncorpus = _norm(corpus)
    keep = []
    for sent in re.split(r"(?<=[。；;])", t):
        c = sent.strip()
        if not c:
            continue
        core = _norm(c)
        if len(core) >= 6 and any(core[i:i + ngram] in ncorpus
                                  for i in range(max(0, len(core) - ngram + 1))):
            continue                      # 这一句抄自指令词，不是画面内容
        # 短句也要拦：「一律写／绝不写／必须写／照抄」是对**写的人**说的话，
        # 对视频模型没有意义，出现即判为指令残留。
        if re.search(r"(一律写|绝不写|必须写|不许写|照抄|下游会|实测)", c):
            continue
        keep.append(c)
    out = "".join(keep).strip()
    # ★ 和模板残留的尖括号一律清掉
    out = re.sub(r"[★＞>]+", "", out).strip(" 。；，")
    return out or re.sub(r"[★＞>]+", "", t.split("。")[0]).strip()


def _texture_footer(style, has_dialogue=True):
    """影像质感块。**直接用 render_notes.json 里这个画风自己的描述**——
    不许再按族写死几段去套 12 种画风：厚涂插画写的是「没有线稿、靠笔触堆出来」，
    被我的「二维动画族」硬编码套成「干净利落的线条、赛璐璐平涂」，
    同一份提示词里自己打自己（2026-08-29 用户发现）。
    kind 字段（photo/2d/3d）只用来给一句媒介声明。"""
    s = str(style or "")
    note, kind = "", "photo"
    try:
        import json as _j
        _rn = _j.loads((Path(__file__).resolve().parent.parent / "presets"
                        / "render_notes.json").read_text(encoding="utf-8"))
        _e = _rn.get(s) or {}
        note = str(_e.get("char") or _e.get("scene") or "").strip()
        kind = str(_e.get("kind") or "photo").strip()
    except Exception:
        pass
    if kind == "2d":
        medium = ("整段画面是**绘制出来的二维画面**，不是真人实拍、不是三维渲染；"
                  "皮肤和材质按上面这种画法处理，不要照片纹理、不要毛孔。")
    elif kind == "3d":
        medium = ("整段画面是**3D 动漫／游戏 CG 动画**（高预算游戏过场渲染），绝不是真人实拍、也不是手绘平面：角色是三维动画角色——"
                  "动漫化的五官（眼大、鼻小、下巴尖）、瓷面般光滑无毛孔的皮肤、大束造型化头发、干净饱和的材质、轮廓光和柔光晕；参考图里的人物照这种 CG 质感呈现，不要变成真人。")
    else:
        medium = ("整段画面是**实拍摄影**质感：自然光学、真实材质纹理、电影级景深，"
                  "暗部保留细节。")
    head = ("影像质感：" + note + "\n") if note else "影像质感："
    return head + medium + _COMMON_TEXTURE.format(
        lip=_LIP_SYNC if has_dialogue else "")


def h3_view(body, seconds, refs, vmap, cmap=None, link=None, canon=None, style=None):
    """分镜正文 → **官方 H3 提示词结构**。

    对齐本机官方节点 comfyui-vrgamedevgirl/VRGDG_MiniMaxH3PromptInstructions.py
    （2026-08-28 用户要求按官方模板来）：
      开头一句带时长画幅 → 每张参考图单独一段（图片N：用途）→
      原生音频段（**台词脚本只在这里整体引用一次**，谁说哪句、其他人闭嘴、
      首句 0.15 秒内开口、末字落在结尾前）→ 无缝时间块 [as–bs]
      （块里提到台词只写「说出台词N」，**不再引原文**）→
      音频段（最终契约）→ 一致性段（脸/发型/服装/单人不克隆）。
    refs: [(名字, 'char'|'scene')]；vmap: {名字: 声线}。
    """
    t = _H3_META.sub("", str(body or ""))
    # 「上一段」对 H3 无意义（它只看这一段），导演的接续用语改成本段视角。
    # 「从上一段的近景」直接删词会剩下「从的近景」（2026-08-28 实测），先补名词
    t = (t.replace("从上一段的", "从此前的").replace("从上一段", "从")
          .replace("使上一段镜头", "开场画面")
          .replace("使上一段", "开场时").replace("上一段", "此前"))
    t = re.sub(r"（此前收尾）", "", t)
    # 「X未出场。」是否定式提名——既会把没戏的人骗进参考包，又有召唤风险，整句剥掉
    t = re.sub(r"[^\n。；]*未出场[^\n。；]*[。；]?", "", t)
    # markdown 星号、行首列表符、接法指令语言——全局清（之前只清镜头文本，
    # 站位/开场状态字段并进首块时把「- **人物相对位置**」「人物状态接住」带进了 H3）
    t = t.replace("**", "")
    t = re.sub(r"^\s*[-·•]\s*", "", t, flags=re.M)
    t = re.sub(r"人物状态接住[:：]?\s*", "", t)
    t = re.sub(r"机位[AB][（(]?[，,]?\s*", "", t)   # 机位库内部标签，朝向词保留
    # 机位标签剥掉后的残根（「主正面背景是」「视角，正面背景是」）和机位句尾
    # 无配对的孤立「）」；换档说明（「此前是中景…景别跳档」）是给校验看的不是给 H3 看的
    t = t.replace("主正面背景是", "正面背景是").replace("视角，正面背景是", "正面背景是")
    t = re.sub(r"(正面背景是[^\n（]*?)）", r"\1", t)
    t = re.sub(r"此前是[^\n。]{0,60}。\s*", "", t)
    t = re.sub(r"[^\n。；]{0,20}景别跳档[。；]?\s*", "", t)
    fields, shots = _h3_fields(t)

    # 1) 抠台词：收进音频脚本，同时**在拍点内联原句**——
    # "说出台词①"的间接引用实测会被 H3 漏说（2026-08-28 用户对比：流水线版缺台词，
    # 内联版一句不漏），045/GPT 两版内联都验证过嘴上不漏、语气也更自然。
    def _sub(m):
        who, heart, tone, text = m.group(1), m.group(2), m.group(3), m.group(4)
        if heart:
            return ("%s的心声（%s）在画外响起：「%s」——他的嘴唇自始至终紧紧闭合，"
                    "下颌纹丝不动，只有眼神在变" % (who, tone or "低声", text))
        # 说话句保持散文——每句后面挂一串"不是念稿/口型同步"的机械尾巴，
        # 反而把台词演成念稿（2026-08-28 用户对比 GPT 版："越改越差"）。
        # 口型和语感的总要求收进段尾"影像质感"块，只说一次。
        return "%s用%s的声音自然开口说：「%s」" % (who, tone or "自然", text)

    for s in shots:
        s[3] = _H3_CUE.sub(_sub, s[3])
        # 镜头内的「[4-7秒]」「【0.0-2.0秒]」小标记：时间已由块头 [as–bs] 表达，重复即噪声
        s[3] = re.sub(r"[【\[]\s*[\d.]+\s*[-–—~]\s*[\d.]+\s*秒\s*[】\]]\s*", "", s[3]).strip()
        # 行首列表符号（「- 」「1. 」「· 」）：H3 吃散文不吃清单，全剥成短句散文
        s[3] = re.sub(r"^\s*(?:[-·•]|\d+[.、])\s*", "", s[3], flags=re.M)

    # 抠台词（音频块用）：老括号体已被 _sub 统一成散文，散文体（「用X的声音说：」
    # 「继续说道：」「语气略带调侃：」）没有括号语气，_H3_CUE 抓不到——
    # 实测整段 4 句台词被判成"本段没有对白"（2026-08-28）。改成单一路径：
    # 扫所有引号，就近人名归属；引号前必须紧贴冒号或说话动词，防止把
    # "说完「倔强」二字"这类复述当成新台词。
    known = set(vmap or ()) | set(cmap or ()) | {n for n, k in refs if k != "scene"}
    cues, seen = [], set()

    def _collect(text):
        for m in re.finditer(r"「([^」]+)」", str(text or "")):
            pre = str(text)[:m.start()].split("\n")[-1][-80:]
            if not re.search(r"(?:[，,：:]|说|道|问|答|唤|喊|响起|音)\s*$", pre):
                continue
            best, pos = None, -1
            for n in known:
                i = pre.rfind(n)
                while i >= 0:
                    # 「看向凯尔说」「凑近凯尔的耳畔说」的凯尔是宾语不是说话人：
                    # 前面是视线/趋近动词，或后面紧跟所有格「的」（心声除外），都不算
                    tail = pre[i + len(n):]
                    # 动词和人名之间可以隔「一旁的/身旁的」这类短语（0-4字），
                    # 紧贴匹配漏放了「看向一旁的玛莎说」→玛莎顶包说话人（2026-08-29）；
                    # 「目光从艾拉脸上」「停留在埃德里克身上」的人名同样是宾语
                    # 所有格排除白名单：「X的声音低沉：「…」」是标准归属句式不是宾语，
                    # 一刀切排除会让真说话人出局、就近错认成别人（seg5「你的手」实测）
                    if (re.search(r"(?:看向|望向|盯着|扫过|瞥向|转向|对着|朝着|凑近|靠近|"
                                  r"贴近|走向|走近|拍了拍|指着|停留在|落在|注视|锁定|"
                                  r"望着|盯住|直视|凝视|看着|瞪着|从)[^，。；：:\n]{0,4}$",
                                  pre[:i])
                            or (tail.startswith("的")
                                and not re.match(r"的(?:心声|声音|嗓音|语气|声线)", tail))):
                        i = pre.rfind(n, 0, i)
                        continue
                    break
                if i > pos:
                    best, pos = n, i
            if not best:
                continue
            # 别名归一：「埃德里克」「霍恩」都是同一张卡的别名——不归一，
            # 同一句话会被当成两个说话人各收一遍，H3 让"两个人"各说一次
            # （2026-08-28 实测台词四重复读）
            best = (canon or {}).get(best, best)
            ctx = pre[pos:]
            heart = "心声" in ctx or "画外" in ctx
            tm = (re.search(r"用([^的「」，。]{1,14})的(?:声音|语气)", ctx)
                  or re.search(r"（([^）]{1,14})）", ctx))
            # 超过8字的"语气"基本是声线卡原文被导演抄进了叙述
            # （「高音清透且略带气声」），语气应是情绪词，污染的回退"自然"
            if tm and len(tm.group(1)) > 8:
                tm = None
            nt = re.sub(r"\W", "", m.group(1))
            # 子串去重只认文本不认说话人：同句在正文块和台词字段各出现一次时，
            # 两处就近人名可能不同——按说话人分键会把同一句收成两条、两个人各说
            # 一遍（seg5「你的手」双人复读实测）。先见者（正文块）胜出。
            if any(nt in t or t in nt for w, t in seen):
                continue
            seen.add((best, nt))
            cues.append((best, heart, tm.group(1) if tm else "自然", m.group(1)))

    for s in shots:
        _collect(s[3])
    _collect(fields.get("台词", ""))   # 镜头里只写"轻声唤道"不带引文的漏网兜底

    total = shots[-1][1] if shots else float(seconds or 10)
    # 开头这一句也得跟画风走——写死「真人视频」时，选了日漫的项目开头说真人、
    # 结尾说二维动画，自相矛盾（2026-08-29 实测）。
    # 也按 render_notes 的 kind 判，别再自己按名字猜族——厚涂插画被猜成
    # 「二维动画」是错的（它是插画不是动画），措辞要能同时罩住赛璐璐和厚涂。
    _kind = {"2d": "二维绘制风格的视频", "3d": "三维渲染视频"}.get(
        _style_kind(style), "电影质感真人视频")
    out = ["生成一段 %g 秒、16:9 的%s。" % (float(seconds or total), _kind)]

    # 2) 参考图：一张一段，写明用途（官方：多视角设定板=同一个人，绝不克隆）
    for i, (n, kind) in enumerate(refs):
        if kind == "scene":
            out.append("图片%d：%s的场景参考图。只作为这个空间的结构、材质和光线依据；"
                       "机位、构图按下面的时间块来。" % (i + 1, n))
        else:
            out.append("图片%d：%s的人设参考图。这是一张多视角人物设定板，"
                       "图中所有视角都是同一个人；只作为%s的脸、发型、体型和服装依据。"
                       "画面中%s只出现一个，绝不复制克隆。" % (i + 1, n, n, n))

    # 3) 原生音频：**台词骨架**——只报句数/顺序/说话人，不引原文。
    # 引原文＋块内内联＝同句两处，H3 随机复读（2026-08-29 用户点名"很影响观感"）；
    # 原文一删干净，背对镜头的画外音又被整句丢掉（seg1 两卷实测漏说）。
    # 骨架＋"画外音照说"契约是两头都验证过的正式版方案（2026-08-29 定案）。
    if cues:
        _mani = "；".join("%s%s%s" % (_CIRCLED[i] if i < len(_CIRCLED) else str(i + 1),
                                      who, "（心声）" if heart else "")
                          for i, (who, heart, _t, _x) in enumerate(cues))
        lines = ["原生音频：由模型生成下列对白与环境音。对白逐字、按顺序生成，"
                 "谁的台词谁说，其他人保持闭口并自然反应；"
                 "第一句在开场 0.15 秒内开口，最后一个字在结尾前约 0.2 秒落下。"
                 "台词共%d句，顺序与说话人：%s。"
                 "台词原文只以下方时间轴内的引号台词为准，每一句都必须说出来——"
                 "说话人背对镜头或不在画面里时照样开口（画外音），一句不许漏；"
                 "每句台词全片只说一次，绝不重复。" % (len(cues), _mani)]
        spoken = list(dict.fromkeys(w for w, _, _, _ in cues))
        vlines = []
        for n in spoken:
            if vmap.get(n):
                vlines.append("%s——%s" % (n, vmap[n].rstrip("。；;")))
            else:
                # 没有人设卡的配角（伙计/路人）：至少给"要区分开"的指令，别让 H3 随缘
                vlines.append("%s——按这个角色的身份和年龄配音，和其他人的声音明显区分" % n)
        if vlines:
            lines.append("声线：" + "；".join(vlines) + "。不同角色的声音必须明显不一样。"
                         "对白要有真人对话的生活语感和情绪起伏，语速自然，绝不念稿。")
        out.append("\n".join(lines))
    else:
        out.append("原生音频：本段没有对白，所有人物保持闭口；只生成环境音。")

    # 3.5) 机位锁死段：背景锚是"每段自己换拍摄方向"的解药（2026-08-28 实测：
    # 三段三个轴、背景乱跳；写"身后是什么"比写"机位在哪"对 H3 有效得多）
    anchor = _strip_instruction_text(fields.get("本段背景锚", "")).strip(" 。；，")
    anchor = re.sub(r"^人物身后的背景自始至终是[：:]?\s*", "", anchor)
    # 锚里的"门缝透光/天光洒入"会被 H3 放大成开门灌日光（2026-08-28 实测：
    # seg1 门已关、seg2 开场门大开白昼光——块里没提门，光漏是锚带进去的）。
    # 门窗状态掐成"紧闭不透光"，透光短语整个摘除。
    anchor = re.sub(r"[，；]?\s*[^，；。]*(?:门缝|窗缝)[^，；。]*(?:透入|漏入|洒入|渗入)[^，；。]*", "", anchor)
    anchor = re.sub(r"(?:缓缓|刚刚)?(?:合拢|紧闭|关上|闭合)后的静止状态", "紧闭状态，不透光", anchor)
    if anchor:
        out.append("机位与画面方向（整段锁死）：%s；"
                   "拍摄轴不翻面，人物左右关系保持不变，光线明暗与色调与相邻镜头同族。" % anchor)

    # 3.6) 剧情衔接护栏：H3 看到"大厅+中景"会自作主张演一遍走进来的入场戏
    # （2026-08-28 实测：收尾是柜台前特写，下一段开场却拍成两人在大厅里走向镜头）。
    # GPT 80 分版靠的就是这句（"紧接发生，不要重新进入，不要重新开门"），照搬。
    if link:
        out.append(link)

    # 4) 时间块：无缝衔接；开场状态并进第一块，收尾状态并进最后一块
    for i, (a, b, cam, txt) in enumerate(shots):
        head = "[%gs–%gs]" % (a, b)
        parts = [("%s。" % cam) if cam else ""]
        # 场景/开场状态/站位并进哪一块：块1是过渡特写时并进块2——
        # 2 秒的手部特写块里塞进全屋大全景描述，H3 直接渲成宽景还多渲人影
        # （2026-08-28 实测两连败的根因）。过渡拍块必须纯净。
        _merge_at = 0
        if len(shots) > 1 and "特写" in (shots[0][2] or "") and \
                (shots[0][1] - shots[0][0]) <= 2.5:
            _merge_at = 1
        # 站位/开场状态并进**第一个宽景块**（中景以上）——并进单人近景块会把
        # "玛莎站在…艾拉站在…"塞进说话人的单人镜头，H3 就把所有人画进近景
        # （2026-08-29 用户打回"近景两侧站人"的祸根）。没有宽景块才退回首个正戏块。
        for _j in range(_merge_at, len(shots)):
            if any(w in (shots[_j][2] or "") for w in ("中景", "全景", "远景")):
                _merge_at = _j
                break
        if i == _merge_at:
            for k in ("本段场景", "开场状态", "人物站位"):
                v = fields.get(k, "").strip(" 。；，")   # 剥元信息后可能剩个孤立句号开头
                if k == "开场状态":
                    # 「镜头从X切至Y」「视线接」这类剪辑说明是给校验看的，
                    # 混进时间块会让 H3 在块中间执行一次莫名的切换。
                    # **按句剥，不按词剥**：原来枚举关键词，漏了「使用的是建立镜头
                    # （无过渡拍）」这种说法，只剥掉后半截、留下一个孤零零的
                    # 「使。」进了成片提示词（2026-08-30 逐字读发现）。
                    v = "".join(
                        c for c in re.split(r"(?<=。)", v)
                        if not re.search(r"(视线接|换档接|切至|接上一段|上一段|"
                                         r"过渡拍|建立镜头|接法|A/?B\s*接)", c)
                    ).strip(" 。；，")
                if v:
                    parts.append(v + "。")
        parts.append(txt)
        # 单人镜头排他句（2026-08-29 用户打回"近景两侧站人"）：特写/近景的台词块
        # 机械补「画面里只有说话人一人」——写进指令词的规矩 Qwen 两连无视，代码来。
        # 块内明确安排了别人在场（X在/站在/坐在/位于）或结束画面点到他人时不注入。
        _cam = cam or ""
        if ("特写" in _cam or "近景" in _cam) and "「" in txt:
            _known2 = set(vmap or ()) | set(cmap or ())
            # 说话人复用音频抠取器的归属结果（引号原文反查）——独立再猜一遍
            # 会重蹈就近人名的老坑（实测把玛莎的台词安给埃德里克）
            _spks = {w4 for w4, _h4, _t4, q4 in cues if ("「%s」" % q4) in txt}
            _spk2 = _spks.pop() if len(_spks) == 1 else None
            if _spk2:
                _blk = " ".join(p for p in parts if p)
                _others = [k for k in _known2
                           if k != _spk2 and _spk2 not in k and k not in _spk2]
                _grp = any(re.search(re.escape(k) + r"(?:在|站在|坐在|位于)", _blk)
                           for k in _others)
                if i == len(shots) - 1 and any(
                        k in fields.get("收尾状态", "") for k in _others):
                    _grp = True
                if not _grp:
                    parts.append("画面里只有%s一人，其他人物都在画框之外。" % _spk2)
        if i == len(shots) - 1 and fields.get("收尾状态"):
            parts.append("结束画面：" + fields["收尾状态"])
        out.append(head + "\n" + " ".join(p for p in parts if p).strip())

    # 4.5) 影像质感块：GPT 80 分版每段末尾都有这一块，我们缺失导致成片偏 CG/摆拍
    # （2026-08-28 用户对比）。全正面写法，整段只出现一次。
    # **必须跟着画风走**：这段原来写死"真实35mm/真实皮肤纹理/保留毛孔"，
    # 等于每段都命令模型拍成真人——选了日漫画风、人设图也是日漫，视频照样渲成
    # 写实真人（2026-08-29 用户发现）。按画风族给不同的质感页脚。
    out.append(_texture_footer(style, has_dialogue=bool(cues)))

    # 5) 音频契约 + 一致性
    amb = fields.get("环境音", "")
    out.append("音频：只生成上面列出的对白和环境音（%s）。全程没有配乐、"
               "没有歌声、没有旁白。" % (amb.rstrip("。") or "这个空间真实的环境声"))
    cons = []
    # 衣着以**人设卡原文**为权威——导演每段自己转述会漂
    # （实测同一人三段写出"皮甲/深色护甲/无外套"三个版本）。
    # 衣着一致性：服装权威＝参考图本身，500 字卡面原文整段删（2026-08-29 12视频
    # 实测删掉后衣着零走样）；只留防克隆句＋从卡面抠出的**剧情道具**（钥匙/怀表
    # 这类叙事挂件，参考图可能拍不清，点名保住）
    prop_lines, seen_cn = [], set()
    for n, kind in refs:
        if kind != "scene":
            hit = (cmap or {}).get(n)
            if hit and hit[0] not in seen_cn:
                seen_cn.add(hit[0])
                for frag in re.split(r"[；;。]", str(hit[1] or "")):
                    if re.search(r"钥匙|怀表|吊坠|项链|戒指|徽章|挂坠", frag):
                        prop_lines.append("%s%s" % (hit[0], frag.strip("，, ")))
    cons.append("每个人物的脸、发型、体型、服装全程与其参考图一致；"
                "同一个人绝不在画面里同时出现两次")
    if prop_lines:
        cons.append("剧情道具全程佩戴：" + "；".join(prop_lines))
    out.append("一致性：%s。" % "。".join(cons))
    final = "\n\n".join(x for x in out if x.strip())
    # 罗盘词→镜头相对（2026-08-29 定案）：东南西北只存在于纸面设定，H3 只认画面
    # 左右——引号台词里的原话（如"住东翼"）一字不动
    _parts = re.split(r"(「[^」]*」)", final)
    for _i, _p in enumerate(_parts):
        if _p.startswith("「"):
            continue
        for _pat, _rep in (
                (r"[东南西北]墙与[东南西北]墙的", ""),
                (r"[东南西北]墙(?:带木窗棂的)?", ""),
                (r"[东南西北]侧(?:石砌|厚重)?", ""),
                (r"（?朝[东南西北]拍）?", ""),
                (r"面向[东南西北]窗方向", "面向大窗方向"),
                (r"[东南西北]窗", "大窗")):
            _p = re.sub(_pat, _rep, _p)
        _parts[_i] = _p
    final = "".join(_parts)
    # 转译罗盘词会留下空壳：「（南侧）」删成「（）」、「机位A（朝北拍）：」剥成「：」。
    # 这些残渣送进 H3 就是噪声，清掉（2026-08-29 实测）。
    final = re.sub(r"[（(]\s*[）)]", "", final)
    final = re.sub(r"(?<![一-龥\w])[：:]\s*(?=正面背景是)", "", final)
    final = re.sub(r"[ 	]{2,}", " ", final)
    # 段内重复引号去重：同句第 2 次起剥引号变叙述（结束画面复述那类），
    # 防 H3 把复述当第二次台词
    _seen_q = {}

    def _dedup_q(mm):
        q = mm.group(1)
        _seen_q[q] = _seen_q.get(q, 0) + 1
        return mm.group(0) if _seen_q[q] == 1 else q

    final = re.sub(r"「([^」]{2,})」", _dedup_q, final)
    return final


def director_pack(sid, g):
    """导演段的最终出片包：(是不是导演段, 参考图名单, 图片路径, 送H3的完整提示词)。

    出片（gen_segment）和提示词预审用的是**同一个函数**——给用户过目的提示词
    和实际发给 H3 的必须一字不差（2026-08-28 用户要求先审提示词再出片）。

    参考图规则：用户在段上勾过/加过（g.refs）就完全听用户的；没动过才用
    正文自动匹配（姓名分段匹配 _names_in）＋按景别自动挂场景板
    （有中景以上镜头才挂，全特写段不挂——场景板是静态远景图，
    当强参考会把特写构图冲掉，2026-08-26 实测）。
    名单先按「有已采用设定图」筛过，@图片N 编号和实际图片严格一一对应。
    """
    body = g.get("prompt") or ""
    is_director = ("本段拍到" in body) or (not (g.get("shot_ids") or []) and "人物站位" in body)
    if not is_director:
        return False, [], [], body

    # 「X未出场」句不算出场证据——实测把没戏的艾拉骗进了参考包。
    # 站位/衣着/布局这类**场级罗列字段**同样不算——布伦特只在 seg1 的站位行里
    # 被提了名，人没进任何镜头，却占了一张参考图（2026-08-28 实测）。
    # 只有镜头行和台词才证明这个人真出现在画面里。
    _match_txt = "\n".join(
        l for l in body.splitlines()
        if not re.match(r"^(人物站位|人物衣着|本段布局|本段背景锚|开场状态)[：:]", l.strip()))
    names = _names_in(sid, re.sub(r"[^\n。；]*未出场[^\n。；]*[。；]?", "", _match_txt))
    scn_names = [str(x.get("name") or "") for x in asset_core.list_assets(sid, "scenes")]
    known = set(_names(sid)) | set(scn_names)
    picked = [str(r.get("n") or "") for r in (g.get("refs") or [])
              if str(r.get("n") or "") in known]
    if picked:
        names = list(dict.fromkeys(picked))
    else:
        hide = set(str(x) for x in (g.get("ref_hide") or []))
        names = [n for n in names if n not in hide]
    byname = _adopted_paths(sid)
    names = [n for n in names if byname.get(n) and os.path.exists(byname[n])][:9]
    if not picked and _wants_scene_ref(body):
        hit = director.seg_location(sid, body)
        if hit and hit not in names and byname.get(hit) and os.path.exists(byname[hit]):
            names = (names + [hit])[:9]

    # 长相服装由人设图管、空间由场景板管、拍什么由时间块管——不再加任何项目前缀
    # （世界观/镜头尺度那块每段重复、连走路段都被塞"情欲张力"，2026-08-28 用户否掉）。
    # 声线和衣着都按**卡上原文**为权威。键除了全名再补上短名——
    # 台词署名常用简称（凯尔），全名精确查会掉成兜底（2026-08-28 实测）。
    vmap, cmap, canon = {}, {}, {}

    def _safe_cut(s, cap):
        """边界安全截断：分号句（服装/声线卡常用）也算句边界——
        只认句号会把"下身是深灰长裤；…"截成"下身是。"（2026-08-28 实测）。"""
        s = str(s or "").strip()
        if len(s) <= cap:
            return s
        s = s[:cap]
        _i = max(s.rfind("。"), s.rfind("；"), s.rfind(";"))
        return s[:_i + 1] if _i >= 20 else s

    for c in asset_core.list_assets(sid, "characters"):
        nm = str(c.get("name") or "")
        v = _safe_cut(c.get("voice"), 90)
        cl = _safe_cut(c.get("clothing"), 160)
        for k in _name_keys(nm):
            canon.setdefault(k, nm)
            if v:
                vmap.setdefault(k, v)
            if cl:
                cmap.setdefault(k, (nm, cl))
    scn_set = set(scn_names)
    refs = [(n, "scene" if n in scn_set else "char") for n in names]
    link = None
    if int(g.get("no") or 1) > 1:
        link = ("剧情衔接：这一段紧接此前的剧情发生。开场第一帧，人物就已经处于下面"
                "开场描述的位置和状态，直接继续下一个动作——不重新入场、不重新开门、"
                "不重复已经发生过的剧情。")
    # 画风决定影像质感块（日漫/3D/写实三套）——不传的话日漫会被渲成真人
    try:
        from cores import project_settings as _ps3
        _st3 = story_core.get_story(sid) or {}
        _style = str((dict(_st3.get("settings") or {}, **(_ps3.get(_st3) or {}))
                      ).get("style") or "")
    except Exception:
        _style = ""
    h3_prompt = h3_view(body, g.get("seconds"), refs, vmap, cmap, link=link,
                        canon=canon, style=_style)
    # 送 H3 之前自检一遍：矛盾和残渣直接记进日志，不用人一行行读
    try:
        _bad = prompt_selfcheck(h3_prompt, _style)
        if _bad:
            log("error", "提示词自检发现 %d 处问题（第%s段）：%s"
                % (len(_bad), g.get("no"), "；".join(_bad)))
    except Exception:
        pass
    return True, names, [byname[n] for n in names], h3_prompt


@post(("/api/timeline/", "/generate-segment"))
def gen_segment(h, path, d):
    """出这一段的视频。没有提示词就先编一份。"""
    from cores import video_layer as vl
    from models import h3_client as h3
    sid = path.split("/")[3]
    _ep_of(sid, d)
    st, tl = _load(sid)
    no, sno = int(d.get("scene_no") or 1), int(d.get("seg_no") or 1)
    sc, g = _seg_of(tl, no, sno)
    if not g:
        return _RESP({"ok": False, "error": "找不到这一段"}, 400)
    # 页面上的提示词框是可编辑的：用户改完直接点生成，就按改完的这份生成
    if str(d.get("prompt") or "").strip():
        g = _apply(sid, no, sno, {"prompt": str(d["prompt"]).strip()})
    if not g.get("prompt"):
        try:
            g = _apply(sid, no, sno, {"prompt": _compile_prompt(sid, sc, g)})
        except Exception as e:
            return _RESP({"ok": False, "error": "编提示词失败：" + str(e)[:220]}, 500)

    if is_new_prompt(g.get("prompt")):
        from cores import reference_ready
        try:
            reference_ready.assert_references(sid, [g])
        except RuntimeError as error:
            return _RESP({"ok": False, "error": str(error)}, 400)
    critical = _render_critical_problems(st, tl, g, str(g.get("prompt") or ""), _ep_of(sid), sno)
    if critical:
        return _RESP({"ok": False,
                      "error": "第 %s 段提示词未通过出片检查：%s" %
                               (sno, "；".join(str(x) for x in critical[:4]))}, 400)

    # 【导演段】和界面预览共用 director_pack——预览给用户看的和实际发给 H3 的
    # 必须是同一份（2026-08-28 用户要求先审提示词再出片）。
    _is_director, _dir_names, _dir_imgs, _h3_prompt = director_pack(sid, g)

    prev = next((x for x in sc.get("segments") or [] if int(x.get("no")) == sno - 1), None)
    prev_ef = (prev or {}).get("end_frame") or None
    # 默认参考包只带这一段真出场的人——不在场的人的设定图也是一种"把他画进来"的暗示
    _ids = g.get("shot_ids") or []
    seg_chars = _on_screen_chars(
        [y for y in (sc.get("shots") or []) if y.get("shot_id") in _ids], _names(sid))
    scene_hit = _match_scene(g.get("location"),
                             [x.get("name") for x in asset_core.list_assets(sid, "scenes")])
    plan = vl.generation_plan(sno - 1, g.get("location"), (prev or {}).get("location"),
                              prev_ef, seg_chars, scene_hit or g.get("location"))
    byname = _adopted_paths(sid)
    # 用户勾了什么用什么；没勾过就用计划的默认包。两种来源最终都恒走 ref2va——
    # **每一段都必须有参考图**；同地点接续时上一段结束帧自动入包（负责衔接，不独扛身份）。
    picked = [r for r in (g.get("refs") or []) if r.get("n")]
    if picked:
        # 人设图只认全局已采用版，不允许某一段偷偷切回候选版。
        imgs = []
        for r_ in picked:
            pth = byname.get(r_.get("n"))
            if pth:
                imgs.append(pth)
    else:
        imgs = []
        for p_ in plan["images"]:
            if p_.get("path"):
                imgs.append(p_["path"])
            elif byname.get(p_.get("name")):
                imgs.append(byname[p_["name"]])
    if prev_ef and prev_ef not in imgs and (prev or {}).get("location") == g.get("location"):
        imgs.append(prev_ef)
    if _is_director:
        # 导演段恒走 ref2va；参考包由 director_pack 算好（用户勾过就听用户的，
        # 没勾过才自动匹配＋按景别挂场景板），和 @图片N 编号严格一一对应。
        imgs = _dir_imgs
        plan = {"mode": "ref2va"}
    elif is_new_prompt(g.get("prompt")):
        # 新链路的段：<Subject N> 按人物卡顺序＋场景卡对应参考图。
        # director_pack 认不出这种格式，原来这里组出 0 张图，单段就出不了片。
        imgs = new_pack(sid, g)
        plan = {"mode": "ref2va"}
        _h3_prompt = str(g.get("prompt") or "")
        # 【段间衔接】上一段的结束帧追加在所有编号 Subject **之后**，不打乱 Picture 编号。
        # 2026-09-05：上面那句「同地点接续时结束帧自动入包」被这一分支的 imgs 覆盖掉了，
        # 所有新链路项目段间都在闪。仍走 ref2va——末帧当首帧的 i2va 会换脸，已弃用。
        if prev_ef and os.path.exists(str(prev_ef)) and str(prev_ef) not in imgs and len(imgs) < 9:
            imgs.append(str(prev_ef))
            _h3_prompt = _h3_prompt.rstrip() + (
                "\n【接上一段】最后一张参考图（第 %d 张）是上一段的最后一帧。"
                "这一段的**第一帧必须从那一帧接着往下演**：人物在画面里的位置、朝向、"
                "镜头的景别和角度、光线和背景，全部保持连续；不许重新构图、"
                "不许让人物重新摆姿势、不许换机位重新开场。"
                "它只负责接戏——人物长相仍以前面的人物参考图为准。" % len(imgs))
    imgs = [x for x in imgs if x and os.path.exists(x)][:9]
    if not imgs:
        return _RESP({"ok": False,
                      "error": "这一段没有任何参考图。先去 🗂 资源 生成并采用人物和场景的设定图"}, 400)

    # 「先出测试档，通过了再出高清」需要**同一个种子**：换尺寸重生成不会一模一样，
    # 但固定种子能让构图和人物位置尽量贴近。所以这里把种子存下来，
    # 出高清时原样传回去（hd=true 时把尺寸档拉到 1.0）。
    hd = bool(d.get("hd"))
    seed = d.get("seed") or (g.get("seed") if (hd or d.get("same_seed")) else None)
    # 任务登记：游戏模式暂停时靠它知道该恢复哪一段
    _task_file = Path(__file__).resolve().parent.parent / "cache" / "current_task.json"
    try:
        _task_file.parent.mkdir(parents=True, exist_ok=True)
        _task_file.write_text(json.dumps({"sid": sid, "scene_no": no, "seg_no": sno,
                                          "hd": hd}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    try:
        log("video", "开始生成 场%d段%d（%s，%d张参考图，%s秒）"
            % (no, sno, plan["mode"], len(imgs), g.get("seconds")))
    except Exception:
        pass
    try:
        try:
            _steps = int(os.environ.get("V41_H3_STEPS") or 0) or None
        except Exception:
            _steps = None
        # 【原来这里写死了 None】size_tier=("1.0" if hd else None) 里那个 None，
        # 让设定页选的档在「生成这一段」这条路上永远传不下去（P242）。
        _tier, _ratio, _steps = _render_params(st, None, override=d)
        r = h3.generate(plan["mode"], _h3_prompt, images=imgs,
                        seconds=min(15, max(4, int(round(float(g.get("seconds") or 5))))),
                        ratio=_ratio, seed=(int(seed) if seed else None),
                        size_tier=("1.0" if hd else _tier), steps=_steps)
    except Exception as e:
        try:
            log("error", "场%d段%d 生成失败：%s" % (no, sno, str(e)[:120]))
        except Exception:
            pass
        return _RESP({"ok": False, "error": str(e)[:300]}, 500)
    finally:
        try:
            _task_file.unlink()
        except Exception:
            pass
    try:
        log("video", "完成 场%d段%d → %sx%s" % (no, sno, r.get("width"), r.get("height")))
    except Exception:
        pass

    src = r.get("output_path") or r.get("path")
    from cores import saga_core as _sgc
    out = _sgc.seg_video_path(sid, _ep_of(sid), no, sno, hd=hd)   # P237 文件名带话号
    os.makedirs(os.path.dirname(out), exist_ok=True)
    shutil.copy2(src, out)
    patch = {"mode": plan["mode"], "seed": r.get("seed"),
             "used_refs": [os.path.basename(x) for x in imgs]}
    for _k, _v in (("size_tier_hd" if hd else "size_tier", r.get("size_tier")),
                   ("ratio", r.get("ratio")), ("steps", r.get("steps"))):
        if _v not in (None, ""):
            patch[_k] = _v
    # 测试档和高清版分开存：高清出来了还要能回去看测试档做对比
    if hd:
        patch["video_hd"] = out
        patch["size_hd"] = "%sx%s" % (r.get("width"), r.get("height"))
    else:
        patch["video"] = out
        patch["size"] = "%sx%s" % (r.get("width"), r.get("height"))
        patch["video_hd"] = ""        # 测试档重出了，旧高清版作废
        # 结束帧只从测试档抽：高清版和测试档构图会有细微偏差，
        # 拿高清的结束帧去接测试档的下一段反而不稳
        ef = out[:-4] + "_end.png"
        try:
            subprocess.run(["ffmpeg", "-y", "-sseof", "-0.1", "-i", out,
                            "-frames:v", "1", ef],
                           capture_output=True, timeout=120)
            if os.path.exists(ef):
                patch["end_frame"] = ef
        except Exception:
            pass
    g = _apply(sid, no, sno, patch)
    return _RESP({"ok": True, "data": {"segment": g}})
