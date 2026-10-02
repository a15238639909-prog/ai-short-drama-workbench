# -*- coding: utf-8 -*-
"""P331 接缝 v2（用户 9-13 定：同场景无缝、换场景切镜、出场拍/入场拍、做过的事不再做）。

三条从 STORY_100 无缝接缝反推出来的必要条件，全部做成程序强制：
① 上一段最后一块＝接力帧：全景/中景、在场的人都在画面里、动作停在进行中；
② 下一段开头段＝上一段结束那一刻（由代码从接力状态生成，不让模型再编）；
③ 第一块＝进行中动作的下半截；已经做完的事（本场累计）不再演。

接力状态用固定模板（导演按模板写「这一段结束时」），这里解析成 dict：
  {"frame": "全景", "people": {名字: {"pos","facing","hands","posture","side"}}, "ongoing": "…", "done": [...], "text": 原文}
换场景（另一张场景板）不接末帧：上一段最后一块＝出场拍，下一段第一块＝入场拍。
全部只在 V41_SEAM=v2 时生效；不自动重出片（用户 9-13：太费时间，检查、尽量无缝就行）。
"""
import os
import re

WIDE_W = re.compile(r"全景|中景|中近景|远景|双人")
CLOSE_W = re.compile(r"特写|(?<!中)近景|微距|贴脸|贴近[^，。；]{0,6}(面部|脸)|聚焦[^，。；]{0,6}(面部|脸|侧脸|眼|手)")
LEAVE_W = r"[^。；]{0,10}(离开|出门|走出|退出|转身走|离场|出画|消失)"
EXIT_W = re.compile(r"转身[^，。；]{0,8}(走|门)|走向[^，。；]{0,6}门|推门|拉门|(推|拉|关|合|推动)(开|上|动)?[^，。；]{0,6}门|走出|出画|离开|门(扇)?(缓缓|轻轻)?(合|关|拉)(上|拢)|合拢|背影|迈向|跨出|跨过门槛|走进[^，。；]{0,6}门|进入[^，。；]{0,4}(浴室|房间|屋|门)|入内|进门|跟进|消失在")
META_W = re.compile(r"[^，。；]*(与原作一致|与原来一致|原来写的|原文一致|与原来的一致|保持一致|最后\s*[12一两]\s*秒[^，。；]*(无台词|没有台词|无声|不说话|静止无声)|无台词|没有人说话|均在画面内|都在画面里)[^，。；]*[，。；]?")
ECHO_W = re.compile(r"全景或中景|停在进行中的一刻|在场的每个人|接力帧|出场拍|入场拍|左右分布|位置、朝向|原来一致|原作")
ACT_W = re.compile(r"(走|站|坐|跪|蹲|转|抬|伸|握|托|推|拉|按|抱|靠|迈|低头|抬头|看|望|说|开口|滑|摸|扶|收|放|贴|环|搭|踩|踏|合|关|开|停|悬|指)")


def rewrite_ok(new, old):
    """守卫重写的块可不可用：不是指令回声、有实际动作、不比原块短太多。"""
    t = str(new or "")
    if not t.strip():
        return False
    if ECHO_W.search(t):
        return False
    body = re.sub(r"^\d+\s*[—\-–]\s*\d+\s*秒[：:]?", "", t.strip())
    if len(re.sub(r"\s", "", body)) < 30 or len(ACT_W.findall(body)) < 2:
        return False
    if len(body) < 0.4 * len(re.sub(r"^\d+\s*[—\-–]\s*\d+\s*秒[：:]?", "", str(old or "").strip())):
        return False
    return True


def strip_cast_sentences(text, names):
    """场景描述里混进的人物句（「苏蔓跨坐林澈腰间」）剔掉。"""
    names = [str(n) for n in (names or []) if n]
    if not names:
        return str(text or "")
    keep = []
    for sent in re.split(r"(?<=[。；])", str(text or "")):
        if sent.strip() and not any(n in sent for n in names):
            keep.append(sent)
    return "".join(keep).strip()


def reconcile_frame(tail, last_block):
    """尾句的景别按最后一块实际写的校正（模型常写 特写 而块是全景）。"""
    t = str(tail or "")
    if not t or not last_block:
        return t
    want = ""
    if WIDE_W.search(last_block) and not CLOSE_W.search(last_block):
        want = "全景" if "全景" in last_block else "中景"
    elif CLOSE_W.search(last_block) and not WIDE_W.search(last_block):
        want = "特写" if "特写" in last_block else "近景"
    if want:
        t = re.sub(r"(景别[：:]\s*)[^\n]*", lambda m: m.group(1) + want, t)
    return t


