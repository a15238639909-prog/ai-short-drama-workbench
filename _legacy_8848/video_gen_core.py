# -*- coding: utf-8 -*-
"""MiniMax H3 本地视频生成核心。

设计目标：
- 不修改 H3 整合包和原 ComfyUI 工作流；
- 使用冻结的 API 工作流模板，只注入必要参数；
- 单 GPU 串行队列，任务状态持久化；
- 通过 GPU hooks 与主工作台现有 Qwen/Krea2 显存管理联动。

本模块只依赖 Python 标准库。
"""
from __future__ import annotations

import base64
import copy
import json
import math
import os
import queue
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from cores import paths as _package_paths
from models.anima_client import local_guard as _local_model_guard

BASE_DIR = Path(__file__).resolve().parent
VIDEO_OUTPUT_ROOT = BASE_DIR / "视频输出"
WORKFLOW_DIR = BASE_DIR / "video_gen_workflows"
VIDEO_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)

# 可通过环境变量覆盖，避免用户以后移动整合包时修改代码。
H3_ROOT = Path(os.environ.get(
    "MINIMAX_H3_ROOT",
    _package_paths.get("h3_root") or str(_package_paths.ROOT / "models_not_configured" / "h3"),
))
H3_COMFY_DIR = H3_ROOT / "ComfyUI"
H3_PYTHON = H3_ROOT / "python" / "python.exe"
H3_INPUT = H3_COMFY_DIR / "input"
H3_OUTPUT = H3_COMFY_DIR / "output"
H3_LOG = H3_COMFY_DIR / "user" / "workbench_start_8190.log"
H3_PORT = int(os.environ.get("MINIMAX_H3_PORT", "8190"))
H3_URL = f"http://127.0.0.1:{H3_PORT}"

STATE_LOCK = threading.RLock()
GPU_LOCK = threading.RLock()
_GPU_HOOKS = {
    "release_writer": None,
    "release_vision": None,
    "stop_krea": None,
}


def configure_gpu_hooks(release_writer=None, release_vision=None, stop_krea=None):
    """由 app.py 注入已有显存释放函数，避免 video_gen_core 反向 import app。"""
    with GPU_LOCK:
        _GPU_HOOKS["release_writer"] = release_writer
        _GPU_HOOKS["release_vision"] = release_vision
        _GPU_HOOKS["stop_krea"] = stop_krea


def _call_hook(name):
    fn = _GPU_HOOKS.get(name)
    if callable(fn):
        try:
            fn()
        except Exception:
            pass


def prepare_gpu_for_h3():
    """统一显存入口：H3 开始前释放写作、视觉与 Krea2。"""
    with GPU_LOCK:
        _call_hook("release_writer")
        _call_hook("release_vision")
        _call_hook("stop_krea")
        time.sleep(1.0)


def _http_json(url, data=None, method=None, timeout=15):
    body = None
    headers = {}
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8", "replace"))


def service_up(timeout=1.5):
    try:
        _http_json(H3_URL + "/system_stats", timeout=timeout)
        return True
    except Exception:
        return False


def _pid_on_port(port):
    if os.name != "nt":
        return None
    try:
        out = subprocess.check_output(["netstat", "-ano"], timeout=8).decode("utf-8", "replace")
        needle = f":{int(port)}"
        for line in out.splitlines():
            if needle in line and "LISTENING" in line:
                parts = line.split()
                if parts and parts[-1].isdigit():
                    return int(parts[-1])
    except Exception:
        pass
    return None


def service_free():
    if not service_up():
        return True
    try:
        _http_json(H3_URL + "/free", {"unload_models": True, "free_memory": True}, method="POST", timeout=12)
        return True
    except Exception:
        return False


def service_stop(kill=True):
    """中断 H3 当前任务并释放；kill=True 时结束 8190 服务进程。"""
    if service_up():
        for endpoint, payload in (("/interrupt", {}), ("/queue", {"clear": True})):
            try:
                _http_json(H3_URL + endpoint, payload, method="POST", timeout=8)
            except Exception:
                pass
        service_free()
    if kill:
        pid = _pid_on_port(H3_PORT)
        if pid:
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], timeout=10, capture_output=True)
            except Exception:
                pass
    return not service_up(timeout=0.8)


