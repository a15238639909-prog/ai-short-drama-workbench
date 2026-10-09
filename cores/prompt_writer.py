# -*- coding: utf-8 -*-
"""prompt_writer.py — 出图提示词的唯一入口：事实 → 指令词 → Qwen → 提示词。

【为什么要有这个模块】
2026-08-24 之前，出图提示词是**代码拼**出来的：
`compile_character_prompt` 245 行、`compile_scene_prompt` 289 行，
里面串着 502 条写死的画面文案（"前景左右各放一根极粗的立柱"
"暖光来自火把、灯笼、法器""画面大面积压在深色里"）。

每一条当初都是照着某一个具体案例写的，本身不带说明书。
而语境有 8 世界 × 12 画风 × 内外 × 11 题材 × 时间 = 8448 种。
502 × 8448 = 四百多万个"这句话在这里成不成立"的判断，
全靠人一格一格撞见、一次一次补 if——三天补了 19 个，
`compile_scene_prompt` 里为此长出 38 个分叉。

实测对照（同一张人物卡）：
    代码拼 1283 字：元指令、自相矛盾（"肩腰比例正常"+"极窄腰极端纤细"）、
                    发型重复 3 次、五官档原样照抄（同档角色必然撞脸）、
                    把"采用一处非对称层次"这种方法论当画面塞进去
    Qwen 写  491 字：没有元指令、没有矛盾、没有重复；
                    把"非对称层次"翻译成了"左肩甲片略长于右肩"

**代码不懂语境，Qwen 懂。**所以代码只做两件事：
  1. 汇总事实（卡上有什么）
  2. 精简成这次要展示的那一句话
剩下的交给指令词和 Qwen。

【GPU 顺序】三方互斥（Qwen / Krea2 / H3 只能有一个在显存里），
切换一次约 35 秒。所以必须**先把所有提示词一次性写完，再交棒出图**——
每张图前调一次 Qwen 会产生 8 次切换，光切换就浪费 280 秒。
批量入口见 write_many()。
"""
import time
from pathlib import Path

INS_DIR = Path(__file__).resolve().parent.parent / "presets" / "instructions"

# 三种图各用哪份指令词。加新的图种就在这里加一行，不改代码。
INSTRUCTIONS = {
    "character": "人物设定图_用户版_指令词.txt",
    "scene": "场景图_指令词.txt",
}


# ---------- 人设图构图跟着画风走（用户 2026-08-26 定）----------
# 【为什么】人设图指令词把构图写死成「游戏角色设定板：白底 + 三视图并排 + 右侧特写」。
# 那是游戏资产图的长相，**天生一眼 AI**——画风只换了末尾的质感描述，构图纹丝不动，
# 所以选了「超仿真生活照」出来的还是白底拼版，完全没有生活照的样子。
# 现在：写实类画风换成单张全身自然照；动漫/游戏/插画类保留设定板（那种风格本来就该长这样）。
PHOTO_STYLES = ("超仿真生活照", "电影级剧照", "80年代胶片电影")

# 要被替换掉的整段设定板构图（从「生成目标：」到构图段结束）
_SHEET_BLOCK_HEAD = "生成目标："
_SHEET_BLOCK_TAIL = "方便后续制作漫画、动画、影视。"

