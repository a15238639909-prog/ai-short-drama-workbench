# -*- coding: utf-8 -*-
"""stable_fullpage_core 唯一整页提示词编译器。
八段固定结构；画风原文逐字开头；只描述当前画面实际需要的内容；
整字段裁剪，禁止句子中间截断；不生成统一否定提示词尾部。"""
import re, json
import app

from stable_fullpage_core.contracts import validate_expansion_against_contract
from stable_fullpage_core.models import select_page_character_anchors

LAYOUT_CN = {
    "hero": "竖版单格主画面整页",
    "hero+strip": "竖版2格：上方通栏主画面，下方横条一格",
    "hero+2": "竖版3格：上方通栏主画面，下方并列两格",
    "hero+2+1": "竖版4格：上方通栏主画面，中间并列两格，下方一格",
    "hero+2+2": "竖版5格：上方通栏主画面，下方两排各两格",
    "2+hero+2": "竖版5格：上下各两格，中间通栏主画面",
    "1+2+2+1": "竖版6格：第一格通栏，中间两排各两格，末格通栏",
}

SHOT_CN = {"大全景": "全景", "远景": "全景", "全景": "全景", "中景": "中景",
           "近景": "近景", "特写": "特写", "极近特写": "大特写"}

STYLE_UNITY = ("全页各画格统一采用上述画风的媒介、人物表现、材质、光线、"
               "色彩与渲染方式。")
STYLE_INTENSIFY = ("全页必须极致、强烈地体现上述画风：媒介、笔触、线条、色彩与"
                   "质感贯穿每个画格，禁止退化为普通写实照片或普通动漫。")
NO_CAMERA_DEVICE = ("画面是漫画/插画画面本身，禁止出现相机、镜头、摄影机、"
                    "摄像机、三脚架、取景器等拍摄设备；提到的视角只是取景方式，"
                    "不是画面中的物体。")
_CAMERA_TERMS = re.compile(r"镜头|摄影机|摄像机|相机|取景器|焦距")


def _camera_repl(m):
    return {"镜头": "视角", "相机": "", "摄影机": "", "摄像机": "",
            "取景器": "画面", "焦距": "景深"}.get(
                m.group(0), "视角")

CONTINUITY = ("同一人物跨格保持年龄、性别、发型、脸部标志、体型与当前服装状态一致；"
              "每格人物数量与本格列出名单一致；各格属于同一场景的连续瞬间。")
ANATOMY_INTEGRITY = ("可见人体结构清楚连贯，头部、躯干与四肢比例协调；四肢在正确关节"
                     "自然连接，手掌呈现五根彼此分明的手指；双人互动时两人的身体轮廓、"
                     "前后遮挡、肢体归属和接触点清楚。")

_PANEL_FIELD_ORDER = (
    ("叙事作用", "storyPurpose"),
    ("表情", "expression"),
    ("位置", "positions"),
    ("关键物体", "importantObject"),
    ("互动", "interaction"),
    ("当前状态", "visibleOutfitOrState"),
    ("环境", "environment"),
)


def _cn_len(text):
    return len(re.findall(r"[\u4e00-\u9fa5]", str(text or "")))


def _dedupe_sentences(text):
    out, seen = [], set()
    for s in re.split(r"(?<=[。])", str(text or "")):
        s = s.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return "".join(out)


def _text_surface_sentence(plan):
    """当前页面实际存在屏幕/标牌/文件等文字表面时才返回正向描述。
    没有对应对象时不生成任何文字控制。"""
    if not isinstance(plan, dict):
        return ""
    # 只扫描当前页可见字段，futureRevealFacts 不进提示词、不触发文字控制
    text_all = json.dumps({
        "panels": plan.get("panels") or [],
        "diegeticTextItems": plan.get("diegeticTextItems") or [],
    }, ensure_ascii=False)
    surfaces = [k for k in ("屏幕", "显示器", "控制台", "标牌", "招牌", "文件",
                            "登记册", "纸张", "地图", "铭牌")
                if k in text_all]
    items = plan.get("diegeticTextItems") or []
    if not surfaces and not items:
        return ""
    if items and isinstance(items, list) and items and \
            isinstance(items[0], dict) and items[0].get("location"):
        loc = str(items[0]["location"])
        return ("%s显示抽象光点、波形和几何线路，"
                "具体文字由本地排字添加。" % loc)
    obj = surfaces[0] if surfaces else "屏幕与标牌"
    return ("%s显示抽象光点、波形和几何线路，"
            "具体文字由本地排字添加。" % obj)


