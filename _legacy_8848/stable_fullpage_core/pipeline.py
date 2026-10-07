# -*- coding: utf-8 -*-
"""stable_fullpage_core 正式生产链：整页生成 + 排字 + 完整PDF。
唯一整页路线；不调用任何 v2.3—v2.7 提示词分支与逐格生成。"""
import os, sys, time, json, re
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
from stable_fullpage_core.contracts import (
    FrozenProjectContract, extract_explicit_locked_facts,
    validate_confirmed_against_contract)
from stable_fullpage_core import models, planning, prompt_compiler, audit, storage

OUT_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "漫画")


def _script_pages_from_stories(stories, contract, visual_plans):
    cards = app.parse_char_sheet(str(contract.confirmedCharacterCards or ""))
    pages = []
    for i, s in enumerate(stories, 1):
        plan = (visual_plans or {}).get(str(i)) or {}
        people = []
        for p in (plan.get("panels") or []):
            for n in (p.get("people") or []):
                n = str(n or "").strip()
                if n and n not in people:
                    people.append(n)
        page_chars = []
        for nm in cards.keys():
            if nm in people or any(
                    re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", nm) ==
                    re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", pn)
                    for pn in people):
                page_chars.append({"name": nm})
        pages.append({
            "page": i, "source_text": str(s),
            "characters": page_chars,
            "scene": str(plan.get("scene") or
                         contract.confirmedSceneSetting or "").split(
                "【场景：")[-1].split("】")[0] or "",
            "image": "", "seed": None,
            "panels": [], "lettering": [],
        })
    return pages


def _load_existing_bible(continue_from):
    """读取前作故事圣经（可选；读取失败返回None，不静默切换旧pipeline）。"""
    if not str(continue_from or "").strip():
        return None
    try:
        prev = storage.load_stable_script(str(continue_from))
        if prev.get("stableLongFormBible"):
            from stable_fullpage_core.models import LongFormStoryBible
            return LongFormStoryBible.from_dict(prev["stableLongFormBible"])
    except Exception:
        return None
    return None


