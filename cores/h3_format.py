# -*- coding: utf-8 -*-
"""MiniMax H3 官方提示词格式层（Ref2VA 六段式）。

依据用户提供的官方规范 #MINIMAXH3-FULL#：
- N>=3 张参考图 → Ref2VA 六段结构，字段名与固定标记保持英文，正文用中文。
- 首镜不写时间戳；其后每镜以 "[Shot N] At mm:ss.mmm, the camera cuts to " 开头。
- 镜头运动必须写成自然语句，使用官方运动词表（Push In / Pan Right / Tracking Shot …）
  加 amplitude / speed，**不得堆成参数标签**（官方指南 4.3 明确禁止）。
- 说话人用稳定 (S1)/(S2)；身份、动作、语气写在 <d> 外，<d> 内只放语言标签与台词原文。
- 无非画面内音乐时 non_diegetic_music 写 N/A。
"""
from cores.script_text import sanitize_role_codes

__all__ = ["camera_motion", "shot_paragraph", "build_ref2va", "fmt_ts"]

# 官方运动词表：按“显式运镜动作优先于跟随”排序匹配
_MOVES = [
    ("弧", "Arc Shot"),
    ("推", "Push In"),
    ("拉", "Pull Out"),
    ("右摇", "Pan Right"),
    ("左摇", "Pan Left"),
    ("摇右", "Pan Right"),
    ("摇左", "Pan Left"),
    ("上摇", "Tilt Up"),
    ("下摇", "Tilt Down"),
    ("横移", "Truck Right"),
    ("左移", "Truck Left"),
    ("右移", "Truck Right"),
    ("升", "Pedestal Up"),
    ("降", "Pedestal Down"),
    ("变焦", "Zoom In"),
    ("跟随", "Tracking Shot"),
    ("跟拍", "Tracking Shot"),
    ("晃", "Shake Slightly"),
    ("固定", "Static Shot"),
]
_VERTICAL_HINT = ("移下", "移上", "下再上", "上再下")


def camera_motion(path, speed):
    """把中文运镜描述翻成官方英文运动表达。返回 (英文短语, 是否静止)。"""
    p = str(path or "")
    s = str(speed or "")

    term = None
    if any(h in p for h in _VERTICAL_HINT):
        term = "Pedestal Down" if ("移下" in p or "下再上" in p) else "Pedestal Up"
    if term is None:
        for key, official in _MOVES:
            if key in p:
                term = official
                break
    if term is None:
        term = "Static Shot"

    if term == "Static Shot":
        return "Static Shot", True

    amp = ""
    if any(k in p or k in s for k in ("微", "小幅", "略")):
        amp = " with small amplitude"
    elif any(k in p or k in s for k in ("大幅", "大范围")):
        amp = " with large amplitude"

    spd = ""
    if any(k in p or k in s for k in ("慢", "缓")):
        spd = " at slow speed"
    elif any(k in p or k in s for k in ("快", "急")):
        spd = " at fast speed"

    return term + amp + spd, False


def fmt_ts(sec):
    m, s = divmod(float(sec), 60)
    return "%02d:%06.3f" % (int(m), s)


def _clean(v, subj_map):
    return sanitize_role_codes(str(v or "").strip(), subj_map)


