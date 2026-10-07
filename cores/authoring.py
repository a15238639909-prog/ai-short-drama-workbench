# -*- coding: utf-8 -*-
"""authoring.py — 五角色创作流水线：小说家/角色设计师/编剧/(场景设计师/导演另有模块)。

链：一句话 →〔简介〕→〔第一话原文·小说家〕→〔人设·角色设计师〕→〔剧本·编剧〕→ 分镜(director.py)。
代码只递事实（世界/尺度/要求/人物）+ 调 Qwen + 解析；内容全由 Qwen 写。
指令词见 presets/instructions/*_指令词.txt。
"""
import io
import json
import os
import re
import time
from pathlib import Path
from .age_policy import AgePolicyError, assert_allowed, age_number, is_minor

INS = Path(__file__).resolve().parent.parent / "presets" / "instructions"


def _scale(settings, layers=("story",)):
    """内容尺度 → 具体指导。不再传光标签，走 tendency_effect 的分层细描述。
    layers 可给多层（story 写故事、figure 管外观、shot 管画面），合并去重。"""
    if isinstance(layers, str):
        layers = (layers,)
    s = settings or {}
    parts = []
    try:
        from .style_presets import tendency_effect
        for L in layers:
            t = (tendency_effect(s.get("content_tendencies") or [], L) or "").strip()
            if t and t not in parts:
                parts.append(t)
    except Exception:
        j = "、".join(str(x) for x in (s.get("content_tendencies") or []) if x)
        if j:
            parts = [j]
    base = "；".join(parts)
    custom = str(s.get("custom_content_scale") or "").strip()
    if custom:
        # 【自定义尺度接口】用户自己写的这段单独成行、逐字遵守、优先级高于以上任何预设，
        # 预设选项不许覆盖或改写它。story/figure/shot 各层都会带上这一段。
        tail = "【用户自定义尺度——逐字遵守，优先级高于以上任何预设】：" + custom
        return (base + "\n" + tail) if base else tail
    return base or "全年龄"


# 人物卡上给出图用的参数：面料规格和五官测量。这些词进了正文就是「卡片贴在小说里」
# （2026-09-07 十八话查出 40 句）。喂给小说家的人物块里先剔掉。
_SPEC_WORDS = ("高支数", "高支", "支数", "棉质", "羊毛混纺", "哑光合成纤维", "合成纤维", "涤纶",
               "真丝", "亚麻", "府绸", "细条纹丝绸", "丝绸", "针织", "面料", "头身比")
_FACE_PARAM = ("眼裂", "眼尾", "眼型", "颧骨", "下颌线", "面部骨骼", "骨骼感", "冷白皮", "肤色",
               "瞳色", "唇形", "眉形", "鼻梁", "发际线", "身高")


def _char_mark(c):
    """从卡上挑一个「肉眼一眼能认出」的记号：剔掉面料规格和五官测量，取第一个短分句。"""
    import re as _re
    src = str(c.get("appearance_details") or c.get("appearance") or "") + "，" + str(c.get("clothing") or "")
    for w in _SPEC_WORDS:
        src = src.replace(w, "")
    segs = [x.strip() for x in _re.split(r"[，。；、,;\n]", src) if x.strip()]
    for x in segs:
        if any(w in x for w in _FACE_PARAM):
            continue
        if 2 <= len(x) <= 14:
            return x
    return ""


def _char_block(characters):
    """已定人物 → 喂给小说家的一段。没有就空。

    只给名字、年龄性别和一个记号。**不给面料规格和五官参数**——给它什么它抄什么，
    实测「深灰色的高支数棉质圆领T恤」「眼尾微微下垂，不笑时眼神专注」整句被抄进小说
    （和 D5「不给范文」是同一条）。出图和视频的一致性靠人设图和参考图，不靠正文。
    """
    if not characters:
        return ""
    lines = []
    for c in characters:
        mk = _char_mark(c)
        lines.append("- %s（%s%s）%s" % (c.get("name") or "", c.get("age") or "", c.get("sex") or "",
                                        ("｜认人的记号：" + mk) if mk else ""))
    return ("【已定人物（故事围绕他们写，名字不许改）】\n" + "\n".join(lines)
            + "\n外貌只用来认人：每个人第一次出场提一次记号就够了，"
              "**不许写布料、支数、领型、瞳色、眼裂、眼尾、颧骨、下颌线这些参数**——那是给画图用的，不是小说。")


def _scale_block(settings, layers=("story",)):
    """内容尺度块。**没选任何尺度就返回空字符串**——整行都不出现。
    以前不管选没选都硬塞一行「内容尺度：全年龄」，是纯噪音（用户 2026-08-27 指出）。"""
    s = settings or {}
    picked = [x for x in (s.get("content_tendencies") or []) if x]
    custom = str(s.get("custom_content_scale") or "").strip()
    if not picked and not custom:
        return ""
    return "内容尺度：" + _scale(s, layers)


def _world_block(settings):
    """世界类型：标签 + 完整定义。
    以前只给「世界：校园」四个字，那张定义表（现代学校环境、制服便服运动服、
    环境保持现实校园逻辑）只在界面「详细」里展示，从没喂给过 Qwen。"""
    from . import style_presets as sp
    w = sp.world_group(str((settings or {}).get("world_type") or "").strip())
    if not w:
        return "世界：按一句话/简介自己判断合适的世界（东方/西方/未来/现代四选一）"
    d = (sp.world_def(w) or sp.world_def(sp.world_legacy_key(w)) or "").strip()
    out = "世界：" + w
    if d:
        out += "\n  └ " + d
    return out


def _strength_block(settings):
    """视觉设计强度：标签 + 完整定义（含头身比数字）。
    以前只给「美型」两个字，Qwen 不知道那是 7.8–8.5 头身还是别的。"""
    from . import style_presets as sp
    v = str((settings or {}).get("visual_strength") or "").strip()
    if not v or v == "自动":
        return ""
    d = (sp.intensity_def(v) or "").strip()
    out = "视觉设计强度：" + v
    if d:
        out += "\n  └ " + d
    return out


def _style_block(settings):
    """画风：名字 + 这个画风的质感描述。只给画面链（写小说不需要知道画风）。"""
    st = str((settings or {}).get("style") or "").strip()
    if not st:
        return ""
    tex = ""
    try:
        import json as _json
        rn = _json.loads((INS.parent / "render_notes.json").read_text(encoding="utf-8"))
        tex = str((rn.get(st) or {}).get("char") or "").strip()
    except Exception:
        tex = ""
    out = "画风：" + st
    if tex:
        out += "\n  └ " + tex
    return out


def _world_extra_block(settings):
    """补充世界类型（克系/武侠/蒸汽朋克）的规则块。原有八种由 _world_block 负责。"""
    try:
        from . import kits as _k
        return _k.world_block_extra(str((settings or {}).get("world_type") or ""))
    except Exception:
        return ""


def _genre_block(settings):
    """影片类型：身份句＋叙事骨架＋节奏。这是治「换题材还是一个味」的正主。"""
    try:
        from . import kits as _k
        return _k.genre_block(str((settings or {}).get("genre") or ""))
    except Exception:
        return ""


def _pov_writing_block(settings):
    """视点对文字链的影响：POV 用第一人称写，自拍要写出对着镜头说话。"""
    try:
        from . import kits as _k
        return _k.pov_writing_block(str((settings or {}).get("pov") or ""))
    except Exception:
        return ""


_LOOK_REAL = ("【人物设计：真实组】\n"
              "画风是真实影像，人物必须是真实的人：真实比例（约 7～7.8 头身，最高不超过两米）、真实五官、真实皮肤。"
              "可以好看——模特身材、大美女大帅哥都行——但一切在真实范围内，像电影里的演员，不做小头长腿、不做非人特征。")
_LOOK_EXAG = ("【人物设计：夸张组】\n"
              "画风是动漫或游戏渲染，人物可以夸张：娇小到一米、壮硕到三米，肌肉极大或曲线极致，小头长腿窄腰，发型服装剪影强烈，"
              "五官更美型更突出；但结构完整、能动起来。")


def _look_block(settings):
    """人物设计约束由画风推出（2026-09-04 用户定）：真实组 / 夸张组。只给人设链。"""
    try:
        from . import style_presets as sp
        st = str((settings or {}).get("style") or sp.DEFAULT_STYLE)
        return _LOOK_REAL if sp.style_is_real(st) else _LOOK_EXAG
    except Exception:
        return ""


# 【选项背后到底该怎么写】用户 2026-08-30 定：选了「东方仙侠」，指令词里不能
# 只有「世界：东方仙侠」这个标签加一句服装建筑说明——要把这一类故事**该怎么写**
# 说清楚，Qwen 才知道往哪儿使劲。设定事实归 _world_block，写法归这里。
_CRAFT = {
    "东方仙侠": (
        "修行是手段不是目的，故事得落在人身上：师门的规矩、辈分、人情、忌讳，"
        "冲突多半从「规矩」和「想做的事」之间长出来。\n"
        "法术要写成看得见的动作和后果——剑光扫过石阶留下的白痕、符纸烧起来的灰、"
        "掌风把袍角掀起——不要堆境界、功法名和数值。\n"
        "山门、云海、悬崖、洞府这些地方本身就是戏：写人在里面显得很小，"
        "写高低差、写风、写脚下的路有多窄。\n"
        "「下山入世」是这一类最经典的转折：山上讲规矩，山下讲人心，"
        "主角带着山上那套规矩走进不讲规矩的地方。"),
    "西方魔幻": (
        "魔法要有代价和限制，用一次就该有一次的后果，不能想用就用。\n"
        "世界靠具体的东西立起来：一枚硬币的花纹、酒馆的价目、佣兵的规矩、"
        "教会的忌讳——不要用大段设定说明。\n"
        "怪物和危险写它做了什么、留下了什么痕迹，不写它的属性和等级。"),
    "东方写实": (
        "写这个年代真实存在的东西：市井的买卖、衙门的规矩、赶路的方式、吃什么穿什么。\n"
        "冲突从身份和处境里长出来，不靠超自然。人物说话要合身份。"),
    "西方写实": (
        "写这个年代真实存在的东西：谋生的方式、阶层的界线、法律和习俗。\n"
        "冲突从身份和处境里长出来，不靠超自然。"),
    "未来科幻": (
        "技术要落到人怎么用它：谁能用、用一次要付什么、坏了会怎样。\n"
        "不要解释原理，写它在画面里长什么样、发出什么声音、留下什么痕迹。\n"
        "阶层、监控、身体改造这类东西写成具体的日常细节，不写成设定介绍。"),
    "校园": (
        "冲突要小而具体：一次考试、一个座位、一句传开的话、一场比赛。\n"
        "人物关系靠日常的来回写出来，不靠旁白交代。"),
    "现代都市": (
        "冲突从钱、位置、关系里长出来。写具体的地方和具体的东西："
        "哪条街、什么车、多少钱、谁欠谁。"),
    "末日": (
        "资源、体力、时间都是稀缺的，每一个决定都要付代价。\n"
        "写环境怎么变得不能住人：坏掉的东西、没人的地方、剩下的痕迹。"),
}


_CRAFT.update({
    "东方": _CRAFT["东方仙侠"] + "\n武侠、江湖、朝堂这些没有法术的东方故事：写这个年代真实存在的东西——市井买卖、衙门规矩、赶路方式、吃穿——冲突从身份和处境里长出来，人物说话合身份。",
    "西方": _CRAFT["西方魔幻"],
    "未来": _CRAFT["未来科幻"],
    "现代": _CRAFT["现代都市"] + "\n校园题材冲突要小而具体（一次考试、一个座位、一句传开的话）；末日题材里资源、体力、时间都稀缺，每个决定都要付代价，写环境怎么变得不能住人。",
})


def _craft_block(settings):
    """这一类故事该怎么写。世界类型认不出来时不写这一段，别硬凑。"""
    from . import style_presets as sp
    w = str((settings or {}).get("world_type") or "").strip()
    return _CRAFT.get(sp.world_group(w) or w, "")


def _writer_block(settings):
    """开场那句身份。写「你是写仙侠小说的作者」，不写「顶级的故事作者」。"""
    w = str((settings or {}).get("world_type") or "").strip()
    m = {"东方仙侠": "仙侠", "西方魔幻": "西方奇幻", "东方写实": "东方古代",
         "西方写实": "西方年代", "未来科幻": "科幻", "校园": "校园",
         "现代都市": "都市", "末日": "末日废土",
         "东方": "东方古代", "西方": "西方奇幻", "未来": "科幻", "现代": "都市"}
    kind = m.get(w)
    return ("你是写%s小说的作者。" % kind) if kind else "你是小说作者。"



def _writer2_block(settings):
    """【新身份】不写"小说作者"，写"为 AI 视频写可视化故事原本的人"。

    用户 2026-08-31 指出：提示词第一句是身份，「小说作者」直接调用文学腔
    （比喻、心理、氛围），后面四十行规则都在反对这个身份——先让它当小说家，
    再骂它写得像小说。今天所有压不下去的指标（比喻 3.8~6.3、抽象 3.2）
    都是这么来的：规则压不过身份。
    保留题材知识，只换职业；同时给出理由（后面要转成视频），
    理由比命令有效，模型能自己推出该怎么写。
    """
    w = str((settings or {}).get("world_type") or "").strip()
    m = {"东方仙侠": "仙侠", "西方魔幻": "西方奇幻", "东方写实": "东方古代",
         "西方写实": "西方年代", "未来科幻": "科幻", "校园": "校园",
         "现代都市": "都市", "末日": "末日废土",
         "东方": "东方古代", "西方": "西方奇幻", "未来": "科幻", "现代": "都市"}
    kind = m.get(w) or ""
    return ("你在为 AI 视频制作写【可视化故事原本】。\n"
            "这不是小说，也不是分镜。它是一个完整、好看的%s故事，"
            "但故事里发生的每一件事，都要能靠人物、动作、环境、对白、表情、"
            "物品直接演出来——写完之后会一场一场转成剧本，再转成视频。" % kind)


def _fill(ins, settings, characters=None, scale_layers=("story",)):
    """把项目设定填进指令词。**每个选项都带上它的定义**，不只给标签。"""
    s = settings or {}
    if characters:
        assert_allowed(cards=characters, settings=s)
    extra = str(s.get("extra_requirements") or "").strip()
    _we = _world_extra_block(s)
    out = (ins.replace("【WRITER2】", _writer2_block(s))
              .replace("【WRITER】", _writer_block(s))
              .replace("【CRAFT】", _craft_block(s))
              .replace("【WORLD】", _world_block(s) + (("\n" + _we) if _we else ""))
              # 【审美优先于强度】两者都在管「人长什么样、能多夸张」，同时给会打架：
              # 审美「沧桑硬派」说往粗砺推、零夸张，强度「美型」说极致美型比谁都好看
              # （2026-08-29 实测同一份指令词里两句自相矛盾）。选了审美就以审美为准。
              .replace("【STRENGTH】", "")     # 视觉强度并入【LOOK】的真实组/夸张组（2026-09-04）
              .replace("【STYLE】", _style_block(s))
              .replace("【GENRE】", _genre_block(s))
              .replace("【LOOK】", _look_block(s))
              .replace("【POV】", _pov_writing_block(s))
              .replace("【SCALE】", _scale_block(s, scale_layers))
              .replace("【EXTRA】", ("【画面观感（用户要的，必须满足）】\n" + extra) if extra else "")
              .replace("【CHARACTERS】", _char_block(characters)))
    # 占位符留下的空行收拾干净，别在指令词里留一串空行
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out


def _q(system, user, mt=4200, temperature=0.8, _tries=3, reasoning=False):
    """调模型。**瞬时故障要自己扛住**：显存吃紧时（实测 23.1/24.5 GB）长上下文
    请求会让 llama-server 吐 500，一次抖动就让整条链断掉、整段没有片子。
    退避重试三次；三次都不成才把异常抛出去（2026-08-30）。
    """
    from models import qwen_client
    last = None
    _no_think_retry = False
    _to = int(min(900, 90 + 0.6 * int(mt or 0)))     # 单次超时：假死不许拖 30 分钟（2026-09-04）
    for i in range(_tries + 1):                       # +1：给 P306 关思考重试留一次
        if i == _tries and not _no_think_retry:
            break
        t0 = time.time()
        try:
            _reply = qwen_client.chat(system, user, temperature=temperature,
                                     max_tokens=mt, reasoning=reasoning, timeout=_to, return_meta=True)
            if isinstance(_reply, tuple):
                rep, _meta = _reply
                if (_meta or {}).get("finish_reason") == "length":
                    _rl = int((_meta or {}).get("reasoning_content_len") or 0)
                    _cl = int((_meta or {}).get("content_len") or len(str(rep or "")))
                    if reasoning and _rl > _cl and not _no_think_retry:
                        # P306：思考把输出额度吃光了（思考 %d 字 > 正文 %d 字）→ 关掉思考再来一次，不算失败
                        _no_think_retry = True
                        reasoning = False
                        try:
                            _log_slow_call(time.time() - t0, mt, system, -1, err="length: 思考%d字/正文%d字 → 关思考重试" % (_rl, _cl))
                        except Exception:
                            pass
                        continue
                    error = RuntimeError("文字输出达到长度上限，未完整结束（思考 %d 字，正文 %d 字，上限 %s tokens）；本轮不继续下游" % (_rl, _cl, mt))
                    error.partial_text = str(rep or "")
                    raise error
            else:
                rep = _reply
            rep = str(rep or "").strip()
            _log_slow_call(time.time() - t0, mt, system, len(rep))
            return rep
        except AgePolicyError:
            raise
        except Exception as ex:
            last = ex
            _log_slow_call(time.time() - t0, mt, system, -1, err=str(ex)[:60])
            if i < _tries - 1:
                time.sleep(3 * (i + 1))
    # 连不上就直说是哪个服务没起来，别把 ConnectionRefusedError 原样丢给界面
    # （2026-09-05：新用户第一件踩的就是没开 llama-server，看到的却是一串英文）
    _msg = str(last or "")
    if any(k in _msg for k in ("Connection", "refused", "Max retries", "timed out",
                               "actively refused", "10061", "Errno 111")):
        try:
            from . import health as _hl
            raise RuntimeError("连不上文字模型：" + _hl.services()["tip"]) from last
        except ImportError:
            raise RuntimeError("连不上文字模型（llama-server，8080 端口），先把它启动起来") from last
    raise last


def _log_slow_call(elapsed, mt, system, out_len, err=""):
    """每次调用记一行到 _calls.log；超过 60s 或出错的再记一份到 _slow_calls.log。"""
    try:
        import datetime
        line = "%s  %6.1fs  mt=%-5s out=%-5s  %s  | %s\n" % (
            datetime.datetime.now().strftime("%m-%d %H:%M:%S"), elapsed, mt, out_len,
            ("ERR " + err) if err else "", str(system or "")[:60].replace("\n", " "))
        with open(str(INS.parent.parent / "_calls.log"), "a", encoding="utf-8") as f:
            f.write(line)
        if elapsed >= 60 or err:
            with open(str(INS.parent.parent / "_slow_calls.log"), "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def _ins(name):
    return (INS / name).read_text(encoding="utf-8")


# ---------- 各角色 ----------

def write_synopsis(one_line, settings):
    """一句话 → 第一话故事简介（可编辑控制点）。"""
    return _q(_fill(_ins("第一话简介_指令词.txt"), settings),
              "一句话：" + str(one_line or "").strip(), mt=800, temperature=0.85)


_CROWD = re.compile(r"路人|众人|众|客人|食客|百姓|其他|甲$|乙$|群|人群|士兵|卫兵|侍卫|随从|下人|仆人|伙计|小贩|商贩|摊主|老汉|老者|差役|茶博士|店家|掌柜|车夫|船夫|艄公|弟子们|信徒|灵兽|马|狗|猫")


def ensure_cards_from_arrangement(sid):
    """按已确认的本话安排直接建人物卡（零模型，P283）：名字、身份（role）、关系、性格照安排写，
    不问设计师。动物/群演不建。已有同名卡不动。返回新建的名字。精简模式下代替 ensure_cards_for_cast。"""
    from . import asset_core, shotlist as _slm
    arr = confirmed_arrangement(sid) or {}
    people = [p for p in ((((arr.get("items") or {}).get("who") or {}).get("people") or [])) if isinstance(p, dict)]
    have = {str(c.get("name") or "").strip() for c in (asset_core.list_assets(sid, "characters") or [])}
    fresh = []
    for p in people:
        nm = str(p.get("name") or "").strip()
        if not nm or nm in have or _slm._is_crowd(nm):
            continue
        fresh.append({"name": nm, "identity": str(p.get("role") or "").strip(),
                      "personality": str(p.get("personality") or "").strip(),
                      "appearance": str(p.get("look") or "").strip(),                  # _create_cards 读的是 appearance（P317：原来写成 appearance_details 没读到，卡是空的）
                      "voice": str(p.get("voice") or "").strip(),
                      "sex": str(p.get("sex") or "").strip(), "age": str(p.get("age") or "").strip()})   # P316：性别年龄进卡
        have.add(nm)
    if not fresh:
        return []
    made = _create_cards(sid, fresh)
    return [c.get("name") for c in made if isinstance(c, dict) and c.get("name")]


def ensure_cards_for_cast(sid, settings, on_step=None, log=None):
    """结构表在场栏里的人，没有卡的一次建齐（P139）。返回新建的名字列表。"""
    from . import asset_core
    s = settings or {}
    rows = [r for r in (s.get("plan_rows") or []) if isinstance(r, dict) and str(r.get("one_line") or "").strip()]
    if not rows:
        return []
    cnt = {}
    for r in rows:
        for tok in re.split(r"[、，,／/和与及]", str(r.get("cast") or "")):
            nm = re.sub(r"（[^）]*）", "", tok).strip()
            if nm and nm not in ("无", "没有"):
                cnt[nm] = cnt.get(nm, 0) + 1
    people = {str(p.get("name") or "").strip() for p in (s.get("plan_people") or []) if isinstance(p, dict)}
    _one = str(s.get("one_line") or "")
    # 只给"一句话里有的人 or 人物表里的人"建卡（P151：框架编的「邻人」一旦有了卡，正文就把它当真人写）
    want = [n for n, k in cnt.items()
            if (k >= 2 or n in people) and len(n) >= 2 and not _CROWD.search(n)
            and (n in _one or n in people or any(n in p or p in n for p in people))]
    have = [str(c.get("name") or "").strip() for c in (asset_core.list_assets(sid, "characters") or [])]
    missing = [n for n in want if not any(n == h or n in h or h in n for h in have)]
    if not missing:
        return []
    if on_step:
        on_step("按结构表补人物卡：" + "、".join(missing))
    one = str(s.get("one_line") or "")
    clue = ("一句话故事：%s\n\n分话结构：\n%s\n\n人物表：\n%s\n\n只为这几个人设计人物卡（名字照抄，一人一张，别的人不要）：%s"
            % (one, "\n".join("第%d话：%s（在场：%s）" % (i + 1, r["one_line"], r.get("cast") or "") for i, r in enumerate(rows)),
               "\n".join("%s｜%s｜%s｜%s" % (p.get("name"), "、".join(str(x) for x in (p.get("sex"), p.get("age")) if x) or "性别年龄按故事判断", p.get("role"), p.get("relation")) for p in (s.get("plan_people") or []) if isinstance(p, dict)) or "（无）",
               "、".join(missing)))
    try:
        designed = design_characters(clue, s)
    except Exception as ex:
        if log is not None:
            log.append("补人物卡失败：%s" % str(ex)[:80])
        return []
    # 人物表里的名字 ↔ 结构表在场栏的泛称（小满 ↔ 小女孩/女孩）：按身份/关系里的关键词对上
    _kw = {"女孩": r"女孩|女儿|少女|姑娘", "小女孩": r"女孩|女儿|少女|姑娘", "女儿": r"女儿|女孩", "新妻": r"妻|娘子|媳", "妻子": r"妻|娘子|媳",
           "老板娘": r"老板娘|掌柜", "服务生": r"服务生|侍应|店员", "女服务生": r"服务生|侍应|店员"}
    alias = {}
    for p in (s.get("plan_people") or []):
        pn = str((p or {}).get("name") or "").strip()
        pd = str((p or {}).get("role") or "") + str((p or {}).get("relation") or "")
        for m in missing:
            if pn and (m == pn or m in pn or pn in m or (m in _kw and re.search(_kw[m], pd))):
                alias.setdefault(m, pn)
    keep, rename = [], {}
    for d in designed or []:
        nm = str((d or {}).get("name") or "").strip()
        hit = next((m for m in missing if m == nm or m in nm or nm in m or alias.get(m) == nm
                    or (m in _kw and re.search(_kw[m], str((d or {}).get("identity") or "") + str((d or {}).get("relation") or "")))), "")
        if hit and not any(hit in rename for _ in [0]) and not any(str(k.get("name")) == (alias.get(hit) or hit) for k in keep):
            final = alias.get(hit) or nm or hit               # 有人物表名字就用它（小满），否则用设计师给的名字，最后才用泛称
            d["name"] = final
            rename[hit] = final
            keep.append(d)
    if not keep:
        if log is not None:
            log.append("角色设计师没给出 %s 的卡" % "、".join(missing))
        return []
    # 关系称谓只用于身份对应；女儿、儿子不代表儿童，不改年龄。
    for hit, final in list(rename.items()):
        d = next((k for k in keep if str(k.get("name")) == final), None)
        if d is None:
            continue
        prole = next((str(p.get("role") or "") for p in (s.get("plan_people") or []) if str((p or {}).get("name") or "").strip() == final), "")


        if prole and (not str(d.get("identity") or "").strip() or re.search(r"铺|店|商|女儿$", str(d.get("identity") or "")) and "女儿" in prole):
            d["identity"] = prole
    made = _create_cards(sid, keep)
    names = [str(c.get("name") or "") for c in made if isinstance(c, dict)]
    # 同一个人的其他泛称（小女孩 改了，女孩 也改）
    all_toks = set()
    for r in rows:
        all_toks |= {t.strip() for t in re.split(r"[、，,／/和与及]", str(r.get("cast") or "")) if t.strip()}
    for hit, final in list(rename.items()):
        for t in all_toks:
            if t != hit and t not in rename and len(t) >= 2 and (t in hit or hit in t):
                rename[t] = final
    # 结构表在场栏里的泛称改成卡名，后面的正文/剧本/裁剪都按卡名认人
    changed = False
    for r in rows:
        toks = [t.strip() for t in re.split(r"[、，,／/和与及]", str(r.get("cast") or "")) if t.strip()]
        new_toks = [rename.get(t, t) for t in toks]
        if new_toks != toks:
            r["cast"] = "、".join(dict.fromkeys(new_toks))
            changed = True
    if changed:
        try:
            from . import story_core as _sc1
            st1 = _sc1.get_story(sid) or {}
            st1.setdefault("settings", {})["plan_rows"] = rows
            _sc1.save_story(st1)
        except Exception:
            pass
    if log is not None:
        log.append("按结构表新建人物卡：" + "、".join(names) + ("；在场栏改名：" + "、".join("%s→%s" % kv for kv in rename.items()) if rename else ""))
    return names


def row_boundary_block(settings, ep=1, cards=None):
    """结构表第 ep 行的事实单（P132/P138）：人物表、已经发生、这一话的边界（地点/在场/落点/变化点）、后面几话不写、基调。
    没有结构表返回空串。"""
    s = settings or {}
    rows = [r for r in (s.get("plan_rows") or []) if isinstance(r, dict) and str(r.get("one_line") or "").strip()]
    ep = int(ep or 1)
    if not rows or not (1 <= ep <= len(rows)):
        return ""
    r = rows[ep - 1]
    lines = []
    # 人物表：框架给的 plan_people 优先，缺的用人物卡补身份
    people = [p for p in (s.get("plan_people") or []) if isinstance(p, dict) and str(p.get("name") or "").strip()]
    by_card = {}
    for c in (cards or []):
        nm = str((c or {}).get("name") or "").strip()
        if nm:
            by_card[nm] = str((c or {}).get("char_type") or (c or {}).get("identity") or "").strip()
    seen = set()
    plines = []
    for p in people:
        nm = str(p.get("name")).strip()
        seen.add(nm)
        _tail = ("【他/她没有名字，提到时就叫「%s」，不许安名字、不许拿别人的名字称呼】" % nm) if p.get("unnamed") else ""
        plines.append("%s——%s%s%s" % (nm, str(p.get("role") or by_card.get(nm) or "").strip() or "（身份按一句话）",
                                  ("，" + str(p.get("relation")).strip()) if str(p.get("relation") or "").strip() else "", _tail))
    for nm, ct in by_card.items():
        if nm not in seen:
            plines.append("%s——%s" % (nm, ct or "（身份按一句话）"))
    # 未出场的人（不在这一话在场栏）：标出来，不许提名字（P143：长老开口就叫出了第 5 话才出场的小满）
    _cast_now = [re.sub(r"（[^）]*）", "", x).strip() for x in re.split(r"[、，,／/和与及]", str(r.get("cast") or "")) if x.strip()]
    def _in_cast(nm):
        return any(nm == c or nm in c or c in nm for c in _cast_now)
    plines = [(x if _in_cast(x.split("——")[0]) else x + "【这一话不出场，名字也不许提】") for x in plines]
    if plines:
        lines.append("【人物表——谁是谁是事实，不许写错、不许对调】\n" + "\n".join("· " + x for x in plines))
    before = [str(x.get("one_line") or "").strip() for x in rows[:ep - 1]]
    if before:
        lines.append("【已经发生（前面几话演过了，这一话不重演、不推翻）】\n" + "\n".join("· " + x[:40] for x in before[-4:]))
    lines.append("【这一话的边界（照着写，不越界）】")
    if str(r.get("landing") or "").strip():
        lines.append("**最后一段写这个画面，然后停笔，后面一个字不写：%s。**" % str(r["landing"]).rstrip("。"))
    if str(r.get("place") or "").strip():
        lines.append("地点：%s——全篇只在这里。" % r["place"])
    if str(r.get("cast") or "").strip():
        lines.append("在场的人：%s。别的人这一话不出场。" % r["cast"])
    if str(r.get("change") or "").strip():
        lines.append("这一话演完，和开头不一样的只有这一件：%s。" % str(r["change"]).rstrip("。"))
    later = [str(x.get("one_line") or "").strip() for x in rows[ep:]][:5]
    if later:
        lines.append("【后面几话才发生的事——这一话一个字不写，不预告不暗示】\n" + "\n".join("· " + x[:40] for x in later))
        # 这一话不许出现的词（P143）：后话在场的人名 + 后话的事件短语（打听/另娶/女儿…）
        try:
            from . import story_plan as _spw
            _ban = []
            for x in rows[ep:]:
                for t in re.split(r"[、，,／/和与及]", str(x.get("cast") or "")):
                    t = re.sub(r"（[^）]*）", "", t).strip()
                    if t and len(t) >= 2 and not _in_cast(t) and not _CROWD.search(t) and t not in _ban:
                        _ban.append(t)
                _one_all = str(s.get("one_line") or "")
                for w in _spw.later_event_words(str(x.get("one_line") or "")):
                    # 只留一句话里也有的实词（另娶/女儿/打听…），模型自己编的碎片（柱正忙/火中烧）不进禁词表（P143b）
                    if w and w in _one_all and w not in str(r.get("one_line") or "") and w not in _ban:
                        _ban.append(w)
            if _ban:
                lines.append("【这一话不许出现的词（别人嘴里说出来也不算）】" + "、".join(_ban[:24]))
        except Exception:
            pass
    # 篇幅服从内容（P144）：这一话事少就写短，不许用新事件撑字数
    _rl = str(r.get("one_line") or "")
    if len(re.sub(r"[^一-龥]", "", _rl)) < 45 or str(r.get("pace") or "") == "日常":
        lines.append("【篇幅】这一话事少，800～1200 字写完就停；字数不够也不许加新事件、新对话、新的人。"
                     "撑住的办法：让对方手上的活一件比一件近或重（远处干活→走近→在他桌旁），中间给一个小意外（东西差点掉了、被人绊一下、汤洒到手上），"
                     "主角每次只用一个手上的小动作回应（指头停住、杯子举起又放下）。")
    _cast_n = len([x for x in re.split(r"[、，,／/和与及]", str(r.get("cast") or "")) if x.strip()])
    if _cast_n >= 4:
        lines.append("【人多】这一话有 %d 个人，每个人第一次出场只给**一句**：他此刻在做什么，不写长相和穿着。"
                     "他们的区别靠说话方式分开（有人报数字、有人只说四个字、有人自问自答），不靠外貌描写。"
                     "至少有三处是被人打断或答非所问。" % _cast_n)
    else:
        lines.append("【外貌】每个人的长相和穿着只在第一次出场写一次，最多一句；后面直接写他在做什么。开头三段里不许有整段的外貌描写。")
    # 「看」型的话（P144）：只看不说
    if re.search(r"看|盯|望|瞧|打量|目光", _rl) and not re.search(r"说|问|搭话|聊|答|喊|叫|开口|对话|交谈|调戏|搭讪", _rl):
        lines.append("【这一话只看不说】两人这一话没有对话；主角坐在原处，不起身、不走近、不碰对方、不给东西。可写的是：看到什么、手里的杯子怎么动、对方在做什么活。")
    # 这一话必须用到的东西（P155②）：落点和变化点里点名的道具，不用到就等于这一话没演
    # 只认「拿得起、能递能藏能碎」的东西；地点（城门）、动词短语（追兵勒马）不是道具
    _PROP_WORDS = ("密信", "信", "令牌", "腰牌", "铁牌", "玉佩", "钥匙", "手电", "手电筒", "伞", "琴", "剑", "长剑", "刀", "砍刀",
                   "酒杯", "杯子", "酒壶", "托盘", "木盘", "碗", "油灯", "灯笼", "手表", "怀表", "包袱", "信封", "纸条", "便利贴",
                   "准考证", "身份证", "号码布", "糖葫芦", "抹布", "擦布", "铜铃", "钱袋", "银锭", "铜钱", "药瓶", "镜子", "绳子",
                   "锁", "笔", "本子", "手机", "杯", "壶", "灯", "牌", "信物", "遗物", "寻人启事", "地图", "黄历", "保温桶", "鸡汤")
    _src_p = str(r.get("one_line") or "") + "。" + str(r.get("landing") or "") + "。" + str(r.get("change") or "")
    _props = []
    for _w in sorted(_PROP_WORDS, key=len, reverse=True):
        if _w in _src_p and not any(_w in x for x in _props):
            _props.append(_w)
    if _props:
        lines.append("【这一话必须真的用到的东西】%s——要写它怎么被拿出来、被谁看、变成什么样，不能只提一句名字。" % "、".join(_props[:4]))
    tone = str(s.get("tone") or "").strip()
    if tone:
        lines.append("基调：%s。" % tone)
        if tone in ("悬疑", "恐怖"):
            lines.append("【悬疑的规矩】这一话的关键事实（谁认出了什么、谁瞒着什么）只让读者从动作和反常里看出来："
                         "该扣的没扣、该问的没问、手停了一下、灯该灭没灭。人物不许把它说出口，也不许在叙述里解释。")
    return "\n".join(lines)


def finish_landing(prose, settings, ep=1, log=None):
    """这一话该完成的那件事没写完 → **接着往下写一小段**把它写到发生为止（P176）。

    不是贴一句落点原文上去（那是倒退，实测把已经离场的人又拉回画面），
    是从正文实际的最后一段接着写，一次调用，最多 120 字。
    补完还是没发生就原样留着，在 result 里标出来——宁可缺，不许贴标签。
    """
    rows = [r for r in ((settings or {}).get("plan_rows") or []) if isinstance(r, dict)]
    ep = int(ep or 1)
    if not (1 <= ep <= len(rows)):
        return prose, ""
    landing = str(rows[ep - 1].get("landing") or "").strip()
    act = _final_action(landing)
    p = str(prose or "")
    if not landing or not act or act in p:
        return prose, ""
    paras = [x for x in p.split("\n") if x.strip()]
    if not paras:
        return prose, ""
    ask = ("这一话的正文写到这里：\n\n%s\n\n"
           "现在接着这一段往下写**一小段**（最多 120 字，两三句），把下面这件事写到**真的发生**为止：\n"
           "%s\n"
           "只写这一小段，不要重写前面，不要总结，不要抒情，不要把上面这句说明抄进去。"
           % ("\n".join(paras[-2:]), landing))
    try:
        add = _q("你是小说家。只补写正文，一小段。", ask, mt=300, temperature=0.7).strip()
    except Exception:
        add = ""
    add = strip_method_marks(fix_traditional(add)).strip()
    if add and act in add and len(add) <= 400:
        out = p.rstrip() + "\n\n" + add
        note = "落点没写完，接着补了一小段（%d 字）" % len(add)
        if log is not None:
            log.append(note)
        return out, note
    note = "落点「%s」没写完，补写也没补上（留着没动）" % landing[:16]
    if log is not None:
        log.append(note)
    return prose, note


def ensure_landing_last(prose, settings, ep=1, log=None):
    """落点原句照抄（P147）：最后一段里要有落点的实词；没有就把落点原句接成最后一段。
    返回 (正文, 说明)。"""
    s = settings or {}
    rows = [r for r in (s.get("plan_rows") or []) if isinstance(r, dict) and str(r.get("one_line") or "").strip()]
    ep = int(ep or 1)
    if not rows or not (1 <= ep <= len(rows)):
        return prose, ""
    landing = str(rows[ep - 1].get("landing") or "").strip()
    if not landing:
        return prose, ""
    paras = [p for p in re.split(r"\n\s*\n", str(prose or "").strip()) if p.strip()]
    if not paras:
        return prose, ""
    _cast = set()
    for r in rows:
        for t in re.split(r"[、，,／/和与及]", str(r.get("cast") or "")):
            t = re.sub(r"（[^）]*）", "", t).strip()
            if t:
                _cast.add(t)
    _l = landing
    for n in sorted(_cast, key=len, reverse=True):
        _l = _l.replace(n, "")
    e = re.sub(r"[^一-龥]", "", _l)
    grams = [e[k:k + 2] for k in range(len(e) - 1)]
    grams = [g for g in grams if not re.search(r"[的了着在是和与把被让给到从]", g)]
    if len(grams) < 3:
        return prose, ""
    last = paras[-1]
    if sum(1 for g in grams if g in last) >= 2:
        return prose, ""
    # 落点的实词在最后三段里一个都没有 → 人物已经换了场景，硬接上去是逻辑断裂（P154）
    tail3 = "".join(paras[-3:])
    if sum(1 for g in grams if g in tail3) == 0:
        note = "落点「%s」和结尾对不上（场景已经换了），没有硬接" % landing[:16]
        if log is not None:
            log.append(note)
        return prose, note
    # P176：不再把落点原句贴上去——贴上去是倒退（第2话实测：女孩已经消失在巷子里了，
    # 末段又补一句"李长渊在人群中锁定女孩身影"）。这里只记账，真缺就由 finish_landing 接着写。
    note = "落点没落在最后一段（没有硬接，交给续写补）"
    if log is not None:
        log.append(note)
    return prose, note
    paras.append(landing.rstrip("。") + "。")
    note = "落点原句没落在最后一段，按原句补了一段"
    if log is not None:
        log.append(note)
    return "\n\n".join(paras), note


def trim_to_boundary(prose, settings, ep=1, log=None):
    """按结构行边界裁正文（P134/P134b）。返回 (正文, 说明)。没有结构表原样返回。
    ① 后话才出场的人（后话在场栏有、本话没有）当主语出场的那一段起裁掉；② 落点实词命中 ≥2 的那一段之后裁掉；
    先试最早的一刀，裁后不足 60% 段落或 <800 字就退到下一刀，都不行就不裁。"""
    s = settings or {}
    rows = [r for r in (s.get("plan_rows") or []) if isinstance(r, dict) and str(r.get("one_line") or "").strip()]
    ep = int(ep or 1)
    if not rows or not (1 <= ep <= len(rows)):
        return prose, ""
    paras = [p for p in re.split(r"\n\s*\n", str(prose or "").strip()) if p.strip()]
    if len(paras) < 6:
        return prose, ""
    row = rows[ep - 1]
    _split = lambda v: [re.sub(r"（[^）]*）", "", x).strip() for x in re.split(r"[、，,／/和与及]", str(v or "")) if x.strip() and x.strip() not in ("无", "没有")]
    mine = set(_split(row.get("cast")))
    later_cast = set()
    for r in rows[ep:]:
        later_cast |= set(_split(r.get("cast")))
    foreign = [n for n in later_cast if n not in mine and len(n) >= 2 and not _CROWD.search(n)
               and not any(n in m or m in n for m in mine)]
    cut_foreign = None
    _act = r"(?:正|在|站|坐|抬|走|笑|说|抱|端|看|转|愣|蹲|跪|伸|推|拉|回|低|从|把|将|手|的脸|的目光|直起|放下|拿|握)"
    for i, p in enumerate(paras):
        if any(re.search(r"(?:^|[。！？，；\n]|」|”)\s*" + re.escape(n) + _act, p) for n in foreign):
            cut_foreign = i
            break
    landing = str(row.get("landing") or "")
    for n in sorted(mine | later_cast, key=len, reverse=True):
        landing = landing.replace(n, "，")
    e = re.sub(r"[^一-龥]", "", landing)
    grams = [e[k:k + 2] for k in range(len(e) - 1)]
    grams = [g for g in grams if not re.search(r"[的了着在是和与把被让给到从]", g)]
    cut_landing = None
    if len(grams) >= 3:
        for i, p in enumerate(paras):
            if i < len(paras) * 0.5:
                continue                                      # 落点只在后半篇认（P142：第 2 段就命中是误报）
            if sum(1 for g in grams if g in p) >= 2:
                cut_landing = i + 1
                break
    cands = sorted(c for c in (cut_foreign, cut_landing) if c is not None and 1 <= c < len(paras))
    if not cands:
        return prose, ""
    # P143：落点段之后若还写了后话的人或事，从落点段后裁，只要求剩 ≥800 字（落点就是收笔处）
    _later_words = set(foreign)
    try:
        from . import story_plan as _spw
        for x in rows[ep:]:
            _later_words |= {w for w in _spw.later_event_words(str(x.get("one_line") or "")) if w and w not in str(row.get("one_line") or "")}
    except Exception:
        pass
    if cut_landing is not None and cut_landing < len(paras) and sum(len(x) for x in paras[:cut_landing]) >= 800:
        tail = "".join(paras[cut_landing:])
        if any(w in tail for w in _later_words):
            kept = paras[:cut_landing]
            note = "按边界裁掉第 %d～%d 段（落点之后写了后话的事）" % (cut_landing + 1, len(paras))
            if log is not None:
                log.append(note)
            return "\n\n".join(kept), note
    cut = None
    for c in cands:
        kept = paras[:c]
        if len(kept) >= max(4, int(len(paras) * 0.6)) and sum(len(x) for x in kept) >= 800:
            cut = c
            break
    if cut is None:
        return prose, "越界但裁后太短，不裁（裁点第 %s/%d 段）" % ("/".join(str(c) for c in cands), len(paras))
    kept = paras[:cut]
    why = "后话人物「%s」提前出场" % next((n for n in foreign if n in paras[cut]), "") if cut == cut_foreign else "写过了落点"
    note = "按边界裁掉第 %d～%d 段（%s）" % (cut + 1, len(paras), why)
    if log is not None:
        log.append(note)
    return "\n\n".join(kept), note


def write_prose(synopsis, settings, characters=None, one_line=""):
    """一句话[+已定人物][+简介] → 第一话小说体正文（小说家）。

    2026-08-28 起第一话**正文先行**：简介可以为空，小说家按一句话直接创作
    （黄金开场/阻力源/转折/结尾钩这些戏剧结构由它在创作层注入——
    剧本层是事实层不许编，冲突只能在这里长出来）。给了简介就以简介为纲
    （用户改完简介点「按设定重新生成故事」走的还是这条反向路）。
    """
    from . import universal_writer as _uw
    if _uw.enabled():
        _s = dict(settings or {})
        _s["one_line"] = _s.get("one_line") or one_line
        _result = _uw.write(_s, brief=synopsis, characters=characters)
        # skipped 不是失败（P185）；unknown 是——截断和"生成期间被改过"都会走到 unknown（P187③）
        if _result["review"]["verdict"] in ("fail", "unknown"):
            raise _uw.DraftNotReady(_result)
        return _result["prose"]
    sys = _fill(_ins("第一话原文_指令词.txt"), settings, characters)
    user = []
    if str(one_line or "").strip():
        user.append("一句话：" + str(one_line).strip())
    # 【这句话本身会决定成败】原来这里写的是「没有简介。按这句一句话，直接创作
    # 第一话的完整小说体正文，戏剧结构照系统指令里的硬标准来。」——
    # 「戏剧结构硬标准」那一节在新指令词里已经不存在了，是个悬空引用；
    # 实测同一份指令词，实验室用干净的一句跑十篇零排比，生产带着这句跑一篇
    # 就写成了一行一句、排比 57%（2026-08-31）。喂进去的话要跟验过的那句一致。
    if str(synopsis or "").strip():
        user.append("第一话故事简介：\n" + str(synopsis).strip())
        user.append("按这个简介写第一话的完整故事正文。")
    else:
        user.append("按这句话写第一话的完整故事正文。")
    _rb = row_boundary_block(settings, 1, cards=characters)
    if _rb:
        user.append(_rb)                                   # 结构行事实单（P132/P138）
    umsg = "\n\n".join(user)
    # 【正文只调两次模型】用户 2026-08-30 定：
    #   第一次 —— 让它写，不打断、不预设收笔点，别把一堆规矩堆给它；
    #   然后  —— 代码把能修的全修掉（这一段在下面的修复链里）；
    #   第二次 —— 只把**代码修不掉的**问题列给它，让它在原稿上改掉，
    #             不是整话重写。
    # 原来是「写 → 校验 → 打回整话重写 ×2 → 才轮到机械修复」，实测那两次重写
    # 的六条理由**全都有机械修复路径**，而且把 4587 字的稿子重写成了 2282 字，
    # 最后还得再花一次调用加厚补回来——三次调用换一版更差的稿子。
    prose = strip_method_marks(
        # mt 8000：6400 时长稿会被砍在半句话上，而「结尾残句」这个毛病
        # 一直被当成模型写坏，其实是 token 用完（2026-08-31 实验，六篇里五篇
        # 都是这么断的）。分幕控长之后正常稿在 4300 字左右，8000 有足够余量。
        fix_traditional(trim_tail(_q(sys, umsg, mt=8000, temperature=0.85))))

    # fix_essay_tail 要在**压缩之前**跑：结尾那一千多字抒情散文也算在字数里，
    # 不先削掉，压缩器会以为正文很长，去砍真正的剧情（2026-08-30 实测仙侠段）。
    prose = fix_essay_tail(fix_truncated_tail(prose))
    _structured = bool(row_boundary_block(settings, 1))
    if not _structured:
        prose = condense_if_long(expand_if_short(prose, settings), settings)
    prose, _tb = trim_to_boundary(prose, settings, 1)           # 结构行边界裁剪（P134）
    if _tb:
        _dbg("边界裁剪", {"ep": 1, "结果": _tb})
    prose, _lz = ensure_landing_last(prose, settings, 1)        # 落点原句照抄（P147）
    if _lz:
        _dbg("落点兜底", {"ep": 1, "结果": _lz})
    if _structured:
        # P138 减法：有结构表时正文到此为止——不扩不压不换句不补事件（这些加工没证明过比初稿好，还造出「眼白静止」这类句子）
        # P142：确定性的时代错词清理留着（东方边城出「纸币」）
        prose = strip_onomatopoeia(fix_anachronisms(fix_typos(prose), settings))
        prose, _nm = strip_meta_sentences(prose)
        if _nm:
            _dbg("删提示词泄漏句", {"ep": 1, "句": _nm})
        prose, _nl = trim_look_paragraphs(prose)
        if _nl:
            _dbg("削开头外貌句", {"ep": 1, "句": _nl})
        _cc = []
        prose = clean_prose_common(prose, settings, 1, log=_cc)      # P165①：和 gen_prose 同一套
        if _cc:
            _dbg("正文公共清理", {"ep": 1, "明细": _cc})
        return prose
    # fix_unfilmable 放在链子最前面：它删段、改句，后面几道（段落节奏、
    # 结尾上帝视角）都得看它改完之后的文本。
    out = fix_anachronisms(
        fix_short_para_run(fix_tail_god_view(fix_tail_questions(fix_time_jumps(
            fix_pose_jumps(fix_cliches(fix_english_words(
                fix_quoted_thoughts(fix_quoted_proper_nouns(
                    fix_unfilmable(fix_typos(prose))))))))))), settings)
    # 【第二次调用：只换句子，不动正文】用户 2026-08-31 定：
    #   出问题的句子挑出来 → 只把这些句子发给模型 → 只收回改好的句子 →
    #   代码逐句替换回去。**原文其余部分一个字都不碰，也不删任何东西。**
    # 原来是把整篇稿子发过去让它"在原稿上改"，它会顺手改掉别处，
    # 安全闸一拦就整份退回，等于白花一次调用（2026-08-30 实测）。
    out = strip_onomatopoeia(out)
    _digest = _chars_digest(characters)
    # 【序章节奏】用户 2026-09-02：第一话是序章，要快速入戏——冲突 25% 前登场、高潮不少于 15%
    # 先定节奏再换句：重写出来的新句子也要过一遍问题句替换
    if row_boundary_block(settings, 1):
        _pace_note = "有结构表，不跑节奏审校（P132）"       # 结构表是节奏的权威，不按冲突片假设压开场扩高潮
    else:
        out, _pace_note = story_pace_pass(out, settings, digest=_digest)
    _dbg("序章节奏审校", {"结果": _pace_note})
    # 【事件表】任意连续 30% 没事件就补一个可见事件；首事件 >8% 压开场（用户 2026-09-03：前一分多钟啥都没有）
    out, _ev_note = story_event_pass(out, one_line=one_line, digest=_digest)
    _dbg("事件表审校", {"结果": _ev_note})
    out = replace_bad_sentences(out, context=_digest)
    out, _nfb = clean_prose_fallback(out)
    _dbg("正文兜底删短句", {"删除": _nfb})
    _left = (prose_problems(out, settings)
             + unfilmable_problems(out) + typo_problems(out))
    if _left:
        _dbg("换句之后还剩的问题", {"条数": len(_left),
                                    "问题": [x[:90] for x in _left[:6]],
                                    "字数": len(out)})
    return out


def collect_bad_sentences(text, cap=24):
    """把有问题的句子挑出来。返回 [(原句, 毛病)]，不改动正文。

    毛病按 _UNFILMABLE 那四类认（比喻／回忆／推演／抽象状态）。
    引号里的命中一律跳过——台词一个字不许动。
    """
    t = str(text or "")
    seen, out = set(), []
    for tag, pat in _UNFILMABLE:
        for m in re.finditer(pat, t):
            if _pos_in_quote(t, m.start()):
                continue
            a = max(t.rfind(c, 0, m.start()) for c in "。！？…\n") + 1
            # 匹配本身已经吃到句末标点的，句子就到这里为止；不然会把下一句也带进来
            _e = m.end() - 1 if (m.end() > 0 and t[m.end() - 1] in "。！？…") else m.end()
            ends = [i for i in (t.find(c, _e) for c in "。！？…\n") if i >= 0]
            b = (min(ends) + 1) if ends else len(t)
            while b < len(t) and t[b] in "」”』）\"'":
                b += 1
            sent = t[a:b].strip()
            if not sent or sent in seen or len(sent) > 160:
                continue
            # 【气味不删，改成人的反应】味道本身拍不到，但**闻的人、吃的人
            # 做了什么**拍得到，而且信息量更大：皱眉、捂鼻子、屏住呼吸、
            # 嚼到一半停住、把碗推开。这跟"心理活动改成动作"是同一个原则，
            # 原来我在心理上这么做、在气味上却选择跳过，不一致
            # （2026-08-31 用户指出）。
            # 中文里气味词本身就是拿别的东西命名的（霉味、血腥气、铁锈味），
            # 那种具名说法不算比喻，只有打比方的那种才收进来。
            # 【截出来的句子不许带孤立引号】按句末标点切句时，上一句台词的
            # 收尾引号会被带进来（「"埃里克的声音沙哑，像是…」），
            # 发过去改完再替换回正文就是脏字符（2026-08-31 自查）。
            sent = re.sub(r'^[”」』"\']+', "", sent).strip()
            if not sent or sent in seen:
                continue
            # 声音的比喻单独归一类：它的改法和别的东西不一样。
            # 【用局部变量】直接改 tag 会污染同一组后面所有的句子——
            # 一句被判成声音，整组都跟着变成声音（2026-08-31 自查）。
            kind = tag
            if kind == "比喻":
                # 【口味先判】「味道」两个字气味和口味都有，先看句子里有没有
                # 吃喝的动作，有就是口味，没有才是鼻子闻的（2026-08-31 自查：
                # 「他咬了一口，那肉的味道像…」被判成了气味）。
                # 「灌」「嘬」歧义太大：「手臂沉重得像灌了铅」被判成口味
                # （2026-08-31 自查）。只留没有歧义的吃喝动词。
                if re.search(r"(尝了|嚼|咬[了一下]|咽|吞|喝[了一下口]|吃|舔)",
                             sent):
                    kind = "比喻·口味"
                elif re.search(r"[味嗅]|闻[到见着]|气息|腥|臭|香(?!烟)", sent):
                    kind = "比喻·气味"
                elif re.search(r"[声响鸣吼叫嘶哼喘]|嗓音|听[见到着]|呼吸", sent):
                    kind = "比喻·声音"
            seen.add(sent)
            out.append((sent, kind))
            if len(out) >= cap:
                return out
    return out


_WHY = {
    "比喻": "这句里有比喻。改成大白话的直接描写：那个东西本身什么颜色、"
            "什么形状、动起来什么样、表面什么手感、有没有缺口污渍、"
            "比旁边的东西大还是小。",
    "比喻·气味": "这句用比方来写气味。气味拍不到，但**闻到的人做了什么**拍得到，"
                 "而且更能让人知道这味道是好是坏。改成写那个人的反应："
                 "吸一下鼻子、皱眉、捂住口鼻、屏住呼吸、侧过脸、后退半步、"
                 "或者凑近再闻一次。气味本身留一个具名的说法就够"
                 "（霉味、血腥气、机油味），不要再打比方。",
    "比喻·口味": "这句用比方来写吃到的味道。味道拍不到，但**吃的人做了什么**拍得到。"
                 "改成写他的动作：嚼到一半停住、咽下去时喉结动一下、把碗推开、"
                 "又扒了两口、辣得吸气、烫得张嘴哈气、伸手去够水。",
    "比喻·声音": "这句用比方来写声音。改成直接写这个声音本身："
                 "是什么东西碰什么东西发出来的、断断续续还是一声接一声、"
                 "越来越快还是慢下来、多响（压过了别的声音，还是只有近处听得见）。"
                 "写声音本身的物理样子（哪里发出、多响、快慢、断不断），不要打比方。",
    "回忆": "这句写的是回忆，摄影机拍不到。**过去发生的事一个细节都不许丢**——"
            "如果这一刻旁边有别人，就把它改成这个人**说出来的一段话**"
            "（「我十分钟前去过洗手间，走廊尽头有个人影闪过去，我以为是保洁。」）；"
            "如果只有他一个人，就改成他此刻的动作，再让他摸出一件跟那段过去"
            "有关的东西。"
            "（不要写成闪回：闪回要单独成一场戏，这一话只有一个地点。）",
    "权衡推演": "这句是在脑子里推演，摄影机拍不到。改成他此刻的动作：手伸出去、"
                "停住、又收回来。",
    "抽象状态": "这句写的是心里的状态，摄影机拍不到。改成演得出来的样子："
                "他的手、他的呼吸、他的身体朝向、他脸上哪块肌肉在动。",
    "否定外观": "这句用「没有X」来写外观，画图模型看到 X 这个词照样会画出 X。改成正面写它有什么："
                "没有腿→四条裹着人皮的肢体贴地撑着；没有眼白→整只眼球从眼角到眼角一色的黑；"
                "没有流血→切口里露出灰白干硬的皮层；没有头发→头顶一片光滑的苍白皮肤。",
    "触感": "这句写的是摸到的温度或麻木，摄影机拍不到。改成看得见的：温热的血→血在冷空气里冒着白气；"
            "虎口发麻→握剑的五指张开又立刻攥紧；冰凉→皮肤起了一层疙瘩、肩膀缩起来。",
    "纯声音": "这句写的是声音本身，摄影机拍不到，视频模型会把它当旁白念出来。"
              "改成**发出声音的东西正在做什么**：器物响写那件器物在动、布料撕裂写布被什么撑开；"
              "写人的嗓音、语气的，改成他说话时看着谁、眉眼和嘴角是什么样、手在做什么——写表情和姿态，不写喉部、声带、舌头这些器官；"
              "句子里原有的动作照原样留着。",
    "气味温度": "这句写的是气味或温度，摄影机拍不到。改成闻到、感到的人的可见反应："
                "皱鼻、屏住呼吸、呼出白气、缩起肩膀、拉紧衣领、抬手挡在鼻前；"
                "句子里原有的地方和物件照原样留着。",
    "内感": "这句写的是身体里面的感觉（气流、血液、心跳、发热、刺痛），摄影机拍不到，"
            "而且照着画会把人画变形。改成外面看得见的：皮肤上那个印记亮起来或颤动、"
            "脖颈青筋鼓起、汗珠滚下、肩膀绷紧、手指发抖、脸色发白、呼吸变粗。"
            "感觉是从哪个部位起的，就写那个部位外面的变化。",
}


_PACE_ASK = """你是短片编辑。下面是一话故事，段落已编号。只回三个数字，格式「冲突：N；交手：K；高潮：M」：
· 冲突：一句话故事里那个要面对的东西（那个人/那头怪物/那件事）**第一次正式出现在画面里**是第几段；
· 交手：双方**第一次身体接触或真正动手**（扑、砍、抓、撞）是第几段；
· 高潮：最紧张的最后一轮对抗从第几段开始。
不要解释。"""


def _quotes_of(text):
    return [m.group(1) for m in re.finditer(r"[「“]([^」”\n]+)[」”]", str(text or ""))]


_ONOMA = re.compile(r"[“「\"]([铿铛砰噗嗤咔嚓哗啦轰嘭嘶咚哐当叮咣噼啪呲滋咕咯吱嗒嗖唰刷！。…—]{1,6})[”」\"](?:的一声|一声)?")


def strip_onomatopoeia(text):
    """引号里的纯拟声（“铿！”“砰！”“噗嗤。”）不是台词，下游会当台词配给某个人念出来。
    独立成段的整段删掉；夹在句子里的连引号一起删。"""
    t = str(text or "")
    paras = re.split(r"(\n\s*\n)", t)
    out = []
    for p in paras:
        if re.fullmatch(r"\s*" + _ONOMA.pattern + r"\s*", p):
            continue                                  # 整段就是一个拟声
        out.append(_ONOMA.sub("", p))
    t = "".join(out)
    return re.sub(r"(\n\s*\n)(\s*\n)+", r"\1", t)   # 删段后留下的多余空行


def _quotes_kept(old, new):
    norm = lambda q: re.sub(r"[^\w一-龥]", "", q)
    have = {norm(x) for x in _quotes_of(new)}
    return [q for q in _quotes_of(old) if norm(q) not in have]


def _rewrite_block(block, sysm, lo, hi, allow_new_quotes=False, temperature=0.5, tries=2):
    """定向重写一块正文。守卫：原台词一句不少；字数在 [lo, hi]；（可选）不许新加台词。
    只差字数的，带着上次的字数再让它改一次（最多 tries 次）；丢台词/加台词的直接丢弃。"""
    why = "没跑"
    src = block
    for k in range(tries):
        try:
            new = _q(sysm, src, mt=3000, temperature=temperature)
        except Exception as ex:
            return None, "调用失败：%s" % str(ex)[:60]
        new = re.sub(r"^\s*[〔\[【(（]\s*\d{1,3}\s*[〕\]】)）]\s*", "", str(new or ""), flags=re.M).strip()
        if not new:
            return None, "空输出"
        miss = _quotes_kept(block, new)
        if miss:
            return None, "丢了台词：%s" % miss[0][:16]
        if not allow_new_quotes and len(_quotes_of(new)) > len(_quotes_of(block)):
            return None, "新加了台词"
        _hi = hi if k == 0 else int(hi * 1.3)            # 第二次放宽三成——差一点点就别整块丢
        _lo = lo if k == 0 else int(lo * 0.8)
        if _lo <= len(new) <= _hi:
            return new, "ok" if k == 0 else "ok（第%d次）" % (k + 1)
        why = "字数 %d 不在 %d～%d" % (len(new), lo, hi)
        # 只差字数：把它自己的稿子发回去，让它在这个基础上按字数改
        src = new
        sysm = sysm + ("\n\n（上一稿 %d 字，%s了。在上一稿的基础上%s，严格控制在 %d～%d 字，其余要求不变。）"
                       % (len(new), "多" if len(new) > hi else "少", "再删几句" if len(new) > hi else "再补几句", lo, hi))
    return None, why


_EDIT_PROTOCOL = """输出格式（只输出这个，不要任何说明）：每条一行——
〔段号〕原句 ⇒ 新句
〔段号〕+ ⇒ 要追加到这一段末尾的一句
规则：原句必须**从那一段里逐字抄下来的一整句**（到句号/叹号/问号为止），替换条目不能省掉原句；追加条目原句位置写「+」；
新句只写摄影机拍得到的东西，
**正面描述**（写它有什么、在做什么，不写它没有什么）；引号里的台词一个字不许改、不许删、不许新加；
一次最多 %d 条，只改最要紧的。"""

_SETTING_CHECK = """你是这部片子的执行导演，手里有【人物卡】【场景卡】和这一话的【正文】（段落已编号）。
逐段对照，找出正文与卡片**对不上**、或者卡片里有而正文**该点出却没点出**的地方：
① 身体结构：肢体数量、有没有翅膀/腿/头发/尾巴，与卡片一致；正文里第一次出现这个人物时，把卡片的形体写进去；
② 标志物：独眼、巨剑（比人高的剑背在背上，拔剑要先卸下剑带、双手用）、烙印的样子，出现时按卡片写；
③ 衣着遮挡：被衣甲遮住的部位不写落点（汗滴在锁骨上→滴在锁甲上）；
④ 场景硬件：门有没有、雨从哪灌进来、积水在哪、石梁/拱券/石柱各在哪，按场景卡；
⑤ 天气：卡片和简介里的天气从**第一段**就要立起来，之后每隔几段带一笔（雨落在哪、水面怎么动）。
""" + _EDIT_PROTOCOL

_SPACE_CHECK = """你是这部片子的执行导演，手里有【场景卡】和这一话的【正文】（段落已编号）。检查**空间和位置**：
① 第一次写到这个地方时，一次写清：门在哪、主要物件（祭坛/石柱/空地/拱券/穹顶缺口）各在哪、彼此隔多远、
   每个人物站在哪、面朝哪；
② 人物转身、后退、闪避，都写方向和终点（朝祭坛退了两步／退到左手那根石柱边）；转过身之后再面对对手要写转回来；
③ 在场但这一阵没动作的人物，**每隔五六段**交代一次：在哪、朝哪看、手在做什么；
④ 位置跳变（地面→高处、屋里→屋外）要有看得见的过程（四肢扒着石柱往上爬……）；
⑤ 视线要成立：站在正前方的人看不到对方的颈后；要看就先让对方侧身。
""" + _EDIT_PROTOCOL

_FIGHT_CHECK = """你是这部片子的武术指导，手里有【人物卡】和这一话的【正文】（段落已编号）。检查**打戏的物理**：
① 道具：剑在谁手里、卡在哪、怎么抽出来的、掉在哪——每换一拍都交代，前后接得上；
② 重量：两米高的东西不能被单手甩飞；比人高的巨剑双手握；被压时膝盖弯、后退半步；
③ 距离：十步远不能一扑就到，先缩距再扑；
④ 真人演员能演：脚踩在地上，站、蹲、退、转、劈、刺、格挡、撞、摔；一件东西不能既在对方嘴里又刺进对方膝盖；
⑤ 每一下有落点和痕迹（刃口卡在皮里、石柱上留下抓痕、靴底蹭出水痕）；铁砍在皮肉上没有火星。
""" + _EDIT_PROTOCOL


_ONE_OFF_ACT = re.compile(r"碰|摸|抚|拂|指腹|耳后|递给|递上|接过|走到|走向|坐下|站起|转身|抬手|伸手|亲|吻|拥抱|抱住|牵|握住")


_ONE_OFF_ACT = re.compile(r"碰|摸|抚|拂|指腹|耳后|递给|递上|接过|走到|走向|坐下|站起|转身|抬手|伸手|亲|吻|拥抱|抱住|牵|握住")


def _chars_digest(chars, wardrobe_row=None, ledger_lines=None):
    """人物卡压成几行：身形/头发/外貌细节/衣着/动作习惯。
    wardrobe_row（P329）：这一段每个人的衣着状态——不是「参考图那一身」时，衣着一栏写它，不写人设卡的衣服（全裸时写连体衣就错了）。
    ledger_lines（P445③）：{名: 上一话结束时的衣着/伤}——续话的提示词写手要知道谁包扎过。"""
    out = ["【人物卡】"]
    for c in chars or []:
        bits = []
        _ll = str((ledger_lines or {}).get(str(c.get("name") or "")) or "").strip()
        if _ll:
            bits.append("此刻（上一话结束时）：" + _ll[:120])
        _wst = ""
        try:
            _wst = str(((wardrobe_row or {}).get(str(c.get("name") or "")) or {}).get("end") or "")
        except Exception:
            _wst = ""
        if _wst == "参考图那一身":
            _wst = ""
        for k in ("build", "hair", "appearance_details", "clothing", "behavior_anchor"):
            v = str(c.get(k) or "").strip()
            if k == "clothing" and _wst:
                bits.append("衣着：本段%s（人设图那一身只是参考，此刻不是）" % _wst)
                continue
            if not (4 < len(v) < 300):
                continue
            if k == "behavior_anchor" and _ONE_OFF_ACT.search(v):
                continue                                                    # P322⑦：一次性剧情动作（碰耳后）不是习惯，喂进去会被提前演
            if k == "behavior_anchor" and _ONE_OFF_ACT.search(v):
                continue                                                    # P322⑦：一次性剧情动作（碰耳后）不是习惯，喂进去会被提前演
            # 卡片里的脏字段（内感/触感/纯声音/气味）不进摘要——喂进去就又带回正文
            if any(p.search(v + "。") for p in (_PAT_NEIGAN, _PAT_CHUGAN, _PAT_SHENGYIN, _PAT_QIWEI)):
                continue
            v = _tidy_sentence(_SIMILE_CLAUSE.sub("", v)) or v
            bits.append("%s：%s" % ({"build": "身形", "hair": "头发", "appearance_details": "外貌细节",
                                      "clothing": "衣着", "behavior_anchor": "动作习惯"}[k], v))
        if bits:
            out.append("· %s —— %s" % (c.get("name") or "", "；".join(bits)))
    return "\n".join(out) if len(out) > 1 else ""


def _cards_digest(sid):
    """人物卡、场景卡压成一段文字给审校用。"""
    try:
        from . import asset_core as _ac
        chars = _ac.list_assets(sid, "characters") or []
        scenes = _ac.list_assets(sid, "scenes") or []
    except Exception:
        return ""
    out = [_chars_digest(chars)]
    for c in []:
        bits = []
        for k in ("build", "hair", "appearance_details", "clothing", "behavior_anchor"):
            v = str(c.get(k) or "").strip()
            if 4 < len(v) < 300:
                bits.append("%s：%s" % ({"build": "身形", "hair": "头发", "appearance_details": "外貌细节",
                                          "clothing": "衣着", "behavior_anchor": "动作习惯"}[k], v))
        if bits:
            out.append("· %s —— %s" % (c.get("name") or "", "；".join(bits)))
    out.append("【场景卡】")
    seen = set()
    try:
        _adopted = {str(v.get("owner_id") or "") for v in (_ac.list_assets(sid, "visuals") or [])
                    if v.get("adopted") or v.get("status") in ("adopted", "已采用")}
    except Exception:
        _adopted = set()
    _imaged = [sc for sc in scenes if str(sc.get("scene_id") or sc.get("id") or "") in _adopted]
    scenes = _imaged or scenes
    for sc in scenes:
        bits = []
        for k in ("space", "light", "ground", "furniture", "landmarks"):
            v = str(sc.get(k) or "").strip()
            if 4 < len(v) < 300 and v not in seen:
                seen.add(v)
                bits.append(v)
        if bits:
            out.append("· %s —— %s" % (sc.get("name") or "", "；".join(bits)))
    return "\n".join(out)


def _parse_edits(rep):
    edits = []
    for line in str(rep or "").splitlines():
        m = re.match(r"^\s*[〔\[【(（]\s*(\d{1,3})\s*[〕\]】)）]\s*(.*?)\s*(?:⇒|=>|→|->)\s*(.+?)\s*$", line)
        if not m:
            continue
        n, old, new = int(m.group(1)), m.group(2).strip(), m.group(3).strip()
        old = re.sub(r"^(?:原句|原文)\s*[：:]\s*", "", old).strip()
        new = re.sub(r"^(?:新句|改为|改成)\s*[：:]\s*", "", new).strip()
        new = re.sub(r"\s*\+\s*", "", new)
        new = new.strip("「」“”\"' ")
        if not new or not old:                    # 省掉原句的条目不要——分不清是替换还是追加
            continue
        edits.append((n, old, new))
    return edits


def _apply_edits(prose, edits, cap=14):
    """逐条核对再替换：原句要在那一段里逐字存在、不在引号里；新句不能带引号（台词不许动）。
    整体守卫：台词集合不变、字数 0.9～1.35 倍；不过就原文返回。"""
    paras = [p for p in re.split(r"\n\s*\n", str(prose or "").strip()) if p.strip()]
    applied = 0
    for n, old, new in edits:
        if applied >= cap or not (1 <= n <= len(paras)):
            continue
        if re.search(r"[「」“”\"]", new):          # 新句不许带台词
            continue
        p = paras[n - 1]
        if old in ("+", "＋"):
            if new[:10] in p or new[:10] in "".join(paras):      # 已经有了，别重复
                continue
            paras[n - 1] = p.rstrip() + ("" if p.rstrip().endswith(("。", "！", "？")) else "。") + new
            applied += 1
            continue
        old_s = old.strip("「」“”\"' ")
        pos = p.find(old_s)
        if pos < 0 or _pos_in_quote(p, pos) or re.search(r"[「」“”\"]", old_s) or new == old_s:
            continue
        if len(new) > len(old_s) * 1.8 + 30:            # 新句不许比原句长太多——长出来的都是编的
            continue
        if new[:12] in "".join(paras).replace(old_s, "", 1):   # 新句已经在别处 → 会造成重复
            continue
        paras[n - 1] = p[:pos] + new + p[pos + len(old_s):]
        applied += 1
    out = "\n\n".join(paras)
    if _quotes_kept(prose, out) or len(_quotes_of(out)) != len(_quotes_of(prose)):
        return prose, 0, "改动了台词，整批丢弃"
    if not (len(prose) * 0.9 <= len(out) <= len(prose) * 1.35):
        return prose, 0, "字数越界（%d→%d），整批丢弃" % (len(prose), len(out))
    return out, applied, "ok"


def _check_pass(prose, sysm, digest, cap=14, temperature=0.3, log_tag=""):
    paras = [p for p in re.split(r"\n\s*\n", str(prose or "").strip()) if p.strip()]
    numbered = "\n\n".join("〔%d〕%s" % (i + 1, p) for i, p in enumerate(paras))
    try:
        rep = _q(sysm % cap, (digest + "\n\n" if digest else "") + "【正文】\n" + numbered, mt=2600, temperature=temperature)
    except Exception as ex:
        return prose, "调用失败：%s" % str(ex)[:60]
    edits = _parse_edits(rep)
    out, n, why = _apply_edits(prose, edits, cap=cap)
    _dbg("审校·" + log_tag, {"模型给": len(edits), "采纳": n, "结果": why,
                             "样例": [(e[0], e[1][:24], e[2][:40]) for e in edits[:6]],
                             "原话": str(rep or "")[:600]})
    return out, "%s：给%d条采纳%d条（%s）" % (log_tag, len(edits), n, why)


def story_setting_pass(prose, sid):
    """设定对齐 → 空间位置 → 打戏物理，三次定向审校（每次一调用），每次都走替换表守卫。"""
    digest = _cards_digest(sid)
    notes = []
    # 空间位置这一道实测会编道具和站位（v11：艾莎"按剑柄""抚十字架"），去掉；位置交代交给剧本层导演清单
    for sysm, tag in ((_SETTING_CHECK, "设定对齐"), (_FIGHT_CHECK, "打戏物理")):
        prose, note = _check_pass(prose, sysm, digest, cap=8, log_tag=tag)
        notes.append(note)
    return prose, "；".join(notes)


_META_SENT = re.compile(r"(这一话|本话|这一集|结构行|事实单|落点|在场栏|人物表|简介里|按结构|不出场)")


_LOOK_WORD = re.compile(r"肤色|皮肤|瞳孔|瞳仁|眼裂|眼尾|眼窝|鼻梁|下颌|颧骨|发型|头发|发丝|长袍|外套|衬衫|马甲|裙|裤|领口|袖口|面料|质地|棉|绸|缎|呢|针织|刺绣|纹路|轮廓分明|五官")
_ACT_WORD = re.compile(r"说|问|答|走|跑|站|坐|抬|放|推|拉|递|接|拿|握|转|停|看向|伸手|低头|回头|皱|笑|喊|摸|翻|按|挂|扫|踩|敲|拧|捏|塞|掏|抽|落|扬|甩|靠|蹲|跪|撑|挤|端|举")


_CARD_SPEC_SUB = None
_CARD_PARAM_SEG = None


def clean_prose_common(prose, settings, ep=1, log=None):
    """两条正文路（write_prose / gen_prose）共用的最后一道确定性清理（P165①）。

    以前只挂在 gen_prose 上，从「生成故事」进来的第一话没跑到——同一个项目，
    第一话带着人物卡参数和《地点》书名号，第二话干净，两条路出来的稿子不一样。
    """
    p = str(prose or "")
    # 地点被写成书名号（实测开头写「《田径场》的午后」）
    try:
        _row = (settings.get("plan_rows") or [])[int(ep) - 1]
        for _pn in re.split(r"[、，,／/及和]", str(_row.get("place") or "")):
            _pn = _pn.strip()
            if _pn and ("《%s》" % _pn) in p:
                p = p.replace("《%s》" % _pn, _pn)
                if isinstance(log, list):
                    log.append("地点去书名号：" + _pn)
    except Exception:
        pass
    p, _n = strip_card_spec(p, log=log)          # P163③ 人物卡参数清出正文
    return p


def _salvage_subject(seg):
    """被删掉的第一个分句里如果只剩一个人名/称呼，把它留下来当主语——
    否则「总监眼型细长，目光扫过办公区」会变成没主语的「目光扫过办公区」（P163b）。"""
    t = re.sub(r"(眼型|眼尾|眼裂|颧骨|下颌线|面部骨骼|骨骼感|头身比|不笑时|肤色|冷白皮)[^，。！？；]*", "",
               str(seg or "")).strip()
    t = re.sub(r"[的了着地]$", "", t)
    return t if 1 <= len(t) <= 4 and not re.search(r"[，。！？；\s]", t) else ""


def strip_card_spec(prose, log=None):
    """把人物卡的参数从正文里清掉（P163③，确定性清理，不问模型）：
      · 面料规格词删掉、衣服留着：「深灰色的高支数棉质圆领T恤」→「深灰色的圆领T恤」
      · 纯五官参数的分句整段删：「眼尾微微下垂」「颧骨高挺」「下颌线紧绷」
      · 瞳色修饰删掉：「深褐色的眼眸紧紧盯着」→「眼眸紧紧盯着」
    整句都是参数（「眼尾微微下垂，不笑时眼神专注且略带疲惫。」）就整句删。
    """
    global _CARD_SPEC_SUB, _CARD_PARAM_SEG
    if _CARD_SPEC_SUB is None:
        _CARD_SPEC_SUB = re.compile("|".join(_SPEC_WORDS))
        _CARD_PARAM_SEG = re.compile(
            r"^(?:[^，。！？；]{0,4})?(?:"
            r"眼裂[^，。！？；]*|眼尾[^，。！？；]*|颧骨[^，。！？；]*|下颌线[^，。！？；]*|"
            r"面部骨骼[^，。！？；]*|骨骼感[^，。！？；]*|不笑时[^，。！？；]*|笑起来时[^，。！？；]*|"
            r"[^，。！？；]{0,6}肤色是[^，。！？；]*|[^，。！？；]{0,6}冷白皮[^，。！？；]*|"
            r"勾勒出[^，。！？；]*(?:肌肉|轮廓|线条)[^，。！？；]*|"
            r"[^，。！？；]{0,4}瞳孔[^，。！？；]{0,6}(?:收缩|扩张)[^，。！？；]*"
            r")$")
    n = [0, 0]
    out_paras = []
    for para in str(prose or "").split("\n"):
        if not para.strip():
            out_paras.append(para)
            continue
        keep_s = []
        for sent in re.split(r"(?<=[。！？])", para):
            if not sent.strip():
                continue
            tail = ""
            m = re.search(r"[。！？]+[”\"』」]*$", sent)
            if m:
                tail = m.group(0)
                core = sent[:m.start()]
            else:
                core = sent
            segs = re.split(r"([，、；])", core)
            kept, i, dropped, subj = [], 0, False, ""    # subj：被删分句里捞回来的主语，挂到下一个留下的分句上
            while i < len(segs):
                seg = segs[i]
                sep = segs[i + 1] if i + 1 < len(segs) else ""
                i += 2
                if seg.strip() and _CARD_PARAM_SEG.match(seg.strip()):
                    n[1] += 1
                    dropped = True
                    subj = subj or _salvage_subject(seg)     # 主语别跟着参数一起删
                    continue
                # 参数词夹在分句中间（「流经棱角分明的下颌线」「目光落在她苍白的下颌线上」）：
                # 整个分句删掉。带引号的分句不动，免得把台词一起删了。
                if (re.search(r"下颌线|颧骨|眼裂|眼尾|眼型|骨骼感|头身比", seg)
                        and not re.search(r"[“”\"「」『』：:]", seg)):
                    n[1] += 1
                    dropped = True
                    subj = subj or _salvage_subject(seg)
                    continue
                if _CARD_SPEC_SUB.search(seg):
                    seg = _CARD_SPEC_SUB.sub("", seg)
                    n[0] += 1
                seg = re.sub(r"(?:深|浅|淡)?[一-龥]{1,3}色的?(?=(?:瞳孔|眼眸|眼瞳|眸子))", "", seg)
                if seg.strip():
                    if subj and not seg.strip().startswith(subj):
                        seg = subj + seg.lstrip()
                    subj = ""
                    kept.append(seg + sep)
            core = "".join(kept).rstrip("，、；")
            if not core.strip():
                continue
            # 删掉分句后剩下没主语的残句（「此刻更是抿成一条线。」）——整句丢掉，
            # 别在正文里留半截话（P163b，实测体育第1话出过）
            if dropped and re.match(r"^(此刻|此时|这时|随即|然后|接着|而|更|也|却|但|又|于是|随后)",
                                    core.strip()):
                n[1] += 1
                continue
            keep_s.append(core + tail)
        p2 = "".join(keep_s)
        p2 = re.sub(r"[，、；]{2,}", "，", p2)
        p2 = re.sub(r"^[，、；]+", "", p2)
        out_paras.append(p2)
    res = "\n".join(out_paras)
    if isinstance(log, list) and (n[0] or n[1]):
        log.append("删面料规格 %d 处、五官参数分句 %d 句" % (n[0], n[1]))
    return res, n[0] + n[1]


def trim_look_paragraphs(prose, log=None, limit=3):
    """开头几段里的纯外貌句（只写长相穿着、没有任何动作）超过 limit 句 → 删多余的（P155①）。
    只在正文前 40% 生效；带动作动词的句子一律不动。"""
    paras = [p for p in re.split(r"\n\s*\n", str(prose or "")) if p.strip()]
    if len(paras) < 4:
        return prose, 0
    head_n = max(2, int(len(paras) * 0.4))
    kept, n, seen = [], 0, 0
    for i, p in enumerate(paras):
        if i >= head_n:
            kept.append(p)
            continue
        out = []
        for sent in re.split(r"(?<=[。！？])", p):
            if not sent.strip():
                continue
            if _LOOK_WORD.search(sent) and not _ACT_WORD.search(sent) and "“" not in sent and "「" not in sent:
                seen += 1
                if seen > limit:
                    n += 1
                    continue
            out.append(sent)
        t = "".join(out).strip()
        kept.append(t if t else p)
    if n and log is not None:
        log.append("开头删掉纯外貌句 %d 句" % n)
    return "\n\n".join(kept), n


def strip_meta_sentences(prose, log=None):
    """正文里把提示词的话写进去了（「虽然这一话里总监没出场，但他的影子…」）→ 删这一句（P151）。"""
    out, n = [], 0
    for para in re.split(r"\n\s*\n", str(prose or "")):
        keep = []
        for sent in re.split(r"(?<=[。！？])", para):
            if sent.strip() and _META_SENT.search(sent) and "“" not in sent and "「" not in sent:
                n += 1
                continue
            keep.append(sent)
        t = "".join(keep).strip()
        if t:
            out.append(t)
    if n and log is not None:
        log.append("删掉写进正文的提示词话 %d 句" % n)
    return "\n\n".join(out), n


def clean_prose_fallback(prose):
    """正文层代码兜底：模型换不掉的拍不到短句直接删。引号里的台词先换成占位符，一个字不动。"""
    t = str(prose or "")
    kept = []

    def _mask(m):
        kept.append(m.group(0))
        return "\u2591DLG%d\u2591" % (len(kept) - 1)

    masked = re.sub(r"[「“][^」”\n]*[」”]", _mask, t)
    out, n = strip_unfilmable_clauses(masked)
    for i, q in enumerate(kept):
        out = out.replace("\u2591DLG%d\u2591" % i, q)
    if "\u2591DLG" in out or len(_quotes_of(out)) != len(_quotes_of(t)):
        return t, 0
    return out, n


_EVENT_ASK = """你是短片剪辑。下面是一话故事，段落已编号。列出**改变人物关系或处境的事件**——某人对某人做了一个动作、说了一句改变局面的话、
一个新人物出现、一个决定。只回段号，用逗号分开，按先后排，例如「3,7,12」。走路、张望、对视、描写外貌不算事件。不要解释。"""


def story_event_pass(prose, one_line="", digest="", log=None):
    """事件表审校：第一个事件 >8% → 压开场（复用 _rewrite_block）；任一连续空窗 >30% → 在空窗块里补一个可见事件。"""
    paras = [p for p in re.split(r"\n\s*\n", str(prose or "").strip()) if p.strip()]
    n = len(paras)
    if n < 6:
        return prose, "段太少不审"
    numbered = "\n\n".join("〔%d〕%s" % (i + 1, p) for i, p in enumerate(paras))
    try:
        rep = _q(_EVENT_ASK, numbered, mt=60, temperature=0.1)
    except Exception as ex:
        return prose, "事件问答失败：%s" % str(ex)[:50]
    ev = sorted({int(x) for x in re.findall(r"\d+", str(rep or "")) if 1 <= int(x) <= n})
    if not ev:
        return prose, "没识别出事件：%s" % str(rep or "")[:30]
    lens = [len(p) for p in paras]
    tot = max(1, sum(lens))
    cum = [0]
    for L in lens:
        cum.append(cum[-1] + L)
    pos = lambda k: cum[k - 1] / tot                 # 第 k 段开头的位置占比
    notes = []
    _dbg("事件表", {"事件段": ev, "首事件位置": round(pos(ev[0]), 2)})
    # ① 开场：第一个事件之前超过 8%
    if pos(ev[0]) > 0.08 and ev[0] > 1:
        head = paras[:ev[0] - 1]
        target = int(tot * 0.08)
        sysm = ("你是这篇故事的作者本人。下面是第一个事件之前的开场。把它**压缩到 %d 字以内、一段**："
                "只留正在发生的那件事和人物在哪、光从哪来；走路、天气、张望、外貌铺陈整句删掉；留下的句子照原样写；"
                "**引号里的台词一字不改、一句不少**；每句台词连着说话人。只输出这一段，不要段号、不要说明。" % max(target, 60))
        new, why = _rewrite_block("\n\n".join(head), sysm, 30, int(max(target, 60) * 1.3))
        if new:
            paras = [p for p in re.split(r"\n\s*\n", new) if p.strip()] + paras[ev[0] - 1:]
            notes.append("开场压到 %d 字" % len(new))
            # 段号变了，重算
            lens = [len(p) for p in paras]; tot = max(1, sum(lens)); cum = [0]
            for L in lens:
                cum.append(cum[-1] + L)
            shift = ev[0] - 1 - len([p for p in re.split(r"\n\s*\n", new) if p.strip()])
            ev = [max(1, e - shift) for e in ev]
        else:
            notes.append("开场压缩丢弃（%s）" % why)
    # ② 空窗：相邻事件之间（含开头到首事件、末事件到结尾）超过 30%
    n = len(paras)
    marks = [0] + [e - 1 for e in ev] + [n]
    gaps = []
    for a, b in zip(marks, marks[1:]):
        if b - a >= 3 and (cum[b] - cum[a]) / tot > 0.30:
            gaps.append((a, b))
    if gaps:
        a, b = max(gaps, key=lambda g: cum[g[1]] - cum[g[0]])
        blk = paras[a:b]
        cur = sum(len(p) for p in blk)
        sysm = ("你是这篇故事的作者本人。下面这一块里没有任何改变人物关系或处境的事，观众会觉得慢。"
                "在**不删原有句子、不加新人物、不换地方**的前提下，往这一块里加一个由这句话故事驱动的可见事件——"
                "一个人对另一个人做一个动作（插话、拉住、挡在中间、夺过、递过去、逼近、后退）并带一句新台词，"
                "写清谁、用哪只手、朝哪、对方怎么接。一句话故事：%s。字数不超过 %d。"
                "只输出改后的这一块，段落之间空一行，不要段号、不要说明。" % (str(one_line or "")[:120], int(cur * 1.4) + 120))
        if digest:
            sysm += "\n\n" + digest
        new, why = _rewrite_block("\n\n".join(blk), sysm, cur, int(cur * 1.6) + 150, allow_new_quotes=True, temperature=0.5)
        if new:
            paras = paras[:a] + [p for p in re.split(r"\n\s*\n", new) if p.strip()] + paras[b:]
            notes.append("空窗块 %d~%d 段补了事件" % (a + 1, b))
        else:
            notes.append("空窗补事件丢弃（%s）" % why)
    if not notes:
        return prose, "事件分布达标（%d 个事件，首事件 %.0f%%）" % (len(ev), pos(ev[0]) * 100)
    return "\n\n".join(paras), "；".join(notes)


def story_pace_pass(prose, settings=None, log=None, digest=""):
    """序章节奏审校：问 Qwen 冲突/高潮在第几段（按**字数占比**判），
    开场太长 → 只把开场发给它压到 ≤15%；高潮太短 → 只把高潮发给它扩到 ≥3 回合。
    中段代码原样拼回。每一块单独守卫（台词一句不少、字数在范围内），不过就保留原文。
    """
    paras = [p for p in re.split(r"\n\s*\n", str(prose or "").strip()) if p.strip()]
    n = len(paras)
    if n < 6:
        return prose, "段太少不审"
    numbered = "\n\n".join("〔%d〕%s" % (i + 1, p) for i, p in enumerate(paras))
    try:
        rep = _q(_PACE_ASK, numbered, mt=40, temperature=0.1)
    except Exception as ex:
        return prose, "问答失败：%s" % str(ex)[:60]
    m1 = re.search(r"冲突[：:]\s*(\d+)", str(rep or ""))
    m2 = re.search(r"高潮[：:]\s*(\d+)", str(rep or ""))
    m3 = re.search(r"交手[：:]\s*(\d+)", str(rep or ""))
    if not (m1 and m2):
        return prose, "问答格式不对：%s" % str(rep or "")[:40]
    c, h = int(m1.group(1)), int(m2.group(1))
    c = min(max(c, 2), n)
    h = min(max(h, c + 1), n)
    k = int(m3.group(1)) if m3 else c
    k = min(max(k, c), h)
    lens = [len(p) for p in paras]
    tot = max(1, sum(lens))
    before = sum(lens[:c - 1]) / tot        # 冲突登场前占全篇字数
    standoff = sum(lens[c - 1:k - 1]) / tot  # 登场到第一次交手之间（对峙）
    tail = sum(lens[h - 1:]) / tot          # 高潮占全篇字数
    late_open = before > 0.25
    long_standoff = standoff > 0.12 and (k - c) >= 4
    short_climax = tail < 0.15
    _dbg("序章节奏", {"段数": n, "冲突段": c, "铺垫占比": round(before, 2), "交手段": k, "对峙占比": round(standoff, 2),
                    "高潮起": h, "高潮占比": round(tail, 2), "开场慢": late_open, "对峙长": long_standoff, "高潮短": short_climax})
    if not (late_open or short_climax or long_standoff):
        return prose, "节奏达标（铺垫%.0f%%，对峙%.0f%%，高潮%.0f%%）" % (before * 100, standoff * 100, tail * 100)
    notes = []
    head, mid, end = paras[:c - 1], paras[c - 1:h - 1], paras[h - 1:]
    if long_standoff:
        # 对峙块（登场→交手前）单独压：台词一句不少，压到全篇 8% 以内
        blk = paras[c - 1:k - 1]
        target = int(tot * 0.12)
        sysm = ("你是这篇故事的作者本人。下面是对手登场之后、第一次交手之前的对峙部分。把它**压缩到 %d 字以内**："
                "整句整句地删——外形描写只留一段、眼睛睁开合成一句、解释声音怎么发出来的删掉、重复的对视删掉；"
                "留下来的句子照原样写。**引号里的台词一字不改、一句不少**，不加新台词。"
                "只输出压缩后的正文，段落之间空一行，不要段号、不要说明。" % target)
        new, why = _rewrite_block("\n\n".join(blk), sysm, int(tot * 0.06), int(target * 1.4))
        if new:
            mid = [p for p in re.split(r"\n\s*\n", new) if p.strip()] + paras[k - 1:h - 1]
            notes.append("对峙 %.0f%%→压到 %d 字" % (standoff * 100, len(new)))
        else:
            notes.append("对峙压缩丢弃（%s）" % why)
    if late_open:
        target = int(tot * 0.18)
        sysm = ("你是这篇故事的作者本人。下面是这一话的开场（冲突还没出现的部分）。把它**压缩到 %d～%d 字**，"
                "分成两到三段。压的办法是**整句整句地删**：走路、进门、放东西、张望、外貌铺陈、气味、声音这些句子删掉；"
                "留下来的句子**照原样写，不改写、不缩成短语**。必须留的是：**主角进入这个地方的那一句**"
                "（从哪、怎么进来的）、这个地方长什么样、光从哪来、人物各站在哪、正在做的那件事、全部台词。"
                "第一段里要出现主角的名字。**引号里的台词一字不改、一句不少**，不加新台词；"
                "**每句台词所在的那一句要连着说话人一起留**（「XX说」「XX停下脚步」这类交代谁在说的话不能删）。"
                "只输出压缩后的正文，段落之间空一行，不要段号、不要说明。" % (int(tot * 0.12), target))
        new, why = _rewrite_block("\n\n".join(head), sysm, int(tot * 0.10), int(target * 1.15))
        if new:
            head = [p for p in re.split(r"\n\s*\n", new) if p.strip()]
            notes.append("开场 %.0f%%→压到 %d 字" % (before * 100, len(new)))
        else:
            notes.append("开场压缩丢弃（%s）" % why)
    if short_climax:
        cur = sum(lens[h - 1:])
        lo, hi = max(int(cur * 1.5), 200), max(int(cur * 3), 500)
        hi = max(lo + 60, min(hi, 2600 - (tot - cur)))        # 全篇别超 2600 字
        sysm = ("你是这篇故事的作者本人。下面是这一话的高潮（最后一轮对抗）。把它**扩写到 %d～%d 字**，"
                "写成**三个回合，每个回合三段，每段一句、不超过 60 字**：第一段对方做了什么（从哪到哪、用哪个部位）；"
                "第二段主角怎么接（用剑还是用身体、朝哪个方向、脚踩在哪）；第三段这一下留下了什么痕迹"
                "（伤口、裂开的东西、溅到哪的血、谁喘气）。"
                "这是真人实拍的打戏：**主角的双脚全程踩在地面或石板上**，动作只有站、蹲、退、转身、劈、刺、"
                "格挡、撞、摔；每一下都有重量、有落点。对方落到地面之后两人才交手。"
                "不加新人物、不换地方、**不加新台词**；**引号里的台词一字不改、一句不少**；"
                "最后一段停在一个**没做完的动作**上（举到一半、刺出去还没到、抓住了还没拉开）。"
                "人物的身体结构、肢体数量、标志物按下面的人物卡写，卡片没有的部位不长出来。"
                "只输出扩写后的正文，段落之间空一行，不要段号、不要说明。" % (lo, hi))
        if digest:
            sysm += "\n\n" + digest
        new, why = _rewrite_block("\n\n".join(end), sysm, int(lo * 0.85), int(hi * 1.15), temperature=0.3)
        if new:
            end = [p for p in re.split(r"\n\s*\n", new) if p.strip()]
            notes.append("高潮 %.0f%%→扩到 %d 字" % (tail * 100, len(new)))
        else:
            notes.append("高潮扩写丢弃（%s）" % why)
    out = "\n\n".join(head + mid + end)
    return out, "；".join(notes)


def replace_bad_sentences(text, log=None, batch=8, context=""):
    """一次调用，只换句子。

    做法：把出问题的句子编号列给模型，要它**逐条只回一句改好的**，
    然后代码把原句替换成新句。正文其余部分一个字不动，也不删任何东西。
    每一句单独验收，验不过的那一句保持原样，不影响别的句子。

    分批发：一次给 20 条时模型会**把原句原样抄回来**，20 条一条都没换成
    （2026-08-31 实测剑风段）。八条一批，并且明写"抄回原句算没做"。
    log 传一个 list 进来的话，会把每一条的 (类型, 原句, 新句, 结果) 记下来。
    """
    t = str(text or "")
    bad = collect_bad_sentences(t)
    if not bad:
        return t
    ok = fail = 0
    for start in range(0, len(bad), batch):
        chunk = bad[start:start + batch]
        kinds = sorted({k for _, k in chunk})
        sysmsg = (
            "你只做一件事：把用户给你的每一句话改写一遍，"
            "改完的句子要能被摄影机拍到。\n"
            + "\n".join("· %s：%s" % (k, _WHY.get(k, "")) for k in kinds) + "\n"
            "规则：\n"
            "① **每一条都必须和原句不一样**。把原句原样抄回来等于没做。\n"
            "② **一句可以改成两三句**。不打比方地把一样东西写清楚，"
            "常常一句话装不下，那就拆开写：先写它整体什么样，再写一个具体的"
            "细节。写成一段连着的话，不要分行。\n"
            "③ 每句里的人、动作、信息一个都不许少，也不许添新的情节；\n"
            "④ 句子里引号中的台词，一个字都不许改；\n"
            "⑤ 逐条回复，一行一条，格式就是「序号. 改好的句子」，"
            "不要解释、不要写原句、不要加空行。"
            + (("\n人物的身体结构和标志物按下面的人物卡写：\n" + context) if context else ""))
        user = "\n".join("%d. 【%s】%s" % (i + 1, k, s)
                         for i, (s, k) in enumerate(chunk))
        try:
            rep = _q(sysmsg, user, mt=1800, temperature=0.4)
        except Exception:
            break
        got = {}
        for line in str(rep or "").splitlines():
            m = re.match(r"^\s*(\d+)\s*[.、．)]\s*(.+?)\s*$", line)
            if m:
                got[int(m.group(1))] = re.sub(r"^【[^】]{1,8}】", "", m.group(2))
        for i, (sent, kind) in enumerate(chunk):
            new = got.get(i + 1, "")
            pat = dict(_UNFILMABLE).get(kind, "")
            # 纯声音/气味温度：要的是"加上发声的东西在做什么/人的反应"，改完还带着声音词是正常的
            # （声音短句下游 H3 层会删），这两类不做"还犯规"复查
            if kind in ("纯声音", "气味温度"):
                pat = ""
            why = ("没给" if not new
                   else "抄回原句" if new.strip() == sent.strip()
                   else "还犯规" if pat and re.search(pat, new)
                   else "改了台词" if any(q not in new for q in _quotes_in(sent))
                   # 【上限放到 3 倍】一句改成两三句是**要的结果**：
                   # 不打比方地写清一样东西，一句话常常装不下
                   # （「云层低垂，像吸饱水的旧抹布」→「厚重的灰黑云层压得很低。
                   # 云底颜色发暗，远处不断有雨幕垂向荒原。」）。
                   # 原来卡 1.8 倍，等于把正确的改法拦在门外（2026-08-31）。
                   else "长度离谱" if not (len(sent) * 0.5 <= len(new)
                                           <= max(len(sent) * 3.0,
                                                  len(sent) + 80))
                   else "")
            if log is not None:
                log.append((kind, sent, new, why or "换掉"))
            if why:
                fail += 1
                _dbg("换句被拒", {"原句": sent[:70], "新句": new[:70],
                                  "原因": why})
                continue
            t = t.replace(sent, new, 1)
            ok += 1
    _dbg("换句结果", {"挑出": len(bad), "换成功": ok, "保持原样": fail})
    return t


def _polish_leftovers(prose, problems, settings=None):
    """把代码修不掉的问题列给模型，让它**在原稿上改**，改完做安全检查。

    用户 2026-08-30 定的第二次调用：不是整话重写，是拿着问题清单去修。
    安全检查三条，任何一条不过就退回原稿——宁可留着问题，也不让它把稿子换掉：
      · 字数不许掉超过一成（重写最典型的症状就是越写越短）
      · 台词一句都不许丢
      · 问题数不许变多
    """
    t = str(prose or "")
    if not t.strip() or not problems:
        return ""
    base = len(problems)
    quotes = _quotes_in(t)
    sysmsg = (
        "你是这篇小说的作者，现在拿着一份问题清单去改自己的稿子。\n"
        "规则：\n"
        "① **在原稿上改**，不是重写。没被点名的地方一个字都不许动；\n"
        "② 每一处引号里的台词**一个字都不许改、不许删**；\n"
        "③ 篇幅不许变短；\n"
        "④ 输出完整的一整话正文，不要任何说明文字、不要标题、不要列改动清单。")
    user = ("【要改的问题】\n" + "\n".join("· " + str(p) for p in problems[:8])
            + "\n\n【原稿】\n" + t)
    try:
        new = strip_method_marks(fix_traditional(trim_tail(
            _q(sysmsg, user, mt=6400, temperature=0.4))))
    except Exception:
        return ""
    if len(re.sub(r"\s", "", new)) < len(re.sub(r"\s", "", t)) * 0.9:
        _dbg("收尾改写被拒", {"原因": "字数掉太多",
                              "前后": "%d -> %d" % (len(t), len(new))})
        return ""
    lost = [q for q in quotes if q not in new]
    if lost:
        _dbg("收尾改写被拒", {"原因": "丢了台词", "丢的": lost[:3]})
        return ""
    if len(prose_problems(new, settings or {})) > base:
        _dbg("收尾改写被拒", {"原因": "问题反而变多"})
        return ""
    return new


# 【正文只许调两次模型】用户 2026-08-31 定死：一次写，一次修，最多两次。
# 所以正文的兜底修复一律走**纯代码**，中途一次模型都不许调——
# 逐句改写、查串戏、查倒带、定向加厚、定向删减，这些原来各自会调模型的，
# 在这个开关下全部降级：能确定性删改的就删改，删改不了的**留着**，
# 攒进问题清单，交给第二次调用一起处理。
_CODE_ONLY = True


def expand_if_short(prose, settings=None, floor=2600, target=3400):
    """篇幅不够时做一次**定向扩充**：拿现有正文去加厚，不重写、不加情节。

    整话重写三轮都没把字数写上去（2026-08-30 实测第一视角 2309 字、
    3D游戏CG 2468 字，反馈里明写了"不要加新情节、把七拍写透"照样没用）——
    每次都从零重写，长度全靠运气。改成把已有的这一版喂回去让它加厚，
    模型只需要往里填内心和细节，比重讲一遍故事容易得多。
    加厚后如果反而更糟（字数没涨，或多出新毛病），就退回原版。
    """
    if _CODE_ONLY:
        return str(prose or "")
    t = str(prose or "")
    n = len(re.sub(r"\s", "", t))
    if n >= floor or not t.strip():
        return t

    def _quotes(x):
        return [re.sub(r"\s", "", q)
                for q in re.findall(r"[「“]([^」”\n]+)[」”]", str(x or ""))]

    old_q = _quotes(t)
    sysmsg = (
        "你是这篇小说的作者，现在做一次加厚。规则：\n"
        "① 情节、场景、人物、结尾，一个都不许改、不许加、不许删；\n"
        "② 原文里每一句带引号的话，**一字不差地保留**，不许改写、不许删、"
        "也不许新加任何一句人物对白；\n"
        "③ 引号只属于人说出口的话——拟声词（吱呀、笃笃这类）一律不许加引号；\n"
        "④ 只在已有的段落里加厚——人物此刻心里在想什么、身体的感觉、"
        "环境里他注意到的细节、动作的分解；\n"
        "⑤ 顺序和原文完全一致，结尾停在原文停的那一瞬；\n"
        "⑥ 输出完整的一整话正文，不要任何说明文字、不要标题、不要分节标记。\n"
        "目标篇幅：%d 字以上。" % target)
    base_probs = len(prose_problems(t, settings or {}))
    best = None
    # 三次：实测同一篇正文，有的轮次一次就成、有的轮次两次都没过护栏，
    # 纯粹是模型输出波动，不该因此让这一话短着交出去（2026-08-30）。
    for _temp in (0.8, 0.7, 0.9):
        try:
            new = trim_tail(_q(sysmsg, t, mt=6400, temperature=_temp))
        except Exception:
            return t
        new = fix_quoted_thoughts(strip_method_marks(fix_traditional(new)))
        if len(re.sub(r"\s", "", new)) <= n * 1.05:
            _dbg("加厚失败", {"原因": "没加厚", "原文字数": n,
                              "产出字数": len(re.sub(r"\s", "", new)), "温度": _temp})
            continue                              # 没加厚，白跑一趟
        # 台词必须一句不丢：正文是剧本层唯一的原话来源，丢一句下游就永远缺一句。
        # 也不许平白多出对白——实测它把「吱呀」「笃笃」加了引号，剧本层
        # 会当成台词去对口型，让人开口说拟声词（2026-08-30）。
        # 原文没有的引号，一律把引号去掉：加厚本来就不许新增对白，
        # 留一句「咔哒」在引号里，剧本层就会安排人开口说这两个字。
        def _unquote_new(m):
            body = re.sub(r"\s", "", m.group(1))
            keep = any(body in o or o in body for o in old_q)
            return m.group(0) if keep else m.group(1)

        new = re.sub(r"[「“]([^」”\n]+)[」”]", _unquote_new, new)
        new_q = _quotes(new)
        # 只护**有内容的台词**（4 字以上）。「走吧。」「嗯。」这种极短的句子
        # 最容易在重写中被并掉，为它整版作废等于让这一话短着交出去——
        # 而正文正是在这一步创建的，下游剧本从最终正文取原话，不存在对不上的契约
        # （2026-08-30 实测：加厚连着失败，每次都只丢一句「走吧。」）。
        _lost = [o for o in old_q
                 if len(o) >= 4 and not any(o in x or x in o for x in new_q)]
        _pb = prose_problems(new, settings or {})
        if len(_pb) > base_probs:
            _dbg("加厚失败", {"原因": "问题变多", "原有": base_probs,
                              "变成": [p[:40] for p in _pb], "温度": _temp})
            continue                              # 加厚把别的写坏了
        if not _lost:
            return new
        # 丢了台词的版本先留着当备选：三次都丢同一句时（实测连丢三次
        # 「到底在等什么？」），退回原版等于让这一话停在 1676 字交出去——
        # 那比丢一句台词严重得多。等三次都试完，没有零损失的版本再用它。
        _dbg("加厚失败", {"原因": "丢了原台词（留作备选）",
                          "丢了": [x[:20] for x in _lost[:3]], "温度": _temp})
        if best is None or len(_lost) < best[0]:
            best = (len(_lost), new)
    if best and best[0] <= 2:
        return best[1]
    return t
    return t


_DBG_PATH = os.environ.get("V41_DIRECTOR_DEBUG") or ""


def _dbg(tag, info):
    """正文层的失败现场落盘。只写文件，不改任何判定。"""
    if not _DBG_PATH:
        return
    try:
        with io.open(_DBG_PATH, "a", encoding="utf-8") as f:
            f.write("\n%s\n[%s] %s  %s\n"
                    % ("=" * 70, time.strftime("%H:%M:%S"), tag,
                       json.dumps(info, ensure_ascii=False)))
    except Exception:
        pass


def echoed_instruction(sysmsg, out):
    """改写结果里是否回抄了指令原文。

    实测（2026-08-30）：让模型把一句运镜改成固定机位，它回了
    「画面里拍到林浅的中近景，林浅在哪，说什么，一个字都不许改，只把镜头的移动去掉。」
    ——指令后半段被当成句子内容抄了进去，而"没有运镜词""长度合理"两道检查都放行。
    句级改写全都要过这一关，否则指令文字会顺着修复灌进正文和视频提示词。
    """
    s, o = str(sysmsg or ""), str(out or "")
    n = 6
    return any(s[i:i + n] in o for i in range(max(0, len(s) - n + 1)))


def _pos_in_quote(text, pos):
    """这个位置是不是落在人物说的话里（引号内）。

    台词一个字都不许改（用户底线）。句级改写把整句丢给模型时，
    句子里如果夹着引号，模型会顺手把台词也改了——实测 STORY_070
    丢了一整句台词（2026-08-30）。命中落在引号里就整处跳过。
    """
    for o, c in (("“", "”"), ("「", "」")):
        a0 = text.rfind(o, 0, pos)
        if a0 < 0:
            continue
        if text.find(c, a0, pos) < 0 and text.find(c, pos) >= 0:
            return True
    return False


def _quotes_in(s):
    return re.findall(r"[“「]([^”」\n]{2,})[”」]", str(s or ""))


def _repair_sentences(text, find_re, sysmsg, forbid_re, rounds=4, fallback=None,
                      clause_first=False):
    """**只重写命中的那一句**，正文其余一个字不动。

    整话重写去修一个词太贵也太险（实测会顺手把别处改坏），删字又会留下残句
    （"资 barely 够"→"资够"）。定位句子边界 → 让模型重写这一句 → 拼回去。
    改出来的还犯规、或长度离谱，就放弃这一处，**跳过去接着修下一处**。

    clause_first=True 时先试**确定性删子句**，删得掉就不调模型。
    比喻绝大多数是纯装饰的独立子句（"…发出细密的爆裂声，像是无数颗冰粒被
    狂风裹挟着"），删掉句子照样通顺，一次模型调用都不用花（2026-08-30）。
    """
    t = str(text or "")
    done = 0          # 已经放弃的命中数：跳过它们继续往后找，别卡在同一处
    for _ in range(rounds):
        # 【引号里的一律跳过】命中落在台词里，改它就是改台词
        hits = [m for m in re.finditer(find_re, t)
                if not _pos_in_quote(t, m.start())]
        if len(hits) <= done:
            break
        hit = hits[done]
        a = max(t.rfind(c, 0, hit.start()) for c in "。！？…\n") + 1
        ends = [i for i in (t.find(c, hit.end()) for c in "。！？…\n") if i >= 0]
        b = (min(ends) + 1) if ends else len(t)
        # 句号常在引号**里面**（"游戏，才刚刚开始。"），切在句号后会把收尾的引号
        # 留在外面，改写完就成了「。""」双引号（2026-08-30 实测）。把结尾的收尾
        # 符号一起吃进来。
        while b < len(t) and t[b] in "」”』）\"'":
            b += 1
        sent = t[a:b]
        _qs = _quotes_in(sent)
        # 【先试白删子句】句子里有两个以上子句、命中的那个子句删掉之后
        # 还剩得下东西——直接删，不花模型调用。
        # 句子里带台词也能删，只要**删完台词一个字不少**：比喻常挂在
        # 说话动作后面（「"动手吧。"苏青说，声音像砂纸磨过墙面」），
        # 一见引号就整句放弃的话，这一类一处都修不到。
        if sent.count("，") >= 1:
            cut = _delete_clause(t, hit.start(), hit.end())
            if cut is not None:
                _new_sent = cut[a:a + max(0, b - a - (len(t) - len(cut)))]
                if (len(re.sub(r"[^\w一-龥]", "", _new_sent)) >= 8
                        and not re.search(find_re, _new_sent)
                        and all(q in _new_sent for q in _qs)):
                    t = cut
                    continue
        if _CODE_ONLY:
            # 删不掉就留着，攒给第二次调用。中途一次模型都不调。
            done += 1
            continue
        try:
            new = _q(sysmsg, sent, mt=260, temperature=0.2).strip().split("\n")[0]
        except Exception:
            break
        # 【台词一个字都不许改】句子里夹着引号时，模型会顺手把台词也改了、
        # 甚至整句吞掉——实测 STORY_070 丢了一整句台词（2026-08-30）。
        # 原句里的每一段引号原文都必须原样出现在改写里，少一个就退回。
        why = ("空" if not new
               else "改了台词" if any(q not in new for q in _qs)
               else "仍犯规" if re.search(forbid_re, new)
               else "回抄指令" if echoed_instruction(sysmsg, new)
               # 上限对短句放宽：把比喻／抽象拆成具体画面本来就会变长，
               # 「剧烈的、撕裂般的疼痛，伴随着机器的轰鸣声。」(21字) 改成
               # 「她紧皱眉头，双手死死按住腹部…机器正发出震耳欲聋的轰鸣。」(43字)
               # 是对的，卡在 2 倍就被判"长度离谱"退回、反而降级成删子句
               # （2026-08-30 实测）。长句仍按 2 倍收口。
               else "长度离谱" if not (len(sent) * 0.4 < len(new)
                                       <= max(len(sent) * 2, len(sent) + 50))
               else "")
        if why:
            _dbg("句级改写被拒", {"原句": sent[:110], "改写": new[:110], "原因": why})
            # 模型改不动就**按子句删**。这些词本来就是规则明令禁止的插入语，
            # 删掉子句、内容照样完整（"可以在接下来的三天内，前往藏剑阁挑法器"
            # → "可以前往藏剑阁挑法器"）；整句只剩这个插入语时（"真正的考验，
            # 才刚刚开始。"）就整句删。实测模型对这两类词连改两次都不动
            # （2026-08-30 仙侠段）。
            # 兜底的删除同样不许碰台词：句子里有引号就整处放弃，
            # 跳过它接着修下一处（不放弃就会卡在同一处把 rounds 耗光）。
            cut = (None if _qs
                   else _delete_sentence(t, a, b) if fallback == "sentence"
                   else _delete_clause(t, hit.start(), hit.end())
                   if fallback == "clause" else None)
            if cut is not None and not re.search(find_re, cut[max(0, a - 4):b]):
                t = cut
                continue
            # 【跳过，不终止】原来这里 break，整轮修复就停在第一处改不动的地方——
            # 实测 30 处比喻只修掉 19 处就收工了（2026-08-30）。
            done += 1
            continue
        t = t[:a] + new + t[b:]
    return t


def _delete_sentence(t, a, b):
    """整句删。套路句是整句旁白插入（"沈清秋知道，真正的考验，才刚刚开始。"），
    只删子句会剩下残句「沈清秋知道，真正的考验。」——这类整句拿掉才通顺。
    删完顺手收掉留下的空段。"""
    if len(re.sub(r"\s", "", t[a:b])) > 60:       # 太长，删了会丢内容
        return None
    out = t[:a] + t[b:]
    return re.sub(r"\n{3,}", "\n\n", out)


def _delete_clause(t, hs, he):
    """删掉命中所在的那个子句；子句就是整句时整句删。删不安全返回 None。"""
    seps = "，,。！？；;\n"
    ca = max(t.rfind(c, 0, hs) for c in seps) + 1
    ends = [i for i in (t.find(c, he) for c in seps) if i >= 0]
    ce = min(ends) if ends else len(t)
    head, mid, tail = t[:ca], t[ca:ce], t[ce:]
    if len(re.sub(r"\s", "", mid)) > 40:          # 子句太长，删了会丢内容
        return None
    # 【句子的第一个子句一律不删】主语通常就在第一个子句里，删了剩下的没主语：
    # 「霓虹灯管像垂死的昆虫，在酒吧浑浊的空气里嗡嗡作响。」删掉前半句就成了
    # 「在酒吧浑浊的空气里嗡嗡作响。」——谁在响？实测 STORY_072 开篇第一句
    # 就这么残了，另外两处是「另一半则是…」「打开了苏青心防的一角。」
    # （2026-08-30 自查）。这类残句比原来的比喻严重得多。
    if ca == 0 or t[ca - 1] in "。！？\n":
        return None
    # 【删完不许剩下半句话】两种残句都是实测出来的（2026-08-30 STORY_075）：
    #   ① 「那人对着他说了三个字，声音沙哑，」——句子以逗号收尾，后半截没了
    #      （被删的子句在段末，tail 是换行不是句号，原来那条分支接不住）
    #   ② 「声音从迷雾深处传来，这次。」——剩下的末子句是个光秃秃的状语
    _tailword = ("这次", "那次", "然后", "接着", "于是", "但是", "可是", "而且",
                 "只是", "忽然", "突然", "随即", "此刻", "这时", "那时", "同时",
                 "接下来", "紧接着")
    _last = re.split(r"[，,]", head.rstrip("，,"))[-1] if head.strip() else ""
    _last = re.split(r"[。！？\n]", _last)[-1].strip()
    if _last in _tailword or (0 < len(_last) <= 2 and _last in "".join(_tailword)):
        return None
    if tail[:1] in "。！？":                       # 子句是整句的收尾 → 连前面的逗号一起收
        if head.endswith(("，", ",")):
            return head[:-1] + tail
        return head + tail[1:] if head.endswith(("。", "！", "？", "\n")) else head + tail
    if tail[:1] in "，,":
        return head + tail[1:]
    # tail 是换行或到底了：head 若以逗号收尾，句子就断在半截，补成句号
    if head.endswith(("，", ",")):
        return head[:-1] + "。" + tail
    return head + tail


_EN_LEAK_RE = r"[一-龥][ ,，]{0,2}[a-z]{4,}[ ,，]{0,2}[一-龥]"

# 心声被写成引号对白：①引号在前、心想在后 ②心想在前、引号在后。
# 引号和"心想"之间**不许跨句号，也不许夹说话动词**——原来只限 8 个字，
# 「"我走了。"他说。她想到很多年前的雪。」这种真台词被判成心声（2026-08-30 实测）。
# 检测器误报还只是烦人，机械修复照着误报动手就会把真台词的引号扒掉，
# 剧本层再也对不上这句原话，所以两边必须用同一套边界。
_GAP = r"(?:[^\n。！？，,]|，(?!\s*[^\n]{0,4}(?:说|道|问|答|喊)))"
_THINK = r"(?:在心里|心里|默念|心想|暗想|腹诽|想到)"
_HEART_QUOTE = (r"[「“][^」”\n]{2,}[」”]" + _GAP + r"{0,8}?" + _THINK +
                r"|(?:在心里|心里|默念|心想|暗想)" + _GAP + r"{0,6}[：:]?\s*[「“]")
_HEART_A = re.compile(r"[「“]([^」”\n]{2,})[」”](?=" + _GAP + r"{0,8}?" + _THINK + r")")
_HEART_B = re.compile(r"((?:在心里|心里|默念|心想|暗想)" + _GAP + r"{0,6}?)"
                      r"[：:]?\s*[「“]([^」”\n]{2,})[」”]")


def fix_unfilmable(text):
    """把拍不到的写法**改掉**，而不是打回让模型重写整话。

    用户 2026-08-30 定的原则：不限制 Qwen 发挥，出了错代码兜底直接修。
    四类分别处理：
      · 比喻／回忆／抽象状态 → **只重写那一句**（句级改写，正文其余一字不动）
      · 整段权衡推演 → 整段删（"代价是巨大的。如果…但如果…"不含情节，
        删掉前后自然衔接；它的作用是交代利害，而利害后面人物对话里都说了）
      · 最后再查一次结尾有没有把时间线倒回去
    用的正则就是 _UNFILMABLE 里那四条——检测器和修复器共用一份，
    两套口径的死循环踩过三次了。
    """
    t = str(text or "")
    # 【不删，只换】用户 2026-08-31 定：比喻／回忆／推演／抽象一律当成"问题"，
    # 由 replace_bad_sentences 一次性拿去换句，正文一个字都不删。
    # 删子句这条路今天出过三次残句（开头主语被删、句子以逗号收尾、
    # 剩下光秃秃的「这次。」），弃用。
    if _CODE_ONLY:
        return fix_rewound_tail(fix_repeated_action(t))
    # ① 比喻：直接写那个东西本身
    #    实测 STORY_070 一篇 3959 字正文里 25 处以上，绝大多数是「像是…」
    #    「仿佛…」这种不带"一样／似的"的写法，老正则一个都没抓到。
    pat_bi = _PAT_BIYU
    t = _repair_sentences(
        t, pat_bi,
        "你是中文小说的文字编辑。这篇正文是拿去拍片子的，只能写摄影机拍得到的东西。"
        "用户给的这一句里有比喻，把比喻换成**那个东西本身的样子**"
        "（「排风扇发出垂死般的轰鸣」→「排风扇扇叶卡着响，转一圈顿一下」；"
        "「她脸色苍白如纸」→「她脸上没有血色，嘴唇发青」）。"
        "句子里的人、动作、信息一个都不许少。只回改好的这一句，不要解释。",
        pat_bi, rounds=34, fallback="clause", clause_first=True)
    # ② 回忆：交代过去改成看得见的（物证／人物说出来），实在不行整句删
    pat_hy = _PAT_HUIYI
    t = _repair_sentences(
        t, pat_hy,
        "你是中文小说的文字编辑。这篇正文是拿去拍片子的，回忆摄影机拍不到。"
        "把用户给的这一句改成**当下看得见的东西**：让他做一个动作、或者摸出一件"
        "跟那段过去有关的物件（「他想起三年前那道划痕」→「他摸出一只破手套，"
        "指头上有道口子」）。不许再出现想起、记得、脑海里。"
        "只回改好的这一句，不要解释。",
        pat_hy, rounds=8, fallback="sentence")
    # ③ 抽象／情绪状态：没有"心里""内心"这些词，但摄影机同样拍不到。
    #    实测 STORY_070：「他浑然不觉」「眼神复杂，有惊讶，有怜悯」
    #    「心跳不由自主地加速」「历经沧桑后的淡然」「真实得让人安心」。
    pat_ab = _PAT_CHOUXIANG
    t = _repair_sentences(
        t, pat_ab,
        "你是中文小说的文字编辑。这篇正文是拿去拍片子的，人心里的状态摄影机拍不到。"
        "把用户给的这一句改成**演员演得出来、镜头拍得到的样子**：写他的手、"
        "他的呼吸、他的身体朝向、他脸上具体哪块肌肉在动"
        "（「他眼神复杂，有惊讶也有怜悯」→「他张了张嘴，没出声，手停在半空」；"
        "「他浑然不觉」→「他一动没动」）。"
        "句子里的人、动作、信息一个都不许少。只回改好的这一句，不要解释。",
        pat_ab, rounds=16, fallback="clause", clause_first=True)
    # ④ 权衡推演：整段是推演的整段删；夹在正常段里的按句删
    paras = [p for p in re.split(r"\n\s*\n", t) if p.strip()]
    keep = [p for p in paras
            if not re.search(r"(代价是|权衡|盘算)", p)
            or len(re.sub(r"\s", "", p)) > 160]
    if len(keep) < len(paras):
        t = "\n\n".join(keep)
    t = _repair_sentences(
        t, _PAT_QUANHENG,
        "你是中文小说的文字编辑。这篇正文是拿去拍片子的，人在脑子里推演"
        "「如果…会怎样」摄影机拍不到。把用户给的这一句改成**他此刻的动作**"
        "（「如果他拿走芯片，她会不会变成白痴？」→「他的手停在半空，"
        "又收回来半寸」）。不许再出现如果、会不会、或者。"
        "只回改好的这一句，不要解释。",
        _PAT_QUANHENG, rounds=8, fallback="sentence", clause_first=True)
    return fix_rewound_tail(fix_repeated_action(fix_orphan_sentence(t)))


def fix_repeated_action(text):
    """相邻两段把同一个动作写了两遍——把后一句删掉。

    实测 STORY_071：「他的指尖在芯片边缘停住，指腹摩挲过那道粗糙的划痕」
    下一段又写「他拇指指腹摩挲过那道划痕，抬头看向……」——下游会按顺序
    拍两遍同一个动作，成片就是卡带（2026-08-30）。
    判据是确定性的：后一段的**第一句**和紧挨着的上一段有 7 个字以上的
    连续重合，而且这一句里没有台词 → 删掉这一句。
    """
    t = str(text or "")
    paras = [p for p in re.split(r"\n\s*\n", t) if p.strip()]
    if len(paras) < 2:
        return t
    out = [paras[0]]
    for p in paras[1:]:
        sents = re.split(r"(?<=[。！？])", p)
        first = sents[0] if sents else ""
        prev = re.sub(r"[^\w一-龥]", "", out[-1])
        f = re.sub(r"[^\w一-龥]", "", first)
        dup = any(f[i:i + 7] in prev for i in range(max(0, len(f) - 6)))
        if dup and len(f) >= 7 and not _quotes_in(first):
            # 【只删重复的那个子句】整句删会把同一句里的新动作一起带走
            # （「他拇指指腹摩挲过那道划痕，抬头看向林浅」——后半句是新的）。
            cls = [c for c in re.split(r"(?<=[，,])", first) if c.strip()]
            keep_c = []
            for c in cls:
                nc = re.sub(r"[^\w一-龥]", "", c)
                if (len(nc) >= 5
                        and any(nc[i:i + 5] in prev
                                for i in range(max(1, len(nc) - 4)))):
                    continue
                keep_c.append(c)
            new_first = "".join(keep_c).lstrip("，,")
            rest = "".join(sents[1:]).strip()
            _dbg("重复动作删子句", {"原句": first[:60], "留下": new_first[:60],
                                    "上一段": out[-1][-40:]})
            merged = (new_first.strip() + rest).strip()
            if merged:
                out.append(merged)
            continue
        out.append(p)
    return "\n\n".join(out)


def fix_orphan_sentence(text):
    """整段里冒出一句跟前后文没关系的串戏句——删掉。

    实测 STORY_071：阿K 在盘算要不要拿芯片，中间插进
    「她盯着镜中那双涣散的瞳孔，指尖颤抖着抚过脸颊上那道新添的淤青。」
    ——镜子和淤青全文再没出现过，是模型串了另一场戏。
    这种句子没有字面特征可抓（不重复、不含禁用词），只能问一句。
    一话一次调用，答出来的句子必须**原文一字不差**出现在正文里、
    而且不含台词，才删；模型答别的一律不动。
    """
    if _CODE_ONLY:
        return str(text or "")
    t = str(text or "")
    if len(t) < 400:
        return t
    try:
        ans = _q("你是剧本统筹。下面是一篇正文。找出**唯一一句**明显串戏的句子："
                 "它写的场景、物件或人物动作，前后文里完全没有出现过、也接不上，"
                 "像是从另一场戏里掉进来的。"
                 "只回那一句的原文，一个字都不许改。没有这样的句子就只回「无」。",
                 t[:6000], mt=120, temperature=0.0).strip().split("\n")[0]
    except Exception:
        return t
    ans = ans.strip("“”「」 　")
    if not ans or ans.startswith("无") or len(ans) < 10 or ans not in t:
        return t
    if _quotes_in(ans):
        return t
    _dbg("串戏句删掉", {"句子": ans[:90]})
    return t.replace(ans, "", 1)


def fix_rewound_tail(text):
    """结尾把时间线倒回去了——整段删。

    实测 STORY_070：探针早就拔出来、数据都上传完了，最后一段又写回
    「指尖那枚探针刚触碰到她后颈的接口……她突然睁开眼，瞳孔里倒映出他那张
    陌生的脸」——同一个动作在正文里演了两遍，中间隔着整场戏。
    根因在 write_prose 的【结尾定点】：先让模型定"收笔在哪一瞬"，模型在故事
    中途就演到了那一瞬，继续把后面写完，最后又把那句收笔词原样贴回末尾。
    留着它，下游按顺序再拍一遍，成片跳回去，观众看不懂（故事稳定性第一）。

    判据只能是语义的：这一段跟前文的字面重合率只有 0.06，跟正常结尾的 0.07
    分不开（2026-08-30 六个项目实测），所以直接问模型一句是非。
    一话一次调用，比重写整话便宜得多。
    """
    if _CODE_ONLY:
        return str(text or "")
    t = str(text or "")
    paras = [p for p in re.split(r"\n\s*\n", t) if p.strip()]
    if len(paras) < 4:
        return t
    last = paras[-1].strip()
    if not (12 <= len(re.sub(r"[^\w一-龥]", "", last)) <= 240):
        return t
    body = "\n\n".join(paras[:-1])
    try:
        ans = _q("你是剧本统筹。判断一件事：正文的最后一段所写的那个瞬间，"
                 "在它前面的正文里**是不是已经发生过了**。"
                 "注意区分——把前面演过的动作又倒回去写一遍（时间线退回去），"
                 "回答「是」；停在一个前面没演过的新动作或新的一句话上，"
                 "回答「否」。只回一个字：是 或 否。",
                 "【前面的正文】\n%s\n\n【最后一段】\n%s"
                 % (body[-2400:], last), mt=8, temperature=0.0).strip()
    except Exception:
        return t
    if ans.startswith("是"):
        _dbg("结尾倒带整段删", {"末段": last[:100]})
        return body
    return t


def fix_typos(text):
    """错别字直接改掉。台词里的错字会被念出来（实测「客官打烚了」），
    而这是确定性的事，不值得占一轮重写。"""
    t = str(text or "")
    for w, r in _TYPOS.items():
        if r:
            t = t.replace(w, r)
    return t


def fix_quoted_proper_nouns(text):
    """店名／地名／组织名用了引号——换成书名号《》。

    引号在正文里只该给"说出口的话"，专名用引号会被下游当成台词：实测成片里
    主角站在店门口，对着空气念了一句"旧时代咖啡"（2026-08-30 用户实测）。
    只认**明确是专名**的写法：前面有"名为/叫做/名叫"，或后面紧跟"区/店/公司/
    组织/集团/酒吧/咖啡"这类通名——避免误伤真台词。
    """
    t = str(text or "")
    q = r"[“”「」]"
    # ① 名为“X” / 叫做“X” / 名叫“X”
    t = re.sub(r"(名为|叫做|名叫|唤作|代号)%s([^“”「」\n]{1,14})%s" % (q, q),
               r"\1《\2》", t)
    # ② “X”区 / “X”店 / “X”号 …（引号后紧跟通名）
    # **引号里带句末标点的是台词，不是专名**：「"别去。"吧台后的女人说」里的
    # "吧"属于"吧台"，把台词判成了专名（2026-08-30 自查发现）。
    # 专名不会自带句号问号感叹号，也不会长到像一句话。
    t = re.sub(r"%s([^“”「」\n。！？…，；]{1,10})%s(?=(区|城|镇|村|街|号|舰|"
               r"公司|集团|组织|门派|计划|行动|项目|事件|系统))" % (q, q),
               r"《\1》", t)
    return t


def fix_quoted_thoughts(text):
    """心里想的话被加了引号——把那对引号去掉。

    这一处必须机械修：引号内容会被剧本层当成真台词拿去对口型，
    等于让演员把心里话说出口。去掉引号后句子照样通顺（"他不会来了，"她在心里说
    → 他不会来了，她在心里说），所以不必问模型，也不会改动一个字的内容。
    冒号顺手换成逗号，否则"她心想：他不会来了"读着像旁白。
    """
    t = str(text or "")
    t = _HEART_A.sub(lambda m: m.group(1), t)
    t = _HEART_B.sub(lambda m: m.group(1).rstrip("：:") + "，" + m.group(2), t)
    return t


def fix_english_words(text):
    """中文正文里混进英文单词——只重写夹着它的那一句。"""
    return _repair_sentences(
        text, _EN_LEAK_RE,
        "你是中文小说的文字校对。只做一件事：把用户给的这一句里的英文单词换成"
        "意思相同的中文，其余一个字都不许改。只回改好的这一句，不要解释。",
        r"[a-z]{4,}")


def fix_short_para_run(text, whole=None):
    """一行一句的短段——合并回完整段落。合并是确定性操作，一个字都不改。

    两种模式：
      · 局部（默认）：只清理结尾区连续 4 段以上的短句排比。
      · 全篇：短段占比过半时启用，从头到尾把连着的短段并成 80~180 字一段。
        实测一句一行能塌满全篇（181 段、中位 10 字、147 段不到 20 字），
        只清理结尾等于没清（2026-09-01）。
    对白段和最后一段不动：对白天生短，最后一段是收笔的钩子。
    """
    t = str(text or "")
    paras = [p for p in re.split(r"\n\s*\n", t) if p.strip()]
    if len(paras) < 5:
        return t

    def short(p):
        return len(p.strip()) < 20 and not re.search(r"[「」“”]", p)

    if whole is None:
        whole = sum(1 for p in paras if short(p)) >= len(paras) * 0.5
    if whole:
        # 全篇重排：连着的短段并到 80~180 字；带引号的台词段单独成段
        out, buf = [], ""
        for i, p in enumerate(paras):
            p = p.strip()
            last = (i == len(paras) - 1)
            # 只有**整段就是一句台词**才单独成段（「…」占了大半）；
            # 叙述里顺带引用一句的照常并进段落——否则台词多的篇目
            # 会被切成一堆 19 字的碎段（2026-09-01 实测中位只到 19）。
            q = sum(len(x) for x in re.findall(r"[「“][^」”]*[」”]", p))
            if (q and q >= len(p) * 0.6) or last:
                if buf:
                    out.append(buf)
                    buf = ""
                out.append(p)
                continue
            if not buf:
                buf = p
            elif len(buf) + len(p) <= 180:
                buf += p
            else:
                out.append(buf)
                buf = p
        if buf:
            out.append(buf)
        return "\n\n".join(out)

    # 局部：只动结尾 450 字
    head_len, start = 0, 0
    for i, p in enumerate(paras):
        if len(t) - head_len <= 450:
            start = i
            break
        head_len += len(p) + 2
        start = i
    out, k = paras[:start], 0
    tail = paras[start:]
    while k < len(tail):
        j = k
        while j < len(tail) - 1 and short(tail[j]):   # 末段是钩子，留着
            j += 1
        if j - k >= 4:
            out.append("".join(x.strip() for x in tail[k:j]))
            k = j
            continue
        out.append(tail[k])
        k += 1
    return "\n\n".join(out)


def fix_truncated_tail(text):
    """结尾是被 token 上限砍断的残句——退回到最后一个完整句子。

    写到上限被砍出「。里面光线昏暗，只有几缕阳光」这种半句（2026-08-30 实测）。
    退回是确定性操作，不问模型。退太多就不动——那说明整段结尾都不完整，
    交给校验器照常报出来。
    """
    t = str(text or "").rstrip()
    if not t or t[-1] in "。！？…」”）』—":
        return t
    cut = max(t.rfind(c) for c in "。！？…」”）』")
    if cut < 0 or len(t) - cut > 160:
        return t
    return t[:cut + 1]


# 结尾抒情散文的动作动词表：一段里连一个都没有，就是没有人在做事的空段。
# 只收**明确是人在做**的动作。「翻」「滚」「倒」「停」这种既能形容景物又能当
# 动作的单字一律不收——「云海翻涌」里的「翻」被当成人的动作，抒情段就删不掉了
# （2026-08-30 自查仙侠段）。
_ACT_VERB = (r"(走|站|坐下|坐在|跪|伸手|伸出|握|抬起|抬手|抬头|低下头|转身|转过|"
             r"推开|推门|拉住|递|摸|抓住|迈|挥|刺|砍|踩|捏|扯|按住|睁开|闭上|"
             r"咬|深吸|吐出|举起|拔|插|扔|接过|拍|敲|退了|抖|颤|喘|笑|哭|喊|"
             r"说|问|答|点头|摇头|皱眉|甩|挡|扑|冲向|跳|爬|蹲|躲|抱|拽|掀|"
             r"舀|端|放下|翻身|翻滚|翻开)")


def fix_essay_tail(text):
    """结尾滑成抒情散文——整段整段删掉，退回最后一个有人在做事的段落。

    实测 STORY_075（仙侠）：故事讲完之后又写了一千五百字
    「风，在吹。吹过云海，吹过山峦……它，是无形的，却又是无处不在的。
    它，是自由的，却又是受控的。」——一个画面都没有，下游没法拍，
    观众也没法看（2026-08-30 用户实测）。
    指令词里写着「最后一句必须是故事里的人正在做的动作或正在说的话」，
    这里就按这条兜底：从末尾往回走，凡是**既没有台词、也没有任何动作动词**
    的段落一律删掉，删到第一个有人在做事的段落为止。
    收口：至少留 4 段，最多删掉全文的四成——防止误伤真结尾。
    """
    t = str(text or "")
    paras = [p for p in re.split(r"\n\s*\n", t) if p.strip()]
    if len(paras) < 6:
        return t
    def _essay(p):
        """这一段是不是"没人在做事"的抒情空段。

        三个条件同时成立才算：没有台词、没有任何动作动词，而且
        **要么整段没有人**（「风，在吹。」「它，是无形的」），
        **要么句子被逗号剁成了抒情腔**（平均每个小句不到 7 个字：
        「林萧，在风中，找到了自己。」「山脉之中，隐藏着无数的秘密。
        等待着，有缘人来揭开。」）。
        只用前两条会误伤真结尾——「门在他身后合上，雨声一下子涨了起来。」
        没有动作动词，却是个正经的气氛收尾（2026-08-30 自查）。
        """
        s = p.strip()
        # 【只删又短又空的】散文诗注水的特征是**一行一句**：段落短。
        # 只看"有没有动作动词"会误伤正常结尾——实测把
        # 「指尖，即将触碰到……灯芯。」这个完整结尾删成了残句
        # （2026-08-31 自查）。超过 40 字的段落一律不动。
        if len(re.sub(r"[^\w一-龥]", "", s)) > 40:
            return False
        if _quotes_in(p) or re.search(_ACT_VERB, p):
            return False
        # 整段没有人称 → 空段。原来这里还认"逗号前的 2~4 个字算人名"，
        # 那条太松，「沙沙声，」都被当成了人名（2026-08-30 自查）。
        # 真有人名的段落基本都带动作动词，上面那道已经拦住了。
        if not re.search(r"[他她]", s):
            return True
        cl = [c for c in re.split(r"[，,。！？；;]", s) if c.strip()]
        return bool(cl) and (sum(len(c) for c in cl) / float(len(cl))) < 7

    keep = len(paras)
    while keep > 4 and _essay(paras[keep - 1]):
        keep -= 1
    # 【删完必须停在完整句子上】剩下的末段如果收在冒号、逗号、破折号上，
    # 那是把话说了一半——继续往回删，删到一句完整的话为止。
    # 实测删到「像是在说：」就收手，比原来的结尾还糟（2026-08-31 自查）。
    while keep > 4 and paras[keep - 1].rstrip()[-1:] not in "。！？…”」":
        keep -= 1
    if keep == len(paras):
        return t
    cut = "\n\n".join(paras[:keep])
    if len(cut) < len(t) * 0.6:          # 删太多，多半是判错了，整处放弃
        return t
    _dbg("结尾抒情段整段删", {"删掉 %d 段" % (len(paras) - keep):
                              [x[:40] for x in paras[keep:keep + 3]],
                              "字数": "%d -> %d" % (len(t), len(cut))})
    return cut


def condense_if_long(prose, settings=None, ceiling=5200, target=4200):
    """篇幅超了做一次**定向压缩**：砍支线和重复心理描写，主线一条不动。

    和加厚对称：整话重写三轮压不下来（实测第一视角写到 9601 字、还把结尾写没了），
    因为每次都从零重写、长度全凭运气。把已有这一版喂回去删减，比重讲一遍容易。
    压完更糟（没短下来、或多出新毛病）就退回原版。
    """
    if _CODE_ONLY:
        return str(prose or "")
    t = str(prose or "")
    n = len(re.sub(r"\s", "", t))
    if n <= ceiling or not t.strip():
        return t
    base = len(prose_problems(t, settings or {}))
    sysmsg = (
        "你是这篇小说的作者，现在做一次删减。规则：\n"
        "① 主线事件、人物、结尾停的那一瞬，一个都不许改；\n"
        "② 删掉支线、重复的心理描写、同一件事的第二次铺陈；\n"
        "③ 这一话只讲**一个连续的时间段**，跨天跨段的部分整块删掉，"
        "不要写「第二天」「几天后」这类跳跃；\n"
        "④ 顺序和原文一致，最后一句必须是完整的句子；\n"
        "⑤ 输出完整的一整话正文，不要任何说明文字、不要标题。\n"
        "目标篇幅：%d 字左右。" % target)
    for _ in range(2):
        try:
            new = _q(sysmsg, t, mt=6400, temperature=0.7)
        except Exception:
            return t
        new = fix_truncated_tail(strip_method_marks(fix_traditional(trim_tail(new))))
        n2 = len(re.sub(r"\s", "", new))
        if not (3000 <= n2 < n * 0.95):
            continue
        if len(prose_problems(new, settings or {})) > base:
            continue
        return new
    return t


def fix_anachronisms(text, settings=None):
    """穿帮词（西方魔幻里的"倒计时"这类）——只重写夹着它的那一句。

    穿帮词按世界类型判定，词表是动态的，所以现算命中再拼正则。
    和套路句同理：整话重写三轮都换不掉一个词，句级改写把范围锁死。
    """
    t = str(text or "")
    for _ in range(3):
        bad = _anachronisms(t, settings)
        if not bad:
            break
        pat = "|".join(re.escape(w) for w in bad)
        new = _repair_sentences(
            t, pat,
            "你是中文小说的文字编辑。用户给的这一句里出现了这个故事的世界里"
            "不该存在的东西：%s。把它换成这个世界里真实存在的说法，"
            "句子的意思、人物、动作一个都不许变，也不许添新内容。"
            "只回改好的这一句，不要解释。" % "、".join(bad[:3]),
            pat, rounds=3)
        if new == t:
            break
        t = new
    return t


def fix_tail_god_view(text, window=450):
    """结尾区的上帝视角剧透句——整句删。

    和套路句同性质：旁白跳出人物视角替读者预告后事（"上面写着他的名字，
    也写着他未知的命运？"）。整话重写改不掉，而这类句子不含情节，删掉前后自然衔接。
    **只在结尾区动刀**——「命运」这类词在正文中间是正常用词，全篇删会误伤
    （2026-08-30）。
    """
    t = str(text or "")

    def _in_quote(text, pos):
        """这个位置是不是落在人物说的话里。

        【引号里的不动】命中落在台词里时，删掉就是删台词——实测
        「她轻声说：“这就是命运吧。”」整句被删、台词没了（2026-08-30 自查）。
        上帝视角指的是**旁白**跳出人物视角替读者预告后事；
        人物自己感叹一句命运，是正常台词。
        """
        for o, c in (("“", "”"), ("「", "」")):
            a0 = text.rfind(o, 0, pos)
            if a0 < 0:
                continue
            if text.find(c, a0, pos) < 0 and text.find(c, pos) >= 0:
                return True
        return False

    for _ in range(3):
        tail_from = max(0, len(t) - window)
        hit = None
        for w in _GOD_VIEW:
            for m in re.finditer(w, t[tail_from:]):
                if _in_quote(t, tail_from + m.start()):
                    continue                      # 这一处在台词里，跳过
                if hit is None or m.start() < hit.start():
                    hit = m
                break
        if not hit:
            break
        hs = tail_from + hit.start()
        he = tail_from + hit.end()
        a = max(t.rfind(c, 0, hs) for c in "。！？…\n") + 1
        ends = [i for i in (t.find(c, he) for c in "。！？…\n") if i >= 0]
        b = (min(ends) + 1) if ends else len(t)
        while b < len(t) and t[b] in "」”』）*":
            b += 1
        if len(re.sub(r"\s", "", t[a:b])) <= 60:
            new = t[:a] + t[b:]                   # 短句：整句删
        else:
            # 长句带内容，整句删会丢东西——只删那个子句（"…上面写着他的名字，
            # **也写着他未知的命运**？"），前半句照留（2026-08-30 实测）。
            new = _delete_clause(t, hs, he)
            if new is None:
                break
        if new == t:
            break
        t = re.sub(r"\n{3,}", "\n\n", new)
    return t


def fix_tail_questions(text):
    """结尾连着发问——把这些问句段改写成陈述。

    规则是"悬念靠没演完的动作留，不靠问读者"。整话重写三轮改不掉这种收尾腔，
    句级改写把范围锁死：只动结尾区里**不带引号**的问句段（带引号的是人物真的
    在问，不能动），最后一段留着当钩子。
    """
    # 口径必须和校验器**完全一致**：校验器按 \n 切最后 450 字，我原来按空行
    # 切最后 8 段，于是出现"校验说 2 个、修复只看到 1 个"，怎么修都过不了
    # （2026-08-30 实测公路片段）。这里照抄校验器的切法。
    t = str(text or "")
    paras = t.split("\n")
    if len(paras) < 3:
        return t
    tail_from, seen = 0, 0
    for i in range(len(paras) - 1, -1, -1):
        seen += len(paras[i]) + 1
        if seen >= 450:
            tail_from = i
            break
    ask_idx = [i for i in range(tail_from, len(paras))
               if paras[i].strip().endswith("？")
               and not re.search(r"[「」“”]", paras[i])]
    if len(ask_idx) < 2:
        return t
    sysmsg = ("你是中文小说的文字编辑。用户给的这一段是小说结尾处的一个问句段，"
              "它在向读者提问。把它改写成陈述句：写人物此刻看到的、做的、"
              "身体感觉到的，不许再出现问号，不许添新情节，不许写预告和感悟。"
              "只回改好的这一段，不要解释。")
    out = list(paras)
    for i in ask_idx:
        try:
            new = _q(sysmsg, paras[i], mt=200, temperature=0.3).strip().split("\n")[0]
        except Exception:
            break
        if (not new or "？" in new or echoed_instruction(sysmsg, new)
                or not (len(paras[i]) * 0.4 < len(new) < len(paras[i]) * 2.2)):
            continue
        out[i] = new
    return "\n".join(out)          # 按 \n 切的就要按 \n 拼回去，否则空行翻倍


def fix_time_jumps(text):
    """一笔带过整段时间的写法（"第二天清晨""转眼"）——只重写那一句。

    这一话只讲一个连续的时间段。压缩那一轮已经点名要求删掉跨天，模型仍会留一句
    （2026-08-30 实测），说明它当成了转场惯用语——句级改写把范围锁死。
    """
    pat = "|".join(re.escape(c) for c in _JUMP_WORDS) + r"|接下来的[一二三四五六七八九十\d]+天"
    return _repair_sentences(
        text, pat,
        "你是中文小说的文字编辑。用户给的这一句用了一笔带过整段时间的写法"
        "（例如「第二天清晨」「转眼」「几天后」）。这一话只讲一个连续的时间段，"
        "把这一句改写成紧接着上一刻发生的事，句子里的人、动作、信息一个都不许少，"
        "也不许添新情节。只回改好的这一句，不要解释。",
        pat, fallback="clause")


def fix_cliches(text):
    """套路句（"才刚刚开始""她不知道的是"这类预告腔）——只重写那一句。

    整话重写实测三轮都删不干净：模型删掉一句又在别处写出同族的另一句。
    句级改写把范围锁死，改完还犯规就不采纳。
    """
    pat = "|".join(re.escape(c) for c in _CLICHE)
    return _repair_sentences(
        text, pat,
        "你是中文小说的文字编辑。用户给的这一句里有一处上帝视角的预告腔"
        "（例如「她不知道的是」「这一切才刚刚开始」「命运的齿轮」）。"
        "把这一句改写成只写当下、不预告后事、不跳出人物视角的写法；"
        "句子里的人、动作、信息一个都不许少，也不许添新情节。"
        "只回改好的这一句，不要解释。",
        pat, fallback="sentence")


def trim_tail(prose):
    """剪掉结尾拖在钩子后面的那截尾巴（上帝视角剧透 / 套路收束）。

    定点之后仍偶有残留，代码兜底：找到**最后一处**命中的剧透句，从它开始整段砍掉，
    再顺手砍掉砍完后遗留在末尾的单句叙述。只在尾部动刀（命中点必须在后 30% 之内），
    最多砍掉全文 15%，砍不安全就原样返回。
    """
    t = str(prose or "").rstrip()
    paras = t.split("\n")
    cut = -1
    for i, p in enumerate(paras):
        s = p.strip()
        if not s:
            continue
        if any(re.search(w, s) for w in _GOD_VIEW) or any(w in s for w in _CLICHE):
            cut = i
    if cut < 0:
        return t
    head = "\n".join(paras[:cut]).rstrip()
    if len(head) < len(t) * 0.85 or not head:
        return t                      # 命中点太靠前：不是尾巴问题，别乱剪
    kept = head.split("\n")
    while kept:                       # 砍完后末尾若剩下单句叙述，一并收干净
        last = kept[-1].strip()
        if last and len(last) < 20 and not re.search(r"[「」“”]", last):
            kept.pop()
            continue
        break
    out = "\n".join(kept).rstrip()
    return out if len(out) >= len(t) * 0.85 else head


# ── 一致性校验用的词表（2026-08-29：STORY_065 正文实测查到的四类错）──
# 单层建筑 vs 多层结构：正文写「破败的小屋、屋顶塌了一半」，结尾却是
# 「门外的走廊上」「指向楼梯的方向」——平房里冒出走廊和楼梯。
_FLAT_BUILDING = ("小屋", "平房", "茅屋", "木屋", "帐篷", "棚子")
_MULTI_FLOOR = ("走廊", "楼梯", "二楼", "三楼", "楼上", "楼下", "电梯", "阁楼")
# 「楼上／楼下」会误配进别的建筑名：「钟**楼上**的指针刚指向午夜」被当成
# "木屋里冒出了楼上"，判成建筑前后矛盾（2026-08-30 实测边境村庄段）。
# 这些字打头的是独立建筑，不是楼层。
_NOT_FLOOR_PREFIX = "钟塔鼓角城岗炮哨阁"
# 光照：写了遮蔽又写阳光下
_NO_SUN = ("阳光几乎被完全遮蔽", "阳光被遮蔽", "不见天日", "阴云密布",
           "乌云蔽日", "天光全无")
_HAS_SUN = ("阳光下", "阳光照", "日光下", "阳光洒", "阳光穿过")
# 繁体字（常见混入的那几个，全表没必要）
_TRAD = "臉們個來這對時實現學經萬與說話東車馬鳥魚點無"
# 繁体字不值得占一轮重试——直接转简。表按 _TRAD 一一对应。
_TRAD2SIMP = str.maketrans("臉們個來這對時實現學經萬與說話東車馬鳥魚點無",
                           "脸们个来这对时实现学经万与说话东车马鸟鱼点无")


def fix_traditional(text):
    """把混入的繁体字换成简体。纯机械，不改别的。"""
    return str(text or "").translate(_TRAD2SIMP)


# 写作方法的标记。校验器按这一套报（宽），机械删按下面那套动手（窄）。
_METHOD_MARK = (r"第[一二三四五六七1-7]拍[：:]|【节拍[^】]*】|节拍结束|开场状态[：:]|"
                r"黄金三角|阻力源[：:]")
# 能安全从句子里抠掉的，只有**明确是标签**的形态：带冒号、或整个被方括号包住。
# "黄金三角"这种裸词不在内——"他站在黄金三角形的祭坛前"是正经句子，
# 抠掉就成了"他站在形的祭坛前"。裸词只报不删，交给重写。
_METHOD_MARK_STRIP = (r"第[一二三四五六七1-7]拍[：:]|【节拍[^】]*】|节拍结束|"
                      r"开场状态[：:]|阻力源[：:]")


def strip_method_marks(text):
    """把七拍／节拍这类写法标记从正文里删掉。

    和繁体字同理：这是格式垃圾不是写作问题，让 Qwen 重写一整话去删七个标签，
    既慢又常常连内容一起改坏（2026-08-29 实测公路片三轮都没删干净）。
    只删标记本身；标记单独占一行时把空行一起收掉。
    """
    out = []
    for ln in str(text or "").split("\n"):
        if re.search(_METHOD_MARK_STRIP, ln):
            # 标记通常是整行小标题（**（第一拍：看见/发生）**）——只抠掉"第一拍："
            # 会留下 `**（看见/发生）**` 这种壳，校验器还不认识它，等于放行。
            # 整行删掉的条件卡死三条，缺一不可：标记出现在行首（只有标题才这样）、
            # 行里没有句末标点、剩下的字不超过 20——否则"他站在黄金三角形的祭坛前"
            # 这种正经句子会被当标签误删。
            bare = re.sub(r"[\s*_#（）()【】\[\]—-]", "", re.sub(_METHOD_MARK_STRIP, "", ln))
            head = re.match(r"^[\s*_#（()【\[]*(?:%s)" % _METHOD_MARK_STRIP, ln)
            if head and not re.search(r"[。！？…]", ln) and len(bare) <= 20:
                continue
            ln = re.sub(r"(?:%s)[ \t　]*" % _METHOD_MARK_STRIP, "", ln)
        out.append(ln)
    t = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return strip_camera_terms(strip_markdown(t))


# 分镜术语：正文里一句都不许有。实测 STORY_073 正文里写着
# 「镜头快速切换，画面晃动。」——下游会把它当成要拍的画面照拍
# （2026-08-30 自查）。只删**整句就是运镜说明**的那种，
# 「他的画面感」「镜子」这类正常用词不碰。
_CAM_SENT = (r"(镜头[^。！？\n]{0,12}(切换|推进|拉远|拉近|摇|移|晃动|一转|给到)|"
             r"画面[^。！？\n]{0,8}(切换|晃动|定格|淡出|淡入|一黑)|"
             r"(切换|切到)[^。！？\n]{0,6}(镜头|画面|特写)|"
             r"(特写|近景|中景|全景|远景)镜头)")


def strip_camera_terms(text):
    """正文里的分镜／运镜说明整句删。正文是小说，不是分镜表。"""
    t = str(text or "")
    out = []
    for para in re.split(r"(\n\s*\n)", t):
        if not para.strip() or para.startswith("\n"):
            out.append(para)
            continue
        keep = [s for s in re.split(r"(?<=[。！？])", para)
                if not (s.strip() and re.search(_CAM_SENT, s)
                        and not _quotes_in(s))]
        out.append("".join(keep))
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


def strip_markdown(text):
    """正文里漏出来的 markdown 记号（**加粗**、行首 # 、行尾孤立的 *）。

    实测 STORY_073 正文里有「**"意识体：林恩_02"**」，STORY_071 有一个
    孤立的行尾星号——这些会被下游当字符照抄进画面里（2026-08-30 自查）。
    """
    t = str(text or "")
    # 开头那一行标题（【第一话：云断处，剑未鸣】/「第一话 云海」）直接删。
    # 指令词里写「不写标题」压不住，删掉是确定性的（2026-08-31 实测）。
    t = re.sub(r"^\s*[【\[]?第[一二三四五六七八九十\d]+话[^\n]{0,24}[】\]]?\s*\n+",
               "", t)
    t = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", t)
    # 剩下的孤立星号一律去掉。原来要求星号前面不是字，于是行尾那种
    # 「源地址：阿K的神经接口*」一个都没清掉（2026-08-30 实测 STORY_074）。
    # 只留数字之间的乘号。
    t = re.sub(r"(?<![0-9])\*(?![0-9])", "", t)
    t = re.sub(r"^#{1,6}[ \t]*", "", t, flags=re.M)
    t = re.sub(r"^[ \t]*[-—–][ \t]+", "", t, flags=re.M)
    return t


def _place_conflicts(t):
    """场所自相矛盾。返回 [(说明, 证据1, 证据2)]。"""
    import re as _re
    out = []
    def _floor_pos(w):
        """这个多层词第一次**真的**当楼层用的位置；一次都没有返回 -1。"""
        for m in _re.finditer(_re.escape(w), t):
            if m.start() and t[m.start() - 1] in _NOT_FLOOR_PREFIX:
                continue                       # 钟楼上／塔楼下：是建筑名不是楼层
            return m.start()
        return -1

    # 文中出现"出租屋／公寓／客栈"这类**楼里的房间**时不查：
    # 「这间小屋是王都下城区最廉价的出租屋」——楼里的房间当然有走廊楼梯，
    # 判成"平房里冒出走廊"是误报（2026-08-30 实测）。
    if _re.search(r"(出租屋|公寓|楼房|旅馆|客栈|宿舍|大楼|楼里|同一栋|这栋)", t):
        return out
    flat = [w for w in _FLAT_BUILDING if w in t]
    multi = [w for w in _MULTI_FLOOR if _floor_pos(w) >= 0]
    if flat and multi:
        # 只在「多层词出现在单层词之后」时报——先写楼房后进小屋是合理的
        fi = min(t.find(w) for w in flat)
        mi = min(_floor_pos(w) for w in multi)
        if mi > fi:
            out.append(("这一处的建筑前后对不上：前面写的是%s，后面却出现了%s"
                        % ("／".join(flat[:2]), "／".join(multi[:2])),
                        t[max(0, fi - 20):fi + 30], t[max(0, mi - 20):mi + 30]))
    return out


def _goal_arrival_conflicts(t):
    """说好去 A，到的却是 B。只查文中明确写了「去X」又写了「来到了Y」的情况。"""
    import re as _re
    out = []
    goals = _re.findall(r"[去往到]([\u4e00-\u9fa5]{2,6}(?:教堂|神殿|城堡|哨站|"
                        r"营地|据点|旅店|码头|磨坊|高塔|地窖))", t)
    arrive = _re.findall(r"来到了?(?:一[座间处])?([\u4e00-\u9fa5]{0,6}"
                         r"(?:小屋|木屋|茅屋|教堂|神殿|城堡|哨站|营地|据点|"
                         r"旅店|码头|磨坊|高塔|地窖))", t)
    if goals and arrive:
        g = {x[-2:] for x in goals}
        a = {x[-2:] for x in arrive}
        if g and a and not (g & a):
            out.append("说好去的是「%s」，实际到达的却是「%s」，中间没有交代改道"
                       % ("／".join(sorted(g)), "／".join(sorted(a))))
    return out


def consistency_problems(text):
    """**正文和剧本都适用**的一致性检查：场所／目的地／光照／繁体。
    正文专属的那些（篇幅、穿帮词、套路句、七拍）不在这里——
    剧本套用正文全套会误报「出现不该有的词：镜头」「篇幅不够3000字」
    （2026-08-29 实测，三轮重试全浪费在改假问题上）。"""
    t = str(text or "")
    probs = []
    for msg, e1, e2 in _place_conflicts(t):
        probs.append("%s。证据：「…%s…」和「…%s…」" % (msg, e1.strip(), e2.strip()))
    for msg in _goal_arrival_conflicts(t):
        probs.append(msg + "——要么改掉目的地，要么写清为什么换地方")
    _dark = [w for w in _NO_SUN if w in t]
    _sun = [w for w in _HAS_SUN if w in t]
    if _dark and _sun:
        probs.append("光照前后矛盾：写了「%s」，后面又写「%s」——阴天雨天不许有阳光"
                     % (_dark[0], _sun[0]))
    _tr = sorted({c for c in t if c in _TRAD})
    if _tr:
        probs.append("混入繁体字：%s——全文一律用简体" % "、".join(_tr))
    return probs


# 摄影机拍不到的写法。都是 2026-08-30 用户实测里漏过去的：
# 「心里/内心」被堵住后，模型换成了「他想起」「代价是巨大的」「他浑然不觉」
# ——同样拍不到。
# ★ 这四条正则是**检测器和修复器共用的同一份**。口径分成两套的坑踩过三次：
#   校验说有问题、修复按另一套正则找不着，就成了死循环。要改一起改。
# 【光秃秃的「像X」也要抓】原来要求句里有"一样／似的／般"，实测
# STORY_071 的「像无数只昆虫在啃噬」「像一潭死水」「像一根刺扎进理智」
# 一个都没抓到，比喻照样进了下游。
# 排除的：不像／好像后缀、录像图像影像肖像画像摄像雕像偶像塑像头像群像
# 自像对象镜像这些**含"像"的正常名词**，以及「像素」。
_NOT_BI = r"(?<![不好录图影肖画摄雕偶塑头群自对镜想石佛神铜蜡玉])"
_PAT_BIYU = (r"(" + _NOT_BI + r"像(?!素)(是|得)?[^，。！？\n]{2,20}|"
             r"如[一-龥]{1,6}般|[一-龥]{1,3}如(?!果|何|此|今|同|下|上|前|后|愿|意|约|期|常|实|是)[一-龥]{1,5}(?=[，。！？、；])|"
             r"如同[^，。！？\n]{2,}|"
             r"仿佛[^，。！？\n]{2,}|宛如|好似|"
             r"[^，。！？\n]{1,6}(?<!一)般[的地][^，。！？\n]{0,8}|"
             r"[^，。！？\n]{1,4}如(纸|刀|血|墨|冰|火|电))")
_PAT_YANSHEN = re.compile(r"[^。！？\n]{0,30}(?:眼神[^，。！？\n]{0,4}(?:充满|透着|带着|透出|流露|闪过|多了|带上|掠过)[^，。！？\n]{1,8}|目光[^。！？\n]{0,12}(?:碰撞|交锋|火花)|火花四溅(?=[^。]*目光)|气氛[^，。]{0,6}(?:凝固|紧张|微妙)|空气[^，。]{0,4}(?:凝固|僵住)|张力[^，。]{0,4}(?:拉满|十足)|气场[^，。]{0,4}(?:全开|压制)|意味深长|难以捉摸|似笑非笑|似有[一-龥]{1,4}|藏着[一-龥]{0,6}(?:谜|深意|心事)|情绪复杂|热气[^，。]{0,4}交融|眼底[^，。]{0,4}(?:藏|闪))[^。！？\n]{0,30}[。！？]")
_PAT_HUIYI = (r"(他|她|我)?(想起|记起|记得|回想起|脑海里闪过|脑海中闪过|忆起)"
              r"[^。！？\n]{4,}")
# 「如果…会不会…」「或者，她根本就…」这种推演，摄影机同样拍不到。
# 实测 STORY_071 整整两段（2026-08-30）。台词里的「如果我拿走它，你会怎样？」
# 在引号里，修复时一律跳过，不受影响。
_PAT_QUANHENG = (r"(代价是|是一个代价|权衡|盘算|"
                 r"如果[^。！？\n]{3,}(会不会|会怎样|会变成|可能会|"
                 r"就[^。！？\n]{3,})|"
                 r"(他|她)知道[^。！？\n]{0,8}(在试探|不仅仅|并不是)|"
                 r"(他|她)在赌[^。！？\n]{0,}|"
                 r"或者，[^。！？\n]{0,4}(她|他|它)根本)")
_PAT_CHOUXIANG = (r"(浑然不觉|不易察觉|难以言喻|历经沧桑|眼神复杂|"
                  r"眼神里充满[了]?[^。！？\n]{2,}|"
                  r"心跳[^。！？\n]{0,4}(加速|漏了一拍|加快)|"
                  r"(压迫|存在|疲惫|安全|宿命|真实|荒诞)感|"
                  # 【不许只认"他／她"】实测 STORY_074 全篇用名字作主语：
                  # 「阿K感到一阵眩晕」「阿K的大脑飞速运转」，只认代词一处都抓不到。
                  # 主语不要求，动词本身就够判——引号里的（台词）另有一道跳过。
                  r"(明白过来|意识到|知道自己|感觉到|感到|感受到|察觉到|"
                  r"总觉得|隐隐觉得)[^。！？\n]{2,}|"
                  r"大脑[^。！？\n]{0,4}(飞速运转|一片空白|轰的一声)|"
                  r"充满[了]?某种[^。！？\n]{0,}|复杂(的)?情绪|"
                  # 实测 STORY_071 新增的一批：模型被堵住"心里"就改说
                  # 「显示出他内心正在进行的激烈挣扎」——换了个壳，一样拍不到
                  r"(显示出|表明|说明)[^。！？\n]{0,6}(内心|情绪|心理)[^。！？\n]{0,}|"
                  r"内心[^。！？\n]{2,}|"
                  r"(他|她)(感到|感受到|察觉到)[^。！？\n]{2,}|"
                  r"(似乎|仿佛)(隐藏|藏着|压抑)[^。！？\n]{0,}|"
                  r"(灵魂|精神|心灵)(疼痛|的疼|受伤|撕裂)|"
                  r"真实得[^。！？\n]{2,}|让人(想要流泪|安心|心碎))")
# 体内感觉：气流沿脊椎爬、血液、心跳、发热、刺痛、寒意——摄影机拍不到，
# 原样搬进镜头行 H3 会画成身体变形（项目97 第10段：男主"变异"）。
_PAT_NEIGAN = re.compile(
    r"[^。！？\n]{0,30}(?:(?:感到|感觉|觉得)[^。！？\n]{0,16}(?:刺痛|酸胀|眩晕|气流|热流|暖流|凉意|寒意|灼烧)|"
    r"(?:气流|热流|暖流|热意|热感|凉意|寒意|灼烧感|刺痛|麻意)(?:顺着|沿着|沿|顺|从|在)[^。！？\n]{0,10}(?:爬|窜|涌|蔓延|扩散|升|上行|下行|游走)|"
    r"(?:耳膜|眼睛|眼里|伤口|手臂|虎口|指尖|头皮|脊背)[^，。！？\n]{0,4}(?:生疼|发疼|刺痛|发麻|酸胀)|[，、]刺痛(?=[。，])|"
    r"(?:顺着|沿着)(?:脊椎|脊背|血管|骨头|骨缝)[^。！？\n]{0,8}(?:爬|窜|涌|升)|"
    r"心跳如鼓|心脏(?:狂跳|猛跳|擂鼓)|血液(?:奔涌|沸腾|倒流|冲上)|太阳穴(?:突突|狂跳)|头皮发麻)[^。！？\n]{0,40}[。！？]")
# 纯声音：句子的主体是"声音"本身（出鞘声、回荡、摩擦声、嗓音怎么样）。摄影机拍不到，
# 视频模型要么画不出来、要么把它当旁白念出来（项目97 第8段：音色描述被念成台词）。
_PAT_SHENGYIN = re.compile(
    r"[^。！？\n]{0,40}(?:的声音|这声音|声音(?:清脆|尖锐|沙哑|低沉|悠长|响起|不大|很轻|从|在)|余音|声响|回荡|作响|响声|低鸣|"
    r"爆裂声|摩擦声|撕裂声|脆响|闷响|巨响|轰响|轰鸣|呼啸声|嘶哑|沙哑|带着痰音|笑声|吼声|啸声|咆哮声|喘息声|震耳|听得见|听见|传来一声|"
    r"声浪|[一-龥]{1,3}声(?=[，。；！？、]|$))"
    r"[^。！？\n]{0,40}[。！？]")
# 气味、温度：拍不到，改成闻到/感到的人的可见反应。
_PAT_QIWEI = re.compile(
    r"[^。！？\n]{0,40}(?:霉味|焦糊气|腥气|血腥味|铁锈味|气味|臭味|香气|气息钻|味道钻|弥漫着|"
    r"比外面更冷|更冷|冰冷的空气|寒气|凉意|燥热|闷热|刺骨)[^。！？\n]{0,40}[。！？]")
# 否定式外观："没有腿""没有眼白""没有流血""没有头发"——模型看到名词照样画出来（用户 2026-09-03 审查结论）
_PAT_FOUDING = re.compile(
    r"[^。！？\n]{0,30}(?:没有|不见|无)(?:腿|眼白|头发|翅膀|流血|出血|双腿|手臂|鼻子|嘴|脸|眼睛|影子)[^。！？\n]{0,40}[。！？]")
# 触感：温热、滚烫、冰凉、发麻——拍不到
_PAT_CHUGAN = re.compile(
    r"[^。！？\n]{0,30}(?:温热|滚烫|冰凉|冰冷刺骨|发麻|麻木|灼手|温热而黏稠)[^。！？\n]{0,40}[。！？]")
_UNFILMABLE = (
    ("回忆", _PAT_HUIYI),
    ("权衡推演", _PAT_QUANHENG),
    ("比喻", _PAT_BIYU),
    ("抽象状态", _PAT_CHOUXIANG),
    ("内感", _PAT_NEIGAN),
    ("纯声音", _PAT_SHENGYIN),
    ("气味温度", _PAT_QIWEI),
    ("否定外观", _PAT_FOUDING),
    ("触感", _PAT_CHUGAN),
    ("抽象状态", _PAT_YANSHEN),
)
# 常见错别字：会被当台词念出来，必须拦住（实测「客官打烚了」）
_TYPOS = {"打烚": "打烊", "按奈": "按捺", "报歉": "抱歉", "急燥": "急躁",
          "渡过难关": "度过难关", "身影绰绰": "人影绰绰", "尉蓝": "蔚蓝",
          "决对": "绝对", "既使": "即使", "股票": None}


def unfilmable_problems(t):
    """正文里摄影机拍不到的写法。空 = 过。

    **引号里的不算**：台词一个字都不许改，修复器本来就跳过引号，
    检测器不跳就会一直报一条永远修不掉的（实测 STORY_073 的
    「就像把半条命从身体里硬拽出来」是林恩说的话，2026-08-30 自查）。
    检测和修复必须同一口径。
    """
    s = str(t or "")
    out = []
    for tag, pat in _UNFILMABLE:
        ms = [m.group(0)[:26] for m in re.finditer(pat, s)
              if not _pos_in_quote(s, m.start())]
        if ms:
            out.append("叙述里有%d处**拍不到**的写法（%s）：%s——"
                       "改成看得见的：要交代过去就让人物说出来或给一件物证；"
                       "要表现犹豫就写他的手伸出去又收回来；比喻直接写那个东西本身"
                       % (len(ms), tag, "、".join("「%s」" % x for x in ms[:2])))
    return out


def typo_problems(t):
    """错别字。台词里的错字会被念出来（实测「客官打烚了」）。"""
    s = str(t or "")
    bad = [(w, r) for w, r in _TYPOS.items() if r and w in s]
    if bad:
        return ["有错别字：%s——改过来，台词里的错字会被念出来"
                % "、".join("「%s」应为「%s」" % (w, r) for w, r in bad[:3])]
    return []


def prose_problems(prose, settings=None):
    """正文的硬伤清单（空 = 过）。每一条都来自实测，不是想象出来的规矩。"""
    t = str(prose or "")
    # 场所／目的地／光照／繁体这四项**已经在 consistency_problems 里了**，
    # 这里原样重做了一遍，于是每条都报两次——同一个假问题连报两条，
    # 反馈里也重复，白占重试的篇幅（2026-08-30 实测钟楼那条报了两遍）。
    probs = list(consistency_problems(t))
    # 【拍不到的写法和错别字不在这里挡】用户 2026-08-30 定的原则：
    # 不限制 Qwen 发挥，等他写完再检查，有错**直接改正修复**，不堵住让他重来。
    # 比喻实测一篇能有 25 处，靠反馈重写三轮也压不干净，而且每轮两分钟；
    # 这两类现在由 fix_unfilmable / fix_typos 在 write_prose 里逐句改掉，
    # 改完之后再用 unfilmable_problems() 复查一遍写进 debug 日志。
    bad = _anachronisms(t, settings)
    if bad:
        probs.append("出现了这个世界不该有的词：%s——用这个世界真实存在的事物来写"
                     % "、".join(bad))
    cli = _cliches(t)
    if cli:
        probs.append("出现了套路句：%s——一个都不许有" % "、".join(cli))
    # 结尾上帝视角/预告：钩子写完后忍不住加一段"剧透+排比+连问"（实测 5 篇中 3 篇）
    tail = t[-450:]
    god = []
    for w in _GOD_VIEW:
        m = re.search(w, tail)
        if m:
            god.append(m.group(0))          # 报命中的原文，不是正则本身
    tail_paras = [p.strip() for p in tail.split("\n") if p.strip()]
    # 对白段天生就短、高潮处的短促节拍也是好东西——真正的注水是**连续**几段
    # 单句叙述堆排比（实测：好结尾最长连续 1–2 段，注水结尾 4 段以上）
    run = best = 0
    for p in tail_paras[:-1]:
        if len(p) < 20 and not re.search(r"[「」“”]", p):
            run += 1
            best = max(best, run)
        else:
            run = 0
    tiny = ["x"] * (best if best >= 4 else 0)
    asks = [p for p in tail_paras if p.endswith("？") and not re.search(r"[「」“”]", p)]
    if god:
        probs.append("结尾用了上帝视角剧透或预告（%s）——最后一句必须是"
                     "故事里的人正在做的动作或正在说的话，写到一半就收笔，"
                     "后面一个字都不许有" % "、".join(god))
    if len(tiny) >= 4:
        probs.append("结尾滑成了一行一句的短句排比（连续 %d 段）——"
                     "结尾段落要和开头一样是有内容的完整段落" % len(tiny))
    if len(asks) >= 2:
        probs.append("结尾连着发问（%d 个问句段）——悬念靠没演完的动作留，不靠问读者"
                     % len(asks))
    # 时间线算术：本地模型算不了年份差（实测 2013→2025 被写成"三年前"）
    years = set(re.findall(r"(?:1[89]|20)\d{2}\s*年", t))
    if len(years) >= 2:
        probs.append("正文里出现了多个具体年份（%s）——不许写具体年份和精确年龄数字，"
                     "一律用'那年冬天''十二年前''三年后'这类相对说法" % "、".join(sorted(years)))
    # 引号纪律：心声写成引号对白会毒害下游剧本层（剧本层把引号内容当台词去对口型）
    heart = re.findall(_HEART_QUOTE, t)
    if heart:
        probs.append("把心里想的话写成了带引号的对白（%d 处）——"
                     "引号只留给真正说出口的话；心里想的用叙述写，不加引号" % len(heart))
    # 中文正文里混进英文单词（实测"这个时间， usually 只有熟客才会来"）
    en = re.findall(r"[一-龥][ ,，]{0,2}[a-z]{4,}[ ,，]{0,2}[一-龥]", t)
    if en:
        probs.append("正文里混进了英文单词（%s）——整篇只用中文" % "、".join(en[:3]))
    # 截断残句：结尾不落在终止标点上＝token 用完被砍（实测"他迈步进"三个字收场，
    # 机检当成了"动作做一半的钩子"——设计的定格和截断的残句必须分开）
    last = t.rstrip()
    if last and last[-1] not in "。！？…」”）』—":
        probs.append("正文结尾是被截断的残句（『%s』）——钩子要停在**完整的一句**上，"
                     "把最后一幕收成完整句子" % last[-14:])
    # 写作方法标记泄漏：七拍/节拍是方法不是文本，标记入文是修过又犯的老毛病
    leak = re.findall(_METHOD_MARK, t)
    if leak:
        probs.append("写作方法的标记泄漏进了正文（%s）——七拍、节拍这些是写法，"
                     "正文里只有小说本身，一个标记都不许出现" % "、".join(set(leak)))
    n = len(re.sub(r"\s", "", t))
    if n < 2600:
        # 反馈必须写死"不要加新情节"——加长度压力最容易换来注水和剧情膨胀
        probs.append("篇幅不够（%d 字，要 3000 字以上）——**不要加新情节、不要多加场景**，"
                     "把已有的关键节点按七拍写透：他心里怎么想、两难在哪、"
                     "先试探了什么、对方怎么回应、然后才做决定" % n)
    elif n > 7000:
        # 【只有下限没有上限】校验反馈一轮轮下来，Qwen 只会往里加东西——
        # 实测第三轮写到 10114 字，剧本跟着涨到 7247 字，人设那步的输出上限
        # 直接被撑爆（2026-08-29）。一话就该是一段连续的戏，不是一本书。
        probs.append("篇幅超了（%d 字，一话控制在 3000–6000 字）——"
                     "**砍掉支线和重复的心理描写**，只留这一话的核心事件；"
                     "不要把后面几话的内容提前写进来" % n)
    jump = [w for w in _JUMP_WORDS if w in t] + re.findall(r"接下来的[一二三四五六七八九十\d]+天", t)
    if jump:
        probs.append("用了一笔带过整段时间的写法（%s）——这一话只讲一个连续的时间段，"
                     "跳过去的部分留给下一话" % "、".join(jump[:3]))
    probs += _pose_conflicts(t)
    return probs


# 姿态连续性（2026-08-28 通用模板层修复）：确定性状态机，只抓两类高置信矛盾——
# "已经站着却又站起身""站着却靠上椅背"。实测 060 正文开头站着写信、后文三次
# 站起身、全篇没有一个坐下动作；这类语义断裂指令词管不住，状态机能管。
# 「坐起身」是从躺着坐起来，不是站起来——「起身」前面带"坐"要排掉，
# 否则「我坐起身」被记成站立，后文真站起来就报了假跳变（2026-08-30 实测）。
_POSE_VERB = (r"(站起身|站起|(?<!坐)起身|坐起|坐回|坐下|落座|坐在|坐进|"
              r"站在|站定|立在|靠在椅背|靠回椅背)")
# 说话归属句是认人名最可靠的来源：能说话的才是人物。
# 名字用**非贪婪**：贪婪会把动词首字吃进名字（「埃里克说」+「道」）。
_SPEAKER = re.compile(r"([一-龥·]{2,6}?)(?:轻声|低声|沉声|大声|冷冷地|缓缓)?"
                      r"(?:说道|问道|喊道|说|道|问|答|喊)")


def _pose_names(t):
    """正文里出现过的人名。**不许用通配符猜**——原来用 `[一-龥·]{2,4}` 硬套，
    既把「喘着粗气」当成人名报了一条永远改不掉的问题，又因为贪婪匹配把动词首字
    吃进名字（「埃里克站」+「起身」）。这两种都是模型改不动的假问题，
    白耗五轮重试（2026-08-30 实测剑风传奇段）。
    """
    from collections import Counter
    c = Counter(_SPEAKER.findall(str(t or "")))
    # 出现两次以上才算准人名，一次的多半是把动词短语切错了。
    # 代词和语气副词开头的一律不算（实测认出过「她轻声」这种假名）。
    stop = {"有人", "那人", "众人", "旁人", "路人", "所有人", "每个人", "什么人"}
    return {n for n, k in c.items()
            if k >= 2 and not re.search(r"[着了过地的声]", n)
            and not re.match(r"^[他她我你它]", n) and n not in stop}


def fix_pose_jumps(text):
    """人已经站着又写一次「站起身」——把多余的那个动作删掉。

    人已经是站姿，"站起身"就是个多余动作，删掉句子照样通顺、意思不变：
    「他站起身，走到窗台边」→「他走到窗台边」。整话重写三轮改不掉这种毛病
    （2026-08-30 实测），而它是确定性的：状态机知道他此刻站着。
    只删**重复的站起**，不动坐下和其它姿态。
    """
    t = str(text or "")
    for _ in range(4):
        spans = _pose_conflict_spans(t)
        if not spans:
            break
        a, b = spans[0]
        head, tail = t[:a], t[b:]
        if head.endswith(("，", "、")):        # "放下茶杯，站起身。" → "放下茶杯。"
            head = head[:-1]
        elif tail.startswith(("，", "、")):    # "站起身，走到窗台边。" → "走到窗台边。"
            tail = tail[1:]
        t = head + tail
    return t


def _pose_conflict_spans(t):
    """报问题的那个动词在原文里的字符区间（给机械删用）。"""
    return [s for s, _msg in _pose_scan(t) if s]


def _pose_conflicts(t):
    return [msg for _s, msg in _pose_scan(t)]


def _pose_scan(t):
    names = _pose_names(t)
    if not names:
        return []
    # 主语和动词之间常夹一整个动作短语（"他**走到床边**坐下"）。原来只放行
    # 几个字，于是那次"坐下"没被记录，后面的"起身"就被当成跳变报了假问题
    # （2026-08-30 实测林深）。改成放行一小段、但**不许夹进别人的名字**，
    # 免得"林浅看着张三站起身"把姿态记到林浅头上。
    # 「我」也要跟：第一视角题材里主角就是「我」，不跟等于整篇不查
    nm_pat = "|".join(sorted(map(re.escape, names), key=len, reverse=True))
    evt = re.compile(r"(%s|他|她|我)([^。！？\n]{0,12}?)%s" % (nm_pat, _POSE_VERB))
    others = re.compile(nm_pat)
    state, last_name, out = {}, None, []
    for m in evt.finditer(str(t or "")):
        n, gap, v = m.group(1), m.group(2), m.group(3)
        # 中间出现了别人、或出现观察动词（"林浅**看着**张三站起身"）——
        # 这个动作是别人做的，不算他的。观察动词这条不依赖人名表，
        # 没说过话的配角也拦得住。
        # 三种情况这个动作不算他的：
        # ① 中间出现了别人的名字；② 中间出现观察动词（"林浅**看着**张三站起身"）；
        # ③ 中间换了主语代词（"看到我，**他**并没有起身"——主语是他不是我）。
        # 另外**否定句不算动作**（"并没有起身"）。
        if (others.search(gap) or re.search(r"(看着|望着|看向|注视|盯着|听着|见)", gap)
                or re.search(r"[他她我]", gap)
                or re.search(r"[没未不]", gap)):
            continue
        # 倒叙里的姿态不算当下状态：「**五年前**，也是这样一个雨夜。他提着行李箱
        # 站在门口」被算进当下，于是"坐在沙发→（倒叙站着）→站起身"判成跳变
        # （2026-08-30 实测）。往前看一句，带回忆标记就跳过。
        _s0 = max(str(t or "").rfind(c, 0, m.start()) for c in "。！？\n") + 1
        _sent = str(t or "")[max(0, _s0 - 60):m.start()]
        if re.search(r"(年前|个月前|天前|那年|当年|那时|从前|小时候|记忆|"
                     r"想起|记得|回想|梦[见里中])", _sent):
            continue
        if n in ("他", "她"):
            n = last_name
        else:
            last_name = n
        if not n:
            continue
        s = state.get(n)
        _sp = (m.start(3), m.end(3))
        if v in ("站起身", "站起", "起身"):
            if s == "stand":
                out.append((_sp, "%s已经站着，却又「%s」——中间缺少坐下的动作。"
                            "人物站/坐状态的每次改变都必须写出动作，不许跳变" % (n, v)))
            state[n] = "stand"
        elif v in ("靠在椅背", "靠回椅背"):
            if s == "stand":
                out.append((None, "%s站着却「%s」——中间缺少坐下的动作" % (n, v)))
            state[n] = "sit"
        elif v in ("坐回", "坐下", "落座", "坐在", "坐进", "坐起"):
            state[n] = "sit"
        else:
            state[n] = "stand"
    seen, uniq = set(), []          # 同一个人跳变两次报两条一模一样的话，去重
    for sp, msg in out:
        if msg not in seen:
            seen.add(msg)
            uniq.append((sp, msg))
    return uniq[:4]


# 高置信现代词黑名单：只收基本不可能在古代/奇幻叙述里合法出现的词，宁缺毋滥
# 现代词黑名单**按世界分档**：现代都市/校园/未来科幻/末日里"手机、镜头、分钟"
# 完全合法，无条件黑名单会误伤（这一版差点把现代题材的正文判成穿帮）。
_ANACHRONISM_CORE = ("投屏", "屏幕", "手机", "电脑", "网络", "视频", "摄像", "自拍",
                     "卡路里")
_ANACHRONISM_STRICT = _ANACHRONISM_CORE + (
    "电话", "电灯", "汽车", "火车", "飞机", "相机", "拍照", "倒计时", "秒表",
    "闹钟", "按钮", "开关", "公里", "公斤", "百分之", "镜头", "分钟", "秒钟",
    # 【科技／医疗一档】实测 STORY_075：东方仙侠的正文里写了「后颈的接口」
    # 「十年前手术台上的白光」「玉简里有一段录音」——世界观当场串味，
    # 而这些词一个都不在词表里（2026-08-30 用户实测仙侠项目）。
    # 只收前工业世界里基本不可能合法出现的，「系统」这类系统流常用词不收。
    "手术", "芯片", "接口", "义体", "数据", "程序", "录音", "激光",
    "电路", "电池", "频率", "扫描", "存储")
_ERA_STRICT = ("东方仙侠", "西方魔幻")      # 前工业时代：连电话火车都不该有
_ERA_CORE = ("东方写实", "西方写实")        # 古代到近代：可能有电灯火车，只禁当代科技

# 网文第一话结尾套路禁句：三轮实测 Qwen 结尾必滑向"幕后人现身+命运宣言+上帝视角
# 剧透"（反馈里明令禁止的原话都会再犯）——先验太强，规则治不了，黑名单兜底。
_GOD_VIEW = (r"[^\n。，]{0,10}不知道的是", r"而这一切", r"等待着[他她它][们]?",
             r"正在等待", r"殊不知", r"[^\n。]{0,8}的目标[，,]?是",
             r"命运", r"这一夜[^\n。]{0,10}注定", r"从这一刻起")
_JUMP_WORDS = ("几天后", "数日后", "一周后", "半个月后", "一个月后", "日子悄然",
               "转眼", "不知过了多久", "一段时间过去", "渐渐地", "时间过得飞快",
               "三天后的", "第二天清晨", "翌日清晨")

_CLICHE = ("才刚刚开始", "仅仅是个开始", "仅仅只是一个开始", "只是一个开始",
           "他不知道的是", "她不知道的是", "他们不知道的是", "命运的齿轮",
           "欢迎来到", "等待着下一个", "与命运", "命运之门")


def _anachronisms(text, settings=None):
    """这个世界不该出现的现代词。现当代/未来/末日世界一律不查。"""
    w = str((settings or {}).get("world_type") or "").strip()
    if w in _ERA_STRICT:
        words = _ANACHRONISM_STRICT
    elif w in _ERA_CORE:
        words = _ANACHRONISM_CORE
    elif settings is None:
        words = _ANACHRONISM_STRICT          # 没给设定时按最严档（老调用方）
    else:
        return []
    return [x for x in words if x in str(text or "")]


def _cliches(text):
    t = str(text or "")
    return [w for w in _CLICHE if w in t]


def summarize_brief(prose, settings):
    """正文 → 忠实提炼的简介（150–250字，点出结尾钩子）。第一话正文先行后用它回填。"""
    return _q(_fill(_ins("第一话简介提炼_指令词.txt"), settings),
              "这一话的正文：\n\n" + str(prose or "").strip() + "\n\n提炼这一话的故事简介。",
              mt=800, temperature=0.5)


def ai_expand(kind, text, settings):
    """扩写简介或正文（kind: '简介' / '正文'）。只扩不改。"""
    sys = _fill(_ins("AI扩写_指令词.txt"), settings)
    return _q(sys, "这是一段%s，请扩写：\n\n%s" % (kind, str(text or "").strip()),
              mt=5200, temperature=0.8)


def prose_to_screenplay(prose, settings, characters=None, scene_names=None, feedback=""):
    """第一话原文 → 剧本（编剧）。

    characters / scene_names 是给编剧的「名字对照表」，两个都缺不得：
    · 人物卡：正文里常用称谓（大师姐），但下游按卡名（凌霜）匹配参考图——
      编剧必须拿到卡名，才能在署名和镜头描述里把称谓换回名字。
    · 场景卡名：场景名跨次生成会漂移（同一间客房三次生成叫了三个名字），
      指令词只能管住一次生成之内；把已有卡名喂进来，编剧照抄，不再另起。
    """
    user = "第一话故事正文：\n\n" + str(prose or "").strip()
    if scene_names:
        user += ("\n\n【已有场景卡】" + "、".join(scene_names)
                 + "\n正文里的地点能对上这些卡的，场景名照抄卡名、一字不差；"
                   "真正的新地点才起新名字。")
    user += "\n\n把它改编成可拍摄的剧本。"
    if str(feedback or "").strip():
        user += "\n\n★★【上一版不合格，整份重写】" + str(feedback).strip()
    return _q(_fill(_ins("原文改剧本_指令词.txt"), settings, characters=characters),
              user, mt=5200, temperature=0.7)



# ════════ 画面稿（正文→画面）的检查与补写 ════════
# 用户 2026-08-31 定的口径：**画面内容不许丢、台词不许丢**，
# 其余（光线、反应、动词准不准）指令词尽力，做不到的不强求。
# 两个检查器都只报"丢了什么"，不改稿；补写只补缺的那几处，原稿一个字不动。

_PIC_SPLIT = re.compile(r"\n\s*\n")
# 台词行的说话人是个干净的名字：不含标点、不含叙述动词。
# 原来只要求"12 字内有冒号"，「老陈双手攥住扳手，他吼道："压力阀锁死了。"」
# 整句被判成台词行，规整就直接放过它，引号里的台词永远提不出来
# （2026-08-31 实测）。
_PIC_DLG = re.compile(
    '^([^\\s：:，。！？、（(【#─"“”「」]{1,8})'
    '(（[^）\\n]{0,6}）)?\\s*[：:]\\s*(.+)$')


def _pic_parts(pics):
    """把画面稿拆成 [(是不是台词行, 原文)]。地点行（── xx ──）单独算。

    **按行拆，不是按空行拆**：模型常把画面和台词写在同一块里、只用单换行
    隔开，按空行拆的话整块被当成一段画面，16 个独立台词行一个都统计不到
    ——害我以为规整失败，其实稿子是好的（2026-08-31 自查）。
    """
    out, buf = [], []

    def _flush():
        if buf:
            t = "\n".join(buf).strip()
            if t:
                out.append((False, t))
            del buf[:]

    for raw in str(pics or "").splitlines():
        line = raw.strip()
        if not line:
            _flush()
            continue
        if line.startswith("──"):
            _flush()
            out.append((False, line))
            continue
        if _PIC_DLG.match(line) and len(line) < 120:
            _flush()
            from .oral_story import dialogue_performance
            m = _PIC_DLG.match(line)
            spoken, cues = dialogue_performance(m.group(3))
            if m.group(2):
                cues.insert(0, m.group(2)[1:-1])
            if cues:
                out.append((False, m.group(1) + "，" + "，".join(cues) + "。"))
            out.append((True, m.group(1) + "：" + spoken))
            continue
        buf.append(line)
    _flush()
    return out



_PIC_SAY = re.compile(r"[「“]([^」”\n]{2,60})[」”]")
# 【名字不许把叙述动词吃进去】原来是 ([\u4e00-\u9fa5]{2,4})(?:说|问|喊|吼|叫…)，
# 正则贪心，「老陈吼道」抓出的名字是"老陈吼"，「阿杰惨叫」抓出"阿杰惨"，
# 台词行就成了「老陈吼：…」「阿杰惨：…」（2026-08-31 实测）。
# 名字取最短的 2~3 字，后面允许夹一个修饰字（大吼／惨叫／低声的头一个字）。
_PIC_WHO = re.compile(
    r"([\u4e00-\u9fa5]{2,3}?)[\u4e00-\u9fa5]{0,1}(?:说|问|喊|吼|叫|道|低声|开口|嘀咕)")



_DLG_CACHE = {}


def extract_dialogue(prose):
    """从正文提取 [(说话人, 台词)]，顺序照正文。一次调用。

    为什么不用正则：中文没有词边界，「阿杰惨叫一声」切出的名字是"阿杰惨"；
    名字和动词之间还夹修饰字（老陈**大**吼）；而且一半的台词写的是代词
    （「"没干。"他说」），要往前追好几句才知道"他"是谁——那是指代消解，
    正则做不了。模型读得懂上下文，判得准（2026-08-31 实测 13 句全对）。

    每一句都用正文原文校对：模型给的台词必须在正文里逐字查得到，
    说话人必须是正文里出现过的名字。对不上的那一句丢掉，不影响别的。
    """
    src = str(prose or "")
    # 【缓存】同一份正文在规整、建表、补写归位三处各被提一次，
    # 一篇稿子白跑 3.5 次调用（2026-08-31 实测）。正文不变，结果就不变。
    ck = hash(src)
    if ck in _DLG_CACHE:
        return _DLG_CACHE[ck]
    quotes = []
    for m in re.finditer(r"[「“]([^」”\n]+)[」”]", src):
        q = m.group(1)
        tail = src[m.end():m.end() + 1]
        if tail and tail in "字声样般似的" and \
                len(re.sub(r"[^\u4e00-\u9fa5]", "", q)) <= 3:
            continue
        if len(re.sub(r"[^\u4e00-\u9fa5]", "", q)) >= 2:
            quotes.append(q)
    if not quotes:
        _DLG_CACHE[ck] = []
        return []
    sysmsg = ("用户给你一段故事，和故事里按顺序列出的每一句台词。"
              "你只做一件事：说出每一句是**谁**说的。\n"
              "· 名字从故事里取，用故事里给的正式名字；\n"
              "· 故事里写「他说」「她问」这种代词的，往前看是谁在做动作，"
              "写那个人的名字；\n"
              "· 实在看不出是谁说的（通讯器里、门外、黑暗里的声音），"
              "写「画外音」；如果那声音是响在某个人脑子里的，"
              "写「异声（某某脑内）」。\n"
              "逐条回复，一行一条，格式就是「序号. 名字」，"
              "不要重复台词、不要解释、不要加空行。")
    user = ("【故事】\n%s\n\n【台词，按顺序】\n%s"
            % (src, "\n".join("%d. %s" % (i + 1, q)
                               for i, q in enumerate(quotes))))
    try:
        rep = _q(sysmsg, user, mt=900, temperature=0.2)
    except Exception:
        return [("", q) for q in quotes]   # 失败不缓存，下次重试
    who = {}
    for line in str(rep or "").splitlines():
        m = re.match(r"^\s*(\d+)\s*[.、．)]\s*(.+?)\s*$", line)
        if m:
            who[int(m.group(1))] = m.group(2).strip("：: 　")
    out = []
    for i, q in enumerate(quotes):
        w = who.get(i + 1, "")
        # 【直接查子串，别切词表】原来把正文按 2~4 字滑窗切成词表，
        # 要求说话人恰好等于表里某个词——「摊主」「埃里克」正文里明明有，
        # 滑窗切不出恰好相等的词，模型答对的 8 句被整批毙掉
        # （2026-09-01 实测）。今天第四次栽在"检查器比产物还严"上。
        base = re.sub(r"（[^）]*）", "", w).strip()
        if w and not w.startswith(("画外音", "异声")) and src.find(base) < 0:
            _dbg("台词说话人查无此人", {"给的": w, "台词": q[:20]})
            w = ""
        out.append((w, q))
    _dbg("台词提取", {"共": len(out),
                      "有名字": sum(1 for w, _ in out if w)})
    _DLG_CACHE[ck] = out
    return out



# ════════ 画面稿 → H3 提示词（WorkFisher 文戏模板）════════
# 模板出处：F:\夸克网盘\【WorkFisher】全网最强！MINIMAX文戏工作流.json 的
# CR Prompt Text 节点。作者标注的三条硬规矩：
#   ① 台词必须符合时长，超了会出大量噪音
#   ② 台词格式一个字都不能改，改了音质下降、杂音、**角色台词互窜**
#   ③ 武戏工作流下期发布（到时再对）

_H3_BIND = ("<Subject%d>%s是<Picture %d>保留参考图中的面部特征、发型、服装、"
            "身材比例、眼镜（如有）以及整体外貌。")
_H3_BIND_STATE = ("<Subject%d>%s是<Picture %d>保留参考图中的面部特征、发型、身材比例以及整体外貌；"
                  "本段衣着：%s")
# 冒号换逗号：「固定的声音：三十出头男性…」H3 会把冒号后面的描述当成一句台词念出来
# （用户 2026-09-02 看项目97 第8段成片指出）。模板原文是冒号，但实测会串成台词。
_H3_VOICE = ("<Subject%d>, S%d is always the speaker. S%d always uses one fixed voice, %s")   # 音色头用英文、不带冒号：中文头会被当台词念，冒号会把后面变成台词（用户 2026-09-02 指出）
_H3_SCENE = "<Subject %d>是%s场景。"
# 场景有参考图时用这一句——不给图，H3 就照人设图的白影棚底画背景
# （2026-09-01 用户实测：三段视频背景全是纯白）
_H3_SCENE_PIC = ("<Subject %d>是<Picture %d>%s场景，"
                 "保留参考图中的空间结构、材质、光线和色调。")
# 没有台词的段落：**正面说该有什么声音**，不要写"不许出现人声"。
# 实测写了"不要出现人声"，第 3 段结尾照样冒出一句日语（2026-09-01 用户看片）
# ——今天反复验证：否定式指令不管用，得给它正面的东西去生成。
_H3_SILENT = "本段全程没有人说话。"
# 【正面写，别写禁令】原来是「禁止项：文字/UI/水印…」，画面照样出字幕
# （2026-09-01 用户实测第 4 段）。今天反复验证：否定式指令不管用。
# 改成正面描述画面该是什么样。
# 【别点名不想要的东西】上一版写「墙面、招牌、纸张、屏幕……上面一个字都没有」，
# 实验 V5 两版墙上都冒出了带乱字的海报——名词喂给模型它就画（2026-09-02 实测）。
# 只说墙上"有什么"，不列举"没有什么"。
_H3_TAIL = "【强制声明】无背景音乐，仅保留环境音与人声和音效。"
# 用户 2026-09-02：尾部只留「无背景音乐」这一条；【禁止项】和「表面空白」那两块撤掉，
# 有没有字幕看成片再说。
# 没给音色时的兜底，按模板的写法：年龄性别→音高音色→常态语速音量→
# 紧张时怎么变→放松时怎么变→禁止什么腔
_H3_VOICE_FALLBACK_CHILD = ("嗓音清亮偏高、还没变声，音色干净，常态语速中等、音量适中，句尾利落下收；紧张时起句更短、语速加快但压低音量；放松时气息松开、尾音略缓。禁止播音腔和夸张舞台腔。")
_H3_VOICE_FALLBACK = ("成年人，中音，音色干净自然，常态语速中等、音量适中，"
                      "句尾利落下收；紧张时起句更短、语速加快但压低音量；"
                      "放松时气息松开、尾音略缓。禁止播音腔和夸张舞台腔。")



_H3_SEX = [("男", ("男", "男性", "少年", "先生", "大叔", "老头", "父", "哥", "爷")),
           ("女", ("女", "女性", "少女", "小姐", "姑娘", "大妈", "婆", "姐", "娘"))]
# 禁令按题材给——H3 默认最容易跑成播音腔，其余按世界观补
_H3_BAN = {
    "现代都市": "禁止播音腔、夸张精英腔和做作的偶像剧腔。",
    "东方仙侠": "禁止播音腔、戏曲唱腔和端着的古装腔。",
    "西方魔幻": "禁止播音腔、舞台剧腔和刻意的译制片腔。",
    "未来科幻": "禁止播音腔、机械音和夸张的科普解说腔。",
    "末日": "禁止播音腔和夸张的嘶吼腔。",
    "校园": "禁止播音腔、幼态撒娇腔和夹子音。",
}
_H3_BAN_DEFAULT = "禁止播音腔和夸张的舞台腔。"



_H3_TAIL_LINES = ("【禁止项】", "【强制声明】")


def fix_h3_format(text):
    """把导演产出的每一段补成 H3 模板要的形状。**只补格式，不动内容。**

    两件事：
      ① 台词行漏了 [Chinese] 就补上——实测五句台词全漏
         （写成 <d>压力阀锁死了。</d>，少了语种标记）。
      ② 段尾没有【禁止项】【强制声明】就补上——写在指令词里模型不写，
         它写完站位表就收尾了（2026-09-01 实测 0/7 段）。
    """
    t = str(text or "")
    # ① 补 [Chinese]
    t = re.sub(r"(says:\s*<d>)(?!\[)", r"\1[Chinese]", t)
    # 顺手把漏掉的 <Subject N> 补回来（B 版同款毛病）
    t = re.sub(r"^([\u4e00-\u9fa5]{2,4})说[：:]\s*\(S(\d+)\)\s*says:",
               r"\1说：<Subject \2> (S\2) says:", t, flags=re.M)
    # ② 补段尾声明
    if _H3_TAIL_LINES[0] not in t:
        t = t.rstrip() + "\n" + _H3_TAIL
    return t

def h3_voice(char, settings=None):
    """把人物卡拼成模板要的那一段音色设定。

    人物卡已经写好的 voice 一个字不改，只在**开头补年龄性别**、
    **结尾补禁令**——这两头是模板有、我们缺的（2026-08-31 对照）。
    字段缺了就跳过那一截，绝不编。
    """
    c = char or {}
    age = str(c.get("age") or "").strip()
    sex = str(c.get("sex") or "").strip()
    # 性别：sex 字段常常是空的，从 age 和外貌里认
    if not sex:
        blob = " ".join(str(c.get(k) or "") for k in
                        ("age", "char_type", "appearance_details",
                         "identity_anchor", "build", "hair"))
        for label, keys in _H3_SEX:
            if any(k in blob for k in keys):
                sex = label
                break
    # 声音提示也保留真实年龄；不把40岁压成模糊年龄段。
    raw_age = age
    n = age_number(raw_age)
    age = (str(n) + "岁") if n is not None else raw_age
    sex_word = (sex + "性") if sex else ""

    head = (age + sex_word).strip()
    body = str(c.get("voice") or "").strip()
    if not body:
        if is_minor(char):
            # 成年那份写死了「成年人，中音」——10 岁的孩子卡上没写声线时，
            # 视频提示词会说她是成年人（P221b）。这份不带年龄措辞，
            # 所以 head 里的「10岁小女孩」要留着。
            body = _H3_VOICE_FALLBACK_CHILD
        else:
            body = _H3_VOICE_FALLBACK.replace("成年人，", "")
    ban = _H3_BAN.get(str((settings or {}).get("world_type") or ""),
                      _H3_BAN_DEFAULT)
    parts = [x for x in (head, body) if x]
    out = "，".join(parts) if len(parts) == 2 else (parts[0] if parts else body)
    if not re.search(r"禁止", out):
        out = out.rstrip("。；;") + "。" + ban
    return out


_SPEAK_CPS = 3.5          # 中文语速，字/秒（代码兜底用；4.5 太快，台词说不完）
_PIC_SECS_MIN = 3.5       # 一幅无台词画面最少给 3.5 秒（一幅＝一个镜头，不到 3 秒视频里看不清；2026-09-04 项目99 实测 28 幅只切出 78 秒）
_PIC_SECS_MAX = 6.0


def _block_seconds(block):
    """一块（一幅画面 + 跟着的台词行）要演多少秒。"""
    pic_len, say_len = 0, 0
    for line in block:
        m = _PIC_DLG.match(line.strip())
        if m and len(line.strip()) <= 200:
            say_len += len(re.sub(r"[^\u4e00-\u9fa5]", "", m.group(3)))
        else:
            pic_len += len(re.sub(r"[^\u4e00-\u9fa5]", "", line))
    secs = say_len / _SPEAK_CPS
    # 画面本身也要时间演；有台词时台词时间已经覆盖了大部分
    base = min(_PIC_SECS_MAX, max(_PIC_SECS_MIN, pic_len / 45.0))
    return (secs + base * 0.5) if say_len else base


def picture_blocks(pics):
    """把画面稿切成块：一幅画面 + 紧跟它的台词行 = 一块。地点行单独一块。"""
    blocks, cur = [], []
    for k, p in _pic_parts(pics):
        if p.startswith("──"):
            if cur:
                blocks.append(cur)
                cur = []
            blocks.append([p])
            continue
        if k:
            # 【一句台词一块】台词是时长的硬约束，一幅画面底下挂 8 句
            # 就是 15 秒说不完（2026-09-01 实测买卖对话全堆在一幅画面下）。
            # 一句台词一个镜头，也正是模板演示的样子。
            cur.append(p)
            blocks.append(cur)
            cur = []
            continue
        if cur:
            blocks.append(cur)
        cur = [p]
    if cur:
        blocks.append(cur)
    return blocks



_ACT_SECS_SYS = """你是拍片子的副导演。用户给你一段一段的分镜画面，有的带台词。你只做一件事：
**估每一条演出来要几秒**，分两个数：
· 动作秒：画面里的动作、停顿、反应要多久；
· 台词秒：这句话按角色当时的情绪、正常语速念出来要多久，含开口前的呼吸和说完的停顿；没台词写 0。

按真人拍摄的节奏估：
· 每一条就是一个镜头，**最少 3 秒**——不到 3 秒的镜头在视频里看不清，估出来不到 3 的一律按 3 算；
· 一个简单动作（抬手、转头、看一眼）连同前后的停顿：3 秒；
· 一串连着的动作（坐起来→下床→走到柜子前）：一个动作 2 秒往上加，三个动作就是 6 秒；
· 情绪停顿（对视、愣住、沉默、回味、犹豫）：2~3 秒——这种要时间落地，给少了就演不出来；
· 剧烈动作（跑、跳、闪避、打斗、摔倒、擦身而过）：一个回合 2~3 秒；
· 纯环境空镜：2~3 秒；一个人静止的姿势：1~2 秒；
· 中文台词大约每秒 3 到 4 个字，带情绪的、拖长音的更慢。

**逐条回复，一行一条，格式就是「序号. 动作x.x 台词y.y」**，秒数写一位小数。
不要解释、不要写内容、不要加空行。"""



def lean_script():
    """精简剧本链（P283）：V41_LEAN_SCRIPT=1 时只留 画面稿 + 拍摄清单，
    导演审校 / 逐幅扩写 / 改写拍不到的句子 / 模型估秒 全部跳过——用户 2026-09-11 定的"关闭连环扩写和模型自我审稿"。"""
    import os
    # P304（2026-09-11 二次规划验收实测）：完整链（导演审校 + 逐幅扩写 + 补漏循环）把 1400 字正文的画面稿顺序打乱
    # （离开照相馆的段落跑到修车铺对话之后、又编出一个"慧扬"），精简链一遍改编顺序不乱——精简链改为默认。
    # 要回到完整链设 V41_FULL_SCRIPT=1；V41_LEAN_SCRIPT=0 也等于完整链。
    v = str(os.environ.get("V41_LEAN_SCRIPT") or "").strip().lower()
    if v in ("0", "off", "false"):
        return False
    if str(os.environ.get("V41_FULL_SCRIPT") or "").strip().lower() in ("1", "on", "true"):
        return False
    return True


def use_model_seconds():
    """动作时长要不要问模型。默认要，V41_MODEL_SECONDS=off 退回纯代码；精简剧本链也不问。"""
    import os
    if lean_script():
        return False
    return str(os.environ.get("V41_MODEL_SECONDS") or "on").strip().lower() != "off"


def _act_seconds(blocks):
    """一次问完所有块的时长。返回 [(动作秒, 台词秒或None)]，问不到就退回代码估值。

    原来只问动作、台词按 4.5 字/秒硬算——实测一段 3 句台词的戏代码算 11.8 秒、
    模型估 23 秒，塞进 12 秒的视频里，台词说不完就成乱码（2026-09-02 用户点名）。
    现在动作和台词都让模型按真人念的节奏估；模型没回的那条退回代码值。
    """
    if not blocks:
        return []
    fallback = []
    items = []
    for i, b in enumerate(blocks, 1):
        pic = " ".join(l for l in b if not _PIC_DLG.match(l.strip()))
        say = [_PIC_DLG.match(l.strip()).group(3) for l in b if _PIC_DLG.match(l.strip())]
        say_chars = sum(len(re.sub(r"[^\u4e00-\u9fa5]", "", q)) for q in say)
        fallback.append((_block_seconds([l for l in b if not _PIC_DLG.match(l.strip())]) or 2.0,
                         say_chars / _SPEAK_CPS if say_chars else 0.0))
        items.append("%d. 画面：%s%s" % (i, re.sub(r"\s+", "", pic)[:160] or "（空镜）",
                                         ("｜台词：" + "／".join(say)) if say else ""))
    try:
        rep = _q(_ACT_SECS_SYS, "【要估的画面】\n" + "\n".join(items),
                 mt=900, temperature=0.2)
    except Exception:
        return fallback
    got = {}
    for line in str(rep or "").splitlines():
        m = re.match(r"^\s*(\d+)\s*[.、．)]\s*动作\s*([\d.]+)\s*台词\s*([\d.]+)", line.strip())
        if m:
            try:
                got[int(m.group(1))] = (float(m.group(2)), float(m.group(3)))
            except Exception:
                pass
    out = []
    for i in range(1, len(blocks) + 1):
        fa, fs = fallback[i - 1]
        a, sy = got.get(i, (None, None))
        # 夹一下：动作 1~10 秒；台词不低于按 3 字/秒算的下限（模型偶尔给 0）
        a = min(10.0, max(3.0, a)) if a is not None else fa
        floor = fs * _SPEAK_CPS / 3.0 if fs else 0.0
        sy = max(floor, sy) if sy is not None else fs
        out.append((a, sy))
    _dbg("时长估算", {"块": len(blocks), "模型给了": len(got),
                      "合计": round(sum(a + b for a, b in out), 1)})
    return out


def slice_pictures(pics, budget=13.0, arrangement=None):
    """按时长把画面稿切成视频段。返回 [{"text":…, "seconds":…, "over", "scene_hint", "beat", "beat_index"}]。

    budget 默认 13 秒——H3 上限 15 秒，留 2 秒余量。
    **一块本身就超预算时单独成段**（那是一句长台词，只能这样），
    并在结果里标出来，让上层知道这一段会超。
    arrangement：已确认的本话安排。给了就每段带 beat/beat_index（0～3，判不出 -1），
    没给全是 ""/-1。
    """
    out, cur, secs = [], [], 0.0
    # 【地点行是权威】画面稿里的「── 城堡净身房｜午后 ──」是剧本自己写的地点，
    # 比事后按文本猜准得多。这里记住每段**开头**所在的地点，交给上层绑场景板。
    # 不记的话上层只能猜，还会被「至少停两段」的平滑规则改错（P243 项目153：五段错四段）。
    here = [""]          # 当前地点（闭包里改）
    cur_scene = [""]     # 这一段开头时的地点
    cur_beat = [-1]      # 这一段属于安排的哪一段情节（-1：没安排/判不出）
    from . import arrangement as _arm

    def _flush():
        if cur:
            _txt = "\n\n".join("\n".join(b) for b in cur)
            _action = "\n".join(line for line in _txt.splitlines() if not _PIC_DLG.match(line.strip()))
            _sentences = max(1, len(re.findall(r"[。！？!?]", _action))) if _action.strip() else 0
            _say_chars = sum(len(re.sub(r"[^一-龥]", "", q)) for _, q in _pic_parts_dialogue(_txt))
            _readable = _sentences * 2.0 + _say_chars / 3.5
            _duration = min(15.0, max(4.0, round(secs, 1), round(_readable, 1)))
            out.append({"text": _txt,
                        "seconds": _duration,
                        "over": _duration > budget + 0.01,
                        "scene_hint": cur_scene[0],
                        "beat": _arm.stage_name(cur_beat[0]),
                        "beat_index": cur_beat[0]})

    _blocks = picture_blocks(pics)
    # 模型偶尔把「殿内交手→冲到广场→飞上天际」塞在一个超长画面行里。
    # 先按可见动作句拆成小块，后面的时长器再组合；否则一段只能绑一张场景图。
    normalized = []
    for b in _blocks:
        pending = []
        for line in b:
            if _PIC_DLG.match(str(line).strip()) or str(line).startswith("──"):
                pending.append(line)
                continue
            sentences = [x for x in re.findall(r"[^。！？]+[。！？]?", str(line)) if x.strip()]
            if len(sentences) < 6:
                pending.append(line)
                continue
            if pending:
                normalized.append(pending)
                pending = []
            for i in range(0, len(sentences), 3):
                normalized.append(["".join(sentences[i:i + 3]).strip()])
        if pending:
            normalized.append(pending)
    _blocks = normalized
    # 单句长台词如果原样作为一块，会形成 20 多秒却被接口硬夹到 15 秒的残片。
    # 这里只切播放单位，不改已保存的剧本；把同一句按自然标点拆成连续的说话段。
    def _split_quote(q, cap=24):
        parts = re.findall(r"[^，。！？；,!?;]+[，。！？；,!?;]?", str(q or ""))
        out_q, cur_q = [], ""
        for part in parts:
            while len(re.sub(r"[^一-龥]", "", part)) > cap:
                cut = min(len(part), cap)
                out_q.append((cur_q + part[:cut]).strip())
                cur_q, part = "", part[cut:]
            if cur_q and len(re.sub(r"[^一-龥]", "", cur_q + part)) > cap:
                out_q.append(cur_q.strip())
                cur_q = part
            else:
                cur_q += part
        if cur_q.strip():
            out_q.append(cur_q.strip())
        return [x for x in out_q if x]

    expanded = []
    for b in _blocks:
        if _block_seconds(b) <= 15.0:
            expanded.append(b)
            continue
        dialogue, action = [], []
        for line in b:
            m = _PIC_DLG.match(str(line).strip())
            (dialogue if m else action).append((line, m))
        if not dialogue:
            expanded.append(b)
            continue
        if action:
            expanded.append([x[0] for x in action])
        for _, m in dialogue:
            for q in _split_quote(m.group(3)):
                expanded.append(["%s%s：%s" % (m.group(1), m.group(2) or "", q)])
    _blocks = expanded
    # 【情节段也是硬边界】每块画面先判属于安排四段里的哪一段（纯代码，单调），
    # 段号一变就断段——不断的话一个视频段会横跨两段情节：按 beat 算秒数对不上预算、
    # 「留到下一话」剪不干净（半段「变化」跟着「经过」出了片）、检查器数不出哪段没镜头。
    # 代价是情节边界处可能留一个短段，和地点行一样：宁可短，不能混。
    _bmap = (_arm.beat_map(["\n".join(b) for b in _blocks], arrangement)
             if arrangement else [-1] * len(_blocks))
    # 动作和台词的时长都让模型按真人节奏估（一次问完），问不到退回代码
    _acts = _act_seconds(_blocks) if use_model_seconds() else None
    _has_dlg = lambda blk: any(re.match(r"^[^\n：:]{1,12}[：:].+$", x.strip()) and not x.strip().startswith("镜头") for x in blk)
    _starts_new_place = lambda blk: bool(re.search(
        r"(?:^|[。！？]\s*)(?:殿门外|殿外|屋外|室外|门外|广场上|街道上|空中|天际|云海之上)",
        "".join(str(x or "") for x in blk)))
    for _bi, b in enumerate(_blocks):
        if _acts is not None:
            _a, _sy = _acts[_bi]
            s1 = _a + _sy
        else:
            s1 = _block_seconds(b)
        # 地点行：更新"现在在哪"，并且**强制断段**。
        # 一个视频段不可能同时在两个地方——不断的话，后面的地点会被前一段
        # 按预算吸进去，一个段横跨两个地点，绑一张场景板必然一半是错的
        # （P243 验收：四个地点切出三段，书房整个消失）。
        if len(b) == 1 and str(b[0] or "").startswith("──"):
            import re as _re
            here[0] = _re.sub(r"^──\s*|\s*──$", "", str(b[0])).split("｜")[0].strip()
            if cur:
                _flush()
                cur, secs = [], 0.0
            continue                    # 地点行本身不进段、不占时长
        _bt = _bmap[_bi] if _bi < len(_bmap) else -1
        # 一段最多一句台词：说话人能给特写、H3 少乱念（用户 2026-09-03）
        if cur and (secs + s1 > budget or len(cur) >= 3 or _starts_new_place(b)
                    or (_has_dlg(b) and any(_has_dlg(x) for x in cur))
                    or (_bt >= 0 and _bt != cur_beat[0])):
            _flush()
            cur, secs = [], 0.0
        if not cur:
            cur_scene[0] = here[0]        # 这一段是在哪儿开场的
            cur_beat[0] = _bt
        cur.append(b)
        secs += s1
    _flush()
    # 【太短的并进邻段】只做"加到超预算就断"没有下限：一块本身很长时，
    # 前面攒的短块被迫单独成段——实测出过 5.4 秒 2 块、7.7 秒 1 块的段
    # （2026-09-01 用户反馈"段太短"，说这是最严重的）。
    # 不足 floor 秒的，先试着并进后一段；后面没有了就并进前一段。
    floor = min(8.0, budget * 0.6)
    i = 0
    while len(out) > 1 and i < len(out):
        if out[i]["seconds"] >= floor:
            i += 1
            continue
        j = i + 1 if i + 1 < len(out) else i - 1
        a, b2 = (i, j) if i < j else (j, i)
        # 【别把两个地点并进一段】并了就只能绑一张场景板，另一半的背景必然是错的
        # （P243 项目153：书房那块被并进庭院段）。宁可留一个短段。
        # 地点不一样就不合——**空也算一种地点**（开场那段在第一条地点行之前，
        # hint 是空的，但它确实是另一个地方；当成"可合并"就会被并进下一个场景，
        # 开场的市场整段消失）。没有地点行的老稿子全是空串，彼此相等，合并照旧。
        _ha = str(out[a].get("scene_hint") or "")
        _hb = str(out[b2].get("scene_hint") or "")
        if _ha != _hb:
            i += 1
            continue
        # 情节段不一样也不合（理由同上：一段只能属于一段情节）
        if out[a].get("beat_index", -1) != out[b2].get("beat_index", -1):
            i += 1
            continue
        _da, _db = _pic_parts_dialogue(out[a].get("text")), _pic_parts_dialogue(out[b2].get("text"))
        if _da and _db and {x[0] for x in _da} != {x[0] for x in _db}:
            i += 1
            continue
        merged_secs = round(out[a]["seconds"] + out[b2]["seconds"], 1)
        # 合并会超 15 秒硬上限就别合——宁可留一个短段，也不能出超时段
        # （2026-09-01 实测合出了 17 秒的段，H3 生不出来）
        if merged_secs > 15.0:
            i += 1
            continue
        merged_text = out[a]["text"] + "\n\n" + out[b2]["text"]
        out[a] = {"text": merged_text, "seconds": merged_secs,
                  "over": merged_secs > budget + 0.01,
                  "scene_hint": out[a].get("scene_hint") or out[b2].get("scene_hint") or "",
                  "beat": out[a].get("beat") or "",
                  "beat_index": out[a].get("beat_index", -1)}
        del out[b2]
        i = max(0, a - 1)
    # 【按预算分时间】有安排就按每段情节的预算给切片定秒数（P260）；没安排保持模型估的
    if arrangement:
        out = _arm.apply_budget_to_slices(out, arrangement)
    return out

def scenes_from_pictures(pics, prose=""):
    """从画面稿提取场景 [{"name":…, "space":…}]。

    地点行「── 黑铁集市｜日 ──」给名字；没有地点行时，
    从正文里找最先出现的《书名号》地名，再不行就用第一幅画面里的头一个名词短语。
    空间描述取**这个地点下第一幅不含人的画面**——指令词已经要求
    "第一幅不拍人，拍这个地方"，那一幅正好就是场景参考图要的东西。
    """
    blocks = _pic_parts(pics)
    out, cur_name, cur_space = [], "", ""

    def _flush():
        if cur_name or cur_space:
            out.append({"name": cur_name or "主场景",
                        "space": cur_space[:400]})

    for k, p in blocks:
        if p.startswith("──"):
            _flush()
            cur_name = re.sub(r"^──\s*|\s*──$", "", p).split("｜")[0].strip()
            cur_space = ""
            continue
        if k or cur_space:
            continue
        cur_space = p.strip()          # 这个地点下的第一幅画面
    _flush()

    if out and out[0]["name"] == "主场景":
        # 书名号也常包报纸、杂志、书籍和档案，不能见到《…》就当地点。
        # 只接受结尾明确像地点的专名；否则保留「主场景」，让后面的场景提取器从正文识别。
        _place_title = re.compile(r"(?:旅馆|酒店|客栈|公寓|别墅|房间|客房|卧室|书房|办公室|教室|医院|学校|学院|工厂|仓库|基地|车站|码头|港口|机场|广场|街道|小巷|走廊|大厅|礼堂|宫殿|城堡|寺庙|教堂|村庄|小镇|城市|岛|山|林|谷|河|湖|海|塔|馆|楼|院|园|宫|殿|堂|厅|室|城|村|镇|街|巷|站|港|店|社)$")
        for m in re.finditer(r"《([^》]{2,20})》", str(prose or "") + pics):
            title = m.group(1).strip()
            if _place_title.search(title):
                out[0]["name"] = title
                break
    # 同名的合并
    seen, merged = set(), []
    for x in out:
        if x["name"] in seen:
            continue
        seen.add(x["name"])
        merged.append(x)
    _dbg("从画面稿取场景", {"场景": [x["name"] for x in merged]})
    return merged


def plan_places(sid, ep=None):
    """结构表的地点栏（P131）：ep 给了返回这一话的地点列表；不给返回全剧所有地点（去重）。没有结构表返回 []。"""
    from . import story_core
    try:
        rows = [r for r in (((story_core.get_story(sid) or {}).get("settings") or {}).get("plan_rows") or [])
                if isinstance(r, dict) and str(r.get("one_line") or "").strip()]
    except Exception:
        return []
    def _split(v):
        return [p.strip() for p in re.split(r"[、／/，,；;]", str(v or "")) if p.strip()]
    if ep is not None:
        return _split(rows[int(ep) - 1].get("place")) if 1 <= int(ep) <= len(rows) else []
    out = []
    for r in rows:
        for p in _split(r.get("place")):
            if p not in out:
                out.append(p)
    return out


def ensure_scenes(sid, ep=1):
    """这一话的场景卡。结构表有「地点」就按地点建/复用（P131，场景是上游定的，不从剧本抠）；
    没有结构地点才退回老路：从画面稿建。返回这一话的场景卡列表。"""
    from . import asset_core
    _places = plan_places(sid, ep)
    if _places:
        have = asset_core.list_assets(sid, "scenes") or []
        out = []
        for p in _places:
            card = next((c for c in have if str(c.get("name") or "").strip() == p), None)
            if card is None:      # 卡名是地点的一部分（卡「酒馆」，地点「镇上酒馆」）也算同一个景
                card = next((c for c in have if str(c.get("name") or "").strip() and str(c.get("name")).strip() in p), None)
            if card is None:
                card = asset_core.create_scene(sid, {"name": p, "space": "", "contract_text": ""})
                if card:
                    card["episode"] = int(ep or 1)
                    card["auto_made"] = True
                    asset_core.save_asset(sid, "scenes", card)
                    _dbg("按结构表建场景卡", {"话": ep, "名": p})
                have = asset_core.list_assets(sid, "scenes") or []
            if card and card.get("scene_id") not in [c.get("scene_id") for c in out]:
                out.append(card)
        # P316b：出片前门里用户点「新建场景卡」建的卡（user_made，记了话号）也是这一话的景——不在结构地点里也要带上出图
        for c in have:
            if c.get("user_made") and int(c.get("episode") or 0) == int(ep or 1) and c.get("scene_id") not in [x.get("scene_id") for x in out]:
                out.append(c)
        # 自动抠出来的碎片卡（没设计稿、没图、用户没改、不在任何一话的地点里）删掉——118 第 2 话曾抠出「酒馆左侧角落」「酒馆中央过道及人群」
        try:
            _all_places = plan_places(sid)
            adopted = {v.get("owner_id") for v in (asset_core.list_assets(sid, "visuals") or []) if v.get("status") == "adopted"}
            from . import story_core as _sc0
            st0 = _sc0.get_story(sid) or {}
            for c in asset_core.list_assets(sid, "scenes") or []:
                nm = str(c.get("name") or "").strip()
                if (not str(c.get("design") or "").strip() and c.get("scene_id") not in adopted and not c.get("edited_by_user")
                        and nm and not any(nm == p or nm in p for p in _all_places)):
                    asset_core.delete_asset(sid, "scenes", c["scene_id"])
                    if c["scene_id"] in (st0.get("scene_ids") or []):
                        st0["scene_ids"].remove(c["scene_id"]); _sc0.save_story(st0)
                    _dbg("删掉不在结构地点里的碎片场景卡", {"名": nm})
        except Exception as ex:
            _dbg("清碎片场景卡失败", {"错": str(ex)[:80]})
        return out
    have = asset_core.list_assets(sid, "scenes") or []
    if have and int(ep or 1) > 1:
        # P445①：续话——这一话正文里对不上现有卡的新地点要建卡（第二话的森林曾被绑到「沙滩」）
        try:
            _new = new_places_for_episode(sid, int(ep or 1))
            if _new:
                _dbg("续话新地点建卡", {"话": ep, "新": _new})
        except Exception as _nx:
            _dbg("续话抠新地点失败", {"错": str(_nx)[:100]})
        return asset_core.list_assets(sid, "scenes") or []
    if have:
        return have
    _saga, e = _ep(sid, ep)
    pics = str((e or {}).get("pictures") or "")
    prose = str((e or {}).get("prose") or "")
    got = scenes_from_pictures(pics, prose)
    # 画面稿里没有地点行时，scenes_from_pictures 只能给出"主场景"这个默认名。
    # 这时改用提取器从正文认——它能认出「柏油路上」「草丛里」这类真地名
    # （2026-09-01 用户实测：乡村项目的场景卡名叫"主场景"）。
    if not got or all(x.get("name") == "主场景" for x in got):
        _raw = [(k, n, f) for k, n, f in _extract_cards_raw(prose) if k == "场景"]
        if _raw:
            got = [{"name": n,
                    "space": f.get("空间") or (got[0]["space"] if got else ""),
                    "light": f.get("光线", ""),
                    "ground": f.get("地面墙面", ""),
                    "furniture": f.get("陈设", ""),
                    "landmarks": f.get("标志物", "")}
                   for n, f in [(n, f) for _k, n, f in _raw]]
    for sc in got:
        try:
            asset_core.create_scene(sid, {
                "name": sc["name"], "space": sc.get("space", ""),
                "light": sc.get("light", ""), "ground": sc.get("ground", ""),
                "furniture": sc.get("furniture", ""),
                "landmarks": sc.get("landmarks", ""),
                "contract_text": sc.get("space", "")})
        except Exception as ex:
            _dbg("建场景卡失败", {"名": sc["name"], "错": str(ex)[:80]})
    return asset_core.list_assets(sid, "scenes") or []


def _place_match(name, have):
    """新地点名对现有卡：同名 / 互相包含 / 同类（岸/水/山林…）且有一张同类卡就算同一个景。返回卡或 None。"""
    from . import oral_story as _os_
    nm = str(name or "").strip()
    if not nm:
        return None
    for c in have:
        cn = str(c.get("name") or "").strip()
        if cn and (cn == nm or cn in nm or nm in cn):
            return c
    _core = re.sub(r"[旁边上里内外中前后处口]$", "", re.sub(r"[·\-—（(].*$", "", nm))
    _grams = {_core[i:i + 2] for i in range(len(_core) - 1)} if len(_core) >= 2 else set()
    for c in have:                                                          # 「礁石旁」↔「礁石浅滩」：共一个两字词
        cn = re.sub(r"[·\-—（(].*$", "", str(c.get("name") or "").strip())
        if any(g in cn for g in _grams):
            return c
    cls = _os_.place_class(nm)
    if cls:
        same = [c for c in have if _os_.place_class(str(c.get("name") or "")) == cls]
        if same:
            return same[0]
    return None


def new_places_for_episode(sid, ep):
    """P445①：第 ep 话正文/画面稿里的地点 → 对不上现有卡的建新卡（带话号、auto_made）。返回新建的名字。"""
    from . import asset_core
    _saga, e = _ep(sid, ep, create=False)
    pics = str((e or {}).get("pictures") or "")
    prose = str((e or {}).get("prose") or "")
    if not (pics.strip() or prose.strip()):
        return []
    got = scenes_from_pictures(pics, prose) or []
    if not got or all(x.get("name") == "主场景" for x in got):
        _raw = [(k, n, f) for k, n, f in _extract_cards_raw(prose) if k == "场景"]
        got = [{"name": n, "space": f.get("空间", ""), "light": f.get("光线", ""), "ground": f.get("地面墙面", ""),
                "furniture": f.get("陈设", ""), "landmarks": f.get("标志物", "")} for _k, n, f in _raw]
    have = asset_core.list_assets(sid, "scenes") or []
    made = []
    for sc in got:
        nm = str(sc.get("name") or "").strip()
        if not nm or nm == "主场景" or _place_match(nm, have):
            continue
        try:
            card = asset_core.create_scene(sid, {"name": nm, "space": sc.get("space", ""), "light": sc.get("light", ""), "ground": sc.get("ground", ""),
                                                 "furniture": sc.get("furniture", ""), "landmarks": sc.get("landmarks", ""), "contract_text": sc.get("space", "")})
            if card:
                card["episode"] = int(ep)
                card["auto_made"] = True
                asset_core.save_asset(sid, "scenes", card)
                made.append(nm)
                have = asset_core.list_assets(sid, "scenes") or []
        except Exception as ex:
            _dbg("续话建场景卡失败", {"名": nm, "错": str(ex)[:80]})
    return made


# ---------- 场景设计稿（美术指导那一步，2026-09-06 接入） ----------
# 【为什么】程序从画面稿机械抠出来的场景卡是碎片（"教堂""教堂内部""祭坛旁"），
# 每张按 60 字空间描述出图，全是闭塞小房间。真实剧组在出图前有美术指导：
# 把碎片归并成"景"，按这场戏的意图定镇场主体、戏区方位、光、尺度，
# 再压成一句话空间给出图。实验十四～十六（项目 97 + 教室/宫殿/森林/草原/修仙大殿）
# 实测：同一模型同一 seed，设计稿出的图明显更大更干净。一话只调一次 Qwen。
DESIGN_HEADS = ['【景】', '【归并自】', '【这里发生的戏】', '【意图】', '【镇场主体】', '【固有陈设】', '【戏区】',
                '【人物痕迹】', '【光】', '【纵深与尺度】', '【状态】', '【一句话空间】']
_D_INDOOR = re.compile(r"殿|厅|室|房|堂|舱|廊|教室|地牢|牢房|内部|洞内|书斋|阁")
_D_OUTDOOR = re.compile(r"洞|山|崖|野|海|荒|城墙|街|码头|外|原|林|坡|谷")


_ECHO_NAME = re.compile(r"用剧本里的叫法|^名字[（(]|^填写|^示例")
_ECHO_SPACE = re.compile(r"把上面所有决定|给出图用|【固有陈设】里的每一样|这一段里\*\*不写人")


def _design_blocks(text):
    """设计稿按【景】切块。P333c：模型把模板占位符抄回来的块（景名「名字（用剧本里的叫法）」、
    空间描述是指令原文）在这里就丢掉——验收和解析看同一份块表。"""
    out = []
    for b in re.split(r"(?=【景】)", str(text or "")):
        if not b.strip().startswith("【景】"):
            continue
        nm = (_design_field(b, "【景】").splitlines() or [""])[0]
        if _ECHO_NAME.search(nm) or _ECHO_SPACE.search(_design_field(b, "【一句话空间】") or ""):
            _dbg("设计稿里的模板回声块，跳过", {"名": nm[:30]})
            continue
        out.append(b)
    return out


def _design_field(block, head):
    # P333b：模型有时把指令模板（════ 分隔线起头）抄在最后一块后面——字段到那里为止
    m = re.search(re.escape(head) + r"[：:]?[ \t]*\n?(.*?)(?=\n【|\n[ \t]*═{3,}|\Z)", block, re.S)
    return (m.group(1) if m else "").strip()


def design_problems(text, names=None):
    """设计稿验收：标题齐、戏区≥2 行、一句话空间≤170 字、参照物合规、尺度和意图一致、室外不写层高、
    现有场景名单每个名字都被覆盖（并进某景或自己成景）。报错文案会原样喂回模型，只说要什么。"""
    probs = []
    blocks = _design_blocks(text)
    if not blocks:
        return ["一份设计稿都没有：每个景以「【景】名字」开头"]
    # 名单覆盖：项目 117 实测"雪地/空地"没被归并也没成景，出图只能走老路（2026-09-06）
    covered = set()
    for d in parse_design(text):
        covered.add(d["name"])
        covered.update(d["merged"])
    miss = [n for n in (names or []) if n and n not in covered]
    if miss:
        probs.append("现有场景名单里的「%s」没有归入任何景也没有单独设计：要么写进某个景的【归并自】，"
                     "要么给它单独一份设计稿（戏发生在那里就必须有景）" % "、".join(miss))
    for b in blocks:
        name = _design_field(b, "【景】").splitlines()[0][:20] if _design_field(b, "【景】") else "？"
        miss = [h for h in DESIGN_HEADS if h not in b]
        if miss:
            probs.append("「%s」缺标题：%s" % (name, "、".join(miss)))
        rows = [l for l in _design_field(b, "【戏区】").splitlines() if l.strip()]
        if len(rows) < 2:
            probs.append("「%s」的【戏区】要两到四行，每行：戏区名｜哪场戏｜相对镇场主体的方向和距离｜地面周围" % name)
        sp = _design_field(b, "【一句话空间】")
        if len(sp) > 170:
            probs.append("「%s」的【一句话空间】超了（%d 字），压到 120 字以内" % (name, len(sp)))
        dz = _design_field(b, "【纵深与尺度】")
        # P350：室内外先由地名定——草地/山谷/岔路口/庭院这类自然或露天地名写成"室内"就是错（197 草地写成 8×6 米室内石屋）
        _named_in = bool(re.search(r"殿|厅|室内|房|堂|舱|廊|教室|地牢|牢房|内部|洞内|书斋|阁|馆|宅|铺|店", name))
        _nat = (not _named_in) and bool(_D_OUTDOOR.search(name) or re.search(r"草地|草坡|旷野|荒野|山谷|谷|路口|岔路|大路|官道|营地|河|湖|海|林|森林|树下|庭院|广场|山顶|悬崖|桥|码头|田|村口", name))
        _says_out = bool(re.match(r"^\s*室外", sp) or re.match(r"^\s*室外", dz))
        indoor = (not _nat) and (not _says_out) and bool(_D_INDOOR.search(name + sp[:40])) and not re.search(r"露天|户外|外墙|外景", sp[:40])
        if _nat and re.search(r"室内|封闭空间|层高|面宽|进深", sp + dz[:80]):
            probs.append("「%s」是室外的自然/露天地方，【一句话空间】和【纵深与尺度】写成室外：远景是什么、中景主体是什么、前景是什么、覆盖范围几十到几百米，去掉室内/层高/面宽/进深" % name)
        if indoor:
            if not re.search(r"尽头|远端|最远|门洞|高窗|柱列|屋架|横梁", dz + sp):
                probs.append("「%s」是室内，【纵深与尺度】写出尽头和顶：最远那道门洞/高窗，露出的屋架横梁或柱列往远处缩小" % name)
        elif not re.search(r"远景|远处|远山|天际|地平线|雾|尽头", dz + sp):
            probs.append("「%s」是室外，【纵深与尺度】写远景（远山/河谷/城堡轮廓/天际）、中景主体、前景低矮物各一句" % name)
        if not indoor and re.search(r"层高|面宽|进深", sp) and _D_OUTDOOR.search(name + sp[:30]):
            probs.append("「%s」是室外，【一句话空间】的尺度按几十米几百米写，去掉层高面宽进深" % name)
    return probs


def parse_design(text):
    """设计稿 → [{name, merged, plays, intent, landmarks, layout, traces, light, depth, states, space, design}]"""
    out = []
    for b in _design_blocks(text):
        name = _design_field(b, "【景】").splitlines()[0].strip() if _design_field(b, "【景】") else ""
        name = re.sub(r"^[：:\s]+", "", name).strip()
        if not name:
            continue
        merged_raw = _design_field(b, "【归并自】")
        merged = [x.strip() for x in re.split(r"[、，,；;\n]", merged_raw) if x.strip() and x.strip() not in ("无", "（无）", "(无)")]
        # P160①：设计稿里的人在这一步就删掉——卡上一旦存进去，出图那边补尺度时会把它原样注入，
        # 绕过所有过滤（实测「第三排正中央坐着老对手」就是这么进的底图）。
        try:
            from .asset_core import strip_person_clauses as _spc
        except Exception:
            _spc = lambda x, names=(): x
        _space = _spc(_design_field(b, "【一句话空间】"))
        _light = _spc(_design_field(b, "【光】"))
        _depth = _spc(_design_field(b, "【纵深与尺度】"))
        _fixt = _design_field(b, "【固有陈设】")
        out.append({
            "name": name, "merged": merged,
            "plays": _design_field(b, "【这里发生的戏】"), "intent": _design_field(b, "【意图】"),
            "landmarks": _spc(_design_field(b, "【镇场主体】")), "layout": _design_field(b, "【戏区】"),
            "fixtures": _fixt,
            "traces": _design_field(b, "【人物痕迹】"), "light": _light,
            "depth": _depth, "states": _design_field(b, "【状态】"),
            "space": _space, "design": b.strip(),
        })
    return out


def _design_fields(d):
    return {"space": d["space"], "contract_text": d["space"], "layout": d["layout"],
            "light": d["light"], "landmarks": d["landmarks"], "furniture": d["traces"],
            "fixtures": d.get("fixtures") or "",
            "depth": d["depth"], "intent": d["intent"], "states": d["states"],
            "plays": d["plays"], "design": d["design"], "merged_from": d["merged"],
            "designed": time.time()}


def apply_design(sid, ep, text):
    """把设计稿落到场景卡：同名卡就地更新（保留图和 id）；被归并的碎片卡——
    没图且用户没改过的删掉，有图或改过的留着并标 merged_into。返回这一话的景卡列表。"""
    from . import asset_core, story_core
    designs = parse_design(text)
    if not designs:
        raise RuntimeError("设计稿里没有「【景】」块")
    cards = asset_core.list_assets(sid, "scenes") or []
    by_name = {str(c.get("name") or "").strip(): c for c in cards}
    adopted = {v.get("owner_id") for v in (asset_core.list_assets(sid, "visuals") or [])
               if v.get("status") == "adopted"}
    used, out = set(), []
    _design_card = {}                       # 设计稿【景】名 → 实际落到的卡名（P316b：归并删卡后要把绑定改到这个名字上）
    # P419：归并只许并同类地点——【归并自】里和主景不同类的（海上 vs 沙滩）拆出来单独建卡
    from . import oral_story as _os_
    try:
        _saga0, _e0 = _ep(sid, int(ep or 1))
        _pics0 = str((_e0 or {}).get("body") or (_e0 or {}).get("pictures") or "")
    except Exception:
        _pics0 = ""
    for d in designs:
        _f0 = _design_fields(d)
        _split = _os_.split_merged_by_class(d["name"], _f0.get("space") or "", d.get("merged") or [])
        for _nm, _als in _split:
            d["merged"] = [m for m in d["merged"] if m != _nm and m not in _als]
            if _nm in by_name and by_name[_nm]["scene_id"] not in used:
                _c = by_name[_nm]
            else:
                _c = asset_core.create_scene(sid, {"name": _nm, "space": "", "contract_text": ""})
                by_name[_nm] = _c
            if not str(_c.get("space") or "").strip() or asset_core._space_is_prose(_c.get("space")):
                _c["space"] = _write_place_space(_nm, _pics0, [d["name"]] + list(by_name.keys()))
                _c["contract_text"] = _c["space"]
            _c["aliases"] = sorted(set([str(x) for x in (_c.get("aliases") or [])] + _als))
            _c["user_made"] = True                                          # 不再被归并删掉、不触发重做设计稿
            _c["episode"] = int(ep or 1)
            _c["updated"] = time.time()
            asset_core.save_asset(sid, "scenes", _c)
            used.add(_c["scene_id"])
            out.append(_c)
            _dbg("归并拆类", {"主景": d["name"], "拆出": _nm, "别名": _als})
    for d in designs:
        card = by_name.get(d["name"])
        if card is None:
            # 名单里有现成卡的，第一张顶上（它的图跟着过来）
            for m in d["merged"]:
                if m in by_name and by_name[m]["scene_id"] not in used:
                    card = by_name[m]
                    break
        f = _design_fields(d)
        _fresh = card is None
        if _fresh:
            card = asset_core.create_scene(sid, dict(f, name=d["name"]))
        card.update(f)
        if _fresh:
            card["name"] = d["name"]
        else:
            # 【已有的卡不改名】P274：卡名是下游找参考图的键（分镜段的 scene、new_pack 按名字比）。
            # 美术指导把「面包店内」「店门口」归并成【景】面包店，一改名，那几段的场景图就静默丢了。
            # 设计稿起的名字只作标题存着，并记进别名，按设计稿名字找也找得到。
            _dn = str(d.get("name") or "").strip()
            _cn = str(card.get("name") or "").strip()
            if _dn and _dn != _cn:
                card["design_title"] = _dn
                _al = [str(x).strip() for x in (card.get("aliases") or []) if str(x).strip()]
                if _dn not in _al:
                    _al.append(_dn)
                card["aliases"] = _al
        if not card.get("episode"):
            card["episode"] = int(ep or 1)
        card["updated"] = time.time()
        asset_core.save_asset(sid, "scenes", card)
        used.add(card["scene_id"])
        out.append(card)
        _design_card[d["name"]] = str(card.get("name") or d["name"])
    st = story_core.get_story(sid) or {}
    _renames = {}
    for d in designs:
        for m in d["merged"]:
            c = by_name.get(m)
            if not c or c["scene_id"] in used:
                continue
            if c["scene_id"] in adopted or c.get("edited_by_user"):
                c["merged_into"] = d["name"]
                asset_core.save_asset(sid, "scenes", c)
            else:
                asset_core.delete_asset(sid, "scenes", c["scene_id"])
                if st and c["scene_id"] in (st.get("scene_ids") or []):
                    st["scene_ids"].remove(c["scene_id"])
                    story_core.save_story(st)
                _renames[str(c.get("name") or m)] = _design_card.get(d["name"], d["name"])
                _dbg("归并删碎片场景卡", {"删": m, "并入": d["name"]})
    if _renames:
        remap_scene_bindings(sid, ep, _renames)          # P316b：清单/分镜段/开场段里绑的旧名一起改
    return out


def fix_scene_spaces(sid, ep=1, on_step=None):
    """P421：出图前把「空间」无效的场景卡（抄正文 / 碎片「远处有树林」/ 空）交给模型按剧本重写。返回改了的卡名。"""
    from . import asset_core
    try:
        _saga0, _e0 = _ep(sid, int(ep or 1))
        pics = str((_e0 or {}).get("body") or (_e0 or {}).get("pictures") or (_e0 or {}).get("prose") or "")
    except Exception:
        pics = ""
    cards = asset_core.list_assets(sid, "scenes") or []
    names = [str(c.get("name") or "") for c in cards]
    fixed = []
    for c in cards:
        if c.get("edited_by_user"):
            continue
        if str(c.get("space") or "").strip() and not asset_core._space_is_prose(c.get("space")):
            continue
        if on_step:
            on_step("补写场景空间描述：" + str(c.get("name") or ""))
        c["space"] = _write_place_space(str(c.get("name") or ""), pics, names,
                                        other_spaces=[str(x.get("space") or "") for x in cards if x is not c and str(x.get("space") or "").strip()])
        c["contract_text"] = c["space"]
        c["updated"] = time.time()
        asset_core.save_asset(sid, "scenes", c)
        fixed.append(str(c.get("name") or ""))
    return fixed


_OUTDOOR_NAME = re.compile(r"森林|树林|林|山|岭|坡|谷|崖|野|郊|荒|村|街|路|巷|滩|岸|海|河|湖|溪|江|潭|广场|院|园|桥|码头|渡口|门口|洞口|营地|田|草地|空地|山道|官道")
_INDOOR_NAME = re.compile(r"屋|房|室|殿|厅|阁|楼|寺|庙|洞|帐篷|舱|车厢|地窖|牢|店|铺|馆|堂|内|里")
_ROOM_ONLY_WORDS = re.compile(r"床|横梁|天花板|地板|床单|木箱")          # 室外地名的描述里不该有的东西（别和下面的 _INDOOR_WORDS 混）
_SPACE_FALLBACK = {
    "山林": "室外，远处是层层叠叠的树冠和薄雾，中景是几株粗大的树干和垂落的藤蔓，前景是腐叶和苔藓覆盖的地面，光线从树冠缝隙斜射下来，色调青绿偏暗，材质为树皮、苔藓、湿土。",
    "路": "室外，远处是连成一片的屋舍和树影，中景是一条踩实的土路和路边的屋檐，前景是路面上的碎石和车辙，天光从侧上方照下来，色调土黄偏暖，材质为泥土、原木、碎石。",
    "岸": "室外，远处是海面和天际线，中景是一段开阔的沙地和几块礁石，前景是被浪打湿的沙面，阳光从侧面照来，色调暖黄和青蓝，材质为细沙、礁石、海水。",
    "水": "室外，远处是开阔的水面和远岸，中景是起伏的浪或水流，前景是近处的水面反光，天光从上方照下来，色调青蓝，材质为水、浪花、雾气。",
    "院": "室外，远处是围墙和屋顶，中景是一片开阔的平地和几件固定的陈设，前景是地面的石板或泥土，天光从上方照下来，色调土黄偏暖，材质为石板、泥土、木头。",
    "屋": "室内，远处是墙面和一扇门，中景是屋里的主要陈设，前景是地面和近处的桌角，光线从窗或门缝斜射进来，色调暖褐，材质为木头、布、泥墙。",
    "棚": "室内，远处是用木条和茅草搭的棚壁，中景是几件简单的家什，前景是压实的泥地，光线从缝隙漏进来，色调昏黄，材质为木条、茅草、泥土。",
}


def _bigram_overlap(a, b):
    def bg(t):
        t = re.sub(r"[^\u4e00-\u9fa5]", "", str(t or ""))
        return {t[i:i + 2] for i in range(len(t) - 1)}
    x, y = bg(a), bg(b)
    return (len(x & y) / len(x)) if x else 0.0


def _place_paragraphs(name, pics):
    """只留提到这个地方的段落（整名、名字的两字片段、同类词），没有就用开头一段。"""
    from . import oral_story as _os_
    nm = re.sub(r"[·\-—（(].*$", "", str(name or "")).strip()
    keys = {nm} | {nm[i:i + 2] for i in range(len(nm) - 1) if not re.search(r"[的之处里内外上下]", nm[i:i + 2])}
    cls = _os_.place_class(nm)
    rx = next((r for c, r in _os_._PLACE_CLASSES if c == cls), "")
    paras = [p.strip() for p in re.split(r"\n\s*\n", str(pics or "")) if p.strip()]
    hit = [p for p in paras if any(k and k in p for k in keys)] or ([p for p in paras if rx and re.search(rx, p)] if rx else [])
    txt = "\n\n".join(hit)[:2500]
    return txt or (paras[0][:800] if paras else "")


def _write_place_space(name, pics, others, other_spaces=None):
    """P419：让模型只写一个地方的空间描述（一段，室内/室外开头，远中前景、陈设、光、材质，不写人不写动作）。
    P454⑪：只喂提到这个地方的段落；室内外按地名定；写成别的卡的样子 / 室内外反了 → 重来一次 → 按类别兜底。"""
    from . import oral_story as _os_
    nm = str(name or "")
    _tail = re.sub(r"[·\-—（(].*$", "", nm).strip()[-2:]
    if _OUTDOOR_NAME.search(_tail):                                            # 「木屋村落」按尾词算室外
        outdoor, indoor = True, False
    elif _INDOOR_NAME.search(_tail):
        outdoor, indoor = False, True
    else:
        outdoor = bool(_OUTDOOR_NAME.search(nm)) and not _INDOOR_NAME.search(nm)
        indoor = bool(_INDOOR_NAME.search(nm)) and not outdoor
    head = "室外，" if outdoor else ("室内，" if indoor else "")
    sysm = ("你是美术指导。只按剧本写下面这个地方的空间描述：一段话，必须以「室内，」或「室外，」开头，依次写远景、中景、前景，"
            "固有陈设（三到五件）、光线方向和颜色、材质。不写人物、不写动作、不写台词、不写别的地方。只输出这一段。")
    if head:
        sysm += "这个地方是%s，第一个词就写「%s」，写的是%s。" % ("室外" if outdoor else "室内", head.strip("，"), "天、地、树、路这类室外的东西" if outdoor else "屋里的墙、门窗、陈设")
    src = _place_paragraphs(nm, pics)
    user = "【地点】%s\n【其它已有的地方（不要写它们）】%s\n\n【剧本里写到这个地方的段落】\n%s" % (nm, "、".join(x for x in others if x and x != nm)[:200], src)

    def _bad(t):
        if not t:
            return "空"
        if outdoor and (t.startswith("室内") or _ROOM_ONLY_WORDS.search(t)):
            return "室外的地方写成了室内"
        if indoor and t.startswith("室外"):
            return "室内的地方写成了室外"
        for o in (other_spaces or []):
            if o and _bigram_overlap(t, o) >= 0.6:
                return "和别的卡写成一样"
        return ""

    t, why = "", ""
    for temp in (0.4, 0.7):
        try:
            t = str(_q(sysm, user, mt=600, temperature=temp) or "").strip().split("\n")[0].strip()
        except Exception:
            t = ""
        t = re.sub(r"^[【\[]?(空间|场景)[】\]]?[：:]\s*", "", t)
        why = _bad(t)
        if not why:
            break
        _dbg("场景空间描述重写", {"地点": nm, "问题": why, "写的": t[:80]})
    if why:
        cls = _os_.place_class(nm)
        if outdoor and cls not in ("山林", "路", "岸", "水", "院"):
            cls = "路" if re.search(r"村|街|路|巷|镇|市|集", nm) else ("山林" if re.search(r"林|山|野|谷|坡", nm) else "院")
        elif indoor and cls not in ("屋", "棚"):
            cls = "屋"
        t = _SPACE_FALLBACK.get(cls) or _SPACE_FALLBACK["山林" if outdoor else "屋"]
        _dbg("场景空间描述用兜底", {"地点": nm, "类别": cls})
    if not re.match(r"^(室内|室外)", t):
        t = (head or ("室内，" if re.search(r"屋|房|室|殿|厅|洞|帐篷里", nm) else "室外，")) + t.lstrip("，, ")
    return t[:400]


def remap_scene_bindings(sid, ep, renames):
    """P316b：场景卡被归并删掉后，把第 ep 话拍摄清单场戏的 card、分镜段的 scene、开场段的 scene/name 改到并入的卡名。返回改了几处。"""
    from . import saga_core as _sc
    if not renames:
        return 0
    n = 0
    try:
        _saga, e = _ep(sid, int(ep or 1))
        sl = e.get("shotlist") if isinstance((e or {}).get("shotlist"), dict) else None
        if sl:
            for s in (sl.get("scenes") or []):
                if isinstance(s, dict) and str(s.get("card") or "") in renames:
                    s["card"] = renames[str(s.get("card"))]
                    n += 1
            if n:
                _update_ep(sid, ep, shotlist=sl)
        tl = _sc.ep_timeline(sid, ep) or {"scenes": []}
        m = 0
        for sc0 in (tl.get("scenes") or []):
            for g in (sc0.get("segments") or []):
                if str(g.get("scene") or "") in renames:
                    g["scene"] = renames[str(g.get("scene"))]
                    m += 1
        for og in (tl.get("opening") or []):
            if str(og.get("scene") or "") in renames:
                og["scene"] = renames[str(og.get("scene"))]
                m += 1
            if og.get("kind") == "scene" and str(og.get("name") or "") in renames:
                og["name"] = renames[str(og.get("name"))]
                m += 1
        if m:
            _sc.save_ep_timeline(sid, ep, tl)
            n += m
        if n:
            _dbg("归并后改绑", {"改名": renames, "处": n})
    except Exception as ex:
        _dbg("归并后改绑失败", {"err": str(ex)[:120]})
    return n


def apply_design_block(sid, card, block):
    """用户在界面上改了一张卡的设计稿：重新解析这一块，字段跟着换。"""
    from . import asset_core
    ds = parse_design(block)
    if not ds:
        card["design"] = str(block or "").strip()
        return card
    d = ds[0]
    card.update(_design_fields(d))
    if d["name"]:
        card["name"] = d["name"]
    return card


def design_scenes(sid, ep=1, force=False):
    """美术指导：这一话的碎片场景卡 → 归并成景 + 设计稿（一次 Qwen，最多三轮定向修）。
    卡都已有设计稿且不 force 就直接返回。"""
    from . import asset_core, story_core
    cards = ensure_scenes(sid, ep)
    if not force and cards and all(str(c.get("design") or "").strip() for c in cards):
        return cards
    st = story_core.get_story(sid) or {}
    settings = dict(st.get("settings") or {})
    _saga, e = _ep(sid, ep)
    # P148：老路（正文→剧本）产物在 body；pictures 是 v2 时代的残留，读它会让设计稿按旧剧本写戏区
    pics = str((e or {}).get("body") or (e or {}).get("pictures") or "")
    if len(pics.strip()) < 50:
        raise RuntimeError("第 %d 话还没有画面稿，先生成剧本" % int(ep or 1))
    chars = [str(c.get("name") or "") for c in (asset_core.list_assets(sid, "characters") or []) if c.get("name")]
    # 只设计还没设计稿的景（P131）：已定的景告诉美术指导，不重做、不重复
    todo = cards if force else [c for c in cards if not str(c.get("design") or "").strip()]
    names = [str(c.get("name") or "") for c in todo if c.get("name")]
    done_names = [str(c.get("name") or "") for c in cards if c not in todo and c.get("name")]
    ins = _ins("场景设计稿_指令词.txt")
    user = ("【一句话故事】%s\n【世界】%s　【画风】%s\n【人物】%s\n【现有场景名单】%s%s\n\n【第 %d 话剧本】\n%s" % (
        st.get("one_line") or "", settings.get("world_type") or "", settings.get("style") or "",
        "、".join(chars) or "（无）", "、".join(names) or "（无）",
        ("\n【已经设计好的景（不要再设计，戏区已定）】" + "、".join(done_names)) if done_names else "", int(ep or 1), pics[:9000]))
    out, probs = "", []
    for k in range(3):
        out = _q(ins, user, mt=3500, temperature=0.5)
        probs = design_problems(out, names)
        _dbg("场景设计稿", {"轮": k + 1, "字": len(out), "景": out.count("【景】"), "问题": probs[:3]})
        if not probs:
            break
        user += "\n\n★★【上一版不合格，只改下面这些，其它原样保留】" + "；".join(probs)
    if not _design_blocks(out):
        raise RuntimeError("美术指导没给出设计稿")
    _update_ep(sid, ep, scene_design=out)
    return apply_design(sid, ep, out)

_STATE_RE = re.compile(r"脱下|脱掉|脱去|换上|穿上|披上|裹着|睡袍|睡衣|内衣|浴巾|赤裸|全裸|裸着|裸体"
                       r"|洗澡|沐浴|入浴|泡在|起床|更衣|解开.{0,6}(衣|裙|扣|带)|衣衫不整|没穿")


def _carry_state(line):
    """「开场穿…；之后全裸」→ 取结束时那半句「全裸」（P318b）。没有分号/之后就整句。"""
    t = str(line or "").strip().rstrip("。")
    parts = [x.strip() for x in re.split(r"[；;]", t) if x.strip()]
    last = parts[-1] if parts else t
    last = re.sub(r"^(之后|然后|随后|后来|最后|接着)[，,：:]?", "", last).strip()
    return last[:80] or t[:80]


def _state_line(name, shots):
    """这个人本段穿什么——只在代码查到换衣/脱衣/裸/浴这类词时才问，问一句话。

    用户 2026-09-02：洗澡不能还穿着衣服；代码挑问题，模型一句话回答，别让它多做。
    """
    try:
        rep = _q("只回答一句话，不解释。用户给你一段画面稿和一个人名，"
                 "你说这个人在这一段里**身上穿什么、有没有变化**。"
                 "格式：「开场穿…；之后…」。全程没变就只回「不变」。",
                 "【人名】%s\n【画面稿】\n%s" % (name, str(shots or "")[:2500]),
                 mt=120, temperature=0.1)
    except Exception:
        return ""
    rep = str(rep or "").strip().splitlines()[0].strip() if rep else ""
    if not rep or "不变" in rep[:4]:
        return ""
    return rep[:120]


def _pov_spec(settings):
    """视点在套装表里的规则包（camera/must/look_at_lens/movement…）。没有就空。"""
    try:
        from . import kits as _k
        name = str((settings or {}).get("pov") or "").strip()
        if not name or name == "自动":
            return name, {}
        return name, (_k.spec("pov", name) or {})
    except Exception:
        return "", {}


def _h3_camera_line(settings, lead=""):
    """摄影行里的机位句。视点从套装表取；电影第三人称/没选就是默认那句。"""
    name, sp = _pov_spec(settings)
    if not sp or name == "电影第三人称":
        return "第三人称摄影，景别、角度、运动和可见范围按本段【机位】执行。"
    bits = []
    for k in ("camera", "must", "movement"):
        v = str(sp.get(k) or "").strip().rstrip("。")
        if v:
            bits.append(v)
    lal = str(sp.get("look_at_lens") or "").strip().rstrip("。")
    if lal and not lal.startswith("禁止"):
        bits.append(lal)
    txt = "；".join(bits)
    if lead:
        txt = txt.replace("主角", lead)
    return "视点·%s：%s。" % (name, txt)


def _h3_pov_rules(settings, lead=""):
    """写镜头行时的视点规矩（只给第一视角/自拍这类改变'谁在拍'的视点）。"""
    name, sp = _pov_spec(settings)
    who = lead or "主角"
    if name == "第一视角POV":
        return ("\n\n【视点：第一视角】镜头就是%s的眼睛。每个镜头行写**%s看到什么**：别人的位置用"
                "「在我左手边／正前方／越来越近」这种说法；%s本人不出现在画面里，最多出现他自己的手、"
                "袖口、手里的东西；和%s说话的人看着镜头说。「谁 + 景别」写的是**被看的那个人**的景别。\n"
                "**每一个镜头都要带一样第一视角的证据**（两样至少一样）：\n"
                "  ① 对方的视线：写「她看向镜头」「她看着我说」「她的眼睛对上镜头」；\n"
                "  ② 我的身体入画：写「我的右手从画面下沿伸进来…」「我的手臂搭在桌沿」。\n"
                "机位就是我坐着/站着时眼睛的高度，跟着我的头轻微动，对方走近时脸在画面里越来越大。"
                % (who, who, who, who))
    if name == "自拍手持":
        return ("\n\n【视点：自拍】%s自己举着相机拍自己，一臂距离、略高角度俯拍。每个镜头行里%s的脸占画面很大、"
                "看着镜头说话，另一个人要入镜就是凑到%s身边一起对着镜头；背景在身后、是次要的。"
                % (who, who, who))
    if name == "监控偷窥":
        return ("\n\n【视点：监控】整段是一台固定在高处角落的摄像头，高位俯角、机位不动、广角畸变；"
                "人物可以走出画面再走回来；「谁 + 景别」都写成全景或远景。")
    if name == "纪录片跟拍":
        return ("\n\n【视点：跟拍】摄影师在现场肩扛跟拍，被拍的人知道镜头在；镜头跟着人走、轻微晃动，"
                "偶尔有人瞥一眼镜头。")
    return ""


def _h3_cine(settings, scene_space, lead=""):
    """【摄影】句：镜头/色调按画风查表，光从场景卡的空间描写里抠出来，机位按视点。全是代码拼的。"""
    from . import style_presets as _sp
    style = str((settings or {}).get("style") or "")
    look = ""
    for k, v in (getattr(_sp, "H3_LOOK", None) or {}).items():
        if k and k in style:
            look = v
            break
    if not look:
        look = (getattr(_sp, "H3_LOOK", None) or {}).get("默认", "")
    if ((settings or {}).get("_beat") or {}).get("director"):
        # 导演段的镜头与取景范围由骨架负责；画风保留影像质感与色彩。
        look = (look.replace("35mm 电影镜头，", "").replace("浅景深，", "").replace("浅景深", "")
                .replace("人物清晰、背景柔和虚化", "材质层次清晰")
                .replace("背景柔和虚化", "景深服从本段景别")).strip("，； ")
    # 光：场景描写里带光源的句子（烛/灯/火/窗/日光/月）
    # P372：本段场景卡自己的光优先（整话那句「炭盆火光照亮床铺」曾串到后山野外）
    _el = str((settings or {}).get("_scene_light") or (settings or {}).get("_time_light") or "").strip()
    sents = ([_el] if _el else []) + [x.strip() for x in re.split(r"[。！？；\n]", str(scene_space or "")) if x.strip()]
    # 只认真正的光词——「乌鸦停在窗框上」带个"窗"字不是光源（项目97 实测）
    _LIT = re.compile(r"主光|辅光|顶灯|吊灯|台灯|路灯|车灯|霓虹|灯光|灯笼|烛|火光|火把|篝火|炉火|壁炉|火焰|月光|月色|阳光|日光|晨光|晨曦|暮色|余晖|夕阳|窗光|天光|荧光灯|背光|逆光|侧光")
    _ACT = re.compile(r"掀|扯|抓|夺|按|推|拉|踹|冲|扑|摔|跪|递|接|握|攥|勾住|探|转身|后退|逼近|说道|喊")
    _names = []
    lit = [x for x in sents
           if _LIT.search(x) and len(x) <= 40 and not _ACT.search(x)
           and not re.search(r"目光|眼光|光滑|光头|光洁|他|她|它|我|们", x)
           and not any(n and n in x for n in _names)][:2]
    light = ("；".join(lit) + "。光有明确的方向：人物朝光的一侧亮、另一侧沉进阴影，背景比人物暗"
             if lit else "一个方向明确的主光源：人物朝光的一侧亮、另一侧沉进阴影，背景比人物暗")
    return ("【摄影】%s光：%s。%s"
            % ((look + "。") if look else "", light, _h3_camera_line(settings, lead)))


def camera_owner_for(sid, settings=None, chars=None):
    """P461：拍摄者。settings.camera_owner（且是卡名）> 口述里最先出现的卡 > 正文里出现最多的卡 > 第一张卡。"""
    from . import asset_core as _ac, oral_story as _os_
    cards = chars if chars is not None else (_ac.list_assets(sid, "characters") or [])
    names = [str(c.get("name") or "") for c in cards if isinstance(c, dict) and c.get("name")]
    if not names:
        return ""
    exp = str((settings or {}).get("camera_owner") or "").strip()
    if exp in names:
        return exp
    try:
        _saga, e = _ep(sid, 1, create=False)
        brief = str((e or {}).get("brief") or "") + " " + str((settings or {}).get("one_line") or "")
        prose = str((e or {}).get("prose") or "")
    except Exception:
        brief, prose = "", ""
    al = _os_.card_aliases(cards)
    first = [(brief.find(a), full) for a, full in al.items() if a and brief.find(a) >= 0]
    if first:
        return min(first)[1]
    cnt = {n: sum(prose.count(a) for a, full in al.items() if full == n and a) for n in names}
    best = max(names, key=lambda n: cnt.get(n, 0))
    return best if cnt.get(best, 0) > 0 else names[0]


def h3_char_order(characters, settings=None):
    """H3 提示词里人物的顺序（＝Subject 编号）。

    第一视角：主角（第一张人设卡）是摄像机，排到最后、不带参考图；其他人按卡的顺序。
    返回 (顺序列表, 不带图的人名或空)。参考包和提示词都用这个函数，编号才对得上。
    """
    chars = [c for c in (characters or []) if (c or {}).get("name")]
    name, _ = _pov_spec(settings)
    if name == "第一视角POV" and chars:
        from .camera_view import owner
        cam = owner(chars, settings)
        return [c for c in chars if c["name"] != cam] + [c for c in chars if c["name"] == cam], cam
    return chars, ""


_H3_BIND_POV = ("<Subject%d>%s是这段的拍摄者——镜头就是%s的眼睛，观众看到的就是%s看到的；"
                "%s在画面里只以自己的手和衣袖出现，声音是%s的。")


# 镜头行头部：镜头N（秒数｜机位｜运镜）：谁+景别。动作   ——括号整块可缺、可只有部分
_SHOT_HEAD = re.compile(r"^镜头(\d+)\s*(?:[（(]([^）)]*)[）)])?\s*[：:]\s*(.*)$")


def shot_parts(line):
    """拆镜头行：(号, 秒数或None, 机位, 运镜, 正文)。不是镜头行返回 None。"""
    m = _SHOT_HEAD.match(str(line or "").strip())
    if not m:
        return None
    no, inside, body = int(m.group(1)), m.group(2) or "", m.group(3)
    secs, cam, mov = None, "", ""
    for tok in re.split(r"[｜|/，,、\s]+", inside):
        tok = tok.strip()
        if not tok:
            continue
        ms = re.match(r"^(\d+(?:\.\d+)?)\s*秒?$", tok) or re.match(r"^(\d+(?:\.\d+)?)s$", tok)
        if ms:
            secs = float(ms.group(1))
        elif tok in ("平视", "俯视", "仰视", "低角度", "越肩", "高角度", "鸟瞰"):
            cam = tok
        elif tok in ("固定", "缓推", "缓拉", "横移", "跟拍", "手持", "甩镜", "推", "拉", "摇", "移", "环绕"):
            mov = tok
    return no, secs, cam, mov, body


def normalize_shot_seconds(body_text, total):
    """把各镜秒数按段总秒归一（模型估的比例保留，总和对齐），秒数缺的按剩余平均补。"""
    lines = str(body_text or "").splitlines()
    idx = [k for k, l in enumerate(lines) if shot_parts(l)]
    if not idx or not total:
        return body_text
    parts = [shot_parts(lines[k]) for k in idx]
    given = [p[1] for p in parts]
    known = sum(x for x in given if x)
    n_missing = sum(1 for x in given if not x)
    if n_missing:
        fill = max(2.0, (float(total) - known) / n_missing) if known < total else 2.0
        given = [x if x else fill for x in given]
    ssum = sum(given) or 1.0
    scale = float(total) / ssum
    secs_list = [max(2.0, round(g * scale)) for g in given]
    # 台词镜头下限：字数/3.5 + 1.5 秒（"还没完"→3 秒，"格拉姆！在上面！"→3.5 秒），差的从最长的动作镜头里扣
    need = {}
    for j, k in enumerate(idx):
        nxt = next((l for l in lines[k + 1:] if l.strip()), "")
        m = re.search(r"\[Chinese\](.*?)</d>", nxt) if "says:" in nxt else None
        if m:
            zh = re.sub(r"[^一-龥]", "", m.group(1))
            need[j] = max(2.0, float(__import__("math").ceil(len(zh) / 3.5 + 1.5)))
    for j, mn in need.items():
        while secs_list[j] < mn:
            donors = [i for i in range(len(secs_list)) if i not in need and secs_list[i] > 2]
            if not donors:
                break
            d = max(donors, key=lambda i: secs_list[i])
            secs_list[d] -= 1
            secs_list[j] += 1
    for k, p, secs in zip(idx, parts, secs_list):
        inside = "｜".join(x for x in ("%d秒" % secs, p[2], p[3]) if x)
        lines[k] = "镜头%d（%s）：%s" % (p[0], inside, p[4])
    return "\n".join(lines)


_SOUND_WORDS = re.compile(
    r"[^，。；]*(沙沙声|呼啸|轰鸣|哑鸣|回荡|噼啪|嘶嘶|脆响|闷响|声响|的声音|这声音|声音(?:清脆|尖锐|沙哑|低沉|悠长|响起|不大|很轻|颤抖|从|在)|"
    r"余音|听着|听见|听得见|响声|作响|尖叫|啸声|长啸|尖啸|声浪|咆哮|嘶吼|嘶鸣|嗡鸣|吱呀|咔哒|哗啦|轰然|痰音|气音|[一-龥]{1,3}声(?=[，。；]|$))[^，。；]*[，。；]?")


def strip_sound_phrases(line):
    """镜头行里描述声音的短句删掉（会被念成台词）。台词行不动。"""
    if not shot_parts(line) or "says:" in line:
        return line
    p = shot_parts(line)
    body = _SOUND_WORDS.sub("", p[4])
    body = re.sub(r"[，；]{2,}", "，", body).strip("，； ")
    inside = "｜".join(x for x in ("%d秒" % p[1] if p[1] else "", p[2], p[3]) if x)
    return "镜头%d%s：%s" % (p[0], ("（%s）" % inside) if inside else "", body)


_VOICE_TECH_SYS = """Rewrite the following Chinese voice description of a character as ONE English line of voice-casting notes,
using exactly these fields in this order: sex and age range; pitch (high/mid/low); timbre (clean/husky/deep/bright/breathy...);
pace (fast/medium/slow); pauses between phrases (many/few); how it changes when emotional.
Plain English only, no quotes, no Chinese characters, no examples, no verbs like say/speak. Output only that line."""


def h3_voice_tech(card, settings=None):
    """音色行用技术说法。卡上没有 voice_h3 就让 Qwen 转一次并存回卡（一人一次）。"""
    if not isinstance(card, dict):
        return h3_voice(card, settings)
    v = str(card.get("voice_h3_en") or "").strip()
    age_key = str(card.get("age") or "").strip()
    if v and card.get("voice_h3_age") == age_key:
        return v
    raw = h3_voice(card, settings)
    _sx = card_sex(card)
    try:
        rep = _q(_VOICE_TECH_SYS, (("这是%s声。" % ("女" if _sx == "女" else "男")) if _sx in ("男", "女") else "") + raw, mt=120, temperature=0.2)
        rep = str(rep or "").strip().splitlines()[0].strip() if rep else ""
    except Exception:
        rep = ""
    if rep and _sx in ("男", "女"):
        # P454⑬：性别按卡改，不信模型猜的（女队长「低沉醇厚」被译成 Male）
        _want, _wrong = ("Female", r"\b(Male|Man|Boy|male|man|boy)\b") if _sx == "女" else ("Male", r"\b(Female|Woman|Girl|female|woman|girl)\b")
        rep = re.sub(_wrong, _want, rep)
        if not re.search(r"\b(Female|Male|Woman|Man|Girl|Boy)\b", rep, re.I):
            rep = _want + " " + rep
    # 含汉字/引号/说念词 → 退回一个保守的英文模板（中文音色描述会被 H3 当台词念出来，2026-09-03 项目98 第 6 段）
    if not rep or re.search(r"[一-龥「」“”]|\bsay|\bspeak|\bor\b", rep, re.I):          # P322⑥："Male or female" 会让配音随机
        sex = "female" if (re.search(r"女", raw) or card_sex(card) == "女") else "male"
        _age = age_number(card.get("age"))
        age = ("%s years old" % _age) if _age is not None else "voice matching the character age"
        rep = "%s, %s, mid pitch, natural timbre, medium pace, few pauses, steadier and quieter when emotional" % (sex, age)
    rep = rep.rstrip(".。") + "."
    try:
        from . import asset_core as _ac, store as _store
        if card.get("story_id") and card.get("character_id"):
            card["voice_h3_en"] = rep
            card["voice_h3_age"] = age_key
            _store.save_json(_ac.assets_path(card["story_id"], "characters") / (card["character_id"] + ".json"), card)
    except Exception:
        pass
    return rep


def scenes_in_text(scenes, text, min_len=3):
    """只留名字在文本里出现过的场景卡（名字任一 ≥3 字子串命中即算）。一个都没有就原样返回。"""
    t = str(text or "")
    keep = []
    for sc in scenes or []:
        name = str(sc.get("name") or "")
        hit = False
        for L in range(len(name), min_len - 1, -1):
            if any(name[a:a + L] in t for a in range(0, len(name) - L + 1)):
                hit = True
                break
        if hit:
            keep.append(sc)
    return keep or list(scenes or [])


def _match_scene_name(hint, names):
    """画面稿地点行的名字 → 场景卡名字。精确优先，再互相包含，再最长公共子串（≥2字）。

    地点行写「城堡净身房」而卡叫「净身房」这类小出入很常见，
    对不上就返回空串，由上层沿用上一段（别乱绑一张）。
    """
    h = str(hint or "").strip()
    if not h:
        return ""
    names = [str(n or "").strip() for n in (names or []) if str(n or "").strip()]
    if h in names:
        return h
    for n in names:                       # 互相包含
        if h in n or n in h:
            return n
    best, best_len = "", 0
    for n in names:                       # 最长公共子串
        for L in range(min(len(h), len(n)), 1, -1):
            if L <= best_len:
                break
            hit = next((h[a:a + L] for a in range(0, len(h) - L + 1) if h[a:a + L] in n), None)
            if hit:
                best, best_len = n, L
                break
    return best if best_len >= 2 else ""


def pick_scene_for_text(text, scenes, prev=""):
    """这段画面稿提到哪张场景卡就用哪张：按场景名和文本的**最长公共子串**打分（≥3 字），
    分高者胜、同分取文本里更早出现的；都没提到沿用上一段；再没有用第一张。"""
    t = str(text or "")
    # 场景名不一定逐字写进画面稿。卡叫「青云大殿内」时，稿子往往只写
    # 「穹顶、供案、主位」。先用这些明确地点词找唯一候选，再走旧的名字匹配。
    cues = {
        "室内": ("室内", "屋内", "殿内", "厅内", "房内", "穹顶", "供案", "主位"),
        "室外": ("室外", "屋外", "殿外", "厅外", "房外"),
        "门口": ("门口", "门前", "殿门", "门外"),
        "广场": ("广场", "殿外广场"),
        "天际": ("天际", "空中", "云海", "御剑", "飞向"),
        "山巅": ("山巅", "山顶", "峰顶"),
    }
    cue_scores = {}
    for sc in scenes or []:
        name = str(sc.get("name") or "")
        space = str(sc.get("space") or sc.get("contract_text") or "")
        score = 0
        for label, words in cues.items():
            if not any(w in t for w in words):
                continue
            if label in name or any(w in name or w in space for w in words):
                score += 10
            elif label == "室内" and ("内" in name or "内部" in space):
                score += 8
            elif label == "室外" and ("外" in name or "外部" in space):
                score += 8
        if score:
            cue_scores[name] = score
    if cue_scores:
        top = max(cue_scores.values())
        winners = [n for n, v in cue_scores.items() if v == top]
        if len(winners) == 1:
            return winners[0]
    best, best_score, best_pos, best_extra = "", 0, 10 ** 9, -1
    for sc in scenes or []:
        name = str(sc.get("name") or "")
        if not name:
            continue
        for L in range(len(name), 2, -1):           # 从整名往短找，找到就停
            hit = False
            for a in range(0, len(name) - L + 1):
                sub = name[a:a + L]
                pos = t.find(sub)
                if pos >= 0:
                    # 同分时：名字去掉公共部分剩下的字（门口／内部）也在文本里的优先（项目97：13 段全绑了门口）
                    _rest = name[:a] + "|" + name[a + L:]
                    extra = sum(1 for _i in range(len(_rest) - 1)
                                if "|" not in _rest[_i:_i + 2] and _rest[_i:_i + 2] in t)
                    if (L > best_score or (L == best_score and extra > best_extra)
                            or (L == best_score and extra == best_extra and pos < best_pos)):
                        best, best_score, best_pos, best_extra = name, L, pos, extra
                    hit = True
                    break
            if hit:
                break
    if best:
        return best
    if prev:
        return prev
    return str((scenes or [{}])[0].get("name") or "") if scenes else ""


_DIRECTOR_REVIEW_SYS = """你是导演，复审一段视频提示词的镜头行。对照清单，**只改不合格的那几镜**，其余镜头行和所有台词行原文原位不动：
1. 每段至少一个远景或全景；远景/全景里必须写清每个人在空间的哪儿、离什么几步、彼此隔多远。
2. 同一个人连续两镜不许同一景别。
3. 人从一个空间到另一个空间要写明"从哪、怎么进、到了哪"；一直在室内的不能像室外。
4. 括号里要有 秒数｜机位｜运镜 三样；文戏固定/缓推为主，动作戏跟拍/手持/低角度、镜头短。
5. 不许出现声音描写（沙沙声、呼啸、听着…）。
6. 镜头里的动作、物件、位置都只能来自画面稿。
输出完整的镜头体（所有行），不要说明。"""


# ─────────── 时间轴式导演（2026-09-04 A/B/C 对比 C 胜）───────────
def use_timeline_director():
    """导演层写法：timeline（默认，时间轴因果链）/ shots（旧镜头行）。V41_DIRECTOR 环境变量切。"""
    import os
    return str(os.environ.get("V41_DIRECTOR") or "timeline").strip().lower() != "shots"


_TL_BLOCK = re.compile(r"^\s*(\d+)\s*[—\-–~～]\s*(\d+)\s*秒[：:]", re.M)
_TL_FACING = re.compile(r"面朝|正对|背对|侧对|朝向|脸朝|挡在[^，。]{0,8}之间|转身面向|迎向")
_TL_BAD_FACING = re.compile(r"背对(?:着)?(?:敌|石魔|对手|威胁|它|巨|怪)")


def _tl_norm_heads(body):
    """P408：模型写的「0—2.5秒：」小数块头 → 四舍五入成整数；块头前保证有空行（_TL_BLOCK 只认整数，认不出整段守卫全跳过）。"""
    t = str(body or "")
    if "秒：" not in t and "秒:" not in t:
        return t

    def _r(m):
        a, b = float(m.group(1)), float(m.group(2))
        return "%d—%d秒：" % (int(a + 0.5), int(b + 0.5))
    t = re.sub(r"(?m)^\s*(\d+(?:\.\d+)?)\s*[—\-–~～]\s*(\d+(?:\.\d+)?)\s*秒[：:]", _r, t)
    # P424：只写了「3—6。」「6—10：」这种没有「秒」的块头 → 补成「3—6秒：」，正文在下一行的接上来
    t = re.sub(r"(?m)^\s*(\d+)\s*[—\-–~～]\s*(\d+)\s*[。．：:]?\s*$", lambda m: "%s—%s秒：" % (m.group(1), m.group(2)), t)
    t = re.sub(r"(\d+—\d+秒：)\n(?=\S)", r"\1", t)
    lines, out = t.split("\n"), []
    for ln in lines:
        if _TL_BLOCK.match(ln) and out and out[-1].strip():
            out.append("")
        out.append(ln)
    t = "\n".join(out)
    # 块头时间重叠（0—10、3—6、6—10）→ 前一块止点 = 后一块起点
    heads = [(m.start(1), m.end(), int(m.group(1)), int(m.group(2))) for m in _TL_BLOCK.finditer(t)]   # start(1)：块头正则的 ^\s* 会吞掉前面的空行
    if len(heads) >= 2:
        fixed, pieces, pos = [], [], 0
        for k, (a, b, x, y) in enumerate(heads):
            nx = heads[k + 1][2] if k + 1 < len(heads) else None
            y2 = nx if (nx is not None and nx > x and y > nx) else y
            pieces.append(t[pos:a] + "%d—%d秒：" % (x, y2))
            pos = b
        pieces.append(t[pos:])
        t = "".join(pieces)
    return t


def _tl_blocks(body):
    """把正文切成 [(起, 止, 文本)]；块外的开头段/收尾段另返回。"""
    lines = body.splitlines()
    idx = [i for i, l in enumerate(lines) if _TL_BLOCK.match(l)]
    if not idx:
        return [], body, ""
    head = "\n".join(lines[:idx[0]]).strip()
    blocks, tail = [], ""
    for k, i in enumerate(idx):
        j = idx[k + 1] if k + 1 < len(idx) else len(lines)
        chunk = lines[i:j]
        # 收尾段（"这一段结束时"）从最后一块里剥出来
        if k == len(idx) - 1:
            for t, l in enumerate(chunk):
                if t > 0 and re.match(r"^\s*[*＊#【\[]*\s*这一段结束时", l):
                    tail = "\n".join(chunk[t:]).strip()
                    chunk = chunk[:t]
                    break
        m = _TL_BLOCK.match(chunk[0])
        blocks.append((int(m.group(1)), int(m.group(2)), "\n".join(chunk).strip()))
    return blocks, head, tail


def _tl_fix_dialogue(body, say_lines):
    """台词行原文只出现一次、在正确的块里；模型写的中文引号裸句删掉。"""
    lines = body.splitlines()
    quotes = []
    for sl in say_lines:
        m = re.search(r"\[Chinese\]([^<]+)</d>", sl)
        quotes.append(re.sub(r"[^一-龥！？]", "", m.group(1)) if m else "")
    norm = lambda q: re.sub(r"[^一-龥！？]", "", q)
    out = []
    seen_say = set()
    for l in lines:
        if "says:" in l:
            key = norm(l)
            if key in seen_say:
                continue
            seen_say.add(key)
            # 模型改写过格式的台词行，换成原文
            fixed = next((sl for sl, q in zip(say_lines, quotes) if q and q in key), l)
            out.append(fixed)
            continue
        raw = l
        for q in quotes:
            if not q:
                continue
            if norm(raw) == q or re.fullmatch(r"\s*[一-龥]{1,4}[：:]\s*" + re.escape(q) + r"\s*", norm(raw).join(["", ""]) if False else raw.strip()) or norm(raw) in (q,):
                raw = ""
                break
            # 行内裸台词：「名字：台词」「“台词”」
            raw = re.sub(r"[一-龥]{1,4}[：:]\s*[“\"「]?" + ".{0,3}".join(map(re.escape, list(q.rstrip("！？")))) + r"[！!？?]?[”\"」]?", "", raw)
            raw = re.sub(r"[“\"「]" + ".{0,3}".join(map(re.escape, list(q.rstrip("！？")))) + r"[！!？?]?[”\"」]", "", raw)
        if raw.strip() or not l.strip():
            out.append(raw)
    lines = out
    have = {norm(l) for l in lines if "says:" in l}
    _used_stub = set()
    for sl, q in zip(say_lines, quotes):
        if any(q and q in h for h in have):
            continue
        # P340：导演在块里留了「X说：」残句（整行或行尾）→ 那就是这句台词的位置，按说话人顺序填进去
        _mw = re.match(r"^([\u4e00-\u9fa5]{1,6})说[：:]", sl)
        _who = _mw.group(1) if _mw else ""
        if _who:
            _stub = None
            for i, l in enumerate(lines):
                if i in _used_stub or "says:" in l:
                    continue
                if not re.search(r"(?:^|[。！？；，\s])" + re.escape(_who) + r"(?:S\d)?说[：:]\s*$", l.strip()):
                    continue
                _nxt = next((x for x in lines[i + 1:] if x.strip()), "")
                if "says:" in _nxt:
                    continue                                              # 「X说：」后面紧跟台词行 → 是引导句不是空位
                _stub = i
                break
            if _stub is not None:
                _used_stub.add(_stub)
                _l = re.sub(re.escape(_who) + r"(?:S\d)?说[：:]\s*$", "", lines[_stub].rstrip()).rstrip()
                if _l.strip():
                    lines[_stub] = _l
                    lines.insert(_stub + 1, sl)
                else:
                    lines[_stub] = sl
                have.add(norm(sl))
                continue
        # 放到含"开口/说/喊"或含台词相近词的那一块末尾，否则第一块末尾
        bidx = [i for i, l in enumerate(lines) if _TL_BLOCK.match(l)]
        def _end(i):
            j = i + 1
            while j < len(lines) and lines[j].strip() and not _TL_BLOCK.match(lines[j]) and not re.match(r"^\s*[*＊#【\[]*\s*这一段结束时", lines[j]):
                j += 1
            return j
        target = None
        for i in bidx:
            seg = " ".join(lines[i:_end(i)])
            if re.search(r"开口|喊道|说道|喊|说完|问道", seg):
                target = i
                break
        if target is None and bidx:
            target = bidx[0]
        if target is not None:
            lines.insert(_end(target), sl)
        else:
            lines.append(sl)
    return "\n".join(lines)


def _tl_scale_each_block(body, chars):
    """非人角色在每个时间块第一次出现时带体量。"""
    for c in chars or []:
        if not is_nonhuman(c):
            continue
        n = str(c.get("name") or "")
        if not n:
            continue
        h = re.search(r"高(约?\s*[一二两三四五六七八九十\d.]+\s*米)", body_scale_line(c))
        tag = "%s（高%s）" % (n, h.group(1) if h else "约3米")
        out = []
        for l in body.splitlines():
            if _TL_BLOCK.match(l) and n in l and tag not in l and ("高约" not in l.split(n, 1)[1][:6]):
                l = l.replace(n, tag, 1)
            out.append(l)
        body = "\n".join(out)
    return body


def _tl_clean(body):
    out = []
    for l in body.splitlines():
        if "says:" in l:
            out.append(l)
            continue
        l = re.sub(r"[A-Za-z]{3,}", "", l)
        l = re.sub(r"[，,]?[^，。]*(?:定格|静止画面|慢动作|慢放)[^，。]*[。]?", "。", l)
        l = re.sub(r"。{2,}", "。", l).replace("。，", "。").replace("，。", "。")
        out.append(l)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def tl_fill_gaps(body, seconds=None):
    """把时间轴的秒数重排成首尾相接、覆盖整段（P228）。只动数字，内容一个字不动。

    实测段2：拍是 0—3 和 10—14，中间 7 秒模型没有任何镜头指令；
    段4：最后一拍写到 12 秒，可这一段有 14.7 秒。
    内容其实都在（两拍之间浮着的台词行归上一块），错的是时间标签。
    规则：首拍从 0 起；每拍的结束秒＝下一拍的开始秒；末拍的结束秒＝这一段的时长。
    """
    blocks, head, tail = _tl_blocks(body)
    if not blocks:
        return body
    total = int(round(float(seconds))) if seconds else max(b for _, b, _ in blocks)
    starts = [0] + [max(a, 0) for a, _, _ in blocks[1:]]
    # 开始秒必须递增；乱了就按原顺序均分
    for i in range(1, len(starts)):
        if starts[i] <= starts[i - 1]:
            starts[i] = starts[i - 1] + 1
    if starts[-1] >= total:
        starts = [round(total * i / len(blocks)) for i in range(len(blocks))]
    ends = starts[1:] + [total]
    out, changed = [], []
    for k, (a, b, txt) in enumerate(blocks):
        na, nb = starts[k], ends[k]
        if (na, nb) != (a, b):
            changed.append("%d—%d秒→%d—%d秒" % (a, b, na, nb))
            txt = _TL_BLOCK.sub("%d—%d秒：" % (na, nb), txt, count=1)
        out.append(txt)
    if changed:
        _dbg("时间轴补空洞", {"改": changed, "段长": total})
    return "\n\n".join([head] + out + ([tail] if tail else [])).strip()


_SAY_LINE = re.compile(r"(<d>\[[^\]]*\])(.*?)(</d>)", re.S)


_SAY_INLINE = re.compile(r"(?<!^)(?<!\n)(\s*)([^\s，。；：:\n]{1,8}说：<Subject\s*\d+>\s*\(S\d+\)\s*says:<d>)", re.M)
_SAY_TAIL = re.compile(r"(</d>)(?!\s*$)(?!\n)(\s*)", re.M)


def split_say_lines(body):
    """台词行单独成行（P393）：段落里夹着的「X说：<Subject N> (SN) says:<d>…</d>」前后各断一行；台词后面的正文另起一行。"""
    t = str(body or "")
    if "says:<d>" not in t:
        return t
    t = _SAY_INLINE.sub(lambda m: "\n" + m.group(2), t)
    out = []
    for line in t.split("\n"):
        if "says:<d>" in line and "</d>" in line:
            head, tail = line.split("</d>", 1)
            out.append(head + "</d>")
            if tail.strip():
                out.append(tail.strip())
        else:
            out.append(line)
    return "\n".join(out)


_IMPLIED_SPEECH = re.compile(r"讲解|解释|说着|念叨|回答|应道|答道|问道|说道|低语|喃喃|嘀咕|交谈|讨论|叙述|讲述|絮絮|说话|开口|喊道|喊出|叫道|吼道|念出|复述|嘱咐|叮嘱|催促|轻声说|大声说|说了|话音|语气|嗓音|声音|聊|对话|询问|发问|开腔|回应道|附和|嘟囔|自言自语")
_MOUTH_CUE = re.compile(r"嘴唇开合|嘴唇微张|嘴唇翕动|嘴唇微动|张开嘴")


def scrub_implied_speech(body, say_lines):
    """P395：① 隐含说话的短句删掉（没台词的人一开口 H3 就胡言）；② 台词前一句补「X嘴唇开合」；③ 台词紧贴它的时间块。"""
    t = str(body or "")
    if "秒：" not in t and "秒:" not in t:
        return t
    lines = t.split("\n")
    out = []
    for i, line in enumerate(lines):
        st = line.strip()
        if not st:
            out.append(line)
            continue
        if "says:<d>" in st or st.startswith("这一段结束时") or st.startswith("【") or st.startswith("·") or st.startswith("<Subject"):
            # ③ 台词行紧贴上一块：中间的空行去掉
            if "says:<d>" in st:
                while out and not out[-1].strip():
                    out.pop()
                # ② 前一句补口型提示
                m = re.match(r"^([^\s：:]{1,8})说：<Subject", st)
                who = m.group(1) if m else ""
                if out and who and not _MOUTH_CUE.search(out[-1]) and not out[-1].strip().startswith("这一段结束时") and "says:<d>" not in out[-1]:
                    out[-1] = out[-1].rstrip("。 ") + "。" + who + "嘴唇开合。"
            out.append(line)
            continue
        _hm = re.match(r"^(\d+(?:\.\d+)?\s*[—\-–~]+\s*\d+(?:\.\d+)?\s*秒[：:])", st)
        _head, _rest = (_hm.group(1), st[_hm.end():]) if _hm else ("", st)
        # 这块后面紧跟台词行吗（隔着空行也算）
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        next_is_say = j < len(lines) and "says:<d>" in lines[j]
        parts = [p for p in re.split(r"(?<=[，。；！？])", _rest) if p]
        keep = []
        for k, p in enumerate(parts):
            if _IMPLIED_SPEECH.search(p) and not _MOUTH_CUE.search(p):
                if next_is_say and k == len(parts) - 1:
                    keep.append(p)                            # 台词前最后一句留着当提示
                continue
            keep.append(p)
        body_txt = "".join(keep).strip()
        if _head and not body_txt:
            body_txt = _rest                                  # 删空了留原文
        out.append(_head + body_txt if _head else body_txt)
    return "\n".join(out)


def _say_quote(l):
    m = re.search(r"\[Chinese\]([^<]+)</d>", str(l or ""))
    return re.sub(r"[^一-龥！？]", "", m.group(1)) if m else ""


def hoist_say_lines(body, order=None):
    """P396（节拍段）：台词行提前——第 1 行放第一个时间块末尾，第 2 行放第二块末尾（没有第二块就紧跟第 1 行）。
    台词挂在最后一块后面时，前面几秒 H3 会让说话人先胡言。台词前一句的「X嘴唇开合」跟着一起搬。
    P407：order 给了这一拍的台词行时，按那个顺序排（模型把「等等」写到「走吧」前面）。"""
    t = str(body or "")
    if "says:<d>" not in t:
        return t
    lines = t.split("\n")
    says = [l for l in lines if "says:<d>" in l]
    if order:
        _oq = [_say_quote(o) for o in order]

        def _key(l):
            q = _say_quote(l)
            for i, oq in enumerate(_oq):
                if q and oq and (q in oq or oq in q):
                    return i
            return len(_oq)
        says = sorted(says, key=_key)
    rest = []
    for l in lines:
        if "says:<d>" in l:
            continue
        rest.append(re.sub(r"[^\s，。；]{1,8}嘴唇开合。?$", "", l) if _MOUTH_CUE.search(l) and not l.strip().startswith("·") else l)
    # 找时间块：块头行的下标（块正文可能跨多行，块末＝下一个空行/块头/结尾之前）
    heads = [i for i, l in enumerate(rest) if _TL_BLOCK.match(l)]
    if not heads:
        return t
    def _block_end(k):
        i = heads[k] + 1
        while i < len(rest) and rest[i].strip() and not _TL_BLOCK.match(rest[i]) and not rest[i].strip().startswith("这一段结束时"):
            i += 1
        return i                                  # 插入位置（块最后一行之后）
    out = list(rest)
    inserts = []
    for n, sl in enumerate(says):
        k = min(n, len(heads) - 1)
        m = re.match(r"^([^\s：:]{1,8})说：<Subject", sl.strip())
        who = m.group(1) if m else ""
        inserts.append((_block_end(k), k, who, sl))
    # 从后往前插，下标不乱；同一块里多行按原顺序
    for pos, k, who, sl in sorted(inserts, key=lambda x: (x[0], says.index(x[3])), reverse=True):
        prev = out[pos - 1]
        if who and not _MOUTH_CUE.search(prev):
            out[pos - 1] = prev.rstrip("，。 ") + "。" + who + "嘴唇开合。"
        out.insert(pos, sl)
    return "\n".join(out)


def dedup_say_lines(body):
    """同一段里已经说过的句子，从后面的台词行里删掉（P228）。

    实测段4 同一句被念三遍：「精灵的账本可不认空头人情。」单独一行，
    下一行又把它整句重念再接新句，再下一行把新句又重念一遍——
    真实台词两句，被撑成 70 字、折合 20 秒，塞进 14.7 秒的段里。
    按句去重：整行都空了就把这一行删掉。
    """
    seen, out = set(), []
    for line in str(body or "").splitlines():
        m = _SAY_LINE.search(line)
        if not m:
            out.append(line)
            continue
        _spk = re.match(r"^\s*([^\s：:]{1,8})说[：:]", line)
        _who = _spk.group(1) if _spk else ""
        sents = [x for x in re.split(r"(?<=[。！？])", m.group(2)) if x.strip()]
        keep = [x for x in sents if (_who, re.sub(r"\s", "", x)) not in seen]          # P423：只在同一个说话人内去重
        for x in keep:
            seen.add((_who, re.sub(r"\s", "", x)))
        if not keep:
            _dbg("台词整行重复，删掉", {"行": m.group(2)[:30]})
            continue
        if len(keep) != len(sents):
            _dbg("台词去重", {"原": m.group(2)[:40], "留": "".join(keep)[:40]})
            line = line[:m.start(2)] + "".join(keep) + line[m.end(2):]
        out.append(line)
    return "\n".join(out)


_POSE_MOVE = re.compile(r"起身|站起|站直|离座|坐下|落座|坐回|蹲下|跪")


def tl_pose_of(head, name):
    """承接段里这个人是坐着还是站着（P229）。判不出返回空。

    承接段来自上一段真实的收尾状态，是这一段的权威姿势。
    """
    t, n = str(head or ""), str(name or "")
    if not n or n not in t:
        return ""
    # P375：接缝模板的状态行「老者：床铺旁｜面朝阿青｜手里无｜坐姿端正｜画面右侧」——第 4 栏是姿势
    for m in re.finditer(r"^[·\s]*" + re.escape(n) + r"[：:]\s*(?:[^｜\n]*｜){3}([^｜\n]*)", t, re.M):
        pose = m.group(1)
        if "躺" in pose or "趴" in pose or "卧" in pose:
            return "躺"
        if "坐" in pose and "站" not in pose:
            return "坐"
        if "站" in pose and "坐" not in pose:
            return "站"
    sit = re.search(n + r"[^，。；｜\n]{0,8}坐", t)
    stand = re.search(n + r"[^，。；｜\n]{0,8}站", t)
    if sit and not stand:
        return "坐"
    if stand and not sit:
        return "站"
    return ""


def fix_pose_words(body, conflicts, prev_state, names):
    """节拍段姿势矛盾的确定性修法（P377）：按上一段的姿势把块里的「站在/站立/站着」「坐在/坐着」换掉，
    从第一个矛盾块起一直换到出现 起身/坐下 这类姿势变化词的块为止。"""
    blocks, head, tail = _tl_blocks(body)
    _rep = {"坐": {"站在": "坐在", "站立": "坐着", "站着": "坐着", "站直": "坐直", "站定": "坐定"},
            "躺": {"站在": "躺在", "站立": "躺着", "站着": "躺着", "坐在": "躺在", "坐着": "躺着", "端坐": "平躺", "坐直": "躺着", "坐定": "躺着"},
            "站": {"坐在": "站在", "坐着": "站着", "端坐": "站立"}}
    for k0, nm, want, got in conflicts:
        table = _rep.get(want) or {}
        if not table:
            continue
        for k in range(k0, len(blocks)):
            a, b, txt = blocks[k]
            if k > k0 and re.search(re.escape(str(nm)) + r"[^。；｜\n]{0,12}?(坐起|坐下|落座|坐回|站起|站直|起立|起身|躺下|倒下|蹲下|跪下)", txt):
                break                                                  # P378：只认这个人自己的姿势变化
            for bad, good in table.items():
                txt = re.sub(re.escape(str(nm)) + r"([^，。；｜\n]{0,8})" + re.escape(bad), lambda m, g=good: m.group(0)[:-len(bad)] + g, txt)
            blocks[k] = (a, b, txt)
    return "\n\n".join([head] + [t for _, _, t in blocks] + ([tail] if tail else [])).strip()


_BEAT_POSE = (
    (r"(?:把|将)(?P<n>{N})[^。；\n]{0,8}?(?:扶起来站稳|扶起来站|扶起来站好|扶站起|拉起来站)", "站立"),
    (r"(?:把|将)(?P<n>{N})[^。；\n]{0,8}?(?:扶起来|扶起|扶坐起|扶坐|拉起来|拉起|抱起来|抱起)", "坐着"),
    (r"(?:把|将)(?P<n>{N})[^。；\n]{0,8}?(?:背起来|背起|背在背上|背上|扛起|扛在肩上|抱在怀里)", "被背着"),
    (r"(?P<n>{N})[^，。；\n]{0,8}?(?:站起来|站起|站稳|站直|起身|起立|站了起来)", "站立"),
    (r"(?P<n>{N})[^，。；\n]{0,8}?(?:坐起来|坐起|坐下|坐到|落座|坐回)", "坐着"),
    (r"(?P<n>{N})[^，。；\n]{0,8}?(?:躺下|躺倒|倒下|倒地|躺在|昏倒|瘫倒)", "躺着"),
    (r"(?P<n>{N})[^，。；\n]{0,8}?(?:跪下|跪在|跪倒)", "跪着"),
    (r"(?P<n>{N})[^，。；\n]{0,8}?(?:蹲下|蹲在|蹲身)", "蹲着"),
    (r"(?P<n>{N})[^，。；\n]{0,8}?(?:被背着|被背在|在背上|趴在[^，。]{0,4}背上)", "被背着"),
)


def beat_pose_state(shots, prev_state, names):
    """守卫用的「上一段状态」：这一拍文本里写明的姿势变化（把X扶起来站稳 / X坐起 / 背在背上）先改进去（P387②）。
    只改姿势栏（第 4 栏）；那个人没有状态行就补一行。"""
    st = str(prev_state or "")
    names = [str(n or "") for n in (names or []) if str(n or "").strip()]
    txt = str(shots or "")
    if not names or not txt.strip():
        return st
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    want = {}
    for pat, pose in _BEAT_POSE:
        for m in re.finditer(pat.replace("{N}", alt), txt):
            want.setdefault(m.group("n"), pose)
    for n, pose in want.items():
        rx = re.compile(r"^([·\s]*" + re.escape(n) + r"[：:]\s*(?:[^｜\n]*｜){3})([^｜\n]*)(.*)$", re.M)
        if rx.search(st):
            st = rx.sub(lambda m: m.group(1) + pose + m.group(3), st, count=1)
        else:
            st = (st + "\n" if st.strip() else "") + "· %s：｜｜｜%s｜" % (n, pose)
    return st


def tl_pose_conflicts(body, names, prev_state=""):
    """哪一块把人写成了和承接段相反的姿势（P229）。返回 [(块序号, 名字, 应有姿势, 写成了)]。
    P375：节拍段的正文头只有【机位】，上一段状态在 prev_state 里，一起看。"""
    blocks, head, _ = _tl_blocks(body)
    out = []
    for n in (names or []):
        want = tl_pose_of(head + "\n" + str(prev_state or ""), n)
        if not want:
            continue
        bad = {"坐": "站", "站": "坐", "躺": "站|坐"}.get(want, "")
        if not bad:
            continue
        for k, (_a, _b, txt) in enumerate(blocks):
            # P378：只认这个人自己的姿势变化，变了就按新姿势继续查
            mv = re.search(re.escape(str(n)) + r"[^。；｜\n]{0,12}?(坐起|坐下|落座|坐回|站起|站直|起立|起身|躺下|倒下|蹲下|跪下)", txt)
            if mv:
                w = mv.group(1)
                want = ("坐" if w in ("坐起", "坐下", "落座", "坐回") else "躺" if w in ("躺下", "倒下")
                        else "站" if w in ("站起", "站直", "起立", "起身") else "")
                bad = {"坐": "站", "站": "坐", "躺": "站|坐"}.get(want, "")
                if not bad:
                    break
                continue
            m_ = re.search(re.escape(str(n)) + r"[^，。；｜\n]{0,8}(" + bad + ")", txt)
            if m_:
                out.append((k, str(n), want, m_.group(1)))
                break
    return out


def tl_pose_problems(body, names):
    """同一个人在这一段里既坐又站（P228 段4、段5 实测）。只报不改。

    先修对峙修复器（它只改站位，会把坐着的人改成站着对峙），
    再看这条还在不在——不猜着改。
    """
    t = str(body or "")
    probs = []
    for n in (names or []):
        n = str(n or "")
        if not n or n not in t:
            continue
        if re.search(r"起身|站起|离座|坐下|落座", t):
            continue                       # 写了起身/坐下这类交代，不算矛盾
        sit = len(re.findall(n + r"[^，。；\n]{0,6}坐", t))
        stand = len(re.findall(n + r"[^，。；\n]{0,6}站", t))
        if sit and stand:
            probs.append("%s 在这一段里既坐着又站着（坐%d处、站%d处），没写起身或坐下" % (n, sit, stand))
    return probs


def _tl_facing_problems(body, names):
    """每块要有朝向词；出现"背对敌人/背对石魔"这类算错。返回 [(块序号, 问题)]。"""
    blocks, _, _ = _tl_blocks(body)
    probs = []
    for k, (a, b, txt) in enumerate(blocks):
        if _TL_BAD_FACING.search(txt):
            probs.append((k, "有人背对着威胁"))
        elif not _TL_FACING.search(txt):
            probs.append((k, "没写站位朝向（谁面朝谁、谁挡在谁前面）"))
    return probs


_WIDE_SHOT = re.compile(r"远景|全景|大全景")
_SAME_SIDE = re.compile(r"并排|并肩|同侧|同一侧|身旁|身边|紧挨|挨在|肩并肩")
_GAP_WORDS = re.compile(r"隔[^，。]{0,6}步|相隔|距[^，。]{0,8}(?:步|米)|[三四五六七八九十两]步(?:开外|远|外)|中间(?:是|留|空)")


def confrontation_problems(body, foe_names):
    """对峙空间检查：整段要有远景/全景；不许把对手写成和主角并排同侧；要写出双方距离。"""
    probs = []
    txt = str(body or "")
    if not _WIDE_SHOT.search(txt):
        probs.append("整段没有一个远景或全景把敌我和中间的空间框进来")
    # 并排/同侧 只在**同一分句**里同时出现对手名才算（"加尔被拖至艾拉内侧，两人并排"是自己人，不算）
    for clause in re.split(r"[，。；、\n]", txt):
        if not _SAME_SIDE.search(clause):
            continue
        hit = next((n for n in (foe_names or []) if n and n in clause), "")
        if hit:
            probs.append("把对手%s写成了和主角并排/同侧" % hit)
            break
    if not _GAP_WORDS.search(txt):
        probs.append("没写出敌我之间隔多远")
    return probs


def confrontation_fix(body, foe_names, prev_state="", foe_desc=""):
    """对峙空间不合格 → 改整段的站位描述（动作和台词不动），一次。

    foe_desc：要素表里的对手描述。对手不是人物卡（追兵、警察、机甲）时 foe_names 是空的，
    这时要用描述当名字，并且改用**追逐几何**——硬套左右两侧会把追逐戏改坏（2026-09-05）。
    """
    probs = confrontation_problems(body, foe_names)
    if not probs:
        return body
    _dbg("对峙空间不合格", {"问题": probs})
    says = [l for l in str(body or "").splitlines() if "says:" in l]
    _foe_txt = "、".join(n for n in (foe_names or []) if n) or \
        re.split(r"[——，,（(]", str(foe_desc or ""))[0].strip() or "对手"
    if foe_names:
        _geo = ("**对手（%s）和主角这一方必须分处画面的相对两侧，中间留出看得见的空地**，"
                "常态相隔三步以上，只有出手接触那一两秒才贴近、打完立刻拉开；"
                "整段至少有一个**远景或全景**把对手、主角方和中间的空地一起框进来。" % _foe_txt)
    else:
        _geo = ("对手是「%s」，它不在人物表上，只作为画面里的威胁出现。"
                "**主角这一方在近处、对手在画面深处的正后方或正前方，两者之间要有一段看得见的纵深距离**"
                "；不要把它摆到主角侧边、也不要贴在身后当背景。"
                "主角和同伴是同一方，必须在同一侧、挨在一起。"
                "整段至少有一个**远景或全景**，把主角方、对手和中间那段距离一起框进来。" % _foe_txt)
    ask = ("下面这段视频提示词的对峙空间不合格：%s。请修改：%s"
           "只改站位、距离和镜头景别，**动作过程和台词行一字不动**，时间块的数量和秒数不变，格式保持原样（开头段 + 「N—N秒：」块 + 「这一段结束时：」）。只输出改好的整段。"
           % ("；".join(probs), _geo))
    try:
        new = str(_q(ask, str(body or ""), mt=2400, temperature=0.4) or "").strip()
    except Exception:
        return body
    if new and all(sl in new for sl in says) and len(confrontation_problems(new, foe_names)) < len(probs):
        body = _tl_clean(new)
    # 代码兜底：还是没有远景 → 把第一个时间块的镜头行景别改成远景
    if not _WIDE_SHOT.search(str(body or "")):
        lines = str(body or "").splitlines()
        done = False
        for i, l in enumerate(lines):
            if "镜头：" in l and not done:
                lines[i] = re.sub(r"镜头：\s*", "镜头：远景，", l, count=1)
                done = True
                break
        if not done:
            for i, l in enumerate(lines):
                if _TL_BLOCK.match(l):
                    lines.insert(i + 1, ("镜头：远景，%s在画面一侧、主角这一方在另一侧，中间是空地，一个画面里看清双方的距离。"
                                         % _foe_txt) if foe_names else
                                 ("大远景，主角这一方在近处，%s在画面深处，中间是一段看得见的空距离，一个画面里看清双方隔多远。"
                                  % _foe_txt))
                    done = True
                    break
        if done:
            body = "\n".join(lines)
            _dbg("远景代码兜底", {})
    return body


_ATTACK_LIKE = re.compile(
    r"伸直(?:右|左)?(?:手)?臂|(?:右|左|手)臂[^，。]{0,4}(?:伸直|前伸|挥|甩)|"
    r"挥出|挥向|挥拳|扑向|猛扑|飞扑|拳|掌击|击中|命中|撞上|撞向|顶开|抡|砸向|踢向|肘击")
_BLOOD_RE = re.compile(r"血痕|渗血|吐血|出血|血迹|溅血|嘴角[^，。]{0,4}血")
_FALL_INTO = re.compile(r"倒向[^，。]{0,6}怀|栽进[^，。]{0,4}怀|失衡[^，。]{0,6}倒")


_SPLIT_SIDE = re.compile(r"分处(?:画面)?(?:的)?相对两侧|分处两侧|各在(?:画面)?一侧|一左一右|中间留出[^，。]{0,6}空地|相隔三步以上|隔着[^，。]{0,4}的空地")


def _sides_of(chars, settings):
    """返回 (对手名单, 同伴名单, 主角名单)。要素表优先，猜法只当兜底。

    2026-09-05：原来是「非人角色算敌人，否则除第一张卡外都算敌人」，
    男主+女主的戏里女主直接被当成敌人，对峙守卫把两人分到了画面两侧。
    """
    names = [c["name"] for c in chars]
    foe_txt = str((settings or {}).get("_foe_text") or "")
    ally_txt = str((settings or {}).get("_ally_text") or "")
    hero_txt = str((settings or {}).get("_hero_text") or "")
    foes = [n for n in names if n and n in foe_txt]
    allies = [n for n in names if n and n in ally_txt]
    heroes = [n for n in names if n and n in hero_txt]
    if not heroes:
        heroes = [n for n in names[:1] if n not in foes]
    allies = [n for n in allies if n not in foes and n not in heroes]
    if not foes:
        # 要素表里的对手不在人物卡上（追兵、警察、机甲这类）→ 非人角色才算敌人，
        # 剩下的一律是自己人；绝不再拿"第二张卡"充数。
        foes = [c["name"] for c in chars if is_nonhuman(c) and c["name"] not in heroes]
        if not allies:
            allies = [n for n in names if n not in foes and n not in heroes]
    if not allies:
        allies = [n for n in names if n not in foes and n not in heroes]
    return foes, [n for n in (heroes + allies) if n], heroes


def ally_side_fix(body, hero_names, ally_names):
    """主角和同伴被分到两侧 → 只改那一块，改成同一侧／一前一后，台词不动，一次。"""
    probs = ally_side_problems(body, hero_names, ally_names)
    if not probs:
        return body
    _dbg("主角同伴被分侧", {"问题": [p for _, p in probs][:2]})
    blocks, head, tail = _tl_blocks(body)
    ask = ("下面是一段视频提示词里的一块，问题：%s。只重写这一块："
           "主角和同伴是**同一方**，必须在**同一侧**、挨在一起；同乘一辆车／马／机甲时写成"
           "**一前一后同一个轮廓**（后面的人贴着前面的人的背），只有一辆载具，不许各骑一辆。"
           "把「分处两侧」「中间留出空地」「相隔三步」这类写法全部去掉——那是留给敌人的。"
           "要留距离的是主角方和**敌人**之间。台词行一字不动，镜头和敌人的位置保留。%s只输出这一块。")
    for ki, why in probs[:2]:
        src = head if ki < 0 else blocks[ki][2]
        keep = "开头保留「%d—%d秒：」。" % (blocks[ki][0], blocks[ki][1]) if ki >= 0 else ""
        says = [l for l in src.splitlines() if "says:" in l]
        try:
            new = str(_q(ask % (why, keep), src, mt=700, temperature=0.4) or "").strip()
        except Exception:
            new = ""
        ok = new and all(sl in new for sl in says) and not ally_side_problems(new, hero_names, ally_names)
        if ok and (ki < 0 or _TL_BLOCK.match(new)):
            if ki < 0:
                head = _tl_clean(new)
            else:
                blocks[ki] = (blocks[ki][0], blocks[ki][1], _tl_clean(new))
    return "\n\n".join([head] + [x for _, _, x in blocks] + ([tail] if tail else [])).strip()


def ally_side_problems(body, hero_names, ally_names):
    """主角和同伴被写成分处两侧／中间留空地 → 报问题。返回 [(块序号, 问题)]。

    2026-09-05 追逐戏实测：铁规6 的"分处相对两侧"被套到了同乘一辆摩托的男女主身上。
    """
    blocks, head, _ = _tl_blocks(body)
    heroes = [n for n in (hero_names or []) if n]
    allies = [n for n in (ally_names or []) if n]
    if not (heroes and allies):
        return []
    probs = []
    for k, (a, b, txt) in enumerate(blocks + ([(0, 0, head)] if head else [])):
        ki = k if k < len(blocks) else -1
        for clause in re.split(r"[。；\n]", txt):
            if not _SPLIT_SIDE.search(clause):
                continue
            if any(h in clause for h in heroes) and any(x in clause for x in allies):
                probs.append((ki, "把主角和同伴写成分处两侧／中间留空地：%s" % clause.strip()[:40]))
                break
    return probs


def ally_action_problems(body, ally_names, foe_names):
    """同伴之间的动作看起来像打架 → 报问题。返回 [(块序号, 问题)]。"""
    blocks, head, _ = _tl_blocks(body)
    allies = [n for n in (ally_names or []) if n]
    probs = []
    for k, (a, b, txt) in enumerate(blocks + ([(0, 0, head)] if head else [])):
        ki = k if k < len(blocks) else -1
        found = False
        for clause in re.split(r"[。；\n]", txt):
            hits = [n for n in allies if n in clause]
            if len(hits) < 2:
                continue
            if _ATTACK_LIKE.search(clause) and not any(f and f in clause for f in (foe_names or [])):
                probs.append((ki, "同伴之间写了攻击同形的动作：%s" % clause.strip()[:40]))
                found = True
                break
            if _FALL_INTO.search(clause):
                probs.append((ki, "写了同伴倒进对方怀里（像挨打）：%s" % clause.strip()[:40]))
                found = True
                break
        # 同一块里既有同伴接触又有同伴出血（血看起来像同伴造成的）
        if not found and _BLOOD_RE.search(txt) and len([n for n in allies if n in txt]) >= 2 \
                and not any(f and f in txt for f in (foe_names or [])):
            probs.append((ki, "同伴接触和同伴出血写在同一块，看起来像同伴打的"))
    return probs


def ally_action_fix(body, ally_names, foe_names):
    """同伴动作像打架 → 只改那一块（或开头段），动作意图改成保护，台词不动，一次。"""
    probs = ally_action_problems(body, ally_names, foe_names)
    if not probs:
        return body
    _dbg("同伴动作像打架", {"问题": [p for _, p in probs][:2]})
    blocks, head, tail = _tl_blocks(body)
    ask_base = ("下面是一段视频提示词里的一块，问题：%s。只重写这一块：把同伴之间的动作改成**明确的保护动作**——"
                "从侧面或身后抓住肩膀往后拉、揽住腰带走、按住后背压低、张开手掌挡在他前面；"
                "**不许**出现伸直手臂、挥出、扑向他、拳、掌击、撞上、倒进怀里这些和打架同形的写法；"
                "同伴身上的伤和血要写明是敌人造成的，或者干脆挪走不写。台词行一字不动，镜头和其他人的动作保留。%s只输出这一块。")
    for ki, why in probs[:2]:
        src = head if ki < 0 else blocks[ki][2]
        keep = "开头保留「%d—%d秒：」。" % (blocks[ki][0], blocks[ki][1]) if ki >= 0 else ""
        says = [l for l in src.splitlines() if "says:" in l]
        try:
            new = str(_q(ask_base % (why, keep), src, mt=700, temperature=0.4) or "").strip()
        except Exception:
            new = ""
        ok = new and all(sl in new for sl in says) and not ally_action_problems(
            new if ki < 0 else new, ally_names, foe_names)
        if ok and (ki < 0 or _TL_BLOCK.match(new)):
            if ki < 0:
                head = _tl_clean(new)
            else:
                blocks[ki] = (blocks[ki][0], blocks[ki][1], _tl_clean(new))
    return "\n\n".join([head] + [x for _, _, x in blocks] + ([tail] if tail else [])).strip()


_CLONE_RE = re.compile(r"[^，。；\n]{0,10}(?:另一分身|分身|另一个[\u4e00-\u9fa5]{2,4}|[\u4e00-\u9fa5]{2,4}（另一[^）]*）)[^，。；\n]{0,20}[，。；]?")


# 【正文里的人名必须是卡上的名字】P244 项目153：绑定块写"艾利安"，
# 正文全程叫"阿离"/"阿利安"。名字对不上，参考图就绑不到人身上，脸必飘。
# 根因在上游（小说正文用"少年/猫娘"不用名字，画面稿继承，到这步模型自己编了一个），
# 上游能不能改好不确定，这里做**确定性兜底**：能改对的直接改，改不对的报出来。

# 人名后面常跟的字（用来认"这是个人名"，不是地名不是物件）
_PERSON_AFTER = ("站", "位于", "走", "面朝", "转", "说", "抬", "伸", "低头", "迈",
                 "保持", "目光", "手中", "身体", "右手", "左手", "双手", "蹲",
                 "坐", "躺", "看", "望", "的视线", "的目光", "背对", "侧身")
# 明确不是人名的（场景/机位词，避免误伤）
_NOT_NAME = ("镜头", "画面", "背景", "前景", "中景", "近景", "远景", "特写", "机位",
             "光线", "场景", "地面", "空气", "声音", "时间", "动作", "位置")


# 会出现在动作词前面、但肯定不是人名的（防止「转身走」把"转身"当成名字）
_NOT_NAME_HEAD = ("转身", "起身", "俯身", "侧身", "回身", "弯腰", "抬头", "低头",
                  "缓缓", "微微", "轻轻", "继续", "同时", "随后", "接着", "然后",
                  "一步", "两步", "三步", "半步", "同步", "快步", "上前", "后退",
                  "视线", "双手", "右手", "左手", "手中", "目光", "身体", "重心")


def _alias_candidates(body, canon):
    """正文里像人名、但不在人物卡上的词。返回 {词: 全文出现次数}，按次数降序。

    两道关，缺一不可：
      · 后面跟着人才会做的动作（站/走/面朝/说…）——**取最短匹配**，
        贪婪会把「阿离转身」整个吃进去，同一个名字被拆成两份就都不够数了。
      · 至少有一次出现在**句首**（「。」「：」「，」换行之后）。
        名字会在句首当主语；「转身」「缓缓」这类只会出现在句子中间。
    """
    import collections
    txt = str(body or "")
    cand = set()
    # {2,4}? 非贪婪：从两个字开始试，够到动作词就停
    for m in re.finditer(r"([\u4e00-\u9fa5]{2,4}?)(?=(%s))" % "|".join(_PERSON_AFTER), txt):
        w = m.group(1)
        if w in canon or w in _NOT_NAME or w in _NOT_NAME_HEAD:
            continue
        if any(w in c or c in w for c in canon if c):      # 是卡上名字的一部分，不算陌生
            continue
        if any(b in w for b in _NOT_NAME) or any(b in w for b in _NOT_NAME_HEAD):
            continue
        cand.add(w)
    hits = collections.Counter()
    for w in cand:
        # 句首出现过才算人名（主语位置）
        if not re.search(r"(?:^|[。：:，,；;\n】）)]\s*)" + re.escape(w), txt):
            continue
        hits[w] = txt.count(w)                             # 按全文次数计，不是按匹配次数
    # 至少出现两次才算"这段一直在用的名字"，一次的多半是路人（守卫、摊主）
    return {w: c for w, c in hits.most_common() if c >= 2}


def fix_body_aliases(body, chars):
    """确定性改名：正文里的化名 → 人物卡上的名字。返回 (新正文, [改了什么])。

    chars 顺序即 Subject 编号（和 h3_prompt 的 head 一致）。
    两条信号都对不上就原样返回，不猜。
    """
    txt = str(body or "")
    canon = [str((c or {}).get("name") or "") for c in (chars or [])]
    canon = [c for c in canon if c]
    if not txt.strip() or not canon:
        return txt, []
    changed = []

    # ① 强信号：正文里「阿离（S1）」而 S1 绑的是艾利安
    for m in list(re.finditer(r"([\u4e00-\u9fa5]{2,4})\s*[（(]\s*S\s*(\d+)\s*[)）]", txt)):
        w, n = m.group(1), int(m.group(2))
        if not (1 <= n <= len(canon)):
            continue
        want = canon[n - 1]
        if w == want or w in canon:
            continue
        if any(c in w for c in canon):
            continue                    # P386a：「面朝老者（S2）」捕到的是「面朝老者」，不是陌生名字——改了会把「面朝」全文吃掉
        if any(b in w for b in _NOT_NAME):
            continue
        if w in want and w != want:
            # P433：「老者」是「青云门老者」的一部分——只改不在全名里的那些，免得叠成「青云门青云门老者」
            _pre = want[:want.index(w)]
            txt = re.sub(r"(?<!%s)%s" % (re.escape(_pre), re.escape(w)), want, txt) if _pre else txt.replace(w, want)
        else:
            txt = txt.replace(w, want)
        changed.append("%s→%s（正文写着 S%d，S%d 绑的是%s）" % (w, want, n, n, want))

    # ② 消去法：正好一个绑定名没出现 + 正好一个陌生人名反复出现
    missing = [c for c in canon if c not in txt]
    if len(missing) == 1:
        cands = _alias_candidates(txt, canon)
        if len(cands) == 1:
            w = list(cands)[0]
            txt = txt.replace(w, missing[0])
            changed.append("%s→%s（%s 在正文里一次没出现，只有%s这一个陌生名字）"
                           % (w, missing[0], missing[0], w))
    if changed:
        _dbg("正文改名", {"改动": changed})
    return txt, changed


def body_name_problems(body, chars):
    """绑定块上的名字在正文里一次都没出现 → 报问题（参考图绑不上人）。"""
    txt = str(body or "")
    canon = [str((c or {}).get("name") or "") for c in (chars or [])]
    canon = [c for c in canon if c]
    if not txt.strip() or not canon:
        return []
    probs = []
    missing = [c for c in canon if c not in txt]
    if missing:
        cands = _alias_candidates(txt, canon)
        extra = ("；正文里在用的名字是：%s" % "、".join(cands)) if cands else ""
        probs.append("绑定了%s，但正文里一次都没提到%s（参考图绑不到人身上，脸会飘）%s"
                     % ("、".join(canon), "、".join(missing), extra))
    return probs


# 画面稿里常见的"没有参考图但确实要出场的人"。按词表认，宁可漏认不错认——
# 漏认只是回到老行为（锁死），错认会让模型凭空加人。
_EXTRA_WORDS = (
    "黑衣人", "蒙面人", "刺客", "杀手", "追兵", "官兵", "士兵", "卫兵", "守卫",
    "师兄弟", "同门", "弟子", "门人", "长老", "掌门", "师傅", "师父", "老者",
    "首领", "头目", "老大", "手下", "随从", "仆人", "丫鬟", "小厮",
    "路人", "行人", "人群", "百姓", "村民", "商贩", "小贩", "摊主", "掌柜",
    "孩童", "老人", "妇人", "男人", "女人", "少年", "少女", "僧人", "道士",
    "马夫", "车夫", "船夫", "乐师", "歌女", "舞女", "战士", "武者",
)


def chars_lock_line(chars, extras=(), extras_scene=False):
    """绑定块里"画面里有哪些角色"那句。三选一（P275）：

    · 点得出名字的配角/群演 → 列出来，让模型照写；
    · 清单说这场戏有没卡的人/动物、但这一段里叫法对不上（清单「奶猫」，画面稿「小猫」）→
      不点名，也**不写死禁止**——写死了这一段就把它们拍没了，而检查器会一直报「只报不修」；
    · 什么配角都没有 → 照旧写死只有这几个角色。

    防重影（三视图被当成三个人）靠「每个只出现一个」那半句，跟禁不禁别人无关。
    """
    names = [str((c or {}).get("name") or "") for c in (chars or [])]
    names = [n for n in names if n]
    who = "、".join(names)
    n = len(names)
    ex = [str(x).strip() for x in (extras or []) if str(x).strip()]
    if ex:
        return ("画面里有参考图的角色只有%s这%d个，每个只出现一个；"
                "参考图是同一个角色的正面、侧面、背面三视图，不是三个人。"
                "除此之外，下面画面里写明的人（%s）照写出来，他们没有参考图，按描述画；"
                "画面里写明之外的人不要凭空加。" % (who, n, "、".join(ex[:6])))
    if extras_scene:
        return ("画面里有参考图的角色只有%s这%d个，每个只出现一个；"
                "参考图是同一个角色的正面、侧面、背面三视图，不是三个人。"
                "下面画面里写明的其他人或动物照写出来，他们没有参考图，按描述画；"
                "画面里写明之外的不要凭空加。" % (who, n))
    return ("画面里只有%s这%d个角色，每个角色只出现一个，不出现任何别的人或生物；"
            "参考图是同一个角色的正面、侧面、背面三视图，不是三个人。" % (who, n))


def fix_pictures_speakers(pics, names=None):
    """画面稿里的台词行，说话人在叫自己的 → 改对。返回 (新画面稿, [改动])。

    【为什么要有这一道】定说话人的候选只有人物卡名单，没有卡的人（师傅/老者/
    首领）根本不在候选里，他的台词只能被安到主角头上——项目155：
    「张林：林儿，带瑶瑶走！」张林在管自己叫林儿（P247）。
    这里把在场但没有卡的人补进候选再判一次。不动上游的解析。
    """
    txt = str(pics or "")
    if not txt.strip():
        return txt, []
    have = [str(n or "").strip() for n in (names or []) if str(n or "").strip()]
    present = have + [w for w in extras_in_shots(txt, have)]
    lines = txt.split("\n")
    changed = []
    for i, ln in enumerate(lines):
        m = _PIC_DLG.match(ln.strip())
        if not m:
            continue
        w, q = m.group(1), m.group(3)
        # 【候选只看前面那一段】拿整篇的泛称当候选，一句话十几个候选永远不唯一，
        # 等于永远改不成。台词的说话人就在紧挨着的那段描写里（P247）。
        _before = "\n".join(lines[max(0, i - 6):i])
        near = [x for x in (have + extras_in_shots(_before, have)) if x in _before]
        near = [x for x in near if x not in _COLLECTIVE]     # 一群人不会说一句台词
        # 【只报不改】"离台词最近的那个人"是判断不是事实（用户 2026-09-10）。
        # 说话人的确定改法由拍摄清单给（每句有原文依据，见 shotlist.fix_pictures_speakers）；
        # 这里只把"在叫自己"这个确定的错报出来。
        for _i, _why, _who in dialogue_speaker_problems([(w, q)], near):
            changed.append("第%d行 说话人可疑：%s%s" % (i + 1, _why, ("（候选：%s）" % _who) if _who else ""))
    return txt, changed


def name_aliases(name):
    """中文名的常见叫法：张林 → 林儿、小林、阿林、张林。用来认"这句话在叫谁"。"""
    n = str(name or "").strip()
    if not n:
        return set()
    out = {n}
    if len(n) >= 2:
        given = n[1:]                      # 去掉姓
        for g in {given, given[-1:]}:
            if not g:
                continue
            out |= {g + "儿", "小" + g, "阿" + g, g + g}
    return {x for x in out if len(x) >= 2}


def _vocative(line):
    """台词开头的称呼：第一个逗号之前那截（没有逗号就整句里的头几个字）。"""
    t = str(line or "").strip()
    head = re.split(r"[，,、！!。.？?：:]", t, 1)[0].strip()
    return head if 1 <= len(head) <= 6 else ""


def dialogue_speaker_problems(pairs, present=None):
    """[(说话人, 台词)] 里，说话人在叫自己的 → 报问题。

    present：这场戏里在场的人（含没有卡的老者/黑衣人首领之类），用来给出可改的候选。
    返回 [(下标, 问题描述, 建议改成谁或空)]。
    """
    out = []
    for i, (w, q) in enumerate(pairs or []):
        w = re.sub(r"（[^）]*）", "", str(w or "")).strip()
        q = str(q or "")
        if not w or not q:
            continue
        voc = _vocative(q)
        if not voc or voc not in name_aliases(w):
            continue
        # 说话人不可能是：这句在叫的人，也不可能是句子里用第三人称提到的人
        ruled = {w}
        for p in (present or []):
            p = str(p or "").strip()
            if p and p != w and p in q:
                ruled.add(p)
        # 【不用全局同义表】师傅和老者是不是同一个人，是这个项目的事实，不该由词表定
        # （换个故事就是两个人——用户 2026-09-10）。候选原样列出，只有**唯一**时才给建议。
        cands = [p for p in (present or []) if str(p or "").strip() and p not in ruled]
        out.append((i, "「%s」开头在叫「%s」，那就是叫说话人自己——%s 不会这么称呼自己"
                    % (q[:18], voc, w), cands[0] if len(cands) == 1 else ""))
    return out


def fix_dialogue_speakers(pairs, present=None):
    """确定性改说话人。返回 (新的 pairs, [改了什么])。改不出唯一解就不动。"""
    pairs = [list(x) for x in (pairs or [])]
    changed = []
    for i, why, who in dialogue_speaker_problems(pairs, present):
        if not who:
            continue
        old = pairs[i][0]
        pairs[i][0] = who
        changed.append("第%d句 %s→%s（%s）" % (i + 1, old, who, why))
    if changed:
        _dbg("台词说话人改正", {"改动": changed})
    return [tuple(x) for x in pairs], changed


# 同一个人的不同叫法——候选去重用（师傅和老者是同一个人，不该算两个候选）
_ROLE_SYNONYM = (
    ("师傅", "师父", "老者", "掌门", "长老", "老人"),
    ("黑衣人", "蒙面人", "首领", "头目", "刺客", "杀手"),
    ("官兵", "士兵", "卫兵", "守卫"),
)


def _role_key(w):
    """把同一个人的不同叫法映到同一个键。"""
    w = str(w or "")
    for grp in _ROLE_SYNONYM:
        if w in grp:
            return grp[0]
    return w


# 一群人，不能当"某一句台词的说话人"
_COLLECTIVE = ("人群", "百姓", "村民", "众人", "同门", "师兄弟", "手下", "随从",
               "行人", "路人", "官兵", "士兵", "弟子", "门人")


def extras_in_shots(shots, names):
    """画面稿里写到、但不在人物卡上的人。返回去重后的词表（保持出现顺序）。

    用来决定绑定块要不要写死"不出现任何别的人"——剧本明明写了黑衣人围攻，
    还锁死就等于把整场冲突戏拍没了（P246）。
    """
    txt = str(shots or "")
    have = [str(n or "") for n in (names or []) if n]
    out = []
    for w in _EXTRA_WORDS:
        if w not in txt:
            continue
        if any(w in n or n in w for n in have):      # 和主角名字重叠的不算配角
            continue
        if w not in out:
            out.append(w)
    # 「少年」「男人」这类泛称，如果画面稿里主角就是用这个称呼的，会误判成配角。
    # 只在它出现次数明显多于一次、且主角名字也在文里时才保留（主角有名字就不会一直用泛称）。
    return out


def unbound_name_problems(prompt_text, all_names):
    """正文里出现了没绑 Subject 的人名 → 报问题。

    没绑参考图的人，H3 只能给他编一张脸；更糟的是模型会为了凑人而复制已绑角色
    （2026-09-05 项目102 段1：「对手林野（另一分身）」）。
    """
    txt = str(prompt_text or "")
    m = re.search(r"画面里只有([^这]{1,40})这\d+个角色", txt)
    if not m:
        return []
    bound = set(x.strip() for x in re.split(r"[、,，]", m.group(1)) if x.strip())
    body = txt[m.end():]
    loose = [n for n in (all_names or []) if n and n not in bound and n in body]
    probs = []
    if loose:
        probs.append("正文里有没绑参考图的人：%s（会被编一张脸）" % "、".join(loose))
    if re.search(r"分身|另一个(?:%s)" % "|".join(re.escape(n) for n in (all_names or []) if n), body):
        probs.append("正文里出现了角色的自我分身")
    return probs


def strip_scene_subject_tags(body, n_chars):
    """把「敌对机甲（S3）」这种**拿场景编号当角色**的写法里的编号去掉。

    n_chars = 人物的 Subject 数量；编号 > n_chars 的都是场景，不是人。
    2026-09-05 项目102 段4：H3 收到「(S3)」就把场景参考板当成一个角色画了出来，
    开头 1.5 秒是空广场，人一个都没有。
    """
    txt = str(body or "")
    try:
        n = int(n_chars or 0)
    except Exception:
        return txt
    if n <= 0:
        return txt
    def _bad(m):
        try:
            return int(m.group(1)) > n
        except Exception:
            return False
    out = re.sub(r"[（(]\s*S\s*(\d+)\s*[)）]",
                 lambda m: "" if _bad(m) else m.group(0), txt)
    out = re.sub(r"<\s*Subject\s*(\d+)\s*>",
                 lambda m: "" if _bad(m) else m.group(0), out)
    out = re.sub(r"[，、]\s*[，、]", "，", out)
    if out != txt:
        _dbg("去掉场景编号当角色", {"人物数": n})
    return out


def drop_clone_clauses(body, names):
    """模型给已绑角色编了个"分身"当第二个人 → 删掉那半句。

    2026-09-05：段1 只绑了一个角色却要写对手，模型写出「对手林野（另一分身）站立于地面」。
    """
    if not re.search(r"分身|另一个(?:%s)" % "|".join(re.escape(n) for n in (names or []) if n), str(body or "")):
        return body
    # 整句删。只删半句会留下没主语的残句（"左侧远处，与机车方隔着约五米。"）
    lines = []
    for ln in str(body).splitlines():
        if not _CLONE_RE.search(ln):
            lines.append(ln)
            continue
        keep = [x for x in re.split(r"(?<=[。！？])", ln) if x and not _CLONE_RE.search(x)]
        lines.append("".join(keep))
    out = "\n".join(lines)
    out = re.sub(r"[，；]{2,}", "，", out)
    out = re.sub(r"[，；]\s*。", "。", out)
    _dbg("删自我复制", {"原长": len(str(body)), "新长": len(out)})
    return out


def drop_empty_say_lines(body):
    """删掉空的「XX说：」残行（后面没有 says 内容）。"""
    lines = str(body or "").splitlines()
    out = []
    for i, l in enumerate(lines):
        if re.fullmatch(r"\s*[\u4e00-\u9fa5]{1,6}(?:S\d)?说[：:]\s*", l):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if "says:" not in nxt:
                continue
            if "说：" in nxt or "说:" in nxt:
                continue
        out.append(l)
    return "\n".join(out)


def deep_video_director(settings=None):
    """普通镜头是否启用深度导演。老项目没有该字段时也按新默认启用。"""
    mode = str((settings or {}).get("video_director_mode") or "standard").strip().lower()   # P365：默认关
    return mode in ("deep", "深度", "深度导演")


def _deep_video_director_instruction(settings=None):
    if not deep_video_director(settings):
        return ""
    extra = ""
    if "网红自然感" in str((settings or {}).get("style") or ""):
        extra = "\n人物表现像真实随手拍：动作有轻微停顿和反应，不摆拍，不做夸张广告式表演。"
    return """【深度导演模式】
先在内部推演本段的人物动机、动作先后、道具状态、对白反应和与上一段的交接，再只输出最终 H3 时间轴提示词，不输出分析过程。
不改剧情，不新增人物、地点、事件或隐藏设定。每段只承担一个清楚的戏剧任务，其余动作都为它服务。
开头必须接住上一段最后可见的姿势、位置、视线和手中物；结尾必须留下下一段能够直接承接的可见状态。
先让观众看懂谁要做什么，再给关键动作和反应留时间；环境和外貌只随动作带出，不抢主要剧情。""" + extra


def _pov_scrub(body, owner):
    """P461b：第一视角——拍摄者的口型提示和「我/X位于画面…」站位句去掉（镜头后面的人没有画面位置）。"""
    if not owner:
        return body
    t = str(body or "")
    t = t.replace("%s嘴唇开合。" % owner, "").replace("我嘴唇开合。", "")
    t = re.sub(r"[^。；\n]*(?:我|" + re.escape(owner) + r")(?:位于|站在|坐在|处于|在)画面[^。；\n]*[。；]", "", t)
    t = re.sub(r"[^。；\n]*(?:我|" + re.escape(owner) + r")(?:的)?(?:脸|面部|表情|眉头|嘴角|眼神|目光)[^。；\n]*[。；]", "", t)
    return re.sub(r"[ \t]+\n", "\n", t)


def _h3_timeline_body(shots, chars, settings=None, prev_state="", seconds=None, plan=None, scene_name="", scene_space="", camera_owner=""):
    """时间轴式正文：一次调用 + 守卫；朝向不合格的块只改那一块再写一次。返回 (body, tail_state)。"""
    from . import seam_v2 as _sv2
    from . import camera_view
    mode, _ = _pov_spec(settings)
    _has_director = bool(isinstance(settings, dict) and (settings.get("_beat") or {}).get("director"))
    if mode in camera_view.MODES and not _has_director:
        # Subject numbering is already reference-sorted; owner comes from original cards.
        who = camera_owner or camera_view.owner(chars, settings)
        idx = {c["name"]: i for i, c in enumerate(chars, 1)}
        dialogue = []
        for speaker, quote in _pic_parts_dialogue(shots):
            speaker = re.sub(r"（[^）]*）", "", str(speaker or "")).strip()
            if speaker in idx:
                i = idx[speaker]
                quote = str(quote).strip().strip('“”「」"')
                dialogue.append("%s说：<Subject %d> (S%d) says:<d>[Chinese]%s</d>" % (speaker, i, i, quote))
        body, tail = camera_view.generate(mode, who, shots, chars, seconds, prev_state,
                                         scene_name, scene_space, dialogue, _q)
        _h3_timeline_body.last_tail = tail
        _h3_timeline_body.last_relay = None
        _h3_timeline_body.last_problems = [("视点检查：" + x) for x in (getattr(camera_view.generate, "last_problems", []) or [])]   # P456：记问题不丢批
        return body, tail
    _ins_name = os.environ.get("V41_TL_INS") or "画面稿改H3提示词_时间轴_指令词.txt"
    if isinstance(settings, dict) and settings.get("_beat"):
        _ins_name = "节拍段_H3提示词_指令词.txt"   # P372：一拍一段用通用节拍模板（一个机位、按秒、只演这一件事）
        if (settings.get("_beat") or {}).get("director"):
            _ins_name = "导演段_H3提示词_指令词.txt"   # P435：导演段——模型只扩写骨架的时间块
    ins = _fill(_ins(_ins_name), settings or {}, None,
                scale_layers=("shot",))     # P330：实验可换指令词；P360：尺度的镜头层进这里（【SCALE】）
    if mode in camera_view.MODES and _has_director:
        ins += _h3_pov_rules(settings, camera_owner or camera_view.owner(chars, settings))   # P461：导演段 + 视点规矩
    _deep_director = deep_video_director(settings)
    if _deep_director:
        ins += "\n\n" + _deep_video_director_instruction(settings)
    idx = {c["name"]: i for i, c in enumerate(chars, 1)}
    _rd = str((settings or {}).get("_ride") or "")
    if _rd:
        ins += "\n\n【同乘】%s。画面里只有这一辆，两人写成一前一后同一个轮廓，不许各骑一辆、不许分处两侧。" % _rd
    # 节奏档决定镜头倾向（设计 v2 第三节）；「影片类型」的镜头倾向退为兜底
    _pc = str((settings or {}).get("_pace") or "")
    if _pc:
        try:
            from . import pace as _pace_mod
            ins += "\n\n【这一话的节奏：%s】镜头倾向：%s" % (_pc, _pace_mod.rules(_pc)["shot_bias"])
            if _pc == "日常":
                ins += " 这一话没有敌人也可以：不要为了「有冲突」硬写一个威胁进来；每块一个看得见的动作、一个变化点就够。"
        except Exception:
            pass
    if (settings or {}).get("_no_strike"):
        ins += ("\n\n【对峙而不打】主角面对对手**一次都不出手**：分侧、空地、远景照常，"
                "但所有交锋动作换成压住的动作（手按在剑柄上没抽、指节攥紧又松开、上前一步又停住）。写出手就是错。")
    try:
        from . import oral_story as _os454
        _al454 = _os454.card_aliases([c for c in chars if isinstance(c, dict)])
        _also = {n: "、".join(a for a, full in _al454.items() if full == n and a != n and a != _os454.short_name(n) and len(a) >= 2)[:40] for n in idx}
    except Exception:
        _also = {}
    ins += ("\n\n【人物编号】\n" + "\n".join("%s ＝ Subject %d ＝ S%d%s%s" % (n, i, i, ("（%s）" % body_scale_line(c)) if is_nonhuman(c) else "",
                                                                    ("（剧本里也叫：%s，都是这一个人）" % _also[n]) if _also.get(n) else "")   # P454⑬：称呼＝同一个人
                                             for (n, i), c in zip(idx.items(), chars)))
    try:
        from . import kits as _kt
        _gsb = _kt.genre_shot_block(str((settings or {}).get("genre") or ""))
        if _gsb:
            ins += "\n\n" + _gsb
    except Exception:
        pass
    _bt = (settings or {}).get("_beat") if isinstance(settings, dict) else None
    _dd = (_bt or {}).get("director") if _bt else None
    if _dd:
        from . import director_shots as _ds
        _dd = _ds.fit_dialogue_timing(_dd)
        ins += "\n\n【本段骨架（【机位】行照抄；块数、每块起止秒、每块的事按这个；标了台词的块末写谁嘴唇开合）】\n" + _ds.skeleton_text(_dd, idx)
        if not _dd.get("cast"):
            ins += "\n【本段是空镜】画面里没有人，只拍景和物。"
            prev_state = "（空镜：这一段没有人）"
    elif _bt:
        # P366：一拍一段——景别、0 秒动作、新地方先全景、硬切不接上一段
        ins += ("\n\n【本段是一拍（只演一件事）】景别：%s。0 秒第一句就是这个动作：「%s」——不铺垫、不先摆站位、不先看环境。"
                "全段只演这一件事和对方的反应，做完就停在可见的姿势上。" % (_bt.get("shot") or "中景", _bt.get("act") or ""))
        if _bt.get("establish"):
            ins += "这是到一个新地方的第一拍：开头 2 秒先给全景交代地点和人在哪，再拉到%s。" % (_bt.get("shot") or "中景")
        if _bt.get("cut"):
            ins += ("本段是硬切开始：【上一段结束时的状态】只用来沿用每个人现在的位置、姿势（躺着/坐着/站着）和手里的东西，"
                    "不接上一段的画面，第一块按这一拍自己的机位和景别起。")
        if _bt.get("empty"):
            ins += ("\n【本拍是空镜】画面里一个人都没有：只拍这一拍写的景和物（光、雾、烛火、陈设的细微变化），全程不出现任何人物，"
                    "不写上一段的人在哪、不让人入画。「这一段结束时」只写「景别」和「正在进行」两行。")
            prev_state = "（空镜：这一段没有人）"          # P386b：不给上一段的人物位置，模型照着就会写人
        _pace = str(_bt.get("pace") or "")
        if _pace == "慢":
            ins += ("\n【文戏拍】这一拍是慢节奏：机位固定不动、景别不变；分成 3 块（8 秒以上的段每块 3～4 秒）；每块只写一个小动作（递、接、看、点头、转头），动作幅度小；"
                    "台词第一句放第一块末尾、第二句放第二块末尾；说完的人保持姿势听对方说；不写走位、不写起身、不写大动作；结尾停在两人的姿势上。")
        elif _pace == "快":
            ins += ("\n【动作拍】这一拍是快节奏：每块必须有一个新的、看得见的动作，0 秒就动；不写「保持 / 静止 / 停顿 / 等待 / 维持姿势」；"
                    "动作句连着写，不写表情心理；台词只有一句时放在动作中间说，两句时先说的放第一块、后说的放第二块；最后一块停在动作的冲击点上（手落下、身体撞上、脚踩实）。")
        _dir = str((settings or {}).get("_direction") or "").strip()
        if _dir:
            ins += ("\n\n【这一段怎么拍——用户写的，优先级最高，照办】\n" + _dir[:600] +
                    "\n里面写了的景别、角度、机位在哪、运动方式、谁在画面哪边、台词在第几秒，原样体现在【机位】行和时间块里；"
                    "写了的动作替代【这一拍】里的动作，没写到的仍按【这一拍】。")
    ins += _h3_pov_rules(settings, chars[0]["name"] if chars else "")
    # 台词行原文（按人物编号拼好）
    say_lines = []
    for w, q in _pic_parts_dialogue(shots):
        w = re.sub(r"（[^）]*）", "", str(w or "")).strip()
        i = idx.get(w)
        if i:
            say_lines.append("%s说：<Subject %d> (S%d) says:<d>[Chinese]%s</d>" % (w, i, i, str(q).strip().strip("“”「」\"")))
    secs = int(round(float(seconds or 10)))
    _layout = (settings or {}).get("_scene_layout") or []
    _layout_hd = ("【这个地方的位置（戏区）——按骨架中的动作写出起点、经过和终点，构图跟随机位】" if _dd else
                 "【这个地方的位置（戏区）——写清每个人在哪个位置；这一拍只演一件事，人不用换位置】" if (isinstance(settings, dict) and settings.get("_beat"))
                  else "【这个地方的位置（戏区）——每一块写清每个人在哪个位置，随事情推进要在这些位置之间移动】")
    _layout_txt = ("\n" + _layout_hd + "\n" + "\n".join("· " + x for x in _layout)) if _layout else ""
    _wrow = (settings or {}).get("_wardrobe_row") if isinstance(settings, dict) else None
    _wtxt = ""
    if isinstance(_wrow, dict):
        from . import wardrobe as _wd
        _wl = []
        for c in chars or []:
            _nm = str(c.get("name") or "")
            _ln = _wd.state_line(_wrow, _nm) if _nm in _wrow else ""
            if _ln:
                _wl.append("· %s：%s" % (_nm, _ln))
        if _wl:
            _wtxt = "\n【本段衣着（每一块照这个写身上的样子；有变化的那一块写出脱/换的动作）】\n" + "\n".join(_wl)
    _seam_txt = ""
    if _sv2.on():
        _seam_txt = (_ds.director_seam_brief(_dd) if _dd else
                     _sv2.seam_brief((settings or {}).get("_seam") or {}, [c["name"] for c in chars]))
        scene_space = _sv2.strip_cast_sentences(scene_space, [c["name"] for c in chars])                  # 场景卡里混进的人物姿势句不给导演
    user = ("【时长】约 %d 秒（最后一块的结束秒数写 %d）\n【场景】%s；%s%s%s\n【人物卡摘要】\n%s\n【上一段结束时的状态】%s\n%s\n【画面稿】\n%s\n\n【台词行原文（一字不改、连格式放进对应时间块，不再用引号写一遍）】\n%s"
            % (secs, secs, scene_name, str(scene_space or "")[:300], _layout_txt, _wtxt, _chars_digest(chars, _wrow, (settings or {}).get("_ledger_people") if isinstance(settings, dict) else None)[:1400],
               (prev_state or "（全片开头，从画面稿第一句直接开始）") + _seam_txt,     # P343：接上一话时 prev_state 是上一话结束时的状态
               (plan_block(plan) if (plan and not os.environ.get("V41_TL_NOPLAN") and not _sv2.on()) else ""), str(shots or ""), "\n".join(say_lines) if say_lines else "无"))
    # 深度思考只在整话分镜表做一次。逐段已经拿到目的/动作/站位，再开思考会
    # 每段先耗尽推理额度、随后退回普通生成，既慢也没有把思考结果用进成品。
    body = str(_q(ins, user, mt=(3200 if _deep_director else 2400),
                  temperature=(0.55 if _deep_director else 0.6),
                  reasoning=False) or "").strip()
    body = _tl_norm_heads(body)                                     # P408：小数块头归整数
    if isinstance(settings, dict) and settings.get("_beat"):
        body = _tl_ensure_block(body, seconds)                      # P409：一个块都没写 → 机位行描述做成一块
    if _dd:
        # P435：机位行、块头、台词位置、结尾状态全按导演骨架钉死；模型只贡献每块的画面描述
        _by_d = {}
        _flat = [x for _a, _b, _t, _ds_ in (_dd.get("blocks") or []) for x in _ds_]
        for _x, _sq in zip(_flat, _dd.get("says") or []):
            _nm_, _q_ = _sq[0], _sq[1]
            _i = idx.get(_nm_) or next((v for k_, v in idx.items() if re.sub(r"（[^）]*）", "", k_) == _nm_), None)
            if _i:
                _by_d[_x] = "%s说：<Subject %d> (S%d) says:<d>[Chinese]%s</d>" % (re.sub(r"（[^）]*）", "", _nm_), _i, _i, str(_q_).strip().strip("“”「」\""))
        body = _ds.assemble(body, _dd, chars, _by_d, scene_space=scene_space)
        say_lines = [v for _k_, v in sorted(_by_d.items())]
    body = _tl_clean(_tl_fix_dialogue(body, say_lines))
    body = strip_alien_garments_text(body, shots)
    body = _tl_scale_each_block(body, chars)
    _orig_blocks = [t for _, _, t in _tl_blocks(body)[0]]                # P322④：守卫重写前的原块，带进离场地点词就回退
    _loc_corpus = str(shots or "") + str(scene_name or "") + str(scene_space or "") + str(prev_state or "")
    _orig_blocks = [t for _, _, t in _tl_blocks(body)[0]]                # P322④：守卫重写前的原块，带进离场地点词就回退
    _loc_corpus = str(shots or "") + str(scene_name or "") + str(scene_space or "") + str(prev_state or "")
    # 承接守卫：第一块要接住上一段的动作，不是重新摆站位（用户 2026-09-04：没衔接的段闪一下很突兀）
    _seam_cfg = (settings or {}).get("_seam") or {}
    if (str(prev_state or "").strip() and not (_sv2.on() and (_seam_cfg.get("scene_changed") or _seam_cfg.get("ep_first")))
            and not (isinstance(settings, dict) and (settings.get("_beat") or {}).get("cut"))):        # P407：硬切的拍不接上一段动作
        _bl, _hd, _tl2 = _tl_blocks(body)
        if _bl:
            _first = _bl[0][2]
            _cont = re.search(r"承接|接着|继续|顺势|延续|仍在|未收|悬(?:停|在)|正(?:在|要)|随即|紧接", _first)
            _static = re.search(r"(?:位于|站在|跪在|蹲在)[^。]{0,20}(?:画面|左侧|右侧|中央)", _first) and not _cont
            if (not _cont) or _static:
                ask = ("下面是一段视频提示词的第一个时间块。上一段结束时的状态是：%s\n"
                       "只重写这一块：**第一句必须是上一段那个正在进行的动作的下半截**（悬着的手砸下来、举起的杖迎上去、滑退的人撞到墙），"
                       "不许重新介绍谁站在哪、不许让人重新摆好姿势。里面的台词行一字不动，动作和镜头保留。开头保留「%d—%d秒：」。只输出这一块。"
                       % (str(prev_state)[:(600 if _sv2.on() else 200)], _bl[0][0], _bl[0][1]))
                try:
                    _new = str(_q(ask, _first, mt=700, temperature=0.4) or "").strip()
                except Exception:
                    _new = ""
                _says0 = [l for l in _first.splitlines() if "says:" in l]
                if _new and _TL_BLOCK.match(_new) and all(sl in _new for sl in _says0):
                    _bl[0] = (_bl[0][0], _bl[0][1], _tl_clean(_new))
                    body = "\n\n".join([_hd] + [x for _, _, x in _bl] + ([_tl2] if _tl2 else [])).strip()
                    _dbg("时间轴第一块改承接", {"上一段": str(prev_state)[:40]})
    # 对峙空间守卫：敌我分侧 + 远景 + 距离（用户 2026-09-04：石头人和主角同一侧，以为是友军）
    # 同乘一辆载具：清掉同伴的所有格载具（"苏拉的机车"），别让画面里多出一辆
    _ride = str((settings or {}).get("_ride") or "")
    if _ride:
        _m = re.match(r"([\u4e00-\u9fa5]{2,4})和([\u4e00-\u9fa5]{2,4})同乘一辆([\u4e00-\u9fa5]{1,5})", _ride)
        if _m:
            from . import story_plan as _spr
            _b = _spr.drop_ally_vehicle(body, _m.group(2), _m.group(3))
            if _b != body:
                body = _b
                _dbg("同乘清洗", {"同伴": _m.group(2)})
    _foes, _allies, _heroes = _sides_of(chars, settings)
    # 【只有真有对手时才跑】原来无条件跑：这一话要素表是空的、_foes 为空，
    # 判据却只看"有没有远景、有没有写距离"——一场送面包的戏当然没有，
    # 于是修复器往画面深处补了一个「对手（未列入人物表）…作为威胁静默凝视」，
    # 还把两个人拉开四步、两拍全打成全景（P228 实测段2、段5）。
    _foe_txt0 = str((settings or {}).get("_foe_text") or "").strip()
    if _foes or _foe_txt0:
        body = confrontation_fix(body, _foes, prev_state, foe_desc=_foe_txt0)
    # 同伴动作像打架（用户 2026-09-05：女主上来给男主一拳把他打吐血）
    body = strip_scene_subject_tags(body, len(chars))
    body = drop_clone_clauses(body, [c["name"] for c in chars])
    body = drop_empty_say_lines(ally_action_fix(body, _allies, _foes))
    # 主角和同伴被写成分处两侧（2026-09-05 追逐戏：同乘一辆摩托的两人被分到画面左右）
    # _allies 里含主角，这里必须刨掉——否则「林野与机甲分处两侧」这种**正确**的敌我分侧
    # 会因为主角同时命中两个名单而被判成误改。
    body = ally_side_fix(body, _heroes, [n for n in _allies if n not in _heroes])
    # 朝向守卫：不合格的块只改那一块
    # 导演骨架已有机位与走位；旧守卫会把脸部特写/移动镜头重写成全身站位介绍。
    probs = [] if _dd else _tl_facing_problems(body, [c["name"] for c in chars])
    if probs:
        blocks, head, tail = _tl_blocks(body)
        for k, why in probs[:2]:
            a, b, txt = blocks[k]
            # 这一段没有对手时，别把威胁和被保护者塞进修复要求里——
            # 实测一场喝茶聊天的戏被写成「面朝门外威胁来源」「防御姿态」（P206）
            _how = ("补上或改正每个人的站位与朝向——保护别人的人身体挡在威胁和被保护者之间、面朝威胁，"
                    "被攻击的人面朝攻击来的方向。" if _foes else
                    "补上每个人的站位与朝向——谁在屋里的哪个位置、和别人隔几步、脸朝着谁。"
                    "这一段没有敌人，写这一段真正在发生的事就行。")
            ask = ("下面是一段视频提示词里的一个时间块，问题：%s。只重写这一块：保持里面的动作、台词行（如果有）一字不动，"
                   "%s开头保留「%d—%d秒：」。只输出这一块。" % (why, _how, a, b))
            try:
                new = str(_q(ask, txt, mt=600, temperature=0.4) or "").strip()
            except Exception:
                new = ""
            if new and _TL_BLOCK.match(new) and not _TL_BAD_FACING.search(new) and all(sl in new for sl in say_lines if sl in txt):
                blocks[k] = (a, b, _tl_clean(new))
                _dbg("时间轴块改朝向", {"块": k + 1, "问题": why})
        body = "\n\n".join([head] + [t for _, _, t in blocks] + ([tail] if tail else [])).strip()
    # 姿势守卫：承接段说他坐着，镜头里就不许突然站着（P229）。
    # 和上面的朝向守卫同一套：只重写出问题的那一块，动作和台词行一字不动。
    _beat_flag = bool(isinstance(settings, dict) and settings.get("_beat"))
    _guard_state = beat_pose_state(shots, prev_state, [c["name"] for c in chars]) if _beat_flag else ""   # P387②：这一拍写的姿势变化优先
    _pc = tl_pose_conflicts(body, [c["name"] for c in chars], _guard_state)
    if _pc and _beat_flag:
        # P377：节拍段——程序直接换词，不让模型重写（重写常被拒收，矛盾留着）
        body = fix_pose_words(body, _pc, _guard_state, [c["name"] for c in chars])
        _dbg("节拍段姿势改词", {"冲突": [(k, nm, want, got) for k, nm, want, got in _pc]})
        _pc = []
    if _pc:
        blocks, head, tail = _tl_blocks(body)
        for k, nm, want, got in _pc[:2]:
            if k >= len(blocks):
                continue
            a, b, txt = blocks[k]
            ask = ("下面是一段视频提示词里的一个时间块。上一段结束时%s是**%s着**的，"
                   "这一块却把%s写成了%s着，中间没有交代他改变姿势。"
                   "只重写这一块：让%s保持%s着（或者补上他改变姿势的那个动作），"
                   "他所在的家具和位置跟着承接段走；"
                   "里面的动作、台词行（如果有）一字不动，开头保留「%d—%d秒：」。只输出这一块。"
                   % (nm, want, nm, got, nm, want, a, b))
            try:
                new_b = str(_q(ask, txt, mt=600, temperature=0.4) or "").strip()
            except Exception:
                new_b = ""
            if new_b and _TL_BLOCK.match(new_b) and all(sl in new_b for sl in say_lines if sl in txt):
                blocks[k] = (a, b, _tl_clean(new_b))
                _dbg("时间轴块改姿势", {"块": k + 1, "人": nm, "应为": want, "原写": got})
        body = "\n\n".join([head] + [t for _, _, t in blocks] + ([tail] if tail else [])).strip()
    _v2_problems = []
    _is_beat = bool(isinstance(settings, dict) and settings.get("_beat"))
    if _is_beat:
        pass        # P373：节拍段——模板已按上一段状态起手，不再让接缝守卫按旧时间轴格式重写第一块（它会吃掉【机位】行）
    elif _sv2.on():
        # P331：同场景＝接力帧 + 开头段复述 + 不重演；换场景＝出场拍/入场拍
        body, _v2_problems = _sv2.apply_guards(body, chars, say_lines, settings, prev_state, scene_name, scene_space, _q)
    else:
        # P322②：谁左谁右、景别 要接上一段结束时的状态；翻了就以上一段为准重写第一块，还不对就记问题
        body = _tl_inherit_staging(body, prev_state, [c["name"] for c in chars], say_lines)
        # P322③：接力帧纪律——最后一块不许收在特写/近景（多人在场时），改成能看见全部在场人物的中景/全景
        if not os.environ.get("V41_TL_NO_RELAY_TAIL"):
            body = _tl_relay_tail(body, [c["name"] for c in chars], say_lines)
    # P322④/P324④：守卫重写（含上面两次）把画面稿/场景卡里没有的地点词带进来 → 回退原块；放在所有重写之后
    body = _tl_revert_foreign_places(body, _orig_blocks, _loc_corpus)
    body = strip_alien_garments_text(body, shots)             # 所有模型修补之后再守一次
    _h3_timeline_body.last_problems = (list(_v2_problems) if _sv2.on() else list(getattr(_tl_inherit_staging, "last_problems", []) or [])) + _tl_position_problems(body, [c["name"] for c in chars], _layout)
    # 【放在所有注入之后】上面每一道修复都可能把秒数改乱、把台词重复写一遍。
    body = split_say_lines(body)                                   # P393：台词行单独成行（夹在段落里 H3 念不清）
    body = dedup_say_lines(body)
    body = scrub_implied_speech(body, say_lines)                   # P395：隐含说话句删、台词前补口型、台词紧贴时间块
    if _is_beat and not _dd:
        body = beat_split_long_block(body, seconds)                # P407/P408：只写了一块（≥8 秒或两句台词）→ 对半拆
        body = hoist_say_lines(body, say_lines)                    # P396：节拍段台词提到第一块末尾（挂在最后一块后面会先胡言）；P407 按原拍顺序
    if _is_beat:
        body = beat_scrub(body, say_lines, scene_space)              # P374：多出的台词行、室外场景里的屋内句
        _known = " ".join([str(shots or ""), str(scene_space or "")] + [" ".join(str(c.get(k) or "") for k in ("clothing", "appearance_details", "other", "look_full")) for c in chars])
        body = drop_alien_weapons(body, _known)                      # P375：模型编的刀剑棍
        if not _dd:
            body = beat_fix_shot(body, (settings.get("_beat") or {}).get("shot"))   # P383：景别按这一拍
        _bt = settings.get("_beat") or {}
        _pc = str(_bt.get("pace") or "")
        _nm0 = [c["name"] for c in chars]
        if _dd:
            pass                                                                   # P435：导演段的走位/保持都按骨架，不按快慢档删句
        elif _pc == "快":
            body = scrub_clauses(body, _HOLD_WORDS, names=_nm0)                    # P404 快档：删保持/静止/等待句
        if _pc == "慢":
            body = scrub_clauses(body, _MOVE_WORDS, keep_if_in=shots, names=_nm0)  # P404 慢档：这一拍没写的走位删掉
        else:
            body = beat_scrub_moves_fast(body, shots, _nm0)                        # P407b 快档：原文里没走的人不许走
        body = beat_drop_alien_force(body, shots, _nm0)                            # P407/P410：原文没写的砸/撞/猛地、抓着别人
        body = beat_scrub_examples(body)                                           # P407：抄进来的指令示例句
        if settings.get("_direction"):
            body = force_direction(body, settings.get("_direction"))          # P397：口述里的景别/角度/运动钉进【机位】行
        _nm = [c["name"] for c in chars]
        if _bt.get("empty"):
            body = beat_drop_people(body, _bt.get("cast") or [])              # P385：空镜里不许有人
            body = beat_empty_tail(body, _bt.get("act"))                      # P407：已完成＝这一拍原文
        else:
            body = beat_drop_alien_poses(body, shots, prev_state, _nm)        # P386c：编的坐下/跪下删掉
            if not _dd:
                body = beat_ensure_act(body, _bt.get("act"))                  # P407：原文动作被跳过 → 放回第一块
            body = beat_scrub_offbeat_actions(body, [n for n in _nm if n in (str(_bt.get("who") or "") + " " + str(_bt.get("act") or ""))], _nm, prev_state)   # P408：不在这一拍的人不摸不拿
            body = beat_fix_hands(body, str(scene_space or ""), _nm,
                                  per_known={c["name"]: " ".join(str(c.get(k) or "") for k in ("clothing", "appearance_details", "other", "look_full")) for c in chars},
                                  beat_text=shots, prev_state=prev_state)          # P407/P408：编的道具，按人查
            body = beat_drop_premature_entry(body, shots)                     # P411：原文没进门就不许在门内
            if not _dd:
                body = beat_fix_sides(body, _nm)                              # P407：左右按第一块
            body = beat_fix_subjects(body, _nm, _bt.get("act"))               # P407：缺主语补人
            if not _dd:
                body = beat_tail_pose_from_body(body, _nm)                    # P433：状态行姿势按最后一块（导演段结尾是程序写的）
        body = beat_even_blocks(body, seconds)                                # P423：不足 2 秒的块平均重排
        body = fix_mouth_cue_speaker(body)                                    # P425：口型提示的人＝说话人
        if not _TL_BLOCK.search(body):
            # P426：守卫跑完仍一个时间块都没有（模型只写了机位行和台词行）→ 用这一拍原文兜一块，台词行跟在后面
            _act0 = str(_bt.get("act") or "").strip().rstrip("。") or "画面里只有环境的细微变化"
            _secs0 = int(float(seconds or 5) + 0.5) or 5
            _ls = body.split("\n")
            _says = [l for l in _ls if "says:<d>" in l]
            _rest = [l for l in _ls if "says:<d>" not in l]
            _k = next((i for i, l in enumerate(_rest) if l.strip().startswith("【机位】")), -1)
            _blk = ["", "0—%d秒：%s。" % (_secs0, _act0)] + _says + [""]
            _rest[_k + 1:_k + 1] = _blk
            body = "\n".join(_rest)
        if _dd:
            from . import director_shots as _ds2
            body = _ds2.assemble(body, _dd, chars, _by_d, scene_space=scene_space)   # P439b：朝向/姿势守卫让模型重写过块 → 机位/块头/台词/左右/结尾再钉一次
            if mode == "第一视角POV":
                body = _pov_scrub(body, camera_owner or camera_view.owner(chars, settings))   # P461b
            body = _ds2.final_tidy(body)                                      # P439：口型只留一个
        body = beat_tidy(body)                                                # P407：残句
    body = tl_fill_gaps(body, seconds)
    _, _, tail = _tl_blocks(body)
    tail_state = re.sub(r"^\s*这一段结束时[：:]\s*", "", tail).strip()[:400] if tail else ""
    _h3_timeline_body.last_tail = tail_state
    if _sv2.on() and tail:
        _blk_last = _tl_blocks(body)[0]
        if _blk_last:
            tail = _sv2.reconcile_frame(tail, _blk_last[-1][2])
    _h3_timeline_body.last_relay = _sv2.parse_relay(tail, [c["name"] for c in chars]) if (_sv2.on() and tail) else None
    if _sv2.on() and tail:
        tail_state = str(_sv2.relay_text(_h3_timeline_body.last_relay) or tail_state)[:600]    # 模板全文给下一段
        _h3_timeline_body.last_tail = tail_state
    return body, tail_state


# ─────────── P322 段间连贯守卫（确定性；只在判定命中时才叫一次模型改一块） ───────────
_PLACE_W = re.compile(r"房间|客厅|卧室|厨房|浴室|阳台|窗边|窗前|落地窗|地毯|沙发|茶几|床边|床上|餐桌|门口|街道|街上|广场|花园|院子|走廊|楼梯|电梯|车内|车里|办公室|教室|大厅|殿|庭院")
_SIDE_W = re.compile(r"(画面)?(左|右)(侧|边|方)")


_INDOOR_WORDS = re.compile(r"屋内|室内|墙壁|墙角|门框|门口内|屋顶|棚顶|床铺|床沿|床上|炕|灶台|窗棂")


_WEAPON_RE = re.compile(r"柴刀|短刀|匕首|长剑|铁剑|木剑|木棍|长棍|长枪|弓箭|斧头|镰刀|佩刀|佩剑|大刀|长刀|钢刀|宝剑")


_SHOT_W = re.compile(r"全景|远景|中景|近景|特写|大特写")


_DIR_SHOT = (("全景", r"全景|远景|大全景"), ("特写", r"特写|近景|大特写"), ("中景", r"中景|中近景|半身"))
_DIR_ANGLE = (("仰拍", r"仰拍|仰视|低角度|低机位"), ("俯拍", r"俯拍|俯视|高角度|高机位|鸟瞰"), ("平视", r"平视|平拍|齐眼"))
_DIR_MOVE = (("固定机位", r"固定(?:机位|镜头)?|不动"), ("手持微晃", r"手持(?:微晃)?"), ("缓慢推近", r"缓慢推近|推近|推进|推镜"), ("缓慢拉远", r"缓慢拉远|拉远|拉开|拉镜"),
             ("跟拍", r"跟拍|跟随|跟着"), ("横摇", r"横摇|摇镜|摇过"), ("环绕", r"环绕"))


def direction_camera(direction):
    """口述拍法 → (景别, 角度, 运动)，没写的为空。"""
    t = str(direction or "")
    pick = lambda table: next((k for k, rx in table if re.search(rx, t)), "")
    return pick(_DIR_SHOT), pick(_DIR_ANGLE), pick(_DIR_MOVE)


def force_direction(body, direction):
    """P397：用户口述里写了的景别/角度/运动，原样钉进【机位】行（写手写了别的就换掉）；景别同时改结尾「景别」行。"""
    shot, angle, move = direction_camera(direction)
    if not (shot or angle or move):
        return body
    lines = str(body or "").splitlines()
    for i, ln in enumerate(lines):
        st = ln.strip()
        if st.startswith("【机位】"):
            rest = st[len("【机位】"):]
            for want, table in ((shot, _DIR_SHOT), (angle, _DIR_ANGLE), (move, _DIR_MOVE)):
                if want:
                    for _, rx in table:
                        rest = re.sub(rx, "", rest)
            rest = re.sub(r"^[，、,\s]+|[，、,\s]{2,}", "，", rest).strip("，、, ")
            rest = re.sub(r"[，、,]+([。；])", r"", rest)
            head = "，".join(x for x in (shot, angle, move) if x)
            lines[i] = "【机位】" + head + ("，" + rest if rest else "")
            break
    out = "\n".join(lines)
    return beat_fix_shot(out, shot) if shot else out


def beat_fix_shot(body, shot):
    """节拍段：【机位】行和结尾「景别」按这一拍定的景别写（P383 真机：口述说全景/特写，模型 22 段全写成中景）。"""
    shot = str(shot or "").strip()
    if shot not in ("全景", "中景", "特写"):
        return body
    lines = str(body or "").splitlines()
    for i, ln in enumerate(lines):
        st = ln.strip()
        if st.startswith("【机位】"):
            rest = st[len("【机位】"):]
            if shot not in rest:
                rest = _SHOT_W.sub(shot, rest) if _SHOT_W.search(rest) else (shot + "，" + rest)
            lines[i] = "【机位】" + rest
        elif re.match(r"^[·•]\s*景别[：:]", st):
            lines[i] = re.sub(r"(景别[：:]\s*).*$", lambda m: m.group(1) + shot, ln)
    return "\n".join(lines)


def drop_alien_weapons(body, known_text):
    """这一拍、人物卡、场景卡里都没提的兵器/工具词，模型写进来就是编的（P375 项目217：第 1 段凭空挂柴刀）→ 删含它的短句；
    「这一段结束时」状态行里的「手里X」改成「手里无」。"""
    known = str(known_text or "")
    alien = {w for w in set(_WEAPON_RE.findall(str(body or ""))) if w not in known}
    if not alien:
        return body
    out = []
    for line in str(body or "").splitlines():
        st = line.strip()
        if not any(w in st for w in alien):
            out.append(line)
            continue
        if "｜" in st:                                                   # 状态行：手里X → 手里无
            for w in alien:
                st = re.sub(r"手里[^｜]*" + re.escape(w) + r"[^｜]*", "手里无", st)
            out.append(st)
            continue
        _hm = re.match(r"^(\d+(?:\.\d+)?\s*[—\-–~]+\s*\d+(?:\.\d+)?\s*秒[：:])", st)
        _head, _rest = (_hm.group(1), st[_hm.end():]) if _hm else ("", st)
        parts = re.split(r"(?<=[，。；])", _rest)
        st2 = "".join(p for p in parts if not any(w in p for w in alien))
        if st2.strip() or _head:
            out.append(_head + st2)
    return "\n".join(out)


_POSE_MOVE = re.compile(r"坐下|坐在|坐到|坐进|落座|跪下|跪坐|跪地|跪在|单膝跪|双膝跪|蹲下|蹲在|蹲身|躺下|躺到|躺在|趴下|趴在")


def _pose_key(w):
    return "躺" if ("躺" in w or "趴" in w) else ("跪" if "跪" in w else ("蹲" if "蹲" in w else "坐"))


def beat_drop_alien_poses(body, beat_text, prev_state, names):
    """节拍段：这一拍没写、上一段状态里也没有的姿势变化（坐下/跪下/蹲下/躺下）是模型编的 → 删含它的短句（P386c）；
    结尾状态行的姿势栏若带这种姿势，改回上一段那个人的姿势（没有就写站立）。"""
    allow = set()
    for src in (str(beat_text or ""), str(prev_state or "")):
        for ch in ("坐", "跪", "蹲", "躺", "趴", "卧"):
            if ch in src:
                allow.add("躺" if ch in ("躺", "趴", "卧") else ch)
    blocks, head, tail = _tl_blocks(body)
    if not blocks:
        return body
    dropped = 0
    for k, (a, b, txt) in enumerate(blocks):
        _hm = re.match(r"^(\d+(?:\.\d+)?\s*[—\-–~]+\s*\d+(?:\.\d+)?\s*秒[：:])", txt.strip())
        _head, _rest = (_hm.group(1), txt.strip()[_hm.end():]) if _hm else ("", txt)
        parts = re.split(r"(?<=[，。；])", _rest)
        keep = []
        for p in parts:
            if "says:<d>" in p:
                keep.append(p)
                continue
            m = _POSE_MOVE.search(p)
            if m and _pose_key(m.group(0)) not in allow:
                dropped += 1
                continue
            keep.append(p)
        txt2 = _head + "".join(keep)
        if not "".join(keep).strip():
            txt2 = txt                                                  # 删空了就留原块
        blocks[k] = (a, b, txt2)
    if tail:
        lines = []
        for ln in tail.splitlines():
            m = re.match(r"^([·\s]*)([^：:｜\n]{1,8})[：:]\s*((?:[^｜\n]*｜){3})([^｜\n]*)(.*)$", ln)
            if m and m.group(2).strip() in (names or []):
                pose = m.group(4)
                pm = _POSE_MOVE.search(pose) or re.search(r"坐|跪|蹲|躺|趴|卧", pose)
                if pm and _pose_key(pm.group(0)) not in allow:
                    nm = m.group(2).strip()
                    old = re.search(r"^[·\s]*" + re.escape(nm) + r"[：:]\s*(?:[^｜\n]*｜){3}([^｜\n]*)", str(prev_state or ""), re.M)
                    pose = old.group(1).strip() if old and old.group(1).strip() else "站立"
                    ln = m.group(1) + m.group(2) + "：" + m.group(3) + pose + m.group(5)
                    dropped += 1
            lines.append(ln)
        tail = "\n".join(lines)
    if not dropped:
        return body
    return "\n\n".join([head] + [t for _, _, t in blocks] + ([tail] if tail else [])).strip()


_HOLD_WORDS = re.compile(r"保持[^，。；]{0,6}(姿势|不动|静止|姿态)|静止不动|一动不动|维持[^，。；]{0,4}姿势|无新动作|无其他动作|停顿|等待[^，。；]{0,6}(回应|反应|回答)|画面静止")
_MOVE_WORDS = re.compile(r"走向|走到|走进|走出|跑向|冲向|退后|后退|上前|转身|站起|起身|坐下|蹲下|绕到|挪到|迈步|踱步")


def scrub_clauses(body, rx, keep_if_in="", names=None):
    """P404：删时间块里命中 rx 的短句（台词行、结尾状态不动）。keep_if_in 给了文本时，短句里的词在这段文本里出现就留。P432：主语回接。"""
    lines = str(body or "").splitlines()
    out = []
    keep_src = str(keep_if_in or "")
    for line in lines:
        st = line.strip()
        if not st or "says:<d>" in st or st.startswith("这一段结束时") or st.startswith("【") or st.startswith("·") or st.startswith("<Subject"):
            out.append(line)
            continue
        _hm = re.match(r"^(\d+(?:\.\d+)?\s*[—\-–~]+\s*\d+(?:\.\d+)?\s*秒[：:])", st)
        _head, _rest = (_hm.group(1), st[_hm.end():]) if _hm else ("", st)
        parts = [p for p in re.split(r"(?<=[，。；！？])", _rest) if p]
        flags = []
        for p in parts:
            m = rx.search(p)
            flags.append(not (m and not (keep_src and any(mm.group(0)[:2] in keep_src for mm in rx.finditer(p)))))
        kept = _reattach_subjects(parts, flags, names)
        body_txt = "".join(kept).strip()
        if _head and not body_txt:
            body_txt = _rest
        out.append(_head + body_txt if _head else body_txt)
    return "\n".join(out)


def beat_drop_people(body, names):
    """空镜拍：块里带人名/人称的短句删掉，结尾的人物行删掉（P385）。删空的块保留一句环境。"""
    names = [str(n or "") for n in (names or []) if str(n or "").strip()]
    if not names:
        return body
    rx = re.compile("|".join(map(re.escape, names)) + r"|（S\d）|\bS\d\b|他|她|人物|少年|老人|老者|身影"
                    r"|面朝|双脚|双手|手臂|头部|四肢|身体|膝盖|双腿|肩|目光|眼神|呼吸|跨入|门槛|站立|坐在|跪|蹲|迈|脚步|指尖|嘴唇|眉"
                    r"|步伐|行进|走向|走到|走进|停在|停下|面向|两人|确认|发力|入画|转身|准备|昏迷|反应|竹筒|背着|托|扶"
                    r"|有一人|一人|三人|多人|主体|站位|品字形|三角|人影|背对|正对镜头|镜头前"
                    r"|拇指|食指|手指|右手|左手|手掌|掌心|手背|左腿|右腿|嘴角|神情|表情|发丝|头发|发辫|麻花辫|重心|眼睛|眼眸|瞳孔|脸颊|额头|鼻尖|下巴"
                    r"|摩挲|握住|攥|衣袖|斗篷|背包|筒身|地图筒|护腕|皮靴|衬衫|马甲|长裤")   # P386b/P387④/P407/P410：没名字的身体句、站位句、随身物句也删
    out = []
    for line in str(body or "").splitlines():
        st = line.strip()
        if re.match(r"^[·•]\s*(%s)[：:]" % "|".join(map(re.escape, names)), st):
            continue
        if not rx.search(st) or st.startswith("这一段结束时") or st.startswith("【"):
            out.append(line)
            continue
        _hm = re.match(r"^(\d+(?:\.\d+)?\s*[—\-–~]+\s*\d+(?:\.\d+)?\s*秒[：:])", st)
        _head, _rest = (_hm.group(1), st[_hm.end():]) if _hm else ("", st)
        parts = re.split(r"(?<=[，。；])", _rest)
        kept = "".join(p for p in parts if not rx.search(p))
        if _head and not kept.strip():
            kept = "画面里没有人，只有环境的细微变化。"
        if kept.strip() or _head:
            out.append(_head + kept)
    return "\n".join(out)


def _beat_join(head, blocks, tail):
    return "\n\n".join(([head] if head else []) + [t for _, _, t in blocks] + ([tail] if tail else [])).strip()


_SUBJ_NAME_RE = None


def _reattach_subjects(parts, keep_flags, names):
    """P432：删短句时，被删短句开头的主语名字接到同一句里下一个保留、且没有主语的短句前面。返回保留的短句列表。"""
    names = [n for n in (names or []) if n]
    out, pending = [], ""
    for p, keep in zip(parts, keep_flags):
        m = re.match(r"^\s*(%s)(?:（S\d）|\(S\d\))?" % "|".join(map(re.escape, names)), p) if names else None
        if keep:
            if pending and not m and not re.match(r"^\s*(他|她|两人|三人|四人|众人|大家|画面|镜头|背景|阳光|海|风|光)", p):
                p = pending + p.lstrip()
            pending = ""
            out.append(p)
        else:
            if m:
                pending = m.group(1)
        if p.rstrip().endswith(("。", "！", "？", "；")):
            pending = ""
    return out


def _scrub_pred(body, pred, names=None):
    """时间块里命中 pred(短句) 的短句删掉（台词行、结尾状态、【】行不动）。P432：删掉带主语的短句时，主语接到下一个短句。"""
    out = []
    for line in str(body or "").splitlines():
        st = line.strip()
        if not st or "says:<d>" in st or st.startswith("这一段结束时") or st.startswith("【") or st.startswith("·") or st.startswith("<Subject"):
            out.append(line)
            continue
        _hm = re.match(r"^(\d+(?:\.\d+)?\s*[—\-–~]+\s*\d+(?:\.\d+)?\s*秒[：:])", st)
        _head, _rest = (_hm.group(1), st[_hm.end():]) if _hm else ("", st)
        parts = [p for p in re.split(r"(?<=[，。；！？])", _rest) if p]
        kept = _reattach_subjects(parts, [not pred(p) for p in parts], names)
        body_txt = "".join(kept).strip()
        if _head and not body_txt:
            body_txt = _rest
        out.append(_head + body_txt if _head else body_txt)
    return "\n".join(out)


_BEAT_EXAMPLES = re.compile(r"悬着的手砸下来|举起的杖迎上去|滑退的人撞到墙")


_CAM_META = re.compile(r"上一段|本段|这一拍|要求|理解为|不额外|指令|按【|【本段|硬切起|接力|第一句|即为|景别直接|不换")


def beat_scrub_examples(body):
    """P407：指令词里的示例句被原样抄进正文 → 删；【机位】里「继承上一段…视角」删；P409：【机位】里模型的推理话（「上一段为特写，本段按…要求」）删。"""
    body = scrub_clauses(body, _BEAT_EXAMPLES)
    lines = str(body or "").splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith("【机位】"):
            ln = re.sub(r"[，,]?继承上一段[^，。；]*", "", ln)
            rest = re.sub(r"「[^」]*」", "", ln.strip()[len("【机位】"):])          # 引句整体先去掉，免得被逗号劈成两半
            parts = [p for p in re.split(r"(?<=[，。；])", rest) if p.strip()]
            keep = [p for p in parts if not _CAM_META.search(p) and "」" not in p and "「" not in p]
            if keep and len(keep) < len(parts):
                ln = "【机位】" + "".join(keep).strip("，、 ")
                if not ln.endswith(("。", "；")):
                    ln += "。"
            lines[i] = ln
    return "\n".join(lines)


def _tl_ensure_block(body, seconds):
    """P409：模型一个时间块都没写（把画面全写进【机位】行）→ 机位行第一句留作机位，其余做成「0—N秒：」一块。"""
    t = str(body or "")
    if _TL_BLOCK.search(t):
        return t
    secs = int(float(seconds or 5) + 0.5) or 5
    lines = t.splitlines()
    for i, ln in enumerate(lines):
        st = ln.strip()
        if st.startswith("【机位】"):
            rest = st[len("【机位】"):]
            parts = [p for p in re.split(r"(?<=。)", rest) if p.strip()]
            cam, desc = (parts[0], "".join(parts[1:])) if len(parts) > 1 else (rest, "")
            lines[i] = "【机位】" + cam.strip()
            desc = desc.strip() or "画面里没有人，只有环境的细微变化。"
            ins = ["", "0—%d秒：%s" % (secs, desc)]
            if i + 1 < len(lines) and lines[i + 1].strip():
                ins.append("")
            lines[i + 1:i + 1] = ins
            return "\n".join(lines)
    return t


_FORCE_VERB = re.compile(r"砸|撞|甩|踹|踢|拽|扑|摔|捶|撕|劈|砍|刺|掐")
_GRAB = re.compile(r"拉住|抓住|抓着|拉着|拽住|拽着|扶住|扶着|抱住|搂住|搂着")     # P410：人对人的抓拉扶抱，原文没写就是编的（对象是物件不算）
_FORCE_ADV = re.compile(r"猛地|猛然|狠狠地?|重重地?")
_BODY_WORD = re.compile(r"手|指|臂|脚|腿|身体|肩|头|膝")


def beat_drop_alien_force(body, beat_text, names=None):
    """P407：这一拍原文没写的砸 / 撞 / 甩 / 拽（人做的）→ 删那个短句；原文没有猛 / 狠 / 重重 → 副词去掉。
    （项目218 第 18 段：原文「指尖碰到水晶」写成「悬停的手猛地砸向水晶」）
    P410b：原文没有抓拉拽扶搂抱，正文里「抓着莉娅衣袖」这种对人的接触 → 删短句（对象是地图筒/斗篷不算）。"""
    src = str(beat_text or "")
    verbs = {w for w in set(_FORCE_VERB.findall(str(body or ""))) if w not in src}
    names = [n for n in (names or []) if n]
    grab_ok = bool(re.search(r"拉|抓|拽|扶|搂|抱|靠在一起|搀", src))

    def _pred(p):
        if "says:<d>" in p:
            return False
        if any(w in p for w in verbs) and bool(_BODY_WORD.search(p)):
            return True
        if not grab_ok and names:
            m = _GRAB.search(p)
            if m and any(n in p[m.end():m.end() + 8] for n in names):
                return True
            if m and re.search(r"(?:他|她)(?:的)?", p[m.end():m.end() + 3]):
                return True
        return False
    if verbs or (not grab_ok and names):
        body = _scrub_pred(body, _pred, names)
    if not re.search(r"猛|狠|重重|用力|一把", src):
        lines = []
        for ln in str(body or "").splitlines():
            if "says:<d>" in ln or ln.strip().startswith("·"):
                lines.append(ln)
            else:
                lines.append(_FORCE_ADV.sub("", ln))
        body = "\n".join(lines)
    return body


_ACT_VERBS = re.compile(r"跑|走进|走出|走到|走上|走过|走|凑过去|凑|伸手|伸出|推开|推|拉住|拉|转身|转过身|抬头|低头|后退|退|摸|放下|放|抽出|摊开|指向|指|碰到|碰|收回|靠|叹|笑|蹲|坐|跪|跳|爬|拿|递|接|回头|挥|招手|打开|抱|背|扶|捡|踩|敲|喘|望|停")


_MOVE_ANY = re.compile(r"走向|走到|走进|走出|走上|走|跑|冲|扑|退后|后退|退|上前|转身|站起|起身|坐下|蹲下|绕到|挪到|迈步|迈|踱步|凑|来到|进|出来|过去|跳|爬|离开|回头")


_SUBJ_LEAD = re.compile(r"^[\s（(]*(?:但|而|随后|随即|然后|接着|此时|同时|与此同时|之后|最后|于是|只有|只见)?[，,]?\s*")


def _clause_subjects(text, names):
    """按短句给主语：名字在句首（可带「但/随后」这类连接词）才算主语，「A和B」两人都算；句中出现的名字是宾语不算；没有主语就沿用上一句的。返回 [(短句, {名字})]。"""
    out, last = [], set()
    for p in [x for x in re.split(r"(?<=[，。；！？])", str(text or "")) if x.strip()]:
        body = _SUBJ_LEAD.sub("", p)
        here = set()
        for n in names:
            i = body.find(n)
            if i < 0:
                continue
            if i <= 1 or re.fullmatch(r"[\s（(]*[^\s，。]{0,8}?(?:和|与|、|及)\s*", body[:i]):
                here.add(n)
        if here:
            last = here
        out.append((p, set(last)))
    return out


def beat_scrub_moves_fast(body, beat_text, names):
    """P407b 快档：这一拍原文里没走位的人，正文里给他写的走位句删掉（第 14 段原文只有莉娅进门，模型让艾登「转身面向门外」）。
    原文里走了的人（上前 / 走进 / 后退）照写，同义词不删。"""
    names = [n for n in (names or []) if n]
    if not names:
        return body
    movers = set()
    for p, subs in _clause_subjects(beat_text, names):
        if _MOVE_ANY.search(p):
            movers |= subs
    if movers >= set(names):
        return body
    lines, out = str(body or "").splitlines(), []
    for line in lines:
        st = line.strip()
        if not st or "says:<d>" in st or st.startswith("这一段结束时") or st.startswith("【") or st.startswith("·") or st.startswith("<Subject"):
            out.append(line)
            continue
        _hm = re.match(r"^(\d+(?:\.\d+)?\s*[—\-–~]+\s*\d+(?:\.\d+)?\s*秒[：:])", st)
        _head, _rest = (_hm.group(1), st[_hm.end():]) if _hm else ("", st)
        _cs = _clause_subjects(_rest, names)
        kept = _reattach_subjects([p for p, _ in _cs], [not (_MOVE_WORDS.search(p) and subs and not (subs & movers)) for p, subs in _cs], names)
        body_txt = "".join(kept).strip()
        if _head and not body_txt:
            body_txt = _rest
        out.append(_head + body_txt if _head else body_txt)
    return "\n".join(out)


def beat_ensure_act(body, act):
    """P407：这一拍原文的动作词在正文里一个都找不到（跑 / 走进 / 凑过去被模型跳过、从做完的状态起写）→ 把原文动作句放到第一块开头。"""
    act = str(act or "").strip()
    blocks, head, tail = _tl_blocks(str(body or ""))
    if not act or not blocks:
        return body
    prose = "\n".join(t for _, _, t in blocks)
    verbs = []
    for m in _ACT_VERBS.finditer(act):
        if m.group(0) not in verbs:
            verbs.append(m.group(0))
    missing = [w for w in verbs if w not in prose]
    if not missing:
        return body
    a, b, txt = blocks[0]
    lines = txt.split("\n")
    m0 = _TL_BLOCK.match(lines[0])
    hd, rest = lines[0][:m0.end()], lines[0][m0.end():].lstrip()
    lines[0] = hd + act.rstrip("。；，") + "。" + rest
    blocks[0] = (a, b, "\n".join(lines))
    return _beat_join(head, blocks, tail)


_HAND_ACT = re.compile(r"伸出|伸手|触碰|触到|轻触|碰到|碰|按在|按住|按压|点在|点上|抓住|抓起|拿起|握住|递|接过|推|拉住|举起|抬手|摸|插|扣住|拽")


def beat_scrub_offbeat_actions(body, who_names, names, prev_state=""):
    """P408：这一拍「谁」里没有的人，只许看、站、面朝，不许伸手摸碰拿（第 11 段「艾登的手和符文」模型让莉娅也去点符文）。
    删他做主语的手上动作短句；结尾状态他的「手里」栏带触/按/点/摸 → 手里无。"""
    who = [n for n in (who_names or []) if n]
    names = [n for n in (names or []) if n]
    others = [n for n in names if n not in who]
    if not who or not others:
        return body
    blocks, head, tail = _tl_blocks(str(body or ""))
    if not blocks:
        return body
    # 上一段这个人手里已经有的东西（拿着地图）不算新动作
    held = {}
    for n in others:
        m = re.search(r"^[·\s]*" + re.escape(n) + r"[：:]\s*[^｜\n]*｜[^｜\n]*｜([^｜\n]*)", str(prev_state or ""), re.M)
        core = _HAND_STRIP.sub("", m.group(1)) if m else ""
        held[n] = [core[j:j + 2] for j in range(len(core) - 1)] if len(core) >= 2 else []
    changed = 0
    out = []
    for a, b, txt in blocks:
        lines = txt.split("\n")
        m0 = _TL_BLOCK.match(lines[0])
        hd, rest = lines[0][:m0.end()], lines[0][m0.end():]
        kept = []
        for p, subs in _clause_subjects(rest, names):
            if (subs and subs <= set(others) and _HAND_ACT.search(p) and "says:<d>" not in p
                    and not any(w in p for n in subs for w in held.get(n, []))):
                changed += 1
                continue
            kept.append(p)
        new = "".join(kept).strip()
        lines[0] = hd + (new if new else rest)
        out.append((a, b, "\n".join(lines)))
    if tail:
        tl = []
        for ln in tail.splitlines():
            m = re.match(r"^([·\s]*)([^：:｜\n]{1,8})[：:]\s*([^｜\n]*｜[^｜\n]*｜)([^｜\n]*)(.*)$", ln)
            if (m and m.group(2).strip() in others and re.search(r"触|按|点|摸|抓住|握住", m.group(4))
                    and not any(w in m.group(4) for w in held.get(m.group(2).strip(), []))):
                ln = m.group(1) + m.group(2) + "：" + m.group(3) + "手里无" + m.group(5)
                changed += 1
            tl.append(ln)
        tail = "\n".join(tl)
    return _beat_join(head, out, tail) if changed else body


_HAND_STRIP = re.compile(r"手里|双手|右手|左手|拿着|握着|抓着|抓住|抱着|按在|按着|扶着|托着|捧着|提着|挂在|搭在|悬停|悬浮|摊开|发光|的|上方|腰间|胸前|身前|（[^）]*）|\([^)]*\)|[上了无一把张颗只块根支着在，、,。 ]")


def _in(core, src):
    return bool(core) and len(core) >= 2 and any(core[j:j + 2] in str(src or "") for j in range(len(core) - 1))


def beat_fix_hands(body, known, names, per_known=None, beat_text="", prev_state=""):
    """P407/P408：结尾状态「手里X」按人查——X 要在这一拍原文、场景卡、**本人**的卡或本人上一段状态里；
    卡上写成「挂/佩/系」在身上的东西（腰间挂的罗盘）不算手里，除非原文写了。查不到 → 手里无，正文里带它的短句删。"""
    known = str(known or "")
    per_known = per_known or {}
    lines = str(body or "").splitlines()
    alien = set()
    for i, ln in enumerate(lines):
        m = re.match(r"^([·\s]*)([^：:｜\n]{1,8})[：:]\s*([^｜\n]*｜[^｜\n]*｜)([^｜\n]*)(.*)$", ln)
        if not (m and m.group(2).strip() in (names or [])):
            continue
        nm = m.group(2).strip()
        if (re.search(r"抓|拉|拽|扶|搂|抱", m.group(4)) and any(o in m.group(4) for o in names if o != nm)
                and not re.search(r"抓|拉|拽|扶|搂|抱|靠在一起|搀", str(beat_text or ""))):
            lines[i] = m.group(1) + m.group(2) + "：" + m.group(3) + "手里无" + m.group(5)     # P410：原文没写抓着别人
            continue
        core = _HAND_STRIP.sub("", m.group(4))
        if len(core) < 2:
            continue
        card = str(per_known.get(nm) or "")
        mine = re.search(r"^[·\s]*" + re.escape(nm) + r"[：:][^\n]*", str(prev_state or ""), re.M)
        ok = _in(core, beat_text) or _in(core, known) or _in(core, mine.group(0) if mine else "")
        if not ok and _in(core, card):
            worn = any(re.search(r"[挂佩系戴][^。；]{0,8}" + re.escape(core[j:j + 2]), card) for j in range(len(core) - 1))
            ok = not worn
        if not ok:
            alien.add(core)
            lines[i] = m.group(1) + m.group(2) + "：" + m.group(3) + "手里无" + m.group(5)
    body = "\n".join(lines)
    if alien:
        body = scrub_clauses(body, re.compile("|".join(map(re.escape, sorted(alien, key=len, reverse=True)))))
    return body


def beat_fix_sides(body, names):
    """P407：每个人在画面左 / 右按第一块写的为准——后面的块和结尾状态里写反了就改回来（第 4/6/9/16/18/24 段块与块之间左右对调）。"""
    blocks, head, tail = _tl_blocks(str(body or ""))
    if not blocks:
        return body
    first = blocks[0][2]
    side = {}
    for n in (names or []):
        m = re.search(re.escape(n) + r"[^。；，]{0,16}?画面(左|右)侧", first) or re.search(r"(?:^|[。；，])\s*画面(左|右)侧[，、]?\s*" + re.escape(n), first, re.M)
        if m:
            side[n] = m.group(1)
    _two = [n for n in (names or []) if n]
    if len(side) == 1 and len(_two) == 2:
        _k = next(iter(side))
        side[[n for n in _two if n != _k][0]] = "右" if side[_k] == "左" else "左"    # 两人戏：知道一个就推另一个
    if not side or (len(side) >= 2 and len(set(side.values())) == 1):
        return body
    changed = 0

    def _fix(txt):
        nonlocal changed
        for n, sd in side.items():
            def _r(m):
                nonlocal changed
                if m.group(2) != sd:
                    changed += 1
                return m.group(1) + sd + m.group(3)
            txt = re.sub(r"(" + re.escape(n) + r"[^。；，]{0,16}?画面)(左|右)(侧)", _r, txt)

            def _r2(m):
                nonlocal changed
                if m.group(2) != sd:
                    changed += 1
                return m.group(1) + sd + m.group(3)
            txt = re.sub(r"((?:^|[。；，])\s*画面)(左|右)(侧[，、]?\s*" + re.escape(n) + ")", _r2, txt, flags=re.M)
        return txt
    blocks = [(a, b, _fix(t) if k else t) for k, (a, b, t) in enumerate(blocks)]
    if tail:
        tl = []
        for ln in tail.splitlines():
            m = re.match(r"^([·\s]*)([^：:｜\n]{1,8})[：:]", ln)
            if m and m.group(2).strip() in side:
                sd = side[m.group(2).strip()]
                if re.search(r"画面(左|右)侧", ln) and ("画面%s侧" % sd) not in ln:
                    changed += 1
                ln = re.sub(r"画面(左|右)侧", "画面%s侧" % sd, ln)
            tl.append(ln)
        tail = "\n".join(tl)
    return _beat_join(head, blocks, tail) if changed else body


_BODY_START = re.compile(r"^(双手|双臂|右手|左手|手指|手掌|身体|目光|眼神|头部|嘴唇|双脚|脚步|肩膀|胸口|眉头|嘴角|手臂|指尖)")


def beat_fix_subjects(body, names, act=""):
    """P407：开头是「双手自然垂落…」这种没主语的块 → 第一块补这一拍原文里第一个名字，后面的块补上一块最后提到的人（第 12 段 9—12 秒）。"""
    blocks, head, tail = _tl_blocks(str(body or ""))
    if not blocks:
        return body
    _pos0 = [(str(act or "").find(n), n) for n in (names or []) if n and n in str(act or "")]
    last, out, changed = (min(_pos0)[1] if _pos0 else None), [], 0
    for k, (a, b, txt) in enumerate(blocks):
        lines = txt.split("\n")
        m0 = _TL_BLOCK.match(lines[0])
        rest = lines[0][m0.end():].lstrip()
        if last and _BODY_START.match(rest) and not any(rest.startswith(n) for n in (names or [])):
            lines[0] = lines[0][:m0.end()] + last + rest
            changed += 1
        pos = [(txt.rfind(n), n) for n in (names or []) if n in txt]
        if pos:
            last = max(pos)[1]
        out.append((a, b, "\n".join(lines)))
    return _beat_join(head, out, tail) if changed else body


def beat_empty_tail(body, act):
    """P407：空镜段的「本段已完成」写这一拍原文（模型抄了第 1 段的溪水）。"""
    blocks, head, tail = _tl_blocks(str(body or ""))
    act = str(act or "").strip().rstrip("。；")
    if not tail or not act:
        return body
    lines = []
    for ln in tail.splitlines():
        if re.match(r"^[·•]\s*(本段已完成|已完成)[：:]", ln.strip()):
            ln = re.sub(r"((?:本段已完成|已完成)[：:]\s*).*$", lambda m: m.group(1) + act, ln)
        lines.append(ln)
    return _beat_join(head, blocks, "\n".join(lines))


def beat_split_long_block(body, seconds=None):
    """P407：慢档 8 秒以上只写了一块 → 按句对半拆成两块（12 秒一块两句台词，模型容易发呆或乱嘴）。台词行留在前一块。"""
    blocks, head, tail = _tl_blocks(str(body or ""))
    if len(blocks) != 1:
        return body
    a, b, txt = blocks[0]
    if b - a < 8 and str(body or "").count("says:<d>") < 2:
        return body
    lines = txt.split("\n")
    m0 = _TL_BLOCK.match(lines[0])
    prose, extra = lines[0][m0.end():].strip(), lines[1:]
    cue = ""
    mc = re.search(r"[^\s，。；]{1,8}嘴唇开合。?$", prose)
    if mc:
        cue, prose = mc.group(0), prose[:mc.start()]
    sents = [x for x in re.split(r"(?<=[。！？])", prose) if x.strip()]
    if len(sents) < 2:
        return body
    h = (len(sents) + 1) // 2
    mid = a + (b - a) // 2
    first = "%d—%d秒：%s%s" % (a, mid, "".join(sents[:h]), cue)
    second = "%d—%d秒：%s" % (mid, b, "".join(sents[h:]))
    new = "\n".join([first] + extra) + "\n\n" + second
    return "\n\n".join(([head] if head else []) + [new] + ([tail] if tail else [])).strip()


_ENTER_WORDS = re.compile(r"门内|门里|屋内|跨过门槛|跨入|进入门|走进门|没入门|进了门")


def beat_drop_premature_entry(body, beat_text):
    """P411：这一拍原文没写进门（推门 / 开门不算进），正文里写人已在门内、跨过门槛 → 删短句（提前演下一拍，第 13 段推门就写成站在门内）。"""
    src = str(beat_text or "")
    if re.search(r"进|入|里去|里面|屋内|门内", src):
        return body
    body = _scrub_pred(body, lambda p: bool(_ENTER_WORDS.search(p)) and "says:<d>" not in p, None)
    out = []
    for ln in str(body or "").splitlines():
        m = re.match(r"^([·•]\s*正在进行[：:]\s*)(.*)$", ln.strip())
        if m and (_ENTER_WORDS.search(m.group(2)) or re.search(r"进入|迈步进|跨进", m.group(2))):
            parts = [p for p in re.split(r"(?<=[，。；])", m.group(2)) if p.strip()]
            keep = [p for p in parts if not (_ENTER_WORDS.search(p) or re.search(r"进入|迈步进|跨进", p))]
            ln = m.group(1) + ("".join(keep).strip("，； ") or "停在这一拍的最后一个动作上")
        out.append(ln)
    return "\n".join(out)


def beat_even_blocks(body, seconds=None):
    """P423：8 秒以上的段里出现不足 2 秒的块（「0—1秒：」）→ 各块时长平均重排，顺序和内容不动。"""
    blocks, head, tail = _tl_blocks(str(body or ""))
    if len(blocks) < 2:
        return body
    total = int(float(seconds or blocks[-1][1]) + 0.5) or blocks[-1][1]
    if total < 8 or not any(b - a < 2 for a, b, _ in blocks):
        return body
    n = len(blocks)
    edges = [round(total * i / n) for i in range(n + 1)]
    out = []
    for k, (a, b, txt) in enumerate(blocks):
        lines = txt.split("\n")
        m0 = _TL_BLOCK.match(lines[0])
        lines[0] = "%d—%d秒：" % (edges[k], edges[k + 1]) + lines[0][m0.end():]
        out.append((edges[k], edges[k + 1], "\n".join(lines)))
    return _beat_join(head, out, tail)


def fix_mouth_cue_speaker(body):
    """P425：台词行前一句「X嘴唇开合」的 X 必须是这句台词的说话人（模型写「张明嘴唇开合」后面却是小林说）。"""
    lines = str(body or "").split("\n")
    for i, ln in enumerate(lines):
        m = re.match(r"^\s*([^\s：:]{1,8})说[：:]<Subject", ln)
        if not m or i == 0:
            continue
        spk = m.group(1)
        prev = lines[i - 1]
        if "says:<d>" in prev or prev.strip().startswith("·"):
            continue
        lines[i - 1] = re.sub(r"([^\s，。；]{1,8})(嘴唇开合)", lambda mm: (spk if mm.group(1) != spk and not mm.group(1).endswith(spk) else mm.group(1)) + mm.group(2), prev)
    return "\n".join(lines)


_LIE = re.compile(r"躺|平躺|卧|趴")
_SIT = re.compile(r"坐|端坐|跪")


def beat_tail_pose_from_body(body, names):
    """P433：结束状态的姿势栏按最后一块正文——正文里这个人还躺着/坐着，状态行却写「站立」→ 改成躺/坐（老者重伤躺着，状态行写站立）。"""
    blocks, head, tail = _tl_blocks(str(body or ""))
    if not blocks or not tail:
        return body
    last = blocks[-1][2]
    lines, changed = [], 0
    for ln in tail.splitlines():
        m = re.match(r"^([·\s]*)([^：:｜\n]{1,8})[：:]\s*((?:[^｜\n]*｜){3})([^｜\n]*)(.*)$", ln)
        if m and m.group(2).strip() in (names or []) and re.search(r"站", m.group(4)):
            nm = m.group(2).strip()
            seg = " ".join(re.findall(re.escape(nm) + r"[^。；]{0,30}", last))
            if seg and _LIE.search(seg) and not re.search(r"站起|起身|坐起", seg):
                ln = m.group(1) + nm + "：" + m.group(3) + re.sub(r"站[立着姿]?", "躺着", m.group(4)) + m.group(5); changed += 1
            elif seg and _SIT.search(seg) and not re.search(r"站起|起身", seg):
                ln = m.group(1) + nm + "：" + m.group(3) + re.sub(r"站[立着姿]?", "坐着", m.group(4)) + m.group(5); changed += 1
        lines.append(ln)
    return _beat_join(head, blocks, "\n".join(lines)) if changed else body


def beat_tidy(body):
    """P407：剪掉台词后留下的「；。」「……”」、没配对的引号、缺内容的括号；行尾孤零零的分号。
    P411：以冒号结尾、后面没跟台词行的短句（「兴奋地开口：」）删。"""
    out = []
    raw = str(body or "").splitlines()
    for idx, ln in enumerate(raw):
        if ln.strip().startswith("【机位】"):
            if ln.count("（") != ln.count("）"):
                ln = ln.replace("（", "").replace("）", "")                          # P433：机位行括号不配对
            if ln.count("(") != ln.count(")"):
                ln = ln.replace("(", "").replace(")", "")
            out.append(ln)
            continue
        if "says:<d>" in ln or ln.strip().startswith("【") or ln.strip().startswith("<Subject"):
            out.append(ln)
            continue
        if _TL_BLOCK.match(ln) or (out and not ln.strip().startswith("·") and not ln.strip().startswith("这一段结束时")):
            nxt = raw[idx + 1] if idx + 1 < len(raw) else ""
            if ln.rstrip().endswith("：") and "says:<d>" not in nxt and not _TL_BLOCK.match(ln.strip()[:12]) and not ln.strip().startswith("这一段结束时"):
                ln = re.sub(r"[^，。；！？]*：\s*$", "", ln.rstrip())
            elif ln.rstrip().endswith("：") and "says:<d>" not in nxt and _TL_BLOCK.match(ln):
                m0 = _TL_BLOCK.match(ln)
                rest = re.sub(r"[^，。；！？]*：\s*$", "", ln[m0.end():].rstrip())
                ln = ln[:m0.end()] + rest
        ln = re.sub(r"…+[”\"]", "", ln)
        if ln.count("“") != ln.count("”"):
            ln = ln.replace("“", "").replace("”", "")
        if ln.count('"') % 2 == 1:
            ln = ln.replace('"', "")
        ln = re.sub(r"[；;]\s*[。.]", "；", ln)
        ln = re.sub(r"，\s*。", "。", ln)
        ln = re.sub(r"。{2,}", "。", ln)
        ln = re.sub(r"[，；]\s*([）)])", r"\1", ln)
        ln = re.sub(r"[（(]\s*[）)]", "", ln)
        ln = re.sub(r"[；，]\s*$", "", ln)
        ln = re.sub(r"([：:])\s*[；。]+\s*$", r"\1", ln)
        ln = re.sub(r"^(\s*)[。，；、]+", r"\1", ln)                                 # P432：行首孤立标点
        if not ln.strip() and raw[idx].strip():
            continue                                                          # 去掉标点后空了的行删
        if ln.count("「") != ln.count("」"):
            ln = ln.replace("「", "").replace("」", "")
        if ln.count("（") != ln.count("）"):
            ln = ln.replace("（", "").replace("）", "")
        _nx = raw[idx + 1] if idx + 1 < len(raw) else ""
        if _MOUTH_CUE.search(ln) and "says:<d>" not in _nx and not ln.strip().startswith("·"):
            ln = re.sub(r"[，。；]?\s*[^\s，。；]{1,8}嘴唇开合[。，]?\s*$", "。", ln.rstrip())          # P411b：没台词跟着的口型提示去掉
            ln = re.sub(r"[，；]。$", "。", ln)
        if re.fullmatch(r"[\s。，；、！？…”“\"]*", ln) and ln.strip():
            continue                                                          # 只剩标点的行
        out.append(ln)
    return "\n".join(out)


def beat_scrub(body, say_lines, scene_space=""):
    """节拍段的两道程序清理（P374）：
    · 本段没有台词行（say_lines 空）却出现 says 行 → 整行删（模型把下一段的台词提前写了）；有台词行的段只留给定的那些
    · 场景卡写的是室外，块里带屋内/墙壁/门框/床铺的短句 → 删那个短句（模型习惯性写屋内）"""
    out = []
    keep = set(str(x).strip() for x in (say_lines or []))
    _sp = str(scene_space or "").strip()
    outdoor = _sp.startswith("室外")
    # P376：场景卡自己写了的东西（草棚里的简易木床、炭盆）不算屋内词——只删卡上没有的
    _alien_in = []
    # P380：草棚是敞棚，「屋内/墙壁」仍算屋内词；P408b：按词查卡——卡上有「墙」只放过墙，不放过「屋内」（遗迹大门「巨石墙体」曾整类放过）
    for _w, _has in (("屋内", r"屋|房|室内"), ("室内", r"屋|房|室内"), ("墙壁", r"墙"), ("墙角", r"墙"), ("门框", r"门框"), ("屋顶", r"屋顶|棚顶|顶"),
                     ("棚顶", r"棚"), ("窗棂", r"窗"), ("窗户", r"窗"), ("窗框", r"窗"), ("窗边", r"窗"), ("天花板", r"天花板")):
        if not re.search(_has, _sp):
            _alien_in.append(_w)
    if "床" not in _sp:
        _alien_in += ["床铺", "床沿", "床上"]
    if "桌" not in _sp:
        _alien_in += ["书桌", "桌子", "桌面", "桌上", "桌边"]                     # P408：桥上摆出书桌
    if "椅" not in _sp:
        _alien_in += ["椅子", "椅背"]
    if "柜" not in _sp:
        _alien_in += ["柜子", "橱柜"]
    if "炕" not in _sp:
        _alien_in += ["炕"]
    if "灶" not in _sp:
        _alien_in += ["灶台"]
    _alien_re = re.compile("|".join(map(re.escape, _alien_in))) if (outdoor and _alien_in) else None
    _lines = str(body or "").splitlines()
    _first = next((i for i, l in enumerate(_lines) if _TL_BLOCK.match(l)), -1)
    _early = [l for l in _lines[:_first] if "says:<d>" in l] if _first > 0 else []
    if _early:
        # P386e：台词行写在第一个时间块前面（模型把它当标题）→ 挪到第一块末尾
        _lines = [l for l in _lines if l not in _early]
        _first = next((i for i, l in enumerate(_lines) if _TL_BLOCK.match(l)), -1)
        _end = _first + 1
        while _end < len(_lines) and _lines[_end].strip() and not _TL_BLOCK.match(_lines[_end]) and not _lines[_end].startswith("这一段结束时"):
            _end += 1
        _lines[_end:_end] = _early
    for line in _lines:
        st = line.strip()
        if "says:<d>" in st:
            if not keep or not any(k and k in st for k in keep):
                continue
        if _alien_re and _alien_re.search(st) and not st.startswith("这一段结束时") and "｜" not in st:
            _hm = re.match(r"^(\d+(?:\.\d+)?\s*[—\-–~]+\s*\d+(?:\.\d+)?\s*秒[：:])", st)     # 块头「0—3秒：」要留住
            _head, _rest = (_hm.group(1), st[_hm.end():]) if _hm else ("", st)
            parts = re.split(r"(?<=[，。；])", _rest)
            st2 = "".join(p for p in parts if not _alien_re.search(p))
            if st2.strip():
                line = _head + st2
            # 删空了就保留原块（宁可留一个屋内词，不能整块没了）
        out.append(line)
    return "\n".join(out)


def _tl_revert_foreign_places(body, orig_blocks, corpus):
    """守卫重写后的块里出现了原块/画面稿/场景卡/上一段都没有的地点词 → 用原块（P322④）。返回新 body。"""
    blocks, head, tail = _tl_blocks(body)
    if not blocks or not orig_blocks:
        return body
    base = str(corpus or "")
    changed = False
    for k, (a, b, txt) in enumerate(blocks):
        if k >= len(orig_blocks) or txt == orig_blocks[k]:
            continue
        new_w = {w for w in _PLACE_W.findall(txt)}
        bad = [w for w in new_w if w not in base and w not in orig_blocks[k]]
        if bad:
            blocks[k] = (a, b, orig_blocks[k])
            changed = True
            _dbg("时间轴块带进离场地点词，回退原块", {"块": k + 1, "词": bad})
    if not changed:
        return body
    return "\n\n".join([head] + [t for _, _, t in blocks] + ([tail] if tail else [])).strip()


def _sides_in(text, names):
    """文本里每个人在画面左/右：{名: '左'|'右'}（同一人两种都出现就不算）。"""
    out = {}
    for nm in names or []:
        if not nm:
            continue
        sides = set()
        for m in re.finditer(re.escape(str(nm)), str(text or "")):
            win = str(text)[m.end():m.end() + 14]
            # P324⑦：只认站位句式（在/站在/位于/靠/背靠/坐在/退到/留在 + 画面左/右），「看向右侧的门」「走到她右侧」不算
            s = re.match(r"(?:在|站在|位于|靠着|靠在|背靠|坐在|退到|站到|移到|留在|贴着)[^，。；]{0,4}?(画面)?(左|右)(侧|边|方)", win)
            if s and not re.search(r"看|望|朝|面向|走向|转向", win[:s.end()]):
                sides.add(s.group(2))
        if len(sides) == 1:
            out[nm] = sides.pop()
    return out


def _tl_inherit_staging(body, prev_state, names, say_lines):
    """第一块里谁左谁右和上一段结束状态相反 → 以上一段为准只重写第一块（一次）；还不对 → 记进 last_problems（P322②）。"""
    _tl_inherit_staging.last_problems = []
    if not str(prev_state or "").strip():
        return body
    if isinstance(names, (list, tuple)):
        names = list(names)
    blocks, head, tail = _tl_blocks(body)
    if not blocks:
        return body
    prev_sides = _sides_in(prev_state, names)
    if not prev_sides:
        return body
    a, b, first = blocks[0]
    now_sides = _sides_in(first, names)
    flipped = [nm for nm, s in now_sides.items() if nm in prev_sides and prev_sides[nm] != s]
    if not flipped:
        return body
    ask = ("下面是一段视频提示词的第一个时间块。上一段结束时的状态是：%s。这一块把 %s 的左右位置写反了（越轴）。"
           "只重写这一块：每个人的画面左右、朝向和景别都以上一段结束时为准，里面的动作、台词行（如果有）一字不动，开头保留「%d—%d秒：」。只输出这一块。"
           % (str(prev_state)[:300], "、".join(flipped), a, b))
    try:
        new = str(_q(ask, first, mt=600, temperature=0.3) or "").strip()
    except Exception:
        new = ""
    new = _tl_first_block_only(new)                                       # P324⑤：模型多吐几块/带尾句只取第一块
    if new and _TL_BLOCK.match(new) and all(sl in new for sl in say_lines if sl in first):
        ns = _sides_in(new, names)
        if not [nm for nm, s in ns.items() if nm in prev_sides and prev_sides[nm] != s]:
            blocks[0] = (a, b, _tl_clean(new))
            _dbg("时间轴第一块按上一段站位重写", {"翻了": flipped})
            return "\n\n".join([head] + [t for _, _, t in blocks] + ([tail] if tail else [])).strip()
    _tl_inherit_staging.last_problems = ["第一块里 %s 的画面左右和上一段结束时相反（越轴），自动改没改成——请看提示词" % "、".join(flipped)]
    _dbg("时间轴第一块越轴没改成", {"翻了": flipped})
    return body


_CLOSE_W = re.compile(r"特写|近景|微距|大特写")
_WIDE_W = re.compile(r"中景|中近景|全景|远景|双人")


def _tl_relay_tail(body, names, say_lines):
    """最后一块是下一段的接力帧：两人以上在场、最后一块只有特写/近景没有中景/全景 → 改成能看见全部在场人物的中景/全景（P322③）。"""
    blocks, head, tail = _tl_blocks(body)
    if not blocks or len([n for n in names if n]) < 2:
        return body
    a, b, last = blocks[-1]
    names = [n for n in names if n and n in last and not re.search(re.escape(n) + r"[^。；]{0,10}(离开|出门|走出|退出|转身走|离场|出画)", last)]   # P324⑧：只要求这一块里真在的人同框
    if len(names) < 2:
        return body
    if not _CLOSE_W.search(last) or _WIDE_W.search(last):
        return body
    ask = ("下面是一段视频提示词的最后一个时间块，它是下一段视频的接力帧。现在收在特写/近景，下一段接不上。"
           "只重写这一块：镜头改成能同时看见 %s 的中景或全景，机位与人眼齐高，每个人的画面左右、朝向照旧；"
           "里面的动作、台词行（如果有）一字不动，开头保留「%d—%d秒：」。只输出这一块。" % ("、".join(n for n in names if n), a, b))
    try:
        new = str(_q(ask, last, mt=600, temperature=0.3) or "").strip()
    except Exception:
        new = ""
    new = _tl_first_block_only(new)
    if new and _TL_BLOCK.match(new) and _WIDE_W.search(new) and all(sl in new for sl in say_lines if sl in last):
        blocks[-1] = (a, b, _tl_clean(new))
        if tail:
            tail = _CLOSE_W.sub("中景", tail)                           # P324③：尾句「收在特写」同步成中景，下一段照它开场
        _dbg("时间轴最后一块改成接力用的中景", {})
        return "\n\n".join([head] + [t for _, _, t in blocks] + ([tail] if tail else [])).strip()
    return body


_POS_W = re.compile(r"(门口|门边|门前|窗边|窗前|窗下|桌边|桌前|桌旁|床边|床前|炉边|炉前|柜台|台前|墙边|墙角|角落|中央|正中|尽头|入口|台阶|楼梯|廊下|檐下|旁边|身旁|身后|前方|对面|靠近|走到|退到|站到|来到|移到|坐到)")


def scene_layout_names(card, beat=False):
    """场景卡【戏区】→ 位置名单（每行「戏区名｜哪场戏｜方向距离｜地面周围」取前两项）（P325）。
    beat=True（节拍段，P383）：去掉「哪场戏」栏——那是设计场景时的戏，不是这一拍要演的（真机把口述的入画写成了掰饼吃饼）。"""
    ly = str((card or {}).get("layout") or "").strip()
    out = []
    for ln in ly.splitlines():
        parts = [x.strip() for x in ln.split("｜") if x.strip()]
        if parts and beat:
            parts = parts[:1] + parts[2:3]
        if parts:
            out.append("｜".join(parts[:3])[:60])
    return out[:8]


def _tl_position_problems(body, names, layout):
    """每块要有人物的位置（戏区名或位置词）；一块都没有 → 记问题（不拦，P325③）。"""
    blocks, head, tail = _tl_blocks(body)
    if not blocks:
        return []
    zone = [str(x).split("｜", 1)[0] for x in (layout or []) if str(x).strip()]
    missing = []
    for k, (a, b, txt) in enumerate(blocks):
        if any(z and z in txt for z in zone) or _POS_W.search(txt):
            continue
        missing.append("%d—%d秒" % (a, b))
    if len(missing) >= max(1, len(blocks) // 2):
        return ["这几块没写人物在场景的哪个位置（%s）——每块要写清在门口/桌边/哪个戏区，段与段之间位置要跟着故事动" % "、".join(missing[:4])]
    return []


def _tl_first_block_only(new):
    """守卫的重写只收一块：模型多吐了几块或带了「这一段结束时」尾句 → 只取第一块（P324⑤）。"""
    t = str(new or "").strip()
    if not t:
        return t
    bl, hd, tl = _tl_blocks(t)
    if bl:
        return bl[0][2].strip()
    return t.split("\n\n")[0].strip()



_CLOTH_SWAP = (("裙摆", "衣摆"), ("衬衫下摆", "衣摆"), ("衣角的褶皱", "衣角"), ("连衣裙", "衣服"), ("长裙", "衣服"), ("短裙", "衣服"),
               ("衬衫", "上衣"), ("T恤", "上衣"), ("毛衣", "上衣"), ("外套", "外层衣服"), ("西装", "外层衣服"), ("背心", "上衣"), ("长袍", "衣服"))


def strip_text_clothing(body, chars):
    """P322⑧（192：上传图是连体衣、正文写「裙摆」，成片每段在两套衣服之间跳）：
    有已采用参考图、卡上又没写服装的人 → 这段正文里的服装名词换成中性词，绑定行「保留参考图中的服装」才是唯一依据。
    卡上写了服装（人设图是按卡出的）就不动——那时文字和图本来同源。"""
    names = [str((c or {}).get("name") or "") for c in (chars or [])
             if str((c or {}).get("name") or "") and (c.get("visuals") or c.get("_has_image")) and not str(c.get("clothing") or "").strip()]
    if not names:
        return body
    out = []
    for ln in str(body or "").split("\n"):
        if "<Subject" in ln or "says:" in ln or "<d>" in ln or _H3_SAY.search(ln) or ln.strip().startswith("<"):
            out.append(ln)                                  # P324①：绑定行、台词行一字不动
            continue
        for a, b in _CLOTH_SWAP:
            ln = ln.replace(a, b)
        out.append(ln)
    return "\n".join(out)


def h3_prompt(shots, characters, scene_name, settings=None, instruction=None,
              scene_image=False, prev_tail="", scene_space="", seconds=None, anchor=None, plan=None):
    """把一段画面稿拼成 H3 提示词。返回整段提示词文本。

    characters: [{"name": 名字, "voice": 音色设定}]，顺序即 Subject 编号，
    和参考图 Picture 编号一一对应——**顺序不能乱**，乱了人就换脸换声。
    场景占用人物之后的下一个 Subject 号（两个人 → 场景是 Subject 3）。
    """
    from . import camera_view
    _view_mode, _ = _pov_spec(settings)
    _camera_owner = camera_view.owner(characters, settings) if _view_mode in camera_view.MODES else ""
    chars, _cam = h3_char_order(characters, settings)   # 第一视角：主角排最后、不带图
    # 【谁说话才写谁的音色】没人说话的段落却列着全部角色的音色，
    # 后面又跟一句"本段没有人说话"——自相矛盾（2026-09-01 用户指出）。
    # 人物绑定（长相）每段都要，音色只给这一段真开口的人。
    _speaks = set()
    for _w, _q in _pic_parts_dialogue(shots):
        _w = re.sub(r"（[^）]*）", "", str(_w or "")).strip()
        if _w:
            _speaks.add(_w)
    head = []
    _txt = str(shots or "")
    for i, c in enumerate(chars, 1):
        # 【衣着状态】这段里有换衣/脱衣/裸/浴这类词、并且提到了这个人 → 问一句他本段穿什么，
        # 绑定行就不再要求"保留参考图的服装"（洗澡还穿着女仆装，就是这句害的）。
        st_line = ""
        _ward = settings.setdefault("_wardrobe", {}) if isinstance(settings, dict) else {}
        _wrow = (settings or {}).get("_wardrobe_row") if isinstance(settings, dict) else None
        if isinstance(_wrow, dict) and c["name"] in _wrow:
            # P329：确定性追踪——开始时/本段内/结束时；人设图永远穿衣，洗澡/脱衣/换衣由这里进提示词
            from . import wardrobe as _wd
            st_line = _wd.state_line(_wrow, c["name"])
            _end = str((_wrow.get(c["name"]) or {}).get("end") or "")
            if _end and _end != "参考图那一身":
                _ward[c["name"]] = _end
            else:
                _ward.pop(c["name"], None)
        elif _STATE_RE.search(_txt) and (len(chars) == 1 or c["name"] in _txt):
            st_line = _state_line(c["name"], _txt)
            if st_line:
                _ward[c["name"]] = _carry_state(st_line)                  # P318b：这段结束时穿什么，往后各段沿用
        elif _ward.get(c["name"]):
            # P318b：本段画面稿没写换衣 → 沿上一段结束时的衣着（原来这里退回人设卡的衣服，第 1 段湿透长袍、第 2 段红丝绒）
            st_line = "沿上一段结束时的样子：%s；本段全程如此" % _ward[c["name"]]
        if c["name"] == _cam:
            head.append(_H3_BIND_POV % (i, c["name"], c["name"], c["name"], c["name"], c["name"]))
        elif st_line:
            head.append(_H3_BIND_STATE % (i, c["name"], i, st_line))
        else:
            head.append(_H3_BIND % (i, c["name"], i))
        if is_nonhuman(c):
            # 非人形角色把体型尺度写进绑定行，视频模型才知道它比人大多少（2026-09-04 石魔段间忽大忽小）
            head.append("<Subject%d>%s的%s。" % (i, c["name"], body_scale_line(c)))
        if c["name"] in _speaks:
            head.append(_H3_VOICE % (i, i, i, h3_voice_tech(c, settings)))
    # 【人物清单锁死】三视图参考图里同一个人画了三遍，视频模型会当成三个人、在主角背后多长出一个敌人
    # （用户 2026-09-04 看项目100 第3段：女主"背对敌人"其实是背后多了一具石魔）。
    if chars:
        # 【剧本写了配角就别锁死】原来无条件加「不出现任何别的人或生物」，
        # 和下一句「画面里出现不在人物表上的东西…直接写它是什么」自相矛盾，
        # 模型听前一句 → 门派被灭那场戏里黑衣人、师兄弟、大火一个都没有
        # （P246 项目155：故事最重要的三个画面全丢了）。
        # 防重影的本意（三视图被当成三个人）由「每个角色只出现一个」那半句保住。
        _extras = extras_in_shots(_txt, [c["name"] for c in chars])
        # 【清单优先】拍摄清单说这场戏有哪些群体/没卡的人，就按它写（P252）；
        # 词表（extras_in_shots）只是老项目没清单时的兜底。
        for _h in (settings or {}).get("_extras_hint") or []:
            _h = str(_h or "").strip()
            if _h and _h not in _extras and _h not in [c["name"] for c in chars]:
                _extras.append(_h)
        head.append(chars_lock_line([c for c in chars if c["name"] != _cam], _extras, (settings or {}).get("_extras_scene")))
        # 场景 Subject 会被当成角色（2026-09-05 项目102 段4：「敌对机甲（S3）」，
        # 而 S3 是场景板，H3 直接把那张板画成了开场空镜）。这里说清编号只到人物为止。
        head.append("Subject 编号只到 S%d 为止，这些是人；再往后的 Subject 是**地方**（场景参考板），"
                    "不是人也不是生物。画面里出现不在人物表上的东西（追兵、机甲、野兽、车），"
                    "直接写它是什么、在画面哪个位置，**不要给它编号**。" % len(chars))
    n_sc = len(chars) + 1
    # 场景可以是多个（一个地点拆成内部/门口/墙边），每个有图的场景绑一个 Subject，
    # 顺序和参考包（segment_api.new_pack：人物图 + 场景图按卡的顺序）一致——
    # 原来只绑第一个，参考包里第 4、5 张图没人认领（项目97 查出）。
    _scenes = list(scene_name) if isinstance(scene_name, (list, tuple)) else [scene_name]
    _scenes = [str(x or "").strip() for x in _scenes if str(x or "").strip()] or [""]
    for k, _sn in enumerate(_scenes):
        if scene_image:
            head.append(_H3_SCENE_PIC % (n_sc + k, n_sc + k, _sn))
        else:
            head.append(_H3_SCENE % (n_sc + k, _sn))
    # 【摄影】光线/镜头/色调/机位——原来提示词里一句电影语言都没有，出来就是平光正面照。
    # 机位按视点（第一视角/自拍/监控/跟拍）取，主角 = 第一张人设卡。
    _lead = _camera_owner or _cam or (chars[0]["name"] if chars else "")
    head.append(_h3_cine(settings, scene_space, _lead))
    # 【本段要点】本话安排的时间预算给这一段情节写了 focus（"给关键对白和反应留时间"），
    # make_h3_prompts 按切片的 beat 塞进 settings["_beat_focus"]；和 _extras_hint 一个路子。
    _bf = str((settings or {}).get("_beat_focus") or "").strip()
    if _bf:
        head.append("本段要点：%s" % _bf)

    if (use_timeline_director() and not instruction) or _view_mode in camera_view.MODES:
        body, _tl_tail = _h3_timeline_body(shots, chars, settings, prev_state=prev_tail, seconds=seconds, plan=plan,
                                           scene_name=(_scenes[0] if _scenes else ""), scene_space=scene_space,
                                           camera_owner=_camera_owner)
        h3_prompt.last_tail = _tl_tail
        h3_prompt.last_relay = getattr(_h3_timeline_body, "last_relay", None)          # P331
    else:
        body = _h3_shots(shots, chars, settings, instruction, prev_tail, cam=_cam, seconds=seconds, plan=plan)
        body = anchor_close_shots(body, anchor if anchor is not None else env_anchor_from_space(scene_space))
        body = ensure_speak_cue(body)                       # 台词前的镜头要有「谁在开口」
        h3_prompt.last_tail = getattr(_h3_shots, "last_tail", "") or ""
    # 【正文的名字必须跟绑定块一致】不一致 = 参考图绑不到人，脸必飘（P244）。
    # 放在这里：body 不管走哪条分支（时间轴导演 / 老 _h3_shots）都过这一道。
    body, _renamed = fix_body_aliases(body, chars)
    body = strip_text_clothing(body, chars)                 # P322⑧：有图没写服装的人，正文里的服装词让位给参考图
    h3_prompt.last_renamed = _renamed
    # 专用主观视点路径已经由 camera_view 输出承接状态和对白，不再附加旧的
    # “本段全程没有人说话”尾巴；那个尾巴会把模型刚写出的对白判成静音。
    if _view_mode in camera_view.MODES:
        tail = ""
    else:
        tail = _H3_TAIL
        if not _H3_SAY.search(body):
            tail = _H3_SILENT + "\n" + _H3_TAIL
    return "\n".join(head) + "\n" + body.strip() + "\n" + tail




# ─────────── 特写匹配切：代码查景别，模型只改那几镜 ───────────
# 用户 2026-09-02 提的方法，实验 V12 验证：E1 收在手的特写、E2 从同一特写起、再脸对脸、再拉开，
# 接口看不出是两段。规矩在指令词第⑫条；模型没照做时由这里兜底。
# 只认「人碰人」：动词后面得跟着另一个人（她/他/对方/人名紧邻），
# 「握持铁剑」「攥枯树枝」这种握东西的不算——项目97 实测因此被塞进了剧本里没有的亲密镜头。
_TOUCH_RE = re.compile(
    r"相拥|拥抱|抱着|抱住|抱进|接吻|亲吻|吻上|吻住|嘴唇贴|"
    r"(按|握|搂|扶|托|捧|抵|贴|靠|压|环)(在|住|着|上)?(她|他|对方)的?(手|腕|背|肩|腰|脸|下巴|额头|胸|颈|肩胛)|"
    r"(贴|靠|抵|埕|倚)(在|着)(他|她|对方)(的)?(胸|身|怀|额头|肩)|(她|他)脸(侧)?贴")
_CONTACT_RE = re.compile(r"手|额头|脸|唇|肩|腰|背|外套|衬衫|下巴|颈")


def _contact_point(state):
    """从收尾状态里挑**一个**确定的接触点短句（优先带「手」的）。修复只给一个，不给选项——
    给两个模型会连「或」一起抄进镜头行（实验四b 实测）。"""
    parts = [p.strip() for p in re.split(r"[，,；;。]", str(state or "")) if p.strip()]
    for p in parts:
        if "手" in p and _TOUCH_RE.search(p):
            return p
    for p in parts:
        if _TOUCH_RE.search(p):
            return p
    return parts[0] if parts else ""


def _rewrite_shots(body, ask, shots, idx, sys_override=None):
    """让模型只改点名的几镜，其余原样。改完校验：台词行一字未动、顺序未变，否则丢弃修改。"""
    old_say = _H3_SAY.findall(body)
    sysm = sys_override or (
        "你在修改一段 MiniMax H3 视频提示词的镜头行。只改我点名的那几镜，"
        "其余镜头行、所有台词行**一个字不动，原样保留、原位保留**。"
        "镜头行格式：「镜头N（秒数｜机位｜运镜）：谁+景别。动作」，景别只用 远景/全景/中景/中近景/近景/特写。"
        "输出完整的镜头体（所有行），不要解释。\n\n【要改什么】\n" + ask)
    try:
        rep = _q(sysm, ("【要改什么】\n" + ask + "\n\n" if sys_override else "") + "【当前镜头体】\n" + body,
                 mt=2200, temperature=0.3)
    except Exception:
        return body
    new = _h3_fix(rep or "", shots, idx)
    # 修复输出里若又带了「收尾状态」行，去掉——那行不进视频
    new = "\n".join(l for l in new.splitlines() if not l.strip().startswith("收尾状态"))
    if _H3_SAY.findall(new) != old_say:
        _dbg("特写衔接修复改动了台词，丢弃", {"前": len(old_say), "后": len(_H3_SAY.findall(new))})
        return body
    if not [l for l in new.splitlines() if shot_parts(l)]:
        return body
    return new


def _pov_guard(body, shots, idx, settings, cam):
    """第一视角：主角是摄像机。① 镜头行把他当被拍对象 → 只改那几镜；
    ② 每镜要有 POV 证据（对方看向镜头 / 我的手入画），缺的补；③ 补完再查一遍①——
    实测补证据那次改写会把「我的手」改回「埃里克近景」（V14e）。"""
    name, _ = _pov_spec(settings)
    if name != "第一视角POV" or not cam:
        return body
    _EVID = re.compile(r"看向镜头|看着我|看进镜头|对上镜头|盯着我|望着我|我的手|我的右手|我的左手|我的手臂|我伸手|我的袖")

    def _lead_shots(b):
        out = []
        for l in b.splitlines():
            if not l.strip().startswith("镜头"):
                continue
            p = shot_parts(l)
            if p and p[4].split("。", 1)[0].startswith(cam):
                out.append("镜头%d" % p[0])
        return out

    def _fix_lead(b):
        bad = _lead_shots(b)
        if not bad:
            return b
        _dbg("第一视角：主角被拍进画面，改写", {"镜": bad})
        return _rewrite_shots(
            b,
            "这是第一视角：%s是拍摄者，镜头就是他的眼睛，他本人不是被拍的对象。"
            "把 %s 这几镜改写成**%s看到的东西**：别人（写清在我左手边/正前方/越来越近，她看向镜头）、环境、"
            "或者%s自己从画面下沿伸进来的手和衣袖；「谁 + 景别」写被看的那个人的景别，"
            "%s自己只能以「我的手」出现，不能写成「%s近景/中景」。其余镜头和所有台词行原文原位不动。"
            % (cam, "、".join(bad), cam, cam, cam, cam),
            shots, idx)

    body = _fix_lead(body)
    lack = ["镜头%d" % shot_parts(l)[0] for l in body.splitlines()
            if shot_parts(l) and not _EVID.search(l)]
    if lack:
        _dbg("第一视角：镜头缺 POV 证据，补", {"镜": lack})
        body = _rewrite_shots(
            body,
            "这是第一视角，%s是拍摄者，他本人不出现在画面里。给 %s 这几镜**各加一样第一视角的证据**"
            "（挑一样，写进这一镜的动作里）：① 对方的视线——「她看向镜头」「她看着我说」；"
            "② 我的身体入画——「我的右手从画面下沿伸进来…」（只能写「我的手」，不能把%s写成被拍的人）。"
            "只往这几镜里加一句，别的字不改；其余镜头和所有台词行原文原位不动。" % (cam, "、".join(lack), cam),
            shots, idx)
        body = _fix_lead(body)      # 补证据那次改写可能又把主角写进去，再查一遍
    return body


def _bridge_shots(body, shots, idx, prev_tail, this_tail):
    """段头/段尾的特写衔接兜底。返回修过的镜头体。"""
    lines = [l for l in body.splitlines() if l.strip().startswith("镜头")]
    if not lines:
        return body
    # 段尾：这段结束时两人贴着 → 最后一镜要是接触点特写
    if this_tail and _TOUCH_RE.search(this_tail):
        last = lines[-1]
        _lb = (shot_parts(last) or (0, 0, "", "", last))[4]
        if not ("特写" in _lb[:12] and _CONTACT_RE.search(last)):
            cp = _contact_point(this_tail)
            if cp:
                body = _rewrite_shots(
                    body, "把**最后一镜**改成这个接触点的特写：「%s」。写清是谁的手/身体哪部分、在对方哪里，"
                          "加一个小动作（收紧、松半分、指腹蹭一下）。其余镜头不动。" % cp, shots, idx)
                lines = [l for l in body.splitlines() if l.strip().startswith("镜头")]
    # 段头：上一段结束时两人贴着 → 镜头1 接触点特写，镜头2 脸对脸近景，镜头3 拉开
    if prev_tail and _TOUCH_RE.search(prev_tail):
        first = lines[0]
        _fb = (shot_parts(first) or (0, 0, "", "", first))[4]
        ok1 = "特写" in _fb[:12] and _CONTACT_RE.search(first)
        ok2 = len(lines) > 1 and re.search(r"脸对脸|面对面|对视|鼻尖|额头", lines[1])
        if not (ok1 and ok2):
            cp = _contact_point(prev_tail)
            body = _rewrite_shots(
                body, "上一段结束时的状态是：「%s」。\n"
                      "新的**镜头1**：特写，拍这个接触点：「%s」，一个小动作。\n"
                      "新的**镜头2**：两人脸对脸的近景，写谁看谁、鼻尖离多远。\n"
                      "新的**镜头3**：中景，两人的姿势和上面那句状态一样，然后接着演原来镜头1 的动作。\n"
                      "原来的镜头往后顺延、编号重排；台词行原文、原来的先后位置都不动。" % (prev_tail, cp),
                shots, idx)
    return body


_ENV_WORDS = re.compile(r"背景是|身后是|身后的|身前的|墙|门|窗|地面|地板|天花|屋顶|光柱|天光|灯光|阳光|月光")   # 通用底表；场景卡里的陈设名在运行时补进去
_WIDE = ("远景", "全景", "大全景")


def env_anchor_from_card(card):
    """只用绑定的那张场景卡：light/ground/furniture/landmarks 字段，去掉带人名/他她它的叙述句。"""
    if not card:
        return ""
    bits = []
    for k in ("light", "ground", "furniture", "landmarks", "space"):
        v = str(card.get(k) or "").strip()
        if v:
            bits.append(v)
    return env_anchor_from_space("；".join(bits))


def env_anchor_from_space(space):
    """从场景卡描写里抠一句 ≤40 字的背景锚点（光 + 地面/物件），给近景/特写用。含人称/人名的叙述句不要。"""
    sents = [x.strip() for x in re.split(r"[。；\n]", str(space or "")) if x.strip()]
    sents = [x for x in sents if not re.search(r"他|她|它|们|停在|走进|站在|深吸|我|尽头|擦着|掀|扯|抓|夺|按|推|拉|踹|冲|扑|摔|跪|递|接|握|攥|甩|拽|说|喊", x) and len(x) <= 30]
    lit = [x for x in sents if re.search(r"光|雨|雪|雾|灯|烛|火", x) and not re.search(r"目光|眼光|光滑|光头", x)]
    obj = [x for x in sents if x not in lit and re.search(r"墙|门|窗|柱|台|桌|椅|床|梁|架|柜|地|路|树|山|楼|车|船|石|水|栏|帘|镜|柜", x)]
    parts = []
    if lit:
        parts.append(lit[0][:22])
    if obj and (not lit or obj[0] != lit[0]):
        parts.append(obj[0][:18])
    return "，".join(parts)[:40]


_SPEAK_VERBS = re.compile(r"说|喊|吼|问|叫|开口|张口|嘴唇|嘴|嗓")


def ensure_speak_cue(body_text):
    """台词前面那个镜头必须有「谁在开口」：说话人名字 + 开口动词。缺哪个补哪个。"""
    lines = str(body_text or "").splitlines()
    for i, line in enumerate(lines):
        if "says:" not in line:
            continue
        m = re.match(r"^([一-龥]{2,4})(?:说)?[：:]", line.strip())
        who = m.group(1) if m else ""
        q = re.search(r"\[Chinese\](.*?)</d>", line)
        shout = bool(q and re.search(r"[！!]", q.group(1)))
        # 往上找最近的镜头行
        k = i - 1
        while k >= 0 and not shot_parts(lines[k]):
            k -= 1
        if k < 0:
            continue
        p = shot_parts(lines[k])
        body = p[4].rstrip("。")
        has_name = (who in body) if who else True
        has_verb = bool(_SPEAK_VERBS.search(body))
        if has_name and has_verb:
            continue
        # 提示词用视觉描述（嘴唇开合），不用"说道/喊道"——H3 会把这两个字连着台词念出来
        cue = ("%s嘴唇开合%s" % (who if not has_name else ("他" if not who else who), "，眉头上扬" if shout else "")) if not has_verb \
            else ("%s在画面里" % who)
        body = body + "，" + cue + "。"
        inside = "｜".join(x for x in ("%d秒" % p[1] if p[1] else "", p[2], p[3]) if x)
        lines[k] = "镜头%d%s：%s" % (p[0], ("（%s）" % inside) if inside else "", body)
    return "\n".join(lines)


def anchor_close_shots(body_text, anchor):
    """近景/特写/中景镜头行里没有任何环境词的，句尾补「背景是……」。全景不动。
    环境词 = 通用底表 + 这张场景卡锚点句里的名词（二字以上的连续汉字块），换题材不用改词表。"""
    if not anchor:
        return body_text
    _extra = [w for w in re.findall(r"[一-龥]{2,4}", str(anchor)) if not re.search(r"从|的|着|了|在|和|与|几缕|一片|满是", w)]
    _extra = _extra + [w[-2:] for w in _extra if len(w) >= 3]          # 「木质吧台」也认「吧台」
    _env = re.compile("|".join([_ENV_WORDS.pattern] + [re.escape(w) for w in _extra])) if _extra else _ENV_WORDS
    lines = str(body_text or "").splitlines()
    out = []
    for i, line in enumerate(lines):
        p = shot_parts(line)
        nxt = next((l for l in lines[i + 1:] if l.strip()), "")
        speaking = "says:" in nxt                       # 下一行是台词 → 这是说话镜头，不加锚点
        if p and not speaking and not any(w in p[4].split("。", 1)[0] for w in _WIDE) and not _env.search(p[4]):
            body = re.sub(r"[，,]?[^，。]{0,6}(?:说道|说|问道|喊道|吼道|低声道)[：:]\s*$", "", p[4].rstrip("。")).rstrip("，。：:")
            body = body + "。背景是" + anchor.rstrip("，。") + "。"
            inside = "｜".join(x for x in ("%d秒" % p[1] if p[1] else "", p[2], p[3]) if x)
            line = "镜头%d%s：%s" % (p[0], ("（%s）" % inside) if inside else "", body)
        elif p and speaking:
            body = re.sub(r"[：:]\s*。?\s*$", "。", p[4])        # 「低声说道：」留着当开口提示，只把悬空冒号收成句号
            inside = "｜".join(x for x in ("%d秒" % p[1] if p[1] else "", p[2], p[3]) if x)
            line = "镜头%d%s：%s" % (p[0], ("（%s）" % inside) if inside else "", body)
        out.append(line)
    return "\n".join(out)


# ───── c. 导演加戏守卫 ─────
_MOVE_VERBS = re.compile(r"跑去|跑向|奔向|奔去|离开|走出|走进|走向|冲出|冲向|跳下|跳上|消失在|退出|进入|穿过|跨过|掠过|逃走|逃向|爬上|爬到|转身向[^，。]{0,6}(?:跑|走)")


def strip_invented_moves(body_text, src):
    """镜头里出现画面稿没有的位移动词短句 → 删掉（导演把"猛地抬头"编成了"转身向缺口跑去，进入雨幕"）。"""
    src = str(src or "")
    out = []
    for line in str(body_text or "").splitlines():
        p = shot_parts(line)
        if not p:
            out.append(line)
            continue
        clauses = re.split(r"([，；])", p[4])
        keep, i = [], 0
        while i < len(clauses):
            c = clauses[i]; sep = clauses[i + 1] if i + 1 < len(clauses) else ""
            m = _MOVE_VERBS.search(c)
            if m and m.group(0) not in src:
                pass
            else:
                keep.append(c + sep)
            i += 2
        body = _tidy_sentence("".join(keep)) or p[4]
        inside = "｜".join(x for x in ("%d秒" % p[1] if p[1] else "", p[2], p[3]) if x)
        out.append("镜头%d%s：%s" % (p[0], ("（%s）" % inside) if inside else "", body))
    return "\n".join(out)


# ───── d. 反应镜头限 2 秒 ─────
_ACT_VERBS = re.compile(r"劈|砍|刺|扑|后退|退开|转身|抓住|抓向|撞|跪|举|挥|冲|甩|踩|抬起|按住|推开|推向|拉开|拉住|走向|走到|跑|蹲|翻身|拔|格挡|横扫|挑|砸|踢|扣住|拽|打滑|滑向|爬|坠|落下|站起|起身|挣|抬头|低头|回头|睁开|张开")


def limit_shot_count(body_text, total):
    """一段镜头数不超过 段秒/3（至少 2 镜）：先删纯反应镜（无台词、无动作动词），再删最短的；台词镜和它前面的开口镜不删。重新编号。"""
    try:
        total = float(total or 0)
    except Exception:
        total = 0.0
    if total <= 0:
        return body_text
    lines = str(body_text or "").splitlines()
    idx = [i for i, l in enumerate(lines) if shot_parts(l)]
    limit = max(2, int(total // 3))
    if len(idx) <= limit:
        return body_text
    protected = set()
    for k, i in enumerate(idx):
        nxt = next((l for l in lines[i + 1:] if l.strip()), "")
        if "says:" in nxt or "says:" in lines[i]:
            protected.add(i)
    def _score(i):
        p = shot_parts(lines[i])
        react = 0 if _ACT_VERBS.search(p[4]) else 1          # 纯反应先删
        return (react, -(p[1] or 0) * -1)                     # 同类里秒数短的先删
    protected.add(idx[0])                                   # 第一镜（建立镜头）不删
    _FACE = re.compile(r"眼|瞳孔|嘴角|眉|喉结|神情|表情|脸")
    def _rank(i):
        body = shot_parts(lines[i])[4]
        act = bool(_ACT_VERBS.search(body))
        face = bool(_FACE.search(body))
        # 先删纯表情反应镜，再删其他无动作镜，最后才删有动作的；同类里秒数短的先删
        return (0 if (face and not act) else (1 if not act else 2), shot_parts(lines[i])[1] or 0)
    removable = sorted([i for i in idx if i not in protected], key=_rank)
    to_drop = set()
    for i in removable:
        if len(idx) - len(to_drop) <= limit:
            break
        to_drop.add(i)
    out, n = [], 0
    for i, l in enumerate(lines):
        if i in to_drop:
            continue
        p = shot_parts(l)
        if p:
            n += 1
            inside = "｜".join(x for x in (("%d秒" % p[1]) if p[1] else "", p[2], p[3]) if x)
            l = "镜头%d%s：%s" % (n, ("（%s）" % inside) if inside else "", p[4])
        out.append(l)
    if to_drop:
        _dbg("镜头数超限删镜", {"删": len(to_drop), "上限": limit, "总秒": total})
    return "\n".join(out)


def cap_reaction_shots(body_text, cap=3):
    out = []
    for line in str(body_text or "").splitlines():
        p = shot_parts(line)
        if p and p[1] and p[1] > cap and not _ACT_VERBS.search(p[4]):
            inside = "｜".join(x for x in ("%d秒" % cap, p[2], p[3]) if x)
            line = "镜头%d（%s）：%s" % (p[0], inside, p[4])
        out.append(line)
    return "\n".join(out)


def drop_alien_says(body_text, shots):
    """镜头行里的台词必须来自这一段画面稿；不是的整行删（含「XX说：」前缀和后面的「说完…」行）。"""
    said = [re.sub(r"[^一-龥]", "", q) for _, q in _pic_parts_dialogue(str(shots or ""))]
    out, drop_next = [], False
    for line in str(body_text or "").splitlines():
        if drop_next and not shot_parts(line) and "says:" not in line and line.strip().startswith(("说完", "问完", "喊完", "吼完")):
            drop_next = False
            continue
        drop_next = False
        m = _H3_SAY.search(line)
        if m:
            nq = re.sub(r"[^一-龥]", "", m.group(3))
            ok = any(nq == g or (len(nq) >= 4 and nq in g) or (len(g) >= 4 and g in nq) for g in said if g)
            if not ok:
                _dbg("删幻觉台词", {"行": line[:80]})
                drop_next = True
                continue
        out.append(line)
    return "\n".join(out)


def whitelist_shot_lines(body_text):
    """镜头表只留：镜头N行 / 台词行 / 台词后紧跟的「说完…」短句。导演的注释、解释、空标题全删——会被 H3 念出来。"""
    out, dropped = [], []
    prev_say = False
    for line in str(body_text or "").splitlines():
        t = line.strip()
        if not t:
            out.append(line)
            continue
        if shot_parts(t) or _H3_SAY.search(t):
            out.append(line)
            prev_say = bool(_H3_SAY.search(t))
            continue
        if prev_say and len(t) <= 60 and re.match(r"^(说完|问完|喊完|吼完|话音|说罢|言罢)", t) and not re.search(r"注[：:]|规则|另起一行|画面稿|提示词|格式", t):
            out.append(line)
            prev_say = False
            continue
        dropped.append(t[:60])
        prev_say = False
    if dropped:
        _dbg("镜头表删非法行", {"删": dropped})
    return "\n".join(out)


_PLAN_SYS = """你是这部短片的导演，下面是整话剧本，已经按 8～15 秒切成了若干段（〔段N〕）。通读全篇后，给**每一段**填一行，用「｜」分隔六栏：
段号｜目的｜关键动作｜站位｜进出｜镜头建议
· 镜头建议：这一段 3～5 个镜头怎么排，按「建立（全景看清位置）→动作（中景）→细节（手/物件特写）→反应（对方脸）」的节奏写，
  每个镜头一个短语，用「/」分开，可以插一个第一视角或越肩；动作幅度要大（整个人的位移、整条手臂）。
· 目的：这一段要让观众看懂的**一件事**（一句话，写观众能看出来的事实，比如"阿离看上了李长渊，苏清婉不高兴"）。
· 关键动作：谁对谁做了什么（一个动作，必须来自这一段的剧本原文；有台词就写谁说了这句）。
· 站位：这一段结束时在场的每个人在画面的左/中/右、彼此隔几步、脸朝哪；和上一段同一地点的，站位要接得上，
  只有剧本写了移动才变。
· 进出：这一段谁从哪到哪；没有就写「无」。
每段一行，不多写别的。"""


def plan_segments(slices, chars=None, log=None, settings=None):
    """整话分镜表：返回 {段号: {"purpose","action","blocking","entry"}}。任何一段解析不到就没有那段（导演照旧写）。"""
    if not slices:
        return {}
    body = "\n\n".join("〔段%d〕%s" % (k + 1, str(sl.get("text") or "")) for k, sl in enumerate(slices))
    try:
        deep = deep_video_director(settings)
        rep = _q(_PLAN_SYS, body, mt=(5200 if deep else 3200), temperature=0.3,
                 reasoning=deep)
    except Exception as ex:
        _dbg("分镜表", {"失败": str(ex)[:80]})
        return {}
    plan = {}
    for line in str(rep or "").splitlines():
        parts = [x.strip() for x in line.strip().strip("|｜").split("｜")]
        if len(parts) < 3:
            parts = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(parts) < 3:
            continue
        m = re.search(r"(\d+)", parts[0])
        if not m:
            continue
        n = int(m.group(1))
        row = {"purpose": parts[1][:80], "action": parts[2][:120],
               "blocking": (parts[3] if len(parts) > 3 else "")[:160], "entry": (parts[4] if len(parts) > 4 else "")[:80],
               "shots": (parts[5] if len(parts) > 5 else "")[:200]}
        if row["purpose"]:
            plan[n] = row
    _dbg("分镜表", {"段数": len(slices), "有表": len(plan), "样例": list(plan.items())[:2]})
    return plan


def plan_block(row):
    """把分镜表一行拼成给导演的段落。"""
    if not row:
        return ""
    out = ["\n\n【这一段要让观众看懂什么】%s" % row.get("purpose", "")]
    if row.get("action"):
        out.append("【关键动作】%s——每个镜头都要拍到它，或者拍到对它的反应。" % row["action"])
    if row.get("blocking"):
        out.append("【站位】%s——★镜头1 的全景必须逐字写出这个站位（谁在左/中/右、隔几步、脸朝哪、谁大谁小），后面各镜不许改变相对位置，除非【进出】写了移动。" % row["blocking"])
    if row.get("entry") and row["entry"] not in ("无", "无。", "-"):
        out.append("【进出】%s" % row["entry"])
    if row.get("shots"):
        out.append("【镜头建议】%s——按这个顺序排镜头，可微调秒数。" % row["shots"])
    return "\n".join(out)


def _h3_shots(shots, chars, settings=None, instruction=None, prev_tail="", cam="", seconds=None, plan=None):
    """镜头行——这一块交给模型写，写完逐行验收。

    prev_tail：上一段最后一个镜头的原文。第一镜要接住它，
    不然段与段之间会断开（用户 2026-09-01 看片反馈）。
    """
    idx = {c["name"]: i for i, c in enumerate(chars, 1)}
    ins = _fill(instruction or _ins(os.environ.get("V41_SHOTS_INS") or "画面稿改H3提示词_指令词.txt"),
                settings or {}, None)
    ins += ("\n\n【人物编号】\n"
            + "\n".join("%s ＝ Subject %d ＝ S%d%s" % (n, i, i, ("（%s）" % body_scale_line(c)) if is_nonhuman(c) else "")
                         for (n, i), c in zip(idx.items(), chars)))
    # 【镜头语言】类型镜头倾向 + 硬要求（2026-09-04 用户看片：五段全是"全景固定—中景缓推—特写固定"，没有远景没有运镜）
    try:
        from . import kits as _kt
        _gsb = _kt.genre_shot_block(str((settings or {}).get("genre") or ""))
    except Exception:
        _gsb = ""
    ins += "\n\n【镜头语言】" + (("\n" + _gsb) if _gsb else "") + (
        "\n· 每段至少一处**真运镜**：跟拍、环绕、横摇、升降、推轨中选一种，写在括号第三项；不许一段全是固定和缓推。"
        "\n· 机位角度要变：不许整段全部平视；两人体型差大时用低角度仰拍大的那个、俯拍小的那个。"
        "\n· 不许每段都是「全景—中景—特写」三件套：按这段的事选景别，动作大用全景和跟拍，对峙用双人中景和越肩，揭示物件才用特写。"
        + ("\n· ★这是全片第一段：镜头1 必须是**远景**——看清地形（门、阶、墙、出口在哪）和每个人的位置与大小差，人在画面里可以很小。"
           if not str(prev_tail or "").strip() else ""))
    if seconds:
        ins += "\n\n【本段时长】约 %d 秒——各镜秒数加起来对上这个数。" % int(round(float(seconds)))
    ins += plan_block(plan)                                # 分镜表这一行：目的/关键动作/站位/进出
    # 第一视角/自拍/监控/跟拍：镜头行得按"谁在拍"来写（用户 2026-09-02：视点没进视频层）
    # chars 已经是 h3_prompt 排好的顺序，主角名由 cam 传进来，别再重算（重算会把主角算成别人）
    ins += _h3_pov_rules(settings, cam or (chars[0]["name"] if chars else ""))
    if str(prev_tail or "").strip():
        ins += ("\n\n【上一段结束时的状态】\n%s\n\n"
                "★你的**镜头1 必须从这个状态起手**：人在哪、什么姿势、手在哪、"
                "贴着谁、看着哪、穿着什么，都照这个接着往下演，"
                "不许当作全新开始——那样两段之间会断开（实测）。"
                % str(prev_tail).strip()[:400])
    # 有台词的画面稿，镜头行里必须有台词——漏了就重写一次，取问题少的那版
    # （2026-09-01 实测 11 段里 3 段整段一句台词都没写）。
    want = len(_pic_parts_dialogue(shots))
    best, best_n = "", None
    for attempt in (1, 2):
        extra = ("" if attempt == 1 or not want else
                 "\n\n★上一次你把台词漏了。这段画面稿里有 %d 句台词，"
                 "每一句都要有自己的镜头行，一句都不许少。" % want)
        raw = _q(ins + extra,
                 "【画面稿】\n" + str(shots or "") + "\n\n写成镜头行。",
                 mt=2600, temperature=0.6 if attempt == 1 else 0.4)
        got = _h3_fix(raw, str(shots or ""), idx)
        n = abs(want - len(_H3_SAY.findall(got)))
        if best_n is None or n < best_n:
            best, best_n = got, n
        if n == 0:
            break
    # 【收尾状态】最后那行「收尾状态：…」不进视频，摘出来给下一段当起手
    # （用户 2026-09-02：第一段拥抱，第二段要从这个姿势延伸；
    #  原来只喂上一段最后一个镜头行——那是动作，不是结果）。
    body, tail = [], ""
    for l in best.splitlines():
        if l.strip().startswith("收尾状态"):
            tail = re.sub(r"^收尾状态[：:]\s*", "", l.strip())
        else:
            body.append(l)
    _h3_shots.last_tail = tail
    out = "\n".join(body)
    # 【导演复审】一次：远景位置 / 景别节奏 / 空间进出 / 括号三样 / 无声音——只改不合格的镜
    out = _director_review(out, str(shots or ""), idx)
    out = camera_variety_fix(out, str(shots or ""), idx, first_segment=not str(prev_tail or "").strip())
    out = strip_invented_moves(out, str(shots or ""))      # 导演加的位移戏删掉
    out = drop_alien_says(out, str(shots or ""))            # 画面稿里没有的台词删掉（范例泄漏/幻觉）
    # 【第一视角】主角是摄像机，被拍进画面了就只改那几镜
    out = _pov_guard(out, str(shots or ""), idx, settings, cam)
    # 【特写匹配切】两人贴着的戏，段尾收特写、段头从同一特写起——模型没照做就只改那几镜
    out = _bridge_shots(out, str(shots or ""), idx, str(prev_tail or ""), tail)
    # 【代码收尾】声音短句删掉；秒数按段总秒归一
    out = "\n".join(strip_sound_phrases(l) for l in out.splitlines())
    out = limit_shot_count(out, seconds)                   # 一段最多 段秒/3 镜（每镜≥3 秒才看得清）
    out = cap_reaction_shots(out)                          # 纯反应镜头最长 3 秒
    out = whitelist_shot_lines(out)                        # 导演夹的注释/说明行删掉（会被念出来）
    if seconds:
        out = normalize_shot_seconds(out, seconds)
    return out


_REAL_MOVES = re.compile(r"跟拍|跟随|环绕|横摇|摇镜|升降|升起|下降|推轨|移镜|手持|甩镜")
_ANGLES = re.compile(r"低角度|仰拍|俯拍|高角度|越肩")


def camera_variety_problems(body_text, first_segment=False):
    lines = [l for l in str(body_text or "").splitlines() if shot_parts(l)]
    if len(lines) < 2:
        return []
    probs = []
    if not any(_REAL_MOVES.search(l) for l in lines):
        probs.append("没有一处真运镜（跟拍/环绕/横摇/升降/推轨）")
    if not any(_ANGLES.search(l) for l in lines):
        probs.append("整段全是平视，没有低角度/俯拍/越肩")
    if first_segment and not re.search(r"远景|大全景", lines[0]):
        probs.append("全片第一段的镜头1 不是远景")
    return probs


def camera_variety_fix(body, shots, idx, first_segment=False):
    """运镜/角度/远景不合格 → 只改括号里的机位运镜和景别，动作与台词一字不动，一次。"""
    probs = camera_variety_problems(body, first_segment)
    if not probs:
        return body
    _dbg("镜头语言不合格", {"问题": probs})
    new = _rewrite_shots(body, "这段镜头行的镜头语言不合格：%s。**只改括号里的机位/运镜和开头的景别词**，动作描述和台词一字不动，镜头数量不变。"
                         "要求：至少一处真运镜（跟拍/环绕/横摇/升降/推轨）；至少一镜非平视（低角度仰拍体型大的、俯拍小的、或越肩）；%s\n【画面稿】\n%s"
                         % ("；".join(probs), ("镜头1 改成远景，看清地形和每个人的位置与大小差。" if first_segment else ""), str(shots or "")[:1500]), shots, idx)
    return new if new and len(camera_variety_problems(new, first_segment)) < len(probs) else body


def _director_review(body, shots, idx):
    """导演复审：清单式一次调用，只改不合格的镜；台词一字不动由 _rewrite_shots 守卫。"""
    if not [l for l in body.splitlines() if shot_parts(l)]:
        return body
    return _rewrite_shots(body, "按你的复审清单逐条检查，只改不合格的那几镜。\n【画面稿】\n" + str(shots or "")[:2500],
                          shots, idx, sys_override=_DIRECTOR_REVIEW_SYS)


_H3_SAY = re.compile(r"<Subject\s*(\d+)>\s*\(S(\d+)\)\s*says:\s*"
                     r"<d>\[Chinese\](.+?)</d>")


def camera_view_owner_name(chars, settings):
    from . import camera_view as _cv
    try:
        return _cv.owner(chars, settings)
    except Exception:
        return ""


def h3_problems(prompt, shots, chars, settings=None):
    """H3 提示词的验收。返回问题清单，空＝过。

    只查三样，都是作者点名会出事的：
      · 台词格式（少一个字符就音质下降、角色互窜）
      · 台词逐字（改了就跟画面稿对不上）
      · Subject 编号和说话人对不对得上（错了就串角色）
    """
    txt = str(prompt or "")
    chars, _ = h3_char_order(chars, settings)
    idx = {c["name"]: i for i, c in enumerate(chars or [], 1)}
    said = {re.sub(r"[^\u4e00-\u9fa5]", "", q)
            for _, q in _pic_parts_dialogue(shots)}
    probs = []
    for line in txt.splitlines():
        if "says:" in line or "<d>" in line or "Subject" in line and "(" in line:
            m = _H3_SAY.search(line)
            if not m:
                if "says" in line or "<d>" in line:
                    probs.append("台词行格式不对，必须是 "
                                 "<Subject N> (SN) says:<d>[Chinese]台词</d>："
                                 + line.strip()[:50])
                continue
            a, b, q = int(m.group(1)), int(m.group(2)), m.group(3)
            if a != b:
                probs.append("Subject %d 和 S%d 对不上：%s" % (a, b, line[:40]))
            nq = re.sub(r"[^\u4e00-\u9fa5]", "", q)
            if said and nq and not any(nq == g or (len(nq) >= 4 and nq in g)
                                       or (len(g) >= 4 and g in nq)
                                       for g in said):
                probs.append("台词「%s」画面稿里查无原话" % q[:20])
            # 说话人和编号对不对：镜头行里写的名字要跟 SN 一致
            who = re.match(r"^([\u4e00-\u9fa5]{2,4})说：", line.strip())
            if who and idx.get(who.group(1)) not in (None, a):
                probs.append("%s 的编号应是 S%d，写成了 S%d"
                             % (who.group(1), idx[who.group(1)], a))
    # 【只在画面稿真有台词时才要求】纯动作段本来就没台词，无条件要求
    # 就是假阳性——今天第三次栽在"检查器比产物还严"上（2026-09-01）。
    if said and not _H3_SAY.search(txt):
        probs.append("画面稿里有 %d 句台词，镜头行里一句都没有" % len(said))
    # 【同一句不许说两遍】实测「两枚银币。」摊主说完埃里克又说一遍——
    # 正文里那是两句不同的话（后一句是"两枚银币太便宜了"）。
    # 原来只查"这句在不在正文里"，重复用查不出来（2026-09-01）。
    seen = {}
    for m in _H3_SAY.finditer(txt):
        nq = re.sub(r"[^\u4e00-\u9fa5]", "", m.group(3))
        if not nq:
            continue
        if nq in seen:
            probs.append("台词「%s」说了两遍（S%s 和 S%s）——"
                         "同一句只能有一个人说一次"
                         % (m.group(3)[:16], seen[nq], m.group(2)))
        else:
            seen[nq] = m.group(2)
    # 【说话人要跟画面稿一致】画面稿里那句是谁说的，镜头行就得是谁
    who_of = {re.sub(r"[^\u4e00-\u9fa5]", "", q): w
              for w, q in _pic_parts_dialogue(shots)}
    num = idx
    for m in _H3_SAY.finditer(txt):
        nq = re.sub(r"[^\u4e00-\u9fa5]", "", m.group(3))
        want = num.get(who_of.get(nq, ""))
        if want and int(m.group(1)) != want:
            probs.append("台词「%s」画面稿里是 %s（S%d）说的，这里写成了 S%s"
                         % (m.group(3)[:14], who_of.get(nq), want, m.group(1)))
    # 正文里出现了没绑参考图的人 / 角色的自我分身 —— 这两样 H3 只能瞎编一张脸
    # （2026-09-05 项目102 段1：编出「对手林野（另一分身）」当敌人）。
    # P47 写了这个检测却只挂在体检脚本上，出片链路一直没查——补上。
    try:
        _own = ""
        try:
            from . import camera_view as _cv456
            if _pov_spec(settings)[0] in _cv456.MODES:
                _own = camera_view_owner_name(chars, settings)
        except Exception:
            _own = ""
        probs += unbound_name_problems(txt, [c.get("name") for c in (chars or []) if c.get("name") != _own])   # P456③：拍摄者本来就不绑图
    except Exception:
        pass
    # 反向那一半：绑定名在正文里一次没出现（正文改叫别的名字了）——P244。
    # unbound_name_problems 只认识卡上的名字，模型自己编的名字它看不见。
    try:
        m0 = re.search(r"画面里只有[^这]{1,40}这\d+个角色", txt)
        probs += body_name_problems(txt[m0.end():] if m0 else txt, chars)
    except Exception:
        pass
    return probs


def h3_critical_problems(prompt, shots="", problems=None, slice_info=None, scene=""):
    """真正会毁掉成片的问题。命中时保留提示词供人工看，但禁止直接送去出片。"""
    src, txt = str(shots or ""), str(prompt or "")
    out = []
    info = slice_info or {}
    try:
        if float(info.get("seconds") or 0) > 15.0:
            out.append("本段超过15秒，必须重新切段")
    except Exception:
        pass
    alien = [w for w in _GARMENT if w in txt and w not in src]
    if alien:
        out.append("提示词编了画面稿没有的衣着/脱衣：%s" % "、".join(alien))
    hard_words = ("台词行格式不对", "一句都没有", "画面稿里查无原话",
                  "画面稿里是", "的编号应是", "没绑参考图的人")
    for p in problems or []:
        if any(w in str(p) for w in hard_words) and str(p) not in out:
            out.append(str(p))
    if not str(scene or "").strip():
        out.append("本段没有绑定场景")
    return out


def _pic_parts_dialogue(shots):
    """画面稿里的台词行 [(名字, 台词)]。"""
    out = []
    for k, p in _pic_parts(shots):
        if not k:
            continue
        m = _PIC_DLG.match(p)
        if m:
            out.append((m.group(1), m.group(3)))
    return out


def visible_cast_text(shots):
    """用于判断谁在镜头里：保留动作叙述和说话人，删掉台词内容中仅被提及的人。"""
    out = []
    for line in str(shots or "").splitlines():
        m = _PIC_DLG.match(line.strip())
        if m:
            out.append(m.group(1))
        else:
            out.append(re.sub(r"[“‘][^”’]*[”’]", "", line))
    return " ".join(out)


_GARMENT = ("睡袍", "睡衣", "浴袍", "浴巾", "内衣", "内裤", "赤裸", "全裸", "裸着", "裸体",
            "换上", "脱下", "脱掉", "湿着头发")


def _strip_alien_garments(line, shots):
    """镜头行里出现了画面稿根本没写的衣着状态 → 删掉含它的那个短句。

    实验 2026-09-02：指令词里拿「白色亚麻睡袍」当例子，模型就把睡袍写进了拥抱戏。
    衣着状态只能来自画面稿，代码逐词对——画面稿里没有的词，整个短句去掉。
    """
    if "says:" in line:
        return line
    src = str(shots or "")
    bad = [w for w in _GARMENT if w in line and w not in src]
    if not bad:
        return line
    head, sep, rest = line.partition("。")
    # 「谁+景别，衣着」在句号前：把逗号后的衣着短句删掉
    parts = [p for p in re.split(r"[，,]", head) if not any(w in p for w in bad)]
    head = "，".join(parts) if parts else head
    # 句号后的动作里也可能带：按短句删
    if rest:
        rest = "，".join(p for p in re.split(r"[，,]", rest) if not any(w in p for w in bad))
    _dbg("镜头行衣着查无出处，删短句", {"词": bad, "行": line[:50]})
    return head + sep + rest


def strip_alien_garments_text(body, shots):
    """时间轴和旧镜头格式共用的衣着守卫；画面稿没写的脱衣/裸露不准进入 H3。"""
    return "\n".join(_strip_alien_garments(line, shots)
                     for line in str(body or "").splitlines())


def _h3_fix(raw, shots, idx):
    """确定性修复：能改对的直接改，改不了的丢掉那一行。"""
    said = {re.sub(r"[^\u4e00-\u9fa5]", "", q)
            for _, q in _pic_parts_dialogue(shots)}
    # 这句在画面稿里是谁说的（去掉括号里的语气注）
    who_of = {re.sub(r"[^\u4e00-\u9fa5]", "", q): re.sub(r"（[^）]*）", "", str(w or "")).strip()
              for w, q in _pic_parts_dialogue(shots)}
    out = []
    for line in str(raw or "").splitlines():
        t = line.rstrip()
        if not t.strip():
            continue
        t = _strip_alien_garments(t, shots)
        # 半角括号、全角冒号这类小偏差，统一成模板的写法。
        # **括号和 says 之间那个空格要留着**：模板原文是「(S2) says:」，
        # 作者说格式一个字都不能改，吃掉空格就是改了（2026-08-31 自查）。
        t = re.sub(r"[（(]\s*[Ss](\d+)\s*[）)]\s*", r"(S\1) ", t)
        t = t.replace("＜", "<").replace("＞", ">")
        t = re.sub(r"\s*says\s*[：:]\s*", " says:", t)
        t = re.sub(r"\(S(\d+)\)\s+says:", r"(S\1) says:", t)
        t = re.sub(r"(<Subject\s*\d+>)\s*\(S", r"\1 (S", t)
        t = re.sub(r"<\s*d\s*>", "<d>", t)
        t = re.sub(r"</\s*d\s*>", "</d>", t)
        t = re.sub(r"\[\s*[Cc]hinese\s*\]", "[Chinese]", t)
        # 漏了语种标记就补上（<d>快跑！</d> → <d>[Chinese]快跑！</d>）
        t = re.sub(r"(says:\s*<d>)(?!\[Chinese\])", r"\1[Chinese]", t)
        # 空台词整行删——<d>[Chinese]</d> 里面什么都没有，发给 H3
        # 就是让人张嘴不出声。这一步要在 _H3_SAY 匹配**之前**做，
        # 因为那个正则要求至少一个字符，空的根本匹配不上（2026-09-01）。
        if re.search(r"says:\s*<d>\[Chinese\]\s*</d>", t):
            _dbg("H3 空台词，丢弃", {"行": t[:60]})
            continue
        # 【补回漏掉的 <Subject N>】模型常写成「老陈 (S1) says:…」，
        # 把前半截的 <Subject N> 漏了。名字→编号是已知映射，直接补
        # （2026-08-31 实测 B 版三行全漏）。
        mm = re.match(r"^([一-龥]{2,4})(说[：:])?\s*\(S(\d+)\)\s*says:", t)
        if mm and "<Subject" not in t:
            who, num = mm.group(1), int(mm.group(3))
            if idx.get(who) in (None, num):
                t = ("%s说：<Subject %d> (S%d) says:" % (who, num, num)
                     + t[mm.end():])
        m = _H3_SAY.search(t)
        if m:
            # 【空台词整行删】实测出过 <d>[Chinese]</d> 里面什么都没有的行，
            # 发给 H3 就是让它张嘴不出声（2026-09-01）。
            if len(re.sub(r"[^一-龥]", "", m.group(3))) < 2:
                _dbg("H3 空台词，丢弃", {"行": t[:60]})
                continue
            a, b = int(m.group(1)), int(m.group(2))
            if a != b:                      # 编号对不上，以 Subject 为准
                t = t.replace("(S%d)" % b, "(S%d)" % a)
            nq = re.sub(r"[^\u4e00-\u9fa5]", "", m.group(3))
            # 【说话人写错直接改对】画面稿里这句是谁说的是确定的：名字和编号都能算出来。
            # V13a 实测「大人……」是莉亚的话，镜头行写成了「埃里克说：<Subject 1> (S1)」。
            _who = who_of.get(nq) or next((w for q, w in who_of.items()
                                           if len(nq) >= 4 and (nq in q or q in nq)), None)
            _want = idx.get(_who) if _who else None
            if _want and _want != a:
                t = re.sub(r"^[一-龥]{2,4}说[：:]", "%s说：" % _who, t)
                t = re.sub(r"<Subject\s*\d+>\s*\(S\d+\)", "<Subject %d> (S%d)" % (_want, _want), t, count=1)
                _dbg("H3 说话人改对", {"句": nq[:12], "改为": "%s S%d" % (_who, _want)})
                a = _want
            if said and nq and not any(nq == g or (len(nq) >= 4 and nq in g)
                                       or (len(g) >= 4 and g in nq)
                                       for g in said):
                _dbg("H3 台词查无原话，丢弃", {"行": t[:60]})
                continue
        out.append(t)
    return _h3_say_norm("\n".join(out))


def _h3_say_norm(text):
    """台词行的最后归一：前缀「名(SN) 说：」→「名说：」；一模一样的台词行只留第一句。"""
    seen, out = set(), []
    for l in str(text or "").splitlines():
        t = re.sub(r"^([一-龥]{1,4})\s*\(S\d+\)\s*说[：:]", r"\1说：", l.strip())
        if _H3_SAY.search(t):
            key = re.sub(r"\s+", "", t)
            if key in seen:
                _dbg("H3 重复台词行，丢弃", {"行": t[:50]})
                continue
            seen.add(key)
        out.append(t if l.strip() else l)
    return "\n".join(out)


# ════════ 覆盖率：靠段号数数，不靠词面比对 ════════
# 用户 2026-09-01：一换题材就出问题，要求按通用模板修，不要打补丁。
# 根本问题是画面稿层没有机制保证"正文每一段都被拍到"，可以随意合并跳过。
# 之前做过逐段词面比对，因为改写换同义词误报太多而拆掉——判据错，路没错。
# 通用解法：正文按段编号发过去，每幅画面标〔N〕说明拍的是第几段，
# 覆盖率就变成**数数**：哪个号没出现就是漏了。跟题材、跟用词全无关，零误报。

# 括号里除了数字，还可能被模型写成「段号12」「段12」——导演审校的提示词通篇写的就是「〔段号〕」。
# 认不出的后果不是少剥一个标记：段号会留进成品（H3 当成画面里的字），
# 逐幅扩写会因为行首是〔而整步跳过，覆盖率检查也数不到（P201 实测）。
_PIC_TAG = re.compile(r"^\s*[〔\[【(（]\s*(?:段号|段|画面|序号|镜头)?\s*(\d{1,3})\s*[〕\]】)）]\s*")


def numbered_prose(prose):
    """正文按段编号，发给画面稿层用。返回 (编号文本, 段数)。"""
    paras = [p.strip() for p in _PIC_SPLIT.split(str(prose or "")) if p.strip()]
    txt = "\n\n".join("〔%d〕%s" % (i + 1, p) for i, p in enumerate(paras))
    return txt, len(paras)


def picture_uncovered(pics, prose):
    """哪几段正文没被拍到。返回 [(段号, 正文原段)]。

    靠段号数数，不比词面。整篇没标号时返回空——那说明模型没照格式写，
    交给别的检查器管，不在这里瞎猜。
    """
    tagged = set()
    for blk in _PIC_SPLIT.split(str(pics or "")):
        m = _PIC_TAG.match(blk.strip())
        if m:
            tagged.add(int(m.group(1)))
    if not tagged:
        return []
    paras = [p.strip() for p in _PIC_SPLIT.split(str(prose or "")) if p.strip()]
    out = []
    for i, p in enumerate(paras, 1):
        if i in tagged:
            continue
        # 纯台词段不用单独成画面，台词有自己的检查器
        bare = re.sub(r"[「“][^」”\n]*[」”]", "", p)
        if len(re.sub(r"[^\u4e00-\u9fa5]", "", bare)) < 8:
            continue
        out.append((i, p))
    return out


def strip_pic_tags(pics):
    """剥掉〔N〕段号——它只给检查器用，进了下游 H3 会当成画面里的字。"""
    out = []
    for blk in _PIC_SPLIT.split(str(pics or "")):
        b = blk.strip()
        if b:
            out.append(_PIC_TAG.sub("", b))
    return "\n\n".join(out)



def cast_from_prose(prose, settings=None):
    """从正文认出这一话有哪些人。返回 [{"name":…, "note":…}]。

    **复用 extract_dialogue 的说话人**，不另调一次模型——
    说话的人就是这一话的主要人物，而那份表 18 句全对（2026-09-01 实测）。
    原来单独调一次认人，同一段正文一次认出 3 个、一次只认出 1 个，不稳。
    只在人物卡为空时才走这里；有卡就用卡，卡是权威。
    """
    src = str(prose or "")
    if len(src) < 50:
        return []
    seen, out = set(), []
    for w, _q in extract_dialogue(src):
        w = re.sub(r"（[^）]*）", "", str(w or "")).strip()
        if not w or w in seen or w.startswith(("画外音", "异声")):
            continue
        if len(w) > 8 or src.find(w) < 0:
            continue
        seen.add(w)
        out.append({"name": w, "note": _cast_note(w, src)})
    _dbg("从正文认人", {"认出": [x["name"] for x in out]})
    return out


def _cast_note(name, prose):
    """这个人在正文里的一句话简介。

    **只取名字紧挨着的那一句**——按"名字第一次出现往后截 220 字"去摘，
    会把隔壁那个人的长相摘过来（摊主摘到了埃里克蹲下和诺拉的眼睛，
    2026-09-01 实测）。摘不准就留空，长相由人物卡负责，别在这里瞎凑。
    """
    src = str(prose or "")
    for m in re.finditer(re.escape(name), src):
        a = max(src.rfind(c, 0, m.start()) for c in "。！？\n") + 1
        ends = [i for i in (src.find(c, m.end()) for c in "。！？\n")
                if i >= 0]
        b = (min(ends) + 1) if ends else len(src)
        sent = src[a:b].strip()
        if 10 <= len(sent) <= 90 and re.search(
                r"(岁|少年|少女|中年|头发|穿|袍|衣|眼|身量|身形|瘦|高|胖)", sent):
            return sent
    return ""




# ════════ 两步提取：正文 → 人物卡/场景卡（2026-09-01 上线）════════
# 用户定的两步：① 只摘正文里已经写了的，没写的空着；② 空的交给设计师去补。
# 中间不做"补空栏+标来源+验填错"——那是替设计师做它本来该做的事。
_CARD_PERSON_F = ["年龄", "性别", "身份", "身形", "头发", "衣着", "五官特征",
                  "标志物", "声音", "动作习惯"]
_CARD_SCENE_F = ["内外", "时间", "空间", "光线", "地面墙面", "陈设", "标志物",
                 "气味声音"]
# 空值的说法很多，别只匹配开头——「文中未提及」以"文"开头就漏过去了
_CARD_EMPTY = re.compile(r"(未提|未知|未写|不详|不明|没有写)|^(无|—|-)$")


def card_one_line(kind, name, fields):
    """把卡压成喂给设计师的一句话；空着的栏明写出来，让设计师去补。

    两步提取的第二步入口：提取器只搬正文有的，剩下的空栏交给设计师设计
    （用户 2026-09-01 定的两步流程）。
    """
    want = _CARD_PERSON_F if kind == "人物" else _CARD_SCENE_F
    known = "；".join("%s：%s" % (k, fields[k]) for k in want if k in fields)
    blank = [k for k in want if k not in fields]
    one = "%s。%s" % (name, known)
    if blank:
        one += "。（正文没写：%s——按%s和这个世界设计）" % (
            "、".join(blank),
            "他的身份、经历" if kind == "人物" else "这个地方的用途")
    return one



def arrangement_ask_block(arr):
    """给画面稿层看的【本话安排】：四段各一行、主要拍什么、结尾锁死。没安排 → 空串。
    放在 user 消息里而不是 system：这是这一话的事实，不是写法。"""
    if not isinstance(arr, dict):
        return ""
    from . import arrangement as _arm
    beats = arr.get("beats") if isinstance(arr.get("beats"), list) else []
    lines = ["【本话安排（已确认，画面必须照它）】"]
    for i, st in enumerate(_arm.STAGES):
        b = beats[i] if i < len(beats) else None
        t = str((b or {}).get("text") if isinstance(b, dict) else (b or "")).strip()
        if t:
            lines.append("· %s：%s" % (st, t))
    shoot = str(((arr.get("items") or {}).get("shoot") or {}).get("text") or "").strip()
    if shoot:
        lines.append("· 主要拍什么：%s" % shoot)
    where = (arr.get("items") or {}).get("where") or {}
    _wt = str(where.get("text") or "").strip()
    _pl = [str(x).strip() for x in (where.get("places") or []) if str(x).strip()]
    if _wt or _pl:
        # 不写场景，模型会自己换地方（实测把面包店画成了杂货铺）
        lines.append("· 用哪些场景：%s%s" % (_wt, ("（地点：%s）" % "、".join(_pl)) if _pl else ""))
    lines.append("· 最后一幅画面必须是「结束」那条描述的画面，之后不再加画面。")
    return "\n".join(lines) if len(lines) > 2 else ""


def prose_to_pictures(prose, settings, characters=None, instruction=None, arrangement=None):
    """正文 → 画面稿。**整条链路最多两次调用**：写一次，有缺口补一次。

    第一次调用里带上代码数出来的两个数字（正文几段、几句台词），
    让模型有个能自查的目标——今天反复验证：描述性的篇幅要求
    （"每段 80~150 字"）它不执行，可数的目标（"故事有 23 段，
    你要写够 23 段"）它照做（2026-08-31）。
    arrangement：已确认的本话安排——四段情节和结尾写进 ask，画面稿不许自己换结尾。
    """
    src = str(prose or "")
    n_para = len([p for p in _PIC_SPLIT.split(src) if p.strip()])
    quotes = [q for q in re.findall(r"[「“]([^」”\n]+)[」”]", src)
              if len(re.sub(r"[^\u4e00-\u9fa5]", "", q)) >= 2]
    names = [str(c.get("name") or c).strip()
             for c in (characters or [])] if characters else []
    names = [n for n in names if n]
    # 【人物卡为空就先认一遍人】没有名单时规整器没名字可查，
    # 台词全掉成画外音（2026-09-01 实测 18 句里 17 句）。
    # 缺的是数据不是措辞——改指令词没用，反而更差。
    cast = characters or []
    if not names:
        cast = cast_from_prose(src, settings)
        names = [c["name"] for c in cast]

    # 【剧本要吃「镜头」层】内容尺度分三层（story/figure/shot），剧本是决定
    # 镜头看什么的地方，只给 story 层等于镜头层白写——新链路里 H3 提示词是纯
    # 格式转换不发明内容，所以镜头层只能在这儿进（2026-09-02 查实：shot 层
    # 唯一的消费者 video_visual_block 只有旧链路在调）。
    ins = _fill(instruction or _ins("正文改画面稿_指令词.txt"),
                settings, cast, scale_layers=("story", "shot"))
    numbered, n_para = numbered_prose(src)
    _arr_txt = arrangement_ask_block(arrangement)
    ask = ("【故事（每段前面的〔N〕是段号）】\n%s\n\n%s"
           "把这个故事写成画面。\n"
           "这个故事有 **%d 段**，里面有 **%d 句**带引号的话。\n"
           "· **每一段都要变成画面，一段都不许跳过**——"
           "每幅画面开头标上它拍的是第几段〔N〕；\n"
           "· **第一幅画面先交代这是什么地方**（不拍人，退到最远），"
           "第二幅才开始拍人；\n"
           "· %d 句台词**一句都不许少**，每句单独占一行，写成「名字：台词」；\n"
           "· 从故事的第一句写到最后一句，**结尾那个没做完的动作／没说完的话，"
           "原样停在那里**。\n"
           "写完从头数一遍段号：1 到 %d，中间有没有断号。"
           % (numbered, (_arr_txt + "\n\n") if _arr_txt else "",
              n_para, len(quotes), len(quotes), n_para))
    raw = _q(ins, ask, mt=9000, temperature=0.75).strip()
    raw = strip_method_marks(fix_traditional(raw))
    out = repair_pictures(raw, src, names)
    # 段号只是给检查器数覆盖率用的，不能进下游——H3 收到〔3〕会当成画面里的字
    out = strip_pic_tags(out)
    _dbg("画面稿完成", {"正文段": n_para, "正文台词": len(quotes),
                        "画面段": len([1 for k, _ in _pic_parts(out) if not k]),
                        "台词行": len([1 for k, _ in _pic_parts(out) if k])})
    return out

def _prose_quotes(prose):
    """正文里所有真台词（去标点），拿来判这句是不是编的。

    引号是现成的，不用调模型。去掉台词表之后一次都没人挡编造的台词，
    实测四次里出现「阿杰，跟紧点。」这种正文没有的句子，
    还有把「干燥」当台词的（2026-08-31）。
    """
    out = set()
    src = str(prose or "")
    for m in re.finditer(r"[「“]([^」”\n]+)[」”]", src):
        q = re.sub(r"[^一-龥]", "", m.group(1))
        tail = src[m.end():m.end() + 1]
        if len(q) >= 2 and not (tail and tail in "字声样般似的" and len(q) <= 3):
            out.add(q)
    return out


def normalize_pictures(pics, prose="", names=None):
    """把画面稿规整成下游能用的形状。**只搬运和切分，不改写一个字。**

    ① 段落里嵌着的台词提出来，单独成行「名字：台词」——嵌在描述里
       下游没法单独提去配音和做字幕。
    ② 一段超过 200 字的按句号切开——摄影机拍不了 290 字那么长的一"幅"画面。

    说话人**查正文**，不从画面稿里猜：正文里每句台词是谁说的是确定的，
    先建一张 {台词 → 说话人} 表，画面稿里遇到引号直接查。
    正则猜说话人这条路改了六轮全是修一处冒两处
    （「老陈吼」「阿杰惨」「攥住扳」），弃用（2026-08-31）。
    """
    text = str(pics or "")
    who_of = _name_list(names, prose)
    ok_q = _prose_quotes(prose)
    out = []
    for blk in _PIC_SPLIT.split(text):
        blk = blk.strip()
        if not blk:
            continue
        if blk.startswith("──"):
            out.append(blk)
            continue
        if _is_clean_dialogue(blk):
            _t = re.sub(r"[^\u4e00-\u9fa5]", "",
                        _PIC_DLG.match(blk).group(3))
            if ok_q and _t and not _quote_ok(_t, ok_q):
                _dbg("台词行查无原话，丢弃", {"行": blk[:50]})
                continue
            out.append(blk)
            continue
        rest, said = _pull_dialogue(blk, who_of, ok_q)
        chunks = [rest] if len(rest) <= 200 else _split_long_pic(rest)
        for c in chunks:
            if len(re.sub(r"[^\u4e00-\u9fa5]", "", c)) >= 8:
                out.append(c.strip())
        out.extend(said)
    _txt = "\n\n".join(out)
    # 说话人在叫自己 → 改对（P247）。放最后：上游候选名单里没有"没卡的人"，
    # 这里补上在场配角再判一次。
    try:
        _txt2, _sp = fix_pictures_speakers(_txt, who_of)     # 现在只报不改（P248）
        if _sp:
            _dbg("画面稿说话人可疑", {"条目": _sp})
    except Exception:
        pass
    return _txt


_SAYV = "说|问|喊|吼|叫|道|应|答|嘀咕|开口|低语"
_PRON = ("他", "她", "它", "他们", "她们", "对方", "两人", "声音", "一个声音")
# 引号周围那一小段"说话动作"，整段摘掉，不留半截
_SAY_ACT = re.compile(
    "[\u4e00-\u9fa5]{0,6}?(?:%s)(?:[了着过道]{0,2})(?:一声|一句|起来)?[，。：:]?"
    % _SAYV)


def _person_names(prose):
    """从正文里收集人名白名单。

    判据：2~3 个汉字，且在正文里至少两次紧跟着"人做的动作"
    （说、问、抬手、转身、蹲下、盯着…）。
    靠正则切"名字+动词"里的名字，边界永远切不准（「他吼道」→"他吼"、
    「阿杰惨叫」→"阿杰惨"，栽了七轮）；先定下名字有哪些，就不用切了。
    """
    src = str(prose or "")
    act = ("说|问|喊|吼|叫|道|抬|转|蹲|站|走|盯|松开|抓起|举起|伸手|低头|"
           "回头|皱|咬|点头|摇头|笑|哼|扭|迈|退|冲|扑|摸")
    cnt = {}
    for m in re.finditer(r"([\u4e00-\u9fa5]{2,3})(?:%s)" % act, src):
        w = m.group(1)
        if w in _PRON or re.search("(?:%s)" % _SAYV, w):
            continue
        cnt[w] = cnt.get(w, 0) + 1
    names = {w for w, c in cnt.items() if c >= 2}
    # 三字名和它的二字前缀同时命中时（「老陈头」/「老陈」），留出现多的那个
    for w in list(names):
        if len(w) == 3 and w[:2] in names and cnt.get(w, 0) < cnt.get(w[:2], 0):
            names.discard(w)
    return names


def _name_list(names, prose=""):
    """这一话的人物名单。项目里给了就用给的，没给就从正文里认。

    名单是**现成的输入**，不该去正文里推——之前按词频推名单，
    测试用的短正文里每个名字不到两次，名单直接空了（2026-08-31）。
    """
    out = []
    for n in (names or []):
        n = str(n or "").strip()
        if n:
            out.append(n)
    if out:
        return out
    # 没给名单：取正文里"紧跟着说话动词"出现最多的那几个词，兜底用
    cnt = {}
    for m in re.finditer(r"([\u4e00-\u9fa5]{2,3})(?:说|问|喊|吼|叫|道)",
                         str(prose or "")):
        w = m.group(1)
        if w not in _PRON:
            cnt[w] = cnt.get(w, 0) + 1
    return [w for w, c in sorted(cnt.items(), key=lambda x: -x[1]) if c >= 2]


def _is_clean_dialogue(line):
    """这一行本来就是干净的「名字：台词」吗。说话人不能带标点或说话动词。"""
    if len(line) > 200 or "\n" in line:
        return False
    m = _PIC_DLG.match(line)
    if not m:
        return False
    if re.search("(?:%s)" % _SAYV, m.group(1)):
        return False
    return not re.search(r'["\u201c\u300c]', m.group(3))


def _quote_ok(nq, ok_q):
    """这句台词在正文里查得到吗。一模一样，或者覆盖原话七成以上
    （同一个人连说两句被合成一行是对的写法，要放行）。"""
    for g in ok_q:
        if nq == g:
            return True
        if nq in g and len(nq) >= max(4, len(g) * 0.7):
            return True
        if g in nq and len(g) >= max(4, len(nq) * 0.5):
            return True
    return False


def _pull_dialogue(blk, names, ok_q=None):
    """从一段画面里把台词摘出来。返回 (剩下的画面, [台词行])。

    说话人＝这一段里、引号**之前**最近出现的那个已知人名。
    名单是给定的，所以不用猜名字边界——之前八轮全栽在
    「阿杰惨叫」切出"阿杰惨"这类边界问题上（2026-08-31）。
    """
    said, spans = [], []
    for m in _PIC_SAY.finditer(blk):
        q = m.group(1)
        nq = re.sub(r"[^\u4e00-\u9fa5]", "", q)
        if len(nq) < 2:
            continue                      # 「川」字这种不是台词
        # 【拟声和字形不是台词】发出"滋滋"声、皱成"川"字。判据跟
        # _quote_chain 一套：紧跟着 字/声/样 的短引号排除。漏了这一条，
        # 「滋滋」被当台词提出来，18 句台词顺序全对却因为它判挂
        # （2026-08-31 实测）。再加一道：正文台词表里查不到的，不是台词。
        nxt = blk[m.end():m.end() + 1]
        if nxt and nxt in "字声样般似的" and len(nq) <= 3:
            continue
        # 【只认正文里真有的台词】没有白名单挡着，模型编的
        # 「阿杰，跟紧点。」和把「干燥」当台词的都会被提成台词行
        # （2026-08-31 实测四次里挂三次）。
        # 宽松到子串包含会放过缩写：正文「跃迁引擎预热到百分之九十，
        # 现在断油就全完了。」被缩成「跃迁引擎预热90%」，
        # 前六个字是子串就放行了（2026-08-31 实测挂两次）。
        # 收紧成：要么一模一样，要么覆盖原话七成以上。
        if ok_q is not None and not _quote_ok(nq, ok_q):
            continue

        a, b = m.start(), m.end()
        # 紧贴引号前面的那段说话动作，整段摘掉
        pre = blk[:a]
        mp = None
        for mm in _SAY_ACT.finditer(pre):
            if mm.end() >= len(pre):
                mp = mm
        if mp is not None:
            a = mp.start()
        else:                             # 引号在前、说话动作在后
            mm = _SAY_ACT.match(blk[b:])
            if mm is not None:
                b = b + mm.end()
        pre_txt = blk[:m.start()]
        hits = [(pre_txt.rfind(n), n) for n in names if n in pre_txt]
        said.append((max(hits)[1] if hits else "", q))
        spans.append((a, b))
    if not spans:
        return blk, []
    keep, prev = [], 0
    for a, b in spans:
        keep.append(blk[prev:a])
        prev = b
    keep.append(blk[prev:])
    rest = re.sub(r"\s+", "", "".join(keep))
    rest = re.sub(r"[，、；]{2,}", "，", rest)
    rest = re.sub(r"[，、；]+(?=[。！？])", "", rest)
    rest = re.sub(r"^[，、；。：:]+|[，、；：:]+$", "", rest).strip()
    return rest, ["%s：%s" % (w, q) if w else "画外音：%s" % q for w, q in said]


def _split_long_pic(para, lo=80, hi=170):
    """把过长的一段按句号切成几段，每段 lo~hi 字，尽量在句子边界上切。"""
    sents = [x for x in re.split(r"(?<=[。！？…])", str(para or "")) if x.strip()]
    out, cur = [], ""
    for sent in sents:
        if cur and len(cur) + len(sent) > hi:
            out.append(cur)
            cur = sent
        else:
            cur += sent
    if cur:
        if out and len(cur) < lo:
            out[-1] += cur
        else:
            out.append(cur)
    return out

def _is_onomat(q, src, pos=None):
    """这段引号里的是拟声／字形，不是台词吗。

    两种写法都要认：
      · 引号紧跟着量词：发出"滋滋"声、皱成"川"字
      · 中间隔一两个字：「"咔哒"一声」「"咔哒"的一声」
    检查器和修复器共用这一份——两套判据会互相打架：
    修复器把「咔哒」删了，检查器还在报"这句台词丢了"（2026-09-01 实测）。
    """
    nq = re.sub(r"[^\u4e00-\u9fa5]", "", str(q or ""))
    if len(nq) > 3:
        return False
    if pos is not None:
        tail = str(src or "")[pos:pos + 1]
        if tail and tail in "字声样般似的":
            return True
    return bool(re.search(re.escape(nq) + r"[」”]?\s*[的一了]{0,2}\s*[声响]",
                          str(src or "")))


def picture_missing_dialogue(pics, prose, people=()):
    """正文里哪几句**当前说出口**的话在画面稿里找不到（P310：口径统一到台词清单）。
    返回 [(原话, 说话人或空, 确定/存疑, 正文里说了几次)]；同一句正文说了 n 次、画面稿里少于 n 次也算缺。"""
    from . import dialogue_ledger as _dl
    def _n(x):
        return re.sub(r"[^\w一-龥]", "", str(x or ""))
    said = [_n(q) for _, q in _pic_parts_dialogue(pics)]          # 只认独立成行的台词（去掉说话人）
    led = _dl.ledger(prose, people)
    miss, seen = [], {}
    for e in _dl.spoken(led):
        seen[e["norm"]] = seen.get(e["norm"], 0) + 1
        have = sum(1 for t in said if e["norm"] and (e["norm"] == t or (len(e["norm"]) >= 4 and e["norm"] in t)))
        if len(e["norm"]) >= 1 and have < seen[e["norm"]]:
            miss.append((e["text"], e["speaker"], e["confidence"], e["count"]))
    return miss


def picture_missing_events(pics, prose, need=2):
    """正文哪几段在画面稿里没有对应的画面。返回 [(段序号, 正文原段)]。

    判据：一段正文里挑出最有辨识度的几个词（长度≥2 的名词性片段），
    画面稿里命中不到 need 个，就算这一段没拍。
    比的是**词**不是句子——画面稿本来就是改写，逐句比对必然全丢。
    """
    def _n(x):
        return re.sub(r"[^\w一-龥]", "", str(x or ""))
    body = _n("".join(t for _, t in _pic_parts(pics)))
    miss = []
    paras = [p.strip() for p in _PIC_SPLIT.split(str(prose or "")) if p.strip()]
    for i, para in enumerate(paras):
        # 去掉引号里的台词再取特征词——台词另有检查器管
        bare = re.sub(r"[「“][^」”\n]*[」”]", "", para)
        toks = [w for w in re.findall(r"[\u4e00-\u9fa5]{2,4}", bare)
                if w not in _PIC_STOP]
        if len(toks) < 3:
            continue
        hit = sum(1 for w in set(toks) if body.find(w) >= 0)
        if hit < need:
            miss.append((i + 1, para))
    return miss


def _pic_event_gaps(pics, prose):
    """情节缺口，去掉"其实只是台词没搬过来"的那几段。

    「"怎么回事？"老陈问，心里咯噔一下。」这种段落，画面本来就拍出来了
    （指示灯变黄那一镜），丢的只是那句台词——台词另有检查器管，
    这里再报一次就是同一个缺口报两遍，补写会补出重复的画面
    （2026-08-31 自查）。判据：这一段去掉引号和心理句之后，
    剩下的实际动作不足 6 个字的，不算情节缺口。
    """
    out = []
    for i, para in picture_missing_events(pics, prose):
        bare = re.sub(r"[「“][^」”\n]*[」”]", "", para)
        bare = re.sub(r"[^\u4e00-\u9fa5]", "", re.sub(
            r"(心里|心中|心头)[^，。！？]*", "", bare))
        bare = re.sub(r"^(某某|老陈|阿杰)?(说|问|喊|道|低声说|开口)?", "", bare)
        if len(bare) >= 6:
            out.append((i, para))
    return out


_PIC_STOP = set("这个那个什么怎么这样那样一个一样已经然后现在还是就是不是可是"
                "但是因为所以如果虽然只是自己他们她们我们你们东西时候地方样子"
                "起来下去过来过去出来进去".split()) | {
    "这个", "那个", "什么", "怎么", "这样", "那样", "一个", "一样", "已经",
    "然后", "现在", "还是", "就是", "不是", "可是", "但是", "因为", "所以",
    "如果", "虽然", "只是", "自己", "他们", "她们", "我们", "你们", "东西",
    "时候", "地方", "样子", "起来", "下去", "过来", "过去", "出来", "进去",
}



def picture_tail_gap(pics, prose):
    """画面稿有没有把故事讲完。返回没拍到的那一截正文（讲完了就是空串）。

    从正文最后一段往前走，找到第一段"确实拍到了"的，它后面的全算丢了。
    只用词命中找边界，不用它判每一段——改写换同义词的噪声，
    在找边界时无所谓，在判每段时会把正常的稿子全判成缺（2026-08-31 实测）。
    """
    def _n(x):
        return re.sub(r"[^\u4e00-\u9fa5]", "", str(x or ""))
    body = _n("".join(t for _, t in _pic_parts(pics)))
    paras = [p.strip() for p in _PIC_SPLIT.split(str(prose or "")) if p.strip()]
    if not paras or not body:
        return str(prose or "")

    # 每个词在正文里出现在几段——只有"独有词"（只在一两段出现的）才作数。
    # 「老陈」「阿杰」这种通篇都是的词，前四段里也有，拿它判后半段拍没拍
    # 必然漏报（第一版就是这么砍掉一半还判"完整"的）。
    from collections import Counter
    df = Counter()
    for p in paras:
        for w in set(re.findall(r"[\u4e00-\u9fa5]{2,4}", p)):
            df[w] += 1

    def covered(para):
        bare = re.sub(r"[「“][^」”\n]*[」”]", "", para)
        toks = {w for w in re.findall(r"[\u4e00-\u9fa5]{2,4}", bare)
                if w not in _PIC_STOP and df[w] <= 2}
        if len(toks) < 2:
            return None          # 判不了（纯台词段／太短），交给台词检查器
        hit = sum(1 for w in toks if body.find(w) >= 0)
        return hit >= max(1, len(toks) * 0.34)

    # 【判不了的段要跳过去，不能当成"拍到了"】
    # 正文最后一段常常是纯台词（「"我们……到家了。"」），没有独有词可判。
    # 原来给它返回 True，它就成了倒着走的刹车——第一步就停下，
    # 砍掉 44/48 段的稿子照样判"完整"（2026-08-31 实测漏报三次）。
    cut = len(paras)
    while cut > 0:
        c = covered(paras[cut - 1])
        if c is True:
            break
        cut -= 1            # 没拍到（False）或判不了（None），都继续往前找
    if cut >= len(paras):
        return ""
    # 只有连着掉了两段以上才算真截断——最后一段没对上多半是换了说法
    if len(paras) - cut < 2:
        return ""
    gap = "\n\n".join(paras[cut:])
    # 【小缺口是噪声，不是截断】结尾一两段换了个说法（正文「一个声音直接在
    # 老陈的脑海里响起」，画面稿写「一个声音在老陈脑海响起」）就会报出
    # 二三十字的假缺口。补它反而会让结尾重复一遍，钩子被顶掉。
    # 不到正文 5%、或者不足 80 字的，一律不算（2026-08-31 实测）。
    if len(gap) < max(80, len(str(prose or "")) * 0.05):
        return ""
    return "\n\n".join(paras[cut:])


def fix_picture_speakers(pics, prose, names=None):
    """画面稿里认不出说话人的台词行补上名字。**只补名字，不改台词内容。**

    说话人归属用 extract_dialogue（一次模型调用），不用正则。
    2026-09-01 两次验证：
      · 模型做这件事 18 句全对，包括代词指代、插入语拆开的两句；
      · 正则试了八轮全败——「阿杰惨叫」切出"阿杰惨"、
        「一问一答交替」在三人对话里全错、位置对齐把买家卖家弄反。
    中文的指代消解正则做不了，这是今天最贵的一条教训。
    只在真有画外音要补时才调，没有就不花这次调用。

    顺带把不是台词的行删掉（拟声词「咔哒」被写成台词行）。
    """
    text = str(pics or "")
    src = str(prose or "")
    ok_q = _prose_quotes(src)

    # ① 不是正文台词的行整行删（拟声、字形、编造）
    kept = []
    for line in text.splitlines():
        m = _PIC_DLG.match(line.strip())
        if m and len(line.strip()) <= 200:
            nq = re.sub(r"[^\u4e00-\u9fa5]", "", m.group(3))
            # 拟声不是台词，判据跟检查器共用一份 _is_onomat
            if _is_onomat(m.group(3), src):
                _dbg("画面稿：拟声不是台词，删行", {"行": line.strip()[:36]})
                continue
            if nq and not _quote_ok(nq, ok_q):
                _dbg("画面稿：不是正文台词，删行", {"行": line.strip()[:40]})
                continue
        kept.append(line)
    text = "\n".join(kept)

    # ② 还剩画外音的才调模型认说话人
    if not [p for k, p in _pic_parts(text) if k and p.startswith("画外音")]:
        return text
    table = {}
    for w, q in extract_dialogue(src):
        if w and not w.startswith(("画外音", "异声")):
            table[re.sub(r"[^\u4e00-\u9fa5]", "", q)] = w
    out = []
    for line in text.splitlines():
        m = _PIC_DLG.match(line.strip())
        if m and m.group(1) == "画外音" and len(line.strip()) <= 200:
            nq = re.sub(r"[^\u4e00-\u9fa5]", "", m.group(3))
            w = table.get(nq)
            if not w:
                for g, ww in table.items():
                    if nq and (nq in g or g in nq) and min(len(nq), len(g)) >= 4:
                        w = ww
                        break
            if w:
                line = "%s：%s" % (w, m.group(3))
        out.append(line)
    return "\n".join(out)



def dedupe_picture_dialogue(pics, prose):
    """画面稿里同一句台词只能出现一次；被截短的补回正文全句。

    做法：正文台词按顺序排好，画面稿的台词行也按顺序走，逐句配对。
    第 k 句台词行如果只是正文第 k 句的一个片段（被截短了），
    就换成正文那一句的全文；如果它和已经用过的某一句重复，整行删掉。
    位置对齐，不猜词义（2026-09-01）。
    """
    text = str(pics or "")
    # 正文台词以确定性的台词清单为准。extract_dialogue 是模型判断，曾把
    # “铁剑横扫……”整段动作认成台词并保留下来（STORY_213）。
    from . import dialogue_ledger as _dl
    seq = [(e.get("norm") or "", e.get("speaker") or "", e.get("text") or "")
           for e in _dl.spoken(_dl.ledger(str(prose or "")))]
    if not seq:
        return text
    out, used, k = [], set(), 0
    for line in text.splitlines():
        m = _PIC_DLG.match(line.strip())
        if not m or len(line.strip()) > 200:
            out.append(line)
            continue
        nq = re.sub(r"[^\u4e00-\u9fa5]", "", m.group(3))
        # 从当前进度往后找第一句还没用过、且对得上的
        # 【全表找，不限进度】画面稿的台词顺序跟正文不完全一致，
        # 只从当前进度往后找会错过乱序的那些——27 句被砍到 6 句，
        # 丢了 10 句真台词（2026-09-01）。改成全表找，只保证不重复用。
        hit = -1
        for j in range(0, len(seq)):
            g = seq[j][0]
            if j in used or not g:
                continue
            if nq == g or (len(nq) >= 3 and nq in g) or \
                    (len(g) >= 3 and g in nq):
                hit = j
                break
        if hit < 0:
            # 配不上任何一句还没用过的正文台词——要么是重复
            # （前面已经说过了），要么是编的。两种都删。
            _dbg("画面稿：台词重复或编造，删行", {"行": line.strip()[:40]})
            continue
        used.add(hit)
        g, w, orig = seq[hit]
        # 被截短了就补回全句；说话人以台词表为准
        who = w or m.group(1)
        # 台词必须逐字回到正文。画面稿偶尔把相邻两句合成一行；若保留
        # 模型那一整行，前一句会在上一行和这里重复出现。
        out.append("%s：%s" % (who, orig))
    return "\n".join(out)

def repair_pictures(pics, prose, names=None):
    """有缺口就补一次。**整条链路最多两次调用**（写一次 + 补一次），
    用户 2026-08-31 定。一次补不完的就留着，不再多轮——多轮的收益
    （四次测试里多补回一两句）不值第三次调用。
    """
    cur = dedupe_picture_dialogue(fix_picture_speakers(
        normalize_pictures(str(pics or ""), prose, names), prose, names), prose)
    if not picture_missing_dialogue(cur, prose) \
            and not picture_tail_gap(cur, prose):
        return cur
    # 【补到没有为止，最多三轮】原来只补一轮就收工，剩下的台词永远补不上
    # ——实测「再不起来，今天的份例金就要被扣光了。」检查器报了丢，
    # 补写一轮没补进去就放行了（2026-09-01 用户反馈）。
    def _gap(x):
        return (len(picture_missing_dialogue(x, prose))
                + (1 if picture_tail_gap(x, prose) else 0))

    best, best_n = cur, _gap(cur)
    for _ in range(0 if lean_script() else 3):        # 精简剧本链：不做补漏扩写（P284，每轮一次模型调用）
        if best_n == 0:
            break
        nxt = dedupe_picture_dialogue(fix_picture_speakers(
            normalize_pictures(_repair_pictures_once(best, prose, names),
                               prose, names), prose, names), prose)
        n2 = _gap(nxt)
        if n2 >= best_n:          # 没变好就停，别越补越肿
            break
        best, best_n = nxt, n2
    return best


def _repair_pictures_once(pics, prose, names=None):
    """把丢掉的台词和情节补回画面稿。**只补，不改已有的一个字。**

    做法跟正文层换句一样：代码挑出缺口 → 只把缺口发给模型 →
    只收回补写的那几段 → 代码插回去。补写的每一段单独验收，
    验不过就丢掉那一段，不影响别的。
    """
    text = str(pics or "")
    dl = picture_missing_dialogue(text, prose)
    tail = picture_tail_gap(text, prose)
    miss = picture_uncovered(text, prose)      # 按段号数，零误报
    if not dl and not tail and not miss:
        return text
    _dbg("画面稿缺口", {"丢台词": len(dl), "丢尾巴": len(tail),
                        "台词": [x[0][:20] for x in dl[:6]],
                        "尾巴开头": tail[:40]})
    items = []
    for i, para in miss[:8]:
        items.append("%d. 【正文第 %d 段没写成画面，补一幅】〔%d〕%s"
                     % (len(items) + 1, i, i, para[:220]))
    if tail:
        items.append("%d. 【这一截故事整个没写成画面，从这里接着写下去】\n%s"
                     % (len(items) + 1, tail[:1200]))
    for q, who, *_rest in dl[:10]:
        items.append("%d. 【这句台词丢了】%s说：「%s」"
                     % (len(items) + 1, who or "有人", q))
    sysmsg = (
        "你在补一份分镜画面稿。用户给你几段【原故事里有、但画面稿里漏掉的东西】，"
        "你把每一条写成一段能拍的画面。\n"
        "· 漏掉的情节：写成一段画面——谁在哪、在做什么、手上脸上什么样、"
        "周围看得见什么、光从哪来、什么东西在动。80~150 字。\n"
        "· 漏掉的台词：先写一句这个人说话时的样子，再另起一行写"
        "「名字：台词」，台词一个字都不许改。\n"
        "规则：\n"
        "① 只写用户给的这几条，不要写别的；\n"
        "② 不许出现「他心里」「他觉得」「他知道」这类摄影机拍不到的话；\n"
        "③ 不打比方，用大白话直接写看得见的东西；\n"
        "④ 逐条回复，格式「序号. 补写的内容」，一条一条来，"
        "条与条之间空一行，不要解释。")
    try:
        rep = _q(sysmsg, "\n\n".join(items), mt=3000, temperature=0.6)
    except Exception:
        return text
    got = {}
    for blk in re.split(r"\n\s*\n", str(rep or "")):
        m = re.match(r"^\s*(\d+)\s*[.、．)]\s*(.+)$", blk.strip(), re.S)
        if m:
            got[int(m.group(1))] = re.sub(r"^【[^】]{1,14}】", "",
                                          m.group(2).strip()).strip()
    ok = 0
    add = []
    for n in range(1, len(items) + 1):
        new = got.get(n, "")
        why = ("没给" if len(new) < 20
               else "还有拍不到的" if re.search(
                   r"(心里|心中|心头|觉得|知道|意识到|想起)", new)
               else "")
        if why:
            _dbg("补写被拒", {"第几条": n, "原因": why, "内容": new[:60]})
            continue
        add.append(new)
        ok += 1
    _dbg("补写结果", {"缺口": len(items), "补上": ok})
    if not add:
        return text
    parts = [p for p in _PIC_SPLIT.split(text) if p.strip()]
    # 尾巴缺口接在末尾（结尾那个钩子就在里面）；
    # 补回来的台词要插到它在正文里该在的位置——一律接末尾会被保真判成
    # "顺序倒挂"：「再试一次。」是正文第二句，补到篇尾就成倒叙了（2026-08-31）。
    # 尾巴缺口接在末尾（结尾那个钩子就在里面）；补回来的台词要归到
    # 它在正文里该在的位置——不然保真会判"顺序倒挂"（「压力阀锁死了。」
    # 是正文第一句，补到后面就成了倒叙）。
    # 顺序直接读正文里引号的先后，不用调模型——排序只要顺序，不要说话人。
    order = [re.sub(r"[^\u4e00-\u9fa5]", "", q)
             for q in re.findall(r"[「“]([^」”\n]+)[」”]", str(prose or ""))]

    def _rank(seg):
        """这一段里的台词在正文里排第几；没有台词返回 None。"""
        n = re.sub(r"[^\u4e00-\u9fa5]", "", seg)
        best = None
        for i, nq in enumerate(order):
            if nq and n.find(nq) >= 0 and (best is None or i < best):
                best = i
        return best

    tail_add = add[:1] if tail else []
    dlg_add = add[1:] if tail else add
    # 【一次性稳定重排，不逐条插】逐条插时每插一条 body 就变了，
    # 后面几条基于乱掉的顺序再插，六句补写全被拍到篇首（2026-08-31 实测）。
    # 没有台词的画面段跟着前面最近的那一段走——画面本来就是给它后面
    # 那句台词做铺垫的。
    # 【只给补写的段落定位，原稿的顺序一个都不动】
    # 上一版把整篇一起重排，连正文里纯画面段的先后也被打乱，
    # 保真反而从挂 1 次变成挂 2 次（2026-08-31 实测）。
    # 做法：先给原稿每一段标上它在正文里的位置（没台词的跟前一段），
    # 再把补写段落按位置插进去；原稿之间的先后保持不变。
    keys, last = [], -1.0
    for seg in parts:
        r = _rank(seg)
        if r is None:
            last += 0.001
            keys.append(last)
        else:
            last = float(r)
            keys.append(last)
    rows = list(zip(keys, range(len(parts)), parts))
    for k, seg in enumerate(dlg_add):
        r = _rank(seg)
        rows.append((float(r) - 0.5 if r is not None else 1e9, 1e6 + k, seg))
    rows.sort(key=lambda x: (x[0], x[1]))
    body = [seg for _, _, seg in rows]
    return "\n\n".join(body + tail_add)

def _quote_chain(prose):
    """正文里所有【真台词】按顺序拼成一条串，用来查"逐字搬"。

    中文引号不只标台词，还标字形、拟声、绰号（「眉头皱成一个"川"字」
    「发出"滋滋"声」）。这些混进来会把前后两句真台词隔开，
    模型把同一个人连说的两句合成一行——本来是对的写法——就被判成编造
    （实测冤枉「三号舱门密封条破裂。里面的东西漏出来了。」2026-08-31）。

    只排除"引号紧跟着 字／声／样 这类量词"的短引号，不按长度排除：
    「没干。」「退后！」「该死！」去掉标点只剩两个字，都是真台词。
    也不能写成 `tail in "字声样"`——空字符串 in 任何字符串都是 True，
    整篇最后一句（后面没字了）会被全部误杀，全片钩子就这么没的。

    三个函数（trim_continued_dialogue / drop_fabricated_inner_voice /
    screenplay_fidelity）原来各存了一份自己的拼法，改一处漏两处，
    所以抽到这里共用。
    """
    def norm(s):
        return re.sub(r"[^\w一-龥]", "", str(s or ""))
    src = str(prose or "")
    out = []
    for m in re.finditer(r"[「“]([^」”\n]+)[」”]", src):
        tail = src[m.end():m.end() + 1]
        if tail and tail in "字声样般似的" and len(norm(m.group(1))) <= 3:
            continue
        out.append(norm(m.group(1)))
    return "".join(out)


def trim_continued_dialogue(body, prose):
    """剧本把正文的台词**续写**长了——砍回正文原话为止。

    实测：正文写「"下一场，苏清婉，对阵……"」故意留白，剧本续成
    「下一场，苏清婉，对阵……宗主千金，林婉儿。」（2026-08-30）。
    规则本来就写着"不许续写"，砍回去正是要的结果，比让模型重写整份剧本便宜得多。
    只砍**开头能对上正文原话**的那种；对不上的不动，照常报出来。
    """
    def norm(s):
        return re.sub(r"[^\w一-龥]", "", str(s or ""))
    P = norm(prose)
    # 中文引号不只标台词，还标字形、绰号、拟声（「眉头皱成一个"川"字」「发出"滋滋"声」）。
    Q = _quote_chain(prose)
    dlg = re.compile(r"^([^\s（(：:【#｜=─]{1,12})(（心声）)?[：:](.+)$")
    out = []
    for line in str(body or "").splitlines():
        m = dlg.match(line.strip())
        if m:
            text = m.group(3).strip()
            t = norm(text)
            if len(t) >= 2 and P.find(t) < 0 and Q.find(t) < 0:
                best = ""
                for k in range(len(text), 3, -1):
                    nk = norm(text[:k])
                    if len(nk) >= 4 and (P.find(nk) >= 0 or Q.find(nk) >= 0):
                        best = text[:k]
                        break
                # 砍剩不到一半就不是"续写"，是整句改写——按相似度吸附回原话
                if best and len(norm(best)) >= max(4, len(t) * 0.5):
                    line = "%s%s：%s" % (m.group(1), m.group(2) or "", best)
                else:
                    # 整句改写（"不要打草**惊**蛇"被写成"不要打草蛇"）——
                    # 规则就是"逐字搬正文原话"，吸附回最接近的那一句正是要的结果。
                    # 只在相似度足够高、且明显强过第二名时才吸附，免得张冠李戴。
                    import difflib
                    sc = sorted(((difflib.SequenceMatcher(None, t, norm(q)).ratio(), q)
                                 for q in re.findall(r"[「“]([^」”\n]+)[」”]",
                                                     str(prose or ""))),
                                key=lambda x: -x[0])
                    if (sc and sc[0][0] >= 0.72
                            and (len(sc) == 1 or sc[0][0] - sc[1][0] >= 0.1)):
                        line = "%s%s：%s" % (m.group(1), m.group(2) or "", sc[0][1])
                    elif not sc or sc[0][0] < 0.5:
                        # 和正文里任何一句都不像（实测最高相似度 0.09）＝**彻底编造**。
                        # 规则本来就写着"正文没给原话的说话动作，用无对白镜头表现"，
                        # 整行删掉正是这个结果；留着它，编造的台词会一路进到成片。
                        # 0.5~0.72 之间存疑的不动，照常报出来让人看（2026-08-30）。
                        continue
        out.append(line)
    return "\n".join(out)


def drop_fabricated_inner_voice(body, prose):
    """删掉正文里查无原话的（心声）独白行。

    只动心声，不动说出口的台词：说出口的台词删了会让镜头里的人张嘴没声，
    那种情况保留问题报出来，交人处理。
    """
    def norm(s):
        return re.sub(r"[^\w一-龥]", "", str(s or ""))
    P = norm(prose)
    Q = _quote_chain(prose)
    keep = []
    for line in str(body or "").splitlines():
        m = re.match(r"^([^\s（(：:【#｜=─]{1,12})（心声）[：:](.+)$", line.strip())
        if m:
            t = norm(m.group(2))
            if len(t) >= 2 and P.find(t) < 0 and Q.find(t) < 0:
                continue
        keep.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(keep))


def screenplay_padding(body):
    """剧本拖沓：同一个画面拍了两遍、或者拍一张不动的脸只为垫台词。

    用户 2026-08-30 定的两条：
      · 同一个画面只展示一次——同一个人同样姿态第二次出现必须有新东西
      · 不许有"只等台词"的空镜（「林默低声问，声音沙哑。」8 秒拍一张不动的脸）
    """
    lines = [l.strip() for l in str(body or "").splitlines() if l.strip()]
    shot_re = re.compile(r"^[（(]([^）)｜|]*)[｜|]\s*([\d.]+)\s*秒[）)](.+)$")
    # "只等台词"：整句就是"某人说/问/答＋语气"，没有任何身体动作
    idle = re.compile(r"^[一-龥]{2,8}(低声|沉声|轻声|冷冷地|缓缓)?"
                      r"(说|问|答|开口|回答|说道|问道)[^\n]{0,10}$")
    out, seen = [], {}
    for i, l in enumerate(lines):
        m = shot_re.match(l)
        if not m:
            continue
        secs, txt = float(m.group(2)), m.group(3).strip().rstrip("。")
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        has_dlg = bool(re.match(r"^[【]?[^\s（(：:【】]{1,12}[】]?"
                                r"(（[^）\n]{0,8}）)?[：:]", nxt))
        if has_dlg and idle.match(txt) and secs >= 5:
            out.append("「%s」是只等台词的空镜（%.0f 秒画面里什么都没发生）——"
                       "写出他说这句话时身上发生了什么：手上的动作、身体朝向、"
                       "表情的变化" % (txt[:20], secs))
        # 重复画面：主语＋地点／姿态的组合出现两次以上
        key = "".join(re.findall(r"[一-龥]{2,4}(?=在|站|坐|靠|盯|看|擦)", txt)[:2])
        if len(key) >= 4:
            seen[key] = seen.get(key, 0) + 1
            if seen[key] == 3:
                out.append("「%s…」这个画面拍了三次以上——同一个人同样的姿态，"
                           "第二次出现必须有新动作、新表情或者他说话，"
                           "没有新东西就把这些镜头并成一个" % txt[:18])
    return out[:3]


def merge_fragmented_shots(body, max_run=2, cap=14):
    """把连续的无台词镜头机械合并成一个。

    指令词改成"何时切一刀"之后镜头数从 98 降到 48，但模型输出有波动——
    下一次又回到 51 镜、3 处碎镜（2026-08-30 实测）。规则压不稳的部分用代码兜底。
    只合并**挨在一起且都没有台词**的镜头：秒数相加、画面描述用句号接起来，
    景别取这一串里最宽的那个（合成一个镜头后机位不动，用能装下全部内容的景别）。
    超过 cap 秒就断开另起一镜——下游一段视频上限 15 秒。
    """
    lines = [l.rstrip() for l in str(body or "").splitlines()]
    shot_re = re.compile(r"^[（(]([^）)｜|]*)[｜|]\s*([\d.]+)\s*秒[）)](.*)$")
    wide = ["特写", "大特写", "近景", "中近景", "中景", "全景", "大全景", "远景"]

    def has_dlg(i):
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        return bool(re.match(r"^[【]?[^\s（(：:【】]{1,12}[】]?(（[^）\n]{0,8}）)?[：:]",
                             nxt))

    out, i = [], 0
    while i < len(lines):
        m = shot_re.match(lines[i].strip())
        if not m or has_dlg(i):
            out.append(lines[i])
            i += 1
            continue
        run = [(m, i)]
        j = i + 1
        while j < len(lines):
            m2 = shot_re.match(lines[j].strip())
            if not m2 or has_dlg(j):
                break
            if sum(float(x[0].group(2)) for x in run) + float(m2.group(2)) > cap:
                break
            # 【按内容量收口】合出来的描述太长就停手：实测高潮那一镜被压成
            # 8 秒里塞了"走到边缘→掏芯片→撩衣领→对准→按下→咔哒"六个动作，
            # 一个镜头根本演不完（2026-08-30 自查）。100 字是一个镜头的上限。
            _len = sum(len(x[0].group(3).strip()) for x in run) + len(m2.group(3).strip())
            if _len > 100:
                break
            run.append((m2, j))
            j += 1
        if len(run) <= max_run:
            out.append(lines[i])
            i += 1
            continue
        # 【秒数重估，不是累加】这几个镜头本来讲的是同一件事，合成一镜后
        # 机位不动、一气呵成，不需要原来那么久。累加会合出一个 14 秒的长镜，
        # 读着就是一大段（2026-08-30 自查）。按"最长的那个 + 每多并一镜加 1 秒"，
        # 再按无台词镜头 2–6 秒的规矩收口。
        each = [float(x[0].group(2)) for x in run]
        secs = min(max(max(each), 3), 6) + (len(run) - 1)
        secs = max(3, min(round(secs), 8))
        cams = [x[0].group(1).strip() for x in run]
        cam = max(cams, key=lambda c: max([wide.index(w) for w in wide if w in c] or [0]))
        txt = "。".join(x[0].group(3).strip().rstrip("。") for x in run if x[0].group(3).strip())
        out.append("（%s｜%d秒）%s。" % (cam, secs, txt))
        i = run[-1][1] + 1
    return "\n".join(out)


def screenplay_wrong_speaker(body, prose=""):
    """台词被安给了错的人——正文里是**无名画外音**，剧本却安到在场角色头上。

    实测（2026-08-30 用户实测 STORY_066）：正文写「一个低沉的声音响起："目标已
    锁定…"」，说话人是通讯器那头的上级；剧本安给了店里的苏芮、还标成她的心声，
    成片里就成了她对着自己念任务指令。
    判据：这句台词在正文里的**前一句**含"一个…声音／有人／听筒／通讯器里"这类
    无名说法，剧本却给了具名说话人 → 报出来。
    """
    def norm(s):
        return re.sub(r"[^\w一-龥]", "", str(s or ""))
    P = str(prose or "")
    anon = re.compile(r"(一(个|阵|把)[^\n。]{0,10}(声音|嗓音)|有人|听筒里|通讯器里|"
                      r"话筒里|电话里|门外传来|身后传来|黑暗中传来|远处传来)")
    out = []
    for line in str(body or "").splitlines():
        m = re.match(r"^[【]?([^\s（(：:【】]{1,12})[】]?(（[^）\n]{0,8}）)?[：:](.+)$",
                     line.strip())
        if not m:
            continue
        who, mark, text = m.group(1), m.group(2) or "", m.group(3).strip()
        if who in ("画外音", "旁白") or len(norm(text)) < 4:
            continue
        i = P.find(text[:12].strip("“”「」"))
        if i < 0:
            continue
        before = P[max(0, i - 60):i]
        if who in before or who in _after_attr(P, i, text):
            continue
        if anon.search(before):
            out.append("「%s…」在正文里是**没有具名说话人**的画外音（%s），"
                       "剧本却安给了%s%s——这种台词行首写「画外音」，"
                       "不许安到在场任何人头上"
                       % (text[:16], anon.search(before).group(0)[:10], who, mark))
    return out[:3]


def _after_attr(P, i, text):
    """引号**后面**那一小截说话人标注（"…"林默低声问）。

    说话人可以写在引号后面，只看前面会把它当成无名画外音（实测「"如果她
    反抗呢？"林默低声问」被误报）。但窗口不能开大：开到 40 字就跑进了下一句，
    下一句里随便出现一个名字都会把真正的错误漏掉（2026-08-30 自查，
    「苏芮（心声）：目标已锁定…」正是这么漏过去的）。
    只取到句末，最多 16 字，而且**必须带说话动词**才算标注：
    「"…"林默低声问」是标注，「"…"苏芮把杯子倒扣在绒布上」是下一个动作，
    后者里的名字跟这句台词没关系，认成说话人就会把真错漏掉。
    """
    j = i + len(str(text or ""))
    m = re.search(r"[”」』]", P[i:j + 8])
    if m:
        j = i + m.end()
    seg = re.split(r"[。！？\n]", P[j:j + 16])[0]
    return seg if re.search(r"(说|问|答|道|开口|喊|叫|低语|嘟囔|补充)", seg) else ""


_STRUCT_WORD = re.compile(r"^(站位|在场|空间|布局|镜头|景别|运镜|时间|地点|人物|道具|音效|画外)$")


def trim_padded_shots(body, prose="", log=None):
    """剧本注水：比正文长太多说明编了正文没有的镜头（P153）。
    从后往前删「无台词 + 正文里找不到依据」的镜头行，删到正文的 1.35 倍为止。场景头和台词行一律不动。"""
    B, Pz = str(body or ""), str(prose or "")
    if len(Pz) < 200 or len(B) <= len(Pz) * 1.6:
        return B, 0
    flat = re.sub(r"[^一-龥]", "", Pz)
    target = int(len(Pz) * 1.35)
    lines = B.split("\n")
    prot = []                       # 不能删的行下标
    for i, l in enumerate(lines):
        t = l.strip()
        if not t or t.startswith("##") or t.startswith("【") or re.match(r"^(在场|空间|布局|站位)[：:]", t):
            prot.append(i)
        elif re.match(r"^[^\n：:（(]{1,8}[：:].+$", t):
            prot.append(i)
    def _grounded(t):
        body_txt = re.sub(r"^[（(][^）)]{0,20}[）)]", "", t)
        e = re.sub(r"[^一-龥]", "", body_txt)
        gram = [e[k:k + 3] for k in range(0, max(0, len(e) - 2), 2)]
        if not gram:
            return True
        hit = sum(1 for g in gram if g in flat)
        return hit * 2 >= len(gram)
    n = 0
    for i in range(len(lines) - 1, -1, -1):
        if len("\n".join(x for j, x in enumerate(lines) if x is not None)) <= target:
            break
        if i in prot or lines[i] is None or not lines[i].strip():
            continue
        # 台词行的上一镜不删（台词要有画面）
        nxt = next((lines[j] for j in range(i + 1, len(lines)) if lines[j] and lines[j].strip()), "")
        if re.match(r"^[^\n：:（(]{1,8}[：:].+$", str(nxt).strip()):
            continue
        if _grounded(lines[i]):
            continue
        lines[i] = None
        n += 1
    out = "\n".join(x for x in lines if x is not None)
    if n and log is not None:
        log.append("剧本注水：删了 %d 个正文里没有的镜头（%d→%d 字）" % (n, len(B), len(out)))
    return out, n


def fix_speaker_by_context(body, prose="", log=None, cast=None):
    """台词归属按正文段落归属改（P146）。只在候选唯一时改，返回 (剧本, 改了几处)。

    判据（全确定性）：
      ① 这句台词的内容（去引号，取前 8～20 字）在正文里能找到；
      ② 取它所在的那一段，段里找「名字+说话动词」或「名字：“…”」；
      ③ 段里只认出一个名字 → 剧本的说话人换成它；认出两个或零个 → 不动。
    """
    B, Pz = str(body or ""), str(prose or "")
    if not Pz.strip():
        return B, 0
    line_re = re.compile(r"^[【]?([^\s（(：:【】]{1,12})[】]?(（[^）\n]{0,8}）)?[：:](.+)$")
    names = {str(x).strip() for x in (cast or []) if str(x or "").strip()}      # 人物卡也算候选（P146b）
    for line in B.splitlines():
        m = line_re.match(line.strip())
        if m and 2 <= len(m.group(1)) <= 6 and re.match(r"^[一-龥]+$", m.group(1)):
            names.add(m.group(1))
    names -= {"画外音", "旁白", "在场", "空间", "布局", "站位", "镜头"}
    if len(names) < 2:
        return B, 0
    paras = [p for p in re.split(r"\n\s*\n", Pz) if p.strip()]
    verb = r"(说道|说|问道|问|答道|答|道|开口|喊|叫|低声|沉声|轻声|冷冷|补充|回应|吩咐|嘱咐)"
    n = 0
    out = []
    for line in B.splitlines():
        m = line_re.match(line.strip())
        if not m or m.group(1) not in names:
            out.append(line)
            continue
        who, mark, text = m.group(1), m.group(2) or "", m.group(3).strip()
        core = re.sub(r"[“”「」\s]", "", text)
        if len(core) < 4:
            out.append(line)
            continue
        key = core[:min(20, len(core))]
        _flat = re.sub(r"[“”「」\s]", "", Pz)
        while len(key) > 4 and key not in _flat:
            key = key[:-2]
        if len(key) < 4 or key not in _flat:
            out.append(line)
            continue
        hit = [p for p in paras if key in re.sub(r"[“”「」\s]", "", p)]
        if len(hit) > 1 and len(key) >= 8:
            hit = hit[:0]                                   # 长台词在多段命中说明定位不准，不动
        if len(hit) != 1:
            out.append(line)
            continue
        seg = hit[0]
        found = set()
        for nm in names:
            if re.search(re.escape(nm) + r"[^。！？\n]{0,10}" + verb, seg) or re.search(re.escape(nm) + r"\s*[：:]\s*[“「]", seg):
                found.add(nm)
        if len(found) == 1:
            nm = found.pop()
            if nm != who:
                n += 1
                if log is not None:
                    log.append("台词归属：%s → %s（%s…）" % (who, nm, core[:10]))
                out.append("%s%s：%s" % (nm, mark, text))
                continue
        out.append(line)
    return "\n".join(out), n


def fix_swapped_speaker(body, prose=""):
    """台词安给了另一个在场角色——按正文的说话动词把名字换回来。

    实测 STORY_070：正文「"里面有什么？"苏青问……"一半。"林克说」，
    剧本写成「林克：里面有什么？ / 苏青：一半。」——一问一答整个对调。
    用户的底线是"台词不要错"，这属于硬错，必须修。
    判据是确定性的：在正文里找到这句原话，看紧贴引号的那一小截说话人标注
    （"…"某某说 / 某某说："…"）。标注里恰好出现一个别的说话人名字 → 换过去。
    标注含糊、找不到原话、认出两个名字 → 一概不动。
    """
    B = str(body or "")
    P = str(prose or "")
    verb = r"(说|问|答|道|开口|喊|叫|低语|嘟囔|补充|回应)"
    line_re = re.compile(r"^[【]?([^\s（(：:【】]{1,12})[】]?"
                         r"(（[^）\n]{0,8}）)?[：:](.+)$")
    names = set()
    for line in B.splitlines():
        m = line_re.match(line.strip())
        if m and 2 <= len(m.group(1)) <= 5 and re.match(r"^[一-龥]+$", m.group(1)):
            names.add(m.group(1))
    names -= {"画外音", "旁白", "在场", "空间", "布局", "站位"}
    if len(names) < 2:
        return B
    out = []
    for line in B.splitlines():
        m = line_re.match(line.strip())
        if not m or m.group(1) not in names:
            out.append(line)
            continue
        who, mark, text = m.group(1), m.group(2) or "", m.group(3).strip()
        core = text.strip("“”「」")
        # 【短台词按带引号的原文定位】「一半。」只有 3 个字，光按内容找会撞上
        # 别处；连引号一起找就准了，而且短台词恰恰最容易被安错人
        # （实测 STORY_070：「"一半。"林克说」被写成「苏青：一半。」）。
        i = -1
        for lq, rq in (("“", "”"), ("「", "」")):
            hits = [mm.start() + 1 for mm in
                    re.finditer(re.escape(lq + core + rq), P)]
            if len(hits) == 1:
                i = hits[0]
                break
        if i < 0:
            key = core[:14]
            i = P.find(key) if len(key) >= 4 else -1
            text = text  # 长台词按内容找，短的找不到就不动
        if i < 0:
            out.append(line)
            continue
        # 引号前面同一句里的标注（某某说："…"）
        pre = P[max(0, i - 40):i]
        pre = re.split(r"[。！？\n]", pre)[-1]
        # 引号后面紧跟的标注（"…"某某说）——复用同一个窗口函数，口径一致
        post = _after_attr(P, i, core)
        cands = set()
        for seg in (pre, post):
            if re.search(verb, seg):
                cands |= {n for n in names if n in seg}
        # 「苏青」和「工装苏青」是同一个人的两种叫法（回忆里两个苏青同框），
        # 短的一定包含在长的里面，取最长的那个；跟原名互相包含就是同一个人，
        # 不许换——换了就把两个苏青并成一个，画面直接错乱（2026-08-30 自查）。
        if len(cands) > 1:
            cands = {max(cands, key=len)}
        if len(cands) == 1:
            got = cands.pop()
            if got != who and got not in who and who not in got:
                _dbg("说话人对调改回", {"台词": text[:24], "剧本": who, "正文": got})
                out.append("%s%s：%s" % (got, mark, text))
                continue
        out.append(line)
    return "\n".join(out)


def fix_wrong_speaker(body, prose=""):
    """把安错人的画外音**改回画外音**，不打回重写。

    判据跟 screenplay_wrong_speaker 完全同一套（同一个正则、同一套归一化），
    口径不一致会让"校验说有、修复看不见"死循环——这个坑踩过三次。
    改法是确定性的：行首的具名说话人换成「画外音」，台词一个字不动。
    """
    def norm(s):
        return re.sub(r"[^\w一-龥]", "", str(s or ""))
    P = str(prose or "")
    anon = re.compile(r"(一(个|阵|把)[^\n。]{0,10}(声音|嗓音)|有人|听筒里|通讯器里|"
                      r"话筒里|电话里|门外传来|身后传来|黑暗中传来|远处传来)")
    out = []
    for line in str(body or "").splitlines():
        m = re.match(r"^[【]?([^\s（(：:【】]{1,12})[】]?(（[^）\n]{0,8}）)?[：:](.+)$",
                     line.strip())
        if not m:
            out.append(line)
            continue
        who, text = m.group(1), m.group(3).strip()
        if who in ("画外音", "旁白") or len(norm(text)) < 4:
            out.append(line)
            continue
        i = P.find(text[:12].strip("“”「」"))
        if i < 0 or who in P[max(0, i - 60):i] or who in _after_attr(P, i, text):
            out.append(line)
            continue
        if anon.search(P[max(0, i - 60):i]):
            out.append("画外音：" + text)
        else:
            out.append(line)
    return "\n".join(out)


def ensure_scene_head_fields(body, names=None, cards=None, log=None):
    """场景头下面必须有【在场】【空间】【布局】【站位】——下游画场景参考图只认这四行。

    2026-09-07 十四话体检：【空间】【布局】【站位】一行都没有，【在场】只有一半。
    指令词第三节写了，模型不给。这里按**已经确定的信息**补，补不出来的不编：
      · 在场 = 这一场镜头描述和台词署名里真出现过的人物卡名字，按出场先后
      · 空间 = 同名场景卡的一句话空间；没有卡就用这一场第一个全景/大全景镜头的描述原句
      · 布局 = 同名场景卡的布局
      · 站位 = 不编（相对位置和绝对方位编出来就是错的，宁可空着让下游自己看镜头）
    """
    _names = [str(n).strip() for n in (names or []) if str(n or "").strip()]
    _cards = list(cards or [])
    lines = str(body or "").splitlines()
    heads = [i for i, l in enumerate(lines) if re.match(r"^#+\s*场景\s*\d+\s*[｜|]", l.strip())]
    if not heads:
        return body, 0
    added = 0
    for hi in reversed(heads):
        head = lines[hi].strip()
        loc = ""
        _p = [x.strip() for x in re.split(r"[｜|]", head.split("场景", 1)[-1]) if x.strip()]
        if len(_p) >= 2:
            loc = _p[1]
        end = next((j for j in heads if j > hi), len(lines))
        block = lines[hi + 1:end]
        have = {t: any(x.strip().startswith("【%s】" % t) for x in block)
                for t in ("在场", "空间", "布局", "站位")}
        card = None
        for c in _cards:
            nm = str(c.get("name") or "").strip()
            if nm and loc and (nm == loc or nm in loc or loc in nm):
                card = c
                break
        got = {}
        for x in block:
            t = x.strip()
            for tag in ("在场", "空间", "布局", "站位"):
                if t.startswith("【%s】" % tag) and tag not in got:
                    got[tag] = t
        ins = []
        if not have["在场"]:
            seen = []
            for x in block:
                t = x.strip()
                m = re.match(r"^([^\s（(：:【#]{1,12})\s*(（心声）)?[：:]", t)
                cands = ([m.group(1)] if m else []) + [n for n in _names if n in t]
                for n in cands:
                    hit = next((y for y in _names if y == n or n in y or y in n), "")
                    if hit and hit not in seen:
                        seen.append(hit)
            if seen:
                ins.append("【在场】" + "、".join(seen))
        if not have["空间"]:
            sp = str((card or {}).get("space") or "").strip()
            if not sp:
                for x in block:
                    t = x.strip()
                    if re.match(r"^[（(](?:大全景|全景|大远景|远景)", t):
                        sp = re.sub(r"^[（(][^）)]*[）)]", "", t).strip()
                        break
                # 镜头描述里全是人在做事，【空间】只要空间——按分句把人删掉（P159b①）
                if sp:
                    try:
                        from . import asset_core as _ac2
                        _seg = r"[^，。；\n]*(?:" + _ac2._PERSON_RE + r")[^，。；\n]*[，；。]?"
                        sp = re.sub(_seg, "", sp)
                        for _n in _names:
                            sp = re.sub(r"[^，。；\n]*" + re.escape(_n) + r"[^，。；\n]*[，；。]?", "", sp)
                    except Exception:
                        pass
                    sp = re.sub(r"^[，。；、]+", "", sp).strip()
            if sp and len(sp) >= 8:
                ins.append("【空间】" + sp[:160])
        if not have["布局"]:
            ly = str((card or {}).get("layout") or "").strip()
            if ly:
                ins.append("【布局】" + ly[:160])
        # 模型偶尔把这四行写两遍（实测场景2 的【空间】【布局】【站位】各两份）——
        # 有没有要补的都按 got 里的第一份重排一次，重复的自然被丢掉
        if ins or sum(1 for x in block if any(x.strip().startswith("【%s】" % t)
                                              for t in ("在场", "空间", "布局", "站位"))) > len(got):
            added += len(ins)
            if isinstance(log, list) and ins:
                log.append("场景「%s」补：%s" % (loc or head[:12], "、".join(x[:4] for x in ins)))
            for x in ins:
                got[x[1:3]] = x
            # 已有的那几行先摘掉，再按固定顺序整组写回场景头下面（P159b②）
            keep = [l for l in lines[hi + 1:end]
                    if not any(l.strip().startswith("【%s】" % t) for t in ("在场", "空间", "布局", "站位"))]
            ordered = [got[t] for t in ("在场", "空间", "布局", "站位") if got.get(t)]
            lines[hi + 1:end] = ordered + keep
    return "\n".join(lines), added


def fix_scene_header(body):
    """把场景头四行里下游用不了的东西删掉。这四行是画场景参考图的唯一依据。

    两类，都是指令词里明令禁止、但模型照样写的（禁令对这个模型无效，
    只能代码删 —— 2026-08-31 实测）：

    ① 【布局】里的**画面方位**。画面左右取决于摄影机站哪边，正打反打里是相反的，
       方位表被一个机位污染，反打就自相矛盾。实测写出「【入口大门】南侧（画面深处）」
       「马匹停在大门外左侧阴影中」。绝对方位（东南西北／靠山一侧）保留。
    ② 【空间】【布局】里混进的英文。这两行会原样进出图提示词，
       实测写出「火把在石壁 niches 中燃烧」。
    """
    out = []
    for line in str(body or "").splitlines():
        s = line.strip()
        if s.startswith("【布局】"):
            # 括号里整段讲画面方位的，连括号一起删
            line = re.sub(r"[（(][^）)]{0,14}画面[^）)]{0,14}[）)]", "", line)
            # 「，向画面深处延伸」这种从逗号到句读的整段删
            line = re.sub(r"[，,][^，,｜|\n]{0,12}画面[^，,｜|\n]{0,12}", "", line)
            # 剩下光秃秃的画面方位词直接抠掉
            line = re.sub(r"画面(左侧|右侧|深处|前景|后方|中央|左|右)", "", line)
            # 「左侧／右侧」在布局行里一律不合法（要写东南西北），删词留内容
            line = re.sub(r"(?<![东南西北])[左右]侧", "", line)
        if s.startswith("【空间】") or s.startswith("【布局】"):
            line = re.sub(r"\s*[A-Za-z]{3,}\s*", "", line)
        # 删完可能留下「石壁 中」「南侧（）」这类空壳，收拾干净
        line = re.sub(r"[（(]\s*[）)]", "", line)
        line = re.sub(r"[，,]\s*(?=[，,｜|。])", "", line)
        line = re.sub(r"[，,]\s*$", "", line)
        out.append(line.rstrip())
    return "\n".join(out)


def fix_idle_shots(body):
    """"只等台词"的空镜——把秒数收到 3 秒，不打回重写。

    这类镜头（「林默低声问，声音沙哑。」8 秒）的毛病是**时长**：画面里什么都
    没发生却占了 8 秒，成片就是一张不动的脸。让模型补动作属于二次创作、会改到
    台词；把 8 秒收成 3 秒是确定性的，画面内容一个字不动，问题就没了。
    """
    lines = [l.rstrip() for l in str(body or "").splitlines()]
    shot_re = re.compile(r"^([（(])([^）)｜|]*)([｜|]\s*)([\d.]+)(\s*秒[）)])(.+)$")
    idle = re.compile(r"^[一-龥]{2,8}(低声|沉声|轻声|冷冷地|缓缓)?"
                      r"(说|问|答|开口|回答|说道|问道)[^\n]{0,10}$")
    out = []
    for i, l in enumerate(lines):
        m = shot_re.match(l.strip())
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        has_dlg = bool(re.match(r"^[【]?[^\s（(：:【】]{1,12}[】]?"
                                r"(（[^）\n]{0,8}）)?[：:]", nxt))
        if (m and has_dlg and float(m.group(4)) >= 5
                and idle.match(m.group(6).strip().rstrip("。"))):
            out.append("%s%s%s3%s%s" % (m.group(1), m.group(2), m.group(3),
                                        m.group(5), m.group(6)))
        else:
            out.append(l)
    return "\n".join(out)


def screenplay_fragmentation(body, prose=""):
    """剧本把连续动作拆成了太多碎镜头——数出来，打回重写。

    实测（2026-08-30 用户实测 STORY_066）：3856 字正文被摊成 98 个镜头 / 7.6 分钟，
    其中「他竖起领子」「他的左臂是义体」「他站在门口看窗内」被切成三个独立镜头，
    11 秒讲一件事。指令词里只有"什么时候要拆"，没有"什么时候该合"，
    光靠加规则不够，这里用数字卡住。

    两个判据，都基于**无台词镜头**（有台词的镜头秒数由台词长度定，不算碎）：
      ① 连续 3 个以上无台词镜头挨在一起 → 这一串是被拆碎的描写
      ② 无台词镜头占比超过六成 → 整篇都在拍描写，不是在拍戏
    """
    # 【口径对齐：只报合并器修不掉的】合并器是从一串的头上贪心地并，
    # 还有内容 100 字／14 秒的上限（不然会合出一个演不完的长镜）；校验器
    # 原来自己数"连着 3 个无台词镜头"，两套算法永远对不齐，于是每一版剧本
    # 都挂着一条改不掉的遗留——模型照着它重写，重写完照样报（2026-08-30 自查）。
    # 现在定义成：合并器跑完之后没变化，就说明没有能并的碎镜，一条都不报。
    # 口径分家的死循环，这是第四次踩。
    _mergeable = merge_fragmented_shots(body) != str(body or "")
    lines = [l.strip() for l in str(body or "").splitlines() if l.strip()]
    shots, cur, runs = [], [], []

    def _close(run):
        return len(run) if len(run) >= 3 else 0

    for i, l in enumerate(lines):
        m = re.match(r"^[（(]([^）)｜|]*)[｜|]\s*([\d.]+)\s*秒[）)](.*)$", l)
        if not m:
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        has = bool(re.search(r"[「“][^」”]{2,}[」”]", l + nxt)) or bool(
            re.match(r"^[【]?[^\s（(：:【]{1,12}[】]?(（[^）]{0,8}）)?[：:]", nxt))
        shots.append(has)
        if has:
            n = _close(cur)
            if n:
                runs.append(n)
            cur = []
        else:
            cur.append((len(m.group(3).strip()), float(m.group(2))))
    n = _close(cur)
    if n:
        runs.append(n)
    probs = []
    if not shots:
        return probs
    silent = len([x for x in shots if not x])
    # 反馈用的话要和指令词里那条判据完全一致，否则模型收到的规则和反馈打架
    if runs and _mergeable:
        probs.append("有 %d 处切得没有理由（最长一串 %d 个无台词镜头挨着）——"
                     "一个镜头是摄影机一次不间断的拍摄。每一刀都要说得出理由："
                     "要看清原来看不清的东西／要交代原来看不到的空间或人／说话人换了／"
                     "时间地点跳了／动作有了结果。说不出理由的那一刀就并回上一个镜头"
                     % (len(runs), max(runs)))
    if silent > len(shots) * 0.6:
        probs.append("无台词镜头占了 %d/%d——整篇在拍描写不是在拍戏。"
                     "外貌、衣着、天气这类小说交代信息的句子并进人物正在做的动作里，"
                     "不单独占一个镜头" % (silent, len(shots)))
    return probs


def screenplay_fidelity(body, prose):
    """剧本对正文的保真验收（2026-08-28 用户定：完全按正文来，台词不能错）。

    指令词里"逐字搬/不许提前"的规则 Qwen 照犯（实测：正文 70% 处的信件内容被提前到
    第一镜心声、「不要工钱？」被续写半句）——正文→剧本是全链唯一没有保真校验的一层，
    这里补上。返回问题清单，空=过。
    """
    def norm(s):
        return re.sub(r"[^\w一-龥]", "", str(s or ""))
    P = norm(prose)
    # 第二条对照串：正文全部引号原话按顺序相连。
    # 「"这块泥，"他低声说，"是你来时路上的记忆"」被插入语拆成两段引号，
    # 剧本合回一句是正确的逐字——只对 P 查子串会误判成编造（实测冤枉 3 句）。
    Q = _quote_chain(prose)
    plen = max(1, len(P))
    probs, positions = [], []
    dlg = re.compile(r"^([^\s（(：:【#｜=─]{1,12})(（心声）)?[：:](.+)$")
    for line in str(body or "").splitlines():
        m = dlg.match(line.strip())
        if not m:
            continue
        who, heart, text = m.group(1), m.group(2) or "", m.group(3).strip()
        t = norm(text)
        if len(t) < 2:
            continue
        if P.find(t) < 0 and Q.find(t) < 0:
            probs.append("%s%s的台词「%s…」在正文里查无原话——台词只许逐字搬正文引号"
                         "原话，不许改写、合并、续写、编造；正文没给原话的说话动作，"
                         "用无对白镜头表现" % (who, heart, text[:18]))
        else:
            positions.append(t)
    # 顺序：贪心向前匹配——短句（"是，大人"）正文里会出现多次，必须从当前进度往后找，
    # 只认第一次出现会把合法复用误判成倒挂（实测误报 4 处）。P、Q 各一条游标。
    cur_p, cur_q = 0, 0
    for t in positions:
        i = P.find(t, cur_p)
        if i >= 0:
            cur_p = max(cur_p, i)
            continue
        j = Q.find(t, cur_q)
        if j >= 0:
            cur_q = max(cur_q, j)
            continue
        k = P.find(t)
        if k >= 0 and cur_p - k > plen * 0.2:
            probs.append("台词「%s…」的顺序倒挂了——它在正文里的位置比剧本前面已经用过"
                         "的内容早了很多，剧本必须严格按正文顺序展开，"
                         "不许把后文信息提前、也不许把前文对话挪到后面" % t[:14])
    return probs


def design_characters(prose, settings):
    """第一话原文 → 人物设定（角色设计师）。返回 list。
    喂给 Qwen 界面下拉的固定选项，让它在自由设计的同时给每人挑好标签，
    人设卡的长相/身材/性格/服装方向/服装轮廓下拉就显示 Qwen 选的值、不再是"自动"。"""
    sys = _fill(_ins("人物设定_指令词.txt"), settings, scale_layers=("story", "figure"))
    try:
        from . import one_shot_setup
        rules = one_shot_setup._enum_rules(settings)
        if rules:
            sys = sys + "\n" + rules
    except Exception:
        pass
    # 正文太长只喂前 6000 字：人设要认的是「有哪些人、什么身份长相」，不需要全部情节。
    # 输出上限 4200→7000：七个人物每人一大段时 4200 会被截断，
    # 截断后 JSON 收不了尾，直接报「人设 JSON 解析失败」（2026-08-29 实测）。
    _p = str(prose or "").strip()
    if len(_p) > 6000:
        _p = _p[:6000] + "\n（后略）"
    umsg = "第一话故事正文：\n\n" + _p + "\n\n为里面的主要人物各设计一份人物设定。"
    _state_src = _p + " " + str((settings or {}).get("episode_notes") or "") + " " + str((settings or {}).get("one_line") or "")
    if re.search(r"衣[物服裳].{0,8}(撕|破|烂|湿|少|剩|刮走|扯)|不蔽体|衣衫不整|衣不蔽体|只剩一半|破布", _state_src):
        # P415：故事里衣服已经被撕破/刮走 → 服装写此刻的状态，不设计完好的日常装（项目221：正文写破碎不堪，卡上仍是风衣西裤）
        umsg += ("\n\n【服装按故事此刻的状态写】正文里这些人的衣服已经被撕破、湿透、只剩一半：「具体服装」就写破碎、湿贴、残缺后的样子"
                 "（哪件撕到哪里、露出什么、用什么布条裹着），不要设计完好的日常装或成套的时装；「服装款式」标签挑最接近残破前原装的那个。")
    # 同一份正文，Qwen 有时返回合法数组、有时返回带解释的散文或被截断的半截 JSON
    # （2026-08-29 实测：同输入重跑就好了）。一次失败就断掉整条链不合理，重试三次，
    # 温度逐次降低（越低越容易吐规规矩矩的 JSON）。
    last = ""
    for temp in (0.85, 0.6, 0.35):
        raw = _q(sys, umsg, mt=7000, temperature=temp)
        got = _parse_char_json(raw)
        if got:
            return _trim_cast(got, _p)
        last = raw
    raise RuntimeError("人设 JSON 解析失败（连试三次，最后一次返回 %d 字，结尾：%s）"
                       % (len(last), last[-60:].replace("\n", " ")))


def _trim_cast(cards, prose, cap=8):
    """砍掉跑飞的人设卡。

    实测（2026-08-30）角色设计师一口气生成 **37 张**，从「史莱姆（路人）」
    「哥布林（路人）」一路列到「巨龙（路人）」——它在枚举奇幻种族。
    这些卡会让出图层去画 37 张人设图，纯粹是浪费。
    三条筛：① 名字带"路人/群众/背景/龙套"的不要；② 名字在正文里找不到的不要
    （真出场的人物正文一定提过）；③ 最多留 8 个，前面的通常是主要人物。
    筛完一个不剩时退回原样——宁可多几张，也不能把主角筛没了。
    """
    # 只在**明显跑飞**时才裁。正常规模（≤10 张）一张不动——人设卡的名字和正文
    # 里的称呼常常不一样（卡叫「戴伞男人」、正文写「那个撑伞的男人」），
    # 按名字筛会误删合法角色，出图层就丢了这个人的外形定义（2026-08-30 实测
    # 正常项目被从 5 张裁到 3 张）。
    if len(cards) <= 10:
        return cards
    p = str(prose or "")

    def keep(c):
        n = str((c or {}).get("name") or "").strip()
        if not n or re.search(r"(路人|群众|背景|龙套|甲乙丙|NPC)", n):
            return False
        core = re.sub(r"[（(].*", "", n).strip()
        return bool(core) and (core in p or n in p)

    # 第一张卡按惯例是主角，无条件留下：第一人称题材里它常叫「主角（无名冒险者）」，
    # 而"主角"二字正文里根本不会出现（正文写的是"我"），按名字筛会把主角筛没
    # ——那比多留几张龙套严重得多（2026-08-30 实测）。
    out = ([cards[0]] if cards else []) + [c for c in cards[1:] if keep(c)]
    return (out or cards)[:cap]


def _parse_char_json(raw):
    """从模型返回里抠出人物数组。抠不出返回 None（交给上层重试）。
    依次试：原样 → 去尾逗号 → 截断补尾（模型写到一半停了，留下完整的几个人）。"""
    m = re.search(r"\[.*\]", str(raw or ""), re.S)
    frag = m.group(0) if m else str(raw or "")
    cands = [frag, re.sub(r",\s*([\]}])", r"\1", frag)]
    if not m:
        # 没有闭合的 ]：从第一个 [ 起，退到最后一个完整对象的 } 处自己收尾
        i = frag.find("[")
        if i >= 0:
            j = frag.rfind("}")
            if j > i:
                cands.append(frag[i:j + 1] + "]")
    for x in cands:
        try:
            got = json.loads(x)
            if isinstance(got, list) and got:
                return got
        except Exception:
            pass
    return None


# ---------- 存档 helper ----------

def _ep(sid, no=1, create=True):
    """取第 no 话（没有就建一个空的）。每话自带 brief/prose/body/timeline 四个槽。"""
    from . import saga_core
    no = int(no or 1)
    saga = saga_core.get_saga(sid)
    e = saga_core.episode(saga, no)
    if e is None and create:
        saga.setdefault("episodes", []).append({"no": no, "season": 1, "status": "EMPTY"})
        saga["episodes"].sort(key=lambda x: int(x.get("no") or 0))
        saga_core.save_saga(sid, saga)
        saga = saga_core.get_saga(sid)
        e = saga_core.episode(saga, no)
    return saga, e


def _update_ep(sid, no, **fields):
    from . import saga_core
    saga, e = _ep(sid, no)
    if e is not None:
        e.update(fields)
        saga_core.save_saga(sid, saga)


def content_sig(text):
    """内容指纹（长度 + md5 前 8 位）。用来判断下游成果是不是照着现在这份上游做的。"""
    import hashlib
    t = str(text or "")
    return "%d:%s" % (len(t), hashlib.md5(t.encode("utf-8", "ignore")).hexdigest()[:8])


def _brief_matches_row(e, settings):
    """这一话的简介和现在结构表的同一行对不对得上（P166②）。

    用内容核对代替"按当前版本猜"：简介本来就是照结构行取的（D6），
    对得上说明这份稿子写的就是现在这一行的事；对不上就是别的版本或别处来的。
    """
    try:
        rows = [r for r in (settings.get("plan_rows") or []) if isinstance(r, dict)]
        no = int(e.get("no") or 0)
        if not (1 <= no <= len(rows)):
            return False
        row = str(rows[no - 1].get("one_line") or "").strip()
        brief = str(e.get("brief") or "").strip()
        if not row or not brief:
            return False
        if row == brief or row in brief or brief in row:
            return True
        a, b = set(re.findall(r"[一-龥]{2}", row)), set(re.findall(r"[一-龥]{2}", brief))
        return bool(a) and len(a & b) / float(len(a)) >= 0.6
    except Exception:
        return False


def episode_source(sid, ep, settings=None, e=None):
    """这一话的稿子是哪一版结构写的（P166②）。返回 ("current"|"old"|"unknown", 说明)。

    · current  版本号对上，或版本号缺失但简介和现在的结构行对得上（内容证据）
    · old      版本号明确对不上——结构改过，这份稿子是旧的
    · unknown  没有版本号，简介也对不上现在的结构行——来源不明，不许当成这一版用
    """
    from . import story_core, saga_core
    if settings is None:
        settings = (story_core.get_story(sid) or {}).get("settings") or {}
    if e is None:
        e = saga_core.episode(saga_core.get_saga(sid), int(ep)) or {}
    pv = int(settings.get("plan_version") or 0)
    ev = e.get("plan_version")
    if ev is not None and int(ev or 0) == pv:
        return "current", "版本号对上（v%s）" % pv
    if ev is not None:
        return "old", "这份稿子是 v%s 写的，现在结构已经是 v%s" % (ev, pv)
    if _brief_matches_row(e, settings):
        return "current", "没有版本号，但简介和现在的结构行对得上"
    return "unknown", "没有版本号，简介也对不上现在的结构行——来源不明"


def stamp_legacy_plan_version(sid):
    """给老数据盖来源（P166②，替掉 P164 那版"版本 ≤1 就认定"的猜法）。

    只有**内容对得上**才盖版本号；对不上的明确记成「来源未知」，
    让它在续写和页面上都是可见的未知，而不是被默认成当前版本。
    """
    from . import story_core, saga_core
    st = story_core.get_story(sid) or {}
    settings = st.get("settings") or {}
    pv = int(settings.get("plan_version") or 0)
    saga = saga_core.get_saga(sid)
    n = 0
    for e in (saga.get("episodes") or []):
        if not str(e.get("prose") or "").strip() or e.get("plan_version") is not None:
            continue
        if _brief_matches_row(e, settings):
            e["plan_version"] = pv
            e["plan_version_src"] = "按简介和结构行对上"
            n += 1
        else:
            e["plan_version_src"] = "未知"
    if n or any(x.get("plan_version_src") == "未知" for x in saga.get("episodes") or []):
        saga_core.save_saga(sid, saga)
    if n:
        _dbg("老数据按内容盖来源", {"话数": n, "版本": pv})
    return n


def screenplay_stale(sid, ep=1):
    """这一话的剧本是不是照着**现在这份**正文写的（P164②）。
    返回 (是不是过期, 原因)。旧成果不删，只是标出来。"""
    try:
        _saga, e = _ep(sid, ep)
    except Exception:
        return False, ""
    e = e or {}
    body = str(e.get("body") or "").strip()
    prose = str(e.get("prose") or "").strip()
    if not body or not prose:
        return False, ""
    if e.get("body_stale"):
        return True, "正文重写过了，这份剧本还是照旧正文写的"
    sig = str(e.get("prose_sig") or "")
    if not sig:
        return False, ""              # 改这版之前生成的剧本，没记来源，不硬报
    if sig != content_sig(prose):
        return True, "正文改过了，这份剧本还是照旧正文写的"
    return False, ""


# 兼容旧调用（第一话）
def _ep1(sid, create=True):
    return _ep(sid, 1, create)


def _update_ep1(sid, **fields):
    return _update_ep(sid, 1, **fields)


def add_episode(sid):
    """追加一话（空的），返回新话号。用户点「＋增加一话」时调。"""
    from . import saga_core
    saga = saga_core.get_saga(sid)
    eps = saga.get("episodes") or []
    nxt = max([int(x.get("no") or 0) for x in eps] or [0]) + 1
    _ep(sid, nxt)          # 建空话
    return nxt


def _prev_context(sid, ep):
    """续写第 ep 话要看的前情：前面每一话的简介 + 上一话原文的结尾。
    （用户 2026-08-26 定：各话简介 + 上话结尾约300字，接得准又不吃 token。）"""
    from . import saga_core
    saga = saga_core.get_saga(sid)
    eps = sorted([x for x in (saga.get("episodes") or [])
                  if int(x.get("no") or 0) < int(ep)],
                 key=lambda x: int(x.get("no") or 0))
    if not eps:
        return ""
    from . import story_core as _sc0
    _settings = (_sc0.get_story(sid) or {}).get("settings") or {}
    stamp_legacy_plan_version(sid)              # 先按内容把来源判一遍（不猜）
    from . import saga_core as _sg0
    eps = sorted([x for x in (_sg0.get_saga(sid).get("episodes") or [])
                  if int(x.get("no") or 0) < int(ep)], key=lambda x: int(x.get("no") or 0))
    lines, _skipped = [], []
    for x in eps:
        _src, _why = episode_source(sid, int(x.get("no") or 0), _settings, x)
        b = str(x.get("brief") or x.get("logline") or "").strip()
        if _src != "current":
            # P166②：旧版本/来源不明的简介一律不进提示词——它带的是旧人名旧剧情
            if b:
                _skipped.append("第%s话简介（%s）" % (x.get("no"), _why))
            continue
        if b:
            lines.append("第%s话简介：%s" % (x.get("no"), b))
    tail = str(eps[-1].get("prose") or "").strip() if eps else ""
    # 上一话不是这一版结构写的 → 结尾不接（P111：会把旧人名、旧关系带进来）
    if eps:
        _src, _why = episode_source(sid, int(eps[-1].get("no") or 0), _settings, eps[-1])
        if _src != "current":
            _skipped.append("第%s话结尾（%s）" % (eps[-1].get("no"), _why))
            tail = ""
    if _skipped:
        _dbg("前情里排除了对不上版本的内容", {"这一话": ep, "排除": _skipped,
                                              "后果": "这些不喂给模型，衔接靠现有结构表"})
    _prev_context.skipped = list(_skipped)
    out = "\n\n".join(lines)
    if tail:
        out += "\n\n【上一话（第%s话）原文的结尾】\n…%s" % (eps[-1].get("no"), tail[-300:])
    return out


def _scale_gate(settings, card):
    """共用边界：通过则原样使用设定，拒绝则报错，不降级尺度或改人物。"""
    assert_allowed(cards=(card,), settings=settings, media="image_prompt")
    return settings or {}

_NEG_KEEP = ("无限", "无袖", "无框", "无领", "无痕", "无瑕", "无暇", "无名",
             "无华", "无数", "无缝", "无光", "无色", "无花")
_NEG_HEAD = re.compile(r"^(无|没有|不带|不含|不要|不许|不能|避免|禁止|排除|不出现|不允许|不是油亮|不是磨皮|非商业)")


# 否定在句子中间：「牌匾和招牌上不写文字」「墙上没有挂画」「桌面不出现杯子」（P213）
_NEG_MID = re.compile(r"(?:不写|不出现|不许出现|不带|不放|没有|不留|不含|不加)[^，。；]{0,8}$")


def _strip_neg(p):
    """把否定句从出图提示词里剥掉。

    Krea2 读不懂否定——"无血迹污渍"等于往画面里放了"血迹污渍"三个字。
    指令词末尾那份「禁止生成」清单是写给 Qwen 看的，但它经常照抄进提示词正文
    （2026-08-27 实测：老贾的提示词结尾抄了"无血迹污渍，无破损湿透衣物，无激烈表情"）。
    以前这里是六个写死的词组，只挡得住"无塑料感"那几个，清单一加新条目就漏。
    改成按句判断：短句 + 以否定词开头 = 整句剥掉；"无限接近真人"这类正常词走白名单。
    """
    keep = []
    for seg in re.split(r"([，。；、\n])", p or ""):
        t = seg.strip()
        if t and len(t) <= 16 and _NEG_HEAD.match(t) and not t.startswith(_NEG_KEEP):
            continue
        # 否定在句子中间的也要剥：「牌匾和招牌上不写文字」——名词在前、否定在中，
        # 上面那条以否定词开头的判断漏掉了它，结果画面里真的多了一块字牌（P213 实测）
        if t and len(t) <= 20 and _NEG_MID.search(t):
            continue
        keep.append(seg)
    out = "".join(keep)
    out = re.sub(r"[，、；]\s*(?=[，。；、])", "", out)   # 剥完留下的连续标点
    out = re.sub(r"(?<=[。！？])\s*[，、；]+", "", out)   # 句号后面剩的逗号
    out = re.sub(r"^\s*[，、；。]+", "", out.strip())
    out = re.sub(r"[，、；]\s*$", "", out.strip())
    out = re.sub(r"。{2,}", "。", out)                       # P354：剥掉整句后留下的「。。」
    return re.sub(r"\s+", " ", out).strip()


# 服装里的「此刻状态」：人设图是全片的形象权威，写进去就跟着这个人走遍每一场戏
# ——实测雷恩卡上写了「因战斗沾湿而半透明，紧贴胸膛」，人设图湿身，五段成片全湿
# （2026-08-29 用户发现）。指令词早写了"临时状态不进人设卡"，Qwen 照样写，
# 老规矩：原则性规则无效，改成代码剥。整个分句摘除，不留半截。
_TRANSIENT_FRAGMENT = (r"[^，。；、：:]*(?:沾湿|湿透|濡湿|汗湿|被雨|淋湿|水渍|"
                       # P327①：194 人设图湿身——「发梢湿润」「黑发微湿」「湿发状态」「战斗后暂时休憩」全是此刻状态
                       # P328f②：水滴（水滴形耳坠）/松弛感（穿搭风格）/绷带（固定造型）/疲惫（常年疲惫是长相）不在表里；小句边界含 、 和 ：
                       r"湿|水珠|水滴状|滴水|滴落|汗珠|汗水|冒汗|沾泥|泥点|泥污|尘土|战斗后|战后|休憩|狼狈|喘息|喘着|伤口|淤青|瘀青|擦伤|划伤|"
                       r"染血|血迹|沾血|血污|破损|撕裂|撕破|破洞|磨破|"
                       r"沾满泥|泥泞|脏污|烧焦|焦黑|"
                       # 身体的瞬时反应：害羞、发笑、紧张那一刻的样子，不是长相和体型。
                       # 实测「耳尖微热」进了身材栏、「肩膀微微发颤」也进了身材栏（P207）
                       r"发颤|颤抖|发抖|哆嗦|微热|发烫|泛红|涨红|通红|僵住|一顿|起伏"
                       r")[^，。；、：:]*[，。；、]?")




_NUDE_RE = re.compile(r"全裸(?!露)|裸体|赤裸(?!露)(?![的着]?[肩臂腿脚背胸膀头上足踝膝腰腹])|一丝不挂|不穿(?:任何)?衣|裸着(?![的着]?[肩臂腿脚背胸膀头上足踝膝腰腹])|全身赤裸|不着寸缕")  


def is_nude(card, settings=None):
    assert_allowed(cards=(card,), settings=settings, media="image_prompt")
    if is_nonhuman(card):
        return False
    txt = " ".join(str((card or {}).get(k) or "") for k in ("clothing", "appearance_details", "look_full"))
    if _NUDE_RE.search(txt):
        return True
    if settings and any("成人向" in str(t) for t in (settings.get("content_tendencies") or [])):
        return bool(_scale_gate(settings, card).get("content_tendencies"))
    return False




def _field_text(v):
    """卡上的字段规整成一段话（P222）。

    设计师有时把服装返回成 {'outfit_preset': …, 'description': …}。
    直接 str() 会把大括号、引号和预设名原样写进出图提示词
    （实测：「这一身衣服：{'outfit_preset': '圣堂魅影修女', 'description': …}」）。
    """
    if isinstance(v, dict):
        for k in ("description", "desc", "text", "描述", "内容", "正文"):
            if str(v.get(k) or "").strip():
                return str(v[k]).strip()
        return "；".join(str(x).strip() for x in v.values() if str(x or "").strip())
    if isinstance(v, (list, tuple)):
        return "；".join(_field_text(x) for x in v if x)
    return str(v or "")


def strip_state_look(text):
    """人设图提示词/设计师那段里的剧情临时状态（湿/汗/血/泥/战斗后…）按小句剥掉（P327①）。
    硬事实段是代码在 Qwen 之后原样追加的，指令词管不到它——所以这一道放在 write_char_sheet 最后。"""
    t = str(text or "")
    if not t.strip():
        return t
    out = []
    for ln in t.split("\n"):
        ln2 = re.sub(_TRANSIENT_FRAGMENT, "", ln)
        ln2 = re.sub(r"[；;，,]{2,}", "；", ln2).strip("；;，, ")
        if ln.strip() and not ln2.strip():
            continue                                  # 整行都是状态（少见）→ 整行删
        if ln2 and not re.search(r"[。！？!?]$", ln2) and re.search(r"[。！？!?]$", ln.strip()):
            ln2 = ln2 + "。"
        out.append(ln2)
    return "\n".join(out)


def sanitize_card_fields(c):
    """仅规整模型返回的字段格式；已写好的文字和真实年龄原样保留。"""
    result = dict(c or {})
    for key in ("appearance", "appearance_details", "hair", "clothing", "personality", "build", "voice"):
        if key in result and isinstance(result[key], (dict, list, tuple)):
            result[key] = _field_text(result[key])
    return result


def guess_age(card, source=""):
    """保留已确认年龄；缺失时只读取与本名紧邻的明确岁数，不按职业猜。"""
    card = card or {}
    age = str(card.get("age") or "").strip()
    if age:
        return age
    name = str(card.get("name") or "").strip()
    if name:
        match = re.search(re.escape(name) + r"[（(，,：:\s]*(?:年龄|今年|现年)?[：:\s]*(\d{1,4})\s*岁", str(source or ""))
        if match:
            return match.group(1)
    return ""

def redesign_character(sid, character_id, on_step=None, brief=None, with_image=False):
    """P357：把一个人从故事里**完全重新设计**（名字/性别/年龄不变），覆盖卡，再重出他的人设图。返回 {"ok", "card", "images"}。"""
    from . import asset_core, story_core, saga_core as _sg
    card = asset_core.get_asset(sid, "characters", character_id)
    if not card:
        raise RuntimeError("找不到这张人物卡")
    st = story_core.get_story(sid) or {}
    settings = dict(st.get("settings") or {})
    name = str(card.get("name") or "").strip()
    # 故事文本：这个人首次出场那一话的正文；没有正文就用整部规划的梗概 + 人物表
    try:
        _ep_no = int(card.get("first_episode") or 1)
    except Exception:
        _ep_no = 1
    prose = ""
    for _n in (_ep_no, 1):
        try:
            _saga, _e = _ep(sid, _n, create=False)
            prose = str((_e or {}).get("prose") or "").strip()
        except Exception:
            prose = ""
        if prose:
            break
    if not prose:
        _plan = settings.get("story_plan") or {}
        prose = str(_plan.get("synopsis") or settings.get("one_line") or "").strip()
        _pp = next((p for p in (_plan.get("people") or []) if str(p.get("name") or "") == name), None)
        if _pp:
            prose += "\n\n【%s】%s，%s，%s；%s" % (name, _pp.get("role") or "", _pp.get("look") or "", _pp.get("personality") or "", _pp.get("relation") or "")
    _brief = str(brief or "").strip()
    if not prose and not _brief:
        raise RuntimeError("没有故事文本，没法重新设计")
    if on_step:
        on_step("角色设计师重新设计：" + name)
    sysm = _fill(_ins("人物设定_指令词.txt"), settings, scale_layers=("story", "figure"))
    try:
        from . import one_shot_setup
        rules = one_shot_setup._enum_rules(settings)
        if rules:
            sysm = sysm + "\n" + rules
    except Exception:
        pass
    _p = prose[:6000] + ("\n（后略）" if len(prose) > 6000 else "")
    _old_cl = str(card.get("clothing") or "").strip()
    if _brief:
        # P400：用户在详细设定框里写的字是唯一依据——写了什么就是什么，选项和所有字段都按它重填
        umsg = ("用户写的人物设定（唯一依据，一字一句都要落实，没写到的才自己补）：\n\n" + _brief[:3000] +
                "\n\n只为「%s」整理出一份完整的人物设定（性别 %s，年龄 %s；身份、长相、身材、发型、声线、服装、识别特征全部按上面这段文字来，"
                "文字里写了矮小就是矮小、写了短发就是短发，不许按旧设定或故事改回去）。只输出这一个人的 JSON 数组。"
                % (name, card.get("sex") or "按描述判断", card.get("age") or "按描述判断"))
    else:
      umsg = ("故事正文：\n\n" + _p + "\n\n只为「%s」重新设计一份完整的人物设定（性别 %s，年龄 %s，这三样照旧不改；身份、长相、身材、发型、声线、服装、识别特征全部从零重新设计，"
            "不参考任何旧设定）。%s只输出这一个人的 JSON 数组。"
            % (name, card.get("sex") or "按故事判断", card.get("age") or "按故事判断",
               ("这个人现在这一身是：「%s」——新设计的服装必须和它明显不同：换款式底子、换主色、换剪影、换露的位置。" % _old_cl[:160]) if _old_cl else ""))
    got = None
    for temp in (0.9, 0.7, 0.5):
        raw = _q(sysm, umsg, mt=3000, temperature=temp)
        arr = _parse_char_json(raw) or []
        got = next((x for x in arr if isinstance(x, dict) and str(x.get("name") or "").strip() == name), None) or (arr[0] if arr and isinstance(arr[0], dict) else None)
        if got:
            break
    if not got:
        raise RuntimeError("设计师没给出这个人的设定")
    c = sanitize_card_fields(dict(got))
    upd = {
        "char_type": str(c.get("identity") or card.get("char_type") or ""),
        "face_type": c.get("face_type") or "", "build": c.get("build") or "",
        "behavior_anchor": c.get("behavior_anchor") or c.get("personality") or "",
        "personality": c.get("personality") or "", "hair": c.get("hair") or "", "voice": c.get("voice") or card.get("voice") or "",
        "clothing_requirement": c.get("clothing_requirement") or "", "clothing": c.get("clothing") or "",
        "appearance_details": c.get("appearance") or c.get("appearance_details") or "",
        "face_spec": {}, "look_full": "", "image_prompt": "", "clothing_designer": "", "outfit_base": "",
    }
    card.update(upd)
    card["name"] = name
    asset_core.save_asset(sid, "characters", card)
    try:
        _log = []
        normalize_cards_for_world(sid, settings, cards=[card], log=_log)
        if _log and on_step:
            on_step("按世界整理：" + "；".join(_log)[:80])
    except Exception as _nx:
        _dbg("重新设计后整理失败", {"err": str(_nx)[:100]})
    # P358：不出图。脸部九项和发型在这里就定好（都是 Qwen），图由「🎭 重新生成设定图」出
    try:
        design_faces(sid, on_step=on_step, only_missing=True)
    except Exception as _fx:
        _dbg("重新设计后定脸失败", {"err": str(_fx)[:100]})
    try:
        _fresh = asset_core.get_asset(sid, "characters", character_id) or card
        if not hair_is_designed(_fresh.get("hair")):
            # design_hair 跳过已有采用图的人——这个人正要重出图，先把旧图标成待换，让它做
            for v in asset_core.list_assets(sid, "visuals") or []:
                if str(v.get("owner_id") or "") == str(character_id) and str(v.get("kind") or "").startswith("character") and v.get("status") == "adopted":
                    v["stale"] = True
                    asset_core.save_asset(sid, "visuals", v)
            design_hair(sid, on_step=on_step, only_missing=True)
    except Exception as _hx:
        _dbg("重新设计后发型设计失败", {"err": str(_hx)[:100]})
    _images = {}
    if with_image:
        # P400：设计完直接重出设定图并采用（用户：不再手点「重新生成设定图」和「采用」）
        if on_step:
            on_step("按新设定出设定图：" + name)
        try:
            from . import project_settings as _pset
            _stl = _pset.style_of(story_core.get_story(sid) or {})
            v, _pr = asset_core.generate_character_master(sid, character_id, _stl, "formal")
            v = asset_core.adopt(sid, v["visual_id"])
            _images = {"visual_id": v.get("visual_id"), "path": v.get("path")}
        except Exception as _ix:
            _dbg("重新设计后出图失败", {"err": str(_ix)[:120]})
            if on_step:
                on_step("设定图没出来：%s" % str(_ix)[:60])
    return {"ok": True, "card": asset_core.get_asset(sid, "characters", character_id), "images": _images}


def _create_cards(sid, chars_data):
    from . import asset_core, story_core as _stc, oral_story as _os_
    cards = []
    # P413：口述里写明的性别（「女团成员，小林，小爱，小可」）压过设计师的判断
    _st0 = _stc.get_story(sid) or {}
    _brief = " ".join([str((_st0.get("settings") or {}).get("one_line") or "")] +
                      [str(e.get("brief") or "") for e in ((_st0.get("saga") or {}).get("episodes") or []) if isinstance(e, dict)])
    _nm_fix = _os_.fix_names_from_brief(_brief, [str(c.get("name") or "") for c in (chars_data or []) if isinstance(c, dict)])
    for c in chars_data or []:
        if isinstance(c, dict) and _nm_fix.get(str(c.get("name") or "")):
            c["name"] = _nm_fix[str(c.get("name") or "")]                  # P418：「林小」改回口述的「小林」
    _sx_map = _os_.sex_from_brief(_brief, [str(c.get("name") or "") for c in (chars_data or []) if isinstance(c, dict)])
    for c in chars_data or []:
        c = sanitize_card_fields(dict(c))
        # 人物卡在“生成文字”结束时就显示最终头身比例，后续出图不再暗中补选。
        try:
            from . import style_presets as _ratio_sp
            _valid_ratios = list(_ratio_sp.BODY_RATIO_DEFS)
            if str(c.get("sheet_body_ratio") or "") not in _valid_ratios:
                _shape = " ".join(str(c.get(k) or "") for k in
                                  ("name", "age", "identity", "appearance", "build"))
                _style = str((_st0.get("settings") or {}).get("style") or "")
                if re.search(r"婴儿|幼童|迷你|三头身", _shape):
                    c["sheet_body_ratio"] = "迷你幼态"
                elif re.search(r"幼态|小精灵|孩童|儿童|男童|女童|四头身|不到一米", _shape):
                    c["sheet_body_ratio"] = "幼态少年"
                elif re.search(r"小少年|小个子|娇小|五头身|六头身", _shape):
                    c["sheet_body_ratio"] = "小少年比例"
                elif re.search(r"夸张|幻想比例|十二头身", _shape):
                    c["sheet_body_ratio"] = "夸张幻想"
                elif re.search(r"大长腿|十头身", _shape):
                    c["sheet_body_ratio"] = "大长腿"
                elif re.search(r"九头身|极度高挑|超模", _shape):
                    c["sheet_body_ratio"] = "高挑模特"
                elif re.search(r"八头身|高挑|修长|长腿", _shape):
                    c["sheet_body_ratio"] = "模特比例"
                else:
                    c["sheet_body_ratio"] = "标准成人" if "电影" in _style else "高挑模特"
        except Exception:
            c["sheet_body_ratio"] = c.get("sheet_body_ratio") or "标准成人"
        if _sx_map.get(str(c.get("name") or "")) and _sx_map[str(c.get("name") or "")] != str(c.get("sex") or "").strip():
            c["sex"] = _sx_map[str(c.get("name") or "")]
        if not str(c.get("sex") or "").strip():
            c["sex"] = card_sex(c)                                          # P316：空性别先按身份词推，不默认女
        # P421：故事里衣服已撕破/湿透，设计师仍给了完好的日常装 → 让模型按此刻状态改写这一张的服装
        try:
            _prose0 = " ".join(str(e.get("prose") or "")[:3000] for e in ((_st0.get("saga") or {}).get("episodes") or []) if isinstance(e, dict))
            _state_src0 = _brief + " " + str((_st0.get("settings") or {}).get("episode_notes") or "") + " " + _prose0
            _cl0 = str(c.get("clothing") or "")
            if (re.search(r"衣[物服裳].{0,8}(撕|破|烂|湿|少|剩|刮走|扯)|不蔽体|衣衫不整|衣不蔽体|只剩一半|破布", _state_src0)
                    and _cl0 and not re.search(r"撕|破|残|裂|剩|湿|烂|断", _cl0)):
                _new_cl = str(_q("你是服装设计师。故事里这个人的衣服已经被海浪/意外撕破、湿透、只剩一半。把下面这段完好的服装描述改写成此刻的状态：哪件撕到哪里、露出什么、用什么布条裹着，保留原来的款式和颜色词，只输出改写后的一段。",
                                 "【人物】%s\n【原服装】%s\n【故事里的状态】%s" % (c.get("name"), _cl0, _state_src0[:600]), mt=500, temperature=0.4) or "").strip()
                if len(_new_cl) > 20:
                    c["clothing"] = _new_cl.split("\n")[0][:600]
        except Exception as _cx:
            _dbg("服装按状态改写失败", {"人": c.get("name"), "err": str(_cx)[:80]})
        # P459：口述/正文里明写了这个人穿什么 → 具体服装照那个写（项目4：口述「T恤和内裤」「换上校服」，设计师给了晚礼服）
        try:
            _all_names = [str(x.get("name") or "") for x in (chars_data or []) if isinstance(x, dict)]
            _al459 = _os_.card_aliases([dict(x, sex=(x.get("sex") or "")) for x in (chars_data or []) if isinstance(x, dict)])
            _sx459 = {str(x.get("name") or ""): str(x.get("sex") or "") for x in (chars_data or []) if isinstance(x, dict)}
            _hints = _os_.outfit_hints(_brief + "\n" + _prose0, _all_names, _al459, _sx459).get(str(c.get("name") or ""), [])
            _want = _os_.outfit_words(_hints)
            _cl1 = str(c.get("clothing") or "")
            if _want and not any(w in _cl1 for w in _want):
                _new_cl = str(_q("你是服装设计师。用户口述里已经写明这个人穿什么，具体服装必须按口述写：口述点到的每件衣物都要出现，颜色、材质、剪裁细节可以补，"
                                 "不许换成别的款式。只输出一段服装描述。",
                                 "【人物】%s（%s）\n【口述里写的穿着】%s\n【原来的设计（只借颜色材质的写法）】%s"
                                 % (c.get("name"), c.get("identity") or "", "；".join(_hints)[:400], _cl1[:300]), mt=500, temperature=0.4) or "").strip()
                if len(_new_cl) > 15 and any(w in _new_cl for w in _want):
                    c["clothing"] = _new_cl.split("\n")[0][:600]
                    c["clothing_requirement"] = "自定义"
                    c["clothing_from_brief"] = True
                    _dbg("服装按口述改写", {"人": c.get("name"), "口述": _hints[:2], "改后": c["clothing"][:80]})
            elif _want:
                c["clothing_from_brief"] = True                              # 设计师自己就写对了，也标上，免得后面被库/尺度改掉
        except Exception as _cx2:
            _dbg("服装按口述改写失败", {"人": c.get("name"), "err": str(_cx2)[:80]})
        card = asset_core.create_character(sid, {
            "name": c.get("name"), "sex": c.get("sex"), "age": str(c.get("age") or ""),
            "char_type": c.get("identity"),
            "sheet_body_ratio": c.get("sheet_body_ratio") or "自动",
            "face_type": c.get("face_type"),            # Qwen 从选项挑的标签 → 长相下拉
            "build": c.get("build"),                    # 从选项挑 → 身材下拉
            "behavior_anchor": c.get("behavior_anchor") or c.get("personality"),  # 从选项挑 → 性格下拉
            "personality": c.get("personality"),        # 自由性格描述（进详细设定框）
            "hair_preset": c.get("hair_preset") or "自动",
            "hair": c.get("hair"),                      # 发型自由文本
            "voice": c.get("voice"),                    # 声线：音色/语速/说话习惯
            "outfit_preset": c.get("outfit_preset"),    # 从选项挑 → 服装方向下拉
            "clothing_requirement": c.get("clothing_requirement"),  # 从选项挑 → 服装轮廓下拉
            "clothing_from_brief": bool(c.get("clothing_from_brief")),   # P459：口述指定的服装，库和尺度不改
            "clothing": c.get("clothing"),              # 具体服装描述
            "appearance_details": c.get("appearance"),  # 外貌与识别特征
            "first_episode": 1})
        cid = card["character_id"] if isinstance(card, dict) else card
        cards.append(asset_core.get_asset(sid, "characters", cid))
    try:
        from . import story_core as _stc0
        _log0 = []
        normalize_cards_for_world(sid, (_stc0.get_story(sid) or {}).get("settings") or {}, cards=cards, log=_log0)
        if _log0:
            _dbg("建卡后按世界整理服装/发型", {"改动": _log0[:8]})
        cards = [asset_core.get_asset(sid, "characters", c.get("character_id")) or c for c in cards]
    except Exception as _nx:
        _dbg("按世界整理人物卡失败", {"err": str(_nx)[:120]})
    return cards


_MODERN_CLOTH = re.compile(r"针织|牛仔|运动鞋|球鞋|T恤|卫衣|涤纶|尼龙|莱卡|雪纺|羽绒|拉链|运动裤|西装|衬衫领带")   # 皮夹克/连帽 库里自己就有，不算
_HAIR_TRANSIENT = re.compile(r"[，,；;]?[^，,；;。]*(凌乱|刚睡醒|汗湿|汗水|被汗|风吹乱|散乱|沾着|湿漉|湿透|凌乱感)[^，,；;。]*[，,；;]?")
_HAIR_DESIGN = re.compile(r"分线|中分|侧分|三七分|偏分|刘海|层次|卷|波浪|编|辫|马尾|丸子|盘|髻|束|扎|发带|发簪|发饰|发夹|皮绳|寸头|短碎|利落")


def hair_is_designed(h):
    return bool(_HAIR_DESIGN.search(str(h or "")))


def normalize_cards_for_world(sid, settings, cards=None, log=None):
    """P352（用户 9-14 定）：服装按世界+身份走服装库；发型剥掉此刻状态。改了的卡直接存盘。
    · 款式（clothing_requirement）不在这个世界的库里 → 按身份关键词挑一条库条目
    · 具体服装为空、或古代/奇幻世界里带现代面料词 → 换成库条目的底子（用户之后只调宽窄长短）
    · 发型里的「凌乱/汗湿/刚睡醒」删掉"""
    from . import asset_core, project_prompt as _pp, style_presets as _sp
    cards = cards if cards is not None else (asset_core.list_assets(sid, "characters") or [])
    world = _pp.resolved_world(settings or {})
    names = _sp.world_outfit_names(settings or {})
    ancient = world in ("东方仙侠", "东方写实", "西方魔幻", "西方写实")
    n = 0
    _royal = re.compile(r"国王|皇帝|王子|领主|公爵|伯爵|侯爵|男爵|亲王|王后|女王|公主|王妃|皇后|女皇|贵族")
    for c in cards:
        if not isinstance(c, dict) or is_nonhuman(c) or c.get("clothing_from_brief"):
            continue                                                        # P459：口述指定的服装不按库改
        changed = []
        req = str(c.get("clothing_requirement") or "").strip()
        entry = None
        _sx = card_sex(c)
        if not str(c.get("sex") or "").strip() and _sx:
            c["sex"] = _sx
            changed.append("性别按身份判为" + _sx)
        _idt = " ".join(str(c.get(k) or "") for k in ("name", "char_type", "identity", "role"))
        if not _sx:
            names_c = []                                                # P355：性别推不出的卡不配库（配错比不配糟）
        elif _royal.search(_idt):
            names_c = ["宫廷贵族裙"] if _sx == "女" else []                # 男性王族库里没有，留给设计师写
        else:
            names_c = names
        if names_c:
            if req not in names_c:
                if len(names_c) == 1:
                    entry = next((x for x in (_pp.load_outfit_library() or {}).get(world, []) if str(x.get("name") or "") == names_c[0]), None)
                else:
                    entry, _w = _pp._choose_outfit_entry(c, settings or {})
                if entry:
                    c["clothing_requirement"] = str(entry.get("name") or "")
                    changed.append("款式：%s→%s" % (req or "空", entry.get("name")))
            else:
                entry = next((x for x in (_pp.load_outfit_library() or {}).get(world, []) if str(x.get("name") or "") == req), None)
        elif req and req not in names_c and names:
            c["clothing_requirement"] = "自定义"                          # 库里没有合适的（或男性王族被配了裙装）：款式写「自定义」，具体服装照设计师的
            changed.append("款式：%s→自定义" % req)
        if entry:
            c["outfit_base"] = str(entry.get("prompt") or "")
        cl = str(c.get("clothing") or "").strip()
        # P358c：具体服装为空时**不再**把库底子原文抄进卡——规划表建的卡一开始都是空的，抄了之后设计师那步只补空字段，
        # 衣服就永远是库里那 10 套原句（200 实测三个人的衣服和库一字不差）。空着留给设计师按人物写；库只定款式名。
        if entry and cl and ancient and _MODERN_CLOTH.search(cl):
            c["clothing_designer"] = cl
            c["clothing"] = str(entry.get("prompt") or "")
            changed.append("具体服装带现代面料，换成库底子「%s」" % entry.get("name"))
        h = str(c.get("hair") or "")
        h2 = _HAIR_TRANSIENT.sub("", h).strip("，,；; ")
        if len(re.sub(r"[^\u4e00-\u9fa5]", "", h2)) < 3:
            h2 = h                                                   # 删空了就留原句（发色别丢），交给发型设计那步重写
        if h2 != h:
            c["hair"] = h2
            changed.append("发型去掉此刻状态")
        if changed:
            asset_core.save_asset(sid, "characters", c)
            n += 1
            if log is not None:
                log.append("%s：%s" % (c.get("name"), "；".join(changed)))
    return n


def design_hair(sid, on_step=None, only_missing=True):
    """在文字阶段为全组人物选定可编辑的发型预设，并写出最终固定发型。"""
    from . import asset_core, story_core, style_presets
    _has_img = {str(v.get("owner_id") or "") for v in (asset_core.list_assets(sid, "visuals") or [])
                if str(v.get("status") or "") == "adopted" and str(v.get("kind") or "").startswith("character") and not v.get("stale")}
    cards = [c for c in (asset_core.list_assets(sid, "characters") or []) if not is_nonhuman(c)]
    # 有采用图或用户手改过的人不动。新卡即使已有一句自由发型，也要归入一个明确预设，
    # 这样文字阶段结束时人物卡就是最终权威，不必等到出图阶段再偷偷改发型。
    def _manual(c):
        preset = str(c.get("hair_preset") or "自动").strip()
        return bool(c.get("edited_by_user")) and (bool(str(c.get("hair") or "").strip()) or preset not in ("", "自动"))
    todo = [c for c in cards
            if str(c.get("character_id") or "") not in _has_img
            and not _manual(c)
            and (not only_missing
                 or str(c.get("hair_preset") or "自动").strip() in ("", "自动")
                 or not hair_is_designed(c.get("hair")))]
    if not todo:
        return {"ok": 0, "note": "发型都已设计"}
    settings = (story_core.get_story(sid) or {}).get("settings") or {}
    world = str(settings.get("world_type") or "")
    if on_step:
        on_step("发型设计：%d 人" % len(todo))
    lines = []
    for c in cards:
        lock = c not in todo
        lines.append("- %s：%s，%s岁，身份「%s」，骨相「%s」，世界「%s」，当前发型预设「%s」%s%s" % (
            c.get("name"), c.get("sex") or "", c.get("age") or "", c.get("char_type") or "", c.get("face_type") or "",
            world, c.get("hair_preset") or "自动",
            ("，现在的发型描述：" + str(c.get("hair"))) if str(c.get("hair") or "").strip() else "",
            "　【已定，照抄】" if lock else ""))
    hair_options = "女：%s\n男：%s" % (
        "、".join((style_presets.HAIR_DEFS.get("女") or {}).keys()),
        "、".join((style_presets.HAIR_DEFS.get("男") or {}).keys()))
    ins = (_ins("发型设计_指令词.txt")
           .replace("【HAIR_OPTIONS】", hair_options)
           .replace("【CHARACTERS】", "\n".join(lines)))
    raw = _q(ins, "按上面的人物表输出 JSON。", mt=900, temperature=0.8)
    got = _parse_char_json(raw) or []
    by = {str(g.get("name") or "").strip(): g for g in got if isinstance(g, dict)}
    n = 0
    for c in todo:
        item = by.get(str(c.get("name") or "").strip()) or {}
        sex = "男" if str(c.get("sex") or "") == "男" else "女"
        defs = style_presets.HAIR_DEFS.get(sex) or {}
        preset = str(item.get("hair_preset") or "").strip()
        h = str(item.get("hair") or "").strip()
        if preset not in defs:
            preset = next((name for name in defs if name in h), "")
        if preset not in defs:
            preset = "短碎发" if sex == "男" and "短碎发" in defs else ("长直发" if "长直发" in defs else next(iter(defs), "自动"))
        if len(h) < 8:
            h = str(defs.get(preset) or c.get("hair") or "").strip()
        if len(h) >= 8:
            c["hair_preset"] = preset
            c["hair"] = _HAIR_TRANSIENT.sub("", h).strip("，,；; ")
            asset_core.save_asset(sid, "characters", c)
            n += 1
    return {"ok": n, "total": len(todo)}


# ──────── 露体尺度的服装核对 + 定向修复（2026-09-02）────────
# 「每人至少露五处、贴身处只留丁字裤/乳贴、外衣敞开、一组反差」是可数的，
# Qwen 常常写成普通的性感办公室装（实测露三处、蕾丝内裤、没反差）。
# 判断题给代码：数一遍；不够就单独一次调用只重写「具体服装」这一项，不打回。
_EXPOSE_WORDS = ("小腹", "腰", "大腿", "臀", "后背", "背部", "锁骨", "侧乳", "下乳", "胸")
_COVER_WORDS = ("丁字裤", "遮片", "布片", "不穿内裤", "贴片")   # 性器官靠什么遮（乳贴不算）
# 露出得是版型自带的（用户 2026-09-04：旗袍开衩那种，不是西装敞开那种）
_CUT_WORDS = ("开衩", "开叉", "镂空", "挖空", "挖剪", "挖到", "单肩", "单腿", "半边", "绑带", "系带",
              "高衩", "低腰", "前后片", "围裙式", "露背", "钥匙孔", "开窗",
              # 紧身少布型 / 内衣结构型 的裁剪词（2026-09-04 v3）
              "网眼", "下乳", "露脐", "超短", "抹胸", "束腰", "胸衣", "吊袜带", "连体", "比基尼", "紧身",
              # ④ 多布侧露型的裁剪词（2026-09-04）
              "前片", "后片", "两侧无布", "侧露", "侧全开", "侧身全开", "两侧完全敞开")
_SILHOUETTE_WORDS = ("紧身少布", "内衣结构", "长摆开衩", "多布侧露")
_STRIP_WORDS = ("敞开", "敞着", "未扣", "不扣", "解开", "滑落", "褪到", "没穿", "脱下", "半脱", "露出内")


def _seduce_tier_on(settings):
    """项目是否勾了「极致诱惑」这类露体尺度。"""
    return any("极致诱惑" in str(t) for t in ((settings or {}).get("content_tendencies") or []))


# P360：极致诱惑＝三级片口径——性爱过程可写可拍，性器官名一个不出现。模型不可靠，这张表是程序兜底。
_GENITAL_SWAP = (
    (re.compile(r"阴茎|肉棒|肉棍|阳具|鸡巴|龟头|阴道|小穴|蜜穴|花穴|阴户|阴部|阴唇|阴蒂|性器官|性器|生殖器|下体"), "下身"),
    (re.compile(r"睾丸|阴囊"), "大腿根"),
    (re.compile(r"肛门|屁眼|后穴|菊穴"), "臀缝"),
)


def desex_text(text, settings):
    """P360 曾在「极致诱惑」下把性器官名换成泛称；P457（用户 9-20）取消这个限制——原样返回。函数留着给旧调用点。"""
    t = str(text or "")
    if True:
        return t
    for rx, rep in _GENITAL_SWAP:
        t = rx.sub(rep, t)
    t = re.sub(r"(下身|大腿根|臀缝)(?:的|和|与|、)?\1", r"\1", t)   # 「阴唇和阴道」→「下身和下身」→「下身」
    return t


def _seduce_check(clothing):
    """返回 (是否达标, 缺什么)。"""
    c = str(clothing or "")
    got = {w for w in _EXPOSE_WORDS if w in c}
    # 「背部」「后背」算一处，「侧乳」「下乳」「胸」算胸这一处
    n = len(got - {"背部", "后背", "侧乳", "下乳", "胸"}) + (1 if got & {"背部", "后背"} else 0) \
        + (1 if got & {"侧乳", "下乳", "胸"} else 0)
    miss = []
    if n < 5:
        miss.append("露的部位只有 %d 处（要≥5）：%s" % (n, "、".join(sorted(got)) or "无"))
    # 【内裤】只有连体衣自带裆部遮挡；其余一律不穿内裤、由前片/后片/裆片遮住
    is_body = "连体" in c
    has_pants = ("丁字裤" in c) or ("内裤" in c and "不穿内裤" not in c and "无内裤" not in c)
    if is_body and has_pants:
        miss.append("连体衣自带裆部遮挡，不再另穿丁字裤/内裤")
    if not is_body:
        if has_pants:
            miss.append("除高衩连体衣外不穿内裤：去掉丁字裤/内裤，性器官由前片/后片/裆片遮住")
        if not any(w in c for w in ("不穿内裤", "无内裤")) or not any(w in c for w in ("前片", "后片", "裆片", "遮住", "遮盖")):
            miss.append("要写明「不穿内裤」并说清性器官和肛门由前片/后片/裆片遮住")
    if "乳房" not in c and not ("侧乳" in c and "下乳" in c):
        miss.append("乳房没露出来（要乳房大面积裸露：上缘、侧乳、下乳线）")
    # 【不露点】乳头要由服装遮住，不能写成露出
    if any(w in c for w in ("乳头露出", "乳头裸露", "露出乳头", "乳头外露", "乳头完全裸露", "乳头不遮")):
        miss.append("乳头要由服装本身遮住（前片边缘/上衣下缘/带子/杯边），不能露")
    if not any(w in c for w in ("乳头", "乳晕")):
        miss.append("要写明乳头由服装的哪一部分遮住")
    cuts = [w for w in _CUT_WORDS if w in c]
    if len(cuts) < 2:
        miss.append("露出不是版型自带的：裁剪手法只有 %d 种（要≥2：开衩/镂空/单侧/绑带/高衩低腰/前后片/露背）" % len(cuts))
    if not any(w in c for w in _SILHOUETTE_WORDS):
        miss.append("没写明轮廓选了哪种（紧身少布型 / 内衣结构型 / 长摆开衩型）")
    # 否定句里提到的脱法不算（「不依赖外套敞开或滑落」是在说明没用这招）
    _pos = "，".join(seg for seg in re.split(r"[，。；]", c)
                    if not any(n in seg for n in ("不依赖", "不靠", "不用", "无需", "不是", "并非", "没有")))
    for _ok in ("两侧敞开", "两侧完全敞开", "侧面敞开", "侧身敞开", "侧边敞开", "两侧开敞"):
        _pos = _pos.replace(_ok, "")          # 说的是版型两侧无布，不是把衣服脱开
    strips = [w for w in _STRIP_WORDS if w in _pos]
    if strips:
        miss.append("用了「脱一半」的做法：%s——露出要靠裁剪，不靠敞开/解开/滑落" % "、".join(strips))
    if "反差" not in c:
        miss.append("没写这套的反差是什么")
    # 遮挡件不能是透的：「透明/薄纱/透光/网眼」和「乳贴/丁字裤」在同一小句里就算
    for seg in re.split(r"[，。；、]", c):
        seg2 = seg.replace("不透光", "").replace("不透明", "").replace("不透", "")   # 「不透光的」不算透
        if any(w in seg2 for w in ("丁字裤", "遮片")) and any(w in seg2 for w in ("透明", "薄纱", "透光", "网眼", "半透")):
            miss.append("性器官遮挡件写成透的了：「%s」——要不透光的实色材质" % seg.strip()[:30])
            break
    if not any(w in c for w in ("靴", "袜", "腿环", "腿带", "绑带")):
        miss.append("没有腿部配件（长靴/过膝袜/腿环/大腿绑带至少一件）")
    # 乳头是软要求（露了不算失败）；但乳贴/贴片是明确不要的（用户 2026-09-04：别扭）
    _pos_n = "，".join(seg for seg in re.split(r"[，。；]", c)
                      if not any(n in seg for n in ("无乳贴", "不用乳贴", "没有乳贴", "不使用", "不依赖", "不靠")))
    if any(w in _pos_n for w in ("乳贴", "胸贴", "乳头贴", "饰片", "贴片遮")):
        miss.append("用了乳贴/贴片——乳头要么露出来，要么由服装本身（上衣下缘/布带/杯边）遮住")
    return (not miss), miss


def _seduce_repair(card, settings, clothing, miss, log=None):
    """只重写「具体服装」这一项。一次调用、一个任务。"""
    from . import style_presets as sp
    tname = next((t for t in (settings.get("content_tendencies") or []) if "极致诱惑" in str(t)), "")
    tier = (sp._scale_table().get(tname) or {}).get("figure", "")
    sysm = ("你是顶级的成人向角色服装设计师。下面这套服装没有达到本项目的露体尺度，"
            "把它**按清单改到位**，只输出改好的「具体服装」一段文字，其余什么都不要输出。\n\n"
            "【本项目的尺度要求】\n" + tier + "\n\n"
            "【必须做到】\n"
            "1. 逐条写明露出的部位，至少五处：小腹、腰线、大腿全长、锁骨、胸部上缘和侧乳、下乳线、后背、臀侧。\n"
            "2. **每处露出写明裁剪手法**（开衩/镂空挖剪/单侧不覆盖/绑带结构/高衩低腰/前后片式/露背挖剪），"
            "露出是版型自带的——**不许用敞开、未扣、解开、滑落、脱一半这种做法**。\n"
            "3. 先写明轮廓选了哪种：**紧身少布型**（连体紧身衣高衩挖空 / 超小背心+紧身短裤 / 抹胸+高衩短裳）、"
            "**内衣结构型**（teddy / 胸衣束腰 / 吊袜带长袜 / harness 绑带）、**长摆开衩型**、"
            "或**多布侧露型（不穿内裤）**：前片遮胸口到大腿根、后片遮臀沟到大腿根、两侧从腋下到大腿根无布，"
            "侧乳/侧腰/臀侧/大腿外侧裸露，性器官靠前后片遮。外层不是必需的，穿得少优先。\n"
            "4. **只有高衩连体衣自带裆部遮挡；其余一律不穿内裤**，写明「不穿内裤」，性器官和肛门由前片/后片/裆片遮住。不用丁字裤。\n"
            "5. 写明这套的反差：端庄的轮廓配极端的裁剪（高领长袖 vs 侧身全开；拖地长裙 vs 前开到胯）。\n"
            "6. 面料至少两种有厚度和垂感；轻薄透最多一处且贴身显曲线。\n"
            "7. **乳房大面积裸露**（上缘、侧乳、下乳线全在），**乳头由服装本身遮住**"
            "（前片边缘、上衣下缘、横过胸口的带子、胸衣杯边）——**不露点，也不用乳贴、胸贴、贴片、饰片**。"
            "**性器官和肛门绝对不露**：由前片/后片/裆片遮住——不透光实色材质，写明材质颜色。\n"
            "8. 至少一件腿部配件：长靴、过膝袜、腿环或大腿绑带，写清在哪条腿、什么材质。\n"
            "外衣的款式保持「%s」。" % (card.get("clothing_requirement") or "原款式"))
    user = ("【人物】%s，%s，%s岁，身材「%s」\n【现在这套（不达标）】\n%s\n【缺什么】\n%s\n\n只输出改好的具体服装。"
            % (card.get("name"), card.get("sex"), card.get("age"), card.get("build") or "",
               clothing, "\n".join("- " + m for m in miss)))
    fixed = _q(sysm, user, mt=900, temperature=0.8).strip()
    fixed = fixed.replace("具体服装：", "").strip("「」\" \n")
    ok, still = _seduce_check(fixed)
    if log is not None:
        log.append("%s 服装定向修复：%s" % (card.get("name"), "达标" if ok else "仍缺 " + "；".join(still)))
    return fixed if len(fixed) > 40 else clothing


def complete_cards(sid, cards, prose, settings, on_step=None, clue_label="故事正文"):
    """工作流1 的补全：用户手选了下拉、但发型/声线/具体服装/外貌这些自由文本还空着，
    就按故事正文把空的补上。**用户填过/选过的一律锁死不动，只补空的。**

    【为什么要有这一步】2026-08-26 用户实测：先建人物再点全部生成，run_all 看到
    有预设人物就直接拿去写故事、出图，跳过了角色设计师——那些自由文本字段永远是空的，
    人设卡"生成完还是不完整"。
    """
    from . import asset_core

    NEED = ("hair", "voice", "clothing", "appearance_details", "personality")
    LOCK_LABELS = (("姓名", "name"), ("性别", "sex"), ("年龄", "age"),
                   ("人物类型", "char_type"), ("长相", "face_type"), ("身材", "build"),
                   ("性格神态", "behavior_anchor"), ("服装款式", "clothing_requirement"))
    out, failed = [], []
    for c in cards:
        card = asset_core.get_asset(sid, "characters", c["character_id"]) or c
        missing = [k for k in NEED if not str(card.get(k) or "").strip()]
        # personality 和 behavior_anchor 一样，说明只有标签没有详述
        if (str(card.get("personality") or "").strip()
                == str(card.get("behavior_anchor") or "").strip()):
            if "personality" not in missing:
                missing.append("personality")
        if not missing:
            out.append(card)
            continue
        if on_step:
            on_step("补全人物设定：" + str(card.get("name")))
        locked = "\n".join("%s：%s" % (lab, card.get(key))
                            for lab, key in LOCK_LABELS
                            if str(card.get(key) or "").strip() not in ("", "自动"))
        user = ("【%s】\n%s\n\n【这个人物已定（锁死，一个字不许改）】\n%s\n\n"
                "【只补这几项，其余一律不要出现】%s" % (
                    clue_label, str(prose or "")[:4000], locked, "、".join(missing)))
        data = {}
        err = ""
        for attempt in (1, 2):
            try:
                # 【年龄门】补全是逐卡调用的，尺度按这张卡过 _scale_gate：
                # 内容边界由共享规则检查，拒绝时保留原人物卡。
                raw = _q(_fill(_ins("人物补全_指令词.txt"), _scale_gate(settings, card),
                               scale_layers=("story", "figure")),
                         user, mt=2000, temperature=0.85)
                m = re.search(r"\{.*\}", raw, re.S)
                data = json.loads(m.group(0)) if m else {}
                if data:
                    break
                err = "Qwen 没返回 JSON"
            except Exception as ex:
                data, err = {}, str(ex)[:60]
                if attempt == 1 and on_step:
                    on_step("重试补全：" + str(card.get("name")))
        changed = False
        for k in missing:
            v = str((data or {}).get(k) or "").strip()
            if v:
                card[k] = v
                changed = True
        # 【露体尺度核对】勾了极致诱惑且这张卡过了年龄门，服装不达标就定向修一次
        try:
            if "clothing" in missing and _seduce_tier_on(_scale_gate(settings, card)) and not is_nude(card, settings) \
                    and card_sex(card) == "女" and not str(card.get("clothing") or "").strip() and not card.get("clothing_from_brief"):
                # 补全没给服装（2026-09-04 薇拉实测：JSON 里没这个字段）——用修复通道从零设计
                if on_step:
                    on_step("%s 补全没写出服装，按露体尺度从零设计" % card.get("name"))
                card["clothing"] = _seduce_repair(card, settings, "（还没有设计，从零写一套）",
                                                  ["整套服装都还没写"])
                changed = True
            if changed and "clothing" in missing and _seduce_tier_on(_scale_gate(settings, card)) and not is_nude(card, settings) and card_sex(card) == "女" and not card.get("clothing_from_brief"):
                _ok, _miss = _seduce_check(card.get("clothing"))
                if not _ok:
                    if on_step:
                        on_step("%s 服装未达露体尺度，定向修复：%s" % (card.get("name"), "；".join(_miss)))
                    card["clothing"] = _seduce_repair(card, settings, card.get("clothing"), _miss)
        except Exception as _e:
            if on_step:
                on_step("服装核对没做成，按原样保存：" + str(_e)[:100])
        if changed:
            asset_core.save_asset(sid, "characters", card)
        else:
            failed.append("%s(%s)" % (card.get("name"), err or "没补上"))
        out.append(card)
    return {"cards": out, "failed": failed}


# ──────── 脸部设计（2026-09-02）────────
# 用户实测三个不同世界的女主是同一张脸。根因：长相标签的定义是一句固定模板，
# 同标签的人出图提示词里脸是逐字相同的一句话；角色设计师被要求写脸的骨架，
# 实际写的全是瞳色/睫毛/肤色/笑意（软要求，模型不执行）。
# 改成九项可数填空：模型从固定选项里选，代码核对选项合法、组合不重复，
# 不合法就修（不打回重来），最后拼进出图提示词。

_FACE_OPTS = {
    "face_shape": ("长脸", "方脸", "圆脸", "心形脸", "鹅蛋脸"),
    "eye_len": ("长", "短"),
    "eye_tail": ("上扬", "平", "下垂"),
    "brow": ("平眉", "挑眉", "弯眉"),
    "brow_bone": ("高", "低"),
    "nose_bridge": ("高", "低"),
    "nose_tip": ("尖", "圆"),
    "lip": ("薄", "厚"),
    "jaw": ("尖", "方", "圆"),
}
_FACE_CN = {
    "face_shape": "脸型", "eye_len": "眼裂", "eye_tail": "眼尾", "brow": "眉形",
    "brow_bone": "眉骨", "nose_bridge": "鼻梁", "nose_tip": "鼻头", "lip": "唇",
    "jaw": "下颌",
}
_FACE_MIN_DIFF = 3   # 任意两人至少几项不同


def _face_norm(spec):
    """每项落到合法选项上：原样合法→留；含选项词→取；否则空。"""
    out = {}
    for k, opts in _FACE_OPTS.items():
        v = str((spec or {}).get(k) or "").strip()
        hit = v if v in opts else next((o for o in opts if o in v), "")
        out[k] = hit
    out["note"] = str((spec or {}).get("note") or "").strip()[:30]
    return out


def _face_diff(a, b):
    return sum(1 for k in _FACE_OPTS if a.get(k) != b.get(k))


def _face_fix(specs, log=None, locked=()):
    """核对 + 修：空项按顺序补第一个没人用的选项；两人差异不足三项，
    就给**没锁住**的那个换项，直到够三项。确定性，不再叫模型。
    locked：这些下标的人已经定过脸（可能已出过图），一项都不动。"""
    keys = list(_FACE_OPTS)
    locked = set(locked or ())
    for i, sp in enumerate(specs):
        if i in locked:
            continue
        for k in keys:
            if not sp.get(k):
                used = {o.get(k) for o in specs[:i]}
                sp[k] = next((o for o in _FACE_OPTS[k] if o not in used), _FACE_OPTS[k][0])
                if log is not None:
                    log.append("%s 的%s空着，补成「%s」" % (sp.get("name"), _FACE_CN[k], sp[k]))
    for i in range(len(specs)):
        for j in range(len(specs)):
            if i == j:
                continue
            # 谁来让步：i 没锁就动 i；i 锁了 j 没锁就动 j；都锁了不动
            mover = i if i not in locked else (j if j not in locked else None)
            if mover is None:
                continue
            other = j if mover == i else i
            guard = 0
            while _face_diff(specs[mover], specs[other]) < _FACE_MIN_DIFF and guard < 12:
                k = next((k for k in keys if specs[mover][k] == specs[other][k]), None)
                if k is None:
                    break
                cur = specs[mover][k]
                specs[mover][k] = next(o for o in _FACE_OPTS[k] if o != cur)
                guard += 1
                if log is not None:
                    log.append("%s 和 %s 太像，把 %s 的%s换成「%s」" % (
                        specs[mover].get("name"), specs[other].get("name"),
                        specs[mover].get("name"), _FACE_CN[k], specs[mover][k]))
    return specs


_NONHUMAN = re.compile(r"魔像|石像|石魔|傀儡|机械生命|机器人|机甲|巨龙|龙族|巨兽|野兽|妖兽|怪物|怪兽|丧尸|亡灵|骷髅|史莱姆|巨人|巨魔|哥布林|兽人|触手|元素体|构装")


def is_nonhuman(c):
    """非人形角色：不套人脸九项，身材写体型尺度（米）。看 char_type / 身份 / 名字。"""
    c = c or {}
    txt = " ".join(str(c.get(k) or "") for k in ("char_type", "identity", "name"))
    return bool(_NONHUMAN.search(txt))


def body_scale_line(c):
    """非人形角色的体型尺度：卡上写了「N米」就用，没写默认「约3米」。"""
    c = c or {}
    txt = " ".join(str(c.get(k) or "") for k in ("look_full", "identity_anchor", "appearance_details", "clothing", "build"))
    m = re.search(r"(约?\s*[一二两三四五六七八九十\d.]+\s*米)", txt)
    h = m.group(1).strip() if m else "约3米"
    return "体型尺度：高%s，体量远超常人，站在人旁边要有明显的大小差；不用人类头身比" % h


def face_line(c):
    """人物卡上的脸部九项 → 一句给出图模型的话。没做过脸部设计就返回空。"""
    f = (c or {}).get("face_spec") or {}
    if not f.get("face_shape"):
        return ""
    return ("%s，%s眼裂、眼尾%s，%s、眉骨%s，鼻梁%s、鼻头%s，%s唇，下颌%s%s" % (
        f["face_shape"], f["eye_len"], f["eye_tail"], f["brow"], f["brow_bone"],
        f["nose_bridge"], f["nose_tip"], f["lip"], f["jaw"],
        ("，静态第一印象：" + f["note"]) if f.get("note") else ""))


def design_faces(sid, on_step=None, only_missing=True):
    """给这个项目还没定脸的人各定一张结构不同的脸，写回人物卡 face_spec。

    一次模型调用做全组（组内不重复它才管得住）；核对和修复在代码里。
    only_missing=True：已经有 face_spec 的人锁住不动（可能已经出过图了，
    改了脸等于换人），新人的脸要和他们拉开。
    """
    from . import asset_core
    cards = asset_core.list_assets(sid, "characters")
    if not cards:
        return {"ok": 0, "note": "没有人物"}
    has = [bool((c.get("face_spec") or {}).get("face_shape")) or is_nonhuman(c) for c in cards]   # 非人形不定脸
    todo = [c for c, h in zip(cards, has) if not (only_missing and h)]
    if not todo:
        return {"ok": 0, "note": "所有人都已经定过脸", "fixes": [], "specs": []}
    if on_step:
        on_step("脸部设计：%d 人（%d 人已定，锁住）" % (len(todo), len(cards) - len(todo)))
    lines = []
    for c, h in zip(cards, has):
        if is_nonhuman(c):
            continue                                    # 非人形：不进脸部设计
        try:
            from . import style_presets as _spf
            _fd = _spf.face_def(card_sex(c) or "女", c.get("face_type")) or ""
        except Exception:
            _fd = ""
        base = "- %s：%s，%s岁，骨相档「%s」%s，性格「%s」%s" % (
            c.get("name"), c.get("sex") or "", c.get("age") or "",
            c.get("face_type") or "自动", ("（九项照这个骨相定：%s）" % _fd) if _fd else "",
            c.get("behavior_anchor") or c.get("personality") or "",
            ("，身份：" + str(c.get("char_type") or c.get("identity") or "")) if (c.get("char_type") or c.get("identity")) else "")
        if only_missing and h:
            f = c["face_spec"]
            base += "　【已定，照抄不改】" + "、".join(
                "%s=%s" % (_FACE_CN[k], f.get(k, "")) for k in _FACE_OPTS)
        lines.append(base)
    ins = _ins("脸部设计_指令词.txt").replace("【CHARACTERS】", "\n".join(lines))
    raw = _q(ins, "按上面的人物表输出 JSON。", mt=1200, temperature=0.9)
    got = _parse_char_json(raw) or []
    by = {str(g.get("name") or "").strip(): g for g in got if isinstance(g, dict)}
    specs, locked, log = [], [], []
    for idx, (c, h) in enumerate(zip(cards, has)):
        if is_nonhuman(c):
            sp = {}                                     # 非人形：没有人脸九项
            locked.append(idx)
        elif only_missing and h:
            sp = dict(c["face_spec"])           # 锁住：原样
            locked.append(idx)
        else:
            sp = _face_norm(by.get(str(c.get("name") or "").strip(), {}))
        sp["name"] = c.get("name")
        specs.append(sp)
    _face_fix(specs, log, locked=locked)
    n = 0
    for idx, (c, sp) in enumerate(zip(cards, specs)):
        if idx in locked:
            continue
        c["face_spec"] = {k: sp[k] for k in list(_FACE_OPTS) + ["note"]}
        asset_core.save_asset(sid, "characters", c)
        n += 1
    return {"ok": n, "fixes": log, "specs": [sp for i, sp in enumerate(specs) if i not in locked]}


def card_sex(c):
    """卡上性别：写了就用；没写按身份/类型/描述里的词推（少年法师→男、猫娘→女）；推不出返回空，**不默认女**（P316，项目183 男主出成女）。"""
    v = str((c or {}).get("sex") or "").strip()
    if v in ("男", "女"):
        return v
    _idt = " ".join(str((c or {}).get(k) or "") for k in ("name", "char_type", "identity", "role"))
    if re.search(r"王后|女王|公主|王妃|皇后|女皇", _idt):
        return "女"                                                     # P355：王族身份词先判（200：国王没性别被配成女武斗家）
    if re.search(r"国王|皇帝|王子|领主|公爵|伯爵|侯爵|男爵|亲王|老王|王上|陛下", _idt):
        return "男"
    try:
        from .whole_plan import guess_sex, norm_sex
        v2 = norm_sex(v)
        if v2:
            return v2
        txt = " ".join(str((c or {}).get(k) or "") for k in ("char_type", "identity", "identity_anchor", "role", "relation", "appearance_details", "look_full"))
        return guess_sex(str((c or {}).get("name") or ""), txt, "", "")
    except Exception:
        return ""


def _build_cm(sex, build):
    """身材档定义里的身高区间中值（cm）；取不到返回 None。"""
    from . import style_presets as sp
    d = sp.body_def(sex, build) or ""
    m = re.search(r"(\d{2,3})\s*[–\-~～]\s*(\d{2,3})\s*cm", d)
    return (int(m.group(1)) + int(m.group(2))) // 2 if m else None


def height_text(c):
    """人设图使用的身高句：预设档展开成真实范围；手填厘米数原样保留。"""
    raw = str((c or {}).get("height") or "").strip()
    if raw in ("", "自动", "None"):
        return ""
    try:
        from . import style_presets as sp
        sex = card_sex(c) or "女"
        d = sp.height_def(sex, raw) or ""
    except Exception:
        d = ""
    return "%s（%s）" % (raw, d) if d else raw


def default_height(c):
    """这个人多高（cm）：卡上填了就用；小孩可按年龄兜底。

    成人身高不再从身材档推断——身高和身材是两个独立选项，不能互相覆盖。
    """
    m = re.search(r"(\d{2,3})\s*(?:cm|厘米|公分)", str((c or {}).get("height") or ""))
    if m:
        return int(m.group(1))
    sex = card_sex(c) or "女"
    try:
        from . import style_presets as sp
        d = sp.height_def(sex, (c or {}).get("height")) or ""
        m = re.search(r"(\d{2,3})\s*[–\-~～]\s*(\d{2,3})\s*cm", d)
        if m:
            return (int(m.group(1)) + int(m.group(2))) // 2
    except Exception:
        pass
    return None


def body_text(c):
    """给出图的纯身材句，只描述骨架、肌肉、脂肪与曲线，不夹带身高。"""
    from . import style_presets as sp
    sex = card_sex(c) or "女"
    build = sp.normalize_body_option(sex, (c or {}).get("build"))
    build = "" if build in ("", "自动", "None") else build
    if not build:
        return ""
    d = sp.body_def(sex, build) or ""
    # 年龄与性别已有独立字段；体型正文不再额外指定“成年”身份。
    d = re.sub(r"^成年(?:男性|女性|肌肉女性)[，,、 ]*", "", d)
    return "%s（%s）" % (build, d) if d else build



def _char_one_line(c, settings=None):
    """人物卡 → 喂给出图指令词的一句话。

    【两个坑，都踩过】
    1. 以前这里**漏了 face_type**——用户在界面选的「精致/冷艳/清冷」根本没进提示词，
       所以几个女角色长得都一样（2026-08-26 用户实测发现）。
    2. 只给标签（"精致"）不够，Qwen 对抽象词的理解每次都飘。这里把标签**展开成定义**
       （精致 → 巴掌小脸、鹅蛋轮廓、鼻梁细直、五官精巧无瑕），给 Qwen 具体的五官事实。
    """
    from . import style_presets as sp

    def _opt(v):
        v = str(v or "").strip()
        return "" if v in ("", "自动", "None") else v

    sex = card_sex(c)                                        # P316：不默认女；推不出就不写性别，让设计师按身份判断
    face, build = _opt(c.get("face_type")), _opt(c.get("build"))
    pose, cloth = _opt(c.get("behavior_anchor")), _opt(c.get("clothing_requirement"))

    def _with_def(label, getter, *args):
        """标签 + 它的定义，定义取不到就只给标签。"""
        if not label:
            return ""
        try:
            d = (getter(*args) or "").strip()
        except Exception:
            d = ""
        return "%s（%s）" % (label, d) if d else label

    ratio_name = _opt(c.get("sheet_body_ratio"))
    if not ratio_name:
        try:
            ratio_name = str((sp.resolve_auto_character(c, settings or {}) or {}).get("sheet_body_ratio") or "").strip()
        except Exception:
            ratio_name = ""
    ratio_text = _with_def(ratio_name, sp.body_ratio_def, ratio_name)

    # 人设图姿势是独立层：普通男女各自读取可编辑预设；成人尺度有姿势时按性别覆盖。
    # 主指令本身不再写固定动作，五官、比例、身材、发型继续来自同一张人物卡。
    scale_pose = ""
    scale_clothing = ""
    gated = _scale_gate(settings or {}, c)
    try:
        variant = sp.character_figure_variant(gated.get("content_tendencies") or [], sex)
        scale_pose = str(variant.get("pose") or "").strip()
        scale_clothing = str(variant.get("clothing") or "").strip()
    except Exception:
        pass
    sheet_pose = scale_pose or sp.sheet_pose_def(sex)

    bits = [
        "%s（%s%s%s）" % (c.get("name"), (sex + "，") if sex else "",
                         ((str(c.get("age")).strip() + ("岁" if str(c.get("age")).strip().isdigit() else "")) + "，") if str(c.get("age") or "").strip() else "", c.get("char_type") or ""),
        # 脸的骨架（九项可数填空）排在标签前面——标签定义是一句模板，
        # 同标签的人会是同一张脸；骨架才是把人和人分开的东西
        ("脸部结构：" + face_line(c)) if not is_nonhuman(c) else "",
        ("骨相：" + _with_def(face, sp.face_def, sex, face)) if not is_nonhuman(c) else "",
        ("头身比例：" + ratio_text) if ratio_text and not is_nonhuman(c) else "",
        ("身材：" + body_text(c)) if not is_nonhuman(c) else body_scale_line(c),
        "神态：" + _with_def(pose, sp.pose_def, pose),
        "发型：" + str(c.get("hair") or ""),
        # P354：有具体服装时款式只给名字（库定义是整套衣服，两套并列会画成两身）
        ("服装款式：" + (cloth if str(c.get("clothing") or "").strip() else _with_def(cloth, sp.cloth_def, cloth))),
        "具体服装：" + str(c.get("clothing") or ""),
        "外貌识别特征：" + str(c.get("appearance_details") or ""),
        ("人设图姿势：" + sheet_pose) if sheet_pose else "",
        ("成人设定图服装：" + scale_clothing) if scale_clothing else "",
    ]
    return "，".join(x for x in bits if x.split("：")[-1].strip())


def style_kind(settings):
    """这个画风是实拍、二维还是三维（render_notes.json 里现成的 kind）。

    取不到就按 photo——那是默认画风。
    """
    st = str((settings or {}).get("style") or "").strip()
    if not st:
        return "photo"
    try:
        import json as _json
        rn = _json.loads((INS.parent / "render_notes.json").read_text(encoding="utf-8"))
        return str((rn.get(st) or {}).get("kind") or "photo").strip() or "photo"
    except Exception:
        return "photo"


# 人种事实同一件，但要按画风的语言说。
# 原来只有一套照片语言（「面部立体有骨感」「雀斑」），钉在所有画风头上，
# 把 3D游戏 和电影级写实拉成了同一张写实欧美脸（P216 四画风实测）。
_RACE_LINES = {
    ("西方", "photo_glam"): ("欧洲白人，像欧美模特或女演员——深眼窝、眉骨突出、眉毛有棱角、"
                        "高而窄的鼻梁、鼻尖略翘、颧骨明显、唇饱满、下颌线清晰、面部立体有骨感；"
                        "肤色白皙带粉或雀斑，浅色瞳孔（灰、蓝、绿、浅褐），眉毛与头发同色；"
                        "五官是纯粹的欧洲人"),
    ("西方", "photo_real"): ("欧洲白人，是现实里真实存在的一个人——深眼窝、眉骨突出、"
                             "高而窄的鼻梁、颧骨和下颌是这个人自己的形状而不是标准模板，"
                             "左右脸不完全对称，鼻子和嘴各有各的特点；"
                             "肤色白皙带粉或雀斑，浅色瞳孔（灰、蓝、绿、浅褐），眉毛与头发同色；"
                             "皮肤上有真实的毛孔、细纹、几颗痣和轻微色斑，眉毛边缘有碎毛，"
                             "嘴唇有干纹，这是一张没有修过的脸"),
    ("东方", "photo_real"): ("东亚人，是现实里真实存在的一个人——眼窝浅、眼距略宽、单或内双、"
                             "鼻根低而鼻梁挺，脸型和下颌是这个人自己的形状而不是标准模板，"
                             "左右脸不完全对称；黑或深棕的眼睛和头发；"
                             "皮肤上有真实的毛孔、细纹、几颗痣和轻微色斑，眉毛边缘有碎毛，"
                             "嘴唇有干纹，这是一张没有修过的脸"),
    ("西方", "2d"): ("欧洲人的轮廓用日漫画法表现——脸小、下巴尖，眼睛大而长、眼尾微挑，"
                     "眉骨和深眼窝只用一两笔线条交代，鼻梁高但鼻子只一小笔，嘴小唇形清楚；"
                     "浅色瞳孔（灰、蓝、绿、浅褐），眉毛与头发同色；"
                     "肤色是平涂的通透白皙，脸颊一小块柔和腮红，皮肤是干净的平涂色块"),
    ("西方", "3d"): ("欧洲人的骨相做成 CG 角色——小脸尖下巴，眼睛偏大偏亮、有清晰的高光点，"
                     "眉骨和颧骨是雕塑感的面块转折，鼻梁高窄、鼻头小巧，唇形饱满干净；"
                     "浅色瞳孔（灰、蓝、绿、浅褐），眉毛与头发同色；"
                     "皮肤像上了釉的瓷面，均匀光滑、只有柔和的粉嫩过渡和干净的镜面高光"),
    ("东方", "photo_glam"): ("东亚人，像东亚偶像——眼窝浅、眼距略宽、单或内双、鼻根低而鼻梁挺、"
                        "脸型小而流畅、皮肤白皙细腻有真实毛孔、黑或深棕的眼睛和头发"),
    ("东方", "2d"): ("东亚人的轮廓用日漫画法表现——小脸尖下巴，眼睛大而长、眼尾微挑，"
                     "眼窝浅所以上眼睑只一条干净的线，鼻根低、鼻子只一小笔，嘴小；"
                     "黑或深棕的眼睛和头发，肤色平涂通透，脸颊一小块柔和腮红"),
    ("东方", "3d"): ("东亚人的骨相做成 CG 角色——小脸尖下巴，眼睛偏大偏亮、有清晰的高光点，"
                     "眼窝浅、鼻根低而鼻梁挺，面部转折柔和；"
                     "黑或深棕的眼睛和头发，皮肤像上了釉的瓷面，均匀光滑没有毛孔"),
}


# 小孩的骨相和大人是两回事：圆脸、婴儿肥、下颌线柔和、眼窝浅、眼睛大而圆。
# 成人那套（深眼窝、眉骨突出、颧骨明显、下颌线清晰、面部立体有骨感）钉在孩子头上，
# 脸会画成缩小版的成年人（P220）。
_RACE_LINES_CHILD = {
    # P351：网红档的小孩也要精致——干净的小脸、大而亮的眼睛、小巧的鼻子、有型的头发，不是"普通小孩"
    ("西方", "photo_glam"): ("欧洲白人小孩，像童装广告里的小模特——脸小而圆润、下颌柔和；眼睛大而亮、睫毛长，眉形清晰，鼻子小巧挺秀，唇小而饱满；"
                             "肤色白皙通透带粉，浅色瞳孔（灰、蓝、绿、浅褐），眉毛与头发同色；皮肤细腻有真实的细微毛孔和绒毛；头发梳理整齐、有型"),
    ("东方", "photo_glam"): ("东亚小孩，像童装广告里的小模特——脸小而圆润、下颌柔和；眼睛大而亮、眼窝浅、睫毛长，鼻子小巧挺秀，唇小而饱满；"
                             "皮肤白皙通透有血色、有真实的细微毛孔；黑或深棕的眼睛和头发，头发梳理整齐、有型"),
    ("西方", "photo_real"): ("欧洲白人小孩——脸圆、有婴儿肥，下颌线柔和不明显；"
                        "眼睛大而圆、眼窝比大人浅，眉骨平缓，鼻梁小、鼻头圆，唇小；"
                        "肤色白皙带粉或雀斑，浅色瞳孔（灰、蓝、绿、浅褐），眉毛与头发同色；"
                        "皮肤细腻、有绒毛和薄薄的红晕"),
    ("西方", "2d"): ("欧洲小孩用日漫画法——圆脸、下巴不尖，眼睛特别大而圆、占脸的比例比大人高，"
                     "眉骨和鼻梁只一两笔轻线，鼻子一小点，嘴小；"
                     "浅色瞳孔（灰、蓝、绿、浅褐），眉毛与头发同色；"
                     "肤色是平涂的通透白皙，脸颊两块柔和腮红"),
    ("西方", "3d"): ("欧洲小孩做成 CG 角色——大圆脸小下巴，眼睛大而亮、有清晰的高光点，"
                     "面块转折圆润没有棱角，鼻头圆、唇小；"
                     "浅色瞳孔（灰、蓝、绿、浅褐），眉毛与头发同色；"
                     "皮肤像上了釉的瓷面，均匀光滑带粉嫩过渡"),
    ("东方", "photo_real"): ("东亚小孩——脸圆、有婴儿肥，下颌线柔和不明显；"
                        "眼睛大而圆、眼窝浅，鼻根低、鼻头圆，唇小；"
                        "皮肤白皙细腻、有绒毛和薄薄的红晕，黑或深棕的眼睛和头发"),
    ("东方", "2d"): ("东亚小孩用日漫画法——圆脸，眼睛特别大而圆、占脸的比例比大人高，"
                     "上眼睑只一条干净的线，鼻子一小点，嘴小；"
                     "黑或深棕的眼睛和头发，肤色平涂通透，脸颊两块柔和腮红"),
    ("东方", "3d"): ("东亚小孩做成 CG 角色——大圆脸小下巴，眼睛大而亮、有清晰的高光点，"
                     "面块转折圆润，鼻根低、鼻头圆；"
                     "黑或深棕的眼睛和头发，皮肤像上了釉的瓷面，均匀光滑"),
}

# 「，像欧美模特或女演员——」这种成人参照，未成年不能用
_ADULT_REF = re.compile(r"，像[^—]{0,12}——")


def card_age_num(card):
    """共享解析明确年龄；不猜数字，不改卡片。"""
    return age_number((card or {}).get("age"))

def face_bucket(settings):
    """写脸用哪一套语言。photo 再分两档（P224，用户 2026-09-09 定）：

      电影级写实 ＝ 极度写实，不美化五官和身材   → photo_real
      网红自然感 ＝ 美化五官和身材，像网红        → photo_glam

    以前两档共用同一段「像欧美模特或女演员」，等于给写实档也下了美化指令，
    四画风对比里这两张分不出来（人物和场景两边都是）。
    二维三维不受影响，照旧按 kind 走。
    """
    k = style_kind(settings)
    if k != "photo":
        return k
    return "photo_glam" if str((settings or {}).get("style") or "").strip() == "网红自然感" else "photo_real"


def _race_line(settings, card=None):
    """世界类型 → 默认人种（用户 2026-09-04 定：东方＝东亚亚洲脸，西方＝欧洲白人脸）。

    判断题给代码：不靠 Qwen 转述，直接写进硬事实段。
    同一件人种事实**按画风的语言说**——照片语言写进动漫和 CG 里，
    会把不同画风拉成同一张写实脸（P216 四画风实测）。
    再**按年龄说**——成人骨相钉在孩子头上，脸会画成缩小版的成年人（P220）。
    """
    w = str((settings or {}).get("world_type") or "")
    side = "东方" if w.startswith("东方") else ("西方" if w.startswith("西方") else "")
    if not side:
        return ""
    kind = face_bucket(settings)
    a = card_age_num(card)
    if a is not None and a < 13:
        txt = _RACE_LINES_CHILD.get((side, kind)) or _RACE_LINES_CHILD[(side, "photo_real")]
        return "人种与五官：" + txt
    txt = _RACE_LINES.get((side, kind)) or _RACE_LINES[(side, "photo_real")]
    if a is not None and a < 18:
        txt = _ADULT_REF.sub("——", txt)      # 十几岁不写「像欧美模特或女演员」
    elif card_sex(card) == "男":
        txt = txt.replace("女演员", "男演员").replace("东亚偶像", "东亚男偶像")      # P316：男角色不照女演员画
    return "人种与五官：" + txt


# 设计师那段自由文本里，「发型…」「服装…」各是一句。只认明确句式，抽不到就不抽（P208）
_LOOK_HAIR = re.compile(r"(?:发型和发色|发型|发色|头发)\s*(?:为|是|：|:)\s*([^。；\n]{2,60})")
# 设计师写服装的格式不固定：有时是「服装为：…」，有时直接「身穿…外罩…下身穿着…」。
# 靠前缀抽会整段漏掉（实测巴顿），所以改成按句子挑（P211）
_WEAR_VERB = re.compile(r"身穿|身着|穿着|外罩|内穿|脚穿|脚踏|系着|佩戴|戴着|腰间|下身|上身")


def wear_sentences(text):
    """从设计师那段里挑出所有讲穿着的句子，拼成一段（P211）。

    「神态亲切、眼神温和」这种没有衣物词也没有穿着动词的句子不会被收进来。
    """
    out = []
    for sent in re.split(r"[。；\n]+", str(text or "")):
        t = sent.strip()
        if not t:
            continue
        if _WEAR_VERB.search(t) or any(re.search(p, t) for p in _WEAR_KINDS):
            out.append(t)
    # 设计师那段有时写得很长。服装栏会被带进每一张图的提示词，
    # 撑到几百字就把真正要紧的信息挤掉了——按句子边界截到 220 字（P212）
    kept, total = [], 0
    for t in out:
        if kept and total + len(t) > 220:
            break
        kept.append(t)
        total += len(t)
    return "；".join(kept)


# 一整套衣服至少要能看出两类：上装、下装、鞋、外层、配件。
# 按类别判比按字数判稳——「深靛蓝色长袍配深棕皮靴」只有十几个字，但它是完整的；
# 「沾着面粉的围裙」六个字，只有配件一类，是不完整的（P209 实测）。
_WEAR_KINDS = (r"衬衫|上衣|外套|袍|罩衫|背心|马甲|毛衣|T恤|长衫|短打",
               r"裤|(?<!围)裙|下装|百褶",
               r"靴|鞋|草鞋|木屐|凉鞋",
               r"斗篷|披风|大衣|风衣|外袍|铠甲|甲胄",
               r"围裙|腰带|束腰|头巾|帽|手套|护腕|项链|吊坠|耳钉|耳环")
# 识别特征：一眼认人的东西
_LOOK_MARK = re.compile(r"([^。；\n]{0,40}(?:佩戴|戴着|挂着|别着|系着一枚|疤|痣|胎记|眼镜|独眼|眼罩)"
                        r"[^。；\n]{0,40})")


def wear_is_complete(text):
    """这段服装描述算不算一整套：至少能看出两类穿着（P209）。"""
    t = str(text or "")
    return sum(1 for pat in _WEAR_KINDS if re.search(pat, t)) >= 2


def fill_look_gaps(card, look):
    """卡上 hair/clothing 还空着的，用设计师写的那段补上。返回补了哪几栏（P208）。

    建卡时「只摘正文里有的」，正文常常不写发色和衣着，空栏本来就该由
    出图提示词那一步的设计师补。它确实写了（第二段里颜色齐全），
    只是一直没回填到卡上，而发给出图模型的硬事实是按卡上字段拼的——
    结果提示词里一个颜色都没有，人物画成没上色的白线稿（实测）。
    """
    filled = []
    text = str(look or "")
    if not text.strip():
        return filled
    if not str((card or {}).get("hair") or "").strip():
        m = _LOOK_HAIR.search(text)
        if m:
            card["hair"] = m.group(1).strip()
            filled.append("发型")
    # 空的、或者只有零星一件（"沾着面粉的围裙"）都算没有——一件围裙不是一整套衣服
    if not wear_is_complete((card or {}).get("clothing")):
        m = wear_sentences(text)
        if m and wear_is_complete(m):
            old_wear = str((card or {}).get("clothing") or "").strip()
            new_wear = m.strip()
            # 正文里刮到的那半句是真事（比如"沾着面粉的围裙"），并进来别丢。
            # 但设计师多半已经写过同一件东西（"外系一条棕色帆布围裙，前襟沾着面粉"），
            # 所以按**核心名词**（末尾那两个字：围裙/皮靴/长裤）去重，不做整句比对
            _head = re.sub(r"[^一-龥]", "", old_wear)[-2:]
            if old_wear and _head and _head not in new_wear:
                new_wear = new_wear.rstrip("。") + "；" + old_wear.rstrip("。")
            card["clothing"] = new_wear
            filled.append("服装")
    if not str((card or {}).get("appearance_details") or "").strip():
        m = _LOOK_MARK.search(text)
        if m:
            card["appearance_details"] = m.group(1).strip()
            filled.append("识别特征")
    return filled


def race_line(c):
    """这个人的种族外形特征。卡上 char_type 命中哪个种族就钉哪一条（P210）。

    实测同一个精灵，四种画风里两种被画成了人类——提示词里有没有「尖耳」
    取决于模型那次怎么措辞。而 style_presets.CHAR_TYPE_DEFS 里
    「精灵＝修长人形骨骼、明显尖耳、轻盈体态与细长手指」早就写好了，
    一直没接进来。接上之后换什么画风都不丢。
    """
    from . import style_presets as _sp
    defs = getattr(_sp, "CHAR_TYPE_DEFS", None) or {}
    txt = " ".join(str((c or {}).get(k) or "") for k in ("char_type", "identity", "identity_anchor"))
    if not txt.strip():
        return ""
    # 命中顺序：先长后短（免得「其他非人」被「人类」抢先），
    # 并且**非人类的种族优先**——「半精灵人类混血」两个词都命中时，
    # 「人类」是默认值，写了别的种族就说明这个人不是普通人类（P212）
    hits = [n for n in sorted(defs, key=len, reverse=True) if n in txt]
    if not hits:
        return ""
    name = next((n for n in hits if n != "人类"), hits[0])
    return "种族外形（照这个画，不能省）：%s——%s" % (name, str(defs[name]).rstrip("。"))


def _hard_facts(c, settings=None):
    """卡上写死、必须照画的东西，拼成一段。没有就空。"""
    bits = []
    # 性别不能再交给提示词模型自行发挥。项目 7 的张凡卡上是男，旧流程却在
    # 改写时漏掉性别，最后生成了女人。这里把年龄和性别作为最高优先级硬事实，
    # 直接追加到 Krea 收到的最终提示词，三个全身视图和特写都必须一致。
    if not is_nonhuman(c):
        identity = _gender_identity(c)
        if identity:
            bits.append("年龄与性别（最高优先级）：%s；三个全身视图和面部特写必须都是同一个%s，"
                        "全身骨骼、胸廓、腰胯、面部与身体性别特征始终按%s人体结构呈现"
                        % (identity, identity, "男性" if card_sex(c) == "男" else "女性"))
    rl = _race_line(settings, c)          # P220 按这个人多大说骨相
    if rl:
        bits.append(rl)
    rc = race_line(c)
    if rc:
        bits.append(rc)
    _ht = height_text(c)
    if _ht:
        bits.append("身高（独立硬约束）：" + _ht.rstrip("。"))
    if not is_nonhuman(c):
        _bt = body_text(c)
        if _bt:
            bits.append("身材（独立硬约束，不改变身高）：" + _bt.rstrip("。"))
    ap = str((c or {}).get("appearance_details") or "").strip()
    if ap:
        bits.append("瞳色、肤色和识别特征：" + ap.rstrip("。"))
    fl = face_line(c)
    if fl and not is_nonhuman(c):
        bits.append("脸部结构：" + fl.rstrip("。"))
    if not is_nonhuman(c):
        try:
            from . import style_presets as _sp
            _sex = card_sex(c) or "女"
            _face = _sp.normalize_face_option(_sex, (c or {}).get("face_type"))
            if _face not in ("", "自动", "None"):
                _fd = _sp.face_def(_sex, _face) or ""
                bits.append("五官骨相（最高优先级；如与其他外貌文字冲突，以本条为准）：%s%s" %
                            (_face, ("（" + _fd.rstrip("。") + "）") if _fd else ""))
        except Exception:
            pass
    hair = str((c or {}).get("hair") or "").strip()
    if hair:
        bits.append("发型和发色（最高优先级；不得改成常见默认发型）：" + hair.rstrip("。"))
    if is_nonhuman(c):
        bits.append(body_scale_line(c))
    # 露体尺度下再钉两句（2026-09-04 实测：Qwen 自己多画了一个「脱掉外层」的视图，乳头露了）
    cl = str((c or {}).get("clothing") or "")
    _nude = is_nude(c, settings)
    if _nude:
        # P359：成年、写明全裸 → 直接按全裸画，三视图和特写都是不穿衣服的身体
        if card_sex(c) == "男":
            bits.append("这个成年男性全身赤裸：三个全身视图和面部特写都是同一个成年男性，平坦男性胸膛、腰腹、臀部和男性下体完整可见，"
                        "肩胸腰胯与身体结构始终保持男性特征；皮肤是真实的皮肤质感，身上只保留卡上写明的固定饰物（如有）")
        elif card_sex(c) == "女":
            bits.append("这个成年女性全身赤裸：三个全身视图和面部特写都是同一个成年女性，女性胸部、腰腹、臀部和女性下体完整可见，"
                        "肩胸腰胯与身体结构始终保持女性特征；皮肤是真实的皮肤质感，身上只保留卡上写明的固定饰物（如有）")
        else:
            bits.append("这个成年人物全身赤裸：三个全身视图和面部特写里都不穿任何衣物，胸、腰腹、臀部和下体完整可见，"
                        "皮肤是真实的皮肤质感；身上只保留卡上写明的固定饰物（如有）")
        cl = ""
    if cl:
        bits.append("这一身衣服：" + cl.rstrip("。"))
    if not hair and not cl:
        # 发色和服装两栏都空：把设计师写的那整段附上。不解析就没有误判，
        # 宁可长一点，也不能让提示词里一个颜色都没有（P208 实测：白线稿）
        lf = str((c or {}).get("look_full") or "").strip()
        if lf:
            bits.append("这个人固定的长相和这一身衣服：" + lf.rstrip("。"))
    # 特殊内容已由共享规则检查；这里仅保留旧编译器的表现格式。
    # 这里再挡一次——两道独立的判断，改坏一处不至于漏。
    if cl and not is_minor(c) and not _nude and any(w in cl for w in ("丁字裤", "遮片", "布片", "不穿内裤", "连体", "前片", "裆片")):
        if card_sex(c) == "男":
            # P360：男性也进这一档了——遮盖句按性别措辞
            bits.append("画面里只有三个全身视图和一个面部特写，四处穿的都是上面这一整套完整服装，"
                        "胸腹和后背裸露，性器官和肛门始终被衣服遮住")
        else:
            bits.append("画面里只有三个全身视图和一个面部特写，四处穿的都是上面这一整套完整服装，"
                        "乳房大面积裸露但乳头被服装本身盖住，皮肤上没有乳贴，性器官和肛门始终被衣服遮住")
    if not bits:
        return ""
    return ("【以下是这个人固定不变的样子，三个视图和特写都照这个画】\n" +
            "。\n".join(bits) + "。")


_LT_TIME = ("白天", "正午", "清晨", "早晨", "上午", "下午", "黄昏", "傍晚", "夜晚", "深夜", "午夜", "夜里")
_LT_SKY = ("晴朗", "晴天", "大晴", "阴天", "多云", "下雨", "雨天", "雪天", "下雪", "雾", "蓝天白云")


def _look_time_light(settings):
    """从【画面观感】里取用户写死的时间和天气，拼成一句钉在提示词末尾。"""
    extra = str((settings or {}).get("extra_requirements") or "") + str((settings or {}).get("custom_guidance") or "")
    if not extra.strip():
        return ""
    t = [w for w in _LT_TIME if w in extra][:1]
    k = [w for w in _LT_SKY if w in extra][:1]
    if not (t or k):
        return ""
    when = "、".join(t + k)
    if any(w in extra for w in ("夜晚", "深夜", "午夜", "夜里")):
        return "背景的时间是%s：天光偏暗，靠环境灯与霓虹照明，天空是夜空。" % when
    return "背景的时间是%s：天是亮的，光源是自然天光，天空明亮，不要夜景不要霓虹夜色。" % when


# 比例、五官和服装以人物卡为准；年龄边界统一由 age_policy 判断。

def _gender_identity(card):
    """把人物卡年龄+性别编成不会含糊的一句身份；非人或未知性别返回空。"""
    if is_nonhuman(card):
        return ""
    sex = card_sex(card)
    if sex not in ("男", "女"):
        return ""
    age = card_age_num(card)
    label = "男性" if sex == "男" else "女性"
    raw = str((card or {}).get("age") or "").strip()
    return (raw + ("岁" if raw.isdigit() else "") + label) if raw else label


def _nude_rule(card):
    """全裸卡按性别给 Qwen 两套明确规则，避免男性卡被通用女性身体词带偏。"""
    sex = card_sex(card)
    if sex == "男":
        body = "这是成年男性：正面写平坦男性胸膛、腰腹和男性下体，侧面写男性胸廓、腰臀，背面写后背和臀部；肩胸腰胯与身体结构保持男性特征。"
    elif sex == "女":
        body = "这是成年女性：正面写女性胸部、腰腹和女性下体，侧面写女性胸廓、腰臀，背面写后背和臀部；肩胸腰胯与身体结构保持女性特征。"
    else:
        body = "正面写胸、腰腹和下体，侧面写胸侧、腰臀，背面写后背和臀部。"
    return ("这个人物全身赤裸：三个全身视图和面部特写都画同一个不穿任何衣物的身体。" + body +
            "正文里每个视图只写身体和皮肤，不出现任何衣物、布料、裙、衫、鞋袜；只保留固定饰物（发饰、耳饰、护腕之类）。")


def _gender_locked_look(text, card):
    """清掉外观回填里与卡片性别明确冲突的身体词，并把年龄性别放在最前面。"""
    t = _strip_neg(str(text or "")).strip()
    sex = card_sex(card)
    if sex == "男":
        t = re.sub(r"乳房(?:、?乳头)?", "平坦男性胸膛", t)
        t = re.sub(r"(?:大|小)?阴唇[^，。；\n]*", "男性下体", t)
        t = t.replace("阴户", "男性下体").replace("女性生殖器", "男性身体结构")
    elif sex == "女":
        t = re.sub(r"阴茎[^，。；\n]*", "女性下体", t)
        t = re.sub(r"睾丸[^，。；\n]*", "女性下体", t)
        t = t.replace("男性生殖器", "女性身体结构").replace("平坦男性胸膛", "女性胸廓")
    identity = _gender_identity(card)
    if identity:
        name = str((card or {}).get("name") or "这个人物").strip()
        t = "%s，%s。%s" % (name, identity, t)
    return re.sub(r"([，。；])\1+", r"\1", t).strip()


def nude_card(card):
    """P363：全裸出图时喂给 Qwen 的卡——服装换成「全身赤裸」，款式清空；其余原样。"""
    return dict(card, clothing="全身赤裸，不穿任何衣物，只保留固定饰物", clothing_requirement="", outfit_base="")


def write_char_sheet(sid, card, settings=None, extra_rules=""):
    """人物卡 → 指令词 → Qwen → 最终人设图提示词。

    程序只汇总人物卡事实，不再自行拼一篇最终提示词。比例、身材、五官、发型、
    服装和成人姿势一次性交给 Qwen；Qwen 负责去重、消除冲突并写成纯正向最终稿。
    本函数只做必要清理，不在 Qwen 结果后追加第二套长提示词。
    """
    from . import asset_core, prompt_writer as pw
    render_card = sanitize_card_fields(dict(card or {}))
    assert_allowed(cards=(render_card,), settings=settings, text=extra_rules, media="image_prompt")

    nude = is_nude(render_card, settings)
    qwen_card = nude_card(render_card) if nude else render_card
    sheet, _look = pw.write_character(
        _char_one_line(qwen_card, settings or {}),
        _scale_gate(settings or {}, render_card),
        extra_rules=str(extra_rules or "").strip())
    sheet = _strip_neg(sheet)
    assert_allowed(cards=(render_card,), settings=settings, text=sheet, media="image_prompt")
    saved = asset_core.get_asset(sid, "characters", card.get("character_id"))
    if saved:
        # 只更新人设图提示词；人物卡和 look_full 不动，避免影响故事与视频链。
        saved["image_prompt"] = sheet
        asset_core.save_asset(sid, "characters", saved)
    return sheet


# 批量出图不再根据全局观感重写已确认的人物卡。

def _gen_char_images(sid, cards, settings, on_step=None):
    """两阶段：先用 Qwen 批量写完所有人设图提示词，再切到 Krea 批量出图。
    避免每个角色都在 Qwen↔Krea 之间来回切模型（每次约 35 秒）。"""

    import os
    import shutil
    from . import asset_core
    from models import krea_client
    krea_dir = str(Path(__file__).resolve().parent.parent / "outputs" / "krea")
    os.makedirs(krea_dir, exist_ok=True)

    # P352：出图前先把发型设计好（没有分线/层次/编发/束法这些设计词的人才做；一次 Qwen 做全组）
    if any(not hair_is_designed(c.get("hair")) and not is_nonhuman(c) for c in cards):
        try:
            _rh = design_hair(sid, on_step=on_step, only_missing=True)
            if on_step and _rh.get("ok"):
                on_step("发型设计好了 %d 人" % _rh.get("ok", 0))
        except Exception as _hx:
            if on_step:
                on_step("发型设计没做成，按原卡出图：" + str(_hx)[:100])
        fresh = {c.get("character_id"): c for c in asset_core.list_assets(sid, "characters")}
        cards = [fresh.get(c.get("character_id"), c) for c in cards]
    # 【出图前先定脸】没定过脸的人先走一遍脸部设计（一次 Qwen 调用做全组，
    # 已定的锁住）。全部生成 / 补出人设图 / 重新生成设定图都经过这里，
    # 所以只挂这一处。做完把传进来的卡换成磁盘上的新卡，face_spec 才带得上。
    if any(not ((c.get("face_spec") or {}).get("face_shape")) for c in cards):
        try:
            r0 = design_faces(sid, on_step=on_step, only_missing=True)
            for m in (r0.get("fixes") or []):
                if on_step:
                    on_step("脸部设计修正：" + m)
        except Exception as e:
            # authoring 没有日志函数，进度回调就是它的日志
            if on_step:
                on_step("脸部设计没做成，按原卡出图：" + str(e)[:120])
        fresh = {c.get("character_id"): c for c in asset_core.list_assets(sid, "characters")}
        cards = [fresh.get(c.get("character_id"), c) for c in cards]

    # 【一个人失败不许连累全队】2026-08-26 实测：第5个人物写提示词时 Qwen 偶发超时，
    # 整个 _gen_char_images 抛出 → 阶段二一张图都没出，前4个写好的提示词全白费。
    # 现在每个人独立 try + 失败重试一次，谁挂了跳过谁，其余照出。
    failed = []

    # 阶段一：Qwen 写全部提示词（模型不切）
    jobs = []
    for c in cards:
        name = c.get("name")
        if on_step:
            on_step("写人设图提示词：" + str(name))
        sheet = None
        for attempt in (1, 2):
            try:
                # 一张卡未通过校验只影响该角色；原有年龄/内容校验照常执行。
                assert_allowed(cards=[c], settings=settings, media="image_prompt")
                manual = str(c.get("prompt_override") or "").strip()
                sheet = manual or write_char_sheet(sid, c, settings)
                break
            except Exception as ex:
                sheet = None
                if attempt == 2:
                    failed.append("%s(提示词:%s)" % (name, str(ex)[:40]))
                elif on_step:
                    on_step("重试提示词：" + str(name))
        if sheet:
            jobs.append((c, name, sheet))

    # 阶段二：切到 Krea 批量出图（第一次 generate 会停 Qwen）
    for c, name, sheet in jobs:
        if on_step:
            on_step("出人设图：" + str(name))
        try:
            res = krea_client.generate(sheet)
            # 【文件名必须带项目号】原来是 CHAR_<人名>.png，只有人名。
            # 两个项目里都有「艾拉」时，后出的把先出的物理覆盖掉，没有任何报错——
            # 实测修理店的 32 岁精灵店主被异世界项目的 6 岁小公主顶替，
            # 一路进了成片（P227）。人名重名很常见，这不是特例。
            dst = os.path.join(krea_dir, "%s_CHAR_%s.png" % (sid, name))
            shutil.copy2(res["output_path"], dst)
            v = asset_core.add_approved_visual(sid, "character_master", c["character_id"], 1, dst, prompt=sheet)
            asset_core.adopt(sid, v["visual_id"])
        except Exception as ex:
            failed.append("%s(出图:%s)" % (name, str(ex)[:40]))
            continue
        try:
            from . import gen_history
            gen_history.record("character", dst, prompt=sheet, source="人设",
                               story_id=sid, extra={"name": name,
                               "owner_kind": "character", "owner_id": c["character_id"]})
        except Exception:
            pass
    return {"ok": len(jobs) - len([f for f in failed if "出图" in f]), "failed": failed}


def gen_char_images(sid, only_missing=True, on_step=None, ep=None):
    """给这个项目的人物出人设图。only_missing=True 时只补还没有图的那些。
    （偶发失败后用它补图，不用把整个故事重跑一遍。）"""
    from . import story_core, asset_core
    st = story_core.get_story(sid) or {}
    if (st.get("settings") or {}).get("no_images"):
        if on_step:
            on_step("项目设置 no_images：跳过人设图")
        return {"ok": 0, "failed": [], "skipped": "no_images"}                  # P429：只做文字
    settings = st.get("settings") or {}
    from . import reference_ready
    have = reference_ready.adopted_paths(sid, "character")
    source = (reference_ready.required_characters(sid, ep) if ep is not None
              else asset_core.list_assets(sid, "characters"))
    cards = [c for c in source
             if not c.get("auto_incidental")
             and not (only_missing and c.get("character_id") in have)]
    if not cards:
        return {"ok": 0, "failed": [], "note": "所有人物都已经有设定图了"}
    r = _gen_char_images(sid, cards, settings, on_step) or {}
    r["total"] = len(cards)
    return r


# ---------- 编排 ----------


def pipeline_status(sid, ep=1):
    """这一话每一步做完没有——给「全部生成」判断该从哪儿接着跑。

    用户 2026-09-01 定：不管跑到哪一步，点「全部生成」就补齐到前五段视频。
    """
    from . import asset_core, story_core
    settings = (story_core.get_story(sid) or {}).get("settings") or {}
    try:
        _saga, e = _ep(sid, ep)
    except Exception:
        e = {}
    e = e or {}
    chars = [c for c in (asset_core.list_assets(sid, "characters") or [])
             if not c.get("auto_incidental")]
    scenes = asset_core.list_assets(sid, "scenes") or []

    # P335：有没有图按已采用的 visual 判（card.visuals 没人写，永远是空）
    _have = set()
    for v in (asset_core.list_assets(sid, "visuals") or []):
        if v.get("status") == "adopted":
            _have.add((str(v.get("kind") or "").split("_")[0], v.get("owner_id")))

    def _img(cards, kind, idf):
        return [c for c in cards if (c.get("visuals") or []) or (kind, c.get(idf)) in _have]

    script = str(e.get("pictures") or e.get("body") or "")
    _stale, _why = screenplay_stale(sid, ep)
    try:
        from . import saga_core as _sg
        _tl_stale = bool(e.get("timeline_stale")) or _sg.timeline_stale(sid, ep)
    except Exception:
        _tl_stale = bool(e.get("timeline_stale"))
    return {
        "script_stale": _stale, "script_stale_why": _why,          # P164②
        "timeline_stale": _tl_stale,
        "plan_problems": list(settings.get("plan_problems") or []),  # P164③
        "one_line": bool(str(settings.get("one_line") or "").strip()),
        "prose": len(str(e.get("prose") or "")),
        "characters": len(chars),
        "char_images": len(_img(chars, "character", "character_id")),
        "scenes": len(scenes),
        "scene_images": len(_img(scenes, "scene", "scene_id")),
        "script": len(script),
        "segments": total_segments(sid, ep) if script.strip() else 0,   # 和分镜页同一口径（按安排剪过）
    }

def refresh_upstream(sid, ep=1, on_step=None, log=None):
    """下游动手之前先把上游对齐（P166①）。返回 {"script": 有没有重出剧本, "shots": 分镜要不要重拆}。

    · 正文改过 → 重出剧本（旧剧本进 versions 存着，出新的成功了才顶上）
    · 剧本改过 → 只回报"分镜要重拆"，具体重拆由调用方在它自己的链路里做
    · 都没变 → 什么都不做，已有成果照旧复用
    """
    out = {"script": False, "shots": False}
    def _say(m):
        if isinstance(log, list):
            log.append(m)
        if on_step:
            on_step(m)
    try:
        _saga, e = _ep(sid, ep)
    except Exception:
        return out
    e = e or {}
    stale, why = screenplay_stale(sid, ep)
    if stale and str(e.get("prose") or "").strip():
        _say("正文改过了，先重出第%d话剧本（旧剧本留在历史版本里）" % ep)
        make_screenplay(sid, ep, on_step=on_step)
        out["script"] = True
    try:
        from . import saga_core as _sg
        _saga2, e2 = _ep(sid, ep)
        out["shots"] = bool(out["script"] or (e2 or {}).get("timeline_stale")
                            or _sg.timeline_stale(sid, ep))
    except Exception:
        out["shots"] = out["script"]
    if out["shots"] and not out["script"]:
        _say("剧本改过了，分镜要按新剧本重拆")
    return out


def run_all(sid, one_line, on_step=None, images=True):
    """全部生成：简介 → 原文(小说家) → 人设(设计师，若无预设) → 人设图。
    images=False＝「生成故事」按钮：到人设卡为止，不出图（2026-08-29 用户拆分）。"""
    from . import story_core, asset_core

    def step(m):
        if on_step:
            on_step(m)
    st = story_core.get_story(sid) or {}
    settings = st.get("settings") or {}
    settings["one_line"] = one_line
    st["settings"] = settings
    story_core.save_story(st)
    # 多话计划（设计 v2 第五节）：有计划表就第一话只认第一行，后面几行的事当禁词
    try:
        from . import story_plan as _spp
        _row_line = _spp.apply_plan_rows(settings, 1)
        if _row_line and _row_line.strip():
            one_line = _row_line
    except Exception:
        pass

    # 工作流1：预设了人物。但只有**真填过**的卡才算数——空的默认「新人物」卡
    # （用户点了＋添加却没填）不能当预设，否则会拿空卡写原文、出空人设图。
    def _filled(c):
        nm = str(c.get("name") or "").strip()
        if nm and not nm.startswith("新人物"):
            return True   # 改过名字 = 用户在管这张卡
        # 名字还是默认，但填了任一设计字段也算真填过（选了下拉也算）
        return any(str(c.get(k) or "").strip() for k in
                   ("appearance_details", "clothing", "build", "face_type",
                    "behavior_anchor", "personality", "char_type", "hair", "voice"))
    _all_chars = asset_core.list_assets(sid, "characters") or []
    preset = [c for c in _all_chars if _filled(c)] or None
    if preset is None:
        # 只有空的默认卡在挡路 → 删掉，让角色设计师从原文重新设计人物
        for c in _all_chars:
            try:
                asset_core.delete_asset(sid, "characters", c["character_id"])
            except Exception:
                pass

    _card_fail = []
    if preset:
        # 【工作流1：先扩写人物，完整了再进故事】用户 2026-08-27 定。
        # 手上只有：你选的下拉 + 一句话 + 世界/强度/画风（还没有正文）。
        # 这样故事才能围绕**完整的人物**来写，而不是围绕半张空卡。
        step("扩写人物设定")
        _cc = complete_cards(sid, preset, one_line, settings, on_step,
                             clue_label="一句话故事") or {}
        preset = _cc.get("cards") or preset
        _card_fail = _cc.get("failed") or []

    # 【正文先行，简介从正文提炼】2026-08-28 用户定。
    # 简介先行时，戏剧结构被 185 字简介掐死（一句话本身没冲突 → 简介没冲突 →
    # 正文忠实执行 → 整话零阻力零钩子，对照短剧行业标准逐条不达标）。
    # 倒过来：小说家按一句话直接创作（行业标准写在原文指令词里），
    # 简介再从成稿忠实提炼——续写第二话时它当前情也更准。
    step("小说家写第一话正文")
    if _story_v2():
        from . import story_plan as _sp
        _r2 = _sp.generate_episode(sid, one_line, settings, chars=preset, on_step=on_step, place="")
        prose = _r2["prose"]
        _update_ep1(sid, prose=prose, script_v2=_r2["script"], outline=_r2["outline"], review=_r2["review"],
                    elements=_r2["elements"], timeline_stale=True,
                    plan_version=int(settings.get("plan_version") or 0), prose_at=time.time())
    else:
        from . import universal_writer as _uw
        if _uw.enabled():
            gen_prose(sid, 1, on_step=on_step)
            prose = str((_ep(sid, 1)[1] or {}).get("prose") or "")
        else:
            prose = write_prose("", settings, characters=preset, one_line=one_line)
        # P165②：这条路以前不写版本号，于是从「生成故事」进来的项目，
        # 第二话续写判定「上一话是旧版本」，把上一话结尾整个丢掉。
        _update_ep1(sid, prose=prose, timeline_stale=True,
                    plan_version=int(settings.get("plan_version") or 0), prose_at=time.time())

    if _story_v2() and _r2.get("synopsis"):
        synopsis = _r2["synopsis"]                     # 给用户看的读者简介（按剧本写，400～700 字）
    else:
        step("从正文提炼第一话简介")
        synopsis = summarize_brief(prose, settings)
    _update_ep1(sid, brief=synopsis)

    if preset:
        cards = preset
    else:
        step("角色设计师设计人物")
        _designed = design_characters(prose, settings)
        _designed, _dropped = _ground_cards(_designed, one_line, settings, (_r2 or {}).get("elements") if _story_v2() else None)
        if _dropped:
            _dbg("人设卡落地：删掉一句话里没有的人", {"删": _dropped})
        cards = _create_cards(sid, _designed)

    img = (_gen_char_images(sid, cards, settings, on_step) or {}) if images else {}
    return {"synopsis_len": len(synopsis), "prose_len": len(prose),
            "characters": len(cards), "images_failed": img.get("failed") or [],
            "cards_failed": _card_fail}


def _ground_cards(designed, one_line, settings, elements=None):
    """设计师立的卡必须是一句话／结构表里有的人（P86）。返回 (保留的卡, 删掉的名字)。
    一句话里点名的人不到 2 个（用户没起名字）→ 不过滤，照旧。"""
    import re as _re
    rows = [str(r.get("one_line") or "") for r in ((settings or {}).get("plan_rows") or []) if isinstance(r, dict)]
    src = str(one_line or "") + "".join(rows)
    el = elements or {}
    src += "".join(str(el.get(k) or "") for k in ("主角", "同伴", "谁挡他", "人物", "人物关系"))
    names_in_src = set(_re.findall(r"[\u4e00-\u9fa5]{2,3}(?=（)", src)) | set(_re.findall(r"(?:大师兄|师妹|弟子|勇者|修女|法师|骑士|少年|少女|老板|店员|司机|队长)([\u4e00-\u9fa5]{2,3})", src))
    named = [c for c in (designed or []) if str(c.get("name") or "").strip()]
    # 一句话里到底有没有点名：设计出的卡里有 ≥2 张名字直接出现在原文里，才算"用户点了名"
    hits = [c for c in named if str(c.get("name")).strip().split("（")[0] in src]
    _hero_txt = str(el.get("主角") or "")
    def _is_hero(nm):
        return bool(nm) and (nm in _hero_txt or (not _hero_txt and src.find(nm) >= 0 and all(src.find(nm) <= src.find(o) for o in [str(x.get("name") or "").split("（")[0] for x in (designed or [])] if o and o in src)))
    if len(hits) < 2:
        for c in (designed or []):                          # 类型落地每张都做（P118）；不删卡
            _nm = str(c.get("name") or "").strip().split("（")[0]
            _ground_card_type(c, _nm, src, is_hero=_is_hero(_nm))
        return list(designed or []), []
    keep, dropped = [], []
    _oral = str((settings or {}).get("story_mode") or "").strip().lower() == "single"
    for c in (designed or []):
        nm = str(c.get("name") or "").strip()
        head = nm.split("（")[0]
        role = "".join(_re.findall(r"（([^）]*)）", nm)) + str(c.get("role") or c.get("identity") or "")
        # P454③：口述模式的卡是设计师照正文立的，正文里没名字只有身份（精灵队长、白肤女精灵）——卡名的两字片段在正文里有就留；
        # 原来按整名筛把队长和围观精灵全删了，导演只剩两张卡，只能拿矮个子女精灵顶替所有人（项目1）
        _frag_hit = _oral and any((head[i:i + 2] in src) for i in range(len(head) - 1) if not _re.search(r"[的之个]", head[i:i + 2]))
        if (head and head in src) or _frag_hit or any(w in src for w in _re.findall(r"[\u4e00-\u9fa5]{2,3}", role) if w):
            _ground_card_type(c, head, src, is_hero=_is_hero(head))
            keep.append(c)
        else:
            dropped.append(nm)
    return keep, dropped


def _ground_card_type(card, name, src, is_hero=False):
    """人物类型没依据 → 换成一句话里紧挨名字的身份词（P90）。原地改卡。"""
    import re as _re
    ct = str(card.get("char_type") or card.get("identity") or "").strip()      # 设计师出的卡用 identity 键（P121）
    if _re.fullmatch(r"(?:女|男|老|小)?(?:服务生|侍应|摊主|邻人|长老|弟子|女孩|少年|少女|掌柜|店员|老板|司机|队长|守卫|人牙子|村民|路人|新妻|新妇)", str(name or "")):
        return                                             # 卡名本身就是身份，类型不动（P118b）
    _cn = _re.sub(r"[^\u4e00-\u9fa5]", "", ct)
    words = [_cn[i:i + 2] for i in range(len(_cn) - 1) if not _re.search(r"[的之]", _cn[i:i + 2])]
    if ct and words and sum(1 for w in words if w in src) * 2 >= len(words):     # 二字块一半以上有依据才算有依据
        return
    role = ""
    if name:
        m = _re.search(r"([\u4e00-\u9fa5]{1,3}的(?:丈夫|妻子|女儿|儿子|父亲|母亲|师兄|师妹|弟子|徒弟))" + _re.escape(name), src) \
            or _re.search(r"((?:大师兄|二师兄|师兄|师姐|师妹|长老|弟子|勇者|修女|武斗家|法师|骑士|少年|少女|外卖员|店员|司机|保安|警察|队长|镖师|拾荒少女))" + _re.escape(name), src) \
            or _re.search(_re.escape(name) + r"（([^）]{1,8})）", src)
        if m:
            role = m.group(1)
        elif name in src and is_hero:
            # 主角：名字前没有紧挨的身份词就取一句话里最靠前的身份词（「贵族少年到酒馆…小豪调戏」→ 贵族少年）
            m2 = _re.search(r"((?:贵族|落魄|年轻|见习|独眼)?(?:少年|少女|剑客|佣兵|女巫|法师|骑士|勇者|修女|武斗家|外卖员|店员|司机|保安|警察|队长|镖师|拾荒少女|大师兄|师兄|师妹|弟子|长老))", src)
            if m2 and _re.search(r"[人客家者员师生手]$|少年|少女", m2.group(1)):
                role = m2.group(1)
    if ct != role:
        card["_char_type_was"] = ct
        card["char_type"] = role
        if "identity" in card:
            card["identity"] = role                        # _create_cards 会把 identity 映射成 char_type（P121）
        for k in ("identity_anchor",):
            v = str(card.get(k) or "")
            if ct and ct in v:
                card[k] = v.replace(ct, role or "（按一句话）")


def extract_cards(sid, on_step=None):
    """【自定义作品用】只读第一话原文，把人物立成卡——不写正文、不出图。

    2026-08-29 用户定：自带成熟故事时，需要一个"只建花名册"的入口。
    人物卡不只是挂图的钩子，还管台词归属（名字）和音频块的声线，所以
    上传自己的人设图之前必须先有卡。

    已存在的同名卡**原样不动**（用户可能已经挂好图/改过声线），只补缺的；
    重复点不会重建、不会覆盖。返回 {"added": [...], "kept": [...]}。
    """
    from . import story_core, asset_core
    _saga, e = _ep1(sid)
    prose = str((e or {}).get("prose") or "").strip()
    if not prose:
        raise RuntimeError("第一话还没有原文，先把你的故事填进原文框")
    settings = (story_core.get_story(sid) or {}).get("settings") or {}
    have = {str(c.get("name") or "").strip(): c
            for c in asset_core.list_assets(sid, "characters")
            if str(c.get("name") or "").strip()}
    if on_step:
        on_step("从原文里提取人物和场景")
    # 【两步提取，2026-09-01 用户定】
    #   ① 只摘正文里已经写了的，没写的空着——不补不编
    #   ② 空的那几栏交给设计师（出图提示词那一步）去补
    # 原来走 design_characters：一次调用又认人又设计，正文没写的它大段补写，
    # 而且**不出场景**——场景图没有数据来源，出来全是纯白影棚底
    # （用户实测：视频背景是白的、人都站着）。
    cards = _extract_cards_raw(prose)
    _scene_cards = [c for c in (asset_core.list_assets(sid, "scenes") or []) if str(c.get("name") or "").strip()]
    have_s = {str(c.get("name") or "").strip() for c in _scene_cards}
    # 【P265 建卡按安排】有已确认的本话安排时，卡只按安排建：
    #   · 人物：名字不在安排人物表（∪ plan_people）里的不建——提取器按原文叫法出「灰狸花猫」「奶猫」，
    #     表上是「流浪猫」，建了也绑不上，还让画面稿里同一只猫两个名字（STORY_164 实测）。
    #   · 场景：提取器出的地点名先归到安排地点表（place_in_table，和清单/出片前门同一口径）；
    #     归不到的（「后巷」）不建；归到的名字已有卡就只补空字段，没有才建——
    #     原来「面包店」和「面包店内」各建一张，提示词随机绑一张，空间进进出出。
    #   人物表/地点表都空时不过滤（和 cast_cards 的退路一致：没表就没法按表建）。
    # 没有安排：一字不改走老路（老项目/自定义作品不受影响）。
    _arr = confirmed_arrangement(sid)
    _allow, _places, skipped, merged = set(), [], [], []
    if _arr:
        from . import shotlist as _slm
        _allow = {str(p.get("name") or "").strip()
                  for p in (((_arr.get("items") or {}).get("who") or {}).get("people") or []) if isinstance(p, dict)}
        _allow |= {str(p.get("name") or "").strip()
                   for p in (settings.get("plan_people") or []) if isinstance(p, dict)}
        _allow = {x for x in _allow if x}
        _places = list(_slm._place_names((((_arr.get("items") or {}).get("where") or {}).get("places") or [])))
        if not _places:
            _places = list(plan_places(sid, 1) or [])
    _SCENE_F = (("space", "空间"), ("light", "光线"), ("ground", "地面墙面"),
                ("furniture", "陈设"), ("landmarks", "标志物"))
    fresh, scenes_added = [], []
    _PRON = ("你", "我", "他", "她", "它", "您", "你们", "我们", "他们", "她们")
    # 只给需要固定长相的主要人物建卡。模型会把司仪、某长老、为首弟子
    # 也列成人物；这些临时角色不在用户构想里，建卡会多出一批人设图并
    # 抢占视频参考位。用户构想明确写到的身份仍保留。
    _user_people_src = (str(settings.get("one_line") or "") + " "
                        + str((e or {}).get("brief") or ""))
    _INCIDENTAL_PERSON = re.compile(
        r"^(?:司仪|旁白|路人|宾客|观众|众人|一名.+|某.+|为首.+|执剑长老|"
        r"[甲乙丙丁]|(?:普通|年轻|年长)?(?:弟子|长老|守卫|侍卫|士兵))$")
    for kind, name, f in cards:
        if kind == "人物" and (str(name).strip() in _PRON or len(str(name).strip()) < 2):
            # P322⑦：第二人称正文抽出「你」「她」当名字 → 绑定行「<Subject1>你是…」没法认人。改成身份名/性别名
            _role = str(f.get("身份") or "").strip()
            _sexw = "女" if "女" in str(f.get("性别") or "") else ("男" if "男" in str(f.get("性别") or "") else "")
            _new = _role[:6] if _role else ((_sexw + "主") if _sexw else "")
            if not _new or any(str(n2).strip() == _new for _k2, n2, _f2 in cards if _k2 == "人物" and n2 != name):
                _new = (_new or "人物") + str(len(fresh) + 1)
            _dbg("人物代词名改身份名", {"原": name, "新": _new})
            f = dict(f, 别称=str(name))
            name = _new
        one = card_one_line(kind, name, f)
        if kind == "人物":
            _canonical = next((str(n2).strip() for k2, n2, _f2 in cards
                               if k2 == "人物" and str(n2).strip() != str(name).strip()
                               and str(name).strip().endswith(str(n2).strip())
                               and str(n2).strip() in _user_people_src), "")
            if _canonical:
                skipped.append({"kind": "人物", "name": name,
                                "why": "与用户构想中的“%s”是同一人" % _canonical})
                continue
            _auto_incidental = bool(_INCIDENTAL_PERSON.match(str(name).strip())
                                    and str(name).strip() not in _user_people_src)
            if _arr and _allow and name not in _allow:
                skipped.append({"kind": "人物", "name": name, "why": "不在本话安排的人物表里"})
                continue
            if name in have:
                continue
            # 正文里没写身份时，用人物表那一行——「精灵女魔法师/店主」这类信息
            # 本来就在 plan_people 里，只是一直没接上，出图那一步不知道她是精灵（P207）
            _role = next((str(p.get("role") or "").strip()
                          for p in (settings.get("plan_people") or [])
                          if str((p or {}).get("name") or "").strip() == name), "")
            _ctype = f.get("身份", "") or _role
            fresh.append({
                "name": name, "age": f.get("年龄", ""), "sex": f.get("性别", ""),
                "char_type": _ctype, "build": f.get("身形", ""),
                "hair": f.get("头发", ""), "clothing": f.get("衣着", ""),
                # 标志物（独眼/眼罩/巨剑/烙印…）并进外貌细节——人设图和视频提示词都从这取
                "appearance_details": "；".join(x for x in (f.get("五官特征", ""),
                                                         f.get("标志物", "")) if x),
                "voice": f.get("声音", ""),
                "personality": f.get("动作习惯", ""),
                "identity_anchor": one,
                "auto_incidental": _auto_incidental})
        elif _arr and _places:
            hit = _slm.place_in_table(name, _places)
            if not hit:
                skipped.append({"kind": "场景", "name": name, "why": "归不到本话安排的地点表"})
                continue
            _one = card_one_line(kind, hit, f) if hit != name else one
            _card = next((c for c in _scene_cards if str(c.get("name") or "").strip() == hit), None)
            if _card is not None:
                # 已有卡只补空栏：用户可能已经改过/挂过图，写了的一个字不动；
                # 一栏都不用补就不落盘（连点两次 updated 不变、不回建）
                _filled = []
                for k, zh in _SCENE_F:
                    v = str(f.get(zh, "") or "").strip()
                    if v and not str(_card.get(k) or "").strip():
                        _card[k] = v
                        _filled.append(k)
                if not str(_card.get("contract_text") or "").strip():
                    _card["contract_text"] = _one
                    _filled.append("contract_text")
                if _filled:
                    _card["updated"] = time.time()
                    asset_core.save_asset(sid, "scenes", _card)
                merged.append({"from": name, "to": hit, "filled": _filled})
                continue
            _new = asset_core.create_scene(sid, {
                "name": hit, "space": f.get("空间", ""),
                "light": f.get("光线", ""), "ground": f.get("地面墙面", ""),
                "furniture": f.get("陈设", ""),
                "landmarks": f.get("标志物", ""),
                "contract_text": _one})
            if _new:
                _scene_cards.append(_new)            # 同一次里再归到这个名字的只补栏，不再建第二张
            have_s.add(hit)
            scenes_added.append(hit)
            if hit != name:
                merged.append({"from": name, "to": hit, "filled": ["new"]})
        elif name not in have_s:
            asset_core.create_scene(sid, {
                "name": name, "space": f.get("空间", ""),
                "light": f.get("光线", ""), "ground": f.get("地面墙面", ""),
                "furniture": f.get("陈设", ""),
                "landmarks": f.get("标志物", ""),
                "contract_text": one})
            scenes_added.append(name)
    _src = str(settings.get("one_line") or "") + " " + prose[:1500]
    for _c in fresh:
        _c["age"] = guess_age(_c, _src)
    added = [c["name"] for c in _create_cards(sid, fresh)
             if isinstance(c, dict) and c.get("name")] if fresh else []
    _dbg("建卡完成", {"人物": added, "场景": scenes_added, "跳过": skipped, "归并": merged,
                      "按安排": bool(_arr), "人物表": sorted(_allow), "地点表": _places})
    return {"added": added, "kept": sorted(have.keys()),
            "scenes": scenes_added, "total": len(have) + len(added),
            "skipped": skipped, "merged": merged}


def _extract_cards_raw(prose):
    """正文 → [(类型, 名字, {栏:值})]。**只摘正文里有的，没有的空着。**

    说话的人一个都不能少——模型每次输出的人物数不一样
    （同一段正文一次出 5 张卡、一次出 4 张，摊主整张丢了），用台词表兜底。
    """
    src = str(prose or "")
    if len(src) < 50:
        return []
    raw = _q(_ins("正文提取人物场景_指令词.txt"),
             "【故事正文】\n" + src, mt=2600, temperature=0.2)
    cards, cur = [], None
    for line in str(raw or "").splitlines():
        m = re.match(r"^\s*(人物|场景)\s*[：:]\s*(.+?)\s*$", line)
        if m:
            nm = re.sub(r"[《》「」\"]|[（(][^）)]*[）)]", "", m.group(2)).strip()
            cur = [m.group(1), nm, {}]
            cards.append(cur)
            continue
        m2 = re.match(r"^\s*([\u4e00-\u9fa5]{2,5})\s*[：:]\s*(.+?)\s*$", line)
        if m2 and cur is not None:
            v = m2.group(2).strip()
            if v and not _CARD_EMPTY.search(v):
                # 年龄那一栏只要结论——实测模型把推理过程也写进来了
                # （"至少七十岁（如果三十年前…那他现在得有一百二十岁）"）
                if m2.group(1) == "年龄":
                    v = re.split(r"[（(]", v)[0].strip()[:10]
                cur[2][m2.group(1)] = v
    have = {n for k, n, _ in cards if k == "人物"}
    for w, _q2 in extract_dialogue(src):
        w = str(w or "").strip()
        if w and not w.startswith(("画外音", "异声")) and w not in have:
            cards.insert(sum(1 for k, _, _ in cards if k == "人物"),
                         ["人物", w, {}])
            have.add(w)
    _dbg("提取卡片", {"人物": [n for k, n, _ in cards if k == "人物"],
                      "场景": [n for k, n, _ in cards if k == "场景"]})
    return cards


def regenerate_story(sid, on_step=None):
    """按设定重新生成故事：用已定人设+简介，重写一个全新第一话正文。"""
    from . import universal_writer
    if universal_writer.enabled() and not _story_v2():
        return gen_prose(sid, 1, on_step)
    from . import story_core, asset_core
    st = story_core.get_story(sid) or {}
    settings = st.get("settings") or {}
    _saga, e = _ep1(sid)
    synopsis = (e or {}).get("brief") or ""
    chars = asset_core.list_assets(sid, "characters")
    if on_step:
        on_step("按人设+简介重写第一话正文")
    if _story_v2():
        from . import story_plan as _sp
        _pl = ""
        try:
            _pl = _cards_digest(sid)
            _pl = _pl[_pl.find("【场景卡】"):] if "【场景卡】" in _pl else ""
        except Exception:
            pass
        _r2 = _sp.generate_episode(sid, settings.get("one_line") or synopsis, settings, chars=chars, on_step=on_step, place=_pl)
        _update_ep1(sid, prose=_r2["prose"], script_v2=_r2["script"], outline=_r2["outline"], review=_r2["review"],
                    elements=_r2["elements"], timeline_stale=True, **({"brief": _r2["synopsis"]} if _r2.get("synopsis") else {}))
        return {"prose_len": len(_r2["prose"]), "v2": True}
    prose = write_prose(synopsis, settings, characters=chars,
                        one_line=settings.get("one_line") or "")
    prose, _nfb2 = clean_prose_fallback(prose)
    _update_ep1(sid, prose=prose)
    return {"prose_len": len(prose)}



# ════════ 新链路：正文 → 画面稿（2026-09-01 上线）════════
# 四段视频实测，B 版（严格照 WorkFisher 文戏模板）胜出，链路从五层缩到三层。
# 旧的 make_screenplay（正文→剧本→导演分镜）保留不删，
# 环境变量 V41_PIPELINE=old 可切回去。

def _story_v2():
    """故事层 v2（规划前置、直接写剧本）。默认开；V41_STORY_V2=0 切回小说→画面稿老路。"""
    import os
    # P132（2026-09-07 用户拍板）：默认走 简介→小说体正文→改剧本 老路；V41_STORY_V2=1 才开 v2
    return str(os.environ.get("V41_STORY_V2", "0")).strip() in ("1", "true", "on")


def use_new_pipeline():
    """走不走新链路。默认走，V41_PIPELINE=old 切回旧的。"""
    import os
    return str(os.environ.get("V41_PIPELINE") or "new").strip().lower() != "old"


UNKNOWN_SPEAKER = "说话人待定"


def fix_paraphrased_dialogue(pics, prose, people=()):
    """P323：画面稿里被改写过的台词行换回原文那一句（清单里最像的那句，相似度 ≥0.55 才换；同一句只用一次）。返回 (新稿, 换了几句)。"""
    import difflib
    from . import dialogue_ledger as _dl, shotlist as _slm
    led = [e for e in _dl.ledger(prose, people) if e["kind"] in (_dl.KIND_SPOKEN, _dl.KIND_RECALLED)]
    if not led:
        return pics, 0
    used, n, out = set(), 0, []
    for ln in str(pics or "").split("\n"):
        m = _slm._PIC_LINE_RE.match(ln.strip())
        if not m:
            out.append(ln)
            continue
        q = m.group(3).strip()
        if any(_slm._same_line(q, e["text"]) for e in led):
            out.append(ln)
            continue
        best, best_r = None, 0.0
        for k, e in enumerate(led):
            if k in used:
                continue
            r = difflib.SequenceMatcher(None, _dl._n(q), e["norm"]).ratio()
            if r > best_r:
                best, best_r = k, r
        if best is not None and best_r >= 0.55:
            used.add(best)
            out.append(ln.replace(q, led[best]["text"]))
            n += 1
        else:
            out.append(ln)
    return "\n".join(out), n


def canonicalize_picture_speakers(pics, prose, people=()):
    """正文能确定说话人的台词，落盘前把画面稿说话人改回正文人物。

    只改逐字能对上的台词，按正文出现顺序逐条消费；正文判不出说话人的保持原样。
    """
    from . import dialogue_ledger as _dl
    led_all = _dl.spoken(_dl.ledger(prose, people))
    led = [e for e in led_all if e.get("speaker")]
    names = []
    for p in people or ():
        name = str((p or {}).get("name") or "").strip() if isinstance(p, dict) else str(p or "").strip()
        if name:
            names.append(name)
    aliases = {n: n for n in names}
    # 正文常把「青云掌门」简称为「掌门」。只收能唯一归属的职衔，避免多个长老互串。
    for suffix in ("掌门", "宗主", "门主", "族长"):
        owners = [n for n in names if n.endswith(suffix)]
        if len(owners) == 1:
            aliases[suffix] = owners[0]
    source = str(prose or "")

    def _speaker_near_quote(quote):
        """补清单判不准的三种常见小说写法：某人动作后开口、引号后某人起身续说、代词前已有说话主体。"""
        pos = source.find(str(quote or ""))
        if pos < 0:
            return "", False
        para = source[max(source.rfind("\n\n", 0, pos) + 2, 0):pos]
        direct = para[para.rfind("”") + 1:]
        # 引号里复述「小师叔问……」不是新的叙述主体，先删掉已经闭合的引语。
        para = re.sub(r"“[^”]*”", "", para)
        subject_verbs = r"(?:说道|问道|答道|说|问|答|喊|吼|开口|接过话头|出声|起身|沉声(?:说)?|低声(?:说)?|冷声(?:说)?|朗声(?:说)?|厉声(?:说)?)"
        direct_candidates = []
        for alias, canonical in aliases.items():
            for m0 in re.finditer(re.escape(alias) + r"[^。！？\n]{0,40}?" + subject_verbs, direct):
                direct_candidates.append((m0.start(), canonical))
        if direct_candidates:
            return max(direct_candidates)[1], True
        # 「陆雪琪微微颔首：」「张小凡抱拳，喉结微动：」：冒号前最后一个
        # 非宾语人物就是接下来这句的说话人；「她转向张小凡：」里的张小凡是宾语，排除。
        if re.search(r"[：:]\s*[“]?$", direct):
            direct_names = []
            for alias, canonical in aliases.items():
                for m0 in re.finditer(re.escape(alias), direct):
                    prefix = direct[max(0, m0.start() - 3):m0.start()]
                    if not re.search(r"转向|看向|望向|对着|面向", prefix):
                        direct_names.append((m0.start(), canonical))
            if direct_names:
                return max(direct_names)[1], True
        candidates = []
        for alias, canonical in aliases.items():
            for m0 in re.finditer(re.escape(alias) + r"[^。！？\n]{0,24}?" + subject_verbs, para):
                candidates.append((m0.start(), canonical))
        if candidates:
            return max(candidates)[1], False
        after = source[pos + len(str(quote or "")):pos + len(str(quote or "")) + 28]
        after = after.split("“", 1)[0]       # 下一句引语里提到的人，不是当前这句的说话人
        for alias, canonical in aliases.items():
            if re.search(re.escape(alias) + r"[^。！？\n]{0,10}?" + subject_verbs, after):
                return canonical, True
        return "", False
    used, out, changed = set(), [], 0
    for line in str(pics or "").splitlines():
        m = _PIC_DLG.match(line.strip())
        if not m:
            out.append(line)
            continue
        nq = _dl._n(m.group(3))
        hit = next((i for i, e in enumerate(led)
                    if i not in used and e.get("norm") == nq), None)
        inferred, strong = _speaker_near_quote(m.group(3))
        if hit is None and not inferred:
            out.append(line)
            continue
        if hit is not None:
            used.add(hit)
        known = str(led[hit].get("speaker") or "").strip() if hit is not None else ""
        want = inferred if (strong or not known) else known
        have = str(m.group(1) or "").strip()
        if want and want != have:
            indent = line[:len(line) - len(line.lstrip())]
            out.append("%s%s%s：%s" % (indent, want, m.group(2) or "", m.group(3)))
            changed += 1
        else:
            out.append(line)
    return "\n".join(out), changed


def beats_same_event(pairs):
    """P323：「安排写的是 A，镜头拍的是 B」字面对不上时，让模型判一次是不是同一件事（一次调用判全部）。返回 {索引: True/False}。"""
    if not pairs:
        return {}
    q = "\n".join("%d. 安排：%s ｜ 镜头：%s" % (i + 1, a, b) for i, (a, b) in enumerate(pairs))
    try:
        rep = _q("下面每一行是一段故事安排和实际拍出来的镜头。只判断镜头拍的是不是安排里那件事（换了说法、细节不同都算是；换了事件、漏了关键动作才算不是）。"
                 "只输出每行的编号和 是/否，如「1 是」，一行一个。", q, mt=200, temperature=0.1)
    except Exception:
        return {}
    out = {}
    for ln in str(rep or "").splitlines():
        m = re.match(r"^\s*(\d+)\s*[.、:：]?\s*(是|否)", ln.strip())
        if m:
            out[int(m.group(1)) - 1] = (m.group(2) == "是")
    return out


def _best_prose_span(want, prose, width=1):
    """原文里和这件事最相关的一句（二字重合最高）带前后各 width 句（P328①）。"""
    from . import shotlist as _slm
    sents = [x.strip() for x in re.split(r"(?<=[。！？!?])", str(prose or "")) if x.strip()]
    if not sents:
        return ""
    best, bi = -1.0, 0
    for i, s_ in enumerate(sents):
        v = _slm._overlap(want, s_)
        if v > best:
            best, bi = v, i
    lo, hi = max(0, bi - width), min(len(sents), bi + width + 1)
    return "".join(sents[lo:hi])[:500]


def write_beat_shots(stage, want, prose, pics, people=()):
    """剧本里「stage」这件事没有镜头 → 让模型照原文补 1~3 行画面稿（只写画面行，不编台词）（P328①）。返回行列表。"""
    from . import shotlist as _slm
    src = _best_prose_span(want, prose)
    sys_ = _ins("补镜头_指令词.txt")
    user = ("【要补的这件事】%s\n【原文里对应的那几句】\n%s\n【在场人物（只用这些名字）】%s\n【现在画面稿的最后几行（写法照这个）】\n%s"
            % (str(want or stage), src or "（原文里没找到对应句子，按这件事本身写）", "、".join(x for x in people if x) or "按原文", "\n".join(str(pics or "").strip().split("\n")[-6:])))
    raw = _q(sys_, user, mt=500, temperature=0.3)
    return filter_beat_lines(str(raw or "").splitlines(), prose, pics=pics)


def filter_beat_lines(lines, prose, cap=3, pics=""):
    """补镜头的行：去编号/标题/地点行；编出来的台词行（原文里没有这句）不要；画面稿里已经有的台词不重复补；最多 cap 行（P328）。"""
    from . import shotlist as _slm
    out = []
    quotes = set(_slm.prose_quotes(prose).keys())
    have = {_slm._n(q) for _w, q in _pic_parts_dialogue(pics)} if pics else set()
    for ln in lines or []:
        ln = re.sub(r"^\s*(\d+|[①②③④⑤⑥⑦⑧⑨])\s*[.、)）]?\s*", "", str(ln or "").strip())
        if not ln or ln.startswith("──") or ln.startswith("【"):
            continue
        m = _slm._PIC_LINE_RE.match(ln)
        if m and (_slm._n(m.group(3)) not in quotes or _slm._n(m.group(3)) in have):
            continue                                  # 编出来的台词行不要；画面稿里已有的那句也不再补一遍
        out.append(ln[:200])
        if len(out) >= cap:
            break
    return out


def insert_beat_lines(pics, lines, stage_index, arrangement):
    """把补的画面行插到「这件事该在的位置」：按段号归属，插在最后一个属于前面段的块后面（P328①）。
    返回 (新稿, 插在第几块之后（0 起）)。"""
    from . import arrangement as _arm
    lines = [str(x).strip() for x in (lines or []) if str(x).strip()]
    if not lines:
        return pics, -1
    blocks = str(pics or "").split("\n\n")
    try:
        bmap = _arm.beat_map(blocks, arrangement) if arrangement else [-1] * len(blocks)
    except Exception:
        bmap = [-1] * len(blocks)
    at = -1
    for k, bi in enumerate(bmap):
        if 0 <= bi < int(stage_index):
            at = k
    if at < 0:
        # 前面没有归属块：放在第一个地点行后面（有的话），否则放最前
        at = 0 if blocks and blocks[0].strip().startswith("──") else -1
    new = blocks[:at + 1] + ["\n\n".join(lines)] + blocks[at + 1:]
    return "\n\n".join(x for x in new if x is not None), at


def gate_chat_interpret(asks_text, pics_numbered, history, user_text):
    """规则解析不了的回答交给模型翻成动作 JSON（P328②）。返回 dict：{reply, actions:[…], unclear}；失败 {}。"""
    sys_ = _ins("出片前门_对话修复_指令词.txt")
    user = ("【问题清单】\n%s\n\n【画面稿（带行号）】\n%s\n\n【之前的对话】\n%s\n\n【用户这句话】%s"
            % (asks_text, pics_numbered[:6000], history or "（无）", user_text))
    try:
        raw = _q(sys_, user, mt=900, temperature=0.2)
    except Exception:
        return {}
    t = str(raw or "")
    i, k = t.find("{"), t.rfind("}")
    if i < 0 or k <= i:
        return {}
    try:
        d = json.loads(t[i:k + 1])
    except Exception:
        try:
            d = json.loads(re.sub(r",\s*([}\]])", r"\1", t[i:k + 1]))
        except Exception:
            return {}
    return d if isinstance(d, dict) else {}


def fix_missing_dialogue(pics, prose, people=()):
    """把画面稿里丢掉的台词按正文顺序补回去；把不该当台词的行删掉。确定性，不叫模型（P310 改用台词清单）。

    · 只补正文里**当前说出口**的话；正文说了几次就最多补到几次，不重复补入。
    · 说话人有明写依据才填；没有就写「说话人待定」，交给出片前门集中问，不借用上一句的人。
    · 没说出口的话（"那句没说出口的对不起"）、招牌/登记簿上的字、拟声，被画面稿写成台词行的，删掉。
    位置：正文里它前面最近的一句、且画面稿里有的台词，插在那句所在的那一块末尾；找不到就插到第一块末尾。
    返回 (新稿, 补了几句)；删掉的行数记在 fix_missing_dialogue.last_removed。
    """
    from . import dialogue_ledger as _dl
    pics = str(pics or "")

    def norm(q):
        return re.sub(r"[^\w一-龥]", "", str(q or ""))

    led = _dl.ledger(prose, people)
    # ① 删：没说出口/招牌/拟声 当成台词行的
    bad = {e["norm"] for e in _dl.not_dialogue(led)}
    spoken_norms = {e["norm"] for e in _dl.spoken(led)}
    removed = 0
    if bad:
        kept = []
        for ln in pics.split("\n"):
            m = _PIC_DLG.match(ln.strip())
            if m and norm(m.group(3)) in bad and norm(m.group(3)) not in spoken_norms:
                removed += 1
                continue
            kept.append(ln)
        if removed:
            pics = "\n".join(kept)
    fix_missing_dialogue.last_removed = removed
    # ② 补
    missing = picture_missing_dialogue(pics, prose, people)      # [(原话, 说话人, 确定/存疑, 次数)]
    if not missing:
        if removed:
            _dbg("画面稿删掉不该当台词的行", {"删了": removed})
        return pics, 0
    order = [e["norm"] for e in _dl.spoken(led)]                  # 正文里台词的先后顺序
    have = {norm(q) for _, q in _pic_parts_dialogue(pics)}
    blocks = pics.split("\n\n")
    added = 0
    for q, who, conf, _cnt in missing:
        nq = norm(q)
        line = "%s：%s" % ((who if (who and conf == "确定") else UNKNOWN_SPEAKER), str(q).strip())
        prev = None
        for x in order:
            if x == nq:
                break
            if x in have:
                prev = x
        target = 0
        if prev is not None:
            for k, b in enumerate(blocks):
                hit = [mm for l in b.splitlines() for mm in [_PIC_DLG.match(l.strip())]
                       if mm and norm(mm.group(3)) == prev]
                if hit:
                    target = k
                    break
        blocks[target] = blocks[target].rstrip("\n") + "\n" + line
        have.add(nq)
        added += 1
    _dbg("画面稿补回台词", {"补了": added, "删了": removed, "说话人待定": sum(1 for _, w, c, _x in missing if not (w and c == "确定"))})
    return "\n\n".join(blocks), added


fix_missing_dialogue.last_removed = 0


def literal_pictures_from_prose(prose, people=()):
    """模型把台词集中抄一遍、正文里又抄一遍时的零模型兜底。

    按正文原顺序把叙述和引号内容拆开；只有台词清单认定为 spoken 的才写成
    「人物：台词」，拟声/招牌仍作为画面叙述。未知说话人留给后面的专职核对。
    """
    from . import dialogue_ledger as _dl
    src = str(prose or "")
    led = {int(e.get("pos") or -1): e for e in _dl.ledger(src, people)}
    out, base = [], 0
    for para in re.split(r"(\n\s*\n)", src):
        if not para.strip() or re.fullmatch(r"\n\s*\n", para):
            base += len(para)
            continue
        lines, at = [], 0
        for m in re.finditer(r"[「“]([^」”\n]{1,200})[」”]", para):
            before = para[at:m.start()].strip()
            if before:
                lines.append(before)
            e = led.get(base + m.start()) or {}
            q = str(e.get("text") or m.group(1)).strip()
            if e.get("kind") == _dl.KIND_SPOKEN:
                lines.append("%s：%s" % (str(e.get("speaker") or UNKNOWN_SPEAKER), q))
            else:
                lines.append(q)
            at = m.end()
        tail = para[at:].strip()
        if tail:
            lines.append(tail)
        if lines:
            out.append("\n".join(lines))
        base += len(para)
    return "\n\n".join(out)


def repeated_prose_dialogue(pics, prose):
    """剧本比正文多抄了多少句完整台词；只数逐字相同，避免语义猜测。"""
    from . import dialogue_ledger as _dl
    total, seen = 0, set()
    for e in _dl.spoken(_dl.ledger(str(prose or ""))):
        q = str(e.get("text") or "").strip()
        if not q or q in seen:
            continue
        seen.add(q)
        total += max(0, str(pics or "").count(q) - int(e.get("count") or 1))
    return total


_PIC_DIRECTOR_SYS = """你是这部片子的导演，现在审一遍编剧交上来的【画面稿】。
逐幅对着下面的清单检查，缺什么就**在那一幅里补上**；已经写好的不要改写、不要删。

清单：
1. 动作写到演员照着能演：谁、做了什么、怎么做的（用哪只手、身体朝哪、力度快慢）、
   从哪里到哪里、当时的表情。缺哪样补哪样。
2. 空间进出写明：人从一个空间到另一个空间，那句要写"从哪、怎么进去、到了哪"
   （「从外面推开门跨进室内」）。一直在室内的不能写成室外。
3. 换到新空间的第一幅是定场：这空间多大、光从哪来什么颜色、主要物件各在哪、人在哪、隔多远。
4. 人和人、人和物的位置关系：站/坐/蹲、脸朝哪、隔几步、中间有什么挡着。
5. 身体里面的感觉（气流/血液/心跳/发热/刺痛）改成外面看得见的（印记亮起、青筋、汗、绷紧、发抖）。
6. 每幅都有光：从哪来、什么颜色、亮暗、照在谁身上哪一块。
7. 只有声音的句子改成**发出声音的东西正在做什么**：剑出鞘写剑刃一寸寸离开剑鞘、门轴响写门板在转、
   撕裂声写阴影里那块布被什么从里面撑开；描述嗓音的整段改成说话时喉部、嘴、下颌的动作。
8. 气味、温度改成人的可见反应：皱鼻、屏息、呼出白气、缩起肩膀、拉紧衣领。
9. 在场但这一阵没动作的人物（比如打戏时站在一旁的人），**每隔四五幅交代一次**：在哪、朝哪看、
   手在做什么、脸上什么反应——一句就够，让观众一直知道他在。

铁规：
· **每幅画面一个都不能动、不能少、不能合并**；
· **台词行（「名字：台词」）一字不改、一句不少、不许新加**；
· 不许添故事里没有的人物、事件、地点；
· 只补不删。

输出修改后的**完整画面稿**，格式和原来一样，不要说明。"""


def fix_sentences(text, items, context="", log=None):
    """指定句子 + 毛病，让模型逐条只回改好的一句；代码替换。守卫：原句逐字存在、引号台词不变、字数 0.5～3 倍。
    items: [(原句, 毛病说明)]。返回 (新文本, 换成功数)。"""
    t = str(text or "")
    items = [(a, b) for a, b in (items or []) if a and a in t]
    if not items:
        return t, 0
    sysmsg = ("你只做一件事：把用户给你的每一句话按后面括号里指出的毛病改写一遍，改完的句子要能被摄影机拍到。\n"
              "规则：① 每一条都必须和原句不一样；② 一句可以改成两三句，写成连着的一段，不分行；"
              "③ 句子里的人、动作、信息一个都不许少，不添新情节；④ 引号里的台词一个字不许改；"
              "⑤ 逐条回复，一行一条，格式「序号. 改好的句子」，不要解释、不要写原句。"
              + (("\n人物的身体结构和标志物按下面的人物卡写：\n" + context) if context else ""))
    user = "\n".join("%d. %s（毛病：%s）" % (i + 1, a, b) for i, (a, b) in enumerate(items))
    try:
        rep = _q(sysmsg, user, mt=1800, temperature=0.4)
    except Exception:
        return t, 0
    got = {}
    for line in str(rep or "").splitlines():
        m = re.match(r"^\s*(\d+)\s*[.、．)]\s*(.+?)\s*$", line)
        if m:
            got[int(m.group(1))] = re.sub(r"^【[^】]{1,8}】", "", m.group(2))
    ok = 0
    for i, (sent, why) in enumerate(items):
        new = got.get(i + 1, "")
        bad = ("没给" if not new else "抄回原句" if new.strip() == sent.strip()
               else "改了台词" if any(q not in new for q in _quotes_in(sent))
               else "长度离谱" if not (len(sent) * 0.5 <= len(new) <= max(len(sent) * 3.0, len(sent) + 80)) else "")
        if log is not None:
            log.append((why, sent, new, bad or "换掉"))
        if bad:
            continue
        t = t.replace(sent, new, 1)
        ok += 1
    return t, ok


_EXPAND_SYS = """你是编剧的助手。下面是几幅画面（编号），每幅给你原句。你**只补充**，不改原句：给每幅补 1～2 句，
写演员和布景本身：①用哪只手/哪条腿、身体朝哪、从哪到哪；②这一刻脸上的表情或呼吸；③人物身后是这个地方的哪样东西、光落在哪。
只写画面里已经有的人和东西；背景只用【这个地方有的东西】里列出来的；不加新人物、不加台词、不加新事件。
**补充里提到人，一律写【故事里的人】给的名字，不写"他""她""它"。**
写法是小说叙述句（"<名字>左手……，身后是……"），由摄影师决定怎么拍。
输出格式：每幅一行「序号. 补充的句子」，不要原句，不要解释。"""
_CAMERA_WORDS = re.compile(r"镜头|画面|特写|全景|中景|近景|远景|俯拍|仰拍|俯冲|推近|拉远|逆光|前景|景深|虚化|构图|取景|定格|慢镜")
_ALIEN_SETTING = re.compile(r"树林|森林|闪电|雷光|雪|沙漠|沙地|海|街道|街|草地|草原|山坡|悬崖|篝火|火把|烛|红光|火光|烟雾")


# 「他们/她们」是复数，指在场的一群人，不会造成认错单个人，放行（P205）
_PRONOUN = re.compile(r"[他她它](?!们)")


def _one_person(line, names):
    """这一句点到的人物表里的人；恰好一个才返回，否则返回空（P200）。"""
    hit = [n for n in names if n and n in line]
    return hit[0] if len(hit) == 1 else ""


def _has_person(text, names):
    """这一句里有没有人：人物表里的名字，或者第三人称代词（P203）。

    「原句只点一个人 → 代词就是他」那条前提不成立（一句里两个人、一个只用代词时会指错），
    已经撤掉。这条只判"有没有人"，不判"是谁"——空镜就是空镜，判得准。
    """
    t = str(text or "")
    return bool(_PRONOUN.search(t)) or any(n and n in t for n in names)


def expand_pictures(pics, names, batch=3, log=None, place=""):
    """剧本逐幅小块扩写：3 幅一批让 Qwen 只补 1～2 句。

    守卫：补写 20～140 字、不带引号、不出现新名字、原句不动、
    不出现拍摄词、不编环境、不夹英文，以及 P200 两条——
    同一句里他和她并存直接拒收；原句只点到一个人时把代词换成那个名字。

    names 可以是「名字」也可以是「名字（身份）」，身份里带着性别，
    直接送进提示词。原来只把 names 收下、一个空循环就丢掉了（实测把少女写成"他"）。
    """
    lines = str(pics or "").split("\n")
    idxs = [i for i, l in enumerate(lines) if l.strip() and not _DLG_LINE.match(l) and not l.startswith(("【", "──", "〔"))
            and len(l.strip()) >= 12]
    raw_names = [str(n).strip() for n in (names or []) if str(n or "").strip()]
    # 「小满（人类少女/学徒）」→ 名字取括号前那段，用来判断这一句点到了谁
    names = [re.split(r"[（(]", n, 1)[0].strip() for n in raw_names]
    who = ("\n\n【故事里的人】" + "、".join(raw_names)) if raw_names else ""
    added = 0
    for b in range(0, len(idxs), batch):
        grp = idxs[b:b + batch]
        user = "\n".join("%d. %s" % (k + 1, lines[i].strip()) for k, i in enumerate(grp))
        try:
            rep = _q(_EXPAND_SYS + who + (("\n\n【这个地方有的东西】" + place) if place else ""),
                     user, mt=600, temperature=0.4)
        except Exception:
            continue
        got = {}
        for ln in str(rep or "").splitlines():
            m = re.match(r"^\s*(\d+)\s*[.、．)]\s*(.+?)\s*$", ln)
            if m:
                got[int(m.group(1))] = m.group(2).strip()
        for k, i in enumerate(grp):
            add = got.get(k + 1, "")
            why = ("没给" if not add else "太短/太长" if not (20 <= len(add) <= 140)
                   else "带引号" if re.search(r"[「」“”\"]", add)
                   else "抄了原句" if add[:12] in lines[i]
                   else "拍摄词" if _CAMERA_WORDS.search(add)
                   else "编环境" if (_ALIEN_SETTING.search(add) and not _ALIEN_SETTING.search(place or "")) else "")
            if not why and "他" in add and "她" in add:
                # 一个人不可能既是他又是她。实测出现过
                # 「他左手撑在水槽边缘，目光落在她指尖翻飞的软布上」——把小满一个人拆成了两个（P200）
                why = "他她并存"
            if not why and re.search(r"[A-Za-z]{3,}", add):
                why = "夹英文"
            if not why and _PRONOUN.search(add):
                # 提示词已经要求写名字。用了代词就是没照做，拒收（P205）。
                # 不"换成名字"——那条前提不成立，会把人指错（P200 的教训）
                why = "补写里用了代词"
            if not why and not _has_person(lines[i], names) and _has_person(add, names):
                # 原句是空镜（一个人都没有），补写却把人加了进来。
                # 加进来就得猜是谁，一猜就错：实测「人站在其中…他右手推开木门」
                # 被安在了第一幅空镜上，而那一刻进门的是巴顿（P203）。
                # 空镜补光影、补陈设都行，补人一定是编的。
                why = "空镜里加了人"
            if log is not None:
                log.append((lines[i][:20], add[:60], why or "补入"))
            if why:
                continue
            lines[i] = lines[i].rstrip() + ("" if lines[i].rstrip().endswith(("。", "！", "？")) else "。") + add.rstrip("。") + "。"
            added += 1
    return "\n".join(lines), added


# 导演常把补充写成独立一行的「（补：……）」。那是给上一幅的补充，不是新画面。
# 留着的话：标记进成品（H3 会把「（补：」当画面里的字），补充还被当成多出来的一幅（P202）
_DIR_ADD = re.compile(r"^\s*[（(]\s*补\s*[：:]\s*(.+?)\s*[）)]\s*$")


def merge_director_notes(pics):
    """把「（补：……）」并回上一幅画面。返回 (新稿, 并了几条)。"""
    out, merged = [], 0
    for line in str(pics or "").split("\n"):
        m = _DIR_ADD.match(line)
        if not m:
            out.append(line)
            continue
        add = m.group(1).strip()
        back = None
        for i in range(len(out) - 1, -1, -1):
            t = out[i].strip()
            if not t:
                continue
            if _DLG_LINE.match(t) or t.startswith(("【", "──", "〔")):
                break
            back = i
            break
        if back is None:
            out.append(add)          # 上面没有画面行，就地展开，不丢内容
        else:
            base = out[back].rstrip()
            out[back] = base + ("" if base.endswith(("。", "！", "？")) else "。") + add.rstrip("。") + "。"
        merged += 1
    return "\n".join(out), merged


def _empty_shots_kept(src, new, names):
    """审校前是空镜的画面，审校后不许有人（P204）。

    空镜是 prose_to_pictures 按「第一幅先交代这是什么地方，不拍人」有意生成的。
    往里加人就得猜是谁，一猜就错——实测第一幅空镜被写成「小满站在烤炉旁」，
    而那一刻进门的是巴顿，修理铺里也没有烤炉。
    """
    a = [t for k, t in _pic_parts(src) if not k]
    b = [t for k, t in _pic_parts(new) if not k]
    pairs = zip(a, b) if len(a) == len(b) else zip(a[:1], b[:1])   # 对不上位置就只查第一幅
    for before, after in pairs:
        if not _has_person(before, names) and _has_person(after, names):
            return False
    return True


def pictures_director_pass(pics, prose, log=None, names=()):
    """导演审校：Qwen 按清单补充画面稿。

    代码守卫：段号覆盖不变、台词集合不变、字数不缩水，
    以及 P204——审校前没人的画面，审校后不许有人。
    names 传「名字（身份）」，身份里带着性别；原来一个字都没传，它只能猜。
    """
    src = str(pics or "")
    raw_names = [str(n).strip() for n in (names or []) if str(n or "").strip()]
    bare = [re.split(r"[（(]", n, 1)[0].strip() for n in raw_names]
    lo, hi = int(len(src) * 1.25), int(len(src) * 1.8)
    sysm = _PIC_DIRECTOR_SYS + (("\n\n【故事里的人】" + "、".join(raw_names)) if raw_names else "") + ("\n\n这一稿太薄：每幅都要补到能照着演——谁、用哪只手/哪条腿、身体朝哪、从哪到哪、当时的表情、"
                                "光落在哪。**改完全文要比原稿多 25%%～80%% 的字**（原稿 %d 字，目标 %d～%d 字）。" % (len(src), lo, hi))
    new = ""
    for k in range(2):
        try:
            new = str(_q(sysm, "【画面稿】\n" + src, mt=7000, temperature=0.5) or "").strip()
        except Exception as ex:
            return src, "审校调用失败：%s" % str(ex)[:60]
        if lo * 0.9 <= len(new) <= hi * 1.15:
            break
        sysm += "\n\n（上一稿 %d 字，%s。请在每一幅里再%s，其余要求不变。）" % (
            len(new), "补得不够" if len(new) < lo * 0.9 else "写多了", "补一两句动作细节" if len(new) < lo * 0.9 else "删掉与动作无关的形容")
    if not new or len(new) < len(src) * 0.9:
        return src, "审校后变短了（%d→%d），丢弃" % (len(src), len(new))
    if len(new) > hi * 1.3:
        return src, "审校写飘了（%d→%d），丢弃" % (len(src), len(new))
    # 段号覆盖：原来覆盖了哪些段，现在也得覆盖
    if set(picture_uncovered(new, prose)) - set(picture_uncovered(src, prose)):
        return src, "审校后多了漏段，丢弃"
    # 台词集合不变
    norm = lambda q: re.sub(r"[^\w一-龥]", "", q)
    d_old = sorted(norm(q) for _, q in _pic_parts_dialogue(src))
    d_new = sorted(norm(q) for _, q in _pic_parts_dialogue(new))
    if d_old != d_new:
        return src, "审校改了台词（%d→%d 句），丢弃" % (len(d_old), len(d_new))
    # 空镜里不许多出人来：加了就得猜是谁，一猜就错（P204）
    if not _empty_shots_kept(src, new, bare):
        return src, "审校往空镜里加了人，丢弃"
    return new, "导演审校已并入（%d→%d 字）" % (len(src), len(new))


_DLG_LINE = re.compile(r"^[^\n：:]{1,12}[：:].*$", re.M)


# 比喻短句：「，像两口枯井」「像是一排排锋利的碎玻璃，」「如雷霆般」「仿佛在审视什么」
_SIMILE_CLAUSE = re.compile(
    r"[，、]?(?:得?(?<![不好录图影肖画摄雕偶塑头群自对镜想石佛神铜蜡玉])像(?!素)(?:是|只|个|一)?|如同|仿佛|宛如|好似|恍若|"
    r"(?<![不])如(?!果|何|此|今|同|下|上|前|后|愿|意|约|期|常|实|是)|"
    r"[^，。；！？]{0,6}般[的地]?)[^，。；！？]{1,24}(?=[，。；！？]|$)")
_DEP_START = re.compile(r"^(?:混合|带着|比刚才|随之|随着|并|且|而|但|却|只有|不大|余音|在|从|格外|显得|仿佛|像|如同)")
_SMELL_CLAUSE = re.compile(
    r"[^，。；！？]*(?:霉味|焦糊气|腥气|血腥味|铁锈味|气味|臭味|香气|弥漫着|比外面更冷|冰冷的空气|寒气|凉意|燥热|闷热)"
    r"[^，。；！？]*[，。；！？]?")


def _tidy_sentence(sent):
    sent = re.sub(r"[，、；]{2,}", "，", sent)
    sent = re.sub(r"^[，、；]+", "", sent)
    sent = re.sub(r"^(它们|他们|她们|它|他|她)，", r"\1", sent)     # 「它，四肢张开」→「它四肢张开」
    sent = re.sub(r"[，、；]+([。！？])", r"\1", sent)
    sent = re.sub(r"[，、；]+$", "。", sent)
    return sent.strip()


def _first_clause_hit(sent, pat):
    """声音/气味词是不是落在第一个短句里——是的话这句的主语就是声音/气味，整句都拍不到。"""
    first = re.split(r"[，、；]", sent, 1)[0]
    if len(first.strip()) <= 2:                  # 「闷响，」这种残词只删它自己，不牵连整句
        return False
    return bool(pat.search(first + "。"))


def strip_unfilmable_clauses(text, kinds=("比喻", "纯声音", "气味温度", "内感", "触感", "否定外观")):
    """代码兜底：模型换不掉的比喻/纯声音/气味温度短句直接删。按句处理，句子删空就删句，段落删空就删段。
    只动非台词段（调用方先把台词行遮住）。返回 (新文本, 删除条数)。"""
    t = str(text or "")
    n = 0
    out_paras = []
    for para in re.split(r"\n", t):
        if not para.strip():
            out_paras.append(para)
            continue
        sents = re.findall(r"[^。！？]+[。！？]?", para)
        keep = []
        for sent in sents:
            # 句子里的台词占位符原样保留，只清洗它后面的叙述
            prefix = ""
            _m = re.match(r"^(.*\u2591DLG\d+\u2591)(.*)$", sent, re.S)
            if _m:
                prefix, sent = _m.group(1), _m.group(2)
                if not sent.strip():
                    keep.append(prefix)
                    continue
            orig = sent
            if not prefix and _pos_in_quote(para, para.find(sent)):
                keep.append(sent)
                continue
            drop = False
            for _k, _pat in (("内感", _PAT_NEIGAN), ("触感", _PAT_CHUGAN), ("否定外观", _PAT_FOUDING), ("比喻", _PAT_YANSHEN)):
                if _k in kinds and _pat.search(sent):
                    if _pat is _PAT_YANSHEN:          # 目光成语：整句判定后，成语短句（火花四溅/剑拔弩张）一起删
                        _pat = re.compile(_PAT_YANSHEN.pattern + r"|火花四溅|剑拔弩张|针锋相对|暗流涌动")
                    # 删掉含触发词的那个短句；整句都是它就整句删
                    _cl = re.split(r"([，、；])", sent)
                    _keep, _i = [], 0
                    while _i < len(_cl):
                        piece = _cl[_i]
                        sep = _cl[_i + 1] if _i + 1 < len(_cl) else ""
                        if not _pat.search("，" + piece + "。"):
                            _keep.append(piece + sep)
                        _i += 2
                    sent = "".join(_keep)
                    if _first_clause_hit(orig, _pat):
                        drop = True
            if "比喻" in kinds and re.search(_PAT_BIYU, sent):
                sent = _SIMILE_CLAUSE.sub("", sent)
            if "纯声音" in kinds and _PAT_SHENGYIN.search(sent):
                if _first_clause_hit(sent, _PAT_SHENGYIN):
                    drop = True
                sent = _SOUND_WORDS.sub("", sent)
            if "气味温度" in kinds and _PAT_QIWEI.search(sent):
                if _first_clause_hit(sent, _PAT_QIWEI):
                    drop = True
                sent = _SMELL_CLAUSE.sub("", sent)
            sent = _tidy_sentence(sent)
            core = re.sub(r"[。！？，、；\s]", "", sent)
            if sent != orig.strip():
                n += 1
                if len(core) < 4 or _DEP_START.match(sent):
                    drop = True
            if drop or len(core) < 2:
                if prefix:
                    keep.append(prefix)          # 台词留着，只丢它后面的叙述
                continue
            keep.append(prefix + sent)
        new_para = "".join(keep)
        if new_para.strip():
            out_paras.append(new_para)
        elif not sents:
            out_paras.append(para)
    res = "\n".join(out_paras)
    return re.sub(r"\n{3,}", "\n\n", res), n


def clean_pictures(pics, log=None):
    """画面稿收尾：先让模型换句（台词遮住），换不掉的代码删短句。"""
    t = str(pics or "")
    kept = []

    def _mask(m):
        kept.append(m.group(0))
        return "\u2591DLG%d\u2591" % (len(kept) - 1)

    masked = _DLG_LINE.sub(_mask, t)
    out = replace_bad_sentences(masked, log=log)
    out, n = strip_unfilmable_clauses(out)
    _dbg("剧本兜底删短句", {"删除": n})
    for i, line in enumerate(kept):
        out = out.replace("\u2591DLG%d\u2591" % i, line)
    if "\u2591DLG" in out:
        return t
    return dedupe_sentences(out)


_WEATHER = re.compile(r"雨幕|雨丝|雨水|雨点|雨声|下雨|大雨|细雨|飘雪|雪花|积雪|雾气|浓雾|薄雾|雾中|沙尘|狂风")


def drop_alien_weather(text, place):
    """场景卡里没有的天气（雨/雪/雾）不许出现在画面里——模型常把别的段的雨搬到晴天的段。只删含天气词的短句。"""
    place = str(place or "")
    t = str(text or "")
    if not t:
        return t, 0
    n = 0
    out = []
    for para in t.split("\n"):
        if _DLG_LINE.match(para) or "\u2591DLG" in para:
            out.append(para)
            continue
        sents = re.findall(r"[^。！？]+[。！？]?", para)
        keep = []
        for sent in sents:
            words = set(_WEATHER.findall(sent))
            alien = [w for w in words if w[0] not in place]          # 首字（雨/雪/雾/沙/风）在场景卡里出现过就算有
            if not alien:
                keep.append(sent)
                continue
            clauses = re.split(r"([，；])", sent)
            kept, i = [], 0
            while i < len(clauses):
                c = clauses[i]; sep = clauses[i + 1] if i + 1 < len(clauses) else ""
                if not _WEATHER.search(c):
                    kept.append(c + sep)
                i += 2
            new = _tidy_sentence("".join(kept))
            n += 1
            if len(re.sub(r"[。！？，、；\s]", "", new)) >= 4:
                keep.append(new)
        out.append("".join(keep) if sents else para)
    return "\n".join(out), n


def drop_name_only_sentences(text, names):
    """孤立的「名字。」句删掉（换句/扩写偶尔留下的残渣）。"""
    t = str(text or "")
    for nm in (names or []):
        if nm:
            t = re.sub(r"(?<=[。！？\n])%s[。！？]" % re.escape(str(nm)), "", t)
            t = re.sub(r"^%s[。！？]" % re.escape(str(nm)), "", t, flags=re.M)
    return t


def dedupe_sentences(text):
    """同一段里一字不差重复的句子只留一句（换句/扩写偶尔把同一句写两遍）。"""
    out = []
    for para in str(text or "").split("\n"):
        sents = re.findall(r"[^。！？]+[。！？]?", para)
        seen, keep = set(), []
        for x in sents:
            k = x.strip()
            if k and k in seen:
                continue
            seen.add(k)
            keep.append(x)
        out.append("".join(keep) if sents else para)
    return "\n".join(out)


def replace_bad_in_pictures(pics, log=None):
    """画面稿版问题句替换：台词行（名字：台词）先换成占位符遮住，只动画面描述；换完再放回。"""
    t = str(pics or "")
    kept = []

    def _mask(m):
        kept.append(m.group(0))
        return "\u2591DLG%d\u2591" % (len(kept) - 1)

    masked = _DLG_LINE.sub(_mask, t)
    out = replace_bad_sentences(masked, log=log)
    for i, line in enumerate(kept):
        out = out.replace("\u2591DLG%d\u2591" % i, line)
    if "\u2591DLG" in out:                       # 占位符被模型动了，保守起见退回原稿
        return t
    return out


_SPEAKER_ASK = """下面是一话故事里的几句台词，每句带前后文（台词用【】标出）。判断每一句是谁说的：
先看台词的内容适合谁的身份和处境说出来，再看紧挨着台词的动作是谁做的。
名字只能从这个表里选：%s
只回答，一行一条，格式「序号. 名字」，不要解释。"""


def _lcs_len(a, b, min_len=6):
    """两段文字的最长公共子串长度（≥min_len 才算），用来把正文段对到画面段。"""
    a, b = str(a or ""), str(b or "")
    best = 0
    for L in range(min(len(a), 40), min_len - 1, -1):
        for i in range(0, len(a) - L + 1):
            if a[i:i + L] in b:
                return L
    return best


def reorder_dialogue(pics, prose, log=None):
    """剧本里台词行的顺序必须和正文一致；乱序的台词行挪到正文里含它的那一段所对应的画面段后面。
    只搬台词行本身，不改任何字。返回 (新剧本, 挪动条数)。"""
    norm = lambda q: re.sub(r"[^\w一-龥]", "", str(q))
    prose_paras = [p for p in re.split(r"\n\s*\n", str(prose or "").strip()) if p.strip()]
    quotes = []                                       # (归一台词, 正文段号)
    for i, p in enumerate(prose_paras):
        for q in re.findall(r"[「“]([^」”\n]+)[」”]", p):
            quotes.append((norm(q), i))
    order = {q: k for k, (q, _) in enumerate(quotes)}
    para_of = {q: i for q, i in quotes}
    lines = str(pics or "").split("\n")

    def _qkey(line):
        m = re.match(r"^([^\n：:]{1,12})[：:](.+)$", line.strip())
        if not m:
            return None
        k = norm(m.group(2).strip("「」“”"))
        if k in order:
            return k
        for q in order:
            if k and (k in q or q in k) and min(len(k), len(q)) >= 4:
                return q
        return None

    dlg = [(i, _qkey(l)) for i, l in enumerate(lines) if _qkey(l)]
    moved = 0
    # 找乱序的：它的正文序号小于前面某条已出现台词的序号
    seq = [order[k] for _, k in dlg]
    # 最长递增子序列里的台词算顺序对的，其余的挪
    n_ = len(seq)
    best_len = [1] * n_
    prev = [-1] * n_
    for a in range(n_):
        for b in range(a):
            if seq[b] < seq[a] and best_len[b] + 1 > best_len[a]:
                best_len[a], prev[a] = best_len[b] + 1, b
    keep = set()
    if n_:
        a = max(range(n_), key=lambda x: best_len[x])
        while a != -1:
            keep.add(a)
            a = prev[a]
    bad = {dlg[pos][0] for pos in range(n_) if pos not in keep}
    if not bad:
        return pics, 0
    # 非台词段落及其索引，用来对齐正文段
    pic_paras = [(i, l) for i, l in enumerate(lines) if l.strip() and not _qkey(l) and not _DLG_LINE.match(l)]
    for i in sorted(bad, reverse=True):
        k = _qkey(lines[i])
        if k is None or k not in para_of:      # 这行台词对不上正文（片段/编的/前面挪动后错位）→ 留在原地，别崩（157 独立验收 KeyError: None）
            continue
        src = prose_paras[para_of[k]]
        src_text = re.sub(r"[「“][^」”\n]+[」”]", "", src)
        best_i, best_s = None, 0
        for pi, pl in pic_paras:
            sc = _lcs_len(src_text, pl)
            if sc > best_s:
                best_i, best_s = pi, sc
        if best_i is None or best_s < 6:
            continue
        line = lines.pop(i)
        if best_i > i:
            best_i -= 1
        lines.insert(best_i + 1, line)
        moved += 1
        if log is not None:
            log.append((line[:30], "→ 画面段 %d" % best_i))
        pic_paras = [(j, l) for j, l in enumerate(lines) if l.strip() and not _qkey(l) and not _DLG_LINE.match(l)]
    _dbg("剧本台词顺序", {"乱序": len(bad), "挪动": moved})
    return "\n".join(lines), moved


def fix_speakers(pics, prose, names):
    """剧本里「名字：台词」的说话人，按正文上下文让 Qwen 判一次（名字只能从人物表选），代码只改说话人。
    返回 (新剧本, 改了几处)。"""
    cards = [c for c in (names or []) if isinstance(c, dict)]
    names = [str(c.get("name") or "") if isinstance(c, dict) else str(c) for c in (names or [])]
    names = [n for n in names if n]
    if re.search(r"众人|宾客|人群|纷纷|哗然", str(prose or "")) and "众人" not in names:
        names.append("众人")
    if not names:
        return pics, 0
    paras = [p for p in re.split(r"\n\s*\n", str(prose or "").strip()) if p.strip()]
    norm = lambda q: re.sub(r"[^\w一-龥]", "", str(q))
    # 正文里每句台词及其上下文
    ctx_of = {}
    for i, p in enumerate(paras):
        for q in re.findall(r"[「“]([^」”\n]+)[」”]", p):
            before = paras[i - 1] if i > 0 else ""
            after = paras[i + 1] if i + 1 < len(paras) else ""
            marked = p.replace(q, "【" + q + "】", 1)
            ctx_of[norm(q)] = (before + "\n" + marked + "\n" + after)[:600]
    # 代码先判：同一段里紧挨着台词前面那句的主语（名字直接用；「它」给唯一的非人角色）。
    # 台词独立成段的不猜（上一段写谁挨打，说话的往往是另一个），交给下面的问答。
    # 非人角色：先看人物卡类型字段，卡里没写再看名字
    nonhuman = [str(c.get("name") or "") for c in cards
                if re.search(r"非人|怪物|野兽|魔物|妖|鬼|尸|机器|兽", str(c.get("identity_anchor") or "") + str(c.get("look_full") or "")[:40])]
    if not nonhuman:
        nonhuman = [n for n in names if re.search(r"怪|兽|魔|鬼|尸|妖", n)]
    rule_of = {}
    for p in paras:
        for m in re.finditer(r"[「“]([^」”\n]+)[」”]", p):
            before = p[:m.start()]
            last = [x for x in re.split(r"[。！？]", before) if x.strip()]
            last = last[-1].strip() if last else ""
            if not last or re.search(r"[「“]", last):
                continue
            who = ""
            for n in names:
                if last.startswith(n):
                    who = n
                    break
            if not who and last.startswith("它") and len(nonhuman) == 1:
                who = nonhuman[0]
            if who:
                rule_of[norm(m.group(1))] = who
    lines = str(pics or "").split("\n")
    items = []                                   # (line_idx, 当前说话人, 台词, 上下文)
    for k, line in enumerate(lines):
        m = re.match(r"^([^\n：:]{1,12})[：:](.+)$", line.strip())
        if not m:
            continue
        pass
        say = m.group(2).strip().strip("「」“”")
        key = norm(say)
        ctx = ctx_of.get(key)
        if not ctx:
            for kk, v in ctx_of.items():
                if key and (key in kk or kk in key):
                    ctx = v
                    break
        if ctx:
            items.append((k, m.group(1), say, ctx))
    if not items:
        return pics, 0
    user = "\n\n".join("%d. 台词：%s\n上下文：\n%s" % (i + 1, it[2], it[3]) for i, it in enumerate(items))
    try:
        rep = _q(_SPEAKER_ASK % "、".join(names), user, mt=200, temperature=0.1)
    except Exception:
        return pics, 0
    ans = {}
    for ln in str(rep or "").splitlines():
        m = re.match(r"^\s*(\d+)\s*[.、．)]\s*(\S+)", ln)
        if m:
            ans[int(m.group(1))] = m.group(2).strip("「」：: ")
    changed = 0
    for i, (k, cur, say, _c) in enumerate(items):
        who = rule_of.get(norm(say)) or ans.get(i + 1, "")    # 代码规则优先，其次问答
        if cur not in names and who not in names:
            who = ans.get(i + 1, "") if ans.get(i + 1, "") in names else (names[0] if names else cur)
        if who in names and who != cur:
            lines[k] = lines[k].replace(cur, who, 1)
            changed += 1
    _dbg("剧本说话人核对", {"台词": len(items), "改": changed, "回答": str(rep or "")[:120]})
    return "\n".join(lines), changed


def build_shotlist_for(sid, ep, pics):
    """建这一话的拍摄清单并存到 e["shotlist"]；按清单只改**确定**的说话人。
    返回 (画面稿, 说明)。抽不出清单就原样返回（下游走老路，老项目不变）。"""
    from . import asset_core as _ac, shotlist as _sl, story_core as _stc
    _saga, e = _ep(sid, ep)
    prose = str((e or {}).get("prose") or "")
    # P263/P264：清单的人物卡口径和提示词绑卡同一口径（cast_cards，P261）——原来给的是全部卡，
    # 重新分话留下的过期自动卡也算"有卡"，清单标了 card=True 而提示词绑不上。
    cards = [str(c.get("name") or "") for c in (cast_cards(sid) or []) if c.get("name")]
    scards = [str(x.get("name") or "") for x in (_ac.list_assets(sid, "scenes") or []) if x.get("name")]
    # 人物表：安排的 items.who.people；没安排（或安排没写人）用结构层的 plan_people。
    # 地点表：安排的 items.where.places + 场景卡名（去重）。清单按表写名字、location 归到表名，
    # 下游（提示词、场景卡、出片前门）才对得上（STORY_164：清单写「灰狸花猫」「临街窗边」，表上是「流浪猫」「店门口」）
    _arr = confirmed_arrangement(sid, ep) or {}
    people = [p for p in ((((_arr.get("items") or {}).get("who") or {}).get("people") or []) if _arr else [])
              if isinstance(p, dict) and str(p.get("name") or "").strip()]
    # 没安排就不给人物表：plan_people 是全剧的统一人物表，不是本话的——拿它当本话人物表，
    # 第 N 话没出场的人会被 validate 打回两轮、还进 ambiguities（P269 复核查出）
    places = list(_sl._place_names((((_arr.get("items") or {}).get("where") or {}).get("places") or []) if _arr else []))
    for _nm in scards:
        if _nm and _nm not in places:
            places.append(_nm)
    sl = _sl.build(prose, pics, cards, scards, people=people, places=places)
    if not sl:
        _update_ep(sid, ep, shotlist=None)
        return pics, {"清单": "没抽出来，走老路"}
    new_pics, changed, doubt = _sl.fix_pictures_speakers(pics, sl)
    if changed:
        pics = new_pics
    # 【只查不改】漏掉的关键事件不再自动补原文原句进去——实测补的和模型换说法写过的重复，
    # 同一个动作演两遍还挤掉后面情节的时间（P278，用户 2026-09-11 验收）。查出来交给检查器问人。
    filled = []
    _miss_ev = _sl.missing_events(sl, pics, prose)
    note = {"人物": len(sl.get("cast") or []), "场": len(sl.get("scenes") or []),
            "台词": len(sl.get("lines") or []), "改说话人": changed,
            "存疑": [(w, want, q[:12]) for _, w, want, q in doubt],
            "没卡有戏": [c.get("name") for c in _sl.needs_card(sl)],
            "画面稿里找不到的事件": [x.get("ev", "")[:16] for x in _miss_ev]}
    if changed or filled:
        sl["pictures_sig"] = _sl.content_sig(pics)
        _update_ep(sid, ep, pictures=pics, body=pics, shotlist=sl)
    else:
        _update_ep(sid, ep, shotlist=sl)
    return pics, note


def fresh_shotlist(sid, ep=1):
    """这一话的拍摄清单，且是照着现在这份画面稿做的；否则 None（下游走老路）。"""
    from . import shotlist as _sl
    try:
        _saga, e = _ep(sid, ep, create=False)
    except Exception:
        return None
    sl = (e or {}).get("shotlist")
    if not isinstance(sl, dict) or not sl.get("scenes"):
        return None
    if sl.get("pictures_sig") and sl["pictures_sig"] != _sl.content_sig((e or {}).get("pictures") or ""):
        return None
    return sl


def drop_signage_lines(pics, prose):
    """画面稿里把招牌/文字当成台词的行删掉（P284：木牌上刻的「清溪苑」写成了「阿明：清溪苑」）。
    只删内容和原文里 刻着/写着「X」 完全一样的台词行。返回 (新稿, 删了几行)。"""
    from . import shotlist as _slm
    signs = _slm.signage_quotes(prose)
    if not signs:
        return pics, 0
    out, n = [], 0
    for ln in str(pics or "").split("\n"):
        m = _slm._PIC_LINE_RE.match(ln.strip())
        if m and _slm._n(m.group(3)) in signs:
            n += 1
            continue
        out.append(ln)
    return ("\n".join(out) if n else pics), n


_DANGLING_SPEAKER = re.compile(r"[，,]?\s*([^，。！？\s：:]{1,8})[：:]\s*([^\s：:，。！？]{1,8})\s*$")


def strip_dangling_speaker(pics):
    """动作行末尾挂着「抱怨道：林阿姨」、下一行才是「林阿姨：台词」——把挂着的那截删掉（P294，模型格式抖动）。
    只删"动词+道/说+冒号+名字"收尾、且下一条非空行就是同一个名字开头的台词行的情况。返回 (新稿, 改了几行)。"""
    lines = str(pics or "").split("\n")
    out, n = [], 0
    for i, ln in enumerate(lines):
        m = _DANGLING_SPEAKER.search(ln)
        if m and len(ln[:m.start()].strip()) >= 6:                     # 前面得是一句动作描写；「老陈：林阿姨」这种整行台词不动
            nxt = next((x.strip() for x in lines[i + 1:] if x.strip()), "")
            name = m.group(2)
            if nxt.startswith(name) and len(nxt) > len(name) and nxt[len(name)] in "：:":
                ln = ln[:m.start()].rstrip("，, ") + "。" if ln[:m.start()].strip() else ""
                n += 1
        out.append(ln)
    return ("\n".join(out) if n else pics), n


_QUOTE_IN_LINE = re.compile(r"[“「\"]([^”」\"]{2,})[”」\"]")


def split_narration_in_dialogue(pics):
    """台词行里混进了叙述（「李建国：他声音有些哑，指腹摩挲塑封膜，“一九九四年……”」）→ 拆成 叙述行 + 台词行（P305）。
    只处理：说话人后面先有 ≥6 字不带引号的叙述、再有引号里的话。返回 (新稿, 拆了几行)。"""
    from . import shotlist as _slm
    lines = str(pics or "").split("\n")
    out, n = [], 0
    for ln in lines:
        m = _slm._PIC_LINE_RE.match(ln.strip())
        if m:
            who, body = m.group(1), m.group(3)
            q = _QUOTE_IN_LINE.search(body)
            if q and q.start() >= 6 and not _QUOTE_IN_LINE.search(body[:q.start()]):
                before = body[:q.start()].strip("，,：: ")
                after = body[q.end():].strip("，,。 ")
                out.append(before + ("。" if before and not before.endswith(("。", "！", "？")) else ""))
                out.append("%s：%s" % (who, q.group(1).strip()))
                if after:
                    out.append(after + ("。" if not after.endswith(("。", "！", "？")) else ""))
                n += 1
                continue
        out.append(ln)
    return ("\n".join(out) if n else pics), n


def make_pictures(sid, ep=1, on_step=None):
    """正文 → 画面稿，存回这一话。两次调用（写一次 + 有缺口补一次）。"""
    from . import story_core, asset_core as _ac
    ep = int(ep or 1)
    _saga, e = _ep(sid, ep)
    prose = str((e or {}).get("prose") or "")
    if not prose.strip():
        raise RuntimeError("第%d话还没有原文，先生成原文" % ep)
    settings = (story_core.get_story(sid) or {}).get("settings") or {}
    chars = cast_cards(sid)                      # 安排确认后只认安排里的人（P261）
    _v2 = str((e or {}).get("script_v2") or "").strip()
    if _v2 and _story_v2():
        # 故事层 v2 已经直接写成画面剧本：不再小说→画面稿，只做清洗/说话人/顺序/残渣
        if on_step:
            on_step("第%d话用编剧组写好的画面剧本" % ep)
        pics = _v2
        # 故事层已做过清洗/说话人核对/连贯性；这里原来又拿"派生正文"核一遍剧本——
        # 派生正文就是从剧本生成的，等于自己核自己，fix_speakers 的主语猜测还可能把核过的说话人改错（2026-09-04 体检）。
        pics = drop_name_only_sentences(pics, [str(c.get("name") or "") for c in chars])
        _update_ep(sid, ep, pictures=pics, body=pics, timeline_stale=True)
        if on_step:
            on_step("剧本完成")
        return {"pictures": pics, "problems": []}
    if on_step:
        on_step("把第%d话原文改编成剧本" % ep)
    # 已确认的本话安排一起给：四段情节和结尾是用户点过头的，画面稿不许自己换
    # 单话直写模式的正文已经由用户确认，剧本只负责把叙述和对白整理成
    # 可拍顺序。再让模型整篇“改编”会随机出现两种坏结果：台词总表重复，
    # 或每段原文后再抄一段扩写（STORY_213 四次实测）。导演创作留给后面的
    # 深度 H3 提示词，这一层用确定性直转，保证故事不被改动。
    if str(settings.get("story_mode") or "").strip().lower() == "single":
        try:
            from . import oral_story as _os454
            prose = _os454.lift_inline_dialogue(prose, chars)                       # P454②
        except Exception:
            pass
        pics = literal_pictures_from_prose(prose, chars)
        _dbg("单话正文直转剧本", {"正文": len(prose), "剧本": len(pics)})
    else:
        pics = prose_to_pictures(prose, settings, characters=chars, arrangement=confirmed_arrangement(sid, ep))
        _repeat_n = repeated_prose_dialogue(pics, prose)
        if _repeat_n >= 3 or "```" in str(pics) or len(str(pics)) > len(prose) * 1.8:
            _dbg("剧本改编异常，改用正文直转兜底", {"重复": _repeat_n, "长度": len(str(pics))})
            pics = literal_pictures_from_prose(prose, chars)
    # 【空稿不落盘】模型偶发返回空/被截断，原来会把空画面稿存进去、旧清单还挂着，
    # 分镜页显示"还没有剧本"却查不出原因（浏览器实测一次）。空了就明确报错，让用户再点一次。
    if len(str(pics or "").strip()) < 50:
        raise RuntimeError("画面稿没写出来（模型返回空或被截断），请再点一次「按原文生成剧本」")
    # 【丢台词直接补回】校验能查出来的确定性问题，代码修，不打回重写
    _who_names = [str(c.get("name") or "").strip() for c in (chars or []) if isinstance(c, dict) and str(c.get("name") or "").strip()]
    try:
        _arr_p = confirmed_arrangement(sid, ep) or {}
        _who_names += [str(p.get("name") or "").strip() for p in (((_arr_p.get("items") or {}).get("who") or {}).get("people") or []) if isinstance(p, dict)]
    except Exception:
        pass
    pics, _added = fix_missing_dialogue(pics, prose, people=_who_names)      # P310：按台词清单补/删
    pics, _nsign = drop_signage_lines(pics, prose)
    if _nsign:
        _dbg("删掉当成台词的招牌文字", {"条": _nsign})
    pics, _ndang = strip_dangling_speaker(pics)                     # P294：「抱怨道：林阿姨」这种尾巴
    if _ndang:
        _dbg("删掉动作行末尾挂着的说话人", {"行": _ndang})
    pics, _nsplit = split_narration_in_dialogue(pics)               # P305：台词行里混进叙述 → 拆开
    if _nsplit:
        _dbg("台词行里的叙述拆成两行", {"行": _nsplit})
    # 短词如果确实出现在正文台词清单里（“走！”“嗯。”）必须保留；只删
    # 模型凭空产生的短待定行。
    try:
        from . import dialogue_ledger as _dl_short
        _spoken_short = {re.sub(r"[^\w一-龥]", "", e.get("text") or "")
                         for e in _dl_short.spoken(_dl_short.ledger(prose, _who_names))}
    except Exception:
        _spoken_short = set()
    _keep = []
    for _ln in pics.split("\n"):
        _m = re.match(r"^\s*" + re.escape(UNKNOWN_SPEAKER) + r"[：:]\s*(.*)$", _ln)
        _short_norm = re.sub(r"[^\w一-龥]", "", _m.group(1)) if _m else ""
        if _m and len(re.sub(r"[^\u4e00-\u9fa5]", "", _m.group(1))) <= 3 \
                and _short_norm not in _spoken_short:
            continue
        _keep.append(_ln)
    pics = "\n".join(_keep)
    # 【导演审校】用户 2026-09-02：剧本写细、空间进出写明、换空间先定场——Qwen 按清单补，代码守卫
    if on_step:
        on_step("导演审校剧本：动作/空间/定场/光")
    # 人物表要在导演之前就备好：导演和逐幅扩写都靠它认人（P200/P204）
    _who = []
    for c in chars:
        _n = str(c.get("name") or "").strip()
        if not _n:
            continue
        _r = str(c.get("role") or c.get("relation") or "").strip()
        _who.append("%s（%s）" % (_n, _r) if _r else _n)
    if lean_script():
        _dnote = "精简模式：跳过导演审校"
    else:
        pics, _dnote = pictures_director_pass(pics, prose, names=_who)
    # 导演的提示词里写着「每幅画面前面有〔段号〕」，它会把 prose_to_pictures 已经剥掉的段号加回来。
    # 加回来有两个后果：逐幅扩写的行过滤是 not startswith("〔")，整步一次调用都不发、静默失效；
    # 段号还会一路留进成品，而 H3 收到〔3〕会把它当成画面里的字。所以这里再剥一次（P201）
    _tagged = pics.count("〔")
    pics = strip_pic_tags(pics)
    if _tagged:
        _dnote = (_dnote or "") + "；导演稿带回 %d 处段号，已剥掉" % _tagged
    pics, _merged = merge_director_notes(pics)          # P202：「（补：…）」并回上一幅
    if _merged:
        _dnote = (_dnote or "") + "；导演写了 %d 条「（补：…）」，已并回对应画面" % _merged
    _dbg("剧本导演审校", {"结果": _dnote})
    # 【逐幅扩写】整篇审校会原样抄回；3 幅一批只补句子，模型服从得多（用户 2026-09-03：用千问扩写但要写细）
    if on_step:
        on_step("剧本逐幅扩写：手/朝向/表情/背景")
    _elog = []
    try:
        _sc_all = [x for x in (_ac.list_assets(sid, "scenes") or []) if x.get("name")]
        _sc_use = scenes_in_text(_sc_all, prose)
        _bits = []
        for sc in _sc_use:
            for k in ("space", "light", "ground", "furniture", "landmarks"):
                v = str(sc.get(k) or "").strip()
                if 4 < len(v) < 300:
                    _bits.append(v)
        _place = "；".join(dict.fromkeys(_bits))[:600]
    except Exception:
        _place = ""
    if lean_script():
        _nadd = 0
    else:
        pics, _nadd = expand_pictures(pics, _who, log=_elog, place=_place)
    _dbg("剧本逐幅扩写", {"补入": _nadd, "样例": _elog[:6]})
    # 【拍不到的句子】正文里没换掉的比喻/气味/温度/纯声音句会被剧本原样抄走——这里再换一遍，台词行遮住不动
    if on_step:
        on_step("剧本换掉拍不到的句子")
    _rlog = []
    if not lean_script():
        pics = clean_pictures(pics, log=_rlog)
    _dbg("剧本问题句替换", {"条数": len(_rlog), "结果": [(x[0], x[3]) for x in _rlog][:12]})
    # 【说话人】正文没写谁说的台词，剧本会猜错（v5：怪物的"有人味"配给了格拉姆）——问一次 Qwen，只改名字
    # 精简链也要做一次专职说话人核对。它只改台词行左侧的人名，不扩写
    # 剧情；省掉这一步会把女主的决定配给掌门（STORY_213 实测）。
    pics, _nsp = fix_speakers(pics, prose, chars)
    # 【台词顺序】剧本里台词先后必须和正文一致（结尾的台词被放到中段，结尾镜头就没词、模型会胡说）
    pics, _nmv = reorder_dialogue(pics, prose)
    # 【残渣与外来天气】孤立的「名字。」删掉；场景卡里没有的雨/雪/雾删短句（正文里有的天气按正文，场景卡有的都算有）
    pics = drop_name_only_sentences(pics, [str(c.get("name") or "") for c in chars])
    pics, _nw = drop_alien_weather(pics, (_place or "") + str(prose or ""))
    _dbg("剧本外来天气", {"删": _nw})
    probs = ([("台词丢了：%s" % x[0]) for x in picture_missing_dialogue(pics, prose)]
             + (["故事没讲完，缺 %d 字" % len(picture_tail_gap(pics, prose))]
                if picture_tail_gap(pics, prose) else []))
    # 【只查不改】没镜头的情节段不再自动补句子（P278）：查出来记日志，出片前检查会问用户
    try:
        _arr_fb = confirmed_arrangement(sid, ep)
        if _arr_fb:
            from . import arrangement as _armfb
            _mb = _armfb.missing_beats(pics, _arr_fb)
            if _mb:
                _dbg("这几段情节没有镜头", {"缺": _mb})
    except Exception as _ex:
        _dbg("查情节段失败", {"err": str(_ex)[:100]})
    # 【也写进 body】新链路里这份"画面稿"就是这一话的剧本，
    # 而界面剧本栏读的是 body。两个字段存同一份：
    # pictures 给下游用（切段、H3 提示词），body 给界面和旧代码用
    # （用户 2026-09-01 定：画面稿改名叫剧本，放进剧本栏）。
    try:
        from . import oral_story as _os454b
        pics = _os454b.lift_inline_dialogue(pics, chars)                     # P454⑮：两条路都对一遍说话人（探险家→少年探险家）、去重
    except Exception:
        pass
    pics = desex_text(pics, settings)                            # P360：极致诱惑下性器官名换泛称
    _update_ep(sid, ep, pictures=pics, body=pics, timeline_stale=True)
    # 【拍摄清单】整条流水线共用的一份事实（P248，用户 2026-09-10 定）：
    # 谁在场、在哪、每句谁说的，每条带原文依据。下游读它，不再每层重猜。
    try:
        if on_step:
            on_step("场记整理拍摄清单")
        pics, _sl_note = build_shotlist_for(sid, ep, pics)
        if _sl_note:
            _dbg("拍摄清单", _sl_note)
    except Exception as _ex:
        _dbg("拍摄清单失败（走老路）", {"err": str(_ex)[:120]})
    if on_step:
        on_step("剧本完成%s" % ("" if not probs else "（还剩 %d 处）" % len(probs)))
    return {"pictures": pics, "problems": probs}


_SLICE_CACHE = {}


def confirmed_arrangement(sid, ep=1):
    """这个项目第 ep 话已确认的本话安排，没有 → None。
    P285：有已确认的整部规划 → 从规划派生这一话的安排（段数按内容定）；
    没有 → 退回旧的单话安排（settings.arrangement，只对第 1 话有意义）。
    读不到项目也返回 None：安排是锦上添花，不能让没安排的老项目断在这里。"""
    from . import story_core, arrangement as _arm
    try:
        settings = (story_core.get_story(sid) or {}).get("settings") or {}
        try:
            from . import whole_plan as _wp
            plan = _wp.confirmed(settings)
            if plan:
                arr = _wp.episode_arrangement(plan, ep)
                return _arm.normalize(arr) if arr else None
        except Exception:
            pass
        if int(ep or 1) != 1:
            return None
        return _arm.confirmed_of(settings)
    except Exception:
        return None


def cast_cards(sid):
    """这一话的人物卡。有已确认的本话安排 → 只认安排人物表里的人（过期的自动卡不算）；
    没有安排 → 全部卡。P261：重新分话/旧清单建过的卡混进画面稿，同一个人两个名字。"""
    from . import asset_core as _ac, story_core as _stc
    cards = _ac.list_assets(sid, "characters") or []
    arr = confirmed_arrangement(sid)
    if not arr:
        return cards
    names = {str(p.get("name") or "").strip() for p in (((arr.get("items") or {}).get("who") or {}).get("people") or []) if isinstance(p, dict)}
    try:
        names |= {str(p.get("name") or "").strip() for p in (((_stc.get_story(sid) or {}).get("settings") or {}).get("plan_people") or []) if isinstance(p, dict)}
    except Exception:
        pass
    names = {x for x in names if x}
    if not names:
        return cards
    kept = [c for c in cards if str(c.get("name") or "").strip() in names]
    return kept or cards


def _sliced(pics, arrangement=None):
    """时长切段，按画面稿全文缓存——切一次要问一次模型（动作时长），
    「生成下面 N 段」连点几次不该重复问。
    缓存键带上安排的指纹：有安排时情节段是硬边界，安排一改切法就变，不能拿旧的。"""
    from . import arrangement as _arm
    key = hash((pics, _arm.beat_sig(arrangement)))
    if key not in _SLICE_CACHE:
        if len(_SLICE_CACHE) > 8:
            _SLICE_CACHE.clear()
        _SLICE_CACHE[key] = slice_pictures(pics, arrangement=arrangement)
    return _SLICE_CACHE[key]


def merge_short_slices(slices, min_sec=8.0, cap=15.0, place_of=None):
    """不足 min_sec 秒的切片并进同场景（scene_hint 相同）的邻段：先并前面、再并后面；合并后不超过 cap 秒；
    跨情节段（beat）允许，跨地点不允许（一段只能绑一张场景板）。反复直到没有可并的。（P330）"""
    out = [dict(s) for s in (slices or [])]
    changed = True
    while changed and len(out) > 1:
        changed = False
        for i, s in enumerate(out):
            if float(s.get("seconds") or 0) >= min_sec:
                continue
            for j in ((i - 1, i + 1) if i > 0 else (i + 1,)):
                if j < 0 or j >= len(out):
                    continue
                t = out[j]
                # 两个各含台词的短段不能再并回去；一段多句台词会让口型和说话人混乱。
                _ds, _dt = _pic_parts_dialogue(s.get("text")), _pic_parts_dialogue(t.get("text"))
                if _ds and _dt and {x[0] for x in _ds} != {x[0] for x in _dt}:
                    continue
                if str(t.get("scene_hint") or "") != str(s.get("scene_hint") or ""):
                    continue
                if place_of is not None:
                    try:
                        if str(place_of(t) or "") != str(place_of(s) or ""):
                            continue                                    # 拍摄清单说不是同一场戏 → 不并（一段只绑一张场景板）
                    except Exception:
                        pass
                if float(t.get("seconds") or 0) + float(s.get("seconds") or 0) > cap + 1.0:
                    continue                                    # 超一秒以内允许（出片时夹到 15 秒，少掉的不到一秒）
                a, b = (t, s) if j < i else (s, t)
                big = t if float(t.get("seconds") or 0) >= float(s.get("seconds") or 0) else s
                merged = dict(big)
                merged["text"] = (str(a.get("text") or "").rstrip() + "\n\n" + str(b.get("text") or "").lstrip()).strip()
                merged["seconds"] = min(cap, float(a.get("seconds") or 0) + float(b.get("seconds") or 0))
                merged["scene_hint"] = a.get("scene_hint") or b.get("scene_hint")
                lo, hi = (j, i) if j < i else (i, j)
                out[lo:hi + 1] = [merged]
                changed = True
                break
            if changed:
                break
    return out


def cut_slices(slices, arrangement=None):
    """「把后半部分留到下一话」：只留 beat_index ≤ cut_after_beat 的段（-1 不剪）。
    判不出情节段的切片（-1）不剪——不知道它是哪段，就不能说它在后半部分。
    beat_map 是单调的，所以留下的一定是前缀，段号不会出现空洞。"""
    from . import arrangement as _arm
    cut = _arm.cut_of(arrangement)
    slices = list(slices or [])
    if cut < 0:
        return slices
    return [s for s in slices if int((s or {}).get("beat_index", -1)) <= cut]


def _set_camera_owner(sid, settings, chars):
    """P461：视点是第一视角/自拍时，把拍摄者定下来写进 settings（本次调用用，不落盘）。"""
    try:
        from . import camera_view as _cv
        if _pov_spec(settings)[0] in _cv.MODES:
            _o = camera_owner_for(sid, settings, chars)
            if _o:
                settings["camera_owner"] = _o
    except Exception:
        pass


def make_h3_prompts(sid, ep=1, per_seg=None, on_step=None, start=0, count=None,
                    prev_tail="", direction=None):
    """画面稿 → H3 提示词，一段一个。

    段的边界＝slice_pictures 的时长切段（8~15 秒），提示词和秒数是同一刀切的。
    （原来提示词按"每 6 块画面"切、秒数按时长切，两套边界对不上——2026-09-02 查出。
    per_seg 参数只为兼容旧调用保留，不再起作用。）
    start/count：只写第 start 段起的 count 段（"生成下面 N 段"用）；
    prev_tail：上一段最后一个镜头行，用来接戏。
    返回 [{"n", "prompt", "problems", "seconds", "over"}]，n 从 start+1 起。
    """
    from . import story_core, asset_core as _ac
    ep = int(ep or 1)
    _saga, e = _ep(sid, ep)
    pics = str((e or {}).get("pictures") or "")
    if not pics.strip():
        raise RuntimeError("第%d话还没有剧本" % ep)
    settings = dict((story_core.get_story(sid) or {}).get("settings") or {})
    try:
        _tl = str(((e or {}).get("elements") or {}).get("时间与光线") or "").strip()
        if _tl:
            settings["_time_light"] = _tl
    except Exception:
        pass
    # 敌我以故事层要素表为准，别让导演层去猜（2026-09-05：两张人物卡时女主被当成敌人）
    try:
        _elx = (e or {}).get("elements") or {}
        settings["_foe_text"] = "%s %s" % (_elx.get("谁挡他") or "", _elx.get("对手是什么") or "")
        settings["_ally_text"] = str(_elx.get("同伴") or "")
        settings["_hero_text"] = str(_elx.get("主角") or "")
        settings["_ride"] = str(_elx.get("_ride") or "")
        settings["_pace"] = str(_elx.get("_pace") or "")            # 节奏档（设计 v2）
        settings["_no_strike"] = bool(_elx.get("_no_strike"))       # 对峙而不打
    except Exception:
        pass
    chars = cast_cards(sid)                      # 安排确认后只认安排里的人（P261）
    _set_camera_owner(sid, settings, chars)      # P461：第一视角/自拍的拍摄者
    # P263：全部场景卡另留一份给清单绑卡用。scenes_in_text 按名字逐字找——正文没逐字出现「面包店」三字时
    # 「面包店内」「店门口」两张卡被整个过滤掉，清单里的场归不到任何卡（STORY_164 实测）
    _scs_all = [x for x in (_ac.list_assets(sid, "scenes") or []) if x.get("name")]
    # 只认这一话正文里提到的场景（剧本扩写可能抄进别的场景卡的描写，不能拿剧本来判）
    _scs = scenes_in_text(_scs_all, str((e or {}).get("prose") or ""))
    # 只绑有已采用场景图的场景（参考包里也只有这些）；一个都没有就绑第一个名字（无图）
    _adopted = {str(v.get("owner_id") or "") for v in (_ac.list_assets(sid, "visuals") or [])
                if str(v.get("status") or "") == "adopted"}
    _with_img = [x for x in _scs if str(x.get("scene_id") or "") in _adopted]
    for _c in chars:
        _c["_has_image"] = str(_c.get("character_id") or "") in _adopted          # P322⑧：有已采用人设图的人，正文服装词让位给图
    scene = ([str(x.get("name") or "") for x in _with_img]
             or ([str(_scs[0].get("name") or "")] if _scs else [""]))
    # 光线句从所有场景卡里找（第一张卡可能只是"坍塌石墙旁"，光在"教堂内部"那张里）
    scene_space = "。".join(str(x.get("space") or x.get("contract_text") or "") for x in _scs)
    # 【服从本话安排】切片带情节段；用户选了「留到下一话」的，超出的段这里就不写提示词
    _arr = confirmed_arrangement(sid, ep)
    from . import arrangement as _arm
    _fr0 = frozen_slicing(sid, ep)
    if not _fr0:
        freeze_slicing(sid, ep, pics, episode_slices(sid, ep, pics=pics, cut=False))   # P339：写提示词之前把切法冻住
    elif _fr0.get("sig") != _pics_sig(pics):
        apply_slicing_drift(sid, ep)                                        # 剧本变了：先作废漂掉的段再写（不静默重冻）
    slices = episode_slices(sid, ep, pics=pics)                             # P336：和出片前门/总段数同一份切片（含并短段）
    if _arr:
        _dbg("切段情节归属", {"段": [(sl.get("beat") or "?") for sl in slices],
                              "剪到": _arm.cut_of(_arr), "总段": len(_sliced(pics, _arr))})
    start = max(0, int(start or 0))
    if start >= len(slices):
        return []
    end = len(slices) if count is None else min(len(slices), start + max(0, int(count)))
    out, _tail = [], str(prev_tail or "")
    # 【分镜表】导演一段一段写不知道全篇，先通读一次给每段定目的/关键动作/站位（用户 2026-09-03）
    _plan = plan_segments(slices, chars, settings=settings)
    if on_step:
        on_step("分镜表：%d/%d 段有目的" % (len(_plan), len(slices)))
    # 【场景按地点只绑一张】这段画面稿提到哪张场景卡就绑哪张，没提沿用上一段——
    # 原来把三张全绑进每一段，H3 每段随机挑一张，成片空间进进出出（用户 2026-09-02）
    # 【同一场戏只用一张板】按段匹配会在同一场戏里来回换板（2026-09-05 项目102：
    # 一场追逐戏 15 段绑了 4 张板横跳，每换一张 H3 就照新板重建构图，接口必跳）。
    # 先逐段算候选，再按场戏分组取多数票，组内共用一张。
    _pick = []
    _pv = ""
    for kk in range(0, len(slices)):
        _pv = pick_scene_for_text(slices[kk]["text"], _with_img or _scs, _pv)
        _pick.append(_pv)
    # 【画面稿写了地点就照它绑】剧本自己标的「── 城堡净身房｜午后 ──」是权威，
    # 比按文本猜准。有地点行时直接用，并且**跳过下面的平滑规则**——
    # 那规则（至少停两段/不走回头路）是给长追逐戏防横跳的，段少时会把对的改错
    # （P243 项目153：5 段跨 4 个地点，被平滑成 2 个地点，错 4 段）。
    _hinted = [str(sl.get("scene_hint") or "") for sl in slices]
    _use_hint = any(_hinted)
    # 【清单优先】拍摄清单里每场戏有原文依据，按它给每段定场，跳过猜测和平滑（P248）。
    _slist = fresh_shotlist(sid, ep)
    if _slist:
        from . import shotlist as _slm
        _names_all = [str(x.get("name") or "") for x in (_with_img or _scs) if x.get("name")]
        # P263：先按清单口径找卡（用户答过的 card 字段 > location 归到卡名单，和出片前门同一个 place_in_table），
        # 候选是**全部**场景卡，不是正文过滤后的；找不到再退回老的 _match_scene_name
        _all_cards = [str(x.get("name") or "") for x in _scs_all if x.get("name")]
        _last = ""
        for kk, _sl_ in enumerate(slices):
            _scn, _score = _slm.scene_for_text(_slist, _sl_.get("text"))
            _m = ""
            if _scn and _score:
                _m = _slm.scene_card(_scn, _all_cards)
                if not _m:
                    _m = _match_scene_name(str(_scn.get("location") or ""), _names_all)
            if _m:
                _last = _m
            _pick[kk] = _last or _pick[kk]
        _use_hint = True                                   # 等于"有权威地点"，下面的平滑不再跑
        _dbg("场景按拍摄清单绑", {"绑到": _pick})
    # 有清单就不跑地点行覆盖：这循环没有地点行时把第 1 段的场景抄给后面所有段，和出片前门（按清单）口径打架，
    # 门改绑后重写提示词又绑回去（STORY_164 第 3 段实测，P269）。清单是原文依据，检查和绑卡必须同一口径。
    _beat_slices = any(("cut" in sl or sl.get("shot")) for sl in slices)      # P415：节拍段的地点行是拍表定的，压过清单
    if _use_hint and (not _slist or _beat_slices):
        _names = [str(x.get("name") or "") for x in (_scs_all or _with_img or _scs) if x.get("name")]     # P422：对名字在全部卡里找，没图的也算
        _last = ""
        for kk, _h in enumerate(_hinted):
            _m = _match_scene_name(_h, _names)
            if _m:
                _last = _m
            elif not _last:
                _last = _pick[kk] or ""
            _pick[kk] = _last or _pick[kk]
        _dbg("场景按画面稿地点行绑", {"地点行": _hinted, "绑到": _pick})
    # 【换景可以，别走回头路】追逐戏本来就该一路换景（穿过车流→擦着幕墙→冲向天际线），
    # 压成一张板反而丢了推进感（用户 2026-09-05）。真正让人觉得"跳"的是**来回横跳**：
    # 幕墙→摩托车上→天际线→**幕墙**→车流中。所以只治两件事：
    #   ① 单向推进：换过去的板不再换回来；
    #   ② 至少停两段：刚换的板连用 ≥2 段，别一段一换地闪。
    # 【② 只在段多时才成立】段数少的时候一话本来就三四个地点、每段一个，
    # 要求"连用两段"必然把中间的地点吃掉——项目155 实测（4 段）：
    # 猜的是 [林间空地, 演武场, 长街, 长街]，被这条改成 [林间空地, 林间空地, 长街, 长街]，
    # 结果师傅在商业街上吐血传送，演武场那张图一次没用上（P245）。
    # ① 任何长度都保留：来回横跳确实伤接口。
    _hold_rule = len(_pick) >= 8
    _raw = list(_pick)                    # 前瞻要看没被改过的原始猜测
    try:
        _used, _cur, _hold = [], "", 0
        for _k in ([] if _use_hint else range(len(_pick))):
            _want = _pick[_k]
            if not _cur:
                _cur, _hold = _want or "", 1
                if _cur:
                    _used.append(_cur)
                _pick[_k] = _cur
                continue
            if _want and _want != _cur:
                # 【前瞻，别先换了再卡住】老写法是"当前板没待够两段就不许换"，
                # 结果换过去之后又因为"不走回头路"回不来：A A B A A A → A A B B B B，
                # 一段闪现把后面三段全带跑了，比不管还糟。
                # 改成看**要换过去的板**在原始猜测里连不连得满两段，连不满就不换。
                _flick = _hold_rule and not (
                    _k + 1 < len(_raw) and _raw[_k + 1] == _want)
                # 【回头路也只在段多时防】"出门→办事→回家"回到家是对的，短片按内容走
                if (_hold_rule and _want in _used) or _flick:   # （段多时）回头路，或只闪一段
                    _pick[_k] = _cur
                    _hold += 1
                    continue
                _cur, _hold = _want, 1                 # 真·换到新地方
                _used.append(_cur)
                _pick[_k] = _cur
                continue
            _pick[_k] = _cur
            _hold += 1
        _dbg("场景板单向推进", {"顺序": _used})
    except Exception:
        pass
    _prev_scene = _pick[start - 1] if start > 0 else ""
    settings["_wardrobe"] = {}
    # P329：衣着状态整话算一遍（确定性：脱/换/洗澡/湿透，原文里被收掉的事件按位置补回），每段 开始时/本段内/结束时
    try:
        from . import wardrobe as _wd
        settings["_wardrobe_plan"] = _wd.track([str(s_.get("text") or "") for s_ in slices],
                                               [str(c.get("name") or "") for c in chars if c.get("name")],
                                               prose=str((e or {}).get("prose") or ""))
    except Exception as _wx:
        settings["_wardrobe_plan"] = []
        _dbg("衣着追踪失败（退回问模型）", {"err": str(_wx)[:100]})
    if start > 0:
        try:
            from . import saga_core as _sgw
            _tlw = _sgw.ep_timeline(sid, ep) or {"scenes": []}
            _gp = next((g for sc0 in (_tlw.get("scenes") or []) for g in (sc0.get("segments") or []) if int(g.get("no") or 0) == start), None)
            if _gp and isinstance(_gp.get("wardrobe"), dict):
                settings["_wardrobe"] = dict(_gp["wardrobe"])            # P318b：接着上一段结束时的衣着
        except Exception:
            pass
    from . import seam_v2 as _sv2
    _roles = _sv2.seam_roles(_pick) if _sv2.on() else []
    _relay_prev, _done_all = None, []
    _prev_present = []
    if _sv2.on() and start == 0 and int(ep or 1) > 1 and not str(prev_tail or "").strip():
        # P343（用户 9-14 定）：新一话的第一段接着上一话结束时的状态——谁在哪、手里什么、已经做过什么，不重新介绍、不重做
        try:
            from . import saga_core as _sgp
            _tlp = _sgp.ep_timeline(sid, int(ep) - 1) or {"scenes": []}
            _gsp = sorted([g for sc0 in (_tlp.get("scenes") or []) for g in (sc0.get("segments") or [])], key=lambda g: int(g.get("no") or 0))
            if _gsp:
                _lastp = _gsp[-1]
                _rp = _lastp.get("relay") if isinstance(_lastp.get("relay"), dict) and _lastp.get("relay") else None
                if _rp is None:
                    _r0 = _sv2.parse_relay(_lastp.get("tail"), _lastp.get("chars") or [c.get("name") for c in chars])
                    _rp = _r0 if _sv2.relay_ok(_r0) else None
                if _rp:
                    _relay_prev = dict(_rp)
                    _relay_prev["from_prev_ep"] = True
                    prev_tail = "（上一话结束时）" + _sv2.relay_text(_rp)
                    _tail = prev_tail                                       # 第一段导演看到的【上一段结束时的状态】
                _done_all = list(_lastp.get("done_all") or [])[-12:]
                _dbg("接上一话的结束状态", {"上一话": int(ep) - 1, "接力": bool(_rp), "已做": len(_done_all)})
        except Exception as _px:
            _dbg("接上一话失败（按全片开头写）", {"err": str(_px)[:100]})
    if _sv2.on() and start > 0:
        try:
            from . import saga_core as _sgr
            _tlr = _sgr.ep_timeline(sid, ep) or {"scenes": []}
            _gpr = next((g for sc0 in (_tlr.get("scenes") or []) for g in (sc0.get("segments") or []) if int(g.get("no") or 0) == start), None)
            if _gpr:
                _relay_prev = _gpr.get("relay") if isinstance(_gpr.get("relay"), dict) and _gpr.get("relay") else None
                if _relay_prev is None:                                  # 老时间轴没存：从存的尾句解析
                    _r0 = _sv2.parse_relay(_gpr.get("tail"), _gpr.get("chars") or [c.get("name") for c in chars])
                    _relay_prev = _r0 if _sv2.relay_ok(_r0) else None
                _done_all = list(_gpr.get("done_all") or [])
        except Exception:
            pass
    try:
        from . import episode_ledger as _ledg
        _lp = ((_ledg.get(sid, int(ep) - 1) or {}).get("people") or {}) if int(ep or 1) > 1 else {}
        settings["_ledger_people"] = {n: _ledg.person_line(sid, int(ep) - 1, n) for n in _lp} if _lp else {}     # P445③
    except Exception:
        settings["_ledger_people"] = {}
    for k in range(start, end):
        chunk = slices[k]["text"]
        settings.pop("_beat", None)
        settings["_direction"] = str(direction or "").strip()          # P397：用户口述的这一段拍法（只在按段重写时给）
        if slices[k].get("shot") or slices[k].get("cut") is not None:
            # P366：这一段是一拍——景别、0 秒动作、硬切/接力都定了
            _act_line = next((l for l in chunk.splitlines() if l.strip() and not l.startswith("──") and not _PIC_DLG.match(l.strip())), "")
            settings["_beat"] = {"shot": str(slices[k].get("shot") or "中景"), "establish": bool(slices[k].get("establish")),
                                 "cut": bool(slices[k].get("cut")), "act": _act_line.strip()[:60],
                                 "pace": str(slices[k].get("pace") or "快"),                                   # P404：这一拍的档位
                                 "who": str(slices[k].get("who") or ""),                                        # P408：这一拍谁在做事
                                 "empty": bool("who" in slices[k] and not str(slices[k].get("who") or "").strip()
                                               and not any(str(c.get("name") or "") and str(c.get("name")) in chunk for c in chars)),
                                 "cast": [str(c.get("name") or "") for c in chars if c.get("name")]}     # P385：空镜拍 + 全员名单（删人用）
            if isinstance(slices[k].get("director"), dict):
                settings["_beat"]["director"] = slices[k]["director"]                                   # P435：导演骨架
            if slices[k].get("cut"):
                _relay_prev = None         # 硬切：不带上一段末帧、不接力；上一段的尾句照给（人在哪、躺着还是站着——P371 项目217 实测丢了就来回跳）
            if slices[k].get("establish") and k > 0 and not isinstance(slices[k].get("director"), dict):
                # P387①：换了地方的第一拍——「转场」＝时间过去了；位置/姿势/托举背负都不沿用（破屋 10 拍背着人对话）
                _tail = "（换了地方、过了些时候：每个人的位置和姿势按这一拍和【场景】重新安排，不沿用上一段的托举、背负、站位）"
        _wp = settings.get("_wardrobe_plan") or []
        settings["_wardrobe_row"] = _wp[k] if k < len(_wp) else None       # P329：这一段每个人的衣着 开始/变化/结束
        _sc_name = _pick[k] or pick_scene_for_text(chunk, _with_img or _scs, _prev_scene)
        _prev_scene = _sc_name
        if _sv2.on():
            _sr = dict(_roles[k]) if k < len(_roles) else {"ep_first": k == 0, "ep_last": k == len(slices) - 1, "scene_changed": False, "last_of_scene": False}
            _sr["relay"] = _relay_prev
            # 画面稿这一段自己写了的事不算"重演"（画面稿是权威）
            _sr["done"] = [d for d in list(_done_all)[-12:] if not _sv2.repeated_done(chunk, [d], [str(c.get("name") or "") for c in chars])]
            # P344（用户 9-14 定）：上一段剧本里的原句也进"不许再演"清单——导演自己概括的词对不上原文时守卫会漏
            _cont = (str((_relay_prev or {}).get("ongoing") or "") + "。" +
                     (re.split(r"[。！？\n]", str(slices[k - 1]["text"]).strip())[-2:] or [""])[-1]) if k > 0 else ""
            _nm_all = [str(c.get("name") or "") for c in chars]
            _sr["prev_script"] = [s_ for s_ in _sv2.script_clauses(slices[k - 1]["text"] if k > 0 else "", names=_nm_all)
                                  if not _sv2.repeated_done(chunk, [s_], _nm_all) and not _sv2.repeated_done(_cont, [s_], _nm_all)][-6:]
            _sr["next_head"] = re.sub(r"\s+", " ", str(slices[k + 1]["text"] if k + 1 < len(slices) else "").strip())[:90]   # 接力帧为下一段第一件事摆位
            # P355：下一段剧本里的动作本段不许先做（200 第 1 段把第 2 段的「递徽记、按在胸前」提前演了，第 2 段又演一遍）
            _sr["next_script"] = [s_ for s_ in _sv2.script_clauses(slices[k + 1]["text"] if k + 1 < len(slices) else "", names=_nm_all, max_len=22)
                                  if not _sv2.repeated_done(chunk, [s_], _nm_all)][:6]
            if slices[k].get("shot") or slices[k].get("cut") is not None:
                _sr["next_script"] = []          # P374：节拍段只演这一拍，不给下一段的事（模型会拿它当本段内容）
                _sr["next_head"] = ""            # P387③：下一拍的开头也不给（空镜里提前走进一个背竹筒的人）
                _sr["beat"] = True               # P385：接缝措辞按节拍段来（「已做完」不是禁词）
            settings["_seam"] = _sr                                             # P331：接缝角色/接力状态/已完成清单
        if on_step:
            on_step("写第 %d 段视频提示词（共 %d 段）" % (k + 1, len(slices)))
        # 在场判定允许**名字后缀**匹配：画面稿里把「三眼白狼」写成「白狼」是常态，
        # 全名精确匹配对不上就当它不在场 → 对手每段都没参考图，每段长得不一样
        # （2026-09-05 导演层体检查出：段2 正文一大段三眼白狼，绑定里只有林小凡）。
        def _in_chunk(nm, txt):
            nm = str(nm or "")
            if not nm:
                return False
            if nm in txt:
                return True
            for k in (3, 2):
                if len(nm) > k and nm[-k:] in txt and len(nm[-k:]) >= 2:
                    return True
            return False
        # 【绑图依据要和写镜头的依据一样宽】只按剧本片段找名字是不够的：
        # 导演计划里点了名的人，写镜头那步一定会把他写进画面，可他没被绑图，
        # 脸就是现编的——实测第1段 chars 只有小满，而 plan.action 是
        # 「小满用力擦拭台面并质问巴顿来意」，成片里巴顿出现了两拍，
        # 12 秒内就换了一次脸（P225）。
        _pl = _plan.get(k + 1) or {}
        _plan_txt = " ".join(str(_pl.get(x) or "") for x in
                             ("purpose", "action", "blocking", "entry", "shots"))             if isinstance(_pl, dict) else str(_pl or "")
        # 在场人物只看可见动作和台词的说话人。台词内容里被提到的人并未出镜：
        # 「小师叔说过……」不能因此把小师叔参考图绑进当前画面（Codex 9-16）。
        # 导演计划里点名的人仍算在场（P225），两条都要。
        _look = visible_cast_text(chunk) + " " + _plan_txt
        if _slist:
            # P264：别称归一再找人——段里只写「灰狸花猫」，卡叫「流浪猫」，清单 aliases 里有这层对应；
            # 不归一就判成不在场，猫每段都没参考图
            _look = _slm.canon_text(_slist, _look)
        _present = [c for c in chars if _in_chunk(c.get("name"), _look)]
        _empty_beat = bool((settings.get("_beat") or {}).get("empty"))
        if _empty_beat:
            _present = []                                 # P384/P385：空镜拍（谁＝无、没人名）就是没人，不退回上一段/全员
        else:
            _present = _present or _prev_present or chars    # 全是他/她 → 沿用上一段在场人；开场才退回全员
        # 【在场人物按清单】这场戏清单说谁在场就绑谁（有卡的才绑得上；没卡的由出片前那道门建卡）——P248
        if _slist:
            _scn, _score = _slm.scene_for_text(_slist, chunk)
            if _scn and _score:
                _want_names = {_slm.name_of_id(_slist, x) for x in (_scn.get("present") or [])}
                _want_names |= {a for x in (_scn.get("present") or [])
                                for a in ((_slm.cast_by_id(_slist).get(x) or {}).get("aliases") or [])}
                _by_list = [c for c in chars if str(c.get("name") or "") in _want_names]
                if _by_list:
                    _present = _by_list
        # 同乘一辆载具的两个人全程同框：一个在场就把另一个补上，否则没绑图的那个会被编一张脸
        # （2026-09-05 项目102 段1：只绑了林野，模型编出"对手林野（另一分身）"当敌人）
        _rd2 = str((settings or {}).get("_ride") or "")
        _mrd = re.match(r"([\u4e00-\u9fa5]{2,4})和([\u4e00-\u9fa5]{2,4})同乘", _rd2)
        if _mrd:
            _pair = {_mrd.group(1), _mrd.group(2)}
            _have = {str(c.get("name") or "") for c in _present}
            if _pair & _have and not _pair.issubset(_have):
                _present = [c for c in chars if str(c.get("name") or "") in (_have | _pair)]
        _dcast = ((settings.get("_beat") or {}).get("director") or {}).get("cast") if isinstance(settings.get("_beat"), dict) else None
        if _dcast is not None:
            _present = [c for c in chars if str(c.get("name") or "") in _dcast]      # P435：导演段只绑画面里的人（画外的人不带图）
            if _pov_spec(settings)[0] == "第一视角POV":
                _own = str(settings.get("camera_owner") or "")
                if _own and _own not in _dcast:
                    _present = _present + [c for c in chars if str(c.get("name") or "") == _own]   # P461：拍摄者进表配音、不带图、不入画
        _prev_present = list(_present)
        # P263：清单绑到的卡可能不在正文过滤后的 _scs 里，锚点从全部场景卡里取
        _sc_card = next((x for x in _scs_all if str(x.get("name") or "") == _sc_name), None)
        settings["_scene_light"] = str((_sc_card or {}).get("light") or "")          # P372：光按本段场景卡
        _seg_space = str((_sc_card or {}).get("space") or (_sc_card or {}).get("contract_text") or "").strip() or scene_space   # P373：环境/光只用本段那张卡
        settings["_scene_layout"] = scene_layout_names(_sc_card, beat=bool(settings.get("_beat")))   # P325：戏区位置名单；节拍段不带「哪场戏」（P383）
        _ssp = str((_sc_card or {}).get("space") or (_sc_card or {}).get("contract_text") or "")
        if len(_ssp) > 120:
            _cut = max(_ssp.rfind("。", 0, 120), _ssp.rfind("；", 0, 120))
            _ssp = _ssp[:_cut + 1] if _cut > 20 else _ssp[:120]
        settings["_seam_scene_space"] = _ssp                                    # P331：开头段用这张卡自己的空间（按句截断）
        # 【配角/群演按清单】这段所在的场戏：群体名 + 在场但没卡的人 → 让 h3_prompt 别锁死（P252）
        settings.pop("_extras_hint", None)
        settings.pop("_extras_scene", None)
        if _slist:
            try:
                _scn2, _score2 = _slm.scene_for_text(_slist, chunk)
                if not (_scn2 and _score2) and _sc_name:
                    # P323：按文本对不上场戏时退回这段绑的场景卡（检查器就是按卡对的；两边口径不一致 → 锁死句被反复报「有问题」）
                    _scn2 = next((s_ for s_ in (_slist.get("scenes") or []) if isinstance(s_, dict)
                                  and (str(s_.get("card") or "") == _sc_name or str(s_.get("location") or "") == _sc_name)), None)
                    _score2 = 1 if _scn2 else 0
                if _scn2 and _score2:
                    _hint = []
                    _chunk_c = _slm.canon_text(_slist, chunk or "")     # 别称归一：画面稿写「母猫」也认得出（P275）
                    _in_scene = [_x for _x in (_slist.get("extras") or [])
                                 if str(_scn2.get("id")) in [str(_y) for _y in (_x.get("scenes") or [])]]
                    for _x in _in_scene:
                        if _x.get("name"):
                            _hint.append(str(_x["name"]))
                    _have_names = {str(c.get("name") or "") for c in _present}
                    _no_card = []
                    for _cid in (_scn2.get("present") or []):
                        _nm2 = _slm.name_of_id(_slist, _cid)
                        if not _nm2 or _nm2 in _have_names:
                            continue
                        _no_card.append(_nm2)
                        if _nm2 in _chunk_c:
                            _hint.append(_nm2)
                    if _hint:
                        settings["_extras_hint"] = _hint
                    # 名字对不上也要记一笔：这场戏确实有没卡的人/动物/群演，提示词就不许写死"不出现任何别的人"
                    # （不点名，免得把这一段没写到的人塞进画面）
                    if _no_card or _in_scene or (_scn2.get("mentioned") or []):
                        settings["_extras_scene"] = True
            except Exception:
                pass
        if not settings.get("_extras_hint") and (settings.get("_beat") or {}).get("director"):
            try:
                from . import director_shots as _ds454
                _xh = _ds454.extras_phrases(settings["_beat"]["director"], [c.get("name") for c in _present])
                if _xh:
                    settings["_extras_hint"] = _xh                                  # P454④：骨架里的群众照写、不锁死
            except Exception:
                pass
        # 【本段要点按安排】这段属于哪一段情节，预算里那条的 focus 就是它的要点（契约 C）
        settings.pop("_beat_focus", None)
        if _arr:
            _bf = _arm.focus_of(_arr, slices[k].get("beat_index", -1))
            if _bf:
                settings["_beat_focus"] = _bf
        _sc_has_img = bool(_sc_card and str(_sc_card.get("scene_id") or "") in _adopted)      # P322①：场景板有图就绑成 <Picture N>
        p = h3_prompt(chunk, _present, [_sc_name] if _sc_name else scene, settings, prev_tail=_tail,
                      anchor=env_anchor_from_card(_sc_card) if _sc_card else None, scene_image=_sc_has_img,
                      scene_space=_seg_space, seconds=slices[k]["seconds"], plan=_plan.get(k + 1))
        # P227：镜头文本是模型现写的，它会引入计划里没有的人（第4段绑了艾拉，
        # 写出来的提示词里有巴顿和小满）。按**最终文本**重新算一次在场，
        # 多出来的人补进去再写一遍——有错直接修，不打回。
        # 只在真多出人时多花一次调用；在场表最多长到全员，不会来回反复。
        _names_in = lambda _p: [c for c in chars
                                if str(c.get("name") or "")
                                and str(c.get("name")) not in
                                {str(x.get("name") or "") for x in _present}
                                and str(c.get("name")) in str(_p or "")]
        _extra = [] if _empty_beat else _names_in(p)
        if _extra:
            _present = list(_present) + _extra
            if _dcast is not None:
                # The generated shot can reveal an entering actor omitted by its cast list.
                # Keep reference bindings and director metadata consistent before one rewrite.
                settings["_beat"]["director"]["cast"] = [str(c.get("name") or "") for c in _present]
            p = h3_prompt(chunk, _present, [_sc_name] if _sc_name else scene, settings,
                          prev_tail=_tail, scene_image=_sc_has_img,
                          anchor=env_anchor_from_card(_sc_card) if _sc_card else None,
                          scene_space=_seg_space, seconds=slices[k]["seconds"],
                          plan=_plan.get(k + 1))
            _dbg("按最终提示词补绑参考图", {"段": k + 1,
                                            "补上": [str(c.get("name")) for c in _extra]})
        _sexes = {card_sex(c) for c in _present if card_sex(c)}
        if _sexes == {"男"}:
            p = p.replace("她", "他")                                          # P373：在场全是男，模型写的「她」是笔误
        elif _sexes == {"女"}:
            p = p.replace("他", "她")
        bad = h3_problems(p, chunk, _present, settings)
        bad = list(bad or []) + list(getattr(_h3_timeline_body, "last_problems", []) or [])     # P324⑥：越轴没改成的记进段问题
        # P225 自检：提示词里出现了人物表上、却没绑参考图的人 → 记下来。
        # 没绑图的人脸是模型现编的，段与段之间必然换脸。
        # 只记不改（D3：有结构表时保真问题只记录不重写）。
        _bound = {str(c.get("name") or "") for c in _present}
        _loose = [str(c.get("name") or "") for c in chars
                  if str(c.get("name") or "") and str(c.get("name")) not in _bound
                  and str(c.get("name")) in str(p or "")]
        # P228：同一个人既坐又站，只报不改（先修对峙修复器，再看还在不在）
        _pose = tl_pose_problems(p, [str(c.get("name") or "") for c in _present])
        if _pose:
            bad = list(bad or []) + _pose
        if _loose:
            bad = list(bad or []) + [
                "提示词里出现了没绑参考图的人：%s——他们的长相是模型现编的，"
                "段与段之间会换脸" % "、".join(_loose[:4])]
        p = desex_text(p, settings)                              # P360：极致诱惑下性器官名换泛称
        # 下一段的起手状态：优先用模型写的「收尾状态」，没写才退回最后一个镜头行
        _tail = getattr(h3_prompt, "last_tail", "") or ""
        if not _tail:
            _ls = [x for x in p.splitlines() if x.strip().startswith("镜头")]
            _tail = _ls[-1].strip() if _ls else ""
        _relay_now = getattr(h3_prompt, "last_relay", None) if _sv2.on() else None
        if _sv2.on():
            if _relay_now:
                _relay_now["done"] = _sv2.done_in_script(_relay_now.get("done") or [], chunk, [str(c.get("name") or "") for c in _present])   # 导演编的事不进清单
            _done_all = (list(_done_all) + list((_relay_now or {}).get("done") or []))[-12:]
            _relay_prev = _relay_now
        critical = h3_critical_problems(p, chunk, bad, slices[k], _sc_name)
        out.append({"n": k + 1, "prompt": p, "problems": bad,
                    "cut": bool(slices[k].get("cut")), "shot": slices[k].get("shot") or "", "establish": bool(slices[k].get("establish")),   # P366
                    "critical_problems": critical, "source_text": chunk,
                    "tail": _tail, "scene": _sc_name, "chars": [str(c.get("name") or "") for c in _present],
                    "direction": str(settings.get("_direction") or ""),
                    "relay": (_relay_now or {}) if _sv2.on() else {}, "done_all": list(_done_all) if _sv2.on() else [],   # P331：接力状态 / 已完成清单
                    "seam": dict(settings.get("_seam") or {}, relay=None, done=None) if _sv2.on() else {},
                    "wardrobe": dict(settings.get("_wardrobe") or {}),          # P318b：这段结束时各人穿什么（下次接着写时读）
                    "plan": _plan.get(k + 1) or {},
                    "seconds": slices[k]["seconds"], "over": bool(slices[k].get("over")),
                    "beat": str(slices[k].get("beat") or ""), "beat_index": int(slices[k].get("beat_index", -1))})
    return out


def total_segments(sid, ep=1):
    """这一话的剧本一共能切几段（按安排剪过之后的数——「留到下一话」的段不算）。没剧本返回 0。"""
    _saga, e = _ep(sid, int(ep or 1))
    pics = str((e or {}).get("pictures") or "")
    if not pics.strip():
        return 0
    try:
        if director_mode(sid) and not (cast_cards(sid) or []):
            return 0                                                         # P460：还没有人物卡就不跑导演分段（会切出全空镜）
    except Exception:
        pass
    return len(episode_slices(sid, ep, pics=pics))


def episode_slices(sid, ep=1, pics=None, cut=True):
    """这一话的整话切片——出提示词、出片前门、总段数都用这一份（P336）。
    = 时长切段 → 短段并进同场戏邻段（<8 秒，接缝 v2 默认；V41_SLICE_MIN_SEC 可改）→ 按安排剪（cut=False 不剪，门看整话用）。
    P332：已经出过片的话不改切法（段号会错位、已出的段全标过期）——并完段数和已有段对不上就不并。"""
    ep = int(ep or 1)
    if pics is None:
        _saga, e = _ep(sid, ep, create=False)
        pics = str((e or {}).get("pictures") or "")
    if not str(pics or "").strip():
        return []
    _arr = confirmed_arrangement(sid, ep)
    fr = frozen_slicing(sid, ep)
    if beat_mode(sid):
        # P366：节拍切段——一拍一段，不并短段、不按安排剪；剧本没变就按冻结的
        if fr and fr.get("sig") == _pics_sig(pics):
            return [dict(s) for s in fr["slices"]]
        return beat_slices_for(sid, ep, pics)
    if fr and fr.get("sig") == _pics_sig(pics):
        merged = [dict(s) for s in fr["slices"]]                          # P339：剧本没变 → 永远按冻结的切片
    else:
        merged = _merged_slices(sid, ep, list(_sliced(pics, _arr)), _arr)
    return cut_slices(merged, _arr) if cut else merged


_BEAT_CACHE = {}


def merge_paren_duplicate_cards(sid):
    """「老者」和「老者（云崖门长辈）」是同一个人（P367 项目217 实测：提取器和剧本各建了一张）：
    留有采用图的那张，名字改成短名；另一张删掉。返回合并了几对。"""
    from . import asset_core, oral_story as _os_
    cards = list(asset_core.list_assets(sid, "characters") or [])
    adopted = {str(v.get("owner_id") or "") for v in (asset_core.list_assets(sid, "visuals") or [])
               if v.get("status") == "adopted" and str(v.get("kind") or "").startswith("character")}
    by_name = {str(c.get("name") or ""): c for c in cards}
    n = 0
    for c in cards:
        nm = str(c.get("name") or "")
        sn = _os_.short_name(nm)
        if sn == nm or sn not in by_name:
            continue
        other = by_name[sn]
        keep, drop = (c, other) if str(c.get("character_id") or "") in adopted or str(other.get("character_id") or "") not in adopted else (other, c)
        keep["name"] = sn
        for k in ("clothing", "appearance_details", "hair", "age", "sex", "char_type", "face_type", "build"):
            if not str(keep.get(k) or "").strip() and str(drop.get(k) or "").strip():
                keep[k] = drop[k]
        try:
            asset_core.save_asset(sid, "characters", keep)
            asset_core.delete_asset(sid, "characters", drop["character_id"])
            n += 1
        except Exception as _mx:
            _dbg("合并重复人物卡失败", {"err": str(_mx)[:100]})
        by_name.pop(nm, None)
    return n


def director_mode(sid_or_settings):
    """P435：节拍模式下默认走导演分段；项目设置 director_shots=False 或环境 V41_DIRECTOR=0 退回一句一拍。"""
    if not beat_mode(sid_or_settings):
        return False
    if str(os.environ.get("V41_DIRECTOR") or "").strip() == "0":
        return False
    from . import story_core as _sc0
    st = sid_or_settings if isinstance(sid_or_settings, dict) else (_sc0.get_story(sid_or_settings) or {})
    sett = st.get("settings") if isinstance(st.get("settings"), dict) else st
    return (sett or {}).get("director_shots", True) is not False


def beat_mode(sid_or_settings):
    """这一话按节拍切吗（P366）。单话直写/口述项目默认是；老的分话项目默认不是；settings.slice_mode 可强制 beat/time。"""
    from . import story_core as _stc
    s = sid_or_settings if isinstance(sid_or_settings, dict) else ((_stc.get_story(str(sid_or_settings)) or {}).get("settings") or {})
    mode = str(s.get("slice_mode") or "").strip().lower()
    if mode in ("beat", "time"):
        return mode == "beat"
    return str(s.get("story_mode") or "").strip().lower() in ("single", "episode", "manual", "shots")


def shots_input(sid, ep, text, pace="适中", on_step=None):
    """模式二「口述拍法」（P379）：口述怎么拍 → 节拍表 → 冻结切片；这一话的正文/剧本就是口述本身。
    人物卡、场景卡由用户在资源页自己建好。返回切片列表。"""
    from . import oral_story as _os_, asset_core, story_core, saga_core as _sgs
    ep = int(ep or 1)
    text = str(text or "").strip()
    if not text:
        raise ValueError("口述内容为空")
    st = story_core.get_story(sid) or {}
    settings = dict(st.get("settings") or {})
    settings["story_mode"] = "shots"
    if not str(settings.get("one_line") or "").strip():
        settings["one_line"] = text[:120]
    st["settings"] = settings
    story_core.save_story(st)
    _update_ep(sid, ep, prose=text, pictures=text, body=text, brief=text[:200], brief_edited_by_user=True,
               story_pace=pace, timeline_stale=True, plan_stale=False, plan_version=0)
    scenes = [x for x in (asset_core.list_assets(sid, "scenes") or []) if x.get("name")]     # P382：带描述给模型对地点
    chars = [c for c in (asset_core.list_assets(sid, "characters") or []) if c.get("name")]    # P384：带性别年龄，称呼对卡
    if on_step:
        on_step("按口述拆节拍：一个镜头一拍")
    _dbg_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs", "节拍表")
    os.makedirs(_dbg_dir, exist_ok=True)
    sl = _os_.shots_table(text, scenes, chars, _q, pace, debug_path=os.path.join(_dbg_dir, "%s_e%d_口述拍法.txt" % (sid, ep)))
    if not sl:
        raise RuntimeError("口述没拆出节拍（模型没按格式输出）")
    # 冻结切片；旧段清掉，提示词/视频按新拍表重来
    tl = _sgs.ep_timeline(sid, ep) or {"scenes": []}
    for sc in tl.get("scenes") or []:
        sc["segments"] = []
    tl["slicing"] = _slicing_record(text, sl)
    _sgs.save_ep_timeline(sid, ep, tl)
    try:
        _cs = "|".join(sorted(str(c.get("name") or "") for c in (cast_cards(sid) or [])))
    except Exception:
        _cs = ""
    _BEAT_CACHE[(str(sid), ep, _pics_sig(text), _cs, str(settings.get("pov") or ""))] = sl
    return sl


def beat_slices_for(sid, ep, pics, on_step=None):
    """剧本 → 节拍表 → 切片（P366）。同一份剧本进程内只问一次模型。"""
    from . import oral_story as _os_, asset_core, story_core
    try:
        _cards_sig = "|".join(sorted(str(c.get("name") or "") for c in (cast_cards(sid) or [])))
    except Exception:
        _cards_sig = ""
    try:
        _pov_sig = str(((story_core.get_story(sid) or {}).get("settings") or {}).get("pov") or "")
    except Exception:
        _pov_sig = ""
    key = (str(sid), int(ep or 1), _pics_sig(pics), _cards_sig, _pov_sig)    # P460：卡名单进缓存键；P461：视点也进（换视点要重切）
    if key not in _BEAT_CACHE:
        merge_paren_duplicate_cards(sid)                                   # P367⑤：「老者」+「老者（云崖门长辈）」合成一张
        scenes = [str(x.get("name") or "") for x in (asset_core.list_assets(sid, "scenes") or []) if x.get("name")]
        chars = [str(c.get("name") or "") for c in (cast_cards(sid) or []) if c.get("name")]
        _saga, e = _ep(sid, int(ep or 1), create=False)
        pace = str((e or {}).get("story_pace") or "适中")
        n_ev = len((e or {}).get("events") or []) or 5
        if on_step:
            on_step("导演拆节拍：一拍一段")
        _dbg_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs", "节拍表")
        os.makedirs(_dbg_dir, exist_ok=True)
        if director_mode(sid):
            # P435：导演分段——一件事 2～3 段、每段一个连续镜头 11～15 秒、只列画面里的人、结束状态同一次调用连着写
            from . import director_shots as _ds
            if on_step:
                on_step("导演分段：一件事 2～3 段")
            _evp = [str(x.get("text") or "") for x in ((e or {}).get("events") or []) if isinstance(x, dict)]
            _prev_led = ""
            try:
                from . import episode_ledger as _ledg
                _prev_led = _ledg.text(sid, int(ep or 1) - 1) if int(ep or 1) > 1 else ""                # P445③：上一话结束时的账本
            except Exception:
                _prev_led = ""
            from . import story_core as _stc461
            _st461 = (_stc461.get_story(sid) or {}).get("settings") or {}
            _pov_name = _pov_spec(_st461)[0]
            _pov_owner = camera_owner_for(sid, _st461) if _pov_name in _ds.POV_MODES else ""     # P461
            sl = _ds.shot_list(pics, asset_core.list_assets(sid, "scenes") or [], cast_cards(sid) or [], _q,
                               debug_path=os.path.join(_dbg_dir, "%s_e%d_导演.txt" % (sid, int(ep or 1))), on_step=on_step,
                               event_points=_evp,                                                      # P440：口述要点给导演
                               event_map=((e or {}).get("fidelity") or {}).get("map"),                    # P443：按忠实核对的事件→段落表分件
                               prev_ledger=_prev_led, pov=_pov_name, owner=_pov_owner)
        else:
            sl = _os_.beat_table(pics, scenes, chars, _q, pace, n_events=n_ev,
                                 debug_path=os.path.join(_dbg_dir, "%s_e%d.txt" % (sid, int(ep or 1))))
        if not sl:
            raise RuntimeError("节拍表没拆出来（模型没按格式输出）")
        if len(_BEAT_CACHE) > 8:
            _BEAT_CACHE.clear()
        _BEAT_CACHE[key] = sl
    return [dict(s) for s in _BEAT_CACHE[key]]


def _pics_sig(pics):
    import hashlib
    return hashlib.sha256(str(pics or "").encode("utf-8")).hexdigest()[:16]


def frozen_slicing(sid, ep):
    """时间轴里冻结的切片 {"sig", "slices"}；没有返回 None（P339）。"""
    try:
        from . import saga_core as _sgf
        fr = (_sgf.ep_timeline(sid, int(ep or 1)) or {}).get("slicing")
        return fr if isinstance(fr, dict) and isinstance(fr.get("slices"), list) and fr.get("slices") else None
    except Exception:
        return None


def _slicing_record(pics, slices):
    keep = ("text", "seconds", "scene_hint", "beat", "beat_index", "shot", "establish", "cut", "who", "pace", "director")   # P366：节拍字段一起冻；P404 档位；P435 导演骨架
    return {"sig": _pics_sig(pics), "at": time.time(),
            "slices": [{k: s.get(k) for k in keep if k in s} for s in (slices or [])]}


def freeze_slicing(sid, ep, pics, slices):
    """把这一话的切片冻进时间轴（P339）：剧本不变就永远按这份切，拍摄清单重建/模型估时抖动都不再改切法。"""
    from . import saga_core as _sgf
    ep = int(ep or 1)
    tl = _sgf.ep_timeline(sid, ep) or {"scenes": []}
    tl["slicing"] = _slicing_record(pics, slices)
    _sgf.save_ep_timeline(sid, ep, tl)


def slicing_drift(sid, ep):
    """剧本改了之后，时间轴里的段从第几段起和现在的切片对不上（1 起；0 = 没漂）。P339。"""
    fr = frozen_slicing(sid, ep)
    if not fr:
        return 0
    _saga, e = _ep(sid, int(ep or 1), create=False)
    pics = str((e or {}).get("pictures") or "")
    if fr.get("sig") == _pics_sig(pics):
        return 0
    old = [re.sub(r"\s+", "", str(s.get("text") or "")) for s in fr["slices"]]
    new = [re.sub(r"\s+", "", str(s.get("text") or "")) for s in episode_slices(sid, ep, pics=pics, cut=False)]
    for k in range(max(len(old), len(new))):
        if k >= len(old) or k >= len(new) or old[k] != new[k]:
            return k + 1
    return 0


def apply_slicing_drift(sid, ep):
    """剧本改了 → 从第一个变了的切片起，后面的段提示词全部作废（有视频的标过期）、超出新总段数的段删掉，
    再按新剧本重新冻结切片。返回 (起始段号, 作废段数)；没漂返回 (0, 0)。P339。
    用户手改过的提示词（prompt_edited）也作废：它对应的剧本块已经不是那一块了。"""
    from . import saga_core as _sgd
    ep = int(ep or 1)
    _saga, e = _ep(sid, ep, create=False)
    pics = str((e or {}).get("pictures") or "")
    if not pics.strip():
        return 0, 0
    fr = frozen_slicing(sid, ep)
    if fr and fr.get("sig") == _pics_sig(pics):
        return 0, 0
    new = episode_slices(sid, ep, pics=pics, cut=False)
    total = len(cut_slices(new, confirmed_arrangement(sid, ep)))
    tl = _sgd.ep_timeline(sid, ep) or {"scenes": []}
    if fr:
        d = slicing_drift(sid, ep)
    else:
        # 老时间轴没冻结过：拿写提示词时盖的正文快照（source_body）切一遍当旧切片来比；没快照/没提示词 → 只冻结
        d = 0
        _has_ps = any(str(g.get("prompt") or "").strip() for sc0 in (tl.get("scenes") or []) for g in (sc0.get("segments") or []))
        snap = str(tl.get("source_body") or "")
        if _has_ps and snap.strip() and re.sub(r"\s+", "", snap) != re.sub(r"\s+", "", pics):
            old = [re.sub(r"\s+", "", str(s.get("text") or "")) for s in episode_slices(sid, ep, pics=snap, cut=False)]
            cur = [re.sub(r"\s+", "", str(s.get("text") or "")) for s in new]
            for k in range(max(len(old), len(cur))):
                if k >= len(old) or k >= len(cur) or old[k] != cur[k]:
                    d = k + 1
                    break
    n_void = 0
    if d:
        for sc0 in (tl.get("scenes") or []):
            keep = []
            for g in (sc0.get("segments") or []):
                no = int(g.get("no") or 0)
                if no >= d:
                    n_void += 1
                    if no > total:
                        # 新切法没这一段了：它的片子改名 .dropped，免得 /progress 按坐标又认领回来
                        for _p in {str(g.get("video") or ""), str(g.get("video_hd") or ""), str(g.get("end_frame") or "")}:
                            try:
                                if _p and os.path.isfile(_p):
                                    os.replace(_p, _p + ".dropped")
                            except Exception:
                                pass
                        try:
                            _sn = int(sc0.get("no") or 1)
                            for _p in (_sgd.seg_video_path(sid, ep, _sn, no), _sgd.seg_video_path(sid, ep, _sn, no, hd=True)):
                                if os.path.isfile(_p):
                                    os.replace(_p, _p + ".dropped")
                        except Exception:
                            pass
                        continue
                    for k in ("prompt", "prompt_auto", "prompt_edited", "prompt_edited_at", "tail", "relay", "done_all", "seam", "problems", "critical_problems", "source_text", "plan"):
                        g.pop(k, None)
                    if str(g.get("video") or "").strip():
                        g["stale_video"] = True                           # 旧片留着，按新剧本重出成功才顶替
                keep.append(g)
            sc0["segments"] = keep
        _dbg("剧本改了，切法跟着变", {"从第几段起": d, "作废": n_void, "新总段": total})
    tl["slicing"] = _slicing_record(pics, new)
    _sgd.save_ep_timeline(sid, ep, tl)
    return d, n_void


def _merged_slices(sid, ep, slices, arr=None):
    """短段并进同场戏邻段；不跨剪点（剪到下一话的段和本话的段不并，先并后剪 = 先剪后并）。
    P332：已经出过片的话不改切法（段号会错位、已出的段全标过期）——并完段数和已有段对不上就不并。"""
    from . import seam_v2 as _sv2m, arrangement as _arm
    _cut = _arm.cut_of(arr) if arr else -1
    try:
        _min_sec = float(os.environ.get("V41_SLICE_MIN_SEC") or (8 if _sv2m.on() else 0))
    except Exception:
        _min_sec = 0
    if _min_sec <= 0:
        return slices
    _sl_m = fresh_shotlist(sid, ep)
    from . import shotlist as _slm_m

    def _place_of(s_):
        side = "|后" if (_cut >= 0 and int((s_ or {}).get("beat_index", -1)) > _cut) else ""
        if _sl_m:
            _scn_, _sc_ = _slm_m.scene_for_text(_sl_m, s_.get("text"))
            return (str((_scn_ or {}).get("id") or "") if _sc_ else "") + side
        return str(s_.get("scene_hint") or "") + side
    merged = merge_short_slices(slices, _min_sec, place_of=_place_of)       # P330：短段并进同场戏邻段
    if frozen_slicing(sid, ep):
        return merged                                                       # P339：有冻结记录 → 段号由冻结/漂移管，不走 P332 计数守卫
    try:
        from . import saga_core as _sgm
        _tlm = _sgm.ep_timeline(sid, ep) or {"scenes": []}
        _segm = [g for sc0 in (_tlm.get("scenes") or []) for g in (sc0.get("segments") or [])]
        if any(str(g.get("video") or "").strip() for g in _segm):
            _n_new = len(cut_slices(merged, arr))
            if _n_new != len(_segm):
                _dbg("这一话已出过片，切法不变（并短段会打乱段号）", {"已有段": len(_segm), "并后": _n_new})
                return slices
    except Exception:
        pass
    return merged


def build_timeline(sid, ep, prompts, replace=False):
    """把提示词段写进这一话的 timeline。已出的视频等成片字段一律保住。

    replace=False：按段号更新/追加，别的段不动；True：整表重建（旧成片字段照样搬）。
    新链路一话就是一条连续的段序列，只用一个场景装全部段。
    这是新链路唯一的建表入口——saga_api 的「全部生成」、分镜页的「全部提示词」、
    「生成下面 N 段」都走这里，别再各建一套。
    """
    from . import saga_core as _sc, asset_core as _ac
    ep = int(ep or 1)
    old = _sc.ep_timeline(sid, ep) or {"scenes": []}
    scname = ""
    try:
        _s0 = _ac.list_assets(sid, "scenes") or []
        scname = str((_s0[0] or {}).get("name") or "") if _s0 else ""
    except Exception:
        pass
    segs = [] if replace else [g for sc0 in (old.get("scenes") or [])
                               for g in (sc0.get("segments") or [])]
    by = {int(g.get("no") or 0): g for g in segs}
    # 整表重建时上面的 segs 是空的，比不出"提示词换没换"——旧表单独留一份按段号查（P282）
    old_by = {int(g.get("no") or 0): g for sc0 in (old.get("scenes") or [])
              for g in (sc0.get("segments") or [])}
    _kept_edited = []
    for p in prompts or []:
        n = int(p.get("n") or 0)
        if n <= 0:
            continue
        g = by.get(n)
        if g is None:
            g = {"no": n}
            segs.append(g)
            by[n] = g
        _newp = p.get("prompt") or ""
        # P282：这一段已经有视频、提示词又换了 → 那条片子是按旧剧本出的，标出来让页面显示、让重出认得到。
        # 比对要拿**旧表**里的那一段（整表重建时 g 是刚建的空壳，比不出来）。
        # 标记只在**真的重出成功**时清（见 api/timeline._gen_next）——存的是"当前提示词"不是"出片时那份"，
        # 在这里比"又一样了"就清会把旧片子重新当成新的
        _prev = old_by.get(n) or g
        # 整表重建时 g 是新壳：旧表标过的「按旧剧本出的」要跟着搬过来（视频文件由 merge_segment_media 搬）
        if _prev is not g and _prev.get("stale_video"):
            g["stale_video"] = True
        # P312：这一段用户手改过 → 不覆盖，新写的存进 prompt_auto；用户主动「重编提示词」走别的路（replace=False 单段）
        if _prev.get("prompt_edited") and str(_prev.get("prompt") or "").strip() and _newp.strip() != str(_prev.get("prompt") or "").strip():
            # 不管整表重建还是按段重写（场景改绑后的「按清单重写」实测把手改的第 1 段盖掉了）：手改的一律保留
            g["prompt"] = _prev["prompt"]
            g["prompt_edited"] = True
            g["prompt_edited_at"] = _prev.get("prompt_edited_at")
            g["prompt_auto"] = _newp
            g["problems"] = p.get("problems") or []
            g["critical_problems"] = p.get("critical_problems") or []
            g["source_text"] = p.get("source_text") or ""
            g["over"] = bool(p.get("over"))
            for _k in ("tail", "scene", "chars", "plan", "seconds", "relay", "done_all"):
                if p.get(_k):
                    g[_k] = p[_k]
            if p.get("beat_index") is not None:
                g["beat"] = str(p.get("beat") or "")
                g["beat_index"] = int(p.get("beat_index"))
            g.setdefault("video", "")
            g.setdefault("shot_ids", [])
            _kept_edited.append(n)
            continue
        if str(_prev.get("video") or "").strip() and str(_prev.get("prompt") or "").strip()                 and _newp.strip() != str(_prev.get("prompt") or "").strip():
            g["stale_video"] = True
        g["prompt"] = _newp
        g["problems"] = p.get("problems") or []
        g["critical_problems"] = p.get("critical_problems") or []
        g["source_text"] = p.get("source_text") or ""
        g["over"] = bool(p.get("over"))
        if p.get("tail"):
            g["tail"] = p["tail"]
        if p.get("scene"):
            g["scene"] = p["scene"]
        if p.get("chars"):
            g["chars"] = p["chars"]
        if p.get("plan"):
            g["plan"] = p["plan"]
        if p.get("wardrobe") is not None:
            g["wardrobe"] = p["wardrobe"]
        if p.get("relay"):
            g["relay"] = p["relay"]                              # P331：接力状态/已完成清单落盘，续写时接得上
        if p.get("done_all") is not None:
            g["done_all"] = list(p["done_all"] or [])
        if p.get("seconds"):
            g["seconds"] = p["seconds"]
        if p.get("direction"):
            g["direction"] = str(p.get("direction"))                       # P397：这一段的口述拍法留在段上
        if p.get("shot") or p.get("cut") is not None:                    # P366：节拍字段
            g["shot"] = p.get("shot") or ""
            g["establish"] = bool(p.get("establish"))
            g["cut"] = bool(p.get("cut"))
            _seam = dict(g.get("seam") or {})
            _seam.pop("same_scene_cut", None)
            if p.get("cut") and p.get("establish"):
                _seam["scene_changed"] = True                            # 换地方：出片不带上一段末帧（segment_api 看这个）
            elif p.get("cut"):
                _seam.pop("scene_changed", None)
                _seam["same_scene_cut"] = True                           # P392②：同场景硬切——带末帧只对齐空间/光，机位按本段
            elif "scene_changed" in _seam:
                _seam.pop("scene_changed", None)
            if _seam:
                g["seam"] = _seam
        if p.get("beat_index") is not None:            # 情节段（预览页/检查器按它对预算）
            g["beat"] = str(p.get("beat") or "")
            g["beat_index"] = int(p.get("beat_index"))
        g.setdefault("video", "")
        g.setdefault("shot_ids", [])
    segs.sort(key=lambda g: int(g.get("no") or 0))
    tl = {"scenes": [{"no": 1, "name": scname or ("第%d话" % ep),
                      "title": scname or "分镜", "location": scname,
                      "time_of_day": "", "interior": "", "beats": [], "shots": [],
                      "warn": "", "state_block": "", "segments": segs}]}
    if isinstance(old.get("slicing"), dict):
        tl["slicing"] = old["slicing"]                                    # P339：冻结的切片跟着表走
    _sc.merge_segment_media(sid, ep, tl)
    # P316：开场段（场景板全景 + 人物亮相）按第 1 段的场景/人物建；提示词没变的段保住已出的视频
    try:
        from . import opening as _op, story_core as _stc
        _op_on = _op.enabled((_stc.get_story(sid) or {}).get("settings") or {})
        tl["opening"] = _op.build(sid, ep, old=old.get("opening"), tl=tl) if _op_on else []     # P321：默认不建开场段
    except Exception as _ox:
        tl["opening"] = list(old.get("opening") or [])
        _dbg("开场段没建成", {"err": str(_ox)[:120]})
    _sc.save_ep_timeline(sid, ep, tl)
    try:
        _sc.stamp_source(sid, ep)                      # 盖正文快照、清 timeline_stale（P312 曾把它放到 return 后面，等于没盖）
    except Exception:
        pass
    return _kept_edited


def next_prompts(sid, ep, upto, on_step=None):
    """「生成下面 N 段」的提示词部分：从第一个没提示词的段起，一直写到第 upto 段。

    已有提示词的段不重写（用户改过的更不能动）。返回 (这次新写的段号列表, 整话总段数)。
    """
    from . import saga_core as _sc
    ep = int(ep or 1)
    try:
        _d, _nv = apply_slicing_drift(sid, ep)                              # P339：剧本改了 → 从变了的那段起作废，下面接着重写
        if _d and on_step:
            on_step("剧本改过了，从第 %d 段起重写提示词（作废 %d 段）" % (_d, _nv))
    except Exception as _dx:
        _dbg("切法漂移处理失败", {"err": str(_dx)[:120]})
    tl = _sc.ep_timeline(sid, ep) or {"scenes": []}
    segs = sorted([g for sc0 in (tl.get("scenes") or []) for g in (sc0.get("segments") or [])],
                  key=lambda g: int(g.get("no") or 0))
    have = [int(g.get("no") or 0) for g in segs if str(g.get("prompt") or "").strip()]
    _total0 = total_segments(sid, ep)
    _missing = [k for k in range(1, _total0 + 1) if k not in set(have)]
    start = (_missing[0] - 1) if _missing else _total0                    # 从第一个没提示词的段起写（中间作废的段也补上），不是 max(have)
    tail = ""
    if start > 0 and start in set(have):
        last = next(g for g in segs if int(g.get("no") or 0) == start)
        tail = str(last.get("tail") or "").strip()
        if not tail:
            _ls = [x for x in str(last.get("prompt") or "").splitlines()
                   if x.strip().startswith("镜头")]
            tail = _ls[-1].strip() if _ls else ""
    total = total_segments(sid, ep)
    count = max(0, min(int(upto), total) - start)
    ps = make_h3_prompts(sid, ep, on_step=on_step, start=start, count=count,
                         prev_tail=tail) if count else []
    if ps:
        build_timeline(sid, ep, ps)
    return [p["n"] for p in ps], total


def make_screenplay(sid, ep=1, on_step=None):
    """按第 ep 话原文生成剧本（编剧），存进该话 body（导演读这个）。"""
    ep = int(ep or 1)
    _saga, e = _ep(sid, ep)
    prose = (e or {}).get("prose") or ""
    if not prose.strip():
        raise RuntimeError("第%d话还没有原文，先生成原文" % ep)
    from . import story_core
    settings = (story_core.get_story(sid) or {}).get("settings") or {}
    if on_step:
        on_step("编剧把第%d话原文改编成剧本" % ep)
    from . import asset_core as _ac, screenplay_norm
    chars = _ac.list_assets(sid, "characters")
    # 只给这一话在场的卡（P137）：结构行在场栏 + 正文里出现过名字的卡；全给会把无名角色套成卡名
    try:
        _rows_s = [r for r in (settings.get("plan_rows") or []) if isinstance(r, dict) and str(r.get("one_line") or "").strip()]
        if 1 <= ep <= len(_rows_s):
            _cast_s = [re.sub(r"（[^）]*）", "", x).strip() for x in re.split(r"[、，,／/和与及]", str(_rows_s[ep - 1].get("cast") or "")) if x.strip()]
            _keep_s = [c for c in chars if str(c.get("name") or "") and (
                str(c.get("name")) in prose or any(x and (x in str(c.get("name")) or str(c.get("name")) in x) for x in _cast_s))]
            if _keep_s:
                chars = _keep_s
    except Exception:
        pass
    scn = [str(x.get("name") or "") for x in _ac.list_assets(sid, "scenes") if x.get("name")]
    _names = [c.get("name") for c in chars if c.get("name")]
    # 【保真验收】台词逐字来自正文＋顺序不倒挂（2026-08-28 用户定：完全按正文来）。
    # 不合格带具体问题打回重写，最多三轮，留问题最少的一版。
    best, best_probs = "", None
    fb = ""
    for attempt in (1, 2, 3):     # 有结构表时保真问题不重写（P138，见下面的 break）；只有"输出损坏"才走第 2 轮（P150）
        body = prose_to_screenplay(prose, settings, characters=chars,
                                   scene_names=scn, feedback=fb)
        # 【格式兜底】模型每被禁掉一种写法就发明一种新的——格式这种确定性的事交给代码
        body = screenplay_norm.normalize(body, _names)
        # 台词镜头秒数查表兜底（Qwen 不守表，44字标5秒——节奏快一倍的真根）
        body = screenplay_norm.enforce_dialogue_seconds(body)
        body = fix_traditional(body)           # 繁体字直接转简，不占重试轮次
        # 保真（台词逐字）+ 一致性（场所／目的地／光照）一起验——
        # 剧本原来只查台词，正文里的场所和光照矛盾被原样抄进来没人管
        # （2026-08-29 实测：小屋里冒出走廊、雨天写阳光下，都是抄过来的）
        probs = (screenplay_fidelity(body, prose) + consistency_problems(body)
                 + screenplay_fragmentation(body, prose)
                 + screenplay_wrong_speaker(body, prose)
                 + screenplay_padding(body))
        # 输出损坏（模型偶发只吐一行场景头）：不算保真问题，无条件重写（P150）
        _shot_re = re.compile(r"^[（(][^）)\n]{0,20}[）)]", re.M)
        _shots = len(_shot_re.findall(str(body or "")))
        _broken = _shots < 3 or len(str(body or "")) < max(300, int(len(str(prose or "")) * 0.25))
        if _broken and attempt < 3:
            _dbg("剧本输出损坏，重写", {"镜头行": _shots, "字数": len(str(body or "")), "轮": attempt})
            if best_probs is None:
                best, best_probs = body, probs
            fb = "上一版只输出了场景头、内容严重不全。请输出完整剧本：每一场都要有镜头行（（景别｜秒数）开头）和该有的台词行。"
            continue
        if (best_probs is None or len(probs) < len(best_probs)
                or (_shots >= 3 and len(_shot_re.findall(str(best or ""))) < 3)):
            best, best_probs = body, probs
        if not probs:
            break
        if settings.get("plan_rows"):
            break                                   # D3：有结构表时保真问题只记录不重写（P138）
        fb = "；".join(probs[:6])
        if on_step:
            on_step("剧本保真验收未过（%d 处），重写第 %d 次" % (len(probs), attempt))
    # 【机械修复，无条件跑】用户 2026-08-30 定的原则：
    # qwen 有错**直接改正修复**，而不是堵住让他重来。
    # 原来这一段只在"校验报了错"时才跑，而且改完必须让问题数**变少**才采纳——
    # 于是没有校验器盯着的毛病（说话人一问一答对调）永远修不到，
    # 实测 STORY_070 就这么漏出去了。这几道修复各改各的、只动命中的那一行，
    # 不会互相打架，直接照单跑完。
    #   · 编造的（心声）独白 → 删行（正文没原话，规则本来就要求用无对白镜头）
    #   · 台词被续写长了     → 砍回正文原话
    #   · 说话人一问一答对调 → 按正文的说话动词换回来（台词错是硬错）
    #   · 无名画外音安给了在场角色 → 改回「画外音」
    #   · 只等台词的空镜 8 秒 → 收到 3 秒
    #   · 连续三个以上无台词碎镜 → 合成一个
    best = fix_scene_header(merge_fragmented_shots(fix_idle_shots(fix_wrong_speaker(
        fix_swapped_speaker(trim_continued_dialogue(
            drop_fabricated_inner_voice(best, prose), prose), prose), prose))))
    _pd_log = []
    best, _pd_n = trim_padded_shots(best, prose, log=_pd_log)                       # 剧本注水裁掉（P153）
    if _pd_n:
        _dbg("剧本注水裁剪", {"删": _pd_n, "明细": _pd_log})
    _sp_log = []
    best, _sp_n = fix_speaker_by_context(best, prose, log=_sp_log, cast=_names)     # 台词归属按正文段落改（P146）
    if _sp_n:
        _dbg("台词归属定点修复", {"改了": _sp_n, "明细": _sp_log[:6]})
        if on_step:
            on_step("台词归属修了 %d 处" % _sp_n)
    best, _canon_n = canonicalize_picture_speakers(best, prose, chars)
    if _canon_n:
        _dbg("台词按正文最终校正", {"处数": _canon_n})
        if on_step:
            on_step("台词按正文最终校正 %d 处" % _canon_n)
    best_probs = (screenplay_fidelity(best, prose) + consistency_problems(best)
                  + screenplay_fragmentation(best, prose)
                  + screenplay_wrong_speaker(best, prose)
                  + screenplay_padding(best))
    _hd_log = []
    best, _hd_n = ensure_scene_head_fields(best, names=_names,
                                           cards=_ac.list_assets(sid, "scenes"), log=_hd_log)   # P159②
    if _hd_n:
        _dbg("场景头四行补齐", {"补": _hd_n, "明细": _hd_log[:6]})
    # P164②：记下这份剧本是照着哪份正文写的。正文之后再改，页面就能标「需要更新」，
    # 而不是把旧剧本当成最新成果继续往下游送。
    # 旧剧本进历史版本再换新的（P166①：不许直接覆盖用户已有的成果）
    try:
        _saga_v, _ev = _ep(sid, ep)
        _old = str((_ev or {}).get("body") or "").strip()
        if _old and _old != best:
            _ev.setdefault("versions", []).append({"at": time.time(), "body": _old})
            from . import saga_core as _sgv
            _sgv.save_saga(sid, _saga_v)
    except Exception:
        pass
    _update_ep(sid, ep, body=best, timeline_stale=True, body_stale=False,
               prose_sig=content_sig(prose), body_at=time.time(),
               plan_version=int(settings.get("plan_version") or 0))
    return {"body_len": len(best), "ep": ep,
            "fidelity_problems": best_probs or []}


# ---------- 续写：第 2 话及以后 ----------

_LIMIT_CONCEPT = {
    "不能动|动不了|使不出|无法动": ("不能动", "动不了", "使不出", "无法", "僵", "束缚", "定住",
                                     "抬不起", "落不下", "悬在", "僵在", "凝住", "收住", "生生忍"),
    "不收|不许|不准|禁止": ("不收", "不许", "不准", "禁止", "规矩", "门规", "戒律", "不能带", "破例"),
}


# 落点里最后那个"做完了"的动作。判完成看它，不看整句词面重合。
_FINAL_ACT = re.compile(
    r"(跑开|跑掉|跑了|走开|走了|离开|转身走|摘下|摘了|取下|拿走|拿出|交出|交给|递给|放下|"
    r"合上|关上|推开|拉开|松开|挂断|按下|收起|抱住|牵住|站起|坐下|躺下|停下|松手|点头|摇头|"
    r"答应|拒绝|签了|吞下|咽下|喝完|吃完|睡着|哭了|笑了|亮了|灭了|开了|锁上|"
    r"站住|截住|拦住|追上)")
# 认知类结果（确认、认出、锁定）**不能按字面判**：第1话赵铁柱亲口说了前妻、走了十年、另娶、
# 孩子没人管，"确认"这件事就是写到了，只是正文里没有"确认"两个字。这类只按词面重合判，门槛提到 3/5。
_COG_ACT = re.compile(r"(确认|认出|看清|锁定|记住|明白|知道|意识到|发现)")


def _final_action(text):
    """落点里最后一个完成动作。「甩开他的手跑开」→「跑开」。"""
    hits = _FINAL_ACT.findall(str(text or ""))
    return hits[-1] if hits else ""


def _limit_concepts(limit):
    """这条限制在正文里可能长什么样。词面对不上不等于没写到。"""
    for pat, words in _LIMIT_CONCEPT.items():
        if re.search(pat, str(limit or "")):
            return words
    return tuple(w for w in re.findall(r"[一-龥]{2,4}", str(limit or "")) if len(w) >= 2)


def episode_result(prose, row=None, slot=None):
    """这一话**实际**写成了什么（P173）。确定性提取，不问模型。

    计划里写了不等于真写进去了——下一话的开始状态只能照这个，不能照计划表。
    记三样：结尾那一段（下一话从这里接）、必须写到的东西有没有真的出现、这一话出现了哪些人。
    """
    p = str(prose or "").strip()
    paras = [x.strip() for x in p.split("\n") if x.strip()]
    must, missed = [], []
    from .story_shape import _bigrams
    _pb = _bigrams(p)
    for key in [str((row or {}).get("landing") or ""), str((row or {}).get("change") or "")]:
        key = key.strip()
        if not key:
            continue
        kb = _bigrams(key)
        _last0 = _final_action(key)
        # 有看得见的结果动作 → 按它判；认知类或没有动作词 → 词面重合要到 3/5
        _need = max(2, len(kb) // 3) if _last0 else max(3, int(len(kb) * 0.6))
        ok = bool(kb) and len(kb & _pb) >= _need
        # 光有词面重合不算完成：落点最后那个动作必须真的做了
        # （「女孩甩开李长渊的手跑开」——只写了甩开、没跑开，就不算）
        _last = _last0
        if ok and _last and _last not in p:
            ok = False
            key = key + "（只写到一半：「%s」没发生）" % _last
        (must if ok else missed).append(key)
    # 限制不能按原句词面对——第1话写的是"灵力束缚、无法迈步、悬在半空"，
    # 和「一根手指都不能动」一个字不重，但它就是写到了。按概念词判。
    _lim = str((slot or {}).get("limit") or "").strip()
    if _lim:
        _con = _limit_concepts(_lim)
        (must if any(w in p for w in _con) else missed).append(_lim)
    who = []
    for n in re.findall(r"[一-龥]{2,4}", str((row or {}).get("cast") or "")):
        if n in p and n not in who:
            who.append(n)
    _dm = dialogue_missing(p, row, slot)
    if _dm:
        missed.append(_dm)
    return {"tail": paras[-1][-160:] if paras else "", "done": must, "missed": missed,
            "who": who, "chars": len(p)}


def dialogue_missing(prose, row=None, slot=None):
    """这一话该有正面交谈却一句对白都没有（P177①）。

    不设"至少几轮"的配额——独处、跟踪、观察本来就可以没有对白。
    只在**两个人正面照面、而且这一格是发现/冲突/兑现**时要求有一次交谈。
    """
    p = str(prose or "")
    if len(re.findall(r"[“「][^”」]{1,80}[”」]", p)) > 0:
        return ""
    if str((slot or {}).get("role") or "") not in ("发现", "冲突", "兑现", "落地"):
        return ""
    cast = [x.strip() for x in re.split(r"[、，,／/和与及]", str((row or {}).get("cast") or "")) if x.strip()]
    if len(cast) < 2:
        return ""
    if not all(c in p for c in cast[:2]):
        return ""
    return "这一话 %s 正面照面却一句对白都没有——该谈的事要当面谈一次" % "、".join(cast[:2])


def episode_task(sid, ep, settings=None):
    """第 ep 话的写作任务：六段式（P173）。没有结构表就返回空串。"""
    from . import story_core, saga_core, story_shape as _shape
    ep = int(ep or 1)
    if settings is None:
        settings = (story_core.get_story(sid) or {}).get("settings") or {}
    rows = [r for r in (settings.get("plan_rows") or []) if isinstance(r, dict)]
    if not (1 <= ep <= len(rows)):
        return ""
    row = rows[ep - 1]
    slot = {}
    try:
        sl = _shape.plan_slots(str(settings.get("one_line") or "")).get("slots") or []
        if 1 <= ep <= len(sl):
            slot = sl[ep - 1]
    except Exception:
        pass
    saga = saga_core.get_saga(sid)
    prev = saga_core.episode(saga, ep - 1) if ep > 1 else None
    pres = (prev or {}).get("result") or {}

    out = ["【这一话的写作任务】"]
    if ep == 1:
        out.append("开始状态：故事从头开始。第一段就把人和地方立住，别铺垫。")
    else:
        _tail = str(pres.get("tail") or "").strip()
        out.append("开始状态：上一话**实际**停在这里——" + (_tail or "（上一话还没写）")
                   + "\n  第一段必须先交代这个状态**怎么过去的**（他怎么脱身、东西最后在谁手上、"
                     "隔了多久、人从哪走到哪），交代完再往下写。"
                     "不许直接跳到新场面，也不许重演上一话已经演过的事，开头一句不要和上一话雷同。")
        if pres.get("done"):
            out.append("上一话真的写到了：" + "；".join(pres["done"][:3]))
        if pres.get("missed"):
            out.append("上一话计划里有、但**实际没写进去**的：" + "；".join(pres["missed"][:3])
                       + "（别当成已经发生过）")
    out.append("本话目的：" + (str(slot.get("done") or row.get("one_line") or "")))
    _role = str(slot.get("role") or "")
    _how = {
        "发现": "先让他去做本来要做的事，撞上一个和预想不一样的事实，这一话就停在他确认了这个事实。",
        "冲突": "两边各要一样东西，正面顶一次。写清楚挡在中间的是什么，别让谁轻易让步。",
        "受挫过程": "他做一次尝试，结果不如预期，但**这次尝试让他多知道了一件事**。只写一次尝试，不要反复。",
        "转变过程": "对方先给一个很小的回应（一个动作、一句试探），他接住了。变化写在动作里，不写心理总结。",
        "兑现": "有外力来抢/来要，或者对方自己做一个选择——用**一件具体的事**把关系定下来。",
        "落地": "把上一话定下来的事对外交代一次：该说的话说给该听的人，挡路的规矩在这里有下文。",
        "日常": "写一件前面没演过的小事。靠这件小事让人更懂这两个人，不推进剧情。",
    }.get(_role, "按本话目的写，一件事写透，别铺第二件。")
    _unnamed = [str(p.get("name") or "").strip()
                for p in (settings.get("plan_people") or []) if p.get("unnamed")]
    _named = [str(p.get("name") or "").strip()
              for p in (settings.get("plan_people") or []) if not p.get("unnamed")]
    if _unnamed:
        out.append("怎么称呼：%s 在这个故事里**没有名字**，正文里就这么叫，"
                   "不许给他起名字，更不许把别人的名字（%s）安到他头上。"
                   % ("、".join(_unnamed[:3]), "、".join(_named[:4])))
    out.append("过程安排：" + _how)
    _cast_n = [x.strip() for x in re.split(r"[、，,／/和与及]", str(row.get("cast") or "")) if x.strip()]
    if len(_cast_n) >= 2 and _role in ("发现", "冲突", "兑现", "落地"):
        out.append("这一话 %s 是正面照面：**必须有一次当面谈**，谈的就是本话目的那件事，"
                   "至少一问一答。别用眼神和沉默把整话演完。" % "、".join(_cast_n[:2]))
    else:
        out.append("这一话不必凑对白：一个人跟踪、观察、赶路的时候，可以一句话都不说。")
    _must = [x for x in [str(row.get("change") or "")] if x.strip()]
    if slot.get("limit"):
        # 可数的正面指标：这一话里要有一处"他想做 X，被这条挡住，结果没做成"
        _must.append("这一话里要有一处：他想做一件事，被「%s」挡住，结果**没做成**——"
                     "把他想做什么、怎么被挡住、最后没做成，三样都写出来" % slot["limit"])
    if _must:
        out.append("必须写到：" + "；".join(_must))
    _land = str(row.get("landing") or "").strip()
    out.append("结束状态：这一话演完，下面这件事必须已经**完成**了——" + (_land or "本话目的")
               + "。用你自己的画面把它写出来（「摘下号码布」不能写成「手碰到号码布」）。"
                 "**不许把上面这句话原样抄成最后一句**，那是给你看的说明，不是正文。")
    _facts = []
    # D：这个故事补出来的设定（一句话没写、但已经定下来的），每一话都照这份
    for c in (settings.get("story_canon") or [])[:6]:
        _facts.append(str(c))
    if pres.get("who"):
        _facts.append("上一话在场的人：" + "、".join(pres["who"][:5]))
    for f in (_shape.read_one_line(str(settings.get("one_line") or "")).get("facts") or [])[:3]:
        _facts.append(f)
    if _facts:
        out.append("已确定事实：" + "；".join(_facts))
    return "\n".join(out)


def gen_brief(sid, ep, on_step=None):
    """按前情（前面各话简介 + 上一话结尾）生成第 ep 话的简介。"""
    from . import story_core, asset_core
    ep = int(ep or 1)
    st = story_core.get_story(sid) or {}
    settings = st.get("settings") or {}
    one = str(settings.get("one_line") or st.get("one_line") or "").strip()
    # 结构表里有这一话 → 简介就是用户写的那一行（P82），不问模型
    try:
        from . import story_plan as _spp
        _rows = [r for r in (settings.get("plan_rows") or []) if isinstance(r, dict) and str(r.get("one_line") or "").strip()]
        if 1 <= ep <= len(_rows):
            _brief = str(_rows[ep - 1]["one_line"]).strip()
            _update_ep(sid, ep, brief=_brief, brief_edited_by_user=False)
            if on_step:
                on_step("第%d话简介取自结构表" % ep)
            return {"brief_len": len(_brief), "ep": ep, "from_plan": True}
    except Exception:
        pass
    if on_step:
        on_step("按前情写第%d话简介" % ep)
    prev = _prev_context(sid, ep)
    if not prev:
        # 没有前情 = 其实是第一话。正文已经有了就从正文提炼（正文先行的正路）；
        # 正文还没写才按一句话现写一份（老路兜底）。
        _saga, e1 = _ep(sid, ep)
        _prose = str((e1 or {}).get("prose") or "").strip()
        brief = summarize_brief(_prose, settings) if _prose else write_synopsis(one, settings)
    else:
        chars = asset_core.list_assets(sid, "characters")
        sys = _fill(_ins("续写简介_指令词.txt"), settings, chars)
        user = []
        if one:
            user.append("整部剧的一句话：" + one)
        user.append("【前情】\n" + prev)
        user.append("现在写第 %d 话的故事简介，从上一话的结尾接着往下。" % ep)
        brief = _q(sys, "\n\n".join(user), mt=1200, temperature=0.85)
    _update_ep(sid, ep, brief=brief, brief_edited_by_user=False)
    return {"brief_len": len(brief), "ep": ep}


def _gen_universal_prose(sid, ep, on_step=None):
    from . import universal_writer as uw, story_core, saga_core
    ep = int(ep or 1)
    settings = (story_core.get_story(sid) or {}).get("settings") or {}
    if settings.get("plan_problems"):
        raise RuntimeError("结构尚未通过：" + "；".join(settings["plan_problems"]))
    saga, current = _ep(sid, ep)
    if (current or {}).get("status") == saga_core.LOCKED:
        raise RuntimeError("这一话已锁定，要改先解锁")
    def input_stamp(s, sg):
        return uw.signature({"source": uw.source(s.get("one_line"), s), "rows": s.get("plan_rows"),
                             "version": s.get("plan_version"), "words": s.get("prose_words"),
                             "episodes": [{k: e.get(k) for k in ("no", "prose", "brief", "brief_edited_by_user", "status", "plan_version")}
                                          for e in sg.get("episodes", []) if int(e.get("no") or 0) <= ep]})
    started_with = input_stamp(settings, saga)
    previous = []
    for no in range(1, ep):
        old = saga_core.episode(saga, no) or {}
        text = str(old.get("prose") or "").strip()
        origin, why = episode_source(sid, no, settings, old)
        if not text:
            # P185：上一话压根还没写出来时，别报来源不明——那是在说版本，不是在说缺稿
            raise RuntimeError("第%d话还没有正文，先把它写出来再写第%d话" % (no, ep))
        if origin != "current":
            raise RuntimeError("第%d话没有可接续的当前正文：%s" % (no, why))
        review = old.get("writing_review") or {}
        if review and review.get("source_sig") != uw.signature(uw.source(settings.get("one_line"), settings)):
            raise RuntimeError("故事要求已改，第%d话需要先对齐" % no)
        item = {"话号": no}
        # The preceding chapter stays intact within a bounded context. No planned brief is history.
        if no == ep - 1:
            item["原文" if len(text) <= 5500 else "原文后5500字（截取）"] = text[-5500:]
        elif review.get("prose_sig") == uw.signature(text):
            item["实际摘要"] = review.get("summary", "")
            item["有原文依据的状态"] = [x.get("fact", "") for x in review.get("state", [])]
        else:
            item["原文结尾（截取，非完整状态）"] = text[-500:]
        previous.append(item)
    # Bound old history as well as the immediate chapter; do not silently include all old prose.
    while len(uw.dump(previous)) > 6000 and len(previous) > 1:
        previous.pop(0)
    try:
        from . import oral_story as _os454
        settings = dict(settings, _events=_os454.events_of((current or {}).get("brief") or "", _q, on_step))   # P454①：写手篇幅按归并后的事件
    except Exception:
        pass
    result = uw.write(settings, ep, previous, brief=(current or {}).get("brief") or "", on_step=on_step,
                      brief_override=bool((current or {}).get("brief_edited_by_user")))
    review = result["review"]
    review["source_sig"] = uw.signature(uw.source(settings.get("one_line"), settings))
    review["plan_version"] = int(settings.get("plan_version") or 0)
    if str(settings.get("story_mode") or "").strip().lower() in ("single", "episode", "manual"):
        # P366：口述剧情模式——程序按事件卡核对：缺事件/多数字/多人物 → 一次定向修；越过本话结尾的段砍掉
        try:
            from . import oral_story as _os_
            _ev = _os_.events_of((current or {}).get("brief") or "", _q, on_step)                            # P454①
            if _ev:
                result["prose"], _fid = _os_.fidelity_pass(result["prose"], _ev, _q, brief=(current or {}).get("brief") or "", on_step=on_step)
                review["fidelity"] = _fid
                _update_ep(sid, ep, events=_ev, fidelity=_fid)
        except Exception as _fx:
            _dbg("事件卡核对失败", {"err": str(_fx)[:120]})
    latest = story_core.get_story(sid) or {}
    if input_stamp(latest.get("settings") or {}, saga_core.get_saga(sid)) != started_with:
        review["verdict"] = "unknown"
        review.setdefault("issues", []).append({"detail": "生成期间正文、前文或故事要求已经修改，本轮草稿不覆盖当前内容"})
    # P185：模型核对默认关着，返回 skipped；真正的闸门是确定性检查报出来的 fail。
    # 原来写的是 != "pass"，关掉核对之后每一话都会被判失败。
    # unknown ＝ 生成期间用户改了东西，本轮草稿不许覆盖；fail ＝ 确定性检查报了问题。
    # 只有 pass 和 skipped（默认不调模型核对）才放行。
    if review["verdict"] in ("fail", "unknown"):
        _update_ep(sid, ep, prose_draft=result["prose"], writing_attempt=review,
                   writing_revision=result.get("revision"), writing_revision_error=result.get("revision_error", ""))
        raise uw.DraftNotReady(result)
    saga, current = _ep(sid, ep)
    old_text = str((current or {}).get("prose") or "")
    if old_text and old_text != result["prose"]:
        current.setdefault("prose_versions", []).append({"at": time.time(), "prose": old_text,
                                                         "writing_review": current.get("writing_review")})
        saga_core.save_saga(sid, saga)
    _brief = str(review.get("summary") or "").strip()
    if not _brief or _brief.lower() in ("ok", "pass"):
        try:
            _rows_b = [r for r in (settings.get("plan_rows") or []) if isinstance(r, dict)]
            _brief = str(_rows_b[ep - 1].get("one_line") or "") if 1 <= ep <= len(_rows_b) else ""     # P321：简介 = 规划卡片的「这一话讲什么」
        except Exception:
            _brief = ""
    _update_ep(sid, ep, prose=result["prose"], brief=_brief, brief_edited_by_user=False,
               writing_review=review, writing_attempt={}, prose_draft="",
               writing_revision=result.get("revision"), writing_revision_error=result.get("revision_error", ""),
               result={"tail": result["prose"][-500:], "done": [], "missed": [],
                       "outcomes": review["outcomes"], "state": review["state"], "checked_by": "model"},
               plan_version=int(settings.get("plan_version") or 0), prose_at=time.time(),
               timeline_stale=True, body_stale=bool((current or {}).get("body")),
               writing_request=str(result.get("request") or "")[:40000],     # P318：这一话写手实际收到的请求原文（验收查"改了有没有送到"）
               **_plan_stamp(settings, ep))
    return {"prose_len": len(result["prose"]), "ep": ep, "review": review["verdict"]}


def _plan_stamp(settings, ep):
    """这一话是按整部规划的哪个版本写的（P285）：uid + 内容指纹；写完清 plan_stale。"""
    try:
        from . import whole_plan as _wp
        plan = _wp.confirmed(settings)
        e = _wp.episode_of(plan, ep) if plan else None
        if e:
            return {"plan_uid": e["uid"], "plan_sig": e["sig"], "plan_stale": False}
    except Exception:
        pass
    return {}


def gen_prose(sid, ep, on_step=None):
    """按第 ep 话的简介（+人设+前情）生成这一话的原文。"""
    from . import universal_writer, manual_story, story_core
    if manual_story.enabled((story_core.get_story(sid) or {}).get("settings")):
        return manual_story.write(sid, ep, on_step)
    if universal_writer.enabled() and not _story_v2():
        return _gen_universal_prose(sid, ep, on_step)
    from . import story_core, asset_core
    ep = int(ep or 1)
    st = story_core.get_story(sid) or {}
    settings = st.get("settings") or {}
    _saga, e = _ep(sid, ep)
    brief = (e or {}).get("brief") or ""
    if not brief.strip():
        # 结构表里有这一话 → 简介直接取第 N 行（P123：剧本页一键出剧本不用先点简介）
        try:
            _rows_b = [r for r in (settings.get("plan_rows") or []) if isinstance(r, dict) and str(r.get("one_line") or "").strip()]
            if 1 <= ep <= len(_rows_b):
                gen_brief(sid, ep, on_step=None)
                _saga, e = _ep(sid, ep)
                brief = (e or {}).get("brief") or ""
        except Exception:
            pass
    if not brief.strip() and ep > 1:
        raise RuntimeError("第%d话还没有简介，先生成简介" % ep)
    # 第一话允许没简介：正文先行，按一句话直接创作（2026-08-28 用户定）
    chars = asset_core.list_assets(sid, "characters")
    if _story_v2():
        # 故事层 v2（P82）：第 N 话也走 要素表→六拍→逐拍剧本；一句话 = 结构表第 N 行（没有就用简介），带前情
        from . import story_plan as _sp
        _st = dict(settings)
        _line = ""
        try:
            _line = _sp.apply_plan_rows(_st, ep)          # 顺带设好 pace / _later_events
        except Exception:
            _line = ""
        _rows_n = len([r for r in (settings.get("plan_rows") or []) if isinstance(r, dict) and str(r.get("one_line") or "").strip()])
        if not (1 <= ep <= _rows_n):
            _line = brief.strip() or str(settings.get("one_line") or "")
        _st["one_line"] = _line
        _st["_prev_context"] = _prev_context(sid, ep) if ep > 1 else ""
        # 全剧已知的人（前面各话要素表 + 人物卡）：这一话一句话里没点到的不许出现（P84 外来角色核对）
        try:
            from . import saga_core as _sg
            _kn = [str(c.get("name") or "") for c in (chars or [])]
            _pv = int(settings.get("plan_version") or 0)
            _pl = []
            for _x in (_sg.get_saga(sid).get("episodes") or []):
                if int(_x.get("no") or 0) < ep and (not _pv or int(_x.get("plan_version") or 0) == _pv):
                    _kn += list(((_x.get("elements") or {}).get("_names")) or [])
                    _pl += [l for l in str(_x.get("script_v2") or "").splitlines() if re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip())]
            _st["_prev_lines"] = _pl[-200:]                 # 前面各话的台词，本话不许原样再说（P122）
            _st["_cast_known"] = [n for n in dict.fromkeys(_kn) if n]
        except Exception:
            _st["_cast_known"] = []
        _pl = ""
        try:
            _pl = _cards_digest(sid)
            _pl = _pl[_pl.find("【场景卡】"):] if "【场景卡】" in _pl else ""
        except Exception:
            pass
        if on_step:
            on_step("编剧组写第%d话（按结构表第%d行）" % (ep, ep) if 1 <= ep <= _rows_n else "编剧组写第%d话（按简介）" % ep)
        # 只给这一话在场的人物卡：一句话里点到的 + 主角（第一张卡 / 出现在最多行里的名字）（P91）
        _chars_ep = chars
        try:
            _rows_txt = [str(r.get("one_line") or "") for r in (settings.get("plan_rows") or []) if isinstance(r, dict)]
            _hero = ""
            if _rows_txt:
                _hero = max((str(c.get("name") or "") for c in (chars or [])),
                            key=lambda n: sum(1 for t in _rows_txt if n and n in t), default="")
            _rc = "、".join(_st.get("_row_cast") or [])
            _rcl = [x for x in (_st.get("_row_cast") or []) if x]
            _keep = [c for c in (chars or []) if str(c.get("name") or "") and (
                str(c.get("name")) in _line or str(c.get("name")) in _rc or str(c.get("name")) == _hero
                or any(x in str(c.get("name")) or str(c.get("name")) in x for x in _rcl))]      # 在场栏「服务生」↔卡「女服务生」互相包含也算（P129t）
            if _keep:
                _chars_ep = _keep
        except Exception:
            _chars_ep = chars
        _r2 = _sp.generate_episode(sid, _line, _st, chars=_chars_ep, on_step=on_step, place=_pl)
        _r2["prose"], _r2["script"] = desex_text(_r2["prose"], settings), desex_text(_r2["script"], settings)   # P360
        _update_ep(sid, ep, prose=_r2["prose"], script_v2=_r2["script"], outline=_r2["outline"], review=_r2["review"],
                   elements=_r2["elements"], timeline_stale=True, plan_version=int(settings.get("plan_version") or 0),
                   **({"brief": _r2["synopsis"]} if (_r2.get("synopsis") and not brief.strip()) else {}))
        return {"prose_len": len(_r2["prose"]), "ep": ep, "v2": True}
    if on_step:
        on_step("小说家写第%d话正文" % ep)
    prev = _prev_context(sid, ep)
    sys = _fill(_ins("第一话原文_指令词.txt"), settings, chars)
    user = []
    one = str(settings.get("one_line") or "").strip()
    if one:
        user.append("整部剧的一句话：" + one)
    if prev:
        user.append("【前情（这一话要从这里接着往下写，别重来、别推翻）】\n" + prev)
    if brief.strip():
        user.append("第%d话故事简介：\n%s" % (ep, brief.strip()))
        user.append("按这个简介，写第%d话的完整小说体正文。" % ep)
    else:
        user.append("没有简介。按这句一句话，直接创作第%d话的完整小说体正文。" % ep)
    _task = episode_task(sid, ep, settings)                # P173 六段式写作任务
    if _task:
        user.append(_task)
    _rb = row_boundary_block(settings, ep, cards=chars)
    if _rb:
        user.append(_rb)                                   # 结构行事实单（P132/P138）
    prose = _q(sys, "\n\n".join(user), mt=5200, temperature=0.85)
    prose = strip_onomatopoeia(prose)
    if _rb:
        prose = fix_anachronisms(fix_typos(fix_essay_tail(fix_truncated_tail(prose))), settings)     # P138 减法：有结构表只做确定性清理（P142 加回时代错词）
        prose, _nm = strip_meta_sentences(prose)
        if _nm:
            _dbg("删提示词泄漏句", {"ep": ep, "句": _nm})
        prose, _nl = trim_look_paragraphs(prose)
        if _nl:
            _dbg("削开头外貌句", {"ep": ep, "句": _nl})
        _cc = []
        prose = clean_prose_common(prose, settings, ep, log=_cc)   # P165①：和 write_prose 同一套
        if _cc:
            _dbg("正文公共清理", {"ep": ep, "明细": _cc})
    else:
        prose, _pace_note = story_pace_pass(prose, settings)
        _dbg("序章节奏审校", {"结果": _pace_note, "ep": ep})
        prose, _ev_note = story_event_pass(prose)
        _dbg("事件表审校", {"结果": _ev_note, "ep": ep})
        prose = replace_bad_sentences(prose)
        prose, _nfb = clean_prose_fallback(prose)
    prose, _tb = trim_to_boundary(prose, settings, ep)          # 结构行边界裁剪（P134）
    prose, _lz = ensure_landing_last(prose, settings, ep)       # 落点检查（P147→P176 只记账）
    if _lz:
        _dbg("落点兜底", {"ep": ep, "结果": _lz})
    prose, _fz = finish_landing(prose, settings, ep)            # P176：真缺就接着写到它发生
    if _fz:
        _dbg("落点补写", {"ep": ep, "结果": _fz})
    if _tb:
        _dbg("边界裁剪", {"ep": ep, "结果": _tb})
    # P164①：正文存盘必须写上它出自哪一版结构——续写靠这个判断能不能接上一话结尾
    # 正文刚重写过：已有的剧本一定是照旧正文写的，直接标出来（老剧本没记 prose_sig 也能判）
    try:
        _saga0, _e0 = _ep(sid, ep)
        _had_body = bool(str((_e0 or {}).get("body") or "").strip())
    except Exception:
        _had_body = False
    # P173：记下这一话**实际**写成了什么，下一话的开始状态照这个，不照计划表
    try:
        _rows_r = [r for r in (settings.get("plan_rows") or []) if isinstance(r, dict)]
        _row_r = _rows_r[ep - 1] if 1 <= ep <= len(_rows_r) else {}
        from . import story_shape as _shape_r
        _sl_r = (_shape_r.plan_slots(str(settings.get("one_line") or "")).get("slots") or [])
        _res = episode_result(prose, _row_r, _sl_r[ep - 1] if 1 <= ep <= len(_sl_r) else {})
        if _res.get("missed"):
            _dbg("这一话计划里有但没写进去", {"ep": ep, "缺": _res["missed"]})
    except Exception:
        _res = {}
    prose = desex_text(prose, settings)                          # P360：极致诱惑下性器官名换泛称
    _update_ep(sid, ep, prose=prose, plan_version=int(settings.get("plan_version") or 0),
               prose_at=time.time(), result=_res,
               **({"body_stale": True} if _had_body else {}))
    return {"prose_len": len(prose), "ep": ep}
