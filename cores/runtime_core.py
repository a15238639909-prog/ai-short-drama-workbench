# -*- coding: utf-8 -*-
"""runtime_core.py — V4.1 Runtime Core：TaskCenter / GPU 游戏模式 / 存储 / 归档 / 问题。"""
import json, os, shutil, threading, time, uuid, zipfile
from pathlib import Path
from . import store

TASKS_DIR = store.DATA / "tasks"
MANIFESTS_DIR = store.DATA / "manifests"
PRODUCTIONS_DIR = store.DATA / "productions"
TASKS_DIR.mkdir(parents=True, exist_ok=True)
PRODUCTIONS_DIR.mkdir(parents=True, exist_ok=True)

STATUSES = ("draft", "queued", "running", "waiting_user", "paused",
            "interrupted", "failed", "completed", "cancelled")

class TaskPaused(Exception):
    pass

def _task_path(tid):
    return TASKS_DIR / (tid + ".json")

def save(t):
    store.save_json(_task_path(t["id"]), t)

def load(tid):
    return store.load_json(_task_path(tid), None)

def list_tasks(limit=50):
    out = []
    for p in TASKS_DIR.glob("task_*.json"):
        t = store.load_json(p, None)
        if t:
            out.append(t)
    return sorted(out, key=lambda x: x.get("created", 0), reverse=True)[:max(1, int(limit))]

def _restore():
    for t in list_tasks(200):
        if t.get("status") in ("running", "queued"):
            t["status"] = "interrupted"
            t["error"] = "服务重启，任务中断；可恢复"
            save(t)

class TaskHandle:
    """传给生产函数的任务句柄：步骤/心跳/暂停/等待用户/检查点。"""
    def __init__(self, tid):
        self.tid = tid
        self._lock = threading.Lock()

    def _get(self):
        return load(self.tid)

    def set(self, **kw):
        with self._lock:
            t = load(self.tid) or {"id": self.tid}
            t.update(kw)
            t["updated"] = time.time()
            if "heartbeat" not in kw:
                t["heartbeat"] = time.time()
            save(t)

    def step(self, label, done, total, detail=""):
        self.set(step_label=label, done=int(done), total=int(total), detail=str(detail),
                 heartbeat=time.time())

    def waiting(self, detail=""):
        self.set(status="waiting_user", detail=detail, heartbeat=time.time())

    def check_pause(self):
        t = self._get() or {}
        if t.get("status") == "paused":
            raise TaskPaused()
        if t.get("status") == "cancelled":
            raise TaskPaused()
        return True

    def interrupted(self):
        return (self._get() or {}).get("status") in ("interrupted", "paused", "cancelled")

def action(tid, act):
    t = load(tid)
    if not t:
        return None
    st = t.get("status")
    if act == "pause" and st in ("running", "queued", "waiting_user"):
        t["status"] = "paused"; t["detail"] = "已暂停（检查点已保存）"; save(t)
    elif act == "resume" and st in ("paused", "interrupted", "failed"):
        t["status"] = "queued"; t["error"] = ""; save(t)
        _queue(tid)
    elif act == "retry" and st in ("failed", "cancelled"):
        t["status"] = "queued"; t["error"] = ""; save(t)
        _queue(tid)
    elif act == "cancel" and st not in ("completed", "cancelled"):
        t["status"] = "cancelled"; t["detail"] = "已取消"; save(t)
    return t

_QUEUE = []
_CV = threading.Condition()

def _queue(tid):
    with _CV:
        _QUEUE.append(tid)
        _CV.notify_all()

def create_task(kind, title, inputs, steps=None):
    tid = store.seq_id("task", len(list(TASKS_DIR.glob("task_*.json"))) + 1)
    t = {"id": tid, "kind": kind, "title": title, "status": "queued",
         "inputs": inputs, "steps": steps or [], "checkpoint": {},
         "heartbeat": time.time(), "created": time.time(), "updated": time.time(),
         "error": "", "output": None, "detail": "排队中"}
    save(t)
    _queue(tid)
    return t

