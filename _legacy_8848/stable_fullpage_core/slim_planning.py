# -*- coding: utf-8 -*-
"""精简规划链路（slim planner）：
大白话逐页正文 + 每页简短分镜，分 3—5 批一次生成，
去掉“剧情节点/执行步骤/重型画面计划”中间层。
人物/场景/画风锚点仍由编译层固定注入，保证跨页稳定。
"""
import os, sys, json, time, re
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
from stable_fullpage_core import models, planning


_BATCH = 2


def _estimate_target_pages(contract, content_bias=""):
    """自动页数按本话方向的内容量估算，不再固定 15 页。
    概要比越长、内容倾向越重（成人/黑暗/血腥），页数越多，
    保证关键事件可以按导演节奏拆页，而不是一页带过。"""
    syn = str(contract.confirmedEpisodeSynopsis or contract.rawUserText or "")
    length = max(240, len(syn))
    pages = int(round(length / 90.0))
    bias = str(content_bias or "")
    if "adult" in bias or "dark" in bias or "gore" in bias:
        pages = int(pages * 1.25)
    return max(8, min(24, pages))


def _sys_prompt(contract, ep_no, target, prev_end=None,
                content_bias="", content_bias_custom=""):
    opening = ""
    if int(ep_no or 1) == 1:
        opening = ("8) 第1话前两页必须各有一条开场旁白（narration），让读者知道："
                   "主角是谁、身在何处、当前是什么日子/事件、要做什么、为什么、"
                   "失败了会怎样；旁白具体通俗，禁止抽象词。")
    prev = ""
    if prev_end:
        prev = ("\n上一话结尾（必须从这里自然承接，不得重置状态）：\n%s\n"
                % json.dumps(prev_end, ensure_ascii=False))
    bias = app._content_bias_prompt(content_bias, content_bias_custom)
    _bias_tags = set()
    for _bk in str(content_bias or "").replace("，", "+").split("+"):
        _bk = _bk.strip()
        if _bk == "dark_adult":
            _bias_tags.update(["dark", "adult"])
        elif _bk:
            _bias_tags.add(_bk)
    if "adult" in _bias_tags:
        bias += (
            "成人亲密剧情编排要求：所有角色亲密；逐页呈现"
            "吸引、靠近、主动选择、双方回应、亲密冲动增强、情绪与节奏变化，以及"
            "事后的拥抱、交流或关系变化。两人都要有持续可见的表情、呼吸、目光、"
            "触碰和主动回应；每页产生新的情绪或关系推进，同一动作不重复铺陈，"
            "整体表现为双方共同享受亲密关系，而不是静态姿势陈列。\n"
            "性行为描写必须详细展开，由你作为导演自由发挥，禁止简化成一句话：\n"
            "a) 体位：写清谁在上谁在下、正面/背后/侧躺、双腿如何摆放分开、"
            "手扶住对方哪个部位、身体怎样接触；更换体位时单独占页；\n"
            "b) 性器官与动作：阴茎（龟头、根部、柱身、精液）、阴道（阴道口、"
            "内壁、爱液、红肿）、阴蒂、乳房与乳头、臀部、大腿内侧；"
            "动作写清插入、抽出、抽插、深顶、顶入子宫口、摩擦、含住、舔弄、"
            "吞吐、吸吮、射精的先后过程；\n"
            "c) 每页只推进一个节奏点，但该点的动作细节、身体反应和双方表情呼吸"
            "写足2—4句；连续动作拆到相邻页，让性爱过程占足页数；\n"
            "d) 性行为动作在action里必须用大白话直接写明动作与性器官"
            "（如“张嘴含住阴茎龟头并吞吐”“阴茎插入阴道”“射精”），"
            "禁止用“侍奉、含住、亲昵、亲密”等含糊词代替；"
            "不写比喻与心理小说腔。\n")
    return (
        "你是漫画编剧兼分镜师。根据给定设定，把本话写成逐页漫画。\n"
        "每一页输出两样东西：\n"
        "1) 大白话剧情：visualEvent（本页画面事件）、newInformation（本页新信息）、"
        "decision（本页人物决定）、nextPageBridge（下一页接什么）、"
        "dialogueItems（对白）、narration（旁白）、light（本页光线一句）、"
        "location（具体地点名）。\n"
        "2) 简短分镜 panels：每格只写 shot（全景/中景/近景/特写）、people（本格人物）、"
        "positions（位置一句）、action（动作一句）、expression（表情一句）、"
        "characterStates（本格每个人的当前服装/身体状态）、importantObject（关键物体一句）、"
        "environment（本格局部环境一句）。\n"
        "人物、服装、整体场景、画风由系统固定注入，你**不要**在分镜里重复描写"
        "服装和整体场景，只写这一格的变化和局部环境。\n"
        "要求：\n"
        "1) 页面顺序时间只能向前，同一事件只发生一次；\n"
        "2) 每页只推进一个主要事件；\n"
        "3) 人物只能使用给定人物卡里的角色，不得新增未冻结角色；\n"
        "3.1) people只能填人物卡中的角色，画作、家具、物品、动物等一律不要放入people；\n"
        "4) 对白要自然，旁白负责时间/地点/前情；\n"
        "5) 结尾页必须出现新的悬念或决定；\n"
        "6) 每页 2—4 格，相邻格景别/机位不要全一样；\n"
        "6.1) 镜头要丰富：对话页也不能全是近景/中景正面，至少一格用中景或全景"
        "展示人物与空间的关系；\n"
        "6.2) 每话安排 2—4 个远景/大远景建立镜头，展示开阔环境与人物在环境中的"
        "比例；安排 1—2 个高角度俯拍或低角度仰拍；\n"
        "6.3) 场景要开阔：远景时环境（天空、建筑、地形、空间纵深）占画面主体，"
        "人物占比不要太大；避免整页都是人物特写；\n"
        "6.4) 允许全景、远景、俯视、仰视、低机位、过肩、前景遮挡等机位，按情绪"
        "和剧情选择，不要为了变化乱用倾斜角度；\n"
        "6.5) 导演思维与详细度：本话方向的剧情量必须按节奏充分展开，"
        "关键事件（战斗、亲密、死亡、追逐、仪式）必须拆成多页，每页只推进一个"
        "节奏点（例如亲密：靠近→对视→触碰→脱衣→前戏→进入→抽插→变故→中箭→"
        "反应，每一环单独占页），禁止把多个关键步骤压进同一页；\n"
        "6.6) 每页visualEvent必须写2—4句具体可见动作，写清谁、做什么、怎么做、"
        "先后顺序和身体部位，禁止一句话带过；newInformation写清本页真正新增的事实；"
        "decision写清人物本页做出的选择；每页情绪要有起点到终点的变化；\n"
        "6.7) 如果本话方向包含的事件量在分配页数内演不完，宁可放慢节奏、增加"
        "细节和反应镜头，也不要压缩事件或提前收尾；\n"
        "6.8) 本话方向（概要）中的每一个关键事件都必须逐项出现在剧本里，"
        "一个不能漏、不能提前、不能重复；明确写出的性行为步骤（如口交、插入、"
        "不同体位、高潮、射精、中箭等）必须按顺序逐项占页呈现，禁止跳过其中任何一步；\n"
        "6.9) 每批只写本批页数，禁止在本批最后一页提前发生后续事件或强行收尾；"
        "未演完的场景下一批自然继续，同一事件（如中箭、射精、死亡）在全话只能发生一次；"
        "本批最后一页必须是正在进行的动作或情绪，不允许出现事件结束、场景转移收尾、"
        "或提前进入后续事件；\n"
        "6.10) 本话方向（概要）写到哪个情节点，本话就写到哪个情节点为止；"
        "概要没有明确写出的后续剧情（如大战、逃跑、结局）不得自行加入本话，"
        "结尾页必须落在概要最后提到的事件上；\n"
        "7) 同一人物跨格、跨页保持发型、年龄和当前身体/服装状态连续；每格都在"
        "characterStates中按人物名填写normal_clothed、partially_exposed、nude或具体服装。"
        "同页发生变化时，变化完成后的下一格立即使用新状态；人物脱衣、换装、"
        "重新穿衣或服装损坏时，本页visualStateChanges必须填写characterId、startPage、"
        "resultingState（normal_clothed/partially_exposed/nude/custom_user_state）、"
        "resultOutfit和reason；startPage填写变化后第一个完整沿用新状态的页面，状态从"
        "startPage起持续到下一次明确变化；\n"
        "7.1) 双人接触动作必须在positions里分别写清两人的头、躯干、骨盆、手臂和腿的"
        "相对位置，在action里明确写出动作发起者与承受者的名字；角色身体部位归属和"
        "动作角色必须一致。每格只安排一个能在静止关键帧中读懂的主要接触动作，复杂"
        "连续动作拆到相邻格；\n"
        "%s%s%s\n"
        "只输出JSON：{\"episode\":{\"title\":\"\",\"goal\":\"\",\"summary\":\"\","
        "\"endingHook\":\"\",\"keyEvents\":[{\"subject\":\"\",\"action\":\"\","
        "\"object\":\"\",\"location\":\"\",\"page\":1}],"
        "\"visualStateChanges\":[]},"
        "\"pages\":[{\"pageNo\":1,\"location\":\"\",\"visualEvent\":\"\","
        "\"newInformation\":\"\",\"decision\":\"\",\"nextPageBridge\":\"\","
        "\"dialogueItems\":[{\"speakerId\":\"\",\"kind\":\"dialogue\",\"text\":\"\"}],"
        "\"narration\":\"\",\"light\":\"\",\"visualStateChanges\":[],"
        "\"panels\":[{\"shot\":\"\",\"people\":[],\"positions\":\"\",\"action\":\"\","
        "\"characterStates\":{},"
        "\"expression\":\"\",\"importantObject\":\"\",\"environment\":\"\"}]}]}"
        % (opening, prev, bias))