def _build_character_section(contract, character_anchors, plan, visual_states,
                             page_no):
    """只注入本页实际出场人物；服装/身体状态按当前状态正向描述。"""
    if not isinstance(plan, dict) or not (plan.get("panels") or []):
        return ""
    selected = select_page_character_anchors(plan, character_anchors)
    if not selected:
        return ""
    states = (visual_states or {}).get(str(page_no)) or {}
    panel_state_names = set()
    for panel in (plan.get("panels") or []):
        if isinstance(panel, dict) and isinstance(panel.get("characterStates"), dict):
            panel_state_names.update(str(x) for x in panel["characterStates"])
    def _panel_state_values(name):
        base = re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", str(name or "")).strip()
        values = set()
        for panel in (plan.get("panels") or []):
            raw = panel.get("characterStates") if isinstance(panel, dict) else {}
            for other, value in (raw or {}).items():
                other_base = re.sub(
                    r"\s*[（(][^）)]*[）)]\s*$", "", str(other or "")).strip()
                if base == other_base and str(value).strip():
                    values.add(str(value).strip())
        return values
    lines = []
    for key, a in selected.items():
        if not isinstance(a, dict):
            continue
        st = states.get(key) or states.get(a.get("characterName") or "") or {}
        body_state = st.get("bodyExposureState") or "normal_clothed"
        panel_values = _panel_state_values(a.get("characterName") or key)
        if len(panel_values) == 1 and next(iter(panel_values)) in (
                "normal_clothed", "partially_exposed", "partial_change", "nude"):
            body_state = next(iter(panel_values))
        parts = []
        if a.get("identityText"):
            parts.append("身份：" + a["identityText"])
        if a.get("appearanceText"):
            parts.append("外观：" + a["appearanceText"])
        panel_controlled = len(panel_values) > 1
        has_nude = "nude" in panel_values
        if panel_controlled and has_nude:
            # 本页角色存在裸体格：即使其他格状态不同，也绝不输出任何服装描述，
            # 避免 Krea 把“服装造型参考”里的衣服画到裸体身上。
            parts.append("当前状态：全身赤裸，身上没有任何衣物；已脱下的衣物不在身上。")
        elif panel_controlled:
            outfit = a.get("defaultOutfitText") or ""
            if outfit:
                parts.append("服装造型参考：" + outfit)
        elif body_state == "nude":
            # 当前剧情为裸露：不注入默认服装，并把外观文本里的服装短语剔除，
            # 避免模型画出衣服。
            outfit_phrases = [str(x).strip() for x in
                              (a.get("defaultOutfitText") or "").split("；")
                              if str(x).strip()]
            cleaned = []
            for part in parts:
                t = str(part or "")
                for ph in outfit_phrases:
                    if ph and ph in t:
                        t = t.replace(ph, "")
                t = re.sub(r"[，,、；;]{2,}", "；", t).strip("，,、；; ")
                if t:
                    cleaned.append(t)
            parts = cleaned
            parts.append("当前状态：全身赤裸，身上没有任何衣物。")
        elif body_state in ("partially_exposed", "partial_change"):
            outfit = st.get("outfitState") or a.get("defaultOutfitText") or ""
            if outfit:
                parts.append("当前服装：" + outfit)
        else:
            outfit = a.get("defaultOutfitText") or ""
            if outfit:
                parts.append("服装：" + outfit)
        if a.get("fixedMarksText"):
            parts.append("固定标志：" + a["fixedMarksText"])
        if a.get("propText"):
            parts.append("随身道具：" + "、".join(a["propText"]))
        lines.append("%s（%s）" % (a.get("characterName") or key,
                                   "；".join(parts)))
    def _display_name(name):
        return re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", str(name or "")).strip()

    names = [_display_name(a.get("characterName") or k)
             for k, a in selected.items()]
    head = "本页人物为：%s。" % "、".join(names)
    if panel_state_names:
        head += "本页各格实际穿着以每格人物状态为准。"
    body = "；".join(lines)
    return head + "人物细节：" + body + "。"


def _build_scene_section(scene_anchor):
    if not isinstance(scene_anchor, dict):
        return ""
    fields = []
    for label, key in (("空间", "spaceText"), ("结构", "architectureText"),
                       ("材质", "materialText"), ("标志物", "landmarkText"),
                       ("光源", "lightingText"), ("色彩", "colorText")):
        vals = [x for x in (scene_anchor.get(key) or []) if x]
        if vals:
            fields.append("%s：%s" % (label, "、".join(vals)))
    if not fields:
        return ""
    return "本页场景：%s（%s）。" % (
        scene_anchor.get("sceneName") or "场景", "；".join(fields))


