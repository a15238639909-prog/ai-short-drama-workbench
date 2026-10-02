# -*- coding: utf-8 -*-
"""saga_api.py — 长篇：故事圣经 / 分话 / 锁定 / 重新规划后续。

三条铁律（界面和后端都按它办）：
    1 已发生事实不可变
    2 当前集可以精细修改
    3 未来只是动态规划，不是已经写完的故事
"""
import re
import threading
import time

from api import post
from api._shared import _RESP, ctx

globals().update(ctx())

_JOBS = {}


def _job(sid):
    return _JOBS.setdefault(sid, {
        "running": False, "step": "", "err": "", "title": "",
        "done": 0, "total": 0, "percent": 0, "eta_seconds": None,
        "started_at": None, "step_started_at": None, "updated_at": time.time(),
        "durations": [], "logs": []})


def _job_event(j, level, msg):
    """任务自己的日志和全站日志同时写，日志按钮才能看到真实步骤。"""
    e = {"ts": time.time(), "level": level, "msg": str(msg)}
    j.setdefault("logs", []).append(e)
    if len(j["logs"]) > 200:
        del j["logs"][:-200]
    j["updated_at"] = e["ts"]
    log(level, msg)


def _start_job(sid, title, total):
    j = _job(sid)
    now = time.time()
    j.clear()
    j.update({
        "running": True, "step": "准备", "err": "", "title": title,
        "done": 0, "total": max(0, int(total)), "percent": 0,
        "eta_seconds": None, "started_at": now, "step_started_at": now,
        "updated_at": now, "durations": [], "logs": []})
    _job_event(j, "info", "%s：任务开始" % title)
    return j


class JobCancelled(Exception):
    """用户点了取消。每一步开头都会查一次标志，查到就从这里抛出去。"""


def _set_step(j, label):
    if j.get("cancel"):
        raise JobCancelled("已取消")
    j["step"] = str(label)
    j["step_started_at"] = time.time()
    _job_event(j, "info", "开始：%s" % label)


def _complete_step(j, detail=""):
    now = time.time()
    started = float(j.get("step_started_at") or now)
    j.setdefault("durations", []).append(max(0.01, now - started))
    j["done"] = min(int(j.get("total") or 0), int(j.get("done") or 0) + 1)
    total = int(j.get("total") or 0)
    j["percent"] = round(j["done"] * 100.0 / total, 1) if total else 0
    _job_event(j, "info", "完成：%s%s" % (j.get("step") or "当前步骤",
                                             ("（%s）" % detail) if detail else ""))


def _job_view(j):
    """返回随时间变化的真实进度；ETA 只用本次已完成步骤的实耗时估算。"""
    out = dict(j)
    out["logs"] = list(j.get("logs") or [])
    out.pop("durations", None)
    total, done = int(j.get("total") or 0), int(j.get("done") or 0)
    out["percent"] = round(done * 100.0 / total, 1) if total else 0
    eta = None
    ds = j.get("durations") or []
    if j.get("running") and total > done and ds:
        avg = sum(ds) / len(ds)
        current = max(0.0, time.time() - float(j.get("step_started_at") or time.time()))
        eta = max(0, int(round(avg * (total - done) - current)))
    elif not j.get("running") and not j.get("err"):
        eta = 0
    out["eta_seconds"] = eta
    out["server_time"] = time.time()
    return out


def _finish_job(j, error=""):
    if j.get("cancel"):
        # 用户主动停的，不算失败：已经出来的东西都留着
        j["step"] = "已取消"
        j["err"] = ""
        j["running"] = False
        j["updated_at"] = time.time()
        _job_event(j, "info", "任务已取消（已完成的部分保留）")
        return
    if error:
        j["err"] = str(error)[:300]
        j["step"] = "失败"
        _job_event(j, "error", "任务失败：%s" % j["err"])
    else:
        j["done"] = int(j.get("total") or j.get("done") or 0)
        j["percent"] = 100
        j["eta_seconds"] = 0
        j["step"] = "完成"
        _job_event(j, "info", "%s：全部完成" % (j.get("title") or "任务"))
    j["running"] = False
    j["updated_at"] = time.time()


@post(("/api/saga/", "/get"))
def saga_get(h, path, d):
    from cores import saga_core as sc
    sid = path.split("/")[3]
    saga = sc.get_saga(sid)
    _annotate_stale(sid, saga)
    return _RESP({"ok": True, "data": {"saga": saga, "progress": sc.progress(saga)}})


@post(("/api/saga/", "/episode-input"))
def saga_episode_input(h, path, d):
    from cores import manual_story
    sid = path.split("/")[3]
    try:
        e = manual_story.save_input(sid, int(d.get("ep") or 1), d.get("brief", ""),
                                    d.get("pace") or "适中", d.get("notes") or "",
                                    d.get("shooting_notes") or "")
        return _RESP({"ok": True, "data": e})
    except (ValueError, TypeError) as error:
        return _RESP({"ok": False, "error": str(error)}, 400)


@post(("/api/saga/", "/shots-input"))
def saga_shots_input(h, path, d):
    """P379 模式二：口述怎么拍 → 节拍表（后台任务，进度走 /progress）。"""
    from cores import authoring
    sid = path.split("/")[3]
    ep = int(d.get("ep") or 1)
    text = str(d.get("text") or "").strip()
    pace = str(d.get("pace") or "适中")
    if not text:
        return _RESP({"ok": False, "error": "先把怎么拍口述进去"}, 400)
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    if _timeline_busy(sid):
        return _RESP({"ok": False, "error": "分镜页的任务还在跑，等它结束再点"}, 400)
    j = _start_job(sid, "口述拆节拍", 1)

    def _run():
        try:
            sl = authoring.shots_input(sid, ep, text, pace, on_step=lambda m: _set_step(j, m))
            _complete_step(j)
            j["beats"] = len(sl)
            _job_event(j, "info", "第 %d 话拆成 %d 拍（%.0f 秒）；下一步写提示词" % (ep, len(sl), sum(float(x.get("seconds") or 0) for x in sl)))
            _finish_job(j)
        except Exception as ex:
            _job_event(j, "error", str(ex)[:200])
            _finish_job(j, str(ex)[:120])

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


@post(("/api/saga/", "/episode-approve"))
def saga_episode_approve(h, path, d):
    from cores import manual_story
    try:
        e = manual_story.approve(path.split("/")[3], int(d.get("ep") or 1))
        return _RESP({"ok": True, "data": e})
    except (ValueError, TypeError) as error:
        return _RESP({"ok": False, "error": str(error)}, 400)


def _annotate_stale(sid, saga):
    """给每一话补两个只读标记：剧本是不是照旧正文写的、分镜是不是照旧剧本拆的（P164②）。
    只加在返回给页面的那份上，不存盘。"""
    from cores import authoring as _au, saga_core as _sc
    try:
        _rows_b = [r for r in (((story_core.get_story(sid) or {}).get("settings") or {}).get("plan_rows") or []) if isinstance(r, dict)]
    except Exception:
        _rows_b = []
    for e in (saga.get("episodes") or []):
        try:
            no = int(e.get("no") or 0)
            if not str(e.get("brief") or "").strip() and 1 <= no <= len(_rows_b):
                e["brief"] = str(_rows_b[no - 1].get("one_line") or "")            # P321：简介 = 规划卡片的「这一话讲什么」（只回给页面，不存盘）
            st, why = _au.screenplay_stale(sid, no)
            e["script_stale"] = st
            e["script_stale_why"] = why
            _has_tl = any((sc0.get("segments") or []) for sc0 in ((e.get("timeline") or {}).get("scenes") or []))
            e["shots_stale"] = bool(_has_tl) and (bool(e.get("timeline_stale")) or _sc.timeline_stale(sid, no))    # P321：没有分镜不报
        except Exception:
            pass


@post(("/api/saga/", "/framework"))
def saga_framework(h, path, d):
    """设定页步骤1 展示用：整部框架（bible + 分季 + 各话标题）+ 第一话故事。
    api.post 会把 data 摊平回来，所以这里直接返回 bible/seasons/episodes。"""
    from cores import saga_core as sc
    sid = path.split("/")[3]
    saga = sc.get_saga(sid)
    _annotate_stale(sid, saga)
    return _RESP({"ok": True, "data": {
        "bible": saga.get("bible") or {},
        "seasons": saga.get("seasons") or [],
        "episodes": saga.get("episodes") or [],
    }})


# ───────── 本话安排（arrangement）：想法 → 安排卡 → 聊天式修改 → 确认落盘 ─────────
# 安排层本体在 cores/arrangement.py（draft / revise / confirm），这里只管收发和存盘。
# 四个路由都是同步的：一次千问 20–60 秒，页面直接等，不走 _job 的后台线程。


def _arrange_open(sid):
    """三个写路由（arrange / revise / confirm）共用的开场检查。
    返回 (st, settings, arrangement 模块, 错误响应)；错误响应非空时路由直接回它。
    生成任务在跑时拒绝：跑批线程会整份覆盖 settings，这时改安排会被它盖掉或把它盖掉。
    安排层没就绪（另有人在写 cores/arrangement.py）要明确说出来，不能 500。"""
    if _job(sid).get("running"):
        return None, None, None, _RESP({"ok": False, "error": "已经在跑了"}, 400)
    try:
        from cores import arrangement
    except ImportError:
        return None, None, None, _RESP({"ok": False, "error": "安排层未就绪"}, 400)
    st = story_core.get_story(sid) or {}
    settings = dict(st.get("settings") or {})
    return st, settings, arrangement, None


def _arrange_store(st, settings, arr):
    """安排卡落到 settings["arrangement"] 并存盘；三个写路由的统一出口。"""
    settings["arrangement"] = arr
    st["settings"] = settings
    story_core.save_story(st)
    return _RESP({"ok": True, "arrangement": arr})



# ═══════════════════════ 整部规划（P286，用户 2026-09-11 定的默认流程）═══════════════════════

def _plan_open(sid):
    if _job(sid).get("running"):
        return None, None, _RESP({"ok": False, "error": "已经在跑了"}, 400)
    st = story_core.get_story(sid) or {}
    if not st:
        return None, None, _RESP({"ok": False, "error": "故事不存在"}, 404)
    return st, dict(st.get("settings") or {}), None


def _plan_view(settings):
    from cores import whole_plan as WP
    plan = settings.get("story_plan") if isinstance(settings.get("story_plan"), dict) else None
    pending = settings.get("story_plan_pending") if isinstance(settings.get("story_plan_pending"), dict) else None
    out = {"plan": plan, "summary": WP.summary(plan) if plan else None, "pending": None, "impact": None, "note": "",
           "last_change": settings.get("story_plan_last_change") if isinstance(settings.get("story_plan_last_change"), dict) else None}
    if pending and isinstance(pending.get("plan"), dict):
        out["pending"] = pending["plan"]
        out["pending_summary"] = WP.summary(pending["plan"])
        out["impact"] = pending.get("impact")
        out["note"] = pending.get("note") or ""
    return out


def _plan_apply(st, settings, new_plan, note):
    """P319：改过的规划落盘——已确认且改动碰到已写好的话（要更新/退休）→ 进待确认；否则（草稿、或没碰到已写的话）直接确认生效。
    返回 ("applied"|"pending", impact)。"""
    from cores import whole_plan as WP
    confirmed = isinstance(settings.get("story_plan"), dict) and settings["story_plan"].get("status") == WP.STATUS_CONFIRMED
    if confirmed:
        imp = WP.impact(settings["story_plan"], new_plan, st.get("saga"))
        if imp.get("stale") or imp.get("retire"):
            settings["story_plan_pending"] = {"plan": new_plan, "impact": imp, "note": note, "at": time.time()}
            return "pending", imp
        saga = st.get("saga") or {"episodes": [], "seasons": [], "state": {}}
        WP.confirm(settings, new_plan, saga)
        st["saga"] = saga
        settings.pop("story_plan_pending", None)
        settings["story_plan_last_change"] = {"impact": imp, "note": note + "（没碰到已写好的话，直接生效）", "at": time.time()}
        return "applied", imp
    new_plan["status"] = WP.STATUS_DRAFT
    settings["story_plan"] = new_plan
    return "applied", None


def _ensure_plan_confirmed(st, settings):
    """P319：写某一话前，草稿规划自动落盘（不用先点「就按这个做」）。有待确认的改动先按它确认。"""
    from cores import whole_plan as WP
    pending = settings.get("story_plan_pending") if isinstance(settings.get("story_plan_pending"), dict) else None
    plan = (pending or {}).get("plan") or settings.get("story_plan")
    if not isinstance(plan, dict) or not plan.get("episodes"):
        return False
    if not pending and plan.get("status") == WP.STATUS_CONFIRMED:
        return True
    saga = st.get("saga") or {"episodes": [], "seasons": [], "state": {}}
    WP.confirm(settings, plan, saga)
    st["saga"] = saga
    settings.pop("story_plan_pending", None)
    st["settings"] = settings
    story_core.save_story(st)
    return True


@post(("/api/saga/", "/plan/", "/draft"))
def saga_plan_draft(h, path, d):
    """一段故事想法（+ 选填 画风/总时长/节奏）→ 整部规划草稿。后台跑（完整梗概 + 分话，2 次调用）。"""
    sid = path.split("/")[3]
    st, settings, bad = _plan_open(sid)
    if bad:
        return bad
    idea = str(d.get("idea") or "").strip()
    if not idea:
        return _RESP({"ok": False, "error": "先写一段故事想法"}, 400)
    prefs = {"style": str(d.get("style") or settings.get("style") or "").strip(), "notes": str(d.get("notes") or "").strip(),
             "total_sec": "", "pace": ""}                                  # P302：总时长/整部节奏已停用
    j = _start_job(sid, "整部规划", 2)

    def _run():
        from cores import whole_plan as WP
        try:
            _set_step(j, "先写通完整故事，再分话、排节奏、估时长")
            log = []
            plan = WP.draft(idea, settings, prefs, log=log)
            _complete_step(j)
            for m in log:
                _job_event(j, "info", m)
            cur = story_core.get_story(sid) or {}
            se = dict(cur.get("settings") or {})
            se["one_line"] = idea                                   # P298：整部规划的想法就是故事的根（下面那个一句话由它接管）
            if isinstance(se.get("story_plan"), dict) and se["story_plan"].get("status") == WP.STATUS_CONFIRMED:
                # 已确认过的规划不被新草稿直接顶掉：新草稿进 pending，用户看过影响再确认
                se["story_plan_pending"] = {"plan": plan, "impact": WP.impact(se["story_plan"], plan, cur.get("saga")),
                                            "note": "重新起草了整部规划", "at": time.time()}
            else:
                se["story_plan"] = plan
                se.pop("story_plan_pending", None)
                se.pop("story_plan_last_change", None)                    # P293：新草稿，上次改动说明作废
            cur["settings"] = se
            story_core.save_story(cur)
            _job_event(j, "info", "整部规划草稿：%d 话，预计 %s" % (len(plan.get("episodes") or []), WP.fmt_range(tuple(plan["total"]["seconds"]))))
            for w in WP.problems(plan)[:6]:
                _job_event(j, "warn", w)
            _finish_job(j)
        except Exception as ex:
            _job_event(j, "error", str(ex)[:300])
            _finish_job(j, str(ex)[:200])

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


@post(("/api/saga/", "/plan/", "/get"))
def saga_plan_get(h, path, d):
    sid = path.split("/")[3]
    st = story_core.get_story(sid) or {}
    return _RESP({"ok": True, "data": _plan_view(st.get("settings") or {})})


@post(("/api/saga/", "/plan/", "/revise"))
def saga_plan_revise(h, path, d):
    """聊天式修改整部规划：一句话 → 模型改（合并/拆分/调顺序/某一话慢下来/晚一话揭晓/改结尾）。后台跑（1 次）。
    草稿直接改；已确认过的进 pending，页面显示「改了哪些话、为什么影响后面」，用户确认一次才落盘。"""
    sid = path.split("/")[3]
    st, settings, bad = _plan_open(sid)
    if bad:
        return bad
    instruction = str(d.get("instruction") or "").strip()
    if not instruction:
        return _RESP({"ok": False, "error": "先说要改什么"}, 400)
    only_no = int(d.get("no") or 0)                                        # P302：单话修改
    from cores import whole_plan as WP
    base = (settings.get("story_plan_pending") or {}).get("plan") if isinstance(settings.get("story_plan_pending"), dict) else None
    base = base or settings.get("story_plan")
    if not isinstance(base, dict) or not base.get("episodes"):
        return _RESP({"ok": False, "error": "还没有整部规划，先生成"}, 400)
    j = _start_job(sid, "修改整部规划", 1)

    def _run():
        try:
            _set_step(j, "按你的话改整部规划")
            new_plan, note = WP.revise(base, instruction, settings, only_no=only_no)
            _complete_step(j)
            cur = story_core.get_story(sid) or {}
            se = dict(cur.get("settings") or {})
            _how, _imp = _plan_apply(cur, se, new_plan, note)             # P319：没碰到已写的话就直接生效
            if _how == "pending":
                se["story_plan_pending"]["instruction"] = instruction
            elif _imp is None:
                # P293：草稿也把"改了哪几话、为什么影响后面"摆出来（只看，不用再确认一次）
                se["story_plan_last_change"] = {"impact": WP.impact(base, new_plan, cur.get("saga")), "note": note,
                                                "instruction": instruction, "at": time.time()}
            cur["settings"] = se
            story_core.save_story(cur)
            _job_event(j, "info", "改好了：" + (note or "（模型没写说明）")[:200])
            _finish_job(j)
        except Exception as ex:
            _job_event(j, "error", str(ex)[:300])
            _finish_job(j, str(ex)[:200])

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


@post(("/api/saga/", "/plan/", "/edit"))
def saga_plan_edit(h, path, d):
    """直接编辑某一话（零模型）：{no, fields:{one_line|title|duration|landing|...}}。草稿直接改；已确认的进 pending。"""
    sid = path.split("/")[3]
    st, settings, bad = _plan_open(sid)
    if bad:
        return bad
    from cores import whole_plan as WP
    base = (settings.get("story_plan_pending") or {}).get("plan") if isinstance(settings.get("story_plan_pending"), dict) else None
    base = base or settings.get("story_plan")
    if not isinstance(base, dict) or not base.get("episodes"):
        return _RESP({"ok": False, "error": "还没有整部规划，先生成"}, 400)
    try:
        new_plan = WP.edit(base, int(d.get("no") or 0), d.get("fields") or {})
    except Exception as ex:
        return _RESP({"ok": False, "error": str(ex)[:200]}, 400)
    _plan_apply(st, settings, new_plan, "直接改了第 %s 话" % d.get("no"))       # P319
    st["settings"] = settings
    story_core.save_story(st)
    return _RESP({"ok": True, "data": _plan_view(settings)})


@post(("/api/saga/", "/plan/", "/edit-story"))
def saga_plan_edit_story(h, path, d):
    """P316：整部级两栏直接编辑（零模型）：{synopsis, presentation}。草稿直接改；已确认的进 pending（各话指纹不变 → 旧稿不标过期）。"""
    sid = path.split("/")[3]
    st, settings, bad = _plan_open(sid)
    if bad:
        return bad
    from cores import whole_plan as WP
    base = (settings.get("story_plan_pending") or {}).get("plan") if isinstance(settings.get("story_plan_pending"), dict) else None
    base = base or settings.get("story_plan")
    if not isinstance(base, dict) or not base.get("episodes"):
        return _RESP({"ok": False, "error": "还没有整部规划，先生成"}, 400)
    try:
        new_plan, changed = WP.edit_story(base, {k: d.get(k) for k in ("synopsis", "presentation") if k in d})
    except Exception as ex:
        return _RESP({"ok": False, "error": str(ex)[:200]}, 400)
    if not changed:
        return _RESP({"ok": True, "data": _plan_view(settings)})
    _label = "、".join({"synopsis": "整个故事怎么发展", "presentation": "整体怎么展示"}[k] for k in changed)
    _plan_apply(st, settings, new_plan, "改了整部的「%s」（各话内容没动；新写的话照它）" % _label)       # P319
    st["settings"] = settings
    story_core.save_story(st)
    return _RESP({"ok": True, "data": _plan_view(settings)})


@post(("/api/saga/", "/plan/", "/merge"))
def saga_plan_merge(h, path, d):
    """P318：把第 no 话并入第 no-1 话（零模型）。草稿直接改；已确认的进 pending。"""
    sid = path.split("/")[3]
    st, settings, bad = _plan_open(sid)
    if bad:
        return bad
    from cores import whole_plan as WP
    base = (settings.get("story_plan_pending") or {}).get("plan") if isinstance(settings.get("story_plan_pending"), dict) else None
    base = base or settings.get("story_plan")
    if not isinstance(base, dict) or not base.get("episodes"):
        return _RESP({"ok": False, "error": "还没有整部规划，先生成"}, 400)
    try:
        new_plan = WP.merge_into_prev(base, int(d.get("no") or 0))
    except Exception as ex:
        return _RESP({"ok": False, "error": str(ex)[:200]}, 400)
    _plan_apply(st, settings, new_plan, "把第 %s 话并入了上一话" % d.get("no"))       # P319
    st["settings"] = settings
    story_core.save_story(st)
    return _RESP({"ok": True, "data": _plan_view(settings)})


@post(("/api/saga/", "/plan/", "/confirm"))
def saga_plan_confirm(h, path, d):
    """确认整部规划（pending 有就确认 pending，否则确认草稿）：落盘 plan_rows/story_design/plan_people，
    旧稿按 uid 标过期或退休，saga 话次按规划重排。"""
    sid = path.split("/")[3]
    st, settings, bad = _plan_open(sid)
    if bad:
        return bad
    from cores import whole_plan as WP
    pending = settings.get("story_plan_pending") if isinstance(settings.get("story_plan_pending"), dict) else None
    plan = (pending or {}).get("plan") or settings.get("story_plan")
    if not isinstance(plan, dict) or not plan.get("episodes"):
        return _RESP({"ok": False, "error": "还没有整部规划，先生成"}, 400)
    saga = st.get("saga") or {"episodes": [], "seasons": [], "state": {}}
    try:
        got, imp = WP.confirm(settings, plan, saga)
    except Exception as ex:
        return _RESP({"ok": False, "error": str(ex)[:200]}, 400)
    settings.pop("story_plan_pending", None)
    st["settings"] = settings
    st["saga"] = saga
    story_core.save_story(st)
    return _RESP({"ok": True, "data": dict(_plan_view(settings), impact=imp)})



@post(("/api/saga/", "/plan/", "/review"))
def saga_plan_review(h, path, d):
    """阶段 3 的程序检查（零模型）：每话正文/剧本有没有执行整部规划。只有「有问题」和「待审核」。"""
    from cores import whole_plan as WP
    sid = path.split("/")[3]
    st = story_core.get_story(sid) or {}
    try:
        got = WP.review_episodes(st.get("settings") or {}, st.get("saga") or {})
    except Exception as ex:
        return _RESP({"ok": False, "error": str(ex)[:200]}, 500)
    return _RESP({"ok": True, "data": {"review": got}})

@post(("/api/saga/", "/plan/", "/discard"))
def saga_plan_discard(h, path, d):
    sid = path.split("/")[3]
    st, settings, bad = _plan_open(sid)
    if bad:
        return bad
    settings.pop("story_plan_pending", None)
    st["settings"] = settings
    story_core.save_story(st)
    return _RESP({"ok": True, "data": _plan_view(settings)})


@post(("/api/saga/", "/arrange"))
def saga_arrange(h, path, d):
    """第一次确认的起点：一段想法（+ 时长 + 感觉）→ 安排卡草稿。
    每次调用都是从想法重新起草，旧草稿被整份替换（修改走 /arrange/revise）。"""
    sid = path.split("/")[3]
    st, settings, arrangement, bad = _arrange_open(sid)
    if bad:
        return bad
    idea = str(d.get("idea") or "").strip()
    if not idea:
        return _RESP({"ok": False, "error": "先写一段想法"}, 400)
    try:
        duration_sec = int(d.get("duration_sec") or 60)
        mood = str(d.get("mood") or "日常聊天").strip()
        arr = arrangement.draft(idea, duration_sec=duration_sec, mood=mood)
        return _arrange_store(st, settings, arr)
    except Exception as ex:
        return _RESP({"ok": False, "error": str(ex)[:200]}, 400)


@post(("/api/saga/", "/arrange/", "/revise"))
def saga_arrange_revise(h, path, d):
    """聊天式修改：一句指令 → 安排层改动相关条目、记一条 history。
    路由用三段式：/arrange/get、/arrange/revise 的后缀会撞上已有的 ("/api/saga/", "/get") 这类两段规则。"""
    sid = path.split("/")[3]
    st, settings, arrangement, bad = _arrange_open(sid)
    if bad:
        return bad
    instruction = str(d.get("instruction") or "").strip()
    if not instruction:
        return _RESP({"ok": False, "error": "先说要改什么"}, 400)
    arr = settings.get("arrangement")
    if not isinstance(arr, dict) or not arr:
        return _RESP({"ok": False, "error": "还没有安排卡，先生成"}, 400)
    try:
        arr = arrangement.revise(arr, instruction)
        return _arrange_store(st, settings, arr)
    except Exception as ex:
        return _RESP({"ok": False, "error": str(ex)[:200]}, 400)


@post(("/api/saga/", "/arrange/", "/confirm"))
def saga_arrange_confirm(h, path, d):
    """确认落盘：安排层把安排卡翻成 plan_rows / story_design / one_line 写进 settings，
    状态置 confirmed。之后「生成故事」看到 confirmed 就不再重新分话（见 _run_universal_story）。"""
    sid = path.split("/")[3]
    st, settings, arrangement, bad = _arrange_open(sid)
    if bad:
        return bad
    arr = settings.get("arrangement")
    if not isinstance(arr, dict) or not arr:
        return _RESP({"ok": False, "error": "还没有安排卡，先生成"}, 400)
    try:
        got = arrangement.confirm(settings, arr)
        # 契约说 confirm 返回安排卡；万一它只就地改 arr 不返回，别把 None 存进去
        return _arrange_store(st, settings, got if isinstance(got, dict) else arr)
    except Exception as ex:
        return _RESP({"ok": False, "error": str(ex)[:200]}, 400)


