# -*- coding: utf-8 -*-
"""固定人物写真 V1.0 正式内核（接入 album_v3 现有项目结构）。
职责：动态 Album Planner、身份基准图、三阶段生成、旧项目迁移。
原则：不重写公共画册能力；人物第一；Prompt 80-180字；不用抽象审美词。
"""

import json
import os
import random
import re
import sys
import time
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "portrait_kernel_v1"))

from core.portrait_kernel import AlbumPlanner
from core.portrait_kernel.models import DomainDef
from core.portrait_kernel.scene_pool import get_scene
from core.portrait_kernel.wardrobe_pool import get_wardrobe_pool
from domains import DOMAINS

NEGATIVE = "摄影机, 摄影师, 监视器, 屏幕, 文字, 徽章, Logo, 乱码, 多余人物, 复制人, 多肢体"

MOTHER_CN = {
    "CLEAN_BEAUTY": "漂亮稳定",
    "QUIET_HUMAN": "安静日常",
    "ENVIRONMENT_REACTION": "环境反应",
    "BODY_IN_MOTION": "动作进行中",
    "UNGUARDED_JOY": "自然开心",
    "FREE_CAMERA": "自由摄影",
}

STATE_CN = {
    "STILL": "安静", "RELAXED": "松弛", "OBSERVING": "远望",
    "INTERACTING": "日常", "MOVING": "行走", "REACTING": "环境反应",
    "JOY": "自然笑",
}

GAZE_MAP = {
    "STILL": "看向镜头", "RELAXED": "看向窗外或远处", "OBSERVING": "看向远处",
    "INTERACTING": "看向手中或身旁", "MOVING": "回头看向镜头",
    "REACTING": "看向风或光来的方向", "JOY": "看向镜头，带着笑意",
}

SUBJECT_RATIO = {
    "close_up": "80%", "head_shoulders": "70%", "half_body": "60%",
    "three_quarter": "55%", "full_body": "50%", "environmental_portrait": "40%",
}

PHOTO_LOOK_LIGHT = {
    "NATURAL_REAL": "自然真实光线",
    "SOFT_DAYLIGHT": "柔和自然光",
    "COOL_DOCUMENTARY": "清冷纪实光线",
    "WARM_LIFESTYLE": "暖色生活光",
    "LIGHT_EDITORIAL": "轻时尚柔和光",
}

DEFAULT_CHARACTER = {
    "REAL_WORLD": "20岁左右成年东亚女性，身形纤细偏瘦，黑色自然中短发，年轻清秀的面部轮廓，深色眼睛，左脸一颗很淡的小痣",
    "SPACE_LIFE": "20岁左右成年东亚女性，身形纤细偏瘦，黑色自然中短发，年轻清秀的面部轮廓，深色眼睛，左脸一颗很淡的小痣",
}

DOMAIN_CN = {"REAL_WORLD": "真实世界", "SPACE_LIFE": "太空生活"}


def _clean(text, limit=400):
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _safe_name(name):
    name = re.sub(r'[\\/:*?"<>|]+', "_", str(name or "").strip())
    return name or "未命名写真"


def _domain(ui):
    d = str((ui or {}).get("domain") or "REAL_WORLD").strip().upper()
    return d if d in DOMAINS else "REAL_WORLD"


def _count(ui):
    try:
        c = int((ui or {}).get("count") or 12)
    except (TypeError, ValueError):
        c = 12
    return max(4, min(c, 24))


def _character(ui, domain):
    text = _clean((ui or {}).get("fixedCharacter"), 300)
    return text or DEFAULT_CHARACTER[domain]


def _look_light(ui):
    look = str((ui or {}).get("photoLook") or "NATURAL_REAL").strip().upper()
    return PHOTO_LOOK_LIGHT.get(look, "自然真实光线")


def _constrained_domain(domain_id, allowed_scenes):
    """手动场景范围：克隆 DomainDef，只保留允许的场景。"""
    base = DOMAINS[domain_id]
    allowed = [s for s in allowed_scenes if s in base.scene_ids]
    if not allowed:
        return base
    suitability = {}
    for mother, scenes in base.scene_suitability.items():
        keep = [s for s in scenes if s in allowed]
        suitability[mother] = keep or allowed
    return DomainDef(
        id=base.id, name=base.name, reality_anchor=base.reality_anchor,
        scene_ids=allowed, wardrobe_ids=base.wardrobe_ids, character=base.character,
        scene_suitability=suitability, preferred_wardrobe={
            s: [w for w in v if w in base.wardrobe_ids] for s, v in base.preferred_wardrobe.items()
        } if False else base.preferred_wardrobe,
        rhythm_cycle=base.rhythm_cycle)


