# -*- coding: utf-8 -*-
"""stage1_core.py — 一键到设定：一句话 → 完整故事 + 全部设定卡 + 全部设定图，然后停。

用户定的两级刹车：
    第一级（这里）：正文 + 人物卡/场景卡 + 设定图，全自动，跑完停下等审。
    第二级（审过后）：分镜单元 → 提示词 →（再审）→ 视频。

顺序反转是关键：先有正文，设定从正文里提取——故事需要几个场景就建几张卡，
不再让预设的两三个场景把故事框死。
"""
import json
import re
import time
from pathlib import Path

INS_DIR = Path(__file__).resolve().parent.parent / "presets" / "instructions"



import re as _re
from . import style_presets as _sp
_PERSON_RE = _re.compile(r"(少年|师兄|师妹|修士|人物|人影|身影|敌人|首领|背影|依偎)")


def clean_field(v, scene_field=False):
    """落卡前的字段消毒（模块级，别处也要用）。

    · 提取器时不时给数组，落卡必须是顿号串
    · 场景字段里的人物词全部剔除——实测"前景：青石与少年"写进纵深字段，
      场景图怎么重出都长人
    """
    if isinstance(v, list):
        v = "、".join(str(x) for x in v)
    if scene_field and isinstance(v, str) and _PERSON_RE.search(v):
        v = "，".join(seg for seg in _re.split(r"[，、；;]", v)
                     if not _PERSON_RE.search(seg))
    return v


def build_extract_user(body, settings=None):
    """按正文提取真实设定；内容边界与各生成入口共用。"""
    from . import project_prompt
    from .age_policy import assert_allowed
    assert_allowed(settings=settings, text=body)
    context = project_prompt.story_settings_block(settings or {})
    return ("【项目已定设定】\n%s\n"
            "【人物事实】原文和用户明确给出的年龄、身份、外貌、体型、服装与关系原样继承。"
            "未确定的年龄保持原年龄段或留空，已确认人物不根据内容尺度改变年龄。\n"
            "【正文】\n%s" % (context or "未提供", str(body or "")[:6000]))

def _clean_completion_prefix(value):
    """模型只应给最终字段值；机械去掉“未提及，补全为”这类过程话。"""
    text = str(value or "").strip()
    if "补全为" in text and any(x in text for x in ("未提及", "未写", "没有写")):
        return text.rsplit("补全为", 1)[-1].strip("，,：:（）()。 ")
    return _re.sub(r"^(?:正文)?(?:未提及|未写|没有写)[，,：:\s]*(?:按[^，,：:]*[，,：:\s]*)?(?:补全为)?[：:\s]*",
                   "", text)