def _user_prompt(contract, ep_no, target, plan_hint, prev_end=None,
                 first=None, last=None, episode_goal="", completed_pages=None):
    cards = str(contract.confirmedCharacterCards or "")
    syn = str(contract.confirmedEpisodeSynopsis or contract.rawUserText or "")
    direction = str(contract.confirmedEpisodeSynopsis or plan_hint or "").strip()
    world = str(contract.confirmedWorldSetting or "")
    lines = [
        "第%d话，共%d页。" % (int(ep_no or 1), int(target or 0)),
        "人物卡：\n%s" % (cards or "（无）"),
        "本话方向：%s" % (direction or syn or "按人物卡和上一话结尾自然续写"),
    ]
    if world:
        lines.append("世界设定：%s" % world)
    if prev_end:
        lines.append("上一话结尾：%s" % json.dumps(prev_end, ensure_ascii=False))
    if first is not None:
        lines.append("已输出页：第%d—%d页（只能接着往后写，不得重复）" % (first, last))
        if completed_pages:
            lines.append("已完成页面事实（这些事件已经发生，必须从最后状态继续）：\n%s" %
                         json.dumps(completed_pages, ensure_ascii=False))
            lines.append("批次纪律：本批必须严格从上一批最后的状态继续；"
                         "本批最后一页必须是进行中的动作或情绪，禁止收尾、"
                         "禁止提前进入后续事件。")
        lines.append("本话目标：%s" % (episode_goal or "继续推进"))
        lines.append("请只输出第%d—%d页的pages，不要重复输出episode对象。" % (
            last + 1, min(target, last + _BATCH)))
    else:
        lines.append("请输出第1—%d页，并在episode里给出本话标题、目标、摘要和结尾钩子。" %
                     min(target, _BATCH))
    return "\n".join(lines)