def _apply_wardrobe(shots, domain, ui, pool):
    strategy = str((ui or {}).get("wardrobeStrategy") or "AUTO").strip().upper()
    if strategy == "FIXED":
        fixed = _clean((ui or {}).get("fixedWardrobe"), 100)
        if not fixed:
            fixed = pool[shots[0]["wardrobe_id"]].description
        for s in shots:
            s["wardrobe_id"] = "FIXED"
            s["wardrobe_desc"] = fixed
        return
    if strategy == "CUSTOM":
        lines = [x.strip() for x in str((ui or {}).get("customWardrobe") or "").splitlines() if x.strip()]
        if lines:
            for i, s in enumerate(shots):
                s["wardrobe_id"] = "CUSTOM%d" % (i % len(lines) + 1)
                s["wardrobe_desc"] = lines[i % len(lines)]
            return
    for s in shots:
        s["wardrobe_desc"] = pool[s["wardrobe_id"]].description


def _build_prompt(identity, shot, look_light, must_include, custom_art):
    near = shot["frame"] in ("close_up", "head_shoulders")
    char = identity
    if near:
        char += "，皮肤自然，眼睛有真实水光，头发有零散碎发"
    parts = [char, shot["action"], "穿" + shot["wardrobe_desc"], shot["scene_space"]]
    if shot.get("effect"):
        parts.append(shot["effect"])
    camera = "%dmm，%s，%s，%s" % (
        shot["lens"],
        {28: "相机离她约0.8米", 35: "相机离她约0.8米", 50: "相机离她约1.5米",
         70: "相机离她约2米", 85: "相机离她约3米", 100: "远距离观察"}.get(shot["lens"], "正常距离"),
        {"close_up": "近景", "head_shoulders": "头肩", "half_body": "半身",
         "three_quarter": "七分身", "full_body": "全身",
         "environmental_portrait": "环境人像"}[shot["frame"]],
        {"eye_level": "平视", "slight_high": "轻微俯拍", "slight_low": "轻微仰拍",
         "side": "侧面机位", "three_quarter": "3/4侧面", "close_wide": "近距离广角"}[shot["angle"]])
    parts.append(camera)
    light = shot["lighting"]
    if look_light and look_light not in light:
        light += "，" + look_light
    parts.append(light)
    if must_include:
        parts.append("画面中包含：" + "、".join(must_include))
    if custom_art:
        parts.append(_clean(custom_art, 120))
    return "。".join(parts) + "。"


def photo_plan_card(ui):
    """AI规划卡片：结构化、可读，不生成大段散文。"""
    domain = _domain(ui)
    count = _count(ui)
    concept = _clean((ui or {}).get("originalTheme"), 200) or "人物写真"
    allowed = [s for s in str((ui or {}).get("sceneScope") or "").split(",") if s.strip()]
    use_domain = _constrained_domain(domain, allowed) if allowed else DOMAINS[domain]
    album = AlbumPlanner().plan(use_domain, count, concept)
    pool = get_wardrobe_pool(domain)
    wardrobe_summary = []
    for s in album.shots[:8]:
        desc = pool[s.wardrobe_id].description
        if desc not in wardrobe_summary:
            wardrobe_summary.append(desc)
    rhythm = [MOTHER_CN[s.mother] for s in album.shots]
    lens_cnt = {}
    for s in album.shots:
        lens_cnt[s.lens] = lens_cnt.get(s.lens, 0) + 1
    frames = sorted(set(s.frame for s in album.shots))
    camera_summary = "焦段：%s；景别：%s" % (
        " / ".join("%dmm×%d" % (k, v) for k, v in sorted(lens_cnt.items())),
        "、".join(frames))
    return {
        "domain": domain,
        "domain_cn": DOMAIN_CN[domain],
        "concept": concept,
        "shooting_tendency": str((ui or {}).get("photoTendency") or "NATURAL_LIFE"),
        "scene_pool": [get_scene(s.id).name for s in (
            [get_scene(x) for x in use_domain.scene_ids])],
        "scene_ids": use_domain.scene_ids,
        "wardrobe_summary": wardrobe_summary[:6],
        "album_rhythm": rhythm,
        "camera_summary": camera_summary,
        "count": count,
    }


