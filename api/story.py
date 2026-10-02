# -*- coding: utf-8 -*-
"""story 域路由（由 server.py 的 do_POST 拆分而来）。"""
from api import post
from api._shared import _RESP, ctx
from cores import style_presets
globals().update(ctx())


@post("/api/stories")
def r_000(h, path, d):
    st = story_core.create_story(str(d.get("one_line") or ""), str(d.get("title") or ""),
                                 str(d.get("mode") or "short"))
    return _RESP({"ok": True, "story": st})

@post(("/api/story/", "/plan"))
def r_001(h, path, d):
    sid = path.split("/")[3]
    st = story_core.get_story(sid)
    if not st:
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    plan = story_core.generate_master_plan(st, float(d.get("temperature") or 0.7))
    return _RESP({"ok": True, "plan": plan})

@post(("/api/story/", "/episode"))
def r_002(h, path, d):
    sid = path.split("/")[3]
    st = story_core.get_story(sid)
    ep = narrative_core.generate_episode(st, int(d.get("episode_no") or 1),
                                         float(d.get("temperature") or 0.7))
    return _RESP({"ok": True, "episode": ep})

@post(("/api/story/", "/state-commit"))
def r_003(h, path, d):
    sid = path.split("/")[3]
    st = story_core.get_story(sid)
    ep_no = int(d.get("episode_no") or st.get("current_episode") or 1)
    snap = state_core.commit_state(st, ep_no, str(d.get("source_note") or "用户采用"))
    return _RESP({"ok": True, "state": snap})

@post(("/api/story/", "/characters/", "/master"))
def r_004(h, path, d):
    parts = path.split("/")
    sid, cid = parts[3], parts[5]
    # 人设图是人物身份锚点：一旦已有采用版就永久复用，不再重抽脸和身材。
    existing = next((v for v in asset_core.list_assets(sid, "visuals")
                     if v.get("owner_id") == cid and v.get("status") == "adopted"), None)
    if existing and not d.get("redo"):
        return _RESP({"ok": True, "visual": existing, "prompt": "", "reused": True,
                      "note": "已有采用的人设图，本次直接复用"})           # P405：带 redo 才重出（成品页「重新生成」）
    from cores import project_settings
    st = story_core.get_story(sid) or {}
    v, prompt = asset_core.generate_character_master(sid, cid, str(d.get("style") or project_settings.style_of(st)),
                                                     str(d.get("quality") or "formal"))
    try:
        v = asset_core.adopt(sid, v["visual_id"])                  # P398：生成即采用（用户：采用按钮去掉）
    except Exception:
        pass
    return _RESP({"ok": True, "visual": v, "prompt": prompt})

@post(("/api/story/", "/scenes/", "/master"))
def r_005(h, path, d):
    parts = path.split("/")
    sid, scid = parts[3], parts[5]
    from cores import project_settings
    st = story_core.get_story(sid) or {}
    v, prompt = asset_core.generate_scene_master(sid, scid, str(d.get("style") or project_settings.style_of(st)),
                                                 str(d.get("quality") or "formal"))
    try:
        v = asset_core.adopt(sid, v["visual_id"])                  # P398：生成即采用
    except Exception:
        pass
    return _RESP({"ok": True, "visual": v, "prompt": prompt})

@post(("/api/story/", "/characters"))
def r_006(h, path, d):
    sid = path.split("/")[3]
    c = asset_core.create_character(sid, d)
    return _RESP({"ok": True, "character": c})

@post(("/api/story/", "/scenes"))
def r_007(h, path, d):
    sid = path.split("/")[3]
    sc = asset_core.create_scene(sid, d)
    return _RESP({"ok": True, "scene": sc})

@post(("/api/story/", "/adopt"))
def r_008(h, path, d):
    parts = path.split("/")
    sid, vid = parts[3], parts[5]
    v = asset_core.adopt_visual(sid, vid)
    return _RESP({"ok": True, "visual": v})

