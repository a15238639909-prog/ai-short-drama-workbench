# -*- coding: utf-8 -*-
"""qwen_client.py — V4.1 clean-room Qwen3.6 文本客户端（llama-server :8080）。"""
import json, os, subprocess, threading, time, urllib.request, urllib.error

QWEN_URL = os.environ.get("V41_QWEN_URL", "http://127.0.0.1:8080")
QWEN_MODEL = "qwen3.6-35b-a3b"
# 【本机路径统一走 cores/paths.py】换机器时改 config.json 或设环境变量就行，
# 不用翻代码（2026-09-05 独立抽取时定）。
try:
    from cores import paths as _paths
    SERVER_EXE = _paths.get("llama_server_exe")
    MODEL_GGUF = _paths.get("model_gguf")
    MMPROJ_GGUF = _paths.get("mmproj_gguf")
    LOG = _paths.get("llama_log")
    ERR_LOG = _paths.get("llama_err_log")
except Exception:                                  # paths 拿不到就当没配（P447：不再内置本机路径）
    SERVER_EXE = os.environ.get("V41_LLAMA_EXE", "")
    MODEL_GGUF = os.environ.get("V41_MODEL_GGUF", "")
    MMPROJ_GGUF = os.environ.get("V41_MMPROJ_GGUF", "")
    LOG = os.environ.get("V41_LLAMA_LOG", "")
    ERR_LOG = os.environ.get("V41_LLAMA_ERR_LOG", "")
_START_LOCK = threading.Lock()
_PROC = None
_REASONING_OVERRIDE = None

def set_reasoning(v):
    """测试/策略用：全局覆盖 enable_thinking（None=按调用参数）。"""
    global _REASONING_OVERRIDE
    _REASONING_OVERRIDE = bool(v) if v is not None else None


_TRACE_PATH = os.environ.get("V41_QWEN_TRACE") or ""
_TRACE_LOCK = threading.Lock()
_TRACE_N = [0]


def _trace(system, user, out, temp, mt, secs):
    """把**每一次**模型调用原样落盘：指令词原文、喂进去的原文、返回的原文。

    只在环境变量 V41_QWEN_TRACE 指了文件时才写。一个字都不截断——
    这份日志的用途就是让人逐字核对链条上到底给 Qwen 看了什么
    （2026-08-30 用户要"全部的原文"）。
    """
    if not _TRACE_PATH:
        return
    try:
        with _TRACE_LOCK:
            _TRACE_N[0] += 1
            n = _TRACE_N[0]
            with open(_TRACE_PATH, "a", encoding="utf-8") as f:
                f.write("\n\n" + "=" * 100 + "\n")
                f.write("【第 %d 次调用】%s ｜ temperature=%s ｜ max_tokens=%s "
                        "｜ 耗时 %s 秒\n" % (n, time.strftime("%H:%M:%S"),
                                            temp, mt, secs))
                f.write("=" * 100 + "\n")
                f.write("\n———————— 指令词（system，Qwen 的身份和规矩）————————\n")
                f.write(str(system or ""))
                f.write("\n\n———————— 喂给它的原文（user）————————\n")
                f.write(str(user or ""))
                f.write("\n\n———————— 它返回的原文（assistant）————————\n")
                f.write(str(out or ""))
                f.write("\n")
    except Exception:
        pass

def up():
    try:
        urllib.request.urlopen(QWEN_URL + "/v1/models", timeout=8).close()
        return True
    except Exception:
        return False


def _game_mode_guard():
    """游戏模式暂停旗立着时拒绝上卡——用户在打游戏，显卡不能碰。"""
    from pathlib import Path as _P
    if (_P(__file__).resolve().parent.parent / "cache" / "game_mode.flag").exists():
        raise RuntimeError("游戏模式暂停中——点右上角「恢复任务」再继续")