# 换上去的单张全身照构图（用户定：全身站姿，服装要完整看得见——它还要当 H3 的人物参考图）
_PHOTO_BLOCK = """生成目标：
一张**真人照片**——用相机给这个人拍的一张全身照。
不是插画、不是CG渲染、不是游戏原画、不是角色设定稿。

构图：
- **画面里只有这一个人，只有一张照片**，不许拼版、不许并排多个视图、不许分格。
- **全身入镜**：从头顶到鞋底完整可见，头顶上方和脚下各留一点余地，不裁头不裁脚。
- 站姿自然放松（不是立正、不是摆拍姿势），正面或略微侧身面向镜头，能看清整套服装。
- 相机大约在人物胸口高度，标准镜头的透视，不俯不仰不广角变形。

人物——**干净、好看**：
- 真人质感：真实的皮肤、真实的发丝、真实的布料垂坠，是照片里的人，不是画出来的人。
- 人物本身体面好看：衣服整洁平整、头发打理过、气色好、状态干净。
- **不要脏、不要破损、不要邋遢、不要蓬头垢面。**

背景——**干净、好看**：
- 符合身份和世界观的真实场景（教室走廊、街边、房间、庭院…），环境本身**整洁、明亮、好看**。
- 背景简洁不杂乱：元素少而干净，自然虚化，主体突出。
- **不要堆满杂物、不要脏乱破败、不要昏暗压抑。**
- 不许白色/纯色/棚拍背景，不许设计稿版式。

光线：
- 自然光（窗光、天光、室内漫射光…），柔和、有方向，不是影棚布光。

★**"真实感"来自拍摄质量，不是来自脏乱**——记住这条：
画面是随手拍下来的、没有经过商业精修：轻微噪点、构图不刻意、没有磨皮和过度调色，
所以它看起来像真照片。
但**被拍的这个人、和他所处的环境，本身是干净好看的**。"""

# 【角色身份也要换】指令词第一行把 Qwen 定成"游戏角色原画师"——写实画风下这是人物
# 不写实的主因（原画师的笔下天然是插画/CG）。照片模式换成摄影师。
_SHEET_ROLE = "你是一名专业的电影角色概念设计师、游戏角色原画师和Krea2高级提示词工程师。"
_PHOTO_ROLE = ("你是一名专业的人像摄影师和Krea2高级提示词工程师。"
               "你要写的是**一张真人照片**的提示词——真实的人、真实的光、真实的材质，"
               "不是插画、不是CG、不是游戏原画。")

# 输出段里那句"三视图加特写那张图"也要跟着改
_SHEET_OUT_LINE = "这一段是完整的角色设定图提示词，就是上面说的三视图加特写那张图，一整段中文。"
_PHOTO_OUT_LINE = "这一段是完整的出图提示词，就是上面说的那张单人全身自然照，一整段中文。"

# 禁止清单里针对三视图的几条，照片模式下要换掉
_SHEET_BANS = """- 三个视图人物大小不一致
- 特写或其它元素压住全身视图
- 全身视图被裁掉头顶或脚"""
_PHOTO_BANS = """- 拼版、并排多视图、分格版式
- 白色或纯色棚拍背景
- 裁掉头顶或脚
- 影棚布光的精修人像感、游戏立绘感"""


def _is_photo_style(style):
    s = str(style or "")
    return any(k in s for k in PHOTO_STYLES)


def _swap_composition(out, style):
    """写实画风：把设定板构图整段换成单张全身照。"""
    if not _is_photo_style(style):
        return out
    i = out.find(_SHEET_BLOCK_HEAD)
    j = out.find(_SHEET_BLOCK_TAIL)
    if i >= 0 and j > i:
        out = out[:i] + _PHOTO_BLOCK + out[j + len(_SHEET_BLOCK_TAIL):]
    out = out.replace(_SHEET_ROLE, _PHOTO_ROLE)
    out = out.replace(_SHEET_OUT_LINE, _PHOTO_OUT_LINE)
    out = out.replace(_SHEET_BANS, _PHOTO_BANS)
    return out


def _call(system, user, max_tokens=2400, temperature=0.7):
    # 1800 时场景板提示词被截断过（结尾停在"纱幔的淡紫、"半句上，2026-08-28 实测）
    from models import qwen_client
    return (qwen_client.chat(system, user, temperature=temperature,
                             max_tokens=max_tokens, reasoning=False) or "").strip()