def build_stable_project(config):
    """前端 /api/script_build 的 stable 入口。
    用户原始输入 → 确认设定 → FrozenProjectContract → 逐页规划 → 落盘。"""
    genre = str(config.get("genre") or config.get("selectedGenre") or "").strip()
    style_name = str(config.get("styleName") or config.get("selectedStyleName") or
                     "").strip()
    style_text = str(config.get("styleText") or config.get("selectedStyleText") or
                     "").strip()
    raw = str(config.get("rawUserText") or config.get("text") or "").strip()
    if not genre or genre.lower() == "auto":
        raise RuntimeError("题材必须手动选择，不能为auto")
    if not style_name or not style_text:
        raise RuntimeError("画风必须手动选择完整文本")
    confirmed_world = str(config.get("worldSetting") or
                          config.get("confirmedWorldSetting") or "")
    confirmed_cards = str(config.get("charSheet") or
                          config.get("confirmedCharacterCards") or "")
    confirmed_scene = str(config.get("sceneSetting") or
                          config.get("confirmedSceneSetting") or "")
    confirmed_synopsis = str(config.get("episodeSynopsis") or
                             config.get("confirmedEpisodeSynopsis") or raw)
    contract = FrozenProjectContract(
        raw_user_text=raw,
        selected_genre=genre,
        selected_style_name=style_name,
        selected_style_text=style_text,
        confirmed_world_setting=confirmed_world,
        confirmed_character_cards=confirmed_cards,
        confirmed_scene_setting=confirmed_scene,
        confirmed_episode_synopsis=confirmed_synopsis,
        confirmed_character_options=config.get("characterOptions") or [],
        confirmed_world_rules=config.get("worldRules") or {},
        explicit_locked_facts=extract_explicit_locked_facts(
            raw, confirmed_world, confirmed_cards, confirmed_scene,
            confirmed_synopsis,
            sources=("user", "confirmed_world", "confirmed_characters",
                     "confirmed_scene", "confirmed_synopsis")),
    )
    errs = contract.validate()
    if errs:
        raise RuntimeError("；".join(errs))
    errs = validate_confirmed_against_contract(contract)
    if errs:
        raise RuntimeError("已确认内容校验失败：" + "；".join(errs[:8]))
    content_bias = str(config.get("contentBias") or "").strip()
    content_bias_custom = str(config.get("contentBiasCustom") or "").strip()
    char_anchors = models.make_character_anchors(contract.confirmedCharacterCards)
    scene_anchors = models.make_scene_anchors(
        contract.confirmedSceneSetting,
        contract.confirmedSceneSetting.split("【场景：")[-1].split("】")[0] or
        "主场景")
    text_only = bool(config.get("textOnly"))
    target = int(config.get("targetPages") or 0) or None
    plan_hint = str(config.get("episodePlanHint") or "").strip()
    existing_bible = _load_existing_bible(config.get("continueFrom") or "")
    slim = bool(config.get("slimPlanner"))
    if existing_bible is not None:
        if slim:
            from stable_fullpage_core import slim_planning
            long = slim_planning.continue_story_episode_slim(
                contract, existing_bible, target,
                title=config.get("title") or "未命名",
                episode_no=config.get("episodeNo"), text_only=text_only,
                plan_hint=plan_hint, content_bias=content_bias,
                content_bias_custom=content_bias_custom)
        else:
            long = planning.continue_story_episode(
                contract, existing_bible, target,
                title=config.get("title") or "未命名",
                episode_no=config.get("episodeNo"), text_only=text_only,
                plan_hint=plan_hint)
    else:
        if slim:
            from stable_fullpage_core import slim_planning
            long = slim_planning.assemble_story_episode_slim(
                contract, target,
                title=config.get("title") or "未命名",
                project_id=str(config.get("seriesId") or
                               config.get("seriesTitle") or ""),
                bible=None, text_only=text_only, plan_hint=plan_hint,
                content_bias=content_bias,
                content_bias_custom=content_bias_custom)
        else:
            long = planning.assemble_story_episode(
                contract, target,
                title=config.get("title") or "未命名",
                project_id=str(config.get("seriesId") or
                               config.get("seriesTitle") or ""),
                bible=None, text_only=text_only, plan_hint=plan_hint)
    # build 阶段只保存 draft；锁定移到正式 render 入口
    episode_draft = long["episodeDraft"]
    pages = _script_pages_from_stories(
        long["pageStories"], contract, long["stableFullPageVisualPlans"])
    visual_states = long["stablePageVisualStates"]
    script = {
        "name": config.get("title") or "未命名",
        "title": config.get("title") or "未命名",
        "art": style_text,
        "directorVersion": "stable_fullpage_core_v1",
        "originalPipeline": "stable_fullpage_core_v1",
        "currentPipeline": "stable_fullpage_core_v1",
        "lastRenderedPipeline": "",
        "charSheet": contract.confirmedCharacterCards,
        "sceneSetting": contract.confirmedSceneSetting,
        "contentBias": content_bias,
        "contentBiasCustom": content_bias_custom,
        "pages": pages,
        "stableContract": contract.to_dict(),
        "stableCharacterAnchors": char_anchors,
        "stableSceneAnchors": scene_anchors,
        "stablePageStories": long["pageStories"],
        "stableFullPageVisualPlans": long["stableFullPageVisualPlans"],
        "stablePageTextPlans": long["stablePageTextPlans"],
        "stablePageReadabilityPlans": long["stablePageReadabilityPlans"],
        "stablePageVisualStates": visual_states,
        "stableEpisodePageMissionMap": long.get("episodePageMissionMap") or {},
        "stablePageStoryRecords": long.get("pageStoryRecords") or {},
        "stableEpisodeActualEndState": long.get("episodeActualEndState") or {},
        "stableEpisodeDeliveryLedger": long.get("episodeDeliveryLedger") or {},
        "stableEpisodeBrief": long.get("episodeBrief") or {},
        "stableEpisodeBeats": long.get("episodeBeats") or [],
        "stableEpisodeEditorialReview": long.get("episodeEditorialReview") or {},
        "stableLongFormBible": long["bible"],
        "stableSlimPlanner": bool(config.get("slimPlanner")),
        "stableLocationAnchors": long.get("locationAnchors") or {},
        "stableSeriesPlan": long["seriesPlan"],
        "stableVolumePlans": [long["volumePlan"]],
        "stableArcPlans": [long["arcPlan"]],
        "stableEpisodePlans": long["episodeWindow"].get("episodes") or [],
        "stableCurrentEpisodePlan": long["episodePlan"],
        "stableEpisodeDraft": episode_draft,
        "stableEpisodePacingPlan": long["episodePacingPlan"],
        "stableEpisodeContext": long["episodeContext"],
        "stableTextOnly": False if slim else text_only,
        "stableRenderRecords": {},
        "stableSeedBase": contract.projectSeedBase,
        "confirmedAt": contract.confirmedAt,
    }
    storage.save_stable_script(script)
    return script


