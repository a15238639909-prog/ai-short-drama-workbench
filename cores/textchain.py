# -*- coding: utf-8 -*-
"""textchain.py — 文字链唯一入口（Phase 0）。

唯一职责：把 data/ 里一个故事的人物/场景/剧本/状态确定性编译成
Krea/H3 最终提示词，并跑 text_checks 文字验收，结果写进 data/textchain/<STORY_ID>/。
内部只调用现有模块（story_profile / quality_core / script_text / h3_format /
prompt_hygiene / video_plan / text_checks），不重写任何解析或校验规则。
"""
import json, re, time
from pathlib import Path

from . import store, story_core, narrative_core, state_core, asset_core
from .story_profile import StoryProfile
from . import quality_core, script_text, h3_format, prompt_hygiene, text_checks
from . import auto_repair
from production import video_plan

__all__ = ["run_for_story", "get_validation", "get_trace"]

MUSIC = "N/A"  # 项目级策略：无背景音乐（生成器读取，不在产物上改）


def out_dir(story_id):
    d = store.DATA / "textchain" / story_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _speaker_alias_map(profile):
    """唯一职责：从人物数据推导说话人全名/简称映射（无硬编码人名）。"""
    m = {}
    for c in profile.characters:
        candidates = []
        for name in (c.get("full_name"), c.get("name")):
            if name and name not in candidates:
                candidates.append(name)
        for name in list(candidates):
            for alias in (name.replace("·", ""), name.split("·")[0], name.split("·")[-1]):
                if alias and alias not in candidates:
                    candidates.append(alias)
        for name in candidates:
            m.setdefault(name, c["code"])
    return m


def _normalize_script(script_txt, profile, amap):
    """唯一职责：把导演剧本规范成标准影视剧本（说话人全名行/括号提示行/台词行），不改台词原文。

    导演剧本常用「卡恩：（收起枪）台词」的紧凑写法，且用简称；此处只做格式与称呼归一，
    让 script_text 用 profile.speakers（全名）即可解析出纯净台词。
    """
    full_by_code = {c["code"]: (c.get("full_name") or c["name"]) for c in profile.characters}
    text = str(script_txt or "")
    aliases = sorted(amap, key=len, reverse=True)
    if aliases:
        cue = re.compile(r"^(%s)([：:])(.*)$" % "|".join(re.escape(a) for a in aliases),
                         re.MULTILINE)
        text = cue.sub(lambda m: full_by_code[amap[m.group(1)]] + m.group(2) + m.group(3), text)

    full_pat = "|".join(re.escape(full_by_code[c["code"]]) for c in profile.characters)
    cue2 = re.compile(r"^(%s)([：:])(.*)$" % full_pat, re.MULTILINE)
    out_lines = []
    for line in text.splitlines():
        m = cue2.match(line)
        if not m:
            out_lines.append(line)
            continue
        rest = m.group(3)
        parens = re.findall(r"[（(][^（）()]*[）)]", rest)
        body = rest
        for p in parens:
            body = body.replace(p, "")
        out_lines.append(m.group(1) + m.group(2))
        for p in parens:
            out_lines.append(p)
        if body.strip():
            out_lines.append(body.strip())
    return "\n".join(out_lines)


def _resolve_line(code, line, by_spk):
    """唯一职责：把镜头里的台词对齐到剧本原文完整句（对白只能来自剧本）。"""
    for ln in by_spk.get(code, []):
        if line in ln or ln in line:
            return ln
    return line


