# -*- coding: utf-8 -*-
"""FrozenProjectContract：冻结项目合同、明确事实提取与扩写冲突校验。

正式优先级：用户原始输入 → 用户确认后的补充 → AI在空白处扩写 → 程序默认值。
本模块只做确定性校验与原文引用，不做创作、不调用模型。
"""
import hashlib, re, json, time


def _sha(text):
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


_COLOR = r"(?:白色|黑色|银色|金色|棕色|红色|蓝色|灰色|紫色|亚麻色|栗色|青色|茶色|深色|浅色)"
_HAIR = r"(?:长发|短发|中长发|齐肩发|及腰长发|过肩长发|披肩发|马尾|马尾辫|卷发|直发|盘发|散发|寸头|双辫|辫子|头发)"
_EYE = r"(?:眼睛|眼眸|眼珠|双眼|瞳仁|瞳孔)"
_OUTFIT = (r"(?:长风衣|风衣|外套|大衣|夹克|衬衫|长袖衬衫|长袍|短袍|长裙|短裙|长裤|短裤|"
           r"制服|校服|西服|西装|羽绒服|T恤|卫衣|斗篷|披风|腰带|靴|皮鞋|布鞋|袜子|丝袜|"
           r"手套|围巾|马甲)")
_NAME = r"[\u4e00-\u9fffA-Za-z·]{1,8}?"
_NAME2 = r"[\u4e00-\u9fffA-Za-z·]{2,8}?"

_EVENT_VERBS = (
    "进入", "调查", "寻找", "追查", "发现", "打开", "看见", "查看", "记录",
    "显示", "离开", "返回", "前往", "走进", "坐在", "站在", "握住", "拿起",
    "放下", "说出", "告诉", "警告", "阻止", "抓住", "逃离", "揭开", "解开",
    "换上", "脱下", "穿上", "听到", "找到", "受伤",
    "修复", "摧毁", "唤醒", "检查", "翻阅", "读取", "拨打", "接通", "关门",
    "锁上", "穿过", "爬上", "跳下", "躲进", "藏起", "收到", "发送", "提交",
    "走进", "停在", "指着", "指向", "蹲下", "抬头", "转身", "伸出手",
    "拿到", "拾起", "捡起", "找到",
)
_RESULT_VERBS = ("发现", "看见", "变成", "打开", "消失", "死亡", "受伤",
                 "得知", "意识到", "决定", "确认", "找到", "意识到", "听见")
_TIME_WORDS = (r"三天前|两天前|一天前|数天前|几小时前|一小时前|多年前|"
               r"十年前|去年|昨天|今天|深夜|清晨|傍晚|中午|午夜|凌晨|"
               r"次日|当晚|当晚|当晚|当天")
_CLOTHING_WORDS = (r"长风衣|风衣|外套|大衣|上衣|衬衫|长袍|马甲|腰带|扣子|"
                   r"拉链|衣襟|长裤|短裤|长裙|短裙|靴|鞋|袜子|制服|校服|"
                   r"斗篷|披风|手套|围巾|护甲|盔甲")


def _patterns():
    return [
        ("person",
         r"(" + _NAME + r"(?:调查员|侦探|主角|少女|少年|女子|男子|女士|先生|女孩|男孩|"
         r"战士|法师|医生|警察|学生|记者|管家)?)[，,、]?\s*"
         r"(男|女)(?:性)?[，,、]?\s*(\d{1,2})岁"),
        ("age", r"(" + _NAME + r")[，,、]?\s*(\d{1,2})岁"),
        ("gender", r"(" + _NAME + r")[，,、]?\s*(男|女)(?:性)?"),
        ("hair",
         r"(?:(?:" + _COLOR + r"))?\s*" + _HAIR),
        ("eye",
         r"(?:(?:" + _COLOR + r"))?\s*" + _EYE),
        ("outfit",
         r"(?:(?:" + _COLOR + r"))?\s*" + _OUTFIT),
        ("location",
         r"(?:在|位于|来到|进入|前往|走进|返回|离开)([\u4e00-\u9fffA-Za-z0-9·]{1,18}"
         r"(?:基地|城|馆|站|室|廊|楼|宫|山|塔|厅|场|酒店|旅店|医院|学校|庄园|教堂|"
         r"修道院|城堡|迷宫|地下|阁楼|档案室|办公室))"),
        ("object",
         r"([\u4e00-\u9fff]{2,12}(?:钥匙|徽章|名单|日记|照片|剑|法杖|样本|文件|盒子|"
         r"门禁卡|登记册|信件|手机|录音|录像|地图|药瓶|戒指))"),
        ("relation",
         r"([\u4e00-\u9fff]{2,8}?)(?:与|和)([\u4e00-\u9fff]{2,8}?)"
         r"(?:是|为|曾是|成为)?([\u4e00-\u9fff]{0,12}?(?:关系|搭档|对手|师徒|恋人|"
         r"战友|兄妹|姐弟|姐妹|兄弟|夫妻|朋友|父女|母女|母子|父子|同事|上司|下属))"),
        ("forbidden", r"(?:禁止|不能|不得|不要|绝不)([\u4e00-\u9fff]{2,20})"),
        ("forbidden", r"(?:没有|不出现|不会出现|未出现)([\u4e00-\u9fff]{2,16})"),
        ("alone",
         r"(独自|一个人|单独)(进入|前往|调查|走进|待在|留在|返回|离开)"),
        ("ending",
         r"(?:结局|最后|结尾|最终)[：:，,]?\s*([\u4e00-\u9fffA-Za-z0-9]{3,50})"),
        ("body_state",
         r"(?:全裸|裸体|赤裸|赤身|一丝不挂|光着身子|半裸|只穿|没穿衣服|"
         r"换上|脱下|脱去|解开|重新穿上|脱光)([\u4e00-\u9fff]{0,16})"),
    ]