def merge_done(old_tail, new_tail, names):
    """重生成的尾句只按最后一块写「本段已完成」→ 把原尾句里的已完成项并回去。"""
    od = parse_relay(old_tail, names).get("done") or []
    nd = parse_relay(new_tail, names).get("done") or []
    allv = [x for x in od + nd if x]
    seen, out = set(), []
    for x in allv:
        if x not in seen:
            seen.add(x)
            out.append(x)
    if not out:
        return new_tail
    if re.search(r"(本段已完成|已完成)[：:]", new_tail):
        return re.sub(r"((?:本段已完成|已完成)[：:]\s*)[^\n]*", lambda m: m.group(1) + "；".join(out[:10]), new_tail)
    return new_tail.rstrip() + "\n· 本段已完成：" + "；".join(out[:10])


def regen_tail(last_block, names, q):
    """最后一块被重写后，按模板重新生成「这一段结束时」。"""
    ask = ("下面是一段视频提示词的最后一个时间块。按固定模板写它结束那一刻的状态，只输出模板，不解释：\n这一段结束时：\n· 景别：全景/中景\n" +
           "".join("· %s：位置｜面朝谁｜手里什么｜姿势｜画面左侧或右侧\n" % n for n in names) +
           "· 正在进行：（停在哪一刻的动作）\n· 本段已完成：（这一块里做完的事，用分号隔开）")
    try:
        t = str(q(ask, last_block, mt=400, temperature=0.2) or "").strip()
    except Exception:
        return ""
    i = t.find("这一段结束时")
    return t[i:].strip() if i >= 0 else ("这一段结束时：\n" + t)
DONE_SPLIT = re.compile(r"[；;，,、]")


def on():
    """P332：默认开（P7 转正）；V41_SEAM=off/v1/0 退回旧链路。"""
    v = str(os.environ.get("V41_SEAM") or "v2").strip().lower()
    return v not in ("off", "v1", "0", "no", "false")


# ─────────── 接力状态：模板解析 / 生成 ───────────

def parse_relay(tail, names):
    """「这一段结束时」模板 → dict。解析不出人就退回 {"text": tail}。"""
    t = str(tail or "").strip()
    t = re.sub(r"^\s*这一段结束时[：:]\s*", "", t)
    out = {"frame": "", "people": {}, "ongoing": "", "done": [], "text": t[:600]}
    names = [str(n) for n in (names or []) if n]
    for ln in t.splitlines():
        ln = ln.strip().lstrip("·•-*").strip()
        if not ln:
            continue
        m = re.match(r"^(景别|正在进行|本段已完成|已完成|已经做完)[：:]\s*(.*)$", ln)
        if m:
            k, v = m.group(1), m.group(2).strip()
            if k == "景别":
                out["frame"] = v[:40]
            elif k == "正在进行":
                out["ongoing"] = v[:120]
            else:
                out["done"] = [x.strip() for x in DONE_SPLIT.split(v) if x.strip()][:8]
            continue
        m = re.match(r"^([^：:｜|]{1,12})[：:]\s*(.*)$", ln)
        if m and any(n in m.group(1) for n in names):
            key = m.group(1).strip()
            nm = key if key in names else max([n for n in names if n in key], key=len)     # 林野 / 林野父亲：取最长
            parts = [x.strip() for x in re.split(r"[｜|]", m.group(2)) if x.strip()]
            side = next((x for x in parts if re.search(r"画面(左|右)", x)), "")
            out["people"][nm] = {"pos": parts[0] if parts else "", "facing": parts[1] if len(parts) > 1 else "",
                                 "hands": parts[2] if len(parts) > 2 else "", "posture": parts[3] if len(parts) > 3 else "",
                                 "side": side}
    return out


def done_in_script(done, chunk, names=()):
    """已完成清单只留画面稿这一段里真写了的事（导演编的不进清单）。"""
    keep = []
    for d in done or []:
        d = str(d).strip("。 ")
        if len(d) < 4:
            if d and d in str(chunk or ""):
                keep.append(d)
        elif repeated_done(str(chunk or ""), [d], names):
            keep.append(d)
    return keep


def relay_ok(relay):
    return bool(relay and relay.get("people"))


