# -*- coding: utf-8 -*-
"""stable_fullpage_core 数据模型：冻结锚点、页面人物选择、视觉状态、
长篇故事圣经与持续记忆（0次模型调用）。"""
import hashlib, re, json, time, os, copy

from stable_fullpage_core.contracts import _sha, _value_conflict


def _sentences(text):
    """按句号/分号切整句，保留整句（不从句子中间截断）。"""
    text = str(text or "")
    parts = [p.strip("，,、；;。 ")
             for p in re.split(r"(?<=[。；;])", text) if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _pick_fragments(card_text, keywords, limit=6):
    """原文片段选择：优先按逗号/顿号切分的原短语（整字段），
    无短语命中时回退整句；不新造内容、不从句子中间截断。"""
    text = str(card_text or "")
    out = []
    for p in re.split(r"[，,、；;]", text):
        p = p.strip("，,、；;。 ")
        if p and any(k in p for k in keywords) and p not in out:
            out.append(p)
        if len(out) >= limit:
            break
    if out:
        return out
    for s in _sentences(text):
        if any(k in s for k in keywords) and s not in out:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def _unique(items):
    out = []
    for x in items:
        if x and x not in out:
            out.append(x)
    return out


def _base_name(name):
    return re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", str(name or "")).strip()


def _clone_base_name(name):
    """分身/镜像/替身类人物名映射到本体名（同一外貌，不新增人物）。"""
    n = str(name or "").strip()
    for prefix in ("镜像", "另一个", "假", "复制", "分身", "冒牌", "替身",
                   "第二个"):
        if n.startswith(prefix):
            n = n[len(prefix):]
            break
    for suffix in ("镜像", "分身", "替身", "复制体"):
        if n.endswith(suffix):
            n = n[:-len(suffix)]
            break
    return n.strip()


class FrozenCharacterRenderAnchor:
    """人物渲染锚点：只使用确认人物卡原文片段，禁止改写/截断中间。
    拆分为 identityText / appearanceText / defaultOutfitText / fixedMarksText。"""

    def __init__(self, character_id, character_name, card_text, source_hash=""):
        self.characterId = character_id
        self.characterName = character_name
        self.sourceCardHash = source_hash or _sha(card_text)
        self.ageSexText = _pick_fragments(card_text, (
            "男", "女", "男性", "女性", "岁", "少年", "少女", "青年", "老年"))
        self.bodyText = _pick_fragments(card_text, (
            "身材", "体型", "高", "矮", "结实", "修长", "消瘦", "魁梧",
            "匀称", "纤细", "肩", "比例"))
        self.faceText = _pick_fragments(card_text, (
            "脸", "眉", "眼", "鼻", "唇", "下颌", "颧", "耳"))
        self.hairText = _pick_fragments(card_text, (
            "发", "马尾", "辫", "刘海", "寸头"))
        self.outfitText = _pick_fragments(card_text, (
            "衣", "衫", "袍", "外套", "风衣", "制服", "夹克", "羽绒服",
            "维护服", "宇航服", "裤", "裙", "靴", "鞋", "手套", "腰带",
            "甲", "护肩", "袖"))
        self.fixedMarkText = _pick_fragments(card_text, (
            "疤", "痣", "耳环", "耳坠", "胎记", "纹身", "烧伤", "伤"))
        self.propText = _pick_fragments(card_text, (
            "剑", "刀", "枪", "杖", "工具", "扫描仪", "钥匙", "背包",
            "卷轴", "日记", "匕首"))
        self.identityText = "；".join(_unique(self.ageSexText))
        self.appearanceText = "；".join(_unique(
            list(self.hairText) + list(self.faceText) + list(self.bodyText)))
        self.defaultOutfitText = "；".join(_unique(self.outfitText))
        self.fixedMarksText = "；".join(_unique(self.fixedMarkText))
        ordered = _unique([
            self.identityText, self.appearanceText, self.defaultOutfitText,
            self.fixedMarksText, "；".join(self.propText)])
        self.renderAnchorText = "；".join(x for x in ordered if x)
        self.anchorHash = _sha(self.renderAnchorText)

    def to_dict(self):
        return {k: getattr(self, k) for k in (
            "characterId", "characterName", "sourceCardHash", "ageSexText",
            "bodyText", "faceText", "hairText", "outfitText", "fixedMarkText",
            "propText", "identityText", "appearanceText", "defaultOutfitText",
            "fixedMarksText", "renderAnchorText", "anchorHash")}


class FrozenSceneRenderAnchor:
    """场景渲染锚点：只使用确认场景设定原文片段。
    世界内文字单独记录为 diegeticTextItems，不进Krea提示词。"""

    def __init__(self, scene_id, scene_name, scene_text, source_hash="",
                 diegetic_text_items=None, future_reveal_facts=None):
        self.sceneId = scene_id
        self.sceneName = scene_name
        self.sourceSceneHash = source_hash or _sha(scene_text)
        self.spaceText = _pick_fragments(scene_text, (
            "空间", "室内", "室外", "走廊", "大厅", "舱", "房间", "半室外"))
        self.architectureText = _pick_fragments(scene_text, (
            "结构", "建筑", "墙体", "柱", "梁", "穹顶", "幕墙", "门", "窗",
            "梯", "廊", "舱壁"))
        self.materialText = _pick_fragments(scene_text, (
            "金属", "混凝土", "玻璃", "石材", "木材", "合金", "复合材料",
            "水泥", "砖", "格栅", "木质", "木地板", "地板", "墙纸", "石阶",
            "石墙", "石板"))
        self.landmarkText = _pick_fragments(scene_text, (
            "标志", "标识", "观景", "台", "柜", "座椅", "设备", "灯带",
            "气闸", "阀门", "屏幕"))
        self.lightingText = _pick_fragments(scene_text, ("光", "灯", "照明"))
        self.colorText = _pick_fragments(scene_text, (
            "白", "灰", "银", "蓝", "冷", "暖", "色"))
        ordered = []
        for field in (self.spaceText, self.architectureText, self.materialText,
                      self.landmarkText, self.lightingText, self.colorText):
            for x in field:
                if x and x not in ordered:
                    ordered.append(x)
        self.renderAnchorText = "；".join(ordered)
        self.anchorHash = _sha(self.renderAnchorText)
        self.persistentSceneFacts = [x for x in ordered if x]
        self.futureRevealFacts = list(future_reveal_facts or [])
        if diegetic_text_items is None:
            diegetic_text_items = _extract_diegetic_text_items(scene_text)
        self.diegeticTextItems = list(diegetic_text_items or [])

    def to_dict(self):
        return {k: getattr(self, k) for k in (
            "sceneId", "sceneName", "sourceSceneHash", "spaceText",
            "architectureText", "materialText", "landmarkText",
            "lightingText", "colorText", "renderAnchorText", "anchorHash",
            "diegeticTextItems", "persistentSceneFacts", "futureRevealFacts")}


def _extract_diegetic_text_items(scene_text):
    """从场景原文提取世界内文字（引号内文字/含标识的标志物）。
    返回 [{"text":..., "location":..., "style":...}]，仅在原文存在时非空。"""
    items = []
    for m in re.finditer(r"[“\"]([^”\"]{2,40})[”\"]", str(scene_text or "")):
        items.append({"text": m.group(1).strip(), "location": "场景内可见表面",
                      "style": ""})
    for m in re.finditer(
            r"([A-Za-z][A-Za-z0-9 ]{2,40})(?:标识|标牌|标志|铭牌)", str(scene_text or "")):
        items.append({"text": m.group(1).strip(), "location": "场景内可见表面",
                      "style": ""})
    out, seen = [], set()
    for it in items:
        key = it["text"]
        if key and key not in seen:
            seen.add(key)
            out.append(it)
    return out


def make_character_anchors(character_cards_text):
    """从确认人物卡文本建立全部人物渲染锚点。"""
    anchors = {}
    for line in str(character_cards_text or "").splitlines():
        m = re.match(r"^\s*([^\s—\-：:]{1,24})\s*[—\-：:]\s*(.+?)\s*$", line)
        if m:
            name = m.group(1).strip()
            card = m.group(2).strip()
            anchors[name] = FrozenCharacterRenderAnchor(
                _stable_id(name), name, card).to_dict()
    return anchors


def select_page_character_anchors(page_plan, character_anchors):
    """只返回本页实际出场人物锚点。
    判断来源 page_plan.panels[*].people，不使用全部项目人物。
    人物缺少冻结锚点时抛错，由主链路停止当前阶段。"""
    anchors = character_anchors or {}
    names = []
    for p in (page_plan or {}).get("panels") or []:
        if not isinstance(p, dict):
            continue
        for n in (p.get("people") or []):
            n = str(n or "").strip()
            if n and n not in names:
                names.append(n)
    out, missing = {}, []
    for n in names:
        a = None
        canonical = ""
        candidates = [n, _base_name(n), _clone_base_name(n)]
        for cand in candidates:
            if not cand:
                continue
            if cand in anchors:
                a = anchors[cand]
                canonical = cand
                break
            for key, val in anchors.items():
                if key == cand or _base_name(key) == cand or \
                        cand in key or key in cand:
                    a = val
                    canonical = key
                    break
            if a is not None:
                break
        if a is None:
            missing.append(n)
        else:
            out[canonical or n] = a
    return out


def make_scene_anchors(scene_setting_text, main_scene_name):
    anchors = {}
    anchors[_stable_id(main_scene_name)] = FrozenSceneRenderAnchor(
        _stable_id(main_scene_name), main_scene_name,
        scene_setting_text).to_dict()
    return anchors


def build_location_anchors(contract, stories, visual_plans):
    """剧情驱动地点锚点：每一页画面计划标出的当前地点，首次出现时
    从该页画面计划的局部环境里提取 3—5 个识别点并固定，之后同页复用。
    不再依赖“用户确认的场景卡”，完全跟随剧情地点。"""
    anchors = {}
    for i, story in enumerate(stories or [], 1):
        plan = (visual_plans or {}).get(str(i)) or {}
        name = str(plan.get("scene") or "").strip()
        if not name or name in anchors:
            continue
        envs = []
        for p in (plan.get("panels") or []):
            if not isinstance(p, dict):
                continue
            e = str(p.get("environment") or "").strip()
            if e and e not in envs:
                envs.append(e)
        light = str(plan.get("light") or "").strip()
        identity = "；".join(envs[:5]) or name
        anchors[name] = {
            "sceneId": _stable_id(name),
            "sceneName": name,
            "sourceSceneHash": _sha(name + identity + light),
            "spaceText": [name] + ([light] if light else []),
            "architectureText": [],
            "materialText": [],
            "landmarkText": [identity],
            "lightingText": [light] if light else [],
            "colorText": [],
            "renderAnchorText": "%s（%s）" % (name, identity),
            "anchorHash": _sha(name + identity),
            "diegeticTextItems": [],
            "persistentSceneFacts": [identity],
            "futureRevealFacts": [],
        }
    return anchors


def _stable_id(name):
    return re.sub(r"[^\w\u4e00-\u9fff]+", "_", str(name or "")).strip("_")


# ---------------------------------------------------------------- 视觉状态

def make_character_visual_state(character_id, character_name, card_text,
                                page_no=None, change_source=""):
    """当前视觉状态。bodyExposureState 是状态不是审查等级：
    normal_clothed / partially_exposed / nude / custom_user_state。"""
    card = str(card_text or "")
    if any(k in card for k in ("全裸", "裸体", "赤裸", "赤身", "一丝不挂",
                               "光着身子", "没穿衣服")):
        body = "nude"
    elif any(k in card for k in ("半裸", "只穿", "光着上身")):
        body = "partially_exposed"
    else:
        body = "normal_clothed"
    outfit = "；".join(_pick_fragments(card, (
        "衣", "衫", "袍", "外套", "风衣", "制服", "夹克", "羽绒服", "裤",
        "裙", "靴", "鞋", "手套", "腰带", "袖", "甲")))
    return {
        "characterId": character_id,
        "characterName": character_name,
        "outfitState": outfit,
        "bodyExposureState": body,
        "changeSource": change_source or "user_confirmed_card",
        "changePage": page_no,
    }


def derive_page_visual_states(contract, page_stories=None, visual_plans=None,
                              episode_plan=None):
    """渲染用视觉状态：初始来自确认人物卡，变化只来自
    正式 episodePlan.visualStateChanges（结构化）。不解析自然语言剧情。"""
    anchors = make_character_anchors(contract.confirmedCharacterCards)
    card_by_name = {}
    for line in str(contract.confirmedCharacterCards or "").splitlines():
        m = re.match(r"^\s*([^\s—\-：:]{1,24})\s*[—\-：:]\s*(.+?)\s*$", line)
        if m:
            card_by_name[m.group(1).strip()] = line.strip()
    states = {}
    for name, a in anchors.items():
        states[name] = make_character_visual_state(
            a["characterId"], name, card_by_name.get(name, ""))
    default_outfits = {name: str(st.get("outfitState") or "")
                       for name, st in states.items()}
    out = {}
    stories = list(page_stories or [])
    changes = {}
    for ch in (episode_plan or {}).get("visualStateChanges") or []:
        if not isinstance(ch, dict):
            continue
        start = int(ch.get("startPage") or 1)
        changes.setdefault(start, []).append(ch)
    for i in range(1, len(stories) + 1):
        page_states = {}
        planned_for_page = changes.get(i) or []
        for name, st in states.items():
            base = _base_name(name)
            planned = next((ch for ch in planned_for_page
                            if _base_name(str(ch.get("characterId") or "")) == base
                            or str(ch.get("characterId") or "") == name), None)
            st = dict(st)
            if planned:
                body = str(planned.get("resultingState") or
                           planned.get("changeType") or "")
                if body in ("nude", "partially_exposed", "partial_change",
                            "normal_clothed", "custom_user_state"):
                    if body == "partial_change":
                        body = "partially_exposed"
                    st["bodyExposureState"] = body
                if body == "nude":
                    st["outfitState"] = ""
                elif body == "normal_clothed":
                    st["outfitState"] = str(
                        planned.get("resultOutfit") or
                        default_outfits.get(name) or
                        st.get("outfitState") or "")
                else:
                    st["outfitState"] = str(planned.get("resultOutfit") or
                                            st.get("outfitState") or "")
                st["changeSource"] = "episode_plan"
                st["changePage"] = i
            states[name] = st
            page_states[name] = st
        out[str(i)] = page_states
    return out


# ================================================================ 长篇故事圣经

def _goal_from_text(text):
    m = re.search(
        r"([\u4e00-\u9fff]{2,10}(?:调查|寻找|追查|查明|潜入|探索|阻止|解救|"
        r"取回)[^。；]{2,40})", str(text or ""))
    return m.group(1) if m else ""


def _extract_explicit_props(card_line):
    """只从明确标注提取初始道具：
    手持/佩戴/携带/装备/武器/腰间佩/挎着/背着/持有 后的对象；
    排除服装/配件词（服/裙/袍/衣/甲/裤/靴/鞋/穗/鞘/带/帽/巾/佩件）。"""
    line = str(card_line or "")
    props = []
    for m in re.finditer(
            r"(?:手持|佩戴|携带|装备|武器|腰间佩|挎着|背着|持有)"
            r"([^，。；、]{1,8})", line):
        item = m.group(1).strip("的、，。； ")
        if not item:
            continue
        if re.search(r"[服裙袍衣甲裤靴鞋穗鞘带帽巾佩件]", item):
            continue
        if item not in props:
            props.append(item)
    return props


def _is_protagonist(name, contract):
    base = _base_name(name)
    full = str(name or "")
    if any(k in base or k in full for k in
           ("主角", "调查员", "侦探", "维护员", "少年", "学生", "见习")):
        return True
    return False


def _clean_goal_from_facts(facts):
    """主角核心行动目标：从用户事件事实提取干净的“动词+对象”，排除外观/服装词。"""
    for f in facts or []:
        if f.get("type") == "event" and f.get("actionVerb") in (
                "调查", "寻找", "追查", "查明"):
            obj = str(f.get("actionObject") or "").strip()
            obj = re.sub(r"^(一座|一间|一个|那名|这位|这间|这层|停电的|失踪的)",
                         "", obj)
            if obj and not re.search(r"[发眼衣风外袍裙裤靴鞋发]", obj):
                return "%s%s" % (f.get("actionVerb"), obj)
    return ""


def build_bible_from_contract(contract, project_id=""):
    """从冻结合同建立唯一 LongFormStoryBible（0次模型调用）。"""
    char_states = {}
    anchors = make_character_anchors(contract.confirmedCharacterCards)
    id_by_name = {_base_name(name): a["characterId"]
                  for name, a in anchors.items()}
    user_facts = []
    for f in contract.userLockedFacts:
        nf = dict(f)
        subj = _base_name(f.get("subject") or "")
        nf["subjectId"] = id_by_name.get(subj, "")
        nf["field"] = f.get("type") or ""
        user_facts.append(nf)
    for name, a in anchors.items():
        base = _base_name(name)
        card_line = ""
        for line in str(contract.confirmedCharacterCards or "").splitlines():
            if line.startswith(name) or (base and line.startswith(base)):
                card_line = line
                break
        vs = make_character_visual_state(
            a["characterId"], name, card_line)
        props = _extract_explicit_props(card_line)
        goal = _clean_goal_from_facts(contract.userLockedFacts) \
            if _is_protagonist(name, contract) else ""
        char_states[a["characterId"]] = {
            "characterId": a["characterId"],
            "characterName": name,
            "currentLocation": "",
            "physicalCondition": "",
            "currentOutfit": vs.get("outfitState") or "",
            "bodyExposureState": vs.get("bodyExposureState") or
            "normal_clothed",
            "currentProps": props,
            "currentGoal": goal,
            "clothingCondition": "intact",
            "currentEmotion": "",
            "knownFacts": [],
            "unknownFacts": [],
            "lastUpdatedEpisode": 0,
            "lastUpdatedPage": 0,
        }
    bible = LongFormStoryBible(
        project_id=project_id,
        raw_user_text=contract.rawUserText,
        user_locked_facts=user_facts,
        canon_facts=contract.canonFacts,
        character_states=char_states,
        draft_plans=contract.draftPlans,
    )
    return bible


class LongFormStoryBible:
    """一份项目只保留一份正式长篇故事圣经。"""

    def __init__(self, project_id="", raw_user_text="", user_locked_facts=None,
                 canon_facts=None, series_plan=None, volume_plans=None,
                 arc_plans=None, episode_plans=None, character_states=None,
                 relationship_states=None, world_states=None, timeline=None,
                 unresolved_threads=None, foreshadowing=None,
                 known_information=None, draft_plans=None,
                 last_completed_episode=0, updated_at=0,
                 canon_commit_log=None):
        self.bibleVersion = 1
        self.projectId = str(project_id or "")
        self.rawUserText = str(raw_user_text or "")
        self.userLockedFacts = list(user_locked_facts or [])
        self.canonFacts = list(canon_facts or [])
        self.seriesPlan = dict(series_plan or {})
        self.volumePlans = list(volume_plans or [])
        self.arcPlans = list(arc_plans or [])
        self.episodePlans = list(episode_plans or [])
        self.characterStates = dict(character_states or {})
        self.relationshipStates = list(relationship_states or [])
        self.worldStates = list(world_states or [])
        self.timeline = list(timeline or [])
        self.unresolvedThreads = list(unresolved_threads or [])
        self.foreshadowing = list(foreshadowing or [])
        self.knownInformation = dict(known_information or {})
        self.draftPlans = dict(draft_plans or {})
        self.canonCommitLog = list(canon_commit_log or [])
        self.lastCompletedEpisode = int(last_completed_episode or 0)
        self.updatedAt = float(updated_at or time.time())
        self.bibleHash = self.compute_hash()

    def compute_hash(self):
        payload = json.dumps({
            "bibleVersion": self.bibleVersion,
            "projectId": self.projectId,
            "rawUserText": self.rawUserText,
            "userLockedFacts": self.userLockedFacts,
            "canonFacts": self.canonFacts,
            "seriesPlan": self.seriesPlan,
            "volumePlans": self.volumePlans,
            "arcPlans": self.arcPlans,
            "episodePlans": self.episodePlans,
            "characterStates": self.characterStates,
            "relationshipStates": self.relationshipStates,
            "worldStates": self.worldStates,
            "timeline": self.timeline,
            "unresolvedThreads": self.unresolvedThreads,
            "foreshadowing": self.foreshadowing,
            "knownInformation": self.knownInformation,
            "draftPlans": self.draftPlans,
            "canonCommitLog": self.canonCommitLog,
            "lastCompletedEpisode": self.lastCompletedEpisode,
        }, ensure_ascii=False, sort_keys=True)
        return _sha(payload)

    def validate(self):
        errs = []
        if not self.rawUserText:
            errs.append("故事圣经缺少rawUserText")
        if self.bibleHash != self.compute_hash():
            errs.append("故事圣经哈希不一致")
        return errs

    def to_dict(self):
        return self.__dict__

    @classmethod
    def from_dict(cls, d):
        d = copy.deepcopy(d or {})
        b = cls(
            project_id=d.get("projectId", ""),
            raw_user_text=d.get("rawUserText", ""),
            user_locked_facts=d.get("userLockedFacts", []),
            canon_facts=d.get("canonFacts", []),
            series_plan=d.get("seriesPlan", {}),
            volume_plans=d.get("volumePlans", []),
            arc_plans=d.get("arcPlans", []),
            episode_plans=d.get("episodePlans", []),
            character_states=d.get("characterStates", {}),
            relationship_states=d.get("relationshipStates", []),
            world_states=d.get("worldStates", []),
            timeline=d.get("timeline", []),
            unresolved_threads=d.get("unresolvedThreads", []),
            foreshadowing=d.get("foreshadowing", []),
            known_information=d.get("knownInformation", {}),
            draft_plans=d.get("draftPlans", {}),
            canon_commit_log=d.get("canonCommitLog", []),
            last_completed_episode=d.get("lastCompletedEpisode", 0),
            updated_at=d.get("updatedAt", 0),
        )
        b.bibleHash = d.get("bibleHash", b.bibleHash)
        return b


def migrate_legacy_longform_memory(bible, script):
    """旧 longFormMemory 只做兼容读取迁移到 bible，不再作为正式写入目标。"""
    old = script.get("longFormMemory")
    if not isinstance(old, dict) or not bible:
        return bible
    if not bible.characterStates and isinstance(old.get("characterStates"), dict):
        for cid, st in old["characterStates"].items():
            if isinstance(st, dict):
                bible.characterStates.setdefault(cid, {})
                bible.characterStates[cid].update({
                    "currentLocation": st.get("location", ""),
                    "physicalCondition": st.get("injuries", [""])[0] if
                    st.get("injuries") else "",
                    "currentProps": st.get("heldItems") or
                    st.get("equipment") or [],
                    "knownFacts": st.get("knownFacts") or [],
                })
    if isinstance(old.get("unresolvedThreads"), list):
        for t in old["unresolvedThreads"]:
            if isinstance(t, dict) and t.get("threadId") and \
                    t not in bible.unresolvedThreads:
                bible.unresolvedThreads.append(t)
    if isinstance(old.get("foreshadowing"), list):
        for t in old["foreshadowing"]:
            if isinstance(t, dict) and t not in bible.foreshadowing:
                bible.foreshadowing.append(t)
    bible.updatedAt = time.time()
    bible.bibleHash = bible.compute_hash()
    return bible


def _canon_fact_key(f):
    return (str(f.get("type") or ""), str(f.get("subject") or ""),
            str(f.get("action") or ""), str(f.get("object") or ""),
            str(f.get("location") or ""))



def _canon_fact_key(f):
    return (str(f.get("type") or ""), str(f.get("subject") or ""),
            str(f.get("action") or ""), str(f.get("object") or ""),
            str(f.get("location") or ""))


# ================================================================ 本话状态机

_EPISODE_STATUSES = ("draft", "production_locked", "completed_canon")


def _episode_plan_hash(ep):
    payload = {k: v for k, v in ep.items()
               if k not in ("planHash", "productionLockedAt", "completedAt",
                            "canonCommitId", "status")}
    return _sha(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def create_episode_draft(bible, episode_no, title, target_pages, plan=None):
    """唯一创建本话草稿的入口；状态 draft。"""
    ep_no = int(episode_no or 0)
    ep_id = "ep%d_%s" % (ep_no, _sha(str(bible.projectId or ""))[:8])
    ep = {
        "episodeId": ep_id,
        "episodeNo": ep_no,
        "status": "draft",
        "title": str(title or ""),
        "targetPages": int(target_pages or 8),
        "planHash": "",
        "productionLockedAt": 0,
        "completedAt": 0,
        "canonCommitId": "",
    }
    if isinstance(plan, dict):
        ep.update({k: v for k, v in plan.items()
                   if k not in ("episodeId", "episodeNo", "status",
                                "targetPages", "planHash",
                                "productionLockedAt", "completedAt",
                                "canonCommitId")})
    ep["planHash"] = _episode_plan_hash(ep)
    return ep


def lock_episode_for_production(episode):
    """唯一锁定入口：draft → production_locked。"""
    if not isinstance(episode, dict):
        raise RuntimeError("episode必须是dict")
    if episode.get("status") != "draft":
        raise RuntimeError("episode状态不是draft，不能锁定")
    episode["status"] = "production_locked"
    episode["productionLockedAt"] = time.time()
    episode["planHash"] = _episode_plan_hash(episode)
    return episode


_BAD_PROP_WORDS = ("疲惫", "微笑", "决心", "枚", "这里", "一丝", "隐忍")
_ACTION_WORDS = ("走进", "看向", "站在", "抬头", "握着", "查看", "转身",
                 "拿起", "离开", "坐下", "蹲下", "跑向", "来到", "伸手",
                 "独自", "站")


def validate_state_transitions(bible, transitions, target_pages,
                               episode_plan=None):
    """结构化状态变化校验：角色存在、页码范围内、from一致、to非空、
    道具为明确对象、地点为ID、不违反userLockedFacts。"""
    errs = []
    transitions = transitions or {}
    target = int(target_pages or 8)
    char_ids = set((bible.characterStates or {}).keys())
    char_names = {_base_name(st.get("characterName") or ""): cid
                  for cid, st in (bible.characterStates or {}).items()}

    def resolve_char(cid):
        cid = str(cid or "")
        if cid in char_ids:
            return cid
        return char_names.get(_base_name(cid), "")

    def check_page(p, label):
        try:
            pn = int(p or 0)
        except Exception:
            pn = 0
        if not (1 <= pn <= target):
            errs.append("%s页码越界：%s（目标%s页）" % (label, p, target))
        return pn

    def char_state(cid):
        return (bible.characterStates or {}).get(cid) or {}

    sim = {cid: dict(st) for cid, st in (bible.characterStates or {}).items()}

    for t in transitions.get("locationChanges") or []:
        if not isinstance(t, dict):
            continue
        cid = resolve_char(t.get("characterId"))
        if not cid:
            errs.append("locationChanges.characterId不存在：%s" % t.get("characterId"))
        to = str(t.get("to") or "").strip()
        if not to:
            errs.append("locationChanges.to为空")
        if len(to) > 12 or any(w in to for w in _ACTION_WORDS):
            errs.append("locationChanges.to不是地点ID：%s" % to)
        check_page(t.get("page"), "locationChanges")
        if not str(t.get("reason") or "").strip():
            errs.append("locationChanges.reason为空")
        if cid and str(t.get("from") or "") != str(
                sim.get(cid, {}).get("currentLocation") or ""):
            errs.append("locationChanges.from与当前状态不一致：%s" % t.get("from"))
        if cid and not errs and str(t.get("to") or "").strip():
            sim.setdefault(cid, {})["currentLocation"] = str(t["to"]).strip()
    for t in transitions.get("physicalConditionChanges") or []:
        if not isinstance(t, dict):
            continue
        cid = resolve_char(t.get("characterId"))
        if not cid:
            errs.append("physicalConditionChanges.characterId不存在")
        if not str(t.get("to") or "").strip():
            errs.append("physicalConditionChanges.to为空")
        check_page(t.get("page"), "physicalConditionChanges")
    for t in transitions.get("outfitChanges") or []:
        if not isinstance(t, dict):
            continue
        cid = resolve_char(t.get("characterId"))
        if not cid:
            errs.append("outfitChanges.characterId不存在")
        to = str(t.get("to") or "").strip()
        if not to:
            errs.append("outfitChanges.to为空")
        check_page(t.get("page"), "outfitChanges")
        if cid and to:
            for f in bible.userLockedFacts or []:
                if f.get("subjectId") == cid and f.get("field") == "outfit"                         and _value_conflict(str(f.get("value") or ""), to):
                    errs.append("outfitChanges与用户锁定服装冲突：%s" % to)
    for t in transitions.get("bodyExposureChanges") or []:
        if not isinstance(t, dict):
            continue
        cid = resolve_char(t.get("characterId"))
        if not cid:
            errs.append("bodyExposureChanges.characterId不存在")
        if str(t.get("to") or "").strip() not in (
                "normal_clothed", "partially_exposed", "partial_change",
                "nude",
                "custom_user_state"):
            errs.append("bodyExposureChanges.to非法：%s" % t.get("to"))
        check_page(t.get("page"), "bodyExposureChanges")
    for t in transitions.get("clothingConditionChanges") or []:
        if not isinstance(t, dict):
            continue
        cid = resolve_char(t.get("characterId"))
        if not cid:
            errs.append("clothingConditionChanges.characterId不存在")
        if str(t.get("to") or "").strip() not in (
                "intact", "damaged", "wet", "dirty", "changed"):
            errs.append("clothingConditionChanges.to非法：%s" % t.get("to"))
        check_page(t.get("page"), "clothingConditionChanges")
        if not str(t.get("reason") or "").strip():
            errs.append("clothingConditionChanges.reason为空")
    for t in list(transitions.get("propAcquisitions") or []) +             list(transitions.get("propLosses") or []):
        if not isinstance(t, dict):
            continue
        cid = resolve_char(t.get("characterId"))
        if not cid:
            errs.append("道具变化characterId不存在")
        prop = str(t.get("propId") or "").strip()
        if not prop or len(prop) > 8 or any(
                w in prop for w in _BAD_PROP_WORDS):
            errs.append("道具不是明确对象：%s" % prop)
        if str(t.get("action") or "") not in ("acquire", "lose"):
            errs.append("道具action必须是acquire/lose")
        check_page(t.get("page"), "道具变化")
    for t in transitions.get("knowledgeGains") or []:
        if not isinstance(t, dict):
            continue
        cid = resolve_char(t.get("characterId"))
        if not cid:
            errs.append("knowledgeGains.characterId不存在")
        if not str(t.get("info") or t.get("factId") or "").strip():
            errs.append("knowledgeGains缺少info/factId")
        check_page(t.get("page"), "knowledgeGains")
    for t in transitions.get("relationshipChanges") or []:
        if not isinstance(t, dict):
            continue
        if not str(t.get("pair") or "").strip() or                 not str(t.get("to") or "").strip():
            errs.append("relationshipChanges缺少pair/to")
        check_page(t.get("page"), "relationshipChanges")
    for t in transitions.get("newThreads") or []:
        if not isinstance(t, dict) or not str(t.get("threadId") or "").strip():
            errs.append("newThreads缺少threadId")
    for t in transitions.get("resolvedThreads") or []:
        tid = str(t.get("threadId") or t if isinstance(t, str) else
                   (t or {}).get("threadId") or "")
        if not tid:
            errs.append("resolvedThreads缺少threadId")
    for t in transitions.get("foreshadowingAdded") or []:
        if not isinstance(t, dict) or not str(t.get("thread") or "").strip():
            errs.append("foreshadowingAdded缺少thread")
    for t in transitions.get("foreshadowingResolved") or []:
        tid = str(t.get("threadId") or t if isinstance(t, str) else
                   (t or {}).get("threadId") or "")
        if not tid:
            errs.append("foreshadowingResolved缺少threadId")
    return errs


def _apply_state_transitions(bible, episode_no, transitions, episode_plan=None):
    """唯一状态应用入口：只读取结构化 stateTransitions，不解析自然语言。"""
    ep_no = int(episode_no or 0)
    transitions = transitions or {}
    char_ids = set((bible.characterStates or {}).keys())
    char_names = {_base_name(st.get("characterName") or ""): cid
                  for cid, st in (bible.characterStates or {}).items()}

    def resolve_char(cid):
        cid = str(cid or "")
        if cid in char_ids:
            return cid
        return char_names.get(_base_name(cid), "")

    def touch(cid, page):
        st = (bible.characterStates or {}).get(cid)
        if st:
            st["lastUpdatedEpisode"] = ep_no
            st["lastUpdatedPage"] = int(page or 0)

    for t in transitions.get("locationChanges") or []:
        if not isinstance(t, dict):
            continue
        cid = resolve_char(t.get("characterId"))
        if cid and str(t.get("to") or "").strip():
            bible.characterStates[cid]["currentLocation"] = str(t["to"]).strip()
            touch(cid, t.get("page"))
    for t in transitions.get("physicalConditionChanges") or []:
        if not isinstance(t, dict):
            continue
        cid = resolve_char(t.get("characterId"))
        if cid and str(t.get("to") or "").strip():
            bible.characterStates[cid]["physicalCondition"] = str(t["to"]).strip()
            touch(cid, t.get("page"))
    for t in transitions.get("outfitChanges") or []:
        if not isinstance(t, dict):
            continue
        cid = resolve_char(t.get("characterId"))
        if cid and str(t.get("to") or "").strip():
            bible.characterStates[cid]["currentOutfit"] = str(t["to"]).strip()
            touch(cid, t.get("page"))
    for t in transitions.get("bodyExposureChanges") or []:
        if not isinstance(t, dict):
            continue
        cid = resolve_char(t.get("characterId"))
        if cid and str(t.get("to") or "").strip():
            to = str(t["to"]).strip()
            if to == "partial_change":
                to = "partially_exposed"
            bible.characterStates[cid]["bodyExposureState"] = to
            touch(cid, t.get("page"))
    for t in transitions.get("clothingConditionChanges") or []:
        if not isinstance(t, dict):
            continue
        cid = resolve_char(t.get("characterId"))
        if cid and str(t.get("to") or "").strip():
            bible.characterStates[cid]["clothingCondition"] = str(t["to"]).strip()
            touch(cid, t.get("page"))
    for t in transitions.get("propAcquisitions") or []:
        if not isinstance(t, dict):
            continue
        cid = resolve_char(t.get("characterId"))
        if cid:
            props = bible.characterStates[cid].setdefault("currentProps", [])
            prop = str(t.get("propId") or "").strip()
            if prop and prop not in props:
                props.append(prop)
            touch(cid, t.get("page"))
    for t in transitions.get("propLosses") or []:
        if not isinstance(t, dict):
            continue
        cid = resolve_char(t.get("characterId"))
        if cid:
            props = bible.characterStates[cid].setdefault("currentProps", [])
            prop = str(t.get("propId") or "").strip()
            if prop in props:
                props.remove(prop)
            touch(cid, t.get("page"))
    for t in transitions.get("knowledgeGains") or []:
        if not isinstance(t, dict):
            continue
        cid = resolve_char(t.get("characterId"))
        if cid:
            info = str(t.get("factId") or t.get("info") or "").strip()
            if info:
                known = bible.characterStates[cid].setdefault("knownFacts", [])
                if info not in known:
                    known.append(info)
                bible.knownInformation.setdefault(cid, [])
                if info not in bible.knownInformation[cid]:
                    bible.knownInformation[cid].append(info)
            touch(cid, t.get("page"))
    for t in transitions.get("relationshipChanges") or []:
        if not isinstance(t, dict):
            continue
        pair = str(t.get("pair") or "").strip()
        to = str(t.get("to") or "").strip()
        if pair and to:
            existing = next((r for r in bible.relationshipStates
                             if r.get("pair") == pair), None)
            if existing:
                existing["status"] = to
                existing["lastUpdatedEpisode"] = ep_no
            else:
                bible.relationshipStates.append({
                    "pair": pair, "status": to,
                    "lastUpdatedEpisode": ep_no})
    for t in transitions.get("newThreads") or []:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("threadId") or "").strip()
        q = str(t.get("question") or t.get("thread") or "").strip()
        if tid and not any(x.get("threadId") == tid
                           for x in bible.unresolvedThreads):
            bible.unresolvedThreads.append({
                "threadId": tid, "question": q,
                "fromEpisode": ep_no, "status": "open"})
    for t in transitions.get("resolvedThreads") or []:
        tid = str(t.get("threadId") if isinstance(t, dict) else t or "").strip()
        if tid:
            for x in bible.unresolvedThreads:
                if x.get("threadId") == tid:
                    x["status"] = "resolved"
                    x["resolvedEpisode"] = ep_no
    for t in transitions.get("foreshadowingAdded") or []:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("threadId") or _sha(str(t.get("thread")))[:10])
        thread = str(t.get("thread") or "").strip()
        if not any(x.get("threadId") == tid for x in bible.foreshadowing):
            bible.foreshadowing.append({
                "threadId": tid, "thread": thread,
                "plantedEpisode": ep_no, "status": "open"})
    for t in transitions.get("foreshadowingResolved") or []:
        tid = str(t.get("threadId") if isinstance(t, dict) else t or "").strip()
        if tid:
            for x in bible.foreshadowing:
                if x.get("threadId") == tid:
                    x["status"] = "resolved"
                    x["resolvedEpisode"] = ep_no
    # timeline 与 canonFacts 只来自 episode_plan.keyEvents（结构化）
    for ev in (episode_plan or {}).get("keyEvents") or []:
        if not isinstance(ev, dict):
            continue
        page = int(ev.get("page") or ev.get("pageStart") or 0)
        bible.timeline.append({
            "episode": ep_no, "page": page,
            "event": "%s%s%s" % (ev.get("subject") or "",
                                 ev.get("action") or "",
                                 ev.get("object") or ""),
            "location": ev.get("location") or ""})
        fact = {
            "factId": "canon_e%d_%s" % (
                ep_no, _sha(json.dumps(ev, ensure_ascii=False))[:10]),
            "episode": ep_no,
            "page": page,
            "type": str(ev.get("type") or "plot"),
            "subject": str(ev.get("subject") or ""),
            "action": str(ev.get("action") or ""),
            "object": str(ev.get("object") or ""),
            "location": str(ev.get("location") or ""),
            "result": str(ev.get("result") or ""),
            "sourceText": str(ev.get("sourceText") or ""),
            "status": "active",
        }
        key = _canon_fact_key(fact)
        if not any(_canon_fact_key(x) == key for x in bible.canonFacts):
            bible.canonFacts.append(fact)
    return bible