def normalize_extracted_character(character, settings=None):
    """把正文提取结果收口到人物卡的正式字段和当前选项。"""
    from . import style_presets as sp
    c = dict(character or {})
    text = " ".join(str(c.get(k) or "") for k in
                    ("name", "alias", "role", "appearance_details", "look"))
    char_types = list(sp.CHAR_TYPE_DEFS)
    char_type = str(c.get("char_type") or "").strip()
    if char_type not in char_types:
        char_type = next((x for x in char_types if x != "人类" and x in text), "人类")
    c["char_type"] = char_type
    c["hair"] = _clean_completion_prefix(c.get("hair"))
    appearance = _clean_completion_prefix(c.get("appearance_details") or c.get("look"))
    # 模型偶尔会把别人的特征搬过来再用括号自我解释；整段都不能进固定人物卡。
    appearance = _re.sub(
        r"[^，；;]*[（(][^）)]*(?:虽为|不是该人物|他人特征|守卫特征)[^）)]*[）)]",
        "", appearance)
    # 临时状态不能冻结成人物主设定；疤痕、胎记等永久特征保留。
    temporary = ("赤脚", "光脚", "淋湿", "湿透", "沾血", "血迹", "泥污", "灰尘", "伤口", "手持")
    appearance = "，".join(
        part.strip() for part in _re.split(r"[，；;]", appearance)
        if (part.strip() and not any(word in part for word in temporary)
            and not _re.search(r"(?:眼神|神情|表情).*(?:转为|变得|从.+到)", part)))
    c["appearance_details"] = appearance
    c["look"] = appearance  # 只读兼容旧页面/旧报告；落卡改用 appearance_details。

    sex = str(c.get("sex") or "女").strip() or "女"
    valid_face = sp.char_field_options("长相", sex)[1:]
    valid_body = sp.char_field_options("身材", sex)[1:]
    valid_pose = list(sp.POSE_DEFS)
    valid_cloth = sp.char_field_options("服装轮廓")[1:-1]
    if c.get("face_type") not in valid_face:
        c["face_type"] = ""
    c["build"] = sp.normalize_body_option(sex, c.get("build"))
    if c.get("build") not in valid_body:
        c["build"] = ""
    if c.get("behavior_anchor") not in valid_pose:
        c["behavior_anchor"] = ""
    c["clothing_requirement"] = sp.normalize_cloth_option(c.get("clothing_requirement"))
    if c.get("clothing_requirement") not in valid_cloth:
        c["clothing_requirement"] = ""
    clothing = str(c.get("clothing") or "")
    clothing = _re.sub(r"[（(][^）)]*(?:沾满|沾血|泥泞|湿透|破损|撕裂)[^）)]*[）)]", "", clothing)
    clothing = "，".join(
        part.strip() for part in _re.split(r"[，；;]", clothing)
        if part.strip() and not any(word in part for word in ("铁枷", "锁链", "手铐", "绳索")))
    c["clothing"] = clothing
    try:
        from . import project_prompt
        valid_outfits = project_prompt.outfit_preset_options(settings or {})[1:-1]
        if c.get("outfit_preset") not in valid_outfits:
            c["outfit_preset"] = "自动"
        garment_words = ("袍", "裙", "衫", "衣", "裤", "甲", "靴", "鞋", "外套")
        if len(clothing) < 40 or sum(w in clothing for w in garment_words) < 3:
            c["clothing"] = project_prompt.complete_outfit(c, settings or {}, seed="extract")[0]
    except Exception:
        c["outfit_preset"] = str(c.get("outfit_preset") or "自动")
    return c


def framework_text(saga, only_names=None, only_places=None):
    """把整部框架拍平成一段可读文本，当作提取人物/场景的依据。

    【为什么不再从正文抽】人物设定本该是**输入**，不是正文的产物。
    原来的顺序是"写完正文 → 从正文里抽人物卡"，于是设定页填的人和正文写出来的人
    是两套东西在打架，而且改一次设定整篇正文就作废。
    现在改成从框架抽：框架每集写了出场人物、地点和剧情简介，成本极低、
    改起来几秒钟，用户可以在没写一个字正文之前就把人和场景定死。
    """
    sg = saga or {}
    b = sg.get("bible") or {}
    lines = []
    if b.get("premise"):
        lines.append("整部前提：" + str(b["premise"]))
    if b.get("world"):
        lines.append("世界怎么运转：" + str(b["world"]))
    pro = b.get("protagonist") or {}
    if pro.get("name"):
        lines.append("主角：%s（%s）" % (pro.get("name"), pro.get("lack") or ""))
    opp = b.get("opponent") or {}
    if opp.get("name"):
        lines.append("主要对手：%s（%s）" % (opp.get("name"), opp.get("just_goal") or ""))
    cast = cast_of(sg)
    if cast:
        lines.append("人物表：" + "；".join(
            "%s（%s%s%s）" % (x.get("name"),
                             str(x.get("sex") or ""), str(x.get("age") or ""),
                             ("，" + str(x.get("role"))) if x.get("role") else "")
            for x in cast.values()))
    # 【为什么要能只挑相关的集】设计 4 个人的外观，用不着把 12 集剧情全读一遍。
    # 输入越长，模型越慢、越容易跑偏，也越容易把输出挤爆。只喂他们出场的那几集就够。
    want_n = set(only_names or [])
    want_p = set(only_places or [])
    for e in sg.get("episodes") or []:
        if want_n or want_p:
            hit = (want_n & set(e.get("chars") or [])) or (want_p & set(e.get("locations") or []))
            if not hit:
                continue
        seg = ["第%s集《%s》" % (e.get("no"), e.get("title") or "")]
        if e.get("logline"):
            seg.append("路标：" + str(e["logline"]))
        if e.get("brief"):
            seg.append("剧情：" + str(e["brief"]))
        if e.get("chars"):
            seg.append("出场人物：" + "、".join(e["chars"]))
        if e.get("locations"):
            seg.append("地点：" + "、".join(e["locations"]))
        if e.get("hook"):
            seg.append("结尾钩子：" + str(e["hook"]))
        lines.append("\n".join(seg))
    return "\n\n".join(lines)


