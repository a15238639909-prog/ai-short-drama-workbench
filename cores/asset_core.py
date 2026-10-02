# -*- coding: utf-8 -*-
"""asset_core.py — V4.1 Asset Core：CharacterContract / Wardrobe / SceneContract / ApprovedVisual。"""
import time, uuid
from . import store
from . import quality_core

def assets_path(story_id, kind):
    d = store.DATA / "assets" / story_id / kind
    d.mkdir(parents=True, exist_ok=True)
    return d

def list_assets(story_id, kind):
    out = []
    for p in assets_path(story_id, kind).glob("*.json"):
        d = store.load_json(p, None)
        if d:
            if kind == "characters":
                d = normalize_character(d)
            out.append(d)
    return sorted(out, key=lambda x: x.get("created", 0))

_ID_FIELD = {"characters": "character_id", "scenes": "scene_id",
             "wardrobes": "wardrobe_id", "visuals": "visual_id"}

def get_asset(story_id, kind, aid):
    d = store.load_json(assets_path(story_id, kind) / (aid + ".json"), None)
    return normalize_character(d) if d and kind == "characters" else d

def save_asset(story_id, kind, data):
    fid = _ID_FIELD.get(kind, kind + "_id")
    payload = normalize_character(data, for_storage=True) if kind == "characters" else data
    store.save_json(assets_path(story_id, kind) / (payload[fid] + ".json"), payload)
    return data


def normalize_character(data, for_storage=False):
    """迁移旧人物字段；读取兼容旧名，写盘只保留新名。"""
    if not data:
        return data
    c = dict(data)
    behavior = str(c.get("behavior_anchor") or c.get("personality") or "")
    appearance = str(c.get("appearance_details") or "").strip()
    if not appearance:
        appearance = "；".join(x for x in (str(c.get("look") or "").strip(),
                                            str(c.get("other") or "").strip()) if x)
    try:
        from . import style_presets
        c["build"] = style_presets.normalize_body_option(c.get("sex"), c.get("build"))
        c["face_type"] = style_presets.normalize_face_option(c.get("sex"), c.get("face_type"))       # P354：旧长相标签 → 骨相档
        c["clothing_requirement"] = style_presets.normalize_cloth_option(c.get("clothing_requirement"))
    except Exception:
        pass
    c["behavior_anchor"] = behavior
    c["appearance_details"] = appearance
    if for_storage:
        for old in ("personality", "look", "other"):
            c.pop(old, None)
    else:
        # 只作为旧模块的运行时只读别名，不再落盘。
        c["personality"] = behavior
        c["look"] = appearance
        c["other"] = appearance
    return c


def canonical_character_patch(data):
    """把旧接口提交的人物字段转成新字段，只转换请求里真实出现的键。"""
    p = dict(data or {})
    if "behavior_anchor" not in p and "personality" in p:
        p["behavior_anchor"] = p.get("personality")
    if "appearance_details" not in p and ("look" in p or "other" in p):
        p["appearance_details"] = "；".join(
            x for x in (str(p.get("look") or "").strip(), str(p.get("other") or "").strip()) if x)
    try:
        from . import style_presets
        if "build" in p:
            p["build"] = style_presets.normalize_body_option(p.get("sex"), p.get("build"))
        if "clothing_requirement" in p:
            p["clothing_requirement"] = style_presets.normalize_cloth_option(p.get("clothing_requirement"))
    except Exception:
        pass
    return p


def refresh_character_anchors(character):
    """用户修改结构化字段后，同步重建冻结外观与神态锚点。"""
    c = character
    c["identity_anchor"] = "；".join(x for x in [
        "年龄性别：" + str(c.get("age") or "") + str(c.get("sex") or ""),
        "人物类型：" + str(c.get("char_type") or ""),
        "脸型：" + str(c.get("face_type") or ""),
        "外貌细节：" + str(c.get("appearance_details") or ""),
        "身材：" + str(c.get("build") or ""),
        "发型：" + str(c.get("hair") or ""),
    ] if x.split("：", 1)[-1].strip())
    c["behavior_anchor"] = str(c.get("behavior_anchor") or "")
    return c

def create_character(story_id, fields):
    cid = "CHAR_" + re_upper(fields.get("name") or "PERSON")
    existing = get_asset(story_id, "characters", cid)
    n = 1
    while existing:
        cid = "CHAR_" + re_upper(fields.get("name") or "PERSON") + "_" + str(n)
        existing = get_asset(story_id, "characters", cid)
        n += 1
    appearance = str(fields.get("appearance_details") or fields.get("look") or fields.get("other") or "")
    behavior = str(fields.get("behavior_anchor") or fields.get("personality") or "")
    identity = "；".join(x for x in [
        "年龄性别：" + str(fields.get("age") or "") + str(fields.get("sex") or ""),
        "人物类型：" + str(fields.get("char_type") or ""),
        "脸型：" + str(fields.get("face_type") or ""),
        "外貌细节：" + appearance,
        "身材：" + str(fields.get("build") or ""),
        "发型：" + str(fields.get("hair") or ""),
    ] if x and x.split("：", 1)[-1].strip())
    char = {
        "character_id": cid, "story_id": story_id, "name": fields.get("name") or "未命名",
        "age": fields.get("age") or "", "sex": fields.get("sex") or "",
        "height": fields.get("height") or "",                                 # P323①：身高（可改；进人设图硬事实）
        # 只给人物设定图用，不写入 identity_anchor，也不进入故事/视频提示词。
        "sheet_body_ratio": fields.get("sheet_body_ratio") or "自动",
        "char_type": fields.get("char_type") or "",
        "face_type": fields.get("face_type") or "",
        "build": fields.get("build") or "", "hair": fields.get("hair") or "",
        "hair_preset": fields.get("hair_preset") or "自动",
        "voice": fields.get("voice") or "",
        "personality": fields.get("personality") or behavior,
        "appearance_details": appearance,
        "clothing": fields.get("clothing") or "", "clothing_requirement": fields.get("clothing_requirement") or "",
        "clothing_from_brief": bool(fields.get("clothing_from_brief")),      # P459：口述指定的服装，库和尺度不改
        "outfit_preset": fields.get("outfit_preset") or "自动",
        "first_episode": int(fields.get("first_episode") or 1),
        "face_features": fields.get("face_features") or {},
        "identity_anchor": identity,
        "behavior_anchor": behavior,
        "default_wardrobe_id": "", "world_compatibility": "",
        "frozen": False, "version": 1, "visuals": [], "created": time.time(), "updated": time.time(),
    }
    save_asset(story_id, "characters", char)
    from . import story_core
    st = story_core.get_story(story_id)
    if st and cid not in (st.get("character_ids") or []):
        st.setdefault("character_ids", []).append(cid)
        story_core.save_story(st)
    return char

def create_wardrobe(story_id, character_id, fields):
    wid = "WARDROBE_" + re_upper(character_id.replace("CHAR_", "")) + "_" + re_upper(fields.get("slot") or "DEFAULT") + "_V1"
    w = {
        "wardrobe_id": wid, "story_id": story_id, "character_id": character_id,
        "slot": fields.get("slot") or "default", "prompt_text": fields.get("prompt_text") or "",
        "top": fields.get("top") or "", "bottom": fields.get("bottom") or "",
        "outer": fields.get("outer") or "", "shoes": fields.get("shoes") or "",
        "material": fields.get("material") or "", "colors": fields.get("colors") or "",
        "fixed_decor": fields.get("fixed_decor") or "", "created": time.time(),
    }
    save_asset(story_id, "wardrobes", w)
    return w

def _space_is_prose(space):
    """「空间」栏不是有效的空间描述：不以 室内/室外 开头（抄的正文、清单里的碎片「远处有树林」）或不足 15 字（P415/P421）。"""
    t = str(space or "").strip()
    return bool(t) and (not re.match(r"^(室内|室外)", t) or len(re.sub(r"[^一-龥]", "", t)) < 10)


