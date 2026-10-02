# -*- coding: utf-8 -*-
"""settings_api 域路由（由 server.py 的 do_POST 拆分而来）。"""
import re
from api import post, get
from api._shared import _RESP, ctx
globals().update(ctx())


@post("/api/content_profile")
def r_017(h, path, d):
    return _RESP({"ok": True, "profile": profile_core.save_profile(d.get("profile") or {})})

@post("/api/project/settings")
def r_026(h, path, d):
    from cores import project_settings, style_presets, character_setup, project_prompt
    sid = str(d.get("story_id") or "")
    story = story_core.get_story(sid)
    if not story:
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    if "settings" in d:
        # 项目名：前端设定页那个输入框传上来就存到 story 顶层 title。
        # 原来没有改名入口，新建的项目名一律是"一句话前16字"或"未命名故事"。
        _title = str(d.get("title") or "").strip()
        if _title:
            story["title"] = _title
            story_core.save_story(story)
        saved = project_settings.save(story, d.get("settings"))
        log("info", "项目设定已保存：" + sid)
        # 只提示不拦截：保存照常成功，冲突信息随响应带回去让页面说一声。
        return _RESP({"ok": True, "data": {
            "settings": saved,
            "conflicts": project_prompt.settings_conflicts(saved)}})
    from cores import auto_repair
    raw_scenes = asset_core.list_assets(sid, "scenes")
    scene_names = [x.get("name") for x in raw_scenes]
    repaired_scenes = []
    for s in raw_scenes:
        rs, notes = auto_repair.repair_scene(s, scene_names)
        rs["repair_notes"] = notes
        repaired_scenes.append(rs)
    ps = project_settings.get(story)
    # 【本话安排随设定一起回】get() 只回白名单字段，安排不在里面 → 页面首屏画不出已有的卡，
    # 只能事后再取一次，卡一插进来下面的按钮就往下跳，用户刚点的「生成故事」落空（浏览器实测两次）。
    _arr = (story.get("settings") or {}).get("arrangement")
    if isinstance(_arr, dict) and _arr:
        ps = dict(ps, arrangement=_arr)
    # P298：整部规划也一起回（首屏就要知道有没有规划——一句话框由想法接管、生成按钮换说法）
    _wp = (story.get("settings") or {}).get("story_plan")
    if isinstance(_wp, dict) and _wp.get("episodes"):
        ps = dict(ps, story_plan={"idea": _wp.get("idea") or "", "status": _wp.get("status"), "version": _wp.get("version"),
                                  "prefs": dict(_wp.get("prefs") or {}),
                                  "episodes": [{"no": e.get("no"), "uid": e.get("uid")} for e in _wp["episodes"]]})
    # 人物卡界面不再显示“自动”。盘上仍可保留自动值，但返回页面前把这次实际会使用的
    # 具体预设算出来；用户只改一项并保存后，这些已显示值会一起冻结，不会重新随机选择。
    _characters = []
    for _raw in asset_core.list_assets(sid, "characters"):
        _c = dict(_raw)
        _resolved = style_presets.resolve_auto_character(_c, ps)
        for _field in ("sex", "face_type", "build", "sheet_body_ratio", "hair_preset",
                       "behavior_anchor", "clothing_requirement", "char_type", "outfit_preset"):
            _value = str(_c.get(_field) or "").strip()
            if _value in ("", "自动", "None", "自动判断"):
                _source = "pose" if _field == "behavior_anchor" else _field
                if _resolved.get(_source):
                    _c[_field] = _resolved[_source]
        _characters.append(_c)
    return _RESP({"ok": True, "data": {
        "title": story.get("title") or "",
        "settings": ps,
        # 四界四风；老项目的旧值不在名单里时追加一项，免得下拉把它顶成第一项、一保存就改了画风
        "world_types": list(style_presets.WORLD_TYPES) + (
            [ps["world_type"]] if str(ps.get("world_type") or "") not in style_presets.WORLD_TYPES + ["", "自动判断", "自动"] else []),
        "visual_strengths": style_presets.VISUAL_STRENGTHS,
        "styles": style_presets.load_styles() + (
            [{"name": ps["style"], "content": (style_presets.style_description(ps["style"]) or "") + "（旧画风，保留这个项目原来的观感）"}]
            if str(ps.get("style") or "") and str(ps.get("style")) not in style_presets.STYLE_NAMES else []),
        "content_tendencies": style_presets.CONTENT_TENDENCIES,
        "custom_content_scale_hint": style_presets.CUSTOM_CONTENT_SCALE_HINT,
        # 每个尺度选项对三层的实际影响，界面点「详细」直接展示——
        # 这是模型真正收到的原话，不是另写一套说明
        # （用户 2026-09-01：把选项和内容都放进设置页，说清对什么有影响）。
        # 文案存在 presets/content_scale.json，用户可以自己改，改完不用重启。
        "content_scale_effects": style_presets._scale_table(),
        # 题材引擎：给出全部可选项和每个引擎的一句话说明，页面上能看见也能改
        "genre_engines": ["自动判定"] + list(__import__(
            "cores.story_gen", fromlist=["x"]).ENGINE_NAMES),
        "genre_engine_defs": __import__("cores.story_gen", fromlist=["x"]).ENGINE_HINTS,
        "hero_styles": style_presets.hero_style_options(),
        "hero_style_defs": style_presets.HERO_STYLE_DIRECTIONS,
        "custom_guidance_hint": style_presets.CUSTOM_GUIDANCE_HINT,
        # 选项旁的「详细」直接展示模型真正收到的底层定义，避免界面说明
        # 和提示词编译规则各写一套、改着改着再次不一致。
        "option_defs": {
            "world": style_presets.WORLD_DEFS,
            "visual_strength": {
                name: style_presets.intensity_def(name)
                for name in style_presets.VISUAL_STRENGTHS
            },
            "style": {item["name"]: item.get("content", "")
                      for item in style_presets.load_styles()},
            "content_tendency": {
                name: "；".join(v for v in (
                    (style_presets.CONTENT_TENDENCY_EFFECT.get(name) or {}).get("story", ""),
                    (style_presets.CONTENT_TENDENCY_EFFECT.get(name) or {}).get("figure", ""),
                    (style_presets.CONTENT_TENDENCY_EFFECT.get(name) or {}).get("shot", ""),
                ) if v)
                for name in style_presets.CONTENT_TENDENCIES
            },
            "character_type": style_presets.CHAR_TYPE_DEFS,
            "face": style_presets.FACE_DEFS,
            "sheet_ratio": style_presets.BODY_RATIO_DEFS,
            "body": style_presets.BODY_DEFS,
            "hair": style_presets.HAIR_DEFS,
            "pose": style_presets.POSE_DEFS,
            "cloth": style_presets.CLOTH_DEFS,
        },
        "char_fields": style_presets.char_fields_for("女", story.get("settings") or {}),
        # 长相/身材分男女两套，界面按人物性别切换；服装款式按这个项目的世界（P352）
        "char_fields_by_sex": {"女": style_presets.char_fields_for("女", story.get("settings") or {}),
                               "男": style_presets.char_fields_for("男", story.get("settings") or {})},
        "outfit_presets_by_world": {
            world: project_prompt.outfit_preset_options(world)
            for world in style_presets.WORLD_TYPES if world not in ("自动判断", "自定义")},
        "outfit_preset_defs_by_world": {
            world: project_prompt.outfit_preset_defs(world)
            for world in style_presets.WORLD_TYPES if world not in ("自动判断", "自定义")},
        "outfit_presets": project_prompt.outfit_preset_options(project_settings.get(story)),
        "outfit_preset_defs": project_prompt.outfit_preset_defs(project_settings.get(story)),
        "behavior_promise": style_presets.BEHAVIOR_PROMISE,
        "setting_presets": style_presets.load_setting_presets(),
        "gated": not character_setup.GEN_ENABLED,
        # 冻结线：页面靠这两项决定显示「确定设定」还是「解冻」
        "lock": project_settings.lock_state(story),
        "lock_impact": project_settings.affected_episodes(
            story, __import__("cores.saga_core", fromlist=["x"]).get_saga(sid)),
        "characters": _characters,
        "scenes": repaired_scenes,
        "visuals": asset_core.list_assets(sid, "visuals"),
    }})