def cast_of(saga):
    """整部人物表（bible.cast）按名字索引。

    【为什么要有这张表】性别、年龄、身份这些事实，框架那一步本来就知道，
    却没往下传——后面建人物卡时只好让模型看着名字重猜一遍，猜出来一水儿全是女。
    真正的解法不是加一句"不许一律填女"（否定式指令基本没用），
    而是**在源头就把事实定死，往下只传不猜**。
    """
    out = {}
    for x in ((saga or {}).get("bible") or {}).get("cast") or []:
        if isinstance(x, dict) and str(x.get("name") or "").strip():
            out[str(x["name"]).strip()] = x
    return out


def roster_from_saga(saga):
    """框架里点过名的人和地点，以及各自第一次出现在第几集。龙套不进名单。"""
    chars, places = {}, {}
    cast = cast_of(saga)
    for e in sorted((saga or {}).get("episodes") or [],
                    key=lambda x: int(x.get("no") or 1)):
        no = int(e.get("no") or 1)
        for n in e.get("chars") or []:
            name = str(n).strip()
            if str((cast.get(name) or {}).get("importance") or "") == "龙套":
                continue          # 龙套不建卡、不画图，省的就是这部分开销
            chars.setdefault(name, no)
        for n in e.get("locations") or []:
            places.setdefault(str(n).strip(), no)
    return chars, places