def opening_from_relay(relay, scene_name="", scene_space="", names=None):
    """同场景下一段的开头段：逐字复述上一段结束那一刻（条件②）。names：本段在场的人（不在场的不写）。"""
    if not relay_ok(relay):
        return ""
    ppl = []
    for nm, p in relay["people"].items():
        if names and nm not in names:
            continue
        bits = [x for x in (p.get("pos"), p.get("facing"), p.get("hands"), p.get("posture")) if x]
        s = "%s%s" % (nm, ("在" + bits[0]) if bits else "")
        if len(bits) > 1:
            s += "，" + "，".join(bits[1:])
        if p.get("side") and p["side"] not in s:
            s += "，在" + p["side"]
        ppl.append(s)
    if not ppl:
        return ""
    frame = relay.get("frame") or "中景"
    scene_space = strip_cast_sentences(scene_space, list(relay["people"].keys()))
    head = "%s%s。" % (scene_name or "", ("，" + str(scene_space or "")[:120].rstrip("。")) if scene_space else "")
    head += "上一段结束的那一刻就是本段 0 秒的画面（%s，在场的人都在画面里）：%s。" % (frame, "；".join(ppl))
    if relay.get("ongoing"):
        head += "此刻%s——本段第一块从这个动作的下半截开始，机位和景别与它相同。" % relay["ongoing"].rstrip("。")
    return head


def relay_text(relay):
    """给下一段导演看的【上一段结束时的状态】原文（模板文本）。"""
    return str((relay or {}).get("text") or "")


# ─────────── 给导演的接缝要求（进 user 消息） ───────────

def seam_brief(seam, cast_names):
    """seam = {"ep_first","ep_last","scene_changed","last_of_scene", "done": [...], "relay": dict|None}。"""
    if not seam:
        return ""
    changed = bool(seam.get("scene_changed"))
    lines = ["\n【接缝要求】"]
    if seam.get("ep_first") and isinstance(seam.get("relay"), dict) and seam["relay"].get("from_prev_ep"):
        # P343：新一话接着上一话
        lines.append("· 本段是**新一话的开头**：第一块先用一个全景交代地方；人物**已经在这里**，身上的东西、手里的东西、刚做完的事都按【上一话结束时的状态】来，"
                     "不重新介绍谁是谁，上一话做过的事不再做一遍。")
    elif seam.get("ep_first"):
        lines.append("· 本段是全片开头：第一块先用一个全景交代地方，再进人。")
    elif changed:
        lines.append("· 本段是**新地方的第一段**：第一块是入场拍——这个地方的全景，人物**已经在这里**，处于【上一段结束时的状态】里说的样子；"
                     "开场动作是上一段结束动作的另一半（上一段收在推门，这里从门被推开、人跨进来开始；上一段收在走远的背影，这里从人已到位开始）。"
                     "画面稿这一段没写「走进来」就不写走进来，直接做本段第一件事。")
    else:
        lines.append("· 本段**接着上一段同一个地方**：第一块的第一句是【上一段结束时的状态】里「正在进行」那个动作的下半截；"
                     "第一块的机位和景别和上一段最后一块相同；每个人的位置、朝向、画面左右原样继承，不重新介绍谁站在哪。")
    if seam.get("ep_last"):
        lines.append("· 本段是全片最后一段：最后一块收在一个能看见所有在场人物的中景或全景，动作收住。")
    elif seam.get("last_of_scene"):
        lines.append("· 本段是**这个地方的最后一段**：最后一块是出场拍——全景或中景，人物做完本场最后一件事后开始离开的动作"
                     "（转身走向门/推门/走出画面/门合上），最后 1 秒画面静止在门口、门或走远的背影上，没有台词。")
    elif seam.get("beat"):
        lines.append("· 最后一块停在这一拍的最后一个动作上（可以放慢、定住），不开始下一件事；最后 1 秒没有台词。")   # P387③
    else:
        lines.append("· 最后一块是**接力帧**：全景或中景，在场的每个人都在画面里，动作停在进行中的一刻（手悬着、门推到一半、迈出一步），最后 2 秒没有台词。"
                     + ("**接力帧要为下一段的第一件事摆好位置**——下一段画面稿开头是：「%s」，最后一块结束时每个人的位置、朝向、手里的东西要正好能接着做这件事。" % str(seam.get("next_head") or "")[:90] if seam.get("next_head") else ""))
    done = [str(x) for x in (seam.get("done") or []) if str(x).strip()]
    if seam.get("beat"):
        # P385：节拍段——「已做完」不是禁词。模型把「不许再演：…托住…」理解成不能写「托住」，写出「双手仍老者的手臂」
        prev_b = [str(x) for x in (seam.get("prev_script") or []) if str(x).strip()]
        if done or prev_b:
            lines.append("· **上一拍已经做完的事**：%s。现在画面里是做完后的样子，不从头再做一遍；"
                         "持续中的姿势和手上的动作（托着、扶着、背着、握着）照【上一段结束时的状态】原样写出来，这些词可以用。" % "；".join((done + prev_b)[:8]))
        done, prev_b = [], []
        seam = dict(seam, prev_script=[])
    if done:
        lines.append("· **上一段已经做完、本段不再做一遍的事**：%s（画面里它们已经是做完的状态）。本段画面稿里写的动作照常演出来，不要提前写成已经做完。" % "；".join(done[:10]))
    prev_script = [str(x) for x in (seam.get("prev_script") or []) if str(x).strip()]
    if prev_script:
        lines.append("· **上一段剧本里已经演过的原句（不许再演，画面里它们已经是做完的状态）**：%s。" % "；".join(prev_script[:10]))
    next_script = [str(x) for x in (seam.get("next_script") or []) if str(x).strip()]
    if next_script:
        lines.append("· **下一段才发生的事，本段一件都不做**（只把人摆到能开始做的位置）：%s。" % "；".join(next_script[:6]))
    if len([n for n in (cast_names or []) if n]) >= 3:
        lines.append("· 三个人以上同框时，每一块最多写两个人当主体（谁在说、谁在回应），其余的人只写站在哪、面朝谁，不写表情和小动作。")
    lines.append("· 「这一段结束时」按下面的固定模板写（每行一条，用「｜」分隔）：\n"
                 "  这一段结束时：\n  · 景别：全景/中景\n" +
                 "".join("  · %s：位置｜面朝谁｜手里什么｜姿势｜画面左侧或右侧\n" % n for n in (cast_names or [])[:4]) +
                 "  · 正在进行：（停在哪一刻的动作）\n  · 本段已完成：（本段做完的事，用分号隔开：脱下的衣服、走过的路、推过的门、说过的关键话）")
    return "\n".join(lines)