@post(("/api/saga/", "/arrange/", "/get"))
def saga_arrange_get(h, path, d):
    """页面打开时取现有安排卡；没有就给 None，页面据此显示输入框还是安排卡。"""
    sid = path.split("/")[3]
    st = story_core.get_story(sid) or {}
    settings = st.get("settings") or {}
    return _RESP({"ok": True, "arrangement": settings.get("arrangement") or None})


def _confirmed_arrangement(sid, ep=1):
    """已确认的安排，或 None（P288：按话取——整部规划派生第 ep 话的安排）。契约 B 是 authoring.confirmed_arrangement（另一个人在写）；
    它还没就绪时直接读 settings.arrangement——两边口径一样：status=="confirmed" 才算。"""
    try:
        from cores import authoring as _au
        fn = getattr(_au, "confirmed_arrangement", None)
        if callable(fn):
            got = fn(sid, ep)
            if isinstance(got, dict) and got:
                return got
            if got is None:
                return None
    except Exception:
        pass
    st = story_core.get_story(sid) or {}
    arr = (st.get("settings") or {}).get("arrangement")
    return arr if isinstance(arr, dict) and str(arr.get("status") or "") == "confirmed" else None


def _episode_slices(sid, ep=1):
    """这一话的整话切片（带 beat/beat_index，切段那边没接上时是没有这两个键的）。切不出来给 []。"""
    try:
        from cores import authoring as _au
        return list(_au.episode_slices(sid, int(ep or 1), cut=False))         # P336：和出提示词同一份切片（含并短段）；门看整话，不剪
    except Exception:
        return []


@post(("/api/saga/", "/arrange/", "/length"))
def saga_arrange_length(h, path, d):
    """出片前门问「这一话切出来 X 秒，预计 Y 秒放不下」，用户点了哪个（契约 E）。
    延长本话 → duration_sec 改成实际总秒数、预算按比例放大；
    留到下一话 → cut_after_beat = 累计秒数不超过预计的最后一个 beat。都记 history，status 保持 confirmed。
    实际秒数从这一话的切片算，不信页面传的数——页面看到的就是这份切片。"""
    sid = path.split("/")[3]
    st, settings, _arrangement, bad = _arrange_open(sid)
    if bad:
        return bad
    arr = settings.get("arrangement")
    if not isinstance(arr, dict) or not arr:
        return _RESP({"ok": False, "error": "还没有安排卡"}, 400)
    if str(arr.get("status") or "") != "confirmed":
        return _RESP({"ok": False, "error": "安排还没确认，先确认安排"}, 400)
    from cores import arrangement_ops as _ops, shotlist as SL
    choice = _ops.normalize_choice(d.get("choice"))
    if not choice:
        return _RESP({"ok": False, "error": "选择只能是「延长本话」或「把后半部分留到下一话」"}, 400)
    try:
        ep = int(d.get("ep") or 1)
    except Exception:
        ep = 1
    slices = _episode_slices(sid, ep)
    if not slices:
        return _RESP({"ok": False, "error": "第%d话还没有剧本或切不出段，算不出实际时长" % ep}, 400)
    total = sum(float(s.get("seconds") or 0) for s in slices)
    per = SL.beat_seconds(slices)
    per = {k: v for k, v in per.items() if k in SL.BEAT_STAGES and v > 0}
    try:
        new = _ops.apply_length_choice(arr, choice, total, per)
    except Exception as ex:
        return _RESP({"ok": False, "error": str(ex)[:200]}, 400)
    return _arrange_store(st, settings, new)


