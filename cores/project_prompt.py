# -*- coding: utf-8 -*-
"""把项目设定确定性地编译到故事、人物、场景和视频文字中。

这里只做字符串整理，不调用任何模型。所有生成链共用这一份定义，避免同一个
“西方魔幻”在故事里有效、到人物图和场景图却丢失。
"""
import hashlib
import json
import re
from pathlib import Path

from . import style_presets


WORLD_KITS = {
    "东方仙侠": {
        "character": "东方交领内衫、束腰长袍、窄袖或广袖外层，丝麻与皮革分层，玉石或金属扣件",
        "scene": "抬梁木构、灰瓦飞檐、石阶山门、楼阁洞府与院落轴线，木、青石、陶瓦、铜件和玉石材质",
        "shot": "东方山水式纵深与留白，建筑轴线、山势和云雾共同组织空间",
        "footwear": "脚上是黑色布面云头履，鞋底是白色千层底",
    },
    "东方写实": {
        "character": "按东方古代现实衣制使用交领、圆领、短打、袍服与腰带，棉麻丝绸和皮革结构清楚",
        "scene": "木构院落、灰瓦屋面、夯土或砖石墙、石板地面、木门窗与真实生活器物",
        "shot": "现实历史空间尺度，礼制轴线与生活动线清楚，不使用悬浮或发光的超自然结构",
        "footwear": "脚上是黑色布面圆口布鞋，鞋底是白色千层底",
    },
    "西方魔幻": {
        "character": "中世纪欧洲层叠穿着：亚麻内衫、羊毛或皮革外层、束腰带，可按身份加入斗篷、锁甲或板甲扣件",
        "scene": "粗砺切石墙、尖拱门窗、木梁屋顶、铁艺构件、石板路与烛火壁炉，城堡和村镇尺度真实",
        "shot": "中世纪奇幻电影空间，石砌建筑、火光与魔法光源形成明确冷暖层次",
        "footwear": "脚上是深棕色皮革短靴，鞋面有金属扣带",
    },
    "西方写实": {
        "character": "欧洲古代至近代现实衣制：亚麻内层、羊毛外套、马甲或长裙、皮革腰带与合时代鞋履",
        "scene": "砖石或切石建筑、拱券门窗、木梁楼板、灰泥墙面、石板街道与真实时代家具",
        "shot": "欧洲历史现实主义空间，建筑年代、室内陈设和自然光方向保持统一",
        "footwear": "脚上是深棕色皮革系带短靴",
    },
    "未来科幻": {
        "character": "模块化未来服装：智能织物贴身层、复合材料外层、功能腰封、磁吸扣件与轻量护具",
        "scene": "合金骨架、复合材料墙板、透明材料隔断、嵌入式灯带、模块舱门与可维护的设备接口",
        "shot": "未来工业空间以结构灯带、屏幕反光和体积光组织纵深，科技部件尺度可信",
        "footwear": "脚上是深色复合材料战术短靴，鞋侧有卡扣",
    },
    "校园": {
        "character": "现实校园衣着：衬衫或针织内层、校服外套、长裤或过膝裙、书包与运动鞋，尺码合身",
        "scene": "教学楼混凝土与砖墙、白色粉刷墙面、玻璃窗、课桌椅、公告栏和塑胶运动场",
        "shot": "现实校园动线与自然采光，走廊、教室和操场尺寸符合真实学校",
        "footwear": "脚上是白色帆布鞋，配白色短袜",
    },
    "现代都市": {
        "character": "当代成衣体系：针织或棉质内搭、合体外套、长裤或裙装、现代五金与符合身份的鞋包",
        "scene": "钢筋混凝土结构、玻璃幕墙、涂料墙面、金属门窗、道路标线与现代生活设施",
        "shot": "当代城市摄影空间，街道、住宅和商业设施尺度真实，照明来源符合现实",
        "footwear": "脚上是与这身衣服相称的现代皮鞋或短靴",
    },
    "末日": {
        "character": "多层生存装备：耐磨内层、补丁外套、实用腰包、护膝护臂、回收金属扣件与防护靴",
        "scene": "开裂混凝土、锈蚀钢材、破损玻璃、临时木板加固、废弃管线与风化尘土",
        "shot": "废墟纵深与资源稀缺痕迹清楚，用遮蔽物、残骸和危险通道组织画面",
        "footwear": "脚上是磨损的军靴，鞋头开胶，鞋带换过",
    },
}

WORLD_KEYWORDS = {
    "东方仙侠": ("仙侠", "修仙", "宗门", "灵气", "飞剑", "洞府"),
    "东方写实": ("古代中国", "朝堂", "江湖", "王朝", "县衙"),
    "西方魔幻": ("西方魔幻", "魔法", "法师", "骑士", "精灵", "矮人", "龙", "地下城"),
    "西方写实": ("欧洲", "西方写实", "贵族", "火枪", "庄园"),
    "未来科幻": ("科幻", "未来", "星际", "机甲", "赛博", "机器人"),
    "校园": ("校园", "学校", "学生", "社团", "教室"),
    "现代都市": ("都市", "现代", "公司", "公寓", "职场"),
    "末日": ("末日", "废土", "丧尸", "灾难", "文明崩坏"),
}

CHARACTER_WORDS = ("人物", "角色", "主角", "全员", "服装", "衣", "裙", "袍", "甲", "靴", "鞋",
                   "发", "脸", "肤", "眼", "妆", "体型", "身材", "饰品", "盔")
SCENE_WORDS = ("场景", "建筑", "房", "室", "城", "村", "街", "门", "窗", "墙", "地面", "材质",
               "森林", "山", "海", "天空", "环境", "陈设", "家具", "灯光", "色调", "拱")
SHOT_WORDS = ("镜头", "构图", "运镜", "景别", "节奏", "分镜", "摄影", "画幅", "机位")
STORY_WORDS = ("故事", "剧情", "结局", "开场", "第一话", "冲突", "关系", "目标", "世界观")
PERSON_WORDS = ("人物", "人影", "身影", "行人", "路人", "人群", "少年", "少女", "男人", "女人",
                "老人", "孩子", "醉汉", "店员", "守卫", "士兵", "骑士", "修士", "师兄", "师妹",
                "敌人", "首领", "头目", "流氓", "背影", "依偎")

