# -*- coding: utf-8 -*-
"""gpu_api.py — 游戏模式：一键停掉所有本地模型、释放显存、暂停任务。

用户要打游戏，显卡必须完全让出来。点「游戏模式」：
    杀 Qwen / ComfyUI(Krea) / H3 → 记下正在跑的任务 → 立起暂停旗
暂停旗立着时，三个模型的 start 都会拒绝启动（models/* 里各有检查）。
点「恢复任务」：拔旗，把暂停时被打断的段重新提交生成。
"""
import json
import threading
import urllib.request
from pathlib import Path

from api import post
from api._shared import _RESP, ctx

globals().update(ctx())

ROOT = Path(__file__).resolve().parent.parent
FLAG = ROOT / "cache" / "game_mode.flag"
TASK = ROOT / "cache" / "current_task.json"


def paused():
    return FLAG.exists()


@post("/api/gpu/pause")
def r_pause(h, path, d):
    from models import gpu_manager
    # 记住暂停时正在跑什么（gen_segment 开跑前会写 current_task.json）
    pending = None
    if TASK.exists():
        try:
            pending = json.loads(TASK.read_text(encoding="utf-8"))
        except Exception:
            pending = None
    FLAG.parent.mkdir(parents=True, exist_ok=True)
    FLAG.write_text(json.dumps({"paused_task": pending}, ensure_ascii=False),
                    encoding="utf-8")
    killed = []
    for fn, name in ((gpu_manager.stop_qwen, "qwen"),
                     (gpu_manager.stop_comfy, "krea"),
                     (gpu_manager.stop_h3, "h3")):
        try:
            if fn():
                killed.append(name)
        except Exception:
            pass
    return _RESP({"ok": True, "data": {"paused": True, "killed": killed,
                                       "paused_task": pending}})


@post("/api/gpu/resume")
def r_resume(h, path, d):
    info = {}
    if FLAG.exists():
        try:
            info = json.loads(FLAG.read_text(encoding="utf-8"))
        except Exception:
            info = {}
        FLAG.unlink()
    task = info.get("paused_task")
    if task:
        # 被打断的段重新提交。自己 POST 自己，走和页面按钮完全相同的路
        def _refire():
            try:
                body = json.dumps({"scene_no": task.get("scene_no"),
                                   "seg_no": task.get("seg_no"),
                                   "hd": task.get("hd") or False}).encode("utf-8")
                req = urllib.request.Request(
                    "http://127.0.0.1:8853/api/timeline/%s/generate-segment" % task.get("sid"),
                    data=body, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=3600).close()
            except Exception:
                pass
        threading.Thread(target=_refire, daemon=True).start()
    return _RESP({"ok": True, "data": {"paused": False, "refired": bool(task),
                                       "task": task}})
