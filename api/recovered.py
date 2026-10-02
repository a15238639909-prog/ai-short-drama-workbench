# -*- coding: utf-8 -*-
"""补回：这些路由第一个条件是 path.count("/")，首轮按 startswith 提取时漏掉了。"""
from api import post
from api._shared import _RESP, ctx
globals().update(ctx())


@post({'prefix': '/api/story/', 'suffix': '/adopt', 'count': 4})
def rec_00(h, path, d):
    sid = path.split("/")[3]
    ep_no = int(d.get("episode_no") or 1)
    ep = narrative_core.adopt_episode(sid, ep_no)
    return _RESP({"ok": True, "episode": ep})

@post({'prefix': '/api/story/', 'suffix': '/characters/save', 'count': 5})
def rec_01(h, path, d):
    sid = path.split("/")[3]
    story = story_core.get_story(sid)
    if not story:
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    items = d.get("characters") if isinstance(d.get("characters"), list) else []
    saved = []
    for c in items:
        cid = c.get("character_id") or ""
        old = asset_core.get_asset(sid, "characters", cid)
        if not old:
            continue
        asset_core.apply_fields(old, asset_core.canonical_character_patch(c), asset_core.CHARACTER_FIELDS)
        asset_core.refresh_character_anchors(old)
        old["edited_by_user"] = True
        old["updated"] = time.time()
        asset_core.save_asset(sid, "characters", old)
        saved.append(old)
    return _RESP({"ok": True, "data": {"characters": saved}})

@post({'prefix': '/api/story/', 'suffix': '/scenes/save', 'count': 5})
def rec_02(h, path, d):
    sid = path.split("/")[3]
    story = story_core.get_story(sid)
    if not story:
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    items = d.get("scenes") if isinstance(d.get("scenes"), list) else []
    saved = []
    for s in items:
        scid = s.get("scene_id") or s.get("name") or ""
        old = asset_core.get_asset(sid, "scenes", scid)
        if not old:
            continue
        asset_core.apply_fields(old, s, asset_core.SCENE_FIELDS)
        old["updated"] = time.time()
        asset_core.save_asset(sid, "scenes", old)
        saved.append(old)
    return _RESP({"ok": True, "data": {"scenes": saved}})

@post({'prefix': '/api/story/', 'suffix': '/scenes/delete', 'count': 6})
def rec_03(h, path, d):
    parts = path.split("/")
    sid, scid = parts[3], parts[4]
    story = story_core.get_story(sid)
    if not story:
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    c = asset_core.get_asset(sid, "scenes", scid)
    if not c:
        return _RESP({"ok": False, "error": "场景不存在"}, 404)
    if scid in (story.get("scene_ids") or []):
        story["scene_ids"].remove(scid)
        story_core.save_story(story)
    trash = ROOT / "cache" / "_deprecated" / ("scene_" + scid + ".json")
    trash.parent.mkdir(parents=True, exist_ok=True)
    import shutil as _sh
    _sh.move(str(store.DATA / "assets" / sid / "scenes" / (scid + ".json")), str(trash))
    log("info", "场景已删除（可恢复）：" + scid)
    return _RESP({"ok": True, "data": {"deleted": scid, "trash": str(trash)}})

@post({'prefix': '/api/story/', 'suffix': '/delete', 'count': 5})
def rec_04(h, path, d):
    parts = path.split("/")
    sid, cid = parts[3], parts[4]
    story = story_core.get_story(sid)
    if not story:
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    c = asset_core.get_asset(sid, "characters", cid)
    if not c:
        return _RESP({"ok": False, "error": "人物不存在"}, 404)
    if cid in (story.get("character_ids") or []):
        story["character_ids"].remove(cid)
        story_core.save_story(story)
    trash = ROOT / "cache" / "_deprecated" / ("char_" + cid + ".json")
    trash.parent.mkdir(parents=True, exist_ok=True)
    import shutil as _sh
    _sh.move(str(store.DATA / "assets" / sid / "characters" / (cid + ".json")), str(trash))
    log("info", "人物已删除（可恢复）：" + cid)
    return _RESP({"ok": True, "data": {"deleted": cid, "trash": str(trash)}})