# ─────────── 守卫（文本层，确定性判 + 只改一块） ───────────

def _blocks(body):
    from . import authoring as au
    return au._tl_blocks(body)


def _join(head, blocks, tail):
    return "\n\n".join([head] + [t for _, _, t in blocks] + ([tail] if tail else [])).strip()


def _says_kept(new, old):
    olds = [l for l in str(old).splitlines() if "says:" in l]
    return all(sl in new for sl in olds)


def present_names(txt, names):
    """这一块里真在画面的人：提到了、且没写他离开/出画。"""
    return [n for n in names if n and n in txt and not re.search(re.escape(n) + LEAVE_W, txt)]


def last_block_relay_ok(txt, names):
    """接力帧：宽景、不收特写、在场的人（没离场的）都在这一块里。"""
    if not WIDE_W.search(txt) or CLOSE_W.search(txt):
        return False
    stay = [n for n in names if n and not re.search(re.escape(n) + LEAVE_W, txt)]
    return all(n in txt for n in stay)


def guard_relay_frame(body, names, say_lines, q, close=False):
    """同场景（非本场最后一段）：最后一块不合格 → 只重写最后一块一次。close=True：全片最后一段，动作收住。"""
    blocks, head, tail = _blocks(body)
    if not blocks:
        return body, False
    a, b, last = blocks[-1]
    if last_block_relay_ok(last, names):
        return body, True
    ppl = "、".join(present_names(last, names) or [n for n in names if n])
    if close:
        ask = ("下面是一段视频提示词的最后一个时间块，也是全片的最后一块。只重写这一块：改成**固定机位的全景或中景**，在场的每个人（%s）都在画面里，"
               "各自的位置、朝向、画面左右和这一块原来写的一致；动作自然收住（放下手、站定、看向对方），最后 2 秒没有人说话。"
               "这一块原有的台词行一字不动（如果有），开头保留「%d—%d秒：」。只输出这一块。" % (ppl, a, b))
    else:
        ask = ("下面是一段视频提示词的最后一个时间块，它是下一段视频的接力帧。只重写这一块：改成**固定机位的全景或中景**，"
               "在场的每个人（%s）都在画面里，各自的位置、朝向、画面左右和这一块原来写的一致；动作停在进行中的一刻（手悬着、门推到一半、迈出一步），"
               "最后 2 秒没有人说话。这一块原有的台词行一字不动（如果有），开头保留「%d—%d秒：」。只输出这一块。" % (ppl, a, b))
    try:
        new = str(q(ask, last, mt=650, temperature=0.3) or "").strip()
    except Exception:
        new = ""
    from . import authoring as au
    new = au._tl_first_block_only(new)
    new = strip_meta(new)
    if new and au._TL_BLOCK.match(new) and _says_kept(new, last) and last_block_relay_ok(new, names) and rewrite_ok(new, last):
        blocks[-1] = (a, b, au._tl_clean(new))
        tail2 = regen_tail(blocks[-1][2], names, q)                     # 尾句按新块重写，下一段照它开场
        if tail2 and parse_relay(tail2, names).get("people"):
            tail = merge_done(tail, tail2, names)
        tail = reconcile_frame(tail, blocks[-1][2])
        return _join(head, blocks, tail), True
    return body, False


