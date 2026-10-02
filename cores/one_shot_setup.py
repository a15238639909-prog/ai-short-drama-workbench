# -*- coding: utf-8 -*-
"""一句话生成全部设定：把 8848 那条链在新平台上串起来。

用户要的是：**只填一句话，就把世界类型、画风、人物、场景全推出来；不满意我自己改。**

对应 8848 的 `generate_quick_comic_story`(app.py:1832) + `gen_charsheet`(app.py:1736)，
逻辑照搬，不重写：
- 原题是不可替换的硬事实 → 全程走 `premise_guard.expand_with_guard`，偏题自动重来一次
- 人物只保留**正文里真的出现过**的，绝不为了凑数补女主/导师/同伴
- 产出一律是**草稿**，用户点「💾 保存这张」才 adopt 落盘

调用方只需给一个 `call_model(messages) -> str`；本模块不直接依赖任何模型客户端，
所以生成未启用时也能跑（返回门控结果），测试里也能用假模型。
"""
import json
import re

NL = chr(10)

from cores import premise_guard, project_settings, style_presets

__all__ = ["build_messages", "generate_all", "GATED_NOTE"]

GATED_NOTE = "生成轮启用后可用"

# 8848 原文，不要改写
_SYSTEM_BASE = (
    "你是短篇视觉故事的编剧兼角色设计师。把用户的一句话扩写成可直接进入制作的项目设定。"
    + premise_guard.SYSTEM_RULE +
    "只保留 1—3 名真正出场的人物，不会为了凑数自动补女主、导师或同伴。"
    "**必须给每个人物起一个具体名字，并在正文里用这个名字称呼他们**，"
    "不要通篇写「一名男性」「那个女孩」这种没有名字的说法。"
    "场景只列故事真正发生的地点，每个地点写清真实尺度（宽×深、层高、可视纵深多少米）"
    "与空间纵深（前景/中景/背景各多远）。"
    "人物外观只写 3—5 个生图必需的固定辨识特征；儿童、少年、老人必须同时写年龄阶段和可见年龄特征。"
)


def _enum_rules(settings=None):
    """把定义表的可选键名给模型，让它从中挑，而不是自由发挥。

    以前模型返回「黑发微卷，眼神温和」这种自由文本，和下拉预设对不上，
    结果长相/身材/性格全落回"自动"，那几张定义表用不上。
    """
    from cores import style_presets as sp
    L = []
    for sex in ("男", "女"):
        faces = sp.char_field_options("长相", sex)[1:]
        bodies = sp.char_field_options("身材", sex)[1:]
        if faces:
            L.append("%s性 face_type 只能从这些里选：%s" % (sex, "、".join(faces)))
        if bodies:
            L.append("%s性 build 只能从这些里选：%s" % (sex, "、".join(bodies)))
    if sp.POSE_DEFS:
        L.append("behavior_anchor 只能从这些里选：" + "、".join(list(sp.POSE_DEFS)))
    cloth = sp.char_field_options("服装轮廓", settings=settings or {})[1:-1]
    if cloth:
        L.append("clothing_requirement 只能从这些里选（这是这个世界的服装底子）：" + "、".join(cloth))
        try:
            _defs = sp.cloth_def
            L.append("每个底子的**结构**长这样（只当骨架参考）：" + "；".join("%s＝%s" % (c, _defs(c)) for c in cloth if _defs(c)))
            L.append("★ clothing（具体服装）**不许照抄底子的原句**：主色、副色、材质、剪裁长短、开衩/露出的位置、配件和标志物都要按这个人的身份、性格和故事重新写，"
                     "同一组人物里两个人不许用同一个底子加同样的配色。底子只决定「这是骑士甲还是法袍」，其它全是你的设计。")
        except Exception:
            pass
    try:
        from cores import project_prompt
        outfits = project_prompt.outfit_preset_options(settings or {})[1:-1]
        if outfits:
            L.append("outfit_preset 服装方向只能从这些里选：" + "、".join(outfits))
    except Exception:
        pass
    if not L:
        return ""
    return ("%s【必须从给定选项里挑，不要自己造词】%s" % (NL, NL)) + (NL.join(L))


