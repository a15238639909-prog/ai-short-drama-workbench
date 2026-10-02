# -*- coding: utf-8 -*-
"""comic_core.py — V4.1 Comic Production：Page Beat / Panel Plan / Krea / 排字 / PDF（clean-room，Pillow）。"""
import json, os, time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from cores import store
from cores import asset_core
from cores import quality_core

COMIC_ROOT = Path(__file__).resolve().parent.parent / "productions" / "comic"
FONT = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"

PAGE_SYSTEM = """你是漫画导演。把已采用的剧本（Events + Narrative Units）改编成 8-12 页竖版漫画的分页计划。
每页只承载一个主要变化，页之间必须有因果链。输出 JSON：
{"pages": [
  {"page": 1, "page_purpose": "这一页在故事里负责什么", "reader_takeaway": "读者看完必须知道/感受到什么",
   "state_change": "谁/什么发生什么变化", "key_moment": "关键瞬间",
   "event_ids": ["必须引用剧本里的 EVENT ID"], "page_result": "这一页结束时实际变化",
   "page_result_id": "R1", "caused_by_result_id": "上一页的 page_result_id（第1页为空）",
   "leads_to": "为什么引出下一页"}
]}
规则：所有 Core Event 必须至少被一页覆盖且不得重复执行；不得把后续剧情提前；不得出现无来源事件。只输出 JSON。"""

PANEL_SYSTEM = """你是漫画分镜。为给定的一页漫画输出 Panel Plan（画面布局），只输出 JSON：
{"panels": [
  {"panel": 1, "layout": "top", "desc": "画面内容（人物/动作/景别/机位/空间）", "dialog": "对白或空", "narration": "旁白或空"}
]}
每页 2-4 格；layout 取值 top/bottom-left/bottom-right/mid-left/mid-right。只输出 JSON。"""

def _extract_json(text):
    t = text.strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    a, b = t.find("{"), t.rfind("}")
    if a >= 0 and b > a:
        try:
            return json.loads(t[a:b + 1])
        except Exception:
            pass
    raise ValueError("模型输出不是 JSON")

def generate_page_beats(story, episode, target_pages=8, temperature=0.7):
    from models import qwen_client
    lines = []
    for e in episode["events"]:
        lines.append("%s [%s] %s：%s" % (e["event_id"], e["importance"], e["title"], e["desc"]))
    for u in episode["units"]:
        lines.append("Unit %s：%s → %s" % (u["unit_id"], u["purpose"], u["result"]))
    core_ids = {e["event_id"] for e in episode["events"] if e["importance"] == "core"}
    warnings = []
    last = None
    for attempt in range(1, 3):
        hint = "\n\n注意：上一版把同一 Core Event 写进了多个页面，请只在其真正发生的页面列出 event_ids；其他页面不得重复。"
        if last:
            hint += "\n上一版校验未通过：%s。请严格满足分页数量与因果链要求。" % last
        user = ("剧本事件：\n%s\n\n请改编成 %d 页漫画分页计划（必须是 %d 页，每页一个主要变化，页间因果链完整）。%s"
                % ("\n".join(lines), int(target_pages), int(target_pages), hint))
        raw = qwen_client.chat_for("comic_page_beat", PAGE_SYSTEM, user, temperature=temperature)
        d = _extract_json(raw)
        pages = d.get("pages") or []
        if not (8 <= len(pages) <= 12):
            last = "页数应为 8-12，实际 %d" % len(pages)
            if attempt == 1:
                continue
            raise ValueError(last)
        result_ids, ok = [], True
        for p in pages:
            rid = str(p.get("page_result_id") or "").strip()
            if rid and rid in result_ids:
                ok = False
                last = "page_result_id 重复：" + rid
                break
            result_ids.append(rid)
            cid = str(p.get("caused_by_result_id") or "").strip()
            if cid and cid not in result_ids[:-1]:
                ok = False
                last = "caused_by_result_id 未引用更早页：" + cid
                break
        if not ok:
            continue
        covered, dup = set(), []
        for p in pages:
            for eid in p.get("event_ids") or []:
                if eid in covered:
                    dup.append(eid)
                covered.add(eid)
        missing = core_ids - covered
        if dup:
            warnings.append("Core Event 被多页引用（按引用处理，非重复执行）：" + ",".join(sorted(set(dup))))
            if attempt == 1:
                last = "同一 Core Event 写进了多个页面：" + ",".join(sorted(set(dup)))
                continue
        if missing:
            if attempt == 1:
                last = "Core Event 未覆盖：" + ",".join(sorted(missing))
                continue
            raise ValueError("Core Event 未覆盖：" + ",".join(sorted(missing)))
        pages[0]["_warnings"] = warnings
        return pages
    raise ValueError("分页计划校验失败：" + str(last))