def service_start(timeout=180):
    _local_model_guard()
    if service_up():
        return True
    if not H3_PYTHON.exists():
        raise RuntimeError(f"找不到 MiniMax H3 Python：{H3_PYTHON}")
    main_py = H3_COMFY_DIR / "main.py"
    if not main_py.exists():
        raise RuntimeError(f"找不到 MiniMax H3 ComfyUI：{main_py}")
    prepare_gpu_for_h3()
    H3_LOG.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    args = [
        str(H3_PYTHON), "-s", str(main_py),
        "--port", str(H3_PORT), "--disable-auto-launch",
    ]
    flags = 0x08000000 if os.name == "nt" else 0
    with open(H3_LOG, "ab") as lf:
        subprocess.Popen(
            args, cwd=str(H3_ROOT), env=env,
            stdout=lf, stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    deadline = time.time() + max(20, int(timeout))
    while time.time() < deadline:
        if service_up(timeout=2):
            return True
        time.sleep(2)
    raise RuntimeError(f"MiniMax H3 ComfyUI 在 {timeout} 秒内未就绪，请查看：{H3_LOG}")


def service_status():
    return {
        "up": service_up(),
        "port": H3_PORT,
        "url": H3_URL,
        "root": str(H3_ROOT),
        "pythonExists": H3_PYTHON.exists(),
        "comfyExists": H3_COMFY_DIR.exists(),
        "workflowDir": str(WORKFLOW_DIR),
        "outputDir": str(VIDEO_OUTPUT_ROOT),
    }


# -------------------- Workflow --------------------

def _load_template(name):
    path = WORKFLOW_DIR / f"{name}.json"
    if not path.exists():
        raise RuntimeError(f"缺少 H3 API 工作流模板：{path.name}")
    with open(path, "r", encoding="utf-8") as f:
        graph = json.load(f)
    if not isinstance(graph, dict) or not graph:
        raise RuntimeError(f"H3 API 工作流模板无效：{path.name}")
    return graph


def _find_node(graph, class_type):
    for nid, node in graph.items():
        if node.get("class_type") == class_type:
            return nid, node
    return None, None


def aligned_frames(seconds):
    seconds = max(4, min(15, int(round(float(seconds or 5)))))
    raw = max(5, round(seconds * 24))
    return int(raw + (5 - (raw % 17)) % 17)


# 用户本机实测可用的 MP 档位（0.4～0.9，默认 0.7）。
MP_PRESETS = (0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
RATIOS = {
    "21:9": (21.0, 9.0),
    "16:9": (16.0, 9.0),
    "4:3": (4.0, 3.0),
    "1:1": (1.0, 1.0),
    "3:4": (3.0, 4.0),
    "9:16": (9.0, 16.0),
}
# 16:9 用户原 H3 工作流实测参考档位（32 倍数对齐后的可用尺寸）。
REFERENCE_SIZES_16_9 = {
    0.4: (864, 480),
    0.5: (960, 544),
    0.6: (1056, 608),
    0.7: (1152, 640),
    0.8: (1216, 672),
    0.9: (1280, 736),
}
CANVAS_MULTIPLE = 32
MAX_CANVAS_AREA = 1920 * 1088
MIN_CANVAS_SIDE = 256


def _ratio_value(ratio="16:9", image_ratio=None):
    if image_ratio and float(image_ratio) > 0:
        return float(image_ratio)
    r = RATIOS.get(str(ratio or ""))
    if r:
        return r[0] / r[1]
    return 16.0 / 9.0


def resolve_size(ratio="16:9", mp=0.7, image_ratio=None,
                 multiple=CANVAS_MULTIPLE, max_area=MAX_CANVAS_AREA):
    """按 比例 + MP + 32倍数对齐 计算最终宽高。

    - 文生 / 全能参考：image_ratio 为空，使用用户所选 ratio；
    - 图生 / 首尾帧：image_ratio 传入首帧原始比例，ratio 被忽略；
    - 16:9 优先使用第一阶段验证过的参考档位，其余比例走通用计算。
    """
    mp = float(mp or 0.7)
    mp = min(0.9, max(0.1, mp))
    if image_ratio is None and str(ratio) == "16:9":
        ref = REFERENCE_SIZES_16_9.get(round(mp, 2))
        if ref and ref[0] * ref[1] <= max_area:
            return ref
    r = _ratio_value(ratio, image_ratio)
    target = mp * 1_000_000.0
    tw = math.sqrt(target * r)
    th = math.sqrt(target / r)
    best = None
    for dw in (-2, -1, 0, 1, 2):
        for dh in (-2, -1, 0, 1, 2):
            cw = max(MIN_CANVAS_SIDE, int(round(tw / multiple) + dw) * multiple)
            ch = max(MIN_CANVAS_SIDE, int(round(th / multiple) + dh) * multiple)
            if cw * ch > max_area:
                continue
            area_err = abs(cw * ch - target) / target
            ratio_err = abs((cw / ch) - r) / r
            score = area_err * 0.6 + ratio_err * 0.4
            if best is None or score < best[0]:
                best = (score, cw, ch)
    if best is None:
        raise RuntimeError("视频尺寸计算失败：找不到符合上限的 32 倍数尺寸")
    return best[1], best[2]


def normalize_size(width=None, height=None, ratio="16:9", mp=0.7, image_ratio=None):
    if width and height:
        w, h = int(width), int(height)
    else:
        w, h = resolve_size(ratio=ratio, mp=mp, image_ratio=image_ratio)
    if w < MIN_CANVAS_SIDE or h < MIN_CANVAS_SIDE:
        raise RuntimeError("视频尺寸过小")
    w = max(MIN_CANVAS_SIDE, int(round(w / CANVAS_MULTIPLE)) * CANVAS_MULTIPLE)
    h = max(MIN_CANVAS_SIDE, int(round(h / CANVAS_MULTIPLE)) * CANVAS_MULTIPLE)
    if w * h > MAX_CANVAS_AREA:
        raise RuntimeError("视频尺寸超过当前 H3 工作流上限（像素面积 ≤ 1920×1088）")
    return w, h


def _read_image_size(path):
    """标准库解析 PNG/JPEG 尺寸，作为前端尺寸信息的后端兜底。"""
    try:
        data = Path(path).read_bytes()
    except Exception:
        return None
    if not data:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w = int.from_bytes(data[16:20], "big")
        h = int.from_bytes(data[20:24], "big")
        return (w, h) if w > 0 and h > 0 else None
    if data[:2] == b"\xff\xd8":
        i = 2
        n = len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h = int.from_bytes(data[i + 5:i + 7], "big")
                w = int.from_bytes(data[i + 7:i + 9], "big")
                return (w, h) if w > 0 and h > 0 else None
            if marker == 0xD8 or marker == 0xD9 or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg_len = int.from_bytes(data[i + 2:i + 4], "big")
            if seg_len < 2:
                return None
            i += 2 + seg_len
    return None


def _safe_file_name(name, default="asset"):
    text = re.sub(r'[\\/:*?"<>|]+', "_", str(name or "")).strip(" ._")
    return (text[:80] or default)


def _decode_data_url(data_url, prefix="image"):
    head, sep, payload = str(data_url or "").partition(",")
    if not sep or ";base64" not in head:
        raise RuntimeError("素材不是有效的 base64 data URL")
    mime = head.split(";", 1)[0].split(":", 1)[-1].lower()
    ext = {
        "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
        "video/mp4": ".mp4", "video/quicktime": ".mov",
        "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
    }.get(mime, ".bin")
    try:
        raw = base64.b64decode(payload)
    except Exception as e:
        raise RuntimeError("素材 base64 解码失败") from e
    return raw, ext


def _copy_asset_to_input(value, prefix="asset"):
    """返回 ComfyUI input 下的相对文件名。支持 data URL 或本地文件路径。"""
    H3_INPUT.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:10]
    if isinstance(value, str) and value.startswith("data:"):
        raw, ext = _decode_data_url(value, prefix)
        name = f"workbench_{prefix}_{token}{ext}"
        (H3_INPUT / name).write_bytes(raw)
        return name
    src = Path(str(value or ""))
    if not src.is_file():
        raise RuntimeError(f"找不到参考素材：{value}")
    ext = src.suffix.lower() or ".bin"
    name = f"workbench_{_safe_file_name(src.stem, prefix)}_{token}{ext}"
    shutil.copy2(str(src), str(H3_INPUT / name))
    return name


def _next_node_id(graph):
    nums = []
    for k in graph:
        try:
            nums.append(int(k))
        except Exception:
            pass
    return str((max(nums) if nums else 200) + 1)


def _add_load_image(graph, filename):
    nid = _next_node_id(graph)
    graph[nid] = {"class_type": "LoadImage", "inputs": {"image": filename}}
    return [nid, 0]


def _add_load_audio(graph, filename):
    nid = _next_node_id(graph)
    graph[nid] = {"class_type": "LoadAudio", "inputs": {"audio": filename}}
    return [nid, 0]


def _add_load_video(graph, filename):
    nid = _next_node_id(graph)
    graph[nid] = {
        "class_type": "VHS_LoadVideo",
        "inputs": {
            "video": filename,
            "force_rate": 0,
            "custom_width": 0,
            "custom_height": 0,
            "frame_load_cap": 0,
            "skip_first_frames": 0,
            "select_every_nth": 1,
            "format": "AnimateDiff",
        },
    }
    return nid


def infer_mode(mode="auto", images=None, videos=None, audios=None):
    mode = str(mode or "auto").lower()
    images = images or []
    videos = videos or []
    audios = audios or []
    aliases = {
        "text": "t2va", "t2v": "t2va", "t2va": "t2va",
        "image": "i2va", "i2v": "i2va", "i2va": "i2va",
        "fl2va": "fl2va", "first_last": "fl2va",
        "ref": "ref2va", "ref2va": "ref2va",
    }
    if mode != "auto":
        if mode not in aliases:
            raise RuntimeError("未知视频生成模式")
        return aliases[mode]
    if videos or audios or len(images) >= 3:
        return "ref2va"
    if len(images) == 2:
        return "fl2va"
    if len(images) == 1:
        return "i2va"
    return "t2va"


def build_workflow(prompt, mode="auto", images=None, videos=None, audios=None,
                   width=None, height=None, ratio="16:9", mp=0.7,
                   image_ratio=None, seconds=5, seed=None,
                   filename_prefix=None, ref_image_size="match"):
    images = list(images or [])
    videos = list(videos or [])
    audios = list(audios or [])
    actual_mode = infer_mode(mode, images, videos, audios)
    frames = aligned_frames(seconds)
    sd = int(seed if seed is not None else random.randint(1, 2**48 - 1))
    prefix = filename_prefix or ("MiniMaxH3/workbench_" + time.strftime("%Y%m%d_%H%M%S"))

    first_ratio = None
    if actual_mode in ("i2va", "fl2va"):
        if not images:
            raise RuntimeError("图生视频至少需要 1 张图片")
        first_ratio = image_ratio
        graph = _load_template("i2va_fl2va")
        _, h3 = _find_node(graph, "MiniMaxH3ImageToVideo")
        first_name = _copy_asset_to_input(images[0], "first")
        if not first_ratio:
            sz = _read_image_size(H3_INPUT / first_name)
            if sz and sz[1] > 0:
                first_ratio = sz[0] / float(sz[1])
        if not first_ratio:
            raise RuntimeError("图生视频需要首帧图片的原始尺寸（请重新上传图片）")
        h3["inputs"]["first_frame"] = _add_load_image(graph, first_name)
        if actual_mode == "fl2va":
            if len(images) < 2:
                raise RuntimeError("首尾帧模式需要 2 张图片")
            last_name = _copy_asset_to_input(images[1], "last")
            h3["inputs"]["last_frame"] = _add_load_image(graph, last_name)
        else:
            h3["inputs"].pop("last_frame", None)
    elif actual_mode == "t2va":
        graph = _load_template("t2va")
        _, h3 = _find_node(graph, "MiniMaxH3ImageToVideo")
    else:
        if not (images or videos or audios):
            raise RuntimeError("全能参考模式至少需要 1 个参考素材")
        if len(images) > 9 or len(videos) > 3 or len(audios) > 3:
            raise RuntimeError("参考素材超过 H3 上限：图片≤9、视频≤3、音频≤3")
        if len(images) + len(videos) + len(audios) > 12:
            raise RuntimeError("图片+视频+音频合计不能超过 12 个")
        graph = _load_template("ref2va")
        _, h3 = _find_node(graph, "MiniMaxH3ReferenceToVideo")
        for i, item in enumerate(images):
            fname = _copy_asset_to_input(item, f"refimg{i+1}")
            h3["inputs"][f"ref_images.ref_image_{i}"] = _add_load_image(graph, fname)
        for i, item in enumerate(videos):
            fname = _copy_asset_to_input(item, f"refvideo{i+1}")
            vid = _add_load_video(graph, fname)
            h3["inputs"][f"ref_videos.ref_video_{i}"] = [vid, 0]
            h3["inputs"][f"ref_video_audios.ref_video_audio_{i}"] = [vid, 2]
        for i, item in enumerate(audios):
            fname = _copy_asset_to_input(item, f"refaudio{i+1}")
            h3["inputs"][f"ref_audios.ref_audio_{i}"] = _add_load_audio(graph, fname)
        h3["inputs"]["ref_image_size"] = str(ref_image_size or "match")

    if not h3:
        raise RuntimeError("工作流模板缺少 MiniMax H3 主节点")
    w, h = normalize_size(width, height, ratio, mp, first_ratio)
    h3["inputs"].update({
        "prompt": str(prompt or "").strip(),
        "width": int(w), "height": int(h), "length": int(frames),
    })
    if not h3["inputs"]["prompt"]:
        raise RuntimeError("视频提示词不能为空")

    _, noise = _find_node(graph, "RandomNoise")
    if noise:
        noise["inputs"]["noise_seed"] = sd
    _, combine = _find_node(graph, "VHS_VideoCombine")
    if combine:
        combine["inputs"]["filename_prefix"] = prefix
    _validate_graph(graph)
    return graph, {
        "mode": actual_mode, "width": w, "height": h,
        "seconds": int(round(float(seconds))), "frames": frames, "seed": sd,
        "filenamePrefix": prefix,
    }


def _validate_graph(graph):
    if not isinstance(graph, dict) or not graph:
        raise RuntimeError("空工作流")
    for nid, node in graph.items():
        if not isinstance(node, dict) or not node.get("class_type") or not isinstance(node.get("inputs"), dict):
            raise RuntimeError(f"工作流节点 {nid} 格式错误")
        for key, value in node["inputs"].items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                if value[0] not in graph:
                    raise RuntimeError(f"节点 {nid}.{key} 引用了不存在的节点 {value[0]}")
    return True


# -------------------- Tasks --------------------

def _task_file(task_dir):
    return Path(task_dir) / "task.json"


def _save_task(task):
    folder = Path(task["dir"])
    folder.mkdir(parents=True, exist_ok=True)
    tmp = _task_file(folder).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(_task_file(folder)))


