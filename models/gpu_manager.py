# -*- coding: utf-8 -*-
"""gpu_manager.py — V4.1 Runtime GPU 互斥（单卡：Qwen/Krea/H3 三类本地重任务互斥）。"""
import json, os, re, subprocess, time
from pathlib import Path as _Path

def pid_on_port(port):
    try:
        out = subprocess.check_output(["netstat", "-ano", "-p", "tcp"], text=True, errors="replace")
    except Exception:
        return None
    for line in out.splitlines():
        if (":%d " % port) in line and "LISTENING" in line:
            m = re.findall(r"(\d+)\s*$", line.strip())
            if m:
                return int(m[-1])
    return None

def stop_process_on_port(port, name=""):
    pid = pid_on_port(port)
    if pid:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=20)
        except Exception:
            pass
        time.sleep(2)
        return pid
    return None

def stop_h3():
    return stop_process_on_port(8190, "H3")

def stop_comfy():
    return stop_process_on_port(8188, "Krea/ComfyUI")

def stop_qwen():
    """停 Qwen：先杀监听 8080 的，再按进程名清掉所有 llama-server。

    【为什么要按名清】只按端口杀，两个 llama-server 同时存在时（服务和脚本各起了一个），
    没抢到 8080 的那个杀不掉，22 GB 显存一直占着，Comfy 加载到一半被挤死
    （2026-09-04 实测：出图 10054/10061 全失败）。单卡规则是同时只准一个重模型，
    所以不在 8080 上的 llama-server 一律是漏网的，直接清。
    """
    pid = stop_process_on_port(8080, "Qwen llama-server")
    try:
        subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True, timeout=20)
        time.sleep(2)
    except Exception:
        pass
    return pid


# ---------- 三方互斥 ----------
# 用户的硬约束：「我的电脑写作画图视频，只能同时存在一个」。
# 但互斥一直只有单向——qwen_client._free_idle_gpu() 会在写作前赶走 Comfy/H3，
# 反过来却没有任何一处调 stop_qwen()。结果是：Qwen 占着显存，
# 点「生成设定图」→ ComfyUI 加载 Krea2 时显存不够，180 秒起不来，前端干等到超时。
# 这里改成谁要用谁先清场，并且加一把全局锁，防止两个重任务同时抢卡。

import threading as _th

GPU_LOCK = _th.RLock()

_PORTS = {"qwen": 8080, "krea": 8188, "h3": 8190}
_STOP = {"qwen": stop_qwen, "krea": stop_comfy, "h3": stop_h3}


def claim(who):
    """要用 GPU 了，把另外两个停掉。返回实际停掉了谁。

    who ∈ {"qwen", "krea", "h3"}。已经不在跑的不动，省掉一次 taskkill 的两秒。
    """
    if who not in _PORTS:
        raise ValueError("不认识的 GPU 使用者：%s" % who)
    freed = []
    for k, port in _PORTS.items():
        if k == who:
            continue
        if pid_on_port(port):
            _STOP[k]()
            freed.append(k)
    if who != "qwen":
        kill_stray_llama()          # 不在 8080 上的 llama-server 是漏网的，一并清（P272）
    return freed


# ═══════════════ P272 一张卡只准一个进程用模型（跨进程硬闸）═══════════════
# 事故：出片任务在用千问，另一个进程（诊断脚本）也去拉千问，两边各起一个 llama-server；
# 启动失败又不杀进程，攒到 8 个各加载 20 GB 模型，内存打满整机卡死。
# 互斥原来只在**同一个进程内**（GPU_LOCK 是线程锁），跨进程完全不设防——这里补上。

_OWNER_FILE = _Path(__file__).resolve().parent.parent / "cache" / "gpu_owner.json"
OWNER_STALE_SEC = 7200        # 主人还活着但两小时没动过 → 当它忘了释放，作废
IDLE_FREE_SEC = 600           # 卡上一个模型都没跑、又这么久没动 → 当它闲着，让位给别的进程
MIN_FREE_GB = float(os.environ.get("V41_MIN_FREE_GB") or 12)   # 起模型至少要这么多可用内存


def _pid_alive(pid):
    """这个进程号还活着吗。查不到就当活着（宁可拒绝，不敢误判成死的去抢卡）。"""
    try:
        out = subprocess.check_output(["tasklist", "/FI", "PID eq %d" % int(pid), "/NH"],
                                      text=True, errors="replace", timeout=15)
        return str(int(pid)) in out
    except Exception:
        return True