def _project_block(settings):
    """项目设定填进指令词的那几个占位。

    指令词里世界观和画风原本是写死的（"14-16世纪欧洲奇幻""日漫+西方奇幻电影写实"），
    那是单项目的写法。工作台要支持 8 世界 × 12 画风，这两处必须变成变量。
    """
    s = settings or {}
    # 内容尺度进人设图走 figure 层的细描述（不是光标签），"成人向"的身材/露肤才真的进画面。
    try:
        from . import style_presets
        base = (style_presets.tendency_effect(s.get("content_tendencies") or [], "figure") or "").strip()
    except Exception:
        base = "、".join(str(x) for x in (s.get("content_tendencies") or []) if x)
    custom = str(s.get("custom_content_scale") or "").strip()
    if custom:
        # 【自定义尺度接口】用户自己写的这段逐字遵守，优先级最高，预设不许覆盖。
        scale = (base + "\n【用户自定义尺度——逐字遵守】：" + custom) if base \
            else ("【用户自定义尺度——逐字遵守】：" + custom)
    else:
        scale = base
    return {
        "world": str(s.get("world_type") or "").strip(),
        "style": str(s.get("style") or "").strip(),
        "strength": str(s.get("visual_strength") or "").strip(),
        "look": str((s.get("look_panel") or {}).get("look") or "").strip(),
        "scale": scale,
        "extra": str(s.get("extra_requirements") or "").strip(),
    }


def _style_text(b, kind="character"):
    """画风名称与媒介类型。

    这里只说明“照片 / 二维绘画 / 三维渲染”，不放人物年龄、比例、身材、
    五官结构、发型、服装或姿势。具体成像质感由 _quality_text 按人物/场景
    各读各的定义，避免同一段画风重复两遍，也避免人物误读 scene 文案。
    """
    sty = b["style"] or "电影级写实"
    try:
        from . import style_presets as _spz
        _kind = _spz.style_kind(sty)
    except Exception:
        _kind = ""
    anime = ("日漫", "动漫", "赛璐璐", "插画", "水墨", "手绘", "像素", "动画", "漫画")
    if _kind == "2d" or (not _kind and any(k in sty for k in anime)):
        medium = "这张图采用二维绘画成像，线条、色块、笔触和绘制光影构成画面。"
    elif _kind == "3d" or "3D" in sty or "游戏模型" in sty:
        medium = "这张图采用三维角色与场景渲染，形体、材质和灯光具有清楚的三维层次。"
    else:
        subject = "人物" if kind == "character" else "场景"
        medium = "这张图采用真实摄影成像，以%s为主体，保留真实材质、光线和镜头质感。" % subject
    return "%s。\n%s" % (sty, medium)


def _world_text(b):
    """世界观那一段：世界名 + 它的定义（含默认人种）。

    2026-09-05 之前只放名字，预设表里「西方＝欧洲白人五官」这类定义从没进过出图链。
    """
    w = b["world"] or "按用户描述判断"
    try:
        from . import style_presets as _sp
        d = (_sp.world_def(b["world"]) or "").strip() if b["world"] else ""
    except Exception:
        d = ""
    return ("%s。%s\n按这个世界的时代、技术上限、服装体系和建筑材料来。%s"
            % (w, ("\n" + d) if d else "",
               ("\n整部作品的美术基调：" + b["look"]) if b["look"] else ""))


def _quality_text(b, kind="character"):
    """人物只读 char，场景只读 scene，谁的质感谁负责。

    【为什么不写死】原来写死「超写实电影概念艺术／Unreal Engine 5」，
    那套是电影CG，套到「超仿真生活照」上会冲成影棚级完美人像
    （实测男主那张就是CG大片，不是手机随拍）。
    """
    try:
        import json as _json
        rn = _json.loads((INS_DIR.parent / "render_notes.json").read_text(encoding="utf-8"))
        entry = rn.get(b["style"]) or {}
        key = "scene" if kind == "scene" else "char"
        tex = str(entry.get(key) or "").strip()
        if kind == "scene" and entry.get("scene_modern"):
            world = str(b.get("world") or "").strip()
            if not world or world.startswith("现代"):
                tex = (tex + " " + str(entry.get("scene_modern") or "").strip()).strip()
    except Exception:
        tex = ""
    return tex or "真实的皮肤纹理、布料纤维和材质质感，光影自然。"


def _strength_text(b):
    """视觉设计强度：把定义（几头身）一起给，只给标签 Qwen 不知道那是什么。"""
    try:
        from . import style_presets as _sp
        sd = (_sp.intensity_def(b["strength"]) or "").strip()
    except Exception:
        sd = ""
    if sd:
        return "视觉设计强度：%s\n  └ %s" % (b["strength"], sd)
    return "视觉设计强度：%s" % b["strength"] if b["strength"] else ""


