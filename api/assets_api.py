# -*- coding: utf-8 -*-
"""assets_api 域路由（由 server.py 的 do_POST 拆分而来）。"""
from api import post
from api._shared import _RESP, ctx
globals().update(ctx())


@post("/api/asset/adopt")
def r_038(h, path, d):
    # 页面发的是 visual_id，这个路由原来只认 asset_id——采用从来没成功过，
    # 界面上的「已采用」全是前端自己画的。两个键名都收。
    aid = str(d.get("asset_id") or d.get("visual_id") or "")
    ver = d.get("version")
    sid = str(d.get("story_id") or "")
    if not sid or not aid:
        return _RESP({"ok": False, "error": "缺少 story_id/asset_id"}, 400)
    v = asset_core.adopt(sid, aid, ver)
    return _RESP({"ok": True, "data": {"asset": v}})


@post("/api/asset/upload")
def r_upload(h, path, d):
    """上传设定图。上传时必须说清归属：人物还是场景、哪一个。

    存进图库当候选（label=上传），资源页可以采用，分镜页可以按段挑选。
    类型隔离在归属关系里天然成立——图挂在谁名下，就只会出现在谁的卡里。
    """
    import base64, os, time
    sid = str(d.get("story_id") or "")
    okind = str(d.get("owner_kind") or "")          # character / scene
    oid = str(d.get("owner_id") or "")
    b64 = str(d.get("data_b64") or "")
    if not (sid and oid and b64) or okind not in ("character", "scene"):
        return _RESP({"ok": False, "error": "缺少 story_id/owner_kind/owner_id/data_b64"}, 400)
    ext = os.path.splitext(str(d.get("filename") or ""))[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        return _RESP({"ok": False, "error": "只收 png/jpg/webp"}, 400)
    try:
        raw = base64.b64decode(b64.split(",")[-1])
    except Exception:
        return _RESP({"ok": False, "error": "图片数据解不开"}, 400)
    if len(raw) > 30 * 1024 * 1024:
        return _RESP({"ok": False, "error": "图片超过 30MB"}, 400)
    out_dir = os.path.join("outputs", "uploads")
    os.makedirs(out_dir, exist_ok=True)
    n = len([v for v in asset_core.list_assets(sid, "visuals") if v.get("owner_id") == oid])
    # 文件名带上版本号和毫秒：只精确到秒的话，给同一个人连传两张（换图）会**同名覆盖**，
    # 两条记录指向同一个文件，旧图在盘上被抹掉、退不回去；而且路径没变，
    # 浏览器还会拿缓存显示旧图，用户以为换图没生效（2026-09-05 实测）。
    fp = os.path.abspath(os.path.join(
        out_dir, "%s_v%d_%d%s" % (oid, n + 1, int(time.time() * 1000), ext)))
    while os.path.exists(fp):                       # 极端情况下再撞就往后挪
        fp = os.path.abspath(os.path.join(
            out_dir, "%s_v%d_%d%s" % (oid, n + 1, int(time.time() * 1000) + 1, ext)))
    with open(fp, "wb") as f:
        f.write(raw)
    v = asset_core.add_approved_visual(
        sid, okind + "_master", oid, n + 1, fp, style_profile="上传")
    # 标记来源，图库里显示「上传」而不是「候选」
    from cores import store as _store
    v["source"] = "upload"
    _store.save_json(asset_core.assets_path(sid, "visuals") / (v["visual_id"] + ".json"), v)
    if d.get("adopt"):
        v = asset_core.adopt(sid, v["visual_id"])                   # P406：上传即采用（成品页「上传替换」）
    return _RESP({"ok": True, "data": {"visual": v}})
