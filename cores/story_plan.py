# -*- coding: utf-8 -*-
"""故事层 v2：规划前置、直接写剧本（2026-09-03 用户定）。

一句话 → 要素表 → （分话框架）→ 第一话大纲卡（六拍）→ 画面剧本（直接写给镜头）→ 冷读看懂测试/吸睛测试 → 定向修
全部由 Qwen 完成，代码只做守卫和判断；产物存进这一话：script_v2（剧本）、outline（大纲卡）、review（审校记录），
并派生一份 prose（给下游的覆盖/台词检查用，用户不看）。

设计原则（今天实测）：力气花在写之前；模型只答判断题；不整篇重写，只修一块；范例全部占位。
"""
import io
import json
import re

from . import authoring as au
from . import pace as _pace

_KV = re.compile(r"^\s*[-•·]?\s*([^：:\n]{1,12})\s*[：:]\s*(.+?)\s*$")


def _kv(text):
    out = {}
    for line in str(text or "").splitlines():
        m = _KV.match(line)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


# ───────────────── 项目设定 → 给编剧组看的块 ─────────────────
def genre_spec_of(settings):
    try:
        from . import kits as _k
        return _k.genre_spec(str((settings or {}).get("genre") or "")) or {}
    except Exception:
        return {}


def settings_blocks(settings):
    """世界写法 / 影片类型 / 视点 / 特殊要求 / 尺度，全是给故事层用的文字块。缺什么就空。"""
    s = settings or {}
    out = {}
    try:
        w = au._world_block(s)
        c = au._craft_block(s)
        out["world"] = (w + ("\n" + c if c else "")) if w and not w.startswith("世界：按一句话") else (c or "")
    except Exception:
        out["world"] = ""
    gs = genre_spec_of(s)
    if gs:
        from . import kits as _k
        out["genre_name"] = _k.genre_group(s.get("genre") or "")
        out["genre"] = ("【影片类型：%s】%s\n六拍骨架：%s\n转折：%s\n代价：%s\n节奏：%s" % (
            out["genre_name"], gs.get("structure", ""),
            "；".join("拍%d %s" % (i + 1, b) for i, b in enumerate(gs.get("beats") or [])),
            gs.get("turn", ""), gs.get("stakes", ""), gs.get("pace", "")))
        out["beats"] = list(gs.get("beats") or [])
    else:
        out["genre_name"], out["genre"], out["beats"] = "", "", []
    try:
        out["pov"] = au._pov_writing_block(s)
    except Exception:
        out["pov"] = ""
    out["extra"] = str(s.get("extra_requirements") or "").strip()
    try:
        out["scale"] = au._scale_block(s, ("story",))
    except Exception:
        out["scale"] = ""
    return out


def settings_text(blocks, keys=("world", "genre", "pov", "extra", "scale")):
    parts = []
    for k in keys:
        v = str((blocks or {}).get(k) or "").strip()
        if not v:
            continue
        if k == "extra":
            v = "【用户特殊要求（必须满足）】" + v
        parts.append(v)
    return "\n".join(parts)


# ───────────────── 第 0 步：一句话分析 ─────────────────
_ANALYZE_SYS = """你是短剧总编剧。用户给你一句话故事和这个项目已有的人物卡、场景卡，你把它拆成要素表，一行一项，格式「项名：内容」，不要多余的话。
**铁规：人物一律写人物卡上的名字**（后面括号里写他的身份，例如「李长渊（大师兄）」）；已有场景卡时，地点从场景卡里选一个，照抄卡上的名字。
不同角色不许同名。人物卡为空时才自己起名。
主角：卡上的名字（身份）
主角要什么：这一话里他要达成的具体目标（能拍出来的动作目标）
同伴：和主角站在同一边、一起行动的人（卡上的名字，可以没有就写「无」）。一句话里写了"和某人一起""两人""搭档""同伙"的，那个人就是同伴，**绝不能当对手**
谁挡他：卡上的名字（身份）——对手、障碍或意外，一个。**一句话里写了主角在躲谁、被谁追、被谁抓（警察、追兵、守卫、仇家），对手就是那一方**，不许拿同伴充数；**更不许把主角本人写在这一栏**——主角就算是罪犯、杀手、通缉犯，他也不是自己的对手，追他抓他的那一方才是（警察、警用机甲、赏金猎人、仇家）；这一话如果**确实没有对手**（旅途日常、家人相处、打听消息），这一栏写「无」，不要硬编一个
对手是什么：一句话说清这个对手的身份和它为什么与主角为敌（"地下城的石守卫，被闯入者唤醒，见活人就杀"）——观众要能一眼分清敌我，不能把对手当成同伴
人物关系：主要人物之间是什么关系，用称呼写（师兄妹／主仆／仇人…），带上名字
地点：这一话只用一个地点，有场景卡就照抄卡名
时间与光线：白天/黄昏/夜；光从哪来（晨光／正午日光／烛火／月光…）。有场景卡就照抄卡上写的时间和光线，不许自己换
题材：一两个词
吸睛靠什么：从「人物」「场景」「故事」里选两样，各写一句具体内容——人物写独特外形或身份（用人物卡里有的）；场景写新奇或广阔在哪（**只能用已有场景卡里写了的东西**，不许编新陈设）；故事写反差或一句能镇场的台词
入口事件：第一话第一个画面该是正在发生的什么事（一个动作，不是环境）
核心冲突动作：把这一话的冲突外化成三个看得见的动作，用「；」分开（插话／拉走／挡在中间／夺过／递过去／逼近／后退…）
结尾钩子：停在哪个没做完的动作或没说完的话上
转折事实：拍四左右要让观众知道的一个新事实（身份、物件真假、对手的弱点、对方的目的），它会改变局面；一句话里有就用它，没有就补一个和这个故事有关的。**必须是拍得到的**：一件物（信、玉佩、空箱子）、一个动作（摘面罩、亮出伤口）、或一句点破的话；眼神、心情、担忧这种看不见的不算
代价：主角输了会失去什么（命、人、钱、路），一句话
是否分话：这一句话一话讲得完写「否」；讲不完写「是」并写几话"""


def _names_of(chars):
    return [str(c.get("name") or "").strip() for c in (chars or []) if str(c.get("name") or "").strip()]


def _card_lines(chars, scenes):
    out = []
    if chars:
        out.append("【人物卡】" + "；".join(
            "%s（%s）" % (str(c.get("name") or ""), (str(c.get("identity_anchor") or "")[:24] or "见卡"))
            for c in chars))
    if scenes:
        out.append("【场景卡】" + "；".join(str(x.get("name") or "") for x in scenes if x.get("name")))
    return "\n".join(out)


# 长得像身份而不是名字：以这些词结尾，或整个就是这些词
_ROLE_LIKE = re.compile(
    r"(?:少年|少女|青年|男子|女子|老人|孩子|剑修|剑客|弟子|骑士|法师|术士|猎人|杀手|刺客|镖师|"
    r"司机|店员|外卖员|保安|侍卫|士兵|队长|船长|机师|维修工|拾荒者|流浪者|旅人|商人|学生|"
    r"修士|道士|和尚|尼姑|巫女|王子|公主|国王|女王|将军|统领|使者|信徒|囚犯|逃犯|通缉犯)$"
    r"|^(?:他|她|某人|主角|男主|女主|一个人|无名|路人)$")


def analyze_one_line(one_line, settings=None, chars=None, scenes=None):
    """要素表。人物/地点必须用卡上的名字；模型写了代称就带着名单再问一次。"""
    cards = _card_lines(chars, scenes)
    blk = settings_blocks(settings)
    ctx = settings_text(blk)
    user = "一句话故事：" + str(one_line or "") + (("\n" + cards) if cards else "") + (("\n\n【项目设定】\n" + ctx) if ctx else "")
    _rowblk = _row_block(settings)
    if _rowblk:
        user += "\n\n" + _rowblk
    _pv = str((settings or {}).get("_prev_context") or "").strip()
    if _pv:
        # 多话（P82）：这一话从前情接着往下；人名、关系、已经发生的事照前情，不重来不推翻
        user += "\n\n【前情——这一话从这里接着往下，人名和关系照前情，前面发生过的事不再发生】\n" + _pv[:1500]
    rep = au._q(_ANALYZE_SYS, user, mt=800, temperature=0.3)
    el = _kv(rep)
    names = _names_of(chars)
    def _bad(e):
        if not names:
            return []
        miss = []
        for key in ("主角", "谁挡他"):
            v = str(e.get(key) or "")
            if v and not any(n in v for n in names):
                miss.append(key)
        return miss
    # 对手不能是同伴（用户 2026-09-05：男女主一起逃，女主被判成了对手）
    def _ally_as_foe(e):
        allies = [x.strip() for x in re.split(r"[、，,／/和与]", str(e.get("同伴") or "")) if x.strip() and x.strip() not in ("无", "没有")]
        allies = [re.sub(r"（[^）]*）", "", a).strip() for a in allies]
        foe_head = re.split(r"[——，,（(]", str(e.get("谁挡他") or ""))[0]
        return [a for a in allies if a and len(a) >= 2 and a in foe_head]
    # 对手不能是主角本人（2026-09-05 犯罪片实测：谁挡他=主角自己，因为主角是罪犯）
    def _hero_as_foe(e):
        hero = re.split(r"[——，,（(]", str(e.get("主角") or ""))[0].strip()
        foe_head = re.split(r"[——，,（(]", str(e.get("谁挡他") or ""))[0].strip()
        return bool(hero and len(hero) >= 2 and foe_head and hero in foe_head)
    if _hero_as_foe(el):
        rep4 = au._q(_ANALYZE_SYS + "\n\n★上一次你把主角 %s 自己写成了对手。主角就算是罪犯、杀手、通缉犯，也不是自己的对手；"
                     "「谁挡他」和「对手是什么」要写**追他抓他挡他路的那一方**（警察、警用机甲、追兵、守卫、仇家、赏金猎人）。重写要素表。"
                     % re.split(r"[——，,（(]", str(el.get("主角") or ""))[0].strip(),
                     user, mt=800, temperature=0.2)
        el4 = _kv(rep4)
        if el4.get("谁挡他") and not _hero_as_foe(el4):
            el, rep = el4, rep4
    _af = _ally_as_foe(el)
    if _af:
        rep3 = au._q(_ANALYZE_SYS + "\n\n★上一次你把同伴 %s 当成了对手。同伴是和主角一起行动的人，对手必须是追他们/抓他们/挡他们路的那一方（警察、追兵、守卫、仇家）。重写要素表。"
                     % "、".join(_af), user, mt=800, temperature=0.2)
        el3 = _kv(rep3)
        if el3.get("谁挡他") and not _ally_as_foe(el3):
            el, rep = el3, rep3
    miss = _bad(el)
    if miss:
        rep2 = au._q(_ANALYZE_SYS + "\n\n★上一次你把 %s 写成了代称。这些项必须写下面名单里的名字：%s" % ("、".join(miss), "、".join(names)),
                     user, mt=700, temperature=0.2)
        el2 = _kv(rep2)
        if len(_bad(el2)) < len(miss):
            el, rep = el2, rep2
    # 主角的"名字"是身份描述（少年剑修／女法师／外卖员）→ 大纲写手会把他当无名角色，
    # 一句台词不给他（2026-09-05 东方冒险动漫：全话 3 句台词全是狼吼）。要求起个真名。
    _hero_head = re.split(r"[——，,（(]", str(el.get("主角") or ""))[0].strip()
    if _hero_head and _ROLE_LIKE.search(_hero_head):
        rep5 = au._q(_ANALYZE_SYS + "\n\n★上一次你把主角写成了「%s」——这是身份不是名字。"
                     "主角必须有一个**真实人名**（两到三个字，带姓，例如 林渊、苏清婉、陈默），"
                     "身份放在括号里。人物栏、人物关系栏也用这个名字。重写要素表。" % _hero_head,
                     user, mt=800, temperature=0.4)
        el5 = _kv(rep5)
        _h5 = re.split(r"[——，,（(]", str(el5.get("主角") or ""))[0].strip()
        if _h5 and not _ROLE_LIKE.search(_h5) and 2 <= len(_h5) <= 4:
            el, rep = el5, rep5
            el["_主角起了名"] = _h5
    # 「主角」栏空着，下游判敌我就没了基准（2026-09-05 东方冒险动漫实测 主角=None）
    if not str(el.get("主角") or "").strip():
        _cand = ""
        if names:
            _cand = names[0]
        else:
            _m0 = re.search(r"([一-龥]{2,4})（", str(el.get("人物") or "") + str(el.get("人物关系") or ""))
            _cand = _m0.group(1) if _m0 else ""
        if _cand:
            el["主角"] = _cand
            el["_主角是补的"] = True
    el["_raw"] = str(rep or "")[:1500]
    if not names:
        names = names_from_elements(el)
    el["_names"] = names
    # 节奏档 + 这一话必须发生的事（设计 v2）：先于大纲定下来，下游所有守卫按它开关
    _gf = ground_foe(el, one_line, names)
    if _gf:
        el["_对手落地"] = _gf
    _gt = ground_twist(el, one_line, names)
    if _gt:
        el["_转折落地"] = _gt
    el["_pace"] = _pace.resolve(settings, one_line)
    el["_no_strike"] = _pace.no_strike(one_line)
    el["_events"] = extract_events(one_line)
    if el["_no_strike"]:
        el["_events"] = no_strike_events(el["_events"])
    # 日常档且一句话里主角没有出手动作 → 对手栏钉为「无」，不给模型造敌人的机会
    # （2026-09-05：推进档要求对立，模型就把「村民」填成对手，六拍变成拍桌震慑村民）
    if el["_pace"] == "日常" and not _pace._CONFLICT.search(str(one_line or "")):
        if str(el.get("谁挡他") or "").strip() not in ("", "无", "没有"):
            el["_对手被钉为无"] = str(el.get("谁挡他"))
        el["谁挡他"] = "无"
        el["对手是什么"] = "无"
    el["_forbid"] = list((settings or {}).get("_later_events") or [])
    _pin_row(el, settings, one_line)                       # 结构行落地（P129）
    return el


def _row_block(settings, for_outline=False):
    """结构表这一话那一行的 地点/阻力/落点/变化点/基调，写成给模型看的一块；没有结构行就空串。"""
    s = settings or {}
    place = str(s.get("_row_place") or "").strip()
    res = str(s.get("_row_resistance") or "").strip()
    land = str(s.get("_row_landing") or "").strip()
    chg = str(s.get("_row_change") or "").strip()
    tone = str(s.get("tone") or "").strip()
    if not (place or res or land or chg):
        return ""
    res_t = res or "无"
    soft = tone in ("日常喜剧", "爱情暧昧", "治愈成长")
    if for_outline:
        return ("【这一话的结构行（总编剧定的）】地点：%s——六拍都在这里；阻力：%s%s；落点：%s——拍6 的可见动作就是它；变化点：%s——六拍演完观众看到的就是这个变化。%s"
                % (place or "按一句话", res_t,
                   "（写「无」＝这一话没有对手、没有危险、没有人挡，六拍不许编危险、不许编身份悬念、不许让谁认出谁）" if res_t in ("无", "没有") else
                   ("（这是让事变难的事，**不是敌人**：对方不是对手，主角处理它的办法是等一等、绕一下、再试一次、说句俏皮话）" if soft else "（阻力只有这一个，不再加别的）"),
                   land or "按一句话", chg or "按一句话",
                   ("基调是%s：这是轻松的戏，没有敌人。主角这一话能做的动作只有这些：看、笑、招手、举杯、搭话、递东西、起身让路、坐回去；"
                    "对方能做的只有这些：端盘、上菜、避让、回头、脸红、笑、回一句、走开。台词是俏皮话和日常招呼。"
                    "六拍怎么不重复：**动的是对方、静的是主角**——店员/服务生每拍干一件不同的活（应声、端酒、倒酒、收碗、送菜、回柜台），主角坐着不动，只有眼睛和手里的杯子在动；"
                    "钉了「看」的话，每拍写出主角这一拍**新看到的一样东西**，顺序是 远处的身影→脸→身段→被对方发现自己在看→走远的背影，一拍一个不重复；"
                    "笑点写成动作：被看住了忘了接话、被逮个正着还嘴硬。" % tone) if soft else ("基调是%s。" % tone if tone else "")))
    return ("【这一话的结构行（总编剧定的，照着填）】地点：%s／阻力：%s／落点：%s／变化点：%s%s\n"
            "规矩：「地点」照抄这里的地点；阻力写「无」就是这一话没有对手、没有危险、没有人挡——「谁挡他」「对手是什么」「代价」都写「无」，别编；"
            "「结尾钩子」就是落点；「主角要什么」要能推出变化点，不许写一句话里没有的目的（认出谁、确认身份、找线索这类）。"
            % (place or "按一句话", res_t, land or "按一句话", chg or "按一句话", ("／基调：" + tone) if tone else ""))


def _grounded(txt, src, names=None):
    """txt 里的实词二字块（去掉人名）至少有一个出现在 src 里。"""
    t = str(txt or "")
    for n in sorted([x for x in (names or []) if x], key=len, reverse=True):
        t = t.replace(n, "，")
    e = re.sub(r"[^一-龥]", "", t)
    grams = [e[i:i + 2] for i in range(len(e) - 1)]
    grams = [g for g in grams if g not in _EV_STOP and not re.search(r"[的了着过在是和与把被让给到从或]", g)]
    if not grams:
        return True
    need = 1 if len(grams) <= 3 else 2
    return sum(1 for g in grams if g in str(src or "")) >= need


def _pin_row(el, settings, one_line):
    """要素表按结构行落地（P129，确定性）：地点照结构行；阻力无→对手无、代价无；结尾钩子=落点；
    主角要什么在一句话/变化点里没依据→换成变化点；基调进题材栏。日常喜剧/爱情暧昧/治愈成长 代价一律无。"""
    s = settings or {}
    place = str(s.get("_row_place") or "").strip()
    res = str(s.get("_row_resistance") or "").strip()
    land = str(s.get("_row_landing") or "").strip()
    chg = str(s.get("_row_change") or "").strip()
    tone = str(s.get("tone") or "").strip()
    soft = tone in ("日常喜剧", "爱情暧昧", "治愈成长")
    log = []
    if place and place not in str(el.get("地点") or ""):
        log.append("地点「%s」→「%s」" % (str(el.get("地点") or "")[:10], place))
        el["地点"] = place
    if res in ("无", "没有") or (not res and str(el.get("_pace") or "") == "日常") or soft:
        # 软基调这一话没有敌人（P129e：118 把女服务生当对手→「锁定目标」「别让她跑了」）；结构行的阻力另立一栏
        for k in ("谁挡他", "对手是什么"):
            if str(el.get(k) or "").strip() not in ("", "无", "没有"):
                log.append("%s「%s」→无" % (k, str(el[k])[:12]))
            el[k] = "无"
    if res and res not in ("无", "没有"):
        el["阻力（让事变难的事，不是敌人）"] = res
    el["_no_foe"] = str(el.get("谁挡他") or "").strip() in ("", "无", "没有")
    if land:
        el["结尾钩子"] = land
    if soft or res in ("无", "没有"):
        if str(el.get("代价") or "").strip() not in ("", "无", "没有"):
            log.append("代价「%s」→无" % str(el["代价"])[:12])
        el["代价"] = "无"
    if tone:
        el["题材"] = tone
    if soft:
        _PRED = re.compile(r"猎手|猎物|目标|敌|仇|围堵|逼近|拦住|拦下|堵住|抓|扣住|逃|追|按住|压住|锁定|捕")
        rel = str(el.get("人物关系") or "")
        if _PRED.search(rel):
            log.append("人物关系「%s」→刚认识" % rel[:14])
            el["人物关系"] = re.sub(r"[是为：:，,].*$", "", rel).strip() + "：刚认识的客人和店员，互相有点好感"
        acts = str(el.get("核心冲突动作") or "")
        if _PRED.search(acts):
            _nm = [x for x in (el.get("_names") or []) if x]
            _h = re.split(r"[——，,（(]", str(el.get("主角") or ""))[0].strip() or (_nm[0] if _nm else "主角")
            _o = next((x for x in _nm if x != _h), "对方")
            log.append("核心冲突动作「%s」→搭话/回话/举杯" % acts[:14])
            el["核心冲突动作"] = "%s招手搭话；%s端着盘子回一句；%s举杯笑" % (_h, _o, _h)
        for k in ("入口事件", "结尾钩子"):
            v = str(el.get(k) or "")
            if _PRED.search(v) and k == "入口事件":
                log.append("入口事件「%s」→按一句话" % v[:14])
                el[k] = "按结构行第一件事直接开始"
    want = str(el.get("主角要什么") or "").strip()
    if want and chg and not _grounded(want, str(one_line or "") + chg + land, el.get("_names")):
        log.append("主角要什么「%s」→「%s」" % (want[:14], chg[:14]))
        el["主角要什么"] = chg
    if log:
        el["_结构行落地"] = "；".join(log)


def _is_carry(r):
    return str((r or {}).get("event") or "").startswith("（承接")


_CONTACT = re.compile(r"捏|拽|扣住|按住|按在|抓住|抓起[^，。；]{0,4}(?:手|臂|腕)|搂|摸|搭在[^，。；]{0,4}(?:腰|肩)|拦住|截住|挡住[^，。；]{0,4}去路|逼近|钳制|拉过|拉住|掐|抵住墙|压在"
                      r"|撞[在上向到]|挑起[^，。；]{0,4}下巴|抬起[^，。；]{0,4}下巴|勾起[^，。；]{0,4}下巴|捧[住起][^，。；]{0,4}脸|扯[住过]|拖[过住]"
                      r"|挑起[^，。；]{0,4}(?:发丝|头发|碎发)|拨[^，。；]{0,3}(?:头发|发丝|碎发)|捏[^，。；]{0,3}脸|拍[^，。；]{0,3}(?:肩|屁股|臀)|拦路|拦在|挡在[^，。；]{0,4}(?:面前|身前|路)")
_SOFT_BAD = re.compile(r"狩猎|猎物|猎手|对峙|蓄势待发|即将爆发|警觉|警惕地|锁定目标|目标|敌意|杀气|压迫感|掌控全局|等待下一幕|下一幕|画面定格|画面在这一刻|定格在这一刻|静谧中酝酿|无声的对话|狩猎姿态")
_FIGHT_LINE = re.compile(r"刀|剑|废|杀|看招|血|死|打|揍|砍|滚|眼熟|不对劲|不对|出事|盯上|跟上|有人|别出声|嘘|小心点|快走|来了[^，。]{0,3}人|寨|帮|门派|仇|埋伏|伏兵|敌|追兵|别回头|受惊|逃|藏|盯我|盯着我|跟着我|看我干什么|看什么看|像狼|像虎|像豹|像蛇|像鹰|狼|杀气|似曾相识|怎么在这|别动|站住|别过来|凶|认识你|见过你|在哪见过|果然是|丢命|要命|没命|性命|命就|命硬|我命|是你？|是你\?|果然|原来是")


def soft_strip_contact(text, one_line):
    """软基调：一句话里没有的身体接触分句删掉（P129k：框架编了「轻捏腰侧」，剧本就捏了三次外加拽臂）。返回 (文字, 删了几处)。"""
    src = str(one_line or "")
    n = 0
    out_lines = []
    for l in str(text or "").split("\n"):
        if not l.strip():
            out_lines.append("")                            # 空行是幅的边界，必须留（P129p）
            continue
        if re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip()):
            out_lines.append(l)
            continue
        keep = []
        _cls = re.split(r"(?<=[，。；！？])", l)
        for ci, c in enumerate(_cls):
            m = _CONTACT.search(c) or (_SOFT_BAD.search(c) if ci > 0 or True else None)
            if m and (not _CONTACT.search(c) or not re.search(re.escape(m.group(0)[:1]), src)):
                n += 1
                if ci == 0:
                    _nm0 = re.match(r"^([一-龥]{2,4}?)(?=[左右双伸抬举放端走坐站转看目视身把将朝向])", c)
                    if _nm0:
                        keep.append(_nm0.group(1))          # 幅首分句删了，名字留下（P129r）
                continue
            keep.append(c)
        t = "".join(keep).strip()
        t = re.sub(r"^[，；、]+", "", t)
        t = re.sub(r"[，；]+([。！？])", r"\1", t)
        if t and t[-1] not in "。！？":
            t = t.rstrip("，；") + "。"
        if t:
            out_lines.append(t)
    return "\n".join(out_lines), n


def outline_drop_foreign_lines(outline, allowed):
    """大纲台词栏：说话人不在名单（含别名）里的句子删掉（P129n：食客甲/乙把一话说成了刀客悬疑）；
    同一拍里重复的句子只留一句、栏里夹的「｜」当分隔（P129x）。返回删了几句。"""
    al = _name_aliases(allowed)
    n = 0
    for r in outline or []:
        ls = split_lines(re.sub(r"[｜|]", "／", str(r.get("line") or "")))
        _seen, _uniq = set(), []
        for x in ls:
            k = re.sub(r"[^一-龥]", "", x)
            if k and k in _seen:
                n += 1
                continue
            _seen.add(k)
            _uniq.append(x)
        ls = _uniq
        keep = []
        for x in ls:
            m = re.match(r"^([^：:]{1,8})[：:]", x.strip())
            who = m.group(1).strip() if m else ""
            if who and not any(who == a or who in a or a in who for a in al):
                n += 1
                continue
            keep.append(x)
        if "／".join(keep) != str(r.get("line") or ""):
            r["line"] = "／".join(keep)
    return n


_LOOK_LADDER = [
    ("身影", "{o}在远处干活，{h}第一次看到{o}的身影，目光停住"),
    ("脸", "{o}端着东西走近，{h}看清{o}的脸，手里的动作慢了半拍"),
    ("身段", "{o}转身干活，{h}的目光从脸移到身段"),
    ("发现", "{o}回头撞见{h}在看，{h}把视线挪开又挪回来"),
]


def outline_look_ladder(outline, el, names, one_line):
    """「看」类日常话（P129q）：承接拍按看的递进梯子填事件——远处身影→走近看清脸→转身看身段→被发现。
    只填承接拍；梯子里已经被钉了的事件覆盖的一级跳过。返回改了哪几拍。"""
    if str((el or {}).get("_pace") or "") != "日常":
        return []
    evs = list((el or {}).get("_events") or [])
    if not any(re.search(r"看|盯|望|瞧|打量", e) for e in evs):
        return []
    hero = re.split(r"[——，,（(]", str((el or {}).get("主角") or ""))[0].strip() or ((names or [""])[0])
    other = next((n for n in (el or {}).get("_allowed_cast") or names or [] if n and n != hero), "")
    if not hero or not other:
        return []
    covered = "".join(evs) + str(one_line or "")
    steps = [t for k, t in _LOOK_LADDER if not (k == "脸" and re.search(r"看清[^，。]{0,4}脸|脸", covered) and re.search(r"看清", covered))]
    out = []
    for k, r in enumerate(outline or []):
        if not _is_carry(r) or not steps:
            continue
        t = steps.pop(0).format(h=hero, o=other)
        r["event"] = t
        r["action"] = t
        out.append(k)
    return out


def outline_landing_only_last(outline, landing, names=None):
    """结构行的落点只许出现在最后一拍（P129o：118 拍1 就演了落点「放下酒杯嘴角勾起玩味的笑」）。返回改了哪几拍。
    人名块不算依据（P129t：「服务生」两块把拍1～4 全删了）。"""
    _l = str(landing or "")
    for _n in _name_aliases(names):
        _l = _l.replace(_n, "，")
    e = re.sub(r"[^一-龥]", "", _l)
    grams = [e[i:i + 2] for i in range(len(e) - 1)]
    grams = [g for g in grams if g not in _EV_STOP and not re.search(r"[的了着过在是和与把被让给到从]", g)]
    if len(grams) < 2 or not outline:
        return []
    hit = []
    for k, r in enumerate(outline[:-1]):
        changed = False
        for key in ("event", "action"):
            cls = re.split(r"(?<=[，。；])", str(r.get(key) or ""))
            keep = [c for c in cls if sum(1 for g in grams if g in c) < 2]
            if len(keep) < len(cls):
                t = "".join(keep).strip("，；。 ")
                r[key] = t if t else r.get(key)
                changed = True
        if changed:
            hit.append(k)
    return hit


def soft_clean_outline(outline, one_line):
    """软基调大纲：动作栏里一句话没有的接触分句删；台词里带打斗词的整句删。返回改了哪几拍。"""
    hit = []
    for k, r in enumerate(outline or []):
        changed = False
        for key in ("event", "action"):
            t2, n = soft_strip_contact(str(r.get(key) or ""), one_line)
            if n:
                r[key] = t2 or r.get(key)
                changed = True
        ls = split_lines(r.get("line"))
        ls2 = [x for x in ls if not _FIGHT_LINE.search(re.sub(r"^[^：:]{1,8}[：:]", "", x))]
        # 同一人连说 ≥3 句只留前两句（P129o：两人戏台词要交替）
        ls3, run_who, run_n = [], "", 0
        for x in ls2:
            m = re.match(r"^([^：:]{1,8})[：:]", x.strip())
            who = m.group(1).strip() if m else ""
            run_n = run_n + 1 if who == run_who else 1
            run_who = who
            if run_n >= 3:
                continue
            ls3.append(x)
        if len(ls3) < len(ls):
            r["line"] = "／".join(ls3)
            changed = True
        if changed:
            hit.append(k)
    return hit


def _carry_beat(outline, i):
    if _is_carry(outline[i]):
        return
    prev = ""
    for j in range(i - 1, -1, -1):                        # 最近一条真实事件（跳过已经是承接的拍，不套娃）
        if not _is_carry(outline[j]):
            prev = str(outline[j].get("event") or "")
            break
    r = outline[i]
    r["event"] = "（承接上一拍「%s」，在场的人留在原位继续手上的事，出一个看得见的小变化）" % prev[:14]
    r["action"] = r["event"]
    r["line"] = ""                                          # 原台词是提前演的内容，留着模型就照它写（P129k）


def outline_premature_beats(outline, events, names=None):
    """补的拍（没钉事件）里演了后面才钉的事件 → 改成承接（P129：118 拍2 就让女服务生走到桌前，拍6 又走一遍）。
    人名块不算依据（「女服务生」三个字在每拍都出现）。"""
    pins = pin_events_to_beats(events, len(outline) or 6)
    _nm = sorted([n for n in (names or []) if n], key=len, reverse=True)

    def _strip(t):
        t = str(t or "")
        for n in _nm:
            t = t.replace(n, "，")
        return t

    def _hit(ev, txt):
        e = re.sub(r"[^一-龥]", "", ev)
        grams = [e[i:i + 2] for i in range(len(e) - 1)]
        grams = [g for g in grams if g not in _EV_STOP and not re.search(r"[的了着过在是和与把被让给到从]", g)]
        if not grams:
            return False
        return sum(1 for g in grams if g in txt) >= (1 if len(grams) <= 3 else 2)
    out = []
    for k, r in enumerate(outline or []):
        if k in pins or _is_carry(r):
            continue
        txt = _strip(str(r.get("event") or "") + str(r.get("action") or ""))
        later = [ev for j, ev in pins.items() if j > k]
        if any(_hit(_strip(ev), txt) for ev in later):
            _carry_beat(outline, k)
            out.append(k)
    return out


_MOVE = re.compile(r"起身|站起|走向|走到|走过去|冲[向出过]|追|挤[过开进]|逼近|拦住|跨过|扑|奔|离开|走出|跑")


