# -*- coding: utf-8 -*-
"""域模块的公共依赖：从 server.py 拆出来的路由体原样引用这些名字。

`_RESP` 只是把 `_json_resp(self, obj)` 的返回值原样交回分发器；
状态码用 `_RESP(obj, 404)`，分发器读 `_status` 决定 HTTP 码。
"""
import json
import os
import time

from cores import (asset_core, canon_core, canon_guard, context_engine, narrative_core,
                   profile_core, project_settings, quality_core, runtime_core,
                   state_core, store, story_core, story_layer)


def _RESP(obj, status=200):
    if isinstance(obj, dict) and status != 200:
        obj = dict(obj)
        obj["_status"] = status
    return obj


def log(level, msg):
    """路由体里用到的日志：转交 server 的实现，避免两份日志。"""
    try:
        import server
        return server.log(level, msg)
    except Exception:
        pass


def ctx():
    """把公共名字灌进域模块的全局，让搬过来的代码不用改。"""
    return {k: v for k, v in globals().items()
            if not k.startswith("__") and k not in ("ctx",)}
