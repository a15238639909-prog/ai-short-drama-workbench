# -*- coding: utf-8 -*-
"""story_gen.py — 一句话 → 完整短篇正文。

为什么单独一步：一键生成那个 JSON 要同时装人物、场景、标题、画风，
故事挤在里面模型只会给三五百字的梗概（实测 512 字）。拆出来单独写，才写得开。

人物名字、场景名字来自第一步的定稿，这里只准用、不准改——
那些名字后面要拿去生图，还要做视频的 subject 定义，改了整条链就对不上。

不达标时的处理沿用全站那三层：
    第 1 层 自动重来（把"为什么不合格"告诉模型）
    第 2 层 重试三次
    第 3 层 还不行就把最长的那稿给出去，**不拦着用户**——拦截是设计失败
"""
import re
from pathlib import Path

from . import premise_guard

INSTRUCTION = (Path(__file__).resolve().parent.parent / "presets" / "instructions"
               / "故事_完整正文.txt")

MIN_CHARS = 1200        # 低于这个字数就是没展开
TRIES = 3
MAX_TOKENS = 6000       # 2000+ 汉字要留够


def _prefix_clean(t):
    """模型偶尔会加"正文：""故事："这类前缀或一个标题行，剥掉。"""
    t = re.sub(r"^\s*(正文|故事正文|故事|标题)\s*[:：]\s*", "", str(t or "").strip())
    return t.strip()


def build_user(premise, setup, settings=None):
    chars = (setup or {}).get("characters") or []
    scenes = (setup or {}).get("scenes") or []
    # 服装必须发过去——指令词里要求"衣服用人物栏定好的那几件"，
    # 但这里原来根本没把 clothing 传给模型，它看不到当然自己编一身。
    ch = "；".join(
        "%s（%s%s；外貌 %s；身上穿 %s）" % (
            c.get("name", ""), c.get("age", ""), c.get("sex", ""),
            c.get("look") or c.get("other") or c.get("features") or "未定",
            c.get("clothing") or "未定")
        for c in chars if c.get("name"))
    # 场景把识别点也带上：故事里提到报刊亭、长椅，后面出图才有依据
    sc = "；".join(
        "%s（%s；标志物 %s）" % (x.get("name", ""), x.get("space", ""),
                             x.get("landmarks") or x.get("furniture") or "未定")
        for x in scenes if x.get("name"))
    tend = ""
    try:
        from .style_presets import tendency_effect
        tend = tendency_effect((settings or {}).get("content_tendencies") or [], "story")
    except Exception:
        pass
    lines = ["一句话：" + str(premise or ""),
             "人物：" + (ch or "（自定）"),
             "场景：" + (sc or "（自定）"),
             "内容尺度：" + (tend or "常规")]
    try:
        from . import project_prompt
        lines.append("项目故事约束：\n" + project_prompt.story_generation_block(settings or {}))
    except Exception:
        pass
    return "\n".join(lines)


def generate(premise, setup=None, settings=None, call_model=None):
    """写出完整短篇正文。返回 (正文, 说明)。"""
    ins = INSTRUCTION.read_text(encoding="utf-8")
    user = build_user(premise, setup, settings)

    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=MAX_TOKENS, **kw)

    best, best_score, note = "", -1, ""
    for _ in range(TRIES):
        txt = _prefix_clean(call_model(ins, user, temperature=0.8))
        why = check(txt, premise, setup)
        score = len(txt) - 400 * len(why)      # 问题越少越好；同样干净时取长的那稿
        if score > best_score:
            best, best_score = txt, score
        if not why:
            return txt, "%d 字" % len(txt)
        note = "；".join(why)
        user = build_user(premise, setup, settings) + "\n\n【重来】" + note
    return best, "重试 %d 次后仍有问题（%d 字）：%s" % (TRIES, len(best), note)


# 结尾跳出来评论故事本身，是模型最爱犯的毛病之一
META_TAIL = ("故事没有", "这是一个关于", "本故事", "这就是他们", "一切都归于",
             "从此以后", "故事的最后", "或许这就是", "而这一切")


def check(txt, premise, setup):
    """把"哪里不合格"逐条列出来，返回空列表就是通过。

    每一条都是机械可查的，不是主观评价——主观的东西没法让模型改。
    这些理由会原样发回给模型让它重写，所以写成"你该怎么做"，不是"你错了"。
    """
    why = []
    if len(txt) < MIN_CHARS:
        why.append("上一稿只有 %d 字，太短。把过程、对白和细节展开，写够 1500 字以上，"
                   "不要用总结句带过" % len(txt))
    if premise_guard.overlap(premise, txt) < premise_guard.DEFAULT_THRESHOLD:
        why.append(premise_guard.RETRY_HINT)

    for c in (setup or {}).get("characters") or []:
        n = str(c.get("name") or "").strip()
        if n and n not in txt:
            why.append("人物「%s」在正文里一次都没出现，故事要围绕给定的人物写" % n)

    # 服装必须是人物卡上那一套——实测里模型很爱自己换一身更好看的
    for c in (setup or {}).get("characters") or []:
        n, cl = str(c.get("name") or ""), str(c.get("clothing") or "")
        items = [x.strip() for x in re.split(r"[，、;；]", cl) if len(x.strip()) >= 2]
        if items and not any(x in txt for x in items):
            why.append("%s 身上穿的不是人物栏定好的「%s」，写到衣服时要用这几件" % (n, cl))

    # 给了几个场景就要走遍几个场景，否则那张场景图白生成
    for s in (setup or {}).get("scenes") or []:
        n = str(s.get("name") or "").strip()
        if not n:
            continue
        # 故事里会写"布拉格老城的石板路"，不会逐字复述"布拉格老城街头"。
        # 所以除了全名，也认场景名去掉通用后缀之后的主干，以及标志物名字。
        stem = re.sub(r"(街头|广场|内部|外景|房间|里|中)$", "", n)
        key = [n, stem] if len(stem) >= 2 else [n]
        key += [x.strip() for x in re.split(r"[，、]", str(s.get("landmarks") or ""))
                if len(x.strip()) >= 2]
        if not any(k and k in txt for k in key):
            why.append("场景「%s」在正文里没用到，给定的每个地点都要走到" % n)

    hit = [w for w in META_TAIL if w in txt[-120:]]
    if hit:
        why.append("结尾跳出来评论故事本身了（出现了「%s」），最后一段要落在一个"
                   "具体画面或一句对白上，写完就停" % hit[0])
    return why


# ---------- 骨架流程（先结构后正文） ----------
# 为什么加这一步：实测里 Qwen 直接写正文，把设定的关键事件丢了三个
# （风吹裙摆的相遇契机、无交谈的初遇、卧室高潮全没了），
# 关键词重合的校验根本测不出"事件结构丢没丢"。
# 骨架几百字，用户先审结构；正文按骨架写，锚词逐条校验。

