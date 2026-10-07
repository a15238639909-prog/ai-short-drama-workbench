# -*- coding: utf-8 -*-
"""oral_story.py — 口述剧情模式（P366，2026-09-17，用户定）。

两件事：
① 文字层：用户口述的一话 → 事件卡（程序断句编号）→ 写手按事件卡扩写 → 程序核对
   （事件齐不齐 / 顺序 / 多出来的数字 / 多出来的说话人物 / 越过本话结尾）→ 一次定向修订 → 越界段砍掉。
② 视频层：剧本 → 节拍表（一拍＝一个人做一件事 + 对方一个反应 + ≤1 句台词）→ 一拍一段。
   程序补三道规矩：0 秒有动作（拍的正文就是动作句）、背/扶/抱/摔/翻身拆成两拍、纯描写不成拍。

模型只服从可数的正面指标（见记忆 qwen-prompt-what-works），所以这里的守卫全是程序做的；模型只做两件窄事：
写正文、把事件映射到段落号；节拍表是模型出、程序验。
"""
import json
import re

# ═══════════════ ① 事件卡 ═══════════════
_SENT_SPLIT = re.compile(r"(?<=[。！？；])")
_HEAD_WORDS = re.compile(r"^(然后|接着|之后|后来|于是|最后|这时候|这时|但是|但|而|并且|就是说|就是|对|嗯|啊|然后的话|然后就是)+[，,]?")
_PER_EVENT = {"紧凑": 180, "适中": 260, "舒缓": 380}


def event_card(brief):
    """口述 → [{"n", "text"}]。只断句、去口头语、合并过短的碎句，一个字不改写。"""
    raw = str(brief or "").replace("\r", "").replace("\n", "。")
    parts = [p.strip() for p in _SENT_SPLIT.split(raw) if p.strip()]
    events, buf = [], ""
    for p in parts:
        p = _HEAD_WORDS.sub("", p).strip("，,。；！？ ")                     # P417b：首尾不带标点
        if not p:
            continue
        buf = (buf + "，" + p) if buf else p
        if len(re.sub(r"[^一-龥]", "", buf)) >= 8:
            events.append(buf)
            buf = ""
    if buf:
        if events:
            events[-1] += "，" + buf
        else:
            events.append(buf)
    if len(events) < 3 and "，" in raw:
        # P413：一长句逗号连到底（「…遇到海难，几个人漂流到荒岛，男人带着三个女孩求生，渐渐生情」）→ 按逗号再分，不足 8 字的碎片（人名）并回前一件
        ev2, buf = [], ""
        for p in [x.strip() for x in re.split(r"[，,]", "".join(e.rstrip("。") + "。" for e in events)) if x.strip()]:
            p = _HEAD_WORDS.sub("", p).strip("，,。；！？ ")                  # P417：句号后的碎片首尾不带标点
            if not p:
                continue
            _tail = re.sub(r"[^一-龥]", "", ev2[-1].split("，")[-1]) if ev2 else ""
            if ev2 and (len(re.sub(r"[^一-龥]", "", p)) < 8 or len(re.sub(r"[^一-龥]", "", ev2[-1])) < 8 or len(_tail) <= 3):
                ev2[-1] = ev2[-1] + "，" + p
            else:
                ev2.append(p)
        if len(ev2) > len(events):
            events = [e.strip("。；！？，, ") for e in ev2]
    return [{"n": i + 1, "text": t} for i, t in enumerate(events)]


# ═══════════════ ①a 口述原话（P455①） ═══════════════
# 「就碰了一下男主，说。喂，你是我们的俘虏，你怎么擅自出来了？」「男主就很疑惑，就是我怎么就被俘虏了？」
# 「她就跟男主说，你擅自闯入了我们的村庄。按道理是要严罚你，但是…先观察你一下。」——用户自己写的台词，写手不许改短。
_QUOTE_LEAD = re.compile(r"(?:说|问|喊|叫|答|回答|吼|骂|嘀咕|疑惑|质问|嘟囔|讲)(?:了|着|道)?(?:他们|她们|他|她|大家|众人|[一-龥]{2}们)?(?:，|。|：|:|、)?\s*(?:就是|说)?\s*[，,：:。]?\s*")
_QUOTE_STRONG = re.compile(r"(?:说|问|喊|叫|答|回答|讲|质问)(?:了|着|道)?(?:他们|她们|他|她|大家|众人|[一-龥]{2}们)?[，。：:、]")   # 「说，」「问她们，」后面整句都是原话
_NARR_MARK = re.compile(r"^(然后|这个就是|这时|这时候|接着|于是|后来|男主|精灵|他|她|队长|几个|结尾|最后|再)")
_SPEECH_HINT = re.compile(r"你|我|吗|？|\?|喂|吧|呢|啊")


def quoted_lines(brief):
    """口述 → [原话台词]（原文子串、去掉首尾标点）。只认跟在说/问/疑惑这类词后面、带你/我/吗/？的句子；
    后面紧跟的句子只要还带你/我、不以叙述词开头，就并进同一句原话。"""
    raw = str(brief or "").replace("\r", "").replace("\n", "。")
    sents = [p.strip() for p in _SENT_SPLIT.split(raw) if p.strip()]
    out = []
    i = 0
    while i < len(sents):
        s = sents[i]
        m = None
        for mm in _QUOTE_LEAD.finditer(s):
            tail = s[mm.end():].strip("，,。； ")
            if len(re.sub(r"[^一-龥]", "", tail)) >= 4 and (_SPEECH_HINT.search(tail) or _QUOTE_STRONG.match(mm.group(0))) and not _NARR_MARK.match(tail):
                m = (mm, tail)
        j = i + 1
        if not m and re.search(r"(说|问|喊|叫|道|答)[了着]?[。，,：:]?$", s.strip("，,。；！？ ") + "。") and j < len(sents):
            nxt = sents[j].strip("，,。； ")                                   # 「…碰了一下男主，说。」下一句整句是原话
            if len(re.sub(r"[^一-龥]", "", nxt)) >= 4 and _SPEECH_HINT.search(nxt) and not _NARR_MARK.match(nxt):
                m = (None, nxt)
                j += 1
        if not m:
            i += 1
            continue
        q = m[1]
        while j < len(sents):
            nxt = sents[j].strip("，,。； ")
            if nxt and _SPEECH_HINT.search(nxt) and not _NARR_MARK.match(nxt) and not _QUOTE_LEAD.search(nxt) and len(re.sub(r"[^一-龥]", "", nxt)) >= 4:
                q = q.rstrip("！？") + "。" + nxt
                j += 1
            else:
                break
        q = q.strip("，,。； ")
        if q and q.strip("！？") not in [x.strip("！？") for x in out]:
            out.append(q)
        i = j
    return out


def strip_brief_echo_dialogue(prose, brief, quotes=None):
    """P455⑦：台词行的内容是口述里的叙述句（≥10 个汉字连续出现在口述里、又不是原话台词）→ 整行删掉。"""
    nb = re.sub(r"[^一-龥]", "", str(brief or ""))
    if len(nb) < 10:
        return prose
    qn = [re.sub(r"[^一-龥]", "", q) for q in (quotes or [])]
    out = []
    for l in str(prose or "").split("\n"):
        m = _DLG_LINE.match(l.strip())
        if m and not l.startswith("──"):
            t = re.sub(r"[^一-龥]", "", m.group(3))
            if len(t) >= 10 and t[:10] in nb and not any(t[:8] in q or q[:8] in t for q in qn if q):
                continue
        out.append(l)
    return "\n".join(out)


_QUOTE_CACHE = {}


def quoted_lines_qwen(brief, q):
    """P456：千问抽台词（只抄原文）。返回 [原话]，全部经过程序核验：必须是口述原文子串、≥3 字、不是间接引语（有他/她没你/我）。"""
    if q is None or not str(brief or "").strip():
        return []
    sysm = ("下面是用户口述的一段故事。有些地方是人物说出口的话（台词）。你只做一件事：把每一句台词原样抄出来，一行一句。"
            "台词只抄人物说出口的那部分，不抄「他说」「然后」这类叙述；只抄口述里有的字，不许改写、不许补全、不许加人物名。没有台词就输出「无」。")
    try:
        raw = str(q(sysm, str(brief), mt=600, temperature=0.2) or "")
    except Exception:
        return []
    nb = re.sub(r"[^一-龥]", "", str(brief))
    out = []
    for l in raw.splitlines():
        l = re.sub(r"^\d+\s*[=＝：:.、]\s*", "", l.strip()).strip("「」“”\"' ")
        t = re.sub(r"[^一-龥]", "", l)
        if not l or l == "无" or len(t) < 3 or t not in nb:
            continue
        if re.search(r"他|她", l) and not re.search(r"你|我|咱|您", l):
            continue                                                            # 间接引语（观察他一段时间）不当台词
        if re.search(r"我(?:打算|想|要|来)(?:给你们|给大家)?(?:讲|说|写)|这个故事|就是说|男主|女主|主角|结尾的时候|口述", l):
            continue                                                            # 开场白 / 自述不当台词
        out.append(l.strip("。；，, "))
    return out


def quoted_lines_all(brief, q=None):
    """P456：代码 ∪ 千问（同一口述只调一次模型）。"""
    base = quoted_lines(brief)
    if q is None:
        return base
    key = str(brief or "")
    if key not in _QUOTE_CACHE:
        _QUOTE_CACHE[key] = quoted_lines_qwen(brief, q)
    out = list(base)
    for x in _QUOTE_CACHE[key]:
        nx = re.sub(r"[^一-龥]", "", x)
        if not any(nx in re.sub(r"[^一-龥]", "", y) or re.sub(r"[^一-龥]", "", y) in nx for y in out):
            out.append(x)
    return out


_GARMENT_W = re.compile(r"T恤|t恤|内裤|校服|水手服|衬衫|连衣裙|裙|睡衣|睡裙|西装|外套|制服|比基尼|泳装|泳衣|盔甲|铠甲|皮甲|长袍|道袍|袈裟|旗袍|和服|运动服|背心|短裤|牛仔|风衣|毛衣|卫衣|夹克|皮衣|长裙|短裙|丝袜|长袜|围裙|斗篷|披风|军装|警服|护士服|女仆装|婚纱|礼服|汉服|长衫|马甲|裸")
_WEAR_W = re.compile(r"穿|套上|裹着|披着|换上|脱")


def outfit_hints(text, card_names=(), aliases=None, sexes=None):
    """P459：口述/正文里「谁穿什么」的句子 → {卡名: [句子]}。
    穿的人＝穿着词前 14 字内最近的卡名/别名，或 他/她（只有一张男卡/女卡时）；句里一个名字都没有 → 沿用上一句的主语。"""
    out = {}
    sents = [p.strip() for p in re.split(r"(?<=[。！？；\n])", str(text or "")) if p.strip()]
    al = aliases or aliases_of(list(card_names or []))
    sx = sexes or {}
    males = [n for n in (card_names or []) if sx.get(n) == "男"]
    females = [n for n in (card_names or []) if sx.get(n) == "女"]
    keys = sorted((k for k in al if k), key=len, reverse=True)
    carry = ""
    for s in sents:
        names_here = [(m.start(), al[k]) for k in keys for m in re.finditer(re.escape(k), s)]
        subj = max(names_here)[1] if names_here else ""              # 句里最后出现的名字当这句的主语（给下一句沿用）
        if not (_GARMENT_W.search(s) and _WEAR_W.search(s)):
            carry = subj or carry
            continue
        for m in _WEAR_W.finditer(s):
            win = s[max(0, m.start() - 14):m.start()]
            if re.search(r"她们|他们|大家|众人|几名|几个|一群", win) or re.search(r"(面前|眼前|身边|旁边|对面|身后)[^穿]{0,10}$", win):
                continue                                                      # 群体在穿、或「他面前有个穿…的」——不是他穿
            if re.search(r"(那个|一个|一团|一条|一只)[只]?$", win):
                continue                                                      # 「那个只裹着内裤的臀瓣」——穿的是物件不是人
            cand = [(p, al[k]) for k in keys for p in [win.rfind(k)] if p >= 0]
            who = max(cand)[1] if cand else ""
            if not who:
                pre = s[:m.start()]
                pp = max(pre.rfind("她"), pre.rfind("他"))                   # 整句里穿着词前最近的他/她
                if pp >= 0 and not re.search(r"她们|他们", pre[max(0, pp - 1):pp + 2]):
                    who = (females[0] if pre[pp] == "她" and len(females) == 1 else (males[0] if pre[pp] == "他" and len(males) == 1 else ""))
                    if not who:
                        continue                                              # 有代词但对不上唯一的卡 → 不猜
            if not who and not names_here:
                who = carry                                                   # 「这个妹妹叫小爱…」→「在家里喜欢穿T恤和内裤」
            if who:
                out.setdefault(who, [])
                t = s.strip("，,。；！？ ")
                if t not in out[who]:
                    out[who].append(t)
                break
        carry = subj or carry
    return out


