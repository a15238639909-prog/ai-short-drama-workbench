# -*- coding: utf-8 -*-
"""h3_client.py — V4.1 clean-room MiniMax H3 客户端。
正式视频：≤15s Sequence + Reference Pack + 全能参考；文生只用于快速测试/空镜/环境。"""
import json, os, random, re, shutil, subprocess, threading, time, uuid, urllib.request, urllib.error
from pathlib import Path

try:
    from cores import paths as _paths
    H3_ROOT = Path(_paths.get("h3_root"))
except Exception:
    H3_ROOT = Path(os.environ.get("V41_H3_ROOT") or ".")            # P447：没配就指向当前目录，用到时报「没配好」
H3_COMFY = H3_ROOT / "ComfyUI"
H3_PYTHON = H3_ROOT / "python" / "python.exe"
H3_INPUT = H3_COMFY / "input"
H3_OUTPUT = H3_COMFY / "output"
H3_URL = os.environ.get("V41_H3_URL", "http://127.0.0.1:8190")
TEMPLATES = Path(__file__).resolve().parent.parent / "models" / "video_workflows"
OUTPUT_ROOT = Path(__file__).resolve().parent.parent / "outputs" / "video"
FRAME_SIZES = {"16:9": (1152, 640), "9:16": (640, 1152), "1:1": (832, 832), "4:5": (768, 960)}

# 【测试档】规划阶段要反复看节奏和衔接，不需要成片分辨率。
# 0.4 倍出图快得多，规划确认无误后再用 1.0 出成片。
# 尺寸必须对齐 16，否则 VAE 会报错。
# 【尺寸按兆像素分档，不是按缩放倍数】
# 之前把「0.4」理解成 0.4 倍缩放，出来 464×256 ＝ 0.12 MP，画面闪烁发糊。
# 用户说的 0.4 是 **0.4 兆像素**，16:9 下就是 864×480——这是他一直在用的档。
SIZE_TIERS = {
    "0.4": {"16:9": (864, 480), "9:16": (480, 864), "1:1": (640, 640), "4:5": (576, 720)},
    "0.7": {"16:9": (1120, 624), "9:16": (624, 1120), "1:1": (832, 832), "4:5": (752, 944)},
    "1.0": {"16:9": (1376, 768), "9:16": (768, 1376), "1:1": (1024, 1024), "4:5": (912, 1136)},
}

# 画幅比 → 宽高比例。**只此一份**：原来 _sized 里还内联了一份，
# 两份都没有 3:4，传 3:4 会被双双退回 16:9，不报错——
# 实测 _sized("3:4","0.7") 和 _sized("16:9","0.7") 返回完全一样的 (1120,624)（P242）。
RATIOS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:5": (4, 5), "3:4": (3, 4)}

# 默认 0.7 MP（1120×624）、25 步、16:9 —— 用户 2026-09-09 定，网页设定页可改。
# 0.4=864×480 实测单段约 281s；1.0=1376×768 实测约 542s；0.7 居中，未单独实测。
# 环境变量只是「没有项目设定时」的兜底，项目设定一填就赢过它。
SIZE_TIER = os.environ.get("V41_H3_SIZE", "0.7")
DEFAULT_RATIO = os.environ.get("V41_H3_RATIO", "16:9")
# 不传步数时用工作流模板里的值（三份模板的 BasicScheduler 实测都是 25）。
DEFAULT_STEPS = int(os.environ.get("V41_H3_STEPS") or 0) or None
TEMPLATE_STEPS = 25          # 模板里的步数；_graph_steps 读不到 BasicScheduler 时用它

# P267【渲染速率】每 1 秒视频要渲染几秒（25 步实测中位；来源：扫 ComfyUI 输出文件名里的时间 vs mtime）。
# 预览确认框原来写死「约 n×1.5 分钟」，0.7 档 25 步一段 12 秒视频实测 11 分钟，差七倍。
# 表值只是没实测记录时的兜底：generate() 每出一段就记一条到 data/render_timing.json，rate_for 优先取实测。
DEFAULT_RATE = {"0.4": 28.0, "0.7": 55.0, "1.0": 95.0}
_TIMING_KEEP = 300           # 只留最近这么多条，文件不会无限长
_TIMING_RECENT = 8           # 同档同步数取最近几条算中位
_TIMING_MIN = 2              # 少于这个数不算实测（一条可能是冷启动/模型加载）