def outline_daily_moves(outline, el, row_text, names):
    """日常档、结构行里主角没有位移动作 → 补的拍里主角不许起身追人（P129：118 拍4「小豪起身挤开人群」）。"""
    if str((el or {}).get("_pace") or "") != "日常" or _MOVE.search(str(row_text or "")):
        return []
    hero = re.split(r"[——，,（(]", str((el or {}).get("主角") or ""))[0].strip() or ((names or [""])[0])
    pins = pin_events_to_beats((el or {}).get("_events") or [], len(outline) or 6)
    out = []
    for k, r in enumerate(outline or []):
        if k in pins or _is_carry(r):
            continue
        txt = str(r.get("event") or "") + str(r.get("action") or "")
        if hero and hero in txt and _MOVE.search(txt):
            _carry_beat(outline, k)
            out.append(k)
    return out


def strip_subject_bleed(paras, names):
    """段里以 他/她 起头的句子、句内又「看着/望向/盯着 + 本段主语」——这个他不是主语本人（多半是路人），删这一句
    （P129：「他跌坐在旁边的条凳上，满脸惊愕地看着小豪」）。台词行不动。"""
    out = []
    for p in paras or []:
        lines = p.split("\n")
        body = lines[0]
        subj = next((n for n in _name_aliases(names) if body.startswith(n)), "")
        # 主语和宾语同一个人（「女服务生目光紧锁过道，随着女服务生转身」）→ 主语写错了：两人戏换成另一个人（P129i）
        if subj:
            _first = re.split(r"(?<=[。！？])", body)[0]
            _twice = subj in _first[len(subj):]
            if _twice or re.search(r"(随着|看向|望向|锁定|盯着|打量|追随|跟着|凝视|注视)" + re.escape(subj), _first[len(subj):]):
                _others = [x for x in (names or []) if x and x != subj and not (x in subj or subj in x)]
                _pick = guess_subject(body[len(subj):], _others, getattr(strip_subject_bleed, "_cards", None), pronoun="他" if "他" in _first else ("她" if "她" in _first else ""))
                if not _pick and len(_others) == 1:
                    _pick = _others[0]
                if _pick:
                    body = _pick + body[len(subj):]
                    subj = _pick
        if subj:
            sents = re.split(r"(?<=[。！？])", body)
            keep = [x for x in sents if not (re.match(r"^\s*[他她]", x) and re.search(r"(看着|望向|盯着|瞪着|望着|看向|瞥向)" + re.escape(subj), x))]
            if len(keep) < len(sents):
                body = "".join(keep).strip()
        out.append("\n".join([body] + lines[1:]) if body else p)
    return out


def names_from_elements(el):
    """没有人物卡时，从要素表「名字（身份）」里取名字：只看 主角/谁挡他/人物/人物关系，名字前必须是行首或分隔符，2～4 字。"""
    found = []
    # 【别用逐字黑名单】原来是 [门人无至与和的了是在着把用向]——名字里**含**这些字就整个丢，
    # 「赵无极」带个「无」直接没了，「无涯」「和光」「门主」同理。
    # 名单以前从来没被真正用过（_kv_names 不存在），这个坑就一直藏着；
    # P59 让名单生效后，立刻表现为对手的台词被整栏清掉、大纲台词数塌方（2026-09-05）。
    # 只挡两种：整个候选就是个通用词；以及**以助词开头**的明显碎片
    # （「和光」这种以「和」结尾的是正经名字，所以不查结尾——查结尾会把它也误杀）。
    _BAD = re.compile(r"^(主角|对手|同伴|师兄|师妹|师姐|师弟|老板|女子|男子|路人|保安|侍卫|众人|"
                      r"两人|三人|众弟子|弟子|长老|掌门|店家|司机|乘客|警察|追兵|守卫)$"
                      r"|^[的了是在着把用向与及跟]")
    for key in ("主角", "谁挡他", "人物", "人物关系"):
        v = str((el or {}).get(key) or "")
        cands = []
        for m in re.finditer(r"(?:^|[，。；、／/：:（）()\s与和及跟—-])\s*([\u4e00-\u9fa5A-Za-z·]{2,5})（", v):
            cands.append(m.group(1))
        for m in re.finditer(r"([\u4e00-\u9fa5A-Za-z·]{2,4})[与和及跟]([\u4e00-\u9fa5A-Za-z·]{2,4})（", v):
            cands.append(m.group(1))
        for n in cands:
            # 剥前缀助词：只剥**一个**字，且剥完仍要 ≥2 字。
            # 「和和光」里前一个「和」是连接词该剥，后面的「和光」是名字不能再剥；
            # 「和光」单独出现时剥完只剩「光」，那就是剥错了，原样保留（2026-09-05）。
            n = n.strip()
            if len(n) >= 3 and re.match(r"^[与和及跟是的把用向让被对]", n):
                n = n[1:]
            if 2 <= len(n) <= 4 and n not in found and not _BAD.search(n):
                found.append(n)
    if not found:
        for key in ("主角", "谁挡他"):
            v = str((el or {}).get(key) or "")
            m = re.match(r"^([\u4e00-\u9fa5A-Za-z·]{2,4})", v)
            if m and m.group(1) not in found and not _BAD.search(m.group(1)):
                found.append(m.group(1))
    return found[:5]


# ───────────────── 第 2 步：第一话大纲卡（六拍） ─────────────────
_OUTLINE_SYS = """你是短剧编剧。按下面的要素表，给第一话（120～150 秒）写六拍大纲卡。每拍一行，用「｜」分八栏：
拍｜占比｜事件｜可见动作｜台词｜观众此刻知道了什么｜在场与站位｜关键物件在谁手里
· 最后一栏写这一拍**结束时**关键物件（酒杯/信/剑…）在谁手里或放在哪；物件只能在某个人手里或放在某处，不能悬在空中、不能自己移动。
六拍固定为：1 钩子(≤8%) ｜ 2 人物与目标(到25%) ｜ 3 升级一 ｜ 4 升级二 ｜ 5 升级三/决定(到90%) ｜ 6 悬念(最后10%)
规则：
· 拍1 的事件必须是正在发生的动作，带一句台词；不能是走路、天气、张望、介绍长相。
· 拍2 结束前所有人物出场，关系用称呼带出来，目标说出口或做出来。
· 拍3、4、5 各放一个核心冲突动作（要素表里那三个），一拍比一拍重；**六拍是六个不同的事件，同一个动作（泼酒/夺杯/递杯…）只出现在一拍里**，每拍由上一拍的结果引起。
· 拍6 停在没做完的动作或没说完的话上。
· 台词栏只写人物**说出口的话**——「观众此刻知道了什么」是另一栏，绝不能写进台词栏（"石魔像被闯入者唤醒"是叙述，"它醒了！"才是台词）。
· 台词栏写成「名字：台词」，每句 ≤15 字，**每拍 2～3 句**（不同的人说，用「／」分开，例：甲：小心！／乙：它醒了）；全话加起来 12～18 句。
· **台词只给会说话的人**：野兽、魔像、机器这类不会说话的角色不给台词，也不要写「吼！」「嗷！」这种拟声当台词——那不算一句，会让全话台词不够。会说话的人多分几句。
· **拍1 的台词里必须有一句点明对手是什么、为什么危险**（"守卫醒了""它不让我们过去"），让观众一眼分清敌我；后面每拍的台词要推进：说出正在做什么、看到了什么、下一步要干什么，不能全是"小心""快跑"这种空喊。
· 站位栏：**敌我分处两侧**——对手在一侧、主角这一方在另一侧，中间隔几步、中间是什么（空地、碎石、桌子）；再写各自脸朝哪。对手绝不能和主角并排或站在他身旁。（栏内用「、」分隔，**栏内不许出现「｜」**）
只输出六行，不要标题、不要解释。"""


def _outline_sys(settings, use_skeleton=True):
    """六拍骨架按影片类型来；没定类型就用通用六拍。转折拍与代价拍的要求一并写进去。
    use_skeleton=False：用户给了事件，骨架不再决定每拍发生什么（设计 v2）。"""
    blk = settings_blocks(settings)
    sysm = _OUTLINE_SYS
    if (settings or {}).get("_no_foe"):
        # 这一话没有对手（P129e）：点明敌我、敌我分两侧那两条换成没有敌人的写法
        sysm = sysm.replace(
            "· **拍1 的台词里必须有一句点明对手是什么、为什么危险**（\"守卫醒了\"\"它不让我们过去\"），让观众一眼分清敌我；后面每拍的台词要推进：说出正在做什么、看到了什么、下一步要干什么，不能全是\"小心\"\"快跑\"这种空喊。",
            "· 这一话**没有对手、没有敌人**：拍1 的台词说出主角此刻在做什么、看见了什么（轻松的一句）；后面每拍的台词是两个人之间的话——搭话、回一句、玩笑、招呼、问一句，说的都是眼前的事。"
            "台词只给在场名单上的人，路人一句都没有；两个人交替说，同一人不连说两句以上；每句都回应这一拍正在做的动作（她倒酒，他说酒的事；她收碗，他说碗的事）。")
        sysm = sysm.replace(
            "· 站位栏：**敌我分处两侧**——对手在一侧、主角这一方在另一侧，中间隔几步、中间是什么（空地、碎石、桌子）；再写各自脸朝哪。对手绝不能和主角并排或站在他身旁。（栏内用「、」分隔，**栏内不许出现「｜」**）",
            "· 站位栏：写每个人在哪、隔几步、中间是什么（桌子、过道）、各自脸朝哪。（栏内用「、」分隔，**栏内不许出现「｜」**）")
    if not use_skeleton:
        # 用户给了事件：底稿那行「钩子/升级一/升级二/悬念」不许再决定每拍是什么
        sysm = re.sub(r"六拍固定为：.*",
                      "六拍按【这一话必须发生的事】的钉法来：钉了事件的拍就演那件事，没钉的拍承接前后按节奏补；"
                      "拍6 停在一个未完成的动作上。不要套「钩子／升级／决定／悬念」那套模板词。",
                      sysm, count=1)
        sysm += "\n· 拍4 或拍5 如果有要素表的「转折事实」就演出来（看得见的证据或动作）；没有就不硬加。"
        return sysm
    if blk.get("beats") and use_skeleton:
        beats = blk["beats"]
        shares = ["≤8%", "到25%", "到45%", "到70%", "到90%", "最后10%"]
        line = "六拍固定为：" + " ｜ ".join("%d %s(%s)" % (i + 1, b.split("：")[0], shares[i] if i < len(shares) else "") for i, b in enumerate(beats))
        sysm = re.sub(r"六拍固定为：.*", line.replace("\\", "\\\\"), sysm, count=1)
        detail = "\n".join("· 拍%d：%s" % (i + 1, b) for i, b in enumerate(beats))
        sysm = sysm.replace("规则：", "这一话是【%s】。每拍要演的事：\n%s\n规则：" % (blk.get("genre_name", ""), detail), 1)
        sysm += "\n· 拍4 必须把要素表里的「转折事实」演出来（看得见的证据或动作，不能只靠嘴说）；拍3 之前要把「代价」说出口或演出来。"
    else:
        sysm += "\n· 拍4 或拍5 必须把要素表里的「转折事实」演出来（看得见的证据或动作）；拍3 之前要把「代价」说出口。"
    return sysm


def split_lines(v):
    """台词栏 → [一句一句]。支持「／」「/」「；」分隔的多句。"""
    v = str(v or "").strip().strip("“”\"「」")
    if not v:
        return []
    parts = [x.strip() for x in re.split(r"[／/]|(?<=[。！？])\s*(?=[一-龥]{1,8}[：:])", v) if x.strip()]
    return parts or [v]


# 台词识别：只有"人说的话"才是台词（2026-09-04 实测叙述栏被念出来）
_NARR_WORDS = re.compile(r"发现|意识到|明白|得知|知道了|因[^，。]{0,6}(?:而|苏醒)|无效|即杀|见活人|处于|使得|导致|暗示|证明|说明|表明|"
                         r"关系确立|登场|出场|观众|此刻|随之|从而|于是|由于|状态|抗性|属性")
_SPOKEN_HINT = re.compile(r"[！!？?…]|^(?:别|快|走|来|住手|站住|等等|不|放|给|滚|闭嘴|小心|救|help)|"
                          r"(?:吧|吗|呢|啊|呀|啦|嘛|了没|没有)$")


def is_spoken_line(x, names=None):
    """这一栏像不像一句台词。带「名字：」前缀的按内容判；否则要是短口语。"""
    x = str(x or "").strip().strip("“”\"「」")
    if not x:
        return False
    m = re.match(r"^([^：:]{1,8})[：:]\s*(.+)$", x)
    body = m.group(2).strip() if m else x
    who = m.group(1).strip() if m else ""
    # 名单外但长得像名字（老板 / 赵掌柜 / K-9）也算说话人——名单常不全（P78：这一条把对手和机器人的台词全丢了）
    if names and who and who not in names and not re.fullmatch(r"[一-龥A-Za-z0-9·\-]{2,6}", who):
        return False
    body = body.strip("“”\"「」")
    if not body:
        return False
    if _NARR_WORDS.search(body):
        return False
    if re.search(r"手里|手中|在桌|桌面|怀里|口袋|地上|（|\(|物件|阴影|镜头|站位", body):
        return False
    # 句里出现人名当主语（"艾拉发现…""石魔像因…"）→ 是叙述不是台词
    if names and any(re.match(r"^%s" % re.escape(n), body) for n in names if n):
        return False
    if len(body) > 16:
        return False
    if m:
        return True
    return len(body) <= 12 and bool(_SPOKEN_HINT.search(body))


def _clean_line_cell(cell, names=None):
    """台词栏 → 规范化的「名字：台词／名字：台词」；不是台词的整栏清空。"""
    names = [n for n in (names or []) if n]
    out = []
    for v in split_lines(cell):
        v = v.strip().strip("“”\"「」")
        if not is_spoken_line(v, names):
            continue
        m = re.match(r"^([^：:]{1,8})[：:]\s*(.+)$", v)
        if m:
            who, body = m.group(1).strip(), m.group(2).strip().strip("“”\"「」")
            # 「艾拉：艾拉发现…」这种说话人和内容重复 → 去掉内容里的名字前缀
            for n in names:
                if body.startswith(n):
                    body = body[len(n):].lstrip("，,：: ")
            # 说话人不在名单里 → 名单本身可能不全（要素表没写全、别名、临时角色），
            # 只要它长得像个名字就留着。以前名单永远是空所以从不过滤；
            # P59 让名单生效后，对手的台词被整栏静默删掉（2026-09-05 东方爽文电影实测）。
            _looks_name = bool(re.fullmatch(r"[一-龥A-Za-z0-9·\-]{2,6}", who))   # K-9 这种也算（P78）
            if body and (not names or who in names or _looks_name):
                out.append("%s：%s" % (who, body))
        else:
            out.append(v)
    return "／".join(out)


_EVENTS_SYS = """你是短剧编剧。把下面这一话的一句话拆成「这一话要发生的事」，按发生顺序一行一条，最多 6 条。
· 只写一句话里**明确写到**的事；不补、不猜，不加原文没有的人物、地点、物件；
· 每条 ≤20 字，人名或动词开头（例：「四人沿路聊天赶路」「进村找客栈歇脚」「听到有人说孩子被抓」）；
· 外貌、性格、关系、画风、氛围这些不是事件，跳过；
· 后面几话才发生的事（原文里写着"后面""第二话""之后"的）不要写进来。
只输出条目，格式「1｜事件」。"""


def _event_anchors(ev, src):
    """事件里 2～4 字的内容词，有几个在原文里出现。凭空编的事件一个都对不上。"""
    words = set(re.findall(r"[一-龥]{2,4}", str(ev or "")))
    src = str(src or "")
    return [w for w in words if w in src]


def extract_events(one_line):
    """一句话 → 这一话必须发生的事（逐条核对原文依据词，对不上的删）。"""
    try:
        rep = str(au._q(_EVENTS_SYS, str(one_line or ""), mt=400, temperature=0.2) or "")
    except Exception:
        return []
    out = []
    for line in rep.splitlines():
        m = re.match(r"^\s*(\d+)\s*[｜|.．、:：]\s*(.+)$", line.strip())
        if not m:
            continue
        ev = re.sub(r"[。！？]+$", "", m.group(2).strip())[:40]
        if ev and _event_anchors(ev, one_line):
            out.append(ev)
    # 事件顺序照一句话里出现的位置（P95：模型把「骂一句就跑」排到了「认出」前面）
    _clauses = [c for c in re.split(r"[，。；！？,;]", str(one_line or "")) if c.strip()]

    def _pos(ev):
        # 定位到命中二字块最多的分句（并列取靠后的）；出现 ≥3 次的块（人名）不算（P102：贪心 4 字块常一个都对不上）
        e = re.sub(r"[^一-龥]", "", str(ev or ""))
        ws = {e[i:i + 2] for i in range(len(e) - 1)}
        ws = [w for w in ws if w not in _EV_STOP and str(one_line).count(w) and str(one_line).count(w) < 3]
        best, best_n = 10 ** 6, 0
        for i, c in enumerate(_clauses):
            n = sum(1 for w in ws if w in c)
            if n and n >= best_n:
                best, best_n = i, n
        return best
    out = sorted(out[:6], key=_pos)
    return out


def no_strike_events(events):
    """「一根手指都不能动／不能动手」是"忍住不出手"，不是身体动不了——事件原话钉进拍里会被照字面演（P79）。
    预览和生成链共用这一个改写（P81）。"""
    return [re.sub(r"(却|但|可)?(忍住)?(一根手指(都)?)?(不能动手|不能动|动不了|动弹不得)|(却|但|可)?忍住(?!没出手)",
                   "忍住没出手，手按在剑柄上又松开", str(e)) for e in (events or [])]

_FOE_ROLES = re.compile(r"摊主|邻人|人牙子|二师兄|三师兄|师兄|师姐|长老|弟子|守卫|店家|掌柜|官兵|山贼|土匪|路人|村民|老板|司机|保安|警察|追兵|仇家|管家|侍卫|门房|护卫|镖师|杀手|机甲|怪物|巨熊|巨兽|魔物|敌人|富二代|老板娘|房东|债主|打手")


def ground_foe(el, one_line, names=None):
    """对手必须是这一话一句话里的人（P83）。前情里的旧对手不算——第 3 话的对手是摊主，不是上一话的赵铁柱。
    返回改动说明（空串＝没改）。"""
    one = str(one_line or "")
    foe = str(el.get("谁挡他") or "").strip()
    if not foe or foe in ("无", "没有"):
        # 对手填了无，但这一话不是日常、一句话里有别的人物表名字 → 对手就是他（P110）
        if _pace.detect(one) != "日常" or _pace._CONFLICT.search(one):
            _side = str(el.get("主角") or "") + str(el.get("同伴") or "")
            _cand = [n for n in (names or []) if n and len(n) >= 2 and n in one and n not in _side]
            if _cand:
                _cand.sort(key=lambda n: one.find(n))
                el["谁挡他"] = _cand[0]
                el["对手是什么"] = _cand[0]
                return "对手填了无，这一话有对立，改成一句话里的「%s」" % _cand[0]
        return ""
    head = re.split(r"[——，,（(：:]", foe)[0].strip()
    note = ""
    if head and head not in one and not any(n in one for n in (names or []) if n and n in head):
        # 一句话里有人物表上的名字、又不是主角/同伴 → 对手就是他（P93：第 11 话赵铁柱上门要钱，别改成无）
        _side = str(el.get("主角") or "") + str(el.get("同伴") or "")
        _cand = [n for n in (names or []) if n and len(n) >= 2 and n in one and n not in _side]
        if _cand:
            _cand.sort(key=lambda n: one.find(n))
            note = "对手「%s」不在这一话里，改成一句话里的「%s」" % (head, _cand[0])
            el["谁挡他"] = _cand[0]
            el["对手是什么"] = _cand[0]
            return note
        _hero_txt = str(el.get("主角") or "") + str(el.get("同伴") or "")
        m = None
        for mm in _FOE_ROLES.finditer(one):
            _pre = one[max(0, mm.start() - 2):mm.start()]
            if re.search(r"[是为乃]$|成为$|叫$", _pre) or mm.group(0) in _hero_txt:
                continue                                   # 「她是天云门弟子」说的是身份，不是对手（P112）
            m = mm
            break
        if m:
            note = "对手「%s」不在这一话里，改成一句话里的「%s」" % (head, m.group(0))
            el["谁挡他"] = m.group(0)
            el["对手是什么"] = m.group(0)
            return note
        if not _pace._CONFLICT.search(one) and not _pace._MID.search(one):
            note = "对手「%s」不在这一话里，这一话也没冲突，改成无" % head
            el["谁挡他"], el["对手是什么"] = "无", "无"
            return note
        note = "对手「%s」不在这一话里（保留名字，删掉编的身份）" % head
        el["谁挡他"] = head
        return note
    # 括号身份：词一个都对不上一句话和人物卡 → 删（「隐姓埋名的师兄/挑夫」）
    m = re.search(r"[（(]([^）)]*)[）)]", foe)
    if m:
        ident = m.group(1)
        src = one + "".join(names or [])
        anchors = [w for w in re.findall(r"[一-龥]{2,4}", ident) if w in src]
        if not anchors:
            el["谁挡他"] = (foe[:m.start()] + foe[m.end():]).strip("—— ,，")
            m2 = re.search(r"([一-龥]{2,3}的(?:丈夫|妻子|父亲|母亲|儿子|女儿|师兄|师妹|弟子))", one)
            if m2 and head and head in one:
                el["谁挡他"] = "%s（%s）" % (head, m2.group(1))
            note = "对手身份「%s」一句话里没有，删掉" % ident
    return note

_SYN = [(r"逃避逮捕|躲避追捕|逃避追捕|甩开追兵|甩开警|追兵|逮捕|警用无人机|巡逻机甲", "追捕"), (r"洗干净|洗净|清洗|冲刷掉|冲洗|沐浴|擦洗", "洗澡"), (r"照料|看护|喂养|喂食|喂饭|喂热汤|悉心照顾", "照顾"), (r"带回|带走|带在身边|带着|领着|牵着", "带着"),
        (r"看见|瞧见|望见|注视到|目光定格在|认出", "发现"), (r"御剑|飞行|腾空|驭剑", "御剑"), (r"歇脚|住店|投宿|落脚", "歇脚"),
        (r"袖口|衣袖|袖角|袖摆", "袖子"), (r"攥住|攥着|拽住|拽着|勾住|勾着|拉住|拉着|抓住|抓着|扯住|扯着", "牵"),
        (r"馍|饼子|干粮", "馒头"), (r"坟头|墓前|墓碑", "坟前"), (r"跪下|跪倒|双膝落地|屈膝", "跪"), (r"石块|巨石|岩石", "石头")]


def _syn_norm(t):
    t = str(t or "")
    for pat, rep in _SYN:
        t = re.sub(pat, rep, t)
    return t


def ending_leak(parts, spans, rows, events, names=None):
    """最后一拍钉的事件（这一话的落点）在前面几拍里提前出现了：返回 {拍下标: 事件}。
    近义词归一后数实词二字块（不含人名、虚词），≥2 个命中才算（P84：第 6 话「牵袖」从第 2 幅就抖出来了）。"""
    pins = pin_events_to_beats(events, len(rows) or 6)
    if not pins:
        return {}
    last_k = max(pins)
    if last_k < 2:
        return {}
    ev = _syn_norm(pins[last_k])
    e = re.sub(r"[^一-龥]", "", ev)
    for n in (names or []):
        e = e.replace(n, "")
    grams = {e[i:i + 2] for i in range(len(e) - 1)}
    grams = {g for g in grams if g not in _EV_STOP and not re.search(r"[的了着过在是和与把被让给到从他她它们上路山两人第一次]", g)}
    # 这一话别的事件里也有的词（女孩／馒头）不算结尾独有，去掉（P88）
    others = _syn_norm("".join(str(v) for k, v in pins.items() if k != last_k))
    grams = {g for g in grams if g not in others}
    out = {}
    for k in range(0, min(last_k - 2, len(spans))):        # 只查前面几拍；紧挨结尾的两拍是铺垫，不算提前（P89）
        a, b = spans[k]
        txt = _syn_norm("\n".join(parts[a:b]))
        hit = [g for g in grams if g in txt]
        if len(hit) >= 2:
            out[k] = pins[last_k]
    return out


def foreign_cast(parts, allowed, known):
    """剧本里出现了这一话不该有的人：known 里、不在 allowed 里的名字。返回 {幅下标: [名字]}。"""
    bad = [n for n in (known or []) if n and n not in (allowed or []) and len(n) >= 2]
    out = {}
    for i, p in enumerate(parts or []):
        narr = "\n".join(l for l in p.split("\n") if not re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip()))
        # 只认这个人**在场做事**：句首／分句首是他的名字（P89：台词里说起他不算串人）
        hit = [n for n in bad if re.search(r"(?:^|[。；！？，\n]\s*)%s(?!的|说|那|这)" % re.escape(n), narr)]
        if hit:
            out[i] = hit
    return out


def allowed_cast(el, one_line, names, row_cast=None):
    """这一话允许在场的人：一句话里点到的 + 主角 + 同伴 + 框架表在场栏（P102）。"""
    src = str(one_line or "") + str(el.get("主角") or "") + str(el.get("同伴") or "") + "、".join(row_cast or [])
    # 卡名和在场栏互相包含也算同一个人（在场栏写「服务生」、卡叫「女服务生」，P129j）
    out = [n for n in (names or []) if n in src or any(c and (c in n or n in c) for c in (row_cast or []))]
    for c in (row_cast or []):
        if c and c not in out and not _ROLE_WORDS.search(c) and not any(c in n or n in c for n in out):
            out.append(c)
    return out

_TWIST_CUE = re.compile(r"却|竟|已|原来|其实|发现|得知|认出|才知道|没想到|另娶|不认|不肯|不收|说不知道|病死|失踪")


def ground_twist(el, one_line, names=None):
    """转折事实必须来自一句话（P85：模型编了"赵铁柱是失踪的师兄"，整话跑偏）。返回改动说明。"""
    tw = str(el.get("转折事实") or "").strip()
    if not tw or tw in ("无", "没有"):
        return ""
    one = str(one_line or "")
    src = one
    body = tw
    for n in (names or []):
        body = body.replace(n, "")
    def _clause_ok(c):
        ws = [w for w in re.findall(r"[一-龥]{2,4}", c) if w not in _EV_STOP]
        if not ws:
            return False
        hit = [w for w in ws if w in src]
        return len(hit) * 2 >= len(ws)                      # 实词一半以上有依据
    clauses = [c.strip("，,、；;。 ") for c in re.split(r"[，,、；;]|而是|但是|其实|并非", body) if c.strip("，,、；;。 ")]
    kept = [c for c in clauses if _clause_ok(c)]
    if kept and len(kept) == len(clauses):
        return ""
    if kept and len(re.sub(r"[^一-龥]", "", "，".join(kept))) >= 4:
        el["转折事实"] = "，".join(kept)
        return "转折事实删掉了没依据的分句：「%s」→「%s」" % (tw[:30], el["转折事实"][:30])
    # 从一句话里挑一句带转折线索的
    sents = [x.strip() for x in re.split(r"[，。；；！？,;]", one) if x.strip()]
    cand = next((x for x in sents if _TWIST_CUE.search(x)), "")
    el["转折事实"] = cand if cand else "无（按一句话演，不加反转）"
    return "转折事实「%s」一句话里没有，改成「%s」" % (tw[:30], el["转折事实"][:30])


_ACT_VERB = re.compile(r"拍(?!摄)|夺|递|抓|拉(?!开距离)|推|按|扣|拧|甩|扔|掷|塞|抢|接|举|挥|劈|砸|踹|踢|跪|抱|搂|拽|扯|掐|捂|(?<!挺)拔|掏|摔|撞|拦|挡|付|赔|掀|拨|摩挲|(?<!挺)抵|亮出|掰|折|断|拾|拣|捡")


def repeated_actions(parts, spans, names=None):
    """同一核心动作在 ≥3 幅、跨 ≥2 拍重复演（P85：碎银拍案板 ×4、夺杖 ×3）。
    返回 {拍下标: [短语]}——只标第一次出现之后的拍。"""
    def _beat_of(i):
        for k, (a, b) in enumerate(spans or []):
            if a <= i < b:
                return k
        return -1
    occ = {}
    for i, p in enumerate(parts or []):
        narr = "\n".join(l for l in p.split("\n") if not re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip()))
        for n in (names or []):
            narr = narr.replace(n, "")
        t = re.sub(r"[^一-龥]", "，", narr)
        seen = set()
        for seg in t.split("，"):
            for j in range(len(seg) - 3):
                g = seg[j:j + 4]
                if g in seen or not _ACT_VERB.search(g) or re.search(r"[的了着过在是和与把被让给到从他她它们]", g):
                    continue
                seen.add(g)
                occ.setdefault(g, []).append(i)
    out = {}
    for g, idx in occ.items():
        beats = sorted({_beat_of(i) for i in idx if _beat_of(i) >= 0})
        if len(idx) >= 3 and len(beats) >= 2:
            for k in beats[1:]:
                out.setdefault(k, []).append(g)
    return out


def drop_unknown_speakers(script, names, one_line, log=None):
    """说话人既不在人物表、也不在一句话里 → 这行台词删掉（P85：路人／老翁／农妇／弟子甲乙丙丁抢戏）。"""
    out, n = [], 0
    one = str(one_line or "")
    for l in str(script or "").split("\n"):
        m = re.match(r"^([^\n：:]{1,8})[：:](.+)$", l.strip())
        if m:
            who = re.sub(r"（[^）]*）", "", m.group(1)).strip()
            if who not in ("画外音",) and not any(nm in who or who in nm for nm in (names or []) if nm) and who not in one:
                n += 1
                if log is not None:
                    log.append("删名单外台词：%s：%s" % (who, m.group(2)[:12]))
                continue
        out.append(l)
    return "\n".join(out), n

def strip_foreign_from_outline(rows, cast, all_names):
    """大纲里不在场的人：删掉含那个名字的分句；整拍空了就写成承接（P92 确定性兜底）。返回改过的拍下标。"""
    foreign = [n for n in (all_names or []) if n and n not in (cast or []) and len(n) >= 2] if cast else []
    if not foreign:
        return []
    fixed = []
    for i, r in enumerate(rows or []):
        changed = False
        for key in ("event", "action", "blocking", "line"):
            v = str(r.get(key) or "")
            if not any(n in v for n in foreign):
                continue
            sep = "／" if key == "line" else "，"
            parts = [c for c in re.split(r"[，；、／/]", v) if c.strip()]
            keep = [c for c in parts if not any(n in c for n in foreign)]
            r[key] = sep.join(keep)
            changed = True
        if changed:
            if not str(r.get("event") or "").strip():
                r["event"] = "（承接上一拍）" + "、".join(cast) + "继续这一话的事"
            if not str(r.get("action") or "").strip():
                r["action"] = r["event"]
            fixed.append(i)
    return fixed

_THREAT = re.compile(r"魔像|石魔|魔物|怪物|妖|魔|兽|敌人|杀手|刺客|袭击|偷袭|埋伏|匪|山贼|盗贼|劫匪")


def daily_threat_rows(rows, elements):
    """日常档、对手为无：大纲里哪些拍硬塞了威胁。返回 [(拍下标, 命中词)]。"""
    el = elements or {}
    if str(el.get("_pace") or "") != "日常" or str(el.get("谁挡他") or "无").strip() not in ("", "无", "没有"):
        return []
    out = []
    for i, r in enumerate(rows or []):
        t = str(r.get("event") or "") + str(r.get("action") or "") + str(r.get("line") or "")
        m = _THREAT.search(t)
        if m:
            out.append((i, m.group(0)))
    return out