@post(("/api/story/", "/ask"))
def r_009(h, path, d):
    sid = path.split("/")[3]
    from models import qwen_client
    story = story_core.get_story(sid)
    ep = narrative_core.load_episode(sid, story.get("current_episode") or 1) if story else None
    st = state_core.latest_state(sid)
    ctx = context_engine.build_context("ask_ai", story or {}, extra={"question": d.get("question", ""),
        "episode": ep.get("brief", {}) if ep else None})
    sys = "你是创作助手。回答基于当前故事资料；默认只读，不写回项目。"
    user = ctx["user"] + "\n问题：" + str(d.get("question") or "")
    reply = qwen_client.chat_for("ask_ai", sys, user)
    return _RESP({"ok": True, "reply": reply})

@post(("/api/story/", "/chat"))
def r_010(h, path, d):
    parts = path.split("/")
    sid = parts[3]
    from models import qwen_client
    vid = d.get("vchar_id") or ""
    c = asset_core.get_asset(sid, "characters", vid)
    if not c:
        return _RESP({"ok": False, "error": "虚拟人物不存在"}, 404)
    persona = ("你是" + (c.get("name") or "这个角色") + "。" +
               "，".join(x for x in [c.get("char_type"), c.get("personality"), c.get("look"), c.get("clothing")] if x) +
               "。你是从故事人物创建的独立虚拟人物：聊天关系与当前心情不写回故事人物。")
    reply = qwen_client.chat_for("vchar", persona, str(d.get("message") or ""))
    return _RESP({"ok": True, "reply": reply})

@post(("/api/story/", "/vchar/create"))
def r_011(h, path, d):
    sid = path.split("/")[3]
    cid = d.get("character_id") or ""
    c = asset_core.get_asset(sid, "characters", cid)
    if not c:
        return _RESP({"ok": False, "error": "人物不存在"}, 404)
    v = {"vchar_id": c["character_id"], "name": c.get("name"), "persona": c.get("personality", ""),
         "identity": c.get("identity_anchor", ""), "clothing": c.get("clothing", ""),
         "created": time.time(), "story_id": sid}
    store.save_json(store.DATA / "assets" / sid / "vchars" / (v["vchar_id"] + ".json"), v)
    return _RESP({"ok": True, "vchar": v})

@post(("/api/story/", "/comic"))
def r_012(h, path, d):
    sid = path.split("/")[3]
    if not story_core.get_story(sid):
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    t = runtime_core.create_task("comic", "漫画：" + sid,
                                 {"story_id": sid, "episode_no": int(d.get("episode_no") or 1),
                                  "target_pages": int(d.get("target_pages") or 8)})
    return _RESP({"ok": True, "task": t})

@post(("/api/story/", "/novel"))
def r_013(h, path, d):
    sid = path.split("/")[3]
    if not story_core.get_story(sid):
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    t = runtime_core.create_task("novel", "小说：" + sid,
                                 {"story_id": sid, "episode_no": int(d.get("episode_no") or 1),
                                  "chapter_count": int(d.get("chapter_count") or 10)})
    return _RESP({"ok": True, "task": t})

@post(("/api/story/", "/album"))
def r_014(h, path, d):
    sid = path.split("/")[3]
    if not story_core.get_story(sid):
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    t = runtime_core.create_task("album", "画册：" + sid,
                                 {"story_id": sid, "theme": str(d.get("theme") or ""),
                                  "count": int(d.get("count") or 3),
                                  "style": str(d.get("style") or "电影级写实"),
                                  "kind": str(d.get("kind") or "cinematic")})
    return _RESP({"ok": True, "task": t})

@post(("/api/story/", "/video/formal"))
def r_015(h, path, d):
    sid = path.split("/")[3]
    if not story_core.get_story(sid):
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    t = runtime_core.create_task("video_formal", "正式视频：" + sid,
                                 {"story_id": sid, "episode_no": int(d.get("episode_no") or 1),
                                  "target_seconds": int(d.get("target_seconds") or 30)})
    return _RESP({"ok": True, "task": t})

