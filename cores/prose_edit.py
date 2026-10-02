# -*- coding: utf-8 -*-
"""只读的人工修稿材料：复用现有检查，不调用模型，不把疑点判成事实。"""
import re

from . import consistency as cc
from .universal_writer import signature


def paragraphs(text, no, source="prose"):
    return [{"no": no, "source": source, "paragraph": i, "quote": m.group().strip()}
            for i, m in enumerate(re.finditer(r"[^\r\n]+", text), 1)
            if m.group().strip()]


def inspect_prose(episodes, no, text, settings=None, source="prose"):
    settings = settings or {}
    current = next((e for e in episodes if int(e.get("no") or 0) == no), {})
    here = paragraphs(text, no, source)
    previous = sorted((e for e in episodes if int(e.get("no") or 0) < no),
                      key=lambda e: int(e["no"]))
    past = [p for e in previous for p in paragraphs(str(e.get("prose") or ""), int(e["no"]))]
    all_paras = past + here
    issues = []

    def add(kind, detail, advice, refs):
        # 没有本话证据的旧问题不算本话问题。引用保留原文，不虚构定位。
        if not any(p["no"] == no for p in refs):
            return
        issues.append({"kind": kind, "detail": detail, "advice": advice, "references": refs})

    for detail in cc.number_conflicts([p["quote"] for p in all_paras]):
        subject = re.search(r"「([^」]+)」", detail).group(1)
        refs = [p for p in all_paras if any(k[1] == subject for k in cc.measured_values(p["quote"]))]
        add("数值待核对", detail,
            "先确认是不是同一个东西、同一时刻。若是同一数据，统一数值；若是变化过程，保留并补清变化原因。", refs)

    row = next((r for r in settings.get("plan_rows", []) if int(r.get("no") or 0) == no), {})
    canon = " ".join(str(x or "") for x in (settings.get("one_line"), settings.get("story_canon"), row.get("one_line")))
    names = [p["name"] for p in settings.get("plan_people", []) if isinstance(p, dict) and p.get("name")]
    if not names:
        names = [s.strip() for s in re.split(r"[、,，/；;]", str(row.get("cast") or "")) if s.strip()]
    for p in here:
        for detail in cc.clock_conflicts(p["quote"], canon):
            add("钟点待核对", detail, "对照故事要求，确认是不是同一件定时发生的事；不同事件可以发生在不同时间。", [p])
        if cc.self_address_conflicts(p["quote"], names):
            add("称呼待核对", "这段的称呼和出现的人名同姓，程序无法确定是谁对谁说话。",
                "看清说话人和听话人；称呼若叫错就改称呼，别人正在招呼这个人则无需修改。", [p])
        for detail in cc.prose_hints(p["quote"]):
            add("路程待核对", detail, "确认是同一段路，还是两条不同路线；路线对比可以保留。", [p])

    # 跨话原句复用（P199）：拿本话整段和前面各话比，逐段比会把长句切碎、比不出来
    older = [str(e.get("prose") or "") for e in previous if str(e.get("prose") or "").strip()]
    for detail in cc.cross_episode_reuse(text, older):
        frag = detail.split("：", 1)[-1]
        refs = [p for p in here if frag[:12] and frag[:12] in p["quote"].replace(" ", "")]
        add("重复待核对", detail,
            "前面某一话已经这么写过。想保留成回环就留着，否则换个说法。", refs or here[:1])

    review = current.get("writing_attempt" if source == "prose_draft" else "writing_review") or {}
    # 旧稿的审查结论不得安在刚修改的正文上。
    if review.get("prose_sig") == signature(text):
        for issue in review.get("issues") or []:
            if not isinstance(issue, dict) or issue.get("kind") == "consistency":
                continue
            quote = str(issue.get("quote") or "")
            refs = [p for p in here if quote and quote in p["quote"]]
            if refs:
                add("已有审稿记录（未复核）", str(issue.get("detail") or "请核对原文"),
                    "这是这份文字已有的审稿记录，可能误报。请结合前后文决定是否修改。", refs)
    return {"issues": issues, "text": text, "source": source,
            "previous_tail": str(previous[-1].get("prose") or "")[-1500:] if previous else "",
            "previous_no": previous[-1]["no"] if previous else None,
            "scope": "本话与已保存的前文；仅检查现有数值、钟点、称呼和路程疑点，不代表完整审稿。"}