SKELETON_INS = (Path(__file__).resolve().parent.parent / "presets" / "instructions"
                / "故事_骨架_指令词.txt")
BODY_INS = (Path(__file__).resolve().parent.parent / "presets" / "instructions"
            / "故事_正文_按骨架_指令词.txt")


def _target_seconds(settings=None, skeleton=None):
    """读取显式或骨架时长；没有可靠正数时返回 0，表示交给故事量自适应。"""
    values = [
        (settings or {}).get("target_duration"),
        (skeleton or {}).get("total_seconds"),
    ]
    for value in values:
        try:
            seconds = int(float(str(value).strip()))
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            return seconds
    return 0


def _setup_lines(setup, settings):
    chars = (setup or {}).get("characters") or []
    scenes = (setup or {}).get("scenes") or []
    ch = "；".join("%s（%s%s；身份 %s；性格/常态 %s；外貌 %s；固定服装 %s）" % (
        c.get("name", ""), c.get("age", ""), c.get("sex", ""),
        c.get("role") or c.get("identity") or c.get("occupation") or "未定",
        c.get("personality") or c.get("behavior_anchor") or "未定",
        c.get("look") or c.get("appearance") or c.get("other") or c.get("features") or "未定",
        c.get("clothing") or c.get("outfit") or c.get("clothing_requirement") or "未定")
        for c in chars if c.get("name"))
    sc = "；".join("%s（%s%s%s；空间 %s；标志物 %s）" % (
        x.get("name", ""), x.get("time_of_day") or "",
        (" " + str(x.get("interior"))) if x.get("interior") else "",
        ("，规模" + str(x.get("scale_class") or x.get("scale")))
        if (x.get("scale_class") or x.get("scale")) else "",
        x.get("space") or x.get("description") or "未定",
        x.get("landmarks") or x.get("furniture") or "未定")
        for x in scenes if x.get("name"))
    s = settings or {}
    lines = ["本话人物事实：" + (ch or "（没有已建人物卡，只能根据用户原文补空白）"),
             "本话场景事实：" + (sc or "（没有已建场景卡，只能根据用户原文补最少必要地点）")]
    target = _target_seconds(s)
    if target:
        lines.append("成片总时长：%s 秒（显式要求，场次时长之和必须保持一致）" % target)
    else:
        lines.append("成片总时长：未指定。按事件量、题材节奏和必要场次自适应，"
                     "不要机械套用固定秒数")
    # 配比和引擎都改成了"跟着故事发展"（用户定的）：默认不注入任何一个。
    # 只有街头篇这类显式设置了的老项目，才继续按设置走。
    if s.get("content_ratio"):
        lines.append("内容配比：" + str(s["content_ratio"]))
    if s.get("sensual_focus"):
        lines.append("感官焦点：" + "、".join(s["sensual_focus"]))
    eng = str(s.get("genre_engine") or "")
    if eng:
        try:
            doc = (Path(__file__).resolve().parent.parent / "presets" / "instructions"
                   / "剧本方法论.md").read_text(encoding="utf-8")
            m = re.search(r"## 引擎[^：\n]*：%s.*?(?=\n## |\Z)" % re.escape(eng), doc, re.S)
            if m:
                lines.append("题材引擎（只调节节拍，不得覆盖用户事实）：\n" + m.group(0).strip())
        except Exception:
            pass
    try:
        from . import project_prompt
        lines.append("项目故事约束：\n" + project_prompt.story_generation_block(s))
    except Exception:
        pass
    return lines


def _merge_adjacent_scenes(data):
    """确定性合并连续且时空完全相同的场，避免把剧情阶段误当成新场次。"""
    if not isinstance(data, dict):
        return data
    source = data.get("scenes")
    if not isinstance(source, list):
        return data

    merged = []
    for raw in source:
        scene = dict(raw) if isinstance(raw, dict) else raw
        if not isinstance(scene, dict):
            merged.append(scene)
            continue
        scene["beats"] = [dict(b) if isinstance(b, dict) else b
                          for b in (scene.get("beats") or [])]
        keys = ("location", "interior", "time_of_day")
        current_key = tuple(str(scene.get(k) or "").strip() for k in keys)
        previous = merged[-1] if merged and isinstance(merged[-1], dict) else None
        previous_key = (tuple(str(previous.get(k) or "").strip() for k in keys)
                        if previous else ())
        if previous and all(current_key) and current_key == previous_key:
            def _seconds(value):
                try:
                    number = float(value)
                    return int(number) if number.is_integer() else number
                except (TypeError, ValueError):
                    return 0

            previous["seconds"] = _seconds(previous.get("seconds")) + _seconds(scene.get("seconds"))
            previous["beats"] = list(previous.get("beats") or []) + list(scene.get("beats") or [])
            previous["chars"] = list(dict.fromkeys(
                list(previous.get("chars") or []) + list(scene.get("chars") or [])))
            purposes = []
            for value in (previous.get("purpose"), scene.get("purpose")):
                for part in re.split(r"[/／、；;|]+", str(value or "")):
                    part = part.strip()
                    if part and part not in purposes:
                        purposes.append(part)
            previous["purpose"] = " / ".join(purposes)
        else:
            merged.append(scene)

    beat_no = 0
    for scene_no, scene in enumerate(merged, 1):
        if not isinstance(scene, dict):
            continue
        scene["no"] = scene_no
        for beat in scene.get("beats") or []:
            if isinstance(beat, dict):
                beat_no += 1
                beat["beat"] = beat_no
    data["scenes"] = merged
    return data



def scene_count_range(total_seconds):
    """这么长的一话该有几场。返回 (下限, 上限)。

    行业经验：短剧一场戏 30~90 秒。低于 30 秒是过场，高于 90 秒观众会觉得黏住了。
    实测踩过的坑：200 秒的一话骨架只给了 1 场，整话困在一间公寓里——
    不是剧情要求，是没人告诉它该有几场。
    """
    t = 0
    try:
        t = float(total_seconds or 0)
    except Exception:
        t = 0
    if t <= 0:
        return 2, 8
    # 下限按"一场最长 90 秒"算——这一条是真正抓 bug 的（200 秒只给 1 场）。
    # 上限按"一场最短 15 秒"算，放得很宽：快切的冷开场本来就可能一场只有
    # 十几秒，卡太紧会把合法的结构判成错的（实测 100 秒 5 场被误伤）。
    # 下限是向上取整，不是四舍五入——语义是"一场最长 90 秒，所以至少要
    # ⌈总秒/90⌉ 场"。用 max(2,…) 会把"50 秒一整话就一场"这种合法结构判成错的。
    import math as _math
    lo = max(1, int(_math.ceil(t / 90.0)))
    # 上限按"一场最短 15 秒"算，放得很宽：快切的冷开场本来就可能一场只有
    # 十几秒，卡太紧会把合法的结构判成错的（实测 100 秒 5 场被误伤）。
    hi = max(lo + 1, int(round(t / 15.0)))
    return lo, hi

