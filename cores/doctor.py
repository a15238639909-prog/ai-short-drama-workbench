# -*- coding: utf-8 -*-
"""开机自检：一次把「能不能用」查清楚，缺什么当场说明白。

上线标准的第一条：新用户拿到工具，最常见的失败是环境没配好——
少个 Python 库、没装 ffmpeg、模型服务没起、config.json 路径不对。
这些以前都要等到生成跑到一半才炸，报的还是英文异常（2026-09-05）。

server.py 启动时调 report() 打一遍；界面也能调 /api/doctor 拿同一份结果。
"""
import os
import shutil
import subprocess
import sys


def _py():
    v = sys.version_info
    ok = v >= (3, 10)
    return ok, "Python %d.%d.%d" % (v.major, v.minor, v.micro), "要 3.10 以上"


def _lib(name, why, must=True):
    try:
        __import__(name)
        return True, name, ""
    except Exception:
        return (not must), name, "缺这个库：pip install %s（%s）" % (
            {"PIL": "pillow", "faster_whisper": "faster-whisper"}.get(name, name), why)


def _exe(name, why):
    p = shutil.which(name)
    if p:
        return True, "%s" % name, ""
    return False, name, "找不到 %s，%s" % (name, why)


def _ffmpeg_works():
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def check():
    """返回 [(级别, 项目, 状态, 说明)]。级别：必须 / 可选。"""
    rows = []

    ok, name, tip = _py()
    rows.append(("必须", "Python 版本", ok, name if ok else "%s（%s）" % (name, tip)))

    for lib, why, must in (("PIL", "出图、拼图要用", True),
                           ("zhconv", "繁简转换", True)):
        ok, nm, tip = _lib(lib, why, must)
        rows.append(("必须" if must else "可选", "Python 库 " + nm, ok, tip or "已安装"))

    ff = _exe("ffmpeg", "截图和拼片要用")[0] and _ffmpeg_works()
    rows.append(("必须", "ffmpeg", ff, "可用" if ff else "没装或不可用：截图、拼成片会失败"))
    fp = _exe("ffprobe", "读视频时长要用")[0]
    rows.append(("必须", "ffprobe", fp, "可用" if fp else "没装：读不到视频时长"))

    # 三个模型服务
    try:
        from cores import health
        st = health.services()["status"]
        rows.append(("必须", "文字模型 llama-server:8080", st.get("文字模型"),
                     "在跑" if st.get("文字模型") else "没起来——写故事、写提示词都要它"))
        rows.append(("必须", "出图出片 ComfyUI:8188", st.get("出图出片"),
                     "在跑" if st.get("出图出片") else "没起来——人设图、场景图、视频都要它"))
    except Exception as ex:
        rows.append(("必须", "模型服务体检", False, "查不了：%s" % str(ex)[:60]))
    try:
        from cores import health as _h
        h3 = _h._port_alive(8190, "/")
        rows.append(("可选", "视频服务 H3:8190", h3, "在跑" if h3 else "没起来——出片时才需要"))
    except Exception:
        pass

    # 本机路径
    try:
        from cores import paths
        bad = paths.missing()
        rows.append(("必须", "本机路径配置", not bad,
                     "都配好了" if not bad else paths.report()[:150]))
    except Exception as ex:
        rows.append(("必须", "本机路径配置", False, str(ex)[:80]))

    # 数据目录可写
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        t = os.path.join(root, "data", "_write_test.tmp")
        os.makedirs(os.path.dirname(t), exist_ok=True)
        open(t, "w").write("1")
        os.remove(t)
        rows.append(("必须", "数据目录可写", True, "可写"))
    except Exception as ex:
        rows.append(("必须", "数据目录可写", False, "写不了：%s" % str(ex)[:60]))

    return rows


def report(quiet_when_ok=False):
    """打一份人能看懂的自检报告。返回 True＝必须项全过。"""
    rows = check()
    must_bad = [r for r in rows if r[0] == "必须" and not r[2]]
    opt_bad = [r for r in rows if r[0] == "可选" and not r[2]]
    if quiet_when_ok and not must_bad and not opt_bad:
        return True
    print("=" * 54)
    print("  开机自检")
    print("=" * 54)
    for lv, name, ok, tip in rows:
        mark = "OK  " if ok else ("!!  " if lv == "必须" else "--  ")
        print("  %s%-30s %s" % (mark, name, tip))
    print("-" * 54)
    if must_bad:
        print("  有 %d 项必须的没过，这些功能会用不了：" % len(must_bad))
        for _, name, _, tip in must_bad:
            print("    · %s —— %s" % (name, tip))
    else:
        print("  必须项全过。" + ("可选项有 %d 项没配，不影响主流程。" % len(opt_bad) if opt_bad else ""))
    print("=" * 54)
    return not must_bad


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(0 if report() else 1)
