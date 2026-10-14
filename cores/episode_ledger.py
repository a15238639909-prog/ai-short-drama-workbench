# -*- coding: utf-8 -*-
"""episode_ledger.py — 话间状态账本（P445③，2026-09-20）。

项目固定后，话与话之间不变的是设定（人物卡、场景卡、风格），会变的是状态：每个人此刻的衣着、伤和包扎、手里的东西、
互相的称呼；场景里新添的东西（帐篷、烤架、晾鱼架）；结尾的时间和天气。第一话是从正文设计出来的，往后必须从上一话
结束时更新，不重新设计——否则第二话把「张哥」写回「张明哥」，第三话就不知道谁包扎过。

build(sid, ep, q)：读这一话正文（+ 导演分段最后一段的结束状态）→ 一次模型调用出固定行 → 解析存进 episode["ledger"]。
text(sid, ep)：给下一话的写手 / 导演 / 提示词写手看的【上一话结束时】文本。"""
import re
import time

_HEAD = re.compile(r"^(人物|场景|结尾)\s*[：:]\s*(.*)$")


def _q_default():
    from .authoring import _q
    return _q


def instruction(names):
    return ("读【这一话正文】，只输出结尾那一刻的状态，每行一条，行首字样固定：\n"
            "人物：<名字>｜衣着=<此刻身上穿的和破损情况，一句>｜伤=<伤在哪、包没包扎；没有写 无>｜手里=<东西；没有写 无>｜称呼=<他怎么叫别人，如 叫张明「张哥」；没有写 无>\n"
            "场景：<场景名>｜新添=<这一话在这里新搭/新放的东西；没有写 无>\n"
            "结尾：时间=<清晨/白天/傍晚/夜>｜天气=<…>｜各人在哪=<名字在哪，顿号分开>\n"
            "人物只写这些人：%s，每人一行；只写正文里写了的，没写的填 无；不解释。" % "、".join(names))


def _kv(cell):
    """「衣着=…」「衣着＝…」「衣着：…」都认（P448④：模型写全角等号时整份账本曾为空）。"""
    parts = re.split(r"\s*[=＝：:]\s*", str(cell or "").strip(), 1)
    k = parts[0].strip()
    v = (parts[1] if len(parts) > 1 else "").strip("。 ")
    return k, v


def parse(raw, names, scene_names):
    led = {"people": {}, "scenes": {}, "when": {}, "at": time.time()}
    for ln in str(raw or "").replace("<", "").replace(">", "").replace("＜", "").replace("＞", "").splitlines():   # 模型抄的占位尖括号
        m = _HEAD.match(ln.strip().lstrip("·-* "))
        if not m:
            continue
        kind, body = m.group(1), m.group(2)
        cells = [c.strip() for c in re.split(r"[｜|]", body) if c.strip()]
        if kind == "人物" and cells:
            nm = next((n for n in sorted(names, key=len, reverse=True) if n in cells[0]), "")
            if not nm:
                continue
            d = {}
            for c in cells[1:]:
                k, v = _kv(c)
                if k in ("衣着", "伤", "手里", "称呼") and v and v != "无":
                    d[k.strip()] = v
            led["people"][nm] = d
        elif kind == "场景" and cells:
            sn = next((s for s in sorted(scene_names, key=len, reverse=True) if s in cells[0]), cells[0][:12])
            for c in cells[1:]:
                k, v = _kv(c)
                if k == "新添" and v and v != "无":
                    led["scenes"][sn] = v
        elif kind == "结尾":
            for c in cells:
                k, v = _kv(c)
                if k in ("时间", "天气", "各人在哪") and v:
                    led["when"][k] = v
    return led