def _scale_text(b):
    """内容尺度：勾了才有，没勾就是空。"""
    if not b["scale"]:
        return ""
    return ("## 内容尺度（这个项目定的，要体现在画面里）\n"
            "本项目的内容尺度是：%s。\n"
            "在不违反上面构图和世界观规则的前提下，让人物的身材曲线、"
            "服饰的贴身/露肤程度、眼神姿态的魅惑感，往这个尺度靠。"
            "用正面、具体的画面描述来体现，不要写成规则或标签。" % b["scale"])


def _extra_text(b):
    """用户填的额外美术要求。

    以前只进文字链，出图链从来收不到——第三张场景板素白简陋就是这么来的
    （2026-08-28 实测）。
    """
    if not b["extra"]:
        return ""
    return "## 用户的额外美术要求（整部作品适用，照办）\n" + b["extra"]


# 占位符 → 拿什么去换。指令词文件里写这些方括号，这里负责填。
_BLOCKS = {
    "【STYLE】": _style_text,
    "【WORLD】": _world_text,
    "【QUALITY】": _quality_text,
    "【STRENGTH】": _strength_text,
    "【SCALE】": _scale_text,
    "【EXTRA】": _extra_text,
}

# 【旧写法兜底】2026-09-01 之前这几段不是占位符，是写死的中文，代码按整段字面
# 匹配换掉。那种写法太脆——用户在设置页把那几段改一个空格，整条出图链就抛错。
# 现在文件里改成了占位符；这张表只为老文件（备份、_bak、外部拷贝）保底。
_LEGACY = {
    "【WORLD】": "默认：\n14-16世纪欧洲奇幻时代，\n魔法与骑士并存，\n真实电影制作级设计。",
    "【STYLE】": '创造"日本动漫角色设计 + 西方奇幻电影写实"的混合视觉风格。',
    "【QUALITY】": ("超写实电影概念艺术，\n真人摄影级光影，\n真实皮肤纹理，\n"
                   "真实布料纤维，\n真实金属反射，\n电影级色彩，\n8K细节，\n"
                   "Unreal Engine 5级别角色渲染。"),
}


def _fill(ins, settings, kind="character"):
    """把指令词里的占位符换成这个项目的实际设定。

    先认占位符；文件里没有占位符（老文件）才退回旧的整段字面匹配。
    """
    b = _project_block(settings)
    out = ins
    done = set()
    for ph, fn in _BLOCKS.items():
        if ph == "【STYLE】":
            val = _style_text(b, kind)
        elif ph == "【QUALITY】":
            val = _quality_text(b, kind)
        else:
            val = fn(b)
        if ph in out:
            out = out.replace(ph, val)
            done.add(ph)
            continue
        old = _LEGACY.get(ph)
        if old and old in out:
            out = out.replace(old, val)
            done.add(ph)
        elif val and ph in ("【SCALE】", "【EXTRA】"):
            # 这两段老文件里根本没有位置，照旧追加到末尾
            out += "\n\n" + val
            done.add(ph)
    # 【必须有的两块】世界观和画风没进去，出来的图就是错世界、错画风，
    # 而且从提示词上看不出来——宁可报错也不能静默出图。
    # （2026-09-01 实测：把【WORLD】整行删掉，原来一声不吭就把世界类型丢了。）
    missing = [p for p in ("【WORLD】", "【STYLE】") if p not in done]
    if missing:
        raise RuntimeError(
            "这份出图指令词里没有 %s，项目的世界类型/画风就进不去。"
            "把这个占位符加回指令词正文里。" % "和".join(missing))
    # 占位符留下的空行收拾干净
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    # 落地自检：填完不该再剩写死的默认，也不该剩没换掉的占位符。
    # 剩了就是文件被改坏了，早点报出来，别出一张欧洲奇幻的仙侠图。
    if "14-16世纪欧洲奇幻" in out or '日本动漫角色设计 + 西方奇幻电影写实' in out:
        raise RuntimeError("指令词里的世界观/画风默认没被项目设定替换掉——"
                           "这份文件既没有【WORLD】/【STYLE】占位符，"
                           "写死的那几段也对不上了")
    left = [p for p in _BLOCKS if p in out]
    if left:
        raise RuntimeError("这些占位符没被替换：" + "、".join(left))
    return out