def _panel_segments(panels, include_fields=None):
    include_fields = set(include_fields or
                         [f[1] for f in _PANEL_FIELD_ORDER] +
                         ["visible", "environment"])
    segs = []
    for idx, p in enumerate(panels, 1):
        if not isinstance(p, dict):
            continue
        shot = str(p.get("shot") or "中景")
        seg = ["第%d格：%s视角" % (idx, SHOT_CN.get(shot, shot))]
        people = [str(x) for x in (p.get("people") or []) if str(x)]
        seg.append("本格%d人：%s" % (
            len(people), "、".join(people) if people else "空镜"))
        raw_states = p.get("characterStates") or {}
        if isinstance(raw_states, dict) and raw_states:
            state_cn = {
                "normal_clothed": "穿着人物细节中的服装",
                "partially_exposed": "部分裸露",
                "partial_change": "部分裸露",
                "nude": "裸体",
            }
            current = ["%s：%s" % (name, state_cn.get(str(state), str(state)))
                       for name, state in raw_states.items()
                       if str(name).strip() and str(state).strip()]
            if current:
                seg.append("人物状态：" + "；".join(current))
        def _clean(v):
            return _CAMERA_TERMS.sub("视角", str(v or "").strip())
        if include_fields & {"positions"} and p.get("positions"):
            seg.append("位置：" + _clean(p.get("positions")))
        if p.get("action"):
            seg.append("动作：" + _clean(p.get("action")))
        if include_fields & {"expression"} and p.get("expression"):
            seg.append("表情：" + _clean(p.get("expression")))
        if include_fields & {"visibleOutfitOrState"} and \
                p.get("visibleOutfitOrState"):
            seg.append("当前状态：" + _clean(p.get("visibleOutfitOrState")))
        if include_fields & {"importantObject"} and p.get("importantObject"):
            seg.append("关键物体：" + _clean(p.get("importantObject")))
        if include_fields & {"interaction"} and p.get("interaction"):
            seg.append("互动：" + _clean(p.get("interaction")))
        if include_fields & {"storyPurpose"} and p.get("storyPurpose"):
            seg.append("作用：" + _clean(p.get("storyPurpose")))
        if include_fields & {"environment"} and p.get("environment"):
            seg.append("环境：" + _clean(p.get("environment")))
        if include_fields & {"visible"} and p.get("visible"):
            seg.append("可见：" + _clean(p.get("visible")))
        segs.append("，".join(seg))
    return segs


def _post_validate(contract, prompt, plan, character_anchors, visual_states,
                   page_no):
    hard = []
    hard += contract.validate()
    soft = validate_expansion_against_contract(
        contract, prompt, check_events=False)
    if soft:
        app.log("⚠ 最终提示词与合同提示（不拦截）：%s" % "；".join(soft[:4]))
    # 本页人物检查：只出现本页实际出场人物
    selected = select_page_character_anchors(plan, character_anchors)
    for name in selected:
        if name not in prompt and (selected[name].get("characterName") or name) \
                not in prompt:
            app.log("⚠ 最终提示词缺少本页人物（不拦截）：%s" % name)
    # 服装/身体状态检查
    states = (visual_states or {}).get(str(page_no)) or {}
    for key, a in selected.items():
        st = states.get(key) or states.get(a.get("characterName") or "") or {}
        body = st.get("bodyExposureState") or "normal_clothed"
        if body == "nude" and a.get("defaultOutfitText"):
            for phrase in a["defaultOutfitText"].split("；"):
                phrase = phrase.strip()
                if phrase and phrase in prompt:
                    app.log("⚠ 裸露状态仍注入了默认服装（不拦截，已尽量剔除）：%s"
                            % phrase)
    if hard:
        raise RuntimeError("最终提示词校验失败：" + "；".join(hard[:6]))


def compile_stable_fullpage_prompt(contract, character_anchors, scene_anchor,
                                   page_visual_plan, page_no,
                                   visual_states=None):
    """编译一页最终整页Krea提示词（0次模型调用）。
    正式顺序：画风原文 → 正向画风统一说明 → 版式 → 本页人物与当前状态 →
    当前场景 → 每格镜头动作 → 必要连续关系 → 当前页特殊对象。"""
    style_text = str(getattr(contract, "selectedStyleText", "") or "").strip()
    if not style_text:
        raise RuntimeError("画风完整原文为空，禁止生成")
    style_text = _CAMERA_TERMS.sub(_camera_repl, style_text)
    plan = page_visual_plan if isinstance(page_visual_plan, dict) else {}
    panels = plan.get("panels") or []
    parts = [style_text]
    layout = str(plan.get("layout") or "")
    parts.append("版式：" + LAYOUT_CN.get(layout, (
        "竖版%d格连续叙事漫画页" % max(1, len(panels)))))
    char_section = _build_character_section(
        contract, character_anchors, plan, visual_states, page_no)
    if char_section:
        parts.append(char_section)
    scene_section = _build_scene_section(scene_anchor)
    if scene_section:
        parts.append(scene_section)
    # 每格内容：优先保留动作/人数，其次按整字段降级
    drop_order = [f[1] for f in _PANEL_FIELD_ORDER]
    for drop_count in range(len(drop_order) + 1):
        keep = set(drop_order[drop_count:]) | {"visible", "environment"}
        panel_segs = _panel_segments(panels, keep)
        prompt = _dedupe_sentences("。".join(x for x in
            parts + panel_segs + [CONTINUITY, ANATOMY_INTEGRITY,
                                  NO_CAMERA_DEVICE]
            if x).rstrip("。") + "。")
        if _cn_len(prompt) <= 1400:
            break
    else:
        raise RuntimeError("第%d页提示词超过1400字上限且无法整字段裁剪" % page_no)
    # 第八部分：当前页实际存在的特殊对象（文字表面）
    text_surface = _text_surface_sentence(plan)
    if text_surface:
        prompt = _dedupe_sentences(prompt + text_surface)
    _post_validate(contract, prompt, plan, character_anchors, visual_states,
                   page_no)
    return prompt