def guard_relay_align(body, names, next_head, q):
    """接力帧的站位要能直接开始下一段第一句：模型判一次，不能就只改最后一块（改完尾句重生成）。返回 (body, 改没改)。"""
    nh = str(next_head or "").strip()
    blocks, head, tail = _blocks(body)
    if not blocks or not nh:
        return body, False
    a, b, last = blocks[-1]
    ask = ("下面是一段视频提示词的最后一个时间块。下一段视频的第一句要做的事是：「%s」。\n"
           "判断：这一块结束时每个人的位置、前后关系、朝向、手里的东西，能不能**不挪位、不转身**就直接开始做下一段那件事？"
           "能 → 只回「可以」两个字。不能 → 只重写这一块：保持固定机位的全景或中景、在场的人（%s）都在画面里、原有台词行一字不动、开头保留「%d—%d秒：」，"
           "把这一块的后半段改成人物自然地走到/转到能直接开始下一段那件事的位置和朝向，动作停在进行中的一刻，最后 2 秒没有人说话。只输出「可以」或重写后的这一块。"
           % (nh[:90], "、".join(n for n in names if n), a, b))
    try:
        new = str(q(ask, last, mt=650, temperature=0.3) or "").strip()
    except Exception:
        return body, False
    if re.match(r"^\W*可以", new) or len(new) < 30:
        return body, False
    from . import authoring as au
    new = strip_meta(au._tl_first_block_only(new))
    if new and au._TL_BLOCK.match(new) and _says_kept(new, last) and last_block_relay_ok(new, names) and rewrite_ok(new, last):
        blocks[-1] = (a, b, au._tl_clean(new))
        tail2 = regen_tail(blocks[-1][2], names, q)
        if tail2 and parse_relay(tail2, names).get("people"):
            tail = merge_done(tail, tail2, names)
        tail = reconcile_frame(tail, blocks[-1][2])
        return _join(head, blocks, tail), True
    return body, False


def guard_first_block_match(body, relay, names, q):
    """第一块的站位（谁在谁前后、朝向、手里的东西）要和接力状态一致；不一致只重写第一块，动作照做。返回 (body, 改没改)。"""
    if not relay_ok(relay):
        return body, False
    blocks, head, tail = _blocks(body)
    if not blocks:
        return body, False
    a, b, first = blocks[0]
    st = relay_text(relay)
    ask = ("下面是一段视频提示词的第一个时间块。上一段结束时的状态（也是这一块 0 秒的画面）是：\n%s\n"
           "判断：这一块开头写的每个人的位置、谁在谁前后、朝向、手里的东西，和上面的状态是不是一致（允许在块内自然地移动，但 0 秒那一刻必须一致）？"
           "一致 → 只回「一致」两个字。不一致 → 只重写这一块：0 秒从上面的状态原样开始，块里要做的动作照做（要换位置就写出走过去/转过去的动作），"
           "原有台词行一字不动，机位和景别不变，开头保留「%d—%d秒：」。只输出「一致」或重写后的这一块。" % (st[:500], a, b))
    try:
        new = str(q(ask, first, mt=700, temperature=0.3) or "").strip()
    except Exception:
        return body, False
    if re.match(r"^\W*一致", new) or len(new) < 30:
        return body, False
    from . import authoring as au
    new = strip_meta(au._tl_first_block_only(new))
    if new and au._TL_BLOCK.match(new) and _says_kept(new, first) and rewrite_ok(new, first):
        blocks[0] = (a, b, au._tl_clean(new))
        return _join(head, blocks, tail), True
    return body, False