def _run(tid):
    t = load(tid)
    if not t or t.get("status") != "queued":
        return
    t["status"] = "running"; t["started"] = time.time(); save(t)
    h = TaskHandle(tid)
    try:
        runner = RUNNERS.get(t.get("kind"))
        if not runner:
            raise RuntimeError("未知任务类型：" + str(t.get("kind")))
        out = runner(h, t)
        t = load(tid) or t
        t["status"] = "completed"; t["output"] = out; t["ended"] = time.time(); t["detail"] = "完成"
        save(t)
    except TaskPaused:
        t = load(tid) or t
        t["status"] = "paused"; t["detail"] = "已暂停"; t["ended"] = time.time(); save(t)
    except Exception as e:
        t = load(tid) or t
        t["status"] = "failed"; t["error"] = str(e)[:800]; t["ended"] = time.time(); save(t)

def worker_loop():
    while True:
        with _CV:
            while not _QUEUE:
                _CV.wait()
            tid = _QUEUE.pop(0)
        _run(tid)

def start_worker():
    _restore()
    threading.Thread(target=worker_loop, daemon=True).start()

# ---- 生产 Runner（把长链接入 TaskCenter） ----
def _run_comic(h, t):
    from production import comic_core
    from cores import story_core, narrative_core, project_settings
    story = story_core.get_story(t["inputs"]["story_id"])
    if not story:
        raise RuntimeError("故事不存在：" + str(t["inputs"].get("story_id")))
    ps = project_settings.get(story)
    ep = narrative_core.load_episode(story["story_id"], int(t["inputs"].get("episode_no") or 1))
    pages = comic_core.generate_page_beats(story, ep, int(t["inputs"].get("target_pages") or 8))
    h.step("分页计划", 0, len(pages))
    total = len(pages)
    files = []
    for i, page in enumerate(pages):
        h.check_pause()
        h.step("整页漫画", i, total)
        panels = comic_core.generate_panel_plan(page)
        prompt = comic_core.compile_page_prompt(story, ep, page, panels,
                                                t["inputs"].get("style") or project_settings.style_of(story))
        from models import krea_client
        res = krea_client.generate(prompt, width=896, height=1600)
        outdir = Path(t["inputs"].get("outdir") or (comic_core.COMIC_ROOT / ("task_" + t["id"])))
        outdir.mkdir(parents=True, exist_ok=True)
        page_file = outdir / ("page_%02d.png" % (i + 1))
        comic_core.letter_page(res["output_path"], page_file, panels)
        files.append(str(page_file))
        t["checkpoint"] = {"done": i + 1}
        save(t)
    pdf = outdir / "comic.pdf"
    comic_core.build_pdf(files, story.get("title") or "comic", pdf)
    rec = {"production_id": store.seq_id("COMIC", int(time.time())), "story_id": story["story_id"],
           "episode_no": int(t["inputs"].get("episode_no") or 1), "pages": len(files), "pdf": str(pdf),
           "images": files,
           "project_style": ps.get("style", ""),
           "event_coverage": sorted({e["event_id"] for e in ep["events"] if e["importance"] == "core"} & set(
               eid for p in pages for eid in (p.get("event_ids") or []))),
           "core_total": len([e for e in ep["events"] if e["importance"] == "core"]), "created": time.time()}
    store.save_json(PRODUCTIONS_DIR / (rec["production_id"] + ".json"), rec)
    return rec

def _run_novel(h, t):
    from production import novel_core
    from cores import story_core, project_settings
    story = story_core.get_story(t["inputs"]["story_id"])
    if not story:
        raise RuntimeError("故事不存在：" + str(t["inputs"].get("story_id")))
    ps = project_settings.get(story)
    rec = novel_core.produce_novel(story, int(t["inputs"].get("episode_no") or 1),
                                   int(t["inputs"].get("chapter_count") or 10), task=h)
    rec["project_style"] = ps.get("style", "")
    return rec

def _run_album(h, t):
    from production import album_core
    from cores import story_core, project_settings
    story = story_core.get_story(t["inputs"]["story_id"])
    if not story:
        raise RuntimeError("故事不存在：" + str(t["inputs"].get("story_id")))
    return album_core.produce_album(story, str(t["inputs"].get("theme") or ""),
                                    int(t["inputs"].get("count") or 3),
                                    str(t["inputs"].get("style") or project_settings.style_of(story)),
                                    str(t["inputs"].get("kind") or "cinematic"), task=h)

