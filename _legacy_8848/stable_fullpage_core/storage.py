# -*- coding: utf-8 -*-
"""stable_fullpage_core 存储：读写剧本JSON与画风预设（兼容前端script_load）。
旧项目导入只用于测试回归，不进入正式生产链。"""
import os, re, json, time, shutil

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.join(_APP_DIR, "剧本")
STYLE_DIR = os.path.join(_APP_DIR, "comic-style-presets")


def _safe(name):
    return re.sub(r'[\\/:*?"<>|\r\n]+', "_", str(name or "未命名")).strip()[:80]


def _clip_whole_sentences(text, limit):
    """按整句裁剪到limit以内，禁止从句子中间截断。"""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    parts = [p.strip("，,、；;。 ")
             for p in re.split(r"(?<=[。；;])", text) if p.strip()]
    out = ""
    for p in parts:
        if len(out) + len(p) + 1 > limit:
            break
        out += (p + "。") if out else p + "。"
    return out.strip("。") + ("。" if out else "")


def save_stable_script(script):
    name = script.get("name") or script.get("title") or "未命名"
    p = os.path.join(SCRIPT_DIR, _safe(name) + ".json")
    script["name"] = _safe(name)
    script["directorVersion"] = "stable_fullpage_core_v1"
    script["originalPipeline"] = script.get("originalPipeline") or "stable_fullpage_core_v1"
    script["currentPipeline"] = "stable_fullpage_core_v1"
    script["lastRenderedPipeline"] = script.get("lastRenderedPipeline") or ""
    script["updatedAt"] = time.time()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)
    shutil.move(tmp, p)
    return p


def load_stable_script(name):
    p = os.path.join(SCRIPT_DIR, _safe(name) + ".json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_style_presets():
    out = {}
    if not os.path.isdir(STYLE_DIR):
        return out
    for fn in sorted(os.listdir(STYLE_DIR)):
        if fn.endswith(".txt"):
            p = os.path.join(STYLE_DIR, fn)
            out[os.path.splitext(fn)[0]] = open(p, encoding="utf-8").read().strip()
    return out


def import_legacy_page_plan(page):
    """把旧项目页面（panels）转换为stable整页画面计划（只读转换，测试回归用）。
    字段按整句裁剪，不从句子中间截断。"""
    panels = []
    for p in (page.get("panels") or []):
        if not isinstance(p, dict):
            continue
        panels.append({
            "shot": str(p.get("shot") or "中景"),
            "subject": _clip_whole_sentences(
                str(p.get("subject") or p.get("visible") or ""), 60),
            "people": [str(x) for x in (p.get("people") or [])],
            "positions": _clip_whole_sentences(str(p.get("positions") or ""), 80),
            "action": _clip_whole_sentences(str(p.get("action") or ""), 120),
            "expression": _clip_whole_sentences(str(p.get("expression") or ""), 80),
            "visibleOutfitOrState": _clip_whole_sentences(
                str(p.get("visibleOutfitOrState") or ""), 120),
            "importantObject": _clip_whole_sentences(
                str(p.get("importantObject") or ""), 80),
            "visible": _clip_whole_sentences(str(p.get("visible") or ""), 120),
            "environment": _clip_whole_sentences(
                str(p.get("staging_background") or p.get("environment") or ""), 120),
            "interaction": _clip_whole_sentences(
                str(p.get("interaction") or ""), 80),
            "storyPurpose": _clip_whole_sentences(
                str(p.get("storyPurpose") or ""), 60),
        })
    return {
        "layout": str(page.get("layout") or "") or
                  ("hero+2" if len(panels) == 3 else
                   ("hero+strip" if len(panels) == 2 else "hero")),
        "characters": [str(c.get("name") or "")
                       for c in (page.get("characters") or [])],
        "scene": str(page.get("scene") or ""),
        "light": str(page.get("light") or ""),
        "panels": panels,
        "diegeticTextItems": page.get("diegeticTextItems") or [],
        "currentPageVisibleFacts": page.get("currentPageVisibleFacts") or [],
        "futureRevealFacts": page.get("futureRevealFacts") or [],
    }


def import_legacy_text_plan(text_units):
    units = []
    for u in (text_units or []):
        if not isinstance(u, dict):
            continue
        t = str(u.get("visibleText") or u.get("text") or "").strip()
        t = re.sub(r"^[^：:]{1,14}[：:]\s*", "", t)
        if not t:
            continue
        units.append({
            "type": str(u.get("type") or "narration"),
            "speakerId": str(u.get("speakerId") or ""),
            "visibleText": t, "text": t,
            "panel": max(1, int(u.get("panel") or 1)),
            "placementPreference": str(u.get("placementPreference") or "topLeft"),
        })
    return units
