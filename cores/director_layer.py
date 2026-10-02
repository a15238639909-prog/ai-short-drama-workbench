# -*- coding: utf-8 -*-
"""director_layer.py — 分镜内核 v2：AI 视频导演逻辑。

替代 scene_layer 的"逐拍拆镜"。老内核把 2302 字拆成 143 镜 / 523 秒，
每 15 秒塞 4~5 镜——每切一镜人脸重抽一次，节奏像 PPT，
还把"皂角味""心跳漏拍"这类不可见信息硬转成了动作。

新内核五步：
    1 场景状态块（机械编译，定死的事实：地点/时间/光线/固定物/每人服装/道具）
    2 节拍表就是事件表（骨架已按题材引擎排好）
    3 导演压缩：整场一次调用，一镜 6~12 秒装一个连续表演，120 秒 ≈ 10~15 镜
    4 严格字段：chars/framing/camera/start_state/action/end_state/dialogue/sound
    5 连续性与卫生检查（机械）：不可见信息、生理学特写、结论词、地点漂移、
      道具增殖、离场复现、时长偏差——违规带原因重来
"""
import json
import re
from pathlib import Path

INS_DIR = Path(__file__).resolve().parent.parent / "presets" / "instructions"

# 不可见信息 / 生理学特写 / 结论词——出现在 action/framing 里就是违规。
# 「特写要有授权」：题材引擎点名的身体局部（裙摆/腿/臀…）不在禁区。
BANNED_INVISIBLE = ("气味", "皂角味", "体温", "心跳", "脉搏", "闻到", "深吸一口气",
                    "内心", "回忆起", "想起", "感受到")
BANNED_PHYSIO = ("瞳孔", "虹膜", "睫毛", "毛孔", "皮下", "快速眼动", "REM")
BANNED_VERDICT = ("诱人", "唯美", "极具电影感", "氛围感", "震撼", "完美")

MIN_SHOT, MAX_SHOT = 4.0, 15.0


def scene_state_block(scene_id, location, time_light, scene_card, char_cards, props):
    """第 1 步：场景状态块。全部来自卡片和骨架，机械拼装，模型无权改。"""
    lines = ["SCENE_%s" % scene_id, "地点：%s（整场锁定，剧情没写离开就不许变）" % location,
             "时间与光线：%s" % time_light]
    if scene_card:
        marks = scene_card.get("landmarks") or scene_card.get("fixed_landmarks") or ""
        if marks:
            lines.append("固定物体：%s" % marks)
    for c in char_cards or []:
        lines.append("%s：%s" % (c.get("name"), c.get("clothing") or "服装照人物卡"))
    if props:
        lines.append("道具：%s（数量不许变，不许换手）" % "、".join(props))
    return "\n".join(lines)


def _lint(shots, chars_allowed, seconds_target):
    """第 5 步：机械检查。返回违规清单（空 = 通过）。"""
    why = []
    total = 0.0
    for i, s in enumerate(shots):
        sid = s.get("shot_id") or ("第%d镜" % (i + 1))
        blob = " ".join(str(s.get(k) or "") for k in
                        ("framing", "camera", "action", "start_state", "end_state"))
        for w in BANNED_INVISIBLE:
            if w in blob:
                why.append("%s 出现不可见信息「%s」——心理/气味/触感不拍，用表情姿态演" % (sid, w))
        for w in BANNED_PHYSIO:
            if w in blob:
                why.append("%s 出现生理学特写「%s」——反应用脸和姿态演" % (sid, w))
        for w in BANNED_VERDICT:
            if w in blob:
                why.append("%s 出现结论词「%s」——写出具体画面，不写结论" % (sid, w))
        if re.search(r"[两二2]\s*辆", blob):
            why.append("%s 道具数量变了（出现「两辆」）" % sid)
        extra = [n for n in re.findall(r"[一-鿿]{2,3}", str(s.get("chars") or ""))
                 if n not in (chars_allowed or []) and n in blob]
        for k in ("start_state", "end_state", "action"):
            if not str(s.get(k) or "").strip():
                why.append("%s 缺 %s" % (sid, k))
        try:
            sec = float(str(s.get("seconds") or 0))
        except Exception:
            sec = 0
        if not (MIN_SHOT <= sec <= MAX_SHOT):
            why.append("%s 时长 %s 秒，要求 %d~%d 秒的连续表演" % (sid, s.get("seconds"), MIN_SHOT, MAX_SHOT))
        total += sec
    if seconds_target and total < seconds_target * 0.8:
        why.append("总时长 %.0f 秒，目标 %s 秒——拉长镜头或补连续表演，不要加碎镜头" % (total, seconds_target))
    if seconds_target and len(shots) > int(seconds_target / 6) + 2:
        why.append("镜头太碎：%d 镜。目标是每镜 6~12 秒装一个连续表演" % len(shots))
    return why