@post(("/api/story/", "/video/quick"))
def r_016(h, path, d):
    sid = path.split("/")[3]
    from production import video_core
    from models import h3_client
    text = str(d.get("text") or "")
    if not text:
        return _RESP({"ok": False, "error": "缺少提示词"}, 400)
    try:
        prompt = video_core.build_h3_prompt({"purpose": text}, {}, mode="t2va", extra_text=text)
        prompt_source = "h3_official_t2va"
    except Exception:
        prompt = text
        prompt_source = "fallback_raw"
    res = h3_client.generate("t2va", prompt, seconds=int(d.get("seconds") or 15))
    dur = video_core.probe_duration(res["output_path"])
    m = quality_core.build_manifest(
        output_id=store.seq_id("VIDEO_QUICK", int(time.time())), story_id=sid,
        production_type="video_quick", style_profile_name="电影级写实",
        model="minimax_h3", model_mode="t2va", prompt=prompt,
        parameters={"prompt_source": prompt_source},
        output_path=res["output_path"])
    return _RESP({"ok": True, "video": res, "duration": dur, "manifest": m})

@post("/api/create/preview")
def r_018(h, path, d):
    one = str(d.get("one_line") or "")
    if not one:
        return _RESP({"ok": False, "error": "缺少一句话"}, 400)
    return _RESP({"ok": True, "data": {
        "message": "Phase 0–8 不发起 AI 生成；Preview 将在素材轮启用后调用 Qwen 生成（thinking 策略见设置页）",
        "one_line": one}})

@post("/api/create/expand")
def r_019(h, path, d):
    """通用扩写：任何一块内容都能用同一个接口扩。

    原来这里是个空壳，返回一句"素材轮启用后进行"就完了，界面上那些扩写按钮
    点了等于没点。用户的规矩是「只要是功能的必须得生效」。

    守的是同一条底线：**只能补，不能改**。扩完用 premise_guard 量一遍和原文的
    重合度，低于阈值说明模型把原意改了，自动重来。
    """
    from cores import expand_core
    text = str(d.get("text") or "").strip()
    if not text:
        return _RESP({"ok": False, "error": "没有可扩写的内容"}, 400)
    try:
        out, note = expand_core.expand(text, str(d.get("purpose") or "内容"))
    except Exception as e:
        return _RESP({"ok": False, "error": str(e)[:200]}, 500)
    return _RESP({"ok": True, "data": {"original": text, "expanded": out, "note": note}})

@post("/api/create/confirm")
def r_020(h, path, d):
    sid = str(d.get("story_id") or "")
    edits = d.get("edits") or {}
    story = story_core.get_story(sid)
    if not story:
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    story.setdefault("create_prefs", {})
    for k in ("speed", "style", "scale"):
        if k in edits:
            story["create_prefs"][k] = edits[k]
    story["production_stage"] = story.get("production_stage") or "draft"
    story_core.save_story(story)
    return _RESP({"ok": True, "data": {"story": story}})

@post(("/api/story/", "/preview"))
def r_029(h, path, d):
    sid = path.split("/")[3]
    story = story_core.get_story(sid)
    if not story:
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    summary = story_core.generate_preview(story)
    return _RESP({"ok": True, "preview": summary})

