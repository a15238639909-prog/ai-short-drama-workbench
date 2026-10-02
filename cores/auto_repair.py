# -*- coding: utf-8 -*-
"""自动修复：能程序修好的，就别弹给人看。

设计原则（用户定的）：**正常生产时尽量不出现拦截。出现拦截，说明上游没做好。**

三层处理，顺序固定：
1. **自动修**——规则明确、修法唯一的，直接改掉，只在生产单里留一行记录
2. **默认补**——缺了但能按类型给出合理默认的，补上并明确告知，用户可改
3. **才问人**——只有人能决定的（年龄设定、剧情走向），才提示

`cores/text_checks.py` 应该在本模块跑完之后再执行；那时还剩下的 FAIL，
才是真正需要人介入的。检查项报 FAIL 的数量，就是本模块还没覆盖到的缺口数量。
"""
import re

from cores.prompt_hygiene import positivize, sanitize_internal_ids

__all__ = ["repair_scene", "repair_prompt", "repair_reference_pack",
           "SCENE_DEFAULTS", "entity_of"]

# 按场景类型给的合理默认尺度（真实世界量级，避免模型出又小又平的盒子）
SCENE_DEFAULTS = {
    "街道": ("宽约 6 米，两侧建筑 3–4 层约 12 米高，可视纵深约 60 米",
             "近景路缘 2 米、中景街道 10–25 米、远景 60 米以外，三层空间平面清晰"),
    "室外": ("开阔场地约 30×20 米，可视纵深 80 米以上",
             "近景 2 米、中景 10–30 米、远景 80 米以外"),
    "大厅": ("约 20×12 米，层高约 8 米，可容纳数十人",
             "前景 3 米、中景 8–15 米、背景 20 米，纵深开阔"),
    "咖啡馆": ("店内约 9×6 米，层高约 3.4 米，可坐 20 余人",
               "前景吧台 1.5 米、中景桌区 4–7 米、背景窗外 15 米以外"),
    "卧室": ("约 5×4 米，层高约 2.8 米，人物站立不顶天花",
             "前景 1.5 米、中景 3 米、背景 5 米，纵深真实不压缩"),
    "室内": ("约 6×5 米，层高约 3 米，人物站立不顶天花",
             "前景 1.5 米、中景 3–4 米、背景 6 米"),
}
_DEFAULT_KEY = "室内"

_ENTITY_SUFFIX = re.compile(r"(_CROP|_FACE|_HALF|_V\d+|_v\d+|\s*\(.*?\)|\s*（.*?）)+$")


def entity_of(source):
    """参考图来源归一到实体：Master 与它的裁图、不同版本，都是同一个实体。"""
    t = str(source or "").strip()
    prev = None
    while t != prev:
        prev = t
        t = _ENTITY_SUFFIX.sub("", t).strip()
    return t


def _guess_kind(name):
    for key in SCENE_DEFAULTS:
        if key in str(name or ""):
            return key
    return _DEFAULT_KEY


def repair_scene(scene, all_scene_names=()):
    """修场景数据：尺度/纵深缺失就补默认；串了别的场景的数据就清掉重补。

    返回 (修好的 scene, [改动说明])。
    """
    s = dict(scene or {})
    notes = []
    name = s.get("name") or s.get("scene_id") or ""
    kind = _guess_kind(name)
    d_scale, d_depth = SCENE_DEFAULTS[kind]

    others = [n for n in all_scene_names if n and n != name and n not in str(name)]
    for field, default, label in (("scale", d_scale, "真实尺度"),
                                  ("depth", d_depth, "空间纵深")):
        val = str(s.get(field) or "").strip()
        # 串场：这个字段里提到了别的场景的名字
        hit = [o for o in others if o and o in val]
        if hit:
            notes.append("%s 的「%s」原本写着「%s」的数据，已按%s类型重新补写" % (name, label, hit[0], kind))
            val = ""
        if not val:
            s[field] = default
            if not hit:
                notes.append("%s 缺「%s」，已按%s类型补默认值（可改）" % (name, label, kind))
        else:
            s[field] = val
    return s, notes


def repair_prompt(text, id_map=None):
    """修提示词：否定式转正向、内部 ID 换成自然称呼。返回 (文本, [改动说明])。"""
    notes = []
    t = str(text or "")
    if id_map:
        before = t
        t = sanitize_internal_ids(t, id_map)
        if t != before:
            notes.append("已把内部编号换成自然称呼")
    t, extracted = positivize(t)
    if extracted:
        notes.append("已把否定式改成正向表述，排除项交负向词：%s" % "、".join(extracted))
    return t, notes


def repair_reference_pack(pictures, is_continuation=False):
    """修参考包：同一实体只留一张、场景只留一张、非续接段去掉结束帧。

    返回 (修好的 pictures, [改动说明])。
    """
    out, seen, notes = [], set(), []
    scene_used = False
    for p in (pictures or []):
        role = p.get("role")
        ent = entity_of(p.get("source"))
        if ent in seen:
            notes.append("参考图里「%s」重复出现，已去掉多余的一张" % ent)
            continue
        if role == "scene_master":
            if scene_used:
                notes.append("出现第二张场景图「%s」，已去掉（当前地点只能有一个）" % ent)
                continue
            scene_used = True
        if role == "previous_end_frame" and not is_continuation:
            notes.append("这一段不是无缝续接，已去掉上一段结束帧（避免把上一段的场景服装带进来）")
            continue
        seen.add(ent)
        out.append(p)
    # 编号重排，保持 Picture 1..N 连续
    for i, p in enumerate(out, 1):
        p["picture"] = "Picture %d" % i
    return out, notes