def _page_out_dir(script):
    d = os.path.join(OUT_ROOT, "stable_" + storage._safe(
        script.get("name") or script.get("title") or "project"))
    os.makedirs(d, exist_ok=True)
    return d


def _set_page_image(script, page_no, final):
    """把稳定整页的成品图写回 pages[].image，前端画廊与重新合成PDF才能读到。"""
    idx = int(page_no) - 1
    pages = script.get("pages") or []
    if 0 <= idx < len(pages):
        pages[idx]["image"] = os.path.relpath(
            final, app.COMIC_DIR).replace("\\", "/")


def _scene_anchor_for_page(script, page_no):
    loc_anchors = script.get("stableLocationAnchors") or {}
    anchors = script.get("stableSceneAnchors") or {}
    plan = (script.get("stableFullPageVisualPlans") or {}).get(str(page_no)) or {}
    scene_name = str(plan.get("scene") or "") or \
        str((script.get("pages") or [{}])[0].get("scene") or "主场景")
    if scene_name in loc_anchors:
        return loc_anchors[scene_name]
    for a in anchors.values():
        if a.get("sceneName") == scene_name:
            return a
    return models.FrozenSceneRenderAnchor(
        models._stable_id(scene_name), scene_name,
        str(script.get("sceneSetting") or "")).to_dict()


def _rows_from_text_plan(text_plan):
    rows = []
    for u in (text_plan or {}).get("textUnits") or []:
        kind = {"dialogue": "dialogue", "innerThought": "innerThought",
                "sfx": "sfx"}.get(str(u.get("type") or ""), "narration")
        pos = str(u.get("placementPreference") or "topLeft")
        pm = {"topLeft": "top_left", "topRight": "top_right",
              "bottomLeft": "bottom_left", "bottomRight": "bottom_right"}
        rows.append({"panel": max(1, int(u.get("panel") or 1)), "kind": kind,
                     "text": str(u.get("visibleText") or u.get("text") or ""),
                     "speaker": "", "position": pm.get(pos, "top_left")})
    return rows


def _render_one_page(script, page_no, retry_index=0):
    contract = FrozenProjectContract.from_dict(script.get("stableContract") or {})
    errs = contract.validate()
    if errs:
        raise RuntimeError("；".join(errs))
    plan = (script.get("stableFullPageVisualPlans") or {}).get(str(page_no))
    if not plan:
        raise RuntimeError("第%d页缺少stable整页画面计划" % page_no)
    prompt = prompt_compiler.compile_stable_fullpage_prompt(
        contract, script.get("stableCharacterAnchors") or {},
        _scene_anchor_for_page(script, page_no), plan, page_no,
        visual_states=script.get("stablePageVisualStates") or {})
    base_seed = int(script.get("stableSeedBase") or 0) + int(page_no) * 9973
    seed = base_seed + int(retry_index) * 7919
    out_dir = _page_out_dir(script)
    base = os.path.join(out_dir, "base_P%02d.png" % page_no)
    t0 = time.time()
    app.comfy_generate_page(prompt, seed, base, "high")
    audit_res = audit.stable_basic_audit(base)
    final = os.path.join(out_dir, "P%02d_finished.png" % page_no)
    rows = _rows_from_text_plan(
        (script.get("stablePageTextPlans") or {}).get(str(page_no)) or {})
    app.add_lettering_to_page(
        base, rows, final,
        panel_count=max(1, len(plan.get("panels") or [])),
        layout="adaptive", lettering_size="balanced")
    record = {
        "pageNumber": page_no,
        "finalPrompt": prompt,
        "finalPromptHash": models._sha(prompt),
        "styleHash": contract.sourceHashes["styleHash"],
        "characterAnchorHashes": {
            k: (v.get("anchorHash") if isinstance(v, dict) else "")
            for k, v in (script.get("stableCharacterAnchors") or {}).items()},
        "sceneAnchorHash": _scene_anchor_for_page(script, page_no).get("anchorHash"),
        "pageSeed": seed,
        "retryIndex": int(retry_index),
        "outputPath": final,
        "elapsed": round(time.time() - t0, 2),
        "needsUserReview": audit_res.get("needsReview", False),
        "audit": audit_res,
    }
    return record, base, final


