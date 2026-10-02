# -*- coding: utf-8 -*-
"""文字链内容级验收：一组通用检查，不含任何具体故事的字面量。

以前的验收是这样写的：
    ("年龄/身份固定", "二十八" in preview or "28" in preview, "A28/B20")
    ("State 继承正确", "removed" in str(ledger["scene3_after"]["B"]["wardrobe"]), ...)
换个故事这些断言要么恒真要么报错，等于没验。这里所有期望值都从
StoryProfile 和账本数据推导，换故事不用改代码。

每个检查返回 {"item": 名称, "ok": bool, "detail": 说明}。
"""
import re

from cores.prompt_hygiene import find_internal_ids
from cores.script_text import dialogue_lines, find_role_code_leak

__all__ = ["run_all", "CHECKS", "body_of", "cn_number"]

# 曾经把运镜/机位堆成标签，官方指南 4.3 明确禁止
LABEL_TOKENS = ("镜头运动：", "机位：", "机高：", "角度：", "距离：", "景深：", "切换理由：")

_CN_DIGITS = "零一二三四五六七八九"


def cn_number(n):
    """0-99 的阿拉伯数字 → 中文写法，用于年龄一致性检查。"""
    n = int(n)
    if n < 10:
        return _CN_DIGITS[n]
    if n < 20:
        return "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    return _CN_DIGITS[n // 10] + "十" + (_CN_DIGITS[n % 10] if n % 10 else "")


def body_of(prompt):
    """只取 detailed_description 段：英文 summary 里的冠词 A 不是角色代号。"""
    a = str(prompt).find("detailed_description:")
    b = str(prompt).find("overall_soundscape:")
    return prompt[a:b] if a >= 0 and b > a else ""


# ---------- 各项检查 ----------

def check_no_internal_ids(profile, art):
    allow = set(profile.role_subjects.values()) | {profile.scene_subject_tag}
    bad = {}
    for sid, p in art["prompts"].items():
        found = find_internal_ids(p, allow=allow)
        if found:
            bad[sid] = sorted(set(found))
    return {"item": "最终提示词无内部工程 ID", "ok": not bad,
            "detail": "全部已换成自然称呼" if not bad else "残留：%s" % bad}


def check_no_role_code_leak(profile, art):
    bad = {}
    for sid, p in art["prompts"].items():
        found = find_role_code_leak(body_of(p))
        found = [c for c in found if c in profile.role_codes]
        if found:
            bad[sid] = sorted(set(found))
    return {"item": "正文无裸角色代号", "ok": not bad,
            "detail": "角色一律用 <Subject N>" if not bad else "残留：%s" % bad}


def check_no_label_stacking(profile, art):
    bad = {sid: [t for t in LABEL_TOKENS if t in body_of(p)]
           for sid, p in art["prompts"].items()}
    bad = {k: v for k, v in bad.items() if v}
    return {"item": "运镜/机位写成自然语句（官方 4.3）", "ok": not bad,
            "detail": "无参数标签堆叠" if not bad else "仍在堆标签：%s" % bad}


def check_dialogue_verbatim(profile, art):
    script_all = "\n".join(art["script"].values())
    true_lines = set(l for _, _, l in dialogue_lines(script_all, profile.speakers))
    prompt_lines = []
    for p in art["prompts"].values():
        prompt_lines += re.findall(r"<d>\[[^\]]+\](.+?)</d>", p)
    audio_lines = [l for a in art["audio"] for l in a.get("dialogue", [])]

    problems = []
    for l in prompt_lines:
        if l not in script_all:
            problems.append("提示词出现剧本没有的台词：%s" % l[:20])
    for l in audio_lines:
        if l.strip() in profile.speakers:
            problems.append("Audio 把人名当成了台词：%s" % l)
        elif l not in true_lines:
            problems.append("Audio 出现非台词内容：%s" % l[:20])
    expect = len(true_lines)
    return {"item": "对白逐字出自剧本", "ok": not problems,
            "detail": ("剧本 %d 条；提示词 %d 条 / Audio %d 条全部命中"
                       % (expect, len(prompt_lines), len(audio_lines)))
            if not problems else "；".join(problems[:3])}