def extract_setup_from_framework(saga, settings=None, call_model=None, upto_episode=1):
    """框架 → {world_type, characters, scenes}，每张卡带首次出场话次。

    人物只建框架里点过名的，路人不建卡。

    【场景为什么只建到第 upto_episode 话】用户定的：场景由剧本和故事决定，
    跟着话走，不属于要冻结的设定。而且实测里 12 集框架点了 19 个地点，
    连同 5 个人物一次性要模型吐完，输出直接被 token 上限截断，
    整步失败退回空白卡。只建当前需要的那几个，一次调用就绰绰有余，
    后面的话次在故事页点「生成本话场景」按需补。
    """
    body = framework_text(saga)
    chars, all_places = roster_from_saga(saga)
    places = {k: v for k, v in all_places.items() if int(v or 1) <= int(upto_episode or 1)}
    if not places:
        places = dict(list(all_places.items())[:2])
    # 【这一步只设计外观，不再推断身份】
    # 姓名、性别、年龄、身份、和主角的关系，框架那一步已经在 cast 人物表里定死了。
    # 这里把它们当**已知事实**逐字传下去，模型只需要做一件事：设计长相和衣服。
    #
    # 之前的做法是让模型看着名字自己猜性别，猜出来一水儿全是女；
    # 我一度加了句"不许一律填女"——否定式指令对模型基本没用，
    # 而且本质上是在下游擦上游的屁股。事实在上游就有，往下传就行了。
    cast = cast_of(saga)

    def _known(name):
        c = cast.get(name) or {}
        bits = [x for x in (c.get("sex"), c.get("age"), c.get("role"), c.get("relation")) 
                if str(x or "").strip()]
        return "%s（%s）" % (name, "，".join(str(x) for x in bits)) if bits else name

    DESIGN = ("\n\n【这一步只做外观设计】上面是整部框架。人物的姓名、性别、年龄和身份"
              "已经在下面逐个给你了，那些是**已定事实，逐字照抄进对应字段，不许改**。"
              "你要做的只有一件事：按这个人的身份、在剧情里干什么、以及已定的世界类型和画风，"
              "替他设计外观。必须给出 face_type、build、behavior_anchor、"
              "clothing_requirement、outfit_preset、hair、appearance_details、beauty_tier。"
              "appearance_details 写够 30 字，包含发型发色、体貌特征和一处固定识别物。"
              "主角的 beauty_tier 填「惊艳」，主要角色填「惊艳」或「耐看」，次要角色填「耐看」。")

    # 【为什么要分批】一次让模型吐十来个人物的完整 JSON，输出会被 token 上限截断，
    # 整步失败退回空白卡——实测栽过两次。每批最多 4 个人，一次调用绰绰有余；
    # 批次之间互不影响，某一批写砸了也只影响那几个人。
    CHUNK = 4
    names = list(chars)
    merged = {"world_type": "", "characters": [], "scenes": []}
    fails = []
    for i in range(0, len(names), CHUNK):
        part = names[i:i + CHUNK]
        ask = DESIGN + ("\n【这一批只做这几个人，别的人一个都不要输出】"
                        + "；".join(_known(x) for x in part)
                        + "。name 逐字照抄，括号里的性别年龄身份也逐字填进 sex/age/role。")
        try:
            got = extract_setup(framework_text(saga, only_names=part) + ask,
                                settings, call_model, require=("characters",)) or {}
            for c in got.get("characters") or []:
                nm = str(c.get("name") or "").strip()
                if nm not in part:
                    continue
                # 人物表里的事实一律以表为准，不接受模型改写
                known = cast.get(nm) or {}
                for k in ("sex", "age", "role"):
                    if str(known.get(k) or "").strip():
                        c[k] = str(known[k]).strip()
                merged["characters"].append(c)
            merged["world_type"] = merged["world_type"] or got.get("world_type") or ""
        except Exception as ex:
            fails.append("%s（%s）" % ("、".join(part), str(ex)[:60]))
    # 名单里漏掉的人照样建卡，只是外观留白——名字和身份对得上比外观全更重要
    have = {str(c.get("name") or "").strip() for c in merged["characters"]}
    if len(have) < len(names):
        miss = [n for n in names if n not in have]
        stub = _fallback_setup({n: chars[n] for n in miss}, {}, settings)
        for c in stub["characters"]:
            known = cast.get(c["name"]) or {}
            for k in ("sex", "age", "role"):
                if str(known.get(k) or "").strip():
                    c[k] = str(known[k]).strip()
        merged["characters"] += stub["characters"]
        fails.append("这几个人只建了空白卡：" + "、".join(miss))

    # 场景单独一次，只做当前话次要用的那几个
    if places:
        ask = ("\n\n【这一次只做场景，不要输出人物】必须逐个建卡的地点："
               + "、".join(places)
               + "。地点名逐字照抄。每个地点的 scale 和 depth 按它的实际大小给真实米数，"
                 "不许所有地点都写成同一个尺寸。")
        try:
            got = extract_setup(framework_text(saga, only_places=list(places)) + ask,
                                settings, call_model, require=("scenes",)) or {}
            merged["scenes"] = [x for x in (got.get("scenes") or [])
                                if str(x.get("name") or "").strip() in places]
        except Exception as ex:
            fails.append("场景细节（%s）" % str(ex)[:60])
    if not merged["scenes"]:
        merged["scenes"] = _fallback_setup({}, places, settings)["scenes"]
    got = merged
    got["world_type"] = got.get("world_type") or ""
    if fails:
        got["_issues"] = "自动补细节有 %d 处没成：%s" % (len(fails), "；".join(fails)[:220])
    for c in got.get("characters") or []:
        c["first_episode"] = chars.get(str(c.get("name") or "").strip(), 1)
    for x in got.get("scenes") or []:
        x["episode"] = all_places.get(str(x.get("name") or "").strip(), 1)
    got["_later_places"] = [k for k in all_places if k not in places]
    return got


def _fallback_setup(chars, places, settings=None):
    """模型没写出合格设定时的确定性兜底：照框架的名单建骨架卡，不调用模型。"""
    from . import project_prompt
    world = project_prompt.resolved_world(settings or {})
    return {
        "world_type": world,
        "characters": [{"name": n, "role": "主角" if i == 0 else "", "sex": "女", "age": "",
                        "char_type": "人类", "face_type": "自动", "build": "自动",
                        "behavior_anchor": "自动", "clothing_requirement": "自动",
                        "outfit_preset": "自动",
                        # 兜底时第一个人默认按主角处理：颜值档是唯一能让模型
                        # 把人画好看的开关，给"耐看"等于主角一开始就输在起跑线。
                        "beauty_tier": "惊艳" if i == 0 else "耐看",
                        "outfit_tier": "日常", "appearance_details": ""}
                       for i, n in enumerate(chars)],
        "scenes": [{"name": n, "space": n, "scale": "宽 8 米、进深 10 米",
                    "depth": "近景 2 米、中景 5 米、远景 10 米",
                    "materials": "", "main_light": "", "time": "", "landmarks": ""}
                   for n in places],
    }


