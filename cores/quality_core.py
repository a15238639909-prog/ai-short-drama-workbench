# -*- coding: utf-8 -*-
"""quality_core.py — V4.1 Quality Core：Style Profiles / Prompt Compiler / Frozen Anchors / Continuity / Manifest。"""
import json, time
from pathlib import Path
from . import store

STYLE_PROFILES = {
    "电影级写实": ("高预算真人电影摄影质感；自然肤色与肤质；真实材质与反光；电影级布光（主光/轮廓光/环境光）；"
               "适度的暗部层次；浅景深；画面干净、低噪点、真实度优先的写实摄影质感。" ),
    "日系动画": ("日系动画作画；干净线条；高饱和而柔和的色彩；光晕与空气感；动画材质；角色美型；无写实摄影颗粒。"),
    "暗黑巴洛克": ("暗黑巴洛克油画质感；厚重材质（丝绸/金属/皮革）；戏剧性明暗；浓郁配色；古典构图；画面真实度中等。"),
    "东方史诗": ("东方史诗电影质感；传统建筑与服饰材质；宏大空间；暖金与黛青配色；飘逸光影；写实人物。"),
    "黑色电影": ("黑色电影（Noir）质感；硬光与深阴影；高对比黑白或低饱和；雨夜与烟雾；胶片颗粒；写实。"),
    "自然纪实": ("自然纪实摄影；真实光线；环境自然色；颗粒适中；无夸张构图；人物与环境真实可信。"),
}

# 【风格唯一来源】提示词里只允许出现一处风格描述——用户在项目设定里选的那个画风。
# 其余部分一律只描述客观事物（脸型、身材、服装、空间结构、尺度、光源位置）。
# 原来开头硬编码一段"高预算真人电影摄影质感…画面干净、低噪点"，会和选中的画风打架：
# 选「超仿真生活照」要的是随手拍的噪点，硬编码那句却要求干净低噪点，方向相反。
def style_profile(name):
    if name in STYLE_PROFILES:
        return STYLE_PROFILES[name]
    try:
        from .style_presets import style_description
        full = style_description(name)
        if full:
            return full
    except Exception:
        pass
    return STYLE_PROFILES["电影级写实"]

# ---- Character Compiler（Frozen Anchors，禁止同义改写） ----
import re


def _identity_extras(anchor, who_so_far):
    """从 identity_anchor 里只挑出还没写进提示词的部分，并剥掉字段标签。"""
    said = "，".join(str(x or "") for x in who_so_far)
    out = []
    for part in re.split(r"[；;]", str(anchor or "")):
        if "：" not in part:
            continue
        label, _, val = part.partition("：")
        # 这些字段各有唯一的结构化来源。人物卡旧快照里的值可能已经过期，
        # 继续当成固定特征会造成“极瘦 + 曲线丰满”这类直接冲突。
        if label.strip() in {"年龄性别", "人物类型", "脸型", "五官骨相", "身材", "体型",
                            "身体比例", "头身比例", "发型", "服装"}:
            continue
        val = val.strip("。；; ")
        if not val:
            continue
        # 这一项的内容已经出现过（哪怕只是一小段），就不再重复
        if val in said or any(v and v in said for v in re.split(r"[，、]", val) if len(v) > 3):
            continue
        out.append(val)
    return "，".join(out)