def check_music_policy(profile, art):
    if profile.policy.get("music") != "none":
        # 不适用 ≠ 通过：单独标记，避免在报告里冒充一条「已验证」
        return {"item": "音乐策略", "ok": True, "skipped": True,
                "detail": "本项目允许背景音乐，此项不适用"}
    bad = [sid for sid, p in art["prompts"].items() if "non_diegetic_music: N/A" not in p]
    return {"item": "无背景音乐（按项目策略）", "ok": not bad,
            "detail": "全部写 N/A" if not bad else "未写 N/A：%s" % bad}


def check_duration_contract(profile, art):
    limits = {s["sequence_id"]: s.get("target_duration") for s in profile.sequences}
    bad = []
    for seq in art["storyboard"]:
        target = limits.get(seq["sequence_id"])
        total = sum(float(s.get("duration") or 0) for s in seq["shots"])
        if target and total > float(target) + 1e-6:
            bad.append("%s %.1fs > %ss" % (seq["sequence_id"], total, target))
    return {"item": "镜头时长总和不超目标时长", "ok": not bad,
            "detail": "各段均在合约内" if not bad else "；".join(bad)}


def check_reference_budget(profile, art):
    cap = profile.policy.get("max_pictures", 3)
    bad = []
    for s in art["ref_plan"]:
        pics = s.get("pictures", [])
        sources = [p.get("source") for p in pics]
        if len(pics) > cap:
            bad.append("%s 用了 %d 张 > %d" % (s["sequence_id"], len(pics), cap))
        if len(set(sources)) != len(sources):
            bad.append("%s 出现重复参考源" % s["sequence_id"])
    return {"item": "参考图最少且不重复", "ok": not bad,
            "detail": "每段 ≤%d 张且互不重复" % cap if not bad else "；".join(bad)}


def check_no_empty_fields(profile, art):
    fields = art.get("required_shot_fields") or ()
    bad = []
    for seq in art["storyboard"]:
        for s in seq["shots"]:
            for f in fields:
                if not str(s.get(f) or "").strip():
                    bad.append("%s/%s 缺 %s" % (seq["sequence_id"], s.get("shot_id"), f))
    return {"item": "分镜关键字段无空值", "ok": not bad,
            "detail": "全部非空" if not bad else "；".join(bad[:3])}


def check_event_order(profile, art):
    covered = []
    for s in profile.sequences:
        covered += list(s.get("events_covered", []))
    known = list(profile.events)
    extra = [e for e in covered if e not in known]
    order_ok = [e for e in covered if e in known] == [e for e in known if e in covered]
    ok = not extra and order_ok
    detail = "覆盖 %d 个事件，顺序与事件账本一致" % len(covered)
    if extra:
        detail = "出现账本外的新事件：%s" % extra
    elif not order_ok:
        detail = "事件顺序与账本不一致"
    return {"item": "事件未新增且未乱序", "ok": ok, "detail": detail}


def check_state_pairs(profile, art):
    ledger = art["ledger"]
    n = len(profile.sequences)
    missing = [k for i in range(1, n + 1)
               for k in ("scene%d_before" % i, "scene%d_after" % i) if k not in ledger]
    return {"item": "每场都有进出状态", "ok": not missing,
            "detail": "%d 场 before/after 齐全" % n if not missing else "缺：%s" % missing}


_REVERT_RE = re.compile(r"(穿上|披上|套上|穿回|重新穿|扣上)")


def check_wardrobe_no_revert(profile, art):
    """通用因果检查：脱掉的衣物不能在没有「穿回」事件的情况下自己回来。"""
    ledger, script = art["ledger"], art["script"]
    scene_ids = [s.get("scene") for s in profile.sequences]
    removed = {c["code"]: set() for c in profile.characters}
    bad = []
    for i, scene_id in enumerate(scene_ids, start=1):
        text = script.get(scene_id, "")
        for key in ("scene%d_before" % i, "scene%d_after" % i):
            state = ledger.get(key, {})
            for code in removed:
                w = str((state.get(code) or {}).get("wardrobe", ""))
                for slot in list(removed[code]):
                    if slot in w and "removed" not in w and not _REVERT_RE.search(text):
                        bad.append("%s 的 %s 在 %s 无事件地恢复" % (code, slot, key))
                for mo in re.finditer(r"([a-z_]+)\s*=\s*removed", w):
                    removed[code].add(mo.group(1))
    return {"item": "脱下的衣物不会自行恢复", "ok": not bad,
            "detail": "跨段服装状态单向继承" if not bad else "；".join(bad[:3])}