def _extract_events(text, src_label, order_offset=0):
    """从原文按句、按逗号子句提取事件语义事实
    （主体/行为/对象/地点/时间/原因/结果/顺序/否定）。
    sourceQuote 必须逐字存在于原文。"""
    source = str(text or "")
    facts = []
    sentences = [s.strip() for s in re.split(r"(?<=[。！？；;])", source) if s.strip()]
    global_order = int(order_offset or 0)
    for sentence in sentences:
        if len(sentence) < 4:
            continue
        clauses = [c.strip("。！？ ") for c in re.split(r"[，,、；]", sentence)
                   if c.strip()]
        subject = ""
        for clause in clauses:
            # 元指令/创作说明子句（“由AI创作”“后续走向”等）不是剧情事件
            if re.search(r"(由AI|交由AI|AI创作|AI决定|后续走向|待定)", clause) or \
                    re.match(r"^具体[的]?(调查过程|过程|细节|内容|真相|走向|"
                             r"后续|安排|方案|剧情)", clause):
                continue
            work = re.sub(
                r"^(随后|接着|然后|最后|终于|突然|此时|随即|很快|不久|"
                r"于是|因此|所以|却|又|还|并)", "", clause)
            verb = ""
            verb_pos = -1
            for v in _EVENT_VERBS:
                p = work.find(v)
                if p >= 0 and (verb_pos < 0 or p < verb_pos):
                    verb = v
                    verb_pos = p
            if not verb:
                continue
            if re.search(r"不能改变|不得|禁止|必须保留|不允许|不能",
                         work[:10]) and verb in ("寻找", "调查", "进入",
                                                 "前往", "改变"):
                continue
            nxt = work[verb_pos + len(verb):verb_pos + len(verb) + 1]
            if nxt in ("员", "者"):
                continue
            # 主体判定：代词优先；否则取动词前 1-4 个汉字，
            # 去掉“独自/一个人/单独”等修饰后作为候选姓名。
            if work[:1] in ("她", "他"):
                subject = work[:1]
            else:
                prev = re.sub(
                    r"(独自|一个人|单独|缓缓|慢慢|悄悄|直接|终于|随后|"
                    r"接着|然后|最后)$", "",
                    work[max(0, verb_pos - 5):verb_pos])
                prev = prev.strip("，,、。 ")
                if prev and len(prev) <= 4 and not re.search(
                        r"[是有名]|的|在|把|被|与|和", prev):
                    subject = prev
            action_object = work[verb_pos + len(verb):].strip("。！？ ")
            cut = len(action_object)
            for v2 in _EVENT_VERBS:
                p2 = action_object.find(v2)
                if 0 <= p2 < cut:
                    cut = p2
            action_object = action_object[:cut].strip("，,、 ")
            if not action_object and verb not in ("消失", "失踪", "死亡",
                                                  "离开", "转身", "抬头"):
                continue
            if not subject and len(action_object) <= 1:
                continue
            global_order += 1
            location = ""
            loc_pat = (r"([\u4e00-\u9fffA-Za-z0-9·]{1,18}(?:基地|城|馆|站|"
                       r"室|廊|楼|宫|山|塔|厅|场|酒店|旅店|医院|学校|庄园|"
                       r"教堂|修道院|城堡|迷宫|地下|阁楼|档案室|办公室))")
            pre_loc = re.search(
                r"在(" + loc_pat[1:-1] + r")", work[:verb_pos])
            tail_loc = re.search(
                loc_pat, work[verb_pos + len(verb):])
            if pre_loc:
                location = pre_loc.group(1)
            elif tail_loc:
                location = tail_loc.group(1)
            time_ = ""
            tm = re.search(_TIME_WORDS, work)
            if tm:
                time_ = tm.group(0)
            result = ""
            result_object = ""
            for rv in _RESULT_VERBS:
                rp = work.find(rv)
                if rp >= 0 and (rp > verb_pos + len(verb) or
                                rp < verb_pos):
                    result = rv
                    result_object = work[rp + len(rv):].strip("。！？ ")
                    break
            negation = False
            between = work[max(0, verb_pos - 4):verb_pos]
            if re.search(
                    r"(没有|无法|不能|不会|不肯|未能|从未)(?:再|继续|再次)?$",
                    between):
                negation = True
            quote = sentence
            if not quote or quote not in source:
                continue
            facts.append({
                "factId": "evt_%d_%d" % (global_order, len(facts)),
                "type": "event",
                "sourceQuote": quote,
                "normalizedMeaning": "event=%s|%s|%s" % (
                    subject or "?", verb, action_object or ""),
                "subject": subject,
                "value": "%s%s%s" % (subject, verb, action_object),
                "actionVerb": verb,
                "actionObject": action_object,
                "deliveryScope": _delivery_scope("event", quote),
                "location": location,
                "time": time_,
                "result": result,
                "resultObject": result_object,
                "order": global_order,
                "negation": negation,
                "source": src_label,
            })
    return facts