def direct_scene(skeleton_scene, scene_card, char_cards, engine_grammar="", call_model=None,
                 scene_id="01"):
    """第 2~4 步：整场一次调用出分镜，过不了检查就带原因重来。"""
    from .model_json import chat_json
    ins = (INS_DIR / "场景_导演分镜.txt").read_text(encoding="utf-8")
    rules = (INS_DIR / "视频导演_总规则.txt").read_text(encoding="utf-8")
    beats = skeleton_scene.get("beats") or []
    chars = skeleton_scene.get("chars") or []
    # 道具从节拍内容里粗提（自行车这类会被模型繁殖的东西必须点名锁数量）
    blob = " ".join(str(b.get("content") or "") for b in beats)
    props = [p for p in ("自行车", "帆布包", "香烟", "手表") if p in blob]
    state = scene_state_block(scene_id, skeleton_scene.get("location") or "",
                              "深秋午后，阴天柔光" if "午后" in blob or True else "",
                              scene_card, char_cards, props)
    user = "\n\n".join([
        "《视频导演总规则》：\n" + rules,
        "场景状态块（定死）：\n" + state,
        ("题材引擎镜头语法：\n" + engine_grammar) if engine_grammar else "",
        # 只写"总时长139秒"+"每镜6~12秒"，模型会把 13 拍并成 7 个短镜头，
        # 加起来才 48 秒，连目标的四成都不到。把镜数下限直接算给它。
        ("总时长：%s 秒。每镜 6~12 秒的连续表演，所以**至少要 %d 个镜头**，"
         "各镜秒数加起来必须接近 %s 秒，不足就把表演拉长，不要靠加碎镜头凑数。"
         % (skeleton_scene.get("seconds"),
            max(1, int(float(skeleton_scene.get("seconds") or 0) / MAX_SHOT + 0.999)),
            skeleton_scene.get("seconds"))),
        "节拍表：\n" + "\n".join("第%s拍（%s，%s秒）：%s" % (b.get("beat"), b.get("phase"),
                                                    b.get("seconds"), b.get("content"))
                              for b in beats)])

    def _need(d):
        sh = d.get("shots")
        if not isinstance(sh, list) or not sh:
            raise ValueError("没给 shots")
        why = _lint(sh, chars, float(skeleton_scene.get("seconds") or 0))
        if why:
            raise ValueError("；".join(why[:6]))

    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=4500, **kw)

    # 【取最好的一版，不要用没校验的那一次】原来是：3 次带校验都不过，就再调一次
    # **完全不校验**的，用它的结果。于是 3 次里最接近的那版（104 秒）被扔掉，
    # 交付的是第 4 次没人管的那版（48 秒）——而 warn 报的还是第 3 次的数字，
    # 数字和交付物对不上，看日志的人会被彻底带偏。
    best, best_n, note = None, None, ""
    for _ in range(3):
        try:
            cand, _ = chat_json(ins, user, call_model, validate=None, tries=2, temperature=0.6)
        except Exception:
            continue
        sh = cand.get("shots") if isinstance(cand, dict) else None
        if not isinstance(sh, list) or not sh:
            continue
        why = _lint(sh, chars, float(skeleton_scene.get("seconds") or 0))
        if not why:
            best, best_n, note = cand, 0, ""
            break
        if best_n is None or len(why) < best_n:
            best, best_n, note = cand, len(why), "；".join(why[:6])
        user = user + "\n\n上一稿的问题（必须改掉）：" + "；".join(why[:6])
    if best is None:
        try:
            best, _ = chat_json(ins, user, call_model, validate=None, tries=1, temperature=0.6)
            note = "三轮都没通过检查，这是未经校验的兜底稿"
        except Exception as e:
            best, note = {"shots": []}, "分镜生成失败：" + str(e)[:150]
    d = best
    shots = d.get("shots") or []
    for i, s in enumerate(shots, 1):
        s["shot_id"] = "S%02d" % i
        s["location"] = skeleton_scene.get("location")
        try:
            s["duration"] = max(MIN_SHOT, min(MAX_SHOT, float(str(s.get("seconds") or 8))))
        except Exception:
            s["duration"] = 8.0
        if note:
            s["warn"] = note
    return {"shots": shots, "state_block": state,
            "total_seconds": sum(s["duration"] for s in shots)}


