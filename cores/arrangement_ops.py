# -*- coding: utf-8 -*-
"""arrangement_ops.py — 出片前"时长放不下"的两种处置：延长本话 / 把后半部分留到下一话。

【为什么单独一个文件】cores/arrangement.py 同一时间有另一个人在改（加 beat 切片、cut_after_beat），
两个人改一个文件必然互相盖。这里只依赖安排卡的**形状**（duration_sec / budget / history），
不 import arrangement.py 里的函数——它没就绪时这里照样能用。

【为什么不重切剧本】切出来 80 秒、预计 60 秒，说明剧本比安排长。两条路都是**改安排去迁就剧本**：
  · 延长本话：预计时长改成实际秒数，预算各条按比例放大到实际值——安排卡上的数字和成片对得上。
  · 留到下一话：找出累计秒数还装得下的最后一个 beat，记成 cut_after_beat；出片只出到那里，
    后面的段留给下一话。剧本一个字不动，确认过的结尾也还在（只是在下一话拍）。
status 保持 confirmed：这两种都是用户在出片前门上点的选择，不是要重新确认安排。
"""
import copy
import time

STAGES = ("开始", "经过", "变化", "结束")


def _stages(arr):
    try:
        from .arrangement import stages_of
        return tuple(stages_of(arr))
    except Exception:
        return STAGES
CHOICE_EXTEND = "延长本话"
CHOICE_DEFER = "留到下一话"
# 页面按钮上写的是「把后半部分留到下一话」，路由里两种写法都认
_CHOICE_ALIAS = {"延长本话": CHOICE_EXTEND, "延长": CHOICE_EXTEND,
                 "留到下一话": CHOICE_DEFER, "把后半部分留到下一话": CHOICE_DEFER, "下一话": CHOICE_DEFER}


def _int(v, default=0):
    try:
        return int(round(float(v)))
    except Exception:
        return default


def normalize_choice(choice):
    """"延长本话" / "留到下一话"；认不出返回 ""。"""
    c = str(choice or "").strip()
    if c in _CHOICE_ALIAS:
        return _CHOICE_ALIAS[c]
    for k, v in _CHOICE_ALIAS.items():
        if k in c:
            return v
    return ""


def per_beat_index(per_beat_sec, arr=None):
    _ST = _stages(arr)
    """per_beat_sec 的键可以是下标（0~3）或段名（开始/经过/变化/结束），统一成 {下标: 秒}。
    "未归属"这种键丢掉——不知道属于哪段的秒数没法参与"累计到哪段"。"""
    out = {}
    for k, v in (per_beat_sec or {}).items():
        i = -1
        if isinstance(k, int):
            i = k
        else:
            ks = str(k).strip()
            if ks in _ST:
                i = _ST.index(ks)
            elif ks.isdigit():
                i = int(ks)
        if 0 <= i < len(_ST):
            out[i] = out.get(i, 0.0) + float(v or 0)
    return out


def scale_budget(budget, target_total):
    """预算各条按比例放大/缩小到 target_total 秒，取整后合计**正好**等于 target_total。
    取整的零头补给秒数最多的那条（它相对误差最小）；每条至少 1 秒，别把短的条放没了。"""
    rows = [dict(b) for b in (budget or []) if isinstance(b, dict)]
    if not rows:
        return rows
    old = sum(max(0, _int(b.get("seconds"))) for b in rows)
    target = max(0, _int(target_total))
    if old <= 0 or target <= 0:
        return rows
    ratio = target / float(old)
    for b in rows:
        b["seconds"] = max(1, _int(max(0, _int(b.get("seconds"))) * ratio))
    diff = target - sum(b["seconds"] for b in rows)
    if diff:
        big = max(rows, key=lambda b: b["seconds"])
        big["seconds"] = max(1, big["seconds"] + diff)
    return rows


def cut_point(per_beat, duration_sec, n_beats=0):
    """累计秒数不超过 duration_sec 的最后一个 beat 下标（至少 0）。
    四段全装得下返回 -1（不用剪）。"""
    dur = _int(duration_sec)
    cum, cut = 0.0, 0
    _n = n_beats if n_beats else len(STAGES)
    for i in range(_n):
        cum += float(per_beat.get(i, 0.0))
        if cum <= dur:
            cut = i
        else:
            break
    return -1 if cut == _n - 1 and cum <= dur else cut


def apply_length_choice(arr, choice, actual_total_sec, per_beat_sec=None):
    """按用户的选择改安排。返回**新的**安排（传入的不动，路由拿返回值落盘）。

    choice：延长本话 / 留到下一话（页面写法「把后半部分留到下一话」也认）。
    actual_total_sec：这一话实际切出来的总秒数。
    per_beat_sec：{beat 下标或段名: 实际秒数}。缺了：延长只做总时长放大；留到下一话判不出剪在哪，抛 ValueError。
    """
    if not isinstance(arr, dict) or not arr:
        raise ValueError("还没有安排卡")
    what = normalize_choice(choice)
    if not what:
        raise ValueError("选择只能是「延长本话」或「把后半部分留到下一话」，给的是「%s」" % str(choice or "")[:20])
    new = copy.deepcopy(arr)
    actual = _int(actual_total_sec)
    old_dur = _int(new.get("duration_sec"))
    hist = [h for h in (new.get("history") or []) if isinstance(h, dict)] if isinstance(new.get("history"), list) else []
    version = max(1, _int(new.get("version"), 1)) + 1
    changed = []

    if what == CHOICE_EXTEND:
        if actual <= 0:
            raise ValueError("实际总秒数是 0，没法延长")
        new["duration_sec"] = actual
        changed.append("duration_sec")
        if new.get("budget"):
            new["budget"] = scale_budget(new.get("budget"), actual)
            changed.append("budget")
        # 之前剪过、现在决定延长：整话都出，剪点作废
        if _int(new.get("cut_after_beat", -1), -1) >= 0:
            new["cut_after_beat"] = -1
            changed.append("cut_after_beat")
        why = "切出来 %d 秒，原定 %d 秒放不下：预计时长改成 %d 秒，预算按比例放大" % (actual, old_dur, actual)
    else:
        per = per_beat_index(per_beat_sec, arr)
        if not per:
            raise ValueError("切片还没有段落归属，判不出剪在哪一段之后——先重新切段")
        if old_dur <= 0:
            raise ValueError("安排里没有预计时长，判不出剪在哪")
        cut = cut_point(per, old_dur, len(_stages(arr)))
        new["cut_after_beat"] = cut
        changed.append("cut_after_beat")
        if cut < 0:
            why = "切出来 %d 秒，四段都装得下 %d 秒，不用剪" % (actual, old_dur)
        else:
            kept = sum(per.get(i, 0.0) for i in range(cut + 1))
            why = ("切出来 %d 秒，原定 %d 秒放不下：只出到「%s」（累计 %d 秒），后半部分留到下一话"
                   % (actual, old_dur, _stages(arr)[cut], _int(kept)))

    new["version"] = version
    new["status"] = "confirmed" if str(arr.get("status") or "") == "confirmed" else new.get("status", "draft")
    new["history"] = hist + [{"version": version, "instruction": what, "changed": changed,
                              "why": why, "at": time.time()}]
    return new
