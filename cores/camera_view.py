"""Subjective camera generation; reference ordering must not change the operator."""
import re

MODES = ("第一视角POV", "自拍手持")


def owner(characters, settings):
    """Explicit operator wins; unnamed eye-level POV belongs to the off-screen viewer."""
    names = [c["name"] for c in characters or [] if c.get("name")]
    explicit = str((settings or {}).get("camera_owner") or "").strip()
    if explicit:
        return explicit                                                     # P461：指定了就是他，不管这段名单里有没有
    if str((settings or {}).get("pov") or "").strip() == "第一视角POV":
        return "我"
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
    offscreen = {who} | {c['name'] for c in chars or [] if c.get('_h3_voice_only')}
    canon = []
    for line in dialogue:
        match = re.match(r'^(.*?)说：<Subject (\d+)> \(S\d+\) says:<d>\[Chinese\](.*?)</d>', line)
        if match:
            canon.append((match[1].strip(), match[3].strip(), line))
    if not canon:
        return body
    words = {text for _, text, _ in canon}
    labels = {speaker for speaker, _, _ in canon}
    slot = object()
    cleaned = []
    for line in body.splitlines():
        if 'says' in line or '<d>' in line:
            # Retain visible action before a speech tag, then replace the
            # utterance by source occurrence rather than a quote-keyed dict.
            prefix = re.split(r'<Subject\s*\d+>', line, maxsplit=1)[0]
            if prefix != line:
                prefix = re.sub(r'(?:' + '|'.join(re.escape(x) for x in labels)
                                + r')说[：:]\s*$', '', prefix).strip()
                if prefix:
                    cleaned.append(prefix)
            match = re.search(r'<d>\s*(?:\[Chinese\])?\s*(.*?)\s*</d>', line)
            if match and match[1].strip() in words:
                cleaned.append(slot)
            continue
        plain = re.match(r'^\s*(?:台词[：:]\s*)?[^：:]{1,12}[：:]\s*(.*?)\s*$', line)
        if plain and plain[1].strip('“”「」"') in words:
            cleaned.append(slot)
        elif line.strip() not in {name + '嘴唇开合。' for name in labels}:
            cleaned.append(line)
    missing = len(canon) - sum(item is slot for item in cleaned)
    end = next((i for i, line in enumerate(cleaned)
                if isinstance(line, str) and '这一段结束时' in line), len(cleaned))
    cleaned[end:end] = [slot] * max(0, missing)
    out, used = [], 0
    for item in cleaned:
        if item is not slot:
            out.append(item)
        elif used < len(canon):
            speaker, _, line = canon[used]
            if speaker not in offscreen:
                out.append(speaker + '嘴唇开合。')
            out.append(line)
            used += 1
    return '\n'.join(out)


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
    source_voices = [c['name'] for c in chars or [] if c.get('_h3_voice_only') and c['name'] != who]
    if source_voices:
        sys += ('\n这些仅为原文声音标签，身份尚未核定：%s。原话逐句保留；'
                '不将标签写成可见人物，不添加其脸、身体或嘴唇动作，不擅自归给其他角色。'
                % '、'.join(source_voices))
    user = ("时长：%s秒\n场景：%s；%s\n人物编号：%s\n上一段结果：%s\n画面稿：\n%s\n台词原文：\n%s"
            % (seconds or 10, scene, space, "；".join("%s=Subject %d" % (c["name"], i)
               for i, c in enumerate(chars, 1)), previous or "开头", shots, "\n".join(dialogue)))
    transport_warning = []
    def ask(text, temperature):
        try:
            return str(call(sys, text, mt=2400, temperature=temperature) or '').strip()
        except (RuntimeError, OSError) as error:
            from .runtime_core import item_failure_requires_stop
            if item_failure_requires_stop(error):
                raise
            transport_warning.append('视点写词服务不可用，沿用本段原文：' + str(error))
            return ''

    body = ask(user, .4)
    def check(text):
        bad = problems(text, mode, who)
        speech = r'<Subject\s*(\d+)>\s*\(S(\d+)\)\s*says:\s*<d>\[Chinese\](.*?)</d>'
        expected = re.findall(speech, '\n'.join(dialogue))
        if re.findall(speech, text) != expected or len(re.findall(r'<d>', text)) != len(expected):
            bad.append('台词编号、原字、出现次数或顺序与本段原文不一致')
        times = [(int(a), int(b)) for a, b in re.findall(r"(?m)^\s*(\d+)\s*[—–-]\s*(\d+)\s*秒[：:]", text)]
        if not times or times[0][0] != 0 or times[-1][1] != int(round(float(seconds or 10))) or any(a >= b for a, b in times) or any(times[i][1] != times[i+1][0] for i in range(len(times)-1)):
            bad.append("时间块不连续或总秒数不符")
        return bad
    bad = check(body)
    if bad and not transport_warning:
        revised = ask(user + "\n当前稿：\n" + body + "\n只修这些问题，保留全部事件及台词：" + "；".join(bad), .2)
        body = revised or body
        bad = check(body)
    if not body.strip():
        # A valid saved source can be transported without another model call.
        # This asserts no invented camera cut, actor, dialogue or completion.
        from .authoring import _pic_parts
        actions = [value for spoken, value in _pic_parts(shots)
                   if not spoken and not value.startswith('──')]
        marker = who + ('主观视角' if mode == '第一视角POV' else '手持自拍')
        body = '0—%s秒：%s，%s\n这一段结束时：保留本段原文末态，实际结果待视频观察确认。' % (
            int(round(float(seconds or 10))), marker, '。'.join(actions) or '观察本段原文所述现场。')
        transport_warning.append('视点写词修复后无可用正文，使用本段原文保守时间轴。')
    body = format_dialogue(body, dialogue, chars, who)                          # P456③：台词行统一成 H3 格式
    bad = check(body)
    generate.last_problems = list(bad) + transport_warning
    if bad and not body.strip():
        raise RuntimeError("视点检查未通过，请编辑提示词：" + "；".join(bad))
    # P456：两次都没过 → 留最后一稿、问题记在段上（原来抛异常会把整批 11 段提示词全丢掉）
    tail = body.split("这一段结束时", 1)[1].lstrip("：: ")[:600] if "这一段结束时" in body else ""
    return body, tail
