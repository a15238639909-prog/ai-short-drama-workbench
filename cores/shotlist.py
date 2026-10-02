# -*- coding: utf-8 -*-
"""shotlist.py — 拍摄清单：整条流水线共用的一份事实。

【为什么要有这一层】（用户 2026-09-10 定）
系统会把故事变成视频的过程中把信息传错、漏掉、自行改掉。原因不是模型不行，
是每过一层就**重新猜一次**：谁在场、在哪、这句谁说的——猜错了就当事实往下传。
项目155：师傅的台词归了张林（说话人只能从人物卡里选，师傅没卡就塞给名单第一个）；
高潮戏绑到商业街（猜对了又被"至少停两段"改错）；黑衣人和大火全没了（"不出现任何别的人"）。

这一层做的事：
  · 由本地千问从**这一话的原文**里抽一份清单：人物（含别称、有没有卡）、群演、
    每场戏在哪/什么时候/谁在场/谁只是被提到/必须发生什么/结束时状态、每句台词谁说的。
  · **每条重要事实都要有原文依据**，代码逐条核：台词要在原文引号里找得到，
    地点要在原文或画面稿里出现，人名和别称要在原文里出现。找不到依据的不收。
  · 有歧义的地方明确标出来，不猜。
  · 下游（在场人物、场景绑定、说话人）读这份清单，读的是编号（C1/S2），名字可以变、编号不变。
  · 出片前一道门：六项检查各给 通过｜有问题｜无法判断。确定的自动修，判不了的停下问用户。

不做的事：
  · 不用全局同义词表判"师傅和老者是不是一个人"——换个故事就是两个人。那是这个项目的事实，让模型按原文判、带证据。
  · 不按"离台词多近"猜说话人然后直接改——程序算得出唯一结果不代表符合故事。
"""
import re
import copy
import json
import time
import hashlib

# ─────────────────────────── 常量 ───────────────────────────

STATUS_PASS = "通过"
STATUS_PROBLEM = "有问题"
STATUS_UNKNOWN = "无法判断"
LAST_ERROR = ""      # build() 最近一次没抽出清单的原因（给驱动/日志看）

_QUOTE_RE = re.compile(r"[「“\"]([^」”\"\n]{1,200})[」”\"]")   # 长台词也要抓到（原来 80 字封顶，长句被当成编的）
_PIC_LINE_RE = re.compile(r"^([^\n：:，。！？、（(【#─\"“”「」]{1,8})(（[^）\n]{0,6}）)?\s*[：:]\s*(.+)$")
_LOC_LINE_RE = re.compile(r"──\s*([^─｜|\n]{1,24})")


def _n(s):
    """去标点，只留汉字字母数字——比对台词/名字用。"""
    return re.sub(r"[^\w一-龥]", "", str(s or ""))


def content_sig(text):
    t = str(text or "")
    return "%d:%s" % (len(t), hashlib.md5(t.encode("utf-8", "ignore")).hexdigest()[:8])


# ─────────────────────────── 原文证据 ───────────────────────────

_SIGN_BEFORE = re.compile(r"((刻|写|印|标|绣|画|题|贴)(着|有|了|上|成)?|标注着|注明|名为|第[一二三四五六七八九十\d]+行"
                          # P327②/P328f①：平板/屏幕上跳出的字（194「Mission Complete」）。提示框/弹窗/字幕/横幅 本身就是"字"；
                          # 屏幕/界面/窗口/平板/通知/消息/提示 要带 显示/跳出 这类动词——「他提示：“快走”」「她带来消息：“…”」是人在说话
                          r"|(提示框|弹窗|对话框|弹出框|字幕|横幅|标语|告示|通知栏)(上|里)?(跳出|弹出|显示|亮出|出现|写着|闪出|打出)?"
                          r"|(屏幕|界面|窗口|平板|手机|终端|显示屏|通知|消息|提示|标题)(上|里)?(跳出|弹出|显示|亮出|闪出|打出|写着)(了|出)?(一行|一句|几个|金色|红色|白色|蓝色|绿色)?(字|大字|提示|提示框|文字|字样)?"
                          r"|(跳出|弹出|闪出)(了|出)?(一行|一句|几个|金色|红色|白色|蓝色|绿色)?(字|大字|提示|提示框|文字|字样))[：:]?\s*$")
_SIGN_AFTER = re.compile(r"^\s*(几|三|两|四|五|六|二|一)?个?(字|大字|小字)|^\s*的?字样|^\s*字样|^\s*的(招牌|牌子|门牌|标签|纸袋|照片袋|信封|封面|标牌|路牌)")


def is_signage_quote(prose, start, end):
    """引号里的这几个字是招牌/文字，不是人说的话（P284：木牌上刻的「清溪苑」被当成台词）。
    看引号前面紧挨着的是不是 刻着/写着/印着…，或后面紧跟「三个字」「的字样」。"""
    t = str(prose or "")
    before = t[max(0, start - 6):start]
    after = t[end:end + 6]
    return bool(_SIGN_BEFORE.search(before)) or bool(_SIGN_AFTER.match(after))


def signage_quotes(prose):
    """原文里所有"招牌/文字"引号 → {规整后: 原句}。画面稿里出现同样内容的台词行要删。"""
    out = {}
    for m in _QUOTE_RE.finditer(str(prose or "")):
        if is_signage_quote(prose, m.start(), m.end()):
            q = m.group(1).strip()
            out.setdefault(_n(q), q)
    return out


def prose_quotes(prose):
    """原文里所有引号台词（去标点）→ {规整后: 原句}。招牌/文字（刻着「X」）不算（P284）。"""
    out = {}
    for m in _QUOTE_RE.finditer(str(prose or "")):
        if is_signage_quote(prose, m.start(), m.end()):
            continue
        q = m.group(1).strip()
        k = _n(q)
        # 一个字也是台词（「走！」「走。」「滚！」）——只要后面跟着句末标点就是真的一句话。
        # 原来 ≥2 字才收，「走！」被当成编的台词整份清单打回；只认叹号问号时「走。」又被打回（157 验收踩到）。
        if len(k) >= 2 or (len(k) == 1 and q[-1:] in "！!？?。."):
            out.setdefault(k, q)
    return out


def _same_line(a, b):
    """两句台词算不算同一句：相等，或**两边都 ≥4 字**时互相包含。
    短句不许用包含判——「走！」是「林儿，带瑶瑶走！」的子串，按包含判就把师傅的话认成张林的（验收踩到）。"""
    a, b = _n(a), _n(b)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    # 短的那边至少占长的 60%：4 个字的「水浑得很」包含在 60 字的长句里不算同一句，
    # 否则片段永远查不出来（157 验收踩到）；画面稿把一句掐头去尾几个字仍算同一句。
    return len(short) >= 4 and len(short) >= 0.6 * len(long_) and short in long_


_CLAUSE_RE = re.compile(r"[，。；：！？,.;:!?…\n]+")


def quote_in_prose(text, quotes):
    """这句台词在不在原文里。

    整句对得上算；**几对相邻引号拼成的一句也算**——原文常写成
    「“…较劲。”阿明说道，“现在…”」，画面稿把它合成一句，不是编的（P257，STORY_163 因此三次抽不出清单）。
    判法：拆成分句，每个 ≥4 字的分句都能在某一句引号里找到（同一句或被包含）。"""
    # 整句：和某句引号相等，或是它掐头去尾后的一截（候选比原句短）。
    # 候选比原句**长**不算——多出来的字就是编的（验收样本：原句后面接一句「所以我决定走了」）。
    k = _n(text)
    if any(k == g or (len(k) >= 4 and k in g and len(k) >= 0.6 * len(g)) for g in quotes):
        return True
    parts = [_n(p) for p in _CLAUSE_RE.split(str(text or "")) if _n(p)]
    parts = [p for p in parts if len(p) >= 4]
    if len(parts) < 2:
        return False
    qs = list(quotes)
    return all(any(_same_line(p, g) or p in g for g in qs) for p in parts)


def in_text(name, *texts):
    n = str(name or "").strip()
    return bool(n) and any(n in str(t or "") for t in texts)