def check_age_consistency(profile, art):
    """角色年龄在正文里保持一致（阿拉伯数字与中文写法都认）。"""
    texts = "\n".join(art.get("canon_texts") or [])
    bad = []
    for c in profile.characters:
        age = c.get("age")
        if not age or not age.isdigit():
            continue
        if age not in texts and cn_number(age) not in texts:
            bad.append("%s（%s岁）在正文中找不到一致的年龄表述" % (c["name"], age))
    return {"item": "角色年龄与设定一致", "ok": not bad,
            "detail": "；".join("%s=%s岁" % (c["name"], c["age"]) for c in profile.characters)
            if not bad else "；".join(bad)}


CHECKS = [
    check_event_order,
    check_state_pairs,
    check_wardrobe_no_revert,
    check_age_consistency,
    check_duration_contract,
    check_reference_budget,
    check_no_internal_ids,
    check_no_role_code_leak,
    check_no_label_stacking,
    check_dialogue_verbatim,
    check_music_policy,
    check_no_empty_fields,
]


def run_all(profile, artifacts):
    """artifacts 需要提供：prompts / script / audio / storyboard / ref_plan / ledger
    以及可选的 required_shot_fields、canon_texts。"""
    results = [fn(profile, artifacts) for fn in CHECKS]
    return {"result": "PASS" if all(r["ok"] for r in results) else "FAIL", "checks": results}

# ---------- 结构与资产纪律（同样不含故事字面量） ----------

CAMERA_TERMS = ("特写", "近景", "中景", "全景", "推近", "拉远", "焦段", "广角", "长焦",
                "机位", "镜头运动", "景深", "俯拍", "仰拍")
WARDROBE_SLOTS = ("outerwear", "base_top", "base_bottom", "footwear", "accessories")
ALLOWED_REF_ROLES = ("character_identity", "scene_master", "previous_end_frame", "prop_master")
FORBIDDEN_ASSET_WORDS = ("current look", "current_look", "alternate", "second view", "second_view")


def check_script_no_camera_terms(profile, art):
    """剧本是给人读的，摄影属于下游；剧本里提前写镜头术语说明层级串了。"""
    bad = []
    for scene_id, text in art["script"].items():
        hit = [t for t in CAMERA_TERMS if t in text]
        if hit:
            bad.append("%s 出现 %s" % (scene_id, hit))
    return {"item": "剧本未提前写摄影术语", "ok": not bad,
            "detail": "剧本只有场景/动作/对白" if not bad else "；".join(bad[:3])}


def check_wardrobe_slots(profile, art):
    ward = art.get("wardrobe")
    if ward is None:
        return {"item": "服装槽位完整", "ok": False, "detail": "缺少 wardrobe 数据，无法验证"}
    bad = []
    for cid, data in ward.items():
        base = data.get("baseline", {})
        missing = [k for k in WARDROBE_SLOTS if k not in base]
        if missing:
            bad.append("%s 缺 %s" % (cid, missing))
    return {"item": "服装槽位完整", "ok": not bad,
            "detail": "每个角色 %d 个槽位齐全" % len(WARDROBE_SLOTS) if not bad else "；".join(bad)}


def check_single_master_per_entity(profile, art):
    """一个角色/一个地点只允许一份正式 Master 提示词。"""
    prompts = art.get("krea_prompt_ids")
    if prompts is None:
        return {"item": "每个实体只有一份 Master", "ok": False, "detail": "缺少资产清单，无法验证"}
    dup = [k for k in set(prompts) if prompts.count(k) > 1]
    return {"item": "每个实体只有一份 Master", "ok": not dup,
            "detail": "共 %d 份，实体各一份" % len(prompts) if not dup else "重复：%s" % dup}