def compile_character_prompt(character, wardrobe=None, scene=None, state=None,
                             action="", composition="", style="电影级写实",
                             purpose_details="", appearance_only=False):
    """编译发给 Krea2 的人物设定板提示词。

    **只写模型看得懂的视觉描述，不写元指令。**
    扩散模型是"你描述什么它画什么"，写「须符合这个世界的服装体系」「内容尺度允许」
    「整体比例风格：」这类话，它既不理解也不执行，只会当成一堆噪声词。

    格式采用用户验证过的分段写法：
        1.画面内容  2.摄影构图  3.发型穿搭  4.姿势神态  5.光影质感
    每段都是纯粹的名词与形容词描述。
    """
    c = character or {}
    proj = c.get("_project") or {}
    try:
        from cores import style_presets as _sp
    except Exception:
        _sp = None

    def d(fn, *a):
        try:
            t = (getattr(_sp, fn)(*a) or "").strip() if _sp else ""
        except Exception:
            t = ""
        # 预设名带冒号的（"极致超仿真生活照：普通手机随手抓拍…"）只保留冒号后的描述
        if "：" in t and t.index("：") < 14:
            t = t.split("：", 1)[1]
        return t.replace("；", "，").strip("。， ")

    def g(k):
        v = str(c.get(k) or "").strip()
        return "" if v in ("自动", "None") else v

    STORY_WORDS = ("常骑", "喜欢", "经常", "习惯", "职业是", "身份是", "背景是",
                   "性格", "目标", "过去", "曾经")

    def clean(t, drop_story=False):
        for tag in ("外貌：", "身材：", "性格：", "识别特征：", "外貌:", "识别特征:"):
            t = t.replace(tag, "")
        t = t.replace("；", "，")
        if drop_story:
            # 设定板只画看得见的外观：剧情、习惯、职业这些模型画不出来，写了只是噪声
            t = "，".join(x for x in t.split("，")
                         if x.strip() and not any(w in x for w in STORY_WORDS))
        return t.strip("，。 ")

    def fix_length(t):
        """把"及肩""及腰"这种相对长度词换成模型画得准的大白话落点。"""
        for a, inline, tail in LENGTH_FIX:
            if a in t:
                return (t.replace(a, inline).strip("，") + tail).strip("，")
        return t

    try:
        from cores import project_prompt as _pp
    except Exception:
        _pp = None
    # 人物卡有值时逐项直读；只有空项才走兼容性的自动兜底。
    # 这样改身材不会重新选择脸，改脸也不会重新选择身材。
    auto = _sp.resolve_auto_character(c, proj) if _sp else {}
    sex, age = g("sex") or "女", g("age")
    # 手动字段原文直达提示词，不再替换发长、删除状态词或做“健康化”改写。
    hair_fixed = g("hair")
    hair_name = g("hair_preset") or auto.get("hair_preset")
    if not hair_fixed and hair_name and _sp:
        hair_fixed = d("hair_def", sex, hair_name)
    face_name = g("face_type") or auto.get("face_type")
    body_name = g("build") or auto.get("build")
    face = d("face_def", sex, face_name)
    body = d("body_def", sex, body_name)
    pose = d("pose_def", auto.get("pose") or g("behavior_anchor") or g("personality") or g("pose"))
    sil = d("cloth_def", auto.get("clothing_requirement") or g("clothing_requirement"))
    species_name = auto.get("char_type") or g("char_type")
    species = "" if species_name in ("", "人类") else d("char_type_def", species_name)
    look = g("appearance_details") or g("look") or g("other")
    # appearance_details 是补充识别点，不再关闭身材或五官下拉；结构化选项始终可控。
    # identity_anchor 是「年龄性别：22岁男；脸型：英气硬朗脸；外貌细节：…」这种
    # **带字段标签的结构化快照**，它的每一项在上面都已经用可画的语言写过一遍了。
    # 原样塞进提示词有两个害处：
    #   1. 整段信息重复第二遍，稀释每个词的权重；
    #   2. 「年龄性别：」「脸型：」是元标签，扩散模型看不懂——本文件开头的规矩
    #      就写着不许出现「体系：」这类标签。
    # 所以只挑出上面确实没写到的那几项，去掉标签后补进去，写过的直接丢。
    identity = _identity_extras(g("identity_anchor"), who_so_far=[
        face, body, look, hair_fixed, species,
        # 枚举名本身也算"已经说过"：上面已经把「英气硬朗脸」展开成骨相描述了，
        # 再补一个词条名只是同义反复。
        age, sex, str(age or "") + str(sex or ""),
        face_name, body_name,
        auto.get("char_type") or g("char_type")])
    sd = d("style_description", str(proj.get("style") or "").strip()) or style_profile(style)

    # 1. 画面内容
    # 人物设定图不再使用绝对身高。单人四视图会自动缩放人物，身高数字没有
    # 可见参照；体态只由头身比例和身材控制。
    ratio = ""
    ratio_name = g("sheet_body_ratio") or auto.get("sheet_body_ratio")
    if not ratio_name or ratio_name == "自动":
        # 自动档先读故事生成的人物卡；只有故事没有明确体态时才按画风兜底。
        # 用户在界面手动选择任何具体档位后，不再进入本分支，手动值始终优先。
        _story_shape = " ".join(str(x or "") for x in (
            age, g("char_type"), g("appearance_details"), g("identity_anchor"),
            g("look_full"), g("build"), g("name")))
        _ratio_style = str(proj.get("style") or style or "")
        if any(x in _story_shape for x in (
                "迷你", "三头身", "3头身", "不到一米", "幼儿体态", "娃娃体态")):
            ratio_name = "迷你幼态"
        elif any(x in _story_shape for x in (
                "幼态矮小", "幼年精灵", "小精灵少年", "孩童体态", "儿童体态",
                "小男孩", "小女孩", "男童", "女童", "四头身", "4头身")):
            ratio_name = "幼态少年"
        elif any(x in _story_shape for x in ("矮小", "短手短腿", "小个子", "敦实短小")):
            ratio_name = "小少年比例"
        elif any(x in _story_shape for x in ("十一头身", "十二头身", "幻想夸张比例")):
            ratio_name = "夸张幻想"
        elif any(x in _story_shape for x in ("十头身", "大长腿")):
            ratio_name = "大长腿"
        elif any(x in _story_shape for x in ("极度高挑", "九头身")):
            ratio_name = "高挑模特"
        elif any(x in _story_shape for x in ("高挑", "修长", "长腿")):
            ratio_name = "模特比例"
        elif "电影" in _ratio_style:
            ratio_name = "标准成人"
        elif any(x in _ratio_style for x in ("网红", "动漫", "日漫", "平面", "3D", "游戏", "卡通")):
            ratio_name = "高挑模特"
        else:
            ratio_name = "标准成人"
    if ratio_name and _sp:
        ratio = (_sp.body_ratio_def(ratio_name) or "").split("；", 1)[0].strip()
    # 精灵通用词里的“修长人形骨骼”会覆盖幼态短肢比例。幼态三档只保留物种
    # 的尖耳和面部识别特征；标准及高挑档仍使用完整物种描述。
    if ratio_name in {"迷你幼态", "幼态少年", "小少年比例"} and species_name == "精灵":
        species = "明显尖耳、精灵族面部特征"
    # 内容倾向里属于"身体长什么样"的部分（夸张体型、成人向的身体结构）
    # 是外观信息，必须进外观段——原来它只进了一键生成那一步的故事文字。
    tend = ""
    try:
        from cores.style_presets import tendency_effect as _te
        tend = _te(proj.get("content_tendencies") or [], "figure")
    except Exception:
        pass
    custom_scale = str(proj.get("custom_content_scale") or "").strip()
    if custom_scale:
        tend = (tend + "，" + custom_scale).strip("，")

    from .age_policy import assert_allowed
    assert_allowed(cards=(c,), settings=proj, media="image_prompt")
    visual_age = age
    adult_anchor = ""
    scale_variant = {"pose": "", "clothing": ""}
    if _sp:
        scale_variant = _sp.character_figure_variant(proj.get("content_tendencies") or [], sex)

    scale_pose = clean(str(scale_variant.get("pose") or ""))
    scale_clothing = clean(str(scale_variant.get("clothing") or ""))
    # 美感只修饰面部和成像，绝不推导身高、体型或比例。
    style_name = str(proj.get("style") or style or "")
    if "电影" in style_name:
        beauty = "极度写实的真人面孔，真实皮肤与自然骨相细节，保持这个人物独有的五官"
    else:
        beauty = "面部美观精致、五官协调上镜，保持这个人物独有的骨相与识别特征"
    # 【六维五官】这是让两个角色长得不一样的主力。
    # 原来区分一张脸全靠"脸型"那 26~33 字，而脸型男女各只有 8 个选项、
    # 写的还是"小脸／宽脸／窄长脸"这种骨相分类——10 个角色必然撞，撞了就是
    # 同一段话，画出来自然像亲戚。现在按眼型/眼睑/眉/鼻/唇/识别点逐项给，
    # 组合数十万级，且卡上存着，重生成不会变脸。
    # 只有卡上真的存了六维五官才启用。老卡没有这个字段，行为完全不变——
    # 不能因为加了新机制就把用户选过的脸型悄悄换掉。
    facial = ""
    spec = c.get("face_spec")
    if isinstance(spec, dict) and spec.get("face_shape"):
        facial = "%s，%s眼裂、眼尾%s，%s、眉骨%s，鼻梁%s、鼻头%s，%s唇，下颌%s%s" % (
            spec.get("face_shape", ""), spec.get("eye_len", ""), spec.get("eye_tail", ""),
            spec.get("brow", ""), spec.get("brow_bone", ""), spec.get("nose_bridge", ""),
            spec.get("nose_tip", ""), spec.get("lip", ""), spec.get("jaw", ""),
            ("，静态第一印象：" + str(spec.get("note"))) if spec.get("note") else "")
    # 这里必须直接取原始字段：g() 会把值转成字符串，dict 进去就成了 "{'eye': ...}"，
    # isinstance 判断永远为假，五官段静默消失。
    feats = c.get("face_features")
    if not facial and _pp and isinstance(feats, dict) and feats:
        try:
            facial = _pp.face_feature_text(feats, sex)
        except Exception:
            facial = ""
    if facial and face:
        # 脸型讲的是轮廓和骨相，六维五官讲的是眼眉鼻唇。
        # 两边都写"眼型中等"和"鹿眼，眼裂高而圆"会自相矛盾，
        # 所以脸型只保留轮廓那几句，五官细节交给六维。
        keep = [x for x in re.split(r"[、，]", face)
                if any(w in x for w in ("脸", "下颌", "颧骨", "骨骼", "骨相",
                                        "面中", "面部线条", "轮廓", "比例", "年龄感"))]
        face = "、".join(keep)
    # 比例和身材放在画面内容第一位。这里全部是模型能直接画出的几何描述，
    # 不依赖“最高优先级”之类模型未必理解的元命令。
    structure_anchor = "，".join(x for x in [
        ("正面、正侧面、背面三个全身视图都采用同一骨架：%s" % ratio) if ratio else "",
        ("三个全身视图都保持同一身材轮廓：%s" % body) if body else "",
    ] if x)
    who = [x for x in [structure_anchor, visual_age, sex, adult_anchor, species,
                       ("身体比例：" + ratio) if ratio else "",
                       ("身材：" + body) if body else "",
                       beauty, facial, ("五官骨相：" + face) if face else "",
                       tend, hair_fixed, look, identity] if x]
    S = ["1.画面内容 " + "，".join(who) + "，纯白背景，画面中只有这一个人物"]

    # 2. 摄影构图
    ratio_ruler = (("，三个全身视图以头顶到下巴作为一个头长，严格保持同一比例尺：" + ratio)
                   if ratio else "")
    S.append("2.摄影构图 16:9 宽幅人物设定板，画面严格由四个互不重叠的视图区组成："
             "前三格依次为正面全身、90度正侧面全身、背面全身，第四格是右侧唯一的正脸特写，四个视图同一人物、同一服装、同一光照，"
             "前三个全身视图都从头顶完整画到脚底，头顶上方和脚底下方各留出一段空白，身体没有任何部分超出画面；第四格只呈现一个头肩正脸，平视机位，画面从近到远全部清晰，没有任何模糊的地方，四个视图同样清楚，"
             # 实测四张里有一张把正脸特写那一格的背景拉成了深灰去营造气氛——
             # 这张图是要当参考图喂给下游的，背景不一致会污染后面每一格。
             "四个视图的背景是同一片均匀的纯白，正脸特写那一格的背景也是纯白，"
             "没有光斑、没有暗角、没有渐变、没有深色衬底" + ratio_ruler)

    # 3. 发型穿搭
    wear = []
    sheet_nude = bool(c.get("_sheet_nude"))
    if scale_clothing:
        wear.append(scale_clothing)
    elif sheet_nude:
        wear.append("成年人物全身不穿服装，三个全身视图保持同一身体和同一固定饰物")
    elif wardrobe and wardrobe.get("prompt_text"):
        wear.append(clean(str(wardrobe["prompt_text"])))
    elif g("clothing"):
        wear.append(g("clothing"))
    detailed_clothing = "，".join(wear)
    silhouette_words = ("贴身", "修身", "宽大", "垂坠", "披挂", "短款", "长款", "多层", "装甲", "不对称")
    if not scale_clothing and sil and not any(w in detailed_clothing for w in silhouette_words):
        wear.append(sil)
    # 世界和整部作品附加要求不再自动改写这张卡的服装；人物卡写什么就画什么。
    if not scale_clothing and not sheet_nude and not any(w in "，".join(wear) for w in FOOTWEAR_WORDS):
        # 传整份 proj 而不是 proj["world_type"]：选「自动判断」时字面值查不到表，
        # 会让中世纪装束配上一双来路不明的平底鞋。
        wear.append(_pp.footwear_for(proj) if _pp else FOOTWEAR_DEFAULT)  # 词条没写鞋，模型会画成赤脚
    cloth_text = "，".join(wear)          # 面料识别只看这一段：头发里的"发丝"不是丝绸
    if g("hair"):
        # 只写"金棕色微卷及肩长发"，模型会画成一整块塑料头套；补上真实头发的结构
        wear.insert(0, hair_fixed + "，" + HAIR_STRUCTURE)
    S.append("3.发型穿搭 " + ("，".join(wear) if wear else "合身的日常成衣，四视图同一套"))

    # 4. 姿势神态
    # 设定板是静止四视图，"手势多、动作幅度大"这类动态描述会和它打架，
    # 只保留脸上看得出来的部分
    # 性格定义（POSE_DEFS）每条都是"神情 + 体态"混写的，例如
    # 活泼外放 = 表情明亮、嘴角上扬、身体姿态开放、手势多、动作幅度大。
    # 设定板是静态四视图，姿势永远是端正站立，体态那一半一旦进来就把设定板毁了。
    # 用白名单只挑神情，比黑名单可靠：黑名单漏一个词就放行一个动作。
    face_pose = "，".join(x for x in re.split(r"[，、]", pose or "")
                         if any(w in x for w in EXPRESSION_KEYS))
    if scale_pose:
        S.append("4.姿势神态 " + scale_pose
                 + ("，" + face_pose if face_pose else "，神情自然"))
    else:
        S.append("4.姿势神态 端正站姿，双手自然垂落在身体两侧，正视镜头"
                 + ("，" + face_pose if face_pose else "，神情自然"))

    # 5. 光影质感
    # 判定要连画风名一起看：d() 会把"极致中国水墨画："这种前缀剥掉，
    # 剥完文本里就没有"水墨"两个字了，只看描述会误判成写实
    photoreal = is_photoreal(str(proj.get("style") or "") + " " + sd)
    sd = sheet_style(sd)                 # 设定板：只留渲染方式，拍摄感全部剔掉
    sd = sd.replace("成年角色外表往二十岁上下靠", "")
    # 这个画风自己的渲染要点。有就用它替掉那两句通用的皮肤/发丝描述——
    # 它写得具体得多（柔光箱位置、胶片曲线、青橙分离、银盐颗粒粗细）。
    # 同样要过 sheet_style：设定板是给下游当参考图的，拍摄感必须剔干净。
    note = sheet_note(render_note(str(proj.get("style") or "").strip(), "char"))
    note = note.replace("成年角色外表往二十岁上下靠", "")
    if note:
        tex = [note]
    else:
        tex = [SKIN_TEXTURE if photoreal else STYLIZED_SKIN,
               ("根根分明的发丝，发丝间有明显光影对比，高光勾勒发丝轮廓，毛发细节清晰锐利"
                if photoreal else "发丝分组清晰，发梢走向明确，高光块形状干净")]
    # 有几种面料就分别写几种反射；认不出来才退回那句笼统的
    fab = fabric_notes(cloth_text) if photoreal else []
    tex += fab if fab else ["服装面料纹理清晰真实" if photoreal else STYLIZED_FABRIC]
    tex.append(DETAIL_INTEGRITY)
    if sd:
        tex.append(sd)
    # 资料级布光和纯白背景是设定板的硬约束，必须压在画风要点后面收尾，
    # 否则画风里的戏剧布光会把四视图的一致性毁掉。
    tex.append(SHEET_PRESENTATION)
    S.append("5.光影质感 " + "，".join(tex))

    # 长提示词的末端再用同样的可画几何收束，避免画风、材质和服装文字把体态稀释。
    if ratio or body:
        body_shape_light = ""
        if body_name in {"偏瘦有线条", "曲线丰满", "丰盈曲线", "夸张曲线", "强壮", "健美", "夸张壮硕"}:
            body_shape_light = ("使用斜前侧塑形主光，保留肩臂、腰腹、臀部和大腿表面的明暗交界，"
                                "让肌肉起伏与身体曲线形成清楚阴影，补光只避免死黑但不抹平结构")
        S.append("6.人体结构校准 " + "，".join(x for x in [
            ("正面、侧面、背面都严格画成%s：%s" % (ratio_name, ratio)) if ratio else "",
            ("人物外轮廓严格画成以下身材：%s" % body) if body else "",
            body_shape_light,
        ] if x))

    if appearance_only:
        # 纯外观：只有 1~4 段，零画风词、零质感词。这一段是要定死存进人物卡的，
        # 换画风时它一个字都不变。渲染交给画风指令词。
        return "。".join(x.strip().rstrip("。 ") for x in S[:4] if str(x).strip()) + "。"

    if composition:
        S.append(composition)
    if purpose_details:
        S.append(purpose_details)
    if identity:
        # 冻结外观在提示词尾部逐字重复一次，长提示词下也不容易被后文稀释。
        S.append("7.人物固定特征 " + identity)
    return "。".join(x.strip().rstrip("。 ") for x in S if str(x).strip()) + "。"


