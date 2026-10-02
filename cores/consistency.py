# -*- coding: utf-8 -*-
"""consistency.py —— 用程序抓"同一篇里自相矛盾"的错。

【为什么要有这个文件】
2026-09-08 实测：让模型自己核对，六组对照（三组坏文本 + 三组人工修正）**六次全判 pass**，
连"把甲的台词和老花镜安到乙头上"这种明显的身份错都说"identity：无混淆"。
模型核对在这个配置下没有鉴别力，再叠加检查器没用。

但它漏掉的那几类错，恰恰是**确定性能抓的**：
  · 悬疑第1话：信号触发时间写成"每晚零点零零分"，而全篇的时间是三点整
  · 悬疑第1话说"电感波动只有百分之五十八"，第2话说"卡在百分之六十二的临界点"
  · 悬疑第2话："向西三公里的枫林苑"下一句"直线距离不过一千米的车程"
  · 冒险第2话：第1话叫"主绳"，第2话开头变成"伞绳"
数字、时间、道具名——这些不需要理解故事，只要对照就能查出来。

这里只做能确定的四件事，查不准的宁可不报：
  ① 同一个被测的东西出现两个不同的值（电感波动 58% / 62%）
  ② 同一段里出现两个同类单位的距离值（三公里 / 一千米）
  ③ 正文里的时间点和故事已定的时间对不上（零点零零分 / 三点整）
  ④ 道具改了名字（主绳 → 伞绳）
另外还有一件在分话层：
  ⑤ 这一话和上一话演的是同一件事，没有进展
"""
import re

# ── 数字归一 ────────────────────────────────────────────────
_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}


def cn_number(text):
    """中文数字 → 整数。认得「三」「十五」「六十二」「一千米」这类常见写法，认不出返回 None。"""
    t = str(text or "").strip()
    if not t:
        return None
    if re.fullmatch(r"[0-9]+", t):
        return int(t)
    if not re.fullmatch(r"[零〇一二三四五六七八九十百千两]+", t):
        return None
    total, section, number = 0, 0, 0
    for ch in t:
        if ch in _CN_DIGIT:
            number = _CN_DIGIT[ch]
        elif ch == "十":
            section += (number or 1) * 10
            number = 0
        elif ch == "百":
            section += (number or 1) * 100
            number = 0
        elif ch == "千":
            section += (number or 1) * 1000
            number = 0
    return total + section + number


# 单位家族：同一家族的值可以互相换算，跨家族不比
_UNIT = {"米": ("长度", 1.0), "公里": ("长度", 1000.0), "千米": ("长度", 1000.0),
         "厘米": ("长度", 0.01), "分钟": ("时长", 1.0), "小时": ("时长", 60.0),
         "秒": ("时长", 1 / 60.0), "天": ("时长", 1440.0), "年": ("时长", 525600.0),
         "岁": ("年龄", 1.0), "元": ("钱", 1.0), "块": ("钱", 1.0)}

_NUM = r"(?:[0-9]+(?:\.[0-9]+)?|[零〇一二三四五六七八九十百千两]+)"
# 「百分之六十二」
# 「百分之五十八」和「58%」是同一件事，模型两种都写；只认前者等于这条检查白做（P191）
_PCT = re.compile(r"百分之(" + _NUM + r")|(" + _NUM + r")\s*[%％]")
# 「三公里」「四十米」「二十分钟」
_QTY = re.compile(r"(" + _NUM + r")\s*(公里|千米|厘米|米|分钟|小时|秒|天|年|岁|元|块)")
# 「三点整」「零点零零分」「十一点」
_CLOCK = re.compile(r"(" + _NUM + r")\s*点(?:\s*(" + _NUM + r")\s*分)?")


def _val(m_num):
    v = cn_number(m_num)
    if v is None:
        try:
            v = float(m_num)
        except Exception:
            v = None
    return v


_LINK = re.compile(r"(只有|大约|约莫|约|稳定在|保持在|卡在|落在|达到|超过|不足|不到|接近|"
                   r"是|为|有|在|到|了|的|已经|足足|整整|将近|差不多)+$")