def generate_skeleton(premise, setup=None, settings=None, call_model=None):
    """一句话设定 → 故事骨架 JSON。"""
    from .model_json import chat_json
    ins = SKELETON_INS.read_text(encoding="utf-8")
    user = "\n".join(["一句话设定：" + str(premise or "")] + _setup_lines(setup, settings))
    names = [x.get("name") for x in (setup or {}).get("scenes") or [] if x.get("name")]

    # 【硬 / 软 之分】用户定的规矩：拦截是设计失败。
    # 硬条件 = 缺了后面根本跑不下去的结构（没有场次、没有节拍、节拍没内容）——
    #          这种必须重来，否则写正文时会直接崩。
    # 软条件 = 质量要求（禁词、不可更改要求、场次数量、每场拍数）——
    #          带着原因重试，但试到头就交付最好的一版并记一笔，
    #          绝不因为它把整个「全部生成」打断在半路。
    def _hard(d):
        scs = d.get("scenes")
        if not scs:
            raise ValueError("没有 scenes。至少要给一场，每场带 beats")
        for x in scs:
            bts = x.get("beats")
            if not bts:
                raise ValueError("第 %s 场没有 beats" % x.get("no"))
            for b in bts:
                if not str(b.get("content") or "").strip():
                    raise ValueError("第 %s 拍没写 content" % b.get("beat"))

    def _soft(d):
        from . import project_prompt
        import json as _json
        serialized = _json.dumps(d, ensure_ascii=False)
        why = []
        scs = d.get("scenes") or []
        for key in ("premise_events", "scenes", "total_seconds", "ending"):
            if key not in d:
                why.append("骨架缺顶层结构字段：" + key)
        required = ("no", "location", "interior", "time_of_day",
                    "scale", "purpose", "seconds")
        for i, x in enumerate(scs):
            miss = [k for k in required if not str(x.get(k) or "").strip()]
            if miss:
                why.append("第%s场缺结构字段：%s" % (x.get("no") or i + 1, "、".join(miss)))
            if str(x.get("interior") or "") not in ("", "内", "外"):
                why.append("第%s场的 interior 只能是「内」或「外」" % (x.get("no") or i + 1))
            if str(x.get("scale") or "") not in ("", "大", "中", "小"):
                why.append("第%s场的 scale 只能是「大」「中」「小」" % (x.get("no") or i + 1))
            if "chars" not in x:
                why.append("第%s场缺结构字段：chars" % (x.get("no") or i + 1))
            for j, beat in enumerate(x.get("beats") or []):
                beat_missing = [k for k in ("beat", "phase", "seconds", "content")
                                if not str(beat.get(k) or "").strip()]
                beat_missing += [k for k in ("value_turn", "has_dialogue", "chars")
                                 if k not in beat]
                if beat_missing:
                    why.append("第%s场第%s拍缺结构字段：%s"
                               % (x.get("no") or i + 1, beat.get("beat") or j + 1,
                                  "、".join(dict.fromkeys(beat_missing))))
            if i:
                prev = scs[i - 1]
                same = all(str(prev.get(k) or "").strip() == str(x.get(k) or "").strip()
                           for k in ("location", "interior", "time_of_day"))
                if same:
                    why.append("第%s场与前一场是连续的同地点、同内外、同时间，"
                               "请合并为一场并把阶段写进 beats" % (x.get("no") or i + 1))
        incompatible = project_prompt.story_world_violations(serialized, settings or {})
        if incompatible:
            why.append("骨架混入了与世界时代冲突的内容（%s）。用这个世界已有的工具、"
                       "魔法或炼金手段替代" % "、".join(incompatible))
        broken = project_prompt.story_requirement_violations(serialized, settings or {})
        if broken:
            why.append("骨架违反不可更改要求：" + "；".join(broken))
        # 【场次数下限】200 秒只给 1 场是结构错误，不是风格选择。
        _lo, _hi = scene_count_range(d.get("total_seconds"))
        if scs and len(scs) < _lo:
            why.append("这一话 %s 秒只写了 %d 场，太少了。一场戏 30~90 秒，"
                       "这个长度应该有 %d~%d 场。把它拆成不同地点或不同时间的几场——"
                       "人物移动到别处、时间推进、换一批人在场，都是新的一场"
                       % (d.get("total_seconds"), len(scs), _lo, _hi))
        if scs and len(scs) > _hi:
            why.append("这一话 %s 秒写了 %d 场，太碎了。相邻的同地点同时间要合成一场，"
                       "目标 %d~%d 场" % (d.get("total_seconds"), len(scs), _lo, _hi))
        # 【地点可以新增，但别放飞】原来这一条是硬性"必须在给定场景名里"，
        # 名单只有一个地点时骨架连第二场都开不出来。现在缺卡会自动补建，
        # 所以允许新增，只是限个数——防止模型每一场换一个新地方。
        _new = [str(x.get("location") or "") for x in scs
                if names and str(x.get("location") or "") not in names]
        _new = [x for x in dict.fromkeys(_new) if x]
        if len(_new) > 2:
            why.append("新增了 %d 个名单外的地点（%s）。最多新增 2 个，"
                       "其余请用已有的场景：%s"
                       % (len(_new), "、".join(_new[:4]), "、".join(names)))
        return why

    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=4500, **kw)

    best, best_score, note, u = None, None, "", user
    for _ in range(TRIES):
        try:
            d, _raw = chat_json(ins, u, call_model, validate=_hard, tries=3, temperature=0.7)
            d = _merge_adjacent_scenes(d)
        except Exception as ex:
            d = None
            u = user + ("\n\n上一次没能给出可用的 JSON（%s）。"
                        "这次只输出一个 JSON 对象，从 { 开始到 } 结束，"
                        "不要代码围栏、不要说明文字。" % str(ex)[:120])
            continue
        why = _soft(d)
        if not why:
            return d
        if best_score is None or len(why) < best_score:
            best, best_score, note = d, len(why), "；".join(why)
        u = user + "\n\n上一稿的问题（必须改掉）：" + "；".join(why)
    # 试到头还有问题：交付最好的一版，把问题记在骨架里带下去，不抛异常。
    if not isinstance(d, dict) or not (d.get("scenes") or []):
        d = _fallback_skeleton(setup, settings)
        d["_issues"] = "模型没能给出可用骨架，已按场景卡拼了一个最小场次表，请手动补"
    elif note:
        d["_issues"] = note
    return d