# ---------- 文戏单元（v3：minimax-drama-prompt 格式） ----------
# 十段实测的教训：一段一镜 → 段内零切换，观感全是拼接缝，"十个片段没有一次关联"。
# 丝滑的切镜只发生在一次生成的内部。所以：
#   单元内部 3~4 镜交给模型自己切；单元之间用「末帧 → 下一单元站位逐字复位」焊死。

FORBIDDEN_TAIL = ("【禁止项】\n\n文字/UI/水印/Logo/角标/可读文字/真实UI\n\n"
                  "【强制声明】\n\n无背景音乐,仅保留环境音与人声和音效;"
                  "画面禁字幕/文字/水印/Logo;禁止可读文字(指画面字幕文字,不含人声台词)")


def _unit_lint(units, total_target, beat_budgets=None):
    why = []
    for i, u in enumerate(units):
        no = u.get("no") or (i + 1)
        try:
            sec = float(u.get("seconds") or 0)
        except Exception:
            sec = 0
        if not (9 <= sec <= 15):
            why.append("单元%s时长 %s，必须 10~15 秒" % (no, u.get("seconds")))
        sh = u.get("shots") or []
        if not (2 <= len(sh) <= 5):
            why.append("单元%s有 %d 镜，应为 2~5 镜（段内切镜是丝滑的来源）" % (no, len(sh)))
        t = 0.0
        for s in sh:
            # t0=0 是合法值——`or -1` 会把 0 当假值吃掉，实测让校验全体误报
            t0 = s.get("t0"); t1 = s.get("t1")
            t0 = float(t0) if t0 is not None else -1.0
            if abs(t0 - t) > 0.51:
                why.append("单元%s时间段断裂：镜头应从 %.0f 秒接续" % (no, t))
                break
            t = float(t1) if t1 is not None else t
        if sh and abs(t - sec) > 0.51:
            why.append("单元%s时间段没铺满：最后到 %.0f 秒，单元 %s 秒" % (no, t, sec))
        blob = (u.get("staging") or "") + " ".join(s.get("text") or "" for s in sh)
        for w in BANNED_PHYSIO + BANNED_INVISIBLE + BANNED_VERDICT:
            if w in blob:
                why.append("单元%s出现禁用词「%s」" % (no, w))
        if not str(u.get("staging") or "").strip():
            why.append("单元%s缺人物站位" % no)
        if not str(u.get("end_state") or "").strip():
            why.append("单元%s缺末帧状态" % no)
    tot = sum(float(u.get("seconds") or 0) for u in units)
    if total_target and abs(tot - total_target) > total_target * 0.2:
        why.append("总时长 %.0f 秒，目标 %s 秒。砍掉凑数单元，不许注水" % (tot, total_target))
    if total_target and len(units) > int(total_target / 12) + 2:
        why.append("单元太多（%d 个）。总时长 %s 秒最多 %d 个单元"
                   % (len(units), total_target, int(total_target / 12) + 1))
    # 空镜灌水检测：实测模型把余韵拉成七个没有人物的单元凑时长
    empty = sum(1 for u in units if not (u.get("chars") or []))
    if empty > 1:
        why.append("有 %d 个无人物的空镜单元。全片最多 1 个空镜单元，"
                   "余韵并进最后一个有人物的单元里" % empty)
    # 顺序检测：实测模型把爆点放开头、结尾再"闪回"重新入画——时间必须只向前走
    beats = [u.get("beat") for u in units]
    if any(b is None for b in beats):
        why.append("每个单元要标 beat（覆盖节拍表第几拍）")
    else:
        bs = [float(b) for b in beats]
        if any(b2 < b1 for b1, b2 in zip(bs, bs[1:])):
            why.append("单元顺序倒流了：beat 序列 %s 必须单调不减，"
                       "按节拍表从第 1 拍顺序推进，禁止闪回" % bs)
        if bs and bs[0] > 1:
            why.append("第一个单元要从节拍表第 1 拍开始，不许直接从爆点讲起")
    blob_all = " ".join((u.get("staging") or "") + " ".join(s.get("text") or "" for s in u.get("shots") or [])
                        for u in units)
    for w in ("回忆", "回溯", "闪回", "平行剪辑", "叠化"):
        if w in blob_all:
            why.append("出现「%s」——时间只向前走，禁止任何闪回和转场把戏" % w)
    for w in ("遗留", "留在长椅", "脱下的"):
        if w in blob_all:
            why.append("出现「%s」——服装永远穿在人物身上，除非节拍表明写脱下" % w)
    # 每拍时长预算：实测模型把 18 秒的余韵摊成 6 个单元 78 秒——
    # 骨架给每拍配了秒数，单元分配必须贴着配额走
    if beat_budgets:
        alloc = {}
        for u in units:
            b = u.get("beat")
            if b is not None:
                alloc[float(b)] = alloc.get(float(b), 0) + float(u.get("seconds") or 0)
        for b, budget in beat_budgets.items():
            got = alloc.get(float(b), 0)
            if budget and got > budget * 1.6:
                why.append("第 %s 拍骨架只配了 %.0f 秒，你给了 %.0f 秒——"
                           "砍掉重复单元，把时长还给别的拍" % (b, budget, got))
            if budget and got < budget * 0.5:
                why.append("第 %s 拍骨架配了 %.0f 秒，你只给了 %.0f 秒——这一拍要展开" % (b, budget, got))
    return why