def check_no_alternate_assets(profile, art):
    """只看真正登记在册的资产条目；清单散文里写「禁止 current look」不算违规。"""
    entries = art.get("asset_entries")
    if entries is None:
        return {"item": "无 current look / 第二视角等衍生资产", "ok": False,
                "detail": "缺少资产条目，无法验证"}
    hit = []
    for e in entries:
        text = " ".join(str(e.get(k, "")) for k in ("asset_id", "type", "name", "prompt_file")).lower()
        hit += ["%s: %s" % (e.get("asset_id"), w) for w in FORBIDDEN_ASSET_WORDS if w.lower() in text]
    return {"item": "无 current look / 第二视角等衍生资产", "ok": not hit,
            "detail": "%d 个资产条目全部为正式 Master" % len(entries) if not hit else "出现：%s" % hit}


def check_reference_roles(profile, art):
    bad = []
    for s in art["ref_plan"]:
        for p in s.get("pictures", []):
            if p.get("role") not in ALLOWED_REF_ROLES:
                bad.append("%s: %s" % (s["sequence_id"], p.get("role")))
    return {"item": "参考图职责在白名单内", "ok": not bad,
            "detail": "仅身份/场景/连续帧/道具" if not bad else "越界：%s" % bad}


def check_custom_profile_preserved(profile, art):
    p = art.get("custom_profile_path")
    ok = bool(p) and p.exists()
    return {"item": "用户自定义内容规则原样保存", "ok": ok,
            "detail": "已保存：%s" % (p.name if ok else "文件缺失")}


CHECKS = CHECKS + [
    check_script_no_camera_terms,
    check_wardrobe_slots,
    check_single_master_per_entity,
    check_no_alternate_assets,
    check_reference_roles,
    check_custom_profile_preserved,
]

# ---------- 送进模型之前的出关检查 ----------

# 实体归一由 cores.auto_repair 统一负责（原先这里有第二份实现，已删）
from cores.auto_repair import entity_of  # noqa: E402


def check_reference_uniqueness(profile, art):
    """一个角色只能有一张参考图，一个场景只能有一张，且不许出现同一实体的两种形态。

    历史事故：大量重复/冲突的参考图（Master + 裁图、两个场景视角）把模型带乱。
    """
    problems = []
    for s in art["ref_plan"]:
        sid = s.get("sequence_id")
        pics = s.get("pictures", [])
        entities, roles = {}, {}
        for p in pics:
            ent = entity_of(p.get("source"))
            role = p.get("role")
            roles.setdefault(role, []).append(ent)
            if ent in entities:
                problems.append("%s：实体 %s 出现 %d 次" % (sid, ent, entities[ent] + 1))
            entities[ent] = entities.get(ent, 0) + 1
        scenes = [e for e in roles.get("scene_master", [])]
        if len(scenes) > 1:
            problems.append("%s：出现 %d 个场景 Master（只允许 1 个）" % (sid, len(scenes)))
        for role, ents in roles.items():
            if role == "character_identity" and len(ents) != len(set(ents)):
                problems.append("%s：同一角色出现多张身份图" % sid)
    return {"item": "参考图每个实体只有一张", "ok": not problems,
            "detail": "人物各 1 张、场景 1 张，无重复实体" if not problems else "；".join(problems[:3])}


# Krea 提示词该有的要素（官方指引：长而具体的自然语言 + 显式材质/光线/镜头）
_KREA_REQUIRED = {"材质": ("材质", "质感", "肌理"),
                  "光线": ("光", "照", "色温"),
                  "镜头": ("mm", "镜头", "广角", "视角"),
                  "构图": ("构图", "画面", "前景", "背景", "视图")}


def check_image_prompt_shape(profile, art):
    """给 Krea 的最终提示词：够具体、无否定式、无内部 ID、单幅画面。"""
    from cores.prompt_hygiene import find_negations
    prompts = art.get("image_prompts") or {}
    if not prompts:
        return {"item": "图片提示词符合官方写法", "ok": False, "detail": "没有拿到图片提示词，无法验证"}
    problems = []
    for name, text in prompts.items():
        if len(text) < 120:
            problems.append("%s 过短（%d 字），不够具体" % (name, len(text)))
        if find_negations(text):
            problems.append("%s 含否定式：%s" % (name, find_negations(text)[:2]))
        if find_internal_ids(text):
            problems.append("%s 含内部 ID：%s" % (name, find_internal_ids(text)[:2]))
        for label, keys in _KREA_REQUIRED.items():
            if not any(k in text for k in keys):
                problems.append("%s 缺少%s描述" % (name, label))
    return {"item": "图片提示词符合官方写法", "ok": not problems,
            "detail": "%d 份提示词具体、正向、无内部 ID" % len(prompts)
            if not problems else "；".join(problems[:3])}