@post(("/api/saga/", "/build"))
def saga_build(h, path, d):
    """一句话（+ 用户已填的设定）→ 圣经 + 分季 + 第一季集纲。后台跑。"""
    from cores import saga_core as sc, asset_core
    sid = path.split("/")[3]
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    st = story_core.get_story(sid) or {}
    settings = dict(st.get("settings") or {})
    one = str(d.get("one_line") or settings.get("one_line") or st.get("one_line") or "").strip()
    if not one:
        return _RESP({"ok": False, "error": "缺一句话"}, 400)
    # 用户已经建好的人物是事实，带进去让故事围绕他们写
    settings["_characters"] = asset_core.list_assets(sid, "characters")
    j.update(running=True, step="写故事圣经", err="")

    def _run():
        try:
            got = sc.build_bible(one, settings, None)
            saga = sc.get_saga(sid)
            saga["bible"] = got.get("bible") or {}
            saga["seasons"] = got.get("seasons") or []
            saga["episodes"] = got.get("episodes") or []
            sc.save_saga(sid, saga)
            j["step"] = "完成"
        except Exception as e:
            j["err"] = str(e)[:300]
            j["step"] = "失败"
        finally:
            j["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


def _ep_scene_images(sid, no, j, skip_existing=True):
    """第 no 话场景图主体（episode-scenes 路由与「生视频/全部生成」链共用，
    2026-08-29 抽出）。进度走传入的 job j，不自己开/收 job。

    skip_existing=True（默认）：**已经有已采用场景图的场景直接跳过**——
    用户自己上传的场景板不会被自动生成顶掉，重复点也不白烧 GPU
    （2026-08-29 用户定）。要重画单张走资源页的「🏛 重新生成场景图」。"""
    from cores import asset_core, saga_core as sc, scene_layer, project_settings as _ps
    saga = sc.get_saga(sid)
    e = sc.episode(saga, no) or {}
    body = str(e.get("body") or "")
    if not body.strip():
        raise RuntimeError("第%d话还没有剧本" % no)
    st = story_core.get_story(sid) or {}
    settings = dict(st.get("settings") or {})
    settings.update(_ps.get(st))
    style = str(settings.get("style") or "电影级剧照")
    # 【美术指导】2026-09-06：出图前先把碎片场景卡归并成景、写设计稿（一次 Qwen）。
    # 卡都已有设计稿就跳过；没做成按原卡出图，不断链。
    from cores import authoring as _au_d
    if _au_d.use_new_pipeline():
        try:
            _cards0 = asset_core.list_assets(sid, "scenes") or []
            _ep_cards = _au_d.ensure_scenes(sid, no) if _au_d.plan_places(sid, no) else []      # 结构表地点（P131）
            # 结构表有地点：这一话有卡没设计稿就设计；没结构表：只在一张设计稿都没有时自动设计（重做走剧本页按钮）
            _need_design = [x for x in _ep_cards if not x.get("user_made")]        # P316b：用户新建的卡不触发重做设计稿（按自己的空间描述出图）
            if (_need_design and any(not str(x.get("design") or "").strip() for x in _need_design)) or \
                    (not _ep_cards and (not _cards0 or not any(str(x.get("design") or "").strip() for x in _cards0))):
                j["step"] = "美术指导设计场景（归并碎片场景、定主体/戏区/光/尺度）"
                _job_event(j, "info", "开始：" + j["step"])
                _dc = _au_d.design_scenes(sid, no)
                _job_event(j, "info", "场景设计稿 %d 个景：%s" % (
                    len(_dc), "、".join(str(x.get("name") or "") for x in _dc[:6])))
        except Exception as _ex:
            _job_event(j, "error", "场景设计稿没做成（按原卡出图）：" + str(_ex)[:120])
    heads = scene_layer._scene_headers(body)
    # 【新链路没有场景头】旧剧本靠「## 场景N｜地点｜…」这种头来知道要画哪些场景；
    # 新链路的剧本是画面稿格式，没有那种头，解析出来是空的，一张图都不画
    # （用户 2026-09-01 实测：场景卡有 3 个，场景图「无」）。
    # 场景卡本身才是权威——解析不出头就直接用卡。
    if not heads:
        from cores import authoring as _au0
        if _au0.use_new_pipeline():
            # 字段名要跟下面循环读的对上：location / interior / space
            _src_cards = (_au0.ensure_scenes(sid, no) if _au0.plan_places(sid, no)
                          else (asset_core.list_assets(sid, "scenes") or []))          # 有结构地点只画这一话的景（P131）
            heads = [{"location": str(c.get("name") or "").strip(),
                      "interior": str(c.get("interior") or "").strip(),
                      "space": str(c.get("space")
                                   or c.get("contract_text") or "").strip()}
                     for c in _src_cards
                     if str(c.get("name") or "").strip()]
            if heads:
                _job_event(j, "info", "剧本里没有场景头，直接用 %d 张场景卡出图"
                           % len(heads))
    # 先把剧本的拍摄条件（内外/尺度/在场）同步到场景卡（建卡逻辑见该函数）；结构表有地点时场景由结构定，不再按剧本抠（P131）
    try:
        from cores import authoring as _au_p
        if not _au_p.plan_places(sid, no):
            _sync_scene_cards_from_body(sid, no, e, body, settings)
    except Exception as ex:
        _job_event(j, "error", "同步场景卡出错（不影响出图）：" + str(ex)[:120])
    cards = {c.get("name"): c for c in asset_core.list_assets(sid, "scenes")}
    # 已有已采用图的场景（用户上传的也在内）——skip_existing 时不再重画
    try:
        _fx = _au_d.fix_scene_spaces(sid, no, on_step=lambda m: _set_step(j, m))      # P421：无效的空间描述先补写再出图
        if _fx:
            _job_event(j, "info", "补写了场景空间描述：" + "、".join(_fx))
    except Exception as _fex:
        _job_event(j, "error", "补写场景空间描述失败：" + str(_fex)[:120])
    if settings.get("no_images"):
        _job_event(j, "info", "项目设置 no_images：场景卡已设计、空间已补写，跳过出图")     # P429/P430：只做文字（放在设计和补写之后）
        return
    _has_img = set()
    if skip_existing:
        import os as _os
        for v in asset_core.list_assets(sid, "visuals"):
            if (v.get("status") == "adopted" and v.get("owner_id")
                    and v.get("path") and _os.path.exists(v["path"])):
                _has_img.add(v["owner_id"])
    # 人物名单：剧本头没有【在场】时，从这一场正文里出现的人名推谁在场。
    _char_names = [str(c.get("name") or "").strip()
                   for c in asset_core.list_assets(sid, "characters")
                   if str(c.get("name") or "").strip()]
    j["total"] = max(1, len(heads))
    done = set()
    imgs = []
    for hd in heads:
        loc = str(hd.get("location") or "").strip()
        interior = str(hd.get("interior") or "").strip()
        # 剧本场次头的 location 有时带内外后缀（"迷宫入口（内）"）有时不带
        # （"迷宫入口"），卡名可能是"迷宫入口（外）"。按 base+内外 后缀逐个试。
        base = re.sub(r"[（(][内外][)）]$", "", loc).strip()
        cands = [loc]
        if interior:
            cands.append("%s（%s）" % (base, interior))
        cands.append(base)
        card = next((cards[c] for c in cands if c in cards), None)
        if card is None:
            # 还找不到：base 开头且内外一致的卡认领
            card = next((c for nm, c in cards.items()
                         if nm.startswith(base)
                         and str(c.get("interior") or "") == interior), None)
        cardname = card.get("name") if card else loc
        if not card:
            _set_step(j, "跳过（没有场景卡）：" + loc)
            _complete_step(j)
            continue
        if card["scene_id"] in done:
            _complete_step(j)
            continue
        done.add(card["scene_id"])
        if card["scene_id"] in _has_img:
            _set_step(j, "已有场景图，跳过：" + cardname)
            _complete_step(j)
            continue
        # 剧情 = 这一场头几镜的动作 + 台词
        # 【新链路没有场景头】hd 里没有 "no"，用 .get 取，取不到就整篇当作这一场
        # ——新链路一话通常就一个连续空间，整篇给它比空着强
        # （用户 2026-09-01 实测：这里直接 KeyError 'no'，场景图一张没出）。
        _no = hd.get("no")
        seg = (body.split("## 场景%s" % _no, 1)[-1].split("## 场景", 1)[0]
               if _no is not None and ("## 场景%s" % _no) in body else body)
        # 在场人物：优先用剧本头的【在场】，没有就从这一场正文里出现的人名推。
        present = [x.strip() for x in
                   re.split(r"[、，,]", str(hd.get("cast") or "")) if x.strip() in _char_names]
        if not present:
            present = [nm for nm in _char_names if nm and nm in seg]
        acts = re.findall(r"^[（(][^）)]{1,30}[）)]\s*(.+)$", seg, re.M)[:3]
        action = "".join(acts) or hd.get("space") or ""
        _set_step(j, "画场景图：" + cardname)
        note = ""
        try:
            v = asset_core.generate_scene_master(
                sid, card["scene_id"], style_profile=style,
                scene_action=action, present_names=present)
            if isinstance(v, tuple):
                v = v[0]
            if v and v.get("visual_id"):
                asset_core.adopt(sid, v["visual_id"])
                imgs.append(cardname)
        except Exception as ex:
            note = "跳过：" + str(ex)[:80]
            _job_event(j, "error", "场景图失败（%s）：%s" % (cardname, str(ex)[:150]))
        _complete_step(j, note)
    # P422：剧本场景头没点到、但这一话用得着的卡（美术指导拆出来的「浅滩/林缘」）也要有图——没图的卡全补一遍
    try:
        for _c in asset_core.list_assets(sid, "scenes") or []:
            if _c.get("scene_id") in done or _c.get("scene_id") in _has_img or _c.get("merged_into"):
                continue
            if _c.get("episode") not in (None, "", int(no)):
                continue
            done.add(_c["scene_id"])
            _set_step(j, "画场景图（补）：" + str(_c.get("name") or ""))
            try:
                v = asset_core.generate_scene_master(sid, _c["scene_id"], style_profile=style, scene_action="", present_names=[])
                if isinstance(v, tuple):
                    v = v[0]
                if v and v.get("visual_id"):
                    asset_core.adopt(sid, v["visual_id"])
                    imgs.append(str(_c.get("name") or ""))
            except Exception as ex:
                _job_event(j, "error", "场景图失败（%s）：%s" % (_c.get("name"), str(ex)[:150]))
    except Exception as _ax:
        _job_event(j, "error", "补场景图这步出错：" + str(_ax)[:120])
    _job_event(j, "info", "第%d话场景图完成：%s" % (no, "、".join(imgs) or "无"))



@post(("/api/saga/", "/cancel"))
def saga_cancel(h, path, d):
    """取消这个项目正在跑的生成（全部生成 / 生视频 / 分镜页的任务），H3 立刻打断。
    已经生成出来的部分保留，不删。"""
    from api import timeline as _tl
    sid = path.split("/")[3]
    return _RESP({"ok": True, "data": _tl.cancel_project(sid)})


@post(("/api/saga/", "/progress"))
def saga_progress(h, path, d):
    return _RESP({"ok": True, "data": _job_view(_job(path.split("/")[3]))})


@post(("/api/saga/", "/write-episode"))
def saga_write_ep(h, path, d):
    """写某一集的详细正文。上下文分层喂（不给全量正文）。"""
    from cores import saga_core as sc, story_gen
    sid = path.split("/")[3]
    no = int(d.get("no") or 1)
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    saga = sc.get_saga(sid)
    e = sc.episode(saga, no)
    if not e:
        return _RESP({"ok": False, "error": "没有第 %d 集" % no}, 400)
    if no > 1:
        prev = sc.episode(saga, no - 1) or {}
        if not (prev.get("body") and prev.get("edited_by_user")):
            return _RESP({"ok": False,
                          "error": "先保存第 %d 话，才能生成第 %d 话" % (no - 1, no)}, 400)
    if e.get("status") == sc.LOCKED:
        return _RESP({"ok": False, "error": "第 %d 集已锁定。要改先解锁" % no}, 400)
    j.update(running=True, step="写第%d集" % no, err="")

    def _run():
        try:
            ctx_text = sc.build_context(saga, no)
            st = story_core.get_story(sid) or {}
            from cores import asset_core as _ac, project_prompt as _pp, project_settings as _ps
            # 只把本话计划里点名的人物/地点喂给正文链。旧代码缺卡时回退到全项目，
            # 会把别话的人物和场景混进来；缺卡只给一个 name 事实，让下游稍后补卡。
            _char_cards = _ac.list_assets(sid, "characters")
            _char_by_name = {str(x.get("name") or "").strip(): x for x in _char_cards}
            _chars = [_char_by_name.get(str(n).strip(), {"name": str(n).strip()})
                      for n in (e.get("chars") or []) if str(n or "").strip()]
            _scene_cards = _ac.list_assets(sid, "scenes")
            _scene_names = [str(x.get("name") or "").strip() for x in _scene_cards]
            _scene_by_name = {str(x.get("name") or "").strip(): x for x in _scene_cards}
            _scenes = []
            for _planned in (e.get("locations") or []):
                _raw = str(_planned or "").strip()
                if not _raw:
                    continue
                _matched = _pp.match_scene_name(_raw, _scene_names)
                _scenes.append(_scene_by_name.get(_matched, {"name": _raw}))
            _setup = {"characters": _chars, "scenes": _scenes}
            _settings = dict(st.get("settings") or {})
            _settings.update(_ps.get(st))
            body, meta = story_gen.generate_story_v2(
                ctx_text + "\n\n【要写的就是第%d集】" % no,
                _settings, None,
                on_step=lambda m: j.update(step="第%d集·%s" % (no, m)),
                setup=_setup)
            s2 = sc.get_saga(sid)
            e2 = sc.episode(s2, no)
            if e2.get("body"):        # 旧稿存历史版本，可回滚
                e2.setdefault("versions", []).append(
                    {"at": time.time(), "body": e2["body"]})
            e2["body"] = body
            e2["status"] = sc.DRAFT
            e2["core"] = meta.get("core")
            # 【骨架必须落盘】分镜层首选读骨架（场次、节拍、秒数都在里面），
            # 读不到才退回"从正文反推"那条老路——那正是节奏像 PPT 的来源。
            # 原来这里只存了 core，骨架直接扔掉，于是分镜永远走的是老路。
            e2["skeleton"] = meta.get("skeleton") or {}
            e2["core_stale"] = False
            e2["skeleton_stale"] = False
            e2["timeline_stale"] = True
            sc.save_saga(sid, s2)
            # 自动判定出的题材引擎由 story_gen 写回传入字典，持久化后下一次沿用。
            if _settings.get("genre_engine"):
                _latest_st = story_core.get_story(sid) or {}
                _ps.save(_latest_st, {
                    "genre_engine": _settings.get("genre_engine"),
                    "genre_engine_reason": _settings.get("genre_engine_reason") or "",
                })
            # 场次表定了每一场是内是外、什么时间、多大规模——这些是拍摄条件，
            # 必须落到场景卡上，出图才能锁住室内外、才知道该画多大多气派。
            # 【按 (地点,内外) 认卡】同一地点一内一外是两个空间，各一张卡。
            # 原来按地点名认卡，「迷宫入口 外」和「迷宫入口 内」写到同一张上，
            # 后写的覆盖前面的，卡上就成了"标着内、光却是树冠漏下的阳光"。
            # 骨架点到而卡里没有的场景，用剧本【空间】原文当场补一张卡——
            # 原来是 continue 跳过，于是那一场永远没有参考图。
            try:
                from cores import scene_layer as _sl
                _r = _sl.sync_scene_cards(sid, e2["skeleton"], body, no, _settings)
                if _r.get("created"):
                    _job_event(j, "info", "补建场景卡：%s" % "、".join(_r["created"]))
                if _r.get("renamed"):
                    _job_event(j, "info", "场景卡改名（区分内外）：%s" % "、".join(_r["renamed"]))
            except Exception:
                pass
            try:
                _st = story_core.get_story(sid) or {}
                _st["skeleton"] = e2["skeleton"]      # 分镜页读的是顶层这一份
                _st["skeleton_episode"] = no
                story_core.save_story(_st)
            except Exception:
                pass
            j["step"] = "完成"
        except Exception as ex:
            j["err"] = str(ex)[:300]
            j["step"] = "失败"
        finally:
            j["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


@post(("/api/saga/", "/generate-episode-brief"))
def saga_generate_episode_brief(h, path, d):
    """根据用户当前填写或原集纲，单独生成这一话的标题与故事简要。"""
    from cores import saga_core as sc
    sid = path.split("/")[3]
    no = int(d.get("no") or 1)
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    saga = sc.get_saga(sid)
    e = sc.episode(saga, no)
    if not e:
        return _RESP({"ok": False, "error": "没有第 %d 话" % no}, 400)
    if no > 1:
        prev = sc.episode(saga, no - 1) or {}
        if not (prev.get("body") and prev.get("edited_by_user")):
            return _RESP({"ok": False,
                          "error": "先保存第 %d 话，才能设计第 %d 话" % (no - 1, no)}, 400)
    if e.get("status") == sc.LOCKED:
        return _RESP({"ok": False, "error": "这一话已锁定，要改先解锁"}, 400)
    title = str(d.get("title") or e.get("title") or "")
    logline = str(d.get("logline") or e.get("logline") or "")
    brief = str(d.get("brief") or e.get("brief") or "")
    hook = str(d.get("hook") or e.get("hook") or "")
    j = _start_job(sid, "第%d话故事简要" % no, 1)

    def _run():
        try:
            _set_step(j, "生成第%d话故事简要" % no)
            got = sc.make_episode_brief(
                saga, no, title, logline, brief=brief, hook=hook)
            latest = sc.get_saga(sid)
            target = sc.episode(latest, no)
            if not target:
                raise ValueError("保存时找不到第 %d 话" % no)
            target.setdefault("outline_versions", []).append({
                "at": time.time(), "title": target.get("title") or "",
                "logline": target.get("logline") or "",
                "brief": target.get("brief") or "",
                "hook": target.get("hook") or ""})
            for _k in ("title", "logline", "brief", "hook"):
                target[_k] = got[_k]
            target["core_stale"] = True
            target["skeleton_stale"] = True
            target["timeline_stale"] = True
            sc.save_saga(sid, latest)
            _complete_step(j)
            _finish_job(j)
        except Exception as ex:
            _finish_job(j, ex)

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


@post(("/api/saga/", "/prose-issues"))
def saga_prose_issues(h, path, d):
    from cores.prose_edit import inspect_prose
    sid = path.split("/")[3]
    story = story_core.get_story(sid)
    if not story:
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    episodes = (story.get("saga") or {}).get("episodes") or []
    no = int(d.get("no") or 1)
    episode = next((e for e in episodes if int(e.get("no") or 0) == no), None)
    if episode is None:
        return _RESP({"ok": False, "error": "没有这一话"}, 404)
    source = "prose_draft" if d.get("source") == "prose_draft" else "prose"
    text = str(d.get("text") if d.get("text") is not None else episode.get(source) or "")
    settings = dict(story.get("settings") or {})
    settings.setdefault("one_line", story.get("one_line") or "")
    return _RESP({"ok": True, "data": inspect_prose(episodes, no, text, settings, source)})


_EP_SAVE_LOCK = threading.RLock()


@post(("/api/saga/", "/save-episode"))
def saga_save_ep(h, path, d):
    with _EP_SAVE_LOCK:
        return _saga_save_ep(h, path, d)


def _saga_save_ep(h, path, d):
    """用户手改的正文。用户写的是事实，直接存，不做任何加工。
    create=1：这一话还不存在就先建一个空的（自定义作品贴原文用）。"""
    from cores import saga_core as sc
    sid = path.split("/")[3]
    saga = sc.get_saga(sid)
    e = sc.episode(saga, int(d.get("no") or 1))
    if not e and d.get("create"):
        # 自定义作品：新项目里还没有任何话，贴进来的原文要有地方落
        # （2026-08-29；不传 create 的老调用行为一个字不变）
        from cores import authoring as _au
        saga, e = _au._ep(sid, int(d.get("no") or 1), create=True)
    if not e:
        return _RESP({"ok": False, "error": "没有这一集"}, 400)
    if e.get("status") == sc.LOCKED:
        return _RESP({"ok": False, "error": "这一话已锁定，要改先解锁"}, 400)
    if "expected_prose" in d and str(d["expected_prose"] or "") != str(e.get("prose") or ""):
        return _RESP({"ok": False, "error": "正文已在其他页面或任务中变化。本次没有覆盖，请先复制你的修改，再刷新核对。"}, 409)
    body_changed = (d.get("body") is not None
                    and str(d.get("body") or "") != str(e.get("body") or ""))
    prose_changed = (d.get("prose") is not None and str(d.get("prose") or "") != str(e.get("prose") or ""))
    shooting_changed = (d.get("shooting_notes") is not None and
                        str(d.get("shooting_notes") or "").strip() != str(e.get("shooting_notes") or "").strip())
    if d.get("brief") is not None and str(d.get("brief") or "") != str(e.get("brief") or ""):
        e["brief_edited_by_user"] = True
    if prose_changed:
        if e.get("prose"):
            e.setdefault("prose_versions", []).append({"at": time.time(), "prose": e["prose"],
                                                       "writing_review": e.get("writing_review")})
        e["writing_review"] = {}
        e["writing_attempt"] = {}
        e["prose_draft"] = ""
        e["body_stale"] = bool(e.get("body"))
        e["timeline_stale"] = True
    if body_changed and e.get("body"):
        e.setdefault("versions", []).append({"at": time.time(), "body": e["body"]})
    # brief（这一话的剧情真相层）和 hook（结尾钩子）也要能存回去——
    # 用户在框架阶段改的就是这两样，存不回去等于白改。
    planning_changed = any(
        d.get(k) is not None and str(d.get(k) or "") != str(e.get(k) or "")
        for k in ("logline", "brief", "hook"))
    cards_changed = any(
        isinstance(d.get(k), list)
        and [str(x).strip() for x in d[k] if str(x or "").strip()] != (e.get(k) or [])
        for k in ("chars", "locations"))
    for k in ("body", "prose", "title", "logline", "brief", "hook", "shooting_notes"):
        if d.get(k) is not None:
            e[k] = str(d[k])
    if (d.get("body") is not None and str(e.get("pictures") or "").strip()
            and str(e.get("pictures") or "") != str(e.get("body") or "")):
        # 新链路里 body 是页面上的剧本，pictures 是分镜真正读取的剧本。
        # 用户改完剧本后只更新 body 会让分镜继续切旧 pictures（213 实测）。
        e["pictures"] = str(e.get("body") or "")
        e.pop("shotlist", None)
        e["timeline_stale"] = True
    for k in ("chars", "locations"):
        if isinstance(d.get(k), list):
            e[k] = [str(x).strip() for x in d[k] if str(x or "").strip()]
    # 只有保存正文才算“前一话已确认”。单独保存标题/简要不能越过顺序门槛。
    if d.get("body") is not None:
        e["edited_by_user"] = True
    if planning_changed or cards_changed:
        e["core_stale"] = True
    if planning_changed or cards_changed or body_changed:
        e["skeleton_stale"] = True
        e["timeline_stale"] = True
    if shooting_changed:
        e["timeline_stale"] = True
        for _scene in ((e.get("timeline") or {}).get("scenes") or []):
            for _segment in (_scene.get("segments") or []):
                for _key in ("prompt", "prompt_auto", "prompt_edited", "prompt_edited_at"):
                    _segment.pop(_key, None)
                if str(_segment.get("video") or "").strip():
                    _segment["stale_video"] = True
    if e.get("status") == sc.EMPTY and e.get("body"):
        e["status"] = sc.DRAFT
    sc.save_saga(sid, saga)
    return _RESP({"ok": True, "data": {"episode": e}})


@post(("/api/saga/", "/impact"))
def saga_impact(h, path, d):
    """点「重新规划后续」之前，先告诉用户会影响谁。"""
    from cores import saga_core as sc
    sid = path.split("/")[3]
    return _RESP({"ok": True, "data": sc.impact_of(sid, int(d.get("from_no") or 1))})


@post(("/api/saga/", "/replan"))
def saga_replan(h, path, d):
    """重新规划后续：已发生的不动，近景重新详细规划，其余只更新集纲。"""
    from cores import saga_core as sc
    sid = path.split("/")[3]
    from_no = int(d.get("from_no") or 1)
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    j.update(running=True, step="重新规划第%d集之后" % from_no, err="")

    def _run():
        try:
            r = sc.replan_forward(sid, from_no)
            j["step"] = "完成：更新了 %d 集" % len(r.get("touched") or [])
        except Exception as e:
            j["err"] = str(e)[:300]
            j["step"] = "失败"
        finally:
            j["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


@post(("/api/saga/", "/lock"))
def saga_lock(h, path, d):
    """锁定一集：事实进状态档。AI 只出增量，代码做合并——历史不可篡改。"""
    from cores import saga_core as sc
    from cores.model_json import chat_json
    sid = path.split("/")[3]
    no = int(d.get("no") or 1)
    saga = sc.get_saga(sid)
    e = sc.episode(saga, no)
    if not e or not e.get("body"):
        return _RESP({"ok": False, "error": "这一集还没有正文"}, 400)
    ins = ("你是故事状态记录员。读这一集的正文，只输出**这一集新增了什么事实**的增量，"
           "不要复述已有的东西，不要总结剧情。\n\n"
           "输出 JSON：{\"at\":\"这集结束时人在哪\",\"level\":\"主角等级如果变了\","
           "\"abilities\":[\"新获得的能力\"],\"items\":[\"新获得的东西\"],"
           "\"party\":[\"新加入队伍的人\"],\"relations\":{\"A→B\":\"关系变成什么\"},"
           "\"world_facts\":[\"新确立的世界规则\"],\"revealed\":[\"观众新知道的\"],"
           "\"hidden\":[\"角色仍不知道的\"],\"open_threads\":[\"新留下的伏笔\"],"
           "\"resolved_threads\":[\"这集解决掉的旧伏笔\"],\"dead\":[\"死了的人\"],"
           "\"promises\":[\"许下的重要承诺\"]}\n没有的键给空数组。只输出 JSON。")
    try:
        from models import qwen_client

        def cm(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=1200, **kw)
        delta, _ = chat_json(ins, "第%d集正文：\n%s" % (no, e["body"][:5000]), cm,
                             validate=None, tries=2, temperature=0.2)
    except Exception as ex:
        return _RESP({"ok": False, "error": "状态提取失败，未锁定：%s" % str(ex)[:180]}, 502)
    if not isinstance(delta, dict):
        return _RESP({"ok": False, "error": "状态提取格式错误，本话未锁定"}, 502)
    meaningful = bool(delta.get("at") or delta.get("level")
                      or (delta.get("relations") or {})
                      or any(delta.get(k) for k in (
                          "abilities", "items", "party", "world_facts", "revealed",
                          "hidden", "open_threads", "resolved_threads", "dead", "promises")))
    if not meaningful:
        return _RESP({"ok": False, "error": "状态提取结果为空，本话未锁定，请重试"}, 502)
    sc.lock_episode(sid, no, delta)
    saga = sc.get_saga(sid)
    return _RESP({"ok": True, "data": {"state": saga.get("state"),
                                       "delta": delta,
                                       "progress": sc.progress(saga)}})


@post(("/api/saga/", "/unlock"))
def saga_unlock(h, path, d):
    """解锁：显式操作，界面上要告诉用户会影响下游。"""
    from cores import saga_core as sc
    sid = path.split("/")[3]
    saga = sc.get_saga(sid)
    e = sc.episode(saga, int(d.get("no") or 1))
    if not e:
        return _RESP({"ok": False, "error": "没有这一集"}, 400)
    e["status"] = sc.DRAFT
    e.pop("locked_at", None)
    sc.rebuild_state(saga)
    sc.save_saga(sid, saga)
    return _RESP({"ok": True, "data": {"episode": e}})


@post(("/api/saga/", "/save-state"))
def saga_save_state(h, path, d):
    """用户手改故事状态（AI 记错了才动）。"""
    from cores import saga_core as sc
    sid = path.split("/")[3]
    saga = sc.get_saga(sid)
    if isinstance(d.get("state"), dict):
        saga["state"] = d["state"]
        sc.save_saga(sid, saga)
    return _RESP({"ok": True, "data": {"state": saga.get("state")}})


@post(("/api/saga/", "/save-bible"))
def saga_save_bible(h, path, d):
    from cores import saga_core as sc
    sid = path.split("/")[3]
    saga = sc.get_saga(sid)
    if isinstance(d.get("bible"), dict):
        saga["bible"] = d["bible"]
    if isinstance(d.get("seasons"), list):
        saga["seasons"] = d["seasons"]
    sc.save_saga(sid, saga)
    return _RESP({"ok": True, "data": {"saga": saga}})


@post(("/api/story/", "/design-scenes"))
def design_scenes_route(h, path, d):
    """美术指导：重做第 no 话的场景设计稿（归并碎片场景、定主体/戏区/光/尺度）。后台跑。"""
    import threading
    from cores import authoring as _au
    sid = path.split("/")[3]
    no = int(d.get("no") or 1)
    _force = bool(d.get("force", True))                                 # 页面按钮＝整话重做；跑批补空卡传 force=False 只做没设计稿的
    j = _start_job(sid, "第%d话场景设计稿" % no, 1)

    def _run():
        try:
            j["step"] = "美术指导设计场景"
            cs = _au.design_scenes(sid, no, force=_force)
            _job_event(j, "info", "场景设计稿 %d 个景：%s" % (
                len(cs), "、".join(str(x.get("name") or "") for x in cs[:6])))
            _finish_job(j)
        except Exception as ex:
            _job_event(j, "error", "设计稿没做成：" + str(ex)[:200])
            try:
                _finish_job(j)
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "job": j.get("id") or j.get("job_id") or ""})


@post(("/api/story/", "/episode-scenes"))
def ep_scenes(h, path, d):
    """第二步 · 每场一张含人物的场景图。后台跑，带进度。

    【新流程】剧本写好后，skeleton 里每一场都有 地点/内外/时间/在场人物/剧情。
    按场次逐张出图：一句话（每个在场人的外观从卡的 look_full 取 + 剧情）→ Qwen → 图。
    这张图画的是"这一场正在发生什么"，给视频当参考。
    同名同内外的场景复用一张卡，不重复出。
    **已有已采用图的场景默认跳过**（保护用户上传的场景板）；force=1 才全部重画。
    """
    import threading
    from cores import asset_core, saga_core as sc, scene_layer, project_settings as _ps
    sid = path.split("/")[3]
    no = int(d.get("no") or 1)
    saga = sc.get_saga(sid)
    e = sc.episode(saga, no) or {}
    body = str(e.get("body") or "")
    if not body.strip():
        return _RESP({"ok": False, "error": "这一话还没有剧本，先生成剧本"}, 400)
    j = _start_job(sid, "第%d话场景图" % no, 1)

    def _run():
        try:
            _ep_scene_images(sid, no, j, skip_existing=not d.get("force"))
            _finish_job(j)
        except Exception as ex:
            _job_event(j, "error", "有一步没跑通：" + str(ex)[:200])
            try:
                _finish_job(j)
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


SL_STATUS_PROBLEM = "有问题"      # cores.shotlist.STATUS_PROBLEM 的别名（这里不便 import）


def _blocked_by_problems(j, rep):
    """检查报了"有问题"但没有要问的：也停下（P280）。P328②：一条问题一条 ask，进对话框逐条说。"""
    bad = [x for x in (rep.get("items") or []) if x.get("status") == SL_STATUS_PROBLEM]
    lines = ["%s：%s" % (x.get("check"), x.get("detail")) for x in bad][:6]
    asks = []
    for x in bad[:8]:
        if x.get("check") == "情节" and x.get("fix") == "beat":
            asks.append({"kind": "beat_missing", "ref": str(x.get("stage") or "")[:40],
                         "question": "剧本里「%s」这件事没有对应镜头" % str(x.get("stage") or "")[:30],
                         "options": ["照原文补镜头", "不用拍这件事", "就按现在的出片"]})
        else:
            asks.append({"kind": "problem", "ref": str(x.get("detail") or "")[:40],
                         "question": "%s：%s" % (x.get("check"), x.get("detail")),
                         "options": ["就按现在的出片", "我去改剧本"]})
    _chat_open(j.get("_sid"), j.get("_ep"), asks)                      # 先落盘，页面一看到停下就来读
    j["blocked"] = asks
    j["step"] = "有 %d 处要你定——在对话框里说一句就行" % len(asks)
    j["err"] = ""
    j["running"] = False
    j["updated_at"] = time.time()
    _job_event(j, "error", "出片前有 %d 处要你定，先不出片（已出的段都留着）：%s" % (len(bad), "；".join(lines)))
    return {"blocked": True, "report": rep}


def _prev_tail_of(sid, ep, i):
    """第 i 段的上一段结束状态（tail；没有就取最后一条镜头行）——按段重写提示词时要带上，不然这一段被当成全片开头写（P324②）。"""
    from cores import saga_core as _sc
    if int(i) <= 1:
        return ""
    try:
        tl = _sc.ep_timeline(sid, ep) or {"scenes": []}
        g = next((x for sc0 in (tl.get("scenes") or []) for x in (sc0.get("segments") or []) if int(x.get("no") or 0) == int(i) - 1), None)
        if not g:
            return ""
        t = str(g.get("tail") or "").strip()
        if not t:
            ls = [x for x in str(g.get("prompt") or "").splitlines() if x.strip().startswith("镜头")]
            t = ls[-1].strip() if ls else ""
        return t
    except Exception:
        return ""


def _judge_beats(j, rep):
    """P323c：「情节」项里"镜头和安排字面对不上"的，让模型判一次是不是同一件事；是 → 通过并去掉那条问题。"""
    from cores import authoring, shotlist as SL
    try:
        beat_asks = [a for a in (rep.get("asks") or []) if a.get("kind") == "beat"]
        if not beat_asks:
            return rep
        pairs = []
        for a in beat_asks:
            m = re.search(r"安排写的是「([^」]*)」，可镜头拍的是「([^」]*)」", str(a.get("question") or ""))
            pairs.append((m.group(1), m.group(2)) if m else ("", ""))
        verdict = authoring.beats_same_event(pairs)
        keep, passed = [], []
        for i, a in enumerate(beat_asks):
            if verdict.get(i):
                passed.append(str(a.get("ref") or ""))
            else:
                keep.append(a)
        if passed:
            rep["asks"] = [a for a in (rep.get("asks") or []) if a.get("kind") != "beat"] + keep
            for it in (rep.get("items") or []):
                if it.get("check") == "情节" and it.get("status") == SL.STATUS_UNKNOWN and any(p and p in str(it.get("detail") or "") for p in passed):
                    it["status"] = SL.STATUS_PASS
                    it["detail"] = "镜头和安排说法不同，但拍的是同一件事：" + str(it.get("detail") or "")[:60]
            if rep.get("status") == SL.STATUS_UNKNOWN and not rep.get("asks") and not any(x.get("status") == SL.STATUS_UNKNOWN for x in (rep.get("items") or [])):
                rep["status"] = SL.STATUS_PROBLEM if any(x.get("status") == SL.STATUS_PROBLEM for x in (rep.get("items") or [])) else SL.STATUS_PASS
            _job_event(j, "info", "情节说法不同但同一件事，算通过：%s" % "、".join(passed))
    except Exception as ex:
        _job_event(j, "error", "情节同义判断出错（按字面）：%s" % str(ex)[:80])
    return rep


def _judge_missing(j, rep, miss, arr, slices_v):
    """P328b：「X 这一段没有镜头」→ 把这件事和它前后段（段号 ±1 及没归属的）的镜头文字给模型判一次是不是已经拍了。
    是 → 那条改成通过；返回还真缺的那些。"""
    from cores import authoring, shotlist as SL
    try:
        stages = list(SL.stages(arr))
        btxt = {str(b.get("stage") or ""): str(b.get("text") or "") for b in (arr.get("beats") or []) if isinstance(b, dict)}
        pairs, rows = [], []
        for it in miss:
            st = str(it.get("stage") or "")
            if st not in stages:
                continue
            k = stages.index(st)
            near = [str(u.get("text") or "") for u in (slices_v or []) if SL.beat_index_of(u) in (k - 1, k + 1, -1)]
            shot = SL._n("".join(near))[:400]
            if not shot:
                continue
            pairs.append((btxt.get(st, st)[:60], shot))
            rows.append(it)
        if not pairs:
            return miss
        verdict = authoring.beats_same_event(pairs)
        left, passed = [], []
        for i, it in enumerate(rows):
            if verdict.get(i):
                it["status"] = SL.STATUS_PASS
                it["fix"] = ""
                it["detail"] = "「%s」拍在相邻段里（模型核过）" % str(it.get("stage") or "")[:24]
                passed.append(str(it.get("stage") or "")[:24])
            else:
                left.append(it)
        if passed:
            _job_event(j, "info", "情节缺段核过：其实拍在相邻段里——%s" % "、".join(passed))
            if not any(x.get("status") == SL.STATUS_PROBLEM for x in (rep.get("items") or [])):
                rep["status"] = SL.STATUS_UNKNOWN if (rep.get("asks") or any(x.get("status") == SL.STATUS_UNKNOWN for x in (rep.get("items") or []))) else SL.STATUS_PASS
        return left + [it for it in miss if it not in rows]
    except Exception as ex:
        _job_event(j, "error", "情节缺段核对出错（按字面）：%s" % str(ex)[:80])
        return miss


def _blocked_by_asks(j, rep):
    """判不了的地方交给用户：任务停下（running=False），j.blocked 里放候选，页面照它画按钮。"""
    _chat_open(j.get("_sid"), j.get("_ep"), rep["asks"])               # 先落盘，页面一看到停下就来读
    j["blocked"] = rep["asks"]
    j["step"] = "有 %d 处要你定——在对话框里说一句就行" % len(rep["asks"])
    j["err"] = ""
    j["running"] = False
    j["updated_at"] = time.time()
    _job_event(j, "error", "有 %d 处要你定，先不出片；答完会自动接着出。" % len(rep["asks"]))
    for _i, _a in enumerate(rep["asks"], 1):
        _job_event(j, "info", "问题 %d：%s（%s）" % (_i, str(_a.get("question") or "")[:80], "、".join(_a.get("options") or [])[:60]))
    return {"blocked": True, "report": rep}


# ─────────────────────────── P328②：出片前门的对话框 ───────────────────────────
_CIRC = "①②③④⑤⑥⑦⑧⑨⑩"
_ACCEPT_RE = re.compile(r"^(就按现在|按现在|就这样|照旧|直接出|都按现在|不用管|就按这个|按这个出|就按现在的出片|继续出)")
_SKIP_RE = re.compile(r"(不用拍|不拍了|不拍|跳过|删了|删掉|去掉|不要了|不要这|不需要)")
_ADD_RE = re.compile(r"(补上|补镜头|补一下|补拍|加上|加镜头|照原文|按原文|拍上|补回)")


def _chat_state(e):
    ch = dict((e or {}).get("gate_chat") or {})
    ch.setdefault("messages", [])
    ch.setdefault("asks", [])
    ch.setdefault("force", False)
    return ch


def _ask_key(a):
    return "%s|%s|%s" % (str(a.get("kind") or ""), str(a.get("ref") or ""), str(a.get("question") or "")[:60])


def _chat_opener(asks):
    """开场白：每条问题一句人话（不叫模型；模型只在解释回答时用）。"""
    out = []
    for i, a in enumerate(asks or []):
        c = _CIRC[i] if i < len(_CIRC) else "%d." % (i + 1)
        k, q, opts = str(a.get("kind") or ""), str(a.get("question") or ""), [str(x) for x in (a.get("options") or [])]
        if k == "speaker":
            m = re.search(r"「([^」]+)」", q)
            out.append("%s 「%s」这句我定不了是谁说的（%s）。你说是谁就行。" % (c, (m.group(1) if m else q)[:40], " / ".join(opts) or "在场的人"))
        elif k == "scene":
            out.append("%s %s 候选：%s。选一张，或者说「新建」。" % (c, q, " / ".join(opts)))
        elif k == "beat_missing":
            out.append("%s %s。可以说「补上」（我照原文补几行）、「不用拍」，或者直接告诉我怎么拍。" % (c, q))
        elif k == "beat":
            out.append("%s %s 说「就按现在的」，或者告诉我改法。" % (c, q))
        elif k == "length":
            out.append("%s %s——「延长本话」还是「留到下一话」？" % (c, q))
        else:
            out.append("%s %s 说「就按现在的出」，或者告诉我怎么改。" % (c, q))
    if len(asks or []) > 1:
        out.append("可以一条一条说（带编号），也可以一句「就按现在的出」全过。都说清了我就接着出片。")
    elif asks:
        out.append("说清了我就接着出片。")
    return "\n".join(out)


def _chat_open(sid, ep, asks):
    """门停下来：把问题和开场白写进这一话的 gate_chat（答过的沿用答案）。"""
    if not sid:
        return
    from cores import authoring
    try:
        _saga, e = authoring._ep(sid, int(ep or 1), create=False)
        ch = _chat_state(e or {})
        # 门每次停下都是一份新清单：上次答过的不沿用（再问同一条说明上次的答法没解决；沿用 skip 答案会让页面
        # 不带 force 一直重跑门→死循环），对话也从头来
        new = [dict(a, answered="") for a in (asks or [])]
        ch["asks"] = new
        ch["force"] = False
        ch["messages"] = [{"who": "qwen", "text": _chat_opener(new), "at": time.time()}]
        authoring._update_ep(sid, int(ep or 1), gate_chat=ch)
    except Exception:
        pass


def _chat_names(sid, ep, asks):
    from cores import authoring, asset_core as _ac
    cast = []
    try:
        _saga, e = authoring._ep(sid, ep, create=False)
        sl = ((e or {}).get("shotlist") or {}) if isinstance((e or {}).get("shotlist"), dict) else {}
        cast = [str(c.get("name") or "") for c in (sl.get("cast") or []) if isinstance(c, dict) and c.get("name")]
    except Exception:
        pass
    for a in asks or []:
        if a.get("kind") == "speaker":
            cast += [str(x) for x in (a.get("options") or [])]
    try:
        scenes = [str(s.get("name") or "") for s in (_ac.list_assets(sid, "scenes") or []) if s.get("name")]
    except Exception:
        scenes = []
    return [x for x in dict.fromkeys(cast) if x], scenes


_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_NUM_HEAD = re.compile(r"^\s*(?:第)?\s*([①②③④⑤⑥⑦⑧⑨⑩]|\d{1,2}|[一二两三四五六七八九十])\s*[.、:：个条句项]?\s*(.*)$", re.S)
_NEG_BEFORE = re.compile(r"(不是|不要|别|不选|非)\s*$")
_QUESTION_RE = re.compile(r"[?？]\s*$|为什么|为啥|怎么回事|是什么意思|什么意思|啥意思|怎么办|能不能|可不可以|吗[?？]?$")


def _num_of(tok):
    if tok in _CIRC:
        return _CIRC.index(tok) + 1
    if tok in _CN_NUM:
        return _CN_NUM[tok]
    try:
        return int(tok)
    except Exception:
        return 0


def _hits(names, text):
    """text 里出现的候选（按长度从长到短；被「不是/不要/别」直接否定的不算）。返回按出现位置排序的 [(pos, name)]。"""
    out, taken = [], []
    for n in sorted({x for x in names if x}, key=len, reverse=True):
        for m in re.finditer(re.escape(n), text):
            if any(a <= m.start() < b for a, b in taken):
                continue                                          # 被更长的候选占了（「公寓大堂至客厅」里的「公寓大堂」）
            if _NEG_BEFORE.search(text[max(0, m.start() - 3):m.start()]):
                taken.append((m.start(), m.end()))
                continue
            taken.append((m.start(), m.end()))
            out.append((m.start(), n))
    return sorted(out)


def _chat_parse_one(t, idx, asks, cast, scenes):
    """一句（可能带编号 idx，-1=没带）→ [(type, ask_index, value)]。"""
    open_ix = [i for i, a in enumerate(asks) if not a.get("answered")]
    kind_at = (asks[idx].get("kind") if idx >= 0 else "")

    def _first(kinds):
        if idx >= 0:
            return idx if kind_at in kinds else -1
        for i in open_ix:
            if asks[i].get("kind") in kinds:
                return i
        return -1

    if _ACCEPT_RE.search(t):
        if idx >= 0:
            return [("skip", idx, "就按现在的出片")]             # 带编号：只过这一条
        return [("accept_all", -1, "就按现在的出片")]
    # 候选原文（含按钮文案「① 照原文补镜头」「2 延长本话」）；同一句里的否定和先后都看
    cand = [idx] if idx >= 0 else open_ix
    best = None
    for i in cand:
        for pos, o in _hits(asks[i].get("options") or [], t):
            if best is None or pos > best[0]:
                best = (pos, i, o)                                  # 「不是林澈，是苏蔓」→ 取最后一个没被否定的
    if best is not None:
        _p, i, o = best
        k = asks[i].get("kind")
        if k == "speaker":
            return [("speaker", i, o)]
        if k == "scene":
            return [("scene", i, o)]
        if k == "length":
            return [("length", i, o)]
        if k == "beat_missing" and o.startswith("照原文"):
            return [("add_shots", i, "")]
        if k == "beat_missing" and o.startswith("不用拍"):
            return [("skip", i, "不拍这件事")]
        if re.match(r"^(就按|已经拍了|继续|在场|不在场)", o):
            return [("skip", i, o)]                     # 在场/不在场（present）没有单独的改法：都是"照现在的出"
        if re.match(r"^(不对|我去改|漏了)", o):
            return [("go_edit", i, o)]
    if _SKIP_RE.search(t):
        i = _first(("beat_missing", "beat", "event", "problem", "present"))
        if i >= 0:
            return [("skip", i, "不拍这件事" if asks[i].get("kind") == "beat_missing" else "就按现在的出片")]
    if _ADD_RE.search(t):
        i = _first(("beat_missing",))
        if i >= 0:
            return [("add_shots", i, "")]
    if "延长" in t or "下一话" in t:
        i = _first(("length",))
        if i >= 0:
            neg_ext = re.search(r"(不|别|不要|无需|不用)\s*延长", t)
            neg_def = re.search(r"(不|别|不要|不用)\s*(留到|留给|放到)?\s*下一话", t)
            if "延长" in t and not neg_ext:
                return [("length", i, "延长本话")]
            if "下一话" in t and not neg_def:
                return [("length", i, "留到下一话")]
            if neg_ext:
                return [("length", i, "留到下一话")]
            if neg_def:
                return [("length", i, "延长本话")]
    hit = _hits(cast, t)
    if hit:
        i = _first(("speaker",))
        nm = hit[-1][1]
        if i >= 0 and (re.search(r"说的|是他|是她|说|讲", t) or t == nm or len(t) <= len(nm) + 2):
            return [("speaker", i, nm)]
    hit_s = _hits(scenes, t)
    if hit_s:
        i = _first(("scene",))
        if i >= 0:
            return [("scene", i, hit_s[-1][1])]
    if re.search(r"新建", t):
        i = _first(("scene",))
        if i >= 0:
            o = next((x for x in (asks[i].get("options") or []) if str(x).startswith("新建")), "")
            if o:
                return [("scene", i, o)]
    return []


def _chat_parse(text, asks, cast, scenes):
    """规则解析：能确定的直接给动作（不叫模型）。返回 [(type, ask_index, value)]；解析不了返回 []。
    · 问句不当动作；· 「① … ② …」按编号拆开各解析；· 编号越界不猜（返回空，交给模型追问）。"""
    t = str(text or "").strip()
    if not t or not asks:
        return []
    if _QUESTION_RE.search(t):
        return []
    parts = [p.strip() for p in re.split(r"(?=[①②③④⑤⑥⑦⑧⑨⑩])", t) if p.strip()]
    if len(parts) <= 1:
        parts = [t]
    acts = []
    for p in parts:
        idx = -1
        m = _NUM_HEAD.match(p)
        rest = p
        if m:
            n = _num_of(m.group(1))
            if not (1 <= n <= len(asks)):
                return []                                       # 编号越界：不猜别的，交给模型追问
            idx, rest = n - 1, (m.group(2).strip() or p)
            if idx >= 0 and asks[idx].get("answered") and len(parts) > 1:
                continue
        got = _chat_parse_one(rest, idx, asks, cast, scenes)
        for g in got:
            if g not in acts:
                acts.append(g)
    return acts


def _chat_apply(sid, ep, asks, acts, ready_n=5):
    """执行动作，改清单/画面稿；返回给用户看的结果句列表。asks 就地标 answered。"""
    from cores import authoring, shotlist as SL, saga_core as sc
    from api import timeline as _tl
    notes = []
    force = False
    pics_changed = False
    # P328f⑤：几条 edit_lines 一起来时按行号从大到小改，前面的行号才不会被先改的挪位
    acts = sorted(acts, key=lambda x: (-(x[2][0]) if (x[0] == "edit_lines" and isinstance(x[2], tuple)) else 0))
    for typ, i, val in acts:
        a = asks[i] if 0 <= i < len(asks) else None
        if typ == "accept_all":
            for x in asks:
                if not x.get("answered"):
                    x["answered"] = "就按现在的出片"
            force = True
            notes.append("好，剩下的都按现在的出。")
            continue
        if a is None and typ != "edit_lines":
            continue
        if typ == "skip":
            a["answered"] = val
            force = True
            notes.append("%s 记下：%s。" % (_CIRC[i] if i < len(_CIRC) else i + 1, val))
        elif typ == "go_edit":
            a["answered"] = ""
            notes.append("好，你去 📖 剧本页改；改完再点「制作本话」/「生成」，我会重查。")
        elif typ == "speaker":
            r = _tl.tl_shotlist_speaker(None, "/api/timeline/%s/shotlist-speaker" % sid, {"ep": ep, "line_n": a.get("ref"), "name": val})
            if r.get("ok"):
                a["answered"] = val
                notes.append("%s 这句记成 %s 说的了。" % (_CIRC[i] if i < len(_CIRC) else i + 1, val))
            else:
                notes.append("%s 没记上：%s" % (_CIRC[i] if i < len(_CIRC) else i + 1, r.get("error")))
        elif typ == "scene":
            mnew = re.match(r"^新建场景卡「(.+)」$", str(val))
            body = {"ep": ep, "scene_id": a.get("ref"), "card": (mnew.group(1) if mnew else val), "create": bool(mnew)}
            r = _tl.tl_shotlist_scene(None, "/api/timeline/%s/shotlist-scene" % sid, body)
            if r.get("ok"):
                a["answered"] = val
                notes.append("%s 这场戏绑到「%s」了。" % (_CIRC[i] if i < len(_CIRC) else i + 1, body["card"]))
            else:
                notes.append("%s 没绑上：%s" % (_CIRC[i] if i < len(_CIRC) else i + 1, r.get("error")))
        elif typ == "length":
            choice = "延长本话" if "延长" in str(val) else "留到下一话"
            r = saga_arrange_length(None, "/api/saga/%s/arrange/length" % sid, {"choice": choice, "ep": ep})
            if r.get("ok"):
                a["answered"] = choice
                notes.append("%s 时长按「%s」处理。" % (_CIRC[i] if i < len(_CIRC) else i + 1, choice))
            else:
                a["answered"] = choice                              # 安排层不可用也别卡住：按用户说的记下，门按 force 过
                force = True
                notes.append("%s 记下「%s」（安排层没法改：%s）。" % (_CIRC[i] if i < len(_CIRC) else i + 1, choice, str(r.get("error"))[:40]))
        elif typ in ("add_shots", "edit_lines"):
            _saga, e = authoring._ep(sid, ep, create=False)
            e = e or {}
            pics = str(e.get("pictures") or "")
            prose = str(e.get("prose") or "")
            arr = _confirmed_arrangement(sid, ep)
            if typ == "add_shots":
                st = str(a.get("ref") or "")
                stages = list(SL.stages(arr)) if arr else []
                btxt = {str(b.get("stage") or ""): str(b.get("text") or "") for b in ((arr or {}).get("beats") or []) if isinstance(b, dict)}
                lines = list(val) if isinstance(val, (list, tuple)) and val else None
                if lines:
                    lines = authoring.filter_beat_lines(lines, prose, pics=pics)      # 模型给的行也不许编台词、不重复台词
                if not lines:
                    sl0 = e.get("shotlist") if isinstance(e.get("shotlist"), dict) else {}
                    people = [str(c.get("name") or "") for c in (sl0.get("cast") or []) if isinstance(c, dict)]
                    lines = authoring.write_beat_shots(st, btxt.get(st, st), prose, pics, people)
                if not lines:
                    notes.append("%s 这件事的镜头没写出来，你直接告诉我拍什么（一两句）。" % (_CIRC[i] if i < len(_CIRC) else i + 1))
                    continue
                pics2, _at = authoring.insert_beat_lines(pics, lines, stages.index(st) if st in stages else 0, arr)
                notes.append("%s 照原文补了 %d 行镜头：%s" % (_CIRC[i] if i < len(_CIRC) else i + 1, len(lines), " / ".join(x[:30] for x in lines)))
            else:
                fr, to, lines = val
                lines = authoring.filter_beat_lines(lines, prose)
                rows = pics.split("\n")
                fr, to = max(1, int(fr)), min(len(rows), int(to))
                if fr > to:
                    notes.append("行号不对（%s-%s），没改。" % (fr, to))
                    continue
                rows[fr - 1:to] = [str(x) for x in lines]
                pics2 = "\n".join(rows)
                notes.append("改了画面稿第 %d-%d 行。" % (fr, to))
            if pics2 != pics:
                authoring._update_ep(sid, ep, pictures=pics2, body=pics2)
                try:
                    p4, _ = authoring.build_shotlist_for(sid, ep, pics2)
                    if isinstance(p4, str) and p4.strip():
                        pics2 = p4
                except Exception as bx:
                    notes.append("（重建拍摄清单失败：%s）" % str(bx)[:60])
                try:
                    sc.stamp_source(sid, ep)
                except Exception:
                    pass
                _dc, _nvc = authoring.apply_slicing_drift(sid, ep)        # P339：改了剧本 → 从变了的段起作废；作废的段出片前由 next_prompts 一次补写
                if _dc:
                    notes.append("改动处在第 %d 段，从这一段起重写提示词（作废 %d 段）。" % (_dc, _nvc))
                pics_changed = True
            if a is not None:
                a["answered"] = "已补镜头" if typ == "add_shots" else "已改剧本"
    return notes, force, pics_changed


def gate_chat(sid, ep, text, n=5, page_asks=None):
    """对话框一条消息 → 解析 → 执行 → 回复。text 空 = 只读状态。返回 {messages, asks, all_clear, force}。
    page_asks：页面看到的 j.blocked——后端没开过对话（提示词没重写成/门崩了这些路径）或清单对不上时，按它开一份（P328c①）。"""
    from cores import authoring
    ep = int(ep or 1)
    _saga, e = authoring._ep(sid, ep, create=False)
    e = e or {}
    ch = _chat_state(e)
    asks = [dict(a) for a in (ch.get("asks") or [])]
    if isinstance(page_asks, list) and page_asks:
        want = [_ask_key(a) for a in page_asks if isinstance(a, dict)]
        have = [_ask_key(a) for a in asks]
        if want and want != have:
            _chat_open(sid, ep, [a for a in page_asks if isinstance(a, dict)])
            _saga, e = authoring._ep(sid, ep, create=False)
            e = e or {}
            ch = _chat_state(e)
            asks = [dict(a) for a in (ch.get("asks") or [])]
    text = str(text or "").strip()

    def _view():
        left = [a for a in asks if not a.get("answered")]
        return {"messages": ch.get("messages") or [], "asks": asks, "left": len(left),
                "all_clear": bool(asks) and not left, "force": bool(ch.get("force"))}

    if not text:
        return _view()
    ch["messages"] = (ch.get("messages") or []) + [{"who": "you", "text": text[:500], "at": time.time()}]
    if not asks:
        ch["messages"].append({"who": "qwen", "text": "现在没有要你定的问题；点「生成」出片就行。", "at": time.time()})
        authoring._update_ep(sid, ep, gate_chat=ch)
        return _view()
    cast, scenes = _chat_names(sid, ep, asks)
    acts = _chat_parse(text, asks, cast, scenes)
    reply = ""
    if not acts:
        # 规则解析不了 → 模型翻成动作
        asks_text = "\n".join("%s [%s] %s（候选：%s）%s" % (_CIRC[i] if i < len(_CIRC) else i + 1, a.get("kind"), a.get("question"), " / ".join(a.get("options") or []), "（已答：%s）" % a["answered"] if a.get("answered") else "")
                              for i, a in enumerate(asks))
        rows = str(e.get("pictures") or "").split("\n")
        pics_num = "\n".join("%d %s" % (k + 1, r) for k, r in enumerate(rows))
        hist = "\n".join("%s：%s" % ("你" if m.get("who") == "you" else "助理", str(m.get("text") or "")[:200]) for m in (ch.get("messages") or [])[-6:-1])
        d = authoring.gate_chat_interpret(asks_text, pics_num, hist, text)
        reply = str(d.get("reply") or "").strip()
        for act in (d.get("actions") or []) if isinstance(d.get("actions"), list) else []:
            if not isinstance(act, dict):
                continue
            typ = str(act.get("type") or "")
            _tok = str(act.get("ask") or "").strip()
            i = _num_of(_tok[:1] if _tok[:1] in _CIRC else _tok) - 1 if _tok else -1
            if typ == "accept_all":
                acts.append(("accept_all", -1, "就按现在的出片"))
            elif typ in ("speaker", "scene", "length") and 0 <= i < len(asks):
                acts.append((typ, i, str(act.get("name") or act.get("card") or act.get("choice") or "")))
            elif typ == "skip" and 0 <= i < len(asks):
                acts.append(("skip", i, "不拍这件事" if asks[i].get("kind") == "beat_missing" else "就按现在的出片"))
            elif typ == "add_shots" and 0 <= i < len(asks):
                acts.append(("add_shots", i, [str(x) for x in (act.get("lines") or []) if str(x).strip()]))
            elif typ == "edit_lines":
                try:
                    acts.append(("edit_lines", -1 if i < 0 else i, (int(act.get("from")), int(act.get("to")), [str(x) for x in (act.get("lines") or [])])))
                except Exception:
                    pass
        if acts and any(t_ == "edit_lines" and ix < 0 for t_, ix, _v in acts):
            # 改行没绑到哪条问题：绑到第一条没答的情节/问题类；一条都没有就只改稿不标答
            first_open = next((k for k, a in enumerate(asks) if not a.get("answered") and a.get("kind") in ("beat_missing", "beat", "event", "problem")), -1)
            acts = [(t_, (first_open if (t_ == "edit_lines" and ix < 0) else ix), v_) for t_, ix, v_ in acts]
    notes, force, _pc = ([], False, False)
    if acts:
        notes, force, _pc = _chat_apply(sid, ep, asks, acts, ready_n=n)
    if force:
        ch["force"] = True
    left = [a for a in asks if not a.get("answered")]
    tail = ""
    if asks and not left:
        tail = "都清了，接着出片。"
    elif left:
        tail = "还剩 %d 条：%s" % (len(left), "；".join("%s %s" % (_CIRC[asks.index(a)] if asks.index(a) < len(_CIRC) else asks.index(a) + 1, str(a.get("question") or "")[:40]) for a in left[:4]))
    if not acts and not reply:
        reply = "没听明白。可以直接说：「这句是苏蔓说的」「②不用拍」「补上」「就按现在的出」，或者告诉我剧本哪几行改成什么。".replace("苏蔓", (cast[0] if cast else "某某"))
    msg = "\n".join(x for x in ([reply] + notes + [tail]) if x)
    ch["messages"].append({"who": "qwen", "text": msg, "at": time.time()})
    ch["asks"] = asks
    authoring._update_ep(sid, ep, gate_chat=ch)
    return _view()


_REBIND_RE = re.compile(r"第(\d+)段应在「([^」]+)」")


def _scene_rebind(detail, scards):
    """出片前门「地点」项的自动修：从 detail 解析「第N段应在「X」」→ (段号, 该绑的场景卡名)。

    X 用 shotlist.place_in_table 归到场景卡名单：归得到返回 (N, 卡名)；归不到返回 (N, "")——
    没有这张卡就不改绑（P263：STORY_164 把「临街窗边」写进 segs["scene"]，提示词按卡名找参考图
    找不到，又绑回店门口，修了等于没修）。detail 不是地点格式返回 (0, "")。
    """
    m = _REBIND_RE.search(str(detail or ""))
    if not m:
        return 0, ""
    from cores import shotlist as SL
    return int(m.group(1)), SL.place_in_table(m.group(2), scards)


def _preflight_gate(sid, j, ready, ep=1, force=False, fix_only=False):
    """出片前检查（P248）。返回 {"blocked": bool, "report": …}。

    确定的自动修（改绑场景 / 建卡 / 重写那几段提示词），最多一轮；
    修完再查一次，还有判不了的就停下问用户，不出片。
    ep：查哪一话（「全部生成」只走第一话；分镜页的「生成下一段」按当前话传）。
    有已确认的安排时多查三项（结尾 / 四段 / 总时长），这三项不靠清单——没清单也查。
    """
    from cores import authoring, shotlist as SL, asset_core as _ac, saga_core as sc
    ep = int(ep or 1)
    j["_sid"], j["_ep"] = sid, ep                                          # P328②：停下时开对话框要知道是哪一话
    sl = authoring.fresh_shotlist(sid, ep)
    arr = _confirmed_arrangement(sid, ep)
    if not sl and not arr:
        _job_event(j, "info", "出片前检查：这一话没有拍摄清单（老项目或清单没抽出来），按原样出片")
        return {"blocked": False}
    # 给检查器的人物卡名单＝cast_cards（安排范围内，和提示词绑卡同口径）：全部卡会把过期卡标成"有卡"，
    # 提示词绑不上又报「没绑」（P269）。_all_cards 只用来判"这张卡已经存在别重建"
    cards = [str(c.get("name") or "") for c in (authoring.cast_cards(sid) or [])]
    _all_cards = [str(c.get("name") or "") for c in (_ac.list_assets(sid, "characters") or [])]
    scards = [str(x.get("name") or "") for x in (_ac.list_assets(sid, "scenes") or [])]

    def _view():
        tl = sc.ep_timeline(sid, ep) or {}
        segs = {int(g.get("no") or 0): g for s2 in (tl.get("scenes") or []) for g in (s2.get("segments") or [])}
        sl_ = _episode_slices(sid, ep)
        out = []
        for i in ready:
            g = segs.get(i) or {}
            s_ = sl_[i - 1] if 0 <= i - 1 < len(sl_) else {}
            out.append({"no": i, "text": s_.get("text") or "",
                        "scene": g.get("scene"), "chars": g.get("chars"), "prompt": g.get("prompt"),
                        "seconds": g.get("seconds") or s_.get("seconds"),
                        # beat 归属来自切片（另一个人加的）；没有就 -1，检查器会报"未检查"
                        "beat": s_.get("beat") or "", "beat_index": SL.beat_index_of(s_)})
        # 安排的三项要看整话切片，不是只看要出的这几段
        slices_v = [{"no": k + 1, "text": s_.get("text") or "", "seconds": s_.get("seconds"),
                     "beat": s_.get("beat") or "", "beat_index": SL.beat_index_of(s_)}
                    for k, s_ in enumerate(sl_)]
        return out, tl, segs, slices_v

    _e1 = (authoring._ep(sid, ep, create=False)[1]) or {}
    pics = str(_e1.get("pictures") or "")
    _prose1 = str(_e1.get("prose") or "")
    # P323b：台词先确定性修——被改写的台词换回原文、原文有画面稿没有的按清单插入；改了就重建清单、相关段重写
    try:
        _people1 = [str(c.get("name") or "") for c in (sl.get("cast") or []) if isinstance(c, dict)]
        _pics2, _n_para = authoring.fix_paraphrased_dialogue(pics, _prose1, _people1)
        _pics3, _n_add = authoring.fix_missing_dialogue(_pics2, _prose1, _people1)
        _n_rm = int(getattr(authoring.fix_missing_dialogue, "last_removed", 0) or 0)
        _pics3, _n_sign = authoring.drop_signage_lines(_pics3, _prose1)     # P327②：屏幕/招牌上的字被写成台词行 → 删
        _n_rm += int(_n_sign or 0)
        if _pics3 != pics:
            authoring._update_ep(sid, ep, pictures=_pics3, body=_pics3)
            _job_event(j, "info", "台词自动修：换回原文 %d 句、补入 %d 句、删掉不是台词的 %d 行" % (_n_para, _n_add, _n_rm))
            try:
                _pics4, _note4 = authoring.build_shotlist_for(sid, ep, _pics3)
                if isinstance(_pics4, str) and _pics4.strip():
                    _pics3 = _pics4                                          # P326c：清单那步可能又按清单改了说话人，用它返回的这份
                _sl_new = authoring.fresh_shotlist(sid, ep)
                if _sl_new:
                    sl = _sl_new
                else:
                    _job_event(j, "error", "台词修后清单没抽出来，沿用旧清单检查")
                    authoring._update_ep(sid, ep, shotlist=sl)
            except Exception as _bx:
                _job_event(j, "error", "台词修后重建清单失败：%s" % str(_bx)[:100])
            pics = _pics3
            try:
                sc.stamp_source(sid, ep)                                     # P326c：画面稿改了要盖快照，不然下次「剧本改过了」全量重写、已出视频全标过期
            except Exception:
                pass
            _d0, _nv0 = authoring.apply_slicing_drift(sid, ep)            # P339：从第一个变了的段起全部作废（后面的段 next_prompts 会补写）
            if _d0:
                _job_event(j, "info", "台词修后切法变了：第 %d 段起作废 %d 段，按新剧本重写" % (_d0, _nv0))
            _hi0 = max([int(x) for x in ready] or [0])
            if _d0 and _hi0 >= _d0:
                # 从 d 一直写到这次要出的最大段（中间作废的段也补上，接力才连得上）；一次调用
                _set_step(j, "台词改了，重写第 %d~%d 段提示词" % (_d0, _hi0))
                _ps0 = authoring.make_h3_prompts(sid, ep, start=_d0 - 1, count=_hi0 - _d0 + 1, prev_tail=_prev_tail_of(sid, ep, _d0)) or []
                if _ps0:
                    authoring.build_timeline(sid, ep, _ps0)
    except Exception as _dx:
        _job_event(j, "error", "台词自动修出错（照旧检查）：%s" % str(_dx)[:100])
    segs_v, tl, segs, slices_v = _view()
    rep = SL.check(sl, pics, segs_v, cards, scards, prose=_prose1, arrangement=arr, slices=slices_v)
    rep = _judge_beats(j, rep) if not fix_only else rep                         # P323c：字面对不上的情节让模型判一次是不是同一件事（P354：只修不问时不问模型）
    for line in SL.summarize(rep):
        _job_event(j, "error" if line.startswith("[有问题]") or line.startswith("[无法判断]") else "info",
                   "出片前检查 " + line)
    if not sl:
        # 没清单：清单那六项没法核，只有安排的三项。有要问的就停，没有就照旧出片
        _job_event(j, "info", "出片前检查：这一话没有拍摄清单，只核对了安排（结尾 / 四段 / 时长）")
        if rep.get("asks") and not fix_only:
            return _blocked_by_asks(j, rep)
        return {"blocked": False, "report": rep}

    # ── P328①：「X 这一段没有镜头」→ 先让模型看前后段是不是已经拍了；真没有的照原文补镜头（改画面稿 → 重建清单 → 盖快照 → 只重写受影响的段 → 再查）──
    try:
        _miss = [it for it in (rep.get("items") or []) if it.get("check") == "情节" and it.get("status") == SL.STATUS_PROBLEM and it.get("fix") == "beat"]
        _miss = []                                          # P353（用户 9-14 定）：不再自动补镜头——它借用后面已有的动作插进前面，制造重复；缺镜头用户自己改提示词
        if _miss and arr:
            _miss = _judge_missing(j, rep, _miss, arr, slices_v)
        if _miss and arr:
            # P328d③：这一话已经补过的段名不再补第二遍（补了还说缺＝检查器的字面对不上，按已补处理）
            _done_b = set(str(x) for x in ((authoring._ep(sid, ep, create=False)[1] or {}).get("beats_added") or []))
            for it in [x for x in _miss if str(x.get("stage") or "") in _done_b]:
                it["status"] = SL.STATUS_PASS
                it["fix"] = ""
                it["detail"] = "「%s」之前已照原文补过镜头" % str(it.get("stage") or "")[:24]
            _miss = [x for x in _miss if str(x.get("stage") or "") not in _done_b]
            if not _miss and not any(x.get("status") == SL.STATUS_PROBLEM for x in (rep.get("items") or [])):
                rep["status"] = SL.STATUS_UNKNOWN if (rep.get("asks") or any(x.get("status") == SL.STATUS_UNKNOWN for x in (rep.get("items") or []))) else SL.STATUS_PASS
        if _miss and arr:
            _stages = list(SL.stages(arr))
            _btxt = {str(b.get("stage") or ""): str(b.get("text") or "") for b in (arr.get("beats") or []) if isinstance(b, dict)}
            _people = [str(c.get("name") or "") for c in (sl.get("cast") or []) if isinstance(c, dict)]
            _pics_b = str((authoring._ep(sid, ep, create=False)[1] or {}).get("pictures") or "")
            _added_b = []
            for it in _miss[:3]:
                _st = str(it.get("stage") or "")
                if _st not in _stages:
                    continue
                _set_step(j, "「%s」没有镜头，照原文补" % _st[:20])
                _ls = authoring.write_beat_shots(_st, _btxt.get(_st, _st), _prose1, _pics_b, _people)
                if not _ls:
                    _job_event(j, "error", "「%s」补镜头没写出来，交给对话框问你" % _st[:24])
                    continue
                _pics_b, _at = authoring.insert_beat_lines(_pics_b, _ls, _stages.index(_st), arr)
                _added_b.append((_st, len(_ls), it))
            if _added_b:
                _prev_done = [str(x) for x in ((authoring._ep(sid, ep, create=False)[1] or {}).get("beats_added") or [])]
                authoring._update_ep(sid, ep, pictures=_pics_b, body=_pics_b, beats_added=_prev_done + [a for a, _b, _it in _added_b])
                for _st, _nl, it in _added_b:                    # P328f④：写盘成功了才把检查项翻成通过
                    it["status"] = SL.STATUS_PASS
                    it["detail"] = "「%s」原来没有镜头，已照原文补了 %d 行" % (_st, _nl)
                    it["fix"] = "done"
                _job_event(j, "info", "自动补镜头：" + "、".join("「%s」%d 行" % (a, b) for a, b, _it in _added_b))
                try:
                    _p4, _ = authoring.build_shotlist_for(sid, ep, _pics_b)
                    if isinstance(_p4, str) and _p4.strip():
                        _pics_b = _p4
                    _sl_b = authoring.fresh_shotlist(sid, ep)
                    if _sl_b:
                        sl = _sl_b
                    else:
                        authoring._update_ep(sid, ep, shotlist=sl)
                except Exception as _bx2:
                    _job_event(j, "error", "补镜头后重建清单失败：%s" % str(_bx2)[:100])
                pics = _pics_b
                try:
                    sc.stamp_source(sid, ep)
                except Exception:
                    pass
                _db, _nvb = authoring.apply_slicing_drift(sid, ep)        # P339：补了镜头切法变了 → 第 _db 段起作废，后面的段 next_prompts 补写
                _hib = max([int(x) for x in ready] or [0])
                if _db:
                    _job_event(j, "info", "补镜头后切法变了：第 %d 段起作废 %d 段，按新剧本重写" % (_db, _nvb))
                if _db and _hib >= _db:
                    _set_step(j, "补了镜头，重写第 %d~%d 段提示词" % (_db, _hib))
                    _psb = authoring.make_h3_prompts(sid, ep, start=_db - 1, count=_hib - _db + 1, prev_tail=_prev_tail_of(sid, ep, _db)) or []
                    if _psb:
                        authoring.build_timeline(sid, ep, _psb)
                segs_v, tl, segs, slices_v = _view()
                rep = SL.check(sl, pics, segs_v, cards, scards, prose=_prose1, arrangement=arr, slices=slices_v)
                rep = _judge_beats(j, rep) if not fix_only else rep
                _job_event(j, "info", "补镜头后再查：%s" % rep.get("status"))
    except JobCancelled:
        raise
    except Exception as _mx:
        _job_event(j, "error", "自动补镜头出错（照旧检查）：%s" % str(_mx)[:100])

    # ── 一轮自动修：只修确定的 ──
    fixed_any, redo = False, set()
    for it in rep.get("items") or []:
        if it.get("fix") != "auto":
            continue
        if it["check"] == "地点":
            # P263：只绑**真有的场景卡**。参考图换了，这段提示词要按新卡重写（redo）——
            # 原来只改 segs["scene"] 不重写，提示词里的 <Picture N> 还是旧场景的图。
            if any(("cut" in _sv or _sv.get("scene_hint")) for _sv in (slices_v or []) if isinstance(_sv, dict)):
                _job_event(j, "info", "节拍段的地点以拍表为准，不按清单改绑：" + str(it.get("detail") or "")[:60])   # P426
                continue
            _n, _card = _scene_rebind(it["detail"], scards)
            if _n and _n in segs:
                if _card:
                    segs[_n]["scene"] = _card
                    fixed_any = True
                    redo.add(_n)
                    _job_event(j, "info", "已改绑：第%d段 → %s" % (_n, _card))
                else:
                    _m = _REBIND_RE.search(it["detail"])
                    _job_event(j, "error", "第%d段要改到「%s」，但没有这张场景卡，不改"
                               % (_n, _m.group(2) if _m else "?"))
        elif it["check"] == "出场人物" and "没绑" in it["detail"]:
            m = __import__("re").search(r"第(\d+)段", it["detail"])
            if m:
                redo.add(int(m.group(1)))
        elif it["check"] == "出场人物" and "禁止别的人" in it["detail"]:
            m = __import__("re").search(r"第(\d+)段", it["detail"])
            if m:
                redo.add(int(m.group(1)))
    if fixed_any:
        sc.save_ep_timeline(sid, ep, tl)
    # 有戏份没卡的人：建卡记录（出不出图由现有"补出缺的人设图"那步决定）
    made = []
    _arr_people = set()
    try:
        _arr_g = authoring.confirmed_arrangement(sid)
        if _arr_g:
            _arr_people = {str(p.get("name") or "").strip() for p in (((_arr_g.get("items") or {}).get("who") or {}).get("people") or []) if isinstance(p, dict)}
    except Exception:
        pass
    for c in SL.needs_card(sl):
        nm = str(c.get("name") or "").strip()
        if not nm or nm in _all_cards:
            continue
        if SL._is_crowd(nm):
            # 动物/群演不建卡（和 check ③b 同口径）：人设图那条链是给人设计的，给猫出图只会出怪东西
            _job_event(j, "info", "%s 按动物/群演处理，不建卡（靠画面稿文字描述）" % nm)
            continue
        _mine = {nm} | {str(x).strip() for x in (c.get("aliases") or []) if str(x).strip()}
        if _arr_people and not (_mine & _arr_people):
            # 安排里没有的人是配角/群演，不建卡（P261：旧清单给猫崽建了两张卡，画面稿因此多出名字）
            _job_event(j, "info", "%s 不在本话安排的人物表里，按配角处理，不建卡" % nm)
            continue
        if _arr_people and nm not in _arr_people:
            # 清单用的是原文叫法、人物表名在别称里：卡按表名建，cast_cards / 安排 / 提示词绑卡才认得（P264）
            nm = sorted(_mine & _arr_people)[0]
            if nm in _all_cards:
                continue
        try:
            _ac.create_character(sid, {"name": nm, "char_type": nm,
                                       "appearance_details": str(c.get("evidence") or "")[:200],
                                       "first_episode": ep})
            made.append(nm)
        except Exception as _ex:
            _job_event(j, "error", "给 %s 建卡失败：%s" % (nm, str(_ex)[:80]))
    if made:
        _job_event(j, "info", "建了人物卡（有戏份但原来没卡）：%s" % "、".join(made))
        try:
            _set_step(j, "补出缺的人设图")
            _r = authoring.gen_char_images(sid, only_missing=True, on_step=lambda m: _set_step(j, m))
            if (_r or {}).get("failed"):
                _job_event(j, "error", "这几个人设图没出来：%s" % "、".join(_r["failed"][:4]))
        except Exception as _ex:
            _job_event(j, "error", "补人设图失败：%s" % str(_ex)[:100])
        # 新卡的人要绑进他在场的那几段：这几段的提示词重写
        for g in segs_v:
            scn, score = SL.scene_for_text(sl, g.get("text"))
            if scn and score and any(SL.name_of_id(sl, x) in made for x in (scn.get("present") or [])):
                redo.add(int(g["no"]))
    if redo:
        _set_step(j, "按清单重写第 %s 段提示词" % "、".join(str(x) for x in sorted(redo)))
        try:
            for i in sorted(redo):
                ps = authoring.make_h3_prompts(sid, ep, start=i - 1, count=1, prev_tail=_prev_tail_of(sid, ep, i)) or []
                if ps:
                    authoring.build_timeline(sid, ep, ps)
        except Exception as _ex:
            _job_event(j, "error", "重写提示词失败：%s" % str(_ex)[:120])
            if not fix_only:
                # P280：提示词没更新成，旧提示词和刚改过的绑定对不上，出片就是错的——停下让人选
                j["blocked"] = [{"kind": "problem", "ref": "",
                                 "question": "第 %s 段的提示词没更新成（%s）。旧提示词和刚改过的场景/人物对不上。"
                                             % ("、".join(str(x) for x in sorted(redo)), str(_ex)[:60]),
                                 "options": ["就按现在的出片", "我去改剧本"]}]
                j["step"] = "需要你确认：提示词没更新成"
                j["running"] = False
                j["updated_at"] = time.time()
                return {"blocked": True, "report": None}
            _job_event(j, "error", "第 %s 段提示词没更新成，照旧出片（P353：只修不问）" % "、".join(str(x) for x in sorted(redo)))
    if fixed_any or made or redo:
        cards = [str(c.get("name") or "") for c in (authoring.cast_cards(sid) or [])]
        segs_v, tl, segs, slices_v = _view()
        rep = SL.check(sl, pics, segs_v, cards, scards, prose=_prose1, arrangement=arr, slices=slices_v)
        rep = _judge_beats(j, rep) if not fix_only else rep
        _job_event(j, "info", "自动修一轮后再查：%s" % rep.get("status"))
        for line in SL.summarize(rep):
            if not line.startswith("[通过]"):
                _job_event(j, "error", "出片前检查 " + line)

    if fix_only:
        # P341：写提示词之前的那一遍——只把剧本修好（台词/招牌/补镜头/建卡），不拦也不问；问的事留给出片那一遍
        return {"blocked": False, "report": rep}
    if force:
        # 用户在页面上选了「就按现在的出片」：把检查结论记下来，照他说的出
        if rep.get("asks") or rep.get("status") == SL.STATUS_PROBLEM:
            _job_event(j, "info", "你选了「就按现在的出片」——检查的问题记在上面，照旧出片")
        return {"blocked": False, "report": rep}
    if rep.get("asks"):
        return _blocked_by_asks(j, rep)
    if rep.get("status") == SL.STATUS_PROBLEM:
        # P280：原来这里只记一条日志就接着烧 GPU——"检查通过"因此没有任何约束力
        return _blocked_by_problems(j, rep)
    return {"blocked": False, "report": rep}


def _first_video_chain(sid, j, n=5, size_tier=None, ep=1, force=False, text_only=False, approved=False):
    ep = int(ep or 1)                                                      # P302：制作第 N 话
    j["ep"] = ep                                                           # P327③：设定页停在出片前门时要知道是哪一话
    _whole = n is None                                                     # P342：n=None → 这一话全部段（门修完剧本段数变了也跟着补）
    """场景图 → 逐段导演 → 装配提示词 → 出片，默认出**前 5 段**
    （2026-08-29 用户定：全部生成一次给够五段）。
    size_tier=None 用默认档（0.4 测试档）；传 "1.0" 出 1376×768 成片档。
    某一段导演三轮不过就停在那儿，前面已出的段照样保留。"""
    import os
    import shutil
    import subprocess
    from cores import saga_core as sc, director, authoring
    from api import segment_api as seg_api
    from models import h3_client as h3
    n = max(1, int(n or 1)) if not _whole else 1
    # 【先补人设图】没有人设图时，提示词末尾照样写着"每个人物与其参考图一致"，
    # 可实际只附了场景参考图——人物长相全靠模型现编，段与段之间必然换脸
    # （2026-08-30 实测 STORY_065：4 张人设卡全无图，成片人脸没有任何锚）。
    # 这条链原来只补场景图，人设图要靠「全部生成」，单点「🎥生视频」就留了这个缺口。
    try:
        from cores import authoring as _au, asset_core as _ac
        if [c for c in _ac.list_assets(sid, "characters") if not c.get("visuals")]:
            _set_step(j, "补出缺的人设图")
            _r = _au.gen_char_images(sid, only_missing=True,
                                     on_step=lambda m: _set_step(j, m))
            if (_r.get("failed") or []):
                _job_event(j, "error", "这几个人设图没出来：%s——成片里他们的长相"
                           "会由模型自己编（可去设定页点「🎭 补出人设图」重试）"
                           % "、".join(_r["failed"][:4]))
    except Exception as ex:
        _job_event(j, "error", "补人设图失败：%s——继续出片，但人物一致性没有锚"
                   % str(ex)[:100])
    # P166①：出片之前先对齐上游——正文改了先重出剧本，剧本改了这一趟必须重拆分镜，
    # 否则就是拿旧剧本的段接着烧片。已有的视频按段号保留，不重烧。
    _fresh = {}
    if approved:
        _job_event(j, "info", "按你认可的剧本和分段出片：不再改剧本、不重分段")      # P462
    else:
        try:
            _rl = []
            _fresh = authoring.refresh_upstream(sid, ep, on_step=lambda m: _set_step(j, m), log=_rl) or {}
            for _m in _rl:
                _job_event(j, "info", _m)
        except Exception as _ex:
            _job_event(j, "error", "对齐上游失败：%s" % str(_ex)[:120])
    _ep_scene_images(sid, ep, j)
    if _whole:
        try:
            n = max(1, authoring.total_segments(sid, ep))
        except Exception:
            n = 1
    j["total"] = int(j.get("total") or 0) + n * 2

    def _segs():
        tl = sc.ep_timeline(sid, ep) or {}
        return {int(g.get("no") or 0): g
                for s2 in (tl.get("scenes") or []) for g in (s2.get("segments") or [])}

    # 【分两批，不交替】先让 Qwen 把 N 段分镜一次导完，再让 H3 连着出 N 段片。
    # 交替跑的话每段都要在 Qwen↔H3 之间换一次模型（N 段换 2N 次），
    # 显存装不下两个模型，换一次就得重新加载（2026-08-29 用户提的）。
    # 【新链路：一次算完所有段，并存盘】旧链路的 direct_next 自己会把段存进
    # saga，后面出片才取得到；新链路只是算出来没存，出片时 _segs() 全是空，
    # 5 段全被跳过 → "一段视频都没出来"（用户 2026-09-01 实测）。
    # 顺便：原来每段都重跑一次 make_h3_prompts，5 段算了 5 遍。
    _new_ps = []
    # 【补齐要能补】原来只在"一段都没有"或"上游变了"时写提示词。
    # 已经出过第 1 段、再点补到 5 段时，这里整个跳过，_new_ps 是空的，
    # 下面 ready 循环拿不到第 2 段就 break，一段新的都不出
    # ——而「点一次补齐到前五段」正是这条路存在的理由（用户 2026-09-01 定，P226 实测失效）。
    _have0 = {k: g for k, g in _segs().items() if str(g.get("prompt") or "").strip()}     # P339：作废的段还留条目、没提示词 → 算没有
    try:
        _can = authoring.total_segments(sid, ep)
    except Exception:
        _can = 0
    _no_prompt = [i for i in range(1, n + 1) if i not in _have0]
    _need_more = bool(_no_prompt) and _can >= _no_prompt[0]
    if not _have0 and _can and not approved:
        # P356：这一话还没写过提示词 → 先把剧本修到终稿（台词/招牌/建卡，只修不问），再切段写提示词（P341 原来挂在按钮一上）；P462 认可过的不修
        try:
            _set_step(j, "写提示词前先把剧本修到终稿")
            _preflight_gate(sid, j, [], ep=ep, fix_only=True)
        except JobCancelled:
            raise
        except Exception as _gx0:
            _job_event(j, "error", "写提示词前的剧本自修出错（照旧写）：%s" % str(_gx0)[:120])
        try:
            _can = authoring.total_segments(sid, ep)
        except Exception:
            pass
        if _whole:
            n = max(1, _can)
    if authoring.use_new_pipeline() and (not _have0 or _fresh.get("shots") or _need_more):
        _set_step(j, "写 %d 段视频提示词" % n)
        try:
            # P342：走 next_prompts——已有的不重写、剧本改了按切法漂移从变了的段起重写（P339），写完自动建表
            _new_nos, _tot = authoring.next_prompts(sid, ep, n, on_step=lambda m: _set_step(j, m))
            _new_ps = [{"n": x} for x in (_new_nos or [])]
            _job_event(j, "info", "视频提示词写好（整话可切 %d 段，这次新写 %d 段，出前 %d 段）" % (int(_tot or 0), len(_new_nos or []), n))
        except Exception as ex:
            _job_event(j, "error", "写视频提示词失败：%s" % str(ex)[:150])

    ready = []
    for i in range(1, n + 1):
        g = _segs().get(i)
        if g and not str(g.get("prompt") or "").strip():
            g = None                                                       # 作废的段：按没有提示词处理
        if not g:
            _set_step(j, "写第 %d 段视频提示词" % i)
            try:
                if authoring.use_new_pipeline():
                    g = _segs().get(i)                                    # P342：next_prompts 已经建表，这里只按段号取
                    _raw = (g or {}).get("prompt") or ""
                else:
                    g, _raw = director.direct_next(sid, ep)
            except Exception as ex:
                _job_event(j, "error", "第 %d 段导演没通过（%s）——先出前 %d 段"
                           % (i, str(ex)[:120], len(ready)))
                break
            if not g:
                # 分清两种：剧本真的只有这么长，还是提示词根本没写出来。
                # 原来一律报「剧本只够分 N 段」——剧本明明能分 23 段时也这么说（P226）。
                if _can > (i - 1):
                    _job_event(j, "error",
                               "第 %d 段没有视频提示词（这一话能分 %d 段）——"
                               "已出的 %d 段保留，请重试或去分镜页单独补这一段"
                               % (i, _can, i - 1))
                else:
                    _job_event(j, "info", "剧本只够分 %d 段" % (i - 1))
                break
        ready.append(i)
        _complete_step(j)
    if not ready:
        raise RuntimeError("一段分镜都没导出来")
    # ── 出片前一道门（P248）：先看这条片子能不能讲清故事，再烧 GPU ──
    try:
        _gate = _preflight_gate(sid, j, ready, ep=ep, force=force, fix_only=True) if not approved else {"blocked": False}   # P353：只修不问，永远不停；P462 认可过的不查
    except JobCancelled:
        raise
    except Exception as _ex:
        _job_event(j, "error", "出片前检查本身出错（照旧出片）：%s" % str(_ex)[:120])
        _gate = {"blocked": False}
    if _gate.get("blocked"):
        return []
    if _whole:
        # P342：门修剧本（补镜头/台词）可能让段数变了 → 补写多出来的段、把作废的段补齐，整话都出
        try:
            _tot2 = max(1, authoring.total_segments(sid, ep))
            _miss2 = [i for i in range(1, _tot2 + 1) if not str((_segs().get(i) or {}).get("prompt") or "").strip()]
            if _miss2:
                _set_step(j, "剧本修后补写第 %d~%d 段提示词" % (_miss2[0], _tot2))
                authoring.next_prompts(sid, ep, _tot2, on_step=lambda m: _set_step(j, m))
            ready = [i for i in range(1, _tot2 + 1) if str((_segs().get(i) or {}).get("prompt") or "").strip()]
            j["total"] = int(j.get("total") or 0) + max(0, len(ready) - n) * 2
            n = len(ready)
        except JobCancelled:
            raise
        except Exception as _ex:
            _job_event(j, "error", "门修后补写提示词失败：%s" % str(_ex)[:150])
    try:
        from cores import episode_ledger as _ledg
        _set_step(j, "记这一话结束时的状态账本")
        _ledg.build(sid, ep)                                                                 # P445③：下一话从这里接
    except Exception as _lx:
        _job_event(j, "error", "状态账本没记成：" + str(_lx)[:120])
    if text_only:
        _job_event(j, "info", "提示词已写好 %d 段（只写文字，没出片）——去 🎬 分镜视频 页看，满意再出片" % len(ready))   # P412
        _set_step(j, "提示词已写好（没出片）")
        return []
    _job_event(j, "info", "分镜就绪 %d 段，开始连续出片（中途不再切模型）" % len(ready))
    # P316：开场段（场景板全景 + 人物亮相）先于第 1 段出；出不来只记日志
    try:
        _n_op, _e_op = seg_api.render_opening_all(sid, ep, size_tier=size_tier, log_fn=lambda m: _set_step(j, m), chk=lambda m: _set_step(j, m))
        if _n_op:
            _job_event(j, "info", "开场段出了 %d 段（场景和人物亮相）" % _n_op)
        if _e_op:
            _job_event(j, "error", _e_op)
    except JobCancelled:
        raise
    except Exception as _ox:
        _job_event(j, "error", "开场段：%s" % str(_ox)[:120])

    outs, _stopped_at = [], 0
    for i in ready:
        g = _segs().get(i)
        if not g:
            continue
        # 断点续跑：这一段已经有片子就跳过，别把前面出好的又烧一遍
        # （2026-08-29 实测：补出第 4、5 段时前 3 段会白重出 30 分钟）。
        # 要重出某一段，去分镜页点那一段的「重新生成」。
        _old = str(g.get("video") or "")
        if _old and os.path.exists(_old) and not g.get("stale_video"):      # P339：按旧剧本出的（stale_video）要重出
            outs.append(_old)
            _set_step(j, "第 %d 段已有视频，跳过" % i)
            _complete_step(j)
            continue
        _set_step(j, "生成第 %d 段视频" % i)
        try:
            patch = seg_api.render_segment_checked(sid, ep, 1, i, size_tier=size_tier,
                                           log_fn=lambda m: _set_step(j, m))
        except Exception as ex:
            _job_event(j, "error", "第 %d 段出片失败：%s——停在这里" % (i, str(ex)[:150]))
            _stopped_at = i
            break
        out = patch["video"]
        outs.append(out)
        _complete_step(j)
        _job_event(j, "info", "第 %d 段视频已生成（%s）：%s"
                   % (i, patch["size"], out))
    if not outs:
        raise RuntimeError("一段视频都没出来")
    _job_event(j, "info", "共出片 %d 段" % len(outs))
    # 【部分失败必须报成失败】原来中途 break 之后照样走到这里 return，
    # 任务状态是「完成」、err 是空的——实测要 5 段只出了 2 段，
    # 调用方看到"完成"就会拿不完整的成片往下走（P230）。
    # 已出的段照旧留在磁盘和分镜表里，这里只是如实报状态。
    if _stopped_at:
        raise RuntimeError("停在第 %d 段：前 %d 段已出并保留，第 %d 段起没有出。"
                           "看任务日志里那条 error；修好之后再点一次「生视频」会从没出的那段接着补"
                           % (_stopped_at, len(outs), _stopped_at))
    return outs


def _timeline_busy(sid):
    """分镜页那边有没有任务在跑（P353：同一话同时只能有一个在写提示词/出片）。"""
    try:
        from api.timeline import _job as _tj
        return bool((_tj(sid) or {}).get("running"))
    except Exception:
        return False


@post(("/api/saga/", "/gen-story"))
def saga_gen_story(h, path, d):
    """「生成故事」＝第一话简介＋原文＋人设卡，**不出人设图**（2026-08-29 拆分）。"""
    sid = path.split("/")[3]
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    if _timeline_busy(sid):
        return _RESP({"ok": False, "error": "分镜页的任务还在跑，等它结束再点"}, 400)
    st = story_core.get_story(sid) or {}
    settings = dict(st.get("settings") or {})
    from cores import manual_story
    _manual = manual_story.enabled(settings)
    if not _manual and _ensure_plan_confirmed(st, settings):
        st = story_core.get_story(sid) or {}
        settings = dict(st.get("settings") or {})
    one = str(d.get("one_line") or settings.get("one_line") or _plan_idea(settings) or "").strip()
    if not one:
        return _RESP({"ok": False, "error": "先在最上面写故事想法并生成整部规划"}, 400)
    if not _manual and d.get("one_line") and d.get("one_line") != settings.get("one_line") and not _plan_idea(settings):
        settings["one_line"] = one
        st["settings"] = settings
        story_core.save_story(st)
    _ep_req = int(d.get("ep") or 1)
    if _manual:
        from cores import saga_core
        e = saga_core.episode(saga_core.get_saga(sid), _ep_req) or {}
        one = str(d.get("brief") or d.get("one_line") or e.get("brief") or "").strip()
        if not one:
            return _RESP({"ok": False, "error": "请填写这一话的剧情"}, 400)
    j = _start_job(sid, "生成故事" if _ep_req == 1 else "第%d话正文" % _ep_req, 4)

    def _run():
        try:
            from cores import universal_writer
            if universal_writer.enabled():
                _run_universal_story(sid, one, settings, j, ep=_ep_req, d_prose_only=bool(d.get("prose_only")))
                return
            from cores import authoring, story_plan as _sp
            # ① 分话框架（话数由内容定），存成结构表；第 N 话生成只认第 N 行（用户 2026-09-06 定：一键出结构）
            _set_step(j, "总编剧：从一句话推分话框架")
            try:
                _fr = _sp.plan_framework(one, settings, n_eps=0)
                _rows = (_fr or {}).get("rows") or []
                if not _rows:
                    # P166③：一行都没推出来＝失败，不是"没关系继续写"
                    _st3 = story_core.get_story(sid) or {}
                    _s3 = _st3.get("settings") or {}
                    _s3["plan_problems"] = ["结构表一行都没解析出来"]
                    _st3["settings"] = _s3
                    story_core.save_story(_st3)
                if _rows:
                    _st2 = story_core.get_story(sid) or {}
                    _s2 = _st2.get("settings") or {}
                    _s2["plan_rows"] = [{"one_line": r["one_line"], "pace": r["pace"], "cast": r.get("cast") or "",
                                         "place": r.get("place") or "", "resistance": r.get("resistance") or "",
                                         "landing": r.get("landing") or "", "geometry": r.get("geometry") or "",
                                         "change": r.get("change") or "", "events": r.get("events") or []} for r in _rows]
                    if _fr.get("tone"):
                        _s2["tone"] = _fr["tone"]                 # 基调（P129）：要素表/大纲按它定代价和调子
                    if _fr.get("people"):
                        _s2["plan_people"] = _fr["people"]        # 人物表（P138）：正文事实单用
                    _st2["settings"] = _s2
                    story_core.save_story(_st2)
                    try:
                        _new_c = authoring.ensure_cards_for_cast(sid, _s2, on_step=lambda m: _set_step(j, m))   # 人设卡随结构表建（P139）
                        if _new_c:
                            _job_event(j, "info", "按结构表新建人物卡：" + "、".join(_new_c))
                    except Exception as _ex:
                        _job_event(j, "error", "按结构表补人物卡失败：" + str(_ex)[:100])
                    _s2["plan_version"] = int(_s2.get("plan_version") or 0) + 1
                    _st2["settings"] = _s2
                    story_core.save_story(_st2)
                    _job_event(j, "info", "故事结构：%d 话，基调 %s（%s）" % (len(_rows), _fr.get("tone") or "未定", "；".join(_fr.get("warnings") or [])[:100]))
                    _hardp = list(_fr.get("hard") or [])
                    _s2["plan_problems"] = _hardp      # 通过时写空表＝清掉上一轮的错（P166③）
                    _st2["settings"] = _s2
                    story_core.save_story(_st2)
                    if _hardp:
                        # P164③：硬问题不是提示，是"这一步没通过"。写进设定，页面要看得见。
                        _job_event(j, "error", "结构没通过（下面各话按现在这版写，可能对不上）：%s"
                                   % "；".join(_hardp)[:200])
                    if _fr.get("problems"):
                        _job_event(j, "info", "结构核对：%s" % "；".join(_fr["problems"])[:160])
            except Exception as _ex:
                # P166③：框架异常不再"按一句话直接写第一话"往下闯——那会拿没校验过的结构
                # 一路生成图和视频。存下问题，这一轮结束。
                _job_event(j, "error", "框架没推出来，这一轮到此为止：%s" % str(_ex)[:150])
                try:
                    _st3 = story_core.get_story(sid) or {}
                    _s3 = _st3.get("settings") or {}
                    _s3["plan_problems"] = ["框架没推出来：" + str(_ex)[:120]]
                    _st3["settings"] = _s3
                    story_core.save_story(_st3)
                except Exception:
                    pass
                _complete_step(j)
                _finish_job(j, "框架没推出来：" + str(_ex)[:120])
                return
            # P166③：结构检查前移。框架跑完＋现有的一轮有限修复之后立刻判，
            # 还有硬伤就存草稿收工——后面的正文、人设卡、人设图、场景图、视频一律不启动。
            _pp0 = list(((story_core.get_story(sid) or {}).get("settings") or {}).get("plan_problems") or [])
            if _pp0:
                _job_event(j, "error", "结构没通过，这一轮到此为止（草稿已保留）：%s。"
                                       "去 ⚙️设定 页把结构表改对，再点一次「生成故事」"
                           % "；".join(_pp0)[:200])
                _complete_step(j)
                _finish_job(j, "结构没通过：" + "；".join(_pp0)[:120])
                return
            _complete_step(j)
            # ② 第一话（简介+剧本）+ 人设卡 + 人设图
            _r = authoring.run_all(sid, one, on_step=lambda m: _set_step(j, m), images=True)
            _cfail = _r.get("cards_failed") or []
            _ifail = _r.get("images_failed") or []
            if _cfail:
                _job_event(j, "error", "这几个人的设定没补全：%s" % "、".join(_cfail))
            if _ifail:
                _job_event(j, "error", "这几个人的设定图没出来：%s" % "、".join(_ifail))
            _complete_step(j)
            # ③ 第一话剧本落到剧本栏 + 场景卡
            _set_step(j, "第一话剧本与场景卡")
            try:
                authoring.make_pictures(sid, 1)
                _sc = authoring.ensure_scenes(sid, 1)
                _job_event(j, "info", "场景卡 %d 个" % len(_sc))
            except Exception as _ex:
                _job_event(j, "error", "剧本/场景卡：%s" % str(_ex)[:120])
            _complete_step(j)
            # ④ 第一话场景图
            _set_step(j, "第一话场景图")
            try:
                _ep_scene_images(sid, 1, j)
            except Exception as _ex:
                _job_event(j, "error", "场景图：%s" % str(_ex)[:120])
            # P164③：结构没通过就不能报「完成」。草稿全留着，但要明说缺什么。
            _pp = list(((story_core.get_story(sid) or {}).get("settings") or {}).get("plan_problems") or [])
            if _pp:
                _job_event(j, "error", "生成完成，但结构没通过，下面这些还缺：%s。草稿都留着，"
                                       "改完结构表再点一次「生成故事」" % "；".join(_pp)[:200])
                _finish_job(j, "结构没通过：" + "；".join(_pp)[:120])
            else:
                _job_event(j, "info", "生成故事完成：结构表＋第一话简介/剧本＋人设卡 %d 张＋人设图＋场景图" % _r.get("characters", 0))
                _finish_job(j)
        except Exception as e:
            _job_event(j, "error", str(e)[:200])
            _finish_job(j, str(e)[:200])

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


def _plan_idea(settings):
    """整部规划里的想法（P298：有规划时它就是一句话故事）。"""
    p = settings.get("story_plan") if isinstance(settings.get("story_plan"), dict) else None
    return str((p or {}).get("idea") or "").strip()


def _complete_cards(sid, j):
    """P321：零模型建的卡只有名字/岁数/外貌一句，发型/服装/长相/身材是空的——出人设图前用一次模型把空栏补齐（只补空的，不盖已有的）。"""
    from cores import authoring, asset_core
    cards = [c for c in (asset_core.list_assets(sid, "characters") or []) if c.get("name")]
    _adopted = {str(v.get("owner_id") or "") for v in (asset_core.list_assets(sid, "visuals") or []) if v.get("status") == "adopted"}
    cards = [c for c in cards if str(c.get("character_id") or "") not in _adopted]            # P326e：已有人设图的卡不再补（图优先于文字衣着）
    need = [c for c in cards if not str(c.get("hair") or "").strip() or not str(c.get("clothing") or "").strip()]
    if not need:
        return []
    st = story_core.get_story(sid) or {}
    settings = dict(st.get("settings") or {})
    _saga = st.get("saga") or {}
    prose = "\n".join(str(e.get("prose") or "") for e in (_saga.get("episodes") or []) if str(e.get("prose") or "").strip())[:6000]
    clue = prose + "\n\n已有人物卡（名字照抄、不加人、不改名；补全每人的发型、服装、长相、身材、识别特征、性格）：\n" + "\n".join(
        "%s｜%s｜%s｜%s岁｜%s" % (c.get("name"), c.get("sex") or "", c.get("char_type") or "", c.get("age") or "", c.get("appearance_details") or "") for c in need)
    _set_step(j, "补全人物卡：" + "、".join(str(c.get("name")) for c in need))
    try:
        designed = authoring.design_characters(clue, settings)
    except Exception as ex:
        _job_event(j, "error", "人物卡补全失败（照旧出图）：" + str(ex)[:120])
        return []
    done = []
    for c in need:
        d = next((x for x in (designed or []) if str((x or {}).get("name") or "").strip() == str(c.get("name")).strip()), None)
        if not d:
            continue
        d = authoring.sanitize_card_fields(dict(d))
        for k_src, k_dst in (("hair", "hair"), ("clothing", "clothing"), ("face_type", "face_type"), ("build", "build"), ("appearance", "appearance_details"),
                             ("personality", "personality"), ("voice", "voice"), ("outfit_preset", "outfit_preset"), ("clothing_requirement", "clothing_requirement")):
            v = str(d.get(k_src) or "").strip()
            if v and not str(c.get(k_dst) or "").strip() or (k_dst in ("face_type", "build") and str(c.get(k_dst) or "") in ("", "自动") and v):
                c[k_dst] = v
        if not str(c.get("behavior_anchor") or "").strip() or str(c.get("behavior_anchor") or "") == "自动":
            c["behavior_anchor"] = str(d.get("behavior_anchor") or d.get("personality") or c.get("personality") or "").strip() or c.get("behavior_anchor")
        asset_core.save_asset(sid, "characters", c)
        done.append(str(c.get("name")))
    if done:
        _job_event(j, "info", "人物卡补全：" + "、".join(done))
    return done


def _whole_plan_write(sid, ep, j, with_script=True, with_prompts=True, n_prompts=None, with_images=True):
    """按已确认的整部规划写第 ep 话：正文 → 人物卡 → 剧本（画面稿）→ 场景卡。全是文字，不出图不出视频（P298）。
    剧本没出来不丢正文：报错说明原因，正文留着。返回 True=正文写成。"""
    from cores import authoring, universal_writer as uw
    j["total"] = 2 if with_script else 1
    _set_step(j, "按已确认的整部规划写第 %d 话正文" % ep)
    try:
        authoring.gen_prose(sid, ep, on_step=lambda message: _set_step(j, message))
    except uw.DraftNotReady:
        _job_event(j, "error", "第 %d 话草稿没通过确定性核对，留在草稿里（不覆盖）" % ep)
        _finish_job(j, "草稿未通过核对")
        return False
    try:
        if authoring.lean_script():
            _new_c = authoring.ensure_cards_from_arrangement(sid)
        else:
            _st_now = story_core.get_story(sid) or {}
            _new_c = authoring.ensure_cards_for_cast(sid, _st_now.get("settings") or {}, on_step=lambda m: _set_step(j, m))
        if _new_c:
            _job_event(j, "info", "建了人物卡：" + "、".join(_new_c))
    except Exception as _ex:
        _job_event(j, "error", "建人物卡失败：" + str(_ex)[:120])
    _complete_step(j)
    _job_event(j, "info", "第 %d 话正文已按整部规划写好" % ep)
    if not with_script:
        return True
    _set_step(j, "编剧把第 %d 话正文改编成剧本" % ep)
    try:
        if authoring.use_new_pipeline():
            authoring.make_pictures(sid, ep, on_step=lambda m: _set_step(j, m))
            try:
                _sc = authoring.ensure_scenes(sid, ep)
                _job_event(j, "info", "场景卡 %d 个：%s" % (len(_sc), "、".join(str(x.get("name") or "") for x in _sc[:4])))
            except Exception as _ex:
                _job_event(j, "error", "场景卡没建成：%s" % str(_ex)[:120])
        else:
            authoring.make_screenplay(sid, ep)
        _complete_step(j)
        _job_event(j, "info", "第 %d 话剧本已出（正文 + 剧本 + 场景卡）" % ep)
    except Exception as _ex:
        _job_event(j, "error", "第 %d 话正文已写好，剧本没出来：%s（正文留着，去剧本页点「按正文生成剧本」再试）" % (ep, str(_ex)[:200]))
        _finish_job(j, "剧本没出来：" + str(_ex)[:120])
        return True
    if with_images:
        # P321：文字这一步就把人物卡补全、出人设图和场景图（用户：生成本话全部文字 = 文字 + 人设图 + 场景图；制作本话只多视频）
        j["total"] = int(j.get("total") or 0) + 2
        try:
            _complete_cards(sid, j)
            from cores import authoring as _au2, asset_core as _ac2
            if [c for c in (_ac2.list_assets(sid, "characters") or []) if not c.get("visuals")]:
                _set_step(j, "出人设图")
                _r = _au2.gen_char_images(sid, only_missing=True, on_step=lambda m: _set_step(j, m))
                if (_r or {}).get("failed"):
                    _job_event(j, "error", "这几个人设图没出来：%s" % "、".join(str(x) for x in _r["failed"][:4]))
            _complete_step(j)
        except Exception as _ex:
            _job_event(j, "error", "人设图没出来：%s" % str(_ex)[:120])
        try:
            _set_step(j, "出第 %d 话场景图" % ep)
            _ep_scene_images(sid, ep, j)
            _complete_step(j)
        except Exception as _ex:
            _job_event(j, "error", "场景图没出来：%s" % str(_ex)[:120])
    if with_prompts:
        # P342（用户 9-14 定）：整话所有段的提示词一次写完（n_prompts=None），切法在这里冻住，后面不再变
        j["total"] = int(j.get("total") or 0) + 2
        try:
            # P341：先把剧本修到终稿（台词换回原文/补漏/删招牌、缺的镜头照原文补、有戏份没卡的建卡），再切段写提示词
            _set_step(j, "写提示词前先把剧本修到终稿")
            _preflight_gate(sid, j, [], ep=ep, fix_only=True)
        except JobCancelled:
            raise
        except Exception as _gx:
            _job_event(j, "error", "写提示词前的剧本自修出错（照旧写）：%s" % str(_gx)[:120])
        _complete_step(j)
        _upto = int(n_prompts) if n_prompts else max(1, authoring.total_segments(sid, ep))
        _set_step(j, "写第 %d 话全部 %d 段的视频提示词" % (ep, _upto) if not n_prompts else "写第 %d 话前 %d 段的视频提示词" % (ep, _upto))
        try:
            _new_ps, _total = authoring.next_prompts(sid, ep, _upto, on_step=lambda m: _set_step(j, m))
            _complete_step(j)
            _job_event(j, "info", "第 %d 话剧本共切 %d 段，提示词已写到第 %d 段（新写 %d 段）" % (ep, int(_total or 0), min(_upto, int(_total or 0)), len(_new_ps or [])))
        except Exception as _ex:
            _job_event(j, "error", "第 %d 话正文和剧本已好，视频提示词没写出来：%s（去分镜页再点「生成下面几段」）" % (ep, str(_ex)[:200]))
            _finish_job(j, "提示词没写出来：" + str(_ex)[:120])
            return True
    return True


def _run_universal_story(sid, one, settings, j, ep=1, d_prose_only=False):
    """Text-only entry: preserve a rejected plan as a draft, then write through the shared path."""
    from cores import authoring, story_plan, universal_writer as uw
    # 单话直写是现在的默认入口：用户给出的这一段就是本话完整剧情，
    # 不先生成整部梗概，也不把剧情切成模型认为的多话。分话功能仍保留，
    # 只有项目明确写 story_mode=split 时才进入下面的旧路径。
    _mode = str((settings or {}).get("story_mode") or "split").strip().lower()
    if _mode in ("single", "episode", "manual"):
        ep = int(ep or 1)
        brief = str(one or "").strip()
        if not brief:
            _job_event(j, "error", "本话想法为空")
            _finish_job(j, "本话想法为空")
            return
        try:
            from cores import manual_story, saga_core
            current = saga_core.episode(saga_core.get_saga(sid), ep) or {}
            manual_story.save_input(sid, ep, brief, current.get("story_pace", "适中"),
                                    current.get("episode_notes", ""), current.get("shooting_notes", ""))
            j["total"] = 1
            _set_step(j, "按你给的本话剧情写正文")
            authoring.gen_prose(sid, ep, on_step=lambda message: _set_step(j, message))
            _complete_step(j)
            _job_event(j, "info", "第 %d 话正文已按本话构想写好；请先阅读，满意后再制作" % ep)
            _finish_job(j)
        except Exception as error:
            _job_event(j, "error", str(error)[:300])
            _finish_job(j, str(error))
        return
    # 【整部规划优先】P286：设定页确认过整部规划 → 直接按它写第 ep 话，跳过重新分话。
    try:
        from cores import whole_plan as WP
        _plan = WP.confirmed(settings)
    except Exception:
        _plan = None
    if _plan:
        ep = int(ep or 1)
        n_eps = len(_plan.get("episodes") or [])
        if ep < 1 or ep > n_eps:
            _job_event(j, "error", "规划里只有 %d 话，没有第 %d 话" % (n_eps, ep))
            _finish_job(j, "没有第 %d 话" % ep)
            return
        _job_event(j, "info", "按整部规划 v%s 写第 %d 话（共 %d 话，跳过重新分话）" % (_plan.get("version"), ep, n_eps))
        try:
            _po = bool(d_prose_only)
            # P342（用户 9-14 定）：按钮一 = 正文 + 剧本 + 人设图 + 场景图 + 整话所有段的视频提示词，只差视频
            # P356（用户 9-14 定）：按钮一 = 正文 + 剧本 + 人设图 + 场景图，不写视频提示词（提示词在按钮二/分镜页出片前写）
            if _whole_plan_write(sid, ep, j, with_script=not _po, with_prompts=False) and j.get("running"):
                _finish_job(j)
        except Exception as error:
            _job_event(j, "error", str(error)[:300])
            _finish_job(j, str(error))
        return
    # 【确认过的本话安排优先】设定页那张卡确认后已经落成 plan_rows[0] + story_design，
    # 再跑一遍分话就把用户确认的东西覆盖了——"确认"等于没确认（P254）。
    _arr = settings.get("arrangement") if isinstance(settings.get("arrangement"), dict) else {}
    if _arr.get("status") == "confirmed":
        # 【安排是契约，先按它重新落盘再写】页面在点「生成故事」前会把设定表存一遍（世界/画风…），
        # 那会改掉"用户依据"的指纹，设计稿被判过期 → 原来就退回重新分话，用户刚确认的卡被模型重排掉
        # （浏览器实测：老板娘/流浪猫 变成了 林姨/灰黑母猫）。确认过的安排不该被设定表的保存作废：
        # 这里按安排把 plan_rows/设计稿/字数重新落一次，指纹自然就对上了。
        try:
            from cores import arrangement as _arm
            _st_c = story_core.get_story(sid) or {}
            _se_c = _st_c.get("settings") or {}
            _arm.confirm(_se_c, _arr)
            _st_c["settings"] = _se_c
            story_core.save_story(_st_c)
            settings = _se_c
        except Exception as _ex:
            _job_event(j, "error", "按安排重新落盘失败：" + str(_ex)[:120])
        j["total"] = 1
        _set_step(j, "按已确认的本话安排写第 1 话")
        _job_event(j, "info", "按已确认的本话安排 v%s 写第 1 话（跳过重新分话）" % _arr.get("version"))
        try:
            authoring.gen_prose(sid, 1, on_step=lambda message: _set_step(j, message))
            try:
                _st_now = story_core.get_story(sid) or {}
                if authoring.lean_script():
                    _new_c = authoring.ensure_cards_from_arrangement(sid)      # P283：零模型，照安排人物表建
                else:
                    _new_c = authoring.ensure_cards_for_cast(sid, _st_now.get("settings") or {},
                                                             on_step=lambda m: _set_step(j, m))
                if _new_c:
                    _job_event(j, "info", "按本话安排建了人物卡：" + "、".join(_new_c))
            except Exception as _ex:
                _job_event(j, "error", "按本话安排建人物卡失败：" + str(_ex)[:120])
            _complete_step(j)
            _job_event(j, "info", "第一话正文已按本话安排写好；请阅读草稿后继续")
            _finish_job(j)
        except Exception as error:
            _job_event(j, "error", str(error)[:300])
            _finish_job(j, str(error))
        return
    def plan_stamp(s):
        return uw.signature({"source": uw.source(s.get("one_line"), s), "rows": s.get("plan_rows"),
                             "version": s.get("plan_version")})
    initial_stamp = plan_stamp(dict(settings, one_line=one))
    j["total"] = 2
    _set_step(j, "先写通完整故事，再安排分话")
    try:
        try:
            planned = story_plan.plan_framework(one, settings, n_eps=0)
        except Exception as error:
            planned = {"rows": [], "hard": ["故事规划未完成：" + str(error)]}
        current = story_core.get_story(sid) or {}
        saved = current.get("settings") or {}
        if plan_stamp(saved) != initial_stamp:
            saved["plan_attempt"] = planned
            current["settings"] = saved
            story_core.save_story(current)
            raise RuntimeError("生成期间故事要求已经修改，本轮结果不覆盖当前设定")
        saved["plan_draft"] = planned
        problems = planned.get("hard") or ([] if planned.get("rows") else ["结构表一行都没解析出来"])
        saved["plan_problems"] = list(problems)
        if problems:
            # 排出来的行照常摆到结构页上，让用户看得见问题在哪、能直接改（P195）。
            # 下游没有放松：plan_problems 有值时 _gen_universal_prose 开头就抛，
            # 「结构没通过，这一轮到此为止」照旧成立。用户改完点「保存结构」即可继续。
            if planned.get("rows"):
                if saved.get("plan_rows"):
                    saved.setdefault("plan_history", []).append(
                        {"at": time.time(), "rows": saved["plan_rows"],
                         "design": saved.get("story_design"), "version": saved.get("plan_version")})
                uw.save_design(saved, planned["rows"], planned.get("design"))
                saved["plan_problems"] = list(problems)   # save_design 不碰它，写回一次防漏
            current["settings"] = saved
            story_core.save_story(current)
            message = ("结构已保留在剧本页，核对修改后点「保存结构」再写正文：" if planned.get("rows")
                       else "未得到可用结构，已有梗概和错误记录保留在剧本页；回设定页重新生成结构：")
            raise RuntimeError(message + "；".join(problems)[:220])
        if saved.get("plan_rows"):
            saved.setdefault("plan_history", []).append({"at": time.time(), "rows": saved["plan_rows"],
                                                         "design": saved.get("story_design"),
                                                         "version": saved.get("plan_version")})
        uw.save_design(saved, planned["rows"], planned.get("design"))
        saved["plan_version"] = int(saved.get("plan_version") or 0) + 1
        saved["plan_problems"] = []
        current["settings"] = saved
        story_core.save_story(current)
        _complete_step(j)
        authoring.gen_prose(sid, 1, on_step=lambda message: _set_step(j, message))
        # 【人设卡随结构表建】这条路原来没有建卡这一步（P250，STORY_157 实测一张卡都没有）。
        # 按结构表在场栏 + 人物表建齐，不出图；出不出图由后面「全部生成/生视频」决定。
        try:
            _st_now = story_core.get_story(sid) or {}
            _new_c = authoring.ensure_cards_for_cast(sid, _st_now.get("settings") or {},
                                                     on_step=lambda m: _set_step(j, m))
            if _new_c:
                _job_event(j, "info", "按结构表建了人物卡：" + "、".join(_new_c))
        except Exception as _ex:
            _job_event(j, "error", "按结构表建人物卡失败：" + str(_ex)[:120])
        _complete_step(j)
        _job_event(j, "info", "完整故事结构与第一话正文已生成；请阅读草稿后继续下一话")
        _finish_job(j)
    except Exception as error:
        _job_event(j, "error", str(error)[:300])
        _finish_job(j, str(error))


@post(("/api/saga/", "/gen-first-video"))
def saga_gen_first_video(h, path, d):
    """「生视频」＝场景图＋分镜＋视频提示词＋视频，默认前 5 段（剧本必须已生成）。
    n 改段数；size_tier 传 "1.0" 出 1376×768 成片档（默认 0.4 测试档）。"""
    from cores import saga_core as sc
    sid = path.split("/")[3]
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    if _timeline_busy(sid):
        return _RESP({"ok": False, "error": "分镜页的任务还在跑，等它结束再点"}, 400)
    e = sc.episode(sc.get_saga(sid), 1) or {}
    if not str(e.get("body") or "").strip():
        return _RESP({"ok": False, "error": "第一话还没有剧本，先点「按原文生成剧本」"}, 400)
    n = int(d.get("n") or 5)
    tier = str(d.get("size_tier") or "").strip() or None
    j = _start_job(sid, "生视频", 1 + n * 2)

    def _run():
        try:
            _first_video_chain(sid, j, n=n, size_tier=tier)
            _finish_job(j)
        except Exception as e:
            _job_event(j, "error", str(e)[:200])
            _finish_job(j, str(e)[:200])

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


@post(("/api/saga/", "/auto-all"))
def saga_auto_all(h, path, d):
    """设定页那个「全部生成」——从一句话跑到**框架和设定就绪**为止。

    【这里不写正文】用户定的流程：一句话 → 框架 → 设定 → 确定设定 → 才写正文。
    正文是整条链最贵的一步，而设定一改它就作废；框架每集只有
    标题/简介/出场人物/地点/钩子，几秒钟就能重来，用它收敛意图才对。

    跑完你在故事页看到的是：整部框架 + 每话集纲 + 人物卡 + 第一话的设定图。
    满意了点「🔒 确定设定」，再去写第一话正文。

    已经填过的东西是**事实**：用户定了人物就围绕这些人写，定了世界就在那个世界里。
    """
    from cores import saga_core as sc, stage1_core, asset_core, story_layer, project_prompt, style_presets
    sid = path.split("/")[3]
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    if _timeline_busy(sid):
        return _RESP({"ok": False, "error": "分镜页的任务还在跑，等它结束再点"}, 400)
    st = story_core.get_story(sid) or {}
    settings = dict(st.get("settings") or {})
    from cores import manual_story
    _manual = manual_story.enabled(settings)
    _auto_write = False
    if _manual:
        try:
            manual_story.require_approved(sid, int(d.get("ep") or 1))
        except ValueError as error:
            if not d.get("auto"):
                return _RESP({"ok": False, "error": str(error)}, 400)
            _auto_write = True                    # P398：一键到底——没正文先写、写完自动确认（在后台任务里做）
    if not _manual and _ensure_plan_confirmed(st, settings):
        st = story_core.get_story(sid) or {}
        settings = dict(st.get("settings") or {})
    one = str(d.get("one_line") or settings.get("one_line") or _plan_idea(settings) or "").strip()
    if not one:
        return _RESP({"ok": False, "error": "先在最上面写故事想法并生成整部规划"}, 400)
    if not _manual and d.get("one_line") and d.get("one_line") != settings.get("one_line") and not _plan_idea(settings):
        settings["one_line"] = one
        st["settings"] = settings
        story_core.save_story(st)
    # 文字链现在只有两步：搭框架 + 整理人物场景名单（正文不在「全部生成」里了）。
    # 图的数量要等名单出来才知道，那时再加进总数。
    _ep_all = int(d.get("ep") or 1)                                        # P302：制作第 N 话（原来只有第一话）
    j = _start_job(sid, "全部生成", 2)

    def _run():
        try:
            # 【新流水线·五角色创作】简介 → 原文(小说家) → 人设(设计师) → 人设图。
            # 框架/集纲/骨架/医生那套已停用；下面那一大段是死代码，保留仅备查。
            from cores import authoring
            if _auto_write:
                _set_step(j, "按你说的写第 %d 话正文" % _ep_all)
                manual_story.write(sid, _ep_all, on_step=lambda m: _set_step(j, m))
                manual_story.approve(sid, _ep_all)
                _job_event(j, "info", "第 %d 话正文写好并已确认，接着往下做" % _ep_all)
            # 【断点续跑】用户 2026-09-01 定：不管跑到哪一步，点一下就补齐，
            # 一路补到前五段视频。已经有的一律跳过，不重做、不覆盖。
            _ep = _ep_all
            _st0 = authoring.pipeline_status(sid, _ep)
            _mode0 = str(((story_core.get_story(sid) or {}).get("settings") or {}).get("story_mode") or "split").lower()
            if _mode0 == "shots":
                # P379 模式二：口述拍法——人物卡/场景卡是用户建的，节拍已冻结；直接写提示词、出片、合成
                _job_event(j, "info", "口述拍法：第 %d 话按冻结的节拍出片" % _ep)
                _n0 = int(d.get("n") or 0) or None
                _tier0 = str(d.get("size_tier") or "").strip() or None
                _outs = _first_video_chain(sid, j, n=_n0, size_tier=_tier0, ep=_ep, force=bool(d.get("force")), text_only=bool(d.get("text_only")), approved=bool(d.get("approved")))
                if _outs:
                    _job_event(j, "info", "第 %d 话出片完成：%d 段" % (_ep, len(_outs)))
                if _outs and not j.get("blocked"):
                    try:
                        from cores import episode_merge as _em
                        _set_step(j, "合成第 %d 话成片" % _ep)
                        _dst = _em.merge_episode(sid, _ep)
                        if _dst:
                            j["merged"] = _dst
                            _job_event(j, "info", "第 %d 话成片：%s" % (_ep, _dst))
                    except Exception as _mx:
                        _job_event(j, "error", "合成成片失败（各段视频都在）：%s" % str(_mx)[:160])
                _finish_job(j)
                return
            _single_mode = _mode0 == "single"
            if _single_mode and _st0["prose"] < 300:
                # 新单话项目缺正文时按当前话输入补写；不回到旧的 run_all 重写入口。
                _st_single = story_core.get_story(sid) or {}
                _se_single = dict(_st_single.get("settings") or {})
                _se_single["story_mode"] = "single"
                _se_single["plan_problems"] = []
                if _ep == 1 and str(one or "").strip():
                    _se_single["one_line"] = str(one).strip()
                _st_single["settings"] = _se_single
                story_core.save_story(_st_single)
                authoring._update_ep(sid, _ep, brief=str(one or "").strip(), brief_edited_by_user=True,
                                     plan_version=0, plan_stale=False)
                _set_step(j, "按你给的本话剧情写正文")
                authoring.gen_prose(sid, _ep, on_step=lambda m: _set_step(j, m))
                _complete_step(j)
                _st0 = authoring.pipeline_status(sid, _ep)
            _job_event(j, "info",
                       "制作第 %d 话，接着上次跑：正文 %d 字｜人物 %d（有图 %d）｜"
                       "场景 %d（有图 %d）｜剧本 %d 字"
                       % (_ep, _st0["prose"], _st0["characters"], _st0["char_images"],
                          _st0["scenes"], _st0["scene_images"], _st0["script"]))
            # P298：有已确认的整部规划 → 第一话正文/剧本按规划写（和「生成故事」同一条路），不走旧的一句话写法
            try:
                from cores import whole_plan as _WP
                _wp = _WP.confirmed((story_core.get_story(sid) or {}).get("settings") or {})
            except Exception:
                _wp = None
            try:
                _se_stale = bool(((authoring._ep(sid, _ep, create=False)[1]) or {}).get("plan_stale"))
            except Exception:
                _se_stale = False
            if (not _single_mode) and _wp and (_st0["prose"] < 300 or _se_stale):                  # 没正文、或规划改过正文过期 → 按规划重写
                if not _whole_plan_write(sid, _ep, j, with_script=True):
                    return
                if not j.get("running"):
                    return
                _st0 = authoring.pipeline_status(sid, _ep)
            elif (not _single_mode) and not _wp and _ep != 1:
                _job_event(j, "error", "没有整部规划时只能制作第 1 话；先在设定页「规划整个故事」")
                _finish_job(j, "没有整部规划")
                return
            if _st0["prose"] >= 300 and _st0["characters"] > 0:
                _job_event(j, "info", "正文和人物卡已有，跳过不重做")
                _r = {"characters": _st0["characters"], "images_failed": [],
                      "cards_failed": []}
                if _st0["char_images"] < _st0["characters"]:
                    _set_step(j, "补出缺的人设图")
                    try:
                        # P335：只补**没有已采用人设图**的人（gen_char_images 按 visual 表判）。
                        # 原来按 card.visuals 判——这个字段从来没人写，结果每次都把所有人的脸重出一遍，
                        # 第 2 话起人物和第 1 话的片子对不上（197 实测）
                        _gr = authoring.gen_char_images(
                            sid, only_missing=True,
                            on_step=lambda m: _set_step(j, m)) or {}
                        _r["images_failed"] = ((_gr.get("failed") or [])
                                               if isinstance(_gr, dict)
                                               else list(_gr))
                        _job_event(j, "info", "人设图补了 %d 张"
                                   % (_gr.get("ok", 0)
                                      if isinstance(_gr, dict) else 0))
                    except Exception as _ex:
                        _job_event(j, "error", "补人设图失败：%s" % str(_ex)[:150])
            elif _single_mode and _st0["prose"] >= 300:
                # 单话项目正文已经由用户确认的入口生成；这里只补人物卡，不能再次创作正文。
                _set_step(j, "从本话正文整理人物卡")
                _st_now = story_core.get_story(sid) or {}
                _ss = _st_now.get("settings") or {}
                _prose_now = str((authoring._ep(sid, _ep, create=False)[1] or {}).get("prose") or "")
                _designed = authoring.design_characters(_prose_now, _ss)
                _designed, _dropped = authoring._ground_cards(_designed, _prose_now, _ss)
                if int(_ep or 1) > 1:
                    # P445②：续话——已有的人不重建，新人只认口述里点了名的（写手起的名字不建卡）
                    from cores import asset_core as _ac445
                    _have445 = {str(c.get("name") or "") for c in (_ac445.list_assets(sid, "characters") or [])}
                    _brief445 = str((authoring._ep(sid, _ep, create=False)[1] or {}).get("brief") or "")
                    _designed = [c for c in (_designed or []) if isinstance(c, dict) and str(c.get("name") or "") not in _have445
                                 and str(c.get("name") or "") and str(c.get("name")) in _brief445]
                _cards = authoring._create_cards(sid, _designed) if _designed else []
                _complete_step(j)
                _r = {"characters": len(_cards or []), "images_failed": [], "cards_failed": []}
            else:
                _r = authoring.run_all(sid, one, on_step=lambda m: _set_step(j, m))
            _fail = _r.get("images_failed") or []
            _job_event(j, "info", "第一话原文和人物就绪，人物 %d 个"
                       % _r.get("characters", 0))
            _cfail = _r.get("cards_failed") or []
            if _cfail:
                _job_event(j, "error", "这几个人的设定没补全：%s。去设定页点「🎭 补出人设图」前先手动补，或重跑一次"
                           % "、".join(_cfail))
            if _fail:
                # 谁没出图要说清楚，别让用户对着少一张图猜（补图按钮在设定页）
                _job_event(j, "error", "这几个人的设定图没出来：%s。去设定页点「🎭 补出人设图」再试"
                           % "、".join(_fail))
            # 「全部生成」走到底（2026-08-29 用户定）：剧本 → 场景图 → 前 5 段视频
            j["total"] = int(j.get("total") or 0) + 1
            _st_now = authoring.pipeline_status(sid, _ep)
            if _st_now["script"] >= 200 and d.get("approved"):
                _job_event(j, "info", "剧本和分段已认可，按这份出")                                    # P462
            elif _st_now["script"] >= 200 and _st_now.get("script_stale"):
                # P166①：旧剧本不能直接拿去出片。先按新正文重出（旧的进历史版本留着）
                _set_step(j, "正文改过了，先重出剧本")
                _rl = []
                authoring.refresh_upstream(sid, _ep, on_step=lambda m: _set_step(j, m), log=_rl)
                _job_event(j, "info", "；".join(_rl) or "上游已对齐")
            elif _st_now["script"] >= 200:
                _job_event(j, "info", "剧本已有且和正文一致，跳过")
            elif authoring.use_new_pipeline():
                _set_step(j, "编剧把第 %d 话原文改编成剧本" % _ep)
                authoring.make_pictures(sid, _ep)
                # 【场景卡在这里建】这条"全部生成"路的人物卡来自一键立项，
                # 场景卡以前没人建——场景图因此没有数据来源，
                # 视频背景出的是人设图的纯白影棚底（用户 2026-09-01 实测）。
                # 放在画面稿之后：那时地点和空间描写最全。
                try:
                    _sc = authoring.ensure_scenes(sid, _ep)
                    _job_event(j, "info", "场景卡 %d 个：%s"
                               % (len(_sc), "、".join(
                                   str(x.get("name") or "") for x in _sc[:4])))
                except Exception as _ex:
                    _job_event(j, "error", "场景卡没建成：%s" % str(_ex)[:120])
            elif not authoring.use_new_pipeline():
                _set_step(j, "编剧把第 %d 话原文改编成剧本" % _ep)
                authoring.make_screenplay(sid, _ep)
            _complete_step(j)
            # P342（用户 9-14 定）：按钮二 = 这一话全部段出片；出完合成一条
            _n = int(d.get("n") or 0) or None
            _tier = str(d.get("size_tier") or "").strip() or None
            _outs = _first_video_chain(sid, j, n=_n, size_tier=_tier, ep=_ep, force=bool(d.get("force")), text_only=bool(d.get("text_only")), approved=bool(d.get("approved")))
            if _outs:
                _job_event(j, "info", "第 %d 话出片完成：%d 段" % (_ep, len(_outs)))
            if _outs and not j.get("blocked"):
                try:
                    from cores import episode_merge as _em
                    _set_step(j, "合成第 %d 话成片" % _ep)
                    _dst = _em.merge_episode(sid, _ep)
                    if _dst:
                        j["merged"] = _dst
                        _job_event(j, "info", "第 %d 话成片：%s" % (_ep, _dst))
                except Exception as _mx:
                    _job_event(j, "error", "合成成片失败（各段视频都在）：%s" % str(_mx)[:160])
            _finish_job(j)
            return
            # ↓↓↓ 以下旧「整部框架」逻辑不再执行（保留备查）
            _set_step(j, "搭整部故事框架")
            settings["_characters"] = asset_core.list_assets(sid, "characters")
            got = sc.build_bible(one, settings, None)
            saga = sc.get_saga(sid)
            saga["bible"] = got.get("bible") or {}
            saga["seasons"] = got.get("seasons") or []
            saga["episodes"] = got.get("episodes") or []
            sc.save_saga(sid, saga)
            if got.get("_issues"):
                _job_event(j, "info", "框架有几处没写全：" + str(got["_issues"])[:200])
            _job_event(j, "info", "框架完成：%d 季 %d 集"
                       % (len(saga["seasons"]), len(saga["episodes"])))
            # 题材引擎在这一步定下来（打分直判，判不出才问一次模型），
            # 写回设定供页面显示，骨架和正文两步都会吃到对应的节拍形状。
            try:
                from cores import story_gen as _sg
                if not str(settings.get("genre_engine") or "").strip():
                    from cores import kits as _kt
                    _gs = _kt.genre_spec(settings.get("genre") or "")
                    if _gs.get("engine"):
                        _eng, _why = _gs["engine"], "按影片类型「%s」定" % _kt.genre_group(settings.get("genre") or "")
                    else:
                        _eng, _why = _sg.detect_engine(one, settings, None)
                    settings["genre_engine"] = _eng
                    settings["genre_engine_reason"] = _why
                    _st = story_core.get_story(sid)
                    _st.setdefault("settings", {})["genre_engine"] = _eng
                    _st["settings"]["genre_engine_reason"] = _why
                    story_core.save_story(_st)
                    _job_event(j, "info", "题材引擎：%s（%s）" % (_eng, _why))
            except Exception:
                pass
            _complete_step(j)

            # 1.5) 第一话故事扩写：框架给每话只有一两句梗概，看不出人物是谁、
            # 这话讲什么。这里单独把第一话扩成 300~600 字的完整故事，
            # 用户在设定页能读懂、能自己改，改完再往下生成剧本。
            _set_step(j, "写第一话故事")
            try:
                _saga2 = sc.get_saga(sid)
                _got = sc.make_episode_brief(_saga2, 1)
                _e1 = sc.episode(_saga2, 1)
                if _e1 and _got:
                    for _k in ("title", "logline", "brief", "hook"):
                        if _got.get(_k):
                            _e1[_k] = _got[_k]
                    sc.save_saga(sid, _saga2)
                    _job_event(j, "info", "第一话故事已写（%d字）" % len(_got.get("brief") or ""))
            except Exception as ex:
                _job_event(j, "error", "第一话故事扩写失败（用框架梗概）：" + str(ex)[:120])
            _complete_step(j)

            # 2) 【框架先行】这里**不写正文**。
            # 用户定的流程：一句话 → 框架 → 设定 → 确认冻结 → 才写正文。
            # 理由：正文是整条链上最贵的一步（初稿+医生+终稿三轮模型调用），
            # 而设定一改它就作废。框架每集只有标题/简介/出场人物/地点/钩子，
            # 几百字、几秒钟就能重来，用它来收敛意图才对。
            # 而且人物设定本该是**输入**——从框架抽，不从正文抽。
            _set_step(j, "整理人物和场景名单")
            # 重跑判重：上一次自动建的卡（用户没手改过的）先清掉，
            # 否则同一角色换个名字就又建一份。用户改过的一律保留——这是事实。
            for old_c in asset_core.list_assets(sid, "characters"):
                if not old_c.get("edited_by_user"):
                    try:
                        asset_core.delete_asset(sid, "characters", old_c["character_id"])
                    except Exception:
                        pass
            for old_s in asset_core.list_assets(sid, "scenes"):
                if not old_s.get("edited_by_user"):
                    try:
                        asset_core.delete_asset(sid, "scenes", old_s["scene_id"])
                    except Exception:
                        pass
            saga = sc.get_saga(sid)
            try:
                setup = stage1_core.extract_setup_from_framework(saga, settings, None)
            except Exception as ex:
                chars_r, places_r = stage1_core.roster_from_saga(saga)
                setup = stage1_core._fallback_setup(chars_r, places_r, settings)
                for c in setup.get("characters") or []:
                    c["first_episode"] = chars_r.get(c["name"], 1)
                for x in setup.get("scenes") or []:
                    x["episode"] = places_r.get(x["name"], 1)
                setup["_issues"] = "自动补人物/场景细节失败：%s" % str(ex)[:160]
            if setup.get("_issues"):
                _job_event(j, "error", str(setup["_issues"])[:220])
            _job_event(j, "info", "本次名单：人物 %d 个、场景 %d 个"
                       % (len(setup.get("characters") or []), len(setup.get("scenes") or [])))
            if setup.get("_later_places"):
                _job_event(j, "info", "后面话次还有 %d 个地点（%s…），"
                                      "写到那一话时在故事页点「生成本话场景」补"
                           % (len(setup["_later_places"]), "、".join(setup["_later_places"][:3])))
            world = setup.get("world_type") or ""
            st2 = story_core.get_story(sid)
            changed_settings = False
            if world and (not settings.get("world_type")
                          or settings.get("world_type") == "自动判断"):
                st2.setdefault("settings", {})["world_type"] = world
                changed_settings = True
            generated_look = str(setup.get("look") or project_prompt.default_look(settings)).strip()
            look_panel = st2.setdefault("settings", {}).setdefault("look_panel", {})
            if generated_look and not str(look_panel.get("look") or "").strip():
                look_panel["look"] = generated_look
                settings.setdefault("look_panel", {})["look"] = generated_look
                changed_settings = True
            if changed_settings:
                story_core.save_story(st2)
            have_c = {c.get("name"): c for c in asset_core.list_assets(sid, "characters")}
            # 六维五官在建卡时一次定死并存进卡里：项目内互相错开，
            # 之后重新生成设定图不会变脸，用户也能在卡上看见和修改。
            face_taken = set()
            for _old in have_c.values():
                face_taken |= project_prompt.face_signature(_old.get("face_features") or {})
            for c in setup.get("characters") or []:
                if c.get("name") in have_c:
                    continue            # 用户已有的人物卡是事实，不动
                fields = {"name": c.get("name"), "age": c.get("age"), "sex": c.get("sex"),
                          "char_type": c.get("char_type"), "face_type": c.get("face_type"),
                          "build": c.get("build"), "behavior_anchor": c.get("behavior_anchor"),
                          "appearance_details": c.get("appearance_details"), "hair": c.get("hair"),
                           "clothing_requirement": c.get("clothing_requirement"),
                           "outfit_preset": c.get("outfit_preset"),
                           "clothing": c.get("clothing"),
                          "identity_anchor": c.get("identity_anchor"),
                          "beauty_tier": c.get("beauty_tier"),
                          "outfit_tier": c.get("outfit_tier"), "role": c.get("role"),
                          # 连载故事里人物是陆续进场的。没有这个字段，
                          # 系统就分不出"第八话才出现的反派"和"第一话的主角"，
                          # 于是第一话还没写就把所有人的设定图都画了。
                          "first_episode": c.get("first_episode") or 1,
                          "auto_made": True}
                _feat = project_prompt.face_features(
                    dict(c, sex=c.get("sex") or authoring.card_sex(c) or "女"), settings, face_taken,
                    seed=str(settings.get("one_line") or "")[:24])
                face_taken |= project_prompt.face_signature(_feat)
                fields["face_features"] = _feat
                fields = {k: (stage1_core.clean_field(v) if k != "auto_made" else v)
                          for k, v in fields.items()}
                # 年龄底线只在勾了成人向时才生效；没勾就按故事里写的实际年龄走。
                if style_presets.requires_adult_cast(settings):
                    try:
                        if fields.get("age") and int(str(fields["age"]).rstrip("岁")) < 18:
                            fields["age"] = "18"
                    except Exception:
                        pass
                made = asset_core.create_character(sid, fields)
                extra = {k: v for k, v in fields.items() if v and k not in (made or {})}
                if made and extra:
                    made.update(extra)
                    asset_core.save_asset(sid, "characters", made)
            have_s = {x.get("name"): x for x in asset_core.list_assets(sid, "scenes")}
            for s2 in setup.get("scenes") or []:
                if s2.get("name") in have_s:
                    continue
                fields = {"name": s2.get("name"), "space": s2.get("space"),
                          "contract_text": s2.get("space"), "scale": s2.get("scale"),
                          "depth": s2.get("depth"), "materials": s2.get("materials"),
                          "main_light": s2.get("main_light"), "time": s2.get("time"),
                          "landmarks": s2.get("landmarks"),
                          "episode": s2.get("episode") or 1,
                          "auto_made": True}
                fields = {k: (stage1_core.clean_field(v, scene_field=True)
                              if k not in ("episode", "auto_made") else v)
                          for k, v in fields.items()}
                card = asset_core.create_scene(sid, fields)
                if card:
                    extra = {k: v for k, v in fields.items() if v and k not in card}
                    if extra:
                        card.update(extra)
                        asset_core.save_asset(sid, "scenes", card)
            _complete_step(j)

            # 4) 设定图：只画第一话用得上的
            # 【卡早建、图晚画】卡是文字，几乎不花成本，全部建好你才能提前调整、
            # 才能保证名字前后一致；图是真金白银的 GPU 时间，按话画。
            # 第八话才登场的反派，没必要在你还没写第二话的时候就占一次显卡。
            style = str(settings.get("style") or "电影级剧照")
            done_ids = {v.get("owner_id") for v in asset_core.list_assets(sid, "visuals")
                        if v.get("status") == "adopted"}
            EP = 1
            pending_characters = [c for c in asset_core.list_assets(sid, "characters")
                                  if c.get("character_id") not in done_ids
                                  and int(c.get("first_episode") or 1) <= EP]
            later_c = len([c for c in asset_core.list_assets(sid, "characters")
                           if int(c.get("first_episode") or 1) > EP])
            if later_c:
                _job_event(j, "info", "另有 %d 个人物在后面的话次才登场，"
                                      "卡已建好，写到那一话再画图" % later_c)
            j["total"] = int(j.get("total") or 0) + len(pending_characters)
            # 【三步走 · 第一步只到这里】用户定的顺序：
            #   1）一句话 → 故事框架 + 第一话故事 + 人物设定图 —— 确认无误
            #   2）第一话剧本 + 场景图（场景图要含人物、按剧情来，所以必须先有剧本）
            #   3）镜头表 + 视频提示词 + 视频
            # 所以"全部生成"只出到人物设定图为止。基调图已废（用户定：不要），
            # 空场场景图也废（用户定：场景图要含人物，归第二步）。
            for c in pending_characters:
                _set_step(j, "画人物设定图：" + str(c.get("name")))
                note = ""
                try:
                    v = asset_core.generate_character_master(sid, c["character_id"],
                                                             style_profile=style)
                    if isinstance(v, tuple):
                        v = v[0]
                    if v and v.get("visual_id"):
                        asset_core.adopt(sid, v["visual_id"])
                except Exception as ex:
                    note = "跳过：%s" % str(ex)[:80]
                    _job_event(j, "error", "人物图失败（%s）：%s" % (c.get("name"), str(ex)[:160]))
                _complete_step(j, note)
            _job_event(j, "info", "第一步完成：故事框架 + 第一话故事 + 人物设定图都好了。"
                                  "确认无误后，去剧本页生成第一话剧本和场景图")
            _finish_job(j)
        except Exception as e:
            # 【任务永不中断】用户定的死规矩。走到这里说明出了预料之外的问题，
            # 但已经做完的部分（框架、人物卡、场景卡、画好的图）都已经落盘，
            # 所以标成"完成"而不是"失败"——让用户看得见成果、能接着改，
            # 而不是对着一句报错和几分钟的白等发呆。
            _job_event(j, "error", "有一步没跑通：%s" % str(e)[:200])
            _job_event(j, "info", "已完成的部分都保留了，去 📖 故事 和 ⚙️ 设定 看看，缺什么可以单独补")
            try:
                _finish_job(j)
            except Exception:
                _finish_job(j, e)

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


@post(("/api/saga/", "/extract-cards"))
def saga_extract_cards(h, path, d):
    """【自定义作品】从第一话原文提取人物卡——不写正文、不出图。
    已有的同名卡原样保留，只补缺的（2026-08-29 用户定）。"""
    from cores import authoring
    sid = path.split("/")[3]
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    j = _start_job(sid, "提取人物卡", 1)

    def _run():
        try:
            r = authoring.extract_cards(sid, on_step=lambda m: _set_step(j, m))
            _job_event(j, "info", "人物卡：新建 %d 张（%s），保留 %d 张"
                       % (len(r.get("added") or []), "、".join(r.get("added") or []) or "无",
                          len(r.get("kept") or [])))
            _finish_job(j)
        except Exception as e:
            _job_event(j, "error", str(e)[:200])
            _finish_job(j, str(e)[:200])

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


@post(("/api/saga/", "/scene-cards"))
def saga_scene_cards(h, path, d):
    """按第 ep 话剧本建/更新场景卡（纯文字，不出图）——自定义作品要靠它
    拿到场景槽位，才能把自己的场景图挂上去（2026-08-29）。"""
    from cores import saga_core as sc, scene_layer, project_settings as _ps
    sid = path.split("/")[3]
    no = int(d.get("ep") or d.get("no") or 1)
    e = sc.episode(sc.get_saga(sid), no) or {}
    body = str(e.get("body") or "")
    if not body.strip():
        return _RESP({"ok": False, "error": "第%d话还没有剧本，先生成剧本" % no}, 400)
    st = story_core.get_story(sid) or {}
    settings = dict(st.get("settings") or {})
    settings.update(_ps.get(st))
    try:
        r = _sync_scene_cards_from_body(sid, no, e, body, settings)
    except Exception as ex:
        return _RESP({"ok": False, "error": str(ex)[:200]}, 500)
    return _RESP({"ok": True, "data": r})


def _sync_scene_cards_from_body(sid, no, e, body, settings):
    """剧本 → 场景卡（没有骨架时用剧本场景头合成一份）。出图链和自定义
    板块共用这段（2026-08-29 抽出，原来只藏在场景图任务里）。"""
    from cores import asset_core, scene_layer
    heads = scene_layer._scene_headers(body)
    sk = e.get("skeleton") or {}
    if not (sk.get("scenes")):
        sk = {"scenes": [{"no": hd["no"], "location": hd.get("location") or "",
                          "interior": hd.get("interior") or "", "scale": ""}
                         for hd in heads if (hd.get("location") or "").strip()]}
    r = scene_layer.sync_scene_cards(sid, sk, body, no, settings) or {}
    r["scenes"] = [str(c.get("name") or "")
                   for c in asset_core.list_assets(sid, "scenes") if c.get("name")]
    return r


@post(("/api/saga/", "/ai-expand"))
def saga_ai_expand(h, path, d):
    """AI 扩写简介或正文（只扩不改）。kind: 简介 / 正文。"""
    from cores import authoring, saga_core as sc
    sid = path.split("/")[3]
    kind = "简介" if str(d.get("kind") or "").strip() == "简介" else "正文"
    text = str(d.get("text") or "").strip()
    if not text:
        return _RESP({"ok": False, "error": "这一栏还是空的，先写点东西再让 AI 扩写"}, 400)
    settings = (story_core.get_story(sid) or {}).get("settings") or {}
    ep = int(d.get("ep") or 1)
    try:
        expanded = authoring.ai_expand(kind, text, settings)
    except Exception as e:
        return _RESP({"ok": False, "error": str(e)[:200]}, 500)
    saga = sc.get_saga(sid)
    e1 = sc.episode(saga, ep)
    if e1 is not None:
        e1["brief" if kind == "简介" else "prose"] = expanded
        sc.save_saga(sid, saga)
    return _RESP({"ok": True, "data": {"expanded": expanded, "ep": ep}})


@post(("/api/saga/", "/make-screenplay"))
def saga_make_screenplay(h, path, d):
    """按第 ep 话原文生成剧本（编剧），存进该话 body。"""
    from cores import authoring
    from cores import saga_core as _sc, project_settings as _ps2
    sid = path.split("/")[3]
    ep = int(d.get("ep") or 1)
    try:
        # 【新链路 2026-09-01】正文 → 画面稿 → H3 提示词，替掉
        # 旧的"正文→剧本→导演分镜"。四段视频实测新版胜出，链路从五层缩到三层。
        # V41_PIPELINE=old 可切回旧链路（出问题的退路）。
        if authoring.use_new_pipeline():
            authoring.extract_cards(sid)
            r = authoring.make_pictures(sid, ep)
        else:
            r = authoring.make_screenplay(sid, ep)
    except Exception as e:
        return _RESP({"ok": False, "error": str(e)[:200]}, 500)
    # 剧本一出就把场景卡建好（纯文字零 GPU）——自带场景图的用户这时就能
    # 把自己的图挂上去，不必先跑一轮自动出图（2026-08-29 用户定）
    try:
        _st2 = story_core.get_story(sid) or {}
        _set2 = dict(_st2.get("settings") or {})
        _set2.update(_ps2.get(_st2))
        _e2 = _sc.episode(_sc.get_saga(sid), ep) or {}
        if authoring.plan_places(sid, ep):              # 结构表定了地点：按地点建卡，不从剧本抠碎片（P131）
            _sr = {"scenes": [str(c.get("name") or "") for c in authoring.ensure_scenes(sid, ep)]}
        else:
            _sr = _sync_scene_cards_from_body(sid, ep, _e2, str(_e2.get("body") or ""), _set2)
        r = dict(r or {})
        r["scenes"] = _sr.get("scenes") or []
    except Exception:
        pass
    return _RESP({"ok": True, "data": r})


@post(("/api/saga/", "/redesign-character"))
def saga_redesign_character(h, path, d):
    """P357：把一个人完全重新设计（名字/性别/年龄不变）并重出人设图。后台跑，进度走 /progress。"""
    from cores import authoring
    sid = path.split("/")[3]
    cid = str(d.get("character_id") or "").strip()
    if not cid:
        return _RESP({"ok": False, "error": "缺 character_id"}, 400)
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    if _timeline_busy(sid):
        return _RESP({"ok": False, "error": "分镜页的任务还在跑，等它结束再点"}, 400)
    j = _start_job(sid, "重新设计人物", 2)

    def _run():
        try:
            r = authoring.redesign_character(sid, cid, on_step=lambda m: _set_step(j, m),
                                             brief=str(d.get("brief") or ""), with_image=True)      # P400：按框内文字设计并直接出图采用
            _job_event(j, "info", "重新设计完成：%s（卡已按你写的更新，设定图已重出并采用）" % str((r.get("card") or {}).get("name") or cid))
            _finish_job(j)
        except Exception as ex:
            _job_event(j, "error", str(ex)[:200])
            _finish_job(j, str(ex)[:120])

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


@post(("/api/saga/", "/gen-char-images"))
def saga_gen_char_images(h, path, d):
    """补出人设图：只给还没有图的人物出（偶发失败后用，不用重跑整个故事）。
    all=1 时所有人物重出。后台跑。"""
    from cores import authoring
    sid = path.split("/")[3]
    only_missing = not d.get("all")
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    j = _start_job(sid, "补出人设图", 1)

    def _run():
        try:
            r = authoring.gen_char_images(sid, only_missing=only_missing,
                                          on_step=lambda m: _set_step(j, m))
            fail = r.get("failed") or []
            _job_event(j, "info", r.get("note") or ("人设图完成 %d/%d" %
                       (r.get("ok", 0), r.get("total", 0))))
            if fail:
                _job_event(j, "error", "这几个还是没出来：" + "、".join(fail))
            _finish_job(j)
        except Exception as e:
            _job_event(j, "error", str(e)[:200])
            try:
                _finish_job(j)
            except Exception:
                _finish_job(j, e)

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


@post(("/api/saga/", "/add-episode"))
def saga_add_episode(h, path, d):
    """＋增加一话：只建一个空话（简介/原文/剧本都空），返回新话号。"""
    from cores import authoring
    sid = path.split("/")[3]
    try:
        no = authoring.add_episode(sid)
    except Exception as e:
        return _RESP({"ok": False, "error": str(e)[:200]}, 500)
    return _RESP({"ok": True, "data": {"no": no}})


@post(("/api/saga/", "/gen-brief"))
def saga_gen_brief(h, path, d):
    """按前情生成第 ep 话的简介。后台跑（要调 Qwen）。"""
    from cores import authoring
    sid = path.split("/")[3]
    ep = int(d.get("ep") or 1)
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    j = _start_job(sid, "第%d话简介" % ep, 1)

    def _run():
        try:
            authoring.gen_brief(sid, ep, on_step=lambda m: _set_step(j, m))
            _job_event(j, "info", "第%d话简介已生成" % ep)
            _finish_job(j)
        except Exception as e:
            _job_event(j, "error", str(e)[:200])
            try:
                _finish_job(j)
            except Exception:
                _finish_job(j, e)

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True, "ep": ep}})


