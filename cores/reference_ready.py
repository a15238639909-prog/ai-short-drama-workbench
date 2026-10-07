"""Reference readiness uses adopted files, never a card's stale visuals field."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def adopted_paths(sid, kind=None):
    from . import asset_core
    paths = {}
    for v in asset_core.list_assets(sid, "visuals") or []:
        if v.get("status") != "adopted" or not v.get("path"):
            continue
        if kind and not str(v.get("kind") or "").startswith(kind):
            continue
        p = Path(str(v["path"]))
        candidates = [p] if p.is_absolute() else [ROOT / "outputs" / p, ROOT / p]
        for candidate in candidates:
            if candidate.is_file() and candidate.stat().st_size > 0:
                paths[str(v.get("owner_id") or "")] = str(candidate)
                break
    return paths


def _current_slices(sid, ep):
    from . import authoring
    frozen = authoring.frozen_slicing(sid, ep)
    _, e = authoring._ep(sid, ep, create=False)
    pics = str((e or {}).get("pictures") or "")
    if pics and frozen and frozen.get("sig") == authoring._pics_sig(pics):
        return frozen.get("slices") or None
    return None


def required_characters(sid, ep=None):
    from . import authoring, story_core
    cards = [c for c in authoring.cast_cards(sid) if not c.get("auto_incidental")]
    slices = _current_slices(sid, ep) if ep is not None else None
    if slices and all(isinstance(s.get("director"), dict) for s in slices):
        names = {n for s in slices for n in s["director"].get("cast", [])}
        cards = [c for c in cards if c.get("name") in names]
    _, camera = authoring.h3_char_order(cards, (story_core.get_story(sid) or {}).get("settings") or {})
    return [c for c in cards if c.get("name") != camera]


def scene_ids_for_episode(sid, ep):
    """None means no trustworthy plan; retain the previous conservative behaviour."""
    from . import asset_core, project_prompt, saga_core
    slices = _current_slices(sid, ep)
    if not slices:
        return None
    cards = {str(c.get("name") or ""): c for c in asset_core.list_assets(sid, "scenes") or []}
    ids = set()
    locations = [str(s.get("scene_hint") or "").strip() for s in slices]
    opening = (saga_core.ep_timeline(sid, ep) or {}).get("opening") or []
    locations.extend(str(s.get("scene") or "").strip() for s in opening if s.get("scene"))
    for raw in locations:
        name = raw if raw in cards else project_prompt.match_scene_name(raw, list(cards))
        if not name or name not in cards:
            return None
        ids.add(cards[name]["scene_id"])
    return ids


def ensure_characters(sid, ep, on_step=None):
    from . import authoring
    result = authoring.gen_char_images(sid, only_missing=True, on_step=on_step, ep=ep) or {}
    paths = adopted_paths(sid, "character")
    missing = [str(c.get("name") or "") for c in required_characters(sid, ep)
               if str(c.get("character_id") or "") not in paths]
    if missing:
        reason = "；".join(str(x) for x in result.get("failed", []))
        if result.get("skipped"):
            reason = "项目设置了只做文字（no_images），请关闭后再生成图片或视频"
        raise RuntimeError("出片已暂停，缺少可用人设图：%s。%s" % ("、".join(missing), reason))
    return result


def assert_references(sid, segments):
    from . import asset_core, authoring, story_core
    all_cards = asset_core.list_assets(sid, "characters") or []
    char_paths = adopted_paths(sid, "character")
    scene_paths = adopted_paths(sid, "scene")
    scene_cards = {c.get("name"): c for c in asset_core.list_assets(sid, "scenes") or []}
    settings = (story_core.get_story(sid) or {}).get("settings") or {}
    missing = []
    for g in segments:
        names = list(g.get("chars") or [])
        cards = [c for c in all_cards if c.get("name") in names]
        if not names and not g.get("no_chars") and not g.get("shot"):
            cards = required_characters(sid)
        order, camera = authoring.h3_char_order(cards, settings)
        missing.extend("人物「%s」" % c.get("name") for c in order
                       if c.get("name") != camera and str(c.get("character_id") or "") not in char_paths)
        known = {c.get("name") for c in all_cards}
        missing.extend("人物卡「%s」" % n for n in names if n not in known)
        scene = str(g.get("scene") or "").strip()
        if scene and str((scene_cards.get(scene) or {}).get("scene_id") or "") not in scene_paths:
            missing.append("场景「%s」" % scene)
    if missing:
        raise RuntimeError("缺少已采用且文件有效的参考图：%s。请补图或上传并采用后再生成视频。" % "、".join(dict.fromkeys(missing)))