# 场景设定图是给后续分镜复用的“静态空间底图”，剧情里的声音、气味和瞬间动作
# 不能混进正向提示词。Krea2 当前工作流的负向分支不起作用，必须在这里清干净。
SCENE_NON_VISUAL_WORDS = ("气味", "味道", "酸腐", "谈笑", "打斗声", "争吵声", "喊声",
                          "脚步声", "惨叫", "轰鸣", "声响", "声音")
SCENE_EVENT_WORDS = ("飞舞", "四溅", "飞溅", "泼洒", "打翻", "挥舞", "奔跑", "追逐",
                     "搏斗", "打斗", "缠斗", "舞蹈", "正在")

def resolved_world(settings):
    """返回服装库/地点库用的旧键（东方仙侠/西方魔幻/未来科幻/现代都市…）。四界新值在这里映射。"""
    s = settings or {}
    chosen = str(s.get("world_type") or "").strip()
    if chosen and chosen not in ("自动", "自动判断", "自定义"):
        return style_presets.world_legacy_key(chosen)
    guessed = inferred_world(s)
    if guessed:
        return guessed
    return "现代都市" if chosen in ("自动", "自动判断", "") else "自定义"


def inferred_world(settings):
    """只按文字猜世界，不看用户已选的值。「自动判断」和「自定义」共用这套推断。"""
    s = settings or {}
    lp = s.get("look_panel") or {}
    source = " ".join(str(x or "") for x in (
        s.get("one_line"), s.get("extra_requirements"), s.get("custom_guidance"),
        lp.get("look"), lp.get("world"), s.get("plan")))
    for world, words in WORLD_KEYWORDS.items():
        if any(w in source for w in words):
            return world
    return ""


def kit_world(settings_or_world, layer="character"):
    """取「可见特征」该用哪套世界。

    【为什么「自定义」需要单独处理】WORLD_KITS 里没有“自定义”这个键，
    过去选它等于服装体系、建材、镜头和鞋全部为空——比选「自动判断」还弱。
    现在的规则是：用户自己在「整部作品长什么样」里写了这一层，就完全听他的，
    一个字都不补；他没写，才按文字猜一套最接近的兜底，避免提示词整段空白。
    """
    if not isinstance(settings_or_world, dict):
        return str(settings_or_world or "")
    world = resolved_world(settings_or_world)
    if world != "自定义":
        return world
    if str(routed_user_text(settings_or_world, layer) or "").strip():
        return ""          # 用户自己写了，不套内置模板
    # 猜不出来就什么都不补。硬套「现代都市」会让一个紫色盐晶世界穿上针织外套，
    # 比留空更糟——留空时还有画风和用户文字兜着。
    return inferred_world(settings_or_world)


def world_kit(settings_or_world, layer):
    world = kit_world(settings_or_world, layer)
    # 给扩散模型的内容只保留可见描述，不放“体系：”这类元标签。
    return str((WORLD_KITS.get(world) or {}).get(layer) or "").replace("：", "，")


FOOTWEAR_DEFAULT = "脚上是与这身衣服相称的平底鞋"


def footwear_for(settings_or_world):
    """默认鞋。唯一数据源是 WORLD_KITS['footwear']，不再另立一张表。

    【为什么必须传 settings 而不是原始 world_type】选「自动判断」时，
    衣服按推断出来的世界给，鞋却按字面的“自动判断”查表——查不到就落到通用
    平底鞋，于是出现中世纪亚麻衫配一双来路不明的平底鞋。
    """
    world = kit_world(settings_or_world, "character")
    return str((WORLD_KITS.get(world) or {}).get("footwear") or "") or FOOTWEAR_DEFAULT


def _split(text):
    return [x.strip() for x in re.split(r"[\n；;。]+", str(text or "")) if x.strip()]


def _matches(clause, layer):
    table = {"character": CHARACTER_WORDS, "scene": SCENE_WORDS,
             "shot": SHOT_WORDS, "story": STORY_WORDS}
    return any(w in clause for w in table.get(layer, ()))


def routed_user_text(settings, layer):
    """按职责分流自由文本。

    extra_requirements 是不可更改的故事事实，只进 story；custom_guidance 是视觉与镜头
    要求，只进 character/scene/shot；look 是整部作品美术基调。
    """
    s = settings or {}
    lp = s.get("look_panel") or {}
    look = "；".join(str(lp.get(k) or "").strip() for k in
                    ("look", "designDirection", "world", "characters") if str(lp.get(k) or "").strip())
    out = []
    for source, allowed_layers, unknown_layers in (
        (look, ("character", "scene", "shot"), ("character", "scene", "shot")),
        (s.get("extra_requirements"), ("story",), ("story",)),
        (s.get("custom_guidance"), ("character", "scene", "shot"), ("shot",)),
    ):
        for clause in _split(source):
            matched = {k for k in ("story", "character", "scene", "shot") if _matches(clause, k)}
            if layer not in allowed_layers:
                continue
            # “场景设定图只画纯环境、不出现人群”含有“人物”二字，旧逻辑因此把它
            # 错送进每一张人物卡。先按句子的真正主体分流，避免人物/场景相互污染。
            if layer == "character" and any(w in clause for w in ("场景图", "场景设定图", "纯环境", "建筑环境")):
                if not any(w in clause for w in ("服装", "发型", "身材", "人物穿")):
                    continue
            if layer == "scene" and any(w in clause for w in ("人物服装", "角色服装", "发型", "身材")):
                if not any(w in clause for w in ("场景", "建筑", "环境", "房子", "城堡")):
                    continue
            if layer in matched or (not matched and layer in unknown_layers):
                if clause not in out:
                    out.append(clause)
    return "；".join(out)


def resolved_intensity(settings):
    """视觉强度由画风推出（2026-09-04 用户定）：真实组（电影质感/写实生活）→ 自然；夸张组（平面动漫/3D游戏）→ 强设计。
    用户手填过明确值（非自动）仍尊重。"""
    s = settings or {}
    value = style_presets.normalize_visual_strength(s.get("visual_strength") or "自动")
    if value not in ("自动", "自动判断", ""):
        return value
    return "自然" if style_presets.style_is_real(s.get("style") or style_presets.DEFAULT_STYLE) else "强设计"


def style_text(settings_or_style):
    if isinstance(settings_or_style, dict):
        name = str(settings_or_style.get("style") or style_presets.DEFAULT_STYLE)
    else:
        name = str(settings_or_style or style_presets.DEFAULT_STYLE)
    return style_presets.style_description(name) or name


