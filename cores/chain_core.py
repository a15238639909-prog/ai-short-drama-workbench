# -*- coding: utf-8 -*-
"""chain_core.py — 阶段A 上游全链（第 1–8 步数据装配）。

唯一职责：把草稿/故事数据装配成八步链视图（每步最终文字可看可改、可追溯），
在 text_checks 之前跑 auto_repair，并把「已自动处理」记录带出来。
不调用任何生成模型；AI 版按钮由前端按「素材轮后可用」门控。
"""
import time
from . import store, story_core, narrative_core, state_core, asset_core
from . import quality_core, script_text, auto_repair, textchain

__all__ = ["PRESETS", "draft_create", "draft_preview", "commit_draft",
           "chain_view", "chain_edit", "chain_checks"]

DRAFT_DIR = store.DATA / "drafts"
DRAFT_DIR.mkdir(parents=True, exist_ok=True)

PRESETS = {
    "题材": ["都市犯罪·硬派", "修仙·废柴逆袭", "科幻赛博朋克", "黑暗奇幻·猎魔人",
             "成人向·奇幻", "悬疑推理", "末世求生", "宫斗权谋"],
    "主角类型": ["废柴逆袭", "强者归来", "穿越重生", "孤胆调查者", "天才少年"],
    "看点": ["打脸爽感", "战斗热血", "情感撩拨", "悬念解谜", "权谋算计", "机缘奇遇"],
    "尺度": ["全年龄", "暴力血腥", "成人向"],
    "文笔": ["跟着题材走", "大白话，好读为主", "有画面感和情绪"],
    "篇幅": ["试写 1 章", "短篇 5 章", "长篇首卷 15 章", "完整长篇 30—60 章"],
}
SPEEDS = ["极速", "智能", "精细"]
BEHAVIOR_PROMISE = "按故事里实际写到的人数生成，不会为了凑数自动补女主、导师或同伴。"


def _krea_negative():
    try:
        from models import krea_client
        return krea_client.NEGATIVE
    except Exception:
        return "文字, 水印, 多余人物, 复制人, 多肢体, 噪点, 颗粒"


def draft_create(one_line, presets=None):
    """唯一负责：存一条创作草稿（未确认前不进 data/stories/）。"""
    draft_id = store.seq_id("DRAFT", int(time.time()))
    d = {"draft_id": draft_id, "one_line": str(one_line or "").strip(),
         "presets": presets or {}, "preview": "", "created": time.time()}
    store.save_json(DRAFT_DIR / (draft_id + ".json"), d)
    return d


def draft_preview(draft):
    """唯一负责：由预设+一句话本地拼一段草稿预览（不调用模型，AI 版生成轮替换）。"""
    p = draft.get("presets") or {}
    line = draft.get("one_line") or ""
    txt = ("这是一个%s的故事。%s。\n主角类型：%s；重点看点：%s；尺度：%s；文笔：%s；篇幅：%s。\n"
           % (p.get("题材") or "待定题材", line,
              p.get("主角类型") or "待定", "、".join(p.get("看点") or ["悬念推进"]),
              p.get("尺度") or "全年龄", p.get("文笔") or "跟着题材走",
              p.get("篇幅") or "短篇 5 章"))
    txt += "\n（本地草稿：AI 版 Story Preview 在生成轮启用后替换；本页可直接改写。）"
    return txt


def commit_draft(draft_id):
    """唯一负责：确认草稿 → 真正创建故事项目（数据写入 data/stories/）。"""
    d = store.load_json(DRAFT_DIR / (draft_id + ".json"), None)
    if not d:
        raise ValueError("草稿不存在")
    p = d.get("presets") or {}
    short = str(p.get("篇幅") or "").startswith(("试写", "短篇"))
    title = "%s·%s" % (p.get("题材") or "故事", p.get("主角类型") or "主角")
    story = story_core.create_story(d.get("one_line") or "", title=title[:40],
                                    mode="short" if short else "long")
    story["create_prefs"] = {k: p.get(k) for k in ("题材", "主角类型", "看点", "尺度", "文笔", "篇幅")}
    story["create_prefs"]["speed"] = p.get("speed") or "智能"
    story["preview_summary"] = d.get("preview") or draft_preview(d)
    story["production_stage"] = "draft"
    story_core.save_story(story)
    return story


def _speakers(chars):
    out = []
    for c in chars:
        name = c.get("name") or ""
        for cand in (name, name.replace("·", ""), name.split("·")[0], name.split("·")[-1]):
            if cand and cand not in out:
                out.append(cand)
    return out