def _fallback_skeleton(setup, settings=None):
    """模型写不出场次表时的确定性兜底。纯拼装、不调模型，一定成功。"""
    scs = (setup or {}).get("scenes") or []
    chars = [str(c.get("name") or "") for c in ((setup or {}).get("characters") or []) if c.get("name")]
    explicit_total = _target_seconds(settings)
    total = explicit_total or (45 * len(scs))
    if not scs:
        return {"premise_events": [], "scenes": [], "total_seconds": total, "ending": ""}
    per, remainder = divmod(total, len(scs))
    out = []
    for i, x in enumerate(scs, 1):
        scene_seconds = per + (1 if i <= remainder else 0)
        scale = str(x.get("scale_class") or x.get("scale") or "中")
        if scale not in ("大", "中", "小"):
            scale = "中"
        out.append({"no": i, "location": x.get("name") or "",
                    "interior": x.get("interior") or "内",
                    "time_of_day": x.get("time_of_day") or "日",
                    "scale": scale,
                    "purpose": "开场" if i == 1 else "推进",
                    "seconds": scene_seconds, "chars": chars,
                    "beats": [{"beat": 1, "phase": "待补", "seconds": scene_seconds,
                               "content": "根据本场目的补写可见行动", "value_turn": "",
                               "has_dialogue": False, "chars": chars}]})
    return {"premise_events": [], "scenes": out,
            "total_seconds": total, "ending": ""}


def _anchor_present(anchor, text):
    """这件东西在不在故事里。**不要求逐字出现。**

    【为什么不能逐字比】锚词是模型自己在骨架里写下的具体名词（"青铜钥匙"）。
    原来要求正文里一字不差地出现，于是：
      1. 正文写"那把钥匙""铜钥匙"就判失败，白白重写一遍；
      2. 更糟的是模型学乖了——它开始在每一句里硬塞全称，
         "他握紧青铜钥匙……他看着青铜钥匙……"，文字变成产品说明书。
    真实写作里同一样东西第二次出现本来就该换说法（青铜钥匙→那把钥匙→它）。
    所以只验"核心名词在场"：全称在算在场，去掉前缀修饰后的名词在场也算。
    """
    a = str(anchor or "").strip()
    t = str(text or "")
    if not a:
        return True
    if a in t:
        return True
    # 逐个剥掉前面的修饰（青铜钥匙 → 铜钥匙 → 钥匙；银色十字架 → 色十字架 → 十字架）
    for i in range(1, len(a) - 1):
        if a[i:] in t:
            return True
    return False



# 每个题材"平均多少秒该有一句台词"的上限。超过就是太哑。
# 数字来自这几类戏的实际形态：斗嘴戏几乎句句接，动作戏可以几十秒不说话。
DIALOGUE_PACE = {
    "群像喜剧型": 12,      # 几个人抢同一个目标，靠对话推进，最密
    "后宫环绕型": 13,      # 打情骂俏、争风吃醋，全靠对话，很密
    "关系推进型": 14,
    "剧情冲突型": 15,
    "爽点打脸型": 15,      # 打脸靠当众说出来，不说等于没打
    "情感虐心型": 18,
    "悬疑压迫型": 22,
    "成长蜕变型": 22,
    "冒险闯关型": 25,
    "生存压迫型": 25,
    "打斗型": 30,          # 打起来可以很久不说话
    "情色片型": 35,        # 靠氛围和动作推进，台词稀
    "感官特写型": 40,
}
DIALOGUE_PACE_DEFAULT = 20

# 开场第一镜的景别。一句话故事的第一镜要先把世界给出来。
OPENING_FRAMINGS = ("大全景", "远景", "全景")


# 短于这个长度的片段不查对白密度——二十秒的一个片段本来就可能一句话没有。
DIALOGUE_MIN_TOTAL = 60


