# -*- coding: utf-8 -*-
"""style_presets.py — 8848 项目设定预设（逐字搬，不重新发明）。

唯一职责：读画风预设（13 个 Krea2 预设 + 完整说明）与设定预设（整部作品长什么样），
并提供项目设定的逐字选项（世界类型/视觉强度/内容倾向/人物卡字段）。
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STYLE_DIR = ROOT / "presets" / "comic_styles"
SETTING_DIR = ROOT / "presets" / "character_settings"
STYLE_DIR.mkdir(parents=True, exist_ok=True)
SETTING_DIR.mkdir(parents=True, exist_ok=True)

# 【四界四风 2026-09-04 用户定】世界只留四个、画风只留四个；旧值靠下面的映射表归并。
WORLD_TYPES = ["东方", "西方", "未来", "现代"]
WORLD_GROUP = {"东方仙侠": "东方", "东方写实": "东方", "武侠": "东方",
               "西方魔幻": "西方", "西方写实": "西方", "蒸汽朋克": "西方",
               "未来科幻": "未来",
               "校园": "现代", "现代都市": "现代", "末日": "现代", "克系宇宙恐怖": "现代"}
# 四界在服装库/地点库/写法表里对应的旧键（这些库不改键，靠映射）
WORLD_LEGACY_KEY = {"东方": "东方仙侠", "西方": "西方魔幻", "未来": "未来科幻", "现代": "现代都市"}
VISUAL_STRENGTHS = ["自动", "自然", "美型（默认）", "强设计"]
STYLE_NAMES = ["电影质感", "写实生活", "平面动漫", "3D游戏"]
STYLE_GROUP = {"Krea2·电影级剧照": "电影质感", "Krea2·80年代胶片电影": "电影质感", "Krea2·仙侠偶像剧": "电影质感",
               "Krea2·尼尔式机械美学": "电影质感", "Krea2·赛博霓虹蒸汽波": "电影质感",
               "Krea2·超仿真生活照": "写实生活", "Krea2·日式小清新": "写实生活",
               "Krea2·高质量日漫": "平面动漫", "Krea2·黑白日漫": "平面动漫", "Krea2·复古赛璐璐80年代": "平面动漫",
               "Krea2·厚涂插画": "平面动漫", "Krea2·水彩油画手绘": "平面动漫", "Krea2·国风水墨写意": "平面动漫",
               "Krea2·3D游戏模型": "3D游戏", "Krea2·像素复古游戏": "3D游戏"}
DEFAULT_STYLE = "电影级写实"   # 2026-09-05 四风改名：电影质感 → 电影级写实


def world_group(name):
    """任意世界值 → 四界之一；认不出返回原值（空就空）。"""
    n = str(name or "").strip()
    if n in WORLD_TYPES:
        return n
    return WORLD_GROUP.get(n, "" if n in ("", "自动", "自动判断", "自定义") else n)


def world_legacy_key(name):
    """四界 → 服装库/地点库用的旧键；旧值原样返回。"""
    n = str(name or "").strip()
    return WORLD_LEGACY_KEY.get(n, n)


def style_group(name):
    """任意画风值 → 四风之一；认不出返回原值。"""
    n = str(name or "").strip()
    if n in STYLE_NAMES:
        return n
    return STYLE_GROUP.get(n, n)


def style_kind(name):
    """photo / 2d / 3d，以 render_notes.json 的 kind 为准。"""
    try:
        rn = json.loads((ROOT / "presets" / "render_notes.json").read_text(encoding="utf-8"))
        k = str((rn.get(str(name or "").strip()) or {}).get("kind") or "")
        if not k:
            k = str((rn.get(style_group(name)) or {}).get("kind") or "")
        return k or "photo"
    except Exception:
        return "photo"


def style_is_real(name):
    """真实组（电影质感/写实生活及一切 photo 类）→ True；夸张组（平面动漫/3D游戏）→ False。"""
    return style_kind(name) == "photo"
CONTENT_TENDENCIES = [
    "黑暗（剧情绝望，总往坏的方向走）", "血腥（断肢/内脏/大出血，可到最重）",
    "成人向·情爱（诱惑/暧昧/亲密关系）",
    "极致诱惑（卖肉向·不露点之外全露）",   # P360：三级片口径，成年男女；正文在 content_scale.json
]

# 【内容倾向影响哪一层】
# 审计发现：这四条原来只进了"一键生成"那一步的故事文字，图片和视频提示词一个字都没吃到。
# 其中「夸张（非常规体型）」是明确的**视觉**指令，不进人物图等于完全没选。
#
#   story  = 影响故事/剧情生成（Qwen 写故事时的尺度边界）
#   figure = 影响人物外观（体型比例，进人物图第1段）
#   shot   = 影响分镜画面（暴力/情色的画面尺度，分镜那一环用）
#
# 值是**可画的视觉描述**，不是"允许…"这种元指令——扩散模型看不懂许可。
CONTENT_TENDENCY_EFFECT = {
    "黑暗（剧情绝望，总往坏的方向走）": {
        "story": "剧情走向绝望，人物的努力多半落空，不给廉价的转机",
        "figure": "人物外观透出被境遇磨损的痕迹——眼神沉郁疲惫、气色偏冷，服装陈旧褪色、带磨损脏污，整体色调压暗，不做光鲜亮丽的美化",
        "shot": "画面低调布光、大面积阴影和冷调，构图上人物被环境挤压或孤立，光比压抑，不给明亮开阔的喘息",
    },
    "血腥（断肢/内脏/大出血，可到最重）": {
        "story": "伤害写到断肢、内脏、大出血的程度，不回避具体后果",
        "figure": "人物身上有固定的战损印记——旧疤、缺损、久经厮杀留下的痕迹，皮肤或衣甲上有干涸发暗的陈年血渍，不做干净无瑕的处理",
        "shot": "伤口断面结构清楚，血液有真实的黏稠度和流向，浸透布料的地方颜色变深发暗",
    },
    # 成人尺度的人设图不再把一整段文字塞进人物外观。
    # 人物五官、比例、体型和发型仍由同一张人物卡提供；这里只替换姿势和服装。
    "成人向·情爱（诱惑/暧昧/亲密关系）": {
        "story": "面向成年读者，人物之间存在明确的身体吸引、诱惑、暧昧与亲密关系，情欲是推动关系的明确动力，情感推进有动机、有回应并影响后续关系，绝不做少儿化处理",
        "figure": "",
        "figure_pose_male": "前三个全身视图使用同一成年男性展示站姿：双脚分开与肩同宽，重心均匀，肩背打开，双臂稍离躯干自然下垂；只转动人物朝向形成正面、正侧面和背面，身体姿势保持一致",
        "figure_pose_female": "前三个全身视图使用同一成年女性展示站姿：双脚前后错开半步，重心落在后腿，骨盆轻微侧移，肩背舒展，双臂稍离躯干自然下垂；只转动人物朝向形成正面、正侧面和背面，身体姿势保持一致",
        "figure_clothing_male": "成年男性全身不穿服装，仅保留人物卡中原有的固定饰物，三个全身视图保持完全相同的穿着状态",
        "figure_clothing_female": "成年女性全身不穿服装，仅保留人物卡中原有的固定饰物，三个全身视图保持完全相同的穿着状态",
        "shot": "多用贴身镜头与肌肤特写，人物之间常有紧贴、依偎、克制又撩人的触碰，湿润的眼神、放松慵懒的身体语言，渲染浓烈的情欲张力与暧昧氛围；到亲密边缘为止，不拍性行为过程",
    },
    # P360：兜底文案（content_scale.json 读不到时才用），真正的三层文案在 json 里
    "极致诱惑（卖肉向·不露点之外全露）": {
        "story": "成人向作品（三级片口径），人物真实年龄保持原设定，剧情服务于性场面，性爱过程可写；下身用泛称，不写性器官名",
        "figure": "",
        "figure_clothing_male": "成年男性服装采用情趣化剪裁：保留人物原服装的世界风格、主色和材质，改为敞怀或无上衣、低腰结构、束带或皮革配件；胸腹和后背大面积露出，关键部位由不透光的裆片或前片覆盖",
        "figure_clothing_female": "成年女性服装采用不露点之外大面积露肤的情趣化剪裁：保留人物原服装的世界风格、主色和材质，使用高衩、腰侧挖空、露背、下乳短上衣、前后片或绑带结构；乳头由不透光服装边缘覆盖，关键部位由不透光前片、后片或裆片覆盖",
        "shot": "三级片拍法：表情特写为主，侧面中景看两人结合与碰撞，乳头可入镜，性器官不入镜",
    },
}


ADULT_TENDENCY = "成人向·情爱（诱惑/暧昧/亲密关系）"



# 【颜值档必须真的进提示词】审计发现：beauty_tier 被提取、被校验、被存进人物卡，
# 但编译人物图提示词时**一个字都没用上**——"惊艳"和"路人脸"画出来完全一样。
# 于是整条链里没有任何一句话告诉扩散模型"这个人要好看"，主角自然画得平庸。
# 视觉设计强度只管头身比例，脸型定义只描述骨相结构，都不负责"好不好看"。
# 【只说"好看的程度"，不说具体骨相】具体的眼眉鼻唇由六维五官库逐项给，
# 这里再写一遍"骨相精致立体、眉眼有神"就是三个惊艳角色共用同一段模板——
# 越写越像。所以这三句刻意压到二十来字。
BEAUTY_TIER_LOOK = {
    "路人脸": "长相普通不突出，皮肤和气色平常",
    "耐看": "皮肤状态好，五官协调度高，属于越看越舒服的长相",
    "惊艳": "皮肤通透干净，五官协调度极高，有明确的银幕级吸引力",
}
LEAD_LOOK = "体态挺拔，气场鲜明，远看剪影就能一眼认出是主要人物"


def beauty_def(tier, is_lead=False):
    """颜值档 → 可画的描述。主角没写档位时按主角级处理，不留白。"""
    n = str(tier or "").strip()
    if n in BEAUTY_TIER_LOOK:
        base = BEAUTY_TIER_LOOK[n]
        if is_lead and n != "路人脸":
            return base + "；" + LEAD_LOOK
        return base
    return LEAD_LOOK if is_lead else BEAUTY_TIER_LOOK["耐看"]


def _scale_table():
    """尺度文案表：优先读 presets/content_scale.json，读不到用代码里的默认。

    用户 2026-09-01 要求能自己改这些话。文件写坏了退回默认，
    不会因为一处 json 语法错误让整个系统起不来。
    每次调用都重读——文案很短，读一次没成本，改完不用重启。
    """
    import json as _json
    import os as _os
    p = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
        "presets", "content_scale.json")
    try:
        d = _json.loads(open(p, encoding="utf-8").read())
        got = d.get("选项") or d.get("options") or {}
        if isinstance(got, dict) and got:
            return got
    except Exception:
        pass
    return CONTENT_TENDENCY_EFFECT


def tendency_effect(tendencies, layer):
    """把选中的内容倾向翻成某一层能用的描述。layer: story | figure | shot"""
    table = _scale_table()
    out = []
    for t in tendencies or []:
        if str(t) == "成人向":
            t = "成人向·情爱（诱惑/暧昧/亲密关系）"
        v = (table.get(str(t)) or {}).get(layer, "")
        if v and v not in out:
            out.append(v)
    return "，".join(out)


def character_figure_variant(tendencies, sex):
    """人物设定图的尺度变体，只返回姿势和服装替换，不改人物身份。

    按 content_scale.json 的顺序合并：后面的非空服装规则覆盖前面的服装规则，
    没写姿势的尺度不会碰姿势。因此同时勾选成人向和极致诱惑时，结果自然是
    成人向姿势 + 极致诱惑服装。
    """
    sx = str(sex or "").strip()
    if sx not in ("男", "女"):
        return {"pose": "", "clothing": ""}
    selected = set()
    for item in tendencies or []:
        name = ADULT_TENDENCY if str(item) == "" else str(item)
        selected.add(name)
    suffix = "male" if sx == "男" else "female"
    pose = ""
    clothing = ""
    for name, layers in _scale_table().items():
        if name not in selected or not isinstance(layers, dict):
            continue
        p = str(layers.get("figure_pose_" + suffix) or "").strip()
        w = str(layers.get("figure_clothing_" + suffix) or "").strip()
        if p:
            pose = p
        if w:
            clothing = w
    return {"pose": pose, "clothing": clothing}
CUSTOM_GUIDANCE_HINT = ("只写视觉和镜头要求，例如：人物服装以银蓝锁甲为主；城堡使用黑色玄武岩尖拱；"
                        "关键场面采用低机位。故事结局、人物关系等事实请写在“不可更改的故事要求”。")
CUSTOM_CONTENT_SCALE_HINT = ("可填写你自己的内容尺度和表现边界。系统按原文保存并交给写作与分镜链，"
                             "不会替你改写成另一种尺度；留空则只使用上面的选项。")
CHAR_TYPE_DEFS = {
    "人类": "正常人类骨骼、五官与四肢结构",
    "精灵": "修长人形骨骼、明显尖耳、轻盈体态与细长手指",
    "矮人": "矮壮人形体格、宽肩厚躯干、粗壮四肢与厚实手掌",
    "兽人": "人形骨架结合明确的兽耳、兽瞳、毛发或口鼻特征，物种特征前后一致",
    "亡灵": "人形骨架带苍白失血肤色、干枯组织或局部骨骼特征",
    "龙裔": "人形骨架带局部鳞片、角、竖瞳与爪形末端，龙类特征固定一致",
    "机械生命": "仿生人形结构、可见关节分件、金属或复合材料外壳与机械接口",
    "其他非人": "非人种族的骨骼、皮肤、头部与肢体规则按人物文字描述固定",
}

CHAR_FIELDS = {
    # 性别已经有独立字段；这里改为真正有视觉作用的物种类型。
    "人物类型": list(CHAR_TYPE_DEFS),
    "长相": ["自动", "美少年", "俊美", "硬朗", "粗犷", "阴柔", "妖异"],
    "身材": ["自动", "少年薄身", "修长", "极瘦", "匀称", "强壮", "巨型"],
    "性格": ["自动", "温柔", "活泼", "冷静", "强势", "阴郁", "狡黠", "疯狂"],
    "服装轮廓": ["自动", "极贴身", "修身利落", "上紧下宽", "上宽下窄", "短款轻装", "长款修身",
                "宽大垂坠", "多层华服", "巨大披挂", "轻型战斗", "重型装甲", "不对称", "极简少层", "自定义"],
}
BEHAVIOR_PROMISE = (
    "故事只有一名主角就先生成一人；明确写了两三名主要人物就按实际人数生成。"
    "**不会为了凑数自动补女主、导师或同伴。**每个人都能单独修改、换服装、添加、删除和生成三视图。"
)


def load_styles():
    """唯一负责：读 13 个画风预设（名字+完整说明），按 8848 顺序返回。"""
    by = {}
    for f in STYLE_DIR.glob("*.txt"):
        by[f.stem] = f.read_text(encoding="utf-8").strip()
    # 只列四风；旧画风文件保留给老项目按名字取说明，不进下拉
    return [{"name": n, "content": by.get(n, "")} for n in STYLE_NAMES if n in by]


def style_description(name):
    """唯一负责：取某个画风的完整说明文字（选中时显示）。"""
    for s in load_styles():
        if s["name"] == name:
            return s["content"]
    return ""


def load_setting_presets():
    """唯一负责：读「整部作品长什么样」的设定预设（designDirection/world/characters）。"""
    out = []
    for f in sorted(SETTING_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({"name": f.stem,
                        "designDirection": str(d.get("designDirection") or ""),
                        "world": str(d.get("world") or ""),
                        "characters": str(d.get("characters") or "")})
        except Exception:
            continue
    return out


def style_to_profile(name):
    """兼容旧调用；新链保留完整 Krea 名，不再把 12 种画风压成 3 种。"""
    n = str(name or "")
    if style_description(n):
        return n
    if "日漫" in n:
        return "日系动画"
    if "水墨" in n:
        return "东方史诗"
    if "胶片" in n or "剧照" in n or "生活照" in n or "赛博" in n:
        return "电影级写实"
    return "电影级写实"


# ---------- 选项背后的真实内容（从 8848 的 WORLD_DEFS / INTENSITY_DEFS / FACE_DEFS 原样搬来） ----------
# 用户逐个调过的。没有这些定义，选「强设计」只是一个词，模型不知道那是"8–9 头身、
# 小头长腿极窄腰"；选「东方仙侠」也不知道服装是交领长袍、建筑是山门洞府。
# 选项名 → 整段定义，编译提示词时必须带上。

def _load_option_defs():
    import json as _json
    p = ROOT / "presets" / "option_defs.json"
    if p.exists():
        try:
            return _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


_OPT = _load_option_defs()
WORLD_DEFS = _OPT.get("WORLD_DEFS", {})
INTENSITY_DEFS = _OPT.get("INTENSITY_DEFS", {})
FACE_DEFS = _OPT.get("FACE_DEFS", {})


def world_def(name):
    """世界类型的完整定义：服装体系/建筑体系/材料/超自然规则。"""
    return WORLD_DEFS.get(str(name or "").strip(), "")


def intensity_def(name):
    """视觉设计强度的完整定义，含具体头身比数字。"""
    n = normalize_visual_strength(name).replace("（默认）", "").strip()
    return INTENSITY_DEFS.get(n, "")


def normalize_visual_strength(name):
    """旧“极端”并入“强设计”；保留读取兼容，界面不再提供极端。"""
    n = str(name or "").strip()
    return "强设计" if n == "极端" else (n or "自动")


# 【选项按用户想象力重构 2026-08-26】长相=气质词(定义写五官)、身材=大白话档位、
# 服装=看得懂的具体款式。老名/曾用的极致名都映到新标签，旧卡照常解析。
FACE_ALIASES = {
    # P352：长相＝骨相五档。旧标签（精致/清冷/冷艳/艳丽/英气/妖异；帅气/阴柔/冷峻/粗犷/反派）映到新档
    "女": {"精致": "清纯", "清冷": "高冷", "冷艳": "高冷", "艳丽": "诱惑", "英气": "成熟", "妖异": "诱惑",
          "幼态圆脸": "可爱", "精致鹅蛋脸": "清纯", "窄长美型脸": "高冷",
          "冷艳锐脸": "冷艳", "明艳浓颜脸": "艳丽", "英气骨相脸": "英气",
          "妖异美型脸": "妖异", "成熟立体脸": "成熟",
          "极致可爱脸": "可爱", "极致精致脸": "精致", "极致清冷脸": "清冷",
          "极致冷艳脸": "冷艳", "极致浓颜脸": "艳丽", "极致英气脸": "英气",
          "极致妖异脸": "妖异", "极致成熟脸": "成熟"},
    "男": {"帅气": "清秀", "阴柔": "清秀", "冷峻": "硬朗", "粗犷": "硬朗", "反派": "邪魅",
          "幼态少年脸": "少年", "王道美型脸": "清秀", "阴柔窄脸": "清秀",
          "冷峻锐脸": "冷峻", "英气硬朗脸": "硬朗", "成熟立体脸": "成熟",
          "粗犷宽脸": "粗犷", "危险反派脸": "反派",
          "极致少年脸": "少年", "极致王道美型脸": "帅气", "极致阴柔脸": "阴柔",
          "极致冷峻脸": "冷峻", "极致硬朗脸": "硬朗", "极致成熟脸": "成熟",
          "极致粗犷脸": "粗犷", "极致反派脸": "反派"},
}
POSE_ALIASES = {"极致松弛": "松弛自然", "极致冷淡": "安静冷淡", "极致警觉": "警觉克制",
                "极致高傲": "高傲从容", "极致活泼": "活泼外放", "极致妩媚": "妩媚主动",
                "极致阴沉": "阴沉危险", "极致英气": "英气利落", "极致胆怯": "胆怯拘谨"}


def normalize_face_option(sex, face_type):
    """旧长相标签 → 骨相档（别名可能两跳：冷艳锐脸→冷艳→高冷）。不认识的原样返回。"""
    sx = str(sex or "").strip() or "女"
    n = str(face_type or "").strip()
    if n in (FACE_DEFS.get(sx) or {}):
        return n
    tab = FACE_ALIASES.get(sx) or {}
    for _ in range(4):
        m = tab.get(n)
        if not m or m == n:
            break
        n = m
    legacy = ({
        "女": {"可爱": "幼态可爱", "清纯": "清纯初恋", "成熟": "成熟端庄",
               "高冷": "清冷高洁", "诱惑": "柔媚精致", "冷艳": "冷艳锐利",
               "艳丽": "明艳浓颜", "英气": "英气利落", "妖异": "异域立体"},
        "男": {"少年": "幼态少年", "清秀": "清俊少年", "硬朗": "英武硬朗",
               "成熟": "成熟沉稳", "邪魅": "邪魅狡黠", "冷峻": "冷峻锐利",
               "粗犷": "凶悍粗犷", "反派": "邪魅狡黠"},
    }.get(sx) or {})
    return legacy.get(n, n)


def face_def(sex, face_type):
    """骨相定义（男女各 5 档）。旧名先过别名表。"""
    sx = str(sex or "").strip()
    n = normalize_face_option(sx, face_type)
    return (FACE_DEFS.get(sx) or {}).get(n, "")


BODY_DEFS = _OPT.get("BODY_DEFS", {})
HEIGHT_DEFS = _OPT.get("HEIGHT_DEFS", {})
BODY_RATIO_DEFS = _OPT.get("BODY_RATIO_DEFS", {})
HAIR_DEFS = _OPT.get("HAIR_DEFS", {})
POSE_DEFS = _OPT.get("POSE_DEFS", {})
SHEET_POSE_DEFS = _OPT.get("SHEET_POSE_DEFS", {})
CLOTH_DEFS = _OPT.get("CLOTH_DEFS", {})
COMIC_DESIGN_PRESETS = _OPT.get("COMIC_DESIGN_PRESETS", {})
HERO_STYLE_DIRECTIONS = _OPT.get("HERO_STYLE_DIRECTIONS", {})
THEME_ALIAS = _OPT.get("THEME_ALIAS", {})
GENRES = _OPT.get("GENRES", {})
ALBUM_AESTHETICS = _OPT.get("ALBUM_AESTHETICS", {})
CREATIVE_HINTS = _OPT.get("CREATIVE_HINTS", {})


BODY_ALIASES = {
    # 身高已经是独立字段；身材只描述骨架、肌肉、脂肪与曲线。
    # 老项目里把高矮混进身材的标签继续兼容，但统一映到纯身材档位。
    "女": {"高瘦": "纤细", "矮瘦": "纤细", "高壮": "强壮", "匀称结实": "运动紧实",
          "营养不良·皮包骨": "极瘦", "微胖": "偏胖",
          "曲线丰满": "丰满曲线", "丰盈曲线": "强烈曲线",
          "性感火辣": "丰满曲线", "标准": "匀称", "娇小": "纤细", "正常偏瘦": "纤细",
          "极瘦高挑": "极瘦单薄", "线条丰满": "丰满曲线", "健美": "运动紧实", "高大": "强壮",
          "娇小匀称": "匀称", "极娇小纤弱": "极瘦单薄", "极致娇小": "纤细",
          "少女纤薄": "极瘦单薄", "成年纤薄": "极瘦单薄", "极致纤薄": "极瘦单薄",
          "美型高挑": "纤细", "纤细高挑": "纤细", "超高挑夸张": "纤细",
          "极致纤细高挑": "极瘦单薄", "极致曲线": "丰满曲线", "极致沙漏": "丰满曲线",
          "标准匀称": "匀称", "极致健美": "运动紧实", "高大强健": "强壮", "极致高大": "强壮"},
    "男": {"高瘦": "清瘦", "矮壮": "壮硕", "高壮": "强壮", "匀称结实": "匀称",
          "营养不良·皮包骨": "极瘦", "微胖": "偏胖",
          "标准": "匀称", "少年": "匀称", "瘦高": "清瘦", "壮汉": "壮硕",
          "娇小少年": "清瘦", "极致少年感": "清瘦", "标准匀称": "匀称",
          "极瘦纤长": "极瘦单薄", "修长青年": "清瘦", "美型少年": "清瘦",
          "纤细修长": "清瘦", "极致纤细修长": "极瘦单薄",
          "宽肩窄腰": "精壮", "极致倒三角": "精壮", "运动薄肌": "精壮",
          "极致精瘦薄肌": "精壮", "强壮肌肉": "强壮", "极致强壮": "强壮",
          "重型壮汉": "壮硕", "超高大夸张": "壮硕", "极致重型": "壮硕"},
}
MERGED_BODY_DEFS = {"女": {}, "男": {}}   # 定义全在 json 新键下
CLOTH_ALIASES = {
    # 服装=具体款式（json 新键）。老的抽象轮廓名/极致名先并类，再由 MERGED 兜底出定义。
    "宽大垂坠": "宽大披挂", "巨大披挂": "宽大披挂",
    "极致贴身": "极贴身", "极致修身": "修身利落", "极致上紧下宽": "上紧下宽",
    "极致上宽下窄": "上宽下窄", "极致短款": "短款轻装", "极致长款": "长款修身",
    "极致宽大披挂": "宽大披挂", "极致多层华服": "多层华服", "极致轻型战斗": "轻型战斗",
    "极致重型装甲": "重型装甲", "极致不对称": "不对称", "极致极简": "极简少层",
}
MERGED_CLOTH_DEFS = {
    # 旧的抽象轮廓定义留档：老卡上存的轮廓值仍能编进提示词，不出现在下拉里。
    "极贴身": "极致贴身：布料如第二层皮肤，胸腰臀腿轮廓完全显形",
    "修身利落": "极致修身：全身贴合、腰线锋利、线条干净无赘余",
    "上紧下宽": "极致上紧下宽：胸腰紧裹、腰线极细；腰胯以下炸开成巨大裙摆/袍摆",
    "上宽下窄": "极致上宽下窄：肩袖极宽夸张、向下急收，倒梯形剪影一眼可辨",
    "短款轻装": "极致短款：上衣极短、裙裤极短，大面积露腰腹腿，轻盈利落",
    "长款修身": "极致长款修身：长及脚踝、全程贴合身线，纵向线条拉到极致",
    "多层华服": "极致多层华服：领裙披层层叠叠、结构繁复到极点、仪式感拉满",
    "宽大披挂": "极致宽大披挂：宽肩宽袖、衣摆巨大垂坠，体积感和流动感拉满",
    "轻型战斗": "极致轻型战斗：短装+护具+束带，开衩露肤、为高速动作而生",
    "重型装甲": "极致重型装甲：大块金属覆盖全身、轮廓厚重如堡垒",
    "不对称": "极致不对称：左右长度/露肤/披挂差异拉到最大，一半一个世界",
    "极简少层": "极致极简：单层单料、结构最少、线条干净到没有一条多余",
}


def normalize_body_option(sex, body_type):
    n = str(body_type or "").strip()
    sx = str(sex or "女")
    current = BODY_DEFS.get(sx) or {}
    if n in current:
        return n
    old = (BODY_ALIASES.get(sx) or {}).get(n, n)
    legacy = ({
        "女": {"纤细": "瘦", "极瘦单薄": "极瘦", "匀称": "标准",
               "运动紧实": "偏瘦有线条", "丰满曲线": "曲线丰满", "强壮": "偏瘦有线条"},
        "男": {"清瘦": "瘦", "极瘦单薄": "极瘦", "匀称": "标准",
               "精壮": "偏瘦有线条", "强壮": "强壮", "壮硕": "健美"},
    }.get(sx) or {})
    return legacy.get(old, old)


def normalize_cloth_option(name):
    n = str(name or "").strip()
    return CLOTH_ALIASES.get(n, n)


def body_def(sex, body_type):
    """身材定义（男女各若干）。"""
    sx = str(sex or "").strip()
    n = normalize_body_option(sx, body_type)
    return ((MERGED_BODY_DEFS.get(sx) or {}).get(n) or
            (BODY_DEFS.get(sx) or {}).get(n, ""))


def height_def(sex, height_type):
    """身高档定义。精确厘米数属于自由输入，不需要查表。"""
    sx = str(sex or "").strip() or "女"
    return (HEIGHT_DEFS.get(sx) or {}).get(str(height_type or "").strip(), "")


def body_ratio_def(name):
    """只供人物设定图使用的头身与四肢比例；不进入故事和视频提示词。"""
    return BODY_RATIO_DEFS.get(str(name or "").strip(), "")


def hair_def(sex, name):
    """男女分开的发型预设；人物卡也可以保留自定义发型全文。"""
    sx = str(sex or "女").strip()
    return (HAIR_DEFS.get(sx) or {}).get(str(name or "").strip(), "")


def pose_def(name):
    """神态体态定义。旧名先过别名表。"""
    n = str(name or "").strip()
    return POSE_DEFS.get(POSE_ALIASES.get(n, n), "")


def sheet_pose_def(sex):
    """人物设定板的普通展示姿势；成人尺度姿势由内容尺度按性别覆盖。"""
    sx = str(sex or "").strip()
    if sx not in ("男", "女"):
        return ""
    return SHEET_POSE_DEFS.get("普通展示·" + sx, "")


def cloth_def(name):
    """服装款式定义：先查服装库（按世界的具体款式，P352），再查旧的款式/轮廓表。"""
    n = normalize_cloth_option(name)
    try:
        from . import project_prompt as _pp
        for _w, _entries in (_pp.load_outfit_library() or {}).items():
            for _e in _entries or []:
                if str(_e.get("name") or "") == n:
                    return str(_e.get("prompt") or "")
    except Exception:
        pass
    return MERGED_CLOTH_DEFS.get(n) or CLOTH_DEFS.get(n, "")


def world_outfit_names(settings):
    """这个世界的服装库条目名（P352：款式跟着世界走）。库里没有这个世界就返回 []。"""
    try:
        from . import project_prompt as _pp
        world = _pp.resolved_world(settings or {}) if isinstance(settings, dict) else str(settings or "")
        return [str(x.get("name") or "") for x in (_pp.load_outfit_library() or {}).get(world, []) if str(x.get("name") or "").strip()]
    except Exception:
        return []


def char_type_def(name):
    """人物类型是物种，不再与性别重复。旧数据里的男/女按人类兼容。"""
    n = str(name or "").strip()
    if n in ("", "自动", "男", "女"):
        n = "人类"
    if n == "非人":
        n = "其他非人"
    return CHAR_TYPE_DEFS.get(n, "")


def resolve_auto_character(character, settings=None):
    """把“自动”确定性落到有定义的选项，不调用模型。"""
    c = character or {}
    s = settings or {}
    raw_sex = str(c.get("sex") or "").strip()
    if raw_sex in ("男", "男性"):
        sex = "男"
    elif raw_sex in ("女", "女性"):
        sex = "女"
    else:
        _sex_text = " ".join(str(c.get(k) or "") for k in
                             ("name", "role", "identity_anchor", "appearance_details", "look", "other"))
        if any(w in _sex_text for w in ("男主", "男性", "男人", "男孩", "少年", "公子", "少爷", "王子")):
            sex = "男"
        elif any(w in _sex_text for w in ("女主", "女性", "女人", "女孩", "少女", "姑娘", "小姐", "公主")):
            sex = "女"
        else:
            sex = "女"
    text = " ".join(str(c.get(k) or "") for k in
                    ("name", "role", "identity_anchor", "appearance_details", "look", "build",
                     "behavior_anchor", "personality", "clothing", "hair", "other"))

    def choose(table, preferred):
        for name in preferred:
            if name in (table or {}):
                return name
        return next(iter(table or {}), "")

    face = str(c.get("face_type") or "自动")
    if face in ("", "自动", "None"):
        face_tab = FACE_DEFS.get(sex) or {}
        if any(w in text for w in ("粗犷", "佣兵", "壮汉")):
            face = choose(face_tab, ["凶悍粗犷", "英武硬朗"] if sex == "男" else ["英气利落", "冷艳锐利"])
        elif any(w in text for w in ("危险", "反派", "阴沉")):
            face = choose(face_tab, ["冷峻锐利", "邪魅狡黠"] if sex == "男" else ["冷艳锐利", "清冷高洁"])
        elif any(w in text for w in ("书生", "学者", "文雅", "温柔")):
            face = choose(face_tab, ["温润书生", "清俊少年"] if sex == "男" else ["温柔知性", "白月光"])
        else:
            face = choose(face_tab, ["清俊少年", "阳光亲和"] if sex == "男" else ["清纯初恋", "白月光"])
    body = normalize_body_option(sex, c.get("build") or "自动")
    if body in ("", "自动", "None"):
        body_tab = BODY_DEFS.get(sex) or {}
        if any(w in text for w in ("营养不良", "皮包骨", "骨瘦如柴", "长期挨饿")):
            body = choose(body_tab, ["营养不良·皮包骨", "极瘦"])
        elif any(w in text for w in ("强壮", "战士", "骑士", "壮汉")):
            body = choose(body_tab, ["强壮", "健美"] if sex == "男" else ["偏瘦有线条", "标准"])
        elif any(w in text for w in ("瘦", "纤细", "法师")):
            body = choose(body_tab, ["瘦", "偏瘦有线条"])
        else:
            body = choose(body_tab, ["标准"])
    pose = str(c.get("behavior_anchor") or c.get("personality") or "自动")
    if pose in ("", "自动", "None"):
        pose = "警觉克制" if any(w in text for w in ("战", "危险", "警惕", "刺客")) else "松弛自然"
    cloth = normalize_cloth_option(c.get("clothing_requirement") or "自动")
    if cloth in ("", "自动", "None"):
        world = str(s.get("world_type") or "")
        if world in ("未来科幻", "末日"):
            cloth = "轻甲战斗服"
        elif world in ("西方魔幻", "东方仙侠"):
            cloth = "法师长袍" if any(w in text for w in ("贵族", "法师", "祭司", "圣女")) else "古风长袍"
        elif world == "校园":
            cloth = "学院制服"
        else:
            cloth = "休闲便装"
    ctype = str(c.get("char_type") or "人类")
    if ctype in ("", "自动", "男", "女"):
        ctype = "人类"
    elif ctype == "非人":
        ctype = "其他非人"

    ratio = str(c.get("sheet_body_ratio") or "自动")
    if ratio in ("", "自动", "None"):
        style = str(s.get("style") or "")
        if any(w in text for w in ("迷你", "三头身", "3头身", "不到一米", "幼儿体态")):
            ratio = "迷你幼态"
        elif any(w in text for w in ("幼态矮小", "幼年精灵", "小精灵少年", "小男孩", "小女孩", "四头身", "4头身")):
            ratio = "幼态少年"
        elif any(w in text for w in ("矮小", "短手短腿", "小个子")):
            ratio = "小少年比例"
        elif any(w in text for w in ("十一头身", "十二头身", "幻想夸张比例")):
            ratio = "夸张幻想"
        elif any(w in text for w in ("十头身", "大长腿")):
            ratio = "大长腿"
        elif any(w in text for w in ("极度高挑", "九头身")):
            ratio = "高挑模特"
        elif any(w in text for w in ("高挑", "修长", "长腿")):
            ratio = "模特比例"
        elif "电影" in style:
            ratio = "标准成人"
        elif any(w in style for w in ("网红", "动漫", "平面", "3D", "游戏", "卡通")):
            ratio = "高挑模特"
        else:
            ratio = "标准成人"
        ratio = ratio if ratio in BODY_RATIO_DEFS else choose(BODY_RATIO_DEFS, ["标准成人"])

    hair = str(c.get("hair_preset") or "自动")
    if hair in ("", "自动", "None"):
        hair_tab = HAIR_DEFS.get(sex) or {}
        raw_hair = str(c.get("hair") or "")
        hair = next((name for name in hair_tab if name and name in raw_hair), "")
        if not hair:
            keyword_choices = [
                ("狼尾", "狼尾"), ("背头", "背头"), ("寸头", "寸头"),
                ("双马尾", "双马尾"), ("高马尾", "高马尾"), ("低马尾", "低马尾"),
                ("丸子", "丸子头"), ("编发", "侧编发"), ("卷", "自然卷"),
                ("长直", "长直发"), ("短发", "短碎发"),
            ]
            for keyword, name in keyword_choices:
                if keyword in raw_hair and name in hair_tab:
                    hair = name
                    break
        if not hair:
            hair = choose(hair_tab, ["短碎发", "侧分短发"] if sex == "男" else ["锁骨直发", "长直发"])

    outfit = str(c.get("outfit_preset") or "自动")
    if outfit in ("", "自动", "None"):
        outfit = "自定义" if str(c.get("clothing") or "").strip() else ""
        if not outfit:
            outfit = next(iter(world_outfit_names(s)), "自定义")

    return {"sex": sex, "face_type": face, "build": body, "pose": pose,
            "clothing_requirement": cloth, "char_type": ctype,
            "sheet_body_ratio": ratio, "hair_preset": hair,
            "outfit_preset": outfit}


def option_def(kind, *keys):
    """统一取任意一张定义表的内容。kind 见 _OPT 的键名。"""
    tab = _OPT.get(kind) or {}
    for k in keys:
        if not isinstance(tab, dict):
            return ""
        tab = tab.get(str(k or "").strip(), "")
    return tab if isinstance(tab, str) else ""

# ---------- 下拉选项必须用定义表的真实键，否则选了等于没选 ----------
# 之前「长相/身材/性格」三项的选项词是自己编的（美少年/修长/温柔…），
# 而定义表里的键是「精致鹅蛋脸/娇小匀称/松弛自然」——对不上，定义永远取不到。
# 现在下拉直接由定义表生成：改定义表 = 改选项，不会再脱节。

def char_field_options(field, sex=None, settings=None):
    """某个人物字段的下拉选项，直接来自定义表；服装款式按世界（settings）从服装库取。"""
    if field in ("长相", "face_type"):
        tab = FACE_DEFS.get(str(sex or "女")) or {}
        return ["自动"] + list(tab)
    if field in ("身高", "height"):
        tab = HEIGHT_DEFS.get(str(sex or "女")) or {}
        return ["自动"] + list(tab)
    if field in ("身体比例", "sheet_body_ratio"):
        return ["自动"] + list(BODY_RATIO_DEFS)
    if field in ("发型", "hair_preset"):
        tab = HAIR_DEFS.get(str(sex or "女")) or {}
        return ["自动"] + list(tab) + ["自定义"]
    if field in ("身材", "build"):
        tab = BODY_DEFS.get(str(sex or "女")) or {}
        out = []
        for old in tab:
            new = normalize_body_option(sex or "女", old)
            if new not in out:
                out.append(new)
        return ["自动"] + out
    if field in ("性格", "神态体态", "behavior_anchor", "personality"):
        return ["自动"] + list(POSE_DEFS)
    if field in ("服装轮廓", "clothing_requirement"):
        _wn = world_outfit_names(settings) if settings else []
        if _wn:
            return ["自动"] + _wn + ["自定义"]                                 # P352：款式＝这个世界的服装库
        out = []
        for old in CLOTH_DEFS:
            new = normalize_cloth_option(old)
            if new not in out:
                out.append(new)
        return ["自动"] + out + ["自定义"]
    return CHAR_FIELDS.get(field, [])


def char_fields_for(sex=None, settings=None):
    """整套人物字段选项（按性别给对应的脸型/身材；服装款式按世界）。"""
    return {
        # 直接读定义表，不读导入时抄的那份——预设表里新加的种族才会出现在下拉里
        # （2026-09-02 用户实测：加了选项前端不显示，就是这一项）
        "人物类型": list(CHAR_TYPE_DEFS),
        "长相": char_field_options("长相", sex),
        "身体比例": char_field_options("身体比例", sex),
        "身材": char_field_options("身材", sex),
        "发型": char_field_options("发型", sex),
        "性格": char_field_options("性格"),
        "服装轮廓": char_field_options("服装轮廓", settings=settings),
    }

# 主角风格方向（8848 原文，9 条）。用户要求「整部作品长什么样」参考 8848 的设定：
# 那边是一个下拉 + 一段自由文本，不是三个并排的框。
HERO_STYLE_DIRECTIONS = {
    "清俊灵动的年轻主角": "清俊灵动的年轻主角，轮廓轻盈，眼神聪慧，带少年成长感",
    "硬朗英俊的行动型主角": "硬朗英俊的行动型主角，骨相清晰，神态坚定，轮廓利落",
    "成熟沉稳的主角": "成熟沉稳的主角，气质克制，五官耐看，具有可靠的领导感",
    "强健挺拔的英雄型主角": "强健挺拔的英雄型主角，肩背有力量，比例修长，具有正面压场感",
    "敏捷锐利的主角": "敏捷锐利的主角，身形轻捷，线条干净，眼神警觉而有判断力",
    "高贵精致的主角": "高贵精致的主角，仪态挺拔，剪裁考究，具有受过良好教养的从容",
    "冷峻优雅的暗色主角": "冷峻优雅的暗色主角，苍白或冷调肤色，轮廓精致，危险感克制",
    "理性清冷的智慧型主角": "理性清冷的智慧型主角，五官清晰，神态专注，气质简洁高级",
    "历练感鲜明的主角": "历练感鲜明的主角，轮廓粗粝但有吸引力，体态强韧，具有生存者气质",
}


def hero_style_options():
    return ["自动"] + list(HERO_STYLE_DIRECTIONS)


def hero_style_def(name):
    return HERO_STYLE_DIRECTIONS.get(str(name or "").strip(), "")


# ════════ 用户可编辑的预设表（2026-09-01 上线）════════
# 用户要求所有选项和内容都能自己改。文案存在 presets/预设表.json，
# 存盘后下次生成就生效，不用重启。
# 只覆盖文件里写了的表；文件不在、写坏了、或某一项格式不对，
# 那一项就退回代码里的默认——不会因为一处 json 语法错误让系统起不来。

# 每种画风在视频里对应的镜头/质感一句话（进 H3 提示词的【摄影】行）。
# 用户 2026-09-02：视频没有电影感——提示词里一句镜头/光线/色调都没有。正面描述，不写禁令。
H3_LOOK = {
    # P323（用户 9-12 给的网红自然感定义）：人是网红、画面不加修饰——手机手持高度、灰蒙低对比、现场光、噪点
    "网红自然感": "真实影像：现场光有一个明确方向（窗光、天光或斜阳），受光面亮、背光面沉进有层次的阴影，面部能看出明暗交界；色彩自然饱满，材质真实，画面清晰通透，景深适中",
    "超仿真生活照": "真人实拍影像，35mm 电影镜头，浅景深，人物清晰、背景柔和虚化；胶片质感，轻微颗粒，暗部偏青、亮部偏暖，白色不过曝",
    "电影级剧照": "电影正片影像，35mm 电影镜头，浅景深，人物清晰、背景柔和虚化；胶片质感，轻微颗粒，暗部偏青、亮部偏暖，白色不过曝",
    "80年代胶片电影": "1980 年代胶片电影影像，柔和的胶片颗粒和轻微光晕，暖调偏黄，暗部发灰，浅景深",
    "3D游戏模型": "次世代游戏引擎实时渲染的电影化过场镜头，基于物理的光照和材质，浅景深，背景柔和虚化",
    "高质量日漫": "剧场版动画的镜头感：主光方向明确、阴影层次分明，人物清晰、背景有空气透视",
    "黑白日漫": "黑白动画影像，明暗对比强，主光方向明确，背景有层次",
    "复古赛璐璐80年代": "80 年代赛璐璐动画影像，主光方向明确、阴影分明，色彩略褪、有胶片颗粒",
    "厚涂插画": "厚涂插画质感的动态影像，笔触可见，主光方向明确，背景有空气透视",
    "水彩油画手绘": "手绘水彩油画质感的动态影像，主光方向明确，背景有层次和景深",
    "国风水墨写意": "水墨写意质感的动态影像，留白和浓淡分明，主光方向明确",
    "像素复古游戏": "像素艺术影像，主光方向明确，前后景层次分明",
    "赛博霓虹蒸汽波": "霓虹色调的电影影像，冷紫与暖橙对撞，浅景深，湿润的反光面",
    "电影质感": "电影正片影像，35mm 电影镜头，浅景深，人物清晰、背景柔和虚化；胶片质感，轻微颗粒，暗部偏青、亮部偏暖，白色不过曝",
    "写实生活": "真人实拍影像，像手持相机随手拍下的现实场景：自然光、不刻意布光、对比平、色彩接近肉眼，轻微数码噪点，人物清晰、背景不刻意虚化",
    "平面动漫": "纯平面二维动画影像：明暗全靠色块和线条，不做三维光照和体积感；主光方向明确、阴影层次分明，人物清晰、背景有空气透视",
    "3D游戏": "3D 动漫／游戏 CG 动画影像（高预算游戏过场渲染），不是真人实拍：角色是三维动画角色——眼大鼻小下巴尖的动漫化五官、瓷面般光滑的皮肤、大束造型化头发、干净饱和的材质、轮廓光和柔光晕；虚拟摄影机可以大角度、慢推、跟随和环绕，浅景深，背景有体积光和雾",
    "默认": "电影感影像，35mm 电影镜头，浅景深，人物清晰、背景柔和虚化；暗部偏青、亮部偏暖",
}

_PRESET_FILE_MAP = {
    "画风·视频里的镜头质感": "H3_LOOK",
    "世界类型·选项列表": "WORLD_TYPES",
    "世界类型·每种世界的定义": "WORLD_DEFS",
    "画风·选项列表": "STYLE_NAMES",
    "视觉设计强度·选项": "VISUAL_STRENGTHS",
    "视觉设计强度·每档的定义": "INTENSITY_DEFS",
    "内容尺度·选项列表": "CONTENT_TENDENCIES",
    "人物种族·每种的骨骼特征": "CHAR_TYPE_DEFS",
    "脸型·每种的定义": "FACE_DEFS",
    "身高·每档的定义": "HEIGHT_DEFS",
    "人设图身体比例·每档的定义": "BODY_RATIO_DEFS",
    "身材·每种的定义": "BODY_DEFS",
    "发型·每种的定义": "HAIR_DEFS",
    "服装款式·每种的定义": "CLOTH_DEFS",
    "姿态·每种的定义": "POSE_DEFS",
    "人设图普通姿势·按性别": "SHEET_POSE_DEFS",
    "颜值档·每档的写法": "BEAUTY_TIER_LOOK",
    "主角风格·每种的方向": "HERO_STYLE_DIRECTIONS",
}


def preset_file():
    """预设表文件的路径。"""
    import os as _os
    return _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
        "presets", "预设表.json")


def load_presets():
    """读 presets/预设表.json，返回 {表名: 内容}。读不到返回 {}。"""
    import json as _json
    try:
        return _json.loads(open(preset_file(), encoding="utf-8").read())
    except Exception:
        return {}


def _apply_presets():
    """按文件覆盖模块里的预设表。每次调用都重读，改完不用重启。"""
    d = load_presets()
    if not d:
        return 0
    g = globals()
    n = 0
    for title, var in _PRESET_FILE_MAP.items():
        v = d.get(title)
        if v is None or var not in g:
            continue
        old = g[var]
        # 类型要对得上：列表还是列表，字典还是字典，不然下游会炸
        if isinstance(old, (list, tuple)) and isinstance(v, list) and v:
            g[var] = v
            n += 1
        elif isinstance(old, dict) and isinstance(v, dict) and v:
            g[var] = v
            n += 1
    return n


_apply_presets()