# 世界定义表里画不出来的词：人物类和事件/抽象类。
# 空场场景图明确不要人，这些词一旦进正向提示词就会招来人物或纯粹浪费权重。
# 世界定义里写给人看的调度语，不是画面内容。
META_WORLD_TERMS = ("允许", "按故事", "默认无", "调用", "需要", "决定", "服从", "但可以", "但只")

# 质感描述必须跟着画风走：日漫设定板写"保留毛孔和面颊绒毛"是错的，
# 二维画风里皮肤本来就是平涂，写毛孔只会让模型在动漫脸上糊一层写实纹理。
NON_PHOTOREAL = ("动漫", "日漫", "赛璐璐", "插画", "厚涂", "水墨", "像素",
                 "游戏模型", "漫画", "手绘", "水彩", "油画", "蒸汽波",
                 "宣纸", "皴法", "留白", "3D", "卡通", "蒸汽波")

STYLIZED_SKIN = "皮肤上色干净均匀，面部结构和明暗交界清楚，五官线条明确"
STYLIZED_FABRIC = "衣物褶皱走向清楚，不同材质用不同的上色方式区分开"


def is_photoreal(style_text):
    return not any(w in str(style_text or "") for w in NON_PHOTOREAL)

# 【设定板 ≠ 成片】人物/场景设定图是**资料图**，不是成品。
# 它会被当参考图喂给下游的视频和漫画，所以画面里任何"拍摄感"都会被烙进去传染给每一格：
# 噪点、曝光不均、构图随意、光晕阴影，在成片里是味道，在参考图里是污染。
# 但也不能一律换成写实中性渲染——项目画风是日漫时，设定板也必须是日漫。
# 所以规则是：**保留画风的渲染方式，剔掉画风的拍摄感。**
SHEET_STRIP = ("抓拍", "随手", "噪点", "曝光不均", "构图随意", "不完美", "不精致",
               "粗粝", "光晕", "颗粒", "手持", "偷拍", "生活照",
               "不布光", "不布景", "不摆拍", "曝光不准", "发糊", "去精致化", "站位自然不刻意",
               # 渲染要点接进来之后才暴露出来的：这些同样是"拍摄感"，
               # 烙进参考图会传染给后面每一格。
               "手机", "HDR", "原图", "锐化", "畸变", "暗角", "景深", "散景", "虚化")

# 设定板固定要的呈现方式（来自 8848 的「中性角色设定渲染」预设，去掉其中的元指令和否定句）
SHEET_PRESENTATION = ("均匀柔和的棚拍光，正面偏左主光，右侧补光提亮暗部，背景干净无投影，"
                      "四个视图受光完全一致，清楚展示身体比例、面部骨相、发型轮廓、"
                      "服装结构与材质差异")


# 【画风渲染要点：本来就该生效，一直没接线】
# presets/render_notes.json 里给 12 个画风各写了几百字的渲染要点（布光、调色、
# 颗粒、皮肤处理、发丝画法），分人物板和场景板两套。审计发现它的唯一读者
# cores/style_instruction.py 从来没有被任何生产代码调用过——两个文件加起来 27KB
# 的画风逻辑全是死数据，出图实际只用到了 comic_styles 里那句 80 字的画风描述。
# 结果就是 12 个画风的差别被压成一句话，胶片曲线、青橙分离、银盐颗粒全没生效。
#
# 原设计是让 Qwen 按这些要点现写第 5 段。那要每张图多一次模型调用（20~30 秒），
# 而且引入随机性。这些要点本身已经是写好的成品文字，直接注入即可，零模型调用。
def render_note(style_name, target="char"):
    """取某个画风的渲染要点。target: char | scene。取不到返回空串，不报错。"""
    try:
        import json as _json
        from pathlib import Path as _P
        f = _P(__file__).resolve().parent.parent / "presets" / "render_notes.json"
        n = _json.loads(f.read_text(encoding="utf-8")).get(str(style_name or "").strip())
        return str((n or {}).get(target) or "")
    except Exception:
        return ""


# ================== 提示词最终清洗层 ==================
# 【为什么需要这一层】提示词由五六个来源拼起来：编译器写死的、画风渲染要点、
# 世界可见特征、人物卡自由文本、六维五官库、用户的「整部作品长什么样」。
# 每个来源都只管自己那一段，谁也不知道别人写了什么，于是：
#   · 发型在第 1 段和第 3 段各写一遍
#   · 布光被画风要点写一套、设定板固定要求又写一套，两套打架
#   · 画风描述出现两次
#   · 场景卡的 depth 字段带着「中景：」「背景：」这种字段标签直接进了提示词
#   · 空场图里混进了人物名（"高台玄尘子所在的主位"）
#   · 剧情事件（"殿侧破窗缺口"）被当成固定陈设写进基准图
# 与其在每个来源上各打一个补丁，不如在最后统一洗一遍。

# 元指令：模型看不懂，只会稀释权重
_META_WORDS = ("不改变", "仍保持", "只改变", "必须呈现", "须符合", "不得出现",
               "按名称作为", "作为明确约束", "写入生成提示词", "整体呈现出", "体现出",
               "呈现出一种")
# 抽象形容：画不出来
_ABSTRACT_WORDS = ("仪式感", "气场", "压抑感", "神秘感", "厚重感", "高级感",
                   "远看剪影", "一眼认出", "让人觉得", "给人一种", "叙事感")
# 字段标签残留
_FIELD_LABEL = ("近景：", "中景：", "远景：", "背景：", "前景：", "主光：", "环境：",
                "空间：", "尺度：", "纵深：", "材质：", "地面：", "墙面：")


def _split_clauses(text):
    import re as _re
    return [x for x in _re.split(r"[，；;。]", str(text or "")) if x.strip()]


def clean_prompt(text, drop_names=()):
    """最终清洗：去重、删元指令、删抽象词、删字段标签、删人物名。

    按分句处理，保持原顺序，只删不改写。
    """
    import re as _re
    out, keys = [], []
    for raw in _split_clauses(text):
        c = raw.strip()
        if not c:
            continue
        # 字段标签残留：把「中景：xxx」里的标签摘掉，内容留下
        for lb in _FIELD_LABEL:
            if c.startswith(lb):
                c = c[len(lb):].strip()
        if not c:
            continue
        if any(w in c for w in _META_WORDS):
            continue
        if any(w in c for w in _ABSTRACT_WORDS):
            continue
        # 空场图里不许出现人物名
        if drop_names and any(n and n in c for n in drop_names):
            continue
        key = _re.sub(r"[\s、]", "", c)
        # 【重复时留信息多的那句】"乳胶漆墙"（materials 字段）和"浅灰乳胶漆墙"
        # （walls 字段）说的是同一面墙，但后者带了颜色。按出现顺序简单去重会留下
        # 先来的短句、把更具体的删掉——墙的颜色就丢了。
        dup_at = -1
        ks = set(key)
        for i, k in enumerate(keys):
            if key == k:
                dup_at = i
                break
            if len(key) >= 4 and len(k) >= 4:
                if key in k or k in key:
                    dup_at = i
                    break
                share = len(ks & set(k)) / max(1, min(len(ks), len(set(k))))
                if share >= 0.8:
                    dup_at = i
                    break
        if dup_at >= 0:
            if len(key) > len(keys[dup_at]):     # 新的更具体 → 换掉旧的
                out[dup_at] = c
                keys[dup_at] = key
            continue
        keys.append(key)
        out.append(c)
    return "，".join(out)


def _keep_more_specific(clauses):
    """两句说同一件事时留信息多的那句。

    "乳胶漆墙" 和 "浅灰乳胶漆墙" 是同一件事，但后者带了颜色。
    按出现顺序去重会留下先出现的短句，把更具体的那句删掉——信息就丢了。
    """
    out = []
    for c in clauses:
        replaced = False
        for i, o in enumerate(out):
            if o in c:                  # 新的更长且包含旧的 → 换成新的
                out[i] = c
                replaced = True
                break
            if c in o:                  # 新的被旧的包含 → 丢掉新的
                replaced = True
                break
        if not replaced:
            out.append(c)
    return out


def sheet_style(text):
    """把画风文本削成设定板能用的：只留渲染方式，去掉拍摄感那些分句。"""
    keep = [x for x in re.split(r"[，、]", str(text or ""))
            if x.strip() and not any(w in x for w in SHEET_STRIP)]
    return "，".join(keep)


def sheet_note(text):
    """渲染要点专用的削法：**按句子删，不按分句删**。

    要点是成句写的（"极细腻均匀的银盐颗粒，暗部比亮部更明显，细到不影响清晰度。"）。
    按逗号切的话，"银盐颗粒"那半句被剔掉，剩下的"暗部比亮部更明显，细到不影响清晰度"
    就成了没有主语的断头句，接在上一句后面读起来完全是另一个意思。
    整句一起删才不会留下这种残骸。
    """
    out = []
    for sent in re.split(r"[。；;]", str(text or "")):
        sent = sent.strip()
        if sent and not any(w in sent for w in SHEET_STRIP):
            out.append(sent)
    return "，".join(out)