def dialogue_floor(settings=None, skeleton=None):
    """这一话至少要有几句台词。返回 (句数下限, 用的节奏值)。

    三种情况不查：
      · 没有目标时长（自适应，不知道该多少句）
      · 太短（不足 60 秒的片段，一句不说也成立）
      · 骨架自己就没标任何 has_dialogue=true（有意设计的无声戏）
    只有骨架说了"这里有人说话"而正文没写出来，才算问题。
    """
    total = _target_seconds(settings, skeleton)
    pace = DIALOGUE_PACE.get(str((settings or {}).get("genre_engine") or "").strip(),
                             DIALOGUE_PACE_DEFAULT)
    if not total or total < DIALOGUE_MIN_TOTAL:
        return 0, pace
    if isinstance(skeleton, dict) and skeleton.get("scenes"):
        marked = any(bool(b.get("has_dialogue"))
                     for x in (skeleton.get("scenes") or [])
                     for b in (x.get("beats") or []))
        if not marked:
            return 0, pace
    return max(2, int(total // pace)), pace


def count_dialogue(body, speakers):
    """正文里有多少句台词。两种写法都算：人名单独一行，和「人名：台词」同行。"""
    speakers = [x for x in (speakers or []) if x]
    if not speakers:
        return 0
    lines = [l.strip() for l in str(body or "").splitlines()]
    n = 0
    for i, l in enumerate(lines):
        if l.rstrip("：:").strip() in speakers:
            j = i + 1
            if j < len(lines) and lines[j].startswith(("（", "(")):
                j += 1
            if j < len(lines) and lines[j] and not lines[j].startswith(("（", "(", "#", "【")):
                n += 1
        elif any(l.startswith(sp + "：") or l.startswith(sp + ":") for sp in speakers):
            if len(l.split("：", 1)[-1].split(":", 1)[-1].strip()) >= 1:
                n += 1
    return n


def opening_framing(body):
    """正文第一个镜头提示里的景别。取不到返回空串。"""
    for raw in str(body or "").splitlines():
        line = raw.strip()
        m = re.match(r"^[（(]([^）)]{1,40})[）)]", line)
        if not m:
            continue
        inner = m.group(1)
        for f in ("大远景", "大特写", "大全景", "全景", "远景", "中景",
                  "近景", "特写", "胸像", "半身"):
            if f in inner:
                return f
        return ""
    return ""

def generate_from_skeleton(premise, skeleton, setup=None, settings=None, call_model=None,
                           previous_body="", diagnosis=""):
    """按定稿骨架写正文。锚词逐条校验，缺了带原因重来。返回 (正文, 说明)。"""
    import json as _json
    ins = BODY_INS.read_text(encoding="utf-8")
    user_parts = ["一句话设定：" + str(premise or ""),
                  "故事骨架（已定稿）：" + _json.dumps(skeleton, ensure_ascii=False)]
    user_parts += _setup_lines(setup, settings)
    if str(diagnosis or "").strip():
        user_parts += [
            "医生诊断（终稿必须逐条落实）：\n" + str(diagnosis).strip(),
            "上一稿全文（保留有效部分，但不得原样交回）：\n"
            + str(previous_body or "")[:6000],
        ]
        ins += ("\n\n## 医生重写任务\n收到医生诊断和上一稿时，逐条落实诊断。"
                "终稿必须产生可核对的实际改动，不得原样输出上一稿。")
    user = "\n".join(user_parts)
    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=4000, **kw)

    anchors = [a for e in skeleton.get("premise_events") or [] for a in e.get("anchors") or []]
    allowed_presence = {
        str(c.get("name") or "").strip()
        for c in ((setup or {}).get("characters") or []) if c.get("name")
    }
    allowed_presence |= {
        str(n or "").strip()
        for scene in (skeleton.get("scenes") or [])
        for n in (scene.get("chars") or []) if str(n or "").strip()
    }
    allowed_presence |= {"无", "无具体人物"}
    previous_norm = re.sub(r"\s+", "", _prefix_clean(previous_body))
    best, best_score, note = "", -1, ""
    for i in range(TRIES):
        txt = _prefix_clean(call_model(ins, user, temperature=0.8))
        why = []
        unchanged_revision = False
        # 【这一层产出的是剧本，不是小说】原来这里禁摄影术语（"镜头/特写/机位"一出现
        # 就判违规），理由是"故事层只写发生了什么"。结果正文写成了文学作品：
        # 没有场景头、没写场地多大、没写谁在场、没有站位、没有景别、几乎没有对白，
        # 直接丢给分镜层，分镜只能靠猜。用户看到的就是"没剧本"。
        # 现在这些恰恰是必须有的。
        target_seconds = _target_seconds(settings, skeleton)
        min_chars = max(60, min(1200, target_seconds * 2)) if target_seconds else 120
        if len(txt) < min_chars:
            why.append("只有 %d 字，低于当前约 %d 秒内容所需的最低 %d 字"
                       % (len(txt), target_seconds, min_chars))
        if "## 场景" not in txt:
            why.append("没有场景头。每一场必须以 `## 场景N｜地点名｜时间 内/外` 开头")
        for tag, label in (("【在场】", "在场人物"), ("【空间】", "场地尺寸和布局"),
                           ("【站位】", "开场站位")):
            if tag not in txt:
                why.append("缺 %s 标签（%s）" % (tag, label))
        # 只允许人物卡或骨架已经点名的人进入【在场】。群众/宾客如果骨架写了就
        # 允许；模型不能为了让场面热闹，临时捏出“管家甲”“护卫乙”这类新角色。
        unknown_presence = []
        for raw_line in re.findall(r"^【在场】\s*(.+)$", txt, re.M):
            for raw_name in re.split(r"[、,，；;]", raw_line):
                clean_name = re.sub(r"[（(].*?[）)]", "", raw_name).strip()
                if clean_name and clean_name not in allowed_presence:
                    unknown_presence.append(clean_name)
        if unknown_presence:
            why.append("【在场】混入人物卡和骨架都没有的人物：%s。"
                       "删掉，或改回骨架已有的群众/宾客身份"
                       % "、".join(dict.fromkeys(unknown_presence)))
        # 对白按骨架需要校验；纯行动、环境或无声表演不强塞对白。
        need_dialogue = any(bool(b.get("has_dialogue"))
                            for x in (skeleton.get("scenes") or [])
                            for b in (x.get("beats") or []))
        _names = [str(c.get("name") or "") for c in ((setup or {}).get("characters") or [])
                  if c.get("name")]
        if not _names:
            _names = list(dict.fromkeys(
                str(n) for x in (skeleton.get("scenes") or [])
                for n in (x.get("chars") or []) if str(n).strip()))
        try:
            from . import script_text as _stx
            _dlg = _stx.dialogue_lines(txt, _names) if _names else []
        except Exception:
            _dlg = []
        if need_dialogue and len(_dlg) < 1:
            why.append("骨架标记了 has_dialogue=true，但正文没有可解析对白。"
                       "至少写一句「人名：台词」，人名后直接跟冒号")
        # 【对白密度下限】实测三个题材各写一话：末日 120 秒只有 2 句台词，
        # 校园群像喜剧 200 秒只有 1 句。群像喜剧的核心就是几个人斗嘴，
        # 一整话一句等于这个题材没生效。指令词里"可以没有对白"那句被用足了，
        # 而校验这边一直没有任何密度要求。
        _floor, _pace = dialogue_floor(settings, skeleton)
        _have = count_dialogue(txt, list(allowed_presence - {"无", "无具体人物"}))
        if _floor and _have < _floor:
            why.append("整话只有 %d 句台词，太哑了。这一话约 %d 秒、题材是「%s」，"
                       "这类戏平均每 %d 秒就该有一句，至少要 %d 句。"
                       "把人物之间的交涉、试探、顶撞、示弱写成实际说出口的话，"
                       "不要全用动作和旁白代替"
                       % (_have, _target_seconds(settings, skeleton),
                          str((settings or {}).get("genre_engine") or "未定"),
                          _pace, _floor))
        # 【开场必须是建立镜头】一句话故事的第一镜要先把世界给出来。
        # 实测末日那一话开场第一镜是特写，观众还不知道在哪就先看了个局部。
        _open = opening_framing(txt)
        if _open and _open not in OPENING_FRAMINGS:
            why.append("开场第一镜是「%s」。第一镜要先交代这是什么地方、"
                       "什么世界，用大全景、远景或全景，把环境给足了再往里推"
                       % _open)
        from . import project_prompt
        incompatible = project_prompt.story_world_violations(txt, settings or {})
        if incompatible:
            why.append("正文混入了与已定世界时代冲突的内容（%s）。用该世界已有的工具、"
                       "魔法或炼金手段替代" % "、".join(incompatible))
        hard = project_prompt.story_requirement_violations(txt, settings or {}, tail_only=True)
        if hard:
            why.append("正文违反不可更改的故事要求：" + "；".join(hard))
        missing = [a for a in anchors if not _anchor_present(a, txt)]
        if missing:
            why.append("这些东西没在故事里出现，对应的设定事件丢了（写到就行，"
                       "不必逐字重复原词）：" + "、".join(missing))
        if str(diagnosis or "").strip() and previous_norm:
            current_norm = re.sub(r"\s+", "", _prefix_clean(txt))
            if current_norm == previous_norm:
                unchanged_revision = True
                why.append("终稿与上一稿规范化后完全相同，医生诊断没有落实。"
                           "必须逐条修改对应位置，不得原样交回")
        score = len(txt) - 500 * len(why)
        # 医生重写时，原样交回的候选没有“终稿”资格。否则连续三次不改，
        # 循环结束后仍会把同一篇原稿当成 best 悄悄保存，形成假成功。
        if not unchanged_revision and score > best_score:
            best, best_score, note = txt, score, ("；".join(why) if why else "")
        if not why:
            return txt, ""
        user = user + "\n\n上一稿的问题（必须改掉）：" + "；".join(why)
    if str(diagnosis or "").strip() and previous_norm and not best:
        raise RuntimeError("医生诊断后的终稿连续 %d 次未产生实际改动，已停止保存假终稿" % TRIES)
    return best, note


# ---------- 引擎自动挡 ----------
# 用户定的：引擎不要手选。从一句话设定自动判：规则直判优先（快、可解释），
# 多个引擎都沾边或一个都不沾时才问一次 Qwen。判定结果写回 settings 供页面显示。

ENGINE_KEYWORDS = {
    "打斗型": ("打斗", "决斗", "大战", "厮杀", "对战", "交手", "战斗", "打架",
               "武打", "斗法", "过招", "拔剑", "出手", "围攻", "单挑",
                   "拳场", "拳赛", "擂台", "比武", "较量", "再打一场", "打赢", "打废", "格斗", "搏斗", "对决", "砍", "刺"),
    "悬疑压迫型": ("悬疑", "谋杀", "失踪", "诡异", "凶手", "尸体", "调查", "线索",
                   "恐怖", "阴森", "跟踪", "密室", "怪谈",
                   "真相", "发现", "死了的人", "事故", "失联", "异常", "不对劲", "瞒着", "秘密", "幽灵", "闹鬼", "不见了"),
    "感官特写型": ("走光", "内裤", "裙摆", "身材", "性感", "诱惑", "特写身体",
                   "曲线", "贴身", "湿身"),
    "关系推进型": ("暧昧", "接吻", "相遇后", "逐渐吸引", "靠近", "告白", "初恋",
                   "分手", "重逢", "心动"),
    "群像喜剧型": ("三个女孩", "几个女孩", "抢着",
                   "喜剧", "搞笑", "日常", "轻松", "闹剧", "小队", "同伴",
                   "勇者队伍"),
    "爽点打脸型": ("打脸", "逆袭", "扮猪吃虎", "废柴", "开挂", "金手指", "系统",
                   "退婚", "被看不起", "扮猪", "隐藏身份", "低调", "重生", "爽文",
                   "羞辱", "看不起", "衣锦还乡", "逐出", "赶出家门", "嘲笑", "瞧不起", "状元", "翻身", "扬眉吐气", "跪了一地", "当众"),
    "生存压迫型": ("末日", "废土", "丧尸", "灾难", "求生", "生存", "物资",
                   "补给", "避难", "文明崩坏", "感染", "幸存者", "资源短缺",
                   "难民", "围困", "断水", "断粮", "最后一座", "最后一个", "配给"),
    "成长蜕变型": ("修炼", "突破", "觉醒", "拜师", "入门", "学艺", "晋级",
                   "境界", "变强", "领悟", "特训", "试炼", "传承",
                   "点破", "指点", "开窍", "瓶颈", "顿悟", "第一次学会",
                   "修真", "修仙", "法术", "体术", "功法", "灵根", "筑基",
                   "结丹", "元婴", "天赋", "资质", "弟子", "师兄", "师父"),
    "情感虐心型": ("误会", "错过", "牺牲", "离别", "诀别", "遗书", "绝症",
                   "隐瞒", "替身", "白月光", "意难平", "虐心", "相认", "认出",
                   "遗物", "瞒着病情", "分手", "临终", "来不及"),
    # 实测漏的一类：「勇者小队进入迷宫探险」十个引擎一个都没命中要害，
    # 只靠"小队"两个字被判成群像喜剧，整部 12 话的框架就按喜剧生成了。
    "冒险闯关型": ("迷宫", "探险", "副本", "闯关", "地下城", "地牢", "古墓",
                   "寻宝", "夺宝", "机关", "陷阱", "关卡", "禁地", "遗迹",
                   "探索", "冒险", "深入", "秘藏", "宝藏", "遗址", "废墟探",
                   "打捞", "生还者", "殖民舰", "沉船", "往下", "一层层", "深处"),
    # 后宫：一男多女，都围着主角，暧昧同时推进。和群像喜剧的区别是
    # 感情线（争的是主角本人），不是抢同一个外部目标。
    "后宫环绕型": ("后宫", "争风吃醋", "吃醋", "青梅竹马", "两女", "三女",
                   "众女", "多个女孩", "一群女", "环绕", "都喜欢他",
                   "都喜欢我", "争抢主角", "齐人之福", "三妻四妾", "多女主",
                   "众多美女", "美女环绕", "桃花运", "艳福"),
    # 情色片：欲望驱动的叙事，靠试探/靠近层层累积。露骨程度由内容尺度控制，
    # 这里只判"这是不是一个以情欲推进为主线的故事"。
    "情色片型": ("情色", "色情", "情欲", "欲望", "床戏", "香艳", "缠绵",
                 "情事", "肉欲", "勾引", "挑逗", "偷情", "出轨", "云雨",
                 "春宵", "情人", "调情", "床上", "激情", "禁忌之恋", "肉体"),
}

# 弱关键词：只说明"故事里有什么"，不说明"张力从哪来"。
# "小队"只是告诉你有几个人，它既可能是喜剧也可能是探险也可能是战争。
# 这类词单独命中不足以定案，必须有别的强词一起。
WEAK_KEYWORDS = {
    "小队", "同伴", "日常", "轻松", "弟子", "师兄", "师父", "低调",
    "靠近", "出手", "调查", "线索", "身材", "贴身", "深入", "探索",
}
STRONG_W, WEAK_W = 1.0, 0.4
# 打分低于这条线就等于"没看出来"，交给模型判，别让一个弱词定了整部戏。
CONFIDENT = 1.0

# 【引擎名单是唯一事实源】加引擎只改这一处，判定、兜底、问模型三处自动跟上。
# 原来"五个引擎"写死在三个地方，加一个引擎要改三处，必漏。
ENGINE_HINTS = {
    "感官特写型": "张力=被看见的身体/瞬间",
    "剧情冲突型": "张力=想要而受阻",
    "关系推进型": "张力=两人距离的变化",
    "打斗型": "张力=武力对抗",
    "悬疑压迫型": "张力=信息差和未知",
    "群像喜剧型": "张力=几个人用互不相同的方式抢同一个目标，谁也没赢",
    "爽点打脸型": "张力=先被当众看轻，再当众兑现真实身份",
    "生存压迫型": "张力=资源有限，每个选择都要付出可见的代价",
    "成长蜕变型": "张力=一直靠的那套老办法这次失灵了",
    "情感虐心型": "张力=观众知道那份心意，当事人不知道",
    "冒险闯关型": "张力=前方一关接一关，每一关都可能过不去，而退路已经断了",
    "后宫环绕型": "张力=一个主角身边围着多个各有魅力的角色，暧昧同时推进、谁也不退场",
    "情色片型": "张力=欲望在层层试探与靠近里累积，克制与放纵的拉扯",
}
ENGINE_NAMES = list(ENGINE_HINTS)

# 【每话的情绪节拍】这是题材引擎真正管的东西——不是对白密度，而是"每一话怎么
# 起伏"。查过爽文/恋爱喜剧/后宫的结构后定的：所有题材的共同底线是"每话一个
# 完整的情绪循环，不是流水账"，但循环的形状按题材不同。
# 框架分话、第一话怎么写，都读这里。
ENGINE_BEAT = {
    "爽点打脸型": "先憋屈（主角被当众看轻、受压、吃瘪，读者跟着难受），憋到位，"
                  "再当众反转爆发、狠狠打脸释放。一话至少走完一个「憋→爆」。",
    "成长蜕变型": "先受挫（老办法失灵、被现实教训），卡住、挣扎，再顿悟或突破，"
                  "拿到一点新东西。一话是一次「挫→悟→进」。",
    "关系推进型": "两人先有一个心动或靠近的瞬间，中间来一个误会或阻碍把距离拉开，"
                  "结尾又靠近一步。一话是一次「近→隔→更近」，留一点甜。",
    "群像喜剧型": "一个误会或巧合起头，几个人用互不相同的方式搅进来、笑料层层升级，"
                  "结尾一个反转收场。一话是一次「起哄→升级→翻车」，要好笑。",
    "情感虐心型": "先铺一段温情或美好，让读者在乎，再揭出残酷的真相或错过，"
                  "结尾把揪心留住。一话是一次「暖→痛」，观众比当事人先知道。",
    "悬疑压迫型": "抛一个谜或异常，顺着线索逼近，局部揭露一点，又带出更大的谜。"
                  "一话是一次「疑→查→揭一角→更深的疑」。",
    "打斗型": "先摆出势均力敌或被压制的局面，交手中一步步紧逼、险象环生，"
              "结尾一个决定胜负或翻盘的动作。一话是一次「对峙→缠斗→定胜负」。",
    "生存压迫型": "先亮出匮乏和威胁，被迫在有限资源里做取舍，付出可见的代价，"
                  "结尾处境变得更险。一话是一次「困→抉择→代价」。",
    "冒险闯关型": "进入一个新关卡/新险境，遇到看似过不去的难题，用智慧或配合破关，"
                  "结尾露出下一层更大的险。一话是一次「入局→破关→更深的险」。",
    "剧情冲突型": "先立起主角想要的东西和挡在前面的阻力，冲突升级、逼到墙角，"
                  "结尾一个转折改变力量对比。一话是一次「想要→受阻→转折」。",
    "感官特写型": "把镜头停在一个人物的魅力或一个撩人的瞬间上，慢慢渲染、逐步推近，"
                  "让那份吸引力落地。一话是一次「注视→靠近→定格」，重氛围。",
    "后宫环绕型": "这一话让一个角色的魅力和与主角的暧昧线往前走一步（新登场、"
                  "旧角色升温、或两个角色之间的小争风），主角左右逢源但不摊牌。"
                  "一话推进一条暧昧线，其余角色保持存在感，谁也不退场。",
    "情色片型": "欲望不是一上来就满足，而是靠一次次试探、擦边、靠近慢慢累积——"
                  "先有克制或阻隔（身份、场合、犹豫），张力憋到位，再放纵释放。"
                  "一话是一次「试探→拉扯→靠近/释放」，情绪比动作更重要。",
}


def engine_beat(name):
    """某个题材引擎的每话情绪节拍。找不到时给一句通用底线。"""
    return ENGINE_BEAT.get(str(name or "").strip(),
                           "每一话是一个完整的情绪循环：有铺垫、有起伏、有一个让读者"
                           "有感觉的节点（爽/甜/揪心/悬），不要流水账赶进度。")


def detect_engine(premise, settings=None, call_model=None):
    """一句话设定 → 引擎名。打分直判，平票才问 Qwen。

    【为什么从"恰好命中一个"改成打分】关键词表从 4 组扩到 9 组以后，
    一句话同时沾上两三个引擎是常态（"末日里开挂打脸"三个都沾）。
    原来的写法只要不是恰好命中一个就丢给模型，等于把好不容易写厚的
    预设扔了、回去让 35B 现场猜——那正是服装库踩过的坑。
    现在数命中个数，多的赢；只有真的分不出来才问模型。
    """
    text = str(premise or "") + " " + str((settings or {}).get("one_line_focus") or "")
    scored = []
    for name, kws in ENGINE_KEYWORDS.items():
        hits = [k for k in kws if k in text]
        n = sum(WEAK_W if k in WEAK_KEYWORDS else STRONG_W for k in hits)
        if n:
            scored.append((n, name, hits))
    scored.sort(key=lambda x: (-x[0], x[1]))
    winners = []
    if scored:
        top = scored[0][0]
        winners = [x[1] for x in scored if x[0] == top]
        # 只有分数够高、且没有并列时才直接定案。一个弱词（"小队"）打 0.4 分，
        # 达不到 CONFIDENT，会落到下面去问模型——这正是它该走的路。
        if len(winners) == 1 and top >= CONFIDENT:
            return winners[0], "关键词打分（%.1f 分：%s）" % (
                top, "、".join(scored[0][2][:4]))

    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=30, **kw)
    # 平票时只在并列的几个里选，不把已经排除掉的重新放回来
    pool = winners or ENGINE_NAMES
    try:
        raw = call_model(
            "判断这个故事设定的张力来源，从下面的引擎里选一个，只输出引擎名，不解释：" + NL
            + NL.join("%s（%s）" % (n, ENGINE_HINTS.get(n, "")) for n in pool),
            "设定：" + text[:500], temperature=0.1)
        for n in pool:
            if n in str(raw):
                return n, "模型判定"
    except Exception:
        pass
    return (winners[0] if winners else "剧情冲突型"), "兜底"


