# -*- coding: utf-8 -*-
"""衣着状态追踪（P329，用户 9-12 定：人设图永远穿衣；剧情里洗澡/脱衣/换衣的状态必须进视频提示词）。

确定性、不叫模型：按段（切片）顺序扫画面稿，每个人维护一份「现在身上什么样」：
  · 脱下/褪下/解开 + 衣物 → 记「已脱下 X」；上下身都脱了或脱的是整身衣（连体衣/长袍/作战服）→「全裸」
  · 换上/穿上/披上/裹上 + 衣物 → 「穿 X」（换上=整套换；穿上=在现在的基础上加）
  · 全裸/赤裸/一丝不挂 → 「全裸」
  · 洗澡语境（走进淋浴/花洒/热水冲刷/泡澡/浴池）且没写穿着泳衣/浴巾 → 「全裸（在洗澡）」——洗澡肯定要脱衣，不管画面稿有没有明写
  · 淋雨/湿透 → 加「（湿透）」，擦干/换衣/洗澡后去掉
  · 地点换到外面（街/店/公司…）而人还是全裸、又没写穿衣 → 回到参考图那一身（故事省略了穿衣）
正文（原文）里有、画面稿里被收掉的事件（比如尺度档把「全裸」抹掉了）按位置比例补回对应的段。
每段给出 start / events / end 三样，提示词绑定行照这个写；end 存进段（wardrobe），下次接着写时读。
"""
import re