def _coerce_shape(d):
    """把模型给的 characters / scenes 强行掰成"一串字典"。

    容忍三种常见跑偏：
      1. 整个列表被包了一层：[[{...}, {...}]]
      2. 元素是字符串："林恩" → {"name": "林恩"}
      3. 元素是列表：["林恩", "22岁"] → {"name": "林恩"}
    掰不动的元素直接丢掉，不让它污染后面的校验。
    """
    # 顶层被写成数组是最常见的一种：[{...}] 或 [[{...}]]。
    # 只处理数组元素、不处理顶层的话，后面 d.get() 照样抛 AttributeError——
    # 那正是「'list' object has no attribute 'get'」的真正来源。
    while isinstance(d, list) and d:
        nxt = next((x for x in d if isinstance(x, (dict, list))), None)
        if nxt is None:
            return {}
        d = nxt
    if not isinstance(d, dict):
        return {}
    for key in ("characters", "scenes"):
        v = d.get(key)
        if isinstance(v, dict):
            v = [v]
        if not isinstance(v, list):
            d[key] = []
            continue
        # 多包了一层
        if len(v) == 1 and isinstance(v[0], list):
            v = v[0]
        out = []
        for x in v:
            if isinstance(x, dict):
                out.append(x)
            elif isinstance(x, str) and x.strip():
                out.append({"name": x.strip()})
            elif isinstance(x, (list, tuple)) and x:
                first = next((y for y in x if isinstance(y, str) and y.strip()), "")
                if first:
                    out.append({"name": first.strip()})
        d[key] = out
    return d


def extract_setup(body, settings=None, call_model=None, require=("characters", "scenes")):
    """正文 → {world_type, characters, scenes}。正文写了的逐字继承。

    require 决定这一次**必须**产出什么。分批抽取时只要人物或只要场景，
    校验就不该因为另一半是空的而判失败。
    """
  
    from . import style_presets as sp
    from . import project_prompt
    outfit_options = project_prompt.outfit_preset_options(settings or {})[1:-1]
    ins += ("\n\n## 当前人物卡合法选项（必须逐字选择）\n"
            "char_type：%s\n男 face_type：%s\n女 face_type：%s\n"
            "男 build：%s\n女 build：%s\nbehavior_anchor：%s\nclothing_requirement：%s\n"
            "outfit_preset：%s"
            % ("、".join(sp.CHAR_TYPE_DEFS),
               "、".join(sp.char_field_options("长相", "男")[1:]),
               "、".join(sp.char_field_options("长相", "女")[1:]),
               "、".join(sp.char_field_options("身材", "男")[1:]),
               "、".join(sp.char_field_options("身材", "女")[1:]),
               "、".join(sp.POSE_DEFS),
               "、".join(sp.char_field_options("服装轮廓")[1:-1]),
               "、".join(outfit_options)))
    user = build_extract_user(body, settings)

    def _need(d):
        # 【为什么先做形状归一】模型偶尔会把 characters 写成
        # [["林恩", "22岁"], ...] 或者 [{...}, "米娅"] 这种混合结构。
        # 原来直接 c.get(...) 会抛 AttributeError，而 chat_json 把**任何**异常
        # 都当成"这次输出不合格"，重试三次后整条链报
        # 「模型连续 3 次没能给出合格 JSON：'list' object has no attribute 'get'」。
        # 那是个假象——JSON 本身是好的，只是形状没对上。先归一，再校验，
        # 报错也要说人话。
        cs, ss = d.get("characters"), d.get("scenes")
        if "characters" in require and not cs:
            raise ValueError("没提取出人物")
        if "scenes" in require and not ss:
            raise ValueError("没提取出场景")
        for c in cs:
            # 允许起新名，但正文里的称呼（alias）必须真实存在，防止凭空造人
            probe = str(c.get("alias") or c.get("name") or "")
            if probe not in body and str(c.get("name") or "") not in body:
                raise ValueError("人物「%s」的称呼「%s」在正文里不存在" % (c.get("name"), probe))
            if c.get("beauty_tier") not in ("路人脸", "耐看", "惊艳"):
                raise ValueError("人物「%s」缺颜值档（路人脸/耐看/惊艳）" % c.get("name"))
            if c.get("outfit_tier") not in ("朴素", "日常", "华丽"):
                raise ValueError("人物「%s」缺华丽度档（朴素/日常/华丽）" % c.get("name"))
        for scene in ss:
            scale_numbers = _re.findall(r"\d+(?:\.\d+)?", str(scene.get("scale") or ""))
            depth_numbers = _re.findall(r"\d+(?:\.\d+)?", str(scene.get("depth") or ""))
            if len(scale_numbers) < 2:
                raise ValueError("场景「%s」的 scale 没有真实米数" % scene.get("name"))
            if not depth_numbers:
                raise ValueError("场景「%s」的 depth 没有真实米数" % scene.get("name"))

    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=6000, **kw)
    d, _ = chat_json(ins, user, call_model, validate=_need, tries=3,
                     normalize=_coerce_shape, temperature=0.4)
    d["characters"] = [normalize_extracted_character(c, settings) for c in (d.get("characters") or [])]
    return d