# ---------- 故事核 + 医生两遍制（v2 链） ----------
# 用户的判断：故事不行，后面全盘皆输。所以火力全部集中在这一步：
#   故事核（欲望/对手/抉择/翻转）→ 骨架（每拍翻转价值）→ 初稿 → 医生诊断 → 终稿

CORE_INS = (Path(__file__).resolve().parent.parent / "presets" / "instructions"
            / "故事_故事核_指令词.txt")

DOCTOR_CHECKLIST = """按下面的清单诊断这篇故事稿，只列问题不夸优点，每条问题给出具体位置和改法：
1. 用户事实与本话范围：只报告与用户已写事实直接矛盾、改变因果或改变本话范围的新增/删改。
   为连接既定动作补出的合理细节不算篡改；例如仙侠冲突中的灵力屏障，若不改变事件结果，
   不能仅因用户没逐字写出就判错。
2. 因果链：段与段之间是「因此/但是」还是「然后」？点出所有流水账连接。
3. 核心抉择演出来了吗？有没有停顿、拉扯、代价，还是一笔带过？
4. 对白：只在人物需要交涉、冲突或表达时检查；纯行动或氛围段不强求对白。
5. 对手是"谁"还是"一群影子"？
6. 只检查关键戏：该发生选择、关系变化或局势变化却没有翻转时指出；建置、过渡、
   日常停顿和纯氛围段不要求为了达标硬造价值翻转。
7. 结尾的主角和开头有没有不同？
输出问题清单，一行一条，不超过 8 条。没有问题就输出「通过」。"""