def _run_video_formal(h, t):
    from production import video_core
    from production import video_plan
    from cores import story_core, narrative_core, project_settings, project_prompt
    story = story_core.get_story(t["inputs"]["story_id"])
    if not story:
        raise RuntimeError("故事不存在：" + str(t["inputs"].get("story_id")))
    ps = project_settings.get(story)
    ep_no = int(t["inputs"].get("episode_no") or 1)
    target = int(t["inputs"].get("target_seconds") or 30)
    ep = narrative_core.load_episode(story["story_id"], ep_no)
    if not ep or not ep.get("adopted"):
        raise RuntimeError("请先采用该话")
    units = ep["units"]
    n = max(1, min(len(units), max(2, int(round(target / 15)))))
    per = max(8, min(15, int(round(target / n))))
    seq_files, seq_meta = [], []
    prev_end = None
    trace_seqs = []
    for i, unit in enumerate(units[:n]):
        h.check_pause()
        h.step("视频序列", i, n)
        chars = list(story.get("character_ids") or [])
        scene = (story.get("scene_ids") or [None])[0]
        plan = video_plan.build_sequence_plan(story, ep, unit, i + 1)
        brief = plan["director_brief"]
        shots_txt = "；".join("镜%s：%s（%s）" % (s.get("shot_id"), s.get("narrative_purpose", ""), s.get("cut_reason", ""))
                              for s in brief.get("shots", []))
        prompt = ("%s。关键瞬间：%s。结果：%s。%s。人物外观与场景固定结构不得改变。"
                  "导演方案：%s。镜头计划：%s。" % (unit.get("purpose", ""), unit.get("key_moment", ""),
                                               unit.get("result", ""), project_prompt.video_visual_block(ps),
                                               brief.get("story_script", ""), shots_txt))
        take = video_core.produce_sequence(story["story_id"], unit, chars, scene, prompt, per,
                                           mode="ref2va", previous_end_state=prev_end, take=i + 1)
        seq_files.append(take["path"])
        seq_meta.append({"unit_id": unit.get("unit_id"), "take": take["take_id"],
                         "pack": take["pack"]["reference_pack_id"], "seconds": per,
                         "event_ids": plan["event_ids"], "director_id": plan["director_id"],
                         "state_snapshot_id": plan["state_snapshot_id"]})
        trace_seqs.append({**plan, "take_id": take["take_id"],
                           "reference_pack_id": take["pack"]["reference_pack_id"],
                           "output": take["path"], "seconds": per})
        if i + 1 < n:
            frame_png = str(Path(__file__).resolve().parent.parent / "cache" / ("end_frame_%s_%d.png" % (story["story_id"], i + 1)))
            prev_end, _ = video_plan.extract_end_frame(take["path"], frame_png)
        t["checkpoint"] = {"done": i + 1}
        save(t)
    h.step("拼接", n, n)
    merged = video_core.concat_sequences(seq_files, target, story.get("title") or "video")
    try:
        video_plan.write_trace(story["story_id"], trace_seqs)
    except Exception:
        pass
    rec = {"production_id": store.seq_id("VIDEO", int(time.time())), "story_id": story["story_id"],
           "episode_no": int(ep_no), "sequences": seq_meta, "output": merged, "created": time.time(),
           "project_style": ps.get("style", "")}
    store.save_json(PRODUCTIONS_DIR / (rec["production_id"] + ".json"), rec)
    return rec

RUNNERS = {"comic": _run_comic, "novel": _run_novel, "album": _run_album, "video_formal": _run_video_formal}

def _run_shell_probe(h, t):
    """唯一职责：外壳验收用的真实步骤任务（写检查点文件，不伪造百分比）。"""
    total = int(t["inputs"].get("steps") or 6)
    outdir = Path(__file__).resolve().parent.parent / "cache" / "shell_probe" / t["id"]
    outdir.mkdir(parents=True, exist_ok=True)
    for i in range(1, total + 1):
        h.check_pause()
        h.step("探测步骤", i, total, "写入检查点 %d/%d" % (i, total))
        (outdir / ("step_%02d.txt" % i)).write_text(
            "real probe step %d at %s" % (i, time.strftime("%H:%M:%S")), encoding="utf-8")
        time.sleep(1)
    h.step("完成", total, total, "检查点文件：" + str(outdir))
    return {"steps": total, "outdir": str(outdir)}

RUNNERS["shell_probe"] = _run_shell_probe

# ================= 素材轮执行器（独占显存，全部走 TaskCenter） =================