_H3_SECTIONS = ("subject_definitions:", "summary:", "retention_analysis:",
                "detailed_description:", "overall_soundscape:", "non_diegetic_music:")


def check_video_prompt_shape(profile, art):
    """给 H3 的最终提示词：官方 Ref2VA 六段式，顺序正确，首镜不带时间戳，对白用官方格式。"""
    problems = []
    for sid, p in art["prompts"].items():
        pos = [p.find(k) for k in _H3_SECTIONS]
        if any(x < 0 for x in pos):
            problems.append("%s 缺六段字段" % sid)
            continue
        if pos != sorted(pos):
            problems.append("%s 六段顺序不对" % sid)
        if "[Shot 1] At " in p:
            problems.append("%s 首镜写了时间戳（官方要求不写）" % sid)
        if "[Shot 1]" not in p:
            problems.append("%s 没有分镜标记" % sid)
        for line in re.findall(r"<d>(.*?)</d>", p):
            if not re.match(r"^\[[^\]]+\]", line):
                problems.append("%s 对白缺语言标签" % sid)
                break
    return {"item": "视频提示词符合官方六段式", "ok": not problems,
            "detail": "%d 份提示词结构合规" % len(art["prompts"])
            if not problems else "；".join(problems[:3])}


CHECKS = CHECKS + [
    check_reference_uniqueness,
    check_image_prompt_shape,
    check_video_prompt_shape,
]

_SCALE_HINT = re.compile(r"\d+(?:\.\d+)?\s*(?:米|㎡|平米|层高)")
_DEPTH_WORDS = ("纵深", "前景", "中景", "背景")
_WIDE_WORDS = ("广角", "中广角", "24mm", "28mm", "35mm")


def check_scene_prompt_scale(profile, art):
    """场景 Master 必须写清真实尺度与空间纵深，否则模型爱出又小又平的盒子空间。"""
    prompts = {k: v for k, v in (art.get("image_prompts") or {}).items() if "scene" in k.lower()}
    if not prompts:
        return {"item": "场景提示词有真实尺度与纵深", "ok": False, "detail": "没有拿到场景提示词，无法验证"}
    problems = []
    for name, text in prompts.items():
        if not _SCALE_HINT.search(text):
            problems.append("%s 没写具体尺寸（米/层高）" % name)
        if sum(1 for w in _DEPTH_WORDS if w in text) < 3:
            problems.append("%s 纵深层次不足（要有前景/中景/背景与纵深）" % name)
        if not any(w in text for w in _WIDE_WORDS):
            problems.append("%s 没指定中广角/广角建立镜头" % name)
    return {"item": "场景提示词有真实尺度与纵深", "ok": not problems,
            "detail": "%d 个场景都写明了尺寸、纵深与广角建立" % len(prompts)
            if not problems else "；".join(problems[:3])}


CHECKS = CHECKS + [check_scene_prompt_scale]

# 两个字段通常写在同一句里：
# 「真实尺度：约 6×5 米，空间纵深：前景 1.5 米……」。
# 不能只按句号截断，否则第一个字段会把第二个字段一起吞掉，造成“空间纵深为空”的误报。
_FIELD_RE = re.compile(
    r"(真实尺度|空间纵深)[：:]\s*(.*?)"
    r"(?=(?:真实尺度|空间纵深)[：:]|[。\r\n]|$)"
)


def check_scene_prompt_fields(profile, art):
    """场景提示词的尺度/纵深必须非空，且不许串进别的场景的数据。

    历史事故：街道的提示词里写着「真实尺度：卧室约 5×4 米……前景床尾 1.5 米」，
    模型照做，在街上画了一张床。咖啡馆和卧室那两份则是空的。
    """
    prompts = {k: v for k, v in (art.get("image_prompts") or {}).items() if "scene" in k.lower()}
    if not prompts:
        return {"item": "场景尺度字段非空且不串场", "ok": False, "detail": "没有拿到场景提示词，无法验证"}
    names = [s_.get("name", "") for s_ in getattr(profile, "scenes", []) if s_.get("name")]
    problems = []
    for key, text in prompts.items():
        fields = dict(
            (m.group(1), (m.group(2) or "").strip(" \t\r\n，,；;"))
            for m in _FIELD_RE.finditer(text)
        )
        for label in ("真实尺度", "空间纵深"):
            if not fields.get(label):
                problems.append("%s 的「%s」是空的" % (key, label))
        own = [n for n in names if n and n in key]
        others = [n for n in names if n and n not in own]
        blob = " ".join(fields.values())
        for other in others:
            if other and other in blob:
                problems.append("%s 的尺度里串进了「%s」的数据" % (key, other))
    return {"item": "场景尺度字段非空且不串场", "ok": not problems,
            "detail": "%d 个场景尺度与纵深齐全且未串场" % len(prompts)
            if not problems else "；".join(problems[:3])}