def _pdf_page_count(path):
    """可选PDF功能使用用户自己安装的pypdf，不依赖作者的运行时目录。"""
    try:
        import pypdf
    except ImportError as e:
        raise RuntimeError("可选PDF功能需要pypdf，请安装：python -m pip install pypdf") from e
    r = pypdf.PdfReader(str(path))
    return len(r.pages)


def _ensure_episode_locked(script):
    ep = script.get("stableEpisodeDraft") or {}
    if ep.get("status") == "draft":
        models.lock_episode_for_production(ep)
        storage.save_stable_script(script)
    if ep.get("status") != "production_locked":
        raise RuntimeError("episode状态必须是draft或production_locked")
    return ep


def _generate_base_page(script, page_no, retry_index=0):
    contract = FrozenProjectContract.from_dict(script.get("stableContract") or {})
    plan = (script.get("stableFullPageVisualPlans") or {}).get(str(page_no))
    if not plan:
        raise RuntimeError("第%d页缺少stable整页画面计划" % page_no)
    prompt = prompt_compiler.compile_stable_fullpage_prompt(
        contract, script.get("stableCharacterAnchors") or {},
        _scene_anchor_for_page(script, page_no), plan, page_no,
        visual_states=script.get("stablePageVisualStates") or {})
    seed = (int(script.get("stableSeedBase") or 0) + int(page_no) * 9973 +
            int(retry_index) * 7919)
    out_dir = _page_out_dir(script)
    base = os.path.join(out_dir, "base_P%02d.png" % page_no)
    app.comfy_generate_page(prompt, seed, base, "high")
    return prompt, seed, base


def _letter_page(script, page_no, base=None):
    plan = (script.get("stableFullPageVisualPlans") or {}).get(str(page_no))
    out_dir = _page_out_dir(script)
    base = base or os.path.join(out_dir, "base_P%02d.png" % page_no)
    final = os.path.join(out_dir, "P%02d_finished.png" % page_no)
    rows = _rows_from_text_plan(
        (script.get("stablePageTextPlans") or {}).get(str(page_no)) or {})
    app.add_lettering_to_page(
        base, rows, final,
        panel_count=max(1, len((plan or {}).get("panels") or [])),
        layout="adaptive", lettering_size="balanced")
    return final