def guard_exit_beat(body, names, say_lines, q):
    """本场最后一段：最后一块要有出场动作 + 静止收尾。"""
    blocks, head, tail = _blocks(body)
    if not blocks:
        return body, False
    a, b, last = blocks[-1]
    if EXIT_W.search(last) and WIDE_W.search(last):
        return body, True
    ask = ("下面是一段视频提示词的最后一个时间块，它是这场戏在这个地方的最后一块（出场拍）。只重写这一块：固定机位的全景或中景，"
           "人物做完这一块原有的事之后开始离开的动作（转身走向门、推门、走出画面、门合上），最后 1 秒画面静止在门口、门或走远的背影上，没有台词。"
           "位置、朝向、画面左右和原来一致，原有的台词行一字不动，开头保留「%d—%d秒：」。只输出这一块。" % (a, b))
    try:
        new = str(q(ask, last, mt=650, temperature=0.3) or "").strip()
    except Exception:
        new = ""
    from . import authoring as au
    new = au._tl_first_block_only(new)
    new = strip_meta(new)
    if new and au._TL_BLOCK.match(new) and _says_kept(new, last) and EXIT_W.search(new) and rewrite_ok(new, last):
        blocks[-1] = (a, b, au._tl_clean(new))
        tail2 = regen_tail(blocks[-1][2], names, q)
        if tail2 and parse_relay(tail2, names).get("people"):
            tail = merge_done(tail, tail2, names)
        tail = reconcile_frame(tail, blocks[-1][2])
        return _join(head, blocks, tail), True
    return body, False


def _overlap(a, b):
    from . import shotlist as sl
    return sl._overlap(a, b)


_FUNC_CH = set("把将了的在到着地得又再就并向从于与和把被让给")


def _content(s):
    return [c for c in re.sub(r"[^\w一-龥]", "", str(s or "")) if c not in _FUNC_CH]


def _bigram_hits(piece, text):
    """(二字组合重合率, 命中个数)。"""
    from . import shotlist as sl
    b = sl._bigrams(piece)
    if not b:
        return 0.0, 0, 0
    t = sl._n(text)
    hit = sum(1 for g in b if g in t)
    return hit / float(len(b)), hit, len(b)


def _window_ratio(dc, sc, mult=2):
    """短语的实词字有多大比例落在句子里一个 2 倍短语长的窗口内（散落全句不算）。"""
    if not dc or not sc:
        return 0.0
    w = max(len(dc) * mult, 4)
    best = 0
    for i in range(0, max(1, len(sc) - w + 1)):
        win = set(sc[i:i + w])
        best = max(best, sum(1 for c in dc if c in win))
    return best / float(len(dc))


def strip_meta(txt):
    """守卫重写常把指令里的元话抄进来（"与原作一致""最后 2 秒无台词"）→ 剥掉（块头「0—4秒：」不动）。"""
    t = str(txt or "")
    m = re.match(r"^\s*\d+\s*[—\-–~～]\s*\d+\s*秒[：:]\s*", t)
    head = m.group(0) if m else ""
    out = META_W.sub("", t[len(head):])
    return head + re.sub(r"[，,]{2,}", "，", out).strip("，, ")


def repeated_done(txt, done, names=()):
    """这一块里出现了已经做完的事：二字重合 ≥0.5，或实词字重合 ≥0.75（「把伞靠到墙边」vs「将伞靠在墙边」）。
    人名的字不算（不然「苏蔓坐下」在任何提到苏蔓的句子里都命中）。返回命中的短语。"""
    hits = []
    name_ch = set("".join(str(n) for n in (names or [])))
    for d in done or []:
        d = str(d).strip("。 ")
        if len(d) < 4:
            continue
        dc = [c for c in _content(d) if c not in name_ch]
        for sent in re.split(r"[。；]", txt):
            if not sent.strip():
                continue
            if re.search(r"已|早已|仍|还(在|是)|依旧|保持|留在|继续|接着|顺势|延续|随即|紧接|下半", sent):
                continue                                          # 说的是"已经是那个状态"或接着做，不是再做一遍
            ratio = _window_ratio(dc, _content(sent)) if len(dc) >= 4 else 0.0
            bg = _bigram_hits(d, sent)
            if (bg[0] >= 0.6 and bg[1] >= (2 if bg[2] <= 3 else 3)) or ratio >= 0.75:
                hits.append(d)
                break
    return hits


_CLAUSE_ACT = re.compile(r"(走|跑|跳|站|坐|跪|蹲|趴|躺|爬|冲|追|拦|挡|转|抬|低|伸|收|拿|拾|捡|握|托|放|推|拉|按|抱|靠|迈|退|进|出|停|指|挥|拍|摸|触|按|递|接|扔|牵|拨|擦|翻|滚|插|系|脱|穿上|换上|披上|摘|戴上|举|落|敲|扶|贴|踩|踏|合|关|开|看|望|扫|盯|点头|摇头|说|喊|笑|哭|叹|吸|张开|弯腰|起身|坐起|回头|转身|滑|扬|甩|捏|塞|掏|抽|端|撑|挤|抓|松|攥)")


