"""Subjective camera generation; reference ordering must not change the operator."""
import re

MODES = ("第一视角POV", "自拍手持")


def owner(characters, settings):
    names = [c["name"] for c in characters or [] if c.get("name")]
    explicit = str((settings or {}).get("camera_owner") or "").strip()
    if explicit:
        return explicit                                                     # P461：指定了就是他，不管这段名单里有没有
    return names[0] if names else ""


def instructions(mode, who):
    base = ("你把给定画面稿转换为视频时间轴，不改事件、人物、台词、道具归属。"
            "连续时间块格式为「0—3秒：画面与动作」，从0秒到指定结束秒数。"
            "每块只写能在镜头里看见的动作，保持同一拍摄者，不加入外部摄影师。"
            "给出的台词行逐字放入对应块，不重复、不遗漏。"
            "最后写「这一段结束时：」记录人物、道具、位置及摄影机朝向，供下一段接续。"
            "承接上一段已发生的结果，不重演递物等动作。\n"
            "本段拍摄者固定为：%s。\n" % who)
    if mode == "第一视角POV":
        return base + ("全段是%s眼睛的第一视角。每块注明「%s主观视角」，"
                       "眼睛高度随头部自然移动。对话对象看向我，递物只见我的手及对方。"
                       "可以只看环境，不必每块伸手。拍摄者的脸、背影、全身不入画；"
                       "%s的反应只写成三样：手的动作、镜头的转动或停顿、说话——镜头看不见自己，所以不写%s的表情、眼神、眉头、嘴角。"
                       "台词行原样照抄给你的格式（<Subject N> (SN) says:<d>[Chinese]…</d>），一行一句，放在它发生的那一块末尾。"
                       "不得正反打、过肩、双人同框、外部跟拍或切换到别人眼睛。" % (who, who, who, who))
    return base + ("全段由%s单手持相机自拍，每块注明「%s手持自拍」，"
                   "一臂距离，自己的脸在前景，环境在身后，运动随持机手轻微变化。"
                   "交谈时对方可从旁边入画；递物使用空着的另一只手，持机手继续举相机。"
                   "不切到眼睛POV，不改成外部跟拍、正反打、三脚架固定机位或无人机。" % (who, who))


def problems(body, mode, who):
    blocks = re.split(r"(?m)(?=^\s*\d+\s*[—–-]\s*\d+\s*秒[：:])", body)
    blocks = [b.split("这一段结束时", 1)[0] for b in blocks
              if re.match(r"\s*\d+\s*[—–-]\s*\d+\s*秒[：:]", b)]
    if not blocks:
        return ["没有可检查的时间块"]
    bad = []
    marker = who + ("主观视角" if mode == "第一视角POV" else "手持自拍")
    banned = r"正反打|过肩|外部跟拍|无人机|三脚架|固定机位"
    if mode == "第一视角POV":
        banned += r"|双人同框|" + re.escape(who) + r"(?:的)?(?:脸|面部|背影|全身|嘴角|眼中|眉头|眼神|目光|表情|神态|苦笑|微笑|脸色|额发)"
    else:
        banned += r"|主观视角|眼睛POV"
    for i, block in enumerate(blocks, 1):
        # Ignore quoted dialogue when checking camera language.
        visual = re.sub(r"<d>.*?</d>", "", block, flags=re.S)
        if marker not in visual:
            bad.append("第%d块没有固定拍摄者及视点" % i)
        if re.search(banned, visual):
            bad.append("第%d块有冲突的摄影机描述" % i)
    return bad