@post("/api/project/settings/generate")
def r_027(h, path, d):
    from cores import character_setup
    res = character_setup.generate_all_settings(str(d.get("story_id") or ""))
    return _RESP({"ok": True, "data": res})

@post("/api/one-shot/generate")
def r_028(h, path, d):
    from cores import one_shot_setup, project_settings as pss
    sid = str(d.get("story_id") or "")
    story = story_core.get_story(sid)
    if not story:
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    ps = pss.get(story)
    premise = str(d.get("one_line") or "") or ps.get("one_line") or story.get("one_line") or ""
    # 生成未启用 → call_model=None → 返回门控；启用后传入 qwen_client.chat 即可
    from models import qwen_client

    def _call(messages):
        # qwen_client.chat 的签名是 (system, user, ...)，不是消息列表
        sys_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
        usr_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
        return qwen_client.chat(sys_msg, usr_msg, temperature=0.7, max_tokens=1600)

    res = one_shot_setup.generate_all(premise, call_model=_call, settings=ps)

    # 设定 → 故事：一键生成出来的正文同时写进故事栏，这是唯一故事事实源。
    # 用户手改过正文就不覆盖（story_layer 内部有保护）。
    draft = res.get("draft") or {}
    if draft.get("story"):
        r2 = story_layer.adopt_generated_body(sid, draft["story"])
        res["story_synced"] = r2["ok"]
        if not r2["ok"]:
            res["note"] = (res.get("note") or "") + "；" + r2["note"]
    return _RESP({"ok": True, "data": res})

