# -*- coding: utf-8 -*-
"""story_shape.py —— 读懂一句话，定出这个故事**自己**的形状。

【为什么要有这个文件】
2026-09-07 查出来的：整部故事的框架一直用「单话事件抽取器」在抽剧情点，
那个抽取器写着「最多 6 条」「后面/之后才发生的事不要写进来」——
对整部故事恰恰相反，"后来""最后"那句往往就是结局。
实测天云门那句话（148 字）抽出 8 条，停在「想带她回宗门」，
后面的「女孩不肯」「宗门也不收凡人」「后来两人情同父女」「日常相处放在很靠后」
四条全丢，结局和用户的创作要求根本没进规划。

话数也一样：原来按一句话有多少汉字定（<40→1 话，>120→字数/15），
于是同一个故事多写两句人物介绍就多分几话。**话数应该由故事本身的内容和节奏定。**

这个模块只做四件事，都不改故事内容：
  ① 把一句话拆成四张表：已定事实 / 必须发生的剧情 / 创作要求 / 待补连接
  ② 认出每条剧情的**作用**（过渡、发现、冲突、转变、结局）
  ③ 按作用给篇幅，推出话数——短故事短框架，长故事长框架，都是从内容推的
  ④ 把待补的连接按规则找出来（限制没给理由、认出没给依据、关系没给过程…）

除了抽事件那一步问一次模型，其余全是确定性的。
"""
import re

# ══ 一、句子分类的词表（判据是句式，不是某个故事） ══════════════

# 创作要求：管整篇怎么写，不是发生的事。原来这类句子被当成事件或被丢掉。
_ASK_RE = re.compile(
    # 元指令＝在说"这故事该怎么写"，不是在说发生了什么。必须带「放在/安排在/留到…」
    # 这类上下文——光有一个「最后」是叙事词，不是要求（实测「最后报了警」被误判过）。
    r"(戏|部分|情节|内容|镜头|篇幅)[^。；]{0,6}(放在|安排在|留到|挪到|压到)|"
    r"(放在|安排在|留到|挪到|压到)[^。；]{0,4}(很)?(靠后|靠前|后面|前面|最后|开头)|"
    r"多写|少写|重点写|着重写|不要写|别写|不许写|一笔带过|"
    r"偏(悬疑|喜剧|温情|甜|虐|燃|治愈|恐怖)|(风格|画风|基调|节奏)(要|得|走|偏)|"
    r"要有(反转|悬念|笑点|泪点)|结局(要|得)|(全篇|整篇|通篇)")

# 背景：已经发生完了的状态，不是这部故事里要演的事
_BG_RE = re.compile(r"(十|几|数|[一二三四五六七八九十百千0-9]+)\s*(年|月|天|日|载)前|"
                    r"当年|曾经|从前|原本|本来|一直没|从未|从没|自小|打小")

# 性格 / 外貌：是人物属性，不是事件（原来「调皮捣蛋又敏感」被拆成两个先后发生的事件）
_TRAIT_RE = re.compile(r"调皮|捣蛋|敏感|老实|温柔|冷淡|高傲|傲|怯|胆小|开朗|internal|"
                       r"活泼|沉默|寡言|暴躁|善良|狡猾|机灵|倔|要强|好强|爱哭|贪吃|"
                       r"漂亮|好看|英俊|清秀|白净|黝黑|高大|瘦小|年幼|年轻|年迈")

# 限制：门规、誓言、身体条件。它是障碍，不是一话戏——不占话，挂到相邻那一话上。
_LIMIT_RE = re.compile(r"不能|不许|不可|不准|不收|禁止|动不了|使不出|无法|没法")

# 结局标记：这句话讲的是全篇的终点。**整部故事必须保留它**（老抽取器恰恰把它删掉）
_END_RE = re.compile(r"后来|最后|最终|终于|从此|结局|到头来|末了|再后来")

# 作用分类（优先级从下往上：转变 > 冲突 > 发现 > 过渡）
_ROLE_PATTERNS = [
    ("过渡", re.compile(r"领命|受命|奉命|派.{0,4}去|下山|上山|出发|上路|赶路|启程|前往|"
                        r"抵达|来到|回到|走到|进门|敲门|推门|坐车|骑马|御剑")),
    ("发现", re.compile(r"找到|见到|遇到|碰到|确认|得知|听说|打听|问出|认出|发现|查到|"
                        r"看见|撞见|翻出|收到|捡到|拿到|想起|识破|揭穿|真相")),
    ("冲突", re.compile(r"质问|对质|争|吵|骂|拒绝|不肯|不愿|不给|不许|抢|夺|拦|挡|"
                        r"打|追|逃|躲|逼|威胁|呵斥|动手|翻脸|对峙|阻止|反对|嫌|赶走")),
    ("转变", re.compile(r"信任|相信|愿意|松口|改变|回心|原谅|和好|和解|情同|接受|答应|"
                        r"承认|放下|释怀|认.{0,2}为(父|母|师|徒)|决定|下定决心|明白|懂了|"
                        r"主动|第一次(主动|开口|笑)|重新")),
]