_G_TOP = r"上衣|衬衫|衬衣|背心|T恤|毛衣|卫衣|胸罩|文胸|内衣|抹胸|吊带|睡衣上衣|战术背心|作战背心|铠甲上身|针织衫|polo衫|POLO衫"
_G_OUTER = r"外套|夹克|大衣|风衣|西装|马甲|皮衣|羽绒服|棉袄|斗篷|披风|罩衫|长外套"
_G_BOTTOM = r"长裤|短裤|裤子|裤|裙子|短裙|长裙|内裤|丁字裤|丝袜|长筒袜|裤袜|打底裤"
_G_FULL = r"连体衣|连体紧身衣|作战服|战术服|制服|长袍|袍子|道袍|睡袍|浴袍|连衣裙|裙装|礼服|旗袍|和服|铠甲|盔甲|甲胄|一身衣服|衣服|衣物|衣裳|衣衫|全部衣物|所有衣物|校服|运动服|泳衣|睡衣|宇航服|潜水服"
_G_MINOR = r"靴|鞋|袜子|手套|帽子|头盔|面具|眼镜|围巾|领带|腰带|斗篷|披风|围裙|浴巾|毛巾"
_G_ANY = "(?:%s|%s|%s|%s|%s)" % (_G_FULL, _G_TOP, _G_OUTER, _G_BOTTOM, _G_MINOR)
_G_NOUN = re.compile(_G_ANY)
_LEAD_JUNK = re.compile(r"^(又|把|将|再|也|然后|并|和|与|及|随手|顺手|便|就|了|下|掉|去|着|上|把她的|把他的|她的|他的|自己的|身上的)+")
# 衣物短语：前面最多 8 个字的修饰（深灰色的战术衬衫）
_GARMENT = r"((?:(?!脱|褪|穿|换|披|裹|解|扯|剥|扒|甩|蹬|摘|卸|滑|扔|搭|丢|踢|走|站|坐|躺|帮|替|给)[^，。；、：:\s「」“”]){0,8}?(?:%s))" % _G_ANY
_UNDRESS = re.compile(r"(脱下|脱掉|脱去|脱了|褪下|褪去|扯下|扯掉|剥下|扒下|扒掉|解下|甩掉|蹬掉|摘下|摘掉|卸下|脱光|褪尽)\s*(?:了|掉|去)?\s*(?:他的|她的|自己的|身上的|外面的|上身的|下身的)?" + _GARMENT)
_UNDRESS2 = re.compile(_GARMENT + r"\s*(被|已经|已|随手|彻底|完全|也)?\s*(脱下|脱掉|脱去|褪下|褪去|扯下|剥下|扒下|甩掉|蹬掉|踢掉|踢开|甩开|滑落|滑下|滑脱|落地|褪尽|扔在|搭在|丢在|甩在|堆在|散落)")
_LOWER = re.compile(_GARMENT + r"\s*(褪至|褪到|拉到|退到|推到|卷到|撩到)\s*([^，。；、]{1,6})")
_DRESS = re.compile(r"(换上|穿上|套上|披上|裹上|系上|穿好|披了|裹了|穿了|换了|重新穿上|重新披上|裹着|围上|围着)\s*(?:了|一件|一条|一套|一身|件|条|套)?\s*(?:他的|她的|自己的|干净的|干爽的|新的)?" + _GARMENT + r"(?:(?:和|与|及|、|跟)(?:一条|一件|一双)?" + _GARMENT + r")?")
_OPEN = re.compile(r"(解开|扯开|拉开|敞开)\s*(?:了)?\s*(?:他的|她的|自己的|身上的)?" + _GARMENT + r"?\s*(的)?(纽扣|扣子|拉链|领口|系带|腰带|前襟|扣)")
_NAKED = re.compile(r"全裸(?!露)|赤裸的身|一丝不挂|赤条条|光着身子|赤身裸体|裸着身|裸体|什么都没穿|没穿衣服|全身赤裸")   # P434
_NAKED_TOP = re.compile(r"光着上身|赤着上身|上身赤裸|上身光着|赤膊|袒露上身|裸着上身|光着膀子")
_BATH = re.compile(r"走进淋浴|进入淋浴|踏进淋浴|走进浴室|进了浴室|踏进浴室|进浴室|淋浴区|淋浴间|站在花洒|花洒下|花洒[^，。；]{0,8}(冲|淋|浇)|热水冲刷|热水漫过|热水淋|温水冲|冲澡|洗澡|沐浴|泡澡|泡在浴|泡进浴|浴池里|浴缸里|坐进浴缸|躺进浴缸|泡温泉|泡汤|温泉池里|入浴")
_BATH_CLOTHED = re.compile(r"穿着[^，。；]{0,6}(泳衣|泳裤|浴衣|内衣|衣服)|裹着浴巾|围着浴巾|裹着浴袍|穿着浴袍|和衣|带着衣服")
_WET = re.compile(r"淋雨|被雨|雨水打湿|雨水浸|湿透|淋湿|浇湿|落汤鸡|全身湿|浑身湿|淋得")
_DRY = re.compile(r"擦干|吹干|烘干|换上干|擦干净身体|裹上浴巾|裹着浴巾|披上浴袍|穿上浴袍")
_PLURAL = re.compile(r"两人|二人|他们|她们|俩人|两个人|三人|众人|几人|全都|一起")
_HELP = re.compile(r"(帮|替|给|为)(她|他|对方)")           # 「阿明帮她解开裙子」：衣服是另一个人的
_OUTDOOR = re.compile(r"街|路|店|铺|公司|办公|学校|教室|广场|车站|机场|商场|餐厅|饭馆|酒吧|公园|大堂|电梯|楼道|走廊|门口|花园|院子|码头|车上|车里|户外|野外|山|林|河|海边|市场|集市|寺|庙|殿|大厅|会议|医院")
_INDOOR_PRIVATE = re.compile(r"浴|卧室|卧房|房间|床|家里|客厅|房内|屋内|帐篷|温泉|更衣|化妆间|浴室|洗手间|寝室|宿舍|旅馆|酒店房")
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])")
_LOC_LINE = re.compile(r"^──\s*([^─｜|\n]{1,24})")


