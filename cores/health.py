# -*- coding: utf-8 -*-
"""体检（红绿灯）：正文 / 剧本 / 提示词 / 语音 四层的机械检查，给界面显示，不改任何东西。

每项返回 {"name": 项名, "ok": True/False, "detail": 一句话}。
判据全是今天（2026-09-03）在项目 97/98 上踩出来的：覆盖、台词顺序、说话人、拍不到句子、
场景绑定、全景占比、台词行格式、音色头格式。
"""
import io
import json
import os
import re

from . import authoring as au


def _item(name, ok, detail=""):
    return {"name": name, "ok": bool(ok), "detail": str(detail or "")}


def _ep(sid, ep=1):
    from . import story_core
    st = story_core.get_story(sid) or {}
    eps = ((st.get("saga") or {}).get("episodes") or [])
    e = eps[int(ep) - 1] if len(eps) >= int(ep) else {}
    return st, e


def story_health(sid, ep=1):
    st, e = _ep(sid, ep)
    prose = str(e.get("prose") or "")
    out = []
    if not prose.strip():
        return [_item("正文", False, "还没有正文")]
    paras = [p for p in re.split(r"\n\s*\n", prose.strip()) if p.strip()]
    quotes = re.findall(r"[「“]([^」”\n]+)[」”]", prose)
    out.append(_item("字数", 800 <= len(prose) <= 2600, "%d 字，%d 段" % (len(prose), len(paras))))
    out.append(_item("台词", len(quotes) >= 4, "%d 句" % len(quotes)))
    bad = au.collect_bad_sentences(prose)
    out.append(_item("拍不到的句子", len(bad) == 0, "%d 句：%s" % (len(bad), "；".join(k for _, k in bad[:4])) if bad else "0 句"))
    # 台词带说话人：引号所在段里有人物名
    from . import asset_core as _ac
    names = [str(c.get("name") or "") for c in (_ac.list_assets(sid, "characters") or []) if c.get("name")]
    orphan = 0
    for p in paras:
        if re.search(r"[「“][^」”\n]+[」”]", p) and names and not any(n in p for n in names):
            orphan += 1
    out.append(_item("台词带说话人", orphan == 0, "%d 段台词没写谁说的" % orphan if orphan else "都有"))
    return out


def pictures_health(sid, ep=1):
    st, e = _ep(sid, ep)
    prose = str(e.get("prose") or "")
    pics = str(e.get("pictures") or e.get("body") or "")
    if not pics.strip():
        return [_item("剧本", False, "还没有剧本")]
    out = []
    unc = au.picture_uncovered(pics, prose)
    out.append(_item("覆盖正文", not unc, "漏了 %d 段" % len(unc) if unc else "全覆盖"))
    miss = au.picture_missing_dialogue(pics, prose)
    out.append(_item("台词齐全", not miss, "丢 %d 句" % len(miss) if miss else "一句不少"))
    # 顺序
    norm = lambda q: re.sub(r"[^\w一-龥]", "", q)
    pq = [norm(q) for q in re.findall(r"[「“]([^」”\n]+)[」”]", prose)]
    order = {q: i for i, q in enumerate(pq)}
    seq = []
    for l in pics.split("\n"):
        m = re.match(r"^([^\n：:]{1,12})[：:](.+)$", l.strip())
        if m and norm(m.group(2).strip("「」“”")) in order:
            seq.append(order[norm(m.group(2).strip("「」“”"))])
    out.append(_item("台词顺序", seq == sorted(seq), "和正文一致" if seq == sorted(seq) else "有台词跑位"))
    from . import asset_core as _ac
    names = {str(c.get("name") or "") for c in (_ac.list_assets(sid, "characters") or [])}
    bad_sp = [l[:20] for l in pics.split("\n") if au._DLG_LINE.match(l) and l.split("：")[0].split(":")[0].strip() not in names]
    out.append(_item("说话人在人物表", not bad_sp, "；".join(bad_sp[:3]) if bad_sp else "都在"))
    badl = au.collect_bad_sentences(au._DLG_LINE.sub(lambda m: "X", pics))
    out.append(_item("拍不到的句子", not badl, "%d 句" % len(badl) if badl else "0 句"))
    out.append(_item("篇幅", len(pics) >= len(prose) * 0.9, "%d 字（正文 %d）" % (len(pics), len(prose))))
    return out