def default_look(settings):
    """模型漏写美术基调时的确定性兜底；只组装已有世界规则，不调用模型。"""
    s = settings or {}
    world = resolved_world(s)
    parts = [world + "世界的统一美术基调"]
    for layer in ("character", "scene"):
        kit = world_kit(s, layer)
        if kit:
            parts.append(kit)
    routed = routed_user_text(s, "scene")
    if routed:
        parts.append(routed)
    parts.append("人物服装、建筑材料、固定识别物与昼夜光线在整部作品中保持一致")
    return "；".join(dict.fromkeys(x.strip("；。 ") for x in parts if x)) + "。"


def project_context(settings, layer, include_style=True):
    """返回某层实际会使用的项目约束，供各生成入口直接拼接。"""
    s = settings or {}
    world = resolved_world(s)
    lines = ["世界类型：%s" % world]
    kit_layer = "shot" if layer == "video" else layer
    kit = world_kit(world, kit_layer)
    if kit:
        lines.append("世界可见特征：" + kit)
    if layer == "character":
        idef = style_presets.intensity_def(resolved_intensity(s))
        if idef:
            lines.append("视觉设计强度：" + idef)
        tend = style_presets.tendency_effect(s.get("content_tendencies") or [], "figure")
        if tend:
            lines.append("人物尺度：" + tend)
    elif layer in ("shot", "video"):
        tend = style_presets.tendency_effect(s.get("content_tendencies") or [], "shot")
        if tend:
            lines.append("镜头尺度：" + tend)
        if str(s.get("custom_content_scale") or "").strip():
            lines.append("用户自定义尺度（逐字遵守）：" + str(s["custom_content_scale"]).strip())
    elif layer == "story":
        tend = style_presets.tendency_effect(s.get("content_tendencies") or [], "story")
        if tend:
            lines.append("故事尺度：" + tend)
        if str(s.get("custom_content_scale") or "").strip():
            lines.append("用户自定义尺度（逐字遵守）：" + str(s["custom_content_scale"]).strip())
    routed = routed_user_text(s, "shot" if layer == "video" else layer)
    if routed:
        lines.append("用户已定要求：" + routed)
    if include_style and layer in ("character", "scene", "shot", "video", "comic"):
        lines.append("画风：" + style_text(s))
    return lines


def story_settings_block(settings):
    s = settings or {}
    lines = project_context(s, "story", include_style=False)
    for layer, label in (("character", "人物世界规则"), ("scene", "场景世界规则")):
        kit = world_kit(s, layer)
        if kit:
            lines.append(label + "：" + kit)
    lp = routed_user_text(s, "character")
    if lp:
        lines.append("人物美术要求：" + lp)
    env = routed_user_text(s, "scene")
    if env:
        lines.append("场景美术要求：" + env)
    if s.get("one_line"):
        lines.insert(0, "原始一句话（事实）：" + str(s["one_line"]))
    return "\n".join(lines)


def story_generation_block(settings):
    """只给故事核/骨架/正文的事实约束，不带视觉与镜头自由文本。"""
    s = settings or {}
    world = resolved_world(s)
    lines = []
    if str(s.get("one_line") or "").strip():
        lines.append("原始一句话（最高优先级硬事实，人物数量、职业、年龄、体型、服装和关系都不得丢失或换成近似职业）："
                     + str(s["one_line"]).strip())
    lines.append("世界类型（已定，不得改成其他题材）：" + world)
    wd = style_presets.world_def(world)
    if wd:
        lines.append("世界规则：" + wd)
    elif world == "自定义":
        # 内置题材表里没有“自定义”。过去这里直接留空，模型拿不到任何世界约束，
        # 反而比选「自动判断」更容易跑偏。改为明确告诉它规则从哪里来。
        lines.append("世界规则：不套用任何内置题材模板。这个世界的时代、技术上限、"
                     "社会结构、服装体系与超自然规则，全部以原始一句话和不可更改的故事要求为准；"
                     "上述文字没写到的部分自行补全，但必须与已写部分同属一个文明，前后一致，不得中途换体系。")
    guard = {
        "东方仙侠": "以古代东方社会与修行体系为基线；除非原始一句话明确指定，不得出现现代电器、枪械、汽车、广播或西式骑士体系。",
        "东方写实": "以古代或近代东方现实社会为基线；不得凭空加入灵气、法术、妖兽、飞剑或现代电子设备。",
        "西方魔幻": "以中世纪架空欧洲与炼金术为技术上限；除非原始一句话明确指定，不得出现电力、电子屏、扩音广播、信号灯、现代显微镜、汽车或枪械。检验药剂只能使用坩埚、蒸馏器、玻璃瓶、银针、魔法或炼金术。",
        "西方写实": "以无超自然力量的西方现实社会为基线；不得凭空加入魔法、异族、怪物、灵气或仙侠体系，时代技术必须服从原始一句话。",
        "未来科幻": "科技、建筑、通信和交通必须属于同一未来技术水平；不得无解释地混入仙侠灵气或纯中世纪社会规则。",
        "校园": "以当代学校生活为基线；除非原始一句话明确指定，不得凭空加入魔法、修仙、怪物、战争装备或未来黑科技。",
        "现代都市": "以当代城市现实为基线；除非原始一句话明确指定，不得凭空加入魔法、修仙、异族或未来黑科技。",
        # 【为什么这条要绑世界而不是绑主角】原文把主角也捆进"必须短缺"，直接压制"末日开挂爽文"——
        # 那类故事的爽点恰恰是主角富足。用户定的规矩：变的从来不是世界，是主角有没有挂。
        "末日": "以崩坏后的现代或近未来社会为基线。环境、物资、交通、通信与秩序始终处在"
                "损坏、短缺和崩坏的状态，其他人普遍艰难求生，这一点不因主角而改变。"
                "主角是否例外由故事本身决定：主角没有特殊能力时，他同样要为物资奔波；"
                "主角拥有特殊能力或金手指时，他的富足必须以周围人的匮乏作为背景反衬，"
                "不得因此把整个世界写成资源正常、秩序完好的社会。",
    }.get(world, "")
    if guard:
        lines.append("时代与技术边界：" + guard)
    tend = style_presets.tendency_effect(s.get("content_tendencies") or [], "story")
    if tend:
        lines.append("故事尺度：" + tend)
    if str(s.get("custom_content_scale") or "").strip():
        lines.append("用户自定义尺度（逐字遵守）：" + str(s["custom_content_scale"]).strip())
    if str(s.get("extra_requirements") or "").strip():
        lines.append("不可更改的故事要求：" + str(s["extra_requirements"]))
    if str(s.get("plan") or "").strip():
        lines.append("已有篇章框架：" + str(s["plan"]))
    return "\n".join(lines)