def _vram_peak_mb():
    """唯一负责：读当前 nvidia-smi 显存占用（失败返回 -1）。"""
    try:
        import subprocess
        out = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used",
                                       "--format=csv,noheader,nounits"], timeout=8)
        return int(out.decode("utf-8", "replace").strip().splitlines()[0].strip())
    except Exception:
        return -1


def _ensure_story_004():
    """唯一负责：素材轮专用故事 STORY_004（老城一夜）及其人物/场景草稿。"""
    from cores import story_core, asset_core
    sid = "STORY_004"
    story = story_core.get_story(sid)
    if not story:
        story = story_core.create_story(
            "两个陌生人在欧洲老城的一夜：街头一瞥、咖啡馆交谈，深夜卧室里外套脱下后依然延续的亲密与安静。",
            title="老城一夜", mode="short")
    chars = [("CHAR_林舟", "林舟", "28", "男", "独自旅行的亚洲青年"),
             ("CHAR_艾莉莎", "艾莉莎", "20", "女", "住在老城的欧洲女孩")]
    for cid, name, age, sex, ctype in chars:
        if not asset_core.get_asset(sid, "characters", cid):
            asset_core.create_character(sid, {"name": name, "age": age, "sex": sex, "char_type": ctype})
    scenes = [("SCENE_老城街道", "老城街道"), ("SCENE_老城咖啡馆", "老城咖啡馆"), ("SCENE_卧室", "卧室")]
    for sid_, name in scenes:
        if not asset_core.get_asset(sid, "scenes", sid_):
            asset_core.create_scene(sid, {"name": name})
    return sid


def _register_visual(story_id, kind, owner_id, version, path, prompt, adopt_now=True):
    """唯一负责：把素材轮生成图登记进资产库并采用（旧版自动 superseded）。"""
    from cores import asset_core
    v = asset_core.add_approved_visual(story_id, kind, owner_id, version, str(path),
                                       style_profile="电影级写实", prompt=prompt)
    if adopt_now:
        v = asset_core.adopt(story_id, v["visual_id"])
    return v