_JSON_SPEC = (
    "严格输出 JSON。**下面每一个字段都必须填满**，一个都不许留空、不许写「自动」——"
    "界面上有这个框，你不填用户就得自己一个个手打。" + NL
    + '{"title":"短标题","world_type":"世界类型","style":"画风名",'
      '"look":"整部作品长什么样","story":"故事简介，200字左右",'
      '"characters":[{"name":"姓名","age":"例：32岁","sex":"男或女",'
      '"char_type":"从选项里挑","face_type":"从选项里挑","build":"从选项里挑",'
      '"behavior_anchor":"性格与神态，从选项里挑","hair":"发色+发式+长度落点",'
      '"outfit_preset":"服装方向，从当前世界的选项里挑",'
      '"clothing_requirement":"服装轮廓，从选项里挑",'
      '"clothing":"具体服装描述：内搭、外层、颜色、面料、剪裁",'
      '"appearance_details":"瞳色、肤色、皮肤特征和随身固定识别物"}],'
      '"scenes":[{"name":"场景名","space":"这是个什么地方","scale":"真实尺度，写具体米数",'
      '"depth":"近景/中景/远景各多远，写具体米数","materials":"主要材质",'
      '"ground":"地面","walls":"墙面","door":"门","window":"窗",'
      '"furniture":"家具陈设","landmarks":"空间识别点","light":"主光源方向与色温"}]}' + NL
    # 下面三段的写法来自 8848 正式版（用户日常在用的那套），只把"漫画"换成"作品"
    + "【look 整部作品长什么样】用 180 到 300 字的大白话写清六件事："
      "这部作品里的人通常是什么身材和打扮；衣服常用什么款式、颜色和材料；"
      "房子、街道或建筑具体长什么样；画面里常用哪些颜色；"
      "白天和晚上分别用什么灯光；哪些固定的东西能让人一眼认出这是这部作品。"
      "不要写「人物轮廓倾向、视觉语言、结构体系、装饰语言、主辅色谱、审美表达」这类行业术语，"
      "不要写比喻，不要写「日漫、3A、写实、二次元、油画」这类画风词——画风用户在上面单独选。" + NL
    + "【characters】只列故事里真正出场的主角和固定主要人物，不给路人、侍从、群众建卡。"
      "clothing 要写出内搭、外层、颜色、面料和剪裁，不能用「普通长袍」「一身黑衣」这种"
      "概括句代替具体结构。appearance_details 写瞳色、肤色、皮肤特征和随身固定识别物"
      "（例如耳钉、固定疤痕、常戴手表），不写场景家具和临时拿到的剧情道具。"
      "人物卡里只能写人物本身，不能出现房间、街道、森林这些背景，也不写经历和能力评价。" + NL
    + "【scenes】每个场景的十一个字段全部要填。scale 和 depth 必须写具体米数——"
      "没有米数，画出来的空间会变成模型屋或者巨人国。"
      "light 要写清光从哪个方向来、什么色温，后面每一格的人物打光都要照着它来。" + NL
    + "不要解释，不要 Markdown。"
)


def _system(settings=None):
    return _SYSTEM_BASE + _enum_rules(settings) + _JSON_SPEC


def build_messages(premise, settings=None, extra_instruction=""):
    """组装发给模型的消息。settings 里已有的选择会作为约束带上。"""
    s = settings or {}
    lines = ["【不可替换的原始故事核心】" + str(premise or "").strip()]
    if s.get("world_type") and s["world_type"] != "自动判断":
        lines.append("世界类型：" + s["world_type"])
    if s.get("style"):
        lines.append("画风：" + s["style"])
    if s.get("visual_strength") and s["visual_strength"] != "自动":
        lines.append("视觉设计强度：" + s["visual_strength"])
    if s.get("content_tendencies"):
        # 用 CONTENT_TENDENCY_EFFECT 的 story 层描述，而不是把带括号的选项名原样丢过去。
        # 选项名是给人看的标签（"血腥（断肢/内脏/大出血，可到最重）"），
        # 表里那句才是给模型的指示。两边共用一张表，改一处就够。
        try:
            from .style_presets import tendency_effect
            t = tendency_effect(s["content_tendencies"], "story")
        except Exception:
            t = "、".join(s["content_tendencies"])
        if t:
            lines.append("内容尺度：" + t)
    if str(s.get("custom_content_scale") or "").strip():
        lines.append("用户自定义尺度（逐字遵守）：" + str(s["custom_content_scale"]).strip())
    if s.get("extra_requirements"):
        lines.append("不可更改的故事要求：" + s["extra_requirements"])
    try:
        from . import project_prompt
        lines.append("项目视觉与世界规则：\n" + project_prompt.story_settings_block(s))
    except Exception:
        pass
    lines.append("扩写只能补充过程与细节，不得改变上述核心事实。")
    if extra_instruction:
        lines.append(extra_instruction)
    return [{"role": "system", "content": _system(s)},
            {"role": "user", "content": "\n".join(lines)}]