def generate_panel_plan(page, temperature=0.6):
    from models import qwen_client
    user = ("本页任务：\n目的：%s\n关键瞬间：%s\n事件：%s\n结果：%s\n\n请输出 Panel Plan。"
            % (page.get("page_purpose", ""), page.get("key_moment", ""),
               ",".join(page.get("event_ids") or []), page.get("page_result", "")))
    raw = qwen_client.chat_for("comic_panel", PANEL_SYSTEM, user, temperature=temperature)
    d = _extract_json(raw)
    panels = d.get("panels") or []
    if not (1 <= len(panels) <= 4):
        raise ValueError("Panel 数应为 1-4")
    return panels

def _box_layout(page_w, page_h, count):
    if count == 1:
        return [(0, 0, page_w, page_h)]
    if count == 2:
        return [(0, 0, page_w, page_h // 2), (0, page_h // 2, page_w, page_h)]
    if count == 3:
        return [(0, 0, page_w, page_h // 2), (0, page_h // 2, page_w // 2, page_h),
                (page_w // 2, page_h // 2, page_w, page_h)]
    return [(0, 0, page_w // 2, page_h // 2), (page_w // 2, 0, page_w, page_h // 2),
            (0, page_h // 2, page_w // 2, page_h), (page_w // 2, page_h // 2, page_w, page_h)]

def letter_page(img_path, out_path, panels, font_size=28):
    im = Image.open(img_path).convert("RGB")
    W, H = im.size
    draw = ImageDraw.Draw(im, "RGBA")
    boxes = _box_layout(W, H, len(panels))
    font = ImageFont.truetype(FONT, font_size)
    pad = max(10, W // 80)
    for i, p in enumerate(panels):
        x1, y1, x2, y2 = boxes[i]
        n = str(p.get("narration") or "").strip()
        d = str(p.get("dialog") or "").strip()
        if n:
            tw = min(x2 - x1 - 2 * pad, W // 2)
            lines = _wrap(draw, n, font, tw)
            line_h = font_size + 6
            bh = len(lines) * line_h + 2 * pad
            draw.rectangle([x1 + pad, y1 + pad, x1 + pad + tw + 2 * pad, y1 + pad + bh],
                           fill=(248, 240, 218, 235), outline=(90, 60, 30, 255), width=2)
            yy = y1 + pad * 2
            for ln in lines:
                draw.text((x1 + 2 * pad, yy), ln, font=font, fill=(30, 24, 16, 255))
                yy += line_h
        if d:
            tw = min(x2 - x1 - 2 * pad, W // 2)
            lines = _wrap(draw, d, font, tw)
            line_h = font_size + 6
            bh = len(lines) * line_h + 2 * pad
            bx, by = x1 + pad, y2 - pad - bh
            draw.rounded_rectangle([bx, by, bx + tw + 2 * pad, by + bh], radius=14,
                                   fill=(255, 255, 252, 240), outline=(20, 18, 15, 255), width=2)
            yy = by + pad
            for ln in lines:
                draw.text((bx + pad, yy), ln, font=font, fill=(20, 18, 15, 255))
                yy += line_h
    im.save(out_path)
    return out_path

def _wrap(draw, text, font, max_w):
    out, cur = [], ""
    for ch in str(text):
        if draw.textlength(cur + ch, font=font) > max_w and cur:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out

def compile_page_prompt(story, episode, page, panels, style="电影级写实"):
    scene_id = (story.get("scene_ids") or [None])[0]
    scene = asset_core.get_asset(story["story_id"], "scenes", scene_id) if scene_id else None
    chars = [asset_core.get_asset(story["story_id"], "characters", cid)
             for cid in (story.get("character_ids") or [])]
    chars = [c for c in chars if c]
    parts = [quality_core.style_profile(style)]
    if scene:
        parts.append("场景固定结构：" + str(scene.get("contract_text", "")))
    for c in chars:
        if c.get("identity_anchor"):
            parts.append("人物固定外貌（不得改动）：" + str(c["identity_anchor"]))
            if c.get("behavior_anchor"):
                parts.append("人物神态：" + str(c["behavior_anchor"]))
    panel_desc = "；".join("第%d格：%s" % (p.get("panel", i + 1), p.get("desc", "")) for i, p in enumerate(panels))
    parts.append("分格画面：" + panel_desc)
    parts.append("竖版整页漫画，2-4 格自适应布局，格间留白，画面干净，先画图不要写任何文字，不要水印")
    return "；".join(parts)

def build_pdf(page_paths, title, out_path):
    images = [Image.open(p).convert("RGB") for p in page_paths if os.path.exists(p)]
    if not images:
        raise ValueError("没有可合成的页面")
    images[0].save(out_path, save_all=True, append_images=images[1:])
    return out_path

def produce_comic(story, episode_no=1, target_pages=8, style="电影级写实"):
    """整话漫画生产：Page Beat → Panel Plan → Krea 整页 → 排字 → PDF（逐页 checkpoint）。"""
    from cores import narrative_core
    from models import krea_client
    ep = narrative_core.load_episode(story["story_id"], episode_no)
    if not ep or not ep.get("adopted"):
        raise ValueError("请先采用该话")
    title = re_sub(story.get("title") or "comic")
    outdir = COMIC_ROOT / (title + "_" + time.strftime("%Y%m%d_%H%M%S"))
    outdir.mkdir(parents=True, exist_ok=True)
    progress = {"pages": [], "files": []}
    pf = outdir / "progress.json"
    if pf.exists():
        progress = store.load_json(pf, {"pages": [], "files": []})
    if not progress.get("pages"):
        progress["pages"] = generate_page_beats(story, ep, int(target_pages))
    done = {os.path.basename(f) for f in progress.get("files", [])}
    for i, page in enumerate(progress["pages"]):
        page_file = outdir / ("page_%02d.png" % (i + 1))
        if page_file.name in done and page_file.exists():
            continue
        panels = generate_panel_plan(page)
        prompt = compile_page_prompt(story, ep, page, panels, style)
        res = krea_client.generate(prompt, width=896, height=1600)
        letter_page(res["output_path"], page_file, panels)
        progress["files"].append(str(page_file))
        store.save_json(pf, progress)
    pdf = outdir / "comic.pdf"
    build_pdf(progress["files"], story.get("title") or "comic", pdf)
    core_ids = {e["event_id"] for e in ep["events"] if e["importance"] == "core"}
    covered = set()
    for p in progress["pages"]:
        covered.update(p.get("event_ids") or [])
    rec = {"production_id": store.seq_id("COMIC", int(time.time())), "story_id": story["story_id"],
           "episode_no": int(episode_no), "pages": len(progress["pages"]),
           "images": progress["files"], "pdf": str(pdf),
           "event_coverage": sorted(core_ids & covered), "core_total": len(core_ids),
           "created": time.time()}
    store.save_json(store.DATA / "productions" / (rec["production_id"] + ".json"), rec)
    return rec

import re as _re
def re_sub(s):
    return _re.sub(r'[\\/:*?"<>|]', "", str(s or ""))[:40]