def build(sid, ep, q=None):
    from . import story_core, saga_core, asset_core
    q = q or _q_default()
    sg = saga_core.get_saga(sid)
    e = saga_core.episode(sg, int(ep)) or {}
    prose = str(e.get("prose") or "").strip()
    if not prose:
        return None
    names = [str(c.get("name") or "") for c in (asset_core.list_assets(sid, "characters") or []) if c.get("name")]
    scene_names = [str(c.get("name") or "") for c in (asset_core.list_assets(sid, "scenes") or []) if c.get("name")]
    # 导演分段最后一段的结束状态：手里的东西、姿势、在哪（程序写的，比模型猜的准）
    tail = ""
    try:
        tl = saga_core.ep_timeline(sid, int(ep)) or {}
        sl = ((tl.get("slicing") or {}).get("slices") or [])
        d = (sl[-1].get("director") or {}) if sl else {}
        from .episode_contract import ending_text
        if ending_text(d):
            tail = "\n【导演分段最后一段结束时（分镜记录，人物进出和状态以此为准）】" + ending_text(d)
            tail += "；地点：" + str(sl[-1].get("scene_hint") or "")
    except Exception:
        pass
    raw = q(instruction(names), "【这一话正文】\n" + prose[-3500:] + tail, mt=900, temperature=0.2)
    led = parse(raw, names, scene_names)
    led["raw"] = str(raw or "")[:2000]
    led['continuity'] = ending_snapshot(sid, ep)
    e["ledger"] = led
    saga_core.save_saga(sid, sg)
    return led


def get(sid, ep):
    from . import saga_core
    e = saga_core.episode(saga_core.get_saga(sid), int(ep)) or {}
    return e.get("ledger") if isinstance(e.get("ledger"), dict) else None


def text(sid, ep):
    """第 ep 话结束时的账本文本（给第 ep+1 话用）。没有账本返回空串。"""
    led = get(sid, ep)
    led = led or {}
    lines = []
    for nm, d in (led.get("people") or {}).items():
        bits = ["%s=%s" % (k, d[k]) for k in ("衣着", "伤", "手里", "称呼") if d.get(k)]
        if bits:
            lines.append("· %s：%s" % (nm, "；".join(bits)))
    for sn, v in (led.get("scenes") or {}).items():
        lines.append("· 场景「%s」新添：%s" % (sn, v))
    w = led.get("when") or {}
    if w:
        lines.append("· 结尾：" + "；".join("%s=%s" % (k, w[k]) for k in ("时间", "天气", "各人在哪") if w.get(k)))
    # Older source-owned directors wrote target_state while the ledger read only
    # legacy end_state dicts. Read the current timeline too, including old saves.
    continuity = ending_snapshot(sid, ep) or led.get('continuity') or {}
    if continuity.get('end_state_text'):
        lines.append('· 上一话最后分镜结束状态：' + continuity['end_state_text'])
    if continuity.get('scene'):
        lines.append('· 上一话最后地点：' + continuity['scene'])
    absence = (continuity.get('presence') or {}).get('absent') or []
    if absence:
        lines.append('· 最后分镜原文明确不在场：' + '、'.join(absence) + '；须有回归过程才能重新出场')
    if continuity:
        lines.append('· 下一话须接续人数、位置、姿势、携带关系和道具归属；状态变化写出过渡。分镜状态是计划，实际尾帧由视频生成步骤对齐。')
    return ("【上一话（第 %d 话）结束时的状态——照这个接，不重新设定】\n" % int(ep)) + "\n".join(lines) if lines else ""


def ending_snapshot(sid, ep):
    """Return the exact final director contract without a model call."""
    from . import saga_core
    from .episode_contract import ending_text
    timeline = saga_core.ep_timeline(sid, int(ep)) or {}
    slices = ((timeline.get('slicing') or {}).get('slices') or [])
    if not slices:
        return {}
    last = slices[-1]
    director = last.get('director') or {}
    return {'end_state_text': ending_text(director), 'scene': str(last.get('scene_hint') or ''),
            'presence': director.get('presence') or {}}


def person_line(sid, ep, name):
    """给提示词写手的人物卡摘要用：这个人上一话结束时的衣着/伤/手里。"""
    led = get(sid, ep)
    d = ((led or {}).get("people") or {}).get(name) or {}
    bits = [d[k] for k in ("衣着", "伤") if d.get(k)]
    return "；".join(bits)