def _run_su_cai_scenes(h, t):
    """素材轮第一步：按官方正向词重出 3 张场景 Master（1920x1080，失败回退 1280x720），登记并采用。"""
    import json, time as _t
    from pathlib import Path as _P
    from cores import asset_core
    from models import krea_client
    ROOT = _P(__file__).resolve().parent.parent
    date = _t.strftime("%Y%m%d")
    outdir = ROOT / "productions" / ("素材轮_" + date) / "scenes"
    outdir.mkdir(parents=True, exist_ok=True)
    src = ROOT / "reports" / "v423_text_final" / "final_review" / "simplified_workflow" / "04_krea_prompts_official"
    common = ("文字, 招牌文字, 门牌文字, 乱码文字, 水印, 编号, 多余人物, 行人, 路人, 人群, 人影, "
              "复制人, 多肢体, 噪点, 颗粒, 模糊")
    jobs = [
        ("SCENE_老城街道", "scene1_street_master.txt", common + ", 车辆, 汽车, 现代元素", 20260830),
        ("SCENE_老城咖啡馆", "scene2_cafe_master.txt", common + ", 菜单文字, 告示文字, 黑板文字, 现代城市", 20260831),
        ("SCENE_卧室", "scene3_bedroom_master.txt", common + ", 第二张床, 双床, 前景床铺", 20260832),
    ]
    sid = _ensure_story_004()
    records = []
    for i, (scene_id, prompt_file, neg, seed) in enumerate(jobs, 1):
        h.check_pause()
        h.step("场景图", i - 1, len(jobs), scene_id)
        prompt = (src / prompt_file).read_text(encoding="utf-8")
        t0 = _t.time()
        res = None
        for w, hh in ((1920, 1080), (1280, 720)):
            try:
                res = krea_client.generate(prompt, negative=neg, width=w, height=hh,
                                           seed=seed, steps=30, cfg=1.2)
                break
            except Exception as e:
                log_hint = str(e)[:200]
        if res is None:
            raise RuntimeError("场景 %s 生成失败：%s" % (scene_id, log_hint))
        png = outdir / (scene_id + ".png")
        shutil.copy2(res["output_path"], str(png))
        v = _register_visual(sid, "scene_master", scene_id, 1, png, prompt)
        rec = {"scene_id": scene_id, "asset_id": v["visual_id"], "status": v["status"],
               "file": str(png), "prompt": prompt, "negative": neg,
               "params": {"width": res.get("width", 1920), "height": res.get("height", 1080),
                          "steps": 30, "cfg": 1.2, "seed": seed},
               "elapsed_s": round(_t.time() - t0, 1)}
        records.append(rec)
        h.step("场景图", i, len(jobs), scene_id + " 完成")
    # 登记既有角色 Master（供视频参考用）
    for cid, fname, prompt_file in (("CHAR_林舟", "KREA_CHAR_A_MASTER.png", "character_A_master.txt"),
                                    ("CHAR_艾莉莎", "KREA_CHAR_B_MASTER.png", "character_B_master.txt")):
        p = ROOT / "productions" / "v423_t3" / "assets" / fname
        if p.exists():
            prompt = (src / prompt_file).read_text(encoding="utf-8")
            _register_visual(sid, "character_master", cid, 1, p, prompt)
    (outdir / "scene_records.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"story_id": sid, "scenes": records}


def _compile_seq_b_prompt():
    """唯一负责：确定性编译 SEQ_B（床沿延续段）H3 六段式提示词，thinking OFF。"""
    from cores.story_profile import StoryProfile
    from cores import h3_format, prompt_hygiene
    ROOT = Path(__file__).resolve().parent.parent
    fr = ROOT / "reports" / "v423_text_final" / "final_review"
    profile = StoryProfile.load(fr)
    subj_defs = []
    for i, c in enumerate(profile.characters, 1):
        subj_defs.append("<Subject %d> 是%s人物 Master 中的%s，保留其脸型、发型、身材与固定识别特征。"
                         % (i, c["name"], c["name"]))
    sc = profile.scenes[2]
    subj_defs.append("%s 是%s场景 Master 中的%s空间，保留其结构、材质、台灯与光线方向。"
                     % (profile.scene_subject_tag, sc["name"], sc["name"]))
    names = profile.role_names
    subj_map = profile.role_subjects
    shots = [
        {"shot_id": "S10", "duration": 5, "framing": "中景(MS)", "narrative_purpose": "两人并肩坐在床沿，安静延续",
         "movement_path": "缓推", "movement_speed": "慢速", "movement_end_point": "二人面部",
         "camera_position": "床尾方向", "camera_height": "平视", "camera_angle": "中性", "camera_distance": "3m",
         "optics": "50mm", "focus_subject": "双人", "depth_of_field": "中景深",
         "foreground": "虚化床尾", "midground": "并肩坐床沿的两人", "background": "卧室墙面与台灯暖光",
         "A_action": "坐在床沿，转头看B", "A_performance": "安静、放松，目光柔和",
         "B_action": "坐在床沿，靠向A肩侧", "B_performance": "依偎、疲惫后的松弛",
         "blocking": "并肩坐床沿，身体微靠", "eyeline": "互视后看向窗外",
         "dominant_light": "床头台灯暖黄", "subject_light_side": "主光来自床头左侧",
         "audio": "安静呼吸、窗外远处电车声", "dialogue": ""},
        {"shot_id": "S11", "duration": 5, "framing": "特写(CU)", "narrative_purpose": "无言的身体靠近与触碰",
         "movement_path": "微推", "movement_speed": "极慢", "movement_end_point": "B侧脸与A的手",
         "camera_position": "过A肩", "camera_height": "平视", "camera_angle": "中性", "camera_distance": "1.2m",
         "optics": "85mm", "focus_subject": "B侧脸", "depth_of_field": "浅焦",
         "foreground": "虚化A肩", "midground": "B侧脸与A的手", "background": "暖光虚化",
         "A_action": "伸手轻触B的发梢与肩线", "A_performance": "温柔、克制",
         "B_action": "微微侧头，闭眼", "B_performance": "安心、放松",
         "blocking": "A手触B肩，B靠向A", "eyeline": "B闭眼，A注视",
         "dominant_light": "台灯暖黄轮廓光", "subject_light_side": "光从B左后方来",
         "audio": "衣料轻响、呼吸", "dialogue": ""},
        {"shot_id": "S12", "duration": 5, "framing": "全景(WS)", "narrative_purpose": "剪影收束，夜色延续",
         "movement_path": "缓摇", "movement_speed": "慢速", "movement_end_point": "窗与月光",
         "camera_position": "房间对角", "camera_height": "贴地", "camera_angle": "微仰", "camera_distance": "6m",
         "optics": "24mm", "focus_subject": "双人剪影", "depth_of_field": "深焦",
         "foreground": "虚化地毯", "midground": "床沿相依的剪影", "background": "窗帘与月光",
         "A_action": "搂住B肩，一同望向窗外", "A_performance": "沉稳、安定",
         "B_action": "靠入A怀中，看向月光", "B_performance": "安静、信任",
         "blocking": "相依坐床沿，面向窗", "eyeline": "同看窗外",
         "dominant_light": "月光+台灯溢光", "subject_light_side": "逆光剪影",
         "audio": "环境声渐静、布料窸窣", "dialogue": ""},
    ]
    paragraphs = []
    for idx, s in enumerate(shots):
        paragraphs.append(h3_format.shot_paragraph(s, idx, idx * 5.0, subj_map, names, dialogue=[]))
    paragraphs.append("连续性硬约束：两人外套均已脱下且全程保持脱下状态，任何镜头不得恢复；"
                      "位置始终在床边，不回到门口；同一卧室、同一台灯、同一光线方向。")
    style_open = "写实真人电影风格；暖色调为主，暗部偏青，整体写实不夸张。"
    summary_en = ("[reference generation] A 15-second bedroom sequence continuing from the previous segment: "
                  "the couple sits at the bedside, coats stay off, quiet intimacy under the same warm lamp light.")
    retention = ["%s: fully_preserved" % tag for tag in
                 (list(subj_map.values()) + [profile.scene_subject_tag])]
    soundscape = "卧室环境声：床头灯电流极轻、窗外远处电车声、布料窸窣与呼吸；对白之间保留自然静默。"
    prompt = h3_format.build_ref2va(subj_defs, summary_en, retention, style_open,
                                    paragraphs, soundscape, music="N/A")
    return prompt_hygiene.sanitize_internal_ids(prompt, profile.id_map), profile


def _run_su_cai_video(h, t):
    """素材轮第二步：卧室 15+15 衔接 A/B 测试（4 段视频 + 2 个拼接），全部走 TaskCenter。"""
    import json, time as _t
    from pathlib import Path as _P
    from cores import text_checks, prompt_hygiene, story_core, asset_core
    from models import h3_client
    from production import video_plan, video_core
    ROOT = _P(__file__).resolve().parent.parent
    date = _t.strftime("%Y%m%d")
    outdir = ROOT / "productions" / ("素材轮_" + date) / "videos"
    outdir.mkdir(parents=True, exist_ok=True)
    fr = ROOT / "reports" / "v423_text_final" / "final_review"
    prompt_a = (fr / "30_h3_final_prompts" / "sequence3_bedroom.txt").read_text(encoding="utf-8")
    prompt_b, profile = _compile_seq_b_prompt()
    sid = _ensure_story_004()
    char_a = str(ROOT / "productions" / "v423_t3" / "assets" / "KREA_CHAR_A_MASTER.png")
    char_b = str(ROOT / "productions" / "v423_t3" / "assets" / "KREA_CHAR_B_MASTER.png")
    bedroom = None
    for v in asset_core.list_assets(sid, "visuals"):
        if v.get("owner_id") == "SCENE_卧室" and v.get("kind") == "scene_master" and v.get("status") == "adopted":
            bedroom = v.get("path")
    if not bedroom or not os.path.exists(str(bedroom)):
        bedroom = str(ROOT / "productions" / ("素材轮_" + date) / "scenes" / "SCENE_卧室.png")
    refs3 = [char_a, char_b, str(bedroom)]
    art = {
        "prompts": {"SEQ_A": prompt_a, "SEQ_B": prompt_b},
        "ref_plan": [
            {"sequence_id": "SEQ_A", "pictures": [
                {"source": "CHAR_林舟_MASTER", "role": "character_identity"},
                {"source": "CHAR_艾莉莎_MASTER", "role": "character_identity"},
                {"source": "SCENE_卧室_MASTER", "role": "scene_master"}]},
            {"sequence_id": "SEQ_B", "pictures": [
                {"source": "CHAR_林舟_MASTER", "role": "character_identity"},
                {"source": "CHAR_艾莉莎_MASTER", "role": "character_identity"},
                {"source": "SCENE_卧室_MASTER", "role": "scene_master"}]},
        ],
    }
    gates = [text_checks.check_video_prompt_shape(profile, art),
             text_checks.check_reference_uniqueness(profile, art),
             text_checks.check_no_internal_ids(profile, art)]
    h.step("出关检查", 0, 8)
    for g in gates:
        if not g["ok"]:
            raise RuntimeError("出关检查 FAIL：%s（%s）" % (g["item"], g["detail"]))
    steps = [("生成 SEQ_A（A组）", 1), ("抽取结束帧", 2), ("生成 SEQ_A（B组）", 3),
             ("生成 SEQ_B（A组）", 4), ("生成 SEQ_B（B组，含结束帧）", 5),
             ("拼接 A 组", 6), ("拼接 B 组", 7), ("写 README", 8)]
    vram_peak = _vram_peak_mb()
    records, files = [], {}
    for i, (label, step) in enumerate(steps):
        h.check_pause()
        h.step(label, step - 1, 8)
    t0 = _t.time()
    vram_peak = max(vram_peak, _vram_peak_mb())
    res_a = h3_client.generate("ref2va", prompt_a, images=refs3, seconds=15, ratio="16:9", seed=3001)
    fa = outdir / "seq_a_agroup.mp4"
    shutil.copy2(res_a["output_path"], str(fa))
    vram_peak = max(vram_peak, _vram_peak_mb())
    records.append({"video": "seq_a_agroup", "prompt": prompt_a, "refs": refs3,
                    "params": {"seconds": 15, "ratio": "16:9", "seed": 3001},
                    "elapsed_s": round(_t.time() - t0, 1), "path": str(fa)})
    files["seq_a_agroup"] = str(fa)
    h.step("生成 SEQ_A（A组）", 1, 8)
    end_frame = outdir / "end_frame_seq_a.png"
    video_plan.extract_end_frame(str(fa), str(end_frame))
    h.step("抽取结束帧", 2, 8)
    t0 = _t.time()
    res_a2 = h3_client.generate("ref2va", prompt_a, images=refs3, seconds=15, ratio="16:9", seed=3002)
    fa2 = outdir / "seq_a_bgroup.mp4"
    shutil.copy2(res_a2["output_path"], str(fa2))
    vram_peak = max(vram_peak, _vram_peak_mb())
    records.append({"video": "seq_a_bgroup", "prompt": prompt_a, "refs": refs3,
                    "params": {"seconds": 15, "ratio": "16:9", "seed": 3002},
                    "elapsed_s": round(_t.time() - t0, 1), "path": str(fa2)})
    files["seq_a_bgroup"] = str(fa2)
    h.step("生成 SEQ_A（B组）", 3, 8)
    t0 = _t.time()
    res_ba = h3_client.generate("ref2va", prompt_b, images=refs3, seconds=15, ratio="16:9", seed=3003)
    fba = outdir / "seq_b_agroup.mp4"
    shutil.copy2(res_ba["output_path"], str(fba))
    vram_peak = max(vram_peak, _vram_peak_mb())
    records.append({"video": "seq_b_agroup", "prompt": prompt_b, "refs": refs3,
                    "params": {"seconds": 15, "ratio": "16:9", "seed": 3003},
                    "elapsed_s": round(_t.time() - t0, 1), "path": str(fba)})
    files["seq_b_agroup"] = str(fba)
    h.step("生成 SEQ_B（A组）", 4, 8)
    refs4 = refs3 + [str(end_frame)]
    t0 = _t.time()
    res_bb = h3_client.generate("ref2va", prompt_b, images=refs4, seconds=15, ratio="16:9", seed=3004)
    fbb = outdir / "seq_b_bgroup.mp4"
    shutil.copy2(res_bb["output_path"], str(fbb))
    vram_peak = max(vram_peak, _vram_peak_mb())
    records.append({"video": "seq_b_bgroup", "prompt": prompt_b, "refs": refs4,
                    "params": {"seconds": 15, "ratio": "16:9", "seed": 3004},
                    "elapsed_s": round(_t.time() - t0, 1), "path": str(fbb)})
    files["seq_b_bgroup"] = str(fbb)
    h.step("生成 SEQ_B（B组，含结束帧）", 5, 8)
    m_a = video_core.concat_sequences([files["seq_a_agroup"], files["seq_b_agroup"]], 30, "素材轮A_15_15")
    fa_out = outdir / "stitch_a_15_15.mp4"
    shutil.copy2(m_a["path"], str(fa_out))
    h.step("拼接 A 组", 6, 8)
    m_b = video_core.concat_sequences([files["seq_a_bgroup"], files["seq_b_bgroup"]], 30, "素材轮B_15_15")
    fb_out = outdir / "stitch_b_15_15.mp4"
    shutil.copy2(m_b["path"], str(fb_out))
    h.step("拼接 B 组", 7, 8)
    manifest = {"date": date, "story_id": sid, "vram_peak_mb": vram_peak,
                "videos": records, "stitch": {"A": str(fa_out), "B": str(fb_out)},
                "note": "无失败重试；种子已固定；A/B 组条件唯一差异是 B 组 SEQ_B 多一张上一段结束帧。"}
    (outdir / "video_records.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    h.step("写 README", 8, 8)
    return manifest

RUNNERS["su_cai_scenes"] = _run_su_cai_scenes
RUNNERS["su_cai_video"] = _run_su_cai_video

# ---- 游戏模式 / 存储 / 归档 / 问题 ----
def game_enter():
    before = gpu_usage()
    paused = []
    for t in list_tasks(200):
        if t.get("status") in ("running", "queued", "waiting_user"):
            action(t["id"], "pause")
            paused.append(t["id"])
    from models import qwen_client, gpu_manager
    try:
        qwen_client.free()
    except Exception:
        pass
    gpu_manager.stop_qwen()
    gpu_manager.stop_comfy()
    gpu_manager.stop_h3()
    time.sleep(3)
    after = gpu_usage()
    state = {"mode": "game", "before": before, "after": after, "paused_tasks": paused, "at": time.time()}
    store.save_json(store.DATA / "gpu_state.json", state)
    return state

def game_exit():
    st = store.load_json(store.DATA / "gpu_state.json", {"mode": "normal"})
    st["mode"] = "normal"; st["at"] = time.time()
    store.save_json(store.DATA / "gpu_state.json", st)
    return st

def gpu_usage():
    try:
        import subprocess
        out = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used,memory.total",
                                       "--format=csv,noheader,nounits"], timeout=8).decode("utf-8", "replace").strip()
        parts = [int(x.strip()) for x in out.splitlines()[0].split(",")]
        return {"used_mb": parts[0], "total_mb": parts[1]}
    except Exception:
        return {"used_mb": -1, "total_mb": -1}

