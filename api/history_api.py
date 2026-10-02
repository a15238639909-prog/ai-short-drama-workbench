# -*- coding: utf-8 -*-
"""history_api —— 生成历史：把各工具出的图/视频汇到一处，能看提示词、能存进资源。

图片其实都从同一套 ComfyUI 出，再各自复制到 outputs/krea|video；legacy 的
「出图词/虚拟人物/视频导演台」把图+prompt.txt 存在「视频提示词存档」里。
所以扫这几处目录 + 读 gen_history 记录，就能覆盖所有工具的产物。
"""
import os
import time
import urllib.parse
from pathlib import Path

from api import get, post
from api._shared import _RESP, ctx
globals().update(ctx())

from cores import gen_history

ROOT = Path(__file__).resolve().parent.parent

# 扫哪些目录。第一组在 v41 根下（/files/ 直接能取）；其余在根外（走 /api/history/file）。
_V41_DIRS = [ROOT / "outputs" / "krea", ROOT / "outputs" / "video", ROOT / "outputs" / "krea_edit"]
try:
    from cores import paths as _paths
    _LEGACY_ROOT = Path(_paths.get("legacy_root"))
except Exception:
    _LEGACY_ROOT = Path(r"D:\小说写作台")
_LEGACY_ARCHIVE = _LEGACY_ROOT / "视频提示词存档"
# 四个工具（问AI/虚拟人物/出图词/视频导演台）各自的落盘处——它们不写 gen_history，
# 提示词分别躺在：任务目录的 prompt.txt / task.json，或直出图的文件名里。
_LEGACY_VIDEO_OUT = _LEGACY_ROOT / "视频输出"            # 🎥 视频导演台四模式成片
_LEGACY_PROMPT_IMG = _LEGACY_ROOT / "漫画" / "提示词直出"  # 🎨 出图词「直接生成图片」
_LEGACY_VCHAR_IMG = _LEGACY_ROOT / "漫画" / "虚拟人物"     # 🎭 虚拟人物头像 / 人设图
try:
    _COMFY_OUTPUT = Path(_paths.get("comfy_output"))
except Exception:
    _COMFY_OUTPUT = Path(r"F:\ComfyUI-aki-v2\ComfyUI\output")
# /api/history/file 只允许读这些根下的文件，防目录穿越。
_FILE_WHITELIST = [_LEGACY_ARCHIVE, _LEGACY_VIDEO_OUT, _LEGACY_PROMPT_IMG,
                   _LEGACY_VCHAR_IMG, _COMFY_OUTPUT]

_IMG = (".png", ".jpg", ".jpeg", ".webp")
_VID = (".mp4", ".webm", ".mov")


def _kind_of(ext):
    e = ext.lower()
    if e in _IMG:
        return "image"
    if e in _VID:
        return "video"
    return ""


def _url_for(p):
    """在 v41 根下 → /files/相对路径；根外 → /api/history/file?p=绝对路径。"""
    try:
        rel = str(Path(p).resolve().relative_to(ROOT)).replace("\\", "/")
        return "/files/" + urllib.parse.quote(rel)
    except Exception:
        return "/api/history/file?p=" + urllib.parse.quote(str(Path(p).resolve()))


def _scan_dir(d, source, recurse=False):
    items = []
    if not d.exists():
        return items
    walker = d.rglob("*") if recurse else d.glob("*")
    for f in walker:
        try:
            if not f.is_file():
                continue
            kind = _kind_of(f.suffix)
            if not kind:
                continue
            items.append({"path": str(f), "kind": kind, "source": source,
                          "ts": f.stat().st_mtime})
        except Exception:
            pass
    return items


def _sidecar_prompt(f):
    """同目录里的提示词原文：存档和视频导演台都会写 prompt.txt；
    视频导演台还会写 task.json，request.prompt 里是真正送去生成的那一段。"""
    d = Path(f).parent
    try:
        pt = d / "prompt.txt"
        if pt.exists():
            t = pt.read_text(encoding="utf-8", errors="replace").strip()
            if t:
                return t[:4000]
    except Exception:
        pass
    try:
        tj = d / "task.json"
        if tj.exists():
            import json
            j = json.loads(tj.read_text(encoding="utf-8", errors="replace"))
            return str((j.get("request") or {}).get("prompt") or "").strip()[:4000]
    except Exception:
        pass
    return ""