def _shot_schema(shot):
    """唯一职责：把项目分镜字段改名映射到 h3_format 标准镜头 schema（不改内容）。"""
    def g(*ks):
        for k in ks:
            v = shot.get(k)
            if v not in (None, ""):
                return str(v)
        return ""
    return {
        "shot_id": shot.get("shot_id") or "SHOT_01",
        "duration": float(shot.get("duration") or 3),
        "narrative_purpose": g("narrative_purpose", "purpose"),
        "framing": g("framing", "shot_type"),
        "movement_path": g("camera_movement", "movement_path") or "固定",
        "movement_speed": g("movement_speed", "camera_speed") or "慢速",
        "movement_end_point": g("movement_end_point", "camera_end") or "无",
        "camera_position": g("camera_position"),
        "camera_height": g("camera_height", "camera_altitude"),
        "camera_angle": g("camera_angle"),
        "camera_distance": g("camera_distance"),
        "optics": g("optics", "lens_character"),
        "focus_subject": g("focus_subject", "visual_focus"),
        "depth_of_field": g("depth_of_field", "dof") or "中景深",
        "foreground": g("foreground", "fg") or "环境细节",
        "midground": g("midground", "mg") or "主体",
        "background": g("background", "bg") or "环境背景",
        "A_action": g("A_action", "action"),
        "A_performance": g("A_performance", "performance"),
        "B_action": g("B_action") or "未入画",
        "B_performance": g("B_performance") or "无",
        "camera_movement": g("camera_movement"),
        "performance": g("performance"),
        "blocking": g("blocking"),
        "eyeline": g("eyeline", "eye_line"),
        "dominant_light": g("dominant_light", "light") or "环境主光",
        "subject_light_side": g("subject_light_side", "light_side") or "主光一侧",
        "audio": g("audio", "sound"),
        "dialogue_raw": shot.get("dialogue") or "",
    }


def _storyboard(profile, story):
    """唯一职责：把导演分镜按事件归属分配到序列，并执行 15s 时长合约。"""
    raw_shots = list((story.get("previs") or {}).get("shots") or [])
    seqs = []
    for i, seq in enumerate(profile.sequences, 1):
        own = [e for e in (seq.get("events_covered") or [])]
        if raw_shots:
            picks = [s for s in raw_shots if any(e in own for e in (s.get("event_ids") or []))]
            if not picks:
                lo = int(round((i - 1) * len(raw_shots) / len(profile.sequences)))
                hi = int(round(i * len(raw_shots) / len(profile.sequences)))
                picks = raw_shots[lo:hi]
        else:
            picks = []
        shots = [_shot_schema(s) for s in picks]
        dc = video_plan.duration_contract(shots, target=seq.get("target_duration") or 15)
        for idx, shot in enumerate(shots):
            if idx < len(dc["timeline"]):
                shot["duration"] = dc["timeline"][idx]["duration"]
        seqs.append({
            "sequence_id": seq["sequence_id"],
            "scene": seq.get("scene"),
            "purpose": (profile.episode or {}).get("units", [{}])[i - 1].get("purpose", "")
                        if i - 1 < len((profile.episode or {}).get("units", [])) else "",
            "shots": shots,
            "timeline": dc["timeline"],
            "duration_contract": dc,
        })
    return seqs


def _audio_plan(profile, storyboard, amap, by_spk):
    """唯一职责：从 shot 对白（经 script_text 解析）组装音频计划，人名不当台词。"""
    out = []
    for seq in storyboard:
        lines = []
        for s in seq["shots"]:
            raw = s.get("dialogue_raw") or ""
            if raw and raw.strip() not in ("无", ""):
                for spk, _, line in script_text.dialogue_lines(raw, list(amap)):
                    code = amap.get(spk)
                    if code:
                        lines.append(_resolve_line(code, line, by_spk))
        out.append({"sequence_id": seq["sequence_id"], "dialogue": lines,
                    "ambience": "现场环境声", "foley": "动作细节", "silence": "对白间保留自然静默",
                    "music": MUSIC})
    return out


def _ledger(profile, state):
    """唯一职责：从状态账本推导每段进出状态（人名经别名映射到角色代号）。"""
    amap = {}
    for c in profile.characters:
        for name in (c["full_name"], c["name"], c["name"].replace("·", ""), c["name"].split("·")[-1]):
            amap[name] = c["code"]

    def code_of(name):
        for k, code in amap.items():
            if k and (k in name or name in k):
                return code
        return None

    clothing = (state or {}).get("clothing_states") or {}
    ledger = {}
    for i, seq in enumerate(profile.sequences, 1):
        before, after = {}, {}
        for name, slots in clothing.items():
            code = code_of(str(name))
            if not code:
                continue
            w = "；".join("%s=%s" % (k, v) for k, v in slots.items())
            before.setdefault(code, {})["wardrobe"] = w
            after.setdefault(code, {})["wardrobe"] = w
        ledger["scene%d_before" % i] = before
        ledger["scene%d_after" % i] = after
    return ledger