def _completed_page_history(pages, last):
    """把已完成批次的事实交给下一批，避免模型只凭页码猜测而重复剧情。"""
    rows = []
    for i in range(1, int(last or 0) + 1):
        p = pages.get(str(i)) or {}
        panels = [x for x in (p.get("panels") or []) if isinstance(x, dict)]
        tail = panels[-1] if panels else {}
        rows.append({
            "pageNo": i,
            "visualEvent": str(p.get("visualEvent") or "")[:140],
            "decision": str(p.get("decision") or "")[:100],
            "nextPageBridge": str(p.get("nextPageBridge") or "")[:100],
            "endPositions": str(tail.get("positions") or "")[:120],
            "endAction": str(tail.get("action") or "")[:100],
            "visualStateChanges": p.get("visualStateChanges") or [],
        })
    return rows


def _collect_visual_state_changes(episode, pages, target):
    """汇总episode与逐页的结构化视觉状态变化，并丢弃无效状态。"""
    allowed = {"normal_clothed", "partially_exposed", "partial_change",
               "nude", "custom_user_state"}
    candidates = list((episode or {}).get("visualStateChanges") or [])
    for i in range(1, int(target or 0) + 1):
        p = pages.get(str(i)) or {}
        for change in (p.get("visualStateChanges") or []):
            if isinstance(change, dict):
                item = dict(change)
                item.setdefault("startPage", i)
                candidates.append(item)
    out, seen = [], set()
    for change in candidates:
        if not isinstance(change, dict):
            continue
        character_id = str(change.get("characterId") or "").strip()
        state = str(change.get("resultingState") or
                    change.get("changeType") or "").strip()
        if not character_id or state not in allowed:
            continue
        if state == "partial_change":
            state = "partially_exposed"
        try:
            start_page = max(1, min(int(target), int(change.get("startPage") or 1)))
        except Exception:
            continue
        item = {
            "characterId": character_id,
            "startPage": start_page,
            "changeType": state,
            "resultingState": state,
            "resultOutfit": str(change.get("resultOutfit") or "").strip(),
            "reason": str(change.get("reason") or "").strip(),
            "source": "episode_plan",
        }
        key = (character_id, start_page, state, item["resultOutfit"])
        if key not in seen:
            seen.add(key)
            out.append(item)
    return sorted(out, key=lambda x: (x["startPage"], x["characterId"]))