@post(("/api/story/", "/previs"))
def r_030(h, path, d):
    sid = path.split("/")[3]
    from production import director_core
    story = story_core.get_story(sid)
    if not story:
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    from cores import story_layer
    no = int(d.get("episode_no") or story.get("current_episode") or 1)
    ep = narrative_core.load_episode(sid, no)

    # 优先用 📖 故事栏 的完整故事与分集（唯一故事事实源）；
    # 老的 narrative episode 只作兼容回退，没有也能跑。
    if ep and ep.get("units"):
        premise = "；".join(u.get("purpose", "") + " → " + u.get("result", "")
                            for u in ep["units"][:4])
    else:
        body = story_layer.get_story_body(sid)
        eps = story_layer.list_episodes(sid)
        this = next((e for e in eps if int(e.get("no") or 0) == no), None)
        # 分集梗概只是列表标签（正文首句），拿它当导演输入会让 AI 自己编剧情。
        # 导演必须看到完整故事，梗概只用来指出这一集的重点。
        focus = (this or {}).get("summary") or ""
        premise = body.strip()
        if focus and focus not in premise:
            premise = focus + "\n" + premise
        if focus:
            premise += "\n【本集重点】" + focus
        premise = premise[:2000]
        if not premise.strip():
            return _RESP({"ok": False,
                          "error": "还没有故事。先在 ⚙️ 设定 一键生成，或在 📖 故事 写完整故事"}, 400)
        ep = {"episode_no": no, "units": []}

    # 人物不能写死：取这个项目自己的人物
    names = [c.get("name") for c in asset_core.list_assets(sid, "characters") if c.get("name")]
    plan, check = director_core.run_director_scene(
        premise, characters="、".join(names) if names else "主角")
    story["previs"] = {"episode_no": ep["episode_no"], "director_intent": plan.get("director_intent"),
                       "shots": plan.get("shots"), "story_script": plan.get("story_script"),
                       "check": check}
    story_core.save_story(story)
    return _RESP({"ok": True, "previs": story["previs"]})

@post(("/api/story/", "/stage"))
def r_031(h, path, d):
    sid = path.split("/")[3]
    story = story_core.get_story(sid)
    if not story:
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    stage = str(d.get("stage") or "")
    if stage not in ("draft", "preview", "adopted", "production"):
        return _RESP({"ok": False, "error": "无效阶段"}, 400)
    story["production_stage"] = stage
    story_core.save_story(story)
    return _RESP({"ok": True, "story": story})

@post(("/api/story/", "/update"))
def r_032(h, path, d):
    sid = path.split("/")[3]
    story = story_core.get_story(sid)
    if not story:
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    for k in ("title", "one_line", "mode", "production_stage"):
        if k in d:
            story[k] = str(d[k])
    if "world_rules" in d:
        wr = d["world_rules"]
        story["world_rules"] = wr if isinstance(wr, list) else [str(wr)]
    story["updated"] = time.time()
    story_core.save_story(story)
    return _RESP({"ok": True, "story": story})

@post(("/api/story/", "/characters/", "/update"))
def r_033(h, path, d):
    parts = path.split("/")
    sid, cid = parts[3], parts[5]
    c = asset_core.get_asset(sid, "characters", cid)
    if not c:
        return _RESP({"ok": False, "error": "人物不存在"}, 404)
    old_face = str(c.get("face_type") or "")
    old_sex = str(c.get("sex") or "")
    old_hair_preset = str(c.get("hair_preset") or "自动")
    patch = asset_core.canonical_character_patch(d)
    asset_core.apply_fields(c, patch, asset_core.CHARACTER_FIELDS)
    # 骨相下拉改了以后，旧的九项脸部结构不能继续锁住，否则界面看似改了，
    # 下一次出图仍沿用旧脸。清空后由下一次“重新生成设定图”按新骨相重新定脸。
    if ("face_type" in patch and str(c.get("face_type") or "") != old_face) or \
       ("sex" in patch and str(c.get("sex") or "") != old_sex):
        c["face_spec"] = {}
    if "hair_preset" in patch and str(c.get("hair_preset") or "自动") != old_hair_preset:
        _sex = "男" if str(c.get("sex") or "") == "男" else "女"
        _hair = style_presets.hair_def(_sex, c.get("hair_preset"))
        if _hair:
            c["hair"] = _hair
    if any(k in patch for k in ("sheet_body_ratio", "build", "face_type", "hair_preset", "hair", "sex",
                                "outfit_preset", "clothing_requirement", "clothing", "appearance_details")):
        c["image_prompt"] = ""       # 卡已改，不能再把旧提示词当成当前提示词展示
    asset_core.refresh_character_anchors(c)
    c["updated"] = time.time()
    c["edited_by_user"] = True      # 用户改过的卡：自动流程重跑时不许清掉
    asset_core.save_asset(sid, "characters", c)
    return _RESP({"ok": True, "character": c})

