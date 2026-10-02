# -*- coding: utf-8 -*-
"""video_layer.py — 分镜 → 分段视频提示词。

架构和图片那条链完全一致（用户定的）：

    事实层（编译器，定死）  +  指令层（Qwen 写自然语言）  =  最终提示词

事实层负责的东西，一个字都不许 AI 改：
  · subject_definitions ← **直接来自人物/场景的冻结外观段**
    这是整条链最关键的一根线：视频里的人必须和设定图是同一个人，
    所以两边的外观描述必须是同一份文本，而不是各写各的。
  · 时间轴 ← 程序按时长累加算出来，AI 算不准
  · 对白 ← 从剧本正文逐字取，不许复述、不许润色
  · 分段 ← 单段超过 15 秒强制拆，H3 一次最多 15 秒

指令层（Qwen）负责的只有三样，都是需要理解剧情才能写的：
  · summary（英文一句话概括）
  · retention_analysis（这一段靠什么留住观众）
  · overall_soundscape（整体声音环境）

音乐固定 N/A —— 用户明确要求不要音乐。
"""
import re

from . import h3_format

MAX_SECONDS = 15.0          # H3 单段上限
DEFAULT_SHOT_SECONDS = 3.0  # 分镜没给时长时的兜底


# ---------- 事实层：从冻结外观段提炼 subject 定义 ----------

# 设定板专用的话不该进视频：视频里没有"四视图"也没有"纯白背景"
SHEET_ONLY = ("四个视图", "四视图", "宽幅人物设定板", "正面全身", "90度正侧面全身",
              "背面全身", "正脸特写", "纯白背景", "画面中只有这一个人物",
              "无裁切", "平视机位", "留出一段空白", "从头顶到鞋底",
              "空场空间基准图", "建立镜头", "画面中没有任何人物", "没有任何模糊的地方")


def _strip_sheet(text):
    """去掉设定板专用的取景话术，只留下"这个人/这个地方长什么样"。"""
    out = []
    for seg in re.split(r"[，。]", str(text or "")):
        seg = seg.strip()
        if seg and not any(w in seg for w in SHEET_ONLY):
            out.append(seg)
    return "，".join(out)


def _sections(appearance):
    """把编号分段的外观段拆成 {编号: 正文}。"""
    d = {}
    for m in re.finditer(r"(\d)\.[^\s]{2,6}\s(.*?)(?=(?:。\d\.)|$)", appearance or "", re.S):
        d[m.group(1)] = m.group(2).strip().rstrip("。")
    return d


def subject_line(tag, name, appearance):
    """一条 subject_definitions。

    只取外观段里"长什么样"的部分——第1段（画面内容）和第3段（发型穿搭），
    第2段是设定板的取景、第4段是设定板的固定站姿，视频里都用不上。
    """
    return "%s = %s：%s" % (tag, name, _merge13(appearance))


def _merge13(appearance):
    """合并外观段的第1段和第3段，逐句去重。

    发型在两段里都出现（第1段是身份特征、第3段是穿搭），设定板里重复无所谓，
    进了视频 subject 定义就是同一句话说两遍，白占权重。
    """
    s = _sections(appearance)
    seen, out = set(), []
    for part in (_strip_sheet(s.get("1", "")), _strip_sheet(s.get("3", ""))):
        for seg in part.split("，"):
            seg = seg.strip()
            if seg and seg not in seen:
                seen.add(seg)
                out.append(seg)
    return "，".join(out)


def scene_line(tag, name, appearance):
    """场景的 subject 定义：空间本体 + 材质陈设，去掉基准图的取景话术。"""
    return "%s = %s：%s" % (tag, name, _merge13(appearance))


def subject_map(names):
    """人物名 → <Subject N>。顺序稳定，靠传进来的名单决定编号。"""
    return {n: "<Subject %d>" % (i + 1) for i, n in enumerate(names)}


# ---------- 事实层：分段 ----------

MAX_ACTION_SHOT_SECONDS = 10.0   # 没台词的镜头单镜上限

def shot_seconds(shot):
    v = shot.get("duration_sec") or shot.get("duration") or shot.get("seconds")
    try:
        v = float(str(v).rstrip("s秒"))
    except Exception:
        v = 0.0
    v = v if v > 0 else DEFAULT_SHOT_SECONDS
    # 硬兜底：单镜时长不许失控（38 秒段的病根是一个长旁白按字数吃掉几十秒）。
    # 有台词的镜头要念完，放宽到 H3 上限；没台词的动作/旁白镜头压到 10 秒内。
    cap = MAX_SECONDS if str(shot.get("dialogue") or "").strip() else MAX_ACTION_SHOT_SECONDS
    return min(v, cap)


def shot_location(shot):
    return str((shot or {}).get("location") or (shot or {}).get("scene") or "").strip()