@post(("/api/saga/", "/gen-prose"))
def saga_gen_prose(h, path, d):
    """按第 ep 话的简介生成这一话的原文。后台跑。"""
    from cores import authoring
    sid = path.split("/")[3]
    ep = int(d.get("ep") or 1)
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    j = _start_job(sid, "第%d话原文" % ep, 1)

    def _run():
        try:
            authoring.gen_prose(sid, ep, on_step=lambda m: _set_step(j, m))
            _job_event(j, "info", "第%d话原文已生成" % ep)
            _finish_job(j)
        except Exception as e:
            _job_event(j, "error", str(e)[:200])
            _finish_job(j, str(e))

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True, "ep": ep}})


@post(("/api/saga/", "/health"))
def saga_health(h, path, d):
    """体检红绿灯：正文/剧本/提示词。只读，不改东西。"""
    from cores import health as _hl
    sid = path.split("/")[3]
    ep = int((d or {}).get("ep") or 1)
    try:
        return _RESP({"ok": True, "data": _hl.all_health(sid, ep)})
    except Exception as e:
        return _RESP({"ok": False, "error": str(e)[:300]}, 500)


@post(("/api/saga/", "/regenerate-story"))
def saga_regen_story(h, path, d):
    """按设定重新生成故事（人设+简介 → 全新第一话原文）。后台跑。"""
    from cores import authoring
    sid = path.split("/")[3]
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    j = _start_job(sid, "按设定重新生成故事", 1)

    def _run():
        try:
            authoring.regenerate_story(sid, on_step=lambda m: _set_step(j, m))
            _job_event(j, "info", "第一话原文已按人设+简介重写")
            _finish_job(j)
        except Exception as e:
            _job_event(j, "error", str(e)[:200])
            try:
                _finish_job(j)
            except Exception:
                _finish_job(j, e)

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


