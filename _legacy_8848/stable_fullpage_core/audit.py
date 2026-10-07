# -*- coding: utf-8 -*-
"""stable_fullpage_core 辅助图片检查（只标记，不删除页、不改提示词、不切逐格）。
不判断裸露面积；裸露本身不构成失败；仅文件损坏/尺寸/伪文字候选等客观问题
触发 needsUserReview。"""
import os, sys, re
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app


def stable_basic_audit(image_path, expected_person_counts=None, use_engine=False):
    """基础检查：存在/损坏/尺寸/OCR伪文字候选。
    expected_person_counts 与 use_engine 仅为旧调用兼容保留，不参与判断；
    旧v2.6审查引擎不进入stable主线。"""
    issues = []
    if not os.path.isfile(image_path) or os.path.getsize(image_path) < 1024:
        return {"ok": False, "issues": ["image_missing_or_empty"],
                "needsReview": True}
    try:
        from PIL import Image
        with Image.open(image_path) as im:
            im.verify()
            w, h = im.size
        if w < 256 or h < 256:
            issues.append("size_too_small")
    except Exception as e:
        return {"ok": False, "issues": ["image_corrupt:" + str(e)[:60]],
                "needsReview": True}
    det = {}
    try:
        det = app.de_text_base_image(image_path) or {}
    except Exception as e:
        issues.append("ocr_check_failed:" + str(e)[:40])
    texts = det.get("texts") or []
    if texts:
        issues.append("generated_text_candidate")
    return {
        "ok": not issues,
        "issues": issues,
        "needsReview": bool(issues),
        "deText": det,
        "engine": {},
    }


VISUAL_SCHEMA = {
    "pageNumber": 0, "peopleCount": 0, "expectedCharacters": [],
    "observedCharacters": [], "characterFeatures": [], "outfitStates": [],
    "bodyExposureStates": [], "sceneFeatures": [], "majorActions": [],
    "styleFeatures": [], "pseudoTextDetected": False,
    "letteringIssues": [], "anatomyIssueTypes": [],
    "anatomyIssuePanels": [], "poseReadable": True,
    "bodyPartOwnershipClear": True, "uncertainties": [], "confidence": 0.0,
}

_ANATOMY_ISSUE_TYPES = {
    "fused_bodies", "fused_limbs", "extra_limb", "missing_limb",
    "impossible_joint", "malformed_hand", "severe_proportion",
    "ambiguous_body_ownership", "impossible_pose",
}


def _expected_context(script, pno):
    plan = (script.get("stableFullPageVisualPlans") or {}).get(str(pno)) or {}
    people = []
    for p in plan.get("panels") or []:
        for n in (p.get("people") or []):
            if n and n not in people:
                people.append(n)
    scene = str(plan.get("scene") or "")
    actions = []
    for p in plan.get("panels") or []:
        if isinstance(p, dict) and p.get("action"):
            actions.append(str(p["action"]))
    outfit = []
    states = (script.get("stablePageVisualStates") or {}).get(str(pno)) or {}
    for st in states.values():
        if isinstance(st, dict) and st.get("outfitState"):
            outfit.append(str(st["outfitState"]))
    return people, scene, actions, outfit


def _build_visual_prompt(script, pno, focus=""):
    people, scene, actions, outfit = _expected_context(script, pno)
    prompt = (
        "你是漫画质检员。只输出JSON（不要其他文字）："
        '{"pageNumber":%d,"peopleCount":0,"expectedCharacters":[],'
        '"observedCharacters":[],"characterFeatures":[],"outfitStates":[],'
        '"bodyExposureStates":[],"sceneFeatures":[],"majorActions":[],'
        '"styleFeatures":[],"pseudoTextDetected":false,"letteringIssues":[],'
        '"anatomyIssueTypes":[],"anatomyIssuePanels":[],"poseReadable":true,'
        '"bodyPartOwnershipClear":true,"uncertainties":[],"confidence":0.0}\n'
        "逐格检查人体结构和双人接触姿势。anatomyIssueTypes只能使用这些固定值："
        "fused_bodies, fused_limbs, extra_limb, missing_limb, impossible_joint, "
        "malformed_hand, severe_proportion, ambiguous_body_ownership, impossible_pose。"
        "只记录画面中清楚可见的结构错误；不评价成人尺度、裸露程度或题材。"
        "本页预期：人物=%s；场景=%s；动作=%s；服装状态=%s。" % (
            int(pno or 0), "、".join(people) or "无", scene or "未知",
            "、".join(actions) or "无", "、".join(outfit) or "无"))
    if focus:
        prompt += "\n重点复查：%s" % focus
    return prompt


