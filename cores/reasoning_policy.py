# -*- coding: utf-8 -*-
"""V4.2.1 Reasoning Policy：统一 fast / standard / deep，生产代码不得各自写 thinking 参数。
依据：reports/v421/02_thinking_ab_report.md 实测结论。"""

# level → enable_thinking
LEVELS = {
    "fast": {"enable_thinking": False},
    "standard": {"enable_thinking": True},
    "deep": {"enable_thinking": True},
}

# task_key → {level, temperature, max_tokens}；enable_thinking 由 level 决定，可显式覆盖
POLICY = {
    "story_preview": {"level": "standard", "temperature": 0.7, "max_tokens": 3600},
    "master_plan": {"level": "deep", "temperature": 0.7, "max_tokens": 6400},
    "episode": {"level": "deep", "temperature": 0.7, "max_tokens": 8000},
    "narrative_units": {"level": "deep", "temperature": 0.7, "max_tokens": 4200},
    "state_commit": {"level": "deep", "temperature": 0.7, "max_tokens": 4200},
    "director": {"level": "deep", "temperature": 0.7, "max_tokens": 12000},
    "video_sequence_planning": {"level": "deep", "temperature": 0.7, "max_tokens": 6000},
    # H3 六段式结构化输出：实测 thinking=True 时 reasoning 吃掉预算导致正文截断，保持 False
    "h3_prompt": {"level": "standard", "temperature": 0.6, "max_tokens": 5200, "enable_thinking": False},
    "comic_page_beat": {"level": "deep", "temperature": 0.7, "max_tokens": 8000},
    "comic_panel": {"level": "deep", "temperature": 0.6, "max_tokens": 3600},
    "novel_chapter_plan": {"level": "deep", "temperature": 0.7, "max_tokens": 7000},
    # 小说正文：standard，但 enable_thinking 暂 False（后续单独 A/B）
    "novel_chapter": {"level": "standard", "temperature": 0.75, "max_tokens": 7000, "enable_thinking": False},
    "universal_prose": {"level": "deep", "temperature": 0.75, "max_tokens": 9000},
    "universal_split": {"level": "fast", "temperature": 0.3, "max_tokens": 6500},
    "album_plan": {"level": "standard", "temperature": 0.7, "max_tokens": 6000},
    "ask_ai": {"level": "fast", "temperature": 0.7, "max_tokens": 1800},
    "vchar": {"level": "fast", "temperature": 0.8, "max_tokens": 1400},
}

def get_policy(task_key):
    """返回 {enable_thinking, temperature, max_tokens, level}。"""
    p = dict(POLICY.get(task_key) or {"level": "standard", "temperature": 0.7, "max_tokens": 3600})
    level = p.get("level", "standard")
    merged = dict(LEVELS.get(level, LEVELS["standard"]))
    merged.update(p)
    merged["level"] = level
    return merged

def describe():
    lines = ["| 任务 | level | enable_thinking | temperature | max_tokens |", "|---|---|---|---|---|"]
    for k in sorted(POLICY):
        p = get_policy(k)
        lines.append("| %s | %s | %s | %s | %s |" % (
            k, p["level"], p["enable_thinking"], p["temperature"], p["max_tokens"]))
    return "\n".join(lines)