# 【相对长度词模型不吃】"及肩""过腰"这类词扩散模型基本没反应，实测四张图全画成
# 过胸长发。换成看得见的落点，模型才画得准。
# 三元组：(要换掉的文艺词, 换成什么留在原地, 追加到末尾的大白话落点)
# 只删不补会把名词一起删掉——"黑色短发"删掉"短发"就只剩"黑色"了。
LENGTH_FIX = (
    ("及肩", "", "，头发长度到肩膀，发梢正好搭在肩膀上"),
    ("齐肩", "", "，头发长度到肩膀，发梢正好搭在肩膀上"),
    ("及锁骨", "", "，头发长度到锁骨"),
    ("及胸", "", "，头发长度到胸口"),
    ("及腰", "", "，头发长度到腰"),
    ("过腰", "", "，头发很长，发梢垂到腰以下"),
    ("齐耳", "", "，头发很短，发梢在耳垂下面一点"),
    ("及踝", "", "，头发极长，发梢垂到脚踝"),
    ("短发", "短头发", "，头发长度在脖子以上"),
    ("长发", "长头发", ""),
)

# 【设定板必须有鞋】词条只写了上衣和裙子时，模型会把人画成赤脚——实测四张全是赤脚。
# 设定板是要照着做后续每一格的，赤脚是个明确的设计信息，不该由"没写"来决定。
FOOTWEAR_WORDS = ("鞋", "靴", "赤脚", "光脚", "凉拖", "木屐", "足袋")

# 默认鞋必须跟着世界类型走。实测里仙侠角色穿着月白长袍配了一双现代球鞋——
# 因为默认写的是"日常平底鞋"，"日常"在仙侠世界里就被理解成了球鞋。
#
# 【为什么这里只剩一个转发】原来这个文件自己存了一张 FOOTWEAR_BY_WORLD，
# project_prompt.WORLD_KITS 里又有一份 footwear，两份内容还不一样，而且这边
# 少了「现代都市」。现在唯一数据源是 WORLD_KITS['footwear']，改一处就够。
FOOTWEAR_DEFAULT = "脚上是与这身衣服相称的平底鞋"


def footwear_for(settings_or_world):
    try:
        from cores import project_prompt as _p
    except Exception:
        return FOOTWEAR_DEFAULT
    return _p.footwear_for(settings_or_world)

# 【通用面料反射表】真实照片里不同面料的反光方式完全不同，一句"面料纹理清晰"
# 会让模型把所有衣服画成同一种塑料光。按服装文本里出现的面料词逐个挂上对应描述。
# 顺序即优先级，最多取三种——挂太多会稀释权重。
FABRIC_LOOK = (
    ("针织", "针织有清晰罗纹和毛圈，表面起绒不反光"),
    ("毛衣", "针织有清晰罗纹和毛圈，表面起绒不反光"),
    ("棉", "棉布有可见织纹，哑光，垂坠时形成柔软的大褶"),
    ("麻", "亚麻有粗糙纹理和自然折痕，哑光"),
    ("丝绸", "丝绸表面有连续流动的高光，随褶皱转折"),
    ("绸", "丝绸表面有连续流动的高光，随褶皱转折"),
    ("缎", "缎面高光锐利集中，暗部深"),
    ("纱", "薄纱半透，叠层处颜色加深，边缘有细密纤维"),
    ("蕾丝", "蕾丝镂空处透出底层，花纹边缘立体"),
    ("皮革", "皮革有硬边高光和折痕，表面细密皮纹"),
    ("皮", "皮革有硬边高光和折痕，表面细密皮纹"),
    ("牛仔", "牛仔布斜纹清晰，接缝处磨白，厚重挺括"),
    ("呢", "羊毛呢表面有短绒，吸光，边缘厚实"),
    ("羊毛", "羊毛表面有短绒，吸光，边缘厚实"),
    ("羽绒", "羽绒面料有充绒鼓包和分区缝线，尼龙表面有细长高光"),
    ("金属", "金属件有硬边镜面反射和清晰高光点"),
    ("铜", "金属件有硬边镜面反射和清晰高光点"),
    ("银", "金属件有硬边镜面反射和清晰高光点"),
    ("亮片", "亮片逐颗反光，形成密集高光点"),
    ("西装", "精纺面料细腻哑光，肩线与驳头挺括，缝线清楚"),
    ("衬衫", "衬衫棉布挺括，领口和门襟有明显折线"),
)


def fabric_notes(text, limit=3):
    """从服装描述里认出面料，返回各自的反射描述（去重，按表内顺序）。"""
    out, seen = [], set()
    for key, note in FABRIC_LOOK:
        if key in (text or "") and note not in seen:
            seen.add(note)
            out.append(note)
            if len(out) >= limit:
                break
    return out


# 【通用头发结构】发色发型是逐人不同的，但"真实头发长什么样"是所有人共通的。
# 只写颜色和长度，模型会画成一整块塑料头套。
# 只写"及肩微卷"模型会画成塑料头套；这几项是**结构**（外观层），
# "发丝有明暗层次"那种是**受光**（渲染层），不放这里
HAIR_STRUCTURE = "发根清楚，发际线自然，分缝可见，几缕碎发垂在颊侧"

# 【通用皮肤质感】真实皮肤各部位受光不同；一句"保留毛孔"不够，要写出差异。
SKIN_TEXTURE = ("真实皮肤质感，保留细微毛孔和面颊绒毛，鼻翼纹理与唇纹可见，"
                "额头与鼻梁的高光强于脸颊，下颌和颈侧转入暗部")

# 【通用细节完整性】负向词负责赶走畸形，正向词负责把该有的结构说清楚。
DETAIL_INTEGRITY = "十指完整、指节和指甲边缘清晰，耳廓结构完整，睫毛根根分明，衣物缝线与边缘清楚"

# 性格定义里属于"脸"的部分。设定板只取这些，体态和手势一律不取。
EXPRESSION_KEYS = ("表情", "嘴角", "眼睑", "眼睛", "眼神", "视线", "眉", "下巴", "头部", "头略", "面部")

NON_VISUAL_WORLD_TERMS = (
    # 人物
    "贵族", "佣兵", "丧尸", "机器人", "骑士", "异族", "怪物", "妖兽", "宗门",
    # 事件 / 抽象
    "犯罪", "战争", "生存", "灾难", "变异", "日常生活", "文明崩坏", "江湖",
    "朝堂", "民间", "修行", "魔法", "生物科技", "赛博", "人物",
)


# ================== 场景尺度增强 ==================
# 【为什么要有这一块】实测对比：同一座大殿，系统原来的提示词出来是"一个大房间"，
# 而用户手写那份出来是"体育场级巨构"。差距全在文字上，七条：
#   1 人物做尺度参照   2 垂直层数   3 前景框景明确指定
#   4 尺度用类比不用数字（模型对"宽40米"不敏感，对"比足球场还大"敏感）
#   5 末尾重申尺度     6 材质写透   7 体积光和空气透视
# 第 1 条和"基准图必须空场"冲突，所以拆成两张图：
#   基准图（空场，给下游当参考）用 2~7；氛围图（有人，给人看效果）七条全用。

SCALE_WORDS = {
    "大": ("空间尺度极端宏大，室内体量接近大型体育场甚至更大，"
           "挑高相当于十几层楼，纵深极远，站在里面会明显感到自身渺小"),
    "中": "空间开阔，挑高约三四层楼，纵深清楚，比常规房间大出一个量级",
    "小": "空间紧凑，但用超广角和低机位把纵深和高度撑开，不显局促",
}
# 【不写具体构件名】原文写的是"梁架、斗拱、藻井"——中国古建的三个专有构件。
# 西方魔幻的地牢、科幻的机库、现代的中庭都不长这样，写上去世界观当场串味。
# 只描述"垂直关系"这件事本身，具体长什么样交给地点库和整部美术基调。
VERTICAL_STRUCT = ("两侧不是简单墙壁，而是多层立体结构层层向上延伸——"
                   "连续的走廊、挑台、栏杆、侧间和高层平台，五到八层以上的垂直关系，"
                   "每一层有不同的进深；上方的承重结构密集交错，"
                   "顶部一部分隐进昏暗空气与雾气里")
FOREGROUND_FRAME = ("前景左右各放一根极粗的立柱（或门框、梁柱）作为视觉框架，"
                    "只占画面两侧边缘，中间完全让开")
# 室外没有立柱。写"立柱"模型就真的在林间空地两侧立起两根巨柱——
# 实测那张森林图两侧全是室内柱廊，就是这一条造的。
FOREGROUND_FRAME_OUT = ("前景左右各放一棵粗大的树干（或一块高耸的岩体、一段倒塌的残垣）"
                        "作为视觉框架，只占画面两侧边缘，中间完全让开")
VOLUMETRIC = ("空气里有轻微雾霭和烟气，光从侧面高处穿透空气形成明显的体积光柱，"
              "远处结构因空气透视逐渐降低对比度，制造真实的超大空间距离感")
# 【不许写成中式宫殿专有】第一版写的是"鎏金与古铜"，那是仙侠大殿才有的东西，
# 套到现代公寓卧室上就成了笑话。这里只写"材质该怎么真实"，不指定是什么材质。
MATERIAL_REAL = ("所有材质都有真实的使用痕迹：金属带氧化和磨损、"
                 "木头有漆面裂纹和纹理、石材有打磨面、织物有褶皱和起绒，"
                 "反光按各自的材质规律来，不是统一的塑料光")
# 【画出来的画风用这一段】上面那段是实拍语汇（氧化磨损、漆面裂纹、反光规律），
# 日漫／插画的场景图套上去，就和"精致赛璐璐、线条干净"在同一份提示词里打架。
# 同样是"让材质有旧感和层次"，换成绘画的说法。
MATERIAL_DRAWN = ("每种材质用不同的笔触和色块区分：金属用高光边和冷色反光，"
                  "木头用木纹线条和暖色分层，石材用块面和颗粒质感，"
                  "布料用褶皱线和柔和的明暗过渡，旧感靠脏色和磨损的色块画出来")