def prompts_health(sid, ep=1):
    st, e = _ep(sid, ep)
    tl = e.get("timeline") or {}
    segs = sorted([g for sc in tl.get("scenes") or [] for g in sc.get("segments") or []], key=lambda g: int(g.get("no") or 0))
    if not segs:
        return [_item("提示词", False, "还没有分镜")]
    out = [_item("段数", True, "%d 段，共 %.0f 秒" % (len(segs), sum(float(g.get("seconds") or 0) for g in segs)))]
    kinds, tot = {}, 0
    no_wide, no_scene, bad_say, snd, silent_dlg = [], [], [], [], []
    from . import asset_core as _ac
    adopted = {str(v.get("owner_id") or "") for v in (_ac.list_assets(sid, "visuals") or []) if str(v.get("status") or "") == "adopted"}
    scene_ids = {str(x.get("name") or ""): str(x.get("scene_id") or "") for x in (_ac.list_assets(sid, "scenes") or [])}
    for g in segs:
        lines = str(g.get("prompt") or "").splitlines()
        shots = [au.shot_parts(l) for l in lines if au.shot_parts(l)]
        sizes = []
        for p in shots:
            m = re.search(r"(远景|全景|中景|中近景|近景|特写)", p[4].split("。", 1)[0])
            sizes.append(m.group(1) if m else "?")
            kinds[sizes[-1]] = kinds.get(sizes[-1], 0) + 1
        tot += len(shots)
        if not any(x in ("远景", "全景") for x in sizes):
            no_wide.append(g["no"])
        if not g.get("scene") or scene_ids.get(str(g.get("scene"))) not in adopted:
            no_scene.append(g["no"])
        for l in lines:
            if ("says" in l or "<d>" in l) and not re.search(r"<Subject \d+> \(S\d+\) says:<d>\[Chinese\].+</d>", l):
                bad_say.append(g["no"])
            if au.shot_parts(l) and au._SOUND_WORDS.search(au.shot_parts(l)[4]):
                snd.append(g["no"])
        if any("says:" in l for l in lines) and any("没有人说话" in l for l in lines):
            silent_dlg.append(g["no"])
    wide = (kinds.get("远景", 0) + kinds.get("全景", 0)) / max(1, tot)
    out.append(_item("全景+远景占比", wide >= 0.15, "%.0f%%" % (wide * 100)))
    out.append(_item("每段有全景", len(no_wide) <= max(1, len(segs) // 4), "缺全景：%s" % no_wide if no_wide else "都有"))
    out.append(_item("场景图绑定", not no_scene, "没绑到有图场景：%s" % no_scene if no_scene else "全部绑定"))
    out.append(_item("台词行格式", not bad_say, "格式不对：%s" % sorted(set(bad_say)) if bad_say else "全部正确"))
    out.append(_item("无声音描写", not snd, "有声音词：%s" % sorted(set(snd)) if snd else "干净"))
    out.append(_item("台词与静音不冲突", not silent_dlg, "既有台词又声明静音：%s" % silent_dlg if silent_dlg else "正常"))
    return out


def all_health(sid, ep=1):
    return {"story": story_health(sid, ep), "pictures": pictures_health(sid, ep), "prompts": prompts_health(sid, ep)}


# ─────────── 语音核对（whisper，CPU；第一次加载模型约 20 秒，之后每段约 1 分钟） ───────────
_WHISPER = None


def _model():
    global _WHISPER
    if _WHISPER is None:
        from faster_whisper import WhisperModel
        _WHISPER = WhisperModel("medium", device="cpu", compute_type="int8")
    return _WHISPER


def voice_check(sid, ep=1, seg_nos=None, on_step=None):
    """逐段转写音轨，和提示词里的台词对比。返回 [{seg, expect, heard, ok}]。
    ok 判据：每句期望台词至少一半的汉字连续出现在转写里；没台词的段转写里不能有 ≥4 个汉字的连续语音。"""
    import subprocess
    st, e = _ep(sid, ep)
    tl = e.get("timeline") or {}
    segs = sorted([g for sc in tl.get("scenes") or [] for g in sc.get("segments") or []], key=lambda g: int(g.get("no") or 0))
    if seg_nos:
        segs = [g for g in segs if int(g.get("no") or 0) in set(int(x) for x in seg_nos)]
    model = _model()
    rows = []
    try:
        import zhconv as _zc                       # whisper 常输出繁体（長淵/師妹），比对前统一成简体
        _simp = lambda x: _zc.convert(str(x), "zh-cn")
    except Exception:
        _simp = lambda x: str(x)
    try:
        from pypinyin import lazy_pinyin as _lp                 # P394：按拼音比对——同音字（遗迹/一季、溪/西）不算没听到
        norm = lambda q: "".join(_lp(re.sub(r"[^一-龥]", "", _simp(q))))
    except Exception:
        norm = lambda q: re.sub(r"[^一-龥]", "", _simp(q))
    for g in segs:
        v = str(g.get("video") or "")
        if not v or not os.path.exists(v):
            rows.append({"seg": g.get("no"), "expect": [], "heard": [], "ok": False, "note": "没有视频"})
            continue
        if on_step:
            on_step("语音核对 第 %s 段" % g.get("no"))
        exp = [re.sub(r".*\[Chinese\](.*?)</d>.*", r"\1", l) for l in str(g.get("prompt") or "").splitlines() if "says:" in l]
        wav = os.path.join("outputs", "video", "_voice_%s_%s.wav" % (sid, g.get("no")))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", v, "-vn", "-ac", "1", "-ar", "16000", wav], capture_output=True)
        try:
            sg, _info = model.transcribe(wav, language="zh", beam_size=5, vad_filter=True)
            heard = [(round(x.start, 1), round(x.end, 1), x.text.strip()) for x in sg]
        except Exception as ex:
            heard = []
            rows.append({"seg": g.get("no"), "expect": exp, "heard": [], "ok": False, "note": "转写失败：%s" % str(ex)[:60]})
            continue
        finally:
            try:
                os.remove(wav)
            except Exception:
                pass
        htext = norm("".join(h[2] for h in heard))
        ok = True
        note = ""
        if exp:
            for q in exp:
                nq = norm(q)
                if not nq:
                    continue
                # 至少一半连续汉字命中（whisper 会写成同音字/繁体，宽松判）
                half = max(2, len(nq) // 2)
                hit = any(nq[i:i + half] in htext for i in range(0, len(nq) - half + 1)) or (len(nq) >= 6 and nq in htext)
                if not hit:
                    ok = False
                    note = "没听到「%s」" % q[:12]
            # 多说了：转写总字数远超台词（念了音色头/注释/胡说）
            exp_len = sum(len(norm(q)) for q in exp)
            if ok and len(htext) > exp_len * 2 + 24:
                ok = False
                note = "多说了：%s" % htext[:20]
            # 多说了（逐段判）：某一段听到的话和所有台词都对不上，且时长 ≥2.5 秒或 ≥4 个字（2026-09-04 项目100 第5段 9 秒胡言漏判）
            if ok:
                for h in heard:
                    ht = norm(h[2])
                    if not ht:
                        continue
                    matched = any(nq and (ht in nq or nq in ht or any(nq[i:i + max(2, len(nq) // 2)] in ht for i in range(0, len(nq) - max(2, len(nq) // 2) + 1)))
                                  for nq in (norm(q) for q in exp))
                    if not matched and ((float(h[1]) - float(h[0])) >= 2.5 or len(ht) >= 12):
                        ok = False
                        note = "多说了（%.1f～%.1f秒）：%s" % (float(h[0]), float(h[1]), ht[:16])
                        break
        else:
            if len(htext) >= 12:
                ok = False
                note = "无台词段有人声：%s" % htext[:16]
        rows.append({"seg": g.get("no"), "expect": exp, "heard": heard, "ok": ok, "note": note})
    return rows


# ─────────── 工作台自愈（2026-09-05：长时间渲染中静默崩过两次）───────────
def workbench_up(port=8853, timeout=3):
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:%d/api/tasks" % port, timeout=timeout).close()
        return True
    except Exception:
        return False


def _port_alive(port, path="/", timeout=2.0):
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:%d%s" % (int(port), path), timeout=timeout).read(1)
        return True
    except Exception as ex:
        # 有响应但状态码不是 200 也算活着（比如 404）——只要端口在应答
        return "HTTP Error" in str(type(ex).__name__) or "HTTPError" in str(type(ex))


def llama_up(port=8080):
    """文字模型（llama-server）在不在。"""
    return _port_alive(port, "/health")


def comfy_up(port=8188):
    """出图出片（ComfyUI）在不在。"""
    return _port_alive(port, "/queue")


def services():
    """三个服务的状态 + 一句人话。给界面和出错提示共用。"""
    st = {"工作台": workbench_up(), "文字模型": llama_up(), "出图出片": comfy_up()}
    down = [k for k, v in st.items() if not v]
    tip = ""
    if "文字模型" in down:
        tip += "文字模型（llama-server，8080 端口）没起来——写故事、写提示词都要它。"
    if "出图出片" in down:
        tip += "出图出片（ComfyUI，8188 端口）没起来——人设图、场景图、视频都要它。"
    if not down:
        tip = "三个服务都在。"
    return {"status": st, "down": down, "tip": tip}


def ensure_workbench(port=8853, wait=60):
    """工作台没在跑就拉起来，等它就绪。返回 True/False。渲染脚本每段前调一次。"""
    import os
    import subprocess
    import sys
    import time
    if workbench_up(port):
        return True
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        out = open(os.path.join(root, "v41_server.log"), "a", encoding="utf-8", errors="ignore")
        err = open(os.path.join(root, "v41_server_err.log"), "a", encoding="utf-8", errors="ignore")
        subprocess.Popen([sys.executable, "-u", "server.py"], cwd=root,
                         stdout=out, stderr=err, creationflags=0x08000000)
    except Exception:
        return False
    for _ in range(wait):
        time.sleep(1)
        if workbench_up(port):
            return True
    return False