def run_stage1(sid, one_line, settings=None, on_step=None, call_model=None):
    """全自动第一级。返回 {story, characters, scenes, world_type}。

    每一步的产物即时落库——中途断了，已完成的部分还在。
    """
    from . import story_core, story_gen, asset_core

    def step(msg):
        if on_step:
            on_step(msg)

    st = story_core.get_story(sid)
    if not st:
        raise RuntimeError("故事不存在：" + sid)
    settings = dict(st.get("settings") or {}, **(settings or {}))

    # 1) v2 故事链：故事核 → 骨架 → 初稿 → 医生诊断 → 终稿
    #    用户的判断：故事不行，后面全盘皆输——火力重心在这一步
    body, meta = story_gen.generate_story_v2(one_line, settings, call_model, on_step=step)
    # 正文从正门入库：story_layer 是故事栏的事实源（带"用户手改不许覆盖"防线），
    # 直接塞 st["story"] 页面根本读不到——实测故事页显示 0 字
    from . import story_layer
    story_layer.adopt_generated_body(sid, body)
    st = story_core.get_story(sid)
    st["story"] = body
    st["skeleton"] = meta.get("skeleton")
    st["story_core_meta"] = meta.get("core")
    st["story_diagnosis"] = meta.get("diagnosis")
    st.setdefault("settings", {})["one_line"] = one_line
    story_core.save_story(st)

    # 2) 从正文提取全部设定
    step("从正文提取人物和场景设定")
    setup = extract_setup(body, settings, call_model)
    world = setup.get("world_type") or ""
    if world or setup.get("look"):
        from . import project_prompt
        st = story_core.get_story(sid)
        changed = False
        if world:
            st.setdefault("settings", {})["world_type"] = world
            changed = True
        look_panel = st.setdefault("settings", {}).setdefault("look_panel", {})
        generated_look = str(setup.get("look") or project_prompt.default_look(settings)).strip()
        if generated_look and not str(look_panel.get("look") or "").strip():
            look_panel["look"] = generated_look
            changed = True
        if changed:
            story_core.save_story(st)

    # 2.5) 起了正式名的：把正文和骨架里的称呼统一替换成正式名，
    #      整条链（单元/提示词/绑图）只认一个身份
    renames = [(str(c.get("alias") or ""), str(c.get("name") or ""))
               for c in setup.get("characters") or []
               if c.get("alias") and c.get("name") and c["alias"] != c["name"]]
    if renames:
        st = story_core.get_story(sid)
        b2 = st.get("story") or ""
        for a, n in renames:
            b2 = b2.replace(a, n)
        st["story"] = b2

        def _ren(x):
            if isinstance(x, str):
                for a, n in renames:
                    x = x.replace(a, n)
                return x
            if isinstance(x, list):
                return [_ren(v) for v in x]
            if isinstance(x, dict):
                return {k: _ren(v) for k, v in x.items()}
            return x
        st["skeleton"] = _ren(st.get("skeleton") or {})
        story_core.save_story(st)
        body = b2

    _s = clean_field

    # 3) 建卡（已有同名卡就更新，不重复建）
    step("写入人物卡和场景卡")
    have_c = {c.get("name"): c for c in asset_core.list_assets(sid, "characters")}
    for c in setup.get("characters") or []:
        fields = {"name": c.get("name"), "age": c.get("age"), "sex": c.get("sex"),
                  "char_type": c.get("char_type"), "face_type": c.get("face_type"),
                  "build": c.get("build"), "behavior_anchor": c.get("behavior_anchor"),
                  "appearance_details": c.get("appearance_details"), "hair": c.get("hair"),
                  "clothing_requirement": c.get("clothing_requirement"),
                  "outfit_preset": c.get("outfit_preset"),
                  "clothing": c.get("clothing"),
                  "identity_anchor": c.get("identity_anchor"),
                  "beauty_tier": c.get("beauty_tier"),
                  "outfit_tier": c.get("outfit_tier"), "role": c.get("role")}
        fields = {k: _s(v) for k, v in fields.items()}

        # 保存真实人物资料；内容是否合法在生成入口判断，不改年龄换取通过。

        old = have_c.get(c.get("name"))
        if old:
            old.update({k: v for k, v in fields.items() if v})
            old["updated"] = time.time()
            asset_core.save_asset(sid, "characters", old)
        else:
            made = asset_core.create_character(sid, fields)
            # create_character 只收白名单字段，档位这类新键会被丢——建完补写一次
            extra = {k: v for k, v in fields.items()
                     if v and k not in (made or {})}
            if made and extra:
                made.update(extra)
                asset_core.save_asset(sid, "characters", made)
    have_s = {x.get("name"): x for x in asset_core.list_assets(sid, "scenes")}
    for s in setup.get("scenes") or []:
        fields = {"name": s.get("name"), "space": s.get("space"),
                  "contract_text": s.get("space"), "scale": s.get("scale"),
                  "depth": s.get("depth"), "materials": s.get("materials"),
                  "main_light": s.get("main_light"), "time": s.get("time"),
                  "landmarks": s.get("landmarks")}
        fields = {k: _s(v, scene_field=True) for k, v in fields.items()}
        old = have_s.get(s.get("name"))
        if old:
            old.update({k: v for k, v in fields.items() if v})
            old["updated"] = time.time()
            asset_core.save_asset(sid, "scenes", old)
        else:
            made = asset_core.create_scene(sid, fields)
            extra = {k: v for k, v in fields.items() if v and k not in (made or {})}
            if made and extra:
                made.update(extra)
                asset_core.save_asset(sid, "scenes", made)

    # 4) 设定图（Krea）：每人四视图 + 每场景基准图，出完自动采用第一版。
    #    你嫌哪张不行，资源页换一版或重生成——增量重出只动它下游。
    step("生成设定图")
    made = []
    for c in asset_core.list_assets(sid, "characters"):
        if any(v.get("owner_id") == c.get("character_id") and v.get("status") == "adopted"
               for v in asset_core.list_assets(sid, "visuals")):
            continue
        step("生成设定图：" + str(c.get("name")))
        try:
            v = asset_core.generate_character_master(sid, c["character_id"],
                                                     style_profile=str((settings or {}).get("style") or "电影级写实"))
            if isinstance(v, tuple):
                v = v[0]          # 返回的是 (visual, prompt) 元组，实测栽在这
            if v and v.get("visual_id"):
                asset_core.adopt(sid, v["visual_id"])
                made.append(c.get("name"))
        except Exception as e:
            step("人物图失败（不拦断）：%s %s" % (c.get("name"), str(e)[:80]))
    for s in asset_core.list_assets(sid, "scenes"):
        if any(v.get("owner_id") == s.get("scene_id") and v.get("status") == "adopted"
               for v in asset_core.list_assets(sid, "visuals")):
            continue
        step("生成场景图：" + str(s.get("name")))
        try:
            v = asset_core.generate_scene_master(sid, s["scene_id"],
                                                 style_profile=str((settings or {}).get("style") or "电影级写实"))
            if isinstance(v, tuple):
                v = v[0]
            if v and v.get("visual_id"):
                asset_core.adopt(sid, v["visual_id"])
                made.append(s.get("name"))
        except Exception as e:
            step("场景图失败（不拦断）：%s %s" % (s.get("name"), str(e)[:80]))
    step("第一级完成，停下等审")
    return {"story_chars": len(setup.get("characters") or []),
            "scenes": len(setup.get("scenes") or []),
            "images": made, "world_type": world, "body_len": len(body)}