# 待补连接：什么样的句子必然缺一环（判据是句式，对任何故事都成立）
_GAP_RULES = [
    (re.compile(r"不能|不许|不可|不准|不收|禁止|动不了|使不出"),
     "为什么%s——不给理由，这只是个设定，不是压力"),
    (re.compile(r"认出|发现|识破|想起|看出"),
     "凭什么%s——认出和发现必须有可信依据"),
    (re.compile(r"情同|信任|相信|和好|和解|反目|愿意跟|认.{0,2}为(父|母|师|徒)"),
     "靠什么走到%s——关系变化要有积累，不能一次帮忙就到位"),
    (re.compile(r"不肯|拒绝|不愿|不答应"),
     "%s是在怕什么——拒绝要有理由，不能只是结果"),
]


# ══ 二、拆句 ══════════════════════════════════════════════════

def split_clauses(one):
    """按句号分句，再按分号/逗号切成能独立成事的片段。片段太短的并回上一句。"""
    out = []
    for sent in re.split(r"(?<=[。；！？])", str(one or "")):
        sent = sent.strip()
        if not sent:
            continue
        parts = [p.strip() for p in re.split(r"[；;，,]", sent) if p.strip()]
        buf = ""
        for p in parts:
            buf = (buf + "，" + p) if buf else p
            # 一个片段至少要有一个动作词才算能独立成事
            if len(buf) >= 8 and re.search(r"[一-龥]{2,}", buf):
                out.append(buf)
                buf = ""
        if buf:
            if out and len(buf) < 8:
                out[-1] = out[-1] + "，" + buf
            else:
                out.append(buf)
    return out


def classify_clause(c):
    """一个片段是什么：创作要求 / 背景事实 / 人物属性 / 事件。"""
    t = str(c or "")
    if _ASK_RE.search(t):
        return "创作要求"
    # 括号里的性格先剥掉再判——「认出女儿（调皮捣蛋又敏感）」是事件，不是人物属性
    body = re.sub(r"[（(][^）)]*[）)]", "", t)
    if _TRAIT_RE.search(body) and not _has_action(body):
        return "人物属性"
    if _LIMIT_RE.search(body):
        return "限制"
    # 时间前缀（十年前/当年/一直没）＝这是交代过去，不是这部戏里要演的事。
    # 背景句里当然也有动词（「十年前下山嫁人」），所以这里不看有没有动作词。
    if _BG_RE.search(body) and not _END_RE.search(body):
        return "背景事实"
    return "事件"


_ACTION_RE = re.compile(
    r"派|去|来|走|找|见|问|说|递|给|拿|捡|带|逃|追|拦|挡|打|抢|夺|骂|喊|哭|笑|"
    r"认出|发现|得知|听说|打听|确认|查|敲|推|开|关|坐|站|蹲|跑|回|上|下|进|出|"
    r"结为|嫁|娶|另娶|辜负|不管|不肯|拒绝|答应|决定|报警|关了|放在|煮|等")


def _has_action(t):
    """这段话里有没有人在做事。没有动作词的多半是在形容人或交代状态。"""
    return bool(_ACTION_RE.search(str(t or "")))


def clause_role(c):
    """这条事件在故事里起什么作用。转变 > 冲突 > 发现 > 过渡。"""
    t = str(c or "")
    role = "发现"
    for name, pat in _ROLE_PATTERNS:
        if pat.search(t):
            role = name
    return role


# ══ 三、读一句话 ══════════════════════════════════════════════