def _call(sysmsg, user, temperature=0.45):
    return planning._beat_json_call(sysmsg, user, temperature=temperature)


def _default_panel(text):
    return [{"shot": "中景", "people": [], "positions": "",
             "action": str(text or "")[:80], "expression": "",
             "importantObject": "", "environment": ""}]


def _normalize_page(p, i, contract):
    dialogue = []
    for d in (p.get("dialogueItems") or []):
        if isinstance(d, dict) and str(d.get("text") or "").strip():
            dialogue.append({
                "speakerId": str(d.get("speakerId") or ""),
                "kind": str(d.get("kind") or "dialogue"),
                "text": str(d.get("text") or "").strip()})
    narration = str(p.get("narration") or "").strip()
    location = str(p.get("location") or "").strip()
    visual = str(p.get("visualEvent") or "").strip()
    ni = str(p.get("newInformation") or "").strip()
    dec = str(p.get("decision") or "").strip()
    bridge = str(p.get("nextPageBridge") or "").strip()
    light = str(p.get("light") or "").strip()
    panels = []
    for pa in (p.get("panels") or []):
        if not isinstance(pa, dict):
            continue
        people = [str(x) for x in (pa.get("people") or []) if str(x).strip()]
        character_states = {
            str(k).strip(): str(v).strip()
            for k, v in (pa.get("characterStates") or {}).items()
            if str(k).strip() and str(v).strip()
        } if isinstance(pa.get("characterStates"), dict) else {}
        panels.append({
            "shot": str(pa.get("shot") or "中景"),
            "people": people,
            "characterStates": character_states,
            "positions": str(pa.get("positions") or ""),
            "action": str(pa.get("action") or ""),
            "expression": str(pa.get("expression") or ""),
            "importantObject": str(pa.get("importantObject") or ""),
            "environment": str(pa.get("environment") or "")})
    if not panels:
        panels = _default_panel(visual)
    chars = []
    for pa in panels:
        for n in pa["people"]:
            if n and n not in chars:
                chars.append(n)
    record = {
        "pageNumber": i, "pageNo": i, "beatId": "B1",
        "executionStepId": "B1-S%d" % i,
        "location": location, "characters": chars,
        "causeFromPreviousPage": "",
        "visualEvent": visual, "newInformation": ni,
        "whyItMatters": "", "characterReaction": "",
        "decisionOrConsequence": dec, "relationshipChange": "",
        "nextPageBridge": bridge,
        "dialogueItems": dialogue, "narration": narration,
        "revealedFactIds": [], "stateChanges": {},
        "storyStateDelta": {"newFactAdded": ni, "decisionMade": dec},
        "pageEndState": {
            "location": location, "activeAction": visual[:60],
            "knownInformation": [ni] if ni else [],
            "decision": dec},
    }
    plan = {"layout": "auto", "scene": location or "主场景",
            "light": light or "随剧情", "panels": panels}
    return record, plan