def _timing_file():
    """实测记录文件。懒 import store：测试把 store.DATA 指到临时目录后才导入本模块也要生效。"""
    from cores import store as _store
    return _store.DATA / "render_timing.json"


def _graph_steps(graph):
    """工作流实际用的步数：BasicScheduler 节点的 steps；没有这个节点/读不出 → 模板值 25。"""
    try:
        _, sched = _find_node(graph or {}, "BasicScheduler")
        v = int((sched or {}).get("inputs", {}).get("steps") or 0)
        return v if v > 0 else TEMPLATE_STEPS
    except Exception:
        return TEMPLATE_STEPS


def _record_timing(meta, steps, elapsed):
    """出完一段记一条 {ts,mode,size_tier,steps,width,height,seconds,elapsed}。
    全 try 包住：记不上不能影响出片（片子已经在手里了）。"""
    try:
        from cores import store as _store
        p = _timing_file()
        rows = _store.load_json(p, [])
        if not isinstance(rows, list):
            rows = []
        m = meta or {}
        rows.append({"ts": time.time(), "mode": str(m.get("mode") or ""),
                     "size_tier": str(m.get("size_tier") if m.get("size_tier") is not None else SIZE_TIER),
                     "steps": int(steps or TEMPLATE_STEPS),
                     "width": int(m.get("width") or 0), "height": int(m.get("height") or 0),
                     "seconds": float(m.get("seconds") or 0), "elapsed": float(elapsed or 0)})
        if len(rows) > _TIMING_KEEP:
            rows = rows[-_TIMING_KEEP:]
        _store.save_json(p, rows)
    except Exception:
        pass


def rate_for(size_tier=None, steps=None):
    """每 1 秒视频要渲染几秒 → (速率, "measured"|"default")。

    同档**同步数**最近 8 条实测 ≥2 条 → 取中位（步数不同耗时成比例，混在一起中位数没意义）；
    否则默认表 × steps/25；表里没有的档按 55×(tier/0.7)^1.5 推（像素数 ∝ tier，耗时略超线性）。
    """
    tier = str(size_tier if size_tier not in (None, "") else SIZE_TIER)
    try:
        st = int(steps or 0) or TEMPLATE_STEPS
    except Exception:
        st = TEMPLATE_STEPS
    try:
        from cores import store as _store
        rows = _store.load_json(_timing_file(), [])
        same = [r for r in (rows if isinstance(rows, list) else [])
                if isinstance(r, dict) and str(r.get("size_tier")) == tier
                and int(r.get("steps") or 0) == st and float(r.get("seconds") or 0) > 0
                and float(r.get("elapsed") or 0) > 0]
        same = same[-_TIMING_RECENT:]
        if len(same) >= _TIMING_MIN:
            vals = sorted(float(r["elapsed"]) / float(r["seconds"]) for r in same)
            n = len(vals)
            med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
            return round(med, 2), "measured"
    except Exception:
        pass
    base = DEFAULT_RATE.get(tier)
    if base is None:
        try:
            base = 55.0 * (float(tier) / 0.7) ** 1.5
        except Exception:
            base = DEFAULT_RATE["0.7"]
    return round(base * st / float(TEMPLATE_STEPS), 2), "default"


def _sized(ratio, tier=None):
    """按兆像素档和画幅比算出尺寸。给了 tier 就用它，否则用默认档。"""
    r = str(ratio or DEFAULT_RATIO)
    if r not in RATIOS:
        # 【认不出就抛错】原来这里静默退回 16:9：传 3:4 出来的是横屏，
        # 不报错、不留痕，只能靠肉眼看出来（P242）。
        raise ValueError("不支持的画幅比：%s（支持 %s）" % (ratio, "、".join(RATIOS)))
    t = str(tier if tier is not None else SIZE_TIER)
    table = SIZE_TIERS.get(t)
    if table and r in table:
        return table[r]                      # 档表里有就用表值，保住历史产物的尺寸
    # 表里没有（3:4 全部档位、以及 0.5/0.6/0.8/0.9 各比例）→ 按兆像素现算，对齐到 16
    try:
        mp = float(t) * 1000000.0
    except Exception:
        mp = 0.7 * 1000000.0
    rw, rh = RATIOS[r]
    import math
    k = math.sqrt(mp / (rw * rh))
    f = lambda v: max(256, int(round(v / 16.0)) * 16)
    return f(rw * k), f(rh * k)