def user_written_text(settings):
    """用户亲手写过或亲手选过的所有内容，拼成一份豁免池。

    【为什么要有这个】禁词检查的本意是拦模型跑偏，不是拦用户。原来的豁免只看
    一句话：用户在「不可更改的故事要求」里写了"主角带一把手枪"，世界又是西方魔幻，
    模型照做，检查却判违规重写——重写时那条要求还在要求它写手枪，两头不是人。
    规矩是：**用户写的字是事实，系统永远不许否定。**
    """
    s = settings or {}
    lp = s.get("look_panel") or {}
    parts = [s.get("one_line"), s.get("extra_requirements"), s.get("custom_content_scale"),
             s.get("custom_guidance"), s.get("plan"), s.get("episode1_plan"),
             lp.get("look"), lp.get("hero_style")]
    parts += [str(x) for x in (s.get("content_tendencies") or [])]
    for c in s.get("_characters") or []:
        # 人物卡上用户填的自由文本，以及他明确选中的物种——选了"精灵"就不该再判
        # "精灵"违规。让你选、又判你违规，是系统自相矛盾。
        parts += [c.get("name"), c.get("role"), c.get("identity_anchor"),
                  c.get("appearance_details"), c.get("clothing"), c.get("other"),
                  c.get("note"), c.get("char_type"), c.get("outfit_preset")]
    return " ".join(str(x or "") for x in parts)


def settings_conflicts(settings):
    """只提示、不拦截：用户手选的世界，和他一句话里写的东西对不对得上。

    用户定的规矩：他写的字是事实，系统永远不许否定。所以这里**不改任何东西、
    不阻止保存**，只把"你的一句话提到了星际战舰，但世界类型选的是校园"
    这种情况说一声，改不改由他决定。
    """
    s = settings or {}
    chosen = str(s.get("world_type") or "").strip()
    if chosen in ("", "自动", "自动判断", "自定义"):
        return []
    text = " ".join(str(x or "") for x in
                    (s.get("one_line"), s.get("extra_requirements")))
    if not text.strip():
        return []
    out = []
    for world, words in WORLD_KEYWORDS.items():
        if world == chosen:
            continue
        hit = [w for w in words if w in text]
        if hit:
            out.append("你写的「%s」像是%s，但世界类型选的是%s"
                       % ("、".join(hit[:3]), world, chosen))
    return out[:3]


def story_world_violations(text, settings):
    """返回正文/骨架里与已定世界时代冲突、且用户自己没写过的词。"""
    s = settings or {}
    world = resolved_world(s)
    premise = user_written_text(s)
    forbidden = {
        "东方仙侠": ("汽车", "手机", "电子屏", "广播", "扩音器", "聚光灯", "枪械", "圣骑士"),
        "东方写实": ("灵气", "飞剑", "法阵", "修士", "妖兽", "仙尊", "金丹", "魔法"),
        "西方魔幻": ("电力", "电子屏", "扩音喇叭", "广播", "聚光灯", "信号灯", "信标灯", "显微镜",
                       "汽车", "手机", "手枪", "步枪"),
        "西方写实": ("魔法", "灵气", "飞剑", "法阵", "精灵", "矮人", "龙血", "妖兽"),
        # guard 里写了"不得无解释地混入仙侠灵气或纯中世纪社会规则"，
        # 禁词表却一直是空的——规矩只在嘴上，没有任何机器检查。
        "未来科幻": ("灵气", "飞剑", "法阵", "修士", "金丹", "仙尊", "妖兽",
                        "圣骑士", "板甲", "锁子甲"),
        "校园": ("灵气", "飞剑", "法阵", "修士", "圣骑士", "星际战舰"),
        "现代都市": ("灵气", "飞剑", "法阵", "修士", "圣骑士", "星际战舰"),
    }.get(world, ())
    body = str(text or "")
    return [word for word in forbidden if word in body and word not in premise]


def story_requirement_violations(text, settings, tail_only=False):
    """把可机械判断的“不可更改故事要求”变成硬检查，其他自然语言要求仍交给模型。"""
    body = str(text or "")
    probe = body[-350:] if tail_only else body
    violations = []
    for clause in _split((settings or {}).get("extra_requirements")):
        ending = re.search(r"结尾必须(?:停在|落在|结束于)(.+)", clause)
        if ending:
            target = ending.group(1).strip("，,。；;：: ")
            for verb in ("拿到", "握住", "取得", "得到", "发现", "看见"):
                if verb in target:
                    target = target.split(verb, 1)[1].strip()
                    break
            if target and target not in probe:
                violations.append("结尾缺少规定内容「%s」" % target)
            continue
        required = re.search(r"必须(?:包含|出现|保留)(.+)", clause)
        if required:
            target = required.group(1).strip("，,。；;：: ")
            if target and target not in body:
                violations.append("缺少规定内容「%s」" % target)
            continue
        forbidden = re.search(r"(?:不能揭晓|不得揭晓|禁止揭晓|不能出现|不得出现|禁止出现)(.+)", clause)
        if forbidden:
            target = forbidden.group(1).strip("，,。；;：: ")
            if target and target in body:
                violations.append("出现了禁止内容「%s」" % target)
    return violations


def video_visual_block(settings):
    return "【项目视觉约束】\n" + "\n".join(project_context(settings, "video", include_style=True))


def strip_scene_people(text, names=None):
    """清除场景卡里的人物、非视觉信息和瞬间事件，只保留空间与静物。"""
    names = [str(x).strip() for x in (names or []) if str(x).strip()]
    out = []
    for clause in re.split(r"[，、；;。]+", str(text or "")):
        c = clause.strip()
        if not c:
            continue
        if any(n in c for n in names) or any(w in c for w in PERSON_WORDS):
            continue
        if any(w in c for w in SCENE_NON_VISUAL_WORDS):
            continue
        if any(w in c for w in SCENE_EVENT_WORDS):
            continue
        # “积满酒渍”会被图像模型夸张成整片液体；固定为可复用的轻度旧痕迹。
        c = c.replace("积满酒渍的", "带少量陈年酒渍的").replace("地面积满酒渍", "木地板带少量陈年酒渍")
        out.append(c)
    return "，".join(out)


_FACE_LIBRARY_PATH = Path(__file__).resolve().parent.parent / "presets" / "face_library.json"