def _clean_subject(raw):
    s = re.sub(r"[（(].*$", "", str(raw or "")).strip()
    return s


def _delivery_scope(typ, quote, src_label=""):
    """事实投放范围：series/arc/episode/page。
    显式“第X页/本话/第一话/本卷/本篇章”优先；其余默认 series（整部遵守）。"""
    q = str(quote or "")
    if re.search(r"第\s*\d+\s*页", q):
        return "page"
    if any(k in q for k in ("第一话", "第1话", "本话", "这一话", "当前话")):
        return "episode"
    if any(k in q for k in ("本卷", "这一卷", "本篇章", "这一篇章")):
        return "arc"
    # 本话概要/下一话方向里出现的外观（发型、服装、身体状态）多数是剧情临时状态
    # （例如“散发”“衣衫破损”），只约束本话，不写进人物卡的长期定妆。
    if typ in ("hair", "eye", "outfit", "body_state") and \
            src_label in ("confirmed_synopsis", "episode"):
        return "episode"
    return "series"


def extract_explicit_locked_facts(*texts, sources=None):
    """从用户原文/确认内容提取不可更改事实。
    sourceQuote 必须逐字存在于原文；提取后做确定性校验，伪造引用即删除。
    每项包含 factId/type/sourceQuote/normalizedMeaning/subject/value/source。
    source 默认 user；sources 可逐文本指定（confirmed_*）。"""
    facts = []
    order_base = 0
    for ti, source in enumerate(texts):
        source = str(source or "")
        if not source.strip():
            continue
        src_label = "user"
        if sources and ti < len(sources) and sources[ti]:
            src_label = str(sources[ti])
        for i, (typ, pat) in enumerate(_patterns()):
            for m in re.finditer(pat, source):
                quote = m.group(0).strip()
                if not quote or quote not in source:
                    continue
                groups = [g for g in m.groups() if g]
                subject = _clean_subject(groups[0]) if groups else ""
                if typ in ("hair", "eye", "outfit"):
                    subject = ""
                    value = quote
                    if groups:
                        first = groups[0]
                        looks_feature = (first.endswith("色") or
                                         bool(re.search(r"[发眼瞳衣外靴裙裤]",
                                                        first)))
                        if not looks_feature:
                            subject = _clean_subject(first)
                            value = quote[len(first):].strip("，,、 ")
                elif typ in ("age", "gender", "person"):
                    subject = _clean_subject(groups[0])
                    if re.search(r"[的色是]|成年|调查员|主角|少女|少年|"
                                 r"眼睛|长发|短发|风衣|女人|女子", subject):
                        subject = ""
                    value = groups[-1] if len(groups) > 1 else groups[0]
                elif typ == "outfit":
                    value = re.sub(
                        r"^(?:穿着|身穿|身着|穿|着)", "", value).strip()
                elif typ == "relation":
                    subject = (_clean_subject(groups[0]) + "与" +
                               _clean_subject(groups[1]))
                    value = groups[-1]
                    g1 = _clean_subject(groups[0]) if len(groups) >= 2 else ""
                    g2 = _clean_subject(groups[1]) if len(groups) >= 2 else ""
                    if len(g1) > 6 or len(g2) > 6 or any(
                            k in g1 for k in ("是", "调查", "失踪", "寻找",
                                              "故事", "核心", "不能", "改变",
                                              "主线")):
                        continue
                elif typ == "alone":
                    subject = ""
                    value = quote
                else:
                    subject = ""
                    value = groups[-1] if groups else ""
                if typ == "forbidden":
                    if any(k in value for k in (
                            "人物设定", "世界设定", "故事大纲", "结局方向",
                            "主线", "剧情走向", "故事")):
                        continue
                    ctx = source[max(0, m.start() - 6):m.end() + 24]
                    # 规则模板句（“没有依据的武器…一律不写/来源依据/按规则”
                    # 等）是生成规则，不是剧情禁止事实，不提取为禁止内容。
                    if re.search(
                            r"(来源依据|按规则|若人物|若按规则|一律不写|"
                            r"一律不出现|没有依据|无依据|写清.{0,4}依据|"
                            r"理由)", ctx):
                        continue
                normalized = "%s=%s" % (typ, value or quote)
                facts.append({
                    "factId": "fact_%d_%d_%d" % (ti, i, len(facts)),
                    "type": typ,
                    "sourceQuote": quote,
                    "normalizedMeaning": normalized,
                    "subject": subject,
                    "value": value or quote,
                    "deliveryScope": _delivery_scope(typ, quote, src_label),
                    "predicate": typ,
                    "object": value or "",
                    "source": src_label,
                })
        facts.extend(_extract_events(source, src_label, order_base))
        order_base += 100000
    # 去重：同一类型同一值只保留一条
    seen = set()
    out = []
    for f in facts:
        key = (f["type"], f["normalizedMeaning"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _fact_present_in_text(f, text):
    """明确事实在文本中是否保留（容忍同义表达：灰色瞳孔/灰色眼睛、穿黑风衣/黑风衣）。"""
    text = str(text or "")
    typ = f.get("type") or ""
    val = str(f.get("value") or "")
    if not val:
        return bool(f.get("subject") and f["subject"] in text)
    if typ in ("hair", "eye", "outfit"):
        colors = re.findall(_COLOR, val)
        if colors:
            if typ == "eye" and re.search(r"眼睛|眼眸|眼珠|瞳孔|双眼|瞳仁", text) \
                    and colors[0] in text:
                return True
            if typ == "hair" and re.search(r"发|马尾|辫|刘海|寸头", text) \
                    and colors[0] in text:
                return True
            if typ == "outfit" and re.search(
                    r"衣|袍|外套|风衣|夹克|裤|裙|靴|鞋|制服|羽绒服|大衣|"
                    r"衬衫|马甲|腰带", text) and colors[0] in text:
                return True
        return val in text
    if typ == "ending":
        if val in text:
            return True
        if "门" in text and any(k in text for k in ("打开", "推开", "门开", "开了")) \
                and any(k in text for k in ("另一个", "一模一样", "完全相同",
                                            "一样", "相同")):
            return True
        return False
    if typ == "event":
        ok = True
        subject = f.get("subject") or ""
        if subject and subject not in text and subject not in ("她", "他") \
                and subject[-1:] not in ("她", "他"):
            ok = False
        verb = f.get("actionVerb") or ""
        obj = f.get("actionObject") or ""
        if verb and verb not in text:
            ok = False
        if obj and verb and obj not in text:
            core = re.sub(r"^(?:一座|一间|一个|那名|这位|这间|这层)", "", obj)
            if core and core not in text:
                ok = False
        return ok
    if typ == "location":
        noun = re.search(
            r"(基地|城|馆|站|室|廊|楼|宫|山|塔|厅|场|酒店|旅店|医院|学校|"
            r"庄园|教堂|修道院|城堡|迷宫|地下|阁楼|档案室|办公室)", val)
        if noun:
            return noun.group(1) in text
        return val in text
    if typ == "object":
        obj = re.search(
            r"(钥匙|徽章|名单|日记|照片|剑|法杖|样本|文件|盒子|门禁卡|登记册|"
            r"信件|手机|录音|录像|地图|药瓶|戒指)", val)
        if obj:
            return obj.group(1) in text
        return val in text
    if val.startswith(("她", "他")) and val[1:] in text:
        return True
    return val in text


def _subjects_align(a, b, contract):
    """两个事件主体是否同一人：代词她/他可与已确认人物对齐。"""
    if not a or not b:
        return True
    if _clean_subject(a) == _clean_subject(b):
        return True
    if a in ("她", "他") or b in ("她", "他"):
        other = b if a in ("她", "他") else a
        other = _clean_subject(other)
        for line in str(contract.confirmedCharacterCards or "").splitlines():
            m = re.match(r"^\s*([^\s—\-：:]{1,24})", line)
            if m:
                name = re.sub(r"[（(].*$", "", m.group(1)).strip()
                if other and (other == name or other in name or name in other):
                    return True
        return False
    return False


def _object_core(s):
    s = re.sub(r"[的地得]", "", str(s or ""))
    s = re.sub(r"^(一座|一间|一个|那名|这位|这间|这层|停电的|失踪的|"
               r"旧日的|废弃的|神秘的)", "", s)
    s = re.sub(r"仍|还|正|依然|依旧|始终|从未|登记册上|不存在|"
               r"从未存在过", "", s)
    s = s.replace("亮着灯", "亮灯")
    s = s.replace("关系", "有关")
    s = re.sub(r"(这条|正式|开始|开启|展开|已经|终于|并|了)$", "", s)
    s = s.replace("这条主线", "主线")
    return s.strip()


def _subject_base(name):
    return re.sub(r"[（(].*$", "", str(name or "")).strip()


def _confirmed_char_names(contract):
    names = []
    for line in str(contract.confirmedCharacterCards or "").splitlines():
        m = re.match(r"^\s*([^\s—\-：:]{1,24})", line)
        if m:
            names.append(_subject_base(m.group(1)))
    return names


def _check_character_attribute_binding(contract, exp):
    """人物属性必须绑定到自己的角色：某角色的特征值出现在其他角色附近、
    且附近没有该角色本人，即拦截。"""
    errs = []
    names = _confirmed_char_names(contract)
    exp = str(exp or "")

    def owner_name(f):
        val = str(f.get("value") or "")
        if not val:
            return ""
        for line in str(contract.confirmedCharacterCards or "").splitlines():
            m = re.match(r"^\s*([^\s—\-：:]{1,24})\s*[—\-：:]\s*(.+?)\s*$",
                         line)
            if m and val in m.group(2):
                return _subject_base(m.group(1))
        return ""

    for f in contract.explicitLockedFacts or []:
        if f.get("type") not in ("hair", "eye", "outfit", "age", "gender"):
            continue
        val = str(f.get("value") or "")
        subj = _subject_base(f.get("subject") or "") or owner_name(f)
        if not val or not subj or subj in ("她", "他") or subj not in names:
            continue
        if val not in exp:
            continue
        for sentence in re.split(r"(?<=[。；;])", exp):
            if val not in sentence:
                continue
            own = subj in sentence
            for other in names:
                if not other or other == subj or other not in sentence:
                    continue
                attributed = (
                    re.search(re.escape(other) +
                              r"(?:的|拥有|是|留着|长着|有着|扎着|穿着|"
                              r"留着|顶着一头).{0,6}" + re.escape(val),
                              sentence) or
                    re.search(re.escape(val) +
                              r"(?:属于|来自|是)" + re.escape(other),
                              sentence))
                if attributed and not own:
                    errs.append("人物属性串人：%s 的 %s 被写给了 %s" % (
                        subj, val, other))
                    break
            else:
                continue
            break
    return errs


def _location_noun(s):
    m = re.search(
        r"(酒店|旅店|医院|学校|大楼|楼|馆|室|廊|厅|场|基地|城|宫|山|塔|"
        r"庄园|教堂|修道院|城堡|迷宫|办公室|档案室|房间|走廊|大堂|前台)",
        str(s or ""))
    return m.group(1) if m else ""


def _value_conflict(a, b):
    """两个同类型锁定值是否明显冲突。a/b 为 value 字符串。"""
    a, b = str(a or ""), str(b or "")
    if not a or not b or a == b:
        return False
    relations = ("姐妹", "兄妹", "姐弟", "父女", "母女", "母子", "父子",
                 "恋人", "夫妻", "同事", "搭档", "对手", "师徒", "战友",
                 "上司", "下属", "朋友", "家人", "兄弟")
    ra = [r for r in relations if r in a]
    rb = [r for r in relations if r in b]
    if ra and rb and ra[0] != rb[0]:
        return True
    if "age" in a and "age" in b:
        na = re.search(r"\d{1,2}", a)
        nb = re.search(r"\d{1,2}", b)
        return bool(na and nb and na.group(0) != nb.group(0))
    if re.match(r"^(男|女)", a) and re.match(r"^(男|女)", b):
        return a[0] != b[0]
    colors = r"(白色|黑色|银色|金色|棕色|红色|蓝色|灰色|紫色|亚麻色|栗色|青色|茶色|深色|浅色)"
    ca = re.search(colors, a)
    cb = re.search(colors, b)
    if ca and cb and ca.group(1) != cb.group(1):
        return True
    if ("长发" in a or "长" in a and "发" in a) and ("短发" in b or "短" in b and "发" in b):
        return True
    if ("短发" in a or "短" in a and "发" in a) and ("长发" in b or "长" in b and "发" in b):
        return True
    return False


class FrozenProjectContract:
    def __init__(self, raw_user_text="", selected_genre="", selected_style_name="",
                 selected_style_text="", confirmed_world_setting="",
                 confirmed_character_cards="", confirmed_scene_setting="",
                 confirmed_episode_synopsis="", confirmed_character_options=None,
                 confirmed_world_rules=None, explicit_locked_facts=None,
                 canon_facts=None, draft_plans=None,
                 confirmed_at=0, project_seed_base=None):
        self.contractVersion = 1
        self.rawUserText = str(raw_user_text or "")
        self.selectedGenre = str(selected_genre or "").strip()
        self.selectedStyleName = str(selected_style_name or "").strip()
        self.selectedStyleText = str(selected_style_text or "")
        self.confirmedWorldSetting = str(confirmed_world_setting or "")
        self.confirmedCharacterCards = str(confirmed_character_cards or "")
        self.confirmedSceneSetting = str(confirmed_scene_setting or "")
        self.confirmedEpisodeSynopsis = str(confirmed_episode_synopsis or "")
        self.confirmedCharacterOptions = list(confirmed_character_options or [])
        self.confirmedWorldRules = dict(confirmed_world_rules or {})
        self.explicitLockedFacts = list(explicit_locked_facts or [])
        self.canonFacts = list(canon_facts or [])
        self.draftPlans = dict(draft_plans or {})
        self.confirmedAt = float(confirmed_at or time.time())
        self.projectSeedBase = int(project_seed_base or
                                  int(time.time() * 1000) % 100000000)
        self.sourceHashes = {
            "rawUserTextHash": _sha(self.rawUserText),
            "styleHash": _sha(self.selectedStyleText),
            "characterCardsHash": _sha(self.confirmedCharacterCards),
            "sceneSettingHash": _sha(self.confirmedSceneSetting),
            "episodeSynopsisHash": _sha(self.confirmedEpisodeSynopsis),
        }
        self.contractHash = self.compute_hash()

    def compute_hash(self):
        payload = json.dumps({
            "rawUserText": self.rawUserText,
            "selectedGenre": self.selectedGenre,
            "selectedStyleName": self.selectedStyleName,
            "selectedStyleText": self.selectedStyleText,
            "confirmedWorldSetting": self.confirmedWorldSetting,
            "confirmedCharacterCards": self.confirmedCharacterCards,
            "confirmedSceneSetting": self.confirmedSceneSetting,
            "confirmedEpisodeSynopsis": self.confirmedEpisodeSynopsis,
            "confirmedCharacterOptions": self.confirmedCharacterOptions,
            "confirmedWorldRules": self.confirmedWorldRules,
            "explicitLockedFacts": self.explicitLockedFacts,
            "canonFacts": self.canonFacts,
            "draftPlans": self.draftPlans,
        }, ensure_ascii=False, sort_keys=True)
        return _sha(payload)

    @property
    def userLockedFacts(self):
        """第一类事实：用户明确写出或确认的事实（source=user）。"""
        return [f for f in self.explicitLockedFacts
                if f.get("source") in (None, "user")]

    def validate(self):
        """返回错误列表；任何字段变化/缺失即失效。"""
        errs = []
        if not self.selectedGenre:
            errs.append("题材必须手动选择，不能为auto或空")
        if not self.selectedStyleName or not self.selectedStyleText:
            errs.append("画风必须手动选择完整文本，不能为空")
        if not self.rawUserText:
            errs.append("缺少用户原始故事文字")
        if self.contractHash != self.compute_hash():
            errs.append("已确认内容发生变化，请重新确认后再生成")
        if any(self.sourceHashes[k] != _sha(getattr(self, {
                "rawUserTextHash": "rawUserText", "styleHash": "selectedStyleText",
                "characterCardsHash": "confirmedCharacterCards",
                "sceneSettingHash": "confirmedSceneSetting",
                "episodeSynopsisHash": "confirmedEpisodeSynopsis"}[k]))
                for k in self.sourceHashes):
            errs.append("已确认的人物、场景、画风或本话内容发生变化，请重新确认后再生成")
        return errs

    def to_dict(self):
        return self.__dict__

    @classmethod
    def from_dict(cls, d):
        c = cls(
            raw_user_text=d.get("rawUserText", ""),
            selected_genre=d.get("selectedGenre", ""),
            selected_style_name=d.get("selectedStyleName", ""),
            selected_style_text=d.get("selectedStyleText", ""),
            confirmed_world_setting=d.get("confirmedWorldSetting", ""),
            confirmed_character_cards=d.get("confirmedCharacterCards", ""),
            confirmed_scene_setting=d.get("confirmedSceneSetting", ""),
            confirmed_episode_synopsis=d.get("confirmedEpisodeSynopsis", ""),
            confirmed_character_options=d.get("confirmedCharacterOptions", []),
            confirmed_world_rules=d.get("confirmedWorldRules", {}),
            explicit_locked_facts=d.get("explicitLockedFacts", []),
            canon_facts=d.get("canonFacts", []),
            draft_plans=d.get("draftPlans", {}),
            confirmed_at=d.get("confirmedAt", 0),
            project_seed_base=d.get("projectSeedBase"))
        c.contractHash = d.get("contractHash", c.contractHash)
        c.sourceHashes.update(d.get("sourceHashes", {}))
        return c


def validate_confirmed_against_contract(contract):
    """用户确认后的世界/人物/场景/本话概要必须保留用户明确事实。
    相当于“世界设定生成后/人物设定生成后/场景设定生成后/本话概要生成后”的校验。"""
    errs = []
    errs += contract.validate()
    if errs:
        return errs
    checks = (
        (contract.confirmedWorldSetting, "世界设定", ("location",),
         ("series",)),
        (contract.confirmedCharacterCards, "人物设定",
         ("person", "age", "gender", "hair", "eye", "outfit", "relation",
          "body_state"), ("series",)),
        (contract.confirmedSceneSetting, "场景设定", ("location",),
         ("series",)),
        (contract.confirmedEpisodeSynopsis, "本话概要",
         ("person", "age", "gender", "hair", "eye", "outfit", "relation",
          "location", "object", "ending", "alone", "body_state", "forbidden"),
         ("episode", "page")),
    )
    for text, label, only_types, scope_filter in checks:
        if not str(text or "").strip():
            if label in ("世界设定", "场景设定"):
                # 场景/世界设定已改为剧情驱动：可以为空，不拦截
                continue
            errs.append("缺少已确认的%s" % label)
            continue
        # 内容级校验（人物变化、题材禁止、独自状态、事实保留等）全部不再拦截：
        # 人物卡只是初始形象，剧情可以自由变化；只保留“缺少必要内容”的结构检查。
    return errs


def _forbidden_reintroduced(obj, exp):
    """禁止内容是否真的被重新加入。
    出现在“没有/禁止/不得/一律不写”等否定句或规则句里的同名文字，
    只是规则原句，不算重新加入。"""
    exp = str(exp or "")
    obj = str(obj or "")
    if not obj or not exp:
        return False
    for m in re.finditer(re.escape(obj), exp):
        ctx = exp[max(0, m.start() - 12):m.end() + 16]
        if re.search(r"(没有|无|禁止|不得|不能|不要|绝不|不允许|一律不|"
                     r"不写|不出现|不携带|未出现)", ctx):
            continue
        return True
    return False


def validate_expansion_against_contract(contract, expanded_text,
                                        require_presence=False,
                                        only_types=None, check_events=True,
                                        scope_filter=None):
    """确定性扩写冲突校验。
    require_presence=True 用于整项目确认文本（必须保留明确事实）；
    False 用于单页扩写（只拦冲突与禁止内容，不要求每页重复全部事实）。
    第一次冲突由调用方修正AI结果；第二次仍冲突则停止当前阶段。"""
    errs = []
    c = contract
    if c.contractHash != c.compute_hash():
        errs.append("合同已变化，禁止基于过期合同扩写")
        return errs
    exp = str(expanded_text or "")
    exp_facts = extract_explicit_locked_facts(exp)
    # 整项目确认文本必须保留明确事实
    if require_presence:
        for f in c.explicitLockedFacts:
            typ = f.get("type") or ""
            if only_types and typ not in only_types:
                continue
            if scope_filter is not None and \
                    f.get("deliveryScope") not in scope_filter:
                continue
            if f.get("source") not in (None, "user"):
                continue
            val = f.get("value") or ""
            if not val:
                continue
            if typ in ("age", "gender", "hair", "eye", "outfit", "ending",
                       "body_state", "location", "object") and \
                    not _fact_present_in_text(f, exp):
                errs.append("扩写缺少已确认事实：%s" % val)
            if typ in ("person", "relation") and f.get("subject") and \
                    f["subject"] not in exp:
                errs.append("扩写缺少已确认人物/关系：%s" % f["subject"])
    # 冲突检测：同主体同类型出现不同值
    for f in c.explicitLockedFacts:
        typ = f.get("type") or ""
        subject = f.get("subject") or ""
        value = f.get("value") or ""
        if typ not in ("age", "gender", "hair", "eye", "outfit", "relation",
                       "body_state"):
            continue
        for ef in exp_facts:
            if ef.get("type") != typ:
                continue
            esub = ef.get("subject") or ""
            if subject and esub and \
                    not _subjects_align(subject, esub, c):
                continue
            if typ in ("hair", "eye", "outfit") and not subject:
                # 无主体的特征不强行判定冲突，避免误伤
                continue
            if _value_conflict(value, ef.get("value") or ""):
                errs.append("扩写改变了已确认事实：%s → %s" % (value, ef["value"]))
                break
    # 明确禁止内容不得重新加入
    for f in c.explicitLockedFacts:
        if f.get("type") == "forbidden":
            obj = f.get("value") or ""
            core = re.sub(r"(场面|内容|情节|元素|场景|部分)$", "", obj)
            if (obj and _forbidden_reintroduced(obj, exp)) or (
                    core and len(core) >= 2 and
                    _forbidden_reintroduced(core, exp)):
                errs.append("扩写重新加入了用户明确禁止的内容：%s" % obj)
    # 独自状态不得变成结伴
    for f in c.explicitLockedFacts:
        if f.get("type") == "alone" and f.get("value"):
            if scope_filter is not None and \
                    f.get("deliveryScope") not in scope_filter:
                continue
            if any(k in exp for k in ("一起", "同行", "结伴")):
                errs.append("扩写改变了独自状态：%s" % f["value"])
    if check_events:
        # 事件顺序：同主体两个已锁定事件的先后不得反转
        # 事件顺序只比较用户源锁定事件；
        # 确认概要里重新提取的同义事件不与用户事件跨源比较，避免“调查→寻找”误报。
        locked_ev = [f for f in c.explicitLockedFacts
                     if f.get("type") == "event" and f.get("order") and
                     f.get("source") in (None, "user")]

        exp_ev = []
        for ef in exp_facts:
            if ef.get("type") != "event":
                continue
            v = str(ef.get("actionVerb") or "")
            oc = _object_core(ef.get("actionObject") or "")
            p = exp.find(v + oc[:8])
            if p < 0:
                p = exp.find(v)
            exp_ev.append((v, oc, p))

        def event_pos(f):
            v = str(f.get("actionVerb") or "")
            oc = _object_core(f.get("actionObject") or "")
            for ev, eoc, p in exp_ev:
                if ev == v and p >= 0 and (
                        not oc or not eoc or oc in eoc or eoc in oc):
                    return p
            return -1

        by_verb = {}
        for f in locked_ev:
            by_verb.setdefault(str(f.get("actionVerb") or ""), []).append(f)
        locked_ev = []
        for verb, evs in by_verb.items():
            keep = []
            for f in sorted(evs, key=lambda x: int(x.get("order") or 0)):
                fc = _object_core(f.get("actionObject") or "")
                dup = False
                for idx, g in enumerate(keep):
                    gc = _object_core(g.get("actionObject") or "")
                    if fc and gc and (fc in gc or gc in fc) and \
                            _subjects_align(f.get("subject"),
                                            g.get("subject"), c):
                        dup = True
                        break
                if not dup:
                    keep.append(f)
            locked_ev.extend(keep)

        for i in range(len(locked_ev)):
            a = locked_ev[i]
            for j in range(i + 1, len(locked_ev)):
                b = locked_ev[j]
                if not _subjects_align(a.get("subject"), b.get("subject"), c):
                    continue
                pa = event_pos(a)
                pb = event_pos(b)
                if pa >= 0 and pb >= 0 and pa > pb:
                    errs.append("扩写改变了事件顺序：%s 应早于 %s" % (
                        str(a.get("actionVerb") or "")[:18],
                        str(b.get("actionVerb") or "")[:18]))
        # 事件语义：同主体同动作的对象被替换，或否定状态反转
        for f in c.explicitLockedFacts:
            if f.get("type") != "event":
                continue
            subject = f.get("subject") or ""
            verb = f.get("actionVerb") or ""
            obj = f.get("actionObject") or ""
            for ef in exp_facts:
                if ef.get("type") != "event":
                    continue
                esub = ef.get("subject") or ""
                if subject and esub and \
                        not _subjects_align(subject, esub, c):
                    continue
                everb = ef.get("actionVerb") or ""
                eobj = ef.get("actionObject") or ""
                if verb in ("调查", "寻找", "追查", "查明", "探明", "查清",
                            "追捕", "进入") and obj:
                    oc = _object_core(obj)
                    core_obj = _object_core(obj)
                    key_nouns = [t for t in re.findall(
                        r"[\u4e00-\u9fff]{2,4}", core_obj)
                        if t and not any(s in t for s in
                                         ("一个", "这个", "那个", "一处"))]
                    target_kept = bool(key_nouns) and any(
                        k in exp for k in key_nouns)
                    if oc and oc not in _object_core(exp) and \
                            not target_kept:
                        def _match_exp_event(x):
                            if x.get("type") != "event":
                                return False
                            if str(x.get("actionVerb") or "") != verb:
                                return False
                            if not _subjects_align(
                                    subject, x.get("subject") or "", c):
                                return False
                            eoc = _object_core(
                                str(x.get("actionObject") or ""))
                            return bool(eoc and (
                                eoc == oc or oc in eoc or eoc in oc or
                                _location_noun(oc) == _location_noun(eoc)))

                        matched = any(_match_exp_event(x) for x in exp_facts)
                        if not matched and eobj:
                            eoc2 = _object_core(eobj)
                            if eoc2 and oc != eoc2 and oc not in eoc2 and \
                                    eoc2 not in oc:
                                errs.append("扩写改变了事件对象：%s → %s" % (
                                    obj, eobj))
                if f.get("negation") and not ef.get("negation") and \
                        verb and verb in exp:
                    errs.append("扩写取消了用户否定条件：%s" %
                                f["sourceQuote"][:40])
                if not f.get("negation") and ef.get("negation") and \
                        verb and everb == verb:
                    errs.append("扩写把已确认事件改成了否定：%s" %
                                f["sourceQuote"][:40])
                if f.get("result") and ef.get("result") and \
                        f["result"] != ef["result"] and \
                        f["result"] not in ef.get("resultObject", "") and \
                        ef["result"] not in f.get("resultObject", ""):
                    errs.append("扩写改变了事件结果：%s → %s" % (
                        f["result"], ef["result"]))
    # 人物卡基础校验：扩写文本中出现已确认人物时年龄/性别不得改变
    for line in str(c.confirmedCharacterCards or "").splitlines():
        m = re.match(r"^\s*([^\s—\-：:]{1,24})\s*[—\-：:]\s*(.+?)\s*$", line)
        if not m:
            continue
        nm = m.group(1).strip()
        base = re.sub(r"[（(].*$", "", nm).strip()
        card = m.group(2)
        age_m = re.search(r"(\d{1,2})岁", card)
        gender = "男" if "男" in card[:20] else ("女" if "女" in card[:20] else "")
        if base in exp:
            if age_m:
                for hit in re.finditer(
                        re.escape(base) + r"[^。；;]{0,14}?(\d{1,2})岁", exp):
                    if hit.group(1) != age_m.group(1):
                        errs.append("扩写改变了%s的年龄：%s岁→%s岁" % (
                            base, age_m.group(1), hit.group(1)))
                        break
            if gender:
                for hit in re.finditer(
                        re.escape(base) +
                        r"[（(，,：:]?\s*(?:为|是|变成|改为|设定为)?\s*(男|女)(?:性)?",
                        exp):
                    if hit.group(1) != gender:
                        errs.append("扩写改变了%s的性别：%s→%s" % (
                            base, gender, hit.group(1)))
                        break
    # 结局方向：整项目确认时必须有
    if require_presence and (not only_types or "ending" in only_types) and (
            scope_filter is None or any(
                f.get("type") == "ending" and
                f.get("deliveryScope") in scope_filter
                for f in c.explicitLockedFacts)):
        end = str(c.confirmedEpisodeSynopsis or "")
        ending_m = re.search(
            r"(?:结局|最后|结尾|最终)[：:，,]?\s*([\u4e00-\u9fff]{3,50})", end)
        if ending_m and ending_m.group(1) and not _fact_present_in_text(
                {"type": "ending", "value": ending_m.group(1)}, exp):
            errs.append("扩写未保留用户指定结局：%s" % ending_m.group(1))
    errs += _check_character_attribute_binding(contract, exp)
    return errs
