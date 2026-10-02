# -*- coding: utf-8 -*-
"""director.py — Qwen 当导演：读第一话剧本，按「戏剧节拍」分段 + 导成电影感视频分镜。

替代旧的「代码扒镜头表 + Qwen 当翻译员」那套（那套把 Qwen 捆成翻译员、镜头平铺、
节奏赶、丢反应）。这里 Qwen 直接读自己写的剧本，自己按戏剧节拍分段、自己定每段时长
（≤15秒）、自己导运镜和表演。代码只做三件事：递事实（剧本/人物锚点/画风尺度）、
调 Qwen、把结果解析成段。指令词见 presets/instructions/导演_分镜_指令词.txt。
"""
import io
import json
import os
import re
import time
from pathlib import Path

INS = Path(__file__).resolve().parent.parent / "presets" / "instructions" / "导演_分镜_指令词.txt"



def _voice_block(sid):
    """【人物编号与音色】——每段都要带，防换声和串角色。

    编号顺序即参考图 Picture 顺序，**不能乱**，乱了就换脸换声。
    音色调 authoring.h3_voice，和 H3 严格模板版共用一个函数
    （2026-08-31：两版实测对比时，音色必须完全一致才只差在结构上）。
    """
    from . import authoring, asset_core
    try:
        chars = asset_core.list_assets(sid, "characters") or []
    except Exception:
        return ""
    st = {}
    try:
        from . import story_core
        st = story_core.get_story(sid) or {}
    except Exception:
        pass
    rows = []
    for i, c in enumerate(chars, 1):
        n = str((c or {}).get("name") or "").strip()
        if not n:
            continue
        rows.append("%s ＝ Subject %d ＝ S%d" % (n, i, i))
        rows.append("<Subject%d>，S%d 永远是说话人。S%d 始终使用一种固定的声音：%s"
                    % (i, i, i, authoring.h3_voice(c, st)))
    if not rows:
        return ""
    return ("\n【人物编号与音色（每段照抄，一个字不许改）】\n"
            + "\n".join(rows) + "\n")


def _anchors(sid):
    from . import asset_core

    def brief(c):
        # 声线也要给——角色设计师给每个人写了音色/语速/说话习惯，
        # 以前锚点里没带，H3 只能自己配音，几个人的声音分不出来
        # （2026-08-27 用户实测反馈）。
        line = "%s（%s，%s，%s）" % (
            c.get("name"), str(c.get("char_type") or "").split("/")[0],
            str(c.get("appearance_details") or "")[:26], str(c.get("clothing") or "")[:24])
        v = str(c.get("voice") or "").strip()
        if v:
            line += "\n    声线：" + v[:60]
        return line
    return "\n".join(brief(c) for c in asset_core.list_assets(sid, "characters"))


def _took_to(text):
    """从一段分镜里抠出「本段拍到」那一句。

    逐段生成时，前几段只需要这一句来交代进度。以前是把前几段**全文**喂进去，
    结果 Qwen 把它们当成格式范例照抄——第一段写薄了（STORY_052 seg1 只有 345 字、
    1 个镜头），后面几段就照着抄，还把段级的「台词/环境音/配乐」抄成每个镜头挂一份。
    格式的唯一权威是系统指令词，不是上一段。
    """
    m = re.search(r"本段拍到[：:]\s*([^\n｜|]+)", str(text or ""))
    if m:
        return m.group(1).strip()
    return str(text or "").strip().replace("\n", " ")[:40]


def _strip_angle(t):
    """剥掉台词行漏出来的尖括号（<大师姐>说 → 大师姐说）。
    指令词说了不要写，模型偶尔还照模板抄出来——对 H3 是乱码 token，
    2026-08-28 实测第一段说话不清、第二段（没尖括号）正常。格式交给代码兜底。"""
    return re.sub(r"[<＜]([^<>＜＞\n]{1,14})[>＞]", r"\1", str(t or ""))


def parse_segments(raw):
    """把「===== 第N段 =====」分隔的文本解析成段列表；掐掉截断的不完整尾段。"""
    parts = re.split(r"=+\s*第\s*[\d一二三四五六七八九十]+\s*段\s*=+", raw or "")
    segs = []
    for p in parts:
        p = p.strip()
        if not p or "镜头" not in p or "配乐" not in p:   # 没有「配乐」收尾的是被截断的残段
            continue
        m = re.search(r"本段时长[：:]\s*([\d.]+)", p)
        segs.append({"no": len(segs) + 1,
                     "seconds": float(m.group(1)) if m else None,
                     "prompt": _strip_angle(p)})
    return segs


def direct_episode(sid, ep=1, call_model=None):
    """读第 ep 话剧本，让 Qwen 导成一段段视频分镜。返回 (段列表, 原文)。"""
    from . import saga_core, story_core
    st = story_core.get_story(sid) or {}
    s = st.get("settings") or {}
    e = saga_core.episode(saga_core.get_saga(sid), ep) or {}
    body = e.get("body") or ""
    if not body.strip():
        raise RuntimeError("第%d话还没有剧本正文" % ep)
    ins = INS.read_text(encoding="utf-8")
    from . import authoring
    scale = authoring._scale(s, "shot")   # 分镜吃 shot 层 + 用户自定义尺度
    shooting_notes = str(e.get("shooting_notes") or "").strip()
    user = ("【项目设定】世界：%s。画风：%s。内容尺度：%s。\n%s【人物锚点（照这个画谁是谁）】\n%s\n%s\n"
            "【第%d话完整剧本】\n%s\n\n把整话按戏剧节拍分成一段段，依次输出每一段。" % (
                s.get("world_type"), s.get("style"), scale,
                (("【用户想怎么拍】%s。只影响机位、节奏和表演感觉，不得改变剧情、人物和台词。\n" % shooting_notes)
                 if shooting_notes else ""),
                _anchors(sid), _voice_block(sid), ep, body))
    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=6000, reasoning=False, **kw)
    raw = (call_model(ins, user, temperature=0.7) or "").strip()
    return parse_segments(raw), raw


END_MARK = "剧本已分完"


def _confirmed_segments(sid, ep):
    """timeline 里已存的导演段（就是已确认的前几段）。返回 (tl, segments列表)。"""
    from . import saga_core
    tl = saga_core.ep_timeline(sid, ep) or {"scenes": []}
    scenes = tl.get("scenes") or []
    segs = (scenes[0].get("segments") or []) if scenes else []
    return tl, segs


# ---------- 台词进度锚：防导演跳戏/编戏 ----------
# 2026-08-28 实测：逐段导到第3段时，导演没接剧本里"进城→伙计引路→进房"，
# 而是自创了一场山道亲密戏（台词「阿砚，」「嗯？」剧本里都没有），第4段再拍真内容，
# 节拍重复。指令词禁了没用——只能用**台词**当进度锚：台词是逐字的、有序的，
# 每段导完按顺序核对，下一段的提示里明确指出"该拍到哪句了"。

# 剧本里的结构字段名，长得像台词行但不是人说的话。
_FIELD_NAMES = {
    "在场", "空间", "时间", "地点", "道具", "光线", "服装", "音效", "环境音",
    "布局", "站位", "场景", "镜头", "配乐", "台词", "本段拍到", "本段时长",
    "本段场景", "本段布局", "本段背景锚", "开场状态", "收尾状态", "人物站位",
    "人物衣着", "站位表", "备注", "说明", "注",
}