def outfit_words(hints):
    """提示句里出现的衣物词（去重）。"""
    ws = []
    for s in hints or []:
        for m in _GARMENT_W.finditer(str(s)):
            if m.group(0) not in ws:
                ws.append(m.group(0))
    return ws


def quote_covered(prose, q):
    """原话 q 有没有写进正文：整句在、或按 6 字窗口有七成命中。"""
    nq = re.sub(r"[^一-龥]", "", str(q or ""))
    np_ = re.sub(r"[^一-龥]", "", str(prose or ""))
    if len(nq) < 4:
        return True
    if nq in np_:
        return True
    win = 6 if len(nq) >= 12 else 4
    hits = sum(1 for k in range(0, len(nq) - win + 1) if nq[k:k + win] in np_)
    return hits >= 0.7 * max(1, len(nq) - win + 1)


# ═══════════════ ①b 事件归并（P454①） ═══════════════
# 用户真实口述是语音转写：开场白（「我打算讲一个故事」）、纯描写（「女人都很高挑」）、重复的话（「醒来…醒来之后」）
# 全被按句号切成一件件"事"，写手每件写一段、导演每件拍 2～3 段 → 项目1 前 7 段全在林子里走、中间 10 段站着看人。
# 修法：句子照旧切，再让模型只回「事N=起-止句号」这种编号分组，程序按编号把原句合并；模型不改一个字，核对报告照旧有效。
_GROUP_LINE = re.compile(r"事\s*(\d+)\s*[=＝：:]\s*(\d+)\s*(?:[-—–~～至到,，、]\s*(\d+))?")
_GROUP_CACHE = {}
_GROUP_MAX = 6          # 一件事最多并 6 句（再多就是模型偷懒全并成一件）


def _group_bounds(raw, n):
    """模型的分组行 → 每件事的起始句号（升序、从 1 起、覆盖到 n）。解析不出返回 []。"""
    starts = set()
    for m in _GROUP_LINE.finditer(str(raw or "")):
        a = int(m.group(2))
        if 1 <= a <= n:
            starts.add(a)
    if not starts:
        return []
    starts.add(1)
    return sorted(starts)


