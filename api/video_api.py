# -*- coding: utf-8 -*-
"""video_api 域路由（由 server.py 的 do_POST 拆分而来）。"""
from api import post
from api._shared import _RESP, ctx
globals().update(ctx())


# 设定页的「🎭 重新生成设定图 / 🏛 重新生成场景图」走这里。
# 原来只 create_task 排个队就返回，而这个队列**没有执行器**——
# 任务永远 queued，图永远不出来，前端也没有进度可显示：点了就是没反应。
_PRODUCE_JOBS = {}


def _pjob(key):
    return _PRODUCE_JOBS.setdefault(key, {"running": False, "step": "", "err": ""})


@post("/api/produce")
def r_035(h, path, d):
    """直接后台出图，带可查进度。"""
    import threading
    from cores import asset_core
    sid = str(d.get("story_id") or "")
    kind = str(d.get("kind") or "")
    oid = str(d.get("owner_id") or "")
    if not (sid and oid):
        return _RESP({"ok": False, "error": "缺少 story_id/owner_id"}, 400)
    # 【不许复用】这里原来有一条短路：只要这个人物已经有「已采用」的人设图，
    # 就直接返回 reused 不出图，而且返回 ok:true——页面照样弹"设定图已生成并采用"。
    # 结果是用户改完人设卡、点「重新生成设定图」、看到成功提示、图纹丝不动
    # （2026-08-27 用户实测）。这两个入口都是用户手点的「出图」按钮，
    # 点了就必须真出。旧图不会丢：新图是新版本，adopt 时旧的转成 superseded 候选。
    key = sid + ":" + oid
    j = _pjob(key)
    if j.get("running"):
        return _RESP({"ok": False, "error": "这一张已经在生成了"}, 400)
    j.update(running=True, step="排队", err="", done=None)

    def _run():
        try:
            ps = project_settings.get(story_core.get_story(sid) or {}) or {}
            style = str(ps.get("style") or "电影级剧照")

            _card = asset_core.get_asset(sid, "scenes" if "scene" in kind else "characters", oid) or {}
            _override = str(d.get("prompt") or _card.get("prompt_override") or "").strip() or None
            if "scene" in kind:
                j["step"] = "画场景图"
                v = asset_core.generate_scene_master(sid, oid, style_profile=style,
                                                     custom_prompt=_override)
            else:
                j["step"] = "画人物设定图"
                v = asset_core.generate_character_master(sid, oid, style_profile=style,
                                                         custom_prompt=_override)
            if isinstance(v, tuple):
                v = v[0]
            if v and v.get("visual_id"):
                asset_core.adopt(sid, v["visual_id"])
                j["done"] = {"visual_id": v["visual_id"], "path": v.get("path")}
            j["step"] = "完成"
        except Exception as e:
            j["err"] = str(e)[:300]
            j["step"] = "失败"
        finally:
            j["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True, "key": key}})


@post("/api/asset/prompt")
def asset_prompt(h, path, d):
    """取某张设定图当前会用的提示词，给页面放进可编辑框。

    用户要求：能看见提示词、能直接改、改完点出图看效果。
    改过的会存在卡上（prompt_override），下次出图默认沿用；
    传 reset=true 就丢掉改动，回到编译器生成的那份。
    """
    from cores import asset_core, quality_core, project_settings
    sid = str(d.get("story_id") or "")
    oid = str(d.get("owner_id") or "")
    kind = str(d.get("kind") or "")
    story = story_core.get_story(sid)
    if not (story and oid):
        return _RESP({"ok": False, "error": "缺少 story_id/owner_id"}, 400)
    ps = project_settings.get(story) or {}
    style = str(ps.get("style") or "电影级剧照")
    is_scene = "scene" in kind
    card = asset_core.get_asset(sid, "scenes" if is_scene else "characters", oid)
    if not card:
        return _RESP({"ok": False, "error": "找不到这张卡"}, 404)
    if d.get("reset"):
        card.pop("prompt_override", None)
        asset_core.save_asset(sid, "scenes" if is_scene else "characters", card)
    c = dict(card)
    c["_project"] = ps
    default = (quality_core.compile_scene_prompt(c, style=style) if is_scene
               else quality_core.compile_character_prompt(c, None, style=style))
    if not is_scene:
        default = default.rstrip("。 ") + "。人物穿戴整齐，全套服装完整地穿在身上。"
        # 卡上 image_prompt 是上一次真正出图用的那份（authoring.write_char_sheet 写的）。
        # quality_core 这份只是没出过图时的兜底——拿它当"默认"会让编辑框里显示的
        # 和实际用的不是同一段，用户改了半天改的是另一份（2026-08-27 发现）。
        default = str(card.get("image_prompt") or "").strip() or default
    return _RESP({"ok": True, "data": {
        "prompt": str(card.get("prompt_override") or default),
        "default_prompt": default,
        "edited": bool(str(card.get("prompt_override") or "").strip())}})


@post("/api/asset/prompt/save")
def asset_prompt_save(h, path, d):
    """把用户改过的提示词存到卡上。"""
    from cores import asset_core
    sid = str(d.get("story_id") or "")
    oid = str(d.get("owner_id") or "")
    is_scene = "scene" in str(d.get("kind") or "")
    card = asset_core.get_asset(sid, "scenes" if is_scene else "characters", oid)
    if not card:
        return _RESP({"ok": False, "error": "找不到这张卡"}, 404)
    card["prompt_override"] = str(d.get("prompt") or "").strip()
    card["edited_by_user"] = True
    asset_core.save_asset(sid, "scenes" if is_scene else "characters", card)
    return _RESP({"ok": True, "data": {"saved": True}})


@post("/api/produce/progress")
def r_035b(h, path, d):
    key = str(d.get("story_id") or "") + ":" + str(d.get("owner_id") or "")
    return _RESP({"ok": True, "data": _pjob(key)})

@post("/api/video/take/adopt")
def r_039(h, path, d):
    sid = str(d.get("story_id") or "")
    seq_id = str(d.get("sequence_id") or "")
    take_id = str(d.get("take_id") or "TAKE_00001")
    vpath = str(d.get("path") or "")
    if not sid or not seq_id:
        return _RESP({"ok": False, "error": "缺少 story_id/sequence_id"}, 400)
    tdir = store.DATA / "takes" / sid
    tdir.mkdir(parents=True, exist_ok=True)
    rec = {"sequence_id": seq_id, "take_id": take_id, "path": vpath,
           "adopted_at": time.time()}
    store.save_json(tdir / (seq_id + ".json"), rec)
    try:
        story = story_core.get_story(sid)
        if story:
            state_core.commit_state(story, story.get("current_episode") or 1,
                                    "采用视频 Take " + take_id + "（" + seq_id + "）")
    except Exception:
        pass
    return _RESP({"ok": True, "data": rec})

@post("/api/video/stitch")
def r_040(h, path, d):
    try:
            sid = str(d.get("story_id") or "")
            if not sid:
                return _RESP({"ok": False, "error": "缺少 story_id"}, 400)
            from production import video_core
            tdir = store.DATA / "takes" / sid
            files = []
            if tdir.exists():
                for fp in sorted(tdir.glob("*.json")):
                    t = store.load_json(fp, None)
                    if t and t.get("path") and os.path.exists(t["path"]):
                        files.append(t["path"])
            if not files:
                for fp in (store.DATA / "productions").glob("*.json"):
                    p = store.load_json(fp, None)
                    if p and p.get("story_id") == sid and str(p.get("kind", "")).lower() == "video":
                        for f in (p.get("files") or []):
                            if os.path.exists(f):
                                files.append(f)
                        if files:
                            break
            if not files:
                return _RESP({"ok": False, "error": "还没有已采用的 Take 或视频成品"}, 400)
            story = story_core.get_story(sid) or {}
            merged = video_core.concat_sequences(files, 30, (story.get("title") or "video") + "_拼接")
            rec = {"production_id": store.seq_id("VIDEO", int(time.time())),
                   "story_id": sid, "episode_no": story.get("current_episode") or 1,
                   "kind": "video", "status": "completed",
                   "files": [merged["path"]], "prompt_ref": "", "reference_ref": "",
                   "task_id": "", "created": time.time(), "adopted": True}
            store.save_json(store.DATA / "productions" / (rec["production_id"] + ".json"), rec)
            return _RESP({"ok": True, "data": {"merged": merged, "production": rec}})
    except Exception as e:
        return _RESP({"ok": False, "error": str(e)[:500]}, 500)