def create_scene(story_id, fields):
    # P415：同名场景卡只留一张——已有就把新给的栏补进去（旧卡的「空间」是抄的正文段落就换成新的），返回旧卡
    _nm = str(fields.get("name") or "").strip()
    _same = next((c for c in (list_assets(story_id, "scenes") or []) if str(c.get("name") or "").strip() == _nm), None) if _nm else None
    if _same:
        _chg = False
        for _k in ("space", "light", "ground", "furniture", "landmarks", "contract_text", "scale", "depth", "walls", "door", "window", "layout"):
            _v = str(fields.get(_k) or "").strip()
            _old = str(_same.get(_k) or "").strip()
            if _v and (not _old or (_k in ("space", "contract_text") and _space_is_prose(_old) and not _space_is_prose(_v))):
                _same[_k] = _v
                _chg = True
        if _chg:
            _same["updated"] = time.time()
            save_asset(story_id, "scenes", _same)
        return _same
    sid = "SCENE_" + re_upper(fields.get("name") or "PLACE")
    existing = get_asset(story_id, "scenes", sid)
    n = 1
    while existing:
        sid = "SCENE_" + re_upper(fields.get("name") or "PLACE") + "_" + str(n)
        existing = get_asset(story_id, "scenes", sid)
        n += 1
    sc = {
        "scene_id": sid, "story_id": story_id, "name": fields.get("name") or "未命名场景",
        "contract_text": fields.get("contract_text") or fields.get("space") or "",
        "regions": fields.get("regions") or "", "materials": fields.get("materials") or "",
        "fixed_landmarks": fields.get("fixed_landmarks") or "", "base_light_color": fields.get("base_light_color") or "",
        # 一键生成给的是这几个字段，以前全被丢掉，导致场景提示词里一句描述都没有
        "space": fields.get("space") or "", "scale": fields.get("scale") or "",
        "depth": fields.get("depth") or "", "light": fields.get("light") or "",
        "ground": fields.get("ground") or "", "walls": fields.get("walls") or "",
        "door": fields.get("door") or "", "window": fields.get("window") or "",
        "furniture": fields.get("furniture") or "", "landmarks": fields.get("landmarks") or "",
        # 绝对方位表（街东侧／街西侧…）。方向不能靠每段现编，得有一份钉死的存在卡上。
        "layout": fields.get("layout") or "",
        "frozen": False, "version": 1, "visuals": [], "created": time.time(), "updated": time.time(),
    }
    for _k in ("edited_by_user", "user_made", "episode"):          # P316b：用户点「新建场景卡」建的卡带上这几个标记（归并时不删）
        if fields.get(_k) not in (None, ""):
            sc[_k] = fields.get(_k)
    save_asset(story_id, "scenes", sc)
    from . import story_core
    st = story_core.get_story(story_id)
    if st and sid not in (st.get("scene_ids") or []):
        st.setdefault("scene_ids", []).append(sid)
        story_core.save_story(st)
    return sc

def add_approved_visual(story_id, kind, owner_id, visual_version, path, style_profile="电影级写实", prompt=""):
    vid = "ASSET_VISUAL_" + re_upper(owner_id) + "_V" + str(visual_version)
    v = {
        "visual_id": vid, "story_id": story_id, "kind": kind, "owner_id": owner_id,
        "visual_version": visual_version, "status": "candidate", "path": path,
        "style_profile": style_profile, "prompt": prompt, "created": time.time(),
    }
    d = assets_path(story_id, "visuals")
    store.save_json(d / (vid + ".json"), v)
    return v

def adopt(story_id, asset_id, version=None):
    """唯一采用入口：同一实体（owner_id+kind）任一时刻只能有一个 active 版本。

    目标版本 → adopted；同实体其他已 adopted 版本 → superseded；candidate/draft 不变。
    采用后冻结对应人物/场景 contract（沿用旧冻结逻辑）。
    """
    v = get_asset(story_id, "visuals", asset_id)
    if not v:
        raise ValueError("视觉资产不存在")
    if version is not None:
        for cand in list_assets(story_id, "visuals"):
            if (cand.get("owner_id") == v.get("owner_id") and cand.get("kind") == v.get("kind")
                    and str(cand.get("visual_version")) == str(version)):
                v = cand
                asset_id = cand["visual_id"]
                break
        else:
            raise ValueError("版本 %s 不存在" % version)
    owner, kind = v.get("owner_id"), v.get("kind")
    for other in list_assets(story_id, "visuals"):
        if (other.get("owner_id") == owner and other.get("kind") == kind
                and other.get("visual_id") != asset_id and other.get("status") == "adopted"):
            other["status"] = "superseded"
            other["updated"] = time.time()
            store.save_json(assets_path(story_id, "visuals") / (other["visual_id"] + ".json"), other)
    v["status"] = "adopted"
    v["updated"] = time.time()
    store.save_json(assets_path(story_id, "visuals") / (asset_id + ".json"), v)
    if str(kind or "").startswith("character"):
        c = get_asset(story_id, "characters", owner)
        if c:
            c["frozen"] = True
            c["updated"] = time.time()
            save_asset(story_id, "characters", c)
    if kind == "scene_master":
        sc = get_asset(story_id, "scenes", owner)
        if sc:
            sc["frozen"] = True
            sc["updated"] = time.time()
            save_asset(story_id, "scenes", sc)
    return v

def adopt_visual(story_id, visual_id):
    """兼容入口：等价于 adopt(story_id, visual_id)，避免旧路由改调用方。"""
    return adopt(story_id, visual_id)

def generate_character_master(story_id, character_id, style_profile="电影级写实", quality="formal",
                              custom_prompt=None):
    from models import krea_client
    c = get_asset(story_id, "characters", character_id)
    if not c:
        raise ValueError("人物不存在")
    wardrobe = None
    if c.get("default_wardrobe_id"):
        wardrobe = get_asset(story_id, "wardrobes", c["default_wardrobe_id"])
    anchor_text = " ".join(x for x in [str(c.get("identity_anchor") or ""),
                                       str(c.get("build") or ""), str(c.get("clothing") or "")] if x)
    laterality = ""
    if "左臂" in anchor_text:
        laterality = ("；断肢方向（关键）：正面全身图中，人物左臂（观众视角画面右侧）肘下缺失、袖管空荡，"
                      "右臂（观众视角画面左侧）完整；侧面与背面图保持同一侧断肢")
    elif "右臂" in anchor_text:
        laterality = ("；断肢方向（关键）：正面全身图中，人物右臂（观众视角画面左侧）肘下缺失、袖管空荡，"
                      "左臂（观众视角画面右侧）完整；侧面与背面图保持同一侧断肢")
    # 把项目设定（世界类型/视觉强度）带给编译器，让选项背后的定义真正生效
    try:
        from cores import story_core as _sc, project_settings as _pset
        c = dict(c)
        _story = _sc.get_story(story_id) or {}
        c["_project"] = _pset.get(_story) or {}
        if style_profile == "电影级写实" and c["_project"].get("style"):
            style_profile = c["_project"]["style"]
    except Exception:
        pass
    # 四视图、构图、背景、统一光照这些现在全由编译器的 2.摄影构图 段负责，
    # 这里再追加一份只会重复，而且旧文案写的是"浅灰背景"，和编译器的"纯白背景"直接打架。
    # 断肢方向是这个人物独有的、编译器拿不到的信息，只留它。
    # 【不再用 _thicken_outfit 补服装】那是旧流程（代码套服装模板把描述补厚）的
    # 补丁。新流程 Qwen 会自己把"黑色修身长裤"补全，不需要代码套模板。
    # 而且它正是"男主穿裙子"的元凶：给男主分了 outfit_preset=夏季校园装，
    # 模板里的"过膝百褶裙"（女生的）被无视性别地套到了男主身上。
    # 现在提示词统一由 authoring.write_char_sheet 写，性别和服装都由卡决定。
    # 用户在页面上改过提示词就直接用他那份——他改的就是最终稿。
    if str(custom_prompt or "").strip():
        prompt = str(custom_prompt).strip()
    else:
        # 【新流程：Qwen 写，不再代码拼】把人物卡压成一句话，交给 prompt_writer。
        # Qwen 一次输出两段：出图提示词 + 通用人物外观。外观存回卡（look_full），
        # 场景图引用这个人物时就用它，人物才不会飘。
        # 【唯一入口】提示词怎么写、否定句怎么剥、look_full 怎么存，
        # 全在 authoring.write_char_sheet 里。这里只负责把断肢方向这条
        # 人物独有的硬信息传进去——两条出图链必须用同一份提示词。
        from cores import authoring as _au
        prompt = _au.write_char_sheet(story_id, c, c.get("_project") or {},
                                      extra_rules=laterality.lstrip("；"))
   

    # Turbo 检查点 + 两段式：7 步 / cfg 1.0。尺寸是**出底**，成品是它的 1.5 倍。
    # 正式 1920×1080 → 2880×1616：四视图并排时每格才有 700+ 像素承载脸部细节。
    steps = 5 if quality == "preview" else 7
    wh = (1280, 720) if quality == "preview" else (1920, 1080)
    res = krea_client.generate(prompt, width=wh[0], height=wh[1], steps=steps)
    version = len([v for v in list_assets(story_id, "visuals")
                   if v.get("owner_id") == character_id and v.get("kind", "").startswith("character")]) + 1
    v = add_approved_visual(story_id, "character_master", character_id, version, res["output_path"],
                            style_profile=style_profile, prompt=prompt)
    quality_core.build_manifest(output_id=v["visual_id"], story_id=story_id,
                                production_type="character_master", style_profile_name=style_profile,
                                model="krea2", model_mode="txt2img", prompt=prompt,
                                parameters={"character_contract_version": c.get("version", 1)},
                                output_path=res["output_path"])
    return v, prompt