def _public_task(task):
    keep = {
        "id", "status", "stage", "detail", "createdAt", "startedAt", "endedAt",
        "mode", "width", "height", "seconds", "frames", "seed", "promptId",
        "error", "outputs", "primary", "folder", "cancelRequested",
    }
    out = {k: copy.deepcopy(v) for k, v in task.items() if k in keep}
    # 提示词要给前端看（待生产列表/历史成品的提示词板块，用户 2026-09-02）。
    # 参考图是 base64，很大，只给张数不给内容——要沿用就传 reuseTask。
    req = task.get("request") or {}
    out["prompt"] = str(req.get("prompt") or "")
    out["imageCount"] = len(req.get("images") or [])
    out["ratio"] = req.get("ratio")
    out["mp"] = req.get("mp")
    if not out.get("mode"):
        out["mode"] = req.get("mode")
    if not out.get("seconds"):
        out["seconds"] = req.get("seconds")
    return out


def _new_task_dir(task_id):
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    path = VIDEO_OUTPUT_ROOT / f"{stamp}_{task_id[:6]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _history(prompt_id):
    try:
        return _http_json(H3_URL + "/history/" + urllib.parse.quote(str(prompt_id)), timeout=12)
    except Exception:
        data = _http_json(H3_URL + "/history?max_items=30", timeout=12)
        if str(prompt_id) in data:
            return {str(prompt_id): data[str(prompt_id)]}
        return {}