def _subject(text, start, span=14):
    """这个数值在测什么：剥掉「只有」「卡在」这类连接词，再取前面 2~4 字的名词。

    不剥的话，「电感波动只有百分之五十八」和「电感波动卡在百分之六十二」会被当成
    两个不同的对象，跨话矛盾就漏了（悬疑实测）。
    """
    left = re.sub(r"[^一-龥]", "", text[max(0, start - span):start])
    left = _LINK.sub("", left)
    return left[-4:] if len(left) >= 4 else left


def measured_values(text):
    """正文里所有「被测对象 → 值」。返回 {(家族, 对象): [(值, 原文片段)]}"""
    out = {}
    t = str(text or "")
    for m in _PCT.finditer(t):
        v = _val(m.group(1) or m.group(2))
        if v is None:
            continue
        out.setdefault(("百分比", _subject(t, m.start())), []).append((v, t[max(0, m.start() - 12):m.end() + 2]))
    for m in _QTY.finditer(t):
        v = _val(m.group(1))
        fam, scale = _UNIT.get(m.group(2), (None, 1.0))
        if v is None or not fam:
            continue
        out.setdefault((fam, _subject(t, m.start())), []).append((v * scale, t[max(0, m.start() - 12):m.end() + 2]))
    return out


def number_conflicts(texts, min_gap=0.05):
    """同一个被测对象出现两个不同的值 → 报出来（①）。

    texts 可以是一话，也可以是整篇的几话——跨话矛盾比话内更常见。
    对象取数值前面最近的 2~4 字，两个片段的对象要**完全一样**才比，宁可漏不可错报。
    """
    pool = {}
    for t in ([texts] if isinstance(texts, str) else list(texts)):
        for key, items in measured_values(t).items():
            pool.setdefault(key, []).extend(items)
    out = []
    for (fam, subj), items in pool.items():
        if len(subj) < 3 or len(items) < 2:
            continue
        vals = {}
        for v, frag in items:
            vals.setdefault(round(float(v), 4), frag)
        if len(vals) < 2:
            continue
        lo, hi = min(vals), max(vals)
        if hi <= 0 or (hi - lo) / hi < min_gap:
            continue
        out.append("「%s」在文中出现了两个不同的值：%s ／ %s（%s）"
                   % (subj, _fmt(lo), _fmt(hi), " ｜ ".join(list(vals.values())[:2])))
    return out


def _fmt(v):
    return str(int(v)) if abs(v - int(v)) < 1e-9 else ("%.2f" % v)


def near_distance_conflicts(text):
    """同一段里出现两个不一样的距离值 → 多半是同一段路被写了两个长度（②）。"""
    out = []
    for para in re.split(r"\n\s*\n|\n", str(text or "")):
        vals = {}
        for m in _QTY.finditer(para):
            fam, scale = _UNIT.get(m.group(2), (None, 1.0))
            v = _val(m.group(1))
            if fam != "长度" or v is None:
                continue
            if v * scale < 50:
                continue                  # 几十厘米的偏移、几米的间距，和路程不是一回事
            vals[round(v * scale, 3)] = para[max(0, m.start() - 10):m.end() + 2]
        if len(vals) >= 2:
            lo, hi = min(vals), max(vals)
            if hi > 0 and (hi - lo) / hi >= 0.2:
                out.append("同一段里出现两个距离：%s米 ／ %s米（%s）"
                           % (_fmt(lo), _fmt(hi), " ｜ ".join(list(vals.values())[:2])))
    return out


_TOD = re.compile(r"(凌晨|清晨|早上|早晨|上午|中午|下午|傍晚|晚上|夜里|深夜|半夜|每晚|每天|每夜|每日)\s*$")


