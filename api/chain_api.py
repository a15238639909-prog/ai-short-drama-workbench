# -*- coding: utf-8 -*-
"""chain_api 域路由（由 server.py 的 do_POST 拆分而来）。"""
from api import post
from api._shared import _RESP, ctx
globals().update(ctx())


@post("/api/chain/presets")
def r_021(h, path, d):
    from cores import chain_core
    return _RESP({"ok": True, "data": {
        "presets": chain_core.PRESETS, "speeds": chain_core.SPEEDS,
        "behavior_promise": chain_core.BEHAVIOR_PROMISE}})

@post("/api/chain/draft")
def r_022(h, path, d):
    from cores import chain_core
    dr = chain_core.draft_create(d.get("one_line"), d.get("presets"))
    dr["preview"] = chain_core.draft_preview(dr)
    store.save_json(store.DATA / "drafts" / (dr["draft_id"] + ".json"), dr)
    return _RESP({"ok": True, "data": {"draft": dr}})

@post("/api/chain/commit")
def r_023(h, path, d):
    from cores import chain_core
    story = chain_core.commit_draft(str(d.get("draft_id") or ""))
    return _RESP({"ok": True, "data": {"story": story}})

@post("/api/chain/edit")
def r_024(h, path, d):
    from cores import chain_core
    view = chain_core.chain_edit(str(d.get("story_id") or ""),
                                 str(d.get("step") or ""), d.get("data"))
    return _RESP({"ok": True, "data": {"view": view}})

@post("/api/chain/checks")
def r_025(h, path, d):
    from cores import chain_core
    return _RESP({"ok": True, "data": chain_core.chain_checks(
        str(d.get("story_id") or ""))})