def _collect_file_descriptors(obj, found=None):
    found = found if found is not None else []
    if isinstance(obj, dict):
        if isinstance(obj.get("filename"), str):
            found.append({
                "filename": obj.get("filename"),
                "subfolder": obj.get("subfolder") or "",
                "type": obj.get("type") or "output",
            })
        for value in obj.values():
            _collect_file_descriptors(value, found)
    elif isinstance(obj, list):
        for value in obj:
            _collect_file_descriptors(value, found)
    return found


def _copy_outputs(history_item, task_dir):
    descriptors = _collect_file_descriptors(history_item.get("outputs", {}))
    seen = set()
    copied = []
    for item in descriptors:
        key = (item["filename"], item["subfolder"], item["type"])
        if key in seen:
            continue
        seen.add(key)
        root = H3_OUTPUT if item["type"] == "output" else (H3_COMFY_DIR / item["type"])
        src = root / item["subfolder"] / item["filename"]
        if not src.is_file():
            continue
        name = src.name
        dst = Path(task_dir) / name
        n = 2
        while dst.exists() and dst.resolve() != src.resolve():
            dst = Path(task_dir) / f"{src.stem}_{n}{src.suffix}"
            n += 1
        if dst.resolve() != src.resolve():
            shutil.copy2(str(src), str(dst))
        copied.append(dst.name)
    # 兼容 VHS 只在 history 里暴露 mp4 的情况；优先带 audio 的成品。
    mp4s = [x for x in copied if x.lower().endswith(".mp4")]
    primary = ""
    if mp4s:
        primary = next((x for x in mp4s if "audio" in x.lower()), mp4s[0])
    return copied, primary