@post(("/api/story/", "/characters/", "/delete"))
def del_character(h, path, d):
    """删人物卡。界面上一直有这个按钮，后端却没有路由——点了永远 not found。"""
    from cores import asset_core
    parts = path.strip("/").split("/")
    sid = parts[2]
    cid = parts[4]
    c = asset_core.get_asset(sid, "characters", cid)
    if not c:
        return _RESP({"ok": False, "error": "人物不存在：" + cid}, 400)
    asset_core.delete_asset(sid, "characters", cid)
    st = story_core.get_story(sid)
    if st and cid in (st.get("character_ids") or []):
        st["character_ids"].remove(cid)
        story_core.save_story(st)
    return _RESP({"ok": True, "data": {"deleted": cid}})


@post(("/api/story/", "/scenes/", "/delete"))
def del_scene(h, path, d):
    from cores import asset_core
    parts = path.strip("/").split("/")
    sid, scid = parts[2], parts[4]
    if not asset_core.get_asset(sid, "scenes", scid):
        return _RESP({"ok": False, "error": "场景不存在"}, 400)
    asset_core.delete_asset(sid, "scenes", scid)
    st = story_core.get_story(sid)
    if st and scid in (st.get("scene_ids") or []):
        st["scene_ids"].remove(scid)
        story_core.save_story(st)
    return _RESP({"ok": True, "data": {"deleted": scid}})