def _name_prompt(f):
    """出图词「直接生成图片」归档成「<时刻>_<提示词>.png」，名字后半截就是提示词
    （app.py 那边截到 30 字，只能拿到这么多）。"""
    stem = Path(f).stem
    if "_" in stem:
        head, rest = stem.split("_", 1)
        if head.isdigit() and rest.strip():
            return rest.strip()
    return ""


def _video_meta(f):
    """视频导演台任务的档位信息：哪种模式、多少秒、多大。"""
    try:
        import json
        tj = Path(f).parent / "task.json"
        if not tj.exists():
            return {}
        j = json.loads(tj.read_text(encoding="utf-8", errors="replace"))
        mode = (j.get("request") or {}).get("mode") or j.get("mode") or ""
        return {k: v for k, v in {
            "mode": {"t2va": "文生视频", "i2va": "图生视频",
                     "fl2va": "首尾帧", "ref2va": "全能参考"}.get(mode, mode),
            "size": ("%sx%s" % (j.get("width"), j.get("height"))) if j.get("width") else "",
            "seconds": j.get("seconds"),
        }.items() if v}
    except Exception:
        return {}


@get("/api/history/list")
def history_list(h, path, q):
    """倒序列出历史。q: story_id(可选，仅排在前)、include_raw(1=连 ComfyUI 原始出图一起扫)、limit。"""
    qs = urllib.parse.parse_qs(q or "")
    include_raw = (qs.get("include_raw", ["0"])[0] == "1")
    limit = int((qs.get("limit", ["400"])[0]) or 400)
    offset = int((qs.get("offset", ["0"])[0]) or 0)                     # P402：翻页
    f_kind = (qs.get("kind", [""])[0] or "").strip()                     # image / video
    f_saved = (qs.get("saved", ["0"])[0] == "1")
    f_source = (qs.get("source", [""])[0] or "").strip()

    # 1) gen_history 记录（带提示词，最准），按相对路径建索引
    rec_by_path = {}
    for r in gen_history.records():
        rec_by_path[str(r.get("path") or "").replace("\\", "/")] = r

    # 2) 扫目录
    scanned = []
    for d in _V41_DIRS:
        scanned += _scan_dir(d, "v41", recurse=False)
    scanned += _scan_dir(_LEGACY_ARCHIVE, "存档", recurse=True)
    # 四个工具的产物（都在子目录里，要递归）
    scanned += _scan_dir(_LEGACY_VIDEO_OUT, "视频导演台", recurse=True)
    scanned += _scan_dir(_LEGACY_PROMPT_IMG, "出图词", recurse=True)
    scanned += _scan_dir(_LEGACY_VCHAR_IMG, "虚拟人物", recurse=True)
    if include_raw:
        scanned += _scan_dir(_COMFY_OUTPUT, "原始", recurse=False)

    # 3) 合并：文件为准，能配上 gen_history 记录就带上提示词/来源
    seen = set()
    items = []
    for s in scanned:
        ap = str(Path(s["path"]).resolve())
        if ap in seen:
            continue
        seen.add(ap)
        try:
            rel = str(Path(ap).relative_to(ROOT)).replace("\\", "/")
        except Exception:
            rel = ap.replace("\\", "/")
        rec = rec_by_path.get(rel)
        prompt = (rec or {}).get("prompt") or ""
        if not prompt:
            # 没进 gen_history 的（四个工具的产物）：先找同目录 prompt.txt/task.json，
            # 再退回文件名里那截。
            prompt = _sidecar_prompt(ap) or _name_prompt(ap)
        extra = (rec or {}).get("extra") or {}
        if s["source"] == "视频导演台":
            extra = dict(extra, **_video_meta(ap))
        items.append({
            "path": rel, "url": _url_for(ap), "kind": s["kind"],
            "prompt": prompt, "source": (rec or {}).get("source") or s["source"],
            "ts": s["ts"], "saved": bool((rec or {}).get("saved")),
            "story_id": (rec or {}).get("story_id") or "",
            "extra": extra,
        })

    items.sort(key=lambda x: x["ts"], reverse=True)
    sources = []
    for it in items:
        if it.get("source") and it["source"] not in sources:
            sources.append(it["source"])
    if f_kind:
        items = [x for x in items if x.get("kind") == f_kind]
    if f_saved:
        items = [x for x in items if x.get("saved")]
    if f_source:
        items = [x for x in items if x.get("source") == f_source]
    total = len(items)
    items = items[offset:offset + limit]
    return _RESP({"ok": True, "data": {"items": items, "count": len(items), "total": total, "offset": offset, "limit": limit, "sources": sources}})