def storage_info():
    def dsize(d):
        if not os.path.isdir(str(d)):
            return 0
        total = 0
        for root, _, files in os.walk(str(d)):
            for fn in files:
                try:
                    total += os.path.getsize(os.path.join(root, fn))
                except Exception:
                    pass
        return total
    root = Path(__file__).resolve().parent.parent
    return {"data": dsize(root / "data"), "productions": dsize(root / "productions"),
            "outputs": dsize(root / "outputs"), "cache": dsize(root / "cache")}

def archive_project(story_id):
    from cores import story_core
    st = story_core.get_story(story_id)
    if not st:
        raise ValueError("故事不存在")
    title = re_sub(st.get("title") or "story")
    outdir = Path(__file__).resolve().parent.parent / "outputs" / "archives"
    outdir.mkdir(parents=True, exist_ok=True)
    zpath = outdir / (title + "_" + time.strftime("%Y%m%d_%H%M%S") + ".zip")
    with zipfile.ZipFile(str(zpath), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("story.json", json.dumps(st, ensure_ascii=False, indent=2))
        prods = [store.load_json(p, None) for p in PRODUCTIONS_DIR.glob("*.json")]
        prods = [p for p in prods if p and p.get("story_id") == story_id]
        z.writestr("productions.json", json.dumps(prods, ensure_ascii=False, indent=2))
        manis = [store.load_json(p, None) for p in MANIFESTS_DIR.glob("*.json")]
        z.writestr("manifests.json", json.dumps(manis, ensure_ascii=False, indent=2))
    st["archived"] = True
    story_core.save_story(st)
    return str(zpath)

def problems():
    failed = [t for t in list_tasks(200) if t.get("status") == "failed"]
    return {"failed_tasks": failed}

import re as _re
def re_sub(s):
    return _re.sub(r'[\\/:*?"<>|]', "", str(s or ""))[:40]