def generate_story_core(premise, settings=None, call_model=None, setup=None):
    """一句话 → 故事核 JSON。"""
    from .model_json import chat_json
    ins = CORE_INS.read_text(encoding="utf-8")

    def _need(d):
        from . import project_prompt
        import json as _json
        serialized = _json.dumps(d, ensure_ascii=False)
        incompatible = project_prompt.story_world_violations(serialized, settings or {})
        if incompatible:
            raise ValueError("故事核混入了与世界时代冲突的内容：" + "、".join(incompatible))
        hard = project_prompt.story_requirement_violations(serialized, settings or {})
        if hard:
            raise ValueError("故事核违反不可更改要求：" + "；".join(hard))
        cs = d.get("characters") or []
        if not cs:
            raise ValueError("没有人物")
        if not any(c.get("role") == "主角" for c in cs):
            raise ValueError("没标主角")
        opp = [c for c in cs if c.get("role") == "对手"]
        if opp and not any(str(c.get("just_goal") or "").strip() for c in opp):
            raise ValueError("对手没有正当目标——纸片恶人立不住")
        cc = d.get("core_choice") or {}
        for k in ("moment", "option_a", "option_b", "picks"):
            if not str(cc.get(k) or "").strip():
                raise ValueError("核心抉择缺 " + k)
        if not isinstance(d.get("value_turns"), list):
            raise ValueError("value_turns 必须是列表；数量按题材、时长和实际关键变化决定")

    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=2000, **kw)
    user = "\n".join(["一句话设定：" + str(premise or "")]
                     + _setup_lines(setup, settings))
    d, _ = chat_json(ins, user, call_model,
                     validate=_need, tries=3, temperature=0.7)
    return d