@post("/api/project/archive")
def r_037(h, path, d):
    sid = d.get("story_id") or (d.get("story") or {}).get("id")
    z = runtime_core.archive_project(sid)
    return _RESP({"ok": True, "file": z})

@post("/api/project/settings/lock")
def r_settings_lock(h, path, d):
    """确定设定 / 解冻。用户定的流程：冻结前不写正文，冻结后不再回头猜设定。"""
    from cores import project_settings, saga_core
    sid = str(d.get("story_id") or "")
    story = story_core.get_story(sid)
    if not story:
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    want = bool(d.get("locked", True))
    if want:
        st = project_settings.lock(story)
        log("info", "设定已冻结：" + sid)
        return _RESP({"ok": True, "data": st})
    saga = saga_core.get_saga(sid)
    res = project_settings.unlock(story, saga)
    log("info", "设定已解冻：" + sid)
    return _RESP({"ok": True, "data": res})


@post("/api/project/settings/impact")
def r_settings_impact(h, path, d):
    """只查不改：冻结之后改了哪些设定、哪几话可能受影响。"""
    from cores import project_settings, saga_core
    sid = str(d.get("story_id") or "")
    story = story_core.get_story(sid)
    if not story:
        return _RESP({"ok": False, "error": "故事不存在"}, 404)
    return _RESP({"ok": True, "data": {
        **project_settings.lock_state(story),
        "impact": project_settings.affected_episodes(story, saga_core.get_saga(sid))}})

# ── 五维设定与套装（2026-08-29）────────────────────────────────
@post("/api/kits/options")
def r_kit_options(h, path, d):
    """界面要的全部可选值 + 套装清单。"""
    from cores import kits
    return _RESP({"ok": True, "data": {
        "dims": {k: kits.options(k) for k in kits.DIMS},
        "labels": {"world": "世界类型", "genre": "影片类型", "art": "画风",
                   "look": "角色审美", "pov": "视点"},
        "kits": [{"name": k.get("name"), "desc": k.get("desc", ""),
                  "aka": k.get("aka", []), "dims": k.get("dims", {}),
                  "user": bool(k.get("user"))} for k in kits.kits()],
        "scale_options": ["黑暗（剧情绝望，总往坏的方向走）",
                          "血腥（断肢/内脏/大出血，可到最重）",
                          "成人向·情爱（诱惑/暧昧/亲密关系）",
                          "极致诱惑（卖肉向·不露点之外全露）"]}})


@post("/api/kits/detect")
def r_kit_detect(h, path, d):
    """一句话 → 五维配置。不写库，只返回结果给界面显示。"""
    from cores import kits
    one = str(d.get("one_line") or "").strip()
    if not one:
        return _RESP({"ok": False, "error": "先写一句话"}, 400)
    try:
        return _RESP({"ok": True, "data": kits.detect(one)})
    except Exception as e:
        return _RESP({"ok": False, "error": str(e)[:200]}, 500)


@post("/api/kits/save")
def r_kit_save(h, path, d):
    """把当前这组五维存成我的套装。"""
    from cores import kits
    name = str(d.get("name") or "").strip()
    dims = d.get("dims") or {}
    if not name:
        return _RESP({"ok": False, "error": "套装要有名字"}, 400)
    if not any(dims.get(k) for k in kits.DIMS):
        return _RESP({"ok": False, "error": "没有可保存的设定"}, 400)
    aka = [x.strip() for x in str(d.get("aka") or "").replace("，", ",").split(",") if x.strip()]
    return _RESP({"ok": True, "data": kits.save_user_kit(
        name, dims, aka=aka, desc=str(d.get("desc") or "").strip())})


@post("/api/presets/get")
def presets_get(h, path, d):
    """读全部预设表（世界类型/画风/人设卡字段/内容尺度…）给设置页显示。

    用户 2026-09-01：所有选项和内容都放进设置页，能自己改。
    返回的就是 presets/预设表.json 的原文，改完存回去下次生成就生效。
    """
    from cores import style_presets as sp
    d2 = sp.load_presets()
    # 画风正文和尺度分层规则不住在预设表文件里，读的时候拼进来当两张表
    _styles, _scale, _errs = _read_virtual()
    if _styles:
        d2[_T_STYLE] = _styles
    if _scale:
        d2[_T_SCALE] = _scale
    _kits, _e2 = _read_kits()
    _errs = list(_errs) + list(_e2)
    d2.update(_kits)
    note = d2.setdefault("_每一项影响什么", {})
    note[_T_STYLE] = "这段话会原样进出图提示词，决定整部作品的画风"
    note[_T_SCALE] = "故事和画面规则照常生效；成人尺度的人设图只按性别替换姿势/服装"
    note.update(_KIT_NOTE)
    note.update(_MERGE_NOTE)
    _merge_tables(d2)
    return _RESP({"ok": True, "data": {
        "presets": d2,
        "warn": "；".join(_errs),
        "file": sp.preset_file(),
        "layers": {
            "story": "决定故事怎么写（进正文的系统指令）",
            "figure": "决定人物长什么样（进人设图提示词）",
            "shot": "决定画面怎么拍（进视频提示词）",
        },
    }})