# 【三维渲染单独一档】3D 既不是实拍也不是手绘：说笔触色块就串成了插画
# （2026-08-30 审计发现，我原来只分"写实/非写实"两档，把 3D 归进了画出来的那档）。
# 三维的材质语言是贴图、粗糙度、金属度、法线。
MATERIAL_CG = ("每种材质给出清晰的物理属性：金属高反射低粗糙度、"
               "木头有木纹贴图和中等粗糙度、石材有凹凸法线和磨损遮罩、"
               "布料有绒毛和次表面透光，旧感靠污渍贴图和边缘磨损做出来")
VOLUMETRIC_CG = ("空气里有体积雾，光穿过形成清晰的体积光束，"
                 "远处物体按距离衰减对比度，层次靠景深和雾效拉开")
# 体积光那段里的"制造真实的超大空间距离感"同样是实拍口吻。
VOLUMETRIC_DRAWN = ("空气里有轻微雾霭和烟气，光从侧面高处穿透空气形成明显的光柱，"
                    "远处结构用降低对比度和提高明度的方式退淡，拉开空间层次")
SCALE_REMINDER = ("再次强调尺度：这不是一个普通大小的房间，"
                  "柱子、阶梯、灯具、楼层全部使用明显超出常规的尺寸，"
                  "一眼就能感受到这个空间的庞大")
SCALE_REMINDER_OUT = ("再次强调尺度：这不是一片普通大小的空地，"
                      "树木、岩体、台阶和远处的山体全部使用明显超出常规的尺寸，"
                      "一眼就能感受到这个地方的辽阔")
# 对比 GPT 出的同类图逐条补的五点——都是文字能补的
SCALE_HARD = ("最前面那两根立柱粗到超出画面上沿，看不见柱顶，"
              "只能靠柱身向上收窄的透视判断它有多高；"
              "画面上半部被层层叠叠的结构层和悬挂物填满，不留成片空白；"
              "从近到远至少能数出四到六层可分辨的建筑结构，一层比一层小、一层比一层淡")
# "楼阁"是东亚建筑词，"悬挂灯具"是室内陈设。原句用在西方魔幻的森林里，
# 出图就长出了歇山顶飞檐和成排吊灯。室外版只说地形和植被的层次。
SCALE_HARD_OUT = ("最前面那棵树（或那块岩体）高到超出画面上沿，看不见顶，"
                  "只能靠树干向上收窄的透视判断它有多高；"
                  "画面上半部被树冠、岩壁或远处的山体填满，不留成片空白；"
                  "从近到远至少能数出四到六层可分辨的地形或植被层次，"
                  "一层比一层小、一层比一层淡")

CROWD_SCALE = ("人物高度不超过最近那根柱子柱基的三分之一，"
               "远处的人缩成两三个像素的深色剪影")


# 【秩序感】混乱不大气，秩序才大气。等距列柱、等高灯具、整齐队列、重复单元——
# 这些是"磅礴"的骨架，没有它画面再华丽也只是热闹。
ORDER_WORDS = ("画面严格中轴对称：两侧等距排开的立柱形成规整列柱，"
               "柱间悬挂等高等距的灯具，上方梁架和斗拱是重复单元密集交错；"
               "地面的石缝、两侧的柱列、远处的阶梯边线，所有线条汇聚到同一个灭点")
# 【暗部压住】画风要点会把暗部抬亮（胶片曲线"最黑处是提亮过的深灰"），
# 那是剧照的味道，不是史诗的味道。氛围图要反过来压。
CONTRAST_HARD = ("亮部集中在中轴线和光柱落点，两侧与上方沉进厚重暗部，"
                 "暗部里仍能看出结构轮廓但不抢戏，最黑处接近纯黑；"
                 "高动态范围，亮部不过曝")
CROWD_REF = ("画面里散布着大量人物，穿着符合这个世界的服装，站在地面和台阶上，"
             "排成整齐的纵列从近处一直排到远处。"
             "他们只是尺度参照，不是主体——近处的人只有正常人大小，"
             "远处的人缩成细小剪影；通过人和柱子、阶梯、灯具的巨大比例差，"
             "一眼看出这个空间有多大。" + CROWD_SCALE)


def compile_scene_mood_prompt(scene, style="电影级写实"):
    """场景**氛围图**：给人看效果、定美术方向用，不当参考图。

    【为什么要跟基准图分开】"大气磅礴"靠七件事：尺度对比、垂直失控、纵深层数、
    强轴线灭点、体积光、暗部压住、秩序感。其中**尺度对比只能靠人**——
    没有已知大小的东西做锚点，再宽的房间也不显大，桌椅太小撑不起体育场级的体量。
    但基准图是要喂给下游视频当参考的，图里有人就会把人烙进后面每一格。
    所以拆两张：基准图空场（可复用），氛围图带人（够震撼）。
    """
    sc = dict(scene or {})
    proj = sc.get("_project") or {}
    base = compile_scene_prompt(sc, style=style)
    # 空场那句在氛围图里要反过来
    base = "，".join(x for x in _split_clauses(base)
                    if "空无一人" not in x and "只有空间和陈设" not in x
                    and "空置桌椅" not in x)
    extra = [CROWD_REF, ORDER_WORDS, CONTRAST_HARD]
    return clean_prompt(base + "，" + "，".join(extra))



# 【整部美术基调要按内外筛一遍】look_panel 是整部作品的材质词表，
# 里面既有"粗砺切石墙、石板路"（内外通用），也有"烛火壁炉"（只有屋里才有）。
# 原样注进一片森林的提示词里，出图就在林间空地上摆了烛台和壁炉。
# 这里按小句筛：室外场景丢掉只有室内才成立的那几句，反之亦然。
_INDOOR_ONLY = ("壁炉", "烛火", "烛台", "吊灯", "家具", "地毯", "帷幔", "床",
                "桌", "椅", "柜", "顶棚", "藻井", "天花", "梁下", "室内")
_OUTDOOR_ONLY = ("天空", "云层", "地平线", "街道", "山脊", "树冠", "林冠",
                 "田野", "海面", "露天", "屋顶上")


def _filter_by_place(text, indoor):
    """按内外筛掉不成立的小句。indoor 为 None 时原样返回。

    用于**场景描述**（地点模板那种整段写景的）。材质词表别用这个，
    用下面的 _strip_place_words——整句丢会把同一句里的有用材质一起丢掉。
    """
    if indoor is None or not str(text or "").strip():
        return str(text or "")
    ban = _OUTDOOR_ONLY if indoor else _INDOOR_ONLY
    parts = [x.strip() for x in re.split(r"[，、；;]", str(text)) if x.strip()]
    kept = [x for x in parts if not any(w in x for w in ban)]
    # 【没筛掉就原样返回】否则会把顿号重排成逗号，把上游那份原文改掉——
    # 下游有按原文子串匹配的地方，一改就对不上。
    if len(kept) == len(parts):
        return str(text)
    return "，".join(kept) if kept else ""


# 词级剔除用的名单：只列**实物**，不列材质。
# "石板街道"里该去掉的是"街道"（室内没有街），"石板"要留（那是地面材料）。
_STRIP_INDOOR = ("烛火壁炉", "壁炉", "烛台", "吊灯", "地毯", "帷幔", "藻井", "望板")
_STRIP_OUTDOOR = ("街道", "天空", "云层", "地平线", "树冠")


def _strip_place_words(text, indoor):
    """从**材质词表**里摘掉内外不成立的那几个实物，其余原样保留。

    【为什么不能整句丢】整部美术基调里有一句"石板路与烛火壁炉"。
    室外场景整句丢掉，连"石板路"这个地面材料一起没了；不丢的话，
    模型就在林间空地上摆一座壁炉——实测出图真的摆了。
    只摘"烛火壁炉"四个字，把"石板路"留下，两头都不亏。
    """
    if indoor is None or not str(text or "").strip():
        return str(text or "")
    out = str(text)
    for w in (_STRIP_OUTDOOR if indoor else _STRIP_INDOOR):
        # 连着前面的"与/和/、"一起去掉，免得留下"石板路与，"这种断茬
        out = re.sub(r"[与和、]?\s*" + re.escape(w), "", out)
    out = re.sub(r"[，、；;]{2,}", "，", out).strip("，、；; ")
    return out if out.strip() else str(text)