def script_clauses(text, min_len=5, max_len=14, names=()):
    """剧本块 → 动作短句（按 。！？；，拆；台词行不要；太短/太长的不要）。给"不许重演"清单用（P344）。
    有人物表时：句子开头的人名是主语，「他/她」承接上一句的主语；没主语的短句补上主语；只留有人名且有动作词的（景物/外貌句不要）。"""
    out = []
    names = [str(n) for n in (names or ()) if str(n)]
    for ln in str(text or "").splitlines():
        s = ln.strip()
        if not s or re.match(r"^[\u4e00-\u9fa5]{1,6}[：:]", s):
            continue                                                    # 「名字：台词」行
        subj = ""
        for sent in re.split(r"[。！？]", s):
            m = re.match(r"^(?:[\u4e00-\u9fa5]{0,4}的)?(" + "|".join(map(re.escape, names)) + r")", sent) if names else None
            if m:
                subj = m.group(1)
            elif names and re.match(r"^(他|她|他们|她们)", sent) and subj:
                pass
            for c in re.split(r"[；，,]", sent):
                c = re.sub(r"^[（(【].*?[）)】]", "", c).strip("“”「」 \t")
                if not c:
                    continue
                if names:
                    if not any(n in c for n in names):
                        if not (subj and _CLAUSE_ACT.search(c)):
                            continue
                        c = re.sub(r"^(他们|她们|他|她)", "", c)            # 「她伸手拨开」→「林夏伸手拨开」
                        c = subj + c
                    if not _CLAUSE_ACT.search(c):
                        continue
                if min_len <= len(re.sub(r"[^\u4e00-\u9fa5]", "", c)) <= max_len + (len(subj) if names else 0) and c not in out:
                    out.append(c)
    return out


def guard_no_repeat(body, done, say_lines, q, max_blocks=2, names=()):
    """任何一块重演已经做完的事 → 只重写那一块（删掉那件事，其余不动）。返回 (body, 还剩的问题)。"""
    if not done:
        return body, []
    blocks, head, tail = _blocks(body)
    problems, fixed = [], 0
    from . import authoring as au
    for k, (a, b, txt) in enumerate(blocks):
        hits = repeated_done(txt, done, names)
        if not hits:
            continue
        if fixed >= max_blocks:
            problems.append("第 %d 块重演了已经做完的事：%s" % (k + 1, "、".join(hits[:3])))
            continue
        ask = ("下面是一段视频提示词里的一个时间块。这些事**在上一段已经做完了**，这一块不许再做一遍：%s。"
               "只重写这一块：把这些事删掉或改成「已经是那个状态」（衣服已经脱下、门已经在身后、人已经到位），其余动作、站位、镜头、台词行一字不动，"
               "开头保留「%d—%d秒：」。只输出这一块。" % ("；".join(hits[:4]), a, b))
        try:
            new = str(q(ask, txt, mt=650, temperature=0.3) or "").strip()
        except Exception:
            new = ""
        new = strip_meta(au._tl_first_block_only(new))
        if new and au._TL_BLOCK.match(new) and _says_kept(new, txt) and not repeated_done(new, done, names) and rewrite_ok(new, txt):
            blocks[k] = (a, b, au._tl_clean(new))
            fixed += 1
        else:
            problems.append("第 %d 块重演了已经做完的事：%s" % (k + 1, "、".join(hits[:3])))
    return (_join(head, blocks, tail) if fixed else body), problems


def inject_opening(body, relay, scene_name="", scene_space="", names=None):
    """同场景：开头段换成代码复述的接力状态（条件②）；原开头里的台词行保留。"""
    op = opening_from_relay(relay, scene_name, scene_space, names)
    if not op:
        return body
    blocks, head, tail = _blocks(body)
    if not blocks:
        return body
    keep = [l for l in str(head or "").splitlines() if "says:" in l]
    return _join("\n".join([op] + keep), blocks, tail)