def chain_view(story_id):
    """唯一负责：装配八步链视图（人物/场景提示词已过 auto_repair；第 8 步读 textchain 修复后产物）。"""
    story = story_core.get_story(story_id) or {}
    ep = narrative_core.load_episode(story_id, story.get("current_episode") or 1) or {}
    previs = story.get("previs") or {}
    chars = asset_core.list_assets(story_id, "characters")
    scens = asset_core.list_assets(story_id, "scenes")
    visuals = asset_core.list_assets(story_id, "visuals")
    adopted = {v.get("owner_id"): v for v in visuals if v.get("status") == "adopted"}

    for c in chars:
        c["krea_positive"] = quality_core.compile_character_prompt(
            c, composition="16:9 人物 Master Sheet：正面/侧面/背面三视图 + 面部特写，浅灰背景",
            purpose_details="布光：均匀柔和棚拍光，主光正面偏左45°，辅光补暗部，背景干净无投影；镜头视角：35mm 等效；构图：四视图均匀分布；画面干净")
        c["krea_positive"], _ = auto_repair.repair_prompt(c["krea_positive"], None)
        c["krea_negative"] = _krea_negative()
        c["adopted_visual"] = adopted.get(c.get("character_id"), {}).get("path", "")

    scene_names = [s.get("name") for s in scens]
    for s in scens:
        rs, notes = auto_repair.repair_scene(s, scene_names)
        s.update(rs)
        s["repair_notes"] = notes
        s["krea_positive"] = quality_core.compile_scene_prompt(
            s, composition="构图：中广角/广角建立画面，3/4 空间角度",
            purpose_details="空场空间基准图；前景/中景/背景三层纵深明确；画面内仅为空间与静物；画面干净"
                            + ("；真实尺度：%s。空间纵深：%s。" % (s.get("scale") or "", s.get("depth") or "")
                               if s.get("scale") or s.get("depth") else ""))
        s["krea_positive"], _ = auto_repair.repair_prompt(s["krea_positive"], None)
        s["krea_negative"] = _krea_negative()
        s["adopted_visual"] = adopted.get(s.get("scene_id"), {}).get("path", "")

    script_txt = previs.get("story_script") or ""
    dialogue = [{"speaker": spk, "line": ln, "paren": paren or ""}
                for spk, paren, ln in script_text.dialogue_lines(script_txt, _speakers(chars))]
    state_lines = []
    try:
        state_lines = chain_state_lines(story_id)
    except Exception:
        state_lines = []

    shots = list(previs.get("shots") or [])
    return {
        "story": story,
        "preview": story.get("preview_summary") or "",
        "master_plan": story.get("master_plan") or {},
        "events": ep.get("events") or [],
        "characters": chars,
        "scenes": scens,
        "script": script_txt,
        "dialogue": dialogue,
        "state_lines": state_lines,
        "director_intent": previs.get("director_intent") or {},
        "blocking": previs.get("blocking") or {},
        "shots": shots,
        "production_sheet": _production_sheet(story_id),
    }


def chain_state_lines(story_id):
    """唯一负责：状态账本人话版（外套已脱下/左臂永久缺失等）。"""
    from . import state_core as sc
    pkg = sc.causal_state_package(story_id)
    lines = []
    if not pkg:
        return lines
    clock = pkg.get("story_clock") or {}
    lines.append("故事时间：第 %s 日 %s" % (clock.get("day", "?"), clock.get("time", "?")))
    for name, slots in (pkg.get("clothing") or {}).items():
        for slot, val in (slots or {}).items():
            label = {"outerwear": "外套", "outer_cloak": "外披风", "upper_body": "上衣",
                     "lower_body": "下装", "underwear": "内层", "footwear": "鞋",
                     "accessories": "配饰"}.get(slot, slot)
            if val == "removed":
                lines.append("%s 的%s已脱下" % (name, label))
            elif val == "worn" and slot in ("outerwear", "outer_cloak"):
                lines.append("%s 的%s穿在身上" % (name, label))
    for name, st in (pkg.get("anatomy") or {}).items():
        for part, val in (st or {}).items():
            if val == "missing":
                lines.append("%s 的%s永久缺失" % (name, {"left_arm": "左臂", "right_arm": "右臂",
                                                        "left_leg": "左腿", "right_leg": "右腿"}.get(part, part)))
    for name, injs in (pkg.get("injuries") or {}).items():
        for inj in (injs or []):
            if isinstance(inj, dict):
                lines.append("%s 有%s（%s，%s）" % (name, inj.get("type", "伤"),
                                                   inj.get("severity", "轻"), inj.get("status", "治疗中")))
    cons = pkg.get("consequences") or []
    if isinstance(cons, dict):
        for name, c in cons.items():
            if c:
                lines.append("%s：%s" % (name, c if isinstance(c, str) else str(c)))
    elif isinstance(cons, list):
        for c in cons:
            if isinstance(c, dict) and c.get("text"):
                lines.append(str(c["text"])[:120])
    return lines


