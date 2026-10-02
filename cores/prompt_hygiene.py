# -*- coding: utf-8 -*-
"""提示词卫生：内部 ID 清理 + 负向表述正向化。通用规则，不针对某个故事。

两件事都曾经用「逐句字符串替换」硬写在一次性脚本里，换故事就失效：
1. 内部 ID：以前三个脚本各存一份 ID_MAP，漏了就把 SEQ_02_CAFE 直接喂给模型。
   现在按通用形态识别内部 ID，映射表由 StoryProfile 从数据生成，漏映射会被验收查出来。
2. 负向表述：以前是 t.replace("不做磨皮", "保留自然肤质") 这种一次性替换。
   现在分两层：先用领域改写表把常见否定式翻成正向，再把剩下的否定短句摘出去
   交给负向词处理。正向词里不留否定句，是因为扩散模型对否定式不可靠。
"""
import re

__all__ = ["INTERNAL_ID_RE", "find_internal_ids", "sanitize_internal_ids",
           "positivize", "NEGATION_REWRITES", "find_negations"]

# 内部工程 ID 的通用形态：全大写词段 + 下划线，段内允许数字与中文
# 例：CHAR_A_旅行者 / SCENE_01_STREET / KREA_CHAR_B_MASTER / USER_CUSTOM_CONTENT_SEGMENT
INTERNAL_ID_RE = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9一-鿿]+)+")

# 领域改写表（通用于生图/生视频提示词，不含任何故事字面量）
# 写成「可在句中命中」的形态，因为否定式经常夹在句子中间：如「青春但不过分幼态」
NEGATION_REWRITES = [
    (re.compile(r"无可读文字"), "表面保持干净"),
    (re.compile(r"无门牌文字"), "门板保持素面"),
    (re.compile(r"无(招牌)?文字"), "表面保持干净"),
    (re.compile(r"无编号"), "画面无标注"),
    (re.compile(r"无水印"), "画面干净"),
    (re.compile(r"无(主角|其他人物|无关人物|人物)"), "画面内仅有空间与静物"),
    (re.compile(r"不做磨皮"), "保留自然肤质与毛孔"),
    (re.compile(r"不做过度变形"), "形变符合所用镜头的自然特性"),
    (re.compile(r"不做多格图"), "单幅完整画面"),
    (re.compile(r"不出现第二套服装"), "全图统一为同一套基准服装"),
    (re.compile(r"[（(]?不单独分件[）)]?"), "（上下一体的单件）"),
    (re.compile(r"[，,]?不扣"), ""),
    (re.compile(r"但?不过分幼态"), "，年龄感与设定年龄一致"),
    (re.compile(r"不强行打光"), "保留现场光比"),
]

# 句子切分：中文标点 + 换行。
# 逗号也必须算分句边界——否定式经常夹在逗号串里（"欧洲老城实景，自然光，不要动漫感"），
# 只按句号分号断句的话，整条会被当成一个不以否定词开头的短句放行。
_CLAUSE_SPLIT = re.compile(r"([；;。，\n])")
# 否定式开头：无 X / 不 X / 禁止 X / 避免 X / 排除 X / 没有 X …
_NEGATION_HEAD = re.compile(r"^(无|没有|不出现|不做|不要|不得|不能|不|禁止|避免|排除)(.+)$")


def find_internal_ids(text, allow=()):
    """返回文本里残留的内部 ID（allow 里的除外）。"""
    allow = set(allow)
    return [m for m in INTERNAL_ID_RE.findall(str(text or "")) if m not in allow]


def sanitize_internal_ids(text, id_map, fallback=None):
    """把内部 ID 换成自然称呼。

    id_map 未覆盖的 ID：给了 fallback 就用 fallback，否则**原样保留**，
    以便验收阶段能把它查出来——不允许用「该对象」这类兜底把问题盖掉。
    """
    t = str(text or "")
    for k in sorted(id_map, key=len, reverse=True):
        t = t.replace(k, id_map[k])
    if fallback is not None:
        t = INTERNAL_ID_RE.sub(fallback, t)
    return t


def find_negations(text):
    """返回文本里仍然存在的否定式短句，用于验收。"""
    out = []
    for chunk in _CLAUSE_SPLIT.split(str(text or "")):
        if not chunk or _CLAUSE_SPLIT.fullmatch(chunk):
            continue
        for clause in re.split(r"[、，,]", chunk):
            c = clause.strip()
            if c and _NEGATION_HEAD.match(c):
                out.append(c)
    return out


def positivize(text):
    """把提示词里的否定式改写成正向表述。

    返回 (正向文本, 需要交给负向词处理的词条列表)。
    第一层：领域改写表（句中命中即改）。
    第二层：仍以否定词开头的短句 → 从正向词里摘掉，词条交给负向词。
    """
    t = str(text or "")
    for pat, rep in NEGATION_REWRITES:
        t = pat.sub(rep, t)

    extracted, out = [], []
    for chunk in _CLAUSE_SPLIT.split(t):
        if not chunk or _CLAUSE_SPLIT.fullmatch(chunk):
            out.append(chunk)
            continue
        parts = []
        for clause in chunk.split("、"):
            c = clause.strip()
            if not c:
                continue
            mo = _NEGATION_HEAD.match(c)
            if mo:
                extracted.append(mo.group(2).strip("；;。，, "))
                continue
            parts.append(c)
        out.append("、".join(parts))

    text_out = "".join(out)
    text_out = re.sub(r"[；;]{2,}", "；", text_out)
    text_out = re.sub(r"(^|\n)[；;]+", r"\1", text_out)
    text_out = re.sub(r"[，,]{2,}", "，", text_out)
    text_out = re.sub(r"[，,](?=[；;。\n])", "", text_out)

    seen, uniq = set(), []
    for w in extracted:
        if w and w not in seen:
            seen.add(w)
            uniq.append(w)
    return text_out, uniq