@get("/api/history/file")
def history_file(h, path, q):
    """取 v41 根目录外、但在白名单目录下的历史文件（缩略图/视频）。"""
    qs = urllib.parse.parse_qs(q or "")
    p = (qs.get("p", [""])[0])
    if not p:
        return _RESP({"ok": False, "error": "缺 p"}, 400)
    try:
        target = Path(urllib.parse.unquote(p)).resolve()
    except Exception:
        return _RESP({"ok": False, "error": "路径非法"}, 400)
    if not any(str(target).startswith(str(w.resolve())) for w in _FILE_WHITELIST):
        return _RESP({"ok": False, "error": "不在允许的目录内"}, 403)
    if not target.is_file():
        return _RESP({"ok": False, "error": "文件不存在"}, 404)
    ext = target.suffix.lower()
    ctype = ("image/png" if ext == ".png" else "image/jpeg" if ext in (".jpg", ".jpeg")
             else "image/webp" if ext == ".webp" else "video/mp4" if ext == ".mp4"
             else "video/webm" if ext == ".webm" else "application/octet-stream")
    try:
        body = target.read_bytes()
    except Exception as e:
        return _RESP({"ok": False, "error": str(e)[:120]}, 500)
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)
    return None


def _resolve_src(rel_or_abs):
    """历史项的 path 还原成真实文件：根下的补全 ROOT，根外的原样。"""
    p = str(rel_or_abs or "")
    cand = (ROOT / p)
    if cand.exists():
        return cand.resolve()
    ap = Path(p).resolve()
    return ap if ap.exists() else None


@post("/api/history/save")
def history_save(h, path, d):
    """保存到资源：把这张图/视频复制进 outputs/saved/{sid}，标记已保存；
    若带了归属（人物/场景），再登记成该归属的设定图，能在资源页里采用。"""
    import shutil
    sid = str(d.get("story_id") or "")
    src_path = str(d.get("path") or "")
    src = _resolve_src(src_path)
    if not src:
        return _RESP({"ok": False, "error": "源文件找不到了"}, 404)
    keep_dir = ROOT / "outputs" / "saved" / (sid or "_")
    keep_dir.mkdir(parents=True, exist_ok=True)
    dst = keep_dir / ("%d_%s" % (int(time.time()), src.name))
    try:
        shutil.copy2(src, dst)
    except Exception as e:
        return _RESP({"ok": False, "error": "复制失败：" + str(e)[:120]}, 500)
    gen_history.mark_saved(src_path)

    visual = None
    okind = str(d.get("owner_kind") or "")
    oid = str(d.get("owner_id") or "")
    if sid and oid and okind in ("character", "scene") and src.suffix.lower() in _IMG:
        try:
            n = len([v for v in asset_core.list_assets(sid, "visuals") if v.get("owner_id") == oid])
            v = asset_core.add_approved_visual(sid, okind + "_master", oid, n + 1, str(dst.resolve()),
                                               style_profile="历史保存")
            v["source"] = "history"
            store.save_json(asset_core.assets_path(sid, "visuals") / (v["visual_id"] + ".json"), v)
            visual = v
        except Exception:
            pass
    return _RESP({"ok": True, "data": {"kept": str(dst.relative_to(ROOT)).replace("\\", "/"),
                                       "visual": visual}})