# 用户自己写了这一项，就不再往里塞预设——跟服装那套规矩一致。
_FACE_USER_WORDS = {
    "eye": ("眼型", "眼睛", "杏眼", "丹凤", "桃花眼", "圆眼", "细长眼", "下垂眼",
            "三角眼", "瞳", "眼裂", "眼尾"),
    "eyelid": ("眼皮", "双眼皮", "内双", "单眼皮", "眼窝"),
    "brow": ("眉",),
    "nose": ("鼻",),
    "mouth": ("唇", "嘴"),
    "mark": ("痣", "疤", "雀斑", "酒窝", "胎记", "刺青", "纹身", "虎牙", "异色瞳", "胡茬"),
}


def load_face_library():
    try:
        data = json.loads(_FACE_LIBRARY_PATH.read_text(encoding="utf-8"))
        if data.get("sexes"):
            return data
    except Exception:
        pass
    return {}


def _face_pool(lib, sex, dim, role_text, beauty_tier=""):
    """某个维度的候选池。

    两级筛：
      1. 颜值档决定"能不能好看"——惊艳只从俊美池抽，路人脸优先从路人池抽。
         这一步必须做在**选项层**：只在提示词里加一句"电影主演级"是没用的，
         主角照样会抽到肿眼泡、塌鼻梁、断鼻。好看与否是由具体五官决定的。
      2. 身份决定"往哪个方向长"——师父偏锐利、少女偏圆润、反派偏阴沉。
    两级都筛空了就退回全池，绝不返回空。
    """
    all_items = ((lib.get("sexes") or {}).get(sex) or {}).get(dim) or []
    pool = all_items
    tier = str(beauty_tier or "").strip()
    gate = None
    plain = ((lib.get("plain") or {}).get(sex) or {}).get(dim) or []
    if tier == "惊艳":
        gate = ((lib.get("attractive") or {}).get(sex) or {}).get(dim)
    elif tier == "路人脸":
        gate = plain
    else:
        # 耐看 = 全池减去路人池。不设这一刀的话，"耐看"照样会抽到
        # 断鼻、塌鼻梁、肿眼泡，跟路人脸就没区别了。
        gate = [x.get("name") for x in pool if x.get("name") not in plain]
    if gate:
        narrowed = [x for x in pool if x.get("name") in gate]
        pool = narrowed or pool
    names = [x.get("name") for x in pool]
    prefer = []
    for grp in (lib.get("tendency") or {}).values():
        if any(w and w in role_text for w in grp.get("words") or []):
            prefer += [n for n in (grp.get(dim) or []) if n in names]
    narrowed = [x for x in pool if x.get("name") in prefer] if prefer else pool
    return narrowed or pool or all_items


def face_features(character, settings=None, taken=None, seed=""):
    """给一个人物挑一套五官。**确定性**：同一个人永远挑到同一套。

    【为什么不能只靠"脸型"这一个选项】实测三个人物的出图提示词各 1000 字左右，
    其中 631 字一字不差，真正描述"这张脸"的只有脸型定义那 26~33 字，占 5%。
    而脸型只有男女各 8 个选项，写的还是"小脸／宽脸／窄长脸"这种骨相分类，
    不是五官识别点——10 个角色必然撞，撞了就是同一段话，画出来当然像亲戚。

    这里把脸拆成六个维度各自查表，组合数从 8 变成十万级：
      身份倾向筛池 → 姓名哈希定序 → 项目内去重 → 用户写过的维度不覆盖
    全程不调模型，同一个人重生成设定图不会变脸。
    """
    lib = load_face_library()
    if not lib:
        return {}
    c = character or {}
    sex = "男" if str(c.get("sex") or "").strip() == "男" else "女"
    role_text = " ".join(str(c.get(k) or "") for k in
                         ("name", "role", "identity_anchor", "char_type"))
    own = " ".join(str(c.get(k) or "") for k in
                   ("appearance_details", "look", "other", "note"))
    key_base = "%s|%s|%s" % (seed, c.get("character_id") or "", c.get("name") or "")
    taken = set(taken or ())
    out = {}
    for dim in lib.get("dimensions") or []:
        if any(w in own for w in _FACE_USER_WORDS.get(dim, ())):
            continue                      # 用户自己写了这一项
        pool = _face_pool(lib, sex, dim, role_text, c.get("beauty_tier"))
        if not pool:
            continue
        if dim == "mark":
            # 识别点：主角和第一话就在场的必给，其余一半概率给。
            is_main = ("主角" in role_text or int(c.get("first_episode") or 9) <= 1)
            if not is_main and int(hashlib.sha1(
                    (key_base + "|markgate").encode("utf-8")).hexdigest()[:4], 16) % 2:
                continue
        idx = int(hashlib.sha1((key_base + "|" + dim).encode("utf-8")).hexdigest()[:8], 16)
        # 项目内去重：先在本档池子里找没被人用过的；整池都被占了（路人池只有
        # 两三个选项，八个龙套必然不够分）就退到全池再找，实在没有才允许重复。
        # 宁可让龙套借一个中性五官，也不要两张一模一样的脸。
        # 去重耗尽时的备用池：非路人档**绝不**退进路人池，
        # 否则"耐看"的角色会拿到断鼻、塌鼻梁、肿眼泡，跟龙套没区别。
        full = ((lib.get("sexes") or {}).get(sex) or {}).get(dim) or []
        if str(c.get("beauty_tier") or "").strip() != "路人脸":
            _plain = ((lib.get("plain") or {}).get(sex) or {}).get(dim) or []
            full = [x for x in full if x.get("name") not in _plain] or full
        picked = None
        for candidates in (pool, full):
            if not candidates:
                continue
            for step in range(len(candidates)):
                cand = candidates[(idx + step) % len(candidates)]
                if ("%s:%s" % (dim, cand.get("name"))) not in taken:
                    picked = cand
                    break
            if picked:
                break
        if picked is None and pool:
            picked = pool[idx % len(pool)]
        if picked:
            out[dim] = picked.get("name")
    return out


def face_feature_text(features, sex="女"):
    """把挑好的五官翻成可画的一段话。"""
    lib = load_face_library()
    table = (lib.get("sexes") or {}).get("男" if str(sex) == "男" else "女") or {}
    parts = []
    for dim in lib.get("dimensions") or []:
        name = (features or {}).get(dim)
        if not name:
            continue
        item = next((x for x in table.get(dim) or [] if x.get("name") == name), None)
        if item and item.get("prompt"):
            parts.append(item["prompt"])
    return "，".join(parts)