@post("/api/presets/save")
def presets_save(h, path, d):
    """存回预设表。存之前先验一遍：类型不对就不存，免得把系统写坏。"""
    import json as _json
    import io as _io
    from cores import style_presets as sp
    body = d.get("presets")
    if not isinstance(body, dict) or not body:
        return _RESP({"ok": False, "error": "内容不对，没存"}, 400)
    # 这两张表的家不在预设表文件里，先拆出去单独存
    body = dict(body)
    _split_tables(body)      # 合并显示的表先拆回「选项列表 + 定义」两张
    # 读的时候临时加的说明（虚拟表/合并表用的）不写回文件，免得越存越脏
    _note = body.get("_每一项影响什么")
    if isinstance(_note, dict):
        _note = dict(_note)
        for _k in list(_KIT_NOTE) + list(_MERGE_NOTE) + [_T_STYLE, _T_SCALE]:
            _note.pop(_k, None)
        body["_每一项影响什么"] = _note
    _st = body.pop(_T_STYLE, None)
    _sc = body.pop(_T_SCALE, None)
    if _st is not None and not isinstance(_st, dict):
        return _RESP({"ok": False, "error": "「%s」格式不对，没存" % _T_STYLE}, 400)
    if _sc is not None and not isinstance(_sc, dict):
        return _RESP({"ok": False, "error": "「%s」格式不对，没存" % _T_SCALE}, 400)
    _kt = {}
    for _title in list(_KIT_TABLES):
        if _title in body:
            _v = body.pop(_title)
            if not isinstance(_v, dict):
                return _RESP({"ok": False, "error": "「%s」格式不对，没存" % _title}, 400)
            _kt[_title] = _v
    try:
        _write_virtual(_st, _sc)
        _write_kits(_kt)
    except Exception as ex:
        return _RESP({"ok": False, "error": "画风/尺度/套装没存上：" + str(ex)[:160]}, 500)
    # 【存之前验一遍】列表还是列表、字典还是字典——类型错了下游会炸
    cur = sp.load_presets()
    for k, v in body.items():
        if k.startswith("_"):
            continue
        old = cur.get(k)
        if old is not None and type(old) is not type(v):
            return _RESP({"ok": False,
                          "error": "「%s」的格式变了（原来是%s，现在是%s），没存"
                                   % (k, type(old).__name__, type(v).__name__)},
                         400)
    p = sp.preset_file()
    try:
        # 存之前留一份，改坏了能找回来
        try:
            _io.open(p + ".bak", "w", encoding="utf-8").write(
                _io.open(p, encoding="utf-8").read())
        except Exception:
            pass
        _io.open(p, "w", encoding="utf-8").write(
            _json.dumps(body, ensure_ascii=False, indent=2))
        sp._apply_presets()
        return _RESP({"ok": True, "data": {"saved": True, "file": p}})
    except Exception as ex:
        return _RESP({"ok": False, "error": str(ex)[:200]}, 500)


# ──────── 画风 / 内容尺度：内容不在预设表文件里，读的时候拼进来 ────────
# 用户 2026-09-01：光给选项名没用，要能看到每个选项背后的内容。
# 这两张是「虚拟表」——界面上和别的表长得一样，存盘时拆回各自原来的位置：
#   画风 → presets/render_notes.json 的 char（人物出图实际读取）
#           同时同步 presets/comic_styles/<名字>.txt，兼容故事规划等旧入口
#   内容尺度 → presets/content_scale.json（故事/人设图/画面，成人尺度另有男女姿势和服装）
_T_STYLE = "画风·每种的定义"
_T_SCALE = "内容尺度·每种的影响（分层）"
# 内部名 ↔ 界面上的说法。成人尺度的人设图规则拆成男女姿势与服装，
# 仍在原来的内容尺度预设里编辑，不增加日常操作入口。
_LAYER_CN = {
    "story": "决定故事怎么写",
    "figure": "普通人设外观补充",
    "figure_pose_male": "人设图·男性姿势替换",
    "figure_pose_female": "人设图·女性姿势替换",
    "figure_clothing_male": "人设图·男性服装替换",
    "figure_clothing_female": "人设图·女性服装替换",
    "shot": "决定画面怎么拍",
}
_LAYER_EN = {v: k for k, v in _LAYER_CN.items()}


def _styles_root():
    import os
    from cores import style_presets as sp
    return str(sp.STYLE_DIR)


def _scale_path():
    import os
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "presets", "content_scale.json")


def _render_notes_path():
    import os
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "presets", "render_notes.json")


