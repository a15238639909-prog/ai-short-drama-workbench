# -*- coding: utf-8 -*-
"""profile_core.py — v4.2 Custom Content Profile（用户自行填写的露骨成人内容层）。
系统不预写露骨内容；只保存/启用/作用范围/追溯。"""
from . import store

PROFILE_FILE = store.DATA / "profiles" / "content_profile.json"

DEFAULT_PROFILE = {
    "enabled": False,
    "tone": "成熟",
    "intensity": "中",
    "adult_appeal": "中",
    "violence": "中",
    "horror": "中",
    "custom_rules": "",
    "scopes": ["story", "novel", "comic", "video", "krea"],
    "updated": 0,
}

def load_profile():
    d = store.load_json(PROFILE_FILE, {})
    out = dict(DEFAULT_PROFILE)
    if isinstance(d, dict):
        out.update(d)
    return out

def save_profile(updates):
    cur = load_profile()
    cur.update(updates or {})
    cur["updated"] = __import__("time").time()
    store.save_json(PROFILE_FILE, cur)
    return cur

def custom_rules_text():
    p = load_profile()
    if not p.get("enabled"):
        return ""
    return str(p.get("custom_rules") or "")