def direct_scene_units(skeleton_scene, scene_card, char_cards, engine_grammar="",
                       call_model=None):
    """整场 → 文戏单元列表。"""
    from .model_json import chat_json
    ins = (INS_DIR / "场景_文戏单元.txt").read_text(encoding="utf-8")
    rules = (INS_DIR / "视频导演_总规则.txt").read_text(encoding="utf-8")
    beats = skeleton_scene.get("beats") or []
    chars = skeleton_scene.get("chars") or []
    blob = " ".join(str(b.get("content") or "") for b in beats)
    props = [p for p in ("自行车", "帆布包", "手表") if p in blob]
    # 服装锚给足：站位里要写"她穿什么"，从人物卡直接抄
    anchors = "；".join("%s：%s" % (c.get("name"), c.get("clothing") or "")
                       for c in char_cards or [] if c.get("name"))
    state = scene_state_block("U", skeleton_scene.get("location") or "",
                              "深秋午后，阴天柔光", scene_card, char_cards, props)
    user = "\n\n".join([
        "《视频导演总规则》：\n" + rules,
        "场景状态块（定死）：\n" + state,
        "人物服装锚（站位里照抄关键件）：\n" + anchors,
        ("题材引擎镜头语法：\n" + engine_grammar) if engine_grammar else "",
        # 只写"总时长139秒"+"每镜6~12秒"，模型会把 13 拍并成 7 个短镜头，
        # 加起来才 48 秒，连目标的四成都不到。把镜数下限直接算给它。
        ("总时长：%s 秒。每镜 6~12 秒的连续表演，所以**至少要 %d 个镜头**，"
         "各镜秒数加起来必须接近 %s 秒，不足就把表演拉长，不要靠加碎镜头凑数。"
         % (skeleton_scene.get("seconds"),
            max(1, int(float(skeleton_scene.get("seconds") or 0) / MAX_SHOT + 0.999)),
            skeleton_scene.get("seconds"))),
        "节拍表：\n" + "\n".join("第%s拍（%s，%s秒）：%s" % (b.get("beat"), b.get("phase"),
                                                    b.get("seconds"), b.get("content"))
                              for b in beats)])

    budgets = {float(b.get("beat")): float(b.get("seconds") or 0)
               for b in beats if b.get("beat") is not None}

    def _need(d):
        us = d.get("units")
        if not isinstance(us, list) or not us:
            raise ValueError("没给 units")
        why = _unit_lint(us, float(skeleton_scene.get("seconds") or 0), beat_budgets=budgets)
        if why:
            raise ValueError("；".join(why[:6]))

    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=6000, **kw)

    # 【取最好的一版，不要用没校验的那一次】原来是：3 次带校验都不过，就再调一次
    # **完全不校验**的，用它的结果。于是 3 次里最接近的那版（104 秒）被扔掉，
    # 交付的是第 4 次没人管的那版（48 秒）——而 warn 报的还是第 3 次的数字，
    # 数字和交付物对不上，看日志的人会被彻底带偏。
    best, best_n, note = None, None, ""
    for _ in range(3):
        try:
            cand, _ = chat_json(ins, user, call_model, validate=None, tries=2, temperature=0.6)
        except Exception:
            continue
        sh = cand.get("shots") if isinstance(cand, dict) else None
        if not isinstance(sh, list) or not sh:
            continue
        why = _lint(sh, chars, float(skeleton_scene.get("seconds") or 0))
        if not why:
            best, best_n, note = cand, 0, ""
            break
        if best_n is None or len(why) < best_n:
            best, best_n, note = cand, len(why), "；".join(why[:6])
        user = user + "\n\n上一稿的问题（必须改掉）：" + "；".join(why[:6])
    if best is None:
        try:
            best, _ = chat_json(ins, user, call_model, validate=None, tries=1, temperature=0.6)
            note = "三轮都没通过检查，这是未经校验的兜底稿"
        except Exception as e:
            best, note = {"shots": []}, "分镜生成失败：" + str(e)[:150]
    d = best
    units = d.get("units") or []
    for i, u in enumerate(units, 1):
        u["no"] = i
        u["location"] = skeleton_scene.get("location")
        if note:
            u["warn"] = note
    return {"units": units, "state_block": state}