def _filter_known_people(contract, visual_plans, records, target):
    known = set()
    for line in str(contract.confirmedCharacterCards or "").splitlines():
        m = re.match(r"^\s*([^\s\u2014\uff0d\u2013::\uff1a]{1,24})", line)
        if m:
            known.add(models._base_name(m.group(1)))

    def base(n):
        return models._base_name(str(n or ""))

    for i in range(1, target + 1):
        plan = visual_plans.get(str(i)) or {}
        chars = []
        for pa in (plan.get("panels") or []):
            if not isinstance(pa, dict):
                continue
            kept = []
            for n in (pa.get("people") or []):
                n = str(n or "").strip()
                bn = base(n)
                if not n:
                    continue
                if bn in known or any(bn in k or k in bn for k in known):
                    kept.append(n)
            pa["people"] = kept
            for n in kept:
                if n not in chars:
                    chars.append(n)
        rec = records.get(str(i)) or {}
        rec["characters"] = chars
        pes = rec.get("pageEndState") or {}
        if isinstance(pes, dict):
            pes["characters"] = chars


def _text_plan(record):
    units = []
    nar = str(record.get("narration") or "").strip()
    if nar:
        units.append({"type": "narration", "speakerId": "", "visibleText": nar,
                      "text": nar, "panel": 1,
                      "placementPreference": "topLeft", "purpose": "旁白"})
    order = 0
    for d in (record.get("dialogueItems") or []):
        if not isinstance(d, dict) or not str(d.get("text") or "").strip():
            continue
        t = str(d["text"]).strip()
        units.append({"type": "dialogue", "speakerId": str(d.get("speakerId") or ""),
                      "visibleText": t, "text": t,
                      "panel": max(1, min(order + 1, 3)),
                      "placementPreference": ("topRight", "bottomLeft",
                                              "bottomRight")[order % 3],
                      "purpose": "对白"})
        order += 1
    return units