def strip_daily_threat(rows, elements):
    """兜底：删掉含威胁词的分句／台词句。返回改过的拍下标。"""
    hits = daily_threat_rows(rows, elements)
    fixed = []
    for i, _w in hits:
        r = rows[i]
        for key in ("event", "action", "blocking"):
            v = str(r.get(key) or "")
            if _THREAT.search(v):
                keep = [c for c in re.split(r"[，；、]", v) if c.strip() and not _THREAT.search(c)]
                r[key] = "，".join(keep)
        v = str(r.get("line") or "")
        if _THREAT.search(v):
            r["line"] = "／".join(c for c in split_lines(v) if not _THREAT.search(c))
        if not str(r.get("event") or "").strip():
            r["event"] = "（承接上一拍）" + "、".join(elements.get("_allowed_cast") or []) + "继续这一话的事"
        if not str(r.get("action") or "").strip():
            r["action"] = r["event"]
        fixed.append(i)
    return fixed


def drop_daily_threat_lines(script, elements, log=None):
    """日常档、对手无：台词行含威胁词 → 删（P94）。"""
    el = elements or {}
    if str(el.get("_pace") or "") != "日常" or str(el.get("谁挡他") or "无").strip() not in ("", "无", "没有"):
        return script, 0
    out, n = [], 0
    for l in str(script or "").split("\n"):
        if re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip()) and _THREAT.search(l):
            n += 1
            if log is not None:
                log.append("删日常威胁台词：" + l.strip()[:20])
            continue
        out.append(l)
    return "\n".join(out), n

_BODY_START = re.compile(r"^(?:左|右|双)?(?:手|臂|脚|腿|肩|膝|眉|眼|目光|视线|喉结|嘴角|嘴唇|指尖|指节|指腹|掌心|下巴|鼻尖|身体|身子|脊背|胸膛|上半身|腰|脖颈|胸|头|背)")


def _name_aliases(names):
    """卡名的别名：去掉性别/身份前缀后的尾巴（女服务生→服务生）、括号前的部分。"""
    out = []
    for n in (names or []):
        n = str(n or "").strip()
        if not n:
            continue
        out.append(n)
        m = re.match(r"^(?:女|男|小|老|大)(.{2,})$", n)
        if m:
            out.append(m.group(1))
    return sorted(set(out), key=len, reverse=True)


_FP_STOP = {"一件", "一条", "一双", "一枚", "一个", "上身", "下身", "外罩", "内穿", "腰间", "脚踩", "材质", "颜色", "整体", "看起", "起来", "显得", "带着", "有一", "一道"}


def card_fingerprints(cards):
    """每张人物卡的衣着/外貌/标志物 → 二字块指纹 {名字: set}；同时给 {名字: 性别}（P129v）。"""
    fps, sex = {}, {}
    for c in cards or []:
        nm = str((c or {}).get("name") or "").strip()
        if not nm:
            continue
        txt = " ".join(str((c or {}).get(k) or "") for k in ("clothing", "appearance_details", "props", "hair", "build", "identity_anchor"))
        e = re.sub(r"[^一-龥]", " ", txt)
        grams = set()
        for w in e.split():
            grams |= {w[i:i + 2] for i in range(len(w) - 1)}
        grams -= _FP_STOP
        grams = {g for g in grams if not re.search(r"[的了着在是和与把被让给到从或]", g)}
        fps[nm] = grams
        sx = str((c or {}).get("sex") or (c or {}).get("gender") or "")
        sex[nm] = "女" if re.search(r"女|她", sx) else ("男" if re.search(r"男|他", sx) else "")
    # 两张卡共有的块不算指纹
    for a in list(fps):
        for b in list(fps):
            if a != b:
                fps[a] = fps[a] - (fps[a] & fps[b]) if False else fps[a]
    shared = set()
    keys = list(fps)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            shared |= fps[keys[i]] & fps[keys[j]]
    for k in keys:
        fps[k] = fps[k] - shared
    return fps, sex


def guess_subject(text, names, cards=None, pronoun=""):
    """这一段文字的主语是谁：按卡的指纹命中数；有 他/她 就先按性别筛；分不出返回空串。"""
    fps, sex = card_fingerprints(cards)
    cands = [n for n in (names or []) if n]
    if pronoun in ("他", "她") and sex:
        want = "男" if pronoun == "他" else "女"
        c2 = [n for n in cands if sex.get(n) == want]
        if len(c2) == 1:
            return c2[0]
        if c2:
            cands = c2
    if not fps:
        return ""
    e = re.sub(r"[^一-龥]", " ", str(text or ""))
    grams = set()
    for w in e.split():
        grams |= {w[i:i + 2] for i in range(len(w) - 1)}
    score = sorted(((len(grams & fps.get(n, set())), n) for n in cands), reverse=True)
    if score and score[0][0] >= 2 and (len(score) == 1 or score[0][0] > score[1][0]):
        return score[0][1]
    return ""


def fix_para_subjects(script, names, cards=None):
    """幅首没有名字（主语被删句删掉了）→ 补主语（P129f）。台词行不动。返回 (剧本, 补了几处)。
    P129k：名字带别名；不是 他/她 开头的，只补「手/目光/身体…」这类肢体开头的句子。
    P129v：主语先按人物卡指纹判（酒杯/长袍/旧疤＝小豪，木盘/马甲/马尾＝女服务生），判不出才用上一幅的主语。"""
    nm = _name_aliases(names)
    if not nm:
        return script, 0
    out, last, n = [], "", 0
    for p in re.split(r"\n\s*\n", str(script or "")):
        lines = p.split("\n")
        i = next((k for k, l in enumerate(lines) if l.strip() and not re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip())), None)
        if i is not None:
            l = lines[i].strip()
            head = next((x for x in nm if l.startswith(x)), "")
            if head:
                last = head
            elif (not re.match(r"^(?:%s)" % "|".join(map(re.escape, nm)), l) and not re.match(r"^[（(]", l)
                  and not re.match(r"^(?:一个|一名|一位|两个|两名|几个|几名|众|旁边|身旁|伙计|食客|路人|汉子|客人|老板|小二|掌柜|侍者|店家|士兵|卫兵|随从|下人|仆人|人群|远处|门口|桌上|酒馆|自然光|阳光|光线|尘埃|白天|夜|黄昏|清晨|烛火|月光)", l)):
                # 句首不是名字：是「他/她」就换名字，否则把名字加在前面；主语先按卡指纹判（P129v/w），判不出才用上一幅的主语
                _fp = guess_subject(p, names, cards, pronoun=l[0] if l[:1] in ("他", "她") else "")
                _who = _fp or last
                if not _who:
                    pass
                elif re.match(r"^[他她]", l):
                    lines[i] = _who + l[1:]
                    n += 1
                elif _BODY_START.match(l) or _fp:
                    lines[i] = _who + l
                    n += 1
        out.append("\n".join(lines))
    return "\n\n".join(out), n


def clean_fragments(script, log=None):
    """删分句/剥引号留下的残片（P113）。返回 (剧本, 改动数)。"""
    n = 0
    out = []
    for para in re.split(r"\n\s*\n", str(script or "")):
        lines = para.split("\n")
        narr = [l for l in lines if not re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip())]
        dlg = [l for l in lines if re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip())]
        _norm = lambda q: re.sub(r"[^一-龥]", "", q)
        have = {_norm(re.split(r"[：:]", l, 1)[1]) for l in dlg}
        new_narr = []
        for l in narr:
            t = l
            # ② 叙述里的引号台词：已有台词行 → 剥掉；没有 → 拆成台词行
            for m in list(re.finditer(r"([一-龥]{0,8}?)(?:说道|说|问道|问|答道|答|吐出[一-龥]{0,4}字|低声道|沉声道|轻声道|冷冷道|开口)?[：:]?\s*[“「]([^”」\n]{1,40})[”」][。；;，]?", t)):
                q = m.group(2)
                if _norm(q) in have or any(_norm(q) in h or h in _norm(q) for h in have if h):
                    t = t.replace(m.group(0), "。" if not m.group(0).endswith("，") else "，", 1); n += 1
                else:
                    who = next((nm for nm in re.findall(r"[一-龥]{2,4}", m.group(1) or "") if nm), "")
                    t = t.replace(m.group(0), "。", 1); n += 1
                    if who:
                        dlg.append("%s：%s" % (who, q)); have.add(_norm(q))
            # ③ 引号残渣
            t = re.sub(r"[。，；]?[”」][；;]?", "。", t)
            t = re.sub(r"[“「]", "", t)
            # 镜头词分句（P122）：「特写镜头聚焦在…，」整个分句删
            t = re.sub(r"(?:^|(?<=[，。；]))(?:特写镜头|镜头|画面|特写)[^，。；]*[，。；]", "", t)
            t = t.replace("背对镜头", "背过身").replace("面向镜头", "面向前方").replace("看向镜头", "看向前方").replace("对着镜头", "对着前方")
            t = re.sub(r"(?:朝|向|对|望)镜头", "朝前方", t)
            t = re.sub(r"[，；]?[^，。；]{0,6}镜头[^，。；]*[，。；]", "，", t)
            # ① 残片
            t = re.sub(r"[，；]?[一-龥]{0,6}中的，(?=[一-龥])", "，", t)
            t = re.sub(r"，也，", "，", t)
            t = re.sub(r"[，；]?\s*(?:也|却|而|并|又|还)?\s*(?:正在|似乎正在|似在)?(?:消化|承受|面对)?这突[。；]", "。", t)
            t = re.sub(r"(?<=[，；])[一-龥]{1}[。；]", "。", t)                  # 「，突。」这种单字残尾
            # P129e 残片：句首的「而是…」「却…」「但在…」短残句；一两个字的孤立分句（「。声，」）；孤立方位词（「，身后，」）
            t = re.sub(r"(?:^|(?<=[。！？，；]))(?:而是|却是|但是|但在|却在|但|却|反而)[一-龥]{0,8}[。；]", "。", t)
            t = re.sub(r"(?<=[。，；])[一-龥]{1,2}[，；]", "", t)
            t = re.sub(r"(?<=[，；。])(?:身后|身前|身旁|周围|四周|前方|远处)[，；]", "", t)
            t = re.sub(r"(?<=。)的[一-龥]{0,6}[，。]", "", t)                    # 「。的手势，」
            t = re.sub(r"(?<=[，；])[一-龥]{1,4}的。", "。", t)                  # 「，脸颊的。」
            t = re.sub(r"(?<=[，；。])动作[，；]", "", t)
            t = re.sub(r"\s*[a-z]{3,}\s*", "", t)                                  # 「service生」这类模型漏出来的英文碎词（P129k）
            t = re.sub(r"(?<=[，；])[^，。；]{0,8}[与和在的到向从把将][一-龥]?。", "。", t)   # 「杯底与桌。」半截分句（P129l）
            t = re.sub(r"(?<=[，；])[他她它]?(?:却|也|又|还|则|便)。", "。", t)              # 「，他却。」只剩连词的残句（P129s）
            t = re.sub(r"[，；]?[^，。；]{0,6}听不清[^，。；]*[，。；]", "。", t)                # 「听不清了」声音残句（P129v）
            t = re.sub(r"[，；]?[^，。；]{0,4}(?:周围很吵|很吵)[^，。；]*[，。；]", "。", t)
            t = re.sub(r"^[一-龥]{0,4}(?:展现|呈现|展示)[^，。；]*[，。；]", "", t)        # 「展现酒馆大厅的全景。」镜头说明句（P129l）
            t = re.sub(r"[，；]{2,}", "，", t); t = re.sub(r"。{2,}", "。", t); t = re.sub(r"，。", "。", t)
            t = re.sub(r"^[，；。]+", "", t.strip())
            if t.strip():
                new_narr.append(t)
        # 「发出短促：笃」「面接触发：磕」这类拟声被写成台词行（P129l）
        dlg = [l for l in dlg if not re.match(r"^[^：:]{0,8}(?:发出|声音|做出|做一个|接触|清脆|轻微|轻响|短促)[^：:]{0,6}[：:]", l.strip())]
        dlg = [l for l in dlg if re.search(r"[一-龥]", re.split(r"[：:]", l, 1)[1])]                     # 「女服务生：。」空台词（P129q）
        out.append("\n".join(new_narr + dlg))
    return "\n\n".join(p for p in out if p.strip()), n

_PT_CLASS = [(r"看到|看见|盯着|注视|瞧见|望着|露出|打量|目光|发现|认出", "看"), (r"走进|来到|到了|进入|抵达|推门|落在|到达", "到"),
             (r"说|告诉|问|答|喊|骂|吼|叮嘱", "说"), (r"想|决定|打算|商量", "定")]


_TIME_STEP = re.compile(r"第[一二三四五六七八九十百\d]+[天晚夜次日年月]|[一二三四五六七八九十\d]+天[后来]|"
                        r"第二天|次日|后来|随后|之后|接着|然后|过了|三天|半年|多年|当晚|那晚|最后")


def merge_points(points):
    """连续同类动作（都是"看"、都是"到"）且没换出新人名的剧情点合成一条（P115）。
    P151：带时间推进词（查了三天、第四晚、后来）的剧情点不合并——它们是不同的场次。"""
    out = []
    last_cls, last_names = None, set()
    for p in points or []:
        cls = next((c for pat, c in _PT_CLASS if re.search(pat, p)), None)
        nm = set(re.findall(r"[一-龥]{2,3}(?=（)", p)) | set(re.findall(r"(?:少年|少女|服务生|女孩|师妹|师兄|长老|掌柜|摊主|邻人|老板|队长|骑士|修女|法师|勇者)", p))
        if _TIME_STEP.search(p):                          # 时间推进＝另一场，不合并（P151b）
            out.append(p); last_cls, last_names = cls, nm
            continue
        if out and cls and cls == last_cls and (not nm or nm <= last_names or not last_names):
            out[-1] = out[-1] if len(out[-1]) >= len(p) else p     # 留长的那条当代表
            last_names |= nm
            continue
        out.append(p); last_cls, last_names = cls, nm
    return out