import re as _re


def style_tail(settings):
    """画风的成像质感段（render_notes 里那段），**由代码原样追加到提示词末尾**。

    【为什么不让 Qwen 自己写】2026-08-26 实测：同一个项目 5 个人物，Qwen 把画风
    复述出 5 种详略——苏予安那条漏了 HDR 和数码锐化，老张那条自己加了"明星相"，
    学生会少女几乎全抄。同一画风出来 5 种风格，整组图就散了。
    画风是**事实**（固定的成像参数），不是创作，不该每次重新发挥。
    """
    b = _project_block(settings)
    try:
        import json as _json
        rn = _json.loads((INS_DIR.parent / "render_notes.json").read_text(encoding="utf-8"))
        return str((rn.get(b["style"]) or {}).get("char") or "").strip()
    except Exception:
        return ""


def prepare_character_facts(one_line):
    """Qwen 整理临时出图资料；只返回结构化事实，既不改人物卡也不拼最终稿。"""
    import json
    instruction = (INS_DIR / "人设图_资料整理_指令词.txt").read_text(encoding="utf-8")
    raw = _call(instruction, one_line, max_tokens=2000, temperature=0.1).strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        facts = json.loads(raw)
        assert isinstance(facts, dict)
        assert isinstance(facts.get("appearance"), dict)
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in facts["appearance"].items())
        assert isinstance(facts.get("expression"), str)
        assert isinstance(facts.get("clothing"), list)
        categories = {"上装", "下装", "鞋靴", "腰带", "佩戴饰物", "护具"}
        for item in facts["clothing"]:
            assert isinstance(item, dict) and item.get("类别") in categories
            assert isinstance(item.get("名称"), str) and item["名称"].strip()
            assert all(isinstance(v, str) for v in item.values())
    except (ValueError, AssertionError, TypeError) as exc:
        raise ValueError("本地千问的人设资料整理格式不完整，请重试；尚未生成图片") from exc
    return facts


def write_character(one_line, settings, extra_rules=""):
    """人物设定图：一次 Qwen 调用，返回 (出图提示词, 通用人物外观)。

    指令词要求 Qwen 输出两段，各带固定标记：
        【出图提示词】…（三视图加特写那张图，拿去出人设图）
        【人物外观】…（这个人固定的样子，存进卡，场景图引用）
    这样一次调用两用，不额外多花一次。切不出第二段时，外观退回用出图提示词兜底。

    extra_rules：这个人物独有、指令词拿不到的硬信息（比如断臂方向）。
    """
    # 人设图是事实整理任务，不需要高随机性。较低温度能减少漏字段和擅自改写。
    raw = write("character", one_line, settings, extra_rules=extra_rules, temperature=0.25)
    m1 = _re.search(r"【出图提示词】\s*(.+?)(?=【人物外观】|$)", raw, _re.S)
    m2 = _re.search(r"【人物外观】\s*(.+)", raw, _re.S)
    sheet = (m1.group(1).strip() if m1 else raw).strip()
    look = (m2.group(1).strip() if m2 else "").strip()
    # 【已撤回 2026-08-27】代码统一追加画风段的做法撤了（用户定：回到原样）。
    # style_tail() 保留备查；要恢复就把下面三行取消注释。
    # tail = style_tail(settings)
    # if tail and tail[:12] not in sheet:
    #     sheet = sheet.rstrip().rstrip("。") + "。" + tail
    return sheet, look


