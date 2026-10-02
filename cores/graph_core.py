# -*- coding: utf-8 -*-
"""graph_core.py — 项目节点图数据（Phase 2）。

唯一职责：从 story/asset/narrative/productions 数据构建项目图节点与连线，
并把画布坐标/备注持久化到 data/graph/<STORY_ID>.json。层级由数据推导，前端不硬编码。
"""
import time
from pathlib import Path
from . import store, story_core, narrative_core, asset_core

ROOT = Path(__file__).resolve().parent.parent
_ROW = {"project": 0, "character": 1, "scene_asset": 1, "scene": 2,
        "comic": 3, "video": 3, "novel": 3, "album": 3}
_PREFIX_KIND = {"COMIC": "comic", "NOVEL": "novel", "ALBUM": "album", "VIDEO": "video"}


def _rel_media(path):
    """把 ROOT 下的绝对路径转成 /files/ 相对地址；文件不存在返回空串。"""
    p = Path(str(path or ""))
    if not p.exists():
        return ""
    try:
        return str(p.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except Exception:
        return ""


def graph_path(story_id):
    return store.DATA / "graph" / (story_id + ".json")


def _pos(graph, node_id, row, col):
    saved = ((graph.get("nodes") or {}).get(node_id)) or {}
    return {"x": saved.get("x", 40 + col * 280),
            "y": saved.get("y", 24 + row * 235),
            "note": saved.get("note", "")}


def build_graph(story_id):
    """唯一负责：为单个故事生成节点图（project/character/scene_asset/scene/comic/video/novel/album）。"""
    story = story_core.get_story(story_id) or {}
    graph = store.load_json(graph_path(story_id), {}) or {}
    nodes, edges = [], []
    pid = "node_" + story_id + "_project"
    nodes.append({"id": pid, "kind": "project",
                  "title": story.get("title") or story_id,
                  "status": story.get("production_stage") or "draft",
                  "version": "", "cover": "", "thumbs": [],
                  "summary": story.get("one_line") or "",
                  "parent_id": None, "story_id": story_id, "page": "project",
                  **_pos(graph, pid, 0, 0)})
    rows = {}

    def emit(kind, node_id, title, status, version, thumbs, summary, page, extra=None):
        row = _ROW.get(kind, 2)
        col = rows.get(kind, 0)
        rows[kind] = col + 1
        n = {"id": node_id, "kind": kind, "title": title, "status": status,
             "version": version, "cover": thumbs[0] if thumbs else "", "thumbs": thumbs,
             "summary": summary, "parent_id": pid, "story_id": story_id, "page": page,
             "extra": extra or {}, **_pos(graph, node_id, row, col)}
        nodes.append(n)
        edges.append({"from": pid, "to": node_id})

    for c in asset_core.list_assets(story_id, "characters"):
        cid = c.get("character_id")
        adopted = next((v for v in asset_core.list_assets(story_id, "visuals")
                        if v.get("owner_id") == cid and v.get("status") == "adopted"), None)
        thumbs = [_rel_media(adopted.get("path"))] if adopted else []
        emit("character", "node_" + story_id + "_char_" + cid,
             c.get("name") or cid,
             "adopted" if adopted else "candidate",
             ("V" + str(adopted["visual_version"])) if adopted else "",
             thumbs, "；".join(x for x in [c.get("char_type"), c.get("look")] if x)[:70],
             "assets", {"character_id": cid})

    for s in asset_core.list_assets(story_id, "scenes"):
        sid = s.get("scene_id")
        adopted = next((v for v in asset_core.list_assets(story_id, "visuals")
                        if v.get("owner_id") == sid and v.get("status") == "adopted"), None)
        thumbs = [_rel_media(adopted.get("path"))] if adopted else []
        emit("scene_asset", "node_" + story_id + "_sceneasset_" + sid,
             s.get("name") or sid,
             "adopted" if adopted else "candidate",
             ("V" + str(adopted["visual_version"])) if adopted else "",
             thumbs, (s.get("contract_text") or "")[:70],
             "assets", {"scene_id": sid})

    ep = narrative_core.load_episode(story_id, story.get("current_episode") or 1) or {}
    previs = story.get("previs") or {}
    for i, u in enumerate((ep.get("units") or []), 1):
        uid = u.get("unit_id") or ("UNIT_%03d" % i)
        emit("scene", "node_" + story_id + "_scene_" + uid,
             "Scene %02d %s" % (i, (u.get("purpose") or "")[:14]),
             "脚本完成" if previs.get("story_script") else "未开始",
             "", [],
             (u.get("key_moment") or u.get("result") or "")[:70],
             "script", {"unit_id": uid})

    for fp in (store.DATA / "productions").glob("*.json"):
        p = store.load_json(fp, None)
        if not p or p.get("story_id") != story_id:
            continue
        prefix = str(p.get("production_id") or "").split("_", 1)[0].upper()
        kind = _PREFIX_KIND.get(prefix, str(p.get("kind") or "unknown").lower())
        if kind not in _ROW:
            kind = "novel"
        files = p.get("files") or []
        thumbs = []
        if kind == "comic":
            thumbs = [_rel_media(f) for f in files if str(f).lower().endswith(".png")][:4]
            thumbs = thumbs or [_rel_media(f) for f in files if str(f).lower().endswith(".pdf")][:1]
        elif kind == "album":
            thumbs = [_rel_media(f) for f in files if str(f).lower().endswith((".png", ".jpg", ".jpeg"))][:3]
        first = next((f for f in files if f), p.get("pdf") or "")
        emit(kind, "node_" + story_id + "_prod_" + p["production_id"],
             p["production_id"], p.get("status") or "completed",
             "V1" if p.get("adopted") else "",
             thumbs, "%s ｜ %d 个文件" % (p.get("kind"), len(files)),
             "productions", {"path": first, "production_id": p["production_id"]})

    return {"story_id": story_id, "nodes": nodes, "edges": edges,
            "updated": graph.get("updated") or 0}


def save_node(story_id, node_id, data):
    """唯一负责：保存节点画布坐标与备注到 data/graph/<STORY_ID>.json。"""
    if not story_id or not node_id:
        raise ValueError("缺少 story_id 或 node_id")
    g = store.load_json(graph_path(story_id), {}) or {}
    g.setdefault("nodes", {})[node_id] = {
        "x": data.get("x"), "y": data.get("y"), "note": data.get("note") or ""}
    g["updated"] = time.time()
    store.save_json(graph_path(story_id), g)
    return {"node_id": node_id, "saved": True, "updated": g["updated"]}