def _read_virtual():
    """把画风正文和尺度三层读成两张表。

    出错不再静默吞掉——吞了的话界面上只表现为「这张表凭空没了」，
    没人知道是文件坏了还是代码错了（实测已经栽过一次：io 忘了导入）。
    """
    import os, json, io
    styles, scale, errs = {}, {}, []
    root = _styles_root()
    try:
        try:
            notes = json.loads(io.open(_render_notes_path(), encoding="utf-8").read())
        except Exception as ex:
            notes = {}
            errs.append("人物画风定义读不了：" + str(ex)[:120])
        for fn in sorted(os.listdir(root)):
            if fn.endswith(".txt"):
                name = fn[:-4]
                legacy = io.open(os.path.join(root, fn), encoding="utf-8").read().strip()
                actual = str((notes.get(name) or {}).get("char") or "").strip()
                styles[name] = actual or legacy
    except Exception as ex:
        errs.append("画风目录读不了：" + str(ex)[:120])
    try:
        raw = json.loads(io.open(_scale_path(), encoding="utf-8").read())
        for name, layers in (raw.get("选项") or {}).items():
            scale[name] = {_LAYER_CN.get(k, k): v for k, v in (layers or {}).items()}
    except Exception as ex:
        errs.append("内容尺度文件读不了：" + str(ex)[:120])
    return styles, scale, errs


def _write_virtual(styles, scale):
    """拆回各自的家；人物画风写进生成链实际读取的 render_notes.json。"""
    import os, json, io
    if styles is not None:
        root = _styles_root()
        notes_path = _render_notes_path()
        try:
            notes = json.loads(io.open(notes_path, encoding="utf-8").read())
        except Exception:
            notes = {}
        keep = set()
        for name, text in styles.items():
            name = str(name).strip()
            # 名字要当文件名用，带路径分隔符的一律不收
            if not name or "/" in name or "\\" in name or name in (".", ".."):
                continue
            keep.add(name + ".txt")
            entry = notes.get(name)
            if not isinstance(entry, dict):
                entry = {}
                notes[name] = entry
            entry["char"] = str(text).strip()
            io.open(os.path.join(root, name + ".txt"), "w",
                    encoding="utf-8").write(str(text).strip() + "\n")
        for fn in os.listdir(root):
            if fn.endswith(".txt") and fn not in keep:
                try:
                    # 删之前挪进 _bak，手滑了还能找回来
                    bak = os.path.join(root, "_bak")
                    os.path.isdir(bak) or os.makedirs(bak)
                    os.replace(os.path.join(root, fn), os.path.join(bak, fn))
                except Exception:
                    pass
        io.open(notes_path, "w", encoding="utf-8").write(
            json.dumps(notes, ensure_ascii=False, indent=2) + "\n")
    if scale is not None:
        p = _scale_path()
        try:
            raw = json.loads(io.open(p, encoding="utf-8").read())
        except Exception:
            raw = {}
        raw["选项"] = {name: {_LAYER_EN.get(k, k): v for k, v in (layers or {}).items()}
                       for name, layers in scale.items()}
        io.open(p, "w", encoding="utf-8").write(
            json.dumps(raw, ensure_ascii=False, indent=2))


# ──────── 套装四维：影片类型 / 角色审美 / 视点 / 世界补充 ────────
# 这四个下拉界面上天天在用，内容却住在 presets/style_kits.json 里，
# 一直没接进设置页（用户 2026-09-01：所有都要内容）。
# 存回去也是写那个文件，不搬家。
_KIT_TABLES = {
    "影片类型·每种怎么拍": "genre",
    "角色审美·每种长什么样": "look",
    "视点·每种的机位规则": "pov",
    "世界补充设定·每种的设定": "world_extra",
}
_KIT_NOTE = {
    "影片类型·每种怎么拍": "决定叙事结构和节奏，进写故事的系统指令",
    "角色审美·每种长什么样": "决定人物的脸和身材，进人设图提示词",
    "视点·每种的机位规则": "决定摄影机的身份和运镜，进分镜和视频提示词",
    "世界补充设定·每种的设定": "冷门世界的补充设定（克系/武侠/蒸汽朋克）",
}
# 字段的内部名 ↔ 界面上的说法。代码认英文键，用户看中文。
_KIT_FIELD_CN = {
    "persona": "让模型扮演谁", "structure": "结构怎么排", "pace": "节奏",
    "shot_bias": "镜头偏好", "seg_seconds": "每段多少秒", "scale": "尺度",
    "face": "脸", "body": "身材", "exaggeration": "夸张到什么程度",
    "build_hint": "体格补充",
    "camera": "摄影机是什么", "anchors": "锚点规则", "look_at_lens": "能不能看镜头",
    "movement": "运镜", "must": "必须做到", "transition": "转场",
    "era": "时代", "rules": "世界规则", "material": "材质与场景", "ban": "禁止出现",
}
_KIT_FIELD_EN = {v: k for k, v in _KIT_FIELD_CN.items()}


