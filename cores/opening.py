# -*- coding: utf-8 -*-
"""开场展示（P316，用户 2026-09-11：「每个故事的开场都默认先展示一下场景，描写一下场景和人物，然后再开始……
故事视频也要开始展示一下场景，让观众知道人物和场景是什么样的」）。

视频层：每话分镜最前面固定几段「开场」，零模型，提示词由代码从场景卡和人物卡拼：
  · 场景段：本话第一个场景的空场建立镜头（用场景参考板）
  · 人物段：这场戏在场、有卡的主要人物各一段亮相（用人设参考图），最多 3 人
规则：第 1 话必有；后面的话只在第一个场景是本话新出现的才给场景段，人物只给本话第一次出场的。
存在 timeline["opening"]：[{"k","kind","name","scene","chars","seconds","prompt","video",...}]，不进 scenes[].segments，
段号、出片前门、剧本核对都不受它影响；出片时先出开场段再出第 1 段（api.timeline._gen_next / saga_api._first_video_chain）。
"""
import re

SCENE_SECONDS = 4
CAST_SECONDS = 4
MAX_CAST = 3
MEDIA_KEYS = ("video", "video_hd", "size", "size_hd", "seed", "used_refs", "end_frame", "mode", "size_tier", "size_tier_hd", "ratio", "steps", "adopted")


def _segs(tl):
    return sorted([g for sc in ((tl or {}).get("scenes") or []) for g in (sc.get("segments") or [])],
                  key=lambda g: int(g.get("no") or 0))


def _prev_usage(sid, ep):
    """前面各话分镜里用过的场景名和人物名（判断"本话新出现"）。"""
    scenes, chars = set(), set()
    try:
        from . import saga_core as _sc
        saga = _sc.get_saga(sid) or {}
        for e in (saga.get("episodes") or []):
            no = int(e.get("no") or 0)
            if not no or no >= int(ep):
                continue
            tl = e.get("timeline") or {}
            for g in _segs(tl):
                if str(g.get("scene") or "").strip():
                    scenes.add(str(g.get("scene")).strip())
                for c in (g.get("chars") or []):
                    if str(c or "").strip():
                        chars.add(str(c).strip())
            for og in (tl.get("opening") or []):
                if og.get("kind") == "scene" and og.get("name"):
                    scenes.add(str(og["name"]))
                if og.get("kind") == "cast" and og.get("name"):
                    chars.add(str(og["name"]))
    except Exception:
        pass
    return scenes, chars


def _first(text, n=60):
    t = re.sub(r"\s+", "", str(text or ""))
    return t[:n].rstrip("，、；：")


def _scene_desc(card):
    """场景卡 → 一两句空间描写（设计稿优先：镇场主体 / 固有陈设 / 光）。"""
    bits = []
    for k in ("landmarks", "space", "contract_text"):
        v = str((card or {}).get(k) or "").strip()
        if v:
            bits.append(_first(v, 80))
            break
    fx = str((card or {}).get("fixtures") or "").strip()
    if fx:
        names = [ln.split("｜", 1)[0].strip() for ln in fx.splitlines() if ln.strip()]
        names = [n for n in names if n][:3]
        if names:
            bits.append("看得见" + "、".join(names))
    lt = str((card or {}).get("light") or "").strip()
    if lt:
        bits.append(_first(lt, 50))
    return "，".join(bits)


def _cast_desc(card):
    """人物卡 → 一句身份+外形（参考图管长相，这里只说身份和衣着，让镜头有东西写）。"""
    bits = []
    ct = str((card or {}).get("char_type") or "").strip()
    if ct:
        bits.append(ct)
    hair = str((card or {}).get("hair") or "").strip()
    if hair:
        bits.append(_first(hair, 24))
    cl = str((card or {}).get("clothing") or "").strip()
    if cl:
        bits.append(_first(cl, 40))
    return "，".join(bits)


def scene_prompt(scene_card, settings=None):
    """空场建立镜头：只绑场景板（Subject 1 = Picture 1），没有人。"""
    from . import authoring as _au
    name = str((scene_card or {}).get("name") or "").strip()
    desc = _scene_desc(scene_card)
    space = str((scene_card or {}).get("space") or (scene_card or {}).get("contract_text") or "")
    head = [_au._H3_SCENE_PIC % (1, 1, name),
            "画面里只有这个地方本身：建筑结构、固有陈设和光，没有人物出现。",
            _au._h3_cine(settings or {}, space, "")]
    body = ("镜头1（%d秒｜齐眼高机位｜缓慢横移推进）：全景。%s%s。镜头从画面一侧缓缓横移到另一侧，看清这个地方的样子，收在最显眼的主体上。"
            % (SCENE_SECONDS, name, ("，" + desc) if desc else ""))
    return "\n".join(head) + "\n" + body + "\n" + _au._H3_SILENT + "\n" + _au._H3_TAIL