def photo_project(ui):
    """创建 V1 写真项目（album_v3 兼容结构）。"""
    domain = _domain(ui)
    count = _count(ui)
    concept = _clean((ui or {}).get("originalTheme"), 200) or "人物写真"
    name = _clean((ui or {}).get("albumName"), 40) or concept
    character = _character(ui, domain)
    look_light = _look_light(ui)
    allowed = [s for s in str((ui or {}).get("sceneScope") or "").split(",") if s.strip()]
    use_domain = _constrained_domain(domain, allowed) if allowed else DOMAINS[domain]
    album = AlbumPlanner().plan(use_domain, count, name)
    album.character.custom_weak_lock = character
    pool = get_wardrobe_pool(domain)

    shots = []
    for s in album.shots:
        scene = get_scene(s.scene_id)
        shots.append({
            "index": s.index + 1, "motherType": s.mother, "scene": s.scene_id,
            "wardrobe_id": s.wardrobe_id, "state": s.state, "action": s.action,
            "lens": s.lens, "frame": s.frame, "angle": s.angle,
            "lighting": s.lighting, "effect": s.effect, "aspect": s.aspect,
            "seed": 0,
        })
    _apply_wardrobe(shots, domain, ui, pool)
    for s in shots:
        scene = get_scene(s["scene"])
        s["scene_space"] = scene.space
        s["scene_name"] = scene.name
        s["gaze"] = GAZE_MAP[s["state"]]
        s["subjectRatio"] = SUBJECT_RATIO[s["frame"]]
        s["aspectRatio"] = _resolve_aspect(ui, s)
        s["title"] = "%s%s" % (scene.name, STATE_CN[s["state"]])
        s["prompt"] = _build_prompt(
            character, s, look_light,
            [x.strip() for x in (ui.get("mustInclude") or []) if str(x).strip()],
            _clean((ui or {}).get("advancedRequirements"), 120))

    # 构图模式覆盖
    comp = str((ui or {}).get("compositionMode") or "AUTO_MIX").upper()
    if comp == "PORTRAIT_ONLY":
        for s in shots:
            s["aspectRatio"] = "4:5"
    elif comp == "LANDSCAPE_ONLY":
        for s in shots:
            s["aspectRatio"] = "3:2"
    elif comp == "FIXED":
        fixed = str((ui or {}).get("fixedAspect") or "4:5")
        for s in shots:
            s["aspectRatio"] = fixed

    base_seed = int((ui or {}).get("baseSeed") or 0) or random.randrange(1, 1900000000)
    for s in shots:
        s["seed"] = base_seed

    # 三张预览：从正式 plan 中选（CLEAN/QUIET、ENV、MOTION/FREE）
    preview_idx = []
    for want in (("CLEAN_BEAUTY", "QUIET_HUMAN"), ("ENVIRONMENT_REACTION",),
                 ("BODY_IN_MOTION", "FREE_CAMERA")):
        for s in shots:
            if s["motherType"] in want and s["index"] not in preview_idx:
                preview_idx.append(s["index"])
                break
    for i, s in enumerate(shots[:3], 1):
        if s["index"] not in preview_idx:
            preview_idx.append(s["index"])
    preview_idx = preview_idx[:3]

    identity_scene = get_scene(shots[0]["scene"])
    identity_prompt = ("%s。她站在%s，安静正面看向镜头，双手自然垂落。穿%s。"
                       "85mm，相机离她约3米，半身，平视，画面清晰。%s。" % (
                           character, identity_scene.space, shots[0]["wardrobe_desc"],
                           look_light))

    images = []
    for s in shots:
        images.append({
            "index": s["index"], "title": s["title"], "role": s["motherType"],
            "status": "pending", "autoPrompt": s["prompt"], "activePrompt": s["prompt"],
            "promptSource": "auto", "file": "", "seed": base_seed,
            "history": [], "subject": s["scene_name"], "action": s["action"],
            "environment": s["scene_space"], "shotSize": s["frame"],
            "cameraAngle": s["angle"], "lensFeeling": "%dmm" % s["lens"],
            "lighting": s["lighting"], "aspectRatio": s["aspectRatio"],
            "uniquePoint": MOTHER_CN[s["motherType"]], "caption": s["gaze"],
            "sampleScore": 60,
            "autoShot": {
                "scene": s["scene"], "wardrobe_desc": s["wardrobe_desc"],
                "state": s["state"], "action": s["action"],
                "lens": s["lens"], "frame": s["frame"], "angle": s["angle"],
                "lighting": s["lighting"], "effect": s["effect"],
                "aspectRatio": s["aspectRatio"], "prompt": s["prompt"],
            },
        })

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    project = {
        "version": "v1-photo",
        "id": re.sub(r"[^0-9a-f]", "", __import__("hashlib").md5(
            ("character_photography\n" + name + "\n" + now).encode("utf-8")).hexdigest())[:12],
        "name": name, "preset": "character_photography", "kernel": "character",
        "status": "planning", "created": now, "updated": now,
        "userInput": {
            "albumName": name, "originalTheme": concept,
            "expandedBrief": str((ui or {}).get("expandedBrief") or "").strip(),
            "fixedCharacter": character,
            "domain": domain, "photoTendency": str((ui or {}).get("photoTendency") or "NATURAL_LIFE"),
            "photoLook": str((ui or {}).get("photoLook") or "NATURAL_REAL"),
            "wardrobeStrategy": str((ui or {}).get("wardrobeStrategy") or "AUTO").upper(),
            "fixedWardrobe": _clean((ui or {}).get("fixedWardrobe"), 100),
            "customWardrobe": str((ui or {}).get("customWardrobe") or "").strip(),
            "compositionMode": str((ui or {}).get("compositionMode") or "AUTO_MIX").upper(),
            "fixedAspect": str((ui or {}).get("fixedAspect") or "4:5"),
            "sceneScope": ",".join(use_domain.scene_ids),
            "advancedRequirements": _clean((ui or {}).get("advancedRequirements"), 500),
            "mustInclude": [str(x).strip() for x in (ui.get("mustInclude") or []) if str(x).strip()],
            "mustExclude": [str(x).strip() for x in (ui.get("mustExclude") or []) if str(x).strip()],
            "count": count, "aspectRatio": str((ui or {}).get("compositionMode") or "AUTO_MIX"),
            "pdfTemplate": "pure",
        },
        "lockedFacts": [], "seriesBible": {}, "sequenceDesign": {}, "diversityMatrix": {},
        "images": images,
        "identityReference": {
            "prompt": identity_prompt, "file": "", "seed": base_seed,
            "status": "pending", "confirmed": False, "history": [],
        },
        "albumPlan": {
            "concept": concept, "domain": domain, "count": count,
            "scenes": use_domain.scene_ids,
            "wardrobe": list(dict.fromkeys(s["wardrobe_desc"] for s in shots)),
            "rhythm": [MOTHER_CN[s["motherType"]] for s in shots],
            "shots": shots,
        },
        "sample": {"imageIndex": preview_idx[0], "previewIndexes": preview_idx,
                   "confirmed": False, "promptEdited": False, "phase": "identity"},
        "phase": "identity",
        "generation": {
            "baseSeed": base_seed, "completed": [], "pending": list(range(1, count + 1)),
            "failed": [], "currentIndex": None,
        },
        "pdf": {"template": "pure", "showTitle": False, "showCaption": False,
                "showPrompt": False, "pureImageVersion": True,
                "path": "", "coverPath": "", "coverUrl": ""},
        "dir": "",
    }
    project["seriesBible"] = {
        "subjectLock": character, "worldLock": DOMAIN_CN[domain] + "：" + " / ".join(
            get_scene(x).name for x in use_domain.scene_ids),
        "wardrobeLock": "、".join(project["albumPlan"]["wardrobe"][:6]),
        "paletteLock": "", "materialLock": DOMAINS[domain].reality_anchor,
        "cameraLock": _camera_summary(shots), "styleLock": project["userInput"]["photoLook"],
    }
    project["albumPlan"]["previewIndexes"] = preview_idx
    return project