def _is_clock(s, m):
    """这个「N点」是不是钟点（P291）：「多一点对话」「早一点」「一点点」不是。
    「一点」只有带分/钟/整/半、或前面有时段词才算；别的数字照旧。"""
    h = _val(m.group(1))
    if h is None or h > 24:
        return False
    after = str(s)[m.end():m.end() + 2]
    if after.startswith(("点", "儿", "也", "都")):          # 一点点 / 一点儿 / 一点也 / 一点都
        return False
    if int(h) == 1 and not m.group(2):
        before = str(s)[max(0, m.start() - 4):m.start()]
        if not (_TOD.search(before) or after.startswith(("钟", "整", "半"))):
            return False
    return True


def clock_conflicts(text, canon):
    """正文里的钟点和故事已定的时间对不上（③）。

    canon 是一句话、梗概、本话安排里出现过的钟点。正文写了别的钟点、
    而故事本来只有一个固定时间点时，就是错（悬疑实测：三点整被写成零点零零分）。
    """
    def clocks(s):
        out = set()
        for m in _CLOCK.finditer(str(s or "")):
            if not _is_clock(s, m):
                continue
            h, mi = _val(m.group(1)), (_val(m.group(2)) if m.group(2) else 0)
            out.add((int(h), int(mi or 0)))
        return out
    want = clocks(canon)
    if len(want) != 1:
        return []                      # 故事本来就有好几个时间点，不判
    w0 = list(want)[0]
    base = w0[0] * 60 + w0[1]
    # 故事里当然可以有别的钟点（交班五点半）。只有**带反复标记**的钟点才该等于那个固定时间：
    # 「每晚零点零零分收到触发信号」——这是在说那件天天发生的事，写错了就是错。
    bad = []
    for m in _CLOCK.finditer(str(text or "")):
        if not _is_clock(text, m):
            continue
        h, mi = _val(m.group(1)), (_val(m.group(2)) if m.group(2) else 0)
        near = str(text)[max(0, m.start() - 14):m.start()]
        if not re.search(r"每晚|每天|每夜|每次|总在|准时|都在|照例|历来", near):
            continue
        if abs((int(h) * 60 + int(mi or 0)) - base) > 5:
            bad.append((int(h), int(mi or 0)))
    bad = sorted(set(bad))
    if not bad:
        return []
    return ["正文里的时间和故事定的对不上：故事是 %d点%02d分，正文写了 %s"
            % (w0[0], w0[1], "、".join("%d点%02d分" % t for t in bad[:3]))]


# 尾字表：只放"通常挂在别的字后面"的字。「伞」这类自己就能成词的不放进来，
# 否则「湿滑的伞」会先匹配掉，后面的「绳」就扫不到了。
_PROP_TAIL = "绳|刀|信|袋|杯|灯|锁|表|车|鞋|书|箱|盒|牌|镜|笔|药|票|卡|杖|衣|帽|巾|钥匙"


# 动词、副词、方位词打头的不是道具名（急绳、压绳、下车、停车都是这么来的）
_NOT_HEAD = set("的了着地得一二两这那每该本条根把件只他她它我你们上下前后左右里外中"
                "是有在和与及就都也还又很更最不没别去来到入出过起开关走跑拉推压拽荡"
                "急时买卖坐骑停放挂拿抓握扶背扛提拎甩挥抛丢扔捡系解绑打修换洗晾"
                "无有没该另同各某几多少半整全新旧"
                # 量词和介词打头的更不是道具名（一双鞋→双鞋、从布袋里→从袋）
                "双只条根把件张块顶串副对包群批次回趟从往向朝给替为被让叫用拿")
# 候选词后面必须是标点或虚词，否则它是更长词的一部分（二车道、轿车顺畅、停车场）
# 这些字接在尾字后面会构成另一个词（二车道、停车场、车站、一辆车厢），候选作废
_EXTEND_AFTER = set("道场库位站辆厢队群丛间口牌照灯轮胎")