def _wardrobe_slots(profile, chars):
    """唯一职责：从人物服装文字推导 5 槽位基线（供服装槽验收，缺数据即为空槽）。"""
    out = {}
    for c in profile.characters:
        ch = next((x for x in chars if x.get("character_id") == c["id"]), {})
        text = str(ch.get("clothing") or "")
        base = {"outerwear": "", "base_top": "", "base_bottom": "", "footwear": "", "accessories": ""}
        if any(k in text for k in ("外套", "开衫", "风衣", "披风", "夹克")):
            base["outerwear"] = "外套"
        if any(k in text for k in ("衬衫", "上衣", "连衣裙", "裙")):
            base["base_top"] = "上衣"
        if "裤" in text:
            base["base_bottom"] = "长裤"
        if any(k in text for k in ("鞋", "靴")):
            base["footwear"] = "鞋"
        if any(k in text for k in ("包", "饰", "表")):
            base["accessories"] = "配饰"
        out[c["id"]] = {"baseline": base}
    return out


def _image_prompts(profile, chars, scens, settings=None):
    """唯一职责：程序化编译 Krea 人物/场景 Master 提示词（不调用任何生成模型）。"""
    prompts = {}
    pset = dict(settings or {})
    pset["_character_names"] = [x.get("name") for x in chars if x.get("name")]
    for c0 in chars:
        c = dict(c0)
        c["_project"] = pset
        comp = "16:9 人物 Master Sheet：同一角色的正面/侧面/背面三视图 + 一张面部表情特写，浅灰背景"
        detail = ("布光：均匀柔和棚拍光，主光正面偏左45°，辅光补暗部，背景干净无投影；"
                  "镜头视角：35mm 等效；构图：四视图均匀分布；画面干净")
        txt = quality_core.compile_character_prompt(c, composition=comp, purpose_details=detail)
        prompts[c["character_id"] + "_master"] = prompt_hygiene.sanitize_internal_ids(txt, profile.id_map)
    for s0 in scens:
        s = dict(s0)
        s["_project"] = pset
        comp = "构图：中广角/广角建立画面，3/4 空间角度"
        detail = "空场空间基准图；前景/中景/背景三层纵深明确；画面内仅为空间与静物；画面干净"
        if s.get("scale") or s.get("depth"):
            detail += "；真实尺度：%s。空间纵深：%s。" % (s.get("scale") or "", s.get("depth") or "")
        txt = quality_core.compile_scene_prompt(s, composition=comp, purpose_details=detail)
        prompts[s["scene_id"] + "_master"] = prompt_hygiene.sanitize_internal_ids(txt, profile.id_map)
    return prompts


def _compile_h3_prompts(profile, storyboard, audio, amap, by_spk, settings=None):
    """唯一职责：按官方六段式确定性编译 H3 最终提示词（thinking OFF，无模型润色）。"""
    subj_defs = []
    for i, c in enumerate(profile.characters, 1):
        subj_defs.append("<Subject %d> 是%s人物 Master 中的%s，保留其脸型、发型、身材与固定识别特征。"
                         % (i, c["name"], c["name"]))
    if profile.scenes:
        sc = profile.scenes[0]
        subj_defs.append("%s 是%s场景 Master 中的%s空间，保留其结构、材质与识别点。"
                         % (profile.scene_subject_tag, sc["name"], sc["name"]))
    tags = list(profile.role_subjects.values())
    if profile.scenes:
        tags.append(profile.scene_subject_tag)
    retention = ["%s: fully_preserved" % t for t in tags]
    from . import project_prompt
    style_open = project_prompt.video_visual_block(settings or {})
    names = {c["code"]: c["name"] for c in profile.characters}

    prompts = {}
    for seq, aud in zip(storyboard, audio):
        paragraphs = []
        for idx, shot in enumerate(seq["shots"]):
            start = seq["timeline"][idx]["start"] if idx < len(seq["timeline"]) else 0
            dials = []
            raw = shot.get("dialogue_raw") or ""
            if raw and raw.strip() not in ("无", ""):
                for spk, paren, line in script_text.dialogue_lines(raw, list(amap)):
                    code = amap.get(spk)
                    if code and code in names:
                        dials.append((names[code], paren, _resolve_line(code, line, by_spk)))
            paragraphs.append(h3_format.shot_paragraph(shot, idx, start, profile.role_subjects,
                                                       names, dialogue=dials))
        scene_name = profile.scene_name(seq.get("scene") or "")
        summary_en = ("[reference generation] A 15-second %s sequence following the story beat: %s"
                      % (scene_name, seq.get("purpose", "")))
        prompts[seq["sequence_id"]] = h3_format.build_ref2va(
            subj_defs, summary_en, retention, style_open, paragraphs,
            "；".join(filter(None, [aud.get("ambience"), aud.get("foley"), aud.get("silence")])),
            music=MUSIC)
    return prompts