def pin_events_to_beats(events, n_beats=6):
    """事件按顺序钉到拍上：第一条→拍1，最后一条→拍6，中间均摊在拍2～5。返回 {拍下标: 事件}。"""
    evs = [e for e in (events or []) if e]
    n = len(evs)
    if n == 0:
        return {}
    if n == 1:
        return {0: evs[0]}
    if n >= n_beats:
        return {i: evs[i] for i in range(n_beats)}
    pins = {0: evs[0], n_beats - 1: evs[-1]}
    mids = evs[1:-1]
    if mids:
        slots = list(range(1, n_beats - 1))                   # 拍2～5 的下标 1..4
        if len(mids) == 1:
            pins[slots[len(slots) // 2 - 1] if len(slots) > 1 else slots[0]] = mids[0]   # 拍3
        else:
            for i, ev in enumerate(mids):
                k = slots[round(i * (len(slots) - 1) / (len(mids) - 1))]
                while k in pins and k < slots[-1]:
                    k += 1
                pins[k] = ev
    return pins


def events_missing(rows, events):
    """大纲里没出现的事件（按依据词判）。返回 [(事件, 应在拍下标)]。"""
    txt = "".join(str(r.get("event", "")) + str(r.get("action", "")) + str(r.get("line", "")) for r in (rows or []))
    pins = pin_events_to_beats(events, len(rows) or 6)
    miss = []
    for k, ev in pins.items():
        anchors = _event_anchors(ev, txt)
        if len(anchors) < max(1, min(2, len(set(re.findall(r"[一-龥]{2,4}", ev))) // 2)):
            miss.append((ev, k))
    return miss


# 地点类别词：事件说在路上，动作/幅却在桌边屋里（或反过来）→ 冲突
_LOC_OUT = re.compile(r"沿路|赶路|旅行|骑|马背|勒马|下马|山道|山路|林间|野外|进村|村口|到达|到了|河边|桥|街上|街头|路上|路边|碎石路|土路|小路|野草|走在前|荒野|雪原|沙漠|海边|城门|田")
_LOC_IN = re.compile(r"木桌|桌边|桌面|围桌|酒杯|举杯|吧台|柜台|酒馆|客栈|房间|屋内|屋里|大厅|床|灶|窗边|墙边|门内|席间|包间|柜子")


def _loc_conflict(event, text):
    """事件和文字不在同一类地点：返回冲突说明，没有冲突返回空串。"""
    ev, tx = str(event or ""), str(text or "")
    if _LOC_OUT.search(ev) and _LOC_IN.search(tx) and not _LOC_OUT.search(tx):
        return "事件在路上/室外，写成了「%s」" % _LOC_IN.search(tx).group(0)
    if _LOC_IN.search(ev) and _LOC_OUT.search(tx) and not _LOC_IN.search(tx):
        return "事件在屋里/桌边，写成了「%s」" % _LOC_OUT.search(tx).group(0)
    return ""


_EV_STOP = {"一个", "一下", "这个", "那个", "然后", "正在", "他们", "她们", "自己", "一起", "无意", "意间", "都不", "一根", "不能",
            "没有", "有人", "人们", "四人", "两人", "三人", "几人", "一句", "一声", "地的", "的的", "了一", "着一"}


def event_in_text(ev, txt):
    """事件有没有在这段文字里发生：事件里任何一个非虚词的二字词出现就算（剧本是改写的，不能按原话找）。"""
    t = str(txt or "")
    e = re.sub(r"[^一-龥]", "", str(ev or ""))
    grams = [e[i:i + 2] for i in range(len(e) - 1)]
    grams = [g for g in grams if g not in _EV_STOP and not re.search(r"[的了着过在是和与把被让给到从]", g)]
    return any(g in t for g in grams) if grams else True


_STRIKE = re.compile(r"剑气|掌风|击中|刺向|刺入|劈向|劈下|一拳|一掌|踹|砸向|轰向|轰击|打飞|震飞|震退|拍飞|逼退|扫过[^。；]{0,12}(?:撞|断|飞|翻)|指尖[^。；]{0,8}(?:利刃|锋|光刃)"
                     r"|(?:威压|气压|灵压|气浪|劲风|灵力)[^。；]{0,10}(?:逼|震|掀|压得|砸)|巨石砸|掌心向下猛压|双脚离地")


def strike_hits(text, hero):
    """主角出手的句子（句首是主角名、或主角名在出手词之前）。返回句子列表。"""
    out = []
    for sent in re.split(r"(?<=[。！？])", str(text or "")):
        m = _STRIKE.search(sent)
        if not m or not hero:
            continue
        k = sent.find(hero)
        if 0 <= k < m.start() and not re.search(r"(?:没|未|不曾|忍住没|收回)[^。]{0,4}" + re.escape(m.group(0)), sent):
            out.append(sent.strip()[:60])
    return out


def strike_paras(parts, hero):
    """哪些幅里主角出手了。返回幅下标列表。"""
    return [i for i, p in enumerate(parts or []) if strike_hits(p, hero)]


def _recipient(ev, names):
    """事件「把X给了Y」里的 Y（只认人物表上的名字）。"""
    m = re.search(r"(?:给了?|递给|交给|塞给|递到|递向|送给|还给)([一-龥]{2,4})", str(ev or ""))
    if not m:
        return ""
    return next((n for n in (names or []) if m.group(1).startswith(n) or n in m.group(1)), "")


def pin_actions_to_events(rows, events, names=None):
    """钉了事件的拍，动作栏必须是在做那件事。返回改过的拍下标列表。
    2026-09-05 冒烟：拍1 事件「沿路旅行」、动作栏「围桌滴药水」，编剧照动作栏写，整话开场跑进酒馆。"""
    pins = pin_events_to_beats(events, len(rows) or 6)
    fixed = []
    for k, ev in pins.items():
        if not (0 <= k < len(rows)):
            continue
        r = rows[k]
        act = str(r.get("action") or "")
        both = act + str(r.get("blocking") or "")
        _to = _recipient(ev, names)
        if _to and _to not in act:
            # 「把剑给了阿禾」写成「递向长老」（P83）：收的人不对，动作栏改成事件本身
            r["action"] = ev
            fixed.append(k)
            continue
        if _event_anchors(ev, both):
            continue
        if _loc_conflict(ev, both):
            r["action"] = ev
            r["blocking"] = ""
        else:
            r["action"] = ("%s：%s" % (ev, act)) if act.strip() else ev
        fixed.append(k)
    return fixed


def beats_offtrack(parts, spans, rows, events):
    """剧本里哪些拍没在演钉给它的事件：地点类别冲突且依据词不足。返回 {拍下标: (事件, 冲突说明)}。"""
    pins = pin_events_to_beats(events, len(rows) or 6)
    out = {}
    for k, ev in pins.items():
        if not (0 <= k < len(spans)):
            continue
        a, b = spans[k]
        txt = "\n".join(parts[a:b])
        if not txt.strip():
            continue
        why = _loc_conflict(ev, txt)
        if why and not (event_in_text(ev, txt) and len(_event_anchors(ev, txt)) >= 1):
            out[k] = (ev, why)
    return out


def episode_outline(elements, settings=None, note=""):
    el = {k: v for k, v in (elements or {}).items() if not k.startswith("_")}
    user = "\n".join("%s：%s" % (k, v) for k, v in el.items())
    ctx = settings_text(settings_blocks(settings), keys=("world", "pov", "extra", "scale"))
    if ctx:
        user += "\n\n【项目设定】\n" + ctx
    if el.get("同伴") and str(el["同伴"]).strip() not in ("无", "没有"):
        user += "\n★%s 和主角是一边的，六拍里他们一起对付对手，绝不能互相争夺或动手。" % el["同伴"]
    # 【用户事件优先】一句话里写到的事钉到拍上，不许换、不许省；类型骨架只在没事件时兜底
    _events = list((elements or {}).get("_events") or [])
    _pace_name = str((elements or {}).get("_pace") or "推进")
    _rules = _pace.rules(_pace_name)
    _pins = pin_events_to_beats(_events)
    if _pins:
        user += "\n\n【这一话必须发生的事】（用户定的，不许换、不许省、不许提前到别的拍）\n" + "\n".join(
            "拍%d 必须是：%s" % (k + 1, ev) for k, ev in sorted(_pins.items()))
        user += ("\n没钉事件的拍按这一话的节奏补（参考方向：%s）——**要写成具体发生的一件事**（谁对谁做了什么），"
                 "不许把这些提示词原样抄进事件栏。补的拍只能承接前后，不许引入新人物、新地点、新敌人。" % _rules["fill_hint"])
    user += "\n\n【这一话的节奏】%s。" % _pace_name
    if _pace_name == "日常":
        user += "这一话可以没有对手；每拍一个看得见的动作 + 一个变化点（到了哪、听到什么、发现什么）；台词 14～18 句，聊天为主；拍6 停在一个未完成的动作上（听到什么正转头、放下杯子看过去）。"
    elif _pace_name == "推进":
        user += "拍3～5 至少一次对立（价钱、方向、规矩、误会、队内分歧都算），不一定要有敌人；拍4 出一个新事实。"
    _rb = _row_block(settings, for_outline=True)
    if _rb:
        user += "\n\n" + _rb
    _pv = str((settings or {}).get("_prev_context") or "").strip()
    if _pv:
        user += "\n\n【前情——拍1 从上一话的结尾接着演，不重演前面话里发生过的事】\n" + _pv[:1200]
    if (elements or {}).get("_no_strike"):
        user += ("\n★这一话主角面对对手**不能动手**：意思是主角**不出招、不放法术攻击、不用剑气掌风碰对方、不打不踹不逼退**，"
                 "只能逼近、盯着、按住剑柄、攥拳再松开、上前一步又停住——**不是身体僵住动不了**。"
                 "可见动作栏里不许出现：剑气、击中、刺向、劈、踹、一拳、一掌、逼退、撞翻。对峙写足，出手一次都不许。")
    _forbid = [w for w in ((elements or {}).get("_forbid") or []) if w]
    if _forbid:
        user += "\n★这些是后面几话的事，这一话一个字都不许出现：" + "、".join(_forbid[:12])
    rep = au._q(_outline_sys(settings, use_skeleton=not _pins) + (("\n\n" + note) if note else ""), user, mt=1500, temperature=0.4)
    rows = []
    for line in str(rep or "").splitlines():
        parts = [x.strip() for x in line.strip().strip("|｜").split("｜")]
        if len(parts) < 4:
            parts = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(parts) < 4:
            continue
        if re.search(r"占比|事件|可见动作|观众|站位", parts[1] + (parts[2] if len(parts) > 2 else "")) and not re.search(r"\d", parts[1]):
            continue                                   # 表头行
        if len(parts) > 8:                              # 站位栏里用了「｜」把物件栏挤到后面
            parts = parts[:6] + ["；".join(parts[6:-1]), parts[-1]]
        elif 5 <= len(parts) < 8 and re.search(r"(?:手里|手中|桌面|桌上|怀里|口袋|地上|阴影)$|在[一-龥]{1,6}(?:手里|手中|桌上|怀里)", parts[-1]):
            parts = parts[:-1] + [""] * (8 - len(parts)) + [parts[-1]]   # 少了几栏、最后一栏是物件 → 补空栏把物件放回第 8 栏
        parts[2] = re.sub(r"^(?:拍\s*\d+\s*)?(?:补|承接|阻力|补拍|新事实|转折)\s*[：:]\s*", "", str(parts[2] or "")).strip()   # 「拍4补：」「阻力：」前缀剥掉（P126）
        row = {"beat": (re.sub(r"[^0-9]", "", parts[0]) or str(len(rows) + 1)), "share": parts[1], "event": parts[2], "action": parts[3],
               "line": parts[4] if len(parts) > 4 else "", "known": parts[5] if len(parts) > 5 else "",
               "blocking": parts[6] if len(parts) > 6 else "", "prop": parts[7] if len(parts) > 7 else ""}
        if row["prop"] and not re.search(r"手|拿|握|持|放|地上|桌|怀|口袋|腰|悬|空", row["prop"]) and len(row["prop"]) < 4:
            row["prop"] = ""
        # 台词栏错位：不像台词就清空（「观众知道了什么」那一栏是叙述，绝不能当台词念出来）
        row["line"] = _clean_line_cell(row["line"], names_from_elements(elements) or [])
        rows.append(row)
    # 物件栏为空 → 从可见动作里推：谁夺/接/托/抢/拿了就在谁手里；推不出继承上一拍
    _names = list(names_from_elements(elements) or [])
    prev_prop = ""
    for r in rows:
        if not r.get("prop"):
            txt = str(r.get("action", "")) + str(r.get("event", ""))
            m = re.search(r"([一-龥]{2,4})[^，。；]{0,10}?(?:夺过|夺下|一把夺|抢过|接过|接住|托起|托住|拿起|端起|握住|递出|递到|递向|举着|手持|手中的酒杯凑|将酒杯)", txt)
            who = m.group(1) if m else ""
            if who and any(w in who for w in ("一把", "顺势", "伸手", "突然", "迅速")):
                who = ""
            r["prop"] = ("%s手中" % who) if who else prev_prop
        prev_prop = r.get("prop", "")
    # 台词栏被清空的拍 → 单独补（叙述句已被 _clean_line_cell 过滤掉）
    try:
        rows = fill_missing_lines(rows, elements, list(names_from_elements(elements) or []))
    except Exception:
        pass
    # 占比由代码定：模型两次都算成 135%+（2026-09-03 实测）
    _shares = ["8%", "17%", "25%", "25%", "15%", "10%"]
    for i, r in enumerate(rows[:6]):
        r["share"] = _shares[i]
    return rows


def _share(v):
    m = re.search(r"(\d+(?:\.\d+)?)", str(v or ""))
    return float(m.group(1)) if m else 0.0


_FILL_LINE_SYS = """你是短剧编剧。下面是一话六拍的大纲，其中几拍台词不够。给这几拍**各补到 2～3 句**。
· 这一拍已经有的那句**照抄保留**，你只在它前后添新的，让这一拍凑够 2～3 句；
规矩：
· 每句 ≤15 字，是人物**说出口的话**：口语、带情绪、能听出正在干什么或看到了什么；
· 不许写旁白、不许写"观众知道了什么"这类叙述（"石魔像被闯入者唤醒"是叙述，"它醒了！"才是台词）；
· 两句要不同的人说，或同一个人两句连着说但意思推进；
· 只用给定的人物名字。
输出：一行一拍，格式「拍N｜名字：台词／名字：台词」，只输出缺的那几拍，不要别的话。"""


def _line_count(cell):
    return len([x for x in re.split(r"[/\uff0f]", str(cell or "")) if x.strip()])


def fill_missing_lines(rows, elements, names, log=None, want=2):
    """台词不足 want 句的拍 → 一次调用补到 2～3 句（原有的那句保留，新的追加在后面）。

    2026-09-05：原来只补「空」的拍。实测公路片每拍都写了 1 句，一句都不空，
    这个补丁从来没跑过，全话只有 7 句。改成按句数触发。"""
    miss = [i for i, r in enumerate(rows) if _line_count(r.get("line")) < want]
    if not miss:
        return rows
    ctx = ["【人物】" + "、".join(names or []),
           "【对手是什么】" + str((elements or {}).get("对手是什么") or (elements or {}).get("谁挡他") or ""),
           "【六拍】"]
    for i, r in enumerate(rows):
        ctx.append("拍%d｜%s｜%s｜台词：%s" % (i + 1, r.get("event", ""), r.get("action", ""),
                                            r.get("line") or "（缺，要补）"))
    ctx.append("【要补的拍】" + "、".join(
        "拍%d（现在 %d 句，补到 2～3 句）" % (i + 1, _line_count(rows[i].get("line"))) for i in miss))
    if miss and miss[0] == 0:
        ctx.append("★拍1 的台词里必须有一句点明对手是什么、为什么危险，让观众一眼分清敌我。")
    try:
        rep = str(au._q(_FILL_LINE_SYS, "\n".join(ctx), mt=600, temperature=0.6) or "")
    except Exception:
        return rows
    got = 0
    for line in rep.splitlines():
        m = re.match(r"^\s*拍?\s*(\d+)\s*[｜|:：]\s*(.+)$", line.strip())
        if not m:
            continue
        k = int(m.group(1)) - 1
        if not (0 <= k < len(rows)) or k not in miss:
            continue
        cell = _clean_line_cell(m.group(2), names)
        if not cell:
            continue
        old = str(rows[k].get("line") or "").strip()
        if old:
            # 追加：模型可能把原句抄回来了，去重后拼
            have = [x.strip() for x in re.split(r"[/\uff0f]", old) if x.strip()]
            add = [x.strip() for x in re.split(r"[/\uff0f]", cell)
                   if x.strip() and x.strip() not in have]
            cell = "\uff0f".join(have + add) if add else old
        if _line_count(cell) > _line_count(rows[k].get("line")):
            got += 1
        rows[k]["line"] = cell
    if log is not None:
        log.append("补台词：%d/%d 拍" % (got, len(miss)))
    return rows


_RIDE_WORDS = "摩托车|摩托|机车|飞车|车|马|骆驼|机甲|滑板|飞行器|艇"
_BEHIND_WORDS = "身后|后座|背后"
_HOLD_WORDS = "环住|抱住|箍住|搂住|扣住|紧贴|贴着"


def shared_ride(script, names=None):
    """判出「谁和谁同乘一辆什么」。返回 (前面的人, 后面的人, 载具词) 或 None。

    名字是已知的，按名单直接配对——用通配的人名组会被贪婪匹配吃掉
    （2026-09-05：「苏拉跨坐在林野身后」里「苏拉跨」被当成了名字）。
    """
    txt = str(script or "")
    ns = [n for n in (names or []) if n and len(n) >= 2]
    if len(ns) < 2:
        return None
    for a in ns:                                   # a = 坐在后面的人
        for b in ns:                               # b = 前面的人
            if a == b:
                continue
            pats = [
                re.escape(a) + r"[^。；\n]{0,12}(?:跨坐|坐在|坐上|骑在|趴在)[^。；\n]{0,6}"
                + re.escape(b) + r"(?:的)?(?:" + _BEHIND_WORDS + r")",
                re.escape(a) + r"[^。；\n]{0,14}(?:" + _HOLD_WORDS + r")[^。；\n]{0,4}"
                + re.escape(b) + r"(?:的)?(?:腰|腰腹|后背|背|肩)",
            ]
            m = next((x for x in (re.search(p, txt) for p in pats) if x), None)
            if not m:
                continue
            # 载具词只在同乘那句话附近取：扫全文会取到追兵的载具
            # （2026-09-05：男女主骑飞行摩托，却判成"同乘一辆机甲"，因为追兵是警用机甲）
            _VEH = r"(飞行摩托|悬浮摩托|摩托车|摩托|机车|机甲|马车|马|飞行器|飞艇|滑板)"
            near = txt[max(0, m.start() - 160):m.end() + 160]
            veh = re.search(_VEH, near) or re.search(_VEH, txt)
            return (b, a, veh.group(1) if veh else "车")
    return None


def drop_ally_vehicle(text, back_name, veh_word="车"):
    """同乘时坐在后面的人没有自己的载具 → 去掉「同伴名+的+载具」这种所有格。"""
    if not (text and back_name):
        return text
    v = _RIDE_WORDS
    t = str(text)
    # 先收并列句（「左侧是林野的机车，右侧是苏拉的机车」），再收所有格；
    # 反过来的话名字已经被删掉，并列句就匹配不上了。
    t = re.sub(r"[，,][^，。；\n]{0,8}是%s(?:的)?(?:%s)" % (re.escape(back_name), v), "", t)
    # 「苏拉的机车」「苏拉摩托」→「机车」「摩托」
    t = re.sub(r"%s(?:的)?((?:%s))" % (re.escape(back_name), v), r"\1", t)
    return t


def outline_problems(rows, elements):
    probs = []
    if len(rows) < 5:
        probs.append("大纲卡只有 %d 拍" % len(rows))
        return probs
    _pn = str((elements or {}).get("_pace") or "推进")
    _rl = _pace.rules(_pn)
    r1 = rows[0]
    _act1 = r"推|拉|抓|夺|喊|冲|砸|跪|拦|挡|摔|撞|递|问|吼|扑" + (("|" + _rl["act_extra"]) if _rl.get("act_extra") else "")
    if re.search(r"走|雾|雨|张望|清晨|黄昏|介绍|长相|环境", r1["event"]) and not re.search(_act1, r1["event"] + r1["action"]):
        probs.append("拍1不是正在发生的动作：%s" % r1["event"][:20])
    # 日常档硬塞威胁（P94：「那石魔像醒了！」）
    for _i, _w in daily_threat_rows(rows, elements):
        probs.append("拍%d日常档硬塞了威胁「%s」（这一话没有对手）" % (_i + 1, _w))
    # 不在这一话的人被拉进大纲（P92：「补：赵铁柱从阴影中冲出挡在路中间」）
    _cast = list((elements or {}).get("_allowed_cast") or [])
    _all_names = list((elements or {}).get("_names") or [])
    _foreign = [n for n in _all_names if n and n not in _cast and len(n) >= 2] if _cast else []
    for _i, _r in enumerate(rows):
        _txt = str(_r.get("event") or "") + str(_r.get("action") or "")
        _hit = [n for n in _foreign if n in _txt]
        if _hit:
            probs.append("拍%d出现了不在这一话的人：%s（这一话在场的只有 %s）" % (_i + 1, "、".join(_hit), "、".join(_cast)))
    # 节奏提示词被原样抄成事件（P91：「一处阻力（规矩对立）」「一次小对立（方向/误会）」）
    for _i, _r in enumerate(rows):
        _evt = str(_r.get("event") or "")
        if re.search(r"一处阻力|小对立|（[^）]*[／/][^）]*）|规矩对立|承接前后|新事实[揭浮][示露现出晓]|揭示新事实|转折事实|一次交锋|一次反击|一次代价|确认时间线|阻力出现|对立升级", _evt):
            probs.append("拍%d事件是模板词不是具体的事：%s" % (_i + 1, _evt[:16]))
    # 用户定的事件一条都不能少（设计 v2 原则 1）
    for _ev, _k in events_missing(rows, (elements or {}).get("_events") or []):
        probs.append("缺事件「%s」（该在拍%d）" % (_ev, _k + 1))
    # 不越界：后面几话的事不许出现
    _txt_all = "".join(str(r.get("event", "")) + str(r.get("action", "")) for r in rows)
    _leak = [w for w in ((elements or {}).get("_forbid") or []) if w and w in _txt_all]
    if _leak:
        probs.append("越界：出现了后面几话的事「%s」" % "、".join(_leak[:4]))
    if not r1.get("line"):
        probs.append("拍1没有台词")
    _all_lines = []
    for r in rows:
        _all_lines += [x for x in re.split(r"[／/]", str(r.get("line") or ""))
                       if x.strip() and not re.search(r"[：:]\s*[吼嗷嘶咆哮嗥呜嚎啊]{1,3}[！!。]?$", x)]
    if len(_all_lines) < max(6, _rl["dialogue_min"] - 2):
        probs.append("全话只有 %d 句台词（要 %d～%d 句，每拍 2～3 句）" % (len(_all_lines), _rl["dialogue_min"], _rl["dialogue_max"]))
    _foe = str((elements or {}).get("对手是什么") or "") + str((elements or {}).get("谁挡他") or "")
    _foe_none = str((elements or {}).get("谁挡他") or "").strip() in ("", "无", "没有")
    _foe_key = [] if _foe_none else [w for w in re.findall(r"[一-龥]{2,4}", _foe)
                                    if w not in ("主角", "对手", "他们", "什么", "为什么", "无无", "没有")][:6]
    _head = str(r1.get("line", "")) + str(rows[1].get("line", "") if len(rows) > 1 else "")
    if _foe_key and not any(w in _head for w in _foe_key) and not re.search(r"醒|守|挡|杀|敌|别过去|退后|它|怪|魔|兽", _head):
        probs.append("开头两拍的台词没点明对手是什么（观众会把对手当同伴）")
    acts = "".join(str(r.get("action", "")) + str(r.get("event", "")) for r in rows[2:5])
    _ACT = r"推|拉|抓|夺|喊|冲|砸|跪|拦|挡|摔|撞|递|逼近|后退|插话|转身|扇|甩|按|抱|拽|拍|压|扣|攥|踹|踢|扑|撕|泼|掐|拔|扯|捂|指|举|放下|挥|劈|刺|跨|绕|逼|退|拧|捧|端|抢|塞|夹|抵|碰|碎|震|掀|拨|搂|吼|哭|站起|起身|松手|翻|掷|扔|掏|拿|接|盖|戳|敲|拽"
    if _rl["conflict_min"] and len(re.findall(_ACT, acts)) < _rl["conflict_min"]:
        probs.append("拍3～5 的可见冲突动作不足%d个" % _rl["conflict_min"])
    last = rows[-1]
    if not re.search(r"没|未|一半|停|悬|正要|伸出|举|张口|抬起", last.get("event", "") + last.get("action", "")):
        probs.append("拍6没有停在未完成的动作上")
    # 六拍六个不同事件：同一核心动作出现在两拍以上就是转圈（2026-09-03 实测泼酒三次）
    _CORE = ("泼", "夺", "碰杯", "接杯", "递", "挡", "拽", "拉住", "推", "喝", "冲出", "切入", "跪", "抱", "扇")
    seen = {}
    for i, r in enumerate(rows):
        txt = str(r.get("event", "")) + str(r.get("action", ""))
        for v in _CORE:
            if v in txt:
                seen.setdefault(v, []).append(i + 1)
    dup = {v: ks for v, ks in seen.items() if len(ks) >= 2}
    if dup:
        probs.append("重复动作：" + "；".join("拍%s都有「%s」" % ("、".join(map(str, ks)), v) for v, ks in dup.items()))
    # 物件在谁手里前后要接得上（2026-09-04 实测：拍3 苏清婉夺了酒盏，拍5 阿离"再次递酒"）
    names = [n for n in (elements or {}).get("_names", []) if n]
    def _owner(v):
        v = str(v or "")
        return next((n for n in names if n in v), "")
    for i in range(1, len(rows)):
        prev_owner = _owner(rows[i - 1].get("prop"))
        txt = str(rows[i].get("event", "")) + str(rows[i].get("action", ""))
        if not prev_owner:
            continue
        for n in names:
            if n == prev_owner:
                continue
            if re.search(re.escape(n) + r"[^。；]{0,14}(?:再次递|递酒|递过|递向|递到|递出|举盏|举杯|举碗|举起|送到|送至|凑近|逼近|抵在|抵向|捧着|捧碗|托着|端着|按在|贴在)", txt):
                probs.append("拍%d %s递/举物件，但拍%d结束时物件在%s手里" % (i + 1, n, i, prev_owner))
                break
        m = re.search(r"([一-龥]{2,4})[^，。；]{0,8}(?:夺过|夺下|一把夺|抢过|接过|接住|夺回)", txt)
        if m and _owner(m.group(1)) and _owner(rows[i].get("prop")) and _owner(rows[i].get("prop")) != _owner(m.group(1)):
            probs.append("拍%d %s夺/接了物件，但这一拍结束时物件写在%s手里" % (i + 1, _owner(m.group(1)), _owner(rows[i].get("prop"))))
    return probs


# ───────────────── 第 3 步：画面剧本 ─────────────────
_SCRIPT_SYS = """你是这部短片的编剧，直接写**画面剧本**（不是小说）。按大纲卡逐拍写，每拍两到四幅画面，格式和规矩：
· **一幅画面写成一段连贯的两到四句话**（不要每句一行、不要在段首写「名字，」）。段落之间空一行。段落里只写摄影机拍得到的：谁（用名字）、用哪只手/哪条腿、做了什么、朝哪、从哪到哪、脸上什么表情、身后是什么、光从哪来。
· 台词单独一行，格式 名字：“台词” —— **必须带中文引号**，每句 ≤15 字，是人物真会说出口的话；一幅画面最多一句台词。
  动作、表情、样子一律写在段落里，**不许用「名字：」开头写动作**。台词前那一幅要写出说话人在看谁、嘴唇开合。
· 动作幅度要大：整个人的位移（走三步、转身、跨过门槛）、整条手臂的动作（抬手过头、一把拉住、推开）、能被看见的力度（把东西按在桌上、扇子拍在掌心）。小到眼皮抖动、指尖微颤的不写。
· 每个关键动作按「建立—动作—细节—反应」写四幅：先一幅看清位置关系，再一幅动作本身，再一幅手/物件的细节，再一幅对方的反应。
· 第一幅就是大纲卡拍1 的事件，不写环境开场；换到新空间时那一幅先写这个地方长什么样。
· 人物第一次出场带身份和一个独特外形点（「师兄李长渊，右耳后一道旧疤」这种写法，用要素表和人物卡里的）。
· 只写大纲卡里有的人物和地点；心理、气味、声音、比喻一律不写。
· **人物一律用人物表上的名字，一个名字只对应一个人**；不同的人不许同名，也不许用「师姐／那女子」代替名字。
全篇 1600～2400 字。只输出剧本。"""


_QUOTED = re.compile(r"^([^\n：:]{1,8})[：:]\s*[“\"「]([^”\"」]{1,40})[”\"」]\s*$")
_COLON_LINE = re.compile(r"^([^\n：:]{1,8})[：:]\s*(.+?)\s*$")


def strip_line_quotes(script):
    """台词行两端的各种引号全部剥掉（模型写成 ”抓到你了。“ 这种反向引号也一样）。"""
    out = []
    for l in str(script or "").split("\n"):
        m = re.match(r"^([^\n：:]{1,8})[：:]\s*(.+)$", l.strip())
        if m:
            t = m.group(2).strip().strip('“”„‟"「」『』 ')
            out.append("%s：%s" % (m.group(1).strip(), t))
        else:
            out.append(l)
    return "\n".join(out)


def normalize_dialogue(script):
    """台词只认带引号的 `名字：“台词”`；其余「名字：内容」还原成叙述句「名字内容」——
    模型常用「名字：动作描写」写画面，不处理下游会当台词念出来（2026-09-03 实测）。返回 (新剧本, 还原条数)。"""
    out, n = [], 0
    # 「风声：呼——」不是人说话（P84）：声音类"说话人"整行还原成叙述
    script = re.sub(r"(?m)^(风声|雨声|雷声|水声|钟声|鸟鸣|马蹄声|脚步声|远处|画面|旁白|背景音)[：:]\s*[“\"「]?([^”\"」\n]*)[”\"」]?\s*$", r"\1\2", str(script or ""))
    # 叙述句中间夹着「名字：“台词”」（P88：`声音干涩：赵铁柱：“大师兄？”。`）→ 剥掉那一截，台词行另有
    script = re.sub(r"(?m)^(?P<pre>[^\n：:]{9,}?)[，。；：:]\s*(?:[\u4e00-\u9fa5·]{2,4})[：:]\s*[“「][^”」\n]{1,40}[”」][。]?", r"\g<pre>。", str(script or ""))
    # 「李长渊说：……」→「李长渊：……」（P83：说话人带了"说/道"，下游当成另一个人）
    script = re.sub(r"(?m)^([\u4e00-\u9fa5·]{2,4}?)(?:说|道|问|答|喊|回答|说道|问道|答道|低声说|轻声说|沉声道|冷冷地说)(?=[：:])", r"\1", str(script or ""))
    # 行内引号台词：`苏拉“抓到你了。” 苏拉侧过头…` → 叙述行 + 独立台词行（2026-09-05 项目102：这两句台词在视频里整个丢了）
    _INLINE_Q = re.compile(r"^([\u4e00-\u9fa5A-Za-z·]{2,6})[：:]?\s*[“\"「]([^”\"」]{1,40})[”\"」]\s*(.*)$")
    _lines = []
    # 「／」是大纲台词栏的分隔符，漏进剧本就会两句挤一行、第一句还常丢冒号
    # （2026-09-05 项目102：`林野雷达锁定了！／苏拉：别让它追上！`）。先拆开、补冒号。
    _src = []
    # 全剧本出现过的说话人，用来给丢了冒号的那半句补回名字
    _speakers = set(re.findall(r"^([\u4e00-\u9fa5A-Za-z·]{2,6})[：:].+$", str(script or ""), re.M))
    for line in str(script or "").splitlines():
        t = line.strip()
        if "／" not in t and "/" not in t:
            _src.append(line)
            continue
        segs = [x.strip() for x in re.split(r"[／/]", t) if x.strip()]
        if len(segs) < 2 or not any(re.match(r"^[\u4e00-\u9fa5A-Za-z·]{2,6}[：:]", x) for x in segs):
            _src.append(line)
            continue
        whos = [re.match(r"^([\u4e00-\u9fa5A-Za-z·]{2,6})[：:]", x).group(1)
                for x in segs if re.match(r"^[\u4e00-\u9fa5A-Za-z·]{2,6}[：:]", x)]
        whos = sorted(set(whos) | _speakers, key=len, reverse=True)
        for x in segs:
            if re.match(r"^[\u4e00-\u9fa5A-Za-z·]{2,6}[：:]", x):
                _src.append(x)
                continue
            hit = next((w for w in whos if x.startswith(w)), "")
            _src.append(("%s：%s" % (hit, x[len(hit):].strip())) if hit else x)
    for line in _src:
        t = line.strip()
        m0 = _INLINE_Q.match(t)
        if m0 and m0.group(3).strip():
            who, q, rest = m0.group(1).strip(), m0.group(2).strip(), m0.group(3).strip()
            if not rest.startswith(who):
                rest = who + rest
            _lines.append(rest)
            _lines.append("%s：%s" % (who, q))
            continue
        _lines.append(line)
    for line in _lines:
        t = line.strip()
        m = _QUOTED.match(t)
        if m:
            out.append("%s：%s" % (m.group(1).strip(), m.group(2).strip()))
            continue
        m2 = _COLON_LINE.match(t)
        if m2 and not t.startswith(("镜头", "【", "──", "〔")):
            body = m2.group(2)
            # 短、口语、没有描写动词 → 当台词；否则还原成叙述
            # 「带她入内」「去查清她的下落」是台词：裸的 他/她 不算描写词，只有 他的手/她们 这种才算（P87）
            looks_line = len(body) <= 15 and not re.search(r"的手|指尖|掌心|身体|背景|镜头|微微|轻轻|缓缓|站在|坐在|走向|抬起|低头|转身|皱眉|眉头|扫过|锁定", body)
            if looks_line:
                out.append("%s：%s" % (m2.group(1).strip(), body))
            else:
                out.append(m2.group(1).strip() + body)
                n += 1
            continue
        out.append(line)
    return "\n".join(out), n


_BEAT_SYS = """你是这部短片的编剧，现在只写**这一拍**的画面剧本（5～7 幅，一幅就是一个 3～5 秒的镜头，整话六拍要撑满两分钟）。格式和规矩：
· 一幅画面写成一段连贯的两到四句话（80～140 字），段落之间空一行。**一幅是一串连着的 2～3 个动作（起—行—止）**，比如"蹬地扑出→抓住肩膀→拖到身后"，自然演 3～6 秒；不许一幅只写一个手势（那样视频会把它拉成慢动作）。每一幅必须有看得见的变化：位置、接触、或物件状态。只写摄影机拍得到的：谁（用名字）、用哪只手/哪条腿、做了什么、朝哪、从哪到哪、脸上的表情、身后是什么。
· 每一幅的第一个词必须是人名，不许用"他/她"开头。光线和天气只在这一拍的第一幅提一句，其余幅不重复写光。
· 这一拍的台词只有大纲卡给的那一句（可能没有）：单独一行，格式 名字：“台词”，带中文引号，放在说话人那一幅的下面。不许加别的台词。
· 按「建立—动作—细节—反应」排幅：第一幅看清谁在哪；然后动作本身；然后手或物件的细节；然后对方的反应。
· 有对手在场时，**每一拍至少有一幅拉开看全场**：写清对手在哪一侧、主角这一方在哪一侧、两边隔几步、中间是什么；对手不许和主角并排站着。
· 动作幅度要大：整个人的位移、整条手臂、看得见的力度。眼皮抖动、指尖微颤这种不写。
· **必须从上一拍的最后一幅接着演**：物件在谁手里、谁站哪、谁做过什么都照着接。**只演这一拍大纲里的事件**——前面拍演过的不重演，后面拍的不提前演。
· 关键物件按大纲的「开始时→结束时」走：物件只能在某个人手里、或被放在某处；**不许悬在空中、不许自己滑动、不许凭空出现第二个**。"动作停在半空"指的是人的手停住，不是物件飘着。
· 这一拍写完时，每个人要停在大纲「结束时人在哪」写的位置：人只往那个位置走，走到了就停；不许让谁走出画面再回来，不许让谁离开后下一拍又出现。
· 只用人物表上的名字，一个名字一个人；不写心理、气味、声音、比喻。
只输出这一拍的画面，不要标题、不要拍号。"""


def write_script_by_beats(elements, outline, chars_digest="", place="", names=None, on_step=None, log=None, spans=None, only=None, notes=None, prev_parts=None):
    """逐拍写：每拍一次调用（2～4 幅），带上一拍最后一幅当上下文。
    spans：传个 list 进来，回填每拍的 (起, 止) 幅下标。only：只写这些拍号（0 起）；其余拍沿用 prev_parts 里的旧幅。notes：{拍号: 追加要求}。"""
    _wlog = log if log is not None else []
    el = {k: v for k, v in (elements or {}).items() if not k.startswith("_")}
    head = ["【要素表】"] + ["%s：%s" % (k, v) for k, v in el.items()]
    if names:
        head.append("【人物表】" + "、".join(names) + "——只用这些名字，一个名字只对应一个人。")
    _cast = list((elements or {}).get("_allowed_cast") or [])
    if _cast and names and len(_cast) < len(names):
        head.append("【这一话在场的人只有】" + "、".join(_cast) + "。人物表里别的人、前情里提到过的人都**不在这一话里**，不许出场、不许动、不许说话；台词里可以提到名字。")
    if chars_digest:
        head.append(chars_digest)
    if place:
        head.append("【这个地方有的东西】" + place)
    _rb2 = _row_block(getattr(write_script_by_beats, "_settings", None), for_outline=True)
    if _rb2:
        head.append(_rb2)
    head.append("【主语规矩】每一句话的主语写名字（在场名单上的人）；路人、食客这类没名字的人只写成「一个食客」「旁边的汉子」，"
                "他们只能被撞开、让路、抬头看一眼，不许有自己的动作段落、不许说话。")
    _ctx = settings_text(settings_blocks(getattr(write_script_by_beats, "_settings", None)), keys=("world", "genre", "pov", "extra", "scale"))
    if _ctx:
        head.append(_ctx)
    if outline:
        head.append("【整话六拍（知道自己在弧线的哪一段）】\n" + "\n".join(
            "拍%s：%s" % (x.get("beat", j + 1), x.get("event", "")) for j, x in enumerate(outline)))
    if el.get("同伴") and str(el["同伴"]).strip() not in ("无", "没有"):
        head.append("【同伴】%s 和主角是**一边的**，一起行动、互相配合；他们之间不许打架、不许抢夺，冲突只发生在主角这一方和对手之间。" % el["同伴"])
    if el.get("时间与光线"):
        head.append("【全篇时间与光线】" + el["时间与光线"] + "——只在每拍第一幅提一句，别的幅不写光；不许换成别的时段或光源。")
    _pvs = str((getattr(write_script_by_beats, "_settings", None) or {}).get("_prev_context") or "").strip()
    if _pvs:
        head.append("【前情（这一话从这里接着往下，不重演）】" + _pvs[-600:])
    if (elements or {}).get("_no_strike"):
        head.append("【对峙而不打】主角面对对手**一次都不出手**：不出招、不放法术攻击、不用剑气掌风碰对方、不打不踹不逼退；"
                    "能写的是逼近、盯着、按住剑柄、攥拳再松开、上前一步又停住、法力在身上翻涌却不放出去。**不是身体僵住动不了**。"
                    "对方可以害怕、后退、跌坐，但不能是被主角打的。")
    head_txt = "\n".join(head)
    parts, prev_last = [], ""
    for i, r in enumerate(outline or []):
        if only is not None and i not in only and prev_parts is not None:
            old = list(prev_parts[i]) if i < len(prev_parts) else []
            if spans is not None:
                spans.append((len(parts), len(parts) + len(old)))
            parts.extend(old)
            if old:
                prev_last = old[-1]
            continue
        if on_step:
            on_step("编剧组：写第 %d/%d 拍" % (i + 1, len(outline)))
        prop_prev = (outline[i - 1].get("prop", "") if i > 0 else "") or "（开场时按这一拍大纲）"
        _end_pos = (outline[i + 1].get("blocking", "") if i + 1 < len(outline) else "") or r.get("blocking", "")
        row = "拍%s（%s）｜事件：%s｜可见动作：%s｜台词：%s｜观众要知道：%s｜站位：%s｜这一拍结束时人在哪（下一拍从这里接，人不许离场再回来）：%s｜物件：这一拍开始时 %s → 结束时 %s" % (
            r.get("beat", i + 1), r.get("share", ""), r.get("event", ""), r.get("action", ""), r.get("line", "") or "无", r.get("known", ""), r.get("blocking", ""),
            _end_pos, prop_prev, r.get("prop", "") or "同上")
        user = head_txt + "\n\n【这一拍的大纲】\n" + row
        done = ["拍%s：%s" % (x.get("beat", j + 1), x.get("event", "")) for j, x in enumerate(outline[:i])]
        if done:
            user += "\n\n【前面已经发生过的事（都演过了，这一拍不要重演）】\n" + "\n".join(done)
        later = ["拍%s：%s" % (x.get("beat", j + i + 2), x.get("event", "")) for j, x in enumerate(outline[i + 1:])]
        if later:
            user += "\n\n【后面才会发生的事（这一拍不许提前演）】\n" + "\n".join(later)
        if prev_last:
            user += "\n\n【上一拍的最后一幅（从这里接着演）】\n" + prev_last[:400]
        elif i == 0:
            user += "\n\n这是全片第一幅：直接从事件开始，不写环境铺垫。"
        if notes and notes.get(i):
            user += "\n\n★" + str(notes[i])
        try:
            rep = str(au._q(_BEAT_SYS, user, mt=1600, temperature=0.7) or "").strip()
        except Exception:
            rep = ""
        rep, _ = normalize_dialogue(rep)
        paras = [p for p in re.split(r"\n\s*\n", rep) if p.strip()]
        if not paras:
            if spans is not None:
                spans.append((len(parts), len(parts)))
            continue
        paras = drop_premature(paras, r, outline[i + 1:], log=_wlog, beat_no=i + 1)
        # 幅首的 他/她 → 这一拍动作的主语
        actor = next((n for n in (names or []) if n in str(r.get("action", "")) + str(r.get("event", ""))), (names or [""])[0])
        if actor:
            paras = [re.sub(r"^(他们|她们|他|她)(?=[^\n]{0,3}[一-龥])", actor, p) for p in paras]
        paras = strip_subject_bleed(paras, names)          # 「他…看着主语」的串人句（P129）
        # 本拍大纲有台词而输出里没有 → 接到本拍的幅上（台词是硬约束，不能靠模型自觉）
        norm = lambda q: re.sub(r"[^一-龥]", "", q)
        _wants = [w for w in split_lines(r.get("line"))
                  if not re.search(r"[：:]\s*[吼嗷嘶咆哮嗥呜嚎啊]{1,3}[！!。]?$", w)]
        for _k, want_line in enumerate(_wants):
            mm = re.match(r"^([^：:]{1,8})[：:]\s*(.+)$", want_line)
            who, txt = (mm.group(1).strip(), mm.group(2).strip()) if mm else ("", want_line)
            if not (txt and norm(txt)):
                continue
            _exist = [norm(m.group(1)) for m in re.finditer(r"^[^\n：:]{1,8}[：:](.+)$", "\n".join(paras), re.M)]
            if any(norm(txt) == x or (len(norm(txt)) >= 4 and norm(txt) in x) for x in _exist):
                continue                              # 已经有这句台词行了才跳过（叙述里提到不算）
            if not who and names:
                who = next((n for n in names if n in paras[-1]), names[0])
            if not who:
                continue
            # 多句分散到不同的幅上：第一句放中间那一幅，其余往后；优先落在**含说话人名字**的幅上（P129h：人走了台词才出来）
            _i = min(len(paras) - 1, max(0, len(paras) - 1 - (len(_wants) - 1 - _k)))
            _cands = [q for q, pp in enumerate(paras) if who in pp and not re.search(r"\n" + re.escape(who) + r"[：:]", pp)]
            if _cands:
                _i = min(_cands, key=lambda q: abs(q - _i))
            paras[_i] = paras[_i].rstrip() + "\n%s：%s" % (who, txt)
        if spans is not None:
            spans.append((len(parts), len(parts) + len(paras)))
        parts.extend(paras)
        prev_last = paras[-1]
    return "\n\n".join(parts)


def thin_beats(parts, spans):
    """哪些拍薄：不到 2 幅或不到 120 字。"""
    out = []
    for i, (a, b) in enumerate(spans or []):
        seg = parts[a:b]
        if len(seg) < 5 or sum(len(x) for x in seg) < 400:
            out.append(i)
    return out


def rewrite_thin_beats(elements, outline, chars_digest, place, names, parts, spans, on_step=None, log=None):
    """只重写薄的拍（最多 3 拍），其余拍原样保留。返回 (parts, spans)。"""
    thin = thin_beats(parts, spans)
    if not thin:
        return parts, spans
    prev_parts = [parts[a:b] for a, b in spans]
    notes = {}
    for i in thin:
        a, b = spans[i]
        notes[i] = "上一稿这一拍只有 %d 幅、%d 字，太薄：这次写满 6～7 幅，每幅 80～140 字，每幅一个新的位移或接触，不重复上一幅的动作。" % (b - a, sum(len(x) for x in parts[a:b]))
    if log is not None:
        log.append("补薄拍：" + "、".join("拍%d" % (i + 1) for i in thin))
    new_spans = []
    txt = write_script_by_beats(elements, outline, chars_digest, place, names=names, on_step=on_step, log=log,
                                spans=new_spans, only=set(thin), notes=notes, prev_parts=prev_parts)
    new_parts = [p for p in re.split(r"\n\s*\n", txt) if p.strip()]
    if len(new_spans) != len(spans):
        return parts, spans
    return new_parts, new_spans


_CORE_PATS = [("泼", r"泼"), ("夺", r"夺过|一把夺|夺走|夺下"), ("碰杯", r"碰杯|杯沿[^。]{0,4}(?:磕|碰|抵)"), ("接杯", r"接住[^。]{0,6}酒?杯|接过[^。]{0,4}酒?杯|托住[^。]{0,4}杯"),
              ("切入", r"大步切入|冲出|冲入画面|切入画面|挤入|挤进"), ("挽", r"挽住|挽起"), ("拽", r"拽|拉扯|拉住"), ("推", r"推开|推出"), ("喝", r"喝下|饮尽|喝干|抿了一口"),
              ("跪", r"跪"), ("抱", r"抱住|拥入"), ("扇", r"扇了|一巴掌"), ("递", r"递向|递到|递给|送至[^。]{0,4}唇")]


def core_hits(text):
    return {k for k, pat in _CORE_PATS if re.search(pat, str(text or ""))}


def drop_premature(paras, this_row, later_rows, log=None, beat_no=0):
    """这一拍的幅里出现了后面拍才有的核心动作、且那幅没台词 → 删（模型不听"不许提前演"）。"""
    mine = core_hits(str(this_row.get("event", "")) + str(this_row.get("action", "")))
    later = set()
    for r in later_rows or []:
        later |= core_hits(str(r.get("event", "")) + str(r.get("action", "")))
    later -= mine
    if not later:
        return paras
    out = []
    for p in paras:
        bad = core_hits(p) & later
        if not bad:
            out.append(p)
            continue
        pats = [pat for k, pat in _CORE_PATS if k in bad]
        kept_lines = []
        for l in p.split("\n"):
            if re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip()):
                kept_lines.append(l)
                continue
            clauses = re.split(r"(?<=[，。；！？])", l)
            keep = [c for c in clauses if not any(re.search(pat, c) for pat in pats)]
            if clauses and keep and clauses[0] not in keep:
                _nm0 = re.match(r"^([一-龥]{2,4}?)(?=[左右双伸抬举放端走坐站转看目视身把将朝向])", clauses[0])
                if _nm0:
                    keep.insert(0, _nm0.group(1))            # 幅首分句删了，名字留下（P129r）
            t = "".join(keep).strip()
            t = re.sub(r"^[，；、]+", "", t)
            t = re.sub(r"[，；]+([。！？])", r"\1", t)
            if t and t[-1] not in "。！？":
                t = t.rstrip("，；") + "。"
            if len(re.sub(r"[^一-龥]", "", t)) >= 12:
                kept_lines.append(t)
        narr_left = [l for l in kept_lines if not re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip())]
        if log is not None:
            log.append("拍%d 提前演了「%s」→ %s" % (beat_no, "/".join(sorted(bad)), "删分句" if narr_left else "删一幅"))
        if narr_left:
            out.append("\n".join(kept_lines))
        elif kept_lines and out:
            out[-1] = out[-1].rstrip() + "\n" + "\n".join(kept_lines)
    return out or paras[:1]


def drop_repeated_actions(script, log=None):
    """同一核心动作第二次出现、且那一幅没有台词 → 整幅删掉（剧情转圈的兜底）。"""
    _CORE = _CORE_PATS[:5]
    paras = [p for p in re.split(r"\n\s*\n", str(script or "").strip()) if p.strip()]
    seen, out, dropped = set(), [], []
    for i, p in enumerate(paras):
        hits = [k for k, pat in _CORE if re.search(pat, p)]
        rep = [k for k in hits if k in seen]
        if rep and i > 0:
            pats = [pat for k, pat in _CORE if k in rep]
            kept_lines = []
            for l in p.split("\n"):
                if re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip()):
                    kept_lines.append(l)
                    continue
                clauses = re.split(r"(?<=[，。；！？])", l)
                keep = [c for c in clauses if not any(re.search(pat, c) for pat in pats)]
                t = "".join(keep).strip()
                t = re.sub(r"^[，；、]+", "", t)
                t = re.sub(r"[，；]+([。！？])", r"\1", t)
                if t and t[-1] not in "。！？":
                    t = t.rstrip("，；") + "。"
                if len(re.sub(r"[^一-龥]", "", t)) >= 12:
                    kept_lines.append(t)
            narr_left = [l for l in kept_lines if not re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip())]
            dropped.append("幅%d(%s%s)" % (i + 1, "/".join(rep), "删分句" if narr_left else "删幅"))
            if narr_left:
                out.append("\n".join(kept_lines))
            elif kept_lines and out:
                out[-1] = out[-1].rstrip() + "\n" + "\n".join(kept_lines)
            continue
        for k in hits:
            seen.add(k)
        out.append(p)
    if dropped and log is not None:
        log.append("删重复动作幅：" + "、".join(dropped))
    return "\n\n".join(out)