def compile_scene_prompt(scene, state=None, style="电影级写实", composition="", purpose_details="",
                         appearance_only=False):
    """编译发给 Krea2 的场景基准图提示词。

    规矩和人物提示词完全一致（用户定的）：
      · 编号分段，段内是纯粹的视觉描述，扩散模型看得懂什么就写什么
      · 不写「须符合」「必须可感知」「体系：」这类元指令——模型看不懂
      · 否定式（"不要动漫感"）不进正向词，交给默认负向词
      · 画风只出现一次，且只用选项里的那个画风
      · 场景只有场景，不出现任何人物
    """
    sc = scene or {}
    proj = sc.get("_project") or {}
    scene_style_name = str(proj.get("style") or style or "")
    scene_photoreal = is_photoreal(scene_style_name + " " + style_profile(scene_style_name))
    # 【三档，不是两档】photo / 2d / 3d 以 render_notes.json 的 kind 为准，
    # 和视频提示词用的是同一个字段（segment_api._style_kind）。
    # 只分"写实/非写实"会把 3D 归进手绘那档，场景图里就出现了"笔触和色块"
    # ——3D 是引擎渲染，材质该说贴图和粗糙度（2026-08-30 审计发现）。
    scene_kind = "photo"
    try:
        import json as _kj
        from pathlib import Path as _KP
        _krn = _kj.loads((_KP(__file__).resolve().parent.parent / "presets"
                          / "render_notes.json").read_text(encoding="utf-8"))
        _ke = _krn.get(scene_style_name)
        if isinstance(_ke, dict):
            scene_kind = str(_ke.get("kind") or "photo").strip()
    except Exception:
        pass
    if scene_kind == "photo":
        scene_photoreal = True
    try:
        from cores import style_presets as _sp
    except Exception:
        _sp = None
    try:
        from cores.prompt_hygiene import positivize as _pos
    except Exception:
        _pos = None
    try:
        from cores import project_prompt as _pp
    except Exception:
        _pp = None

    def d(fn, *a):
        try:
            t = (getattr(_sp, fn)(*a) or "").strip() if _sp else ""
        except Exception:
            t = ""
        t = t.replace("；", "，").rstrip("。，")
        # 预设名前缀（"极致超仿真生活照：…"）是给人看的标签，模型不需要
        if "：" in t[:14]:
            t = t.split("：", 1)[1].strip()
        return t

    def g(k):
        v = sc.get(k)
        if isinstance(v, (list, dict)):
            import json as _j
            v = _j.dumps(v, ensure_ascii=False)
        v = str(v or "").strip()
        return "" if v in ("自动", "None", "[]", "{}") else v

    import re

    def clean(t):
        """去掉元指令措辞，只留下可画的内容。"""
        t = str(t or "").strip()
        if not t:
            return ""
        if _pos:
            t = _pos(t)[0]
        for w in ("须符合", "必须符合", "应符合", "不得", "必须可感知", "要求", "禁止", "允许"):
            t = t.replace(w, "")
        t = re.sub(r"^(这个空间是|空间是|场景是)", "", t.strip())
        t = re.sub(r"[（(]([^）)]*)[）)]", "，" + chr(92) + "1", t)   # 括号是给人看的批注，摊平成描述
        # 不只清“人物”二字；角色名、醉汉、少女等也不能进入空场正向词。
        if _pp:
            t = _pp.strip_scene_people(t, proj.get("_character_names") or [])
        else:
            t = "，".join(x for x in re.split(r"[，、]", t) if x and "人物" not in x)
        return t.strip("，。；、 ")

    S = []

    # 1. 画面内容 —— 空间本体 + 陈设 + 识别点，一句话说清这是个什么地方
    who = [clean(g("contract_text") or g("space") or g("desc"))]
    for k in ("furniture", "regions", "fixed_landmarks", "landmarks"):
        if g(k):
            who.append(clean(g(k)))
    seen, con = set(), []
    for x in who:                       # 识别点常与家具重复，去重
        for seg in re.split(r"[，、]", x):
            seg = seg.strip()
            if seg and seg not in seen:
                seen.add(seg); con.append(seg)
    # 使用正向可见描述强调环境主体。当前 Krea2 工作流 cfg=1，负向词不起作用；
    # “没有人物”之类写法反而会激活人物概念，所以这里不出现“人”字。
    # 【空场怎么说也要分内外】"空置桌椅与安静陈设"是屋里的说法，
    # 套到一片森林上就凭空多出一堆桌椅——实测里森林场景真的画出了成排的椅子。
    _empty_word = ("空无一人的静态环境建筑摄影" if scene_photoreal
                   else "空无一人的静态环境设定图")
    _indoor_now = _pp.place_is_indoor(sc, proj) if _pp else None
    con.insert(0, _empty_word + ("，空旷的地面与自然地貌，地形与固定建筑是唯一主体"
                                 if _indoor_now is False
                                 else "，空置桌椅与安静陈设，空间结构与固定陈设是唯一主体"))
    # 空场声明并进第 1 段。它单独成段会把后面"主光源并进第3段"的逻辑打断，
    # 结果一份提示词里出现两个「3.材质陈设」。
    con.append("画面里空无一人，地面和道路上没有走动的身影，这是一张只有空间的空场图"
               if _indoor_now is False else
               "画面里空无一人，所有座位和台阶都是空的，这是一张只有空间和陈设的空场图")
    S.append("1.画面内容 " + clean_prompt("，".join(x for x in con if x)))

    # 2. 摄影构图 —— 空场基准图的固定拍法 + 真实尺度与纵深（写成看得见的距离）
    # 【为什么原来的场景图不大气】原文是「中广角建立镜头，3/4 空间角度，
    # 完整展示墙面、地面、顶棚、门窗和家具布局」——这是资料图的拍法：
    # 要把东西拍全就得退远、用平视、收紧构图，而这几件事正好把体量感和纵深压没了。
    # 而且机位高度一个字没写（人物图写死了平视，场景图漏了），默认站立视角，
    # 房间就只是房间。
    #
    # 现在补的都是「既增强体量、又不破坏信息完整性」的要素：
    #   广角 + 低机位   → 显高大，信息不丢
    #   顶棚与层高      → 把 scale 里的层高真正用起来
    #   灭点与纵深线    → 透视收敛，一眼看出进深
    #   尺度参照物      → "大"是相对的，没有门和台阶做人体尺度锚点，30米和8米画出来一样大
    #   轻度空气层次    → 大气感最强的单一手段
    # 刻意不加的：前景大面积遮挡、强暗部、浅景深——那会毁掉基准图的资料性，
    # 而这张图是要当参考图喂给后面每一格的。
    # 【室内外必须二选一并锁死】实测大殿那张图里，殿内、庭院、石阶、远山云海
    # 同时出现——室内室外糊成一团。根因是提示词从头到尾没有一句话交代这是室内
    # 还是室外，模型两边都画。这里定死，而且要写清"看不到什么"的正向替代表述。
    indoor = _pp.place_is_indoor(sc, proj) if _pp else None
    # 【只能正面写】第一版写的是"窗外只有亮光，看不到室外的天空、山峦、庭院"——
    # 这是否定式，cfg=1.0 下完全无效，实测出来的大殿正中还是露着天、
    # 侧面月洞门外还是山景。改成正面交代"四面和顶是什么做的"，
    # 给模型具体可画的东西，而不是要求它"别画"。
    if indoor is True:
        # 【不许写成中式建筑】原文写的是"格扇门、窗纸、木构藻井、望板"——
        # 那是中国古建的构件名。套到西方魔幻的地牢、科幻的舰桥、现代的公寓上，
        # 模型就照着画中式屋顶，整张图的世界观当场串味。
        # 这里只说"围合关系"，具体是石拱顶还是钢桁架还是石膏板，
        # 由地点库和「整部作品长什么样」去定。
        place_lock = ("这是一处**完全封闭的室内**空间：四面全是实心墙体，从地面一直砌到顶；"
                      "墙上的开口只有门洞和高处的窗洞，透进一片柔和的散光；"
                      "顶棚是一整片完整封闭的结构，从画面最近处一直盖到最深处的那面墙，"
                      "画面上方的每一寸都被顶棚占满，看得见的全是这个空间自己的构造")
    elif indoor is False:
        place_lock = ("这是一处**室外**空间：头顶是开阔的天空，占据画面上部；"
                      "地面一直延伸到远处的地平线，远山或建筑轮廓在天边形成剪影")
    else:
        place_lock = ("空间的围合方式全画面统一：要么四面墙加完整顶棚，要么开阔天空加地平线，"
                      "整张图只呈现其中一种")
    # 空间规模由场次表定（大/中/小）。小空间也不能拍得憋屈——
    # 用更广的焦段和更低的机位把它撑开，而不是把它画大。
    # 【没写规模就按"中"，不是"大"】原来默认"大"，于是街边一家满是油污玻璃窗的
    # 旧咖啡店拿到了"室内体量接近大型体育场、挑高十几层楼、五到八层垂直结构"
    # 这一整套巨构模板，出来的图和正文完全对不上（2026-08-30 用户实测 STORY_066）。
    # "大"是给宫殿、机库、教堂这种真正的巨构留的，必须由场次表明确指定，不能兜底。
    _sc_class = str(g("scale_class") or "").strip()
    _cls = _sc_class if _sc_class in ("大", "中", "小") else "中"
    # 【机位说法也要跟画风走】"等效20mm广角、相机架在离地1.2米"是实拍摄影的话，
    # 日漫场景图不该出现相机和焦段——同一份提示词里既要"精致赛璐璐"又要
    # "等效20mm镜头"，模型两头够（2026-08-30 用户实测 STORY_066）。
    # 画出来的画风改成描述**视角本身**，不提器材。
    if scene_kind == "3d":
        # 三维有虚拟摄影机，可以说焦段和机位高度，但不提"相机架在"这种现场用语
        _lens = {"小": "18mm 超广角建立镜头，视点约 0.9 米高、仰视，把小空间的纵深撑开",
                 "中": "24mm 广角建立镜头，视点约 1.2 米高、略微仰视，纵深清楚",
                 }.get(_cls, "20mm 广角建立镜头，视点约 1.2 米高、明显仰视，"
                             "把空间的体量和高度渲足")
    elif scene_photoreal:
        if _cls == "小":
            _lens = ("等效18mm超广角建立镜头，相机贴近地面约0.9米仰视，"
                     "把这个小空间的纵深和高度撑开，不显局促")
        elif _cls == "中":
            _lens = "等效24mm广角建立镜头，相机架在离地约1.2米的低机位略微仰视"
        else:
            _lens = ("等效20mm广角建立镜头，相机架在离地约1.2米的低机位明显仰视，"
                     "把这个空间的体量和高度拍足，画面开阔壮观有压场感")
    else:
        if _cls == "小":
            _lens = "广角建立画面，视点压得很低、略微仰看，把这个小空间的纵深和高度撑开"
        elif _cls == "中":
            _lens = "广角建立画面，视点略低于人眼、略微仰看，纵深清楚"
        else:
            _lens = ("广角建立画面，视点压低明显仰看，把这个空间的体量和高度画足，"
                     "画面开阔有压场感")
    # 【规模词和垂直结构都得分内外】SCALE_WORDS 和 VERTICAL_STRUCT 是给室内写的
    # （"室内体量接近大型体育场""回廊、挑台、栏杆、斗拱、藻井"），
    # 原样套到一片森林上，模型就真的在林子里盖起了带回廊的大厅——
    # 那张"室内柱廊配露天树冠"的图就是这么来的。
    if indoor is False:
        _scale_word = {
            "大": "视野极开阔，纵深一眼望不到底，周围的地形或建筑高得需要抬头才看得见顶",
            "中": "场地开阔，纵深清楚，周围的树木或建筑明显高过人好几倍",
            "小": "场地不大，但用超广角和低机位把纵深和高度撑开，不显局促",
        }.get(_cls, "视野极开阔，纵深一眼望不到底")
        _vert = ("竖向层次靠地形和植被拉开：近处的岩体、树干和坡坎，"
                 "中景的成片林冠或建筑轮廓，远处逐层退淡的山脊，"
                 "最高的那一层向上超出画面上沿")
    else:
        _scale_word = SCALE_WORDS.get(_cls, SCALE_WORDS["大"])
        if isinstance(_scale_word, tuple):
            _scale_word = _scale_word[0]
        # 【垂直结构只给"大"】"五到八层以上的垂直关系、连续的走廊挑台栏杆"
        # 是巨构才有的。一间街边咖啡店套上去，模型就在店里盖起了多层回廊
        # （2026-08-30 用户实测 STORY_066）。中小空间只写它自己的高度层次。
        _vert = (VERTICAL_STRUCT if _cls == "大" else
                 "竖向层次靠这个空间自己的构造拉开：地面、家具、墙面陈设、"
                 "顶部灯具或结构各占一层，近处的物件比远处明显大")
    comp = ["16:9 宽幅环境基准图，" + _lens + "，3/4 空间角度",
            _scale_word, place_lock]
    if _cls != "小":
        comp.append(_vert)
    # 【框景也要按规模】"极粗的立柱"是巨构才有的。一家街边咖啡店前面立两根
    # 巨柱，整张图就变味了（2026-08-30 用户实测）。中小空间用门框、货架、
    # 窗框这种它自己有的东西做框景。
    if _cls == "大":
        comp.append(FOREGROUND_FRAME_OUT if indoor is False else FOREGROUND_FRAME)
    else:
        comp.append("前景左右各放一样这个空间本来就有的东西（门框、窗框、货架、"
                    "栏杆、树干）作为视觉框架，只占画面两侧边缘，中间完全让开")
    if g("scale"):
        comp.append(clean(g("scale")))
    if g("depth"):
        comp.append(clean(g("depth")))
    # 「柱距宽、跨度大，站在里面显得人很小」同样只适用于巨构
    comp += ([(("顶棚和承重结构在画面上方完整可见，画面上部留出足够高度体现层高，"
                "空间开阔挑高，柱距宽、跨度大，站在里面显得人很小") if _cls == "大" else
               "顶部结构在画面上方可见，画面上部留出层高，空间比例真实可信")]
             if indoor is not False else
             ["天空占据画面上部，远景层层退开，空间开阔壮观"]) + [
             ("地面和顶棚的透视线向画面深处明显收敛到一个清楚的灭点，纵深一眼可读"
              if indoor is not False else
              "地面、道路或岸线向画面深处明显收敛到一个清楚的灭点，纵深一眼可读"),
             # 【尺度参照物不能写死现代物件】原文室外那条写的是"树木、车辆、路灯"，
             # 车和路灯只有现代世界才有。西方魔幻的林间遗迹里被塞进两辆汽车和
             # 一排路灯——实测出图就是这样。改成各个世界都成立的东西：
             # 门洞、台阶、栏杆的尺寸是由人体决定的，哪个时代都一样。
             ("画面里有门洞、台阶、栏杆或家具按真实人体尺寸出现，作为衡量空间大小的参照"
              if indoor is not False else
              "画面里有树木、台阶、门洞、栏杆或建筑门窗按真实人体尺寸出现，"
              "作为衡量空间大小的参照"),
             "空气有轻微的通透层次，远处比近处略淡，光在空间里能看出方向和体积",
             "近景、中景、远景层次分明，全画面清晰没有虚化"]
    if composition:
        comp.append(clean(composition))
    S.append("2.摄影构图 " + "，".join(x for x in comp if x))

    # 3. 材质陈设 —— 地面墙面门窗的具体材料，去掉「X是Y」的字段标签
    mat = []
    for k in ("materials", "ground", "walls", "door", "window"):
        if g(k):
            mat.append(clean(g(k)))
    # 【谁先谁后：用户写的永远压过预设】
    # 人物那边早就有这个规矩了——服装写够厚就不再塞世界的默认穿着。
    # 场景这边一直没有：世界给"粗砺切石墙"，用户写"暖白与胡桃木"，两句并排送进去，
    # 模型只好各画一半。现在按同一套规矩来：用户写了建材，世界的建材就不再补。
    routed_env = clean(_pp.routed_user_text(proj, "scene")) if _pp else ""
    routed_env = _strip_place_words(routed_env, indoor)
    # 【space/contract_text 也算"用户写了"】自动生成的场景卡不填 materials，
    # 而是把"柏油路面、霓虹招牌、油污玻璃窗、木质吧台"写进 space；
    # 判断"够不够厚"时不看这两个字段，就会再叠一层世界材质包——
    # 一家旧咖啡店被套上"合金骨架、模块舱门、设备接口"（2026-08-30 用户实测）。
    own = "，".join(mat) or clean(g("space") or g("contract_text"))
    user_env = "；".join(x for x in (own, routed_env) if x)
    MATERIAL_WORDS = ("墙", "地面", "地板", "屋顶", "顶棚", "门", "窗", "石", "木",
                      "砖", "瓦", "金属", "钢", "玻璃", "混凝土", "泥", "布", "漆",
                      "材质", "材料")
    # 够厚 = 说得清是什么做的：有一定长度，且至少点到三种材料/构件。
    user_env_is_enough = (len(user_env) >= 30
                          and sum(1 for w in MATERIAL_WORDS if w in user_env) >= 3)
    if routed_env and routed_env not in mat:
        mat.append(routed_env)
    if not user_env_is_enough:
        # 【world_kit 不筛】它是这个世界的**材质词表**（"砖石、拱券、木梁、
        # 灰泥、石板街道"），不是某个场景的描述。按小句筛会把"石板街道"整句
        # 丢掉，连"石板"这个材质一起丢——室内场景反而少了地面材料。
        # 要筛的是 look_panel（整部美术散文，里面有"烛火壁炉"这种只有屋里成立的）
        # 和 location_kit（地点模板，本身就是一段场景描述）。
        env2 = _strip_place_words(_pp.world_kit(proj, "scene") if _pp else "", indoor)
        if env2 and env2 not in mat:
            mat.append(env2)
    # 地点专用包（酒馆/城堡/教堂这种）只在用户没写这个地点长什么样时才补
    location_kit = _filter_by_place(_pp.scene_type_kit(sc, proj) if _pp else "", indoor)
    if location_kit and location_kit not in mat and not user_env_is_enough:
        mat.append(location_kit)
    # 【材质写法要跟画风走】MATERIAL_REAL 是实拍语汇（氧化磨损、漆面裂纹、
    # 反光按材质规律）。日漫画风的场景图套上这段，就成了"精致赛璐璐"和
    # "真实使用痕迹"同一份提示词里打架（2026-08-30 用户实测 STORY_066）。
    mat.append(MATERIAL_REAL if scene_kind == "photo" else
               MATERIAL_CG if scene_kind == "3d" else MATERIAL_DRAWN)
    if mat:
        S.append("3.材质陈设 " + clean_prompt("，".join(_keep_more_specific([x for x in mat if x]))))
    # 【cfg=1.0 下否定词无效】"不要出现人物"这种写法完全没作用，实测赛博街区那张
    # 摊位后面照样坐了两个人。基准图带人会污染下游每一格，只能用正向描述顶回去。


    if appearance_only:
        # 纯外观：1~3 段。第4段的光影质感交给画风指令词。
        # 但主光源方向和色温是**场景设计信息**不是画风，所以它留在这里，
        # 作为第3段的收尾——后面每一格的人物打光都要照着它来。
        lit0 = clean(g("light") or g("main_light") or g("base_light_color"))
        if lit0:
            # 主光源是**场景设计信息**不是画风：后面每一格的人物打光都要照着它来。
            # 早先给它单独编了个 3.5 段，Qwen 每次都把这个不三不四的编号折进第4段，
            # 逐字比对因此假失败。并进第3段收尾就没这问题了。
            if S and S[-1].startswith("3."):
                S[-1] = S[-1].rstrip("。，") + "，主光源是" + lit0
            else:
                S.append("3.材质陈设 主光源是" + lit0)
        return "。".join(x.strip().rstrip("。；;，") for x in S if str(x).strip()) + "。"

    # 4. 光影质感 —— 光源方向色温 + 当前状态；画风自带光线时不再补棚拍描述
    sd = d("style_description", str(proj.get("style") or "").strip()) or style_profile(style)
    # 人物剧照常用的“故事感、张力”会把静态场景变成正在发生的事件；场景图只保留
    # 所选画风的渲染、调色、材质和光影特征。
    if sd:
        sd = "，".join(x.strip() for x in re.split(r"[，；;]", sd)
                      if x.strip() and not any(w in x for w in ("故事感", "有张力", "戏剧冲突")))
    lit = []
    light = g("light") or g("main_light") or g("base_light_color")
    if light:
        lit.append(clean(light))
    if state:
        cur = [clean(str(state.get(k))) for k in ("time", "weather", "damage",
                                                  "temporary_lighting_state") if state.get(k)]
        lit += [x for x in cur if x]
    # 合并后的美术要求已按内容分流到材质段；人物服装要求不会再污染空场光影。
    if purpose_details:
        lit.append(clean(purpose_details))
    # 场景基准图同样接上这个画风自己的渲染要点（它写清了这个画风该怎么打光、
    # 怎么调色、各种材质怎么受光）。同样过一遍 sheet_style 剔掉拍摄感。
    scene_note = sheet_note(render_note(str(proj.get("style") or "").strip(), "scene"))
    if scene_note:
        lit.append(scene_note)
    else:
        lit.append("材质表面纹理清晰真实，光线在不同材质上的反射有区别" if scene_photoreal
                   else "材质层次通过清晰轮廓、分层上色与统一光影区分")
    if sd:
        lit.append(sd)
    lit.append(VOLUMETRIC if scene_kind == "photo" else
               VOLUMETRIC_CG if scene_kind == "3d" else VOLUMETRIC_DRAWN)
    if _cls == "大":
        lit.append(SCALE_HARD_OUT if indoor is False else SCALE_HARD)
    lit.append(SCALE_REMINDER_OUT if indoor is False else SCALE_REMINDER)
    S.append("4.光影质感 " + clean_prompt("，".join(x for x in lit if x)))

    text = "。".join(x.strip().rstrip("。；;，") for x in S if str(x).strip()) + "。"
    text = re.sub(r"，(?=，)", "", text)      # 摊平括号会留下空档，收掉连续逗号
    return text.replace("。。", "。").replace("，。", "。")

