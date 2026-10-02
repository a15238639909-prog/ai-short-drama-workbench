# -*- coding: utf-8 -*-
"""screenplay_norm.py — 剧本格式规范化：不管模型怎么写，进下游前统一成标准格式。

【为什么要有这个文件】
剧本的行格式只有三种，下游（场景卡解析、导演分镜）全靠它认：

    （景别｜X秒）画面里发生什么      ← 镜头
    人名：台词原文                   ← 说出口的台词
    人名（心声）：心里想的            ← 内心独白

**行首的括号是"镜头"的唯一标志**。任何别的东西顶到行首带括号，都会被解析器
当成一个新镜头吃掉，它下面那行内容跟着一起丢。

指令词里已经把这三种行写死了，但模型每被禁掉一种写法就发明一种新的
（2026-08-27 三轮实测）：
    （无声）苏酥（心声）：好香……     ← 加了前缀
    （心声）\n她竟这般生动。          ← 标签单独成行，内容换行
    （画外音｜苏酥心声）\n师兄在乎的…  ← 换了个禁用词
    （全景｜4秒）\n玄色长袍的玄清…     ← 镜头和描述拆成两行
指令词已经 12KB，继续堆规则的边际效益在下降。格式这种确定性的东西
交给代码兜底，比祈祷模型守规矩可靠。

normalize() 只动格式，一个字的内容都不改。
"""
import re

# 认得出的景别词（和 scene_layer._FRAMINGS 保持一致的常见集合）
_FRAMINGS = ("大远景", "大全景", "远景", "全景", "中远景", "中景", "中近景",
             "近景", "大特写", "特写", "主观镜头", "特效镜头", "跟拍镜头", "空镜")

# 行首括号里只有景别/秒数 → 这是一个镜头头
_SHOT_HEAD = re.compile(r"^[（(]\s*([^）)]{1,40})\s*[）)]\s*(.*)$")
# 心声标签的各种变体：（心声）/（内心独白）/（画外音｜某某心声）/（无声）某某（心声）…
_VO_TAG = re.compile(r"^[（(]\s*(?:无声|画外音?|旁白|内心独白|心声|OS|os)"
                     r"(?:\s*[｜|、，,]\s*)?([^）)]{0,12}?)\s*(?:心声|内心独白)?\s*[）)]\s*(.*)$")
# 「人名（心声）：内容」——已经是标准写法
_VO_OK = re.compile(r"^([^\s（(：:]{1,12})[（(]\s*心声\s*[）)]\s*[：:]\s*(.+)$")
# 「人名：台词」
_LINE_SAY = re.compile(r"^([^\s（(：:]{1,12})\s*[：:]\s*(.+)$")
# 结构行
_STRUCT = re.compile(r"^(#|【)")


def _is_shot_head(inner):
    """括号里的内容像不像"景别｜秒数"。"""
    t = str(inner or "")
    if any(f in t for f in _FRAMINGS):
        return True
    return bool(re.search(r"[\d.]+\s*秒", t))


# 台词署名的方括号变体：`【凯尔】：追！`——指令词的格式示例用【甲】当占位符，
# 模型把方括号也当成了格式抄出来（2026-08-28 实测：整话 21 句台词因行首【
# 被当成结构行，台词锚整套失明）。连同`名：（心声）内容`的心声后置变体一起规范化。
_BRACKET_NAME = re.compile(r"^【([^】\n]{1,12})】\s*(（心声）)?\s*[：:]\s*", re.M)
# 「心声」被写成景别的变体：`（心声｜4秒）【凯尔】（心声）：内容` 整行会被当镜头行，
# 台词表收不到、校验反把它判成编造（2026-08-28 实测）。拆成标准两行。
_VO_AS_SHOT = re.compile(r"^（心声[｜|]([\d.]+)秒）\s*【?([^】\s（(：:]{1,12})】?"
                         r"（心声[^）]*）[：:]\s*(.+)$", re.M)