def _kit_path():
    from cores import kits
    return str(kits.KIT_FILE)


def _read_kits():
    """套装四维读成四张表：选项名 → {中文字段名: 内容}。"""
    import json, io
    out, errs = {}, []
    try:
        raw = json.loads(io.open(_kit_path(), encoding="utf-8").read())
        dims = raw.get("dimensions") or {}
        for title, dim in _KIT_TABLES.items():
            opts = (dims.get(dim) or {}).get("options") or {}
            if not opts:
                continue
            out[title] = {
                name: {_KIT_FIELD_CN.get(k, k): _kit_show(v)
                       for k, v in (fields or {}).items()}
                for name, fields in opts.items()}
    except Exception as ex:
        errs.append("套装文件读不了：" + str(ex)[:120])
    return out, errs


def _write_kits(tables):
    """存回 style_kits.json。只动被改的那几维，别的原样留着。"""
    import json, io, os
    if not tables:
        return
    p = _kit_path()
    raw = json.loads(io.open(p, encoding="utf-8").read())
    dims = raw.setdefault("dimensions", {})
    for title, data in tables.items():
        dim = _KIT_TABLES[title]
        node = dims.setdefault(dim, {})
        was = node.get("options") or {}     # 原来长什么类型，存回去还是什么类型
        node["options"] = {
            name: {_KIT_FIELD_EN.get(k, k): _kit_keep(
                       (was.get(name) or {}).get(_KIT_FIELD_EN.get(k, k)), v)
                   for k, v in (fields or {}).items()}
            for name, fields in (data or {}).items()}
    # 存之前留一份，改坏了能找回来
    try:
        io.open(p + ".bak", "w", encoding="utf-8").write(
            io.open(p, encoding="utf-8").read())
    except Exception:
        pass
    io.open(p, "w", encoding="utf-8").write(
        json.dumps(raw, ensure_ascii=False, indent=2))
    # 套装模块有缓存，按 mtime 判断，写完它下次自己会重读


def _kit_show(v):
    """给界面看：列表摊成多行文本，一行一条。别的原样。"""
    if isinstance(v, list):
        return "\n".join(str(x) for x in v)
    return v


def _kit_keep(old, new):
    """存回去：原来是列表的，把多行文本切回列表；不然类型被改坏。"""
    if isinstance(old, list):
        return [x.strip() for x in str(new or "").split("\n") if x.strip()]
    return new


# ──────── 选项列表 + 定义 → 合成一张表 ────────
# 用户 2026-09-01：点开要能直接看到内容。
# 原来一个下拉拆成两张表（一张只有名字、一张只有内容），点错就啥也看不到。
# 现在读的时候合成一张，存的时候拆回去——底层文件还是原来的结构。
_MERGE = {
    # 合并后的表名: (选项列表的表名, 定义表的表名)
    "世界类型（选项和内容）": ("世界类型·选项列表", "世界类型·每种世界的定义"),
    "画风（选项和内容）": ("画风·选项列表", _T_STYLE),
    "视觉设计强度（选项和内容）": ("视觉设计强度·选项", "视觉设计强度·每档的定义"),
    "内容尺度（选项和内容）": ("内容尺度·选项列表", _T_SCALE),
}
_MERGE_NOTE = {
    "世界类型（选项和内容）": "决定时代、能不能有超自然、穿什么用什么",
    "画风（选项和内容）": "这段话原样进出图提示词，决定整部作品长什么样",
    "视觉设计强度（选项和内容）": "决定头身比和夸张程度，从写实到动漫角色",
    "内容尺度（选项和内容）": "普通尺度照常叠加；成人尺度只按性别替换人设图的姿势和服装",
}


def _bare(name):
    """「美型（默认）」→「美型」。选项名带括号注解，定义表里存的是光名字。"""
    i = str(name).find("（")
    return str(name)[:i].strip() if i > 0 else str(name)


def _merge_tables(d2):
    """把成对的两张表合成一张，原来那两张从界面上撤掉。"""
    for title, (lk, dk) in _MERGE.items():
        opts, defs = d2.get(lk), d2.get(dk)
        if not isinstance(opts, list) or not isinstance(defs, dict):
            continue
        out = {}
        for name in opts:
            v = defs.get(name)
            if v is None:
                v = defs.get(_bare(name))
            # 「自动」这种没有定义的，留空——界面上会写「留空＝这一项不设置」
            out[name] = v if v is not None else ""
        # 定义表里有、选项列表里没有的键（「美型」是「美型（默认）」的定义键，
        # 「极端」是没启用的一档）不往这儿放——放了会被当成选项存回下拉里，
        # 实测下拉从 4 项变 6 项。它们在文件里原样留着，不显示也不动。
        d2.pop(lk, None)
        d2.pop(dk, None)
        d2[title] = out
    return d2


