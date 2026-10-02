# -*- coding: utf-8 -*-
"""原题守卫：AI 扩写用户输入时，不许改变原意。

**这是从 8848 搬过来的、用户已经调好并长期在用的机制**（`app.py` 的
`_premise_overlap` / `generate_quick_comic_story`），不是重新发明的。

它比"扩写完再检查有没有丢东西"更成熟，因为它带**自动重试**：
偏题了不是直接报错，而是追加一条更严厉的指令再要一次，两次都不行才交给人。
这正好符合"拦截是设计失败，能自动修就自动修"。

三件事：
1. `overlap()`  —— 字面二元组重合度，低成本拦"完全换题"，不要求逐字照抄
2. `SYSTEM_RULE` —— 写进 system prompt 的原题约束原文（8848 用的那段）
3. `expand_with_guard()` —— 带重试的扩写包装：偏题自动重来一次
"""
import re

__all__ = ["overlap", "SYSTEM_RULE", "RETRY_HINT", "expand_with_guard", "DEFAULT_THRESHOLD"]

# 8848 实际在用的阈值：低于此值判定为"换了个故事"
DEFAULT_THRESHOLD = 0.18

_WORD = re.compile(r"[一-鿿A-Za-z0-9]")

# 写进 system prompt 的原题约束（8848 原文，不要改写）
SYSTEM_RULE = (
    "用户的一句话是不可替换的故事核心：其中明确的人物年龄、地点、关键物品、动作目标和结果都必须保留，"
    "禁止另写一个相似主题或完全不同的故事。扩写只能补充过程，不得改变上述核心事实。"
)

# 第一次偏题后追加的更严厉指令（8848 原文）
RETRY_HINT = (
    "上一次输出偏离原题。此次正文必须直接出现原题中的主要人物年龄/身份、"
    "关键地点、关键物品和行动结果，否则视为失败。"
)


def overlap(premise, text):
    """原题与扩写结果的字面重合度，0–1。

    用二元组而不是逐字比对：允许模型换语序、补细节，但换了题材就会掉下去。
    原题短于 4 个字时退化成"整句是否出现"。
    """
    source = "".join(_WORD.findall(str(premise or ""))).lower()
    target = "".join(_WORD.findall(str(text or ""))).lower()
    if len(source) < 4:
        return 1.0 if source and source in target else 0.0
    grams = {source[i:i + 2] for i in range(len(source) - 1)}
    return sum(1 for g in grams if g in target) / max(1, len(grams))


def expand_with_guard(premise, call_model, threshold=DEFAULT_THRESHOLD,
                      max_attempts=2, min_length=0):
    """带原题守卫的扩写。

    call_model(extra_instruction) -> 扩写文本；第一次传空串，重试时传 RETRY_HINT。
    返回 {"text", "attempts", "overlap", "ok", "note"}。

    偏题不直接报错：先自动重来一次（第 1 层自动修）；两次都不行才把结论交给人，
    并说明差在哪，而不是抛一个用户看不懂的异常。
    """
    best, best_score, attempts = "", -1.0, 0
    for i in range(max(1, int(max_attempts))):
        attempts = i + 1
        text = str(call_model(RETRY_HINT if i else "") or "").strip()
        score = overlap(premise, text)
        if score > best_score:
            best, best_score = text, score
        if len(text) >= min_length and score >= threshold:
            return {"text": text, "attempts": attempts, "overlap": round(score, 3),
                    "ok": True, "note": ""}
    return {"text": best, "attempts": attempts, "overlap": round(best_score, 3), "ok": False,
            "note": "扩写连续 %d 次偏离原题（重合度 %.2f，需 ≥%.2f）。"
                    "已保留最接近的一次，请把一句话写得再具体一点，或直接手改。"
                    % (attempts, best_score, threshold)}