def group_events(events, q, on_step=None):
    """[{"n","text"}] 句子 → [{"n","text","src":[句号]}] 事。q 为空或句子少于 4 句原样返回。"""
    events = list(events or [])
    n = len(events)
    if n < 4 or q is None:
        return events
    key = "\n".join(str(e.get("text") or "") for e in events)
    if key in _GROUP_CACHE:
        return [dict(x) for x in _GROUP_CACHE[key]]
    if on_step:
        on_step("把口述的 %d 句归并成几件事" % n)
    sysm = ("你是编剧助理。下面是用户口述的一话，已按句子编号。你把句子归并成「事」：一件事＝人物做了一件推动故事的动作"
            "（出发、被抓、醒来、解绳开门、被拦住质问、押回房间、围观、训话）。"
            "只描写人物长相、穿着、身材、环境、背景说明的句子，并入它所说明的那件事；"
            "「我打算讲一个故事」这类开场白并入第一件事；意思重复的句子并入前一件事。\n"
            "每件事一行，格式：事N=起始句号-结束句号。句子必须连续、按原顺序、每句只属于一件事、一句不落。只输出这些行。")
    user = "\n".join("%d. %s" % (i + 1, str(e.get("text") or "")) for i, e in enumerate(events))
    try:
        raw = str(q(sysm, user, mt=600, temperature=0.2) or "")
    except Exception:
        return events
    starts = _group_bounds(raw, n)
    if len(starts) < 2 or len(starts) >= n:
        return events                                     # 没分出组、或每句一件 → 照旧
    groups = []
    for i, a in enumerate(starts):
        b = starts[i + 1] - 1 if i + 1 < len(starts) else n
        idx = list(range(a, b + 1))
        while len(idx) > _GROUP_MAX:                      # 太大的组对半拆
            groups.append(idx[:len(idx) // 2])
            idx = idx[len(idx) // 2:]
        groups.append(idx)
    out = []
    for g in groups:
        txt = "。".join(str(events[i - 1].get("text") or "").strip("。；！？ ") for i in g)
        out.append({"n": len(out) + 1, "text": txt, "src": g})
    _GROUP_CACHE[key] = [dict(x) for x in out]
    return out


def events_of(brief, q=None, on_step=None):
    """口述 → 事件卡（切句 + 归并）。整条链只认这一个入口（写手表、写手篇幅、核对、导演分段都从这里拿）。"""
    return group_events(event_card(brief), q, on_step)


def plan_episode(brief, q, settings=None, previous=None, on_step=None):
    """One bounded planning call: separate design constraints from causal story events."""
    settings = settings or {}
    if on_step:
        on_step("区分人物场景要求，规划本话的因果事件")
    instruction = (
        '你是短剧编剧，先整理用户的一句话或口述，再规划这一话。只输出JSON：'
        '{"mode":"idea或outline","context":["人物、场景、画风等要求"],'
        '"events":[{"text":"谁采取什么行动，遇到什么，并造成什么结果",'
        '"cause":"承接前一件事的具体原因","change":"这件事新增的线索、处境或决定"}]}。\n'
        '先区分：标题、题材、年龄、外貌、服装、画风、拍摄要求属于context；events只写真正发生的行动与结果。'
        '混在同一句里的身份和剧情分别整理，人物与场景要求完整保留。\n'
        '用户只给设想、目标或开场时用idea：围绕同一个目标发展较完整的因果事件链，通常6～9件作为参考，实际按故事需要，'
        '写清触发问题、主动尝试、实际阻碍、根据新线索改变办法、付诸行动、结果。'
        '每件约40～80字，变化来自具体行动；内容较长时增加有效进展，而不是多轮询问确认。'
        '新增阻碍服务原来的题材和目标，普通行动故事也可用方法失败、资源不足和协作推进。\n'
        '用户已讲明过程、顺序或结尾，或明确只拍一个动作时用outline：忠实整理原有事件，'
        '关键行为可拆出因果步骤，保持指定的动机、顺序、身份、解法和结局。简单事件可以保持短篇。'
        '原话台词保留在对应事件，字句保持原样。\n'
        '每件事的change写具体的变化，例如“旧路封闭，改走排水渠”，而不是“气氛紧张”。'
        '物件位置、谁拿着什么、已经完成的动作逐件向后承接。'
        '过渡与最后一次确认并入相邻事件；末事件实际完成本话目标，写清异常原因或问题的解决结果；准备明天行动属于中途步骤。'
        '已有角色和上一话结尾是固定事实；用户指定的角色、场景范围优先。'
    )
    request = {"本话口述": str(brief), "项目要求": {k: settings.get(k) for k in
               ("world_type", "style", "extra_requirements", "episode_notes", "_cast_names") if settings.get(k)},
               "上一话结尾": str(settings.get("_ledger_prev") or "") or
               (str((previous or [])[-1].get("原文") or "")[-1200:] if previous else "")}
    obj = _json_obj(q(instruction, json.dumps(request, ensure_ascii=False), mt=2400, temperature=0.35))
    mode, raw_events = obj.get("mode"), obj.get("events")
    if mode not in ("idea", "outline") or not isinstance(raw_events, list) or not raw_events:
        raise ValueError("本话事件规划格式不完整，请重新生成文字；未覆盖已有正文")
    context = obj.get("context")
    if not isinstance(context, list) or any(not isinstance(x, str) for x in context):
        raise ValueError("人物场景要求未正确分离，请重新生成文字")
    events = []
    for item in raw_events:
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip()
                                             for k in ("text", "cause", "change")):
            raise ValueError("事件缺少行动、原因或结果，请重新生成文字")
        if any(_norm_cn(item["text"]) == _norm_cn(e["text"]) for e in events):
            raise ValueError("本话规划出现重复事件，请重新生成文字")
        events.append(dict(item, n=len(events) + 1))
    return {"mode": mode, "context": context, "events": events}


# ═══════════════ ①c 段落中间的台词提行（P454②） ═══════════════
# 项目1：写手把台词写成「她开口，嗓音清脆。女：你是我们的俘虏。女：怎么擅自出来了？她保持着…」——不带引号、不独立成行、
# 说话人写成「女 / 少年 / 精灵 / 队长」。台词编号器只认行首「卡名：」，16 句一句没认出，整话哑剧。
_ROLE_TOKEN = re.compile(r"^(他|她)$|^[一-龥]{0,4}(女|男|男主|女主|主角|男生|女生|少年|少女|女孩|男孩|老者|队长|精灵|女子|男子|首领|师父|师傅|老板|店员|士兵|守卫|村民|"
                         r"女人|男人|孩子|母亲|父亲|妈妈|爸爸|哥哥|姐姐|弟弟|妹妹|长老|掌柜|大叔|大婶|老人|队员|护士|医生|老师|警察|军官|将军|王子|公主|国王|女王)$")
_INLINE_DLG = re.compile(r"(?:(?<=[。！？；，,\n])|^)\s*([一-龥]{1,8})[：:]\s*([^。！？\n]{1,80}[。！？]?)")
_FEM = re.compile(r"^(女|她|女子|女人|女孩|少女|姑娘|女主|女主角|女生)$")
_MAL = re.compile(r"^(男|他|男子|男人|男孩|少年|小伙|男主|男主角|主角|男生)$")


def _speaker_to_card(tok, items, aliases):
    """「女」→ 唯一的女卡；「少年」→ 名字含「少年」的卡或唯一男卡；「精灵」→ 唯一名字含「精灵」的卡；对不上返回原词。"""
    nm = canon(tok, aliases)
    if nm:
        return nm
    hit = [n for n, _s, _a in items if tok in n]
    if len(hit) == 1:
        return hit[0]
    if _FEM.match(tok):
        f = [n for n, sx, _a in items if sx == "女"]
        return f[0] if len(f) == 1 else tok
    if _MAL.match(tok):
        m = [n for n, sx, _a in items if sx == "男"]
        return m[0] if len(m) == 1 else tok
    return tok


_NARR_START = re.compile(r"^(他们|她们|他|她|众人|大家|几名|几个|一名|一个|两人|三人|四人|门|房门|木门|枪|长枪|风|阳光|夜|远处|屋)")


def _cut_dialogue(spk, text, names, aliases):
    """台词文本 → (台词, 叙述尾巴)。台词到第一个句末标点为止；后面以 他/她/人名 开头的是叙述；台词里出现说话人自己的名字 → 从名字前切开。"""
    t = str(text or "").strip()
    keys = sorted({k for k, full in (aliases or {}).items() if full == spk and len(k) >= 2} | {spk, short_name(spk)}, key=len, reverse=True)
    cut = len(t)
    for k in keys:
        p = t.find(k, 1)
        if p > 0:
            cut = min(cut, p)                                                  # 自己的名字出现在台词里 → 那是叙述
    pos = 0
    while pos < cut:
        m = re.search(r"[。！？!?]|…+|\.{3,}", t[pos:cut])
        if not m:
            break
        end = pos + m.end()
        rest = t[end:].lstrip("”」 ")
        if not rest:
            break
        if _NARR_START.match(rest) or canon(rest[:8], aliases):
            cut = min(cut, end)
            break
        pos = end
    if cut >= len(t):
        return t, ""
    return t[:cut].strip("，, "), t[cut:].strip()


def lift_inline_dialogue(prose, chars):
    """段落中间的「女：台词。」提成独立行「卡名：台词。」；台词后面粘的叙述另起一行（P455⑨）。只搬运，不改台词一个字。"""
    items = _char_items(chars)
    names = [n for n, _s, _a in items]
    aliases = card_aliases(chars)
    out_paras = []
    for para in str(prose or "").split("\n"):
        if not para.strip():
            out_paras.append(para)
            continue
        pieces, at = [], 0
        for m in _INLINE_DLG.finditer(para):
            if m.start() < at:
                continue
            tok = m.group(1)
            if not (canon(tok, aliases) or _ROLE_TOKEN.match(tok)):
                continue
            before = para[at:m.start()].strip()
            if before:
                pieces.append(before)
            spk = _speaker_to_card(tok, items, aliases)
            body = para[m.start(2):].strip() if m.start() == 0 else m.group(2).strip()
            say, rest = _cut_dialogue(spk, body, names, aliases)
            pieces.append("%s：%s" % (spk, say))
            if m.start() == 0:
                at = m.start(2) + len(body) - len(rest) if rest else len(para)
                if rest:
                    # 尾巴里可能还有「名：」，交给下一轮匹配；先把纯叙述部分放进去
                    nm2 = _INLINE_DLG.search(rest)
                    if nm2 and (canon(nm2.group(1), aliases) or _ROLE_TOKEN.match(nm2.group(1))):
                        pieces.append(rest[:nm2.start()].strip()) if rest[:nm2.start()].strip() else None
                        at = m.start(2) + len(body) - len(rest) + nm2.start()
                    else:
                        pieces.append(rest)
                        at = len(para)
                continue
            at = m.end() - (len(rest) if rest else 0)
        tail = para[at:].strip()
        if tail:
            pieces.append(tail)
        out_paras.append("\n".join(x for x in pieces if x) if pieces else para)
    return dedupe_dialogue_lines("\n".join(out_paras))


_FILLER_LINE = re.compile(r"^(哦|嗯|啊|哈|哎|唉|嘿|喂|好|好吧|好的|行|行吧|是|是的|对|知道了|嗯嗯|哦哦|哦。|嗯。)$")


def dedupe_dialogue_lines(prose):
    """P454⑭：同一个人同一句台词行只留第一次出现的；P455②：同一句相邻（3 行内）换了说话人 → 只留后一个（写手自我纠正）；
    P455③：单字应答（哦/嗯/好吧）不进剧本；连续多个空行并成一个。"""
    lines = str(prose or "").split("\n")
    recent, drop = [], set()                                   # recent: [(行号, 说话人, 归一句)]
    seen = set()
    for i, l in enumerate(lines):
        m = _DLG_LINE.match(l.strip())
        if not (m and not l.startswith("──")):
            continue
        spk, txt = m.group(1).strip(), re.sub(r"[\s。！？!?，,…]", "", m.group(3))
        if _FILLER_LINE.match(txt) or not re.sub(r"（[^）]*）|\([^)]*\)", "", txt).strip():
            drop.add(i)                                                        # 单字应答 / 括号里只有动作（P456）
            continue
        for (j, s2, t2) in recent[-3:]:
            if t2 == txt and s2 != spk and j not in drop:
                drop.add(j)                                    # 同一句换了人：前一个是写错的
        key = (spk, txt)
        if key in seen:
            drop.add(i)
            continue
        seen.add(key)
        recent.append((i, spk, txt))
    t = "\n".join(l for i, l in enumerate(lines) if i not in drop)
    return re.sub(r"\n{3,}", "\n\n", t)


_SEX_GROUP_F = re.compile(r"女团|女孩|女生|姑娘|少女|女子|女性|女儿|妻子|老婆|女友|女朋友|妹妹|姐姐|母亲|妈妈|阿姨|奶奶|女人|女")
_SEX_GROUP_M = re.compile(r"男团|男孩|男生|小伙|少年|男子|男性|儿子|丈夫|老公|男友|男朋友|弟弟|哥哥|父亲|爸爸|叔叔|爷爷|男人|军人|退伍兵|男")


def fix_names_from_brief(brief, names):
    """P418：卡名不在口述里，口述里却有同样几个字换了顺序的名字（林小 ↔ 小林）→ 返回 {卡名: 口述名}。"""
    text = str(brief or "")
    out = {}
    for n in [x for x in (names or []) if x]:
        if n in text or len(n) < 2 or len(n) > 3:
            continue
        key = "".join(sorted(n))
        for i in range(len(text) - len(n) + 1):
            cand = text[i:i + len(n)]
            if cand != n and "".join(sorted(cand)) == key and re.fullmatch(r"[一-龥]+", cand):
                out[n] = cand
                break
    return out


def sex_from_brief(brief, names):
    """P413：按口述里名字前面的群体词定性别。「几个女团成员，小林，小爱，小可」→ 三个都是女；「少年张明」→ 男。
    名字前 12 字内最近的性别词算数；名字之间只隔标点 / 和 / 与 的，沿用前一个名字的性别。返回 {名字: 男/女}。"""
    text = str(brief or "")
    out = {}
    last_pos, last_sex = -100, ""
    for n in sorted([x for x in (names or []) if x], key=lambda x: text.find(x) if x in text else 10 ** 6):
        i = text.find(n)
        if i < 0:
            continue
        gap = text[last_pos:i] if last_pos >= 0 else ""
        if last_sex and last_pos >= 0 and re.fullmatch(r"[\s，,、和与及跟]*", gap):
            out[n] = last_sex
        else:
            win = text[max(0, i - 12):i]
            mf, mm = [m.end() for m in _SEX_GROUP_F.finditer(win)], [m.end() for m in _SEX_GROUP_M.finditer(win)]
            sx = ""
            if mf or mm:
                sx = "女" if max(mf or [-1]) > max(mm or [-1]) else "男"
            if sx:
                out[n] = sx
        if n in out:
            last_sex = out[n]
        else:
            last_sex = ""
        last_pos = i + len(n)
    return out


_PACE_TELL = {
    "舒缓": "给关键交流、观察与选择留空间；人物说话时继续手里的事，回应带来新的信息或决定。过渡简洁，口述指定的快慢优先",
    "紧凑": "压缩等待、赶路与重复反应，行动和对白接着推进；用短问答交代目的、线索和行动理由，重要交流完整保留。人物边行动边说话，口述指定的快慢优先",
}


def words_range(prose_words):
    """「3000～4000」「3000-4000」「3500」→ (下限, 上限)；解析不出返回 (0, 0)。"""
    nums = [int(x) for x in re.findall(r"\d{3,5}", str(prose_words or ""))]
    if not nums:
        return 0, 0
    return (nums[0], nums[-1]) if len(nums) >= 2 else (nums[0], nums[0])


def writer_extras(events, pace="适中", prose_words=None, quotes=None):
    """篇幅是整话参考，按因果的重要程度分配，事件数量不再乘固定字数。"""
    n = max(1, len(events or []))
    _lo, _hi = words_range(prose_words)
    if not _lo:
        _lo, _hi = 1200, 1800
    _tell = _PACE_TELL.get(str(pace or ""), "")
    return dict(({"讲法（本话节奏：%s）" % pace: _tell} if _tell else {}), **{
        "本话事件卡（按编号顺序各写成 1～3 段；每一件都要写出现场过程，一件不少、顺序不变）":
            {str(e["n"]): e["text"] for e in (events or [])},
        "本话止于": "事件 %d 写完就结束，之后的事一句不写" % n,
        "现场写法": "事件卡作为因果提纲；正文写人物正在采取的行动、必要对白和实际后果。人物身份和场景设定随行动带出",
        "篇幅分配": "关键尝试、阻碍、选择和结果展开，赶路、收拾、点头和确认简写。每件事按内容分配篇幅，一次事件结束便进入其造成的下一步",
        "事件因果与落点": [{"事件": e["n"], "原因": e.get("cause", ""), "实际变化": e.get("change", "")}
                         for e in (events or []) if e.get("change")],
        "允许补充的四类": "对白、动作、周围环境、表情反应。人物只有事件卡里出现的（可以给他们取名字）；路人不说话、不取名",
        "对白的写法": "每个发言单独占一行，写成「名字：台词」，名字用人物表里的名字；对白安排在对应行动发生时，提示、决定在执行之前，确认在结果之后。一次交流与连续动作放在同一个自然段，事件进展后再空行换段",
        "有效对白": "把关键互动写成问答、提醒、分歧或决定，让观众听懂人物想做什么、依据是什么、下一步怎么做。新增台词通常每句8～24字，条件和原因说完整；原话照原样保留。台词传达判断，画面展示依据和行动，二者提供互补信息；纯动作和独处保留自然的无对白段落，用户明确的对白要求优先",
        **({"口述原话台词（一字不改、不缩短，原样写进对白）": list(quotes)} if quotes else {}),                                                        # P455①
        "数字的写法": "事件卡没给的银两数、年限、天数、招式名、门规一律不写具体数，用「一些银钱」「些时日」这类说法",
        "篇幅": "约 %d～%d 字，作为整话参考；以事件发展完整为准，简单剧情可以更短" % (_lo, _hi),
    })


# ═══════════════ ① 程序核对 ═══════════════
_NUM = r"[一二两三四五六七八九十百千万\d]+"
_NUM_UNIT = re.compile(r"(?<![第])(?:[一二三四五六七八九十百千万\d]+|两)\s*(两|文|钱|银|金|年|月|日|天|载|岁|层|阶|招|式|重)")   # 「第二天」序数不算；「两」既是数也是单位，数词里不含它
_SPEAK = re.compile(r"([一-龥]{2,4})(?:说|道|问|答|喊|笑道|低声道|沉声道|开口)")


def paragraphs(prose):
    return [p.strip() for p in re.split(r"\n\s*\n", str(prose or "")) if p.strip()]


def _json_obj(text):
    m = re.search(r"\{.*\}", str(text or ""), re.S)
    if not m:
        return {}
    s = m.group(0)
    try:
        return json.loads(s)
    except Exception:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", s))
        except Exception:
            return {}


def _map_events(paras, events, q):
    """窄任务：每个事件落在哪些段；出场的有名字的人物；本话最后一件事写完之后还有哪些段。"""
    sysm = ("你是核对编辑。给你【事件卡】和【编号段落】，只输出 JSON，不解释：\n"
            "{\"map\": {\"事件号\": [段号, ...]}, \"people\": [{\"name\": \"正文里的名字\", \"role\": \"他对应事件卡里的哪个人（少年/老者/…），对应不上写 其他\"}],"
            " \"beyond\": [最后一个事件已经写完之后仍在继续讲新事情的段号]}\n"
            "map 里每个事件号都要有；某事件正文里没写就给空数组。段号只用给出的编号。")
    user = "【事件卡】\n" + "\n".join("%d. %s" % (e["n"], e["text"]) for e in events) + \
           "\n\n【编号段落】\n" + "\n".join("[%d] %s" % (i + 1, p) for i, p in enumerate(paras))
    try:
        obj = _json_obj(q(sysm, user, mt=1200, temperature=0.1))
    except Exception:
        obj = {}
    mp = {}
    for k, v in (obj.get("map") or {}).items():
        try:
            mp[int(k)] = sorted({int(x) for x in (v or []) if 1 <= int(x) <= len(paras)})
        except Exception:
            mp[int(k)] = []
    people = [{"name": str(p.get("name") or ""), "role": str(p.get("role") or "")} for p in (obj.get("people") or []) if isinstance(p, dict)]
    beyond = []
    for x in obj.get("beyond") or []:
        try:
            if 1 <= int(x) <= len(paras):
                beyond.append(int(x))
        except Exception:
            pass
    return mp, people, sorted(set(beyond))


def check_prose(prose, events, brief, mapping, people, beyond, quotes=None):
    paras = paragraphs(prose)
    rep = {"missing": [], "order_ok": True, "numbers": [], "extra_people": [], "beyond": beyond, "map": mapping}
    first = []
    for e in events:
        ps = mapping.get(e["n"]) or []
        if not ps:
            rep["missing"].append(e["n"])
        else:
            first.append((e["n"], ps[0]))
    rep["order_ok"] = all(first[i][1] <= first[i + 1][1] for i in range(len(first) - 1))
    for m in _NUM_UNIT.finditer(prose):
        tok = m.group(0)
        if tok not in str(brief or "") and tok not in rep["numbers"]:
            rep["numbers"].append(tok)
    roles_txt = " ".join(e["text"] for e in events)
    for p in people:
        nm, role = p["name"], p["role"]
        if not nm or nm not in prose:
            continue
        if nm in roles_txt or nm in str(brief or "") or (re.sub(r"[男女]", "", nm) in re.sub(r"[男女]", "", roles_txt + str(brief or ""))):
            continue                                                       # P428：口述里点了名的人不算多出来的（艾登曾被改成路人）；P454：小个子女精灵≈小个子精灵
        if role == "其他" or (role and role not in roles_txt):
            if _SPEAK.search(prose) and re.search(re.escape(nm) + r"(?:说|道|问|答|喊|笑道|低声道|沉声道|开口|：)", prose):
                rep["extra_people"].append(nm)
    rep["missing_quotes"] = [q for q in (quotes or []) if not quote_covered(prose, q)]           # P455①
    rep["verdict"] = "pass" if not (rep["missing"] or rep["numbers"] or rep["extra_people"] or rep["beyond"] or not rep["order_ok"] or rep["missing_quotes"]) else "fail"
    return rep


def _repair(prose, events, rep, q):
    """一次定向修订：只改清单里的段，其余一字不动。返回新正文（改不动就原样）。"""
    paras = paragraphs(prose)
    issues = []
    for n in rep["missing"]:
        e = next(x for x in events if x["n"] == n)
        issues.append("事件 %d 没写：「%s」——在它前后事件之间补写出现场过程（可以扩展相邻的已有段，或新增一段）" % (n, e["text"]))
    if rep["numbers"]:
        issues.append("去掉这些事件卡没给的具体数字，改成模糊说法：" + "、".join(rep["numbers"]))
    if rep["extra_people"]:
        issues.append("这些人物事件卡里没有：" + "、".join(rep["extra_people"]) + "——删掉他们的台词，最多留作不说话、不取名的路人")
    if not rep["order_ok"]:
        issues.append("事件顺序和事件卡不一致，调整段落顺序使其一致")
    for _q in rep.get("missing_quotes") or []:
        issues.append("口述里这句原话没写进对白：「%s」——在它所属的那件事里，作为一行「名字：台词」原样写进去（一个字不改），把你替代它写的那句短台词删掉" % _q)
    if not issues:
        return prose
    sysm = ("你是小说修订编辑。只按【问题清单】改，只改涉及的段，其他段一字不动、不润色、不缩短。"
            "只输出 JSON：{\"patches\": [{\"paragraph_id\": 段号, \"text\": \"改后的这一段完整正文\"}], "
            "\"insert_after\": [{\"paragraph_id\": 段号, \"text\": \"要在这一段之后新增的整段\"}]}。段号只用给出的编号。")
    user = ("【事件卡】\n" + "\n".join("%d. %s" % (e["n"], e["text"]) for e in events) +
            "\n\n【问题清单】\n" + "\n".join("- " + x for x in issues) +
            "\n\n【编号段落】\n" + "\n".join("[%d] %s" % (i + 1, p) for i, p in enumerate(paras)))
    try:
        obj = _json_obj(q(sysm, user, mt=4000, temperature=0.4))
    except Exception:
        return prose
    if not obj:
        return prose
    # 模型偶尔把4个输入段重编号成8段。部分应用会丢掉后半故事，整批拒绝错误编号。
    patches = obj.get("patches") or []
    inserts = obj.get("insert_after") or []
    try:
        ids = [int(it["paragraph_id"]) for it in patches]
        if len(ids) != len(set(ids)) or any(not 1 <= k <= len(paras) for k in ids):
            return prose
        if any(not 0 <= int(it["paragraph_id"]) <= len(paras) for it in inserts):
            return prose
    except (KeyError, TypeError, ValueError):
        return prose
    new = list(paras)
    for it in obj.get("patches") or []:
        try:
            k = int(it.get("paragraph_id")); t = str(it.get("text") or "").strip()
        except Exception:
            continue
        if 1 <= k <= len(new) and len(t) > 10:
            new[k - 1] = t
    ins = []
    for it in obj.get("insert_after") or []:
        try:
            k = int(it.get("paragraph_id")); t = str(it.get("text") or "").strip()
        except Exception:
            continue
        if 0 <= k <= len(new) and len(t) > 10:
            ins.append((k, t))
    for k, t in sorted(ins, key=lambda x: -x[0]):
        new.insert(k, t)
    return "\n\n".join(new)


_ENDING_MARK = re.compile(r"日子一天天过去|从此以后|从此，|故事，?在这一刻|画上句号|新的生活|明天太阳升起|多年以后|后来的日子|岁月静好|他们的故事|再见，?荒岛|一切归于")


_TABLE_KEY = "每件事的状态与对白参考（按状态承接行动，用户原话原样保留；其余对白按剧情作用选用，已由动作讲清的信息简写；一次事件只演一遍）"


def status_dialogue_table(events, q, quotes=None):
    """每件事的状态与必要对白，一次模型调用；对白数量由信息作用决定。
    P455①：口述里的原话台词一并给，要求原样放进对白。"""
    lines = [str(e.get("text") if isinstance(e, dict) else e).strip() for e in (events or [])]
    lines = [l for l in lines if l]
    if not lines or q is None:
        return ""
    sysm = ("你是编剧。给下面每件事写两样东西，只按给的事写，不加人物、不加事件、不写结局之外的事。每件事的格式：\n"
            "【事件N】原句\n【状态】这件事发生时人物之间怎么称呼、关系到哪一步、这件事由谁动手（比如：三个女孩还没接受他，只叫「你」「喂」，不喊张哥；拜师前不叫师父；推门的是莉娅）\n"
            "对白：先确定这件事的变化，再写需要说出来的试探、理由、分歧或决定。通常关键交流2～4句，动作和过渡0～1句，具体数量按信息需要；连续争执或长解释按用户内容保留。每行「名字：台词」，通常8～24字，原因和条件说完整。"
            "只用事件里的人物，称呼符合【状态】，知识来自此时已经发生或看到的事。每轮推进目的、证据、分歧、选择中的一项，回应承接上一句；人物一边做事一边交流。"
            "对白按原文动作顺序安排：看到线索以后辨认，听到声音以后回应，确认依据以后作决定；后来才出现的信息留在后面的对白。对白说明判断与意图，动作展现证据与结果。原文明确要求安静时按原文。\n只输出这些。")
    if quotes:
        sysm += "\n口述里已经写好的原话台词（下面列出）必须原样放进对应那件事的对白里，一个字不改、不缩短、不拆散；其余对白你补。"
    user = "【事件】\n" + "\n".join("%d. %s" % (i, l) for i, l in enumerate(lines, 1))
    if quotes:
        user += "\n\n【口述原话台词（对应事件中使用，不另算事件）】\n" + "\n".join("「%s」" % x for x in quotes)
    try:
        t = str(q(sysm, user, mt=2500, temperature=0.4) or "").strip()
    except Exception:
        return ""
    return t if "【状态】" in t or "对白" in t else ""


def _norm_cn(t):
    return re.sub(r"[^一-龥0-9A-Za-z]", "", str(t or ""))


def strip_echo_paragraphs(prose, events):
    """P416：正文里整段照抄事件卡原句的段（项目222 前六段＝口述六句）删掉——那不是写出来的现场过程，留着核对会当作事件已写。
    判法：段落去标点后等于某条事件原文，或包含事件原文且不比它长三成。返回 (新正文, 删掉的段号)。"""
    paras = paragraphs(prose)
    evs = [_norm_cn(e.get("text")) for e in (events or []) if _norm_cn(e.get("text"))]
    keep, dropped = [], []
    for i, p in enumerate(paras, 1):
        np_ = _norm_cn(p)
        echo = any(ev and (np_ == ev or (ev in np_ and len(np_) <= len(ev) * 1.3 + 6)) for ev in evs)
        if echo and len(paras) > 1:
            dropped.append(i)
        else:
            keep.append(p)
    return ("\n\n".join(keep) if keep else prose), dropped


def fidelity_pass(prose, events, q, brief="", on_step=None, rounds=1, preserve_story=False):
    """核对 + 修 + 砍越界。返回 (正文, 报告)。永远返回一份正文，不打回。"""
    brief = brief or " ".join(e["text"] for e in events)
    quotes = quoted_lines_all(brief, q)                                         # P455①；P456：代码 ∪ 千问
    prose = strip_brief_echo_dialogue(prose, brief, quotes)                    # P455⑦：口述叙述句被写成台词 → 删
    if on_step:
        on_step("按事件卡核对正文")
    prose, _echo = strip_echo_paragraphs(prose, events)                     # P416：抄卡段先删，缺的事件走定向补写
    if _echo and on_step:
        on_step("正文里 %d 段是照抄的事件卡原句，删掉后补写" % len(_echo))
    paras = paragraphs(prose)
    mp, people, beyond = _map_events(paras, events, q)
    rep = check_prose(prose, events, brief, mp, people, beyond, quotes)
    rep["repaired"] = False
    if rep["verdict"] == "fail" and (rep["missing"] or rep["numbers"] or rep["extra_people"] or not rep["order_ok"] or rep.get("missing_quotes")):
        if on_step:
            on_step("定向修订：" + "；".join(
                (["缺事件 %s" % rep["missing"]] if rep["missing"] else []) +
                (["数字 %s" % "、".join(rep["numbers"][:4])] if rep["numbers"] else []) +
                (["多出的人 %s" % "、".join(rep["extra_people"][:3])] if rep["extra_people"] else [])))
        prose2 = _repair(prose, events, rep, q)
        if prose2 != prose:
            prose = prose2
            rep["repaired"] = True
            paras = paragraphs(prose)
            mp, people, beyond = _map_events(paras, events, q)
            rep2 = check_prose(prose, events, brief, mp, people, beyond, quotes)
            rep2["repaired"] = True
            rep = rep2
    # 越界：最后一个事件写完之后还在讲新事的段，砍掉（程序做，不靠模型自觉）
    cut = []
    last_n = max(e["n"] for e in events) if events else 0
    last_ps = mp.get(last_n) or []
    # P421：收尾式蒙太奇（「日子一天天过去」「故事画上句号」「新的生活」）从标志句起整段砍掉——写手把最后一件事写完还要写以后
    _first_last = min(last_ps) if last_ps else 0
    _mark = next((i for i, p in enumerate(paras, 1) if i > _first_last and _ENDING_MARK.search(p)), 0)
    if _mark and not preserve_story:
        paras = paras[:_mark - 1]
        prose = "\n\n".join(paras)
        rep["ending_cut_from"] = _mark
        mp = {k: [x for x in v if x < _mark] for k, v in mp.items()}
        last_ps = mp.get(last_n) or []
    if last_ps and not preserve_story:
        last_par = max(last_ps)
        cut = [i for i in range(last_par + 1, len(paras) + 1) if i in set(rep.get("beyond") or [])]
        # beyond 没报但明显在最后事件之后还有很多段 → 也砍（保留最后事件所在段及其后一段作收尾）
        tail_extra = [i for i in range(last_par + 2, len(paras) + 1)]
        cut = sorted(set(cut) | set(tail_extra))
    if cut:
        paras = [p for i, p in enumerate(paras, 1) if i not in set(cut)]
        prose = "\n\n".join(paras)
        rep["cut_paragraphs"] = cut
        rep["beyond"] = []
    rep["verdict"] = "pass" if not (rep["missing"] or rep["numbers"] or rep["extra_people"] or not rep["order_ok"]) else "fail"
    rep["events"] = events
    rep["quotes"] = quotes
    return prose, rep


# ═══════════════ ② 节拍表 ═══════════════
_HARD = {
    "背": ("蹲下身，把{o}的手臂搭上自己的肩，托住{o}的腿弯（还没背起来）", "已经把{o}背在背上，直起身往前走"),
    "扶": ("伸手托住{o}的手臂和后背（还没扶起来）", "已经把{o}扶起来站稳，手仍托着"),
    "抱": ("俯身伸手，双臂穿到{o}身下（还没抱起来）", "已经把{o}抱在怀里，直起身"),
    "摔": ("身子一晃，手往旁边抓（还没倒下）", "已经倒在地上，撑着手要起来"),
    "倒": ("身子一晃，手往旁边抓（还没倒下）", "已经倒在地上，撑着手要起来"),
    "翻身": ("手撑住地面，肩膀转过来（还没翻过身）", "已经翻过身来，仰面躺着"),
    "跳": ("手扶住边缘，身体前倾（还没跳）", "已经落在下面，站稳"),
    "爬": ("手抓住上面的边缘，脚蹬住（还没上去）", "已经爬到上面，站起来"),
    "扛": ("蹲下，肩膀顶住{o}（还没扛起来）", "已经把{o}扛在肩上，直起身"),
}
_HARD_RE = re.compile(r"(背起|扶起|抱起|摔倒|跌倒|倒下|翻身|跳下|跳上|爬上|爬下|扛起)")   # 「背着/扶着」是持续状态，不拆
_PERSON_OBJ = re.compile(r"他|她|老者|老人|少年|孩子|伤者|人|尸")
_BEAT_LINE = re.compile(r"^\s*(?:拍)?\s*(\d+)\s*[｜|]\s*(.*?)\s*[｜|]\s*(.*?)\s*[｜|]\s*(.*?)\s*[｜|]\s*(.*?)\s*[｜|]\s*(.*?)\s*[｜|]\s*(.*?)\s*$")
_DLG_LINE = re.compile(r"^([^：:，。！？\s（）─]{1,12})(（[^）]*）)?[：:]\s*(.+)$")
_SHOTS = ("全景", "中景", "特写")


def short_name(name):
    """「老者（云崖门长辈）」→「老者」。"""
    return re.sub(r"（[^）]*）|\([^)]*\)", "", str(name or "")).strip()


def aliases_of(char_names):
    """每个卡名的别名：全名、去括号短名。返回 {别名: 全名}，长别名优先匹配。"""
    al = {}
    for n in char_names or []:
        al[n] = n
        sn = short_name(n)
        if sn and sn not in al:
            al[sn] = n
    return al


_TYPE_SPLIT = re.compile(r"[/／、，,；;：:（）()\s|｜]+")
_ROLE_TAIL = re.compile(r"^(队长|守卫|首领|老板|店员|士兵|村长|长老|师父|师傅|掌柜|老者|老人|队员|护士|医生|老师|警察|军官|将军|王子|公主|国王|女王|大叔|大婶|哨兵|猎人|侍女|管家|头目|祭司|巫女|族长|酋长)$")
_GENERIC_TYPE = {"人物", "角色", "主角", "配角", "路人", "其他", "男性", "女性", "男", "女", "无", "人类", "人"}


def card_aliases(chars):
    """P454⑨：卡名别名表 {别名: 卡名}。名字、去括号短名之外，再加卡的「人物类型」词（精灵队长 → 瑟琳娜、小个子精灵 → 艾拉）、
    去掉性别字的写法（小个子女精灵）、类型词的尾两字（队长）——只有唯一一张卡符合的才算；再并上按年龄性别的称呼（role_aliases）。"""
    items = _char_items(chars)
    names = [n for n, _s, _a in items]
    al = aliases_of(names)
    words, tails = {}, {}
    for c in chars or []:
        if not isinstance(c, dict):
            continue
        nm = str(c.get("name") or "").strip()
        if not nm:
            continue
        ct = " ".join(str(c.get(k) or "") for k in ("char_type", "identity"))
        for w in _TYPE_SPLIT.split(ct):
            w = w.strip("的 ")
            if len(w) < 2 or w in _GENERIC_TYPE or re.match(r"\d", w):
                continue
            words.setdefault(w, set()).add(nm)
            w2 = re.sub(r"[男女]", "", w)
            if len(w2) >= 2 and w2 != w:
                words.setdefault(w2, set()).add(nm)
            if len(w) >= 4 and _ROLE_TAIL.match(w[-2:]):
                tails.setdefault(w[-2:], set()).add(nm)                       # 精灵队长 → 队长；「精灵」这种种族词不做尾别名
    for nm in names:                                                          # P454⑮：卡名后缀（少年探险家 → 探险家）
        sn = short_name(nm)
        for k in range(1, len(sn) - 2):
            words.setdefault(sn[k:], set()).add(nm)
        sx = next((x for n2, x, _a in items if n2 == nm), "")
        if sx in ("男", "女") and sx not in sn and len(sn) >= 3:
            for k in range(1, len(sn)):                                       # P455⑧：小个子精灵 ↔ 小个子女精灵
                words.setdefault(sn[:k] + sx + sn[k:], set()).add(nm)
        if len(sn) >= 3 and _ROLE_TAIL.match(sn[-2:]):
            tails.setdefault(sn[-2:], set()).add(nm)                            # 精灵队长 → 队长
    for w, who in list(words.items()) + [(w, who) for w, who in tails.items() if w not in _GENERIC_TYPE]:
        if len(who) == 1 and w not in al and not any(w in n for n in names if n != next(iter(who))):
            al[w] = next(iter(who))
    try:
        for w, nm in role_aliases(chars).items():
            al.setdefault(w, nm)
    except Exception:
        pass
    males = [n for n, sx, _a in items if sx == "男"]
    females = [n for n, sx, _a in items if sx == "女"]
    if len(males) == 1:
        for w in ("男主", "男主角"):
            al.setdefault(w, males[0])                                          # 写手爱把主角写成「男主：」
    if len(females) == 1:
        for w in ("女主", "女主角"):
            al.setdefault(w, females[0])
    return al


def canon(name_field, aliases):
    """字段里出现的第一个（最长的）别名 → 全名；没有返回空。"""
    f = str(name_field or "")
    for a in sorted(aliases, key=len, reverse=True):
        if a and a in f:
            return aliases[a]
    return ""


def merge_split_quotes(pics):
    """P421：同一个人连着两行台词、前一句以逗号结尾（「张哥，」+「你叫什么名字？」是一句被「她喊道」隔开的）→ 并成一行。"""
    out = []
    for l in str(pics or "").splitlines():
        m = _DLG_LINE.match(l.strip())
        if m and out and not l.startswith("──"):
            # 往回找最近的台词行（中间最多隔一行旁白：「她试探性地喊道」）
            j = len(out) - 1
            while j >= max(0, len(out) - 2) and not _DLG_LINE.match(out[j].strip()):
                j -= 1
            pm = _DLG_LINE.match(out[j].strip()) if j >= 0 else None
            if pm and pm.group(1) == m.group(1) and pm.group(3).strip().endswith(("，", ",", "、")):
                out[j] = "%s：%s%s" % (pm.group(1), pm.group(3).strip(), m.group(3).strip())
                continue
        out.append(l)
    return "\n".join(out)


def dialogue_performance(text):
    """Separate leading stage directions from the words to be spoken.

    Only recognise performance cues; parenthesised dialogue/meaning stays intact.
    """
    text = str(text or "").strip()
    cues = []
    while True:
        m = re.match(r"^[（(]([^（）()\n]{1,24})[）)]\s*(.+)$", text)
        if not m:
            break
        cue = m.group(1).strip()
        if not re.match(r"^(?:低喝|低声|低吼|轻声|高声|大喊|怒吼|喊道|喃喃|喘息|"
                        r"温柔|平静|冷笑|微笑|咬牙|愣住|点头|摇头|眯起眼|"
                        r"看向|望向|转向|护住|扶住|抱住|握紧|抬头|低头)", cue):
            break
        cues.append(cue)
        text = m.group(2).strip()
    return text, cues


def numbered_script(pics):
    """剧本里的台词行编号：【D3】名：句。返回 (编号后的剧本, [(说话人, 句子), ...])。"""
    D, lines = [], []
    for l in merge_split_quotes(pics).splitlines():
        m = _DLG_LINE.match(l.strip())
        if m and not l.startswith("──"):
            spoken, cues = dialogue_performance(m.group(3))
            if m.group(2):
                cues.insert(0, m.group(2)[1:-1])
            if cues:
                lines.append(m.group(1) + "，" + "，".join(cues) + "。")
            D.append((m.group(1), spoken))
            lines.append("【D%d】%s：%s" % (len(D), m.group(1), spoken))
        else:
            lines.append(l)
    return "\n".join(lines), D


def beat_instruction(scene_names, char_names, lo, hi, n_dlg):
    return ("你是短剧导演。把【剧本】拆成节拍表。一拍＝一个人做一件推动故事的事 + 对方的一个反应，最多一句台词。"
            "每拍一行，格式固定：\n拍N｜地点｜谁｜做什么｜对方反应｜台词｜景别\n"
            "· 全话 %d～%d 拍。同一个人连续的几个小动作合成一拍（掰饼、揣怀里、推门＝一拍）；每一件推动故事的事一拍\n"
            "· 地点只从这些名字里选：%s\n"
            "· 谁 / 对方只能是这些人：%s（没有对方写 无）\n"
            "· 做什么：一句话、一个动作，这个人是主语（例：蹲下探老者鼻息）\n"
            "· 对方反应：对方的一个动作或表情（例：睁开眼抓住他手腕）；没有写 无\n"
            "· 台词：剧本里的台词已经编号（【D1】…【D%d】），台词栏只写编号（例：D3）；每个编号都要用到、按顺序、一拍最多一个编号；这一拍没台词写 无\n"
            "· 景别：全景 / 中景 / 特写。揭示、决定、反应用特写；动作用中景；到一个新地方的第一拍用全景\n"
            "· 背、扶、抱、摔、翻身这类动作写成两拍：准备（蹲下托住，还没背起）和完成后（已在背上，起身走）\n"
            "· 只写剧本里发生的事；纯环境、心情描写不成拍；赶路、等待这类只写一拍\n"
            "只输出节拍行，不解释。" % (lo, hi, "、".join(scene_names or []) or "剧本里的地点", "、".join(char_names or []), n_dlg))


def parse_beats(text, scene_names, char_names, D=None, aliases=None):
    aliases = aliases or aliases_of(char_names)
    D = D or []
    beats = []
    for line in str(text or "").splitlines():
        if "｜" not in line and "|" not in line:
            continue
        cols = [c.strip() for c in re.split(r"[｜|]", line.strip())]
        if len(cols) < 4 or not re.match(r"^(?:拍)?\s*\d+\s*$", cols[0]):
            continue
        cols += [""] * (7 - len(cols))
        n, place, who, act, react, say, shot = cols[:7]
        # 台词栏和景别栏错位（模型少写一栏）：景别栏里是 D 编号或台词、台词栏里是景别 → 换回来
        if re.search(r"D\s*\d+|：", shot) and not re.search(r"D\s*\d+|：", say):
            say, shot = shot, say
        if not act.strip() or act.strip() in ("无", "—", "-"):
            continue
        who_c = canon(who, aliases) if char_names else who.strip()
        if char_names and not who_c:
            continue
        say = say.strip()
        dk = None
        md = re.search(r"D\s*(\d+)", say)
        if md and 1 <= int(md.group(1)) <= len(D):
            dk = int(md.group(1))
            say = "%s：%s" % (short_name(canon(D[dk - 1][0], aliases) or D[dk - 1][0]), D[dk - 1][1])
        elif say in ("无", "—", "-", ""):
            say = ""
        react = react.strip()
        if react in ("无", "—", "-"):
            react = ""
        shot = next((sh for sh in _SHOTS if sh in shot), "中景")
        beats.append({"place": place.strip(), "who": who_c, "act": act.strip().rstrip("。"), "react": react.rstrip("。"),
                      "say": say, "shot": shot, "_d": dk})
    return beats


def _bigrams(t, names=()):
    t = str(t or "")
    for n in sorted(names, key=len, reverse=True):
        if n:
            t = t.replace(n, "")
    t = re.sub(r"[^\u4e00-\u9fa5]", "", t)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def dialogue_context(pics):
    """每句台词前后最近的叙述（给补拍找位置用）。返回 [(前一句叙述, 后一句叙述), ...]，与 numbered_script 的 D 同序。
    剧本常把台词行写在它那段叙述的**前面**，所以后文也要看。"""
    lines = [l.strip() for l in str(pics or "").splitlines() if l.strip() and not l.strip().startswith("──")]
    out = []
    for i, st in enumerate(lines):
        if not _DLG_LINE.match(st):
            continue
        prev = next((lines[j][-200:] for j in range(i - 1, -1, -1) if not _DLG_LINE.match(lines[j])), "")
        nxt = next((lines[j][:200] for j in range(i + 1, len(lines)) if not _DLG_LINE.match(lines[j])), "")
        out.append((prev, nxt))
    return out


def ask_dialogue_positions(beats, D, missing, q):
    """窄任务：没安排进节拍的台词该在哪一拍之后说。返回 {编号: 拍序号(0=最前面)}；问不出返回 {}。"""
    if not missing or q is None:
        return {}
    sysm = ("你是短剧导演。下面是已经排好的节拍（按顺序编号）和几句还没安排进去的台词。"
            "给每句台词指出它应该在哪一拍**之后**说（0 表示在第 1 拍之前）。只输出 JSON：{\"D编号\": 拍序号}，不解释。")
    user = "【节拍】\n" + "\n".join("%d. %s%s%s" % (i + 1, b["who"], b["act"], ("；" + b["react"]) if b["react"] else "") for i, b in enumerate(beats)) + \
           "\n\n【台词】\n" + "\n".join("D%d %s：%s" % (k, D[k - 1][0], D[k - 1][1]) for k in missing)
    try:
        obj = _json_obj(q(sysm, user, mt=300, temperature=0.1))
    except Exception:
        return {}
    out = {}
    for k, v in (obj or {}).items():
        m = re.search(r"(\d+)", str(k))
        try:
            if m and 0 <= int(v) <= len(beats):
                out[int(m.group(1))] = int(v)
        except Exception:
            pass
    return out


def ensure_dialogue(beats, D, aliases, contexts=None, q=None):
    """剧本里的每句台词都要在某一拍里、按顺序：重复用的只留第一次，没用到的补成单独一拍。
    补拍位置：先问模型「在哪一拍之后」（窄任务）；程序核顺序（不早于前一句台词那拍、不晚于后一句）；
    不合格退回相似度（台词前后两段叙述、去掉人名）；都不行放在下一句台词之前。"""
    contexts = contexts or []
    names = list(aliases or {})
    seen = set()
    for b in beats:
        if b.get("_d") in seen:
            b["say"], b["_d"] = "", None
        elif b.get("_d"):
            seen.add(b["_d"])
    missing = [k for k in range(1, len(D) + 1) if k not in seen]
    hint = ask_dialogue_positions(beats, D, missing, q) if missing else {}
    for k in missing:
        spk, txt = D[k - 1]
        floor = max([i for i, b in enumerate(beats) if b.get("_d") and b["_d"] < k], default=-1)
        ceil_ = min([i for i, b in enumerate(beats) if b.get("_d") and b["_d"] > k], default=len(beats))
        # 说话的人得先「能动」：他第一次当主语的拍之前不能开口（老者昏迷时不会报恩；P370c 项目217 实测）
        _spk_c = canon(spk, aliases)
        _first_act = next((i for i, b in enumerate(beats) if _spk_c and b["who"] == _spk_c), None)
        if _first_act is not None and _first_act < ceil_:
            floor = max(floor, _first_act)              # 至少在他第一次动作那拍之后
        idx = None
        if k in hint and floor < hint[k] <= ceil_:
            idx = hint[k] - 1                         # 「第 n 拍之后」= 索引 n-1
        if idx is None:
            ctx = contexts[k - 1] if k - 1 < len(contexts) else ("", "")
            if isinstance(ctx, str):
                ctx = (ctx, "")
            best, best_n = -1, 0
            for side in (0, 1):
                cb = _bigrams(ctx[side], names)
                for i in range(floor + 1, ceil_):
                    n_ = len(cb & _bigrams(beats[i]["act"] + beats[i]["react"], names))
                    if n_ > best_n:
                        best, best_n = i, n_
            idx = best if (best >= 0 and best_n >= 2) else (ceil_ - 1 if floor < 0 else floor)
        place = beats[idx]["place"] if 0 <= idx < len(beats) else (beats[-1]["place"] if beats else "")
        who = canon(spk, aliases) or spk
        # P372：对方的状态从上一拍带过来（「老者胸口剧烈起伏」→ 写提示词的知道他还躺着）
        _prev_react = beats[idx]["react"] if 0 <= idx < len(beats) else ""
        _oth = _other(who, list(aliases.values()))
        _react = _prev_react if (_prev_react and (_oth in _prev_react or short_name(_oth) in _prev_react)) else ""
        beats.insert(idx + 1, {"place": place, "who": who, "act": "面朝对方，开口说话", "react": _react, "shot": "特写", "_d": k,
                               "say": "%s：%s" % (short_name(who), txt)})
        seen.add(k)
    return beats


def ensure_tail(beats, pics, char_names, aliases=None):
    """剧本最后一段叙述（最后一句台词之后的事）没有拍覆盖 → 补一拍，用那段的最后一句当动作，全景。"""
    aliases = aliases or aliases_of(char_names)
    names = list(aliases)
    lines = [l.strip() for l in str(pics or "").splitlines() if l.strip() and not l.strip().startswith("──")]
    tail = ""
    for st in reversed(lines):
        if _DLG_LINE.match(st):
            break
        tail = st + tail
    if not tail or not beats:
        return beats
    sents = [x for x in re.findall(r"[^。！？]+[。！？]?", tail) if x.strip()]
    last = sents[-1].strip().rstrip("。！？") if sents else tail[-40:]
    tb = _bigrams(last, names)                                          # 看的是最后一句（上路那句），不是整段
    covered = any(len(tb & _bigrams(b["act"] + b["react"], names)) >= 2 for b in beats[-3:])
    if covered:
        return beats
    who = canon(tail, aliases) or (beats[-1]["who"] if beats else "")
    act = re.sub(r"^(他|她|%s)[，,]?" % "|".join(re.escape(a) for a in sorted(names, key=len, reverse=True)), "", last).strip("，, ")
    if not act:
        return beats
    beats.append({"place": beats[-1]["place"], "who": who, "act": act, "react": "", "say": "", "shot": "全景", "_d": None})
    return beats


def merge_small(beats, hi):
    """拍数超上限：并同地、同人、都没台词的相邻拍（动作用逗号连），直到不超。"""
    while len(beats) > hi:
        merged = False
        for i in range(len(beats) - 1):
            a, b = beats[i], beats[i + 1]
            _cl = len(re.split(r"[，、,]", a["act"])) + len(re.split(r"[，、,]", b["act"]))
            _micro = len(a["act"]) <= 10 and len(b["act"]) <= 10          # 两个都是碎动作：景别不同也并
            if a["place"] == b["place"] and a["who"] == b["who"] and (a["shot"] == b["shot"] or _micro) and not a["say"] and not b["say"] and not a["react"] and not a.get("_split") and not b.get("_split") and _cl <= 3:
                a["act"] = a["act"] + "，" + b["act"]
                a["react"] = a["react"] or b["react"]
                a["shot"] = b["shot"] if b["shot"] == "特写" else a["shot"]
                del beats[i + 1]
                merged = True
                break
        if not merged:
            break
    return beats


def _split_say(say, cap=30):
    """长台词拆成几拍：只在句末（。！？；）拆，逗号不拆；单句再长也整句留一拍（P431）。"""
    m = re.match(r"^([^：:]{1,8})[：:]\s*(.+)$", say)
    if not m:
        return [say]
    who, q = m.group(1), m.group(2).strip().strip("“”「」\"")
    if len(re.sub(r"[^一-龥]", "", q)) <= cap:
        return [say]
    parts = [x for x in re.findall(r"[^。！？；]+[。！？；]?", q) if x.strip()]
    out, cur = [], ""
    for p in parts:
        if cur and len(re.sub(r"[^一-龥]", "", cur + p)) > cap:
            out.append(cur)
            cur = p
        else:
            cur += p
    if cur:
        out.append(cur)
    return ["%s：%s" % (who, x.strip()) for x in out]


def _other(who, char_names):
    for c in char_names or []:
        if c not in who and short_name(c) not in who:
            return c
    return ""


_FAST_VERBS = re.compile(r"打|踢|砍|劈|刺|挥|冲|扑|撞|摔|跳|跑|追|躲|闪|滚|抓住|拽|拉住|推开|甩|射|砸|踹|翻过|抢|夺|逃|后退|挡|格开|袭|扑倒|撞开|拔|掷|扔|踩|扫|震动|坍塌|崩塌|炸开|爆")
_CUE_SLOW = re.compile(r"[（(]?(慢慢来|慢一点|放慢|慢节奏|文戏|慢镜头)[）)]?[，,。]?")
_CUE_FAST = re.compile(r"[（(]?(快一点|快切|加快|快节奏|动作戏|紧张起来|快镜头)[）)]?[，,。]?")
_FILLER = re.compile(r"^(站着|站在原地|站定|等着|等待|看着|望着|盯着|沉默|不动|静静地?站|一动不动|保持|停在原地)")
_EP_PACE = {"舒缓": "慢", "紧凑": "快", "适中": "快"}      # P407：只有快 / 慢两档；旧项目的「适中」按快


def beat_pace(b, default="快"):
    """一拍的档位（P407 只有两档）：口述提示词最高（慢慢来 / 快一点）→ 有打冲躲抓这类动作词→快 → 有台词没硬动作→慢
    → 没台词的普通动作按本话默认（舒缓→慢，紧凑→快）。"""
    txt = " ".join(str(b.get(k) or "") for k in ("act", "react", "say"))
    if _CUE_SLOW.search(txt):
        return "慢"
    if _CUE_FAST.search(txt):
        return "快"
    _act = str(b.get("act") or "")
    _fast = bool(_FAST_VERBS.search(_act + str(b.get("react") or "")) or _HARD_RE.search(_act)) and not re.search(r"停下|停止|平息|停了|静下", _act)
    if _fast:
        return "快"                                                         # 「震动停下来」是收势，不算快
    if b.get("say"):
        return "慢"                                                         # P407c：有台词又没硬动作 → 慢（收势句也算）
    return default


def _clauses(act):
    """动作句的分句数（纯说话拍算 0）——慢档并拍只并小动作，合起来不超过三个分句。"""
    a = str(act or "")
    if a in ("说话", "接着说下去", "面朝对方，开口说话"):
        return 0
    return len([x for x in re.split(r"[，、,]", a) if x.strip()])


def strip_pace_cues(text):
    return _CUE_FAST.sub("", _CUE_SLOW.sub("", str(text or ""))).strip("，, ")


_ENV_HEAD = re.compile(r"^(海风|风|海鸟|鸟|阳光|夕阳|晨光|月光|火光|夜幕|夜色|天色|天空|云|雨|雪|雾|浪|海浪|海潮|潮水|潮声|影子|远处|岛上|荒岛|画面|镜头|四周|周围|篝火|火堆|火苗|树梢|棕榈叶|沙滩上|海面|笑声|香气|鱼肉|一切|整个|山|溪|林间|阳光下|日子|第二天|次日|清晨|夜晚|傍晚|黄昏|天亮|时间|这一天|这一夜)")
_TIME_JUMP = re.compile(r"第二天|次日|清晨|天亮|翌日|夜幕降临|夜深|入夜|傍晚|黄昏|日落|第.天|几天后|数日后|一周后|过了.{0,3}(天|日|夜)|醒来|睡醒|醒了")
_GROUP_WORD = re.compile(r"人影|身影|四人|三人|几人|众人|大家|四个人|三个人|两人|所有人|她们|他们|女孩们|几个人")
_OBJ_HEAD = re.compile(r"^(鱼|门|风|水|火|树|船|浪|石|草|沙|布|鸟|叶|木|绳|帐篷|火苗|烤鱼|竹|藤|海|天|云|雨|影|灰尘|尘土|光|符文|水晶|地图|刀|剑|杖|车|马|女孩们|女孩|她们|他们|众人|大家|三人|两人|四人)")


def fix_env_beats(beats, char_names):
    """P415：拍里「谁」挂了人、「做什么」却是环境句 → 群体句（四个人影忙碌）算全员，纯环境句算空镜。
    P421：没写「谁」的动作拍沿用上一拍的人；反应栏只是人名/群体词（「小林。」「女孩们。」）→ 清空。"""
    names = [n for n in (char_names or []) if n]
    shorts = [short_name(n) for n in names]
    prev_who = ""

    def _group_subject(a):
        m = _GROUP_WORD.search(a)
        if not m:
            return False
        if re.search(r"[向对给朝看替把让]\S{0,3}$", a[:m.start()]):
            return False                                                    # 「向女孩们伸出手」是宾语
        return m.start() <= 6 or bool(re.search(r"[，。；]\s*(但|而|只有|唯有)?\s*$", a[:m.start()]))   # P432：句首，或前一分句结束后紧跟
    for b in beats:
        act = str(b.get("act") or "")
        if _TIME_JUMP.search(act[:12]):
            b["_split"] = "time"                                            # P426：时间跳转的拍不和前一拍并、硬切起
        react_core = re.sub(r"[^一-龥]", "", str(b.get("react") or ""))
        if react_core and (react_core in names + shorts or _GROUP_WORD.fullmatch(react_core) or len(react_core) <= 2
                           or re.fullmatch(r"(?:%s)(?:和(?:%s))*" % ("|".join(map(re.escape, names + shorts)), "|".join(map(re.escape, names + shorts))), react_core)):
            b["react"] = ""
        if not b.get("who"):
            if act and not _ENV_HEAD.match(act) and not _group_subject(act) and prev_who and not any(n and n in act for n in names + shorts):
                b["who"] = prev_who                                         # 「转身背对女孩们」是上一拍那个人接着做
            elif _group_subject(act):
                b["who"] = "和".join(shorts or names)
                b["_noprefix"] = True
        elif not any(n and n in act for n in names + shorts):
            if _group_subject(act):
                b["who"] = "和".join(shorts or names)
                b["_noprefix"] = True
            elif _ENV_HEAD.match(act):
                b["who"] = ""
                b["react"] = ""
                b["say"] = ""
        if b.get("who"):
            prev_who = b["who"]
    return beats


def normalize_beats(beats, char_names, pace="适中"):
    """程序三道规矩 + 时长 + 接法。返回 [{text, seconds, scene_hint, beat, beat_index, shot, establish, cut, who, pace}]。
    P404：每拍先判档（慢 / 中 / 快），慢档并拍接力、快档短切轮换。"""
    aliases = aliases_of(char_names)
    beats = fix_env_beats(beats, char_names)                                # P415：环境句不挂人
    _default = _EP_PACE.get(str(pace or "紧凑"), "快")
    expanded = []
    for b in beats:
        m = _HARD_RE.search(b["act"])
        key = None
        if m:
            after = b["act"][m.end():m.end() + 12] + b["act"][max(0, m.start() - 6):m.start()]
            obj_is_person = bool(_PERSON_OBJ.search(after)) or any(a in after for a in aliases if a not in b["who"] and short_name(a) not in b["who"])
            if obj_is_person:
                key = next((k for k in _HARD if k in m.group(1)), None)
        _recent = " ".join(x["act"] + x["react"] for x in expanded[-3:])
        if key and re.search(r"背在背上|已经把|扛在肩上|抱在怀里|扶起来", _recent):
            key = None                                                     # 前面三拍里已经背/抱/扶完成了，不再拆
        if key and not re.search(r"已经|已在|完成|起身走|起身", b["act"] + " " + b["react"]):
            o = short_name(_other(b["who"], char_names)) or "对方"
            a, c = _HARD[key]
            expanded.append(dict(b, act=a.format(o=o), react="", say="", _d=None, shot="中景", _split="prep"))
            expanded.append(dict(b, act=c.format(o=o), _split="done"))
            continue
        says = _split_say(b["say"]) if b["say"] else [""]
        for i, s_ in enumerate(says):
            if i == 0:
                expanded.append(dict(b, say=s_))
            else:
                _spk = canon(s_.split("：", 1)[0], aliases) or b["who"]        # 续说的拍：谁 = 台词的说话人
                _lis = short_name(_other(_spk, char_names))
                expanded.append(dict(b, who=_spk, act="接着说下去", react=(_lis + "听着") if _lis else "", say=s_, shot="特写", _d=None))
    # P392①：只说话的拍并进上一拍（同地点、两边台词都不长）——一问一答放一段里，不再一句一段
    folded = []
    for b in expanded:
        prev_b = folded[-1] if folded else None
        _q = b["say"].split("：", 1)[-1] if b["say"] else ""
        _pq = (prev_b or {}).get("say", "").split("：", 1)[-1] if prev_b and prev_b.get("say") else ""
        if (prev_b is not None and b["say"] and b["act"] == "说话" and b["place"] == prev_b["place"]
                and "\n" not in prev_b.get("say", "") and prev_b.get("who")
                and len(re.sub(r"[^一-龥]", "", _q)) <= 22 and len(re.sub(r"[^一-龥]", "", _pq)) <= 22):
            prev_b["say"] = (prev_b["say"] + "\n" + b["say"]) if prev_b["say"] else b["say"]
            if b.get("react") and not prev_b.get("react"):
                prev_b["react"] = b["react"]
            continue
        folded.append(dict(b))
    expanded = folded
    # ── P404 按拍判档 + 慢档并拍 + 快档删填充 / 并台词 ──
    for i, b in enumerate(expanded):
        b["_pace"] = beat_pace(b, _default)
        b["act"] = strip_pace_cues(b["act"]) or b["act"]
        b["react"] = strip_pace_cues(b["react"])
    for i, b in enumerate(expanded):
        if _FILLER.match(str(b.get("act") or "")) and not b.get("say") and i > 0:
            b["_pace"] = expanded[i - 1]["_pace"]                            # 站着/等着这类拍跟前一拍的档位走
    paced = []
    for b in expanded:
        prev_b = paced[-1] if paced else None
        if b["_pace"] == "快" and not b.get("say") and b.get("who") and _FILLER.match(str(b.get("act") or "")) and not b.get("_split"):
            continue                                                       # 快档：纯站着 / 等着的填充拍删掉
        if (b["_pace"] == "快" and prev_b is not None and b.get("say") and str(b.get("act") or "") in ("说话", "接着说下去")
                and b["place"] == prev_b["place"] and prev_b.get("who")
                and len(re.sub(r"[^一-龥]", "", b["say"].split("：", 1)[-1])) <= 10
                and prev_b.get("say", "").count("\n") < 2):
            prev_b["say"] = (prev_b["say"] + "\n" + b["say"]) if prev_b["say"] else b["say"]     # 快档：短台词并进动作拍
            continue
        if (b["_pace"] == "慢" and prev_b is not None and prev_b.get("_pace") == "慢" and b["place"] == prev_b["place"]
                and b.get("who") and prev_b.get("who") and not b.get("_split") and not prev_b.get("_split")
                and b["act"] != "接着说下去" and prev_b["act"] != "接着说下去"
                and (prev_b.get("say", "").count("\n") + (b["say"].count("\n") + 1 if b.get("say") else 0)) <= 2
                and _clauses(prev_b["act"]) + _clauses(b["act"]) <= 3):
            # 慢档：同地连续文戏拍并成一段（≤3 句台词、动作合计 ≤60 字）
            if b["act"] not in ("说话", "接着说下去", "面朝对方，开口说话"):
                if prev_b["act"] in ("面朝对方，开口说话", "说话"):
                    # P430：前一拍只是占位的「开口说话」→ 用后一拍的真动作替换，不拼成「面朝对方，开口说话，林岩搁柴刀擦手」
                    prev_b["act"] = ("" if b.get("_noprefix") or b["who"] == prev_b["who"] else short_name(b["who"])) + b["act"]
                    if b["who"] != prev_b["who"] and not b.get("_noprefix"):
                        prev_b["_noprefix"] = True
                else:
                    prev_b["act"] = prev_b["act"].rstrip("。") + "，" + (("" if b.get("_noprefix") or b["who"] == prev_b["who"] else short_name(b["who"])) + b["act"])
            elif prev_b["act"] == "面朝对方，开口说话" and b["act"] == "面朝对方，开口说话":
                prev_b["act"] = "两人面对面说话"                                    # P416：两个占位拍并成一句
                prev_b["_noprefix"] = True
            if b.get("react") and not prev_b.get("react"):
                prev_b["react"] = b["react"]
            if b.get("say"):
                prev_b["say"] = (prev_b["say"] + "\n" + b["say"]) if prev_b["say"] else b["say"]
            prev_b["_merged"] = True
            continue
        paced.append(dict(b))
    expanded = paced
    out, prev = [], None
    _cycle = ("全景", "中景", "特写")
    for b in expanded:
        act, react = b["act"], b["react"]
        if react and not any(a in react for a in aliases) and not _OBJ_HEAD.match(react):     # P415：「鱼挣扎着被挑出」不补人名
            o = short_name(_other(b["who"], char_names))
            if o:
                react = o + react
        clauses = len(re.split(r"[，、,]", act + ("，" + react if react else "")))
        say_len = sum(len(re.sub(r"[^一-龥]", "", ln.split("：", 1)[-1])) for ln in b["say"].splitlines()) if b["say"] else 0
        _pc = b.get("_pace") or _default
        if not b.get("who"):
            secs = 4 if pace == "紧凑" else 5                                  # P392①：空镜定场 5 秒
        elif _pc == "慢":
            secs = max(8, min(12, round(6 + 1.5 * min(clauses, 3) + say_len / 3)))     # P404 慢档：8～12 秒，稳镜头
        else:
            _nsay = b["say"].count("\n") + 1 if b["say"] else 0
            secs = max(6 if _nsay >= 2 else 4, min(8 if _nsay >= 2 else 6, round(3 + 1.2 * min(clauses, 3) + say_len / 4)))   # P404 快档：4～6 秒，一个动作；两句台词 6～8
        secs = float(secs)
        establish = prev is None or b["place"] != prev["place"] or b.get("_split") == "time"    # P426：换了时间也算换场
        if _pc == "快" and prev is not None and not establish and b["shot"] == prev["shot"] and b["who"] == prev["who"] and not b.get("_split"):
            b["shot"] = _cycle[(_cycle.index(b["shot"]) + 1) % 3] if b["shot"] in _cycle else b["shot"]   # 快档：连续同景别同人就换一档景别
        if _pc == "慢":
            cut = True if prev is None else bool(establish)                  # 慢档：同地不切，末帧接力
        else:
            cut = True                                                       # 快档：每拍硬切
        who_txt = short_name(b["who"])
        _pre = "" if b.get("_noprefix") else who_txt          # P389②③：多人拍/做什么已带名字，不再加主语
        text = "── %s ──\n%s%s。%s" % (b["place"], _pre, act, ("" if not react else react + "。"))
        if b["say"]:
            text += "\n" + b["say"]
        out.append({"text": text, "seconds": secs, "scene_hint": b["place"], "beat": "", "beat_index": -1,
                    "shot": b["shot"], "establish": bool(establish), "cut": bool(cut), "who": who_txt, "pace": _pc})
        prev = b
    return out


def beat_range(n_events, n_dlg, hanzi):
    """拍数范围：事件数给下限，正文字数给上限（约 35 字一拍 + 每句台词一拍），取大的（P414）。"""
    lo = max(6, int(n_events or 5) + 1, int(hanzi or 0) // 70)
    hi = max(10, 2 * int(n_events or 5) + int(n_dlg or 0), int(hanzi or 0) // 35 + int(n_dlg or 0))
    return lo, max(hi, lo + 2)


def beat_table(pics, scene_names, char_names, q, pace="适中", n_events=5, debug_path=""):
    script, D = numbered_script(pics)
    aliases = aliases_of(char_names)
    lo, hi = beat_range(n_events, len(D), len(re.sub(r"[^一-龥]", "", str(pics or ""))))
    ins = beat_instruction(scene_names, char_names, lo, hi, len(D))
    raw = q(ins, "【剧本】\n" + script, mt=3000, temperature=0.3)
    beats = parse_beats(raw, scene_names, char_names, D, aliases)
    if len(beats) < 3:
        raw = q(ins, "【剧本】\n" + script + "\n\n（严格按格式，每拍一行，至少 %d 拍）" % lo, mt=3000, temperature=0.5)
        beats = parse_beats(raw, scene_names, char_names, D, aliases)
    beats = ensure_dialogue(beats, D, aliases, dialogue_context(pics), q)
    beats = ensure_tail(beats, pics, char_names, aliases)
    beats = merge_small(beats, hi)
    beat_table.last_raw = raw
    if debug_path:
        try:
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(ins + "\n\n===== 模型输出 =====\n" + raw + "\n\n===== 解析后 =====\n" + json.dumps(beats, ensure_ascii=False, indent=1))
        except Exception:
            pass
    return normalize_beats(beats, char_names, pace)


# ═══════════════ ③ 口述拍法（模式二） ═══════════════
def _scene_items(scenes):
    """场景卡列表（dict 或名字）→ [(名字, 一句描述)]。"""
    out = []
    for x in scenes or []:
        if isinstance(x, dict):
            nm = str(x.get("name") or "").strip()
            desc = "，".join(p.strip() for p in re.split(r"[。；\n]", str(x.get("contract_text") or x.get("desc") or x.get("description") or ""))[:2] if p.strip())
        else:
            nm, desc = str(x or "").strip(), ""
        if nm:
            out.append((nm, desc[:24]))
    return out


_PLACE_CLASSES = (("棚", r"棚|柴房|窝棚|茅舍"), ("水", r"河|湖|溪|江|海|潭|池|岸边|水中|船上|海上"), ("岸", r"滩|沙滩|码头|渡口|岸"), ("路", r"官道|大道|路|街|巷|桥"),
                  ("院", r"院|园|坪|广场"), ("屋", r"房|屋|家|室内|堂|殿|厅|阁|楼|寺|庙|洞"), ("山林", r"森林|树林|林|山|岭|坡|谷|崖|野|郊|荒"))


def split_merged_by_class(main_name, main_space, merged):
    """P419：【归并自】里和主景不同类的地点拆出来：返回 [(新卡名, [别名...])]，同类的留在主景里。
    （海上（水）不能并沙滩（岸）；礁石堆这种判不出类的跟着主景。）"""
    main_cls = place_class(main_name) or place_class(str(main_space or "")[:40])
    groups = {}
    for m in merged or []:
        c = place_class(m)
        if not c or c == main_cls:
            continue
        groups.setdefault(c, []).append(str(m).strip())
    return [(ms[0], ms[1:]) for ms in groups.values() if ms]


def place_class(t):
    """先认地点名称中的具体空间，再回退到自然地貌；专名里的海/林不抢占正殿/广场。"""
    t = re.split(r"[，。；\n]", str(t or "").strip(), maxsplit=1)[0]
    spaces = (("棚", r"柴房|窝棚|茅舍|草棚|棚"),
              ("岸", r"沙滩|海滩|河滩|码头|渡口|岸边|河岸|湖岸|海岸"),
              ("路", r"官道|大道|道路|街道|小路|巷|桥"),
              ("院", r"庭院|院子|花园|草坪|广场|院|园|坪"),
              ("屋", r"室内|正殿|大殿|大厅|房间|房|屋|堂|殿|厅|阁|楼|寺|庙|洞"),
              ("水", r"水中|船上|海上|河中|湖中|江中|池塘"))
    matches = [(m.end(), len(m.group()), c) for c, rx in spaces for m in re.finditer(rx, t)]
    if matches:
        return max(matches)[2]
    for c, rx in _PLACE_CLASSES:
        if re.search(rx, t):
            return c
    return ""


def _char_items(chars):
    """人物卡列表（dict 或名字）→ [(名字, 性别, 年龄)]。"""
    out = []
    for x in chars or []:
        if isinstance(x, dict):
            nm = str(x.get("name") or "").strip()
            sex = str(x.get("sex") or x.get("gender") or "").strip()
            m = re.search(r"\d+", str(x.get("age") or ""))
            age = int(m.group(0)) if m else -1
        else:
            nm, sex, age = str(x or "").strip(), "", -1
        if nm:
            out.append((nm, sex, age))
    return out


def role_aliases(chars):
    """按卡的年龄/性别给称呼别名（口述里的「少年/老人/老师傅」→ 卡名）；只有唯一一张卡符合的称呼才算。"""
    items = _char_items(chars)
    names = [n for n, _, _ in items]
    words = {}
    for nm, sex, age in items:
        ws = []
        if 0 <= age <= 19:
            ws += ["孩子", "小孩"] + (["少年", "男孩", "小伙", "小子", "少年郎"] if sex == "男" else (["少女", "女孩", "姑娘", "丫头"] if sex == "女" else ["少年", "少女"]))
        elif age >= 50:
            ws += ["老人", "老人家", "老者", "长者"] + (["老头", "老汉", "老丈", "老爷子", "老师傅", "老先生", "老翁", "老伯"] if sex == "男" else (["老太太", "老婆婆", "老妇", "老妪", "老奶奶"] if sex == "女" else []))
        elif age >= 20:
            ws += (["男子", "男人", "汉子", "青年", "小伙子"] if sex == "男" else (["女子", "女人", "女郎", "妇人"] if sex == "女" else []))
        for w in ws:
            words.setdefault(w, set()).add(nm)
    al = {}
    for w, who in words.items():
        if len(who) == 1 and w not in names:
            al[w] = next(iter(who))
    return al


def resolve_places(beats, scenes, q=None):
    """口述里的地点（森林/房间）→ 场景卡名。精确/包含 → 按类别唯一 → 模型选卡（类别里多张就只在这几张里选）→ 沿用上一拍 → 第一张卡。"""
    items = _scene_items(scenes)
    names = [n for n, _ in items]
    if not names:
        return beats
    memo, last = {}, ""
    for b in beats:
        p = str(b.get("place") or "").strip()
        if p in memo:
            m = memo[p]
        else:
            m = _match_name(p, names)
            cand = items
            if not m and p:
                pc = place_class(p)
                same = [(n, d) for n, d in items if pc and place_class(n) == pc]
                if len(same) == 1:
                    m = same[0][0]
                elif len(same) > 1:
                    cand = same
            if not m and p and q is not None:
                try:
                    ans = q("只回答一个场景卡的名字，不解释。", "场景卡：\n%s\n\n口述里的地点「%s」最像哪一张卡？" % (
                        "\n".join("· %s：%s" % (n, d) for n, d in cand), p), mt=40, temperature=0)
                    m = _match_name(str(ans or "").strip().strip("「」“”\"。"), [n for n, _ in cand])
                except Exception:
                    m = ""
            memo[p] = m
        if m:
            last = m
        b["place"] = last or names[0]
    return beats


def _match_name(h, names):
    h = str(h or "").strip()
    if not h:
        return ""
    if h in names:
        return h
    for n in names:
        if h in n or n in h:
            return n
    best, best_len = "", 0
    for n in names:
        for L in range(min(len(h), len(n)), 1, -1):
            if L <= best_len:
                break
            if any(h[a:a + L] in n for a in range(0, len(h) - L + 1)):
                best, best_len = n, L
                break
    return best if best_len >= 2 else ""


def dictation_instruction(scene_names, char_names):
    _sc = "；".join(("%s（%s）" % (n, d)) if d else n for n, d in _scene_items(scene_names)) or "口述里的地点"
    _ch = "、".join(("%s（%s%s）" % (n, sx, ("，%d岁" % ag) if ag >= 0 else "")) if (sx or ag >= 0) else n for n, sx, ag in _char_items(char_names))
    return ("你是短剧导演。用户口述了这一话**怎么拍**（一个镜头接一个镜头），你把它整理成节拍表，一个镜头一拍，不改他的意思、不加他没说的动作。"
            "每拍一行，格式固定：\n拍N｜地点｜谁｜做什么｜对方反应｜台词｜景别\n"
            "· 地点照口述里的叫法写（森林、房间、山下…），口述没说地点就沿用上一拍，只有说了「转场」「下一个场景」才换。场景卡有：%s\n"
            "· 谁 / 对方只能是这些人：%s（没有对方写 无；口述里的「少年/老人」对应到卡名）\n"
            "· 做什么：照口述写这一镜头里这个人做的一件事（入画、回头、搀扶、背起、照顾、睁眼…）；两个人一起做的事，谁栏写两个名字\n"
            "· 对方反应：口述里说了才写，没说写 无\n"
            "· 台词：口述里说的话写成「名字：原话」，一字不改；台词跟着说话人那一拍的动作写在同一行（做什么＋台词），不要为台词单开一拍；一个人连说两句才分两拍；没有写 无\n"
            "· 景别：口述说了全景/中景/特写就照写；没说的动作用中景，说话和表情用特写；「入画」「转场」后的第一个镜头用全景\n"
            "· 口述里的「烛火特写」这类空镜也算一拍：谁写 无，做什么写画面（烛火在桌角跳动）\n"
            "只输出节拍行，不解释。" % (_sc, _ch))


def parse_beats_loose(text, scene_names, char_names, aliases=None, sexes=None):
    """口述拍法的解析：允许「谁」为空（空镜），其余同 parse_beats。"""
    aliases = aliases or aliases_of(char_names)
    beats = []
    for line in str(text or "").splitlines():
        if "｜" not in line and "|" not in line:
            continue
        cols = [c.strip() for c in re.split(r"[｜|]", line.strip())]
        if len(cols) < 4 or not re.match(r"^(?:拍)?\s*\d+\s*$", cols[0]):
            continue
        cols += [""] * (7 - len(cols))
        n, place, who, act, react, say, shot = cols[:7]
        if re.search(r"：", shot) and not re.search(r"：", say):
            say, shot = shot, say
        if not act.strip() or act.strip() in ("无", "—", "-"):
            continue
        _who_parts = [w for w in re.split(r"[、，,和与及\s]+", who.strip()) if w]
        _who_names = []
        for w in _who_parts:
            _cn = canon(w, aliases) if char_names else w
            if _cn and _cn not in _who_names:
                _who_names.append(_cn)
        who_c = (_who_names[0] if _who_names else (canon(who, aliases) if char_names else who.strip()))
        if who.strip() in ("无", "—", "-", ""):
            who_c = ""
        _multi = _who_names[1:] if len(_who_names) > 1 else []
        say = say.strip()
        if say in ("无", "—", "-"):
            say = ""
        elif say and "：" in say:
            spk, q_ = say.split("：", 1)
            say = "%s：%s" % (short_name(canon(spk, aliases) or spk), q_.strip().strip("“”「」\""))
        react = re.sub(r"^([^（(]+)[（(]([^）)]+)[）)]$", r"\1\2", react.strip())     # P389④：艾登（喘气）→ 艾登喘气
        if react in ("无", "—", "-"):
            react = ""
        _rest = react
        for a in list(aliases.keys()) + list(aliases.values()) + list(char_names or []):
            _rest = _rest.replace(str(a), "")
        if not re.search(r"[一-龥]", _rest):
            react = ""                                                  # 反应栏只填了个名字（老者）→ 当无
        act = act.strip().rstrip("。")
        if say and "：" in say:
            _q = say.split("：", 1)[1]
            if re.match(r"^[^，。]{0,4}(说|道|问|答|喊)[：:]", act) or (len(_q) >= 4 and _q[:6] in act):
                act = re.sub(r"^([^，。]{0,4})(说|道|问|答|喊)[：:].*$", r"\1\2话", act) if re.match(r"^[^，。]{0,4}(说|道|问|答|喊)[：:]", act) else "说话"
                act = act.replace("说话话", "说话").replace("问话", "问对方")
        shot = next((sh for sh in _SHOTS if sh in shot), "中景")
        # P389③：做什么开头的「转场，」「特写/全景/中景（，）」去掉；景别词也算进景别栏
        act = re.sub(r"^(转场|下一个镜头|下一个场景)[，,、]?\s*", "", act)
        _m_sh = re.match(r"^(全景|中景|特写|近景)[，,、]?\s*", act)
        if _m_sh:
            shot = {"近景": "特写"}.get(_m_sh.group(1), _m_sh.group(1))
            act = act[_m_sh.end():]
        act = re.sub(r"^画面[（(](.*)[）)]$", r"\1", act)
        _noprefix = False
        if _multi and who_c:
            _names_txt = "和".join(short_name(x) for x in [who_c] + _multi)
            if re.search(r"两人|两个人|二人", act):
                act = re.sub(r"两人|两个人|二人", _names_txt, act, count=1)          # 「两人一起转身」「…两人抬头」
            elif re.match(r"^(一起|同时|都|从|走|转|站|望|并肩|靠|抬|跑|停|退|躲|看|回|冲)", act):
                act = _names_txt + act                                             # 动作开头 → 名字＋动作
            _noprefix = True
        elif who_c and act.startswith(short_name(who_c)):
            _noprefix = True
        beats.append({"place": place.strip(), "who": who_c, "act": act, "react": react.rstrip("。"),
                      "say": say, "shot": shot, "_d": None, "_noprefix": _noprefix, "_multi": _multi})
    return fill_missing_who(merge_placeholder_says(beats), char_names, aliases, sexes)


_BODY_START = re.compile(r"^(双手|右手|左手|手指|手掌|手|指尖|掌心|眼睛|双眼|脸|嘴|嘴角|肩|背影|脚|脚步)")
_PERSON_HINT = re.compile(r"他|她|手|指尖|掌心|眼睛|双眼|脸|嘴|嘴角|肩膀|背影|脚步|表情")


_HAND_ACT = re.compile(r"手|伸|摸|推|够|碰|摊|握|拉|拿|递|指|抓|捧|托")


def fill_missing_who(beats, char_names, aliases=None, sexes=None):
    """P390/P391：谁＝无但做什么里明明有人（名字/两人/他她/身体部位）→ 补上，不当空镜。
    她/他 按卡的性别对人（唯一一张才算）；手/指尖的特写归最近一拍做了手上动作的人。"""
    aliases = aliases or aliases_of(char_names)
    names = [str(n) for n in (char_names or []) if str(n).strip()]
    sexes = dict(sexes or {})
    by_sex = {}
    for n in names:
        sx = str(sexes.get(n) or "").strip()
        if sx in ("男", "女"):
            by_sex.setdefault(sx, []).append(n)
    prev_who, prev_multi, hand_who = "", [], ""
    for b in beats:
        act = str(b.get("act") or "")
        if not b.get("who"):
            hit = [n for n in sorted(aliases, key=len, reverse=True) if n in act]
            _pro = "女" if "她" in act else ("男" if "他" in act and "他们" not in act else "")
            if hit:
                b["who"] = aliases[hit[0]]
                b["_noprefix"] = True
            elif _pro and len(by_sex.get(_pro) or []) == 1:
                b["who"] = by_sex[_pro][0]
                if _BODY_START.match(act):
                    b["act"] = short_name(b["who"]) + "的" + act
                b["_noprefix"] = True
            elif re.search(r"两人|两个人|二人|他们|她们|俩", act) and len(names) >= 2:
                pair = [prev_who] + [n for n in (prev_multi or []) if n != prev_who] if prev_who else []
                pair = [n for n in pair if n in names][:2] or names[:2]
                if len(pair) < 2:
                    pair = (pair + [n for n in names if n not in pair])[:2]
                b["who"], b["_multi"] = pair[0], pair[1:]
                b["act"] = re.sub(r"两人|两个人|二人|他们|她们|俩", "和".join(short_name(x) for x in pair), act, count=1)
                b["_noprefix"] = True
            elif _PERSON_HINT.search(act) and (hand_who or prev_who):
                _w = hand_who if (_BODY_START.match(act) or re.search(r"手|指尖|掌心", act)) and hand_who else prev_who
                b["who"] = _w
                if _BODY_START.match(act):
                    b["act"] = short_name(_w) + "的" + act
                b["_noprefix"] = True
        if b.get("who"):
            prev_who, prev_multi = b["who"], list(b.get("_multi") or [])
            if _HAND_ACT.search(str(b.get("act") or "")) and not str(b.get("act") or "").startswith("说话"):
                hand_who = b["who"]
    return beats


_PLACEHOLDER = re.compile(r"无具体动作|接上一拍|无动作|^做什么|^（?同上|^继续说|^说话$|^开口$")


def merge_placeholder_says(beats):
    """P389①：做什么是占位（无具体动作描述，接上一拍）的拍——有台词就并回上一拍（同一人、上一拍没台词），并不回去的做什么改成「说话」；没台词的删。"""
    out = []
    for b in beats:
        if _PLACEHOLDER.search(str(b.get("act") or "")):
            if b.get("say"):
                prev = out[-1] if out else None
                if prev is not None and prev.get("who") == b.get("who") and not prev.get("say") and prev.get("place") == b.get("place"):
                    prev["say"] = b["say"]
                    if not prev.get("react") and b.get("react"):
                        prev["react"] = b["react"]
                    if b.get("shot") == "特写" and prev.get("shot") != "特写":
                        pass                                            # 景别照上一拍的动作
                    continue
                b = dict(b, act="说话")
            else:
                continue
        out.append(b)
    return out


def shots_table(text, scene_names, char_names, q, pace="适中", debug_path=""):
    """口述拍法 → 切片。模型只做「口述 → 拍表」，其余程序做。"""
    ins = dictation_instruction(scene_names, char_names)
    _names = [n for n, _, _ in _char_items(char_names)]
    _al = aliases_of(_names)
    for w, nm in role_aliases(char_names).items():
        _al.setdefault(w, nm)                                           # P384：少年/老人 这类称呼 → 卡名
    _sx = {n: sx for n, sx, _ in _char_items(char_names)}
    raw = q(ins, "【口述】\n" + str(text or ""), mt=3000, temperature=0.3)
    beats = parse_beats_loose(raw, scene_names, _names, _al, _sx)
    if len(beats) < 2:
        raw = q(ins, "【口述】\n" + str(text or "") + "\n\n（严格按格式，每个镜头一行）", mt=3000, temperature=0.5)
        beats = parse_beats_loose(raw, scene_names, _names, _al, _sx)
    char_names = _names
    beats = resolve_places(beats, scene_names, q)
    shots_table.last_raw = raw
    if debug_path:
        try:
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(ins + "\n\n===== 模型输出 =====\n" + raw + "\n\n===== 解析后 =====\n" + json.dumps(beats, ensure_ascii=False, indent=1))
        except Exception:
            pass
    return normalize_beats(beats, char_names, pace)