def _run_slim(contract, bible, arc, window, ep_no, target_pages, title,
              plan_hint, prev_end=None, content_bias="",
              content_bias_custom=""):
    target = int(target_pages or 0)
    if target <= 0:
        target = _estimate_target_pages(contract, content_bias)
    target = max(4, min(24, target))
    sysmsg = _sys_prompt(contract, ep_no, target, prev_end,
                         content_bias, content_bias_custom)
    pages = {}
    episode = {}
    first = 0
    last = 0
    goal = ""
    while last < target:
        start = last + 1
        end = min(target, start + _BATCH - 1)
        completed = _completed_page_history(pages, last) if last else None
        user = _user_prompt(contract, ep_no, target, plan_hint, prev_end,
                            first if first else None, last, goal, completed)
        obj = None
        last_err = ""
        for attempt in range(2):
            try:
                obj = _call(sysmsg, user, 0.4 if attempt else 0.5)
            except Exception as e:
                last_err = str(e)
                time.sleep(3)
                continue
            if isinstance(obj, dict) and (obj.get("pages") or obj.get("_items")):
                break
            last_err = "解析失败"
        if not isinstance(obj, dict):
            raise RuntimeError("精简规划第%d—%d页两次失败：%s" % (start, end, last_err))
        if start == 1:
            ep = obj.get("episode") or {}
            if isinstance(ep, dict):
                episode = ep
                goal = str(ep.get("goal") or "")
        batch = obj.get("pages") or obj.get("_items") or []
        got = 0
        for p in batch:
            if not isinstance(p, dict):
                continue
            try:
                pno = int(p.get("pageNo") or p.get("pageNumber") or 0)
            except Exception:
                continue
            if start <= pno <= end:
                pages[str(pno)] = p
                got += 1
        if got == 0:
            raise RuntimeError("精简规划第%d—%d页没有返回页面" % (start, end))
        first = first if first else start
        last = end
    # 归一化
    records = {}
    visual_plans = {}
    stories = []
    for i in range(1, target + 1):
        p = pages.get(str(i))
        if not isinstance(p, dict):
            raise RuntimeError("缺少第%d页" % i)
        rec, plan = _normalize_page(p, i, contract)
        records[str(i)] = rec
        visual_plans[str(i)] = plan
        stories.append(planning.format_page_story_text(rec))
    _filter_known_people(contract, visual_plans, records, target)
    episode.setdefault("episodeNo", int(ep_no or 1))
    episode.setdefault("title", str(title or ""))
    episode.setdefault("goal", str(episode.get("goal") or "") or
                       str(contract.confirmedEpisodeSynopsis or "")[:120])
    episode.setdefault("summary", str(episode.get("summary") or "")[:300])
    episode.setdefault("endingHook", str(episode.get("endingHook") or ""))
    episode.setdefault("targetPages", target)
    episode["visualStateChanges"] = _collect_visual_state_changes(
        episode, pages, target)
    episode["characters"] = episode.get("characters") or list(
        dict.fromkeys([n for r in records.values() for n in (r.get("characters") or [])]))
    start_state = {"location": str((records.get("1") or {}).get("location") or ""),
                   "characters": list(episode.get("characters") or []),
                   "activeAction": str((records.get("1") or {}).get("visualEvent") or "")[:80]}
    last_rec = records.get(str(target)) or {}
    end_state = {
        "location": str(last_rec.get("location") or ""),
        "characters": list(last_rec.get("characters") or []),
        "completedAction": str(last_rec.get("visualEvent") or "")[:120],
        "newInformation": [str(last_rec.get("newInformation") or "")] if str(
            last_rec.get("newInformation") or "").strip() else [],
        "decision": str(last_rec.get("decisionOrConsequence") or ""),
        "nextQuestion": str(episode.get("endingHook") or ""),
    }
    episode["startState"] = episode.get("startState") or start_state
    episode["endState"] = episode.get("endState") or end_state
    episode["keyEvents"] = [e for e in (episode.get("keyEvents") or [])
                            if isinstance(e, dict)]
    text_plans = {}
    for i in range(1, target + 1):
        text_plans[str(i)] = {"pageNumber": i,
                              "textUnits": _text_plan(records[str(i)])}
    visual_states = models.derive_page_visual_states(
        contract, stories, visual_plans, episode)
    actual = planning.derive_episode_actual_end_state(records, episode)
    readability = {}
    for i in range(1, target + 1):
        readability[str(i)] = planning.make_page_readability_plan(
            i, stories[i - 1], visual_plans.get(str(i)) or {},
            text_plans.get(str(i)) or {}, episode, record=records.get(str(i)))
    ledger = planning.build_episode_delivery_ledger(contract, episode)
    missions = {"episodeNo": int(ep_no or 1), "targetPages": target,
                "beats": [], "missions": [
                    {"pageNumber": i, "beatId": "B1",
                     "executionStepId": "B1-S%d" % i,
                     "location": str(records[str(i)].get("location") or ""),
                     "characters": records[str(i)].get("characters") or [],
                     "pageEvent": str(records[str(i)].get("visualEvent") or ""),
                     "newInformation": str(records[str(i)].get("newInformation") or ""),
                     "whyItMatters": "", "characterReaction": "",
                     "decisionOrConsequence": str(
                         records[str(i)].get("decisionOrConsequence") or ""),
                     "nextPageBridge": str(records[str(i)].get("nextPageBridge") or "")}
                    for i in range(1, target + 1)]}
    dup = planning.check_page_duplication(stories)
    if dup:
        app.log("⚠ 精简规划逐页重复提示（不拦截）：%s" % "；".join(dup[:3]))
    location_anchors = models.build_location_anchors(contract, stories, visual_plans)
    review = {"status": "pass", "episodeLoad": "balanced", "causality": "clear",
              "pacing": "natural", "relationshipProgression": "supported",
              "revealTiming": "appropriate", "repetitionRisk": "low",
              "problems": [], "revisionInstructions": []}
    return {
        "brief": {"episodeNo": int(ep_no or 1), "episodeGoal": episode.get("goal"),
                  "summary": episode.get("summary"),
                  "endingHook": episode.get("endingHook"),
                  "reservedForLater": []},
        "beats": [{"beatId": "B1", "cause": "", "event": episode.get("summary"),
                   "newInformation": "", "characterResponse": "",
                   "decisionOrConsequence": "", "relationshipChange": "",
                   "stateBefore": start_state, "stateAfter": end_state,
                   "pageStart": 1, "pageEnd": target,
                   "executionSteps": []}],
        "review": review, "target": target, "episode": episode,
        "ledger": ledger, "missions": missions, "records": records,
        "pages": stories, "readability": readability,
        "visualPlans": visual_plans, "textPlans": text_plans,
        "visualStates": visual_states,
        "actualEndState": actual,
        "locationAnchors": location_anchors,
    }


