# -*- coding: utf-8 -*-
"""剧本文字层：唯一的剧本解析与角色代号净化入口。

背景：早期各轮任务在一次性脚本里各写各的 startswith 解析，同一份剧本被两个
函数读出两种结果（对白抽成人名列表 / 动作描述当对白）。此模块把解析收敛成
一份实现，任何下游（Audio 计划、H3 提示词、验收）都必须从这里取对白。

剧本格式约定（Qwen 输出的标准影视剧本写法）：
    艾莉莎                      <- 说话人提示行：整行只有人名
    （拉开对面的椅子坐下）        <- 括号内为表演提示，不是台词
    看来风又把你吹到这儿来了。     <- 真正的台词
其余行一律视为动作/描写，绝不进入对白。
"""
import re

__all__ = ["parse_screenplay", "dialogue_lines", "sanitize_role_codes",
           "find_role_code_leak", "ROLE_CODE_RE"]

# 角色代号：夹在中文/标点之间的裸 A / B（如 "B转A"、"过B肩拍A"、"A停步"）。
# 前后有字母或数字的不算（MS / WS / 35mm / A4 不会误伤）。
ROLE_CODE_RE = re.compile(r"(?<![A-Za-z0-9_])([AB])(?![A-Za-z0-9_])")

_PAREN_HEAD = ("（", "(")


def _is_cue(line, speakers):
    """整行只有人名（允许结尾冒号）时，认定为说话人提示行。"""
    t = line.rstrip("：:").strip()
    return t in speakers


def parse_screenplay(text, speakers):
    """把剧本正文解析成块序列。

    返回 [{"type": "dialogue"|"action", "speaker", "parenthetical", "text"}]
    - dialogue：speaker 必有，text 是台词原文（逐字保留，不改写）
    - action：speaker 为 None，text 是动作/描写原文
    """
    speakers = tuple(speakers)
    blocks = []
    pending = None          # 等待台词的说话人
    parenthetical = None    # 该说话人的表演提示

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue

        # 形式一：整行人名
        if _is_cue(line, speakers):
            pending = line.rstrip("：:").strip()
            parenthetical = None
            continue

        # 形式二：人名：台词（同一行）
        inline = None
        for sp in speakers:
            for sep in ("：", ":"):
                if line.startswith(sp + sep) and len(line) > len(sp) + 1:
                    inline = (sp, line[len(sp) + 1:].strip())
                    break
            if inline:
                break
        if inline:
            blocks.append({"type": "dialogue", "speaker": inline[0],
                           "parenthetical": None, "text": inline[1]})
            pending = None
            parenthetical = None
            continue

        # 括号行：只有在等待台词时才是表演提示
        if line[0] in _PAREN_HEAD:
            if pending:
                parenthetical = line
            else:
                blocks.append({"type": "action", "speaker": None,
                               "parenthetical": None, "text": line})
            continue

        if pending:
            blocks.append({"type": "dialogue", "speaker": pending,
                           "parenthetical": parenthetical, "text": line})
            pending = None
            parenthetical = None
        else:
            blocks.append({"type": "action", "speaker": None,
                           "parenthetical": None, "text": line})

    return blocks


def dialogue_lines(text, speakers):
    """只取真正的台词：[(说话人, 表演提示 or None, 台词原文)]。无对白返回 []。"""
    return [(b["speaker"], b["parenthetical"], b["text"])
            for b in parse_screenplay(text, speakers) if b["type"] == "dialogue"]


def sanitize_role_codes(text, mapping):
    """把字段里的裸角色代号换成对外可读的称呼。

    mapping 例：{"A": "<Subject 1>", "B": "<Subject 2>"}
    只应作用于中文正文字段，不要整段套用在英文 summary 上（英文冠词 "A" 会被误替）。
    """
    if not text:
        return text
    return ROLE_CODE_RE.sub(lambda m: mapping.get(m.group(1), m.group(1)), str(text))


def find_role_code_leak(text):
    """返回文本里残留的裸角色代号列表；用于验收，空列表即通过。"""
    return ROLE_CODE_RE.findall(str(text or ""))