def _camera_summary(shots):
    lens_cnt = {}
    frames = set()
    for s in shots:
        lens_cnt[s["lens"]] = lens_cnt.get(s["lens"], 0) + 1
        frames.add(s["frame"])
    return "焦段：%s；景别：%s" % (
        " / ".join("%dmm×%d" % (k, v) for k, v in sorted(lens_cnt.items())),
        "、".join(sorted(frames)))


def _resolve_aspect(ui, shot):
    comp = str((ui or {}).get("compositionMode") or "AUTO_MIX").upper()
    if comp != "AUTO_MIX":
        return "4:5"
    return shot["aspect"]


def _abs_dir(project):
    from app import COMIC_DIR, safe_name
    rel = str(project.get("dir") or "").replace("\\", "/")
    parts = [safe_name(x) for x in rel.split("/") if x]
    return os.path.join(COMIC_DIR, *parts) if parts else COMIC_DIR


def _save(project):
    from album_v3_core import album_save_project
    album_save_project(project)


def _archive_path(abs_dir, fname):
    d = os.path.join(abs_dir, "原图")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, fname)


def _move_to_history(abs_dir, sub, fname):
    src = os.path.join(abs_dir, "原图", fname)
    if not os.path.isfile(src):
        return None
    hist = os.path.join(abs_dir, "重出历史", sub)
    os.makedirs(hist, exist_ok=True)
    dest = os.path.join(hist, time.strftime("%Y%m%d_%H%M%S") + "_" + fname)
    os.replace(src, dest)
    return os.path.basename(dest)