def ensure_subjects(script, names):
    """每幅第一句必须以人名开头：他/她 → 换成上一幅最后出现的人名；直接以动作或身体部位开头 → 补人名。"""
    names = [n for n in (names or []) if n]
    if not names:
        return script
    paras = [p for p in re.split(r"\n\s*\n", str(script or "").strip()) if p.strip()]
    last_name = names[0]
    out = []
    for p in paras:
        lines = p.split("\n")
        first = lines[0].strip()
        if not re.match(r"^[^\n：:]{1,8}[：:].+$", first):
            starts_name = any(first.startswith(n) for n in names)
            m = re.match(r"^(他们|她们|他|她)(?=[^\n]{0,3}[一-龥])", first)
            if m:
                first = last_name + first[len(m.group(1)):]
            elif not starts_name and re.match(r"^(?:上半身|下半身|身体|身子|双手|双臂|左手|右手|左脚|右脚|左腿|右腿|手指|指尖|手臂|肩膀|脖颈|头|眼睛|目光|嘴角|脸|背部|膝盖|脚)", first):
                first = last_name + first
            lines[0] = first
        # 记住这一幅最后出现的人名，给下一幅用
        pos = [(p.rfind(n), n) for n in names if n in p]
        if pos:
            last_name = max(pos)[1]
        out.append("\n".join(lines))
    return "\n\n".join(out)


def tidy_script(script, names=None):
    """台词孤行并回上一幅；同一句台词只留第一次。"""
    paras = [p for p in re.split(r"\n\s*\n", str(script or "").strip()) if p.strip()]
    out = []
    seen = {}
    norm = lambda q: re.sub(r"[^一-龥]", "", q)
    for p in paras:
        lines = p.split("\n")
        kept = []
        seen["_last"] = None                          # 判重只在一幅之内
        for l in lines:
            m = re.match(r"^([^\n：:]{1,8})[：:](.+)$", l.strip())
            if m and (not names or m.group(1).strip() in names):
                key = m.group(1).strip() + "|" + norm(m.group(2))
                if key and key == seen.get("_last"):
                    continue                          # 只删同一个人紧挨着说的同一句；不同人/不同幅允许重复
                seen["_last"] = key
            kept.append(l)
        if not kept:
            continue
        only_dlg = all(re.match(r"^([^\n：:]{1,8})[：:](.+)$", l.strip()) for l in kept)
        if only_dlg and out:
            out[-1] = out[-1].rstrip() + "\n" + "\n".join(kept)
        else:
            out.append("\n".join(kept))
    # 双冒号「李长渊：：师弟」→ 一个；句尾孤悬的连词「……胸前，又。」删掉（P85）
    out = [re.sub(r"(?m)^([^\n：:]{1,8})[：:]\s*[：:]+", r"\1：", p) for p in out]
    out = [re.sub(r"[，、]\s*(?:又|却|但|而|并|再|也|还|随即|然后|接着|随后)\s*[。！？]", "。", p) for p in out]
    out = [re.sub(r"(?m)^(?:又|却|但|而|并|再|也|还|随即|然后|接着|随后)\s*[。！？]\s*$\n?", "", p) for p in out]
    # 站位栏原文抄进剧本的句子删掉（"左阿离、中李长渊、右：苏清婉，三人隔0.5步站立，脸朝左。"）
    _BLK = re.compile(r"(?:^|[。；])[^。；]*?(?:^|[，、])?(?:左[：:]?[一-龥]{2,4}[、，]|隔\s*[0-9０-９半一两]+\s*步|脸朝[左右前后内外下]|三人隔|站位)[^。；]*[。；]?")
    cleaned = []
    for p in out:
        ls = []
        for l in p.split("\n"):
            if re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip()):
                ls.append(l)
                continue
            l2 = _BLK.sub(lambda m: "。" if m.group(0).startswith(("。", "；")) else "", l)
            l2 = re.sub(r"^[。；，、\s]+", "", l2).replace("。。", "。").strip()
            if l2:
                ls.append(l2)
        if ls:
            cleaned.append("\n".join(ls))
    out = cleaned
    # 全篇重复 ≥3 次的分句只留第一次（"正午日光从落地窗…""指节因用力而泛白"）
    cnt = {}
    for p in out:
        for l in p.split("\n"):
            if re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip()):
                continue
            for c in re.split(r"[，。；！？]", l):
                k = norm(c)
                if len(k) >= 6:
                    cnt[k] = cnt.get(k, 0) + 1
    rep_keys = {k for k, v in cnt.items() if v >= 3}
    if rep_keys:
        seen_c = set()
        new_out = []
        for p in out:
            ls = []
            for l in p.split("\n"):
                if re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip()):
                    ls.append(l)
                    continue
                parts_ = re.split(r"(?<=[，。；！？])", l)
                kept = []
                for c in parts_:
                    k = norm(c)
                    if k in rep_keys:
                        if k in seen_c:
                            continue
                        seen_c.add(k)
                    kept.append(c)
                t = "".join(kept).strip()
                t = re.sub(r"^[，；]+", "", t)
                t = re.sub(r"[，；]+([。！？])", r"\1", t)
                if t and t[-1] not in "。！？":
                    t = t.rstrip("，；") + "。"
                if len(norm(t)) >= 4:
                    ls.append(t)
            if ls and any(not re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip()) for l in ls):
                new_out.append("\n".join(ls))
            elif ls and new_out:
                new_out[-1] = new_out[-1].rstrip() + "\n" + "\n".join(ls)
        out = new_out
    # 相邻幅重复的句子删掉（幅20 = 幅19 + 一句）；同一个人第二次入画的分句删掉
    _ENTER = r"(?:冲出|冲入|切入|走入|跑入|进入|闯入|跨入)[^，。；]{0,6}?(?:画面|镜头|画|巷)?"
    seen_enter = set()
    prev_sents = set()
    fixed = []
    for p in out:
        ls = []
        cur_sents = set()
        for l in p.split("\n"):
            if re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip()):
                ls.append(l)
                continue
            sents = [x for x in re.split(r"(?<=[。！？])", l) if x.strip()]
            keep = []
            for x in sents:
                key = norm(x)
                if key and key in prev_sents:
                    continue
                # 入画分句
                for n in (names or []):
                    m = re.search(re.escape(n) + r"[^，。；]{0,6}?从[^，。；]{0,10}?" + _ENTER + r"[^，。；]*[，。；]?", x)
                    if m:
                        if n in seen_enter:
                            x = x.replace(m.group(0), "", 1)
                            x = re.sub(r"^[，、；\s]+", "", x)
                            if x and not any(x.startswith(k) for k in (names or [])):
                                x = n + x
                        else:
                            seen_enter.add(n)
                        break
                if x.strip():
                    keep.append(x.strip())
                    cur_sents.add(norm(x))
            if keep:
                ls.append("".join(keep))
        prev_sents = cur_sents
        if ls and any(not re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip()) for l in ls):
            fixed.append("\n".join(ls))
        elif ls and fixed:
            fixed[-1] = fixed[-1].rstrip() + "\n" + "\n".join(ls)
    out = fixed
    # 叙述完全相同的两幅只留第一幅，后一幅的台词并进去
    def _narr(p):
        return norm("".join(l for l in p.split("\n") if not re.match(r"^([^\n：:]{1,8})[：:](.+)$", l.strip())))
    res, seen_n = [], {}
    for p in out:
        key = _narr(p)
        if key and key in seen_n:
            dlg = [l for l in p.split("\n") if re.match(r"^([^\n：:]{1,8})[：:](.+)$", l.strip())]
            if dlg:
                res[seen_n[key]] = res[seen_n[key]].rstrip() + "\n" + "\n".join(dlg)
            continue
        seen_n[key] = len(res)
        res.append(p)
    return ensure_subjects("\n\n".join(res), names)


def script_shape_problems(script, outline):
    """剧本形状守卫：每拍要 2～4 幅、全篇 1400 字以上。返回问题清单（空＝过）。"""
    paras = [p for p in re.split(r"\n\s*\n", str(script or "").strip()) if p.strip()]
    beats = max(1, len(outline or []))
    probs = []
    if len(script or "") < 3200:
        probs.append("只有 %d 字（两分钟要 3200～4500）" % len(script or ""))
    if len(paras) < beats * 5:
        probs.append("只有 %d 幅画面（%d 拍要 %d 幅以上）" % (len(paras), beats, beats * 5))
    return probs


def script_name_problems(script, names):
    """剧本人名守卫：出现的名字都要在人物卡里；卡上的在场角色至少各出现一次。"""
    if not names:
        return []
    probs = []
    for n in names:
        pass
    used = [n for n in names if n in str(script or "")]
    if not used:
        probs.append("剧本里一个人物卡上的名字都没有")
    # 段首「名字，」这种写法要纠正
    if re.search(r"^(%s)[，,]" % "|".join(re.escape(n) for n in names), str(script or ""), re.M):
        probs.append("有段落以「名字，」开头（应写成连贯的句子）")
    return probs


def write_script(elements, outline, chars_digest="", place="", names=None, extra=""):
    el = {k: v for k, v in (elements or {}).items() if not k.startswith("_")}
    user = ["【要素表】"] + ["%s：%s" % (k, v) for k, v in el.items()]
    if names:
        user.append("【人物表】" + "、".join(names) + "——只用这些名字，一个名字只对应一个人。")
    user.append("\n【大纲卡】")
    for r in outline:
        user.append("｜".join([r["beat"], r["share"], r["event"], r["action"], r["line"], r["known"], r["blocking"]]))
    if chars_digest:
        user.append("\n" + chars_digest)
    if place:
        user.append("\n【这个地方有的东西】" + place)
    rep = au._q(_SCRIPT_SYS + (("\n\n" + extra) if extra else ""), "\n".join(user), mt=4600, temperature=0.75)
    return str(rep or "").strip()


# ───────────────── 第 4 步：审校（判断题） ─────────────────
_COLD_SYS = """你是第一次看这部短片的观众，只看下面的剧本。用**六行**回答（下面六项一项都不能少），每行一句话，格式「项名：内容」：
讲了什么：这一话发生了什么事（一句）
主角要什么：
谁挡他：
谁是敌人：画面里**什么在跟主角作对**——可以是人、野兽、机器，也可以是正在关的门、塌下来的石头、追上来的火，写清它是什么、怎么挡主角（看不出来就写「看不出来」）
局面在哪一刻变了：中间哪一件事、哪个新知道的事实让情况和开头不一样了（没有就写「没有」）
结尾悬念：最后停在什么上
只回答，不评价。"""

_JUDGE_SYS = """下面左边是编剧的意图，右边是一个观众冷读剧本后的回答。逐项判断观众有没有看懂，每项只回「对」或「错」，格式「项名：对/错」。
项名固定六个：讲了什么、主角要什么、谁挡他、谁是敌人、局面在哪一刻变了、结尾悬念。
「谁是敌人」：观众答的和编剧写的对手是同一个就算对；**对手是机关、环境、动物、倒计时这类不是人的东西时，观众答出那个东西也算对**，不要求是"人"。答「看不出来」才算错。
「结尾悬念」：**只看观众答的是不是停在一个没做完的动作、没说完的话、没揭晓的结果上**（手停在半空、蓄力要扑、门推开一半、话说了一半、胜负未分）——是就算对，**不要求和编剧写的那句一致**，剧本中途改过是正常的。观众答「结束了/没有悬念/尘埃落定」才算错。「局面在哪一刻变了」这一项：观众答出的事实和编剧的「转折事实」是同一件事才算对，答「没有」算错。"""


_COLD_KEYS = ("讲了什么", "主角要什么", "谁挡他", "谁是敌人", "局面在哪一刻变了", "结尾悬念")


def cold_read(script):
    """六项都要答到。mt 给够（300 会答到一半被截断），缺项就带着项名重问一次。"""
    out = _kv(au._q(_COLD_SYS, str(script or ""), mt=520, temperature=0.2))
    miss = [k for k in _COLD_KEYS if not str(out.get(k) or "").strip()]
    if miss:
        rep2 = au._q(_COLD_SYS + "\n\n★上一次你漏答了：%s。六项一项都不能少，每项一行。" % "、".join(miss),
                     str(script or ""), mt=520, temperature=0.2)
        out2 = _kv(rep2)
        for k in _COLD_KEYS:
            if not str(out.get(k) or "").strip() and str(out2.get(k) or "").strip():
                out[k] = out2[k]
    return out


def judge(elements, outline, cold):
    intent = ("讲了什么：%s\n主角要什么：%s\n谁挡他：%s\n谁是敌人：%s\n局面在哪一刻变了（转折事实）：%s\n结尾悬念：%s" % (
        "；".join(r["event"] for r in outline)[:200], elements.get("主角要什么", ""), elements.get("谁挡他", ""),
        elements.get("对手是什么", "") or elements.get("谁挡他", ""),
        elements.get("转折事实", "") or "（编剧没定）", elements.get("结尾钩子", "")))
    keys = ("讲了什么", "主角要什么", "谁挡他", "谁是敌人", "局面在哪一刻变了", "结尾悬念")
    ans = "\n".join("%s：%s" % (k, cold.get(k, "")) for k in keys)
    rep = au._q(_JUDGE_SYS, "【编剧意图】\n" + intent + "\n\n【观众回答】\n" + ans, mt=160, temperature=0.1)
    kv = _kv(rep)
    out = {k: (kv.get(k, "").startswith("对")) for k in keys}
    if not str(elements.get("转折事实") or "").strip():
        out["局面在哪一刻变了"] = not re.search(r"没有|无", str(cold.get("局面在哪一刻变了", "")))
    # 谁是敌人：观众答里出现了对手的关键词就算过（对手是机关/环境时判卷常判"不是人"）
    _foe_txt = str(elements.get("谁挡他") or "") + str(elements.get("对手是什么") or "")
    _foe_key = [w for w in re.findall(r"[一-龥]{2,5}", _foe_txt)
                if w not in ("主角", "对手", "身份", "试图", "因为", "目的", "设定")][:8]
    # 日常档没有对手：「谁挡他」「谁是敌人」两题不问（设计 v2：日常放开对手必填）
    if str(elements.get("_pace") or "") == "日常" and str(elements.get("谁挡他") or "").strip() in ("", "无", "没有"):
        out["谁挡他"] = True
        out["谁是敌人"] = True
    _ans_foe = str(cold.get("谁是敌人", ""))
    if _ans_foe and not re.search(r"看不出|不清楚|没有敌人", _ans_foe) and any(k in _ans_foe for k in _foe_key):
        out["谁是敌人"] = True
    # 结尾悬念：观众答的只要停在没做完的动作/没说完的话/没揭晓的结果上就算过（判卷常因不一字对应误判）
    _tail_ans = str(cold.get("结尾悬念", ""))
    if _tail_ans and not re.search(r"结束了|尘埃落定|没有悬念|圆满|收场", _tail_ans):
        if re.search(r"未|没|将要|正要|即将|悬|停在|半空|一半|蓄力|准备|欲|待|不明|胜负|是否|能否|会不会|逼近|袭来|抬起|张口|伸出", _tail_ans):
            out["结尾悬念"] = True
    return out


def hook_check(script, elements):
    """第一幅里有没有钩子：入口事件的动词 / 一句台词。代码判。"""
    first = [p for p in re.split(r"\n\s*\n", str(script or "").strip()) if p.strip()]
    head = "\n".join(first[:2]) if first else ""
    has_line = bool(re.search(r"^[^\n：:]{1,8}[：:].+$", head, re.M))
    has_act = bool(re.search(r"推|拉|抓|夺|喊|冲|砸|跪|拦|挡|摔|撞|递|逼近|后退|转身|扇|甩|按|抱|拽|跨|问|吼|扑|拔|举", head))
    no_env = not re.match(r"^\s*(清晨|黄昏|夜|雾|雨|风|天光|远处|山|街|城)", head)
    return {"台词": has_line, "动作": has_act, "不是环境开场": no_env}


def fix_head_code(script, outline, names, hk):
    """开场不合格 → 纯代码修：拍一台词挪进前两幅；幅一开头没有人名的环境句删掉。"""
    paras = [p for p in re.split(r"\n\s*\n", str(script or "").strip()) if p.strip()]
    if len(paras) < 3:
        return script, "太短不修"
    notes = []
    if not hk.get("不是环境开场", True):
        lines = paras[0].split("\n")
        first = lines[0]
        sents = [x for x in re.split(r"(?<=[。！？；])", first) if x.strip()]
        k = next((i for i, x in enumerate(sents) if any(n in x for n in (names or []))), None)
        if k:
            lines[0] = "".join(sents[k:])
            paras[0] = "\n".join(lines)
            notes.append("删了开头 %d 句环境" % k)
    if not hk.get("台词", True):
        line_re = re.compile(r"^[^\n：:]{1,8}[：:].+$", re.M)
        moved = False
        for j in range(2, min(8, len(paras))):          # 前 8 幅里已有的台词挪过来（P75：原来只看幅3、4）
            m = line_re.search(paras[j])
            if m:
                ln = m.group(0)
                paras[j] = line_re.sub("", paras[j], count=1).strip()
                paras[1] = paras[1].rstrip() + "\n" + ln
                notes.append("把幅%d的台词挪到幅2" % (j + 1))
                moved = True
                break
        if not moved and outline:
            # 只接大纲拍1 的**第一句**，且剧本里还没有这句（P75：整串三句接上去，幅1 挂三行、后面又重复）
            _norm = lambda q: re.sub(r"[^一-龥]", "", q)
            _all = _norm("\n\n".join(paras))
            for v in split_lines((outline[0] or {}).get("line")):
                m = re.match(r"^([^：:]{1,8})[：:]\s*(.+)$", str(v).strip())
                if not (m and (not names or m.group(1).strip() in names)):
                    continue
                _txt = m.group(2).strip().strip("“”「」")
                if _norm(_txt) and _norm(_txt) in _all:
                    break                                     # 已经在别的幅里了，不重复接
                paras[0] = paras[0].rstrip() + "\n%s：%s" % (m.group(1).strip(), _txt)
                notes.append("拍一台词接到幅1")
                break
    paras = [p for p in paras if p.strip()]
    return "\n\n".join(paras), ("开场代码修：" + "；".join(notes)) if notes else "开场未改"


def fix_head(script, elements, why, outline=None):
    """开场不合格：只重写前两幅，要求第一幅就是入口事件+台词；后面拍的动作不许提前出现。"""
    paras = [p for p in re.split(r"\n\s*\n", str(script or "").strip()) if p.strip()]
    if len(paras) < 3:
        return script, "太短不修"
    head = "\n\n".join(paras[:2])
    nxt = paras[2] if len(paras) > 2 else ""
    later = "；".join(str(r.get("event", "")) for r in (outline or [])[1:]) or "后面几拍的事"
    line1 = (outline or [{}])[0].get("line", "") if outline else ""
    sysm = ("你是这部短片的编剧。下面是开场两幅，问题：%s。重写这两幅：第一幅必须是「%s」正在发生（用名字+动作动词写清谁对谁做什么），"
            "带这一句台词（格式「名字：台词」单独一行）：%s。不写天气、不写走路、不写外貌铺陈。"
            "**这两幅里只能有这一件事，不许出现后面才发生的事：%s**。物件在谁手里、谁站哪，要和下一幅接得上。下一幅是：%s\n"
            "只输出这两幅，段落之间空一行，不加新人物。"
            % (why, elements.get("入口事件", ""), line1 or "一句 ≤15 字的台词", later[:160], nxt[:200]))
    new, note = au._rewrite_block(head, sysm, 60, 420, allow_new_quotes=True, temperature=0.5)
    if not new:
        return script, "开场重写丢弃（%s）" % note
    np_ = [p for p in re.split(r"\n\s*\n", new.strip()) if p.strip()]
    first_narr = "".join(l for l in (np_[0] if np_ else "").split("\n") if not re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip()))
    if len(np_) != 2 or len(first_narr) < 40 or not re.search(r"[一-龥]{2,4}(?:抬|伸|握|抓|推|拽|夺|递|接|冲|转|走|跨|扑|挡|按|扣|拉|举|指|甩)", first_narr):
        return script, "开场重写丢弃（%d 幅/首幅 %d 字，不够一幅画）" % (len(np_), len(first_narr))
    return "\n\n".join([new] + paras[2:]), "开场已重写"


def fix_tail(script, elements):
    paras = [p for p in re.split(r"\n\s*\n", str(script or "").strip()) if p.strip()]
    if len(paras) < 3:
        return script, "太短不修"
    tail = "\n\n".join(paras[-2:])
    prv = paras[-3] if len(paras) > 2 else ""
    sysm = ("你是这部短片的编剧。下面是结尾两幅。重写它们，让全片停在「%s」——一个没做完的动作或一句说了一半的话，"
            "用名字+动作写清最后一帧是谁的手在哪、脸朝哪；**两幅之间以及和上一幅要接得上，同一个动作不做两遍**。上一幅是：%s\n"
            "只输出这两幅，段落之间空一行，不加新人物，台词 ≤15 字。" % (elements.get("结尾钩子", ""), prv[:200]))
    new, note = au._rewrite_block(tail, sysm, 60, 420, allow_new_quotes=True, temperature=0.5)
    if not new:
        return script, "结尾重写丢弃（%s）" % note
    # 重写不能把钩子改没了（2026-09-05：钩子是"手指触到令牌"，重写后成了"长老笑、主角离去"）
    _hook = str((elements or {}).get("结尾钩子") or "")
    _keys = [w for w in re.findall(r"[一-龥]{2,4}", _hook) if w not in ("主角", "结尾", "画面", "然后", "接着")][:6]
    if _keys and not any(k in new for k in _keys):
        return script, "结尾重写丢弃（把钩子「%s」改没了）" % _hook[:20]
    if re.search(r"离去|走开|散去|结束|落幕|尘埃落定|松了口气后转身", new) and not re.search(r"未|没|正要|即将|停在|半空|悬", new):
        return script, "结尾重写丢弃（改成了收场，不是钩子）"
    return "\n\n".join(paras[:-2] + [new]), "结尾已重写"


def fix_goal(script, elements, item, names=None):
    """观众没看懂目标/对手：让模型只出一行台词「名字：台词」，代码接到前 30% 里说话人最后出现的那一幅末尾。"""
    paras = [p for p in re.split(r"\n\s*\n", str(script or "").strip()) if p.strip()]
    if len(paras) < 3:
        return script, "太短不修"
    k = max(2, len(paras) * 3 // 10)
    blk = "\n\n".join(paras[:k])
    if item == "主角要什么":
        want = elements.get("主角要什么", "")
        fact = "主角这一话要做的事：%s" % want
    elif item == "谁是敌人":
        want = elements.get("对手是什么", "") or elements.get("谁挡他", "")
        fact = "观众要一眼分清敌我，这个对手是：%s" % want
    else:
        want = elements.get("谁挡他", "")
        fact = "挡在主角面前的是：%s" % want
    sysm = ("你是这部短片的编剧。观众看了下面的开头没看出来这件事：%s。请写**一句**能把它说出口的台词，由在场的人说（用名字）。"
            "要求：≤15 字、口语、像人会说的话；不许出现「主角」「要什么」「谁挡他」这类字眼，不许再加冒号。"
            "**只输出一行，格式：名字：台词**，不要别的字。" % fact)
    try:
        rep = str(au._q(sysm, blk, mt=60, temperature=0.5) or "").strip()
    except Exception as ex:
        return script, "补%s失败（%s）" % (item, str(ex)[:30])
    line = ""
    for l in rep.splitlines():
        m = re.match(r"^\s*[「\"“]?([^\n：:「」“”\"]{1,8})[：:]\s*(.+?)[」\"”]?\s*$", l.strip())
        if m:
            who, txt = m.group(1).strip(), m.group(2).strip().strip("“”「」\"")
            if (not names or who in names) and 1 <= len(txt) <= 18 and not re.search(r"主角|要什么|谁挡|项名|[：:]", txt):
                line = "%s：%s" % (who, txt)
                break
    if not line:
        return script, "补%s丢弃（模型没给出合格的一行：%s）" % (item, rep[:30])
    who = line.split("：")[0]
    idx = next((i for i in range(k - 1, -1, -1) if who in paras[i]), k - 1)
    if re.search(r"^[^\n：:]{1,8}[：:].+$", paras[idx], re.M) and idx + 1 < len(paras):
        idx = idx + 1 if not re.search(r"^[^\n：:]{1,8}[：:].+$", paras[idx + 1], re.M) else idx
    paras[idx] = paras[idx].rstrip() + "\n" + line
    return "\n\n".join(paras), "已补%s（接在幅%d：%s）" % (item, idx + 1, line)


def outline_lines(outline, names):
    """大纲卡里的台词 → [(说话人, 台词)]。一拍可能有多句。"""
    out = []
    for r in outline or []:
        for v in split_lines(r.get("line")):
            m = re.match(r"^([^：:]{1,8})[：:]\s*(.+)$", v)
            if m and (not names or m.group(1).strip() in names):
                out.append((m.group(1).strip(), m.group(2).strip().strip("“”\"「」")))
            elif v:
                out.append(("", v))
    return out


def fix_line_speakers(script, outline, names):
    """剧本里的台词行和大纲卡对：同一句话说话人不一样 → 按大纲卡改名字。返回 (剧本, 改了几处)。"""
    norm = lambda q: re.sub(r"[^一-龥]", "", q)
    want = {norm(t): who for who, t in outline_lines(outline, names) if who and t}
    if not want:
        return script, 0
    n = 0
    out = []
    for line in str(script or "").splitlines():
        m = re.match(r"^([^\n：:]{1,8})[：:](.+)$", line.strip())
        if m and m.group(1).strip() in (names or []):
            key = norm(m.group(2))
            hit = want.get(key, "") or next((w for k, w in want.items() if k and len(k) >= 4 and k in key), "")
            if hit and hit != m.group(1).strip():
                out.append("%s：%s" % (hit, m.group(2).strip()))
                n += 1
                continue
        out.append(line)
    return "\n".join(out), n


# ───────────────── 连贯性（物件在谁手里 / 谁站哪 / 谁做过什么） ─────────────────
_CONT_SYS = """你是剪辑助理。下面是一部短片的画面剧本，每幅画面前有〔幅N〕编号。只找**前后矛盾**：
· 一件物件在谁手里前后不接（比如上一幅已经接过/喝掉，下一幅又在原来的人手里）；
· 一个人已经做过的动作又重做一遍，或者位置/站位前后对不上；
· 已经离开画面的人又无缘无故出现；
· 物件悬在空中、自己移动、或凭空多出一个。
找到就一行一条，格式「幅号｜一句话说清矛盾｜前一幅原句片段｜这一幅原句片段」，两个片段必须是剧本里逐字存在的原文（各 6～20 字）；最多三条，**没有就只回「无」，不要硬凑**。左手右手这种不影响剧情的差别不算矛盾。不要别的话。"""


def continuity_pass(script, names, log=None):
    paras = [p for p in re.split(r"\n\s*\n", str(script or "").strip()) if p.strip()]
    if len(paras) < 6:
        return script, "太短不查"
    numbered = "\n\n".join("〔幅%d〕%s" % (i + 1, p) for i, p in enumerate(paras))
    try:
        rep = au._q(_CONT_SYS, numbered, mt=300, temperature=0.1)
    except Exception as ex:
        return script, "连贯性问答失败：%s" % str(ex)[:40]
    issues = []
    norm_all = re.sub(r"[^一-龥]", "", script or "")
    for line in str(rep or "").splitlines():
        m = re.match(r"^\s*[〔\[]?\s*幅?\s*(\d+)\s*[〕\]]?\s*[｜|]\s*(.+)$", line.strip())
        if not (m and 1 <= int(m.group(1)) <= len(paras)):
            continue
        cols = [c.strip() for c in re.split(r"[｜|]", m.group(2))]
        why = cols[0][:80]
        quotes = [re.sub(r"[^一-龥]", "", c) for c in cols[1:3]]
        if len(quotes) < 2 or any(len(q) < 4 or q not in norm_all for q in quotes):
            if log is not None:
                log.append("幅%d 矛盾无证据，忽略：%s" % (int(m.group(1)), why[:24]))
            continue
        if re.search(r"左手|右手|左臂|右臂", why) and not re.search(r"夺|接|递|放|拿|抽|在[一-龥]{1,4}手", why):
            continue
        issues.append((int(m.group(1)), why))
    if not issues:
        return script, "连贯性通过"
    fixed = 0
    for k, why in issues[:3]:
        i = k - 1
        prev_p = paras[i - 1] if i > 0 else ""
        next_p = paras[i + 1] if i + 1 < len(paras) else ""
        blk = paras[i]
        dlg = re.findall(r"^[^\n：:]{1,8}[：:].+$", blk, re.M)
        sysm = ("你是这部短片的编剧。下面是一幅画面，它和前后幅有矛盾：%s。\n上一幅：%s\n下一幅：%s\n"
                "只重写这一幅，让物件在谁手里、谁站哪、谁做了什么和前后接上；台词行（名字：台词）原样保留、不加台词；"
                "不加新人物；字数和原来差不多。只输出这一幅。" % (why, prev_p[:220], next_p[:220]))
        new, note = au._rewrite_block(blk, sysm, max(20, int(len(blk) * 0.5)), int(len(blk) * 1.6) + 40, temperature=0.4)
        if new and all(d in new for d in dlg):
            # 多段用单换行拼着 → 叙述行之间补空行，让下游按幅切
            lines = [l for l in new.split("\n") if l.strip()]
            rebuilt, buf = [], []
            for l in lines:
                if re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip()):
                    buf.append(l.strip())
                else:
                    if buf:
                        rebuilt.append("\n".join(buf))
                    buf = [l.strip()]
            if buf:
                rebuilt.append("\n".join(buf))
            paras[i] = "\n\n".join(rebuilt) if rebuilt else new
            fixed += 1
        elif log is not None:
            log.append("幅%d 未修：%s" % (k, note))
    return "\n\n".join(paras), "连贯性：查出 %d 处，修了 %d 处（%s）" % (len(issues), fixed, "；".join("幅%d %s" % (k, w[:24]) for k, w in issues))


# ───────────────── 微表情限次 / 台词不许念要素表 ─────────────────
# 【按裸词收】原来写死了搭配（喉结"滚动"、瞳孔"收缩"），模型换个动词就漏
# ——「喉结上下滑动」「瞳孔骤然一缩」全没被限次，删了 7 处仍剩 19 次（2026-09-05 实测）。
# 这几个词在画面剧本里基本只出现在微表情语境，直接按词收，和体检同一个口径。
_MICRO_PATS = [("指节泛白", r"指节|指关节"),
               ("眉头紧锁", r"眉头|眉心"),
               ("嘴唇微张", r"嘴唇微张|唇微张|嘴唇紧抿|嘴角[^，。；]{0,4}(?:抿|勾|上扬|下撇)"),
               ("瞳孔收缩", r"瞳孔"),
               ("喉结滚动", r"喉结"),
               ("呼吸", r"呼吸[^，。；]{0,4}(?:急促|凝滞|粗重|变得)|胸口[^，。；]{0,4}起伏"),
               ("眼神", r"眼神[^，。；]{0,6}(?:从[^，。；]{1,6}转为|转为|闪过|锐利|冷冽|游移|躲闪)|目光[^，。；]{0,4}(?:游移|闪躲|死死)")]