def doctor_review(body, call_model=None, *, premise="", skeleton=None,
                  setup=None, settings=None):
    """医生诊断：返回问题清单文本（"通过" = 没问题）。"""
    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=800, **kw)
    import json as _json
    context = ["一句话设定：" + str(premise or "")]
    if skeleton:
        context.append("本话骨架（已定稿）：" + _json.dumps(skeleton, ensure_ascii=False))
    context += _setup_lines(setup, settings)
    context.append("待诊断故事稿：\n" + str(body or "")[:5000])
    return str(call_model(DOCTOR_CHECKLIST, "\n".join(context),
                          temperature=0.3) or "").strip()


def generate_story_v2(premise, settings=None, call_model=None, on_step=None, setup=None):
    """v2 全链：故事核 → 骨架 → 初稿 → 医生 → 终稿。
    返回 (正文, {"core":…, "skeleton":…, "diagnosis":…})。"""
    def step(m):
        if on_step:
            on_step(m)
    # 【题材引擎自动挡】用户定的：引擎不要手选，但也不能不用。
    # 引擎决定的是节拍形状和秒数配比——这是结构决策，让 35B 现场判断必然飘。
    # 审计发现 detect_engine 写好后从来没人调用，genre_engine 只有读没有写，
    # 于是整个引擎库一直是死的。这里把它接上：没手动指定就自动判一次，
    # 结果写回 settings，页面能看见，骨架和正文两步都会吃到对应的节拍形状。
    settings = settings if isinstance(settings, dict) else {}
    setup = setup if isinstance(setup, dict) else {"characters": [], "scenes": []}
    if not str(settings.get("genre_engine") or "").strip():
        try:
            eng, why = detect_engine(premise, settings, call_model)
            settings["genre_engine"] = eng
            settings["genre_engine_reason"] = why
            step("题材引擎：" + eng)
        except Exception:
            pass
    step("定故事核")
    core = generate_story_core(premise, settings, call_model, setup)
    import json as _json
    core_line = "故事核（已定稿，骨架和正文都必须服从）：" + _json.dumps(core, ensure_ascii=False)
    step("写骨架")
    # 真实人物卡和场景卡必须贯穿故事核、骨架、正文和医生诊断。
    sk = generate_skeleton(str(premise) + "\n" + core_line,
                           setup, settings, call_model)
    step("写初稿")
    draft, _n = generate_from_skeleton(str(premise) + "\n" + core_line, sk,
                                       setup, settings, call_model)
    step("医生诊断")
    diag = doctor_review(draft, call_model, premise=premise, skeleton=sk,
                         setup=setup, settings=settings)
    if "通过" in diag[:10] and len(diag) < 30:
        return draft, {"core": core, "skeleton": sk, "diagnosis": "通过",
                       "engine": settings.get("genre_engine") or ""}
    step("按诊断重写终稿")
    final, _n2 = generate_from_skeleton(
        str(premise) + "\n" + core_line,
        sk, setup, settings, call_model, previous_body=draft, diagnosis=diag)
    return final, {"core": core, "skeleton": sk, "diagnosis": diag,
                   "engine": settings.get("genre_engine") or ""}


def generate_shotlist(premise, settings=None, call_model=None, setup=None, on_step=None):
    """只出【故事核 + 场次表】，不写正文。

    【为什么要拆出来】场次表原来只是 generate_story_v2 的中间产物，正文不写
    就拿不到。但用户要确认的正是场次表——「四场戏、哪儿、内外、多少秒、干什么」
    一眼能看出结构对不对；而一句话故事简介读着挺顺，四个毛病一个都看不出来。
    确认门必须开在写正文之前（正文 9 分钟，场次表 2 分钟），
    所以这一步必须能单独跑。

    返回 (skeleton, {"core":…})。
    """
    def step(m):
        if on_step:
            on_step(m)
    settings = settings if isinstance(settings, dict) else {}
    setup = setup if isinstance(setup, dict) else {"characters": [], "scenes": []}
    if not str(settings.get("genre_engine") or "").strip():
        try:
            eng, why = detect_engine(premise, settings, call_model)
            settings["genre_engine"] = eng
            settings["genre_engine_reason"] = why
            step("题材引擎：" + eng)
        except Exception:
            pass
    step("定故事核")
    core = generate_story_core(premise, settings, call_model, setup)
    import json as _json
    core_line = "故事核（已定稿，骨架和正文都必须服从）：" + _json.dumps(core, ensure_ascii=False)
    step("写场次表")
    sk = generate_skeleton(str(premise) + chr(10) + core_line, setup, settings, call_model)
    return sk, {"core": core}