@post(("/api/saga/", "/build-shotlist"))
def saga_build_shotlist(h, path, d):
    """只生成某一话的【场次表】，不写正文。

    【为什么单独一个接口】用户要在冻结设定之前确认这一话的结构。
    场次表（几场、在哪、内外、多少秒、这场干什么）一眼能看出建置够不够、
    是不是全困在一个屋里、有没有缺场景卡；而一句话故事简介读着挺顺，
    这些毛病一个都看不出来。
    成本上也该开在这里：场次表约 2 分钟，正文约 9 分钟——
    先花 2 分钟确认结构，比写完 9 分钟再推倒重来划算。
    """
    import threading
    from cores import saga_core as sc, story_gen
    sid = path.split("/")[3]
    no = int(d.get("no") or 1)
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    saga = sc.get_saga(sid)
    e = sc.episode(saga, no)
    if not e:
        return _RESP({"ok": False, "error": "没有第 %d 集" % no}, 400)
    j.update(running=True, step="第%d话场次表" % no, err="")

    def _run():
        try:
            from cores import asset_core as _ac, project_prompt as _pp, project_settings as _ps
            ctx_text = sc.build_context(saga, no)
            st = story_core.get_story(sid) or {}
            _char_by_name = {str(x.get("name") or "").strip(): x
                             for x in _ac.list_assets(sid, "characters")}
            _chars = [_char_by_name.get(str(n).strip(), {"name": str(n).strip()})
                      for n in (e.get("chars") or []) if str(n or "").strip()]
            _scene_cards = _ac.list_assets(sid, "scenes")
            _scene_names = [str(x.get("name") or "").strip() for x in _scene_cards]
            _scene_by_name = {str(x.get("name") or "").strip(): x for x in _scene_cards}
            _scenes = []
            for _planned in (e.get("locations") or []):
                _raw = str(_planned or "").strip()
                if not _raw:
                    continue
                _m = _pp.match_scene_name(_raw, _scene_names)
                _scenes.append(_scene_by_name.get(_m, {"name": _raw}))
            _settings = dict(st.get("settings") or {})
            _settings.update(_ps.get(st))
            sk, meta = story_gen.generate_shotlist(
                ctx_text + (chr(10) * 2) + "【要写的就是第%d集】" % no,
                _settings, None, setup={"characters": _chars, "scenes": _scenes},
                on_step=lambda m: j.update(step="第%d集·%s" % (no, m)))
            s2 = sc.get_saga(sid)
            e2 = sc.episode(s2, no)
            e2["skeleton"] = sk or {}
            e2["core"] = meta.get("core")
            e2["skeleton_stale"] = False
            # 正文还没写，已有的正文和分镜跟新场次表可能对不上，标记出来。
            if e2.get("body"):
                e2["body_stale"] = True
                e2["timeline_stale"] = True
            sc.save_saga(sid, s2)
            if _settings.get("genre_engine"):
                _ps.save(story_core.get_story(sid) or {}, {
                    "genre_engine": _settings.get("genre_engine"),
                    "genre_engine_reason": _settings.get("genre_engine_reason") or ""})
            # 场次表一出来就把拍摄条件落到卡上，缺的卡当场补建——
            # 这样用户看场次表时，下面的场景卡列表已经是齐的了。
            try:
                from cores import scene_layer as _sl
                _r = _sl.sync_scene_cards(sid, sk, e2.get("body") or "", no, _settings)
                if _r.get("created"):
                    j["note"] = "补建场景卡：" + "、".join(_r["created"])
            except Exception:
                pass
            try:
                _st = story_core.get_story(sid) or {}
                _st["skeleton"] = e2["skeleton"]
                _st["skeleton_episode"] = no
                story_core.save_story(_st)
            except Exception:
                pass
            j["step"] = "完成"
        except Exception as ex:
            # 【任务永不中断】场次表没出来也要把话说清楚，不能只留一句报错。
            j["err"] = str(ex)[:300]
            j["step"] = "失败"
        finally:
            j["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


@post(("/api/saga/", "/shotlist"))
def saga_shotlist(h, path, d):
    """读某一话的场次表（给设定页的确认面板用）。"""
    from cores import saga_core as sc, asset_core, project_prompt as pp
    sid = path.split("/")[3]
    no = int(d.get("no") or 1)
    saga = sc.get_saga(sid)
    e = sc.episode(saga, no) or {}
    sk = e.get("skeleton") or {}
    scenes = list(sk.get("scenes") or [])
    names = [str(x.get("name") or "") for x in asset_core.list_assets(sid, "scenes")]
    rows = []
    for x in scenes:
        loc = str(x.get("location") or "")
        it = str(x.get("interior") or "")
        want = ("%s（%s）" % (loc, it)) if it else loc
        has = want in names or bool(pp.match_scene_name(loc, names))
        rows.append({"no": x.get("no"), "location": loc, "interior": it,
                     "time_of_day": x.get("time_of_day"), "scale": x.get("scale"),
                     "seconds": x.get("seconds"), "purpose": x.get("purpose"),
                     "has_card": bool(has)})
    return _RESP({"ok": True, "data": {
        "no": no, "title": e.get("title") or "", "brief": e.get("brief") or "",
        "hook": e.get("hook") or "",
        "total_seconds": sk.get("total_seconds"),
        "scenes": rows, "has_body": bool(e.get("body")),
        "engine": str(((story_core.get_story(sid) or {}).get("settings") or {})
                      .get("genre_engine") or "")}})


# ───────── P462：两按钮流程——先看剧本和分段，认可了再出图出片 ─────────

def _light_status(sid, ep):
    """P463：文字链用的轻量状态——不碰 total_segments（它在分段过期时会去跑导演，等于偷偷调模型）。"""
    from cores import authoring, asset_core as _ac
    e = authoring._ep(sid, int(ep), create=False)[1] or {}
    chars = [c for c in (_ac.list_assets(sid, "characters") or []) if not c.get("auto_incidental")]
    _stale, _why = authoring.screenplay_stale(sid, int(ep))
    return {"prose": len(str(e.get("prose") or "")), "characters": len(chars),
            "script": len(str(e.get("pictures") or e.get("body") or "")), "script_stale": _stale}


def _brief_sig(sid, ep):
    from cores import universal_writer as _uw, saga_core as _sg
    e = _sg.episode(_sg.get_saga(sid), int(ep)) or {}
    return _uw.signature(str(e.get("brief") or "").strip())


def _text_chain_to_shots(sid, j, ep, one):
    """正文 → 人物卡 → 剧本 → 场景卡 → 剧本终稿修 → 导演分段冻结。不出任何图。
    P463：口述改过（brief 指纹和上次写正文时不同）→ 重写正文（没出过人设图就连人物卡一起重建）；否则正文/剧本/分段一个字不动，只补缺的。"""
    from cores import authoring, shot_edit, asset_core as _ac
    _st0 = _light_status(sid, ep)
    _e0 = authoring._ep(sid, ep, create=False)[1] or {}
    _rewrite = _st0["prose"] >= 300 and _e0.get("text_brief_sig") and _e0.get("text_brief_sig") != _brief_sig(sid, ep)
    if _rewrite:
        _job_event(j, "info", "「这一话讲什么」改过了：正文、剧本、分段按新口述重写")
        _adopted = {str(v.get("owner_id") or "") for v in (_ac.list_assets(sid, "visuals") or []) if v.get("status") == "adopted"}
        _cards0 = _ac.list_assets(sid, "characters") or []
        if not any(str(c.get("character_id") or "") in _adopted for c in _cards0):
            for c in _cards0:
                try:
                    _ac.delete_asset(sid, "characters", c.get("character_id"))
                except Exception:
                    pass
            _job_event(j, "info", "人物卡还没出过图，随新正文重建")
        authoring._update_ep(sid, ep, prose="", body="", pictures="", shotlist=None)
        try:
            from cores import saga_core as _sgx
            _tlx = _sgx.ep_timeline(sid, ep) or {"scenes": []}
            _tlx.pop("slicing", None)
            _sgx.save_ep_timeline(sid, ep, _tlx)
            shot_edit.void_segments_from(sid, ep, 1, 0)
        except Exception:
            pass
        _st0 = _light_status(sid, ep)
    if _st0["prose"] < 300:
        _st_single = story_core.get_story(sid) or {}
        _se_single = dict(_st_single.get("settings") or {})
        if str(_se_single.get("story_mode") or "single").lower() != "single":
            raise RuntimeError("这条路只给单话/口述项目用（分话项目请用「生成这一话」）")
        _se_single["plan_problems"] = []
        if int(ep) == 1 and str(one or "").strip():
            _se_single["one_line"] = str(one).strip()
        _st_single["settings"] = _se_single
        story_core.save_story(_st_single)
        authoring._update_ep(sid, ep, brief=str(one or "").strip(), brief_edited_by_user=True, plan_version=0, plan_stale=False)
        _set_step(j, "按你给的本话剧情写正文")
        authoring.gen_prose(sid, ep, on_step=lambda m: _set_step(j, m))
        _complete_step(j)
        _st0 = _light_status(sid, ep)
    authoring._update_ep(sid, ep, text_brief_sig=_brief_sig(sid, ep))           # P463：这份正文对应的口述
    try:
        from cores import manual_story as _ms
        if _ms.enabled((story_core.get_story(sid) or {}).get("settings")):
            _ms.approve(sid, ep)                                           # 逐话模式：这条链写的正文就算确认过（用户在剧本页看的就是它）
    except Exception as _ax:
        _job_event(j, "error", "正文确认没记上（出片时可能重写正文）：%s" % str(_ax)[:100])
    if _st0["characters"] <= 0:
        _set_step(j, "从本话正文整理人物卡")
        _ss = (story_core.get_story(sid) or {}).get("settings") or {}
        _prose_now = str((authoring._ep(sid, ep, create=False)[1] or {}).get("prose") or "")
        _designed = authoring.design_characters(_prose_now, _ss)
        _designed, _dropped = authoring._ground_cards(_designed, _prose_now, _ss)
        if int(ep) > 1:
            _have = {str(c.get("name") or "") for c in (_ac.list_assets(sid, "characters") or [])}
            _brief = str((authoring._ep(sid, ep, create=False)[1] or {}).get("brief") or "")
            _designed = [c for c in (_designed or []) if isinstance(c, dict) and str(c.get("name") or "") not in _have
                         and str(c.get("name") or "") and str(c.get("name")) in _brief]
        _cards = authoring._create_cards(sid, _designed) if _designed else []
        _job_event(j, "info", "人物卡 %d 张：%s" % (len(_cards or []), "、".join(str(c.get("name") or "") for c in (_cards or [])[:6])))
        _complete_step(j)
    else:
        _job_event(j, "info", "正文和人物卡已有，跳过不重做")
    _st_now = _light_status(sid, ep)
    if _st_now["script"] >= 200 and _st_now.get("script_stale"):
        _set_step(j, "正文改过了，先重出剧本")
        _rl = []
        authoring.refresh_upstream(sid, ep, on_step=lambda m: _set_step(j, m), log=_rl)
        _job_event(j, "info", "；".join(_rl) or "上游已对齐")
    elif _st_now["script"] >= 200:
        _job_event(j, "info", "剧本已有且和正文一致，跳过")
    else:
        _set_step(j, "编剧把第 %d 话原文改编成剧本" % ep)
        authoring.make_pictures(sid, ep)
        try:
            _sc = authoring.ensure_scenes(sid, ep)
            _job_event(j, "info", "场景卡 %d 个：%s" % (len(_sc), "、".join(str(x.get("name") or "") for x in _sc[:4])))
        except Exception as _ex:
            _job_event(j, "error", "场景卡没建成：%s" % str(_ex)[:120])
    _complete_step(j)
    try:
        _set_step(j, "把剧本修到终稿（台词/招牌/建卡，只修不问）")
        _preflight_gate(sid, j, [], ep=ep, fix_only=True)
    except JobCancelled:
        raise
    except Exception as _gx:
        _job_event(j, "error", "剧本自修出错（照旧分段）：%s" % str(_gx)[:120])
    _complete_step(j)
    _rows = shot_edit.rows_of(sid, ep)
    if _rows["n"] and not _rows["stale"]:
        _job_event(j, "info", "分段已有且和剧本一致（%d 段），保留" % _rows["n"])
        n = _rows["n"]
    else:
        _set_step(j, "导演分段" if not _rows["n"] else "剧本改过了，导演重新分段")
        n = shot_edit.reslice(sid, ep, on_step=lambda m: _set_step(j, m))
        _job_event(j, "info", "第 %d 话分成 %d 段——去 📖 剧本 页看剧本和分段" % (ep, n))
    _complete_step(j)
    return n


@post(("/api/saga/", "/plan-shots"))
def saga_plan_shots(h, path, d):
    """按钮①：生成剧本和分段（不出图不出片）。"""
    sid = path.split("/")[3]
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    if _timeline_busy(sid):
        return _RESP({"ok": False, "error": "分镜页的任务还在跑，等它结束再点"}, 400)
    st = story_core.get_story(sid) or {}
    settings = dict(st.get("settings") or {})
    ep = int(d.get("ep") or 1)
    one = str(d.get("one_line") or "").strip()
    if not one:
        from cores import saga_core as _sg
        e = _sg.episode(_sg.get_saga(sid), ep) or {}
        one = str(e.get("brief") or settings.get("one_line") or "").strip()
    if not one:
        return _RESP({"ok": False, "error": "先写这一话讲什么"}, 400)
    if d.get("one_line") and ep == 1 and d.get("one_line") != settings.get("one_line"):
        settings["one_line"] = one
        st["settings"] = settings
        story_core.save_story(st)
    j = _start_job(sid, "生成剧本和分段", 5)

    def _run():
        try:
            _text_chain_to_shots(sid, j, ep, one)
            _finish_job(j)
        except JobCancelled:
            _finish_job(j, "已取消")
        except Exception as ex:
            _finish_job(j, str(ex)[:300])

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


@post(("/api/saga/", "/slices"))
def saga_slices(h, path, d):
    from cores import shot_edit
    sid = path.split("/")[3]
    return _RESP({"ok": True, "data": shot_edit.rows_of(sid, int(d.get("ep") or 1))})


@post(("/api/saga/", "/slices-save"))
def saga_slices_save(h, path, d):
    from cores import shot_edit
    sid = path.split("/")[3]
    if _job(sid).get("running") or _timeline_busy(sid):
        return _RESP({"ok": False, "error": "有任务在跑，等它结束再改分段"}, 400)
    try:
        r = shot_edit.save_rows(sid, int(d.get("ep") or 1), d.get("rows") or [])
    except ValueError as ex:
        return _RESP({"ok": False, "error": str(ex)}, 400)
    return _RESP({"ok": True, "data": r})


@post(("/api/saga/", "/reslice"))
def saga_reslice(h, path, d):
    from cores import shot_edit
    sid = path.split("/")[3]
    j = _job(sid)
    if j.get("running") or _timeline_busy(sid):
        return _RESP({"ok": False, "error": "有任务在跑，等它结束再重分"}, 400)
    ep = int(d.get("ep") or 1)
    j = _start_job(sid, "重新分段", 1)

    def _run():
        try:
            _set_step(j, "导演按现在的剧本重新分段")
            n = shot_edit.reslice(sid, ep, on_step=lambda m: _set_step(j, m))
            _complete_step(j)
            _job_event(j, "info", "重分成 %d 段（之前写的提示词已作废）" % n)
            _finish_job(j)
        except JobCancelled:
            _finish_job(j, "已取消")
        except Exception as ex:
            _finish_job(j, str(ex)[:300])

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


@post(("/api/saga/", "/approve-shots"))
def saga_approve_shots(h, path, d):
    from cores import shot_edit, manual_story as _ms
    sid = path.split("/")[3]
    ep = int(d.get("ep") or 1)
    try:
        if _ms.enabled((story_core.get_story(sid) or {}).get("settings")):
            try:
                _ms.require_approved(sid, ep)
            except ValueError:
                _ms.approve(sid, ep)                                       # 认可分段＝正文也认可；想法改过没重写会在这里报错
        n = shot_edit.approve(sid, ep)
    except ValueError as ex:
        return _RESP({"ok": False, "error": str(ex)}, 400)
    return _RESP({"ok": True, "data": {"n": n}})


@post(("/api/saga/", "/make"))
def saga_make(h, path, d):
    """P463 设定页三按钮：stage=text（剧本+分段）| images（+人设图+场景图）| video（+提示词+全部视频+成片）。后一阶段自动先补前面的。"""
    from cores import shot_edit, manual_story as _ms
    sid = path.split("/")[3]
    stage = str(d.get("stage") or "text")
    if stage not in ("text", "images", "video"):
        return _RESP({"ok": False, "error": "stage 只能是 text / images / video"}, 400)
    j = _job(sid)
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    if _timeline_busy(sid):
        return _RESP({"ok": False, "error": "分镜页的任务还在跑，等它结束再点"}, 400)
    ep = int(d.get("ep") or 1)
    from cores import saga_core as _sg
    e = _sg.episode(_sg.get_saga(sid), ep) or {}
    one = str(e.get("brief") or ((story_core.get_story(sid) or {}).get("settings") or {}).get("one_line") or "").strip()
    if not one and not str(e.get("prose") or "").strip():
        return _RESP({"ok": False, "error": "先写这一话讲什么"}, 400)
    title = {"text": "生成文字", "images": "生成图片", "video": "生成视频"}[stage]
    j = _start_job(sid, title, {"text": 5, "images": 7, "video": 9}[stage])

    def _run():
        try:
            n = _text_chain_to_shots(sid, j, ep, one)
            if stage == "text":
                _job_event(j, "info", "文字部分完成：剧本 + %d 段分段——去 📖 剧本 页看，改了会自动保存" % n)
                _finish_job(j)
                return
            from cores import authoring, asset_core as _ac
            if [c for c in (_ac.list_assets(sid, "characters") or []) if not c.get("visuals")]:
                _set_step(j, "出人设图")
                _r = authoring.gen_char_images(sid, only_missing=True, on_step=lambda m: _set_step(j, m)) or {}
                if _r.get("failed"):
                    _job_event(j, "error", "这几个人设图没出来：%s" % "、".join(_r["failed"][:4]))
            else:
                _job_event(j, "info", "人设图都有了，跳过")
            _complete_step(j)
            _set_step(j, "出场景图")
            _ep_scene_images(sid, ep, j)
            _complete_step(j)
            if stage == "images":
                _job_event(j, "info", "图片部分完成——去 📖 剧本 页看人设图和场景图")
                _finish_job(j)
                return
            try:
                _ms.approve(sid, ep) if _ms.enabled((story_core.get_story(sid) or {}).get("settings")) else None
            except Exception:
                pass
            shot_edit.approve(sid, ep)
            _outs = _first_video_chain(sid, j, n=None, size_tier=(str(d.get("size_tier") or "").strip() or None), ep=ep, approved=True)
            if _outs:
                _job_event(j, "info", "第 %d 话出片完成：%d 段" % (ep, len(_outs)))
            if _outs and not j.get("blocked"):
                try:
                    from cores import episode_merge as _em
                    _set_step(j, "合成第 %d 话成片" % ep)
                    _dst = _em.merge_episode(sid, ep)
                    if _dst:
                        j["merged"] = _dst
                        _job_event(j, "info", "第 %d 话成片：%s" % (ep, _dst))
                except Exception as _mx:
                    _job_event(j, "error", "合成成片失败（各段视频都在）：%s" % str(_mx)[:160])
            _finish_job(j)
        except JobCancelled:
            _finish_job(j, "已取消")
        except Exception as ex:
            _finish_job(j, str(ex)[:300])

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True, "stage": stage}})