_VO_AFTER_COLON = re.compile(r"^([^\s（(：:【\n]{1,12})\s*[：:]\s*[（(]\s*心声\s*[）)]\s*", re.M)
# 「老对手（画外音）：挺漂亮的」——说出口的话，只是人不在画面里。标签去掉，当普通台词走（P158②）
_SAY_TAG_AFTER_NAME = re.compile(
    r"^([^\s（(：:【\n]{1,12})\s*[（(]\s*(?:画外音?|旁白|O\.?S\.?|os|V\.?O\.?|vo)\s*[）)]\s*[：:]\s*", re.M)
# 事实单/结构表的栏目名被抄进剧本（「在场：林舟」）——不是台词也不是镜头，整行删（P158①）
_FACT_HEAD = re.compile(r"^(在场|空间|布局|人物表|事实单|边界|落点|篇幅|基调|时间|地点|这一话)[：:]")
# 上一个镜头行末尾的动作短语被当成说话人（「眉头微：」「拿起手机：」）——认动作词（P158③）
_ACT_WORD = re.compile(r"(拿起|放下|抬|低头|转身|握|翻|皱|眉|摇|点头|推|拉|按|递|摸|叹|扶|举|指|瞥|盯|咬|吸|"
                       r"起身|坐下|站起|伸手|收回|捏|攥|甩|抹|擦|敲|踩|掏)")


_SHOT_SEC = re.compile(r"^([（(]\s*[^）)｜|]*[｜|]\s*)([\d.]+)(\s*秒[^）)]*[）)])")
_DLG_LINE = re.compile(r"^([^\s（(：:【#｜=─]{1,12})(（心声）)?[：:](.+)$")


def enforce_dialogue_seconds(body):
    """台词镜头的秒数按查表兜底重算（只加不减）。

    指令词里的查表（10字→3秒…41-50字→12秒）Qwen 基本不执行：
    实测 44 字台词标 5 秒、40 字标 5 秒——切段按这些数字算，每段被塞进
    约两倍内容，这就是"节奏比 GPT 快一倍"的真根（2026-08-28 复盘）。
    确定性计算交给代码：一个镜头下面所有台词行字数相加查表，
    标的秒数小于查表值就改成查表值；没台词的镜头不动。
    """
    lines = str(body or "").split("\n")
    out = list(lines)
    cur, chars = None, 0

    def _need(n):
        for cap, sec in ((10, 3), (20, 6), (30, 8), (40, 10), (50, 12)):
            if n <= cap:
                return sec
        return 14   # 单镜上限，再长该拆句（拆句是指令词的事）

    def _flush():
        nonlocal cur, chars
        if cur is not None and chars:
            s = lines[cur].strip()
            m = _SHOT_SEC.match(s)
            if m and float(m.group(2)) < _need(chars):
                out[cur] = _SHOT_SEC.sub(
                    lambda mm: mm.group(1) + "%g" % _need(chars) + mm.group(3), s, count=1)
        cur, chars = None, 0

    for i, raw in enumerate(lines):
        s = raw.strip()
        if _SHOT_SEC.match(s):
            _flush()
            cur = i
        elif re.match(r"^(#|【)", s):
            _flush()
        elif cur is not None:
            dm = _DLG_LINE.match(s)
            if dm:
                chars += len(re.sub(r"[^\w一-龥]", "", dm.group(3)))
    _flush()
    return "\n".join(out)