def character_look(card):
    """把一张人物卡压成一句外观，用来钉进场景一句话里。

    【为什么必须钉进去】场景图的一句话如果只写人名（"在场：亚瑟、艾莉丝"），
    Qwen 不知道亚瑟穿铠甲、艾莉丝穿法师袍，只能自己编——实测出来
    骑士画成了平民、法师画成了村姑。名字不带长相，等于没说。
    Krea2 又不吃参考图，人物长相只能靠这句话带进去。
    """
    c = card or {}
    # 【优先用卡上存的通用外观】人设图那一次 Qwen 调用产出的【人物外观】存进了
    # look_full 字段——那是最全的、发色瞳色肤色都钉死的描述。有它就直接用，
    # 场景图里人物才不会飘（实测亚瑟没存外观时头发被画成灰白）。
    full = str(c.get("look_full") or "").strip()
    if full:
        return "%s（%s）" % (str(c.get("name") or ""), full.rstrip("。"))
    # 兜底：还没生成过人设图、卡上没有 look_full 时，用卡上的零散字段拼。
    role = str(c.get("role") or "").split("/")[0].strip()
    cloth = str(c.get("clothing") or c.get("outfit_preset") or "").strip()
    if "：" in cloth:
        cloth = cloth.split("：", 1)[1]
    cloth = "、".join([x.strip() for x in cloth.replace("；", "，").split("，")
                       if x.strip()][:3])
    look = str(c.get("appearance_details") or "").strip().rstrip("。")
    bits = [x for x in (role, look, ("身穿" + cloth) if cloth else "") if x]
    return "%s（%s）" % (str(c.get("name") or ""), "，".join(bits))


def scene_plate_one_line(card):
    """场景卡 → 给「场景参考板」指令词的一句话。**不带任何人物。**

    旧的 scene_one_line 拼的是「地点＋每个在场人的外观＋正在发生什么」，
    画出来是一张剧情插图。当视频参考图用时，图里的脸会和人设图的脸打架，
    H3 同时收到两套长相（2026-08-27）。参考板只描述空间本身。
    """
    c = card or {}
    it = str(c.get("interior") or "").strip()
    where = "%s，%s，%s" % (
        str(c.get("name") or ""),
        "室内" if it in ("内", "室内") else "室外",
        {"日": "白天", "夜": "夜晚", "黄昏": "黄昏", "清晨": "清晨"}.get(
            str(c.get("time_of_day") or c.get("time") or "").strip(), "白天"))
    bits = [where]
    for label, key in (("空间", "space"), ("布局", "layout"), ("材质", "materials"),
                       ("主光", "light"), ("标志物", "landmarks")):
        v = str(c.get(key) or "").strip()
        if v:
            bits.append("【%s】%s" % (label, v))
    return "。".join(bits[:1]) + "\n" + "\n".join(bits[1:])


def write(kind, one_line, settings, extra_rules="", temperature=0.7):
    """一句话 + 项目设定 → Qwen 写好的出图提示词。

    kind      : character | scene
    one_line  : 这次要画什么，一句话。人物是人物长什么样，场景是**这一场正在发生什么**
    settings  : 项目设定（世界类型 / 画风 / 视觉强度 / 整部美术基调）
    """
    if kind == "character":
        from .age_policy import assert_allowed
        assert_allowed(settings=settings, text=str(one_line or "") + "\n" + str(extra_rules or ""), media="image_prompt")
    f = INS_DIR / INSTRUCTIONS[kind]
    ins = _fill(f.read_text(encoding="utf-8"), settings, kind=kind)
    if extra_rules:
        ins += "\n\n## 这一次的额外要求\n" + str(extra_rules).strip()
    return _call(ins, str(one_line or "").strip(), temperature=temperature)


def write_many(jobs, settings, on_step=None):
    """批量写。**Qwen 一次上场把所有提示词写完，再交棒 Krea2。**

    jobs: [{"kind": "character", "key": "亚瑟", "one_line": "..."}, ...]
    返回 {key: prompt}；单条失败不影响其它条（任务永不中断）。
    """
    out, n = {}, len(jobs or [])
    for i, j in enumerate(jobs or [], 1):
        key = j.get("key") or str(i)
        if on_step:
            on_step("写提示词 %d/%d：%s" % (i, n, key))
        t0 = time.time()
        try:
            out[key] = {"prompt": write(j["kind"], j["one_line"], settings,
                                        j.get("extra_rules") or ""),
                        "seconds": round(time.time() - t0)}
        except Exception as e:
            out[key] = {"prompt": "", "error": str(e)[:200],
                        "seconds": round(time.time() - t0)}
    return out