def _dialogue_list(screenplay, names=None):
    """剧本里按顺序出现的 (说话人, 台词)。

    剧本有**两种**台词写法，都得认：
    ① 独立行：`司仪：下一场，苏清婉，对阵……`
    ② 写在镜头行里：`（中景｜5秒）林远皱眉，提高音量：“早啊，先生。”`
    原来只认①。碰上②时台词表整个是空的，而"编造台词"的判定是
    「引号内容不在本段台词表里」——表空了，导演写什么都算编造，五轮全灭。
    2026-08-30 实测：一轮里三个题材同时挂在这上面，剧本全用的②。
    说话人取引号前**最近出现的人名**（给了人名表就按表认，认不出就留空，
    只影响反馈措辞，不影响"这句必须拍到"的判定）。
    """
    out = []
    nm = [n for n in (names or []) if n]
    for line in str(screenplay or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # 括号标注不止「（心声）」一种：实测还有「苏青（对镜头）：…」（自拍题材
        # 满篇都是），只认心声就等于整段台词没认出来，导演照剧本拍全被判编造
        # （2026-08-30 实测）。放宽成任意短标注。
        m = re.match(r"^([^\s（(：:【#｜=─]{1,12})(?:（[^）\n]{0,8}）)?[：:](.+)$", line)
        if m:
            # 场景头的字段名不是人名。「在场：艾利安」「空间：狭小阁楼…」长得和
            # 台词行一模一样，认成台词后会被当作"必须拍到"，机械内联时就往视频
            # 提示词里塞一句"空间自然开口说：…"。目前字段行进的是 scene_head、
            # 不在 shots 里，成片没被污染（已核查 15 个项目 0 处），
            # 但解析器本身不该认错（2026-08-30）。
            if m.group(1) in _FIELD_NAMES:
                continue
            out.append((m.group(1), m.group(2).strip()))
            continue
        # 第三种写法：【雷恩】（心声）这是第三场。——方括号包人名、没有冒号。
        # 不认它，台词表就是空的，而"编造台词"判的是"引号内容不在台词表里"，
        # 表空了导演写什么都算编造，五轮全灭（2026-08-30 实测）。
        m2 = re.match(r"^【([^】\n]{1,12})】\s*(?:（[^）\n]{0,6}）)?\s*(.+)$", line)
        # 排除表和冒号分支共用 _FIELD_NAMES：这里原本自带一张更短的表，
        # 缺了「在场」「空间」，于是场景头字段从这个分支漏进台词表（2026-08-30）。
        if m2 and m2.group(1).strip() not in _FIELD_NAMES:
            out.append((m2.group(1).strip(), m2.group(2).strip()))
            continue
        if line.startswith("【"):          # 布局行里的引号不是台词
            continue
        for q in re.finditer(r"[「“]([^」”\n]{2,})[」”]", line):
            before = line[:q.start()]
            who = ""
            best = -1
            for n in nm:
                p = before.rfind(n)
                if p > best:
                    best, who = p, n
            # 判据是**前面有没有说话动词**，不是有没有认出人名。
            # 只认人名会把群众角色的台词全丢掉（「围观弟子议论：“…”」
            # 「另一弟子笑道：“…”」），台词表又变空，导演照剧本拍反被判编造
            # （2026-08-30 实测仙侠段）。而信上的字是"字迹潦草"、报纸是"标题写着"，
            # 都没有说话动词，照样排除得掉。
            sv = _SPEECH_VERB.search(before)
            # **必须有说话动词**，光有人名不算。原来写成"有人名或有说话动词"，
            # 于是「林默站在名为"旧时代咖啡"的店铺门口」里的**店名**被当成台词，
            # 成片里林默真的开口念了一句"旧时代咖啡"（2026-08-30 用户实测）。
            # 引号在中文里也用来标专名、书名、强调，不是只用来标话。
            if not sv:
                continue
            if not who:
                # 无名角色：取说话动词前面那几个字当称呼
                who = re.sub(r"^.*[）)，,。：:]", "", before[:sv.start()]).strip()[-6:]
            out.append((who, q.group(1).strip()))
    return out


# 说话动词。带引号但前面没有这些词的，是信上的字／报纸标题／牌子，不是台词。
_SPEECH_VERB = re.compile(
    r"(说道|笑道|喝道|问道|答道|叹道|冷笑道|低声道|沉声道|开口|议论|"
    r"低语|呢喃|嘀咕|嘟囔|吼|喊|叫道|念|说|问|答|回应|补充|接话)")


def _norm_quote(t):
    """比对台词时的归一化：只留文字，标点一律剥掉。

    原来是列举中文标点，漏了**半角**的那一套——导演把台词写成
    「...了，明年的宗门贡献点……」（三个半角句点），半角 . 留在串里，
    子串匹配对不上，照剧本拍的台词被判成编造、整段打死（2026-08-30 实测）。
    列举永远会漏，改成反过来：非文字一律去掉。
    """
    return re.sub(r"[^\w一-龥]", "", str(t or ""))


def _staged_cues(seg_prompt):
    """这一段用来算进度的台词引句：全段所有「」，只剔「本段拍到」那一行。

    编排写法太多样（说，/说：/说道/合并引用），按包装匹配是打不完的正则地鼠；
    唯一实测会谎报的是「拍到」摘要行（第7句只进摘要没进镜头），剔掉它就够了。
    另收「台词：」字段里不带「」的裸写行（`凯尔（心声）：X`）——实测漏计一句
    导致下一段把同一拍整个重拍了一遍（2026-08-28 seg1/seg2 开场重复）。
    """
    t = re.sub(r"^本段时长[^\n]*$", "", str(seg_prompt or ""), flags=re.M)
    out = re.findall(r"「([^」]+)」", t)
    i = t.find("台词：")
    if i >= 0:
        for line in t[i:].split("\n"):
            line = line.strip()
            m = re.match(r"^(?:台词[：:]\s*)?[^\s（(：:「]{1,12}(?:（心声）)?[：:]\s*([^「\n]{2,60})$", line)
            if m and not any(m.group(1) in q or q in m.group(1) for q in out):
                out.append(m.group(1).strip().rstrip("。"))
            if line.startswith(("环境音", "配乐", "收尾状态")):
                break
    return out


def _consume(dialogues, k, q):
    """用一条引句从第 k 句起往前消化台词，返回新 k。
    拆句：引句是第 k 句的一部分 → 消化 1 句。
    合并：引句一口气引了连续几句 → 逐句消化到断为止。
    对不上第 k 句的引句（引的是前文/背景）→ 不动。"""
    qn = _norm_quote(q)
    if k < len(dialogues):
        tn = _norm_quote(dialogues[k][1])
        if (len(qn) >= 3 and qn in tn) or (len(qn) < 3 and q.strip() in dialogues[k][1]):
            return k + 1
    n = k
    while n < len(dialogues):
        tn = _norm_quote(dialogues[n][1])
        if tn and tn in qn:
            n += 1
        else:
            break
    return n


def _covered_count(dialogues, seg_prompts):
    """已确认的段按顺序覆盖到了剧本第几句台词。"""
    k = 0
    for p in seg_prompts:
        for q in _staged_cues(p):
            k = _consume(dialogues, k, q)
    return k


_SLICE_SHOT = re.compile(r"^[（(]\s*[^）)｜|]*[｜|]\s*([\d.]+)\s*秒[^）)]*[）)]", re.M)


def slice_screenplay(body):
    """把剧本按「每镜秒数累加 ≤15s」切成段清单。**切段是确定性计算，代码做，
    不留给 Qwen**——让它自己定范围时，贪多→装不下→丢长台词，三次重试都治不了
    （2026-08-28 实测）。返回 [{"scene_head": 场景头四行, "shots": 这一拍组的剧本原文,
    "seconds": 秒数和}]。台词/心声行跟着它上面的镜头走。
    """
    lines = str(body or "").splitlines()
    slices, cur_head, cur_shots, cur_secs = [], "", [], 0.0

    def _flush():
        nonlocal cur_shots, cur_secs
        if cur_shots:
            slices.append({"scene_head": cur_head,
                           "shots": "\n".join(cur_shots).strip(),
                           "seconds": round(cur_secs, 1)})
        cur_shots, cur_secs = [], 0.0

    head_buf = []
    for line in lines:
        ls = line.strip()
        if ls.startswith("## 场景"):
            _flush()
            head_buf = [ls]
            cur_head = ls
            continue
        if ls.startswith("【") and head_buf:
            head_buf.append(ls)
            cur_head = "\n".join(head_buf)
            continue
        m = _SLICE_SHOT.match(ls)
        if m:
            head_buf = []
            secs = float(m.group(1))
            if cur_shots and cur_secs + secs > 15:
                _flush()
            cur_shots.append(ls)
            cur_secs += min(secs, 15)
            continue
        if ls and cur_shots:
            cur_shots.append(ls)   # 台词/心声行跟着上面的镜头
    _flush()
    return slices


_TIERS = (("大特写", 1), ("特写", 1), ("中近景", 2), ("近景", 2),
          ("中景", 3), ("大全景", 4), ("中远景", 4), ("远景", 4), ("全景", 4))

# ── 镜头配方：这一段是什么戏，就用什么镜头语法（2026-08-29 用户定）───────
# 起因：实测两个项目的全景/远景占比都是 **0%**，近景+特写占 67~70%，
# 观众永远不知道人在什么地方、彼此隔多远——"不像电影"就是缺建立镜头。
# 按段自动判戏种，配方当数据行注进提示词，校验按配方查景别分布。
_PACK_ACTION = (r"打斗|交手|交战|挥剑|拔剑|格挡|追击|追逐|奔跑|逃|扑向|"
                r"砍|刺|撞|踢|摔|爆炸|坠落|跳下|跃起|冲锋|厮杀")
_PACK_MOVE = (r"走|前行|赶路|穿过|穿越|翻过|越过|跋涉|行进|骑|驶|飞行|"
              r"上路|启程|沿着|踏上|远处|地平线|旷野|荒原|山脊")
_PACKS = {
    "建立": ("远景或全景开场，先交代空间和人物在空间里的位置和距离；"
             "整段人脸最多占一镜",
             "允许一次缓推或缓拉展示环境规模", "本段自身就是建立镜头，不需要过渡特写"),
    "旅行": ("全景至少一半——人在环境里就该是小小的两点；中景约三成；特写不超过两成",
             "允许一次缓慢横移或拉远", "空镜过渡：风景、脚步、被风吹动的草木"),
    "对话": ("中景四成、近景四成、特写两成；整段必须至少有一个中景以上的镜头"
             "交代谁和谁在一起",
             "零运镜，全部固定，景别变化一律用切镜", "手、道具、或听的人的反应"),
    "动作": ("全景三成、中景四成、近景三成；每镜短促",
             "允许一次跟随或环绕，写明动机", "动作细节特写"),
    "情绪": ("特写为主，但整段必须有一个环境全景托底，别整段贴着脸拍",
             "零运镜", "环境空镜"),
}


# 运镜词。**校验和修复共用这一份**——原来两处各存一份，改一处漏一处。
# 「摇」要排掉日常词：「光晕在他侧脸投**下摇**曳的阴影」里的"下摇"不是运镜，
# 却被判成超限，而这种命中根本改不掉（去掉"下摇"就得删掉"摇曳"），
# 于是修复一轮轮空转，最后整段被打死，真正超标的只有一个"拉近"（2026-08-30 实测）。
# 只可能是镜头在动的词。
# 「缓推」要挡住「缓**缓推**回笔尾」（手指推笔帽）、「急推」要挡住「紧**急推**开」——
# 这种跨词命中根本改不掉（去掉"缓推"就得删掉整个动作），修复空转到整段打死
# （2026-08-30 实测）。「推至」挪到两可组：「把杯子推至桌边」是人在推。
_MOVE_STRICT = r"(?<!缓)缓推|(?<!紧)急推|摇镜|跟拍|镜头[^\n。]{0,6}拉开"
# 人和镜头共用的词：「他的脚步**上移**」是人在爬楼梯，「他**拉近**距离」是人在走近。
# 这些只有前面出现"镜头/机位/画面/视角"时才算运镜——否则改写把"镜头跟随"删掉了、
# 剩下的"脚步上移"照样命中，判定"仍含运镜"拒绝采纳，整段跟着打死（2026-08-30 实测）。
_MOVE_AMBIG = (r"推近|推进|推至|推入|拉远|拉近|拉入|拉出|拉开距离|"
               r"横移|平移|环绕|[上下左右横]摇(?![曳晃摆头篮滚])|[上下]移")
_MOVE_RE = "(?:%s)|(?:%s)" % (_MOVE_STRICT, _MOVE_AMBIG)
_CAM_WORD = r"(镜头|机位|画面|视角)"


def _move_hits(text):
    """真正算运镜的命中。粗筛用 _MOVE_RE，两可词再看前文有没有镜头主语。"""
    t = str(text or "")
    out = []
    for m in re.finditer(_MOVE_RE, t):
        if re.fullmatch(_MOVE_STRICT, m.group(0)):
            out.append(m)
            continue
        if re.search(_CAM_WORD, t[max(0, m.start() - 12):m.start()]):
            out.append(m)
    return out


_HEAD_RE = r"^镜头\d+（[^）\n]*）"

# 拒绝出段时把现场落盘。**只写文件，不改任何判定**——不落盘就只能靠错误信息
# 一句话反推，实测连猜三轮都没找对根因（2026-08-30）。
# 每段分镜的重试次数。3 次时各条 T0 校验加起来，整话至少一段被打死的概率很高，
# 而一段被打死＝这一段没有片子。一次尝试 20~40 秒，多两次很划算（2026-08-30）。
_TRIES = 5

_DBG_PATH = os.environ.get("V41_DIRECTOR_DEBUG") or ""


def _dbg(tag, sid, ep, no, prompt, extra=None):
    if not _DBG_PATH:
        return
    try:
        with io.open(_DBG_PATH, "a", encoding="utf-8") as f:
            f.write("\n%s\n[%s] %s 第%s话 第%s段  %s\n%s\n%s\n"
                    % ("=" * 70, time.strftime("%H:%M:%S"), tag, ep, no,
                       json.dumps(extra or {}, ensure_ascii=False), "-" * 70, prompt))
    except Exception:
        pass


# 元数据行：背景锚/场景/布局/站位表这些是**照抄的设定**，不是导演在选镜头。
# 「八角笼四周环绕生锈铁丝网围栏」是在描述场景被围着，不是运镜；它躲在背景锚里，
# 导演连改都不许改，却被判运镜超限，修复只能空转到整段打死（2026-08-30 实测）。
_META_LINE = re.compile(r"^(本段背景锚|本段场景|本段布局|本段拍到|本段时长|"
                        r"站位表|环境音|配乐|人物站位)[：:]", re.M)


def _cam_key(s):
    """比对机位句时的归一化：分隔符统一、空白去掉。

    导演写「正面背景是教室内部**，**画面左侧是西侧墙面」用逗号，合法句用分号，
    按分号切出来的字段全对不上——左右两个锚点其实一模一样，
    却被判成"没照抄机位句"整段打死（2026-08-30 实测）。
    """
    # 顿号「、」不算分隔符——它是字段**内部**的并列（"正面背景是讲台、黑板"），
    # 一并归一化会把首字段切成两段，左右锚点整体错位、照样匹配不上。
    return re.sub(r"[\s，,；;]+", "；", str(s or "")).strip("；")


def _meta_spans(text):
    """元数据行在全文里的字符区间。"""
    out = []
    for m in _META_LINE.finditer(text):
        e = text.find("\n", m.end())
        out.append((m.start(), len(text) if e < 0 else e))
    return out


def _plain_hits(text, figw):
    """本段里**不属于身材展示、也不在元数据行里**的运镜命中。
    判定口径和 T0 校验保持一致（身材词前后 50 字）。"""
    meta = _meta_spans(text)
    return [m for m in _move_hits(text)
            if not re.search(figw, text[max(0, m.start() - 50):m.end() + 50])
            and not any(a <= m.start() < b for a, b in meta)]


def _shot_blocks(text):
    """按镜头切块，返回 [(头文本, 块起, 块止)]。"""
    hs = [m for m in re.finditer(r"^镜头\d+（([^）\n]*)）", str(text or ""), re.M)]
    out = []
    for i, m in enumerate(hs):
        end = hs[i + 1].start() if i + 1 < len(hs) else len(text)
        out.append((m.group(1), m.start(), end))
    return out


def _fix_head_body_move(prompt, figw):
    """镜头头写「固定」、正文却写推近／拉入——同一个镜头里自己打自己。

    2026-08-30 逐字读发现：`镜头2（2–15秒，全景，固定）` 的正文里同时有
    「镜头从远处缓缓拉入」和「镜头缓慢推近至中景」。这句矛盾直接发给 H3，
    它要么不动、要么乱动，两头都不对。
    **镜头头是权威字段**（景别和运镜都写在那儿），所以删正文里的运镜说法，
    不去改头。删不干净返回 None，交给校验照常判。
    """
    text = str(prompt or "")
    meta = _meta_spans(text)
    # 「开场状态」不在镜头块里，但它会被装配时并进第一个宽景块，一样进成片
    # （实测「开场状态：镜头从远处缓缓拉入」按块扫描漏掉了）。所有镜头头都写
    # 固定时，它也不许有运镜。
    blocks = _shot_blocks(text)
    if blocks and all("固定" in hd for hd, _a, _b in blocks):
        m0 = re.search(r"^开场状态[：:][^\n]*$", text, re.M)
        if m0:
            blocks = [("固定", m0.start(), m0.end())] + blocks
    for hd, a, b in blocks:
        if "固定" not in hd:
            continue                      # 头本来就允许运镜，不管
        hits = [m for m in _move_hits(text[a:b])
                if not re.search(figw, text[a:b][max(0, m.start() - 50):m.end() + 50])
                and not any(x <= a + m.start() < y for x, y in meta)]
        if not hits:
            continue
        blk = text[a:b]
        for m in reversed(hits):
            # 整句就是一句运镜话术时删掉整句；否则只删运镜从句
            s0 = max(blk.rfind(c, 0, m.start()) for c in "。！？\n") + 1
            e0 = min([i for i in (blk.find(c, m.end()) for c in "。！？\n") if i >= 0]
                     or [len(blk)]) + 1
            sent = blk[s0:e0]
            cut = re.sub(r"镜头[^，。；\n]{0,24}?(?:%s)[^，。；\n]{0,14}[，、]?"
                         % _MOVE_AMBIG, "", sent)
            cut = re.sub(r"(?:%s)[^，。；\n]{0,10}[，、]?" % _MOVE_STRICT, "", cut)
            core = re.sub(r"[\s。，、；]", "", cut)
            blk = blk[:s0] + (cut if len(core) >= 6 else "") + blk[e0:]
        # 删整句会留下残标点：句子被整段拿掉后，冒号或上一句的句号后面
        # 直接跟着下一句的开头，装配出来就是「…（街道中央）。 。前方的面包车…」
        # （2026-08-30 实测，是我这次删除引入的）。统一收干净。
        blk = re.sub(r"[，、]{2,}", "，", blk)
        blk = re.sub(r"。[\s]*。+", "。", blk)
        blk = re.sub(r"([：:])\s*。+\s*", r"\1", blk)
        blk = re.sub(r"\n\s*。+\s*", "\n", blk)
        text = text[:a] + blk + text[b:]
    return text if text != str(prompt or "") else None


def _fix_transition(prompt, dlg):
    """把不合格的过渡拍（镜头1）改写成 ≤2.5 秒的静态特写。改不成返回 None。

    过渡拍是掩段间接缝用的短插入镜头，规则要求 ≤2.5 秒、特写、固定，且不许拍
    本段说话人的正脸（H3 对人脸过渡拍服从率低，会渲成站姿全身像）。
    导演五轮都改不对时，代码替它改写这一个镜头块。

    **镜头1 里有台词就不动**：过渡拍改成无对白插入会把那句台词弄丢，
    宁可照常打死，也不能让成片少一句话。
    """
    from . import authoring
    text = str(prompt or "")
    m = re.search(r"^镜头1（[^）\n]*）.*?(?=^镜头2（|\Z)", text, re.M | re.S)
    if not m:
        return None
    blk = m.group(0)
    if re.search(r"[「“]", blk):
        return None
    spk = set()
    for w, _t in dlg:
        spk.add(str(w))
        spk.update(x for x in re.split(r"[·•]", str(w)) if len(x) >= 2)
    # 规则和格式模板分开：模板是**要求照抄的**，正确答案必然包含它，
    # 拿它去查回抄会把满分答案误杀（2026-08-30 实测：搪瓷杯那一版本来完全合格）。
    rules = ("你是分镜师。用户给你一个镜头块，它是用来掩住段落接缝的**过渡拍**，"
             "现在不合格。把它改写成：0–2秒、特写、固定机位，只拍**一个具体细节**"
             "（手、道具、衣角、地面、光斑之类），细节必须是用户给的材料里已经出现过的，"
             "不许出现人的正脸和眼睛，不许有台词，不许有镜头移动，不许凭空添新东西。\n"
             "直接输出改好的镜头块，不要复述要求，不要加任何说明。")
    sysmsg = rules + "\n格式照这样：镜头1（0–2秒，特写，固定）：后面跟一句画面描述。"
    # 把本段其余画面一并给它当素材：过渡拍本身就是说话人的脸时，块内除了脸
    # 没有别的细节可拍，只给块等于让它无米下炊（2026-08-30 实测改不动）。
    rest = (text[:m.start()] + text[m.end():]).strip()
    umsg = ("【要改写的镜头块】\n%s\n\n【本段其它画面，细节从这里取】\n%s"
            % (blk.strip(), rest[:900]))
    try:
        new = authoring._q(sysmsg, umsg, mt=200, temperature=0.2).strip()
    except Exception:
        return None
    new = new.split("镜头2")[0].strip()
    h = re.match(r"^镜头1（([^）\n]*)）", new)
    if not h:
        return None
    hd = h.group(1)
    dm = re.search(r"([\d.]+)\s*[–\-~]\s*([\d.]+)\s*秒", hd)
    if not (dm and float(dm.group(2)) - float(dm.group(1)) <= 2.5):
        return None
    if "特写" not in hd or "固定" not in hd:
        return None
    body = new[h.end():]
    if (_move_hits(new) or re.search(r"[「“]", new)
            or authoring.echoed_instruction(rules, new)
            or (any(s and s in body for s in spk)
                and re.search(r"面部|脸|眼|眉|眸", body))):
        return None
    return text[:m.start()] + new + "\n" + text[m.end():]


def _static_ize(prompt, budget, figw):
    """把超预算的运镜改成固定机位。改不干净返回 None（不采纳，照常打死）。

    分两种命中，各治各的：
    ① **镜头头里的运镜字段**（镜头2（3–9秒，全景，缓推））——那是个枚举槽，
       直接写成「固定」就完事，不必问模型。先前整行丢给模型改写，改出来过不了
       自查就把整次修复都放弃了，运镜超限照样打死（2026-08-30 实测「环绕」）。
    ② 正文句子里的运镜——只重写那一句，画面内容一个字不动：删词会写出残句
       （"镜头缓推至她的脸"→"镜头至她的脸"），整段重写又贵又常把内容改坏。

    身材展示的运镜不动——那是用户明确批准的例外。
    """
    from . import authoring
    text = str(prompt or "")
    heads = [(m.start(), m.end()) for m in re.finditer(_HEAD_RE, text, re.M)]

    def _in_head(pos):
        return any(a <= pos < b for a, b in heads)

    # ① 镜头头：从后往前替换，前面的位置不受影响
    for h in reversed(_plain_hits(text, figw)):
        if _in_head(h.start()):
            text = text[:h.start()] + "固定" + text[h.end():]
    text = re.sub(r"(固定)(?:[、，]?固定)+", r"\1", text)

    # ② 正文句子。**某一句改不动就换下一句**，不要把已经改好的几处一起作废：
    # 全有或全无时，一段里五处运镜只要有一处改写没过自查，整段就被打死，
    # 前面四处白改（2026-08-30 实测「上移」单句能改好、真实段落照样被拒）。
    sysmsg = ("你是分镜师。用户给你一句分镜文字，里面写了镜头的移动。"
              "把它改成机位固定的写法：拍到的人、物、动作、台词、景别全部保留原样，"
              "只把镜头移动本身去掉。结果里不能出现推、拉、摇、移、跟拍、环绕。"
              "直接输出改好的那一句，不要复述要求，不要加任何说明。")
    for _ in range(8):
        hits = _plain_hits(text, figw)
        if len(hits) <= budget:
            return text if text != str(prompt or "") else None
        moved = False
        for h in reversed(hits):
            a = max(text.rfind(c, 0, h.start()) for c in "。！？\n") + 1
            ends = [i for i in (text.find(c, h.end()) for c in "。！？\n") if i >= 0]
            b = (min(ends) + 1) if ends else len(text)
            sent = text[a:b]
            # 先试**确定性删除**：整句就是一句运镜话术时（"镜头拉远至全景，交代空间"），
            # 模型没有实质内容可保留，就会把指令原文抄上去当描述（回抄检测拦下后
            # 修复也跟着放弃，整段打死，2026-08-30 实测）。这种形状直接删掉
            # 运镜从句即可——景别本来就写在镜头头里，删了不丢信息。
            _cut = re.sub(r"镜头[^，。；\n]{0,24}?(?:%s)[^，。；\n]{0,12}[，、]?"
                          % _MOVE_AMBIG.replace("(?![曳晃摆头篮滚])", ""), "", sent)
            if _cut != sent and not _move_hits(_cut):
                _core = re.sub(r"^[^：:]{0,20}[：:]", "", _cut).strip(" ，。")
                if len(_core) >= 4:
                    text = text[:a] + _cut + text[b:]
                    moved = True
                    break
            # 字段名（"开场状态："）摘出来不参与改写：模型总把它当废话删掉，
            # 于是长度只剩三分之一、被"长度离谱"挡下，内容本来是对的
            # （2026-08-30 实测）。改完再原样接回去。
            _lb = re.match(r"^([^\s：:，。]{2,6}[：:])\s*", sent)
            label, core = (_lb.group(1), sent[_lb.end():]) if _lb else ("", sent)
            ok = None
            for _temp in (0.2, 0.55):     # 一次不成再试一次：换个温度常常就过了
                try:
                    new = authoring._q(sysmsg, core, mt=220,
                                       temperature=_temp).strip().split("\n")[0]
                except Exception:
                    # 模型这一下没调通（超时／排队）不该让整段陪葬：镜头头那一步
                    # 已经是纯字符串替换，能救多少算多少，剩下的交给 T0 照常判。
                    return text if text != str(prompt or "") else None
                new = re.sub(r"^%s\s*" % re.escape(label), "", new) if label else new
                why = ("空" if not new
                       else "仍含运镜" if _move_hits(new)
                       else "回抄指令" if authoring.echoed_instruction(sysmsg, new)
                       # 下限放到 0.25：删掉运镜从句本来就会大幅缩短
                       # （"镜头从上一段埃里克的中景拉远至全景，展现哨卡全貌"
                       # → "全景，展现哨卡全貌"，删掉的全是运镜话术、内容一字没丢，
                       # 却卡在 0.4 的下限上差了半个字，2026-08-30 实测）。
                       # 另加 6 字底线，防止被改成一句空话。
                       else "长度离谱" if not (max(6, len(core) * 0.25) <= len(new)
                                            < len(core) * 1.8)
                       else "")
                if not why:
                    ok = label + new
                    break
                _dbg("运镜改写被拒", "-", "-", "-", "",
                     {"原句": sent[:120], "改写": new[:120], "原因": why, "温度": _temp})
            if ok is None:
                continue                  # 这一句改不动，换下一处
            text = text[:a] + ok + text[b:]
            moved = True
            break
        if not moved:                     # 一整轮没有一处改得动，认输
            return None
    return None


# 每个配方允许的运镜次数。**必须和 _PACKS 里写给 Qwen 的话一致**——
# 配方说「允许一次缓慢横移」，校验却按「有台词就零容忍」打回，就是自相矛盾，
# 三轮全灭（2026-08-29 实测：旅行段写了一次拉近被打死）。
_PACK_BUDGET = {"建立": 1, "旅行": 1, "对话": 0, "动作": 1, "情绪": 0}


def _pack_of(shots_text, dlg, is_scene_open):
    """判这一段该用哪个镜头配方。顺序有讲究：场次开场优先，其次动作，
    再次有台词=对话，然后赶路，剩下算情绪。"""
    t = str(shots_text or "")
    if is_scene_open:
        return "建立"
    if len(re.findall(_PACK_ACTION, t)) >= 2:
        return "动作"
    if dlg:
        return "对话"
    if len(re.findall(_PACK_MOVE, t)) >= 2:
        return "旅行"
    return "情绪"


def _pack_relay(pack):
    g, mv, tr = _PACKS[pack]
    return ("★★【本段镜头配方——%s戏】景别：%s；运镜：%s；过渡拍：%s\n\n"
            % (pack, g, mv, tr))


def _framing_tier(text, last=False):
    """一段分镜开场（或收尾）的景别档位；认不出返回 0。

    收尾景别优先读「收尾状态」行——单主机位改革后一段常只有镜头1，
    运镜途中景别早变了，镜头头只代表开场。
    """
    t = str(text or "")
    if last:
        m = re.search(r"^收尾状态[：:]([^\n]+)", t, re.M)
        if m:
            for name, tier in _TIERS:
                if name in m.group(1):
                    return tier, name
    heads = re.findall(r"^镜头\d+（([^）]*)）", t, re.M)
    if not heads:
        return 0, ""
    h = heads[-1] if last else heads[0]
    for name, tier in _TIERS:
        if name in h:
            return tier, name
    return 0, h[:6]


_LR_PAT = re.compile(r"([一-龥·]{2,6})站?在([一-龥·]{2,6})的?(左|右)手边")


def _lr_conflicts(texts):
    """人物左右手关系有没有前后打架（同段内＋跨段间）。

    「甲在乙的左手边」≡「乙在甲的右手边」（两人同朝向的约定）。归一成
    有序对后，同一对人物只允许一个方向——实测同一段里先写"艾拉站在凯尔
    右手边"隔两句又写"艾拉在凯尔的左手边"，成片方向自然乱（2026-08-28）。
    """
    seen, bad = {}, []
    for t in texts:
        for a, b, d in _LR_PAT.findall(str(t or "")):
            if a == b:
                continue
            x, y = sorted((a, b))
            left = a if d == "左" else b
            rel = "L" if left == x else "R"
            if seen.get((x, y), rel) != rel:
                bad.append("%s／%s" % (x, y))
            seen.setdefault((x, y), rel)
    return sorted(set(bad))


def _camera_stations(layout):
    """场景布局 → 两个法定机位的背景句（机位库锚）。

    每段独立生成时，H3 会把房间重新想象一遍——实测 seg2 的大全景里窗跑到
    书桌正后方、壁炉消失，和相邻段几何对不上（2026-08-28 用户点名"位置不一样"）。
    从布局的东西南北推出「朝北拍/朝南拍」两个机位各自的正面/画左/画右，
    中景以上只许二选一照抄，房间几何就定死了。"""
    items = re.findall(r"【([^】]{1,12})】[：:]([^｜\n]+)", str(layout or ""))
    walls = {"北": [], "南": [], "东": [], "西": []}
    for name, desc in items:
        for d in walls:
            if d in desc[:6]:
                walls[d].append(name)
                break
    if not (walls["北"] or walls["南"]):
        return ""
    # 这面墙没有具名物件时，说清楚是**哪一侧**的墙。原来一律写"该侧墙面"，
    # 是个没替换干净的占位说法，导演看着别扭就自己改成"西侧墙面"，
    # 然后因为和合法句不全等被判"无法机械修复"，整段打死（2026-08-30 实测）。
    def j(L, d):
        return "、".join(L) if L else "%s侧墙面" % d

    # 具名物件不足两个时**不给约束**。机位库锚的意义是把房间几何钉到具名物件上；
    # 一个物件都没有时生成的是「正面背景是该侧墙面；画面左侧是该侧墙面；
    # 画面右侧是该侧墙面」——一句没有信息的话，却要求导演逐字照抄，
    # 导演写了真实内容反而被判"没照抄机位句"打死（2026-08-30 实测干草堆那段）。
    if sum(len(v) for v in walls.values()) < 2:
        return ""

    def line(tag, front, left, right, fd, ld, rd):
        return ("机位%s：正面背景是%s；画面左侧是%s；画面右侧是%s"
                % (tag, j(front, fd), j(left, ld), j(right, rd)))
    return (line("A（朝北拍）", walls["北"], walls["西"], walls["东"], "北", "西", "东")
            + "\n"
            + line("B（朝南拍）", walls["南"], walls["东"], walls["西"], "南", "东", "西"))


def _dlg_block(screenplay, dlg_text):
    """剧本里这句台词的原拍：它上面那行镜头 + 台词行本身（给重试反馈当抄写样板）。"""
    lines = str(screenplay or "").splitlines()
    for i, line in enumerate(lines):
        if dlg_text and dlg_text in line:
            prev = next((lines[j] for j in range(i - 1, -1, -1) if lines[j].strip()), "")
            return (prev + "\n" + line).strip()
    return dlg_text


def _fabricated_quotes(dialogues, seg_prompt, full_body=""):
    """剧本里**查无此话**的台词引句（≥4字才算，短叹词误杀率高）。

    嵌套引号豁免：导演写「埃德里克说：「不要工钱？」」时，外层引号内容
    是"叙述＋真台词"——只要引号里**包着**某句剧本台词就不算编造
    （2026-08-29 实测误报导致 seg4 三连拒）。

    ★ 判据是**整份剧本**，不是本段的台词表（2026-08-30 实测 STORY_074）。
      本段台词表来自 _dialogue_list，它认不出没有说话动词的无名台词
      （「"小心点，它很烫。"一个声音从阴影里传来。」——"传来"不是说话动词），
      于是这句真台词被判成编造。原来的处置是打死整段，改成机械删除之后，
      **真台词被直接删掉了**，比原来更糟。
      编造指的是导演凭空造话；剧本里写着的话，不管本段认没认出来，都不是编造。
    """
    alln = [_norm_quote(t) for _, t in dialogues]
    fb = _norm_quote(full_body)
    out = []
    for q in re.findall(r"「([^」]+)」", str(seg_prompt or "")):
        nq = _norm_quote(q)
        if len(nq) < 4:
            continue
        if any(nq in a or a in nq for a in alln if a):
            continue
        if fb and nq in fb:
            continue
        out.append(q)
    return out


def direct_next(sid, ep=1, call_model=None):
    """逐段导演：读已确认的前几段，只生成**下一段**并追加进 timeline。
    用户改过的前段会一并当上下文喂进去，保证下一段接住改后的末尾状态。
    返回 (段dict 或 None, raw)；None 表示剧本已全部分完。"""
    from . import saga_core, story_core, authoring
    st = story_core.get_story(sid) or {}
    s = st.get("settings") or {}
    e = saga_core.episode(saga_core.get_saga(sid), ep) or {}
    body = str(e.get("body") or "")
    if not body.strip():
        raise RuntimeError("第%d话还没有剧本正文" % ep)
    tl, existing = _confirmed_segments(sid, ep)
    confirmed = [str(g.get("prompt") or "") for g in existing]
    n_next = len(confirmed) + 1
    scale = authoring._scale(s, "shot")
    ins = INS.read_text(encoding="utf-8")
    done_txt = "\n".join("第%d段已经拍到：%s" % (i + 1, _took_to(t))
                        for i, t in enumerate(confirmed))
    # 上一段的「收尾状态」单独拎出来——只丢全文的话，Qwen 只知道"别重复"，
    # 不知道"要接住"（2026-08-27 用户反馈第1、2段衔接不上）。
    last_end = ""
    if confirmed:
        _i = confirmed[-1].rfind("收尾状态")
        if _i >= 0:
            last_end = confirmed[-1][_i:].strip()
    # 背景锚接力：同一场戏各段独立生成时，Qwen 每段都会重选拍摄方向
    # （实测三段三个轴、背景乱跳）。机制同台词进度锚——已确定的事实由代码递进去，
    # 不留给模型重新选择：第一段定下的锚，后续段照抄。
    scene_anchor = None
    for p in confirmed:
        m = re.search(r"^本段背景锚[：:]\s*([^\n]+)", p, re.M)
        if m:
            # Qwen 会把接力句的前缀一起抄进字段，剥掉，锚永远是裸的；
            # 动作词也要掐成静态——"刚刚合拢的门"接力下去，每一段都把关门再演一遍
            # （2026-08-28 实测门关了两次）。
            a = re.sub(r"^人物身后的背景自始至终是[：:]?\s*", "", m.group(1).strip())
            a = re.sub(r"(刚刚|正在|缓缓|逐渐)(合拢|关上|闭合)", "紧闭", a)
            # 透光短语会被下游放大成开门灌日光，接力前摘干净（同 h3_view 的清洗）
            a = re.sub(r"[，；]?\s*[^，；。]*(?:门缝|窗缝)[^，；。]*(?:透入|漏入|洒入|渗入)[^，；。]*", "", a)
            scene_anchor = re.sub(r"刚刚|正在", "", a)
    anchor_relay = ""
    if scene_anchor:
        anchor_relay = ("★★【本场已定背景锚——照抄】人物身后的背景自始至终是：%s\n"
                        "你这一段的「本段背景锚」照抄这一句。只有剧本在本段发生了"
                        "有动机的空间转移（人物走到新位置、转向新目标）才允许换锚，"
                        "并且要在镜头里把转移过程拍出来。\n\n" % scene_anchor)
    # 左右锚接力：机制同背景锚——第一段定下的人物左右手关系是整场定盘星。
    # 只靠事后校验打回，Qwen 三次都改不对（2026-08-28 实测第三段仍写反）；
    # 已确定的事实由代码递进去，不留给模型重新选择。
    lr_seen = {}
    for p in confirmed:
        for a, b, d in _LR_PAT.findall(p):
            if a != b:
                x, y = sorted((a, b))
                lr_seen.setdefault((x, y), "%s在%s的%s手边" % (a, b, d))
    if lr_seen:
        anchor_relay += ("★★【本场已定左右锚——照抄，整场不变】%s。\n"
                         "所有段落统一用这一个说法写相对位置，不许写出相反的方向。\n\n"
                         % "；".join(lr_seen.values()))
    # 画面左右锚：同理接力。相对位置锁了但机位越到对面（过肩反打），
    # 画面左右照样镜像（2026-08-28 实测 seg2 被拍成柜台后反打，整幅翻面）。
    # 轴锁切镜是用户验证过的正解——第一段定下的画面左右，整场锁死。
    ps_seen = {}
    for p in confirmed:
        # 间隔里再出现左右字样的（「凯尔右侧（画面右侧）」）说话对象存疑，宁缺毋滥
        for a, d in re.findall(r"([一-龥·]{2,6})[^\n。；，左右]{0,10}[（(]?画面(左|右)侧", p):
            ps_seen.setdefault(a, d)
        for d, a in re.findall(r"画面(左|右)侧[为是]([一-龥·]{2,6})", p):
            ps_seen.setdefault(a, d)
    # 固定物（门/壁炉/窗…）也纳入画面锚——只锁人物时，机位一转固定物就换边，
    # 实测同一场里 seg2 门口在画左、seg3 门口跑到"玛莎最右侧靠近门口"自相矛盾
    _FIXED = ("门口", "大门", "双开门", "壁炉", "窗", "柜台", "楼梯", "床")
    for p in confirmed:
        for a in _FIXED:
            for m in re.finditer(a + r"[^\n。；，左右]{0,8}画面(左|右)侧", p):
                ps_seen.setdefault(a, m.group(1))
            for m in re.finditer(r"画面(左|右)侧[为是][^，。；\n]{0,6}" + a, p):
                ps_seen.setdefault(a, m.group(1))
    ps = {a: d for a, d in ps_seen.items()
          if a in _FIXED or any(a in x or x in a for pair in lr_seen for x in pair)}
    if ps:
        anchor_relay += ("★★【本场已定画面左右——照抄，整场同轴】%s。\n"
                         "整场从轴线同一侧拍摄，机位只在这一侧内换角度换景别，"
                         "所有段落保持这个画面左右。\n\n"
                         % "、".join("%s在画面%s侧" % (a, d) for a, d in ps.items()))

    # 站位表接力（第五锚）：上一段结束时人在哪，这一段开场就在哪——
    # 只锁画面左右不锁绝对位置时，玛莎每段在房间里漂移（2026-08-28 用户点名）
    prev_table = ""
    if confirmed:
        _tm = re.search(r"^站位表[：:]\s*(.+)$", confirmed[-1], re.M)
        prev_table = (_tm.group(1).strip() if _tm else "")
    table_relay = ""
    if prev_table:
        table_relay = ("★★【开场站位表——本段开场时人物就在这些位置，照抄起步】\n"
                       "站位表：%s\n谁要换位置，必须在本段镜头里拍出他移动的过程，"
                       "并在结尾的站位表里更新他的新位置；没拍移动就不许变。\n\n" % prev_table)

    # 【切段由代码做，Qwen 只导戏】让它自己定范围时贪多→装不下→丢长台词，
    # 三次重试都治不了（2026-08-28 实测）。剧本每镜带秒数，切段是确定性计算；
    # Qwen 只拿到本段的剧本拍组原文，打包自由没了，跳句/超时这一类病根消失。
    slices = slice_screenplay(body)
    if n_next > len(slices):
        return None, END_MARK
    sl = slices[n_next - 1]
    # 本段必须拍全的台词（校验用）。人名表用来认内联台词的说话人。
    try:
        from . import asset_core as _ac0
        _names = [c.get("name") for c in _ac0.list_assets(sid, "characters") if c.get("name")]
    except Exception:
        _names = []
    dlg = _dialogue_list(sl["shots"], _names)
    # 剧本这一段谁都没动的话，站位接力直接钉死——实测 Qwen 会擅自把人挪回
    # 它顺手的位置（书桌），三轮反馈都掰不回来，不如开头就把路堵上
    # 移动动作词表。**宁可放宽也不能太窄**：窄了会把剧本要求的走位判成"没授权"，
    # 把人钉在做不到那个动作的位置上，导演合理地挪人反被判瞬移、整段打死
    # （2026-08-30 实测：剧本写「林默推开后门」，词表不认识"推开"，
    # 于是把他钉在柜台后——站在柜台后推不开后门）。
    # 放宽的代价只是偶尔漏判一次连续性；收紧的代价是这一段没有片子。
    def _aliases(n):
        """人名的等价写法。剧本爱用简称：站位表写「货车司机」，剧本写「司机」，
        名字对不上就以为没授权移动，导演照剧本走位反被判瞬移、整段打死
        （2026-08-30 实测）。全名、括号/间隔号拆出的每一节、以及末 2~3 字都算。
        """
        s = str(n or "").strip()
        out = {s}
        out.update(x for x in re.split(r"[·•（）()]", s) if len(x) >= 2)
        if len(s) >= 3:
            out.update({s[-2:], s[-3:]})
        # 掐掉跨括号/间隔号切出来的碎片（「纸男）」「板）」「·霍恩」），
        # 这种碎片会乱匹配。全名本身保留，不受这条限制。
        clean = {s} | {x.strip() for x in out
                       if len(x.strip()) >= 2 and not re.search(r"[·•（）()]", x)}
        return sorted(clean, key=len, reverse=True)

    _MOVE_VERB = (r"(走|上前|来到|退|迈|踏|跨|穿过|绕过|跑|冲|奔|返回|回到|跟上|"
                  r"凑近|起身|站起|坐下|坐回|落座|靠近|挪|移步|推开|拉开|俯身|"
                  r"蹲下|转身|下楼|上楼|出门|进门|钻进|翻过|"
                  r"并肩|脚步|快步|大步|疾走|追上|赶上|迎上|贴近|退开|错身|"
                  r"跟随|领着|带路|绕到|移动|"
                  # 被别人搬动、或姿态整体改变的（"卡修斯把她提溜起来，让她骑在左肩上"）
                  r"踉跄|靠在|骑在|抱起|抱住|提起|提溜|扛|背起|拉起|拽|扶起|"
                  r"搂|拖|放下|摔|倒下|爬起|翻身|挪到)")
    if prev_table and not re.search(_MOVE_VERB, sl["shots"]):
        table_relay += ("★★【本段剧本没有任何移动动作——所有人整段钉在开场站位表的"
                        "位置上，姿态不变，结尾站位表照抄开场的绑定，一字不改】\n\n")
    elif prev_table:
        # 整段有人动、别人没动时，原来这条钉不生效，Qwen 就顺手把没戏的人也挪了，
        # 三轮反馈掰不回来，最后按「瞬移未拍移动」整段打死（2026-08-30 实测林远）。
        # 改成**逐人钉**：剧本没给谁移动动作，就点名钉住谁。判定口径和校验一致
        # （裸「移」不算，「目光移开」是视线不是走位）。
        _still = []
        for _n in re.findall(r"([一-龥·（()）]{2,10})＝", prev_table):
            _pat = "|".join(re.escape(a) for a in _aliases(_n))   # 简称也算（司机／货车司机）
            _mv = [h for h in re.finditer(r"(?:%s)[^\n。]{0,20}?%s" % (_pat, _MOVE_VERB),
                                          sl["shots"])
                   if not re.search(r"(目光|视线|眼神)[^。]{0,8}$",
                                    sl["shots"][:h.start(1)])]
            if not _mv:
                _still.append(_n)
        if _still:
            table_relay += ("★★【这几个人本段剧本里没有任何移动动作：%s——他们整段钉在"
                            "开场站位表的位置上，姿态不变，结尾站位表里他们的绑定"
                            "照抄开场，一字不改】\n\n" % "、".join(_still))
    # 机位库锚：从这一场的场景卡布局推导两个法定机位（背景句代码算好），
    # 中景以上的镜头只许二选一并照抄背景句——房间几何不再每段重新想象
    cam_relay = ""
    try:
        from . import asset_core as _ac
        _lm = re.search(r"##\s*场景\d+｜([^｜\n]+)", sl["scene_head"] or "")
        _loc = (_lm.group(1) if _lm else "").strip()
        _card = next((x for x in _ac.list_assets(sid, "scenes")
                      if x.get("name") and (x["name"] in _loc or _loc in x["name"])), None)
        _cams = _camera_stations((_card or {}).get("layout") or "")
        if _cams:
            cam_relay = ("★★【机位库——本场只有这两个机位，中景以上的镜头必须选其一，"
                         "并把它的背景句照抄进镜头描述里】\n" + _cams + "\n\n")
    except Exception:
        cam_relay = ""
    # 镜头配方：这一段是什么戏就用什么镜头语法（场次第一段＝建立镜头）
    _prev_head = (slices[n_next - 2]["scene_head"] if n_next >= 2 else "")
    _scene_open = (sl["scene_head"] or "") != (_prev_head or "")
    pack = _pack_of(sl["shots"], dlg, _scene_open)
    # 影片类型的镜头倾向 ＋ 视点的整组机位规则（2026-08-29 五维改造）。
    # 视点不是第三人称时，机位库/站位表那套第三人称的锚**整组作废**——
    # 第一视角下镜头就是眼睛，「拍摄轴不翻面」根本不成立。
    _genre_shot, _pov_rules, _pov = "", "", "电影第三人称"
    try:
        from . import kits as _k
        _genre_shot = _k.genre_shot_block(str(s.get("genre") or ""))
        _pov = str(s.get("pov") or "") or "电影第三人称"
        if _pov != "电影第三人称":
            _pov_rules = _k.pov_block(_pov)
    except Exception:
        pass
    if _pov_rules:
        cam_relay = ""          # 机位库属于第三人称，换视点就撤掉
        table_relay = ""        # 站位表同理，由视点自己的锚接管
    user = (
        "【项目设定】世界：%s。画风：%s。内容尺度：%s。\n【人物锚点（照这个画谁是谁）】\n%s\n%s\n"
        % (s.get("world_type"), s.get("style"), scale, _anchors(sid),
           _voice_block(sid))
        + ((_pov_rules + "\n\n") if _pov_rules else "")
        + ((_genre_shot + "\n\n") if _genre_shot else "")
        + (("【前 %d 段已经拍到哪儿了（衔接用，不许重拍）】\n%s\n\n"
            % (len(confirmed), done_txt)) if confirmed else "【这是全片第一段】\n\n")
        + _pack_relay(pack) + anchor_relay + cam_relay + table_relay
        + (("★★【上一段的收尾状态——本段「开场状态」的人物状态要接住它，"
            "但开场镜头必须换档（景别跳一档或角度转30度以上）】\n%s\n\n"
            % last_end) if last_end else "")
        + "★★【你这一段要导的剧本拍组——只导它，一拍不多、一拍不少、一句台词不落】\n"
        + sl["scene_head"] + "\n\n" + sl["shots"] + "\n"
        + "（这组拍子的秒数和是 %g 秒，「本段时长」就写 %g 秒。）\n\n" % (sl["seconds"], sl["seconds"])
        + "【输出第 %d 段】把上面这组拍子导成一段视频分镜，**只输出这一段**"
          "（一个 `===== 第%d段 =====` 块），严格照系统指令里的完整结构和详细程度来写。"
          % (n_next, n_next))
    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            # 和 authoring._q 一样退避重试：显存吃紧时 llama-server 会吐 500，
            # 一次抖动不该让这一段没有片子（2026-08-30 实测第 8 轮 500）。
            last = None
            for i in range(3):
                try:
                    return qwen_client.chat(sysmsg, usr, max_tokens=6000,
                                            reasoning=False, **kw)
                except Exception as ex:
                    last = ex
                    if i < 2:
                        time.sleep(3 * (i + 1))
            raise last

    # 校验：拍组里的台词一句不落、不编造、不抄占位符。范围已由代码定死，跳段不可能。
    seg, raw = None, ""
    for attempt in range(1, _TRIES + 1):
        raw = (call_model(ins, user, temperature=0.7) or "").strip()
        parsed = parse_segments(raw)
        if parsed:
            cand = parsed[0]
        elif raw:
            cand = {"seconds": None, "prompt": _strip_angle(raw)}   # 没规范分隔也兜底
        else:
            return None, raw
        # 【先修头身矛盾】镜头头写「固定」、正文却写"镜头缓慢推近"——同一个镜头里
        # 自己打自己，发给 H3 它要么不动要么乱动。这一条没有校验器管，
        # 段落会带着矛盾直接出片，所以在**每一段产出后立刻修**，不等失败才修
        # （2026-08-30 逐字读 STORY_065 第 2 镜发现）。
        _hb = _fix_head_body_move(cand.get("prompt") or "", r"身材|身形|曲线|体态|全身")
        if _hb:
            cand["prompt"] = _hb
        bad = _fabricated_quotes(dlg, cand.get("prompt") or "", body)
        tpl = "（起–止秒" in (cand.get("prompt") or "")
        # 完整性只认**时间块里的编排**——「台词：」汇总栏不算拍
        # （实测：两句台词只进汇总没进镜头，校验被骗过，成片里就漏说）。
        # 引号认「」和曲引号“”两种（导演叙述式内嵌用的是后者）。
        pre = (cand.get("prompt") or "").split("\n台词：", 1)[0]
        pre = re.sub(r"^本段时长[^\n]*$", "", pre, flags=re.M)
        shot_quotes = re.findall(r"「([^」]+)」", pre) + re.findall(r"“([^”]+)”", pre)
        # 长台词允许**跨镜说完**：把本段所有引号按顺序拼起来再查一次——
        # 现实里一句长台词说到一半切镜是常规拍法，只认单引号完整匹配的话，
        # 28 字的台词被拆到两镜就判"没拍全"，三轮全灭（2026-08-29 实测）
        _joined = _norm_quote("".join(shot_quotes))
        miss = [(w, t) for w, t in dlg
                if not any(_norm_quote(t) in _norm_quote(q) or _norm_quote(q) in _norm_quote(t)
                           for q in shot_quotes)
                and _norm_quote(t) not in _joined]
        # 静态机位律（2026-08-28 用户定）：运镜一段最多一次——安静戏被连续推拉
        # 拍成"无人机巡游"。摇曳/拉开这类日常词不算，只数明确的运镜词。
        cp = cand.get("prompt") or ""
        # 文戏零运镜（2026-08-29 用户定案）：有台词的段推拉会晕、拉向特写会穿帮，
        # 景别变化只许切镜；无台词的动作段保留一次带动机的运镜。
        # 唯一文戏例外：身材展示拍（成人向）——句内写明展示身材的缓慢运镜放行一次
        # 元数据行里的命中一律不算——校验说超标、修复却看不到那一处，就是死循环
        _meta = _meta_spans(cp)
        _mv = [m for m in _move_hits(cp)
               if not any(a <= m.start() < b for a, b in _meta)]
        _figw = r"身材|身形|曲线|体态|全身"
        _hp = [m.start() for m in re.finditer(r"^镜头\d", cp, re.M)]

        def _shot_of(pos):
            k = -1
            for i, hp0 in enumerate(_hp):
                if hp0 <= pos:
                    k = i
                else:
                    break
            return k

        fig, fig_shots, plain = [], set(), []
        for h in _mv:
            if re.search(_figw, cp[max(0, h.start() - 50):h.end() + 50]):
                fig.append(h.group(0))
                fig_shots.add(_shot_of(h.start()))
            else:
                plain.append(h.group(0))
        # 运镜预算由**镜头配方**给（和提示词里写给 Qwen 的话一致）；
        # 身材展示拍按镜头块计数：一个镜头里的缓推+下移算同一次运动
        _budget = _PACK_BUDGET.get(pack, 0 if dlg else 1)
        overmove = (plain if len(plain) > _budget
                    else (fig if len(fig_shots) > 1 else None))
        # 过渡拍律：镜头1 必须是 ≤2.5 秒的静态特写（掩接缝用）。
        # **建立段豁免**：过渡拍是用来掩段间接缝的，而场次第一段是新场景开场
        # （全片第一段更没有上一段），本来就该用全景建立空间——两条规则打架时
        # 让建立包优先，否则 Qwen 照配方写全景、被过渡拍打回，三轮全灭
        # （2026-08-29 实测 063 第 1 段）。
        heads = re.findall(r"^镜头\d+（([^）]*)）", cp, re.M)
        notrans = None
        if heads and pack != "建立":
            h1 = heads[0]
            dm = re.search(r"([\d.]+)\s*[–\-~]\s*([\d.]+)\s*秒", h1)
            dur = (float(dm.group(2)) - float(dm.group(1))) if dm else 99
            if "特写" not in h1 or "固定" not in h1 or dur > 2.5:
                notrans = h1[:24]
            else:
                # 过渡拍不许拍本段说话人的正脸——实测人脸过渡拍 H3 服从率低
                # （直接渲成站姿全身像），手/道具/听者反应服从率高
                _l1 = re.search(r"^镜头1（[^）]*）[：:]?([^\n]*)", cp, re.M)
                _l1t = _l1.group(1) if _l1 else ""
                # 说话人可能以短名出现（埃德里克·凡·霍恩→埃德里克），全名和
                # ·分隔的每一节都算——实测全名匹配漏放了短名的眼眸特写
                _spk = set()
                for w, _t in dlg:
                    _spk.add(str(w))
                    _spk.update(x for x in re.split(r"[·•]", str(w)) if len(x) >= 2)
                if any(s and s in _l1t for s in _spk) and re.search(r"面部|脸|眼|眉|眸", _l1t):
                    notrans = "过渡拍拍了说话人的脸"
        # 站位表律：每段必须输出站位表；有开场表时，换绑定物必须配移动动作
        notable, movebad = None, []
        _tb = re.search(r"^站位表[：:]\s*(.+)$", cp, re.M)
        if not _tb:
            notable = True
        elif prev_table:
            cur = dict(re.findall(r"([一-龥·]{2,8})＝([^；;（(]+)", _tb.group(1)))
            prv = dict(re.findall(r"([一-龥·]{2,8})＝([^；;（(]+)", prev_table))
            # 开场姿态（站/坐）也要接住——实测站位表照抄合规、正文却整段
            # 「坐在书桌后」：表格合规正文违规，只审表拦不住
            prv_post = dict(re.findall(r"([一-龥·]{2,8})＝[^；;（(]*[（(](站|坐)",
                                       prev_table))

            def _move_ok(n2):
                # 移动授权只认剧本拍组——搜导演自己的产物的话，它会伪造
                # 「后退坐回」给瞬移开绿灯（实测把下一段的坐回偷拍进本段）。
                # 裸「移」不算：「目光/眼神…移开」全是视线，两次骗过审计。
                # 词表和上面的钉位共用 _MOVE_VERB，两处走样过一次。
                # 代词也认：剧本常写「他放轻脚步…」，只认全名会把授权漏掉。
                # 判定按**整句**来，不数字符距离：「艾拉踉跄一下，顺势靠在卡修斯
                # 腿上」这类句子里，人名和动作之间隔多远全看行文，卡 20 字的窗口
                # 只是碰运气（2026-08-30 实测艾拉被抱上肩，判成瞬移打死）。
                shots = sl["shots"]
                al = _aliases(n2)
                npat = "|".join(re.escape(a) for a in al)
                for sent in re.split(r"[。！？\n]", shots):
                    mv = [h for h in re.finditer(_MOVE_VERB, sent)
                          if not re.search(r"(目光|视线|眼神)[^。]{0,8}$",
                                           sent[:h.start()])]
                    if not mv:
                        continue
                    if re.search(npat, sent):
                        return True
                    # 整句只有代词时，看这一句之前最近提到的是不是他
                    if re.search(r"[他她我]", sent):
                        seg0 = shots[:shots.find(sent)] if sent else ""
                        last = None
                        for nm in cur:
                            p = seg0.rfind(nm)
                            if p >= 0 and (last is None or p > last[0]):
                                last = (p, nm)
                        if last and last[1] == n2:
                            return True
                return False

            for n2, pos2 in cur.items():
                p2 = prv.get(n2)
                if p2 and p2.strip() != pos2.strip() and not _move_ok(n2):
                    movebad.append(n2)
            # 【瞬移直接改回去，不打死整段】用户 2026-08-30 定的原则：
            # qwen 有错直接改正修复，而不是堵住让他重来。
            # 判据本身是对的——剧本没给移动动作，这个人就没动过；
            # 错的是处置：原来 5 次不过就 raise，**整段视频没了**
            # （实测 STORY_070 第 5 段就这么丢的，前 5 段只出了 4 段）。
            # 站位表是导演自己的记账，不是故事内容：把这几个人的绑定
            # 照抄上一段的结尾表，瞬移就不存在了，画面反而更稳。
            if movebad:
                _line = _tb.group(1)
                _new = _line
                for n2 in movebad:
                    _old = re.search(re.escape(n2) + r"＝[^；;]+", _line)
                    _src = re.search(re.escape(n2) + r"＝[^；;]+", prev_table)
                    if _old and _src:
                        _new = _new.replace(_old.group(0), _src.group(0))
                if _new != _line:
                    # 按匹配区间替换，不拼「站位表：」——冒号可能是半角、
                    # 后面可能有空格，拼字符串会悄悄替换失败
                    cp = cp[:_tb.start(1)] + _new + cp[_tb.end(1):]
                    cand["prompt"] = cp
                    cur = dict(re.findall(r"([一-龥·]{2,8})＝([^；;（(]+)", _new))
                    movebad = [n2 for n2, pos2 in cur.items()
                               if prv.get(n2)
                               and prv[n2].strip() != pos2.strip()
                               and not _move_ok(n2)]
                    _dbg("瞬移改回上一段站位", sid, ep, n_next, cp,
                         {"改前": _line[:120], "改后": _new[:120],
                          "剩余": movebad})
            for n2, ep2 in prv_post.items():
                if n2 in movebad or _move_ok(n2):
                    continue
                wrong = (r"(坐在|坐回|坐进|落座)" if ep2 == "站"
                         else r"(站在|站起|起身)")
                if re.search(re.escape(n2) + r"[^\n。]{0,6}" + wrong, cp):
                    movebad.append(n2)
        # 机位库律：有机位库时，中景以上的镜头必须**原文照抄**某个机位的背景句——
        # 只写"机位B"标签不算（实测贴了标签、背景句一句没抄，标签还写反了朝向）
        nocam = None
        if cam_relay and any(any(w in h for w in ("中景", "中近景", "全景", "远景"))
                             for h in heads):
            cam_sents = [s.strip() for s in re.findall(r"(正面背景是[^\n]+)", cam_relay)]
            if not any(s in cp for s in cam_sents):
                nocam = True
            else:
                # 精确匹配律：文中每一处「正面背景是…」都必须逐字等于合法机位句，
                # 句内塞私货（如"画面左侧是壁炉与埃德里克"）一律打回。
                # **只管中景以上的镜头**：机位库锚是用来钉房间几何的，拿它去卡
                # 一个脸部特写（"正面背景是林远模糊的侧脸轮廓"）没有道理，
                # 而且永远对不上，整段跟着打死（2026-08-30 实测）。
                for _m in re.finditer(r"正面背景是[^\n]+", cp):
                    t = _m.group(0)
                    _h = cp.rfind("镜头", 0, _m.start())
                    _e = cp.find("\n", _h)
                    _hl = cp[_h:_e if _e >= 0 else len(cp)] if _h >= 0 else ""
                    if _hl and not any(w in _hl for w in ("中景", "中近景", "全景", "远景")):
                        continue
                    if not any(_cam_key(t).startswith(_cam_key(s)) for s in cam_sents):
                        nocam = True
                        break
        # 换档律校验：开场景别和上一段收尾同档＝跳剪。
        # **有合规过渡拍时不查**——1-2 秒的特写插在接缝上，换档已经由它完成，
        # 再要求镜头2 的主机位也换档是双重要求：实测第 4 段连续三轮死在
        # 「近景→（特写过渡）→近景」，而这个接法视觉上根本不跳（2026-08-29）。
        samecut = None
        if confirmed and notrans is not None:
            pt, pn = _framing_tier(confirmed[-1], last=True)
            main_head = heads[0] if heads else ""
            ct, cn = 0, ""
            for name, tier in _TIERS:
                if name in main_head:
                    ct, cn = tier, name
                    break
            if pt and ct and pt == ct and ct != 1:
                samecut = (pn, cn)
        lrbad = _lr_conflicts(list(confirmed) + [cand.get("prompt") or ""])
        # 【视点门控】机位库、站位表、左右锚、过渡拍、静态机位——这一整套
        # 都是为「摄影机是第三只眼」写的。换成 POV／自拍后它们的前提不成立
        # （镜头就是眼睛，轴线会跟着头转），必须整组失效，否则新视点一上来
        # 就被老校验打死（2026-08-29 五维改造）。视点自身的规矩由 pov_block 管。
        povbad = None
        if _pov_rules:
            nocam = notable = lrbad = notrans = None
            movebad = []
            overmove = None
            # 视点专属校验（规则得有人守，否则 Qwen 照第三人称写——实测 POV 段
            # 写出「镜头拉远，展现出埃里克背着邮包的全貌」，主角被拍进了画面）
            # POV 的主角是**视角人物**，不是这段第一个说话的人——用第一个说话人
            # 判定时，别人说话的段就把主角认错了（实测把艾拉当成了视角人物，
            # 于是「埃里克的背影占据前景」这种明显违规被放过）。
            # 视角人物＝人物卡里的第一张（角色设计师按主次排），设定里指定了就用指定的。
            _lead = str(s.get("pov_lead") or "").strip()
            if not _lead:
                try:
                    from . import asset_core as _ac2
                    _cs = [str(c.get("name") or "") for c in _ac2.list_assets(sid, "characters")
                           if str(c.get("name") or "").strip()]
                    _lead = _cs[0] if _cs else ""
                except Exception:
                    _lead = ""
            if _pov == "第一视角POV" and _lead:
                # 主角被当成被拍对象的各种写法：X的背影/侧脸/全貌、聚焦X、
                # 展现出X、画面中X、X占据前景……只要他成了被摄体就打回
                _bad_hit = (
                    re.search(re.escape(_lead)
                              + r"(?:的)?\s*(全貌|身影|背影|全身|侧脸|正脸|面容|表情|"
                                r"轮廓|身形|背部|脸上)", cp)
                    or re.search(r"(聚焦|对准|展现出|拍摄|切至|画面中(?:的)?|镜头对着)"
                                 r"[^\n。]{0,6}" + re.escape(_lead), cp)
                    or re.search(re.escape(_lead) + r"[^\n。]{0,8}(占据|出现在)"
                                 r"[^\n。]{0,4}(画面|前景|镜头)", cp))
                if _bad_hit:
                    povbad = ("第一视角下**视角人物%s绝不能出现在画面里**——"
                              "镜头就是他的眼睛，只能拍他看到的东西（别人、环境、"
                              "他自己的手和拿着的东西）。把「%s的背影／侧脸／聚焦%s」"
                              "这类写法全部改掉：改成他看到了什么。"
                              % (_lead, _lead, _lead))
            elif _pov == "自拍手持":
                if not re.search(r"看(?:向|着)镜头|对着镜头|直视镜头|朝镜头", cp):
                    povbad = ("自拍视角下**主角必须看着镜头说话**——"
                              "写明他看向镜头、对着镜头讲话；这是自拍和第三人称最大的区别")
        # 景别配方校验：建立段必须真给建立镜头；任何一段不许整段贴脸拍
        # （实测全片全景占比 0%、近景+特写 70%，观众不知道人在哪——2026-08-29）
        packbad = None
        _wide_n = sum(1 for h in heads
                      if any(w in h for w in ("全景", "远景", "中远景", "大全景")))
        _mid_n = sum(1 for h in heads if "中景" in h)
        if heads:
            if pack in ("建立", "旅行") and _wide_n == 0:
                packbad = "本段是%s戏，一个全景/远景都没有" % pack
            elif pack == "情绪" and _wide_n == 0 and len(heads) > 2:
                packbad = "情绪戏整段贴着脸拍，缺一个环境全景托底"
            elif _wide_n == 0 and _mid_n == 0:
                packbad = "整段全是近景和特写，没有一个中景以上的镜头交代人在哪"
        if (not bad and not tpl and not miss and not samecut and not lrbad
                and not overmove and not notrans and not nocam
                and not notable and not movebad and not packbad and not povbad):
            cand["seconds"] = float(sl["seconds"])   # 时长以切段计算为准
            seg = cand
            break
        if attempt < _TRIES:
            probs = []
            if bad:
                probs.append("你编了拍组里没有的台词：%s——台词只能逐字来自上面的拍组"
                             % "；".join("「%s」" % q for q in bad[:3]))
            if tpl:
                probs.append("镜头头写的还是模板占位符「（起–止秒，景别，运镜）」——"
                             "必须换成真实数字和景别（例：镜头1（0–5秒，全景，固定））")
            if miss:
                probs.append("拍组里这几句台词你没拍：%s——每一句都要按"
                             "「人名（语气）说，「原文」」编排进镜头里"
                             % "；".join("%s：「%s」" % (w, t[:20]) for w, t in miss[:3]))
            if samecut:
                probs.append("你的开场镜头（%s）和上一段收尾镜头（%s）是同一档景别——"
                             "这是跳剪。开场必须换档：景别至少跳一档（特写↔近景↔中景↔全景）"
                             "或机位角度转30度以上" % (samecut[1], samecut[0]))
            if lrbad:
                probs.append("这几对人物的左右手关系你写得前后矛盾（或和之前段落矛盾）：%s——"
                             "人物相对位置是定盘星，整场只许一个方向，先想清楚谁在谁的哪只手边，"
                             "再全段统一用这一个说法" % "；".join(lrbad))
            if overmove:
                probs.append("运镜写了 %d 处（%s）——%s"
                             % (len(overmove), "、".join(overmove[:4]),
                                ("这是有台词的文戏，**零运镜**：每个镜头一律「固定」，"
                                 "景别变化全部用切镜（切必换档），把推拉摇移全部改成切镜"
                                 if dlg else
                                 "动作段一段最多一次运镜且要写明动机（跟随/环绕），"
                                 "其余镜头锁定「固定」")))
            if notrans:
                probs.append("镜头1（%s…）不合过渡拍规矩——必须是 1–2 秒的静态特写，"
                             "拍**手、道具或听者反应**（绝不拍本段说话人的正脸）：`镜头1"
                             "（0–2秒，特写，固定）：<上一段结尾的一个细节>`，"
                             "镜头2 起才是主机位" % notrans)
            if nocam:
                probs.append("中景以上的镜头没有照抄机位背景句——只写「机位A/B」标签不算，"
                             "必须把选中机位的整句「正面背景是…；画面左侧是…；画面右侧是…」"
                             "一字不差抄进镜头描述里")
            if notable:
                probs.append("缺「站位表：」行——最后必须输出本段结束时每个在场人物的"
                             "位置表：`站位表：名字＝固定物＋方位（站/坐）；…`，"
                             "固定物从【布局】里取")
            if povbad:
                probs.append(povbad)
            if packbad:
                probs.append("%s——本段配方要求：%s。把其中一镜改成全景/远景："
                             "先给空间和人物在空间里的位置，人可以拍得很小"
                             % (packbad, _PACKS[pack][0]))
            if movebad:
                probs.append("你把 %s 挪了位置或改了姿态（站↔坐），但这一段的剧本"
                             "拍组里**没有**给他们任何移动动作——剧本没让动的人绝对"
                             "不许动：正文每一镜和结尾站位表都让他们保持开场站位表的"
                             "位置和姿态，站着的人不许写「坐在」，一个字都不许改"
                             % "、".join(movebad[:3]))
            user += "\n\n★★【上一版不合格，重写这一段】" + "。".join(probs) + "。"
        else:
            if bad:
                # 【编造的台词直接删掉，不打死整段】用户 2026-08-30 定的原则：
                # qwen 有错直接改正修复。编造的台词本来就不该存在，删掉它
                # 剩下的画面和真台词一句不少；而打死整段＝这一段没片子。
                cp3 = cand.get("prompt") or ""
                for q in bad:
                    # 连引号一起删；删完清理留下的孤立标点
                    cp3 = cp3.replace("「%s」" % q, "")
                cp3 = re.sub(r"[，,]\s*(?=[。\n])", "", cp3)
                cp3 = re.sub(r"([：:])\s*(?=[。\n])", r"\1", cp3)
                cp3 = re.sub(r"。\s*。+", "。", cp3)
                _dbg("编造台词直接删", sid, ep, n_next, cp3,
                     {"删掉的": bad[:3]})
                cand["prompt"] = cp3
                bad = _fabricated_quotes(dlg, cp3, body)
            if bad:
                raise RuntimeError("导演%d次都编造了拍组里没有的台词：%s"
                                   % (_TRIES, "；".join("「%s」" % q for q in bad[:3])))
            if miss:
                # 机械修复：缺的台词若在「台词」汇总字段里（Qwen 顽固地只填汇总
                # 不进镜头块，实测同一句连拒九次），代码替它内联进台词字段前的
                # 最后一个镜头块——那正是说话的拍（2026-08-29）
                cp2 = cand.get("prompt") or ""
                cut = cp2.find("\n台词：")
                if cut < 0:
                    # 汇总栏格式不一定带前导换行（半角冒号、行中出现都见过）——
                    # 定位失败就退到全文末尾注入，别让一句台词逼死整段
                    _m2 = list(re.finditer(r"台词[：:]", cp2))
                    cut = _m2[-1].start() if _m2 else len(cp2)
                # 只要这句台词在产物里**任何地方**出现过（说明导演写了、只是
                # 没内联进镜头块），就机械内联；真没写才打死（2026-08-29 实测：
                # 长台词导演只填汇总栏，旧的严格定位接不住，三轮全灭）
                # 产物里连影子都没有时也照样内联。台词是**必须拍到**的内容，
                # 少一句就是成片少一句话；而"整段不出"是少了一整段，更亏。
                # 落盘留痕，方便回头看导演到底漏了什么（2026-08-30）。
                if not all(_norm_quote(t) in _norm_quote(cp2) for _, t in miss):
                    _dbg("台词整句漏拍·强行内联", sid, ep, n_next, cp2,
                         {"缺的台词": [t for _, t in miss]})
                inject = "".join("\n%s自然开口说：「%s」，口型与语音同步。" % (w, t)
                                 for w, t in miss)
                cand["prompt"] = cp2[:cut] + inject + cp2[cut:]
            if nocam:
                # 机械修复：私改的机位句按「正面背景」首槽对回合法句整句替换；
                # 对不上合法机位的直接打死（2026-08-29 实测 Qwen 往句里塞人名）
                cp2 = cand.get("prompt") or ""
                for t in set(re.findall(r"正面背景是[^\n。]+", cp2)):
                    if t in cam_sents:
                        continue
                    legal = [s for s in cam_sents
                             if _cam_key(s).split("；")[0] == _cam_key(t).split("；")[0]]
                    if len(legal) != 1:
                        # 首槽对不上时改看**左右两槽**：左右是轴线锚点，
                        # 它俩对得上就说明机位没选错，只是正面背景的说法被改了词
                        # （"该侧墙面"→"西侧墙面"）——照合法句整句换回来正是要的结果。
                        _lr = lambda s: "；".join(_cam_key(s).split("；")[1:])
                        legal = [s for s in cam_sents if _lr(s) and _lr(s) == _lr(t)]
                    if len(legal) != 1:
                        # 合法句被原样照抄了、后面又缀了补充说明
                        # （"…画面右侧是更衣室方向（机位切换至林野侧前方）"）——
                        # 前缀对得上就说明机位没错，砍掉尾巴即可。
                        legal = [s for s in cam_sents
                                 if _cam_key(t).startswith(_cam_key(s))]
                    if len(legal) != 1:
                        # 最后一招：**吸附到最像的合法机位**。导演常写一个合理
                        # 但不在机位库里的机位——同样几件物体换个朝向摆
                        # （2026-08-30 实测：更衣室/垃圾桶/破碎镜子三件都在，
                        # 只是正面和画右对调）。按"这条合法句提到的物件有几件
                        # 出现在导演写的句子里"打分，最高分唯一才吸附；
                        # 打平说明分不清朝向，宁可打死也不瞎钉几何。
                        def _score(s):
                            objs = [x for x in re.split(r"[；、]", re.sub(
                                r"(正面背景是|画面左侧是|画面右侧是)", "", s)) if x]
                            return sum(1 for o in objs if o and o in t)
                        _sc = sorted(((_score(s), s) for s in cam_sents),
                                     key=lambda x: -x[0])
                        if _sc and _sc[0][0] >= 1 and (len(_sc) == 1
                                                       or _sc[0][0] > _sc[1][0]):
                            legal = [_sc[0][1]]
                    if len(legal) != 1:
                        # 【认不准就取第一条，不打死整段】原来这里"宁可打死也不
                        # 瞎钉几何"。按用户 2026-08-30 的验收口径反过来了：
                        # 背景朝向错属于"对画面稳定性影响不大"的小问题，
                        # 而少一段视频是大问题——机位库第一条是本场的主机位，
                        # 钉它比整段不出强。
                        _dbg("机位句认不准，取第一条", sid, ep, n_next, cp2,
                             {"私改的句子": t, "合法机位句": cam_sents})
                        if not cam_sents:
                            raise RuntimeError("导演%d次都没照抄机位背景句，"
                                               "且机位库是空的：%s"
                                               % (_TRIES, t[:50]))
                        legal = [cam_sents[0]]
                    cp2 = cp2.replace(t, legal[0])
                cand["prompt"] = cp2
            if overmove:
                _dbg("运镜超限", sid, ep, n_next, cand.get("prompt") or "",
                     {"pack": pack, "budget": _budget, "overmove": overmove,
                      "有台词": bool(dlg)})
                # 机械修复：把带运镜的那一句改写成固定机位。删词会写出残句
                # （"镜头缓推至她的脸"→"镜头至她的脸"），整段重写又贵又常连内容一起改坏，
                # 所以只重写那一句、画面内容不动。改不干净就不采纳，照常打死。
                # 文戏零运镜是用户 2026-08-29 的定案，三轮改不掉是常态，不能因此丢掉整段。
                _fixed = _static_ize(cand.get("prompt") or "", _budget, _figw)
                if _fixed:
                    cand["prompt"] = _fixed
                    overmove = None
                else:
                    _dbg("运镜修复失败", sid, ep, n_next, cand.get("prompt") or "",
                         {"budget": _budget, "overmove": overmove})
            if notable and prev_table and not re.search(_MOVE_VERB, sl["shots"]):
                # 站位表整行漏了。**剧本这一段没有任何移动动作**时，上一段的表
                # 就是正确答案（接力语义：没拍出移动就不许变位置），直接接过来。
                # 有移动动作时不敢猜，照常打死。
                cand["prompt"] = ((cand.get("prompt") or "").rstrip()
                                  + "\n站位表：" + prev_table)
                notable = None
            # H3 模板格式收尾：补 [Chinese]、补漏掉的 <Subject N>、补段尾声明。
            # 这三样写在指令词里模型照漏（2026-09-01 实测），代码补掉。
            cand["prompt"] = authoring.fix_h3_format(cand.get("prompt") or "")
            if notrans:
                _dbg("过渡拍不合格", sid, ep, n_next, cand.get("prompt") or "",
                     {"notrans": notrans, "pack": pack})
                _t2 = _fix_transition(cand.get("prompt") or "", dlg)
                if _t2:
                    cand["prompt"] = _t2
                    notrans = None
            # 其余 T0 校验（打回级）三轮不过就拒绝出段——此前这里静默放行，
            # 私货机位句、瞬移、说话人正脸过渡拍全从这个口子漏进成片
            hard = ["%s=%s" % (nm, str(v)[:60])
                    for nm, v in (("换档接", samecut), ("左右锚", lrbad),
                                  ("运镜超限", overmove), ("过渡拍", notrans),
                                  ("站位表缺失", notable), ("瞬移未拍移动", movebad))
                    if v]
            if hard:
                _dbg("T0硬失败", sid, ep, n_next, cand.get("prompt") or "",
                     {"hard": hard, "pack": pack, "有台词": bool(dlg)})
                raise RuntimeError("导演%d次都不过 T0 校验（%s），拒绝出段"
                                   % (_TRIES, "；".join(hard)))
            cand["seconds"] = float(sl["seconds"])   # 只剩占位小疵：时长按切段
            seg = cand
    seg = {"no": n_next, "seconds": seg.get("seconds"),
           "prompt": seg.get("prompt") or raw, "video": "", "shot_ids": []}
    if not (tl.get("scenes")):
        tl = {"scenes": [{"no": 1, "title": "导演分镜", "location": "", "time_of_day": "",
                          "interior": "", "beats": [], "shots": [], "warn": "",
                          "state_block": "", "segments": []}]}
    tl["scenes"][0].setdefault("segments", []).append(seg)
    saga_core.save_ep_timeline(sid, ep, tl)
    return seg, raw


def seg_location(sid, prompt):
    """从导演段文字推一个稳定的场景标识：优先匹配已有场景卡名（同场景才判得出"接续"），
    匹配不到就取『人物站位/开场状态』行的头一个地点短语。归一化掉引号，好对上带引号的卡名。"""
    from . import asset_core
    p = str(prompt or "")

    def _n(x):
        return re.sub(r"[「」“”\"'‘’·、，,。\s]", "", str(x or ""))

    pn = _n(p)
    for s in asset_core.list_assets(sid, "scenes"):
        nm = str(s.get("name") or "")
        core = _n(nm)
        if core and (pn.find(core) >= 0 or (len(core) >= 5 and pn.find(core[:5]) >= 0)):
            return nm
    m = re.search(r"(?:人物站位|开场状态)[：:]\s*([^，,。\n｜|；;（(]+)", p)
    return m.group(1).strip()[:20] if m else ""


def save_to_timeline(sid, ep, segs):
    """把导演分好的段存进这一话的 timeline（一段一段，带 prompt）。"""
    from . import saga_core
    tl = {"scenes": [{"no": 1, "title": "导演分镜", "location": "", "time_of_day": "",
                      "interior": "", "beats": [], "shots": [], "warn": "", "state_block": "",
                      "segments": [{"no": g["no"], "seconds": g.get("seconds"),
                                    "prompt": g["prompt"], "video": "", "shot_ids": []}
                                   for g in segs]}]}
    # 重写提示词不等于删片子：同段号已出的视频/结束帧/种子原样搬过来
    # （2026-09-02 用户实测：点一次「全部生成提示词」，分镜里的视频全没了）
    saga_core.merge_segment_media(sid, ep, tl)
    saga_core.save_ep_timeline(sid, ep, tl)
    return tl