def format_dialogue(body, dialogue, chars, who):
    """模型写的「名：台词」「台词：名：台词」→ 给定的 H3 格式行；找不到的台词补到含这句的块末；非拍摄者说话前加「X嘴唇开合。」。"""
    lines = body.split("\n")
    canon = {}
    for d in dialogue:
        m = re.search(r"\[Chinese\](.*?)</d>", d)
        if m:
            canon[re.sub(r"[^一-龥]", "", m.group(1))] = d
    fixed = []
    for l in lines:
        if "says" in l or "<d>" in l:
            m = re.search(r"<d>\s*(?:\[Chinese\])?\s*(.*?)\s*</d>", l)
            nq = re.sub(r"[^一-龥]", "", m.group(1)) if m else re.sub(r"[^一-龥]", "", l.split("says", 1)[-1])
            hit = canon.get(nq) or next((v for k, v in canon.items() if nq and (nq in k or k in nq) and min(len(nq), len(k)) >= 2), "")
            if hit:
                pre = l[:l.find("<Subject")] if "<Subject" in l and l.find("<Subject") > 0 and "说：" in l[:l.find("<Subject")] else ""
                fixed.append(hit)                                              # 歪写的 says 行 → 标准行
            continue                                                            # 台词表里没有的 says 行（模型编的）→ 删
        fixed.append(l)
    lines = fixed
    for d in dialogue:
        m = re.search(r"^(.*?)说：<Subject (\d+)> \(S\d+\) says:<d>\[Chinese\](.*?)</d>", d)
        if not m:
            continue
        spk, quote = m.group(1).strip(), m.group(3).strip()
        nq = re.sub(r"[^一-龥]", "", quote)
        cue = "" if spk == who else ("%s嘴唇开合。" % spk)
        done = False
        for i, l in enumerate(lines):
            if "says:<d>" in l:
                if nq and nq in re.sub(r"[^一-龥]", "", l):
                    done = True
                    break
                continue
            t = l.strip()
            mm = re.match(r"^(?:台词[：:]\s*)?([^：:]{1,8})[：:]\s*[“「\"]?(.+?)[”」\"]?\s*$", t)
            if mm and nq and re.sub(r"[^一-龥]", "", mm.group(2)) == nq:
                lines[i] = (cue + "\n" if cue and not any(cue in x for x in lines[max(0, i - 3):i]) else "") + d
                done = True
                break
        if not done and nq:
            for i, l in enumerate(lines):
                if nq in re.sub(r"[^一-龥]", "", l) and "这一段结束时" not in l:
                    lines[i] = re.sub(re.escape(quote), "", l).rstrip("，,") + ("" if cue and cue in l else cue)
                    lines.insert(i + 1, d)
                    done = True
                    break
        if not done:
            k = next((i for i, l in enumerate(lines) if "这一段结束时" in l), len(lines))
            lines.insert(k, (cue + "\n" if cue else "") + d)
    return "\n".join(lines)


def generate(mode, who, shots, chars, seconds, previous, scene, space, dialogue, call):
    if not who:
        raise ValueError("第一视角/自拍需要先确定拍摄者")
    # 画面稿常用“人物：台词”而不是内部的方括号格式；两种都要传给导演。
    names = [str(c.get("name") or "") for c in chars or []]
    known = set(names)
    if not dialogue:
        for line in str(shots or "").splitlines():
            m = re.match(r"^\s*([^：:]{1,8})\s*[：:]\s*(.+?)\s*$", line)
            if m and m.group(1) in known:
                n = names.index(m.group(1)) + 1
                dialogue.append("%s说：<Subject %d> (S%d) says:<d>[Chinese]%s</d>" %
                                (m.group(1), n, n, m.group(2).strip('“”「」"')))
    sys = instructions(mode, who)
    user = ("时长：%s秒\n场景：%s；%s\n人物编号：%s\n上一段结果：%s\n画面稿：\n%s\n台词原文：\n%s"
            % (seconds or 10, scene, space, "；".join("%s=Subject %d" % (c["name"], i)
               for i, c in enumerate(chars, 1)), previous or "开头", shots, "\n".join(dialogue)))
    body = str(call(sys, user, mt=2400, temperature=0.4) or "").strip()
    def check(text):
        bad = problems(text, mode, who)
        for line in dialogue:
            m = re.search(r"\[Chinese\](.*?)</d>", line)
            quote = (m.group(1) if m else line).strip()
            # 检查台词内容出现一次即可，导演可以换说话标签但不能删句或重复句。
            n = text.count(quote)
            if n != 1:
                bad.append("台词缺失或重复：%s" % quote[:20])
        times = [(int(a), int(b)) for a, b in re.findall(r"(?m)^\s*(\d+)\s*[—–-]\s*(\d+)\s*秒[：:]", text)]
        if not times or times[0][0] != 0 or times[-1][1] != int(round(float(seconds or 10))) or any(a >= b for a, b in times) or any(times[i][1] != times[i+1][0] for i in range(len(times)-1)):
            bad.append("时间块不连续或总秒数不符")
        return bad
    bad = check(body)
    if bad:
        body = str(call(sys, user + "\n当前稿：\n" + body + "\n只修这些问题，保留全部事件及台词：" + "；".join(bad), mt=2400, temperature=0.2) or "").strip()
        bad = check(body)
    body = format_dialogue(body, dialogue, chars, who)                          # P456③：台词行统一成 H3 格式
    bad = check(body)
    generate.last_problems = list(bad)
    if bad and not body.strip():
        raise RuntimeError("视点检查未通过，请编辑提示词：" + "；".join(bad))
    # P456：两次都没过 → 留最后一稿、问题记在段上（原来抛异常会把整批 11 段提示词全丢掉）
    tail = body.split("这一段结束时", 1)[1].lstrip("：: ")[:600] if "这一段结束时" in body else ""
    return body, tail
