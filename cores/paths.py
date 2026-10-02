# -*- coding: utf-8 -*-
"""本机路径配置：模型、ComfyUI、H3、旧版四大工具在哪。

【为什么单独一份】这些路径原来写死在 models/qwen_client.py、krea_client.py、
h3_client.py、api/history_api.py 里（F:\\夸克网盘\\…、F:\\ComfyUI-aki-v2、D:\\小说写作台）。
换一台机器这些路径都不一样，工具直接不能用，而且报的错还看不出是路径问题
（2026-09-05 独立抽取时查出）。

取值顺序：环境变量 > 包根目录下的 config.json > 内置默认值（开源版为空，必须在 config.json 或环境变量里配置）。

config.json 例子（放在 server.py 旁边）：

    {
      "llama_server_exe": "F:/llama.cpp/llama-server.exe",
      "model_gguf":       "F:/models/qwen.gguf",
      "mmproj_gguf":      "F:/models/mmproj.gguf",
      "comfy_dir":        "F:/ComfyUI-aki-v2",
      "comfy_output":     "F:/ComfyUI-aki-v2/ComfyUI/output",
      "h3_root":          "F:/Work-Fisher",
      "legacy_root":      "D:/小说写作台"
    }

哪一项留空或指向不存在的地方，用到它的那个功能会明说"没配好"，而不是抛一串英文。
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_DEFAULTS = {
    "llama_server_exe": "",
    "model_gguf": "",
    "mmproj_gguf": "",
    "llama_log": "",
    "llama_err_log": "",
    "comfy_dir": "",
    "comfy_output": "",
    "h3_root": "",
    "legacy_root": "",
}

_ENV = {
    "llama_server_exe": "V41_LLAMA_EXE",
    "model_gguf": "V41_MODEL_GGUF",
    "mmproj_gguf": "V41_MMPROJ_GGUF",
    "llama_log": "V41_LLAMA_LOG",
    "llama_err_log": "V41_LLAMA_ERR_LOG",
    "comfy_dir": "V41_COMFY_DIR",
    "comfy_output": "V41_COMFY_OUTPUT",
    "h3_root": "V41_H3_ROOT",
    "legacy_root": "V41_LEGACY_ROOT",
}

_cfg = {}
try:
    _p = ROOT / "config.json"
    if _p.exists():
        _cfg = json.loads(_p.read_text(encoding="utf-8")) or {}
except Exception:
    _cfg = {}


def get(key):
    """取一项路径。环境变量 > config.json > 内置默认。"""
    env = _ENV.get(key)
    if env:
        v = str(os.environ.get(env) or "").strip()
        if v:
            return v
    v = str(_cfg.get(key) or "").strip()
    if v:
        return v
    return _DEFAULTS.get(key, "")


def path(key):
    return Path(get(key))


def missing():
    """哪些路径配了但不存在。返回 [(项名, 值)]，给体检和出错提示用。"""
    out = []
    for k in _DEFAULTS:
        if k.endswith("_log"):
            continue                       # 日志文件不存在是正常的
        v = get(k)
        if v and not os.path.exists(v):
            out.append((k, v))
    return out


_CN = {
    "llama_server_exe": "llama-server 程序",
    "model_gguf": "文字模型文件",
    "mmproj_gguf": "多模态投影文件",
    "comfy_dir": "ComfyUI 目录",
    "comfy_output": "ComfyUI 输出目录",
    "h3_root": "H3 视频工作流目录",
    "legacy_root": "旧版四大工具目录",
}


def report():
    """一句人话说清哪几项没配好。"""
    bad = missing()
    if not bad:
        return "本机路径都配好了。"
    return "这几项路径找不到，改 config.json 或设环境变量：" + "；".join(
        "%s（%s）= %s" % (_CN.get(k, k), _ENV.get(k, ""), v) for k, v in bad)