def _finalize(bible, series, arc, window, volume, ep_no, title, r):
    draft = models.create_episode_draft(
        bible, int(ep_no or 1), str(title), r["target"], r["episode"])
    draft["actualEndState"] = r["actualEndState"]
    draft["lastPageRecord"] = r["records"].get(str(r["target"])) or {}
    bible.seriesPlan = series
    bible.volumePlans = [v for v in (bible.volumePlans or [])
                         if v.get("volumeNo") != volume.get("volumeNo")] + [volume]
    bible.arcPlans = [a for a in (bible.arcPlans or [])
                      if a.get("arcId") != arc.get("arcId")] + [arc]
    bible.episodePlans = [e for e in (window.get("episodes") or [])
                          if int(e.get("episodeNo") or 0) != int(ep_no or 1)] + [draft]
    bible.draftPlans.update({
        "series": series, "volume": volume, "arc": arc, "window": window,
        "episode": r["episode"], "pacing": {
            "episodeNo": int(ep_no or 1), "episodeType": (
                "opening" if int(ep_no or 1) == 1 else "normal"),
            "targetPages": r["target"], "reason": "精简规划自动生成"},
        "actualEndState": r["actualEndState"],
        "editorialReview": r["review"]})
    bible.updatedAt = time.time()
    bible.bibleHash = bible.compute_hash()
    context = {"episodeNo": int(ep_no or 1), "goal": r["episode"].get("goal"),
               "summary": r["episode"].get("summary"),
               "characters": r["episode"].get("characters") or [],
               "endingHook": r["episode"].get("endingHook") or ""}
    return {
        "title": title, "bible": bible.to_dict(),
        "seriesPlan": series, "volumePlan": volume, "arcPlan": arc,
        "episodeWindow": window, "episodePlan": r["episode"],
        "episodeDraft": draft,
        "episodePacingPlan": {
            "episodeNo": int(ep_no or 1),
            "episodeType": "opening" if int(ep_no or 1) == 1 else "normal",
            "targetPages": r["target"], "reason": "精简规划自动生成", "acts": []},
        "episodeContext": context,
        "pageStories": r["pages"],
        "stableFullPageVisualPlans": r["visualPlans"],
        "stablePageTextPlans": r["textPlans"],
        "stablePageReadabilityPlans": r["readability"],
        "stablePageVisualStates": r["visualStates"],
        "episodePageMissionMap": r["missions"],
        "pageStoryRecords": r["records"],
        "episodeActualEndState": r["actualEndState"],
        "episodeDeliveryLedger": r["ledger"],
        "episodeBrief": r["brief"], "episodeBeats": r["beats"],
        "episodeEditorialReview": r["review"],
        "locationAnchors": r["locationAnchors"],
    }


