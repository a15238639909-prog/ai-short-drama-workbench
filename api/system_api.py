# -*- coding: utf-8 -*-
"""system_api 域路由（由 server.py 的 do_POST 拆分而来）。"""
from api import post, get
from api._shared import _RESP, ctx
globals().update(ctx())


@post("/api/game_mode")
def r_036(h, path, d):
    action = str(d.get("action") or "status")
    if action in ("enter", "on"):
        st = runtime_core.game_enter()
        log("info", "游戏模式开启：显存 %.1f→%.1f GB" % (
            round((st.get("before") or {}).get("used_mb", 0) / 1024, 1),
            round((st.get("after") or {}).get("used_mb", 0) / 1024, 1)))
    elif action in ("exit", "off"):
        st = runtime_core.game_exit()
        log("info", "游戏模式关闭")
    else:
        st = {"mode": store.load_json(store.DATA / "gpu_state.json", {}).get("mode", "normal")}
    return _RESP({"ok": True, "game_mode": st})

@get("/api/doctor")
def r_doctor(h, path, q):
    """开机自检的机器可读版：界面可以拿去显示"缺什么"。"""
    from cores import doctor
    rows = doctor.check()
    return _RESP({"ok": True, "data": {
        "items": [{"level": lv, "name": nm, "ok": bool(ok), "tip": tip} for lv, nm, ok, tip in rows],
        "must_bad": [nm for lv, nm, ok, _ in rows if lv == "必须" and not ok],
    }})