def _split_tables(body):
    """存的时候拆回两处：名字回选项列表，内容回定义表。

    定义键沿用文件里原来的写法（「美型（默认）」的内容还是存进「美型」），
    不然改一次名字就多出一条重复定义。
    """
    from cores import style_presets as sp
    cur = sp.load_presets()
    _st_cur, _sc_cur, _ = _read_virtual()
    old_defs = {_T_STYLE: _st_cur, _T_SCALE: _sc_cur}
    for title, (lk, dk) in _MERGE.items():
        if title not in body:
            continue
        merged = body.pop(title)
        if not isinstance(merged, dict):
            continue
        old = old_defs.get(dk) or cur.get(dk) or {}
        old_list = cur.get(lk) or []
        # 在原表上增改，不整份重建——不然没露面的键（例如「极端」）会被写丢
        defs = dict(old)
        names = []
        for name, val in merged.items():
            name = str(name).strip()
            if not name:
                continue
            names.append(name)
            # 内容空着的（「自动」这类）不往定义表里写，保持原样
            if val == "" or val is None:
                continue
            key = name if name in old else (_bare(name) if _bare(name) in old else name)
            defs[key] = val
        # 用户在界面上删掉的那几项，定义也跟着删
        for gone in [n for n in old_list if n not in merged]:
            defs.pop(gone, None)
            defs.pop(_bare(gone), None)
        if names:
            body[lk] = names
        if defs:
            body[dk] = defs
    return body


@post("/api/structure/preview")
def r_structure_preview(h, path, d):
    """设定页「📐 故事结构」：每话的事件清单 / 节奏档 / 几何提示，外加代码判出的缺口问题。
    事件抽取要调模型（每行一次，约 5 秒）；模型不在就用离线粗拆兜底。"""
    from cores import story_plan as _sp, pace as _pc, health as _hl
    rows = [r for r in (d.get("rows") or []) if isinstance(r, dict) and str(r.get("one_line") or "").strip()]
    if not rows and str(d.get("one_line") or "").strip():
        rows = [{"one_line": str(d.get("one_line"))}]
    if not rows:
        return _RESP({"ok": False, "error": "先写一句话故事"}, 400)
    online = _hl.llama_up()
    from cores import universal_writer as _uw
    if _uw.enabled():
        if not online:
            return _RESP({"ok": True, "data": {"rows": _uw.normalize_rows(rows), "online": False,
                          "questions": ["文字模型未启动，已保留现有分话，没有改写事件"]}})
        sid = str(d.get("story_id") or "")
        settings = ((story_core.get_story(sid) or {}).get("settings") or {}) if sid else {}
        one = str(settings.get("one_line") or d.get("one_line") or "；".join(r["one_line"] for r in rows))
        planned = _sp.plan_framework(one, settings, n_eps=len(rows), notes="本次用户修改的分话安排：" + _uw.dump(rows))
        return _RESP({"ok": True, "data": {"rows": planned["rows"], "design": planned.get("design"),
                      "questions": planned["problems"], "online": True, "calls": planned["calls"]}})
    out = []
    for i, r in enumerate(rows, 1):
        one = str(r.get("one_line") or "")
        evs = (_sp.extract_events(one) if online else []) or _sp.extract_events_offline(one)
        if _pc.no_strike(one):
            evs = _sp.no_strike_events(evs)                       # 和生成链同一改写（P81）
        pace = str(r.get("pace") or "") if str(r.get("pace") or "") in _pc.PACES else _pc.detect(one)
        geo = "追逐" if re.search(r"追|逃|载具|摩托|马车", one) else (
            "对峙" if (pace == "高潮" or _pc.no_strike(one) or re.search(r"对峙|面对|拦|不能动手|跪下|质问", one)) else (
                "行进" if re.search(r"赶路|旅行|沿路|骑|走", one) else "对谈"))
        out.append({"no": i, "one_line": one, "events": evs, "pace": pace,
                    "no_strike": _pc.no_strike(one), "geometry": geo,
                    "rules": {k: v for k, v in _pc.rules(pace).items() if k in ("dialogue_min", "dialogue_max", "need_foe", "seg_seconds")}})
    return _RESP({"ok": True, "data": {"rows": out, "questions": _sp.structure_gaps(rows), "online": online}})


@post("/api/structure/save")
def r_structure_save(h, path, d):
    """确认结构：存 settings.plan_rows；第一话的一句话同步成第一行。"""
    sid = str(d.get("story_id") or "")
    rows = [r for r in (d.get("rows") or []) if isinstance(r, dict) and str(r.get("one_line") or "").strip()]
    if not sid or not rows:
        return _RESP({"ok": False, "error": "缺 story_id 或计划表为空"}, 400)
    st = story_core.get_story(sid)
    if not st:
        return _RESP({"ok": False, "error": "项目不存在"}, 404)
    settings = st.get("settings") or {}
    from cores import universal_writer as _uw
    _uw.save_design(settings, rows, d.get("design") or settings.get("story_design"))
    settings["plan_problems"] = []
    # 一句话（前提）保持原样，不再被第 1 行覆盖（P120）；第 N 话生成走 apply_plan_rows 取第 N 行
    settings["plan_version"] = int(settings.get("plan_version") or 0) + 1      # 结构版本（P111）：前情只接同版本的话
    st["settings"] = settings
    story_core.save_story(st)
    log("info", "故事结构已确认：%d 话（版本 %d）" % (len(rows), settings["plan_version"]))
    return _RESP({"ok": True, "data": {"plan_rows": settings["plan_rows"]}})


