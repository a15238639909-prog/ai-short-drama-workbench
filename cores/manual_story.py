"""Manual episode plots: project origin is immutable after episode one is saved."""
import copy
import time
from . import story_core, saga_core, universal_writer as uw


def enabled(settings):
    return (settings or {}).get("story_mode") in ("single", "episode", "manual")


def save_input(sid, no, brief, pace="适中", notes="", shooting_notes=""):
    no = int(no)
    if no < 1 or pace not in ("舒缓", "适中", "紧凑"):
        raise ValueError("话号或节奏无效")
    st = story_core.get_story(sid)
    if not st or not enabled(st.get("settings")):
        raise ValueError("此项目不是逐话写作模式")
    sg = saga_core._saga(st)
    current = saga_core.episode(sg, no)
    if current is None:
        if no > 1 and not saga_core.episode(sg, no - 1):
            raise ValueError("请按顺序添加下一话")
        current = {"no": no, "status": saga_core.PLANNED}
        sg["episodes"].append(current)
    if current.get("status") == saga_core.LOCKED:
        raise ValueError("这一话已锁定，请先解锁")
    brief = str(brief or "").strip()
    if any(current.get(k, "") != v for k, v in (("brief", brief), ("story_pace", pace),
                                                  ("episode_notes", notes))):
        current.pop("prose_approved_sig", None)
    shooting_changed = str(current.get("shooting_notes") or "") != str(shooting_notes or "").strip()
    if shooting_changed:
        current["timeline_stale"] = True
        for scene in ((current.get("timeline") or {}).get("scenes") or []):
            for segment in (scene.get("segments") or []):
                for key in ("prompt", "prompt_auto", "prompt_edited", "prompt_edited_at"):
                    segment.pop(key, None)
                if str(segment.get("video") or "").strip():
                    segment["stale_video"] = True
    current.update(brief=brief, brief_edited_by_user=True, story_pace=pace,
                   episode_notes=notes, shooting_notes=str(shooting_notes or "").strip())
    # First input is the historical origin, not a mutable alias of the current textbox.
    settings = st.setdefault("settings", {})
    if no == 1 and brief and not settings.get("initial_story_input"):
        settings["initial_story_input"] = brief
        settings["one_line"] = brief
        st.setdefault("project_settings", {}).update(one_line=brief, initial_story_input=brief)
        st["one_line"] = brief
    story_core.save_story(st)
    return current


def approval_signature(e):
    return uw.signature({k: e.get(k) for k in ("prose", "brief", "story_pace", "episode_notes")})


def require_approved(sid, no):
    e = saga_core.episode(saga_core.get_saga(sid), no) or {}
    if not str(e.get("prose") or "").strip() or e.get("prose_approved_sig") != approval_signature(e):
        raise ValueError("请先通读并确认第%d话正文，再制作或续写" % int(no))
    return e


def approve(sid, no):
    sg = saga_core.get_saga(sid)
    e = saga_core.episode(sg, no) or {}
    if not str(e.get("prose") or "").strip():
        raise ValueError("这一话还没有正文")
    if e.get("writing_review", {}).get("brief_sig") != uw.signature(e.get("brief", "")):
        raise ValueError("本话想法已经改动，请先按新想法重写正文")
    e["prose_approved_sig"] = approval_signature(e)
    saga_core.save_saga(sid, sg)
    return e