def _bigrams(s):
    s = _n(s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _overlap(piece, text):
    """piece 的二字组合有多少出现在 text 里（0~1）。不看语序，换个说法也对得上：
    「同门师兄弟横七竖八躺着」和「横七竖八躺着同门师兄弟」是同一件事。"""
    b = _bigrams(piece)
    if not b:
        return 0.0
    t = _n(text)
    return sum(1 for g in b if g in t) / float(len(b))


def canon_text(sl, text):
    """把别称换成本名再比对：画面稿写「老者」、清单写「师傅」，是同一个人。"""
    t = str(text or "")
    for c in (sl or {}).get("cast") or []:
        name = str(c.get("name") or "").strip()
        for a in sorted((c.get("aliases") or []), key=len, reverse=True):
            # 别名是本名的一截（「A」对「室友A」）不能替换——会把「室友A」变成「室友室友A」（P284）
            if a and name and a != name and a not in name:
                t = t.replace(a, name)
    return t


def mark_cards(sl, card_names):
    """按人物卡名单给 cast 打 card 标记（名字或别称对上就算有卡）。"""
    cn = {str(x).strip() for x in (card_names or []) if str(x).strip()}
    for c in (sl or {}).get("cast") or []:
        names = {str(c.get("name") or "").strip()} | set(c.get("aliases") or [])
        c["card"] = bool(names & cn)
    return sl


# ─────────────────────────── 规整 + 校验（chat_json 用）───────────────────────────

def normalize(d):
    """把模型吐的清单掰成固定形状；缺的键补空，编号缺的按顺序补。"""
    if isinstance(d, list):
        d = {"scenes": d}
    d = dict(d or {})
    d.setdefault("cast", [])
    d.setdefault("extras", [])
    d.setdefault("scenes", [])
    d.setdefault("lines", [])
    d.setdefault("ambiguities", [])
    for i, c in enumerate(d["cast"], 1):
        if not isinstance(c, dict):
            d["cast"][i - 1] = c = {"name": str(c)}
        c.setdefault("id", "C%d" % i)
        c.setdefault("aliases", [])
        c["aliases"] = [str(a).strip() for a in (c.get("aliases") or []) if str(a).strip()]
        c.setdefault("card", False)
        c.setdefault("evidence", "")
    for i, s in enumerate(d["scenes"], 1):
        if not isinstance(s, dict):
            d["scenes"][i - 1] = s = {"location": str(s)}
        s.setdefault("id", "S%d" % i)
        for k in ("present", "mentioned", "must_happen"):
            s.setdefault(k, [])
            s[k] = [str(x).strip() for x in (s.get(k) or []) if str(x).strip()]
        s.setdefault("time", "")
        s.setdefault("end_state", "")
        s.setdefault("evidence", "")
        # P263：location 归到地点表/场景卡之后，原写法存 spot；card 是用户答过「这场戏用哪张卡」的答案；
        # place_unresolved 标记地点表里对不上的场（出片前门要问用户，不能瞎绑）
        s.setdefault("spot", "")
        s.setdefault("card", "")
        s.setdefault("place_unresolved", False)
    for i, ln in enumerate(d["lines"], 1):
        if not isinstance(ln, dict):
            continue
        ln.setdefault("n", i)
        ln.setdefault("confidence", "确定")
        ln.setdefault("evidence", "")
        ln.setdefault("scene", "")
    for x in d["extras"]:
        if isinstance(x, dict):
            x.setdefault("scenes", [])
            x.setdefault("count", "")
    return d


_ANIMAL_RE = re.compile(r"(猫|狗|犬|马|驴|牛|羊|鸟|鸡|鸭|鹅|鱼|鼠|兔|鹿|狼|虎|狮|熊|龙|蛇|猴|鹰|猪)$")
_GROUP_RE = re.compile(r"^(路人|众人|群众|人群|百姓|食客|客人|士兵|卫兵|侍卫|随从|下人|仆人|弟子|信徒|村民|学生|观众|旅客|乘客|黑衣人|保镖)[们甲乙丙丁]?$|们$")


def _is_crowd(name):
    """这个名字是不是动物/群演：动物＝三字以内、以动物字结尾（猫、流浪猫、奶猫、小狗）；群演＝路人/众人/弟子们这类群体叫法。
    不借 authoring._CROWD——那条正则不锚定，「马小玲」「司马懿」「掌柜」都会命中，按"动物/群演不建卡"主角就永远没卡（P269 复核查出）。"""
    n = str(name or "").strip()
    if not n:
        return False
    if _GROUP_RE.search(n):
        return True
    return len(n) <= 3 and bool(_ANIMAL_RE.search(n))


def validate(d, prose, pictures="", card_names=(), people_names=(), strict_people=False):
    """格式对不够，事实要能找到依据。抛 ValueError 让 chat_json 重试。

    只在这里**硬拦**真正会让下游出错的：
      · 台词不在原文里（编的台词一定是错的）
      · 人名不在原文里（编出来的人一定是错的）
      · 场景一个都没有 / 台词说话人指向不存在的编号
    别称、地点用软核：找不到依据的丢掉，不整份重来（模型常把地点写得比原文长一点）。

    P264 人物表口径：people_names 是本话安排的人物表名字。
      · 表上的名字**不要求出现在原文里**——STORY_164 人物表写「流浪猫」，原文全程叫「灰狸花猫」，
        按旧规则 name=流浪猫 会被「人名不在原文」打回，模型只好写 灰狸花猫，下游提示词只认表上的人，卡永远绑不上。
      · aliases 里允许写人物表名（模型没改 name 但把表名放进别称，也能对上）。
      · 表上的人没被任何 cast 的 name/alias 认领：strict_people 时打回（前两轮给模型机会改），
        否则写进 ambiguities（kind=cast），出片前门报「无法判断」。动物/群演的名字不参与 strict——
        猫认不认得出来不该让整份清单抽不出来。
    """
    prose = str(prose or "")
    pictures = str(pictures or "")
    quotes = prose_quotes(prose)
    cast_ids = {c.get("id") for c in d.get("cast") or [] if isinstance(c, dict)}
    _tbl = [str(x or "").strip() for x in (people_names or []) if str(x or "").strip()]
    if not d.get("scenes"):
        raise ValueError("没有 scenes")
    if not d.get("cast"):
        raise ValueError("没有 cast")
    def _tbl_ok(c):
        """表上的名字原文里可以一次不出现（表名是作者定的），但 evidence 必须是原文里的话——不然模型可以把没出场的人编进来"""
        if str(c.get("name") or "").strip() not in _tbl:
            return False
        ev = str(c.get("evidence") or "").strip()
        return bool(ev) and (_n(ev) in _n(prose) or _overlap(ev, prose) >= 0.6)
    bad_names = [c.get("name") for c in d["cast"]
                 if isinstance(c, dict) and not in_text(c.get("name"), prose, pictures) and not _tbl_ok(c)]
    if bad_names:
        raise ValueError("这些人名在原文里找不到：%s" % "、".join(str(x) for x in bad_names))
    # 【片段先丢】一句话的一截（清单里另一句的子串，或原文某句台词的子串且不到它的 60%）
    # 不是编的台词，是模型切碎了——静默丢掉，不整份打回（157 真跑三次都因此被打回、清单抽不出来）
    _lt = [_n(ln.get("text")) for ln in d.get("lines") or [] if isinstance(ln, dict)]
    _pq = list(quotes.keys())
    _keep = []
    for ln in d.get("lines") or []:
        if not isinstance(ln, dict):
            continue
        k = _n(ln.get("text"))
        is_frag = bool(k) and (any(o != k and len(o) > len(k) and k in o for o in _lt)
                               or any(len(o) > len(k) and k in o and len(k) < 0.6 * len(o) for o in _pq))
        if not is_frag:
            _keep.append(ln)
    d["lines"] = _keep
    bad_lines = [ln.get("text") for ln in d.get("lines") or []
                 if isinstance(ln, dict) and ln.get("text") and not quote_in_prose(ln["text"], quotes)]
    if bad_lines:
        raise ValueError("这些台词原文里没有：%s" % " / ".join(str(x)[:16] for x in bad_lines[:4]))
    bad_spk = [ln.get("n") for ln in d.get("lines") or []
               if isinstance(ln, dict) and ln.get("speaker") and ln["speaker"] not in cast_ids]
    if bad_spk:
        raise ValueError("台词 %s 的 speaker 不是 cast 里的编号" % bad_spk[:5])
    # 软核：别称要在原文/画面稿里出现过，否则删掉这个别称（不整份重来）
    # 别称也不许是**另一张卡的名字**——157 实测把「林阿四」写成了阿棠的别称，而林阿四是 C5
    all_names = {str(c.get("name") or "").strip() for c in d["cast"] if isinstance(c, dict)}
    # 被两个人同时认领的别称（157 v2：老烟枪和探长都写了「男人」）→ 谁也不给：
    # 留着的话 canon_text 会把正文里每个「男人」都换成其中一个人，说话人/在场全乱。
    _claim = {}
    for c in d["cast"]:
        if isinstance(c, dict):
            for a in c.get("aliases") or []:
                _claim[a] = _claim.get(a, 0) + 1
    for c in d["cast"]:
        if isinstance(c, dict):
            me = str(c.get("name") or "").strip()
            c["aliases"] = [a for a in c.get("aliases") or []
                            if (in_text(a, prose, pictures) or a in _tbl) and a != me and a not in all_names
                            and a not in me                                   # 本名的一截不算别名（P284）
                            and _claim.get(a, 0) == 1]
    # 软核：场景 present/mentioned 只能是 cast 编号
    for s in d["scenes"]:
        for k in ("present", "mentioned"):
            s[k] = [x for x in s.get(k) or [] if x in cast_ids]
    # P264：人物表上的人要被认领（name 或 alias）。在别称软删**之后**判——被删掉的别称不算认领。
    if _tbl:
        claimed = set()
        for c in d["cast"]:
            if isinstance(c, dict):
                claimed |= {str(c.get("name") or "").strip()} | set(c.get("aliases") or [])
        others = [str(c.get("name") or "").strip() for c in d["cast"]
                  if isinstance(c, dict) and str(c.get("name") or "").strip() not in _tbl]
        d.setdefault("ambiguities", [])
        _known = {str(a.get("name") or "") for a in d["ambiguities"] if isinstance(a, dict) and a.get("kind") == "cast"}
        for nm in _tbl:
            if nm in claimed or nm in _known:
                continue
            if strict_people and not _is_crowd(nm):
                raise ValueError("人物表里的「%s」清单没认领：cast 里找出原文对应的那个人，name 改成「%s」，"
                                 "原文里的叫法写进 aliases；确实对不上的写进 ambiguities（kind=cast）" % (nm, nm))
            d["ambiguities"].append({"kind": "cast", "name": nm, "candidates": list(others),
                                     "why": "人物表里的这个人清单没认领"})
    mark_cards(d, card_names)
    return d


# ─────────────────────────── 生成（问千问）───────────────────────────

def _people_lines(people):
    """人物表 → 提示词里的行：name｜role｜relation。元素可以是 dict 或纯名字。"""
    out = []
    for p in people or []:
        if isinstance(p, dict):
            nm = str(p.get("name") or "").strip()
            if not nm:
                continue
            out.append("｜".join([nm, str(p.get("role") or "").strip(), str(p.get("relation") or "").strip()]))
        else:
            nm = str(p or "").strip()
            if nm:
                out.append(nm)
    return out


def _place_names(places):
    """地点表 → 名字列表。元素可以是 dict（name/place/text）或字符串。"""
    out = []
    for x in places or []:
        if isinstance(x, dict):
            x = x.get("name") or x.get("place") or x.get("text")
        x = str(x or "").strip()
        if x and x not in out:
            out.append(x)
    return out


def build(prose, pictures="", card_names=(), scene_card_names=(), call_model=None, instruction="",
          people=(), places=()):
    """从原文（和画面稿）抽一份拍摄清单。返回 dict；抽不出来返回 None（上层走老路）。

    call_model(system, user) -> str。不传就用 qwen_client.chat（温度低、不长）。
    people：本话安排的人物表 [{name,role,relation}]；places：安排的地点表 [名字]。
    P263/P264：清单的人名以人物表为准、地点归到地点表——下游（提示词、场景卡）只认表上的名字，
    清单按原文叫法写（灰狸花猫／临街窗边）就永远对不上，出片前门修了等于没修（STORY_164 实测）。
    """
    from .model_json import chat_json
    if not str(prose or "").strip():
        return None
    _pl = _people_lines(people)
    _names = [ln.split("｜", 1)[0] for ln in _pl]
    _places = _place_names(places)
    if _names:
        # P313：有本话人物表 → 人物卡名单只给表上的人；别的话的人物卡不给模型认领（"男人"被认成第 1 话的李师傅）
        card_names = [c for c in (card_names or []) if str(c or "").strip() in _names]
    if call_model is None:
        # 走 authoring._q：有退避重试，而且进 _calls.log（原来直接调 qwen_client，日志里查不到这次调用）
        from . import authoring as _au

        _budget = [6400, 8000, 9600]      # 截断了再用同样的预算重试是白试：每次加码（P253）
        _try = {"i": 0}

        def call_model(system, user, **kw):
            mt = _budget[min(_try["i"], len(_budget) - 1)]
            _try["i"] += 1
            return _au._q(system, user, mt=mt, temperature=0.2)
    ins = instruction or _default_instruction()
    user = ("【人物卡（有参考图的人）】%s\n【场景卡】%s\n"
            "【本话人物表（name｜role｜relation）：表上的人 cast.name 一律用表上的名字，原文里的其他叫法写进 aliases】\n%s\n"
            "【地点表（location 尽量用这里的名字）】%s\n\n【原文】\n%s"
            % ("、".join(card_names) or "无", "、".join(scene_card_names) or "无",
               "\n".join(_pl) or "无", "、".join(_places) or "无", str(prose)[:6000]))
    if str(pictures or "").strip():
        user += "\n\n【画面稿（地点行和台词行可参考，但事实以原文为准）】\n" + str(pictures)[:4000]

    global LAST_ERROR
    LAST_ERROR = ""
    _errs = []
    _round = {"i": 0}

    def _v(d):
        # 前两轮人物表的人没被认领就打回（把错误原因带给模型改）；第三轮放行，写进 ambiguities 让出片前门报——
        # 三轮全打回就是「清单抽不出来」，比一条无法判断更糟
        _round["i"] += 1
        try:
            return validate(d, prose, pictures, card_names, people_names=_names, strict_people=(_round["i"] < 3))
        except Exception as ex:
            _errs.append(str(ex)[:160])
            raise
    try:
        d, _raw = chat_json(ins, user, call_model, validate=_v, tries=3, normalize=normalize)
    except Exception as ex:
        _errs.append("chat_json: " + str(ex)[:160])
    if _errs and (not locals().get("d")):
        LAST_ERROR = " ‖ ".join(_errs[-3:])
        return None
    if _errs:
        LAST_ERROR = " ‖ ".join(_errs[-3:])          # 有过失败但最后成功了，也留着看
    if not isinstance(d, dict):
        return None
    mark_cards(d, card_names)
    bind_places(d, _places)
    demote_foreign_cast(d, _names)                                  # P313：表外的卡名改回原文叫法
    apply_ledger(d, prose, _names)                                  # P310：台词清单是唯一来源
    d["built_at"] = time.time()
    d["prose_sig"] = content_sig(prose)
    d["pictures_sig"] = content_sig(pictures)
    return d


def apply_ledger(sl, prose, people_names=()):
    """把台词清单落到拍摄清单（P310）：lines **按清单重建**，每句正文引号一条（原文原句、顺序照正文）。
    · 说话人：清单里有明写依据 → 用清单的（确定，带依据句）；没有 → 用模型对同一句的判断（同句/子句/合并句都认）；都没有 → 存疑。
    · 没说出口/招牌/拟声 → 不进 lines；回忆里的话进 lines 但标 recalled=True（画面稿没有它不算丢，有它要提示）。
    · 每条带 count（正文说了几次）。返回改动说明列表。"""
    from . import dialogue_ledger as _dl
    led = _dl.ledger(prose, people_names)
    if not led or not isinstance(sl, dict):
        return []
    notes = []
    model_lines = [ln for ln in (sl.get("lines") or []) if isinstance(ln, dict) and _n(ln.get("text"))]

    def _model_match(e):
        k = e["norm"]
        for ln in model_lines:
            t = _n(ln.get("text"))
            if t == k or (len(k) >= 4 and k in t) or (len(t) >= 4 and t in k and len(t) >= 0.6 * len(k)):
                return ln
        return None

    new_lines = []
    for e in led:
        if e["kind"] in (_dl.KIND_UNSPOKEN, _dl.KIND_SIGNAGE, _dl.KIND_ONOMAT, _dl.KIND_TERM):
            if _model_match(e):
                notes.append("去掉不是台词的「%s」（%s）" % (e["text"][:16], {"unspoken": "没说出口", "signage": "招牌/文字", "onomat": "拟声", "term": "引称"}[e["kind"]]))
            continue
        m = _model_match(e)
        spk, conf, ev = "", "存疑", ""
        if e["speaker"] and e["confidence"] == "确定":
            want = id_of_name(sl, e["speaker"])
            if want:
                spk, conf, ev = want, "确定", e["evidence"]
                if m and str(m.get("speaker") or "") and str(m.get("speaker")) != want:
                    notes.append("「%s」说话人按原文依据改为 %s（%s）" % (e["text"][:16], e["speaker"], e["evidence"][:20]))
        if not spk and m:
            spk, conf, ev = str(m.get("speaker") or ""), str(m.get("confidence") or "存疑"), str(m.get("evidence") or "")
        if not spk:
            conf = "存疑"
        new_lines.append({"n": len(new_lines) + 1, "text": e["text"], "speaker": spk, "confidence": conf, "evidence": ev,
                          "scene": str((m or {}).get("scene") or ""), "count": e["count"], "recalled": e["kind"] == _dl.KIND_RECALLED})
    if new_lines:
        sl["lines"] = new_lines
    sl["ledger_summary"] = _dl.summary(led)
    return notes


def demote_foreign_cast(sl, table_names):
    """有本话人物表时，cast 里名字不在表上、又是别的话的卡名的人 → 改回原文叫法（第一个别称），去掉卡标记，记 foreign_of。
    返回改动说明列表。"""
    if not table_names or not isinstance(sl, dict):
        return []
    notes = []
    for c in sl.get("cast") or []:
        if not isinstance(c, dict):
            continue
        nm = str(c.get("name") or "").strip()
        if nm and nm not in table_names and c.get("card"):
            al = [a for a in (c.get("aliases") or []) if str(a).strip()]
            new = al[0] if al else nm
            notes.append("「%s」不在这一话的人物表里，清单把「%s」认成了他 → 改回「%s」" % (nm, "/".join(al[:2]) or nm, new))
            c["foreign_of"] = nm
            c["name"] = new
            c["aliases"] = [a for a in al if a != new]
            c["card"] = False
    if notes:
        sl["cast_notes"] = notes
    return notes


def _default_instruction():
    try:
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "presets" / "instructions" / "拍摄清单_指令词.txt"
        return p.read_text(encoding="utf-8")
    except Exception:
        return _FALLBACK_INS


_FALLBACK_INS = """你是剧组的场记。读这一话原文，只输出一份 JSON 拍摄清单，不改写故事。
每条事实必须能在原文里找到依据，evidence 填原文里的那句话。判不准的写进 ambiguities，不要猜。
{"cast":[{"id":"C1","name":"原文里的名字","aliases":["原文里出现过的别称"],"evidence":"原文句"}],
 "extras":[{"name":"群体名","count":"数量","scenes":["S2"]}],
 "scenes":[{"id":"S1","location":"地点","spot":"比地点更细的位置，没有留空","time":"时间","present":["C1"],"mentioned":[],"must_happen":["必须发生的事"],"end_state":"结束时人在哪、什么状态","evidence":"原文句"}],
 "lines":[{"n":1,"text":"原文台词一字不改","speaker":"C1","scene":"S1","confidence":"确定"}],
 "ambiguities":[{"kind":"speaker","line":3,"candidates":["C3","C1"],"why":"一句话，30字内"}]}
只输出 JSON。"""


# ─────────────────────────── 查询（下游读）───────────────────────────

def cast_by_id(sl):
    return {c.get("id"): c for c in (sl or {}).get("cast") or [] if isinstance(c, dict)}


def id_of_name(sl, name):
    """名字或别称 → 编号。"""
    n = str(name or "").strip()
    for c in (sl or {}).get("cast") or []:
        if n == str(c.get("name") or "").strip() or n in (c.get("aliases") or []):
            return c.get("id")
    return ""


def name_of_id(sl, cid):
    return str((cast_by_id(sl).get(cid) or {}).get("name") or "")


def same_place(a, b):
    """两个地点名是不是同一个地方：相等 / 互含 / 4 字连续窗口对上 / 二字组合≥0.6。
    和 make_h3_prompts 绑卡用的口径一致——原来检查器按整串比，
    「十六铺码头老烟枪杂货摊」和「十六铺码头西侧老烟枪的杂货摊」被判成绑错（157）。"""
    a, b = _n(a), _n(b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 4 and any(short[i:i + 4] in long_ for i in range(0, len(short) - 3)):
        return True
    return _overlap(short, long_) >= 0.6


def place_in_table(name, table):
    """一个地点名归到地点表（安排的地点表 / 场景卡名单）里的哪一个。
    精确 > 互含（取表里最长的那个：「面包店内（库房）」同时含「面包店」「面包店内」，细的那张才对）> same_place；
    都不像返回 ""——不像就是不像，别硬塞一张（STORY_164「临街窗边」被绑到店门口就是硬塞出来的）。
    P263：build 归地点表、check 找场景卡、提示词绑卡三处共用这一个口径，不然修了又被绑回去。"""
    n = str(name or "").strip()
    tbl = [str(x or "").strip() for x in (table or []) if str(x or "").strip()]
    if not n or not tbl:
        return ""
    if n in tbl:
        return n
    nk = _n(n)
    if not nk:
        return ""
    for t in tbl:
        if _n(t) == nk:
            return t
    # 互含分两种：表名是地点名的一截（地点写得更细：「面包店内（库房）」含「面包店内」「面包店」）→ 取最长的表名；
    # 地点名是表名的一截（表名更细：「面包店」对「面包店内」「面包店门口」）→ 取多出的字最少的那张，最像。
    inner = [t for t in tbl if _n(t) and _n(t) in nk]
    if inner:
        return max(inner, key=lambda t: len(_n(t)))
    outer = [t for t in tbl if _n(t) and nk in _n(t)]
    if outer:
        return min(outer, key=lambda t: (len(_n(t)), tbl.index(t)))
    like = [t for t in tbl if same_place(n, t)]
    if like:
        return max(like, key=lambda t: (_overlap(n, t), len(_n(t))))
    return ""


def bind_places(sl, table):
    """清单每场戏的 location 归到地点表：归得到 → location 改成表名、原写法存 spot（已有 spot 不覆盖）、
    place_unresolved=False；归不到 → place_unresolved=True 原样留着。表空原样返回。
    返回 sl（原地改）。"""
    tbl = [str(x or "").strip() for x in (table or []) if str(x or "").strip()]
    if not sl or not tbl:
        return sl
    for s in (sl or {}).get("scenes") or []:
        if not isinstance(s, dict):
            continue
        loc = str(s.get("location") or "").strip()
        hit = place_in_table(loc, tbl)
        if hit:
            if loc != hit and not str(s.get("spot") or "").strip():
                s["spot"] = loc
            s["location"] = hit
            s["place_unresolved"] = False
        else:
            s["place_unresolved"] = True
    return sl


def scene_card(s, card_names):
    """这场戏该绑哪张场景卡：s["card"]（用户答过的）在名单里就用它，否则 location 归到卡名单；
    都没有返回 ""（出片前门问用户，提示词沿用上一段——不猜）。"""
    s = s or {}
    cn = [str(x or "").strip() for x in (card_names or []) if str(x or "").strip()]
    c = str(s.get("card") or "").strip()
    if c and c in cn:
        return c
    return place_in_table(s.get("location"), cn)


def _loc_score(loc, tk):
    """地点名对这段文字（已去标点）的证据强度：整个出现 3 分；连着 4 个字对上 2 分；否则 0。"""
    lk = _n(loc)
    if not lk:
        return 0.0
    if lk in tk:
        return 3.0                                         # 地点整个出现，强证据
    # 「陌生的青石长街」vs 画面稿「青石长街」：地点是名词短语，连着的 4 个字对上就是它
    # （二字组合会被前面的修饰词稀释：6 组只中 3 组，反而判不出）
    if len(lk) >= 4 and any(lk[i:i + 4] in tk for i in range(0, max(1, len(lk) - 3))):
        return 2.0
    return 0.0


def scene_for_text(sl, text):
    """一段画面稿属于哪场：按 location / must_happen / evidence 和这段文字的**二字组合重合度**取最高。
    返回 (scene dict 或 None, 分数)。分数 0 = 没有依据，上层别乱绑。
    不看语序、先把别称归一（老者→师傅），换个说法也对得上。
    location 被 bind_places 归成表名之后原写法在 spot 里，两个哪个对得上算哪个（spot 空时和原来一样）。"""
    # P355：台词行不参与地点匹配——国王说「你的队伍已在王宫后院等候」，这段还在大殿，却被绑到后院
    text = "\n".join(ln for ln in str(text or "").split("\n") if not _PIC_LINE_RE.match(ln.strip()))
    t = canon_text(sl, text)
    if not _n(t):
        return None, 0
    tk = _n(t)
    best, best_s = None, 0.0
    for s in (sl or {}).get("scenes") or []:
        score = max(_loc_score(s.get("location"), tk), _loc_score(s.get("spot"), tk))
        for piece in list(s.get("must_happen") or []) + [s.get("evidence", "")]:
            ov = _overlap(canon_text(sl, piece), t)
            if ov >= 0.6:
                score += ov
        if score > best_s:
            best, best_s = s, score
    return best, (best_s if best_s >= 0.6 else 0)


def line_of(sl, text):
    """一句台词在清单里那一条（不猜：同一句才算）。"""
    for ln in (sl or {}).get("lines") or []:
        if _same_line(ln.get("text"), text):
            return ln
    return None


def speaker_of(sl, text):
    """一句台词的说话人编号（清单里查，不猜）。找不到返回 ""。"""
    ln = line_of(sl, text)
    return str((ln or {}).get("speaker") or "")


def present_in_scene(sl, scene_id):
    s = next((x for x in (sl or {}).get("scenes") or [] if x.get("id") == scene_id), None)
    return list((s or {}).get("present") or [])


def needs_card(sl):
    """有台词或在场、但没有人物卡的人。这些人要建卡记录（出不出图另说）。"""
    speaking = {ln.get("speaker") for ln in (sl or {}).get("lines") or [] if isinstance(ln, dict)}
    present = set()
    for s in (sl or {}).get("scenes") or []:
        present |= set(s.get("present") or [])
    out = []
    for c in (sl or {}).get("cast") or []:
        if not c.get("card") and (c.get("id") in speaking or c.get("id") in present):
            out.append(c)
    return out


# ─────────────────────────── 画面稿对照清单（确定性）───────────────────────────

def pictures_dialogue(pictures):
    """画面稿里的台词行 [(行号, 说话人写法, 台词)]。"""
    out = []
    for i, ln in enumerate(str(pictures or "").split("\n")):
        m = _PIC_LINE_RE.match(ln.strip())
        if m:
            out.append((i, m.group(1).strip(), m.group(3).strip()))
    return out


def fix_pictures_speakers(pictures, sl):
    """画面稿台词行的说话人对齐到清单：清单里**确定**的才改，存疑的不动。
    返回 (新画面稿, [改动], [存疑])。这是"程序把确定的信息传下去"，不是猜。"""
    lines = str(pictures or "").split("\n")
    changed, doubtful = [], []
    for i, w, q in pictures_dialogue(pictures):
        ln = line_of(sl, q)
        if not ln:
            continue
        want_id = str(ln.get("speaker") or "")
        want = name_of_id(sl, want_id)
        if not want:
            continue
        cur_id = id_of_name(sl, w)
        if str(ln.get("confidence") or "确定") != "确定":
            if cur_id != want_id:
                doubtful.append((i, w, want, q))
            continue
        if cur_id != want_id:
            lines[i] = lines[i].replace(w, want, 1)
            changed.append("第%d行 %s→%s：%s" % (i + 1, w, want, q[:16]))
    return "\n".join(lines), changed, doubtful


# ─────────────────────────── 出片前检查（三态）───────────────────────────

def merged_picture_lines(lines, pic_keys):
    """画面稿里哪些台词行是清单里**相邻几句**拼起来的（P284）。返回那些行的规整文本集合。"""
    keys = [_n(x.get("text")) for x in (lines or []) if isinstance(x, dict)]
    out = set()
    for pk in pic_keys or []:
        if not pk:
            continue
        for i in range(len(keys)):
            acc = ""
            for j in range(i, min(len(keys), i + 4)):
                acc += keys[j]
                if j > i and acc == pk:
                    out.add(pk)
                    break
                if len(acc) >= len(pk):
                    break
    return out


def merged_line_indexes(lines, pic_keys):
    """清单里哪些句（下标）被画面稿的某一行以"相邻几句拼一起"的方式包含了（P284）。"""
    keys = [_n(x.get("text")) for x in (lines or []) if isinstance(x, dict)]
    covered = set()
    for pk in pic_keys or []:
        if not pk:
            continue
        for i in range(len(keys)):
            acc = ""
            for j in range(i, min(len(keys), i + 4)):
                acc += keys[j]
                if j > i and acc == pk:
                    covered.update(range(i, j + 1))
                    break
                if len(acc) >= len(pk):
                    break
    return covered


def check(sl, pictures, segments, card_names=(), scene_card_names=(), prose="",
          arrangement=None, slices=None):
    """六项检查（＋有安排时的三项：结尾 / 情节四段 / 时长）。
    返回 {"status": 通过|有问题|无法判断, "items": [...], "asks": [...]}。

    segments: [{"no":1,"text":画面稿片段,"scene":绑定的场景名,"chars":[名字],"prompt":...,
                "beat":段名,"beat_index":0~3（判不出 -1）}]
    每一项 {"check","status","detail","fix"}：fix = "auto"（确定能修）| "ask"（要问用户）| ""。
    asks: [{"question","options","kind","ref"}] 给页面显示。

    arrangement：已确认的本话安排（settings.arrangement，status=="confirmed"）；None 就不查那三项。
    slices：整话的切片（带 beat_index）。安排的三项要看**整话**——出片前门只把要出的前 N 段
    当 segments 传进来，拿它们查"结尾拍到没有"必然误报"没拍到"（后面的段还没轮到）。
    没传 slices 就退回用 segments（零模型测试就是这么喂的）。
    """
    items, asks = [], []
    if not sl:
        items.append({"check": "清单", "status": STATUS_UNKNOWN, "detail": "没有拍摄清单，无法核对", "fix": ""})
        # 安排的三项不靠清单，照查——总时长放不放得下和清单没关系，老项目也该被问到
        a_items, a_asks = arrangement_checks(arrangement, slices if slices is not None else segments)
        items.extend(a_items)
        asks.extend(a_asks)
        return {"status": STATUS_UNKNOWN, "items": items, "asks": asks}
    if card_names:
        # 卡是会变的（出片前门刚建了卡、用户在资源页手工建了卡）：按现在的卡名单重新打标，别用建清单时冻结的。
        # 在副本上打，盘上的清单不动（P269）
        sl = copy.deepcopy(sl)
        mark_cards(sl, card_names)
    cast = cast_by_id(sl)
    scene_names = [str(s.get("location") or "") for s in sl.get("scenes") or []]
    _scards = [str(x or "").strip() for x in (scene_card_names or []) if str(x or "").strip()]
    # P264：本话安排的人物表。有表 → 表外的人是配角/群演（不建卡、不要求绑）；没表 → 旧口径一字不改
    _tbl_people = _arrangement_people(arrangement)

    def _cast_names(cid):
        c = cast.get(cid) or {}
        return {str(c.get("name") or "").strip()} | set(c.get("aliases") or [])

    # ⓪ P313：别的话的人跑进这一话（清单把原文里的"男人"认成了第 1 话的李师傅）
    if _tbl_people:
        for c in (sl.get("cast") or []):
            if isinstance(c, dict) and (c.get("foreign_of") or (c.get("card") and str(c.get("name") or "").strip() not in _tbl_people)):
                items.append({"check": "人物", "status": STATUS_PROBLEM,
                              "detail": "「%s」不在这一话的人物表里（表上是：%s），清单把「%s」认成了他——请看是不是别的话的人" % (
                                  c.get("foreign_of") or c.get("name"), "、".join(sorted(_tbl_people)), "/".join((c.get("aliases") or [c.get("name")])[:2])), "fix": ""})
    # ① 台词：每句有明确说话人，画面稿没换人
    dl = pictures_dialogue(pictures)
    _all_line_texts = [_n(x.get("text")) for x in sl.get("lines") or []]
    _merged_pics = merged_picture_lines(sl.get("lines") or [], [_n(q) for _, _, q in dl])
    for i, w, q in dl:
        ln = line_of(sl, q)
        if not ln and _n(q) in _merged_pics:
            continue                                   # 这一行是相邻几句台词拼的（P284），说话人按第一句判
        if not ln:
            k = _n(q)
            if any(k and o != k and len(o) > len(k) and k in o for o in _all_line_texts):
                items.append({"check": "台词", "status": STATUS_PROBLEM,
                              "detail": "画面稿把一句话的一截当成台词：%s（%s）" % (q[:16], w), "fix": ""})
            else:
                items.append({"check": "台词", "status": STATUS_UNKNOWN,
                              "detail": "画面稿这句原文里对不上：%s" % q[:20], "fix": ""})
            continue
        want_id = ln.get("speaker") or ""
        cur_id = id_of_name(sl, w)
        if ln.get("confidence") != "确定":
            cands = [name_of_id(sl, c) for c in (next((a.get("candidates") for a in sl.get("ambiguities") or []
                                                        if a.get("line") == ln.get("n")), None) or [want_id])]
            items.append({"check": "台词", "status": STATUS_UNKNOWN,
                          "detail": "「%s」说话人无法确定" % q[:20], "fix": "ask"})
            _opts = [c for c in cands if c]
            if not _opts:
                _opts = [str(c.get("name") or "") for c in (sl.get("cast") or []) if isinstance(c, dict) and str(c.get("name") or "").strip()]   # P323d：没候选就列全部在场人物
            asks.append({"kind": "speaker", "ref": ln.get("n"),
                         "question": "这一句的说话人无法确定：「%s」" % q,
                         "options": _opts})
        elif cur_id != want_id:
            items.append({"check": "台词", "status": STATUS_PROBLEM,
                          "detail": "「%s」原文是%s说的，画面稿写成%s" % (q[:16], name_of_id(sl, want_id), w),
                          "fix": "auto"})
    # 原文有、画面稿没有的台词（丢了）；画面稿里是别句片段的台词（编出来的一截）
    pic_texts = [_n(q) for _, _, q in dl]
    _merged_ok = merged_line_indexes(sl.get("lines") or [], pic_texts)
    for _k, ln in enumerate(sl.get("lines") or []):
        if _k in _merged_ok:
            continue                                   # 和相邻句合成了一行（P284），不算丢
        if ln.get("recalled"):
            continue                                   # P310：回忆里的话，画面稿不写不算丢
        if not any(_same_line(ln.get("text"), q) for _, _, q in dl):
            items.append({"check": "台词", "status": STATUS_PROBLEM,
                          "detail": "原文这句台词画面稿里没有：%s（%s）" % (str(ln.get("text"))[:18], name_of_id(sl, ln.get("speaker"))),
                          "fix": ""})
    # P310：同一句剧本里出现次数不能超过正文；回忆里的话当当前对白要提示；没说出口的话当台词是错
    try:
        from . import dialogue_ledger as _dl
        _led = _dl.ledger(prose, [str(c.get("name") or "") for c in (sl.get("cast") or []) if isinstance(c, dict)]) if prose else []
    except Exception:
        _led = []
    if _led:
        _pic_counts = {}
        for _, _w, _q in dl:
            _pic_counts[_n(_q)] = _pic_counts.get(_n(_q), 0) + 1
        _seen_norm = set()
        for e in _led:
            if e["norm"] in _seen_norm:
                continue
            _seen_norm.add(e["norm"])
            have = _pic_counts.get(e["norm"], 0)
            if e["kind"] == _dl.KIND_SPOKEN and have > e["count"]:
                items.append({"check": "台词", "status": STATUS_PROBLEM,
                              "detail": "「%s」正文说了 %d 次，画面稿里出现 %d 次——重复补入了" % (e["text"][:16], e["count"], have), "fix": ""})
            elif e["kind"] == _dl.KIND_UNSPOKEN and have:
                items.append({"check": "台词", "status": STATUS_PROBLEM,
                              "detail": "「%s」在正文里没说出口，画面稿写成了台词" % e["text"][:16], "fix": ""})
            elif e["kind"] == _dl.KIND_RECALLED and have:
                items.append({"check": "台词", "status": STATUS_UNKNOWN,
                              "detail": "「%s」在正文里是回忆里的话，画面稿当成当前对白——请看" % e["text"][:16], "fix": ""})
    if not any(x["check"] == "台词" for x in items):
        items.append({"check": "台词", "status": STATUS_PASS, "detail": "%d 句说话人都对上了" % len(dl), "fix": ""})

    # ② 地点：每段绑定的场景与该段事件一致
    #    只有一场戏 → 每段都是它；判不出的段继承上一段（和 make_h3_prompts 的口径一样）
    scenes_all = sl.get("scenes") or []
    last_s = scenes_all[0] if len(scenes_all) == 1 else None
    _asked_scene = set()
    for g in segments or []:
        s, score = scene_for_text(sl, g.get("text"))
        if (s is None or score <= 0) and len(scenes_all) == 1:
            s, score = scenes_all[0], 1
        if (s is None or score <= 0) and last_s is not None:
            s, score = last_s, 1                          # 继承上一段
        bound = str(g.get("scene") or "")
        if s is None or score <= 0:
            items.append({"check": "地点", "status": STATUS_UNKNOWN,
                          "detail": "第%s段在哪场戏判不出（清单里没有依据）" % g.get("no"), "fix": ""})
            continue
        last_s = s
        want = str(s.get("location") or "")
        if _scards:
            # P263：传了场景卡名单就按**卡名**比。原来拿清单地点「临街窗边」比，报「应在临街窗边」，
            # 自动改绑成一张不存在的卡，重写提示词又绑回店门口——修了等于没修（STORY_164）。
            # 找不到卡的场戏只问一次：用哪张卡是用户的事，程序不猜。
            want_card = scene_card(s, _scards)
            if not want_card:
                if s.get("id") not in _asked_scene:
                    _asked_scene.add(s.get("id"))
                    items.append({"check": "地点", "status": STATUS_UNKNOWN,
                                  "detail": "「%s」（第%s段起）没有对应的场景卡，不知道该用哪张" % (want, g.get("no")),
                                  "fix": "ask"})
                    asks.append({"kind": "scene", "ref": s.get("id"),
                                 "question": "「%s」这场戏用哪张场景卡？（场景卡里没有这个地点；原文写法：%s）"
                                             % (want, str(s.get("spot") or "").strip() or want),
                                 "options": list(_scards) + ["新建场景卡「%s」" % want]})
                continue
            want = want_card
        # 有卡名单时 bound/want 都是卡名，按卡名比（same_place 会把「面包店门口」「面包店内」判成一张，P269）；
        # 老口径（没卡名单）仍用 same_place
        _mismatch = (bool(bound) and place_in_table(bound, _scards) != want) if _scards \
            else (bool(bound) and not same_place(bound, want))
        if _mismatch:
            items.append({"check": "地点", "status": STATUS_PROBLEM,
                          "detail": "第%s段应在「%s」，绑的是「%s」" % (g.get("no"), want, bound), "fix": "auto"})
    if not any(x["check"] == "地点" for x in items):
        items.append({"check": "地点", "status": STATUS_PASS, "detail": "各段场景都对", "fix": ""})

    # ③ 出场人物：这一段要绑的人 = 这段文字里开口的人 + 被点名且在场的人
    #    （原来按整场戏的在场名单要求每一段，探长中途登场就把前面几段全误报——157）
    last_s = scenes_all[0] if len(scenes_all) == 1 else None
    for g in segments or []:
        s, score = scene_for_text(sl, g.get("text"))
        if (s is None or score <= 0) and last_s is not None:
            s, score = last_s, 1
        if s is None or score <= 0:
            continue
        last_s = s
        seg_txt = canon_text(sl, g.get("text"))
        # P264：按**编号**比，不按名字——chars 里写的是别称（灰狸花猫）而清单本名是流浪猫，按名字比永远「没绑」。
        # 有安排人物表时只要求有卡的人绑（表外的配角/动物不建卡，提示词按文字描述，绑不上是对的）；
        # 没表沿用旧口径：没卡的人 ③b 会给他建卡，建完就得绑。
        speak = set()
        for _, _w, _q in pictures_dialogue(g.get("text") or ""):
            _ln = line_of(sl, _q)
            if _ln and _ln.get("speaker"):
                speak.add(str(_ln["speaker"]))
        named = {c for c in (s.get("present") or []) if name_of_id(sl, c) and name_of_id(sl, c) in seg_txt}
        need = {x for x in (speak | named) if x and x in cast and ((cast[x].get("card")) or not _tbl_people)}
        have = {id_of_name(sl, x) for x in (g.get("chars") or [])}
        miss = sorted(name_of_id(sl, x) for x in need if x not in have)
        if miss:
            items.append({"check": "出场人物", "status": STATUS_PROBLEM,
                          "detail": "第%s段%s在这段里开口/被点名，提示词没绑" % (g.get("no"), "、".join(miss)), "fix": "auto"})
        p = str(g.get("prompt") or "")
        if "不出现任何别的人" in p and (s.get("mentioned") or any(
                str(x.get("scenes") or []).find(str(s.get("id"))) >= 0 for x in sl.get("extras") or [])):
            items.append({"check": "出场人物", "status": STATUS_PROBLEM,
                          "detail": "第%s段提示词禁止别的人出现，但这场戏有配角/群演" % g.get("no"), "fix": "auto"})
    if not any(x["check"] == "出场人物" for x in items):
        items.append({"check": "出场人物", "status": STATUS_PASS, "detail": "在场人物都绑上了", "fix": ""})

    # ③b 有台词/在场却没有人物卡的人。开了口的一定在场 → 有问题（建卡）；
    #     清单说在场、但整篇画面稿里一句没说、名字也没出现在动作里的 → 可能只是被提到
    #     （157 的「船长」：老烟枪说"船长说舱里没行李"）→ 无法判断，问用户，不擅自建卡
    # 只数叙述文字：台词里被提到不算在场（157 的船长只出现在老烟枪的台词里）
    _narr = canon_text(sl, "\n".join(ln for ln in str(pictures or "").split("\n") if not _PIC_LINE_RE.match(ln.strip())))
    _speakers = {ln.get("speaker") for ln in sl.get("lines") or [] if isinstance(ln, dict)}
    for c in needs_card(sl):
        nm = str(c.get("name") or "")
        if _tbl_people:
            # P264：有安排人物表时按表处理。表外的人（奶猫、路人）是配角/动物——出片那边本来就不给他们建卡
            # （P261），这里再报「没卡」「在场吗」就是问一个没人会答的问题，还把出片挡住。
            if not (_cast_names(c.get("id")) & _tbl_people):
                if _is_crowd(nm):
                    items.append({"check": "出场人物", "status": STATUS_PASS,
                                  "detail": "%s按动物/群演处理，不建卡" % nm, "fix": ""})
                else:
                    items.append({"check": "出场人物", "status": STATUS_PASS,
                                  "detail": "%s不在本话人物表里，按配角处理（提示词按文字描述，不建卡）" % nm, "fix": ""})
                continue
            if _is_crowd(nm):
                # 表上的动物（流浪猫）也不建卡：人设图那条链是给人设计的（定脸、三视图），给猫出图只会出怪东西；
                # 动物靠画面稿里的文字描述，出片前门那边同样跳过它（口径一致）
                items.append({"check": "出场人物", "status": STATUS_PASS,
                              "detail": "%s按动物/群演处理，不建卡" % nm, "fix": ""})
                continue
        if c.get("id") in _speakers or (nm and _narr.count(nm) >= 2):
            items.append({"check": "出场人物", "status": STATUS_PROBLEM,
                          "detail": "%s有戏份但没有人物卡（脸会每段现编）" % nm, "fix": "auto"})
        else:
            items.append({"check": "出场人物", "status": STATUS_UNKNOWN,
                          "detail": "清单说%s在场，但画面稿里既没开口也没动作——可能只是被提到" % nm, "fix": "ask"})
            asks.append({"kind": "present", "ref": c.get("id"),
                         "question": "「%s」在这场戏里真的在场吗？（原文只提到他，画面稿里没有他的动作）" % nm,
                         "options": ["在场，要建卡", "不在场，只是被提到"]})
    # ③c 人物表上的人清单没认出是谁（validate 第三轮放行时写进 ambiguities kind=cast）：
    #     他的卡绑不上，但程序判不了他是清单里的哪一个——报无法判断，不问（问了也没法自动改清单）
    for a in (sl.get("ambiguities") or []) if _tbl_people else []:
        if isinstance(a, dict) and a.get("kind") == "cast" and str(a.get("name") or "").strip():
            items.append({"check": "出场人物", "status": STATUS_UNKNOWN,
                          "detail": "人物表里的%s清单没认出是谁（候选：%s）——他的卡绑不上"
                                    % (a["name"], "、".join(str(x) for x in (a.get("candidates") or [])) or "无"),
                          "fix": ""})

    # ④ 情节：关键事件都有对应镜头。must_happen 是千问的转述（"阿棠询问林阿四的下落"），
    #    按二字组合比画面稿必然对不上（157 实测 15 条全报）。改按**实体词**：
    #    事件里的人名 + 2 字名词，≥50% 出现在段落文字里就算拍到了。
    #    对整份画面稿（不是只对要出片的前 N 段——后面的事件当然还没拍到，157 一口气误报 8 条）；
    #    事件里的词只认原文里出现过的，不在原文的是转述/切碎（「码头等待」→「头等」），不计分。
    all_text = canon_text(sl, pictures) if str(pictures or "").strip() else canon_text(sl, "".join(str(g.get("text") or "") for g in segments or []))
    _pr = canon_text(sl, prose) if str(prose or "").strip() else ""
    for s in sl.get("scenes") or []:
        for ev in s.get("must_happen") or []:
            toks = event_tokens(sl, ev)
            if _pr:
                toks = [t for t in toks if t in _pr]
            if not toks:
                continue
            hit = sum(1 for t in toks if t in all_text)
            if hit < max(1, (len(toks) + 1) // 2):
                items.append({"check": "情节", "status": STATUS_UNKNOWN,
                              "detail": "这件事画面稿里按字面找不到：%s（可能换了说法拍了，也可能漏了）" % str(ev)[:24],
                              "fix": "ask"})
                asks.append({"kind": "event", "ref": str(ev)[:40],
                             "question": "这件事画面稿里找不到：「%s」。是已经用别的说法拍了，还是漏了？" % str(ev)[:40],
                             "options": ["已经拍了，继续出片", "漏了，我去改剧本"]})
    if not any(x["check"] == "情节" for x in items):
        items.append({"check": "情节", "status": STATUS_PASS, "detail": "关键事件都有镜头", "fix": ""})

    # ⑤ 衔接：相邻两段换了场景时，前一段 end_state 要有着落（只查有没有，不判好坏）
    prev_scene = None
    for g in segments or []:
        s, score = scene_for_text(sl, g.get("text"))
        if s and score > 0:
            if prev_scene and s.get("id") != prev_scene.get("id") and not str(prev_scene.get("end_state") or "").strip():
                items.append({"check": "衔接", "status": STATUS_UNKNOWN,
                              "detail": "从「%s」换到「%s」，前一场没写结束状态" % (prev_scene.get("location"), s.get("location")), "fix": ""})
            prev_scene = s
    if not any(x["check"] == "衔接" for x in items):
        items.append({"check": "衔接", "status": STATUS_PASS, "detail": "换场都有交代", "fix": ""})

    # ⑥ 时长：有台词的段放得下（粗判：每秒 4 字，留 2 秒动作）
    for g in segments or []:
        secs = float(g.get("seconds") or 0)
        if not secs:
            continue
        said = "".join(q for _, _, q in pictures_dialogue(g.get("text") or ""))
        need = len(_n(said)) / 4.0 + 2.0
        if said and need > secs + 0.5:
            items.append({"check": "时长", "status": STATUS_PROBLEM,
                          "detail": "第%s段 %.0f 秒放不下 %d 字台词（约需 %.0f 秒）" % (g.get("no"), secs, len(_n(said)), need),
                          "fix": ""})
    if not any(x["check"] == "时长" for x in items):
        items.append({"check": "时长", "status": STATUS_PASS, "detail": "台词都放得下", "fix": ""})

    # ⑦⑧⑨ 安排的三项：确认的结尾拍到没有 / 四段各有镜头没有 / 总时长放不放得下
    # 安排那三项比对前先把别称归一（清单叫「流浪猫」、画面稿写「灰狸花猫」，不归一算不上重合，白问一次）
    _au = slices if slices is not None else segments
    _au = [dict(u, text=canon_text(sl, u.get("text"))) for u in (_au or []) if isinstance(u, dict)]
    a_items, a_asks = arrangement_checks(arrangement, _au)
    items.extend(a_items)
    asks.extend(a_asks)

    # 总状态：有 ask 的 → 无法判断（要停）；有问题 → 有问题；否则通过
    if asks or any(x["status"] == STATUS_UNKNOWN and x["check"] in ("台词",) for x in items):
        status = STATUS_UNKNOWN
    elif any(x["status"] == STATUS_PROBLEM for x in items):
        status = STATUS_PROBLEM
    else:
        status = STATUS_PASS
    return {"status": status, "items": items, "asks": asks}


def _arrangement_people(arrangement):
    """本话安排的人物表名字集合（items.who.people[].name；也认顶层 people）。没有安排/没有表 → 空集。"""
    if not isinstance(arrangement, dict) or not arrangement:
        return set()
    ps = ((arrangement.get("items") or {}).get("who") or {}).get("people") if isinstance(arrangement.get("items"), dict) else None
    if not ps:
        ps = arrangement.get("people")
    out = set()
    for p in (ps or []) if isinstance(ps, list) else []:
        nm = str(p.get("name") or "").strip() if isinstance(p, dict) else str(p or "").strip()
        if nm:
            out.add(nm)
    return out


# ─────────────────────────── 安排的三项（结尾 / 情节四段 / 时长）───────────────────────────

BEAT_STAGES = ("开始", "经过", "变化", "结束")      # 默认四段；有安排时按安排取（P285）


def stages(arrangement=None):
    """这份安排的段名（段数按内容定）；没安排就是默认四段。"""
    try:
        from .arrangement import stages_of
        return tuple(stages_of(arrangement))
    except Exception:
        return BEAT_STAGES
LENGTH_OVER = 1.15          # 切出来的总秒数超过预计的这个倍数 → 放不下，问用户
LENGTH_UNDER = 0.7          # 不到预计的这个倍数 → 太短，提示可以再写细一点（不问）
LENGTH_OPTIONS = ("延长本话", "把后半部分留到下一话")


def beat_index_of(unit):
    """一个切片/段的 beat_index：0~3；没有、判不出、非法都算 -1。
    切片是另一个人在 authoring.slice_pictures 里加的；他那边没就绪时这里拿到 -1，不崩。"""
    try:
        i = int((unit or {}).get("beat_index", -1))
    except Exception:
        return -1
    return i if i >= 0 else -1


def cut_after_beat_of(arrangement):
    """安排里的 cut_after_beat：-1 不剪；0..3 只出到这个 beat。没有/非法算 -1。"""
    try:
        c = int((arrangement or {}).get("cut_after_beat", -1))
    except Exception:
        return -1
    return c if 0 <= c < len(stages(arrangement)) else -1


def _runs3(text, n=3):
    """去标点后，每个分句里的连续 n 字片段（不跨分句——「灯亮起来，两人」不该出「来两人」）。"""
    out = set()
    for seg in re.split(r"[^\w一-龥]+", str(text or "")):
        for i in range(len(seg) - n + 1):
            out.add(seg[i:i + n])
    return out


def ending_matches(end_text, last_text):
    """最后一段拍的是不是确认的结尾：二字词重合 ≥0.3，或抄到了结束句的连续片段。
    连续片段这条比契约的"连续三字"略紧：要么一个四字片段，要么两个不同的三字片段——
    单个三字（「一会儿」「看了一」）太常见，换了结尾也能碰上。"""
    if not _n(end_text) or not _n(last_text):
        return False
    if _overlap(end_text, last_text) >= 0.3:
        return True
    body = _n(last_text)
    if any(r in body for r in _runs3(end_text, 4)):
        return True
    return sum(1 for r in _runs3(end_text, 3) if r in body) >= 2


BEAT_CONTENT_MIN = 0.25      # 这一段镜头和安排那段描述的最低重合（安排是转述，门槛不能高）


def _s_text(u):
    return str((u or {}).get("text") or "")


def beat_covered(want, text):
    """安排里这件事有没有拍在这段镜头里：整句二字重合，或**任一小句**（≥4 字）重合 ≥ 门槛（P328b）。
    安排是转述（「林澈从背后环抱苏蔓，下巴抵肩窝」），镜头写「站在她身后，手臂环过腰际，下巴轻轻抵在她肩窝」，
    整句重合只有 0.22，小句「下巴抵肩窝」有 0.5。"""
    if _overlap(want, text) >= BEAT_CONTENT_MIN:
        return True
    names = []
    try:
        arr = _ARR_CTX.get("arr") or {}
        names = [str(p.get("name") or "") for p in ((((arr.get("items") or {}).get("who") or {}).get("people") or [])) if isinstance(p, dict)]
    except Exception:
        names = []
    names = [x for x in names if len(x) >= 2]
    t2 = str(text or "")
    for nm in names:
        t2 = t2.replace(nm, "　")
    for cl in re.split(r"[，,；;。]", str(want or "")):
        cl = cl.strip()
        for nm in names:
            cl = cl.replace(nm, "　")                             # P328f⑥：人名不算重合（人名 + 一个动词就到一半）
        if len(_n(cl)) >= 4 and _overlap(cl, t2) >= BEAT_CLAUSE_MIN:
            return True
    return False


BEAT_CLAUSE_MIN = 0.5        # 小句短，人名一占就够 0.25 了——小句路径要一半以上重合


def missing_idx(missing_names):
    """段名列表 → 下标集合。"""
    st = stages(_ARR_CTX.get("arr"))
    return {st.index(x) for x in (missing_names or []) if x in st}


_ARR_CTX = {}     # missing_idx 要知道当前安排的段名（P285）


def arrangement_checks(arrangement, units):
    """契约 F 的三项。units：整话切片（优先）或段列表，每个带 text / seconds / beat_index。
    返回 (items, asks)；没有安排返回空，三项一个都不出现。

    · 结尾：最后一段和 beats[结束] 对得上 → 通过；否则 有问题「确认的结尾没拍到」。
      剪到下一话（cut_after_beat 0..2）时结尾本来就在下一话，不算问题。
    · 情节：四段各至少一个切片；缺的一段一条 有问题「「变化」这一段没有镜头」。
      切片都没有 beat_index（切段那边还没接上）→ 无法判断「未检查」，不假装通过。
    · 时长：总秒数 > 预计×1.15 → asks 里一条 kind=length（延长本话 / 把后半部分留到下一话）；
      < 0.7× → 无法判断，提示可以再写细一点；其余通过。剪过之后只数剪进来的段。
    """
    items, asks = [], []
    if not isinstance(arrangement, dict) or not arrangement:
        return items, asks
    beats = [b for b in (arrangement.get("beats") or []) if isinstance(b, dict)]
    beat_text = {str(b.get("stage") or ""): str(b.get("text") or "") for b in beats}
    ST = stages(arrangement)
    _ARR_CTX["arr"] = arrangement
    cut = cut_after_beat_of(arrangement)
    last_stage = len(ST) - 1 if cut < 0 else cut
    units = [u for u in (units or []) if isinstance(u, dict)]
    has_beat = any(beat_index_of(u) >= 0 for u in units)
    # 剪过：只看 beat 不超过剪点的段；判不出 beat 的段（-1）保留——不知道它属于哪段，不敢丢
    scope = [u for u in units if cut < 0 or beat_index_of(u) <= cut]

    # 结尾
    end_text = beat_text.get("结束", "") or (str(beats[-1].get("text") or "") if beats else "")
    if not end_text.strip():
        items.append({"check": "结尾", "status": STATUS_UNKNOWN, "detail": "安排里没写「结束」这一段，无法核对结尾", "fix": ""})
    elif 0 <= cut < len(ST) - 1:
        items.append({"check": "结尾", "status": STATUS_PASS,
                      "detail": "后半部分留到下一话（剪在「%s」之后），确认的结尾在下一话拍" % ST[cut], "fix": ""})
    else:
        last = next((u for u in reversed(scope) if _n(u.get("text"))), None)
        if last is None:
            items.append({"check": "结尾", "status": STATUS_UNKNOWN, "detail": "还没有切出任何一段，无法核对结尾", "fix": ""})
        elif ending_matches(end_text, last.get("text")):
            items.append({"check": "结尾", "status": STATUS_PASS, "detail": "最后一段拍的是确认的结尾：%s" % end_text[:24], "fix": ""})
        else:
            items.append({"check": "结尾", "status": STATUS_PROBLEM,
                          "detail": "确认的结尾没拍到：安排定的是「%s」，最后一段是「%s」"
                                    % (end_text[:24], _n(last.get("text"))[:24]), "fix": ""})

    # 情节：四段各至少一个切片
    if not units:
        items.append({"check": "情节", "status": STATUS_UNKNOWN, "detail": "还没有切出任何一段，四段情节未检查", "fix": ""})
    elif not has_beat:
        items.append({"check": "情节", "status": STATUS_UNKNOWN, "detail": "切片还没有段落归属（beat），四段情节未检查", "fix": ""})
    else:
        have = {beat_index_of(u) for u in units}
        missing = []
        for i in range(0, last_stage + 1):
            if i in have:
                continue
            # P328①：段号归属是"一块只归一段"，一块里连着拍了两件事时后一件没有自己的段号（194：环抱/跨坐/亲吻
            #        都在淋浴那一块里）→ 先看这件事的内容有没有拍在别的段里，拍了就算有镜头
            want = beat_text.get(ST[i], "")
            if _n(want) and any(beat_covered(want, _s_text(u)) for u in units):
                items.append({"check": "情节", "status": STATUS_PASS, "detail": "「%s」拍在相邻段里" % ST[i], "fix": ""})
                continue
            missing.append(ST[i])
        for st in missing:
            # fix="beat"：出片前门会让模型照原文补镜头（P328①），不是问用户
            items.append({"check": "情节", "status": STATUS_PROBLEM, "detail": "「%s」这一段没有镜头" % st, "fix": "beat", "stage": st})
        # 【标签齐不等于拍全了】P279：用户把前三段内容全换成「老板娘站着看木箱」、只留标签，
        # 原来照样判"四段情节都有镜头"。这里再核内容：这一段的镜头文字要和安排里那段描述有实质重合。
        off = []
        for i in range(0, last_stage + 1):
            if i in missing_idx(missing):
                continue
            want = beat_text.get(ST[i], "")
            if not _n(want):
                continue
            shot = "".join(_s_text(u) for u in units if beat_index_of(u) == i)
            if not _n(shot):
                continue
            if _overlap(want, shot) < BEAT_CONTENT_MIN:
                off.append((ST[i], want, shot))
        for st, want, shot in off:
            items.append({"check": "情节", "status": STATUS_UNKNOWN,
                          "detail": "「%s」这一段的镜头和安排对不上：安排写的是「%s」，镜头拍的是「%s」"
                                    % (st, want[:20], _n(shot)[:20]), "fix": "ask"})
            asks.append({"kind": "beat", "ref": st,
                         "question": "「%s」这一段安排写的是「%s」，可镜头拍的是「%s」。按现在的镜头出片吗？"
                                     % (st, want[:24], _n(shot)[:24]),
                         "options": ["就按现在的镜头出", "不对，我去改剧本"]})
        if not missing and not off:
            items.append({"check": "情节", "status": STATUS_PASS,
                          "detail": ("%d 段情节都有镜头" % len(ST) if cut < 0 else "到「%s」为止的各段都有镜头" % ST[cut]), "fix": ""})

    # 时长：总秒数对预计
    try:
        dur = int(float(arrangement.get("duration_sec") or 0))
    except Exception:
        dur = 0
    total = 0.0
    for u in scope:
        try:
            total += float(u.get("seconds") or 0)
        except Exception:
            pass
    total_i = int(round(total))
    if dur <= 0:
        pass                    # 规划阶段不设时长，按剧本切出的长度出片——不设预算就没有可核的，不单出一条（P328③，原 P323 出一条"通过"和上面那条重复）
    elif not scope:
        items.append({"check": "时长", "status": STATUS_UNKNOWN, "detail": "还没有切出任何一段，总时长未检查", "fix": ""})
    elif total > dur * LENGTH_OVER and cut >= 0:
        # 已经剪过还放不下（第一段本身就超了）：再问一次「留到下一话」只会得到同一个剪点，
        # 问下去是死循环。如实报问题，不拦——要么延长本话，要么把剧本写短
        items.append({"check": "时长", "status": STATUS_PROBLEM,
                      "detail": "剪到「%s」之后仍有 %d 秒，预计 %d 秒还是放不下——只能延长本话或把剧本写短"
                                % (ST[cut], total_i, dur), "fix": ""})
    elif total > dur * LENGTH_OVER:
        q = "这一话切出来 %d 秒，预计 %d 秒放不下" % (total_i, dur)
        items.append({"check": "时长", "status": STATUS_UNKNOWN, "detail": q + "——延长本话，还是把后半部分留到下一话？", "fix": "ask"})
        asks.append({"kind": "length", "ref": "", "question": q, "options": list(LENGTH_OPTIONS),
                     "total_sec": total_i, "duration_sec": dur})
    elif total < dur * LENGTH_UNDER and cut < 0:
        # 剪过之后比预计短是用户自己选的，不再提示"写细一点"
        items.append({"check": "时长", "status": STATUS_UNKNOWN,
                      "detail": "这一话切出来 %d 秒，比预计 %d 秒短不少——剧本可以再写细一点" % (total_i, dur), "fix": ""})
    else:
        items.append({"check": "时长", "status": STATUS_PASS,
                      "detail": "切出来 %d 秒，预计 %d 秒，放得下%s" % (total_i, dur, "" if cut < 0 else "（后半部分留到下一话）"), "fix": ""})
    return items, asks


def beat_seconds(units, arrangement=None):
    """每个 beat 实际切出来多少秒：{段名: 秒}（含 "未归属" 一栏，切片没 beat 时都在这里）。
    页面拿它和 budget 对照；apply_length_choice 的 per_beat_sec 也从这里来。"""
    ST = stages(arrangement)
    out = {st: 0.0 for st in ST}
    out["未归属"] = 0.0
    for u in units or []:
        if not isinstance(u, dict):
            continue
        try:
            s = float(u.get("seconds") or 0)
        except Exception:
            s = 0.0
        i = beat_index_of(u)
        out[ST[i] if 0 <= i < len(ST) else "未归属"] = out.get(ST[i] if 0 <= i < len(ST) else "未归属", 0.0) + s
    return {k: int(round(v)) for k, v in out.items()}


_STOP2 = ("并且", "然后", "随后", "开始", "继续", "自己", "他们", "她们", "一个", "一下", "一声",
          "什么", "这个", "那个", "没有", "已经", "正在", "可以", "不过", "以及", "对方")


def event_tokens(sl, ev):
    """事件描述里的实体词：cast 名字（含别称→本名）+ 出现的 2 字名词（去掉虚词）。"""
    t = canon_text(sl, ev)
    out = []
    for c in (sl or {}).get("cast") or []:
        nm = str(c.get("name") or "")
        if nm and nm in t and nm not in out:
            out.append(nm)
    rest = t
    for nm in out:
        rest = rest.replace(nm, " ")
    for m in re.finditer(r"[\u4e00-\u9fa5]{2}", rest):
        w = m.group(0)
        if w in _STOP2 or w in out:
            continue
        if re.search(r"[了的着过是把被在向到从与和并将并让]", w):
            continue
        out.append(w)
    return out[:8]


def plan_fidelity(row, prose):
    """这一话对应的结构表行有没有在正文里落地：地点、在场的人、事件要素。
    返回 [(check, status, detail)]。没有结构表行 → 空（老项目）。"""
    out = []
    if not isinstance(row, dict) or not str(prose or "").strip():
        return out
    pr = str(prose or "")
    for place in [x.strip() for x in re.split(r"[、，,／/；;]", str(row.get("place") or "")) if x.strip()]:
        if not same_place(place, pr) and not (_n(place)[:4] and _n(place)[:4] in _n(pr)):
            out.append(("地点", STATUS_PROBLEM, "结构表定的地点「%s」正文里没有" % place))
    for who in [re.sub(r"（[^）]*）", "", x).strip() for x in re.split(r"[、，,／/和与及]", str(row.get("cast") or "")) if x.strip()]:
        if who and who not in pr:
            out.append(("出场人物", STATUS_PROBLEM, "结构表定的在场人物「%s」正文里没出现" % who))
    cast_names = [re.sub(r"（[^）]*）", "", x).strip() for x in re.split(r"[、，,／/和与及]", str(row.get("cast") or "")) if x.strip()]
    for ev in row.get("events") or []:
        ev = str(ev)
        named = [n for n in cast_names if n and n in ev]
        if any(n not in pr for n in named):
            out.append(("情节", STATUS_PROBLEM, "结构表事件「%s」点名的人正文里没有" % ev[:20]))
        elif _overlap(ev, pr) >= 0.5:
            pass                                             # 字面能对上，算落地
        else:
            # 结构表的事件是转述（"化解危机"），正文是描写，字面对不上不等于没写——老实报判不了
            out.append(("情节", STATUS_UNKNOWN, "结构表事件「%s」正文里找不到字面对应，需要人看" % ev[:20]))
    if not out:
        out.append(("结构表", STATUS_PASS, "这一话按结构表那一行写了"))
    return out


_SENT_SPLIT = re.compile(r"(?<=[。！？!?])")


def _prose_sentences(prose):
    out = []
    for para in str(prose or "").split("\n"):
        for sent in _SENT_SPLIT.split(para):
            sent = sent.strip()
            if len(_n(sent)) >= 4:
                out.append(sent)
    return out


SAME_ACTION = 0.4        # 整句这么像就算"这一块已经在演这个动作"（换个说法写也算拍到了）


def depicts(sl, ev, block):
    """这一块画面是不是在演这件事：要素词过半 或 整句二字组合重合 ≥0.4。

    P277：只按要素词判会把模型的改写判成"没拍到"——「手指分开交错的枯枝」对
    「放下抹布，拨开低垂的枝叶」一个要素词都不中，于是又插一遍原句，画面上同一个动作演两遍。
    """
    txt = canon_text(sl, block)
    toks = event_tokens(sl, ev)
    if toks:
        hit = sum(1 for t in toks if t in txt)
        if hit >= max(1, (len(toks) + 1) // 2):
            return True
    return _overlap(canon_text(sl, ev), txt) >= SAME_ACTION


def event_covered(sl, ev, text, prose=""):
    """这件事在这段文字里拍到了没：和 check() 的「情节」项同一口径（要素词≥一半出现）。"""
    toks = event_tokens(sl, ev)
    if prose:
        _pr = canon_text(sl, prose)
        toks = [t for t in toks if t in _pr]
    if not toks:
        return True
    hit = sum(1 for t in toks if t in canon_text(sl, text))
    return hit >= max(1, (len(toks) + 1) // 2)


def missing_events(sl, pictures, prose=""):
    """清单里的关键事件，哪些在画面稿里**按字面**找不到。返回 [{"scene","ev","near"}]。

    P278：原来这里会把原文原句插成一幅画面（"补回原文"）。实测那是错的——模型换了说法写过了，
    再插一遍就是同一个动作演两遍，还挤掉后面情节的时间（用户 2026-09-11 在 164 的剧本里查出三处）。
    而"换个说法写的算不算拍到"字面判不出（「拨开低垂的枝叶」对「手指分开交错的枯枝」零重合），
    所以只查不改：报给检查器 → 问用户。near 是最像的那一块，给人对照用。
    """
    out = []
    pics = str(pictures or "")
    if not pics.strip() or not sl:
        return out
    blocks = [b for b in re.split(r"\n\s*\n", pics) if b.strip() and not b.strip().startswith("──")]
    for sc in (sl.get("scenes") or []):
        for ev in (sc.get("must_happen") or []):
            if event_covered(sl, ev, pics, prose):
                continue
            near, best = "", 0.0
            for b in blocks:
                v = _overlap(canon_text(sl, ev), canon_text(sl, b))
                if v > best:
                    near, best = b, v
            out.append({"scene": sc.get("id"), "ev": str(ev), "near": near.strip()[:40]})
    return out


def summarize(report):
    """给日志/页面看的一行一条。"""
    out = []
    for x in (report or {}).get("items") or []:
        out.append("[%s] %s：%s" % (x.get("status"), x.get("check"), x.get("detail")))
    return out