def _production_sheet(story_id):
    """唯一负责：第 8 步生产单（参考包已修复 + H3 提示词 + 出关检查 + 修复记录）。"""
    tc = store.DATA / "textchain" / story_id
    result = store.load_json(tc / "result.json", {})
    prompts = store.load_json(tc / "prompts.json", {})
    art = store.load_json(tc / "artifacts.json", {})
    visuals = asset_core.list_assets(story_id, "visuals")
    path_of = {v.get("visual_id"): v.get("path") for v in visuals}
    sheets = []
    for seq in art.get("ref_plan") or []:
        pics = []
        for p in seq.get("pictures") or []:
            pics.append({"role": p.get("role"), "source": p.get("source"),
                         "purpose": p.get("purpose", ""),
                         "path": path_of.get(p.get("source"), "")})
        sheets.append({
            "sequence_id": seq.get("sequence_id"),
            "pictures": pics,
            "prompt": (prompts.get("h3") or {}).get(seq.get("sequence_id"), ""),
            "params": {"seconds": 15, "ratio": "16:9", "seed": "固定（见任务记录）"},
        })
    return {"sheets": sheets, "validation": result, "repair_notes": result.get("repair_notes") or []}


def chain_edit(story_id, step, data):
    """唯一负责：把第 2–7 步的编辑写回数据（写后立即重新装配视图）。"""
    story = story_core.get_story(story_id)
    if not story:
        raise ValueError("故事不存在")
    if step == "preview":
        story["preview_summary"] = str(data.get("text") or "")
        story_core.save_story(story)
    elif step == "plan":
        story["master_plan"] = data if isinstance(data, dict) else {}
        story_core.save_story(story)
    elif step == "events":
        ep = narrative_core.load_episode(story_id, story.get("current_episode") or 1) or {}
        ep["events"] = data if isinstance(data, list) else []
        ep["updated"] = time.time()
        narrative_core.save_episode(ep)
    elif step == "characters":
        for c in (data if isinstance(data, list) else []):
            cid = c.get("character_id") or c.get("name") or ""
            old = asset_core.get_asset(story_id, "characters", cid)
            if old:
                for k in ("name", "age", "sex", "char_type", "look", "build", "hair",
                          "personality", "clothing", "clothing_requirement", "other"):
                    if k in c:
                        old[k] = c[k]
                old["updated"] = time.time()
                asset_core.save_asset(story_id, "characters", old)
    elif step == "scenes":
        for s in (data if isinstance(data, list) else []):
            sid = s.get("scene_id") or s.get("name") or ""
            old = asset_core.get_asset(story_id, "scenes", sid)
            if old:
                for k in ("name", "contract_text", "regions", "materials", "fixed_landmarks",
                          "base_light_color", "scale", "depth", "location_map"):
                    if k in s:
                        old[k] = s[k]
                old["updated"] = time.time()
                asset_core.save_asset(story_id, "scenes", old)
    elif step == "script":
        story.setdefault("previs", {})["story_script"] = str(data.get("text") or "")
        story_core.save_story(story)
    elif step == "director":
        story.setdefault("previs", {})["director_intent"] = data.get("director_intent") or {}
        if "blocking" in data:
            story["previs"]["blocking"] = data.get("blocking") or {}
        story_core.save_story(story)
    elif step == "shots":
        story.setdefault("previs", {})["shots"] = data if isinstance(data, list) else []
        story_core.save_story(story)
    else:
        raise ValueError("未知步骤：" + str(step))
    return chain_view(story_id)


def chain_checks(story_id):
    """唯一负责：返回最近一次「修复后」验收结果与修复记录。"""
    res = textchain.get_validation(story_id)
    if not res:
        res = textchain.run_for_story(story_id)
    return {"result": res.get("result"), "checks": res.get("checks") or [],
            "repair_notes": res.get("repair_notes") or []}