def cast_prompt(card, scene_card, settings=None):
    """人物亮相：绑一个人（Subject 1）+ 这话第一个场景板（Subject 2）。"""
    from . import authoring as _au
    name = str((card or {}).get("name") or "").strip()
    sc_name = str((scene_card or {}).get("name") or "").strip()
    desc = _cast_desc(card)
    space = str((scene_card or {}).get("space") or (scene_card or {}).get("contract_text") or "")
    head = [_au._H3_BIND % (1, name, 1),
            _au.chars_lock_line([{"name": name}], [], False),
            "Subject 编号只到 S1 为止，这是人；再往后的 Subject 是**地方**（场景参考板），不是人也不是生物。"]
    if sc_name:
        head.append(_au._H3_SCENE_PIC % (2, 2, sc_name))
    head.append(_au._h3_cine(settings or {}, space, name))
    where = ("站在%s里" % sc_name) if sc_name else "站在这个地方"
    half = max(1, CAST_SECONDS // 2)
    body = ("镜头1（%d秒｜齐胸高机位｜缓慢推近）：%s全景。%s%s%s，身体微微侧向镜头，手上停着自然的小动作，头发和衣摆随微风轻轻动。\n"
            "镜头2（%d秒｜齐眼高机位｜固定）：%s近景。%s抬眼看向前方，神情平静，看得清脸、发型和衣着。"
            % (half, name, name, where, ("，" + desc) if desc else "", CAST_SECONDS - half, name, name))
    return "\n".join(head) + "\n" + body + "\n" + _au._H3_SILENT + "\n" + _au._H3_TAIL


def build(sid, ep, old=None, settings=None, tl=None):
    """按这一话分镜的第 1 段（场景 + 在场人物）建开场段列表。old：旧列表，提示词没变的段保住已出的视频。
    tl：还没存盘的新分镜表（build_timeline 建表时传进来）；不传就读盘上的。"""
    from . import saga_core as _sc, asset_core as _ac, story_core as _st
    ep = int(ep or 1)
    if tl is None:
        tl = _sc.ep_timeline(sid, ep) or {"scenes": []}
    segs = _segs(tl)
    if not segs:
        return []
    settings = settings or ((_st.get_story(sid) or {}).get("settings") or {})
    first = segs[0]
    sc_name = str(first.get("scene") or "").strip()
    chars = [str(c or "").strip() for c in (first.get("chars") or []) if str(c or "").strip()]
    scenes_all = {str(x.get("name") or "").strip(): x for x in (_ac.list_assets(sid, "scenes") or [])}
    cards_all = {str(x.get("name") or "").strip(): x for x in (_ac.list_assets(sid, "characters") or [])}
    sc_card = scenes_all.get(sc_name)
    prev_scenes, prev_chars = _prev_usage(sid, ep) if ep > 1 else (set(), set())
    out = []
    if sc_card and (ep == 1 or sc_name not in prev_scenes):
        out.append({"kind": "scene", "name": sc_name, "scene": sc_name, "chars": [], "no_chars": True,
                    "seconds": SCENE_SECONDS, "prompt": scene_prompt(sc_card, settings)})
    n_cast = 0
    for nm in chars:
        card = cards_all.get(nm)
        if not card or n_cast >= MAX_CAST:
            continue
        if ep > 1 and nm in prev_chars and str(card.get("first_episode") or "1") != str(ep):
            continue
        out.append({"kind": "cast", "name": nm, "scene": sc_name if sc_card else "", "chars": [nm],
                    "seconds": CAST_SECONDS, "prompt": cast_prompt(card, sc_card, settings)})
        n_cast += 1
    for i, og in enumerate(out, 1):
        og["k"] = i
        og["video"] = ""
    # 提示词没变的段保住成片
    for og in out:
        o = next((x for x in (old or []) if x.get("kind") == og["kind"] and x.get("name") == og["name"]), None)
        if o and str(o.get("prompt") or "") == og["prompt"]:
            for k in MEDIA_KEYS:
                if o.get(k) not in (None, "", [], {}):
                    og[k] = o[k]
    return out


def ensure(sid, ep, rebuild=False):
    """timeline 里没有开场段就建（老分镜表兼容）。返回列表。"""
    from . import saga_core as _sc
    ep = int(ep or 1)
    tl = _sc.ep_timeline(sid, ep) or {"scenes": []}
    if tl.get("opening") is not None and not rebuild:
        return tl.get("opening") or []
    op = build(sid, ep, old=tl.get("opening"))
    tl["opening"] = op
    _sc.save_ep_timeline(sid, ep, tl)
    return op


def enabled(settings=None):
    """开关：P321 用户定——开场展示是**写故事**的事（首次出现的场景/人物先描写），不另出视频。默认关；项目设定 opening_show=True 才开。"""
    v = (settings or {}).get("opening_show")
    return v in (True, 1, "1", "on", "开", "是")