def commit_completed_episode_to_canon(bible, episode, target_pages,
                                      page_paths, pdf_path, page_count,
                                      state_transitions=None,
                                      episode_plan=None, page_stories=None):
    """唯一 canon 提交入口。重复提交同一 episodeId 直接返回已提交。"""
    ep = episode or {}
    ep_id = str(ep.get("episodeId") or "")
    if not ep_id:
        raise RuntimeError("episode缺少episodeId")
    for rec in bible.canonCommitLog or []:
        if rec.get("episodeId") == ep_id:
            return {"episodeId": ep_id,
                    "canonCommitId": rec.get("canonCommitId"),
                    "alreadyCommitted": True}
    if ep.get("status") != "production_locked":
        raise RuntimeError("episode未锁定，不能提交canon")
    if ep.get("canonCommitId"):
        return {"episodeId": ep_id,
                "canonCommitId": ep["canonCommitId"],
                "alreadyCommitted": True}
    pages = list(page_paths or [])
    if len(pages) != int(target_pages or 0):
        raise RuntimeError("页面数不足：%d != %d" % (
            len(pages), int(target_pages or 0)))
    if not pdf_path or not os.path.isfile(str(pdf_path)):
        raise RuntimeError("PDF不存在，不能提交canon")
    if int(page_count or 0) != int(target_pages or 0):
        raise RuntimeError("PDF页数不符：%d != %d" % (
            int(page_count or 0), int(target_pages or 0)))
    ep_no = int(ep.get("episodeNo") or 0)
    transitions = state_transitions or         (episode_plan or {}).get("stateTransitions") or {}
    errs = validate_state_transitions(
        bible, transitions, target_pages, episode_plan)
    if errs:
        raise RuntimeError("状态变化校验失败：" + "；".join(errs[:6]))
    _apply_state_transitions(bible, ep_no, transitions, episode_plan)
    bible.lastCompletedEpisode = max(bible.lastCompletedEpisode, ep_no)
    commit_id = _sha(ep_id + "|" + str(time.time()))[:24]
    ep["status"] = "completed_canon"
    ep["completedAt"] = time.time()
    ep["canonCommitId"] = commit_id
    bible.canonCommitLog.append({
        "episodeId": ep_id, "episodeNo": ep_no,
        "canonCommitId": commit_id, "committedAt": time.time()})
    for i, e in enumerate(bible.episodePlans or []):
        if isinstance(e, dict) and e.get("episodeId") == ep_id:
            bible.episodePlans[i] = ep
            break
    bible.updatedAt = time.time()
    bible.bibleHash = bible.compute_hash()
    return {"episodeId": ep_id, "canonCommitId": commit_id,
            "alreadyCommitted": False}