def assemble_story_episode_slim(contract, target_pages=None, title="",
                                project_id="", bible=None, text_only=True,
                                plan_hint="", content_bias="",
                                content_bias_custom=""):
    if bible is None:
        bible = models.build_bible_from_contract(contract, project_id)
    series = planning.build_series_outline(contract, bible)
    aw = planning.build_arc_and_window(contract, bible, series)
    arc, window = aw["arcOutline"], aw["rollingWindow"]
    volume = {"volumeNo": 1, "title": str(series.get("seriesTitle") or title),
              "goal": str(arc.get("stageGoal") or "")}
    r = _run_slim(contract, bible, arc, window, 1, target_pages, title,
                  plan_hint, content_bias=content_bias,
                  content_bias_custom=content_bias_custom)
    return _finalize(bible, series, arc, window, volume, 1, title, r)


def continue_story_episode_slim(contract, bible, target_pages=None, title="",
                                episode_no=None, text_only=True, plan_hint="",
                                content_bias="", content_bias_custom=""):
    if bible is None:
        raise RuntimeError("缺少LongFormStoryBible")
    ep_no = int(episode_no or (bible.lastCompletedEpisode + 1))
    series = bible.seriesPlan or (bible.draftPlans or {}).get("series") or {}
    arc = next(iter(bible.arcPlans or []), None) or \
        (bible.draftPlans or {}).get("arc") or {}
    window = (bible.draftPlans or {}).get("window") or {}
    if not series or not arc or not window.get("episodes"):
        raise RuntimeError("缺少已有上层规划，请先首次创建长篇")
    prev = next((e for e in (bible.episodePlans or [])
                 if isinstance(e, dict) and
                 int(e.get("episodeNo") or 0) == ep_no - 1 and
                 (e.get("actualEndState") or e.get("endState"))), None)
    prev_end = (prev or {}).get("actualEndState") or \
        (prev or {}).get("endState") or {}
    volume = {"volumeNo": 1, "title": str(series.get("seriesTitle") or title),
              "goal": str(arc.get("stageGoal") or "")}
    r = _run_slim(contract, bible, arc, window, ep_no, target_pages, title,
                  plan_hint, prev_end=prev_end, content_bias=content_bias,
                  content_bias_custom=content_bias_custom)
    return _finalize(bible, series, arc, window, volume, ep_no, title, r)