# 「身影/人影/剪影」不再算人：建立镜头档要求极远处放一个尺度人影（2026-09-03）。
# 角色名和有身份的人（女仆、管家…）仍然不许出现。
_SCENE_PEOPLE = ("女仆", "男仆", "仆人", "侍女", "管家", "少女", "女孩", "女人",
                 "男人", "少年", "老者", "站着一", "坐着一",
                 # 2026-09-06 实验十六森林：写成"画面中人物为成年女性薇……身穿……"，老词表一个没中
                 "女性", "男性", "人物为", "身穿", "穿着", "神情",
                 # 2026-09-06 宫殿：两侧"卫兵列阵"被画成前景一排真人——底图里一个人都不要，卫兵也是视频阶段放的
                 "卫兵", "士兵", "侍卫", "列阵", "人群", "跪列", "弟子", "信徒", "群众")


_SCENE_SMALL = ("血迹", "血泊", "血水", "黑血", "灰烬", "残骸", "信件", "一沓信", "羊皮纸", "狼毛", "毛发",
                "杯子", "酒杯", "碗", "蜡烛残", "烛泪", "散落的", "笔记本", "课程表", "日记")


def scene_indoor(card):
    """室内外由代码定，不信模型：景名/空间描述里有殿厅室房堂教室…且没写露天就是室内。"""
    import re as _re
    s = str(card.get("name") or "") + str(card.get("space") or card.get("contract_text") or "")[:60]
    _nm = str(card.get("name") or "")
    _sp0 = str(card.get("space") or card.get("contract_text") or "")[:8]
    if _re.search(r"露天|户外|外墙|外景", _nm) or _re.match(r"^\s*室外", _sp0):
        return False
    if _re.search(r"内部|殿内|室内|大殿|厅|书房|卧室|房间|地牢|牢房|教室|走廊|船舱|洞内|堂|阁|殿|馆|宅|室|房|铺|店|舱", _nm):
        return True                                             # P354：地名里有室内词（林府大厅/桥头酒馆）先算室内
    if _re.search(r"广场|街|码头|山道|林|原|坡|谷|海|崖|草地|路口|岔路|大路|官道|营地|河|湖|树下|庭院|山顶|桥|田|村口|荒野|旷野", _nm):
        return False                                            # P350：自然/露天地名一律室外
    return bool(_re.search(r"内部|殿内|室内|大殿|厅|书房|卧室|房间|地牢|牢房|教室|走廊|船舱|洞内|堂|阁|殿", s))


def scene_design_user(card, one, cast=None):
    """设计稿 → 出图用户文本（正式版和实验室共用）。
    只给：室内外（代码定）、空间、戏区方位（去掉人名列）、意图、一件镇场主体、光、纵深（去掉前景遮挡段）。
    人物痕迹不进图——场景图是干净的底，人物和小件由视频阶段带进去（用户 2026-09-06 定）。"""
    import re as _re
    indoor = scene_indoor(card)
    # 人物卡名字整个删掉：设计稿的一句话空间/光/戏区里常带人名（"照亮沈青"），写提示词的模型见名就画人
    names = sorted({str(n).strip() for n in (cast or []) if str(n).strip()}, key=len, reverse=True)
    def _strip(x):
        x = strip_headcount(str(x or ""))     # P218「中央是三人围坐的圆桌」→「中央是圆桌」
        for n in names:
            x = x.replace(n, "")
        # 删完人名会剩下孤零零的"和/与"（"照亮沈青和林小满" → "照亮和"），这里收掉。
        # 替换串本该是 \1（保留动词，删掉后面孤零零的"和/与"）。早前一个 heredoc
        # 补丁把反斜杠吞成了 0x01 控制字符，等于往提示词里塞乱码，P218 修掉。
        x = _re.sub(r"(照亮|站在|位于|前的|处的)(和|与)", r"\1", x)
        # 上面那条只管连词紧挨着动词（"照亮和"）。隔了词的还得再收一遍：
        #   "照亮门口区和，形成冷灰色调"       → "照亮门口区，形成冷灰色调"
        #   "冲突发生地，和处于暖光与冷光交界处" → "冲突发生地，处于暖光与冷光交界处"
        # 只动贴着标点的连词，句子中间的"暖光与冷光"一个不碰。
        # 不整句删：整句删会把"照亮门口区""落在桌边区中央"这些真空间信息一起带走。
        x = _re.sub(r"[和与、]+(?=[，。；｜\n]|$)", "", x)
        x = _re.sub(r"(?<=[，。；｜\n])[和与、]+", "", x)
        return _re.sub(r"^[和与、]+", "", x)
    card = {k: (_strip(v) if isinstance(v, str) else v) for k, v in dict(card).items()}
    # 群体人物短语也删（"两侧有持戟卫兵列阵""跪成两列的弟子"）——按逗号/顿号切片段，含群体词的片段整段去掉
    _crowd = r"卫兵|士兵|侍卫|列阵|人群|跪列|弟子|信徒|群众|仆从|随从"
    _seg = r"[^，。；｜\n]*(?:" + _crowd + r")[^，。；｜\n]*[，；]?"
    card = {k: (_re.sub(_seg, "", v) if isinstance(v, str) else v) for k, v in card.items()}
    # P157：设计稿里的人多半是称呼（老对手、爸爸、二姨），不在人物卡名字表里。按分句再删一遍。
    _pseg = r"[^，。；｜\n]*(?:" + _PERSON_RE + r")[^，。；｜\n]*[，；]?"
    card = {k: (_re.sub(_pseg, "", v) if isinstance(v, str) else v) for k, v in card.items()}
    space = str(card.get("space") or card.get("contract_text") or "").strip()
    space = _re.sub(r"^(室内|室外)[，,、 ]*", "", space)
    depth = [seg.strip() for seg in _re.split(r"[｜|\n]", str(card.get("depth") or ""))
             if seg.strip()]                                    # P350：前景那句也给（低矮的草石栏杆，不挡人）
    _fix = str(card.get("fixtures") or "").strip()
    return ("【场景基本信息】%s\n【室内外】%s\n【一句话空间】%s\n【空间布局（只是地面分区，不往里放东西）】%s\n"
            "【设计意图】%s\n【镇场主体（画面最大的那件）】%s\n%s【光】\n%s\n【纵深与尺度】\n%s"
            % (one, "室内——有屋顶和四面墙，机位在内部" if indoor else "室外——没有屋顶，尺度按几十到几百米",
               space, scene_layout_for_image(card.get("layout")) or "无", card.get("intent") or "",
               card.get("landmarks") or "",
               ("【固有陈设（这个地方在使用中本来就有的大件，每一样都要画出来、按给的方位摆）】\n%s\n" % _fix) if _fix else "",
               card.get("light") or "", "｜".join(depth)))