def _prop_words(text, tails=None):
    """从纯文本里抽道具名。只认「一个修饰字 + 尾字」，且前后边界干净。"""
    t = str(text or "")
    out = []
    for m in re.finditer(r"(?:" + _PROP_TAIL + r")", t):
        tail = m.group(0)
        if tails is not None and tail not in tails:
            continue
        nxt = t[m.end():m.end() + 1]
        if nxt in _EXTEND_AFTER:
            continue                                  # 二车道、停车场、车站
        # 一个字和两个字的候选都给出去，谁是真名字由调用方按"出现次数"定
        # （「救生衣」会反复出现，「好主绳」「滑的伞绳」只会出现一次）
        left = re.sub(r"[^一-龥]", "", t[max(0, m.start() - 2):m.start()])
        while left and left[0] in _NOT_HEAD:
            left = left[1:]
        if not left:
            continue
        out.append(left[-1:] + tail)
        if len(left) >= 2:
            out.append(left + tail)
    return out


def prop_drift(text, known):
    """道具改了名字（④）。已知道具「主绳」，正文里冒出同尾字的「伞绳」就报。

    误报比漏报更坏：候选词后面必须是标点或虚词，第一个字不能是动词方位词，
    否则「二车道」「轿车顺畅通过」「停车场」会被当成新道具（实测一话报十几条）。
    """
    known = [str(k).strip() for k in (known or []) if str(k or "").strip()]
    if not known:
        return []
    tails = {}
    for k in known:
        if re.search(r"(" + _PROP_TAIL + r")$", k) and len(k) >= 2:
            tails.setdefault(k[-1], set()).add(k)
    if not tails:
        return []
    out, seen = [], set()
    for word in _prop_words(text, tails):
        tail = word[-1]
        if word in tails[tail] or word in seen:
            continue
        if any(word in k or k in word for k in tails[tail]):
            continue
        seen.add(word)
        out.append("道具名对不上：故事里是「%s」，正文里写成了「%s」"
                   % ("／".join(sorted(tails[tail])), word))
    return out


_QUOTED = re.compile(r'[“"「『][^”"」』]*[”"」』]')


def _narrative_chars(text):
    """叙述部分的汉字串，附带每个字在原文里的位置。

    引号里的台词要排除：人物本来就会故意重复同一句（「顺路，纯属顺路」），
    那是写法不是毛病。标点和空白也不参与比对，免得只差一个逗号就漏判。
    """
    t = _QUOTED.sub("　", str(text or ""))
    keep, where = [], []
    for i, ch in enumerate(t):
        if "\u4e00" <= ch <= "\u9fa5":
            keep.append(ch)
            where.append(i)
    return "".join(keep), where, t


def _shared_runs(a, b, n):
    """a、b 两串里所有长度 >= n 的极大公共连续片段，返回 a 里的 (起点, 长度)。"""
    index = {}
    for i in range(len(b) - n + 1):
        index.setdefault(b[i:i + n], i)
    out, i = [], 0
    while i <= len(a) - n:
        j = index.get(a[i:i + n])
        if j is None:
            i += 1
            continue
        k = n
        while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
            k += 1
        out.append((i, k))
        i += k
    return out


def cross_episode_reuse(prose, history=(), min_run=16):
    """这一话有没有把前面某一话的叙述原样再用一遍（P199）。

    prose 是本话正文，history 是前面各话的正文。只提示不拦——
    这是文笔复用，不是事实错误。阈值 16 是在 4 个故事 15 话上量出来的：
    12 会多报边界情况，24 会漏掉实测那条 21 字的，14~20 都是 1 真 0 误。
    """
    here, where, raw = _narrative_chars(prose)
    out = []
    for older in (history or []):
        there, _, _ = _narrative_chars(older)
        if not there:
            continue
        for start, size in _shared_runs(here, there, min_run):
            frag = raw[where[start]:where[start + size - 1] + 1]
            out.append("这一段和前面某一话有 %d 个字逐字相同，像是原句再用了一遍：%s"
                       % (size, frag[:60]))
    return out


def row_no_progress(rows):
    """这一话和上一话演的是同一件事（⑤）。分话层的检查，不看正文。"""
    def sig(r):
        t = " ".join(str((r or {}).get(k) or "") for k in ("one_line", "change", "landing"))
        t = re.sub(r"[^一-龥]", "", t)
        return {t[i:i + 2] for i in range(len(t) - 1)}
    out = []
    for i in range(1, len(rows or [])):
        a, b = sig(rows[i - 1]), sig(rows[i])
        if not a or not b:
            continue
        if len(a & b) >= max(8, int(len(b) * 0.55)):
            out.append("第 %d 话和第 %d 话演的是同一件事，这一话没有进展" % (i + 1, i))
    return out