CHECKS = CHECKS + [check_scene_prompt_fields]

def check_continuation_reference(profile, art):
    """无缝续接段必须带上一段结束帧；非续接段不许带。

    依据 2026-08-19 本机实测：不加结束帧，两段接口处光线会跳；加了就看不出接痕。
    规则同时防反向滥用——不连续的段硬塞结束帧会把上一段的场景/服装带进来。
    """
    cont = {}
    for s_ in profile.sequences:
        src = str(s_.get("continuity_source") or "")
        cont[s_["sequence_id"]] = ("continuation" in src) or ("续接" in src) or ("承接" in src)
    problems = []
    for s_ in art["ref_plan"]:
        sid = s_.get("sequence_id")
        roles = [p.get("role") for p in s_.get("pictures", [])]
        has_end = "previous_end_frame" in roles
        if cont.get(sid) and not has_end:
            problems.append("%s 是续接段却没带上一段结束帧（接口会跳光）" % sid)
        if not cont.get(sid, False) and has_end:
            problems.append("%s 不是续接段却带了结束帧" % sid)
    return {"item": "续接段带结束帧、非续接段不带", "ok": not problems,
            "detail": "参考包与连续性标记一致" if not problems else "；".join(problems[:3])}


CHECKS = CHECKS + [check_continuation_reference]

# ---------- 扩写保真：AI 只能补，不能改 ----------

# 从原文里抽"不许被改动"的东西：数字、引号内容、人名/专名（连续 2+ 汉字且非常见虚词）
_KEEP_NUM = re.compile(r"\d+(?:\.\d+)?")
_KEEP_QUOTE = re.compile(r"[「『\"'']([^」』\"'']{1,40})[」』\"'']")
_STOP = ("的", "了", "在", "和", "与", "是", "有", "这", "那", "他", "她", "它",
         "我", "你", "们", "个", "把", "被", "就", "都", "而", "但", "也", "还")


def source_facts(text):
    """原文里必须原样保留的事实碎片：数字、引号内容、专名。"""
    t = str(text or "")
    facts = set(_KEEP_NUM.findall(t)) | set(_KEEP_QUOTE.findall(t))
    for w in re.findall(r"[一-鿿]{2,6}", t):
        if w not in _STOP and not any(x == w for x in _STOP):
            facts.add(w)
    return facts


def lost_in_expansion(original, expanded, sample=None):
    """扩写后丢掉的原文事实。sample 用于限制检查规模（None=全查）。"""
    exp = str(expanded or "")
    facts = sorted(source_facts(original), key=len, reverse=True)
    if sample:
        facts = facts[:sample]
    return [f for f in facts if f not in exp]


def check_expansion_fidelity(profile, art):
    """AI 扩写用户输入时不许改原意：原文的数字、引号内容、专名必须逐字还在。

    用户明确要求：扩写只能补细节，不能改原文；原文不通顺也不改它的意思。
    """
    pairs = art.get("expansions") or []
    if not pairs:
        return {"item": "扩写未改动原文事实", "ok": True, "skipped": True,
                "detail": "本轮没有扩写内容，此项不适用"}
    problems = []
    for name, original, expanded in pairs:
        lost = lost_in_expansion(original, expanded)
        if lost:
            problems.append("%s 丢了原文里的：%s" % (name, "、".join(lost[:4])))
    return {"item": "扩写未改动原文事实", "ok": not problems,
            "detail": "%d 处扩写全部保留原文事实" % len(pairs)
            if not problems else "；".join(problems[:3])}


CHECKS = CHECKS + [check_expansion_fidelity]