def scene_layout_for_image(layout):
    """设计稿的戏区表「戏区名｜哪场戏｜方位｜地面周围」给出图时去掉第二列——那一列全是人名和动作，
    出图模型会照着把人画进空间参考板（实验十六森林实测）。"""
    import re as _re
    rows = []
    for ln in str(layout or "").splitlines():
        if "｜" in ln or "|" in ln:
            cols = [x.strip() for x in _re.split(r"[｜|]", ln)]
            if len(cols) >= 3:      # 三列也常见（模型把方位和地面并成一列），第二列一律是戏
                cols = [cols[0]] + cols[2:]
            ln = "｜".join(cols).replace("，。", "。").replace("，｜", "｜")
        rows.append(ln)
    return "\n".join(rows)


_CN_NUM = r"[一二两三四五六七八九十百千0-9]+"


def scene_prompt_fix_scale(prompt, card):
    """出图提示词里的尺度必须和设计稿一致（P131：118 酒馆设计稿进深约八米，提示词抄了范文的四十米、三层楼穹顶）。
    按分句处理：① 设计稿有的量（进深/层高/面宽/高/宽/长）数字不同 → 改成设计稿的；② 设计稿没有这个量 → 这一分句删掉；
    ③ 设计稿没写穹顶/拱顶/层楼/大殿 → 带这些词的分句删掉；④ 删完提示词里没有尺度了 → 把设计稿【一句话空间】接在第一句（画风句）后面。"""
    import re as _re
    card = card or {}
    src = " ".join(str(card.get(k) or "") for k in ("space", "depth", "design", "contract_text"))
    out = str(prompt or "")
    if not src.strip() or not out.strip():
        return out
    _MEAS = r"(进深|层高|面宽|高|宽|长)"
    _PFX = r"((?:约|大约|接近|相当于)?)"
    fixed = []

    def _design_val(key):
        m = _re.search(key + r"(?:约|大约|接近|相当于)?(" + _CN_NUM + r")(米|层楼|层)", src)
        return (m.group(1), m.group(2)) if m else None

    banned = _re.compile(r"穹顶|拱顶|层楼|拱梁|拱形|中殿|大殿")
    design_has_grand = bool(_re.search(r"穹顶|拱顶|层楼|教堂|大殿|殿|礼堂|中殿", src))
    clauses = [c for c in _re.split(r"(?<=[，。；])", out) if c]
    keep = []
    for c in clauses:
        if not design_has_grand and banned.search(c):
            fixed.append("删「%s」" % c.strip()[:12])
            continue
        m = _re.search(_MEAS + _PFX + "(" + _CN_NUM + r")(米|层楼|层)", c)
        if m:
            dv = _design_val(m.group(1))
            if dv is None:
                fixed.append("删「%s」" % c.strip()[:12])
                continue
            if dv[0] != m.group(3):
                fixed.append("%s%s→%s" % (m.group(1), m.group(3), dv[0]))
                c = c[:m.start()] + m.group(1) + m.group(2) + dv[0] + dv[1] + c[m.end():]
        keep.append(c)
    out = "".join(keep)
    if not _re.search(_MEAS + _PFX + _CN_NUM + r"(?:米|层楼|层)", out):
        m1 = _re.search(r"【一句话空间】[：:]?\s*\n?(.*?)(?=\n【|\Z)", str(card.get("design") or ""), _re.S)
        one = (m1.group(1) if m1 else str(card.get("space") or "")).strip()
        one = _re.sub(r"^(室内|室外)[，,、 ]*", "", one)
        # P160②：老卡的一句话空间里写着人（「第三排正中央坐着老对手」），注入前按分句删掉
        one = strip_person_clauses(one)
        if one:
            sents = _re.split(r"(?<=[。])", out, maxsplit=1)
            out = (sents[0] + one.rstrip("。") + "。" + (sents[1] if len(sents) > 1 else "")) if len(sents) > 1 else out + one
            fixed.append("补设计稿一句话空间")
    if fixed:
        try:
            from cores.authoring import _dbg
            _dbg("场景提示词尺度照设计稿", {"改": fixed})
        except Exception:
            pass
    return out


_WORLD_MATERIAL = {
    "未来科幻": (r"石砌|石块|石墙|砖墙|夯土|木梁|木质墙|茅草|瓦片", "金属板、复合材料、玻璃、涂装钢结构"),
    "科幻": (r"石砌|石块|石墙|砖墙|夯土|木梁|木质墙|茅草|瓦片", "金属板、复合材料、玻璃、涂装钢结构"),
    "东方": (r"玻璃幕墙|不锈钢|水泥墙|混凝土|霓虹|塑料|电线|空调", "木、砖、夯土、青石、纸窗"),
    "东方古代": (r"玻璃幕墙|不锈钢|水泥墙|混凝土|霓虹|塑料|电线|空调", "木、砖、夯土、青石、纸窗"),
    "西方奇幻": (r"玻璃幕墙|不锈钢|混凝土|霓虹|塑料", "石、木、铁、粗麻"),
}


# P316：世界 → 场景提示词开头钉一句这个世界的建筑材料和陈设（正面写，Krea 不认否定）；
# 西方世界里模型顺手写的中式词换成欧式词（项目183：青石板/铜铃/木栅栏/条凳 → 出来是中式院子）
_WORLD_SCENE_LINE = {
    "西方": "这是中世纪欧洲风格的地方：石砌墙体与灰色石板地、粗木梁与橡木家具、铁艺灯具、粗麻与皮革的织物、半圆拱门与铅条玻璃窗、欧洲式的木棚货摊。",
    "东方": "这是东亚古代的地方：木构梁柱、青砖灰瓦、纸窗木格、飞檐回廊、石灯笼与竹木器物。",
    "未来": "这是未来世界的地方：金属板与复合材料、玻璃与涂装钢结构、嵌入式光源、干净的接缝与面板。",
}
_WORLD_WORD_SWAP = {
    "西方": (("青石板", "灰色石板"), ("青砖", "石块"), ("青瓦", "石板瓦"), ("飞檐", "尖顶"), ("纸窗", "铅条玻璃窗"), ("木格窗", "铅条玻璃窗"),
            ("灯笼", "铁艺灯"), ("条凳", "长木凳"), ("八仙桌", "橡木长桌"), ("太师椅", "高背木椅"), ("屏风", "挂毯"), ("回廊", "石柱廊"),
            ("牌匾", "木招牌"), ("檐角", "屋檐"), ("庭院", "石砌院落"), ("影壁", "石墙")),
    "未来": (("青石板", "金属地板"), ("石砌", "金属板拼接"), ("木梁", "钢梁"), ("灯笼", "嵌入式灯带")),
}


def scene_prompt_fix_world(prompt, settings=None):
    """P316：场景提示词对上世界——开头一句世界陈设（正面），中式/时代错位的词按表替换。现代世界不动。"""
    w = str((settings or {}).get("world_type") or "").strip()
    key = next((k for k in _WORLD_SCENE_LINE if w.startswith(k)), "")
    p = str(prompt or "")
    if not key or not p.strip():
        return prompt
    n = 0
    for a, b in _WORLD_WORD_SWAP.get(key, ()):
        if a in p:
            p = p.replace(a, b)
            n += 1
    # P350：不再在开头钉"石砌墙体…木棚货摊"那句——草地、山谷也被造成石屋加货摊（197/199 实测）；世界靠指令词第 2 句"这是哪儿"
    try:
        from cores.authoring import _dbg
        _dbg("场景提示词对世界", {"世界": key, "换词": n})
    except Exception:
        pass
    return p


def scene_prompt_fix_material(prompt, settings=None):
    """材质对上世界观（P156②：空间站主控室左墙是石砌的）。命中黑名单的分句删掉，并在末尾点明该用的材质。"""
    import re as _re
    w = str((settings or {}).get("world_type") or "").strip()
    hit = next((v for k, v in _WORLD_MATERIAL.items() if k and (k in w or w in k)), None)
    if not hit or not str(prompt or "").strip():
        return prompt
    ban, want = hit
    out, n = [], 0
    for sent in _re.split(r"(?<=[。！？])", str(prompt)):
        if sent.strip() and _re.search(ban, sent):
            n += 1
            continue
        out.append(sent)
    p = "".join(out)
    if n:
        p = p.rstrip("。 ") + "。墙面、地面、结构的材质只用%s，不出现不属于这个世界的材料。" % want
        try:
            from cores.authoring import _dbg
            _dbg("场景材质对世界观", {"删": n, "世界": w})
        except Exception:
            pass
    return p