def validate_continuity(story, episode, state_snapshot):
    errors, warnings = [], []
    if not episode or not state_snapshot:
        return {"ok": False, "errors": ["缺少 episode 或 state"]}
    ledger = {e["event_id"] for e in episode["events"]}
    for u in episode["units"]:
        for eid in u.get("must_include_event_ids", []):
            if eid not in ledger:
                errors.append("Unit %s 引用无来源事件 %s" % (u["unit_id"], eid))
    resolved = set(state_snapshot.get("plot_state", {}).get("resolved_events", []))
    if not resolved:
        warnings.append("状态快照没有任何已发生事件")
    return {"ok": not errors, "errors": errors, "warnings": warnings}

# ---- Manifest（Generation Manifest 基础） ----
def build_manifest(output_id, story_id, production_type, source_narrative_ids=None,
                   event_ids=None, state_snapshot_ids=None, character_contract_versions=None,
                   wardrobe_versions=None, scene_versions=None, style_profile_name="",
                   director_version="", prompt="", model="", model_mode="",
                   reference_pack_ids=None, parameters=None, output_path=""):
    manifest = {
        "manifest_id": store.seq_id("MANIFEST", len(list((store.DATA / "manifests").glob("*.json"))) + 1),
        "output_id": output_id, "story_id": story_id, "production_type": production_type,
        "source_narrative_ids": source_narrative_ids or [],
        "event_ids": event_ids or [], "state_snapshot_ids": state_snapshot_ids or [],
        "character_contract_versions": character_contract_versions or {},
        "wardrobe_versions": wardrobe_versions or {}, "scene_versions": scene_versions or {},
        "style_profile": style_profile_name, "director_version": director_version,
        "prompt": prompt, "model": model, "model_mode": model_mode,
        "reference_pack_ids": reference_pack_ids or [], "parameters": parameters or {},
        "created_at": time.time(), "output_path": output_path,
    }
    store.save_json(store.DATA / "manifests" / (manifest["manifest_id"] + ".json"), manifest)
    return manifest