def _zone(garment):
    g = str(garment or "")
    if re.search(_G_FULL, g):
        return "full"
    if re.search(_G_OUTER, g):
        return "outer"
    if re.search(_G_TOP, g):
        return "top"
    if re.search(_G_BOTTOM, g):
        return "bottom"
    return "minor"


class _State(object):
    __slots__ = ("base", "off", "on", "naked", "wet", "bath", "trail")

    def __init__(self):
        self.base = ""            # "" = 参考图那一身；换上 X 后 = "X"
        self.off = []             # 已脱下的（短语）
        self.on = []              # 在现在基础上穿上的
        self.naked = ""           # "" / "上身赤裸" / "全裸"
        self.wet = False
        self.bath = False
        self.trail = []           # 本段内发生的事（短句）

    def text(self):
        if self.naked == "全裸":
            t = "全裸" + ("（在洗澡，身上有水）" if self.bath else "")
            if self.on:
                t = "只穿" + "、".join(self.on[-2:]) + ("（其余全裸）")
            return t
        parts = []
        parts.append(("穿" + self.base) if self.base else "参考图那一身")
        if self.on:
            parts.append("外加" + "、".join(self.on[-3:]))
        if self.off:
            parts.append("已脱下" + "、".join(self.off[-3:]))
        if self.naked == "上身赤裸":
            parts.append("上身赤裸")
        t = "，".join(parts)
        if self.wet and not self.bath:
            t += "（湿透）"
        return t

    def snapshot(self):
        return self.text()


def _apply(st, kind, garment="", extra=""):
    """一件事落到状态上。返回一句给提示词看的话。"""
    g = str(garment or "").strip()
    if kind == "undress":
        if not g:
            return ""
        z = _zone(g)
        if g not in st.off:
            st.off.append(g)
        st.on = [x for x in st.on if x != g]
        if z == "full":
            st.naked = "全裸"
            st.off = []
        else:
            zones_off = {_zone(x) for x in st.off}
            if st.naked == "全裸":
                pass
            elif "top" in zones_off and "bottom" in zones_off and not st.base:
                st.naked = "全裸"                                  # 上身衣 + 下身衣都脱了（外套不算上身衣）
                st.off = []
            elif "top" in zones_off and not st.base and z == "top" and re.search(r"内衣|胸罩|文胸|抹胸|吊带", g) is None and st.naked != "全裸":
                st.naked = st.naked or ""
        st.trail.append("脱下" + g)
        return "脱下" + g
    if kind == "lower":
        if g and g not in st.off:
            st.trail.append("%s%s%s" % (g, extra.split("|")[0], extra.split("|")[1] if "|" in extra else ""))
            st.off.append(g + extra.split("|")[0] + (extra.split("|")[1] if "|" in extra else ""))
        return st.trail[-1] if st.trail else ""
    if kind == "dress":
        if not g:
            return ""
        verb = extra or "穿上"
        if verb.startswith("换") or st.naked == "全裸" and _zone(g) in ("full",):
            st.base, st.on, st.off, st.naked = g, [], [], ""
            st.bath = False
        elif st.naked == "全裸":
            st.on.append(g)
            if _zone(g) == "full":
                st.base, st.on, st.naked = g, [], ""
            st.bath = False
        else:
            st.on.append(g)
            st.off = [x for x in st.off if x != g]
            if _zone(g) == "full":
                st.base, st.on = g, []
        if re.search(r"浴巾|浴袍|睡袍|睡衣", g):
            st.bath = False
            st.wet = False
        st.trail.append(verb + g)
        return verb + g
    if kind == "open":
        st.trail.append("解开" + (g or "") + (extra or ""))
        return st.trail[-1]
    if kind == "naked":
        st.naked, st.off, st.on = "全裸", [], []
        st.trail.append("全裸")
        return "全裸"
    if kind == "naked_top":
        if st.naked != "全裸":
            st.naked = "上身赤裸"
        st.trail.append("上身赤裸")
        return "上身赤裸"
    if kind == "bath":
        if st.naked != "全裸":
            st.naked, st.off, st.on = "全裸", [], []
            st.trail.append("洗澡，脱光")
        st.bath = True
        st.wet = False
        return "在洗澡（全裸）"
    if kind == "wet":
        if st.wet or st.bath:
            return ""
        st.wet = True
        st.trail.append("湿透")
        return "湿透"
    if kind == "dry":
        st.wet = False
        st.trail.append("擦干")
        return "擦干"
    if kind == "reset":
        st.base, st.off, st.on, st.naked, st.bath, st.wet = "", [], [], "", False, False
        st.trail.append("换回原来那一身")
        return "换回原来那一身"
    return ""