FPS = 24  # 模板里 VHS_VideoCombine 的 frame_rate=24


def _find_node(graph, class_type):
    """按 class_type 找第一个节点。返回 (节点id, 节点dict)，找不到 (None, None)。"""
    for nid, node in graph.items():
        if isinstance(node, dict) and node.get("class_type") == class_type:
            return nid, node
    return None, None


def _aligned_frames(seconds):
    """秒 → 帧数。24fps，对齐到 4 的倍数（模板默认 length=124 就是这个约定），
    时长夹在 4~15 秒。"""
    sec = max(4, min(15, float(seconds or 15)))
    return max(96, int(round(sec * FPS / 4.0)) * 4)


def _copy_to_input(path, tag):
    """把本地图片复制进 H3 的 ComfyUI input 目录，返回文件名。
    名字带 uuid，避免同名旧图被 ComfyUI 缓存顶掉新图。"""
    src = Path(path)
    if not src.exists():
        raise RuntimeError("参考图不存在：" + str(path))
    H3_INPUT.mkdir(parents=True, exist_ok=True)
    fname = "v41_%s_%s%s" % (tag, uuid.uuid4().hex[:8], src.suffix or ".png")
    shutil.copy2(str(src), str(H3_INPUT / fname))
    return fname


def _add_load_image(graph, fname):
    """往图里加一个 LoadImage 节点，返回它的输出连接 [节点id, 0]。"""
    nid = str(max([int(k) for k in graph.keys() if str(k).isdigit()] or [900]) + 1)
    graph[nid] = {"class_type": "LoadImage", "inputs": {"image": fname}}
    return [nid, 0]


# 取消标志：同一时刻只跑一段 H3（显卡互斥），一个标志够用。
# 生成开始时清零；轮询里看到就 /interrupt 并抛「已取消」。
CANCEL = {"flag": False}


def request_cancel():
    CANCEL["flag"] = True