_ONOMAT = re.compile(r"[吼嗷嘶咆哮嗥呜嚎啊呀哈哦噢唔嗯哼]{1,4}[！!。～~…—─\-－\s]*")


_REWRITE_DUP_SYS = """你是短剧编剧。下面这句台词在这一话里已经说过一遍了，现在要给它换一句新的。
· 换的这句由**同一个人**说，接住它所在那一幅的动作和处境；
· ≤15 字，口语，人真会说出口的话；
· 不许和已经说过的任何一句重样，也不许只改一两个字；
· 只输出新台词本身，不要名字、不要冒号、不要引号、不要解释。"""


def dedupe_lines_global(script, log=None, prev_lines=None):
    """全篇同一个人说了同样的话 → 后面那句改写；改不出来就删（删到只剩 10 句就不删了）。
    prev_lines：前面各话的台词行（P122：「没叫你，看你」第 1、2 话各说一遍），预置进去重表。"""
    lines = str(script or "").splitlines()
    seen = {}
    for pl in (prev_lines or []):
        m0 = re.match(r"^([^\n：:]{1,8})[：:]\s*(.+)$", str(pl).strip())
        if m0:
            seen[(m0.group(1).strip(), re.sub(r"[，。！？、…\s]", "", m0.group(2)))] = -1
    dups = []                                  # [(行号, 说话人, 原句)]
    for i, ln in enumerate(lines):
        m = re.match(r"^([^\n：:]{1,8})[：:]\s*(.+)$", ln.strip())
        if not m:
            continue
        key = (m.group(1).strip(), re.sub(r"[，。！？、…\s]", "", m.group(2)))
        if key in seen:
            dups.append((i, m.group(1).strip(), m.group(2).strip()))
        else:
            seen[key] = i
    if not dups:
        return script
    total = sum(1 for ln in lines if re.match(r"^[^\n：:]{1,8}[：:].+$", ln.strip()))
    drop = []
    for i, who, txt in dups:
        # 拟声重复直接删。交给模型改写会把野兽改成会说话的
        # （2026-09-05 实测：「白狼：嘶！」→「白狼：再来」，狼开口说中文了）
        if _ONOMAT.fullmatch(txt.strip()):
            drop.append(i)
            continue
        ctx = "\n".join(lines[max(0, i - 4):i])              # 这句之前几行的画面
        said = "、".join(sorted({re.sub(r"^[^\n：:]{1,8}[：:]\s*", "", x.strip())
                                for x in lines if re.match(r"^[^\n：:]{1,8}[：:].+$", x.strip())}))[:200]
        try:
            new = str(au._q(_REWRITE_DUP_SYS,
                            "【这一幅之前发生了什么】\n%s\n\n【说话的人】%s\n【要换掉的重复台词】%s\n【这一话已经说过的话】%s"
                            % (ctx[:600], who, txt, said), mt=60, temperature=0.7) or "").strip()
        except Exception:
            new = ""
        new = re.sub(r"^[^\n：:]{1,8}[：:]\s*", "", new.splitlines()[0] if new else "")
        new = new.strip().strip("\u201c\u201d\"\u300c\u300d")
        ok = (new and len(new) <= 15 and new != txt
              and re.sub(r"[，。！？、…\s]", "", new) not in
              {re.sub(r"[，。！？、…\s]", "", x) for x in re.split(r"、", said)})
        if ok:
            lines[i] = "%s：%s" % (who, new)
            if log is not None:
                log.append("重复台词改写：%s「%s」→「%s」" % (who, txt, new))
        else:
            drop.append(i)
    if drop and total - len(drop) >= 10:
        for i in sorted(drop, reverse=True):
            if log is not None:
                log.append("重复台词删除：%s" % lines[i].strip()[:24])
            lines.pop(i)
    elif drop and log is not None:
        log.append("重复台词 %d 句改写失败，但删了会不够 10 句，留着" % len(drop))
    return "\n".join(lines)


def cap_micro_expressions(script, per_kind=2, log=None):
    """同一类微表情全篇最多 per_kind 次，多出来的分句删掉。跑到不动点。

    2026-09-05：这个函数**不是幂等的**——删掉分句后标点被重排，分句边界跟着变，
    上一遍没数到的那些这一遍才露出来。线上只跑一遍，结果 28 处删到 16 处就停了
    （上限 14）。所以这里循环到不再删为止，最多三遍。
    """
    prev = str(script or "")
    total = 0
    # 上限 12 遍：3 遍不够（东方公路电影原始 31 处，3 遍只删到 16，上限 14）。
    # 每遍都会删掉一些，收敛很快，12 遍只是保险。
    for _ in range(12):
        _lg = []
        out = _cap_micro_once(prev, per_kind, _lg)
        n = 0
        for m in _lg:
            try:
                n = int(re.search(r"删了 (\d+) 处", m).group(1))
            except Exception:
                n = 0
        total += n
        if out == prev or not n:
            prev = out
            break
        prev = out
    if total and log is not None:
        log.append("微表情限次：删了 %d 处" % total)
    return prev


def _cap_micro_once(script, per_kind=2, log=None):
    """限次跑一遍。"""
    paras = [p for p in re.split(r"\n\s*\n", str(script or "").strip()) if p.strip()]
    count = {k: 0 for k, _ in _MICRO_PATS}
    removed = 0
    out = []
    for p in paras:
        ls = []
        for l in p.split("\n"):
            if re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip()):
                ls.append(l)
                continue
            clauses = re.split(r"(?<=[，。；！？])", l)
            keep = []
            _first_narr = not any(not re.match(r"^[^\n：:]{1,8}[：:].+$", x.strip()) for x in ls)   # 这一幅的第一行叙述
            for ci, c in enumerate(clauses):
                drop = False
                for k, pat in _MICRO_PATS:
                    if re.search(pat, c):
                        count[k] += 1
                        if count[k] > per_kind and not (_first_narr and ci == 0):   # 幅首第一分句是主语所在，只计数不删（P129f）
                            drop = True
                        break
                if drop:
                    removed += 1
                    continue
                keep.append(c)
            t = "".join(keep).strip()
            t = re.sub(r"^[，；、]+", "", t)
            t = re.sub(r"[，；]+([。！？])", r"\1", t)
            if t and t[-1] not in "。！？":
                t = t.rstrip("，；") + "。"
            if len(re.sub(r"[^一-龥]", "", t)) >= 6:
                ls.append(t)
        narr = [l for l in ls if not re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip())]
        if narr:
            out.append("\n".join(ls))
        elif ls and out:
            out[-1] = out[-1].rstrip() + "\n" + "\n".join(ls)
    if removed and log is not None:
        log.append("微表情限次：删了 %d 处" % removed)
    return "\n\n".join(out)


def drop_meta_lines(script, elements, log=None):
    """台词与要素表原文（转折事实/代价/主角要什么/谁挡他）重合 ≥60% → 删这句台词（模型把设定念成了台词）。"""
    norm = lambda q: re.sub(r"[^一-龥]", "", str(q or ""))
    facts = [norm(elements.get(k, "")) for k in ("转折事实", "代价", "主角要什么", "谁挡他")]
    facts = [f for f in facts if len(f) >= 6]
    if not facts:
        return script
    def _overlap(a, b):
        if not a or not b:
            return 0.0
        grams = {a[i:i + 2] for i in range(len(a) - 1)}
        hit = sum(1 for i in range(len(b) - 1) if b[i:i + 2] in grams)
        return hit / max(1, len(b) - 1)
    out, n = [], 0
    for l in str(script or "").split("\n"):
        m = re.match(r"^([^\n：:]{1,8})[：:](.+)$", l.strip())
        if m:
            t = norm(m.group(2))
            if len(t) >= 6 and any(_overlap(f, t) >= 0.6 for f in facts):
                n += 1
                continue
        out.append(l)
    if n and log is not None:
        log.append("删了 %d 句念设定的台词" % n)
    return "\n".join(out)


# ───────────────── 光线守卫（时间与光线全篇统一） ─────────────────
_DAY_WORDS = r"阳光|晨光|日光|正午|白昼|晨雾|朝阳|日头|天光"
_NIGHT_WORDS = r"霓虹|月光|夜色|夜风|夜幕|烛火|烛光|灯笼[^，。]{0,3}(?:暖光|光晕|昏黄|的光)|灯火|夜"


def light_period(elements):
    t = str((elements or {}).get("时间与光线", "")) + str((elements or {}).get("地点", ""))
    if re.search(r"夜|晚|月", t):
        return "night"
    if re.search(r"白天|晨|午|日|黄昏|傍晚", t):
        return "day"
    return ""


def light_guard(script, elements, log=None):
    """要素表定了白天，幅里写夜/霓虹/月光 → 重写那一幅（台词保留）；反之亦然。重写不成就删掉那个分句。"""
    period = light_period(elements)
    if not period:
        return script
    bad_pat = _NIGHT_WORDS if period == "day" else _DAY_WORDS
    want = str((elements or {}).get("时间与光线", "")) or ("白天" if period == "day" else "夜晚")
    paras = [p for p in re.split(r"\n\s*\n", str(script or "").strip()) if p.strip()]
    n = 0
    for i, p in enumerate(paras):
        narr = "\n".join(l for l in p.split("\n") if not re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip()))
        if not re.search(bad_pat, narr):
            continue
        dlg = re.findall(r"^[^\n：:]{1,8}[：:].+$", p, re.M)
        sysm = ("你是这部短片的编剧。全片的时间与光线是「%s」，下面这一幅却写了和它冲突的光（%s）。只改光线和照明相关的词，"
                "让它符合「%s」；动作、人物、台词行原样保留；字数和原来差不多。只输出这一幅。"
                % (want, "、".join(sorted(set(re.findall(bad_pat, narr)))), want))
        new, note = au._rewrite_block(p, sysm, max(20, int(len(p) * 0.6)), int(len(p) * 1.4) + 30, temperature=0.3)
        if new and all(d in new for d in dlg) and not re.search(bad_pat, new):
            paras[i] = new
        else:
            # 兜底：删掉含冲突词的分句
            kept = []
            for l in p.split("\n"):
                if re.match(r"^[^\n：:]{1,8}[：:].+$", l.strip()):
                    kept.append(l)
                    continue
                segs = re.split(r"(?<=[，。；])", l)
                segs = [x for x in segs if x and not re.search(bad_pat, x)]
                txt = "".join(segs).strip("，； ")
                if txt and not txt.endswith("。"):
                    txt += "。"
                if txt:
                    kept.append(txt)
            paras[i] = "\n".join(kept) if kept else p
        n += 1
    if n and log is not None:
        log.append("光线统一为「%s」：改了 %d 幅" % (want, n))
    return "\n\n".join(paras)


# ───────────────── 读者简介（给用户看的） ─────────────────
_SYNOPSIS_SYS = """你是剧集编辑。下面是一话短片的画面剧本（〔幅N〕是镜头编号，「名字：台词」是说的话）。把它写成给**观众**看的【故事简介】——像给朋友讲这段戏：
· 400～650 字，分 3～5 个自然段，按剧本顺序讲：开头是谁在哪、正在做什么；接着每一步冲突谁对谁做了什么、说了什么（台词用引号原样引用）；最后停在哪个没做完的动作或没说完的话上。
· **只讲事，不讲拍法**：不写发色发髻、衣服、戒指、疤痕、瞳孔颜色、指节泛白、光线镜头这些画面细节；动作只说到"夺过酒盏""攥住袖口"这种程度。
· 只讲剧本里真实发生的事，不加新情节、不解读心理、不评价好坏、不用"男主""女主"这类代称；地点用【地点】里给的名字。
· 结尾单独一段，一句话写「这一话看点：……」，说清吸睛的是什么。
只输出简介正文，不要标题。"""


_LOOK_WORDS = r"发髻|马尾|长发|发丝|瞳孔|眼眸|戒指|疤痕|指节|泛白|银簪|衣襟|袖口|肤色|肌肤|日光|阳光|光影|镜头|特写|画面"


def write_reader_synopsis(script, elements, names=None, log=None):
    paras = [p for p in re.split(r"\n\s*\n", str(script or "").strip()) if p.strip()]
    numbered = "【地点】%s\n【时间】%s\n\n" % ((elements or {}).get("地点", ""), (elements or {}).get("时间与光线", "")) + \
               "\n\n".join("〔幅%d〕%s" % (i + 1, p) for i, p in enumerate(paras))
    dlg = [m.group(1).strip().strip("“”「」") for m in re.finditer(r"^[^\n：:]{1,8}[：:](.+)$", script or "", re.M)]
    norm = lambda q: re.sub(r"[^一-龥]", "", q)
    best, best_score = "", -999
    note = ""
    for attempt in range(3):
        try:
            rep = str(au._q(_SYNOPSIS_SYS + note, numbered, mt=1200, temperature=0.4 + 0.15 * attempt) or "").strip()
        except Exception:
            rep = ""
        if not rep:
            continue
        probs = []
        if not (350 <= len(rep) <= 1300):
            probs.append("字数 %d" % len(rep))
        miss = [n for n in (names or []) if n not in rep]
        if miss:
            probs.append("缺人名 " + "、".join(miss))
        hit = sum(1 for d in dlg if norm(d) and norm(d) in norm(rep))
        if dlg and hit * 2 < len(dlg):
            probs.append("台词只引了 %d/%d 句" % (hit, len(dlg)))
        if "看点" not in rep:
            probs.append("没写看点")
        looks = re.findall(_LOOK_WORDS, rep)
        if len(looks) >= 3:
            probs.append("写了拍摄细节 " + "、".join(sorted(set(looks))[:5]))
        score = -len(probs)
        if score > best_score:
            best, best_score = rep, score
        if not probs:
            break
        note = "\n\n★上一稿的问题：%s。请改正后重写。" % "；".join(probs)
        if log is not None:
            log.append("读者简介第 %d 稿：%s" % (attempt + 1, "；".join(probs)))
    return fix_synopsis_speakers(strip_look_clauses(best, names), script, names)


def fix_synopsis_speakers(syn, script, names):
    """简介里一句话的主语（句内最早出现的名字）和剧本台词行的说话人不一致 → 把说话归属拆成独立一句。"""
    if not names:
        return syn
    norm = lambda q: re.sub(r"[^一-龥]", "", q)
    who_of = {}
    for m in re.finditer(r"^([^\n：:]{1,8})[：:](.+)$", str(script or ""), re.M):
        if m.group(1).strip() in names:
            who_of[norm(m.group(2))] = m.group(1).strip()
    if not who_of:
        return syn
    def _fix(m):
        pre, attr, quote = m.group(1), m.group(2), m.group(3)
        key = norm(quote)
        want = who_of.get(key) or next((w for k, w in who_of.items() if len(k) >= 4 and (k in key or key in k)), "")
        if not want:
            return m.group(0)
        text = pre + attr
        pos = [(text.find(n), n) for n in names if n in text]
        subject = min(pos)[1] if pos else ""
        if subject == want:
            return m.group(0)
        lead = pre.rstrip().rstrip("，、；")
        if lead and lead[-1] not in "。！？":
            lead += "。"
        return lead + want + "说道：" + quote
    pat = r"(?:(?<=[。！？\n])|^)([^。！？“”\n]*?)([一-龥]{0,10}?(?:说道|喊道|问道|答道|低声道|冷冷道|开口道|开口|说|喊|问|道)[：:]?\s*)(“[^”]+”)"
    return re.sub(pat, _fix, str(syn or ""))


def strip_look_clauses(text, names=None):
    """简介里含外貌/拍摄细节的分句删掉（模型三稿都不听，代码兜底）。台词引号内不动。"""
    out_paras = []
    for para in str(text or "").split("\n"):
        if not para.strip():
            out_paras.append(para)
            continue
        # 先把引号里的台词保护起来
        quotes = re.findall(r'[“"][^”"]*[”"]', para)
        tmp = para
        for i, q in enumerate(quotes):
            tmp = tmp.replace(q, "\x00%d\x00" % i, 1)
        sents = re.split(r"(?<=[。！？])", tmp)
        kept_s = []
        for sent in sents:
            if not sent.strip():
                continue
            clauses = re.split(r"(?<=[，；])", sent)
            kept = [c for c in clauses if not re.search(_LOOK_WORDS, c) or "\x00" in c]
            if not kept:
                continue
            if clauses and kept and kept[0] is not clauses[0] and "\x00" not in "".join(kept):
                # 主语分句被删、剩下的没主语（"猛地后退。"）→ 整句删
                head = kept[0].strip()
                if names:
                    if not any(head.startswith(n) for n in names):
                        continue
                elif not re.match(r"^[\u4e00-\u9fa5A-Za-z·]{2,3}(?:说|喊|问|看|抬|伸|走|站|坐|转|把|将|从|向|在|用)", head):
                    continue
            txt = "".join(kept).strip()
            txt = re.sub(r"[，；]+([。！？])", r"\1", txt)
            if txt and txt[-1] not in "。！？”\"" and not txt.endswith("\x00"):
                txt = txt.rstrip("，；") + "。"
            kept_s.append(txt)
        res = "".join(kept_s)
        for i, q in enumerate(quotes):
            res = res.replace("\x00%d\x00" % i, q)
        out_paras.append(res)
    return "\n".join(out_paras)


# ───────────────── 派生正文（给下游用） ─────────────────
def derive_prose(script):
    """剧本 → 正文样式：「名：台词」变成「名说：“台词”」，其余段照抄。下游的覆盖/台词/说话人检查靠它。"""
    out = []
    for para in re.split(r"\n\s*\n", str(script or "").strip()):
        lines = []
        for l in para.split("\n"):
            m = re.match(r"^([^\n：:]{1,8})[：:](.+)$", l.strip())
            if m and not l.strip().startswith("镜头"):
                lines.append("%s说：“%s”" % (m.group(1).strip(), m.group(2).strip().strip("“”「」")))
            else:
                lines.append(l)
        out.append("\n".join(lines))
    return "\n\n".join(out)


# ───────────────── 总控 ─────────────────

# ═══════════ 框架层：一句话 → N 话分话框架（P96） ═══════════
_FW_PACES = ("日常", "推进", "高潮")
_FW_GEOS = ("行进", "对谈", "对峙", "追逐")
_ROLE_WORDS = re.compile(r"摊主|邻人|人牙子|二师兄|三师兄|师兄|师姐|师妹|长老|弟子|守卫|店家|掌柜|官兵|山贼|土匪|路人|村民|老板|司机|保安|警察|追兵|仇家|管家|侍卫|门房|护卫|镖师|杀手|机甲|怪物|巨熊|巨兽|魔物|敌人|富二代|老板娘|房东|债主|打手|丈夫|妻子|女儿|儿子|父亲|母亲|娘|爹|孩子|女孩|少年|少女|修女|武斗家|法师|勇者|骑士|外卖员|店员|队长|新妻|人们|众人|大家")


def _fw_parse(rep):
    rows = []
    for line in str(rep or "").splitlines():
        t = line.strip().strip("|｜ ")
        if not t or ("｜" not in t and "|" not in t):
            continue
        if re.match(r"^[-—:： ]+$", t.replace("|", "").replace("｜", "")):
            continue                                   # markdown 分隔行
        parts = [x.strip() for x in re.split(r"[｜|]", t)]
        parts[0] = re.sub(r"^第|话$|集$", "", parts[0]).strip()
        if len(parts) < 3 or not re.match(r"^\d+", parts[0]):
            continue
        parts += [""] * (10 - len(parts))
        # 十栏（P129）：话号｜讲什么｜节奏｜在场｜地点｜阻力｜几何｜落点｜变化点｜备注。
        # 老八/九栏没有地点栏——按几何词落在第 6 栏还是第 7 栏认；都认不出就按非空栏数
        _new = (parts[6] in _FW_GEOS) or (parts[5] not in _FW_GEOS and len([x for x in parts if x]) >= 9)
        if _new:
            place, parts = parts[4], parts[:4] + parts[5:] + [""]
        else:
            place = ""
        # 八栏格式：第 8 栏是变化点、第 9 栏备注；老七栏格式第 8 栏就是备注（"补"/"拆自…"）
        _p7, _p8 = parts[7], parts[8]
        if re.fullmatch(r"\s*(补|拆自[^｜|]*)?\s*", _p7 or "") and not _p8:
            change, note = "", _p7
        else:
            change, note = _p7, _p8
        rows.append({"no": int(re.sub(r"\D", "", parts[0]) or len(rows) + 1), "one_line": parts[1], "pace": parts[2] if parts[2] in _FW_PACES else "",
                     "cast": parts[3], "place": place.strip(), "resistance": parts[4], "geometry": parts[5] if parts[5] in _FW_GEOS else "", "landing": parts[6],
                     "change": change.strip(), "note": note})
    return rows


_FW_TONES = ("日常喜剧", "爱情暧昧", "悬疑", "动作冒险", "恐怖", "争斗复仇", "治愈成长")


def _norm_tone(t):
    t = str(t or "")
    for k, v in (("喜剧", "日常喜剧"), ("日常", "日常喜剧"), ("暧昧", "爱情暧昧"), ("爱情", "爱情暧昧"), ("悬疑", "悬疑"),
                 ("动作", "动作冒险"), ("冒险", "动作冒险"), ("恐怖", "恐怖"), ("复仇", "争斗复仇"), ("争斗", "争斗复仇"),
                 ("治愈", "治愈成长"), ("成长", "治愈成长")):
        if k in t:
            return v
    return ""


def _fw_people(rep):
    """框架输出里的人物行「人物｜名字｜身份｜与主角的关系」（P138）。"""
    out = []
    for line in str(rep or "").splitlines():
        t = line.strip().strip("|｜ ")
        if not t.startswith("人物"):
            continue
        parts = [x.strip() for x in re.split(r"[｜|]", t)]
        if len(parts) < 3 or parts[0] != "人物" or not parts[1]:
            continue
        out.append({"name": parts[1], "role": parts[2] if len(parts) > 2 else "", "relation": parts[3] if len(parts) > 3 else ""})
    return out


def _fw_tone(rep):
    """框架输出第一行「基调｜X」（P129）。"""
    m = re.search(r"基调\s*[｜|：:]\s*([一-龥]{2,6})", str(rep or ""))
    return _norm_tone(m.group(1)) if m else ""


def detect_tone(one_line):
    """一句话里没让模型说基调时的代码兜底：只按明确的词判，判不出留空。"""
    t = str(one_line or "")
    if re.search(r"追杀|复仇|报仇|仇家|杀了|血战|决战|屠", t):
        return "争斗复仇"
    if re.search(r"鬼|恶魔|诡异|阴森|恐怖|尸", t):
        return "恐怖"
    if re.search(r"悬疑|谜|失踪|真相|线索|凶手|密室", t):
        return "悬疑"
    if re.search(r"冒险|地下城|怪物|探险|秘境|寻宝|遗迹", t):
        return "动作冒险"
    if re.search(r"调戏|暧昧|美丽|火辣|心动|喜欢|表白|约会|乳沟|臀", t):
        return "爱情暧昧"
    if re.search(r"情同父女|情同母子|父女|母女|亲情|收养|相依|照顾|成长|治愈|带回|认出", t):
        return "治愈成长"
    if re.search(r"喝酒|聊天|吃饭|赶路|日常|相处|逛|做饭", t):
        return "日常喜剧"
    return ""


_PLACE_RE = re.compile(r"([一-龥]{0,3}(?:酒馆|客栈|茶馆|饭馆|酒楼|山道|山路|后山|山顶|山脚|镇上|镇子|村口|村里|城门|城里|院子|院门|大殿|大厅|山洞|洞口|林间|树林|河边|桥上|街上|街头|集市|码头|营地|宫殿|寺庙|道观|教堂|教室|学校|公司|办公室|地铁|公路|沙漠|海边|船上|车上|屋里|房间|厨房|书房|卧室|坟前|药铺|铁匠铺|广场|门口))")


def _fw_row_text(r):
    """框架一行的十栏文本（续写/修复表用）。"""
    return "%d｜%s｜%s｜%s｜%s｜%s｜%s｜%s｜%s｜%s" % (r["no"], r["one_line"], r["pace"], r["cast"], r.get("place", ""), r["resistance"], r["geometry"], r["landing"], r.get("change", ""), r["note"])


def _fw_names(text):
    """一句话里有名字的人：「名字（身份）」或 身份词+名字（大师兄李长渊 / 丈夫赵铁柱）。"""
    t = str(text or "")
    out = set(re.findall(r"([一-龥]{2,3})（", t))
    out |= set(re.findall(r"(?:大师兄|二师兄|师兄|师姐|师妹|长老|弟子|勇者|修女|武斗家|法师|骑士|丈夫|妻子|女儿|儿子|少年|少女|外卖员|店员|司机|保安|警察|队长|镖师|少女|叫)([一-龥]{2,3})", t))
    return {n for n in out if not _ROLE_WORDS.fullmatch(n)}


def _fw_names_strict(text):
    """一句话里的人名（P129c）：_fw_names 的结果 + 「名字＋动词」抓到的，去掉含虚词/动词字的碎片（「到酒馆」）。"""
    t = str(text or "")
    out = set(_fw_names(t))
    # 逐个身份词扫（P133b）：一次性交替匹配时「长老」会先吃掉「派大师」，后面紧跟的「大师兄李长渊」就漏了
    for _role in ("大师兄", "二师兄", "师兄", "师姐", "师妹", "师弟", "长老", "弟子", "勇者", "修女", "武斗家", "法师", "骑士", "丈夫", "妻子",
                  "女儿", "儿子", "少年", "少女", "外卖员", "店员", "司机", "保安", "警察", "队长", "镖师", "叫", "名叫", "名唤"):
        for _m in re.finditer(re.escape(_role) + r"([一-龥]{2,3})", t):
            out.add(_m.group(1))
    out |= set(re.findall(r"([一-龥]{2,3})(?=调戏|说道|说|问|看到|看见|走进|走到|来到|坐在|拿起|拔出|发现|决定|带着|护着|背着|跟着|抱着|牵着|盯着)", t))
    bad = re.compile(r"[的了着在到和与是有不无一个这那他她们把被让给从向对就还也都很又去来时间派命令叫使十百千年月日前后天最小私自]")
    return {n for n in out if not bad.search(n) and not _ROLE_WORDS.fullmatch(n) and len(n) >= 2
            and not re.search(r"(?:大师|师|兄|姐|妹|老|长)$", n)}


# 模型爱自造的称呼 → 只在原句里有对应叫法时才换（P169③）
_ALIAS = {
    "女儿": ("弟子", "丫头", "小丫头", "女娃", "娃娃", "小徒弟", "徒儿"),
    "女孩": ("弟子", "丫头", "小丫头", "女娃", "娃娃", "小徒弟", "徒儿"),
    "小师妹": ("师妹儿", "小师姐"),
    "丈夫": ("夫君", "男人"),
}


def _fix_person_words(rows, one, people=None):
    """一句话里没给名字的人，框架里必须照原句的叫法写。

    实测：小师妹的女儿被模型叫成「弟子」，在场栏和这一话的描述全跟着错，
    下游按名字建人物卡时又会多出一张「弟子」。这里按原句的叫法换回去。
    """
    names = {str(p.get("name") or "").strip() for p in (people or [])}
    for canon, alts in _ALIAS.items():
        if canon not in str(one or ""):
            continue
        if any(canon in n for n in names if n):
            pass
        for r in rows:
            for k in ("one_line", "cast", "landing", "change", "resistance"):
                v = str(r.get(k) or "")
                for a in alts:
                    if not a or a not in v or a in str(one or ""):
                        continue
                    # 「守门弟子」「大弟子」「门下弟子」是别人，不是这个孩子——只换独立出现的
                    v = re.sub(r"(?<!守门)(?<!看门)(?<!门下)(?<![大小内外众老])" + re.escape(a),
                               canon, v)
                r[k] = v
    return rows


def _rewrite_dup_rows(rows, slots, one, sysm, user, log=None):
    """哪一话和前面某话演的是同一件事，就只重写那一话（P171②）。

    模型偶发把某一行原样复制（实测第 4 话和第 6 话逐字相同）。整表重写换来的
    未必更好，而且会把已经写对的几话也搅了——只补这一格。
    """
    from .story_shape import _bigrams
    if not rows or not slots:
        return rows

    def _txt(r):
        return " ".join(str((r or {}).get(k) or "") for k in ("one_line", "landing", "change"))

    for i in range(1, min(len(rows), len(slots))):
        bi = _bigrams(_txt(rows[i]))
        dup = next((j for j in range(i) if bi and
                    len(bi & _bigrams(_txt(rows[j]))) >= max(6, int(len(bi) * 0.6))), None)
        if dup is None:
            continue
        sl = slots[i]
        ask = ("%s\n\n【只写第 %d 话这一行，别的话不要写】\n"
               "这一话要落的是：%s\n"
               "第 %d 话已经演过「%s」，这一话**不许再演同一件事**。\n"
               "只输出一行，格式和上面一样，话号写 %d。"
               % (user, i + 1, sl.get("done") or "", dup + 1,
                  str(rows[dup].get("one_line") or "")[:40], i + 1))
        try:
            rep = au._q(sysm, ask, mt=400, temperature=0.5)
            new_rows = _fw_parse(rep)
        except Exception:
            new_rows = []
        if new_rows:
            nr = dict(new_rows[0])
            nr["no"] = i + 1
            if len(_bigrams(_txt(nr)) & _bigrams(_txt(rows[dup]))) < max(6, int(len(bi) * 0.6)):
                rows[i] = nr
                if isinstance(log, list):
                    log.append("第 %d 话和第 %d 话重复，定点重写了第 %d 话" % (i + 1, dup + 1, i + 1))
    return rows


def _drop_dup_checks(probs, sh):
    """有话位表时，剧情点覆盖和顺序以话位检查为准，老校验器那两类不再报（P170）。

    两个校验器切词方式不同，同一个框架一个说覆盖了、一个说没覆盖。
    同一件事只能有一个口径，否则修复轮会去修一个本来正确的框架。
    """
    if not (sh or {}).get("slots"):
        return list(probs or [])
    return [p for p in (probs or [])
            if not re.match(r"缺剧情点|顺序反了", str(p or ""))]


# 把关系定下来的那件事：有人来抢/来要而他挡住，或者她自己做了选择、交出东西
_DECISIVE = re.compile(r"抢|要人|要回|来领|来接|夺|拦|挡|护住|挡在|站到|牵住|牵起|抱住|"
                       r"选择|选了|决定|答应|点头|留下|跟他走|交给|递给|拿出|摘下|放下|"
                       r"当众|说死|担保|承诺|发誓")