def _who(sent, names, last_names, plural_all):
    """这句话的主语（可能多个）。就近：事件前最近的人名；没有就句首的人名；再没有就上一句的人；「两人」= 全部（正好两个人时就是这两个）。"""
    found = [(m.start(), n) for n in names for m in re.finditer(re.escape(n), sent)]
    if _PLURAL.search(sent) and (not found or len(set(n for _, n in found)) >= 2):
        return list(names) if len(names) <= 2 else list(plural_all)
    if found:
        found.sort()
        return [found[0][1]]
    return list(last_names)


def _events_in(sent):
    """一句话里的衣着事件，按出现位置排序。[(pos, kind, garment, extra)]"""
    ev = []
    for m in _UNDRESS.finditer(sent):
        ev.append((m.start(), "undress", m.group(2), ""))
    for m in _UNDRESS2.finditer(sent):
        ev.append((m.start(), "undress", m.group(1), ""))
    for m in _LOWER.finditer(sent):
        ev.append((m.start(), "lower", m.group(1), "%s|%s" % (m.group(2), m.group(3))))
    for m in _DRESS.finditer(sent):
        ev.append((m.start(), "dress", m.group(2), m.group(1)))
        if m.group(3):
            ev.append((m.start() + 1, "dress", m.group(3), "穿上"))     # 「换上 X 和 Y」：Y 是加在 X 上的，不是再换一次
    for m in _OPEN.finditer(sent):
        ev.append((m.start(), "open", m.group(2) or "", m.group(4)))
    for m in _NAKED_TOP.finditer(sent):
        ev.append((m.start(), "naked_top", "", ""))
    for m in _NAKED.finditer(sent):
        ev.append((m.start(), "naked", "", ""))
    if _BATH.search(sent) and not _BATH_CLOTHED.search(sent):
        ev.append((_BATH.search(sent).start(), "bath", "", ""))
    if _WET.search(sent):
        ev.append((_WET.search(sent).start(), "wet", "", ""))
    if _DRY.search(sent):
        ev.append((_DRY.search(sent).start(), "dry", "", ""))
    # 同一句同一件衣物同一类事只留一个（_UNDRESS 和 _UNDRESS2 可能都命中；按衣物名词去重）；衣物短语去掉句首虚词
    seen, out = set(), []
    for pos, kind, g, extra in sorted(ev):
        g = _LEAD_JUNK.sub("", str(g or "")).strip()
        noun = _G_NOUN.findall(g)
        key = (kind, noun[-1] if noun else g)
        if key in seen:
            continue
        seen.add(key)
        out.append((pos, kind, g, extra))
    # 同一句里「西装」和「西装外套」这种一长一短同位置的算一件，留长的
    final = []
    for e in out:
        dup = False
        for f in out:
            if f is e or f[1] != e[1] or not f[2] or not e[2]:
                continue
            if e[2] != f[2] and e[2] in f[2] and abs(int(f[0]) - int(e[0])) <= 12:
                dup = True
                break
        if not dup:
            final.append(e)
    return final