@post(("/api/story/", "/scenes/", "/update"))
def r_034(h, path, d):
    parts = path.split("/")
    sid, scid = parts[3], parts[5]
    sc = asset_core.get_asset(sid, "scenes", scid)
    if not sc:
        return _RESP({"ok": False, "error": "场景不存在"}, 404)
    for k in ("name", "contract_text", "regions", "materials",
              "fixed_landmarks", "base_light_color",
              "space", "layout", "light", "landmarks", "depth", "intent", "states", "furniture"):
        if k in d:
            sc[k] = str(d[k])
    # 用户改了设计稿正文：重新解析，一句话空间/戏区/光/主体等字段跟着换（2026-09-06）
    if "design" in d:
        from cores import authoring as _au
        _au.apply_design_block(sid, sc, str(d["design"] or ""))
    sc["updated"] = time.time()
    # 【为什么必须打这个标记】自动流程重跑时会把"没被用户改过"的卡整张删掉。
    # 人物卡那边一直有这一行，场景卡这条路径漏了——用户手改过的场景，
    # 下一次「全部生成」会被直接清掉，改了等于没改。
    sc["edited_by_user"] = True
    asset_core.save_asset(sid, "scenes", sc)
    return _RESP({"ok": True, "scene": sc})


@post("/api/story/body")
def story_body(h, path, d):
    """故事栏：完整故事正文、分集、事件账本。action=split 出分集草稿。"""
    sid = str((d or {}).get("story_id") or "")
    try:
        if "body" in d:
            story_layer.save_story_body(sid, d.get("body"))
        if "episodes" in d:
            story_layer.save_episodes(sid, d.get("episodes"))
        if d.get("action") == "split":
            return _RESP({"ok": True, "data": {
                "episodes": story_layer.split_into_episodes(sid, d.get("count"))}})
        return _RESP({"ok": True, "data": {
            "body": story_layer.get_story_body(sid),
            "episodes": story_layer.list_episodes(sid),
            "events": story_layer.event_ledger(sid),
            "status": story_layer.story_status(sid)}})
    except ValueError as e:
        return _RESP({"ok": False, "error": str(e)}, 404)


# ---------- 一键到设定（两级刹车的第一级） ----------
_S1_JOBS = {}


@post(("/api/story/", "/auto-stage1"))
def r_stage1(h, path, d):
    """一句话 → 正文 + 全部设定卡 + 全部设定图，然后停下等审。后台跑。"""
    import threading
    sid = path.split("/")[3]
    one_line = str(d.get("one_line") or "").strip()
    if not one_line:
        st0 = story_core.get_story(sid) or {}
        one_line = str((st0.get("settings") or {}).get("one_line") or st0.get("one_line") or "")
    if not one_line:
        return _RESP({"ok": False, "error": "缺一句话设定"}, 400)
    j = _S1_JOBS.setdefault(sid, {})
    if j.get("running"):
        return _RESP({"ok": False, "error": "已经在跑了"}, 400)
    j.update(running=True, step="排队", err="", done=None)

    def _run():
        from cores import stage1_core
        try:
            r = stage1_core.run_stage1(sid, one_line,
                                       on_step=lambda m: j.update(step=m))
            j["done"] = r
        except Exception as e:
            j["err"] = str(e)[:300]
        finally:
            j["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return _RESP({"ok": True, "data": {"started": True}})


@post(("/api/story/", "/auto-stage1-progress"))
def r_stage1_prog(h, path, d):
    sid = path.split("/")[3]
    return _RESP({"ok": True, "data": _S1_JOBS.get(sid) or {}})