def shot_paragraph(shot, idx, start_sec, subj_map, names, dialogue=None, voice_intro=None):
    """生成一个镜头的中文正文段落（官方标记保持英文）。

    subj_map: {"A": "<Subject 1>", "B": "<Subject 2>"}
    names:    {"A": "林舟", "B": "艾莉莎"}
    dialogue: [(说话人名, 表演提示 or None, 台词原文)]
    voice_intro: {人名: "首次开口时的声音身份描述"}，只在该人首次说话时插入。
    """
    g = lambda k: _clean(shot.get(k), subj_map)
    head = "[Shot 1] " if idx == 0 else "[Shot %d] At %s, the camera cuts to " % (idx + 1, fmt_ts(start_sec))

    # 运镜字段两套来源：导演层给 movement_path/speed，分镜层只给 camera_movement。
    # 哪个有内容用哪个。
    move_src = shot.get("movement_path") or shot.get("camera_movement")
    motion, is_static = camera_motion(move_src, shot.get("movement_speed"))
    end_point = g("movement_end_point")
    if end_point in ("无", "None", "-"):
        end_point = ""
    if is_static:
        move_sentence = "镜头保持 %s，构图不变%s。" % (motion, "，%s" % end_point if end_point else "")
    else:
        move_sentence = "镜头以 %s %s%s。" % (motion, _clean(move_src, subj_map),
                                            "，最终停在%s" % end_point if end_point else "")

    # 【空槽绝不落地】模型是输入什么生成什么——字段缺失时把连接词一起省掉，
    # 否则会输出「距主体约，标准 镜头，焦点落在，。」这种带着空洞的句子。
    def sent(pieces, tail="。"):
        ps = [x for x in pieces if x]
        return "，".join(ps) + tail if ps else ""

    parts = [
        sent([g("framing"), g("narrative_purpose")]),
        move_sentence,
        sent([("机位设在" + g("camera_position")) if g("camera_position") else "",
              g("camera_height"), g("camera_angle"),
              ("距主体约" + g("camera_distance")) if g("camera_distance") else "",
              (g("optics") + " 镜头") if g("optics") else "",
              ("焦点落在" + g("focus_subject")) if g("focus_subject") else "",
              g("depth_of_field")]),
        sent([("前景是" + g("foreground")) if g("foreground") else "",
              ("中景是" + g("midground")) if g("midground") else "",
              ("背景是" + g("background")) if g("background") else ""]),
    ]

    a_act, b_act = g("A_action"), g("B_action")
    if a_act:
        parts.append(sent([subj_map["A"] + a_act, g("A_performance")]))
    if b_act and b_act not in ("未入画", "无"):
        parts.append(sent([subj_map["B"] + b_act, g("B_performance")]))
    if not a_act and not b_act:
        # 分镜层的镜头只有一个 action 字段，这是镜头的主体内容，绝不能丢——
        # 之前它根本没进提示词，视频只能靠前中背景猜。
        act = g("action")
        if act:
            parts.append(act if act.endswith(("。", "！", "？")) else act + "。")
    parts.append(sent([("站位上" + g("blocking")) if g("blocking") else "", g("eyeline")]))
    parts.append(sent([("光线为" + g("dominant_light")) if g("dominant_light") else "",
                       ("主体受光来自" + g("subject_light_side")) if g("subject_light_side") else ""]))
    parts = [x for x in parts if x]

    for name, paren, line in (dialogue or []):
        tag = subj_map["A"] if name == names["A"] else subj_map["B"]
        sid = "(S1)" if name == names["A"] else "(S2)"
        intro = ""
        if voice_intro and name in voice_intro:
            intro = "（%s）" % voice_intro.pop(name)
        act = ""
        if paren:
            act = paren.strip("（）()") + "，"
        parts.append(" %s %s%s%s说：<d>[中文]%s</d> " % (tag, intro, act, sid + " ", line))

    if shot.get("audio"):
        parts.append("同期声：%s。" % g("audio"))

    return head + "".join(parts)


def build_ref2va(subject_definitions, summary_en, retention, style_open,
                 shot_paragraphs, soundscape, music="N/A"):
    """按官方顺序拼装六段。music 为空/无音乐时必须是 N/A。"""
    detailed = "detailed_description:\n" + style_open + "\n" + "\n".join(shot_paragraphs)
    return "\n\n".join([
        "subject_definitions:\n" + "\n".join(subject_definitions),
        "summary:\n" + summary_en,
        "retention_analysis:\n" + "\n".join(retention),
        detailed,
        "overall_soundscape: " + soundscape,
        "non_diegetic_music: " + (music or "N/A"),
    ])