def _split_embedded_quotes(text, names, last_speaker=""):
    """镜头描述里夹着的「小豪盯着她：“这酒，烈吗？”。」→ 描述里删掉引号句，返回 (描述, [(说话人, 台词)…])。
    说话人：引号前 15 字内最近出现的名字；没有就用上一个说话人；再没有就不拆。"""
    t = str(text or "")
    if "“" not in t:
        return t, []
    names = [n for n in (names or []) if n]
    found = []
    def _rep(m):
        before = t[:m.start()]
        who = ""
        best = -1
        for n in names:
            k = before.rfind(n)
            if k > best and len(before) - k <= 20:
                best, who = k, n
        if not who:
            # 名单外的人（小女孩/卖包子老汉没有人物卡）：拿这一句的主语——句首或逗号后 2～4 字紧跟动作词（P136）
            _ms = list(re.finditer(r"(?:^|[。！？，；])\s*([一-龥]{2,4}?)(?=[踮说问答低抬转走站坐伸接看笑咬点摇喊叫回开皱眯抿深吸将把从])", before))
            _ms = [x for x in _ms if not re.match(r"^(?:一只|一双|一个|一名|两个|声音|目光|视线)$", x.group(1))]
            if _ms:
                who = _ms[-1].group(1)
        who = who or last_speaker
        if not who:
            return m.group(0)
        found.append((who, m.group(1).strip()))
        return "。"
    out = re.sub(r"[，,]?\s*(?:[说道问答喊叫低声轻声沉声冷冷开口]{0,4})?[：:]?\s*“([^”]{1,60})”[。！？]?\s*", _rep, t)
    out = re.sub(r"。{2,}", "。", out).strip("，, ")
    return out, found