def face_signature(features):
    """项目内去重用的占位集合。"""
    return {"%s:%s" % (k, v) for k, v in (features or {}).items() if v}


_PLACE_LIBRARY_PATH = Path(__file__).resolve().parent.parent / "presets" / "place_library.json"


def load_place_library():
    """地点模板库（按世界分组）。格式与服装方向库一致：改数据就是改预设。"""
    try:
        data = json.loads(_PLACE_LIBRARY_PATH.read_text(encoding="utf-8"))
        worlds = data.get("worlds") or {}
        if isinstance(worlds, dict) and worlds:
            return worlds
    except Exception:
        pass
    return {}


def place_options(settings_or_world):
    """某个世界有哪些地点模板（给界面下拉用）。"""
    world = kit_world(settings_or_world, "scene") if isinstance(settings_or_world, dict)         else str(settings_or_world or "")
    return ["自动"] + [x.get("name") for x in (load_place_library().get(world) or [])] + ["自定义"]


def place_defs(settings_or_world):
    world = kit_world(settings_or_world, "scene") if isinstance(settings_or_world, dict)         else str(settings_or_world or "")
    return {x.get("name"): x.get("prompt") for x in (load_place_library().get(world) or [])}



def place_entry(scene, settings=None):
    """场景 → 命中的地点模板整条（不只是 prompt）。

    原来 place_is_indoor 和 scene_type_kit 各写了一遍同样的关键词匹配，
    一个只取 indoor、一个只取 prompt，新增的 scale 谁都读不到。
    收敛成一处，三个字段一起返回。
    """
    sc = scene or {}
    world = kit_world(settings or {}, "scene")
    entries = load_place_library().get(world) or []
    if not entries:
        return None
    chosen = str(sc.get("place_preset") or "").strip()
    if chosen and chosen not in ("自动", "自定义", "None"):
        exact = next((x for x in entries if str(x.get("name") or "") == chosen), None)
        if exact:
            return exact
    source = " ".join(str(sc.get(k) or "") for k in
                      ("name", "contract_text", "space", "regions",
                       "landmarks", "fixed_landmarks"))
    # 内外要对得上，否则「迷宫入口（外）」会命中室内的「地牢遗迹」，
    # 把"甬道宽6米、主厅层高25米"这种室内尺度安到一片林间空地上。
    want = str(sc.get("interior") or "").strip()
    want_indoor = True if want in ("内", "室内") else (False if want in ("外", "室外") else None)
    best, best_n = None, 0
    for x in entries:
        n = sum(1 for w in (x.get("keywords") or []) if w and w in source)
        if not n:
            continue
        if want_indoor is not None and "indoor" in x and bool(x["indoor"]) != want_indoor:
            n -= 0.5          # 内外不符要罚，但仍可在没有更好选择时兜底
        if n > best_n:
            best, best_n = x, n
    return best


# 等级 → 兜底米数。地点库没命中时用这个，保证"大"永远不会是个小屋子。
# 分内外两套：室外写"层高"是错的（露天没有顶棚），要写纵深和周围物体的高度。
SCALE_FALLBACK_IN = {
    "大": "宽40米×深60米/层高25米",
    "中": "宽16米×深20米/层高6米",
    "小": "宽8米×深10米/层高3.5米",
}
SCALE_FALLBACK_OUT = {
    "大": "视野纵深数百米，周围建筑或地形高逾30米",
    "中": "场地纵深60米，周围建筑或树木高约12米",
    "小": "场地纵深15米，周围物体高约4米",
}
SCALE_FALLBACK = SCALE_FALLBACK_IN          # 旧调用兼容


def _fallback_scale(cls, scene=None):
    out = str((scene or {}).get("interior") or "").strip() in ("外", "室外")
    tbl = SCALE_FALLBACK_OUT if out else SCALE_FALLBACK_IN
    return tbl.get(cls, "")
# 等级排序，用于取"更大的那个"。
SCALE_RANK = {"小": 1, "中": 2, "大": 3}


def place_scale(scene, settings=None):
    """这个场景该多大。返回 (等级, 具体米数)，判不出来返回 (None, "")。

    【为什么必须定死】实测里模型把"古老遗迹的入口"判成
    「宽8米×深10米/层高6米、规模小」，出图就是个小土洞——
    用户说的"一个小屋子看起来猥琐憋屈"，根子在这张卡上，不在模型。
    """
    hit = place_entry(scene, settings)
    if not (hit and hit.get("scale_class")):
        return None, ""
    cls = str(hit["scale_class"])
    # 【等级可以借用，米数不能借用】"迷宫入口（外）"命中的是室内的「地牢遗迹」，
    # 等级"大"是对的，但"甬道宽6米、主厅层高25米"是室内说法，安到林间空地上就荒唐。
    want = str((scene or {}).get("interior") or "").strip()
    if want and "indoor" in hit:
        same = bool(hit["indoor"]) == (want in ("内", "室内"))
        if not same:
            return cls, _fallback_scale(cls, scene)
    return cls, str(hit.get("scale") or _fallback_scale(cls, scene))


def merge_scale(card_class, card_scale, want_class, scene=None, settings=None):
    """把卡上已有的尺度和新来的等级合成一份自洽的 (等级, 米数)。

    规矩：等级取两者中更大的那个——同一个空间在不同场次被判成"中"和"小"时，
    按大的算比按小的算安全（拍大了只是气派，拍小了就是憋屈）。
    米数必须跟等级对得上，对不上就用地点库或兜底表重写。
    """
    lib_class, lib_scale = place_scale(scene or {}, settings)
    cands = [c for c in (card_class, want_class, lib_class) if c in SCALE_RANK]
    if not cands:
        return (str(card_class or ""), str(card_scale or ""))
    final = max(cands, key=lambda c: SCALE_RANK[c])
    # 米数：优先用地点库里这一类的原文（前提是等级一致），否则用兜底表。
    if lib_class == final and lib_scale:
        return final, lib_scale
    if str(card_class or "") == final and str(card_scale or "").strip():
        return final, str(card_scale).strip()
    return final, _fallback_scale(final, scene)