def _parse(raw):
    """从模型输出里抠 JSON，容忍前后多余文字。"""
    # 提取逻辑统一在 cores.model_json：剥围栏、切括号、补尾逗号/中文引号/裸换行。
    # 这里原来只做一次朴素 json.loads，模型多打一个逗号就整个作废。
    from .model_json import extract
    return extract(raw)


def _normalize_enums(c, settings=None):
    """模型万一没照选项挑，程序归一到最接近的合法键——不能让定义表白搭。

    匹配顺序：完全相同 → 选项名出现在模型给的文本里 → 模型文本里的词出现在选项名里。
    都对不上就留空（"自动"），原文照样保留在 look/other，一个字不丢。
    """
    from cores import style_presets as sp
    c = dict(c)
    sex = str(c.get("sex") or "女").strip() or "女"
    if "behavior_anchor" not in c and "personality" in c:
        c["behavior_anchor"] = c.get("personality")
    if "appearance_details" not in c:
        c["appearance_details"] = "；".join(
            x for x in (str(c.get("look") or "").strip(), str(c.get("other") or "").strip()) if x)

    def pick(val, options):
        v = str(val or "").strip()
        if not v or not options:
            return ""
        if v in options:
            return v
        for o in options:
            if o and o in v:          # 选项名整个出现在模型给的词里
                return o
        # 按连续二字重合度打分，取最高；单字重合太容易配错
        # （"鹅蛋脸清秀"曾被配成"幼态圆脸"，"修身"被配成"极贴身"）
        grams = {v[i:i + 2] for i in range(len(v) - 1)}
        best, score = "", 0
        for o in options:
            og = {o[i:i + 2] for i in range(len(o) - 1)}
            n = len(grams & og)
            if n > score:
                best, score = o, n
        return best if score >= 1 else ""

    for field, opts in (
        ("face_type", sp.char_field_options("长相", sex)[1:]),
        ("build", sp.char_field_options("身材", sex)[1:]),
        ("behavior_anchor", list(sp.POSE_DEFS or {})),
        ("clothing_requirement", sp.char_field_options("服装轮廓")[1:-1]),
    ):
        raw = c.get(field)
        hit = pick(raw, opts)
        if hit:
            c[field] = hit
            if raw and raw != hit:
                # 没被选项覆盖的原文并进 look，避免信息丢失
                c["appearance_details"] = (str(c.get("appearance_details") or "") + "；" + str(raw)).strip("；")
        elif raw:
            c["appearance_details"] = (str(c.get("appearance_details") or "") + "；" + str(raw)).strip("；")
            c[field] = ""
    c["build"] = sp.normalize_body_option(sex, c.get("build"))
    c["clothing_requirement"] = sp.normalize_cloth_option(c.get("clothing_requirement"))
    try:
        from cores import project_prompt
        valid_outfits = project_prompt.outfit_preset_options(settings or {})[1:-1]
        if c.get("outfit_preset") not in valid_outfits:
            c["outfit_preset"] = "自动"
        clothing = str(c.get("clothing") or "")
        garment_words = ("袍", "裙", "衫", "衣", "裤", "甲", "靴", "鞋", "外套")
        if len(clothing) < 40 or sum(w in clothing for w in garment_words) < 3:
            c["clothing"] = project_prompt.complete_outfit(c, settings or {}, seed="one-shot")[0]
    except Exception:
        c["outfit_preset"] = str(c.get("outfit_preset") or "自动")
    c["behavior_anchor"] = str(c.get("behavior_anchor") or "")
    # 临时只读别名，兼容旧草稿查看与旧测试；保存人物时只写新字段。
    c["personality"] = c["behavior_anchor"]
    c["look"] = c["appearance_details"]
    c["other"] = c["appearance_details"]
    return c