def run_for_story(story_id):
    """唯一职责：对单个故事跑完整文字链（提示词编译 + 全部验收），写进 data/textchain/。"""
    story = story_core.get_story(story_id)
    if not story:
        raise ValueError("故事不存在：" + str(story_id))
    profile = StoryProfile.from_story(story_id)
    chars = asset_core.list_assets(story_id, "characters")
    scens = asset_core.list_assets(story_id, "scenes")
    visuals = asset_core.list_assets(story_id, "visuals")
    state = state_core.latest_state(story_id)

    storyboard = _storyboard(profile, story)
    amap = _speaker_alias_map(profile)
    script_txt = _normalize_script((story.get("previs") or {}).get("story_script") or "", profile, amap)
    by_spk = {}
    for spk, _, line in script_text.dialogue_lines(script_txt, list(amap)):
        code = amap.get(spk)
        if code:
            by_spk.setdefault(code, []).append(line)
    audio = _audio_plan(profile, storyboard, amap, by_spk)
    from . import project_settings
    ps = project_settings.get(story)
    h3_prompts = _compile_h3_prompts(profile, storyboard, audio, amap, by_spk, ps)
    # —— 自动修复（第 1 层自动修 / 第 2 层默认补），必须在 text_checks 之前跑 ——
    repair_notes = []
    repaired_scenes = []
    for s in scens:
        rs, notes = auto_repair.repair_scene(s, [x.get("name") for x in scens])
        repaired_scenes.append(rs)
        repair_notes += notes
    image_prompts = {}
    for k, t in _image_prompts(profile, chars, repaired_scenes, ps).items():
        t2, notes = auto_repair.repair_prompt(t, profile.id_map)
        image_prompts[k] = t2
        repair_notes += notes
    ledger = _ledger(profile, state)

    adopted = {v.get("owner_id"): v for v in visuals if v.get("status") == "adopted"}
    ref_plan = []
    for seq in profile.sequences:
        pics = []
        for c in profile.characters:
            v = adopted.get(c["id"])
            if v:
                pics.append({"source": v.get("visual_id"), "role": "character_identity",
                             "natural_name": c["name"] + " Master", "purpose": "锁定角色身份"})
        sc0 = profile.scenes[0] if profile.scenes else None
        if sc0:
            v = adopted.get(sc0["scene_id"])
            if v:
                pics.append({"source": v.get("visual_id"), "role": "scene_master",
                             "natural_name": sc0["name"] + " Master", "purpose": "锁定场景结构"})
        ref_plan.append({"sequence_id": seq["sequence_id"], "pictures": pics,
                         "continuation": bool(seq.get("continuation"))})
    ref_plan_repaired = []
    for seq in ref_plan:
        pics, notes = auto_repair.repair_reference_pack(seq["pictures"],
                                                        is_continuation=seq.get("continuation"))
        ref_plan_repaired.append({**seq, "pictures": pics})
        repair_notes += notes
    ref_plan = ref_plan_repaired

    ep = profile.episode or {}
    script = {profile.scenes[0]["scene_id"]: script_txt} if profile.scenes else {}
    canon_texts = [x for x in [story.get("one_line"), story.get("preview_summary"),
                               script_txt, (ep.get("brief") or {}).get("synopsis")] if x]

    artifacts = {
        "prompts": h3_prompts,
        "image_prompts": image_prompts,
        "script": script,
        "audio": audio,
        "storyboard": storyboard,
        "ref_plan": ref_plan,
        "ledger": ledger,
        "wardrobe": _wardrobe_slots(profile, chars),
        "krea_prompt_ids": list(image_prompts),
        "asset_entries": [{"asset_id": v.get("visual_id"), "type": v.get("kind"),
                           "name": v.get("visual_id"), "prompt_file": ""} for v in visuals],
        "custom_profile_path": store.DATA / "content_profiles" / "user_custom_rules.txt"
                               if (store.DATA / "content_profiles" / "user_custom_rules.txt").exists() else None,
        "required_shot_fields": ("duration", "framing", "camera_movement", "blocking", "performance"),
        "canon_texts": canon_texts,
    }
    result = text_checks.run_all(profile, artifacts)
    result["repair_notes"] = repair_notes

    d = out_dir(story_id)
    store.save_json(d / "result.json", result)
    store.save_json(d / "prompts.json", {"h3": h3_prompts, "image": image_prompts})
    save_artifacts = dict(artifacts)
    save_artifacts["custom_profile_path"] = str(artifacts["custom_profile_path"]) \
        if artifacts["custom_profile_path"] else None
    store.save_json(d / "artifacts.json", save_artifacts)
    store.save_json(d / "trace.json", get_trace(story_id))

    lines = ["# 文字链验收：%s" % story_id, "",
             "> 结论：%s（%d 项，规则唯一来源 cores/text_checks.py）" %
             (result["result"], len(result["checks"])), ""]
    if repair_notes:
        lines += ["已自动处理 %d 处（可展开查看）：" % len(repair_notes), ""]
        lines += ["- " + n for n in repair_notes]
        lines.append("")
    for c in result["checks"]:
        mark = "PASS" if c["ok"] else ("SKIP" if c.get("skipped") else "FAIL")
        lines.append("- [%s] %s：%s" % (mark, c["item"], c["detail"]))
    (d / "result.md").write_text("\n".join(lines), encoding="utf-8")
    return result