def match_scene_name(location, scene_names):
    """地点名 → 场景卡名。剥标点后互相包含，再不行按字重合率挑最像的。

    骨架给的 location（"云岚宗·订婚宴大殿"）和场景卡名（"云岚宗大殿"）
    经常差一两个字，精确匹配一失手，拍摄条件和参考图就全丢了。
    """
    import re as _re
    loc = str(location or "").strip()
    if not loc:
        return None
    for n_ in scene_names:
        if n_ == loc:
            return n_
    for n_ in scene_names:
        if loc in n_ or n_ in loc:
            return n_
    def _norm(x):
        return _re.sub(r"[·・\-—－_、，,。.\s（）()【】\[\]]+", "", str(x or ""))
    ln = _norm(loc)
    for n_ in scene_names:
        nn = _norm(n_)
        if nn and (ln in nn or nn in ln):
            return n_
    best, best_r = None, 0.0
    for n_ in scene_names:
        nn = _norm(n_)
        if not nn:
            continue
        r = len(set(ln) & set(nn)) / max(1, min(len(set(ln)), len(set(nn))))
        if r > best_r:
            best, best_r = n_, r
    return best if best_r >= 0.6 else None


def place_is_indoor(scene, settings=None):
    """这个场景是室内还是室外。返回 True/False/None（判不出来）。

    【为什么必须显式判定】实测的云岚宗大殿那张图里，大殿内景、庭院、石阶、
    远山云海同时出现在一个画面里——室内室外糊成一团，看着就不成立。
    根因有两个：地点模板里写了"殿门外是石阶与云海"（已改），
    以及提示词从头到尾**没有一句话说这是室内还是室外**，
    模型自然两边都画。这里把它定死。
    """
    sc = scene or {}
    v = str(sc.get("interior") or "").strip()
    if v in ("内", "室内", "indoor", "interior"):
        return True
    if v in ("外", "室外", "outdoor", "exterior"):
        return False
    world = kit_world(settings or {}, "scene")
    entries = load_place_library().get(world) or []
    source = " ".join(str(sc.get(k) or "") for k in
                      ("name", "contract_text", "space", "landmarks", "fixed_landmarks"))
    # 名字里明写了内外的，优先级最高。"山门结界外"会命中「宗门大殿」的关键词"山门"
    # 而被判成室内——名字都写了"外"了，不该被模板带偏。
    name = str(sc.get("name") or "")
    # 【跨内外的场景不许锁死】「旧时代咖啡店**外及店内**」这种名字末尾是"内"，
    # 就被判成全封闭室内，提示词于是写"四面实心墙、顶棚盖满画面"，
    # 可这一场第一段拍的是街上下着雨（2026-08-30 用户实测 STORY_066）。
    # 同时含内外的返回 None，让下游用"围合方式全画面统一"那句兜底。
    if re.search(r"(外[^\n]{0,4}(及|和|与|、|\+)[^\n]{0,4}内|"
                 r"内[^\n]{0,4}(及|和|与|、|\+)[^\n]{0,4}外)", name):
        return None
    if name.endswith(("外", "外面", "外景", "口")) or "门外" in name or "野外" in name:
        return False
    if name.endswith(("内", "内景", "里")) or "内景" in name:
        return True
    chosen = str(sc.get("place_preset") or "").strip()
    hit = None
    if chosen and chosen not in ("自动", "自定义", "None"):
        hit = next((x for x in entries if x.get("name") == chosen), None)
    if hit is None:
        best, best_n = None, 0
        for x in entries:
            k = sum(1 for w in (x.get("keywords") or []) if w and w in source)
            if k > best_n:
                best, best_n = x, k
        hit = best
    if hit is not None and "indoor" in hit:
        return bool(hit["indoor"])
    # 模板里找不到就按名字里的字判
    if any(w in source for w in ("殿", "室", "房", "厅", "舱", "洞", "教室", "客栈内",
                                 "店内", "内景", "地牢", "密室", "走廊", "楼道")):
        return True
    if any(w in source for w in ("街", "路", "广场", "山", "林", "野", "田", "海",
                                 "天台", "屋顶", "操场", "外景", "谷", "崖", "空地")):
        return False
    return None


def scene_type_kit(scene, settings=None):
    """按世界与地点补充可见的时代陈设，避免宽泛地点被模型现代化。

    【为什么要有】只写一个宽泛世界词时，模型很容易把酒馆补成现代酒吧。
    原来只有西方魔幻一个世界写了这张表（还是硬编码在代码里），
    另外七个世界的"客栈""办公室""避难所"照样被模板化。
    现在跟服装方向库一样，全部搬进 presets/place_library.json：
    加地点是改数据，不是改代码。
    """
    # 【内外不符就不套模板】原来这里自己写了一遍关键词匹配，不看 indoor。
    # 于是「迷宫入口（外）」——一片林间空地——套上了「地牢遗迹」的模板，
    # 提示词里凭空多出"拱券甬道、铁制火盆、木栅门"，出图就在森林里
    # 盖起了带火盆的地下石厅。现在共用 place_entry，它会先比内外。
    sc = scene or {}
    hit = place_entry(sc, settings)
    if not hit:
        return ""
    want = str(sc.get("interior") or "").strip()
    if want and "indoor" in hit:
        if bool(hit["indoor"]) != (want in ("内", "室内")):
            return ""
    return str(hit.get("prompt") or "")


