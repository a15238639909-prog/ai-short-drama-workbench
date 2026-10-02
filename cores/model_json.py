# -*- coding: utf-8 -*-
"""model_json.py — 从模型输出里取 JSON 的唯一实现。

用户定的规矩：**拦截是设计失败。** 正常生产过程不该因为模型偶尔多打了一个逗号
就抛异常停在半路。所以这里做三层，和 auto_repair 一样的思路：

    第 1 层 自动修 —— 剥围栏、切括号、补常见语法错（尾逗号、中文引号、单引号）
    第 2 层 换个说法重试 —— 带上失败原因让模型重来，最多几次
    第 3 层 才报错 —— 前两层都不行，才说明是真的写不出来

`chat_json()` 是给所有"要模型吐 JSON"的地方用的统一入口。
director_core 和 one_shot_setup 原本各写了一份提取逻辑，
其中一份直接 raise，temperature 高一点就会把整条链打断。
"""
import json
import re

# ```json ... ``` 或 ``` ... ```
_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*|\s*```\s*$", re.M)


def _repairs(t):
    """按从保守到激进的顺序，产出几个候选文本。"""
    yield t
    # 尾逗号：{"a":1,}  [1,2,]
    yield re.sub(r",\s*([}\]])", r"\1", t)
    # 中文引号被当成了 JSON 引号
    y = t.replace("“", '"').replace("”", '"').replace("：", ":").replace("，", ",")
    yield y
    yield re.sub(r",\s*([}\]])", r"\1", y)
    # 字符串里出现真实换行（JSON 不允许）
    def _nl(m):
        return m.group(0).replace("\n", "\\n").replace("\r", "")
    yield re.sub(r'"(?:[^"\\]|\\.)*"', _nl, t, flags=re.S)


def extract(text):
    """从模型输出里取出 dict/list；取不出来返回 None（不抛）。"""
    t = _FENCE.sub("", str(text or "")).strip()
    if not t:
        return None
    cands = [t]
    a, b = t.find("{"), t.rfind("}")
    if a >= 0 and b > a:
        cands.append(t[a:b + 1])
    a, b = t.find("["), t.rfind("]")
    if a >= 0 and b > a:
        cands.append(t[a:b + 1])
    for c in cands:
        for r in _repairs(c):
            try:
                v = json.loads(r)
            except Exception:
                continue
            if isinstance(v, (dict, list)):
                return v
    return None


RETRY_HINT = ("上一次的输出没法解析成 JSON。这次**只输出一个 JSON 对象**，"
              "从 { 开始、到 } 结束，不要写代码围栏、不要写任何说明文字，"
              "字符串里不要出现没有转义的换行和双引号。")


def chat_json(system, user, chat, validate=None, tries=3, normalize=None, **kw):
    """让模型吐 JSON，自动修 + 重试。

    chat: 形如 chat(system, user, **kw) 的可调用对象（qwen_client.chat 或 chat_for 的偏函数）
    validate: 可选，validate(dict) -> None/抛异常。抛了就当这次不合格，重试。
    """
    last = ""
    u = user
    for i in range(max(1, tries)):
        raw = chat(system, u, **kw) or ""
        d = extract(raw)
        if d is not None and normalize is not None:
            # 校验之前先把形状掰正。模型经常把顶层写成数组、把数组元素写成字符串，
            # 这些都不是"JSON 不合格"，只是包装不对——掰得动就不该重试。
            try:
                d = normalize(d)
            except Exception:
                pass
        if d is not None:
            if validate is None:
                return d, raw
            try:
                validate(d)
                return d, raw
            except ValueError as e:
                last = "上一次的输出缺内容：%s" % e
            except Exception as e:
                # 校验器自己崩了（多半是模型给的结构和预期形状对不上）。
                # 原来这里和"缺内容"混成一句，用户看到的是
                # 「模型连续 3 次没能给出合格 JSON：'list' object has no attribute 'get'」——
                # JSON 其实是好的，报错完全指错了方向。
                last = ("上一次的结构和要求对不上（%s: %s）。请严格按 JSON 规格输出："
                        "每个数组元素都必须是对象 {}，不能是字符串或数组。"
                        % (type(e).__name__, e))
        else:
            last = RETRY_HINT
        u = user + "\n\n【重来】" + last
    raise ValueError("模型连续 %d 次没能给出合格 JSON：%s" % (tries, last))