_PERSON_RE = (
    # 统称 / 身体 —— 底图里出现任何一个都是错
    r"人物|人影|人形|剪影|背影|身影|面孔|脸上|眼神|肩膀|双手|手中|手里|手上|指尖|"
    r"卫兵|士兵|侍卫|列阵|人群|跪列|弟子|信徒|群众|仆从|随从|"
    r"少女|女孩|女人|男人|少年|老者|侍女|女仆|男仆|仆人|老人|孩子|小孩|婴儿|"
    r"行人|路人|观众(?!席)|看客|旅客|乘客(?!座)|顾客|客人|病人|死者|遗体|尸体(?!台|床)|"
    # 亲属称呼 —— 群像题材的人全是这么写的
    r"爸爸|妈妈|爷爷|奶奶|姥姥|姥爷|父亲|母亲|儿子|女儿|孙子|孙女|夫妻|"
    r"二姨|大姨|姨妈|叔叔|舅舅|伯父|婶婶|哥哥|姐姐|弟弟|妹妹|"
    # 职业 / 关系称呼
    r"对手|队友|同事|同学|老板娘|老板|店主|掌柜|伙计|店员|服务员|保安|门卫|保洁|"
    r"医生|护士|警察|老师|考生|司机|教练(?!席)|解说|工程师|守夜人|守将|死士|主角|"
    # 人做的事
    r"身穿|穿着|连体衣|神情|怀抱|仰望|站立的位置|坐在|坐着|站着|站在|靠在|蹲在|躺在|"
    r"翻找|指路|低头|抬头|转身|侧身|背对|交谈|微笑|握紧|握着|"
    # 逼出人的机位
    r"车内视角|驾驶位|副驾驶|后座|过肩|第一人称|主观视角"
)


import re      # P218：下面两条正则在模块层编译

# 「空无一人」「一个人都没有」是我们自己写的正向收尾，里面也有"一人"，先护住
_NO_PERSON_KEEP = re.compile(
    r"空无一人|一个人都没有|一个人也没有|一个人都不出现|不见一人|"
    r"(?:没有|无|不见)\s*(?:任何)?\s*[一二两三四五六七八九十几]?\s*(?:个|名|位)?\s*人")

# 「几个人＋姿势」这个修饰：前面可带"可坐/容纳"，后面可带姿势和"的"
_HEADCOUNT = re.compile(
    r"(?:可坐|能坐|坐得下|容纳)?"
    r"[一二两三四五六七八九十几数百千0-9]+\s*(?:个|名|位)?\s*人"
    r"(?![高宽长深大小矮多称]|沙发|床|桌|间|房)"          # 尺度词和家具名不是人
    r"(?:们)?"
    r"(?:\s*(?:围坐|围站|围拢|环坐|对坐|对饮|并坐|并排|同坐|落座|就座|坐|站|聚|挤)"
    r"\s*(?:在|着|于)?)?"
    r"(?:的)?")

# 没有数词的姿势：「地面为石板，围坐圆桌」——「围坐」本身就是人在做的事。
# 只删成词的，不删单字「坐」：「坐北朝南」「坐落」是建筑说法。戏区名「围坐区」也放过。
_POSTURE = re.compile(
    r"(?:围坐|环坐|对坐|并坐|同坐|落座|就座|围站|围拢|围立)(?!区)"
    r"\s*(?:在|着|于)?\s*(?:的)?")


def strip_headcount(text):
    """只摘掉「几个人＋姿势」这个修饰，家具本身留着：「三人围坐的圆桌」→「圆桌」（P218）。

    美术指导写【一句话空间】时会顺手把戏写进去（「中央是三人围坐的圆桌」），
    「三人」既不是人名也不是称呼，删人名和删称呼两道过滤器一个都拦不住，
    图里就真的坐了三个人。
    但整句删也不行——圆桌和后半句的光会一起没掉，底图反而变空。所以这里只摘修饰。
    """
    t = str(text or "")
    if not t.strip():
        return t
    keep = []

    def _hide(m):
        keep.append(m.group(0))
        return "\x00%d\x00" % (len(keep) - 1)

    t = _NO_PERSON_KEEP.sub(_hide, t)
    t = _HEADCOUNT.sub("", t)
    t = _POSTURE.sub("", t)
    t = re.sub(r"[，,、；;]{2,}", "，", t)
    t = re.sub(r"[，,、；;]+([。！？])", r"\1", t)
    t = re.sub(r"([。！？])[，,、；;]+", r"\1", t)
    for i, v in enumerate(keep):
        t = t.replace("\x00%d\x00" % i, v)
    return t.strip()


def strip_person_clauses(text, names=()):
    """按分句把带人的片段删掉，别的留着（P160）。设计稿、一句话空间、剧本【空间】共用。"""
    import re as _re
    t = strip_headcount(str(text or ""))          # P218 先摘「三人围坐的」，家具留着
    if not t.strip():
        return t
    for n in sorted({str(x).strip() for x in (names or []) if str(x or "").strip()}, key=len, reverse=True):
        t = _re.sub(r"[^，。；｜\n]*" + _re.escape(n) + r"[^，。；｜\n]*[，；]?", "", t)
    t = _re.sub(r"[^，。；｜\n]*(?:" + _PERSON_RE + r")[^，。；｜\n]*[，；]?", "", t)
    t = _re.sub(r"[，,、；;]{2,}", "，", t)
    t = _re.sub(r"[，,、；;]+。", "。", t)
    t = _re.sub(r"。[，,、；;]+", "。", t)
    t = _re.sub(r"。{2,}", "。", t)
    t = _re.sub(r"^[，,、；;。]+", "", t)
    return t.strip()


def _has_person(text, names=()):
    import re as _re
    t = str(text or "")
    if any(str(n).strip() and str(n) in t for n in (names or ())):
        return True
    return bool(_re.search(_PERSON_RE, t))


def scene_prompt_repair(prompt, cast):
    """确定性修复（有错直接修，不打回）：删掉含人名 / 人物词 / 服装词的整句。
    验收器三轮打回后模型仍会把角色写进空间参考板，这里代码兜底，图里绝不能有人。"""
    import re as _re
    names = [str(n) for n in (cast or []) if str(n).strip()]
    prompt = strip_headcount(prompt)              # P218 先摘修饰，再走整句删除
    _cast_re = _re.compile("|".join(_re.escape(n) for n in names)) if names else None
    _person = _re.compile("女性|男性|内裤|乳|" + _PERSON_RE)
    # P350（用户 9-14 定）：远处的小人影、牲畜可以留——只删主角句，和"能看清脸/衣服"的人
    _far = _re.compile(r"远处|远景|极远|中远景|模糊|小小|小人影|背对|侧对|看不清|牲畜|牛羊|马匹|狗|鸟|羊")
    _clear = _re.compile(r"身穿|穿着|神情|正脸|脸部特写|面孔清晰|特写|近景|前景[^，。；]{0,6}(?:人|少女|女孩|男人|少年)")   # P354：裸「脸」会把「看不清脸」那句删掉
    out, cut = [], 0
    for para in str(prompt or "").split("\n"):
        sents = _re.split(r"(?<=[。！？])", para)
        kept = []
        for s in sents:
            if not s.strip():
                continue
            if _cast_re and _cast_re.search(s):
                cut += 1
                continue
            if _person.search(s) and not (_far.search(s) and not _clear.search(s)):
                cut += 1
                continue
            kept.append(s)
        out.append("".join(kept))
    res = "\n".join(x for x in out if x.strip())
    if _has_person(res, names) and _clear.search(res):   # P157 自检：删完还有能看清的人就留证据（远处小人影不算）
        try:
            from cores.authoring import _dbg
            _dbg("底图提示词删完还有人", {"删句": cut, "残留": _re.search(_PERSON_RE, res).group(0)})
        except Exception:
            pass
    return res


_SCENE_DECOR = ("纹章", "徽章", "吊灯", "油画", "挂毯", "旗帜", "雕花", "鎏金",
                "银器", "酒柜", "酒架", "兽皮", "地毯", "盔甲", "雕塑", "肖像")