def split_segments(shots, limit=MAX_SECONDS):
    """把分镜切成一段一段，每段 ≤15 秒，**而且一段里只有一个地点**。

    地点边界优先于时长。实测踩过的坑：只按时长切，9 个镜头里街头和卧室混着，
    一段 14 秒里塞了街头两镜 + 卧室一镜，H3 生成出来就是画面在街头和卧室之间硬跳。
    H3 一次生成的是一个连续片段，中途换地点它没法处理。

    单个镜头就超过 15 秒的，自己独占一段并标出来——那是分镜本身要改，
    不能靠这里偷偷截断（截断会让画面在中途断掉）。
    """
    segs, cur, acc, loc = [], [], 0.0, None

    def flush():
        if cur:
            segs.append({"shots": list(cur), "seconds": round(acc, 1),
                         "location": loc, "over": False})

    for sh in shots or []:
        d = shot_seconds(sh)
        here = shot_location(sh)
        if d > limit:
            flush(); cur, acc = [], 0.0
            segs.append({"shots": [sh], "seconds": round(d, 1),
                         "location": here, "over": True})
            loc = None
            continue
        # 换地点了 → 必须断段，哪怕这一段才 3 秒
        if cur and here and loc and here != loc:
            flush(); cur, acc = [], 0.0
        if acc + d > limit and cur:
            flush(); cur, acc = [], 0.0
        if not cur:
            loc = here
        cur.append(sh)
        acc += d
    flush()
    return segs


# ---------- 事实层：对白逐字取 ----------

def dialogue_for(shot, script_text, speakers=None):
    """这个镜头要说的台词，逐字来自剧本正文。

    分镜里的 dialogue 字段常常是 AI 复述的版本，用它会和剧本对不上。
    所以拿分镜给的说话人 + 台词去剧本里核对，对不上就以剧本为准。
    """
    from . import script_text as st
    # speakers 必须传：剧本解析靠人物名单区分"人名行"和"动作行"，
    # 不给名单会把所有短行都当成人名。
    lines = st.dialogue_lines(script_text or "", list(speakers or []))
    want = shot.get("dialogue") or shot.get("lines") or []
    if isinstance(want, str):
        want = [want]
    out = []
    for w in want:
        w = str(w).strip()
        if not w:
            continue
        hit = next((l for l in lines if l[2] and (l[2] in w or w in l[2])), None)
        out.append(hit if hit else (shot.get("speaker") or "", None, w))
    return out


# ---------- 拼装 ----------

def compile_segment(seg, idx, subj_map, names, script, summary_en, retention,
                    style_open, soundscape, voice_intro=None):
    """把一段（≤15秒）编译成官方 Ref2VA 六段式。

    时间轴由程序累加，不交给模型——模型算时间是不准的，而 H3 靠时间戳切镜。
    """
    paras, t = [], 0.0
    for i, sh in enumerate(seg["shots"]):
        paras.append(h3_format.shot_paragraph(
            sh, i, t, subj_map, names,
            dialogue=dialogue_for(sh, script, names.values() if hasattr(names, "values") else names),
            voice_intro=voice_intro))
        t += shot_seconds(sh)
    # 站位句放在 detailed_description 最前面：模型先知道谁在哪一侧，再读镜头
    stage = staging_line(subj_map if isinstance(subj_map, dict) else {},
                         list(names.values()) if hasattr(names, "values") else list(names or []),
                         seg["shots"])
    open_text = (stage + " " + (style_open or "")).strip() if stage else style_open
    return h3_format.build_ref2va(
        subject_definitions=seg["subject_definitions"],
        summary_en=summary_en, retention=retention, style_open=open_text,
        shot_paragraphs=paras, soundscape=soundscape, music="N/A")


def reference_pack(seg_index, char_names, scene_name, prev_end_frame=None,
                   same_location_as_prev=False):
    """这一段要带的参考图。

    用户 A/B 实测的结论：每个出场人物 1 张身份图 + 场景 1 张，重复的不要。

    **场景图必须是这一段自己的地点**。实测里四段全传了同一张街头图，
    卧室那两段拿不到卧室的参考，只能靠文字硬扛。

    上一段的结束帧只在**同一个地点、剧情连续**时才加。换了地点还带上一段结束帧，
    等于让模型在新地点里去接旧画面，反而制造混乱。
    """
    pics = [{"kind": "character", "name": n} for n in dict.fromkeys(char_names) if n]
    if scene_name:
        pics.append({"kind": "scene", "name": scene_name})
    if seg_index > 0 and prev_end_frame and same_location_as_prev:
        pics.append({"kind": "previous_end_frame", "path": prev_end_frame})
    return pics

# ---------- 画面位置锚点 ----------