def _slot_problems(rows, slots, limits=()):
    """按话位表逐格查：第 i 话有没有落到第 i 格该落的东西（P168）。

    话位是代码排的，模型只负责填。它常见的偏法是把过渡铺满、把结局挤掉——
    这里逐格对，缺哪一格说哪一格。
    """
    from .story_shape import _bigrams

    def _row_txt(k):
        if not (0 <= k < len(rows)):
            return ""
        return " ".join(str(rows[k].get(x) or "") for x in
                        ("one_line", "landing", "change", "resistance"))

    out = []
    # 同一条剧情点占了几格（拆开演）→ 这几格合起来算覆盖，别逐格误报（P169④）
    by_anchor = {}
    for i, sl in enumerate(slots):
        if sl.get("anchor"):
            by_anchor.setdefault(sl["anchor"], []).append(i)
    for anchor, idxs in by_anchor.items():
        span = set(idxs) | {min(idxs) - 1, max(idxs) + 1}      # 允许落在相邻一话
        txt = " ".join(_row_txt(k) for k in sorted(span))
        a, b = _bigrams(anchor), _bigrams(txt)
        if a and len(a & b) < max(2, len(a) // 4):
            sl = slots[idxs[0]]
            out.append("第 %d 话没落到「%s」——这一格要的是：%s"
                       % (idxs[0] + 1, anchor[:18], (sl.get("done") or "")[:40]))
    for i, sl in enumerate(slots):
        if i >= len(rows):
            out.append("第 %d 话没落到「%s」——这一话被漏掉了" % (i + 1, (sl.get("done") or "")[:24]))
            continue
        txt = _row_txt(i)
        # ① 没有剧情点的格子（日常、过程）不许把前面演过的事再演一遍
        if not sl.get("anchor"):
            for k in range(i):
                pa, pb = _bigrams(_row_txt(k)), _bigrams(txt)
                if pa and pb and len(pa & pb) >= max(6, int(len(pb) * 0.6)):
                    out.append("第 %d 话和第 %d 话演的是同一件事——这一格要演前面没演过的"
                               % (i + 1, k + 1))
                    break
        if sl.get("limit"):
            lw = _bigrams(sl["limit"])
            if lw and len(lw & _bigrams(txt)) < 2:
                out.append("第 %d 话没让「%s」挡住他" % (i + 1, sl["limit"][:16]))
        # 结局那两格被写成日常＝结局没兑现（P168c 实测：第6～8话全是买新衣、扫落叶）
        if sl.get("role") in ("兑现", "落地") and str(rows[i].get("pace") or "") == "日常":
            out.append("第 %d 话是「%s」那一格，不能写成日常——结局要用一件具体的事定下来"
                       % (i + 1, sl["role"]))
        # 兑现格查的是"有没有一件决定性的事"，不是有没有写出结局那四个字（P172）
        if sl.get("role") == "兑现" and not _DECISIVE.search(txt):
            out.append("第 %d 话没有把关系定下来的那件事——要有人来抢/来要而他挡住，"
                       "或者她自己做了一个选择、交出一样东西" % (i + 1))
        # 落地格查的是"挡路的规矩有没有下文"
        if sl.get("role") == "落地" and limits:
            lb = set()
            for _lm in limits:
                lb |= _bigrams(_lm)
            if lb and len(lb & _bigrams(txt)) < 2:
                out.append("第 %d 话没给「%s」一个下文——挡了一路的规矩要在这里被回应"
                           % (i + 1, str(limits[0])[:14]))
    return out


def framework_problems(rows, one_line, points=None, n_want=None):
    """代码核对框架。返回问题列表（给修复指令词逐条用）。"""
    probs = []
    one = str(one_line or "")
    if not rows:
        return ["没有解析出任何一行"]
    if n_want and abs(len(rows) - int(n_want)) > 2:
        probs.append("要 %d 话，只铺了 %d 话" % (int(n_want), len(rows)))
    # ① 用户剧情点全在、顺序一致
    pts = list(points or [])
    last_idx = -1

    def _covers(pt, txt):
        e = re.sub(r"[^一-龥]", "", str(pt or ""))
        grams = [e[i:i + 2] for i in range(len(e) - 1)]
        grams = [g for g in grams if g not in _EV_STOP and not re.search(r"[的了着过在是和与把被让给到从]", g) and str(one).count(g) < 3]
        need = 1 if len(grams) <= 3 else 2
        return sum(1 for g in grams if g in txt) >= need if grams else True

    _cast_txt = "".join(str(r.get("cast") or "") for r in rows)
    _cast_grams = {_cast_txt[i:i + 2] for i in range(len(_cast_txt) - 1)}        # 在场栏里的名字块不算依据（P117）

    def _home_of(pt):
        # 归家：近义归一后，第一个覆盖到的话；一块都没命中 → None（P108/P108b/P109）
        e = re.sub(r"[^一-龥]", "", _syn_norm(str(pt or "")))
        grams = [e[i:i + 2] for i in range(len(e) - 1)]
        grams = [g for g in grams if g not in _EV_STOP and not re.search(r"[的了着过在是和与把被让给到从]", g) and str(one).count(g) < 3 and g not in _cast_grams]
        need = 1 if len(grams) <= 4 else 2
        for i, r in enumerate(rows):                       # 第一个覆盖到的话（P109）
            txt = _syn_norm(r["one_line"])
            if sum(1 for g in grams if g in txt) >= need:
                return i
        return None
    for pt in pts:
        idx = _home_of(pt)
        if idx is None:
            probs.append("缺剧情点「%s」" % pt)
            continue
        if idx < last_idx - 1:                             # 差两话以上才算反（相邻重叠正常，P109）
            probs.append("顺序反了：「%s」应在第 %d 话之后" % (pt, last_idx + 1))
        last_idx = max(last_idx, idx)
    # ② 人物只来自一句话：只查「在场的人」那一栏（P97）；身份词（卖艺人/混混/大夫）只提醒（P98）
    soft = []
    for r in rows:
        for tok in re.split(r"[、，,／/和与及]", str(r["cast"] or "")):
            nm = re.sub(r"（[^）]*）", "", tok).strip()
            if not nm or nm in ("无", "没有") or _ROLE_WORDS.search(nm) or nm in one or len(nm) > 4:
                continue
            if re.search(r"[人夫子主客民妇汉娘婆翁兵贩匪徒工商侍卫官吏丁甲乙丙痞霸僧道姑爷机群队阵雾影兽魔怪灵物猫狗鸟马牛羊鱼们]$|师|老|大夫|混混|乞丐|小二|伙计|头目|首领|流氓|地痞|恶霸|无人机|机甲|怪物", nm):
                soft.append("第 %d 话有一句话里没有的身份「%s」" % (r["no"], nm))
                continue
            probs.append("第 %d 话造了人物「%s」" % (r["no"], nm))
    # ②a2 在场栏里的人必须在一句话或人物表里（P151：恐怖片框架编了「邻人」，建卡后被当成真人写进正文）
    _tbl = {str(p.get("name") or "").strip() for p in (getattr(framework_problems, "people", None) or [])}
    for r in rows:
        for tok in re.split(r"[、，,／/和与及]", str(r.get("cast") or "")):
            nm = re.sub(r"（[^）]*）", "", tok).strip()
            if not nm or nm in ("无", "没有") or len(nm) < 2 or nm in one or nm in _tbl:
                continue
            if any(nm in x or x in nm for x in _tbl):
                continue
            probs.append("第 %d 话在场栏有一句话和人物表里都没有的「%s」" % (r["no"], nm))
    # ②b 任意两话重复（P100/P102）：二字块重合过半
    _grams = []
    for r in rows:
        a = re.sub(r"[^一-龥]", "", r["one_line"])
        _grams.append({a[k:k + 2] for k in range(len(a) - 1)})
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            ga, gb = _grams[i], _grams[j]
            if ga and gb and len(ga & gb) * 2 >= min(len(ga), len(gb)):
                probs.append("第 %d 话和第 %d 话内容重复" % (rows[i]["no"], rows[j]["no"]))
    # ②c 容量（P105）：正好一个变化点。太满 = 落进 ≥3 条剧情点 或 变化点栏写了两件；太薄 = 没有变化点
    _CHG = re.compile(r"到了|抵达|到达|进了|知道|得知|发现|认出|听到|拿到|得到|失去|丢了|答应|同意|拒绝|不肯|被救|获救|被抓|被带走|带走|买下|收下|接住|救下|牵|抱住|哭|决定|出发|离开|回到|松手|放开|信任|喊.{0,3}师父|另娶|病死|死了|受伤|中毒|醒来|睡着|收养|留下|赶走|逐出|输了|赢了|变了|成了|第一次")
    # 每条剧情点只归"家"（命中二字块最多的那一话，并列取靠前）——P106：按覆盖数会每话都算 4 条
    def _hits(pt, txt):
        e = re.sub(r"[^一-龥]", "", str(pt or ""))
        grams = [e[i:i + 2] for i in range(len(e) - 1)]
        grams = [g for g in grams if g not in _EV_STOP and not re.search(r"[的了着过在是和与把被让给到从]", g) and str(one).count(g) < 3]
        return sum(1 for g in grams if g in txt)
    home = {}
    for pt in pts:
        # 容量归家：命中最多的话（并列取靠前），且命中数 ≥2（P109b）
        e = re.sub(r"[^一-龥]", "", _syn_norm(str(pt or "")))
        grams = [e[i:i + 2] for i in range(len(e) - 1)]
        grams = [g for g in grams if g not in _EV_STOP and not re.search(r"[的了着过在是和与把被让给到从]", g) and str(one).count(g) < 3]
        best, bn = None, 0
        for i, r in enumerate(rows):
            n = sum(1 for g in grams if g in _syn_norm(r["one_line"]))
            if n > bn:
                best, bn = i, n
        if best is not None and bn >= 2:
            home[best] = home.get(best, 0) + 1
    for i, r in enumerate(rows):
        chg = str(r.get("change") or "")
        n_pts = home.get(i, 0)
        two = len([c for c in re.split(r"[，；、,;]|并且|同时|还", chg) if c.strip()]) >= 2 and len(chg) > 14
        if n_pts >= 3 and (not n_want or int(n_want) > 2):     # 一两话的短故事本来就装下所有剧情点（P119）
            probs.append("第 %d 话太满：落进了 %d 条剧情点" % (r["no"], n_pts))
        elif two:
            soft.append("第 %d 话变化点像是两件「%s」" % (r["no"], chg[:24]))       # 一事两说很常见，只提醒（P108）
        elif not chg.strip() and not _CHG.search(r["one_line"]):
            probs.append("第 %d 话太薄：结束时什么都没变（%s）" % (r["no"], r["one_line"][:20]))
        elif re.fullmatch(r"[^一-龥]*(?:领命|受命|出发|决定|转身|离开|走出|到达|抵达|启程|下山|上路|动身)[^一-龥]{0,2}(?:下山|出发|离开|上路)?[^一-龥]*", chg.strip()) and n_pts <= 1:
            probs.append("第 %d 话太薄：变化点只是过渡「%s」，撑不起两分钟（并入相邻话或补过程结果）" % (r["no"], chg.strip()[:12]))
    # ②d 软基调（P129k）：一句话里没有的身体接触动词是模型加的（「轻捏腰侧」→ 整话捏腰拽臂）
    _tone_fw = getattr(framework_problems, "tone", "")
    if _tone_fw in ("日常喜剧", "爱情暧昧", "治愈成长"):
        for r in rows:
            for m in _CONTACT.finditer(r["one_line"] + "。" + str(r.get("landing") or "") + "。" + str(r.get("change") or "")):
                if m.group(0)[:1] not in one:
                    probs.append("第 %d 话加了一句话里没有的身体接触「%s」" % (r["no"], m.group(0)[:4]))
                    break
    # ②f 一句话里没有的硬事实（P135：「小师妹三年前病死」——一句话只说"一直没回宗门"，生死婚嫁这类事实不许编）
    _FACT = re.compile(r"病死|死了|去世|身亡|亡故|丧命|被杀|战死|失踪|另娶|改嫁|再嫁|续弦|私奔|成亲|成婚|定亲|怀孕|生子|中毒|重伤|残废|瞎了|失忆")
    for r in rows:
        for m in _FACT.finditer(r["one_line"] + "。" + str(r.get("change") or "")):
            w = m.group(0)
            if w not in one and not (w in ("另娶", "改嫁", "再嫁", "续弦") and re.search(r"另娶|改嫁|再嫁|续弦|娶了", one)):
                probs.append("第 %d 话编了一句话里没有的事实「%s」" % (r["no"], w))
                break
    # ②e 一句话里没有的地点结构（P129t：「走下楼梯」「二楼栏杆」把酒馆变成两层，场景卡跟着裂）
    _PSTRUCT = re.compile(r"楼梯|二楼|三楼|楼上|楼下|阁楼|后院|后厨|地窖|阳台|走廊|包间|雅间|雅座|柜台后|吧台")
    for r in rows:
        for m in _PSTRUCT.finditer(r["one_line"] + "。" + str(r.get("landing") or "") + "。" + str(r.get("place") or "")):
            if m.group(0) not in one:
                probs.append("第 %d 话加了一句话里没有的地点结构「%s」" % (r["no"], m.group(0)))
                break
    # ③ 一话一件事：分句 ≤ 4，字数 ≤ 80
    for r in rows:
        cl = [c for c in re.split(r"[，；。]", r["one_line"]) if c.strip()]
        if len(cl) > 5 and len(r["one_line"]) > 70:
            probs.append("第 %d 话装了不止一件事（%d 个分句 %d 字）" % (r["no"], len(cl), len(r["one_line"])))
    # ④ 节奏：没填才算问题；三种都有/不连排只是提醒（P97：用户明说"第一话只旅行"这种慢开场是合理的）
    paces = [r["pace"] for r in rows]
    warns = []
    if len(rows) >= 6:
        for p in _FW_PACES:
            if p not in paces:
                warns.append("整部没有「%s」档的话" % p)
    for i in range(2, len(paces)):
        if paces[i] and paces[i] == paces[i - 1] == paces[i - 2]:
            warns.append("第 %d～%d 话节奏连排「%s」" % (i - 1, i + 1, paces[i]))
            break
    for r in rows:
        if not str(r.get("place") or "").strip():
            soft.append("第 %d 话地点没填" % r["no"])
    framework_problems.warnings = warns + soft
    for r in rows:
        if not r["pace"]:
            probs.append("第 %d 话节奏没填（只能 日常/推进/高潮）" % r["no"])
    # ⑤ 阻力
    for r in rows:
        if r["pace"] in ("推进", "高潮") and (not r["resistance"] or r["resistance"] in ("无", "没有")):
            probs.append("第 %d 话是%s却没阻力" % (r["no"], r["pace"]))
    # ⑥ 落点是画面
    for r in rows:
        if not r["landing"] or re.search(r"情感|关系|升温|信任|心里|心中|明白|意识到|决定$", r["landing"]):
            probs.append("第 %d 话落点不是画面：%s" % (r["no"], (r["landing"] or "空")[:20]))
    return probs


def plan_framework(one_line, settings=None, n_eps=12, notes="", log=None):
    """Prose-first planning; the previous pipeline is an explicit rollback option."""
    from . import universal_writer
    if universal_writer.enabled():
        return universal_writer.plan(one_line, settings, n_eps, notes, log, model=au._q)
    return _legacy_plan_framework(one_line, settings, n_eps, notes, log)


def _legacy_plan_framework(one_line, settings=None, n_eps=12, notes="", log=None):
    """一句话 → N 话框架。1 次铺 + ≤1 次修。返回 {rows, problems, points, calls}。"""
    _log = log if log is not None else []
    one = str(one_line or "").strip()
    sysm = au._fill(au._ins("故事_分话框架_指令词.txt"), settings or {}, None)
    user = "【一句话故事（含用户已想好的剧情点）】\n%s\n\n【要铺几话】%d 话" % (one, int(n_eps or 0))
    if notes:
        user += "\n\n【用户的要求】" + str(notes)
    # 用户剧情点：模型抽（有依据词核对），抽不到用离线拆；按顺序列给模型（P97）
    try:
        points = extract_events(one) or []
    except Exception:
        points = []
    if len(points) < 2:
        points = extract_events_offline(one)
    # P167：整部故事的剧情点和话数改由 story_shape 出。
    # 老路用的是**单话**事件抽取器（最多 6 条、"后面/之后"的不要），对整部故事恰好相反——
    # 天云门实测：抽出 8 条停在「想带她回宗门」，结局「后来两人情同父女」和
    # 用户要求「日常相处放在很靠后」全丢了。
    from . import story_shape as _shape
    _sh = _shape.plan_slots(one)
    if _sh["must"]:
        points = [x["text"] for x in _sh["must"]] + list(_sh.get("limits") or [])
    if _pace.no_strike(one):
        points = no_strike_events(points)                 # 「一根手指都不能动」→「忍住没出手」（P102：指令词里写含义没用，喂之前先改）
    # 话数由剧情点数决定（P114：73 字的一句话被硬铺成 12 话）：没指定 → K+1，夹在 2～12；指定了但铺不满 → 提醒
    _k = len(merge_points(points))                       # 同一刻的几条"看"合成一条再数（P115）
    # P116：短的就一两话，长的才多——K ≤ 6 → K 话；K ≥ 7 → K+1；写了"后面/后续还有"再加 2 话；1～12
    _more = 2 if re.search(r"后面|后续|以后|之后还有|往后|后来", one) else 0
    # P167：话数按**内容的作用**定，不按一句话有多少字。
    # 老规则是 <40 字→1 话、>120 字→字数/15——同一个故事多写两句人物介绍就多分几话，
    # 短故事被铺成 12 话、长故事被压成 6 话，都是这么来的。
    _auto = int(_sh["episodes"]) if _sh.get("must") else max(1, min(12, (_k if _k <= 6 else _k + 1) + _more))
    _note_n = ""
    if not n_eps or int(n_eps) <= 0:
        n_eps = _auto
        _note_n = _sh.get("why") or ("话数自动定为 %d 话" % n_eps)
    elif int(n_eps) > 2 * _k + 2:
        _note_n = "你要 %d 话，但一句话里只有 %d 条剧情点，铺不满会靡水；建议 %d 话" % (int(n_eps), _k, _auto)
    user = user.replace("【要铺几话】%d 话" % int(n_eps or 0), "【要铺几话】%d 话" % int(n_eps)) if "【要铺几话】" in user else user
    user = re.sub(r"【要铺几话】\d+ 话", "【要铺几话】%d 话" % int(n_eps), user)
    if points:
        user += "\n\n【必须落进去的剧情点，按这个顺序，原词照用】\n" + "\n".join("%d. %s" % (i + 1, p) for i, p in enumerate(points))
    # P167：把这句话读出来的东西全部交底——背景、限制、性格、创作要求、要补的连接。
    # 这些以前要么被当成剧情点占了一话，要么直接丢了。
    if _sh.get("facts"):
        user += ("\n\n【背景（已经发生完了，交代一句就行，不要单独占一话）】\n"
                 + "\n".join("· " + x for x in _sh["facts"]))
    if _sh.get("limits"):
        user += ("\n\n【限制（挡主角的规矩和条件，要一直起作用，但不单独占一话）】\n"
                 + "\n".join("· " + x for x in _sh["limits"]))
    if _sh.get("traits"):
        user += ("\n\n【人物性格（是属性，不是事件，别拆成两话）】\n"
                 + "\n".join("· " + x for x in _sh["traits"]))
    if _sh.get("asks"):
        user += ("\n\n【用户对整篇的要求（必须照办）】\n"
                 + "\n".join("· " + x for x in _sh["asks"]))
    if _sh.get("gaps"):
        user += ("\n\n【一句话没交代、你必须在框架里补上的连接】\n"
                 + "\n".join("· " + x for x in _sh["gaps"])
                 + "\n补的时候守四条：①不新增有名字的人物；②不推翻上面任何已定事实；"
                   "③补出来的设定要让每一条剧情点都变成非发生不可；④和基调一致。")
    if _sh.get("slots"):
        _ss = _sh["slots"]
        user += ("\n\n【每一话必须落的东西——话号是定死的，不许调换、不许合并、不许多写一话】\n"
                 + "\n".join(
                     "第%d话（%s）：%s%s" % (
                         i + 1, sl["role"], sl["done"],
                         ("；这一话里「%s」要真的挡住他一次" % sl["limit"]) if sl.get("limit") else "")
                     for i, sl in enumerate(_ss))
                 + "\n每一格只写这一格的事。前面的过渡（领命、赶路、进门）压进第1话开头，不许单独占话。")
    if False:
        _skel = [w for w in _sh["weights"] if w["weight"]]
        user += ("\n\n【篇幅怎么分（话数就是这么算出来的，照着分）】\n"
                 + "\n".join("· %s → %d 话（%s）" % (w["text"][:26], w["weight"], w["role"]) for w in _skel)
                 + ("\n· 关系怎么变来的、拒绝在怕什么，要用戏演出来，另外占 %d 话"
                    % len(_sh.get("process_eps") or []) if _sh.get("process_eps") else "")
                 + ("\n· 用户点名要的日常相处，单独一话，放在最后" if _sh.get("extra_daily") else ""))
    rep = au._q(sysm, user, mt=2600, temperature=0.3)
    rows = _fw_parse(rep)
    calls = 1
    _people = _fw_people(rep)                              # 人物表（P138）
    # 一句话里没名字但被提到的人（小师妹/丈夫/女儿/老板娘…）也要进表（P149：不然模型把别人的名字安给她）
    _have_p = {str(p.get("name") or "").strip() for p in _people}
    _desc_all = "".join(str(p.get("role") or "") + str(p.get("relation") or "") for p in _people)
    for _m in re.finditer(r"(小?师妹|小?师兄|小?师姐|小?师弟|丈夫|妻子|新妻|女儿|儿子|老板娘|老板|女服务生|服务生|掌柜|长老|师父|母亲|父亲|爹|娘)", one):
        _nm = _m.group(1)
        # 已经在表里、或已经被别人的身份/关系写到（「赵铁柱——小师妹的丈夫」）→ 不补，免得多出「丈夫」「师兄」这种噪音行
        if _nm in _have_p or any(_nm in x or x in _nm for x in _have_p) or _nm in _desc_all:
            continue
        _have_p.add(_nm)
        _people.append({"name": _nm, "role": "（一句话里没给名字）", "relation": "", "unnamed": True})
    _tone = _fw_tone(rep) or detect_tone(one)               # 基调（P129）：模型第一行说，没说按词判
    # P133：一句话里是查探/辜负/认亲这类事、没有喝酒聊天调戏这类休闲词，模型却因为「日常相处」几个字选了日常喜剧 → 治愈成长
    if _tone == "日常喜剧" and not re.search(r"喝酒|聊天|吃饭|逛|调戏|暧昧|做饭|玩|逗", one) \
            and re.search(r"查|辜负|另娶|不能动手|认出|带回|情同父女|收养|失踪|下落|复仇|营救", one):
        _log.append("基调 日常喜剧→治愈成长（一句话里没有休闲词，有查探/辜负/认亲）")
        _tone = "治愈成长"
    framework_problems.tone = _tone
    framework_problems.people = _people
    _n = int(n_eps or 12)
    # 行数不够 → 续写（P107：模型常一行就停，重问整表也会再停）；最多续两次
    for _c in range(2):
        if len(rows) >= _n:                                # 严格：少一行也续（P129c：N=2 时一行曾被放过）
            break
        _log.append("框架解析出 %d 行（要 %d），从第 %d 话续写" % (len(rows), _n, len(rows) + 1))
        _have = "\n".join(_fw_row_text(r) for r in rows)
        rep = au._q(sysm, user + "\n\n【已经写好的前 %d 话】\n%s\n\n从第 %d 话**接着**往下写到第 %d 话，只输出新的这几行，格式同上，话号连着编。" % (len(rows), _have, len(rows) + 1, _n),
                    mt=2600, temperature=0.4)
        more = [r for r in _fw_parse(rep) if r["no"] > len(rows)]
        calls += 1
        if not more:
            break
        rows = rows + more
        for i, r in enumerate(rows):
            r["no"] = i + 1
    if len(rows) > _n + 3:
        rows = rows[:_n]
        for i, r in enumerate(rows):
            r["no"] = i + 1
    probs = _drop_dup_checks(framework_problems(rows, one, points, n_eps), _sh)
    probs += _slot_problems(rows, _sh.get("slots") or [], _sh.get("limits") or [])   # P168：按话位表逐格查
    _log.append("框架第 1 稿：%d 话，问题 %d：%s" % (len(rows), len(probs), "；".join(probs)[:300]))
    _hard = lambda ps: [p for p in ps if re.match(r"缺剧情点|顺序反了|.*造了人物|.*装了不止一件事|要 \d+ 话|.*内容重复|.*太满|.*太薄|.*身体接触|.*地点结构|.*没有的事实|.*都没有的|第 \d+ 话没落到", p)]
    if _hard(probs) and rows:
        fix_sys = au._fill(au._ins("故事_分话框架修复_指令词.txt"), settings or {}, None)
        table = "\n".join(_fw_row_text(r) for r in rows)
        fix_user = "【一句话故事】\n%s\n\n【必须落进去的剧情点，按顺序】\n%s\n\n【现在的框架】\n%s\n\n【要修的】\n%s" % (
            one, "\n".join("%d. %s" % (i + 1, p) for i, p in enumerate(points)), table, "\n".join("· " + p for p in probs))
        rep2 = au._q(fix_sys, fix_user, mt=2000, temperature=0.3)
        fixed = _fw_parse(rep2)
        calls += 1
        # 只合并被改的那几行（P98：让模型吐整表会丢行、乱话号）
        merged = [dict(r) for r in rows]
        by_no = {r["no"]: i for i, r in enumerate(merged)}
        for fr in fixed:
            if fr["no"] in by_no:
                merged[by_no[fr["no"]]] = fr
            elif 1 <= fr["no"] <= len(merged) + 1 and "拆" in fr.get("note", ""):
                merged.insert(fr["no"] - 1, fr)
        for i, r in enumerate(merged):
            r["no"] = i + 1
        probs2 = (_drop_dup_checks(framework_problems(merged, one, points, n_eps), _sh)
                  + _slot_problems(merged, _sh.get("slots") or [], _sh.get("limits") or []))
        _log.append("框架修复稿：改了 %d 行，问题 %d：%s" % (len(fixed), len(probs2), "；".join(probs2)[:300]))
        if fixed and len(_hard(probs2)) < len(_hard(probs)):
            rows, probs = merged, probs2
        else:
            _log.append("修复稿没有减少硬问题，保留第 1 稿")
    # 确定性兜底：节奏没填按内容判；几何没填按内容判
    _OBST = re.compile(r"回避|避开|躲|拒|不理|不肯|不让|不愿|拦|挡|忙得|没空|催|骂|嫌|瞪|推开|拉开|阻|不许|禁止|监视|追[赶上出来过着了]|抓|打|抢|赶|逃|争|吵|误会|质问|不信|怀疑|盘问|讨价|不给|收回|护住")   # 「视线追随」不是阻力（P129g）
    for r in rows:
        if not r["pace"]:
            r["pace"] = _pace.detect(r["one_line"])
        # 软基调下的假推进（P129d）：阻力栏里没有挡人的词、原文也没有推进/冲突词 → 这一话就是日常，阻力无
        if (_tone in ("日常喜剧", "爱情暧昧", "治愈成长") and r["pace"] == "推进"
                and not _OBST.search(str(r.get("resistance") or "")) and not _OBST.search(r["one_line"])
                and not _pace._MID.search(r["one_line"]) and not _pace._CONFLICT.search(r["one_line"])):
            _log.append("第 %d 话阻力「%s」不是阻力，节奏 推进→日常" % (r["no"], str(r.get("resistance") or "")[:16]))
            r["pace"], r["resistance"] = "日常", "无"
        # 软基调、一句话里没有追杀/打斗词 → 不许高潮（P129m：调戏被标成高潮，剧本就撞人挑下巴）
        if _tone in ("日常喜剧", "爱情暧昧", "治愈成长") and r["pace"] == "高潮" and not _pace._HIGH.search(one) and not _pace._CONFLICT.search(one):
            _log.append("第 %d 话软基调不许高潮，高潮→推进" % r["no"])
            r["pace"] = "推进"
        # 地点归一（P129m）：行里的地点含一句话里的地名，就只留那个地名（「酒馆二楼雅座」→「酒馆」），场景卡才不会裂成两个
        _pl_one = [m.group(1) for m in _PLACE_RE.finditer(one)]
        _pl_one = sorted(set(re.sub(r"^[一-龥]{0,3}?(?=酒馆|客栈|茶馆|饭馆|酒楼)", "", p) if re.search(r"酒馆|客栈|茶馆|饭馆|酒楼", p) else p for p in _pl_one), key=len, reverse=True)
        if _pl_one and str(r.get("place") or "").strip():
            _toks = [t.strip() for t in re.split(r"[、／/，,；;]", str(r.get("place") or "")) if t.strip()]
            _new = []
            for t in _toks:
                _hit = next((p for p in _pl_one if p in t), "")
                _new.append(_hit or t)
            _new = list(dict.fromkeys(_new))
            if "、".join(_new) != str(r.get("place") or "").strip():
                _log.append("第 %d 话地点「%s」→「%s」" % (r["no"], str(r.get("place") or "")[:12], "、".join(_new)))
                r["place"] = "、".join(_new)
        if not r["geometry"]:
            r["geometry"] = "追逐" if re.search(r"追|逃|载具|摩托|马车", r["one_line"]) else ("对峙" if (r["pace"] == "高潮" or _pace.no_strike(r["one_line"])) else ("行进" if re.search(r"赶路|旅行|沿路|骑|走|上山|下山", r["one_line"]) else "对谈"))
        r["no_strike"] = _pace.no_strike(r["one_line"])
        # 在场栏：一句话里出现过的名字，只要这一话原文里也出现了，就必须在在场栏里（模型爱写「贵族少年」这类身份词，P129c）
        for _nm in _fw_names_strict(one):
            if _nm in r["one_line"] and _nm not in str(r.get("cast") or ""):
                r["cast"] = (str(r.get("cast") or "").strip() + "、" + _nm).strip("、")
        # 在场栏碎片清理（P151）：以动词/方位字结尾的（老陈发、课桌里、监控里）、或别人名字的加长版，删掉
        _toks = [x.strip() for x in re.split(r"[、，,／/和与及]", str(r.get("cast") or "")) if x.strip()]
        _known = {str(p.get("name") or "").strip() for p in _people if str(p.get("name") or "").strip()}
        _keep_t = []
        for _t in _toks:
            if _t in _known:
                _keep_t.append(_t)
                continue
            if re.search(r"[发现说看走来去在的了里中上下边旁着过给把被]$", _t) and _t not in one:
                continue
            if any(_t != _k and _t.startswith(_k) for _k in _known):
                continue
            _keep_t.append(_t)
        if _keep_t and "、".join(_keep_t) != str(r.get("cast") or ""):
            r["cast"] = "、".join(dict.fromkeys(_keep_t))
        r["events"] = extract_events_offline(r["one_line"])
        if not str(r.get("place") or "").strip():           # 地点没填：从这一话原文里抠一个地名，再不行沿用上一话（P129）
            _pm = _PLACE_RE.search(r["one_line"])
            r["place"] = _pm.group(1) if _pm else (rows[rows.index(r) - 1].get("place", "") if rows.index(r) > 0 else "")
        # 「补」由代码判：这一话有没有落到用户的剧情点（P98）
        _hit = [p for p in points if event_in_text(p, r["one_line"]) and _event_anchors(p, r["one_line"])]
        r["note"] = "" if _hit else "补"
        r["points"] = _hit
    # P171②：某一话和前面某话重复 → 只重写这一话（定点修，不整表重来）
    rows = _rewrite_dup_rows(rows, _sh.get("slots") or [], one, sysm, user, _log)
    # P169②：节奏由格子的作用定，直接改写，不问模型也不打回
    _slots_now = _sh.get("slots") or []
    for _i, _sl in enumerate(_slots_now):
        if _i < len(rows):
            _want = _shape.slot_pace(_sl.get("role"))
            if str(rows[_i].get("pace") or "") != _want:
                rows[_i]["pace"] = _want
    # P169③：一句话里没名字的人，照原句怎么叫就怎么叫；模型自造的称呼换回去
    rows = _fix_person_words(rows, one, _people)
    _w = list(getattr(framework_problems, "warnings", []) or [])
    if _note_n:
        _w.insert(0, _note_n)
    # 硬问题单独回给调用方：一键生成那边要按它决定是不是"这一步没通过"（P164③）
    return {"rows": rows, "problems": probs, "hard": _hard(probs), "warnings": _w,
            "points": points, "calls": calls, "n": int(n_eps), "tone": _tone, "people": _people}

def apply_plan_rows(settings, ep_no=1):
    """多话计划：第 ep_no 话只认自己那一行；后面几行的事件关键词进 _later_events 当禁词。
    返回这一话该用的一句话（没有计划表就原样返回 settings.one_line）。"""
    s = settings if isinstance(settings, dict) else {}
    rows = [r for r in (s.get("plan_rows") or []) if isinstance(r, dict) and str(r.get("one_line") or "").strip()]
    if not rows or ep_no < 1 or ep_no > len(rows):
        return str(s.get("one_line") or "")
    row = rows[ep_no - 1]
    if str(row.get("pace") or "") in _pace.PACES:
        s["pace"] = row["pace"]
    # 框架表的「在场的人」（P102）：生成链按它放人
    s["_row_cast"] = [re.sub(r"（[^）]*）", "", x).strip() for x in re.split(r"[、，,／/和与及]", str(row.get("cast") or "")) if x.strip() and x.strip() not in ("无", "没有")]
    for _k in ("place", "resistance", "landing", "geometry", "change"):      # 结构行的 地点/阻力/落点/几何/变化点 一路传到要素表、大纲、逐拍（P129）
        s["_row_" + _k] = str(row.get(_k) or "").strip()
    # 高频名字（≥3 行出现的 2～3 字串）先从后话原文里抠掉再切块，免得切出「渊身后」「柱手里」（P84）
    _texts0 = [str(r.get("one_line") or "") for r in rows]
    _names0 = set()
    for t in _texts0:
        for i in range(len(t)):
            for L in (3, 2):
                w = t[i:i + L]
                if len(w) == L and re.fullmatch(r"[一-龥]+", w) and sum(1 for x in _texts0 if w in x) >= 3:
                    _names0.add(w)
    later = []
    for r in rows[ep_no:]:
        _t = str(r.get("one_line") or "")
        for w in sorted(_names0, key=len, reverse=True):
            _t = _t.replace(w, "，")
        later += later_event_words(_t)
    # 只留前面 ≤ep 话原文里都**没出现**的词（P83：「李长渊身」「天云门」「阿禾躲在」这种共用词不许当禁词）
    mine = str(row.get("one_line") or "")
    earlier = "".join(str(r.get("one_line") or "") for r in rows[:ep_no])
    # 人名会出现在很多话里：在 ≥3 行里都出现的 2～3 字串当作名字，含名字的块不当禁词（「阿禾躲在」「柱手里」）
    _texts = [str(r.get("one_line") or "") for r in rows]
    _freq = set()
    for t in _texts:
        for i in range(len(t)):
            for L in (2, 3):
                w = t[i:i + L]
                if len(w) == L and re.fullmatch(r"[一-龥]+", w) and sum(1 for x in _texts if w in x) >= 3:
                    _freq.add(w)
    s["_later_events"] = sorted({w for w in later if w not in earlier and len(w) >= 3
                                 and not any(f in w for f in _freq)})[:40]
    return mine


_STOP = {"东西", "一个", "两人", "后面", "之后", "然后", "这个", "那个", "自己", "一起", "一下", "什么",
         "怎么", "这样", "那样", "还是", "已经", "开始", "最后", "无意", "人们", "有人", "大家", "他们", "她们"}


def later_event_words(one_line):
    """后话一句话里能当禁词的实词：按虚词切开，取 2～4 字块，去掉停用词和带虚词的块。
    （2026-09-05：原来用滑动 n-gram，切出「力全开却」「到丈夫」这种碎片，还把「东西」禁了。）"""
    t = str(one_line or "")
    chunks = re.split(r"[，。、；：！？,.;:!? ]|的|了|着|过|和|与|在|到|却|把|被|是|也|都|就|还|很|又|去|来|从|向|给|对|让|要|会|能|想|说", t)
    out = []
    for c in chunks:
        c = c.strip()
        # 只要 3～4 字的块：它们才是"事件短语"（不管女儿／女孩不肯／一根手指）。
        # 2 字块多是全剧共用的设定名词（宗门／凡人／丈夫），禁了会误伤这一话（2026-09-05）。
        if 3 <= len(c) <= 4 and re.fullmatch(r"[一-龥]+", c) and c not in _STOP:
            out.append(c)
        elif len(c) > 4:
            for w in (c[:3], c[-3:]):
                if re.fullmatch(r"[一-龥]+", w) and w not in _STOP:
                    out.append(w)
    return out


def extract_events_offline(one_line):
    """不调模型的事件粗拆：按「，。；然后/接着/再/最后」切句，每句一条。给多话禁词和预览兜底用。"""
    t = str(one_line or "")
    parts = re.split(r"[，。；,;]|然后|接着|再|最后|之后|随后|无意间", t)
    _DESC = re.compile(r"^(?:[一-龥]{0,3}(?:年轻|美丽|漂亮|火辣|绝美|紧致|高挑|娇小|瘦弱|英俊|帅气|冷峭|温柔|活泼|调皮|敏感|可爱)[一-龥]{0,4}|身材[一-龥]{0,6}|长相[一-龥]{0,6}|穿着[一-龥]{0,8}|[一-龥]{0,4}画风|[一-龥]{0,4}质感|[一-龥]{2,4}风格)$")
    out = [p.strip() for p in parts if len(p.strip()) >= 4 and not _DESC.match(p.strip())]     # 纯描写不算事件（P114）
    return out[:8]


def structure_gaps(rows):
    """代码判缺口，最多 3 个问题。模型只负责措辞（这里直接给成中文句子，不再调模型）。"""
    qs = []
    alltxt = "".join(str(r.get("one_line") or "") for r in rows)
    # ① 提到的人没说明生死/态度
    for who in ("丈夫", "妻子", "父亲", "母亲", "师父", "长老", "国王", "队长"):
        if who in alltxt and not re.search(who + r".{0,12}(死|活着|去世|离开|反对|支持|同意|不管|负心|另娶|失踪|不收|不肯|不许|拦|收养|答应|默许)", alltxt):
            qs.append("「%s」在故事里是什么态度、还在不在？（这决定他是阻力还是背景）" % who)
            break
    # ② 阻力有两个候选没定
    for r in rows:
        t = str(r.get("one_line") or "")
        if t.count("不肯") + t.count("不收") + t.count("不让") + t.count("反对") >= 2:
            qs.append("这一话有两处阻力（%s），哪一处是主的？" % "、".join(re.findall(r"[一-龥]{1,4}(?:不肯|不收|不让|反对)", t)[:2]))
            break
    # ③ 日常段没说做几话
    if re.search(r"日常|相处|后面|后续", alltxt) and not re.search(r"[一二三四五六七八九十\d]+话", alltxt):
        qs.append("日常/相处的部分打算做几话？一话两分钟只装得下一件小事。")
    # ④ 高潮档却没有对手
    for i, r in enumerate(rows, 1):
        t = str(r.get("one_line") or "")
        if _pace.detect(t) == "高潮" and not re.search(r"怪|敌|匪|贼|杀手|追兵|机甲|巨|兽|魔|军", t):
            qs.append("第 %d 话是高潮，但没写对手是谁。" % i)
            break
    return qs[:3]

def generate_episode(sid, one_line, settings=None, chars=None, on_step=None, place=""):
    """一句话 → 要素表 → 大纲卡 → 剧本 → 审校/定向修。返回 dict(script, prose, outline, elements, review)。约 8～11 次 Qwen 调用。"""
    step = on_step or (lambda m: None)
    review = []
    step("编剧组：拆一句话要素")
    _scenes = []
    try:
        from . import asset_core as _ac
        _scenes = [x for x in (_ac.list_assets(sid, "scenes") or []) if x.get("name")]
    except Exception:
        pass
    el = analyze_one_line(one_line, settings, chars=chars, scenes=_scenes)
    names = el.get("_names") or []
    try:
        el["_allowed_cast"] = allowed_cast(el, one_line, names, (settings or {}).get("_row_cast"))   # 这一话在场的人（P91/P102）
    except Exception:
        el["_allowed_cast"] = []
    step("编剧组：写六拍大纲卡")
    if isinstance(settings, dict):
        settings["_no_foe"] = bool(el.get("_no_foe"))      # 大纲系统词按有无对手换（P129e）
    outline = episode_outline(el, settings)
    probs = outline_problems(outline, el)
    if probs:
        review.append("大纲卡：" + "；".join(probs))
        hard = [p for p in probs if re.search(r"物件|重复动作|没有台词|只有 \d+ 拍|不是正在发生|句台词|点明对手", p)]
        if hard:
            step("编剧组：大纲卡有硬问题，重写一次")
            outline2 = episode_outline(el, settings, note="★上一稿的问题：%s。六拍必须是六个**不同**的事件，一拍由上一拍引起，同一个动作只做一次；"
                                       "关键物件在谁手里要一拍接一拍：谁夺了就在谁手里，别人不能凭空再递。" % "；".join(hard))
            probs2 = outline_problems(outline2, el)
            if len(probs2) < len(probs):
                outline, probs = outline2, probs2
            review.append("大纲卡重写后：%s" % ("；".join(probs) or "通过"))
    # 用户事件重写一次仍缺 → 代码直接把事件文字注入那一拍（确定性兜底，不再问模型）
    _still = events_missing(outline, el.get("_events") or [])
    if _still:
        for _ev, _k in _still:
            if 0 <= _k < len(outline):
                outline[_k]["event"] = "%s。%s" % (_ev, str(outline[_k].get("event") or ""))
        review.append("事件兜底注入：" + "、".join("拍%d←「%s」" % (_k + 1, _ev) for _ev, _k in _still))
    # 大纲模板词事件（P104）：重写后还在就改成承接句
    _tpl = re.compile(r"一处阻力|小对立|（[^）]*[／/][^）]*）|规矩对立|承接前后|新事实[揭浮][示露现出晓]|新事实揭晓|揭示新事实|转折事实|一次交锋|一次反击|一次代价|确认时间线|阻力出现|对立升级")
    _tf = []
    for _i, _r in enumerate(outline):
        if _tpl.search(str(_r.get("event") or "")):
            _prev_ev = str(outline[_i - 1].get("event") or "") if _i > 0 else ""
            _r["event"] = "（承接上一拍「%s」，在场的人继续做手上的事，出一个看得见的小变化）" % _prev_ev[:14]
            if _tpl.search(str(_r.get("action") or "")) or not str(_r.get("action") or "").strip():
                _r["action"] = _r["event"]
            _r["line"] = ""                                 # 模板词拍的台词也是编的（P129n）
            _tf.append(_i)
    if _tf:
        review.append("大纲模板词改成承接：拍" + "、".join(str(k + 1) for k in _tf))
    # 日常档的威胁（P94）：重写后还在就代码删分句
    _dt = strip_daily_threat(outline, el)
    if _dt:
        review.append("大纲删掉日常档硬塞的威胁：拍" + "、".join(str(k + 1) for k in _dt))
    # 大纲里不在这一话的人（P92）：重写后还在就代码删分句
    _sf = strip_foreign_from_outline(outline, el.get("_allowed_cast") or [], names)
    if _sf:
        review.append("大纲删掉不在场的人：拍" + "、".join(str(k + 1) for k in _sf))
    # 补的拍不许提前演后面钉了的事件；日常档补拍不许让主角乱跑（P129）
    _pm = outline_premature_beats(outline, el.get("_events") or [], names)
    if _pm:
        review.append("大纲补拍提前演了后面的事→改承接：拍" + "、".join(str(k + 1) for k in _pm))
    _dm = outline_daily_moves(outline, el, one_line, names)
    if _dm:
        review.append("日常档补拍让主角乱跑→改承接：拍" + "、".join(str(k + 1) for k in _dm))
    _lk = outline_look_ladder(outline, el, names, one_line)
    if _lk:
        review.append("看的递进填进承接拍：拍" + "、".join(str(k + 1) for k in _lk))
    _ll = outline_landing_only_last(outline, (settings or {}).get("_row_landing"), list(names or []) + list(el.get("_allowed_cast") or []))
    if _ll:
        review.append("落点提前到了前面的拍→删：拍" + "、".join(str(k + 1) for k in _ll))
    _nf = outline_drop_foreign_lines(outline, list(names or []) + list(el.get("_allowed_cast") or []))
    if _nf:
        review.append("大纲删掉名单外的台词 %d 句（食客甲乙这类路人不说话）" % _nf)
    _soft_ep = str((settings or {}).get("tone") or "") in ("日常喜剧", "爱情暧昧", "治愈成长") and el.get("_no_foe")
    if _soft_ep:
        _sc = soft_clean_outline(outline, one_line)
        if _sc:
            review.append("软基调大纲删掉一句话没有的接触/打斗：拍" + "、".join(str(k + 1) for k in _sc))
    # 钉了事件的拍，动作栏／站位必须是在做那件事（P74：拍1 事件"沿路旅行"、动作栏却围桌滴药水，开场跑进酒馆）
    _fixed_acts = pin_actions_to_events(outline, el.get("_events") or [], names=names)
    if _fixed_acts:
        review.append("动作栏对齐事件：拍" + "、".join(str(k + 1) for k in _fixed_acts))
    _rules_ep = _pace.rules(str(el.get("_pace") or "推进"))
    review.append("节奏档：%s；事件 %d 条" % (el.get("_pace"), len(el.get("_events") or [])))
    # 台词句数是可数的硬指标，不指望模型整卡重写能改对：定稿后按拍补足（2026-09-05 公路片 7 句）
    for _r in range(2):
        _tot = sum(_line_count(r.get("line")) for r in outline)
        if _tot >= _rules_ep["dialogue_min"]:
            break
        _log2 = []
        try:
            outline = fill_missing_lines(outline, el, names, log=_log2, want=2 + _r)
        except Exception as _ex:
            review.append("台词补足第%d轮出错：%s" % (_r + 1, str(_ex)[:80]))
            break
        _new = sum(_line_count(r.get("line")) for r in outline)
        review.append("台词补足第%d轮：%d → %d 句" % (_r + 1, _tot, _new))
        if _new <= _tot:
            break
    _nf2 = outline_drop_foreign_lines(outline, list(names or []) + list(el.get("_allowed_cast") or []))
    if _nf2:
        review.append("台词补足后再删名单外台词 %d 句" % _nf2)
    if _soft_ep:
        soft_clean_outline(outline, one_line)
    digest = au._chars_digest(chars or [])
    strip_subject_bleed._cards = chars                    # 串人判定按卡指纹（P129v）
    write_script_by_beats._settings = settings or {}
    step("编剧组：逐拍写画面剧本")
    _wlog = []
    _spans = []
    script = write_script_by_beats(el, outline, digest, place, names=names, on_step=step, log=_wlog, spans=_spans)
    _parts = [p for p in re.split(r"\n\s*\n", script) if p.strip()]
    if len(_spans) == len(outline) and sum(b - a for a, b in _spans) == len(_parts):
        _np = script_name_problems(script, names) + script_shape_problems(script, outline)
        if _np or thin_beats(_parts, _spans):
            if _np:
                review.append("剧本第一稿：%s" % "；".join(_np))
            step("编剧组：补薄的拍")
            _parts, _spans = rewrite_thin_beats(el, outline, digest, place, names, _parts, _spans, on_step=step, log=_wlog)
            script = "\n\n".join(_parts)
        # 补完还不到两分钟的量 → 再补最薄的几拍（后面的守卫还会削掉 150～250 字，所以目标留到 3600）
        for _round in range(2):
            # 目标 4000 不是笔误：后面的微表情限次会削掉 300～500 字（2026-09-05 实测两例被削到 3200 以下）
            if len("\n\n".join(_parts)) >= 4000 or len(_spans) != len(outline):
                break
            _sizes = sorted(range(len(_spans)), key=lambda i: sum(len(x) for x in _parts[_spans[i][0]:_spans[i][1]]))[:3]
            _notes = {}
            for i in _sizes:
                _cur = sum(len(x) for x in _parts[_spans[i][0]:_spans[i][1]])
                _notes[i] = ("这一拍现在只有 %d 字、%d 幅，太薄。这次写满 6～7 幅、每幅 80～140 字，这一拍加起来 **600 字以上**；"
                             "每幅一个新的位移或接触，不许把上一幅的动作再写一遍。" % (_cur, _spans[i][1] - _spans[i][0]))
            _prev = [_parts[a:b] for a, b in _spans]
            _ns = []
            _txt2 = write_script_by_beats(el, outline, digest, place, names=names, on_step=step, log=_wlog,
                                          spans=_ns, only=set(_sizes), notes=_notes, prev_parts=_prev)
            _p2 = [p for p in re.split(r"\n\s*\n", _txt2) if p.strip()]
            if len(_ns) == len(_spans) and len(_txt2) > len("\n\n".join(_parts)):
                _parts, _spans, script = _p2, _ns, _txt2
                _wlog.append("二次补薄第%d轮：拍%s → %d 字" % (_round + 1, "、".join(str(i + 1) for i in _sizes), len(_txt2)))
            else:
                break
    # 幅落地核对（P74）：钉了事件的拍，它的幅必须在事件的地点、做那件事；跑偏的只重写那几拍
    if len(_spans) == len(outline) and sum(b - a for a, b in _spans) == len(_parts):
        _off = beats_offtrack(_parts, _spans, outline, el.get("_events") or [])
        if _off:
            step("编剧组：重写跑偏的拍")
            _notes = {i: "★这一拍的事件是「%s」：每一幅都必须在这个地方、做这件事。上一稿%s，整拍重写，不许出现那个地方的东西。" % (ev, why)
                      for i, (ev, why) in _off.items()}
            _prev = [_parts[a:b] for a, b in _spans]
            _ns = []
            _txt3 = write_script_by_beats(el, outline, digest, place, names=names, on_step=step, log=_wlog,
                                          spans=_ns, only=set(_off), notes=_notes, prev_parts=_prev)
            _p3 = [p for p in re.split(r"\n\s*\n", _txt3) if p.strip()]
            if len(_ns) == len(_spans) and sum(b - a for a, b in _ns) == len(_p3) and not beats_offtrack(_p3, _ns, outline, el.get("_events") or []):
                _parts, _spans, script = _p3, _ns, _txt3
                _wlog.append("幅落地重写：拍" + "、".join(str(i + 1) for i in sorted(_off)))
            else:
                _wlog.append("幅落地重写未通过，保留原稿：拍" + "、".join(str(i + 1) for i in sorted(_off)))
    # 对峙而不打（P77）：主角出手的幅所在的拍只重写那几拍
    if el.get("_no_strike") and len(_spans) == len(outline) and sum(b - a for a, b in _spans) == len(_parts):
        _hero = next((n for n in (names or []) if n in str(el.get("主角") or "")), (names or [""])[0])
        _bad = strike_paras(_parts, _hero)
        if _bad:
            _beats = {k for k, (a, b) in enumerate(_spans) if any(a <= i < b for i in _bad)}
            step("编剧组：重写出手的拍")
            _notes = {k: "★这一拍主角%s**一次都不能出手**：上一稿写了「%s」。不出招、不放法术攻击、不用剑气掌风碰对方、不打不踹不逼退；"
                         "改成逼近、盯着、按住剑柄、攥拳再松开、法力翻涌却不放出去。对方可以怕、可以退，但不能是被打的。整拍重写。"
                      % (_hero, "；".join(strike_hits("\n".join(_parts[_spans[k][0]:_spans[k][1]]), _hero)[:2])) for k in _beats}
            _prev = [_parts[a:b] for a, b in _spans]
            _ns = []
            _txt4 = write_script_by_beats(el, outline, digest, place, names=names, on_step=step, log=_wlog,
                                          spans=_ns, only=_beats, notes=_notes, prev_parts=_prev)
            _p4 = [p for p in re.split(r"\n\s*\n", _txt4) if p.strip()]
            if len(_ns) == len(_spans) and sum(b - a for a, b in _ns) == len(_p4) and len(strike_paras(_p4, _hero)) < len(_bad):
                _parts, _spans, script = _p4, _ns, _txt4
                _wlog.append("不能动手重写：拍" + "、".join(str(k + 1) for k in sorted(_beats)) + ("" if not strike_paras(_p4, _hero) else "（仍有 %d 幅出手）" % len(strike_paras(_p4, _hero))))
            else:
                _wlog.append("不能动手重写未通过，保留原稿：拍" + "、".join(str(k + 1) for k in sorted(_beats)))
    # 外来角色 + 结尾事件提前（P84）：只重写那几拍，一轮
    if len(_spans) == len(outline) and sum(b - a for a, b in _spans) == len(_parts):
        _allowed = allowed_cast(el, one_line, names, (settings or {}).get("_row_cast"))
        _known = list(dict.fromkeys(list(names or []) + list((settings or {}).get("_cast_known") or [])))
        _fc = foreign_cast(_parts, _allowed, _known) if _allowed else {}
        _fc_beats = {k for k, (a, b) in enumerate(_spans) if any(a <= i < b for i in _fc)}
        _el = ending_leak(_parts, _spans, outline, el.get("_events") or [], names=names)
        _rp = repeated_actions(_parts, _spans, names)
        _notes = {}
        for k, gs in _rp.items():
            _notes[k] = "★「%s」在前面的拍里已经演过了：这一拍不许再演同一个动作（同一件东西只拍一次、只夺一次、只付一次钱），换成这一拍大纲里自己的事。整拍重写。" % "」「".join(gs[:3])
        for k in _fc_beats:
            _who = sorted({n for i in _fc for n in _fc[i] if _spans[k][0] <= i < _spans[k][1]})
            _notes[k] = "★这一话在场的人只有：%s。%s 不在这一话里，一个字都不许出现，也不许换个称呼出现；不要为了有冲突把别人拉进来。整拍重写。" % ("、".join(_allowed), "、".join(_who))
        for k, ev in _el.items():
            _notes[k] = (_notes.get(k, "") + " ★「%s」是这一话最后一拍才发生的事：这一拍里不许发生，也不许用近义写法（攥着／拽着／勾着都算）。整拍重写。" % ev).strip()
        if _notes:
            step("编剧组：重写串人/提前抖包袱的拍")
            _prev = [_parts[a:b] for a, b in _spans]
            _ns = []
            _txt5 = write_script_by_beats(el, outline, digest, place, names=names, on_step=step, log=_wlog,
                                          spans=_ns, only=set(_notes), notes=_notes, prev_parts=_prev)
            _p5 = [p for p in re.split(r"\n\s*\n", _txt5) if p.strip()]
            if len(_ns) == len(_spans) and sum(b - a for a, b in _ns) == len(_p5):
                _fc2 = foreign_cast(_p5, _allowed, _known) if _allowed else {}
                _el2 = ending_leak(_p5, _ns, outline, el.get("_events") or [], names=names)
                _rp2 = repeated_actions(_p5, _ns, names)
                if len(_fc2) + len(_el2) + len(_rp2) < len(_fc) + len(_el) + len(_rp):
                    _parts, _spans, script = _p5, _ns, _txt5
                    _wlog.append("串人/提前重写：拍" + "、".join(str(k + 1) for k in sorted(_notes)) + ("" if not (_fc2 or _el2) else "（仍剩 %d 处）" % (len(_fc2) + len(_el2))))
                else:
                    _wlog.append("串人/提前重写未改善，保留原稿：拍" + "、".join(str(k + 1) for k in sorted(_notes)))
            else:
                _wlog.append("串人/提前重写未通过，保留原稿")
        if _fc:
            review.append("外来角色：" + "、".join(sorted({n for v in _fc.values() for n in v})))
        if _el:
            review.append("结尾事件提前：拍" + "、".join(str(k + 1) for k in sorted(_el)))
        if _rp:
            review.append("重复动作：" + "、".join("拍%d「%s」" % (k + 1, gs[0]) for k, gs in sorted(_rp.items())))
    # 守卫重写会削薄：不到 3200 字再补一轮薄拍（P92）
    if len(_spans) == len(outline) and sum(b - a for a, b in _spans) == len(_parts) and len("\n\n".join(_parts)) < 3200:
        step("编剧组：守卫后补薄")
        _parts2, _spans2 = rewrite_thin_beats(el, outline, digest, place, names, _parts, _spans, on_step=step, log=_wlog)
        if len(_spans2) == len(outline) and len("\n\n".join(_parts2)) > len("\n\n".join(_parts)):
            _parts, _spans, script = _parts2, _spans2, "\n\n".join(_parts2)
            _wlog.append("守卫后补薄 → %d 字" % len(script))
    script = tidy_script(script, names)
    # 名单外说话人的台词删掉（P85）
    _speak_ok = list(names or []) + list(el.get("_allowed_cast") or []) + list((settings or {}).get("_row_cast") or [])
    _speak_ok += [re.sub(r"（[^）]*）", "", x).strip() for x in re.split(r"[、，,／/和与及]", str(el.get("同伴") or "") + "，" + str(el.get("人物") or "")) if x.strip()]
    script, _nfr = clean_fragments(script, log=_wlog)                      # 残片/夹引号（P113）先清，拆出的台词再过名单
    if _nfr:
        _wlog.append("清残片 %d 处" % _nfr)
    script, _nuk = drop_unknown_speakers(script, [x for x in dict.fromkeys(_speak_ok) if x and x not in ("无", "没有")], one_line, log=_wlog)
    script, _ndt = drop_daily_threat_lines(script, el, log=_wlog)          # 日常档威胁台词（P94）
    if _wlog:
        review.append("；".join(_wlog))
    review.append("剧本：%d 字 %d 幅" % (len(script), len([p for p in re.split(r"\n\s*\n", script.strip()) if p.strip()])))
    # 审校：开场钩子
    hk = hook_check(script, el)
    changed = False
    if not all(hk.values()):
        why = "、".join(k for k, v in hk.items() if not v) + "不合格"
        script, note = fix_head_code(script, outline, names, hk)
        script, _ = normalize_dialogue(script)
        review.append("开场：%s → %s" % (why, note))
        changed = changed or note.startswith("开场代码修")
    # 审校：冷读看懂
    step("编剧组：冷读看懂测试")
    cold = cold_read(script)
    verdict = judge(el, outline, cold)
    review.append("冷读：" + "；".join("%s%s" % (k, "✓" if v else "✗") for k, v in verdict.items()))
    for item in ("主角要什么", "谁挡他", "谁是敌人"):
        if not verdict.get(item, True):
            step("编剧组：观众没看懂%s，补台词" % item)
            script, note = fix_goal(script, el, item, names=names)
            script, _ = normalize_dialogue(script)
            review.append(note)
            changed = changed or note.startswith("已补")
    if not verdict.get("局面在哪一刻变了", True) and str(el.get("转折事实") or "").strip() and len(_spans) == len(outline):
        step("编剧组：观众没看到转折，重写转折拍")
        _ti = 3 if len(outline) >= 4 else len(outline) - 1
        _parts0 = [p for p in re.split(r"\n\s*\n", script) if p.strip()]
        if sum(b - a for a, b in _spans) == len(_parts0):
            _prev = [_parts0[a:b] for a, b in _spans]
            _ns = []
            _txt = write_script_by_beats(el, outline, digest, place, names=names, on_step=step, log=_wlog, spans=_ns, only={_ti},
                                         notes={_ti: "观众看完没发现局面变了。这一拍必须把「转折事实：%s」用看得见的证据或动作演出来（一件物、一个动作、一句点破的台词），让人一眼知道情况不一样了。" % el.get("转折事实", "")},
                                         prev_parts=_prev)
            _np2 = [p for p in re.split(r"\n\s*\n", _txt) if p.strip()]
            if len(_ns) == len(_spans):
                script = tidy_script("\n\n".join(_np2), names)
                _spans = _ns
                review.append("转折拍已重写（拍%d）" % (_ti + 1))
                changed = True
    if not verdict.get("结尾悬念", True):
        step("编剧组：结尾不悬，重写结尾")
        script, note = fix_tail(script, el)
        script, _ = normalize_dialogue(script)
        review.append(note)
        changed = changed or note == "结尾已重写"
    # 说话人按大纲卡核对（确定性改名）
    script, _nsp = fix_line_speakers(script, outline, names)
    if _nsp:
        review.append("按大纲卡改了 %d 处说话人" % _nsp)
        changed = True
    # 连贯性：物件在谁手里 / 谁站哪 / 重复动作
    step("编剧组：查前后矛盾")
    _clog = []
    script, _cnote = continuity_pass(script, names, log=_clog)
    review.append(_cnote + ("；" + "；".join(_clog) if _clog else ""))
    changed = changed or ("修了 0 处" not in _cnote and "查出" in _cnote)
    script, _ = normalize_dialogue(script)
    script = tidy_script(script, names)
    _rlog = []
    script = drop_repeated_actions(script, log=_rlog)
    if _rlog:
        review.extend(_rlog)
        changed = True
    # 修过东西才复读；一处没改就沿用第一次的答案（省 2 次调用）
    if changed:
        step("编剧组：复读确认")
        cold2 = cold_read(script)
        verdict2 = judge(el, outline, cold2)
        review.append("复读：" + "；".join("%s%s" % (k, "✓" if v else "✗") for k, v in verdict2.items()))
        cold = cold2
    else:
        review.append("中途没改动，不复读")
    script = strip_line_quotes(script)
    _mlog = []
    script = drop_meta_lines(script, el, log=_mlog)
    script = cap_micro_expressions(script, per_kind=_rules_ep["micro_per_kind"], log=_mlog)
    script = dedupe_lines_global(script, log=_mlog, prev_lines=(settings or {}).get("_prev_lines") or [])
    script = tidy_script(script, names)
    review.extend(_mlog)
    # 光线全篇统一（要素表定的时间与光线为准）
    _llog = []
    script = light_guard(script, el, log=_llog)
    review.extend(_llog)
    # 同乘一辆载具的两个人不许各有一辆（2026-09-05：女主坐后座却有"苏拉摩托的挡泥板"）
    _ride = shared_ride(script, names)
    if _ride:
        _front, _back, _veh = _ride
        _before = script
        script = drop_ally_vehicle(script, _back, _veh)
        if script != _before:
            review.append("同乘修：%s坐在%s的%s后面，去掉了「%s的%s」" % (_back, _front, _veh, _back, _veh))
        el["_ride"] = "%s和%s同乘一辆%s，%s在前%s在后，全片只有这一辆" % (_front, _back, _veh, _front, _back)
    # 卫生：拍不到的句子（台词遮住）→ 兜底删
    script = au.clean_pictures(script)
    # 微表情限次**最后再跑一遍**：前面那次之后 tidy_script/light_guard 会重排标点，
    # 分句边界一变，按分句数的上限就守不住（2026-09-05：限完 13 处，存盘 17 处）。
    _mlog2 = []
    script = cap_micro_expressions(script, per_kind=_rules_ep["micro_per_kind"], log=_mlog2)
    if _mlog2:
        review.append("收尾" + _mlog2[0])
    if str((settings or {}).get("tone") or "") in ("日常喜剧", "爱情暧昧", "治愈成长") and el.get("_no_foe"):
        script, _nct = soft_strip_contact(script, one_line)
        if _nct:
            review.append("软基调剧本删掉一句话没有的身体接触分句 %d 处" % _nct)
    script, _nfr2 = clean_fragments(script)                 # 限次删分句之后再清一遍残片（P129f）
    script, _nsub = fix_para_subjects(script, list(dict.fromkeys(list(names or []) + list(el.get("_allowed_cast") or []))), cards=chars)
    if _nsub:
        review.append("幅首补主语 %d 处" % _nsub)
    if len(script) < 3000:
        review.append("⚠ 最终只有 %d 字（两分钟约需 3200+），切段后可能不到 110 秒" % len(script))
    prose = derive_prose(script)
    step("编剧组：写给观众看的故事简介")
    _slog = []
    synopsis = write_reader_synopsis(script, el, names=names, log=_slog)
    review.extend(_slog)
    return {"script": script, "prose": prose, "outline": outline, "elements": el, "review": review, "cold": cold, "synopsis": synopsis}
