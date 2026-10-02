# -*- coding: utf-8 -*-
"""expand_core.py — 通用扩写。全站所有「AI 扩写 / 重新扩写」都走这里。

用户长期强调的一条：**AI 在扩写我的输入时一定不要改变原意，尽可能不改原文。**

所以扩写完不是直接给出去，而是量两个东西：
  · 和原文的重合度（premise_guard）—— 低了说明模型是在重写而不是在扩写
  · 长度倍数 —— 只多出一点点等于没扩，翻十倍是在灌水

不合格就把原因告诉模型重来，三次都不行也把最好的那稿给出去——拦截是设计失败。
"""
import re
from pathlib import Path

from . import premise_guard

INSTRUCTION = (Path(__file__).resolve().parent.parent / "presets" / "instructions"
               / "通用_扩写.txt")

TRIES = 3
MIN_RATIO = 1.4         # 至少要比原文长这么多倍，否则等于没扩
MAX_RATIO = 6.0         # 超过就是在灌水
OVERLAP_MIN = 0.30      # 扩写比一句话生成要求更严：这是在补，不是在重新创作


def _clean(t):
    t = re.sub(r"^\s*(扩写后|扩写结果|以下是[^\n：:]*)\s*[:：]\s*", "", str(t or "").strip())
    return t.strip()


def check(original, out):
    """返回不合格的理由列表，空列表就是通过。"""
    why = []
    n0, n1 = len(original), len(out)
    if n1 < n0 * MIN_RATIO:
        why.append("扩写后只有 %d 字，原文 %d 字，几乎没扩开。把细节补足，"
                   "写到原文的两三倍" % (n1, n0))
    if n1 > n0 * MAX_RATIO and n1 > 600:
        why.append("扩写后 %d 字，比原文 %d 字膨胀太多了，删掉重复和无关的部分" % (n1, n0))
    if premise_guard.overlap(original, out) < OVERLAP_MIN:
        why.append("你把原文改写了。原文里已经说定的事一个字都不能动，"
                   "只能在它后面补细节。把原文的说法保留下来，再往下写")
    return why


def expand(text, purpose="内容", call_model=None):
    """扩写一段内容，返回 (扩写结果, 说明)。"""
    ins = INSTRUCTION.read_text(encoding="utf-8")
    base = "这段内容是：%s\n原文：\n%s" % (purpose, text)
    user = base

    if call_model is None:
        from models import qwen_client

        def call_model(sysmsg, usr, **kw):
            return qwen_client.chat(sysmsg, usr, max_tokens=4000, **kw)

    best, best_score, note = "", -1e9, ""
    for _ in range(TRIES):
        out = _clean(call_model(ins, user, temperature=0.7))
        why = check(text, out)
        score = -len(why) * 1000 + min(len(out), len(text) * 3)
        if score > best_score:
            best, best_score = out, score
        if not why:
            return out, "%d 字 → %d 字" % (len(text), len(out))
        note = "；".join(why)
        user = base + "\n\n【重来】" + note
    return best, "重试 %d 次后仍有问题：%s" % (TRIES, note)