def get_validation(story_id):
    """唯一职责：读最近一次文字链验收结果。"""
    return store.load_json(out_dir(story_id) / "result.json", {})


def get_trace(story_id, target=None):
    """唯一职责：给出 母种子→母计划→事件→剧本→状态→导演→分镜→最终提示词 的来源链。"""
    story = story_core.get_story(story_id) or {}
    ep = narrative_core.load_episode(story_id, story.get("current_episode") or 1) or {}
    plan = story.get("master_plan") or {}
    previs = story.get("previs") or {}
    chain = [
        {"stage": "母种子", "source": "story.one_line", "content": story.get("one_line", "")},
        {"stage": "母计划", "source": "story.master_plan",
         "content": "；".join(filter(None, [plan.get("core_conflict"), plan.get("protagonist_goal")]))},
        {"stage": "事件", "source": "episode.events",
         "content": "；".join(e.get("title", "") for e in (ep.get("events") or []))},
        {"stage": "剧本", "source": "story.previs.story_script",
         "content": (previs.get("story_script") or "")[:500]},
        {"stage": "状态", "source": "state_core.latest_state",
         "content": str(state_core.latest_state(story_id) or {}).replace("'", "")[:500]},
        {"stage": "导演", "source": "story.previs.director_intent",
         "content": str(previs.get("director_intent") or "")[:300]},
        {"stage": "分镜", "source": "story.previs.shots",
         "content": "%d 镜" % len(previs.get("shots") or [])},
    ]
    prompts = store.load_json(out_dir(story_id) / "prompts.json", {})
    if target and target in (prompts.get("h3") or {}):
        chain.append({"stage": "最终提示词", "source": target, "content": prompts["h3"][target]})
    elif target is None:
        for sid in (prompts.get("h3") or {}):
            chain.append({"stage": "最终提示词", "source": sid, "content": prompts["h3"][sid]})
    return chain