class VideoTaskManager:
    def __init__(self):
        self.tasks = {}
        self.q = queue.Queue()
        self.current_id = None
        self.stop_event = threading.Event()
        self.worker = threading.Thread(target=self._worker_loop, daemon=True, name="h3-video-worker")
        self._load_existing()
        self.worker.start()

    def _load_existing(self):
        for p in VIDEO_OUTPUT_ROOT.glob("*/task.json"):
            try:
                task = json.loads(p.read_text(encoding="utf-8"))
                if task.get("status") in ("queued", "loading", "running"):
                    task["status"] = "failed"
                    task["stage"] = "interrupted"
                    task["error"] = "工作台上次退出时任务未完成"
                    task["endedAt"] = time.time()
                    _save_task(task)
                self.tasks[str(task.get("id"))] = task
            except Exception:
                pass

    def submit(self, payload):
        payload = dict(payload or {})
        # 【沿用历史任务的参考图】历史成品里改了提示词再出一版：
        # 提示词用新的，参考图/模式/比例/像素/时长缺什么就从那条任务里补
        # （用户 2026-09-02：生成过的留在历史里，再生成的是新一条）。
        reuse = str(payload.pop("reuseTask", "") or "")
        if reuse:
            with STATE_LOCK:
                old = (self.tasks.get(reuse) or {}).get("request") or {}
            for k in ("images", "videos", "audios", "mode", "ratio", "mp", "seconds", "imageRatio"):
                if payload.get(k) in (None, "", [], {}) and old.get(k) not in (None, ""):
                    payload[k] = copy.deepcopy(old[k])
            if not str(payload.get("prompt") or "").strip():
                payload["prompt"] = old.get("prompt") or ""
        task_id = uuid.uuid4().hex
        folder = _new_task_dir(task_id)
        task = {
            "id": task_id,
            "status": "queued", "stage": "queued", "detail": "等待生成",
            "createdAt": time.time(), "startedAt": 0, "endedAt": 0,
            "promptId": "", "error": "", "outputs": [], "primary": "",
            "folder": folder.name, "dir": str(folder), "cancelRequested": False,
            "request": copy.deepcopy(payload),
        }
        with STATE_LOCK:
            self.tasks[task_id] = task
            _save_task(task)
        self.q.put(task_id)
        return _public_task(task)

    def get(self, task_id=None):
        with STATE_LOCK:
            if task_id:
                task = self.tasks.get(str(task_id))
                return _public_task(task) if task else None
            if self.current_id and self.current_id in self.tasks:
                return _public_task(self.tasks[self.current_id])
            return None

    def list(self, limit=50):
        with STATE_LOCK:
            rows = sorted(self.tasks.values(), key=lambda x: x.get("createdAt", 0), reverse=True)
            return [_public_task(x) for x in rows[:max(1, int(limit))]]

    def busy(self):
        with STATE_LOCK:
            if self.current_id:
                t = self.tasks.get(self.current_id) or {}
                if t.get("status") in ("loading", "running"):
                    return True
            return False

    def cancel(self, task_id=None):
        with STATE_LOCK:
            tid = str(task_id or self.current_id or "")
            task = self.tasks.get(tid)
            if not task:
                return False
            task["cancelRequested"] = True
            task["detail"] = "正在停止…"
            _save_task(task)
            is_current = tid == self.current_id
        if is_current:
            try:
                _http_json(H3_URL + "/interrupt", {}, method="POST", timeout=8)
            except Exception:
                pass
        return True

    def stop_all(self):
        with STATE_LOCK:
            for task in self.tasks.values():
                if task.get("status") in ("queued", "loading", "running"):
                    task["cancelRequested"] = True
                    if task.get("status") == "queued":
                        task["status"] = "cancelled"
                        task["stage"] = "cancelled"
                        task["endedAt"] = time.time()
                    _save_task(task)
        try:
            _http_json(H3_URL + "/interrupt", {}, method="POST", timeout=8)
        except Exception:
            pass
        return True

    def _update(self, task, **kwargs):
        with STATE_LOCK:
            task.update(kwargs)
            _save_task(task)

    def _worker_loop(self):
        while True:
            tid = self.q.get()
            try:
                with STATE_LOCK:
                    task = self.tasks.get(tid)
                    if not task or task.get("status") == "cancelled" or task.get("cancelRequested"):
                        continue
                    self.current_id = tid
                self._run_task(task)
            except Exception as e:
                if task:
                    self._update(task, status="failed", stage="failed", detail="生成失败", error=str(e), endedAt=time.time())
            finally:
                with STATE_LOCK:
                    if self.current_id == tid:
                        self.current_id = None
                self.q.task_done()

    def _run_task(self, task):
        req = task.get("request") or {}
        from cores.age_policy import assert_model_request
        assert_model_request(req.get("prompt"), media="video")
        if os.environ.get("V41_NO_MODEL"):
            raise RuntimeError("V41_NO_MODEL=1：零模型测试不许调模型（内嵌视频入口）")
        if task.get("cancelRequested"):
            self._update(task, status="cancelled", stage="cancelled", endedAt=time.time())
            return
        self._update(task, status="loading", stage="gpu", detail="正在切换显存并启动 MiniMax H3…", startedAt=time.time())
        service_start()
        if task.get("cancelRequested"):
            self._update(task, status="cancelled", stage="cancelled", endedAt=time.time())
            return

        self._update(task, stage="workflow", detail="正在准备 H3 工作流…")
        prefix = f"MiniMaxH3/workbench_{task['id'][:10]}"
        graph, meta = build_workflow(
            req.get("prompt", ""), mode=req.get("mode", "auto"),
            images=req.get("images") or [], videos=req.get("videos") or [], audios=req.get("audios") or [],
            width=req.get("width"), height=req.get("height"), ratio=req.get("ratio", "16:9"),
            mp=req.get("mp", 0.7), image_ratio=req.get("imageRatio"),
            seconds=req.get("seconds", 5), seed=req.get("seed"), filename_prefix=prefix,
            ref_image_size=req.get("ref_image_size", "match"),
        )
        self._update(task, **meta)
        # 保存真正提交给 H3 的 API workflow，后续每条片都可复现。
        Path(task["dir"], "workflow_api.json").write_text(
            json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
        Path(task["dir"], "prompt.txt").write_text(str(req.get("prompt") or ""), encoding="utf-8")

        self._update(task, status="running", stage="submit", detail="正在提交 H3 任务…")
        result = _http_json(H3_URL + "/prompt", {"prompt": graph}, method="POST", timeout=30)
        prompt_id = str(result.get("prompt_id") or "")
        if not prompt_id:
            raise RuntimeError("H3 ComfyUI 没有返回 prompt_id：" + json.dumps(result, ensure_ascii=False)[:300])
        self._update(task, promptId=prompt_id, stage="running", detail="H3 正在生成音视频…")

        started = time.time()
        while True:
            if task.get("cancelRequested"):
                try:
                    _http_json(H3_URL + "/interrupt", {}, method="POST", timeout=8)
                except Exception:
                    pass
                self._update(task, status="cancelled", stage="cancelled", detail="已取消", endedAt=time.time())
                return
            hist = _history(prompt_id)
            item = hist.get(prompt_id) if isinstance(hist, dict) else None
            if item:
                status_info = item.get("status") or {}
                status_str = str(status_info.get("status_str") or "")
                completed = bool(status_info.get("completed"))
                if status_str == "error":
                    msgs = status_info.get("messages") or []
                    raise RuntimeError("H3 工作流执行失败：" + str(msgs)[-1200:])
                if completed or item.get("outputs"):
                    outputs, primary = _copy_outputs(item, task["dir"])
                    if outputs:
                        self._update(
                            task, status="finished", stage="finished", detail="生成完成",
                            outputs=outputs, primary=primary, endedAt=time.time(),
                            durationCostSeconds=round(time.time() - started, 1),
                        )
                        return
            waited = int(time.time() - started)
            self._update(task, detail=f"H3 正在生成音视频，已等待 {waited} 秒…")
            time.sleep(3)


MANAGER = VideoTaskManager()


def submit_task(payload):
    return MANAGER.submit(payload or {})


def task_status(task_id=None):
    return MANAGER.get(task_id)


def list_tasks(limit=50):
    return MANAGER.list(limit)


def is_busy():
    return MANAGER.busy()


def cancel_task(task_id=None):
    return MANAGER.cancel(task_id)


def stop_all():
    MANAGER.stop_all()
    try:
        service_stop(kill=True)
    except Exception:
        pass
    return True


def task_file_path(task_id, name=None):
    with STATE_LOCK:
        task = MANAGER.tasks.get(str(task_id))
        if not task:
            return None
        folder = Path(task["dir"]).resolve()
    filename = str(name or task.get("primary") or "")
    if not filename:
        return None
    path = (folder / filename).resolve()
    try:
        path.relative_to(folder)
    except Exception:
        return None
    return path if path.is_file() else None