def unit_prompt(unit, style_line="", char_cards=None, scene_name="", has_prev=False):
    """一个单元 → 最终生成提示词（机械拼装，零模型）。

    格式 = minimax-drama-prompt 的分镜时间线 + 用户 H3 打斗工作流成稿的**资产角色绑图**：
    「<主体N> 即 <图N> 中的人物，保持与图中完全一致的身份、脸部结构、发型、服装」——
    用"和图里一模一样"替代几百字外观描述，身份跟着参考图走，文字只锚关键件。
    图N 的顺序必须和实际传给 H3 的参考图顺序一致：出场人物 → 场景 → 上一段末帧。
    """
    on = unit.get("chars") or []
    cards = [c for c in (char_cards or []) if c.get("name") in on]
    bind, idx = [], 0
    for c in cards:
        idx += 1
        sx = str(c.get("sex") or "")
        who = "男子" if "男" in sx else ("女子" if "女" in sx else "人物")
        bind.append("<主体%d> 即 <图%d> 中的%s（%s）：保持与图中完全一致的身份、脸部结构、"
                    "肤色、发型、服装与配饰。关键件：%s。"
                    % (idx, idx, who, c.get("name"), c.get("clothing") or "照图"))
    if scene_name:
        idx += 1
        bind.append("<场景> 即 <图%d>：%s。空间结构、固定物、材质与光线与图保持一致。"
                    % (idx, scene_name))
    if has_prev:
        idx += 1
        bind.append("<图%d> 是上一段结束时的画面，本段从这个状态无缝继续。" % idx)
    lines = ["## P%02d｜%s秒｜%s" % (unit.get("no") or 0,
                                    int(float(unit.get("seconds") or 0)),
                                    unit.get("title") or "")]
    if bind:
        lines += ["", "资产角色："] + bind
    lines += ["", "【分镜】", "",
              "人物站位：" + str(unit.get("staging") or "").strip()
              + (("　" + style_line) if style_line else "")]
    for i, sh in enumerate(unit.get("shots") or [], 1):
        lines.append("")
        lines.append("镜头%d（%d–%d秒，%s）：%s" % (
            i, int(float(sh.get("t0") or 0)), int(float(sh.get("t1") or 0)),
            sh.get("framing") or "中景", str(sh.get("text") or "").strip()))
    lines += ["", FORBIDDEN_TAIL]
    return "\n".join(lines)


