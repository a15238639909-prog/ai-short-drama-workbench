# -*- coding: utf-8 -*-
"""krea_edit_client.py — Krea2 图像编辑（换装 / 换状态 / 局部改）。

来自用户网盘的「Krea2图像编辑流」，底模就是现在出设定图那个 krea2_turbo，
只多挂一个 identity_edit LoRA 和两个自定义节点（comfyui-krea2edit）：

    Krea2EditGroundedEncode   把指令和**原图一起**过 Qwen3-VL 编码
                              （纯文本编码会丢掉"看着这张图改"的那一半）
    Krea2EditModelPatch       把原图的 latent 打进模型，保住身份

**这解决的是设定图的老问题**：一张四视图基准图定死人是谁，
剧情要换装/受伤/湿身时，不重新抽卡，而是在基准图上改——脸不会变。

指令写法（来自工作流自带示例）：
    将角色的衣服换成白色吊带背心和黄色雨衣。
    将角色的衣服改成黄色，画面其余部分与原图保持一致。
    生成一张该人物的侧脸照，目光看向画面右侧，背景为中性灰色。
    在她的肩膀上添加一只彩色鹦鹉。
双图：把第二张图当 image_b
    将图1角色的衣服替换为图2的衣服。
"""
import json
import os
import shutil
import time
import urllib.request
import uuid
from pathlib import Path

from . import krea_client       # 复用它的 ComfyUI 启动/轮询/端口

COMFY_DIR = krea_client.COMFY_DIR
COMFY_URL = krea_client.COMFY_URL
INPUT_DIR = Path(COMFY_DIR) / "ComfyUI" / "input"
OUTPUT_ROOT = Path(__file__).resolve().parent.parent / "outputs" / "krea_edit"

UNET = r"krea2\Krea2-MuseByStable_v15Turbo_fp8.safetensors"
LORA = r"krea2\Krea2-编辑identity_edit_v1_2.safetensors"
CLIP = "qwen3vl_4b_fp8_scaled.safetensors"
VAE = "qwen_image_vae.safetensors"

STEPS, CFG = 10, 1.0        # 工作流原值，不动
GROUNDING_PX = 768          # 喂给 Qwen3-VL 的最长边


def _copy_in(path, tag):
    """把图复制进 ComfyUI 的 input，名字带 uuid 防缓存。"""
    src = Path(path)
    if not src.exists():
        raise RuntimeError("图不存在：" + str(path))
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    name = "edit_%s_%s%s" % (tag, uuid.uuid4().hex[:8], src.suffix or ".png")
    shutil.copy2(str(src), str(INPUT_DIR / name))
    return name


def _fit_1mp(path, mp=1.0):
    """按目标兆像素算出宽高，对齐到 16。保持原图比例。"""
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
    import math
    k = math.sqrt(mp * 1_000_000 / float(w * h))
    f = lambda v: max(256, int(round(v * k / 16.0)) * 16)
    return f(w), f(h)