_SCENE_LUX = ("伯爵", "公爵", "侯爵", "男爵", "亲王", "国王", "女王", "王后",
              "皇", "领主", "贵族", "宫殿", "王府", "豪门", "城堡", "宗主", "掌门")


_SCENE_MODERN = ("塑料", "霓虹", "玻璃幕墙", "不锈钢", "混凝土", "沥青", "汽车", "电线", "路灯", "空调", "招牌灯箱", "运动鞋", "牛仔", "针织")


def _scene_prompt_problems(prompt, card, cast_names, grade=""):
    """场景板提示词验收（P350 短配方）：主角名不出现；远/中/前景和光各有一句；室外不写层高进深；
    奇幻/古代世界不出现现代物件词；唯一物件不重复；不写否定句。报错文案会原样喂回模型，只说要什么。"""
    import re as _re
    t = str(prompt or "")
    # P354：第一句是原样抄的画风句（里面有"室内的窗光""不是照片"这类词），不参与验收
    _body = t.split("。", 2)[-1] if "。" in t else t
    _first = t[:len(t) - len(_body)]
    if not _re.search(r"画风|风格|摄影|照片|动画|渲染|自然感|写实", _first):
        _body = t
    errs = []
    hits = [n for n in cast_names if n and n in _body]
    if hits:
        errs.append("提示词里写了主角（%s）——主角有自己的人设图，这张图里不画他们；远处的小人影可以保留" % "、".join(sorted(set(hits))[:5]))
    for item in ("壁炉", "大门", "双开门", "床", "柜台", "楼梯", "祭坛", "篝火", "大树"):
        if len(_re.findall(item, t)) > 3:
            errs.append("「%s」写了 %d 次——唯一物件只写一次，其它地方不重提，否则会画成多个" % (item, len(_re.findall(item, t))))
    need = []
    if not _re.search(r"远处|远景|远端|远方|尽头|天际|地平线|雾|远山|天空", _body):
        need.append("远景（最远的东西是什么，一层比一层淡）")
    if not _re.search(r"中景|中央|中间|中部|正中|主体|画面(左|右)侧|正前方|深处", _body):
        need.append("中景（这个地方的主体在画面哪一侧）")
    if not _re.search(r"前景|近处|脚下|画面下方", _body):
        need.append("前景（低矮的草丛/岩石/栏杆/地砖，不挡中景）")
    if not _re.search(r"光|阳|正午|黄昏|清晨|傍晚|夜|晨|夕", _body):
        need.append("光（几点钟、从哪边来、什么颜色）")
    if need:
        errs.append("缺这几句：" + "；".join(need))
    if not scene_indoor(card) and _re.search(r"层高|面宽|进深|封闭空间|室内", _body):
        errs.append("这是室外，去掉「层高／面宽／进深／封闭／室内」，写地貌和远近（几十米到几百米）")
    _w = str(card.get("_world") or "")
    if _w and not _w.startswith("现代") and not _w.startswith("未来"):
        _mod = sorted({w for w in _SCENE_MODERN if w in _body})
        if _mod:
            errs.append("这个世界没有「%s」这些现代物件，换成这个世界的东西" % "、".join(_mod))
    _neg = _re.findall(r"(?:没有|无人|无任何|无多余|不写|禁止|不出现|不许|不要|不放|不留)[一-龥]{0,8}", _body)
    if _neg:
        errs.append("把这些否定句删掉，只写画面上有什么：" + "、".join(sorted(set(_neg))[:4]))
    _small = sorted({w for w in _SCENE_SMALL if w in _body})
    if _small:
        errs.append("小道具不进这张图：去掉「%s」" % "、".join(_small))
    if _re.search(r"文字|字迹|写着|字样|英文|汉字", _body):
        errs.append("牌子和旗帜上是图案和纹章，把「文字/字迹/写着」那句改成图案")
    return errs


def _scene_prompt_problems_old(prompt, card, cast_names, grade=""):
    """旧版验收（P350 前）：留档备查，不再调用。"""
    import re as _re
    t = str(prompt or "")
    # 先剥掉否定句（"无人物、无人影"是合规收尾，不算提到人）
    t2 = _re.sub(r"[无没][有]?[任何主要]*(?:人物|人影|人群|剪影|身影|清晰面孔)[、，。；]?",
                 "", t)
    # 【尺子不算人】建立镜头档要求极远处放一个占画面不到百分之一的人影剪影当尺度参照
    # （2026-09-03 实测这是"显大"的关键之一）。带"参照/百分之一/千分之一/衬出"的句子剥掉再查。
    t2 = "。".join(x for x in _re.split(r"[。；]", t2)
                  if not _re.search(r"参照|百分之一|千分之一|衬出|尺度", x))
    errs = []
    hits = [n for n in cast_names if n and n in t2]
    hits += [w for w in _SCENE_PEOPLE if w in t2]
    if hits:
        errs.append("提示词里写了人物（%s）——这是纯空间参考板，任何人物、人影、"
                    "剪影都不许出现，把他们做的事换成空间自己的状态"
                    "（女仆在扫地→地面洁净/立着扫帚）" % "、".join(sorted(set(hits))[:5]))
    # 唯一物件（从布局里提取的标志物）跨分区重复描述会被画成两个
    for item in ("壁炉", "大门", "双开门", "床", "柜台", "楼梯"):
        if item in str(card.get("layout") or "") and len(_re.findall(item, t)) > 3:
            errs.append("「%s」在提示词里被描述了 %d 次——唯一物件只在它自己的分区里"
                        "写一次，其它分区不重提，否则会被画成多个（实测画出两个壁炉）"
                        % (item, len(_re.findall(item, t))))
    # 构图锚定：四个方位词必须齐——不锚定机位就随机，出斜角歪图（实测）
    need = [w for w in ("画面左", "画面右", "前景") if w not in t]
    if "正前方" not in t and "深处" not in t:
        need.append("正前方/深处")
    if need:
        errs.append("构图没有锚定（缺：%s）——写出画面左侧、画面右侧、正前方深处、"
                    "前景各是什么，方位可以是空的（空的石板地／平整的石台）" % "、".join(need))
    # 【显大的三样证据】2026-09-03 同场景同 seed 实测：有这三样出来是整座山，没有就是一间屋子。
    # 报错只说要什么，不说不要什么——报错文案会被原样喂回模型，禁令它照抄进提示词。
    _space0 = str(card.get("space") or card.get("contract_text") or "")
    _indoor = bool(_re.search(r"内部|殿内|室内|大殿|厅|书房|卧室|房间|地牢|牢房|教室|走廊|船舱|洞内", _space0 + str(card.get("name") or "")))         and not _re.search(r"露天|户外|外墙|外景", _space0)
    if _indoor:
        # 室内的尺子在尽头：门洞/窗/座椅——教室里要求"极远处人影小屋"是把教室当山谷（2026-09-06 实验十六教室三轮都过不了）
        if not (_re.search(r"人影|门洞|门框|门|窗|柱|座椅|课桌|讲台|蒲团", t)
                and _re.search(r"尽头|远端|最远|极远|远处|百分之", t)):
            errs.append("缺尺度参照物——室内的尺子放在尽头：最远那道门洞／一扇窗／一把座椅，"
                        "写明它离机位多远、占画面多小")
    elif not (_re.search(r"人影|小屋|船|马车|车辆|旅人|行人", t)
              and _re.search(r"百分之一|千分之一|极远|远处.{0,6}(小|微小)", t)):
        errs.append("缺尺度参照物——在极远处、和主体同一深度，放一个占画面高度不到百分之一的"
                    "人影剪影（或一间小屋、一艘船），写明它有多小、离多远")
    if _indoor:
        # 室内的"远"靠纵深：尽头变暗变小、光柱和尘埃、柱列一根根缩小——写了远山雾海就会把墙拆掉（实测大殿被画成露天柱廊）
        if not _re.search(r"光柱|尘|尽头|远端|越来越小|一根根|一层层|渐暗|沉进阴影", t):
            errs.append("缺纵深——室内空间写出远端：尽头变暗变小、柱列一根根往远处缩小、高窗的光柱和空气里的尘")
    elif not _re.search(r"雾|远山|发白|变淡|空气透视|层层|越远越", t):
        errs.append("缺空气透视——写出远景（远山／雾海／天际）一层比一层淡、发白发灰，"
                    "前景清楚、中景是主体")
    # 前景遮挡不再要求（2026-09-06 用户定：场景图是给人物站进去的底，前景压东西人就被挡住）
    # 讲故事的小件不许进底图——它们由视频提示词在镜头里带出来
    _small = sorted({w for w in _SCENE_SMALL if w in t})
    if _small:
        errs.append("这张图是干净的底，不画讲故事的小件——把「%s」这些去掉，只留空间结构和一件镇场主体"
                    % "、".join(_small))
    _space = str(card.get("space") or card.get("contract_text") or "")
    if not _indoor and _re.search(r"洞|山|崖|野|海|荒|城池|城墙|码头|街", _space) and _re.search(r"层高|面宽|进深", t):
        errs.append("这是室外／洞穴／山体，尺度按山和城的量级写（几十米、几百米、几公里），"
                    "把「层高／面宽／进深」这些房间尺度词换掉")
    # 否定句（"无杂物""无多余陈设""不写…"）是模型把验收反馈原样抄进去的——
    # 出图模型读不懂否定，点名的东西反而会被画出来。先剥掉再查，并要求改成正面写法。
    _neg = _re.findall(r"[无没][有]?[多余任何]*[一-龥]{1,8}|不写[一-龥]{1,8}|禁止[一-龥]{1,8}", t)
    _neg = [x for x in _neg if not _re.match(r"[无没]有?(?:人物|人影|人群|剪影|身影|清晰面孔|照片纹理)", x)
            and not _re.match(r"无(?:限|数|边|尽|垠|际)", x)]     # "无限延伸"不是否定句
    if _neg:
        errs.append("把这些否定句删掉，只写画面上有什么（写地面是什么材质、画面里有哪几样东西）："
                    + "、".join(sorted(set(_neg))[:4]))
    _t_pos = _re.sub(r"[无没][有]?[多余任何]*[一-龥]{1,8}", "", t)
    _bad = [w for w in ("散落", "堆积", "杂物", "碎石堆", "散落着") if w in _t_pos]
    if _bad:
        errs.append("地面要空：把「%s」那句改成地面是什么材质（平整的石台／干燥的沙地），"
                    "画面三分之二是天空、雾、水面或空地" % "、".join(sorted(set(_bad))))
    # 豪华档身份物件够数（档次由代码从正文身份词判定）
    if grade == "豪华":
        got = sorted({w for w in _SCENE_DECOR if w in t})
        # 建立镜头档：豪华档只要一件镇场道具（体量大、贴在建筑上），不再要四样
        if len(got) < 1:
            errs.append("这是豪华档空间（正文里的主人是贵族/领主级），提示词里一件身份物件"
                        "都没有——要有一件体量大的镇场道具（整面墙的挂毯、悬在中央的巨型吊灯、"
                        "壁炉上方的纹章石雕这类），最多再加两件，不写小件")
    return errs