@post(("/api/saga/", "/script-save"))
def saga_script_save(h, path, d):
    """P463 剧本页单框自动保存：正文＝剧本＝分镜读的剧本，三份同一段字；改了就算用户确认过。分段不在这里动（过期由下一步生成前重分）。"""
    from cores import saga_core as sc, manual_story as _ms, universal_writer as _uw
    sid = path.split("/")[3]
    ep = int(d.get("ep") or 1)
    text = str(d.get("text") or "")
    saga = sc.get_saga(sid)
    e = sc.episode(saga, ep)
    if not e:
        return _RESP({"ok": False, "error": "没有这一话"}, 400)
    if e.get("status") == sc.LOCKED:
        return _RESP({"ok": False, "error": "这一话已锁定"}, 400)
    if text == str(e.get("pictures") or e.get("body") or e.get("prose") or ""):
        return _RESP({"ok": True, "data": {"changed": False}})
    if str(e.get("prose") or "").strip():
        e.setdefault("prose_versions", []).append({"at": time.time(), "prose": e["prose"]})
        e["prose_versions"] = e["prose_versions"][-10:]
    e["prose"] = e["body"] = e["pictures"] = text
    e.pop("shotlist", None)
    from cores import authoring as _au463
    e["prose_sig"] = _au463.content_sig(text)                                  # 剧本就是照这份正文来的（同一份字），别再报过期重出
    e["body_stale"] = False
    e["edited_by_user"] = True
    e["timeline_stale"] = True
    e["text_brief_sig"] = _uw.signature(str(e.get("brief") or "").strip())
    if _ms.enabled((story_core.get_story(sid) or {}).get("settings")):
        e["writing_review"] = dict(e.get("writing_review") or {}, brief_sig=_uw.signature(e.get("brief", "")))
        e["prose_approved_sig"] = _ms.approval_signature(e)
    if e.get("status") == sc.EMPTY:
        e["status"] = sc.DRAFT
    sc.save_saga(sid, saga)
    from cores import shot_edit
    return _RESP({"ok": True, "data": {"changed": True, "slices_stale": bool(shot_edit.rows_of(sid, ep).get("stale"))}})