def build_graph(image_path, prompt, seed=None, image_b=None, mp=1.0,
                negative="", steps=STEPS):
    """装配编辑工作流（API 格式）。"""
    import random
    w, h = _fit_1mp(image_path, mp)
    a_name = _copy_in(image_path, "a")
    g = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "LoraLoaderModelOnly",
              "inputs": {"model": ["1", 0], "lora_name": LORA, "strength_model": 1.0}},
        "3": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": CLIP, "type": "krea2", "device": "default"}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "5": {"class_type": "LoadImage", "inputs": {"image": a_name}},
        "6": {"class_type": "ImageScaleToTotalPixels",
              "inputs": {"image": ["5", 0], "upscale_method": "lanczos",
                         "megapixels": mp, "resolution_steps": 8}},
        "7": {"class_type": "VAEEncode", "inputs": {"pixels": ["6", 0], "vae": ["4", 0]}},
        # 身份补丁：把原图 latent 打进模型，这是"脸不变"的关键
        "8": {"class_type": "Krea2EditModelPatch",
              "inputs": {"model": ["2", 0], "source_latent": ["7", 0]}},
        "9": {"class_type": "Krea2EditGroundedEncode",
              "inputs": {"clip": ["3", 0], "prompt": str(prompt or ""),
                         "image": ["6", 0], "grounding_px": GROUNDING_PX}},
        "10": {"class_type": "Krea2EditGroundedEncode",
               "inputs": {"clip": ["3", 0], "prompt": str(negative or ""),
                          "image": ["6", 0], "grounding_px": GROUNDING_PX}},
        "11": {"class_type": "EmptySD3LatentImage",
               "inputs": {"width": w, "height": h, "batch_size": 1}},
        "12": {"class_type": "KSampler",
               "inputs": {"model": ["8", 0], "positive": ["9", 0], "negative": ["10", 0],
                          "latent_image": ["11", 0],
                          "seed": int(seed if seed is not None else random.randint(1, 2**48 - 1)),
                          "steps": int(steps), "cfg": CFG, "sampler_name": "euler",
                          "scheduler": "simple", "denoise": 1.0}},
        "13": {"class_type": "VAEDecode", "inputs": {"samples": ["12", 0], "vae": ["4", 0]}},
        "14": {"class_type": "SaveImage",
               "inputs": {"images": ["13", 0], "filename_prefix": "krea2_edit"}},
    }
    if image_b:
        b_name = _copy_in(image_b, "b")
        g["15"] = {"class_type": "LoadImage", "inputs": {"image": b_name}}
        g["16"] = {"class_type": "ImageScaleToTotalPixels",
                   "inputs": {"image": ["15", 0], "upscale_method": "lanczos",
                              "megapixels": mp, "resolution_steps": 8}}
        g["9"]["inputs"]["image_b"] = ["16", 0]
        g["10"]["inputs"]["image_b"] = ["16", 0]
    return g, {"width": w, "height": h, "seed": g["12"]["inputs"]["seed"]}


def edit(image_path, prompt, seed=None, image_b=None, mp=1.0, timeout=900,
         out_name=None):
    """在一张图上按指令编辑。返回 {output_path, seed, width, height}。"""
    from cores.age_policy import assert_model_request

    if __import__("os").environ.get("V41_NO_MODEL"):
        raise RuntimeError("V41_NO_MODEL=1：零模型测试不许调模型（图片编辑）")
    from . import gpu_manager
    if not krea_client.up():
        with gpu_manager.GPU_LOCK:
            gpu_manager.claim("krea")
        krea_client.start_comfy()
    graph, meta = build_graph(image_path, prompt, seed=seed, image_b=image_b, mp=mp)
    r = krea_client._http("POST", "/prompt", {"prompt": graph}, timeout=60)
    pid = r.get("prompt_id")
    if not pid:
        raise RuntimeError("ComfyUI 没返回 prompt_id：" + str(r)[:200])
    deadline = time.time() + timeout
    while time.time() < deadline:
        hist = krea_client._http("GET", "/history/" + pid, timeout=60)
        item = hist.get(pid)
        if item:
            st = item.get("status", {})
            if st.get("status_str") == "error":
                raise RuntimeError("编辑失败：" + str(st.get("messages"))[-400:])
            for node_out in (item.get("outputs") or {}).values():
                for im in node_out.get("images", []):
                    src = Path(COMFY_DIR) / "ComfyUI" / "output" / (im.get("subfolder") or "") / im["filename"]
                    if src.exists():
                        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
                        dst = OUTPUT_ROOT / (out_name or im["filename"])
                        shutil.copy2(str(src), str(dst))
                        return dict(meta, output_path=str(dst), output_file=dst.name)
        time.sleep(3)
    raise RuntimeError("编辑超时（%d 秒）" % timeout)