def _eval_high_issues(fp, script, pno):
    """只返回高影响问题；不确定/低置信只进 uncertainties。"""
    issues = []
    if not isinstance(fp, dict):
        return ["视觉输出格式错误"]
    people, scene, actions, outfit = _expected_context(script, pno)
    observed = [str(x) for x in (fp.get("observedCharacters") or [])]
    conf = float(fp.get("confidence") or 0.0)
    # 主要人物缺失：预期人物名（去括号）应至少有一个出现在观察中
    if people and not any(
            any(b in o or o in b for b in
                [re.sub(r"[（(].*$", "", p).strip() for p in people])
            for o in observed):
        if conf >= 0.3:
            issues.append("主要人物缺失")
    # 明确多出主要人物：观察中出现非背景/人影的额外具体人物
    extra = [o for o in observed
             if not any(k in o for k in ("背景", "人影", "模糊", "影子",
                                        "剪影", "路人")) and
             not any(b in o or o in b for b in
                     [re.sub(r"[（(].*$", "", p).strip() for p in people])]
    if len(extra) >= 1 and conf >= 0.4:
        issues.append("多出主要人物：" + "、".join(extra[:3]))
    if fp.get("pseudoTextDetected") and conf >= 0.3:
        issues.append("伪文字")
    if conf >= 0.65:
        anatomy = [str(x) for x in (fp.get("anatomyIssueTypes") or [])
                   if str(x) in _ANATOMY_ISSUE_TYPES]
        issues.extend("人体结构：" + x for x in anatomy)
        if fp.get("poseReadable") is False:
            issues.append("姿势关系不可读")
        if len(people) >= 2 and fp.get("bodyPartOwnershipClear") is False:
            issues.append("双人肢体归属不清")
    return issues


def page_needs_pose_review(script, pno):
    """成人向默认仅复查包含双人动作的页面，控制本地模型调用次数。"""
    plan = (script.get("stableFullPageVisualPlans") or {}).get(str(pno)) or {}
    for panel in (plan.get("panels") or []):
        if not isinstance(panel, dict):
            continue
        people = [x for x in (panel.get("people") or []) if str(x).strip()]
        if len(people) >= 2 and (panel.get("action") or panel.get("positions")):
            return True
    return False


def visual_review_page(image_path, script, pno):
    """单图视觉审查：返回 (fingerprint, high_issues)。
    高影响问题做一次针对复查；两次不一致则视为不确定，不自动重出。"""
    try:
        data_url = app._qwen36_compress_image(image_path)
        fp = app.qwen36_vision(
            data_url, _build_visual_prompt(script, pno),
            max_tokens=1300, return_json=True)
        issues = _eval_high_issues(fp, script, pno)
        if issues:
            focus = "重点复查：" + "、".join(issues[:3])
            fp2 = app.qwen36_vision(
                data_url, _build_visual_prompt(script, pno, focus),
                max_tokens=900, return_json=True)
            issues2 = _eval_high_issues(fp2, script, pno)
            both = [x for x in issues if x in issues2]
            if both:
                return fp2, both
            # 两次不一致：不确定，不自动重出
            return fp, []
        return fp, []
    except Exception as e:
        return None, ["visual_error:" + str(e)[:120]]


def compare_page_fingerprints(prev, cur):
    """两张单图指纹的确定性跨页比较（不使用双图接口）。"""
    diffs = []
    if not isinstance(prev, dict) or not isinstance(cur, dict):
        return ["缺少指纹"]
    if prev.get("peopleCount") != cur.get("peopleCount"):
        diffs.append("人物数量变化")
    for key, label in (("characterFeatures", "人物特征"),
                       ("outfitStates", "服装"),
                       ("sceneFeatures", "场景"),
                       ("styleFeatures", "画风")):
        a = [str(x) for x in (prev.get(key) or [])]
        b = [str(x) for x in (cur.get(key) or [])]
        if a and b and not (set(a) & set(b)):
            diffs.append("%s明显变化" % label)
    return diffs


def visual_lettering_review(image_path, script, pno):
    """排字图检查：只查文字问题，不查画面。"""
    try:
        data_url = app._qwen36_compress_image(image_path)
        prompt = (
            "你是排字质检员。只输出JSON："
            '{"letteringIssues":[],"overlaps":false,"coversFace":false,'
            '"outOfBounds":false,"missingText":false,"doublePunctuation":false,'
            '"garbled":false,"uncertainties":[],"confidence":0.0}\n'
            "只检查文字框重叠、越界、遮挡人物脸部、缺字、双标点、乱码。")
        fp = app.qwen36_vision(
            data_url, prompt, max_tokens=800, return_json=True)
        issues = []
        if fp.get("overlaps") or fp.get("coversFace") or \
                fp.get("outOfBounds") or fp.get("garbled"):
            issues.append("排字问题")
        if fp.get("missingText") or fp.get("doublePunctuation"):
            issues.append("文字内容问题")
        return fp, issues
    except Exception as e:
        return None, ["lettering_visual_error:" + str(e)[:120]]