def write(sid, no, on_step=None):
    no = int(no)
    st = story_core.get_story(sid) or {}
    settings = copy.deepcopy(st.get("settings") or {})
    sg = saga_core.get_saga(sid)
    current = saga_core.episode(sg, no) or {}
    if current.get("status") == saga_core.LOCKED:
        raise ValueError("这一话已锁定")
    if not str(current.get("brief") or "").strip():
        raise ValueError("先填写本话想发生什么")
    previous = []
    for n in range(1, no):
        old = require_approved(sid, n)
        previous.append({"话号": n, "用户剧情": old.get("brief"),
                         "原文" if n >= no-2 else "原文结尾（截取）": old["prose"] if n >= no-2 else old["prose"][-600:]})
    before = uw.signature({"settings": st.get("settings"), "episodes": sg.get("episodes")})
    settings["story_pace"] = current.get("story_pace", "适中")
    settings["episode_notes"] = current.get("episode_notes", "")
    # P445②③：人物表固定（第一话建的卡），续话带上一话结束时的账本
    try:
        from . import asset_core as _ac
        settings["_cast_names"] = [str(c.get("name") or "") for c in (_ac.list_assets(sid, "characters") or []) if c.get("name") and not c.get("auto_incidental")]
    except Exception:
        settings["_cast_names"] = []
    if no > 1:
        try:
            from . import episode_ledger as _ledg
            settings["_ledger_prev"] = _ledg.text(sid, no - 1)
        except Exception:
            settings["_ledger_prev"] = ""
    # 一个输入框：设定说明与剧情分开，简短构思补成因果链，完整口述忠实整理。
    from . import oral_story as _os_, authoring as _au
    plot = _os_.plan_episode(current["brief"], _au._q, settings, previous, on_step)
    settings["_events"] = plot["events"]
    settings["_story_context"] = plot["context"]
    settings["_plot_mode"] = plot["mode"]
    if settings.get("writer_table", True):
        # P429 写法 H（9-19 六种写法试验里最好）：先出每件事的【状态】+ 对白轮次，正文按表写
        try:
            from . import oral_story as _os_, authoring as _au
            if on_step:
                on_step("先定每件事的称呼状态和对白")
            if on_step:
                on_step("找口述里的原话台词（代码+千问）")
            settings["_quotes"] = _os_.quoted_lines_all(current["brief"], _au._q)                           # P455①；P456：代码 ∪ 千问
            settings["_writer_table"] = _os_.status_dialogue_table(settings.get("_events") or _os_.event_card(current["brief"]), _au._q, quotes=settings["_quotes"])
        except Exception as _tx:
            settings["_writer_table"] = ""
    result = uw.write(settings, no, previous, brief=current["brief"], on_step=on_step, brief_override=True)
    # P366：口述剧情——程序按事件卡核对（缺事件/多数字/多人物 → 一次定向修；越过本话结尾的段砍掉）
    _ev, _fid = [], {}
    try:
        from . import oral_story as _os_, authoring as _au
        _ev = settings.get("_events") or _os_.event_card(current["brief"])
        if _ev:
            result["prose"], _fid = _os_.fidelity_pass(result["prose"], _ev, _au._q, brief=current["brief"], on_step=on_step,
                                                    preserve_story=True)
            result["review"]["fidelity"] = _fid
        # P454②：段落中间的「女：台词」提成独立行、说话人对到卡名（否则台词编号器一句认不出，整话哑剧）
        try:
            from . import asset_core as _ac2
            result["prose"] = _os_.lift_inline_dialogue(result["prose"], _ac2.list_assets(sid, "characters") or [])
        except Exception:
            pass
    except Exception as _fx:
        _fid = {"verdict": "error", "err": str(_fx)[:160]}
    latest = story_core.get_story(sid) or {}
    sg = saga_core.get_saga(sid)
    target = saga_core.episode(sg, no)
    if target is None:
        raise ValueError("生成期间这一话已删除")
    changed = before != uw.signature({"settings": latest.get("settings"), "episodes": sg.get("episodes")})
    if changed or result["review"]["verdict"] in ("fail", "unknown"):
        target.update(prose_draft=result["prose"], writing_attempt=result["review"])
        saga_core.save_saga(sid, sg)
        raise ValueError("本轮正文留作草稿，没有覆盖旧稿：" + ("生成期间输入有变化" if changed else "正文检查未通过"))
    if target.get("prose") and target["prose"] != result["prose"]:
        target.setdefault("prose_versions", []).append({"at": time.time(), "prose": target["prose"]})
    review = result["review"]
    review["brief_sig"] = uw.signature(target["brief"])
    target.update(prose=result["prose"], writing_review=review, prose_draft="", writing_attempt={},
                  writing_request=uw.write.last_request, plan_stale=False, plan_version=0,
                  events=_ev, fidelity=_fid, story_plan=plot)                    # 规划留档，文字和分段使用同一份事件链
    target.pop("prose_approved_sig", None)
    saga_core.save_saga(sid, sg)
    return result["prose"]