def resolve_style(style_profile, project_style, default_style="电影级写实"):
    """这张图到底用哪个画风（P219）。

    以前默认值是字符串 "电影级写实"，代码拿它当"没传"的哨兵，
    结果显式要电影级写实会被换成项目画风——四个画风里唯独这一个点不出来。
    现在"没传"就是 None，传了什么就是什么。
    """
    st = str(style_profile or "").strip()
    if st:
        return st
    return str(project_style or "").strip() or default_style


# 画风只写在提示词开头压不住后面的写实描述（P234 四轮实拍）：
# 风格段是交给千问写提示词的素材，它会改写和丢弃；而提示词后半段 500 多字
# 全是空间的写实描述，把二维和三维档淹掉。这一句钉在**最末尾**，
# 位置在代码里、千问改写不到，也是模型读到的最后一句。
# 媒介宣告用「是X不是Y」是有效的，和"画面里的物件不要否定式"是两回事。
_STYLE_TAIL = {
    "电影级写实": "整张图是实拍的电影摄影：真实材质、真实光学、细腻的胶片颗粒。",
    "网红自然感": "整张图是一张真实照片：现场光有方向、明暗有层次、色彩自然饱满、材质真实。",
    "平面动漫": "整张图是手绘的二维动画背景：每样东西都有深色轮廓线，颜色是高纯度的平涂色块——"
                "石头画成带蓝的青灰，木头画成暖橙棕，白墙画成奶油黄，暗部画成深蓝紫，"
                "整张画没有一块脏灰色；不是照片、不是三维渲染。",
    # 【别写"建模／模型／网格"】那是制作过程，模型会照着画出中间态——
    # P234 实测：写了「建模出来的物件」，成片里家具和石块边缘叠了一层白色线框。
    # 只描述渲染出来的成品长什么样。
    "3D游戏": "整张图是日式 RPG 游戏过场动画里的一幕：表面是均匀铺满的单色材质，"
              "高光是一块规整的亮斑，阴影是引擎算出来的干净软阴影，色彩饱和明快；"
              "物件的外形是完整的实体块面，不是照片、不是手绘。",
}


def style_tail(style):
    """按画风钉在提示词最末尾的那句媒介宣告（P234）。认不出就不加。"""
    return _STYLE_TAIL.get(str(style or "").strip(), "")