# 行业做法（2026 AI 影视工作流的通行经验）：给每个角色一个**固定的画面位置**和
# **颜色锚点**，并在每段开头写明谁在哪一侧。不写的话，模型会在段与段之间
# 把两个人左右互换，观众看着就像换了机位又没换——这是长片里最常见的穿帮。
#
# 位置怎么定：分镜的 blocking 字段里如果自己说了左右，就听它的；
# 没说就按 subject 编号稳定分配（1 号在左、2 号在右），整段不变。
_LEFT = ("左", "画左", "镜头左")
_RIGHT = ("右", "画右", "镜头右")


def staging_line(subj_map, names, shots):
    """这一段的画面站位。返回一句话，放在 detailed_description 开头。

    只锚定**这一段画面里真出现**的人。不在场的人也写上的话，
    等于告诉模型把他画进去——单人段里凭空多出第二个人就是这么来的。
    """
    listed = [sh.get("chars") for sh in (shots or []) if isinstance((sh or {}).get("chars"), list)]
    if listed:
        # 导演内核的 chars 字段是权威的"画面里有谁"——画外被提到名字的人不算
        onset = {n for lst in listed for n in lst}
        order = [n for n in names if n and n in onset]
    else:
        blob = " ".join(" ".join(str((sh or {}).get(k) or "") for k in
                                 ("subject", "action", "A_action", "B_action",
                                  "foreground", "midground", "background", "blocking"))
                        for sh in (shots or []))
        order = [n for n in names if n and n in blob]
    if len(order) < 2:
        return ""
    txt = "".join(str((sh or {}).get("blocking") or "") for sh in (shots or []))
    a, b = order[0], order[1]
    # 分镜里明说了谁在右，就按它来
    if any(w in txt for w in _RIGHT) and a in txt:
        ia = txt.find(a)
        ib = txt.find(b)
        if 0 <= ib < ia:
            a, b = b, a
    return "画面站位固定：%s 始终在画面左侧，%s 始终在画面右侧，整段不交换。" % (
        subj_map.get(a, "<Subject 1>"), subj_map.get(b, "<Subject 2>"))


def color_anchor(name, appearance):
    """从外观段里抽一个颜色锚点，写进 subject 定义的开头。

    行业经验：每个角色要有一个一眼能认出的颜色，模型靠它在镜头之间对上人。
    """
    import re as _re
    m = _re.search(r"(深灰|浅灰|米白|米色|墨绿|藏青|深蓝|浅蓝|酒红|暗红|土黄|"
                   r"黑色|白色|灰色|棕色|驼色)[^，。]{0,6}(大衣|外套|毛衣|开衫|连衣裙|"
                   r"衬衫|长袍|夹克|风衣|工装|作战服|西装)", str(appearance or ""))
    return m.group(0) if m else ""

# ---------- 用哪个模式生成 ----------

def generation_plan(seg_index, location, prev_location, prev_end_frame,
                    char_names, scene_name):
    """这一段该用哪个 H3 模式、带哪些图。返回 {mode, images, why}。

    A/B 实测结论（同一段提示词、同一张上一段尾帧，只换模式）：

        ref2va（尾帧混在 4 张参考图里）—— 接缝有偏移：构图拉远、头部角度变了
        i2va  （尾帧直接当第 1 帧）    —— 接缝完全无缝，后段人物一致性一样好，
                                          而且快 16%（361s vs 429s）

    少给 3 张参考图质量没降，是因为**首帧本身就锁死了人物长相、服装和场景**，
    再喂身份图是重复信息。行业管这叫 Chain Continuity / 首帧锚定法。

    所以规则很简单：
      · 同地点接着上一段  → i2va，上一段尾帧当首帧
      · 新场景的第一段    → ref2va，需要人物图和场景图把这个空间和这些人立起来
    """
    # 【v2 结论，推翻上面的首帧锚定】那次 A/B 链条短、段内没切镜。
    # 整片实测里链条一长就露馅：i2va 只锁第 1 帧，段内一切镜人脸靠文字重抽，
    # 误差沿链累积——艾莉丝在场2段1和段3明显不是同一张脸。
    # 规则改为：**每一段都是 ref2va，身份图每段重新入包**；
    # 同地点接续时追加上一段结束帧，负责接光线构图，不再独扛身份。
    same = bool(prev_end_frame) and bool(prev_location) and location == prev_location
    pics = reference_pack(seg_index, char_names, scene_name,
                          prev_end_frame=prev_end_frame,
                          same_location_as_prev=same)
    why = ("接续段：身份图+场景图重新入包，上一段结束帧负责衔接光线和构图"
           if (seg_index > 0 and same) else "新场景的第一段，用人物图和场景图把人和空间立起来")
    return {"mode": "ref2va", "images": pics, "why": why}