def direct_units_by_beat(skeleton_scene, scene_card, char_cards, engine_grammar="",
                         call_model=None):
    """逐拍生成文戏单元（v4）。

    整场一次调用实测三轮都失败在时长分配上：堆叠 36 秒只写 12 秒、
    余韵 18 秒灌成 96 秒——重试带原因也不收敛。
    所以分配不再交给模型：**每拍该出几个单元由预算算死**（budget/12.5 取整），
    模型每次只写一拍的单元，上一单元的 end_state 逐字塞给它当开场站位。
    """
    from .model_json import chat_json
    ins = (INS_DIR / "场景_文戏单元.txt").read_text(encoding="utf-8")
    rules = (INS_DIR / "视频导演_总规则.txt").read_text(encoding="utf-8")
    beats = skeleton_scene.get("beats") or []
    blob = " ".join(str(b.get("content") or "") for b in beats)
    props = [p for p in ("自行车", "帆布包", "手表") if p in blob]
    state = scene_state_block("U", skeleton_scene.get("location") or "",
                              "深秋午后，阴天柔光", scene_card, char_cards, props)
    anchors = "；".join("%s：%s" % (c.get("name"), c.get("clothing") or "")
                       for c in char_cards or [] if c.get("name"))
    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=3200, **kw)

    all_units, prev_end = [], ""
    for b in beats:
        budget = float(b.get("seconds") or 12)
        n = max(1, int(round(budget / 12.5)))
        per = budget / n
        want = [max(10, min(15, round(per))) for _ in range(n)]

        def _need(d, _n=n, _want=want, _beat=b):
            us = d.get("units")
            if not isinstance(us, list) or len(us) != _n:
                raise ValueError("这一拍必须正好 %d 个单元，你给了 %d 个"
                                 % (_n, len(us or [])))
            for u in us:
                u["beat"] = _beat.get("beat")
            why = _unit_lint(us, sum(_want))
            # 单拍批次不套整场级规则：空镜配额、单元数上限、"从第1拍开始"、
            # beat 顺序——这些只对整场成立，套在"只写第2拍"的小批次上永远不过，
            # 实测把三次重试全部烧在误报上
            why = [w for w in why if not any(k in w for k in
                   ("空镜单元", "单元太多", "第 1 拍开始", "单调不减", "beat（覆盖"))]
            if why:
                raise ValueError("；".join(why[:5]))

        user = "\n\n".join(filter(None, [
            "《视频导演总规则》：\n" + rules,
            "场景状态块（定死）：\n" + state,
            "人物服装锚：\n" + anchors,
            ("题材引擎镜头语法：\n" + engine_grammar) if engine_grammar else "",
            ("上一单元末帧（本拍第一个单元的站位必须从这个状态逐字复位）：\n" + prev_end)
            if prev_end else "这是全片第一拍，站位自建。",
            "只写这一拍：第%s拍（%s，共 %s 秒）：%s" % (b.get("beat"), b.get("phase"),
                                                b.get("seconds"), b.get("content")),
            "拆成 %d 个单元，秒数分别为 %s。单元之间也要末帧→站位无缝复位。"
            % (n, "、".join("%d" % w for w in want))]))
        try:
            d, _ = chat_json(ins, user, call_model, validate=_need, tries=3,
                             temperature=0.6)
            note = ""
        except Exception as e:
            d, _ = chat_json(ins, user, call_model, validate=None, tries=1,
                             temperature=0.6)
            note = str(e)[:160]
        got = (d.get("units") or [])[:n]
        for u in got:
            u["beat"] = b.get("beat")
            u["location"] = skeleton_scene.get("location")
            if note:
                u["warn"] = note
        if got:
            prev_end = str(got[-1].get("end_state") or prev_end)
        all_units += got
    for i, u in enumerate(all_units, 1):
        u["no"] = i
    return {"units": all_units, "state_block": state}
