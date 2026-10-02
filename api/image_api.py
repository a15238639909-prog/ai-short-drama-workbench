# -*- coding: utf-8 -*-
"""P401：🎨 出图词——一句话或整段提示词 → 超清图（和人设图 / 场景图同一条链）。"""
import os
from pathlib import Path
from api import post
from api._shared import _RESP

ROOT = Path(__file__).resolve().parent.parent


@post("/api/image/generate")
def image_generate(h, path, d):
    """{text, kind: character|scene|free, story_id?, style?} → {path, url, prompt}。同步：出一张约 2～3 分钟。"""
    from cores import story_core, project_settings, prompt_writer, gen_history
    from cores.authoring import _strip_neg
    from models import krea_client
    text = str((d or {}).get("text") or "").strip()
    kind = str((d or {}).get("kind") or "free").strip()
    sid = str((d or {}).get("story_id") or "").strip()
    if not text:
        return _RESP({"ok": False, "error": "先写要画什么"}, 400)
    st = story_core.get_story(sid) if sid else None
    P = project_settings.get(st or {})
    style = str((d or {}).get("style") or "").strip()
    if style:
        P = dict(P, style=style)
    if kind in ("character", "scene"):
        prompt = prompt_writer.write(kind, text, P)
        prompt = _strip_neg(prompt)
    else:
        prompt = text
    try:
        _bw = int((d or {}).get("base_w") or 0)
        _bh = int((d or {}).get("base_h") or 0)
    except Exception:
        _bw, _bh = 0, 0
    if _bw and _bh and (_bw * _bh > 2560 * 2560 or min(_bw, _bh) < 256):
        return _RESP({"ok": False, "error": "出底尺寸超范围（长边最多 2560）"}, 400)
    try:
        res = krea_client.generate(prompt, base_w=_bw or None, base_h=_bh or None)     # P450：比例 / 清晰度
    except Exception as e:
        return _RESP({"ok": False, "error": "出图失败：%s" % str(e)[:200]}, 500)
    out = str(res.get("output_path") or "")
    _w = _h = 0
    try:
        import struct
        with open(out, "rb") as _f:
            _hd = _f.read(24)
        if _hd[:8] == b"\x89PNG\r\n\x1a\n":
            _w, _h = struct.unpack(">II", _hd[16:24])
    except Exception:
        pass
    try:
        rel = str(Path(out).resolve().relative_to(ROOT)).replace("\\", "/")
    except Exception:
        rel = out.replace("\\", "/")
    gen_history.record("image", out, prompt, source="出图词", story_id=sid,
                       extra={"kind": kind, "style": P.get("style") or ""})
    return _RESP({"ok": True, "data": {"path": rel, "url": "/files/" + rel, "prompt": prompt, "style": P.get("style") or "", "width": _w, "height": _h}})