def apply_guards(body, chars, say_lines, settings, prev_state, scene_name, scene_space, q):
    """v2 守卫总入口（替代 P322②③ 那两道）。返回 (body, problems)。"""
    seam = (settings or {}).get("_seam") or {}
    names = [str(c.get("name") or "") for c in (chars or []) if c.get("name")]
    changed = bool(seam.get("scene_changed"))
    relay = seam.get("relay")
    problems = []
    from . import authoring as au
    # ③ 已经做完的事不再演（含换场景后的入场段）；P344：上一段剧本原句也算
    body, pr = guard_no_repeat(body, list(seam.get("done") or []) + list(seam.get("prev_script") or []), say_lines, q, names=names)
    problems += pr
    # P355：下一段才发生的事本段不许先做——只查最后一块（提前演多半发生在段尾）
    _nx = [str(x) for x in (seam.get("next_script") or []) if str(x).strip()]
    if _nx:
        blocks0, head0, tail0 = _blocks(body)
        if blocks0:
            a0, b0, last0 = blocks0[-1]
            hits0 = repeated_done(last0, _nx, names)
            if hits0:
                ask0 = ("下面是一段视频提示词的最后一个时间块。这些事**是下一段才发生的**，这一块不许先做：%s。"
                        "只重写这一块：把这些事删掉，人物停在准备开始做它的位置和朝向，其余动作、站位、镜头、台词行一字不动，"
                        "开头保留「%d—%d秒：」。只输出这一块。" % ("；".join(hits0[:4]), a0, b0))
                try:
                    new0 = str(q(ask0, last0, mt=650, temperature=0.3) or "").strip()
                except Exception:
                    new0 = ""
                new0 = strip_meta(au._tl_first_block_only(new0))
                if new0 and au._TL_BLOCK.match(new0) and _says_kept(new0, last0) and not repeated_done(new0, _nx, names) and rewrite_ok(new0, last0):
                    blocks0[-1] = (a0, b0, au._tl_clean(new0))
                    body = _join(head0, blocks0, tail0)
                else:
                    problems.append("最后一块提前演了下一段的事：%s" % "、".join(hits0[:3]))
    if not seam.get("ep_first") and not changed and relay_ok(relay):
        # ② 开头段＝上一段结束那一刻；左右继承守卫照跑（空间描述用这一段绑的那张场景卡自己的）
        body = inject_opening(body, relay, scene_name, str((settings or {}).get("_seam_scene_space") or scene_space or ""), names)
        body, _m = guard_first_block_match(body, relay, names, q)       # 前后/朝向也要接上
        # 左右：老守卫认的是「X站在画面左侧」句式，从接力状态合成一句给它
        sides = "；".join("%s站在%s" % (nm, p.get("side")) for nm, p in relay["people"].items() if p.get("side") and nm in names)
        if sides:
            body = au._tl_inherit_staging(body, sides, names, say_lines)
            problems += list(getattr(au._tl_inherit_staging, "last_problems", []) or [])
    # ① 最后一块：接力帧 / 出场拍 / 全片收住
    if seam.get("ep_last"):
        body, ok = guard_relay_frame(body, names, say_lines, q, close=True)
    elif seam.get("last_of_scene"):
        body, ok = guard_exit_beat(body, names, say_lines, q)
        if not ok:
            problems.append("最后一块没有出场动作（转身走向门/推门/走出画面/门合上），换场景会生硬")
    else:
        body, ok = guard_relay_frame(body, names, say_lines, q)
        if not ok:
            problems.append("最后一块不是接力帧（全景/中景 + 在场全员），下一段接不上")
        else:
            body, _al = guard_relay_align(body, names, seam.get("next_head"), q)     # 站位要能直接开始下一段第一句
    # 重写过的块最后再查一遍不重演（只记，不再改）
    blocks, _h, _t = _blocks(body)
    for k, (a, b, txt) in enumerate(blocks):
        hits = repeated_done(txt, seam.get("done") or [], names)
        if hits and not any(("第 %d 块" % (k + 1)) in x for x in problems):
            problems.append("第 %d 块重演了已经做完的事：%s" % (k + 1, "、".join(hits[:3])))
    return body, problems


def seam_roles(pick):
    """每段的接缝角色（按绑定的场景板判本场第一段/最后一段）。
    返回 [{"ep_first","ep_last","scene_changed","last_of_scene"}]。"""
    n = len(pick or [])
    out = []
    for k in range(n):
        cur = str(pick[k] or "")
        prev = str(pick[k - 1] or "") if k > 0 else None
        nxt = str(pick[k + 1] or "") if k + 1 < n else None
        out.append({"ep_first": k == 0, "ep_last": k == n - 1,
                    "scene_changed": bool(prev is not None and prev != cur),
                    "last_of_scene": bool(nxt is not None and nxt != cur)})
    return out