def _run(texts, names, states, present_of=None):
    """按块顺序把事件落到 states。texts: [块文本]; 返回每块 [{name: [事件句]}]。"""
    per_block = []
    last_names = list(names[:1])
    for txt in texts:
        happened = {}
        loc = _LOC_LINE.match(str(txt or "").strip())
        if loc:
            place = loc.group(1)
            # 换到外面的地方、人还全裸、又没写穿衣 → 故事省略了穿衣，回到参考图那一身
            if _OUTDOOR.search(place) and not _INDOOR_PRIVATE.search(place):
                for n in names:
                    if states[n].naked == "全裸" or states[n].bath:
                        _apply(states[n], "reset")
                        happened.setdefault(n, []).append("换回原来那一身")
            per_block.append(happened)
            continue
        present = [n for n in names if n in str(txt)] or list(names)
        for sent in [x for x in _SENT_SPLIT.split(str(txt or "")) if x.strip()]:
            evs = _events_in(sent)
            if not evs:
                fnd = [n for n in names if n in sent]
                if fnd:
                    last_names = fnd[:1]
                continue
            subj = _who(sent, names, last_names, present)
            in_sent = [n for n in names if n in sent]
            # 「阿明帮她解开裙子的拉链，裙子滑落」：脱的是另一个人的衣服（两个人时能定；多人时不猜）
            help_other = None
            if _HELP.search(sent) and len(names) == 2 and len(in_sent) == 1:
                help_other = [n for n in names if n != in_sent[0]]
            for _pos, kind, g, extra in evs:
                for nm in names:
                    g = g.replace(nm, "")
                if help_other and kind in ("undress", "lower", "open", "dress", "naked", "naked_top"):
                    for n in help_other:
                        line = _apply(states[n], kind, g, extra)
                        if line:
                            happened.setdefault(n, []).append(line)
                    continue
                # 事件前最近的人名优先；洗澡/全裸/湿透/擦干这类"环境事件"句子里点到的人全算（「苏蔓走进淋浴区，林澈紧随其后，热水漫过全身」）
                before = [(m.start(), n) for n in names for m in re.finditer(re.escape(n), sent) if m.start() < _pos]
                if kind in ("bath", "naked", "wet", "dry") and len(in_sent) >= 2:
                    tgt = in_sent
                else:
                    tgt = [max(before)[1]] if (before and not _PLURAL.search(sent)) else subj
                for n in tgt:
                    if n not in states:
                        continue
                    line = _apply(states[n], kind, g, extra)
                    if line:
                        happened.setdefault(n, []).append(line)
            fnd = [n for n in names if n in sent]
            if fnd:
                last_names = fnd[:1]
        per_block.append(happened)
    return per_block


def track(slices, names, prose="", start_states=None):
    """slices: [段文本]（按顺序）；names: 人名；prose: 原文（可选，用来补画面稿里被收掉的事件）。
    返回 [{name: {"start": 文本, "events": [句], "end": 文本}}]，与 slices 一一对应。
    start_states: {name: 文本} 上一段结束时的状态（接着写时给）；只认「全裸」/「湿透」/「穿X」这几种能还原的。"""
    names = [str(n) for n in (names or []) if str(n).strip()]
    states = {n: _State() for n in names}
    for n, t in (start_states or {}).items():
        if n in states and t:
            t = str(t)
            if "全裸" in t or "只穿" in t:
                states[n].naked = "全裸"
                if "洗澡" in t:
                    states[n].bath = True
            elif t.startswith("穿"):
                states[n].base = t[1:].split("，")[0]
            if "湿透" in t:
                states[n].wet = True
            if "上身赤裸" in t:
                states[n].naked = states[n].naked or "上身赤裸"
    # 原文里有、画面稿里没有的事件：按位置比例补到对应的段
    extra_by_slice = _prose_extras(slices, names, prose)
    out = []
    for k, txt in enumerate(slices or []):
        start = {n: states[n].snapshot() for n in names}
        for n in names:
            states[n].trail = []
        blocks = [b for b in str(txt or "").split("\n\n")]
        per = _run(blocks, names, states)
        for n, evs in (extra_by_slice.get(k) or {}).items():
            for kind, g in evs:
                if n in states and not _already(states[n], kind, g):
                    line = _apply(states[n], kind, g, "")
                    if line:
                        per.append({n: [line + "（原文）"]})
        row = {}
        for n in names:
            evs = []
            for h in per:
                evs += h.get(n) or []
            row[n] = {"start": start[n], "events": evs, "end": states[n].snapshot()}
        out.append(row)
    return out