def owner():
    """现在是谁在用卡 → {"who","pid","at","note"}；没人用或主人已死/过期返回 None。"""
    try:
        d = json.loads(_OWNER_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(d, dict) or not d.get("pid"):
        return None
    if time.time() - float(d.get("at") or 0) > OWNER_STALE_SEC:
        return None
    if not _pid_alive(d["pid"]):
        return None
    # 主人还活着，但三个模型端口一个都没人监听（卡上没东西在跑）、又超过 10 分钟没动 →
    # 它只是开着没干活，别把用户自己的脚本锁死（P272b）
    if time.time() - float(d.get("at") or 0) > IDLE_FREE_SEC and not any(
            pid_on_port(p) for p in _PORTS.values()):
        return None
    return d


def free_gb():
    """可用物理内存（GB）。查不到返回 None（不拦）。"""
    try:
        out = subprocess.check_output(
            ["wmic", "OS", "get", "FreePhysicalMemory", "/value"],
            text=True, errors="replace", timeout=15)
        m = re.search(r"FreePhysicalMemory=(\d+)", out)
        if m:
            return int(m.group(1)) / 1048576.0
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"],
            text=True, errors="replace", timeout=25)
        return int(str(out).strip()) / 1048576.0
    except Exception:
        return None


def stray_llama_pids():
    """所有 llama-server 进程号（不管在不在 8080 上）。"""
    out = []
    try:
        txt = subprocess.check_output(["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/NH"],
                                      text=True, errors="replace", timeout=20)
        for line in txt.splitlines():
            m = re.match(r"\s*llama-server\.exe\s+(\d+)", line)
            if m:
                out.append(int(m.group(1)))
    except Exception:
        pass
    return out


def kill_stray_llama(keep_pid=None):
    """llama-server 只准活一个：在 8080 上的那个（或指定保留的），别的全杀。
    返回杀掉的进程号列表。P272：僵尸进程一个不留——这是把机器搞死的直接原因。"""
    keep = keep_pid or pid_on_port(8080)
    killed = []
    for pid in stray_llama_pids():
        if keep and pid == int(keep):
            continue
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=20)
            killed.append(pid)
        except Exception:
            pass
    if killed:
        time.sleep(2)
    return killed


def acquire(who, note=""):
    """要起/用模型之前占卡。别的活着的进程占着 → 抛错，绝不拉第二个模型进程。

    同一个进程再 acquire（工作台在 qwen/krea/h3 之间切换）永远放行，只更新记录。
    V41_GPU_FORCE=1 可强行接管（只在确认没有别的任务在跑时用）。
    """
    me = os.getpid()
    cur = owner()
    if cur and int(cur["pid"]) != me and not os.environ.get("V41_GPU_FORCE"):
        raise RuntimeError(
            "显卡正被另一个进程使用：%s（进程 %s，%s 起）。"
            "一张卡同时只准一个模型——请等它结束；确认那边已经停了就设 V41_GPU_FORCE=1 再跑。"
            % (cur.get("who"), cur.get("pid"),
               time.strftime("%H:%M:%S", time.localtime(float(cur.get("at") or 0)))))
    try:
        _OWNER_FILE.parent.mkdir(parents=True, exist_ok=True)
        _OWNER_FILE.write_text(json.dumps(
            {"who": who, "pid": me, "at": time.time(), "note": str(note or "")[:200]},
            ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return True


def release(who=None):
    """放开卡（只有自己占着时才删）。模型进程留着没关系，别的进程要用会先 claim 清场。"""
    cur = owner()
    if cur and int(cur["pid"]) == os.getpid():
        try:
            _OWNER_FILE.unlink()
        except Exception:
            pass


def guard(who, need_gb=None, note=""):
    """起模型前的总闸：① 跨进程占用 ② 停掉别的模型 + 清僵尸 ③ 内存够不够。三关都过才返回。

    顺序很重要：**先停别人再量内存**。P276 实测——场景图跑完 ComfyUI 占着 19 GB，
    接着要起千问，先量就只剩 2.1 GB 被拒绝；而千问启动本来就会先停掉 ComfyUI，停完有 20 GB。
    闸把自己拦死了，整个出片任务停在那儿。
    """
    acquire(who, note)
    killed = kill_stray_llama()
    freed = []
    try:
        freed = claim(who)          # 一张卡只准一个：先把另外两个停掉，内存和显存都还回来
    except Exception:
        pass
    if freed or killed:
        time.sleep(3)               # taskkill 之后系统要一会儿才把内存还回来
    need = MIN_FREE_GB if need_gb is None else float(need_gb)
    fg = free_gb()
    for _ in range(3):
        if fg is None or fg >= need:
            break
        time.sleep(3)
        fg = free_gb()
    if fg is not None and fg < need:
        raise RuntimeError(
            "可用内存只剩 %.1f GB，起模型至少要 %.1f GB（已经先停掉%s了）——"
            "先关掉占内存的程序再试（宁可现在报错，也不能把机器拖死）。"
            % (fg, need, "、".join(freed) if freed else "别的模型"))
    return {"killed_stray": killed, "freed": freed, "free_gb": fg}