def normalize(body, speakers=None):
    """把剧本正文规整成标准三行格式。只改格式，不改内容。

    speakers: 已知人物名列表，用来给漏了人名的心声行补上说话人。
    """
    names = [str(x).strip() for x in (speakers or []) if str(x or "").strip()]
    body = _VO_AS_SHOT.sub(lambda m: "（特写｜%s秒）%s神情微动，目光落在别处，心中闪过念头。\n"
                                     "%s（心声）：%s" % (m.group(1), m.group(2),
                                                        m.group(2), m.group(3)),
                           str(body or ""))
    body = _BRACKET_NAME.sub(lambda m: m.group(1) + (m.group(2) or "") + "：", body)
    body = _VO_AFTER_COLON.sub(r"\1（心声）：", body)
    body = _SAY_TAG_AFTER_NAME.sub(r"\1：", body)               # P158②
    # 「【卖艺人】台词」→「卖艺人：台词」（P136）。但【在场】【空间】【布局】【站位】是场景头的结构标签，
    # 下游画场景参考图只认方括号形式，动了就等于把这四行删了（P159③）。
    body = re.sub(r"(?m)^\s*【(?!在场|空间|布局|站位)([^】\n]{1,8})】\s*[：:]?\s*(?=\S)", r"\1：", body)
    lines = str(body or "").replace("\r\n", "\n").split("\n")
    out = []
    last_speaker = ""      # 最近出现过的说话人，心声漏名字时兜底
    last_shot_txt = ""     # 最近一个镜头的画面描述，用来从里面找人名

    def _guess_speaker(hint):
        """心声是谁的：括号里给了就用；否则从最近的镜头描述里找人名；再不行用上一个说话人。"""
        h = str(hint or "").strip()
        for n in names:
            if h and (h in n or n in h):
                return n
        # 镜头描述里最后出现的那个人名，通常就是这一拍的主体
        best, pos = "", -1
        for n in names:
            i = last_shot_txt.rfind(n)
            if i > pos:
                best, pos = n, i
        return best or last_speaker or (names[0] if names else "")

    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        i += 1
        if not line:
            out.append("")
            continue
        if _STRUCT.match(line):
            out.append(line)
            continue

        # ---- 已经是标准心声行 ----
        m = _VO_OK.match(line)
        if m:
            last_speaker = m.group(1)
            out.append("%s（心声）：%s" % (m.group(1), m.group(2).strip()))
            continue

        m = _SHOT_HEAD.match(line)
        if m:
            inner, rest = m.group(1).strip(), m.group(2).strip()

            # ---- 情况A：括号是心声标签的变体 ----
            vm = _VO_TAG.match(line)
            if vm and not _is_shot_head(inner):
                hint, rest2 = vm.group(1), vm.group(2).strip()
                # 「（无声）苏酥（心声）：好香」这种，rest2 里还带着人名和冒号
                inner_ok = _VO_OK.match(rest2) if rest2 else None
                if inner_ok:
                    who, txt = inner_ok.group(1), inner_ok.group(2).strip()
                else:
                    say = _LINE_SAY.match(rest2) if rest2 else None
                    if say:
                        who, txt = say.group(1), say.group(2).strip()
                    else:
                        txt = rest2
                        # 内容在下一行
                        while not txt and i < len(lines):
                            nxt = lines[i].strip()
                            i += 1
                            if not nxt:
                                continue
                            txt = nxt
                            break
                        who = _guess_speaker(hint)
                if txt:
                    last_speaker = who or last_speaker
                    out.append("%s（心声）：%s" % (who or "旁白", txt))
                continue

            # ---- 情况B：真的是镜头头 ----
            if _is_shot_head(inner):
                if rest:
                    rest, _embedded = _split_embedded_quotes(rest, names, last_speaker)   # 镜头行里夹的台词拆出来（P133）
                    out.append("（%s）%s" % (inner, rest))
                    last_shot_txt = rest
                    for _who, _txt in _embedded:
                        out.append("%s：%s" % (_who, _txt))
                        last_speaker = _who
                else:
                    # 描述被换到了下一行，并回来
                    desc = ""
                    while i < len(lines):
                        nxt = lines[i].strip()
                        if not nxt:
                            i += 1
                            continue
                        # 下一行如果是台词/心声/结构/另一个镜头头，就说明这个镜头真的没描述
                        if (_STRUCT.match(nxt) or _SHOT_HEAD.match(nxt)
                                or _VO_OK.match(nxt) or _LINE_SAY.match(nxt)):
                            break
                        desc = nxt
                        i += 1
                        break
                    out.append("（%s）%s" % (inner, desc) if desc else "（%s）" % inner)
                    last_shot_txt = desc
                continue

            # ---- 情况C：括号里既不是景别也不是心声（「（动作）」这类自造行）----
            # 内容并进上一个镜头的描述里，不让它顶到行首变成假镜头
            tail = rest
            if not tail and i < len(lines):
                nxt = lines[i].strip()
                if nxt and not (_STRUCT.match(nxt) or _SHOT_HEAD.match(nxt)):
                    tail = nxt
                    i += 1
            say = _LINE_SAY.match(tail) if tail else None
            if say and say.group(1) in names:
                out.append("%s：%s" % (say.group(1), say.group(2).strip()))
                last_speaker = say.group(1)
            elif tail:
                # 找最近的镜头行接上去
                for k in range(len(out) - 1, -1, -1):
                    if out[k].startswith("（") or out[k].startswith("("):
                        out[k] = out[k].rstrip("。") + "。" + tail
                        last_shot_txt = out[k]
                        break
                else:
                    out.append(tail)
            continue

        # ---- 结构词当说话人（「站位：咔哒」）：并进上一个镜头（P153） ----
        if _FACT_HEAD.match(line) and not any(line.startswith(n + "：") or line.startswith(n + ":")
                                              for n in names):
            continue                                            # P158①：事实单栏目名，整行删
        _sw = re.match(r"^(站位|镜头|景别|运镜|音效|画外)[：:]\s*(.*)$", line)
        if _sw:
            _tail = (_sw.group(2) or "").strip()
            for k in range(len(out) - 1, -1, -1):
                if out[k].startswith("（") or out[k].startswith("("):
                    if _tail:
                        out[k] = out[k].rstrip("。") + "。" + _tail
                    break
            continue

        # ---- 普通台词行 ----
        say = _LINE_SAY.match(line)
        if say:
            _sp, _txt = say.group(1).strip(), say.group(2).strip()
            _known = any(_sp == n or _sp in n or n in _sp for n in names) if names else True
            # P158③：署名是上一个镜头末尾的动作短语（「眉头微」「拿起手机」）——按镜头描述里的人名回填
            if names and not _known and _ACT_WORD.search(_sp):
                _who = _guess_speaker("")
                if _who:
                    # 这个动作短语本来就是从上一个镜头行末尾抄下来的，丢掉就行，别再补回去
                    last_speaker = _who
                    out.append("%s：%s" % (_who, _txt))
                    continue
            last_speaker = _sp
        out.append(line)

    # 收尾：连续空行压成一个
    txt = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", txt).strip() + "\n"