def row_thin_change(rows):
    """「变化」这一栏写得太薄——提醒，不拦（P193）。

    原来和"演的是同一件事"混在 row_no_progress 里一起当硬问题，
    结果同一件事有两套判法（缺 change 只提醒、短 change 却判死），而且第 1 话根本不查。
    写作那一步有梗概和前文兜着，薄一栏不至于写不出来。
    """
    out = []
    for i, r in enumerate(rows or [], 1):
        chg = re.sub(r"[^一-龥]", "", str((r or {}).get("change") or ""))
        if len(chg) < 4:
            out.append("第 %d 话没写清这一话改变了什么" % i)
    return out


def missing_props(prose, props):
    """本话点名要用的道具，正文里一次都没出现（P181）。

    比"道具改名"稳得多：改名的后果之一就是原名消失，这条能兜住，
    而且几乎不会误报——只查"点名了却没出现"，不猜正文里的新词是不是它。
    """
    t = str(prose or "")
    gone = [p for p in (props or []) if len(str(p)) >= 2 and str(p) not in t]
    if not gone:
        return []
    return ["本话安排里点名的道具，正文里一次都没出现：%s" % "、".join(gone[:4])]


_ADDRESS = re.compile(r"([一-龥])(工|总|哥|姐|姨|叔|婶|爷|师傅|老师|大夫|警官|队长|经理|保安)")
_QUOTE = re.compile(r"[“「]([^”」]{1,120})[”」]")


def self_address_conflicts(prose, names):
    """有人在对话里叫的是**自己的**姓（⑥）。

    实测：冒险结局「周工，」这句是周屿说的，对面站着林深——他在叫自己。
    判据很窄，一段里同时满足三条才报：这段有引号台词、台词里有"姓+称谓"、
    而这一段里只出现了一个人名、且那个人和这个称谓同姓。宁可漏，不要错报。
    """
    cast = [str(n).strip() for n in (names or []) if len(str(n).strip()) >= 2]
    if not cast:
        return []
    out, seen = [], set()
    for para in re.split(r"\n\s*\n|\n", str(prose or "")):
        here = [n for n in cast if n in para]
        if len(here) != 1:
            continue                      # 一段里出现两个人就分不清谁在说，跳过
        who = here[0]
        for q in _QUOTE.findall(para):
            for m in _ADDRESS.finditer(q):
                term = m.group(0)
                if m.group(1) != who[0] or term in seen or term in cast:
                    continue
                seen.add(term)
                out.append("「%s」是%s在叫自己的姓——这一段里说话的只有他一个人" % (term, who))
    return out


def prose_hints(prose):
    """只提示、不拦的（P187④）。

    「同一段两个路程距离」抓到过真错（三公里 vs 一千米），但正常写法里也会有
    「这条路三公里，那条一公里半」这种对比。它判不出哪种是哪种，
    所以降级：写进 warnings 给人看，不再把这一话打回。
    """
    return near_distance_conflicts(prose)


def prose_problems(prose, canon="", props=(), history=(), cast=()):
    """一话正文的确定性一致性检查，返回问题列表（能确定的才报）。

    道具改名（prop_drift）不在这里跑：它按尾字比对，真样本上一话误报 3~5 条
    （轿车、劳保鞋、将绳固定都被当成改了名）。函数留着备用，生产链路不用它。
    """
    texts = list(history or []) + [prose]
    out = []
    out += number_conflicts(texts)
    out += clock_conflicts(prose, canon)
    out += self_address_conflicts(prose, cast)
    # 道具那两条（改名 prop_drift / 缺失 missing_props）都不在生产链路里：
    # 没有分词器，按尾字回看切出来的是碎词（周修鞋、好绳、默停车），
    # 十话实测误报 6 条、真错 0 条。函数和单测留着，等有了可靠的道具来源再接。
    return out