@post({'prefix': '/api/production/', 'suffix': '/open_folder', 'count': 4})
def rec_05(h, path, d):
    pid = path.split("/")[3]
    p = store.load_json(store.DATA / "productions" / (pid + ".json"), None)
    if not p:
        return _RESP({"ok": False, "error": "成品不存在"}, 404)
    target = next((f for f in (p.get("files") or []) if os.path.exists(f)), None)
    if target is None and isinstance(p.get("output"), dict):
        target = p["output"].get("path")
    if target and os.path.exists(target):
        import subprocess as _sp
        _sp.Popen(["explorer", "/select,", str(target)])
        return _RESP({"ok": True, "data": {"path": target}})
    return _RESP({"ok": False, "error": "文件不存在"}, 404)

@post({'prefix': '/api/production/', 'suffix': '/export', 'count': 4})
def rec_06(h, path, d):
    pid = path.split("/")[3]
    p = store.load_json(store.DATA / "productions" / (pid + ".json"), None)
    if not p:
        return _RESP({"ok": False, "error": "成品不存在"}, 404)
    outdir = ROOT / "outputs" / "exports" / (p.get("story_id") or "story")
    outdir.mkdir(parents=True, exist_ok=True)
    import shutil
    copied = []
    for f in (p.get("files") or []):
        if os.path.exists(f):
            dst = outdir / (pid + "_" + os.path.basename(f))
            shutil.copy2(f, str(dst))
            copied.append(str(dst))
    if not copied:
        return _RESP({"ok": False, "error": "没有可导出文件"}, 400)
    return _RESP({"ok": True, "data": {"files": copied}})

@post({'prefix': '/api/asset/', 'suffix': '/export', 'count': 4})
def rec_07(h, path, d):
    aid = path.split("/")[3]
    sid = str(d.get("story_id") or "")
    v = store.load_json(store.DATA / "assets" / sid / "visuals" / (aid + ".json"), None)
    if not v or not v.get("path") or not os.path.exists(v["path"]):
        return _RESP({"ok": False, "error": "该资产没有可导出文件"}, 400)
    outdir = ROOT / "outputs" / "exports" / sid
    outdir.mkdir(parents=True, exist_ok=True)
    dst = outdir / (aid + os.path.splitext(v["path"])[1] or ".png")
    import shutil
    shutil.copy2(v["path"], str(dst))
    return _RESP({"ok": True, "data": {"path": str(dst), "rel": str(dst.relative_to(ROOT)).replace("\\", "/")}})


# 一键生成是按"前 N 个更新、超出的新建"写回的，需要单个更新和新建两条路由。
# 原来只有批量 /characters/save，前端却在 POST /characters/<id>/update ——
# 那条路由压根不存在，写入全部 404，页面上自然什么都没填上。
@post({'prefix': '/api/story/', 'suffix': '/update', 'count': 7})
def rec_char_update(h, path, d):
    parts = path.split("/")
    sid, kind, oid = parts[3], parts[4], parts[5]
    if kind not in ("characters", "scenes"):
        return _RESP({"ok": False, "error": "不认识的类型：" + kind}, 400)
    old = asset_core.get_asset(sid, kind, oid)
    if not old:
        return _RESP({"ok": False, "error": "找不到 " + oid}, 404)
    allowed = (asset_core.CHARACTER_FIELDS if kind == "characters"
               else asset_core.SCENE_FIELDS)
    patch = asset_core.canonical_character_patch(d) if kind == "characters" else d
    asset_core.apply_fields(old, patch, allowed)
    if kind == "characters":
        asset_core.refresh_character_anchors(old)
    old["edited_by_user"] = True
    old["updated"] = time.time()
    asset_core.save_asset(sid, kind, old)
    return _RESP({"ok": True, "data": old})


@post({'prefix': '/api/story/', 'suffix': '/characters', 'count': 4})
def rec_char_create(h, path, d):
    sid = path.split("/")[3]
    if not story_core.get_story(sid):
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    return _RESP({"ok": True, "data": asset_core.create_character(sid, d)})


@post({'prefix': '/api/story/', 'suffix': '/scenes', 'count': 4})
def rec_scene_create(h, path, d):
    sid = path.split("/")[3]
    if not story_core.get_story(sid):
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    return _RESP({"ok": True, "data": asset_core.create_scene(sid, d)})