# ================= v4.1 质量资产落地：Krea2 验证策略 + H3 官方提示词指南 + 导演方法 =================
KREA_RULES = [
    "提示词只用正向词，禁止使用“不要/没有/无”等否定（扩散模型会把否定词当成要画的）。",
    "人数先声明：本画面出现 N 人；未出场人物只进禁止名单，不进正文。",
    "人物固定锚点（Identity Anchor）完整前置，且正文与尾部重复锚定，禁止不同义改写。",
    "关键事件/动作/道具作为硬事实编译进提示词，不得交给模型自由发挥。",
    "场景固定结构（Scene Contract）逐页复用，临时天气/破坏进入 State，不改 Master。",
    "每张图保存生成清单（模型/种子/尺寸/提示词/工作流版本），可重现。",
    "画风与人物设计分离：Style Profile 不重新定义脸型/身材/身份/服装类型。",
]

DIRECTOR_SYSTEM = """你是导演兼编剧。所有故事/剧本/分镜必须先解决叙事，再谈镜头。
固定流程：Narrative Unit（故事变化）→ 场次目的 → 观众知识差 → 人物目标/阻力 → 权力/情绪变化 → 表演（目标+潜台词+可见动作+视线+姿态+停顿）→ Blocking（位置/距离/移动/停顿/视线/空间）→ 镜头（景别/机位/运动：类型+幅度+速度）→ 剪辑点（为什么现在切）→ 声音（环境音/关键声/对白距离/音乐进入点）。
规则：
1. 换拍法只允许改表演/站位/摄影/剪辑/节奏/声音，不得改 Event Ledger 的剧情事件。
2. 每镜头必须承担新信息/人物反应/空间关系/情绪/节奏/伏笔/转场之一。
3. 镜头语言用具体可执行描述，不用抽象形容词堆砌。
4. 分镜的每个镜头包含：画面内容、人物动作与视线、景别、机位与运镜（运动类型+幅度+速度）、剪辑点、声音。"""

H3_REF2VA_SYSTEM = """You are a professional video prompt writer following the official MiniMax H3 full-reference (Ref2VA) prompt format.
Rewrite the given sequence into EXACTLY six sections, in this order, written in English:
1. subject_definitions: define <Subject N>/<Picture N>/<Audio N> labels and what each reference preserves (character identity, costume, scene, style, continuity).
2. summary: one paragraph starting with [reference generation], summarizing target video and reference roles.
3. retention_analysis: one line per label using markers fully_preserved / partially_preserved / attribute_transfer / weak_reference.
4. detailed_description: 350-500 English words, shot by shot in playback order. Style opening sentence before [Shot 1]. Camera motion written as natural English with motion type+amplitude+speed. Stable speaker IDs (S1). Dialogue inside <d>[Chinese] ...</d> preserving original words. Later shots use [Shot N] At MM:SS.mmm.
5. overall_soundscape: 1-4 English sentences of ambience and physical sounds.
6. non_diegetic_music: 1-3 English sentences of instrumentation/tempo/dynamics, or N/A.
Output only the six sections, no extra commentary."""

H3_T2VA_SYSTEM = """You are a professional video prompt writer following the official MiniMax H3 text-to-video (T2VA) prompt format.
Write a complete audiovisual timeline with exactly three fields:
integrated_multimodal_description: [Shot 1] style opening (Cinematic/live-action...), composition, subjects, actions, camera motion (type+amplitude+speed), dialogue with speaker IDs and <d>[Chinese]...</d>, later shots at [Shot N] At MM:SS.mmm.
overall_soundscape: 1-4 English sentences of ambient and physical sounds.
non_diegetic_music: 1-3 English sentences of instrumentation/tempo/dynamics, or N/A.
Output only the three fields."""

# ================= v4.2 Sensual Tension Budget（节奏控制，不是露骨内容模板） =================
SENSUAL_CUES = ("触碰", "指尖", "领口", "锁骨", "发丝", "裙摆", "体温", "欲望", "诱惑",
                "暧昧", "湿润", "心跳", "抚摸", "腰肢", "贴面", "解开", "爱抚")
INTENSE_CUES = ("吻", "拥抱", "贴身", "解开", "褪去", "卧倒", "交叠")
SHOT_KEYS = ("narrative_purpose", "blocking", "performance", "action", "eye_line",
             "visual_focus", "dialogue", "sound", "framing")

def sensual_shot_analysis(shots):
    """逐镜统计性感/高强度线索（启发式：同一镜头内出现的 cue 文本）。"""
    out = []
    for sh in shots:
        text = " ".join(str(sh.get(k) or "") for k in SHOT_KEYS)
        cues = [c for c in SENSUAL_CUES if c in text]
        intense = [c for c in INTENSE_CUES if c in text]
        out.append({"shot": str(sh.get("shot_id") or ""), "sensual": bool(cues),
                    "cues": cues, "intense": intense})
    return out

def validate_sensual_tension_budget(story_script, shots, director_intent):
    """V4.2 §6.2 系统检查：低频/不重复/有场景理由/有铺垫/推进剧情。"""
    errors, warnings = [], []
    analysis = sensual_shot_analysis(shots or [])
    sensual_idx = [i for i, a in enumerate(analysis) if a["sensual"]]
    n = max(1, len(analysis))
    ratio = len(sensual_idx) / n
    if ratio > 0.4:
        errors.append("性感镜头占比 %.0f%% 超过 40%% 预算" % (ratio * 100))
    # 同一种性感表现不得连续反复使用：连续镜头里出现相同 cue 才算重复
    for i in range(len(sensual_idx) - 1):
        a1, a2 = analysis[sensual_idx[i]], analysis[sensual_idx[i + 1]]
        if sensual_idx[i + 1] - sensual_idx[i] == 1 and set(a1["cues"]) & set(a2["cues"]):
            errors.append("同一种性感暗示连续使用：%s 与 %s 都出现 %s"
                          % (a1["shot"], a2["shot"], "、".join(sorted(set(a1["cues"]) & set(a2["cues"])))))
    # 连续性感镜头硬上限：最多 3 个（关系升级小高潮），之后必须有叙事镜头
    run = 1
    for i in range(1, len(analysis)):
        if analysis[i]["sensual"] and analysis[i - 1]["sensual"]:
            run += 1
            if run > 3:
                errors.append("连续性感镜头超过 3 个（%s 起），中间缺少叙事镜头"
                              % analysis[i - run + 1]["shot"])
        else:
            run = 1
    intent = director_intent or {}
    if not any(intent.get(k) for k in ("who_wants", "power_shift", "emotion_from_to")):
        errors.append("导演意图缺少吸引关系分析（who_wants / power_shift / emotion_from_to 至少一项）")
    for i, a in enumerate(analysis):
        if a["intense"]:
            if i < 2:
                errors.append("高强度性感镜头过早出现（第 %d 镜 %s，缺少铺垫）" % (i + 1, a["shot"]))
            if not any(x["sensual"] for x in analysis[:i]):
                warnings.append("高强度镜头 %s 之前没有低强度性感铺垫" % a["shot"])
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "shots": len(analysis), "sensual_ratio": round(ratio, 2),
            "per_shot": analysis}

def check_sensual_tension_benchmark(bdir=None):
    """对 08_sensual_tension Benchmark 产物执行预算检查并落盘。"""
    import json as _json
    from pathlib import Path as _Path
    bdir = _Path(bdir) if bdir else (_Path(__file__).resolve().parent.parent /
                                     "reports" / "Director_Benchmarks" / "08_sensual_tension")
    script = (bdir / "01_故事剧本.md").read_text(encoding="utf-8") if (bdir / "01_故事剧本.md").exists() else ""
    shots, intent = [], {}
    if (bdir / "03_分镜表.md").exists():
        raw = (bdir / "03_分镜表.md").read_text(encoding="utf-8")
        a, b = raw.find("["), raw.rfind("]")
        if a >= 0 and b > a:
            try:
                shots = _json.loads(raw[a:b + 1])
            except Exception:
                shots = []
    if (bdir / "02_导演方案.md").exists():
        raw = (bdir / "02_导演方案.md").read_text(encoding="utf-8")
        a, b = raw.find("{"), raw.rfind("}")
        if a >= 0 and b > a:
            try:
                intent = _json.loads(raw[a:b + 1])
            except Exception:
                intent = {}
    result = validate_sensual_tension_budget(script, shots, intent)
    (bdir / "05_性感张力预算检查.json").write_text(_json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
