# -*- coding: utf-8 -*-
"""krea_client.py — V4.1 clean-room Krea/ComfyUI 客户端。
约束：Krea 无参考图输入；只注入文字（正向/负向）+ 尺寸 + 种子 + 采样参数。"""
import json, os, shutil, subprocess, threading, time, urllib.request, urllib.error, uuid
from pathlib import Path

COMFY_URL = os.environ.get("V41_COMFY_URL", "http://127.0.0.1:8188")
try:
    from cores import paths as _paths
    COMFY_DIR = _paths.get("comfy_dir")
except Exception:
    COMFY_DIR = os.environ.get("V41_COMFY_DIR", "")
# 两段式工作流：从用户实跑成功的成品图 PNG 元数据里原样提取（Krea2-2ST-…）。
# 1920×1080 出底 → LatentUpscaleBy bislerp ×1.5 → 第二段重采样细化 → ColorMatch 回色
# 成品 2880×1616。这是用户验证过能出高清的那套，不要改采样参数。
WORKFLOW = Path(__file__).resolve().parent.parent / "models" / "krea2_2stage_workflow.json"

# 工作流里各节点的固定编号（来自模板，改模板时同步改这里）
N_POSITIVE = "264"      # CLIPTextEncode 正向
N_LATENT = "261"        # EmptyLatentImage 出底尺寸
N_SEED = "268"          # Seed (rgthree)
N_STAGE1 = "202"        # KSamplerAdvanced 第一段
N_STAGE2 = "211"        # KSamplerAdvanced 第二段
UPSCALE = 1.5           # LatentUpscaleBy 的倍数，成品尺寸 = 出底 × 这个数
OUTPUT_ROOT = Path(__file__).resolve().parent.parent / "outputs" / "krea"
# 【负向词在这套工作流里不起作用】两个采样器的 negative 都接 ConditioningZeroOut，
# 而且 cfg=1.0 时无分类器引导，负向分支根本不参与计算。
# 用户原图里那个「负面条件」节点也是悬空的。留着这个常量只为兼容老调用，不再有实际效果。
# 想赶走某样东西，只能在正向词里正面描述它不在（"纯白背景，画面中只有这一个人物"）。
NEGATIVE = ""

_START_LOCK = threading.Lock()

def up(timeout=5):
    try:
        urllib.request.urlopen(COMFY_URL + "/system_stats", timeout=timeout).close()
        return True
    except Exception:
        return False


def _game_mode_guard():
    """游戏模式暂停旗立着时拒绝上卡——用户在打游戏，显卡不能碰。"""
    from pathlib import Path as _P
    if (_P(__file__).resolve().parent.parent / "cache" / "game_mode.flag").exists():
        raise RuntimeError("游戏模式暂停中——点右上角「恢复任务」再继续")

def start_comfy(timeout=180):
    if os.environ.get("V41_NO_MODEL"):
        raise RuntimeError("V41_NO_MODEL=1：零模型测试不许调模型（krea_client.start_comfy）")
    _game_mode_guard()
    with _START_LOCK:
        if up():
            return True
        # 单卡互斥：Qwen 占着显存 ComfyUI 就加载不动，180 秒都起不来。
        # 之前互斥只有单向（Qwen 起前赶别人，别人起前不赶 Qwen），这里补上。
        from . import gpu_manager
        gpu_manager.guard("krea", note="ComfyUI:8188")        # P272 跨进程占用/僵尸/内存总闸
        gpu_manager.claim("krea")
        py = os.path.join(COMFY_DIR, "python", "python.exe")
        if not os.path.exists(py):
            raise RuntimeError("ComfyUI python 不存在：" + py)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        args = [py, "-s", r"ComfyUI\main.py", "--port", "8188", "--disable-auto-launch",
                "--disable-all-custom-nodes", "--whitelist-custom-nodes",
                "ComfyUI-ConditioningKrea2Rebalance", "ComfyUI-Lora-Manager", "rgthree-comfy",
                # 两段式工作流的 ColorMatchV2 在 KJNodes 里，少了它整条链起不来
                "ComfyUI-KJNodes",
                # 图像编辑（换装/换状态）：Krea2EditGroundedEncode + Krea2EditModelPatch
                "comfyui-krea2edit"]
        log = open(os.path.join(COMFY_DIR, "v41_comfy.log"), "ab")
        subprocess.Popen(args, cwd=COMFY_DIR, env=env, stdout=log, stderr=subprocess.STDOUT,
                         creationflags=0x08000000)
        for _ in range(int(timeout / 3)):
            time.sleep(3)
            if up():
                return True
        raise RuntimeError("ComfyUI 180 秒内未就绪")