def read_one_line(one):
    """一句话 → 四张表。全确定性，不调模型。

    返回 {"facts": [...], "must": [{"text","role","is_end"}...],
          "asks": [...], "traits": [...], "gaps": [...]}
    """
    one = str(one or "").strip()
    facts, must, asks, traits, limits = [], [], [], [], []
    for c in split_clauses(one):
        kind = classify_clause(c)
        if kind == "创作要求":
            asks.append(c)
        elif kind == "人物属性":
            traits.append(c)
        elif kind == "限制":
            limits.append(c)                      # 障碍，不占话
        elif kind == "背景事实":
            facts.append(c)
        else:
            must.append({"text": c, "role": clause_role(c), "is_end": bool(_END_RE.search(c))})
    must = _merge_adjacent(must)
    # 括号里的性格说明单独摘出来当属性（「女儿（调皮捣蛋又敏感）」）
    for m in re.findall(r"[（(]([^）)]{2,20})[）)]", one):
        if _TRAIT_RE.search(m) and m not in traits:
            traits.append(m)
    return {"facts": facts, "must": must, "asks": asks, "traits": traits, "limits": limits,
            "gaps": find_gaps(must + [{"text": x} for x in facts + limits])}


def _merge_adjacent(must):
    """相邻两条同作用、又说的是同一拨人同一件事 → 并成一条。
    「找到赵铁柱」和「对方已另娶不管女儿」是同一场戏的两句，不该各占一话。"""
    out = []
    for it in must:
        if out and out[-1]["role"] == it["role"] and not out[-1]["is_end"] and not it["is_end"]:
            a = set(re.findall(r"[一-龥]{2,4}", out[-1]["text"]))
            b = set(re.findall(r"[一-龥]{2,4}", it["text"]))
            if a & b or re.match(r"^(对方|他|她|那|这|随后|接着|然后)", it["text"]):
                out[-1] = {"text": out[-1]["text"] + "，" + it["text"],
                           "role": it["role"], "is_end": False}
                continue
        out.append(dict(it))
    return out


def find_gaps(items):
    """按句式找出必须补的连接。对任何故事都用同一套判据。"""
    gaps, seen = [], set()
    for it in items:
        t = str((it or {}).get("text") or "")
        for pat, tpl in _GAP_RULES:
            m = pat.search(t)
            if not m:
                continue
            key = (tpl, m.group(0))
            if key in seen:
                continue
            seen.add(key)
            frag = t if len(t) <= 22 else (t[:20] + "…")
            gaps.append(tpl % ("「%s」" % frag))
    return gaps


# ══ 四、按内容定话数 ══════════════════════════════════════════

# 每种作用要占多少话。**过渡不占话**——领命、赶路、进门压进相邻那一话的开头。
_ROLE_WEIGHT = {"过渡": 0, "发现": 1, "冲突": 1, "转变": 2, "结局": 1}


def plan_shape(one, min_ep=1, max_ep=12):
    """一句话 → 这个故事的形状：话数 + 每一话为什么存在。

    话数从**内容的作用**推，不看一句话有多少字：
      · 过渡（领命、赶路、进门）不占话，压进相邻那一话的开头
      · 发现、冲突各占一话
      · 转变占两话——关系和主意的改变必须有过程，一话写不出积累
      · 结局占一话；用户要求里有"日常/相处"这类，再加一话放在最后
    """
    r = read_one_line(one)
    rows, n = [], 0
    for it in r["must"]:
        role = it["role"]
        if it["is_end"]:
            # 结局如果本身就是一次关系转变（情同父女、终于原谅），要两话：兑现 + 落地
            role = "结局+转变" if role == "转变" else "结局"
        w = 2 if role == "结局+转变" else _ROLE_WEIGHT.get(role, 1)
        rows.append({"text": it["text"], "role": role, "weight": w})
        n += w
    # 关系怎么变来的、拒绝在怕什么——这些过程本身要用戏演出来，各加一话
    _proc = [g for g in r["gaps"] if g.startswith("靠什么走到") or "是在怕什么" in g]
    n += len(_proc)
    extra = 0
    if any(re.search(r"日常|相处|生活|平淡|温馨", a) for a in r["asks"]):
        extra = 1                      # 用户点名要的日常戏，单独给一话，按要求放最后
    n = max(min_ep, min(max_ep, n + extra))
    r["process_eps"] = _proc
    r["episodes"] = n
    r["weights"] = rows
    r["extra_daily"] = extra
    r["why"] = _shape_reason(rows, extra, n, _proc)
    return r


def _shape_reason(rows, extra, n, proc=()):
    cnt = {}
    for x in rows:
        cnt[x["role"]] = cnt.get(x["role"], 0) + 1
    bits = ["%s%d 条" % (k, v) for k, v in cnt.items()]
    tail = "；用户点名要日常相处，最后加 1 话" if extra else ""
    zero = [x["text"][:12] for x in rows if x["weight"] == 0]
    z = ("；过渡不占话（%s）" % "、".join(zero[:3])) if zero else ""
    p = ("；关系过程要演出来，加 %d 话" % len(proc)) if proc else ""
    return "按内容定 %d 话：%s（转变按 2 话给，其余 1 话）%s%s%s" % (
        n, "，".join(bits), z, p, tail)


