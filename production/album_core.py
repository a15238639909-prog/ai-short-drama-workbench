# -*- coding: utf-8 -*-
"""album_core.py — V4.1 Album Production：规划 / 样片 / 批量 / PDF（clean-room）。"""
import json, re, time
from pathlib import Path
from PIL import Image
from cores import store
from cores import asset_core
from cores import quality_core

ALBUM_ROOT = Path(__file__).resolve().parent.parent / "productions" / "album"

ALBUM_SYSTEM = """你是画册策划。根据主题与故事资产规划一组风格统一的画册图片。
只输出 JSON：{"album_name":"系列名","series_bible":{"subjectLock":"","worldLock":"","wardrobeLock":"","paletteLock":"","materialLock":"","cameraLock":"","styleLock":""},
"images":[{"index":1,"title":"短标题","subject":"主体","action":"动作","environment":"环境","light":"光线","composition":"构图"}]}
图片数量必须等于用户指定。固定人物写真：subject 必须引用人物固定外貌与服装。只输出 JSON。"""

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

def plan_album(story, theme, count, kind="cinematic", temperature=0.7):
    from models import qwen_client
    chars = "；".join((c.get("name") or "") + "：" + "，".join(x for x in [c.get("look"), c.get("clothing")] if x)
                      for c in [asset_core.get_asset(story["story_id"], "characters", cid)
                                for cid in (story.get("character_ids") or [])] if c)
    scene = None
    if story.get("scene_ids"):
        scene = asset_core.get_asset(story["story_id"], "scenes", story["scene_ids"][0])
    user = ("主题：%s\n类型：%s\n故事：%s\n世界：%s\n人物：%s\n场景：%s\n\n请规划 %d 张画册。"
            % (theme, kind, story.get("one_line", ""), "、".join(story.get("world_rules") or []),
               chars or "无", (scene.get("contract_text", "") if scene else ""), int(count)))
    raw = qwen_client.chat_for("album_plan", ALBUM_SYSTEM, user, temperature=temperature)
    d = _extract_json(raw)
    imgs = d.get("images") or []
    if len(imgs) != int(count):
        raise ValueError("图片数量应为 %d" % int(count))
    return d

def _compile_album_prompt(item, bible, style, char_anchor=""):
    parts = [quality_core.style_profile(style)]
    parts.append("系列锁定：" + json.dumps(bible, ensure_ascii=False))
    if char_anchor:
        parts.append("人物固定外貌（不得改动）：" + char_anchor)
    parts.append("主体：" + str(item.get("subject", "")))
    if item.get("action"):
        parts.append("动作：" + str(item["action"]))
    if item.get("environment"):
        parts.append("环境：" + str(item["environment"]))
    if item.get("light"):
        parts.append("光线：" + str(item["light"]))
    if item.get("composition"):
        parts.append("构图：" + str(item["composition"]))
    return "；".join(parts)

def produce_album(story, theme, count=3, style="电影级写实", kind="cinematic", task=None):
    from models import krea_client
    plan = plan_album(story, theme, int(count), kind)
    title = re.sub(r'[\\/:*?"<>|]', "", plan.get("album_name") or "album")
    outdir = ALBUM_ROOT / (title + "_" + time.strftime("%Y%m%d_%H%M%S"))
    outdir.mkdir(parents=True, exist_ok=True)
    char_anchor = ""
    if kind == "character_photography" and story.get("character_ids"):
        c = asset_core.get_asset(story["story_id"], "characters", story["character_ids"][0])
        if c:
            char_anchor = c.get("identity_anchor") or ""
    images = []
    for idx, item in enumerate(plan["images"]):
        if task:
            task.check_pause()
            task.step("画册图片", idx, len(plan["images"]))
        prompt = _compile_album_prompt(item, plan.get("series_bible", {}), style, char_anchor)
        res = krea_client.generate(prompt, width=1024, height=1024)
        dst = outdir / ("%02d.png" % int(item.get("index", 1)))
        import shutil
        shutil.copy2(res["output_path"], str(dst))
        images.append(str(dst))
        plan["_prompt_" + str(item.get("index"))] = prompt
    pdf = outdir / "album.pdf"
    imgs = [Image.open(p).convert("RGB") for p in images]
    imgs[0].save(pdf, save_all=True, append_images=imgs[1:])
    rec = {"production_id": store.seq_id("ALBUM", int(time.time())), "story_id": story["story_id"],
           "name": plan.get("album_name", ""), "kind": kind, "images": images, "pdf": str(pdf),
           "bible": plan.get("series_bible", {}), "prompts": {k: v for k, v in plan.items() if k.startswith("_prompt_")},
           "created": time.time()}
    store.save_json(store.DATA / "productions" / (rec["production_id"] + ".json"), rec)
    return rec