def _http(method, path, data=None, timeout=60):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(H3_URL + path, data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _validate_reference_image_size(value):
    if value not in ("match", "max"):
        raise ValueError("参考图精度须为match或max")


def build_workflow(mode, prompt, images=None, seconds=15, ratio=None, seed=None,
                   filename_prefix=None, size_tier=None, steps=None, reference_videos=None, exact_frames=None,
                   reference_image_size="match"):
    """装配 H3 工作流。images 为本地图片路径列表（正式制作=Reference Pack）。"""
    _validate_reference_image_size(reference_image_size)
    mode = str(mode or "t2va").lower()
    # Motion-transfer opt-in only. Existing story generation keeps its frame policy.
    if exact_frames is not None:
        if mode != 'ref2va' or not isinstance(exact_frames, int) or not 107 <= exact_frames <= 362 or exact_frames % 17 != 5:
            raise ValueError('动作迁移帧数须为107～362之间的17n+5')
    frame_count = exact_frames if exact_frames is not None else _aligned_frames(seconds)
    images = list(images or [])
    reference_videos = list(reference_videos or [])
    if reference_videos and mode != 'ref2va':
        raise ValueError('参考视频只用于ref2va模式')
    if len(reference_videos) > 3:
        raise ValueError('参考视频最多3段')
    if mode == "t2va":
        graph = json.loads((TEMPLATES / "t2va.json").read_text(encoding="utf-8"))
        _, h3 = _find_node(graph, "MiniMaxH3ImageToVideo")
    elif mode in ("i2va", "fl2va"):
        graph = json.loads((TEMPLATES / "i2va_fl2va.json").read_text(encoding="utf-8"))
        _, h3 = _find_node(graph, "MiniMaxH3ImageToVideo")
        if not images:
            raise RuntimeError("图生/首尾帧需要图片")
        fname = _copy_to_input(images[0], "first")
        h3["inputs"]["first_frame"] = _add_load_image(graph, fname)
        if mode == "fl2va":
            if len(images) < 2:
                raise RuntimeError("首尾帧需要 2 张图片")
            lname = _copy_to_input(images[1], "last")
            h3["inputs"]["last_frame"] = _add_load_image(graph, lname)
        else:
            h3["inputs"].pop("last_frame", None)
    else:  # ref2va 全能参考
        if not images and not reference_videos:
            raise RuntimeError("全能参考需要参考图或参考视频")
        if len(images) > 9:
            raise RuntimeError("参考图最多 9 张")
        graph = json.loads((TEMPLATES / "ref2va.json").read_text(encoding="utf-8"))
        _, h3 = _find_node(graph, "MiniMaxH3ReferenceToVideo")
        for i, item in enumerate(images):
            fname = _copy_to_input(item, "refimg%d" % (i + 1))
            h3["inputs"]["ref_images.ref_image_%d" % i] = _add_load_image(graph, fname)
        h3["inputs"]["ref_image_size"] = reference_image_size
        for i, video in enumerate(reference_videos):
            # motion_core prepares a bounded 24fps clip; H3 receives the full frame batch.
            fname = _copy_to_input(video, 'refvideo%d' % (i + 1))
            nid = str(max(int(k) for k in graph if str(k).isdigit()) + 1)
            cid = str(int(nid) + 1)
            graph[nid] = {'class_type': 'LoadVideo', 'inputs': {'file': fname}}
            graph[cid] = {'class_type': 'GetVideoComponents', 'inputs': {'video': [nid, 0]}}
            h3['inputs']['ref_videos.ref_video_%d' % i] = [cid, 0]
    if not h3:
        raise RuntimeError("模板缺少 H3 主节点")
    ratio = ratio or DEFAULT_RATIO
    if steps is None:
        steps = DEFAULT_STEPS
    w, h = _sized(ratio, size_tier)
    h3["inputs"].update({"prompt": str(prompt or "").strip(),
                         "width": int(w), "height": int(h),
                         "length": frame_count})
    _, noise = _find_node(graph, "RandomNoise")
    used_seed = int(seed if seed is not None else random.randint(1, 2**48 - 1))
    if noise:
        noise["inputs"]["noise_seed"] = used_seed
    if steps is not None:
        _, sched = _find_node(graph, "BasicScheduler")
        if sched:
            sched["inputs"]["steps"] = int(steps)
    _, combine = _find_node(graph, "VHS_VideoCombine")
    if combine:
        combine["inputs"]["filename_prefix"] = filename_prefix or ("MiniMaxH3/v41_" + time.strftime("%Y%m%d_%H%M%S"))
    meta = {"mode": mode, "width": w, "height": h, "frames": frame_count,
                   "seconds": max(4, min(15, int(round(float(seconds))))),
                   "seed": used_seed,
                   "size_tier": (size_tier if size_tier is not None else SIZE_TIER),
                   "ratio": ratio, "steps": steps,
                   "expect_audio": bool(combine and isinstance(combine.get("inputs", {}).get("audio"), (list, tuple)))}
    if mode == "ref2va":
        meta["reference_image_size"] = reference_image_size
    return graph, meta

_START_LOCK = threading.Lock()


def up(timeout=5):
    try:
        urllib.request.urlopen(H3_URL + "/system_stats", timeout=timeout).close()
        return True
    except Exception:
        return False



def _game_mode_guard():
    """游戏模式暂停旗立着时拒绝上卡——用户在打游戏，显卡不能碰。"""
    from cores import runtime_reset
    runtime_reset.assert_current()
    from pathlib import Path as _P
    if (_P(__file__).resolve().parent.parent / "cache" / "game_mode.flag").exists():
        raise RuntimeError("旧的暂停状态尚未清除，请点右上角「清理缓存」再开始新任务")

def start_h3(timeout=240):
    if os.environ.get("V41_NO_MODEL"):
        raise RuntimeError("V41_NO_MODEL=1：零模型测试不许调模型（h3_client.start_h3）")
    _game_mode_guard()
    """拉起 H3 的 ComfyUI。之前这个函数被调用但根本不存在——
    H3 没开着的时候点「生成这一段」直接 NameError。"""
    with _START_LOCK:
        if up():
            return True
        # 单卡互斥：Qwen/Krea 占着显存的话 H3 模型根本加载不进去，先清场
        from . import gpu_manager
        gpu_manager.guard("h3", note="H3 ComfyUI:8190")       # P272 跨进程占用/僵尸/内存总闸
        with gpu_manager.GPU_LOCK:
            gpu_manager.claim("h3")
            if not H3_PYTHON.exists():
                raise RuntimeError("H3 python 不存在：" + str(H3_PYTHON))
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            log = open(str(H3_ROOT / "v41_h3.log"), "ab")
            gpu_manager.spawn("h3", [str(H3_PYTHON), "-s", r"ComfyUI\main.py", "--port", "8190",
                              "--disable-auto-launch"],
                             cwd=str(H3_ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
                             creationflags=0x08000000)
            for _ in range(int(timeout / 3)):
                time.sleep(3)
                _game_mode_guard()
                if up():
                    return True
            raise RuntimeError("H3 ComfyUI %d 秒内未就绪，看 %s" % (timeout, H3_ROOT / "v41_h3.log"))


def _receipt_path(key):
    import hashlib
    from cores import store
    return store.DATA / 'generation_requests' / 'h3' / (hashlib.sha256(str(key).encode('utf-8')).hexdigest() + '.json')


def recovery_receipt(key):
    from cores import store
    return store.load_json(_receipt_path(key), {}) if key else {}


def _save_receipt(key, receipt):
    if key:
        from cores import store
        receipt['updated_at'] = time.time()
        store.save_json(_receipt_path(key), receipt)


def _has_audio_stream(path):
    """A VHS filename is a hint; verify the completed mux really has audio."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=codec_type',
             '-of', 'json', str(path)], capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.returncode == 0 and any(
            item.get('codec_type') == 'audio' for item in json.loads(result.stdout).get('streams', []))
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return False


def generate(mode, prompt, images=None, seconds=15, ratio=None, seed=None, timeout=3600,
             size_tier=None, steps=None, reference_videos=None, exact_frames=None, recovery_key=None,
             reference_image_size="match"):
    """提交一次 H3 生成，轮询并复制 mp4 到 outputs/video。"""
    _validate_reference_image_size(reference_image_size)
    from cores.age_policy import assert_model_request

    if os.environ.get("V41_NO_MODEL"):
        raise RuntimeError("V41_NO_MODEL=1：零模型测试不许调模型（视频生成）")
    from cores import runtime_reset
    runtime_reset.assert_current()
    CANCEL["flag"] = False
    recovery_key = recovery_key or ('standalone:' + uuid.uuid4().hex)
    receipt = recovery_receipt(recovery_key)
    if receipt.get('status') == 'completed' and Path(str((receipt.get('result') or {}).get('output_path') or '')).is_file():
        return receipt['result']
    if receipt.get('status') == 'cancelled':
        raise RuntimeError('已取消：已保存的视频任务不会自动重新提交')
    if not up():
        start_h3()
    from . import gpu_manager as _gm
    _gm.acquire("h3", note="generate")                 # P273 用模型也要过占卡闸
    pid = receipt.get('prompt_id')
    resume = bool(pid and receipt.get('status') in ('submitting', 'submitted'))
    if resume:
        # Queue first, then history: a job finishing between these queries is
        # still found. Never infer non-submission from a lost POST response.
        try:
            queue = _http('GET', '/queue', timeout=30)
            hist = _http('GET', '/history/' + pid, timeout=30)
        except Exception as exc:
            raise RuntimeError('H3任务状态暂时无法确认，已保存任务编号，恢复时查询原任务') from exc
        queued = any(len(row) > 1 and str(row[1]) == pid
                     for row in queue.get('queue_running', []) + queue.get('queue_pending', []))
        if not queued and pid not in hist:
            prefix = str(receipt.get('filename_prefix') or '')
            # VHS retains the raw video next to the final '-audio' mux. Plain
            # lexicographic sorting puts '.mp4' last and would recover silence.
            # All three shipped templates contain audio; old receipts therefore
            # default to requiring it as well.
            meta = dict(receipt.get('meta') or {})
            meta.setdefault('expect_audio', True)
            candidates = sorted(H3_OUTPUT.glob(prefix + '*.mp4'),
                                key=lambda path: (path.name.endswith('-audio.mp4'), path.name),
                                reverse=True) if prefix else []
            recovered = [path for path in candidates
                         if not meta['expect_audio'] or _has_audio_stream(path)]
            if recovered:
                src = recovered[0]
                OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
                dst = OUTPUT_ROOT / src.name
                shutil.copy2(src, dst)
                result = {'output_file': src.name, 'output_path': str(dst), **meta,
                          'steps': receipt.get('steps') or meta.get('steps')}
                receipt.update(status='completed', result=result)
                _save_receipt(recovery_key, receipt)
                return result
            if receipt.get('status') == 'submitting':
                raise RuntimeError('H3提交结果待核对，已保存任务编号；继续查询原任务，不重复生成')
            # The service had acknowledged this exact id. If both the active
            # queue and history lost it (e.g. service restart), no job remains.
            resume = False
    if resume:
        meta = receipt['meta']
        _t0 = float(receipt.get('started_at') or time.time())
        _steps_used = receipt.get('steps') or TEMPLATE_STEPS
    else:
        pid = str(uuid.uuid4())
        prefix = 'MiniMaxH3/v41_recovery_' + pid
        graph, meta = build_workflow(mode, prompt, images, seconds, ratio, seed,
                                     filename_prefix=prefix, size_tier=size_tier, steps=steps,
                                     reference_videos=reference_videos, exact_frames=exact_frames,
                                     reference_image_size=reference_image_size)
        _t0, _steps_used = time.time(), _graph_steps(graph)
        receipt = {'status': 'submitting', 'prompt_id': pid, 'meta': meta,
                   'started_at': _t0, 'steps': _steps_used, 'filename_prefix': prefix}
        # Persist the client UUID before sending; ComfyUI accepts prompt_id.
        _save_receipt(recovery_key, receipt)
        if CANCEL["flag"]:
            receipt['status'] = 'cancelled'
            _save_receipt(recovery_key, receipt)
            raise RuntimeError('已取消')
        runtime_reset.assert_current()
        try:
            result = _http("POST", "/prompt", {"prompt": graph, "prompt_id": pid}, timeout=60)
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                receipt.update(status='rejected', error=str(exc))
                _save_receipt(recovery_key, receipt)
                raise
            raise RuntimeError('H3提交结果待核对，已保存任务编号；继续查询原任务，不重复生成') from exc
        except Exception as exc:
            raise RuntimeError('H3提交结果待核对，已保存任务编号；继续查询原任务，不重复生成') from exc
        if not result.get('prompt_id'):
            raise RuntimeError('H3提交结果待核对，未返回任务编号')
        pid = str(result['prompt_id'])
        receipt.update(status='submitted', prompt_id=pid)
        _save_receipt(recovery_key, receipt)
    deadline = time.time() + timeout
    while time.time() < deadline:
        runtime_reset.assert_current()
        if CANCEL["flag"]:
            # 用户点了取消：让 ComfyUI 打断当前队列，不等这段生完
            try:
                _http("POST", "/interrupt", {}, timeout=10)
            except Exception:
                pass
            CANCEL["flag"] = False
            receipt['status'] = 'cancelled'
            _save_receipt(recovery_key, receipt)
            raise RuntimeError("已取消")
        try:
            hist = _http("GET", "/history/" + pid, timeout=60)
        except Exception as exc:
            raise RuntimeError('H3任务查询暂时失败，已保存任务编号，恢复时继续查询') from exc
        item = hist.get(pid)
        if item:
            st = item.get("status", {})
            if st.get("status_str") == "error":
                receipt.update(status='failed', error=str(st.get('messages'))[-500:])
                _save_receipt(recovery_key, receipt)
                raise RuntimeError("H3 工作流失败：" + str(st.get("messages"))[-500:])
            outs = item.get("outputs", {})
            for node_out in outs.values():
                for v in node_out.get("gifs", []) + node_out.get("videos", []):
                    fn = v.get("filename")
                    sub = v.get("subfolder") or ""
                    src = H3_OUTPUT / sub / fn
                    if src.exists():
                        runtime_reset.assert_current()
                        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
                        dst = OUTPUT_ROOT / fn
                        shutil.copy2(str(src), str(dst))
                        _record_timing(meta, _steps_used, time.time() - _t0)    # P267 真拿到片子才记
                        result = {**meta, "output_file": fn, "output_path": str(dst),
                                "mode": meta["mode"], "frames": meta["frames"],
                                "seed": meta.get("seed"),
                                "size_tier": meta.get("size_tier"),
                                "width": meta.get("width"), "height": meta.get("height"),
                                "expect_audio": meta.get("expect_audio", True), "steps": _steps_used}
                        receipt.update(status='completed', result=result)
                        _save_receipt(recovery_key, receipt)
                        return result
        time.sleep(4)
    raise RuntimeError("H3 生成超时，已保存任务编号，恢复时继续查询原任务")