def generate_all(premise, call_model=None, settings=None):
    """一句话 → 全套草稿设定。

    call_model(messages) -> 模型输出文本；传 None 表示生成未启用，返回门控结果。
    返回 {"ok", "draft", "note", "overlap", "attempts"}；draft 是**草稿**，不落盘。
    """
    premise = str(premise or "").strip()
    if not premise:
        return {"ok": False, "draft": None, "note": "先写一句话故事", "overlap": 0, "attempts": 0}
    if call_model is None:
        return {"ok": False, "draft": None, "note": GATED_NOTE, "overlap": 0, "attempts": 0}

    holder = {}

    def _call(extra):
        raw = call_model(build_messages(premise, settings, extra))
        data = _parse(raw)
        holder["data"] = data
        if not isinstance(data, dict):
            return str(raw or "")
        story = str(data.get("story") or "").strip()
        if story:
            return story
        # 少数 Qwen 响应会正确给出人物、场景和服装，却漏掉 story 键。
        # 相关性守卫不能因此把整份结构化设定误判成空输出；只拿真正的模型字段
        # 做检查，不把用户原题塞进去“凑分”。
        return json.dumps({
            "title": data.get("title"),
            "characters": data.get("characters") or [],
            "scenes": data.get("scenes") or [],
        }, ensure_ascii=False)

    res = premise_guard.expand_with_guard(premise, _call)
    data = holder.get("data")
    if not isinstance(data, dict):
        return {"ok": False, "draft": None, "note": "模型没有返回可解析的设定，请重试或把一句话写得更具体",
                "overlap": res["overlap"], "attempts": res["attempts"]}

    model_story_missing = not str(data.get("story") or "").strip()
    story = str(data.get("story") or premise).strip()
    # 8848 的规矩是"不为凑数补人"，指的是不许无中生有地加角色。
    # 但如果模型把人写进了故事却没在正文里点名（"一名男性…那个女孩…"），
    # 直接丢掉会让用户什么都拿不到——保留，并标出正文有没有点名，界面上提示。
    chars = []
    for c in (data.get("characters") or []):
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if name:
            # 起了名字却在正文里找不到 → 这是凑数补的人（女主/导师/同伴），丢掉
            if name not in story:
                continue
            c = dict(c); c["in_story"] = True
        else:
            # 根本没起名 → 是故事里的真人物，只是模型没点名。留下并给临时名，
            # 提示用户补一个；直接丢掉会让用户什么都拿不到。
            if not (c.get("look") or c.get("age") or c.get("clothing")):
                continue                  # 既没名字也没描述，才是真的空条目
            c = dict(c)
            c["name"] = "人物%d" % (len(chars) + 1)
            c["in_story"] = False
        c = _normalize_enums(c, settings)
        chars.append(c)
        if len(chars) >= 3:
            break
    scenes = [s for s in (data.get("scenes") or []) if isinstance(s, dict) and s.get("name")]

    # 画风：模型给的若在预设表里就用它；不在表里也不要丢，退回用户已选的
    known = []
    for fn in ("styles", "list_styles", "all_styles", "load_styles"):
        f = getattr(style_presets, fn, None)
        if callable(f):
            try:
                known = [x.get("name") if isinstance(x, dict) else str(x) for x in (f() or [])]
                break
            except Exception:
                pass
    picked = data.get("style")
    style = picked if (not known or picked in known) else None
    style = style or (settings or {}).get("style")

    draft = {
        "title": str(data.get("title") or premise)[:30],
        "one_line": premise,
        "story": story,
        "world_type": data.get("world_type") or (settings or {}).get("world_type") or "自动判断",
        "style": style,
        # look = 整部作品长什么样。原来 draft 里根本没带这个键，
        # 模型明明生成了，装配时被丢掉，界面上那个框永远是空的。
        "look": str(data.get("look") or data.get("design_direction") or "").strip(),
        "characters": chars,
        "scenes": scenes,
    }
    unnamed = [c["name"] for c in chars if not c.get("in_story")]
    note = res["note"] or ("已按一句话推出 %d 个人物、%d 个场景，都是草稿，改完再点保存"
                           % (len(chars), len(scenes)))
    if model_story_missing:
        note += "；模型漏了故事简介，已保留原始一句话作为故事底稿，人物和场景没有丢失"
    if unnamed:
        note += "；其中 %s 在正文里没有点名，建议你补个名字" % "、".join(unnamed)
    return {"ok": res["ok"], "draft": draft, "note": note,
            "overlap": res["overlap"], "attempts": res["attempts"]}