def start():
    if os.environ.get("V41_NO_MODEL"):
        raise RuntimeError("V41_NO_MODEL=1：零模型测试不许调模型（qwen_client.start）")
    _game_mode_guard()
    global _PROC
    with _START_LOCK:
        if up():
            return True
        for _ in range(8):
            if up():
                return True
            time.sleep(1.5)
        # GPU 互斥：Qwen 前停止 ComfyUI 与 H3
        # P272 总闸（跨进程占用 / 清僵尸 llama / 内存够不够）——这三条不许吞异常，
        # 吞了就等于没闸：事故那次就是别的进程照样拉起了第二个 llama-server
        from . import gpu_manager
        gpu_manager.guard("qwen", note="llama-server:8080")
        try:
            gpu_manager.claim("qwen")
            time.sleep(2)
        except Exception:
            pass
        if not (os.path.exists(SERVER_EXE) and os.path.exists(MODEL_GGUF) and os.path.exists(MMPROJ_GGUF)):
            raise RuntimeError("Qwen3.6 模型文件缺失")
        # 默认全部层上 GPU（-ngl 999）。要一边打游戏一边跑的时候，用
        # V41_QWEN_NGL 把层数压下来给游戏留显存——层数少了会明显变慢，
        # 只在需要共用显卡时设（2026-08-31 用户要求）。
        ngl = str(os.environ.get("V41_QWEN_NGL") or "999").strip() or "999"
        ctx = str(os.environ.get("V41_QWEN_CTX") or "16384").strip() or "16384"
        args = [SERVER_EXE, "-m", MODEL_GGUF, "--mmproj", MMPROJ_GGUF,
                "-ngl", ngl, "-c", ctx, "-n", ctx, "-np", "1",
                "-fa", "on", "--host", "127.0.0.1", "--port", "8080", "--jinja"]
        # P271：上一次没起来的进程还在就先杀掉——不然每次重试多一个 llama-server 一起加载 20 GB 模型，
        # 内存打满谁也起不来（实测攒到 8 个）
        if _PROC is not None and _PROC.poll() is None:
            try:
                _PROC.kill()
                _PROC.wait(timeout=10)
            except Exception:
                pass
            _PROC = None
        out = open(LOG, "a", encoding="utf-8", errors="ignore")
        err = open(ERR_LOG, "a", encoding="utf-8", errors="ignore")
        _PROC = subprocess.Popen(args, creationflags=0x08000000, stdout=out, stderr=err)
        try:
            _wait = max(60, int(os.environ.get("V41_QWEN_START_WAIT") or 180))
        except Exception:
            _wait = 180
        for _ in range(_wait // 2):
            time.sleep(2)
            if up():
                return True
            if _PROC.poll() is not None:
                break                                   # 进程自己退了（端口被占/显存不够），别干等
        # 没就绪：把这个进程收掉再报错，不留僵尸
        try:
            if _PROC.poll() is None:
                _PROC.kill()
                _PROC.wait(timeout=10)
        except Exception:
            pass
        _rc = _PROC.poll()
        _PROC = None
        raise RuntimeError("Qwen3.6 %d 秒内未就绪%s" % (_wait, ("（进程已退出，退出码 %s，看 %s）" % (_rc, ERR_LOG)) if _rc not in (None,) else ""))

def free():
    global _PROC
    if _PROC is not None:
        try:
            _PROC.terminate()
        except Exception:
            pass
        _PROC = None

def _free_idle_gpu():
    """GPU 互斥：Qwen 生成前，若 H3/Comfy 已就绪且队列空闲，先释放显存，避免 Qwen 显存不足卡死。

    【地雷修复】批量生成的两段之间队列会瞬时为空——这时有人调 Qwen，
    H3 就被"顺手"清掉，正在收尾的段直接丢。现在有视频任务登记
    （cache/current_task.json 存在）时绝不动 H3。"""
    from pathlib import Path as _P
    if (_P(__file__).resolve().parent.parent / "cache" / "current_task.json").exists():
        return
    try:
        from . import gpu_manager
        from . import h3_client
        from . import krea_client
        for mod, url in ((h3_client, h3_client.H3_URL), (krea_client, krea_client.COMFY_URL)):
            try:
                if mod.up():
                    q = json.loads(urllib.request.urlopen(url + "/queue", timeout=8).read().decode("utf-8", "replace"))
                    if not (q.get("queue_running") or q.get("queue_pending")):
                        gpu_manager.stop_h3() if mod is h3_client else gpu_manager.stop_comfy()
            except Exception:
                pass
    except Exception:
        pass

SAMPLING_OVERRIDE = None      # P141：对照实验时由脚本设置，正常运行为 None




# ─────────────── P283：每次真实请求前的两道闸（用户 2026-09-11 定）───────────────
PUBG_EXES = ("TslGame.exe", "TslGame_BE.exe", "PUBG.exe")


def pubg_running():
    """PUBG 在不在跑：按进程名查（TslGame.exe 是 PUBG 的主程序）。查不到进程表就当没在跑，别误伤。"""
    try:
        out = subprocess.check_output(["tasklist", "/NH"], text=True, errors="replace", timeout=15,
                                      creationflags=0x08000000)
    except Exception:
        return False
    low = out.lower()
    return any(x.lower() in low for x in PUBG_EXES)


def _pubg_guard():
    if pubg_running():
        raise RuntimeError("PUBG 正在运行，本次不调模型（显卡让给游戏；关掉游戏再点）")


def _budget_path():
    from pathlib import Path as _P
    return _P(__file__).resolve().parent.parent / "cache" / "call_budget.json"


def budget_state():
    """本轮调用上限的账本：{limit, used, calls:[{n, ts, task}]}。没设 V41_CALL_BUDGET 返回 None。"""
    lim = str(os.environ.get("V41_CALL_BUDGET") or "").strip()
    if not lim:
        return None
    try:
        lim = int(lim)
    except Exception:
        return None
    p = _budget_path()
    data = {"limit": lim, "used": 0, "calls": []}
    try:
        if p.exists():
            old = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(old, dict):
                data.update({k: old.get(k, data[k]) for k in ("used", "calls")})
    except Exception:
        pass
    data["limit"] = lim
    return data


def _budget_take(task=""):
    """记一次真实请求；到上限就报错——用户定的硬上限，重试也算一次。账本落盘，重启不清零。"""
    data = budget_state()
    if data is None:
        return
    if int(data.get("used") or 0) >= int(data["limit"]):
        raise RuntimeError("已达本轮模型调用上限 %d 次，按约定停止，不再增加调用" % data["limit"])
    data["used"] = int(data.get("used") or 0) + 1
    data.setdefault("calls", []).append({"n": data["used"], "ts": time.strftime("%m-%d %H:%M:%S"),
                                         "task": str(task or "")[:60]})
    try:
        p = _budget_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def budget_reset(limit=None):
    """开新一轮：清账本（limit 传了就一并写进环境变量）。"""
    if limit is not None:
        os.environ["V41_CALL_BUDGET"] = str(int(limit))
    try:
        p = _budget_path()
        if p.exists():
            p.unlink()
    except Exception:
        pass

def chat(system, user, temperature=0.7, max_tokens=3600, timeout=1800,
         reasoning=None, return_meta=False):
    from cores.age_policy import assert_model_request, assert_model_response

    if os.environ.get("V41_NO_MODEL"):
        raise RuntimeError("V41_NO_MODEL=1：零模型测试不许调模型（qwen_client.chat）")
    # P273：已经起着的模型也要过占卡闸——别的进程直接调 chat() 同样是在抢卡
    # （事故那次的诊断脚本就是这么插进出片任务里的）。同一个进程永远放行。
    _pubg_guard()                                   # P283：用户在打 PUBG 就不碰显卡
    _budget_take(str(system or "")[:40])            # P283：本轮上限，到了就停
    from . import gpu_manager as _gm
    _gm.acquire("qwen", note="chat")
    if not up():
        start()
    else:
        _free_idle_gpu()
        # 【Qwen 在跑就不许 Comfy 同住】2026-09-05 实测：_free_idle_gpu 查 Comfy 队列
        # 失败会静默放过，Qwen 和 Comfy 同住 24 GB 的卡；之后 Comfy 接着采样到第二段
        # 放大就崩（10061）。单卡规则是同时只准一个重模型——有视频任务登记时才留 H3，
        # Comfy 没有这种理由，直接清。
        try:
            from . import krea_client as _kc, gpu_manager as _gm
            if _kc.up(timeout=2):
                _gm.stop_comfy()
                time.sleep(2)
        except Exception:
            pass
    r = reasoning if _REASONING_OVERRIDE is None else _REASONING_OVERRIDE
    payload = {
        "model": QWEN_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": bool(r)},
    }
    if SAMPLING_OVERRIDE:                                   # 对照实验用（P141）：{"top_p":0.8,"top_k":20,"min_p":0,"presence_penalty":1.5,"temperature":0.7}
        for _k, _v in dict(SAMPLING_OVERRIDE).items():
            if _v is not None:
                payload[_k] = _v
    body = json.dumps(payload).encode("utf-8")
    t0 = time.time()
    req = urllib.request.Request(QWEN_URL + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        obj = json.loads(response.read().decode("utf-8", "replace"))
    choice = obj["choices"][0]
    msg = choice.get("message") or {}
    content = str(msg.get("content") or "")

    _trace(system, user, content, temperature, max_tokens,
           round(time.time() - t0, 1))
    if return_meta:
        meta = {
            "reasoning": bool(r),
            "model": obj.get("model"),
            "elapsed_s": round(time.time() - t0, 3),
            "usage": obj.get("usage"),
            "finish_reason": choice.get("finish_reason"),
            "content_len": len(content),
            "reasoning_content_len": len(str(msg.get("reasoning_content") or "")),
            "reasoning_content": str(msg.get("reasoning_content") or ""),
        }
        return content, meta
    return content

def chat_for(task_key, system, user, temperature=None, max_tokens=None,
             timeout=1800, return_meta=False):
    """按 Reasoning Policy 调用（生产代码统一入口，不各自写 thinking 参数）。"""
    from cores import reasoning_policy
    p = reasoning_policy.get_policy(task_key)
    return chat(
        system, user,
        temperature=temperature if temperature is not None else p["temperature"],
        max_tokens=max_tokens if max_tokens is not None else p["max_tokens"],
        timeout=timeout,
        reasoning=p["enable_thinking"],
        return_meta=return_meta,
    )