@post("/api/structure/framework")
def r_structure_framework(h, path, d):
    """一句话 → N 话分话框架（P96）：1 次铺 + ≤1 次修，代码核对后连问题一起返回。"""
    from cores import story_plan as _sp, health as _hl
    one = str(d.get("one_line") or "").strip()
    sid = str(d.get("story_id") or "")
    if not one and sid:
        one = str(((story_core.get_story(sid) or {}).get("settings") or {}).get("one_line") or "")
    if not one:
        return _RESP({"ok": False, "error": "先写一句话故事"}, 400)
    if not _hl.llama_up():
        return _RESP({"ok": False, "error": "文字模型没起来，推框架要它"}, 503)
    settings = ((story_core.get_story(sid) or {}).get("settings") or {}) if sid else {}
    _log = []
    r = _sp.plan_framework(one, settings, n_eps=int(d.get("n") or 0), notes=str(d.get("notes") or ""), log=_log)     # n=0 ＝按剧情点自动（P114）
    log("info", "框架：%d 话，%d 次调用，问题 %d" % (len(r["rows"]), r["calls"], len(r["problems"])))
    return _RESP({"ok": True, "data": {"rows": r["rows"], "design": r.get("design") or {}, "problems": r["problems"], "warnings": r.get("warnings") or [], "points": r["points"], "calls": r["calls"], "n": r.get("n"), "log": _log}})


@get("/api/structure/rows")
def r_structure_rows(h, path, q):
    """剧本页顶部的结构表（P123）。"""
    if isinstance(q, str):                                  # GET 的 q 是原始查询串
        from urllib.parse import parse_qs
        q = {k: v[0] for k, v in parse_qs(q).items()}
    sid = str((q or {}).get("story_id") or "")
    st = story_core.get_story(sid) if sid else None
    if not st:
        return _RESP({"ok": False, "error": "项目不存在"}, 404)
    settings = st.get("settings") or {}
    rows = [r for r in (settings.get("plan_rows") or []) if isinstance(r, dict) and str(r.get("one_line") or "").strip()]
    draft = settings.get("plan_draft") or {}
    return _RESP({"ok": True, "data": {"rows": rows, "version": int(settings.get("plan_version") or 0),
        "problems": settings.get("plan_problems") or [], "warnings": draft.get("warnings") or [],
        "synopsis": draft.get("draft") or "", "structure_draft": draft.get("structure_draft") or ""}})

@post("/api/flow/status")
def flow_status(h, path, d):
    """这台工作台现在在跑哪一版（只读，不改任何东西）。

    磁盘指纹 vs 进程里已加载的指纹：两个对不上就是**进程还没重启**，
    这时候在网页上测到的问题可能是旧代码的问题（P184）。
    """
    import hashlib
    import inspect
    import os as _os
    from cores import universal_writer as _uw, consistency as _cc
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))

    def disk(rel):
        # 按文本读并统一换行：磁盘上是 CRLF，inspect.getsource 拿到的是 LF，
        # 直接比字节永远对不上（第一版就踩了这个）
        try:
            with open(_os.path.join(root, rel), encoding="utf-8", newline=None) as f:
                return hashlib.sha256(f.read().encode("utf-8")).hexdigest()[:12]
        except Exception:
            return ""

    def loaded(mod):
        try:
            return hashlib.sha256(inspect.getsource(mod).encode("utf-8")).hexdigest()[:12]
        except Exception:
            return ""

    mods = {"cores/universal_writer.py": _uw, "cores/consistency.py": _cc}
    files = []
    fresh = True
    for rel, mod in mods.items():
        a, b = disk(rel), loaded(mod)
        if a and b and a != b:
            fresh = False
        files.append({"file": rel, "磁盘": a, "进程里": b, "一致": a == b})
    return _RESP({"ok": True, "data": {
        "写作流程": "universal（新）" if _uw.enabled() else "legacy（旧）",
        "模型核对": "开" if _uw.model_review_on() else "关（默认；16 次实测它输出常量 pass）",
        "确定性一致性检查": "开（数字矛盾／钟点对不上／有人叫自己的姓——这三条会拦稿；路程只写进提示，不拦）",
        "进程是新代码": fresh,
        "提示": "「进程是新代码」为 false 时，先重启工作台再测；刷新网页不会重载 Python。",
        "文件": files}})