def generate_scene_master(story_id, scene_id, style_profile=None, quality="formal",
                          custom_prompt=None, scene_action="", present_names=None):
    """场景参考板：画的是"这个地方长什么样"，给视频当**背景锚**。

    【为什么不含人物】更早的版本是"含人物的剧情场景图"（画这一刻正在发生什么）。
    当视频参考图用时，图里那几张脸会和人设图的脸打架，H3 同时收到两套长相。
    现在参考板只画空间：主要角色一个不画；人群只在"这个空间本来就有人"
    （集市/酒馆）时画，且一律中远景模糊无脸。scene_action / present_names
    两个参数保留只为兼容旧调用方，参考板不再用它们。
    """
    from models import krea_client
    from cores import prompt_writer as _pw, project_settings as _pset, story_core as _sc
    sc = get_asset(story_id, "scenes", scene_id)
    if not sc:
        raise ValueError("场景不存在")
    _story = _sc.get_story(story_id) or {}
    P = _pset.get(_story) or {}
    # 参数以前只是算出来放着：真正决定画风的是 P["style"]，
    # 所以想单出某个画风的场景图根本做不到（P217 实测：四个画风出来一模一样）。
    # 这里把它写进 P 的副本，不动项目设定本身。
    style_profile = resolve_style(style_profile, P.get("style"))
    if style_profile != P.get("style"):
        P = dict(P, style=style_profile)

    if str(custom_prompt or "").strip():
        prompt = str(custom_prompt).strip()
    else:
        # 参考板只描述空间本身：地点+内外+时间 +【空间】【布局】【材质】【主光】【标志物】
        _card = dict(sc)
        if not str(_card.get("space") or "").strip():
            _card["space"] = sc.get("contract_text") or scene_action or ""
        one = _pw.scene_plate_one_line(_card)
        # 【2026-08-28 用户定】场景设计师改读故事正文直供：旧链路
        # 正文→编剧压成60字【空间】→再压一句话→Qwen扩写，两次压缩把尺度氛围
        # 全漏光，出图全是闭塞小房间。现在把正文全文一并喂给场景设计师，
        # 让它自己提取该场景的全部描写（场景图_指令词 新版信息来源规则）。
        _user = "【场景基本信息】%s\n【场景布局】%s" % (one, sc.get("layout") or "无")
        try:
            from cores import saga_core as _sg
            _e = _sg.episode(_sg.get_saga(story_id), int(sc.get("episode") or 1)) or {}
            if str(_e.get("prose") or "").strip():
                _user += "\n\n【故事正文】\n" + str(_e["prose"]).strip()
        except Exception:
            pass
        # 【美术指导设计稿】2026-09-06：卡上有设计稿就按设计稿出图，不再喂正文全文
        # （正文让 Qwen 滑回剧情插图；设计稿已经把意图/主体/戏区/光/尺度定死，
        # 实验十五、十六实测同 seed 出图更大更干净）。
        if str(sc.get("design") or "").strip():
            _user = scene_design_user(sc, one, [str(c.get("name") or "") for c in list_assets(story_id, "characters")])
        # 【场景提示词验收器】喂正文全文后 Qwen 会滑回"剧情插图"：实测把女仆/
        # 艾拉/人影写进了空间参考板（画面直接出人），"壁炉"跨分区重复描述 5 次
        # （画面画了两个壁炉）。规则是愿望，验收打回是兜底。
        _cast = [str(c.get("name") or "") for c in list_assets(story_id, "characters")
                 if c.get("name")]
        # 档次判定走代码（确定性）：正文/场景信息里出现贵族身份词 → 豪华档
        _grade = "豪华" if any(w in _user for w in _SCENE_LUX) else ""
        if str(sc.get("design") or "").strip():
            _grade = ""      # 有设计稿：镇场主体已经定了，不再按身份词加料（2026-09-06 用户：不要混搭）
        _grade = ""                                                        # P350：短配方里陈设由指令词第 7 句定，不再按身份词加料
        sc = dict(sc, _world=str(P.get("world_type") or ""))              # P350：验收器要知道世界（现代物件词）
        for _try in (1, 2, 3):
            prompt = _pw.write("scene", _user, P)
            errs = _scene_prompt_problems(prompt, sc, _cast, grade=_grade)
            if not errs:
                break
            _user += "\n\n★★【上一版不合格，重写】" + "；".join(errs)
        # 三轮之后不管过没过，人名/人物/服装句一律删掉——参考板里绝不能有人（2026-09-06）
        prompt = scene_prompt_repair(prompt, _cast)
        prompt = scene_prompt_fix_scale(prompt, sc)         # 尺度数字照设计稿（P131）
        prompt = scene_prompt_fix_material(prompt, P)       # 材质对上世界观（P156②）
        prompt = scene_prompt_fix_world(prompt, P)          # P316：世界陈设句 + 中式词换欧式词
        prompt = scene_prompt_repair(prompt, _cast)         # P160③：注入做完之后再兜一次，过滤必须是最后一道
        # 收尾这句以前是否定句：「画面中没有任何人物，一个人都不出现。牌匾和招牌上不写文字。」
        # krea_client 自己的注释写着：负向词在这套工作流里不参与计算，
        # 想赶走什么只能正面描述它不在。那句反着来，把「人物」「牌匾」「招牌」「文字」
        # 四个名词直接送进了画面——实测壁炉上多了一块写着乱码汉字的牌子（P213）。
        # 改成正面说画面里**有什么**。
        # P350：收尾不再写"屋子空着、椅子空着"（草地也按屋子画）；只说这张图是地方的全景、主角不在
        prompt = (prompt.rstrip("。 ")
                  + "。这是这个地方的全景建立镜头，画面里是空间本身和它固有的陈设。")   # P354：不点名人影/牲畜（见名词就画）
        # 剥否定句放最后一步：人设图那条链路一直有，场景图这条没有；
        # 而且原来加在收尾句之前等于白剥（P213）
        from cores.authoring import _strip_neg as _sn
        prompt = _sn(prompt)
        # 【最后一句是画风】二维/三维画风钉在最末尾（P234）；照片类画风第一句已经是画风，不再加"手机直出"那句（P350）
        try:
            from cores import style_presets as _spk
            _kind = _spk.style_kind(P.get("style") or style_profile)
        except Exception:
            _kind = "photo"
        _st = style_tail(P.get("style") or style_profile) if _kind in ("2d", "3d") else ""
        if _st:
            prompt = prompt.rstrip("。 ") + "。" + _st
    # Turbo 检查点 + 两段式：7 步 / cfg 1.0。尺寸是**出底**，成品是它的 1.5 倍。
    # 正式 1920×1080 → 2880×1616：四视图并排时每格才有 700+ 像素承载脸部细节。
    steps = 5 if quality == "preview" else 7
    wh = (1280, 720) if quality == "preview" else (1920, 1080)
    res = krea_client.generate(prompt, width=wh[0], height=wh[1], steps=steps)
    version = len([v for v in list_assets(story_id, "visuals")
                   if v.get("owner_id") == scene_id and v.get("kind") == "scene_master"]) + 1
    v = add_approved_visual(story_id, "scene_master", scene_id, version, res["output_path"],
                            style_profile=style_profile, prompt=prompt)
    quality_core.build_manifest(output_id=v["visual_id"], story_id=story_id,
                                production_type="scene_master", style_profile_name=style_profile,
                                model="krea2", model_mode="txt2img", prompt=prompt,
                                parameters={"scene_contract_version": sc.get("version", 1)},
                                output_path=res["output_path"])
    return v, prompt

import re
def re_upper(s):
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", str(s or ""))
    return (s or "X")[:24].upper()

# 【字段清单唯一来源】人物卡/场景卡有哪些字段，只在这里写一次。
# 之前界面、创建、保存三处各写了一份，互相不一样：
#   · 保存路由的场景清单少了 space/ground/walls/door/window/furniture/light 七个，
#     而那七个正是编译器真正要读的 —— 用户在界面上改完一保存就没了
#   · 人物清单少了 face_type，长相选了等于没选
CHARACTER_FIELDS = ("name", "age", "sex", "sheet_body_ratio", "char_type", "face_type", "build",
                    "hair", "hair_preset", "voice", "personality",
                    "behavior_anchor", "appearance_details",
                    "clothing", "clothing_requirement", "outfit_preset", "note",
                    # 连载故事里人物是陆续进场的：这一项决定第几话才需要给他画图，
                    # 也决定故事页「本话人物」里谁该标"新"。
                    "first_episode",
                    # 六维五官（眼型/眼睑/眉/鼻/唇/识别点）。存在卡上而不是每次现算，
                    # 是为了让用户能看见、能改，也保证重生成设定图不会变脸。
                    "face_features")

SCENE_FIELDS = ("name", "space", "scale", "depth", "materials", "ground", "walls",
                "door", "window", "furniture", "landmarks", "light",
                "contract_text", "regions", "fixed_landmarks", "base_light_color",
                "location_map", "main_light", "time",
                # episode 原来不在白名单里，界面上没法把一个场景挪到别的话次，
                # 只能靠自动流程写死。场景是跟着故事走的，这个必须可改。
                "episode", "place_preset",
                # 场次表定的拍摄条件：内/外、什么时间、空间规模。
                # 出图时靠 interior 锁死室内外，靠 scale_class 决定画多大多气派。
                "interior", "time_of_day", "scale_class")


def apply_fields(old, new, allowed):
    """把 new 里出现过的字段写进 old。没出现的保持原样，不会被空值冲掉。"""
    for k in allowed:
        if k in (new or {}):
            old[k] = new[k]
    return old


def delete_asset(story_id, kind, asset_id):
    """删一张卡（连同它名下的视觉登记）。

    自动流程重跑时用来清掉"上一次自动建的、用户没改过的"卡——
    同一个角色换个名字就再建一份，实测出现过 8 张卡两套人马。
    """
    import os
    p = assets_path(story_id, kind) / (str(asset_id) + ".json")
    if p.exists():
        p.unlink()
    for v in list_assets(story_id, "visuals"):
        if v.get("owner_id") == asset_id:
            vp = assets_path(story_id, "visuals") / (v["visual_id"] + ".json")
            if vp.exists():
                vp.unlink()
            f = v.get("path")
            if f and os.path.exists(f) and "outputs" in str(f):
                try:
                    os.remove(f)
                except Exception:
                    pass
    return True


# ---------- 服装加厚 ----------
# 用户的判断（实测支持）：出现裸体是**服装描述不到位**，不是模型的问题。
# Krea2 的否定词在 cfg=1.0 下失效，写禁令没用；把服装写厚才有用。
# 而且用户对服装设计要求高——人物除了好看，服装要有特点。

def _thicken_outfit(c, story_id):
    """服装写得太薄时，用内置组合模板补全；返回（描述, 模板编号）。"""
    settings = {}
    try:
        from cores import story_core as _sc, project_settings as _ps
        settings = _ps.get(_sc.get_story(story_id) or {}) or {}
    except Exception:
        pass
    from cores import project_prompt
    return project_prompt.complete_outfit(c, settings, seed=story_id)