# ══ 五、话位表：第几话落什么，代码说了算 ══════════════════════

def plan_slots(one, min_ep=1, max_ep=12):
    """一句话 → 每一话的格子。模型只往格子里填，不决定顺序和分配。

    为什么要定死：实测把骨架当参考塞给模型，它照旧按自己的顺序铺，
    前面过渡铺满、结局没地方放（天云门 8 话里「情同父女」一个字没有）。

    每格给三样东西：
      · anchor  这一话必须落的那条剧情点（原词，用来做覆盖检查）
      · role    这一话起什么作用
      · done    这一话演完，什么必须已经发生了
    """
    sh = plan_shape(one, min_ep=min_ep, max_ep=max_ep)
    slots = []
    trans = [g for g in sh["gaps"] if "是在怕什么" in g]
    grow = [g for g in sh["gaps"] if g.startswith("靠什么走到")]
    for it in sh["weights"]:
        if not it["weight"]:
            continue
        if it["role"] == "结局+转变":
            # 关系怎么变来的（过程）先插在结局前面，结局本身占最后两格
            for g in grow:
                slots.append({"anchor": "", "role": "转变过程",
                              "done": _strip_gap(g) + "——要用戏演出来，不能一句话交代"})
            grow = []
            slots.append({"anchor": "", "role": "兑现", "end_of": it["text"],
                          "done": ("这一话必须有**一件具体的事**把这条结局定下来：" + it["text"]
                                   + "。要么有人来抢/来拦、他挡住了，要么她自己做了一个选择、"
                                     "交出了一样东西。**不许用日常相处代替**——日常是结局之后的事，"
                                     "不是结局本身。这一话的节奏不许写日常。")})
            slots.append({"anchor": "", "role": "落地", "end_of": it["text"],
                          "done": ("上一话定下来的关系，在这一话里对外**兑现一次**："
                                   "该交代的人交代掉、该回应的规矩回应掉（原来挡路的那条限制到这里必须有下文）。"
                                   "不总结、不抒情。这一话的节奏不许写日常。")})
            continue
        slots.append({"anchor": it["text"], "role": it["role"],
                      "done": "这一话结束时，这件事已经发生了：" + it["text"]})
        # 拒绝在怕什么：紧跟在那条冲突后面演
        if it["role"] == "冲突" and trans:
            for g in trans:
                slots.append({"anchor": "", "role": "受挫过程",
                              "done": _strip_gap(g) + "——一次尝试带来一个新认识，不许反复原地打转"})
            trans = []
    for g in trans + grow:            # 没挂上的过程格，补在结局前
        slots.insert(max(0, len(slots) - 1),
                     {"anchor": "", "role": "过程", "done": _strip_gap(g)})
    if sh.get("extra_daily"):
        slots.append({"anchor": "", "role": "日常",
                      "done": ("用户点名要的日常相处，**只有这一话是日常**，放在最后："
                               "写一件前面没演过的小事（一顿饭、一次赶集、一件针线活），"
                               "靠它让人更懂这两个人。**不许重复前面任何一话演过的事，"
                               "也不许把剧情点原话抄一遍**——前面的事已经发生完了。")})
    # 限制挂到词面最贴的那一格，让它在戏里真的挡一次人
    for lim in sh.get("limits") or []:
        best, hit = None, -1
        wl = _bigrams(lim)
        for sl in slots:
            if sl.get("limit"):
                continue                      # 一格最多挂一条，别都堆在第一格
            n = len(wl & _bigrams(sl.get("anchor") or ""))
            if n > hit:
                best, hit = sl, n
        if best is not None:
            best["limit"] = lim
    slots = slots[:max_ep]
    sh["slots"] = slots
    sh["episodes"] = len(slots)
    return sh


# 每一格该是什么节奏——由作用推得出来，不该问模型（实测 8 话里 4 话乱标日常）
_ROLE_PACE = {"发现": "推进", "冲突": "高潮", "受挫过程": "推进", "转变过程": "推进",
              "兑现": "高潮", "落地": "推进", "过程": "推进", "日常": "日常",
              "过渡": "推进", "结局": "推进"}


def slot_pace(role):
    return _ROLE_PACE.get(str(role or ""), "推进")


def _bigrams(t):
    """所有相邻两字。按固定宽度切词两边对不齐（"宗门也不" vs "宗门"），滑窗才对得上。"""
    t = re.sub(r"[^一-龥]", "", str(t or ""))
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _strip_gap(g):
    return str(g or "").split("——")[0].strip()