def render_stable_project(script, pages_to_render=None, force_redo=False):
    """整话整页渲染（分阶段）：
    A规划已在build完成 → B Krea批量 → C视觉批量单图审查 → D批量重出(≤2轮)
    → 排字 → PDF(pypdf实际页数) → canon提交 → 三话滚动。
    部分 pages_to_render：只渲染指定页，不生成PDF/不提交/不滚动。"""
    contract = FrozenProjectContract.from_dict(script.get("stableContract") or {})
    errs = contract.validate()
    if errs:
        raise RuntimeError("；".join(errs))
    records = script.setdefault("stableRenderRecords", {})
    ep = script.get("stableEpisodeDraft") or {}
    if not ep.get("episodeId"):
        raise RuntimeError("缺少stableEpisodeDraft")
    already_committed = ep.get("status") == "completed_canon"
    if already_committed and not force_redo:
        if pages_to_render:
            for pno in [int(x) for x in pages_to_render]:
                record, base, final = _render_one_page(script, pno, 0)
                records[str(pno)] = record
                _set_page_image(script, pno, final)
                storage.save_stable_script(script)
            return {"pdf": "", "records": records,
                    "needsReviewPages": [],
                    "partial": True, "alreadyCommitted": True}
        return {"pdf": script.get("stablePdf") or "",
                "records": records,
                "needsReviewPages": [str(p) for p, r in records.items()
                                     if r.get("needsUserReview")],
                "alreadyCommitted": True}
    # 部分渲染：只更新指定页，不PDF/commit/roll
    if pages_to_render:
        for pno in [int(x) for x in pages_to_render]:
            record, base, final = _render_one_page(script, pno, 0)
            records[str(pno)] = record
            _set_page_image(script, pno, final)
            storage.save_stable_script(script)
        return {"pdf": "", "records": records,
                "needsReviewPages": [str(p) for p, r in records.items()
                                     if r.get("needsUserReview")],
                "partial": True}
    if not already_committed:
        ep = _ensure_episode_locked(script)
    target_pages = int(ep.get("targetPages") or 0)
    if target_pages <= 0:
        raise RuntimeError("stableEpisodeDraft.targetPages缺失")
    targets = list(range(1, target_pages + 1))
    out_dir = _page_out_dir(script)
    def _prog(percent, detail, kind=None):
        try:
            if kind:
                app.op_update("auto_job", percent=percent, detail=detail, kind=kind)
            else:
                app.op_update("auto_job", percent=percent, detail=detail)
        except Exception:
            pass
    _prog(0.02, "准备基础页…")
    # 阶段B：批量基础页（跳过已有合格基础页，断点续跑）
    base_paths = {}
    for pno in targets:
        old = records.get(str(pno)) or {}
        base = os.path.join(out_dir, "base_P%02d.png" % pno)
        if not force_redo and old.get("baseOk") and os.path.isfile(base) and \
                not old.get("needsUserReview"):
            base_paths[pno] = base
            _prog(0.05 + 0.55 * len(base_paths) / max(1, len(targets)),
                  "基础页沿用 %d/%d" % (len(base_paths), len(targets)),
                  kind="krea2_1024x1792")
            continue
        prompt, seed, base = _generate_base_page(script, pno, 0)
        base_paths[pno] = base
        audit_res = audit.stable_basic_audit(base)
        records[str(pno)] = {
            "pageNumber": pno, "finalPrompt": prompt,
            "finalPromptHash": models._sha(prompt),
            "styleHash": contract.sourceHashes["styleHash"],
            "pageSeed": seed, "retryIndex": 0,
            "baseOk": True, "basePath": base,
            "needsUserReview": audit_res.get("needsReview", False),
            "audit": audit_res}
        storage.save_stable_script(script)
        _prog(0.05 + 0.55 * len(base_paths) / max(1, len(targets)),
              "基础页出图 %d/%d" % (len(base_paths), len(targets)),
              kind="krea2_1024x1792")
    # 阶段C：视觉批量单图审查
    issues_map = {}
    # 视觉审查默认关闭；只有显式 STABLE_ENABLE_VISUAL_REVIEW=1 时才启用。
    visual_setting = os.environ.get("STABLE_ENABLE_VISUAL_REVIEW", "").strip()
    if visual_setting == "1":
        review_targets = list(targets)
    else:
        review_targets = []
    visual_enabled = bool(review_targets)
    if visual_enabled:
        try:
            app.comfy_free()
            app.qwen36_start()
        except Exception:
            pass
        reviewed = 0
        for pno in review_targets:
            fp, issues = audit.visual_review_page(base_paths[pno], script, pno)
            records[str(pno)]["visualFingerprint"] = fp
            records[str(pno)]["visualIssues"] = issues
            if issues:
                issues_map[pno] = issues
            reviewed += 1
            _prog(0.62 + 0.13 * reviewed / max(1, len(review_targets)),
                  "视觉质检 %d/%d" % (reviewed, len(review_targets)),
                  kind="vision_call")
    # 阶段D：批量重出（最多两轮）
    rerender_done = 0
    if visual_enabled:
        # 成人向自动模式只重出一轮；显式全量审查保留最多两轮。
        for round_i in ((1, 2) if visual_setting == "1" else (1,)):
            fail = [p for p in issues_map if issues_map[p]]
            if not fail:
                break
            _prog(0.78, "重出问题页（第 %d 轮，共 %d 页）" % (round_i, len(fail)),
                  kind="krea2_1024x1792")
            try:
                app.qwen36_free()
            except Exception:
                pass
            for pno in fail:
                prompt, seed, base = _generate_base_page(script, pno, round_i)
                base_paths[pno] = base
                records[str(pno)].update({
                    "finalPrompt": prompt,
                    "finalPromptHash": models._sha(prompt),
                    "pageSeed": seed, "retryIndex": round_i,
                    "baseOk": True, "basePath": base})
                storage.save_stable_script(script)
                rerender_done += 1
                _prog(0.78 + 0.12 * rerender_done / max(1, len(fail)),
                      "重出问题页 %d/%d" % (rerender_done, len(fail)),
                      kind="krea2_1024x1792")
            try:
                app.comfy_free()
                app.qwen36_start()
            except Exception:
                pass
            new_map = {}
            for pno in fail:
                fp, issues = audit.visual_review_page(
                    base_paths[pno], script, pno)
                records[str(pno)]["visualFingerprint"] = fp
                records[str(pno)]["visualIssues"] = issues
                if issues:
                    new_map[pno] = issues
                    records[str(pno)]["needsUserReview"] = True
            issues_map = new_map
    # 排字（基础页全部完成；有问题的页标记 needsUserReview 但仍排字出图）
    finals = {}
    for lettered, pno in enumerate(targets, 1):
        final = _letter_page(script, pno, base_paths[pno])
        finals[pno] = final
        records[str(pno)]["outputPath"] = final
        _set_page_image(script, pno, final)
        rec = records[str(pno)]
        rec["needsUserReview"] = bool(rec.get("needsUserReview") or
                                      issues_map.get(pno))
        storage.save_stable_script(script)
        _prog(0.92 + 0.05 * lettered / max(1, len(targets)),
              "排字 %d/%d" % (lettered, len(targets)),
              kind="lettering")
    script["stableRenderRecords"] = records
    script["lastRenderedPipeline"] = "stable_fullpage_core_v1"
    # PDF：由整话全部页面生成，pypdf 实际读页数
    _prog(0.98, "正在合成 PDF…", kind="pdf_merge")
    ordered = [finals[p] for p in targets]
    title = str(script.get("title") or script.get("name") or "漫画") + \
        "·stable_fullpage_core_v1·整页成品版"
    pdf, url = app._auto_pdf(title, ordered, project=script)
    if not pdf or not os.path.isfile(pdf):
        raise RuntimeError("PDF未生成")
    pdf_pages = _pdf_page_count(pdf)
    if pdf_pages != target_pages:
        raise RuntimeError("PDF页数不符：%d != %d" % (pdf_pages, target_pages))
    # 首次成品才提交canon并滚动窗口；已经提交的整话重出只更新图片与PDF。
    if not already_committed:
        from stable_fullpage_core.models import (
            LongFormStoryBible, commit_completed_episode_to_canon)
        bible = LongFormStoryBible.from_dict(script.get("stableLongFormBible") or {})
        commit = commit_completed_episode_to_canon(
            bible, ep, target_pages, ordered, pdf, pdf_pages,
            state_transitions=None,
            episode_plan=script.get("stableCurrentEpisodePlan") or {},
            page_stories=script.get("stablePageStories") or [])
        script["stableCanonCommit"] = commit
        script["stableLongFormBible"] = bible.to_dict()
        # 三话滚动正式接入
        window = {"episodes": script.get("stableEpisodePlans") or []}
        if not window["episodes"] and bible.draftPlans.get("window"):
            window = bible.draftPlans["window"]
        if window.get("episodes"):
            rolled = planning.roll_episode_window_after_commit(
                bible, window, contract, ep.get("episodeNo"))
            script["stableEpisodePlans"] = rolled["episodes"]
            bible.draftPlans["window"] = rolled
            script["stableLongFormBible"] = bible.to_dict()
    script["stablePdf"] = pdf
    script["pdf"] = url
    storage.save_stable_script(script)
    return {"pdf": url, "records": records,
            "needsReviewPages": [str(p) for p, r in records.items()
                                 if r.get("needsUserReview")]}


def rerender_stable_page(script, page_no, retry_index=0):
    """只重出当前页；不重新提交canon、不重算状态、不滚动窗口。"""
    record, base, final = _render_one_page(script, int(page_no), int(retry_index))
    script.setdefault("stableRenderRecords", {})[str(page_no)] = record
    _set_page_image(script, int(page_no), final)
    script["lastRenderedPipeline"] = "stable_fullpage_core_v1"
    storage.save_stable_script(script)
    return record