def photo_generate_identity(project, rerender=False, seed=None):
    """阶段1：生成人物基准图（独立于 images[]）。"""
    from app import comfy_generate
    ref = project["identityReference"]
    abs_dir = _abs_dir(project)
    fname = "00_身份基准图.png"
    if rerender and ref.get("file") and os.path.isfile(os.path.join(abs_dir, "原图", ref["file"])):
        old = _move_to_history(abs_dir, "00", ref["file"])
        if old:
            ref.setdefault("history", []).append({"file": old, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    prompt = ref.get("prompt") or ""
    sd = int(seed) if seed is not None else int(project["generation"]["baseSeed"])
    if rerender and seed is None:
        sd = random.randrange(1, 1900000000)
    ref["seed"] = sd
    project["generation"]["baseSeed"] = sd
    out = _archive_path(abs_dir, fname)
    comfy_generate(prompt, sd, out, (1024, 1280), negative_prompt=NEGATIVE)
    from urllib.parse import urlencode
    rel = os.path.relpath(out, os.path.join(os.path.dirname(os.path.abspath(__file__)), "漫画")).replace("\\", "/")
    ref.update({"file": fname, "status": "completed", "archUrl": "/panel?" + urlencode({"p": rel})})
    ref["confirmed"] = False
    _save(project)
    return project, {"url": ref.get("archUrl", ""), "file": fname}


def photo_generate_image(project, index, rerender=False, seed=None):
    """生成正式写真一张；index 为 1..count。"""
    from app import comfy_generate, COMIC_DIR
    from album_v3_core import ALBUM_ASPECT_SIZES
    idx = int(index)
    images = project.get("images") or []
    if idx < 1 or idx > len(images):
        raise RuntimeError("图片编号超出范围")
    img = images[idx - 1]
    abs_dir = _abs_dir(project)
    old_file = str(img.get("file") or "")
    if not rerender and img.get("status") == "completed":
        if old_file and os.path.isfile(os.path.join(abs_dir, "原图", old_file)):
            return project, {"url": img.get("archUrl", ""), "file": old_file, "skipped": True}
    if rerender and old_file and os.path.isfile(os.path.join(abs_dir, "原图", old_file)):
        old = _move_to_history(abs_dir, "%02d" % idx, old_file)
        if old:
            img.setdefault("history", []).append({"file": old, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    prompt = img.get("activePrompt") or img.get("autoPrompt") or ""
    base_seed = int(project["generation"].get("baseSeed") or random.randrange(1, 1900000000))
    if seed is not None:
        sd = int(seed)
    elif rerender:
        sd = random.randrange(1, 1900000000)
        if idx == int(project["sample"]["imageIndex"]):
            project["generation"]["baseSeed"] = sd
    else:
        sd = base_seed
    img["status"] = "generating"
    img["seed"] = sd
    project["generation"]["currentIndex"] = idx
    _save(project)
    fname = "%02d_%s.png" % (idx, _safe_name(img.get("title") or ("第%02d张" % idx)))
    out = _archive_path(abs_dir, fname)
    wh = ALBUM_ASPECT_SIZES.get(img.get("aspectRatio") or "4:5", (1024, 1280))
    comfy_generate(prompt, sd, out, wh, negative_prompt=NEGATIVE)
    rel = os.path.relpath(out, COMIC_DIR).replace("\\", "/")
    arch = "/panel?" + urlencode({"p": rel})
    img.update({"status": "completed", "file": fname, "seed": sd, "archUrl": arch,
                "activePrompt": prompt, "promptSource": img.get("promptSource") or "auto"})
    gen = project["generation"]
    gen["completed"] = sorted(set(gen.get("completed") or []) | {idx})
    gen["pending"] = [i for i in gen.get("pending") or [] if i != idx]
    gen["failed"] = [i for i in gen.get("failed") or [] if i != idx]
    gen["currentIndex"] = None
    project["status"] = "completed" if len(gen["completed"]) == len(images) else "images"
    _save(project)
    return project, {"url": arch, "file": fname, "skipped": False}


def photo_regenerate(project, index, prompt=None, restore_auto=False):
    if str(index) == "0" or index == 0:
        ref = project["identityReference"]
        if restore_auto:
            ref.pop("userPrompt", None)
        elif prompt:
            ref["prompt"] = prompt
        ref["confirmed"] = False
        _save(project)
        return project
    idx = int(index)
    img = project["images"][idx - 1]
    if restore_auto:
        img["activePrompt"] = img.get("autoPrompt") or ""
        img["promptSource"] = "auto"
    elif prompt:
        img["activePrompt"] = prompt
        img["promptSource"] = "user"
    _save(project)
    return project


def photo_confirm(project, confirmed, phase=None):
    phase = phase or project.get("phase") or "identity"
    if phase == "identity":
        project["identityReference"]["confirmed"] = bool(confirmed)
        if confirmed:
            project["phase"] = "preview"
    elif phase == "preview":
        project["sample"]["confirmed"] = bool(confirmed)
        if confirmed:
            project["phase"] = "album"
    _save(project)
    return project


def photo_resume(project):
    pending = []
    if project["identityReference"].get("status") != "completed":
        pending.append(0)
    pending += [i for i, im in enumerate(project["images"], 1) if im.get("status") != "completed"]
    project["generation"]["pending"] = pending
    _save(project)
    return pending


def _shot_prompt(project, shot):
    ui = project.get("userInput") or {}
    character = ui.get("fixedCharacter") or DEFAULT_CHARACTER.get(ui.get("domain", "REAL_WORLD"), "")
    look_light = _look_light(ui)
    must = ui.get("mustInclude") or []
    custom = _clean(ui.get("advancedRequirements"), 120)
    return _build_prompt(
        character, shot, look_light,
        [str(x).strip() for x in must if str(x).strip()], custom)


def photo_edit_shot(project, index, fields=None):
    """单张详细调整：直接写回 albumPlan.shots / images / activePrompt。"""
    fields = dict(fields or {})
    idx = int(index)
    images = project.get("images") or []
    if idx < 1 or idx > len(images):
        raise RuntimeError("图片编号超出范围")
    img = images[idx - 1]
    shots = ((project.get("albumPlan") or {}).get("shots")) or []
    shot = dict(shots[idx - 1]) if idx <= len(shots) else {}

    def upd(dst, key, value):
        if value is not None and str(value).strip() != "":
            dst[key] = value

    if fields.get("scene"):
        sid = str(fields["scene"])
        scene = get_scene(sid)
        upd(shot, "scene", sid)
        upd(shot, "scene_name", scene.name)
        upd(shot, "scene_space", scene.space)
        upd(img, "subject", scene.name)
        upd(img, "environment", scene.space)
    for key in ("wardrobe_desc", "state", "action", "lighting", "effect"):
        if key in fields:
            upd(shot, key, fields[key])
    if fields.get("lens") not in (None, ""):
        try:
            upd(shot, "lens", int(fields["lens"]))
        except (TypeError, ValueError):
            pass
    for key in ("frame", "angle", "aspectRatio"):
        if key in fields:
            upd(shot, key, fields[key])

    img["action"] = shot.get("action", img.get("action", ""))
    img["shotSize"] = shot.get("frame", img.get("shotSize", ""))
    img["cameraAngle"] = shot.get("angle", img.get("cameraAngle", ""))
    img["lensFeeling"] = "%dmm" % shot.get("lens", 50)
    img["lighting"] = shot.get("lighting", img.get("lighting", ""))
    img["aspectRatio"] = shot.get("aspectRatio", img.get("aspectRatio", "4:5"))
    prompt = str(fields.get("prompt") or "").strip()
    if prompt:
        shot["prompt"] = prompt
        img["activePrompt"] = prompt
        img["promptSource"] = "user"
    else:
        shot["prompt"] = _shot_prompt(project, shot)
        img["activePrompt"] = shot["prompt"]
        img["promptSource"] = "auto"
    img["edited"] = True
    if idx <= len(shots):
        shots[idx - 1] = shot
    _save(project)
    return project


def photo_restore_shot(project, index):
    """恢复AI设计：回到 autoPrompt 与 autoShot。"""
    idx = int(index)
    images = project.get("images") or []
    if idx < 1 or idx > len(images):
        raise RuntimeError("图片编号超出范围")
    img = images[idx - 1]
    auto = img.get("autoShot") or {}
    shots = ((project.get("albumPlan") or {}).get("shots")) or []
    if idx <= len(shots):
        shot = shots[idx - 1]
        for k, v in auto.items():
            shot[k] = v
        shots[idx - 1] = shot
    img["activePrompt"] = img.get("autoPrompt") or auto.get("prompt") or ""
    img["promptSource"] = "auto"
    img["edited"] = False
    img["action"] = auto.get("action", img.get("action", ""))
    img["shotSize"] = auto.get("frame", img.get("shotSize", ""))
    img["cameraAngle"] = auto.get("angle", img.get("cameraAngle", ""))
    img["lensFeeling"] = "%dmm" % auto.get("lens", 50)
    img["lighting"] = auto.get("lighting", "")
    img["aspectRatio"] = auto.get("aspectRatio", "4:5")
    _save(project)
    return project


def photo_replan(project):
    """重新规划整套：只改 Album Plan，保留已确认人物。"""
    ui = dict(project.get("userInput") or {})
    identity = project.get("identityReference") or {}
    base_seed = int(identity.get("seed") or
                    project.get("generation", {}).get("baseSeed") or
                    random.randrange(1, 1900000000))
    ui["baseSeed"] = base_seed
    newp = photo_project(ui)
    newp["dir"] = project.get("dir", "")
    newp["id"] = project.get("id", newp["id"])
    newp["name"] = project.get("name", newp["name"])
    newp["created"] = project.get("created", newp["created"])
    newp["identityReference"] = identity
    newp["phase"] = "preview" if identity.get("confirmed") else "identity"
    newp["sample"]["confirmed"] = False
    newp["sample"]["imageIndex"] = newp["sample"]["previewIndexes"][0]
    newp["generation"]["baseSeed"] = base_seed
    _save(newp)
    return newp


def migrate_photo_project(project):
    """旧 character_photography 项目内存迁移，不破坏原文件。"""
    if str(project.get("preset") or "") != "character_photography":
        return project
    ui = project.setdefault("userInput", {})
    ui.setdefault("domain", "REAL_WORLD")
    ui.setdefault("photoLook", "NATURAL_REAL")
    ui.setdefault("photoTendency", "NATURAL_LIFE")
    ui.setdefault("wardrobeStrategy", "AUTO")
    ui.setdefault("compositionMode", "AUTO_MIX")
    ui.setdefault("sceneScope", "")
    project.setdefault("phase", "identity")
    if "identityReference" not in project:
        images = project.get("images") or []
        if images and str(images[0].get("role") or "") == "identity_reference":
            old = images.pop(0)
            project["identityReference"] = {
                "prompt": old.get("activePrompt") or old.get("autoPrompt") or "",
                "file": old.get("file") or "", "seed": old.get("seed") or 0,
                "status": old.get("status") or "pending", "confirmed": False,
                "history": old.get("history") or [],
            }
            for i, im in enumerate(images, 1):
                im["index"] = i
        else:
            character = ui.get("fixedCharacter") or DEFAULT_CHARACTER[ui["domain"]]
            project["identityReference"] = {
                "prompt": "%s。她站在普通场景前，安静正面看向镜头，双手自然垂落。85mm，半身，平视，画面清晰。自然真实光线。" % character,
                "file": "", "seed": project.get("generation", {}).get("baseSeed", 0),
                "status": "pending", "confirmed": False, "history": [],
            }
    sample = project.setdefault("sample", {})
    sample.setdefault("imageIndex", 1)
    sample.setdefault("previewIndexes", [1, 2, 3])
    sample.setdefault("phase", project.get("phase", "identity"))
    if not project.get("albumPlan"):
        project["albumPlan"] = {"concept": ui.get("originalTheme") or project.get("name", ""),
                                "domain": ui["domain"], "count": len(project.get("images") or [])}
    project.setdefault("pdf", {}).setdefault("template", "pure")
    return project
