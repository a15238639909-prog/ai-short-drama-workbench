# -*- coding: utf-8 -*-
"""HTTP 路由注册表：把 server.py 里那两个上千行的 do_GET/do_POST 拆成按域分模块。

为什么拆：8848 的 app.py 就是从"所有路由写在一个函数里"长成 30,739 行的。
这里定一条规矩——**server.py 只负责收发和分发，不含任何业务判断**；
每个域一个模块，注册自己的路由。

用法（在域模块里）：

    from api import get, post

    @get("/api/gpu")
    def gpu(h, path, q):
        return {"ok": True, "data": {...}}

    @post("/api/story/body")
    def story_body(h, path, d):
        return {"ok": True, "data": {...}}

处理函数返回 dict 时由分发器统一 JSON 输出；返回 None 表示已自行写回响应。
路径可以是精确串，也可以是 (前缀, 后缀) 元组做前后缀匹配。
"""
GET_ROUTES = []
POST_ROUTES = []


def _match(rule, path):
    """规则可以是三种形态：
       "精确路径"
       (前缀, 后缀)
       (前缀, 中间必须包含, 后缀)   ← 少了中间这段，
                                     /api/story/X/scenes/master 会被
                                     /api/story/X/characters/master 抢先匹配。
    """
    if isinstance(rule, dict):
        # 最完整的形态：可同时限定前缀/中间片段/后缀/斜杠段数
        if "prefix" in rule and not path.startswith(rule["prefix"]):
            return False
        if "contains" in rule and rule["contains"] not in path:
            return False
        if "suffix" in rule and not path.endswith(rule["suffix"]):
            return False
        if "count" in rule and path.count("/") != rule["count"]:
            return False
        return True
    if isinstance(rule, tuple):
        if len(rule) == 3:
            pre, mid, suf = rule
            return path.startswith(pre) and mid in path and path.endswith(suf)
        pre, suf = rule
        return path.startswith(pre) and path.endswith(suf)
    return path == rule


def get(rule):
    def deco(fn):
        GET_ROUTES.append((rule, fn))
        return fn
    return deco


def post(rule):
    def deco(fn):
        POST_ROUTES.append((rule, fn))
        return fn
    return deco


def _specificity(rule):
    """越具体越先匹配：精确路径 > 三段(前缀+中间+后缀) > 两段。
       不靠注册顺序碰运气——注册顺序一变就会静默调错处理函数。"""
    if isinstance(rule, dict):
        return 1 + len(rule) * 0.1      # 限定条件越多越具体
    if isinstance(rule, tuple):
        return 1 if len(rule) == 3 else 0
    return 2


def dispatch(routes, h, path, payload):
    """找到匹配的处理函数并调用。没有匹配返回 False，交回 server.py 走原有分支。"""
    for rule, fn in sorted(routes, key=lambda x: -_specificity(x[0])):
        if _match(rule, path):
            return fn(h, path, payload)
    return False


def load_all():
    """导入所有域模块，触发注册。新增域模块记得加到这里。"""
    from . import (story, settings_api, chain_api, video_api, system_api,  # noqa: F401
                   assets_api, recovered, timeline, segment_api, gpu_api,
                   saga_api, history_api, config_api, image_api)
    return len(GET_ROUTES), len(POST_ROUTES)