def _already(st, kind, g):
    if kind == "naked" or kind == "bath":
        return st.naked == "全裸"
    if kind == "undress":
        return st.naked == "全裸" or g in st.off
    if kind == "dress":
        return st.base == g or g in st.on
    return False


def _prose_extras(slices, names, prose):
    """原文里的 全裸/洗澡/换上 这类事件，画面稿对应位置没有的，按位置比例补上。返回 {slice_idx: {name: [(kind, garment)]}}。"""
    prose = str(prose or "")
    if not prose.strip() or not slices:
        return {}
    total_p = len(prose)
    lens = [len(str(s or "")) for s in slices]
    total_s = float(sum(lens)) or 1.0
    bounds, acc = [], 0
    for L in lens:
        bounds.append((acc / total_s, (acc + L) / total_s))
        acc += L
    joined = "\n".join(str(s or "") for s in slices)
    out = {}
    pos = 0
    last_names = list(names[:1])
    for sent in [x for x in _SENT_SPLIT.split(prose) if x.strip()]:
        p0 = prose.find(sent, pos)
        if p0 >= 0:
            pos = p0 + len(sent)
        ratio = (p0 if p0 >= 0 else pos) / float(total_p)
        evs = [e for e in _events_in(sent) if e[1] in ("naked", "bath", "dress", "undress")]
        fnd = [n for n in names if n in sent]
        if not evs:
            if fnd:
                last_names = fnd[:1]
            continue
        k = next((i for i, (a, b) in enumerate(bounds) if a <= ratio < b), len(bounds) - 1)
        # 画面稿这一段或相邻段已经写了同类事件就不补
        near = "".join(str(slices[i] or "") for i in range(max(0, k - 1), min(len(slices), k + 2)))
        in_sent = [n for n in names if n in sent]
        if not in_sent and not _PLURAL.search(sent):
            continue                                            # 句里没点名：不敢猜是谁的衣服，不补
        for _pos, kind, g, extra in evs:
            if kind == "naked" and (_NAKED.search(near) or _BATH.search(near)):
                continue
            if kind == "bath" and _BATH.search(near):
                continue
            if kind in ("dress", "undress"):
                if not g:
                    continue
                noun = re.findall(_G_ANY, g)
                if noun and re.search(noun[-1], near):
                    continue                                    # 画面稿附近已经写了这件衣物的事（措辞可能不同）
            if kind in ("bath", "naked") and (len(in_sent) >= 2 or _PLURAL.search(sent)):
                subj = in_sent or list(names)
            else:
                subj = _who(sent, names, last_names, in_sent or names)
            for n in subj:
                out.setdefault(k, {}).setdefault(n, []).append((kind, g))
        if fnd:
            last_names = fnd[:1]
    return out


def state_line(row, name):
    """给提示词绑定行的一句：有变化写「本段开始时…；本段内…；结束时…」，没变化写「沿上一段结束时的样子…」。参考图那一身且没变化 → 空（用普通绑定行）。"""
    r = (row or {}).get(name) or {}
    start, evs, end = str(r.get("start") or ""), list(r.get("events") or []), str(r.get("end") or "")
    if not evs:
        if not start or start == "参考图那一身":
            return ""
        return "沿上一段结束时的样子：%s；本段全程如此" % start
    return "本段开始时：%s；本段内：%s；结束时：%s" % (start or "参考图那一身", "、".join(evs)[:120], end)