def _http(method, path, data=None, timeout=60):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(COMFY_URL + path, data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

N_LATENT = "261"        # EmptyLatentImage：出底尺寸（P450：只有这一个节点接受外部改）


def _round16(v):
    return max(256, int(round(float(v) / 16.0)) * 16)


def generate(prompt, negative=NEGATIVE, width=None, height=None, seed=None, steps=None, cfg=None, base_w=None, base_h=None):
    from cores.age_policy import assert_model_request

    if os.environ.get("V41_NO_MODEL"):
        raise RuntimeError("V41_NO_MODEL=1：零模型测试不许调模型（krea_client.generate）")
    from . import gpu_manager as _gm
    _gm.acquire("krea", note="generate")               # P273 用模型也要过占卡闸
    """提交一次 Krea 生成。返回 {output_file, output_path}。请求中绝无图片输入。

    width/height/steps/cfg/negative 全部保留为兼容参数，**一律忽略**：
    工作流是用户实跑验证过的那套，节点参数不接受外部改动。
    成品固定 2880×1616（1920×1080 出底 ×1.5 潜空间放大）。
    """
    # GPU 互斥：Krea 前停止 H3 与 Qwen，避免显存争用
    from . import gpu_manager
    gpu_manager.stop_h3()
    killed = gpu_manager.stop_qwen()
    time.sleep(2)
    # 【Qwen 和 Comfy 同住过显卡，Comfy 必须重起】2026-09-05 实测三次：Comfy 在 Qwen
    # 加载期间没被清掉（两者并存），Qwen 一退出、Comfy 接着采样，到第二段放大时
    # 显存里被挤散的权重重载失败，进程崩掉 → 10061。刚杀了 llama-server 就把
    # 现成的 Comfy 也重起一遍，让它在干净的显卡上重新加载，多花一分钟换一张必出的图。
    if killed and up():
        gpu_manager.stop_comfy()
        time.sleep(3)
    if not up():
        start_comfy()
    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    seed = seed if seed is not None else uuid.uuid4().int % (2**31)
    # 【工作流原样不动】用户实跑验证过这套节点能出 2880×1616 高清图，
    # 尺寸、步数、cfg、采样器、放大倍数、回色强度一律不碰。
    # 这里只做两件事：换提示词、换种子（不换种子每次出的是同一张图）。
    wf[N_POSITIVE]["inputs"]["text"] = prompt
    wf[N_SEED]["inputs"]["seed"] = int(seed)
    wf[N_SEED].pop("is_changed", None)
    if base_w and base_h and N_LATENT in wf:
        # P450：出图词的比例 / 清晰度——只换出底宽高（16 的倍数），×1.5 放大、步数、cfg 不动
        wf[N_LATENT]["inputs"]["width"] = _round16(base_w)
        wf[N_LATENT]["inputs"]["height"] = _round16(base_h)
    result = _http("POST", "/prompt", {"prompt": wf}, timeout=60)
    prompt_id = result.get("prompt_id")
    if not prompt_id:
        raise RuntimeError("ComfyUI 未返回 prompt_id")
    # 轮询（冷启动/显存争用下执行可能较长）
    deadline = time.time() + 1800
    while time.time() < deadline:
        hist = _http("GET", "/history/" + prompt_id, timeout=30)
        item = hist.get(prompt_id)
        if item:
            if item.get("status", {}).get("status_str") == "error":
                raise RuntimeError("Krea 工作流执行失败")
            outs = item.get("outputs", {})
            for node_out in outs.values():
                for img in node_out.get("images", []):
                    fn = img.get("filename")
                    sub = img.get("subfolder") or ""
                    src = Path(COMFY_DIR) / "ComfyUI" / "output" / sub / fn
                    if src.exists():
                        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
                        dst = OUTPUT_ROOT / fn
                        shutil.copy2(str(src), str(dst))
                        return {"output_file": fn, "output_path": str(dst)}
        time.sleep(3)
    raise RuntimeError("Krea 生成超时")

# 【不默认启用】用户平时用 Krea2 就是默认负向词，不去改它。
# 这个函数留着备用（例如画风明确要颗粒感时可手动调用），但生成链路不自动调，
# 免得负向词变成一个会自己动的变量。
_GRAIN_WORDS = ("噪点", "颗粒", "胶片颗粒", "粗糙颗粒", "噪声纹理", "高ISO噪点", "彩色噪点")
_GRAIN_WANTED = ("噪点", "颗粒", "胶片", "粗粝", "抓拍", "手机", "复古游戏", "像素")


def negative_for(style_text=""):
    """按画风裁剪负向词。style_text 是选中画风的完整描述。"""
    words = [w.strip() for w in NEGATIVE.split(",") if w.strip()]
    if style_text and any(k in style_text for k in _GRAIN_WANTED):
        words = [w for w in words if w not in _GRAIN_WORDS]
    return ", ".join(words)