_OUTFIT_BASES = {
    "东方仙侠": [
        "米白亚麻交领内衫，窄袖收腕；深青丝麻束腰长袍，侧摆开衩；墨色皮革宽腰封；同色直筒下装；包头布履；玉扣与暗纹护腕",
        "浅灰棉麻中衣，交领压边；靛蓝短褂叠半长披挂；双层皮革束带；窄脚长裤；软底皮靴；铜扣药囊",
    ],
    "东方写实": [
        "本白麻布交领内衣，窄袖收口；深褐棉布圆领外袍；素皮腰带；灰黑直裾下装；包头布履；木质腰牌",
        "浅青细棉中衣；炭灰短打外层，肘部加固补片；双环皮革腰带；宽松长裤；低帮皮靴；布质随身袋",
    ],
    "西方魔幻": [
        "象牙白亚麻立领内衫，袖口系带；深蓝羊毛长外衣，不对称下摆；棕色皮革双腰带；炭灰长裤；高帮包头皮靴；银色符文扣与短斗篷",
        "灰白亚麻衬衣；暗红皮革护肩叠轻锁甲外层；黑色宽腰封；耐磨束脚裤；带金属护片的战靴；旧铜徽章与小药囊",
        "雾灰贴身长袖内层；墨绿羊毛兜帽斗篷；交叉皮革束带；深色长裙或长裤；系带皮靴；骨质扣件与卷轴筒",
    ],
    "西方写实": [
        "乳白亚麻衬衣，袖口收褶；深棕羊毛马甲与及膝外套；皮革腰带；炭灰长裤；系带皮靴；黄铜表链与布手套",
        "浅灰棉质内裙或内衫；海军蓝羊毛长外套；窄皮腰带；同色下装；低跟包头皮鞋；哑光金属胸针",
    ],
    "未来科幻": [
        "石墨灰智能织物贴身层，分区针织；银黑复合材料短外套，偏置拉链；模块化功能腰封；弹性战术长裤；一体式功能靴；发光状态扣与腕部终端",
        "雾白无缝内层；深蓝轻质护甲背心与可拆袖外层；磁吸腰带；关节分片长裤；磁吸底短靴；透明数据片挂件",
    ],
    "校园": [
        "白色棉质衬衫，领口与袖口整洁；深蓝针织校服外套；细皮腰带；灰色长裤或过膝裙；白色运动鞋；帆布书包与校徽别针",
        "浅色圆领针织内搭；米灰运动校服夹克；弹力腰头；深色运动长裤；低帮帆布鞋；腕表与学生证套",
    ],
    "现代都市": [
        "浅灰棉质内搭，领口利落；深色合体短外套；简洁皮革腰带；直筒长裤或中长裙；现代短靴；哑光金属腕表与小型肩包",
        "米白针织内层；炭灰风衣，隐藏门襟与斜切口袋；窄腰带；深色长裤；低帮皮鞋；几何金属扣件",
    ],
    "末日": [
        "灰褐耐磨贴身内层，汗渍与补线清楚；橄榄绿多口袋外套；回收皮革双腰带；加固工装裤；磨损防护战靴；护腕、绳结与金属水壶",
        "暗灰棉布内衫；拼接帆布兜帽披肩与轻护甲；模块腰包；膝部补强长裤；钢头战靴；旧铜扣和过滤面罩挂件",
    ],
}

_OUTFIT_LIBRARY_PATH = Path(__file__).resolve().parent.parent / "presets" / "outfit_library.json"


def load_outfit_library():
    """读取独立服装方向库；损坏时回退旧模板，不能让人物生成整条链失败。"""
    try:
        data = json.loads(_OUTFIT_LIBRARY_PATH.read_text(encoding="utf-8"))
        worlds = data.get("worlds") or {}
        if isinstance(worlds, dict) and worlds:
            return worlds
    except Exception:
        pass
    return {world: [{"name": "%s基础装-%d" % (world, i + 1), "keywords": [], "prompt": text}
                    for i, text in enumerate(items)] for world, items in _OUTFIT_BASES.items()}


def outfit_preset_options(settings_or_world):
    """当前世界可选服装方向。服装轮廓仍由原下拉独立控制。"""
    world = resolved_world(settings_or_world) if isinstance(settings_or_world, dict) else str(settings_or_world or "")
    return ["自动"] + [str(x.get("name") or "") for x in load_outfit_library().get(world, [])
                       if str(x.get("name") or "").strip()] + ["自定义"]


def outfit_preset_defs(settings_or_world):
    world = resolved_world(settings_or_world) if isinstance(settings_or_world, dict) else str(settings_or_world or "")
    return {str(x.get("name") or ""): str(x.get("prompt") or "")
            for x in load_outfit_library().get(world, []) if x.get("name")}


def _choose_outfit_entry(character, settings=None, seed=""):
    c, s = character or {}, settings or {}
    world = resolved_world(s)
    entries = list(load_outfit_library().get(world) or load_outfit_library().get("现代都市") or [])
    if not entries:
        return None, world
    selected = str(c.get("outfit_preset") or "自动").strip()
    if selected not in ("", "自动", "自定义", "None"):
        exact = next((x for x in entries if str(x.get("name") or "") == selected), None)
        if exact:
            from .age_policy import assert_allowed
            assert_allowed(cards=(c,), settings=s, text=exact.get("prompt"), media="image_prompt")
            return exact, world
    source = " ".join(str(c.get(k) or "") for k in
                      ("name", "role", "char_type", "identity", "identity_anchor", "appearance_details", "look", "other", "clothing", "clothing_requirement"))
    # 自动候选也调用同一规则。明确选择若不合适则报错，不偷偷换别的款式。
    from .age_policy import assert_allowed, AgePolicyError
    eligible = []
    for entry in entries:
        try:
            assert_allowed(cards=(c,), settings=s, text=entry.get("prompt"), media="image_prompt")
        except AgePolicyError:
            continue
        eligible.append(entry)
    if not eligible:
        raise AgePolicyError("当前人物没有符合年龄与内容边界的服装预设，请检查人物卡与服装选项。")
    entries = eligible
    scored = []
    for entry in entries:
        score = sum(2 for word in (entry.get("keywords") or []) if word and word in source)
        scored.append((score, entry))
    best = max((x[0] for x in scored), default=0)
    pool = [entry for score, entry in scored if score == best] if best else entries
    key = "%s|%s|%s|%s|%s" % (seed, c.get("character_id"), c.get("name"), c.get("role"), world)
    idx = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16) % len(pool)
    return pool[idx], world


def complete_outfit(character, settings=None, seed=""):
    """用内置组合模板补齐服装，稳定、快速，绝不启动文字模型。"""
    c = character or {}
    entry, world = _choose_outfit_entry(c, settings, seed)
    if not entry:
        return str(c.get("clothing") or ""), "%s-无模板" % world
    original = str(c.get("clothing_original") or c.get("clothing") or "").strip()
    selected = str(c.get("outfit_preset") or "自动").strip()
    if selected == "自定义" and original:
        return original, "%s-自定义" % world
    resolved = "服装方向「%s」，内层、外层、腰部、下装、鞋和配件齐全：%s" % (
        entry.get("name"), entry.get("prompt"))
    # 自动模式保留用户已经明确写出的颜色、饰品或身份特征；明确点了某一预设时，
    # 预设就是新选择，不再把上一套完整服装混进来。
    if original and selected in ("", "自动", "None"):
        resolved = "保留原始服装特征：%s；%s" % (original.rstrip("。；;"), resolved)
    intensity = resolved_intensity(settings or {})
    if intensity == "强设计":
        resolved += "；强设计强化：主轮廓远看清楚，采用一处非对称层次、一处高识别度视觉焦点和软硬材质反差，装饰服务于身份与动作，不堆满全身"
    return resolved, "%s-%s" % (world, entry.get("name"))
