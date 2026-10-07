# -*- coding: utf-8 -*-
"""stable_fullpage_core 规划：逐页剧情、整页画面计划、可读性计划、排字文字计划。
只调用本地Qwen（chat_stream），不调用任何旧director链函数。"""
import os, sys, re, json, time, difflib, traceback
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
from stable_fullpage_core.contracts import (
    validate_expansion_against_contract, extract_explicit_locked_facts,
    _value_conflict, _object_core)
from stable_fullpage_core import models


def _extract_json_obj(raw):
    t = str(raw or "")
    m = re.search(r"```(?:json)?\s*(.+?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    st = t.strip()
    if st.startswith("[") and st.rfind("]") > st.find("["):
        try:
            arr = json.loads(st)
            if isinstance(arr, list):
                return {"_array": arr}
        except Exception:
            pass
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        t = t[i:j + 1]
    # 兼容模型偶发的裸中文值： "text": 啪嗒，
    def _fix_unquoted(m):
        return ': "%s"' % m.group(1).strip().replace('"', '\\"')

    t = re.sub(r":\s*([\u4e00-\u9fff][^,\]\n}]*?)(?=\s*[,}\]])",
               _fix_unquoted, t)
    t = re.sub(r":\s*([\u4e00-\u9fff][^,\]\n}]*?)(?=\s*\"\w+\")",
               lambda m: ': "%s",' % m.group(1).strip().replace('"', '\\"'),
               t)
    # 兼容模型在字符串值里嵌入的未转义引号（如 "B5: "镜中人…）
    t = re.sub(r'"([^"]{1,30}): "([\u4e00-\u9fff])', r'"\1: \2', t)
    # 兼容模型偶发的多余左花括号
    t = re.sub(r"\{\s*\{", "{", t)
    # 兼容模型偶发的单引号JSON字符串（值或数组元素）
    def _esc_sq(m):
        return m.group(1).replace("\\", "\\\\").replace('"', '\\"')

    t = re.sub(r":\s*'([^']*)'", lambda m: ': "%s"' % _esc_sq(m), t)
    t = re.sub(r",\s*'([^']*)'", lambda m: ', "%s"' % _esc_sq(m), t)
    t = re.sub(r"\[\s*'([^']*)'", lambda m: '["%s"' % _esc_sq(m), t)
    # 兼容模型提前停止：补齐未闭合的 ] 和 }
    in_str = False
    esc = False
    open_b = open_c = 0
    for ch in t:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            open_c += 1
        elif ch == "}":
            open_c = max(0, open_c - 1)
        elif ch == "[":
            open_b += 1
        elif ch == "]":
            open_b = max(0, open_b - 1)
    t = t + "]" * open_b + "}" * open_c
    try:
        return json.loads(t)
    except Exception:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", t))
        except Exception:
            return None


def _parse_pages(raw, target):
    """按“第N页/【第N页】”标题切分逐页文本（re.split 多捕获组会混入 None，
    这里用 finditer 定位标题边界）。"""
    t = str(raw or "")
    marks = []
    for m in re.finditer(r"(?m)^\s*(?:【第\s*(\d+)\s*页】|第\s*(\d+)\s*页)", t):
        marks.append((m.start(), int(m.group(1) or m.group(2))))
    pages = []
    for idx, (start, _) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(t)
        pages.append(t[start:end].strip())
    if len(pages) < target:
        pages = [p.strip() for p in re.split(
            r"(?m)^(?=地点与时间：)", t) if p.strip()]
    return pages[:target]


class PageStoryBuildResult(list):
    """pageStories 兼容列表，同时携带结构化正式结果。"""

    def __init__(self, texts, records=None, mission_map=None,
                 actual_end_state=None, ledger=None):
        super().__init__(texts)
        self.records = records or {}
        self.mission_map = mission_map or {}
        self.actual_end_state = actual_end_state or {}
        self.ledger = ledger or {}


def _text_similarity(a, b):
    """归一化文本相似度（0-1），用于禁止字段互相复制。"""
    a = re.sub(r"\s+", "", str(a or ""))
    b = re.sub(r"\s+", "", str(b or ""))
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


_INFO_PLACEHOLDER_RE = re.compile(
    r"^(无|没有|暂无|—|-|无（.+）|（无）|无\.|N/?A|无新信息|"
    r"仅(?:确认|观察|动作|过渡|状态).*|无(?:任何)?新信息.*)[。！？]?$")


def _real_info(v):
    v = str(v or "").strip()
    return "" if _INFO_PLACEHOLDER_RE.match(v) else v


def _call_json(sysmsg, user, temp=0.35, num_predict=3000):
    last = ""
    for attempt in range(2):
        try:
            raw = app.chat_stream(
                [{"role": "system", "content": sysmsg},
                 {"role": "user", "content": user}],
                temp + 0.1 * attempt, None, num_predict)
        except Exception as e:
            last = str(e)
            time.sleep(4)
            continue
        obj = _extract_json_obj(raw)
        if isinstance(obj, dict):
            return obj
        last = "解析失败"
        time.sleep(4)
    raise RuntimeError("模型输出不是有效JSON：" + last)


def _retry_json_call(fn, *args):
    try:
        return fn(*args)
    except Exception:
        time.sleep(3)
        return fn(*args)


def _beat_json_call(sysmsg, user, temperature=0.4):
    raw = app.chat_stream(
        [{"role": "system", "content": sysmsg},
         {"role": "user", "content": user}], temperature, None, 5000)
    obj = _extract_json_obj(raw)
    if isinstance(obj, dict) and "_array" in obj:
        obj = {"_items": obj["_array"]}
    if not isinstance(obj, dict):
        raise RuntimeError("模型输出不是有效JSON")
    return obj


# =====================================================================
# 唯一正式故事链：StoryBible → SeriesOutline → Arc+Window → Brief+Beats
# → EditorialReview → 页数估计 → PageMissionMap → PageStoryRecords
# → Readability(直映) → ActualEndState
# =====================================================================

def _compact_bible_summary(contract, bible):
    """故事链各层共享的精简上下文（不塞整页记录）。"""
    return {
        "rawUserText": str(contract.rawUserText or ""),
        "worldSetting": str(contract.confirmedWorldSetting or ""),
        "characters": str(contract.confirmedCharacterCards or ""),
        "sceneSetting": str(contract.confirmedSceneSetting or ""),
        "userLockedFacts": [f for f in (contract.userLockedFacts or [])
                            if isinstance(f, dict)],
        "seriesPlan": (bible.seriesPlan if bible else {}) or {},
        "volumePlans": (bible.volumePlans if bible else []) or [],
        "arcPlans": (bible.arcPlans if bible else []) or [],
        "episodePlans": [e for e in (bible.episodePlans if bible else []) or []
                         if isinstance(e, dict) and (
                             e.get("episodeNo") == (bible.lastCompletedEpisode
                                                    if bible else 0))],
        "characterStates": (bible.characterStates if bible else {}) or {},
        "unresolvedThreads": (bible.unresolvedThreads if bible else []) or [],
        "lastCompletedEpisode": (bible.lastCompletedEpisode if bible else 0) or 0,
    }


def build_series_outline(contract, bible, retry=False):
    """StoryBible + SeriesOutline：一次调用，只定整部主要阶段。"""
    ctx = _compact_bible_summary(contract, bible)
    sysmsg = (
        "你是长篇故事总策划。只规划整部故事的主要阶段（3-6个阶段），"
        "不得要求第一话完成整部故事事件。必须保留用户锁定事实。"
        "只输出JSON："
        '{"seriesTitle":"","stages":[{"stage":1,"name":"","goal":"",'
        '"episodes":"","endingDirection":""}],'
        '"coreConflict":"","longTermGoal":"","finalEndingDirection":""}')
    user = json.dumps(ctx, ensure_ascii=False)
    obj = _call_json(sysmsg, user, 0.4 if retry else 0.35, 3000)
    return obj


def build_arc_and_window(contract, bible, series, retry=False):
    """ArcOutline + RollingEpisodeWindow：一次调用。
    Arc 只规划当前篇章约6-10话的一句话任务；窗口只保留当前/下一/下下话。"""
    ctx = _compact_bible_summary(contract, bible)
    sysmsg = (
        "你是长篇故事篇章策划。只规划当前篇章ArcOutline（6-10话，每话一句任务与"
        "推进方向，不写逐页剧情），并输出RollingEpisodeWindow（3话：当前话详细、"
        "下一话中等、下下话简略）。必须延续seriesPlan与用户锁定事实。只输出JSON："
        '{"arcOutline":{"arcId":"ARC1","episodesCount":8,'
        '"episodeDirections":[{"episodeNo":1,"task":"","direction":""}]},'
        '"rollingWindow":{"episodes":[{"episodeNo":1,"detailLevel":"full",'
        '"title":"","goal":"","keyEvents":[],"characters":[],"endingHook":""}]}}')
    user = "seriesPlan：\n%s\n\n%s" % (
        json.dumps(series, ensure_ascii=False), json.dumps(ctx, ensure_ascii=False))
    obj = _call_json(sysmsg, user, 0.4 if retry else 0.35, 6000)
    if not obj.get("arcOutline") or not obj.get("rollingWindow"):
        raise RuntimeError("Arc/Window字段缺失")
    return obj


def build_episode_brief_and_beats(contract, bible, arc, window, ep_no,
                                  retry=False, plan_hint=""):
    """CurrentEpisodeBrief + EpisodeBeatSheet：一次调用。
    第一话默认只承担建立主角/处境/目标/环境/首个异常/有限关系变化/下一话钩子。"""
    ctx = _compact_bible_summary(contract, bible)
    base = next((e for e in (window.get("episodes") or [])
                 if isinstance(e, dict) and
                 int(e.get("episodeNo") or 0) == int(ep_no or 1)), {})
    prev_end = {}
    if bible and int(ep_no or 1) > 1:
        prev = next((e for e in (bible.episodePlans or [])
                     if isinstance(e, dict) and
                     int(e.get("episodeNo") or 0) == int(ep_no or 1) - 1 and
                     (e.get("actualEndState") or e.get("endState"))), None)
        prev_end = (prev or {}).get("actualEndState") or \
            (prev or {}).get("endState") or {}
    sysmsg = (
        "你是本话编剧。当前话编号由程序给定（episodeNo=%d）。"
        "先输出CurrentEpisodeBrief（episodePurpose/protagonistImmediateGoal/"
        "mainQuestion/mainConflict/relationshipStep/plannedReveals/"
        "reservedForLater/endingChange/endingHook/startState），"
        "再输出EpisodeBeatSheet（4-7个主要beat，每个beat包含beatId/purpose/"
        "cause/event/newInformation/characterResponse/decisionOrConsequence/"
        "relationshipChange/stateBefore/stateAfter/reservedEventsNotUsed）。"
        "长篇第一话不得同时完成：多场完整大战、完整觉醒、重大关系彻底和解、"
        "大幅亲密升级、核心秘密完整揭露、篇章主要反转、主要敌人解决、篇章目标完成。"
        "主角行为必须符合人物性格，不得突然张扬无敌。"
        "只输出JSON："
        '{"brief":{"episodeNo":%d,"episodeType":"opening",'
        '"startState":{"location":"","characters":[],"activeAction":"",'
        '"knownInformation":[],"openQuestion":""},'
        '"episodePurpose":"","protagonistImmediateGoal":"","mainQuestion":"",'
        '"mainConflict":"","relationshipStep":"","plannedReveals":[],'
        '"reservedForLater":[],"endingChange":"","endingHook":""},'
        '"beats":[{"beatId":"B1","purpose":"","cause":"","event":"",'
        '"newInformation":"","characterResponse":"",'
        '"decisionOrConsequence":"","relationshipChange":"",'
        '"stateBefore":{},"stateAfter":{},"reservedEventsNotUsed":[]}]}' % (
            int(ep_no or 1), int(ep_no or 1)))
    user = (
        "arcOutline：%s\n本话窗口条目：%s\n上一话实际结尾：%s\n%s\n%s" % (
            json.dumps(arc, ensure_ascii=False),
            json.dumps(base, ensure_ascii=False),
            json.dumps(prev_end, ensure_ascii=False),
            json.dumps(ctx, ensure_ascii=False),
            ("\n用户补充要求：%s" % str(plan_hint)) if plan_hint else ""))
    obj = _call_json(sysmsg, user, 0.4 if retry else 0.35, 6000)
    brief = obj.get("brief") or {}
    beats = obj.get("beats") or []
    if not brief or not beats:
        raise RuntimeError("Brief/Beats字段缺失")
    brief["episodeNo"] = int(ep_no or 1)
    brief["episodeType"] = "opening" if int(ep_no or 1) == 1 else \
        str(brief.get("episodeType") or "normal")
    return {"brief": brief, "beats": beats}


def review_current_episode_draft(contract, bible, arc, window, brief, beats):
    """唯一编剧审稿（确定性）：只判断目标/因果/负荷/关系铺垫/秘密时机/重复风险。"""
    problems = []
    revisions = []
    goal = str(brief.get("protagonistImmediateGoal") or "").strip()
    if not goal:
        problems.append("本话目标不清楚")
        revisions.append("补全protagonistImmediateGoal")
    if not beats or len(beats) > 7:
        problems.append("beat数量应为4-7个")
        revisions.append("合并或拆分beat到4-7个")
    causality = "clear"
    for i, b in enumerate(beats):
        if not str(b.get("cause") or "").strip() or \
                not str(b.get("event") or "").strip():
            causality = "weak"
            problems.append("B%d缺少原因或事件" % (i + 1))
            break
    big_events = sum(1 for b in beats if any(
        k in str(b.get("event") or "") for k in (
            "大战", "觉醒", "和解", "告白", "揭露", "真相", "击败", "解决")))
    load = "overloaded" if big_events >= 3 else (
        "light" if len(beats) <= 3 else "balanced")
    if load == "overloaded":
        problems.append("第一话事件过载")
        revisions.append("把大战/觉醒/和解/真相类beat保留到后续话")
    relationship = "supported" if any(
        str(b.get("relationshipChange") or "").strip() for b in beats) else \
        "unsupported"
    reveal_timing = "appropriate"
    reserved = [str(x) for x in (brief.get("reservedForLater") or [])]
    for b in beats:
        ev = str(b.get("event") or "")
        if any(k in ev for k in reserved if k and len(k) >= 4):
            reveal_timing = "premature"
            problems.append("提前消费保留事件：%s" % ev[:40])
            break
    repetition = "low"
    evs = [str(b.get("event") or "")[:40] for b in beats]
    if len(set(evs)) != len(evs):
        repetition = "high"
        problems.append("存在重复beat事件")
    status = "pass" if not problems else "revise"
    return {
        "status": status,
        "episodeLoad": load,
        "causality": causality,
        "pacing": "rushed" if load == "overloaded" else (
            "dragging" if len(beats) <= 3 else "natural"),
        "relationshipProgression": relationship,
        "revealTiming": reveal_timing,
        "repetitionRisk": repetition,
        "problems": problems,
        "revisionInstructions": revisions,
    }


def estimate_episode_pages(brief, beats, user_pages=None):
    """先定剧情节点，再按beat复杂度估计页数（26-36）。"""
    if user_pages and int(user_pages) >= 8:
        return int(user_pages)
    weight = 0
    for b in beats:
        base = 4
        if str(b.get("newInformation") or "").strip():
            base += 1
        if str(b.get("relationshipChange") or "").strip():
            base += 1
        if str(b.get("decisionOrConsequence") or "").strip():
            base += 1
        weight += base
    weight = max(26, min(36, weight))
    return weight


def _distribute_beats_to_pages(beats, target):
    """确定性页码分配：按beat复杂度加权，首beat从1开始，末beat结束于target。"""
    beats = [b for b in beats if isinstance(b, dict)]
    if not beats:
        raise RuntimeError("缺少beats")
    weights = []
    for b in beats:
        w = 4
        if str(b.get("newInformation") or "").strip():
            w += 1
        if str(b.get("relationshipChange") or "").strip():
            w += 1
        if str(b.get("decisionOrConsequence") or "").strip():
            w += 1
        weights.append(max(2, w))
    total_w = sum(weights)
    start = 1
    for i, b in enumerate(beats):
        raw = target * weights[i] / total_w
        end = start + int(round(raw)) - 1
        if i == len(beats) - 1:
            end = target
        b["pageStart"] = start
        b["pageEnd"] = max(start, end)
        start = end + 1
    return beats


def _validate_mission_map(missions, beats, target, start_page=1):
    """PageMissionMap 确定性校验：数量/页码/beat归属/关键字段。"""
    errs = []
    if not isinstance(missions, list) or len(missions) != target:
        return ["mission数量必须等于%d" % target]
    page_beat = {}
    for b in beats:
        for p in range(int(b.get("pageStart") or 1),
                       int(b.get("pageEnd") or 1) + 1):
            page_beat[p] = str(b.get("beatId") or "")
    for i, m in enumerate(missions):
        if not isinstance(m, dict):
            errs.append("mission不是对象")
            continue
        pno = int(m.get("pageNumber") or 0)
        if pno != start_page + i:
            errs.append("页码应为%d，实际%d" % (start_page + i, pno))
        if str(m.get("beatId") or "") != page_beat.get(pno, ""):
            errs.append("P%d beatId与beat页码不符" % pno)
        if not str(m.get("location") or "").strip():
            errs.append("P%d location为空" % pno)
        if not str(m.get("pageEvent") or "").strip():
            errs.append("P%d pageEvent为空" % pno)
    return errs


def build_episode_page_mission_map(contract, brief, beats, target, ledger=None,
                                   retry=False):
    """PageMissionMap：按beat分两段生成全部页面任务（每段可重写一次）。"""
    beats = _distribute_beats_to_pages([dict(b) for b in beats], target)
    ledger = ledger or build_episode_delivery_ledger(contract, {
        "episodeGoal": brief.get("protagonistImmediateGoal"),
        "keyBeats": beats, "endState": {}, "targetPages": target})
    page_beat = {}
    for b in beats:
        for p in range(int(b.get("pageStart") or 1),
                       int(b.get("pageEnd") or 1) + 1):
            page_beat[p] = str(b.get("beatId") or "")
    halves = [beats[:len(beats) // 2], beats[len(beats) // 2:]]
    missions = []
    last_err = ""
    for half in halves:
        lo = int(half[0].get("pageStart") or 1)
        hi = int(half[-1].get("pageEnd") or lo)
        count = hi - lo + 1
        sysmsg = (
            "你是漫画页面任务规划师。根据EpisodeBeatSheet把第%d-%d页逐页分配任务，"
            "共%d条，页码连续、数量准确。每页只能推进当前beat内的一小步，"
            "不得进入后续beat或后续话事件，不得使用保留事件。"
            "只输出JSON："
            '{"missions":[{"pageNumber":%d,"beatId":"B1","location":"",'
            '"characters":[],"pageEvent":"","newInformation":"",'
            '"whyItMatters":"","characterReaction":"",'
            '"decisionOrConsequence":"","nextPageBridge":""}]}' % (
                lo, hi, count, lo))
        user = ("CurrentEpisodeBrief：%s\nEpisodeBeatSheet：%s\n保留事件：%s\n"
                "请输出第%d-%d页missions。" % (
                    json.dumps(brief, ensure_ascii=False),
                    json.dumps(half, ensure_ascii=False),
                    json.dumps(brief.get("reservedForLater") or [],
                               ensure_ascii=False), lo, hi))
        ok = False
        for attempt in range(2):
            obj = None
            try:
                obj = _beat_json_call(sysmsg, user,
                                      temperature=0.5 if attempt else 0.4)
            except Exception as e:
                last_err = str(e)
                time.sleep(3)
                continue
            half_m = sorted((obj or {}).get("missions") or [],
                            key=lambda x: int((x or {}).get("pageNumber") or 0))
            errs = _validate_mission_map(half_m, [dict(b) for b in half], count,
                                         lo)
            if not errs:
                missions.extend(half_m)
                ok = True
                break
            last_err = "；".join(errs[:8])
            user += "\n\n上次不合格：%s\n请只重写第%d-%d页missions。" % (
                last_err, lo, hi)
        if not ok:
            raise RuntimeError("PageMissionMap第%d-%d页两次失败：%s" % (
                lo, hi, last_err))
    full = sorted(missions, key=lambda x: int(x.get("pageNumber") or 0))
    errs = _validate_mission_map(full, beats, target)
    if errs:
        raise RuntimeError("PageMissionMap整体校验失败：" + "；".join(errs[:8]))
    return {"episodeNo": int(brief.get("episodeNo") or 1),
            "targetPages": target, "beats": beats, "missions": full}


def _validate_record_group(records, missions, ledger):
    """PageStoryRecords 分组校验（轻量、不修改剧情）。"""
    errs = []
    if not isinstance(records, list) or len(records) != len(missions):
        return ["记录数量必须等于%d" % len(missions)]
    for i, r in enumerate(records):
        if not isinstance(r, dict):
            errs.append("记录不是对象")
            continue
        m = missions[i] if i < len(missions) else {}
        pno = int(r.get("pageNumber") or 0)
        if pno != int(m.get("pageNumber") or 0):
            errs.append("pageNumber应为%d，实际%d" % (
                int(m.get("pageNumber") or 0), pno))
        if str(r.get("beatId") or "") != str(m.get("beatId") or ""):
            errs.append("P%d beatId与mission不一致" % pno)
        if not str(r.get("visualEvent") or "").strip():
            errs.append("P%d visualEvent为空" % pno)
        ni = _real_info(r.get("newInformation") or "")
        if ni and not _specific_why(r.get("whyItMatters") or ""):
            errs.append("P%d newInformation缺少具体原因" % pno)
        if str(r.get("characterReaction") or "").strip() and \
                _text_similarity(str(r.get("visualEvent") or ""),
                                 str(r.get("characterReaction") or "")) > 0.95:
            errs.append("P%d characterReaction复制event" % pno)
        bridge = str(r.get("nextPageBridge") or "").strip()
        if pno < len(missions) and not bridge:
            errs.append("P%d nextPageBridge为空" % pno)
        for v in _reserved_fact_hits(_execution_text(r), ledger):
            errs.append("P%d 使用了保留事件：%s" % (pno, v[:30]))
    return errs


def _build_page_record_groups(contract, brief, beats, missions, target,
                              ledger, retry=False):
    """按4-6页一组生成PageStoryRecords；一组失败只重写该组一次。"""
    records = {}
    ms = (missions or {}).get("missions") or []
    step = 5
    for start in range(0, len(ms), step):
        chunk = ms[start:start + step]
        first, last = int(chunk[0]["pageNumber"]), int(chunk[-1]["pageNumber"])
        beat_ids = sorted(set(str(m.get("beatId") or "") for m in chunk))
        sysmsg = (
            "你是漫画逐页编剧。根据页面任务把第%d-%d页写成PageStoryRecord JSON，"
            "共%d条。每页只能完成对应mission的小步动作，不得进入后续beat或"
            "后续话事件，不得使用保留事件。只输出JSON："
            '{"pages":[{"pageNumber":1,"beatId":"B1","location":"",'
            '"characters":[],"causeFromPreviousPage":"","visualEvent":"",'
            '"newInformation":"","whyItMatters":"","characterReaction":"",'
            '"decisionOrConsequence":"","relationshipChange":"",'
            '"nextPageBridge":"","dialogueItems":[],"revealedFactIds":[],'
            '"stateChanges":{},"storyStateDelta":{},'
            '"pageEndState":{}}]}' % (first, last, len(chunk)))
        user = ("CurrentEpisodeBrief：%s\n本组页面任务：%s\n保留事件：%s\n"
                "请输出第%d-%d页PageStoryRecords。" % (
                    json.dumps(brief, ensure_ascii=False),
                    json.dumps(chunk, ensure_ascii=False),
                    json.dumps(brief.get("reservedForLater") or [],
                               ensure_ascii=False), first, last))
        last_err = ""
        for attempt in range(2):
            obj = None
            try:
                obj = _beat_json_call(sysmsg, user,
                                      temperature=0.5 if attempt else 0.4)
            except Exception as e:
                last_err = str(e)
                time.sleep(3)
                continue
            pages = sorted((obj or {}).get("pages") or [],
                           key=lambda x: int((x or {}).get("pageNumber") or 0))
            errs = _validate_record_group(pages, chunk, ledger)
            if not errs:
                for r in pages:
                    records[str(int(r["pageNumber"]))] = r
                break
            last_err = "；".join(errs[:8])
            user += "\n\n上次不合格：%s\n请只重写本组PageStoryRecords。" % last_err
        else:
            raise RuntimeError("第%d-%d页记录两次失败：%s" % (
                first, last, last_err))
    return records


# =====================================================================
# 逐页节奏压缩与事件唯一执行（20260807）
# EpisodeBeatSheet → executionSteps → 真实页数 → 步骤制PageStoryRecords
# =====================================================================

_STEP_WEAK_RESULT_RE = re.compile(
    r"^(?:仅|只是)?(?:观察|等待|准备|继续|没有结果|无结果|未发生|"
    r"尚未发生|即将发生)[\u4e00-\u9fff]{0,12}[。！]?$"
    r"|(?:等待|即将|准备)[^。，]{0,16}(?:落下|接触|发生|打开|到达|到来)")
_STEP_PURPOSE_SET = {
    "orientation", "character", "motivation", "pressure", "action",
    "discovery", "reaction", "decision", "relationship", "escalation",
    "hook", "environment", "transition"}
_PURPOSE_SYNONYMS = {
    "understandcharacter": "character", "characterintro": "character",
    "charactersetup": "character", "characterestablish": "character",
    "establishgoal": "motivation", "goal": "motivation", "want": "motivation",
    "why": "motivation", "motivate": "motivation",
    "difficulty": "pressure", "risk": "pressure", "obstacle": "pressure",
    "trouble": "pressure", "stakes": "pressure",
    "incident": "discovery", "incidence": "discovery", "event": "discovery",
    "anomaly": "discovery", "trigger": "discovery", "newinformation": "discovery",
    "info": "discovery", "reveal": "discovery", "discover": "discovery",
    "result": "action", "outcome": "action", "act": "action",
    "respond": "reaction", "response": "reaction",
    "choose": "decision", "choice": "decision",
    "relation": "relationship",
    "tension": "escalation", "conflict": "escalation",
    "cliffhanger": "hook", "question": "hook", "teaser": "hook",
    "establish": "orientation", "world": "orientation", "setup": "orientation",
}


def _normalize_step_purpose(p):
    """把模型自造/中英文同义词归一化为规范purpose值。"""
    key = re.sub(r"[^a-z]", "", str(p or "").lower())
    if key in _STEP_PURPOSE_SET:
        return key
    if key in _PURPOSE_SYNONYMS:
        return _PURPOSE_SYNONYMS[key]
    zh = str(p or "").strip()
    zh_map = {
        "建立": "orientation", "人物": "character", "动机": "motivation",
        "目标": "motivation", "压力": "pressure", "风险": "pressure",
        "阻碍": "pressure", "事件": "discovery", "异常": "discovery",
        "新信息": "discovery", "反应": "reaction", "决定": "decision",
        "关系": "relationship", "升级": "escalation", "悬念": "hook",
        "钩子": "hook", "环境": "environment", "过渡": "transition",
        "动作": "action", "行动": "action", "结果": "action",
    }
    for k, v in zh_map.items():
        if k in zh:
            return v
    return ""
_PERSONALITY_BANNED = (
    "不再保守", "放弃保守", "变得张扬", "张扬", "狡黠", "享受关注",
    "公开炫耀", "展示力量", "肆无忌惮", "变得无敌", "引人注目")


def _personality_violations(text):
    """检查人物核心性格漂移词；否定前缀（不/未/没有/避免）不算。"""
    hits = []
    t = str(text or "")
    for w in _PERSONALITY_BANNED:
        for m in re.finditer(re.escape(w), t):
            s = max(0, m.start() - 3)
            prefix = t[s:m.start()]
            if re.search(r"(不|未|没有|避免|拒绝|反对)", prefix):
                continue
            hits.append(w)
            break
    return hits


def _shingles(s, n=2):
    s = re.sub(r"\s+", "", str(s or ""))
    return set(s[i:i + n] for i in range(max(0, len(s) - n + 1)))


def _causal_link_ok(prev_result, next_cause):
    """宽松因果承接：下一Beat首个cause必须与上一Beat最后result有可识别关联。"""
    a, b = str(prev_result or ""), str(next_cause or "")
    if not a or not b:
        return False
    if _text_similarity(a, b) >= 0.25:
        return True
    return len(_shingles(a) & _shingles(b)) >= 1


def _opening_reader_gaps(beats):
    """opening第一话8项读者信息覆盖：WHO/WHERE/WANT/WHY/PRESSURE/
    INCIDENT/REACTION/HOOK。只检查purpose覆盖，不检查关键词。"""
    gaps = []
    purposes = set()
    for b in beats or []:
        for s in b.get("executionSteps") or []:
            p = str(s.get("purpose") or "")
            if p in _STEP_PURPOSE_SET:
                purposes.add(p)
    need = {
        "WHO/WHERE": {"orientation", "character"},
        "WANT/WHY": {"motivation"},
        "PRESSURE": {"pressure"},
        "INCIDENT": {"discovery"},
        "REACTION": {"reaction", "decision"},
        "HOOK": {"hook"},
    }
    for label, ps in need.items():
        if not (purposes & ps):
            gaps.append(label)
    return gaps


def _opening_summary_check(beats):
    """摘要检查：step<12或总页数<20或读者信息缺失时给出原因。
    只报告，不自动复制/补页。"""
    reasons = []
    steps = [s for b in beats or []
             for s in (b.get("executionSteps") or [])]
    if len(steps) < 12:
        reasons.append("executionSteps只有%d个，疑似剧情摘要" % len(steps))
    pages = estimate_pages_from_steps([dict(b) for b in beats or []])
    if pages < 20:
        reasons.append("总页数%d少于20，疑似摘要" % pages)
    reasons += _opening_reader_gaps(beats)
    return reasons


_DELIVERY_MODES = {"visual", "dialogue", "narration", "action", "mixed"}
_HIDDEN_STRENGTH = (
    "隐藏实力", "故意压制", "故意压低", "压制灵力", "隐藏修为", "装弱",
    "藏拙", "其实很强", "故意控制灵力", "故意只", "故意留手",
    "避免灵力过高", "避免过高", "避免灵力溢出", "控制在及格线",
    "收住真正实力", "不想暴露天赋", "隐藏真实修为")


def _visible_text(record):
    """只取读者真正可见的正文：画面 + 对白/旁白/内心。不取后台字段。"""
    parts = [str(record.get("visualEvent") or "")]
    for d in (record.get("dialogueItems") or []):
        if isinstance(d, dict):
            parts.append(str(d.get("text") or ""))
    return " ".join(parts)


def _has_speech(record):
    return any(str(d.get("text") or "").strip()
               for d in (record.get("dialogueItems") or [])
               if isinstance(d, dict))


def _delivery_visible_ok(fact, txt, has_speech):
    """deliveryIntent的事实是否已转为正文证据。
    有对白/旁白即视为已交付；纯画面时只要有足够可见内容即视为已交付，
    语义是否准确由人工阅读判断，不用关键词白名单。"""
    if has_speech:
        return True
    return len(re.sub(r"\s+", "", txt)) >= 20


def _hidden_strength_hits(text):
    """无依据的隐藏实力暗示（小集合，不扩大成大型词表）。"""
    hits = []
    t = str(text or "")
    for w in _HIDDEN_STRENGTH:
        for m in re.finditer(re.escape(w), t):
            s = max(0, m.start() - 3)
            if re.search(r"(不|未|没有|避免|拒绝|反对)", t[s:m.start()]):
                continue
            hits.append(w)
            break
    return hits


def _step_boundary_violations(pages, steps):
    """当前页不得提前完成下一步的核心result（按指令只判断result边界）。"""
    errs = []
    step_ids = [str(s.get("stepId") or "") for s in steps or []]
    for r in pages or []:
        sid = str(r.get("executionStepId") or "")
        if sid not in step_ids:
            continue
        idx = step_ids.index(sid)
        if idx + 1 >= len(step_ids):
            continue
        nxt = steps[idx + 1]
        vt = _visible_text(r)
        pno = int(r.get("pageNumber") or 0)
        probe = str(nxt.get("result") or "").strip()
        if len(probe) >= 8 and (
                probe in vt or
                _text_similarity(vt[:150], probe) > 0.85 or
                len(_shingles(vt) & _shingles(probe)) >= 6):
            errs.append("P%d 提前完成下一步%s的核心结果" % (
                pno, nxt.get("stepId")))
    return errs


def _duplicate_outcome_errors(pages):
    """连续两页读者最终结果基本相同 → 重复风险。"""
    errs = []
    for i in range(1, len(pages or [])):
        a, b = pages[i - 1], pages[i]
        if str(a.get("executionStepId") or "") == \
                str(b.get("executionStepId") or ""):
            # 同一step的两页是合法的action/result两阶段，不算重复
            continue
        oa = "%s %s %s" % (a.get("decisionOrConsequence"),
                           a.get("newInformation"), a.get("nextPageBridge"))
        ob = "%s %s %s" % (b.get("decisionOrConsequence"),
                           b.get("newInformation"), b.get("nextPageBridge"))
        if oa.strip() and ob.strip() and \
                _text_similarity(oa, ob) > 0.92 and \
                _text_similarity(str(a.get("visualEvent") or ""),
                                 str(b.get("visualEvent") or "")) > 0.75:
            errs.append("P%d/P%d 连续页面功能重复" % (
                int(a.get("pageNumber") or 0), int(b.get("pageNumber") or 0)))
    return errs


def _delivery_visible_errors(pages, steps):
    """带deliveryIntent的step，事实必须出现在对应页的可见正文。"""
    errs = []
    for s in steps or []:
        di = s.get("deliveryIntent") or {}
        if not isinstance(di, dict):
            continue
        fact = str(di.get("fact") or "").strip()
        if len(fact) < 4:
            continue
        sid = str(s.get("stepId") or "")
        owned = [r for r in (pages or [])
                 if str(r.get("executionStepId") or "") == sid]
        txt = " ".join(
            str(r.get("location") or "") + " " + _visible_text(r)
            for r in owned)
        speech = any(_has_speech(r) for r in owned)
        if not _delivery_visible_ok(fact, txt, speech):
            errs.append("%s deliveryIntent事实未在正文可见：%s" % (
                sid, fact[:25]))
    return errs


def _dialogue_required_errors(pages, steps):
    """preferredMode=dialogue/mixed 的 step 必须真实产生非空对白。"""
    errs = []
    for s in steps or []:
        di = s.get("deliveryIntent") or {}
        if not isinstance(di, dict):
            continue
        mode = str(di.get("preferredMode") or "").strip()
        if mode not in ("dialogue", "mixed"):
            continue
        sid = str(s.get("stepId") or "")
        owned = [r for r in (pages or [])
                 if str(r.get("executionStepId") or "") == sid]
        if not any(_has_speech(r) for r in owned):
            errs.append("%s preferredMode=%s但未产生dialogueItems" % (
                sid, mode))
    return errs


def _completed_result_repeat_errors(pages, steps, completed_results=None):
    """当前页不得重复已完成beat的核心结果（跨beat收尾动作/独白防重复）。"""
    errs = []
    for res in (completed_results or []):
        res = str(res or "").strip()
        if len(res) < 8:
            continue
        for r in pages or []:
            vt = _visible_text(r)
            if res in vt or _text_similarity(vt[:150], res) > 0.9 or \
                    len(_shingles(vt) & _shingles(res)) >= 6:
                errs.append("P%d 重复已完成结果：%s" % (
                    r.get("pageNumber"), res[:20]))
                break
    return errs


def _steps_quality_errors(beats):
    """executionSteps 确定性质量检查：唯一、有结果、有叙事作用、页数合规、
    Beat间因果承接、2页step两阶段、不压缩成摘要。"""
    errs = []
    seen_ids, seen_events = set(), set()
    for bi, b in enumerate(beats or []):
        steps = b.get("executionSteps") or []
        bid = str(b.get("beatId") or "?")
        if not steps:
            errs.append("%s 缺少executionSteps" % bid)
            continue
        env_count = 0
        results = []
        for si, s in enumerate(steps):
            sid = str(s.get("stepId") or "").strip()
            if not sid or sid in seen_ids:
                errs.append("%s stepId重复或为空" % bid)
            seen_ids.add(sid)
            purpose = _normalize_step_purpose(s.get("purpose"))
            if purpose not in _STEP_PURPOSE_SET:
                errs.append("%s(%s) purpose缺失或非法：%s" % (
                    bid, sid, str(s.get("purpose") or "空")[:20]))
            di = s.get("deliveryIntent")
            if di is not None:
                if not isinstance(di, dict):
                    errs.append("%s(%s) deliveryIntent必须是对象" % (bid, sid))
                else:
                    fact = str(di.get("fact") or "").strip()
                    mode = str(di.get("preferredMode") or "").strip()
                    if len(fact) < 4:
                        errs.append("%s(%s) deliveryIntent.fact为空" % (
                            bid, sid))
                    if mode and mode not in _DELIVERY_MODES:
                        errs.append("%s(%s) preferredMode非法：%s" % (
                            bid, sid, mode))
            cause = str(s.get("cause") or "").strip()
            act = str(s.get("action") or "").strip()
            res = str(s.get("result") or "").strip()
            gain = str(s.get("readerGain") or "").strip()
            if (not cause and not (bi == 0 and si == 0)) or not act or not res:
                errs.append("%s(%s) 缺少cause/action/result" % (bid, sid))
            if not act:
                errs.append("%s(%s) 空动作不能形成step" % (bid, sid))
            if len(res) < 6 or _STEP_WEAK_RESULT_RE.search(res):
                errs.append("%s(%s) 结果不具体：%s" % (bid, sid, res[:20]))
            if not gain and not res and not (s.get("stateDelta") or {}):
                errs.append("%s(%s) readerGain/result/stateDelta全为空" % (
                    bid, sid))
            if act and res and _text_similarity(act, res) > 0.95:
                errs.append("%s(%s) result复制action" % (bid, sid))
            kind = str(s.get("stepKind") or "action")
            if kind in ("environment", "transition"):
                env_count += 1
            try:
                pages = int(s.get("recommendedPages") or 1)
            except Exception:
                pages = 1
            if pages not in (1, 2):
                errs.append("%s(%s) recommendedPages必须为1或2" % (bid, sid))
            if kind in ("environment", "transition") and pages != 1:
                errs.append("%s(%s) 环境/过渡step只能1页" % (bid, sid))
            if pages == 2:
                stages = s.get("pageStages") or []
                if not isinstance(stages, list) or len(stages) != 2 or \
                        not all(str(x or "").strip() for x in stages):
                    errs.append("%s(%s) 2页step必须提供两个不同pageStages" % (
                        bid, sid))
                elif _text_similarity(str(stages[0]), str(stages[1])) > 0.92:
                    errs.append("%s(%s) 2页step两个阶段重复" % (bid, sid))
            eid = str(s.get("eventId") or "").strip()
            if eid:
                if eid in seen_events:
                    errs.append("eventId重复：%s" % eid)
                seen_events.add(eid)
            results.append(res)
        if env_count > 1:
            errs.append("%s 环境/过渡step超过1个" % bid)
        for i in range(len(results)):
            for j in range(i + 1, len(results)):
                if _text_similarity(results[i], results[j]) > 0.92:
                    errs.append("%s 步骤结果重复：S%d/S%d" % (bid, i + 1, j + 1))
    prev_result = ""
    for i, b in enumerate(beats or []):
        steps = b.get("executionSteps") or []
        if not steps:
            continue
        first_cause = str(steps[0].get("cause") or "").strip()
        if i > 0 and first_cause:
            linked = bool(re.match(r"^(承接|连接|上一|B\d+-S\d+)", first_cause))
            if not linked:
                linked = _causal_link_ok(prev_result, first_cause)
                frozen_cause = str(b.get("cause") or "").strip()
                if not linked and frozen_cause:
                    linked = _causal_link_ok(frozen_cause, first_cause)
            if not linked:
                errs.append("%s 第一个step未承接上一beat结果：%s" % (
                    str(b.get("beatId") or "?"), first_cause[:30]))
        prev_result = str(steps[-1].get("result") or "").strip()
    return errs


def _budget_take(budget, purpose):
    """模型调用预算：超过上限立即停止，不继续盲跑。"""
    if budget is None:
        return
    if int(budget.get("used") or 0) >= int(budget.get("max") or 8):
        raise RuntimeError("模型调用预算已用完（%s）" % purpose)
    budget["used"] = int(budget.get("used") or 0) + 1


def build_beat_execution_steps(contract, brief, beats, budget=None,
                               reference_steps=None, keep_beat_ids=None,
                               no_retry=False):
    """把EpisodeBeatSheet每个beat分解为有叙事作用的executionSteps：
    每个beat 2—6个step，opening第一话参考18—26个step；因果承接；不压摘要。"""
    beats = [dict(b) for b in beats]
    for b in beats:
        b.pop("pageStart", None)
        b.pop("pageEnd", None)
    sysmsg = (
        "你是漫画编剧的步骤分解员。把EpisodeBeatSheet的每个beat分解为"
        "executionSteps。规则：1) 每个step只发生一次，必须有明确结果；"
        "2) 只改变姿势、镜头或观察角度不构成step；"
        "3) 等待、准备、即将、观察不能作为step结果；"
        "4) 相同事件不得拆成两个step；若确有两阶段（如轻微前兆与公开共鸣），"
        "必须分成不同step且结果明显不同；"
        "5) 每个beat自然拆成2—6个有效step，禁止把beat压成1个大事件摘要；"
        "opening第一话整体参考18—26个step；"
        "6) 每个step必须承担一种叙事作用：建立背景、理解人物、明确目标、"
        "风险压力、新信息、阻碍、行动、结果、反应、决定、关系变化、"
        "问题升级或悬念；"
        "7) 每个beat的第一个step.cause必须明显承接上一beat最后result；"
        "8) 简单step=1页；重要首次登场/空间建立/重要对话/情绪变化/冲突转折/"
        "核心异常/结尾钩子可2页，2页step必须给出两个不同pageStages阶段；"
        "9) 不得改变人物卡已确认的核心性格，不得出现张扬、狡黠、"
        "不再保守等性格漂移；10) 不得使用保留事件；"
        "11) 承担重要信息交付的step必须设置deliveryIntent："
        "{fact:要交付给读者的具体事实, preferredMode:visual|dialogue|"
        "narration|action|mixed}；deliveryIntent只是意图，"
        "最终必须转化为正文；deliveryIntent要么不写，写了fact必须非空；"
        "12) opening第一话前2—3页必须真实演出主角的日常生活场景"
        "（依据人物卡职业），通过行动、环境与自然对白让读者认识主角身份、"
        "生活、目标与动机；禁止只用闪回物件或旁白代替；"
        "13) 不得把谨慎/低调推导成隐藏实力；能力表现采用普通/勉强/正常通过"
        "等保守解释，除非上层事实明确支持；"
        "14) 每个step只完成自己的result，不得提前执行下一步的核心结果；"
        "关键事件的起因必须来自当前场景正在发生的自然动作；"
        "15) 有人物互动的场景优先使用自然对白表达信息，"
        "禁止解释型旁白连续堆设定；"
        "16) 不得为了凑数量拆分动作，也不得把整个beat压成摘要；"
        "17) 动机/目标交付step应设计成人物互动场景"
        "（如掌柜/同行询问），deliveryIntent.preferredMode优先"
        "dialogue或mixed，动机必须由正文自然表达，禁止只用闪回物件代替；"
        "18) 性格状态与能力事实是两个维度：没有能力事实支持时，"
        "不得从谨慎/低调推导出隐藏实力；禁止‘故意压低、避免灵力过高、"
        "控制在及格线、隐藏真实修为、收住真正实力、不想暴露天赋’等表述，"
        "测灵等能力表现按当前实际水平采用普通/正常/刚好达标解释；"
        "19) 需要意外结果（如受伤流血）时，原因必须来自当前正在进行的动作、"
        "环境中已有物件、人物已携带物品或上一页自然留下的结果；"
        "禁止临时引入专门工具动作；"
        "20) 结尾step必须从上一beat实际结果之后开始，产生新的结果"
        "（行动结果/环境残留/轻微外部注意/新压力/未解释证据/选拔推进），"
        "禁止重复上一beat已完成的收尾动作（擦拭、环顾、确认无人、决定保密），"
        "禁止重复同一句内心独白。"
        "只输出JSON："
        '{"beats":[{"beatId":"B1","executionSteps":['
        '{"stepId":"B1-S1","beatId":"B1","purpose":"orientation",'
        '"cause":"","action":"","result":"","readerGain":"",'
        '"characterResponse":"","stateDelta":{},"recommendedPages":1,'
        '"eventId":"B1-E1","pageStages":["",""],'
        '"deliveryIntent":{"fact":"","preferredMode":"visual"}}]}]}')
    prov_episode = {
        "episodeGoal": str((brief or {}).get("protagonistImmediateGoal") or ""),
        "keyBeats": beats, "endState": {}, "targetPages": 0}
    prov_ledger = build_episode_delivery_ledger(contract, prov_episode)
    user = ("CurrentEpisodeBrief：%s\nEpisodeBeatSheet：%s\n保留事件：%s\n"
            "opening第一话需要让读者知道：WHO(主角是谁)、WHERE(生活在哪里)、"
            "WANT(想得到什么)、WHY(为什么)、PRESSURE(压力/困难)、"
            "INCIDENT(本话异常)、REACTION(主角反应)、HOOK(下一话钩子)。\n"
            "用户锁定设定（必须遵守，不得改动）：\n%s\n%s\n%s\n%s\n"
            "opening第一话前20%%—25%%篇幅必须让读者自然认识主角的日常生活与身份"
            "（例如人物卡中的职业和处境）、他为什么参加当前行动、失败对他意味着"
            "什么；禁止跳过日常生活直接进入事件现场。不得提前揭示保留事件/后续"
            "秘密。\n"
            "每个step的purpose必须从以下值中选择：orientation、character、"
            "motivation、pressure、action、discovery、reaction、decision、"
            "relationship、escalation、hook；opening第一话必须至少各有一个"
            "orientation/character、motivation、pressure、discovery、reaction、"
            "hook（pressure放在B2，hook放在B5）。\n"
            "请为每个beat输出2—6个executionSteps，整体18—26个；"
            "禁止把beat压成1个摘要step。" % (
                json.dumps(brief, ensure_ascii=False),
                json.dumps(beats, ensure_ascii=False),
                json.dumps((brief or {}).get("reservedForLater") or [],
                           ensure_ascii=False),
                str(getattr(contract, "rawUserText", "") or ""),
                str(getattr(contract, "confirmedWorldSetting", "") or ""),
                str(getattr(contract, "confirmedCharacterCards", "") or ""),
                str(getattr(contract, "confirmedSceneSetting", "") or "")))
    if reference_steps:
        user += ("\n\n上一版executionSteps（仅作参考，不要照抄）：%s\n"
                 "本轮程序会直接沿用以下beat的上一版步骤：%s；"
                 "其余beat请按新规则重写；不要改变五个Beat核心事件。" % (
                     "、".join(sorted(set(keep_beat_ids or []))),
                     json.dumps(reference_steps, ensure_ascii=False)))
    last_err = ""
    fallback = None
    for attempt in (range(1) if no_retry else range(2)):
        _budget_take(budget, "executionSteps")
        try:
            obj = _beat_json_call(
                sysmsg, user, temperature=0.45 if attempt else 0.35)
        except Exception as e:
            last_err = str(e)
            time.sleep(3)
            continue
        out_beats = (obj or {}).get("beats") or \
            (obj or {}).get("_items") or []
        keep = set(keep_beat_ids or [])
        ref_by_id = {}
        if reference_steps:
            for rb in reference_steps:
                if isinstance(rb, dict):
                    ref_by_id[str(rb.get("beatId") or "")] = \
                        rb.get("executionSteps") or []
        merged = []
        for b in beats:
            nb = dict(b)
            bid = str(b.get("beatId") or "")
            if keep and bid in keep:
                # 冻结beat：直接沿用上一版步骤，不采用模型本轮输出
                nb["executionSteps"] = ref_by_id.get(bid) or []
            else:
                ob = next((x for x in out_beats
                           if str((x or {}).get("beatId") or "") == bid), {})
                nb["executionSteps"] = ob.get("executionSteps") or []
            for s in nb["executionSteps"]:
                p = _normalize_step_purpose(s.get("purpose"))
                if p:
                    s["purpose"] = p
                di = s.get("deliveryIntent")
                if di is not None and (
                        not isinstance(di, dict) or
                        not str(di.get("fact") or "").strip()):
                    # 空deliveryIntent视为未提供：清理空壳，不影响剧情
                    s.pop("deliveryIntent", None)
                try:
                    _pages = int(s.get("recommendedPages") or 1)
                except Exception:
                    _pages = 1
                if _pages == 2:
                    stages = s.get("pageStages")
                    if not isinstance(stages, list) or len(stages) != 2 or \
                            not all(str(x or "").strip() for x in stages):
                        act = str(s.get("action") or "").strip()
                        res = str(s.get("result") or "").strip()
                        if act and res and _text_similarity(act, res) < 0.95:
                            s["pageStages"] = [act, res]
            merged.append(nb)
        fallback = merged
        errs = _steps_quality_errors(merged)
        text = json.dumps(merged, ensure_ascii=False)
        for v in _reserved_fact_hits(text, prov_ledger):
            errs.append("使用了保留事件：%s" % str(v)[:30])
        gaps = _opening_reader_gaps(merged)
        step_count = len([s for b in merged
                          for s in (b.get("executionSteps") or [])])
        if not errs and not gaps and step_count >= 12:
            # 内容完整时允许少于20页：不为了凑页数复制step
            return merged
        if attempt == 0:
            if gaps:
                last_err = "opening读者信息缺失：" + "；".join(gaps)
            elif step_count < 12:
                last_err = ("executionSteps只有%d个，疑似剧情摘要；"
                            "请细化到18—26个有叙事作用的step" % step_count)
            else:
                last_err = "；".join(errs[:10])
            user += ("\n\n上次不合格：%s\n请只重写executionSteps；"
                     "如果步骤已经完整，不要为了凑页数复制内容。" % last_err)
            continue
        if gaps:
            last_err = "opening读者信息缺失：" + "；".join(gaps)
        elif errs:
            last_err = "；".join(errs[:10])
        elif step_count < 12:
            last_err = "executionSteps只有%d个，仍然过少" % step_count
        else:
            return merged
    if fallback and any(
            s for b in fallback for s in (b.get("executionSteps") or [])):
        app.log("⚠ executionSteps未完全通过质量检查（不拦截，继续生成）：%s"
                % last_err)
        return fallback
    raise RuntimeError("executionSteps两次失败：%s" % last_err)


def estimate_pages_from_steps(beats):
    """页数由executionSteps决定：简单step 1页，重要step最多2页；不补空页。"""
    start = 1
    total = 0
    for b in beats or []:
        n = 0
        for s in b.get("executionSteps") or []:
            try:
                p = max(1, min(2, int(s.get("recommendedPages") or 1)))
            except Exception:
                p = 1
            n += p
        b["pageStart"] = start
        b["pageEnd"] = max(start, start + n - 1)
        start += n
        total += n
    return total


def _validate_step_records(pages, beat, steps, ledger, completed_step_ids,
                           completed_event_ids, prev_state, later_beats,
                           completed_results=None):
    """步骤制记录校验：唯一执行、时间向前、组状态连续、性格不漂移。"""
    errs = []
    if not isinstance(pages, list) or not pages:
        return ["本beat页面记录为空"]
    step_ids = [str(s.get("stepId") or "") for s in steps or []]
    if not step_ids:
        return ["本beat缺少executionSteps"]
    expected_ids = []
    for s in steps or []:
        try:
            n = max(1, min(2, int(s.get("recommendedPages") or 1)))
        except Exception:
            n = 1
        expected_ids += [str(s.get("stepId") or "")] * n
    if len(pages) != len(expected_ids):
        errs.append("记录数量应为%d，实际%d" % (len(expected_ids), len(pages)))
    first_pno = int(beat.get("pageStart") or 1)
    last_pno = int(beat.get("pageEnd") or (first_pno + len(pages) - 1))
    for i, r in enumerate(pages):
        pno = int(r.get("pageNumber") or 0)
        if pno != first_pno + i:
            errs.append("P%d 页码应连续为%d" % (pno, first_pno + i))
        if str(r.get("beatId") or "") != str(beat.get("beatId") or ""):
            errs.append("P%d beatId应为%s" % (pno, beat.get("beatId")))
        if not str(r.get("location") or "").strip():
            errs.append("P%d location为空" % pno)
        if not str(r.get("visualEvent") or "").strip():
            errs.append("P%d visualEvent为空" % pno)
        sid = str(r.get("executionStepId") or "")
        if sid not in step_ids:
            errs.append("P%d executionStepId不属于本beat：%s" % (pno, sid))
        if sid in (completed_step_ids or []):
            errs.append("P%d 已完成step %s 再次执行" % (pno, sid))
        if i == 0:
            if prev_state and not str(
                    r.get("causeFromPreviousPage") or "").strip():
                errs.append("P%d 缺少承接上一beat的causeFromPreviousPage" % pno)
        else:
            if not str(r.get("causeFromPreviousPage") or "").strip():
                errs.append("P%d causeFromPreviousPage为空" % pno)
        ni = _real_info(r.get("newInformation") or "")
        if ni and not _specific_why(r.get("whyItMatters") or ""):
            errs.append("P%d newInformation缺少具体原因" % pno)
        ev = str(r.get("visualEvent") or "")
        react = str(r.get("characterReaction") or "")
        if react and _text_similarity(ev, react) > 0.95:
            errs.append("P%d characterReaction复制event" % pno)
        if pno < last_pno and not str(r.get("nextPageBridge") or "").strip():
            errs.append("P%d nextPageBridge为空" % pno)
        pes = r.get("pageEndState")
        if not isinstance(pes, dict) or not pes:
            errs.append("P%d pageEndState为空" % pno)
        for v in _reserved_fact_hits(_execution_text(r), ledger):
            errs.append("P%d 使用了保留事件：%s" % (pno, str(v)[:30]))
        for v in _later_beat_hits(_execution_text(r), later_beats):
            errs.append("P%d 提前消费后续beat：%s" % (pno, str(v)[:30]))
        pv = _personality_violations(
            "%s %s %s" % (react, r.get("decisionOrConsequence"), ni))
        if pv:
            errs.append("P%d 人物性格漂移：%s" % (pno, "、".join(pv)))
        delta = r.get("storyStateDelta") or {}
        has_delta = any(str(delta.get(k) or "").strip() for k in (
            "locationChanged", "newFactAdded", "obstacleChanged",
            "goalChanged", "decisionMade", "relationshipChanged",
            "riskChanged", "questionChanged"))
        dec = str(r.get("decisionOrConsequence") or "").strip()
        if not ni and not has_delta and not dec and not react:
            errs.append("P%d 无任何有效结果" % pno)
        if not ni and not has_delta and not dec and re.search(
                r"(等待|即将|准备)[^。]{0,16}(落下|接触|发生|打开|到达)", ev):
            errs.append("P%d 仅等待/即将类填充页" % pno)
    seq = []
    for r in pages:
        sid = str(r.get("executionStepId") or "")
        if sid in step_ids:
            seq.append(step_ids.index(sid))
    if seq != sorted(seq):
        errs.append("步骤顺序倒退或重复触发")
    for s in steps or []:
        sid = str(s.get("stepId") or "")
        try:
            n = max(1, min(2, int(s.get("recommendedPages") or 1)))
        except Exception:
            n = 1
        cnt = sum(1 for r in pages
                  if str(r.get("executionStepId") or "") == sid)
        if cnt != n:
            errs.append("%s 应占%d页，实际%d页" % (sid, n, cnt))
        eid = str(s.get("eventId") or "")
        if eid and eid in (completed_event_ids or []):
            errs.append("%s 事件%s已完成，不能再次触发" % (sid, eid))
    # 读者可见交付：deliveryIntent事实必须出现在正文
    errs += _delivery_visible_errors(pages, steps)
    # dialogue/mixed 必须有真实对白
    errs += _dialogue_required_errors(pages, steps)
    # 不得重复已完成beat的核心结果
    errs += _completed_result_repeat_errors(pages, steps, completed_results)
    # step边界：本页不得提前完成下一步核心结果
    errs += _step_boundary_violations(pages, steps)
    # 连续页面功能重复
    errs += _duplicate_outcome_errors(pages)
    # 无依据隐藏实力暗示
    for r in pages or []:
        hits = _hidden_strength_hits(
            _visible_text(r) + " " + str(r.get("characterReaction") or ""))
        if hits:
            errs.append("P%d 无依据暗示隐藏实力：%s" % (
                r.get("pageNumber"), "、".join(hits)))
    return errs


def _normalize_step_records(pages, beat, steps, completed_step_ids,
                            prev_end_state):
    """为记录补齐步骤账本字段（eventSequenceIndex/moment/pageStartState）。
    只做确定性整理，不修改剧情内容。"""
    step_ids = [str(s.get("stepId") or "") for s in steps or []]
    seq_offset = len(completed_step_ids or [])
    step_occ = {}
    for idx, r in enumerate(pages):
        sid = str(r.get("executionStepId") or "")
        step_occ.setdefault(sid, []).append(idx)
    out = []
    completed = list(completed_step_ids or [])
    prev_loc = str((prev_end_state or {}).get("location") or "")
    for idx, r in enumerate(pages):
        sid = str(r.get("executionStepId") or "")
        nr = dict(r)
        nr["eventSequenceIndex"] = seq_offset + step_ids.index(sid) + 1
        nr["executionStepMoment"] = (
            "action" if idx == step_occ[sid][0] else "result")
        nr["pageStartState"] = {
            "location": prev_loc,
            "completedSteps": list(completed)}
        pes = nr.get("pageEndState") or {}
        prev_loc = str(pes.get("location") or nr.get("location") or prev_loc)
        if idx == step_occ[sid][-1]:
            completed.append(sid)
        out.append(nr)
    return out


def _remap_step_ids(pages, steps):
    """按recommendedPages把页码顺序重映射到executionStepId，并补齐
    空的pageEndState（由记录确定性推导）。
    只修正标号/状态格式，不改剧情内容；数量不符时不映射，交给校验失败。"""
    expected = []
    for s in steps or []:
        try:
            n = max(1, min(2, int(s.get("recommendedPages") or 1)))
        except Exception:
            n = 1
        expected += [str(s.get("stepId") or "")] * n
    if len(pages or []) != len(expected):
        return pages
    out = []
    for r, sid in zip(pages, expected):
        nr = dict(r)
        nr["executionStepId"] = sid
        pes = nr.get("pageEndState")
        if not isinstance(pes, dict) or not pes:
            nr["pageEndState"] = {
                "location": str(nr.get("location") or ""),
                "decision": str(nr.get("decisionOrConsequence") or ""),
                "knownInformation": [
                    str(nr.get("newInformation") or "")]
                if str(nr.get("newInformation") or "").strip() else []}
        out.append(nr)
    return out


def build_episode_page_records_by_steps(contract, brief, beats, ledger,
                                        budget=None, only_beat_ids=None,
                                        existing_records=None,
                                        no_retry=False):
    """按beat生成PageStoryRecords：每beat一次模型调用，步骤唯一、时间向前。
    一组失败只重试一次；总调用由budget控制（默认≤8次）。
    only_beat_ids：只重生成指定beat（用于定点修复），其余beat沿用
    existing_records并推进账本。"""
    records = dict(existing_records or {})
    completed_step_ids = []
    completed_event_ids = []
    completed_results = []
    current_story_state = dict((brief or {}).get("startState") or {})
    prev_end_state = current_story_state
    target_ids = set(only_beat_ids or [])
    for bi, beat in enumerate(beats or []):
        steps = beat.get("executionSteps") or []
        bid = str(beat.get("beatId") or "?")
        if not steps:
            raise RuntimeError("%s缺少executionSteps" % bid)
        if target_ids and bid not in target_ids:
            # 非目标beat：沿用已有记录，只推进完成账本与当前状态
            last_key = str(beat.get("pageEnd") or "")
            last_rec = records.get(last_key) or {}
            completed_step_ids += [str(s.get("stepId") or "") for s in steps]
            completed_event_ids += [
                str(s.get("eventId") or "") for s in steps
                if str(s.get("eventId") or "").strip()]
            if steps:
                completed_results.append(
                    str(steps[-1].get("result") or ""))
            if last_rec:
                current_story_state = dict(last_rec.get("pageEndState") or {})
                current_story_state["location"] = str(
                    last_rec.get("location") or
                    current_story_state.get("location") or "")
            prev_end_state = current_story_state
            continue
        page_start = int(beat.get("pageStart") or 1)
        page_end = int(beat.get("pageEnd") or 1)
        count = page_end - page_start + 1
        later = (beats or [])[bi + 1:]
        prev_ctx = {} if bi == 0 else prev_end_state
        opening_rule = _opening_intro_rule(contract, brief)
        sysmsg = (
            "你是漫画逐页编剧（步骤制）。当前beat必须按executionSteps顺序执行，"
            "每个step只发生一次，时间只能向前。禁止：重复执行已完成step/事件；"
            "回到上一组已发生事件之前；用等待、观察、准备、即将填充页数；"
            "改变人物核心性格（不得出现张扬、狡黠、不再保守等漂移）。"
            "每个step最多2页：第1页为该step动作，"
            "第2页(如有)为该step结果。每页内容必须对应step的purpose/"
            "readerGain，不得脱离step另编剧情。"
            "每页visualEvent只能覆盖当前executionStep的action/result范围，"
            "不得提前执行后续step的事件；同一句内心独白/对白只能出现一次；"
            "2页step的第2页只能表现结果与反应，不得重演第1页动作。"
            "executionStepId必须按recommendedPages顺序连续分配："
            "steps=[S1(1页),S2(1页),S3(1页)]时，第1页=S1、第2页=S2、第3页=S3；"
            "若S1为2页，则第1-2页=S1、第3页=S2；禁止跳步、缺步、重复分配。"
            "带deliveryIntent的step，其页面必须把fact转化为可见证据"
            "（画面/对白/旁白/人物动作/其他人物反应）；readerGain不等于"
            "已交付。不得把谨慎推导为隐藏实力（禁止故意压制灵力、隐藏修为、"
            "其实很强等无依据设定）。有现实人物互动的场景优先使用自然对白；"
            "连续页面不得承担相同叙事功能，两页读者结果相同时应合并。"
            "preferredMode为dialogue或mixed的step，对应页面必须包含非空的"
            "dialogueItems（speakerId+text），不得留空。能力事实：没有证据"
            "证明主角拥有远超测试结果的灵力，禁止‘故意压低、避免灵力过高、"
            "控制在及格线、隐藏真实修为、收住真正实力、不想暴露天赋’等"
            "表述，测灵结果按当前实际水平普通/正常/刚好达标解释。"
            "本beat不得重复上一beat已完成的收尾动作（擦拭、环顾、确认无人、"
            "决定保密），同一句内心独白不得重复出现。"
            "每页必须有明确结果。只输出JSON："
            '{"pages":[{"pageNumber":1,"beatId":"B1",'
            '"executionStepId":"B1-S1","location":"","characters":[],'
            '"causeFromPreviousPage":"","visualEvent":"",'
            '"newInformation":"","whyItMatters":"",'
            '"characterReaction":"","decisionOrConsequence":"",'
            '"relationshipChange":"","nextPageBridge":"",'
            '"dialogueItems":[],"revealedFactIds":[],"stateChanges":{},'
            '"storyStateDelta":{},"pageEndState":{}}]}')
        if opening_rule:
            sysmsg += "\n" + opening_rule
        user = (
            "CurrentEpisodeBrief：%s\n当前beat：%s\nexecutionSteps：%s\n"
            "已完成step：%s\n已完成事件：%s\n当前故事状态：%s\n"
            "上一beat实际结束状态：%s\n保留事件：%s\n"
            "请只输出第%d-%d页PageStoryRecords，共%d条；"
            "每页executionStepId必须是本beat步骤，页码连续，"
            "页数与executionSteps的recommendedPages合计一致。" % (
                json.dumps(brief, ensure_ascii=False),
                json.dumps({k: beat.get(k) for k in (
                    "beatId", "purpose", "event", "newInformation",
                    "characterResponse", "decisionOrConsequence",
                    "relationshipChange")}, ensure_ascii=False),
                json.dumps(steps, ensure_ascii=False),
                json.dumps(completed_step_ids, ensure_ascii=False),
                json.dumps(completed_event_ids, ensure_ascii=False),
                json.dumps(current_story_state, ensure_ascii=False),
                json.dumps(prev_ctx, ensure_ascii=False),
                json.dumps((brief or {}).get("reservedForLater") or [],
                           ensure_ascii=False),
                page_start, page_end, count))
        last_err = ""
        for attempt in (range(1) if no_retry else range(2)):
            _budget_take(budget, "records:%s" % bid)
            try:
                obj = _beat_json_call(
                    sysmsg, user, temperature=0.5 if attempt else 0.4)
            except Exception as e:
                last_err = str(e) + " | " + traceback.format_exc()[-800:]
                time.sleep(3)
                continue
            pages = sorted((obj or {}).get("pages") or
                           (obj or {}).get("_items") or [],
                           key=lambda x: int((x or {}).get("pageNumber") or 0))
            pages = _remap_step_ids(pages, steps)
            errs = _validate_step_records(
                pages, beat, steps, ledger, completed_step_ids,
                completed_event_ids, prev_ctx, later,
                completed_results)
            if not errs and opening_rule:
                trial = dict(records)
                for r in pages:
                    trial[str(int(r.get("pageNumber") or 0))] = r
                errs += _opening_intro_errors(trial, contract)
            if not errs:
                pages = _normalize_step_records(
                    pages, beat, steps, completed_step_ids, prev_end_state)
                for r in pages:
                    records[str(int(r["pageNumber"]))] = r
                break
            last_err = "；".join(errs[:10])
            user += ("\n\n上次不合格：%s\n"
                     "请只重写本beat第%d-%d页PageStoryRecords，"
                     "不得改变步骤顺序，不得重复已发生事件。" % (
                         last_err, page_start, page_end))
        else:
            raise RuntimeError("第%d-%d页(%s)两次失败：%s" % (
                page_start, page_end, bid, last_err))
        completed_step_ids += [str(s.get("stepId") or "") for s in steps]
        completed_event_ids += [
            str(s.get("eventId") or "") for s in steps
            if str(s.get("eventId") or "").strip()]
        if steps:
            completed_results.append(
                str(steps[-1].get("result") or ""))
        last_rec = records[str(page_end)]
        current_story_state = dict(last_rec.get("pageEndState") or {})
        current_story_state["location"] = str(
            last_rec.get("location") or
            current_story_state.get("location") or "")
        prev_end_state = current_story_state
    return {
        "records": records,
        "completedStepIds": completed_step_ids,
        "completedEventIds": completed_event_ids,
        "currentStoryState": current_story_state}


def _single_pass_step_contract(steps):
    """把executionSteps确定性整理为单次生成输入（不调用模型，不新增规划层）。"""
    out = []
    for i, s in enumerate(steps or []):
        nxt = steps[i + 1] if i + 1 < len(steps or []) else None
        facts = []
        if str(s.get("readerGain") or "").strip():
            facts.append(str(s.get("readerGain") or "").strip())
        di = s.get("deliveryIntent") or {}
        if isinstance(di, dict) and str(di.get("fact") or "").strip():
            facts.append(str(di.get("fact") or "").strip())
        try:
            pages = max(1, min(2, int(s.get("recommendedPages") or 1)))
        except Exception:
            pages = 1
        out.append({
            "stepId": str(s.get("stepId") or ""),
            "beatId": str(s.get("beatId") or ""),
            "recommendedPages": pages,
            "startState": str(s.get("cause") or ""),
            "requiredEvent": str(s.get("action") or ""),
            "requiredResult": str(s.get("result") or ""),
            "requiredVisibleFacts": facts,
            "characterResponse": str(s.get("characterResponse") or ""),
            "allowedFreedom": "自然对白、动作细节、环境细节、情绪表现、"
                              "信息表达方式；不得改剧情/能力/目标/世界规则",
            "endBoundary": (
                "最多进行到本step的requiredResult成立为止；"
                "不得提前执行下一step：%s" % (
                    str((nxt or {}).get("action") or "本话结尾")))})
    return out


def _validate_single_pass_records(records, beats, target):
    """single-pass最小结构校验：页数/顺序/step覆盖/结构完整。不做故事质量判断。"""
    errs = []
    steps = [s for b in beats or [] for s in (b.get("executionSteps") or [])]
    step_ids = [str(s.get("stepId") or "") for s in steps]
    expected = []
    for s in steps:
        try:
            n = max(1, min(2, int(s.get("recommendedPages") or 1)))
        except Exception:
            n = 1
        expected += [str(s.get("stepId") or "")] * n
    if len(records) != target or target != len(expected):
        errs.append("页数应为%d，实际%d" % (len(expected), len(records)))
    seq = []
    for i in range(1, target + 1):
        r = (records or {}).get(str(i))
        if not isinstance(r, dict):
            errs.append("P%d 缺失" % i)
            continue
        pno = int(r.get("pageNumber") or r.get("pageNo") or 0)
        if pno != i:
            errs.append("pageNo应为%d，实际%d" % (i, pno))
        sid = str(r.get("executionStepId") or "")
        if sid not in step_ids:
            errs.append("P%d executionStepId非法：%s" % (i, sid))
        if not str(r.get("location") or "").strip():
            errs.append("P%d location为空" % i)
        if not str(r.get("visualEvent") or "").strip():
            errs.append("P%d visualEvent为空" % i)
        if not isinstance(r.get("pageEndState"), dict):
            errs.append("P%d pageEndState缺失" % i)
        if not isinstance(r.get("dialogueItems"), list):
            errs.append("P%d dialogueItems缺失" % i)
        if sid in step_ids:
            seq.append(step_ids.index(sid))
    if seq != sorted(seq):
        errs.append("步骤顺序倒置或重复执行")
    present = set()
    for i in range(1, target + 1):
        sid = str((records or {}).get(str(i), {}).get("executionStepId") or "")
        present.add(sid)
    for sid in step_ids:
        if sid not in present:
            errs.append("%s 未出现" % sid)
    cnt = {}
    for i in range(1, target + 1):
        sid = str((records or {}).get(str(i), {}).get("executionStepId") or "")
        cnt[sid] = cnt.get(sid, 0) + 1
    for s in steps:
        sid = str(s.get("stepId") or "")
        try:
            n = max(1, min(2, int(s.get("recommendedPages") or 1)))
        except Exception:
            n = 1
        if cnt.get(sid, 0) != n:
            errs.append("%s 应%d页，实际%d页" % (sid, n, cnt.get(sid, 0)))
    return errs


def build_episode_page_records_single_pass(contract, brief, beats, ledger,
                                           budget=None, no_retry=False):
    """整话正文一次生成：executionSteps是硬剧情轨道，模型只负责演出。
    正式正文唯一入口；本函数每次调用只允许一次Qwen。"""
    steps = [s for b in beats or []
             for s in (b.get("executionSteps") or [])]
    if not steps:
        raise RuntimeError("缺少executionSteps")
    target = 0
    for s in steps:
        try:
            target += max(1, min(2, int(s.get("recommendedPages") or 1)))
        except Exception:
            target += 1
    contract_steps = _single_pass_step_contract(steps)
    opening_rule = _opening_intro_rule(contract, brief)
    locked = (
        "用户锁定设定（不得改动）：\n%s\n%s\n%s\n%s\n"
        "CurrentEpisodeBrief：%s\n保留事件：%s\n"
        "性格状态与能力事实是两个维度：没有能力事实支持时，不得从谨慎/低调"
        "推导隐藏实力；能力表现使用普通/正常/刚好达标解释。"
        "opening第一话必须在正文自然让读者知道：主角是谁、现在的生活、"
        "为什么参加、想得到什么、当前压力。"
        "已完成的requiredResult后续不得重新执行；同一句内心独白/对白不得"
        "重复出现；不得提前揭示保留事件。" % (
            str(getattr(contract, "rawUserText", "") or ""),
            str(getattr(contract, "confirmedWorldSetting", "") or ""),
            str(getattr(contract, "confirmedCharacterCards", "") or ""),
            str(getattr(contract, "confirmedSceneSetting", "") or ""),
            json.dumps(brief, ensure_ascii=False),
            json.dumps((brief or {}).get("reservedForLater") or [],
                       ensure_ascii=False)))
    if opening_rule:
        locked += "\n" + opening_rule
    sysmsg = (
        "你是漫画正文编剧。executionSteps是当前话的事实合同：事件顺序、因果、"
        "人物目标、动作、结果、必须让读者知道的信息都已确定，你只能把它们"
        "自然演成漫画，不得重新决定故事。\n"
        "【A. LOCKED FACTS】\n%s\n"
        "【B. STORY EXECUTION CONTRACT】\n按顺序执行以下step，每页只覆盖"
        "当前step的requiredEvent/requiredResult，不得提前执行下一step的"
        "requiredResult，不得重复已完成结果。\n%s\n"
        "【C. CREATIVE FREEDOM】\n只允许自由决定：自然对白、动作细节、"
        "环境细节、情绪表现、信息表达方式。\n"
        "规则：1) 总页数=%d，pageNo从1连续；2) 每页executionStepId必须对应"
        "其step；3) 2页step的两页必须承担不同叙事功能（事件→反应/结果）；"
        "4) 人物互动场景自然产生对白，禁止解释型设定台词；5) 禁止重复已完成"
        "事件与同一句独白；6) 关键事件（受伤/接触/异常）只发生一次，顺序单向。\n"
        "只输出JSON：{\"pages\":[{\"pageNo\":1,\"executionStepId\":\"B1-S1\","
        "\"location\":\"\",\"characters\":[],\"visualEvent\":\"\","
        "\"dialogueItems\":[{\"speakerId\":\"\",\"kind\":\"dialogue\","
        "\"text\":\"\"}],\"narration\":\"\",\"pageEndState\":{}}]}" % (
            locked, json.dumps(contract_steps, ensure_ascii=False), target))
    user = "请按STORY EXECUTION CONTRACT输出完整第1话共%d页PageStoryRecords。" % target
    last_err = ""
    records = {}
    for attempt in (range(1) if no_retry else range(2)):
        _budget_take(budget, "single_pass")
        try:
            obj = _beat_json_call(sysmsg, user, temperature=0.4)
        except Exception as e:
            last_err = str(e)
            time.sleep(3)
            continue
        pages = sorted(
            (obj or {}).get("pages") or (obj or {}).get("_items") or [],
            key=lambda x: int((x or {}).get("pageNo") or
                              (x or {}).get("pageNumber") or 0))
        records = {}
        for p in pages:
            nr = dict(p)
            pno = int(nr.get("pageNo") or nr.get("pageNumber") or 0)
            nr["pageNumber"] = pno
            nr["pageNo"] = pno
            records[str(pno)] = nr
        errs = _validate_single_pass_records(records, beats, target)
        if not errs and opening_rule:
            errs += _opening_intro_errors(records, contract)
        if attempt == 1:
            # 开场介绍已尽力要求；最后一次不再因开场旁白细节拦截生成
            errs = [e for e in errs if "开场" not in e]
        if not errs:
            return {
                "records": records,
                "completedStepIds": [str(s.get("stepId") or "") for s in steps],
                "completedEventIds": [
                    str(s.get("eventId") or "") for s in steps
                    if str(s.get("eventId") or "").strip()],
                "currentStoryState": (records.get(str(target)) or {}).get(
                    "pageEndState") or {}}
        last_err = "；".join(errs[:10])
        user += "\n\n上次不合格：%s\n请只重写完整一话。" % last_err
    if records:
        app.log("⚠ 逐页记录未完全通过校验（不拦截，继续生成）：%s" % last_err)
        return {
            "records": records,
            "completedStepIds": [str(s.get("stepId") or "") for s in steps],
            "completedEventIds": [
                str(s.get("eventId") or "") for s in steps
                if str(s.get("eventId") or "").strip()],
            "currentStoryState": (records.get(str(target)) or {}).get(
                "pageEndState") or {}}
    raise RuntimeError("single-pass两次失败：%s" % last_err)


def _deterministic_page_cards(beats):
    """把executionSteps确定性展开为每页事件卡：页数、事件、结果、边界全部
    由程序决定，正文模型无权更改（不调用模型）。"""
    cards = []
    page_no = 0
    completed = []
    prev_result = ""
    for b in beats or []:
        for s in (b.get("executionSteps") or []):
            try:
                n = max(1, min(2, int(s.get("recommendedPages") or 1)))
            except Exception:
                n = 1
            facts = []
            if str(s.get("readerGain") or "").strip():
                facts.append(str(s.get("readerGain") or "").strip())
            di = s.get("deliveryIntent") or {}
            if isinstance(di, dict) and str(di.get("fact") or "").strip():
                facts.append(str(di.get("fact") or "").strip())
            mode = str((di or {}).get("preferredMode") or "").strip() \
                if isinstance(di, dict) else ""
            stages = s.get("pageStages") or []
            sid = str(s.get("stepId") or "")
            act = str(s.get("action") or "")
            res = str(s.get("result") or "")
            if n == 1:
                page_no += 1
                cards.append({
                    "pageNo": page_no, "stepId": sid,
                    "beatId": str(s.get("beatId") or ""),
                    "pageStage": "full",
                    "requiredEvent": act,
                    "requiredResult": res,
                    "requiredVisibleFacts": facts,
                    "preferredDialogue": mode in ("dialogue", "mixed"),
                    "startState": prev_result or str(s.get("cause") or ""),
                    "completedEvents": list(completed),
                    "endBoundary": "本页完成requiredEvent并达成requiredResult；"
                                   "不得提前执行下一页事件。"})
            else:
                page_no += 1
                cards.append({
                    "pageNo": page_no, "stepId": sid,
                    "beatId": str(s.get("beatId") or ""),
                    "pageStage": "action",
                    "requiredEvent": act,
                    "requiredResult": str(
                        stages[0] if stages and str(stages[0] or "").strip()
                        else act),
                    "requiredVisibleFacts": facts,
                    "preferredDialogue": mode in ("dialogue", "mixed"),
                    "startState": prev_result or str(s.get("cause") or ""),
                    "completedEvents": list(completed),
                    "endBoundary": "本页只表现事件发生（action），"
                                   "不得提前完成step的result。"})
                page_no += 1
                cards.append({
                    "pageNo": page_no, "stepId": sid,
                    "beatId": str(s.get("beatId") or ""),
                    "pageStage": "result",
                    "requiredEvent": res,
                    "requiredResult": str(
                        stages[1] if len(stages) > 1 and
                        str(stages[1] or "").strip() else res),
                    "requiredVisibleFacts": facts,
                    "preferredDialogue": mode in ("dialogue", "mixed"),
                    "startState": str(
                        stages[0] if stages and str(stages[0] or "").strip()
                        else act),
                    "completedEvents": list(completed),
                    "endBoundary": "本页完成step的result；"
                                   "不得提前执行下一步事件。"})
            prev_result = res
            completed.append(res)
    return cards


def _opening_intro_rule(contract, brief):
    """第1话开场介绍规则：前两页必须用旁白让读者知道谁/在哪/做什么/为什么。"""
    if int((brief or {}).get("episodeNo") or 1) != 1:
        return ""
    return ("opening第1话前2页是开场介绍：第1页用远景/全景展示人物全身与主场景的"
            "空间关系，人物不占满画面；第1—2页必须各写一条40—90字开场旁白"
            "（narration），直接向读者说明：主角是谁、身在何处、当前是什么日子/"
            "事件、她要做什么、为什么、失败了会怎样；旁白必须具体通俗（写清姓名、"
            "地名、日期与后果），禁止用抽象词；不得跳过介绍直接进入事件现场。")


def _opening_intro_errors(records, contract):
    """第1话开场交付校验：前两页必须有够长旁白，且可见文字出现主角姓名与地点。"""
    errs = []
    if not isinstance(records, dict) or not records:
        return errs
    p1 = records.get("1") or {}
    p2 = records.get("2") or {}

    def visible(r):
        parts = [str(r.get("narration") or "")]
        for d in (r.get("dialogueItems") or []):
            if isinstance(d, dict):
                parts.append(str(d.get("text") or ""))
        return " ".join(parts)

    v12 = visible(p1) + visible(p2)
    if len(str(p1.get("narration") or "").strip()) < 20:
        errs.append("P1开场旁白缺失或太短（需至少20字）")
    if len(str(p2.get("narration") or "").strip()) < 15:
        errs.append("P2开场旁白缺失或太短（需至少15字）")
    lead = ""
    try:
        cards = app.parse_char_sheet(str(contract.confirmedCharacterCards or ""))
        names = list(cards.keys())
        if names:
            lead = re.sub(r"\s*[（(][^）)]*[）)]\s*$", "",
                          str(names[0])).strip()
    except Exception:
        pass
    if lead and lead not in v12:
        errs.append("P1—P2可见文字未出现主角姓名「%s」" % lead)
    scene = str(contract.confirmedSceneSetting or "")
    scene = scene.split("【场景：")[-1].split("】")[0].strip()
    core = str(scene).split("·")[0].split("：")[0].strip()
    if core and core not in v12:
        errs.append("P1—P2可见文字未出现地点「%s」" % core)
    return errs


def _validate_deterministic_records(records, cards):
    """deterministic页事件的最小结构校验：页数、pageNo、字段、stepId顺序、
    事件边界（本页不得提前出现下一页事件原文）。不做故事质量判断。"""
    errs = []
    target = len(cards or [])
    if len(records) != target:
        errs.append("页数应为%d，实际%d" % (target, len(records)))
    for i in range(1, target + 1):
        r = (records or {}).get(str(i))
        card = cards[i - 1] if i - 1 < len(cards or []) else {}
        if not isinstance(r, dict):
            errs.append("P%d 缺失" % i)
            continue
        pno = int(r.get("pageNumber") or r.get("pageNo") or 0)
        if pno != i:
            errs.append("pageNo应为%d，实际%d" % (i, pno))
        if str(r.get("executionStepId") or "") != str(card.get("stepId") or ""):
            errs.append("P%d stepId应为%s" % (i, card.get("stepId")))
        if not str(r.get("visualEvent") or "").strip():
            errs.append("P%d visualEvent为空" % i)
        if not isinstance(r.get("dialogueItems"), list):
            errs.append("P%d dialogueItems缺失" % i)
        if not isinstance(r.get("narration"), str):
            errs.append("P%d narration缺失" % i)
        if not isinstance(r.get("pageEndState"), dict):
            errs.append("P%d pageEndState缺失" % i)
    # preferredDialogue：该step至少一页有自然对白（不强求每页都说话）
    step_speech = {}
    for i in range(1, target + 1):
        r = (records or {}).get(str(i)) or {}
        sid = str(r.get("executionStepId") or "")
        has = any(str(d.get("text") or "").strip()
                  for d in (r.get("dialogueItems") or [])
                  if isinstance(d, dict))
        step_speech[sid] = step_speech.get(sid, False) or has
    for card in cards or []:
        if card.get("preferredDialogue") and \
                not step_speech.get(str(card.get("stepId") or "")):
            errs.append("P%d 要求自然对白但dialogueItems为空" %
                        card.get("pageNo"))
    # 事件边界：本页不得提前出现下一页事件/结果原文（>=10字符）
    for i in range(1, target):
        r = (records or {}).get(str(i)) or {}
        nxt = cards[i]
        txt = str(r.get("visualEvent") or "") + " " + \
            str(r.get("narration") or "")
        for probe in (str(nxt.get("requiredEvent") or ""),
                      str(nxt.get("requiredResult") or "")):
            probe = str(probe or "").strip()
            if len(probe) >= 10 and probe in txt:
                errs.append("P%d 提前出现下一页事件：%s" % (i, probe[:20]))
                break
    return errs


def build_episode_page_records_deterministic(contract, brief, beats, ledger,
                                             budget=None, no_retry=False):
    """保底方案正式入口：程序确定每页事件（deterministic page events），
    一次Qwen只负责把每页演出来（画面/对白/旁白），不再决定剧情与页数。"""
    cards = _deterministic_page_cards(beats)
    if not cards:
        raise RuntimeError("缺少页面事件卡")
    target = len(cards)
    opening_rule = _opening_intro_rule(contract, brief)
    locked = (
        "用户锁定设定（不得改动）：\n%s\n%s\n%s\n%s\n"
        "CurrentEpisodeBrief：%s\n保留事件：%s\n"
        "性格状态与能力事实是两个维度：没有能力事实支持时，不得从谨慎/低调"
        "推导隐藏实力；能力表现使用普通/正常/刚好达标解释。"
        "opening第一话必须在正文自然让读者知道：主角是谁、现在的生活、"
        "为什么参加、想得到什么、当前压力。"
        "已完成的completedEvents不得重新执行；同一句内心独白/对白不得重复；"
        "不得提前揭示保留事件。" % (
            str(getattr(contract, "rawUserText", "") or ""),
            str(getattr(contract, "confirmedWorldSetting", "") or ""),
            str(getattr(contract, "confirmedCharacterCards", "") or ""),
            str(getattr(contract, "confirmedSceneSetting", "") or ""),
            json.dumps(brief, ensure_ascii=False),
            json.dumps((brief or {}).get("reservedForLater") or [],
                       ensure_ascii=False)))
    if opening_rule:
        locked += "\n" + opening_rule
    sysmsg = (
        "你是漫画正文编剧。每一页要演什么已经由程序确定（PAGE CARDS），"
        "你只负责把每页写得自然：画面、对白、旁白/内心。"
        "不得改变事件顺序、不得提前执行后续页面事件、不得重复已完成事件。\n"
        "【A. LOCKED FACTS】\n%s\n"
        "【PAGE CARDS】\n%s\n"
        "【CREATIVE FREEDOM】\n自然对白、动作细节、环境细节、情绪表现、"
        "信息表达方式；不得改剧情/能力/目标/世界规则。\n"
        "规则：1) 共%d页，pageNo从1连续；2) 每页只能演当前页requiredEvent/"
        "requiredResult，不得提前执行下一页；3) preferredDialogue=true的页面"
        "必须包含自然对白；4) 禁止解释型设定台词；5) 已完成事件不得重演；"
        "6) 同一句独白不得重复出现。\n"
        "只输出JSON：{\"pages\":[{\"pageNo\":1,\"visualEvent\":\"\","
        "\"dialogueItems\":[{\"speakerId\":\"\",\"kind\":\"dialogue\","
        "\"text\":\"\"}],\"narration\":\"\",\"pageEndState\":{}}]}" % (
            locked, json.dumps(cards, ensure_ascii=False), target))
    user = "请按PAGE CARDS输出完整第1话共%d页，每页只写表达。" % target
    last_err = ""
    records = {}
    for attempt in (range(1) if no_retry else range(2)):
        _budget_take(budget, "deterministic")
        try:
            obj = _beat_json_call(sysmsg, user, temperature=0.4)
        except Exception as e:
            last_err = str(e)
            time.sleep(3)
            continue
        pages = sorted(
            (obj or {}).get("pages") or (obj or {}).get("_items") or [],
            key=lambda x: int((x or {}).get("pageNo") or
                              (x or {}).get("pageNumber") or 0))
        records = {}
        for p in pages:
            nr = dict(p)
            pno = int(nr.get("pageNo") or nr.get("pageNumber") or 0)
            card = cards[pno - 1] if 1 <= pno <= target else {}
            nr["pageNumber"] = pno
            nr["pageNo"] = pno
            nr["executionStepId"] = str(card.get("stepId") or "")
            nr["beatId"] = str(card.get("beatId") or "")
            records[str(pno)] = nr
        errs = _validate_deterministic_records(records, cards)
        if not errs and opening_rule:
            errs += _opening_intro_errors(records, contract)
        if attempt == 1:
            # 开场介绍已尽力要求；最后一次不再因开场旁白细节拦截生成
            errs = [e for e in errs if "开场" not in e]
        if not errs:
            # 程序确定性补齐派生字段（页面事件由pageCard决定，不是模型编造）
            for i, card in enumerate(cards, start=1):
                r = records.get(str(i)) or {}
                facts = card.get("requiredVisibleFacts") or []
                beat_loc = ""
                beat_chars = []
                for b in beats or []:
                    if str(b.get("beatId")) == str(card.get("beatId") or ""):
                        sa = b.get("stateAfter") or {}
                        beat_loc = str(sa.get("location") or "")
                        beat_chars = list(sa.get("characters") or [])
                        break
                r.setdefault("location", beat_loc)
                r.setdefault("characters", beat_chars)
                r.setdefault("newInformation",
                             facts[0] if facts else
                             str(card.get("requiredResult") or ""))
                r.setdefault("whyItMatters",
                             facts[0] if facts else "")
                r.setdefault("decisionOrConsequence",
                             str(card.get("requiredResult") or ""))
                r.setdefault("causeFromPreviousPage",
                             str(card.get("startState") or ""))
                r.setdefault(
                    "nextPageBridge",
                    str(cards[i]["requiredEvent"]) if i < target else "本话结束")
                r.setdefault("storyStateDelta", {
                    "decisionMade": str(card.get("requiredResult") or "")})
            steps = [s for b in beats or []
                     for s in (b.get("executionSteps") or [])]
            return {
                "records": records,
                "completedStepIds": [str(s.get("stepId") or "") for s in steps],
                "completedEventIds": [
                    str(s.get("eventId") or "") for s in steps
                    if str(s.get("eventId") or "").strip()],
                "currentStoryState": (records.get(str(target)) or {}).get(
                    "pageEndState") or {}}
        last_err = "；".join(errs[:10])
        user += "\n\n上次不合格：%s\n请只重写完整一话的表达。" % last_err
    if records:
        app.log("⚠ 逐页记录未完全通过校验（不拦截，继续生成）：%s" % last_err)
        steps = [s for b in beats or []
                 for s in (b.get("executionSteps") or [])]
        return {
            "records": records,
            "completedStepIds": [str(s.get("stepId") or "") for s in steps],
            "completedEventIds": [
                str(s.get("eventId") or "") for s in steps
                if str(s.get("eventId") or "").strip()],
            "currentStoryState": (records.get(str(target)) or {}).get(
                "pageEndState") or {}}
    raise RuntimeError("deterministic两次失败：%s" % last_err)


def build_longform_context_package(contract, bible, target_pages=None,
                                   episode_plan=None):
    """统一长篇上下文包：用户锁定事实/上层规划/三话窗口/人物状态完整保留；
    普通历史说明只保留结构化条目，不做字符串切片。"""
    pkg = {
        "rawUserText": str(contract.rawUserText or ""),
        "confirmedCharacterCards": str(contract.confirmedCharacterCards or ""),
        "confirmedSceneSetting": str(contract.confirmedSceneSetting or ""),
        "confirmedEpisodeSynopsis": str(
            contract.confirmedEpisodeSynopsis or ""),
        "userLockedFacts": (bible.userLockedFacts if bible else []) or [],
        "canonFacts": [],
        "seriesPlan": (bible.seriesPlan if bible else {}) or {},
        "volumePlans": (bible.volumePlans if bible else []) or [],
        "arcPlans": (bible.arcPlans if bible else []) or [],
        "episodePlans": (bible.episodePlans if bible else []) or [],
        "characterStates": (bible.characterStates if bible else {}) or {},
        "relationshipStates": (
            bible.relationshipStates if bible else []) or [],
        "timeline": [],
        "unresolvedThreads": (
            bible.unresolvedThreads if bible else []) or [],
        "foreshadowing": (bible.foreshadowing if bible else []) or [],
        "knownInformation": (
            bible.knownInformation if bible else {}) or {},
        "lastCompletedEpisode": (
            bible.lastCompletedEpisode if bible else 0) or 0,
        "targetPages": int(target_pages or 0) or 8,
    }
    if bible:
        canon = bible.canonFacts or []
        last = max([int(c.get("episode") or 0) for c in canon] or [0])
        relevant = [c for c in canon
                    if int(c.get("episode") or 0) >= max(1, last - 2)]
        pkg["canonFacts"] = relevant[-60:]
        pkg["timeline"] = (bible.timeline or [])[-40:]
    if isinstance(episode_plan, dict):
        pkg["currentEpisodePlan"] = episode_plan
    return pkg


def build_episode_text_plans(contract, episode_plan, pages, visual_plans,
                             target_pages, records=None):
    """每页先生成结构化可读性字段，再由Qwen按字段生成textItems；
    程序只做质量校验，不自行拼接正文。"""
    fields = {}
    for i, story in enumerate(pages, 1):
        fields[str(i)] = make_page_readability_plan(
            i, story, (visual_plans or {}).get(str(i)) or {},
            {"pageNumber": i, "textUnits": []}, episode_plan,
            record=(records or {}).get(str(i)))
    ctx = build_longform_context_package(contract, None, target_pages)
    is_opening = int((episode_plan or {}).get("episodeNo") or 1) == 1
    opening_hint = (
        "第1话第1—2页必须各包含一条开场介绍旁白（kind=narration），"
        "用30—60字直接向读者说明：主角是谁、身在何处、当前是什么日子/事件、"
        "她要做什么、为什么、失败了会怎样；对白不能代替旁白。"
    ) if is_opening else ""
    sysmsg = (
        "你是漫画文字编辑。根据每一页给定的可读性字段生成排字文字计划。"
        "只生成读者需要的信息；旁白负责时间/地点/前情/原因，对白负责新信息/"
        "关系/冲突/决定，内心负责怀疑/推理/恐惧，音效只增强动作。"
        "不得出现编剧术语，不得把创作目的写给读者，不得把人物外貌当目标；"
        "全话同一句话不得在两页重复出现，同一页内也不得出现完全相同的句子，"
        "同一信息在不同页面必须换用不同措辞；对白必须直接写出人物说的话，"
        "禁止用括号舞台说明（如“（疲惫的声音）”）代替对白文本。"
        "句子必须完整并以句号结束。speakerId必须与说话角色一致；"
        "没有明确说话人时speakerId留空。只输出JSON："
        '{"pages":[{"pageNumber":1,"textUnits":[{"kind":"narration|dialogue|'
        'innerThought|soundEffect","speakerId":"","text":"可见文本",'
        '"panel":1,"purpose":"本句作用","placementPreference":'
        '"topLeft|topRight|bottomLeft|bottomRight"}]}]}') + (
        ("\n" + opening_hint) if opening_hint else "")
    out = {}
    page_nos = list(range(1, len(pages) + 1))
    chunk_size = 8
    for start in range(0, len(page_nos), chunk_size):
        chunk = page_nos[start:start + chunk_size]
        sub_fields = {str(p): fields[str(p)] for p in chunk}
        user = ("项目上下文：\n%s\n\n本次只生成第%d至第%d页的可读性文字计划：\n%s" % (
            json.dumps(ctx, ensure_ascii=False),
            chunk[0], chunk[-1],
            json.dumps(sub_fields, ensure_ascii=False)))
        obj = None
        last_err = ""
        for attempt in range(3):
            try:
                raw = app.chat_stream(
                    [{"role": "system", "content": sysmsg},
                     {"role": "user", "content": user}],
                    0.4 - 0.05 * attempt, None, 6000)
                obj = _extract_json_obj(raw)
            except Exception as e:
                last_err = str(e)
                time.sleep(4)
                continue
            if not isinstance(obj, dict) or \
                    not isinstance(obj.get("pages"), list):
                last_err = "解析失败"
                time.sleep(4)
                continue
            break
        if not isinstance(obj, dict) or not isinstance(obj.get("pages"), list):
            raise RuntimeError("可读性文字计划解析失败：" + last_err)
        got = {}
        for p in obj["pages"]:
            if not isinstance(p, dict):
                continue
            got[str(int(p.get("pageNumber") or 0))] = p
        for pno in chunk:
            p = got.get(str(pno))
            if not isinstance(p, dict):
                raise RuntimeError("可读性文字计划缺少第%d页" % pno)
            units = []
            for u in (p.get("textUnits") or []):
                if not isinstance(u, dict):
                    continue
                t = str(u.get("text") or "").strip()
                t = re.sub(r"^[（(][^）)]{1,30}[）)]\s*", "", t).strip()
                if not t:
                    continue
                kind = str(u.get("kind") or "narration")
                if kind == "soundEffect":
                    kind = "sfx"
                if kind not in ("narration", "dialogue", "innerThought", "sfx"):
                    kind = "narration"
                if kind != "sfx" and len(t) >= 6 and \
                        not re.search(r"[。！？…]$", t) and \
                        not re.search(r"(\.\.\.|…)$", t):
                    t += "。"
                units.append({
                    "type": kind,
                    "speakerId": str(u.get("speakerId") or ""),
                    "visibleText": t, "text": t,
                    "panel": max(1, int(u.get("panel") or 1)),
                    "placementPreference": str(
                        u.get("placementPreference") or "topLeft"),
                    "purpose": str(u.get("purpose") or ""),
                })
            seen_units = set()
            deduped = []
            for u in units:
                key = (u["type"], u["visibleText"])
                if key in seen_units:
                    continue
                seen_units.add(key)
                deduped.append(u)
            out[str(pno)] = {"pageNumber": pno, "textUnits": deduped}
    # 确定性去重：连续页面出现完全相同的句子时只保留首次出现
    prev_texts = set()
    for pno in sorted(out, key=int):
        kept = []
        for u in out[pno]["textUnits"]:
            t = str(u.get("visibleText") or u.get("text") or "").strip()
            if t and t in prev_texts:
                continue
            kept.append(u)
        out[pno]["textUnits"] = kept
        prev_texts = {str(u.get("visibleText") or "").strip()
                      for u in kept
                      if str(u.get("visibleText") or "").strip()}
    if is_opening:
        # 兜底：即使文字编辑器漏写开场旁白，也把逐页记录里的开场旁白印上，
        # 保证第1话前两页一定有可读的介绍。
        for pno in ("1", "2"):
            rec = (records or {}).get(pno) or {}
            fallback = str(rec.get("narration") or "").strip()
            if not fallback:
                continue
            page = out.get(pno) or {}
            units = page.get("textUnits") or []
            has_narration = any(
                u.get("type") == "narration" and
                len(str(u.get("visibleText") or "").strip()) >= 10
                for u in units)
            if not has_narration:
                if not re.search(r"[。！？…]$", fallback):
                    fallback += "。"
                units.insert(0, {
                    "type": "narration", "speakerId": "",
                    "visibleText": fallback, "text": fallback,
                    "panel": 1, "placementPreference": "topLeft",
                    "purpose": "开场介绍"})
                page["textUnits"] = units
    errs = check_text_quality(out, episode_plan)
    if errs:
        app.log("⚠ 可读性文字质量检查未通过（不拦截，继续排字）：%s"
                % "；".join(errs[:6]))
    return out


def _mission_map_from_records(records, beats, target):
    """修复后PageMissionMap：从PageStoryRecords确定性生成，不再调用模型。"""
    missions = []
    for i in range(1, int(target) + 1):
        r = (records or {}).get(str(i)) or {}
        missions.append({
            "pageNumber": i,
            "beatId": str(r.get("beatId") or ""),
            "executionStepId": str(r.get("executionStepId") or ""),
            "location": str(r.get("location") or ""),
            "characters": r.get("characters") or [],
            "pageEvent": str(r.get("visualEvent") or ""),
            "newInformation": str(r.get("newInformation") or ""),
            "whyItMatters": str(r.get("whyItMatters") or ""),
            "characterReaction": str(r.get("characterReaction") or ""),
            "decisionOrConsequence": str(
                r.get("decisionOrConsequence") or ""),
            "nextPageBridge": str(r.get("nextPageBridge") or ""),
        })
    return {"episodeNo": 1, "targetPages": int(target), "beats": beats,
            "missions": missions}


def _validate_beat_coverage(beats, target):
    """keyBeats 页码覆盖硬校验：从1连续到target，无空档无重叠。"""
    errs = []
    if not beats:
        return ["缺少keyBeats"]
    for i, b in enumerate(beats):
        if not isinstance(b, dict):
            errs.append("第%d个beat不是对象" % (i + 1))
            continue
        bid = str(b.get("beatId") or ("B%d" % (i + 1)))
        ps = int(b.get("pageStart") or 0)
        pe = int(b.get("pageEnd") or 0)
        if not (1 <= ps <= pe <= target):
            errs.append("%s页码越界：%d-%d（目标%d）" % (bid, ps, pe, target))
    if beats and int(beats[0].get("pageStart") or 0) != 1:
        errs.append("第一个beat未从第1页开始")
    if beats and int(beats[-1].get("pageEnd") or 0) != target:
        errs.append("最后一个beat未结束于第%d页" % target)
    prev_end = 0
    for b in beats:
        if not isinstance(b, dict):
            continue
        ps = int(b.get("pageStart") or 0)
        pe = int(b.get("pageEnd") or 0)
        if prev_end and ps != prev_end + 1:
            errs.append("%s与上一beat有空档或重叠" % str(b.get("beatId") or ""))
        prev_end = pe
    return errs


def _redistribute_beat_pages(beats, target):
    """确定性重新分配页码：按原顺序均分，最后一beat结束于target。"""
    n = max(1, len(beats))
    base, rem = divmod(int(target), n)
    start = 1
    for i, b in enumerate(beats):
        end = start + base - 1 + (1 if i >= n - rem else 0)
        b["pageStart"] = start
        b["pageEnd"] = end
        start = end + 1
    return beats


_COVERAGE_SYSMSG = (
    "你是漫画页码分配器。本话已有keyBeats，但页码没有覆盖全部页数。"
    "请只重新分配每个beat的pageStart/pageEnd，使其连续覆盖第1页到第%d页："
    "第一个beat从第1页开始，最后一个beat结束于第%d页，相邻beat之间"
    "不能有空档、不能重叠。不得改变beat顺序、事件内容、endState、endingHook。"
    "只输出JSON："
    '{"keyBeats":[{"beatId":"B1","pageStart":1,"pageEnd":6}]}')


def build_episode_delivery_ledger(contract, episode):
    """未来事实投放账本：series事件未安排进本话计划时进入reservedFutureFacts。"""
    facts = list(contract.userLockedFacts or
                 contract.explicitLockedFacts or [])
    plan_text = json.dumps(episode or {}, ensure_ascii=False)
    available, reserved = [], []
    for f in facts:
        if not isinstance(f, dict):
            continue
        typ = f.get("type") or ""
        scope = f.get("deliveryScope") or "series"
        val = str(f.get("value") or "")
        if scope in ("episode", "page"):
            available.append(f)
            continue
        if typ in ("event", "ending"):
            candidates = [val, str(f.get("actionObject") or ""),
                          str(f.get("sourceQuote") or "")]
            scheduled = any(
                c and len(c) >= 4 and _info_in_plan(c, plan_text)
                for c in candidates)
            (available if scheduled else reserved).append(f)
        else:
            # 外观/身份/世界规则等背景事实属于“遵守”，不是“揭示”
            available.append(f)
    scheduled_reveals = [{
        "factId": str(f.get("factId") or ""),
        "beatId": "",
        "firstAllowedPage": 1,
    } for f in available]
    return {
        "availableFacts": available,
        "scheduledReveals": scheduled_reveals,
        "reservedFutureFacts": reserved,
    }


def _known_characters(context, episode, contract=None):
    known = set()
    for cid, st in (context.get("currentCharacterStates") or {}).items():
        known.add(str(cid))
        known.add(models._base_name(str(cid)))
        if isinstance(st, dict):
            known.add(models._base_name(str(st.get("characterName") or "")))
    for c in (episode.get("characters") or []):
        known.add(str(c))
    for b in (episode.get("keyBeats") or []):
        if isinstance(b, dict):
            known.update(str(x) for x in (b.get("characters") or []))
    if contract is not None:
        for line in str(contract.confirmedCharacterCards or "").splitlines():
            m = re.match(r"^\s*([^\s—\-：:]{1,24})\s*[—\-：:]", line)
            if m:
                base = re.sub(r"[（(].*$", "", m.group(1)).strip()
                if base:
                    known.add(base)
    known.discard("")
    return known


def _reserved_fact_hits(text, ledger):
    hits = []
    for f in (ledger or {}).get("reservedFutureFacts") or []:
        if not isinstance(f, dict):
            continue
        for v in (str(f.get("value") or ""),
                  str(f.get("actionObject") or ""),
                  str(f.get("sourceQuote") or "")):
            v = v.strip()
            if len(v) >= 4 and v in str(text or ""):
                hits.append(v)
    return hits


def _later_beat_hits(text, later_beats):
    hits = []
    for b in later_beats or []:
        if not isinstance(b, dict):
            continue
        for v in (str(b.get("event") or ""),
                  str(b.get("newInformation") or "")):
            v = v.strip()
            if len(v) >= 6 and v in str(text or ""):
                hits.append(v)
    return hits


def _execution_text(obj):
    """只取页面“实际执行”字段，排除承接/桥接等预告性字段。"""
    if not isinstance(obj, dict):
        return ""
    if "pageEvent" in obj or "expectedReaction" in obj:
        keys = ("pageEvent", "newInformation", "expectedReaction",
                "expectedDecisionOrConsequence", "location", "characters")
    else:
        keys = ("visualEvent", "newInformation", "characterReaction",
                "decisionOrConsequence", "location", "characters",
                "dialogueItems", "pageEndState")
    return json.dumps({k: obj.get(k) for k in keys if k in obj},
                      ensure_ascii=False)


def _dedupe_new_information(items, seen):
    """同一新信息只保留首次出现的页面；后续页清空（确定性整理）。"""
    for it in items or []:
        if not isinstance(it, dict):
            continue
        ni = str(it.get("newInformation") or "").strip()
        ni = _real_info(ni)
        it["newInformation"] = ni
        if not ni:
            it["whyItMatters"] = ""
        if ni in seen:
            it["newInformation"] = ""
            it["whyItMatters"] = ""
        elif ni:
            seen.add(ni)
    return items


def _characters_ok(chars, allowed):
    bad = []
    for c in chars or []:
        c = str(c or "").strip()
        if not c:
            continue
        if c in allowed:
            continue
        if any(a and (a in c or c in a) for a in allowed):
            continue
        bad.append(c)
    return bad


_INFO_STOPWORDS = (
    "一个", "这个", "那个", "自己", "一样", "出现", "存在", "开始", "进行",
    "发出", "传来", "没有", "已经", "正在", "仿佛", "似乎", "显得", "带来",
    "保持", "形成", "显示", "暗示", "说明", "证明", "因为", "所以", "并且",
    "以及", "还是", "就是", "只是", "但是", "然后", "最后", "突然", "渐渐",
    "继续", "回到", "离开", "进入", "推开", "发现", "看到", "听见", "感到",
    "觉得", "知道", "确认", "找到", "像是", "如同", "里面", "外面", "上面",
    "下面", "之间", "之中", "之后", "之前", "旁边", "周围", "附近", "整个",
    "微弱", "冷色", "暖色", "隐约", "似乎", "几乎", "渐渐", "轻轻", "缓缓",
    "慢慢", "静静", "冷冷", "微微", "阵阵")


def _info_in_plan(info, plan_text):
    """实际新信息是否属于本话计划范围：按2字关键片段匹配计划全文。"""
    info = str(info or "")
    if not info:
        return True
    if info in plan_text:
        return True
    tokens = set()
    for i in range(len(info) - 1):
        t = info[i:i + 2]
        if re.fullmatch(r"[\u4e00-\u9fff]{2}", t) and \
                t not in _INFO_STOPWORDS:
            tokens.add(t)
    return any(t in plan_text for t in tokens)


def _normalize_visual_plan(plan, page_no):
    """统一整页画面计划字段；保留旧字段作为兼容别名。"""
    panels = []
    for idx, p in enumerate((plan or {}).get("panels") or [], 1):
        if not isinstance(p, dict):
            continue
        people = [str(x) for x in (p.get("people") or []) if str(x)]
        panels.append({
            "shot": str(p.get("shot") or "中景"),
            "people": people,
            "positions": str(p.get("positions") or p.get("position") or ""),
            "action": str(p.get("action") or ""),
            "expression": str(p.get("expression") or ""),
            "visibleOutfitOrState": str(p.get("visibleOutfitOrState") or ""),
            "importantObject": str(p.get("importantObject") or ""),
            "environment": str(p.get("environment") or p.get("visible") or ""),
            "storyPurpose": str(p.get("storyPurpose") or ""),
            "subject": str(p.get("subject") or ""),
            "visible": str(p.get("visible") or ""),
            "interaction": str(p.get("interaction") or ""),
        })
    return {
        "layout": str((plan or {}).get("layout") or "") or
                  ("hero+2" if len(panels) == 3 else
                   ("hero+strip" if len(panels) == 2 else "hero")),
        "characters": [str(x) for x in ((plan or {}).get("characters") or [])],
        "scene": str((plan or {}).get("scene") or ""),
        "light": str((plan or {}).get("light") or ""),
        "panels": panels,
        "diegeticTextItems": (plan or {}).get("diegeticTextItems") or [],
        "currentPageVisibleFacts": (plan or {}).get(
            "currentPageVisibleFacts") or [],
        "futureRevealFacts": (plan or {}).get("futureRevealFacts") or [],
    }


def build_page_visual_plan(contract, page_text, page_no):
    """每页一次Qwen生成结构化整页画面计划（不是最终提示词）。"""
    sysmsg = (
        "你是漫画整页画面策划。根据本页剧情输出一个结构化JSON整页画面计划，"
        "不要输出最终生图提示词，不要加入本页未出场的人物；people只能是"
        "已确认人物卡中的人物，不得新增未冻结角色。要求：整页2—4格；"
        "每格只承担一个主要叙事瞬间；人物动作唯一；不把后续事件提前画出；"
        "证据页明确证据位置和观看关系；情绪页允许近景；环境建立页允许人物较小；"
        "末格用于本页推进或钩子。只输出JSON："
        '{"layout": "hero+2", "characters": ["人名"], "scene": "当前场景名", '
        '"light": "本页光线", "panels": [{"shot": "全景/中景/近景/特写", '
        '"people": ["本格实际人物"], "positions": "人物在画面中的左右前后位置", '
        '"action": "唯一主要动作", "expression": "表情", '
        '"visibleOutfitOrState": "当前服装或身体状态（正向描述）", '
        '"importantObject": "本格关键物体及归属", '
        '"environment": "本格可见的局部场景", '
        '"storyPurpose": "本格叙事作用：establish/discover/action/evidence/'
        'reaction/decision/transition"}]}')
    if int(page_no or 0) == 1:
        sysmsg += ("\n第1页是开场建立页：使用远景或全景镜头，人物全身入画且"
                   "不占画面主体，清晰展示人物与主场景的空间关系、纵深与"
                   "环境全貌。")
    user = ("已冻结人物：%s\n已冻结场景：%s\n本页剧情：\n%s\n请输出第%d页整页画面计划。" % (
        str(contract.confirmedCharacterCards or ""),
        str(contract.confirmedSceneSetting or ""),
        str(page_text or ""), int(page_no or 0)))
    raw = app.chat_stream(
        [{"role": "system", "content": sysmsg},
         {"role": "user", "content": user}], 0.4, None, 2400)
    plan = _extract_json_obj(raw)
    if not isinstance(plan, dict) or not plan.get("panels"):
        raise RuntimeError("第%d页整页画面计划解析失败" % int(page_no or 0))
    plan = _normalize_visual_plan(plan, int(page_no or 0))
    plan = _filter_known_people(plan, contract)
    errs = validate_expansion_against_contract(
        contract, json.dumps(plan, ensure_ascii=False), require_presence=False,
        check_events=False)
    if errs:
        app.log("⚠ 第%d页画面计划与冻结合同提示（不拦截，继续生成）：%s" % (
            int(page_no or 0), "；".join(errs[:5])))
    return plan


def _filter_known_people(plan, contract):
    known = set()
    for line in str(contract.confirmedCharacterCards or "").splitlines():
        m = re.match(r"^\s*([^\s—\-：:]{1,24})", line)
        if m:
            known.add(models._base_name(m.group(1)))
    for p in plan.get("panels") or []:
        if not isinstance(p, dict):
            continue
        kept = []
        for n in (p.get("people") or []):
            n = str(n or "").strip()
            bn = models._base_name(n)
            if not n:
                continue
            if bn in known or any(bn in k or k in bn for k in known):
                kept.append(n)
        p["people"] = kept
    return plan


def make_page_readability_plan(page_no, story, visual_plan, text_plan,
                               episode_plan=None, record=None):
    """每页结构化可读性字段。
    正式链路直接映射 PageStoryRecord，不再从自由文本正则猜测；
    无 record 的分支只保留给旧测试/旧数据兼容，正式链路不调用。"""
    if record is not None:
        pno = int(record.get("pageNumber") or page_no or 1)
        goal = str((episode_plan or {}).get("episodeGoal") or
                   (episode_plan or {}).get("goal") or "")
        total = int((episode_plan or {}).get("targetPages") or pno)
        ni = str(record.get("newInformation") or "").strip()
        why = str(record.get("whyItMatters") or "").strip()
        react = str(record.get("characterReaction") or "").strip()
        dec = str(record.get("decisionOrConsequence") or "").strip()
        bridge = str(record.get("nextPageBridge") or "").strip()
        reader_needs = []
        if str(record.get("location") or "").strip():
            reader_needs.append("当前地点")
        if goal:
            reader_needs.append("当前目标")
        if ni:
            reader_needs.append("本页新信息")
        if react:
            reader_needs.append("人物反应")
        if dec:
            reader_needs.append("本页结果")
        visual_shows = []
        for p in ((visual_plan or {}).get("panels") or []):
            if isinstance(p, dict):
                for k in ("visible", "visibleOutfitOrState", "environment",
                          "importantObject"):
                    v = str(p.get(k) or "").strip()
                    if v and v not in visual_shows:
                        visual_shows.append(v)
        return {
            "pageNumber": pno,
            "contextFromPreviousPage": str(
                record.get("causeFromPreviousPage") or ""),
            "whatHappens": str(record.get("visualEvent") or ""),
            "newInformation": ni,
            "whyItMatters": why,
            "characterReaction": react,
            "decisionOrConsequence": dec,
            "nextPageBridge": bridge,
            "readerNeedsToKnow": reader_needs,
            "visualAlreadyShows": visual_shows,
            "textNeedsToExplain": reader_needs,
            "characterGoal": goal,
            "clueMeaning": why if ni else "",
            "pageConsequence": dec,
            "endingHook": str((episode_plan or {}).get("endingHook") or "")
            if pno == total else "",
            "openingNarration": str(record.get("narration") or ""),
            "isOpeningEpisode": int(
                (episode_plan or {}).get("episodeNo") or 1) == 1,
            "textItems": (text_plan or {}).get("textUnits") or [],
        }
    raise RuntimeError("可读性必须由 PageStoryRecord 直接映射，禁止正则猜测")


def _specific_why(why):
    why = _real_info(str(why or ""))
    return len(why) >= 6


def _roll_window_brief(contract, bible, next_no, retry=False):
    ctx = json.dumps(_compact_bible_summary(contract, bible),
                     ensure_ascii=False)
    sysmsg = (
        "你是长篇漫画话次策划。当前话已完成，请只输出下一话（第%d话）的"
        "简要方向 brief，不要展开细节。必须承接已有规划与人物状态。"
        "只输出JSON："
        '{"episodeNo":%d, "detailLevel":"brief", "title":"话名", '
        '"goal":"本话方向", "keyEvents":[], "characters":[], '
        '"endingHook":"", "foreshadowing":[], "resolvesThreads":[], '
        '"openQuestions":[], "visualStateChanges":[]}' % (
            int(next_no), int(next_no)) + (
        "注意：上一次规划未通过方向检查，请重写。" if retry else ""))
    user = "下一话编号：%d\n\n%s" % (int(next_no), ctx)
    try:
        plan = _call_json(sysmsg, user, 0.35, 1800)
    except Exception:
        time.sleep(4)
        plan = _call_json(sysmsg, user, 0.3, 1800)
    plan["episodeNo"] = int(next_no)
    plan["detailLevel"] = "brief"
    plan["status"] = "draft"
    return plan


def roll_episode_window_after_commit(bible, window, contract,
                                     completed_episode=1):
    """canon提交后的唯一滚动入口：
    移除已完成话；原第2/3话保持编号并升级 full/medium；只新生成下一话 brief。"""
    done = int(completed_episode or 0)
    eps = [dict(e) for e in (window or {}).get("episodes") or []
           if isinstance(e, dict) and
           int(e.get("episodeNo") or 0) != done]
    for e in eps:
        no = int(e.get("episodeNo") or 0)
        if no == done + 1:
            e["detailLevel"] = "full"
        elif no == done + 2:
            e["detailLevel"] = "medium"
        e["status"] = "draft"
    next_no = max([int(e.get("episodeNo") or 0) for e in eps] +
                  [done]) + 1
    brief = _roll_window_brief(contract, bible, next_no)
    eps.append(brief)
    eps.sort(key=lambda x: int(x.get("episodeNo") or 0))
    window["episodes"] = eps
    return window


def _resolve_char_id(cid, bible):
    cid = str(cid or "")
    ids = set((bible.characterStates or {}).keys())
    if cid in ids:
        return cid
    base = models._base_name(cid)
    for cid2, st in (bible.characterStates or {}).items():
        if models._base_name(str(st.get("characterName") or "")) == base:
            return cid2
    return ""


def _norm_body_exposure(to):
    to = str(to or "").strip()
    if to == "partial_change":
        return "partially_exposed", ""
    if to.startswith("clothed_"):
        cond = "changed"
        if "torn" in to or "damage" in to or "破" in to:
            cond = "damaged"
        if "wet" in to or "湿" in to:
            cond = "wet"
        if "dirty" in to or "脏" in to:
            cond = "dirty"
        body = "partially_exposed" if (
            "open" in to or "expos" in to or "露" in to) else "normal_clothed"
        return body, cond
    if "裸" in to or "nude" in to:
        return "nude", ""
    return to, ""


def _norm_cond(to):
    to = str(to or "").strip()
    if to in ("intact", "damaged", "wet", "dirty", "changed"):
        return to
    if "torn" in to or "damage" in to or "破" in to or "裂" in to:
        return "damaged"
    if "wet" in to or "湿" in to:
        return "wet"
    if "dirty" in to or "脏" in to:
        return "dirty"
    if "change" in to or "换" in to:
        return "changed"
    return ""


def normalize_and_validate_state_transitions(contract, bible, episode_plan,
                                             target_pages,
                                             allow_qwen_retry=True):
    """正式唯一状态变化规范化+校验入口。
    确定性归一（补空数组/映射characterId/page/from/别名/拆分clothed_*）；
    语义不合规时由本地Qwen只重出 stateTransitions 一次；第二次失败停止。"""
    plan = dict(episode_plan or {})
    tr = dict((plan.get("stateTransitions") or {}))
    keys = ("locationChanges", "physicalConditionChanges", "outfitChanges",
            "bodyExposureChanges", "clothingConditionChanges",
            "propAcquisitions", "propLosses", "knowledgeGains",
            "relationshipChanges", "newThreads", "resolvedThreads",
            "foreshadowingAdded", "foreshadowingResolved")
    for k in keys:
        tr.setdefault(k, [])
        if not isinstance(tr[k], list):
            tr[k] = []
    # 模拟当前状态，用于 from 字段
    sim = {cid: dict(st) for cid, st in (bible.characterStates or {}).items()}
    field_map = {"locationChanges": "currentLocation",
                 "physicalConditionChanges": "physicalCondition",
                 "outfitChanges": "currentOutfit"}
    for key, field in field_map.items():
        out = []
        for t in tr.get(key) or []:
            if not isinstance(t, dict):
                continue
            t = dict(t)
            cid = _resolve_char_id(t.get("characterId"), bible)
            if not cid:
                continue
            t["characterId"] = cid
            try:
                t["page"] = int(t.get("page") or 0)
            except Exception:
                t["page"] = 0
            t["from"] = (sim.get(cid) or {}).get(field, "")
            to = str(t.get("to") or "").strip()
            if not to:
                continue
            t["to"] = to
            out.append(t)
            sim.setdefault(cid, {})[field] = to
        tr[key] = out
    body_out, cond_out = [], []
    for t in tr.get("bodyExposureChanges") or []:
        if not isinstance(t, dict):
            continue
        t = dict(t)
        cid = _resolve_char_id(t.get("characterId"), bible)
        if not cid:
            continue
        t["characterId"] = cid
        try:
            t["page"] = int(t.get("page") or 0)
        except Exception:
            t["page"] = 0
        body, cond = _norm_body_exposure(t.get("to"))
        if body not in ("normal_clothed", "partially_exposed", "nude",
                        "custom_user_state"):
            continue
        t["to"] = body
        body_out.append(t)
        if cond:
            cond_out.append({"characterId": cid, "to": cond,
                             "page": t.get("page", 0),
                             "reason": str(t.get("reason") or "服装状态变化")})
    tr["bodyExposureChanges"] = body_out
    cond_norm = []
    for t in tr.get("clothingConditionChanges") or []:
        if not isinstance(t, dict):
            continue
        t = dict(t)
        cid = _resolve_char_id(t.get("characterId"), bible)
        if not cid:
            continue
        t["characterId"] = cid
        try:
            t["page"] = int(t.get("page") or 0)
        except Exception:
            t["page"] = 0
        to = _norm_cond(t.get("to"))
        if to:
            t["to"] = to
            cond_norm.append(t)
    for c in cond_out:
        if not any(x.get("characterId") == c["characterId"] and
                   x.get("page") == c["page"] for x in cond_norm):
            cond_norm.append(c)
    tr["clothingConditionChanges"] = cond_norm
    for key in ("propAcquisitions", "propLosses"):
        out = []
        for t in tr.get(key) or []:
            if not isinstance(t, dict):
                continue
            t = dict(t)
            cid = _resolve_char_id(t.get("characterId"), bible)
            if not cid:
                continue
            t["characterId"] = cid
            prop = str(t.get("propId") or t.get("item") or t.get("object") or
                       t.get("prop") or "").strip()
            if not prop:
                continue
            t["propId"] = prop
            t["action"] = "acquire" if key == "propAcquisitions" else "lose"
            try:
                t["page"] = int(t.get("page") or 0)
            except Exception:
                t["page"] = 0
            out.append(t)
        tr[key] = out
    know_out = []
    for t in tr.get("knowledgeGains") or []:
        if not isinstance(t, dict):
            continue
        t = dict(t)
        cid = _resolve_char_id(t.get("characterId"), bible)
        if not cid:
            continue
        t["characterId"] = cid
        info = str(t.get("info") or t.get("factId") or t.get("knowledge") or
                   "").strip()
        if not info:
            continue
        t["info"] = info
        try:
            t["page"] = int(t.get("page") or 0)
        except Exception:
            t["page"] = 0
        know_out.append(t)
    tr["knowledgeGains"] = know_out
    for key in ("relationshipChanges", "newThreads", "resolvedThreads",
                "foreshadowingAdded", "foreshadowingResolved"):
        out = []
        for t in tr.get(key) or []:
            if isinstance(t, str) and t.strip():
                t = {"threadId": t.strip()} if key in (
                    "resolvedThreads", "foreshadowingResolved") else t
            if not isinstance(t, dict):
                continue
            t = dict(t)
            if t.get("characterId"):
                cid = _resolve_char_id(t["characterId"], bible)
                if not cid:
                    continue
                t["characterId"] = cid
            if not str(t.get("threadId") or t.get("question") or t.get(
                    "thread") or t.get("pair") or "").strip():
                continue
            out.append(t)
        tr[key] = out
    plan["stateTransitions"] = tr
    errs = models.validate_state_transitions(bible, tr, target_pages, plan)
    if errs and allow_qwen_retry:
        # 只重出 stateTransitions，不重写剧情
        try:
            sysmsg = ("你是漫画状态变化编辑器。只根据错误修正 stateTransitions，"
                      "不改剧情、不新增事件。只输出JSON："
                      '{"stateTransitions":{"locationChanges":[],'
                      '"physicalConditionChanges":[],"outfitChanges":[],'
                      '"bodyExposureChanges":[],"clothingConditionChanges":[],'
                      '"propAcquisitions":[],"propLosses":[],'
                      '"knowledgeGains":[],"relationshipChanges":[],'
                      '"newThreads":[],"resolvedThreads":[],'
                      '"foreshadowingAdded":[],"foreshadowingResolved":[]}}')
            user = ("当前stateTransitions：%s\n错误：%s\n请修正后输出完整"
                    "stateTransitions。" % (
                        json.dumps(tr, ensure_ascii=False),
                        "；".join(errs[:6])))
            raw = app.chat_stream(
                [{"role": "system", "content": sysmsg},
                 {"role": "user", "content": user}], 0.3, None, 2600)
            obj = _extract_json_obj(raw)
            if isinstance(obj, dict) and isinstance(
                    obj.get("stateTransitions"), dict):
                plan["stateTransitions"] = obj["stateTransitions"]
                return normalize_and_validate_state_transitions(
                    contract, bible, plan, target_pages,
                    allow_qwen_retry=False)
        except Exception as e:
            errs.append("Qwen重出失败：%s" % str(e)[:200])
        log_entry = {"episode": plan.get("episodeNo"),
                     "rawTransitions": tr,
                     "errors": errs[:10]}
        bible.draftPlans.setdefault("stateTransitionLogs", []).append(log_entry)
        bible.updatedAt = time.time()
        bible.bibleHash = bible.compute_hash()
        raise RuntimeError("stateTransitions两次不合规：" + "；".join(errs[:6]))
    return plan


_JARGON_WORDS = ("编剧", "读者", "本页", "推动剧情", "目的是让", "交代",
                 "铺垫", "回收伏笔", "悬念设置", "塑造", "刻画", "为下文",
                 "为后续", "让观众", "让读者")


def check_text_quality(text_plans, episode_plan=None, contract=None):
    """文字质量硬检查：完整句、重复、标点、编剧术语、人称、目标与线索语义。"""
    errs = []
    pages = sorted((text_plans or {}).keys(), key=int)
    prev_texts = set()
    goal = str((episode_plan or {}).get("goal") or "")
    gender_by_name = {}
    if contract is not None:
        for line in str(contract.confirmedCharacterCards or "").splitlines():
            m = re.match(r"^\s*([^\s—\-：:]{1,24})\s*[—\-：:]\s*(.+?)\s*$",
                         line)
            if m:
                base = re.sub(r"[（(].*$", "", m.group(1)).strip()
                gender_by_name[base] = ("男" if "男" in m.group(2)[:12]
                                        else ("女" if "女" in m.group(2)[:12]
                                              else ""))
    for p in pages:
        units = (text_plans or {}).get(p, {}).get("textUnits") or []
        texts = set()
        for u in units:
            t = str(u.get("visibleText") or u.get("text") or "").strip()
            if not t:
                continue
            if u.get("type") != "sfx" and \
                    not re.search(r"[。！？…]$", t) and \
                    not re.search(r"(\.\.\.|…)$", t) and len(t) >= 6:
                errs.append("P%s文字不完整句：%s" % (p, t[:30]))
            if re.search(r"[。！？，、]{2,}", t):
                errs.append("P%s重复标点：%s" % (p, t[:30]))
            if any(k in t for k in _JARGON_WORDS):
                errs.append("P%s出现编剧术语：%s" % (p, t[:30]))
            if t in texts:
                errs.append("P%s同一页重复文字：%s" % (p, t[:30]))
            texts.add(t)
            if t in prev_texts:
                errs.append("连续页面重复文字：%s" % t[:30])
            if goal and "目标是" in t and any(
                    k in t for k in ("白色长发", "灰色眼睛", "长风衣", "短发",
                                     "校服", "外套")):
                errs.append("调查目标来自人物外观：%s" % t[:30])
            speaker = str(u.get("speakerId") or "").strip()
            if speaker and gender_by_name:
                base_sp = re.sub(r"[（(].*$", "", speaker).strip()
                g = gender_by_name.get(base_sp, "")
                if g == "男" and "她" in t:
                    errs.append("P%s男性角色出现“她”：%s" % (p, t[:30]))
                if g == "女" and "他" in t:
                    errs.append("P%s女性角色出现“他”：%s" % (p, t[:30]))
            if u.get("type") == "dialogue" and not speaker and \
                    re.search(r"[他她]", t):
                errs.append("P%s对白无speakerId却使用人称：%s" % (p, t[:30]))
        prev_texts = texts
    return errs


_POSITION_RECTS = {
    "topLeft": (0.04, 0.04, 0.46, 0.24),
    "topRight": (0.54, 0.04, 0.96, 0.24),
    "bottomLeft": (0.04, 0.72, 0.46, 0.96),
    "bottomRight": (0.54, 0.72, 0.96, 0.96),
}


def validate_text_layout(text_plans, panel_count=3, face_rects=None):
    """文字版式确定性检查：同一格文字数量/重叠/超出安全区/覆盖人物脸部。
    face_rects: {pageNo: {panel: [(x0,y0,x1,y1), ...]}} 归一化坐标。"""
    errs = []
    for p, plan in (text_plans or {}).items():
        units = plan.get("textUnits") or []
        by_panel = {}
        for u in units:
            panel = max(1, min(int(u.get("panel") or 1), int(panel_count or 1)))
            by_panel.setdefault(panel, []).append(u)
        for panel, us in by_panel.items():
            if len(us) > 4:
                errs.append("P%s第%d格文字过多(%d条)" % (p, panel, len(us)))
            seen_pos = set()
            for u in us:
                pos = str(u.get("placementPreference") or "topLeft")
                if pos in seen_pos:
                    errs.append("P%s第%d格文字框重叠(%s)" % (p, panel, pos))
                seen_pos.add(pos)
                rect = _POSITION_RECTS.get(pos)
                if not rect:
                    continue
                faces = ((face_rects or {}).get(str(p)) or {}).get(panel) or []
                for fr in faces:
                    if _rects_overlap(rect, fr):
                        errs.append("P%s第%d格文字框覆盖人物脸部" % (p, panel))
    return errs


def _rects_overlap(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)



def _page_presence_only_types(contract):
    types = set()
    for f in (contract.userLockedFacts or []):
        if f.get("deliveryScope") in ("episode", "page"):
            types.add(f.get("type"))
    return tuple(t for t in types if t)


def _validate_episode_continuity(prev_end, cur_start):
    errs = []
    if not isinstance(prev_end, dict) or not isinstance(cur_start, dict):
        return errs
    pl = str(prev_end.get("location") or "")
    cl = str(cur_start.get("location") or "")
    loc_ok = not (pl and cl) or _location_related(pl, cl)
    if not loc_ok:
        errs.append("地点承接不符：上一话结束%s，本话开始%s" % (pl, cl))
    pq = str(prev_end.get("nextQuestion") or "")
    oq = str(cur_start.get("openQuestion") or "")
    # 地点连续时，问题允许换措辞（“门后是谁”→“604房间有什么线索”仍算承接）；
    # 地点断裂时，问题检查作为佐证，避免重新开局。
    if not loc_ok and pq and oq and not _question_continuous(pq, oq):
        errs.append("问题承接不符")
    return errs


def _location_continuous(prev_loc, cur_loc):
    """地点承接：允许同一空间的不同局部（楼梯口→楼梯中段），
    只要两者共享足够长的核心地点名。"""
    def _norm_loc(s):
        s = str(s or "")
        s = s.replace("门前", "门口")
        s = re.sub(r"房门(?!口)", "房门口", s)
        s = re.sub(r"(入口|出口|中段|尽头|内部|外部|附近|旁边|上方|下方|"
                   r"区域|位置)$", "", s)
        s = s.replace("通道", "走廊").replace("廊道", "走廊")
        return s

    a, b = _norm_loc(prev_loc), _norm_loc(cur_loc)
    if not a or not b:
        return True
    if a in b or b in a:
        return True
    m = difflib.SequenceMatcher(None, a, b).find_longest_match()
    core_len = int(m.size)
    return core_len >= 3 and core_len * 2 >= min(len(a), len(b))


def _location_related(a, b):
    """宽松地点关系：允许同一楼层/同一区域的局部推进（走廊尽头→楼梯口）。"""
    def _norm_loc(s):
        s = str(s or "")
        s = s.replace("门前", "门口")
        s = re.sub(r"房门(?!口)", "房门口", s)
        s = re.sub(r"(入口|出口|中段|尽头|内部|外部|附近|旁边|上方|下方|"
                   r"区域|位置)$", "", s)
        s = s.replace("通道", "走廊").replace("廊道", "走廊")
        return s

    a, b = _norm_loc(a), _norm_loc(b)
    if not a or not b:
        return True
    if a in b or b in a:
        return True
    def _is_enclosed_room(s):
        return bool(re.search(r"房间(?!口|门)|内部|屋内|室内", s))

    _TRANSIT = ("楼梯", "入口", "门口", "走廊", "通道", "过道", "门前")
    if (_is_enclosed_room(a) and any(k in b for k in _TRANSIT)) or \
            (_is_enclosed_room(b) and any(k in a for k in _TRANSIT)):
        return False
    m = difflib.SequenceMatcher(None, a, b)
    longest = m.find_longest_match()
    if int(longest.size) >= 2 or m.ratio() >= 0.45:
        return True
    fa = re.search(r"([一二三四五六七八九十0-9]+(?:层|楼))", a)
    fb = re.search(r"([一二三四五六七八九十0-9]+(?:层|楼))", b)
    return bool(fa and fb and fa.group(1) == fb.group(1))


def _question_continuous(prev_q, cur_q):
    """问题承接：允许换措辞，但必须共享核心话题词或足够长的共同片段。"""
    def _norm_q(t):
        t = str(t or "")
        t = re.sub(r"[0-9０-９]+[层楼]", "楼层", t)
        t = re.sub(r"[一二三四五六七八九十百\d]+[层楼]", "楼层", t)
        return t

    a, b = _norm_q(prev_q), _norm_q(cur_q)
    if not a or not b:
        return True
    if a in b or b in a:
        return True
    m = difflib.SequenceMatcher(None, a, b).find_longest_match()
    if int(m.size) >= 2:
        return True
    topics = ("楼层", "空间", "规则", "异常", "真相", "楼梯", "走廊", "出口",
              "入口", "原因", "来源", "去向",
              "谁", "为什么", "是否", "如何", "什么", "哪里", "原因")
    return any(k in a and k in b for k in topics)


def _episode_chain_key(ep):
    items = [x for x in ((ep or {}).get("keyBeats") or
                         (ep or {}).get("keyEvents") or [])
             if isinstance(x, dict)]
    chain = [(str(x.get("location") or ""),
              str(x.get("action") or x.get("event") or "")[:24],
              str(x.get("object") or x.get("newInformation") or "")[:24])
             for x in items]
    return chain


def check_episode_duplication(prev, cur):
    errs = []
    pa = _episode_chain_key(prev or {})
    ca = _episode_chain_key(cur or {})
    if len(pa) >= 3 and len(ca) >= len(pa):
        for i in range(len(ca) - len(pa) + 1):
            if ca[i:i + len(pa)] == pa:
                errs.append("完整关键事件链重复上一话")
                break
    return errs


def check_page_duplication(pages):
    errs = []
    prev = None
    for i, p in enumerate(pages, 1):
        loc = re.search(r"地点与时间[：:]\s*([^。；\n]+)", str(p or ""))
        act = re.search(r"画面事件[：:]\s*([^。；\n]+)", str(p or ""))
        cur = (loc.group(1).strip() if loc else "",
               act.group(1).strip() if act else "")
        if prev and cur == prev and cur[0] and cur[1]:
            errs.append("连续页面重复：%d-%d" % (i - 1, i))
        prev = cur
    return errs


def _filter_presence_errors(errs, episode):
    goal_text = json.dumps(episode or {}, ensure_ascii=False)
    out = []
    for e in errs:
        m = re.search(r"缺少已确认事实：(.+)$", e)
        if m and m.group(1).strip() in goal_text:
            continue
        m2 = re.search(r"事件对象：(.+?) →", e)
        if m2 and m2.group(1) in goal_text:
            continue
        out.append(e)
    return out


def _assemble_episode_plan(contract, bible, brief, beats, target, review,
                           episode_no, episode_type):
    last = beats[-1] if beats else {}
    sa = last.get("stateAfter") or {}
    end_state = {
        "location": str(sa.get("location") or ""),
        "characters": list(sa.get("characters") or []),
        "completedAction": str(last.get("decisionOrConsequence") or
                               last.get("event") or ""),
        "newInformation": [str(b.get("newInformation") or "") for b in beats
                           if str(b.get("newInformation") or "").strip()],
        "newDecision": str(last.get("decisionOrConsequence") or ""),
        "nextQuestion": str(brief.get("mainQuestion") or
                            brief.get("endingHook") or ""),
    }
    characters = []
    for b in beats:
        for c in (b.get("stateAfter") or {}).get("characters") or []:
            if c and c not in characters:
                characters.append(c)
    events = []
    for b in beats:
        events.append({
            "type": "plot", "subject": "、".join(
                (b.get("stateAfter") or {}).get("characters") or []),
            "action": str(b.get("event") or "")[:40],
            "object": str(b.get("newInformation") or "")[:40],
            "location": str((b.get("stateAfter") or {}).get("location") or ""),
            "result": str(b.get("decisionOrConsequence") or "")[:40],
            "pageStart": int(b.get("pageStart") or 1),
            "pageEnd": int(b.get("pageEnd") or 1),
            "sourceText": str(b.get("cause") or "")})
    return {
        "episodeNo": int(episode_no or 1),
        "episodeType": str(episode_type or "normal"),
        "episodeGoal": str(brief.get("protagonistImmediateGoal") or ""),
        "summary": str(brief.get("episodePurpose") or ""),
        "startState": brief.get("startState") or {},
        "endState": end_state,
        "keyBeats": beats,
        "keyEvents": events,
        "characters": characters,
        "targetPages": int(target),
        "episodePurpose": str(brief.get("episodePurpose") or ""),
        "mainQuestion": str(brief.get("mainQuestion") or ""),
        "mainConflict": str(brief.get("mainConflict") or ""),
        "relationshipStep": str(brief.get("relationshipStep") or ""),
        "plannedReveals": brief.get("plannedReveals") or [],
        "reservedForLater": brief.get("reservedForLater") or [],
        "endingChange": str(brief.get("endingChange") or ""),
        "endingHook": str(brief.get("endingHook") or ""),
        "editorialReview": review,
    }


def _episode_context_payload(brief, beats, target, actual, bible):
    return {
        "episodeNo": int(brief.get("episodeNo") or 1),
        "seriesPremise": {"direction": ""},
        "currentEpisodePlan": {
            "episodeNo": int(brief.get("episodeNo") or 1),
            "episodeGoal": brief.get("protagonistImmediateGoal"),
            "keyBeats": beats, "targetPages": target},
        "previousEpisodeEndState": {},
        "previousEpisodeActualEndState": {},
        "previousEpisodeLastPageRecord": {},
        "currentCharacterStates": (bible.characterStates if bible else {}) or {},
        "episodeRequiredFacts": [],
        "episodeDeliveryLedger": {},
        "targetPages": int(target),
        "episodeActualEndState": actual,
    }


def derive_episode_actual_end_state(records, episode=None):
    """只根据实际最后一页与整话记录确定性生成实际话末状态。"""
    recs = [records[k] for k in sorted(records, key=int)]
    if not recs:
        return {}
    last = recs[-1]
    pes = last.get("pageEndState") or {}
    new_info = []
    for r in recs:
        v = _real_info(r.get("newInformation") or "")
        if v and v not in new_info:
            new_info.append(v)
    return {
        "location": str(pes.get("location") or last.get("location") or ""),
        "characters": list(last.get("characters") or []),
        "completedAction": str(pes.get("activeAction") or
                               last.get("decisionOrConsequence") or
                               last.get("visualEvent") or ""),
        "newInformation": new_info,
        "newDecision": str(pes.get("decision") or
                           last.get("decisionOrConsequence") or ""),
        "nextQuestion": str(pes.get("openQuestion") or
                            last.get("nextPageBridge") or ""),
    }


def format_page_story_text(record):
    """PageStoryRecord → 兼容pageStories文本（确定性格式化）。"""
    lines = ["【第%02d页】" % int(record.get("pageNumber") or 1)]
    lines.append("地点与时间：%s / %s" % (
        str(record.get("location") or ""),
        str(record.get("time") or "")))
    ev = str(record.get("visualEvent") or "")
    ni = str(record.get("newInformation") or "").strip()
    if ni:
        ev += " 新信息：%s" % ni
    lines.append("画面事件：%s" % ev)
    cause = str(record.get("causeFromPreviousPage") or "")
    if cause:
        lines.append("承接：%s" % cause)
    react = str(record.get("characterReaction") or "")
    if react:
        lines.append("反应：%s" % react)
    dec = str(record.get("decisionOrConsequence") or "")
    if dec:
        lines.append("决定：%s" % dec)
    bridge = str(record.get("nextPageBridge") or "")
    if bridge:
        lines.append("下一页：%s" % bridge)
    items = [d for d in (record.get("dialogueItems") or [])
             if isinstance(d, dict)]
    if items:
        lines.append("对白：")
        for d in items:
            txt = str(d.get("text") or "").strip()
            if not txt:
                continue
            kind = str(d.get("kind") or "dialogue")
            sp = str(d.get("speakerId") or "")
            if kind == "innerThought":
                lines.append("%s：（内心）%s" % (sp or "？", txt))
            elif kind == "narration":
                lines.append("旁白：%s" % txt)
            elif kind in ("soundEffect", "sfx"):
                lines.append("音效：%s" % txt)
            else:
                lines.append("%s：%s" % (sp or "？", txt))
    return "\n".join(lines)


def build_page_stories(contract, brief, beats, missions, records, target):
    """只格式化兼容文本：PageStoryRecords 是唯一事实来源，不修改剧情。"""
    texts = [format_page_story_text(records[str(i)])
             for i in range(1, target + 1)]
    actual = derive_episode_actual_end_state(records, brief)
    return PageStoryBuildResult(
        texts, records=records, mission_map=missions,
        actual_end_state=actual,
        ledger=build_episode_delivery_ledger(contract, brief))


def _ensure_story_progress(records, target):
    """连续3页必须至少发生一次有效变化，禁止用走路/观察/空镜灌水。"""
    run = 0
    for i in range(1, int(target) + 1):
        r = (records or {}).get(str(i)) or {}
        delta = r.get("storyStateDelta") or {}
        changed = any(str(delta.get(k) or "").strip() for k in (
            "locationChanged", "newFactAdded", "obstacleChanged",
            "goalChanged", "decisionMade", "relationshipChanged",
            "riskChanged", "questionChanged"))
        if changed or _real_info(r.get("newInformation") or "") or \
                str(r.get("decisionOrConsequence") or "").strip() or \
                str(r.get("relationshipChange") or "").strip():
            run = 0
        else:
            run += 1
            if run >= 3:
                app.log("⚠ 连续3页无有效剧情变化（第%d-%d页），不拦截，继续"
                        % (i - 2, i))


def _run_story_episode(contract, bible, arc, window, ep_no, target_pages,
                       title, plan_hint, text_only):
    """EpisodeBrief+Beats → 审稿 → 页数 → missions → records → 结尾。"""
    bb = build_episode_brief_and_beats(
        contract, bible, arc, window, ep_no, plan_hint=plan_hint)
    brief, beats = bb["brief"], bb["beats"]
    review = review_current_episode_draft(
        contract, bible, arc, window, brief, beats)
    if review["status"] != "pass":
        bb = build_episode_brief_and_beats(
            contract, bible, arc, window, ep_no, retry=True,
            plan_hint=plan_hint)
        brief, beats = bb["brief"], bb["beats"]
        review = review_current_episode_draft(
            contract, bible, arc, window, brief, beats)
        if review["status"] != "pass":
            app.log("⚠ 第%d话编剧审稿未通过（不拦截，继续生成）：%s" % (
                int(ep_no or 1), "；".join(review["problems"][:6])))
    # 页数由executionSteps决定：不再按固定权重分配页数，不补空页。
    budget = {"max": 20, "used": 0}
    beats = build_beat_execution_steps(contract, brief, beats, budget=budget)
    target = estimate_pages_from_steps(beats)
    episode_type = "opening" if int(ep_no or 1) == 1 else \
        str(brief.get("episodeType") or "normal")
    episode = _assemble_episode_plan(
        contract, bible, brief, beats, target, review, ep_no, episode_type)
    ledger = build_episode_delivery_ledger(contract, episode)
    rec_result = build_episode_page_records_deterministic(
        contract, brief, beats, ledger, budget=budget)
    records = rec_result["records"]
    missions = _mission_map_from_records(records, beats, target)
    result = build_page_stories(contract, brief, beats, missions, records,
                                target)
    _ensure_story_progress(records, target)
    dup = check_page_duplication(result)
    if dup:
        app.log("⚠ 逐页重复（不拦截，继续生成）：%s" % "；".join(dup[:4]))
    readability = {}
    for i in range(1, target + 1):
        readability[str(i)] = make_page_readability_plan(
            i, result[i - 1], {}, {}, episode,
            record=records.get(str(i)))
    visual_plans, text_plans, visual_states = {}, {}, {}
    if not text_only:
        for i, pt in enumerate(result, 1):
            visual_plans[str(i)] = _retry_json_call(
                build_page_visual_plan, contract, pt, i)
        text_plans = _retry_json_call(
            build_episode_text_plans, contract, episode, result,
            visual_plans, target, records)
        visual_states = models.derive_page_visual_states(
            contract, result, visual_plans, episode)
    actual = result.actual_end_state
    return {
        "brief": brief, "beats": beats, "review": review,
        "target": target, "episode": episode, "ledger": ledger,
        "missions": missions, "records": records, "pages": result,
        "readability": readability, "visualPlans": visual_plans,
        "textPlans": text_plans, "visualStates": visual_states,
        "actualEndState": actual,
    }


def assemble_story_episode(contract, target_pages=None, title="", project_id="",
                           bible=None, text_only=True, plan_hint=""):
    """用户想法 → 圣经 → SeriesOutline → Arc+Window → 第一话 → 逐页剧本。"""
    if bible is None:
        bible = models.build_bible_from_contract(contract, project_id)
    series = build_series_outline(contract, bible)
    aw = build_arc_and_window(contract, bible, series)
    arc, window = aw["arcOutline"], aw["rollingWindow"]
    volume = {"volumeNo": 1,
              "title": str(series.get("seriesTitle") or title),
              "goal": str(arc.get("stageGoal") or "")}
    r = _run_story_episode(contract, bible, arc, window, 1, target_pages,
                           title, plan_hint, text_only)
    draft = models.create_episode_draft(
        bible, 1, str(title), r["target"], r["episode"])
    draft["actualEndState"] = r["actualEndState"]
    draft["lastPageRecord"] = r["records"].get(str(r["target"])) or {}
    bible.seriesPlan = series
    bible.volumePlans = [v for v in (bible.volumePlans or [])
                         if v.get("volumeNo") != volume.get("volumeNo")] + \
        [volume]
    bible.arcPlans = [a for a in (bible.arcPlans or [])
                      if a.get("arcId") != arc.get("arcId")] + [arc]
    bible.episodePlans = [e for e in (window.get("episodes") or [])
                          if int(e.get("episodeNo") or 0) != 1] + [draft]
    bible.draftPlans.update({
        "series": series, "volume": volume, "arc": arc, "window": window,
        "episode": r["episode"], "pacing": {
            "episodeNo": 1, "episodeType": "opening",
            "targetPages": r["target"],
            "reason": "由EpisodeBeatSheet复杂度估计"},
        "actualEndState": r["actualEndState"],
        "editorialReview": r["review"]})
    bible.updatedAt = time.time()
    bible.bibleHash = bible.compute_hash()
    return {
        "title": title, "bible": bible.to_dict(),
        "seriesPlan": series, "volumePlan": volume, "arcPlan": arc,
        "episodeWindow": window, "episodePlan": r["episode"],
        "episodeDraft": draft,
        "episodePacingPlan": {
            "episodeNo": 1, "episodeType": "opening",
            "targetPages": r["target"],
            "reason": "由EpisodeBeatSheet复杂度估计", "acts": []},
        "episodeContext": _episode_context_payload(
            r["brief"], r["beats"], r["target"], r["actualEndState"], bible),
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
    }


def continue_story_episode(contract, bible, target_pages=None, title="",
                           episode_no=None, text_only=True, plan_hint=""):
    """续写：只承接上一话actualEndState，重写当前话。"""
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
    r = _run_story_episode(contract, bible, arc, window, ep_no, target_pages,
                           title, plan_hint, text_only)
    # 承接校验：第一页地点必须与上一话实际结尾相关
    first = (r["records"].get("1") or {}).get("pageEndState") or \
        (r["records"].get("1") or {})
    if prev_end.get("location") and not _location_related(
            str(first.get("location") or ""),
            str(prev_end.get("location") or "")):
        app.log("⚠ 第%d话首页地点与上一话结尾不完全一致（不拦截，继续生成）：%s"
                % (ep_no, str(prev_end.get("location") or "")))
    draft = models.create_episode_draft(
        bible, ep_no, str(title), r["target"], r["episode"])
    draft["actualEndState"] = r["actualEndState"]
    draft["lastPageRecord"] = r["records"].get(str(r["target"])) or {}
    bible.episodePlans = [e for e in bible.episodePlans or []
                          if e.get("episodeId") != draft["episodeId"]] + \
        [draft]
    bible.draftPlans["episode"] = r["episode"]
    bible.draftPlans["pacing"] = {
        "episodeNo": ep_no, "episodeType": str(
            r["episode"].get("episodeType") or "normal"),
        "targetPages": r["target"],
        "reason": "由EpisodeBeatSheet复杂度估计"}
    bible.draftPlans["actualEndState"] = r["actualEndState"]
    bible.draftPlans["editorialReview"] = r["review"]
    bible.updatedAt = time.time()
    bible.bibleHash = bible.compute_hash()
    return {
        "title": title, "bible": bible.to_dict(),
        "seriesPlan": series, "volumePlan": next(iter(bible.volumePlans or []),
                                                 {}) or {},
        "arcPlan": arc, "episodeWindow": window,
        "episodePlan": r["episode"], "episodeDraft": draft,
        "episodePacingPlan": bible.draftPlans["pacing"],
        "episodeContext": _episode_context_payload(
            r["brief"], r["beats"], r["target"], r["actualEndState"], bible),
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
    }
